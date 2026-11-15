#!/usr/bin/env python
# -*- coding: utf-8 -*-
"""Replace a staircase chain with the two or three lattice legs it is describing.

Measured need: 1470 of the 3910 track segments on the SOM board are shorter than
0.25 mm, and they are not stubs - they are zigzags. A chain reads

    16.778,-32.103 -> 16.833,-32.048 -> 16.833,-31.993 -> 16.943,-31.883 -> ...

which is a 45 degree line walked in 0.055 mm stair steps: 45 up, axis over, 45 up,
axis over. Every step is lattice legal, so `board_shape_gate` has nothing to say
about it, but on a plot the line is a saw edge and a person reading the board sees
"ugly" without being able to name a rule. This tool names it: the chain deviates
from its own chord by less than STAIR_MM, so the chain *is* a line that was written
down badly.

The fix is Douglas-Peucker with a hard deviation budget, then every surviving leg
that is not on the lattice is re-cut into one 45 and one axis leg; chain endpoints
never move, and a cut that would leave an acute corner at either end is refused.
Clearance is walked against every other net's pads, vias and tracks before a single
segment is added - the new copper is straighter, and "straighter" must not mean
"closer".

Only simple chains are touched: a component whose internal joints are all exactly
two-leg, with no arc on the net. Anything else is skipped and counted, because what
"the chain" means there is a judgement call.

Nothing here is a route.
"""
import math
import sys

import pcbnew

MM = pcbnew.ToMM
STAIR_MM = 0.08
CLR = 0.10
TOL = 1.0


def ends(t):
    a, b = t.GetStart(), t.GetEnd()
    return (MM(a.x), MM(a.y)), (MM(b.x), MM(b.y))


def heading(u, v):
    return math.degrees(math.atan2(v[1] - u[1], v[0] - u[0])) % 180.0


def on_lattice(u, v, tol=TOL):
    h = heading(u, v)
    return min(abs(h), abs(h - 45.0), abs(h - 90.0), abs(h - 135.0),
               abs(h - 180.0)) <= tol


def interior(far_a, v, far_b):
    r1 = math.atan2(far_a[1] - v[1], far_a[0] - v[0])
    r2 = math.atan2(far_b[1] - v[1], far_b[0] - v[0])
    d = abs(math.degrees(r1 - r2)) % 360.0
    if d > 180.0:
        d = 360.0 - d
    return d


def not_acute(far_a, v, far_b):
    return interior(far_a, v, far_b) >= 90.0 - 1e-6


def pt_seg(px, py, a, c):
    dx, dy = c[0] - a[0], c[1] - a[1]
    l2 = dx * dx + dy * dy
    if l2 < 1e-18:
        return math.hypot(px - a[0], py - a[1])
    u = max(0.0, min(1.0, ((px - a[0]) * dx + (py - a[1]) * dy) / l2))
    return math.hypot(px - (a[0] + u * dx), py - (a[1] + u * dy))


def dp(points, eps):
    if len(points) < 3:
        return list(points)
    keep = [False] * len(points)
    keep[0] = keep[-1] = True
    stack = [(0, len(points) - 1)]
    while stack:
        i, j = stack.pop()
        a, c = points[i], points[j]
        worst, idx = -1.0, -1
        for k in range(i + 1, j):
            d = pt_seg(points[k][0], points[k][1], a, c)
            if d > worst:
                worst, idx = d, k
        if worst > eps and idx > 0:
            keep[idx] = True
            stack.append((i, idx))
            stack.append((idx, j))
    return [p for p, k in zip(points, keep) if k]


def build_buckets(board, tracks):
    buckets = {}

    def put(kind, key, box):
        x0, y0, x1, y1 = box
        for gx in range(int(math.floor(x0)), int(math.floor(x1)) + 1):
            for gy in range(int(math.floor(y0)), int(math.floor(y1)) + 1):
                buckets.setdefault((gx, gy), []).append((kind, key))

    for f in board.GetFootprints():
        for p in f.Pads():
            try:
                bb = p.GetBoundingBox()
            except Exception:
                continue
            x0, x1 = bb.GetLeft() / 1e6, bb.GetRight() / 1e6
            y0 = min(bb.GetTop(), bb.GetBottom()) / 1e6
            y1 = max(bb.GetTop(), bb.GetBottom()) / 1e6
            put("pad", (p.GetNetname(), x0, y0, x1, y1),
                (x0 - 1, y0 - 1, x1 + 1, y1 + 1))
    for t in board.GetTracks():
        if t.Type() == pcbnew.PCB_VIA_T:
            q = t.GetPosition()
            r = MM(t.GetWidth(pcbnew.F_Cu)) / 2.0
            put("via", (t.GetNetname(), MM(q.x), MM(q.y), r),
                (MM(q.x) - r - 1, MM(q.y) - r - 1, MM(q.x) + r + 1, MM(q.y) + r + 1))
    for t in tracks:
        a, c = ends(t)
        w = MM(t.GetWidth()) / 2.0
        put("seg", (t.GetNetname(), t.GetLayer(), a, c, w),
            (min(a[0], c[0]) - w - 1, min(a[1], c[1]) - w - 1,
             max(a[0], c[0]) + w + 1, max(a[1], c[1]) + w + 1))
    return buckets


def clear_ok(buckets, net, lay, u, v, half):
    need = CLR + half
    ln = math.hypot(v[0] - u[0], v[1] - u[1])
    n = max(1, int(ln / 0.1))
    for k in range(n + 1):
        px = u[0] + (v[0] - u[0]) * k / float(n)
        py = u[1] + (v[1] - u[1]) * k / float(n)
        for kind, key in buckets.get((int(math.floor(px)), int(math.floor(py))), ()):
            if key[0] == net:
                continue
            if kind == "pad":
                _, x0, y0, x1, y1 = key
                dx = max(x0 - px, 0.0, px - x1)
                dy = max(y0 - py, 0.0, py - y1)
                if math.hypot(dx, dy) < need:
                    return False
            elif kind == "via":
                _, vx, vy, r = key
                if math.hypot(vx - px, vy - py) - r < need:
                    return False
            else:
                _, klay, a, c, w2 = key
                if klay != lay:
                    continue
                if pt_seg(px, py, a, c) - w2 < need:
                    return False
    return True


def cut(a, c):
    """Split a->c into lattice legs without moving either end, or None."""
    if on_lattice(a, c):
        return [a, c]
    dx, dy = c[0] - a[0], c[1] - a[1]
    ax, ay = abs(dx), abs(dy)
    if ax < 1e-6 or ay < 1e-6:
        return None
    m = min(ax, ay)
    sx = 1.0 if dx > 0 else -1.0
    sy = 1.0 if dy > 0 else -1.0
    p = (a[0] + sx * m, a[1] + sy * m)
    if not (on_lattice(a, p) and on_lattice(p, c)):
        return None
    if not (not_acute(c, p, a) and not_acute(a, p, c)):
        return None
    return [a, p, c]


def main(argv):
    if len(argv) < 3:
        print("usage: straighten.py BOARD OUT [--apply] [--eps MM] [--max-mm MM]")
        return 2
    src, out = argv[1], argv[2]
    apply_ = "--apply" in argv
    eps = STAIR_MM
    if "--eps" in argv:
        eps = float(argv[argv.index("--eps") + 1])
    maxmm = 6.0
    if "--max-mm" in argv:
        maxmm = float(argv[argv.index("--max-mm") + 1])

    board = pcbnew.LoadBoard(src)
    if board is None:
        raise SystemExit("cannot read %s" % src)
    tracks = [t for t in board.GetTracks() if t.Type() == pcbnew.PCB_TRACE_T]
    arc_nets = set(t.GetNetname() for t in board.GetTracks()
                   if t.Type() == pcbnew.PCB_ARC_T)
    buckets = build_buckets(board, tracks)

    groups = {}
    for t in tracks:
        groups.setdefault((t.GetNetname(), t.GetLayer()), []).append(t)

    def key(p):
        return (round(p[0], 3), round(p[1], 3))

    # A vertex stops a chain when it is not a plain two-leg joint, or when a pad or
    # a via is sitting on it. Chains are the runs between those, so a branched net
    # is handled one arm at a time instead of being skipped whole.
    anchors = set()
    for f in board.GetFootprints():
        for p in f.Pads():
            q = p.GetPosition()
            anchors.add(key((MM(q.x), MM(q.y))))
    for t in board.GetTracks():
        if t.Type() == pcbnew.PCB_VIA_T:
            q = t.GetPosition()
            anchors.add(key((MM(q.x), MM(q.y))))

    rebuilt = skipped = held = 0
    why = {}
    report = []
    for (net, lay), ts in sorted(groups.items()):
        if net in arc_nets:
            skipped += 1
            continue
        adj = {}
        for t in ts:
            a, c = ends(t)
            adj.setdefault(key(a), []).append((t, key(c)))
            adj.setdefault(key(c), []).append((t, key(a)))
        node = set(k for k, v in adj.items() if len(v) != 2) | (anchors & set(adj))
        used = set()
        for n0 in sorted(node):
            for t0, other in adj.get(n0, ()):
                if id(t0) in used:
                    continue
                chain, ctrk, cur, ok = [n0], [t0], other, True
                used.add(id(t0))
                chain.append(cur)
                while cur not in node:
                    nxt = None
                    for t, o in adj.get(cur, ()):
                        if t is t0 or id(t) in used:
                            continue
                        if nxt is not None:
                            ok = False
                            break
                        nxt = (t, o)
                    if not ok or nxt is None:
                        break
                    t, o = nxt
                    used.add(id(t))
                    cur = o
                    chain.append(cur)
                    ctrk.append(t)
                if not ok or len(chain) < 4:
                    continue
                pts = chain
                if any(math.hypot(pts[i + 1][0] - pts[i][0],
                                  pts[i + 1][1] - pts[i][1]) > maxmm
                       for i in range(len(pts) - 1)):
                    held += 1
                    why["long"] = why.get("long", 0) + 1
                    continue
                simple = dp(pts, eps)
                if len(simple) >= len(pts) - 1:
                    continue
                legs, fine = [], True
                for i in range(len(simple) - 1):
                    seg = cut(simple[i], simple[i + 1])
                    if seg is None:
                        fine = False
                        break
                    legs.extend(seg[:-1])
                if not fine:
                    held += 1
                    why["cut"] = why.get("cut", 0) + 1
                    continue
                legs.append(simple[-1])
                half = ts[0].GetWidth() / 2e6
                if not all(clear_ok(buckets, net, lay, legs[i], legs[i + 1], half)
                           for i in range(len(legs) - 1)):
                    held += 1
                    why["clear"] = why.get("clear", 0) + 1
                    continue
                rebuilt += 1
                report.append((net, lay, len(pts) - 1, len(legs) - 1))
                if apply_:
                    for t in ctrk:
                        board.Remove(t)
                    for i in range(len(legs) - 1):
                        nt = pcbnew.PCB_TRACK(board)
                        nt.SetStart(pcbnew.VECTOR2I(
                            int(round(legs[i][0] * 1e6)),
                            int(round(legs[i][1] * 1e6))))
                        nt.SetEnd(pcbnew.VECTOR2I(
                            int(round(legs[i + 1][0] * 1e6)),
                            int(round(legs[i + 1][1] * 1e6))))
                        nt.SetWidth(ts[0].GetWidth())
                        nt.SetLayer(lay)
                        nt.SetNet(ts[0].GetNet())
                        board.Add(nt)

    for net, lay, before, after in report[:20]:
        print("%-18s %-7s %4d segs -> %3d" % (net, lay, before, after))
    print("chains rebuilt %d   chains held %d   groups skipped %d   why %s" % (
        rebuilt, held, skipped, why))
    if not apply_:
        print("dry run (add --apply)")
        return 0
    board.Save(out)
    print("saved -> %s" % out)
    return 0


if __name__ == "__main__":
    sys.exit(main(sys.argv))
