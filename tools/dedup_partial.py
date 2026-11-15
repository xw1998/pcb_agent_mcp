#!/usr/bin/env python3
"""Delete copper that is drawn twice *on top of itself*, not end-to-end twice.
Nothing here is a route.

`dedup_copper.py` catches the easy copy: same net, same layer, both endpoints
agree within 0.02 mm.  What it cannot see is the other copy the router leaves
behind -- one segment that is a *prefix* of another, e.g.

    /GND  F.Cu  (35.000,19.910) -> (35.200,19.910)   w 0.10
    /GND  F.Cu  (35.000,19.910) -> (35.450,19.910)   w 0.10

The two share a start and one is strictly inside the other.  No endpoint pair
matches, so the duplicate audit reports nothing -- but `board_shape_gate` sees
two rays leaving the same vertex in the same direction and calls it a **0 degree
corner**, which is what an acute corner looks like when the two legs are
collinear.  It is also real, doubled copper on the same path.

A sub-segment is dropped only when all of the following hold:

  * same net and same layer,
  * every endpoint of the short one lies on the long one within --tol,
  * the short one is strictly shorter than the long one (length, not extent,
    because the two may be collinear in exactly opposite directions),
  * dropping it leaves no thinner copper behind: the kept segment is at least
    as wide, so the copper that carries the current is the one that stays.

Deletion can still disconnect something (the sub-segment's free end may be the
only thing reaching a via), so the unconnected count is measured before and
after and a run that makes it worse is refused and not written at all.

    python tools/dedup_partial.py BOARD [--apply OUT] [--tol 0.02]

Without --apply nothing is written.
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
        if t.Type() == pcbnew.PCB_ARC_T:
            continue
        a, c = t.GetStart(), t.GetEnd()
        out.append({"net": t.GetNetname(), "layer": t.GetLayer(),
                    "w": t.GetWidth() / 1e6,
                    "a": (a.x / 1e6, a.y / 1e6),
                    "c": (c.x / 1e6, c.y / 1e6), "obj": t})
    return out


def _length(s):
    return math.hypot(s["c"][0] - s["a"][0], s["c"][1] - s["a"][1])


def _on_seg(p, s, tol):
    """Is p on segment s (within tol of the line, projection inside)? """
    ax, ay = s["a"]
    bx, by = s["c"]
    dx, dy = bx - ax, by - ay
    l2 = dx * dx + dy * dy
    if l2 < 1e-12:
        return math.hypot(p[0] - ax, p[1] - ay) <= tol
    u = ((p[0] - ax) * dx + (p[1] - ay) * dy) / l2
    if u < -tol / math.sqrt(l2) or u > 1.0 + tol / math.sqrt(l2):
        return False
    px = ax + u * dx
    py = ay + u * dy
    return math.hypot(p[0] - px, p[1] - py) <= tol


def find_contained(segs, tol):
    """(keep, drop) pairs where drop is a sub-segment of keep."""
    out = []
    refused = []
    n = len(segs)
    for i in range(n):
        A = segs[i]
        for j in range(n):
            if i == j:
                continue
            B = segs[j]
            if A["net"] != B["net"] or A["layer"] != B["layer"]:
                continue
            la, lb = _length(A), _length(B)
            if la >= lb - tol:
                continue
            if not (_on_seg(A["a"], B, tol) and _on_seg(A["c"], B, tol)):
                continue
            if B["w"] < A["w"] - 1e-9:
                refused.append((A, B, "kept copper would be thinner"))
                continue
            out.append((B, A))
    # A segment may be a sub-segment of two others; drop it once.
    seen = set()
    uniq = []
    for keep, drop in out:
        if id(drop["obj"]) in seen:
            continue
        seen.add(id(drop["obj"]))
        uniq.append((keep, drop))
    return uniq, refused


def main(argv=None):
    ap = argparse.ArgumentParser()
    ap.add_argument("board")
    ap.add_argument("--apply", metavar="OUT", default=None)
    ap.add_argument("--tol", type=float, default=0.01)
    a = ap.parse_args(argv)

    b = pcbnew.LoadBoard(a.board)
    b.BuildConnectivity()
    uc0 = b.GetConnectivity().GetUnconnectedCount(False)
    segs = segs_of(b)
    pairs, refused = find_contained(segs, a.tol)

    per = {}
    lost = 0.0
    for keep, drop in pairs:
        L = _length(drop)
        lost += L
        per[(drop["net"], drop["layer"])] = per.get((drop["net"], drop["layer"]), 0.0) + L

    print("segments %d   sub-segment pairs %d   redundant %.2f mm   unconnected %d"
          % (len(segs), len(pairs), lost, uc0))
    for (net, lay), L in sorted(per.items(), key=lambda kv: -kv[1])[:20]:
        print("   %-18s %-7s %6.2f mm" % (net, b.GetLayerName(lay), L))
    for A, B, why in refused[:10]:
        print("   refused %-16s %-7s short (%.3f,%.3f)->(%.3f,%.3f) w %.3f vs "
              "long w %.3f : %s"
              % (A["net"], b.GetLayerName(A["layer"]),
                 A["a"][0], A["a"][1], A["c"][0], A["c"][1], A["w"], B["w"], why))

    if not a.apply:
        print("measured only; pass --apply OUT to write")
        return 0
    if not pairs:
        print("nothing to delete; board left untouched")
        return 0

    for keep, drop in pairs:
        b.Remove(drop["obj"])
    b.BuildConnectivity()
    uc1 = b.GetConnectivity().GetUnconnectedCount(False)
    print("unconnected %d -> %d" % (uc0, uc1))
    if uc1 > uc0:
        raise SystemExit("REFUSED: dropping the sub-segments made the board less "
                         "connected (%d -> %d); they were carrying something"
                         % (uc0, uc1))
    b.Save(a.apply)
    print("saved %s  (deleted %d segments)" % (a.apply, len(pairs)))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
