"""The house rules a routing obeys, read off a board that already looks right.

`routing_profile` measures shape: how much runs diagonally, how often it changes
layer, how far a track goes before it leaves its pad. On this machine's two boards
those numbers came out level - the F412 core board reads *better* than a
production Jetson carrier on detour ratio (1.089 vs 1.258) and on how far a net
strays from its own pads (1.000 vs 1.013). So shape is not what the eye is
reading. What it is reading is this file:

* one width per role, drawn from a short ladder (a signal 0.15 mm and a 3V3 rail
  at the same width is not a routing mistake, it is a missing decision),
* one via family, or two with a clear split between "signal via" and "stitching",
* turns taken the same way every time,
* and a preferred direction per layer, if the board uses one.

None of that is geometry, and none of it is caught by a DRC: every one of those
556 F412 segments at 0.15 mm is legal. It is a *style* defect, and style can be
extracted from a reference board as numbers, then diffed against the board being
drawn. That is what this module does.

Read 2026-11-07 for the numbers quoted above (see `docs/122`). Nothing here
writes: `fix_list` names the net, the width it has and the width the rule asks
for, and the edit is a `set_track_width` the caller applies one net at a time.
"""
import math


#: A net whose name is on this list is power and is expected to be wider than a
#: signal net. Matched case-insensitively as a substring, so `+3V3`, `3V3A` and
#: `DVDD_3V3` all land here.
POWER_HINTS = ("3v3", "5v", "12v", "1v8", "2v5", "vcc", "vdd", "vbat", "vbus",
               "vref", "vcap", "vin", "pwr", "vsys", "vcore", "vddio", "avdd")
#: Nets whose copper is poured rather than routed. A pour has no width, so a
#: width rule that mentions them is noise and is dropped from the fix list.
POUR_HINTS = ("gnd", "ground", "agnd", "dgnd", "vss", "earth")
#: Two widths closer than this are the same width. KiCad stores millimetres as
#: decimals, so 0.15 and 0.15000000000000002 are one rung of the ladder.
WIDTH_TOL_MM = 0.005
#: A net counts as differential when its name ends in one of these.
DIFF_SUFFIXES = ("_dp", "_dm", "_p", "_n", "dp", "dm", "d+", "d-", "tx+", "tx-",
                 "rx+", "rx-")
#: A turn is counted when the direction changes by at least this much; below it
#: the two records are the same straight run split in two.
TURN_MIN_DEG = 20.0
#: Endpoints closer than this belong to the same node of the net's copper graph.
JOIN_TOL_MM = 0.12


def _median(values):
    values = [v for v in values if v is not None]
    if not values:
        return None
    ordered = sorted(values)
    mid = len(ordered) // 2
    return ordered[mid] if len(ordered) % 2 else (ordered[mid - 1] + ordered[mid]) / 2.0


def _rungs(entries, tol=WIDTH_TOL_MM):
    """Group (value, weight) into rungs, biggest first. Values within `tol` of the
    rung already open are folded into it: a ladder is a decision, not a float
    accident."""
    rungs = []
    for value, weight in sorted(entries, key=lambda e: e[0]):
        if rungs and value - rungs[-1]["value"] <= tol:
            rung = rungs[-1]
            rung["value"] = (rung["value"] * rung["count"] + value * weight) / (rung["count"] + weight)
            rung["count"] += weight
        else:
            rungs.append({"value": value, "count": weight})
    total = sum(r["count"] for r in rungs) or 1
    out = [{"width_mm": round(r["value"], 4), "segments": r["count"],
            "share": round(r["count"] / float(total), 4)} for r in rungs]
    out.sort(key=lambda r: -r["segments"])
    return out


def _point_segment_distance(point, start, end):
    px, py = point
    ax, ay = start
    bx, by = end
    dx, dy = bx - ax, by - ay
    span = dx * dx + dy * dy
    if span < 1e-12:
        return (px - ax) ** 2 + (py - ay) ** 2
    t = max(0.0, min(1.0, ((px - ax) * dx + (py - ay) * dy) / span))
    qx, qy = ax + t * dx, ay + t * dy
    return (px - qx) ** 2 + (py - qy) ** 2

def _segment_distance(a, b, c, d):
    """Smallest centre-to-centre distance between two segments, squared."""
    best = min(_point_segment_distance(a, c, d), _point_segment_distance(b, c, d),
               _point_segment_distance(c, a, b), _point_segment_distance(d, a, b))
    return best ** 0.5

def _point_box_distance(point, cx, cy, hw, hh):
    """Distance from a point to an axis-aligned box, 0 inside it."""
    dx = max(abs(point[0] - cx) - hw, 0.0)
    dy = max(abs(point[1] - cy) - hh, 0.0)
    return (dx * dx + dy * dy) ** 0.5

def _segment_box_distance(a, b, cx, cy, hw, hh):
    """Distance from a segment to an axis-aligned box.

    Exact for the cases that matter: the closest pair between a segment and a
    convex box is always either (segment, corner) or (segment endpoint, edge), and
    both are covered. Rotation is ignored - the pads on the boards this was
    measured on are axis-aligned, and a rotated one would be read as its unrotated
    box.
    """
    corners = [(cx - hw, cy - hh), (cx + hw, cy - hh),
               (cx + hw, cy + hh), (cx - hw, cy + hh)]
    best = min(_point_segment_distance(c, a, b) ** 0.5 for c in corners)
    best = min(best, _point_box_distance(a, cx, cy, hw, hh),
               _point_box_distance(b, cx, cy, hw, hh))
    return best

def _segment_obox_distance(a, b, cx, cy, hw, hh, deg):
    """Distance from a segment to a rectangle rotated by `deg` about its centre.

    Measured need: J1 on the F412 board is a micro-USB socket at 270 degrees, so
    its 0.4 x 1.35 mm pads point along x, not along y. Read as an axis-aligned box
    the pad reaches 0.46 mm further than it really does, and the reachable width
    for VBUS came out at 0.15 against a real 0.30.
    """
    theta = math.radians(deg or 0.0)
    ca, sa = math.cos(theta), math.sin(theta)

    def back(point):
        dx, dy = point[0] - cx, point[1] - cy
        return (dx * ca + dy * sa, -dx * sa + dy * ca)

    return _segment_box_distance(back(a), back(b), 0.0, 0.0, hw, hh)

def clearance_room(board, clearance_mm=None, rows=None, planned_widths=None):
    """How wide each net's copper could get before it breaks clearance.

    For one net growing next to a neighbour of width `wo` at centre distance `d`,
    the edge-to-edge gap is `d - w/2 - wo/2`, so `w <= 2*(d - clearance) - wo`.
    When BOTH nets are being widened the same arithmetic solved symmetrically gives
    `w <= d - clearance`, and both forms are used below (`planned` decides which).

    Measured reason this exists: on the F412 core board the rule set asked for
    0.30 mm on USB_DP/DM; they run as a 0.5 mm-pitch pair whose 45 degree stretch
    sits 0.389 mm apart, so 0.30 mm leaves 0.089 mm against a 0.15 mm rule - seven
    DRC errors and a rolled-back plan. The symmetric bound is 0.239 mm, which is
    why the fix list now caps the pair at 0.20 rather than trying 0.30.

    This is a lower bound on what fits, not a fabrication check: it stops at the
    first neighbour on one layer and does not model drill-to-drill, the board edge,
    or KiCad's own shape refinement (roundrect radius, oval pads).
    """
    from . import routing_profile as rp

    rows = rows if rows is not None else rp._segment_table(board)
    rules = getattr(board, "rules", None) or {}
    if clearance_mm is None:
        clearance_mm = ((rules.get("clearance") or {}).get("min_clearance")
                        or rules.get("min_clearance") or 0.15)
    clearance_mm = float(clearance_mm)
    planned = {k: float(v) for k, v in (planned_widths or {}).items()}

    widths = {}
    for row in rows:
        if row.get("width_mm") is not None:
            widths.setdefault(row["net"], float(row["width_mm"]))

    fixed_pairs = []        # (net, allowed) from pads and vias, independent of width
    track_pairs = []        # (net, other_net, distance)
    for row in rows:
        if row.get("width_mm") is None:
            continue
        for other in rows:
            if other["net"] == row["net"] or other["layer"] != row["layer"]:
                continue
            if other["width_mm"] is None:
                continue
            if (other["net"], row["net"]) < (row["net"], other["net"]):
                continue                    # one row per unordered pair
            distance = _segment_distance(row["start"], row["end"],
                                         other["start"], other["end"])
            if distance > 3.0:              # nothing this far away can bind a width
                continue
            track_pairs.append((row["net"], other["net"], distance))
    for fp in getattr(board, "footprints", []) or []:
        for pad in fp.get("pads") or []:
            if pad.get("net") is None or pad.get("x") is None:
                continue
            layers = [l for l in (pad.get("layers") or []) if l.endswith(".Cu")]
            hw = float(pad.get("w") or 0.0) / 2.0
            hh = float(pad.get("h") or 0.0) / 2.0
            deg = pad.get("rotation")
            if deg is None:
                deg = fp.get("rotation") or 0.0
            tag = "%s.%s" % (fp.get("ref"), pad.get("number"))
            for row in rows:
                if row["net"] == pad["net"] or row.get("width_mm") is None:
                    continue
                if layers and row["layer"] not in layers and "*.Cu" not in layers:
                    continue
                distance = _segment_obox_distance(row["start"], row["end"],
                                                  float(pad["x"]), float(pad["y"]),
                                                  hw, hh, deg)
                if distance > 2.0:
                    continue
                fixed_pairs.append((row["net"], 2.0 * (distance - clearance_mm),
                                    ("pad", tag, distance)))
    for via in getattr(board, "vias", []) or []:
        if via.get("net") is None or via.get("x") is None:
            continue
        radius = float(via.get("diameter") or 0.0) / 2.0
        layers = via.get("layers") or ["F.Cu", "B.Cu"]
        for row in rows:
            if row["net"] == via["net"] or row.get("width_mm") is None:
                continue
            if layers and row["layer"] not in layers and "*.Cu" not in layers:
                continue
            distance = (_point_segment_distance((float(via["x"]), float(via["y"])),
                                                row["start"], row["end"]) ** 0.5
                        - radius)
            if distance > 1.5:
                continue
            fixed_pairs.append((row["net"], 2.0 * (distance - clearance_mm),
                                ("via", "%s at (%.2f, %.2f)" % (via.get("net"),
                                                               float(via["x"]),
                                                               float(via["y"])),
                                 distance)))

    room = {}
    bound_by = {}
    for net in widths:
        limit = None
        binding = None
        for owner, allowed, what in fixed_pairs:
            if owner == net and (limit is None or allowed < limit):
                limit, binding = allowed, what
        for a, b, distance in track_pairs:
            if net not in (a, b):
                continue
            other = b if a == net else a
            other_w = planned.get(other, widths.get(other, 0.0))
            if other in planned and net in planned:
                allowed = distance - clearance_mm
            else:
                allowed = 2.0 * (distance - clearance_mm) - other_w
            if limit is None or allowed < limit:
                limit = allowed
                binding = ("track", other, distance)
        if limit is not None:
            room[net] = max(widths[net], round(limit, 4))
            if binding:
                bound_by[net] = {"kind": binding[0], "neighbour": binding[1],
                                 "centre_distance_mm": round(binding[2], 4),
                                 "allows_mm": round(limit, 4)}
    return {"clearance_mm": clearance_mm,
            "max_width_mm": room,
            "bound_by": bound_by,
            "planned_widths_mm": planned,
            "method": ("segment-to-segment and segment-to-box distances on the same "
                       "layer; a pair of nets that are BOTH being widened uses the "
                       "symmetric bound d - clearance, a single net growing next to a "
                       "fixed neighbour uses 2*(d - clearance) - w_neighbour"),
            "caveat": ("a lower bound on what fits, not a fabrication check: first "
                       "neighbour on one layer, no drill-to-drill, no board edge, "
                       "pads read as their axis-aligned box")}

def segment_room(board, clearance_mm=None, rows=None, planned_widths=None):
    """Per SEGMENT version of :func:`clearance_room`: how wide each one could get.

    A net is not one copper object. The F412 core board's +3V3 runs 65 segments;
    eleven of them sit in a 0.5 mm-pitch fan-out and cannot take 0.50 mm, while the
    other fifty-four can. Answering at the net level therefore loses the widening
    that IS possible: the net gets blocked as a whole and the rail stays at the
    signal width. This answers per segment so the rule can be applied to the copper
    that can take it.

    Same geometry, same bound, same caveat as :func:`clearance_room` - it is the
    first neighbour on one layer, it does not model drill-to-drill or the board
    edge, and pads are read as their axis-aligned box. Nothing here routes.
    """
    from . import routing_profile as rp

    rows = rows if rows is not None else rp._segment_table(board)
    rules = getattr(board, "rules", None) or {}
    if clearance_mm is None:
        clearance_mm = ((rules.get("clearance") or {}).get("min_clearance")
                        or rules.get("min_clearance") or 0.15)
    clearance_mm = float(clearance_mm)
    planned = {k: float(v) for k, v in (planned_widths or {}).items()}

    widths = {}
    for row in rows:
        if row.get("width_mm") is not None:
            widths.setdefault(row["net"], float(row["width_mm"]))

    def keep(i, allowed, binding):
        best = limits.get(i)
        if best is None or allowed < best[0]:
            limits[i] = (allowed, binding)

    limits = {}
    # --- segment against segment, both directions -------------------------
    for i, row in enumerate(rows):
        if row.get("width_mm") is None:
            continue
        for j, other in enumerate(rows):
            if j <= i or other["net"] == row["net"] or other["layer"] != row["layer"]:
                continue
            if other.get("width_mm") is None:
                continue
            distance = _segment_distance(row["start"], row["end"],
                                         other["start"], other["end"])
            if distance > 3.0:
                continue
            if row["net"] in planned and other["net"] in planned:
                allowed = distance - clearance_mm
            else:
                other_w = planned.get(other["net"], widths.get(other["net"], 0.0))
                allowed = 2.0 * (distance - clearance_mm) - other_w
            keep(i, allowed, ("track", other["net"], distance))
            # the neighbour may grow too; its own bound is its own to work out
            if other["net"] in planned:
                row_w = planned.get(row["net"], widths.get(row["net"], 0.0))
                keep(j, 2.0 * (distance - clearance_mm) - row_w,
                     ("track", row["net"], distance))
    # --- segment against pads and vias ------------------------------------
    for fp in getattr(board, "footprints", []) or []:
        for pad in fp.get("pads") or []:
            if pad.get("net") is None or pad.get("x") is None:
                continue
            layers = [l for l in (pad.get("layers") or []) if l.endswith(".Cu")]
            hw = float(pad.get("w") or 0.0) / 2.0
            hh = float(pad.get("h") or 0.0) / 2.0
            deg = pad.get("rotation")
            if deg is None:
                deg = fp.get("rotation") or 0.0
            tag = "%s.%s" % (fp.get("ref"), pad.get("number"))
            for i, row in enumerate(rows):
                if row["net"] == pad["net"] or row.get("width_mm") is None:
                    continue
                if layers and row["layer"] not in layers and "*.Cu" not in layers:
                    continue
                distance = _segment_obox_distance(row["start"], row["end"],
                                                  float(pad["x"]), float(pad["y"]),
                                                  hw, hh, deg)
                if distance > 2.0:
                    continue
                keep(i, 2.0 * (distance - clearance_mm), ("pad", tag, distance))
    for via in getattr(board, "vias", []) or []:
        if via.get("net") is None or via.get("x") is None:
            continue
        radius = float(via.get("diameter") or 0.0) / 2.0
        layers = via.get("layers") or ["F.Cu", "B.Cu"]
        for i, row in enumerate(rows):
            if row["net"] == via["net"] or row.get("width_mm") is None:
                continue
            if layers and row["layer"] not in layers and "*.Cu" not in layers:
                continue
            distance = (_point_segment_distance((float(via["x"]), float(via["y"])),
                                                row["start"], row["end"]) ** 0.5
                        - radius)
            if distance > 1.5:
                continue
            keep(i, 2.0 * (distance - clearance_mm),
                 ("via", "%s at (%.2f, %.2f)" % (via.get("net"), float(via["x"]),
                                                float(via["y"])), distance))

    out = {}
    for i, row in enumerate(rows):
        if row.get("width_mm") is None or not row.get("uuid"):
            continue
        current = float(row["width_mm"])
        if i in limits:
            allowed, binding = limits[i]
            out[row["uuid"]] = {
                "net": row["net"], "layer": row["layer"], "current_mm": current,
                "max_width_mm": max(current, round(allowed, 4)),
                "bound_by": {"kind": binding[0], "neighbour": binding[1],
                             "centre_distance_mm": round(binding[2], 4),
                             "allows_mm": round(allowed, 4)},
            }
        else:
            out[row["uuid"]] = {"net": row["net"], "layer": row["layer"],
                                "current_mm": current, "max_width_mm": None,
                                "bound_by": None}
    return {"clearance_mm": clearance_mm,
            "segments": len(out),
            "by_uuid": out,
            "method": ("identical distances to clearance_room, kept per segment "
                       "instead of collapsed per net"),
            "caveat": ("a lower bound on what fits: first neighbour on one layer, "
                       "no drill-to-drill, no board edge, pads as axis-aligned boxes")}

def _track_points(track):
    """A track's copper centreline as a polyline.

    KiCad 10 writes a rounded corner or a tuned bump as `(arc)`, and 862 of the
    sky_star board's copper objects are 157 arcs. Read as the chord (start to end
    only) an arc's distance to a neighbour is wrong in both directions, so the
    per-it geometry below walks the sampled points the adapter already keeps.
    """
    pts = track.get("_points")
    if pts and len(pts) >= 2:
        return [(float(p[0]), float(p[1])) for p in pts]
    return [(float(track["start"][0]), float(track["start"][1])),
            (float(track["end"][0]), float(track["end"][1]))]

def _clearance_of(board, net):
    """The clearance the project itself assigns to this net, else the board floor."""
    rules = getattr(board, "rules", None) or {}
    classes = rules.get("net_classes") or {}
    assign = rules.get("netclass_assignments") or {}
    for name in assign.get(net) or []:
        value = (classes.get(name) or {}).get("clearance")
        if value is not None:
            return float(value)
    return float((rules.get("clearance") or {}).get("min_clearance") or 0.15)

def via_room(board, clearance_mm=None):
    """Per VIA version of :func:`segment_room`: how large each one could get.

    Why it is not a single board-wide number: this repository's own sky_star
    board has 201 vias, all 0.45/0.25. Eight of them sit in a 0.60 mm fan-out
    where the copper gap at 0.45 mm is already exactly the 0.15 mm the project
    asks for, so 0.50 mm is not available there while it is everywhere else.
    A board-wide "vias are 0.60" rule would either be refused by DRC or, worse,
    written and shipped. The annular rule couples the two numbers - KiCad asks
    `(size - drill) / 2 >= min_via_annular_width`, so a larger drill needs a
    larger size - while the hole-to-hole rule caps the drill on its own and
    does not care about nets at all.

    Nothing here writes; it answers "how big could this one be and what
    stopped it". Zones are deliberately not a constraint: the filler rebuilds
    them around foreign copper, which is why widening a track only collides
    with a pour until `--refill-zones` runs.
    """
    rules = getattr(board, "rules", None) or {}
    via_rules = rules.get("via") or {}
    if clearance_mm is None:
        clearance_mm = (rules.get("clearance") or {}).get("min_clearance") or 0.15
    clearance_mm = float(clearance_mm)
    min_annular = float(via_rules.get("min_via_annular_width") or 0.0)
    min_hole_gap = float(via_rules.get("min_hole_to_hole") or 0.0)
    min_size = float(via_rules.get("min_via_diameter") or 0.0)

    vias = [v for v in (getattr(board, "vias", None) or [])
            if v.get("x") is not None and v.get("diameter")]
    size_limits, drill_limits = {}, {}

    def keep(table, v, allowed, binding):
        best = table.get(id(v))
        if best is None or allowed < best[0]:
            table[id(v)] = (allowed, binding)

    def label(other):
        return ("%s at (%.2f, %.2f)" % (other.get("net"), float(other["x"]),
                                        float(other["y"])))

    # --- via against via: copper gap, and hole-to-hole (net-independent) ---
    for i, a in enumerate(vias):
        for b in vias[i + 1:]:
            d = math.hypot(float(a["x"]) - float(b["x"]),
                           float(a["y"]) - float(b["y"]))
            if d > 3.0:
                continue
            if a.get("net") != b.get("net"):
                clearance = max(clearance_mm, _clearance_of(board, a.get("net")),
                                _clearance_of(board, b.get("net")))
                for me, other in ((a, b), (b, a)):
                    allowed = 2.0 * (d - float(other["diameter"]) / 2.0 - clearance)
                    keep(size_limits, me, allowed,
                         ("via", label(other), round(d, 4)))
            for me, other in ((a, b), (b, a)):
                allowed = 2.0 * (d - float(other["drill"]) / 2.0 - min_hole_gap)
                keep(drill_limits, me, allowed,
                     ("via_hole", label(other), round(d, 4)))

    # --- via against foreign tracks ---------------------------------------
    for v in vias:
        net = v.get("net")
        layers = v.get("layers") or ["F.Cu", "B.Cu"]
        for t in getattr(board, "tracks", None) or []:
            if t.get("net") == net or t.get("layer") not in layers:
                continue
            pts = _track_points(t)
            d = min(_point_segment_distance((float(v["x"]), float(v["y"]),),
                                            p, q) ** 0.5
                    for p, q in zip(pts, pts[1:]))
            if d > 3.0:
                continue
            clearance = max(clearance_mm, _clearance_of(board, net),
                            _clearance_of(board, t.get("net")))
            allowed = 2.0 * (d - float(t.get("width") or 0.0) / 2.0 - clearance)
            keep(size_limits, v, allowed,
                 ("track", t.get("net"),
                  round(d - float(t.get("width") or 0.0) / 2.0, 4)))

    # --- via against foreign pads -----------------------------------------
    for fp in getattr(board, "footprints", None) or []:
        for pad in fp.get("pads") or []:
            if pad.get("net") is None or pad.get("x") is None:
                continue
            layers = [l for l in (pad.get("layers") or []) if l.endswith(".Cu")]
            hw = float(pad.get("w") or 0.0) / 2.0
            hh = float(pad.get("h") or 0.0) / 2.0
            deg = pad.get("rotation")
            if deg is None:
                deg = fp.get("rotation") or 0.0
            tag = "%s.%s" % (fp.get("ref"), pad.get("number"))
            for v in vias:
                if v.get("net") == pad.get("net"):
                    continue
                vlayers = v.get("layers") or ["F.Cu", "B.Cu"]
                if layers and not set(layers) & set(vlayers) and "*.Cu" not in layers:
                    continue
                here = (float(v["x"]), float(v["y"]))
                d = _segment_obox_distance(here, here, float(pad["x"]),
                                           float(pad["y"]), hw, hh, deg)
                if d > 3.0:
                    continue
                clearance = max(clearance_mm, _clearance_of(board, v.get("net")),
                                _clearance_of(board, pad.get("net")))
                keep(size_limits, v, 2.0 * (d - clearance),
                     ("pad", tag, round(d, 4)))
                if pad.get("hole_mm"):
                    centre = math.hypot(float(v["x"]) - float(pad["x"]),
                                        float(v["y"]) - float(pad["y"]))
                    allowed = 2.0 * (centre - float(pad["hole_mm"]) / 2.0
                                     - min_hole_gap)
                    if allowed < float(v["drill"]):
                        keep(drill_limits, v, allowed,
                             ("pad_hole", tag,
                              round(centre - float(pad["hole_mm"]) / 2.0, 4)))

    out = {}
    for idx, v in enumerate(vias):
        current_size = float(v["diameter"])
        current_drill = float(v["drill"])
        entry = {"net": v.get("net"),
                 "at": [round(float(v["x"]), 3), round(float(v["y"]), 3)],
                 "current_size_mm": current_size,
                 "current_drill_mm": current_drill,
                 "max_size_mm": None, "max_drill_mm": None,
                 "bound_by": None, "drill_bound_by": None}
        copper = size_limits.get(id(v))
        hole = drill_limits.get(id(v))
        if copper is not None:
            allowed, binding = copper
            # a via never shrinks: what fits is a ceiling, and the current size
            # is already on the board. Never report a smaller number than the
            # copper that exists, or `max < current` reads as "must shrink".
            entry["max_size_mm"] = max(current_size, min_size, round(allowed, 4))
            entry["bound_by"] = {"kind": binding[0], "neighbour": binding[1],
                                 "allows_mm": round(allowed, 4)}
        if hole is not None:
            allowed, binding = hole
            entry["max_drill_mm"] = max(current_drill, round(allowed, 4))
            entry["drill_bound_by"] = {"kind": binding[0], "neighbour": binding[1],
                                       "allows_mm": round(allowed, 4)}
        # the annular rule is the one bound that depends on both numbers, so
        # it is reported as its own field rather than folded into either.
        size_for = entry["max_size_mm"] if entry["max_size_mm"] is not None \
            else current_size
        entry["drill_ceiling_from_annular_mm"] = round(size_for - 2.0 * min_annular, 4)
        out[v.get("uuid") or "index-%d" % idx] = entry
    return {"clearance_mm": clearance_mm,
            "min_via_annular_width_mm": min_annular,
            "min_hole_to_hole_mm": min_hole_gap,
            "vias": len(out),
            "by_uuid": out,
            "method": ("nearest foreign copper on a shared layer, plus the "
                       "hole-to-hole rule that does not care about nets"),
            "caveat": ("a lower bound: first neighbour only, no board edge, no "
                       "mask/paste, pads as rotated boxes, zones left to the filler")}

def role_of(net):
    """Power, differential or signal - read off the net name, nothing else.

    Returns `pour` for a ground net, because a pour has no width and a width rule
    for it would be a rule about nothing."""
    name = (net or "").strip().lower()
    if not name:
        return "unassigned"
    for hint in POUR_HINTS:
        if hint in name:
            return "pour"
    for hint in POWER_HINTS:
        if hint in name:
            return "power"
    for suffix in DIFF_SUFFIXES:
        if name.endswith(suffix):
            return "differential"
    return "signal"


def _turns(rows):
    """Every place two segments of one net meet, as the angle they turn through.

    The graph is the net's endpoints bucketed at JOIN_TOL_MM; a node with two
    segments is one turn. Nodes with three or more segments (a tee into a pad, a
    via landing on a track) are counted once per pair and flagged through
    `junctions`, because a tee is not a bend and averaging it with one hides the
    difference."""
    by_net = {}
    for row in rows:
        by_net.setdefault(row["net"], []).append(row)
    deltas = []
    junctions = 0
    for segs in by_net.values():
        buckets = {}
        for index, row in enumerate(segs):
            for end in ("start", "end"):
                key = (round(row[end][0] / JOIN_TOL_MM), round(row[end][1] / JOIN_TOL_MM))
                buckets.setdefault(key, []).append(index)
        seen = set()
        for members in buckets.values():
            if len(members) > 2:
                junctions += 1
            for i in range(len(members)):
                for j in range(i + 1, len(members)):
                    pair = (min(members[i], members[j]), max(members[i], members[j]))
                    if pair in seen:
                        continue
                    seen.add(pair)
                    delta = abs(segs[pair[0]]["angle_deg"] - segs[pair[1]]["angle_deg"]) % 180.0
                    if delta > 90.0:
                        delta = 180.0 - delta
                    if delta >= TURN_MIN_DEG:
                        deltas.append(delta)
    return deltas, junctions


def style(board):
    """One board's house style. Absolute numbers, no comparison."""
    from . import routing_profile as rp

    rows = rp._segment_table(board)
    per_net_width = {}
    for row in rows:
        width = row.get("width_mm")
        if width is None:
            continue
        per_net_width.setdefault(row["net"], set()).add(round(float(width), 4))
    ladder = _rungs([(float(r["width_mm"]), 1) for r in rows if r.get("width_mm") is not None])

    roles = {}
    for net, widths in per_net_width.items():
        role = role_of(net)
        entry = roles.setdefault(role, {"nets": 0, "widths": set(), "median": []})
        entry["nets"] += 1
        entry["widths"].update(widths)
        entry["median"].append(_median(sorted(widths)))
    by_role = {}
    for role, entry in sorted(roles.items()):
        by_role[role] = {"nets": entry["nets"],
                         "widths_mm": sorted(entry["widths"]),
                         "median_width_mm": round(_median(entry["median"]), 4)
                         if entry["median"] else None}

    vias = {}
    for via in getattr(board, "vias", []) or []:
        drill = via.get("drill")
        dia = via.get("diameter")
        if drill is None or dia is None:
            continue
        key = (round(float(drill), 4), round(float(dia), 4))
        vias[key] = vias.get(key, 0) + 1
    via_total = sum(vias.values()) or 1
    via_ladder = sorted(({"drill_mm": k[0], "diameter_mm": k[1], "count": v,
                          "share": round(v / float(via_total), 4)}
                         for k, v in vias.items()), key=lambda e: -e["count"])

    deltas, junctions = _turns(rows)
    share_45 = (sum(1 for d in deltas if abs(d - 45.0) <= 1.0) / float(len(deltas))
                if deltas else None)
    share_90 = (sum(1 for d in deltas if abs(d - 90.0) <= 1.0) / float(len(deltas))
                if deltas else None)

    return {
        "board": (getattr(board, "source", None) or {}).get("path"),
        "segments": len(rows),
        "width": {
            "segments_with_width": len([r for r in rows if r.get("width_mm") is not None]),
            "distinct": len(ladder),
            "dominant_mm": ladder[0]["width_mm"] if ladder else None,
            "dominant_share": ladder[0]["share"] if ladder else None,
            "ladder": ladder,
        },
        "by_role": by_role,
        "vias": {"count": via_total, "families": len(via_ladder),
                 "dominant_share": via_ladder[0]["share"] if via_ladder else None,
                 "ladder": via_ladder},
        "corners": {"turns": len(deltas),
                    "share_turning_45": round(share_45, 4) if share_45 is not None else None,
                    "share_turning_90": round(share_90, 4) if share_90 is not None else None,
                    "junctions": junctions,
                    "median_turn_deg": round(_median(deltas), 2) if deltas else None},
    }


def rules(styles, baseline_width_mm=None, ladder_step_mm=0.05):
    """The consensus of one or more reference styles, as rules to apply.

    Two ways to read a reference, and they are not the same thing:

    * the reference's own millimetres (`absolute`), which are sized for that board
      and its fab - a 0.10 mm signal on a 230 mm carrier is not a width for a 53 mm
      board with 0.15 mm minimums;
    * the reference's *shape* scaled onto this board's baseline (`scaled`, what
      `width_by_role` reports when `baseline_width_mm` is given): keep the board's
      own signal width, and set each role by the reference's ratio to its own
      signal width. Ratios travel between boards; millimetres do not.

    `confidence` is `one board` unless several references agree, because one board
    is a sample of one. The arithmetic is printed under `scaled_from` so a caller
    can check it without re-reading this file.
    """
    boards = [s for s in (styles or []) if s]
    if not boards:
        return {"boards": 0, "confidence": "none",
                "why": "no reference style was given: nothing to copy"}
    absolute = {}
    for role in ("power", "differential", "signal"):
        values = [(s.get("by_role") or {}).get(role, {}).get("median_width_mm")
                  for s in boards]
        values = [v for v in values if v]
        if values:
            absolute[role] = round(_median(values), 4)
    families = [s["vias"]["families"] for s in boards if (s.get("vias") or {}).get("families")]
    used = [s for s in boards if (s.get("width") or {}).get("distinct")]
    out = {
        "boards": len(boards),
        "absolute": absolute,
        "width_rungs": {"min": min((s["width"]["distinct"] for s in used), default=None),
                        "max": max((s["width"]["distinct"] for s in used), default=None)},
        "via_families": min(families) if families else None,
        "corner": {"share_turning_45": _median([(s.get("corners") or {}).get("share_turning_45")
                                                for s in boards])},
        "confidence": "one board" if len(boards) == 1 else "consensus of %d" % len(boards),
        "note": ("a reference is evidence about one design, not about what is correct; "
                 "`absolute` copies that board's millimetres, `width_by_role` scales "
                 "its ratios onto this board"),
    }
    ref_signal = absolute.get("signal")
    width_by_role = dict(absolute)
    if baseline_width_mm and ref_signal:
        scaled = {"signal": round(float(baseline_width_mm), 4)}
        for role, value in absolute.items():
            if role == "signal":
                continue
            ratio = value / ref_signal
            raw = float(baseline_width_mm) * ratio
            snapped = round(raw / float(ladder_step_mm)) * float(ladder_step_mm)
            scaled[role] = round(max(float(baseline_width_mm), snapped), 4)
        width_by_role = scaled
        out["scaled_from"] = {"baseline_width_mm": round(float(baseline_width_mm), 4),
                             "reference_signal_mm": ref_signal,
                             "ratios": {r: round(v / ref_signal, 4)
                                        for r, v in absolute.items() if r != "signal"},
                             "ladder_step_mm": ladder_step_mm}
    out["width_by_role"] = width_by_role
    return out


def diffs(board, rule_set, clearance_mm=None, cap=True):
    """Where this board departs from the rule set, net by net.

    One row per (net, role, current width). `target_width_mm` is the rule set's
    width for that role; `pour` nets are skipped because a pour has no width, and
    a net already at the rule set's width is skipped because there is nothing to
    do. The rows are the `set_track_width` calls, in order of how much copper they
    touch - the widest rail first, which is also the one that reads as wrong.

    With `cap` (the default) the target is the smaller of the rule set's width and
    `clearance_room`, and a row that got cut says so under `capped_by_clearance`:
    the width the rule wants may simply not fit next to the neighbours this board
    already has, and finding that out from a rolled-back DRC gate is a bad way to
    learn it.
    """
    from . import routing_profile as rp

    rows = rp._segment_table(board)
    per_net = {}
    for row in rows:
        if row.get("width_mm") is None:
            continue
        entry = per_net.setdefault(row["net"], {"widths": set(), "segments": 0,
                                                "length_mm": 0.0})
        entry["widths"].add(round(float(row["width_mm"]), 4))
        entry["segments"] += 1
        entry["length_mm"] += row["length_mm"]
    wanted = (rule_set or {}).get("width_by_role") or {}
    wanted = {k: round(float(v), 4) for k, v in wanted.items()}
    current_of = {net: max(entry["widths"]) for net, entry in per_net.items()}
    wanted_of = {net: wanted.get(role_of(net)) for net in per_net}
    planned = {net: target for net, target in wanted_of.items()
               if target and target > current_of.get(net, 0) + WIDTH_TOL_MM}
    if cap:
        room = clearance_room(board, clearance_mm=clearance_mm, rows=rows,
                              planned_widths=planned)
        # a second pass with the widths the first pass settled on: the symmetric
        # bound changes when a neighbour that was going to grow no longer does
        room = clearance_room(board, clearance_mm=clearance_mm, rows=rows,
                              planned_widths={
                                  net: min(planned.get(net, current_of[net]),
                                           room["max_width_mm"].get(net, planned.get(net, 0)))
                                  for net in planned})
    else:
        room = None
    room_mm = dict((room or {}).get("max_width_mm") or {})
    # a differential pair is ONE decision: if either side has no room, widening the
    # other makes the pair unequal, which is worse than leaving both alone
    pairs = {}
    for net in per_net:
        if role_of(net) == "differential":
            base = net.lower()
            for suffix in DIFF_SUFFIXES:
                if base.endswith(suffix):
                    base = base[: -len(suffix)]
                    break
            pairs.setdefault(base, []).append(net)
    pair_capped = []
    for base, members in pairs.items():
        if len(members) < 2:
            continue
        tightest = min(room_mm.get(n, 0.0) for n in members)
        for net in members:
            if room_mm.get(net, 0.0) > tightest:
                pair_capped.append({"pair": members, "net": net,
                                    "fits_mm": round(tightest, 4)})
                room_mm[net] = tightest
    fix, already, capped, blocked = [], 0, [], []
    for net, entry in per_net.items():
        role = role_of(net)
        if role == "pour":
            continue
        target = wanted.get(role)
        if target is None:
            continue
        current = sorted(entry["widths"])
        if len(current) == 1 and abs(current[0] - target) <= WIDTH_TOL_MM:
            already += 1
            continue
        asked = target
        fits = room_mm.get(net)
        if fits is not None and fits < target:
            target = max(current[-1], round(fits / 0.05) * 0.05)
            if target < asked:
                capped.append({"net": net, "asked_mm": asked,
                               "fits_mm": round(fits, 4), "suggested_mm": target})
        if target <= current[-1] + WIDTH_TOL_MM and len(current) == 1:
            blocked.append({"net": net, "role": role, "asked_mm": asked,
                            "fits_mm": round(fits, 4) if fits is not None else None,
                            "why": ("the rule's width does not fit next to this net's "
                                    "neighbours at the board's own clearance; the "
                                    "neighbours have to move first")})
            continue
        fix.append({"net": net, "role": role, "current_widths_mm": current,
                    "target_width_mm": round(target, 4),
                    "capped_by_clearance": bool(capped and capped[-1]["net"] == net),
                    "segments": entry["segments"],
                    "length_mm": round(entry["length_mm"], 2),
                    "op": {"op": "set_track_width", "width": round(target, 4),
                           "net": net}})
    fix.sort(key=lambda r: -r["length_mm"])
    return {
        "board": (getattr(board, "source", None) or {}).get("path"),
        "nets_considered": len(per_net),
        "nets_already_right": already,
        "nets_to_change": len(fix),
        "fix": fix,
        "clearance_room": room,
        "capped": capped,
        "blocked": blocked,
        "pair_capped": pair_capped,
        "skipped": {"pour": sorted(n for n in per_net if role_of(n) == "pour"),
                    "no_rule_for_role": sorted({role_of(n) for n in per_net} - set(wanted) - {"pour"})},
        "note": ("these are edits, not opinions: each row is one set_track_width. "
                 "Nothing is applied here - a width change can break clearance, so it "
                 "goes through board_apply with its DRC diff gate like any other op."),
    }
