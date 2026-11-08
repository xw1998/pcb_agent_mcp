"""$1I8749/VDDA : TP3.1 (2.00,-5.00, F.Cu 1.27 dia test pad) -> U1.22 (-4.50,7.55).

The whole F.Cu block between U1's pin row and the R/C row below it is empty for
any x >= -6.4 (`_map2.py -9.2 2.6 -6.0 8.2 0.2`), so this needs no layer change.

Measured corridors:
  F col x=2.00  : -5.00 .. 6.52 free
  F row y=5.00  : -5.00 .. 2.40 free
  F col x=-4.50 : 4.80 .. 7.70 free    (own pad through 7.55)
  NB: travelling west at y=6.50 instead (0.25 under U1's pads) seals a +3V3
      pour pocket around U1.11 (x 0.68..1.72, y 6.78..9.95) - use y=5.00.
  TP3.1 is a B.Cu-only test pad -> a via-in-pad at (2.00,-5.00) is mandatory
      (`_vc.py`: F 1.425 / B 2.365, both >= 0.45)
The 45-degree jog is put at x=-4.30 (clear of the neighbouring pad at -4.85).
"""
W = 0.15
SEGS = [
    ('$1I8749/VDDA', 'F.Cu', [(2.000, -5.000), (2.000, 4.800), (1.800, 5.000),
                              (-4.300, 5.000), (-4.500, 5.200), (-4.500, 7.550)]),
]
VIAS = [
    ('$1I8749/VDDA', 2.000, -5.000, 0.25, 0.45),   # via-in-pad on TP3.1 (pad is B.Cu only)
]

def build(rt):
    for net, layer, pts in SEGS:
        rt.poly(net, layer, pts)
    for net, x, y, drill, size in VIAS:
        rt.via(net, x, y, drill, size)
