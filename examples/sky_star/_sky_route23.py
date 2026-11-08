"""Part 23 - $1N4415 : H1.9 (0.92,-32.27) to the C25.2 stub at (-6.00,-18.07).

Measured space (route coords, B.Cu unless said otherwise):

  F col x=0.92    free -34.00..-30.86   the short F hop out of the H1.9 pad
  B col x=0.92    free -34.00..-25.50   long straight north out of the via
  row y=-26.70    free -13.50..+1.50    the east/west lane south of everything
  B col x=-12.50  free -27.00..-19.66   the only continuous north/south lane
                                        west of the +5V wall
  row y=-20.00    free -13.00..-5.50    east lane towards the C25 pocket
  B col x=-8.20   free -20.34..-17.00   short north spur off that lane
  row y=-18.60    free -8.60..-5.52     the last east lane into the pocket
  B col x=-6.00   free -20.50..-16.00   (not used north of -18.6)

Two constraints decided the shape, both about GND stitch sites (0.50 rim
required, so a route segment must stay >= 0.575 away from a candidate):

  * (-6.075,-26.094) sits on the natural y=-26.00 lane; the lane was moved
    down to y=-26.70 (0.606 off) so the island keeps its via.
  * (-7.565,-20.249) and (-6.365,-19.458) straddle any lane through
    x -7.6..-6.3 between y=-20.82 and y=-18.88.  The east lane therefore ends
    at x=-8.50 (0.68 diagonal off the first site) and the last stretch runs
    at y=-18.60 (0.858 off the second site).

Why the west lane is at x=-12.50:
  `+5V` holds y=-25.20 from -10.60 to +6.00 with a via at (-10.60,-25.20), and
  `$1I7801/$1N962` ends at a via at (-11.50,-23.55); both close the floor
  between -11.5 and -10.6.  x=-12.50 clears both.

Where the second via goes:
  The C25.2 stub already covers x=-6.00 from y=-18.07 down to y=-18.67, so the
  via at (-6.00,-18.20) lands on existing net copper and needs no F segment.
  _vc: F 0.525 / B 2.425 -> OK.

Nothing here is a route; it is the line that the measurements left open.
"""

W = 0.15

SEGS = [
    ('$1N4415', 'F.Cu', [(0.920, -32.270), (0.920, -31.600)]),
    ('$1N4415', 'B.Cu', [(0.920, -31.600), (0.920, -27.000),
                         (0.620, -26.700), (-12.200, -26.700),
                         (-12.500, -26.400), (-12.500, -20.300),
                         (-12.200, -20.000), (-8.500, -20.000),
                         (-8.200, -19.700), (-8.200, -18.900),
                         (-7.900, -18.600), (-6.400, -18.600),
                         (-6.000, -18.200)]),
]

VIAS = [
    ('$1N4415', 0.920, -31.600, 0.25, 0.45),
    ('$1N4415', -6.000, -18.200, 0.25, 0.45),
]

def build(rt):
    for net, layer, pts in SEGS:
        rt.poly(net, layer, pts)
    for net, x, y, drill, size in VIAS:
        rt.via(net, x, y, drill, size)
