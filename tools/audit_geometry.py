#!/usr/bin/env python
# -*- coding: utf-8 -*-
"""Measure the shape of the copper that is already on a board.

Three things a routed board is judged on here, and nothing else:

  * **corners** — every vertex where two segments of the same net meet is one of
    ``0`` (straight), ``45`` or ``90`` degrees of turn. Anything else is bad:
    a turn greater than 90 degrees is an *acute* inside corner (acid trap,
    impedance kink, and it is what makes a layout look hand-drawn), a turn
    between 0 and 90 that is not 45 is a bend off the 45 degree grid, and a
    turn of 180 is a fold-back that reuses the same copper twice.
  * **detour** — the copper length of a net against the straight distance
    between its two furthest endpoints. A layout that goes the wrong way round
    a component shows up here as a ratio far above the rest of the board.
  * **layer hops** — how many times a net changes layer, i.e. how many vias it
    needs to reach a target that is almost straight ahead.

Nothing here is a route. This script only reads; it never writes the board.
Usage:  audit_geometry.py BOARD.kicad_pcb [--top N] [--json OUT.json]
"""
import json
import math
import sys

import pcbnew

MM = pcbnew.ToMM
# Vertices are compared at 1e-4 mm so that two copies of the same coordinate in
# the file always land on the same node.
ND = 4
ANGLE_TOL = 0.5          # degrees; a 45/90 corner measured off by 0.5 is still 45/90


def _key(x, y):
    return (round(x, ND), round(y, ND))


def _angle(u, v):
    """Turn from direction u to direction v, in degrees, 0..180."""
    nu = math.hypot(*u)
    nv = math.hypot(*v)
    if nu < 1e-9 or nv < 1e-9:
        return None
    c = (u[0] * v[0] + u[1] * v[1]) / (nu * nv)
    c = max(-1.0, min(1.0, c))
    return math.degrees(math.acos(c))


def _chains(edges):
    """Split a set of segments (per layer) into polylines."""
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
        chain = [a, b]
        # extend forward
        while True:
            tail, prev = chain[-1], chain[-2]
            nxt = [p for p in adj.get(tail, []) if p != prev and (tail, p) not in used]
            if not nxt:
                break
            p = nxt[0]
            used.add((tail, p))
            used.add((p, tail))
            chain.append(p)
        # extend backward
        while True:
            head, prev = chain[0], chain[1]
            prv = [p for p in adj.get(head, []) if p != prev and (p, head) not in used]
            if not prv:
                break
            p = prv[0]
            used.add((p, head))
            used.add((head, p))
            chain.insert(0, p)
        out.append(chain)
    return out


def _seg_len(p, q):
    return math.hypot(q[0] - p[0], q[1] - p[1])


def _classify(turn):
    if turn is None:
        return "degenerate"
    for want in (0.0, 45.0, 90.0, 180.0):
        if abs(turn - want) <= ANGLE_TOL:
            return {0.0: "straight", 45.0: "corner135",
                    90.0: "corner90", 180.0: "foldback"}[want]
    return "acute" if turn > 90.0 else "offgrid"


def audit(path, top=25):
    board = pcbnew.LoadBoard(path)
    if board is None:
        raise SystemExit("cannot read %s" % path)
    lay_name = {i: board.GetLayerName(i) for i in range(pcbnew.PCB_LAYER_ID_COUNT)}

    pads = {}          # net -> [ (x, y) ] ; a corner at a pad centre is a T, not a bend
    for fp in board.GetFootprints():
        for pad in fp.Pads():
            p = pad.GetPosition()
            pads.setdefault(pad.GetNetname(), []).append(_key(MM(p.x), MM(p.y)))

    per_layer = {}     # (net, layer) -> [ (a, b) ]
    vias = {}          # net -> [ (x, y) ]
    for t in list(board.GetTracks()):
        net = t.GetNetname()
        if t.Type() == pcbnew.PCB_VIA_T:
            p = t.GetPosition()
            vias.setdefault(net, []).append(_key(MM(p.x), MM(p.y)))
            continue
        s, e = t.GetStart(), t.GetEnd()
        a = _key(MM(s.x), MM(s.y))
        b = _key(MM(e.x), MM(e.y))
        if a == b:
            per_layer.setdefault((net, t.GetLayer()), []).append((a, b))
            continue
        per_layer.setdefault((net, t.GetLayer()), []).append((a, b))

    nets = {}
    for (net, lay), edges in per_layer.items():
        rec = nets.setdefault(net, {"segs": 0, "vias": 0, "length": 0.0, "layers": set(),
                                    "acute": [], "offgrid": [], "foldback": [],
                                    "ends": {}, "hops": 0})
        rec["segs"] += len(edges)
        rec["layers"].add(lay_name.get(lay, str(lay)))
        for a, b in edges:
            rec["length"] += _seg_len(a, b)
        adj = {}
        for a, b in edges:
            adj.setdefault(a, []).append(b)
            adj.setdefault(b, []).append(a)
        for a, b in edges:
            for v, prev, nxt in ((b, a, None), (a, None, b)):
                pass
        # corners: at every degree-2 node, the turn between the two segments
        for node, nb in adj.items():
            if len(nb) != 2:
                continue
            if any(abs(node[0] - p[0]) < 0.05 and abs(node[1] - p[1]) < 0.05 for p in pads.get(net, ())):
                continue   # copper fanning out of one pad: the angle here means nothing
            p, q = nb[0], nb[1]
            u = (node[0] - p[0], node[1] - p[1])
            w = (q[0] - node[0], q[1] - node[1])
            kind = _classify(_angle(u, w))
            if kind in ("acute", "offgrid", "foldback"):
                rec[kind].append({"at": node, "deg": round(_angle(u, w), 2), "layer": lay_name.get(lay, str(lay))})
        # endpoints of each chain become candidates for the "far apart" pair
        for ch in _chains(edges):
            for node in (ch[0], ch[-1]):
                if len(adj.get(node, [])) <= 1:
                    rec["ends"][node] = True
    for net, pts in vias.items():
        if net in nets:
            nets[net]["vias"] = len(pts)

    rows = []
    for net, rec in nets.items():
        ends = list(rec.pop("ends").keys())
        direct = 0.0
        for i in range(len(ends)):
            for j in range(i + 1, len(ends)):
                d = _seg_len(ends[i], ends[j])
                if d > direct:
                    direct = d
        ratio = (rec["length"] / direct) if direct > 1e-6 else 0.0
        rows.append({
            "net": net, "segs": rec["segs"], "vias": rec["vias"],
            "layers": sorted(rec["layers"]), "length": round(rec["length"], 3),
            "direct": round(direct, 3), "ratio": round(ratio, 3),
            "acute": rec["acute"], "offgrid": rec["offgrid"], "foldback": rec["foldback"],
        })
    rows.sort(key=lambda r: (-len(r["acute"]) - len(r["offgrid"]), -r["ratio"]))
    return rows


def main(argv):
    if len(argv) < 2:
        raise SystemExit(__doc__)
    path = argv[1]
    top = 25
    out_json = None
    i = 2
    while i < len(argv):
        if argv[i] == "--top":
            top = int(argv[i + 1]); i += 2
        elif argv[i] == "--json":
            out_json = argv[i + 1]; i += 2
        else:
            raise SystemExit("unknown argument %r" % argv[i])
    rows = audit(path, top)
    na = sum(len(r["acute"]) for r in rows)
    no = sum(len(r["offgrid"]) for r in rows)
    nf = sum(len(r["foldback"]) for r in rows)
    print("nets %d   acute %d   offgrid %d   foldback %d" % (len(rows), na, no, nf))
    bad = [r for r in rows if r["acute"] or r["offgrid"] or r["foldback"]]
    for r in bad[:top]:
        print("  %-18s segs %3d vias %2d  acute %2d offgrid %2d fold 1?%d  ratio %.2f" % (
            r["net"], r["segs"], r["vias"], len(r["acute"]), len(r["offgrid"]),
            len(r["foldback"]), r["ratio"]))
        for c in (r["acute"] + r["offgrid"] + r["foldback"])[:3]:
            print("      %.3f,%.3f  %.2f deg  %s" % (c["at"][0], c["at"][1], c["deg"], c["layer"]))
    worst = sorted(rows, key=lambda r: -r["ratio"])[:8]
    print("worst detour:")
    for r in worst:
        print("  %-18s ratio %5.2f  len %7.3f  direct %7.3f  segs %3d vias %2d" % (
            r["net"], r["ratio"], r["length"], r["direct"], r["segs"], r["vias"]))
    if out_json:
        with open(out_json, "w", encoding="utf-8", newline="\n") as fh:
            json.dump(rows, fh, ensure_ascii=False, indent=1)
        print("json -> %s" % out_json)
    return 0


if __name__ == "__main__":
    sys.exit(main(sys.argv))
