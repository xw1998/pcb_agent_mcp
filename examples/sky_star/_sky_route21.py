"""sky_star part 21 - $1N4919  P1.10 -> U1.32.

Measured (route coords, `_runs.py`, clearance 0.15 / w 0.15):
  F col x=-16.90   free 13.00..15.20        (up out of P1.10)
  via (-16.000,15.000)  F 0.611 / B 0.520   OK
  B row y=15.00    free -16.40..-9.40       (east highway, dead-ends on B1.2 pad)
  B col x=-9.60    free 14.80..18.40        (north lift)
  B row y=17.00    free -10.00..-5.80       (east over the B1.2 pad)
  B col x=-5.80    free  9.20..18.00        (only lane between B1.2 pad -6.10
                                             and the PA1 trunk at x=-5.50)
  B row y= 9.20    free -7.00..-6.00        (west step out of that lane)
  B col x=-6.20    free  1.00..13.60        (long south run)
  via (-6.200,3.000)    F 0.652 / B 2.125   OK
  F row y= 3.00    free -7.00..-5.00        (into U1.32 pad, east edge -6.75)

Every corner carries an explicit 45 deg chamfer.
"""
W = 0.15

SEGS = [
    ('$1N4919', 'F.Cu', [(-16.930, 13.970), (-16.930, 14.070),
                         (-16.000, 15.000)]),
    ('$1N4919', 'B.Cu', [(-16.000, 15.000), (-9.900, 15.000),
                         (-9.600, 15.300), (-9.600, 16.700),
                         (-9.300, 17.000), (-6.100, 17.000),
                         (-5.800, 16.700), (-5.800, 9.600),
                         (-6.200, 9.200), (-6.200, 3.000)]),
    ('$1N4919', 'F.Cu', [(-6.200, 3.000), (-6.850, 3.000)]),
]

VIAS = [
    ('$1N4919', -16.000, 15.000, 0.25, 0.45),
    ('$1N4919', -6.200, 3.000, 0.25, 0.45),
]


def build(rt):
    for net, layer, pts in SEGS:
        rt.poly(net, layer, pts)
    for net, x, y, drill, size in VIAS:
        rt.via(net, x, y, drill, size)
