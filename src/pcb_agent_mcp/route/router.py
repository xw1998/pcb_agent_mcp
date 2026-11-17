"""The search itself: which pad connects to which, in what order, and what to do
when a net loses.

The shape of a run:

1. resolve the rules (`rules.resolve`) - a router with invented rules draws
   copper its own DRC rejects, so a board with no clearance is refused here;
2. decide which layers are routing layers and which are planes (`_split_layers`);
   a plane is a layer that a pour of a power net already covers, and a signal net
   is not routed across it - it drops ONE via into it, the way a person does;
3. per net, build a minimum spanning tree over its terminals and route the edges;
   nets are ordered longest-first because on the F401 core board the four nets
   that failed with shortest-first were exactly the four longest;
4. nets that failed get a second sweep (same free space, new tree root) and then
   a bounded **rip-up** round: the shortest nets already routed are pulled out and
   re-routed together with the net that lost. Bounded, and every rip is counted;
5. every emitted segment and via is re-checked with exact geometry. A segment the
   exact check rejects takes its whole net down with it - copper is never kept on
   the strength of a raster cell.

Nothing here writes to a file. The result is ops in the standard shape, which the
caller feeds to `edit.propose` + `edit.apply_plan`.
"""

import math
import time

from .. import geometry as G
from . import blockers as BLOCK
from . import rules as R
from . import topology as TOPO
from . import tune
from .grid import Grid, astar, mst_edges, simplify

# Negotiated congestion (see `_negotiate`). `SOFT_BLOCK` is what another net's
# cell costs in a "where would I go" search. It is deliberately SMALL (five
# cells): the question is "which corridor does this net want", and the honest
# answer is the near-straight one. A large toll makes the search hunt for a far
# detour that costs less than the crossing - that is the answer to a different
# question, and on a 240k-cell board it also spends the whole budget doing it
# (measured: 60.0 made one wish search on the 60x40 mm probe board run past a
# minute without finishing). Five cells still biases the path away from
# gratuitous crossings, which is enough to name who is in the way.
SOFT_BLOCK = 0.5
# The wish search is a diagnostic, not a route: it is allowed to give up early.
# A near-straight corridor needs a few thousand expansions, so this is a
# generous ceiling rather than a working limit.
WISH_BUDGET = 40000

# A net whose name looks like this has a plane layer on a typical board. It is
# only used to *guess which layers are planes*; the pour in the file has to agree,
# and if it does not, the guess is dropped (see `_split_layers`).
POWER_HINTS = ("GND", "AGND", "DGND", "PGND", "VSS", "VCC", "VDD", "VIN",
               "VBAT", "VBUS", "+3V3", "+5V", "+12V", "3V3", "5V0", "VREF")

# How far from a pad centre to look for a legal terminal cell when the centre
# itself is buried in another net's halo.
TERMINAL_SEARCH_MM = 1.6


class RouteError(Exception):
    def __init__(self, message, reason_code="ROUTE_FAILED", hint=None):
        Exception.__init__(self, message)
        self.message = message
        self.reason_code = reason_code
        self.hint = hint


def _looks_power(net):
    up = (net or "").upper()
    return any(up == h or up.startswith(h) or up.endswith(h) for h in POWER_HINTS)


def _copper_layers(board):
    names = [str(s.get("name")) for s in (board.stackup or [])
             if str(s.get("name") or "").endswith(".Cu")]
    return names or ["F.Cu", "B.Cu"]


def _split_layers(board, copper, explicit=None):
    """Routing layers vs plane layers, and WHY each plane is a plane.

    A layer counts as a plane only when a non-keepout zone with a power-looking
    net covers it. Guessing "the inner layers are planes" would drop a signal
    layer on a 4-layer board that routes on all four, so the pour has to be in the
    file for the layer to be demoted.
    """
    if explicit:
        routing = [l for l in explicit if l in copper]
        if not routing:
            raise RouteError("none of %s is a copper layer (board has %s)"
                             % (list(explicit), ", ".join(copper)),
                             "ARG_INVALID")
        return routing, {}
    planes = {}
    for z in board.zones or []:
        if z.get("is_keepout") or not z.get("net"):
            continue
        if not _looks_power(z.get("net")):
            continue
        ring = z.get("outline") or []
        if len(ring) < 3:
            continue
        area = abs(G.polygon_area(ring))
        for layer in (z.get("layers") or []):
            if layer not in copper:
                continue
            got = planes.setdefault(layer, {"net": z.get("net"), "area_mm2": 0.0})
            got["area_mm2"] = round(got["area_mm2"] + area, 2)
    routing = [l for l in copper if l not in planes]
    if not routing:
        raise RouteError("every copper layer on this board is a plane, so there "
                         "is nowhere to route a signal",
                         "NO_ROUTING_LAYER")
    return routing, planes


def _apply_terminal_override(terms, override, grid, routing):
    """Swap a pad-centre terminal for the escape-via centres the caller placed.

    See `plan`'s `terminal_override`. The terminal keeps the pad's identity
    (reference/number) so a failure still names the pad a person can look at,
    and records the pad centre it replaced so the report stays auditable.
    """
    want = {}
    for net, pts in (override or {}).items():
        pts = list(pts or ())
        if not pts:
            continue
        # A point is either `[x, y]` (the terminal sits on every routing layer,
        # which lets the search keep a whole net on one layer and leave the
        # escape via connected on one side only - a dangling via) or
        # `{"at": [x, y], "layers": [...]}` (the terminal arrives on named
        # layers only, so the via that carried the pad down is the one and only
        # way onto that layer). The second form is what keeps every escape via
        # carrying current.
        out_pts = []
        for p in pts:
            if isinstance(p, dict):
                at = p.get("at") or p.get("xy") or []
                pts_only = [str(l) for l in (p.get("layers") or ())] or None
                out_pts.append((float(at[0]), float(at[1]), pts_only))
            else:
                out_pts.append((float(p[0]), float(p[1]), None))
        want[str(net)] = out_pts
    out = []
    used = {}
    for t in terms:
        pts = want.get(t["net"])
        if not pts:
            out.append(t)
            continue
        if used.get(t["net"]):
            continue                      # one override covers the whole net
        used[t["net"]] = True
        for x, y, only in pts:
            i, j = grid.node(x, y)
            lays = [l for l in routing if l in only] if only else list(routing)
            term = {"reference": t["reference"], "pad": t["pad"],
                    "net": t["net"],
                    "at": [round(x, 4), round(y, 4)],
                    "layers": lays or list(routing), "node": (i, j),
                    "half_mm": 0.1,
                    "escaped_from": t["at"]}
            # Pinned terminals are escape vias: the pad's own copper is already
            # on the outer layer, so letting the search touch that copper makes
            # the via dangle (it carries one layer and nothing else).  Marked so
            # `_route_signal_net` insists on arriving on the pinned layers.
            if only:
                term["pinned"] = True
            out.append(term)
    report = {"nets": sorted(used), "points": sum(len(v) for v in want.values())}
    return out, report


def _pad_terminals(board, grid, layers):
    """Every pad that carries a net, as a terminal the search can reach."""
    from ..adapters.canonical import pad_points  # noqa: F401  (pad geometry)

    out = []
    for fp in board.footprints:
        ref = fp.get("ref") or fp.get("reference")
        for pad in fp.get("pads") or []:
            net = pad.get("net")
            if not net or pad.get("x") is None:
                continue
            hole = pad.get("hole_mm")
            if (pad.get("type") == "np_thru_hole" and hole
                    and float(pad.get("w") or 0) <= float(hole) + 1e-6
                    and float(pad.get("h") or 0) <= float(hole) + 1e-6):
                continue                       # no copper at all
            raw = pad.get("layers") or []
            if "*.Cu" in raw:
                pad_layers = list(layers)
            elif "F&B.Cu" in raw:
                pad_layers = [l for l in layers if l in ("F.Cu", "B.Cu")]
            else:
                pad_layers = [l for l in layers if l in raw]
            if hole is not None and not pad_layers:
                pad_layers = list(layers)      # a drilled pad is on every copper
            if not pad_layers:
                continue
            i, j = grid.node(float(pad["x"]), float(pad["y"]))
            term = {"reference": ref, "pad": pad.get("number"), "net": net,
                    "at": [round(float(pad["x"]), 4), round(float(pad["y"]), 4)],
                    "layers": pad_layers, "node": (i, j),
                    "half_mm": max(float(pad.get("w") or 0),
                                   float(pad.get("h") or 0)) / 2.0}
            out.append(term)
    return out


def _free_terminal(grid, term, net):
    """Pad centre if it is reachable for its own net, else the nearest free cell.

    The centre of a fine-pitch pad is inside its own copper, so it is free for its
    own net by construction. It is only searched for when another net's halo has
    grown over it (a pad inside another footprint's courtyard, a test point under
    a connector), which is a real thing on crowded boards.
    """
    i, j = term["node"]
    for layer in term["layers"]:
        if grid.free(i, j, layer, net):
            return (i, j, layer)
    r_max = int(math.ceil(TERMINAL_SEARCH_MM / grid.pitch))
    for r in range(1, r_max + 1):
        best = None
        for di in range(-r, r + 1):
            for dj in range(-r, r + 1):
                if max(abs(di), abs(dj)) != r:
                    continue
                ni, nj = i + di, j + dj
                for layer in term["layers"]:
                    if not grid.free(ni, nj, layer, net):
                        continue
                    d = math.hypot(di, dj)
                    if best is None or d < best[0]:
                        best = (d, (ni, nj, layer))
        if best is not None:
            return best[1]
    return None


def _existing_tree(board, grid, net, layers):
    """Nodes already covered by this net's own copper (for incremental routing).

    Every existing track segment and via of the net seeds the tree, so a second
    run only routes what is actually missing instead of re-routing the net from
    scratch and doubling its copper.
    """
    seed = set()
    for tr in board.tracks:
        if tr.get("net") != net or tr.get("layer") not in layers:
            continue
        pts = tr.get("_points") or [tr.get("start"), tr.get("end")]
        for p in pts:
            i, j = grid.node(float(p[0]), float(p[1]))
            seed.add((i, j, tr.get("layer")))
    for via in board.vias:
        if via.get("net") != net:
            continue
        i, j = grid.node(float(via["x"]), float(via["y"]))
        for layer in layers:
            if grid.free(i, j, layer, net):
                seed.add((i, j, layer))
    return seed


def _touches_own(board, net, x, y, half, slack=0.06):
    """Is this pad already connected by its own net's copper?"""
    for tr in board.tracks:
        if tr.get("net") != net:
            continue
        pts = tr.get("_points") or [tr.get("start"), tr.get("end")]
        w = float(tr.get("width") or 0.0)
        for k in range(len(pts) - 1):
            if G.dist_point_segment((x, y), tuple(pts[k]), tuple(pts[k + 1])) \
                    <= half + w / 2.0 + slack:
                return True
    for via in board.vias:
        if via.get("net") != net:
            continue
        dia = float(via.get("diameter") or via.get("drill") or 0.4)
        if math.hypot(float(via["x"]) - x, float(via["y"]) - y) <= half + dia / 2.0 + slack:
            return True
    return False


class _Emitter(object):
    """Collects ops for ONE net at a time, and only lets it into the map on success.

    Three states matter here and getting them wrong is how a router paints over
    its own copper:

    * **buffered** - an op has passed the exact check but the net is still being
      routed. It is NOT in the search map: another net might still cause this one
      to fail, and a failed net must leave nothing behind.
    * **committed** - the net finished. Its copper goes into the map so every
      later net has to route around it.
    * **rolled back** - the net failed. Its buffer is dropped; the rejection
      reasons are kept because they are the explanation of the failure.
    """

    def __init__(self, grid, bundle, board=None, layers=None, pitch=None,
                 ignore_zone_areas=False, blind_vias=False):
        self.grid = grid
        self.bundle = bundle
        self.board = board
        self.layers = list(layers or grid.layers)
        self.pitch = pitch if pitch is not None else grid.pitch
        # Kept so a rebuilt map (rip-up) is the same map: dropping this flag made
        # the second pass route around pour areas the first pass had been told to
        # ignore, which is not a rip-up, it is a different board.
        self.ignore_zone_areas = bool(ignore_zone_areas)
        # A blind/buried via is copper on a *slice* of the stack, so it is only
        # offered when the caller asked for one: it costs more to make and it
        # has to be checked against exactly the layers it occupies.
        self.blind_vias = bool(blind_vias)
        self.ops = []                 # committed ops, in emission order
        self.bad = []                 # exact-check rejections, kept for the report
        self.length = {}
        self._buf = []
        self._buf_net = None
        # Vias the net already has, so a search that arrives at a point where a
        # via of the same net already sits does not drill a second hole there.
        # Two 0.1 mm drills 0.15 mm apart is a `hole_to_hole` error, and the
        # copper is no better connected for it: a via fan-out handed to this
        # router as pre-placed escape vias produced 25 of exactly those pairs.
        self._vias_by_net = {}
        self._buf_vias = []
        self._seed_vias()

    def _seed_vias(self):
        """Start from the vias the board already has, then the ones replay adds."""
        self._vias_by_net = {}
        for via in (getattr(self.board, "vias", None) or []):
            self._vias_by_net.setdefault(via.get("net"), []).append(
                (float(via["x"]), float(via["y"]),
                 list(via.get("layers") or [])))
        for op in self.ops:
            if op.get("op") == "add_via":
                span = op.get("layers") or list(self.layers)
                self._vias_by_net.setdefault(op.get("net"), []).append(
                    (float(op["x"]), float(op["y"]), list(span)))

    def _via_covers(self, net, x, y, span):
        """Is there already a via of `net` close enough at (x, y) to reuse?

        The tolerance is not a rounding slack: the search lands on grid nodes,
        the escape vias were placed off-grid, so the nearest node to a placed
        via is up to half a pitch away on both axes.  At the old 1e-3 that
        never matched, and 25 pairs of same-net vias 0.02-0.03 mm apart came
        out of one fan-out - `hole_to_hole` errors whose copper gains nothing.
        """
        tol = self.pitch * 1.5
        known = list(self._vias_by_net.get(net, ()))
        known += [(v[1], v[2], v[3]) for v in self._buf_vias if v[0] == net]
        for vx, vy, vlayers in known:
            if abs(vx - x) > tol or abs(vy - y) > tol:
                continue
            if not vlayers or all(l in vlayers for l in span):
                return True
        return False

    # ------------------------------------------------------------ lifecycle
    def mark(self, net):
        self._buf = []
        self._buf_vias = []
        self._buf_net = net
        return {"net": net, "n_bad": len(self.bad)}

    def commit(self, mark):
        """The net finished: its copper joins the map and the op list."""
        net = mark["net"]
        for item, op in self._buf:
            self.grid.add_copper(item)
            self.ops.append(op)
        for v in self._buf_vias:
            self._vias_by_net.setdefault(v[0], []).append(v[1:])
        self._buf = []
        self._buf_vias = []

    def rollback(self, mark):
        """The net failed: drop its buffer, keep the reasons it failed."""
        net = mark["net"]
        self._buf = []
        # Emitted-but-uncommitted length must not leak into the answer.
        self.length[net] = 0.0

    def forget(self, nets):
        """Rip nets out completely and rebuild the search map from what is left.

        Used by the rip-up round. Rebuilding is the only honest way to do this:
        the map's cells and its spatial hash have no per-net undo, and a map that
        still remembers ripped copper is a map of a board that does not exist.
        """
        nets = set(nets)
        self.ops = [op for op in self.ops if op.get("net") not in nets]
        for n in nets:
            self.length[n] = 0.0
        self.rebuild()

    def rebuild(self):
        """Re-derive the search map from the ops that are still standing.

        The map has no per-op undo, and a post-pass that replaced copper (a
        meander inserted for length matching) leaves the map describing a board
        that no longer exists. Replaying the surviving ops is the only version
        that cannot drift.
        """
        self.grid = Grid(self.board, self.layers, self.pitch, self.bundle,
                         ignore_zone_areas=self.ignore_zone_areas)
        for op in self.ops:
            self._replay(op)
        self._seed_vias()             # the replayed vias are holes that exist too

    def _replay(self, op):
        if op["op"] == "add_track":
            self.grid.add_track(op["net"], op["layer"],
                                (op["x1"], op["y1"]), (op["x2"], op["y2"]),
                                op["width"])
        else:
            self.grid.add_via(op["net"], op["x"], op["y"],
                              op.get("size") or self.grid.via_dia)

    def track(self, net, layer, a, b, width):
        x1, y1 = a
        x2, y2 = b
        if abs(x1 - x2) < 1e-9 and abs(y1 - y2) < 1e-9:
            return True
        blockers = self.grid.seg_blockers((x1, y1), (x2, y2), layer, net, width)
        if blockers:
            self.bad.append({"net": net, "layer": layer,
                             "from": [round(x1, 4), round(y1, 4)],
                             "to": [round(x2, 4), round(y2, 4)],
                             "why": blockers[0]})
            return False
        self._buf.append((
            {"kind": "track", "net": net, "layer": layer, "layers": [layer],
             "seg": ((x1, y1), (x2, y2), float(width)), "ref": "router"},
            {"op": "add_track", "x1": round(x1, 6), "y1": round(y1, 6),
             "x2": round(x2, 6), "y2": round(y2, 6),
             "width": width, "layer": layer, "net": net}))
        self.length[net] = self.length.get(net, 0.0) + math.hypot(x2 - x1, y2 - y1)
        return True

    def _span(self, span):
        """The requested span as a contiguous slice of this grid's stackup."""
        if not span:
            return list(self.layers)
        wanted = set(span)
        ordered = [l for l in self.layers if l in wanted]
        return ordered or list(self.layers)

    def via(self, net, x, y, span=None):
        """Place a via. `span` is the slice of the stack it must be copper on.

        With `blind_vias` off the span collapses to the whole stack **and** the
        collision test runs over the whole stack, so what the model thinks the
        via occupies and what the file says are the same thing. Turning it on
        writes `layers` for a partial span - that is a blind or buried via, and
        the exact check then covers only the layers it really sits on.
        """
        span = self._span(span)
        if not self.blind_vias:
            span = list(self.layers)
        if self._via_covers(net, x, y, span):
            return True               # the hole is already there; no second one
        blockers = self.grid.via_blockers(x, y, net, span)
        if blockers:
            self.bad.append({"net": net, "via_at": [round(x, 4), round(y, 4)],
                             "span": [span[0], span[-1]], "why": blockers[0]})
            return False
        spec = R.for_net(self.bundle, net)
        dia = float(spec["via_diameter_mm"])
        op = {"op": "add_via", "x": round(x, 6), "y": round(y, 6), "net": net,
              "size": spec["via_diameter_mm"], "drill": spec["via_drill_mm"]}
        if span != list(self.layers):
            op["layers"] = [span[0], span[-1]]
        self._buf_vias.append((net, x, y, list(span)))
        self._buf.append((
            {"kind": "via", "net": net, "layers": list(span),
             "poly": G.circle_points(x, y, dia / 2.0, 16), "ref": "router"},
            op))
        return True


def plan(board, pitch_mm=0.1, layers=None, nets=None, clearance_mm=None,
         track_mm=None, via_diameter_mm=None, via_drill_mm=None,
         edge_clearance_mm=None, creepage_mm=None, creepage_pairs=None,
         diff_pairs=None, impedance_targets=None, working_voltage_v=None,
         pollution_degree=2, material_group="IIIa", insulation="basic",
         coating=False, altitude_m=0, length_groups=None,
         rip_up_rounds=1, second_sweep=True, incremental=False,
         fixed_nets=None,
         congestion_rounds=2, congestion_max_rips=12, congestion_weight=1.0,
         allow_vias=True, max_nets=None, ignore_zone_areas=False,
         return_path=True, return_path_net=None, topology="mst",
         progress=None, blind_vias=False, keepout_rects=None, grid_origin=None,
         terminal_override=None, net_priority=None, bundle_bands=None,
         via_gate="raster", new_copper_layers=None):
    """Route what can be routed and say plainly what cannot.

    Returns a plain dict; the tool layer wraps it in an envelope. A net is only
    reported `routed` when every one of its MST edges found a path AND every
    emitted segment passed the exact clearance check.

    `progress` is an optional callable that gets one dict per finished net
    (`net`, `ok`, `status`, `s`). Routing a dense 50-net board takes minutes, and
    a caller who cannot see which net it is on has no way to tell "working" from
    "wedged".

    `keepout_rects` are rectangles the RULE CATALOGUE forbids copper in (see
    `ruleset.projection.router_inputs`), each a dict with ``x0/y0/x1/y1``,
    ``rule`` and ``owner``. They enter the same two gates a board keepout does -
    the raster the search walks on and the exact ring check every emitted segment
    is measured against. Passing them and having the router silently ignore them
    would be worse than not passing them at all, so a rect without a rule id or
    an owner is named in ``grid.dropped_rule_keepouts`` rather than dropped.

    `terminal_override` replaces the pad centre of named nets with a caller
    supplied list of points: `{"<net>": [[x, y], ...]}`. It exists for the case a
    plain router cannot handle by itself - a fine-pitch BGA where the search
    would have to drop a via in the pad centre to reach an inner layer, which is
    a manufacturing defect (via-in-pad). The caller lays the dogbone stub and its
    via first, then hands those via centres in as the terminals; the search then
    starts OUTSIDE the pad and never needs a via in a pad. The points are used
    verbatim - a point that is not on the board is the caller's error, not
    something to guess around, so a net named here with no usable point is
    reported rather than silently falling back to the pad centre.

    `via_gate` says how much geometry the A* is allowed to see before it
    pays for a via. "raster" (default, and the historical behaviour) is the
    occupancy raster alone: cheap, and OPTIMISTIC - the raster knows the
    cell is not obviously occupied, not that a 0.6 mm ring fits between two
    exactly-on-the-rule neighbours. "exact" runs `grid.via_blockers` - the
    same 64-gon test the emitter is judged by - as a confirmation before the
    via is priced, so a search that is short of space stops planning vias the
    emitter is going to reject. `exact` costs a real geometric test per
    candidate node, so it is a knob and not the default, and the criterion for
    turning it on is measured rather than felt: it earns its cost only when the
    run reports vias refused at emission (`stats.rejected_ops` carrying
    `clearance`). On the RK3506 SOM it earned nothing - the plan came out
    byte-identical to the raster gate's while taking 8.0x as long - so the
    honest reading there is "not needed", not "better". (An earlier note here
    blamed the raster gate for a DDR bus escaping to F.Cu; that turned out to
    be wrong - see `docs/189`, where the copper source is the middle of the
    path and the fix is `new_copper_layers`.)

    `new_copper_layers` separates two things that used to be one number.
    `layers` answers "which pads may I start from" - a terminal is collected
    when the pad has copper on one of them. It does NOT answer "where may this
    net put new copper", and conflating the two is how an inner-layer-only DDR
    plan kept coming back with F.Cu segments: the terminals were pinned to
    In2.Cu/B.Cu, and then the search walked the middle of the path on F.Cu
    because F.Cu was a legal terminal-collecting layer. Naming
    `new_copper_layers` narrows the WALK (A* steps) and the terminals'
    accepted layers to that set, while the grid keeps every layer for physics -
    a via still has to clear the copper on F.Cu it is not allowed to use. A
    terminal left with no layer in the set is reported per pad rather than
    quietly dropped, because "no terminal" is a layout fact and silent failure
    is a wrong answer. `None` (default) is the historical behaviour: any
    collected layer is also a new-copper layer.

    `bundle_bands` is how a bus gets a geometry instead of a pile of
    independently shortest paths. Each entry is
    `{"name": str, "nets": [...], "radius_mm": 0.8, "discount": 0.6}`:

    * the named nets are routed **first** and, inside the band, **shortest
      first** - the shortest path is the best skeleton for the others to
      follow, and starting from the longest net would make everyone inherit
      the worst detour;
    * once the first member is routed, its copper becomes a *guide*: cells
      within `radius_mm` of it refund `discount` of their step cost to the
      remaining members (`route.grid.astar(guide=...)`). The group converges
      on one corridor, so the spread that length matching cares about comes
      out of the layout;
    * the guide only changes cost. A cell the raster calls blocked stays
      blocked for every member, so a band cannot buy tidiness with a
      clearance violation.

    A band entry may also reserve room for the members that will have to
    meander: `{"reserve_nets": [...], "reserve_mm": 0.5, "reserve_price": 4.0}`
    marks a ring `reserve_mm` wide around those members' copper and multiplies
    every other member's step cost inside it by `reserve_price`
    (`route.grid.astar(reserve=...)`). A corridor that merely packs its members
    at clearance has no lateral space left for length matching, and the space
    cannot be created afterwards - by then there is copper in it - so the ring
    has to be claimed while the bus is being routed. `reserve_nets` must be
    members of the same band: a reservation is a claim the group makes on its
    own shared space, not a global keepout. Like the guide it changes cost and
    only cost, and the report carries the air it actually bought
    (`bundle_bands[i].reserve.gap_mm`, measured at the corridor's waist rather
    than at the pads, which are packed by definition).

    This is the honest half of "bundle routing": it does not force the members
    onto the identical polyline (that needs path templating the router does
    not have yet), it makes the corridor the cheapest place to be. A band that
    cannot be honoured is reported per band in `stats["bundle_bands"]` rather
    than silently ignored.

    `topology` says how a multi-pad net is joined: `"mst"` (default) is a
    spanning tree over the pad centres, `"steiner"` adds branch points that are
    not pads (see `route.topology`). A spanning tree can only bend at pads, so on
    a star of pads it draws longer copper than the same search needs to; the
    branch points cost one extra A* each and only appear where they strictly
    shorten the tree.
    """
    if topology not in ("mst", "steiner"):
        raise RouteError("unknown topology %r" % (topology,), "ARG_INVALID",
                         hint="use \"mst\" (spanning tree over pad centres) or "
                              "\"steiner\" (adds branch points that are not pads)")
    t0 = time.time()
    bundle = R.resolve(board, clearance_mm=clearance_mm, track_mm=track_mm,
                       via_diameter_mm=via_diameter_mm, via_drill_mm=via_drill_mm,
                       edge_clearance_mm=edge_clearance_mm,
                       creepage_mm=creepage_mm, creepage_pairs=creepage_pairs,
                       diff_pairs=diff_pairs,
                       impedance_targets=impedance_targets,
                       working_voltage_v=working_voltage_v,
                       pollution_degree=pollution_degree,
                       material_group=material_group, insulation=insulation,
                       coating=coating, altitude_m=altitude_m)
    copper = _copper_layers(board)
    routing, planes = _split_layers(board, copper, explicit=layers)

    grid_layers = list(routing) + [l for l in planes if l not in routing]
    grid = Grid(board, grid_layers, float(pitch_mm), bundle,
                ignore_zone_areas=ignore_zone_areas,
                extra_keepouts=keepout_rects, origin=grid_origin)
    em = _Emitter(grid, bundle, board=board, layers=grid_layers,
                  pitch=float(pitch_mm), ignore_zone_areas=ignore_zone_areas,
                  blind_vias=blind_vias)

    terms = _pad_terminals(board, grid, routing)
    if nets:
        wanted = set(nets)
        terms = [t for t in terms if t["net"] in wanted]
    if terminal_override:
        terms, override_report = _apply_terminal_override(
            terms, terminal_override, grid, routing)
    else:
        override_report = {}
    if not terms:
        raise RouteError("no pad with a net is on a routing layer, so there is "
                         "nothing to connect", "NO_GEOMETRY",
                         hint="pads with no net are not routed; a board whose only "
                              "nets are planes needs no router")

    # New copper layers. See the docstring: the terminal set above answers
    # "which pads are reachable at all", this answers "where may copper be
    # placed". The two differ on a board whose pads fan out on an outer layer
    # and whose bus is supposed to live on the inner ones.
    new_layers = None
    lost_terminals = []
    if new_copper_layers:
        named = [str(l) for l in new_copper_layers]
        copper_set = set(copper)
        bad = [l for l in named if l not in copper_set]
        if bad:
            raise RouteError(
                "%s is not a copper layer on this board (board has %s)"
                % (", ".join(bad), ", ".join(copper)), "ARG_INVALID",
                hint="new_copper_layers is read against the board, not guessed; "
                     "the layer names must match the stackup verbatim")
        new_layers = [l for l in named if l in routing]
        if not new_layers:
            raise RouteError(
                "none of %s is a routing layer, so no new copper could be placed"
                % (", ".join(named),), "ARG_INVALID",
                hint="routing layers on this board are %s"
                     % (", ".join(routing) or "(none)"))
        allowed = set(new_layers)
        kept = []
        for t in terms:
            keep_l = [l for l in t["layers"] if l in allowed]
            if not keep_l:
                lost_terminals.append({"net": t["net"], "pad": t["pad"],
                                       "reference": t["reference"],
                                       "at": t["at"],
                                       "layers": list(t["layers"])})
                continue
            t["layers"] = keep_l
            kept.append(t)
        terms = kept

    plane_nets = {}
    for layer, spec in planes.items():
        plane_nets.setdefault(spec["net"], []).append(layer)

    by_net = {}
    for t in terms:
        by_net.setdefault(t["net"], []).append(t)

    if max_nets:
        keep = sorted(by_net, key=lambda n: -len(by_net[n]))[:int(max_nets)]
        by_net = dict((k, by_net[k]) for k in keep)

    # ------------------------------------------------------------------ fixed
    # What "fixed" means here, and what it does not.
    #
    # Specctra writes the claim as `(type fix)` and backs it with a rule of its
    # own: the session file does not return fixed copper at all. This router
    # never edits copper that is already on the board, so the only way a fixed
    # net could move is by being ripped up and re-routed by this code. Three
    # rules follow, and the last one is the honest half:
    #
    #   * never a rip-up candidate (neither pass, negotiation included);
    #   * never re-routed, even when its pads look unconnected - routing it would
    #     move copper that was declared immovable;
    #   * a fixed net the board has NO copper for is reported, because "leave it
    #     alone and call it fixed" is the one outcome that would quietly ship an
    #     open net.
    fixed = set(str(n) for n in (fixed_nets or ()))
    fixed_copper = {}
    if fixed:
        # A name the board has never heard of gets no copper record: reporting
        # six zeros for it would look like "we looked and found nothing", when
        # the truth is that there is nothing to look at. It is listed under
        # `unknown` instead.
        pad_nets = set(p.get("net") for fp in board.footprints
                        for p in (fp.get("pads") or []) if p.get("net"))
        for net in sorted(fixed):
            segs = [tr for tr in board.tracks if tr.get("net") == net]
            vias = [v for v in board.vias if v.get("net") == net]
            if not segs and not vias and net not in pad_nets:
                continue
            length = 0.0
            for tr in segs:
                pts = tr.get("_points") or [tr.get("start"), tr.get("end")]
                for k in range(len(pts) - 1):
                    length += math.hypot(pts[k + 1][0] - pts[k][0],
                                         pts[k + 1][1] - pts[k][1])
            fixed_copper[net] = {
                "segments": len(segs), "vias": len(vias),
                "length_mm": round(length, 3),
                "locked_segments": sum(1 for tr in segs if tr.get("locked")),
                "locked_vias": sum(1 for v in vias if v.get("locked"))}
        # Coupling REPLACES the second net's copper and a meander REBUILDS a
        # straight run: both move copper, so a fixed net inside either is refused
        # rather than quietly dropped (a dropped pair would still be reported as
        # a pair, and one of its two nets was never allowed to move).
        moved = []
        for pr in (bundle.get("diff_pairs") or []):
            for side in ("p", "n"):
                if str(pr.get(side) or "") in fixed:
                    moved.append(str(pr.get(side)))
        for grp in (length_groups or []):
            if isinstance(grp, dict):
                for n in (grp.get("nets") or []):
                    if str(n) in fixed:
                        moved.append(str(n))
        if moved:
            raise RouteError(
                "a fixed net cannot be coupled or length-matched: %s. Both passes "
                "MOVE copper (coupling rebuilds the second net as an offset copy of "
                "the first, a meander rebuilds a straight run), which is exactly "
                "what 'fixed' forbids. Drop it from the pair/group, or stop calling "
                "it fixed." % ", ".join(sorted(set(moved))), "ARG_CONFLICT",
                hint="fixed_nets is a promise about copper that is already on the "
                     "board; a net you still want this router to shape must not be "
                     "in it")

    results = {}
    work = []
    for net, plist in by_net.items():
        pts = [(p["at"][0], p["at"][1]) for p in plist]
        edges = mst_edges(pts)
        span = sum(math.hypot(pts[a][0] - pts[b][0], pts[a][1] - pts[b][1])
                   for a, b in edges)
        results[net] = {"net": net, "status": "pending", "pads": len(plist),
                        "mst_mm": round(span, 3), "length_mm": 0.0, "vias": 0,
                        "segments": 0, "blind_vias": 0, "edges": len(edges),
                        "reason": None,
                        "on_plane": plane_nets.get(net)}
        if net in fixed:
            have = fixed_copper.get(net) or {"segments": 0, "vias": 0,
                                             "length_mm": 0.0,
                                             "locked_segments": 0,
                                             "locked_vias": 0}
            results[net].update({"status": "fixed", "fixed": True,
                                 "length_mm": have["length_mm"],
                                 "segments": have["segments"],
                                 "vias": have["vias"],
                                 "locked_segments": have["locked_segments"],
                                 "locked_vias": have["locked_vias"]})
            if not have["segments"] and not have["vias"]:
                results[net]["status"] = "fixed_missing_copper"
                results[net]["reason"] = {
                    "code": "fixed_missing_copper",
                    "detail": "declared fixed, but the board carries no track and "
                              "no via of this net; leaving it unrouted and calling "
                              "it fixed would report an open net as settled"}
            continue
        work.append((span, net, plist))
    if lost_terminals:
        # A net whose every pad sits only on layers the caller forbade has no
        # legal place to start. "Not routed" is the answer, but a net that simply
        # disappears from the report reads as "not asked for", so it is entered
        # as failed with the pads and their layers named.
        by_lost = {}
        for row in lost_terminals:
            by_lost.setdefault(row["net"], []).append(row)
        for net, rows in sorted(by_lost.items()):
            if net in by_net:
                continue
            results[net] = {"net": net, "status": "failed", "pads": len(rows),
                            "mst_mm": 0.0, "length_mm": 0.0, "vias": 0,
                            "segments": 0, "blind_vias": 0, "edges": 0,
                            "on_plane": None,
                            "reason": {"code": "no_terminal_on_new_copper_layers",
                                       "detail": rows[:6],
                                       "new_copper_layers": list(new_layers)}}

    work.sort(reverse=True)          # longest first - see the module docstring
    bands = []
    band_of = {}
    if bundle_bands:
        known = set(results)
        for k, spec in enumerate(bundle_bands):
            if not isinstance(spec, dict):
                raise RouteError("bundle_bands[%d] is not an object" % (k,),
                                 "ARG_INVALID",
                                 hint='bundle_bands entries are '
                                      '{name, nets: [...], radius_mm, discount}')
            nets = [str(n) for n in (spec.get("nets") or [])]
            name = str(spec.get("name") or ("band%d" % k))
            if len(nets) < 2:
                raise RouteError("bundle_bands[%d] (%s) names fewer than 2 nets"
                                 % (k, name), "ARG_INVALID",
                                 hint="a corridor is a group claim on shared "
                                      "space; one net has nothing to share")
            unknown = [n for n in nets if n not in known]
            if unknown:
                # Dropping a name the board does not have would leave the caller
                # believing a corridor was built for a net that was never
                # considered, so it is refused with the names that failed.
                raise RouteError("bundle_bands[%d] (%s) names nets the board does "
                                 "not have: %s" % (k, name, ", ".join(unknown[:6])),
                                 "ARG_INVALID",
                                 hint="net names must match the pad nets verbatim "
                                      "(leading '/' included)")
            radius = float(spec.get("radius_mm", 0.8))
            discount = float(spec.get("discount", 0.6))
            outside = float(spec.get("outside", 1.0))
            if radius <= 0:
                raise RouteError("bundle_bands[%d] radius_mm must be > 0" % (k,),
                                 "ARG_INVALID")
            if not (0.0 < discount < 0.95):
                raise RouteError("bundle_bands[%d] discount must be in (0, 0.95): "
                                 "1.0 would make the corridor free and the search "
                                 "would ignore every honest detour" % (k,),
                                 "ARG_INVALID")
            if not (1.0 <= outside <= 4.0):
                raise RouteError("bundle_bands[%d] outside must be in [1, 4]: "
                                 "beyond that the corridor stops being a "
                                 "preference and starts walling off legal space"
                                 % (k,), "ARG_INVALID")
            reserve = None
            want = [str(n) for n in (spec.get("reserve_nets") or [])]
            if want:
                rmm = spec.get("reserve_mm")
                rprice = float(spec.get("reserve_price", 4.0))
                if rmm is None:
                    raise RouteError(
                        "bundle_bands[%d] (%s) names reserve_nets but no "
                        "reserve_mm: a ring with no width is not a "
                        "reservation" % (k, name), "ARG_INVALID",
                        hint="reserve_mm is the lateral room each reserved net "
                             "keeps on both sides; a readable meander needs "
                             "about 1.5x the track width, i.e. 0.15 mm on 0.1 mm "
                             "copper")
                rmm = float(rmm)
                if not (0.0 < rmm <= 4.0):
                    raise RouteError(
                        "bundle_bands[%d] (%s) reserve_mm must be in (0, 4]: "
                        "beyond 4 mm the ring stops being air for a meander "
                        "and starts being a second corridor" % (k, name),
                        "ARG_INVALID")
                if not (1.0 < rprice <= 20.0):
                    raise RouteError(
                        "bundle_bands[%d] (%s) reserve_price must be in (1, 20]: "
                        "1.0 is no reservation at all, and past 20 the price "
                        "stops steering the search and starts deciding it"
                        % (k, name), "ARG_INVALID")
                outside_band = [n for n in want if n not in nets]
                if outside_band:
                    raise RouteError(
                        "bundle_bands[%d] (%s) reserves room around nets that "
                        "are not in the band: %s. A reservation is the group "
                        "claiming its own shared space, not a global keepout, "
                        "so the reserved net must be a member"
                        % (k, name, ", ".join(outside_band[:6])),
                        "ARG_INVALID",
                        hint="put the net in this band's nets, or drop it from "
                             "reserve_nets")
                reserve = {"nets": want, "mm": rmm, "price": rprice,
                           "painted": 0}
            bands.append({"name": name, "nets": nets, "radius_mm": radius,
                          "discount": discount, "outside": outside,
                          "routed": [], "reserve": reserve})
            for n in nets:
                band_of[n] = len(bands) - 1
        if bands:
            reserving = set()
            for b in bands:
                if b.get("reserve"):
                    reserving |= set(b["reserve"]["nets"])

            def _band_key(w):
                bi = band_of.get(w[1])
                if bi is None:
                    return (len(bands), 1, -w[0])
                # A reserved net goes first inside its band: the ring has to
                # exist before anyone else is placed, or the reservation is a
                # no-op that reports success - the exact failure mode this
                # router refuses elsewhere. Inside each group, shortest first.
                return (bi, 0 if w[1] in reserving else 1, w[0])
            work.sort(key=_band_key)

    band_stamp = {}
    band_use = {}

    def _stamp(maps, nets, radius_mm, discount):
        """Paint a band's existing copper into the per-layer discount maps."""
        g = em.grid
        q = max(1, int(round(float(radius_mm) / g.pitch)))
        peak = int(round(float(discount) * 255.0))
        if peak <= 0:
            return 0
        stamps = 0
        for op in em.ops:
            if op.get("net") not in nets:
                continue
            if op["op"] == "add_track":
                spans = [(op["layer"], (op["x1"], op["y1"]),
                          (op["x2"], op["y2"]))]
            elif op["op"] == "add_via":
                spans = [(l, (op["x"], op["y"]), (op["x"], op["y"]))
                         for l in (op.get("layers") or g.layers)]
            else:
                continue
            for l, a, b in spans:
                buf = maps.get(l)
                if buf is None:
                    continue
                n = int(math.hypot(b[0] - a[0], b[1] - a[1]) / g.pitch) + 1
                for kk in range(n + 1):
                    t = kk / float(n)
                    ci, cj = g.node(a[0] + (b[0] - a[0]) * t,
                                    a[1] + (b[1] - a[1]) * t)
                    for dj in range(-q, q + 1):
                        jj = cj + dj
                        if jj < 0 or jj >= g.ny:
                            continue
                        row = jj * g.nx
                        for di in range(-q, q + 1):
                            ii = ci + di
                            if ii < 0 or ii >= g.nx:
                                continue
                            d = math.hypot(di, dj)
                            if d > q + 0.5:
                                continue
                            v = int(round(peak * (1.0 - d / float(q + 1))))
                            if v > 0:
                                k2 = row + ii
                                if buf[k2] < v:
                                    buf[k2] = v
                                stamps += 1
        return stamps

    band_reserve_stamp = {}

    def _paint_ring(maps, nets, radius_mm):
        """Mark every cell within `radius_mm` of a reserved member's copper.

        Same walk as `_stamp`, minus the graduated value: a reservation is a
        price, not a preference curve, so a cell is either inside the ring or
        it is not.
        """
        g = em.grid
        q = max(1, int(round(float(radius_mm) / g.pitch)))
        cells = 0
        for op in em.ops:
            if op.get("net") not in nets:
                continue
            if op["op"] == "add_track":
                spans = [(op["layer"], (op["x1"], op["y1"]),
                          (op["x2"], op["y2"]))]
            elif op["op"] == "add_via":
                spans = [(l, (op["x"], op["y"]), (op["x"], op["y"]))
                         for l in (op.get("layers") or g.layers)]
            else:
                continue
            for l, a, b in spans:
                buf = maps.get(l)
                if buf is None:
                    continue
                n = int(math.hypot(b[0] - a[0], b[1] - a[1]) / g.pitch) + 1
                for kk in range(n + 1):
                    t = kk / float(n)
                    ci, cj = g.node(a[0] + (b[0] - a[0]) * t,
                                    a[1] + (b[1] - a[1]) * t)
                    for dj in range(-q, q + 1):
                        jj = cj + dj
                        if jj < 0 or jj >= g.ny:
                            continue
                        row = jj * g.nx
                        for di in range(-q, q + 1):
                            ii = ci + di
                            if ii < 0 or ii >= g.nx:
                                continue
                            if math.hypot(di, dj) > q + 0.5:
                                continue
                            idx = row + ii
                            if not buf[idx]:
                                buf[idx] = 255
                                cells += 1
        return cells

    def _band_guide(net):
        bi = band_of.get(net)
        if bi is None or not bands[bi]["routed"]:
            return (None, 1.0)
        spec = bands[bi]
        st = band_stamp.get(bi)
        if st is None:
            st = band_stamp[bi] = {"maps": None, "nets": set()}
        if st["maps"] is None:
            st["maps"] = dict((l, bytearray(grid.nx * grid.ny))
                              for l in grid_layers)
        fresh = set(spec["routed"]) - st["nets"]
        if fresh:
            _stamp(st["maps"], fresh, spec["radius_mm"], spec["discount"])
            st["nets"] |= fresh
        use = band_use.setdefault(bi, {"calls": 0})
        use["calls"] += 1
        return (st["maps"], spec["outside"])

    def _band_reserve(net):
        """The rings this net must pay to cross. `(None, 1.0)` when it is one of
        the reserved members - the room is being kept FOR it, not FROM it."""
        bi = band_of.get(net)
        if bi is None:
            return (None, 1.0)
        spec = bands[bi].get("reserve")
        if not spec or net in spec["nets"]:
            return (None, 1.0)
        st = band_reserve_stamp.get(bi)
        if st is None:
            st = band_reserve_stamp[bi] = {"maps": None, "nets": set()}
        if st["maps"] is None:
            st["maps"] = dict((l, bytearray(grid.nx * grid.ny))
                               for l in grid_layers)
        fresh = (set(spec["nets"]) & set(bands[bi]["routed"]) - st["nets"])
        if fresh:
            spec["painted"] += _paint_ring(st["maps"], fresh, spec["mm"])
            st["nets"] |= fresh
        return (st["maps"], spec["price"])

    if net_priority:
        # Order is a first-come-first-served decision on shared space, so the
        # net that routes last inherits every detour the earlier ones made.
        # A bus whose members need the same length cannot live with that: four
        # nets paying 30 mm of the group's 32 mm of detour is what turns a
        # length-matched byte lane into an 11 mm meander nobody can build.
        # `net_priority` is the caller saying "these go first" - a re-route, not
        # a tweak, and the only honest way to shorten a net this router made too
        # long.
        rank = dict((n, k) for k, n in enumerate(net_priority))
        work.sort(key=lambda w: (rank.get(w[1], len(rank)), -w[0]))

    def _route_plane_net(net, plist):
        """A net with a plane does not get routed pad-to-pad: each pad drops one
        via into the plane. Anything else would draw copper across the board to
        duplicate a connection the pour already makes."""
        plane_layer = plane_nets[net][0]
        failed = []
        for t in plist:
            node = _free_terminal(grid, t, net)
            if node is None:
                failed.append({"pad": "%s.%s" % (t["reference"], t["pad"]),
                               "why": {"reason": "no_terminal"}})
                continue
            i, j, layer = node
            x, y = grid.xy(i, j)
            if incremental and _touches_own(board, net, x, y, t["half_mm"]):
                continue
            span = [l for l in grid_layers
                    if l in (layer, plane_layer)
                    or _between(grid_layers, layer, plane_layer, l)]
            if not em.via(net, x, y, span):
                failed.append({"pad": "%s.%s" % (t["reference"], t["pad"]),
                               "why": em.bad[-1]["why"]})
                continue
            results[net]["vias"] += 1
            if layer != plane_layer:
                # The via lands on the plane; a stub from the pad to the via is
                # only needed when the terminal was not the pad centre.
                px, py = t["at"]
                if math.hypot(px - x, py - y) > grid.pitch / 2.0:
                    if em.track(net, layer, (px, py), (x, y),
                                R.for_net(bundle, net)["track_mm"]):
                        results[net]["segments"] += 1
        if failed:
            results[net]["status"] = "failed"
            results[net]["reason"] = {"code": "plane_via_failed",
                                      "detail": failed[:6],
                                      "failed_pads": len(failed)}
        else:
            results[net]["status"] = "routed"
            results[net]["length_mm"] = round(em.length.get(net, 0.0), 3)
        return results[net]["status"] == "routed"

    def _route_signal_net(net, plist):
        width = R.for_net(bundle, net)["track_mm"]
        tree_nodes = set()
        terminals = []
        for t in plist:
            node = _free_terminal(grid, t, net)
            if node is None:
                results[net]["status"] = "failed"
                results[net]["reason"] = {
                    "code": "no_terminal",
                    "detail": [{"pad": "%s.%s" % (t["reference"], t["pad"]),
                                "at": t["at"],
                                "why": grid.blocked_reason(t["node"][0],
                                                           t["node"][1],
                                                           t["layers"][0], net)}]}
                return False
            terminals.append({"term": t, "node": node})
        if incremental:
            # Pinned terminals carry their own rule: the search must reach them
            # on the named layers, so the net's existing copper is only seeded
            # on those layers.  Seeding the outer stub as well let the route
            # walk down the pad's own fan-out and stop there, which is exactly
            # how an escape via ends up hanging on one layer.
            pin_layers = set()
            for item in terminals:
                if item["term"].get("pinned"):
                    pin_layers |= set(item["term"]["layers"])
            seed_layers = ([l for l in grid_layers if l in pin_layers]
                           if pin_layers else grid_layers)
            seed = _existing_tree(board, grid, net, seed_layers)
            tree_nodes |= seed
            kept = []
            for item in terminals:
                t = item["term"]
                if not t.get("pinned") and \
                        _touches_own(board, net, t["at"][0], t["at"][1],
                                     t["half_mm"]):
                    results.setdefault("_skipped", []).append(net)
                    continue
                kept.append(item)
            if len(kept) != len(terminals):
                results[net]["notes"] = "pads already connected were skipped"
            terminals = kept
            if len(terminals) <= 1 and tree_nodes:
                results[net]["status"] = "routed"
                results[net]["length_mm"] = round(em.length.get(net, 0.0), 3)
                results[net]["reason"] = {"code": "already_connected"}
                return True
        # A pad whose own centre cell is blocked is still routed, from a nearby
        # free cell - but the copper from the pad to that cell is a segment like
        # any other and has to be drawn and checked. Without this the net came
        # back `routed` while its first track began a millimetre away from the
        # pad, touching nothing: a wrong answer, not a slow one.
        escapes = []
        for item in terminals:
            t = item["term"]
            i, j = t["node"]
            ni, nj, layer = item["node"]
            if (ni, nj) == (i, j):
                continue
            ex, ey = grid.xy(ni, nj)
            if not em.track(net, layer, (t["at"][0], t["at"][1]), (ex, ey), width):
                results[net]["status"] = "failed"
                results[net]["reason"] = {
                    "code": "terminal_unreachable",
                    "detail": [{"pad": "%s.%s" % (t["reference"], t["pad"]),
                                "at": t["at"],
                                "escape_to": [round(ex, 4), round(ey, 4)],
                                "why": em.bad[-1] if em.bad else None}],
                    # what is actually in the way, and what it would take to move
                    # it - a "no" with a reason beats a bare "no"
                    "blockers": BLOCK.blockers(grid, net, t["at"], layer,
                                               width=width)}
            results[net]["segments"] += 1
            escapes.append("%s.%s" % (t["reference"], t["pad"]))
        if escapes:
            note = ("%d pad(s) had their centre cell blocked and were joined to "
                    "the nearest free cell: %s" % (len(escapes),
                                                   ", ".join(escapes)))
            results[net]["notes"] = ((results[net]["notes"] + "; ") + note
                                     if results[net].get("notes") else note)

        if not terminals:
            results[net]["status"] = "failed"
            results[net]["reason"] = {"code": "no_terminals"}
            return False
        pts = [(it["term"]["at"][0], it["term"]["at"][1]) for it in terminals]
        # `topology.tree` in "mst" mode reproduces `mst_edges` exactly, but its
        # edge list indexes the *pad* list only, so the old contract holds.
        tplan = TOPO.tree(pts, mode=topology)
        branch = []
        for (bx, by) in tplan["steiner"]:
            bi, bj = grid.node(bx, by)
            if not (0 <= bi < grid.nx and 0 <= bj < grid.ny):
                branch = None
                break
            branch.append({"node": (bi, bj, None),
                           "xy": grid.xy(bi, bj)})
        if branch is None:                       # a branch point fell off the grid
            branch = []
            tplan = TOPO.tree(pts, mode="mst")
            results[net]["topology_note"] = ("a steiner branch point landed outside "
                                            "the grid, so mst was used")
        edges = [tuple(e) for e in tplan["edges"]]
        results[net]["topology"] = tplan["mode"]
        results[net]["tree_mm"] = tplan["length_mm"]
        if tplan["mode"] == "steiner":
            results[net]["steiner_points"] = [list(p) for p in tplan["steiner"]]
            results[net]["tree_mm_mst"] = tplan["mst_length_mm"]
            results[net]["tree_saved_mm"] = tplan["saved_mm"]

        def goals_of(k):
            if k >= len(terminals):
                b = branch[k - len(terminals)]
                i, j = b["node"][0], b["node"][1]
                # a branch point has no pad, so it is free to sit on any layer
                return ({(i, j)}, {(i, j): set(grid_layers)})
            it = terminals[k]
            i, j, layer = it["node"]
            return ({(i, j)}, {(i, j): set(it["term"]["layers"]) | {layer}})

        if not tree_nodes:
            i, j, layer = terminals[0]["node"]
            tree_nodes = {(i, j, layer)}
        ok = True
        for a, b in edges:
            goals, glayers = goals_of(b)
            guide_now = _band_guide(net)
            rsv_now = _band_reserve(net)
            ast_diag = {}
            path = astar(grid, net, tree_nodes, goals, glayers,
                         allow_vias=allow_vias,
                         via_check=(_via_gate(grid, net, exact=(via_gate == "exact"))
                               if allow_vias else None),
                         diag=ast_diag, guide=guide_now[0],
                         guide_outside=guide_now[1], walk_layers=new_layers,
                         reserve=rsv_now[0], reserve_price=rsv_now[1])
            if path is None:
                src = goals
                back = {(s[0], s[1]) for s in tree_nodes}
                bl = {}
                for s in tree_nodes:
                    bl.setdefault((s[0], s[1]), set()).add(s[2])
                path = astar(grid, net, {(i, j, l) for (i, j) in src
                                         for l in glayers[(i, j)]},
                             back, bl, allow_vias=allow_vias,
                             via_check=(_via_gate(grid, net, exact=(via_gate == "exact"))
                               if allow_vias else None),
                             diag=ast_diag, guide=guide_now[0],
                             guide_outside=guide_now[1], walk_layers=new_layers,
                             reserve=rsv_now[0], reserve_price=rsv_now[1])
            if path is None:
                t = (terminals[b]["term"] if b < len(terminals) else
                     {"reference": "(branch)", "pad": "-",
                      "at": list(branch[b - len(terminals)]["xy"])})
                bnode = (terminals[b]["node"] if b < len(terminals) else
                         branch[b - len(terminals)]["node"][:2] + (grid_layers[0],))
                results[net]["status"] = "failed"
                # A search that ran out of budget has not shown there is no
                # path, only that this router stopped looking. Saying "no_path"
                # for that would be an answer the evidence does not support.
                results[net]["reason"] = {
                    "code": "search_budget" if ast_diag.get("budget_hit")
                            else "no_path",
                    "detail": [{"edge": "%s.%s -> %s.%s"
                                % ((terminals[a]["term"]["reference"]
                                    if a < len(terminals) else "(branch)"),
                                   (terminals[a]["term"]["pad"]
                                    if a < len(terminals) else "-"),
                                   t["reference"], t["pad"]),
                                "target": t["at"],
                                "why": grid.blocked_reason(bnode[0], bnode[1],
                                                           bnode[2], net)}],
                    "blockers": BLOCK.blockers(grid, net, t["at"],
                                               bnode[2] if bnode[2] else grid_layers[0],
                                               width=width)}
                ok = False
                break
            simple = simplify(path)
            for p1, p2 in zip(simple, simple[1:]):
                x1, y1 = grid.xy(p1[0], p1[1])
                x2, y2 = grid.xy(p2[0], p2[1])
                if p1[2] != p2[2]:
                    # A through via is the cheap one, so it is tried first; a
                    # blind via is only an escape route for a spot where the
                    # stack is blocked for a through hole.
                    placed = em.via(net, x1, y1)
                    span = None
                    if not placed and blind_vias:
                        span = [l for l in grid_layers
                                if l in (p1[2], p2[2])
                                or _between(grid_layers, p1[2], p2[2], l)]
                        placed = em.via(net, x1, y1, span)
                    if not placed:
                        ok = False
                        break
                    results[net]["vias"] += 1
                    if span and span != list(grid_layers):
                        results[net]["blind_vias"] = results[net].get("blind_vias", 0) + 1
                    continue
                if em.track(net, p1[2], (x1, y1), (x2, y2), width):
                    results[net]["segments"] += 1
                else:
                    ok = False
                    break
            if not ok:
                results[net]["reason"] = {"code": "clearance",
                                          "detail": [em.bad[-1]] if em.bad else []}
                break
            for s in path:
                tree_nodes.add(s)
        if ok:
            results[net]["status"] = "routed"
            results[net]["length_mm"] = round(em.length.get(net, 0.0), 3)
        return ok

    stats = {"rips": 0, "sweeps": 1, "elapsed_s": 0.0, "rule_bundle": bundle,
             "topology": topology,
             "rip_passes": 0,
             "grid_notes": list(grid.notes), "grid_cells": grid.nx * grid.ny,
             "pitch_mm": float(pitch_mm),
             "terminal_override": override_report}

    def _attempt(net, plist):
        """One net, one transaction: commit its copper only if it finished."""
        t_net = time.time()
        mark = em.mark(net)
        ok = (_route_plane_net(net, plist) if net in plane_nets
              else _route_signal_net(net, plist))
        if ok:
            em.commit(mark)
            bi = band_of.get(net)
            if bi is not None:
                bands[bi]["routed"].append(net)
        else:
            em.rollback(mark)
        results[net]["route_s"] = round(time.time() - t_net, 3)
        if progress is not None:
            progress({"net": net, "ok": ok,
                      "status": results[net].get("status"),
                      "s": results[net]["route_s"]})
        return ok

    pending = []
    for span, net, plist in work:
        results[net]["mst_mm"] = round(span, 3)
        if not _attempt(net, plist):
            pending.append((span, net, plist))

    if pending and second_sweep:
        # Same free space, different tree root: the first pass may have started
        # from a pad that boxes the net in. Nothing is ripped up here.
        stats["sweeps"] = 2
        still = []
        for span, net, plist in pending:
            flist = list(reversed(plist))
            if results[net]["status"] == "routed":
                continue
            if not _attempt(net, flist):
                still.append((span, net, plist))
        pending = still

    def _blockers_of(net, plist):
        """Routed nets whose copper physically sits where this net has to go.

        Ripping the SHORTEST nets is easier to write and near useless: on the F401
        core board every net that lost the first pass routed fine on the empty
        board, so what stops them is somebody else's copper in the way, not
        geometry. Ripping a net 20 mm away buys nothing. Victims are chosen by
        overlap with the box this net spans, against copper that was really
        emitted.
        """
        xs = [p["at"][0] for p in plist]
        ys = [p["at"][1] for p in plist]
        pad = 1.5
        box = (min(xs) - pad, min(ys) - pad, max(xs) + pad, max(ys) + pad)
        hits = {}
        for op in em.ops:
            other = op["net"]
            if other == net or other in plane_nets or other in fixed:
                continue
            if op["op"] == "add_via":
                ox0 = ox1 = op["x"]
                oy0 = oy1 = op["y"]
            elif op["op"] == "add_track":
                ox0, ox1 = sorted((op["x1"], op["x2"]))
                oy0, oy1 = sorted((op["y1"], op["y2"]))
            else:
                continue
            if ox1 < box[0] or ox0 > box[2] or oy1 < box[1] or oy0 > box[3]:
                continue
            hits[other] = hits.get(other, 0) + 1
        return [n for n, _c in sorted(hits.items(), key=lambda kv: -kv[1])[:3]]

    if pending and rip_up_rounds:
        # Bounded rip-up, one pass per `rip_up_rounds`: each net that lost gets
        # the copper standing in its way moved out, then everyone who moved is
        # re-routed. The rest of the board is untouched - its copper is still in
        # the map, and re-routing it would only re-derive the same paths.
        for _round in range(int(rip_up_rounds)):
            if not pending:
                break
            stats["rip_passes"] = _round + 1
            still = []
            for span, net, plist in pending:
                if results[net].get("status") == "routed":
                    continue
                rips = _blockers_of(net, plist)
                if not rips:
                    still.append((span, net, plist))
                    continue
                # Rip-out rebuilds the search map from the ops that remain; a map
                # that still remembers ripped copper is a map of another board.
                em.forget(rips)
                grid = em.grid
                # The guide maps were painted from copper that just moved out;
                # the next member must not follow a corridor nobody occupies.
                band_stamp.clear()
                band_reserve_stamp.clear()
                for b in bands:
                    if b.get("reserve"):
                        b["reserve"]["painted"] = 0
                for n in rips:
                    bi = band_of.get(n)
                    if bi is not None and n in bands[bi]["routed"]:
                        bands[bi]["routed"].remove(n)
                em.bad[:] = [b for b in em.bad if b.get("net") not in rips]
                for n in rips:
                    results[n]["status"] = "pending"
                    results[n]["rips"] = results[n].get("rips", 0) + 1
                    results[n]["length_mm"] = 0.0
                    results[n]["vias"] = 0
                    results[n]["segments"] = 0
                stats["rips"] += len(rips)
                failed_now = []
                for n in [net] + rips:
                    pl = by_net.get(n) or []
                    if not pl:
                        continue
                    results[n]["status"] = "pending"
                    if not _attempt(n, pl):
                        failed_now.append(n)
                # Whatever lost again stays pending for the next pass; a net that
                # only lost because it was ripped gets another chance there.
                for n in failed_now:
                    still.append((results[n].get("mst_mm") or 0.0, n,
                                  by_net.get(n) or []))
            pending = still

    def _routed_now():
        return sum(1 for r in results.values()
                   if isinstance(r, dict) and r.get("status") == "routed")

    def _wish(net, plist):
        """Where this net would go if everyone else's copper were merely expensive.

        This is the question a negotiated router has to answer, and it cannot be
        answered from occupancy: this raster makes a cell exclusive, so two nets
        never *share* one and "overuse" never appears. Contention here is a net
        that cannot move at all because somebody else's copper is in the only
        corridor it has. So the search is re-run with `soft` - other nets'
        copper (and keepouts) may be entered at a large price - and the resulting
        path is the corridor the net WANTS. The nets whose copper those cells
        belong to are, by name, the ones in the way.

        The path is never emitted: it crosses other nets by construction.
        """
        # Pad centres, not `_free_terminal` cells: a pad whose own cell is
        # covered by somebody else's clearance has no free terminal - that is
        # exactly the situation this search exists to describe, so asking for a
        # free node first would return "nothing to want" for the one net that
        # most needs an answer.
        nodes = []
        for t in plist:
            i, j = grid.node(t["at"][0], t["at"][1])
            if 0 <= i < grid.nx and 0 <= j < grid.ny:
                nodes.append((t, (i, j, t["layers"][0])))
        if len(nodes) < 2:
            return set(), {}
        pts = [(it[0]["at"][0], it[0]["at"][1]) for it in nodes]
        tree = {nodes[0][1]}
        want, who = set(), {}
        for _a, b in mst_edges(pts):
            i, j, layer = nodes[b][1]
            path = astar(grid, net, tree, {(i, j)},
                         {(i, j): set(nodes[b][0]["layers"]) | {layer}},
                         budget=WISH_BUDGET,
                         allow_vias=allow_vias,
                         via_check=(_via_gate(grid, net, exact=(via_gate == "exact"))
                               if allow_vias else None),
                         soft=SOFT_BLOCK, walk_layers=new_layers)
            if path is None:
                continue
            for p in path:
                cell = p[1] * grid.nx + p[0]
                want.add(cell)
                val = grid._map[p[2]][cell]
                if val > 0:
                    for other in grid._sets[val - 1]:
                        if other != net:
                            who[other] = who.get(other, 0) + 1
            tree |= set(path)
        return want, who
    def _price(hist, weight):
        """Install `weight * history` as this round's per-cell price."""
        pen = grid._pen
        if pen is None or len(pen) != grid.nx * grid.ny:
            pen = grid._pen = [0.0] * (grid.nx * grid.ny)
        else:
            for k in range(len(pen)):
                pen[k] = 0.0
        for cell, v in hist.items():
            if 0 <= cell < len(pen):
                pen[cell] = weight * v

    def _recompute_lengths():
        em.length = {}
        for op in em.ops:
            if op["op"] == "add_track":
                em.length[op["net"]] = em.length.get(op["net"], 0.0) + math.hypot(
                    op["x2"] - op["x1"], op["y2"] - op["y1"])

    def _reconcile():
        """Make `results` describe the copper that is standing, and nothing else.

        `_attempt` commits exactly when it succeeded, so "has copper" and "was
        routed" are the same claim. After a restore the two have to be put back in
        step by hand, or the answer would describe a round that no longer exists.
        """
        have = set(op["net"] for op in em.ops if op.get("net"))
        for net, res in results.items():
            if not isinstance(res, dict):
                continue
            if net in have:
                res["status"] = "routed"
                res["length_mm"] = round(em.length.get(net, 0.0), 3)
                res.pop("reason", None)
            else:
                res["status"] = "failed"
                res["length_mm"] = 0.0
                res.setdefault("reason", {"code": "not_attempted"})

    def _negotiate(rounds, weight, max_rips):
        """Rip up and re-route with a price on the corridors the losers wanted.

        Plain greedy routing has one shot: whoever goes first keeps the corridor
        and every later net has to live with it. The second sweep only changes a
        tree root, and one rip-up round only moves the copper that physically
        overlaps a loser's pads - neither can move a net that is merely *in the
        way*. This loop can, because it asks each loser where it would go if the
        board were empty (`_wish`), prices those cells (history cost, accumulated
        across rounds), and then rips up the nets named by the cells it had to
        cross, so they can be re-routed somewhere else with those prices in the
        search. A net that has an alternative detour takes it; a net that does
        not keeps its copper and the price rises again next round.

        The best round is kept, not the last: prices make late rounds willing to
        take long detours, and a round that routed fewer nets must not be the
        answer just because it ran later.
        """
        nonlocal grid, pending
        hist = {}
        log = []
        best = {"routed": _routed_now(), "round": 0,
                "ops": [dict(op) for op in em.ops], "length": dict(em.length)}
        for rnd in range(1, rounds + 1):
            if not pending:
                break
            demand, who = {}, {}
            for _span, net, plist in pending:
                if net in plane_nets:
                    continue
                cells, named = _wish(net, plist)
                for cell in cells:
                    demand[cell] = demand.get(cell, 0) + 1
                for other, n in named.items():
                    if (other in by_net and other not in plane_nets
                            and other not in fixed):
                        who[other] = who.get(other, 0) + n
            for cell, n in demand.items():
                hist[cell] = hist.get(cell, 0.0) + float(n)
            losers = [net for _s, net, _pl in pending]
            victims = [n for n in sorted(who, key=lambda n: -who[n])
                       if n not in losers][:max_rips]
            todo = losers + victims
            log.append({"round": rnd, "demand_cells": len(demand),
                        "in_the_way": len(who), "ripped": len(todo),
                        "losers": len(losers), "before": _routed_now()})
            em.forget(todo)
            grid = em.grid
            _price(hist, weight)         # forget() rebuilt the map, not the prices
            ripped = set(todo)
            em.bad[:] = [b for b in em.bad if b.get("net") not in ripped]
            for n in todo:
                results[n]["status"] = "pending"
                results[n].pop("reason", None)
                results[n]["length_mm"] = 0.0
                results[n]["vias"] = 0
                results[n]["segments"] = 0
                results[n]["rips"] = results[n].get("rips", 0) + 1
            for n in sorted(todo, key=lambda n: -(results[n].get("mst_mm") or 0.0)):
                pl = by_net.get(n) or []
                if pl:
                    _attempt(n, pl)
            pending = [(span, net, plist) for span, net, plist in work
                       if results[net].get("status") != "routed"]
            got = _routed_now()
            log[-1]["after"] = got
            if got > best["routed"]:
                best = {"routed": got, "round": rnd,
                        "ops": [dict(op) for op in em.ops],
                        "length": dict(em.length)}
            if not pending:
                break
        reverted = False
        if best["round"] and _routed_now() < best["routed"]:
            em.ops = [dict(op) for op in best["ops"]]
            em.rebuild()
            grid = em.grid
            em.length = dict(best["length"])
            _reconcile()
            reverted = True
        return {"rounds_asked": rounds, "rounds_run": len(log),
                "weight": weight, "max_rips": max_rips,
                "soft_block": SOFT_BLOCK,
                "best_round": best["round"], "best_routed": best["routed"],
                "reverted_to_best": reverted,
                "history_cells": len(hist), "log": log}

    congestion = None
    if pending and congestion_rounds:
        congestion = _negotiate(int(congestion_rounds), float(congestion_weight),
                                int(congestion_max_rips))
        stats["congestion"] = congestion

    # Length matching and diff-pair bookkeeping run on the FINISHED copper: a
    # meander changes a length, so it cannot happen before the path exists, and a
    # pair's achieved gap is a property of both finished paths. Every meander
    # segment goes back through the exact check, so this pass cannot introduce a
    # violation the router would have refused.
    tune_report = tune.finish(em, bundle, results, diff_pairs=bundle["diff_pairs"],
                              length_groups=length_groups, terms_by_net=by_net,
                              return_path=return_path,
                              return_path_net=return_path_net)
    stats["tuning"] = tune_report

    out_nets = []
    for net, res in results.items():
        if not isinstance(res, dict):
            continue
        if res["status"] == "pending":
            res["status"] = "failed"
            res.setdefault("reason", {"code": "not_attempted"})
        if not res.get("fixed"):
            res["segments"] = sum(1 for op in em.ops
                                  if op["op"] == "add_track" and op["net"] == net)
            res["vias"] = sum(1 for op in em.ops
                              if op["op"] == "add_via" and op["net"] == net)
        out_nets.append(res)
    out_nets.sort(key=lambda r: (r["status"] != "failed", -(r.get("mst_mm") or 0)))
    routed_n = sum(1 for r in out_nets if r["status"] == "routed")
    fixed_n = sum(1 for r in out_nets if r["status"] == "fixed")
    failed = [r for r in out_nets if r["status"] not in ("routed", "fixed")]
    if new_layers is not None:
        stats["new_copper_layers"] = list(new_layers)
        stats["terminals_off_new_copper_layers"] = len(lost_terminals)
    stats["elapsed_s"] = round(time.time() - t0, 3)
    stats["nets_total"] = len(out_nets)
    stats["nets_routed"] = routed_n
    stats["nets_failed"] = len(failed)
    stats["nets_fixed"] = fixed_n
    stats["ops"] = len(em.ops)
    stats["rejected_ops"] = len(em.bad)
    if bands:
        rep = []
        for bi, spec in enumerate(bands):
            lens = [float(results[n].get("length_mm") or 0.0)
                    for n in spec["nets"]]
            live = [x for x in lens if x > 0.0]
            rep.append({"name": spec["name"],
                        "nets": list(spec["nets"]),
                        "radius_mm": spec["radius_mm"],
                        "discount": spec["discount"],
                        "outside": spec["outside"],
                        "routed": len(live),
                        "skeleton": (spec["routed"][0] if spec["routed"] else None),
                        "guide_calls": band_use.get(bi, {}).get("calls", 0),
                        "length_mm": [round(x, 3) for x in lens],
                        "spread_mm": (round(max(live) - min(live), 3)
                                      if len(live) > 1 else None)})
        stats["bundle_bands"] = rep

    def _band_reserve_gap(spec):
        """How much air the nets that must meander actually got, measured at the
        corridor's waist.

        The distance people quote for a corridor - centre to centre at the pads -
        says nothing about meander room: the pads are packed by definition, so
        that number is fixed before any copper is drawn. What decides whether
        `route.tune_board` can put a readable tooth down is the nearest other
        member's copper to the *middle* of the reserved net, so samples within
        `1.0 mm` of the reserved net's own terminals are skipped and the rest is
        measured segment to segment.
        """
        res = spec.get("reserve")
        if not res:
            return None
        want = set(res["nets"])
        flats = {}
        for op in em.ops:
            if op["op"] != "add_track":
                continue
            # Copper on another layer is not in the way - a gap measured across
            # layers would report 0.00 mm between two nets that legally cross,
            # which is the opposite of air.
            flats.setdefault((op["net"], op["layer"]), []).append(
                (op["x1"], op["y1"], op["x2"], op["y2"]))
        pads = {}
        for n in want:
            pads[n] = [(p["at"][0], p["at"][1])
                       for p in (by_net.get(n) or [])]
        def _near_pad(n, x, y):
            for (px, py) in pads.get(n) or ():
                if math.hypot(x - px, y - py) < 1.0:
                    return True
            return False
        out = {}
        for n in sorted(want):
            best = None
            mine = [(l, seg) for (m, l), segs in flats.items() if m == n
                    for seg in segs]
            others = [((m, l), seg) for (m, l), segs in flats.items()
                      if m != n for seg in segs]
            for l_here, (x1, y1, x2, y2) in mine:
                # Sample the run instead of testing its endpoints: a straight
                # 4 mm segment has BOTH endpoints on pads, so an endpoint test
                # would skip exactly the copper the question is about.
                span = math.hypot(x2 - x1, y2 - y1)
                steps = max(1, int(span / 0.1))
                for kk in range(steps + 1):
                    t = kk / float(steps)
                    px = x1 + (x2 - x1) * t
                    py = y1 + (y2 - y1) * t
                    if _near_pad(n, px, py):
                        continue
                    for (m, l), (a1, b1, a2, b2) in others:
                        if l != l_here:
                            continue
                        d = G.dist_point_segment((px, py), (a1, b1), (a2, b2))
                        if best is None or d < best[0]:
                            best = (d, m)
            if best is None:
                continue
            out[n] = {"gap_mm": round(best[0], 4), "nearest": best[1]}
        return {"nets": sorted(want), "mm": res["mm"],
                "price": res["price"], "ring_cells": res["painted"],
                "gap_mm": out}

    if bands:
        for bi_v, spec_v in enumerate(bands):
            got = _band_reserve_gap(spec_v)
            if got is not None:
                stats["bundle_bands"][bi_v]["reserve"] = got
    return {
        "layers_routing": routing,
        "layers_plane": planes,
        "rules": {"clearance_mm": bundle["clearance_mm"],
                  "track_mm": bundle["track_mm"],
                  "via_diameter_mm": bundle["via_diameter_mm"],
                  "via_drill_mm": bundle["via_drill_mm"],
                  "edge_clearance_mm": bundle["edge_clearance_mm"],
                  "creepage_mm": bundle["creepage_mm"],
                  "creepage_pairs": bundle["creepage_pairs"],
                  "creepage_lookup": bundle.get("creepage_lookup"),
                  "diff_pairs": bundle["diff_pairs"],
                  "impedance_targets": bundle["impedance_targets"],
                  "sources": bundle["sources"],
                  "assumed": bundle["assumed"]},
        "rule_keepouts": {"used": grid.rule_keepouts,
                          "dropped": grid.dropped_rule_keepouts,
                          "cells_raster": len(grid.keepouts)},
        "diff_pairs": tune_report["diff_pairs"],
        "length_groups": tune_report["length_groups"],
        "return_path": tune_report.get("return_path"),
        "congestion": congestion,
        "pour": _pour_report(board, em.ops, copper),
        "nets": out_nets,
        "fixed": ({"nets": sorted(fixed), "copper": fixed_copper,
                   "unknown": sorted(fixed - set(by_net)),
                   "ops_emitted_for_fixed": sum(
                       1 for op in em.ops if op.get("net") in fixed)}
                  if fixed else None),
        "failed": [{"net": r["net"], "reason": r.get("reason")} for r in failed],
        "ops": em.ops,
        "rejected": em.bad[:20],
        "stats": stats,
    }


def _pour_report(board, ops, copper_layers):
    """Which pours the new copper lands in, and what that costs.

    A track that crosses a pour of another net is not a short: the pour is
    re-filled around it. What it IS is a stale snapshot - the copper in the file
    no longer describes the same board - so the honest output is "these zones must
    be refilled", with the number of segments inside each, instead of a silence
    that lets a filled board look finished. Vias are through vias here (the op
    carries no span), so they are copper on every layer in the stack.
    """
    pours = []
    for z in board.zones or []:
        if z.get("is_keepout") or not z.get("net"):
            continue
        ring = z.get("outline") or []
        if len(ring) < 3:
            continue
        box = G.bbox_of(ring)
        for layer in (z.get("layers") or []):
            pours.append((layer, z.get("net"), box, ring))
    if not pours:
        return {"needs_refill": False, "zones": [], "why": "no pour on this board"}
    hit = {}
    for op in ops:
        if op.get("op") == "add_track":
            points = [(op["x1"], op["y1"]),
                      ((op["x1"] + op["x2"]) / 2.0, (op["y1"] + op["y2"]) / 2.0),
                      (op["x2"], op["y2"])]
            layers = [op["layer"]]
        elif op.get("op") == "add_via":
            points = [(op["x"], op["y"])]
            layers = list(copper_layers)
        else:
            continue
        for layer, zone_net, box, ring in pours:
            if layer not in layers:
                continue
            for px, py in points:
                if not (box[0] - 1e-6 <= px <= box[2] + 1e-6
                        and box[1] - 1e-6 <= py <= box[3] + 1e-6):
                    continue
                if not G.point_in_polygon((px, py), ring):
                    continue
                key = (layer, zone_net)
                rec = hit.setdefault(key, {"layer": layer, "zone_net": zone_net,
                                           "segments_inside": 0,
                                           "same_net_as_the_new_copper": False})
                rec["segments_inside"] += 1
                if zone_net == op.get("net"):
                    rec["same_net_as_the_new_copper"] = True
                break
    zones = sorted(hit.values(), key=lambda r: -r["segments_inside"])
    return {"needs_refill": bool(zones),
            "zones": zones,
            "why": ("new copper sits inside %d pour(s): the filled polygons in the "
                    "file describe the board before this routing and must be "
                    "refilled (board_fill_zones) before the board reads as finished"
                    % (len(zones),)) if zones else "no new copper inside a pour"}

def _between(layers, a, b, cand):
    """Is `cand` between `a` and `b` in stackup order (for a via's span)?"""
    if a not in layers or b not in layers or cand not in layers:
        return False
    ia, ib, ic = layers.index(a), layers.index(b), layers.index(cand)
    return min(ia, ib) < ic < max(ia, ib)


def _via_gate(grid, net, exact=False):
    """A* asks this before paying for a via.

    It asks the VIA raster, not the track raster: a via is 0.6 mm where a track
    is 0.25 mm, and using one raster for both is exactly how seven nets on the
    F401 core board ended up "routed" with a via the exact check rejected.

    `exact=True` adds the emitter's own test (`grid.via_blockers`, a 64-gon at
    the class diameter, judged pair by pair against each net rule) after the
    raster says yes. The raster is the cheap rejection; this is the answer.
    Without it the search and the write disagree by exactly the amount the
    raster is optimistic - and the search is the one that loses, because the
    emitter then refuses the via the plan was built on.
    """
    def check(i, j, _net):
        for layer in grid.layers:
            if not grid.free_via(i, j, layer, net):
                return False
        if not exact:
            return True
        x, y = grid.xy(i, j)
        return grid.via_blockers(x, y, net) == []
    return check
