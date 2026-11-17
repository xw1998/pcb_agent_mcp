"""Plan the copper a power rail needs, then judge the copper the board already has.

Why this module exists: `board_power_copper` says WHICH carrier is failing
(`backing=traces_only`, `narrower_than_asked`) but never says what to do about it,
so the only ways forward were "widen something" or "pour something" - both guesses.
On the RK3506 SOM that guess was wrong six times out of six: `+1V8_ANA` runs 91
segments at 0.1 mm with no plane, needs 0.786 mm for 2 A, and nothing in the
answer said whether 0.786 mm even fits there.

So this planner answers four questions per net, in the order that decides what
"done" means:

1. **Coverage (does the copper reach the pads).** A plane that misses a ball, or a
   pad with no copper of its net on it, makes every later number irrelevant.
   `pads.orphan` names the pads; nothing else in this file is worth reading until
   that list is empty.
2. **Area (is the copper thick enough).** For a plane the file states
   `min_thickness` - KiCad will not pour copper thinner than that, so
   `min_thickness >= required` is a conservative PROOF that the pour carries the
   current, and `min_thickness < required` proves nothing (the pour may be fine;
   the file does not say so). That asymmetry is reported as `covered` vs
   `plane_covered_capacity_unmeasured`, never as pass/fail on area alone: area in
   mm2 has no current in it at all, and the width of the copper that actually
   carries the current is the fill outline minus its voids - a shape computation
   this module does not do, so it is reported as unmeasured rather than guessed.
3. **Cut (is the pour still the pour).** A same-layer foreign track whose centre
   line lies inside a filled polygon means the polygons predate the track - a
   stale pour, which reads as copper everywhere and connects nothing. Also
   reported: two different nets at the same priority on one layer, because the
   file has no way left to say which of them wins.
4. **Failure (can this answer be wrong).** Every row says what would make it
   false (`why`), and a net with no current has no demand and gets `no_demand`
   rather than a default ampacity. A planner that invents the current would
   happily approve 0.1 mm copper for 10 A.

What it will NOT do: invent the pour outline. `actions` carry the numbers a pour
needs (net, layer, priority, min_thickness) and `needs_outline: true`; the shape
stays the caller's, exactly as `board_add_zone` requires.
"""
import math

from .. import geometry as G
from ..calc import current as CUR


class PlanError(ValueError):
    """Bad input: no currents, an unknown net, a current that is not a number."""


# Verdicts, strongest evidence first. The order is what makes a row readable on
# its own: "pads_uncovered" outranks "covered" because a plane that misses a ball
# is not covering anything.
VERDICTS = (
    "no_demand",
    "no_copper",
    "plane_stale",
    "pads_uncovered",
    "covered",
    "plane_covered_capacity_unmeasured",
    "traces_ok",
    "widen_traces",
    "pour_needed",
)

#: How far out a "is there room to widen" search looks beyond the demanded width.
#: The window must exceed the demand, or a track with plenty of room would be
#: reported as unmeasurable instead of legal.
ROOM_WINDOW_EXTRA_MM = 1.0


def copper_layers(board):
    """Copper layer names in stackup order - the order is what makes a via span."""
    names = [str(s.get("name")) for s in (board.stackup or [])
             if str(s.get("name") or "").endswith(".Cu")]
    return names or ["F.Cu", "B.Cu"]


def _is_external(board, layer):
    cu = copper_layers(board)
    return layer in (cu[0], cu[-1])


def _pad_layers(pad, cu):
    """Which copper layers a pad really has copper on.

    Two spellings are wildcards and must be expanded, or every through-hole pad
    reads as "not on the layer" and a covered rail is reported uncovered: a
    through-hole pad stores `*.Cu` (all copper) and a front-and-back pad stores
    `F&B.Cu`. Measured on this board: 8 pads of `/+3V3` and `/+5V_IN` sit
    directly under a 0-distance track of their own net and were still counted as
    orphan until this expansion existed.
    """
    raw = [str(l) for l in (pad.get("layers") or [])]
    if "*.Cu" in raw:
        return set(cu)
    if "F&B.Cu" in raw:
        return {cu[0], cu[-1]} if len(cu) > 1 else {cu[0]}
    return set(l for l in raw if l.endswith(".Cu"))


# ------------------------------------------------------------- geometry bits
def _seg_poly_dist(a, b, poly):
    """Distance from a segment to a polygon's EDGE (0 when it touches or enters)."""
    if G.point_in_polygon(a, poly) or G.point_in_polygon(b, poly):
        return 0.0
    n = len(poly)
    d = min(G.dist_point_polygon(a, poly), G.dist_point_polygon(b, poly))
    for i in range(n):
        d = min(d, G.dist_segment_segment(a, b, poly[i], poly[(i + 1) % n]))
    return d


def _edge_segments(board):
    segs = []
    for ring in (board.outline or []):
        if len(ring) < 3:
            continue
        n = len(ring)
        for i in range(n):
            segs.append((ring[i], ring[(i + 1) % n]))
    return segs


def _foreign_items(board, layer, net, cache):
    """Pads / tracks / vias of OTHER nets on one copper layer (cached per layer)."""
    key = (layer, net)
    if key not in cache:
        from ..oracle import _items
        cache[key] = [it for it in _items(board, layer=layer)
                      if it.get("net") != net]
    return cache[key]


def _room_mm(board, seg_a, seg_b, width, foreign, edges, clearance, window):
    """How much wider this segment may legally get, in mm of EDGE room.

    Returns `(room, near)` where `room` is the free gap left after the clearance
    (so a legal width is `width + 2*room`) and `near` names what is closest.
    `room=None` means nothing was found inside `window`, i.e. the answer is "at
    least `window`", not "unlimited" - that distinction is what keeps a corner
    outside the window from being reported as free space.
    """
    from ..oracle import _item_bbox, _dist
    a, b = seg_a, seg_b
    x0, x1 = min(a[0], b[0]) - window, max(a[0], b[0]) + window
    y0, y1 = min(a[1], b[1]) - window, max(a[1], b[1]) + window
    seg_item = {"kind": "track", "net": None, "layer": None,
                "seg": (a, b, width), "ref": "this segment"}
    best, near = None, None
    for it in foreign:
        bx = _item_bbox(it)
        if bx[2] < x0 or bx[0] > x1 or bx[3] < y0 or bx[1] > y1:
            continue
        d = _dist(seg_item, it)
        if best is None or d < best:
            best, near = d, it
    room = None if best is None else best - float(clearance)
    # board edge: a track that leaves the board is not a track. Checked even when
    # no copper was found inside the window - on a small board the edge is the
    # thing that is close, and skipping it would report free space that is not.
    for (ea, eb) in edges:
        if max(ea[0], eb[0]) < x0 or min(ea[0], eb[0]) > x1 \
                or max(ea[1], eb[1]) < y0 or min(ea[1], eb[1]) > y1:
            continue
        de = G.dist_segment_segment(a, b, ea, eb) - width / 2.0 - float(clearance)
        if room is None or de < room:
            room = de
            near = {"kind": "board_edge", "net": None, "ref": "Edge.Cuts"}
    return room, near


# ----------------------------------------------------------------- the plan
def _need_width(demand, layer, delta_t_c, copper_oz, standard):
    which = "external" if layer else "internal"
    r = CUR.width_for_current(float(demand), delta_t_c=float(delta_t_c),
                             copper_oz=float(copper_oz), layer=which,
                             standard=standard)
    if r.get("status") != "ok":
        raise PlanError("IPC width for %s A: %s"
                        % (demand, r.get("error") or r.get("status")))
    return float(r["value"])


def _round_up(x, step=0.05):
    return round(math.ceil(x / step - 1e-9) * step, 3)


def _segment_touches_polygon(a, b, poly, half_width):
    """Does a track body (centreline + half its width) overlap this fill?

    A fill that merges with a same-net track of the same layer is electrically
    connected to whatever that track lands on. Counting only pads and vias made
    this planner call such a pour "reaches nothing", which is a false negative:
    on the delivery board the /+5V_IN rectangle sits on top of the 0.80 mm In3
    bus and touches no pad and no via at all.
    """
    if G.point_in_polygon(a, poly) or G.point_in_polygon(b, poly):
        return True
    n = len(poly)
    for i in range(n):
        c = poly[i]
        d = poly[(i + 1) % n]
        if G.segments_intersect(a, b, c, d):
            return True
        if G.dist_segment_segment(a, b, c, d) <= half_width + 1e-9:
            return True
    return False


def plan(board, currents, nets=None, delta_t_c=10.0, copper_oz=1.0,
         standard="ipc2221", clearance_mm=None, edge_clearance_mm=None,
         window_mm=None):
    """Per rail: what copper it needs, what it has, and what to do about the gap.

    `currents` maps net -> amps. A net named in `nets` but absent from `currents`
    comes back `no_demand` with its geometry and no width judgement at all: there
    is no way to say "enough copper" without knowing the current, and inventing
    one would approve 0.1 mm for 10 A. Passing neither `currents` nor `nets`
    raises, because then there is nothing to report.

    `clearance_mm` / `edge_clearance_mm` default to what the board's own rules
    file says (`.kicad_pro` min_clearance / min_copper_edge_clearance). Passing a
    number overrides it; guessing 0.2 mm on a board whose rule is 0.1 mm invented
    a violation at every fine-pitch escape, which is why the default is read.
    """
    if not currents and not nets:
        raise PlanError("pass currents={net: amps} (and optionally nets=[...] to "
                        "inspect a net with no current); with neither there is "
                        "nothing to report")
    rules = getattr(board, "rules", None) or {}
    clr_src = "argument"
    if clearance_mm is None:
        clearance_mm = (rules.get("clearance") or {}).get("min_clearance")
        clr_src = "board.rules.min_clearance" if clearance_mm is not None else "default"
    if clearance_mm is None:
        clearance_mm = 0.2
    edge_src = "argument"
    if edge_clearance_mm is None:
        edge_clearance_mm = (rules.get("clearance") or {}).get(
            "min_copper_edge_clearance")
        edge_src = ("board.rules.min_copper_edge_clearance"
                    if edge_clearance_mm is not None else "default")
    if edge_clearance_mm is None:
        edge_clearance_mm = 0.3
    want = {}
    for net, amps in (currents or {}).items():
        try:
            amps = float(amps)
        except Exception:
            raise PlanError("current for %r is not a number: %r" % (net, amps))
        if amps <= 0:
            raise PlanError("current for %r must be > 0, got %r" % (net, amps))
        want[str(net)] = amps
    if nets:
        # Listing a net with no current is an INSPECTION, not an error: it comes
        # back `no_demand` (geometry only, nothing judged) instead of guessing A.
        want = {str(n): want.get(str(n)) for n in nets}

    # Net names arrive with or without the sheet path: KiCad stores `/+3V3`,
    # people type `+3V3`. Resolving here (and reporting BOTH spellings) is what
    # keeps a correct plan from coming back `no_copper` because of a slash.
    # The net TABLE counts, not just the items: a net declared on the board and
    # carrying no copper at all is a real answer (`no_copper`), not a bad name.
    known = set()
    for n in (getattr(board, "nets", None) or {}).values():
        if n:
            known.add(str(n))
    for t in board.tracks:
        if t.get("net"):
            known.add(t["net"])
    for v in board.vias:
        if v.get("net"):
            known.add(v["net"])
    for z in (board.zones or []):
        if z.get("net"):
            known.add(z["net"])
    for fp in board.footprints:
        for pd in fp.get("pads") or []:
            if pd.get("net"):
                known.add(pd["net"])
    alias = {}
    for n in sorted(known):
        alias.setdefault(str(n).lstrip("/"), n)
    resolved = {}
    for net, amps in want.items():
        if net in known:
            resolved[net] = (net, amps)
            continue
        hit = alias.get(net.lstrip("/"))
        if hit is None:
            raise PlanError("net %r is not on this board; it has %d names, e.g. %s"
                            % (net, len(known), ", ".join(sorted(known)[:6])))
        resolved[str(hit)] = (net, amps)
    want = resolved

    cu = copper_layers(board)
    edges = _edge_segments(board)

    # one index of foreign copper per (layer, net) actually needed
    by_layer = {}
    tracks_of = {}
    vias_of = {}
    for t in board.tracks:
        n = t.get("net")
        if n is None:
            continue
        tracks_of.setdefault(n, []).append(t)
    for v in board.vias:
        n = v.get("net")
        if n is not None:
            vias_of.setdefault(n, []).append(v)

    pads_of = {}
    for fp in board.footprints:
        for pd in fp.get("pads") or []:
            n = pd.get("net")
            if n is None or pd.get("x") is None:
                continue
            pads_of.setdefault(n, []).append((fp, pd))

    zone_rows = {}
    for z in (board.zones or []):
        if z.get("keepout"):
            continue
        n = z.get("net")
        polys = [q for q in (z.get("filled_polygons") or []) if len(q) >= 3]
        for lay in (z.get("layers") or []):
            zone_rows.setdefault(lay, []).append(
                {"net": n, "priority": z.get("priority", 0),
                 "min_thickness_mm": z.get("min_thickness"),
                 "connect": (z.get("connect_pads_effective")
                             or z.get("connect_pads")),
                 "thermal_bridge_mm": z.get("thermal_bridge_width_mm"),
                 "filled": bool(polys), "polys": polys,
                 "area_mm2": sum(_poly_area(q) for q in polys)})

    # layer-by-layer "who owns the copper", which is the only plane split this
    # format can express; a tie at the same priority is reported as such.
    layers = []
    for lay in cu:
        zs = zone_rows.get(lay) or []
        ties = []
        by_pri = {}
        for z in zs:
            by_pri.setdefault(z["priority"], set()).add(z["net"])
        for pri, owners in sorted(by_pri.items()):
            named = sorted(o for o in owners if o is not None)
            if len(named) > 1:
                # A tie only arbitrates something where the fills actually meet.
                # Two zones at the same priority that sit in different parts of
                # the layer cannot starve each other, and reporting the tie as a
                # problem would be a false alarm (the delivery board's In3 has
                # /+3V3 and /+5V_IN both at 0, 0.6 mm apart, touching nowhere).
                meet = False
                polys_by_net = {}
                for z in zs:
                    if z["net"] in named and z["filled"]:
                        polys_by_net.setdefault(z["net"], []).extend(z["polys"])
                for i, n1 in enumerate(named):
                    for n2 in named[i + 1:]:
                        for p1 in polys_by_net.get(n1, []):
                            for p2 in polys_by_net.get(n2, []):
                                if G.dist_polygon_polygon(p1, p2) <= 1e-9:
                                    meet = True
                                    break
                            if meet:
                                break
                        if meet:
                            break
                    if meet:
                        break
                ties.append({"priority": pri, "nets": named,
                             "fills_meet": meet,
                             "why": ("at the same priority the fills meet, so one "
                                     "of them has to be lifted or one will be cut "
                                     "back by the other"
                                     if meet else
                                     "at the same priority, but the fills do not "
                                     "meet on this layer: the tie arbitrates "
                                     "nothing here")})
        layers.append({"layer": lay,
                       "zones": [{"net": z["net"], "priority": z["priority"],
                                  "area_mm2": round(z["area_mm2"], 2),
                                  "filled": z["filled"],
                                  "min_thickness_mm": z["min_thickness_mm"]}
                                 for z in sorted(zs, key=lambda r: (-r["priority"],
                                                                    r["net"] or ""))],
                       "free": not zs,
                       "priority_ties": ties})

    rows = []
    for net, (asked, demand) in sorted(want.items()):
        row = _one(board, net, demand, cu, edges, by_layer, tracks_of,
                   vias_of, pads_of, zone_rows, delta_t_c, copper_oz,
                   standard, clearance_mm, clr_src, edge_clearance_mm,
                   window_mm)
        row["net_asked"] = asked
        rows.append(row)

    summary = {}
    for r in rows:
        summary[r["verdict"]] = summary.get(r["verdict"], 0) + 1

    return {"rows": rows, "summary": summary, "layers": layers,
            "assumptions": {
                "clearance_mm": round(float(clearance_mm), 4),
                "clearance_source": clr_src,
                "edge_clearance_mm": round(float(edge_clearance_mm), 4),
                "edge_clearance_source": edge_src,
                "delta_t_c": float(delta_t_c),
                "copper_oz": float(copper_oz),
                "standard": standard,
            },
            "model": ("per-net copper plan: IPC-2221 demand width vs measured trace "
                      "widths, legal widening room from exact clearance to foreign "
                      "copper, and for planes a coverage count plus the one number "
                      "the file states about thickness (min_thickness)"),
            "precision": (
                "a plane is proven to carry a current ONLY when its stated "
                "min_thickness >= the demanded width. Below that nothing is proven "
                "and nothing is denied: the corridor width (fill outline minus its "
                "voids) is a shape fact this planner deliberately does not compute, "
                "so capacity comes back 'unmeasured' rather than estimated. "
                "pad_rim_min_mm is the distance from the nearest pad centre to the "
                "fill edge - a rim exists check, not a cross-section. mm2 areas and "
                "pad counts are measured but never judged. 'room to widen' uses "
                "centre-line distance minus clearance and assumes the segment can "
                "grow symmetrically; it says nothing about what happens at the "
                "segment's ends."),
            "read_it_like_this": (
                "verdict is the FIRST defect found, not a score: no_demand means no "
                "current was given for a net you asked to inspect (geometry shown, "
                "nothing judged), no_copper means not one item of this net is on "
                "the board, "
                "plane_stale means the filled polygons predate same-layer tracks of "
                "other nets (refill, then re-plan), pads_uncovered means a ball of "
                "the rail touches no copper of its own net, covered means a filled "
                "plane states min_thickness >= the demanded width, "
                "plane_covered_capacity_unmeasured means the pour reaches the pads "
                "but the file proves nothing about the copper width that carries the "
                "current, traces_ok means traces alone are wide enough, "
                "widen_traces means they are not but legally could be, and "
                "pour_needed means at least one neck cannot be widened inside its "
                "clearance. Read 'unmeasured' as unmeasured, not as failing: it is "
                "an information gap, and the action says what would close it"),
            "actions_need": ("every pour action carries needs_outline=true: this "
                             "planner names net / layer / priority, the outline "
                             "stays the caller's (board_add_zone)")}


def _poly_area(poly):
    a = 0.0
    n = len(poly)
    for i in range(n):
        x1, y1 = poly[i]
        x2, y2 = poly[(i + 1) % n]
        a += x1 * y2 - x2 * y1
    return abs(a) / 2.0


def _net_clearance(board, net, base_mm, base_src):
    """The clearance KiCad actually applies to this net, and where it came from.

    `design_settings.rules.min_clearance` is a project-wide FLOOR, not the number
    DRC uses pair by pair: KiCad measures the clearance between two items against
    their net classes, and the floor only bounds it from below. Using the floor
    alone made "there is room to widen" optimistic on any board whose power nets
    sit in a class with a bigger clearance, so the class value is raised over it
    whenever the project says which class this net is in.
    """
    rules = getattr(board, "rules", None) or {}
    classes = rules.get("net_classes") or {}
    best = None
    for spec in classes.values():
        if net not in (spec.get("nets") or []):
            continue
        c = spec.get("clearance")
        if isinstance(c, (int, float)) and not isinstance(c, bool):
            best = float(c) if best is None else max(best, float(c))
    if best is None:
        return float(base_mm), base_src
    if best <= float(base_mm):
        # the class cannot lower the project floor, so the floor stays the rule
        return float(base_mm), base_src
    return best, "board.rules.net_classes"


def _one(board, net, demand, cu, edges, by_layer, tracks_of, vias_of, pads_of,
         zone_rows, delta_t_c, copper_oz, standard, clearance_mm, clr_src,
         edge_clearance_mm, window_mm):
    from ..oracle import _item_bbox, _dist
    from ..adapters.canonical import pad_points

    clearance_mm, clr_src = _net_clearance(board, net, clearance_mm, clr_src)

    if demand is None:
        ext_need = int_need = None
        need = {"external": None, "internal": None}
    else:
        ext_need = _need_width(demand, True, delta_t_c, copper_oz, standard)
        int_need = _need_width(demand, False, delta_t_c, copper_oz, standard)
        need = {"external": round(ext_need, 4), "internal": round(int_need, 4)}

    tracks = tracks_of.get(net) or []
    pads = pads_of.get(net) or []
    vias = vias_of.get(net) or []

    plane_layers = {}
    plane_min_thk = {}
    plane_polys = []          # (layer, poly)
    for lay in cu:
        for z in (zone_rows.get(lay) or []):
            if z["net"] != net or not z["filled"]:
                continue
            plane_layers[lay] = round(plane_layers.get(lay, 0.0) + z["area_mm2"], 2)
            thk = z["min_thickness_mm"]
            prev = plane_min_thk.get(lay, "unset")
            if thk is None or prev is None:
                plane_min_thk[lay] = None
            elif prev == "unset":
                plane_min_thk[lay] = float(thk)
            else:
                plane_min_thk[lay] = min(float(prev), float(thk))
            for q in z["polys"]:
                plane_polys.append((lay, q))
    plane_area = round(sum(plane_layers.values()), 2)

    # ---- 1. coverage ------------------------------------------------------
    covered_pads = 0
    orphan = []
    for fp, pd in pads:
        if _pad_on_copper(pd, cu, tracks, vias, plane_polys):
            covered_pads += 1
        else:
            orphan.append("%s.%s" % (fp.get("ref") or "?", pd.get("number") or "?"))

    # ---- 3. cut: is the pour stale, and do two owners tie -----------------
    stale = []
    for lay, poly in plane_polys:
        for t in board.tracks:
            tn = t.get("net")
            if tn is None or tn == net or t.get("layer") != lay:
                continue
            a, b = t.get("start"), t.get("end")
            if not a or not b:
                continue
            if G.point_in_polygon(a, poly) or G.point_in_polygon(b, poly):
                stale.append({"layer": lay, "net": tn,
                              "ref": "%s.%s" % (t.get("start"), t.get("end"))})
                break

    # ---- 2. area / width --------------------------------------------------
    segs = []
    for t in tracks:
        a, b = t.get("start"), t.get("end")
        if not a or not b:
            continue
        lay = t.get("layer")
        w = float(t.get("width") or 0.0)
        req = None if ext_need is None else (
            ext_need if _is_external(board, lay) else int_need)
        segs.append({"a": a, "b": b, "layer": lay, "w": w, "req": req})
    widths = sorted({round(s["w"], 3) for s in segs})
    thin = [s for s in segs if s["req"] is not None and s["w"] + 1e-9 < s["req"]]
    thin_len = sum(math.hypot(s["b"][0] - s["a"][0], s["b"][1] - s["a"][1])
                   for s in thin)

    room = None          # smallest room found anywhere (what the flags read)
    legal_widen = None   # smallest LEGAL width over the thin segments
    neck = None          # the segment that sets `legal_widen`
    if thin:
        win = window_mm or (max(s["req"] for s in thin) + ROOM_WINDOW_EXTRA_MM)
        for s in thin:
            r, near = _room_mm(board, s["a"], s["b"], s["w"],
                               _foreign_items(board, s["layer"], net, by_layer),
                               edges, clearance_mm, win)
            if r is not None and (room is None or r < room):
                room = r
            if r is None:
                # nothing foreign inside the window: this segment is not the one
                # that decides how wide the copper may get
                continue
            legal_s = s["w"] + 2.0 * r
            if legal_widen is None or legal_s < legal_widen:
                legal_widen = legal_s
                neck = {"layer": s["layer"], "width_mm": round(s["w"], 3),
                        "required_mm": round(s["req"], 3),
                        "room_mm": round(r, 3),
                        "legal_width_mm": round(legal_s, 3),
                        "criterion": "smallest legal width (w + 2*room) over the "
                                     "segments that are below the demand",
                        "blocked_by": (None if near is None else
                                       {"kind": near.get("kind"),
                                        "net": near.get("net"),
                                        "ref": near.get("ref")})}
    if legal_widen is not None:
        legal_widen = round(legal_widen, 3)

    # What a filled plane file can and cannot prove.
    #
    # `min_thickness` is the ONLY statement a zone makes about how thick the
    # copper is: KiCad will not pour thinner than that, so
    # `min_thickness >= required` is a conservative PROOF that the pour carries
    # the current. Below that it proves nothing - the copper may well be fine and
    # the file simply does not say - so a plane is never called too thin.
    #
    # What is NOT measured: the width of the copper that actually carries the
    # current. That is the union of the filled polygons minus their voids, i.e. a
    # shape computation, and this module does not do shape computations to invent
    # a number. `pad_rim_min_mm` is printed because a caller deserves to see there
    # IS a rim of copper around the nearest pad, but it is a distance from a pad
    # centre to a polygon edge - it is not a cross-section, and doubling it (an
    # earlier draft did exactly that and called the result an "ampacity mouth")
    # does not make it one. Neither pad count nor mm2 area says a plane carries a
    # current.
    plane_ok = {}
    for lay, area in plane_layers.items():
        thk = plane_min_thk.get(lay)
        req = (None if ext_need is None else
               (ext_need if _is_external(board, lay) else int_need))
        inside = 0
        touching = 0
        straddling = []
        rim = []
        for fp, pd in pads:
            lay_pad = _pad_layers(pd, cu)
            if lay_pad and lay not in lay_pad:
                continue
            pad_poly = pad_points(pd)
            for (l2, poly) in plane_polys:
                if l2 != lay:
                    continue
                c = (pd["x"], pd["y"])
                near = (len(pad_poly) >= 3
                        and G.dist_polygon_polygon(pad_poly, poly) <= 1e-9)
                if not near and not G.point_in_polygon(c, poly):
                    continue
                touching += 1
                if G.point_in_polygon(c, poly):
                    inside += 1
                    rim.append(G.dist_point_polygon(c, poly))
                    if len(pad_poly) >= 3 and not all(
                            G.point_in_polygon(q, poly) for q in pad_poly):
                        straddling.append("%s.%s" % (fp.get("ref") or "?",
                                                     pd.get("number") or "?"))
                break
        vias_inside = 0
        for v in vias:
            p = v.get("at") or (v.get("x"), v.get("y"))
            if not p or p[0] is None:
                continue
            for (l2, poly) in plane_polys:
                if l2 != lay:
                    continue
                if G.point_in_polygon(p, poly):
                    vias_inside += 1
                    rim.append(G.dist_point_polygon(p, poly))
                    break
        tracks_touching = 0
        for sg in segs:
            if sg["layer"] != lay:
                continue
            for (l2, poly) in plane_polys:
                if l2 != lay:
                    continue
                if _segment_touches_polygon(sg["a"], sg["b"], poly,
                                            float(sg["w"]) / 2.0):
                    tracks_touching += 1
                    break
        rim.sort()
        connects = set()
        bridges = []
        for z in (zone_rows.get(lay) or []):
            if z["net"] != net or not z["filled"]:
                continue
            if z.get("connect"):
                connects.add(str(z["connect"]))
            if z.get("thermal_bridge_mm") is not None:
                bridges.append(float(z["thermal_bridge_mm"]))
        proves = (req is not None and thk is not None
                  and float(thk) + 1e-9 >= req)
        plane_ok[lay] = {
            "min_thickness_mm": thk,
            "required_mm": (None if req is None else round(req, 4)),
            "proves_enough": bool(proves),
            "connect_modes": sorted(connects),
            "thermal_bridge_mm": (round(min(bridges), 3) if bridges else None),
            "pads_inside": inside,
            "pads_touching": touching,
            "pads_straddling": straddling[:4],
            "vias_inside": vias_inside,
            "tracks_touching": tracks_touching,
            "pad_rim_min_mm": (round(rim[0], 3) if rim else None),
            "measured": ["min_thickness_mm", "connect_modes", "pads_touching",
                         "pads_inside", "pads_straddling", "vias_inside",
                         "tracks_touching", "thermal_bridge_mm"],
            "not_measured": ["current_corridor_width"],
            "why_unmeasured": (
                "the file states no corridor width; the copper width that carries "
                "the current is the fill outline minus its voids, a shape "
                "computation this module does not do, so the capacity is reported "
                "as unmeasured instead of estimated. pad_rim_min_mm is a pad-to-"
                "edge distance and is not a cross-section."),
        }
        if touching == 0 and vias_inside == 0 and tracks_touching == 0:
            plane_ok[lay]["reaches_nothing"] = True
        if not proves:
            plane_ok[lay]["why_not"] = (
                "no current was given, so no width can be demanded of this plane; "
                "coverage is shown, thickness is not judged" if req is None else
                "the plane does not state min_thickness >= %.3f mm. That is not "
                "evidence the copper is too thin - only that the file does not "
                "prove it is thick enough to carry %.3f A." % (req, demand))

    # ---- verdict ----------------------------------------------------------
    flags = []
    actions = []
    if demand is None:
        verdict = "no_demand"
    elif stale:
        verdict = "plane_stale"
        flags.append("foreign_track_inside_plane")
        actions.append({"op": "fill_zones", "why": "the pour predates a same-layer "
                        "track of another net; refill, then re-plan"})
    elif not tracks and not plane_polys and not vias and not pads:
        verdict = "no_copper"
    elif orphan:
        verdict = "pads_uncovered"
        flags.append("pad_without_copper")
    elif plane_polys:
        if all(v["proves_enough"] for v in plane_ok.values()):
            verdict = "covered"
        else:
            verdict = "plane_covered_capacity_unmeasured"
            flags.append("plane_capacity_unmeasured")
            if any(v.get("reaches_nothing") for v in plane_ok.values()):
                flags.append("plane_reaches_nothing")
            for lay, v in sorted(plane_ok.items()):
                if v["proves_enough"]:
                    continue
                actions.append({
                    "op": "prove_plane_capacity", "net": net, "layer": lay,
                    "min_thickness_mm": v["min_thickness_mm"],
                    "required_mm": v["required_mm"],
                    "connect_modes": v["connect_modes"],
                    "thermal_bridge_mm": v["thermal_bridge_mm"],
                    "pads_inside": v["pads_inside"],
                    "pads_touching": v["pads_touching"],
                    "pads_straddling": v["pads_straddling"],
                    "why": ("the pour touches %d pad(s), contains %d via(s) and "
                            "touches %d same-net track(s) on %s, and the file proves "
                            "nothing about the copper width that carries %.3f A: "
                            "min_thickness is %s mm against %.3f mm demanded. This "
                            "is NOT a finding that the plane is too thin - the "
                            "corridor width is a shape fact this planner does not "
                            "compute. Measure it (shape/void analysis) or state "
                            "min_thickness >= the demand, which KiCad will honour "
                            "only if the plane can exist at that thickness."
                            % (v["pads_touching"], v["vias_inside"],
                               v["tracks_touching"], lay, demand,
                               v["min_thickness_mm"], v["required_mm"]))})
    elif not thin:
        verdict = "traces_ok"
    elif room is None or (legal_widen is not None
                          and legal_widen + 1e-9 >= max(s["req"] for s in thin)):
        verdict = "widen_traces"
        flags.append("all_traces_below_demand_but_room_to_widen")
        for lay_class in ("external", "internal"):
            req = ext_need if lay_class == "external" else int_need
            n = len([s for s in thin
                     if (_is_external(board, s["layer"])) == (lay_class == "external")])
            if not n:
                continue
            actions.append({"op": "set_track_width", "net": net,
                            "layer_class": lay_class,
                            "from_mm": min(s["w"] for s in thin if
                                           (_is_external(board, s["layer"]))
                                           == (lay_class == "external")),
                            "to_mm": round(req, 4), "segments": n,
                            "legal_up_to_mm": legal_widen})
    else:
        verdict = "pour_needed"
        flags.append("neck_cannot_be_widened")
        if room is not None and room < 0:
            # A negative room is not "no space for wider copper" in the usual
            # sense: it says the existing neck already sits inside the clearance
            # to the thing named in `neck.blocked_by`. That is a DRC's call, not
            # this planner's, so it is reported and the caller is sent there -
            # but only when the clearance came from the board. Against a number
            # this planner ASSUMED (no .kicad_pro), the same geometry is not a
            # finding, it is the assumption showing through, so it says so.
            # A number the CALLER passed is a rule they stated; only "default"
            # means nobody stated one and the planner picked 0.2 mm itself.
            if str(clr_src) == "default":
                flags.append("neck_inside_assumed_clearance")
            else:
                flags.append("neck_already_inside_clearance")
        # Candidates are ranked inner-first: a plane belongs on an inner layer, and
        # offering F.Cu first would send the caller to pour over the signal side.
        inner = [l for l in cu[1:-1]] if len(cu) > 2 else list(cu)
        cands = ([l for l in inner if not (zone_rows.get(l) or [])] +
                 [l for l in inner if (zone_rows.get(l) or [])] +
                 [l for l in cu if l not in inner])
        for lay in cands[:2]:
            req = ext_need if _is_external(board, lay) else int_need
            actions.append({"op": "add_zone", "net": net, "layer": lay,
                            "priority": _next_priority(zone_rows, lay, net),
                            "required_width_mm": round(req, 4),
                            "min_thickness_mm": None,
                            "needs_outline": True,
                            "next_tool": "board_add_zone",
                            "why": "no room to widen the neck (widest legal on it "
                                   "is %s mm), so the copper has to come from a "
                                   "pour. min_thickness stays the zone default: a "
                                   "plane carries current by being WIDE, not by "
                                   "declaring itself thick - what has to be >= "
                                   "%.3f mm is the copper at the neck, which is a "
                                   "shape decision, not a zone parameter"
                                   % (legal_widen, req)})

    why = {
        "no_demand": "no current was given for this net",
        "no_copper": "no tracks, vias, filled plane or pads of this net at all",
        "plane_stale": "a same-layer track of another net lies inside a filled "
                       "polygon: the polygons predate the track",
        "pads_uncovered": "pads of this net touch no copper of this net",
        "covered": "a filled plane states min_thickness >= required width",
        "plane_covered_capacity_unmeasured": (
            "a filled plane reaches every pad of this net, but the file proves "
            "nothing about the copper width that carries the current: its stated "
            "min_thickness is below the demand, and the corridor width (fill "
            "outline minus voids) is not computed here"),
        "traces_ok": "every trace is at least as wide as the demand",
        "widen_traces": "traces are narrower than the demand but the narrowest "
                        "one has room to grow to it",
        "pour_needed": "traces are narrower than the demand and the narrowest "
                       "cannot legally grow to it",
    }[verdict]

    row_flags = list(flags)
    if str(clr_src) == "default":
        # nothing on disk states a clearance: every "room" below is measured
        # against an assumed number and must not be read as a DRC verdict
        row_flags.append("clearance_assumed")

    return {
        "net": net,
        "clearance": {"mm": round(float(clearance_mm), 4), "source": clr_src},
        "demand_a": demand,
        "required_width_mm": need,
        "carrier": ("plane" if plane_area > 1e-9 else
                    ("traces_only" if tracks else "none")),
        "pads": {"total": len(pads), "on_copper": covered_pads,
                 "orphan": orphan},
        "plane": {"area_mm2": plane_area,
                  "layers": plane_layers,
                  "per_layer": plane_ok,
                  "stale_evidence": stale[:4]},
        "trace": {"segments": len(segs),
                  "length_mm": round(sum(math.hypot(s["b"][0] - s["a"][0],
                                                    s["b"][1] - s["a"][1])
                                         for s in segs), 2),
                  "widths_mm": widths,
                  "narrowest_mm": (widths[0] if widths else None),
                  "widest_mm": (widths[-1] if widths else None),
                  "below_demand": len(thin),
                  "below_demand_length_mm": round(thin_len, 2),
                  "widest_legal_mm": legal_widen,
                  "neck": neck},
        "verdict": verdict,
        "why": why,
        "flags": row_flags,
        "actions": actions,
    }


def _pad_on_copper(pd, cu, tracks, vias, plane_polys):
    from ..adapters.canonical import pad_points
    poly = pad_points(pd)
    pads_layers = _pad_layers(pd, cu)
    for lay, plane in plane_polys:
        if pads_layers and lay not in pads_layers:
            continue
        if G.point_in_polygon((pd["x"], pd["y"]), plane):
            return True
    cx, cy = pd["x"], pd["y"]
    r = max(float(pd.get("w") or 0.0), float(pd.get("h") or 0.0)) / 2.0
    for t in tracks:
        if pads_layers and t.get("layer") not in pads_layers:
            continue
        a, b = t.get("start"), t.get("end")
        if not a or not b:
            continue
        if _seg_poly_dist(a, b, poly) <= float(t.get("width") or 0.0) / 2.0 + 1e-9:
            return True
    for v in vias:
        p = v.get("at") or (v.get("x"), v.get("y"))
        if not p or p[0] is None:
            continue
        d = math.hypot(p[0] - cx, p[1] - cy)
        if d <= r + float(v.get("size") or 0.0) / 2.0 + 1e-9:
            return True
    return False


def _next_priority(zone_rows, layer, net):
    """One above everything already on that layer, so the new pour wins."""
    best = 0
    for z in (zone_rows.get(layer) or []):
        if z["net"] == net:
            continue
        best = max(best, int(z["priority"] or 0))
    return best + 1



