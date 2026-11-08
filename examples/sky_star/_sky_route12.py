"""Hand-written routing, part 12 - $1N4891, RN2.6 -> P2.1 (gap 10.55 mm).

$1N4891 has three pieces: U1.84 (already joined to RN2.6) and P2.1, an SMD pad
at (16.93,24.13) whose left edge reaches x 16.14.  10.55 mm apart in a straight
line, but F.Cu on that side of the board is a picket fence (every lane measured
shorter than 3.2 mm between y 17 and 24), so the run goes to B.Cu.

Measured before drawing (all numbers from _lane.clearance, need = 0.15 + 0.075,
via need 0.45):

  F.Cu col x 7.55  open 14.00 .. 17.00    (runs straight through RN2.6's pad)
  F.Cu row y 16.00 open  6.80 .. 11.40
  both layers at (9.925,16.00): 0.45 clear  (F 8.00..11.40, B 9.40..18.90)
  B.Cu  row y 16.00 open  9.40 .. 18.90
  B.Cu  col x 18.75 open 15.50 .. 21.10   (0.30 from $1N4863 at x 19.20 and
                                           0.50 from $1N4752's via at 18.25)
  B.Cu  row y 17.10 open  9.80 .. 18.60
  B.Cu  col x 16.25 open 16.90 .. 24.30   (P2's pads are SMD, so B.Cu is free
                                           under them; the leg passes x 16.25,
                                           clear of $1N4863's via at 16.93)
  B.Cu  (16.93,24.13) is P2.1's own centre: via-in-pad, which is what part 10b
        already does for P2.3.

The wall at y 16.51 is $1N4752 (track 4.10..18.25 ending in a via at 18.25);
x 18.75 gets past its end while staying 0.30 off $1N4863's own column.
"""
W = 0.15

SEGS = [
    # F.Cu: down out of RN2.6, then east along y 16.00
    ('$1N4891', 'F.Cu', [(7.5499, 16.6225), (7.5499, 16.075)]),
    ('$1N4891', 'F.Cu', [(7.5499, 16.075), (7.625, 16.000)]),
    ('$1N4891', 'F.Cu', [(7.625, 16.000), (9.925, 16.000)]),
    # B.Cu: past $1N4752's end, up, back west, then north to P2.1
    ('$1N4891', 'B.Cu', [(9.925, 16.000), (18.675, 16.000)]),
    ('$1N4891', 'B.Cu', [(18.675, 16.000), (18.750, 16.075)]),
    ('$1N4891', 'B.Cu', [(18.750, 16.075), (18.750, 17.025)]),
    ('$1N4891', 'B.Cu', [(18.750, 17.025), (18.675, 17.100)]),
    ('$1N4891', 'B.Cu', [(18.675, 17.100), (16.325, 17.100)]),
    ('$1N4891', 'B.Cu', [(16.325, 17.100), (16.250, 17.175)]),
    ('$1N4891', 'B.Cu', [(16.250, 17.175), (16.250, 23.450)]),
    ('$1N4891', 'B.Cu', [(16.250, 23.450), (16.930, 24.130)]),
]

VIAS = [
    ('$1N4891', 9.925, 16.000, 0.25, 0.45),
    ('$1N4891', 16.930, 24.130, 0.25, 0.45),
]

def build(rt):
    for net, layer, pts in SEGS:
        rt.poly(net, layer, pts)
    for net, x, y, drill, size in VIAS:
        rt.via(net, x, y, drill, size)
