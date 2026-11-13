# Shared copper geometry for the self-check and the stitching tool.
# A pad is modelled by its real shape: circle -> point + radius, oval ->
# capsule (segment + radius), rect -> rectangle, roundrect -> rectangle shrunk
# by its corner radius + that radius as an inflation.  Every item therefore
# owns a base shape ('disc' point / 'seg' / 'rect') plus an inflation 'half';
# copper distance = max(0, base_distance - halfA - halfB).  The earlier version
# turned every pad into a max(hx,hy) disc, which both missed pad corners
# (silent short) and reported circles as clipped by their bounding square
# (false 0.03 mm hits on J1's round pads).
import math
import pcbnew

SIG = {'F.Cu', 'In2.Cu', 'In4.Cu', 'B.Cu'}
ALL = {'F.Cu', 'In1.Cu', 'In2.Cu', 'In3.Cu', 'In4.Cu', 'B.Cu'}


def pt_seg(px, py, x1, y1, x2, y2):
    dx, dy = x2 - x1, y2 - y1
    L2 = dx * dx + dy * dy
    if L2 < 1e-12:
        return math.hypot(px - x1, py - y1)
    u = max(0.0, min(1.0, ((px - x1) * dx + (py - y1) * dy) / L2))
    return math.hypot(px - (x1 + u * dx), py - (y1 + u * dy))


def pt_rect(px, py, hx, hy):
    return math.hypot(max(abs(px) - hx, 0.0), max(abs(py) - hy, 0.0))


def seg_rect(s0, hx, hy):
    if pt_rect(s0[0], s0[1], hx, hy) <= 0.0 and pt_rect(s0[2], s0[3], hx, hy) <= 0.0:
        return 0.0
    d = min(pt_rect(s0[0], s0[1], hx, hy), pt_rect(s0[2], s0[3], hx, hy))
    for (ex0, ey0, ex1, ey1) in ((-hx, -hy, hx, -hy), (hx, -hy, hx, hy),
                                 (hx, hy, -hx, hy), (-hx, hy, -hx, -hy)):
        d = min(d, pt_seg(ex0, ey0, s0[0], s0[1], s0[2], s0[3]),
                pt_seg(s0[0], s0[1], ex0, ey0, ex1, ey1),
                pt_seg(s0[2], s0[3], ex0, ey0, ex1, ey1))
        def cr(ax, ay, bx, by, cx, cy):
            return (bx - ax) * (cy - ay) - (by - ay) * (cx - ax)
        d1 = cr(s0[0], s0[1], s0[2], s0[3], ex0, ey0)
        d2 = cr(s0[0], s0[1], s0[2], s0[3], ex1, ey1)
        d3 = cr(ex0, ey0, ex1, ey1, s0[0], s0[1])
        d4 = cr(ex0, ey0, ex1, ey1, s0[2], s0[3])
        if d1 * d2 < 0 and d3 * d4 < 0:
            d = 0.0
    return d


def seg_seg(a, c):
    def cr(ax, ay, bx, by, cx, cy):
        return (bx - ax) * (cy - ay) - (by - ay) * (cx - ax)
    d1 = cr(a[0], a[1], a[2], a[3], c[0], c[1])
    d2 = cr(a[0], a[1], a[2], a[3], c[2], c[3])
    d3 = cr(c[0], c[1], c[2], c[3], a[0], a[1])
    d4 = cr(c[0], c[1], c[2], c[3], a[2], a[3])
    if d1 * d2 < 0 and d3 * d4 < 0:
        return 0.0
    return min(pt_seg(a[0], a[1], c[0], c[1], c[2], c[3]),
               pt_seg(a[2], a[3], c[0], c[1], c[2], c[3]),
               pt_seg(c[0], c[1], a[0], a[1], a[2], a[3]),
               pt_seg(c[2], c[3], a[0], a[1], a[2], a[3]))


def frame_pt(px, py, g):
    """world point -> rectangle frame (axis aligned, centred at g[0], g[1])"""
    cx, cy, hx, hy, c, s = g[:6]
    dx, dy = px - cx, py - cy
    return dx * c + dy * s, -dx * s + dy * c


def frame_seg(s0, g):
    a = frame_pt(s0[0], s0[1], g)
    b = frame_pt(s0[2], s0[3], g)
    return (a[0], a[1], b[0], b[1])


def rect_rect(ga, gb):
    sub = []
    for (g, other) in ((ga, gb), (gb, ga)):
        q = []
        for sx in (-1, 1):
            for sy in (-1, 1):
                p = frame_pt(other[0] + sx * other[2] * other[4] - sy * other[3] * other[5],
                             other[1] + sx * other[2] * other[5] + sy * other[3] * other[4], g)
                q.append(p)
        edges = [q[0], q[1], q[3], q[2]]
        d = min(seg_rect((edges[i][0], edges[i][1], edges[(i + 1) % 4][0], edges[(i + 1) % 4][1]),
                         g[2], g[3]) for i in range(4))
        sub.append(d)
    return min(sub)


def build_items(board, extra_signal_layers=None):
    """Return (items, holes) for the physical board.  Items carry kind, net,
    layers, geom and half (inflation radius)."""
    items, holes = [], []
    for t in board.GetTracks():
        s, e = t.GetStart(), t.GetEnd()
        x1, y1 = pcbnew.ToMM(s.x), -pcbnew.ToMM(s.y)
        x2, y2 = pcbnew.ToMM(e.x), -pcbnew.ToMM(e.y)
        if isinstance(t, pcbnew.PCB_VIA):
            items.append(dict(kind='disc', net=t.GetNetname(), layers=set(ALL),
                              geom=(x1, y1, x1, y1), half=t.GetWidth(pcbnew.F_Cu) / 2e6))
            holes.append((x1, y1, t.GetDrill() / 2e6))
        else:
            items.append(dict(kind='seg', net=t.GetNetname(),
                              layers={pcbnew.LayerName(t.GetLayer())},
                              geom=(x1, y1, x2, y2), half=t.GetWidth() / 2e6))
    lid = {pcbnew.LayerName(i): i for i in range(pcbnew.PCB_LAYER_ID_COUNT)}
    for fp in board.GetFootprints():
        for p in fp.Pads():
            sz, pos = p.GetSize(), p.GetPosition()
            x, y = pcbnew.ToMM(pos.x), -pcbnew.ToMM(pos.y)
            hx, hy = sz.x / 2e6, sz.y / 2e6
            on = {L for L in ALL if p.IsOnLayer(lid[L])}
            drill = p.GetDrillSize().x / 2e6
            if drill > 0:
                on = set(ALL)   # a plated through hole spans every copper layer
                holes.append((x, y, drill))
            a = math.radians(-p.GetOrientationDegrees())
            shape = p.GetShape()
            if shape == pcbnew.PAD_SHAPE_CIRCLE:
                items.append(dict(kind='disc', net=p.GetNetname(), layers=on,
                                  geom=(x, y, x, y), half=min(hx, hy),
                                  pad_owner=(fp.GetReference(), p.GetNumber())))
            elif shape == pcbnew.PAD_SHAPE_OVAL:
                if hx >= hy:
                    lx, ly = hx - hy, 0.0
                else:
                    lx, ly = 0.0, hy - hx
                dx, dy = lx * math.cos(a) - ly * math.sin(a), lx * math.sin(a) + ly * math.cos(a)
                items.append(dict(kind='seg', net=p.GetNetname(), layers=on,
                                  geom=(x - dx, y - dy, x + dx, y + dy), half=min(hx, hy),
                                  pad_owner=(fp.GetReference(), p.GetNumber())))
            else:
                r = p.GetRoundRectCornerRadius() / 1e6 if shape == pcbnew.PAD_SHAPE_ROUNDRECT else 0.0
                ihx, ihy = max(hx - r, 0.0), max(hy - r, 0.0)
                items.append(dict(kind='rect', net=p.GetNetname(), layers=on,
                                  geom=(x, y, ihx, ihy, math.cos(a), math.sin(a)), half=r,
                                  pad_owner=(fp.GetReference(), p.GetNumber())))
    return items, holes


def base_dist(A, B):
    """distance between the two base shapes (no inflation)."""
    ga, gb = A['geom'], B['geom']
    ka, kb = A['kind'], B['kind']
    if ka == 'disc' and kb == 'disc':
        return math.hypot(ga[0] - gb[0], ga[1] - gb[1])
    if ka == 'disc' and kb == 'seg':
        return pt_seg(ga[0], ga[1], gb[0], gb[1], gb[2], gb[3])
    if ka == 'seg' and kb == 'disc':
        return pt_seg(gb[0], gb[1], ga[0], ga[1], ga[2], ga[3])
    if ka == 'disc' and kb == 'rect':
        return pt_rect(*frame_pt(ga[0], ga[1], gb), gb[2], gb[3])
    if ka == 'rect' and kb == 'disc':
        return pt_rect(*frame_pt(gb[0], gb[1], ga), ga[2], ga[3])
    if ka == 'seg' and kb == 'seg':
        return seg_seg(ga, gb)
    if ka == 'seg' and kb == 'rect':
        return seg_rect(frame_seg(ga, gb), gb[2], gb[3])
    if ka == 'rect' and kb == 'seg':
        return seg_rect(frame_seg(gb, ga), ga[2], ga[3])
    return rect_rect(ga, gb)


def dist(A, B):
    return max(0.0, base_dist(A, B) - A['half'] - B['half'])


def dist_item(it, px, py):
    """copper distance from point (px, py) to item it."""
    if it['kind'] == 'disc':
        return math.hypot(it['geom'][0] - px, it['geom'][1] - py) - it['half']
    if it['kind'] == 'rect':
        return pt_rect(*frame_pt(px, py, it['geom']), it['geom'][2], it['geom'][3]) - it['half']
    return pt_seg(px, py, it['geom'][0], it['geom'][1], it['geom'][2], it['geom'][3]) - it['half']


def dist_seg(it, s0):
    if it['kind'] == 'disc':
        return pt_seg(it['geom'][0], it['geom'][1], s0[0], s0[1], s0[2], s0[3]) - it['half']
    if it['kind'] == 'rect':
        return seg_rect(frame_seg(s0, it['geom']), it['geom'][2], it['geom'][3]) - it['half']
    return seg_seg(s0, it['geom']) - it['half']
