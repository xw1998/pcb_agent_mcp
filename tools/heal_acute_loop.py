#!/usr/bin/env python3
"""Repair an acute corner by *removing* the copper that makes it, when the
copper is redundant.  Nothing here is a route.

`chamfer_acute.py` fixes an acute corner by shortening both legs and joining the
two new points, and it says so: three or more legs at one vertex is a judgement
call and it refuses.  Those refusals are real defects, not noise.  On this board
all three of them come from the same habit -- the router closed a *loop* or hung
a *stub* off a through line:

  * a 0.65 mm pad row fan-out that goes A -> up-45 -> right -> down-45 -> B and
    is ALSO connected by the direct 45 from A to B.  The three pieces form a
    triangle; the copper codirectional with another piece reads as a 0 degree
    corner at one end and a 45 degree corner at the other.
  * a 45 tap that lands on a through line: the diagonal and the line above it
    meet at 45, and no lattice path can meet a through line from a 45 tap at
    anything but 45 on one side.  The tap is a T, and a T drawn as a diagonal is
    an acid trap.

In both cases one or two of the pieces is carrying nothing.  Deleting it is
strictly better copper: same connection, less copper, no acid trap.

The search is deliberately small and total:

  1. find every acute joint the same way `chamfer_acute` does (`interior` under
     90 by more than one degree, so the two tools cannot disagree),
  2. take the same-net same-layer segments at or near that joint,
  3. try deleting subsets up to --max-drop pieces,
  4. keep a subset only if, with it gone,
       - no joint left in the touched neighbourhood is still acute,
       - no endpoint left behind is dangling (one ray and not on a pad or via),
       - every pad and via that touched the neighbourhood is still touching it,
  5. prefer the smallest subset, then the longest copper removed (dead copper
     that is long is the more obvious defect).

Deleting is checked against the board's own connectivity count before anything is
written; a run that raises it is refused and nothing is saved.

    python tools/heal_acute_loop.py BOARD OUT [--apply] [--max-drop 3]
    python tools/heal_acute_loop.py BOARD OUT --only 20.675,26.050 --only 10.2,3.9
"""
import argparse
import itertools
import math
import sys

import pcbnew

MM = pcbnew.ToMM
ACUTE_TOL = 1.0
NEAR = 1.30
SNAP = 0.002


def _pt(v):
    return (MM(v.x), MM(v.y))


def _key(p):
    return (round(p[0], 3), round(p[1], 3))


def fetch(board):
    """Every straight track as a plain record; arcs and vias are separate."""
    segs, vias, pads = [], [], []
    for t in board.GetTracks():
        if isinstance(t, pcbnew.PCB_VIA):
            r = MM(t.GetWidth(pcbnew.F_Cu)) / 2.0
            vias.append({"p": _pt(t.GetPosition()), "r": r,
                         "net": t.GetNetname(), "obj": t})
            continue
        if t.Type() == pcbnew.PCB_ARC_T:
            continue
        a, c = _pt(t.GetStart()), _pt(t.GetEnd())
        segs.append({"a": a, "c": c, "net": t.GetNetname(), "lay": t.GetLayer(),
                     "w": MM(t.GetWidth()), "obj": t})
    for f in board.GetFootprints():
        for p in f.Pads():
            if not p.IsOnCopperLayer() and not p.GetLayerSet().Contains(pcbnew.F_Cu):
                pass
            bb = p.GetBoundingBox()
            pads.append({"net": p.GetNetname(),
                         "x0": min(bb.GetLeft(), bb.GetRight()) / 1e6,
                         "x1": max(bb.GetLeft(), bb.GetRight()) / 1e6,
                         "y0": min(bb.GetTop(), bb.GetBottom()) / 1e6,
                         "y1": max(bb.GetTop(), bb.GetBottom()) / 1e6,
                         "obj": p})
    return segs, vias, pads


def d_point_seg(p, s):
    ax, ay = s["a"]
    bx, by = s["c"]
    dx, dy = bx - ax, by - ay
    l2 = dx * dx + dy * dy
    if l2 < 1e-18:
        return math.hypot(p[0] - ax, p[1] - ay)
    u = max(0.0, min(1.0, ((p[0] - ax) * dx + (p[1] - ay) * dy) / l2))
    return math.hypot(p[0] - (ax + u * dx), p[1] - (ay + u * dy))


def dist_point_seg_point(p, s):
    return min(math.hypot(p[0] - s["a"][0], p[1] - s["a"][1]),
               math.hypot(p[0] - s["c"][0], p[1] - s["c"][1]), d_point_seg(p, s))


def interior(far1, v, far2):
    r1 = math.atan2(far1[1] - v[1], far1[0] - v[0])
    r2 = math.atan2(far2[1] - v[1], far2[0] - v[0])
    d = abs(math.degrees(r1 - r2)) % 360.0
    if d > 180.0:
        d = 360.0 - d
    return d


def rays_at(segs, net, lay, v, skip=()):
    """(far point, segment) for every same-net segment with an endpoint at v."""
    out = []
    for s in segs:
        if id(s["obj"]) in skip:
            continue
        if s["net"] != net or s["lay"] != lay:
            continue
        for p, far in ((s["a"], s["c"]), (s["c"], s["a"])):
            if abs(p[0] - v[0]) <= SNAP and abs(p[1] - v[1]) <= SNAP:
                out.append((far, s))
    return out


def acutes(segs, net, lay, vertices, skip=()):
    """Acute joints among the given vertices, after removing `skip`."""
    bad = []
    for v in vertices:
        rs = rays_at(segs, net, lay, v, skip)
        uniq = {}
        for far, s in rs:
            uniq.setdefault(_key(far), far)
        fars = list(uniq.values())
        for i in range(len(fars)):
            for j in range(i + 1, len(fars)):
                ang = interior(fars[i], v, fars[j])
                if ang < 90.0 - ACUTE_TOL:
                    bad.append((v, fars[i], fars[j], ang))
    return bad


def _interior_of(p, s, eps=1e-6):
    """Is p strictly inside segment s, not at either end?"""
    ax, ay = s["a"]
    bx, by = s["c"]
    dx, dy = bx - ax, by - ay
    l2 = dx * dx + dy * dy
    if l2 < 1e-18:
        return False
    u = ((p[0] - ax) * dx + (p[1] - ay) * dy) / l2
    if u <= eps or u >= 1.0 - eps:
        return False
    return math.hypot(p[0] - (ax + u * dx), p[1] - (ay + u * dy)) <= SNAP + 1e-9


def covered(p, net, lay, segs, vias, pads, skip=(), strict=False):
    """Is p on copper of this net on this layer (track body, via or pad)?

    `strict` counts only copper that p lands *inside* -- the middle of somebody
    else's track, a pad, a via.  An endpoint of the same segment that p came
    from is not coverage: that is exactly the dangling end the caller is asking
    about, and counting it made this audit pass a stub whose only neighbour was
    itself.
    """
    for s in segs:
        if id(s["obj"]) in skip:
            continue
        if s["net"] != net or s["lay"] != lay:
            continue
        if strict:
            if _interior_of(p, s):
                return True
        elif d_point_seg(p, s) <= SNAP + 1e-9:
            return True
    for v in vias:
        if v["net"] == net and math.hypot(v["p"][0] - p[0], v["p"][1] - p[1]) <= v["r"]:
            return True
    for q in pads:
        if q["net"] == net and q["x0"] - SNAP <= p[0] <= q["x1"] + SNAP and \
                q["y0"] - SNAP <= p[1] <= q["y1"] + SNAP:
            return True
    return False


def _heading(s, a_to_c=True):
    ax, ay = s["a"] if a_to_c else s["c"]
    bx, by = s["c"] if a_to_c else s["a"]
    return math.degrees(math.atan2(by - ay, bx - ax)) % 180.0


def _axis_aligned(s):
    h = _heading(s)
    return min(abs(h - 0.0), abs(h - 90.0), abs(h - 180.0)) <= 0.5


def _diagonal(s):
    h = _heading(s)
    return abs(h - 45.0) <= 0.5 or abs(h - 135.0) <= 0.5


def _line_meet(s1, s2):
    """Where the two infinite lines cross, or None when they are parallel."""
    x1, y1 = s1["a"]
    x2, y2 = s1["c"]
    x3, y3 = s2["a"]
    x4, y4 = s2["c"]
    d = (x1 - x2) * (y3 - y4) - (y1 - y2) * (x3 - x4)
    if abs(d) < 1e-12:
        return None
    a = x1 * y2 - y1 * x2
    b = x3 * y4 - y3 * x4
    return ((a * (x3 - x4) - (x1 - x2) * b) / d,
            (a * (y3 - y4) - (y1 - y2) * b) / d)


def dangling(segs, net, lay, skip, vias, pads):
    """Endpoints that end in mid air after the deletion."""
    out = []
    seen = set()
    for s in segs:
        if id(s["obj"]) in skip:
            continue
        if s["net"] != net or s["lay"] != lay:
            continue
        for p in (s["a"], s["c"]):
            k = _key(p)
            if k in seen:
                continue
            seen.add(k)
            if len(rays_at(segs, net, lay, p, skip)) != 1:
                continue
            if covered(p, net, lay, segs, vias, pads, skip, strict=True):
                continue
            out.append(p)
    return out


SLIDE_MAX = 0.80


def anchors_at(v, net, lay, vias, pads, near=2.5):
    """Pads and vias of this net around the joint, as (centre, radius)."""
    out = []
    for q in vias:
        if q["net"] != net:
            continue
        if math.hypot(q["p"][0] - v[0], q["p"][1] - v[1]) <= near:
            out.append((q["p"], q["r"]))
    for q in pads:
        if q["net"] != net:
            continue
        c = ((q["x0"] + q["x1"]) / 2.0, (q["y0"] + q["y1"]) / 2.0)
        if math.hypot(c[0] - v[0], c[1] - v[1]) <= near:
            out.append((c, 0.5 * min(q["x1"] - q["x0"], q["y1"] - q["y0"])))
    return out


def local_segs(v, near, net, lay, segs):
    return [s for s in segs
            if s["net"] == net and s["lay"] == lay
            and dist_point_seg_point(v, s) <= near]


def _groups(anchors, segs):
    """Connected groups of the pads and vias that this copper ties together.

    A deletion is only redundant if it does not split them, and "the far end is
    still on some copper" is not enough to know that: an island made of one pad,
    one via and one 45 degree stub is still an island.  The graph here is local
    on purpose -- tracks, endpoints and the pads/vias that touch them -- and the
    endpoint-on-body rule is the T junction, which is how a track legally joins
    the middle of another one.
    """
    parent = {}

    def find(x):
        parent.setdefault(x, x)
        while parent[x] != x:
            parent[x] = parent[parent[x]]
            x = parent[x]
        return x

    def union(x, y):
        rx, ry = find(x), find(y)
        if rx != ry:
            parent[rx] = ry

    nodes = []
    for s in segs:
        ka, kc = _key(s["a"]), _key(s["c"])
        union(("e", ka), ("e", kc))
        nodes.append((ka, kc, s))
    for ka, kc, s in nodes:
        for pk in (ka, kc):
            for _ka, _kc, t in nodes:
                if t is s:
                    continue
                if _interior_of(pk, t):
                    union(("e", pk), ("e", _key(t["a"])))
    for i, (p, r) in enumerate(anchors):
        tag = ("a", i)
        find(tag)
        for ka, kc, t in nodes:
            if math.hypot(p[0] - t["a"][0], p[1] - t["a"][1]) <= SNAP or \
                    math.hypot(p[0] - t["c"][0], p[1] - t["c"][1]) <= SNAP or \
                    _interior_of(p, t):
                union(tag, ("e", ka))
                break
        else:
            # a via or pad sitting inside another pad: no track needed
            for j, (q, r2) in enumerate(anchors):
                if i != j and math.hypot(p[0] - q[0], p[1] - q[1]) <= r + r2:
                    union(tag, ("a", j))
    groups = {}
    for i in range(len(anchors)):
        groups.setdefault(find(("a", i)), []).append(i)
    return frozenset(frozenset(v) for v in groups.values())


def keeps_anchors_together(anchors, before, after):
    """Every anchor pair that was joined before is still joined after."""
    gb = _groups(anchors, before)
    ga = _groups(anchors, after)
    for pair in [frozenset(x) for x in _pairs(len(anchors))]:
        for grp in gb:
            if pair <= grp:
                if not any(pair <= g for g in ga):
                    return False
                break
    return True


def _pairs(n):
    out = []
    for i in range(n):
        for j in range(i + 1, n):
            out.append((i, j))
    return out


def slide_tap(segs, net, lay, v, vias, pads):
    """A 45 tap that lands on a through line, slid along the tap's own arm.

    A diagonal that taps a straight run of copper always meets it at 45 degrees
    on one side -- that is the acid trap.  If the diagonal's far end is the end
    of an axis aligned piece whose line crosses the through line, the tap can be
    slid to that crossing instead: the diagonal goes away, the axis piece is
    lengthened by the same small amount, and the joint becomes a plain T.  No new
    direction is introduced and nothing else moves.
    """
    hits = []
    uniq = {}
    for far, s in rays_at(segs, net, lay, v):
        uniq.setdefault((_key(far), id(s["obj"])), (far, s))
    items = list(uniq.values())
    for i in range(len(items)):
        for j in range(i + 1, len(items)):
            (f1, s1), (f2, s2) = items[i], items[j]
            if interior(f1, v, f2) >= 90.0 - ACUTE_TOL:
                continue
            diags = [x for x in ((f1, s1), (f2, s2)) if _diagonal(x[1])]
            straights = [x for x in ((f1, s1), (f2, s2)) if not _diagonal(x[1])]
            if len(diags) != 1 or len(straights) != 1:
                continue
            (F, D) = diags[0]
            (FL, L) = straights[0]
            _ = FL
            for E in segs:
                if E is D or E is L:
                    continue
                if E["net"] != net or E["lay"] != lay:
                    continue
                if not any(abs(p[0] - F[0]) <= SNAP and abs(p[1] - F[1]) <= SNAP
                           for p in (E["a"], E["c"])):
                    continue
                if not _axis_aligned(E):
                    continue
                N = _line_meet(E, L)
                if N is None:
                    continue
                step = math.hypot(N[0] - F[0], N[1] - F[1])
                if step < 0.02 or step > SLIDE_MAX:
                    continue
                rest = [s for s in segs if s is not E]
                if not covered(N, net, lay, rest, vias, pads, strict=True):
                    continue
                Ec = dict(E)
                if _key(E["a"]) == _key(F):
                    Ec["a"] = N
                else:
                    Ec["c"] = N
                sim = [Ec if s is E else s for s in segs]
                skip = set([id(D["obj"])])
                vs = [v, F, N, Ec["a"], Ec["c"]]
                if acutes(sim, net, lay, vs, skip):
                    continue
                if dangling(sim, net, lay, skip, vias, pads):
                    continue
                hits.append((D, E, F, N, Ec))
    return hits


def main(argv=None):
    ap = argparse.ArgumentParser()
    ap.add_argument("board")
    ap.add_argument("out")
    ap.add_argument("--apply", action="store_true")
    ap.add_argument("--max-drop", type=int, default=3)
    ap.add_argument("--only", action="append", default=None,
                    metavar="X,Y", help="restrict to these vertices")
    a = ap.parse_args(argv)

    board = pcbnew.LoadBoard(a.board)
    board.BuildConnectivity()
    uc0 = board.GetConnectivity().GetUnconnectedCount(False)
    segs, vias, pads = fetch(board)

    only = None
    if a.only:
        only = [tuple(float(v) for v in s.split(",")) for s in a.only]

    # Every joint, layer by layer, net by net.
    joints = {}
    for s in segs:
        for p in (s["a"], s["c"]):
            joints.setdefault((s["net"], s["lay"], _key(p)), p)

    found = []
    for (net, lay, k), v in sorted(joints.items()):
        if only is not None and not any(
                abs(v[0] - x) <= SNAP and abs(v[1] - y) <= SNAP for x, y in only):
            continue
        rs = rays_at(segs, net, lay, v)
        fars = list({_key(f): f for f, _s in rs}.values())
        hit = any(interior(fars[i], v, fars[j]) < 90.0 - ACUTE_TOL
                  for i in range(len(fars)) for j in range(i + 1, len(fars)))
        if hit:
            found.append((net, lay, v))

    if not found:
        print("no acute joints found; nothing to do")
        return 0

    plans = []
    for net, lay, v in found:
        # Candidate pieces: anything same net/layer that touches or runs within
        # NEAR of the joint.  A redundant loop side is reachable that way.
        cand = [s for s in segs
                if s["net"] == net and s["lay"] == lay
                and dist_point_seg_point(v, s) <= NEAR]
        verts = set()
        for s in cand:
            verts.add(_key(s["a"]))
            verts.add(_key(s["c"]))
        vmap = {}
        for s in cand:
            vmap[_key(s["a"])] = s["a"]
            vmap[_key(s["c"])] = s["c"]
        vlist = [vmap[k] for k in verts]

        best = None
        for n in range(1, a.max_drop + 1):
            for combo in itertools.combinations(cand, n):
                skip = set(id(s["obj"]) for s in combo)
                if acutes(segs, net, lay, vlist, skip):
                    continue
                if dangling(segs, net, lay, skip, vias, pads):
                    continue
                # Pads and vias that were on this copper must still be on it.
                lost = []
                for s in cand:
                    if id(s["obj"]) not in skip:
                        continue
                    for p in (s["a"], s["c"]):
                        if not covered(p, net, lay, segs, vias, pads, skip):
                            lost.append(p)
                if lost:
                    continue
                # An island of pad + via + stub is still an island: the pads and
                # vias this copper joins must stay joined.
                if not keeps_anchors_together(
                        anchors_at(v, net, lay, vias, pads),
                        local_segs(v, 2.5, net, lay, segs),
                        [s for s in local_segs(v, 2.5, net, lay, segs)
                         if id(s["obj"]) not in skip]):
                    continue
                gain = sum(math.hypot(s["c"][0] - s["a"][0], s["c"][1] - s["a"][1])
                           for s in combo)
                if best is None or (n, -gain) < (best[0], -best[1]):
                    best = (n, gain, combo)
            if best is not None:
                break
        if best is not None:
            plans.append({"net": net, "lay": lay, "v": v, "op": "drop",
                          "combo": best[2]})
            continue
        slides = slide_tap(segs, net, lay, v, vias, pads)
        if slides:
            D, E, F, N, Ec = slides[0]
            plans.append({"net": net, "lay": lay, "v": v, "op": "slide",
                          "D": D, "E": E, "F": F, "N": N})
            continue
        plans.append({"net": net, "lay": lay, "v": v, "op": None})

    drop = {}
    slides = []
    for pl in plans:
        net, lay, v, op = pl["net"], pl["lay"], pl["v"], pl["op"]
        tag = "%-16s %-7s acute at %.3f,%.3f : " % (
            net, board.GetLayerName(lay), v[0], v[1])
        if op is None:
            print(tag + "no redundant piece and no slide room, left alone")
            continue
        if op == "drop":
            combo = pl["combo"]
            print(tag + "drop %d piece(s)" % len(combo))
            for s in combo:
                L = math.hypot(s["c"][0] - s["a"][0], s["c"][1] - s["a"][1])
                print("      w %.3f  (%.3f,%.3f)->(%.3f,%.3f)  %.2f mm"
                      % (s["w"], s["a"][0], s["a"][1], s["c"][0], s["c"][1], L))
                drop[id(s["obj"])] = s
            continue
        D, E, F, N = pl["D"], pl["E"], pl["F"], pl["N"]
        moved = math.hypot(N[0] - F[0], N[1] - F[1])
        print(tag + "slide the 45 tap (%.3f,%.3f)->(%.3f,%.3f) into a T at "
              "(%.3f,%.3f), %.2f mm, then drop the diagonal" %
              (F[0], F[1], E["c"][0] if _key(E["a"]) == _key(F) else E["a"][0],
               E["c"][1] if _key(E["a"]) == _key(F) else E["a"][1],
               N[0], N[1], moved))
        slides.append(pl)
        drop[id(D["obj"])] = D

    print("acute joints %d   repaired %d   left alone %d   pieces dropped %d   "
          "taps slid %d"
          % (len(plans), sum(1 for p in plans if p["op"] is not None),
             sum(1 for p in plans if p["op"] is None), len(drop), len(slides)))
    if not a.apply:
        print("dry run (add --apply)")
        return 0
    if not drop and not slides:
        print("nothing to delete; board left untouched")
        return 0

    for pl in slides:
        E, F, N = pl["E"], pl["F"], pl["N"]
        obj = E["obj"]
        if _key(E["a"]) == _key(F):
            obj.SetStart(pcbnew.VECTOR2I(int(round(N[0] * 1e6)),
                                         int(round(N[1] * 1e6))))
        else:
            obj.SetEnd(pcbnew.VECTOR2I(int(round(N[0] * 1e6)),
                                       int(round(N[1] * 1e6))))
    for s in drop.values():
        board.Remove(s["obj"])
    board.BuildConnectivity()
    uc1 = board.GetConnectivity().GetUnconnectedCount(False)
    print("unconnected %d -> %d" % (uc0, uc1))
    if uc1 > uc0:
        raise SystemExit("REFUSED: the repair made the board less connected "
                         "(%d -> %d)" % (uc0, uc1))
    board.Save(a.out)
    print("saved -> %s  (dropped %d segments, slid %d taps)"
          % (a.out, len(drop), len(slides)))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
