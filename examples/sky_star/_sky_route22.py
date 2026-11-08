"""sky_star part 22 - AGND  R8.1 (-7.20,-21.133) <-> C5.2 (-2.40,-20.267).

Measured (route coords, `_runs.py`):
  F row y=-21.13   free -8.90..-6.50      (east into the R8.1 pad)
  F row y=-20.55   free -9.10..-8.30      (both layers open window)
  B row y=-20.80   free -8.80.. 4.00      (the only unbroken B lane; the
                                           band y -21.6..-20.9 is walled
                                           on B everywhere east of -10)
  note: the lane is held at y=-20.80, not -20.55, to leave 0.45 of room for
  the GND stitch via at (-7.565,-20.349) that _stitch_gnd.py needs there.
  via (-8.600,-20.550)  F 0.725 / B 0.575  OK
  B col x=-8.60 is walled at -21.5..-20.9, so the west via is approached
  from the east only -- which is exactly this lane.
  via (-1.000,-20.270)  F 0.454 / B 0.855  OK
  F row y=-20.27   free -2.60..-0.60      (lands on the existing
                                           AGND seg C5.2--C6.1)

Every corner carries an explicit 45 deg chamfer.
"""
W = 0.15

SEGS = [
    ('AGND', 'F.Cu', [(-8.600, -20.550), (-8.600, -20.880),
                      (-8.350, -21.130), (-7.200, -21.130)]),
    ('AGND', 'B.Cu', [(-8.600, -20.550), (-8.350, -20.800),
                      (-1.530, -20.800), (-1.000, -20.270)]),
    ('AGND', 'F.Cu', [(-1.000, -20.270), (-1.200, -20.267)]),
]

VIAS = [
    ('AGND', -8.600, -20.550, 0.25, 0.45),
    ('AGND', -1.000, -20.270, 0.25, 0.45),
]


def build(rt):
    for net, layer, pts in SEGS:
        rt.poly(net, layer, pts)
    for net, x, y, drill, size in VIAS:
        rt.via(net, x, y, drill, size)
