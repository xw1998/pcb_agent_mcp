# Nothing here is a route.  Every net already has one; this tool can only ADD
# copper to an existing net, one 45-degree trombone at a time, on a segment the
# net already runs on.  The reference of a group is therefore its LONGEST
# member: a net that is too long cannot be fixed by a meander, that is a
# re-route.  Each tooth is checked against foreign copper on its own layer at
# 0.05 half-width + 0.10 clearance before it is emitted, and the emitted plan
# is delete_copper(net) + add_track(every original and meandered segment) +
# add_via(every original through via), so a net is rewritten whole and never
# left half-meandered.
# Order of the rules, tightest first (a meander that breaks a pair to fix a lane
# is a regression):
#   1. intra-pair skew     DQS0P/N, DQS1P/N, CLKP/N
#   2. within-byte-lane    DQ0-7+DM0 vs DQS0, DQ8-15+DM1 vs DQS1
#   3. addr/cmd/control vs CLK
#   4. inter-lane          lane0 vs lane1
import json, math, os, sys
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import pcbnew
import copper_geo as PG

BOARD, OUT = sys.argv[1], sys.argv[2]
GAP = 0.15            # our half 0.05 + clearance 0.10
A_MAX = 0.35
PAD = 0.20            # axial lead in / out of a tooth block


def groups_from(lengths):
    lane0 = ["DQ0", "DQ1", "DQ2", "DQ3", "DQ4", "DQ5", "DQ6", "DQ7", "DM0"]
    lane1 = ["DQ8", "DQ9", "DQ10", "DQ11", "DQ12", "DQ13", "DQ14", "DQ15", "DM1"]
    cmd = ["A0", "A1", "A2", "A3", "A4", "A5", "A6", "A7", "A8", "A9", "A10",
           "A11", "A12", "A13", "A14", "BA0", "BA1", "BA2", "CSN", "RASN",
           "CASN", "WEN", "ODT", "RESET", "CKE"]
    pairs = [("DQS0P", "DQS0N"), ("DQS1P", "DQS1N"), ("CLKP", "CLKN")]
    out = []
    for p, n in pairs:
        out.append(("pair %s/%s" % (p, n), [p, n]))
    out.append(("lane0", lane0))
    out.append(("lane1", lane1))
    out.append(("cmd", cmd + ["CLKP"]))
    return out


b = pcbnew.LoadBoard(BOARD)
items, holes = PG.build_items(b)


def segs_of(net):
    out = []

    def visit(x1, y1, x2, y2, layer):
        out.append((x1, y1, x2, y2, layer))

    for t in b.GetTracks():
        if t.GetNetname() != net or isinstance(t, pcbnew.PCB_VIA):
            continue
        s, e = t.GetStart(), t.GetEnd()
        visit(pcbnew.ToMM(s.x), -pcbnew.ToMM(s.y),
              pcbnew.ToMM(e.x), -pcbnew.ToMM(e.y),
              pcbnew.LayerName(t.GetLayer()))
    return out


def vias_of(net):
    out = []
    for t in b.GetTracks():
        if t.GetNetname() != net or not isinstance(t, pcbnew.PCB_VIA):
            continue
        p = t.GetPosition()
        out.append((pcbnew.ToMM(p.x), -pcbnew.ToMM(p.y),
                    t.GetWidth(pcbnew.F_Cu) / 1e6, t.GetDrill() / 1e6))
    return out


def length_of(segs):
    return sum(math.hypot(s[2] - s[0], s[3] - s[1]) for s in segs)


NAMES = [k[len("/DDR_"):] for k in {t.GetNetname() for t in b.GetTracks()}
         if k.startswith("/DDR_")]
CUR = {n: length_of(segs_of("/DDR_" + n)) for n in NAMES}

targets = []
for gname, members in groups_from(CUR):
    have = [(m, CUR.get(m)) for m in members if CUR.get(m)]
    if len(have) < 2:
        continue
    ref = max(v for _, v in have)
    for m, v in have:
        d = ref - v
        if d > 0.05:
            targets.append((gname, m, d))
targets.sort(key=lambda t: -t[2])
print("targets", len(targets), flush=True)
for g, m, d in targets:
    print("  %-16s %-6s add %.3f" % (g, m, d), flush=True)


def objs_near(s0, rad):
    x0 = min(s0[0], s0[2]) - rad
    x1 = max(s0[0], s0[2]) + rad
    y0 = min(s0[1], s0[3]) - rad
    y1 = max(s0[1], s0[3]) + rad
    out = []
    for it in items:
        g = it['geom']
        if it['kind'] == 'disc':
            ix0, ix1, iy0, iy1 = g[0], g[0], g[1], g[1]
        elif it['kind'] == 'seg':
            ix0, ix1 = min(g[0], g[2]), max(g[0], g[2])
            iy0, iy1 = min(g[1], g[3]), max(g[1], g[3])
        else:
            ix0, ix1 = g[0] - g[2], g[0] + g[2]
            iy0, iy1 = g[1] - g[3], g[1] + g[3]
        if ix1 < x0 or ix0 > x1 or iy1 < y0 or iy0 > y1:
            continue
        out.append(it)
    return out


def profile(s0, layer, net, side):
    """(t, free offset) along the centreline, on one side, at 0.05 mm steps."""
    x1, y1, x2, y2 = s0[0], s0[1], s0[2], s0[3]
    L = math.hypot(x2 - x1, y2 - y1)
    if L < 1e-6:
        return [], L
    ux, uy = (x2 - x1) / L, (y2 - y1) / L
    nx, ny = -uy * side, ux * side
    near = [it for it in objs_near(s0, A_MAX + 0.5)
            if it['net'] != net and layer in it['layers']]
    if not near:
        return [(k * 0.05, A_MAX) for k in range(int(L / 0.05) + 1)], L
    out = []
    for k in range(int(L / 0.05) + 1):
        tt = k * 0.05
        px, py = x1 + ux * tt, y1 + uy * tt
        d = min(PG.dist_item(it, px, py) for it in near)
        out.append((tt, max(0.0, d - 0.05 - 0.10)))
    return out, L


def best_run(prof, want, need_len):
    """longest stretch whose free width stays >= want and is long enough"""
    best = None
    i = 0
    n = len(prof)
    while i < n:
        if prof[i][1] >= want:
            j = i
            while j + 1 < n and prof[j + 1][1] >= want:
                j += 1
            span = prof[j][0] - prof[i][0]
            if span >= need_len and (best is None or span > best[1] - best[0]):
                best = (prof[i][0], prof[j][0])
            i = j + 1
        else:
            i += 1
    return best


def build(s0, side, A, k, t0=0.0):
    x1, y1, x2, y2 = s0[0], s0[1], s0[2], s0[3]
    L = math.hypot(x2 - x1, y2 - y1)
    ux, uy = (x2 - x1) / L, (y2 - y1) / L
    nx, ny = -uy * side, ux * side
    pts = [(x1, y1)]
    if t0 > 1e-9:
        pts.append((x1 + ux * t0, y1 + uy * t0))
    s = t0
    for _ in range(k):
        pts.append((x1 + ux * (s + A) + nx * A, y1 + uy * (s + A) + ny * A))
        s += 2 * A
        pts.append((x1 + ux * s, y1 + uy * s))
    pts.append((x2, y2))
    return [(pts[i][0], pts[i][1], pts[i + 1][0], pts[i + 1][1], s0[4])
            for i in range(len(pts) - 1)]


def ok_segs(new, net, layer, s0):
    near = [it for it in objs_near(s0, A_MAX + 0.6)
            if it['net'] != net and layer in it['layers']]
    for s in new:
        for it in near:
            if PG.dist_seg(it, s) < GAP - 1e-9:
                return False
    return True


ops = []
report = []
for gname, name, need in targets:
    net = "/DDR_" + name
    segs = segs_of(net)
    order = sorted(range(len(segs)), key=lambda i: -math.hypot(
        segs[i][2] - segs[i][0], segs[i][3] - segs[i][1]))
    added = 0.0
    newsegs = list(segs)
    used = set()
    for rep in range(4):
      if added >= need - 0.02:
        break
      for i in order:
        if i in used or added >= need - 0.02:
            continue
        s = segs[i]
        L = math.hypot(s[2] - s[0], s[3] - s[1])
        if L < 4 * PAD + 1.0:
            continue
        cand = None
        best = None
        for side in (1.0, -1.0):
            prof, _ = profile(s, s[4], net, side)
            for want in (0.30, 0.25, 0.20, 0.15, 0.12):
                run = best_run(prof, want, 4 * PAD + 2 * want)
                if run is None:
                    continue
                A = min(A_MAX, want * 0.85)
                pitch = 2 * A + PAD
                kmax = int((run[1] - run[0] - 2 * PAD) / pitch)
                if kmax < 1:
                    continue
                per = (2 * A * math.sqrt(2) - 2 * A)
                k = min(kmax, max(1, int(math.ceil((need - added) / per))))
                while k >= 1:
                    c = build(s, side, A, k, run[0] + PAD)
                    if ok_segs(c, net, s[4], s):
                        cand, best = c, k * per
                        break
                    k -= 1
                if cand:
                    break
            if cand:
                break
        if not cand:
            continue
        added += best
        newsegs[i] = cand
        used.add(i)
        continue
    if not used:
        report.append((gname, name, need, 0.0))
        continue
    flat = []
    for seg in newsegs:
        if isinstance(seg, list):
            flat.extend(seg)
        else:
            flat.append(seg)
    ops.append({"op": "delete_copper", "net": net})
    by_layer = {}
    for s in flat:
        by_layer.setdefault(s[4], []).append(list(s[:4]))
    for L, ss in by_layer.items():
        ops.append({"net": net, "layer": L, "segs": ss})
    vs = vias_of(net)
    if vs:
        ops.append({"net": net, "vias": vs, "sizes": vs})
    report.append((gname, name, need, added))

json.dump({"ops": ops, "report": report}, open(OUT, "w"), indent=1)
print("---")
for gname, name, need, added in report:
    print("%-16s %-6s need %.3f added %.3f %s"
          % (gname, name, need, added, "" if added >= need - 0.02 else "SHORT"), flush=True)
