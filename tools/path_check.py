#!/usr/bin/env python
# -*- coding: utf-8 -*-
"""Check a polyline that a human typed, against the copper already on the board.

**Nothing here is a route.**  There is no search, no cost function, no grid
expansion: the vertex list is an input, this tool only answers "is this
polyline legal".  Wires are placed by the operator writing coordinates; this
script is the ruler and the eye, not the hand.

What it reports, per segment:

  * the segment angle, and whether it sits on the 45 degree grid
    ({0, 45, 90, 135} -- every copper segment on a hand-drawn board is one of
    these four),
  * the minimum copper clearance along the centreline, sampled every
    ``--step`` mm, measured to the nearest foreign copper on the same layer
    (the net's own copper, and copper on other layers, are excluded),
  * the blocked runs, collapsed to intervals and printed with the real
    ``(x,y)`` they span, so the operator can see which vertex has to move.

Clearance is decided with ``copper_geo``'s real pad shapes (circle / oval /
rounded rect / segment) rather than bounding boxes, because a bounding box on
a 0.85 mm round pad reports hits that do not exist and misses corners that do.

Usage:
    path_check.py BOARD.kicad_pcb --net /USB1_DP --layer F.Cu --width 0.10 \
        --points "41.70,33.058 41.70,21.30 44.00,19.00 45.00,19.00"

Exit code: 0 when every segment is on the 45 grid and no sample is short,
1 when anything is short or off-grid, 2 on usage error.
"""
import argparse
import json
import math
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import copper_geo  # noqa: E402  (needs the sys.path line above)

OFF_GRID = (0.0, 45.0, 90.0, 135.0, 180.0)
ANGLE_TOL = 0.05          # degrees; 1e-3 mm of drift over 1 mm is 0.06 deg


def own_net(name):
    """Normalise a net name for comparison.

    The shell may hand us a Windows path ('D:/x/USB1_DP') when the argument
    starts with a slash, so keep the last path component.  The leading '/'
    that KiCad puts on every net name is dropped.
    """
    name = name.replace("\\", "/")
    if "/" in name:
        name = name.rsplit("/", 1)[-1]
    return name


def parse_points(text):
    out = []
    for chunk in text.replace(";", " ").split():
        x, y = chunk.split(",")
        out.append((float(x), float(y)))
    return out


def segment_angle(a, c):
    """Segment direction folded into 0..180 degrees."""
    return math.degrees(math.atan2(c[1] - a[1], c[0] - a[0])) % 180.0


def on_grid(ang):
    return min(abs(ang - k) for k in OFF_GRID) < ANGLE_TOL


def collapse(offsets, gap=0.15):
    """Collapse a sorted list of blocked sample offsets into runs."""
    if not offsets:
        return []
    runs = []
    start = prev = offsets[0]
    for v in offsets[1:]:
        if v - prev > gap:
            runs.append((start, prev))
            start = v
        prev = v
    runs.append((start, prev))
    return runs


def check(board_path, net, layer, width, points, need=0.10, step=0.10):
    import pcbnew

    board = pcbnew.LoadBoard(board_path)
    items, _holes = copper_geo.build_items(board)
    own = own_net(net)
    pool = [it for it in items
            if layer in it["layers"] and own_net(it["net"]) != own]

    rows, violations = [], 0
    for i in range(len(points) - 1):
        a, c = points[i], points[i + 1]
        length = math.dist(a, c)
        ang = segment_angle(a, c)
        grid_ok = on_grid(ang)
        n = max(1, int(length / step))
        blocked, best = [], 9e9
        for k in range(n + 1):
            t = k / n
            px = a[0] + (c[0] - a[0]) * t
            py = a[1] + (c[1] - a[1]) * t
            d = 9e9
            for it in pool:
                v = copper_geo.dist_item(it, px, py) - width / 2.0
                if v < d:
                    d = v
                    if d < -1.0:        # deep inside something; stop scanning
                        break
            if d < need:
                blocked.append(t * length)
            if d < best:
                best = d
        runs = []
        for r in collapse(blocked):
            f0, f1 = r[0] / length, r[1] / length
            runs.append({
                "from": [round(a[0] + (c[0] - a[0]) * f0, 4),
                         round(a[1] + (c[1] - a[1]) * f0, 4)],
                "to": [round(a[0] + (c[0] - a[0]) * f1, 4),
                       round(a[1] + (c[1] - a[1]) * f1, 4)],
            })
        rows.append({
            "i": i,
            "from": list(a),
            "to": list(c),
            "length_mm": round(length, 4),
            "angle_deg": round(ang, 3),
            "on_45_grid": grid_ok,
            "min_clearance_mm": round(best, 4),
            "blocked_samples": len(blocked),
            "samples": n + 1,
            "blocked_runs": runs,
        })
        if runs:
            violations += 1
        if not grid_ok:
            violations += 1

    return {
        "board": os.path.basename(board_path),
        "net": own,
        "layer": layer,
        "width_mm": width,
        "need_mm": need,
        "step_mm": step,
        "segments": rows,
        "violations": violations,
    }


def main(argv):
    ap = argparse.ArgumentParser(
        description="Check a hand-typed polyline against a board's copper. "
                    "Nothing here is a route.")
    ap.add_argument("board")
    ap.add_argument("--net", required=True)
    ap.add_argument("--layer", required=True,
                    help="F.Cu / In1.Cu / In2.Cu / In3.Cu / In4.Cu / B.Cu")
    ap.add_argument("--width", type=float, required=True, help="trace width, mm")
    ap.add_argument("--points", required=True,
                    help='"x0,y0 x1,y1 ..." in board mm coordinates, y positive')
    ap.add_argument("--need", type=float, default=0.10,
                    help="required copper clearance, mm (default 0.10)")
    ap.add_argument("--step", type=float, default=0.10,
                    help="centreline sampling step, mm (default 0.10)")
    ap.add_argument("--json", metavar="OUT.json")
    args = ap.parse_args(argv[1:])

    pts = parse_points(args.points)
    if len(pts) < 2:
        print("need at least two points", file=sys.stderr)
        return 2

    res = check(args.board, args.net, args.layer, args.width, pts,
                need=args.need, step=args.step)

    print("net %s  layer %s  width %.3f  segments %d  need %.3f"
          % (res["net"], res["layer"], res["width_mm"],
             len(res["segments"]), res["need_mm"]))
    for r in res["segments"]:
        print("  %2d %8.3f mm  ang %6.2f %s  min %+.3f  blocked %d/%d"
              % (r["i"], r["length_mm"], r["angle_deg"],
                 "OK " if r["on_45_grid"] else "BAD",
                 r["min_clearance_mm"], r["blocked_samples"], r["samples"]))
        for run in r["blocked_runs"]:
            print("        blocked (%8.4f,%8.4f)-(%.4f,%.4f)"
                  % (run["from"][0], run["from"][1],
                     run["to"][0], run["to"][1]))
    print("violations", res["violations"])

    if args.json:
        with open(args.json, "w", encoding="utf-8") as fh:
            json.dump(res, fh, indent=1, ensure_ascii=False)
    return 0 if res["violations"] == 0 else 1


if __name__ == "__main__":
    sys.exit(main(sys.argv))
