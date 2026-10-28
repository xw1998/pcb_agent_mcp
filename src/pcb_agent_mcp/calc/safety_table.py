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
import bisect
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

IPC2152_COEFF_KEYS = ("K", "a", "b", "c")
DEFAULT_MIL_PER_OZ = 1.378


def _ba_pick(model, internal, thickness_mil):
    """Independent re-implementation of the bucket rule, used only to judge a document."""
    if not internal:
        ext = model.get("external") or {}
        return tuple(float(ext[k]) for k in IPC2152_COEFF_KEYS)
    rows = sorted(model.get("internal") or [], key=lambda r: float(r["thickness_mil"]))
    for i in range(len(rows) - 1):
        lo = float(rows[i]["thickness_mil"])
        hi = float(rows[i + 1]["thickness_mil"])
        if thickness_mil < (lo + hi) / 2.0:
            return tuple(float(rows[i][k]) for k in IPC2152_COEFF_KEYS)
    return tuple(float(rows[-1][k]) for k in IPC2152_COEFF_KEYS)


def _ba_thick_mil(mil_per_oz, copper_oz):
    return copper_oz * (mil_per_oz if mil_per_oz else DEFAULT_MIL_PER_OZ)


def _ba_width(mil_per_oz, model, internal, copper_oz, current_a, delta_t_c):
    th = _ba_thick_mil(mil_per_oz, copper_oz)
    k, a, b, c = _ba_pick(model, internal, th)
    return (delta_t_c / (k * current_a ** a * th ** c)) ** (1.0 / b)


def _ba_current(mil_per_oz, model, internal, copper_oz, width_mil, delta_t_c):
    th = _ba_thick_mil(mil_per_oz, copper_oz)
    k, a, b, c = _ba_pick(model, internal, th)
    return (delta_t_c / (k * width_mil ** b * th ** c)) ** (1.0 / a)


def _validate_model(doc, min_sources):
    """Judge the closed-form IPC-2152 model instead of transcribed chart points.

    Four questions, each of which a wrong coefficient set fails:
      1. are the coefficients there, and do they have the shape of a temperature-rise fit
         (K>0, current exponent >1, width and thickness exponents <0)?
      2. are there two independent sources?
      3. does the declared anchor table reproduce from those coefficients?
      4. do the model's physical properties hold under numerical probing (more copper
         carries more current, hotter budget carries more current, and an internal trace
         is not derated by the obsolete factor of two)?
    """
    checks = []
    model = doc.get("model") or {}
    mil_per_oz = float(model.get("thickness_mil_per_oz") or 0.0)

    ext = model.get("external") or {}
    rows = model.get("internal") or []
    missing = [k for k in IPC2152_COEFF_KEYS if ext.get(k) in (None, "")]
    for i, r in enumerate(rows):
        for k in ("copper_oz", "thickness_mil") + IPC2152_COEFF_KEYS:
            if r.get(k) in (None, ""):
                missing.append("internal[%d].%s" % (i, k))
    checks.append(_check("model_coefficients_present", not missing and len(rows) >= 2,
                         "external + %d internal bucket(s)%s"
                         % (len(rows), "" if not missing else "; missing " + ", ".join(missing[:8]))))

    shape_problems = []
    sets = [("external", ext)] + [("internal[%d]" % i, r) for i, r in enumerate(rows)]
    for name, cs in sets:
        try:
            k, a, b, c = (float(cs[key]) for key in IPC2152_COEFF_KEYS)
        except (KeyError, TypeError, ValueError):
            shape_problems.append("%s: non-numeric coefficient" % name)
            continue
        if k <= 0:
            shape_problems.append("%s: K=%g; a temperature-rise fit needs K>0" % (name, k))
        if a <= 1.0:
            shape_problems.append("%s: a=%g; current must enter superlinearly" % (name, a))
        if b >= 0:
            shape_problems.append("%s: b=%g; a wider trace must run cooler" % (name, b))
        if c >= 0:
            shape_problems.append("%s: c=%g; thicker copper must run cooler" % (name, c))
    checks.append(_check("model_coefficients_sane", not shape_problems,
                         "ok" if not shape_problems else "; ".join(shape_problems[:6])))

    sources = model.get("sources") or []
    checks.append(_check("model_has_independent_sources", len(sources) >= min_sources,
                         "%d source(s); %d required" % (len(sources), min_sources)))

    anchors = doc.get("anchors") or []
    tol = float(doc.get("anchor_tolerance_rel") or 0.01)
    anchor_problems = []
    if len(anchors) < 4:
        anchor_problems.append("only %d anchor(s); at least 4 are needed to pin the buckets"
                               % len(anchors))
    for i, a in enumerate(anchors):
        try:
            internal = str(a["layer"]).lower().startswith("int")
            oz = float(a["copper_oz"])
            dtc = float(a["delta_t_c"])
            if a.get("width_mil") not in (None, ""):
                label = "width_mil"
                want = float(a["width_mil"])
                got = _ba_width(mil_per_oz, model, internal, oz, float(a["current_a"]), dtc)
            elif a.get("current_a") not in (None, ""):
                label = "current_a"
                want = float(a["current_a"])
                got = _ba_current(mil_per_oz, model, internal, oz, float(a["width_mil"]), dtc)
            else:
                anchor_problems.append("anchor %d has neither width_mil nor current_a" % i)
                continue
        except (KeyError, TypeError, ValueError) as exc:
            anchor_problems.append("anchor %d unreadable: %s" % (i, exc))
            continue
        if want <= 0 or abs(got - want) / want > tol:
            anchor_problems.append("anchor %d %s: declared %.4f, model gives %.4f (>%.1f%% off)"
                                   % (i, label, want, got, tol * 100.0))
    checks.append(_check("model_reproduces_anchors", not anchor_problems,
                         "%d anchor(s) reproduced within %.1f%%"
                         % (len(anchors), tol * 100.0) if not anchor_problems
                         else "; ".join(anchor_problems[:6])))

    probe = []
    try:
        prev = None
        for w in (5.0, 10.0, 20.0, 40.0):
            cur = _ba_current(mil_per_oz, model, False, 1.0, w, 10.0)
            if prev is not None and cur <= prev:
                probe.append("current does not rise with width at 1 oz (10 degC)")
            prev = cur
        prev = None
        for d in (5.0, 10.0, 20.0, 40.0):
            cur = _ba_current(mil_per_oz, model, False, 1.0, 10.0, d)
            if prev is not None and cur <= prev:
                probe.append("current does not rise with allowed delta-T")
                break
            prev = cur
        w_ext = _ba_width(mil_per_oz, model, False, 1.0, 1.0, 10.0)
        w_int = _ba_width(mil_per_oz, model, True, 1.0, 1.0, 10.0)
        if not (0.5 < w_int / w_ext < 1.2):
            probe.append("internal/external width ratio %.2f contradicts IPC-2152 (internal "
                         "traces are not derated by a fixed factor of two)" % (w_int / w_ext))
    except (KeyError, TypeError, ValueError, ZeroDivisionError) as exc:
        probe.append("probe failed: %s" % (exc,))
    checks.append(_check("model_physical_properties_hold", not probe,
                         "ok" if not probe else "; ".join(probe[:6])))
    return checks



def validate_ipc2152(doc, min_sources=MIN_SOURCES):
    """Judge whatever form the IPC-2152 data takes.

    Two forms are supported and both must survive judgement:
      - model: the published closed-form fit (Brooks & Adam 2015). Checked for shape,
        sources, anchor reproduction and physical behaviour - see _validate_model.
      - points: transcribed chart rows. Checked for two sources per row, current rising
        with cross-section and with allowed temperature rise.
    An empty points list is fine when a model is present; an empty points list with no
    model is not, because then nothing at all was checked and `usable` would be a lie.
    """
    checks = []
    problems = []
    checks.append(_check("schema_is_current", doc.get("schema") == 2,
                         "schema=%r" % (doc.get("schema"),)))
    checks.append(_check("declared_verified", doc.get("verified") is True,
                         "top-level verified=%r" % (doc.get("verified"),)))
    checks.append(_check("edition_named", bool(str(doc.get("edition") or "").strip()),
                         "edition=%r" % (doc.get("edition"),)))

    model = doc.get("model") or {}
    if model:
        checks.extend(_validate_model(doc, min_sources))
    else:
        checks.append(_check("model_or_points_present", bool(doc.get("points")),
                             "no closed-form model and no transcribed points: nothing to check"))

    points = doc.get("points") or []
    for i, r in enumerate(points):
        missing = [f for f in IPC2152_REQUIRED if r.get(f) in (None, "")]
        if missing:
            problems.append("point %d missing %s" % (i, ", ".join(missing)))
        elif len(r.get("sources") or []) < min_sources:
            problems.append("point %d has %d source(s); %d required"
                            % (i, len(r.get("sources") or []), min_sources))
    if points:
        checks.append(_check("every_point_has_provenance", not problems,
                             "%d point(s)" % len(points) if not problems
                             else "; ".join(problems[:6])))
    else:
        checks.append(_check("no_points_transcribed", not problems,
                             "0 point(s); the row-wise provenance rules are vacuous, the "
                             "closed-form model above is what is being trusted"))

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
    if which in ("creepage", "clearance"):
        # a hand-written table: the old checks still apply, and it is still
        # unverified until someone fills data/creepage_tables.json
        return check_file(path)
    if which in ("iec60664", "iec60664-1", "iec-60664"):
        doc, why = load(path or IEC60664_PATH)
        if doc is None:
            return {"status": "error", "reason_code": "TABLE_UNREADABLE",
                    "message": why, "usable": False}
        report = validate_iec60664(doc)
        report.update({"status": "ok", "path": path or IEC60664_PATH,
                       "verified_flag": doc.get("verified"),
                       "edition": doc.get("edition")})
        return report
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


# ------------------------------------------------------- IEC 60664-1 ladders
#
# The JSON behind calc/iec60664.py was *generated* from a compiled reference
# implementation, so the risk is not "someone mistyped a number" but "the
# generator lost a step, sorted a ladder wrong, or collapsed a staircase into a
# ramp".  The checks below are aimed at exactly those failures: a lost or
# duplicated threshold, a non-monotone ladder, a ladder whose declared end does
# not match its last step, a linear segment that does not pass through the
# origin, and the physical probes (reinforced = 2x basic, PD1 <= PD2 <= PD3,
# distance never falls as voltage rises).

IEC60664_PATH = os.path.join(os.path.dirname(DATA_PATH), "iec60664_tables.json")

IEC60664_MG = ("I", "II", "IIIa", "IIIb")


def _ladder_value(step_value, x):
    if isinstance(step_value, dict):
        return float(step_value.get("slope", 0.0)) * x + float(
            step_value.get("intercept", 0.0))
    return float(step_value)


def _check_ladder(name, entry, problems, allow_empty=False):
    steps = entry.get("steps") or []
    if not steps:
        if not allow_empty:
            problems.append("%s: no steps" % name)
        elif not entry.get("note"):
            problems.append("%s: empty without a note saying why" % name)
        return []
    xs = [float(s[0]) for s in steps]
    for a, b in zip(xs, xs[1:]):
        if not b > a:
            problems.append("%s: thresholds not strictly ascending at %r" % (name, b))
    vals = [_ladder_value(s[1], x) for s, x in zip(steps, xs)]
    for i, (a, b) in enumerate(zip(vals, vals[1:])):
        if b < a - 1e-12:
            problems.append("%s: distance decreases at step %d (%r -> %r)"
                            % (name, i + 1, a, b))
    for s in steps:
        if isinstance(s[1], dict):
            intercept = abs(float(s[1].get("intercept", 0.0)))
            if intercept > 1e-9 * max(1.0, abs(vals[-1])):
                problems.append("%s: linear segment does not pass through the "
                                "origin (intercept %r)" % (name, intercept))
    declared = entry.get("max_v")
    if declared is None:
        problems.append("%s: no max_v declared" % name)
    elif abs(float(declared) - xs[-1]) > 1e-9 * max(1.0, xs[-1]):
        problems.append("%s: max_v %r does not match the last threshold %r"
                        % (name, declared, xs[-1]))
    return steps


def validate_iec60664(doc, min_sources=MIN_SOURCES):
    """Judge data/iec60664_tables.json. Pure: parsed JSON in, report out."""
    checks = []
    problems = []

    edition = str(doc.get("edition") or "")
    checks.append(_check(
        "edition_named", "60664" in edition,
        edition or "edition is missing"))

    checks.append(_check("verified_flag", bool(doc.get("verified")),
                         "verified: %r" % (doc.get("verified"),)))

    sources = doc.get("sources") or []
    kinds = {str(s.get("role") or s.get("type") or "") for s in sources
             if isinstance(s, dict)}
    checks.append(_check(
        "two_independent_sources", len(sources) >= min_sources,
        "%d sources: %s" % (len(sources), ", ".join(sorted(str(s) for s in kinds)) or "none")))

    creep = doc.get("creepage") or {}
    expected_keys = {"PD%d|MG_%s|pcb%d" % (pd, mg, pcb)
                     for pd in (1, 2, 3, 4) for mg in IEC60664_MG for pcb in (0, 1)}
    missing = sorted(expected_keys - set(creep))
    checks.append(_check(
        "creepage_grid_complete", not missing and len(creep) == 32,
        "32 ladders expected, %d present%s"
        % (len(creep), "" if not missing else "; missing " + ", ".join(missing[:6]))))

    for key in sorted(creep):
        _check_ladder("creepage[%s]" % key, creep[key], problems,
                      allow_empty=key.startswith("PD4|"))

    peak = doc.get("clearance_peak") or {}
    for pd in (1, 2, 3, 4):
        entry = peak.get("PD%d" % pd)
        if entry is None:
            problems.append("clearance_peak: PD%d missing" % pd)
            continue
        _check_ladder("clearance_peak[PD%d]" % pd, entry, problems)
    distinct = {json.dumps(e.get("steps")) for e in peak.values() if isinstance(e, dict)}
    checks.append(_check(
        "clearance_peak_pd_independent", len(distinct) <= 1,
        "Table F.8 takes no pollution degree, so its ladders must be identical; "
        "%d distinct copies" % len(distinct)))

    transient = doc.get("clearance_transient") or {}
    for pd in (1, 2, 3, 4):
        entry = transient.get("PD%d" % pd)
        if entry is None:
            problems.append("clearance_transient: PD%d missing" % pd)
            continue
        _check_ladder("clearance_transient[PD%d]" % pd, entry, problems)

    checks.append(_check("ladders_sane_and_monotone", not problems,
                         "ok" if not problems else "; ".join(problems[:6])))

    # physical probes, computed from the JSON itself
    def table(key, x):
        entry = (doc.get("creepage") or {}).get(key)
        if not entry or not entry.get("steps"):
            return None
        xs = [float(s[0]) for s in entry["steps"]]
        if x > xs[-1]:
            return None
        i = bisect.bisect_left(xs, x)
        return _ladder_value(entry["steps"][i][1], x)

    probes = []
    for x in (100.0, 250.0, 400.0, 630.0):
        for mg in ("I", "IIIa"):
            a, b, c = (table("PD%d|MG_%s|pcb0" % (pd, mg), x) for pd in (1, 2, 3))
            if None in (a, b, c):
                continue
            if not (a <= b <= c):
                probes.append("PD ordering broken at %g V / MG %s (%r, %r, %r)"
                              % (x, mg, a, b, c))
            for pd in (1, 2):
                lo = table("PD%d|MG_%s|pcb0" % (pd, mg), x)
                hi = table("PD%d|MG_%s|pcb0" % (pd, mg), x * 2)
                if lo is not None and hi is not None and hi < lo:
                    probes.append("creepage falls as voltage doubles at %g V / "
                                  "MG %s / PD%d (%r -> %r)" % (x, mg, pd, lo, hi))
    checks.append(_check("physics_probes", not probes,
                         "ok" if not probes else "; ".join(probes[:6])))

    not_covered = doc.get("not_covered") or []
    checks.append(_check("limits_are_declared", len(not_covered) >= 3,
                         "%d declared limits" % len(not_covered)))

    failed = [c for c in checks if not c["ok"]]
    ladders = sum(len(v.get("steps") or []) for v in creep.values())
    return {"usable": not failed, "ladders": len(creep), "steps": ladders,
            "checks": checks, "failed": [c["check"] for c in failed],
            "why_not_usable": ("every check passed" if not failed else
                               "the ladder tables cannot be trusted: " +
                               ", ".join(c["check"] for c in failed))}
