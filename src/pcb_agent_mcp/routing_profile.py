"""What a routing looks like as numbers, so "like the reference" can be measured.

The question this answers was asked in one sentence: why does copying somebody's
board look right, and drawing it myself look wrong? Copying hands over the answer
(every segment, every channel); drawing it myself leaves only `board_drc`, which
is flat across the whole region of "mostly fine". For routing the missing piece is
not another router - it is the *shape* of a good routing, written down:

* which way each layer prefers to run (a layer that is 80 % horizontal is a
  discipline; a layer split 50/50 across every angle is a free-for-all),
* how long a segment is allowed to be before it reads as a crossing rather than a
  route,
* how many bends a net takes,
* how far a track travels before it leaves its pad (fan-out length),
* how often it changes layer.

Measured 2026-11-07 on this machine's two ends of the range: the F412 core board
(336 top segments, many crossing the whole board diagonally) against the Jetson
carrier (22 151 segments) - the numbers are in `docs/122`.

Nothing here routes. `violations()` returns the segments that break a rule so a
person can redraw them one at a time; automatic routing is banned in this server
and a profile tool would not change that.
"""
import math

#: A segment whose angle is within this many degrees of 0/45/90/135 counts as on
#: that direction. One degree is far tighter than a hand-drawn bend and far looser
#: than float noise on a length of 50 mm.
ANGLE_TOL_DEG = 1.0
#: A turn is counted when the direction changes by at least this much. Below it the
#: two segments are the same straight run split into two records.
TURN_MIN_DEG = 20.0
#: Points are matched to pads / to each other within this distance, in mm. Two
#: tenths of a millimetre is under a 0.1 mm grid step and far under a pad.
JOIN_TOL_MM = 0.12
#: A segment longer than this share of the board diagonal is a "crossing": it
#: spans the board instead of serving a neighbourhood.
CROSSING_SHARE = 0.25


def _bbox(board):
    rings = [ring for ring in (getattr(board, "outline", None) or []) if len(ring) >= 3]
    if rings:
        xs = [p[0] for ring in rings for p in ring]
        ys = [p[1] for ring in rings for p in ring]
        return (min(xs), min(ys), max(xs), max(ys))
    xs, ys = [], []
    for fp in getattr(board, "footprints", []) or []:
        if fp.get("x") is not None and fp.get("y") is not None:
            xs.append(float(fp["x"]))
            ys.append(float(fp["y"]))
    if not xs:
        return None
    return (min(xs), min(ys), max(xs), max(ys))


def _median(values):
    if not values:
        return None
    ordered = sorted(values)
    mid = len(ordered) // 2
    return ordered[mid] if len(ordered) % 2 else (ordered[mid - 1] + ordered[mid]) / 2.0


def _share(values, predicate):
    if not values:
        return None
    return sum(1 for v in values if predicate(v)) / float(len(values))


def _direction(start, end):
    dx, dy = end[0] - start[0], end[1] - start[1]
    if abs(dx) < 1e-9 and abs(dy) < 1e-9:
        return None
    angle = math.degrees(math.atan2(dy, dx)) % 180.0
    folded = angle - round(angle / 45.0) * 45.0
    bucket = int(round(angle / 45.0)) % 4         # 0 horizontal, 1 45, 2 vertical, 3 135
    return angle, abs(folded), bucket


def _segment_table(board):
    rows = []
    for track in getattr(board, "tracks", []) or []:
        start, end = track.get("start"), track.get("end")
        if not start or not end:
            continue
        info = _direction(start, end)
        if info is None:
            continue
        angle, deviation, bucket = info
        length = math.hypot(end[0] - start[0], end[1] - start[1])
        rows.append({"uuid": track.get("uuid"), "net": track.get("net"),
                     "layer": track.get("layer"), "length_mm": length,
                     "angle_deg": angle, "off_grid_deg": deviation,
                     "bucket": bucket, "start": start, "end": end,
                     "width_mm": track.get("width")})
    return rows


def _key(point):
    return (round(point[0] / JOIN_TOL_MM), round(point[1] / JOIN_TOL_MM))


def _bends(rows):
    """Bends per net: walk each net's segments as a graph of endpoints, count the
    places where the direction changes by TURN_MIN_DEG or more."""
    by_net = {}
    for row in rows:
        by_net.setdefault(row["net"], []).append(row)
    per_net = {}
    for net, segs in by_net.items():
        adjacency = {}
        for index, row in enumerate(segs):
            for end in ("start", "end"):
                adjacency.setdefault(_key(row[end]), []).append(index)
        seen = {}
        turns = 0
        for index, row in enumerate(segs):
            for end in ("start", "end"):
                others = [i for i in adjacency.get(_key(row[end]), []) if i != index]
                for other in others:
                    pair = (min(index, other), max(index, other))
                    if pair in seen:
                        continue
                    seen[pair] = True
                    a = segs[index]["angle_deg"]
                    b = segs[other]["angle_deg"]
                    delta = abs(a - b) % 180.0
                    if delta > 90.0:
                        delta = 180.0 - delta
                    if delta >= TURN_MIN_DEG:
                        turns += 1
        per_net[net] = turns
    return per_net


def _fanout(rows, board):
    """Length of the first segment after a pad, per net. A short fan-out means the
    track leaves the pin along the pin's own direction; a long one means it struck
    out diagonally across the board from the pad."""
    pads = {}
    for fp in getattr(board, "footprints", []) or []:
        for pad in fp.get("pads") or []:
            net = pad.get("net")
            if net is None or pad.get("x") is None or pad.get("y") is None:
                continue
            pads.setdefault(net, []).append((float(pad["x"]), float(pad["y"])))
    values = []
    for row in rows:
        near = pads.get(row["net"]) or []
        for point in (row["start"], row["end"]):
            for pad in near:
                if math.hypot(point[0] - pad[0], point[1] - pad[1]) <= JOIN_TOL_MM:
                    values.append(row["length_mm"])
                    break
    return values


def profile(board, net_scope=None):
    """One board's routing shape. Absolute numbers; no comparison happens here."""
    rows = _segment_table(board)
    if net_scope:
        rows = [r for r in rows if r["net"] == net_scope]
    box = _bbox(board)
    diagonal = None
    if box:
        diagonal = math.hypot(box[2] - box[0], box[3] - box[1])
    layers = {}
    for row in rows:
        entry = layers.setdefault(row["layer"], {"segments": 0, "length_mm": 0.0,
                                                "buckets": [0, 0, 0, 0]})
        entry["segments"] += 1
        entry["length_mm"] += row["length_mm"]
        entry["buckets"][row["bucket"]] += 1
    per_layer = []
    for name, entry in sorted(layers.items()):
        total = entry["segments"]
        horiz = entry["buckets"][0]
        vert = entry["buckets"][2]
        axis = horiz + vert
        per_layer.append({
            "layer": name,
            "segments": total,
            "length_mm": round(entry["length_mm"], 2),
            "horizontal_share": round(horiz / float(total), 4) if total else None,
            "vertical_share": round(vert / float(total), 4) if total else None,
            "diagonal_share": round((entry["buckets"][1] + entry["buckets"][3])
                                    / float(total), 4) if total else None,
            "preference_strength": round(max(horiz, vert) / float(axis), 4) if axis else None,
            "preferred_direction": ("horizontal" if horiz > vert else "vertical")
                                   if axis else None,
        })
    lengths = [row["length_mm"] for row in rows]
    bends = _bends(rows)
    vias = {}
    for via in getattr(board, "vias", []) or []:
        if via.get("net") is not None:
            vias[via["net"]] = vias.get(via["net"], 0) + 1
    fanout = _fanout(rows, board)
    crossings = [row for row in rows
                 if diagonal and row["length_mm"] > CROSSING_SHARE * diagonal]
    off_grid = _share([row["off_grid_deg"] for row in rows],
                      lambda v: v > ANGLE_TOL_DEG)
    result = {
        "board": (getattr(board, "source", None) or {}).get("path"),
        "segments": len(rows),
        "total_length_mm": round(sum(lengths), 2),
        "board_diagonal_mm": round(diagonal, 2) if diagonal else None,
        "length_mm": {
            "median": round(_median(lengths), 3) if lengths else None,
            "p90": round(sorted(lengths)[int(0.9 * (len(lengths) - 1))], 3) if lengths else None,
            "max": round(max(lengths), 3) if lengths else None,
        },
        "off_grid_share": round(off_grid, 4) if off_grid is not None else None,
        "diagonal_share": (round(_share([r["bucket"] for r in rows],
                                       lambda b: b in (1, 3)), 4) if rows else None),
        "crossing_segments": len(crossings),
        "crossing_share": round(len(crossings) / float(len(rows)), 4) if rows else None,
        "crossing_threshold_mm": round(CROSSING_SHARE * diagonal, 2) if diagonal else None,
        "bends_per_net_median": _median(list(bends.values())) if bends else None,
        "nets_with_copper": len(bends),
        "vias_per_net_median": _median(list(vias.values())) if vias else None,
        "fanout_length_mm_median": (round(_median(fanout), 3) if fanout else None),
        "fanout_segments": len(fanout),
        "layers": per_layer,
        "thresholds": {"angle_tol_deg": ANGLE_TOL_DEG, "turn_min_deg": TURN_MIN_DEG,
                       "join_tol_mm": JOIN_TOL_MM,
                       "crossing_share": CROSSING_SHARE,
                       "note": "these decide the counts; change one and every share "
                               "under it changes with it"},
    }
    result["discipline"] = _discipline(result)
    return result


def _discipline(got):
    """A single 0-100 for ordering boards against each other.

    Four things people see first: how much of the copper runs diagonally, how many
    segments cross the board, whether each layer has a preferred direction, and how
    far a track travels before it leaves its pad. Each is mapped from "what a tidy
    board looks like" (low, low, strong, short) with the anchors printed in the
    answer, because a score with hidden anchors is a vibe.
    """
    parts = {}

    def push(name, value, good, bad):
        if value is None:
            return
        if good == bad:
            parts[name] = None
            return
        raw = (value - bad) / float(good - bad)
        parts[name] = round(max(0.0, min(1.0, raw)), 4)

    push("diagonal", got.get("diagonal_share"), 0.10, 0.70)
    push("crossing", got.get("crossing_share"), 0.01, 0.25)
    strengths = [row.get("preference_strength") for row in got.get("layers") or []
                 if row.get("preference_strength") is not None]
    push("layer_preference", _median(strengths) if strengths else None, 0.90, 0.55)
    push("fanout", got.get("fanout_length_mm_median"), 1.0, 8.0)
    scored = [v for v in parts.values() if v is not None]
    return {"score": round(100.0 * sum(scored) / len(scored), 1) if scored else None,
            "components": parts,
            "anchors": {"diagonal_share": [0.10, 0.70],
                        "crossing_share": [0.01, 0.25],
                        "layer_preference_strength": [0.90, 0.55],
                        "fanout_length_mm_median": [1.0, 8.0],
                        "note": "good/bad anchors: the score is a ranking device for "
                                "comparing boards, not a verdict on whether a routing "
                                "works"}}


COMPARE_KEYS = ("diagonal_share", "crossing_share", "off_grid_share",
                "bends_per_net_median", "vias_per_net_median",
                "fanout_length_mm_median")


def compare(target, references):
    """Target against a set of reference profiles, one row per reading."""
    boards = [r for r in (references or []) if r]
    out = {"boards": len(boards), "rows": [], "reference": None}
    if not boards:
        out["why"] = ("no reference profile was given: the readings are absolute, and "
                     "'is this like the reference' was not answered")
        return out
    for key in COMPARE_KEYS:
        mine = target.get(key)
        theirs = [ref.get(key) for ref in boards
                  if isinstance(ref.get(key), (int, float))]
        if not isinstance(mine, (int, float)) or not theirs:
            out["rows"].append({"reading": key, "target": mine,
                                "reference_median": None,
                                "delta": None,
                                "position": "not comparable here"})
            continue
        ref_median = _median(theirs)
        lower_is_better = key in ("diagonal_share", "crossing_share",
                                  "off_grid_share", "bends_per_net_median",
                                  "vias_per_net_median", "fanout_length_mm_median")
        worse = (mine > ref_median) if lower_is_better else (mine < ref_median)
        out["rows"].append({
            "reading": key, "target": round(mine, 4),
            "reference_median": round(ref_median, 4),
            "delta": round(mine - ref_median, 4),
            "better_when": "lower" if lower_is_better else "higher",
            "verdict": "further from the reference" if worse else "at or better",
        })
    out["further"] = [r["reading"] for r in out["rows"]
                      if r.get("verdict") == "further from the reference"]
    out["reference"] = {"boards": len(boards)}
    return out


def violations(board, limits=None):
    """The segments and nets that break a rule, named so they can be redrawn.

    Defaults come from the anchors in `_discipline`; every one is echoed back with
    the list so a caller can see what was applied. This returns copper, not a plan:
    no tool here routes, and the redraw stays a person's work.
    """
    limits = dict(limits or {})
    box = _bbox(board)
    diagonal = math.hypot(box[2] - box[0], box[3] - box[1]) if box else None
    max_len = limits.get("max_segment_mm")
    if max_len is None and diagonal:
        max_len = CROSSING_SHARE * diagonal
    rows = _segment_table(board)
    bends = _bends(rows)
    max_bends = limits.get("max_bends_per_net")
    min_off_grid = limits.get("diagonal_share_limit", 0.30)

    long_segments = [r for r in rows if max_len and r["length_mm"] > max_len]
    long_segments.sort(key=lambda r: -r["length_mm"])
    off_grid = [r for r in rows if r["off_grid_deg"] > ANGLE_TOL_DEG]
    busy_nets = ([{"net": net, "bends": count}
                  for net, count in sorted(bends.items(), key=lambda kv: -kv[1])]
                 if max_bends is not None else [])
    busy_nets = [n for n in busy_nets if max_bends is not None and n["bends"] > max_bends]
    layers = {}
    for row in rows:
        entry = layers.setdefault(row["layer"], [0, 0, 0, 0])
        entry[row["bucket"]] += 1
    weak_layers = []
    for name, buckets in sorted(layers.items()):
        total = sum(buckets)
        axis = buckets[0] + buckets[2]
        if not total:
            continue
        strength = max(buckets[0], buckets[2]) / float(axis) if axis else None
        if strength is not None and strength < 0.75:
            weak_layers.append({"layer": name, "preference_strength": round(strength, 4),
                                "horizontal": buckets[0], "vertical": buckets[2],
                                "diagonal": buckets[1] + buckets[3]})
    return {
        "board": (getattr(board, "source", None) or {}).get("path"),
        "limits": {"max_segment_mm": round(max_len, 3) if max_len else None,
                   "max_bends_per_net": max_bends,
                   "diagonal_share_limit": min_off_grid,
                   "off_grid_tol_deg": ANGLE_TOL_DEG,
                   "note": "defaults are the crossing share and the discipline "
                           "anchors; pass limits= to use your own"},
        "long_segments": [{"uuid": r["uuid"], "net": r["net"], "layer": r["layer"],
                           "length_mm": round(r["length_mm"], 3),
                           "start": [round(r["start"][0], 3), round(r["start"][1], 3)],
                           "end": [round(r["end"][0], 3), round(r["end"][1], 3)]}
                          for r in long_segments[:200]],
        "long_segment_count": len(long_segments),
        "off_grid_segments": len(off_grid),
        "nets_over_bend_limit": busy_nets[:200],
        "layers_without_a_preferred_direction": weak_layers,
        "how_many_to_redraw": len(long_segments) + len(off_grid),
    }
