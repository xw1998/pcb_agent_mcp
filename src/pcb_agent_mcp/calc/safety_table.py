"""Can a safety-distance table be trusted? This module is the thing that answers.

`calc/creepage.py` fails closed when the table is not verified, which is right but
incomplete: "not verified" used to mean "nobody wrote it", and nothing could tell a
correctly transcribed table from a plausible-looking one. Here that gap is closed by
making the table itself checkable - provenance, monotonicity, and completeness - so a
table is either accepted by rules that a bad transcription cannot pass, or named.

What these rules CAN check: that every row says which table it came from, in which
edition, with two independent sources; that the distance never decreases as voltage
rises; that it never decreases as the material group gets worse; that PD1 <= PD2 <=
PD3 on the same voltage; that no two rows claim the same key.

What they CANNOT check: whether a source transcribed the standard correctly. Two
blogs copying each other pass rule one and fail nothing. That is why the shipped file
keeps `verified: false` and carries its candidate rows as *evidence*, not as answers.
"""
import json
import os

DATA_PATH = os.path.join(
    os.path.dirname(os.path.dirname(os.path.dirname(os.path.dirname(
        os.path.abspath(__file__))))),
    "data", "creepage_tables.json",
)

MATERIAL_ORDER = {"I": 0, "II": 1, "IIIA": 2, "IIIB": 3}
POLLUTION_ORDER = {"PD1": 1, "PD2": 2, "PD3": 3, "PD4": 4}
REQUIRED_ROW_FIELDS = ("voltage_v", "pollution_degree", "material_group",
                       "insulation", "distance_mm", "table")
MIN_SOURCES = 2


def _check(name, ok, detail):
    return {"check": name, "ok": bool(ok), "detail": detail}


def _pairs(rows, key_fn):
    """Group by key_fn and yield (key, rows sorted by voltage)."""
    groups = {}
    for r in rows:
        groups.setdefault(key_fn(r), []).append(r)
    for key, group in sorted(groups.items()):
        yield key, sorted(group, key=lambda r: float(r["voltage_v"]))


def validate(doc, min_sources=MIN_SOURCES):
    """Judge a table document. Pure: takes the parsed JSON, returns a report."""
    checks = []
    problems = []

    schema = doc.get("schema")
    checks.append(_check("schema_is_current", schema == 2,
                         "schema=%r (this validator reads 2)" % (schema,)))

    verified = doc.get("verified") is True
    checks.append(_check("declared_verified", verified,
                         "top-level verified=%r" % (doc.get("verified"),)))

    edition = str(doc.get("edition") or "").strip()
    checks.append(_check("edition_named", bool(edition),
                         "edition=%r - a distance without its edition is not a "
                         "requirement" % edition))

    tables = doc.get("tables") or {}
    rows = []
    for key, table_rows in sorted(tables.items()):
        if not isinstance(table_rows, list):
            problems.append("table %r is not a list of rows" % key)
            continue
        for i, r in enumerate(table_rows):
            missing = [f for f in REQUIRED_ROW_FIELDS if r.get(f) in (None, "")]
            if missing:
                problems.append("table %r row %d missing %s" % (key, i, ", ".join(missing)))
                continue
            sources = r.get("sources") or []
            if len(sources) < min_sources:
                problems.append(
                    "table %r row %d has %d source(s); %d independent sources are "
                    "required (a single transcription is a rumour, not a requirement)"
                    % (key, i, len(sources), min_sources))
            rows.append(dict(r, __table=key))

    checks.append(_check("every_row_has_provenance", not problems,
                         "%d row(s) inspected" % len(rows) if not problems
                         else "; ".join(problems[:6])))

    # --- monotonicity in voltage -------------------------------------------------
    rising_problems = []
    for key, group in _pairs(rows, lambda r: (r["__table"], str(r["pollution_degree"]).upper(),
                                              str(r["material_group"]).upper(),
                                              str(r["insulation"]).lower())):
        prev = None
        for r in group:
            d = float(r["distance_mm"])
            if d <= 0:
                rising_problems.append("%s: non-positive distance" % (key,))
            if prev is not None and d < prev[1]:
                rising_problems.append(
                    "%s: %.3f mm at %s V is below %.3f mm at %s V"
                    % (key, d, r["voltage_v"], prev[1], prev[0]))
            prev = (float(r["voltage_v"]), d)
    checks.append(_check("distance_rises_with_voltage", not rising_problems,
                         "ok" if not rising_problems else "; ".join(rising_problems[:6])))

    # --- monotonicity across material group / pollution degree -------------------
    order_problems = []
    buckets = {}
    for r in rows:
        k = (r["__table"], str(r["insulation"]).lower(), float(r["voltage_v"]),
             str(r["pollution_degree"]).upper())
        buckets.setdefault(k, {})[str(r["material_group"]).upper()] = float(r["distance_mm"])
    for k, by_group in sorted(buckets.items()):
        ordered = [(MATERIAL_ORDER[g], by_group[g]) for g in by_group
                   if g in MATERIAL_ORDER]
        ordered.sort()
        for (_, lo), (_, hi) in zip(ordered, ordered[1:]):
            if hi < lo:
                order_problems.append("%s: a worse material group asks for less (%s)"
                                      % (k, by_group))
    for r_key in ():
        pass
    pd_buckets = {}
    for r in rows:
        k = (r["__table"], str(r["insulation"]).lower(), float(r["voltage_v"]),
             str(r["material_group"]).upper())
        pd_buckets.setdefault(k, {})[str(r["pollution_degree"]).upper()] = \
            float(r["distance_mm"])
    for k, by_pd in sorted(pd_buckets.items()):
        ordered = [(POLLUTION_ORDER[p], by_pd[p]) for p in by_pd if p in POLLUTION_ORDER]
        ordered.sort()
        for (_, lo), (_, hi) in zip(ordered, ordered[1:]):
            if hi < lo:
                order_problems.append("%s: a dirtier environment asks for less (%s)"
                                      % (k, by_pd))
    checks.append(_check("worse_case_never_asks_for_less", not order_problems,
                         "ok" if not order_problems else "; ".join(order_problems[:6])))

    # --- duplicates --------------------------------------------------------------
    seen = {}
    dupes = []
    for r in rows:
        k = (r["__table"], float(r["voltage_v"]), str(r["pollution_degree"]).upper(),
             str(r["material_group"]).upper(), str(r["insulation"]).lower())
        if k in seen:
            dupes.append(str(k))
        seen[k] = True
    checks.append(_check("no_duplicate_rows", not dupes,
                         "ok" if not dupes else "; ".join(dupes[:6])))

    failed = [c for c in checks if not c["ok"]]
    return {
        "usable": not failed,
        "checked_at": "creepage_tables.json",
        "rows": len(rows),
        "checks": checks,
        "failed": [c["check"] for c in failed],
        "why_not_usable": ("every check passed" if not failed else
                           "the table cannot be trusted: " +
                           ", ".join(c["check"] for c in failed)),
    }


def load(path=None):
    path = path or DATA_PATH
    if not os.path.exists(path):
        return None, "table file missing: " + path
    try:
        with open(path, "r", encoding="utf-8") as fh:
            return json.load(fh), None
    except Exception as exc:  # noqa: BLE001
        return None, "table file unreadable: %s" % exc


def check_file(path=None):
    doc, why = load(path)
    if doc is None:
        return {"status": "error", "reason_code": "TABLE_UNREADABLE",
                "message": why, "usable": False}
    report = validate(doc)
    report["status"] = "ok"
    report["path"] = path or DATA_PATH
    report["verified_flag"] = doc.get("verified")
    report["edition"] = doc.get("edition")
    candidates = doc.get("candidates") or {}
    if candidates:
        report["candidates"] = {
            "note": candidates.get("note"),
            "conflict": candidates.get("conflict"),
            "sources": [s.get("url") for s in candidates.get("sources") or []],
        }
    return report


def sources_disagree(candidate_rows):
    """Do the candidate transcriptions disagree anywhere? Returns the disagreements.

    This is the honest reason the shipped table is empty: the public transcriptions
    that were found do not agree with each other, so no row could be filled without
    picking a favourite.
    """
    out = []
    by_key = {}
    for row in candidate_rows or []:
        key = (str(row.get("pollution_degree")).upper(),
               str(row.get("material_group")).upper(), float(row.get("voltage_v")))
        by_key.setdefault(key, []).append(row)
    for key, rows in sorted(by_key.items()):
        values = {float(r["distance_mm"]) for r in rows}
        if len(values) > 1:
            out.append({
                "key": {"pollution_degree": key[0], "material_group": key[1],
                        "voltage_v": key[2]},
                "values_mm": sorted(values),
                "sources": [r.get("source") for r in rows],
            })
    return out

IPC2152_PATH = os.path.join(os.path.dirname(DATA_PATH), "ipc2152_tables.json")
IPC2152_REQUIRED = ("copper_oz", "delta_t_c", "area_mil2", "current_a", "table")


def validate_ipc2152(doc, min_sources=MIN_SOURCES):
    """Same idea for the current-capacity charts: provenance plus monotonicity.

    IPC-2152 is a set of measured charts, not a formula, so the two checks that
    catch a fabricated transcription are: does every point carry two sources, and
    does current rise with cross-section and rise with allowed temperature rise.
    """
    checks = []
    problems = []
    checks.append(_check("schema_is_current", doc.get("schema") == 2,
                         "schema=%r" % (doc.get("schema"),)))
    checks.append(_check("declared_verified", doc.get("verified") is True,
                         "top-level verified=%r" % (doc.get("verified"),)))
    checks.append(_check("edition_named", bool(str(doc.get("edition") or "").strip()),
                         "edition=%r" % (doc.get("edition"),)))

    points = doc.get("points") or []
    for i, r in enumerate(points):
        missing = [f for f in IPC2152_REQUIRED if r.get(f) in (None, "")]
        if missing:
            problems.append("point %d missing %s" % (i, ", ".join(missing)))
        elif len(r.get("sources") or []) < min_sources:
            problems.append("point %d has %d source(s); %d required"
                            % (i, len(r.get("sources") or []), min_sources))
    checks.append(_check("every_point_has_provenance", not problems,
                         "%d point(s)" % len(points) if not problems
                         else "; ".join(problems[:6])))

    rising = []
    groups = {}
    for r in points:
        if any(r.get(f) in (None, "") for f in IPC2152_REQUIRED):
            continue
        key = (round(float(r["copper_oz"]), 3), float(r["delta_t_c"]))
        groups.setdefault(key, []).append(r)
    for key, rows in sorted(groups.items()):
        rows = sorted(rows, key=lambda r: float(r["area_mil2"]))
        prev = None
        for r in rows:
            cur = float(r["current_a"])
            if prev is not None and cur < prev[1]:
                rising.append("%s: %s A at %s mil2 is below %s A at %s mil2"
                              % (key, cur, r["area_mil2"], prev[1], prev[0]))
            prev = (float(r["area_mil2"]), cur)
    checks.append(_check("current_rises_with_area", not rising,
                         "ok" if not rising else "; ".join(rising[:6])))

    by_dt = {}
    for r in points:
        if any(r.get(f) in (None, "") for f in IPC2152_REQUIRED):
            continue
        k = (round(float(r["copper_oz"]), 3), float(r["area_mil2"]))
        by_dt.setdefault(k, []).append(r)
    dt_problems = []
    for k, rows in sorted(by_dt.items()):
        rows = sorted(rows, key=lambda r: float(r["delta_t_c"]))
        prev = None
        for r in rows:
            cur = float(r["current_a"])
            if prev is not None and cur < prev[1]:
                dt_problems.append("%s: a hotter budget allows less current" % (k,))
            prev = (float(r["delta_t_c"]), cur)
    checks.append(_check("current_rises_with_delta_t", not dt_problems,
                         "ok" if not dt_problems else "; ".join(dt_problems[:6])))

    failed = [c for c in checks if not c["ok"]]
    return {"usable": not failed, "rows": len(points), "checks": checks,
            "failed": [c["check"] for c in failed],
            "why_not_usable": ("every check passed" if not failed else
                               "the table cannot be trusted: " +
                               ", ".join(c["check"] for c in failed))}


def check_which(which="creepage", path=None):
    """The single entry point behind the safety_table_check tool."""
    which = str(which or "creepage").strip().lower()
    if which in ("creepage", "clearance", "iec60664"):
        return check_file(path)
    if which in ("ipc2152", "ipc-2152", "current"):
        doc, why = load(path or IPC2152_PATH)
        if doc is None:
            return {"status": "error", "reason_code": "TABLE_UNREADABLE",
                    "message": why, "usable": False}
        report = validate_ipc2152(doc)
        report.update({"status": "ok", "path": path or IPC2152_PATH,
                       "verified_flag": doc.get("verified"),
                       "edition": doc.get("edition"),
                       "why_empty": doc.get("why_empty")})
        return report
    return {"status": "error", "reason_code": "ARG_INVALID",
            "message": "which must be creepage or ipc2152"}

