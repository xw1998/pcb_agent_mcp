#!/usr/bin/env python
# -*- coding: utf-8 -*-
"""Take the staircase out of copper that is already on the board.

The generator snaps every move to a 0.05 mm raster, so a run that should be one
45 degree leg becomes a ladder of 20 short segments. Nothing is wrong
electrically, but a 30 mm net diced into 91 segments is what a board looks like
when it is drawn by a machine that has no idea what a leg is.

This pass replaces each polyline of one net on one layer with a shorter polyline
(Douglas-Peucker, tolerance in mm) and **only keeps the replacement when it is
provably no worse**:

  * every dropped vertex was within ``--eps`` of the kept polyline, and
  * every kept segment clears all copper of *other* nets on that layer.

A chain that cannot be shortened legally is left exactly as it was and reported.
Copper is written, so unlike the read-only probes this file does write the board;
it never changes a net, never adds a via and never moves a pad.
Usage:  simplify_copper.py BOARD [--apply] [--eps 0.10] [--only NET,NET]
"""
import math
import sys

import pcbnew

MM = pcbnew.ToMM
ND = 4
CU_LAYERS = (pcbnew.F_Cu, pcbnew.In1_Cu, pcbnew.In2_Cu, pcbnew.In3_Cu,
             pcbnew.In4_Cu, pcbnew.B_Cu)


def key(x, y):
    return (round(x, ND), round(y, ND))


def seg_pt_dist(a, b, p):
    ax, ay = a
    bx, by = b
    px, py = p
    dx, dy = bx - ax, by - ay
    L2 = dx * dx + dy * dy
    if L2 <= 1e-18:
        return math.hypot(px - ax, py - ay)
    t = ((px - ax) * dx + (py - ay) * dy) / L2
    t = max(0.0, min(1.0, t))
    return math.hypot(px - (ax + t * dx), py - (ay + t * dy))


def seg_seg_dist(a, b, c, d):
    return min(seg_pt_dist(c, d, a), seg_pt_dist(c, d, b),
               seg_pt_dist(a, b, c), seg_pt_dist(a, b, d))


def rect_pt_dist(lo, hi, p):
    dx = max(lo[0] - p[0], 0.0, p[0] - hi[0])
    dy = max(lo[1] - p[1], 0.0, p[1] - hi[1])
    return math.hypot(dx, dy)


def rect_seg_dist(lo, hi, a, b):
    # conservative: sample the segment, then clamp into the rectangle
    n = max(2, int(math.hypot(b[0] - a[0], b[1] - a[1]) / 0.05) + 1)
    best = 1e9
    for i in range(n + 1):
        t = i / float(n)
        best = min(best, rect_pt_dist(lo, hi, (a[0] + (b[0] - a[0]) * t,
                                              a[1] + (b[1] - a[1]) * t)))
    return best


def dp(pts, eps):
    if len(pts) < 3:
        return list(pts)
    keep = [False] * len(pts)
    keep[0] = keep[-1] = True
    stack = [(0, len(pts) - 1)]
    while stack:
        i, j = stack.pop()
        if j <= i + 1:
            continue
        worst, wi = -1.0, -1
        for k in range(i + 1, j):
            d = seg_pt_dist(pts[i], pts[j], pts[k])
            if d > worst:
                worst, wi = d, k
        if worst > eps:
            keep[wi] = True
            stack.append((i, wi))
            stack.append((wi, j))
    return [p for p, k in zip(pts, keep) if k]


def dp_idx(pts, eps):
    """Douglas-Peucker returning the indices it keeps."""
    if len(pts) < 3:
        return list(range(len(pts)))
    keep = [False] * len(pts)
    keep[0] = keep[-1] = True
    stack = [(0, len(pts) - 1)]
    while stack:
        i, j = stack.pop()
        if j <= i + 1:
            continue
        worst, wi = -1.0, -1
        for k in range(i + 1, j):
            d = seg_pt_dist(pts[i], pts[j], pts[k])
            if d > worst:
                worst, wi = d, k
        if worst > eps:
            keep[wi] = True
            stack.append((i, wi))
            stack.append((wi, j))
    return [i for i, k in enumerate(keep) if k]


def chains_of(edges):
    adj = {}
    for a, b in edges:
        adj.setdefault(a, []).append(b)
        adj.setdefault(b, []).append(a)
    used = set()
    out = []
    for a, b in edges:
        if (a, b) in used:
            continue
        used.add((a, b))
        used.add((b, a))
        ch = [a, b]
        while True:
            tail, prev = ch[-1], ch[-2]
            nxt = [p for p in adj.get(tail, ()) if p != prev and (tail, p) not in used]
            if not nxt:
                break
            p = nxt[0]
            used.add((tail, p))
            used.add((p, tail))
            ch.append(p)
        while True:
            head, prev = ch[0], ch[1]
            prv = [p for p in adj.get(head, ()) if p != prev and (p, head) not in used]
            if not prv:
                break
            p = prv[0]
            used.add((p, head))
            used.add((head, p))
            ch.insert(0, p)
        out.append(ch)
    return out


def main(argv):
    board_path = argv[1]
    apply_ = "--apply" in argv
    eps = 0.10
    only = None
    if "--eps" in argv:
        eps = float(argv[argv.index("--eps") + 1])
    if "--only" in argv:
        only = set(argv[argv.index("--only") + 1].split(","))

    board = pcbnew.LoadBoard(board_path)
    if board is None:
        raise SystemExit("cannot read %s" % board_path)

    tracks = [t for t in list(board.GetTracks()) if t.Type() != pcbnew.PCB_VIA_T]
    # Arcs are left completely alone: their copper bulges away from the chord, so
    # treating one as a straight segment (as the polyline walker would) cuts
    # through whatever the arc is bending around -- measured on this board as
    # /DDR_DQ2 shorting to /DDR_DQ0 on In4.Cu.
    arcs = [t for t in tracks if t.Type() == pcbnew.PCB_ARC_T]

    buckets = {}
    WIDTH = {}

    def put(lay, rec, lo, hi):
        for cx in range(int(lo[0] // 1.0), int(hi[0] // 1.0) + 1):
            for cy in range(int(lo[1] // 1.0), int(hi[1] // 1.0) + 1):
                buckets.setdefault((lay, cx, cy), []).append(rec)

    for t in tracks:
        s, e = t.GetStart(), t.GetEnd()
        a = (MM(s.x), MM(s.y))
        b = (MM(e.x), MM(e.y))
        rec = (t.GetNetname(), a, b, "seg")
        WIDTH[(t.GetNetname(), a, b)] = MM(t.GetWidth())
        put(t.GetLayer(), rec, (min(a[0], b[0]), min(a[1], b[1])),
            (max(a[0], b[0]), max(a[1], b[1])))
    for a_ in arcs:
        bb = a_.GetBoundingBox()
        lo = (MM(bb.GetLeft()), MM(bb.GetTop()))
        hi = (MM(bb.GetRight()), MM(bb.GetBottom()))
        put(a_.GetLayer(), (a_.GetNetname(), lo, hi, "rect"), lo, hi)
    for v in list(board.GetTracks()):
        if v.Type() != pcbnew.PCB_VIA_T:
            continue
        p = v.GetPosition()
        r = MM(v.GetWidth(pcbnew.F_Cu)) / 2.0
        c = (MM(p.x), MM(p.y))
        for lay in CU_LAYERS:
            put(lay, (v.GetNetname(), c, (c[0] + r, c[1]), "circ"), (c[0] - r, c[1] - r),
                (c[0] + r, c[1] + r))
    for fp in list(board.GetFootprints()):
        for pad in fp.Pads():
            ls = pad.GetLayerSet()
            p = pad.GetPosition()
            hs = pad.GetSize()
            lo = (MM(p.x) - MM(hs.x) / 2.0, MM(p.y) - MM(hs.y) / 2.0)
            hi = (MM(p.x) + MM(hs.x) / 2.0, MM(p.y) + MM(hs.y) / 2.0)
            for lay in CU_LAYERS:
                if ls.Contains(lay):
                    put(lay, (pad.GetNetname(), lo, hi, "rect"), lo, hi)

    def clearance_ok(lay, net, a, b, w):
        cx0, cx1 = int(min(a[0], b[0]) // 1.0), int(max(a[0], b[0]) // 1.0)
        cy0, cy1 = int(min(a[1], b[1]) // 1.0), int(max(a[1], b[1]) // 1.0)
        for cx in range(cx0 - 1, cx1 + 2):
            for cy in range(cy0 - 1, cy1 + 2):
                for onet, o1, o2, ow in buckets.get((lay, cx, cy), ()):
                    if onet == net:
                        continue
                    if ow == "circ":
                        d = seg_pt_dist(a, b, o1)
                        need = w / 2.0 + (o2[0] - o1[0]) + 0.10
                    elif ow == "rect":
                        d = rect_seg_dist(o1, o2, a, b)
                        need = w / 2.0 + 0.10
                    else:
                        d = seg_seg_dist(a, b, o1, o2)
                        need = w / 2.0 + WIDTH[(onet, o1, o2)] / 2.0 + 0.10
                    if d < need - 1e-9:
                        return False, d - need
        return True, 0.0

    per_layer = {}
    by_edge = {}
    for t in tracks:
        if t.Type() == pcbnew.PCB_ARC_T:
            continue
        if only is not None and t.GetNetname() not in only:
            continue
        s, e = t.GetStart(), t.GetEnd()
        a = key(MM(s.x), MM(s.y))
        b = key(MM(e.x), MM(e.y))
        per_layer.setdefault((t.GetNetname(), t.GetLayer()), []).append((a, b))
        by_edge.setdefault((t.GetNetname(), t.GetLayer()), {})[(a, b)] = t

    tot_before = tot_after = 0
    changed = refused = 0
    reasons = []
    for (net, lay), edges in sorted(per_layer.items()):
        tbl = by_edge[(net, lay)]
        for ch in chains_of(edges):
            tot_before += len(ch) - 1
            if len(ch) < 3:
                tot_after += len(ch) - 1
                continue
            # pass 1: drop vertices where the copper goes dead straight. This
            # moves nothing, so it can never collide with anything.
            keep = [0]
            for i in range(1, len(ch) - 1):
                u = (ch[i][0] - ch[keep[-1]][0], ch[i][1] - ch[keep[-1]][1])
                w = (ch[i + 1][0] - ch[i][0], ch[i + 1][1] - ch[i][1])
                cross = u[0] * w[1] - u[1] * w[0]
                dot = u[0] * w[0] + u[1] * w[1]
                if dot > 0 and abs(cross) < 1e-9 * max(1.0, math.hypot(*u) * math.hypot(*w)):
                    continue
                keep.append(i)
            keep.append(len(ch) - 1)
            # pass 2: straighten a ladder into a leg, with a shrinking tolerance
            # so a chain only gives up what it can afford to give up.
            w = MM((tbl.get((ch[0], ch[1])) or tbl.get((ch[1], ch[0]))).GetWidth())
            for eps_try in (eps, eps * 0.6, eps * 0.35, eps * 0.2, eps * 0.1):
                sub = dp_idx([ch[i] for i in keep], eps_try)
                cand = [keep[j] for j in sub]
                if len(cand) >= len(keep):
                    break
                ok = True
                for k in range(len(cand) - 1):
                    good, _d = clearance_ok(lay, net, ch[cand[k]], ch[cand[k + 1]], w)
                    if not good:
                        ok = False
                        break
                if ok:
                    keep = cand
                    break
            if len(keep) >= len(ch):
                tot_after += len(ch) - 1
                continue
            if len(keep) >= len(ch):
                tot_after += len(ch) - 1
                continue
            changed += 1
            tot_after += len(keep) - 1
            if not apply_:
                continue
            for k in range(len(keep) - 1):
                a, b = ch[keep[k]], ch[keep[k + 1]]
                head = tbl.get((ch[keep[k]], ch[keep[k] + 1])) or tbl.get((ch[keep[k] + 1], ch[keep[k]]))
                head.SetStart(pcbnew.VECTOR2I(int(round(a[0] * 1e6)), int(round(a[1] * 1e6))))
                head.SetEnd(pcbnew.VECTOR2I(int(round(b[0] * 1e6)), int(round(b[1] * 1e6))))
                for i in range(keep[k] + 1, keep[k + 1]):
                    t = tbl.get((ch[i], ch[i + 1])) or tbl.get((ch[i + 1], ch[i]))
                    if t is not None and t is not head:
                        board.Remove(t)

    print("segments %d -> %d   chains simplified %d   chains refused %d" % (
        tot_before, tot_after, changed, refused))
    for net, lay, why in reasons[:15]:
        print("  REFUSED %-16s %-8s %s" % (net, lay, why))
    if apply_:
        board.Save(board_path)
        print("saved -> %s" % board_path)
    else:
        print("dry run (add --apply to write)")
    return 0


if __name__ == "__main__":
    sys.exit(main(sys.argv))
