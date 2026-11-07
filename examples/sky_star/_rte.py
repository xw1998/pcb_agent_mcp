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


def chamfer(pts, d=1.20):
    """Replace each interior vertex with a 45 deg bevel (fixed, no cleverness).

    Kept for callers that want a one-shot polyline; Route.finalize() does the
    bit that matters, namely sizing every bevel so that it is as long as the
    neighbours allow and never breaks a spacing rule.
    """
    if len(pts) < 3:
        return pts
    out = [pts[0]]
    for i in range(1, len(pts) - 1):
        a, v, b = pts[i - 1], pts[i], pts[i + 1]
        l1 = math.dist(a, v)
        l2 = math.dist(v, b)
        c = min(d, l1 * 0.47, l2 * 0.47)
        if c < 1e-6:
            out.append(v)
            continue
        u1 = ((v[0] - a[0]) / l1, (v[1] - a[1]) / l1)
        u2 = ((b[0] - v[0]) / l2, (b[1] - v[1]) / l2)
        out.append((v[0] - u1[0] * c, v[1] - u1[1] * c))
        out.append((v[0] + u2[0] * c, v[1] + u2[1] * c))
    out.append(pts[-1])
    ded = [out[0]]
    for p in out[1:]:
        if math.dist(p, ded[-1]) > 1e-6:
            ded.append(p)
    return ded


CELL = 1.5          # spatial hash cell, mm


def _cells(x0, y0, x1, y1, pad=0.0):
    out = []
    for ix in range(int(math.floor((x0 - pad) / CELL)),
                    int(math.floor((x1 + pad) / CELL)) + 1):
        for iy in range(int(math.floor((y0 - pad) / CELL)),
                        int(math.floor((y1 + pad) / CELL)) + 1):
            out.append((ix, iy))
    return out


class Route:
    """Collects polylines, then sizes every 45 deg bevel against the real rules.

    poly() stores the raw polyline and a *cap*, not a done deal: after all
    groups are in, finalize() walks the table, and any bevel that would eat
    into a neighbour is shortened until the whole board is clean.  Corners
    therefore end up as long 45 deg mitres wherever there is room, and only
    get nipped back where the channel is genuinely narrow.
    """

    def __init__(self):
        self.paths = []     # (net, layer, w, pts, cap)
        self.vias = []
        self.segs = []      # filled by finalize()

    def poly(self, net, layer, pts, w=W, bevel=1.20):
        self.paths.append((net, layer, w, [(float(p[0]), float(p[1])) for p in pts],
                           bevel))

    def via(self, net, x, y, drill=0.25, size=0.45):
        self.vias.append((net, x, y, drill, size))

    # ------------------------------------------------------------------ emit
    def _emit(self, caps):
        segs, owner = [], []
        for k, (net, layer, w, pts, cap) in enumerate(self.paths):
            cl = caps[k]
            ch, own = [pts[0]], [None]
            for j in range(1, len(pts) - 1):
                a, v, b = pts[j - 1], pts[j], pts[j + 1]
                l1 = math.dist(a, v)
                l2 = math.dist(v, b)
                c = min(cap, cl[j - 1], l1 * 0.47, l2 * 0.47)
                me = {(k, j - 1)}
                if c < 1e-6:
                    ch.append(v)
                    own.append(me)
                    continue
                u1 = ((v[0] - a[0]) / l1, (v[1] - a[1]) / l1)
                u2 = ((b[0] - v[0]) / l2, (b[1] - v[1]) / l2)
                ch.append((v[0] - u1[0] * c, v[1] - u1[1] * c))
                own.append(me)
                ch.append((v[0] + u2[0] * c, v[1] + u2[1] * c))
                own.append(me)
            ch.append(pts[-1])
            own.append(None)
            prev, oa = ch[0], own[0]
            for p, ob in zip(ch[1:], own[1:]):
                if math.dist(prev, p) > 1e-9:
                    s = set()
                    if oa:
                        s |= oa
                    if ob:
                        s |= ob
                    segs.append((net, layer, w, prev, p))
                    owner.append(s)
                prev, oa = p, ob
        return segs, owner

    # -------------------------------------------------------------- offenders
    def _offenders(self, segs, owner, pads, vias, CLR):
        bad = set()
        pg = {}
        for p in pads:
            r = 0.5 * math.hypot(p.w, p.h) + 1.0
            for c in _cells(p.x - r, p.y - r, p.x + r, p.y + r):
                pg.setdefault((p.layer,) + c, []).append(p)
        sg = {}
        for si, (net, layer, w, a, b) in enumerate(segs):
            for c in _cells(min(a[0], b[0]), min(a[1], b[1]),
                            max(a[0], b[0]), max(a[1], b[1]), 1.0):
                sg.setdefault((layer,) + c, []).append(si)
        vg = {}
        for net, x, y, dr, sz in vias:
            for c in _cells(x, y, x, y, 1.0):
                vg.setdefault(c, []).append((net, x, y, dr, sz))

        for si, (net, layer, w, a, b) in enumerate(segs):
            need = CLR + w / 2
            seen = set()
            for c in _cells(min(a[0], b[0]), min(a[1], b[1]),
                            max(a[0], b[0]), max(a[1], b[1]), need):
                for p in pg.get((layer[0],) + c, ()):
                    if p.net == net or id(p) in seen:
                        continue
                    seen.add(id(p))
                    if p.dist_seg(a, b) < need - 1e-6:
                        bad |= owner[si]
                for sj in sg.get((layer,) + c, ()):
                    if sj <= si:
                        continue
                    n2, l2_, w2, a2, b2 = segs[sj]
                    if n2 == net:
                        continue
                    from _chk import _seg_seg
                    d = _seg_seg(a, b, a2, b2) - (w + w2) / 2
                    if d < CLR - 1e-6:
                        bad |= owner[si]
                        bad |= owner[sj]
        for net, x, y, dr, sz in vias:
            seen = set()
            for c in _cells(x, y, x, y, 1.0):
                for sj in sg.get(('F.Cu',) + c, ()):
                    n2, l2_, w2, a2, b2 = segs[sj]
                    if n2 == net or sj in seen:
                        continue
                    seen.add(sj)
                    if _seg_seg_p((x, y), a2, b2) - sz / 2 - w2 / 2 < CLR - 1e-6:
                        bad |= owner[sj]
                for sj in sg.get(('B.Cu',) + c, ()):
                    n2, l2_, w2, a2, b2 = segs[sj]
                    if n2 == net or sj in seen:
                        continue
                    seen.add(sj)
                    if _seg_seg_p((x, y), a2, b2) - sz / 2 - w2 / 2 < CLR - 1e-6:
                        bad |= owner[sj]
        return bad

    # --------------------------------------------------------------- finalize
    def finalize(self, verbose=True):
        import _chk
        pads, vias, CLR = _chk.PADS, self.vias, _chk.CLR
        caps = []
        for k, (net, layer, w, pts, cap) in enumerate(self.paths):
            row = []
            for j in range(1, len(pts) - 1):
                l1 = math.dist(pts[j - 1], pts[j])
                l2 = math.dist(pts[j], pts[j + 1])
                row.append(min(cap, l1 * 0.47, l2 * 0.47))
            caps.append(row)
        # A vertex that sits on its own pad or via is not a visible corner: the
        # pad covers the mitre.  Zeroing those keeps the "45 deg" statistic
        # honest, because it then counts only corners a human would look at.
        pads_of = {}
        for p in pads:
            pads_of.setdefault((p.net, p.layer), []).append(p)
        for k, (net, layer, w, pts, cap) in enumerate(self.paths):
            for j in range(1, len(pts) - 1):
                if caps[k][j - 1] < 1e-6:
                    continue
                v = pts[j]
                hit = any(pp.dist(v) < 0.05 for pp in pads_of.get((net, layer[0]), ()))
                if not hit:
                    hit = any(n2 == net and math.hypot(v[0] - x, v[1] - y) < sz / 2 + 0.05
                              for n2, x, y, _dr, sz in vias)
                if hit:
                    caps[k][j - 1] = 0.0
        it = 0
        for it in range(1, 200):
            segs, owner = self._emit(caps)
            bad = self._offenders(segs, owner, pads, vias, CLR)
            if not bad:
                break
            for k, j in bad:
                if caps[k][j] > 0.02:
                    caps[k][j] *= 0.70
        self.segs = segs
        # report
        lens = []
        for k, (net, layer, w, pts, cap) in enumerate(self.paths):
            for j, c in enumerate(caps[k]):
                if c > 1e-6:
                    lens.append(c * math.sqrt(2.0))
        if verbose:
            n = len(lens)
            lens.sort()
            print('bevels %d  iters %d' % (n, it))
            if n:
                for q in (0, 10, 25, 50, 75, 90, 100):
                    print('   p%-3d 45deg edge %6.3f mm' % (q, lens[min(n - 1, int(q * n / 100))]))
                small = sum(1 for v in lens if v < 0.30)
                print('   <0.30 mm (reads as a right angle): %d' % small)
                if small:
                    print('   -- the nipped ones (net, vertex, edge mm) --')
                    tiny = []
                    for k, (net, layer, w, pts, cap) in enumerate(self.paths):
                        for j, c in enumerate(caps[k]):
                            if 1e-6 < c * math.sqrt(2.0) < 0.30:
                                tiny.append((net, pts[j + 1], c * math.sqrt(2.0)))
                    for net, v, e in sorted(tiny, key=lambda t: -t[2]):
                        print('      %-28s (%.2f,%.2f) %.3f' % (net, v[0], v[1], e))
        return self.segs


def _seg_seg_p(p, a, b):
    from _chk import _seg_seg
    return _seg_seg(p, p, a, b)


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
