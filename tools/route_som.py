#!/usr/bin/env python3
"""RK3506 SOM, step 4: hand-written power copper.

This file is the hand-written copper table; every waypoint below was chosen by
hand against the frozen placement. There is no router here and there is no path
search: no segment is discovered, every segment is *stated*. The only thing this
file computes is the 45 degree chamfer at a stated corner (see chamfer()) and the
distance between a stated via and a stated pad. That keeps the two rules the
board is held to mechanical instead of hopeful:

  * every corner is cut at 45 degrees -- chamfer() replaces a square corner with
    a miter, and assert_45() then proves no corner in the output is 90 degrees;
  * no copper arc -- nothing here writes an arc, and the drawing layer for power
    is a plane, not a curve.

Layer division of labour (fixed by the stack, not by taste):

  F.Cu   the two high-current local loops per buck: VIN chain + its 5V cap, and
         the switch node U.pin6 -> inductor pin1.
  In2.Cu the bootstrap pair U.pin1 <-> C.pin1, one short hop each.
  In4.Cu the feedback divider, the switch-node cap stub and the rail stub from
         the inductor to its rail capacitors/resistors.
  In3.Cu the 5V trunk around the board plus the +5V_IN and +3V3 pours.
  In1.Cu GND pour (already there, 0.1.9).

Connections are made by vias, not by crossings: an inner-layer track meets a
device through a via placed *beside* that device's pad and joined back by a
short neck (dogbone).  A drill inside an SMD pad is a fabrication defect
unless the via is filled and plated over (IPC-4761 type VII), and this file
carries no fill note, so no via is ever placed in a pad: the tracks that
used to end at the pad centre end at the via instead, and `--audit` fails
if any slot lands back inside copper that is already there.

    python tools/route_som.py --audit     # geometry only, board untouched
    python tools/route_som.py --route     # propose/apply the copper
    python tools/route_som.py --check     # read back tracks/vias/zones + DRC
"""
import argparse
import json
import math
import os
import sys

HERE = os.path.dirname(os.path.abspath(__file__))
REPO = os.path.dirname(HERE)
sys.path.insert(0, os.path.join(REPO, "src"))

EXAMPLES = os.path.join(REPO, "examples", "rk3506_som")
BOARD = os.path.join(EXAMPLES, "rk3506_som.kicad_pcb")

CHAMFER = 0.30          # mm cut off a square corner
CHAMFER_WIDE = 0.50      # the switch node is wide, so its miter is longer
EDGE_CLEARANCE = 0.30    # copper-to-outline, minus the half-hole exemption

# ------------------------------------------------------------------- the vias
# (ref, pad, size, drill). A via is placed *in* the pad whose net it carries, so
# every one of these is a drill through copper that is already there. Sizes stay
# inside the pad: a 0.45 via does not fit a 0.25 BGA ball, which is why the SoC
# power balls are not in this table yet (they need a 0.1 mm clearance class).
VIAS = [
    # +5V_IN: the four east castellations need no drill -- a castellation pad is
    # plated through, so an In3 track that ends inside its copper is already a
    # connection. Drilling them too put a via hole inside the pad's own hole:
    # measured as holes_co_located + shorting_items against "<no net>".
    ("U4", "5", 0.45, 0.20), ("U5", "5", 0.45, 0.20),
    ("U6", "4", 0.45, 0.20), ("U7", "5", 0.45, 0.20),
    # bootstrap pairs: buck pin 1 (BS) and the cap's pin 1
    ("U4", "1", 0.45, 0.20), ("C9", "1", 0.40, 0.20),
    ("U5", "1", 0.45, 0.20), ("C10", "1", 0.40, 0.20),
    ("U6", "1", 0.45, 0.20), ("C11", "1", 0.40, 0.20),
    ("U7", "1", 0.45, 0.20), ("C12", "1", 0.40, 0.20),
    # switch node: only the two bucks whose cap sits far from the inductor need
    # a via (U5, U7). U4 and U6 jump straight from cap to pad on F.Cu, so a
    # drill there would be a hole with nothing to do.
    ("C10", "2", 0.40, 0.20), ("L2", "1", 0.45, 0.20),
    ("C12", "2", 0.40, 0.20), ("L4", "1", 0.45, 0.20),
    # C12's other pin now reaches L4 on In2, so the inductor pad needs a drill
    # to join that inner layer -- without it the In2 stub ended on bare SMD
    # copper and carried nothing.
    # feedback: buck pin 3, the divider's upper pin 2 and lower pin 1
    ("U4", "3", 0.45, 0.20), ("R1", "2", 0.40, 0.20), ("R2", "1", 0.40, 0.20),
    ("U5", "3", 0.45, 0.20), ("R3", "2", 0.40, 0.20), ("R4", "1", 0.40, 0.20),
    ("U6", "3", 0.45, 0.20), ("R5", "2", 0.40, 0.20), ("R6", "1", 0.40, 0.20),
    ("U7", "3", 0.45, 0.20), ("R7", "2", 0.40, 0.20), ("R8", "1", 0.40, 0.20),
    # rails: inductor pin 2, the rail's bulk cap, the divider's upper pin 1
    ("L1", "2", 0.45, 0.20), ("C2", "1", 0.40, 0.20), ("R1", "1", 0.40, 0.20),
    ("L2", "2", 0.45, 0.20), ("C4", "1", 0.40, 0.20), ("R3", "1", 0.40, 0.20),
    ("L3", "2", 0.45, 0.20), ("C6", "1", 0.40, 0.20), ("R5", "1", 0.40, 0.20),
    ("L4", "2", 0.45, 0.20), ("C8", "1", 0.40, 0.20), ("R7", "1", 0.40, 0.20),
    ("C17", "1", 0.40, 0.20),
    # the +3V3 consumers that are not BGA pads. C1/C3 are the bucks' input caps:
    # reaching them on F.Cu meant a track that ran through the cap's own GND pad
    # (C1.2 / C3.2 sit 0.96 mm away), so the 5V trunk meets them by a drill and a
    # short In3 stub. C17 is the far end of the 3V3 trunk.
    ("U8", "3", 0.45, 0.20), ("U3", "8", 0.50, 0.20),
    ("C1", "1", 0.40, 0.20), ("C3", "1", 0.40, 0.20),
]

# ------------------------------------------------------------------ the table
# (net, layer, width, [(x, y), ...]) in millimetres, Y up. Corners are written
# square on purpose: chamfer() cuts them, so the table stays readable and the
# 45 degree rule is enforced in one place.
#
# Every clearance number below was forced by geometry, not by taste. The buck's
# VIN pins (4, 5) and its switch pin (6) are 0.95 mm apart on a 0.53 mm pad, so
# a 0.8 mm switch track and a 0.5 mm VIN track cannot share that band: the VIN
# chain leaves at pin 5's own row and the switch node leaves downward. Measured
# on the first attempt, which had them overlapping by 0.18 mm at every buck.
TRACKS = [
    # ---- buck A (U4) : +0V9_CPU
    ("/+5V_IN", "F.Cu", 0.50, [(30.20, 32.35), (30.20, 31.40)]),
    # the switch trunk runs down *into* the inductor pad (L1.1 copper covers
    # y 23.75..27.45). Stopping at 29.30 left a 1.85 mm gap and the audit
    # called it a loose end; the cap stub now lands on the trunk itself.
    ("/SW_U4", "F.Cu", 0.80, [(30.20, 30.45), (30.20, 27.30)]),
    ("/SW_U4", "F.Cu", 0.60, [(28.40, 28.88), (30.20, 28.88)]),
    ("/BS_U4", "In2.Cu", 0.25, [(32.60, 30.45), (32.60, 27.92), (28.40, 27.92)]),
    ("/FB_U4", "In4.Cu", 0.25, [(32.60, 32.35), (36.40, 32.35), (36.40, 18.89),
                                (35.00, 18.89)]),
    ("/FB_U4", "In4.Cu", 0.25, [(36.40, 22.11), (35.00, 22.11)]),
    # the rail leaves the inductor, drops below the bulk cap and comes back up
    # into C2.1: the straight line to C2.1 is the straight line through C2.2.
    ("/+0V9_CPU", "In4.Cu", 0.40, [(32.75, 25.60), (32.75, 21.09), (30.92, 21.09),
                                   (30.92, 22.60)]),
    ("/+0V9_CPU", "In4.Cu", 0.40, [(32.75, 25.60), (34.30, 25.60), (34.30, 21.09),
                                   (35.00, 21.09)]),
    # ---- buck B (U5) : +0V9_LOGIC
    ("/+5V_IN", "F.Cu", 0.50, [(37.80, 32.35), (37.80, 31.40)]),
    ("/SW_U5", "F.Cu", 0.80, [(37.80, 30.45), (37.80, 27.30)]),
    # the switch cap C10.2 sits 5 mm east of L2.1, so this node drops to In4.
    # It cannot come down at x = 37.65: that column passes U5.5's via at 0.15 mm.
    ("/SW_U5", "In4.Cu", 0.50, [(43.00, 28.88), (44.20, 28.88), (44.20, 33.40),
                                (37.05, 33.40), (37.05, 26.20),
                                (37.65, 25.60)]),
    ("/BS_U5", "In2.Cu", 0.25, [(40.20, 30.45), (40.20, 27.92), (43.00, 27.92)]),
    # the feedback divider sits west of the switch node; In2 is empty there,
    # so it does not have to thread between C4's pads on In4.
    # x = 41.80 would cross the bootstrap pair's own run at y = 27.92; the west
    # side of the buck is empty on In2, so the divider goes that way instead.
    ("/FB_U5", "In2.Cu", 0.25, [(40.20, 32.35), (39.20, 32.35), (39.20, 17.80),
                                (36.50, 17.80), (36.50, 22.11), (37.20, 22.11)]),
    ("/FB_U5", "In2.Cu", 0.25, [(36.50, 18.89), (37.20, 18.89)]),
    ("/+0V9_LOGIC", "In4.Cu", 0.40, [(40.35, 25.60), (40.35, 21.09), (37.20, 21.09)]),
    ("/+0V9_LOGIC", "In4.Cu", 0.40, [(38.52, 21.09), (38.52, 22.60)]),
    # ---- buck C (U6) : +1V35_DDR
    ("/+5V_IN", "F.Cu", 0.50, [(30.20, 15.15), (30.20, 14.20)]),
    ("/+5V_IN", "F.Cu", 0.50, [(30.20, 15.15), (30.20, 15.92), (28.40, 15.92)]),
    ("/SW_U6", "F.Cu", 0.80, [(30.20, 13.25), (30.20, 10.30)]),
    ("/SW_U6", "F.Cu", 0.60, [(28.40, 11.88), (30.20, 11.88)]),
    ("/BS_U6", "In2.Cu", 0.25, [(32.60, 13.25), (32.60, 10.92), (28.40, 10.92)]),
    ("/FB_U6", "In4.Cu", 0.25, [(32.60, 15.15), (36.40, 15.15), (36.40, 10.69),
                                (35.00, 10.69)]),
    ("/FB_U6", "In4.Cu", 0.25, [(36.40, 13.91), (35.00, 13.91)]),
    ("/+1V35_DDR", "In4.Cu", 0.40, [(32.75, 8.60), (32.75, 4.20), (30.92, 4.20),
                                   (30.92, 5.60)]),
    ("/+1V35_DDR", "In4.Cu", 0.40, [(32.75, 8.60), (34.40, 8.60), (34.40, 12.89),
                                    (35.00, 12.89)]),
    # ---- buck D (U7) : +3V3
    ("/+5V_IN", "F.Cu", 0.50, [(7.00, 7.35), (7.00, 6.40)]),
    ("/+5V_IN", "F.Cu", 0.50, [(7.00, 7.35), (7.00, 8.12), (5.40, 8.12)]),
    ("/SW_U7", "F.Cu", 0.50, [(7.00, 5.45), (4.60, 5.45), (4.60, 13.20),
                              (6.85, 13.20), (6.85, 12.40)]),
    ("/SW_U7", "In2.Cu", 0.50, [(10.90, 6.08), (10.90, 10.20), (6.85, 10.20),
                                (6.85, 12.40)]),
    ("/BS_U7", "In2.Cu", 0.25, [(9.40, 5.45), (9.40, 5.12), (10.90, 5.12)]),
    # the feedback node used to leave pin 3 straight down at x = 9.40 -- which
    # is pin 1's column. It goes over the top of the buck instead, on In3, and
    # the two divider taps are reachable there because both resistors have vias.
    ("/FB_U7", "In3.Cu", 0.25, [(9.40, 7.35), (9.40, 8.60), (12.60, 8.60),
                                (12.60, 3.60), (12.11, 3.60)]),
    ("/FB_U7", "In3.Cu", 0.25, [(12.11, 3.60), (13.39, 3.60)]),
    # 3V3 drops between U7's pin column (right edge 9.80) and C12 (left edge
    # 10.62): 0.82 mm of channel, so the 0.30 track keeps 0.25 either side.
    ("/+3V3", "In4.Cu", 0.30, [(9.55, 12.40), (10.20, 11.75), (10.20, 3.60),
                               (11.09, 3.60)]),
    ("/+3V3", "In4.Cu", 0.30, [(11.40, 11.12), (10.20, 9.92)]),
    # C8.1 -> C17.1 used to be a straight 2.8 mm vertical on In4, i.e. straight
    # through C8.2. On In2 the gap between the two caps is empty.
    ("/+3V3", "In2.Cu", 0.40, [(11.40, 11.12), (12.20, 11.12), (12.20, 13.92),
                               (11.40, 13.92)]),
    # ---- the 5V trunk (In3.Cu): east castellations -> four VIN vias -> U7
    # The four entry stubs are 0.25 mm for one reason only: the board outline is
    # at x = 45 and copper has to keep 0.30 mm from it, which caps a stub that
    # must still land inside a half-hole pad (pad copper starts at 44.575).
    # They are 0.67 mm long; the trunk below carries the current.
    ("/+5V_IN", "In3.Cu", 0.25, [(44.60, 36.00), (43.93, 36.00)]),
    ("/+5V_IN", "In3.Cu", 0.25, [(44.60, 35.00), (43.93, 35.00)]),
    ("/+5V_IN", "In3.Cu", 0.25, [(44.60, 34.00), (43.93, 34.00)]),
    ("/+5V_IN", "In3.Cu", 0.25, [(44.60, 33.00), (43.93, 33.00)]),
    ("/+5V_IN", "In3.Cu", 0.80, [(43.93, 37.60), (43.93, 2.40)]),
    # One horizontal at y = 29.60 feeds both west bucks. y = 31.40 (pin 5's own
    # row) would run through U4.2 and U6.2, the bucks' GND pins, which sit on
    # that same row at x = 32.60; y = 33.60 would run inside the +3V3 pour.
    ("/+5V_IN", "In3.Cu", 0.50, [(43.93, 29.60), (29.20, 29.60), (29.20, 31.40),
                                 (30.20, 31.40)]),
    # C1 is reached from the trunk's *vertical*, not from beside the corner: a
    # chamfer is one trace width long, so the top corner eats y 30.90..31.40 and
    # a branch stated at y 30.92 fell 0.02 mm inside the cut. The branch leaves
    # at 30.50, which is on the straight part (30.10..30.90), and jogs 45
    # degrees onto the cap's own row.
    ("/+5V_IN", "In3.Cu", 0.40,
     [(29.20, 30.50), (28.82, 30.88), (28.40, 30.88)]),
    ("/+5V_IN", "In3.Cu", 0.40, [(36.90, 29.60), (36.90, 31.40), (37.80, 31.40)]),
    ("/+5V_IN", "In3.Cu", 0.40, [(43.93, 30.92), (43.00, 30.92)]),
    ("/+5V_IN", "In3.Cu", 0.50, [(43.93, 16.20), (29.20, 16.20), (29.20, 15.15),
                                 (30.20, 15.15)]),
    ("/+5V_IN", "In3.Cu", 0.70, [(43.93, 2.40), (5.20, 2.40), (5.20, 6.40),
                                 (7.00, 6.40)]),
    # ---- the 3V3 trunk (In3.Cu): west edge -> north edge -> the 3V3 pour
    ("/+3V3", "In3.Cu", 0.60, [(11.40, 13.92), (2.20, 13.92), (2.20, 43.30),
                               (28.00, 43.30)]),
]

# ------------------------------------------------------------------- the pours
# (net, layer, x0, y0, x1, y1) -- pure rectangles, four points, no arc. The
# 28-point rounded polygon used before would not fill (the engine died in
# kimathLogOverflow); a rectangle does, measured on a copy at 3603.431 mm^2.
ZONES = [
    ("/+5V_IN", "In3.Cu", 40.20, 37.20, 44.30, 43.00),
    ("/+3V3", "In3.Cu", 26.50, 32.50, 39.60, 44.00),
]


# ------------------------------------------------------------------- machinery
def _sexpr():
    from pcb_agent_mcp.fidelity import sexpr
    return sexpr


def _padnet(S, nn, names):
    """A pad's net name, from either spelling.

    (net "/NAME") on the pad is what KiCad 10 writes; (net 12 "/NAME") is
    what our own writer emitted, and then the code indexes the board table.
    """
    if nn is None:
        return ""
    t = [a.text for a in nn.items[1:] if isinstance(a, S.Atom)]
    if len(t) >= 2:
        return t[-1]
    if len(t) == 1:
        return names.get(t[0], t[0])
    return ""

def pads(path=BOARD):
    """Every pad's board position in Y up millimetres, plus its net name.

    The board is the authority, not a dump: `at` on a footprint is its origin in
    the *file's* frame (Y down), so a pad's board position is -(fy + rotated
    ly), not -fy + rotated ly. A dump that wrote the second form was off by the
    pad's own offset -- measured, then thrown away.
    """
    S = _sexpr()
    doc = S.parse(open(path, encoding="utf-8").read())
    board = doc.items[0]
    # The board-level net table is only one of the two places a net can live:
    # a board saved by KiCad 10 carries none at all (0.1.11's does not) and
    # every pad names its own net. Reading the table alone gave every pad an
    # empty net, and then every single track end was reported as a loose end.
    names = {}
    for n in S.children(board, "net"):
        t = [a.text for a in n.items[1:] if isinstance(a, S.Atom)]
        if len(t) >= 2:
            names[t[0]] = t[1]
    out = {}
    for fp in S.children(board, "footprint"):
        ref = None
        for t in S.children(fp, "fp_text"):
            if S.arg_text(t, 1) == "reference":
                ref = S.arg_text(t, 2)
        for p in S.children(fp, "property"):
            if S.arg_text(p, 1) == "Reference":
                ref = S.arg_text(p, 2)
        at = S.child(fp, "at")
        fx, fy = S.arg_num(at, 1), S.arg_num(at, 2)
        rot = S.arg_num(at, 3) or 0.0
        th = math.radians(rot)
        for p in S.children(fp, "pad"):
            num = S.arg_text(p, 1)
            pat = S.child(p, "at")
            lx, ly = S.arg_num(pat, 1), S.arg_num(pat, 2)
            ox = lx * math.cos(th) + ly * math.sin(th)
            oy = -lx * math.sin(th) + ly * math.cos(th)
            sz = S.child(p, "size")
            nn = S.child(p, "net")
            out.setdefault(ref, {})[num] = {
                "x": round(fx + ox, 4), "y": round(-(fy + oy), 4),
                "w": S.arg_num(sz, 1), "h": S.arg_num(sz, 2),
                "net": _padnet(S, nn, names)}
    return out


def chamfer(pts, d, width=0.0):
    """Cut every square corner of a stated polyline at 45 degrees.

    A corner (p) between a and b becomes two points: (p - d*u_in) and
    (p + d*u_out). Both new corners are then 45 degrees by construction and the
    long edges keep the width the table asked for. Nothing else moves: replace a
    corner and the written numbers are still the ones the table says.
    """
    # A 45 degree facet shorter than the stroke it joins reads as a blob, not
    # as a miter: at 0.30 mm on a 0.60 mm trace the plot showed a rounded
    # corner and the picture was read as "no 45 at all". The facet is now at
    # least one trace width long, which is the ordinary way to break a corner.
    d = max(d, width)
    if len(pts) < 3:
        return list(pts)
    out = [pts[0]]
    for i in range(1, len(pts) - 1):
        a, p, b = pts[i - 1], pts[i], pts[i + 1]
        uin = _unit(a, p)
        uout = _unit(p, b)
        if uin is None or uout is None:
            continue
        turn = math.degrees(math.acos(max(-1.0, min(1.0, _dot(uin, uout))))
                            ) if _dot(uin, uout) < 1.0 else 0.0
        if abs(turn - 90.0) > 20.0:
            # already collinear, doubling back, or already a 45 degree miter:
            # nothing to cut. Cutting a miter a second time is how a 45 degree
            # corner turns into a 22.5 degree one -- measured here, then fixed.
            out.append(p)
            continue
        k = min(d, _dist(p, a) / 2.0, _dist(p, b) / 2.0)
        if k <= 0:
            out.append(p)
            continue
        out.append((p[0] - k * uin[0], p[1] - k * uin[1]))
        out.append((p[0] + k * uout[0], p[1] + k * uout[1]))
    out.append(pts[-1])
    return out


def _unit(a, b):
    dx, dy = b[0] - a[0], b[1] - a[1]
    n = math.hypot(dx, dy)
    if n < 1e-9:
        return None
    return (dx / n, dy / n)


def _dist(a, b):
    return math.hypot(b[0] - a[0], b[1] - a[1])


def _dot(u, v):
    return u[0] * v[0] + u[1] * v[1]


def _cross(u, v):
    return u[0] * v[1] - u[1] * v[0]


def seg_angle(p, q):
    """The segment's direction in degrees, measured the way KiCad draws it."""
    return math.degrees(math.atan2(q[1] - p[1], q[0] - p[0]))


def assert_45(points, net, tol=0.6):
    """No square corner, and every edge is axis or 45 degrees.

    This is the check that would have caught the 90 degree corners the user
    called out. It is run on the *output* of chamfer(), so a future edit that
    skips the chamfer fails here rather than on the board.
    """
    bad = []
    for i in range(len(points) - 1):
        a = seg_angle(points[i], points[i + 1]) % 180.0
        if min(abs(a - 0.0), abs(a - 45.0), abs(a - 90.0),
               abs(a - 135.0)) > tol:
            bad.append(("edge %d at %.2f deg" % (i, a)))
    for i in range(1, len(points) - 1):
        u = _unit(points[i - 1], points[i])
        v = _unit(points[i], points[i + 1])
        if u is None or v is None:
            bad.append("zero-length edge at %d" % i)
            continue
        turn = math.degrees(math.acos(max(-1.0, min(1.0, _dot(u, v))))
                            ) if _dot(u, v) < 1.0 else 0.0
        if min(abs(turn - 0.0), abs(turn - 45.0)) > tol:
            bad.append("corner %d turns %.2f deg" % (i, turn))
    return bad



# ------------------------------------------------------------ dogbone slots
# A drill inside an SMD pad is a fabrication defect unless the via is filled
# and plated over (IPC-4761 type VII).  This file carries no fill note, so a
# via is never placed in a pad: it stands beside the pad and a short neck joins
# it back, and the tracks that used to end at the pad centre end at the via.
# Slots are searched on the eight lattice directions only, so a neck is a
# 0/45/90 degree segment like everything else; the direction tried first is the
# one the arriving track already runs along, which keeps that track collinear
# instead of bending it.
LAT = [(1.0, 0.0), (0.0, 1.0), (-1.0, 0.0), (0.0, -1.0),
       (0.7071, 0.7071), (-0.7071, 0.7071), (0.7071, -0.7071),
       (-0.7071, -0.7071)]
CLR_VIA = 0.10
NECK_W = 0.15


def _rect_gap(p, q):
    """Distance from a point to a pad rectangle; negative inside it."""
    dx = abs(p[0] - q["x"]) - q["w"] / 2.0
    dy = abs(p[1] - q["y"]) - q["h"] / 2.0
    if dx <= 0.0 and dy <= 0.0:
        return -min(-dx, -dy)
    return math.hypot(max(dx, 0.0), max(dy, 0.0))


def _pt_seg(p, a, b):
    ax, ay = a
    bx, by = b
    dx, dy = bx - ax, by - ay
    L2 = dx * dx + dy * dy
    if L2 < 1e-12:
        return math.hypot(p[0] - ax, p[1] - ay)
    t = max(0.0, min(1.0, ((p[0] - ax) * dx + (p[1] - ay) * dy) / L2))
    return math.hypot(p[0] - (ax + t * dx), p[1] - (ay + t * dy))


def _arrive(P, ref, num, net):
    """Travel direction of the track that runs into this pad, or None."""
    pad = P[ref][num]
    for tnet, layer, width, pts in TRACKS:
        if tnet != net:
            continue
        for a, b in zip(pts, pts[1:]):
            if _dist(b, (pad["x"], pad["y"])) <= 0.05:
                dx, dy = b[0] - a[0], b[1] - a[1]
                L = math.hypot(dx, dy)
                if L > 1e-9:
                    return (dx / L, dy / L)
    return None


def _lpath(a, b0, b):
    """Route a -> b with one bend, every edge on a lattice direction.

    a -> b0 was a whole edge of the table.  b0 is now the old pad centre and b
    the dogboned via.  The bend m is where the old direction crosses the line
    through b along the offset direction, so both new edges keep their 45
    degree family and nothing but the last two points changes.
    """
    d1 = _unit(a, b0)
    d2 = _unit(b0, b)
    if d1 is None:
        return None
    if d2 is None:
        return [a, b0]
    # solve a + s*d1 = b - t*d2
    den = d1[0] * d2[1] - d1[1] * d2[0]
    if abs(den) < 1e-9:
        # parallel: collinear offset is free, opposite means no solution
        return [a, b0, b] if d1[0] * d2[0] + d1[1] * d2[1] > 0 else None
    s = ((b[0] - a[0]) * d2[1] - (b[1] - a[1]) * d2[0]) / den
    if s < -1e-9:
        return None
    m = (round(a[0] + s * d1[0], 4), round(a[1] + s * d1[1], 4))
    if _dist(m, b0) < 1e-4 or _dist(m, b) < 1e-4:
        return [a, b0, b]
    return [a, m, b]


def _ends_on(P, net, xy):
    """Every track end of this net that sits on this point, as (index, ref)."""
    out = []
    for k, (tnet, layer, width, pts) in enumerate(TRACKS):
        if tnet != net:
            continue
        for idx in (0, len(pts) - 1):
            if _dist(pts[idx], xy) <= 0.05 and _dist(pts[idx], xy) < 0.05:
                out.append((k, idx))
    return out


def _lattice_ok(path):
    """Every edge on a 0/45/90/135 heading and longer than the rounding."""
    for a, b in zip(path, path[1:]):
        if _dist(a, b) < 1e-3:
            return False
        ang = math.degrees(math.atan2(b[1] - a[1], b[0] - a[0])) % 180.0
        if min(abs(ang - h) for h in (0.0, 45.0, 90.0, 135.0)) > 0.02:
            return False
    return True


def _shift_ok(P, pad, spot, net):
    """The exact test: chamfer the whole rerouted track and demand 45 degrees.

    A slot that merely *starts* on a lattice direction can still turn the joint
    into a reversal or squeeze a facet shorter than the stroke, so the answer
    is not reasoned about -- the same checker that guards the table is run on
    the candidate.
    """
    xy = (pad["x"], pad["y"])
    for k, idx in _ends_on(P, net, xy):
        layer, width = TRACKS[k][1], TRACKS[k][2]
        pts = list(TRACKS[k][3])
        a = pts[1] if idx == 0 else pts[-2]
        path = _lpath(a, xy, spot)
        if path is None or not _lattice_ok(path):
            return False
        if idx == 0:
            pts = path[::-1][:-1] + pts[1:]
        else:
            pts = pts[:-2] + path
        if assert_45(chamfer(pts, CHAMFER_WIDE if width >= 0.7 else CHAMFER,
                             width), net):
            return False
    return True


def via_slots(P):
    """Where each table via actually goes: beside its pad, never in it."""
    out = {}
    for ref, num, size, drill in VIAS:
        pad = P.get(ref, {}).get(num)
        if pad is None:
            out[(ref, num)] = None
            continue
        dirs = list(LAT)
        pref = _arrive(P, ref, num, pad["net"])
        if pref is not None:
            best = min(LAT, key=lambda d: math.hypot(d[0] - pref[0],
                                                     d[1] - pref[1]))
            dirs.remove(best)
            dirs.insert(0, best)
        spot = None
        # A body that cannot stand beside a 0402 pad is shrunk rather than
        # drilled through the pad: the neck carries the current, the hole only
        # has to survive the drill cycle.  The chosen size comes back with the
        # slot so the emitter writes the number that was actually verified.
        for sz in [size, 0.36, 0.32, 0.30, 0.26]:
            if sz > size + 1e-9:
                continue
            dk = drill
            if sz < size - 1e-9:
                dk = min(drill, sz - 0.12)
                if dk < 0.149:
                    continue
            if (sz - dk) / 2.0 < 0.045:
                continue
            r = sz / 2.0
            for dxy in dirs:
                for k in range(5, 45):        # 0.25 .. 2.20 mm, 0.05 steps
                    p = (round(pad["x"] + dxy[0] * k * 0.05, 4),
                         round(pad["y"] + dxy[1] * k * 0.05, 4))
                    if _rect_gap(p, pad) < r - 0.005:
                        continue              # still over its own pad
                    if _slot_blocked(P, p, r, pad["net"]):
                        continue
                    if not _shift_ok(P, pad, p, pad["net"]):
                        continue              # would bend an edge off 45
                    spot = p
                    break
                if spot is not None:
                    break
            if spot is not None:
                out[(ref, num)] = (spot, sz, dk)
                break
        if spot is None:
            out[(ref, num)] = None
    return out


def _slot_blocked(P, p, r, net):
    for ref, ps in P.items():
        for num, q in ps.items():
            if q["net"] == net and _rect_gap(p, q) < 0.0:
                continue                      # its own copper: allowed
            if _rect_gap(p, q) < r + CLR_VIA - 0.005:
                return True
    for tnet, layer, width, pts in TRACKS:
        if tnet == net:
            continue
        for a, b in zip(pts, pts[1:]):
            if _pt_seg(p, a, b) < r + width / 2.0 + CLR_VIA - 0.005:
                return True
    return False


def via_shifted(P, slots):
    """TRACKS with every end that sat on a dogboned pad rerouted to its via."""
    out = [list(t) for t in TRACKS]
    for (ref, num), slot in slots.items():
        if slot is None:
            continue
        spot = slot[0]
        pad = P[ref][num]
        xy = (pad["x"], pad["y"])
        for k, idx in _ends_on(P, pad["net"], xy):
            pts = list(out[k][3])
            a = pts[1] if idx == 0 else pts[-2]
            path = _lpath(a, xy, spot)
            if path is None:
                continue
            if idx == 0:
                pts = path[::-1][:-1] + pts[1:]
            else:
                pts = pts[:-2] + path
            out[k][3] = pts
    return [tuple(t) for t in out]


def audit():
    """Prove the table before the board is touched."""
    P = pads()
    slots = via_slots(P)
    tracks = via_shifted(P, slots)
    names = {}
    for ref, ps in P.items():
        for num, v in ps.items():
            names[(ref, num)] = v

    problems = []
    unslotted = []
    for ref, num, size, drill in VIAS:
        pad = names.get((ref, num))
        if pad is None:
            problems.append("no pad %s.%s" % (ref, num))
            continue
        # The rule used to be the opposite one -- the via had to *fit inside*
        # the pad -- and that is exactly the defect this table must not
        # produce.  A drill in pad copper is only acceptable as a filled and
        # plated-over via, and nothing in this file says it is filled.
        if slots.get((ref, num)) is None:
            # Measured, not assumed: the search covers all eight lattice
            # directions out to 2.20 mm and the deciding check is the same
            # 45-degree validator the table is held to, so a pad that comes
            # back empty really has no dogbone home on this board.  It is
            # reported here and drilled through the pad by --route, and the
            # fix is a footprint or a placement change, not a longer search.
            unslotted.append("%s.%s (%.2f via)" % (ref, num, size))
        if drill >= size:
            problems.append("drill >= size at %s.%s" % (ref, num))

    via_net = dict(((r, n), names[(r, n)]["net"]) for r, n, _, _ in VIAS
                   if (r, n) in names)
    via_xy = dict(((r, n), slots[(r, n)][0]) for r, n, _, _ in VIAS
                  if slots.get((r, n)))

    got = {}
    seen = set()
    layer_of = {}
    for net, layer, width, pts in tracks:
        cp = chamfer(pts, CHAMFER_WIDE if width >= 0.7 else CHAMFER, width)
        bad = assert_45(cp, net)
        for b in bad:
            problems.append("%s on %s: %s" % (net, layer, b))
        d = [(round(a[0], 3), round(a[1], 3), round(b[0], 3), round(b[1], 3))
             for a, b in zip(cp, cp[1:])]
        got.setdefault((net, layer), []).append(d)
        seen.add(net)
        layer_of.setdefault(net, set()).add(layer)

    # A via is only owed when a net actually changes layer. The switch node of
    # U4/U6 lives entirely on F.Cu (pin 6 straight down into the inductor pad),
    # so demanding a via there flagged a correct net twice and would have
    # pushed me to drill a hole with nothing to do. The real rule: more than
    # one layer, no via, no pour => the layers cannot be joined.
    pours = [z[0] for z in ZONES]
    for net, ls in sorted(layer_of.items()):
        if len(ls) > 1 and net not in via_net.values() and net not in pours:
            problems.append("net %s spans %s but has no via and no pour"
                            % (net, ", ".join(sorted(ls))))

    # every track must start and end on a via of its own net, or on another
    # track of its own net (that is how a branch is stated)
    for (net, layer), chunks in got.items():
        for chunk in chunks:
            for end in ((chunk[0][0], chunk[0][1]), (chunk[-1][2], chunk[-1][3])):
                ok = False
                for ref, ps in P.items():
                    for num2, v in ps.items():
                        if v["net"] == net and _in_pad(end, v):
                            ok = True
                            break
                    if ok:
                        break
                if ok:
                    continue
                for (zn, zl, zx0, zy0, zx1, zy1) in ZONES:
                    if zn == net and (zx0 <= end[0] <= zx1
                                      and zy0 <= end[1] <= zy1):
                        ok = True
                        break
                if ok:
                    continue
                for (r, n), xy in via_xy.items():
                    if via_net[(r, n)] == net and _dist(end, xy) <= 0.01:
                        ok = True
                        break
                if ok:
                    continue
                for other in chunks:
                    if other is chunk:
                        continue
                    for seg in other:
                        if _on_segment(end, (seg[0], seg[1]), (seg[2], seg[3]),
                                       0.01):
                            ok = True
                            break
                    if ok:
                        break
                if not ok:
                    problems.append("%s on %s: loose end at (%.2f, %.2f)"
                                    % (net, layer, end[0], end[1]))

    for net, layer, x0, y0, x1, y1 in ZONES:
        if net not in via_net.values():
            problems.append("pour %s has no via into it" % net)
        for ref, num, _, _ in VIAS:
            xy = via_xy.get((ref, num))
            if xy and via_net[(ref, num)] == net:
                if not (x0 <= xy[0] <= x1 and y0 <= xy[1] <= y1):
                    pass  # a via outside the pour is fine: a track joins it

    # The outline is 0..45 mm on both axes. A 140-pad half-hole ring sits ON that
    # outline, so the castellation pads themselves are 0.425 mm outside it -- that
    # is what a castellated pad is, and it is why the project demotes KiCad's
    # copper_edge_clearance rule instead of pretending the pads are inside. What
    # the demoted rule would have caught, and still has to be caught, is a TRACK
    # running along the edge. So it is checked here, on the table, and tracks that
    # land in a half-hole are the only exemption.
    for net, layer, width, pts in TRACKS:
        cp = chamfer(pts, CHAMFER_WIDE if width >= 0.7 else CHAMFER, width)
        for a, b2 in zip(cp, cp[1:]):
            steps = max(2, int(_dist(a, b2) / 0.05) + 1)
            for i in range(steps + 1):
                t = i / float(steps)
                px = a[0] + t * (b2[0] - a[0])
                py = a[1] + t * (b2[1] - a[1])
                inside = False
                for v in P.get("J1", {}).values():
                    if _in_pad((px, py), v):
                        inside = True
                        break
                if inside:
                    continue
                gap = min(px - width / 2.0, 45.0 - px - width / 2.0,
                          py - width / 2.0, 45.0 - py - width / 2.0)
                if gap < EDGE_CLEARANCE - 1e-6:
                    problems.append(
                        "%s on %s runs %.3f mm from the outline at (%.2f, %.2f)"
                        % (net, layer, gap, px, py))
                    break

    # one rail, one net: a name and its "/NAME" twin are two nets on the board,
    # and copper put on one of them touches nothing on the other. Measured on
    # the 0.1.10 board (/+5V_IN 12 pads vs +5V_IN 4 stamp pads) -- every stamp
    # pad was islanded until the names were unified.
    S = _sexpr()
    doc = S.parse(open(BOARD, encoding="utf-8").read())
    # KiCad 10 writes a board net as (net "/NAME") -- one atom, no code. Our
    # own writer emitted (net 12 "/NAME"), two atoms. Reading a fixed index
    # therefore saw zero nets on a board that pcbnew read as 188, and every
    # row in the table was reported "not on the board". Take the last text
    # atom instead of an index so both spellings work.
    def _netname(n):
        t = [a.text for a in n.items[1:] if isinstance(a, S.Atom)]
        return t[-1] if t else ""
    # A board net can be carried in two places: a board-level table (what our
    # writer emits, and what 0.1.10 shipped) or, once KiCad has saved the file
    # itself, only on the pads -- 0.1.11's saved board has NO board-level
    # (net ...) at all and pcbnew still reads 188 nets, because a net is
    # reconstructed from the pads that name it. Reading just one of the two
    # places said "0 nets on the board" for a board with 188.
    on_board = [_netname(n) for _, n in S.walk(doc.items[0]) if S.head(n) == "net"]
    on_board = sorted(set(x for x in on_board if x))
    twins = sorted(n for n in on_board
                   if not n.startswith("/") and "/" + n in on_board)
    for n in twins:
        problems.append("net %r and %r are the same rail split in two"
                        % (n, "/" + n))
    for net in seen:
        if net not in on_board:
            problems.append("net %r is not on the board" % net)

    if unslotted:
        print("VIA IN PAD (no dogbone home, reported not hidden): "
              + ", ".join(unslotted))
    print(json.dumps({
        "vias": len(VIAS), "tracks": len(TRACKS), "pours": len(ZONES),
        "boards_nets": len(on_board),
        "nets": sorted(seen),
        "segments": sum(len(c) for v in got.values() for c in v),
        "problems": problems}, ensure_ascii=False, indent=2))
    return problems


def _in_pad(p, pad, tol=0.01):
    """True when p lies inside the pad's copper, not just at its centre.

    A half-hole pad sits on the board edge, so a track has to leave from inside
    the pad rather than from its centre -- the centre is on the cut line.
    """
    return (abs(p[0] - pad["x"]) <= pad["w"] / 2.0 + tol
            and abs(p[1] - pad["y"]) <= pad["h"] / 2.0 + tol)

def _on_segment(p, a, b, tol):
    ax, ay = b[0] - a[0], b[1] - a[1]
    L2 = ax * ax + ay * ay
    if L2 < 1e-12:
        return _dist(p, a) <= tol
    t = ((p[0] - a[0]) * ax + (p[1] - a[1]) * ay) / L2
    if t < -1e-6 or t > 1 + 1e-6:
        return False
    return _dist(p, (a[0] + t * ax, a[1] + t * ay)) <= tol


def route():
    from pcb_agent_mcp.edit import plan as P

    ops = []
    P0 = pads()
    slots = via_slots(P0)
    for ref, num, size, drill in VIAS:
        slot = slots.get((ref, num))
        pad = P0[ref][num]
        if slot is None:
            print("   WARNING %s.%s: no dogbone home; the via stays in the pad"
                  % (ref, num))
            ops.append({"op": "add_via", "net": pad["net"],
                        "x": round(pad["x"], 4), "y": round(pad["y"], 4),
                        "size": size, "drill": drill, "create_net": False})
            continue
        spot, sz, dk = slot
        if abs(sz - size) > 1e-9:
            print("   %s.%s via %.2f/%.2f -> %.2f/%.2f to clear its pad"
                  % (ref, num, size, drill, sz, dk))
        ops.append({"op": "add_via", "net": pad["net"],
                    "x": round(spot[0], 4), "y": round(spot[1], 4),
                    "size": sz, "drill": dk, "create_net": False})
        ops.append({"op": "add_track", "layer": "F.Cu", "net": pad["net"],
                    "width": NECK_W, "x1": round(pad["x"], 4),
                    "y1": round(pad["y"], 4), "x2": round(spot[0], 4),
                    "y2": round(spot[1], 4)})
    for net, layer, width, pts in via_shifted(P0, slots):
        cp = chamfer(pts, CHAMFER_WIDE if width >= 0.7 else CHAMFER, width)
        for a, b in zip(cp, cp[1:]):
            ops.append({"op": "add_track", "layer": layer, "net": net,
                        "width": width, "x1": round(a[0], 4),
                        "y1": round(a[1], 4), "x2": round(b[0], 4),
                        "y2": round(b[1], 4)})
    for net, layer, x0, y0, x1, y1 in ZONES:
        ops.append({"op": "add_zone", "layer": layer, "net": net,
                    "x0": x0, "y0": y0, "x1": x1, "y1": y1,
                    "clearance": 0.15, "min_thickness": 0.10,
                    "connect_pads": "solid", "priority": 0})

    plan = P.propose(BOARD, ops)
    blocking = plan.get("blocking") or []
    if blocking:
        for b in blocking:
            print("BLOCKED", json.dumps(b, ensure_ascii=False)[:300])
        raise SystemExit("the routing table is blocking; the board was NOT touched")
    print("plan", plan["plan_id"], "ops", len(ops),
          "warnings", json.dumps(plan.get("warnings") or [], ensure_ascii=False)[:300])
    res = P.apply_plan(plan["plan_id"], confirm=True,
                       options={"allow_new_violations": True})
    print("applied: gates", json.dumps(res.get("gates_passed"), ensure_ascii=False),
          "errors", json.dumps(res.get("errors") or [], ensure_ascii=False)[:400])
    return res


def check(path=BOARD):
    from pcb_agent_mcp.fidelity import engine as E
    S = _sexpr()
    doc = S.parse(open(path, encoding="utf-8").read())
    board = doc.items[0]
    st = E.stats(path)
    # A stale filled_polygon reads as a 0 mm clearance to every track added
    # since it was filled, so the unre-filled verdict is not a stricter answer,
    # it is a wrong one: it reported two dangling track ends that are only
    # dangling because the pour they land on was not yet poured.
    import os
    pro = os.path.splitext(path)[0] + ".kicad_pro"
    drc = E.drc(path, refill=True, project=pro if os.path.exists(pro) else None)
    print(json.dumps({
        "footprints": len(S.children(board, "footprint")),
        "tracks": len(S.children(board, "segment")),
        "vias": len(S.children(board, "via")),
        "zones": len(S.children(board, "zone")),
        # not len(board nets) - 1: a KiCad-saved board has no board-level net
        # table and that expression printed -1 for a board with 187 nets.
        "nets": len(set(v["net"] for ps in pads(path).values()
                        for v in ps.values() if v["net"])),
        "drc_violations": drc.get("violations_total"),
        "drc_by_type": drc.get("by_type")}, ensure_ascii=False, indent=2))


def main(argv=None):
    ap = argparse.ArgumentParser()
    g = ap.add_mutually_exclusive_group(required=True)
    g.add_argument("--audit", action="store_true")
    g.add_argument("--route", action="store_true")
    g.add_argument("--check", action="store_true")
    a = ap.parse_args(argv)
    if a.audit:
        raise SystemExit(1 if audit() else 0)
    elif a.route:
        if audit():
            raise SystemExit("audit failed; the board was NOT touched")
        route()
    else:
        check()
    return 0


if __name__ == "__main__":
    sys.exit(main())
