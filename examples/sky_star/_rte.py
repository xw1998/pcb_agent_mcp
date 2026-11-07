"""Manual routing engine for sky_star.  Geometry rules are mine; no auto-router.

Rules
-----
* 0.2 mm signal, 0.45/0.25 via, clearance 0.15 (project default 0.15).
* LQFP100 left/right columns escape horizontally, top/bottom vertically.
* Only monotone lane order is used, so parallel lanes never cross.
* Corners are chamfered to 45 deg by construction.
"""
import json, math

W = 0.2
N = json.load(open('_scratch/sky/_nets.json'))
ORIGIN = json.load(open('_scratch/sky/_origin.json'))

PAD = {}          # (ref, num) -> pad
BY_NET = {}
for net, ps in N.items():
    BY_NET[net] = ps
    for p in ps:
        PAD[(p['ref'], p['num'])] = p


def f(v):
    s = '%.4f' % v
    s = s.rstrip('0').rstrip('.')
    return '0' if s in ('-0', '') else s


def chamfer(pts, d=0.35):
    """Replace each interior 90 deg vertex with a 45 deg bevel."""
    if len(pts) < 3:
        return pts
    out = [pts[0]]
    for i in range(1, len(pts) - 1):
        a, v, b = pts[i - 1], pts[i], pts[i + 1]
        l1 = math.dist(a, v)
        l2 = math.dist(v, b)
        c = min(d, l1 / 2, l2 / 2)
        if c < 1e-6:
            out.append(v)
            continue
        u1 = ((v[0] - a[0]) / l1, (v[1] - a[1]) / l1)
        u2 = ((b[0] - v[0]) / l2, (b[1] - v[1]) / l2)
        p1 = (v[0] - u1[0] * c, v[1] - u1[1] * c)
        p2 = (v[0] + u2[0] * c, v[1] + u2[1] * c)
        out.append(p1)
        out.append(p2)
    out.append(pts[-1])
    # drop duplicates
    ded = [out[0]]
    for p in out[1:]:
        if math.dist(p, ded[-1]) > 1e-6:
            ded.append(p)
    return ded


class Route:
    def __init__(self):
        self.segs = []   # (net, layer, w, (x1,y1), (x2,y2))
        self.vias = []   # (net, x, y, drill, size)

    def poly(self, net, layer, pts, w=W, bevel=0.35):
        pts = chamfer(pts, bevel)
        for a, b in zip(pts, pts[1:]):
            if math.dist(a, b) < 1e-6:
                continue
            self.segs.append((net, layer, w, a, b))

    def via(self, net, x, y, drill=0.25, size=0.45):
        self.vias.append((net, x, y, drill, size))


# ---------------------------------------------------------------- P1 / P2
def pads_of(ref, net=None):
    out = []
    for n, ps in N.items():
        if net and n != net:
            continue
        for p in ps:
            if p['ref'] == ref:
                out.append(dict(p, net=n))
    return out


def other_end(net, ref):
    ps = BY_NET[net]
    if len(ps) != 2:
        return None
    a, b = ps
    return b if a['ref'] == ref else a


def mcu_side(p):
    """Which side of U1 a pad sits on."""
    if abs(p['x'] + 7.5499) < 0.01:
        return 'L'
    if abs(p['x'] - 7.5499) < 0.01:
        return 'R'
    if abs(p['y'] - 7.5499) < 0.01:
        return 'T'
    if abs(p['y'] + 7.5499) < 0.01:
        return 'B'
    return '?'


def left_group():
    """(header pad, mcu pad, outer?) sorted by header y desc."""
    rows = []
    for p in pads_of('P1'):
        o = other_end(p['net'], 'P1')
        if not o or o['ref'] != 'U1':
            continue
        side = mcu_side(o)
        rows.append((p, o, p['x'] < -18.0, side))
    rows.sort(key=lambda r: -r[0]['y'])
    return rows


def right_group():
    rows = []
    for p in pads_of('P2'):
        o = other_end(p['net'], 'P2')
        if not o or o['ref'] != 'U1':
            continue
        side = mcu_side(o)
        rows.append((p, o, p['x'] > 18.0, side))
    rows.sort(key=lambda r: -r[0]['y'])
    return rows


if __name__ == '__main__':
    for name, g in (('P1', left_group()), ('P2', right_group())):
        print('==', name, len(g))
        for p, o, outer, side in g:
            print('  %-4s y=%7.2f %s  <- U1.%-3s %s (%6.2f,%6.2f) %s'
                  % (p['num'], p['y'], 'OUT' if outer else 'in ', o['num'], side,
                     o['x'], o['y'], p['net']))
