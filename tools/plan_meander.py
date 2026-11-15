#!/usr/bin/env python3
"""Does this net have anywhere to put the length it still owes?

`tune_board` can tell you a net is 8.53 mm short and then fail, and the failure
looks like a meander bug.  It is not: a 45-degree zig-zag adds `2*sqrt(2)*A - 2*A
= 0.828*A` of copper for every `2*A` of path it consumes, i.e. **0.414 mm of extra
length per millimetre of path** - independent of the amplitude.  So the question a
meander cannot answer itself is only ever this one:

    how much of this net's path has room beside it for a tooth of height A?

That is measurable before anything is drawn.  Walk the net's centreline, and at
every step probe perpendicular to it on both sides until foreign copper appears.
A sample can host a tooth if `room >= A + width/2 + clearance`.  Multiply the
good samples by the step and you have `meanderable_mm`; divide `need` by 0.414 and
you have `required_mm`.  `fits` is the comparison, and `blocked_spans` names the
coordinates, because "the DDR area has no room" is only actionable with places.

This file writes nothing and routes nothing.  Run it under KiCad's Python:

    env -u PYTHONPATH -u PYTHONHOME "D:/KiCad/10.0/bin/python.exe" -u \
        tools/plan_meander.py --board B.kicad_pcb --nets DDR --amplitude 0.30

Nets are matched against the net name case-insensitively (`--nets DDR` takes
every net whose name contains DDR; give a comma-separated list for several).
The target for each group is the longest net in that group, which is the same
convention `tune_board` uses for a length group - a longer net is not shortened.
"""
import argparse
import math
import os
import re
import sys

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, HERE)

#: Extra length a 45-degree zig-zag buys per millimetre of path consumed.  Two
#: legs of A*sqrt(2) replacing a base of 2A: (2*sqrt(2)-2)/2 = 0.4142.
GAIN_PER_MM = 2.0 * math.sqrt(2.0) - 2.0
GAIN_PER_MM /= 2.0

#: Probe step along the path, and the largest perpendicular room worth looking
#: at.  A tooth taller than this is not a meander, it is a detour.
STEP_MM = 0.20
MAX_ROOM_MM = 1.50

#: Clearance used when deciding whether a probe point is free.  The board's own
#: netclass clearance is 0.10; the caller can move it with --clearance.
CLR_MM = 0.10


def mm(v):
    return v / 1e6


def build_items(board):
    """Copper items and a 1 mm bucket index, in canonical mm (Y up).

    `copper_geo.build_items` already knows every KiCad pad shape; the bucket index
    is what makes a per-sample probe affordable on a board with 10k items.
    """
    import copper_geo
    items, holes = copper_geo.build_items(board)
    index = {}
    size = 1.0
    for n, it in enumerate(items):
        x0, y0, x1, y1 = _bbox(it)
        pad = MAX_ROOM_MM + it["half"] + 1.0
        for gx in range(int(math.floor((x0 - pad) / size)),
                        int(math.floor((x1 + pad) / size)) + 1):
            for gy in range(int(math.floor((y0 - pad) / size)),
                            int(math.floor((y1 + pad) / size)) + 1):
                index.setdefault((gx, gy), []).append(n)
    return items, holes, index, size


def _bbox(it):
    g = it["geom"]
    if it["kind"] == "disc":
        return g[0], g[1], g[0], g[1]
    if it["kind"] == "seg":
        return min(g[0], g[2]), min(g[1], g[3]), max(g[0], g[2]), max(g[1], g[3])
    # rect: geom = (cx, cy, hx, hy, cos, sin)
    cx, cy, hx, hy = g[0], g[1], g[2], g[3]
    r = math.hypot(hx, hy) + it["half"]
    return cx - r, cy - r, cx + r, cy + r


def near(items, index, size, x, y, radius):
    got = set()
    for gx in range(int(math.floor((x - radius) / size)),
                    int(math.floor((x + radius) / size)) + 1):
        for gy in range(int(math.floor((y - radius) / size)),
                        int(math.floor((y + radius) / size)) + 1):
            got.update(index.get((gx, gy), ()))
    return got


def room_at(items, index, size, x, y, layer, net, clr):
    """Room beside a point on the centreline, in millimetres.

    `min over every foreign item on this layer of dist_item(x, y)` minus the
    clearance, clipped to [0, MAX_ROOM_MM].  This is the distance to the NEAREST
    copper in any direction, so it is a lower bound on the room in one particular
    perpendicular direction - a sample that reads "room 0.20" may in fact have
    more in the direction a tooth would take.  Deliberate: a bound that can only
    under-report keeps `fits` honest.
    """
    import copper_geo
    best = MAX_ROOM_MM
    for n in near(items, index, size, x, y, MAX_ROOM_MM + 0.4):
        it = items[n]
        if it["net"] == net or layer not in it["layers"]:
            continue
        d = copper_geo.dist_item(it, x, y)
        if d < best:
            best = d
    return max(0.0, min(MAX_ROOM_MM, best - clr))


def net_segments(board, net_pat):
    """Segments of every matching net, as (net, layer, width, p1, p2)."""
    import pcbnew
    want = [p.lower() for p in net_pat]
    out = []
    for t in board.GetTracks():
        if isinstance(t, pcbnew.PCB_VIA):
            continue
        name = t.GetNetname()
        low = name.lower()
        if not any(low.find(p) >= 0 for p in want):
            continue
        s, e = t.GetStart(), t.GetEnd()
        out.append((name, pcbnew.LayerName(t.GetLayer()), t.GetWidth() / 1e6,
                    (mm(s.x), -mm(s.y)), (mm(e.x), -mm(e.y))))
    return out


def length_group(net):
    """The length-matching group a net belongs to, derived from its own name.

    A length budget is never global: `DDR_A8` is matched against the address
    group, never against a 33 mm clock.  Comparing every bus net to the longest
    net on the board reads "47/49 short" on a board where the honest answer is
    per-group, so the group key comes from the name: strip a leading `/`, then
    strip the trailing instance bits (`13`, `0P`, `1N`, ...).

        /DDR_DQ13  -> DDR_DQ      /DDR_DQS0P -> DDR_DQS
        /DDR_A8    -> DDR_A       /DDR_BA0   -> DDR_BA
    """
    name = net.lstrip("/")
    return re.sub(r"[0-9]*[PMN]?$", "", name).rstrip("_") or name


def analyse(board, patterns, amplitude, clr=CLR_MM, step=STEP_MM, group_by=None):
    """`group_by="prefix"` splits the matched nets into length groups.

    Default (None) keeps the original single-target behaviour: every matched net
    is compared to the longest one.  That is right when the caller narrowed the
    net set to one group already (what the CLI does with `--nets DDR_A`), and it
    is wrong for `--nets DDR`, so the split has to be asked for.
    """
    import copper_geo                                            # noqa: F401
    items, holes, index, size = build_items(board)
    segs = net_segments(board, patterns)
    by_net = {}
    for name, layer, w, p1, p2 in segs:
        by_net.setdefault(name, []).append((layer, w, p1, p2))

    rows = []
    for name, parts in sorted(by_net.items()):
        length = sum(math.hypot(p2[0] - p1[0], p2[1] - p1[1]) for _, _, p1, p2 in parts)
        width = sorted(w for _, w, _, _ in parts)[len(parts) // 2]
        rows.append({"net": name, "length_mm": round(length, 3),
                     "width_mm": round(width, 3), "segments": len(parts),
                     "group": (length_group(name) if group_by == "prefix" else None)})
    if not rows:
        return {"error": "no net matched %r" % (patterns,), "rows": []}

    targets = {}
    for r in rows:
        g = r["group"] if r["group"] is not None else ""
        targets[g] = max(targets.get(g, 0.0), r["length_mm"])
    for r in rows:
        key = r["group"] if r["group"] is not None else ""
        t = targets[key]
        r["group_target_mm"] = round(t, 3)
        r["need_mm"] = round(t - r["length_mm"], 3)
        r["required_path_mm"] = round(r["need_mm"] / GAIN_PER_MM, 3) if r["need_mm"] > 0 else 0.0
    target = max(r["length_mm"] for r in rows)
    for r in rows:
        r["need_mm"] = round(target - r["length_mm"], 3)
        r["required_path_mm"] = round(r["need_mm"] / GAIN_PER_MM, 3) if r["need_mm"] > 0 else 0.0

    for r in rows:
        if r["need_mm"] <= 0:
            r.update({"meanderable_mm": None, "fits": True, "blocked_spans": [],
                      "note": "longest net in the group; a meander never shortens it"})
            continue
        name = r["net"]
        w = r["width_mm"]
        need_room = amplitude + w / 2.0 + clr
        good = 0.0
        bad = []
        for layer, sw, p1, p2 in by_net[name]:
            seg_len = math.hypot(p2[0] - p1[0], p2[1] - p1[1])
            if seg_len < 1e-6:
                continue
            ux, uy = (p2[0] - p1[0]) / seg_len, (p2[1] - p1[1]) / seg_len
            n = max(1, int(seg_len / step))
            for i in range(n):
                t = (i + 0.5) / n
                x, y = p1[0] + (p2[0] - p1[0]) * t, p1[1] + (p2[1] - p1[1]) * t
                room = room_at(items, index, size, x, y, layer, name, clr)
                if room >= need_room:
                    good += seg_len / n
                else:
                    bad.append((round(x, 3), round(y, 3), round(room, 3)))
        r["meanderable_mm"] = round(good, 3)
        r["fits"] = bool(good >= r["required_path_mm"])
        r["room_short_mm"] = round(max(0.0, r["required_path_mm"] - good), 3)
        r["need_room_mm"] = round(need_room, 3)
        # collapse the blocked samples into spans: keep the first few with room
        # below what a tooth needs, because their coordinates are the fix list
        r["blocked_spans"] = bad[:12]
        r["blocked_samples"] = len(bad)

    return {
        "amplitude_mm": amplitude,
        "clearance_mm": clr,
        "gain_per_mm": round(GAIN_PER_MM, 4),
        "how": ("a 45-degree zig-zag buys %.4f mm of copper per mm of path, whatever "
                "the amplitude; a sample can host a tooth only if the perpendicular "
                "room beside it is at least amplitude + width/2 + clearance"
                % GAIN_PER_MM),
        "target_mm": round(max(r["length_mm"] for r in rows), 3),
        "groups": ({k: round(v, 3) for k, v in sorted(targets.items()) if k}
                   if group_by else {}),
        "group_count": (len([k for k in targets if k]) if group_by else 1),
        "group_by": group_by,
        "rows": rows,
        "not_fitting": [r["net"] for r in rows if not r["fits"]],
    }


def main(argv=None):
    ap = argparse.ArgumentParser(description=__doc__,
                                 formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--board", required=True)
    ap.add_argument("--nets", required=True, help="substring(s), comma separated")
    ap.add_argument("--amplitude", type=float, default=0.30,
                    help="tooth height proposed (mm); 0.30 clears a 0.10 pitch")
    ap.add_argument("--clearance", type=float, default=CLR_MM)
    ap.add_argument("--step", type=float, default=STEP_MM)
    ap.add_argument("--group-by-prefix", action="store_true",
                    help="split the matched nets into length groups by name "
                         "prefix (DDR_DQ13 -> DDR_DQ); use with --nets DDR")
    ap.add_argument("--json", action="store_true")
    a = ap.parse_args(argv)

    import pcbnew
    import json
    board = pcbnew.LoadBoard(a.board)
    pats = [p.strip() for p in a.nets.split(",") if p.strip()]
    got = analyse(board, pats, a.amplitude, clr=a.clearance, step=a.step,
                  group_by=("prefix" if a.group_by_prefix else None))
    if a.json:
        print(json.dumps(got, indent=2, ensure_ascii=False))
        return 0
    if got.get("error"):
        print(got["error"])
        return 2
    print("target %.3f mm (longest net in the group)   gain %.4f mm/mm   tooth %.2f mm"
          % (got["target_mm"], got["gain_per_mm"], got["amplitude_mm"]))
    print()
    print("%-16s %8s %7s %8s %10s %10s %6s" %
          ("net", "length", "need", "path_req", "meanderable", "room_short", "fits"))
    for r in sorted(got["rows"], key=lambda r: -(r.get("room_short_mm") or 0)):
        print("%-16s %8.3f %7.3f %8.3f %10s %10s %6s"
              % (r["net"], r["length_mm"], r["need_mm"], r["required_path_mm"],
                 "-" if r["meanderable_mm"] is None else "%.3f" % r["meanderable_mm"],
                 "-" if not r.get("room_short_mm") else "%.3f" % r["room_short_mm"],
                 "yes" if r["fits"] else "NO"))
    print()
    print("not fitting: %d of %d" % (len(got["not_fitting"]), len(got["rows"])))
    for r in got["rows"]:
        if r["fits"]:
            continue
        print("  %-16s blocked at %s (room < %.3f mm)"
              % (r["net"], r["blocked_spans"][:3], r["need_room_mm"]))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
