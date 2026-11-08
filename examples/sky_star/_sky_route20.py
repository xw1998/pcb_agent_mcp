"""Route part 20: $1I8749/VDDA  L1.2 (F.Cu pad) -> the existing VDDA trunk.

Why this shape: VDDA already owns a trunk that ends at U1.22 (-4.50,7.55); the
only piece still floating is L1.2 at (-8.20,15.50).  West of x=-4.5 the board
has a clean north-south corridor on F.Cu and a single unobstructed east-west
lane at y=6.50, so the join is one L-shaped run with two 45 deg chamfers; no
via, no layer change, no new copper enclosure (a dead-end run cannot split pour).

The lane sits at y=6.50, NOT at y=6.40: $1I8749/$2N1188 (part 19) climbs to
U1.21 along x=-4.00 from y=6.10 to 7.55, which blocks the corridor east of
x=-4.30; the run stops at x=-4.65 and drops 45 deg onto the VDDA vertical at
x=-4.50 so it never comes near that spur.

Measured free runs (0.15 track, clearance 0.225, route coords):
  F col x=-8.20 :  6.40..17.00   (10.6 mm, whole descent)
  F row y= 6.50 : -9.10..-4.30   ( 4.8 mm, single clear lane)
  F row y= 6.45 : -9.10..-4.30   ( 4.8 mm)
  F col x=-6.00 : -6.00.. 6.40
  the y=6.50 lane ends at -4.30 exactly because of part 19's x=-4.00 spur
  (pads at (-8.20,15.50) L1.2 and the trunk node (-4.50,6.35) belong to VDDA)
"""

W = 0.15

SEGS = [
    ('$1I8749/VDDA', 'F.Cu', [(-8.200, 15.500), (-8.200, 6.650),
                              (-8.050, 6.500), (-4.650, 6.500),
                              (-4.500, 6.350)]),
]

VIAS = []

def build(rt):
    for net, layer, pts in SEGS:
        rt.poly(net, layer, pts)
    for net, x, y, drill, size in VIAS:
        rt.via(net, x, y, drill, size)
