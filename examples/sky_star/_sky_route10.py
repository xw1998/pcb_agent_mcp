"""Hand-written routing, part 10 - the first of the RN2 nets, U1.65 -> RN2.8.

The $1N48xx / $1N49xx family is 15 nets that all start on a U1 pin, pass through
a resistor-network pin and end on a header pin.  None of them has any copper, so
each one is two long legs.  This file does the first leg of $1N4863, and the
lanes were measured before anything was drawn:

  y = -6.500  open 0.50 .. 10.00   (x 0.5..6.5 is under the QFP body - no copper
                                    there, the pads are only on the four edges)
  x =  9.900  open -6.00 .. 16.60  - the one full-height column on the right
  y = 13.000  open 6.00 .. 10.00
  x =  6.450  open 13.00 .. 16.35  (6.3..6.6 only; it stops at RN2.8's pad edge
                                    16.37, which is the point)

Second leg (RN2.8 -> P2.3 at (16.93,21.59)) goes over to B.Cu, because every F.Cu
lane between y 17 and 25 in x 7..17 measures under 4 mm while the back side is
wide open once you are east of x 18.75.  Measured before drawing:

  F.Cu  x = 9.900 open 13.00 .. 16.00 straight up from the existing vertical
  B.Cu  y = 15.000 open 9.50 .. 20.25  (the y 16.5 back-side wall stops at x 18.5)
  B.Cu  x = 19.200 open 14.50 .. 22.00
  B.Cu  y = 21.590 open, and P2.3's own land is 1.8 x 1.8 so a 0.45 via sits
        inside it (via-in-pad; P2 is an SMD oval header, no drill to fight).

Not done here either, kept for the record: the C7.2 GND island cannot be bridged
from (2.4,15.8) to (3.6,15.8) because $1N4752's own B.Cu track sits as a wall at
x = 2.9 from y 8.78 up to its pad at 16.51 - i.e. the island can only be reached
round the top of that pad, and there is no room there for a 0.15 track.
"""
W = 0.15

SEGS = [
    ('$1N4863', 'F.Cu', [(1.000, -7.100), (1.000, -6.500)]),
    ('$1N4863', 'F.Cu', [(1.000, -6.500), (9.900, -6.500)]),
    ('$1N4863', 'F.Cu', [(9.900, -6.500), (9.900, 13.000)]),
    ('$1N4863', 'F.Cu', [(9.900, 13.000), (6.450, 13.000)]),
    ('$1N4863', 'F.Cu', [(6.450, 13.000), (6.450, 16.400)]),
    # second leg: up the same x = 9.9 column, then over to B.Cu
    ('$1N4863', 'F.Cu', [(9.900, 13.000), (9.900, 15.000)]),
    ('$1N4863', 'B.Cu', [(9.900, 15.000), (18.700, 15.000)]),
    ('$1N4863', 'B.Cu', [(18.700, 15.000), (19.200, 15.500)]),
    ('$1N4863', 'B.Cu', [(19.200, 15.500), (19.200, 21.090)]),
    ('$1N4863', 'B.Cu', [(19.200, 21.090), (18.700, 21.590)]),
    ('$1N4863', 'B.Cu', [(18.700, 21.590), (16.930, 21.590)]),
]

VIAS = [
    ('$1N4863', 9.900, 15.000, 0.25, 0.45),
    ('$1N4863', 16.930, 21.590, 0.25, 0.45),
]

def build(rt):
    for net, layer, pts in SEGS:
        rt.poly(net, layer, pts)
    for net, x, y, drill, size in VIAS:
        rt.via(net, x, y, drill, size)
