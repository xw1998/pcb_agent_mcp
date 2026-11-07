"""Hand-written routing, part 3: the TF-card group (SDIO + detect + clock).

Layout decision that makes this group routable by hand at all: the seven ESD
diodes are placed in the SAME left-to-right order as the card socket's pads
(D2, D3, CMD, N1161, D0, D1, CD).  The reference layout had them reversed,
which turned the socket fan-out into a seven-net channel inversion; matching
the order makes every net a nested two-bend escape, so nothing crosses.

Geometry (canonical mm, Y up)
  socket pads   y 22.35..23.95, signal centres -2.25 .. 6.55 at 1.1 pitch
  diode points  y 20.825, 2.0 pitch, centred on the socket (x = -3.85 .. 8.15)
  pull-ups      y 19.00, pad 1 exactly on the net's escape column (point+0.65)
  RN row        y 17.05 (pads 1..n at 17.478), 0.5 pitch
  the escape column from the diode point down to the pull-up is the only
  vertical corridor: the diode's own GND pad sits directly under the point.

Two nets are long jumps and take B.Cu:
  * $1N1161 : diode point -> R21.2 at x 10.1 (y 18.833), on B.Cu at y 20.825
  * SDIO_CLK: RN1.4 (x -0.45) -> R21.1 at x 10.1 (y 17.967), B.Cu at y 17.85
"""
W = 0.2

SEGS = [
    # ---- _SDIO_D2 : CARD.1 - D5.1 - R18.1 - RN1.1 -------------------------
    ('$1I7895/_SDIO_D2', 'F.Cu', [(-2.25, 23.15), (-2.25, 22.425), (-3.85, 20.825)], 1.20),
    ('$1I7895/_SDIO_D2', 'F.Cu', [(-3.85, 20.825), (-3.20, 20.825), (-3.20, 19.222)], 1.20),
    ('$1I7895/_SDIO_D2', 'F.Cu', [(-3.20, 19.222), (-1.95, 17.472)], 1.20),
    # ---- _SDIO_D3 : CARD.2 - D4.1 - R19.1 - RN1.2 -------------------------
    ('$1I7895/_SDIO_D3', 'F.Cu', [(-1.15, 23.15), (-1.15, 21.525), (-1.85, 20.825)], 1.20),
    ('$1I7895/_SDIO_D3', 'F.Cu', [(-1.85, 20.825), (-1.20, 20.825), (-1.20, 19.222)], 1.20),
    ('$1I7895/_SDIO_D3', 'F.Cu', [(-1.20, 19.222), (-1.45, 18.972), (-1.45, 17.472)], 1.20),
    # ---- _SDIO_CMD : CARD.3 - D8.1 - R15.1 - RN1.3 ------------------------
    ('$1I7895/_SDIO_CMD', 'F.Cu', [(-0.05, 23.15), (-0.05, 21.025), (0.15, 20.825)], 1.20),
    ('$1I7895/_SDIO_CMD', 'F.Cu', [(0.15, 20.825), (0.80, 20.825), (0.80, 18.60)], 1.20),
    ('$1I7895/_SDIO_CMD', 'F.Cu', [(0.80, 18.60), (0.30, 18.10), (-0.95, 18.10), (-0.95, 17.477)], 1.20),
    # ---- $1N1161 : CARD.5 - D9.1 - R21.2 ----------------------------------
    ('$1I7895/$1N1161', 'F.Cu', [(2.15, 23.15), (2.15, 20.825)], 1.20),
    ('$1I7895/$1N1161', 'F.Cu', [(2.15, 20.825), (2.80, 20.825)], 1.20),
    ('$1I7895/$1N1161', 'B.Cu', [(2.80, 20.825), (9.30, 20.825), (9.30, 17.983)], 1.20),
    ('$1I7895/$1N1161', 'F.Cu', [(9.30, 17.983), (9.98, 17.983), (10.10, 17.983)], 1.20),
    # ---- _SDIO_D0 : CARD.7 - D7.1 - R16.1 - RN2.1 -------------------------
    ('$1I7895/_SDIO_D0', 'F.Cu', [(4.35, 23.15), (4.35, 21.025), (4.15, 20.825)], 1.20),
    ('$1I7895/_SDIO_D0', 'F.Cu', [(4.15, 20.825), (4.80, 20.825), (4.80, 19.222)], 1.20),
    ('$1I7895/_SDIO_D0', 'F.Cu', [(4.80, 19.222), (6.55, 17.472)], 1.20),
    # ---- _SDIO_D1 : CARD.8 - D6.1 - R17.1 - RN2.2 -------------------------
    ('$1I7895/_SDIO_D1', 'F.Cu', [(5.45, 23.15), (5.45, 21.525), (6.15, 20.825)], 1.20),
    ('$1I7895/_SDIO_D1', 'F.Cu', [(6.15, 20.825), (6.80, 20.825), (6.80, 19.222)], 1.20),
    ('$1I7895/_SDIO_D1', 'F.Cu', [(6.80, 19.222), (7.05, 18.972), (7.05, 17.472)], 1.20),
    # ---- _SD_CARD_DET : CARD.9 - D3.1 - R20.1 - RN2.3 ---------------------
    ('$1I7895/_SD_CARD_DET', 'F.Cu', [(6.55, 23.15), (6.55, 22.425), (8.15, 20.825)], 1.20),
    ('$1I7895/_SD_CARD_DET', 'F.Cu', [(8.15, 20.825), (8.80, 20.825)], 1.20),
    # the diagonal has to clear RN2's unused pad 4 (x 7.90..8.20), which sits
    # 0.25 mm to the right of pad 3, so it turns down early and drops vertically.
    ('$1I7895/_SD_CARD_DET', 'F.Cu', [(8.80, 20.825), (8.80, 19.05)], 1.20),
    ('$1I7895/_SD_CARD_DET', 'F.Cu', [(8.80, 19.05), (7.55, 17.80)], 1.20),
    ('$1I7895/_SD_CARD_DET', 'F.Cu', [(7.55, 17.80), (7.55, 17.477)], 1.20),
    # ---- _SDIO_CLK : RN1.4 - R21.1 ---------------------------------------
    ('$1I7895/_SDIO_CLK', 'F.Cu', [(-0.45, 17.477), (0.10, 17.477)], 1.20),
    ('$1I7895/_SDIO_CLK', 'B.Cu', [(0.10, 17.477), (9.30, 17.477), (9.30, 17.117)], 1.20),
    ('$1I7895/_SDIO_CLK', 'F.Cu', [(9.30, 17.117), (9.98, 17.117), (10.10, 17.117)], 1.20),
]

VIAS = [
    ('$1I7895/$1N1161', 2.80, 20.825),
    ('$1I7895/$1N1161', 9.30, 17.983),
    ('$1I7895/_SDIO_CLK', 0.10, 17.477),
    ('$1I7895/_SDIO_CLK', 9.30, 17.117),
]


def build(rt):
    for net, layer, pts, bev in SEGS:
        rt.poly(net, layer, pts, bevel=bev)
    for net, x, y in VIAS:
        rt.via(net, x, y)
