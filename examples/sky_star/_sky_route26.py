"""Part 26 - $1N4919 : the trunk end at (-5.941,16.841) to the RN3.8 pad at (9.223,22.950).

The trunk already reaches B.Cu y=17.000 from x=-9.101 to x=-6.299; a via dropped
straight onto that segment gets us to F.Cu, which is wide open west of x=-6.0.
The long haul north is taken on F.Cu, the long haul east on B.Cu, because the
F.Cu row at y=22.50 is walled from -2.84 to 7.14 while the B.Cu row beside it
is clear from -4.70 to 9.60.

Measured space (route coords, net $1N4919):

  _vc (-6.40,17.00)  F 1.068 / B 0.825  -> VIA OK   (the via sits on the trunk)
  col F.Cu x=-6.40   free 16.90..23.00
  row F.Cu y=22.50   free -6.50..-4.00  (then walled to 7.14)
  _vc (-4.30,22.50)  F 1.383 / B 0.625  -> VIA OK
  row B.Cu y=22.50   free -4.70..9.60   (0.50 above part 25's y=22.00 lane)
  _vc ( 8.50,22.80)  F 0.511 / B 0.725  -> VIA OK
  col F.Cu x= 8.70   free 21.14..25.50
  row F.Cu y=22.95   free 8.40..9.58    (RN3.7 pad is 0.275 south of the entry)

Every corner is a 45 degree chamfer of 0.15.

Nothing here is a route; it is the line that the measurements left open.
"""

W = 0.15

SEGS = [
    ('$1N4919', 'F.Cu', [(-6.400, 17.000), (-6.400, 22.350),
                         (-6.250, 22.500), (-4.300, 22.500)]),
    ('$1N4919', 'B.Cu', [(-4.300, 22.500), (8.350, 22.500),
                         (8.500, 22.650), (8.500, 22.800)]),
    ('$1N4919', 'F.Cu', [(8.500, 22.800), (8.650, 22.950),
                         (9.223, 22.950)]),
]

VIAS = [('$1N4919', -6.400, 17.000, 0.25, 0.45),
        ('$1N4919', -4.300, 22.500, 0.25, 0.45),
        ('$1N4919',  8.500, 22.800, 0.25, 0.45)]

def build(rt):
    for net, layer, pts in SEGS:
        rt.poly(net, layer, pts)
    for net, x, y, drill, size in VIAS:
        rt.via(net, x, y, drill, size)
