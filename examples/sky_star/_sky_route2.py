"""Hand-written routing, part 2: top-left oscillators and the D1/C7/TP chain.

Every waypoint is picked by hand from the free-space map; no router involved.

Topology notes
  * X1/X2 sit in the middle of every OSC escape, so each net keeps its own
    height: 9.30 (OSC32_IN, X2.1 tapped upward), 11.90/12.10 (OSC32_OUT),
    11.50 (OSC_OUT), 12.375/13.30 (OSC_IN).
  * C3/C4 were moved right of X2 in the floorplan - at x=5.5 they sat exactly in
    the only crossing corridor, which left C4.2 unreachable without a short.
  * B.Cu behind the package is bare, so $2N1173 switches layers just above
    U1.6 and runs up at x=3.50, left of every T2 fan-out vertical.
  * The 1N4752 corridor (B.Cu, y=16.51) walls off the two halves of $2N1173,
    so the halves are joined by the F.Cu run at y=14.40 that hops over the wall.
"""
W = 0.2

SEGS = [
    # ---- OSC32_IN : U1.8 / X2.1 / C3.2 ------------------------------------
    ('$1I8749/OSC32_IN', 'F.Cu', [(2.50, 7.5499), (2.42, 8.10), (2.42, 9.90),
                                  (5.70, 9.90), (5.70, 9.55)], 1.20),
    ('$1I8749/OSC32_IN', 'F.Cu', [(3.70, 9.90), (3.70, 11.00)], 1.20),
    # ---- OSC32_OUT : U1.9 / X2.2 / C4.2 -----------------------------------
    ('$1I8749/OSC32_OUT', 'F.Cu', [(2.00, 7.5499), (2.00, 12.10), (1.40, 12.10),
                                   (1.40, 11.20)], 1.20),
    ('$1I8749/OSC32_OUT', 'F.Cu', [(2.00, 12.10), (4.60, 12.10), (4.80, 11.90),
                                   (8.60, 11.90), (8.60, 12.50)], 1.20),
    # ---- OSC_IN : U1.12 / X1.3 / C2.1 -------------------------------------
    ('$1I8749/OSC_IN', 'F.Cu', [(0.50, 7.5499), (0.40, 8.00), (0.40, 13.30),
                                (-7.375, 13.30), (-7.375, 12.50)], 1.20),
    ('$1I8749/OSC_IN', 'F.Cu', [(0.40, 12.375), (-2.40, 12.375)], 1.20),
    # ---- OSC_OUT : U1.13 / X1.1 / C1.2 ------------------------------------
    ('$1I8749/OSC_OUT', 'F.Cu', [(0.00, 7.5499), (0.00, 11.50), (-3.50, 11.50),
                                 (-3.50, 10.60), (-4.30, 10.60)], 1.20),
    ('$1I8749/OSC_OUT', 'F.Cu', [(-4.30, 10.60), (-6.60, 10.60), (-6.60, 9.60)],
     0.12),
    # ---- $2N1173 : U1.6 / D1.3 / C7.1 / TP1.1 ----------------------------
    ('$1I8749/$2N1173', 'F.Cu', [(3.40, 7.5499), (3.40, 9.35)], 1.20),
    ('$1I8749/$2N1173', 'B.Cu', [(3.40, 9.35), (3.40, 14.05), (5.25, 14.05)],
     0.12),
    ('$1I8749/$2N1173', 'F.Cu', [(5.25, 14.05), (5.43, 14.90)], 1.20),
    ('$1I8749/$2N1173', 'B.Cu', [(-4.00, -5.00), (-4.00, 14.90), (0.40, 14.90)],
     0.12),
    ('$1I8749/$2N1173', 'F.Cu', [(0.40, 14.90), (0.40, 14.40)], 1.20),
    ('$1I8749/$2N1173', 'F.Cu', [(0.40, 14.40), (5.43, 14.40), (5.43, 14.90)],
     0.12),
    # ---- $2N1176 : D1.1 / R3.2 -------------------------------------------
    ('$1I8749/$2N1176', 'F.Cu', [(2.601, 13.45), (2.601, 13.10), (6.00, 13.10),
                                 (6.00, 17.00), (5.43, 17.00)], 1.20),
]

VIAS = [
    ('$1I8749/$2N1173', 3.40, 9.35),      # above the T2 via row, down to B.Cu
    ('$1I8749/$2N1173', 5.25, 14.05),     # up to C7.1
    ('$1I8749/$2N1173', 0.40, 14.90),     # down to D1.3
]


def build(rt):
    for net, layer, pts, bev in SEGS:
        rt.poly(net, layer, pts, w=W, bevel=bev)
    for net, x, y in VIAS:
        rt.via(net, x, y)
