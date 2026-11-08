"""Part 14: GND lower-left cluster

Merges the four stray GND islands of the bottom-left corner into the main GND
piece with a single trunk along a measured clean corridor.

Pad centres (route coords, y up), read straight out of the board file:
  C14.1 (GND)  (-7.033,-11.000)  pad 0.566 x 0.540  -> x -7.316..-6.750, y -11.270..-10.730
  C15.1 (GND)  (-4.400,-10.567)  pad 0.540 x 0.566  -> x -4.670..-4.130, y -10.850..-10.280
  C16.1 (GND)  (-1.767,-11.000)  pad 0.566 x 0.540  -> x -2.050..-1.484, y -11.270..-10.730
  C17.1 (GND)  ( 0.433,-11.000)  pad 0.566 x 0.540  -> x  0.150.. 0.716, y -11.270..-10.730
  SW1.1 (GND)  (-4.925,-12.650)  pad 0.650 x 1.200  -> x -5.250..-4.600, y -13.250..-12.050
C14.1 and C17.1 are already on the main GND piece (they do not show up as a
separate piece), C15.1 / C16.1 / SW1.1 are pieces 3 / 2 / 5.

Measured corridors (F.Cu, own net excluded):
  row y=-10.400  open -7.600..0.900   (8.5 mm, one run)
  row y=-10.450  open -7.600..0.400   (one run)
  col x=-4.925   open -12.050..-10.300 entirely (1.750 mm, one run)

Trunk at y=-10.400 runs straight through C15.1's pad (pad spans y -10.850..
-10.280), so C15.1 needs no stub.  The +3V3 0402s C14.2 / C16.2 / C17.2 sit at
y -11.270..-10.730, i.e. 0.33 mm below the trunk, which still clears the 0.225
rule; the measured row confirms it.

The only pinch is via PC6 at (0.000,-9.900) size 0.45: the trunk passes 0.500 mm
below its centre => 0.200 mm copper gap, above the 0.150 mm board minimum.

Each direction change gets an explicit 45 degree chamfer.
"""
W = 0.15
SEGS = [
    # trunk: C14.1 -> through C15.1 pad -> C17.1
    ('GND', 'F.Cu', [(-7.033, -11.000), (-7.033, -10.700), (-6.733, -10.400),
                     (0.133, -10.400), (0.433, -10.700), (0.433, -11.000)]),
    # stub to C16.1, leaving the trunk at 45 degrees
    ('GND', 'F.Cu', [(-1.467, -10.400), (-1.767, -10.700), (-1.767, -11.000)]),
    # stub down to SW1.1; x=-4.925 keeps 0.255 mm from C15.2 (+3V3)
    ('GND', 'F.Cu', [(-4.625, -10.400), (-4.925, -10.700), (-4.925, -12.650)]),
]
VIAS = []


def build(rt):
    for net, layer, pts in SEGS:
        rt.poly(net, layer, pts)
    for net, x, y, drill, size in VIAS:
        rt.via(net, x, y, drill, size)
