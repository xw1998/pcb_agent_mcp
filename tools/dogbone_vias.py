"""Move a drilled hole out of a pad: via-in-pad becomes a dogbone.

Why this exists
---------------
A via drilled through pad copper is a process defect, not a routing choice:
the drill pulls the pad's copper into the hole, the annular ring around the hole
is no longer a flat disc under the component lead, and solder wicks down the
barrel during reflow.  Every fab house that quotes via-in-pad asks for resin
plug and plating fill; without it the joint is porous.  So the rule is blunt:
**no hole through pad copper**, no matter how convenient the escape was.

What it does
------------
For each via whose body overlaps a pad of the same net, the tool tries to move
that via *straight out along the line its outgoing track already runs on*.  That
choice is the whole trick: the outgoing copper stays collinear, so nothing bends
off the 45 degree grid, and a short neck connects the pad to the new hole.

A via is moved only when all of these hold; otherwise it is reported, never
guessed at:

* the hole's body, at its new spot, clears every foreign pad and every foreign
  track on every layer by CLR;
* it also clears *its own* pad copper by CLR - a hole whose ring touches the pad
  it left is still a hole in that pad;
* it stays inside the board with EDGE to spare;
* exactly one track ended on the old hole, and that track runs on a multiple of
  45 degrees away from the pad.

The default is a report: it names every hole drilled through pad copper and
says, per hole, whether a dogbone slot exists.  Moving holes needs ``--apply``,
because on a board that is already full the move can cost more than it fixes -
measured on the RK3506 SOM that is exactly what happens (see
docs/163-盘中孔审计与SMD网可布性.md).

usage: dogbone_vias.py IN.kicad_pcb [OUT.kicad_pcb] [--apply [--only N]]
"""
import math
import sys

import pcbnew

CLR = 0.10        # rule clearance between different copper
EDGE = 0.30       # board-edge clearance
STUB_W = 0.10     # neck between pad and the relocated hole
STEP = 0.05       # search step, mm
FAR = 2.20        # never move a hole further than this, mm


def mm(v):
    return pcbnew.ToMM(v)



def cross(a, b, c, d):
    """True when segment ab and segment cd properly cross.  A shared end, a
    collinear overlap or a touching endpoint all return False: those are joints
    the router already draws on purpose."""
    def side(p, q, r):
        v = (q[0] - p[0]) * (r[1] - p[1]) - (q[1] - p[1]) * (r[0] - p[0])
        return 0 if abs(v) < 1e-9 else (1 if v > 0 else -1)
    o1, o2 = side(a, b, c), side(a, b, d)
    o3, o4 = side(c, d, a), side(c, d, b)
    if o1 == o2 or o3 == o4:
        return False
    return True


def inside(pt, poly):
    x, y = pt
    hit = False
    n = len(poly)
    for i in range(n):
        x1, y1 = poly[i]
        x2, y2 = poly[(i + 1) % n]
        if (y1 > y) != (y2 > y):
            if x < (x2 - x1) * (y - y1) / (y2 - y1 + 1e-12) + x1:
                hit = not hit
    return hit


def boxes(board):
    pads = []
    for f in board.GetFootprints():
        for p in f.Pads():
            bb = p.GetBoundingBox()
            pads.append(dict(
                ref='%s.%s' % (f.GetReference(), p.GetNumber()),
                net=p.GetNetname(),
                x=mm(p.GetPosition().x), y=mm(p.GetPosition().y),
                l=mm(bb.GetLeft()), r=mm(bb.GetRight()),
                t=mm(bb.GetTop()), b=mm(bb.GetBottom()),
            ))
    return pads


def edge_gap(px, py, e):
    return min(px - e[0], e[2] - px, py - e[1], e[3] - py)


def gap_to_box(px, py, b):
    dx = max(b['l'] - px, 0.0, px - b['r'])
    dy = max(b['t'] - py, 0.0, py - b['b'])
    return math.hypot(dx, dy)


def seg_gap(px, py, s):
    (ax, ay), (bx, by) = s['a'], s['b']
    vx, vy = bx - ax, by - ay
    L2 = vx * vx + vy * vy
    t = 0.0 if L2 == 0 else max(0.0, min(1.0, ((px - ax) * vx + (py - ay) * vy) / L2))
    return math.hypot(px - (ax + t * vx), py - (ay + t * vy))


def seg_gap_pt(a, b, px, py):
    vx, vy = b[0] - a[0], b[1] - a[1]
    L2 = vx * vx + vy * vy
    t = 0.0 if L2 == 0 else max(0.0, min(1.0, ((px - a[0]) * vx + (py - a[1]) * vy) / L2))
    return math.hypot(px - (a[0] + t * vx), py - (a[1] + t * vy))

def site_ok(px, py, r, net, pads, segs, e, zones=(), vias=()):
    if edge_gap(px, py, e) < r + EDGE:
        return False
    for b in pads:
        if gap_to_box(px, py, b) < r + CLR - 0.005:
            return False
    for s in segs:
        if s['net'] == net:
            continue                      # its own copper may carry the neck
        if seg_gap(px, py, s) < r + s['w'] / 2.0 + CLR - 0.005:
            return False
    # Existing vias are obstacles too: they carry copper on every layer.  Leaving
    # them out let /FB_U5 land 0.0908 mm from a /GND via and earned six clearance
    # violations - the tool was reporting a legal site that DRC then refused.
    for (vx, vy, vr) in vias:
        if math.hypot(px - vx, py - vy) < r + vr + CLR - 0.005:
            return False
    return True


def dir45(dx, dy):
    n = math.hypot(dx, dy)
    if n < 1e-6:
        return None
    a = math.degrees(math.atan2(dy, dx))
    k = round(a / 45.0)
    if abs(a - k * 45.0) > 1.0:
        return None
    return (math.cos(math.radians(k * 45.0)), math.sin(math.radians(k * 45.0)))


def main():
    src = sys.argv[1]
    apply = '--apply' in sys.argv
    dst = [a for a in sys.argv[2:] if not a.startswith('--')]
    dst = dst[0] if dst else src
    measure_only = not apply
    only = None
    for i, a in enumerate(sys.argv):
        if a == '--only':
            only = int(sys.argv[i + 1])
    b = pcbnew.LoadBoard(src)
    pads = boxes(b)
    bb = b.GetBoardEdgesBoundingBox()
    e = [mm(bb.GetLeft()), mm(bb.GetTop()), mm(bb.GetRight()), mm(bb.GetBottom())]

    zones = []
    for z in b.Zones():
        for k in range(z.Outline().OutlineCount()):
            pts = [(mm(z.Outline().Outline(k).CPoint(i).x),
                    mm(z.Outline().Outline(k).CPoint(i).y))
                   for i in range(z.Outline().Outline(k).PointCount())]
            if len(pts) >= 3:
                zones.append(dict(net=z.GetNetname(), pts=pts))
    tracks = [t for t in b.GetTracks() if not isinstance(t, pcbnew.PCB_VIA)]
    vias = [t for t in b.GetTracks() if isinstance(t, pcbnew.PCB_VIA)]
    segs = [dict(net=t.GetNetname(), L=pcbnew.LayerName(t.GetLayer()),
                 w=mm(t.GetWidth()), a=(mm(t.GetStart().x), mm(t.GetStart().y)),
                 b=(mm(t.GetEnd().x), mm(t.GetEnd().y)), t=t) for t in tracks]
    via_pos = []
    for v in vias:
        p = v.GetPosition()
        via_pos.append((id(v), mm(p.x), mm(p.y), mm(v.GetWidth(pcbnew.F_Cu)) / 2.0))

    inp = []
    for v in vias:
        pos = v.GetPosition()
        x, y = mm(pos.x), mm(pos.y)
        r = mm(v.GetWidth(pcbnew.F_Cu)) / 2.0
        dr = mm(v.GetDrill())
        for pad in pads:
            if pad['net'] == v.GetNetname() and gap_to_box(x, y, pad) < r - 0.005:
                inp.append((v, x, y, r, dr, pad))
                break

    print('vias total %d, drilled through pad copper %d' % (len(vias), len(inp)))
    moved, refused = [], []
    for idx, (v, x, y, r, dr, pad) in enumerate(inp):
        if only is not None and idx != only:
            continue
        inc = [s for s in segs
               if (abs(s['a'][0] - x) < 0.02 and abs(s['a'][1] - y) < 0.02) or
               (abs(s['b'][0] - x) < 0.02 and abs(s['b'][1] - y) < 0.02)]
        if measure_only:
            print('  %-14s (%8.3f,%8.3f) r=%.2f pad %-8s incident=%d'
                  % (v.GetNetname(), x, y, r, pad['ref'], len(inc)))
            continue
        # A move is legal only if every track that ended on the old hole still
        # runs on its own line afterwards: the via slides along that line and its
        # endpoint slides with it, so no angle changes anywhere.  A hole whose
        # neighbourhood has two different lines is refused, not averaged.
        seglines = []
        for g in inc:
            far = (g['b'] if (abs(g['a'][0] - x) < 0.02 and abs(g['a'][1] - y) < 0.02)
                   else g['a'])
            seglines.append((far, g))
        dirs = []
        for (far, g) in seglines:
            u = dir45(far[0] - x, far[1] - y)
            if u is not None and u not in dirs:
                dirs.append(u)
        if not seglines:
            dirs = [(math.cos(math.radians(k * 45.0)),
                     math.sin(math.radians(k * 45.0))) for k in range(8)]
        if not dirs:
            refused.append((v, x, y, 'outgoing copper is not on the 45 degree grid'))
            continue

        def line_of(g):
            (ax, ay), (bx, by) = g['a'], g['b']
            n = math.hypot(bx - ax, by - ay)
            return (ax, ay, (bx - ax) / n, (by - ay) / n)

        def dist_line(p, ln):
            ax, ay, ux, uy = ln
            return abs((p[0] - ax) * uy - (p[1] - ay) * ux)

        lines = [line_of(g) for (far, g) in seglines]
        spot = None
        for u in dirs:
            k = 1
            while k * STEP <= FAR:
                px = round(x + u[0] * k * STEP, 4)
                py = round(y + u[1] * k * STEP, 4)
                if all(dist_line((px, py), ln) < 0.061 for ln in lines):
                    others = [(vx, vy, vr) for (vi, vx, vy, vr) in via_pos if vi != id(v)]
                    if site_ok(px, py, r, v.GetNetname(), pads, segs, e, zones, others):
                        spot = (px, py)
                        break
                k += 1
            if spot is not None:
                break
        if spot is None:
            refused.append((v, x, y, 'no legal site within %.2f mm on the lines its '
                            'own copper already runs on' % FAR))
            continue
        neck = ((x, y), spot)
        bad = False
        mine = set(id(g['t']) for g in inc)
        for s2 in segs:
            if (s2['L'] == 'F.Cu' and id(s2['t']) not in mine and
                    cross(neck[0], neck[1], s2['a'], s2['b'])):
                bad = True
                break
        if not bad:
            for (vi, vx, vy, vr) in via_pos:
                if vi == id(v):
                    continue
                if seg_gap_pt(neck[0], neck[1], vx, vy) < 0.05 + vr + CLR - 0.005:
                    bad = True
                    break
        if bad:
            refused.append((v, x, y, 'the neck would cross existing copper'))
            continue
        moved.append(dict(via=v, net=v.GetNetname(), nc=v.GetNetCode(), old=(x, y),
                          spot=spot, r=r, drill=dr, segs=inc, lines=lines, pad=pad))

    print('  movable %d, refused %d' % (len(moved), len(refused)))
    for (v, x, y, why) in refused:
        print('  REFUSED %-14s (%8.3f,%8.3f) : %s' % (v.GetNetname(), x, y, why))
    if measure_only or not moved:
        return 0

    for m in moved:
        b.Remove(m['via'])
    for m in moved:
        nv = pcbnew.PCB_VIA(b)
        nv.SetPosition(pcbnew.VECTOR2I(pcbnew.FromMM(m['spot'][0]),
                                       pcbnew.FromMM(m['spot'][1])))
        nv.SetWidth(pcbnew.FromMM(m['r'] * 2))
        nv.SetDrill(pcbnew.FromMM(m['drill']))
        nv.SetViaType(pcbnew.VIATYPE_THROUGH)
        nv.SetLayerPair(pcbnew.F_Cu, pcbnew.B_Cu)
        nv.SetNetCode(m['nc'])
        b.Add(nv)
    for m in moved:
        for g, ln in zip(m['segs'], m['lines']):
            t = g['t']
            ax, ay, ux, uy = ln
            proj = (m['spot'][0], m['spot'][1])
            snap = (round(ax + ux * ((proj[0] - ax) * ux + (proj[1] - ay) * uy), 4),
                    round(ay + uy * ((proj[0] - ax) * ux + (proj[1] - ay) * uy), 4))
            if (abs(mm(t.GetStart().x) - m['old'][0]) < 0.02 and
                    abs(mm(t.GetStart().y) - m['old'][1]) < 0.02):
                t.SetStart(pcbnew.VECTOR2I(pcbnew.FromMM(snap[0]),
                                           pcbnew.FromMM(snap[1])))
            else:
                t.SetEnd(pcbnew.VECTOR2I(pcbnew.FromMM(snap[0]),
                                         pcbnew.FromMM(snap[1])))
    pcbnew.SaveBoard(dst, b)
    print('  wrote %s (%d holes moved out of pads)' % (dst, len(moved)))
    return 0


if __name__ == '__main__':
    sys.exit(main())
