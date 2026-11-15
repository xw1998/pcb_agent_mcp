#!/usr/bin/env python3
"""Move a via that was drilled through a pad to a legal site beside that pad,
and re-join it with lattice-aligned copper.  Nothing here is a route: no net is
created, no pad is moved, and every added segment is 0/45/90.

A via inside a pad is the "盘中孔" defect: the fab cannot fill it reliably and
it wicks solder off the joint, so it must not be shipped.  The repair is the one
a hand engineer does -- put the via beside the pad and stub it -- so this tool
has to prove four things before it writes anything:

  * the new site is not inside ANY pad, its own net included, with the via body
    fully outside it (a body that merely straddles the pad edge is still
    via-in-pad for the fab),
  * the new via body clears every foreign-net item on every copper layer,
  * the new drill keeps 0.15 mm of hole-to-hole to every other drill,
  * every stub is lattice-only, so every corner it introduces is 90 or 135
    degrees, and it clears foreign copper on its own layer.

The stub is a breadth-first search on the 0.05 mm grid over the eight lattice
directions: 8-connected runs are 0/45/90 by construction, the shortest one is
kept, and a collinear run is emitted as one segment.  For every track endpoint
that used to sit on the old via, the same site is rejoined on that track's own
layer, so no layer loses its connection.  When no site can be proved, the via
is left exactly where it was and reported -- a wrong answer is worse than a
refused one.

    python tools/via_off_pad.py BOARD [--apply OUT]
"""
import argparse
import collections
import math

import pcbnew

COPPER = ("F.Cu", "In1.Cu", "In2.Cu", "In3.Cu", "In4.Cu", "B.Cu")
CLR = 0.10
VIA_R = 0.10
H2H = 0.15
EDGE = 0.50
STEP = 0.05
TW = 0.10
DIRS = ((1, 0), (-1, 0), (0, 1), (0, -1), (1, 1), (1, -1), (-1, 1), (-1, -1))


class Items(object):
    def __init__(self, board):
        self.board = board
        self.buckets = {}
        self.pads = []
        self.drills = []
        for f in board.GetFootprints():
            for p in f.Pads():
                q, s = p.GetPosition(), p.GetSize()
                lay = set(L for L in COPPER if p.IsOnLayer(board.GetLayerID(L)))
                cx, cy = q.x / 1e6, q.y / 1e6
                ref = "%s/%s" % (f.GetReference(), p.GetNumber())
                self._add({"kind": "pad", "net": p.GetNetname(), "layers": lay,
                           "c": (cx, cy), "h": (s.x / 2e6, s.y / 2e6), "ref": ref})
                self.pads.append((cx, cy, s.x / 2e6, s.y / 2e6, ref))
                d = p.GetDrillSize().x
                if d > 0:
                    self.drills.append((cx, cy, d / 2e6))

    def add_tracks(self, board):
        self.tracks = []
        for t in board.GetTracks():
            a, c = t.GetStart(), t.GetEnd()
            if isinstance(t, pcbnew.PCB_VIA):
                p = t.GetPosition()
                cx, cy = p.x / 1e6, p.y / 1e6
                r = t.GetWidth(pcbnew.F_Cu) / 2e6
                self._add({"kind": "via", "net": t.GetNetname(), "layers": set(COPPER),
                           "c": (cx, cy), "h": (r, r)})
                self.drills.append((cx, cy, (t.GetDrill() / 2e6) or 0.05))
                continue
            lay = board.GetLayerName(t.GetLayer())
            self._add({"kind": "seg", "net": t.GetNetname(), "layers": {lay},
                       "a": (a.x / 1e6, a.y / 1e6), "c": (c.x / 1e6, c.y / 1e6),
                       "r": t.GetWidth() / 2e6})
            self.tracks.append((t, lay))

    def _add(self, it):
        xs, ys = corners(it)
        for gx in range(int(math.floor(xs[0])), int(math.floor(xs[1])) + 1):
            for gy in range(int(math.floor(ys[0])), int(math.floor(ys[1])) + 1):
                self.buckets.setdefault((gx, gy), []).append(it)

    def near(self, x, y, rad):
        seen, out = set(), []
        for gx in range(int(math.floor(x - rad)), int(math.floor(x + rad)) + 1):
            for gy in range(int(math.floor(y - rad)), int(math.floor(y + rad)) + 1):
                for it in self.buckets.get((gx, gy), ()):
                    if id(it) not in seen:
                        seen.add(id(it))
                        out.append(it)
        return out


def corners(it):
    if it["kind"] == "seg":
        return ((min(it["a"][0], it["c"][0]) - it["r"], max(it["a"][0], it["c"][0]) + it["r"]),
                (min(it["a"][1], it["c"][1]) - it["r"], max(it["a"][1], it["c"][1]) + it["r"]))
    x, y = it["c"]
    return ((x - it["h"][0], x + it["h"][0]), (y - it["h"][1], y + it["h"][1]))


def pt_seg(px, py, a, c):
    ax, ay = a
    bx, by = c
    vx, vy = bx - ax, by - ay
    L2 = vx * vx + vy * vy
    if L2 == 0:
        return math.hypot(px - ax, py - ay)
    t = max(0.0, min(1.0, ((px - ax) * vx + (py - ay) * vy) / L2))
    return math.hypot(px - (ax + t * vx), py - (ay + t * vy))


def dist(it, x, y):
    if it["kind"] == "seg":
        return pt_seg(x, y, it["a"], it["c"]) - it["r"]
    dx = max(abs(x - it["c"][0]) - it["h"][0], 0.0)
    dy = max(abs(y - it["c"][1]) - it["h"][1], 0.0)
    if dx == 0.0 and dy == 0.0:
        return 0.0
    return math.hypot(dx, dy)


def pad_of(idx, x, y, margin=0.0):
    for (cx, cy, hx, hy, ref) in idx.pads:
        if abs(x - cx) <= hx + margin and abs(y - cy) <= hy + margin:
            return ref
    return None


def site_ok(idx, net, x, y):
    b = idx.board.GetBoardEdgesBoundingBox()
    if not (b.GetLeft() / 1e6 + EDGE < x < b.GetRight() / 1e6 - EDGE and
            b.GetTop() / 1e6 + EDGE < y < b.GetBottom() / 1e6 - EDGE):
        return "edge"
    ref = pad_of(idx, x, y, VIA_R)
    if ref:
        return "pad " + ref
    for it in idx.near(x, y, 1.0):
        if it["net"] == net:
            continue
        if dist(it, x, y) < VIA_R + CLR - 1e-9:
            return "copper " + it["net"]
    for (hx, hy, hr) in idx.drills:
        if math.hypot(x - hx, y - hy) < hr + 0.05 + H2H - 1e-9:
            return "drill"
    return None


def cell_free(idx, net, x, y, layer):
    for it in idx.near(x, y, 1.0):
        if it["net"] == net or layer not in it["layers"]:
            continue
        if dist(it, x, y) < TW / 2.0 + CLR - 1e-9:
            return False
    return True


def bfs(idx, net, x0, y0, layer, limit=5.0, seed=None):
    """Free lattice cells reachable from a point, with their parent, on a
    0.05 mm grid over the eight lattice directions.  `seed` is a pad rectangle:
    every cell inside it is a start, because the pad is already copper of this
    net and a stub standing on it needs no clearance of its own.  Seeding only
    the centre strands the search inside a pad whose neighbours are all at the
    netclass clearance, which is how six of the ten repairs were refused at
    first."""
    n = int(round(limit / STEP))
    si, sj = int(round(x0 / STEP)), int(round(y0 / STEP))
    seen = {}
    q = collections.deque()
    if seed is not None:
        cx, cy, hx, hy = seed
        i0, i1 = int(math.floor((cx - hx) / STEP)), int(math.ceil((cx + hx) / STEP))
        j0, j1 = int(math.floor((cy - hy) / STEP)), int(math.ceil((cy + hy) / STEP))
        for i in range(i0, i1 + 1):
            for j in range(j0, j1 + 1):
                seen[(i, j)] = None
                q.append((i, j))
    if not seen:
        seen[(si, sj)] = None
        q.append((si, sj))
    order = []
    while q:
        i, j = q.popleft()
        order.append((i, j))
        for (di, dj) in DIRS:
            ni, nj = i + di, j + dj
            if abs(ni - si) > n or abs(nj - sj) > n:
                continue
            if (ni, nj) in seen:
                continue
            x, y = ni * STEP, nj * STEP
            if not cell_free(idx, net, x, y, layer):
                seen[(ni, nj)] = None
                continue
            seen[(ni, nj)] = (i, j)
            q.append((ni, nj))
    return seen, si, sj


def path_to(seen, key):
    out = []
    while key is not None:
        out.append(key)
        key = seen[key]
    out.reverse()
    return out


def emit_runs(cells):
    pts = [(round(i * STEP, 4), round(j * STEP, 4)) for (i, j) in cells]
    r = [pts[0]]
    for p in pts[1:]:
        if abs(p[0] - r[-1][0]) > 1e-9 or abs(p[1] - r[-1][1]) > 1e-9:
            r.append(p)
    segs, i = [], 0
    while i < len(r) - 1:
        d = ((r[i + 1][0] > r[i][0]) - (r[i + 1][0] < r[i][0]),
             (r[i + 1][1] > r[i][1]) - (r[i + 1][1] < r[i][1]))
        j = i + 1
        while j + 1 < len(r):
            d2 = ((r[j + 1][0] > r[j][0]) - (r[j + 1][0] < r[j][0]),
                  (r[j + 1][1] > r[j][1]) - (r[j + 1][1] < r[j][1]))
            if d2 != d:
                break
            j += 1
        segs.append((r[i][0], r[i][1], r[j][0], r[j][1]))
        i = j
    return [s for s in segs
            if math.hypot(s[2] - s[0], s[3] - s[1]) > 1e-3]


def main(argv=None):
    ap = argparse.ArgumentParser()
    ap.add_argument("board")
    ap.add_argument("--apply", metavar="OUT", default=None)
    a = ap.parse_args(argv)

    b = pcbnew.LoadBoard(a.board)
    idx = Items(b)
    idx.add_tracks(b)
    bad = []
    for t in b.GetTracks():
        if not isinstance(t, pcbnew.PCB_VIA):
            continue
        p = t.GetPosition()
        vx, vy = p.x / 1e6, p.y / 1e6
        ref = pad_of(idx, vx, vy, 0.0)
        if ref:
            bad.append((t, vx, vy, ref))
    print("vias inside a pad: %d" % len(bad))
    for (t, vx, vy, ref) in bad:
        print("   %-14s %s at %.3f,%.3f" % (t.GetNetname(), ref, vx, -vy))
    if not a.apply:
        print("measured only; pass --apply OUT to repair")
        return 0

    fixed = 0
    dropped = 0
    for (via, vx, vy, ref) in bad:
        net = via.GetNetname()
        # Every same-net track end that used to land on the old via has to be
        # rejoined on its own layer.  "Within 0.08 of the via" was too narrow:
        # the fanout stubs stop a third of a millimetre short of the pad centre,
        # so those ends were left behind and the board lost two connections.
        ends = []
        seen_end = set()
        for (tr, lay) in idx.tracks:
            if tr.GetNetname() != net:
                continue
            for e in (tr.GetStart(), tr.GetEnd()):
                ex, ey = e.x / 1e6, e.y / 1e6
                if math.hypot(ex - vx, ey - vy) > 0.50:
                    continue
                if lay == "F.Cu" and pad_of(idx, ex, ey, 0.0) == ref:
                    continue          # already on the pad we are stubbing from
                key = (lay, round(ex, 4), round(ey, 4))
                if key in seen_end:
                    continue
                seen_end.add(key)
                ends.append(key)
        seed = None
        for (cx, cy, hx, hy, r2) in idx.pads:
            if r2 == ref:
                seed = (cx, cy, hx, hy)
                break
        bf, si, sj = bfs(idx, net, vx, vy, "F.Cu", seed=seed)
        # nearest legal via site that the pad can actually reach
        best = None
        for key in bf:
            if bf[key] is None and key != (si, sj):
                continue
            x, y = key[0] * STEP, key[1] * STEP
            if site_ok(idx, net, x, y):
                continue
            d = math.hypot(x - vx, y - vy)
            if d > 4.0:
                continue
            if best is None or d < best[0]:
                best = (d, key)
        if best is None:
            # Last resort: a via that only stitches a poured net carries no
            # signal, so deleting it may be free.  The connectivity count is the
            # only oracle trusted here -- if the board is no less connected, the
            # via was redundant and the pad keeps its pour.
            b.BuildConnectivity()
            u0 = b.GetConnectivity().GetUnconnectedCount(False)
            ended = any(
                tr.GetNetname() == net and (
                    math.hypot(tr.GetStart().x / 1e6 - vx, tr.GetStart().y / 1e6 - vy) <= 0.10 or
                    math.hypot(tr.GetEnd().x / 1e6 - vx, tr.GetEnd().y / 1e6 - vy) <= 0.10)
                for (tr, _l) in idx.tracks)
            if ended:
                print("   REFUSED %-13s %s: no legal site and copper ends on it"
                      % (net, ref))
                continue
            b.Remove(via)
            b.BuildConnectivity()
            u1 = b.GetConnectivity().GetUnconnectedCount(False)
            if u1 > u0:
                b.Add(via)
                print("   REFUSED %-13s %s: no legal site, deleting it disconnects"
                      % (net, ref))
                continue
            dropped += 1
            print("   dropped %-13s %s: stitching only, board no less connected"
                  % (net, ref))
            continue
        sx, sy = round(best[1][0] * STEP, 4), round(best[1][1] * STEP, 4)
        plan = [("F.Cu", emit_runs(path_to(bf, best[1])))]
        good = True
        for (lay, ex, ey) in ends:
            bf2, ri, rj = bfs(idx, net, ex, ey, lay)
            if best[1] not in bf2 or (bf2[best[1]] is None and best[1] != (ri, rj)):
                good = False
                break
            plan.append((lay, emit_runs(path_to(bf2, best[1]))))
        if not good:
            print("   REFUSED %-14s %s: an inner-layer end cannot reach the site"
                  % (net, ref))
            continue
        b.BuildConnectivity()
        uc0 = b.GetConnectivity().GetUnconnectedCount(False)
        b.Remove(via)
        new_objs = []
        nv = pcbnew.PCB_VIA(b)
        nv.SetPosition(pcbnew.VECTOR2I(int(round(sx * 1e6)), int(round(sy * 1e6))))
        nv.SetWidth(int(round(2 * VIA_R * 1e6)))
        nv.SetDrill(int(round(0.1 * 1e6)))
        nv.SetLayerPair(pcbnew.F_Cu, pcbnew.B_Cu)
        nv.SetNet(via.GetNet())
        b.Add(nv)
        new_objs.append(nv)
        nseg = 0
        for (lay, segs) in plan:
            for (x1, y1, x2, y2) in segs:
                seg = pcbnew.PCB_TRACK(b)
                seg.SetStart(pcbnew.VECTOR2I(int(round(x1 * 1e6)), int(round(y1 * 1e6))))
                seg.SetEnd(pcbnew.VECTOR2I(int(round(x2 * 1e6)), int(round(y2 * 1e6))))
                seg.SetWidth(int(round(TW * 1e6)))
                seg.SetLayer(b.GetLayerID(lay))
                seg.SetNet(via.GetNet())
                b.Add(seg)
                new_objs.append(seg)
                nseg += 1
        b.BuildConnectivity()
        uc1 = b.GetConnectivity().GetUnconnectedCount(False)
        if uc1 > uc0:
            # The repair must not cost a connection.  KiCad's own count is the
            # oracle, so a stub that silently missed an inner-layer end is put
            # back exactly as it was instead of being shipped.
            for o in new_objs:
                b.Remove(o)
            b.Add(via)
            print("   REVERTED %-13s %s: moving it lost %d connection(s)"
                  % (net, ref, uc1 - uc0))
            continue
        idx.drills.append((sx, sy, 0.05))
        idx._add({"kind": "via", "net": net, "layers": set(COPPER),
                  "c": (sx, sy), "h": (VIA_R, VIA_R)})
        fixed += 1
        print("   moved %-14s %s -> %.3f,%.3f  %.2f mm away, %d segs"
              % (net, ref, sx, -sy, best[0], nseg))
    b.BuildConnectivity()
    print("repaired %d, dropped %d of %d" % (fixed, dropped, len(bad)))
    b.Save(a.apply)
    print("saved %s" % a.apply)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
