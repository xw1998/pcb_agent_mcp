"""The RK3506 core board schematic, asked of the schematic write path.

Nothing here is a route, a placement on copper, or a decision about copper: this
places **library symbols** and **local net labels** so that KiCad's own netlister
reproduces the ball-to-net table `tools/gen_netmap.py` froze.

    python tools/gen_som_sch.py --castellation   # write lib/SOM_CASTELLATION_140
    python tools/gen_som_sch.py --apply          # new_file -> propose -> apply
    python tools/gen_som_sch.py --check          # netlist on disk vs the CSV

Why labels on pin tips and no drawn wires: the SoC is a 333-ball BGA and its
symbol (EasyEDA -> lib/RK3506.kicad_sym, converted by tools/gen_lceda_symbol.py)
draws the pins in three lanes (x = +-20.32, +-83.82, +-111.76). A wire from every
pin would cross other pins. A local label placed exactly on a pin's connection
point joins that pin to every same-named label on the sheet - and KiCad's netlister
is the evidence that it did, not this file's opinion.

The castellation (J1) is 140 half-holes: not a purchased part, so no library has
it, so `castellation_symbol()` writes one from `som_pinout_140.csv` in the same
generated-symbol style as the EasyEDA conversions in lib/.

Power: `netmap_terminals.csv` models a regulator as a single terminal named after
its rail. A schematic may not: a rail whose source pin does not exist is a rail no
ERC can check. So this file also places the converter complement (inductor, input
and output capacitors, feedback divider, bootstrap capacitor) with values computed
from the datasheet numbers docs/144 already froze (Vref 0.6 V, fsw 500 kHz) and
from design values that are named as design values. Per-pin decoupling for the 31
supply balls is NOT here - docs/146 registers that as the next batch.
"""
import argparse
import csv
import json
import os
import re
import sys
import tempfile

HERE = os.path.dirname(os.path.abspath(__file__))
REPO = os.path.dirname(HERE)
EXAMPLES = os.path.join(REPO, "examples", "rk3506_som")
LIB = os.path.join(EXAMPLES, "lib")
sys.path.insert(0, os.path.join(REPO, "src"))

PAPER = "A1"
LABEL_SIZE = 1.0            # mm; the densest pitch on the sheet is 2.54 mm

DEVICES = {                 # CSV device name -> (reference, library, symbol)
    "SoC": ("U1", "RK3506.kicad_sym", "RK3506J"),
    "DDR": ("U2", "MT41K256M16.kicad_sym", "MT41K256M16"),
    "NAND": ("U3", "W25N01GVZEIG.kicad_sym", "W25N01GVZEIG"),
    "XTAL24": ("Y1", "X322524MSB4SI.kicad_sym", "X322524MSB4SI"),
    "RESET": ("SW1", "SKRPACE010.kicad_sym", "SKRPACE010"),
}
VALUES = {"SoC": "RK3506J", "DDR": "MT41K256M16HA-125:E",
          "NAND": "W25N01GVZEIG", "XTAL24": "24 MHz", "RESET": "RESET"}
DEVICE_OF_REF = {v[0]: k for k, v in DEVICES.items()}
DEVICE_OF_REF["J1"] = "STAMP"

POSITIONS = {
    "U1": (330.0, 300.0),
    "U2": (540.0, 300.0),
    "U3": (640.0, 120.0),
    "Y1": (640.0, 175.0),
    "SW1": (740.0, 175.0),
    "J1": (95.0, 300.0),
}

# one SY8113BADC per rail a switcher makes (docs/144). The last four numbers are
# overrides for the feedback divider (Rtop, Rbot in ohms); None means "computed".
BUCKS = [
    ("U4", "+0V9_CPU", (640.0, 230.0)),
    ("U5", "+0V9_LOGIC", (640.0, 300.0)),
    ("U6", "+1V35_DDR", (640.0, 370.0)),
    ("U7", "+3V3", (640.0, 440.0)),
]
LDO = ("U8", "+1V8_ANA", (470.0, 470.0))
BEADS = [("FB1", "+0V9_PMU", (760.0, 318.0)), ("FB2", "+0V9_ANA", (760.0, 352.0))]
VREF_RAIL, VREF_AT = "+0V675_REF", (760.0, 452.0)

KICAD_SYMBOLS = r"D:\KiCad\10.0\share\kicad\symbols"
PASSIVE = {                 # kind -> (lib_id, library file, symbol name, footprint)
    "R": ("Device:R", os.path.join(KICAD_SYMBOLS, "Device.kicad_sym"), "R",
          "Resistor_SMD:R_0402_1005Metric"),
    "C": ("Device:C", os.path.join(KICAD_SYMBOLS, "Device.kicad_sym"), "C",
          "Capacitor_SMD:C_0402_1005Metric"),
    "L": ("Device:L", os.path.join(KICAD_SYMBOLS, "Device.kicad_sym"), "L",
          "Inductor_SMD:L_Cenker_CKCS4020"),
    "FB": ("Device:L", os.path.join(KICAD_SYMBOLS, "Device.kicad_sym"), "L",
           "Inductor_SMD:L_0603_1608Metric"),
}
PASSIVE_PINS = {"1": (0.0, 3.81, 270), "2": (0.0, -3.81, 90)}   # Device:R/C/L

VREF_IC = 0.600     # SY8113BADC feedback reference (docs/144, external evidence)
FSW = 500e3         # switching frequency                          (docs/144)
VIN = 5.0           # nominal input rail
RIPPLE_I = 0.30     # DESIGN: peak-to-peak inductor ripple / load current
RIPPLE_V = 0.01     # DESIGN: output ripple / Vout
DVIN = 0.05         # DESIGN: allowed input ripple / Vin
LOADS = {"+0V9_CPU": 1.2, "+0V9_LOGIC": 0.8, "+1V35_DDR": 0.8, "+3V3": 1.6}
E96 = [1.00, 1.02, 1.05, 1.07, 1.10, 1.13, 1.15, 1.18, 1.21, 1.24, 1.27,
       1.30, 1.33, 1.37, 1.40, 1.43, 1.47, 1.50, 1.54, 1.58, 1.62, 1.65, 1.69,
       1.74, 1.78, 1.82, 1.87, 1.91, 1.96, 2.00, 2.05, 2.10, 2.15, 2.21, 2.26,
       2.32, 2.37, 2.43, 2.49, 2.55, 2.61, 2.67, 2.74, 2.80, 2.87, 2.94, 3.01,
       3.09, 3.16, 3.24, 3.32, 3.40, 3.48, 3.57, 3.65, 3.74, 3.83, 3.92, 4.02,
       4.12, 4.22, 4.32, 4.42, 4.53, 4.64, 4.75, 4.87, 4.99, 5.11, 5.23, 5.36,
       5.49, 5.62, 5.76, 5.90, 6.04, 6.19, 6.34, 6.49, 6.65, 6.81, 6.98, 7.15,
       7.32, 7.50, 7.68, 7.87, 8.06, 8.25, 8.45, 8.66, 8.87, 9.09, 9.31, 9.53,
       9.76]
CAPS = [1.0, 2.2, 4.7, 10.0, 22.0, 47.0]     # uF; the values that are on a reel


# ------------------------------------------------------------------- reading in
def read_csv(name):
    with open(os.path.join(EXAMPLES, name), encoding="utf-8", newline="") as fh:
        return list(csv.DictReader(fh))


def symbol_pins(path, name):
    """{number: (x, y, rotation, pin_name)} for one symbol of a .kicad_sym file."""
    with open(path, encoding="utf-8") as fh:
        lines = fh.read().splitlines()
    start = None
    for i, line in enumerate(lines):
        if "(symbol " in line and ('"%s"' % name) in line:
            start, indent = i, len(line) - len(line.lstrip())
            break
    if start is None:
        raise SystemExit("%s has no symbol %r" % (os.path.basename(path), name))
    block = [lines[start]]
    for line in lines[start + 1:]:
        if "(symbol " in line and len(line) - len(line.lstrip()) <= indent:
            break
        block.append(line)
    # KiCad's own libraries put one item per line, ours put a whole pin on one.
    # Both are legal, so the parse must not depend on which one it is reading.
    text = re.sub(r"\n[ \t]*", " ", "\n".join(block))
    out = {}
    for m in re.finditer(r"\(pin [a-z_]+ [a-z_]+ \(at ([-\d.]+) ([-\d.]+) (\d+)\)"
                         r" \(length ([\d.]+)\)", text):
        tail = text[m.end():m.end() + 400]
        num = re.search(r'\(number "([^"]*)"', tail)
        nm = re.search(r'\(name "([^"]*)"', tail)
        out[num.group(1)] = (float(m.group(1)), float(m.group(2)),
                             int(m.group(3)), nm.group(1) if nm else "")
    return out


def tip(origin, pin):
    """Pin connection point in sheet millimetres (sheet Y grows downwards)."""
    return (round(origin[0] + pin[0], 4), round(origin[1] - pin[1], 4))


def outward(rotation):
    return {0: (-1, 0), 90: (0, 1), 180: (1, 0), 270: (0, -1)}[(rotation) % 360]


def label_rotation(rotation):
    dx, dy = outward(rotation)
    if dx > 0:
        return 0
    if dx < 0:
        return 180
    return 270 if dy > 0 else 90


GRID = 1.27          # KiCad's default schematic connection grid

def on_grid(v):
    """Nearest connection-grid value; ERC flags every pin that misses it."""
    return round(round(float(v) / GRID) * GRID, 4)


def op_label(x, y, text, rot):
    return {"op": "add_label", "text": text, "x": on_grid(x), "y": on_grid(y),
            "rotation": rot, "size": LABEL_SIZE}


def op_symbol(ref, lib_id, lib_path, name, x, y, value, footprint=None):
    op = {"op": "add_symbol", "lib_id": lib_id, "library": lib_path,
          "symbol": name, "x": on_grid(x), "y": on_grid(y),
          "reference": ref, "value": value}
    if footprint:
        op["footprint"] = footprint
    return op


# ------------------------------------------------------------ castellation J1
J1_PITCH, J1_TOP, J1_X = 2.54, 88.9, 10.16


def j1_pin(n):
    x = -J1_X if n <= 70 else J1_X
    rot = 0 if n <= 70 else 180
    y = J1_TOP - J1_PITCH * ((n - 1) % 70)
    return (x, y, rot)


def write_castellation_symbol(path=None):
    """Write the 140-hole connector symbol from som_pinout_140.csv.

    Two columns of 70, body 10.16 mm wide and 185 mm tall, pins 2.54 mm apart,
    pin names hidden (the label on the pin already says the net, and two lines of
    text on a 2.54 mm pitch is unreadable).
    """
    path = path or os.path.join(LIB, "SOM_CASTELLATION_140.kicad_sym")
    rows = read_csv("som_pinout_140.csv")
    nums = [int(r["pin"]) for r in rows]
    if len(rows) != 140 or sorted(nums) != list(range(1, 141)):
        raise SystemExit("som_pinout_140.csv is not 140 rows numbered 1..140")
    by_num = {int(r["pin"]): r for r in rows}
    out = ['(kicad_symbol_lib',
           '  (version 20241209)',
           '  (generator "pcb_agent_mcp")',
           '  (generator_version "0.1.8")',
           '  (symbol "SOM_CASTELLATION_140"',
           '    (property "Reference" "J" (at 0 96.14 0)'
           ' (effects (font (size 1.27 1.27)) (justify left)))',
           '    (property "Value" "SOM_CASTELLATION_140" (at 0 93.64 0)'
           ' (effects (font (size 1.27 1.27)) (justify left)))',
           '    (property "Footprint" "RK3506:SOM_CASTELLATION_140" (at 0 0 0)'
           ' (effects (font (size 1.27 1.27)) (hide yes)))',
           '    (property "Datasheet" "" (at 0 0 0)'
           ' (effects (font (size 1.27 1.27)) (hide yes)))',
           '    (property "Description" "140 castellation half-holes on the board'
           ' edge; not a purchased part - pads only, no BOM line" (at 0 0 0)'
           ' (effects (font (size 1.27 1.27)) (hide yes)))',
           '    (pin_names (offset 0.508) (hide yes))',
           '    (symbol "SOM_CASTELLATION_140_0_1"',
           '      (rectangle (start -5.08 91.44) (end 5.08 -91.44)'
           ' (stroke (width 0.254) (type default)) (fill (type background)))']
    for n in range(1, 141):
        x, y, rot = j1_pin(n)
        name = (by_num[n]["net"] or "GND").replace('"', "")
        if not name:
            raise SystemExit("hole %d has no net" % n)
        out.append('      (pin passive line (at %.2f %.2f %d) (length 5.08)'
                   % (x, y, rot))
        out.append('        (name "%s" (effects (font (size 1.0 1.0))))' % name)
        out.append('        (number "%d" (effects (font (size 1.0 1.0)))))' % n)
    out += ['    )', '  )', ')', '']
    with open(path, "w", encoding="utf-8", newline="\n") as fh:
        fh.write("\n".join(out))
    return path


# ----------------------------------------------------------------- power values
def rail_volts():
    out = {}
    for row in read_csv("power_rails.csv"):
        try:
            out[row["rail"]] = float(row["typ_v"])
        except (TypeError, ValueError):
            continue
    return out


def snap_si(target, series=None):
    """Snap a positive number to an E-series mantissa, keeping its decade."""
    series = series or E96
    decade = 1.0
    while target >= 10.0:
        target /= 10.0
        decade *= 10.0
    while target < 1.0 and target > 0:
        target *= 10.0
        decade /= 10.0
    return min(series, key=lambda e: abs(e - target)) * decade


def snap_cap(target):
    """Next standard capacitor value on or above `target`, in uF."""
    for c in CAPS:
        if c >= target * 0.95:
            return c
    return CAPS[-1]


def buck_values(rail, volts):
    """(inductor uH, Cin uF, Cout uF, Rtop ohm, Rbot ohm, vout_actual)."""
    load = LOADS[rail]
    vout = volts[rail]
    duty = vout / VIN
    d_i = RIPPLE_I * load
    l_uh = (VIN - vout) * vout / (VIN * FSW * d_i) * 1e6
    i_rms_cin = load * (duty * (1 - duty)) ** 0.5
    cin = i_rms_cin / (FSW * DVIN * VIN) * 1e6
    cout = d_i / (8 * FSW * RIPPLE_V * vout) * 1e6
    rbot = 10e3
    rtop = snap_si(rbot * (vout / VREF_IC - 1.0))
    vout_actual = VREF_IC * (1 + rtop / rbot)
    return (round(snap_si(l_uh), 2), snap_cap(cin), snap_cap(cout),
            int(round(rtop)), int(rbot), round(vout_actual, 4))


def si(value):
    """Value string in the SI prefix notation KiCad users read."""
    if value >= 1e6:
        return "%gM" % (value / 1e6)
    if value >= 1e3:
        return "%gk" % (value / 1e3)
    return "%g" % value


# --------------------------------------------------------------------- the plan
def build_plan():
    """Every operation, and the labels each component pin must carry."""
    ops, labels, notes = [], [], []
    tips = {}                      # (x, y) -> (ref, pin, net)  collision guard

    def put_label(origin, pin, net, ref, num):
        pt = tip((on_grid(origin[0]), on_grid(origin[1])), pin)
        if pt in tips:
            raise SystemExit("two pins share the point %s: %s.%s and %s.%s"
                             % (pt, ref, num, tips[pt][0], tips[pt][1]))
        tips[pt] = (ref, num, net)
        labels.append(op_label(pt[0], pt[1], net, label_rotation(pin[2])))

    # ---- library devices, every terminal from netmap_terminals.csv
    by_device = {}
    for row in read_csv("netmap_terminals.csv"):
        by_device.setdefault(row["device"], []).append(row)
    for dev, (ref, lib_file, sym) in sorted(DEVICES.items()):
        lib_path = os.path.join(LIB, lib_file)
        pins = symbol_pins(lib_path, sym)
        x, y = POSITIONS[ref]
        nick = os.path.splitext(lib_file)[0]
        ops.append(op_symbol(ref, "%s:%s" % (nick, sym), lib_path, sym, x, y,
                             VALUES.get(dev, sym)))
        have = {r["pin"] for r in by_device[dev]}
        missing = sorted(have - set(pins))
        if missing:
            raise SystemExit("%s: the symbol has no pin for ball(s) %s"
                             % (ref, missing))
        for row in by_device[dev]:
            put_label((x, y), pins[row["pin"]], row["net"], ref, row["pin"])
        extra = sorted(set(pins) - have)
        for num in extra:
            pt = tip((x, y), pins[num])
            tips[pt] = (ref, num, None)
            ops.append({"op": "add_no_connect",
                         "x": on_grid(pt[0]), "y": on_grid(pt[1])})
            notes.append("%s pin %s (%s) is not in the datasheet ball table: "
                         "no-connect flag, not a net" % (ref, num, pins[num][3]))

    # ---- J1, the 140 half-holes
    jx, jy = POSITIONS["J1"]
    ops.append(op_symbol("J1", "SOM_CASTELLATION_140:SOM_CASTELLATION_140",
                         os.path.join(LIB, "SOM_CASTELLATION_140.kicad_sym"),
                         "SOM_CASTELLATION_140", jx, jy, "SOM_CASTELLATION_140"))
    for row in by_device["STAMP"]:
        n = int(row["pin"])
        x, y, rot = j1_pin(n)
        put_label((jx, jy), (x, y, rot), row["net"], "J1", str(n))

    # ---- switchers and their complement
    volts = rail_volts()
    for i, (ref, rail, (bx, by)) in enumerate(BUCKS, start=1):
        l_uh, cin, cout, rtop, rbot, vout = buck_values(rail, volts)
        notes.append("%s %s: L %.1fuH, Cin %.1fuF, Cout %.1fuF, FB %s/%s -> "
                     "%.4f V (design values, docs/146)" %
                     (ref, rail, l_uh, cin, cout, si(rtop), si(rbot), vout))
        lib = os.path.join(LIB, "SY8113BADC.kicad_sym")
        ops.append(op_symbol(ref, "SY8113BADC:SY8113BADC", lib, "SY8113BADC",
                             bx, by, "SY8113BADC"))
        pins = symbol_pins(lib, "SY8113BADC")
        nets = {"5": "+5V_IN", "4": "+5V_IN", "2": "GND",
                "6": "SW_%s" % ref, "3": "FB_%s" % ref, "1": "BS_%s" % ref}
        for num, net in nets.items():
            put_label((bx, by), pins[num], net, ref, num)
        cols = [bx + 45, bx + 63, bx + 81, bx + 99]
        parts = [
            ("L%d" % i, "L", "%.1fuH" % l_uh, (cols[0], by),
             ("SW_%s" % ref, rail)),
            ("C%d" % (2 * i - 1), "C", "%.1fuF" % cin, (cols[1], by - 10),
             ("+5V_IN", "GND")),
            ("C%d" % (8 + i), "C", "100nF", (cols[1], by + 10),
             ("BS_%s" % ref, "SW_%s" % ref)),
            ("C%d" % (2 * i), "C", "%.1fuF" % cout, (cols[2], by),
             (rail, "GND")),
            ("R%d" % (2 * i - 1), "R", si(rtop), (cols[3], by - 10),
             (rail, "FB_%s" % ref)),
            ("R%d" % (2 * i), "R", si(rbot), (cols[3], by + 10),
             ("FB_%s" % ref, "GND")),
        ]
        for pref, kind, value, at, netpair in parts:
            ops.append(op_symbol(pref, PASSIVE[kind][0], PASSIVE[kind][1],
                                 PASSIVE[kind][2], at[0], at[1], value,
                                 PASSIVE[kind][3]))
            for num, net in zip(("1", "2"), netpair):
                put_label(at, PASSIVE_PINS[num], net, pref, num)

    # ---- the 1.8 V LDO
    ref, rail, (bx, by) = LDO
    lib = os.path.join(LIB, "AMS1117-1.8.kicad_sym")
    ops.append(op_symbol(ref, "AMS1117-1.8:AMS1117-1.8", lib, "AMS1117-1.8",
                         bx, by, "AMS1117-1.8"))
    pins = symbol_pins(lib, "AMS1117-1.8")
    for num, net in {"3": "+3V3", "1": "GND", "4": rail, "2": rail}.items():
        put_label((bx, by), pins[num], net, ref, num)
    for pref, at, value, net1 in [("C17", (bx + 42, by - 10), "10uF", "+3V3"),
                                  ("C18", (bx + 42, by + 10), "10uF", rail)]:
        ops.append(op_symbol(pref, PASSIVE["C"][0], PASSIVE["C"][1],
                             PASSIVE["C"][2], at[0], at[1], value, PASSIVE["C"][3]))
        for num, net in zip(("1", "2"), (net1, "GND")):
            put_label(at, PASSIVE_PINS[num], net, pref, num)

    # ---- bead-isolated analogue rails
    for ref, rail, (bx, by) in BEADS:
        ops.append(op_symbol(ref, PASSIVE["FB"][0], PASSIVE["FB"][1],
                             PASSIVE["FB"][2], bx, by, "600R@100MHz",
                             PASSIVE["FB"][3]))
        for num, net in zip(("1", "2"), ("+0V9_LOGIC", rail)):
            put_label((bx, by), PASSIVE_PINS[num], net, ref, num)
    for pref, rail, at in [("C14", "+0V9_PMU", (bx + 30, 306)),
                           ("C15", "+0V9_PMU", (bx + 30, 330)),
                           ("C16", "+0V9_ANA", (bx + 30, 358))]:
        ops.append(op_symbol(pref, PASSIVE["C"][0], PASSIVE["C"][1],
                             PASSIVE["C"][2], at[0], at[1], "10uF",
                             PASSIVE["C"][3]))
        for num, net in zip(("1", "2"), (rail, "GND")):
            put_label(at, PASSIVE_PINS[num], net, pref, num)

    # ---- the DDR reference divider (1k/1k + 100 nF, docs/144)
    bx, by = VREF_AT
    for pref, kind, value, at, netpair in [
            ("R9", "R", "1k", (bx, by), ("+1V35_DDR", VREF_RAIL)),
            ("R10", "R", "1k", (bx + 22, by), (VREF_RAIL, "GND")),
            ("C13", "C", "100nF", (bx + 44, by), (VREF_RAIL, "GND"))]:
        ops.append(op_symbol(pref, PASSIVE[kind][0], PASSIVE[kind][1],
                             PASSIVE[kind][2], at[0], at[1], value,
                             PASSIVE[kind][3]))
        for num, net in zip(("1", "2"), netpair):
            put_label(at, PASSIVE_PINS[num], net, pref, num)
    for lab in labels:                     # labels were collected apart from
        ops.append(lab)                    # the symbols; they go to batch 2
    for o in ops:                          # a pin that misses the grid leaves
        if "x" in o:                       # an ERC warning and a dangling end
            assert on_grid(o["x"]) == o["x"] and on_grid(o["y"]) == o["y"], o
    return ops, notes, plan_stat(ops)

def plan_stat(ops):
    """The two counts that MUST match what KiCad reads back off the sheet.

    They are computed here, from the libraries and the frozen table, in a path
    that never looks at the generated file. `check()` then compares them with
    KiCad's own numbers: a mismatch means the sheet is not what was planned,
    which is exactly the answer this tool refuses to guess.
    """
    cache, symbol_pins_total = {}, 0
    for o in ops:
        if o["op"] != "add_symbol":
            continue
        key = (o["library"], o["symbol"])
        if key not in cache:
            cache[key] = set(symbol_pins(o["library"], o["symbol"]))
        symbol_pins_total += len(cache[key])
    no_connects = sum(1 for o in ops if o["op"] == "add_no_connect")
    table = [r for r in read_csv("netmap_terminals.csv") if r["device"] != "RAIL"]
    spots = {}
    for o in ops:
        if o["op"] == "add_label":
            spots.setdefault(o["text"], []).append((o["x"], o["y"]))
    alone = sorted(n for n, p in spots.items() if len(p) == 1)
    return {
        "table_pins": len(table),
        "symbol_pins": symbol_pins_total,
        "outside_table": symbol_pins_total - len(table),
        "no_connect_flags": no_connects,
        "single_ended_nets": len(_single_ended(table)),
        "plan_isolated_labels": len(alone),
        "plan_isolated_at": sorted(spots[n][0] for n in alone),
        "plan_isolated_nets": alone,
    }

def _single_ended(table):
    """Nets with exactly one terminal: they can only ever be isolated labels."""
    per = {}
    for r in table:
        per[r["net"]] = per.get(r["net"], 0) + 1
    return sorted(n for n, c in per.items() if c == 1)


def positions_outside(ops):
    """Whatever the planner put outside an A1 sheet, so it is said not hidden."""
    w, h = 841.0, 594.0
    bad = [o for o in ops if "x" in o and (o["x"] < 0 or o["x"] > w
                                           or o["y"] < 0 or o["y"] > h)]
    return bad


# ------------------------------------------------------------------------ apply
def apply(path, paper=PAPER):
    from pcb_agent_mcp.edit import sch_plan as SP

    ops, notes, stat = build_plan()
    out = positions_outside(ops)
    if out:
        raise SystemExit("%d object(s) would land outside %s: %s"
                         % (len(out), paper, out[:3]))
    symbols = [o for o in ops if o["op"] == "add_symbol"]
    rest = [o for o in ops if o["op"] != "add_symbol"]
    print("plan: %d symbols, %d labels/no-connects" % (len(symbols), len(rest)))
    print("plan stat: %s" % json.dumps(stat, ensure_ascii=False, sort_keys=True))
    for n in notes:
        print("  note:", n)

    if not os.path.exists(path):
        SP.new_file(path, paper=paper, title="RK3506 core board", confirm=True)
        print("new_file: %s" % path)
    else:
        print("reusing existing sheet: %s" % path)

    plan = SP.propose(path, symbols, options={"engine": False})
    res = SP.apply_plan(plan["plan_id"], confirm=True,
                        options={"allow_new_violations": True, "engine": False})
    print("batch 1 (symbols) written; gates %s" % ",".join(res["gates_passed"]))

    plan = SP.propose(path, rest, options={"engine": True})
    print("batch 2 propose: blocking=%s warnings=%s"
          % (plan["blocking"], [w.get("gate") for w in plan["warnings"]]))
    res = SP.apply_plan(plan["plan_id"], confirm=True,
                        options={"allow_new_violations": True})
    print("batch 2 (labels) written; gates %s" % ",".join(res["gates_passed"]))
    erc = res["gates"]["erc"]
    print("ERC new=%s resolved=%s after_total=%s"
          % (erc.get("new_total"), erc.get("resolved_total"),
             erc.get("after_total")))
    for v in (erc.get("new") or [])[:12]:
        print("   new ERC: %s %s %s" % (v["severity"], v["type"], v["description"]))
    return res


# ------------------------------------------------------------------------ check
TEXT_OVERLAP_MM = 1.5   # below this in BOTH axes the boxes are just the
                        # ordinary pin-number / net-label adjacency

def text_overlaps(path):
    """What the sheet actually prints: pairs of text boxes that sit on each other.

    The boxes come from KiCad's own PDF export (`engine.sch_pdf`), so this reads
    the text KiCad drew. Optional instrument: with no pymupdf installed the gate
    reports `skipped` rather than pretending it measured zero.

    Measured on this sheet: before the symbol fields were lifted out of the
    drawing, the worst pair was the LDO's value text 3.87 mm across its own GND
    pin name; before the stacked VSS balls were pushed four grids out, the ball
    number sat 1.84 mm across the neighbouring GND label.
    """
    from pcb_agent_mcp.fidelity import engine as E
    try:
        import pymupdf
    except ImportError:
        return {"status": "skipped", "reason": "pymupdf not installed"}
    tmp = tempfile.mkdtemp(prefix="sch_text_")
    pdf = os.path.join(tmp, "sheet.pdf")
    plot = E.sch_pdf(path, pdf)
    if plot.get("status") != "ok":
        return {"status": "error", "reason": plot.get("reason_code")}
    doc = pymupdf.open(pdf)
    spans = [(s["bbox"], s["text"].strip())
             for block in doc[0].get_text("dict")["blocks"]
             for line in block.get("lines") or []
             for s in line["spans"] if s["text"].strip()]
    mm = 25.4 / 72.0
    worst, pair_at, over = (0.0, 0.0), ("", ""), 0
    for i, (a, ta) in enumerate(spans):
        for b, tb in spans[i + 1:]:
            w = min(a[2], b[2]) - max(a[0], b[0])
            h = min(a[3], b[3]) - max(a[1], b[1])
            if w <= 0.6 and h <= 0.6:
                continue
            w, h = w * mm, h * mm
            if w > TEXT_OVERLAP_MM and h > TEXT_OVERLAP_MM:
                over += 1
            if w * h > worst[0] * worst[1]:
                worst, pair_at = (w, h), (ta, tb)
    doc.close()
    return {"status": "ok", "spans": len(spans),
            "pairs_on_each_other": over,
            "worst_axes_mm": [round(v, 2) for v in worst],
            "worst_pair": list(pair_at)}

def check(path):
    """KiCad's netlist on disk, compared pin by pin with the frozen table."""
    from pcb_agent_mcp.fidelity import engine as E

    nl = E.sch_netlist(path)
    if nl.get("status") != "ok":
        raise SystemExit("netlister: %s" % nl.get("reason_code"))
    got = {}
    for net in nl["nets"]:
        name = (net["name"] or "").lstrip("/")
        for ref, pin in net["nodes"]:
            got[(ref, str(pin))] = name
    expected = {}
    for row in read_csv("netmap_terminals.csv"):
        if row["device"] == "RAIL":
            continue                       # a schema, not a pin (see docstring)
        ref = "J1" if row["device"] == "STAMP" else DEVICES[row["device"]][0]
        expected[(ref, str(row["pin"]))] = row["net"]
    bad, extra = [], []
    for key, want in sorted(expected.items()):
        have = got.get(key)
        if have is None:
            bad.append({"pin": "%s.%s" % key, "want": want, "got": "absent"})
        elif have != want:
            bad.append({"pin": "%s.%s" % key, "want": want, "got": have})
    for key, have in sorted(got.items()):
        if key not in expected and not have.startswith("unconnected-"):
            extra.append({"pin": "%s.%s" % key, "net": have})
    rails = {n["name"].lstrip("/") for n in nl["nets"]}
    want_rails = {r["rail"] for r in read_csv("power_rails.csv")} | {VREF_RAIL}
    missing_rails = sorted(want_rails - rails)

    # ---- cross-checks: what the plan predicted vs what KiCad reports back
    # Both sides are computed independently. The plan never reads the file it
    # generated, and ERC never reads the table. A mismatch means the sheet on
    # disk is not the frozen design, and that is an answer, not a nit.
    ops, _notes, stat = build_plan()
    erc = E.sch_erc(path)
    if erc.get("status") != "ok":
        raise SystemExit("erc engine: %s" % erc.get("reason_code"))
    by_type = dict(erc.get("by_type") or {})
    unexpected = sorted(t for t in by_type if t != "isolated_pin_label")
    fails = []
    if len(expected) != stat["table_pins"]:
        fails.append("plan has %d table pins, the netlist was asked about %d"
                     % (stat["table_pins"], len(expected)))
    if len(got) - len(expected) != stat["symbol_pins"] - stat["table_pins"]:
        fails.append("netlist carries %d pins, the libraries say %d"
                     % (len(got), stat["symbol_pins"]))
    if len(extra) != stat["outside_table"] - stat["no_connect_flags"]:
        fails.append("%d pins outside the table, plan says %d minus %d no-connects"
                     % (len(extra), stat["outside_table"],
                        stat["no_connect_flags"]))
    if unexpected:
        fails.append("ERC types beyond isolated_pin_label: %s" % unexpected)
    # Where KiCad found an isolated label, and where the plan put a lone label,
    # are two independent lists of sheet coordinates; they must be the same
    # list. Comparing positions rather than the text keeps this working in any
    # UI language (the ERC item text is translated).
    erc_at = sorted((round(i["pos"]["x"], 2), round(i["pos"]["y"], 2))
                    for v in erc.get("violations") or []
                    for i in v.get("items") or [])
    plan_at = sorted((round(x, 2), round(y, 2))
                     for x, y in stat["plan_isolated_at"])
    if erc_at != plan_at:
        only_erc = sorted(set(erc_at) - set(plan_at))
        only_plan = sorted(set(plan_at) - set(erc_at))
        fails.append("isolated labels disagree: ERC has %d at %s, plan has %d "
                     "at %s" % (len(erc_at), only_erc[:4], len(plan_at),
                                only_plan[:4]))
    # A net the table gives one terminal may still reach a second pin, but only
    # if that pin is outside the frozen table (the analogue beads do exactly
    # that). Nothing may fall off the list silently.
    nets_outside = {e["net"] for e in extra}
    lost = sorted(n for n in _single_ended(
        [r for r in read_csv("netmap_terminals.csv") if r["device"] != "RAIL"])
        if n not in stat["plan_isolated_nets"] and n not in nets_outside)
    if lost:
        fails.append("table single-ended nets that are neither isolated nor "
                     "terminated outside the table: %s" % lost)
    text = text_overlaps(path)
    if text.get("status") == "ok" and text["pairs_on_each_other"]:
        fails.append("two pieces of text sit on each other: %s vs %s, "
                     "%.2f x %.2f mm"
                     % tuple(text["worst_pair"] + text["worst_axes_mm"]))

    report = {
        "netlist_engine": nl.get("engine"),
        "components": nl.get("components_total"), "nets": nl.get("nets_total"),
        "pins_expected": len(expected), "pins_found": len(got),
        "pins_wrong_or_absent": len(bad), "pins_extra_outside_the_table": len(extra),
        "rails_missing": missing_rails,
        "plan": stat,
        "erc_violations": erc.get("violations_total"),
        "erc_by_type": by_type,
        "text": text,
        "mismatches": bad[:25], "extra": extra[:25],
        "cross_checks_failed": fails,
    }
    print(json.dumps(report, ensure_ascii=False, indent=2))
    if bad or missing_rails or fails:
        raise SystemExit("the schematic is NOT the frozen table: %s"
                         % (bad[:3] or missing_rails or fails))
    return report


def main(argv=None):
    ap = argparse.ArgumentParser()
    ap.add_argument("--apply", action="store_true")
    ap.add_argument("--check", action="store_true")
    ap.add_argument("--castellation", action="store_true")
    ap.add_argument("--path", default=os.path.join(EXAMPLES, "rk3506_som.kicad_sch"))
    a = ap.parse_args(argv)
    if a.castellation or a.apply:
        write_castellation_symbol()
    if a.apply:
        apply(a.path)
    if a.check:
        check(a.path)


if __name__ == "__main__":
    main()
