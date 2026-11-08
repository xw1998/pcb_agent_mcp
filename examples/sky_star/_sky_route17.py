"""AGND : C21.1 via (8.50,-11.70) -> C11.1 pad (7.20,-19.53, F.Cu only).

Measured corridors (route coords, `_runs.py` / `_map2.py` / `_vc.py`):
  B col x=8.50 : -15.60 .. -11.60 free  (the whole B.Cu west of x<=8.6 is
                 open from -14.4 to -11.6, so no need for the F-side detour
                 over the -13.2 diagonal obstacle band)
  via grid      : (8.50,-15.30) F 0.694 / B 0.647 -> VIA OK
                  (8.50,-15.00) F 0.437 -> NO ; (8.50,-14.70) F 0.260 -> NO
  F col x=8.50  : -20.20 .. -15.02 free (continuous, no jog needed)
  F row y=-19.50: 7.00 .. 11.18 free
  C11.1 pad     : F.Cu only, size 0.5657x0.54 at 90 deg ->
                  x 6.93..7.47, y -19.81..-19.25
All corners are explicit 45-degree chamfers.
"""
W = 0.15
SEGS = [
    ('AGND', 'B.Cu', [(8.500, -11.700), (8.500, -15.300)]),
    ('AGND', 'F.Cu', [(8.500, -15.300), (8.500, -19.100), (8.100, -19.500),
                      (7.300, -19.500)]),
]
VIAS = [
    ('AGND', 8.500, -15.300, 0.25, 0.45),
]

def build(rt):
    for net, layer, pts in SEGS:
        rt.poly(net, layer, pts)
    for net, x, y, drill, size in VIAS:
        rt.via(net, x, y, drill, size)
