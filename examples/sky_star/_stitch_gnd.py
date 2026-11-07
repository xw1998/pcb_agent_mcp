"""One stitching via per F.Cu GND island, chosen by `board_via_sites`.

Why this file exists (all four facts measured on sky_star, see docs/130):

  * the F.Cu GND pour fills as 15 blocks; only 16 conductors exist on the board
    (15 F.Cu pieces + the B.Cu plane) with nothing joining them, and the 101 GND
    pads have 0 tracks and 0 vias, so the plane is unreachable from F.Cu;
  * widening the +3V3 strips moves 115 -> 112 / 109 and adds `zones_intersect`;
  * lowering the zone's own clearance 0.2 -> 0.15 changes nothing (netclass
    `Default.clearance 0.200` overrides it);
  * one plane for the whole board is *worse*: 129 unconnected, GND split 15 -> 62.

So the fix is a via per island, and the legality question is exactly what
`board_via_sites` answers. This script only asks it, one island at a time, and
prefers the site furthest from the island's rim: a hole on a thin neck is left
floating by the next refill (measured: island 5 at (128.7, -139.3) reported
`via_dangling` after DRC refilled the pour).

Order matters: islands are read from a REFILLED board, and the result must be
refilled again before it is believed.

    python _stitch_gnd.py in.kicad_pcb out.kicad_pcb
"""

import sys, os, math, uuid

sys.path.insert(0, os.path.join('..', '..', 'src'))

from pcb_agent_mcp import tools, geometry as G, oracle as _orc

VIA_SIZE = 0.45
VIA_DRILL = 0.25
RIM = 0.50            # want the hole at least this far inside the island

VIA = ('\t(via\r\n\t\t(at %s %s)\r\n\t\t(size %s)\r\n\t\t(drill %s)\r\n'
       '\t\t(layers "F.Cu" "B.Cu")\r\n\t\t(capping no)\r\n'
       '\t\t(covering\r\n\t\t\t(front no)\r\n\t\t\t(back no)\r\n\t\t)\r\n'
       '\t\t(plugging\r\n\t\t\t(front no)\r\n\t\t\t(back no)\r\n\t\t)\r\n'
       '\t\t(filling no)\r\n\t\t(net "GND")\r\n\t\t(uuid "%s")\r\n\t)\r\n')


def own_room(poly, p):
    """Distance from a point to the island's rim (0 on the boundary)."""
    best = 1e9
    n = len(poly)
    for i in range(n):
        a, b = poly[i], poly[(i + 1) % n]
        if a == b:
            continue
        v = G.dist_point_segment(p, a, b)
        if v < best:
            best = v
    return best


def touch_room(poly, p):
    """0 when the point is inside the island, else its distance to the rim.

    A via does not have to be INSIDE an island: its annulus just has to overlap
    island copper. Measured on sky_star: the three 0.28 mm2 blobs are crossed by a
    B.Cu track 0.158 mm from the centre, so no hole may sit there, while a hole on
    the rim (0.44 mm from that track) both connects and passes DRC.
    """
    if G.point_in_polygon(p, poly):
        return 0.0
    best = 1e9
    n = len(poly)
    for i in range(n):
        a, b = poly[i], poly[(i + 1) % n]
        if a == b:
            continue
        v = G.dist_point_segment(p, a, b)
        if v < best:
            best = v
    return best


def rim_room(poly, p):
    """Distance to the rim from inside (0 on the boundary, 1e9 outside)."""
    if not G.point_in_polygon(p, poly):
        return 0.0
    return touch_room(poly, [p]) or _rim(poly, p)


def _rim(poly, p):
    best = 1e9
    n = len(poly)
    for i in range(n):
        a, b = poly[i], poly[(i + 1) % n]
        if a == b:
            continue
        v = G.dist_point_segment(p, a, b)
        if v < best:
            best = v
    return best


def pick(board, poly):
    bb = G.bbox_of(poly)
    big = max(bb[2] - bb[0], bb[3] - bb[1]) >= 6
    step = 1.0 if big else 0.1
    res = _orc.via_sites(
        board, region=[bb[0] - 0.4, bb[1] - 0.4, bb[2] + 0.4, bb[3] + 0.4],
        grid_mm=step, diameter_mm=VIA_SIZE, drill_mm=VIA_DRILL, net='GND',
        layers=['F.Cu', 'B.Cu'], max_sites=300, pours_block=False)
    if res.get('status') != 'ok':
        return None, res.get('message')
    cands = []
    for s in res['value']['sites']:
        p = tuple(s['at'])
        t = touch_room(poly, p)
        if t > 0.15:                 # annulus would miss island copper
            continue
        cands.append((s['room_mm'], -t, s))
    if not cands:
        return None, 'no legal site touching the island'
    cands.sort(reverse=True, key=lambda c: (c[0], c[1]))
    best = cands[0][2]
    best['touch_mm'] = round(touch_room(poly, tuple(best['at'])), 3)
    return best, None


def main(src, dst):
    board, err = tools._load_board({'path': src})
    if err:
        raise SystemExit(err)
    zones = [z for z in board.zones
             if z.get('net') == 'GND' and 'F.Cu' in (z.get('layers') or [])]
    picks, skipped = [], []
    for z in zones:
        for j, poly in enumerate(z['filled_polygons']):
            area = abs(G.polygon_area(poly))
            s, why = pick(board, poly)
            if s is None:
                skipped.append((j, round(area, 2), why))
                continue
            picks.append((s['at'][0], s['at'][1], s, j, area))

    text = open(src, encoding='utf-8', newline='').read()
    blob = ''
    for k, (x, y, s, j, area) in enumerate(picks):
        blob += VIA % ('%.3f' % x, '%.3f' % -y, VIA_SIZE, VIA_DRILL,
                       uuid.UUID(int=0x6000 + k))
    at = text.rfind('\t(gr_rect')
    if at < 0:
        at = text.rfind('\n)')
    out = text[:at] + blob + text[at:]
    open(dst, 'w', encoding='utf-8', newline='').write(out)

    print('islands %d  vias %d  skipped %d' % (len(picks) + len(skipped),
                                               len(picks), len(skipped)))
    for j, area, why in skipped:
        print('   skipped island %-2d area %6.2f mm2  %s' % (j, area, why))
    for x, y, s, j, area in picks:
        print('   via (%8.3f, %9.3f)  island %-2d room %.3f touch %.3f  %s'
              % (x, y, j, s['room_mm'], s['touch_mm'], s['limited_by']))


if __name__ == '__main__':
    main(sys.argv[1], sys.argv[2])
