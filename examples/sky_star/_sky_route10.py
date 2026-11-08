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

Second leg (RN2.8 -> P2.3 at (16.93,21.59)) is not drawn yet: every horizontal
lane between y 17 and 25 in x 7..17 measures under 4 mm.

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
]

VIAS = []

def build(rt):
    for net, layer, pts in SEGS:
        rt.poly(net, layer, pts)
    for net, x, y, drill, size in VIAS:
        rt.via(net, x, y, drill, size)
