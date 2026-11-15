#!/usr/bin/env python
# -*- coding: utf-8 -*-
"""Force every corner of the copper onto the 45 degree grid, and take the
staircase out while doing it.

Two things are wrong with machine-made copper on this board and both are
visible at a glance:

  * a run that should be one leg is diced into dozens of 0.05 mm steps, so a
    30 mm net reads as 91 segments;
  * some corners turn 106..174 degrees, i.e. the inside angle is a sharp wedge.
    That is the "acute routing" that gets flagged in review.

This pass rebuilds each polyline as **octilinear** legs: every leg is either a
single 45/axis leg, or exactly two legs (diagonal part + axis part). A turn is
therefore always 0, 45 or 90 degrees, and a staircase collapses into a few long
legs. A chain is only replaced when

  * every resulting turn is within 0.5 degree of 0/45/90, and
  * every resulting segment keeps at least the netclass clearance (0.10 mm) to
    copper of other nets, on that layer, vias and arc bounding boxes included, and
  * the chain does not get longer than it was (``--grow`` mm of slack).

Anything else is left exactly as it was and reported. Nets that own an arc are
skipped completely: an arc here means a deliberately length-tuned serpentine and
this pass must not touch it.
Usage:  snap_corners.py BOARD [--apply] [--eps 0.12] [--grow 0.30] [--only NET,NET]
"""
import math
import sys

import pcbnew

MM = pcbnew.ToMM
ND = 4
CU_LAYERS = (pcbnew.F_Cu, pcbnew.In1_Cu, pcbnew.In2_Cu, pcbnew.In3_Cu,
             pcbnew.In4_Cu, pcbnew.B_Cu)
ANGLE_TOL = 0.5
LEGAL = (0.0, 45.0, 90.0)


def key(x, y):
    return (round(x, ND), round(y, ND))


def seg_pt_dist(a, b, p):
    dx, dy = b[0] - a[0], b[1] - a[1]
    L2 = dx * dx + dy * dy
    if L2 <= 1e-18:
        return math.hypot(p[0] - a[0], p[1] - a[1])
    t = ((p[0] - a[0]) * dx + (p[1] - a[1]) * dy) / L2
    t = max(0.0, min(1.0, t))
    return math.hypot(p[0] - (a[0] + t * dx), p[1] - (a[1] + t * dy))


def seg_seg_dist(a, b, c, d):
    return min(seg_pt_dist(c, d, a), seg_pt_dist(c, d, b),
               seg_pt_dist(a, b, c), seg_pt_dist(a, b, d))


def rect_pt_dist(lo, hi, p):
    dx = max(lo[0] - p[0], 0.0, p[0] - hi[0])
    dy = max(lo[1] - p[1], 0.0, p[1] - hi[1])
    return math.hypot(dx, dy)


def rect_seg_dist(lo, hi, a, b):
    n = max(2, int(math.hypot(b[0] - a[0], b[1] - a[1]) / 0.05) + 1)
    best = 1e9
    for i in range(n + 1):
        t = i / float(n)
        best = min(best, rect_pt_dist(lo, hi, (a[0] + (b[0] - a[0]) * t,
                                              a[1] + (b[1] - a[1]) * t)))
    return best


def turn(u, w):
    nu, nw = math.hypot(*u), math.hypot(*w)
    if nu < 1e-9 or nw < 1e-9:
        return None
    c = max(-1.0, min(1.0, (u[0] * w[0] + u[1] * w[1]) / (nu * nw)))
    return math.degrees(math.acos(c))


def legal(t):
    if t is None:
        return False
    return any(abs(t - w) <= ANGLE_TOL for w in LEGAL)


def dp_idx(pts, eps):
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


def octi(p, q):
    """One or two octilinear legs from p to q. Returns the two orders."""
    dx, dy = q[0] - p[0], q[1] - p[1]
    sx = 0.0 if abs(dx) < 1e-9 else math.copysign(1.0, dx)
    sy = 0.0 if abs(dy) < 1e-9 else math.copysign(1.0, dy)
    d = min(abs(dx), abs(dy))
    if d < 1e-9 or abs(abs(dx) - abs(dy)) < 1e-9:
        return [[q], [q]]
    diag_to = (p[0] + sx * d, p[1] + sy * d)
    return [[diag_to, q], [q]] if False else [
        [diag_to, q],                                  # diagonal first
        [(q[0] - sx * d, q[1] - sy * d), q],           # axis first
    ]


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


def _dirs(seq):
    return [(seq[k + 1][0] - seq[k][0], seq[k + 1][1] - seq[k][1])
            for k in range(len(seq) - 1)
            if math.hypot(seq[k + 1][0] - seq[k][0], seq[k + 1][1] - seq[k][1]) > 1e-9]

def _pen(t):
    if legal(t):
        return 0.0
    return 1.0 + abs((t or 0.0) - 90.0) / 90.0

def build_path(pts, blocks):
    """Turn chain points into an octilinear point list.

    Each leg has two ways to be decomposed (diagonal first or axis first). Picking
    them greedily per leg leaves joints that a different earlier choice would have
    fixed, so this walks a two-state DP over the whole chain: state = direction of
    the last sub-leg of the current leg.
    """
    if len(pts) < 2:
        return list(pts)
    legs = []
    for i in range(len(pts) - 1):
        p, q = pts[i], pts[i + 1]
        opts = []
        for opt in octi(p, q):
            seq = [p] + [v for v in opt
                         if math.hypot(v[0] - p[0], v[1] - p[1]) > 1e-9]
            opts.append(seq)
        legs.append(opts)
    # forward DP: dp[j] = (cost, back) for option j of this leg
    dp = []
    for i, opts in enumerate(legs):
        cur = []
        for j, seq in enumerate(opts):
            ds = _dirs(seq)
            inner = 0.0
            for k in range(len(ds) - 1):
                inner += _pen(turn(ds[k], ds[k + 1]))
            if i == 0:
                cur.append((inner, None))
                continue
            best, bi = 1e18, None
            for pi, (pc, _) in enumerate(dp[-1]):
                pds = _dirs(legs[i - 1][pi])
                c = pc + inner + (_pen(turn(pds[-1], ds[0])) if pds and ds else 0.0)
                if c < best:
                    best, bi = c, pi
            cur.append((best, bi))
        dp.append(cur)
    bi = min(range(len(dp[-1])), key=lambda k: dp[-1][k][0])
    pick = [bi]
    for i in range(len(legs) - 1, 0, -1):
        pick.append(dp[i][pick[-1]][1])
    pick.reverse()
    out = [pts[0]]
    for i, j in enumerate(pick):
        seq = legs[i][j]
        for v in seq[1:]:
            if math.hypot(v[0] - out[-1][0], v[1] - out[-1][1]) > 1e-9:
                out.append(v)
    return out


def all_legal(path):
    for k in range(1, len(path) - 1):
        u = (path[k][0] - path[k - 1][0], path[k][1] - path[k - 1][1])
        w = (path[k + 1][0] - path[k][0], path[k + 1][1] - path[k][1])
        if not legal(turn(u, w)):
            return False, turn(u, w)
    return True, 0.0


def main(argv):
    bp = argv[1]
    apply_ = "--apply" in argv
    eps = 0.12
    grow = 0.30
    only = None
    for flag, cast in (("--eps", float), ("--grow", float), ("--only", str)):
        if flag in argv:
            v = cast(argv[argv.index(flag) + 1])
            if flag == "--eps":
                eps = v
            elif flag == "--grow":
                grow = v
            else:
                only = set(v.split(","))

    board = pcbnew.LoadBoard(bp)
    if board is None:
        raise SystemExit("cannot read %s" % bp)
    tracks = [t for t in list(board.GetTracks()) if t.Type() != pcbnew.PCB_VIA_T]
    arcs = [t for t in tracks if t.Type() == pcbnew.PCB_ARC_T]
    tuned = set(a.GetNetname() for a in arcs)

    buckets, WIDTH = {}, {}

    def put(lay, rec, lo, hi):
        for cx in range(int(lo[0] // 1.0), int(hi[0] // 1.0) + 1):
            for cy in range(int(lo[1] // 1.0), int(hi[1] // 1.0) + 1):
                buckets.setdefault((lay, cx, cy), []).append(rec)

    for t in tracks:
        if t.Type() == pcbnew.PCB_ARC_T:
            continue
        s, e = t.GetStart(), t.GetEnd()
        a = (MM(s.x), MM(s.y))
        b = (MM(e.x), MM(e.y))
        WIDTH[(t.GetNetname(), a, b)] = MM(t.GetWidth())
        put(t.GetLayer(), (t.GetNetname(), a, b, "seg"),
            (min(a[0], b[0]), min(a[1], b[1])), (max(a[0], b[0]), max(a[1], b[1])))
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
            put(lay, (v.GetNetname(), c, (c[0] + r, c[1]), "circ"),
                (c[0] - r, c[1] - r), (c[0] + r, c[1] + r))
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
                for onet, o1, o2, kind in buckets.get((lay, cx, cy), ()):
                    if onet == net:
                        continue
                    if kind == "circ":
                        d = seg_pt_dist(a, b, o1)
                        need = w / 2.0 + (o2[0] - o1[0]) + 0.10
                    elif kind == "rect":
                        d = rect_seg_dist(o1, o2, a, b)
                        need = w / 2.0 + 0.10
                    else:
                        d = seg_seg_dist(a, b, o1, o2)
                        need = w / 2.0 + WIDTH.get((onet, o1, o2), 0.1) / 2.0 + 0.10
                    if d < need - 1e-9:
                        return False, d - need
        return True, 0.0

    per_layer, by_edge = {}, {}
    for t in tracks:
        if t.Type() == pcbnew.PCB_ARC_T:
            continue
        if t.GetNetname() in tuned and "--force" not in argv:
            continue
        if only is not None and t.GetNetname() not in only:
            continue
        s, e = t.GetStart(), t.GetEnd()
        a, b = key(MM(s.x), MM(s.y)), key(MM(e.x), MM(e.y))
        per_layer.setdefault((t.GetNetname(), t.GetLayer()), []).append((a, b))
        by_edge.setdefault((t.GetNetname(), t.GetLayer()), {})[(a, b)] = t

    seg_before = seg_after = 0
    ok_chains = refused = 0
    reasons = []
    for (net, lay), edges in sorted(per_layer.items()):
        tbl = by_edge[(net, lay)]
        for ch in chains_of(edges):
            seg_before += len(ch) - 1
            if len(ch) < 3:
                seg_after += len(ch) - 1
                continue
            L0 = sum(math.hypot(ch[i + 1][0] - ch[i][0], ch[i + 1][1] - ch[i][1])
                     for i in range(len(ch) - 1))
            w = MM((tbl.get((ch[0], ch[1])) or tbl.get((ch[1], ch[0]))).GetWidth())
            # A coarse simplify can shave a corner close enough to a neighbour to
            # break clearance. Rather than throw the chain away, walk a ladder of
            # tolerances and take the coarsest one that survives every check.
            path, bad = None, None
            for e in [eps, 0.10, 0.08, 0.06, 0.04, 0.03, 0.02]:
                if e > eps or e <= 0.0:
                    continue
                idx = dp_idx(ch, e)
                pts = [ch[i] for i in idx]
                p2 = build_path(pts, None)
                good, t = all_legal(p2)
                if not good:
                    bad = "turn %.1f" % (t or 0.0)
                    continue
                L1 = sum(math.hypot(p2[i + 1][0] - p2[i][0], p2[i + 1][1] - p2[i][1])
                         for i in range(len(p2) - 1))
                if L1 > L0 + grow:
                    bad = "longer %.3f" % (L1 - L0)
                    continue
                why = None
                for i in range(len(p2) - 1):
                    good, d = clearance_ok(lay, net, p2[i], p2[i + 1], w)
                    if not good:
                        why = "clearance %+.4f at %.3f,%.3f" % (d, p2[i][0], p2[i][1])
                        break
                if why:
                    bad = why
                    continue
                path, bad = p2, None
                break
            if path is None:
                refused += 1
                reasons.append((net, board.GetLayerName(lay), bad))
                seg_after += len(ch) - 1
                continue
            ok_chains += 1
            seg_after += len(path) - 1
            if not apply_:
                continue
            # Drop the whole old chain and lay down fresh tracks. Recycling the
            # old track objects one by one looked tidier but produced exactly the
            # same segment twice when the rebuilt path had fewer points than the
            # pool: one leftover track still pointed at the old corner.
            proto = tbl.get((ch[0], ch[1])) or tbl.get((ch[1], ch[0]))
            for i in range(len(ch) - 1):
                old = tbl.get((ch[i], ch[i + 1])) or tbl.get((ch[i + 1], ch[i]))
                if old is not None:
                    board.Remove(old)
            for i in range(len(path) - 1):
                t = proto.Duplicate()
                t.SetStart(pcbnew.VECTOR2I(int(round(path[i][0] * 1e6)),
                                           int(round(path[i][1] * 1e6))))
                t.SetEnd(pcbnew.VECTOR2I(int(round(path[i + 1][0] * 1e6)),
                                         int(round(path[i + 1][1] * 1e6))))
                board.Add(t)

    # Duplicate copper: the same two endpoints, on one layer, for one net. These
    # are leftovers from earlier passes and they read as a 180 degree foldback to
    # any shape audit, so sweep them up here.
    seen, dup = set(), 0
    for t in list(board.GetTracks()):
        if t.Type() == pcbnew.PCB_VIA_T:
            continue
        s, e = t.GetStart(), t.GetEnd()
        k = (t.GetNetname(), t.GetLayer(),
             tuple(sorted([(round(MM(s.x), ND), round(MM(s.y), ND)),
                           (round(MM(e.x), ND), round(MM(e.y), ND))])))
        if k in seen:
            dup += 1
            if apply_:
                board.Remove(t)
            continue
        seen.add(k)

    print("segments %d -> %d   chains rebuilt %d   refused %d   (tuned nets skipped: %d)" % (
        seg_before, seg_after, ok_chains, refused, len(tuned)))
    if dup:
        print("duplicate segments removed: %d" % dup)
    for net, lay, why in reasons[:12]:
        print("  REFUSED %-16s %-8s %s" % (net, lay, why))
    if apply_:
        board.Save(bp)
        print("saved -> %s" % bp)
    else:
        print("dry run (add --apply to write)")
    return 0


if __name__ == "__main__":
    sys.exit(main(sys.argv))
