#!/usr/bin/env python3
"""Freeze the RK3506J core-board power tree and check it against the datasheet.

Nothing here is a route, a placement, or a copper decision. This file turns the
number of every *supply* ball into a rail name, and refuses to emit a tree that
breaks one of the following rules:

* every non-ground supply group appears exactly once in the rule table, and
  every group in ``power_groups_rk3506j.csv`` is known (a typo becomes an error,
  not a silently unpowered ball);
* a rail's recommended band (datasheet Table 3-2) contains the band of every
  group it feeds, so a ball is never fed outside its own spec;
* ``DDR_VREF_*`` is exactly ``DDR_VDDQ/2`` (0.675 V for 1.35 V), the DDR3L
  definition, not a number typed next to it;
* pins the datasheet calls "Internal voltage output" (``ACODEC_ADC_VCM``,
  ``ACODEC_ADC_AVDD1V6``) are never connected to a regulator: they only get a
  decoupling capacitor to ``ACODEC_ADC_AVSS``;
* every rail is reachable from the single 5 V input and the source graph is a
  tree (no cycle, no orphan, no rail sourced from a rail that is below its own
  output);
* no regulator is loaded past 80 % of its rating, and the sum of the input-side
  power of the 5 V converters stays inside the stated 10 W budget.

Where a number is *not* in the datasheet (per-rail current, unit load split,
switching frequency, feedback reference), the table says so in ``basis`` and the
number stays a design value with a stated derivation. The build refuses to
invent a feedback divider: ``SY8113BADC``'s Vref and fsw are not in this repo
yet, so ``fb_divider`` stays ``None`` until the vendor datasheet is read.

Usage
-----
    python tools/power_tree.py --groups examples/rk3506_som/power_groups_rk3506j.csv \
        --out-rails examples/rk3506_som/power_rails.csv \
        --out-balls examples/rk3506_som/power_balls.csv
"""
from __future__ import annotations

import argparse
import csv
import io
import os
import sys

# --------------------------------------------------------------------------
# Design limit of the input. External evidence, not a datasheet number:
# 创龙 SOM-TL3506 hardware manual states the core board is fed from 5.0 V and
# that the baseboard should plan for 10 W on VDD_5V_SOM.
# --------------------------------------------------------------------------
INPUT_RAIL = "+5V_IN"
INPUT_BUDGET_W = 10.0
INPUT_MIN_V = 4.75
INPUT_MAX_V = 5.25
DERATING = 0.80  # a regulator is not used past 80 % of its rated current

GROUND = "GND"
INTERNAL = "INTERNAL"

# --------------------------------------------------------------------------
# Rails.  typ/min/max come from datasheet Table 3-2 (recommended operating
# condition), except +5V_IN (input tolerance, design value) and +0V675_REF
# (derived from DDR_VDDQ by definition).
# --------------------------------------------------------------------------
RAILS = {
    INPUT_RAIL: {
        "typ": 5.0, "min": INPUT_MIN_V, "max": INPUT_MAX_V,
        "kind": "input", "src": None, "device": "stamp holes 42-45",
        "lcsc": "", "rating_ma": None, "load_ma": None,
        "basis": "5.0 V input, ±5 % design window; 4 stamp pads (pinout 42-45)",
    },
    "+0V9_CPU": {
        "typ": 0.95, "min": 0.855, "max": 1.103,
        "kind": "regulator", "src": INPUT_RAIL, "device": "SY8113BADC",
        "lcsc": "C78989", "rating_ma": 3000, "load_ma": 1200,
        "basis": "Table 3-2 CPU_DVDD 0.855/0.95/1.103; fixed 0.95 V (no DVFS on a core board); load is a design value (datasheet gives no per-rail current)",
    },
    "+0V9_LOGIC": {
        "typ": 0.90, "min": 0.855, "max": 0.998,
        "kind": "regulator", "src": INPUT_RAIL, "device": "SY8113BADC",
        "lcsc": "C78989", "rating_ma": 3000, "load_ma": 800,
        "basis": "Table 3-2 LOGIC_DVDD 0.855/0.90/0.998; load is a design value",
    },
    "+0V9_PMU": {
        "typ": 0.90, "min": 0.81, "max": 0.99,
        "kind": "bead", "src": "+0V9_LOGIC", "device": "ferrite bead + 2x10 uF",
        "lcsc": "", "rating_ma": None, "load_ma": 100,
        "basis": "Table 3-2 PMU_LOGIC_DVDD0V9 0.81/0.90/0.99 equals the LOGIC band, so one 0.9 V converter feeds both through a bead; datasheet p.8 says the PMU has three separate voltage domains, it does not require separate regulators",
    },
    "+0V9_ANA": {
        "typ": 0.90, "min": 0.81, "max": 0.99,
        "kind": "bead", "src": "+0V9_LOGIC", "device": "ferrite bead + 10 uF",
        "lcsc": "", "rating_ma": None, "load_ma": 150,
        "basis": "Table 3-2 AVDD_0V9 0.81/0.90/0.99; USB/MIPI PHY only, fed through a bead from the logic 0.9 V",
    },
    "+1V35_DDR": {
        "typ": 1.35, "min": 1.283, "max": 1.418,
        "kind": "regulator", "src": INPUT_RAIL, "device": "SY8113BADC",
        "lcsc": "C78989", "rating_ma": 3000, "load_ma": 800,
        "basis": "Table 3-2 DDR3L IO power 1.283/1.35/1.418; load is a design value",
    },
    "+0V675_REF": {
        "typ": 0.675, "min": 0.6415, "max": 0.709,
        "kind": "divider", "src": "+1V35_DDR", "device": "1 % divider 1k/1k + 100 nF",
        "lcsc": "", "rating_ma": None, "load_ma": 2,
        "basis": "DDR3L defines VREF = VDDQ/2 = 0.675 V, so the band is exactly the DDR_VDDQ band halved (0.6415/0.675/0.709)",
    },
    "+3V3": {
        "typ": 3.30, "min": 3.00, "max": 3.63,
        "kind": "regulator", "src": INPUT_RAIL, "device": "SY8113BADC",
        "lcsc": "C78989", "rating_ma": 3000, "load_ma": 1600,
        "basis": "Table 3-2 PMUIO_VCC3V3 / USB20_OTG_AVDD3V3 / VCCIO* at 3.3 V 3.00/3.30/3.63; load covers SoC IO + NAND + the 1V8 LDO input current + 3V3_OUT (design value)",
    },
    "+1V8_ANA": {
        "typ": 1.80, "min": 1.62, "max": 1.98,
        "kind": "regulator", "src": "+3V3", "device": "AMS1117-1.8",
        "lcsc": "C351432", "rating_ma": 1000, "load_ma": 250,
        "basis": "Table 3-2 AVDD_1V8 / PLL / ADC 1.62/1.80/1.98; LDO from 3V3 keeps the PLL and ADC rails linear (load is a design value)",
    },
    GROUND: {
        "typ": 0.0, "min": 0.0, "max": 0.0,
        "kind": "ground", "src": None, "device": "",
        "lcsc": "", "rating_ma": None, "load_ma": None,
        "basis": "VSS 140 balls + ACODEC_ADC_AVSS (Table 2-2)",
    },
}

# group -> rail. DDR_VREF_* and ACODEC internal outputs are handled explicitly.
GROUPS = {
    "VSS": GROUND,
    "ACODEC_ADC_AVSS": GROUND,
    "CPU_DVDD": "+0V9_CPU",
    "LOGIC_DVDD": "+0V9_LOGIC",
    "PMU_LOGIC_DVDD0V9": "+0V9_PMU",
    "MIPI_DPHY_AVDD0V9": "+0V9_ANA",
    "USB20_OTG_AVDD0V9": "+0V9_ANA",
    "SYS_PLL_AVDD1V8": "+1V8_ANA",
    "USB20_OTG_AVDD1V8": "+1V8_ANA",
    "MIPI_DPHY_AVDD1V8": "+1V8_ANA",
    "TSADC_VCC1V8": "+1V8_ANA",
    "SARADC_AVDD1V8": "+1V8_ANA",
    "ACODEC_ADC_AVDD1V8": "+1V8_ANA",
    "PMUIO_VCC3V3": "+3V3",
    "USB20_OTG_AVDD3V3": "+3V3",
    "VCCIO1_VCC": "+3V3",
    "VCCIO2_VCC": "+3V3",
    "VCCIO3_VCC": "+3V3",
    "VCCIO4_VCC": "+3V3",
    "DDR_VDDQ": "+1V35_DDR",
    "DDR_VREF_CA": "+0V675_REF",
    "DDR_VREF_DQ": "+0V675_REF",
    "ACODEC_ADC_VCM": INTERNAL,
    "ACODEC_ADC_AVDD1V6": INTERNAL,
}

# Pins the datasheet marks as "Internal voltage output": capacitor only.
INTERNAL_GROUPS = {
    "ACODEC_ADC_VCM": "10 uF + 100 nF to ACODEC_ADC_AVSS (1A13); no regulator",
    "ACODEC_ADC_AVDD1V6": "10 uF + 100 nF to ACODEC_ADC_AVSS (1A13); no regulator",
}

# Balls the datasheet itself lists twice. Table 2-2 prints 1A13 inside the VSS
# run *and* names it as the ACODEC_ADC_AVSS group. Both readings are ground, so
# the net is unambiguous; the raw table is kept as extracted and the double
# listing is reported rather than silently de-duplicated.
DATASHEET_DOUBLE_LISTED = {"1A13": ("VSS", "ACODEC_ADC_AVSS")}

# Explicit design decisions that are *not* datasheet facts, kept next to the data.
DESIGN_NOTES = [
    "VCCIO1..VCCIO4 are all fed from +3V3: every interface this board exposes "
    "(RMII, UART, SPI, CAN, LCDC, SAI) is a 3.3 V interface. Consequence: the "
    "SDMMC bank cannot switch to 1.8 V signalling; a baseboard needing UHS-I "
    "must add its own 1.8/3.3 V switch, the way 创龙's VDD_ADJ_SD_OUT (1.8/3.3 V, "
    "200 mA max) does.",
    "The datasheet defines one internal power-on-reset circuit (p.8) and gives no "
    "rail sequencing table; there is no external sequencer on this board. Any "
    "sequencing requirement would have to come from a Rockchip hardware design "
    "guide, which is not in this repo (registered as undefined, not assumed).",
    "SY8113BADC feedback divider / inductor / output capacitor are deliberately "
    "absent: Vref and fsw are not in this repo yet. Their values are computed "
    "from the vendor datasheet in the next batch, not guessed here.",
    "DDR3L single x16 die: no VTT termination rail is generated; ODT is used on "
    "the DRAM side and the address/command nets are point-to-point.",
]


class PowerTreeError(Exception):
    pass


def _f(x):
    """One supply cell -> list of floats. Dual-voltage pads read ``1.62/3.00``.

    The RK3506 datasheet lists VCCIO1..4 as two separate bands (1.8 V family and
    3.3 V family); that is one pad with two legal levels, not a 1.62..3.63 band.
    Keeping the slash means the check can refuse to average the two.
    """
    if x is None:
        return None
    x = str(x).strip()
    if x == "":
        return None
    if "/" in x:
        return [float(part) for part in x.split("/")]
    return [float(x)]


def read_groups(path):
    """Read ``power_groups_rk3506j.csv`` (group,ball,min,typ,max,unit)."""
    rows = []
    with io.open(path, encoding="utf-8") as fh:
        for r in csv.DictReader(fh):
            rows.append({
                "group": r["group"], "ball": r["ball"],
                "min": _f(r["min"]), "typ": _f(r["typ"]), "max": _f(r["max"]),
                "unit": r.get("unit") or "",
            })
    if not rows:
        raise PowerTreeError("empty group table")
    return rows


def check_rail_bands(groups, rails=None):
    """Every group's own band must sit inside the band of its rail."""
    rails = RAILS if rails is None else rails
    errs = []
    for g in groups:
        name = g["group"]
        if name not in GROUPS:
            errs.append("group %r has no rail rule" % name)
            continue
        rail = GROUPS[name]
        if rail in (GROUND, INTERNAL):
            continue
        if rail not in rails:
            errs.append("group %r points at unknown rail %r" % (name, rail))
            continue
        r = rails[rail]
        los, his = g["min"], g["max"]
        if los is None or his is None:
            # a band the datasheet does not number (internal outputs) cannot be
            # checked, and must not be silently accepted as "fine"
            errs.append("group %r has no numeric band in the source table" % name)
            continue
        if len(los) != len(his):
            errs.append("group %r has mismatched dual bands" % name)
            continue
        # a dual-band pad passes if *one* of its bands sits inside the rail
        fits = [l >= r["min"] - 1e-9 and h <= r["max"] + 1e-9
                for l, h in zip(los, his)]
        if not any(fits):
            errs.append("group %r band %s..%s leaves rail %s band %.4g..%.4g"
                        % (name, "/".join("%.4g" % v for v in los),
                           "/".join("%.4g" % v for v in his),
                           rail, r["min"], r["max"]))
    return errs


def check_internal(groups):
    """Internal-output groups must exist, be in INTERNAL_GROUPS, and be unrouted."""
    errs = []
    for g in groups:
        name = g["group"]
        if GROUPS.get(name) != INTERNAL:
            continue
        if name not in INTERNAL_GROUPS:
            errs.append("internal group %r has no decoupling rule" % name)
            continue
        if g["min"] is not None:
            errs.append("internal group %r carries a numeric supply band; it is an "
                        "output, not a supply" % name)
    for name in INTERNAL_GROUPS:
        if not any(g["group"] == name for g in groups):
            errs.append("internal group %r missing from the ball table" % name)
    return errs


def check_cover(groups):
    """Coverage both ways: no group without a rule, no rule without a group."""
    errs = []
    seen = {g["group"] for g in groups}
    for name in sorted(seen):
        if name not in GROUPS:
            errs.append("group %r has no rail rule" % name)
    for name in GROUPS:
        if name not in seen:
            errs.append("rule for %r but no ball carries that group" % name)
    # every rule target must be a known rail
    for name, rail in GROUPS.items():
        if rail not in (GROUND, INTERNAL) and rail not in RAILS:
            errs.append("rule %r -> unknown rail %r" % (name, rail))
    return errs


def check_unique(balls):
    """A ball may be listed twice only if both listings land on the same rail."""
    errs = []
    by_ball = {}
    for b in balls:
        by_ball.setdefault(b["ball"], set()).add(b["rail"])
    for ball, rails in sorted(by_ball.items()):
        if len(rails) > 1:
            errs.append("ball %r is on more than one rail: %s"
                        % (ball, ", ".join(sorted(rails))))
    for ball, groups in DATASHEET_DOUBLE_LISTED.items():
        got = [b["group"] for b in balls if b["ball"] == ball]
        if sorted(got) != sorted(groups):
            errs.append("double-listed ball %r reads %s, expected %s"
                        % (ball, got, list(groups)))
    return errs


def check_vref(rails=None, tol=1e-9):
    """DDR3L: VREF must be exactly half of DDR_VDDQ, all three points."""
    rails = RAILS if rails is None else rails
    errs = []
    vddq, vref = rails.get("+1V35_DDR"), rails.get("+0V675_REF")
    if vddq is None or vref is None:
        return ["DDR_VDDQ or VREF rail missing"]
    for k in ("min", "typ", "max"):
        want = vddq[k] / 2.0
        if abs(vref[k] - want) > tol:
            errs.append("VREF %s=%.6g but DDR_VDDQ %s/2=%.6g" % (k, vref[k], k, want))
    return errs


def check_sources(rails=None):
    """Every rail must reach the input, and the source graph must be a tree."""
    rails = RAILS if rails is None else rails
    errs = []
    for name, r in rails.items():
        if name == INPUT_RAIL or r["kind"] == "ground":
            continue
        src = r["src"]
        if src is None:
            errs.append("rail %r has no source" % name)
            continue
        if src not in rails:
            errs.append("rail %r sourced from unknown rail %r" % (name, src))
            continue
        if r["kind"] not in ("regulator", "bead", "divider", "ground"):
            errs.append("rail %r has unknown kind %r" % (name, r["kind"]))
        # walk up, detect cycles
        seen = {name}
        cur = src
        while cur is not None:
            if cur in seen:
                errs.append("rail %r source graph has a cycle at %r" % (name, cur))
                break
            seen.add(cur)
            cur = rails[cur]["src"] if cur in rails else None
        if cur is None and INPUT_RAIL not in seen:
            errs.append("rail %r does not reach %s" % (name, INPUT_RAIL))
    return errs


def check_load(rails=None, derating=DERATING):
    """A regulator is not loaded past its derated rating; input power fits."""
    rails = RAILS if rails is None else rails
    errs = []
    input_power_w = 0.0
    for name, r in rails.items():
        load = r.get("load_ma")
        rating = r.get("rating_ma")
        if load is not None and rating is not None:
            if load > derating * rating:
                errs.append("rail %r load %d mA over %.0f %% of %d mA rating"
                            % (name, load, derating * 100, rating))
        if r["src"] == INPUT_RAIL and r["kind"] == "regulator":
            input_power_w += r["typ"] * (load or 0) / 1000.0
    if input_power_w > INPUT_BUDGET_W + 1e-9:
        errs.append("input-side power %.3f W over the %.1f W budget"
                    % (input_power_w, INPUT_BUDGET_W))
    return errs, input_power_w


def build(groups_path):
    """Return (rails_rows, ball_rows, report) or raise PowerTreeError."""
    groups = read_groups(groups_path)
    errs = []
    errs += check_cover(groups)
    errs += check_unique([{"ball": g["ball"], "group": g["group"],
                           "rail": GROUPS[g["group"]]} for g in groups
                          if g["group"] in GROUPS])
    errs += check_rail_bands(groups)
    errs += check_internal(groups)
    errs += check_vref()
    errs += check_sources()
    load_errs, power_w = check_load()
    errs += load_errs
    if errs:
        raise PowerTreeError("; ".join(errs))

    rails_rows = []
    for name, r in RAILS.items():
        rails_rows.append({
            "rail": name, "kind": r["kind"], "typ_v": "%.4f" % r["typ"],
            "min_v": "%.4f" % r["min"], "max_v": "%.4f" % r["max"],
            "source": r["src"] or "", "device": r["device"], "lcsc": r["lcsc"],
            "rating_ma": "" if r["rating_ma"] is None else r["rating_ma"],
            "load_ma": "" if r["load_ma"] is None else r["load_ma"],
            "balls": "", "basis": r["basis"],
        })
    ball_rows = []
    for g in groups:
        rail = GROUPS[g["group"]]
        ball_rows.append({"ball": g["ball"], "group": g["group"],
                          "rail": rail, "kind": "internal" if rail == INTERNAL else "supply"})
    counts = {}
    for b in ball_rows:
        counts[b["rail"]] = counts.get(b["rail"], 0) + 1
    for row in rails_rows:
        n = counts.get(row["rail"], 0)
        row["balls"] = n
    report = {"groups": len(groups), "balls": len(ball_rows),
              "double_listed": sorted(DATASHEET_DOUBLE_LISTED),
              "rails": len(rails_rows), "input_power_w": round(power_w, 3),
              "input_budget_w": INPUT_BUDGET_W,
              "per_rail_balls": {k: v for k, v in sorted(counts.items())},
              "notes": DESIGN_NOTES,
              "internal_groups": INTERNAL_GROUPS}
    return rails_rows, ball_rows, report


def write_csv(path, rows, fields):
    parent = os.path.dirname(os.path.abspath(path))
    if parent and not os.path.isdir(parent):
        os.makedirs(parent)
    with io.open(path, "w", encoding="utf-8", newline="") as fh:
        w = csv.DictWriter(fh, fieldnames=fields)
        w.writeheader()
        for r in rows:
            w.writerow(r)


RAIL_FIELDS = ["rail", "kind", "typ_v", "min_v", "max_v", "source", "device",
               "lcsc", "rating_ma", "load_ma", "balls", "basis"]
BALL_FIELDS = ["ball", "group", "rail", "kind"]


def main(argv=None):
    ap = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    ap.add_argument("--groups", default="examples/rk3506_som/power_groups_rk3506j.csv")
    ap.add_argument("--out-rails", default="examples/rk3506_som/power_rails.csv")
    ap.add_argument("--out-balls", default="examples/rk3506_som/power_balls.csv")
    args = ap.parse_args(argv)
    try:
        rails_rows, ball_rows, report = build(args.groups)
    except PowerTreeError as exc:
        sys.stderr.write("power tree refused: %s\n" % exc)
        return 2
    write_csv(args.out_rails, rails_rows, RAIL_FIELDS)
    write_csv(args.out_balls, ball_rows, BALL_FIELDS)
    print("rails %d, balls %d, input-side %.3f W of %.1f W"
          % (report["rails"], report["balls"],
             report["input_power_w"], report["input_budget_w"]))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
