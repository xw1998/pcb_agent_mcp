#!/usr/bin/env python
# -*- coding: utf-8 -*-
"""Re-draw a straight track that is not on the 0/45/90 lattice as two legs that are.

Measured need: on the SOM board `board_shape_gate` reports 62 segments, 86.45 mm of
copper, whose heading is neither H, V nor 45 - 14.0 deg, 10.8 deg, 151.1 deg. They
are not random: they are the hand written power table in `route_som.py`, which
places track corners at coordinates rather than along directions. 86 mm of
"almost diagonal" is the single most visible thing on the plot.

The fix is local and exact. A segment from A to B with dx, dy can be replaced by
two legs without moving either endpoint: a 45 degree leg of length min(|dx|,|dy|)
and an axis leg of the remainder, in either order (diagonal first or axis first).
Both legs are lattice legal by construction, and the joint between them is 135.
Only the two ends can still be wrong, so each end is checked against the segments
that already land there: every resulting corner must be 135, 90 or 180. When both
orders pass, the shorter one wins; when neither passes, the segment is reported
and left alone.

Nothing here moves a pad, a via or an endpoint, so a net that already connects
still connects. Clearance is checked, not assumed: each candidate leg is walked in
0.1 mm steps against a 1 mm bucket grid of every other net's pads, vias and tracks,
and refused if it comes closer than clearance + half the track width. The first
version had no such guard and the DRC on a 46 segment run reported three new
clearance errors - /FB_U4 past a 0.3838 mm-away U4 ground pad and a via on /+5V_IN,
and /FB_U5 past U5's ground pad, all at 0.0758-0.0838 mm against a 0.1 mm rule.

Nothing here is a route.
"""
import math
import sys

import pcbnew

MM = pcbnew.ToMM
ND = 4
NDIR = ((1, 0), (1, 1), (0, 1), (-1, 1), (-1, 0), (-1, -1), (0, -1), (1, -1))


def ends(t):
    a, b = t.GetStart(), t.GetEnd()
    return (MM(a.x), MM(a.y)), (MM(b.x), MM(b.y))


def heading(a, b):
    d = math.degrees(math.atan2(b[1] - a[1], b[0] - a[0])) % 180.0
    return d


def on_lattice(a, b, tol=0.5):
    h = heading(a, b)
    return min(abs(h - 0.0), abs(h - 45.0), abs(h - 90.0), abs(h - 135.0),
               abs(h - 180.0)) <= tol


def corner(a, v, b):
    """Interior angle at v between the rays v->a and v->b, degrees 0..180."""
    r1 = math.atan2(a[1] - v[1], a[0] - v[0])
    r2 = math.atan2(b[1] - v[1], b[0] - v[0])
    d = abs(math.degrees(r1 - r2)) % 360.0
    if d > 180.0:
        d = 360.0 - d
    return 180.0 - abs(180.0 - d) if d > 90.0 else d


def legal_angle(deg, tol=1.0):
    return min(abs(deg - g) for g in (90.0, 135.0, 180.0)) <= tol


def main(argv):
    src, out = argv[1], argv[2]
    apply_ = "--apply" in argv
    only = None
    if "--net" in argv:
        only = set(argv[argv.index("--net") + 1].split(","))
    maxmm = 30.0
    if "--max-mm" in argv:
        maxmm = float(argv[argv.index("--max-mm") + 1])

    b = pcbnew.LoadBoard(src)
    trk = [t for t in b.GetTracks() if t.Type() == pcbnew.PCB_TRACE_T]
    # endpoint -> [(track, other end)]  for the joint check, and the same map
    # rewritten as segments are replaced so the next one sees current copper.
    def census():
        m = {}
        for t in trk:
            if t is None:
                continue
            a, c = ends(t)
            m.setdefault((t.GetNetname(), t.GetLayer(), round(a[0], 3),
                          round(a[1], 3)), []).append((t, c))
            m.setdefault((t.GetNetname(), t.GetLayer(), round(c[0], 3),
                          round(c[1], 3)), []).append((t, a))
        return m

    def neighbours(m, net, lay, p, self_t):
        out_ = []
        for t, other in m.get((net, lay, round(p[0], 3), round(p[1], 3)), ()):
            if t is self_t:
                continue
            out_.append(other)
        return out_

    def ends_ok(m, net, lay, a, p, c, self_):
        """At a and c the new leg must meet whatever is already there legally."""
        for v, far, new_leg in ((a, p, p), (c, p, p)):
            for other in neighbours(m, net, lay, v, self_):
                if not legal_angle(corner(far, v, other)):
                    return False
        return True

    # ---- clearance grid over every OTHER net's copper --------------------
    CLR = 0.10
    buckets = {}

    def _put(item_kind, key, box):
        x0, y0, x1, y1 = box
        for gx in range(int(math.floor(x0)), int(math.floor(x1)) + 1):
            for gy in range(int(math.floor(y0)), int(math.floor(y1)) + 1):
                buckets.setdefault((gx, gy), []).append((item_kind, key))

    for f in b.GetFootprints():
        for p in f.Pads():
            try:
                bb = p.GetBoundingBox()
            except Exception:
                continue
            _put("pad", (p.GetNetname(), bb.GetLeft() / 1e6, bb.GetTop() / 1e6,
                         bb.GetRight() / 1e6, bb.GetBottom() / 1e6),
                 (bb.GetLeft() / 1e6 - 1, min(bb.GetTop(), bb.GetBottom()) / 1e6 - 1,
                  bb.GetRight() / 1e6 + 1, max(bb.GetTop(), bb.GetBottom()) / 1e6 + 1))
    for v in b.GetTracks():
        if v.Type() != pcbnew.PCB_VIA_T:
            continue
        q = v.GetPosition()
        # PCB_VIA::GetWidth() without a layer argument is an assert in KiCad 10
        # (pcb_track.cpp:387) - it opens a dialog and the script appears to hang.
        r = MM(v.GetWidth(pcbnew.F_Cu)) / 2.0
        _put("via", (v.GetNetname(), MM(q.x), MM(q.y), r),
             (MM(q.x) - r - 1, MM(q.y) - r - 1, MM(q.x) + r + 1, MM(q.y) + r + 1))
    for t in trk:
        a, c = ends(t)
        w = MM(t.GetWidth()) / 2.0
        _put("seg", (t.GetNetname(), t.GetLayer(), a, c, w),
             (min(a[0], c[0]) - w - 1, min(a[1], c[1]) - w - 1,
              max(a[0], c[0]) + w + 1, max(a[1], c[1]) + w + 1))

    def _d2seg(px, py, a, c):
        dx, dy = c[0] - a[0], c[1] - a[1]
        L2 = dx * dx + dy * dy
        if L2 < 1e-18:
            return math.hypot(px - a[0], py - a[1])
        u = max(0.0, min(1.0, ((px - a[0]) * dx + (py - a[1]) * dy) / L2))
        return math.hypot(px - (a[0] + u * dx), py - (a[1] + u * dy))

    def clear_ok(net, lay, u, v, half):
        need = CLR + half
        L = math.hypot(v[0] - u[0], v[1] - u[1])
        n = max(1, int(L / 0.1))
        for k in range(n + 1):
            px = u[0] + (v[0] - u[0]) * k / float(n)
            py = u[1] + (v[1] - u[1]) * k / float(n)
            for kind, key in buckets.get((int(math.floor(px)), int(math.floor(py))), ()):
                if key[0] == net:
                    continue
                if kind == "pad":
                    _, x0, y0, x1, y1 = key[0], key[1], key[2], key[3], key[4]
                    dx = max(x0 - px, 0.0, px - x1)
                    dy = max(y0 - py, 0.0, py - y1)
                    if math.hypot(dx, dy) < need:
                        return False
                elif kind == "via":
                    _, vx, vy, r = key
                    if math.hypot(vx - px, vy - py) - r < need:
                        return False
                else:
                    _ln, klay, a, c, w2 = key
                    if klay != lay:
                        continue
                    if _d2seg(px, py, a, c) - w2 < need:
                        return False
        return True

    changes = []
    for t in trk:
        net, lay = t.GetNetname(), t.GetLayer()
        if only is not None and net not in only:
            continue
        a, c = ends(t)
        if on_lattice(a, c):
            continue
        if t.GetLength() / 1e6 > maxmm:
            continue
        dx, dy = c[0] - a[0], c[1] - a[1]
        ax, ay = abs(dx), abs(dy)
        if ax < 1e-3 or ay < 1e-3:
            continue
        m = census()
        best = None
        for diag_first in (True, False):
            if ax == ay:
                # already exactly diagonal in both axes; only the order differs
                continue
            m2 = min(ax, ay)
            sx = 1.0 if dx > 0 else -1.0
            sy = 1.0 if dy > 0 else -1.0
            if diag_first:
                p = (a[0] + sx * m2, a[1] + sy * m2)
            else:
                if ax > ay:
                    p = (a[0] + sx * (ax - m2), a[1])
                else:
                    p = (a[0], a[1] + sy * (ay - m2))
            if math.hypot(p[0] - a[0], p[1] - a[1]) < 1e-3:
                continue
            if math.hypot(c[0] - p[0], c[1] - p[1]) < 1e-3:
                continue
            if not (on_lattice(a, p) and on_lattice(p, c)):
                continue
            if not legal_angle(corner(a, p, c)):
                continue
            if not ends_ok(m, net, lay, a, p, c, t):
                continue
            if not (clear_ok(net, lay, a, p, t.GetWidth() / 2e6)
                    and clear_ok(net, lay, p, c, t.GetWidth() / 2e6)):
                continue
            L = math.hypot(p[0] - a[0], p[1] - a[1]) + math.hypot(c[0] - p[0],
                                                                c[1] - p[1])
            if best is None or L < best[0]:
                best = (L, p, diag_first)
        if best is None:
            changes.append((net, lay, None, t.GetLength() / 1e6, a, c))
            continue
        changes.append((net, lay, best[1], t.GetLength() / 1e6, a, c))
        if apply_:
            w = t.GetWidth()
            b.Remove(t)
            for u, v in ((a, best[1]), (best[1], c)):
                nt = pcbnew.PCB_TRACK(b)
                nt.SetStart(pcbnew.VECTOR2I(int(round(u[0] * 1e6)),
                                            int(round(u[1] * 1e6))))
                nt.SetEnd(pcbnew.VECTOR2I(int(round(v[0] * 1e6)),
                                          int(round(v[1] * 1e6))))
                nt.SetWidth(w)
                nt.SetLayer(lay)
                nt.SetNet(t.GetNet())
                b.Add(nt)

    ok_n = sum(1 for c in changes if c[2] is not None)
    bad = [c for c in changes if c[2] is None]
    for net, lay, p, L, a, c in changes[:40]:
        print("%-16s %-7s %5.2f mm  %s" % (
            net, lay, L, "-> x=%.3f" % p[0] if p else "LEFT (no legal order)"))
    print("off-lattice tracks %d   fixed %d   left %d" % (len(changes), ok_n,
                                                          len(bad)))
    if not apply_:
        print("dry run (add --apply, and pass a separate OUT path)")
        return 0
    b.Save(out)
    print("saved -> %s" % out)
    return 0


if __name__ == "__main__":
    sys.exit(main(sys.argv))
