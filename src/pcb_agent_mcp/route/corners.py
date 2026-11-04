"""Corners: measure the turns a board already has, and miter the right ones.

Why this module exists at all
-----------------------------
Every other geometry module here measures *distance*: clearance, edge, annular
ring. Nothing measured *angle*, so a board whose every turn was 90 degrees scored
a clean bill of health - clearance was fine, DRC was fine, and the copper still
looked like graph paper. That is a hole in the checks, not a matter of taste:

* the industry convention (and IPC-2221's recommendation) is that signal turns are
  45 degree mitres or arcs, never a bare 90 degree step;
* an ACUTE corner is a real defect - the etchant cannot refresh inside the wedge
  and the copper is over-etched there (the "acid trap");
* at 1 oz copper and a few amps a corner is also a stress and current-density
  concentration point.

What is measured here
---------------------
* ``off45``: a segment whose direction is not within `tolerance_deg` of a multiple
  of 45 degrees. This is the same reading as `experience.observe(
  "tracks_off_45_grid")`, kept separate so the audit can be called without the
  experience ledger.
* a **corner**: two and only two segments of the same net and layer that share an
  endpoint. Its ``turn_deg`` is the angle between them as seen from the vertex:
  180 is a straight run, 135 is a proper 45 degree mitre, 90 is the right angle
  this module removes, below 90 is an acute corner and is reported, never fixed.

And what is not
---------------
A vertex with three or more segments is a **junction** (a T or a star). It is
reported and skipped: a T has no single "corner" to cut, and guessing which leg
is the trunk is exactly the kind of decision that belongs to a human.
"""
import collections
import math

# A vertex is shared when the two segment ends agree to a hundredth of a micron.
# Anything coarser starts joining segments that merely pass near each other.
_VERTEX_DECIMALS = 4
_EPS = 1e-9


def _vkey(point):
    return (round(float(point[0]), _VERTEX_DECIMALS),
            round(float(point[1]), _VERTEX_DECIMALS))


def _sub(a, b):
    return (a[0] - b[0], a[1] - b[1])


def _len(v):
    return math.hypot(v[0], v[1])


def _unit(v, length=None):
    n = _len(v) if length is None else length
    if n <= _EPS:
        return None
    return (v[0] / n, v[1] / n)


def distances(a, b):
    return math.hypot(a[0] - b[0], a[1] - b[1])


def segment_off45(start, end, tolerance_deg=0.5):
    """Degrees this segment sits away from the nearest multiple of 45.

    Returns ``None`` for a zero-length segment: it has no direction, and calling
    that "0 degrees off" would be a made-up answer.
    """
    dx, dy = end[0] - start[0], end[1] - start[1]
    if abs(dx) <= _EPS and abs(dy) <= _EPS:
        return None
    angle = math.degrees(math.atan2(dy, dx)) % 180.0
    return angle - round(angle / 45.0) * 45.0


def _segments(board):
    """The board's straight copper as (index, track). Arcs are left out.

    An arc has a start and an end but no single direction, so measuring it on the
    45 degree grid would be measuring the chord. It is reported as `arcs_skipped`.
    """
    tracks = getattr(board, "tracks", None) or []
    out, arcs = [], 0
    for i, t in enumerate(tracks):
        if t.get("_arc"):
            arcs += 1
            continue
        if not t.get("start") or not t.get("end"):
            continue
        out.append((i, t))
    return out, arcs


def survey(board, tolerance_deg=0.5):
    """Read every 45-degree deviation and every corner on `board`.

    Read-only. The answer says what is on the board; it never says the board is
    wrong - a 90 degree corner on a 100 kHz LED net is not a defect, it is a
    corner. The defect claim is only ever made about acute corners.
    """
    tol = float(tolerance_deg)
    segs, arcs = _segments(board)

    off, devs = 0, []
    zero = 0
    for _i, t in segs:
        dev = segment_off45(t["start"], t["end"], tol)
        if dev is None:
            zero += 1
            continue
        if abs(dev) > tol:
            off += 1
            devs.append(abs(dev))
    devs.sort()

    ends = collections.defaultdict(list)
    for i, t in segs:
        ends[(t.get("net"), t.get("layer")) + _vkey(t["start"])].append((i, 0))
        ends[(t.get("net"), t.get("layer")) + _vkey(t["end"])].append((i, 1))

    by_uuid = {t.get("uuid"): t for _i, t in segs}
    corners, junctions = [], []
    for key, bucket in ends.items():
        if len(bucket) < 2:
            continue
        net, layer = key[0], key[1]
        vertex = (key[2], key[3])
        if len(bucket) > 2:
            junctions.append({"net": net, "layer": layer,
                              "vertex_mm": [round(vertex[0], 4), round(vertex[1], 4)],
                              "segments": len(bucket)})
            continue
        (ia, ea), (ib, eb) = bucket
        if ia == ib:
            # A zero-length loop: both ends on the same segment. No corner.
            continue
        ta, tb = by_uuid_id(segs, ia), by_uuid_id(segs, ib)
        oa = ta["end"] if ea == 0 else ta["start"]
        ob = tb["end"] if eb == 0 else tb["start"]
        da, db = _sub(oa, vertex), _sub(ob, vertex)
        la, lb = _len(da), _len(db)
        ua, ub = _unit(da, la), _unit(db, lb)
        if ua is None or ub is None:
            continue
        dot = max(-1.0, min(1.0, ua[0] * ub[0] + ua[1] * ub[1]))
        turn = math.degrees(math.acos(dot))
        if abs(turn - 180.0) <= tol:
            kind = "straight"
        elif abs(turn - 135.0) <= tol:
            kind = "miter45"
        elif abs(turn - 90.0) <= tol:
            kind = "right"
        elif turn < 90.0 - tol:
            kind = "acute"
        else:
            kind = "other"
        corners.append({
            "kind": kind,
            "net": net,
            "layer": layer,
            "vertex_mm": [round(vertex[0], 4), round(vertex[1], 4)],
            "turn_deg": round(turn, 4),
            "leg_a_mm": round(la, 4),
            "leg_b_mm": round(lb, 4),
            "uuid_a": ta.get("uuid"),
            "uuid_b": tb.get("uuid"),
            "leg_a_end": "start" if ea == 1 else "end",
            "leg_b_end": "start" if eb == 1 else "end",
            "_vertex": vertex,
            "_ua": ua,
            "_ub": ub,
            "_index_a": ia,
            "_index_b": ib,
            "_end_a": ea,
            "_end_b": eb,
        })

    counts = collections.Counter(c["kind"] for c in corners)
    right = [c for c in corners if c["kind"] == "right"]
    right.sort(key=lambda c: -min(c["leg_a_mm"], c["leg_b_mm"]))
    return {
        "tolerance_deg": tol,
        "segments": len(segs),
        "arcs_skipped": arcs,
        "zero_length": zero,
        "off45_segments": off,
        "off45_worst_deg": round(max(devs), 4) if devs else None,
        "off45_median_deg": round(devs[len(devs) // 2], 4) if devs else None,
        "corners": len(corners),
        "corner_kinds": dict(counts),
        "right_angle_corners": len(right),
        "acute_corners": counts.get("acute", 0),
        "junctions": len(junctions),
        "right_angle_shortlist": [_public(c) for c in right[:20]],
        "_right_all": right,
        "junction_shortlist": junctions[:10],
    }


def by_uuid_id(segs, index):
    for i, t in segs:
        if i == index:
            return t
    raise KeyError(index)


def _public(corner):
    return {k: v for k, v in corner.items() if not k.startswith("_")}


def _pads_and_vias(board):
    """Pad and via footprints as (net, x, y, half_w, half_h). Rotated pads use the
    larger half-size on both axes: over-covering a pad only ever makes this check
    refuse more, and refusing a cut at a pad is the safe direction."""
    pads = []
    for p in (getattr(board, "pads", None) and board.pads() or []):
        w = float(p.get("w") or 0.0)
        h = float(p.get("h") or 0.0)
        if p.get("rotation") in (90.0, 270.0):
            w, h = h, w
        pads.append((p.get("net"), float(p.get("x") or 0.0), float(p.get("y") or 0.0),
                     max(w, h) / 2.0))
    vias = []
    for v in (getattr(board, "vias", None) or []):
        d = float(v.get("diameter") or 0.0)
        at = v.get("start") or v.get("at") or (v.get("x"), v.get("y"))
        if at and at[0] is not None:
            vias.append((v.get("net"), float(at[0]), float(at[1]), max(d / 2.0, 0.3)))
    return pads, vias


def _vertex_sits_on(vertex, pads, vias, tol_mm=0.05):
    """Why this vertex may not be trimmed: it is somebody's copper, not a bend."""
    x, y = vertex
    for net, px, py, r in pads:
        if abs(x - px) <= r + tol_mm and abs(y - py) <= r + tol_mm:
            return "vertex_on_pad"
    for net, vx, vy, r in vias:
        if abs(x - vx) <= r + tol_mm and abs(y - vy) <= r + tol_mm:
            return "vertex_on_via"
    return None



def _dist_point_seg(p, a, b):
    ax, ay = a
    bx, by = b
    dx, dy = bx - ax, by - ay
    L2 = dx * dx + dy * dy
    if L2 <= _EPS:
        return math.hypot(p[0] - ax, p[1] - ay)
    t = ((p[0] - ax) * dx + (p[1] - ay) * dy) / L2
    t = max(0.0, min(1.0, t))
    return math.hypot(p[0] - (ax + t * dx), p[1] - (ay + t * dy))


def _seg_cross(a, b, c, d):
    def orient(p, q, r):
        return (q[0] - p[0]) * (r[1] - p[1]) - (q[1] - p[1]) * (r[0] - p[0])
    d1, d2 = orient(c, d, a), orient(c, d, b)
    d3, d4 = orient(a, b, c), orient(a, b, d)
    return ((d1 > 0) != (d2 > 0)) and ((d3 > 0) != (d4 > 0))


def _dist_seg_seg(a, b, c, d):
    if _seg_cross(a, b, c, d):
        return 0.0
    return min(_dist_point_seg(a, c, d), _dist_point_seg(b, c, d),
               _dist_point_seg(c, a, b), _dist_point_seg(d, a, b))


def _inside_rect(p, cx, cy, hw, hh):
    return abs(p[0] - cx) <= hw and abs(p[1] - cy) <= hh


def _dist_seg_rect(a, b, cx, cy, hw, hh):
    if _inside_rect(a, cx, cy, hw, hh) or _inside_rect(b, cx, cy, hw, hh):
        return 0.0
    corners = [(cx - hw, cy - hh), (cx + hw, cy - hh),
               (cx + hw, cy + hh), (cx - hw, cy + hh)]
    best = min(_dist_point_seg(c, a, b) for c in corners)
    for i in range(4):
        best = min(best, _dist_seg_seg(a, b, corners[i], corners[(i + 1) % 4]))
    return best


def _obstacles(board):
    """Everything a new diagonal could hit, with the net it belongs to.

    The diagonal is not part of the board yet, so it cannot be asked about by
    identity - it has to be measured against the copper that is already there.
    """
    segs = []
    for t in (getattr(board, "tracks", None) or []):
        if t.get("_arc") or not t.get("start") or not t.get("end"):
            continue
        segs.append((t.get("net"), t.get("layer"), tuple(t["start"]),
                     tuple(t["end"]), float(t.get("width") or 0.0)))
    rects = []
    for p in (getattr(board, "pads", None) and board.pads() or []):
        w, h = float(p.get("w") or 0.0), float(p.get("h") or 0.0)
        if float(p.get("rotation") or 0.0) in (90.0, 270.0):
            w, h = h, w
        layers = p.get("layers") or []
        rects.append((p.get("net"), layers, float(p.get("x") or 0.0),
                      float(p.get("y") or 0.0), w / 2.0, h / 2.0))
    vias = []
    for v in (getattr(board, "vias", None) or []):
        at = v.get("start") or v.get("at")
        if not at:
            continue
        vias.append((v.get("net"), float(at[0]), float(at[1]),
                     float(v.get("diameter") or 0.0) / 2.0))
    return segs, rects, vias


def _diagonal_hits(a, b, layer, net, width, obstacles, clearance):
    """The first thing this diagonal would come too close to, or None."""
    segs, rects, vias = obstacles
    for one, olayer, c, d, ow in segs:
        if one == net or olayer != layer:
            continue
        gap = _dist_seg_seg(a, b, c, d) - (width + ow) / 2.0
        if gap < clearance - 1e-9:
            return {"what": "segment", "net": one, "gap_mm": round(gap, 4)}
    for one, layers, cx, cy, hw, hh in rects:
        if one == net:
            continue
        if not ("*.Cu" in layers or layer in layers):
            continue
        gap = _dist_seg_rect(a, b, cx, cy, hw, hh) - width / 2.0
        if gap < clearance - 1e-9:
            return {"what": "pad", "net": one, "gap_mm": round(gap, 4)}
    for one, vx, vy, r in vias:
        if one == net:
            continue
        gap = _dist_point_seg((vx, vy), a, b) - width / 2.0 - r
        if gap < clearance - 1e-9:
            return {"what": "via", "net": one, "gap_mm": round(gap, 4)}
    return None


def chamfer_ops(board, max_cut_mm=1.0, min_leg_mm=0.4, tolerance_deg=0.5,
                nets=None, clearance_mm=0.25):
    """Plan a symmetric 45 degree mitre at every right-angle corner.

    The topology is not changed: each corner's vertex is replaced by two points
    `c` mm back along each leg, joined by one diagonal. Because the two legs are
    perpendicular, that diagonal is at exactly 45 degrees to both, and both pull
    backs are the same length so the mitre is symmetric.

    Three things are deliberately refused and reported instead of forced through:

    * ``leg_too_short``: the cut would leave less than `min_leg_mm` of a leg, or
      would eat a leg entirely. Shortening a stub to nothing is not a chamfer;
    * a corner whose two legs have different widths is chamfered at the wider
      width and listed under ``width_mismatch`` - a joint that steps in width is
      a real thing on a fan-out, but the caller should see it;
    * junctions (3+ segments), acute corners and arcs are never touched.

    Returns the ops in the order they must be applied: every delete first, then
    every add, so an intermediate state never carries two copies of a segment.
    """
    tol = float(tolerance_deg)
    max_cut = float(max_cut_mm)
    min_leg = float(min_leg_mm)
    segs, _arcs = _segments(board)
    by_index = {i: t for i, t in segs}
    rows = survey(board, tolerance_deg=tol)["_right_all"]

    pads, vias = _pads_and_vias(board)
    obstacles = _obstacles(board)
    cuts = {}            # (index, which_end) -> cut length
    diagonals = []       # (vertex-based points, width)
    skipped, mismatch = [], []
    # Shortest corners first. Two corners can share one segment - a 0.5 mm jog
    # between two bends has a leg at each end and cannot give both of them 1 mm
    # back. Assigning greedily by remaining leg length is what keeps a rebuild
    # from turning a segment inside out (both ends pulled past each other, which
    # is what produced overlapping copper and 26 brand-new acute corners the
    # first time this ran).
    order = sorted(rows, key=lambda c: min(c["leg_a_mm"], c["leg_b_mm"]))
    for c in order:
        if nets and c["net"] not in nets:
            continue
        why = _vertex_sits_on(c["_vertex"], pads, vias)
        if why:
            # A mitre at a pad entry pulls both legs back off the pad and leaves a
            # gap where the joint used to be. That is not a chamfer, it is an
            # open circuit with nice corners.
            skipped.append({"vertex_mm": c["vertex_mm"], "net": c["net"],
                            "reason": why})
            continue
        ta, tb = by_index[c["_index_a"]], by_index[c["_index_b"]]
        la, lb = c["leg_a_mm"], c["leg_b_mm"]
        ia, ea = c["_index_a"], c["_end_a"]
        ib, eb = c["_index_b"], c["_end_b"]
        la_free = la - cuts.get((ia, 1 - ea), 0.0) - min_leg
        lb_free = lb - cuts.get((ib, 1 - eb), 0.0) - min_leg
        cut = min(max_cut, la_free, lb_free)
        if cut <= 1e-6 and min(max_cut, la - min_leg, lb - min_leg) > 1e-6:
            skipped.append({"vertex_mm": c["vertex_mm"], "net": c["net"],
                            "reason": "leg_reserved",
                            "leg_a_free_mm": round(la_free, 4),
                            "leg_b_free_mm": round(lb_free, 4),
                            "why": "the leg already gives its other end to a bigger "
                                   "corner; cutting both would pass the two points "
                                   "through each other"})
            continue
        if cut <= 1e-6:
            skipped.append({"vertex_mm": c["vertex_mm"], "net": c["net"],
                            "reason": "leg_too_short",
                            "leg_a_mm": la, "leg_b_mm": lb,
                            "what_would_be_needed": round(min(la, lb) / 2.0, 4)})
            continue
        wa, wb = float(ta.get("width") or 0.0), float(tb.get("width") or 0.0)
        if abs(wa - wb) > 1e-6:
            mismatch.append({"vertex_mm": c["vertex_mm"], "net": c["net"],
                             "width_a_mm": wa, "width_b_mm": wb,
                             "used_mm": max(wa, wb)})
        v, ua, ub = c["_vertex"], c["_ua"], c["_ub"]
        pa = (v[0] + ua[0] * cut, v[1] + ua[1] * cut)
        pb = (v[0] + ub[0] * cut, v[1] + ub[1] * cut)
        width_here = max(float(ta.get("width") or 0.0), float(tb.get("width") or 0.0))
        # A staircase of two parallel bends 0.45 mm apart is the case that breaks
        # a mitre: both corners get cut, and the two diagonals cross. Measuring the
        # diagonal against the copper that is already there is the only way to know
        # before writing it.
        hit = _diagonal_hits(pa, pb, c["layer"], c["net"], width_here,
                             obstacles, float(clearance_mm))
        if hit:
            skipped.append({"vertex_mm": c["vertex_mm"], "net": c["net"],
                            "reason": "would_collide", "with": hit["what"],
                            "with_net": hit["net"], "gap_mm": hit["gap_mm"],
                            "cut_mm": round(cut, 4)})
            continue
        cuts[(ia, ea)] = cut
        cuts[(ib, eb)] = cut
        diagonals.append({"net": c["net"], "layer": c["layer"],
                          "start": list(pa), "end": list(pb),
                          "width": max(wa, wb), "cut_mm": round(cut, 4)})

    deletes, adds = [], []
    touched = sorted({i for (i, _e) in cuts})
    for i in touched:
        t = by_index[i]
        p_start = list(t["start"])
        p_end = list(t["end"])
        length = distances(p_start, p_end)
        cs = cuts.get((i, 0), 0.0)
        ce = cuts.get((i, 1), 0.0)
        ux = _unit(_sub(p_end, p_start), length)
        if ux is None:
            continue
        if cs:
            p_start = [p_start[0] + ux[0] * cs, p_start[1] + ux[1] * cs]
        if ce:
            p_end = [p_end[0] - ux[0] * ce, p_end[1] - ux[1] * ce]
        if distances(p_start, p_end) <= 1e-6:
            skipped.append({"net": t.get("net"), "reason": "segment_would_vanish",
                            "uuid": t.get("uuid")})
            continue
        deletes.append({"op": "delete_track", "uuid": t.get("uuid")})
        adds.append({"op": "add_track",
                     "x1": round(p_start[0], 4), "y1": round(p_start[1], 4),
                     "x2": round(p_end[0], 4), "y2": round(p_end[1], 4),
                     "width": float(t.get("width") or 0.25),
                     "layer": t.get("layer"), "net": t.get("net")})
    for d in diagonals:
        adds.append({"op": "add_track",
                     "x1": round(d["start"][0], 4), "y1": round(d["start"][1], 4),
                     "x2": round(d["end"][0], 4), "y2": round(d["end"][1], 4),
                     "width": d["width"], "layer": d["layer"], "net": d["net"]})
    return {
        "ops": deletes + adds,
        "corners_chamfered": len(diagonals),
        "segments_rewritten": len(deletes),
        "skipped": skipped,
        "width_mismatch": mismatch,
        "cut_mm_range": [min([d["cut_mm"] for d in diagonals], default=None),
                         max([d["cut_mm"] for d in diagonals], default=None)],
        "diagonals": diagonals[:20],
    }
