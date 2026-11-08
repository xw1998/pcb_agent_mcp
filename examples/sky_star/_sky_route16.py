"""AGND : TP4.1 (5.00,-5.00, B.Cu test pad) -> C21.1 (8.50..9.20 / -12.10..-11.30 pad, F.Cu).

Measured corridors (route coords, `_runs.py` / `_vc.py` / `_vgrid.py`):
  B row y=-5.00 : 4.60 .. 8.60 free
  B col x=8.30  : -7.60 .. -4.55 free      (this lane jumps the -6.0 pinch at x=8.5)
  B row y=-6.60 : 7.80 .. 8.90 free
  B col x=8.50  : -12.00 .. -6.45 free
  via grid around C21.1: only x 8.5..8.6 / y -12.3..-11.2 satisfy the 0.45
      via clearance on BOTH layers -> via-in-pad on C21.1 at (8.50,-11.70)
All corners are explicit 45-degree chamfers.
"""
W = 0.15
SEGS = [
    ('AGND', 'B.Cu', [(5.000, -5.000), (8.100, -5.000), (8.300, -5.200),
                      (8.300, -6.600), (8.500, -6.800), (8.500, -11.700)]),
]
VIAS = [
    ('AGND', 8.500, -11.700, 0.25, 0.45),   # via-in-pad on C21.1
]

def build(rt):
    for net, layer, pts in SEGS:
        rt.poly(net, layer, pts)
    for net, x, y, drill, size in VIAS:
        rt.via(net, x, y, drill, size)
