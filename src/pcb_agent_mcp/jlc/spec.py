"""Three-tier verdict engine: measured values against the published fab capability library.

Ported from jlceda-pcb-ai-workflow (MIT) `scripts/spec_check.py`. Two properties are
kept because they are the whole point:

* **this module contains no fabrication threshold.** Every number lives in
  ``data/jlc-pcb-capabilities.json`` / ``jlc-smt-capabilities.json`` with a source
  id, a tier, a URL, a retrieval date and the verbatim quote. A rule whose numbers
  cannot be traced is rejected by :func:`validate`.
* **missing evidence is not a pass.** A rule that applies but has no measured value
  comes back ``NEEDS_EVIDENCE`` and, outside partial mode, makes the run BLOCKED.

Verdicts: ``BLOCK`` below the published limit, ``WARN`` between limit and
recommended, ``PASS`` at or above recommended, ``COST_TRIGGER`` as a flag inside
a PASS/WARN row, ``NEEDS_EVIDENCE`` when the value was never measured, ``N/A``
when the object does not exist on this board.
"""
import json
import os

DATA = os.path.join(os.path.dirname(os.path.abspath(__file__)), "data")

OPS = {">=", "<=", "==", "in", "not_in"}
TIERS = {"T0", "T1", "T2"}      # T3 (third-party) must never enter the library
NONE_PRESENT = "NONE_PRESENT"   # measurement value: the board has no such object

_CACHE = {}


def load(name):
    """Load a data file by base name, e.g. ``load("jlc-pcb-capabilities.json")``."""
    if name in _CACHE:
        return _CACHE[name]
    path = name if os.path.isabs(name) else os.path.join(DATA, name)
    with open(path, encoding="utf-8") as handle:
        doc = json.load(handle)
    _CACHE[name] = doc
    return doc


def load_json(path):
    with open(path, encoding="utf-8") as handle:
        return json.load(handle)


# --------------------------------------------------------------- library audit
def validate(data, label="data"):
    """Every limit must be attributable. Returns a list of problems (empty = ok)."""
    problems = []
    if not isinstance(data.get("tolerance_epsilon"), (int, float)):
        problems.append("%s: tolerance_epsilon missing (the comparison tolerance must be "
                        "declared in the library; this module has no default)" % label)
    srcs = data.get("sources", {})
    for sid, src in srcs.items():
        for field in ("tier", "url", "fetched_at"):
            if not src.get(field):
                problems.append("%s: source %s lacks %s" % (label, sid, field))
        if src.get("tier") not in TIERS:
            problems.append("%s: source %s tier=%s not allowed (T0/T1/T2 only)"
                            % (label, sid, src.get("tier")))
    seen = set()
    for rule in data.get("rules", []):
        rid = rule.get("id", "<no id>")
        if rid in seen:
            problems.append("%s: duplicate rule id %s" % (label, rid))
        seen.add(rid)
        for field in ("id", "name", "key", "op"):
            if not rule.get(field):
                problems.append("%s: %s lacks %s" % (label, rid, field))
        if rule.get("op") not in OPS:
            problems.append("%s: %s has illegal op=%s" % (label, rid, rule.get("op")))
        if not any(k in rule for k in ("limit", "recommended")):
            problems.append("%s: %s has neither limit nor recommended" % (label, rid))
        for kind in ("limit", "recommended", "cost_trigger"):
            tier = rule.get(kind)
            if tier is None:
                continue
            if tier.get("src") not in srcs:
                problems.append("%s: %s.%s src=%s is not registered in sources"
                                % (label, rid, kind, tier.get("src")))
            if not str(tier.get("quote", "")).strip():
                problems.append("%s: %s.%s lacks the verbatim quote" % (label, rid, kind))
            if kind != "cost_trigger" and not any(k in tier for k in ("value", "by", "set")):
                problems.append("%s: %s.%s lacks value/by/set" % (label, rid, kind))
    return problems


# ------------------------------------------------------------- process parsing
def layer_class(layers):
    return "single" if layers == 1 else ("double" if layers == 2 else "multi")


def _fmt_oz(oz):
    return format(float(oz), "g")


def resolve(spec, process):
    """Pick the value that applies to this process; ``None`` when nothing matches."""
    if spec is None:
        return None
    if "value" in spec:
        return spec["value"]
    if "set" in spec:
        return spec["set"]
    by = spec.get("by", {})
    lc = layer_class(process["layers"])
    oz = _fmt_oz(process["copper_oz"])
    for key in ("%s_%soz" % (lc, oz), "default_%soz" % oz, lc, "default"):
        if key in by:
            return by[key]
    return None


def applicable(rule, process):
    applies = rule.get("applies")
    if not applies:
        return True
    lc = layer_class(process["layers"])
    if "layer_class" in applies and lc not in applies["layer_class"]:
        return False
    if "copper_oz" in applies and float(process["copper_oz"]) not in [float(x) for x in applies["copper_oz"]]:
        return False
    if "layers_ge" in applies and process["layers"] < applies["layers_ge"]:
        return False
    if "layers_lt" in applies and process["layers"] >= applies["layers_lt"]:
        return False
    if "thickness_le" in applies:
        thickness = process.get("thickness_mm")
        if thickness is None or thickness > applies["thickness_le"]:
            return False
    for key, values in applies.get("when", {}).items():
        # an undeclared edge defaults to routed (mill) - that is the process default
        declared = process.get(key, "routed" if key == "edge" else None)
        if declared not in values:
            return False
    return True


# ------------------------------------------------------------------- verdicts
def _num_in(value, seq, eps):
    for item in seq:
        if isinstance(item, (int, float)) and isinstance(value, (int, float)):
            if abs(value - item) <= eps:
                return True
        elif value == item:
            return True
    return False


def _src_label(sources, spec):
    if not spec:
        return None
    src = sources.get(spec["src"], {})
    return "%s %s <%s> @%s" % (src.get("tier", "?"), src.get("title", spec["src"]),
                               src.get("url", ""), src.get("fetched_at", ""))


def evaluate_rule(rule, process, measurements, eps, sources):
    key = rule["key"]
    source = process if rule.get("from") == "process" else measurements
    lim_spec, rec_spec = rule.get("limit"), rule.get("recommended")
    lim, rec = resolve(lim_spec, process), resolve(rec_spec, process)
    res = {"rule_id": rule["id"], "name": rule["name"], "key": key, "op": rule["op"],
           "limit": lim, "recommended": rec, "measured": None, "verdict": None, "flags": [],
           "source": {"limit": _src_label(sources, lim_spec),
                      "recommended": _src_label(sources, rec_spec)},
           "note": rule.get("note")}
    if lim is None and rec is None:
        res["verdict"] = "N/A"
        res["na_reason"] = "no_rule_values"
        return res
    if source.get(key) == NONE_PRESENT:
        res["verdict"] = "N/A"
        res["na_reason"] = "no_objects"
        return res
    if key not in source or source[key] is None:
        res["verdict"] = "NEEDS_EVIDENCE"
        return res
    measured = source[key]
    res["measured"] = measured
    op = rule["op"]
    verdict = "PASS"
    if op == ">=":
        if lim is not None and measured < lim - eps:
            verdict = "BLOCK"
        elif rec is not None and measured < rec - eps:
            verdict = "WARN"
    elif op == "<=":
        if lim is not None and measured > lim + eps:
            verdict = "BLOCK"
        elif rec is not None and measured > rec + eps:
            verdict = "WARN"
    elif op == "==":
        if lim is not None and abs(measured - lim) > eps:
            verdict = "BLOCK"
    elif op == "in":
        if lim is not None and not _num_in(measured, lim, eps):
            verdict = "BLOCK"
    elif op == "not_in":
        if lim is not None and _num_in(measured, lim, eps):
            verdict = "BLOCK"
    res["verdict"] = verdict
    trigger = rule.get("cost_trigger")
    if trigger and isinstance(measured, (int, float)) and measured < trigger["below"] - eps:
        res["flags"].append("COST_TRIGGER(<%smm: %s)" % (trigger["below"], trigger["quote"]))
    return res


def evaluate(process, measurements, rule_sets, partial=False):
    """``rule_sets``: [(data_dict, label), ...] -> (results, summary, code)."""
    for need in ("layers", "copper_oz"):
        if process.get(need) is None:
            raise ValueError("process lacks required field %s (the process is never guessed - "
                             "ask the user to declare it)" % need)
    results = []
    for data, label in rule_sets:
        eps = data["tolerance_epsilon"]
        for rule in data["rules"]:
            if not applicable(rule, process):
                continue
            row = evaluate_rule(rule, process, measurements, eps, data["sources"])
            row["set"] = label
            results.append(row)
    results = [r for r in results
               if not (r["verdict"] == "N/A" and r.get("na_reason") == "no_rule_values")]
    counts = {}
    for row in results:
        counts[row["verdict"]] = counts.get(row["verdict"], 0) + 1
    evaluated = sum(v for k, v in counts.items() if k not in ("NEEDS_EVIDENCE", "N/A"))
    summary = {"total_applicable": len(results), "evaluated": evaluated, "counts": counts,
               "cost_triggers": sum(1 for r in results if r["flags"]), "partial": partial}
    if counts.get("BLOCK"):
        code = 1
    elif counts.get("NEEDS_EVIDENCE") and not partial:
        code = 2
    else:
        code = 0
    return results, summary, code


def rule_sets(process):
    sets = [(load("jlc-pcb-capabilities.json"), "pcb")]
    if process.get("smt") in ("economy", "standard"):
        sets.append((load("jlc-smt-capabilities.json"), "smt"))
    return sets


def check(process, measurements, partial=False, strict=None):
    """Convenience wrapper: pcb (+smt when declared) library against measurements."""
    if strict is not None:
        partial = not strict
    results, summary, code = evaluate(process, measurements, rule_sets(process), partial=partial)
    return {"results": results, "summary": summary, "exit": code}


def _mil(value):
    return "%.2fmil" % (value / 0.0254) if isinstance(value, (int, float)) else ""


def render(results, summary, process, code):
    lines = ["basis: JLCPCB capability library | %s layer(s) (%s) | copper %soz | "
             "thickness %s mm | surface %s"
             % (process["layers"], layer_class(process["layers"]), process["copper_oz"],
                process.get("thickness_mm", "undeclared"), process.get("surface", "undeclared"))]
    order = {"BLOCK": 0, "NEEDS_EVIDENCE": 1, "WARN": 2, "PASS": 3, "N/A": 4}
    for row in sorted(results, key=lambda r: (order.get(r["verdict"], 9), r["rule_id"])):
        tag = {"BLOCK": "[BLOCK]", "WARN": "[WARN]", "PASS": "[PASS]",
               "NEEDS_EVIDENCE": "[NEEDS_EVIDENCE]", "N/A": "[N/A]"}[row["verdict"]]
        if row["verdict"] == "N/A":
            line = "%s %s %s: no such object on this board" % (tag, row["rule_id"], row["name"])
        elif row["verdict"] == "NEEDS_EVIDENCE":
            line = "%s %s %s: no measured value for `%s`" % (tag, row["rule_id"], row["name"], row["key"])
        else:
            def fmt(x):
                if isinstance(x, (int, float)) and row["key"] not in (
                        "copper_oz", "thickness_mm", "mark_count", "via_in_smd_pad_count"):
                    return "%s(%s)" % (x, _mil(x))
                return str(x)
            line = "%s %s %s: measured %s | limit %s | recommended %s" % (
                tag, row["rule_id"], row["name"], fmt(row["measured"]),
                fmt(row["limit"]) if row["limit"] is not None else "-",
                fmt(row["recommended"]) if row["recommended"] is not None else "-")
        lines.append(line)
        for flag in row["flags"]:
            lines.append("          %s" % flag)
        if row["verdict"] in ("BLOCK", "WARN"):
            which = "limit" if row["verdict"] == "BLOCK" else "recommended"
            basis = row["source"].get(which) or row["source"].get("limit")
            if basis:
                lines.append("          basis: %s" % basis)
    lines.append("-- summary: applicable %d, evaluated %d | %s | cost triggers %d%s"
                 % (summary["total_applicable"], summary["evaluated"], summary["counts"],
                    summary["cost_triggers"],
                    " | PARTIAL MODE: uncovered rules are not a pass" if summary["partial"] else ""))
    lines.append({0: "verdict: nothing blocked inside the declared scope (not a promise the "
                     "circuit works or the board is manufacturable)",
                  1: "verdict: blocked - do not pass this gate",
                  2: "verdict: BLOCKED - applicable rules have no measured evidence"}[code])
    return "\n".join(lines)
