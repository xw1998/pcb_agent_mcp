"""Creepage / clearance - FAIL CLOSED by design.

There is no trustworthy memorised number here. IEC 60664-1 / 62368-1 tables
depend on voltage, pollution degree, material group (CTI), insulation type,
altitude and coating. Until a verified table is loaded this tool refuses.
"""
import json
import os

from .result import ok, err

DATA_PATH = os.path.join(
    os.path.dirname(os.path.dirname(os.path.dirname(os.path.dirname(
        os.path.abspath(__file__))))),
    "data", "creepage_tables.json",
)

STANDARD_HINT = (
    "Creepage distance is not a number anyone should recall from memory. "
    "Pick the safety standard family first (IEC 60664-1 / IEC 62368-1 / "
    "instrument standard), then load its table into data/creepage_tables.json "
    "with the table number, standard edition and a verified flag."
)


def _load():
    if not os.path.exists(DATA_PATH):
        return None, "table file missing: " + DATA_PATH
    try:
        with open(DATA_PATH, "r", encoding="utf-8") as fh:
            data = json.load(fh)
    except Exception as exc:  # noqa: BLE001
        return None, "table file unreadable: %s" % exc
    if not data.get("verified"):
        return None, "table file present but not verified"
    return data, None


def _lookup(kind, safety_standard, pollution_degree, material_group, insulation,
            voltage_v, altitude_m, coating):
    data, why = _load()
    if data is None:
        return err(
            "safety distance tables are not available, refusing to answer",
            "SAFETY_TABLE_MISSING",
            hint=STANDARD_HINT,
            extra={"detail": why, "requested": {
                "kind": kind,
                "safety_standard": safety_standard,
                "voltage_v": voltage_v,
            }},
        )
    std = str(safety_standard or "").strip()
    if not std:
        return err(
            "safety_standard is required",
            "SAFETY_STANDARD_REQUIRED",
            hint="Different standards give different distances. Name the family explicitly.",
        )
    tables = data.get("tables", {})
    key = "%s|%s" % (kind, std)
    if key not in tables:
        return err("no table for this standard/kind", "SAFETY_TABLE_NOT_FOUND",
                   hint="available: " + ", ".join(sorted(tables.keys())))
    rows = tables[key]
    pd = "PD%s" % pollution_degree
    mg = str(material_group).upper()
    try:
        v = float(voltage_v)
    except (TypeError, ValueError):
        return err("voltage_v numeric required", "ARG_INVALID")

    candidates = [r for r in rows
                  if str(r.get("pollution_degree")).upper() == pd.upper()
                  and str(r.get("material_group")).upper() == mg
                  and str(r.get("insulation")).lower() == str(insulation).lower()]
    if not candidates:
        return err("no table row for this combination", "SAFETY_ROW_NOT_FOUND",
                   extra={"requested": {"pollution_degree": pd,
                                        "material_group": mg,
                                        "insulation": insulation}})
    candidates.sort(key=lambda r: float(r["voltage_v"]))
    below = [r for r in candidates if float(r["voltage_v"]) <= v]
    above = [r for r in candidates if float(r["voltage_v"]) > v]
    if not below:
        return err("voltage below table range", "OUT_OF_TABLE",
                   hint="use the lowest tabulated row only with engineering review")
    row = below[-1]
    exact = (not above) or abs(float(row["voltage_v"]) - v) < 1e-9
    value = float(row["distance_mm"])

    extra = {"table_row_voltage_v": float(row["voltage_v"])}
    if not exact:
        extra["interpolation_note"] = (
            "Voltage falls between tabulated steps. Returned the lower step "
            "(conservative). Do not linearly interpolate safety tables without "
            "checking the standard's own rule."
        )
    if coating:
        extra["coating_note"] = (
            "Coating can relax the requirement, but only where coverage is complete "
            "(pin roots, board edge and mask openings are usually NOT covered)."
        )
    if altitude_m and float(altitude_m) > 2000:
        extra["altitude_note"] = (
            "Above 2000 m the tables require an altitude correction factor; "
            "this build does not apply it - treat the result as insufficient."
        )
    return ok(
        value=round(value, 3),
        unit="mm",
        model="standard_table_lookup",
        basis="%s table %s (edition %s)" % (std, row.get("table", "?"),
                                            data.get("edition", "?")),
        precision="table step (values are tabulated, not continuous)",
        assumptions={
            "kind": kind,
            "voltage_v": v,
            "pollution_degree": pd,
            "material_group": mg,
            "insulation": insulation,
            "altitude_m": altitude_m,
            "coating": coating,
        },
        extra=extra,
    )


def creepage_required(voltage_v, pollution_degree=2, material_group="IIIa",
                      insulation="basic", altitude_m=0, coating=False,
                      safety_standard=None):
    return _lookup("creepage", safety_standard, pollution_degree, material_group,
                   insulation, voltage_v, altitude_m, coating)


def clearance_required(voltage_v, pollution_degree=2, material_group="IIIa",
                       insulation="basic", altitude_m=0, coating=False,
                       safety_standard=None):
    return _lookup("clearance", safety_standard, pollution_degree, material_group,
                   insulation, voltage_v, altitude_m, coating)
