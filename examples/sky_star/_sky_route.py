"""Hand-written routing for the left header P1 of sky_star.  No router.

Classes
  L  : MCU left column -> inner/outer header pad, corridor lanes (already done).
  B  : MCU bottom row  -> header pads y<=-6.35, fanned down then on B.Cu.
  T  : MCU top row     -> the two top header pads, on B.Cu past the crowded top.

Rules baked in: 0.2 mm signal, 0.45/0.25 via, 45 deg corners, monotone nesting
so no two tracks of different nets ever cross.  Verified by _chk.py then DRC.
"""
import json, math, sys
sys.path.insert(0, '_scratch/sky')
sys.path.insert(0, '.')
import _rte as R

W = R.W
NETS = R.N

XN = -9.70      # first lane (L class)
LANE = 0.55
XVIA = (-8.80, -9.30)
XVIA2 = -18.25
XPN = -16.93
XPW = -19.47


def net_of(ref, num):
    for n, ps in NETS.items():
        for p in ps:
            if p['ref'] == ref and p['num'] == num:
                return n, p
    raise KeyError((ref, num))


def mcu_pad(net):
    for p in NETS[net]:
        if p['ref'] == 'U1':
            return p
    raise KeyError(net)


def klass(rows, side):
    return [r for r in rows if r[3] == side]


def build_L(rt, rows):
    """Left column, sorted by header y desc; index by in/out order like before."""
    ins = sorted([r for r in rows if not r[2]], key=lambda r: -r[0]['y'])
    outs = sorted([r for r in rows if r[2]], key=lambda r: -r[0]['y'])
    for i, (p, m, _, _) in enumerate(ins):
        net = p['net']
        xc = XN - LANE * i
        rt.poly(net, 'F.Cu', [(m['x'], m['y']), (xc, m['y']), (xc, p['y']), (XPN, p['y'])])
    for i, (p, m, _, _) in enumerate(outs):
        net = p['net']
        xc = XN - LANE * i
        xv = XVIA[i % 2]
        rt.poly(net, 'F.Cu', [(m['x'], m['y']), (xv, m['y'])])
        rt.via(net, xv, m['y'])
        rt.poly(net, 'B.Cu', [(xv, m['y']), (xc, m['y']), (xc, p['y']), (XVIA2, p['y'])])
        rt.via(net, XVIA2, p['y'])
        rt.poly(net, 'F.Cu', [(XVIA2, p['y']), (XPW, p['y'])])


def build_B(rt, rows):
    """Bottom row -> header pads below y=-6.

    Two nested ladders, because the targets straddle the fan band: the six
    shallow pads (sy > -12) turn *down* into the band then rise to their pad,
    the six deep pads plunge past it.  Rise uses lanes running left, plunge
    uses lanes running right; in each half the nesting is monotone, so nothing
    crosses.  The fan lives on B.Cu (only six pads on the bottom layer there).
    """
    rows = sorted(rows, key=lambda r: r[1]['x'])          # by MCU pin x
    rise = [r for r in rows if r[0]['y'] > -12.0]
    plunge = [r for r in rows if r[0]['y'] <= -12.0]

    def emit(i, p, m, outer, yc, xl, d):
        sy = p['y']
        ay = sy + 0.3 if not outer else sy - 0.3
        xe = XVIA2 if outer else -15.5
        rt.poly(net_of('P1', p['num'])[0] and p['net'] and 'F.Cu' or 'F.Cu', 'F.Cu', []) if False else None
        net = p['net']
        rt.poly(net, 'F.Cu', [(m['x'], m['y']), (m['x'], d)])
        rt.via(net, m['x'], d)
        rt.poly(net, 'B.Cu', [(m['x'], d), (m['x'], yc), (xl, yc), (xl, ay), (xe, ay)],
                bevel=1.20)
        rt.via(net, xe, ay)
        rt.poly(net, 'F.Cu', [(xe, ay), (p['x'], sy)])

    for i, (p, m, outer, _) in enumerate(rise):
        emit(i, p, m, outer, yc=-10.0 - 0.5 * i, xl=-9.7 - 0.5 * i,
             d=-9.0 if i % 2 == 0 else -9.4)
    for i, (p, m, outer, _) in enumerate(plunge):
        emit(i, p, m, outer, yc=-13.2 - 0.5 * i, xl=-13.0 + 0.5 * i,
             d=-9.0 if i % 2 == 0 else -9.4)


def build_T(rt, rows):
    """Top row: up, over to B.Cu (the top strip is crowded on F.Cu), then left."""
    fin = {r[0]['num']: r for r in rows}
    plans = {
        '1': dict(jog=9.35, xv=-5.0, xe=XVIA2, sy=24.13),   # PA2, outer pad
        '4': dict(jog=8.75, xv=-5.5, xe=-15.5, sy=21.59),   # PA1, inner pad
    }
    for num, pl in plans.items():
        p, m, outer, _ = fin[num]
        net = p['net']
        rt.poly(net, 'F.Cu', [(m['x'], m['y']), (m['x'], pl['jog']), (pl['xv'], pl['jog'])],
                bevel=1.20)
        rt.via(net, pl['xv'], pl['jog'])
        rt.poly(net, 'B.Cu', [(pl['xv'], pl['jog']), (pl['xv'], pl['sy']), (pl['xe'], pl['sy'])])
        rt.via(net, pl['xe'], pl['sy'])
        rt.poly(net, 'F.Cu', [(pl['xe'], pl['sy']), (p['x'], p['y'])])



XR0 = 14.80     # right-side lane 0 (closest to header; 0.5 clear of the
                # PC3 descent at x=15.30)
RSTEP = 0.40
XRI = 15.62     # inner-column via x (0.81 gap between U5 and the pad column)
XRO = 18.25     # outer-column via x (0.74 gap between the two pad columns)


def build_R(rt, rows):
    """MCU right column -> header.  Long run on B.Cu; lanes nest outward."""
    rows = sorted(rows, key=lambda r: -r[0]['y'])
    for i, (p, m, outer, _) in enumerate(rows):
        net, sy = p['net'], p['y']
        # outer rows drop 0.45 so the via never sits on the inner pad row and
        # the corridor clears PC3's pad-row approach at 3.81
        ay = sy + (-0.55 if outer else 0.3)
        xc = XR0 - RSTEP * i
        xv = 8.8 if i % 2 == 0 else 9.3
        xe = XRO if outer else XRI
        rt.poly(net, 'F.Cu', [(m['x'], m['y']), (xv, m['y'])])
        rt.via(net, xv, m['y'])
        rt.poly(net, 'B.Cu', [(xv, m['y']), (xc, m['y']), (xc, ay), (xe, ay)])
        rt.via(net, xe, ay)
        rt.poly(net, 'F.Cu', [(xe, ay), (p['x'], sy)])


def build_B2(rt, rows):
    """Three bottom-row nets that reach the right header under the switches."""
    # PC7 (outer) runs *above* PC6 so PC6's horizontal never has to cross the
    # deeper of the two verticals; the two verticals are 0.5 apart in x.
    plan = {'$1N4758': dict(vy=-9.30, ry=-16.81, xe=XRO, ay=-16.51),
            'PC7': dict(vy=-9.30, ry=-18.95, xe=XRO, ay=-19.05),
            'PC6': dict(vy=-9.90, ry=-19.55, xe=XRI, ay=-19.05)}
    for p, m, outer, _ in rows:
        pl = plan[p['net']]
        rt.poly(p['net'], 'F.Cu', [(m['x'], m['y']), (m['x'], pl['vy'])])
        rt.via(p['net'], m['x'], pl['vy'])
        rt.poly(p['net'], 'B.Cu', [(m['x'], pl['vy']), (m['x'], pl['ry']),
                                   (pl['xe'], pl['ry'])])
        rt.via(p['net'], pl['xe'], pl['ry'])
        rt.poly(p['net'], 'F.Cu', [(pl['xe'], pl['ry']), (p['x'], p['y'])])


def build_T2(rt, rows):
    """Top row -> right header, all on B.Cu.

    Ten nets leave the top pad row upward, drop to the bottom layer in one
    stagger row of vias (y 8.78 on the right cluster, 9.05/9.60 over the left
    cluster where B.Cu is bare behind the package) and finish on B.Cu, which is
    empty behind the header (its pads are single-sided F.Cu).  The staircase
    nests: the leftmost via owns the highest corridor, so a vertical never
    crosses a horizontal of another net.  B1.1 (x 5.7..9.1, y 13.85..16.15) is
    passed under at 13.55/13.15 and above at 16.51.
    """
    by = {r[0]['net']: r for r in rows}

    def drop(net, vx, vy, yknee, path, xe, ay):
        p, m, outer, _ = by[net]
        rt.poly(net, 'F.Cu', [(m['x'], m['y']), (m['x'], yknee), (vx, vy)],
                bevel=1.20)
        rt.via(net, vx, vy)
        rt.poly(net, 'B.Cu', [(vx, vy)] + path)
        rt.via(net, xe, ay)
        rt.poly(net, 'F.Cu', [(xe, ay), (p['x'], p['y'])])

    # --- pads 3.0..6.0: one via row 0.60 apart, knee at the pad tip so the
    # fan never clips a neighbour pad; U1.6 escapes through a via just above
    # its own pad, in the 0.60 slot left free between 2.90 and 4.00 ----------
    drop('$1N4752', 2.90, 8.78, 8.35, [(2.90, 16.51), (XRO, 16.51)], XRO, 16.51)
    drop('PE6', 4.00, 8.78, 8.35, [(4.00, 13.55), (9.70, 13.55), (9.70, 13.97),
                                   (XRI, 13.97)], XRI, 13.97)
    drop('PE5', 4.60, 8.78, 8.35, [(4.60, 13.15), (17.00, 13.15), (17.00, 13.97),
                                   (XRO, 13.97)], XRO, 13.97)
    drop('PE4', 5.20, 8.78, 8.35, [(5.20, 11.95), (XRI, 11.95)], XRI, 11.95)
    drop('PE3', 5.80, 8.78, 8.35, [(5.80, 11.43), (XRO, 11.43)], XRO, 11.43)
    # PE2 has no neighbour on its right, so it can take a straight diagonal and
    # leave the knee slot to PE3.
    p, m, outer, _ = by['PE2']
    rt.poly('PE2', 'F.Cu', [(m['x'], m['y']), (6.40, 8.78)])
    rt.via('PE2', 6.40, 8.78)
    rt.poly('PE2', 'B.Cu', [(6.40, 8.78), (6.40, 8.89), (XRI, 8.89)])
    rt.via('PE2', XRI, 8.89)
    rt.poly('PE2', 'F.Cu', [(XRI, 8.89), (p['x'], p['y'])])

    # --- pads -2.5..-1.0: vias fanned left, two staggered rows --------------
    drop('PC3', -2.50, 9.60, 8.55, [(-2.50, 5.50), (15.30, 5.50), (15.30, 3.81),
                                    (XRI, 3.81)], XRI, 3.81)
    drop('PC2', -1.95, 8.95, 8.55, [(-1.95, 5.85), (XRO, 5.85)], XRO, 5.85)
    drop('PC1', -1.20, 9.60, 8.55, [(-1.20, 6.35), (XRI, 6.35)], XRI, 6.35)
    drop('PC0', -0.55, 9.05, 8.55, [(-0.55, 7.00), (XRO, 7.00), (XRO, 7.70)],
         XRO, 7.70)


def main():
    rt = R.Route()
    g = R.left_group()
    build_L(rt, klass(g, 'L'))
    build_B(rt, klass(g, 'B'))
    build_T(rt, klass(g, 'T'))
    g2 = R.right_group()
    build_R(rt, klass(g2, 'R'))
    build_B2(rt, klass(g2, 'B'))
    build_T2(rt, klass(g2, 'T'))
    import _sky_route2
    _sky_route2.build(rt)
    import _sky_route3
    _sky_route3.build(rt)
    import _sky_route4
    _sky_route4.build(rt)
    import _sky_route5
    _sky_route5.build(rt)
    import _sky_route6
    _sky_route6.build(rt)
    rt.finalize()
    out = dict(segs=[dict(net=n, layer=l, w=w, pts=[a, b])
                     for n, l, w, a, b in rt.segs],
               vias=[dict(net=n, x=x, y=y, drill=d, size=s)
                     for n, x, y, d, s in rt.vias])
    json.dump(out, open('_scratch/sky/_routes.json', 'w'), indent=1)
    print('segs', len(out['segs']), 'vias', len(out['vias']))
    return out


if __name__ == '__main__':
    out = main()
    sys.path.insert(0, '_scratch/sky')
    import _chk
    _chk.check([(s['net'], s['layer'], s['w'], tuple(s['pts'][0]), tuple(s['pts'][1]))
                for s in out['segs']],
               [(v['net'], v['x'], v['y'], v['drill'], v['size']) for v in out['vias']])
