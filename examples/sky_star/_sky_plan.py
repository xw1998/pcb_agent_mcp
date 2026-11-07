"""Floorplan the 立创 sky-star parts on my own outline, then emit a copper-free board.

Canonical frame: mm, Y up.  The .kicad_pcb is written with y_file = -y.
"""
import sys, math, json
sys.path.insert(0, '.')
sys.path.insert(0, '_scratch/sky')
from _sky_model import model

MIL = 0.0254
BW, BH = 44.0, 72.0          # board outline, mm
EDGE = 0.6                   # pad must stay this far inside the outline

# ref: (x, y, rot, layer)  layer 1 = F.Cu (front), 2 = B.Cu (back)
PLACE = {
    # headers, full length of both long edges
    'P1': (-18.2, 0.0, -90, 1), 'P2': (18.2, 0.0, -90, 1),
    # M3 corners
    'hole1': (-17.8, 31.0, 0, 1), 'hole2': (17.8, 31.0, 0, 1),
    'hole3': (17.8, -31.0, 0, 1), 'hole4': (-17.8, -31.0, 0, 1),
    'U1': (0.0, 0.0, 180, 1),
    # TF card, top edge
    'CARD1': (0.0, 28.6, 180, 1),
    # SDIO ESD row + pull-up arrays under the card
    # Diodes and pull-ups are ordered like the socket pads (D2, D3, CMD,
    # N1161, D0, D1, CD).  The reference kept them reversed; matching the
    # card order turns the fan-out into seven nested escapes instead of a
    # seven-net channel inversion.
    'D5': (-3.85, 20.4, -90, 1), 'D4': (-1.85, 20.4, -90, 1), 'D8': (0.15, 20.4, -90, 1),
    'D9': (2.15, 20.4, -90, 1), 'D7': (4.15, 20.4, -90, 1), 'D6': (6.15, 20.4, -90, 1),
    'D3': (8.15, 20.4, -90, 1),
    'R18': (-3.633, 19.0, 180, 1), 'R19': (-1.633, 19.0, 180, 1),
    'R15': (0.367, 19.0, 180, 1), 'R16': (4.367, 19.0, 180, 1),
    'R17': (6.367, 19.0, 180, 1), 'R20': (9.233, 19.0, 0, 1),
    'RN1': (-1.2, 17.05, 0, 1), 'RN2': (7.3, 17.05, 0, 1),
    # L1 stays next to the VDDA pin; everything else that used to sit in the
    # left corridor moves to the free band under the MCU so the 8.6 mm
    # escape channel between U1 and P1 is completely clear.
    'L1': (-7.5, 15.5, 180, 1),
    # key row (hands reach the middle-lower band)
    'SW1': (-6.0, -14.8, -90, 1), 'SW2': (0.0, -14.8, -90, 1), 'SW3': (6.0, -14.8, -90, 1),
    # pulled-up / LED / ADC filter parts, row A
    # the de-bounce / filter row is ordered by NET, not by reference: R24+C25
    # under SW1, R25+R27+C26 under SW2, R26+R28+C27 under SW3, R29+LED2 and
    # R30.  Keeping each net inside a 2.4 mm window is the only way this row can
    # be routed on one layer - the pads leave a single usable lane above them.
    'R24': (-7.2, -19.1, 90, 1), 'C25': (-6.0, -19.1, 90, 1),
    'R25': (-4.8, -19.1, 270, 1), 'R27': (-3.6, -19.1, 270, 1), 'C26': (-2.4, -19.1, 90, 1),
    'R26': (-1.2, -19.1, 270, 1), 'R28': (0.0, -19.1, 270, 1), 'C27': (1.2, -19.1, 90, 1),
    'R29': (2.4, -19.1, 270, 1), 'LED2': (3.6, -19.1, -90, 1), 'R30': (4.8, -19.1, 270, 1),
    'C10': (6.0, -19.1, 90, 1), 'C11': (7.2, -19.1, 90, 1),
    # ADC / USB RC, row B
    'R8': (-7.2, -20.7, 90, 1), 'R1': (-6.0, -20.7, 90, 1), 'R2': (-4.8, -20.7, 90, 1),
    'R4': (-3.6, -20.7, 90, 1), 'C5': (-2.4, -20.7, 90, 1), 'C6': (-1.2, -20.7, -90, 1),
    'C13': (0.0, -20.7, 90, 1), 'R5': (1.2, -20.7, 90, 1), 'R6': (2.4, -20.7, 90, 1),
    'R7': (3.6, -20.7, 0, 1), 'C12': (-13.5, -28.5, 90, 1),
    # crystals + load caps
    'X1': (-3.5, 11.5, 0, 1), 'C1': (-7.0, 9.5, 0, 1), 'C2': (-7.0, 12.5, 0, 1),
    # X2's load caps move right of the crystal: at 5.5 they sat exactly in the
    # only crossing corridor between the MCU top row and the OSC chain, which
    # made OSC32_OUT's third pad unreachable without a short.
    'X2': (2.5, 11.0, 180, 1), 'C3': (6.4, 9.5, 180, 1), 'C4': (8.2, 12.5, 0, 1),
    # VBAT / VREF group
    'D1': (1.5, 14.4, 0, 1), 'C7': (5.0, 15.0, 180, 1), 'R3': (5.0, 17.0, 0, 1),
    'U4': (11.5, 26.9, 180, 1),
    # SPI flash, top right
    'U5': (13.0, 15.0, 0, 1), 'RN3': (9.65, 22.2, -90, 1),
    'R22': (11.73, 9.2, 270, 1), 'R23': (11.5, 24.3, 180, 1), 'R21': (10.1, 17.55, 90, 1),
    # USB-C + input protection, bottom left
    'U2': (-6.0, -31.0, 0, 1), 'D2': (-6.0, -25.6, -90, 1), 'F1': (-11.5, -26.0, 90, 1),
    'R9': (-11.5, -22.4, 90, 1), 'R10': (-10.0, -22.4, 90, 1), 'R11': (-8.5, -22.4, 90, 1),
    'R12': (-7.0, -22.4, 90, 1), 'R13': (-5.5, -22.4, 90, 1),
    # 3V3 regulator + bulk caps + power LED
    'U3': (5.5, -23.8, -90, 1), 'C19': (8.5, -23.8, 90, 1), 'C20': (11.0, -23.8, -90, 1),
    'LED1': (-2.5, -23.0, -90, 1), 'R14': (-1.0, -23.0, 180, 1),
    # debug header, bottom middle-right
    'H1': (6.0, -31.0, 0, 1),
    # MCU decoupling, bottom channel
    'C14': (-6.6, -11.0, 0, 1),
    'C15': (-4.4, -11.0, -90, 1), 'C16': (-2.2, -11.0, 180, 1), 'C17': (0.0, -11.0, 180, 1),
    'C18': (2.2, -11.0, 0, 1), 'C22': (4.4, -11.0, 180, 1), 'C23': (6.6, -11.0, 180, 1),
    'C21': (8.8, -11.0, 90, 1),

    'C9': (10.9, -11.5, 0, 1), 'C8': (10.5, -14.6, 180, 1), 'C24': (12.6, -14.6, -90, 1),
    # back side
    'B1': (0.0, 15.0, 180, 2),
    'TP1': (-4.0, -5.0, 180, 2), 'TP2': (-1.0, -5.0, 180, 2),
    'TP3': (2.0, -5.0, 180, 2), 'TP4': (5.0, -5.0, 180, 2),
}


def pad_pts(c):
    a = math.radians(c['angle'])
    ca, sa = math.cos(a), math.sin(a)
    box = []
    for p in c['pads']:
        w = (p['w'] or 0) * MIL
        h = (p['h'] or 0) * MIL
        px, py = p['x'] * MIL, p['y'] * MIL
        # pad rectangle corners, rotated by its own padAngle then by the footprint
        pa = math.radians(p.get('angle') or 0)
        pca, psa = math.cos(pa), math.sin(pa)
        for dx in (-w / 2, w / 2):
            for dy in (-h / 2, h / 2):
                qx = px + dx * pca - dy * psa
                qy = py + dx * psa + dy * pca
                box.append((qx * ca - qy * sa, qx * sa + qy * ca))
    if not box:
        box = [(0.0, 0.0)]
    xs = [b[0] for b in box]
    ys = [b[1] for b in box]
    return min(xs), min(ys), max(xs), max(ys)


def main():
    m = model('examples/ProPrj_立创梁山派·天空星开发板_2026-11-05.epro2')
    byref = {c['ref']: c for c in m['components']}
    missing = [r for r in byref if r not in PLACE]
    if missing:
        print('NOT PLACED:', missing)
    boxes = {}
    origin = {}
    for ref, (x, y, rot, layer) in PLACE.items():
        c = dict(byref[ref])
        c['angle'] = rot
        x0, y0, x1, y1 = pad_pts(c)
        # PLACE gives the pad-bbox CENTRE; a few LCEDA footprints have their origin
        # far from the pads (H1 sits 6.8 mm off), so convert to an origin here.
        ox = x - (x0 + x1) / 2.0
        oy = y - (y0 + y1) / 2.0
        origin[ref] = (ox, oy, rot, layer)
        boxes[ref] = (ox + x0, oy + y0, ox + x1, oy + y1, layer, rot)
    M = 0.15          # courtyard margin between parts
    bad = []
    refs = sorted(boxes)
    for i in range(len(refs)):
        for j in range(i + 1, len(refs)):
            a, b = refs[i], refs[j]
            A, B = boxes[a], boxes[b]
            if A[4] != B[4]:
                continue
            ox = min(A[2], B[2]) - max(A[0], B[0])
            oy = min(A[3], B[3]) - max(A[1], B[1])
            if ox > -M and oy > -M:
                bad.append((a, b, round(ox, 2), round(oy, 2)))
    for r in refs:
        x0, y0, x1, y1 = boxes[r][:4]
        if x0 < -BW / 2 + EDGE or x1 > BW / 2 - EDGE or y0 < -BH / 2 + EDGE or y1 > BH / 2 - EDGE:
            bad.append((r, 'EDGE', round(x0, 2), round(y0, 2), round(x1, 2), round(y1, 2)))
    print('OVERLAPS/EDGE:', len(bad))
    for b in bad:
        print('  ', b)
    json.dump({k: v[:4] for k, v in boxes.items()}, open('_scratch/sky/_boxes.json', 'w'), indent=0)
    json.dump({k: v for k, v in origin.items()}, open('_scratch/sky/_origin.json', 'w'), indent=0)


if __name__ == '__main__':
    main()
