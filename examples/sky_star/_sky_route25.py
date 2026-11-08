"""Part 25 - $1N4849 : RN1.5 (-0.45,16.622) to the P2.4 pad at (19.47,21.59).

Both ends are F.Cu pads, so the long haul is taken on B.Cu between two vias;
everything above the RN1 row is a set of lanes that the measurements left open.

Measured space (route coords, net $1N4849):

  row F.Cu  y=16.00   free -2.50..3.00    the lane south of the RN1 pad row
  _vc (-2.60,16.00) F 0.622 / B 1.025 -> VIA OK
  col B.Cu  x=-2.60   free 15.50..22.50
  row B.Cu  y=22.00   free -2.00..10.30   passes the x=9.30 wall
  col B.Cu  x= 8.90   free 20.90..22.00
  row B.Cu  y=21.00   free  8.70..15.94   below the x=10.75 wall
  col B.Cu  x=15.94   free 21.00..27.00
  row B.Cu  y=25.00   free 11.52..20.00   above that wall
  col B.Cu  x=20.40   free 21.00..27.00
  _vc (20.40,21.59) F 1.640 / B 1.225 -> VIA OK
  row F.Cu  y=21.59   clear east of the P2.4 pad out to x=21.00

Why the two risers and not one: the x=10.75 barrier runs 21.50..25.26 and the
x=16.25 barrier runs 15.70..23.84, so no single horizontal lane joins the RN1
side to the P2 side.  y=22.00 gets east to x=10.30, then the run drops to
y=21.00 (below the barrier), climbs at x=15.94, and comes back east on y=25.00
above the barrier before dropping at x=20.40.

Every corner is a 45 degree chamfer of 0.15.

Nothing here is a route; it is the line that the measurements left open.
"""

W = 0.15

SEGS = [
    ('$1N4849', 'F.Cu', [(-2.600, 16.000), (-0.550, 16.000),
                         (-0.400, 16.150), (-0.400, 16.622)]),
    ('$1N4849', 'B.Cu', [(-2.600, 16.000), (-2.600, 21.850),
                         (-2.450, 22.000), (8.750, 22.000),
                         (8.900, 21.850), (8.900, 21.150),
                         (9.050, 21.000), (15.790, 21.000),
                         (15.940, 21.150), (15.940, 24.850),
                         (16.090, 25.000), (20.250, 25.000),
                         (20.400, 24.850), (20.400, 21.590)]),
    ('$1N4849', 'F.Cu', [(20.400, 21.590), (19.470, 21.590)]),
]

VIAS = [('$1N4849', -2.600, 16.000, 0.25, 0.45),
        ('$1N4849', 20.400, 21.590, 0.25, 0.45)]

def build(rt):
    for net, layer, pts in SEGS:
        rt.poly(net, layer, pts)
    for net, x, y, drill, size in VIAS:
        rt.via(net, x, y, drill, size)
