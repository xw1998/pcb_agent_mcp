"""Hand-written routing, part 8 - GND islands tied to the B.Cu plane.

Each GND pad that still sat on its own F.Cu pour fragment gets a stitching via.
The via goes to the B.Cu GND zone (one 2295 mm2 plate), so the other side is done.

Sites were measured twice and the first answer was wrong, so the rule is written
down here: the clearance has to be asked on BOTH layers, because a via lives on
both - `_free.py via` only looked at F.Cu and 12 real shortfalls came back as
`via-track ... d<0` against B.Cu signal tracks.  A second run of `_findvia.py`
against a stale `_routes.json` produced five "legal" sites of which two were not;
every number below was re-checked with `_chk` on the current route set.

R30.2 / C14.1 / C24.1 need a stub because the pad's own free direction is short;
C7.2 takes the via inside the pad (0.566 x 0.540 pad, 0.45 via fits).
"""
W = 0.15

SEGS = [
    ('GND', 'F.Cu', [(4.800, -19.533), (4.800, -20.100)]),    # [0.72] stub
    ('GND', 'F.Cu', [(-7.033, -11.000), (-7.020, -9.400)]),   # [0.71] stub
    ('GND', 'F.Cu', [(12.600, -14.167), (12.400, -13.085)]),  # [0.60] stub
]

VIAS = [
    ('GND', 4.800, -20.100, 0.25, 0.45),
    ('GND', -7.020, -9.400, 0.25, 0.45),
    ('GND', 12.400, -13.085, 0.25, 0.45),
    ('GND', 4.567, 15.000, 0.25, 0.45),   # via in pad C7.2
]

def build(rt):
    for net, layer, pts in SEGS:
        rt.poly(net, layer, pts)
    for net, x, y, drill, size in VIAS:
        rt.via(net, x, y, drill, size)
