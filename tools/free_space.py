#!/usr/bin/env python
# -*- coding: utf-8 -*-
"""Print a free-space map of one rectangle on one copper layer.

**Nothing here is a route.**  This tool does not place anything and does not
suggest a path; it prints, cell by cell, how much room there is.  What is done
with that is the operator's job -- the wires on a hand-drawn board come from
coordinates a human wrote down, and this is only the eye.

Per cell centre, the copper distance to the nearest item on that layer is
measured with ``copper_geo``'s real pad shapes (circle / oval / rounded rect /
segment).  The cell is then:

  ``.``  a 0.10 mm track passes with full clearance (>= 0.25 mm to copper edge)
  ``+``  marginal (0.15 .. 0.25 mm), i.e. it fits the 0.10 mm rule but with no
         margin for the next track beside it
  ``#``  wall (< 0.15 mm), a 0.10 mm track with 0.10 mm clearance cannot pass
  ``@``  an operator-supplied mark (net endpoint, keep-out corner, ...)

Rows are printed top-down in board coordinates (y grows upward), so the map
reads the same way as the board does.

Usage:
    free_space.py BOARD.kicad_pcb --layer F.Cu \
        --x0 20 --y0 8 --x1 46 --y1 34 --step 0.5 \
        --mark USB1_DP:45,19 --mark U1.B15:22.6861,32.777
"""
import argparse
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import copper_geo  # noqa: E402


def own_net(name):
    name = name.replace("\\", "/")
    if "/" in name:
        name = name.rsplit("/", 1)[-1]
    return name


def build(board_path, layer, exclude):
    import pcbnew

    board = pcbnew.LoadBoard(board_path)
    items, _holes = copper_geo.build_items(board)
    skip = {own_net(n) for n in exclude}
    pool = [it for it in items
            if layer in it["layers"] and own_net(it["net"]) not in skip]
    return pool


def cell_char(pool, x, y):
    best = 9e9
    for it in pool:
        d = copper_geo.dist_item(it, x, y)
        if d < best:
            best = d
            if best < 0.05:          # deep in copper, nothing else matters
                break
    if best < 0.15:
        return "#"
    return "." if best >= 0.25 else "+"


def main(argv):
    ap = argparse.ArgumentParser(
        description="Print a free-space map of a rectangle on one layer. "
                    "Nothing here is a route.")
    ap.add_argument("board")
    ap.add_argument("--layer", required=True)
    ap.add_argument("--x0", type=float, required=True)
    ap.add_argument("--y0", type=float, required=True)
    ap.add_argument("--x1", type=float, required=True)
    ap.add_argument("--y1", type=float, required=True)
    ap.add_argument("--step", type=float, default=0.5)
    ap.add_argument("--exclude-net", action="append", default=[],
                    help="net(s) to ignore, e.g. --exclude-net /GND")
    ap.add_argument("--mark", action="append", default=[],
                    help='NAME:x,y -- print @ at that cell')
    args = ap.parse_args(argv[1:])

    pool = build(args.board, args.layer, args.exclude_net)

    nx = int(round((args.x1 - args.x0) / args.step)) + 1
    ny = int(round((args.y1 - args.y0) / args.step)) + 1

    marks = set()
    for spec in args.mark:
        _name, coord = spec.split(":", 1)
        mx, my = (float(v) for v in coord.split(","))
        gx = int(round((mx - args.x0) / args.step))
        gy = int(round((my - args.y0) / args.step))
        marks.add((gx, gy))

    print("%s  x %.2f..%.2f  y %.2f..%.2f  step %.2f  items %d  excluded %s"
          % (args.layer, args.x0, args.x1, args.y0, args.y1, args.step,
             len(pool), ",".join(args.exclude_net) or "-"))

    print("       " + "".join(str(int(round(args.x0 + i * args.step)) % 10)
                              for i in range(nx)))
    for j in range(ny - 1, -1, -1):
        y = args.y0 + j * args.step
        cells = []
        for i in range(nx):
            if (i, j) in marks:
                cells.append("@")
            else:
                cells.append(cell_char(pool, args.x0 + i * args.step, y))
        print("%6.2f %s" % (y, "".join(cells)))
    return 0


if __name__ == "__main__":
    sys.exit(main(sys.argv))
