"""A board either passes the drawing rules or it does not, and the rules are ranked.

Why this module exists, in the reviewer's words: "the MCP does not constrain you
enough". That complaint is not about a missing measurement - `angle_audit`,
`net_geometry_report` and `house_style` already measure every number below. What
was missing is a place that turns the measurements into **one verdict with a
ranked list of what is blocking**, so a generator has something to be refused by
instead of a pile of numbers it can talk past.

The ranking is part of the answer, not decoration. When a board fails two rules
at once, the higher one is what to fix first, and a caller told "fail" without an
order has to re-derive the order itself (and will pick wrong - the cheapest rule
to satisfy is rarely the one that matters).

The order is by "how unrecoverable is this in copper":

1. `acute_corner` - an interior angle under 90 degrees is an acid trap and an
   etch defect. It cannot be tuned away later; the shape has to be redrawn.
2. `off_45_heading` - a segment that is neither H, V nor 45. Nothing in this
   house is drawn at 30 degrees, and one such segment is a straight violation
   rather than a style opinion.
3. `duplicate_copper` - two tracks with the same net, layer and both endpoints.
   Pure waste, and it silently doubles every length and detour number the other
   audits report (`/FSPI_CLK` on the SOM board carried exactly this pair).
4. `redundant_loop` - a signal net that closes a ring against itself. Two paths
   between the same pair of pins is copper drawn twice, and it inflates every
   length the other rules read. `duplicate_copper` cannot see it (the endpoints
   differ) and `detour_ratio` cannot see it until the ring is long enough to break
   a cap. Rails are reported and exempt from blocking: feeding one ball row from
   two stitched rails is how a rail shares current without being widened.
5. `square_corner` - a 90 degree turn where 135 was available. Reported always,
   blocking only in `strict`. A pocket between pads can force a 90, so this one
   is a judgement and says so.
6. `detour_ratio` - routed length over the minimum spanning tree of the net's pad
   centres. Blocking only past `hard_ratio`, because the denominator is a LOWER
   bound: a multi-drop net or a net whose parts sit on opposite edges of the board
   legitimately scores high.

Every threshold here is a judgement, and each one is echoed back with the number
that used it so the verdict can be argued with. Nothing in this module writes,
routes or places; it reads a `canonical.Board` and returns a dict.

Nothing here is a route.
"""
import math

from . import oracle as _oracle
from .calc.result import ok, err

#: The rules, highest priority first. `blocking_when` is prose for the reader; the
#: code below is the same statement in branches. Kept as data so a caller can
#: print the ordering without running anything.
RULES = (
    {"priority": 1, "id": "acute_corner", "blocking": True,
     "rule": "no interior corner under 90 degrees"},
    {"priority": 2, "id": "off_45_heading", "blocking": True,
     "rule": "every segment heading is 0/45/90 degrees"},
    {"priority": 3, "id": "duplicate_copper", "blocking": True,
     "rule": "no two tracks share net, layer and both endpoints"},
    {"priority": 4, "id": "redundant_loop", "blocking": True,
     "rule": "no signal net closes a ring against itself (a rail may be a grid)"},
    {"priority": 5, "id": "dead_end", "blocking": True,
     "rule": "every track end and every via touches copper on more than one side"},
    {"priority": 6, "id": "square_corner", "blocking": False,
     "rule": "a 90 degree turn where a 135 chamfer was available"},
    {"priority": 7, "id": "detour_ratio", "blocking": False,
     "rule": "routed length stays near the pad spanning tree"},
)

#: A net whose name looks like a rail may legitimately run a longer path than a
#: signal, because it also has to be wide and reach several pins. Judgement.
POWER_HINTS = ("3v3", "5v", "12v", "1v8", "1v35", "2v5", "0v9", "vcc", "vdd",
               "vbat", "vbus", "vref", "vcap", "vin", "pwr", "vsys", "vcore",
               "vddio", "avdd", "dvdd")

#: Above this ratio the detour stops being explainable by topology and becomes a
#: loop. Twice the spanning tree on a signal net is already twice the copper.
HARD_RATIO_SIGNAL = 2.5
HARD_RATIO_POWER = 3.2


def _is_powerish(name):
    low = (name or "").lower()
    return any(h in low for h in POWER_HINTS)


def _duplicate_copper(board, tol_mm=0.002):
    """Tracks that are the same piece of copper twice.

    Keyed on the unordered endpoint pair so the direction of the second copy does
    not matter. Arcs are keyed on chord plus net and layer, which is enough to
    catch a copy that was cloned rather than drawn.
    """
    seen = {}
    dups = []
    for t in board.tracks or []:
        net = t.get("net")
        lay = t.get("layer")
        st, en = t.get("start"), t.get("end")
        if net is None or st is None or en is None:
            continue
        a = (round(float(st[0]) / tol_mm), round(float(st[1]) / tol_mm))
        b = (round(float(en[0]) / tol_mm), round(float(en[1]) / tol_mm))
        key = (net, lay, min(a, b), max(a, b))
        if key in seen:
            dups.append({"net": net, "layer": lay,
                         "start": [round(float(st[0]), 3), round(float(st[1]), 3)],
                         "end": [round(float(en[0]), 3), round(float(en[1]), 3)],
                         "width_mm": round(float(t.get("width") or 0.0), 3)})
        else:
            seen[key] = t
    return dups


#: A ring inside one net is copper the net does not need, and nothing else in
#: this module sees it: `duplicate_copper` only catches a copy with the SAME two
#: endpoints, and `detour_ratio` only fires once the ring is long enough to push
#: the ratio past a cap. "Drawn twice between the same two pins" is the ordinary
#: way a redundant path appears - a second attempt left in place beside the first.
#:
#: Nodes are (layer, x, y) snapped to SNAP_MM, so copper that merely CROSSES is
#: not a connection. Layers are fused only where the board itself says they are:
#: at a via and at a through-hole pad. A same-net pair of tracks meeting on two
#: different layers with no via between them is a different defect (a connection
#: that is not one), and calling it a ring here would hide it.
#:
#: A vertex landing on another segment's interior SPLITS that segment, because
#: that is how the copper actually meets: on the SOM board the 4.455 mm In3 arm
#: ends at (12.355, 3.600), in the middle of the 12.110 -> 13.390 tie, and an
#: endpoint-only graph read the ring as two unrelated paths.
SNAP_MM = 0.01
MIN_EDGE_MM = 0.02


def _redundant_loop(board, snap_mm=None, min_edge_mm=None):
    """Per net, the rings it closes: copper that could be taken out.

    Returns one row per net with at least one ring, listing the segments that
    close it (longest first - the long way around is the one to drop, the short
    direct link is the one to keep). `cycles` is `edges - nodes + components` of
    that net's copper graph, which counts independent rings, not walks over them.
    """
    snap = float(SNAP_MM if snap_mm is None else snap_mm)
    floor = float(MIN_EDGE_MM if min_edge_mm is None else min_edge_mm)
    by_net = {}
    for t in board.tracks or []:
        net, lay = t.get("net"), t.get("layer")
        st, en = t.get("start"), t.get("end")
        if net is None or lay is None or not st or not en:
            continue
        by_net.setdefault(net, []).append(
            (lay, float(st[0]), float(st[1]), float(en[0]), float(en[1]),
             float(t.get("width") or 0.0)))

    vias_by_net, pads_by_net = {}, {}
    for v in board.vias or []:
        if v.get("net") is None or v.get("x") is None or v.get("y") is None:
            continue
        vias_by_net.setdefault(v["net"], []).append((float(v["x"]), float(v["y"])))
    for p in board.pads() or []:
        if p.get("net") is None or p.get("x") is None or p.get("y") is None:
            continue
        lays = [str(v) for v in (p.get("layers") or [])]
        if not (p.get("hole_mm") or any(v.startswith("*") for v in lays)):
            continue
        pads_by_net.setdefault(p["net"], []).append((float(p["x"]), float(p["y"])))

    def node(lay, x, y):
        return (lay, int(round(x / snap)), int(round(y / snap)))

    rows = []
    for net, segs in by_net.items():
        verts = {}
        for lay, x1, y1, x2, y2, _w in segs:
            s = verts.setdefault(lay, set())
            s.add(node(lay, x1, y1)[1:])
            s.add(node(lay, x2, y2)[1:])

        edges = []
        for lay, x1, y1, x2, y2, w in segs:
            dx, dy = x2 - x1, y2 - y1
            L2 = dx * dx + dy * dy
            cuts = []
            if L2 > 0.0:
                L = math.sqrt(L2)
                for vx, vy in verts.get(lay, ()):
                    px, py = vx * snap, vy * snap
                    t = ((px - x1) * dx + (py - y1) * dy) / L2
                    if t <= 0.0 or t >= 1.0:
                        continue
                    # a vertex sitting ON an endpoint is not a split: cutting
                    # there invents a near-zero edge and a phantom ring
                    if t * L < floor or (1.0 - t) * L < floor:
                        continue
                    cx, cy = x1 + t * dx, y1 + t * dy
                    if math.hypot(px - cx, py - cy) > snap:
                        continue
                    cuts.append((t, px, py))
            cuts.sort()
            pts = [(x1, y1)] + [(cx, cy) for _t, cx, cy in cuts] + [(x2, y2)]
            for i in range(len(pts) - 1):
                a, b = pts[i], pts[i + 1]
                ka, kb = node(lay, *a), node(lay, *b)
                if ka == kb:
                    continue
                edges.append((math.hypot(b[0] - a[0], b[1] - a[1]),
                              lay, ka, kb, a, b, w))
        if not edges:
            continue

        # Two separate union-finds, and they must stay separate.
        #
        # `ident` answers "are these two snap keys the same physical point of
        # copper". It is fed ONLY by the board's own evidence of a join: a via,
        # a through-hole pad, or a track end a few microns off. `conn` answers
        # "are these two points electrically on the same piece of this net",
        # and it is fed by the edges.
        #
        # Conflating them is the trap this code already fell into twice. Using
        # one map for both makes every pair of points on a connected net share a
        # root, so every edge becomes a zero-length stub and the ring disappears;
        # and merging identities without shrinking `nodes` leaves `nodes` too
        # high, so each joined via cancels a real ring and the count can even go
        # negative (the /FB_U7 loop on the SOM board read -1).
        ident = {}

        def ifind(a):
            ident.setdefault(a, a)
            r = a
            while ident[r] != r:
                r = ident[r]
            while ident[a] != r:
                ident[a], a = r, ident[a]
            return r

        def iunion(a, b):
            ra, rb = ifind(a), ifind(b)
            if ra != rb:
                ident[ra] = rb

        for _L, _lay, ka, kb, _a, _b, _w in edges:
            ifind(ka)
            ifind(kb)

        def join_at(x, y):
            # the via / pad anchor can sit a few microns off the track end that
            # reaches it (9.400 vs 9.406 on the SOM board), so the join looks
            # within 2 snap cells instead of demanding an exact hit. The epsilon
            # is load-bearing: 3.582 against 3.600 is 0.020000000000000018 in
            # floats, so a bare `<= 2*snap` refuses to join a pad the track
            # actually reaches, and the ring that hangs off it goes unseen.
            near = [n for n in ident
                    if abs(n[1] * snap - x) <= 2 * snap + 1e-9
                    and abs(n[2] * snap - y) <= 2 * snap + 1e-9]
            for n in near[1:]:
                iunion(near[0], n)

        for x, y in vias_by_net.get(net, ()):
            join_at(x, y)
        for x, y in pads_by_net.get(net, ()):
            join_at(x, y)

        # Collapse every endpoint onto its merged identity BEFORE counting, so
        # `nodes` counts physical points. An edge whose two ends collapse onto
        # the same identity is a stub shorter than the join tolerance, not
        # copper, and dropping it is what keeps a joined via from eating a ring.
        merged = []
        for L, lay, ka, kb, a, b, w in edges:
            ra, rb = ifind(ka), ifind(kb)
            if ra == rb:
                continue
            merged.append((L, lay, ra, rb, a, b, w))
        if not merged:
            continue

        nodes = set()
        for _L, _lay, ra, rb, _a, _b, _w in merged:
            nodes.add(ra)
            nodes.add(rb)

        conn = {}

        def cfind(a):
            conn.setdefault(a, a)
            r = a
            while conn[r] != r:
                r = conn[r]
            while conn[a] != r:
                conn[a], a = r, conn[a]
            return r

        for _L, _lay, ra, rb, _a, _b, _w in merged:
            cra, crb = cfind(ra), cfind(rb)
            if cra != crb:
                conn[cra] = crb
        comps = set(cfind(n) for n in nodes)
        cycles = len(merged) - len(nodes) + len(comps)
        if cycles <= 0:
            continue

        # which segments close a ring: add the shortest first, so the short
        # direct link becomes the kept tree and the long way around is what is
        # left over - removing the long one loses the least
        tree = {}

        def tfind(a):
            tree.setdefault(a, a)
            r = a
            while tree[r] != r:
                r = tree[r]
            return r

        removable = []
        for e in sorted(merged, key=lambda r: r[0]):
            ra, rb = tfind(e[2]), tfind(e[3])
            if ra == rb:
                removable.append(e)
            else:
                tree[ra] = rb
        removable.sort(key=lambda r: -r[0])
        rows.append({
            "net": net,
            "cycles": int(cycles),
            "segments": len(edges),
            "nodes": len(nodes),
            "components": len(comps),
            "removable": [
                {"layer": e[1],
                 "start": [round(e[4][0], 3), round(e[4][1], 3)],
                 "end": [round(e[5][0], 3), round(e[5][1], 3)],
                 "length_mm": round(e[0], 3),
                 "width_mm": round(e[6], 3)}
                for e in removable[:3]],
            "removable_length_mm": round(sum(e[0] for e in removable), 3),
        })
    rows.sort(key=lambda r: (-r["removable_length_mm"], r["net"]))
    return rows


#: Copper that is not part of any circuit. Two shapes, one defect:
#:
#:   * a track end that touches nothing -- a tail drawn off a real node and left
#:     hanging. It conducts nothing, it is a stub the etcher has to remove, and on
#:     a dense board it is an antenna.
#:   * a via with copper on one side only. The hole is drilled, plated and paid
#:     for, and it joins nothing. This is what "via_dangling" means in KiCad's own
#:     DRC, and it is the defect that hid 24 times on the SOM board.
#:
#: "Touches" is decided the way copper actually behaves, not the way coordinates
#: look: two pieces touch when their half-widths overlap, so two 0.1 mm tracks
#: whose ends sit 0.0139 mm apart ARE connected. An endpoint-only reading calls
#: that pair two dead ends and is wrong -- it reported 296 of them on a board
#: whose DRC was clean. The zone fill is consulted last, and only for a point
#: that nothing else holds, because a rail via is often held by its pour alone.
TOUCH_TOL_MM = 0.006

def _seg_point_distance(px, py, x1, y1, x2, y2):
    dx, dy = x2 - x1, y2 - y1
    l2 = dx * dx + dy * dy
    if l2 < 1e-18:
        return math.hypot(px - x1, py - y1)
    t = ((px - x1) * dx + (py - y1) * dy) / l2
    if t <= 0.0:
        return math.hypot(px - x1, py - y1)
    if t >= 1.0:
        return math.hypot(px - x2, py - y2)
    return math.hypot(px - (x1 + t * dx), py - (y1 + t * dy))

def _point_in_polygon(px, py, pts):
    inside = False
    n = len(pts)
    for i in range(n):
        x1, y1 = pts[i]
        x2, y2 = pts[(i + 1) % n]
        if (y1 > py) != (y2 > py):
            if px < (x2 - x1) * (py - y1) / (y2 - y1) + x1:
                inside = not inside
    return inside

def _dead_end(board, tol_mm=None):
    """Track ends that touch nothing, and vias with copper on one side only.

    Returns (items, n_track_ends, n_vias). Each item names `kind` so a reader can
    tell a hanging tail from a dead hole without reading the detail string.
    """
    tol = float(TOUCH_TOL_MM if tol_mm is None else tol_mm)
    from .adapters.canonical import pad_points

    # A segment's identity is its index, not its uuid: a caller may hand this
    # function bare dicts (the tests do), and "skip myself" has to work there too.
    segs = {}
    for idx, t in enumerate(board.tracks or []):
        net, lay = t.get("net"), t.get("layer")
        st, en = t.get("start"), t.get("end")
        if net is None or lay is None or not st or not en:
            continue
        segs.setdefault((net, lay), []).append(
            (float(st[0]), float(st[1]), float(en[0]), float(en[1]),
             float(t.get("width") or 0.0), idx))

    vias_by_net = {}
    for v in board.vias or []:
        if v.get("net") is None or v.get("x") is None or v.get("y") is None:
            continue
        vias_by_net.setdefault(v["net"], []).append(
            (float(v["x"]), float(v["y"]),
             float(v.get("diameter") or 0.0),
             [str(x) for x in (v.get("layers") or [])]))

    pads_by_net = {}
    for p in board.pads() or []:
        net = p.get("net")
        if net is None or p.get("x") is None or p.get("y") is None:
            continue
        lays = [str(x) for x in (p.get("layers") or [])]
        thru = bool(p.get("hole_mm")) or any(x.startswith("*") for x in lays)
        pads_by_net.setdefault(net, []).append(
            (float(p["x"]), float(p["y"]),
             None if thru else [x for x in lays if not x.startswith("*")],
             pad_points(p)))

    zones_by_net = {}
    for z in board.zones or []:
        net = z.get("net")
        polys = z.get("filled_polygons") or []
        if net is None or not polys:
            continue
        lays = [str(x) for x in (z.get("layers") or [])]
        for poly in polys:
            if len(poly) >= 3:
                xs = [q[0] for q in poly]
                ys = [q[1] for q in poly]
                zones_by_net.setdefault(net, []).append(
                    (lays, poly, min(xs), min(ys), max(xs), max(ys)))

    def zone_holds(net, lay, x, y):
        for lays, poly, x0, y0, x1, y1 in zones_by_net.get(net, ()):
            if lays and lay not in lays:
                continue
            if x < x0 or x > x1 or y < y0 or y > y1:
                continue
            if _point_in_polygon(x, y, poly):
                return True
        return False

    def copper_here(net, lay, x, y, radius, skip_uuid=None, skip_via=None):
        """Is there same-net copper on `lay` at (x,y) other than the caller?

        `skip_via` is not a nicety: without it a via is its own copper on every
        layer it spans, so every via reads as held six times and the rule can
        never fire. That is exactly what the first draft of this function did.
        """
        for x1, y1, x2, y2, w, uid in segs.get((net, lay), ()):
            if skip_uuid is not None and uid == skip_uuid:
                continue
            if _seg_point_distance(x, y, x1, y1, x2, y2) <= radius + w / 2.0 + tol:
                return True
        for px, py, pl, poly in pads_by_net.get(net, ()):
            if pl is not None and lay not in pl:
                continue
            if math.hypot(x - px, y - py) <= radius + tol:
                return True
            if len(poly) >= 3 and _point_in_polygon(x, y, poly):
                return True
        for vx, vy, vd, _vl in vias_by_net.get(net, ()):
            if skip_via is not None and math.hypot(vx - skip_via[0], vy - skip_via[1]) <= 1e-9:
                continue
            if math.hypot(x - vx, y - vy) <= radius + vd / 2.0 + tol:
                return True
        return zone_holds(net, lay, x, y)

    items = []
    n_track_ends = 0
    for (net, lay), rows in segs.items():
        for x1, y1, x2, y2, w, uid in rows:
            for ex, ey in ((x1, y1), (x2, y2)):
                if copper_here(net, lay, ex, ey, w / 2.0, skip_uuid=uid):
                    continue
                n_track_ends += 1
                items.append({"kind": "track_end", "net": net, "layer": lay,
                              "x": round(ex, 4), "y": round(ey, 4),
                              "width_mm": round(w, 3)})

    n_vias = 0
    for net, rows in vias_by_net.items():
        for vx, vy, vd, vlayers in rows:
            layers = vlayers or ["F.Cu", "B.Cu"]
            held = 0
            for lay in layers:
                if copper_here(net, lay, vx, vy, vd / 2.0, skip_via=(vx, vy)):
                    held += 1
            if held > 1:
                continue
            n_vias += 1
            items.append({"kind": "via", "net": net,
                          "x": round(vx, 4), "y": round(vy, 4),
                          "diameter_mm": round(vd, 3),
                          "layers_with_copper": held,
                          "layers_spanned": len(layers)})
    items.sort(key=lambda r: (r["kind"] != "via", r["net"] or "", r["x"], r["y"]))
    return items, n_track_ends, n_vias

def gate(board, strict=False, angle_tol_deg=1.0,
         hard_ratio_signal=None, hard_ratio_power=None, top=20):
    """One verdict for "is this copper drawn the way the house draws it".

    `strict` promotes `square_corner` and every detour over the soft threshold to
    blocking. The default is deliberately not strict: a strict-by-default gate on
    a board with pad pockets would refuse everything and be turned off, and a gate
    that gets turned off constrains nobody.

    Returns the ranked rule table with each rule's measured value, the blocking
    subset, and a `next_fix` naming the single highest-priority item. It is not a
    DRC: clearance, holes, mask and connectivity are `board_drc`'s job, and a board
    that passes here can still be electrically broken.
    """
    ang = _oracle.angle_audit(board, layer=None, angle_tol_deg=angle_tol_deg,
                              top_other=int(top))
    if isinstance(ang, dict) and ang.get("status") != "ok":
        return ang
    av = (ang.get("value") if isinstance(ang, dict) else None) or {}

    acute_list, square_list = [], []
    other_pct_max = 0.0
    other_segs = []
    for lay, c in (av.get("corners") or {}).items():
        for row in (c.get("acute_list") or []):
            acute_list.append(dict(row, layer=lay))
        for row in (c.get("right_angle_list") or []):
            square_list.append(dict(row, layer=lay))
    for lay, st in (av.get("per_layer") or {}).items():
        other_pct_max = max(other_pct_max, float(st.get("other_pct") or 0.0))
        for row in (st.get("other_segments") or []):
            other_segs.append(dict(row, layer=lay))
    acute_total = sum(int(c.get("corner_acute") or 0)
                      for c in (av.get("corners") or {}).values())
    square_total = sum(int(c.get("corner_90") or 0)
                       for c in (av.get("corners") or {}).values())
    other_total = sum(int(s.get("other") or 0)
                      for s in (av.get("per_layer") or {}).values())
    arc_tracks = sum(int(s.get("arc_tracks") or 0)
                     for s in (av.get("per_layer") or {}).values())

    dups = _duplicate_copper(board)
    loops = _redundant_loop(board)
    dead_items, dead_ends, dead_vias = _dead_end(board)

    geo = _oracle.net_geometry_report(board, min_segments=1, top=0, detour_flag=1.8)
    gv = (geo.get("value") if isinstance(geo, dict) else None) or {}
    hard_s = float(HARD_RATIO_SIGNAL if hard_ratio_signal is None else hard_ratio_signal)
    hard_p = float(HARD_RATIO_POWER if hard_ratio_power is None else hard_ratio_power)
    soft = 1.8
    detour_soft, detour_hard = [], []
    for row in (gv.get("nets") or []):
        ratio = row.get("detour_ratio")
        if ratio is None:
            continue
        cap = hard_p if _is_powerish(row.get("net")) else hard_s
        item = {"net": row["net"], "detour_ratio": ratio,
                "length_mm": row.get("length_mm"),
                "pad_mst_mm": row.get("pad_mst_mm"),
                "segments": row.get("segments"), "hard_cap": cap}
        if ratio >= cap:
            detour_hard.append(item)
        elif ratio >= soft:
            detour_soft.append(item)
    detour_hard.sort(key=lambda r: -r["detour_ratio"])
    detour_soft.sort(key=lambda r: -r["detour_ratio"])

    rules = []
    def _rule(r, value, blocked, detail, items=None):
        rules.append({"priority": r["priority"], "id": r["id"],
                      "rule": r["rule"], "measured": value,
                      "blocking": bool(blocked and (r["blocking"] or strict)),
                      "detail": detail,
                      "items": (items or [])[:int(top)]})

    _rule(RULES[0], acute_total, acute_total > 0,
          "%d corner(s) with an interior angle under 90 deg - acid trap, redraw "
          "the shape; it does not tune away" % acute_total, acute_list)
    _rule(RULES[1], other_total, other_total > 0,
          "%d segment(s) are neither H, V nor 45 (worst layer %.2f%%)"
          % (other_total, other_pct_max), other_segs)
    _rule(RULES[2], len(dups), bool(dups),
          "%d duplicated track(s): same net, layer and both endpoints" % len(dups),
          dups)
    # A rail is allowed to be a grid. Feeding one ball row from two stitched
    # rails is how power is shared without widening a trace, and `/ +1V8_ANA` on
    # the SOM board does exactly that between y=31.246 and y=31.521. A signal has
    # no such reason: two paths between the same two pins is copper drawn twice.
    # Both are listed, only the signal rings block, and the rail rows are still
    # in the report so nobody can hide an accident behind the exemption.
    loop_rail = [r for r in loops if _is_powerish(r["net"])]
    loop_sig = [r for r in loops if not _is_powerish(r["net"])]
    _rule(RULES[3], len(loop_sig), bool(loop_sig),
          "%d signal net(s) close a ring against themselves (%d ring(s)); the long "
          "way around is listed first and is the piece to take out. %d rail net(s) "
          "with %d ring(s) are listed but not blocking - a rail may be a stitched "
          "grid on purpose" % (len(loop_sig),
                               sum(r["cycles"] for r in loop_sig),
                               len(loop_rail),
                               sum(r["cycles"] for r in loop_rail)),
          loop_sig + loop_rail)
    _rule(RULES[4], dead_ends + dead_vias, bool(dead_items),
          "%d track end(s) touch no copper and %d via(s) have copper on one side "
          "only - neither joins anything. Copper that carries nothing is a stub "
          "the etcher still has to make and a hole that is drilled and paid for "
          "and does nothing. Touching is judged by half-width overlap, so two "
          "0.1 mm tracks whose ends sit 0.0139 mm apart count as joined"
          % (dead_ends, dead_vias), dead_items)
    _rule(RULES[5], square_total, False,
          "%d square (90 deg) corner(s); a 135 chamfer may or may not have been "
          "available - blocking only with strict=true" % square_total, square_list)
    _rule(RULES[6], len(detour_hard), bool(detour_hard),
          "%d net(s) past the hard detour cap (%d more between 1.8 and the cap)"
          % (len(detour_hard), len(detour_soft)),
          [dict(r, severity="hard") for r in detour_hard]
          + [dict(r, severity="soft") for r in detour_soft])

    blocking = [r for r in rules if r["blocking"]]
    verdict = "shape_pass" if not blocking else "shape_fail"
    next_fix = None
    if blocking:
        top_rule = min(blocking, key=lambda r: r["priority"])
        next_fix = {"rule": top_rule["id"], "priority": top_rule["priority"],
                    "measured": top_rule["measured"],
                    "detail": top_rule["detail"]}

    return ok(
        value={
            "verdict": verdict,
            "strict": bool(strict),
            "blocking": [r["id"] for r in blocking],
            "arc_tracks": arc_tracks,
            "next_fix": next_fix,
            "rules": rules,
            "rule_order": [{"priority": r["priority"], "id": r["id"],
                            "blocking_by_default": r["blocking"],
                            "rule": r["rule"]} for r in RULES],
            "read_it_like_this": (
                "verdict is pass only when no rule that is blocking by default "
                "fires; strict=true also blocks square corners and every detour "
                "past 1.8. `rules` is already sorted by priority, so fix rules[0]"
                " first. This is NOT a DRC: clearance, holes, mask and "
                "connectivity are board_drc's job, and a board that passes here "
                "can still be electrically broken. Arcs are not a violation: "
                "they are counted in arc_tracks and excluded from the heading and "
                "corner rows, because a curve sampled into chords looks like 1165 "
                "segments at random angles"),
        },
        unit="count / ratio",
        model="angle_audit (segment headings, corner interior angles) + "
              "net_geometry_report (length over pad MST) + an endpoint duplicate "
              "scan + a per-net copper graph whose rings are counted, each folded "
              "into a ranked pass/fail",
        basis="the board's own track list; corners are seen only where two "
              "segments share an endpoint to 0.001 mm; the detour denominator is "
              "the pad-centre spanning tree, a lower bound",
        precision="duplicates are matched to %.2f mm on the endpoints; headings to "
                  "%.2f deg; rings are found on a graph snapped to %.2f mm with "
                  "cuts shallower than %.2f mm ignored; the detour caps are "
                  "judgements, echoed per net"
                  % (0.002, float(angle_tol_deg), SNAP_MM, MIN_EDGE_MM),
        assumptions={"strict": bool(strict),
                     "angle_tol_deg": float(angle_tol_deg),
                     "hard_ratio_signal": hard_s,
                     "hard_ratio_power": hard_p,
                     "soft_ratio": soft})
