"""Part 24 - $1N9171 : P2.26 (19.47,-6.35) to the B1.1 pad at (7.40,15.00).

B1.1 is a B.Cu pad, so the whole run stays on B after one via at the P2 end.

Measured space (route coords):

  F col x=19.47   free -7.76..-4.94    the short F hop out of the P2.26 pad
  B col x=19.47   free -10.00..15.36   the east edge highway, 25 long
  row y=14.50     free +7.00..+20.00   one unbroken east/west lane
  B col x=7.40    free  13.86..16.20   short north spur into the B1.1 pad
  _vc (19.47,-6.60) F 1.390 / B 2.326 -> VIA OK

Why the east/west lane had to be y=14.50 and not 14.40 or 14.60:
  two via rims squeeze that band - `$1N4863` sits at (9.90,15.00) and `PE6` at
  (15.62,13.97).  A 0.15 track needs 0.45 centre clearance from each, so the
  lane has to sit inside [14.42, 14.55]; 14.50 is the only round value that
  clears both.  (At 14.40 and 14.60 _runs reports the row as broken.)

Why the lane cannot continue north-east to the x=19.47 highway:
  `$1N4863` leaves y=15.00 through a diagonal (18.70,15.00)-(19.20,15.50) and
  then runs north at x=19.20 up to 21.09, sealing that corner.  Hence the drop
  to y=14.20 on the highway side and the 45 degree step onto y=14.50.

Nothing here is a route; it is the line that the measurements left open.
"""

W = 0.15

SEGS = [
    ('$1N9171', 'F.Cu', [(19.470, -6.350), (19.470, -6.600)]),
    ('$1N9171', 'B.Cu', [(19.470, -6.600), (19.470, 14.200),
                         (19.170, 14.500), (7.700, 14.500),
                         (7.400, 14.800), (7.400, 15.000)]),
]

VIAS = [('$1N9171', 19.470, -6.600, 0.25, 0.45)]

def build(rt):
    for net, layer, pts in SEGS:
        rt.poly(net, layer, pts)
    for net, x, y, drill, size in VIAS:
        rt.via(net, x, y, drill, size)
