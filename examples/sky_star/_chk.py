"""Geometry checker for hand-drawn routes: pad clearance, track/via spacing.

No router.  It only answers "does this polyline break a rule".
"""
import json, math

CLR = 0.15
EDGE = 0.5         # copper (not centreline) to board edge, DRC rule 0.5
BX, BY = 22.0, 36.0

N = json.load(open('_scratch/sky/_nets.json'))

def _rot(p, a):
    c, s = math.cos(math.radians(a)), math.sin(math.radians(a))
    return (p[0] * c - p[1] * s, p[0] * s + p[1] * c)

def _seg_seg(a, b, c, d):
    def cross(o, p, q):
        return (p[0] - o[0]) * (q[1] - o[1]) - (p[1] - o[1]) * (q[0] - o[0])
    def inter(o, p, q, r):
        d1 = cross(o, p, q); d2 = cross(o, p, r)
        d3 = cross(q, r, o); d4 = cross(q, r, p)
        return ((d1 > 0) != (d2 > 0)) and ((d3 > 0) != (d4 > 0))
    def pseg(p, a, b):
        vx, vy = b[0] - a[0], b[1] - a[1]
        L = vx * vx + vy * vy
        t = 0.0 if L < 1e-12 else max(0.0, min(1.0, ((p[0] - a[0]) * vx + (p[1] - a[1]) * vy) / L))
        return math.hypot(p[0] - a[0] - t * vx, p[1] - a[1] - t * vy)
    if inter(a, b, c, d):
        return 0.0
    return min(pseg(a, c, d), pseg(b, c, d), pseg(c, a, b), pseg(d, a, b))

class Pad:
    __slots__ = ('x', 'y', 'w', 'h', 'a', 'layer', 'net', 'ref', 'num')
    def __init__(self, d, net):
        self.x, self.y = d['x'], d['y']
        # pads that the netlist carries as a zero-size point (the LIB part has
        # no copper) are 0.6 x 0.6 once _sky_emit2 back-fills them; checking
        # against a point would let a track sit inside real copper.
        self.w = d['w'] if d['w'] >= 0.05 else 0.6
        self.h = d['h'] if d['h'] >= 0.05 else 0.6
        self.a = d['a']
        self.layer = 'F' if d['layer'].startswith('F') else 'B'
        self.net, self.ref, self.num = net, d['ref'], d['num']

    def dist(self, p):
        """signed-ish distance from point p to pad copper (0 if inside)."""
        q = _rot((p[0] - self.x, p[1] - self.y), -self.a)
        dx = max(abs(q[0]) - self.w / 2, 0.0)
        dy = max(abs(q[1]) - self.h / 2, 0.0)
        return math.hypot(dx, dy)

    def dist_seg(self, a, b):
        ra = _rot((a[0] - self.x, a[1] - self.y), -self.a)
        rb = _rot((b[0] - self.x, b[1] - self.y), -self.a)
        hw, hh = self.w / 2, self.h / 2
        corners = [(-hw, -hh), (hw, -hh), (hw, hh), (-hw, hh)]
        def inside(p):
            return abs(p[0]) <= hw and abs(p[1]) <= hh
        if inside(ra) or inside(rb):
            return 0.0
        best = 1e9
        for i in range(4):
            c, d = corners[i], corners[(i + 1) % 4]
            best = min(best, _seg_seg(ra, rb, c, d))
        return best

PADS = []
for net, ps in N.items():
    for p in ps:
        PADS.append(Pad(p, net))

def check(segs, vias, verbose=True):
    """segs: (net, layer, w, (x1,y1),(x2,y2)); vias: (net,x,y,drill,size)."""
    bad = []
    for i, (net, layer, w, a, b) in enumerate(segs):
        for p in PADS:
            if p.net == net:
                continue
            if not layer.startswith(p.layer):
                continue
            d = p.dist_seg(a, b)
            need = CLR + w / 2
            if d < need - 1e-6:
                bad.append('pad %s.%s %s: d=%.3f<%.3f at %s-%s' %
                           (p.ref, p.num, net, d, need, a, b))
        for x, y in (a, b):
            if BX - abs(x) < EDGE + w / 2 or BY - abs(y) < EDGE + w / 2:
                bad.append('edge %s at %s (copper %.3f from edge)'
                           % (net, (x, y), min(BX - abs(x), BY - abs(y)) - w / 2))
    for i, (n1, x1, y1, d1, s1) in enumerate(vias):
        if min(BX - abs(x1), BY - abs(y1)) < EDGE + s1 / 2:
            bad.append('edge via %s at %s' % (n1, (x1, y1)))
        for p in PADS:
            if p.net == n1:
                continue
            d = p.dist((x1, y1)) - s1 / 2
            if d < CLR - 1e-6:
                bad.append('via-pad %s vs %s.%s d=%.3f' % (n1, p.ref, p.num, d))
        for j in range(i + 1, len(vias)):
            n2, x2, y2, _, s2 = vias[j]
            if n1 == n2:
                continue
            d = math.hypot(x1 - x2, y1 - y2) - (s1 + s2) / 2
            if d < CLR - 1e-6:
                bad.append('via-via %s/%s d=%.3f' % (n1, n2, d))
        for net, layer, w, a, b in segs:
            if net == n1:
                continue
            d = _seg_seg((x1, y1), (x1, y1), a, b) - s1 / 2 - w / 2
            if d < CLR - 1e-6:
                bad.append('via-track %s/%s d=%.3f' % (n1, net, d))
    # track vs track, same layer
    for i in range(len(segs)):
        n1, l1, w1, a1, b1 = segs[i]
        for j in range(i + 1, len(segs)):
            n2, l2, w2, a2, b2 = segs[j]
            if n1 == n2 or not l1.startswith(l2[0]):
                continue
            d = _seg_seg(a1, b1, a2, b2) - (w1 + w2) / 2
            if d < CLR - 1e-6:
                bad.append('track-track %s/%s d=%.3f %s %s' % (n1, n2, d, (a1, b1), (a2, b2)))
    if verbose:
        print('segs', len(segs), 'vias', len(vias), 'violations', len(bad))
        for b in bad[:40]:
            print('  ', b)
    return bad
