"""Hand-written routing, part 9 - the +5V pour fragment that floats.

Measured on the refilled board: the +5V F.Cu pour fills as three islands, and
the one around D2.5 (-6.67..-5.53 x, -25.60..-23.70 y) is the only one still
alone.  The rest of +5V already has a B.Cu track running at y = -25.20 from
x = -10.60 to 6.00, so the fragment needs one hole onto that track - no track
of its own.

Site: (-6.100, -25.200).  Inside the fragment on F.Cu (`_zq.py +5V F.Cu` says
IN pour) and on the B.Cu track.  Both-layer clearance at the 0.45 via is
F 0.680 / B 2.108 against the 0.375 needed (`_lane.clearance`, no zone copper
counted - the pour backs off by itself).
"""
W = 0.15

SEGS = []

VIAS = [
    ('+5V', -6.100, -25.200, 0.25, 0.45),   # fragment D2.5 -> B.Cu +5V trunk
]

def build(rt):
    for net, layer, pts in SEGS:
        rt.poly(net, layer, pts)
    for net, x, y, drill, size in VIAS:
        rt.via(net, x, y, drill, size)
