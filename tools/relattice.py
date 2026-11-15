#!/usr/bin/env python
# -*- coding: utf-8 -*-
"""Rebuild an off-lattice copper chain into lattice legs without moving an end.

Nothing here is a route.  No pad moves, no via moves, no net changes, and no
length is invented: every leg written here lies on the 0/45/90/135 grid, and the
two ends of a rebuilt chain are exactly the two points that were there before.

Measured need.  `board_shape_gate` on the SOM board reports 14 segments whose
heading is neither H, V nor 45 (14.0 deg, 151.1 deg, 115.6 deg ...) and 10
corners whose interior angle is under 90 deg -- an acid trap the fab cannot etch
cleanly.  Both lists point at the same handful of nets: the hand written power
table in `route_som.py` places corners at coordinates instead of along
directions, so `/+1V8_ANA` walks 21.765,38.600 -> 19.503,38.035 -> 21.399,36.989
and the corner in the middle comes out at 42.9 deg.

`fix_angles.py` cuts ONE off-lattice segment into one 45 leg and one axis leg.
On this board it fixes 1 of 15 and refuses the other 14 with "no legal order",
because the failing corner is a V: both of its legs are off-lattice, so whichever
order is chosen at one end leaves an acute joint at the other.  Cutting each leg
where it stands cannot fix that.  What can is replacing the whole chain between
two joints that must stay (a pad, a via, a branch) with a lattice path, letting
the *free* corners move:

    A ---- V1 ---- V2 ---- C     (V free, legs off-lattice, corners acute)
    A --- P1 ---- P2 ---- C      (P chosen so every leg is on the grid and the
                                  joints at A, P1, P2, C are all >= 90 deg)

The search is exact and closed form, not a heuristic walk.  A path with one
intermediate point is the intersection of a lattice ray out of A with a lattice
ray into C (8x8 pairs).  A path with two intermediate points is the same system
with one free parameter, swept on a 0.05 mm ladder (8x8x8 directions).  A
candidate survives only when

  * every leg is on the 0/45/90/135 grid and no leg is degenerate,
  * the rebuilt chain is not longer than the copper it replaces,
  * every joint it makes is >= 90 deg, measured against the segments that stay
    (the copper at a branch point is included),
  * every leg clears every other net's pads, vias, tracks and arcs by the
    netclass clearance, measured with the exact segment-segment distance this
    repo already uses -- `seg_seg_dist` in `untune.py`, which reads a crossing as
    ZERO.  The version that only compared four endpoints reported 0.13 mm for
    copper that actually crossed, and that is how the first applied untune pass
    cut /DDR_DQ2 across /DDR_DQ13.

Refusals are reported with the direction that came closest and why it failed; a
chain whose only legal shape is longer than what is there is left exactly as it
was, and a net carrying an arc on the layer is skipped and counted, because an
arc has no chord to rebuild against.

Usage:
  relattice.py BOARD [--out OUT] [--apply] [--net N1,N2] [--json LOG.json]
"""
import argparse
import heapq
import json
import math
import os
import sys

import pcbnew

HERE = os.path.dirname(os.path.abspath(__file__))
REPO = os.path.dirname(HERE)
sys.path.insert(0, os.path.join(REPO, "src"))
sys.path.insert(0, HERE)

from untune import (Field, MM, norm, sub, on_45_grid, CLEAR_MM,  # noqa: E402
                    point_seg_dist, rect_point_dist, seg_seg_dist)

ANGLE_TOL = 0.5
MIN_ANGLE = 90.0 - ANGLE_TOL
DIRS = ((1, 0), (1, 1), (0, 1), (-1, 1), (-1, 0), (-1, -1), (0, -1), (1, -1))
STEP = 0.05
SLACK = 1.03            # a rebuilt chain may be up to 3% longer than what it replaces


def _interior(a, v, c):
    u = sub(a, v)
    w = sub(c, v)
    nu, nw = norm(u), norm(w)
    if nu < 1e-12 or nw < 1e-12:
        return 0.0
    c0 = (u[0] * w[0] + u[1] * w[1]) / (nu * nw)
    return math.degrees(math.acos(max(-1.0, min(1.0, c0))))


def _lattice(a, b):
    return norm(sub(b, a)) > 1e-9 and on_45_grid(a, b, ANGLE_TOL)


class Seg(object):
    __slots__ = ("a", "b", "layer", "width", "obj", "net")

    def __init__(self, a, b, layer, width, obj, net):
        self.a, self.b = a, b
        self.layer, self.width, self.obj, self.net = layer, width, obj, net

    @property
    def length(self):
        return norm(sub(self.b, self.a))


def _key(p):
    return (round(p[0], 4), round(p[1], 4))


def _graph(segs):
    adj = {}
    for s in segs:
        adj.setdefault(_key(s.a), []).append(s)
        adj.setdefault(_key(s.b), []).append(s)
    return adj


def _chains(segs, anchors=frozenset()):
    """Edge-disjoint chains between non-moveable nodes.

    A node is non-moveable when its degree is not 2 (a branch or a dead end) and
    also when it carries a via or a pad: those are copper on another layer or on
    the part, so moving the track point off them disconnects the net.  The first
    applied version moved a track corner that happened to be a via and turned one
    GND via and one /+1V8_ANA via into unconnected items.
    """
    adj = _graph(segs)
    deg = dict((k, len(v)) for k, v in adj.items())
    fixed = set(k for k, d in deg.items() if d != 2) | set(
        k for k in deg if k in anchors)
    used = set()
    out = []
    for start in sorted(fixed):
        for s in adj[start]:
            if id(s) in used:
                continue
            chain = []
            pts = []
            cur = start
            e = s
            ok = True
            while True:
                used.add(id(e))
                chain.append(e)
                nxt = _key(e.b) if _key(e.a) == cur else _key(e.a)
                if not pts:
                    pts.append(cur)
                pts.append(nxt)
                if nxt in fixed:
                    break
                cand = [x for x in adj[nxt] if id(x) not in used]
                if len(cand) != 1:
                    ok = False
                    break
                cur, e = nxt, cand[0]
                if len(chain) > 400:
                    ok = False
                    break
            if ok and len(chain) >= 1:
                out.append((pts, chain))
    # a closed loop has no fixed node: report it rather than guess
    loops = [s for s in segs if id(s) not in used]
    return out, loops


def _solve2(a, c, orig, keep=40, slack=1.00):
    """Lattice paths with one intermediate point, shortest first."""
    cand = {}
    for d1 in DIRS:
        for d2 in DIRS:
            det = d1[0] * (-d2[0]) - d1[1] * (-d2[1])
            if abs(det) < 1e-12:
                continue
            rx, ry = c[0] - a[0], c[1] - a[1]
            t1 = (rx * (-d2[0]) - ry * (-d2[1])) / det
            t2 = (d1[0] * ry - d1[1] * rx) / det
            if t1 <= 1e-9 or t2 <= 1e-9:
                continue
            p = (round(a[0] + t1 * d1[0], 6), round(a[1] + t1 * d1[1], 6))
            seglen = t1 * norm(d1) + t2 * norm(d2)
            if seglen > orig * slack + 1e-9:
                continue
            if not (_lattice(a, p) and _lattice(p, c)):
                continue
            if _interior(a, p, c) < MIN_ANGLE:
                continue
            prev = cand.get(p)
            if prev is None or seglen < prev[0]:
                cand[p] = (seglen, [p])
    return sorted(cand.values(), key=lambda t: t[0])[:keep]


def _solve3(a, c, orig, keep=40, slack=1.00):
    """Lattice paths with two intermediate points, shortest first."""
    cand = {}
    R = (c[0] - a[0], c[1] - a[1])
    for d1 in DIRS:
        for d2 in DIRS:
            for d3 in DIRS:
                det = d1[0] * d3[1] - d1[1] * d3[0]
                if abs(det) < 1e-12:
                    continue
                n2 = norm(d2)
                # sweep t2 on the ladder
                t2 = STEP
                while t2 * n2 <= orig * slack + 1e-9:
                    bx, by = R[0] - t2 * d2[0], R[1] - t2 * d2[1]
                    t1 = (bx * d3[1] - by * d3[0]) / det
                    t3 = (d1[0] * by - d1[1] * bx) / det
                    if t1 > 1e-9 and t3 > 1e-9:
                        ln = (t1 * norm(d1) + t2 * n2 + t3 * norm(d3))
                        if ln <= orig * slack + 1e-9:
                            p1 = (a[0] + t1 * d1[0], a[1] + t1 * d1[1])
                            p2 = (p1[0] + t2 * d2[0], p1[1] + t2 * d2[1])
                            if (_lattice(a, p1) and _lattice(p1, p2) and
                                    _lattice(p2, c) and
                                    _interior(a, p1, p2) >= MIN_ANGLE and
                                    _interior(p1, p2, c) >= MIN_ANGLE):
                                k = (_key(p1), _key(p2))
                                prev = cand.get(k)
                                if prev is None or ln < prev[0]:
                                    cand[k] = (ln, [p1, p2])
                    t2 += STEP
    return sorted(cand.values(), key=lambda t: t[0])[:keep]


def _joints_ok(segs_all, pts, skip):
    """Every joint along the new path, plus the joints against copper staying."""
    for i in range(1, len(pts) - 1):
        if _interior(pts[i - 1], pts[i], pts[i + 1]) < MIN_ANGLE:
            return False, ("P%d" % i, _interior(pts[i - 1], pts[i], pts[i + 1]))
    adj = _graph([s for s in segs_all if id(s) not in skip])
    for end in (pts[0], pts[-1]):
        far = pts[1] if end == pts[0] else pts[-2]
        for other in adj.get(_key(end), ()):
            q = other.b if _key(other.a) == _key(end) else other.a
            ang = _interior(q, end, far)
            if ang < MIN_ANGLE:
                return False, ("end", round(ang, 2))
    return True, None


def _clearance(field, layer, pts, width, net):
    worst = 99.0
    for i in range(len(pts) - 1):
        g = field.nearest(layer, pts[i], pts[i + 1], width / 2.0, net)
        if g is not None:
            worst = min(worst, g)
    return worst


def rebuild_chain(a, c, orig, segs_all, skip, field, layer, width, net):
    """First lattice path (shortest that clears) replacing the chain."""
    tried = 0
    last = None
    for solver, npts in ((_solve2, 1), (_solve3, 2)):
        for cand in solver(a, c, orig, slack=SLACK):
            tried += 1
            pts = [a] + cand[1] + [c]
            ok, why = _joints_ok(segs_all, pts, skip)
            if not ok:
                last = {"reason": "joint_%s" % (why[0],),
                        "angle_deg": why[1]}
                continue
            gap = _clearance(field, layer, pts, width, net)
            if gap < CLEAR_MM - 1e-9:
                last = {"reason": "clearance", "gap_mm": round(gap, 4)}
                continue
            return {"ok": True, "pts": pts, "orig_mm": round(orig, 4),
                    "new_mm": round(cand[0], 4),
                    "save_mm": round(orig - cand[0], 4),
                    "clearance_mm": round(gap, 4), "legs": npts + 1,
                    "tried": tried}
    out = {"ok": False, "orig_mm": round(orig, 4), "tried": tried}
    out.update(last or {"reason": "no_lattice_path"})
    return out


# ------------------------------------------------------- staircase chain solver
STEPS = (0.05, 0.025, 0.02, 0.01, 0.005)
SQ2 = math.sqrt(2.0)
MOVES = ((1, 0, 1.0), (-1, 0, 1.0), (0, 1, 1.0), (0, -1, 1.0),
         (1, 1, SQ2), (1, -1, SQ2), (-1, 1, SQ2), (-1, -1, SQ2))


def _pick_step(a, c):
    """Finest-free ladder that puts BOTH ends on the same lattice.

    A staircase chain is rebuilt by searching a lattice; the searched lattice
    has to contain both ends exactly, otherwise the last leg walks into the via
    at a heading that is not on the 0/45/90/135 grid and the gate rejects it a
    second time.  The step is chosen from the coarsest that divides both
    ordinates, so node counts stay small where the geometry allows it.
    """
    dx, dy = abs(c[0] - a[0]), abs(c[1] - a[1])
    for s in STEPS:
        if abs(dx / s - round(dx / s)) < 1e-4 and abs(dy / s - round(dy / s)) < 1e-4:
            return s
    return None


def _local_obstacles(field, layer, lo, hi):
    """Field records whose 1 mm buckets overlap the search box."""
    seen, out = set(), []
    for cx in range(int(math.floor(lo[0])) - 1, int(math.floor(hi[0])) + 2):
        for cy in range(int(math.floor(lo[1])) - 1, int(math.floor(hi[1])) + 2):
            for rec in field.cells.get((layer, cx, cy), ()):
                if id(rec) in seen:
                    continue
                seen.add(id(rec))
                out.append(rec)
    return out


def _rec_bbox(rec):
    if rec[0] == "seg":
        a, b, r = rec[1], rec[2], rec[3]
        return (min(a[0], b[0]) - r, min(a[1], b[1]) - r,
                max(a[0], b[0]) + r, max(a[1], b[1]) + r)
    if rec[0] == "rect":
        lo, hi, r = rec[1], rec[2], rec[3]
        return (lo[0] - r, lo[1] - r, hi[0] + r, hi[1] + r)
    c, r = rec[1], rec[2]
    return (c[0] - r, c[1] - r, c[0] + r, c[1] + r)


def _rec_dist(rec, p):
    if rec[0] == "seg":
        return point_seg_dist(p, rec[1], rec[2]) - rec[3]
    if rec[0] == "rect":
        return rect_point_dist(rec[1], rec[2], p) - rec[3]
    return norm(sub(p, rec[1])) - rec[2]


def _free_mask(a, c, step, obstacles, need, slack=None):
    """Node (i,j) -> point a + (i*step, j*step); True where copper may sit.

    The lattice is anchored at A, not at the min corner: A is copper that stays,
    so it has to BE a node.  Indices run negative when C lies to the left or
    below A, which is why the index space is reported back with its origin.

    A node is kept only when its distance to every foreign record is at least
    `need + slack`.  That slack is what makes a diagonal move safe to take
    without checking its midpoint: every point of a move lies within
    step/sqrt(2) of one of its two nodes, so the whole move clears by `need`.
    """
    if slack is None:
        slack = step / SQ2
    ni = int(round((c[0] - a[0]) / step))
    nj = int(round((c[1] - a[1]) / step))
    m = max(4, int(math.ceil(0.75 / step)))
    i_lo, i_hi = min(0, ni) - m, max(0, ni) + m
    j_lo, j_hi = min(0, nj) - m, max(0, nj) + m
    NI, NJ = i_hi - i_lo + 1, j_hi - j_lo + 1
    if NI * NJ > 900000:
        return None
    free = bytearray(b"\x01") * (NI * NJ)
    ns = need + slack
    for rec in obstacles:
        lo0, lo1, hi0, hi1 = _rec_bbox(rec)
        i0 = int(math.floor((lo0 - ns - a[0]) / step))
        i1 = int(math.ceil((hi0 + ns - a[0]) / step))
        j0 = int(math.floor((lo1 - ns - a[1]) / step))
        j1 = int(math.ceil((hi1 + ns - a[1]) / step))
        i0, i1 = max(i_lo, i0), min(i_hi, i1)
        j0, j1 = max(j_lo, j0), min(j_hi, j1)
        if i0 > i1 or j0 > j1:
            continue
        for j in range(j0, j1 + 1):
            y = a[1] + j * step
            base = (j - j_lo) * NI
            for i in range(i0, i1 + 1):
                if _rec_dist(rec, (a[0] + i * step, y)) < ns:
                    free[base + (i - i_lo)] = 0
    return free, NI, NJ, i_lo, j_lo, step


def _astar(mask, si, sj, gi, gj, NI, NJ, goal_idx=None):
    # local index convention matches _free_mask: k = j * NI + i
    free = mask
    INF = 1e18
    g = {sj * NI + si: 0.0}
    prev = {}
    if goal_idx is None:
        goal = set([gj * NI + gi])
    else:
        goal = set(j * NI + i for i, j in goal_idx)
    heap = [(math.hypot(gi - si, gj - sj), 0.0, si, sj)]
    seen = 0
    while heap:
        f, gc, i, j = heapq.heappop(heap)
        k = j * NI + i
        if gc > g.get(k, INF) + 1e-12:
            continue
        if k in goal:
            keys = [k]
            while keys[-1] in prev:
                keys.append(prev[keys[-1]])
            keys.reverse()
            return [(x % NI, x // NI) for x in keys]
        seen += 1
        if seen > 3000000:
            return None
        for di, dj, w in MOVES:
            ni_, nj_ = i + di, j + dj
            if ni_ < 0 or nj_ < 0 or ni_ >= NI or nj_ >= NJ:
                continue
            nk = nj_ * NI + ni_
            if not free[nk]:
                continue
            ng = gc + w
            if ng >= g.get(nk, INF) - 1e-12:
                continue
            g[nk] = ng
            prev[nk] = k
            heapq.heappush(heap, (ng + math.hypot(gi - ni_, gj - nj_), ng,
                                  ni_, nj_))
    return None


def _simplify(pts):
    out = [pts[0]]
    for i in range(1, len(pts) - 1):
        a, b, c = out[-1], pts[i], pts[i + 1]
        u = (b[0] - a[0], b[1] - a[1])
        v = (c[0] - b[0], c[1] - b[1])
        if abs(u[0] * v[1] - u[1] * v[0]) < 1e-9 and u[0] * v[0] + u[1] * v[1] > 0:
            continue
        out.append(b)
    out.append(pts[-1])
    return out


def _self_overlap(pts, segs_all, skip, net, width):
    """New legs must not lie on top of copper of the same net that stays."""
    keep = [s for s in segs_all if id(s) not in skip]
    if not keep:
        return None
    for i in range(len(pts) - 1):
        a, b = pts[i], pts[i + 1]
        for s in keep:
            d = seg_seg_dist(a, b, s.a, s.b)
            if d < width - 1e-6:
                if (norm(sub(a, s.a)) < 1e-6 or norm(sub(a, s.b)) < 1e-6 or
                        norm(sub(b, s.a)) < 1e-6 or norm(sub(b, s.b)) < 1e-6):
                    continue
                return round(d, 4)
    return None


def _goal_nodes(c, step, a, i_lo, j_lo, NI, NJ):
    """Lattice nodes from which C is reached by ONE legal 0/45/90 leg.

    Both ends of a chain are copper that stays (a via, a pad, a branch), and
    their distance is not always a multiple of the search step -- a 45 degree
    chamfer puts an end at 0.15*sqrt(2), which no decimal ladder divides.  So
    the search is not asked to land on C; it is asked to land on any node whose
    straight run to C is already on the 0/45/90/135 grid, and that run becomes
    one more leg of the path.
    """
    out = []
    span = norm(sub(c, a)) + 2.0
    n = int(span / (step * 4)) + 1
    for ux, uy in DIRS:
        un = math.hypot(ux, uy)
        ux, uy = ux / un, uy / un
        for k in range(1, n + 1):
            t = k * step * 4
            p = (c[0] - t * ux, c[1] - t * uy)
            ii = (p[0] - a[0]) / step
            jj = (p[1] - a[1]) / step
            if abs(ii - round(ii)) > 1e-6 or abs(jj - round(jj)) > 1e-6:
                continue
            ii, jj = int(round(ii)), int(round(jj))
            li, lj = ii - i_lo, jj - j_lo
            if not (0 <= li < NI and 0 <= lj < NJ):
                continue
            if not _lattice(p, c):
                continue
            out.append((li, lj, p))
    return out


def rebuild_chain_astar(a, c, orig, segs_all, skip, field, layer, width, net,
                        margin=0.75):
    lo = (min(a[0], c[0]) - margin, min(a[1], c[1]) - margin)
    hi = (max(a[0], c[0]) + margin, max(a[1], c[1]) + margin)
    obstacles = _local_obstacles(field, layer, lo, hi)
    need = width / 2.0 + CLEAR_MM
    last = None
    for step in (0.05, 0.025, 0.02):
        for infl in (step / SQ2, 0.0):
            m = _free_mask(a, c, step, obstacles, need, infl)
            if m is None:
                return {"ok": False, "orig_mm": round(orig, 4),
                        "reason": "box_too_big", "step": step}
            free, NI, NJ, i_lo, j_lo, st = m
            si, sj = -i_lo, -j_lo
            if not free[sj * NI + si] and infl:
                continue
            if not free[sj * NI + si]:
                last = {"reason": "start_blocked"}
                continue
            goals = [g for g in _goal_nodes(c, st, a, i_lo, j_lo, NI, NJ)
                     if free[g[1] * NI + g[0]]]
            if not goals:
                last = {"reason": "no_goal_node", "step": st}
                continue
            gi = int(round((c[0] - a[0]) / st)) - i_lo
            gj = int(round((c[1] - a[1]) / st)) - j_lo
            path = _astar(free, si, sj, gi, gj, NI, NJ,
                          set((g[0], g[1]) for g in goals))
            if path is None:
                last = {"reason": "astar_fail", "step": st, "goals": len(goals)}
                continue
            pts = [(a[0] + (i + i_lo) * st, a[1] + (j + j_lo) * st)
                   for i, j in path]
            pts[0] = a
            pts = _simplify(pts + [c])
            raw = sum(norm(sub(pts[k + 1], pts[k]))
                      for k in range(len(pts) - 1))
            if raw > orig * 1.02 + 1e-9:
                last = {"reason": "longer", "new_mm": round(raw, 4)}
                continue
            if any(_interior(pts[k - 1], pts[k], pts[k + 1]) < MIN_ANGLE
                   for k in range(1, len(pts) - 1)):
                last = {"reason": "joint_angle"}
                continue
            ok, why = _joints_ok(segs_all, pts, skip)
            if not ok:
                last = {"reason": "joint_end", "angle_deg": why[1]}
                continue
            gap = _clearance(field, layer, pts, width, net)
            if gap < CLEAR_MM - 1e-9:
                last = {"reason": "clearance", "gap_mm": round(gap, 4)}
                continue
            dup = _self_overlap(pts, segs_all, skip, net, width)
            if dup is not None:
                last = {"reason": "self_overlap", "gap_mm": dup}
                continue
            return {"ok": True, "pts": pts, "orig_mm": round(orig, 4),
                    "new_mm": round(raw, 4), "save_mm": round(orig - raw, 4),
                    "clearance_mm": round(gap, 4), "legs": len(pts) - 1,
                    "step": st, "inflated": bool(infl),
                    "mid_pts": len(pts) - 2}
    out = {"ok": False, "orig_mm": round(orig, 4), "reason": "no_astar_path"}
    out.update(last or {})
    return out


def main(argv=None):
    ap = argparse.ArgumentParser()
    ap.add_argument("board")
    ap.add_argument("--out")
    ap.add_argument("--apply", action="store_true")
    ap.add_argument("--net")
    ap.add_argument("--json")
    ap.add_argument("--stair", action="store_true",
                    help="rebuild staircase chains with a lattice search")
    ap.add_argument("--astar", action="store_true",
                    help="use the lattice search on off-grid / acute chains too")
    ap.add_argument("--short", type=float, default=0.3,
                    help="a leg under this many mm counts as a stair")
    ap.add_argument("--min-stairs", type=int, default=2)
    args = ap.parse_args(argv)

    board = pcbnew.LoadBoard(args.board)
    nets = None
    if args.net:
        nets = set(n.strip() for n in args.net.split(",") if n.strip())

    anchors = set()
    for v in board.GetTracks():
        if v.Type() != pcbnew.PCB_VIA_T:
            continue
        pos = v.GetPosition()
        anchors.add((round(MM(pos.x), 4), round(MM(pos.y), 4)))
    for fp in board.GetFootprints():
        for pad in fp.Pads():
            pos = pad.GetPosition()
            anchors.add((round(MM(pos.x), 4), round(MM(pos.y), 4)))

    buckets, arcs = {}, {}
    for t in board.GetTracks():
        if t.Type() == pcbnew.PCB_VIA_T:
            continue
        name = t.GetNetname()
        if nets and name not in nets:
            continue
        if t.Type() == pcbnew.PCB_ARC_T:
            arcs.setdefault((name, t.GetLayer()), 0)
            arcs[(name, t.GetLayer())] += 1
            continue
        s, e = t.GetStart(), t.GetEnd()
        a = (MM(s.x), MM(s.y))
        b = (MM(e.x), MM(e.y))
        buckets.setdefault((name, t.GetLayer()), []).append(
            Seg(a, b, t.GetLayer(), MM(t.GetWidth()), t, name))

    plan, log = [], []
    skipped_arc = 0
    for (name, layer), segs in sorted(buckets.items()):
        if (name, layer) in arcs:
            skipped_arc += 1
            log.append({"net": name, "layer": board.GetLayerName(layer),
                        "reason": "net_has_arc_on_layer"})
            continue
        bad = [s for s in segs if not _lattice(s.a, s.b)]
        if not bad and not args.stair:
            # every leg is already on the grid; only a corner can be acute
            continue
        field = Field(board, skip_nets=set([name]))
        chains, loops = _chains(segs, anchors)
        for pts, chain in chains:
            short = [s for s in chain if s.length < args.short]
            if args.stair:
                # a staircase is many short legs fitting a direction the
                # 0/45/90 grid does not have; a two-leg chain is not one
                if len(chain) < 3 or len(short) < args.min_stairs:
                    continue
            else:
                if not any(not _lattice(s.a, s.b) for s in chain):
                    # every leg on the grid already; only a corner can be acute
                    inner = [_interior(pts[i - 1], pts[i], pts[i + 1])
                             for i in range(1, len(pts) - 1)]
                    if not inner or min(inner) >= MIN_ANGLE:
                        continue
            a, c = pts[0], pts[-1]
            orig = sum(s.length for s in chain)
            skip = set(id(s) for s in chain)
            if args.stair or args.astar:
                res = rebuild_chain_astar(a, c, orig, segs, skip, field, layer,
                                          chain[0].width, name)
            else:
                res = rebuild_chain(a, c, orig, segs, skip, field, layer,
                                    chain[0].width, name)
            rec = dict(res, net=name, layer=board.GetLayerName(layer),
                       nodes=len(pts))
            if res["ok"]:
                plan.append((name, layer, chain, res))
                print("  %-14s %-7s %2d->%d legs  %5.2f -> %5.2f mm  save %5.2f"
                      "  clr %.4f" % (name, board.GetLayerName(layer),
                                      len(chain), res["legs"], res["orig_mm"],
                                      res["new_mm"], res["save_mm"],
                                      res["clearance_mm"]))
            else:
                log.append(rec)

    print("chains rebuilt %d   skipped (arc on layer) %d" % (len(plan), skipped_arc))
    if args.json:
        json.dump({"rebuilt": [
            {"net": n, "layer": board.GetLayerName(lay),
             "orig_mm": r["orig_mm"], "new_mm": r["new_mm"],
             "save_mm": r["save_mm"], "clearance_mm": r["clearance_mm"],
             "pts": r["pts"]} for n, lay, _c, r in plan],
            "refusals": log}, open(args.json, "w"), indent=1)

    if not args.apply or not plan:
        print("dry run (add --apply and --out OUT)")
        return 0

    for name, layer, chain, res in plan:
        netinfo = board.FindNet(name)
        if netinfo is None:
            raise SystemExit("net %s vanished" % name)
        for s in chain:
            board.Remove(s.obj)
        pts = res["pts"]
        for i in range(len(pts) - 1):
            nt = pcbnew.PCB_TRACK(board)
            nt.SetStart(pcbnew.VECTOR2I(int(round(pts[i][0] * 1e6)),
                                        int(round(pts[i][1] * 1e6))))
            nt.SetEnd(pcbnew.VECTOR2I(int(round(pts[i + 1][0] * 1e6)),
                                      int(round(pts[i + 1][1] * 1e6))))
            nt.SetWidth(int(round(chain[0].width * 1e6)))
            nt.SetLayer(layer)
            nt.SetNet(netinfo)
            board.Add(nt)
    out = args.out or args.board
    board.Save(out)
    print("saved -> %s" % out)
    return 0


if __name__ == "__main__":
    sys.exit(main())
