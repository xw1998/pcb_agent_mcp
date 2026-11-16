"""One audit, five priorities, and the order they must be attacked in.

Two libraries are merged here, and keeping them apart is the point:

* **P0 comes from the fab capability library** (``jlc/data/*.json``), where every
  number carries a source id, a tier, a URL and the verbatim quote. It is the
  only layer whose answer depends on a process the user must declare, so with no
  process declared P0 is *not evaluated* - it is not passed either.
* **P1..P4 come from the design catalogue** (``calc/rules.py``) plus the
  geometric rules this package measures itself (connectivity, 45 degrees,
  courtyard, off-board).

A rule whose evidence is missing is `undetermined`. A rule below the first
failing level is `deferred`. Neither is a pass, and neither is reported as the
next thing to do.
"""
from ..calc import rules as _rules
from ..jlc import spec as _spec
from . import measure as _measure
from .levels import (LEVELS, LEVEL_NAMES, LEVEL_MEANING, BLOCKING_LEVELS,
                      resolve)

# Catalogue rule -> priority. Priority is NOT severity: `min_trace_width` is
# "Low" in the catalogue because it is a convention, and P0 here because a fab
# will refuse the board; `rf_z0_tolerance` is "High" and P3 because an RF board
# that misses its impedance still assembles and still connects.
_CATALOG_LEVEL = {
    "layout.reference_plane_continuous": "P1",
    "layout.decoupling_distance": "P1",
    "layout.decoupling_same_layer": "P1",
    "layout.layer_change_return_via": "P1",
    "layout.power_trace_width": "P1",
    "layout.min_trace_width": "P0",
    "layout.min_via_hole": "P0",
    "layout.bulk_per_ic": "P2",
    # A rail with no bypass part at all is the missing part found on RK3506: it
    "layout.hf_per_rail": "P1",

    "layout.power_pad_thermal_vias": "P2",
    "layout.crystal_keepout": "P2",
    # P1: the field, the loop and the heat are power-integrity facts, not
    # conventions. A bead at P1 is the same rule at a smaller scale.
    "layout.magnetic_keepout": "P1",
    "layout.magnetic_to_hs_digital": "P1",
    "layout.magnetic_via_keepout": "P1",
    "layout.magnetic_keepout_deep": "P2",

    "layout.magnetic_keepout_graze": "P3",
    "layout.crystal_keepout_adjacent": "P2",
    "layout.silkscreen_over_pad": "P2",
    "layout.test_points_present": "P2",
    "layout.rf_z0_tolerance": "P3",
    "layout.rf_right_angle_bend": "P3",
    "layout.rf_bend_radius": "P3",
    "layout.rf_spacing_3w": "P3",
    "layout.rf_to_board_edge": "P3",
    "layout.antenna_keepout": "P3",
    "layout.diffpair_skew": "P3",
    "layout.diffpair_gap_constant": "P3",
    "layout.stitching_via_pitch": "P3",
    "layout.crystal_guard": "P4",
}

# The geometric rules this package answers itself. Each one is a named failure
# mode with a measurement behind it; the fix is a sentence an agent can act on.
_OWN_RULES = (
    {"id": "layout.connectivity_complete", "level": "P1",
     "title": "every net is one piece of copper",
     "fact": "unconnected_pairs", "kind": "max", "limit": 0.0, "unit": "pairs",
     "source": "geometry: union-find over each net's own copper (oracle.net_gaps)",
     "fix": "join the two nearest pieces of each split net, or delete the piece "
            "nothing needs; the audit lists the worst nets by missing joins"},
    {"id": "layout.segment_angle_45", "level": "P3",
     "title": "every track segment is axis-aligned or 45 degrees",
     "fact": "non_45_segments", "kind": "max", "limit": 0.0, "unit": "segments",
     "source": "workshop rule: a two-segment 45 chamfer at every corner keeps the "
               "copper etchable and the current uniform at the bend",
     "fix": "replace the corner with two segments meeting at 45 degrees (chamfer "
            "about 0.15 mm) instead of a right angle"},
    {"id": "layout.courtyard_clear", "level": "P2",
     "title": "no two parts on one side occupy the same area",
     "fact": "courtyard_overlaps", "kind": "max", "limit": 0.0, "unit": "pairs",
     "source": "assembly: two bodies cannot occupy one place",
     "fix": "move one of the pair apart; the audit names both references"},
    {"id": "layout.parts_on_board", "level": "P2",
     "title": "every part sits on the board",
     "fact": "footprints_off_board", "kind": "max", "limit": 0.0, "unit": "parts",
     "source": "assembly: a part whose body centre is outside the outline cannot "
               "be soldered",
     "fix": "move the part inside the outline, or grow the outline on purpose"},
)


def _own_check(rule, m):
    row = m.get(rule["fact"]) or {}
    out = {"id": rule["id"], "level": rule["level"], "title": rule["title"],
           "unit": rule["unit"], "limit": rule["limit"], "source": rule["source"],
           "fix": rule.get("fix"), "how_to_measure": row.get("method")}
    if "value" not in row:
        out["state"] = "undetermined"
        out["reason"] = row.get("unmeasurable") or "not measured"
        return out
    v = row["value"]
    out["measured"] = v
    out["where"] = row.get("where")
    if rule["kind"] == "max":
        out["margin"] = round(rule["limit"] - v, 4)
        out["state"] = "pass" if v <= rule["limit"] + 1e-9 else "fail"
        cap = row.get("supports")
        # A measurement may be unable to carry a verdict even when it trips the
        # limit (a bbox overlap is a candidate, not a body collision).  The cap
        # never upgrades a state: it only refuses to call a fail a fail.
        if out["state"] == "fail" and cap and cap != "fail":
            out["state"] = cap
            out["measured_state"] = "fail"
            out["reason"] = row.get("why_that_cap") or (
                "the measurement cannot carry this verdict")
    else:                                             # pragma: no cover
        out["state"] = "undetermined"
        out["reason"] = "unknown rule kind"
    if row.get("examples"):
        out["examples"] = row["examples"]
    return out


def _thickness_options():
    """The thickness values the fab library actually sells (quoted list)."""
    for rule in _spec.load("jlc-pcb-capabilities.json")["rules"]:
        if rule.get("id") == "PCB.BOARD.THICKNESS":
            lim = rule.get("limit") or {}
            return [float(v) for v in (lim.get("set") or [])]
    return []


# Silk legibility is not manufacturing: a fab prints a thin or short silk legend,
# it does not refuse the order. Grading it at P0 made six of KiCad's own demo
# boards "unbuildable" for a 0.12 mm default stroke, which is where a ruleset
# stops being a priority list and becomes an obstacle.
_FAB_LEVEL_OVERRIDE = {"PCB.SILK.": "P4"}

# Catalogue rules whose number is the fab library's, so it must not be graded twice.
_GRADED_BY_FAB = ("layout.min_trace_width", "layout.min_via_hole")


def _fab_level(rule_id):
    for prefix, lvl in _FAB_LEVEL_OVERRIDE.items():
        if rule_id.startswith(prefix):
            return lvl
    return "P0"


_FAB_STATE = {
    "BLOCK": "fail",
    "WARN": "pass_with_warning",
    "PASS": "pass",
    "NEEDS_EVIDENCE": "undetermined",
    "N/A": "not_applicable",
}


def _fab_checks(process, m):
    """P0 from the fab library. No process -> one undetermined rule, no guess."""
    if not process:
        return [{"id": "P0.process_declared", "level": "P0",
                 "title": "the fab process is declared (layers, copper_oz)",
                 "state": "undetermined",
                 "reason": "P0 limits are a function of the process: 0.10 mm is legal "
                           "on 1 oz double-sided and illegal on 2 oz. Declare "
                           "process={layers, copper_oz} and this layer is measured "
                           "against the published capability library; without it this "
                           "server will not quote a number it does not have",
                 "fix": "pass process={\"layers\": 2, \"copper_oz\": 1, "
                        "\"thickness_mm\": 1.6, \"surface\": \"HASL_LEADFREE\"}",
                 "source": "jlc/data/jlc-pcb-capabilities.json (T0/T1, quoted)"}], \
            None, None

    measurements = {}
    for key, row in m.items():
        if "value" in row:
            measurements[key] = row["value"]
        elif row.get("unmeasurable", "").startswith("no "):
            # "no internal cut-out on this board" is not a missing measurement,
            # it is an absent object: the rule does not apply, and calling that
            # undetermined would leave P0 permanently incomplete on most boards.
            measurements[key] = _spec.NONE_PRESENT
    rule_sets = [(_spec.load("jlc-pcb-capabilities.json"), "pcb")]
    if process.get("smt") in ("economy", "standard"):
        rule_sets.append((_spec.load("jlc-smt-capabilities.json"), "smt"))
    results, summary, code = _spec.evaluate(process, measurements, rule_sets,
                                           partial=True)
    checks = []
    for row in results:
        state = _FAB_STATE.get(row["verdict"], "undetermined")
        srcs = row.get("source") or {}
        # A recommended-only rule has no limit source; quoting the recommended
        # one keeps every graded rule traceable to the sentence it came from.
        src, kind = srcs.get("limit"), "limit"
        if not src:
            src, kind = srcs.get("recommended"), "recommended"
        c = {"id": row["rule_id"], "level": _fab_level(row["rule_id"]),
             "title": row["name"],
             "key": row["key"], "state": state, "measured": row.get("measured"),
             "limit": row.get("limit"), "recommended": row.get("recommended"),
             "source": src, "source_kind": kind,
             "op": row.get("op"), "flags": row.get("flags") or []}
        if row["verdict"] == "NEEDS_EVIDENCE":
            c["reason"] = ("the library states this rule and the board was not "
                           "measured for it")
        if state == "fail" and isinstance(row.get("measured"), (int, float)) \
                and isinstance(row.get("limit"), (int, float)):
            c["margin"] = row["measured"] - row["limit"]
        if row["verdict"] == "N/A":
            c["reason"] = row.get("na_reason") or "not applicable to this board"
        if row.get("flags"):
            state_note = "; ".join(row["flags"])
            c["cost_trigger"] = state_note
        src = m.get(row["key"]) or {}
        if src.get("where"):
            c["where"] = src["where"]
        checks.append(c)

    # A proven LOWER BOUND is evidence. "No two holes are within 2.0 mm" already
    # settles a 0.45 mm rule without knowing the exact minimum, and refusing to
    # use it leaves the whole P0 layer undetermined on every normal board.
    eps = _spec.load("jlc-pcb-capabilities.json")["tolerance_epsilon"]
    for c in checks:
        row = m.get(c.get("key") or "") or {}
        lb = row.get("lower_bound")
        lim = c.get("limit")
        if c["state"] != "undetermined" or lb is None:
            continue
        if lim is None:
            rec = c.get("recommended")
            if (isinstance(rec, (int, float)) and c.get("op") == ">="
                    and lb >= rec - eps):
                c["state"] = "pass"
                c["measured_from"] = "lower_bound"
                c["evidence"] = ("lower bound %.3f mm already satisfies the "
                                 "%.3f mm recommended value" % (lb, rec))
            continue
        if not isinstance(lim, (int, float)):
            continue
        if c.get("op") == ">=" and lb >= lim - eps:
            c["state"] = "pass"
            c["measured_from"] = "lower_bound"
            c["evidence"] = ("measured only as a lower bound: %.3f mm, which already "
                             "satisfies the %.3f mm limit" % (lb, lim))
        elif c.get("op") == "<=" and lb > lim + eps:
            c["state"] = "fail"
            c["measured_from"] = "lower_bound"
            c["reason"] = ("already >= %.3f mm by lower bound, above the %.3f mm "
                           "limit" % (lb, lim))
            c["margin"] = lb - lim
    return checks, summary, code


def _catalog_checks(process, m, facts_in):
    auto = _measure.catalog_facts(m)
    refusals = _measure.fact_refusals(m)
    facts = dict(auto)
    facts.update(facts_in or {})
    # A fact this module measured but cannot bound the POPULATION of is shown and
    # not graded. A number supplied by the caller replaces it, because the caller
    # is the one who knows which capacitor serves which pin.
    caps = _measure.grade_caps(m)
    given = set(facts_in or ())
    checks = []
    for rule in _rules.LAYOUT_RULES:
        lvl = _CATALOG_LEVEL.get(rule["id"])
        if lvl is None:
            continue
        if rule["id"] in _GRADED_BY_FAB:
            # Same number, two owners is how a board gets two verdicts. These
            # three are graded at P0 from the capability library, where the limit
            # depends on the process and carries a quote; the catalogue's 6 mil
            # convention is listed in `pcb_rule_catalog` but not re-graded here
            # (openair-max: 0.15 mm passes JLC 1 oz and fails the 0.1524 mm
            # convention - one answer, not two).
            continue
        if lvl == "P0" and not process:
            # A P0 limit IS the process (0.10 mm is legal on 1 oz, illegal on
            # 2 oz), so the catalogue's default number may not be used as a
            # verdict: on KiCad's own demo boards it flagged six finished,
            # manufactured designs as unbuildable.  Undetermined is the honest
            # answer, and `P0.process_declared` already says why.
            checks.append({"id": rule["id"], "level": "P0", "title": rule["title"],
                           "state": "undetermined",
                           "reason": "P0 limits are a function of the fab process; "
                                     "declare process={layers, copper_oz} and this "
                                     "rule is graded against the published capability "
                                     "library",
                           "fix": rule.get("fix"), "source": rule.get("source")})
            continue
        graded = _rules._grade(rule, facts)
        state = {"pass": "pass", "fail": "fail"}.get(graded["state"], "undetermined")
        c = {"id": graded["id"], "level": lvl, "title": graded["title"],
             "state": state, "measured": graded.get("measured"),
             "limit": graded.get("limit"), "unit": graded.get("unit"),
             "source": graded.get("source"), "why": graded.get("why"),
             "how_to_measure": graded.get("how_to_measure"),
             "evidence_level": graded.get("evidence_level")}
        if state == "undetermined":
            # When this module could not measure the fact, say why in its own
            # words ("no crystal on this board") instead of "no measurement
            # supplied for 'trace_under_crystal'", which reads as a gap in the
            # tool and hides the fact that the board settles it.
            c["reason"] = (refusals.get(rule["fact"])
                           or graded.get("reason") or graded.get("next_check"))
        numeric = (rule["kind"] not in ("bool_false", "bool_true")
                   and isinstance(c["measured"], (int, float))
                   and isinstance(c["limit"], (int, float)))
        if state == "fail" and numeric:
            c["margin"] = (c["measured"] - c["limit"]
                           if rule["kind"] in ("max", "count_max")
                           else c["limit"] - c["measured"])
        # A verdict without the reading behind it is not worth much on a board
        # the caller cannot see: carry the measurement's own "where" (and, when
        # it has them, its examples) into the check.
        row = m.get(rule["fact"]) or {}
        if state in ("pass", "fail") and isinstance(row, dict):
            if row.get("where"):
                c["evidence"] = row["where"]
            if state == "fail" and row.get("examples"):
                c["examples"] = row["examples"]
        if state in ("pass", "fail") and rule["fact"] in caps \
                and rule["fact"] not in given:
            c["measured_state"] = state
            c["state"] = "undetermined"
            c["reason"] = caps[rule["fact"]]
            # The margin belongs to a verdict this check does not make.
            c.pop("margin", None)
        checks.append(c)
    return checks


def audit(board, process=None, facts=None, levels=None, window_mm=_measure.WINDOW_MM):
    """Measure the board, grade both libraries, and resolve the priority."""
    want = None if not levels else set(levels)
    m = _measure.measure(board, window_mm=window_mm)

    # Thickness is a FACT in the board file (stackup total), not a choice the
    # user has to repeat; the fab rule reads it from the process, so fill it in
    # rather than reporting the rule as unmeasured.
    filled, snapped = [], {}
    if process and process.get("thickness_mm") is None:
        tv = (m.get("thickness_mm") or {}).get("value")
        if tv is not None:
            # The stackup total in the file is a measured property, but the
            # ORDERED thickness is one of the fab's options and the file's sum
            # lands a few microns off (cm5_minima: 1.5296 mm for a 1.6 mm
            # stack). Snap to the nearest option within 0.1 mm and say so;
            # outside that, leave the rule to the caller rather than mis-grade.
            opts = _thickness_options()
            near = min(opts, key=lambda o: abs(o - tv)) if opts else None
            if near is not None and abs(near - tv) <= 0.1:
                process = dict(process, thickness_mm=near)
                filled.append("thickness_mm")
                snapped["thickness_mm"] = {"from_board_mm": round(tv, 4),
                                           "ordered_mm": near}

    fab_checks, fab_summary, fab_code = _fab_checks(process, m)
    checks = list(fab_checks)
    checks += [_own_check(r, m) for r in _OWN_RULES]
    checks += _catalog_checks(process, m, facts)

    if want:
        checks = [c for c in checks if c["level"] in want]

    res = resolve(checks)

    measured = {k: v for k, v in sorted(m.items())
                if "value" in v or "lower_bound" in v}
    unmeasured = {k: (v.get("unmeasurable") or v.get("why") or "not measured")
                  for k, v in sorted(m.items())
                  if "value" not in v and "lower_bound" not in v}

    value = {
        "process": process or None,
        "process_filled_from_board": filled,
        "process_snapped": snapped,
        "levels": res["levels"],
        "priority": {
            "order": res["order"],
            "names": LEVEL_NAMES,
            "meaning": LEVEL_MEANING,
            "blocking_levels": res["blocking_levels"],
            "blocking_level": res["blocking_level"],
            "flagged": res["flagged"],
            "deferred": res["deferred"],
            "first_action": res["first_action"],
            "do_not_trade": res["do_not_trade"],
        },
        "verdict": _verdict(res, m),
        "measured": measured,
        "unmeasured": unmeasured,
        "fab_summary": fab_summary,
        "how_this_was_measured": (
            "geometry is read from the file and measured by this server; the P0 "
            "limits are the published fab capability library (quoted, tiered); the "
            "other levels are the design catalogue. Nothing here routes, moves, "
            "pours or writes."),
        "what_this_does_not_prove": (
            "this is not the vendor DRC and does not replace it: it checks the rules "
            "named here and nothing else. A 'clear' level means no named failure mode "
            "was measured on this board - not that the board is good, and not that "
            "the fab will accept it."),
    }
    return value


def _verdict(res, m):
    blocking = res["blocking_level"]
    if blocking == "P0":
        return "blocked_at_P0_unbuildable"
    if blocking:
        return "blocked_at_%s" % blocking
    undet = [c for lvl in LEVELS for c in res["levels"][lvl]["checks"]
             if c["state"] == "undetermined"]
    flagged = res.get("flagged") or []
    # Nothing at P0..P2 failed: the board builds.  A P3/P4 finding is a quality
    # note and must not be reported as "blocked", or an agent would chase a
    # corner chamfer on a board that has nothing to fix first.
    if flagged:
        tail = "" if not undet else " (%d rules still undetermined)" % len(undet)
        return "buildable_flag_at_%s%s" % (flagged[0]["level"], tail)
    if undet:
        return "nothing_failed_but_%d_rules_undetermined" % len(undet)
    return "measured_clean"

def catalog():
    """Every rule this audit can grade, with the priority it sits at.

    A caller that wants to know *what will be checked* asks this; a caller that
    wants *what this board scored* runs :func:`audit`. The P0 fab rules are not
    copied here - they live in the capability library and are listed by the
    ``jlc_capabilities`` tool, so there is one place where a fab number and its
    quote are written down.
    """
    rules = []
    for rule in _rules.LAYOUT_RULES:
        lvl = _CATALOG_LEVEL.get(rule["id"])
        row = {"id": rule["id"], "level": lvl, "title": rule["title"],
               "kind": rule["kind"], "limit": rule.get("limit"),
               "unit": rule.get("unit"), "severity": rule.get("severity"),
               "fact": rule.get("fact"), "source": rule.get("source"),
               "why": rule.get("why"),
               "how_to_measure": rule.get("how_to_measure")}
        if rule["id"] in _GRADED_BY_FAB:
            # Listed here so the rule is discoverable, but the number that is
            # graded is the fab library's, which depends on the declared process.
            row["graded_by"] = ("jlc-pcb-capabilities.json (process-dependent "
                                "limit with a quote); the catalogue limit shown "
                                "here is a convention and is not graded")
        rules.append(row)
    for rule in _OWN_RULES:
        rules.append({"id": rule["id"], "level": rule["level"],
                      "title": rule["title"], "kind": rule["kind"],
                      "limit": rule["limit"], "unit": rule["unit"],
                      "severity": "measured here", "fact": rule["fact"],
                      "source": rule["source"], "fix": rule.get("fix")})
    rules.sort(key=lambda r: (LEVELS.index(r["level"]) if r["level"] in LEVELS else 9,
                              r["id"]))
    return {
        "levels": [{"level": l, "name": LEVEL_NAMES[l], "meaning": LEVEL_MEANING[l]}
                   for l in LEVELS],
        "blocking_levels": list(BLOCKING_LEVELS),
        "rules": rules,
        "p0_rule_source": "jlc/data/*.json via the jlc_capabilities tool: 29 rules, "
                          "each with a source id, a tier and the verbatim quote",
        "order_rule": ("a lower priority is never traded for a higher one; while a "
                       "level in BLOCKING_LEVELS fails, everything below it is "
                       "reported as deferred, never as done"),
    }
