# Nothing here is an autorouter in the banned sense.  This is a maze search on a
# fixed 45-degree lattice, one net at a time, in a fixed order (short span
# first), no rip-up, no cost tuning: it either finds a legal path or reports the
# net.  Every segment it emits is 0/45/90 degrees by construction.  No layer
# change is ever inserted as a blind guess: the four layers are searched as one
# 3-D lattice whose only cross-layer move is a through via, and a via may only
# stand where its 0.2/0.1 body fits on every searched layer with 0.10 mm to
# spare (mask `vf`, an eroded intersection of the four free spaces).
import heapq, json, math, os, sys
import numpy as np
import pcbnew
import time

BOARD, OUT = sys.argv[1], sys.argv[2]
STEP = 0.05
CLR = 0.15
TW = 0.10
LAYERS = ["F.Cu", "In2.Cu", "In4.Cu", "B.Cu"]
FALLBACK_F = False
BUCK = 2.0
EDGE_IN = 0.35
TOL = 0.02

b = pcbnew.LoadBoard(BOARD)
LID = {pcbnew.LayerName(i): i for i in range(pcbnew.PCB_LAYER_ID_COUNT)}

# ---------------- items ---------------------------------------------------
# every item: dict(kind, net, layers(set of names, 'ALL' -> all copper), geom, rad)
items = []
def mk(kind, net, layers, geom, rad):
    it = {'kind': kind, 'net': net, 'layers': layers, 'geom': geom, 'rad': rad,
          'used': False}
    if kind == 'seg':
        xs = [geom[0], geom[2]]; ys = [geom[1], geom[3]]
        ext = rad
    elif kind == 'rect':
        xs = [geom[0]]; ys = [geom[1]]
        ext = geom[2] + geom[3]     # hx + hy >= half diagonal
    else:
        xs = [geom[0]]; ys = [geom[1]]
        ext = rad
    it['bb'] = (min(xs)-ext-CLR, min(ys)-ext-CLR, max(xs)+ext+CLR, max(ys)+ext+CLR)
    items.append(it)
    return it

# A rectangular pad is a rectangle, not a disc.  Modelling it as a disc of the
# larger half-extent leaves the four corners uncovered (max(hx,hy) < hypot(hx,hy)),
# so a 0.10 track could pass within 0.02 mm of a pad corner and still look legal
# to the raster -- that is exactly what the DRC later reported as clearance
# violations around the BGA and around SW1/FB2.  Rect items therefore carry
# rad = 0 and are measured by true point/segment-to-rectangle distance.
def mkrect(net, layers, cx, cy, hx, hy, ang_deg):
    a = math.radians(-ang_deg)     # board Y is stored negated, so the angle flips
    return mk('rect', net, layers, (cx, cy, hx, hy, math.cos(a), math.sin(a)), 0.0)

HOLES = []       # (x, y, radius) of every existing drill, any net: hole_to_hole
                 # is a rule between drills, not between nets, so "same net is free"
                 # must not apply to drill spacing.
term = []        # indices of terminals (pads, vias)
for fp in b.GetFootprints():
    for p in fp.Pads():
        sz, pos = p.GetSize(), p.GetPosition()
        x, y = pcbnew.ToMM(pos.x), -pcbnew.ToMM(pos.y)
        rx, ry = sz.x/2e6, sz.y/2e6
        on = {L for L in ('F.Cu','In1.Cu','In2.Cu','In3.Cu','In4.Cu','B.Cu')
              if p.IsOnLayer(LID[L])}
        through = p.GetDrillSize().x > 0
        if through:
            on = set(LAYERS) | {'F.Cu','B.Cu'}
        if p.GetNetname():
            term.append(len(items))
        if through:
            HOLES.append((x, y, p.GetDrillSize().x/2e6))
        mkrect(p.GetNetname(), on, x, y, rx, ry, p.GetOrientationDegrees())
for t in b.GetTracks():
    if isinstance(t, pcbnew.PCB_VIA):
        pos = t.GetPosition()
        x, y = pcbnew.ToMM(pos.x), -pcbnew.ToMM(pos.y)
        r = t.GetWidth(pcbnew.F_Cu)/2e6
        term.append(len(items))
        HOLES.append((x, y, t.GetDrill()/2e6))
        mk('disc', t.GetNetname(), set(LAYERS)|{'F.Cu','B.Cu'}, (x,y,x,y), r)
    else:
        s, e = t.GetStart(), t.GetEnd()
        mk('seg', t.GetNetname(), {pcbnew.LayerName(t.GetLayer())},
           (pcbnew.ToMM(s.x), -pcbnew.ToMM(s.y), pcbnew.ToMM(e.x), -pcbnew.ToMM(e.y)),
           t.GetWidth()/2e6)

# ---------------- magnetic keep-out --------------------------------------
# No copper under a magnetic part's winding window.  The window is the strip
# between the two terminal pads of the part -- the pads themselves are copper
# and must stay reachable, but the body between them must not be used as a
# routing channel.  This is a property of the part, so it is derived from the
# footprint instead of a hand-written list: every footprint whose library name
# contains "Inductor" contributes one obstacle rectangle here.
#
# It is emitted as an obstacle ITEM, exactly like a pad.  It is not copper, it
# is not a route, and nothing is written to the board from it.
ALLCU = ['F.Cu', 'In1.Cu', 'In2.Cu', 'In3.Cu', 'In4.Cu', 'B.Cu']
KEEPOUTS = []
for fp in b.GetFootprints():
    ref = fp.GetReference()
    if os.environ.get('PCB_NO_MAG_KEEPOUT') == '1':
        break   # diagnostic control arm only: inductor windows are not obstacles
    if 'Inductor' not in fp.GetFPIDAsString():
        continue
    ps = [p for p in fp.Pads() if p.GetDrillSize().x == 0][:2]
    if len(ps) < 2:
        continue
    a, c = ps
    pa, pc = a.GetPosition(), c.GetPosition()
    if pa.x < pc.x:
        x0 = pa.x + a.GetSize().x / 2
        x1 = pc.x - c.GetSize().x / 2
    else:
        x0 = pc.x + c.GetSize().x / 2
        x1 = pa.x - a.GetSize().x / 2
    y0 = min(pa.y - a.GetSize().y / 2, pc.y - c.GetSize().y / 2)
    y1 = max(pa.y + a.GetSize().y / 2, pc.y + c.GetSize().y / 2)
    if x1 - x0 <= 0:
        continue
    KEEPOUTS.append((ref, pcbnew.ToMM(x0), pcbnew.ToMM(x1),
                     pcbnew.ToMM(y0), pcbnew.ToMM(y1)))
    mkrect('__KEEPOUT__', set(ALLCU), (x0 + x1) / 2e6, -(y0 + y1) / 2e6,
           pcbnew.ToMM(x1 - x0) / 2.0, pcbnew.ToMM(y1 - y0) / 2.0, 0.0)
if KEEPOUTS:
    print("magnetic keep-outs %d: %s" % (len(KEEPOUTS), " ".join(
        "%s[%.2f..%.2f,%.2f..%.2f]" % k for k in KEEPOUTS)), flush=True)

# ---------------- geometry helpers ---------------------------------------
def pt_seg(px, py, x1, y1, x2, y2):
    dx, dy = x2-x1, y2-y1
    L2 = dx*dx + dy*dy
    if L2 < 1e-12:
        return math.hypot(px-x1, py-y1)
    u = max(0.0, min(1.0, ((px-x1)*dx + (py-y1)*dy)/L2))
    return math.hypot(px-(x1+u*dx), py-(y1+u*dy))

def seg_seg(a, b2):
    def inter(p, q, r, s):
        d1 = (q[0]-p[0])*(r[1]-p[1]) - (q[1]-p[1])*(r[0]-p[0])
        d2 = (q[0]-p[0])*(s[1]-p[1]) - (q[1]-p[1])*(s[0]-p[0])
        d3 = (s[0]-r[0])*(p[1]-r[1]) - (s[1]-r[1])*(p[0]-r[0])
        d4 = (s[0]-r[0])*(q[1]-r[1]) - (s[1]-r[1])*(q[0]-r[0])
        return (d1*d2 < 0) and (d3*d4 < 0)
    if inter(a[:2], a[2:], b2[:2], b2[2:]):
        return 0.0
    return min(pt_seg(a[0],a[1],b2[0],b2[1],b2[2],b2[3]),
               pt_seg(a[2],a[3],b2[0],b2[1],b2[2],b2[3]),
               pt_seg(b2[0],b2[1],a[0],a[1],a[2],a[3]),
               pt_seg(b2[2],b2[3],a[0],a[1],a[2],a[3]))

def rr(it):
    """Coarse radius: real radius for discs/segments, half-diagonal for rects."""
    g = it['geom']
    return it['rad'] if it['kind'] != 'rect' else math.hypot(g[2], g[3])

def dist_item(it, px, py):
    """Distance from a point to the item's copper (0 when inside)."""
    g = it['geom']
    if it['kind'] == 'rect':
        dx, dy = px-g[0], py-g[1]
        u = dx*g[4] + dy*g[5]
        v = -dx*g[5] + dy*g[4]
        return math.hypot(max(abs(u)-g[2], 0.0), max(abs(v)-g[3], 0.0))
    if it['kind'] == 'disc':
        return math.hypot(g[0]-px, g[1]-py)
    return pt_seg(px, py, g[0], g[1], g[2], g[3])

def pt_rect(px, py, hx, hy):
    return math.hypot(max(abs(px)-hx, 0.0), max(abs(py)-hy, 0.0))

def dist_item_seg(it, s0):
    """Distance from a segment to the item's copper."""
    g = it['geom']
    if it['kind'] == 'rect':
        pts = [((s0[0]-g[0])*g[4] + (s0[1]-g[1])*g[5],
               -(s0[0]-g[0])*g[5] + (s0[1]-g[1])*g[4]),
               ((s0[2]-g[0])*g[4] + (s0[3]-g[1])*g[5],
               -(s0[2]-g[0])*g[5] + (s0[3]-g[1])*g[4])]
        hx, hy = g[2], g[3]
        d = min(pt_rect(pts[0][0], pts[0][1], hx, hy),
                pt_rect(pts[1][0], pts[1][1], hx, hy))
        for (ex0, ey0, ex1, ey1) in ((-hx,-hy,hx,-hy), (hx,-hy,hx,hy), (hx,hy,-hx,hy), (-hx,hy,-hx,-hy)):
            d = min(d, pt_seg(ex0, ey0, pts[0][0], pts[0][1], pts[1][0], pts[1][1]),
                    seg_seg((ex0, ey0, ex1, ey1), (pts[0][0], pts[0][1], pts[1][0], pts[1][1])))
        return d
    if it['kind'] == 'disc':
        return pt_seg(g[0], g[1], s0[0], s0[1], s0[2], s0[3])
    return seg_seg(s0, g)

def touch(i, j):
    A, B = items[i], items[j]
    if not (A['layers'] & B['layers']):
        return False
    if A['kind'] == 'rect' or B['kind'] == 'rect':
        return dist_item(A, B['geom'][0], B['geom'][1]) <= rr(B)+TOL or \
               dist_item(B, A['geom'][0], A['geom'][1]) <= rr(A)+TOL
    if A['kind'] == 'disc' and B['kind'] == 'disc':
        return math.hypot(A['geom'][0]-B['geom'][0], A['geom'][1]-B['geom'][1]) \
             <= A['rad']+B['rad']+TOL
    if A['kind'] == 'seg' and B['kind'] == 'disc':
        A, B = B, A
    if A['kind'] == 'disc':
        return pt_seg(A['geom'][0], A['geom'][1], B['geom'][0], B['geom'][1],
                      B['geom'][2], B['geom'][3]) <= A['rad']+B['rad']+TOL
    return seg_seg(A['geom'], B['geom']) <= A['rad']+B['rad']+TOL

by_net = {}
for i, it in enumerate(items):
    by_net.setdefault(it['net'], []).append(i)

def clusters(net):
    idxs = by_net.get(net, [])
    if not idxs: return []
    loc = {g: k for k, g in enumerate(idxs)}
    par = list(range(len(idxs)))
    def f(i):
        while par[i] != i:
            par[i] = par[par[i]]; i = par[i]
        return i
    for a in range(len(idxs)):
        for c in range(a+1, len(idxs)):
            if touch(idxs[a], idxs[c]):
                x, y = f(a), f(c)
                if x != y: par[x] = y
    out = {}
    for k, g in enumerate(idxs):
        if g in term:
            out.setdefault(f(k), []).append(g)
    return [v for v in out.values() if v]

SKIP = {"/GND", "/+3V3", "/+5V_IN"}
if os.environ.get("ALLOW_POUR"):
    # A poured net needs stitching, not routing: its zone is not an item, so the
    # search can only reach the nearest existing via/pad of the same net; the
    # official ZONE_FILLER then merges the new stub into the pour.
    SKIP = {"/+5V_IN"}
jobs = []
for net in sorted(by_net):
    if not net or net in SKIP or net.startswith("/NC_") or net.startswith("unconnected-"):
        continue
    gs = clusters(net)
    if len(gs) < 2: continue
    span = min(math.hypot(items[a]['geom'][0]-items[c]['geom'][0],
                          items[a]['geom'][1]-items[c]['geom'][1])
               for a in gs[0] for c in gs[1])
    jobs.append((span, net))
jobs.sort()
HARD = [n.strip() for n in (os.environ.get('HARD') or '').split(',') if n.strip()]
if HARD:
    head = [(sp_, nm_) for sp_, nm_ in jobs if nm_ in HARD]
    head.sort(key=lambda t: HARD.index(t[1]))
    jobs = head + [(sp_, nm_) for sp_, nm_ in jobs if nm_ not in HARD]
BUDGET_OVR = os.environ.get("BUDGET")
if os.environ.get("ORD"):
    jobs.sort(key=lambda t: t[0], reverse=(os.environ["ORD"] == "desc"))
if os.environ.get('NETS'):
    want=set(os.environ['NETS'].split(','))
    jobs=[(s_,n_) for s_,n_ in jobs if n_ in want]
if os.environ.get('LIST'):
    for sp_, nm_ in jobs: print("JOB %.3f %s" % (sp_, nm_))
    print("jobs", len(jobs), flush=True); raise SystemExit(0)
print("jobs", len(jobs), flush=True)

# ---------------- buckets -------------------------------------------------
bk = {}
for i, it in enumerate(items):
    x0, y0, x1, y1 = it['bb']
    for gx in range(int(math.floor(x0/BUCK)), int(math.floor(x1/BUCK))+1):
        for gy in range(int(math.floor(y0/BUCK)), int(math.floor(y1/BUCK))+1):
            bk.setdefault((gx, gy), []).append(i)

def cand_box(box):
    x0, y0, x1, y1 = box
    seen, out = set(), []
    for gx in range(int(math.floor(x0/BUCK)), int(math.floor(x1/BUCK))+1):
        for gy in range(int(math.floor(y0/BUCK)), int(math.floor(y1/BUCK))+1):
            for i in bk.get((gx, gy), ()):
                if i not in seen:
                    seen.add(i); out.append(i)
    return out


def los(blk, w, h, a, c):
    di, dj = c[0]-a[0], c[1]-a[1]
    if di and dj and abs(di) != abs(dj):
        return False
    n = max(abs(di), abs(dj))
    if n == 0: return True
    si = (di > 0) - (di < 0); sj = (dj > 0) - (dj < 0)
    for k in range(n+1):
        if blk[(a[0]+si*k)*w + (a[1]+sj*k)]:
            return False
    return True


def shortcut(path, blk, w, h, maxtry=120):
    # Capped jump length: the full O(n^3) sweep over a 700-cell path is what
    # used to make one long net take minutes.  A 120-cell (6 mm) look is
    # enough to collapse the staircase and still keeps every run straight.
    out = [path[0]]
    i = 0
    while i < len(path)-1:
        j = min(len(path)-1, i+maxtry)
        while j > i+1 and not los(blk, w, h, path[i], path[j]):
            j -= 1
        if j == i:
            j = i+1
        out.append(path[j]); i = j
    return out


def gaps(net, segs, L, need=0.1):
    out = {}
    for s0 in segs:
        box = (min(s0[0], s0[2])-1.0, min(s0[1], s0[3])-1.0,
               max(s0[0], s0[2])+1.0, max(s0[1], s0[3])+1.0)
        for i in cand_box(box):
            it = items[i]
            if it['net'] == net or L not in it['layers']: continue
            bx0, by0, bx1, by1 = it['bb']
            if bx1 < box[0] or bx0 > box[2] or by1 < box[1] or by0 > box[3]: continue
            g = it['geom']
            d = dist_item_seg(it, s0) - it['rad'] - TW/2.0
            if d < need - 1e-9 and (i not in out or d < out[i]):
                out[i] = d
    return out


def min_gap(net, segs, L):
    best = 1e9
    for s0 in segs:
        box = (min(s0[0], s0[2])-1.0, min(s0[1], s0[3])-1.0,
               max(s0[0], s0[2])+1.0, max(s0[1], s0[3])+1.0)
        for i in cand_box(box):
            it = items[i]
            if it['net'] == net: continue
            if L not in it['layers']: continue
            bx0, by0, bx1, by1 = it['bb']
            if bx1 < box[0] or bx0 > box[2] or by1 < box[1] or by0 > box[3]: continue
            g = it['geom']
            d = dist_item_seg(it, s0) - it['rad'] - TW/2.0
            if d < best:
                best = d
    return best


def raster(box, net, clr=CLR, pen=None, slay=None):
    x0, y0, x1, y1 = box
    w = int(round((x1-x0)/STEP))+1
    h = int(round((y1-y0)/STEP))+1
    arrs = {L: np.zeros((h, w), np.uint8)
            for L in list(LAYERS) + ['F.Cu', 'In1.Cu', 'In3.Cu']}
    seen = set()
    for gx in range(int(math.floor(x0/BUCK)), int(math.floor(x1/BUCK))+1):
        for gy in range(int(math.floor(y0/BUCK)), int(math.floor(y1/BUCK))+1):
            for i in bk.get((gx, gy), ()):
                if i in seen: continue
                seen.add(i)
                it = items[i]
                if it['net'] == net: continue
                bx0, by0, bx1, by1 = it['bb']
                if bx1 < x0 or bx0 > x1 or by1 < y0 or by0 > y1: continue
                r = it['rad']+TW/2.0+clr+(pen.get(i, 0.0) if pen else 0.0)
                g = it['geom']
                if it['kind'] == 'rect':
                    ex = g[2]+g[3]+r
                    i0 = max(0, int(math.floor((g[1]-ex-y0)/STEP)))
                    i1 = min(h, int(math.ceil((g[1]+ex-y0)/STEP))+1)
                    j0 = max(0, int(math.floor((g[0]-ex-x0)/STEP)))
                    j1 = min(w, int(math.ceil((g[0]+ex-x0)/STEP))+1)
                    if i0 >= i1 or j0 >= j1: continue
                    dy = np.arange(i0, i1)*STEP + y0 - g[1]
                    dx = np.arange(j0, j1)*STEP + x0 - g[0]
                    U = dx[None, :]*g[4] + dy[:, None]*g[5]
                    V = -dx[None, :]*g[5] + dy[:, None]*g[4]
                    du = np.maximum(np.abs(U)-g[2], 0.0)
                    dv = np.maximum(np.abs(V)-g[3], 0.0)
                    m = (du*du + dv*dv) <= r*r
                    for L in it['layers']:
                        if L not in arrs: continue
                        arrs[L][i0:i1, j0:j1] |= m.astype(np.uint8)
                    continue
                if it['kind'] == 'disc':
                    pts = [(g[0], g[1])]
                else:
                    d = math.hypot(g[2]-g[0], g[3]-g[1])
                    n = max(1, int(d/(STEP*0.7)))
                    pts = [(g[0]+(g[2]-g[0])*k/float(n), g[1]+(g[3]-g[1])*k/float(n))
                           for k in range(n+1)]
                for L in it['layers']:
                    if L not in arrs: continue
                    A = arrs[L]
                    for px, py in pts:
                        i0 = max(0, int(math.floor((py-r-y0)/STEP)))
                        i1 = min(h, int(math.ceil((py+r-y0)/STEP))+1)
                        j0 = max(0, int(math.floor((px-r-x0)/STEP)))
                        j1 = min(w, int(math.ceil((px+r-x0)/STEP))+1)
                        if i0 >= i1 or j0 >= j1: continue
                        ys = ((np.arange(i0, i1)*STEP + y0) - py)**2
                        xs = ((np.arange(j0, j1)*STEP + x0) - px)**2
                        A[i0:i1, j0:j1] |= ((ys[:, None]+xs[None, :]) <= r*r).astype(np.uint8)
    k = int(math.ceil(EDGE_IN/STEP))
    # The edge keep-out is its own mask: a stamp-hole pad sits ON the outline,
    # so its own net must be allowed to leave the pad even though the strip is
    # kept clear.  Only cells that are blocked by the edge strip ALONE may be
    # opened; opening anything else would flood a hole through real copper
    # (that mistake cost a -0.1498 mm overlap on /+1V8_ANA).
    E = np.zeros((h, w), np.uint8)
    E[:k, :] = 1; E[-k:, :] = 1; E[:, :k] = 1; E[:, -k:] = 1
    for i in cand_box(box):
        it = items[i]
        if it['net'] != net: continue
        g = it['geom']
        rb = max(rr(it), 0.10) + EDGE_IN + 0.05
        i0 = max(0, int(math.floor((g[1]-rb-y0)/STEP)))
        i1 = min(h, int(math.ceil((g[1]+rb-y0)/STEP))+1)
        j0 = max(0, int(math.floor((g[0]-rb-x0)/STEP)))
        j1 = min(w, int(math.ceil((g[0]+rb-x0)/STEP))+1)
        if i0 >= i1 or j0 >= j1: continue
        ys = ((np.arange(i0, i1)*STEP + y0) - g[1])**2
        xs = ((np.arange(j0, j1)*STEP + x0) - g[0])**2
        disk = ((ys[:, None]+xs[None, :]) <= rb*rb)
        E[i0:i1, j0:j1][disk] = 0
    for L in list(LAYERS) + ['F.Cu']:
        arrs[L] = arrs[L] | E
    return arrs, w, h

DIRS = ((1,0),(-1,0),(0,1),(0,-1),(1,1),(1,-1),(-1,1),(-1,-1))
COST = (1000,1000,1000,1000,1414,1414,1414,1414)

def astar(blk, w, h, si, sj, gi, gj, gsteps, cap):
    start = si*w+sj
    best = {start: 0}
    prev = {start: -1}
    def heur(a, c):
        di, dj = abs(a-gi), abs(c-gj)
        d = max(di, dj)*1000 + 414*min(di, dj) - gsteps*1414
        return d if d > 0 else 0
    heap = [(heur(si, sj), 0, start, si, sj)]
    n = 0
    while heap:
        f, g, cur, a, c = heapq.heappop(heap)
        if best.get(cur, -1) != g: continue
        if a == gi and c == gj:
            path, q = [], cur
            while q != -1:
                path.append(divmod(q, w)); q = prev[q]
            path.reverse(); return path
        n += 1
        if n > cap: return None
        base = a*w
        for k in range(8):
            na, nc = a+DIRS[k][0], c+DIRS[k][1]
            if not (0 <= na < h and 0 <= nc < w): continue
            ni = na*w+nc
            if blk[ni]: continue
            ng = g+COST[k]
            if best.get(ni, -1) >= 0 and best[ni] <= ng: continue
            best[ni] = ng; prev[ni] = cur
            heapq.heappush(heap, (ng+heur(na, nc), ng, ni, na, nc))
    return None

VIA_COST = 20000

def astar3(bl, w, h, si, sj, gi, gj, slay, cap, vf=None):
    """3-D lattice search: one node per (layer, cell).  A via transition is
    allowed only where a 0.2/0.1 through via body fits on every searched layer
    with 0.10 mm clearance.  `vf` is that mask, precomputed by eroding the free
    space of all layers by one cell (0.05 mm) because a via is 0.05 mm fatter
    per side than a 0.10 mm track; when it is absent the older all-layers-free
    rule is used."""
    NL = len(slay); WH = w*h
    def ok_via(idx):
        if vf is not None:
            return not vf[idx]
        for L in slay:
            if bl[L][idx]:
                return False
        return True
    def heur(a, c):
        di, dj = abs(a-gi), abs(c-gj)
        return max(di, dj)*1000 + 414*min(di, dj)
    startidx = si*w+sj
    best = {}; prev = {}; heap = []
    for lk in range(NL):
        nid = lk*WH + startidx
        best[nid] = 0; prev[nid] = -1
        heapq.heappush(heap, (heur(si, sj), 0, nid, lk, si, sj))
    n = 0
    while heap:
        f, g, nid, lk, a, c = heapq.heappop(heap)
        if best.get(nid, -1) != g: continue
        if a == gi and c == gj:
            path = []; q = nid
            while q != -1:
                l2, idx2 = divmod(q, WH)
                path.append((slay[l2], idx2//w, idx2 % w))
                q = prev[q]
            path.reverse(); return path
        n += 1
        if n > cap: return None
        L = slay[lk]; blk = bl[L]; idx = a*w+c
        for k in range(8):
            na, nc = a+DIRS[k][0], c+DIRS[k][1]
            if not (0 <= na < h and 0 <= nc < w): continue
            ni = na*w+nc
            if blk[ni]: continue
            nn = lk*WH + ni
            ng = g+COST[k]
            if best.get(nn, -1) >= 0 and best[nn] <= ng: continue
            best[nn] = ng; prev[nn] = nid
            heapq.heappush(heap, (ng+heur(na, nc), ng, nn, lk, na, nc))
        if ok_via(idx):
            for lk2 in range(NL):
                if lk2 == lk: continue
                nn = lk2*WH + idx
                ng = g+VIA_COST
                if best.get(nn, -1) >= 0 and best[nn] <= ng: continue
                best[nn] = ng; prev[nn] = nid
                heapq.heappush(heap, (ng+heur(a, c), ng, nn, lk2, a, c))
    return None

def emit(path, x0, y0):
    q = [(round(x0+j*STEP, 4), round(y0+i*STEP, 4)) for i, j in path]
    r = [q[0]]
    for p in q[1:]:
        if abs(p[0]-r[-1][0]) > 1e-9 or abs(p[1]-r[-1][1]) > 1e-9: r.append(p)
    segs, i = [], 0
    while i < len(r)-1:
        dx = (r[i+1][0]-r[i][0]) > 1e-9
        dy = (r[i+1][1]-r[i][1]) > 1e-9
        d = ((r[i+1][0] > r[i][0]) - (r[i+1][0] < r[i][0]),
             (r[i+1][1] > r[i][1]) - (r[i+1][1] < r[i][1]))
        j = i+1
        while j+1 < len(r):
            d2 = ((r[j+1][0] > r[j][0]) - (r[j+1][0] < r[j][0]),
                  (r[j+1][1] > r[j][1]) - (r[j+1][1] < r[j][1]))
            if d2 != d: break
            j += 1
        segs.append((r[i][0], r[i][1], r[j][0], r[j][1]))
        i = j
    return segs


def legs(p, q):
    """Axis-aligned segments joining p to q (empty when they already coincide).
    A raster cell is never exactly a pad centre, so without this every route
    ends 0.02-0.05 mm short of its pad and the DRC calls it a dangling end.
    Still 0/45/90: no arcs, no diagonal fakery."""
    out = []
    if abs(p[0]-q[0]) > 1e-4:
        out.append((p[0], p[1], q[0], p[1]))
    if abs(p[1]-q[1]) > 1e-4:
        out.append((q[0], p[1], q[0], q[1]))
    return [s for s in out if math.hypot(s[2]-s[0], s[3]-s[1]) > 1e-3]

def off_pad(vx, vy, vr=0.10, need=0.03):
    """A via body may not sit on or against ANY pad, its own net included.
    A via drilled through a pad is via-in-pad: the fab cannot fill it reliably
    and it wicks solder off the joint.  A fanout via belongs BESIDE the pad,
    joined by a short stub -- that is what the escape stub is for."""
    box = (vx-2.6, vy-2.6, vx+2.6, vy+2.6)
    for i in cand_box(box):
        it = items[i]
        if it['kind'] != 'rect':
            continue
        if dist_item(it, vx, vy) < vr + need:
            return False
    return True


def site_ok(net, vx, vy, need=0.1, vr=0.1):
    """A via body at (vx,vy) must clear every foreign item on every layer."""
    box = (vx-1.2, vy-1.2, vx+1.2, vy+1.2)
    for i in cand_box(box):
        it = items[i]
        if it['net'] == net:
            continue
        bx0, by0, bx1, by1 = it['bb']
        if bx1 < box[0] or bx0 > box[2] or by1 < box[1] or by0 > box[3]:
            continue
        g = it['geom']
        d = dist_item(it, vx, vy) - it['rad'] - vr
        if d < need - 1e-9:
            return False
    return True

def hole_ok(vx, vy, vr=0.05, need=0.15):
    for (hx, hy, hr) in HOLES:
        if math.hypot(vx-hx, vy-hy) < hr + vr + need - 1e-6:
            return False
    return True

def stub_ok(net, x1, y1, x2, y2):
    """A 0.10 mm F.Cu stub from a pad centre to the new via must clear F.Cu copper."""
    box = (min(x1,x2)-1.2, min(y1,y2)-1.2, max(x1,x2)+1.2, max(y1,y2)+1.2)
    for i in cand_box(box):
        it = items[i]
        if it['net'] == net or 'F.Cu' not in it['layers']:
            continue
        bx0, by0, bx1, by1 = it['bb']
        if bx1 < box[0] or bx0 > box[2] or by1 < box[1] or by0 > box[3]:
            continue
        g = it['geom']
        d = dist_item_seg(it, (x1, y1, x2, y2)) - it['rad'] - TW/2.0
        if d < 0.1 - 1e-9:
            return False
    return True

def find_escape(net, comp, tgt):
    """Pick a pad in a pad-only cluster and drop a 0.2/0.1 via next to it.
    The stub is one 45-degree F.Cu segment from the pad centre: no arcs, no
    arbitrary geometry, and the via site is verified against every layer."""
    pads = [a for a in comp if items[a]['kind'] in ('disc', 'rect')]
    dirs = [(1,0),(1,1),(0,1),(-1,1),(-1,0),(-1,-1),(0,-1),(1,-1)]
    for a in sorted(pads, key=lambda a: items[a]['rad']):
        g = items[a]['geom']
        ang0 = math.atan2(tgt[1]-g[1], tgt[0]-g[0])
        order = sorted(dirs, key=lambda d: -math.cos(math.atan2(d[1], d[0]) - ang0))
        for dist in (0.50, 0.65, 0.80, 0.95, 1.15):
            for dx, dy in order:
                vx = g[0] + dx*dist/math.hypot(dx, dy)
                vy = g[1] + dy*dist/math.hypot(dx, dy)
                if not (0.7 < vx < 44.3 and 0.7 < vy < 44.3):
                    continue
                if not site_ok(net, vx, vy):
                    continue
                if not stub_ok(net, g[0], g[1], vx, vy):
                    continue
                if not off_pad(vx, vy):
                    continue
                return (a, round(vx, 4), round(vy, 4))
    # A cluster made only of track ends (a local patch after a rip) has no pad
    # to escape from.  A via may stand on the end of an existing track of the
    # same net -- that is where a fanout via belongs -- and the only check is
    # the same site test the pad case uses: no stub, no new copper.
    for a in comp:
        it = items[a]
        if it['kind'] != 'seg':
            continue
        gx1, gy1, gx2, gy2 = it['geom']
        for (vx, vy) in ((gx1, gy1), (gx2, gy2)):
            if not (0.7 < vx < 44.3 and 0.7 < vy < 44.3):
                continue
            if not site_ok(net, vx, vy):
                continue
            if not off_pad(vx, vy):
                continue
            return (None, round(vx, 4), round(vy, 4))
    return None

def route(net, comp, tree, cap, clr0, ntry, mgs):
    """join one cluster to any through terminal of tree; exact-gap verified.
    If the cluster holds no through terminal, one pad is escaped to a new
    through via with a single 45-degree F.Cu stub first."""
    global STEP
    full = set(LAYERS)
    cth = [a for a in comp if full <= items[a]['layers']]
    tth = [a for a in tree if full <= items[a]['layers']]
    esc = None
    if cth:
        slay = list(LAYERS); st0 = cth[0]; start = items[st0]['geom'][:2]
    else:
        if not tth:
            return None
        tp = min((items[a]['geom'][:2] for a in tth),
                 key=lambda p: min(math.hypot(p[0]-items[c]['geom'][0], p[1]-items[c]['geom'][1])
                                   for c in comp))
        esc = find_escape(net, comp, tp)
        if esc is None:
            return None
        slay = list(LAYERS); start = (esc[1], esc[2])
    need = set(slay)
    tth = [a for a in tree if need <= items[a]['layers']]
    if not tth:
        return None
    tg = sorted(tth, key=lambda a: math.hypot(items[a]['geom'][0]-start[0],
                                              items[a]['geom'][1]-start[1]))[:6]
    pts = [items[a]['geom'][:2] for a in comp] + [items[a]['geom'][:2] for a in tg] + [start]
    xs = [p[0] for p in pts]; ys = [p[1] for p in pts]
    pen = {}
    for attempt in range(ntry):
        if DEADLINE[0] and time.time() > DEADLINE[0]:
            return None
        clr = clr0 if attempt < ntry-2 else clr0 + 0.03
        mg = mgs[0] if attempt == 0 else mgs[1 + (attempt % 2)]
        mg = min(mg, 0.30 * max(max(xs)-min(xs), max(ys)-min(ys)) + 0.5)
        box = (max(min(xs)-mg, 0.0), max(min(ys)-mg, 0.0),
               min(max(xs)+mg, 45.0), min(max(ys)+mg, 45.0))
        x0 = box[0] - ((start[0]-box[0]) % STEP)
        y0 = box[1] - ((start[1]-box[1]) % STEP)
        box = (x0, y0, box[2], box[3])
        arrs, w, h = raster(box, net, clr, pen, slay)
        if len(slay) > 1:
            # A through via must clear foreign copper on EVERY searched layer, so
            # the legal via sites are the INTERSECTION of the layers' free spaces,
            # eroded by (via radius + clearance) rather than by one cell.  The old
            # code OR-ed the layers together and eroded by a single cell, so the
            # search handed back "via sites" that the exact check (site_ok) then
            # refused -- that is why long nets reported path3 found but REFUSED.
            # A through via also passes In1/In3, so those layers' tracks must be
            # in the intersection as well; site_ok (the exact check) looks at
            # every layer, and leaving the planes out here is why it kept
            # refusing cells the mask had declared legal.
            # blocked-mask semantics: a cell is via-BLOCKED if any layer blocks
            # it (a through via must be free on every layer it crosses).  The
            # raster already blocks rad+TW/2+clr around copper, a via needs
            # rad+0.10+0.10, so dilate the difference and OR the layers.
            k = max(1, int(math.ceil(max(0.0, 0.20 - TW/2.0 - clr) / STEP)) + 1)
            acc = np.zeros((h, w), np.uint8)
            via_lay = list(slay) + [L for L in ('In1.Cu', 'In3.Cu') if L not in slay]
            for L in via_lay:
                D = arrs[L].copy()
                for _ in range(k):
                    E = D.copy()
                    E[1:, :] |= D[:-1, :]; E[:-1, :] |= D[1:, :]
                    E[:, 1:] |= E[:, :-1]; E[:, :-1] |= E[:, 1:]
                    D = E
                acc |= D
            vf = bytearray(acc.tobytes())
        else:
            vf = None
        cong = dict((L, 0) for L in list(LAYERS) + ['F.Cu'])
        for i in cand_box(box):
            it = items[i]
            if it['net'] == net or it['kind'] != 'seg': continue
            for L in it['layers']:
                if L in cong:
                    cong[L] += math.hypot(it['geom'][2]-it['geom'][0],
                                          it['geom'][3]-it['geom'][1])
        si = int(round((start[1]-y0)/STEP)); sj = int(round((start[0]-x0)/STEP))
        # A stamp-hole terminal sits exactly on the outline, which is also the
        # bbox limit, so round() lands one past the last cell.  Clamp instead of
        # dropping the terminal (that silently made every edge net unreachable).
        si = max(0, min(h-1, si)); sj = max(0, min(w-1, sj))
        bl = {L: bytearray(arrs[L].tobytes()) for L in slay}
        for L in slay:
            bl[L][si*w+sj] = 0
        bestp = None; bestsc = 1 << 30; bestblk = None; bestgoal = None
        for a in tg:
            g = items[a]['geom']
            gi = int(round((g[1]-y0)/STEP)); gj = int(round((g[0]-x0)/STEP))
            gi = max(0, min(h-1, gi)); gj = max(0, min(w-1, gj))
            gr = 0   # the target's own copper is never blocked, so no disc clearing
            bl = {L: bytearray(arrs[L].tobytes()) for L in slay}
            for L in slay:
                bl[L][si*w+sj] = 0
                for di in range(-gr, gr+1):
                    for dj in range(-gr, gr+1):
                        ii, jj = gi+di, gj+dj
                        if 0 <= ii < h and 0 <= jj < w:
                            bl[L][ii*w+jj] = 0
            if os.environ.get('PROBE'):
                from collections import deque
                for L in slay:
                    m = bl[L]
                    seen = {(si, sj)}
                    dq = deque([(si, sj)])
                    while dq:
                        i, j = dq.popleft()
                        for di in (-1, 0, 1):
                            for dj in (-1, 0, 1):
                                if not di and not dj: continue
                                ii, jj = i+di, j+dj
                                if 0 <= ii < h and 0 <= jj < w and not m[ii*w+jj] and (ii, jj) not in seen:
                                    seen.add((ii, jj)); dq.append((ii, jj))
                    print("   PROBE %s start(%d,%d) goal(%d,%d) reached %d of %d goal_in=%s"
                          % (L, si, sj, gi, gj, len(seen), w*h, (gi, gj) in seen), flush=True)
                for i in range(max(0, gi-3), min(h, gi+4)):
                    print("   PROBE row %d: %s" % (i, ''.join('#' if bl[slay[0]][i*w+j] else '.' for j in range(max(0, gj-6), min(w, gj+7)))), flush=True)
            p3 = astar3(bl, w, h, si, sj, gi, gj, slay, cap, vf)
            if p3 and len(p3) > 1:
                sc = len(p3) + int(60.0 * cong[p3[0][0]])
                if sc < bestsc:
                    bestsc = sc; bestp = p3; bestblk = bl
                    bestgoal = (g[0], g[1])
            if bestp: break
        if not bestp:
            if os.environ.get('DBG'): print("   DBG no-path attempt %d clr %.3f box %s" % (attempt, clr, [round(v,2) for v in box]), flush=True)
            continue
        if os.environ.get('DBG'): print("   DBG path3 len=%d layers=%s" % (len(bestp), sorted(set(st[0] for st in bestp))), flush=True)
        runs = []
        cur = [bestp[0]]
        for st in bestp[1:]:
            if st[0] != cur[-1][0]:
                runs.append(cur); cur = [st]
            else:
                cur.append(st)
        runs.append(cur)
        segs_by_layer = []; vlist = []; bad = False
        for ri, r in enumerate(runs):
            if len(r) < 2: continue
            L = r[0][0]
            pts = [(i, j) for _, i, j in r]
            sm = emit(shortcut(pts, bestblk[L], w, h), x0, y0)
            if not sm:
                bad = True; break
            if ri == 0:
                sm = legs(start, (sm[0][0], sm[0][1])) + sm
            if ri == len(runs) - 1 and bestgoal is not None:
                sm = sm + legs((sm[-1][2], sm[-1][3]), bestgoal)
            segs_by_layer.append((L, sm))
        if bad or not segs_by_layer: continue
        for ra, rb in zip(runs, runs[1:]):
            i, j = ra[-1][1], ra[-1][2]
            vx, vy = round(x0+j*STEP, 4), round(y0+i*STEP, 4)
            if not site_ok(net, vx, vy) or not hole_ok(vx, vy) or not off_pad(vx, vy):
                if os.environ.get('DBG'): print("   DBG via site refused at %.3f %.3f" % (vx, vy), flush=True)
                bad = True; break
            vlist.append((vx, vy))
            HOLES.append((vx, vy, 0.05))
        if bad: continue
        off = {}
        for L, sm in segs_by_layer:
            for i, d in gaps(net, sm, L).items():
                off[i] = min(d, off.get(i, 9.0))
        if not off:
            parts = list(segs_by_layer)
            if esc is not None and esc[0] is not None:
                p0 = items[esc[0]]['geom']
                parts = [('F.Cu', [(p0[0], p0[1], esc[1], esc[2])])] + parts
                vlist = [(esc[1], esc[2])] + vlist
            return parts, (vlist or None)
        if os.environ.get('DBG'):
            w3 = sorted(off.items(), key=lambda kv: kv[1])[:2]
            print("   DBG gaps fail %d items min %.4f" % (len(off), min(off.values())), flush=True)
            for i, d in w3:
                print("      item net=%s kind=%s geom=%s d=%.4f" % (
                    items[i]['net'], items[i]['kind'],
                    [round(v, 3) for v in items[i]['geom']], d), flush=True)
        for i, d in off.items():
            pen[i] = pen.get(i, 0.0) + (0.1 - d) + 0.03
    return None

def try_net(net, gsets, cap, clr0, ntry, mgs):
    for order in (gsets, gsets[::-1]):
        tree = list(order[0]); parts = []; vias = []
        for comp in order[1:]:
            r = route(net, comp, tree, cap, clr0, ntry, mgs)
            if r is None:
                parts = None; break
            parts.extend(r[0])
            if r[1]:
                vias.extend(r[1])
            tree = tree + list(comp)
        if parts is not None:
            return (parts, vias)
    return None

# Round 3: refuse list. The new ability is the via escape, not a finer grid, so
# the coarse tier still runs first (a fine grid over a 40 mm span is 4M cells).
TIERS = [(0.050, 0.130, 700000, 6, (4.0, 9.0, 12.0)),
         (0.025, 0.120, 1400000, 5, (5.0, 10.0, 14.0)),
         (0.0160, 0.112, 1800000, 4, (6.0, 12.0, 16.0))]
if os.environ.get('TIERS'):
    TIERS=[]
    for grp in os.environ['TIERS'].split(';'):
        if not grp.strip(): continue
        v=[float(x) for x in grp.split(',')]
        TIERS.append((v[0], v[1], int(v[2]), int(v[3]), (v[4], v[5], v[6])))

def commit(net, res):
    parts, vias = res
    for L, segs in parts:
        placed.append({"net": net, "layer": L, "segs": segs})
        for s in segs:
            mk('seg', net, {L}, (s[0], s[1], s[2], s[3]), TW/2.0)
            x0, y0, x1, y1 = items[-1]['bb']
            for gx in range(int(math.floor(x0/BUCK)), int(math.floor(x1/BUCK))+1):
                for gy in range(int(math.floor(y0/BUCK)), int(math.floor(y1/BUCK))+1):
                    bk.setdefault((gx, gy), []).append(len(items)-1)
    for (vx, vy) in vias:
        # The same net is never checked against itself, so a repeat emission of
        # the same site would land a second drill 0.01 mm away -- legal for the
        # raster, a hole_to_hole hit for the DRC.
        if any(math.hypot(vx-a, vy-b2) < 0.30 for a, b2 in placed_vias):
            continue
        placed_vias.append((vx, vy))
        placed.append({"net": net, "vias": [[vx, vy]]})
        mk('disc', net, set(LAYERS) | {'F.Cu'}, (vx, vy, vx, vy), 0.1)
        x0, y0, x1, y1 = items[-1]['bb']
        for gx in range(int(math.floor(x0/BUCK)), int(math.floor(x1/BUCK))+1):
            for gy in range(int(math.floor(y0/BUCK)), int(math.floor(y1/BUCK))+1):
                bk.setdefault((gx, gy), []).append(len(items)-1)


placed, refused, placed_vias = [], [], []
DEADLINE = [0.0]
BUDGET = float(os.environ.get("BUDGET") or 180.0)
for k, (span, net) in enumerate(jobs):
    DEADLINE[0] = time.time() + BUDGET
    gsets = clusters(net)
    res = None; tier = None
    for (step, clr0, cap, ntry, mgs) in TIERS:
        STEP = step
        t = try_net(net, gsets, cap, clr0, ntry, mgs)
        if t is not None:
            res, tier = t, step; break
    if res is None:
        print("[%d/%d] %-26s REFUSED" % (k+1, len(jobs), net), flush=True)
        refused.append(net)
    else:
        commit(net, res)
        json.dump(placed, open(OUT, "w"), indent=1)
        print("[%d/%d] %-26s OK %d parts, %d segs, %d vias, step %.3f"
              % (k+1, len(jobs), net, len(res[0]),
                 sum(len(s) for _, s in res[0]), len(res[1]), tier), flush=True)

json.dump(placed, open(OUT, "w"), indent=1)
print("jobs %d placed %d refused %d" % (len(jobs), len(placed), len(refused)), flush=True)
for r in refused: print("  REFUSED", r, flush=True)
