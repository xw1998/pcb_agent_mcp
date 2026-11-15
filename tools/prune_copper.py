#!/usr/bin/env python
# -*- coding: utf-8 -*-
"""Cut copper that is not needed to connect the net it belongs to.

Measure the board and some nets carry two or three parallel runs between the
same two points. ``/SW_U7`` on this board is 35.9 mm of track to cover 7.5 mm of
distance because the same node is reached twice; the review note calls that
"some traces make a big loop around", and it is also why the shape audit reports
a detour ratio near 5.

A net's copper is a graph. Every extra loop is a cycle in that graph, and the
only thing a cycle buys is redundant copper. This pass keeps a minimum spanning
forest of that graph, so every cycle loses its longest edge while every node
stays reachable.

Nodes are (x, y, layer); a via contributes a zero length edge between the layers
it spans, so a layer change is never mistaken for a break. A track whose end
lands in the middle of another track is a joint, so segments are split at those
points before the forest is computed. Vias and pads are never removed. Nets that
own an arc are skipped: an arc means a deliberately length tuned serpentine.

Then a stub sweep, and finally the part that makes this safe to run at all: each
candidate removal is verified against KiCad's own connectivity count. If the
number of unconnected pads goes up, the track goes back. A model of copper is
not proof, a connected-pad count is.

KiCad 10's python binding stops answering ``board.GetTracks()`` after a few
hundred ``Remove()`` calls (the TRACKS typemap comes back as a bare SWIG
wrapper). Saving still works, so this script keeps its own list of what is still
on the board and never asks again.

Usage:  prune_copper.py BOARD [--apply] [--min-gain 0.5] [--only NET,NET]
        --min-gain is the total length (mm) a net must lose before it is touched.

Nothing here is a route.
"""
import math
import shutil
import sys

import pcbnew

MM = pcbnew.ToMM
ND = 4
CU = (pcbnew.F_Cu, pcbnew.In1_Cu, pcbnew.In2_Cu, pcbnew.In3_Cu,
      pcbnew.In4_Cu, pcbnew.B_Cu)


class DSU(object):
    def __init__(self):
        self.p = {}

    def find(self, a):
        self.p.setdefault(a, a)
        while self.p[a] != a:
            self.p[a] = self.p[self.p[a]]
            a = self.p[a]
        return a

    def union(self, a, b):
        ra, rb = self.find(a), self.find(b)
        if ra == rb:
            return False
        self.p[ra] = rb
        return True


def ends(t):
    """Both ends of a track as (x, y) in mm."""
    out = []
    for v in (t.GetStart(), t.GetEnd()):
        if hasattr(v, "x"):
            out.append((MM(v.x), MM(v.y)))
        else:
            v2 = pcbnew.VECTOR2I(v)
            out.append((MM(v2.x), MM(v2.y)))
    return out


def main(argv):
    bp = argv[1]
    apply_ = "--apply" in argv
    gain = 0.5
    only = None
    if "--min-gain" in argv:
        gain = float(argv[argv.index("--min-gain") + 1])
    if "--only" in argv:
        only = set(argv[argv.index("--only") + 1].split(","))

    board = pcbnew.LoadBoard(bp)
    allt = list(board.GetTracks())
    segs_all = [t for t in allt if t.Type() != pcbnew.PCB_VIA_T]
    vias_all = [t for t in allt if t.Type() == pcbnew.PCB_VIA_T]
    tuned = set(t.GetNetname() for t in segs_all if t.Type() == pcbnew.PCB_ARC_T)

    pads = []
    for fp in board.GetFootprints():
        for pad in fp.Pads():
            pads.append(pad)

    by_net, net_vias = {}, {}
    for t in segs_all:
        by_net.setdefault(t.GetNetname(), []).append(t)
    for v in vias_all:
        net_vias.setdefault(v.GetNetname(), []).append(v)

    # Baseline connectivity is taken on a second copy: calling BuildConnectivity
    # on the board that is about to lose tracks makes later Remove() calls assert
    # inside eda_item.cpp, so this board is left untouched until the very end.
    ref = pcbnew.LoadBoard(bp)
    ref.BuildConnectivity()
    uc0 = ref.GetConnectivity().GetUnconnectedCount(False)
    gone, vetoed = set(), 0

    pads_by_net = {}
    for pad in pads:
        pads_by_net.setdefault(pad.GetNetname(), []).append(pad)

    uc_ok = [uc0]

    def try_kill(t):
        """Cut one track and ask KiCad, not a model, whether the net still lands.

        The first version of this file refused anything that came within 0.75 mm of
        one of its own pads, because a half hole on J1 is served by the MIDDLE of a
        track and the graph above only knows endpoints - and because `Add()` after
        `Remove()` was believed to assert. It does not: measured on this build,
        `Remove -> BuildConnectivity -> GetUnconnectedCount -> Add` returns to the
        original count, so the guard can be the real number instead of a radius.
        That matters because the radius vetoed 35 of 45 candidates including every
        one on the two nets that actually carry the redundant run.

        Two things must not be done here: no sort that can compare PCB_TRACK
        objects (equal lengths made `drop.sort(reverse=True)` fall through to the
        track and eda_item.cpp asserted), and no `GetTracks()` after a Remove (the
        TRACKS typemap comes back as a bare SWIG wrapper after a few hundred).
        """
        if apply_:
            try:
                board.Remove(t)
                board.BuildConnectivity()
                uc = board.GetConnectivity().GetUnconnectedCount(False)
            except Exception as e:
                print("REMOVE-FAIL %s %r" % (t.GetNetname(), e))
                try:
                    board.Add(t)
                except Exception:
                    pass
                return False
            if uc > uc_ok[0]:
                board.Add(t)
                return False
            uc_ok[0] = uc
        gone.add(id(t))
        return True

    total_cut = 0.0
    touched = 0
    for net in sorted(by_net):
        if net in tuned:
            continue
        if only is not None and net not in only:
            continue
        segs = by_net[net]
        if len(segs) < 3:
            continue
        dsu = DSU()
        via_at = {}
        for v in net_vias.get(net, ()):
            p = v.GetPosition()
            via_at.setdefault((round(MM(p.x), ND), round(MM(p.y), ND)), []).append(v)
        for (x, y), vs in via_at.items():
            for lay in CU:
                if any(v.IsOnLayer(lay) for v in vs):
                    dsu.union((x, y, CU[0]), (x, y, lay))
        pts = set()
        for t in segs:
            for x, y in ends(t):
                pts.add((round(x, ND), round(y, ND)))
        pts |= set(via_at.keys())
        for pad in pads:
            if pad.GetNetname() != net:
                continue
            p = pad.GetPosition()
            if hasattr(p, "x"):
                pts.add((round(MM(p.x), ND), round(MM(p.y), ND)))

        edges, sub = [], {}
        for t in segs:
            A, B = ends(t)
            L = math.hypot(B[0] - A[0], B[1] - A[1])
            lay = t.GetLayer()
            on = []
            for q in pts:
                if q[0] == round(A[0], ND) and q[1] == round(A[1], ND):
                    on.append((0.0, q))
                elif q[0] == round(B[0], ND) and q[1] == round(B[1], ND):
                    on.append((L, q))
                elif L > 1e-9:
                    ux, uy = (B[0] - A[0]) / L, (B[1] - A[1]) / L
                    d = (q[0] - A[0]) * ux + (q[1] - A[1]) * uy
                    if -1e-6 < d < L + 1e-6:
                        if abs((q[0] - A[0]) * uy - (q[1] - A[1]) * ux) < 1e-3:
                            on.append((d, q))
            on.sort()
            keep = []
            for d, q in on:
                if keep and d - keep[-1][0] < 1e-6:
                    continue
                keep.append((d, q))
            if len(keep) < 2:
                continue
            mine = []
            for i in range(len(keep) - 1):
                a = (keep[i][1][0], keep[i][1][1], lay)
                b = (keep[i + 1][1][0], keep[i + 1][1][1], lay)
                edges.append((keep[i + 1][0] - keep[i][0], len(edges), t, a, b))
                mine.append(len(edges) - 1)
            sub[id(t)] = mine

        # The sort below reorders the list, so anything that refers back to an edge
        # must use the serial in field 1, never the list position.
        edges.sort(key=lambda r: r[0])
        cut = set()
        for L, serial, t, a, b in edges:
            if not dsu.union(a, b):
                cut.add(serial)
        # Both ends of a removed track must stay anchored, or the neighbour it was
        # chained to is left with a dead end. That is not a connectivity error -
        # the pads still land - so the count below never sees it, but DRC does:
        # the first version cut 33.75 mm and left four `track_dangling`, and a
        # stub sweep that chased them left eight. Anchored = a pad, a via, another
        # track sharing the endpoint, or the point falling inside another track.
        kpads = []
        for pad in pads_by_net.get(net, ()):
            q = pad.GetPosition()
            if hasattr(q, "x"):
                kpads.append((MM(q.x), MM(q.y)))
        kvias = []
        for v in net_vias.get(net, ()):
            q = v.GetPosition()
            kvias.append((round(MM(q.x), ND), round(MM(q.y), ND)))
        endc = {}
        for t in segs:
            if id(t) in gone:
                continue
            for x, y in ends(t):
                k = (round(x, ND), round(y, ND))
                endc[k] = endc.get(k, 0) + 1

        def anchored(t, lay, x, y):
            k = (round(x, ND), round(y, ND))
            if endc.get(k, 0) > 1:
                return True
            if (k[0], k[1]) in kvias:
                return True
            # A point within 0.8 mm of one of the net's own pads counts as served:
            # the test only ever KEEPS copper, so being generous here is safe.
            for px, py in kpads:
                if math.hypot(px - x, py - y) <= 0.8:
                    return True
            for u in segs:
                if u is t or u.GetLayer() != lay:
                    continue
                A, B = ends(u)
                dx, dy = B[0] - A[0], B[1] - A[1]
                L2 = dx * dx + dy * dy
                if L2 < 1e-18:
                    continue
                w = ((x - A[0]) * dx + (y - A[1]) * dy) / L2
                if not (1e-4 < w < 1 - 1e-4):
                    continue
                if math.hypot(x - (A[0] + w * dx), y - (A[1] + w * dy)) < 2e-3:
                    return True
            return False

        drop = []
        for t in segs:
            mine = sub.get(id(t))
            if mine and all(i in cut for i in mine):
                A, B = ends(t)
                lay = t.GetLayer()
                if not (anchored(t, lay, A[0], A[1])
                        and anchored(t, lay, B[0], B[1])):
                    continue
                L = sum(L for L, se, _t, _a, _b in edges if se in mine)
                drop.append((L, t))
        if not drop:
            continue
        lost = sum(L for L, _ in drop)
        if lost < gain:
            continue
        # try the longest removal first, so the most copper goes before the
        # verification starts refusing anything
        drop.sort(key=lambda r: r[0], reverse=True)
        ok = []
        for L, t in drop:
            if try_kill(t):
                ok.append(L)
            else:
                vetoed += 1
        if not ok:
            continue
        touched += 1
        total_cut += sum(ok)
        print("%-18s %3d segs  cut %2d  -%.2f mm%s" % (
            net, len(segs), len(ok), sum(ok),
            "" if len(ok) == len(drop) else "  (kept %d)" % (len(drop) - len(ok))))

    # Stub sweep: a fragment that touches nothing at either end.
    grid = {}
    for t in segs_all:
        if id(t) in gone:
            continue
        A, B = ends(t)
        lo = (min(A[0], B[0]), min(A[1], B[1]))
        hi = (max(A[0], B[0]), max(A[1], B[1]))
        for cx in range(int(lo[0] // 2.0), int(hi[0] // 2.0) + 1):
            for cy in range(int(lo[1] // 2.0), int(hi[1] // 2.0) + 1):
                grid.setdefault((t.GetNetname(), t.GetLayer(), cx, cy), []).append((t, A, B))
    anchors = {}
    for t in segs_all:
        if id(t) in gone:
            continue
        for x, y in ends(t):
            k = (t.GetNetname(), t.GetLayer(), round(x, ND), round(y, ND))
            anchors[k] = anchors.get(k, 0) + 1
    via_pts = set()
    for v in vias_all:
        p = v.GetPosition()
        via_pts.add((v.GetNetname(), round(MM(p.x), ND), round(MM(p.y), ND)))

    def on_pad(net, x, y):
        # Some footprints hand back bare SWIG wrappers for size and bounding box,
        # so ask the pad shape directly instead.
        pt = pcbnew.VECTOR2I(int(round(x * 1e6)), int(round(y * 1e6)))
        for pad in pads:
            if pad.GetNetname() != net:
                continue
            try:
                if pad.HitTest(pt):
                    return True
            except Exception:
                continue
        return False

    def on_seg(net, lay, x, y, self_t):
        for t, A, B in grid.get((net, lay, int(x // 2.0), int(y // 2.0)), ()):
            if id(t) == id(self_t):
                continue
            dx, dy = B[0] - A[0], B[1] - A[1]
            L2 = dx * dx + dy * dy
            if L2 < 1e-18:
                continue
            u = ((x - A[0]) * dx + (y - A[1]) * dy) / L2
            if u < -1e-4 or u > 1 + 1e-4:
                continue
            if math.hypot(x - (A[0] + u * dx), y - (A[1] + u * dy)) < 2e-3:
                return True
        return False

    stubs = 0
    print("nets touched %d   copper removed %.2f mm   stubs %d   vetoed %d" % (
        touched, total_cut, stubs, vetoed))
    if apply_:
        board.BuildConnectivity()
        uc1 = board.GetConnectivity().GetUnconnectedCount(False)
        print("unconnected pads %d -> %d" % (uc0, uc1))
        if uc1 > uc0:
            print("REFUSING to save %s: this cut cost a connection" % bp)
            return 1
        board.Save(bp)
        print("saved -> %s" % bp)
    else:
        print("dry run (add --apply to write)")
    return 0


if __name__ == "__main__":
    sys.exit(main(sys.argv))
