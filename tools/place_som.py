#!/usr/bin/env python3
"""RK3506 SOM, step 3: hand placement of the 45 devices on the front side.

Nothing here is a route; it is a placement table. Every coordinate in PLACEMENT
was chosen by hand against the frozen pinout -- there is no automatic placement
in this repository, by instruction ("不许自动布线自动布局"). The table is not
checked against the world by hope: this file measures each footprint's own
courtyard, then proves -- before the board is touched -- that

  * no two courtyards overlap (axis-aligned bound of the rotated courtyard),
  * every courtyard stays EDGE_GAP away from the board edge,
  * no courtyard covers a castellation half-hole,
  * every netlist part has a coordinate and every coordinate has a part.

Copper comes later. The DDR bus faces U2 because U1's DDR balls sit on the
footprint's +Y side, which is the board's south side after rot 0; that is the
one placement decision the netlist makes for us, and it is the reason U2 is
directly below U1 rather than beside it.

    python tools/place_som.py --audit     # geometry only, no board change
    python tools/place_som.py --place     # add the 45 footprints (propose/apply)
    python tools/place_som.py --check     # read back what is on the board
"""
import argparse
import json
import os
import sys

HERE = os.path.dirname(os.path.abspath(__file__))
REPO = os.path.dirname(HERE)
sys.path.insert(0, os.path.join(REPO, "src"))

EXAMPLES = os.path.join(REPO, "examples", "rk3506_som")
BOARD = os.path.join(EXAMPLES, "rk3506_som.kicad_pcb")
SCHEMATIC = os.path.join(EXAMPLES, "rk3506_som.kicad_sch")
LIB = os.path.join(EXAMPLES, "lib", "RK3506.pretty")
KICAD_FP = os.environ.get("KICAD10_FOOTPRINTS",
                          r"D:/KiCad/10.0/share/kicad/footprints")

SIZE = 45.0
EDGE_GAP = 1.2          # courtyard to board edge; castellations reach 0.425 in
CASTELLATION_PITCH = 1.0

# ------------------------------------------------------------------ the table
# (x, y, rot) in millimetres, Y UP, rotation CCW, all on F.Cu (single side SMD).
#
#   y 45  <- N edge: LCDC_D0..D23, CLK, HSYNC, VSYNC, DEN
#   x 45  <- E edge: MIPI DSI, USB0/USB1, SARADC, ACODEC, +5V_IN (y 33..36)
#   y  0  <- S edge: SDMMC, RMII0, RMII1, +3V3 (x 3..6)
#   x  0  <- W edge: RESET_N, UART0, SPI0/1, SAI0/1, CLK_OUT, ETH_CLK0_25M
#
# U1 rot 0 puts its DDR balls (local +Y) to the south, its LCDC and MIPI/USB
# balls to the north, SDMMC/RMII to the east and SPI/SAI/UART to the west.
# So: U1 in the middle-north, U2 straight below it, the four bucks east (the
# 5V_IN stamp pads are on the east edge at y 33..36), 3V3 buck south-west (the
# +3V3 stamp pads are on the south edge at x 3..6), NAND east-north (FSPI balls
# are U1's north-east corner), crystal west (OSC balls are U1's west edge).
PLACEMENT = {
    # ---- the two BGAs
    "U1": (21.0, 28.0, 0.0),      # RK3506J, 333 ball
    "U2": (21.0, 10.0, 0.0),      # MT41K256M16 DDR3L, 96 ball
    # ---- memory / reset / clock
    "U3": (34.5, 38.0, 90.0),     # W25N01GV SPI-NAND, beside U1's FSPI corner
    "Y1": (11.6, 29.4, 0.0),      # 24 MHz, beside OSC_XIN/XOUT (H3/H2)
    "SW1": (7.5, 20.0, 90.0),     # RESET, between U1 T3 and stamp pad 112
    # ---- buck A: +0V9_CPU (U4 -> L1), east column, high (near +5V_IN pads)
    "U4": (31.4, 31.4, 0.0),
    "L1": (31.4, 25.6, 0.0),
    "C1": (28.4, 31.4, 90.0),     # 5V bulk in
    "C9": (28.4, 28.4, 90.0),     # bootstrap, U4 pins 1/6
    "C2": (31.4, 22.6, 0.0),      # +0V9_CPU out bulk
    "R1": (35.0, 21.6, 90.0),     # divider, +0V9_CPU / FB_U4
    "R2": (35.0, 19.4, 90.0),
    # ---- buck B: +0V9_LOGIC (U5 -> L2), east column, high
    "U5": (39.0, 31.4, 0.0),
    "L2": (39.0, 25.6, 0.0),
    "C3": (43.0, 31.4, 90.0),
    "C10": (43.0, 28.4, 90.0),
    "C4": (39.0, 22.6, 0.0),      # +0V9_LOGIC out bulk
    "R3": (37.2, 21.6, 90.0),
    "R4": (37.2, 19.4, 90.0),
    # ---- buck C: +1V35_DDR (U6 -> L3), east of U2, so 1V35 stays short
    "U6": (31.4, 14.2, 0.0),
    "L3": (31.4, 8.6, 0.0),
    "C5": (28.4, 16.4, 90.0),
    "C11": (28.4, 11.4, 90.0),
    "C6": (31.4, 5.6, 0.0),       # +1V35_DDR out bulk
    "R5": (35.0, 13.4, 90.0),
    "R6": (35.0, 11.2, 90.0),
    # ---- buck D: +3V3 (U7 -> L4), next to the +3V3 stamp pads (x 3..6)
    "U7": (8.2, 6.4, 0.0),
    "L4": (8.2, 12.4, 0.0),
    "C7": (5.4, 8.6, 90.0),
    "C12": (10.9, 5.6, 90.0),
    "C8": (11.4, 11.6, 90.0),     # +3V3 out bulk
    "C17": (11.4, 14.4, 90.0),
    "R7": (11.6, 3.6, 0.0),
    "R8": (13.9, 3.6, 0.0),
    # ---- 1V8 LDO (AMS1117-1.8) north, feeding U1's 1V8_ANA balls
    "U8": (24.6, 38.6, 0.0),
    "C18": (17.6, 37.0, 90.0),
    "C16": (17.9, 34.6, 0.0),      # +0V9_ANA out
    # ---- ferrites + analogue rails, west column beside U1
    "FB1": (12.4, 26.4, 0.0),     # +0V9_LOGIC -> +0V9_PMU
    "FB2": (12.4, 24.6, 0.0),     # +0V9_LOGIC -> +0V9_ANA
    "C14": (12.4, 22.6, 0.0),     # +0V9_PMU
    "C15": (12.4, 21.0, 0.0),
    # ---- DDR reference divider 0.675 V, beside U2 pin H1
    "C13": (13.4, 10.4, 90.0),
    "R9": (13.4, 8.2, 90.0),
    "R10": (13.4, 6.0, 90.0),
}


# --------------------------------------------------------------- measurement
def _sexpr():
    from pcb_agent_mcp.fidelity import sexpr
    return sexpr


def footprint_path(lib_id):
    """`Lib:Name` -> the .kicad_mod on disk, or an error. Never invented."""
    lib, name = lib_id.split(":", 1)
    if lib == "RK3506":
        path = os.path.join(LIB, name + ".kicad_mod")
    else:
        if not os.path.isdir(KICAD_FP):
            raise SystemExit("KiCad footprint library root is missing: %r\n"
                             "set KICAD10_FOOTPRINTS to the .../footprints "
                             "directory of your KiCad install" % KICAD_FP)
        path = os.path.join(KICAD_FP, lib + ".pretty", name + ".kicad_mod")
    if not os.path.isfile(path):
        raise SystemExit("footprint file does not exist: %r (for %s)" % (path, lib_id))
    return path


def measure(mod_path):
    """Pad-copper AABB and courtyard AABB, both in the footprint's own frame."""
    S = _sexpr()
    fp = S.parse(open(mod_path, encoding="utf-8").read()).items[0]
    pads = S.children(fp, "pad")
    if not pads:
        raise SystemExit("%s has no pads - refusing to place an empty footprint"
                         % mod_path)
    px, py, w, h = [], [], [], []
    for p in pads:
        at = S.child(p, "at")
        sz = S.child(p, "size")
        px.append(S.arg_num(at, 1))
        py.append(S.arg_num(at, 2))
        w.append(S.arg_num(sz, 1) or 0.0)
        h.append(S.arg_num(sz, 2) or 0.0)
    xs_lo = [px[i] - w[i] / 2.0 for i in range(len(px))]
    xs_hi = [px[i] + w[i] / 2.0 for i in range(len(px))]
    ys_lo = [py[i] - h[i] / 2.0 for i in range(len(py))]
    ys_hi = [py[i] + h[i] / 2.0 for i in range(len(py))]
    pad_box = (min(xs_lo), min(ys_lo), max(xs_hi), max(ys_hi))
    xs, ys = [], []
    for kind in ("fp_rect", "fp_line", "fp_arc", "fp_poly", "fp_circle"):
        for it in S.children(fp, kind):
            lay = S.child(it, "layer")
            if lay is None or S.arg_text(lay, 1) != "F.CrtYd":
                continue
            for key in ("start", "end", "center", "mid"):
                n = S.child(it, key)
                if n is not None:
                    xs.append(S.arg_num(n, 1))
                    ys.append(S.arg_num(n, 2))
    if not xs:
        raise SystemExit("%s has no F.CrtYd graphics; a courtyard is what this "
                         "audit measures, so a footprint without one cannot be "
                         "placed" % mod_path)
    crt = (min(xs), min(ys), max(xs), max(ys))
    return pad_box, crt


def placed_rect(x, y, rot, box):
    """The footprint AABB on the board: rotate the four corners, then bound."""
    import math
    a = math.radians(rot)
    ca, sa = math.cos(a), math.sin(a)
    xs, ys = [], []
    for cx in (box[0], box[2]):
        for cy in (box[1], box[3]):
            # KiCad: +angle rotates the footprint counter-clockwise; the board's
            # Y axis is up here, so the maths stays in the usual orientation.
            xs.append(x + cx * ca - cy * sa)
            ys.append(y + cx * sa + cy * ca)
    return (min(xs), min(ys), max(xs), max(ys))


def rect_gap(a, b):
    """Axis-aligned gap between two rectangles; negative means overlap."""
    dx = max(b[0] - a[2], a[0] - b[2])
    dy = max(b[1] - a[3], a[1] - b[3])
    if dx < 0 and dy < 0:
        return -min(-dx, -dy)
    return max(dx, dy)


def fmt(r):
    return "x %.2f..%.2f y %.2f..%.2f" % (r[0], r[2], r[1], r[3])


# ---------------------------------------------------------------------- input
def netlist():
    from pcb_agent_mcp.fidelity import engine
    res = engine.sch_netlist(SCHEMATIC)
    if str(res.get("status")) != "ok":
        raise SystemExit("the schematic will not netlist: %s" % res.get("message"))
    pad_nets = {}
    for net in res["nets"]:
        for ref, pin in net["nodes"]:
            pad_nets.setdefault(ref, {})[str(pin)] = net["name"]
    parts = {c["ref"]: c for c in res["components"]}
    return parts, pad_nets


def stamp_rects():
    """Where the 140 half-holes are, as board-frame circles (Y up)."""
    import csv
    out = []
    with open(os.path.join(EXAMPLES, "netmap_terminals.csv"),
              encoding="utf-8", newline="") as fh:
        rows = [r for r in csv.DictReader(fh) if r["device"] == "STAMP"]
    if len(rows) != 140:
        raise SystemExit("expected 140 stamp terminals, found %d" % len(rows))
    # pad 1..40 north, 41..70 east, 71..110 south, 111..140 west; spans are
    # centred, which is the 0.1.10 fix -- see gen_som_footprint.py edge_axis().
    n_ns, n_ew = 40, 30
    span_ns = (n_ns - 1) * CASTELLATION_PITCH
    span_ew = (n_ew - 1) * CASTELLATION_PITCH
    half = SIZE / 2.0
    centres = {}
    for i in range(n_ns):
        centres[1 + i] = (half - span_ns / 2.0 + i * CASTELLATION_PITCH, SIZE)
        centres[1 + n_ns + n_ew + i] = (half + span_ns / 2.0 - i * CASTELLATION_PITCH, 0.0)
    for i in range(n_ew):
        centres[1 + n_ns + i] = (SIZE, half + span_ew / 2.0 - i * CASTELLATION_PITCH)
        centres[1 + 2 * n_ns + n_ew + i] = (0.0, half - span_ew / 2.0 + i * CASTELLATION_PITCH)
    R = 0.85 / 2.0
    for num in sorted(centres):
        x, y = centres[num]
        out.append((num, (x - R, y - R, x + R, y + R)))
    return out


# --------------------------------------------------------------------- audits
def audit():
    parts, _ = netlist()
    # J1 is the castellation ring itself: step 2 (gen_som_board.py
    # --castellation) put it on the board, so it has no coordinate here.
    missing = [r for r in parts if r not in PLACEMENT and r != "J1"]
    extra = [r for r in PLACEMENT if r not in parts]
    if missing or extra:
        raise SystemExit("placement table and netlist disagree: missing %s extra %s"
                         % (missing, extra))
    rects = {}
    rows = []
    for ref in sorted(PLACEMENT, key=lambda r: (r[0], int(r[1:]) if r[1:].isdigit() else 0)):
        x, y, rot = PLACEMENT[ref]
        lib_id = parts[ref]["footprint"]
        _pad_box, crt = measure(footprint_path(lib_id))
        rect = placed_rect(x, y, rot, crt)
        rects[ref] = rect
        rows.append((ref, lib_id, rect))

    problems = []
    for ref, lib_id, rect in rows:
        if (rect[0] < EDGE_GAP or rect[1] < EDGE_GAP
                or rect[2] > SIZE - EDGE_GAP or rect[3] > SIZE - EDGE_GAP):
            problems.append("%s %s is %.2f mm from the edge (need %.1f): %s"
                            % (ref, lib_id.split(":")[1], min(rect[0], rect[1],
                               SIZE - rect[2], SIZE - rect[3]), EDGE_GAP, fmt(rect)))
        for num, pad in stamp_rects():
            if rect_gap(rect, pad) <= 0.0:
                problems.append("%s covers castellation pad %d at %s"
                                % (ref, num, fmt(pad)))
                break

    worst = []
    refs = [r[0] for r in rows]
    for i in range(len(refs)):
        for j in range(i + 1, len(refs)):
            a, b = refs[i], refs[j]
            gap = rect_gap(rects[a], rects[b])
            if gap <= 0.0:
                problems.append("%s and %s overlap by %.2f mm  %s vs %s"
                                % (a, b, -gap, fmt(rects[a]), fmt(rects[b])))
            elif gap < 0.20:
                worst.append((gap, a, b))

    us = {k: sum(1 for r in rows if r[0][0] == k) for k in "URCLY"}
    report = {
        "parts": len(rows),
        "counts": us,
        "overlaps_or_edge": len(problems),
        "tight_pairs_under_0.2mm": len(worst),
        "board_used_mm2": round(sum((r[2][2]-r[2][0])*(r[2][3]-r[2][1])
                                    for r in rows), 1),
        "board_area_mm2": SIZE * SIZE,
    }
    for p in problems:
        print("FAIL", p)
    for gap, a, b in sorted(worst):
        print("TIGHT %.2f mm %s %s" % (gap, a, b))
    print(json.dumps(report, ensure_ascii=False))
    if problems:
        raise SystemExit("%d placement problems; the board was NOT touched" % len(problems))
    return report


# ----------------------------------------------------------------------- place
def place():
    from pcb_agent_mcp.edit import plan as P

    parts, pad_nets = netlist()
    ops = []
    for ref in sorted(PLACEMENT, key=lambda r: (r[0], int(r[1:]) if r[1:].isdigit() else 0)):
        x, y, rot = PLACEMENT[ref]
        lib_id = parts[ref]["footprint"]
        lib, name = lib_id.split(":", 1)
        src = os.path.join(LIB, name + ".kicad_mod")
        if not os.path.isfile(src):
            src = os.path.join(KICAD_FP, lib + ".pretty", name + ".kicad_mod")
        ops.append({"op": "add_footprint", "ref": ref, "lib_id": lib_id,
                    "footprint_file": src, "x": x, "y": y, "rot": rot,
                    "layer": "F.Cu", "value": parts[ref]["value"],
                    "pad_nets": pad_nets.get(ref, {}), "create_nets": True})
    plan = P.propose(BOARD, ops)
    blocking = plan.get("blocking") or []
    if blocking:
        for b in blocking:
            print("BLOCKED", json.dumps(b, ensure_ascii=False)[:300])
        raise SystemExit("placement is blocking; the board was NOT touched")
    print("plan", plan["plan_id"], "ops", len(ops),
          "warnings", json.dumps(plan.get("warnings") or [], ensure_ascii=False)[:300])
    res = P.apply_plan(plan["plan_id"], confirm=True,
                       options={"allow_new_violations": True})
    print("applied: gates",
          json.dumps(res.get("gates_passed"), ensure_ascii=False),
          "errors", json.dumps(res.get("errors") or [], ensure_ascii=False)[:300])
    return res


def check(path=BOARD):
    """What is actually on the board now, plus what KiCad says about it."""
    from pcb_agent_mcp.fidelity import engine as E
    from pcb_agent_mcp.fidelity import kicad_doc as KD
    S = _sexpr()
    doc = KD.KiCadDocument.from_path(path)
    refs = []
    for fp in doc.items("footprint"):
        for prop in S.children(fp, "property"):
            if S.arg_text(prop, 1) == "Reference":
                refs.append(S.arg_text(prop, 2))
    st = E.stats(path)
    drc = E.drc(path)
    print(json.dumps({"footprints": len(refs),
                      "refs_not_placed": sorted(r for r in refs if r not in PLACEMENT),
                      "refs_missing": sorted(r for r in PLACEMENT if r not in refs),
                      "nets": len(doc.items("net")) - 1,
                      "board": {k: (st.get("board") or {}).get(k)
                                for k in ("width", "height", "has_outline")},
                      "drc_violations": drc.get("violations_total"),
                      "drc_by_type": drc.get("by_type")}, ensure_ascii=False, indent=2))


def main(argv=None):
    ap = argparse.ArgumentParser()
    g = ap.add_mutually_exclusive_group(required=True)
    g.add_argument("--audit", action="store_true")
    g.add_argument("--place", action="store_true")
    g.add_argument("--check", action="store_true")
    a = ap.parse_args(argv)
    if a.audit:
        audit()
    elif a.place:
        audit()
        place()
    else:
        check()
    return 0


if __name__ == "__main__":
    sys.exit(main())
