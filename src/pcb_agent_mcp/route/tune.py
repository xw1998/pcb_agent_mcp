"""Post-passes the maze search cannot do on its own.

Three things the search gives you for free and this module does not re-do:
clearance, creepage and copper-to-edge. Three things it cannot give you, because
they are properties of the *finished* path rather than of a single step:

* **length matching** - the shortest legal path for one net is by definition the
  wrong length for a group whose other members are longer. Lengthening means
  replacing a finished segment with a longer one that goes through the same two
  points, and then re-checking every new segment with the exact geometry check;
* **diff-pair bookkeeping** - the declared pair already reached the search as a
  required spacing (rules.pair_spacing) and a common width, so what is left is
  measuring what came out: how close the two nets actually run and how far apart
  their lengths are;
* **saying which of those two failed** - a pair that came out 0.9 mm apart where
  0.15 mm was asked for is routed, not matched, and the answer has to say so with
  the number that came out.

What is NOT here: coupled diff-pair routing. The two nets are routed
independently and must keep the declared spacing; the tool measures the achieved
gap and reports it. A constant-gap coupled walk (one net's path offset from the
other's, corners mitered) is a different algorithm and is not implemented - the
description says so and the report carries `coupled: False` rather than leaving a
reader to assume otherwise.
"""

import math

from .. import geometry as G

# A serpentine has to keep its legs apart or the meander becomes a different
# problem: at 45 degrees the perpendicular separation between the two legs of one
# tooth is amplitude / sqrt(2), so the 3w rule from the RF skill needs
# amplitude >= 3 * width * sqrt(2). Rounded up, and never below half a millimetre
# (a 0.1 mm meander is not a meander, it is a wiggle nobody can fabricate).
AMP_FACTOR = 3.0 * math.sqrt(2.0)
AMP_FLOOR_MM = 0.5
# Fabrication tolerance for "the gap is the gap". Measured on purpose: a mitered
# corner is exactly `gap` from the leg it was offset from, and a straight run is
# exact to floating point, so 0.02 mm is slack for the corner arc only.
GAP_TOL_MM = 0.02
# How far above the closest bounding-box gap a pair may sit and still be measured
# exactly. Generous on purpose: the box gap ignores both widths, and being 2 mm
# sloppy here costs a few distance calls, while being tight would report a gap
# that is not the closest one.
BOUND_SLACK_MM = 2.0


def _tracks(ops, net):
    return [o for o in ops if o.get("op") == "add_track" and o.get("net") == net]


def _seg_length(op):
    return math.hypot(op["x2"] - op["x1"], op["y2"] - op["y1"])


def net_length(ops, net, width=None):
    """Copper length of one net, the way a length-tuning engineer measures it.

    Tracks only. A via is not length: its contribution depends on the stackup and
    the drill, and adding a made-up stub length would make two different numbers
    (track length and electrical length) look like one.
    """
    return sum(_seg_length(o) for o in _tracks(ops, net))


def _gap_between(ops_a, ops_b):
    """Closest approach between two nets' copper (edge to edge, in mm).

    `None` when either side has no copper: "no gap" and "not routed" are
    different answers and a 0.0 here would read as a short.
    """
    segs_a = [((o["x1"], o["y1"]), (o["x2"], o["y2"]), o["width"])
              for o in ops_a if o.get("op") == "add_track"]
    segs_b = [((o["x1"], o["y1"]), (o["x2"], o["y2"]), o["width"])
              for o in ops_b if o.get("op") == "add_track"]
    if not segs_a or not segs_b:
        return None
    # Cheap bbox lower bound first: only the pairs whose boxes are closest can be
    # the closest copper, and walking every pair of a 200-segment net twice is the
    # slowest thing in this pass. The bound is a bound, not a cut: every pair
    # within BOUND_SLACK of the best box gap is measured exactly.
    pairs = []
    best_box = None
    for a, b, wa in segs_a:
        for c, d, wb in segs_b:
            bg = G.bbox_gap(G.bbox_of([a, b]), G.bbox_of([c, d]))
            pairs.append((bg, a, b, wa, c, d, wb))
            if best_box is None or bg < best_box:
                best_box = bg
    worst = None
    for bg, a, b, wa, c, d, wb in pairs:
        if bg > best_box + BOUND_SLACK_MM:
            continue
        g = G.dist_segment_segment(a, b, c, d) - (wa + wb) / 2.0
        if worst is None or g < worst:
            worst = g
    if worst is None:
        return None
    vias_a = [o for o in ops_a if o.get("op") == "add_via"]
    vias_b = [o for o in ops_b if o.get("op") == "add_via"]
    for v in vias_a:
        for w in vias_b:
            g = (math.hypot(v["x"] - w["x"], v["y"] - w["y"])
                 - (v["size"] + w["size"]) / 2.0)
            worst = min(worst, g)
    return worst


def _serpentine(a, b, extra_mm, amplitude, side=1):
    """Points from `a` to `b` whose copper is `extra_mm` longer than the straight.

    A continuous 45-degree triangle wave: one peak off the straight line and back
    adds `2*A*(sqrt(2)-1)` per tooth, and the two legs of one tooth stay
    `A*sqrt(2)` apart perpendicular - the spacing the caller's amplitude buys (see
    AMP_FACTOR). The last tooth is allowed to be shorter than the rest, so the
    achieved length lands on the target instead of on the next whole tooth: a
    meander that overshoots by 0.6 mm is a fatter mismatch than the one it was
    asked to fix.

    Returns `None` when the segment is too short to hold the meander at all - a
    zigzag crammed in less than its own run is a row of right angles, and the
    caller gets a refusal instead of a shape nobody can fabricate.
    """
    length = math.hypot(b[0] - a[0], b[1] - a[1])
    if length <= 1e-9 or amplitude <= 1e-9 or extra_mm <= 1e-9:
        return None
    unit = 2.0 * (math.sqrt(2.0) - 1.0)        # extra length per mm of amplitude
    per_tooth = unit * amplitude
    teeth = int(extra_mm // per_tooth)
    rest = extra_mm - teeth * per_tooth
    amps = [amplitude] * teeth
    if rest > 1e-9:
        tail = rest / unit
        if tail > amplitude:
            tail = amplitude
        amps.append(tail)
    if not amps or len(amps) > 400:
        return None
    # Along the track each tooth consumes twice its amplitude; the rest of the
    # segment is the straight lead-in and lead-out.
    used = 2.0 * sum(amps)
    if used > length - 0.5:
        return None
    ux, uy = (b[0] - a[0]) / length, (b[1] - a[1]) / length
    nx, ny = -uy * side, ux * side
    pts = [a]
    run = 0.0
    for amp in amps:
        run += 2.0 * amp
        base = (a[0] + ux * run, a[1] + uy * run)
        peak = (base[0] - ux * amp + nx * amp, base[1] - uy * amp + ny * amp)
        pts.append(peak)
        pts.append(base)
    pts.append(b)
    return pts


def max_extra(length, amplitude):
    """How much longer a segment of `length` can get from a meander of this amplitude.

    Each millimetre of amplitude buys `2*(sqrt(2)-1)` mm of extra copper but
    consumes two millimetres of the segment, so a segment can hold at most
    `(length - 0.5) / 2` millimetres of amplitude. One meander per net is not
    enough on a real board: correcting 10 mm of skew on 6 mm segments needs the
    correction spread over several of them, and a router that only tries the
    longest one reports "cannot" where "three of these" would do.
    """
    unit = 2.0 * (math.sqrt(2.0) - 1.0)
    return max(0.0, unit * (length - 0.5) / 2.0)


def _emit_meander(em, op, pts):
    """Replace one finished segment with this polyline, or leave it alone.

    Every new segment goes through the exact check first. A meander that would
    come within clearance of a neighbour is not written at all: the group then
    reports the length it actually achieved, which is the honest answer and also
    the one a person needs in order to move the track somewhere else.
    """
    net, layer, width = op["net"], op["layer"], op["width"]
    for k in range(len(pts) - 1):
        a, b = pts[k], pts[k + 1]
        bad = em.grid.seg_blockers(a, b, layer, net, width)
        if bad:
            return False, {"reason": "blocked",
                           "at": [round(a[0], 3), round(a[1], 3)],
                           "detail": bad[0]}
    for k in range(len(pts) - 1):
        a, b = pts[k], pts[k + 1]
        em.grid.add_track(net, layer, a, b, width)
    new_ops = []
    for k in range(len(pts) - 1):
        a, b = pts[k], pts[k + 1]
        new_ops.append({"op": "add_track", "x1": round(a[0], 6),
                        "y1": round(a[1], 6), "x2": round(b[0], 6),
                        "y2": round(b[1], 6), "width": width, "layer": layer,
                        "net": net})
    idx = em.ops.index(op)
    em.ops[idx:idx + 1] = new_ops
    em.length[net] = em.length.get(net, 0.0) - _seg_length(op) \
        + sum(math.hypot(o["x2"] - o["x1"], o["y2"] - o["y1"]) for o in new_ops)
    return True, {"replaced": [[round(op["x1"], 3), round(op["y1"], 3)],
                               [round(op["x2"], 3), round(op["y2"], 3)]],
                  "segments": len(new_ops)}


def match_group(em, bundle, group, results, ops_of=None):
    """Bring one length group as close to equal as the finished copper allows."""
    from . import rules as R

    nets = [n for n in (group.get("nets") or []) if n]
    name = group.get("name") or "+".join(nets)
    max_skew = group.get("max_skew_mm")
    amplitude = group.get("amplitude_mm")
    before = dict((n, round(net_length(em.ops, n), 4)) for n in nets)
    routed = dict((n, bool(_tracks(em.ops, n))) for n in nets)
    target = group.get("target_mm")
    if target is None:
        target = max(before.values()) if before else 0.0
    target = float(target)
    out = {"name": name, "nets": nets, "target_mm": round(target, 4),
           "max_skew_mm": max_skew, "before": before, "after": before,
           "routed": routed,
           "status": "reported", "detail": {}}
    missing = [n for n in nets if not routed[n]]
    if missing:
        out["status"] = "length_not_matched"
        out["detail"]["not_routed"] = missing
        return out

    for net in nets:
        need = target - before[net]
        tol = (float(max_skew) / 2.0) if max_skew is not None else 0.0
        if need <= tol:
            continue
        spec = R.for_net(bundle, net)
        width = float(spec["track_mm"])
        amp = float(amplitude) if amplitude else max(AMP_FLOOR_MM,
                                                     AMP_FACTOR * width)
        cands = sorted(_tracks(em.ops, net), key=_seg_length, reverse=True)
        got = None
        why = None
        remaining = need
        parts = []
        for op in cands:
            if remaining <= max(tol, 1e-6):
                break
            seg_len = _seg_length(op)
            room = max_extra(seg_len, amp)
            if room <= 1e-6:
                why = {"reason": "no_segment_long_enough",
                       "needed_mm": round(need, 4), "amplitude_mm": round(amp, 4),
                       "longest_segment_mm": round(seg_len, 4)}
                continue
            want = min(remaining, room)
            a = (op["x1"], op["y1"])
            b = (op["x2"], op["y2"])
            for side in (1, -1):
                pts = _serpentine(a, b, want, amp, side)
                if pts is None:
                    why = {"reason": "no_segment_long_enough",
                           "needed_mm": round(need, 4),
                           "amplitude_mm": round(amp, 4),
                           "longest_segment_mm": round(seg_len, 4)}
                    continue
                ok, info = _emit_meander(em, op, pts)
                if not ok:
                    why = info
                    continue
                added = net_length(em.ops, net) - before[net] - sum(
                    p["added_mm"] for p in parts)
                parts.append({"replaced": info["replaced"],
                              "segments": info["segments"],
                              "amplitude_mm": round(amp, 4),
                              "teeth": (len(pts) - 2) // 2,
                              "side": side,
                              "added_mm": round(added, 4)})
                remaining -= added
                break
        got = None
        if parts:
            got = {"added_mm": round(net_length(em.ops, net) - before[net], 4),
                   "amplitude_mm": round(amp, 4),
                   "meanders": parts}
        if got is None:
            out["detail"].setdefault(net, {})["why"] = why or {
                "reason": "no_segment_long_enough",
                "needed_mm": round(need, 4),
                "amplitude_mm": round(amp, 4)}
        else:
            out["detail"][net] = got
            if net in results:
                results[net]["length_mm"] = round(net_length(em.ops, net), 3)
                results[net]["segments"] = len(_tracks(em.ops, net))
                results[net]["matched"] = True

    after = dict((n, round(net_length(em.ops, n), 4)) for n in nets)
    out["after"] = after
    vals = list(after.values())
    out["skew_before_mm"] = round(max(before.values()) - min(before.values()), 4)
    out["skew_after_mm"] = round(max(vals) - min(vals), 4)
    if max_skew is None:
        out["status"] = "reported"
    elif out["skew_after_mm"] <= float(max_skew) + 1e-9:
        out["status"] = "matched"
    else:
        out["status"] = "length_not_matched"
        out["detail"].setdefault("_skew_short_by_mm",
                                 round(out["skew_after_mm"] - float(max_skew), 4))
    return out


def pair_report(em, pair, bundle=None):
    """What a declared diff pair actually came out as."""
    from . import rules as R

    p, n = pair["p"], pair["n"]
    ops_p = [o for o in em.ops if o.get("net") == p]
    ops_n = [o for o in em.ops if o.get("net") == n]
    gap = _gap_between(ops_p, ops_n)
    len_p = net_length(em.ops, p)
    len_n = net_length(em.ops, n)
    out = {"p": p, "n": n, "gap_declared_mm": pair.get("gap_mm"),
           "width_mm": pair.get("width_mm"),
           "max_skew_mm": pair.get("max_skew_mm"),
           "coupled": False,
           "length_p_mm": round(len_p, 4), "length_n_mm": round(len_n, 4),
           "skew_mm": round(abs(len_p - len_n), 4),
           "gap_achieved_mm": None if gap is None else round(gap, 4),
           "status": "reported"}
    if bundle is not None:
        # The declared gap is a MINIMUM as far as the search is concerned, so a
        # board whose own clearance or creepage is stricter will never reach it.
        # Saying which rule is binding turns "the pair is not coupled" into an
        # instruction (loosen the clearance, or accept the wider pair).
        out["spacing_required_mm"] = round(R.pair_spacing(bundle, p, n), 4)
    if gap is None:
        out["status"] = "pair_unmatched"
        out["why"] = {"reason": "one_side_not_routed",
                      "p": bool(ops_p), "n": bool(ops_n)}
        return out
    asked = pair.get("gap_mm")
    if asked is not None and gap > asked + GAP_TOL_MM:
        out["status"] = "pair_not_coupled"
        out["why"] = {"reason": "wider_than_declared",
                      "declared_mm": asked, "achieved_mm": round(gap, 4),
                      "note": "the two nets are routed with the declared spacing as "
                              "a minimum; they did not end up running side by side "
                              "and this router does not couple them"}
    elif asked is not None and gap < asked - 1e-6:
        out["status"] = "pair_violated"
        out["why"] = {"reason": "closer_than_declared",
                      "declared_mm": asked, "achieved_mm": round(gap, 4)}
    return out


def finish(em, bundle, results, diff_pairs=None, length_groups=None):
    """Run the post-passes and rebuild the search map from what actually stands."""
    pairs = list(diff_pairs or [])
    groups = [dict(g) for g in (length_groups or [])]
    reports = [pair_report(em, p, bundle) for p in pairs]
    # A pair with a skew budget IS a length group with two members - writing the
    # meander code twice would be two places to get it wrong.
    have = set(tuple(sorted(g.get("nets") or [])) for g in groups)
    for p in pairs:
        if p.get("max_skew_mm") is None or not p.get("p") or not p.get("n"):
            continue
        key = tuple(sorted([p["p"], p["n"]]))
        if key in have:
            continue
        groups.append({"name": "pair %s/%s" % (p["p"], p["n"]),
                       "nets": [p["p"], p["n"]],
                       "max_skew_mm": p["max_skew_mm"]})
        have.add(key)
    length_reports = [match_group(em, bundle, g, results) for g in groups]
    if length_reports:
        em.rebuild()
        for net in set(n for g in groups for n in (g.get("nets") or [])):
            if net in results:
                results[net]["length_mm"] = round(net_length(em.ops, net), 3)
                results[net]["segments"] = len(_tracks(em.ops, net))
        # The gap is only meaningful after the meanders: a changed length is
        # changed copper, and the pair's achieved spacing has to be re-measured.
        reports = [pair_report(em, p, bundle) for p in pairs]
    return {"diff_pairs": reports, "length_groups": length_reports}
