"""$1N4401 : U1.69 (3.00,-7.55) -> H1.8 (3.46,-29.73), 22.18 mm apart.

Measured corridors (route coords, `_runs.py` / `_vc.py`):
  F col x=3.00  : pad bottom -8.35 .. -8.80 free (row -8.60 F open 2.40..4.70)
  B row y=-9.00 : 2.40 .. 8.90 free
  via (8.00,-9.00)  F 0.716 / B 1.125  -> OK
  F row y=-9.20 : 7.50 .. 15.80 free
  F col x=15.50 : -10.65 .. -9.05 free
  via (15.50,-10.40) F 0.515 / B 0.515 -> OK
  B row y=-10.40: 11.60 .. 21.00 free
  B col x=20.50 : -30.00 .. -10.00 free (right-edge highway)
  B row y=-29.73: 2.40 .. 21.60 free (bottom highway)
  H1.8 pad: SMD rect 2.0 x 1.8 on F.Cu, x 2.46..4.46, y -30.63..-28.83
            -> via-in-pad at its centre lands on B.Cu
All corners are explicit 45-degree chamfers.
"""
W = 0.15
SEGS = [
    # 1) out of U1.69 straight down, then via to B.Cu
    ('$1N4401', 'F.Cu', [(3.000, -7.550), (3.000, -8.800)]),
    # 2) B.Cu: down-east, run east under the QFP bottom row
    ('$1N4401', 'B.Cu', [(3.000, -8.800), (3.200, -9.000), (7.800, -9.000)]),
    # 3) F.Cu: east on y=-9.20 then 45 down at x=15.50 to the -10.40 lane.
    #    An earlier version dropped at x=12.00 and fenced off a +3V3 island
    #    (bbox 156.35..160.22 / 114.28..116.86) - keep the drop east of x=15
    ('$1N4401', 'F.Cu', [(8.000, -9.000), (8.200, -9.200), (15.300, -9.200),
                         (15.500, -9.400), (15.500, -10.400)]),
    # 4) B.Cu: east to the right-edge highway, south to the bottom, west to H1.8
    ('$1N4401', 'B.Cu', [(15.500, -10.400), (20.300, -10.400), (20.500, -10.600),
                         (20.500, -29.530), (20.300, -29.730), (3.460, -29.730)]),
]
VIAS = [
    ('$1N4401', 3.000, -8.800, 0.25, 0.45),
    ('$1N4401', 8.000, -9.000, 0.25, 0.45),
    ('$1N4401', 15.500, -10.400, 0.25, 0.45),
    ('$1N4401', 3.460, -29.730, 0.25, 0.45),   # via-in-pad on H1.8
]

def build(rt):
    for net, layer, pts in SEGS:
        rt.poly(net, layer, pts)
    for net, x, y, drill, size in VIAS:
        rt.via(net, x, y, drill, size)
