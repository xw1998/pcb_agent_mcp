"""Turn the ball/pad tables into one net list: every pin, one net, one owner.

Nothing here is a route, a placement, or a copper decision. This tool only says
**which pin is on which net**, so that a schematic (and later a layout) can be
checked against a table instead of against memory.

The inputs are the tables this repository already froze:

* ``pinmap_rk3506j.csv``      332 named SoC balls (Table 2-1 of the datasheet)
* ``power_balls.csv``         ball -> rail, from Table 2-2 / 3-2 (docs/144)
* ``power_rails.csv``         the 10 rails and who produces them (docs/144)
* ``ddr3l_signals.csv``       DDR3L signal -> SoC ball (docs/143 section 3)
* ``som_pinout_140.csv``      the 140 stamp holes, already assigned (0.1.2)
* ``lib/*.kicad_sym``         the pin *names* on the other side of each net

The output is two CSVs (``netmap_nets.csv`` and ``netmap_terminals.csv``).

Rules, each with an audit that fails loudly (exit code 2, no bytes written):

R1  every SoC ball is on exactly one net, and every SoC ball is covered;
R2  a DDR3L net is the *same* signal on both sides. The two vocabularies do not
    match by themselves (`DQS1P` vs `UDQS`, `CASN` vs `CAS#`, `A10` vs `A10/AP`),
    so the translation is a **declared table** (`DDR_SOC_TO_CHIP`) and an audit
    refuses any chip signal pin the table does not cover - that is what catches
    the datasheet's DQ1/DQ11 swap rather than a fuzzy string match;
R3  a chip-side power pin is on a rail, a chip-side NC pin is its own no-connect,
    and no chip pin is left over;
R4  every stamp hole has a net that some SoC pin provides; the ball printed on
    the row must exist in the pin map; a ball the design already ruled on (rail,
    internal output, crystal pin, NAND bus, no-connect) keeps that net and the
    stamp row may not rename it; and one ball may not be exported on two holes;
R5  a part with no host pin is *dropped and named*, never wired to something
    plausible: RK3506J has no dedicated 32 kHz pin (datasheet p.8 gives an
    internal LF RC, and the only external 32 K path is the CLK_32K mux,
    `RM_IO14`, which sits on N3 - already the exported SPI1_CSN0);
R6  an unexported, unclaimed signal ball becomes ``NC_<ball>`` and is refused if
    the stamp table exports it;
R7  every rail has a producer terminal (the converter/bead/divider that makes it)
    and every load; the `+5V_IN` rail has no producer because it comes from the
    stamp holes, and `+0V675_REF` must stay exactly half of `+1V35_DDR` because
    it is a VDDQ/2 divider node, not a converter output;
R8  a net needs two ends - except `nc` and `internal` nets, which are exactly
    their own pin (an internal output the chip drives must not acquire a driver).

Deliberately not decided here: decoupling capacitors, series termination,
pull-up/down values (except the two the datasheet forces), and every copper
question.
"""

from __future__ import annotations

import argparse
import csv
import os
import re
import sys

HERE = os.path.dirname(os.path.abspath(__file__))
EXAMPLES = os.path.join(os.path.dirname(HERE), "examples", "rk3506_som")

#: Stamp-hole power net name -> rail name. The stamp table was frozen in 0.1.2,
#: before the rails had names, so this is the one place the two vocabularies are
#: reconciled. Signal names are *not* aliased: the stamp name is the board-level
#: contract and stays the net name.
RAIL_ALIASES = {"5V_IN": "+5V_IN", "3V3_OUT": "+3V3", "GND": "GND"}

#: Rails that must appear on the stamp holes: a core board that cannot be fed
#: and cannot feed anything back is not a core board.
EXPORTED_RAILS = ("+5V_IN", "+3V3", "GND")

#: SoC DDR3L token -> chip pin name. One row per pair; every chip signal pin must
#: appear as a value, and every value must be unique (checked by `audit_names`).
DDR_SOC_TO_CHIP = {
    "A0": "A0", "A1": "A1", "A2": "A2", "A3": "A3", "A4": "A4", "A5": "A5",
    "A6": "A6", "A7": "A7", "A8": "A8", "A9": "A9", "A10": "A10/AP",
    "A11": "A11", "A12": "A12/BC#", "A13": "A13", "A14": "A14",
    "BA0": "BA0", "BA1": "BA1", "BA2": "BA2",
    "CASN": "CAS#", "RASN": "RAS#", "WEN": "WE#", "CSN": "CS#", "CKE": "CKE",
    "ODT": "ODT", "RESET": "RESET#", "CLKP": "CK", "CLKN": "CK#",
    # byte 0 = DQ0..DQ7 -> LDQS/LDM; byte 1 = DQ8..DQ15 -> UDQS/UDM
    "DQS0P": "LDQS", "DQS0N": "LDQS#", "DQS1P": "UDQS", "DQS1N": "UDQS#",
    "DM0": "LDM", "DM1": "UDM", "RZQ": "ZQ",
    "VREF_CA": "VREFCA", "VREF_DQ": "VREFDQ",
}
for _i in range(16):
    DDR_SOC_TO_CHIP["DQ%d" % _i] = "DQ%d" % _i

#: SoC DDR tokens the chosen device does not have a pin for. A 4 Gb x16 DRAM is
#: A0-A14 (15 row bits) plus BA0-BA2; there is no A15 pad. Declaring it here means
#: the audit fails if a future device *does* have one, instead of silently wiring
#: a ball to nothing.
DDR_SOC_NO_CHIP_PIN = {
    "A15": "4 Gb x16 DRAM has A0-A14 only: no A15 pad (chip pin list has none)",
}

#: Chip pins that are a rail, not a signal.
DDR_POWER_PINS = {"VDDQ": "+1V35_DDR", "VDD": "+1V35_DDR",
                  "VSS": "GND", "VSSQ": "GND"}

#: Chip pins that are their own node: VREF is driven by the divider (docs/144
#: 4.1), ZQ by its own resistor.
DDR_REF_PINS = {"VREFCA": "+0V675_REF", "VREFDQ": "+0V675_REF",
                "ZQ": "DDR_ZQ"}

#: NAND pad name -> net. The W25N01GV is a 3 V part, the EP is GND.
NAND_PIN_NETS = {"CS#": "FSPI_CSN", "CLK": "FSPI_CLK",
                 "DI(DQ0)": "FSPI_D0", "DO(DQ1)": "FSPI_D1",
                 "WP#(DQ2)": "FSPI_D2", "HOLD#(DQ3)": "FSPI_D3",
                 "VCC": "+3V3", "VSS": "GND", "EP": "GND"}

#: 24 MHz crystal: pads 1/3 are the two terminals, 2/4 are the case -> GND.
XTAL24_PIN_NETS = {"OSC1": "OSC_XIN", "OSC2": "OSC_XOUT", "GND": "GND"}

#: Reset key SKRPACE010: pads 1/2 are one node, 3/4 the other.
RESET_PIN_NETS = {"1": "RESET_N", "2": "RESET_N", "3": "GND", "4": "GND"}


class NetmapError(Exception):
    """One or more rules failed; nothing was written."""


def _rows(path):
    with open(path, newline="", encoding="utf-8") as f:
        return list(csv.DictReader(f))


def read_symbol_pins(path):
    """Return [(name, number)] for a `.kicad_sym`, by scanning pin blocks.

    Only the two strings each pin carries are read; geometry and graphics are
    left alone (this tool has no business judging a symbol's shape).
    """
    with open(path, encoding="utf-8") as f:
        text = f.read()
    pins = []
    for block in re.split(r"\n\s*\(pin ", text)[1:]:
        chunk = " ".join(block.split("\n")[0:6])
        m_name = re.search(r'\(name "([^"]*)"', chunk)
        m_num = re.search(r'\(number "([^"]*)"', chunk)
        if m_name and m_num:
            pins.append((m_name.group(1), m_num.group(1)))
    return pins


def _norm(name):
    """Datasheet pin names are multi-function: `A/B/C`, `_d`/`_u`/`_z` suffixes."""
    return name.split("/")[0].strip()


def ddr_token(pin_name):
    """Return the DDR3L token of a SoC DDR ball, or None.

    `DDR2_RASN/DDR3_A10` -> `A10`; `DDR_DQ12` -> `DQ12`. DDR3 is the alternative
    this board uses; DDR2 names are never taken.
    """
    for part in pin_name.split("/"):
        part = part.strip()
        if part.startswith("DDR3_"):
            return part[5:]
        if part.startswith("DDR_") and not part.startswith(("DDR2_", "DDR3_")):
            return part[4:]
    return None


def _is_ddr_ball(pin_name):
    return pin_name.startswith(("DDR_", "DDR2_", "DDR3_"))


def build(examples=EXAMPLES, symbols=None):
    """Return (nets, terminals, report) or raise NetmapError."""
    examples = str(examples)
    symbols = symbols or {}
    pinmap = _rows(os.path.join(examples, "pinmap_rk3506j.csv"))
    power = _rows(os.path.join(examples, "power_balls.csv"))
    ddr = _rows(os.path.join(examples, "ddr3l_signals.csv"))
    stamp = _rows(os.path.join(examples, "som_pinout_140.csv"))
    rails = _rows(os.path.join(examples, "power_rails.csv"))

    errs = []
    nets = {}          # net -> {"class", "note"}
    terminals = []     # (net, device, pin)

    def put(net, cls, note=""):
        if net in nets:
            if nets[net]["class"] != cls:
                errs.append("net %r claimed as %r and as %r"
                            % (net, nets[net]["class"], cls))
            elif note and not nets[net]["note"]:
                nets[net]["note"] = note
            return net
        nets[net] = {"class": cls, "note": note}
        return net

    def add(net, device, pin):
        for n, d, p in terminals:
            if d == device and p == pin:
                errs.append("%s pin %r is already on net %r (now also %r)"
                            % (device, pin, n, net))
                return
        terminals.append((net, device, pin))

    # --- R2: the name table must be a bijection onto the chip's signal pins --
    values = list(DDR_SOC_TO_CHIP.values())
    if len(values) != len(set(values)):
        dupes = sorted({v for v in values if values.count(v) > 1})
        errs.append("DDR_SOC_TO_CHIP maps two SoC tokens onto %r" % dupes)

    # --- R2: a declared no-chip-pin token must really have no chip pin ------
    for tok in DDR_SOC_NO_CHIP_PIN:
        if tok in set(DDR_SOC_TO_CHIP.values()):
            errs.append("DDR_SOC_NO_CHIP_PIN says %r has no chip pin, but the "
                        "name table maps something onto it" % tok)

    # --- rails ------------------------------------------------------------
    rail_names = [r["rail"] for r in rails]
    rail_src = {r["rail"]: (r["source"], r["device"]) for r in rails}
    for want in EXPORTED_RAILS:
        if want not in rail_names:
            errs.append("rail %r is required on the stamp holes but is not in "
                        "power_rails.csv" % want)
    for alias, rail in RAIL_ALIASES.items():
        if rail not in rail_names:
            errs.append("alias %r -> %r: no such rail" % (alias, rail))

    # --- R7: the VREF node is VDDQ/2, so its two rails must actually be 2:1 --
    by_rail = {r["rail"]: r for r in rails}
    if "+0V675_REF" in by_rail and "+1V35_DDR" in by_rail:
        try:
            ref_v = float(by_rail["+0V675_REF"]["typ_v"])
            ddr_v = float(by_rail["+1V35_DDR"]["typ_v"])
        except (TypeError, ValueError):
            errs.append("power_rails.csv has a non-numeric typ_v on the "
                        "VREF rail pair")
        else:
            if abs(ref_v - ddr_v / 2.0) > 0.005:
                errs.append("rail '+0V675_REF' is %.4f V but half of "
                            "'+1V35_DDR' (%.4f V) is %.4f V: the VREF node is a "
                            "VDDQ/2 divider, so the two rails must stay 2:1"
                            % (ref_v, ddr_v, ddr_v / 2.0))

    ball_rail = {r["ball"]: r["rail"] for r in power}
    ddr_ball_map = {r["signal"]: r["ball"] for r in ddr}
    stamp_rows = [r for r in stamp if r["ball"]]
    stamp_balls = {r["ball"] for r in stamp_rows}
    stamp_nets = {r["ball"]: r["net"] for r in stamp_rows}
    stamp_pin_of = {r["ball"]: r["pin"] for r in stamp_rows}

    # --- R1 / R2 / R6: the SoC side --------------------------------------
    seen_balls = {}
    ball_nets = {}     # ball -> the net a rule owns it, for the stamp audit
    for row in pinmap:
        ball, pin_name = row["ball"], row["pin_name"]
        if ball in seen_balls:
            errs.append("ball %r appears twice in pinmap_rk3506j.csv "
                        "(first as %r, now as %r)"
                        % (ball, seen_balls[ball], pin_name))
            continue
        seen_balls[ball] = pin_name

        # power and ground first: a ball's class beats its name prefix
        if row["class"] == "power/ground":
            rail = ball_rail.get(ball)
            if rail is None:
                errs.append("ball %r (%r) has no rail in power_balls.csv"
                            % (ball, pin_name))
                continue
            if rail == "INTERNAL":
                net = _norm(pin_name)
                put(net, "internal",
                    "datasheet p.39 direction O: the chip drives it; decoupling only")
            else:
                put(rail, "ground" if rail == "GND" else "power")
                net = rail
            ball_nets[ball] = net
            add(net, "SoC", ball)
            continue

        if _is_ddr_ball(pin_name):
            token = ddr_token(pin_name)
            if token is None:
                errs.append("ball %r: %r carries no DDR3 token" % (ball, pin_name))
                continue
            if token not in ddr_ball_map:
                errs.append("ball %r is %r but ddr3l_signals.csv has no %r"
                            % (ball, pin_name, token))
                continue
            if ddr_ball_map[token] != ball:
                errs.append("DDR signal %r is at %r in ddr3l_signals.csv but "
                            "pinmap puts it at %r" % (token, ddr_ball_map[token], ball))
                continue
            if ball in stamp_balls:
                errs.append("DDR ball %r is exported on stamp pin %r: the DDR bus "
                            "stays on the board (docs/143 R6)"
                            % (ball, stamp_nets[ball]))
                continue
            if token in DDR_SOC_NO_CHIP_PIN:
                net = "NC_" + ball
                put(net, "nc", DDR_SOC_NO_CHIP_PIN[token])
            elif token in ("VREF_CA", "VREF_DQ"):
                net = "+0V675_REF"
                put(net, "power", "VDDQ/2 divider (docs/144 4.1): a passive node, no regulator")
            elif token.startswith("OPEN"):
                net = "NC_" + ball
                put(net, "nc", "datasheet marks it OPEN: no net")
            elif token == "RZQ":
                net = "DDR_ZQ"
                put(net, "signal", "RZQ: 1 % resistor to GND, value later")
            else:
                net = "DDR_" + token
                put(net, "signal")
            ball_nets[ball] = net
            add(net, "SoC", ball)
            continue

        first = _norm(pin_name)
        # the function a pin name alone already fixes (no stamp table needed)
        natural = None
        if first in ("OSC_XIN", "OSC_XOUT"):
            natural = first
        elif first == "NPOR":
            natural = "RESET_N"
        elif first.startswith("FSPI_"):
            natural = first
        if ball in stamp_balls:
            name = stamp_nets[ball]
            net = RAIL_ALIASES.get(name, name)
            if natural is not None and net != natural:
                errs.append("stamp pin %s says net %r but ball %r is %r by rule "
                            "(the pin name decides: %r)"
                            % (stamp_pin_of.get(ball, "?"), net, ball, natural,
                               pin_name))
            if name in RAIL_ALIASES:
                put(net, "ground" if net == "GND" else "power")
            else:
                note = ""
                if name == "SARADC_IN0":
                    note = ("also the boot strap: 10 K pull-up to +1V8_ANA "
                            "(docs/143)")
                elif name == "RESET_N":
                    note = "1 K pull-up to +3V3 (docs/143)"
                put(net, "signal", note)
            add(net, "SoC", ball)
        elif first in ("OSC_XIN", "OSC_XOUT"):
            net = "OSC_XIN" if first.endswith("XIN") else "OSC_XOUT"
            put(net, "signal", "24 MHz crystal")
            ball_nets[ball] = net
            add(net, "SoC", ball)
        elif first == "NPOR":
            put("RESET_N", "signal", "1 K pull-up to +3V3 (docs/143)")
            ball_nets[ball] = "RESET_N"
            add("RESET_N", "SoC", ball)
        elif first.startswith("FSPI_"):
            put(first, "signal", "SPI-NAND (FSPI)")
            ball_nets[ball] = first
            add(first, "SoC", ball)
        else:
            put("NC_" + ball, "nc", "no host pin, not exported: leave open")
            add("NC_" + ball, "SoC", ball)

    # --- R2b: nothing in ddr3l_signals.csv may be missing or mislabelled ---
    for sig, ball in ddr_ball_map.items():
        if sig == "VDDQ":       # power row: checked against the rails instead
            if ball_rail.get(ball) != "+1V35_DDR":
                errs.append("ddr3l_signals.csv %r -> %r, but that ball is on rail %r"
                            % (sig, ball, ball_rail.get(ball)))
            continue
        if ball not in seen_balls:
            errs.append("ddr3l_signals.csv %r -> %r, but pinmap has no such ball"
                        % (sig, ball))
        elif ddr_token(seen_balls[ball]) != sig:
            errs.append("ddr3l_signals.csv %r -> %r, but that ball is %r"
                        % (sig, ball, seen_balls[ball]))

    # --- R7: every rail has a producer and every load --------------------
    for name in rail_names:
        if name == "GND":
            continue
        put(name, "ground" if name == "GND" else "power")
        if name != "+5V_IN":
            src, device = rail_src[name]
            if not device:
                errs.append("rail %r has no producing device" % name)
                continue
            # one terminal per rail instance: the same part feeds four rails
            add(name, "RAIL", "%s@%s" % (device, name))
            if src and src not in rail_names:
                errs.append("rail %r is fed from %r, which is not a rail"
                            % (name, src))

    # --- R4: the stamp holes ---------------------------------------------
    # A stamp hole either names a ball the design already ruled on (a rail, an
    # internal output, a crystal pin, the NAND bus, a no-connect) or it names a
    # plain GPIO ball, in which case the stamp table *is* the board-level name.
    # Either way the declared net must agree with the ball's rule, and a ball
    # may not appear on two holes.
    stamp_ball_uses = {}
    for row in stamp:
        pin, ball = row["pin"], row["ball"]
        net = RAIL_ALIASES.get(row["net"], row["net"])
        if ball:
            stamp_ball_uses.setdefault(ball, []).append(pin)
            if ball not in seen_balls:
                errs.append("stamp pin %s exports ball %r, which "
                            "pinmap_rk3506j.csv does not have" % (pin, ball))
        if net not in nets:
            errs.append("stamp pin %s is on net %r, which no SoC pin provides"
                        % (pin, net))
            continue
        if nets[net]["class"] == "nc":
            errs.append("stamp pin %s exports net %r, which is a no-connect"
                        % (pin, net))
        ruled = ball_nets.get(ball) if ball else None
        if ruled is not None and ruled != net:
            errs.append("stamp pin %s says net %r but ball %r is %r by rule "
                        "(datasheet / docs/144 / docs/143), not a free choice"
                        % (pin, net, ball, ruled))
        add(net, "STAMP", pin)
    for ball, pins in sorted(stamp_ball_uses.items()):
        if len(pins) > 1:
            errs.append("ball %r is exported on %d stamp holes (%s): one ball, "
                        "one hole" % (ball, len(pins), " ".join(pins)))

    # --- R3: the chip side -----------------------------------------------
    if not symbols.get("DDR"):
        errs.append("no DDR symbol pins supplied: the chip side cannot be checked")
    covered = set()
    for name, number in symbols.get("DDR", []):
        if name in DDR_POWER_PINS:
            rail = DDR_POWER_PINS[name]
            put(rail, "ground" if rail == "GND" else "power")
            add(rail, "DDR", number)
        elif name in DDR_REF_PINS:
            net = DDR_REF_PINS[name]
            if name.startswith("VREF"):
                put(net, "power", "VDDQ/2 divider (docs/144 4.1): a passive node, no regulator")
            else:
                put(net, "signal", "1 % resistor to GND, value later")
            add(net, "DDR", number)
        elif name == "NC":
            put("NC_DDR_" + number, "nc", "chip marks the pad NC")
            add("NC_DDR_" + number, "DDR", number)
        elif name in covered:
            errs.append("chip pin name %r appears twice: the name table cannot "
                        "say which net it is" % name)
            covered.add(name)
        else:
            tok = [t for t, c in DDR_SOC_TO_CHIP.items() if c == name]
            if not tok:
                errs.append("DDR pin %r (pad %s) is not in DDR_SOC_TO_CHIP"
                            % (name, number))
            else:
                net = "DDR_" + tok[0]
                if net not in nets:
                    errs.append("DDR pin %r (pad %s) maps to net %r, which no SoC "
                                "ball provides (names disagree)" % (name, number, net))
                add(net, "DDR", number)
            covered.add(name)

    for key, table, device in (("NAND", NAND_PIN_NETS, "NAND"),
                              ("XTAL24", XTAL24_PIN_NETS, "XTAL24"),
                              ("RESET", RESET_PIN_NETS, "RESET")):
        for name, number in symbols.get(key, []):
            net = table.get(name)
            if net is None:
                errs.append("%s pin %r (pad %s) has no rule" % (device, name, number))
                continue
            put(net, "ground" if net == "GND" else
                "power" if net.startswith("+") else "signal")
            add(net, device, number)

    # --- R5: a part with no host pin is dropped by name ------------------
    dropped = []
    if "XTAL32" in symbols:
        dropped.append({
            "part": "NX2012SA (32.768 kHz)",
            "why": "RK3506J has no dedicated 32 kHz pin: datasheet p.8 offers the "
                   "internal LF RC clock, and the only external 32 K path is the "
                   "CLK_32K mux (RM_IO14), which sits on ball N3 - already the "
                   "exported SPI1_CSN0 (stamp pin 123)",
            "consequence": "not drawn in this schematic; footprint and symbol stay "
                           "in lib/ for a board that owns a 32 K host pin",
        })

    # --- R8: a net needs two ends ---------------------------------------
    errs.extend(audit_net_ends(nets, terminals))

    if errs:
        raise NetmapError("; ".join(errs))

    report = {
        "balls": len(seen_balls),
        "nets": len(nets),
        "terminals": len(terminals),
        "by_class": {c: sum(1 for v in nets.values() if v["class"] == c)
                     for c in sorted({v["class"] for v in nets.values()})},
        "dropped_parts": dropped,
    }
    return nets, terminals, report


def audit_net_ends(nets, terminals):
    """R8, as a function so the rule itself can be tested on a toy net list."""
    errs = []
    counts = {}
    for net, _d, _p in terminals:
        counts[net] = counts.get(net, 0) + 1
    for net, info in sorted(nets.items()):
        n = counts.get(net, 0)
        if info["class"] in ("nc", "internal"):
            if n != 1:
                errs.append("%s net %r must have exactly its own pin, has %d terminals"
                            % (info["class"], net, n))
        elif n < 2:
            errs.append("net %r has %d terminal(s): a net needs two ends" % (net, n))
    return errs

def write_csv(path, rows, fields):
    with open(path, "w", newline="", encoding="utf-8") as f:
        w = csv.DictWriter(f, fieldnames=fields)
        w.writeheader()
        for r in rows:
            w.writerow(r)


def main(argv=None):
    ap = argparse.ArgumentParser(description=__doc__.split("\n")[0])
    ap.add_argument("--examples", default=EXAMPLES)
    ap.add_argument("--out-nets", default=None)
    ap.add_argument("--out-terminals", default=None)
    args = ap.parse_args(argv)

    examples = args.examples
    symbols = {}
    lib = os.path.join(examples, "lib")
    for key, stem in (("DDR", "MT41K256M16"), ("NAND", "W25N01GVZEIG"),
                      ("XTAL24", "X322524MSB4SI"), ("XTAL32", "NX2012SA"),
                      ("RESET", "SKRPACE010")):
        path = os.path.join(lib, stem + ".kicad_sym")
        if os.path.exists(path):
            symbols[key] = read_symbol_pins(path)

    try:
        nets, terminals, report = build(examples, symbols)
    except NetmapError as exc:
        print("netmap refused: %s" % exc, file=sys.stderr)
        return 2

    out_nets = args.out_nets or os.path.join(examples, "netmap_nets.csv")
    out_terms = args.out_terminals or os.path.join(examples, "netmap_terminals.csv")
    write_csv(out_nets, [{"net": k, "class": v["class"], "note": v["note"]}
                         for k, v in sorted(nets.items())], ["net", "class", "note"])
    write_csv(out_terms, [{"net": n, "device": d, "pin": p}
                          for n, d, p in sorted(terminals)], ["net", "device", "pin"])
    print("balls %d, nets %d, terminals %d"
          % (report["balls"], report["nets"], report["terminals"]))
    print("classes " + ", ".join("%s %d" % (k, v) for k, v in report["by_class"].items()))
    for d in report["dropped_parts"]:
        print("dropped: %s - %s" % (d["part"], d["why"]))
    return 0


if __name__ == "__main__":
    sys.exit(main())
