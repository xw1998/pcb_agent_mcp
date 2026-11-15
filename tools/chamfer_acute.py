#!/usr/bin/env python
# -*- coding: utf-8 -*-
"""Chamfer an acute copper corner into two 135 degree corners.

Measured need: `board_shape_gate` flags an acute interior corner on the SOM board
19 times, and every one of them sits at a joint where a hand placed corner meets a
pad fan-out. An acute corner is an acid trap: the etchant cannot get in, the
corner under-cuts, and no amount of re-running the router fixes it because the
corner is a *vertex* shared by two segments that both have to keep their far ends.

The fix does not move either far end and does not move the corner. It shortens
both legs by d1 and d2 along their own directions and joins the two new points
with one extra segment. The joint is then three corners, all obtuse, and the net
still connects because every new point lies on copper that was already there.

d1 and d2 are not free: the joining segment has to be lattice legal (0/45/90/135)
too, or the chamfer would just replace an acute corner with an off-45 segment.
So the search walks d1, d2 on a 0.05 mm grid inside the legs and keeps the
smallest pair whose join lands on the lattice and whose new corners are all
90/135/180. Clearance is walked, not assumed, on a 1 mm bucket grid of every
other net's pads, vias and tracks - the chamfer is the only new copper.

Vertices with three or more same-net segments are reported and left alone: what
"the corner" means there is a judgement call, and a gate that guesses is worse
than a gate that refuses.

Nothing here is a route.
"""
import math
import sys

import pcbnew

MM = pcbnew.ToMM
GRID = 0.05
MAX_D = 1.20
CLR = 0.10
TOL = 1.0
ACUTE_TOL = 1.0
DIRS = ((1, 0), (1, 1), (0, 1), (-1, 1),
        (-1, 0), (-1, -1), (0, -1), (1, -1))


def ends(t):
    a, b = t.GetStart(), t.GetEnd()
    return (MM(a.x), MM(a.y)), (MM(b.x), MM(b.y))


def heading(u, v):
    return math.degrees(math.atan2(v[1] - u[1], v[0] - u[0])) % 180.0


def on_lattice(u, v, tol=TOL):
    h = heading(u, v)
    return min(abs(h - 0.0), abs(h - 45.0), abs(h - 90.0), abs(h - 135.0),
               abs(h - 180.0)) <= tol


def interior(far_a, v, far_b):
    """Interior angle at v between rays v->far_a and v->far_b, 0..180."""
    r1 = math.atan2(far_a[1] - v[1], far_a[0] - v[0])
    r2 = math.atan2(far_b[1] - v[1], far_b[0] - v[0])
    d = abs(math.degrees(r1 - r2)) % 360.0
    if d > 180.0:
        d = 360.0 - d
    return d


def legal_far(far_a, v, far_b, tol=TOL):
    """Acute is anything under 90; 90/135/180 are the house corners."""
    return abs(interior(far_a, v, far_b) - 90.0) <= tol or \
        abs(interior(far_a, v, far_b) - 135.0) <= tol or \
        abs(interior(far_a, v, far_b) - 180.0) <= tol


def build_clearance(board, tracks):
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


def d_point_seg(px, py, a, c):
    dx, dy = c[0] - a[0], c[1] - a[1]
    l2 = dx * dx + dy * dy
    if l2 < 1e-18:
        return math.hypot(px - a[0], py - a[1])
    u = max(0.0, min(1.0, ((px - a[0]) * dx + (py - a[1]) * dy) / l2))
    return math.hypot(px - (a[0] + u * dx), py - (a[1] + u * dy))


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
                if d_point_seg(px, py, a, c) - w2 < need:
                    return False
    return True


def main(argv):
    if len(argv) < 3:
        print("usage: chamfer_acute.py BOARD OUT [--apply] [--max-d MM]")
        return 2
    src, out = argv[1], argv[2]
    apply_ = "--apply" in argv
    maxd = MAX_D
    if "--max-d" in argv:
        maxd = float(argv[argv.index("--max-d") + 1])

    board = pcbnew.LoadBoard(src)
    if board is None:
        raise SystemExit("cannot read %s" % src)
    tracks = [t for t in board.GetTracks() if t.Type() == pcbnew.PCB_TRACE_T]
    arcs = set(t.GetNetname() for t in board.GetTracks()
               if t.Type() == pcbnew.PCB_ARC_T)

    def census():
        m = {}
        for t in tracks:
            a, c = ends(t)
            for p, far in ((a, c), (c, a)):
                m.setdefault((t.GetNetname(), t.GetLayer(),
                              round(p[0], 3), round(p[1], 3)), []).append((t, far))
        return m

    buckets = build_clearance(board, tracks)
    m = census()
    acute, done, multi, refused = [], [], 0, 0
    touched = set()

    for key, lst in sorted(m.items()):
        net, lay, vx, vy = key
        v = (vx, vy)
        if id(net) in touched:
            continue
        if len(lst) != 2:
            if len(lst) > 2:
                multi += 1
            continue
        (t1, far1), (t2, far2) = lst
        if t1 is t2:
            continue
        ang = interior(far1, v, far2)
        # An 89.94 deg joint is a rounding artefact of two plated segments meeting
        # on the lattice, not an acid trap: the etchant is as happy there as at 90.
        # Only a joint a full degree under square is chamfered, which is the same
        # tolerance board_shape_gate uses, so the two cannot disagree.
        if ang >= 90.0 - ACUTE_TOL:
            continue
        acute.append((net, lay, v, ang, far1, far2))
        l1 = math.hypot(far1[0] - v[0], far1[1] - v[1])
        l2 = math.hypot(far2[0] - v[0], far2[1] - v[1])
        u1 = ((far1[0] - v[0]) / l1 if l1 > 1e-9 else 0.0,
              (far1[1] - v[1]) / l1 if l1 > 1e-9 else 0.0)
        u2 = ((far2[0] - v[0]) / l2 if l2 > 1e-9 else 0.0,
              (far2[1] - v[1]) / l2 if l2 > 1e-9 else 0.0)
        lim1 = min(maxd, 0.85 * l1)
        lim2 = min(maxd, 0.85 * l2)
        # Solve the chamfer analytically instead of scanning a grid. For a join
        # direction e on the lattice, the chord p1->p2 is parallel to e exactly
        # when d1/d2 = cross(u2, e) / cross(u1, e). Walking d2 and deriving d1
        # keeps the chord on the lattice to the last bit, where a 0.05 mm grid on
        # both legs almost never lines up (a 45 degree joint needs d1 = sqrt(2)*d2,
        # which no pair of grid steps satisfies).
        best = None
        for ex, ey in DIRS:
            c1 = u1[0] * ey - u1[1] * ex
            c2 = u2[0] * ey - u2[1] * ex
            if abs(c1) < 1e-9:
                continue
            ratio = c2 / c1
            if ratio <= 1e-6:
                continue
            d2max = min(lim2, lim1 / ratio)
            if d2max < 0.10:
                continue
            d2 = 0.10
            while d2 <= d2max + 1e-9:
                d1 = d2 * ratio
                p1 = (v[0] + u1[0] * d1, v[1] + u1[1] * d1)
                p2 = (v[0] + u2[0] * d2, v[1] + u2[1] * d2)
                if math.hypot(p2[0] - p1[0], p2[1] - p1[1]) >= 0.10:
                    if (best is None or d1 + d2 < best[0]) and \
                            on_lattice(p1, p2) and \
                            legal_far(far1, p1, p2) and legal_far(far2, p2, p1):
                        best = (d1 + d2, p1, p2)
                        break
                d2 += 0.01
            if best is not None:
                break
        if best is None:
            refused += 1
            continue
        dsum, p1, p2 = best
        half = t1.GetWidth() / 2e6
        if not clear_ok(buckets, net, lay, p1, p2, half):
            refused += 1
            continue
        done.append((net, lay, v, p1, p2, ang, dsum))
        if apply_:
            for t, far, np_ in ((t1, far1, p1), (t2, far2, p2)):
                a, c = ends(t)
                if round(a[0], 3) == round(v[0], 3) and round(a[1], 3) == round(v[1], 3):
                    t.SetStart(pcbnew.VECTOR2I(int(round(np_[0] * 1e6)),
                                               int(round(np_[1] * 1e6))))
                else:
                    t.SetEnd(pcbnew.VECTOR2I(int(round(np_[0] * 1e6)),
                                             int(round(np_[1] * 1e6))))
                _ = far
            nt = pcbnew.PCB_TRACK(board)
            nt.SetStart(pcbnew.VECTOR2I(int(round(p1[0] * 1e6)), int(round(p1[1] * 1e6))))
            nt.SetEnd(pcbnew.VECTOR2I(int(round(p2[0] * 1e6)), int(round(p2[1] * 1e6))))
            nt.SetWidth(t1.GetWidth())
            nt.SetLayer(lay)
            nt.SetNet(t1.GetNet())
            board.Add(nt)
            tracks.append(nt)

    for net, lay, v, p1, p2, ang, dsum in done[:40]:
        print("%-18s %-7s acute %5.1f deg at %.2f,%.2f  legs %.2f + %.2f mm" % (
            net, lay, ang, v[0], v[1],
            math.hypot(p1[0] - v[0], p1[1] - v[1]),
            math.hypot(p2[0] - v[0], p2[1] - v[1])))
    print("acute joints %d   chamfered %d   refused %d   skipped(>=3 legs) %d   "
          "arc nets %d" % (len(acute), len(done), refused, multi, len(arcs)))
    if not apply_:
        print("dry run (add --apply)")
        return 0
    board.Save(out)
    print("saved -> %s" % out)
    return 0


if __name__ == "__main__":
    sys.exit(main(sys.argv))
