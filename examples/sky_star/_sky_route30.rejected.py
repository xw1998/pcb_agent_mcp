"""Part 30 - REJECTED. AGND west group's B.Cu tail (-1.882,-20.800) to the
C10.1/C11.1 pads at (6.000..7.200,-19.533).

The B.Cu lane is there: row y=-20.800 is clear from x=-2.0 to x=8.0, and a via
at (6.800,-20.350) is clean on both layers (F 0.651 / B 0.725).  What is not
there is any way onto F.Cu.  The +3V3 fill threads a bridge at file y=125.2
(route y=-20.2) that runs unbroken from file x=153.71 east past 161.42, and
directly under it the GND fill closes the only alternative: the free gap
between GND and +3V3 at file y 124.8..125.4 is 0.21 mm wide at every row,
against the 0.45 mm a 0.15 trace with 0.15 clearance needs.

Measured (file coords, x window 150..158):

  y=124.80  +3V3 [153.77,154.03] [156.21,..]   GND [152.76,153.56]  gap 0.21
  y=125.00  +3V3 [153.72,154.15] [154.85,155.35] [156.05,..]
            GND [152.57,153.51]                                  gap 0.21
  y=125.20  +3V3 [153.71,.. continuous ..]     GND [152.56,153.50]  gap 0.21
  y=125.40  +3V3 [153.73,.. continuous ..]     GND [153.05,153.43]  gap 0.30

Tried and measured as a first pass: F.Cu riser at route x=6.800 (file 155.3),
0.82 mm long, via at (6.800,-20.350).  DRC after refill: the riser cut the
+3V3 bridge and the +3V3 island count went 7 -> 8; the B.Cu lane across the
solid GND plane took the GND island count 62 -> 63 (the stitcher had to move
its via at route (3.235,-20.511) out to (3.135,-20.311)); unconnected went
47 -> 48 even though AGND itself dropped 4 -> 3.

The B.Cu side cannot be used to dodge the bridge either: the B.Cu wall at
y -18.8..-19.5 runs from x=1.5 to x=18.5, and B.Cu above y=-19.9 is blocked
at every column from x=5.6 to x=8.4, so no via can be landed north of the
bridge to skip it.

Nothing here is a route; it is the line that the measurements closed.
"""
