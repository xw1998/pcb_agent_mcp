"""Part 13: $1N4905  U1.30 (-7.55,4.00) -> P1.8 (通孔, -16.93,15.64)

Measured facts (route coords, y up):
  F.Cu row y=4.0   open -9.80..-8.00          (U1.30 own pad excluded)
  F.Cu col x=-9.2  open 4.00..5.80
  B.Cu col x=-9.2  open 5.20..7.40 (need 0.45)

  PA3 on F.Cu = closed reverse-L wall:
      diagonal (-9.05,6.06)->(-9.68,6.81)
      vertical x=-9.70 y 7.01..19.03
      horizontal y=19.05 x -9.7166..-16.93  (end sits right above P1.8)
    B.Cu has PC4 vertical x=-9.70 only y 2.92..12.77.
  => cross the x=-9.70 line on B.Cu above y=12.77+0.3.

  B.Cu col x=-9.15 open 5.00..13.60
  B.Cu col x=-9.30 open 5.00..13.70
  B.Cu row y=14.0  open -10.00..-9.40
  B.Cu row y=14.2  open -10.40..-9.40
  B.Cu row y=14.4  open -16.90..-9.40
  B.Cu row y=14.6  open -16.90..-9.40
  P1.9 pad (x=-16.93,y=13.10) top edge y=14.00 -> y=14.40 gives 0.40 clearance
  P1.8 is through-hole -> B.Cu may terminate directly in the pad.
"""
W = 0.15
SEGS = [
    ('$1N4905', 'F.Cu', [(-7.550, 4.000), (-8.600, 4.000)]),
    ('$1N4905', 'F.Cu', [(-8.600, 4.000), (-9.150, 4.550)]),
    ('$1N4905', 'F.Cu', [(-9.150, 4.550), (-9.150, 5.500)]),
    ('$1N4905', 'B.Cu', [(-9.150, 5.500), (-9.150, 13.500)]),
    ('$1N4905', 'B.Cu', [(-9.150, 13.500), (-9.750, 14.100)]),
    ('$1N4905', 'B.Cu', [(-9.750, 14.100), (-9.750, 14.400)]),
    ('$1N4905', 'B.Cu', [(-9.750, 14.400), (-16.930, 14.400), (-16.930, 16.510)]),
]
VIAS = [
    ('$1N4905', -9.150, 5.500, 0.25, 0.45),
    # via-in-pad: P1.8 is an SMD oval on F.Cu only, the B.Cu run must land in it
    ('$1N4905', -16.930, 16.510, 0.25, 0.45),
]


def build(rt):
    for net, layer, pts in SEGS:
        rt.poly(net, layer, pts)
    for net, x, y, drill, size in VIAS:
        rt.via(net, x, y, drill, size)
