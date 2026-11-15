#!/usr/bin/env python
# -*- coding: utf-8 -*-
"""Take a length-tuning meander back out of copper that is already on the board.

Nothing here is a route.

Measured need. On the SOM board the DDR nets were length-matched by a 45 degree
saw: `/DDR_DQ0` walks from y -21.4 to y -14.9 in 63 steps of 0.2 mm. That saw is
1.57 mm of extra copper spread over 6.5 mm of corridor, i.e. it buys length at
0.24 mm per mm travelled. A rounded meander buys 0.57 mm per mm travelled
(`route.tune_board.arc_meander`), so the same 1.57 mm fits in 2.75 mm and the
other 3.7 mm can be a straight line. The saw is not wrong electrically; it is
what a board looks like when the length correction is smeared along the whole
run instead of being put in one place, and it is what a person calls "ugly".

This pass removes the smear. It finds, inside one net's chain on one layer, a
run whose length is much larger than its own chord, and replaces the whole run
with that chord - when, and only when

  * every point the run passes through stays within `--band` of the chord (so
    the chord is inside the corridor the copper already occupied, which is why
    replacing it cannot bring copper closer to a neighbour),
  * the chord is on the 45 degree grid this package routes on (0/45/90/135),
    because replacing an off-grid saw with an off-grid line moves the problem,
  * the chord clears every other net's pads, tracks and arcs on that layer by at
    least the netclass clearance, measured exactly, and
  * the run keeps both of its endpoints, so the net is still connected.

A run that fails any of those is left exactly as it was and reported with the
reason. The tool never adds a via, never moves a pad and never changes a net.

The point of the pass is the retune that follows: once the saw is gone,
`route.tune_board.plan(style="arc")` can put ONE rounded meander where it
belongs instead of stacking a second shape on top of the first.

Usage:  untune.py BOARD [--apply] [--only NET,NET] [--prefix /DDR_]
                        [--band 1.20] [--ratio 1.15] [--json OUT]
"""
import argparse
import json
import math
import os
import sys

import pcbnew

HERE = os.path.dirname(os.path.abspath(__file__))
REPO = os.path.dirname(HERE)
sys.path.insert(0, os.path.join(REPO, "src"))

MM = pcbnew.ToMM
CLEAR_MM = 0.10
# Below this the "run" is not tuning, it is the path: replacing copper with an
# equal-length corner only adds churn to the diff.
MIN_SAVE = 0.05
GRID_ANGLE_TOL = 0.5
CU_LAYERS = (pcbnew.F_Cu, pcbnew.In1_Cu, pcbnew.In2_Cu, pcbnew.In3_Cu,
             pcbnew.In4_Cu, pcbnew.B_Cu)


# ----------------------------------------------------------------- geometry
def sub(a, b):
    return (a[0] - b[0], a[1] - b[1])


def norm(v):
    return math.hypot(v[0], v[1])


def point_seg_dist(p, a, b):
    d = sub(b, a)
    L2 = d[0] * d[0] + d[1] * d[1]
    if L2 <= 1e-18:
        return norm(sub(p, a))
    t = ((p[0] - a[0]) * d[0] + (p[1] - a[1]) * d[1]) / L2
    t = max(0.0, min(1.0, t))
    return norm(sub(p, (a[0] + t * d[0], a[1] + t * d[1])))


def _orient(p, q, r):
    return (q[0] - p[0]) * (r[1] - p[1]) - (q[1] - p[1]) * (r[0] - p[0])

def _within(p, q, r):
    return (min(p[0], r[0]) - 1e-9 <= q[0] <= max(p[0], r[0]) + 1e-9 and
            min(p[1], r[1]) - 1e-9 <= q[1] <= max(p[1], r[1]) + 1e-9)

def seg_seg_dist(a, b, c, d):
    """Distance between two segments, with a crossing read as ZERO.

    The four-endpoint minimum is not a distance: two segments that cross have
    every endpoint far from the other segment, so the minimum of the four
    endpoint pulls comes back positive and a chord laid straight through a
    neighbour looks legal. That is exactly how the first applied pass cut
    /DDR_DQ2 across /DDR_DQ13 at a gap of 0.0 mm while this function reported
    0.13 mm. The proper orientation test comes first now.
    """
    d1, d2 = _orient(c, d, a), _orient(c, d, b)
    d3, d4 = _orient(a, b, c), _orient(a, b, d)
    if (((d1 > 1e-12 and d2 < -1e-12) or (d1 < -1e-12 and d2 > 1e-12)) and
            ((d3 > 1e-12 and d4 < -1e-12) or (d3 < -1e-12 and d4 > 1e-12))):
        return 0.0
    for v, e1, e2 in ((a, c, d), (b, c, d), (c, a, b), (d, a, b)):
        o = _orient(e1, e2, v)
        if abs(o) <= 1e-12 and _within(e1, v, e2):
            return 0.0
    return min(point_seg_dist(a, c, d), point_seg_dist(b, c, d),
               point_seg_dist(c, a, b), point_seg_dist(d, a, b))


def rect_point_dist(lo, hi, p):
    dx = max(lo[0] - p[0], 0.0, p[0] - hi[0])
    dy = max(lo[1] - p[1], 0.0, p[1] - hi[1])
    return math.hypot(dx, dy)


def rect_seg_dist(lo, hi, a, b):
    n = max(2, int(norm(sub(b, a)) / 0.05) + 1)
    best = 1e9
    for i in range(n + 1):
        t = i / float(n)
        p = (a[0] + (b[0] - a[0]) * t, a[1] + (b[1] - a[1]) * t)
        best = min(best, rect_point_dist(lo, hi, p))
    return best


def angle_mod180(a, b):
    ang = math.degrees(math.atan2(b[1] - a[1], b[0] - a[0])) % 180.0
    return ang


def on_45_grid(a, b, tol=GRID_ANGLE_TOL):
    ang = angle_mod180(a, b)
    for legal in (0.0, 45.0, 90.0, 135.0, 180.0):
        if abs(ang - legal) <= tol:
            return True
    return False


def polyline_length(pts):
    return sum(norm(sub(pts[i + 1], pts[i])) for i in range(len(pts) - 1))


# --------------------------------------------------------------- the board
def collect(board, nets):
    """Every track and arc of `nets`, bucketed by (net, layer)."""
    per = {}
    for t in board.GetTracks():
        if t.Type() == pcbnew.PCB_VIA_T:
            continue
        name = t.GetNetname()
        if name not in nets:
            continue
        s, e = t.GetStart(), t.GetEnd()
        a = (MM(s.x), MM(s.y))
        b = (MM(e.x), MM(e.y))
        item = {"obj": t, "uuid": t.m_Uuid.AsString(), "layer": t.GetLayer(),
                "width": MM(t.GetWidth()), "a": a, "b": b,
                "arc": t.Type() == pcbnew.PCB_ARC_T,
                "length": MM(t.GetLength()) if t.Type() == pcbnew.PCB_ARC_T
                else norm(sub(b, a))}
        per.setdefault((name, t.GetLayer()), []).append(item)
    return per


def chains(items):
    """Walk `items` into ordered polylines, each edge used exactly once.

    A chain whose ends are the only loose ends is the shape a meander lives on;
    a chain that branches has no single "the run" to remove, so the caller sees
    whatever the walk produced and the band test decides. Walking every edge from
    both of its ends is what produced a doubled run list on the first attempt -
    each path came out twice, once in each direction - so the edges are marked
    used globally here.
    """
    key = lambda p: (round(p[0], 4), round(p[1], 4))
    adj = {}
    for i, it in enumerate(items):
        adj.setdefault(key(it["a"]), []).append(i)
        adj.setdefault(key(it["b"]), []).append(i)
    used = set()
    out = []

    def free(pt):
        for i in adj.get(pt, ()):
            if i not in used:
                return i
        return None

    # Prefer starting at an open end, so a path is walked through and not from
    # its middle outwards.
    order = sorted(range(len(items)),
                   key=lambda i: (0 if (len(adj[key(items[i]["a"])]) == 1
                                        or len(adj[key(items[i]["b"])]) == 1)
                                  else 1, i))
    for first in order:
        if first in used:
            continue
        used.add(first)
        it = items[first]
        ka, kb = key(it["a"]), key(it["b"])
        chain = [it]
        pts = [ka, kb]
        cur = kb
        while True:
            i = free(cur)
            if i is None:
                break
            used.add(i)
            n = items[i]
            other = key(n["b"]) if key(n["a"]) == cur else key(n["a"])
            chain.append(n)
            pts.append(other)
            cur = other
        cur = ka
        while True:
            i = free(cur)
            if i is None:
                break
            used.add(i)
            n = items[i]
            other = key(n["b"]) if key(n["a"]) == cur else key(n["a"])
            chain.insert(0, n)
            pts.insert(0, other)
            cur = other
        out.append((pts, chain))
    return out


def find_runs(pts, band, ratio, min_edges=3, min_chord=0.4):
    """Maximal runs whose length is much more than their own chord."""
    n = len(pts)
    runs = []
    i = 0
    while i < n - min_edges:
        best = None
        j = i + min_edges
        while j < n:
            a, b = pts[i], pts[j]
            chord = norm(sub(b, a))
            if chord < min_chord:
                j += 1
                continue
            wide = max(abs(_lateral(pts[k], a, b)) for k in range(i + 1, j))
            if wide > band:
                break
            if polyline_length(pts[i:j + 1]) / chord >= ratio:
                best = j
            j += 1
        if best is None:
            i += 1
        else:
            runs.append((i, best))
            i = best
    return runs


def _lateral(p, a, b):
    d = sub(b, a)
    L = norm(d)
    if L <= 1e-12:
        return norm(sub(p, a))
    return ((p[0] - a[0]) * (-d[1]) + (p[1] - a[1]) * d[0]) / L


# ------------------------------------------------------------- clearance
class Field(object):
    """Foreign copper per layer, bucketed on a 1 mm raster."""

    def __init__(self, board, skip_nets):
        # Nothing is left out at build time. The copper around a DDR net is
        # mostly the OTHER DDR nets, so raising them out of the field is how the
        # first run wrote 12 real clearance violations and the second run left
        # one chord straight through a neighbour. A net under replacement is
        # filtered per query instead, by `nearest(..., skip_net=net)`.
        self.cells = {}
        for t in board.GetTracks():
            if t.Type() == pcbnew.PCB_VIA_T:
                continue
            if t.GetNetname() in skip_nets:
                continue
            lay = t.GetLayer()
            s, e = t.GetStart(), t.GetEnd()
            a = (MM(s.x), MM(s.y))
            b = (MM(e.x), MM(e.y))
            w = MM(t.GetWidth())
            if t.Type() == pcbnew.PCB_ARC_T:
                bb = t.GetBoundingBox()
                lo = (MM(bb.GetLeft()) - w, MM(bb.GetTop()) - w)
                hi = (MM(bb.GetRight()) + w, MM(bb.GetBottom()) + w)
                self._put(lay, ("rect", lo, hi, w / 2.0, t.GetNetname()),
                          (lo[0], lo[1]), (hi[0], hi[1]))
            else:
                self._put(lay, ("seg", a, b, w / 2.0, t.GetNetname()), a, b)
        # Vias are copper too, and a chord laid through a plane via is a short.
        # They are drilled through every layer, so each one is registered on all
        # of them at its pad diameter.
        for v in board.GetTracks():
            if v.Type() != pcbnew.PCB_VIA_T:
                continue
            if v.GetNetname() in skip_nets:
                continue
            pos = v.GetPosition()
            c = (MM(pos.x), MM(pos.y))
            r = MM(v.GetWidth(pcbnew.F_Cu)) / 2.0
            for lay in CU_LAYERS:
                self._put(lay, ("circ", c, r, r, v.GetNetname()), c, c)
        for fp in board.GetFootprints():
            for p in fp.Pads():
                if p.GetNetname() in skip_nets:
                    continue
                pos = p.GetPosition()
                c = (MM(pos.x), MM(pos.y))
                r = MM(p.GetSize().x) / 2.0
                for lay in CU_LAYERS:
                    if not p.IsOnLayer(lay):
                        continue
                    self._put(lay, ("circ", c, r, r, p.GetNetname()), c, c)

    def _put(self, lay, rec, lo, hi):
        for cx in range(int(math.floor(min(lo[0], hi[0]) - 2.0)),
                        int(math.floor(max(lo[0], hi[0]) + 2.0)) + 1):
            for cy in range(int(math.floor(min(lo[1], hi[1]) - 2.0)),
                            int(math.floor(max(lo[1], hi[1]) + 2.0)) + 1):
                self.cells.setdefault((lay, cx, cy), []).append(rec)

    def nearest(self, lay, a, b, half_width, skip_net):
        """Clearance from segment a-b (grown by its half width) to foreign copper."""
        best = None
        cx0 = int(math.floor(min(a[0], b[0]) - 2.0))
        cx1 = int(math.floor(max(a[0], b[0]) + 2.0))
        cy0 = int(math.floor(min(a[1], b[1]) - 2.0))
        cy1 = int(math.floor(max(a[1], b[1]) + 2.0))
        seen = set()
        for cx in range(cx0, cx1 + 1):
            for cy in range(cy0, cy1 + 1):
                for rec in self.cells.get((lay, cx, cy), ()):
                    if id(rec) in seen:
                        continue
                    seen.add(id(rec))
                    if rec[4] == skip_net:
                        continue
                    kind = rec[0]
                    if kind == "seg":
                        d = seg_seg_dist(a, b, rec[1], rec[2]) - rec[3] - half_width
                    elif kind == "rect":
                        d = rect_seg_dist(rec[1], rec[2], a, b) - rec[3] - half_width
                    else:
                        d = (point_seg_dist(rec[1], a, b) - rec[2] - half_width)
                    if best is None or d < best:
                        best = d
        return best


# ------------------------------------------------------------------ main
def main(argv=None):
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("board")
    ap.add_argument("--apply", action="store_true")
    ap.add_argument("--only", default=None,
                    help="comma separated net names (default: by --prefix)")
    ap.add_argument("--prefix", default="/DDR_")
    ap.add_argument("--band", type=float, default=1.20)
    ap.add_argument("--ratio", type=float, default=1.15)
    ap.add_argument("--json", default=None)
    ap.add_argument("--ops", default=None, help="write the plan to this json file")
    args = ap.parse_args(argv)

    board = pcbnew.LoadBoard(args.board)
    if board is None:
        raise SystemExit("cannot read %s" % args.board)

    all_nets = set()
    for t in board.GetTracks():
        n = t.GetNetname()
        if n:
            all_nets.add(n)
    if args.only:
        nets = set(n.strip() for n in args.only.split(",") if n.strip())
    else:
        nets = set(n for n in all_nets if n.startswith(args.prefix))
    missing = sorted(nets - all_nets)
    nets &= all_nets

    field = Field(board, skip_nets=())
    per = collect(board, nets)

    ops = []
    report = {"board": args.board, "band_mm": args.band, "ratio": args.ratio,
              "nets": sorted(nets), "missing": missing, "lanes": {}}
    removed = 0
    for (net, lay), items in sorted(per.items()):
        lay_name = board.GetLayerName(lay)
        for pts, chain in chains(items):
            runs = find_runs(pts, args.band, args.ratio)
            if not runs:
                continue
            # last run first: replacing one shifts no index because we work on
            # whole-chain indices and emit deletes by uuid
            for (i, j) in reversed(runs):
                a, b = pts[i], pts[j]
                chord = norm(sub(b, a))
                # The run's real length, arcs included: a chord polyline would
                # understate a run that carries arcs and make the saving look
                # bigger than it is.
                span = sum(it["length"] for it in chain[i:j])
                entry = report["lanes"].setdefault(net, {"before_mm": 0.0,
                                                         "removed_mm": 0.0,
                                                         "runs": []})
                row = {"layer": lay_name,
                       "start": [round(a[0], 3), round(a[1], 3)],
                       "end": [round(b[0], 3), round(b[1], 3)],
                       "edges": j - i, "chord_mm": round(chord, 3),
                       "span_mm": round(span, 3),
                       "saved_mm": round(span - chord, 3)}
                width = chain[i]["width"]
                # The replacement is a chord when the chord is itself on the
                # 45 grid, otherwise the two axis legs through one corner - a
                # straight saw whose ends are 3 degrees off vertical is still a
                # saw, and an off-grid chord would only move the problem into
                # `board_shape_gate`.
                cands = []
                if on_45_grid(a, b):
                    cands.append([a, b])
                cands.append([a, (a[0], b[1]), b])
                cands.append([a, (b[0], a[1]), b])
                pick = None
                why = None
                for legs in cands:
                    legs = [p for k, p in enumerate(legs)
                            if k == 0 or norm(sub(p, legs[k - 1])) > 1e-6]
                    if len(legs) < 2:
                        continue
                    if any(not on_45_grid(legs[k], legs[k + 1])
                           for k in range(len(legs) - 1)):
                        why = why or "off_45_grid"
                        continue
                    bad = None
                    gaps = []
                    for k in range(len(legs) - 1):
                        g = field.nearest(lay, legs[k], legs[k + 1],
                                          width / 2.0, net)
                        gaps.append(g)
                        if g is not None and g < CLEAR_MM - 1e-6:
                            bad = g
                    if bad is not None:
                        why = "clearance"
                        row["clearance_mm"] = round(bad, 4)
                        continue
                    pick = legs
                    row["clearance_mm"] = (None if all(g is None for g in gaps)
                                            else round(min(g for g in gaps
                                                           if g is not None), 4))
                    break
                if pick is not None and span - polyline_length(pick) < MIN_SAVE:
                    pick = None
                    why = "no_shorter_alternative"
                if pick is None:
                    row["refused"] = why or "off_45_grid"
                    row["angle_deg"] = round(angle_mod180(a, b), 1)
                    entry["runs"].append(row)
                    continue
                row["refused"] = None
                row["legs"] = len(pick) - 1
                row["new_mm"] = round(polyline_length(pick), 3)
                row["saved_mm"] = round(span - polyline_length(pick), 3)
                entry["runs"].append(row)
                for it in chain[i:j]:
                    ops.append({"op": "delete_track", "uuid": it["uuid"],
                                "include_arcs": bool(it["arc"])})
                # The plan interpreter takes Y up while pcbnew hands back Y
                # down (`edit/ops.py::_verify_add_track` compares -file_y), so
                # every Y is negated here. The first apply wrote the chords
                # mirrored onto the empty side of the board, which is why it
                # showed up as 26 new unrouted nets instead of new copper.
                for k in range(len(pick) - 1):
                    ops.append({"op": "add_track",
                                "x1": round(pick[k][0], 6),
                                "y1": round(-pick[k][1], 6),
                                "x2": round(pick[k + 1][0], 6),
                                "y2": round(-pick[k + 1][1], 6),
                                "width": width, "layer": lay_name, "net": net})
                removed += 1

    # lengths before / after, from the two plans
    def net_lengths(bd):
        out = {}
        for t in bd.GetTracks():
            if t.Type() == pcbnew.PCB_VIA_T:
                continue
            n = t.GetNetname()
            if n not in nets:
                continue
            out[n] = out.get(n, 0.0) + (
                MM(t.GetLength()) if t.Type() == pcbnew.PCB_ARC_T
                else norm(sub((MM(t.GetEnd().x), MM(t.GetEnd().y)),
                              (MM(t.GetStart().x), MM(t.GetStart().y)))))
        return out

    before = net_lengths(board)
    for n, v in before.items():
        report["lanes"].setdefault(n, {"before_mm": 0.0, "removed_mm": 0.0,
                                       "runs": []})["before_mm"] = round(v, 3)

    print("nets %d   chains walked   runs removed %d   ops %d"
          % (len(nets), removed, len(ops)))
    if missing:
        print("not on this board: %s" % missing)
    short = []
    for n in sorted(report["lanes"]):
        e = report["lanes"][n]
        ok = sum(1 for r in e["runs"] if r.get("refused") is None)
        bad = [r for r in e["runs"] if r.get("refused")]
        print("  %-13s %8.3f mm  runs %d ok %d refused %d  saved %.3f"
              % (n, e["before_mm"], len(e["runs"]), ok, len(bad),
                 sum(r["saved_mm"] for r in e["runs"] if r.get("refused") is None)))
        for r in bad:
            short.append((n, r))
    for n, r in short[:20]:
        print("  REFUSED %-12s %-7s %5.1f,%-5.1f %s %s"
              % (n, r["layer"], r["start"][0], r["start"][1], r["refused"],
                 r.get("angle_deg") or r.get("clearance_mm")))

    if args.json:
        with open(args.json, "w") as fh:
            json.dump(report, fh, indent=1)

    if args.ops:
        with open(args.ops, "w") as fh:
            json.dump(ops, fh, indent=1)
    if not args.apply:
        print("dry run (add --apply to write)")
        return 0
    if not ops:
        print("nothing to do")
        return 0

    from pcb_agent_mcp.edit import plan as P
    pl = P.propose(args.board, ops, {"engine": True, "allow_new_violations": True,
                                     "precheck_clearance_mm": CLEAR_MM})
    if pl.get("blocking"):
        print("BLOCKED %s" % json.dumps(pl["blocking"], default=str)[:600])
        return 2
    res = P.apply_plan(pl["plan_id"], confirm=True, options={"engine": True})
    print("applied -> %s  %s" % (res.get("status"), res.get("path")))
    return 0


if __name__ == "__main__":
    sys.exit(main())
