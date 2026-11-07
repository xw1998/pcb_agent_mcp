"""Hand-written routing, part 6: the USB data pair, the CC series resistors and
the 5 V input chain (header -> fuse -> VBUS -> 5 V header).

Geometry decisions
  * U2's pads interleave the two data nets (net 8196 lives on B6/A6 with net
    962's A7 wedged between them), so exactly one crossing is unavoidable; it is
    paid with a single via pair under the connector on B.Cu, which is bare below
    y = -29 here.
  * D2 is the ESD array; its two pad rows leave a 1.6 mm tunnel, and both data
    nets climb that tunnel between D2's GND / +5V pads.
  * $1N957 / $1N960 travel in the still-empty strip y = -31 .. -32.2.  R11 was
    moved from x = -8.5 to -7.85 (see _sky_plan) because the only free vertical
    corridor through U2's pad row is between A4B9 and A5; keeping R11 at -8.5
    would put its riser inside the A4B9 pad.
  * PIN_IN_+5V has to join both 20-pin headers.  The 6.5 mm mounting-hole pads
    wall off the bottom edge at |x| ~ 17.8, so the bus drops between the holes
    (x = 13.5) and runs along y = -34.9, where only U2's shield pads and H1 are
    near.  F1.1 rides the same bus through x = -12.3.
"""
W = 0.2

SEGS = [
    # ================================================== $1N8196  (USB D+) ===
    # A6 escapes below the connector, crosses on B.Cu, comes back up on B6's
    # column which is D2 pad-3's column as well.
    ('$1I7801/$1N8196', 'F.Cu', [(-6.25, -28.452), (-6.25, -29.80)]),
    ('$1I7801/$1N8196', 'B.Cu', [(-6.25, -29.80), (-5.25, -29.80)]),
    ('$1I7801/$1N8196', 'F.Cu', [(-5.25, -29.80), (-5.25, -28.452)]),
    # B6 -> D2.3 -> (tunnel) -> D2.4 -> R13.1 -> R12.1
    ('$1I7801/$1N8196', 'F.Cu', [(-5.25, -28.452), (-5.25, -24.25), (-5.25, -22.833)]),
    ('$1I7801/$1N8196', 'F.Cu', [(-5.25, -22.833), (-7.00, -22.833)]),

    # ================================================== $1N962   (USB D-) ===
    # A7 loops under B7 on F.Cu (the horizontal sits below A6's via).
    ('$1I7801/$1N962', 'F.Cu', [(-5.75, -28.452), (-5.75, -30.40),
                                (-6.75, -30.40), (-6.75, -28.452)]),
    # B7 -> D2.1 -> (tunnel) -> D2.6 -> step left under R12 -> B.Cu -> R9.1
    ('$1I7801/$1N962', 'F.Cu', [(-6.75, -28.452), (-6.95, -26.95), (-6.95, -24.25),
                                (-6.95, -23.55), (-7.75, -23.55)]),
    ('$1I7801/$1N962', 'B.Cu', [(-7.75, -23.55), (-11.50, -23.55)]),
    ('$1I7801/$1N962', 'F.Cu', [(-11.50, -23.55), (-11.50, -22.833)]),

    # ================================================== $1N957  (CC1) ======
    ('$1I7801/$1N957', 'F.Cu', [(-4.25, -28.452), (-4.25, -22.833), (-4.50, -22.833)]),

    # ================================================== $1N960  (CC2) ======
    ('$1I7801/$1N960', 'F.Cu', [(-7.25, -28.452), (-7.25, -31.80)]),
    ('$1I7801/$1N960', 'B.Cu', [(-7.25, -31.80), (-11.10, -31.80)]),
    ('$1I7801/$1N960', 'F.Cu', [(-11.10, -31.80), (-11.10, -27.50),
                                (-10.00, -27.50), (-10.00, -22.833)]),

    # ================================================== PIN_IN_+5V =========
    # P1.40 -> F1.1 -> down the left riser -> bottom bus
    ('PIN_IN_+5V', 'F.Cu', [(-16.93, -24.13), (-16.93, -26.90), (-11.50, -26.90)]),
    ('PIN_IN_+5V', 'F.Cu', [(-12.30, -26.90), (-12.30, -34.90)]),
    # P2.40 -> round the mounting hole -> down to the bus
    ('PIN_IN_+5V', 'F.Cu', [(19.47, -24.13), (19.47, -26.40), (13.50, -26.40),
                            (13.50, -34.90)]),
    # the bus itself, then every VBUS / 5 V header tap
    ('PIN_IN_+5V', 'F.Cu', [(13.50, -34.90), (-12.30, -34.90)]),
    ('PIN_IN_+5V', 'F.Cu', [(-8.40, -34.90), (-8.40, -28.452)]),   # U2.A4B9
    ('PIN_IN_+5V', 'F.Cu', [(-3.60, -34.90), (-3.60, -28.452)]),   # U2.A9B4
    ('PIN_IN_+5V', 'F.Cu', [(3.46, -34.90), (3.46, -32.27)]),      # H1.7

    # ================================================== $2N1193  (U1.73) ===
    ('$1I8749/$2N1193', 'F.Cu', [(5.00, -7.55), (5.00, -9.60), (7.70, -9.60),
                                 (7.70, -14.60), (9.80, -14.60)]),

    # ================================================== $2N1200  (U1.49) ===
    # left flank -> B.Cu lane at y = -21.2 (the fan-out lanes stop at -19.55)
    # -> right flank -> C9.  R7 taps the same lane through its own via.
    ('$1I8749/$2N1200', 'F.Cu', [(-7.55, -5.50), (-9.40, -5.50), (-9.40, -21.20)]),
    ('$1I8749/$2N1200', 'B.Cu', [(-9.40, -21.20), (13.20, -21.20)]),
    ('$1I8749/$2N1200', 'F.Cu', [(13.20, -21.20), (13.20, -11.50), (11.60, -11.50)]),
    ('$1I8749/$2N1200', 'F.Cu', [(4.033, -20.70), (6.40, -20.70), (6.40, -21.70)]),
    # +5V clinch under the regulator (its pad sits 0.1 mm above the band)
    ('+5V', 'F.Cu', [(7.00, -24.762), (7.00, -25.30)]),
    # +5V: the band is sliced by the USB risers, so the two halves are bridged
    # on the bare B.Cu behind it.
    ('+5V', 'B.Cu', [(-10.60, -25.20), (6.00, -25.20)]),
    ('+5V', 'F.Cu', [(6.00, -25.20), (6.664, -25.20)]),
    ('$1I8749/$2N1200', 'B.Cu', [(6.40, -21.70), (6.40, -21.20)]),
]

VIAS = [
    ('$1I7801/$1N8196', -6.25, -29.80),
    ('$1I7801/$1N8196', -5.25, -29.80),
    ('$1I7801/$1N962', -7.75, -23.55),
    ('$1I7801/$1N962', -11.50, -23.55),
    ('$1I7801/$1N960', -7.25, -31.80),
    ('$1I7801/$1N960', -11.10, -31.80),
    ('$1I8749/$2N1200', -9.40, -21.20),
    ('$1I8749/$2N1200', 13.20, -21.20),
    ('$1I8749/$2N1200', 6.40, -21.70),
    ('+5V', -10.60, -25.20),
    ('+5V', 6.00, -25.20),

]


def build(rt):
    for net, layer, pts in SEGS:
        rt.poly(net, layer, pts, W)
    for net, x, y in VIAS:
        rt.via(net, x, y)
