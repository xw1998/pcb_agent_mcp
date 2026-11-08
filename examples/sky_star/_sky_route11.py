"""Hand-written routing, part 11 - the short D1.2 <-> B1.1 hop of $1N9171.

$1N9171 has three pieces: D1.2 at (2.60,15.35), B1.1 (a B.Cu land, 3.4 x 2.3,
x 5.70..9.10 / y 13.85..16.15) and P2.26 far away at (19.47,-6.35).  This file
closes the first gap, 2.58 mm, which is the cheapest edge on the board.

Measured before drawing (all numbers from _lane.clearance, need = 0.15 + 0.075):

  F.Cu y 15.70  open 2.95 .. 5.50   (the floor is C7.1, a 0.566 x 0.54 pad at
                                     (5.433,15.00) whose top edge is 15.27 - so
                                     y 15.5 leaves only 0.230, hence 15.70)
  B.Cu (5.45,15.70) free 0.735      (hole/annular: 0.45 / 0.25, netclass minimum)
  B.Cu (5.45,15.70) -> (5.85,16.10) is a 45 degree stub that lands inside B1.1

C7.1 keeps the via 0.43 away, which is why the stub still works at 15.70.
"""
W = 0.15

SEGS = [
    ('$1N9171', 'F.Cu', [(2.6013, 15.350), (2.9513, 15.700)]),
    ('$1N9171', 'F.Cu', [(2.9513, 15.700), (5.450, 15.700)]),
    ('$1N9171', 'B.Cu', [(5.450, 15.700), (5.850, 16.100)]),
]

VIAS = [
    ('$1N9171', 5.450, 15.700, 0.25, 0.45),
]


def build(rt):
    for net, layer, pts in SEGS:
        rt.poly(net, layer, pts)
    for net, x, y, drill, size in VIAS:
        rt.via(net, x, y, drill, size)
