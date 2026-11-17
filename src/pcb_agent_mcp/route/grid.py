"""The map a router searches, and the exact check that decides what it may keep.

Two different questions get two different mechanisms, on purpose:

* **Where can I go?** - a raster of cells, each knowing which nets already have
  copper within `clearance + track/2` of the cell centre. Rasterising is what
  makes an A* over a 53 x 30 mm board finish in seconds instead of minutes.
  The raster is allowed to be WRONG IN THE SAFE DIRECTION ONLY: it uses the
  strictest net class on the board for every net, and it marks a cell blocked
  when any other net's copper is nearer than that, so a cell it calls free is
  genuinely free for the loosest class and cells it calls blocked may be legal
  for a net with a thinner track. It never calls a cell free that is not.
* **Is this segment legal?** - exact polygon/segment distance (`_dist`), against
  a spatial hash, with the clearance required being `max(class(a), class(b))`.
  This is what accepts copper. The raster only proposes.

The asymmetry is the point: a fast search that is a little pessimistic reports
"no route" where a person might find one, which is annoying but honest. A fast
search that is optimistic writes DRC violations into someone's board, which is
not.
"""

import heapq
import math

from .. import geometry as G
from ..oracle import _bucketize, _dist, _item_bbox, _items
from . import rules as R

# Search costs. Measured on the F401 core board: a via is worth about 9 mm of
# track (people pay a via to avoid a long detour, not for the fun of drilling),
# and a turn is cheap but not free - without it the paths come out as a staircase
# that no DRC objects to and no person would draw.
VIA_COST = 9.0
TURN_COST = 0.55
DIAG = 1.36            # sqrt(2) - diagonal step length
HEUR_W = 1.25          # weighted A*: a slightly longer wire for a far faster search
MAX_BUDGET = 400000
# A guide map stores the discount of a cell as one byte (see `astar(guide=...)`),
# so the inverse only has to be computed once.
INV255 = 1.0 / 255.0


def _steps(pitch, lo, hi):
    return int(math.floor((hi - lo) / pitch)) + 1

def _axis_rect(poly):
    """Rectangle `(x0, y0, x1, y1)` if `poly` is exactly an axis-aligned one.

    Pads are overwhelmingly axis-aligned rectangles; walking four edges for each
    of a few hundred thousand cells to learn the same thing is wasted time.
    Anything else (circles, rotated pads, board outlines) returns None and takes
    the exact path.
    """
    if len(poly) < 4 or len(poly) > 5:
        return None
    xs = [p[0] for p in poly[:4]]
    ys = [p[1] for p in poly[:4]]
    x0, x1 = min(xs), max(xs)
    y0, y1 = min(ys), max(ys)
    seen = set((round(p[0], 6), round(p[1], 6)) for p in poly[:4])
    want = set((round(a, 6), round(b, 6))
               for a in (x0, x1) for b in (y0, y1))
    if seen != want:
        return None
    return (x0, y0, x1, y1)


class Grid(object):
    """Cell occupancy per copper layer, plus exact geometry for acceptance."""

    def __init__(self, board, layers, pitch, bundle, ignore_zone_areas=False,
                 extra_keepouts=None, origin=None):
        if pitch <= 0:
            raise ValueError("pitch must be > 0")
        self.board = board
        self.layers = list(layers)
        self.pitch = float(pitch)
        self.bundle = bundle
        # The strictest clearance on the board, used for every raster margin.
        self.clr = float(bundle["clearance_mm"])
        for spec in (bundle.get("per_net") or {}).values():
            self.clr = max(self.clr, float(spec["clearance_mm"]))
        # Creepage is a second distance rule that may be larger than the electrical
        # clearance (rules.creepage). The raster takes the widest creepage any pair
        # asked for - one number for every net, pessimistic in the safe direction -
        # while `seg_blockers` / `via_blockers` then judge each pair by its own
        # `pair_spacing`. Both ends are needed: only the raster and the search
        # plans tracks inside the band the judge later rejects.
        self.creepage = R.creepage_max(bundle)
        # Creepage and a declared diff-pair gap are both "distance between two
        # nets" rules that are wider than the net-class clearance; the raster
        # takes the widest of them, the exact check uses each pair's own number.
        self.spacing = max(self.creepage, R.spacing_max(bundle))
        if self.spacing > self.clr:
            self.clr = self.spacing
        self.track = float(bundle["track_mm"])
        for spec in (bundle.get("per_net") or {}).values():
            self.track = max(self.track, float(spec["track_mm"]))
        # Two margins, because a via and a track are not the same size: a 0.6 mm
        # via needs 0.5 mm from foreign copper where a 0.25 mm track needs 0.325.
        # One raster sized for the via would wall off every fine-pitch pad (a
        # 0.5 mm-pitch row has its neighbour's copper 0.35 mm from the pad
        # centre), and one sized for the track let the search place vias the exact
        # check then rejected - 7 of 50 nets failed that way on the F401 core
        # board, every one of them with reason `clearance` on a via.
        self.via_dia = float(bundle["via_diameter_mm"])
        for spec in (bundle.get("per_net") or {}).values():
            self.via_dia = max(self.via_dia, float(spec["via_diameter_mm"]))
        self.margin = self.clr + self.track / 2.0
        self.margin_via = self.clr + self.via_dia / 2.0
        # Copper-to-edge. This is not a clearance between nets, so it is a third
        # number and not a third margin: it applies to every net, on every layer,
        # and the referee applies it even when the file is silent (see
        # rules.DEFAULT_EDGE_CLEARANCE_MM).
        self.edge = float(bundle.get("edge_clearance_mm") or 0.0)

        box = self._extent()
        self.x0, self.y0, self.x1, self.y1 = box
        if origin is not None:
            # Lattice PHASE, not a new extent. The default phase is the board
            # bbox corner, so on a fine-pitch BGA (U1: 0.5621 mm pitch, pad
            # centres at .x749/.x001) every pad centre sits a fraction of a step
            # off the lattice: the search draws a segment 0.099 mm from a
            # neighbour pad and the exact check rejects it by 0.001 mm. Anchoring
            # the phase on a pad centre makes the lattice coincide with the pad
            # grid instead of drifting against it.
            ox, oy = float(origin[0]), float(origin[1])
            self.x0 = ox + math.floor((self.x0 - ox) / self.pitch) * self.pitch
            self.y0 = oy + math.floor((self.y0 - oy) / self.pitch) * self.pitch
        self.nx = _steps(self.pitch, self.x0, self.x1)
        self.ny = _steps(self.pitch, self.y0, self.y1)

        # Flat per-layer integer maps, not a dict keyed by (layer, i, j). The
        # search asks this question tens of millions of times per board: on the
        # F401 core board a 12-net run put 63 s of 229 s inside `free` and another
        # 53 s in the `dict.get` behind it, one tuple hash per cell tested. A list
        # index is one multiply and one load. Encoding:
        #   0  free for every net
        #   -1 blocked for every net (off board, keepout, net-less graphic)
        #   k  self._sets[k - 1] - the nets whose copper is nearer than clearance
        self.onboard = bytearray(self.nx * self.ny)   # 1 = on the board outline
        self._map = dict((l, [0] * (self.nx * self.ny)) for l in self.layers)
        self._mapv = dict((l, [0] * (self.nx * self.ny)) for l in self.layers)
        self._sets = []              # interned frozenset of net names
        self._set_ids = {}           # frozenset -> k (> 0)
        self.wall = set()            # kept for callers that ask about walls
        self.notes = []
        if self.spacing > float(bundle["clearance_mm"]):
            self.notes.append(
                "a creepage / diff-pair rule of %.3f mm is wider than every "
                "electrical clearance here, so the raster keeps that distance "
                "everywhere (the exact check uses each pair's own number)"
                % self.spacing)
        # Negotiated congestion (router._negotiate) puts a per-cell penalty here.
        # None means "no penalty anywhere", which is the greedy router's world;
        # A* allocates the flat list on first use so an unpriced run pays nothing
        # for a feature it does not use.
        self._pen = None
        self.keepouts = []           # exact rings, for the check that is the judge

        self._obs = {}               # layer -> (items, bboxes, buckets)
        self._cell = max(1.0, self.margin * 4)
        self._inside_mask()
        self._mark_edge()
        self._mark_copper()
        self._mark_keepouts(ignore_zone_areas)
        # Rectangles that come from the rule catalogue rather than from the board
        # file (design_constraints.keepout_rects). They go through the SAME two
        # paths a zone keepout does - the raster and the exact ring check - so a
        # rule cannot be honoured by the search and quietly ignored by the judge.
        # Marking only the raster would let a proposed segment cross the rect and
        # still be reported clean, which is the defect this whole version is about.
        self.rule_keepouts = 0
        self.dropped_rule_keepouts = []
        if extra_keepouts:
            self._add_rule_keepouts(extra_keepouts)

    # ------------------------------------------------------------- geometry
    def _extent(self):
        """Board bbox, from the outline if there is one, else from the copper."""
        if self.board.outline:
            xs, ys = [], []
            for ring in self.board.outline:
                for x, y in ring:
                    xs.append(x)
                    ys.append(y)
            if xs:
                return (min(xs), min(ys), max(xs), max(ys))
        pts = []
        for it in _items(self.board):
            x0, y0, x1, y1 = _item_bbox(it)
            pts.append((x0, y0))
            pts.append((x1, y1))
        if not pts:
            return (0.0, 0.0, 10.0, 10.0)
        return (min(p[0] for p in pts), min(p[1] for p in pts),
                max(p[0] for p in pts), max(p[1] for p in pts))

    def xy(self, i, j):
        return self.x0 + i * self.pitch, self.y0 + j * self.pitch

    def node(self, x, y):
        return (int(round((x - self.x0) / self.pitch)),
                int(round((y - self.y0) / self.pitch)))

    def _inside_mask(self):
        """Which cells are on the board. Scanline fill, not 650k point tests."""
        if not self.board.outline:
            self.onboard[:] = b"\x01" * len(self.onboard)
            self.notes.append("no board outline: every cell is treated as on-board")
            return
        rings = [r for r in self.board.outline if len(r) >= 3]
        for j in range(self.ny):
            y = self.y0 + j * self.pitch
            xs = []
            for ring in rings:
                n = len(ring)
                for k in range(n):
                    ax, ay = ring[k]
                    bx, by = ring[(k + 1) % n]
                    if (ay > y) == (by > y):
                        continue
                    t = (y - ay) / (by - ay)
                    xs.append(ax + t * (bx - ax))
            xs.sort()
            for k in range(0, len(xs) - 1, 2):
                lo, hi = xs[k], xs[k + 1]
                i0 = max(0, int(math.ceil((lo - self.x0) / self.pitch)))
                i1 = min(self.nx - 1, int(math.floor((hi - self.x0) / self.pitch)))
                for i in range(i0, i1 + 1):
                    self.onboard[j * self.nx + i] = 1
        # Off the board is blocked for every net, in every layer, so one flag per
        # cell is enough and `free` needs no second lookup.
        for k in range(self.nx * self.ny):
            if not self.onboard[k]:
                for l in self.layers:
                    self._map[l][k] = -1
                    self._mapv[l][k] = -1

    def _mark_edge(self):
        """A band `edge + track/2` (and `edge + via/2`) wide inside Edge.Cuts.

        Measured, not predicted: without this band the F401 core board came out
        with LED1_A / LED2_A running 0.4 mm from the edge - legal to the search,
        0.275 mm of copper to the edge, and six `copper_edge_clearance` errors
        from kicad-cli. The band is blocked for EVERY net (`-1`), because the rule
        is about the board, not about a neighbour's copper.
        """
        if not self.edge or not self.board.outline:
            return
        rings = [r for r in self.board.outline if len(r) >= 3]
        if not rings:
            return
        m_tr, m_v = self.edge + self.track / 2.0, self.edge + self.via_dia / 2.0
        m = m_tr if m_tr > m_v else m_v
        pitch, nx, ny = self.pitch, self.nx, self.ny
        onb = self.onboard
        marked = 0
        for ring in rings:
            n = len(ring)
            for s in range(n):
                a, b = ring[s], ring[(s + 1) % n]
                ax0, ax1 = (a[0], b[0]) if a[0] <= b[0] else (b[0], a[0])
                ay0, ay1 = (a[1], b[1]) if a[1] <= b[1] else (b[1], a[1])
                i0 = max(0, int(math.floor((ax0 - m - self.x0) / pitch)))
                i1 = min(nx - 1, int(math.ceil((ax1 + m - self.x0) / pitch)))
                j0 = max(0, int(math.floor((ay0 - m - self.y0) / pitch)))
                j1 = min(ny - 1, int(math.ceil((ay1 + m - self.y0) / pitch)))
                if i1 < i0 or j1 < j0:
                    continue
                for j in range(j0, j1 + 1):
                    y = self.y0 + j * pitch
                    base = j * nx
                    for i in range(i0, i1 + 1):
                        k = base + i
                        if not onb[k]:
                            continue
                        d = G.dist_point_segment((self.x0 + i * pitch, y), a, b)
                        if d >= m:
                            continue
                        for layer in self.layers:
                            if d < m_tr:
                                self._map[layer][k] = -1
                            if d < m_v:
                                self._mapv[layer][k] = -1
                        marked += 1
        if marked:
            self.notes.append("copper-to-edge %.3f mm: %d cells inside the band are "
                              "blocked for every net" % (self.edge, marked))

    # ------------------------------------------------------------- marking
    def _sid(self, nets):
        """Intern a set of nets. -1 means 'this cell blocks every net'.

        The stored form is a tuple - the search reads `sets[k][0]` for the
        overwhelmingly common one-net case - and the intern key is a frozenset,
        because two tuples listing the same nets in another order are the same
        cell state.
        """
        if None in nets:
            return -1
        key = nets if isinstance(nets, frozenset) else frozenset(nets)
        got = self._set_ids.get(key)
        if got is None:
            got = len(self._sets) + 1
            self._sets.append(tuple(key))
            self._set_ids[key] = got
        return got

    def _put(self, arr, k, sid):
        """Merge `sid` into one cell: free -> that net set, union otherwise."""
        cur = arr[k]
        if cur == sid or cur < 0:
            return
        if cur == 0:
            arr[k] = sid
        elif sid < 0:
            arr[k] = -1
        else:
            arr[k] = self._sid(frozenset(self._sets[cur - 1] + self._sets[sid - 1]))

    def _mark_cells(self, item, nets):
        """Mark every cell within each margin of this item as occupied by `nets`.

        Two margins, one pass: the track raster (clearance + track/2) decides
        where the path may go, the via raster (clearance + via/2) decides where a
        layer change is even worth asking about. The union of the two ranges is
        walked once and each cell's distance is measured once - measuring it
        twice was 21 s of a 229 s run.
        """
        x0, y0, x1, y1 = _item_bbox(item)
        layers = [l for l in (item.get("layers") or []) if l in self.layers]
        if not layers:
            return
        nets = nets if isinstance(nets, frozenset) else frozenset(nets)
        sid = self._sid(nets)
        m_tr, m_v = self.margin, self.margin_via
        m = m_tr if m_tr > m_v else m_v
        nx = self.nx
        i0 = max(0, int(math.floor((x0 - m - self.x0) / self.pitch)))
        i1 = min(nx - 1, int(math.ceil((x1 + m - self.x0) / self.pitch)))
        j0 = max(0, int(math.floor((y0 - m - self.y0) / self.pitch)))
        j1 = min(self.ny - 1, int(math.ceil((y1 + m - self.y0) / self.pitch)))
        onb = self.onboard
        pitch = self.pitch
        for j in range(j0, j1 + 1):
            y = self.y0 + j * pitch
            base = j * nx
            for i in range(i0, i1 + 1):
                k = base + i
                if not onb[k]:
                    continue
                d = self._dist_point(item, self.x0 + i * pitch, y)
                if d >= m:
                    continue
                for layer in layers:
                    if d < m_tr:
                        self._put(self._map[layer], k, sid)
                    if d < m_v:
                        self._put(self._mapv[layer], k, sid)

    @staticmethod
    def _dist_point(item, x, y):
        """Distance from a point to an item's copper boundary (0 on it).

        Rectangles - which is what almost every pad is - take a four-subtraction
        path instead of a four-edge scan. The answer is the same one
        `dist_point_polygon` gives: outside the rectangle the distance to its
        boundary is the corner gap, inside it the distance to the nearest edge.
        """
        poly = item.get("poly")
        if poly is not None:
            r = item.get("rect")
            if r is None:
                r = _axis_rect(poly)
                item["rect"] = r if r else ()
            if r:
                dx = max(r[0] - x, 0.0, x - r[2])
                dy = max(r[1] - y, 0.0, y - r[3])
                if dx > 0.0 or dy > 0.0:
                    return math.hypot(dx, dy)
                return min(x - r[0], r[2] - x, y - r[1], r[3] - y)
            return G.dist_point_polygon((x, y), poly)
        a, b, w = item["seg"]
        return max(0.0, G.dist_point_segment((x, y), a, b) - w / 2.0)

    def _mark_copper(self):
        for layer in self.layers:
            objs = []
            for it in _items(self.board, layer=layer):
                if layer not in (it.get("layers") or []):
                    continue
                objs.append(it)
            self._obs[layer] = (objs, [_item_bbox(o) for o in objs],
                                _bucketize(objs, self._cell,
                                           pad=self.margin + 0.5))
        # Mark with a set of net names. A copper graphic has no net at all: its
        # net cannot be attributed, so it blocks every net and the answer says so.
        blank = 0
        for layer in self.layers:
            objs = self._obs[layer][0]
            for it in objs:
                net = it.get("net")
                if net:
                    self._mark_cells(it, {net})
                else:
                    blank += 1
                    self._mark_cells(it, {None})
        if blank:
            self.notes.append(
                "%d copper item(s) carry no net (graphics); they block every net "
                "because a clearance cannot be attributed to them" % blank)

    def _mark_keepouts(self, ignore_zone_areas):
        n_keep = n_zone = 0
        for z in self.board.zones or []:
            zl = [l for l in (z.get("layers") or []) if l in self.layers]
            if not zl:
                continue
            ring = z.get("outline") or []
            if len(ring) < 3:
                continue
            no_tr = no_via = True
            fill_in = False
            if z.get("is_keepout"):
                n_keep += 1
                ko = z.get("keepout") or {}
                # A keepout says what it keeps out. `(keepout (tracks allowed))`
                # is a zone a track may cross; walling it off in the raster would
                # close space the board file explicitly leaves open, and every
                # later net would route around a rule the board never made.
                no_tr = str(ko.get("tracks", "not_allowed")) != "allowed"
                no_via = str(ko.get("vias", "not_allowed")) != "allowed"
                self.keepouts.append({
                    "ring": list(ring), "layers": set(zl),
                    "tracks": no_tr, "vias": no_via,
                    "pads": str(ko.get("pads", "not_allowed")) != "allowed",
                    "copperpour": str(ko.get("copperpour", "not_allowed")) != "allowed",
                    "footprints": str(ko.get("footprints", "not_allowed")) != "allowed",
                    "bbox": list(G.bbox_of(ring))})
                if not no_tr and not no_via:
                    continue        # it constrains nothing this router places
                nets = frozenset([None])        # a keepout blocks every net
                fill_in = True                  # ...and it blocks its INTERIOR
            elif ignore_zone_areas:
                continue
            else:
                n_zone += 1
                nets = frozenset([z.get("net")] if z.get("net") else [None])
                # A pour is different: its copper is the copper, and a track
                # crossing it is legal (the pour is re-cut around tracks). Only
                # the rim is marked, so the search keeps off the pour's edge.
                fill_in = False
            sid = self._sid(nets)
            self._fill_ring(ring, zl, sid, no_tr, no_via, fill_in)
        if n_keep:
            open_keep = n_keep - sum(1 for k in self.keepouts
                                     if k["tracks"] or k["vias"])
            self.notes.append(
                "%d keepout zone(s) block every net"
                % n_keep
                + (("; %d of them constrain neither tracks nor vias and were "
                    "not walled off" % open_keep) if open_keep else ""))
        if n_zone:
            self.notes.append(
                "%d pour/zone area(s) block every net except their own; a pour is "
                "not re-cut around new tracks by this router" % n_zone)

    def _fill_ring(self, ring, zl, sid, no_tr=True, no_via=True, fill_in=True):
        """Block every raster cell within the margin of `ring` on layers `zl`.

        One implementation for board keepouts, pour rims and catalogue
        rectangles: a second copy is how the raster and the exact check drift
        apart, and then the search routes through a rule the judge still sees.
        """
        bx = G.bbox_of(ring)
        m_tr, m_v = self.margin, self.margin_via
        m = m_tr if m_tr > m_v else m_v
        nx = self.nx
        i0 = max(0, int(math.floor((bx[0] - m - self.x0) / self.pitch)))
        i1 = min(nx - 1, int(math.ceil((bx[2] + m - self.x0) / self.pitch)))
        j0 = max(0, int(math.floor((bx[1] - m - self.y0) / self.pitch)))
        j1 = min(self.ny - 1, int(math.ceil((bx[3] + m - self.y0) / self.pitch)))
        onb = self.onboard
        pitch = self.pitch
        for j in range(j0, j1 + 1):
            y = self.y0 + j * pitch
            base = j * nx
            for i in range(i0, i1 + 1):
                k = base + i
                if not onb[k]:
                    continue
                px = self.x0 + i * pitch
                d = G.dist_point_polygon((px, y), ring)
                # A keepout's whole interior is forbidden, not just a band
                # around its rim: copper inside it is what the zone is FOR.
                # Marking only the rim let the search walk straight through
                # the middle of a keepout and call it free. The test has to
                # be against the ring, before either margin decides anything
                # - checking it only past the larger margin left a band where
                # the track map said free and the via map said blocked.
                if fill_in and d > 0.0 and G.point_in_polygon((px, y), ring):
                    d = 0.0
                if d >= m:
                    continue
                for layer in zl:
                    if no_tr and d < m_tr:
                        self._put(self._map[layer], k, sid)
                    if no_via and d < m_v:
                        self._put(self._mapv[layer], k, sid)

    def _add_rule_keepouts(self, rects):
        """Wall off rectangles a rule forbids copper in, not the board file.

        `rects` carries ``x0/y0/x1/y1`` in file coordinates plus the rule id and
        the owner that produced it; a rectangle with no owner is dropped, because
        a block nobody can re-derive is a block nobody can audit.
        """
        kept = 0
        dropped = []
        for r in rects:
            rule = r.get("rule") or r.get("name")
            owner = r.get("owner") or r.get("ref")
            if not rule or not owner:
                dropped.append({"rect": [r.get("x0"), r.get("y0"),
                                          r.get("x1"), r.get("y1")],
                                "why": "no rule id or no owner"})
                continue
            x0, y0 = float(r["x0"]), float(r["y0"])
            x1, y1 = float(r["x1"]), float(r["y1"])
            if x1 < x0:
                x0, x1 = x1, x0
            if y1 < y0:
                y0, y1 = y1, y0
            layers = [l for l in (r.get("layers") or self.layers)
                      if l in self.layers]
            if not layers:
                continue
            ring = [(x0, y0), (x1, y0), (x1, y1), (x0, y1)]
            self.keepouts.append({
                "ring": ring, "layers": set(layers),
                "tracks": True, "vias": True, "pads": True,
                "copperpour": True, "footprints": False,
                "bbox": [x0, y0, x1, y1],
                "rule": rule, "owner": owner})
            self._fill_ring(ring, layers, self._sid(frozenset([None])))
            kept += 1
        self.rule_keepouts = kept
        if kept or dropped:
            self.notes.append(
                "%d rectangle(s) from the rule catalogue block every net "
                "(same raster and same exact check as a board keepout)"
                % kept
                + ("; %d dropped for having no rule id or no owner" % len(dropped)
                   if dropped else ""))
        self.dropped_rule_keepouts = dropped

    # ----------------------------------------------- growing the map with copper
    def add_copper(self, item):
        """Add copper this run created, so later nets must keep clear of it.

        This is the bug that produced 200 DRC errors on the first end-to-end run:
        the exact check compared every new segment against the items read from
        the FILE, so two nets routed in the same run never saw each other and
        drew right over each other (`shorting_items`, `tracks_crossing`). Copper
        the router adds has to go back into the map it searches, or the map is a
        description of a board that no longer exists.
        """
        nets = {item.get("net")}
        self._mark_cells(item, nets)
        for layer in (item.get("layers") or []):
            got = self._obs.get(layer)
            if got is None:
                continue
            objs, boxes, buckets = got
            idx = len(objs)
            objs.append(item)
            box = _item_bbox(item)
            boxes.append(box)
            pad = self.margin + 0.5
            for gx in range(int(math.floor((box[0] - pad) / self._cell)),
                            int(math.floor((box[2] + pad) / self._cell)) + 1):
                for gy in range(int(math.floor((box[1] - pad) / self._cell)),
                                int(math.floor((box[3] + pad) / self._cell)) + 1):
                    buckets.setdefault((gx, gy), []).append(idx)

    def add_track(self, net, layer, a, b, width):
        self.add_copper({"kind": "track", "net": net, "layer": layer,
                         "layers": [layer],
                         "seg": (tuple(a), tuple(b), float(width)),
                         "ref": "router"})

    def add_via(self, net, x, y, dia):
        self.add_copper({"kind": "via", "net": net, "layers": list(self.layers),
                         "poly": G.circle_points(x, y, dia / 2.0, 64),
                         "ref": "router"})

    # -------------------------------------------------------------- queries
    def cells_near(self, x, y, radius):
        """Every flat cell index within `radius` mm of a point.

        Used by the congestion measure, which asks "which cells can this copper
        be seen from" - the barrier it casts is wider than the copper itself, so
        two nets can be in conflict while never sharing a cell. Returns indices
        in `j * nx + i` order, clipped to the raster.
        """
        if radius <= 0:
            radius = self.pitch
        nx, ny = self.nx, self.ny
        i0 = int(math.floor((x - self.x0 - radius) / self.pitch - 1e-9))
        i1 = int(math.ceil((x - self.x0 + radius) / self.pitch + 1e-9))
        j0 = int(math.floor((y - self.y0 - radius) / self.pitch - 1e-9))
        j1 = int(math.ceil((y - self.y0 + radius) / self.pitch + 1e-9))
        i0, j0 = max(0, i0), max(0, j0)
        i1, j1 = min(nx - 1, i1), min(ny - 1, j1)
        out = []
        for j in range(j0, j1 + 1):
            base = j * nx
            for i in range(i0, i1 + 1):
                if math.hypot(self.x0 + i * self.pitch - x,
                              self.y0 + j * self.pitch - y) <= radius + 1e-9:
                    out.append(base + i)
        return out

    def pen_at(self, i, j):
        """The congestion penalty on one cell (0.0 when no penalty map exists)."""
        pen = self._pen
        if pen is None:
            return 0.0
        return pen[j * self.nx + i]

    def index(self, i, j):
        """Flat index, or None when the node is off the raster.

        Out-of-range indices must be refused here, not silently wrapped: the
        maps are plain Python lists, so `j * nx + i` with i = -5 lands on a
        perfectly ordinary in-board cell and a query about a point five
        millimetres outside the board comes back "free". The router never asks
        about such a node, but a caller checking a coordinate can, and the
        answer would be a guess rather than the truth.
        """
        if i < 0 or j < 0 or i >= self.nx or j >= self.ny:
            return None
        return j * self.nx + i

    def free(self, i, j, layer, net):
        k = self.index(i, j)
        if k is None:
            return False
        st = self._map[layer][k]
        if st == 0:
            return True
        if st < 0:
            return False
        s = self._sets[st - 1]
        return len(s) == 1 and net in s

    def free_via(self, i, j, layer, net):
        """Can a via of the class diameter stand here (raster, pessimistic)?"""
        k = self.index(i, j)
        if k is None:
            return False
        st = self._mapv[layer][k]
        if st == 0:
            return True
        if st < 0:
            return False
        s = self._sets[st - 1]
        return len(s) == 1 and net in s

    def blocked_reason(self, i, j, layer, net):
        """Which net is in the way (for a failure message a person can act on)."""
        k = self.index(i, j)
        if k is None or not self.onboard[k]:
            return {"reason": "off_board", "at": list(self.xy(i, j))}
        st = self._map[layer][k]
        if st == 0:
            return None
        if st < 0:
            return {"reason": "keepout", "net": None, "layer": layer,
                    "at": list(self.xy(i, j))}
        s = self._sets[st - 1]
        for n in s:
            if n != net:
                return {"reason": "keepout" if n is None else "net",
                        "net": n, "layer": layer, "at": list(self.xy(i, j))}
        return None

    def seg_blockers(self, a, b, layer, net, width=None, floor_mm=0.0):
        """Every other-net item this segment would violate, worst first.

        Exact geometry, per-pair clearance. This is the acceptance test; the
        raster above only proposed the move.

        `floor_mm` raises every pair's requirement to at least that number. The
        routing rules may allow 0.1 mm, but `edit.apply_plan` refuses a plan that
        lands copper inside its 0.2 mm floor (`clearance_no_worse`), so a caller
        that wants its plan to be writable passes the same floor here and never
        proposes copper the judge is going to throw out.
        """
        width = float(width if width is not None
                      else R.for_net(self.bundle, net)["track_mm"])
        got = self._obs.get(layer)
        if got is None:
            return [{"reason": "no_such_layer", "layer": layer}]
        objs, boxes, buckets = got
        seg = (tuple(a), tuple(b), width)
        probe = {"seg": seg}
        bx = _item_bbox(probe)
        slack = self.clr + width + 0.5
        cand = set()
        cell = self._cell
        for gx in range(int(math.floor((bx[0] - slack) / cell)),
                        int(math.floor((bx[2] + slack) / cell)) + 1):
            for gy in range(int(math.floor((bx[1] - slack) / cell)),
                            int(math.floor((bx[3] + slack) / cell)) + 1):
                idx = buckets.get((gx, gy))
                if idx:
                    cand.update(idx)
        out = []
        for k in cand:
            it = objs[k]
            if it.get("net") == net:
                continue                      # its own copper never blocks it
            need = R.pair_spacing(self.bundle, net, it.get("net"))
            if floor_mm > need:
                need = floor_mm
            ib = boxes[k]
            gapx = max(0.0, ib[0] - bx[2], bx[0] - ib[2])
            gapy = max(0.0, ib[1] - bx[3], bx[1] - ib[3])
            if math.hypot(gapx, gapy) >= need:
                continue                      # boxes too far apart to be close
            d = _dist(probe, it)
            if d >= need - 1e-9:
                continue
            out.append({"reason": "clearance", "net": it.get("net"),
                        "kind": it.get("kind"), "reference": it.get("ref"),
                        "layer": layer, "needed_mm": round(need, 4),
                        "actual_mm": round(d, 4),
                        "short_by_mm": round(need - d, 4)})
        out.extend(self._keepout_for_segment(tuple(a), tuple(b), layer, width))
        out.extend(self._edge_for_segment(tuple(a), tuple(b), width))
        out.sort(key=lambda w: -w["short_by_mm"])
        return out

    def _outline_segments(self):
        for ring in (self.board.outline or []):
            n = len(ring)
            if n < 3:
                continue
            for s in range(n):
                yield ring[s], ring[(s + 1) % n]

    def _too_close_to_edge(self, dist, half, where):
        need = self.edge + half
        if self.edge and dist < need - 1e-9:
            return {"reason": "too_close_to_edge", "net": None, "kind": "edge",
                    "layer": None, "at": list(where),
                    "needed_mm": round(need, 4), "actual_mm": round(dist, 4),
                    "short_by_mm": round(need - dist, 4)}
        return None

    def _edge_for_segment(self, a, b, width):
        """Is this track's copper inside the `edge` band along Edge.Cuts?

        Same rule the referee applies. It is a separate test from clearance
        because it has nothing to do with which net is next door.
        """
        if not self.edge or not self.board.outline:
            return []
        half = width / 2.0
        ax0, ax1 = (a[0], b[0]) if a[0] <= b[0] else (b[0], a[0])
        ay0, ay1 = (a[1], b[1]) if a[1] <= b[1] else (b[1], a[1])
        gap = None
        for c, d in self._outline_segments():
            bx0, bx1 = (c[0], d[0]) if c[0] <= d[0] else (d[0], c[0])
            by0, by1 = (c[1], d[1]) if c[1] <= d[1] else (d[1], c[1])
            if (ax1 + half < bx0 - self.edge or ax0 - half > bx1 + self.edge
                    or ay1 + half < by0 - self.edge
                    or ay0 - half > by1 + self.edge):
                continue
            g = 0.0 if G.segments_intersect(a, b, c, d) \
                else G.dist_segment_segment(a, b, c, d)
            if gap is None or g < gap:
                gap = g
                if gap <= half:
                    break
        if gap is None:
            return []                      # nothing in the edge band, nothing to say
        hit = self._too_close_to_edge(gap, half, a)
        return [hit] if hit else []

    def _edge_for_via(self, x, y, dia):
        if not self.edge or not self.board.outline:
            return []
        half = dia / 2.0
        gap = None
        for c, d in self._outline_segments():
            if (x + half + self.edge < min(c[0], d[0])
                    or x - half - self.edge > max(c[0], d[0])
                    or y + half + self.edge < min(c[1], d[1])
                    or y - half - self.edge > max(c[1], d[1])):
                continue
            g = G.dist_point_segment((x, y), c, d)
            if gap is None or g < gap:
                gap = g
                if gap <= half:
                    break
        if gap is None:
            return []
        hit = self._too_close_to_edge(gap, half, (x, y))
        return [hit] if hit else []

    def _keepout_for_segment(self, a, b, layer, width):
        """Copper may not enter a keepout at all - that is not a clearance.

        The raster below keeps the search out of these rings, but a raster cell
        is never the judge: a segment the search proposed still has to be
        measured against the ring itself, or a track can cross a keepout and be
        reported clean.
        """
        half = width / 2.0
        out = []
        ax0, ax1 = (a[0], b[0]) if a[0] <= b[0] else (b[0], a[0])
        ay0, ay1 = (a[1], b[1]) if a[1] <= b[1] else (b[1], a[1])
        for ko in self.keepouts:
            if layer not in ko["layers"] or not ko["tracks"]:
                continue
            bb = ko["bbox"]
            if (ax1 + half < bb[0] or ax0 - half > bb[2]
                    or ay1 + half < bb[1] or ay0 - half > bb[3]):
                continue
            ring = ko["ring"]
            if len(ring) < 3:
                continue
            why, gap = None, None
            if G.point_in_polygon(a, ring) or G.point_in_polygon(b, ring):
                why, gap = "inside_keepout", 0.0
            else:
                n = len(ring)
                for k in range(n):
                    c, d = ring[k], ring[(k + 1) % n]
                    if G.segments_intersect(a, b, c, d):
                        why, gap = "crosses_keepout", 0.0
                        break
                    g = G.dist_segment_segment(a, b, c, d)
                    if gap is None or g < gap:
                        gap = g
                if why is None and gap >= half - 1e-9:
                    continue
            out.append({"reason": "keepout", "why": why or "too_close_to_keepout",
                        "net": None, "kind": "keepout", "layer": layer,
                        "needed_mm": round(half, 4),
                        "actual_mm": round(gap, 4),
                        "short_by_mm": round(half - gap, 4)})
        return out

    def _keepout_for_via(self, x, y, layer, dia):
        half = dia / 2.0
        out = []
        poly = None
        for ko in self.keepouts:
            if layer not in ko["layers"] or not ko["vias"]:
                continue
            bb = ko["bbox"]
            if (x + half < bb[0] or x - half > bb[2]
                    or y + half < bb[1] or y - half > bb[3]):
                continue
            ring = ko["ring"]
            if len(ring) < 3:
                continue
            if G.point_in_polygon((x, y), ring):
                gap, why = 0.0, "inside_keepout"
            else:
                if poly is None:
                    poly = G.circle_points(x, y, half, 16)
                gap = G.dist_polygon_polygon(poly, ring)
                why = "too_close_to_keepout"
                if gap >= 1e-9:
                    continue
            out.append({"reason": "keepout", "why": why, "net": None,
                        "kind": "keepout", "layer": layer,
                        "needed_mm": round(half, 4),
                        "actual_mm": round(max(0.0, gap), 4),
                        "short_by_mm": round(half - max(0.0, gap), 4)})
        return out

    def seg_ok(self, a, b, layer, net, width=None):
        return self.seg_blockers(a, b, layer, net, width) == []

    def via_blockers(self, x, y, net, span=None):
        """Is a via legal here? Same exact test, on a circle of the class diameter."""
        spec = R.for_net(self.bundle, net)
        dia = float(spec["via_diameter_mm"])
        # The probe is a polygon, so its resolution is a correctness question and
        # not a speed one. A 16-gon inscribed in the drill sits up to
        # r*(1-cos(pi/16)) = 1.9% INSIDE the real circle: the check is then
        # optimistic by 2 um and passes copper KiCad's DRC calls 0.0987 mm apart.
        # 64 sides bring that error down to 0.12 um, which is below anything the
        # DRC can see, while staying inscribed: an exactly-on-the-rule gap (the
        # rule says 0.1 mm IS compliant) must not be refused, or a legal fan-out
        # turns into 11 unroutable nets.
        poly = G.circle_points(x, y, dia / 2.0, 64)
        span = span or self.layers
        out = []
        for layer in span:
            got = self._obs.get(layer)
            if got is None:
                continue
            objs, boxes, buckets = got
            probe = {"poly": poly}
            bx = _item_bbox(probe)
            slack = self.clr + dia + 0.5
            cell = self._cell
            cand = set()
            for gx in range(int(math.floor((bx[0] - slack) / cell)),
                            int(math.floor((bx[2] + slack) / cell)) + 1):
                for gy in range(int(math.floor((bx[1] - slack) / cell)),
                                int(math.floor((bx[3] + slack) / cell)) + 1):
                    idx = buckets.get((gx, gy))
                    if idx:
                        cand.update(idx)
            for k in cand:
                it = objs[k]
                if it.get("net") == net:
                    continue
                need = R.pair_spacing(self.bundle, net, it.get("net"))
                ib = boxes[k]
                gapx = max(0.0, ib[0] - bx[2], bx[0] - ib[2])
                gapy = max(0.0, ib[1] - bx[3], bx[1] - ib[3])
                if math.hypot(gapx, gapy) >= need:
                    continue
                d = _dist(probe, it)
                # ZERO geometric margin, but 1 nm of float slack: the rule says
                # a gap of exactly `need` IS compliant, and `circle_points` puts
                # a vertex at angle 0, so the orthogonal case measures exactly
                # `need` - which float subtraction reports a hair below it. The
                # 1 nm only cancels that error; it is not design margin. Real
                # margin is bought upstream by asking for a wider clearance
                # (0.105 mm on the RK3506 run), because this check must not
                # refuse a legal exactly-on-the-rule fan-out.
                if d >= need - 1e-6:
                    continue
                out.append({"reason": "clearance", "net": it.get("net"),
                            "kind": it.get("kind"), "reference": it.get("ref"),
                            "layer": layer, "needed_mm": round(need, 4),
                            "actual_mm": round(d, 4),
                            "short_by_mm": round(need - d, 4)})
        out.extend(self._keepout_for_via(x, y, layer, dia))
        # A via also has to be inside the board, with its own ring on the board.
        if self.board.outline:
            for px, py in poly:
                if not any(G.point_in_polygon((px, py), ring)
                           for ring in self.board.outline if len(ring) >= 3):
                    out.append({"reason": "off_board", "at": [x, y]})
                    break
        out.extend(self._edge_for_via(x, y, dia))
        out.sort(key=lambda w: -w.get("short_by_mm", 99))
        return out

    def via_ok(self, x, y, net, span=None):
        return self.via_blockers(x, y, net, span) == []

    def copper_items_near(self, x, y, net, radius):
        """Items of `net` whose copper is within `radius` of (x, y).

        `[(item, layer), ...]`, plus one entry per pour of that net covering the
        point (`item` is the zone, `layer` the layer it covers). Needed by the
        ground-stitching pass, which has to land a via ON copper, and "on copper"
        is a question about items, not about a raster cell.
        """
        out = []
        if not net or radius < 0:
            return out
        for layer in self.layers:
            got = self._obs.get(layer)
            if got:
                objs, boxes, _buckets = got
                for k, it in enumerate(objs):
                    if it.get("net") != net:
                        continue
                    b = boxes[k]
                    if b[0] - radius > x or b[2] + radius < x:
                        continue
                    if b[1] - radius > y or b[3] + radius < y:
                        continue
                    if self._dist_point(it, x, y) <= radius:
                        out.append((it, layer))
            for z in (self.board.zones or []):
                if z.get("is_keepout") or z.get("net") != net:
                    continue
                if layer not in (z.get("layers") or []):
                    continue
                ring = z.get("outline") or []
                if len(ring) >= 3 and G.point_in_polygon((x, y), ring):
                    out.append((z, layer))
        return out

    def copper_layers_near(self, x, y, net, radius):
        """Layers on which `net` has copper within `radius` of (x, y).

        "Copper" is a pad, a track, a via of that net, **or a pour of that net
        covering the point**. The pour is the case that matters: on a board whose
        ground IS a pour there is no ground track anywhere, and answering "no
        ground here" would refuse to stitch the very boards that are stitched by
        construction.
        """
        if not net or radius <= 0:
            return []
        out = []
        for layer in self.layers:
            hit = False
            got = self._obs.get(layer)
            if got:
                objs, boxes, _buckets = got
                for k, it in enumerate(objs):
                    if it.get("net") != net:
                        continue
                    b = boxes[k]
                    if b[0] - radius > x or b[2] + radius < x:
                        continue
                    if b[1] - radius > y or b[3] + radius < y:
                        continue
                    if self._dist_point(it, x, y) <= radius:
                        hit = True
                        break
            if not hit:
                for z in (self.board.zones or []):
                    if z.get("is_keepout") or z.get("net") != net:
                        continue
                    if layer not in (z.get("layers") or []):
                        continue
                    ring = z.get("outline") or []
                    if len(ring) >= 3 and G.point_in_polygon((x, y), ring):
                        hit = True
                        break
            if hit:
                out.append(layer)
        return out


# --------------------------------------------------------------------- search
DIRS = ((1, 0, 1.0), (-1, 0, 1.0), (0, 1, 1.0), (0, -1, 1.0),
        (1, 1, DIAG), (1, -1, DIAG), (-1, 1, DIAG), (-1, -1, DIAG))
DIRS_K = tuple((k, d[0], d[1], d[2]) for k, d in enumerate(DIRS))


def astar(grid, net, sources, goals, goals_layers, budget=MAX_BUDGET,
          allow_vias=True, via_check=None, diag=None, soft=0.0, guide=None,
          guide_outside=1.0, walk_layers=None, reserve=None,
          reserve_price=1.0):
    # NOTE ON THE INLINING BELOW. The inner loop reads the rasters directly
    # (`grid._map[layer]` is a flat list indexed by `j * nx + i`) instead of
    # calling `grid.free()`, and goal lookup goes through an integer key instead
    # of a tuple. That is not premature: a cProfile of a 12-net run on the F401
    # core board (229 s total) spent 63 s inside `free`, 53 s inside the
    # `dict.get` behind it, 33 s in the via gate and 12 s in `heappop`. The
    # search asks "is this cell free" some 70 million times, and each answer was
    # three tuple hashes. The logic is unchanged - the same pessimism, the same
    # no-corner-cutting rule, the same turn and via costs.
    """8-way A* between a set of sources (a tree) and a set of goals.

    `sources` is an iterable of `(i, j, layer)`; `goals_layers` maps `(i, j)` to
    the set of layers a goal may be reached on. Every node carries the direction
    it was reached from, so a turn can cost something - otherwise the paths come
    out as staircases. Vias are searched as a move between layers at the same
    node, and are only taken on a node that passes `via_check`.

    `soft` turns a refusal into a price: when it is > 0, another net's copper
    (and a keepout or the area off the board) may still be entered, at `soft`
    extra per cell. That is not a routing mode - the path it returns is allowed
    to cross other nets and is never emitted - it is how the congestion loop asks
    "if everyone else were merely expensive, where would this net go?". The
    answer is the corridor the net WANTS, and the cells it crosses name the nets
    standing in it.

    `guide` is a per-layer map of cell discounts, `{layer: bytearray(nx*ny)}`,
    where a cell's value divided by 255 is the fraction of the step cost that
    is refunded inside it. It is how "these nets must share one corridor"
    becomes a search preference instead of a meander added afterwards: a net
    walking inside another member's corridor pays less per cell than one
    cutting a fresh path of its own, so the group converges on one geometry
    and the group's spread comes out of the layout rather than out of tuning.
    It changes cost and only cost - a cell the raster calls blocked stays
    blocked, so a guide can never trade legality for tidiness. `None` leaves
    every cost exactly what it was.

    `guide_outside` prices the cells a guide does NOT cover, as a multiple of
    their usual step cost. A discount alone is a preference the search may
    ignore; with `guide_outside` > 1 the corridor is where the cheap walking
    is, so a member that could cut a shorter line of its own pays for doing
    so. That is what makes the group's *length* follow the skeleton rather
    than merely its direction.

    `reserve` is the mirror image of `guide`: a per-layer map
    (`{layer: bytearray(nx*ny)}`) of cells this net must pay `reserve_price`
    times their usual step cost to walk through. It exists because a corridor
    that is merely *shared* is not the same as a corridor that leaves room to
    work in: a bus whose members are packed at clearance has no lateral space
    left for the meanders that length matching needs (a readable wave needs
    ~1.5x track width of air on each side, see `route.tune_board`). Marking the
    ring around the nets that will need that room makes everyone else take the
    long way round *while they are still being routed*, which is the only
    moment the room can be created - after the fact there is copper in it.
    Like `guide` it changes cost and only cost: a cell the raster calls blocked
    stays blocked whatever the price, so a reservation can never buy a clearance
    violation, and a price that turns out to be too high costs a longer path
    rather than a missing one.

    `walk_layers` is the set of layers the path may put copper on. `None` is
    every layer in the grid. A node on a layer outside the set can still be a
    source or a goal - that is how a terminal on an outer layer is entered - but
    no step leaves it, so the only way off such a node is a via. The grid keeps
    all layers regardless: a via still has to clear the copper on the layers it
    is not allowed to use, which is the whole point of routing a bus inside.

    Returns the path as `[(i, j, layer), ...]` or `None`.
    """
    goals = set(goals)
    if not goals or not sources:
        return None
    nx, ny = grid.nx, grid.ny
    maps, mapsv, sets = grid._map, grid._mapv, grid._sets
    # Negotiated congestion: an all-zero list when nobody set a price, so the
    # inner loop always pays one index and one add and never a branch. The list
    # is owned by the grid so every net in a round sees the same prices.
    pen = grid._pen
    if pen is None:
        pen = grid._pen = [0.0] * (nx * ny)
    gd_maps = guide
    rsv_maps = reserve
    layers = grid.layers
    nlayer = len(layers)
    li_of = dict((l, k) for k, l in enumerate(layers))
    # `outside` is the single scalar a cell is decoded from: (node * nlayer + li)
    # * 9 + direction.
    gi = sum(g[0] for g in goals) / float(len(goals))
    gj = sum(g[1] for g in goals) / float(len(goals))
    heur_w = HEUR_W
    goal_layers = {}
    for (gi_, gj_) in goals:
        goal_layers[gj_ * nx + gi_] = goals_layers.get((gi_, gj_), ())

    best, parent, heap = {}, {}, []
    for (i, j, l) in sources:
        st = ((j * nx + i) * nlayer + li_of[l]) * 9 + 8
        if st in best:
            continue
        best[st] = 0.0
        parent[st] = None
        heap.append((heur_w * math.hypot(i - gi, j - gj), 0.0, st))
    heapq.heapify(heap)
    seen = 0
    via_memo = {}
    while heap:
        _f, g, st = heapq.heappop(heap)
        if g > best.get(st, 1e18) + 1e-9:
            continue
        d = st % 9
        rest = st // 9
        l = layers[rest % nlayer]
        node = rest // nlayer
        j = node // nx
        i = node - j * nx
        gl = goal_layers.get(node)
        if gl is not None and l in gl:
            path, s = [], st
            while s is not None:
                sd = s % 9
                srest = s // 9
                snode = srest // nlayer
                sj = snode // nx
                path.append((snode - sj * nx, sj, layers[srest % nlayer]))
                s = parent[s]
            return path[::-1]
        seen += 1
        if seen > budget:
            if diag is not None:
                diag["budget_hit"] = True
                diag["expanded"] = seen
            return None
        mp = maps[l]
        gmap = gd_maps.get(l) if gd_maps is not None else None
        rmap = rsv_maps.get(l) if rsv_maps is not None else None
        base = j * nx
        for k, di, dj, step in (DIRS_K if (walk_layers is None or l in walk_layers)
                                else ()):
            ni = i + di
            nj = j + dj
            if ni < 0 or nj < 0 or ni >= nx or nj >= ny:
                continue
            val = mp[nj * nx + ni]
            toll = 0.0
            if val:
                if val < 0:
                    if not soft:
                        continue
                    toll = soft
                else:
                    s = sets[val - 1]
                    if not (len(s) == 1 and s[0] == net):
                        if not soft:
                            continue
                        toll = soft
            if di and dj:
                # No corner cutting: both orthogonal neighbours must be free, or
                # the diagonal shaves a corner off another net's copper.
                val = mp[base + ni]
                if val:
                    if val < 0:
                        if not soft:
                            continue
                    else:
                        s = sets[val - 1]
                        if not (len(s) == 1 and s[0] == net) and not soft:
                            continue
                val = mp[nj * nx + i]
                if val:
                    if val < 0:
                        if not soft:
                            continue
                    else:
                        s = sets[val - 1]
                        if not (len(s) == 1 and s[0] == net) and not soft:
                            continue
            cell = nj * nx + ni
            if gmap is None:
                step_cost = step
            else:
                gv = gmap[cell]
                if gv:
                    step_cost = step * (1.0 - gv * INV255)
                elif guide_outside != 1.0:
                    step_cost = step * guide_outside
                else:
                    step_cost = step
            if rmap is not None and rmap[cell]:
                step_cost = step_cost * reserve_price
            nc = g + step_cost + (0.0 if d == k else TURN_COST) + pen[cell] + toll
            ns = (cell * nlayer + (rest % nlayer)) * 9 + k
            if nc < best.get(ns, 1e18) - 1e-9:
                best[ns] = nc
                parent[ns] = st
                heapq.heappush(heap, (nc + heur_w * math.hypot(ni - gi, nj - gj),
                                      nc, ns))
        if not allow_vias:
            continue
        if node in goal_layers:
            # A via drilled exactly on a goal node is either a via in the pad
            # (banned) or, worse, a way to *claim* the goal without reaching it
            # on a layer the goal accepts: on /DDR_DQ9 the search ran on F.Cu to
            # the escape-via node and "arrived" by dropping a zero-cost via onto
            # the pre-placed escape via - the net came back routed and left that
            # via carrying one layer (`via_dangling`). The goal has to be
            # reached by copper on one of its own layers, so no via here.
            continue
        if via_check is not None:
            got = via_memo.get(node)
            if got is None:
                got = via_memo[node] = 1 if via_check(i, j, net) else 0
            if not got:
                continue
        for l2 in layers:
            if l2 == l:
                continue
            val = mapsv[l2][node]
            vtoll = 0.0
            if val:
                if val < 0:
                    if not soft:
                        continue
                    vtoll = soft
                else:
                    s = sets[val - 1]
                    if not (len(s) == 1 and s[0] == net):
                        if not soft:
                            continue
                        vtoll = soft
            if rmap is not None and rmap[node]:
                vtoll += VIA_COST * (reserve_price - 1.0)
            nc = g + VIA_COST + (0.0 if d == 8 else TURN_COST) + pen[node] + vtoll
            ns = (node * nlayer + li_of[l2]) * 9 + 8
            if nc < best.get(ns, 1e18) - 1e-9:
                best[ns] = nc
                parent[ns] = st
                heapq.heappush(heap, (nc + heur_w * math.hypot(i - gi, j - gj),
                                      nc, ns))
    return None


def simplify(path):
    """Drop collinear intermediate nodes, keeping layer changes."""
    if not path:
        return []
    out = [path[0]]
    for p in path[1:]:
        if p[2] != out[-1][2]:
            out.append(p)
            continue
        if len(out) >= 2 and out[-1][2] == out[-2][2]:
            ax, ay = out[-2][0], out[-2][1]
            bx, by = out[-1][0], out[-1][1]
            cx, cy = p[0], p[1]
            if (bx - ax) * (cy - ay) - (by - ay) * (cx - ax) == 0:
                out[-1] = p
                continue
        out.append(p)
    return out


def mst_edges(nodes):
    """Prim's algorithm on a small point set: which pairs to connect, and in
    what order. Returns `[(a, b), ...]` indices."""
    n = len(nodes)
    if n < 2:
        return []
    used = [False] * n
    used[0] = True
    out = []
    best = [None] * n
    for k in range(1, n):
        best[k] = (math.hypot(nodes[k][0] - nodes[0][0],
                              nodes[k][1] - nodes[0][1]), 0)
    while len(out) < n - 1:
        pick, bd = None, None
        for k in range(n):
            if used[k] or best[k] is None:
                continue
            if bd is None or best[k][0] < bd:
                pick, bd = k, best[k][0]
        if pick is None:
            break
        out.append((best[pick][1], pick))
        used[pick] = True
        for k in range(n):
            if used[k]:
                continue
            d = math.hypot(nodes[k][0] - nodes[pick][0],
                           nodes[k][1] - nodes[pick][1])
            if best[k] is None or d < best[k][0]:
                best[k] = (d, pick)
    return out
