"""Design-for-manufacture checks, driven by a capability document YOU supply.

Why there is no built-in table: "minimum track width 0.127 mm" is a promise made
by ONE fab at ONE price tier. Baking a vendor's numbers in would make the answer
right for exactly that vendor and silently wrong for the next one, with nothing
in the result saying which case the reader is in. So this module ships the RULES
- what each number means, which way it points, and what has to be measured to
compare against it - and refuses to invent the LIMITS.

Two failure modes are refused rather than passed over:

* an unknown rule key is a typo, and a typo'd rule would be reported as "not
  stated" instead of "not understood" - the capability document is rejected
  whole, listing the keys it did not recognise;
* a capability document with no `source` is rejected. An unattributed limit is
  indistinguishable from one somebody made up, which is the exact thing this
  project exists to avoid elsewhere (see calc/safety_table.py).

Rule keys carry their unit in the name (`_mm`, `_layers`), so nothing here has
to guess whether "0.15" meant millimetres or mils.
"""

import math

from .result import err, ok

# ------------------------------------------------------------------ the rules
# key -> {what, unit, direction, fact}. `fact` names the measurement the Oracle
# has to hand over; a capability rule whose fact is missing comes back as
# `not_checked` (with the reason), never as a pass.
RULES = {
    "min_track_width_mm": {
        "what": "narrowest copper track",
        "unit": "mm", "direction": "min", "fact": "narrowest_track",
    },
    "min_clearance_mm": {
        "what": "smallest copper-to-copper gap between different nets",
        "unit": "mm", "direction": "min", "fact": "min_copper_gap",
    },
    "min_via_drill_mm": {
        "what": "smallest via finished hole",
        "unit": "mm", "direction": "min", "fact": "min_via_drill",
    },
    "min_via_annular_ring_mm": {
        "what": "smallest via annular ring, (diameter - drill) / 2",
        "unit": "mm", "direction": "min", "fact": "min_via_ring",
    },
    "min_pth_annular_ring_mm": {
        "what": "smallest plated through-hole pad annular ring, "
                "(min(pad w, pad h) - drill) / 2",
        "unit": "mm", "direction": "min", "fact": "min_pad_ring",
    },
    "min_hole_to_hole_mm": {
        "what": "smallest web between two drilled holes (wall to wall)",
        "unit": "mm", "direction": "min", "fact": "min_hole_web",
    },
    "min_copper_to_edge_mm": {
        "what": "smallest copper-to-board-edge distance",
        "unit": "mm", "direction": "min", "fact": "min_copper_to_edge",
    },
    "min_slot_width_mm": {
        "what": "narrowest internal cut-out (slot / routed hole)",
        "unit": "mm", "direction": "min", "fact": "min_slot_width",
    },
    "max_board_width_mm": {
        "what": "board outline width",
        "unit": "mm", "direction": "max", "fact": "board_width",
    },
    "max_board_height_mm": {
        "what": "board outline height",
        "unit": "mm", "direction": "max", "fact": "board_height",
    },
    "max_copper_layers": {
        "what": "number of copper layers",
        "unit": "count", "direction": "max", "fact": "copper_layers",
    },
    # 0.0.56: three quantities that used to be listed as "not checked by design"
    # are measured now, so a capability document can carry a limit for them.
    "min_silk_line_width_mm": {
        "what": "narrowest silkscreen stroke (drawn lines and text strokes alike)",
        "unit": "mm", "direction": "min", "fact": "min_silk_line_width",
    },
    "min_silk_text_height_mm": {
        "what": "smallest silkscreen character height",
        "unit": "mm", "direction": "min", "fact": "min_silk_text_height",
    },
    "min_mask_web_mm": {
        "what": "narrowest strip of solder mask between two openings on one side",
        "unit": "mm", "direction": "min", "fact": "min_mask_web",
    },
    "min_copper_to_hole_mm": {
        "what": "smallest gap from another net's copper to a FINISHED hole wall "
                "(drill radius + the plating the document states)",
        "unit": "mm", "direction": "min", "fact": "min_copper_to_hole",
    },
}

# Things a fab capability document often states that this layer CANNOT check.
# Listed so "not in the result" is not read as "checked and fine".
NOT_CHECKED_BY_DESIGN = [
    # 0.0.56 narrowed this: the web BETWEEN two openings is measured now
    # (`min_mask_web_mm`). What is still not modelled is a mask feature that does
    # not come from a gap - a mask-defined pad (opening smaller than the copper)
    # and a sliver between an opening and the mask's own edge are not judged.
    "mask-defined pads (an opening smaller than its copper) and any mask sliver "
    "that is not the gap between two openings",
    # 0.0.56 narrowed this too: stroke widths and character heights are measured
    # (`min_silk_line_width_mm`, `min_silk_text_height_mm`). Still not modelled:
    # items whose width the file does not state, and silk clipping over pads.
    "silkscreen items that state no line width (no default is substituted), and "
    "silk-over-pad clipping",
    "impedance-controlled stackup sign-off (see diff_impedance_field; the fab's own "
    "coupon is the verdict)",
    "hole positional tolerance, plating thickness tolerance, board thickness "
    "tolerance, copper weight tolerance - process tolerances, not geometry",
    "panelisation, tooling/break-away rails, V-cut, fiducials and their clearance",
    "non-plated slot vs plated slot distinction beyond what the file states",
    # 0.0.56 narrowed this: copper to the finished hole wall IS measured when the
    # document supplies `plating_um`. The annular ring is still measured to the
    # DRILL wall - a plated hole's finished ring is smaller, and that is not
    # folded in, because silently shrinking an existing answer is not a fix.
    "barrel taper and hole-size tolerance behind the plating figure, and the "
    "annular ring AFTER plating (rings here are measured to the drill wall)",
    "arc/teardrop smoothing of routed corners (arcs here are polylines)",
]

PRECISION = (
    "limits come from the supplied capability document verbatim - this layer does "
    "not round, tighten or invent them; measurements are the same ones the Oracle "
    "reports elsewhere (arcs are polylines). Solder-mask expansion is applied only "
    "where the file states it and defaults to 0 where it does not; plating build-up "
    "is applied only when the document supplies `plating_um`. So a measurement "
    "within a few microns of a limit should be re-read as 'at the limit', not as "
    "a pass"
)

# A process parameter, not a geometry limit: how far the copper in the barrel grows
# past the drilled wall. It belongs to the same document as the limits (it is a
# property of the fab) and it is NEVER defaulted - a hole measured to the drill
# wall and one measured to the finished wall differ by exactly this, and guessing
# it would decide the verdict.
PLATING_KEY = "plating_um"

PLATING_NOTE = (
    "state the fab's hole plating thickness in micrometres, e.g. \"plating_um\": 25; "
    "without it, copper-to-finished-hole is reported not_checked rather than "
    "measured against the drill wall"
)


# ------------------------------------------------------------------ plumbing
def _is_number(v):
    return isinstance(v, (int, float)) and not isinstance(v, bool) and math.isfinite(v)


def _rows(rules):
    return [{"rule": k, "what": v["what"], "unit": v["unit"],
             "direction": v["direction"]} for k, v in sorted(RULES.items())
            if k not in rules]


def read_plating(doc):
    """`plating_um` -> (millimetres or None, error or None). Never defaulted."""
    if not isinstance(doc, dict):
        return None, None
    v = doc.get(PLATING_KEY)
    if v is None:
        return None, None
    if not _is_number(v):
        return None, err("`%s` is not a finite number" % PLATING_KEY,
                         "CAPABILITY_VALUE_INVALID",
                         hint=PLATING_NOTE,
                         extra={"invalid_rules": [{"rule": PLATING_KEY,
                                                   "value": repr(v),
                                                   "why": "not a finite number"}]})
    if v < 0:
        return None, err("`%s` is negative" % PLATING_KEY,
                         "CAPABILITY_VALUE_INVALID",
                         hint=PLATING_NOTE,
                         extra={"invalid_rules": [{"rule": PLATING_KEY, "value": v,
                                                   "why": "must be >= 0"}]})
    return float(v) / 1000.0, None


def read_capability(doc):
    """Validate a capability document. Returns (rules, error_envelope_or_None)."""
    if not isinstance(doc, dict):
        return None, err("the capability document must be a JSON object",
                         "ARG_INVALID",
                         hint='shape: {"fab": "...", "source": "...", '
                              '"verified": false, "rules": {"min_track_width_mm": 0.127}}')
    rules = doc.get("rules")
    if not isinstance(rules, dict) or not rules:
        return None, err("the capability document carries no rules",
                         "CAPABILITY_EMPTY",
                         hint="known rule keys: %s" % ", ".join(sorted(RULES)),
                         extra={"known_rules": _rows({})})
    source = doc.get("source")
    if not isinstance(source, str) or not source.strip():
        # An unattributed limit cannot be told apart from a made-up one.
        return None, err("the capability document has no `source`; a limit without "
                         "provenance is indistinguishable from an invented one",
                         "SOURCE_REQUIRED",
                         hint='state where the numbers came from, e.g. '
                              '"fab capability page, checked 2026-10-28" or '
                              '"quotation #1234 from <fab>"')
    _plating_mm, plating_err = read_plating(doc)
    if plating_err:
        return None, plating_err
    unknown = sorted(k for k in rules if k not in RULES)
    if unknown:
        return None, err("unknown rule key(s): %s - the whole document is rejected, "
                         "because a typo would otherwise be reported as 'not stated'"
                         % ", ".join(unknown),
                         "CAPABILITY_KEY_UNKNOWN",
                         hint="known rule keys: %s" % ", ".join(sorted(RULES)),
                         extra={"unknown_rules": unknown,
                                "known_rules": _rows({})})
    bad = []
    for k, v in sorted(rules.items()):
        if not _is_number(v):
            bad.append({"rule": k, "value": v if isinstance(v, (str, int, float))
                        else repr(v), "why": "not a finite number"})
        elif v <= 0:
            bad.append({"rule": k, "value": v, "why": "must be > 0"})
    if bad:
        return None, err("%d rule value(s) are unusable: %s"
                         % (len(bad), ", ".join(b["rule"] for b in bad)),
                         "CAPABILITY_VALUE_INVALID",
                         hint="limits are numbers greater than zero; units live in the "
                              "key name (_mm / _layers)",
                         extra={"invalid_rules": bad})
    return {k: float(v) for k, v in rules.items()}, None


def validate(doc):
    """Check a capability document on its own, before any board is opened."""
    rules, e = read_capability(doc)
    if e:
        return e
    value = {
        "fab": doc.get("fab"),
        "source": doc.get("source"),
        "verified": bool(doc.get("verified")),
        "rules_stated": len(rules),
        "rules_known": len(RULES),
        "coverage_pct": round(100.0 * len(rules) / len(RULES), 1),
        "stated": [{"rule": k, "limit": v, "unit": RULES[k]["unit"],
                    "what": RULES[k]["what"]} for k, v in sorted(rules.items())],
        "not_stated": _rows(rules),
        "plating_um": (None if doc.get(PLATING_KEY) is None
                       else float(doc.get(PLATING_KEY))),
        "not_checked_by_design": NOT_CHECKED_BY_DESIGN,
    }
    if not doc.get("verified"):
        value["warnings"] = [
            "`verified` is false: this layer has no way to know whether the numbers "
            "match the fab's current capability. Re-read the source before ordering.",
        ]
    return ok(
        value=value,
        unit="mixed",
        model="dfm_capability_document",
        basis="rule catalogue defined in calc/dfm.py (key = quantity + unit, "
              "direction = which way the limit points); the LIMITS are taken from the "
              "supplied document and are never defaulted",
        precision=PRECISION,
        assumptions={"source": doc.get("source"), "fab": doc.get("fab")},
    )


# ------------------------------------------------------------------ the check
def _compare(direction, measured, limit, tol):
    if direction == "min":
        return measured + tol >= limit
    return measured - tol <= limit


def evaluate(doc, facts, tol=0.0):
    """Compare measured facts against a capability document.

    `facts` maps a fact name (see `RULES[*]["fact"]`) to one of:
      {"measured": x, "where": "..."}            a number, compared directly
      {"lower_bound": x, "where": "..."}         min-rule only: true value >= x
      {"upper_bound": x, "where": "..."}         max-rule only: true value <= x
      {"not_applicable": "why"}                  the board has no such feature
      {"why": "..."} / missing                   could not be measured
    """
    rules, e = read_capability(doc)
    if e:
        return e
    facts = facts or {}
    checks = []
    failed = passed = 0
    checked_keys, unchecked_keys, na_keys = [], [], []
    for key in sorted(rules):
        spec = RULES[key]
        limit = rules[key]
        fact = facts.get(spec["fact"]) or {}
        entry = {"rule": key, "what": spec["what"], "unit": spec["unit"],
                 "direction": spec["direction"], "limit": limit,
                 "fact": spec["fact"]}
        if fact.get("not_applicable"):
            entry["verdict"] = "not_applicable"
            entry["note"] = fact["not_applicable"]
            na_keys.append(key)
        elif fact.get("measured") is not None:
            m = float(fact["measured"])
            entry["measured"] = round(m, 6)
            entry["where"] = fact.get("where")
            good = _compare(spec["direction"], m, limit, tol)
            entry["verdict"] = "pass" if good else "fail"
            entry["margin"] = round((m - limit) if spec["direction"] == "min"
                                    else (limit - m), 6)
            checked_keys.append(key)
            if good:
                passed += 1
            else:
                failed += 1
        elif fact.get("lower_bound") is not None and spec["direction"] == "min":
            b = float(fact["lower_bound"])
            entry["lower_bound"] = round(b, 6)
            entry["where"] = fact.get("where")
            good = b + tol >= limit
            entry["verdict"] = "pass" if good else "fail"
            entry["note"] = ("no feature was found inside the search window; the true "
                             "value is at least this bound")
            checked_keys.append(key)
            if good:
                passed += 1
            else:
                failed += 1
        elif fact.get("upper_bound") is not None and spec["direction"] == "max":
            b = float(fact["upper_bound"])
            entry["upper_bound"] = round(b, 6)
            good = b - tol <= limit
            entry["verdict"] = "pass" if good else "fail"
            checked_keys.append(key)
            if good:
                passed += 1
            else:
                failed += 1
        else:
            entry["verdict"] = "not_checked"
            entry["note"] = fact.get("why") or "the measurement was not taken"
            unchecked_keys.append(key)
        checks.append(entry)

    overall = "fail" if failed else ("pass" if passed else "not_checked")
    value = {
        "fab": doc.get("fab"),
        "source": doc.get("source"),
        "verified": bool(doc.get("verified")),
        "overall": overall,
        "checks": checks,
        "passed": passed,
        "failed": failed,
        "failed_rules": [c["rule"] for c in checks if c["verdict"] == "fail"],
        "not_checked": unchecked_keys,
        "not_applicable": na_keys,
        "checked": checked_keys,
        "rules_stated": len(rules),
        "rules_known": len(RULES),
        "missing_known_rules": _rows(rules),
        "plating_um": (None if doc.get(PLATING_KEY) is None
                       else float(doc.get(PLATING_KEY))),
        "not_checked_by_design": NOT_CHECKED_BY_DESIGN,
    }
    warn = []
    if not doc.get("verified"):
        warn.append("`verified` is false: the limits are the supplied document's, not "
                    "evidence that this fab can build to them today")
    if value["missing_known_rules"]:
        warn.append("%d of %d known rules were not stated by the capability document "
                    "(see missing_known_rules) - those questions are unanswered, not "
                    "answered 'fine'"
                    % (len(value["missing_known_rules"]), len(RULES)))
    if overall != "fail" and unchecked_keys:
        warn.append("overall is %r, but %d stated rule(s) could not be measured: %s"
                    % (overall, len(unchecked_keys), ", ".join(unchecked_keys)))
    if warn:
        value["warnings"] = warn
    return ok(
        value=value,
        unit="mixed",
        model="dfm_rule_catalogue_vs_measured_facts",
        basis="each stated limit compared against a geometry measurement taken from "
              "the board file; a rule with no measurement is reported not_checked, "
              "never assumed to pass",
        precision=PRECISION,
        assumptions={"tolerance": tol,
                     "source": doc.get("source"),
                     "fab": doc.get("fab"),
                     "rules_stated": sorted(rules)},
    )
