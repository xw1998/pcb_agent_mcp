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

The SHAPE of a meander is a choice, not a constant. `style="arc"` draws the same
added length as a ROUNDED wave (straight run along the axis, 90-degree arc, climb,
arc back - tangent continuous, closed-form `h + r*(pi - 2)` per tooth), which is
the shape this package's own planner (`route.tune_board`) defaults to and the only
one the user has accepted; `style="chamfer"` keeps the 45-degree tooth this module
has always emitted, and stays the default so a caller who says nothing gets the
copper it got yesterday. Both styles go through the SAME geometry: an "arc" wave
is a call into `route.tune_board.arc_meander`, not a second implementation.
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
# A meander that does not fit at the declared amplitude is not the end of the
# answer: a narrower one still adds length, it just needs a longer segment. The
# ladder is what turns "cannot match this group" into "matched with a 0.9 mm
# amplitude", and each step is re-checked exactly, so a narrow shape is never
# written on the strength of the wide one having been close.
AMPLITUDE_LADDER = (1.0, 0.75, 0.5, 0.35, 0.25)
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
    """STRAIGHT segments of one net, i.e. the segments a meander may replace.

    Straight only on purpose: an arc is copper (it is length, see `net_length`)
    but it is never a meander HOST - a tooth fanned off a curve has no axis to
    fan from, and "re-shape the equalising wave" is not a thing this module does.
    """
    return [o for o in ops if o.get("op") == "add_track" and o.get("net") == net]

def _arcs(ops, net):
    """Arc ops of one net, in the order they were written."""
    return [o for o in ops
            if o.get("op") == "add_track_arc" and o.get("net") == net]


def _seg_length(op):
    return math.hypot(op["x2"] - op["x1"], op["y2"] - op["y1"])


def net_length(ops, net, width=None):
    """Copper length of one net, the way a length-tuning engineer measures it.

    Tracks AND arcs. A via is not length: its contribution depends on the stackup
    and the drill, and adding a made-up stub length would make two different
    numbers (track length and electrical length) look like one. An arc IS length,
    measured through its three points (`edit.ops.arc_length_mm`) rather than from
    a chord polyline - the same number `route.tune_board` reports, so a rounded
    meander written by either module adds the same millimetres to this figure.
    Counting arcs only when they exist keeps the all-straight case at exactly the
    arithmetic it had before.
    """
    total = sum(_seg_length(o) for o in _tracks(ops, net))
    arcs = _arcs(ops, net)
    if arcs:
        from ..edit import ops as OPS
        total += sum(OPS.arc_length_mm((o["x1"], o["y1"]),
                                       (o["mx"], o["my"]),
                                       (o["x2"], o["y2"])) for o in arcs)
    return total


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


#: How the meander `match_group` writes is shaped. "chamfer" is the 45-degree
#: tooth this module has always emitted; "arc" is the rounded wave, produced by
#: `route.tune_board.arc_meander` - one geometry, two callers, so the router's
#: post-pass and the offline planner cannot drift into two different "arc
#: meanders". The default is "chamfer" on purpose: a caller who says nothing gets
#: byte-for-byte the copper it got before this option existed.
MEANDER_STYLES = ("chamfer", "arc")
MEANDER_STYLE_DEFAULT = "chamfer"

def _wave(a, b, extra_mm, amplitude, side=1, style=MEANDER_STYLE_DEFAULT,
          width=None):
    """Pieces of a meander that adds `extra_mm` to the run from `a` to `b`.

    Returns `(pieces, info)`. `pieces` is a list of `("line", p, q)` /
    `("arc", s, m, e)` - the same list shape `route.tune_board.plan` emits, so a
    reader can put the two answers side by side. `info` says what the report
    needs about the shape (`teeth`, `arcs`, `radius_mm`, `sub_legible`).

    `(None, refusal)` when this segment cannot hold the wave, and the refusal
    names WHY: `no_segment_long_enough` (the run is shorter than the wave needs),
    `too_shallow` (only cross-bars narrower than a trace fit - a different fix
    from "no room") and `no_legible_radius`. A caller that cannot tell those
    apart re-runs the tool with the same inputs.

    `style="chamfer"` reproduces `_serpentine` exactly and emits no arc, so the
    default path is unchanged down to the coordinates; `style="arc"` hands the
    same arithmetic to `route.tune_board.arc_meander` (imported here, not at
    module scope, because `tune_board` already imports this module).
    """
    if style not in MEANDER_STYLES:
        raise ValueError("style must be one of %s, got %r"
                         % (", ".join(MEANDER_STYLES), style))
    length = math.hypot(b[0] - a[0], b[1] - a[1])
    if style == "chamfer":
        pts = _serpentine(a, b, extra_mm, amplitude, side)
        if pts is None:
            return None, {"reason": "no_segment_long_enough",
                          "longest_segment_mm": round(length, 4)}
        return ([("line", pts[k], pts[k + 1]) for k in range(len(pts) - 1)],
                {"teeth": (len(pts) - 2) // 2, "arcs": 0, "radius_mm": None,
                 "sub_legible": False})
    from . import tune_board as TB
    got = TB.arc_meander({"start": (float(a[0]), float(a[1])),
                          "end": (float(b[0]), float(b[1])),
                          "length_mm": length,
                          "width": float(width or 0.0)},
                         extra_mm, amplitude, side=side)
    if got is None:
        return None, {"reason": "no_segment_long_enough",
                      "longest_segment_mm": round(length, 4)}
    if got.get("pieces") is None:
        why = {"reason": ("too_shallow" if got.get("too_shallow")
                           else "no_legible_radius")}
        why.update((k, v) for k, v in got.items() if k != "pieces")
        return None, why
    info = {"teeth": got["teeth"],
            "arcs": sum(1 for p in got["pieces"] if p[0] == "arc"),
            "radius_mm": round(float(got["r"]), 4),
            "sub_legible": bool(got.get("sub_legible"))}
    if got.get("sub_legible"):
        info["radius_floor_mm"] = got.get("r_floor_mm")
    return got["pieces"], info

def _emit_meander(em, op, pieces):
    """Replace one finished segment with this path, or leave it alone.

    Every new piece goes through the exact check first; arcs are checked chord by
    chord with the probe widened by the sagitta the chord can hide - the SAME
    probe `route.tune_board.plan` judges arcs with, so "it passed here" and "it
    passed there" are one test and not two. A meander that would come within
    clearance of a neighbour is not written at all: the group then reports the
    length it actually achieved, which is the honest answer and also the one a
    person needs in order to move the track somewhere else.
    """
    from . import tune_board as TB
    net, layer, width = op["net"], op["layer"], op["width"]
    probes = list(TB._pieces_probe(pieces, width))
    for a, b, w in probes:
        bad = em.grid.seg_blockers(a, b, layer, net, w)
        if bad:
            return False, {"reason": "blocked",
                           "at": [round(a[0], 3), round(a[1], 3)],
                           "detail": bad[0]}
    for a, b, w in probes:
        em.grid.add_track(net, layer, a, b, w)
    new_ops = []
    added = 0.0
    for piece in pieces:
        if piece[0] == "line":
            a, b = piece[1], piece[2]
            added += math.hypot(b[0] - a[0], b[1] - a[1])
            new_ops.append({"op": "add_track", "x1": round(a[0], 6),
                            "y1": round(a[1], 6), "x2": round(b[0], 6),
                            "y2": round(b[1], 6), "width": width,
                            "layer": layer, "net": net})
        else:
            from ..edit import ops as OPS
            a, m, b = piece[1], piece[2], piece[3]
            added += OPS.arc_length_mm(a, m, b)
            new_ops.append({"op": "add_track_arc", "x1": round(a[0], 6),
                            "y1": round(a[1], 6), "mx": round(m[0], 6),
                            "my": round(m[1], 6), "x2": round(b[0], 6),
                            "y2": round(b[1], 6), "width": width,
                            "layer": layer, "net": net})
    idx = em.ops.index(op)
    em.ops[idx:idx + 1] = new_ops
    em.length[net] = em.length.get(net, 0.0) - _seg_length(op) + added
    return True, {"replaced": [[round(op["x1"], 3), round(op["y1"], 3)],
                               [round(op["x2"], 3), round(op["y2"], 3)]],
                  "segments": len(new_ops),
                  "arcs": sum(1 for o in new_ops
                              if o["op"] == "add_track_arc")}


def _candidate_order(em, net, cands, air_mm=2.0):
    """Rank a net's straight segments by how well a meander would sit on them.

    Length alone is the wrong key twice over: a shorter segment in open copper is
    a better host than the longest one squeezed between two other nets, and a
    segment that ends at a via is the worst host of all - a serpentine right at a
    layer change is a stub, and every tooth of it is a reflection the board did
    not have before. So the score is "how much length this segment can hold",
    discounted by what surrounds it:

    * `room_mm` - `max_extra` at the widest amplitude, i.e. the honest upper
      bound on what this segment can buy;
    * `ends_at_a_via` - quarter weight, from the net's own vias on that layer;
    * `other_copper_within_air` - quarter weight when copper of ANOTHER net sits
      within `air_mm` of the segment's midpoint, read from the router's own
      raster (a coarse probe, and named as one).

    Returns `[(score, op, why), ...]` best first, with `why` kept so the report
    can say which segments were passed over and for what.
    """
    out = []
    vias = [(o["x"], o["y"]) for o in em.ops if o.get("op") == "add_via"
            and o.get("net") == net]
    grid = em.grid
    for op in cands:
        a = (op["x1"], op["y1"])
        b = (op["x2"], op["y2"])
        seg_len = _seg_length(op)
        widest = max(AMP_FLOOR_MM, AMP_FACTOR * float(op["width"]))
        room = max_extra(seg_len, widest)
        ends_at_a_via = any(
            (abs(vx - a[0]) < 1e-6 and abs(vy - a[1]) < 1e-6)
            or (abs(vx - b[0]) < 1e-6 and abs(vy - b[1]) < 1e-6)
            for vx, vy in vias)
        crowd = None
        try:
            layer_map = grid._map[op["layer"]]
            for cell in grid.cells_near((a[0] + b[0]) / 2.0, (a[1] + b[1]) / 2.0,
                                        air_mm):
                val = layer_map[cell]
                if not val:
                    continue
                names = grid._sets[val - 1]
                if len(names) == 1 and names[0] == net:
                    continue
                crowd = True
                break
        except (KeyError, IndexError, AttributeError):   # noqa: BLE001
            crowd = None
        score = room
        if ends_at_a_via:
            score *= 0.25
        if crowd:
            score *= 0.25
        out.append((score, op, {"length_mm": round(seg_len, 4),
                                "room_mm": round(room, 4),
                                "ends_at_a_via": bool(ends_at_a_via),
                                "other_copper_within_mm": (air_mm if crowd else None)}))
    out.sort(key=lambda it: -it[0])
    return out

def match_group(em, bundle, group, results, ops_of=None):
    """Bring one length group as close to equal as the finished copper allows."""
    from . import rules as R

    nets = [n for n in (group.get("nets") or []) if n]
    name = group.get("name") or "+".join(nets)
    max_skew = group.get("max_skew_mm")
    amplitude = group.get("amplitude_mm")
    # The shape is the caller's, per group: a group that asks for "arc" gets the
    # rounded wave, and one that says nothing gets the tooth this module has
    # always drawn - so "style not mentioned" cannot change a single coordinate.
    style = group.get("style") or MEANDER_STYLE_DEFAULT
    if style not in MEANDER_STYLES:
        raise ValueError("%s: style must be one of %s, got %r"
                         % (name, ", ".join(MEANDER_STYLES), style))
    before = dict((n, round(net_length(em.ops, n), 4)) for n in nets)
    routed = dict((n, bool(_tracks(em.ops, n))) for n in nets)
    target = group.get("target_mm")
    if target is None:
        target = max(before.values()) if before else 0.0
    target = float(target)
    out = {"name": name, "nets": nets, "target_mm": round(target, 4),
           "max_skew_mm": max_skew, "before": before, "after": before,
           "routed": routed, "style": style,
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
        # Length is not the best host criterion (see `_candidate_order`), and one
        # amplitude is not the only shape: the ladder below is tried on each
        # candidate before it is given up on.
        ranked = _candidate_order(em, net, _tracks(em.ops, net))
        cands = [op for _score, op, _why in ranked]
        tried = [why for _score, _op, why in ranked]
        ladder = [amp]
        for factor in AMPLITUDE_LADDER:
            narrow = amp * factor
            if narrow >= max(AMP_FLOOR_MM / 4.0, 0.15) and \
                    all(abs(narrow - x) > 1e-9 for x in ladder):
                ladder.append(narrow)
        got = None
        why = None
        remaining = need
        parts = []
        for op in cands:
            if remaining <= max(tol, 1e-6):
                break
            seg_len = _seg_length(op)
            if max_extra(seg_len, ladder[0]) <= 1e-6:
                why = {"reason": "no_segment_long_enough",
                       "needed_mm": round(need, 4),
                       "amplitude_mm": round(ladder[0], 4),
                       "longest_segment_mm": round(seg_len, 4)}
                continue
            a = (op["x1"], op["y1"])
            b = (op["x2"], op["y2"])
            placed = False
            for this_amp in ladder:
                room = max_extra(seg_len, this_amp)
                if room <= 1e-6:
                    continue
                want = min(remaining, room)
                for side in (1, -1):
                    pieces, meta = _wave(a, b, want, this_amp, side, style,
                                         width)
                    if pieces is None:
                        # The refusal carries its own reason and the numbers that
                        # led to it; the group's need is added next to them so a
                        # reader can see how far short this host is.
                        why = dict(meta)
                        why.setdefault("needed_mm", round(need, 4))
                        why.setdefault("amplitude_mm", round(this_amp, 4))
                        why.setdefault("longest_segment_mm", round(seg_len, 4))
                        continue
                    ok, info = _emit_meander(em, op, pieces)
                    if not ok:
                        why = dict(info)
                        why["amplitude_mm"] = round(this_amp, 4)
                        continue
                    added = net_length(em.ops, net) - before[net] - sum(
                        p["added_mm"] for p in parts)
                    part = {"replaced": info["replaced"],
                            "segments": info["segments"],
                            "amplitude_mm": round(this_amp, 4),
                            "narrowed": abs(this_amp - amp) > 1e-9,
                            "style": style,
                            "teeth": meta["teeth"],
                            "side": side,
                            "added_mm": round(added, 4)}
                    if style == "arc":
                        # Only named when it exists: an all-straight report stays
                        # byte-identical, an arc one says how round it came out.
                        part["arcs"] = info["arcs"]
                        part["radius_mm"] = meta["radius_mm"]
                        part["sub_legible"] = meta["sub_legible"]
                        if meta.get("radius_floor_mm") is not None:
                            part["radius_floor_mm"] = meta["radius_floor_mm"]
                    parts.append(part)
                    remaining -= added
                    placed = True
                    break
                if placed:
                    break
            if placed:
                continue
        got = None
        if parts:
            got = {"added_mm": round(net_length(em.ops, net) - before[net], 4),
                   "amplitude_mm": round(parts[0]["amplitude_mm"], 4),
                   "amplitude_ladder_mm": [round(x, 4) for x in ladder],
                   "style": style,
                   "meanders": parts,
                   "hosts_considered": tried[:8]}
            if style == "arc":
                got["arcs"] = sum(int(p.get("arcs") or 0) for p in parts)
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


def finish(em, bundle, results, diff_pairs=None, length_groups=None,
           terms_by_net=None, return_path=True, return_path_net=None):
    """Run the post-passes and rebuild the search map from what actually stands.

    Order matters and is not arbitrary: **coupling first**, because it REPLACES
    the second net's copper (a meander written before it would be thrown away),
    then length matching, because a meander changes a length, and **ground
    stitching last**, because it reacts to whatever vias the board finally has -
    a pair coupled after the stitching pass would leave its fanout vias
    unserved.
    """
    from . import couple as C

    pairs = [dict(p) for p in (diff_pairs or [])]
    groups = [dict(g) for g in (length_groups or [])]
    coupling = _couple_pairs(em, bundle, results, pairs, terms_by_net)
    reports = [pair_report(em, p, bundle) for p in pairs]
    for rep in reports:
        key = tuple(sorted([rep.get("p"), rep.get("n")]))
        info = coupling.get(key)
        if not info:
            continue
        rep["coupled"] = bool(info.get("coupled"))
        rep["coupling"] = info
        # The measured gap is still reported even when coupling worked: a coupled
        # pair whose copper is 0.05 mm off is a fact about the board, not a reason
        # to stop reporting numbers.
        if info.get("coupled_fraction") is not None:
            rep["coupled_fraction"] = info["coupled_fraction"]
        if not info.get("coupled"):
            # The measured gap still stands, but "the pair kept its independent
            # routing" now has a reason attached, and the reason is not "this
            # router cannot couple" any more.
            rep["why"] = _coupling_why(info, rep.get("gap_declared_mm"),
                                       rep.get("gap_achieved_mm"))
        if info.get("coupled"):
            if rep.get("status") == "pair_not_coupled":
                rep["status"] = "pair_coupled"
            elif rep.get("status") == "reported":
                rep["status"] = "pair_coupled"
            if info.get("skew_over_budget"):
                rep["status"] = "pair_skew_over_budget"
                rep["why"] = info.get("why") or {
                    "reason": "coupled_pair_cannot_be_meandered",
                    "skew_mm": info.get("skew_mm"),
                    "max_skew_mm": info.get("max_skew_mm"),
                    "note": "a coupled pair has to be lengthened as a PAIR (both "
                            "members accordion together); meandering one member "
                            "would destroy the gap, so nothing was meandered and "
                            "the skew is reported as it came out"}
    coupled_keys = set(k for k, v in coupling.items() if v.get("coupled"))
    # A pair with a skew budget IS a length group with two members - writing the
    # meander code twice would be two places to get it wrong.
    have = set(tuple(sorted(g.get("nets") or [])) for g in groups)
    for p in pairs:
        if p.get("max_skew_mm") is None or not p.get("p") or not p.get("n"):
            continue
        key = tuple(sorted([p["p"], p["n"]]))
        if key in have:
            continue
        # A coupled pair DOES get its skew budget turned into a length group.
        # The meander then lands on the shorter member only, and the exact check
        # forces it to the side AWAY from its partner (a tooth toward the partner
        # would eat the gap and be refused). That is how a person tunes a coupled
        # pair: the extra length lives outside the pair, not between the two
        # traces. `coupled_fraction` is re-measured afterwards so the number that
        # finally comes out already counts any length that left the pair.
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
        # changed copper, and every number about the pair has to be re-measured.
        reports = [pair_report(em, p, bundle) for p in pairs]
        _refresh_measures(em, pairs, coupling, reports)
    rp = None
    if return_path:
        from . import return_path as RP
        rp = RP.stitch(em, bundle, results, net=return_path_net or None)
    return {"diff_pairs": reports, "length_groups": length_reports,
            "return_path": rp}


_COUPLING_TEXT = {
    "not_asked": "the pair was declared with couple=false, so the two nets were "
                 "routed independently",
    "no_terminal_information": "the router was given no terminal table, so it "
                               "cannot know which pads are the pair's ends and "
                               "left the two nets independently routed",
    "one_side_not_routed": "one side of the pair did not route, so there is no "
                           "reference path to offset",
    "pair_needs_two_pads_each": "coupling needs exactly two pads on each side: "
                                "the offset walk has two ends and cannot guess "
                                "which branch a third pad belongs to",
    "reference_net_has_no_copper": "the first net has no copper to offset",
    "reference_net_branches": "the first net's copper branches (or loops), so "
                              "there is no single path to offset - a guess would "
                              "put the second net in the wrong place",
    "reference_net_has_a_zero_length_run": "a layer run of the first net has no "
                                           "length, so its normal is undefined",
    "offset_degenerate": "the offset path collapsed (a repeated point leaves no "
                         "normal to offset along)",
    "offset_blocked": "the offset path runs into other copper",
    "fanout_too_long": "the offset path would start too far from a pad to be a "
                       "fanout, and joining it would draw a diagonal nobody asked "
                       "for",
}


def _coupling_why(info, declared, achieved):
    """One sentence for "why is this pair not coupled", specific to the reason."""
    reason = info.get("reason") or "not_coupled"
    text = _COUPLING_TEXT.get(reason, "coupling was not applied (%s)" % reason)
    out = {"reason": reason, "coupled": False,
           "declared_mm": declared, "achieved_mm": achieved,
           "note": "the two nets do not run side by side: %s" % text}
    detail = info.get("detail")
    if isinstance(detail, dict):
        out["detail"] = detail
    for key in ("gap_mm", "coupled_fraction", "skew_mm", "max_skew_mm"):
        if info.get(key) is not None:
            out[key] = info[key]
    return out


def _refresh_measures(em, pairs, coupling, reports):
    """Re-measure every pair number against the copper that finally stands."""
    from . import couple as C

    for pair in pairs:
        key = tuple(sorted([pair.get("p"), pair.get("n")]))
        info = coupling.get(key)
        if not info or not info.get("coupled"):
            continue
        p, n = pair.get("p"), pair.get("n")
        gap = float(pair["gap_mm"])
        info["coupled_fraction"] = C.coupled_fraction(em.ops, p, n, gap)
        g = _gap_between([o for o in em.ops if o.get("net") == p],
                         [o for o in em.ops if o.get("net") == n])
        info["gap_achieved_mm"] = None if g is None else round(g, 4)
        info["length_p_mm"] = round(net_length(em.ops, p), 4)
        info["length_n_mm"] = round(net_length(em.ops, n), 4)
        info["skew_mm"] = round(abs(info["length_p_mm"] - info["length_n_mm"]), 4)
        for rep in reports:
            if tuple(sorted([rep.get("p"), rep.get("n")])) != key:
                continue
            rep["coupled"] = True
            rep["coupling"] = info
            rep["coupled_fraction"] = info["coupled_fraction"]
            rep["gap_achieved_mm"] = info["gap_achieved_mm"]
            rep["length_p_mm"] = info["length_p_mm"]
            rep["length_n_mm"] = info["length_n_mm"]
            rep["skew_mm"] = info["skew_mm"]
            # A meander outside the pair is copper that is not at the gap any
            # more, so the pair status has to say "coupled, tuned outside" rather
            # than pretend the whole run is a tight pair.
            frac = info["coupled_fraction"]
            if marry_ok(rep, frac):
                rep["status"] = "pair_coupled"
            else:
                rep["status"] = "pair_coupled_partly"
                rep.setdefault("why", {})["note"] = (
                    "the pair is coupled but only %.0f%% of the second net's "
                    "length runs at the declared gap; the rest is fanout and "
                    "length-tuning copper outside the pair" % (100.0 * frac))


def marry_ok(rep, frac):
    """Is this pair coupled tightly enough to be called coupled without a caveat?"""
    if rep.get("status") == "pair_skew_over_budget":
        return False
    return frac is not None and frac >= 0.8


def _couple_pairs(em, bundle, results, pairs, terms_by_net):
    """Make the second net of each pair the first's path, offset by the gap."""
    from . import couple as C

    out = {}
    for pair in pairs:
        p, n = pair.get("p"), pair.get("n")
        key = tuple(sorted([p, n]))
        if not p or not n:
            continue
        if pair.get("couple") is False:
            out[key] = {"p": p, "n": n, "coupled": False,
                        "reason": "not_asked",
                        "note": "the pair was declared with couple=false, so the "
                                "two nets were routed independently"}
            continue
        if terms_by_net is None:
            out[key] = {"p": p, "n": n, "coupled": False,
                        "reason": "no_terminal_information"}
            continue
        if (results.get(p) or {}).get("status") != "routed" \
                or (results.get(n) or {}).get("status") != "routed":
            out[key] = {"p": p, "n": n, "coupled": False,
                        "reason": "one_side_not_routed",
                        "status_p": (results.get(p) or {}).get("status"),
                        "status_n": (results.get(n) or {}).get("status")}
            continue
        rep = C.coupled_route(em, bundle, pair, terms_by_net.get(p),
                              terms_by_net.get(n))
        gap = pair.get("gap_mm")
        if rep.get("ok"):
            rep["coupled_fraction"] = C.coupled_fraction(em.ops, p, n, float(gap))
            ops_p = [o for o in em.ops if o.get("net") == p]
            ops_n = [o for o in em.ops if o.get("net") == n]
            g = _gap_between(ops_p, ops_n)
            rep["gap_achieved_mm"] = None if g is None else round(g, 4)
            rep["length_p_mm"] = round(net_length(em.ops, p), 4)
            rep["length_n_mm"] = round(net_length(em.ops, n), 4)
            rep["skew_mm"] = round(abs(rep["length_p_mm"] - rep["length_n_mm"]), 4)
            if n in results:
                results[n]["length_mm"] = round(rep["length_n_mm"], 3)
                results[n]["segments"] = len(_tracks(em.ops, n))
                results[n]["vias"] = len([o for o in em.ops
                                          if o.get("op") == "add_via"
                                          and o.get("net") == n])
                results[n]["coupled_to"] = p
            budget = pair.get("max_skew_mm")
            if budget is not None and rep["skew_mm"] > float(budget) + 1e-9:
                rep["skew_over_budget"] = True
                rep["max_skew_mm"] = budget
        out[key] = rep
    if out:
        em.rebuild()
    return out
