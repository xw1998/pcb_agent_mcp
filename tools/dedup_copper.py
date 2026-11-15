#!/usr/bin/env python3
"""Delete copper that is already there twice.  Nothing here is a route.

A board assembled by applying the same routing plan twice ends up with pairs of
segments that agree to a few microns at both ends.  They are not a clearance
problem, they are not an unconnected problem, and the DRC says nothing about
them -- but on screen they read as a fat, doubled, slightly off-centre line,
which is exactly what "the copper looks like shit" means.  So the duplicate is
measured here and the thinner of the two is deleted; the wider one stays, which
is why deleting can never disconnect anything.

Two segments count as the same copper when they carry the same net on the same
layer AND both endpoints agree within --tol.  A segment whose twin is wider is
kept: it is the one carrying the current.

    python tools/dedup_copper.py BOARD [--apply OUT] [--tol 0.02]

Without --apply the board is only measured.  With --apply the board is written
to OUT (never in place) and the unconnected count before and after is printed:
a deletion that changes it is refused and the run stops, because that would not
be a duplicate.
"""
import argparse
import math
import sys

import pcbnew


def segs_of(board):
    out = []
    for t in board.GetTracks():
        if isinstance(t, pcbnew.PCB_VIA):
            continue
        a, c = t.GetStart(), t.GetEnd()
        out.append({"net": t.GetNetname(), "layer": t.GetLayer(),
                    "w": t.GetWidth() / 1e6,
                    "a": (a.x / 1e6, a.y / 1e6),
                    "c": (c.x / 1e6, c.y / 1e6), "obj": t})
    return out


def same(p, q, tol):
    return abs(p[0] - q[0]) <= tol and abs(p[1] - q[1]) <= tol


def find_twins(segs, tol):
    """Every ordered pair (keep, drop) of segments that are the same copper."""
    out = []
    n = len(segs)
    for i in range(n):
        A = segs[i]
        for j in range(i + 1, n):
            B = segs[j]
            if A["net"] != B["net"] or A["layer"] != B["layer"]:
                continue
            if (same(A["a"], B["a"], tol) and same(A["c"], B["c"], tol)) or \
               (same(A["a"], B["c"], tol) and same(A["c"], B["a"], tol)):
                keep, drop = (A, B) if A["w"] >= B["w"] else (B, A)
                out.append((keep, drop))
    return out


def main(argv=None):
    ap = argparse.ArgumentParser()
    ap.add_argument("board")
    ap.add_argument("--apply", metavar="OUT", default=None)
    ap.add_argument("--tol", type=float, default=0.02)
    a = ap.parse_args(argv)

    b = pcbnew.LoadBoard(a.board)
    b.BuildConnectivity()
    uc0 = b.GetConnectivity().GetUnconnectedCount(False)
    segs = segs_of(b)
    twins = find_twins(segs, a.tol)

    drop_ids = set()
    lost = 0.0
    per = {}
    for keep, drop in twins:
        if id(drop["obj"]) in drop_ids:
            continue
        drop_ids.add(id(drop["obj"]))
        L = math.hypot(drop["a"][0] - drop["c"][0], drop["a"][1] - drop["c"][1])
        lost += L
        per[(drop["net"], drop["layer"])] = per.get((drop["net"], drop["layer"]), 0.0) + L

    print("segments %d   duplicate pairs %d   redundant %.2f mm   unconnected %d"
          % (len(segs), len(twins), lost, uc0))
    for (net, lay), L in sorted(per.items(), key=lambda kv: -kv[1])[:20]:
        print("   %-18s %-7s %6.2f mm" % (net, b.GetLayerName(lay), L))
    if not a.apply:
        print("measured only; pass --apply OUT to write")
        return 0
    if not drop_ids:
        print("nothing to delete; board left untouched")
        return 0

    for s in segs:
        if id(s["obj"]) in drop_ids:
            b.Remove(s["obj"])
    b.BuildConnectivity()
    uc1 = b.GetConnectivity().GetUnconnectedCount(False)
    print("unconnected %d -> %d" % (uc0, uc1))
    if uc1 > uc0:
        raise SystemExit("REFUSED: deleting the twins made the board less "
                         "connected (%d -> %d); that is not a duplicate"
                         % (uc0, uc1))
    b.Save(a.apply)
    print("saved %s  (deleted %d segments)" % (a.apply, len(drop_ids)))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
