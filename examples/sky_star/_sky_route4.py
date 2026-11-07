"""Hand-written routing, part 4: the SPI flash group (CS / CLK / MISO / MOSI).

Geometry decisions
  * RN3 sits left of the flash, so the four series resistors line up as nested
    lanes: CS (21.45) -> MISO (22.45) -> MOSI (22.95), each lane owned by the
    flash pad column to its right, so no two lanes cross.
  * CLK is the third pad of RN3 and its partner R23 sits above the block; the
    only way out is over MISO's lane, so CLK crosses on B.Cu (one via pair).
  * CS's pull-up R22 hangs directly under U5 on the CS escape column, so the
    pull-up is a T on that column and costs no extra copper.
  * $1N1206 leaves U5 on the right (through the 15.62 gap between the flash and
    P2) and comes back over the top, above every lane it would otherwise cut.
"""
W = 0.2

SEGS = [
    # ---- _SPI_CS : U5.1 - RN3.4 - R22.1 -----------------------------------
    ('$1I7895/_SPI_CS', 'F.Cu', [(11.095, 11.47), (11.73, 12.105), (11.73, 21.45),
                                 (10.078, 21.45)], 0.12),
    ('$1I7895/_SPI_CS', 'F.Cu', [(11.73, 12.105), (11.73, 9.633)], 0.001),
    # ---- _SPI_MISO : U5.2 - RN3.2 ----------------------------------------
    ('$1I7895/_SPI_MISO', 'F.Cu', [(12.365, 11.47), (13.00, 12.105), (13.00, 22.45),
                                   (10.078, 22.45)], 0.12),
    # ---- _SPI_MOSI : U5.5 - RN3.1 ----------------------------------------
    ('$1I7895/_SPI_MOSI', 'F.Cu', [(14.905, 18.53), (14.905, 22.95),
                                   (10.078, 22.95)], 0.12),
    # ---- _SPI_CLK : RN3.3 - R23.2 ----------------------------------------
    ('$1I7895/_SPI_CLK', 'F.Cu', [(10.078, 21.95), (10.75, 21.95)], 0.001),
    ('$1I7895/_SPI_CLK', 'B.Cu', [(10.75, 21.95), (10.75, 24.98), (11.067, 24.98)], 0.12),
    ('$1I7895/_SPI_CLK', 'F.Cu', [(11.067, 24.98), (11.067, 24.30)], 0.001),
    # ---- $1N1206 : U5.6 - R23.1 ------------------------------------------
    ('$1I7895/$1N1206', 'F.Cu', [(13.635, 18.53), (13.635, 19.95)], 0.001),
    # the 19.95 crossing runs on B.Cu so it does not cut MOSI's riser
    ('$1I7895/$1N1206', 'B.Cu', [(13.635, 19.95), (15.62, 19.95)], 0.12),
    ('$1I7895/$1N1206', 'F.Cu', [(15.62, 19.95), (15.62, 23.40), (11.933, 23.40),
                                 (11.933, 24.30)], 0.12),
]

VIAS = [
    ('$1I7895/$1N1206', 13.635, 19.95),
    ('$1I7895/$1N1206', 15.62, 19.95),
    ('$1I7895/_SPI_CLK', 10.75, 21.95),
    ('$1I7895/_SPI_CLK', 11.067, 24.98),
]


def build(rt):
    for net, layer, pts, bev in SEGS:
        rt.poly(net, layer, pts, bevel=bev)
    for net, x, y in VIAS:
        rt.via(net, x, y)
