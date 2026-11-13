"""Channel template for the two-via nets.  Nothing here is a router.

Every net handled here already has both ends broken out to a through via by
``tools/fanout_som.py``.  The template is one shape, stated once:

    via A --45deg--> (xm, .) --vertical--> (xm, .) --45deg--> via B

``xm`` walks a fixed candidate list (A.x, B.x, the midpoint, then 0.5 mm
steps across the gap), and the layers are tried in the fixed order In2, In4,
B.Cu (In1 and In3 carry the planes).  The first candidate whose whole
polyline is clear is taken; there is no cost function and no backtracking.
A net that finds nothing is counted and listed.

Clearance is held in a 0.02 mm grid that stores the *owning net* per cell, so
a net's own via and its own stub are not obstacles to itself; a cell claimed
by two different nets becomes -1 and blocks everyone.  The grid marks
obstacle discs at radius + 0.15 mm (0.1 rule plus grid margin).

The measured wall is in docs/150: the escape vias sit 0.562 mm apart, which
leaves 0.162 mm of legal track-centre space between them, so a straight
channel exists but cannot be moved sideways.  That is why this template lands
only part of the bus and the rest are printed as REFUSED.
"""

# The original one-line disclaimer, kept word for word:
# Nothing here is a generic router.  It is one stated template:
#
#   via A --45deg--> (xm, .) --vertical--> (xm, .) --45deg--> via B
#
# where xm is walked through a fixed candidate list (A.x, B.x, midpoint,
# then 0.5 mm steps across the channel).  The first candidate whose whole
# polyline is clear on a layer, in the fixed layer order In2, In4, B.Cu,
# is taken.  A net that finds nothing is counted and listed, never moved
# somewhere else and never retried with a cost function.
import json
import math
import sys

import numpy as np
import pcbnew

BOARD = sys.argv[1] if len(sys.argv) > 1 else (
    "D:/工作/git_project/pcb_agent_mcp/examples/rk3506_som/rk3506_som.kicad_pcb")
OUT = sys.argv[2] if len(sys.argv) > 2 else "bus_ops.json"
STEP = 0.02
N = int(round(45.0 / STEP)) + 1
TRACK_W = 0.10
CLR = 0.15   # 0.1 rule + 0.05 grid margin
EDGE = 0.30
LAYERS = ["In2.Cu", "In4.Cu", "B.Cu"]

b = pcbnew.LoadBoard(BOARD)
LM = {b.GetLayerName(i): i for i in range(b.GetCopperLayerCount() + 2)}
LM.update({pcbnew.LayerName(k): k for k in range(pcbnew.PCB_LAYER_ID_COUNT)
           if pcbnew.LayerName(k) != ""})


def name_of(i):
    return pcbnew.LayerName(i)


def idx(v):
    return int(round(v / STEP))


def mark_disc(g, x, y, r, nid=1):
    """g stores the owning net id per cell; -1 means "more than one net".
    A cell owned by the net being routed is not an obstacle to that net."""
    i0, i1 = max(0, idx(y - r)), min(N, idx(y + r) + 1)
    j0, j1 = max(0, idx(x - r)), min(N, idx(x + r) + 1)
    if i0 >= i1 or j0 >= j1:
        return
    ys = (np.arange(i0, i1) * STEP - y) ** 2
    xs = (np.arange(j0, j1) * STEP - x) ** 2
    m = ys[:, None] + xs[None, :] <= r * r
    sub = g[i0:i1, j0:j1]
    bad = m & (sub != nid)
    nz = sub != 0            # snapshot before any write; the two masks below
    sub[bad & ~nz] = nid     # are disjoint, so order does not matter
    sub[bad & nz] = -1


def mark_seg(g, x1, y1, x2, y2, r, nid=1):
    d = math.hypot(x2 - x1, y2 - y1)
    n = max(1, int(d / (STEP * 0.5)))
    for k in range(n + 1):
        t = k / float(n)
        mark_disc(g, x1 + (x2 - x1) * t, y1 + (y2 - y1) * t, r, nid)


grids = {L: np.zeros((N, N), dtype=np.int16) for L in LAYERS}
# board edge
for L in LAYERS:
    g = grids[L]
    k = idx(EDGE)
    g[:k, :] = -1
    g[-k:, :] = -1
    g[:, :k] = -1
    g[:, -k:] = -1

NETID = {}
for t in b.GetTracks():
    for nm in (t.GetNetname(),):
        if nm and nm not in NETID:
            NETID[nm] = len(NETID) + 1
for fp in b.GetFootprints():
    for pd in fp.Pads():
        nm = pd.GetNetname()
        if nm and nm not in NETID:
            NETID[nm] = len(NETID) + 1


def nid_of(nm):
    return NETID.get(nm, 1)


vias = []
for t in b.GetTracks():
    if isinstance(t, pcbnew.PCB_VIA):
        p = t.GetPosition()
        x, y = pcbnew.ToMM(p.x), pcbnew.ToMM(p.y)
        vias.append({"x": x, "y": -y, "net": t.GetNetname(),
                     "d": pcbnew.ToMM(t.GetWidth(pcbnew.F_Cu))})
        for L in LAYERS:
            mark_disc(grids[L], x, -y, t.GetWidth(pcbnew.F_Cu) / 2e6 + CLR,
                      nid_of(t.GetNetname()))
    else:
        s, e = t.GetStart(), t.GetEnd()
        x1, y1 = pcbnew.ToMM(s.x), -pcbnew.ToMM(s.y)
        x2, y2 = pcbnew.ToMM(e.x), -pcbnew.ToMM(e.y)
        L = name_of(t.GetLayer())
        if L in grids:
            mark_seg(grids[L], x1, y1, x2, y2, t.GetWidth() / 2e6 + CLR,
                     nid_of(t.GetNetname()))

pads = []
for fp in b.GetFootprints():
    for p in fp.Pads():
        sz = p.GetSize()
        pos = p.GetPosition()
        layers = []
        for L in LAYERS:
            if p.IsOnLayer(LM[L]):
                layers.append(L)
        pads.append({"x": pcbnew.ToMM(pos.x), "y": -pcbnew.ToMM(pos.y),
                     "r": max(sz.x, sz.y) / 2e6, "layers": layers,
                     "net": p.GetNetname()})
        for L in layers:
            mark_disc(grids[L], pcbnew.ToMM(pos.x), -pcbnew.ToMM(pos.y),
                      max(sz.x, sz.y) / 2e6 + CLR, nid_of(p.GetNetname()))

# A net needs a route when its two escape vias are not already joined by
# existing copper.  Union-find over the vias of one net; a track joins the two
# vias it touches (0.02 mm tolerance).
TOL = 0.02
segs_by_net = {}
for t in b.GetTracks():
    if not isinstance(t, pcbnew.PCB_VIA):
        s0, e0 = t.GetStart(), t.GetEnd()
        segs_by_net.setdefault(t.GetNetname(), []).append(
            (pcbnew.ToMM(s0.x), -pcbnew.ToMM(s0.y),
             pcbnew.ToMM(e0.x), -pcbnew.ToMM(e0.y)))
by_net = {}
for v in vias:
    by_net.setdefault(v["net"], []).append(v)


def on_via(v, x, y):
    return abs(v["x"] - x) <= TOL and abs(v["y"] - y) <= TOL



todo = []
for net, vs in by_net.items():
    if not net or net.startswith("/NC_") or net.startswith("unconnected-"):
        continue
    if len(vs) != 2:
        continue
    par = {0: 0, 1: 1}

    def find(i):
        while par[i] != i:
            i = par[i]
        return i

    for x1, y1, x2, y2 in segs_by_net.get(net, []):
        for i, v in enumerate(vs):
            if on_via(v, x1, y1) or on_via(v, x2, y2):
                for j2, w in enumerate(vs):
                    if j2 != i and (on_via(w, x1, y1) or on_via(w, x2, y2)):
                        par[find(i)] = find(j2)
    if find(0) == find(1):
        continue
    todo.append((net, vs[0], vs[1]))
todo.sort(key=lambda t: (round(min(t[1]["y"], t[2]["y"]), 2), t[0]))


def clear_line(L, pts, nid):
    g = grids[L]
    for i in range(len(pts) - 1):
        x1, y1 = pts[i]
        x2, y2 = pts[i + 1]
        d = math.hypot(x2 - x1, y2 - y1)
        n = max(1, int(d / (STEP * 0.5)))
        for k in range(n + 1):
            t = k / float(n)
            x, y = x1 + (x2 - x1) * t, y1 + (y2 - y1) * t
            a, bb = idx(y), idx(x)
            if not (0 <= a < N and 0 <= bb < N):
                return False
            o = g[a, bb]
            if o != 0 and o != nid:
                return False
    return True


def build(A, B, xm):
    u, w = xm - A["x"], B["x"] - xm
    t = B["y"] - A["y"]
    s = 1.0 if t > 0 else -1.0
    if abs(t) + 1e-9 < abs(u) + abs(w):
        return None
    pts = [(A["x"], A["y"])]
    if abs(u) > 1e-6:
        pts.append((xm, A["y"] + s * abs(u)))
    if abs(w) > 1e-6:
        pts.append((xm, B["y"] - s * abs(w)))
    pts.append((B["x"], B["y"]))
    # drop duplicates
    out = [pts[0]]
    for p in pts[1:]:
        if math.hypot(p[0] - out[-1][0], p[1] - out[-1][1]) > 1e-6:
            out.append(p)
    return out


def cands(A, B):
    xs = [A["x"], B["x"], round((A["x"] + B["x"]) / 2.0, 4)]
    lo, hi = sorted((A["x"], B["x"]))
    x = math.ceil(lo * 2) / 2.0
    while x < hi:
        xs.append(round(x, 4))
        x += 0.5
    seen = set()
    out = []
    for xm in xs:
        k = round(xm, 4)
        if k in seen:
            continue
        seen.add(k)
        out.append(k)
    return out


placed = []
refused = []
for net, A, B in todo:
    hit = None
    for L in LAYERS:
        for xm in cands(A, B):
            pts = build(A, B, xm)
            if pts is None:
                continue
            if clear_line(L, pts, nid_of(net)):
                hit = (L, pts)
                break
        if hit:
            break
    if not hit:
        refused.append("%s %.2f,%.2f -> %.2f,%.2f"
                       % (net, A["x"], A["y"], B["x"], B["y"]))
        continue
    L, pts = hit
    segs = []
    for i in range(len(pts) - 1):
        x1, y1 = pts[i]
        x2, y2 = pts[i + 1]
        segs.append([round(x1, 4), round(y1, 4), round(x2, 4), round(y2, 4)])
        mark_seg(grids[L], x1, y1, x2, y2, TRACK_W / 2.0 + CLR, nid_of(net))
    placed.append({"net": net, "layer": L, "segs": segs})

print("todo %d placed %d refused %d" % (len(todo), len(placed), len(refused)))
for r in refused:
    print("  REFUSED", r)
by_layer = {}
for p in placed:
    by_layer[p["layer"]] = by_layer.get(p["layer"], 0) + 1
print("by layer", by_layer)
json.dump(placed, open(OUT, "w"), indent=1)
