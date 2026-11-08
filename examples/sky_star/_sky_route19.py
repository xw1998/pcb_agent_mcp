"""Route part 19: $1I8749/$2N1188  TP2.1 (B.Cu pad) -> U1.21 (F.Cu pad).

Why this shape: U1.21 sits just below the top pin row, inside a pocket of +3V3
pour.  VDDA (part 18) already owns the y=5.00 east-west wall plus the x=-4.50
vertical, so the pocket can only be entered from the east across the y=5.00
wall -- impossible on F.Cu (the wall spans x -4.30..1.80).  So we cross under
the wall on B.Cu (TP2.1 is itself a B.Cu pad, no via needed at the pad), come
up through a via at (-3.00, 5.90) and finish with a short F.Cu spur into
U1.21 from the south.  A dead-end spur cannot enclose pour, so no new +3V3
island is created.

Measured free runs (0.15 track, clearance 0.225):
  B col x=-1.00 : -5.20..5.20   (full)
  B row y=5.10  : -3.70..-0.80
  B col x=-3.00 :  4.80..6.20
  F col x=-3.00 :  5.80..6.52
  F row y=6.35  : -4.20..-2.80
  F col x=-4.00 :  5.30..9.00
Via (-3.00,5.90): F 0.825 / B 0.868  (>= 0.45)
"""

W = 0.15

SEGS = [
    # B.Cu: from TP2.1 north, west under the y=5.00 VDDA wall, up to the via
    ('$1I8749/$2N1188', 'B.Cu', [(-1.000, -5.000), (-1.000, 4.900),
                                 (-1.200, 5.100), (-2.800, 5.100),
                                 (-3.000, 5.300), (-3.000, 5.900)]),
    # F.Cu: short spur into U1.21 from the south (never crosses VDDA)
    ('$1I8749/$2N1188', 'F.Cu', [(-3.000, 5.900), (-3.000, 6.150),
                                 (-3.200, 6.350), (-3.800, 6.350),
                                 (-4.000, 6.550), (-4.000, 7.550)]),
]

VIAS = [
    ('$1I8749/$2N1188', -3.000, 5.900, 0.25, 0.45),
]


def build(rt):
    for net, layer, pts in SEGS:
        rt.poly(net, layer, pts)
    for net, x, y, drill, size in VIAS:
        rt.via(net, x, y, drill, size)
