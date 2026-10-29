"""IEC 60664-1:2020-05 insulation coordination: creepage, clearance, grooves.

The numbers come from data/iec60664_tables.json, which was NOT typed out of
the standard or out of a picture.  The KiCad 10.0 calculator implementation of
the same tables was compiled and probed, every breakpoint was recovered by
bisection, and the port below reproduces the compiled code on 3760 probe
points with zero mismatches (see the file's ``how_it_was_obtained``).

What that buys: the numbers are behaviourally equal to a published open-source
transcription, and any drift can be re-proved by re-running the probe.  What it
does NOT buy: nobody has independently checked KiCad's transcription against
the paid standard text, so the answer is a table value, not a physical law.

Two deliberate refusals:

* PD4 creepage does not exist (permanent conductive pollution).  KiCad's code
  happens to return the constant 1.6 mm there; we refuse instead of shipping a
  nonsense number.
* Anything past the end of a ladder is refused, not extrapolated.

Every ladder is a list of ``[upper_input, value]``: the value applies when the
input is <= that upper bound (the standard's own ``<=`` semantics).  A step
whose ``value`` is an object ``{"slope": a, "intercept": b}`` is the one place
the source is not a constant: PD2 with material group IIIa/IIIb above 160 V
follows ``creepage = V / 100`` up to 50 kV (iec60664.cpp line 1057).  Without
that segment the port would silently truncate a 50 kV ladder at 160 V.
"""
import bisect
import json
import os

from .result import ok, err

DATA_PATH = os.path.join(
    os.path.dirname(os.path.dirname(os.path.dirname(os.path.dirname(
        os.path.abspath(__file__))))),
    "data", "iec60664_tables.json",
)

MODEL = "iec60664_1_2020_table_lookup"

PRECISION = (
    "Table value, not a continuous function: the answer is the tabulated step "
    "whose upper bound the input falls under, so it is quantised (you cannot "
    "interpolate between steps).  Numbers reproduce the KiCad 10.0 "
    "transcription of IEC 60664-1:2020-05 exactly; that transcription has not "
    "been independently diffed against the paid standard text."
)

_MATERIAL_GROUPS = {
    "I": "I",
    "II": "II",
    "IIIA": "IIIa",
    "IIIB": "IIIb",
}

_OVC = {"I": "OV_I", "II": "OV_II", "III": "OV_III", "IV": "OV_IV"}

_CACHE = {}


# ---------------------------------------------------------------- plumbing

def _load():
    if "doc" in _CACHE:
        return _CACHE["doc"], _CACHE.get("why")
    doc = None
    why = None
    if not os.path.exists(DATA_PATH):
        why = "table file missing: " + DATA_PATH
    else:
        try:
            with open(DATA_PATH, "r", encoding="utf-8") as fh:
                doc = json.load(fh)
        except Exception as exc:  # noqa: BLE001
            why = "table file unreadable: %s" % exc
    if doc is not None:
        if not doc.get("verified"):
            why = "table file present but not marked verified"
            doc = None
        elif doc.get("schema") != 1:
            why = "unexpected schema: %r" % (doc.get("schema"),)
            doc = None
    _CACHE["doc"] = doc
    _CACHE["why"] = why
    return doc, why


def reset_cache():
    """Drop the parsed table (tests use this to prove the loader reacts)."""
    _CACHE.clear()


def _pd(value):
    # int(2.7) is 2, which would answer a question nobody asked: a fractional
    # pollution degree is a typo (or a mis-scaled input), not a degree.
    try:
        pd = float(value)
    except (TypeError, ValueError):
        return None, err("pollution_degree must be an integer 1..4", "ARG_INVALID",
                         extra={"got": value})
    if pd != int(pd):
        return None, err("pollution_degree must be a whole number 1..4",
                         "ARG_INVALID",
                         hint="the table is tabulated per degree; 2.5 is not a "
                              "degree, so rounding it to 2 would answer a "
                              "different question",
                         extra={"got": value})
    pd = int(pd)
    if pd < 1 or pd > 4:
        return None, err("pollution_degree must be 1..4", "ARG_INVALID",
                         extra={"got": value})
    return pd, None


def _mg(value):
    raw = str(value or "").strip().replace(" ", "").replace("_", "").upper()
    mg = _MATERIAL_GROUPS.get(raw)
    if mg is None:
        return None, err("material_group must be one of I, II, IIIa, IIIb",
                         "ARG_INVALID", extra={"got": value})
    return mg, None


def _insulation(value):
    raw = str(value or "").strip().lower()
    if raw in ("", "basic"):
        return "basic", None
    if raw == "reinforced":
        return "reinforced", None
    return None, err(
        "this build implements basic and reinforced insulation only",
        "ARG_INVALID",
        hint="functional insulation is not in the KiCad calculator scope, so it "
             "cannot be reproduced from the verified source; treat it as a gap, "
             "not as basic insulation.",
        extra={"got": value},
    )


def _num(value, name):
    try:
        return float(value), None
    except (TypeError, ValueError):
        return None, err("%s must be numeric" % name, "ARG_INVALID",
                         extra={"got": value})


def _lookup(entry, x):
    """Return (value, upper_bound) for the ladder step covering x, else None."""
    steps = entry.get("steps") or []
    if not steps:
        return None
    bounds = [float(s[0]) for s in steps]
    i = bisect.bisect_left(bounds, x)
    if i >= len(steps):
        return None
    raw = steps[i][1]
    if isinstance(raw, dict):
        value = float(raw.get("slope", 0.0)) * x + float(raw.get("intercept", 0.0))
    else:
        value = float(raw)
    return value, float(steps[i][0])


def _ladder_group(doc, group, key):
    table = doc.get(group) or {}
    entry = table.get(key)
    if entry is None:
        return None, err(
            "no %s ladder for this combination" % group, "SAFETY_ROW_NOT_FOUND",
            extra={"requested": key, "available": sorted(table.keys())},
        )
    return entry, None


def _out_of_validity(what, x, entry, extra=None):
    out = {"input": x, "ladder_max": entry.get("max_v")}
    if extra:
        out.update(extra)
    return err(
        "%s: input is past the end of the tabulated ladder" % what,
        "OUT_OF_VALIDITY",
        hint="The standard's table stops here; extrapolating a safety distance "
             "is how boards ship with arcing gaps.  Go to the next higher "
             "design rule (or a different insulation coordination method).",
        extra=out,
    )


def _altitude_factor(doc, altitude_m):
    raw = doc.get("clearance_altitude_factor") or []
    if not raw:
        return None
    bounds = [float(r[0]) for r in raw]
    i = bisect.bisect_left(bounds, altitude_m)
    if i >= len(raw):
        return None
    return float(raw[i][1]), float(raw[i][0])


def _forced_off_note(doc, voltage_v, pd, mg, pcb_material):
    """Explain the Table F.5 note that silently disables the PCB-material column."""
    if not pcb_material:
        return None
    if voltage_v > 1000:
        return "pcb_material flag had no effect: Table F.5 drops it above 1000 V rms"
    if pd >= 3:
        return "pcb_material flag had no effect: Table F.5 drops it at PD3/PD4"
    if pd >= 2 and mg == "IIIb":
        return ("pcb_material flag had no effect: Table F.5 drops it for material "
                "group IIIb at PD2 and above")
    return None


# ---------------------------------------------------------------- creepage

def creepage_mm(voltage_rms_v, pollution_degree=2, material_group="IIIa",
                insulation="basic", pcb_material=False, altitude_m=0,
                coating=False):
    """Minimum creepage distance in mm (IEC 60664-1 Table F.5)."""
    doc, why = _load()
    if doc is None:
        return err("IEC 60664-1 tables are not available, refusing to answer",
                   "SAFETY_TABLE_MISSING", hint=why, extra={"model_file": DATA_PATH})
    pd, e = _pd(pollution_degree)
    if e:
        return e
    mg, e = _mg(material_group)
    if e:
        return e
    ins, e = _insulation(insulation)
    if e:
        return e
    v, e = _num(voltage_rms_v, "voltage_rms_v")
    if e:
        return e
    if v < 0:
        return err("voltage_rms_v must be >= 0", "ARG_INVALID", extra={"got": v})
    alt, e = _num(altitude_m or 0, "altitude_m")
    if e:
        return e

    if pd == 4:
        return err(
            "PD4 creepage is not a specified quantity",
            "STANDARD_SCOPE",
            hint="At pollution degree 4 (permanent conductive pollution) the "
                 "standard refuses to tabulate creepage at all.  KiCad's code "
                 "returns a constant 1.6 mm here; that is an artefact, not a "
                 "distance.  Reduce the pollution degree or use a different "
                 "protective measure.",
            extra={"pollution_degree": pd},
        )

    key = "PD%d|MG_%s|pcb%d" % (pd, mg, 1 if pcb_material else 0)
    entry, e = _ladder_group(doc, "creepage", key)
    if e:
        return e
    hit = _lookup(entry, v)
    if hit is None:
        return _out_of_validity("creepage", v, entry, {"ladder": key})
    table_mm, bound = hit

    mult = 1.0
    if ins == "reinforced":
        mult = float((doc.get("reinforced") or {}).get("creepage_multiplier", 2.0))
    value = table_mm * mult

    extra = {
        "ladder": key,
        "ladder_step_upper_v": bound,
        "table_value_mm": table_mm,
        "insulation": ins,
        "reinforced_multiplier": mult,
        "pcb_material_requested": bool(pcb_material),
        "edition": doc.get("edition"),
    }
    extra["standards_note"] = (
        "IEC 60664-1 additionally requires creepage >= clearance; that clamp "
        "lives in spacing(), not here, so this figure is Table F.5 alone and "
        "may be the smaller of the two."
    )
    note = _forced_off_note(doc, v, pd, mg, pcb_material)
    if note:
        extra["pcb_material_note"] = note
    if alt > 2000:
        extra["altitude_note"] = (
            "Table A.2 altitude correction applies to CLEARANCE, not to "
            "creepage; the creepage figure above is unmodified.  The clearance "
            "figure is the one that has to grow."
        )
    if coating:
        extra["coating_note"] = (
            "Conformal coating can relax creepage only under IEC 60664-3 with "
            "verified coverage (pin roots, board edge and mask openings are "
            "usually NOT covered).  Solder mask is not a conformal coating."
        )

    return ok(
        value=round(value, 6),
        unit="mm",
        model=MODEL,
        basis="IEC 60664-1:2020-05 Table F.5 (creepage), inhomogeneous field",
        precision=PRECISION,
        assumptions={
            "voltage_rms_v": v,
            "pollution_degree": pd,
            "material_group": mg,
            "insulation": ins,
            "pcb_material": bool(pcb_material),
            "altitude_m": alt,
        },
        extra=extra,
    )


# ---------------------------------------------------------------- clearance

def _transient_for_insulation(doc, transient_kv, ins):
    """Apply 5.2.5 to the transient voltage, exactly as the C++ does."""
    if ins != "reinforced":
        return transient_kv, None
    rule = doc.get("reinforced") or {}
    series = rule.get("transient_preferred_series_kv") or {}
    for k, v in series.items():
        if abs(float(k) - transient_kv) <= 1e-12:
            return float(v), "preferred_series"
    return transient_kv * float(rule.get("transient_otherwise_multiplier", 1.6)), \
        "times_1.6"


def clearance_mm(peak_v, transient_v=None, pollution_degree=2,
                 insulation="basic", altitude_m=0):
    """Minimum clearance in mm (Tables F.2 / F.8, corrected by Table A.2).

    ``peak_v`` is the peak value of the working voltage in volts.
    ``transient_v`` is the transient overvoltage the gap must withstand, in
    volts (normally the rated impulse withstand voltage from
    :func:`impulse_withstand_v`).  If it is omitted only the peak-voltage
    branch is used, which can UNDERSTATE the clearance - that is reported in
    ``extra.transient_not_supplied`` rather than hidden.
    """
    doc, why = _load()
    if doc is None:
        return err("IEC 60664-1 tables are not available, refusing to answer",
                   "SAFETY_TABLE_MISSING", hint=why, extra={"model_file": DATA_PATH})
    pd, e = _pd(pollution_degree)
    if e:
        return e
    ins, e = _insulation(insulation)
    if e:
        return e
    pk, e = _num(peak_v, "peak_v")
    if e:
        return e
    if pk < 0:
        return err("peak_v must be >= 0", "ARG_INVALID", extra={"got": pk})
    alt, e = _num(altitude_m or 0, "altitude_m")
    if e:
        return e

    extra = {"edition": doc.get("edition"), "insulation": ins}
    branches = {}

    t_entry, e = _ladder_group(doc, "clearance_transient", "PD%d" % pd)
    if e:
        return e
    p_entry, e = _ladder_group(doc, "clearance_peak", "PD%d" % pd)
    if e:
        return e

    if transient_v is None:
        extra["transient_not_supplied"] = (
            "Only the peak-voltage branch of the clearance table was applied.  "
            "Real spacings are usually governed by the transient overvoltage of "
            "the overvoltage category; pass transient_v (see "
            "impulse_withstand_v) for the full answer."
        )
        t_volts = None
    else:
        t_volts, e = _num(transient_v, "transient_v")
        if e:
            return e
        if t_volts < 0:
            return err("transient_v must be >= 0", "ARG_INVALID",
                       extra={"got": t_volts})

    c_transient = None
    if t_volts is not None:
        t_kv, rule = _transient_for_insulation(doc, t_volts / 1000.0, ins)
        extra["transient_kv_used"] = t_kv
        if rule:
            extra["transient_reinforced_rule"] = rule
        hit = _lookup(t_entry, t_kv)
        if hit is None:
            branches["clearance_transient"] = {
                "status": "out_of_validity", "input_kv": t_kv,
                "ladder_max_kv": t_entry.get("max_v"),
            }
        else:
            c_transient = hit[0]
            extra["transient_step_upper_kv"] = hit[1]

    pk_kv = pk / 1000.0
    pk_used_kv = pk_kv * 1.6 if ins == "reinforced" else pk_kv
    extra["peak_kv_used"] = pk_used_kv
    hit = _lookup(p_entry, pk_used_kv)
    c_peak = None
    if hit is None:
        branches["clearance_peak"] = {
            "status": "out_of_validity", "input_kv": pk_used_kv,
            "ladder_max_kv": p_entry.get("max_v"),
        }
    else:
        c_peak = hit[0]
        extra["peak_step_upper_kv"] = hit[1]

    if branches:
        # The C++ propagates -1 if EITHER branch is off the table (a clearance
        # must satisfy both), so taking the max of whatever survived would
        # understate the gap.  Refuse the same way.
        out = dict(extra)
        out["branches"] = branches
        return err(
            "clearance: a ladder branch stops below the requested input",
            "OUT_OF_VALIDITY",
            hint="Clearance has to satisfy the transient-derived AND the "
                 "peak-derived table, so one branch running off the end is "
                 "enough to make the answer unknown.  The standard's table "
                 "ends here - do not extrapolate a safety distance.",
            extra=out,
        )

    used = [v for v in (c_transient, c_peak) if v is not None]
    value = max(used)
    extra["clearance_from_transient_mm"] = c_transient
    extra["clearance_from_peak_mm"] = c_peak
    extra["governing_branch"] = (
        "transient" if c_transient is not None and c_transient >= value
        else "peak")

    altf = _altitude_factor(doc, alt)
    if altf is None:
        return err("altitude is past the end of Table A.2", "OUT_OF_VALIDITY",
                   extra={"altitude_m": alt,
                          "ladder_max": (doc.get("clearance_altitude_factor") or [[None]])[-1][0]})
    factor, alt_bound = altf
    value *= factor
    extra["altitude_factor"] = factor
    extra["altitude_step_upper_m"] = alt_bound

    return ok(
        value=round(value, 6),
        unit="mm",
        model=MODEL,
        basis="IEC 60664-1:2020-05 Tables F.2/F.8 + A.2 (clearance), "
              "inhomogeneous field",
        precision=PRECISION,
        assumptions={
            "peak_v": pk,
            "transient_v": t_volts,
            "pollution_degree": pd,
            "insulation": ins,
            "altitude_m": alt,
        },
        extra=extra,
    )


# ------------------------------------------------------- impulse withstand

def impulse_withstand_v(rated_voltage_v, overvoltage_category="II"):
    """Rated impulse withstand voltage in V peak (IEC 60664-1 Table F.1)."""
    doc, why = _load()
    if doc is None:
        return err("IEC 60664-1 tables are not available, refusing to answer",
                   "SAFETY_TABLE_MISSING", hint=why, extra={"model_file": DATA_PATH})
    cat = _OVC.get(str(overvoltage_category or "").strip().upper())
    if cat is None:
        return err("overvoltage_category must be one of I, II, III, IV",
                   "ARG_INVALID", extra={"got": overvoltage_category})
    v, e = _num(rated_voltage_v, "rated_voltage_v")
    if e:
        return e
    if v < 0:
        return err("rated_voltage_v must be >= 0", "ARG_INVALID", extra={"got": v})
    table = doc.get("impulse_withstand_v") or {}
    entry = {"steps": table.get(cat)}
    if not entry["steps"]:
        return err("no impulse table for this category", "SAFETY_ROW_NOT_FOUND",
                   extra={"requested": cat})
    hit = _lookup(entry, v)
    if hit is None:
        # The table tops out at the highest standardised system voltage.
        rated_max = float(entry["steps"][-1][0])
        return err(
            "rated voltage is past the end of Table F.1",
            "OUT_OF_VALIDITY",
            hint="Above the highest tabulated system voltage the standard no "
                 "longer assigns an impulse withstand voltage; the clearance "
                 "must come from the actual transient environment.",
            extra={"input": v, "ladder_max": rated_max, "category": cat},
        )
    return ok(
        value=hit[0],
        unit="V",
        model=MODEL,
        basis="IEC 60664-1:2020-05 Table F.1 (rated impulse withstand voltage)",
        precision=PRECISION,
        assumptions={"rated_voltage_v": v, "overvoltage_category": cat},
        extra={"ladder_step_upper_v": hit[1], "edition": doc.get("edition")},
    )


# ------------------------------------------------------------ groove width

def min_groove_width_mm(clearance_mm_value, pollution_degree=2):
    """Minimum width of a groove, clause 6.8 (needs the clearance as input)."""
    doc, why = _load()
    if doc is None:
        return err("IEC 60664-1 tables are not available, refusing to answer",
                   "SAFETY_TABLE_MISSING", hint=why, extra={"model_file": DATA_PATH})
    pd, e = _pd(pollution_degree)
    if e:
        return e
    c, e = _num(clearance_mm_value, "clearance_mm")
    if e:
        return e
    if c <= 0:
        return err("clearance_mm must be > 0 to derive a groove width",
                   "ARG_INVALID", extra={"got": c})
    if abs(c) < 3.0:
        return ok(
            value=round(c / 3.0, 6),
            unit="mm",
            model=MODEL,
            basis="IEC 60664-1:2020-05 clause 6.8 (clearance < 3 mm -> width/3)",
            precision=PRECISION,
            assumptions={"clearance_mm": c, "pollution_degree": pd},
            extra={"rule": "clearance_divided_by_3", "edition": doc.get("edition")},
        )
    table = doc.get("min_groove_width_mm") or {}
    value = table.get("PD%d" % pd)
    if value is None:
        return err(
            "clause 6.8 gives no groove width at pollution degree %d" % pd,
            "STANDARD_SCOPE",
            hint="PD4 has no specified groove width (same reason creepage has "
                 "none).  Do not substitute another pollution degree.",
            extra={"pollution_degree": pd},
        )
    return ok(
        value=float(value),
        unit="mm",
        model=MODEL,
        basis="IEC 60664-1:2020-05 clause 6.8 (groove width by pollution degree)",
        precision=PRECISION,
        assumptions={"clearance_mm": c, "pollution_degree": pd},
        extra={"rule": "by_pollution_degree", "edition": doc.get("edition")},
    )


# ------------------------------------------------------------- combined

def spacing(voltage_rms_v, peak_v=None, transient_v=None, pollution_degree=2,
            material_group="IIIa", insulation="basic", pcb_material=False,
            altitude_m=0, coating=False):
    """Creepage + clearance together, and which one governs.

    Mirrors the standard's own end rule: the creepage distance may not be
    smaller than the clearance, so the governing number is the larger of the
    two.  ``peak_v`` defaults to ``voltage_rms_v * sqrt(2)``.
    """
    v, e = _num(voltage_rms_v, "voltage_rms_v")
    if e:
        return e
    if peak_v is None:
        peak_v = v * 2.0 ** 0.5
    creep = creepage_mm(v, pollution_degree=pollution_degree,
                        material_group=material_group, insulation=insulation,
                        pcb_material=pcb_material, altitude_m=altitude_m,
                        coating=coating)
    clear = clearance_mm(peak_v, transient_v=transient_v,
                         pollution_degree=pollution_degree,
                         insulation=insulation, altitude_m=altitude_m)
    if creep.get("status") != "ok":
        creep["failed_part"] = "creepage"
        return creep
    if clear.get("status") != "ok":
        clear["failed_part"] = "clearance"
        return clear

    c_req = float(creep["value"])
    clr = float(clear["value"])
    governing = "clearance" if clr > c_req else "creepage"
    value = max(c_req, clr)
    groove = min_groove_width_mm(clr, pollution_degree=pollution_degree)

    extra = {
        "creepage_mm": c_req,
        "clearance_mm": clr,
        "governing": governing,
        "creepage_detail": creep,
        "clearance_detail": clear,
        "edition": creep.get("edition"),
    }
    if groove.get("status") == "ok":
        extra["min_groove_width_mm"] = groove["value"]
    else:
        extra["min_groove_width_note"] = groove.get("reason_code")
    if creep.get("altitude_note"):
        extra["altitude_note"] = creep["altitude_note"]

    return ok(
        value=round(value, 6),
        unit="mm",
        model=MODEL,
        basis="IEC 60664-1:2020-05 (creepage Table F.5 vs clearance Tables "
              "F.2/F.8; the larger governs)",
        precision=PRECISION,
        assumptions={
            "voltage_rms_v": v,
            "peak_v": peak_v,
            "transient_v": transient_v,
            "pollution_degree": pollution_degree,
            "material_group": _mg(material_group)[0] or material_group,
            "insulation": insulation,
            "pcb_material": bool(pcb_material),
            "altitude_m": altitude_m,
        },
        extra=extra,
    )


def info():
    """What this engine covers - for capabilities / safety_table_check."""
    doc, why = _load()
    if doc is None:
        return err("IEC 60664-1 tables are not available", "SAFETY_TABLE_MISSING",
                   hint=why, extra={"model_file": DATA_PATH})
    creep = doc.get("creepage") or {}
    steps = sum(len(v.get("steps") or []) for v in creep.values())
    return ok(
        value=len(creep),
        unit="ladders",
        model=MODEL,
        basis="IEC 60664-1:2020-05: Tables F.5, F.2, F.8, A.2, F.1, clause 6.8",
        precision=PRECISION,
        assumptions={"inhomogeneous field only": True},
        extra={
            "edition": doc.get("edition"),
            "creepage_ladders": len(creep),
            "creepage_steps": steps,
            "has_altitude": bool(doc.get("clearance_altitude_factor")),
            "has_impulse": bool(doc.get("impulse_withstand_v")),
            "has_groove": bool(doc.get("min_groove_width_mm")),
            "clause_map": doc.get("clause_map"),
            "not_covered": doc.get("not_covered"),
            "sources": doc.get("sources"),
        },
    )
