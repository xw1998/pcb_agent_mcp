"""Hand-written routing, part 7: the analog row under the MCU.

The Vref / VDDA / AGND parts (R1 R2 R4 R5 R6 C5 C6 C10 C11) sit in two 1.2 mm
pitches rows at y = -20.267 (top) and y = -21.133 (bottom).  Each net owns a
short horizontal run straight through its own pads; the long haul up to the
MCU pins is a separate job (see below).

No autorouter: every coordinate is a part pin, measured off _nets.json.
"""
W = 0.15

SEGS = [
    # ---- $1I8749/$2N1188 : R1.1 R2.1 R4.1 C5.1 C6.2 (bottom row) ---------
    ('$1I8749/$2N1188', 'F.Cu', [(-6.00, -21.133), (-1.20, -21.133)]),
    # ---- $1I8749/$2N1197 : R5.2 R6.2 ------------------------------------
    ('$1I8749/$2N1197', 'F.Cu', [(1.20, -20.267), (2.40, -20.267)]),
    # ---- AGND : C5.2 C6.1 / C10.1 C11.1 ---------------------------------
    ('AGND', 'F.Cu', [(-2.40, -20.267), (-1.20, -20.267)]),
    ('AGND', 'F.Cu', [(6.00, -19.533), (7.20, -19.533)]),
    # ---- VDDA : C10.2 C11.2 --------------------------------------------
    ('$1I8749/VDDA', 'F.Cu', [(6.00, -18.667), (7.20, -18.667)]),
]


def build(rt):
    for net, layer, pts in SEGS:
        rt.poly(net, layer, pts)
