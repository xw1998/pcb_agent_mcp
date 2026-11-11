"""One entry point per absorbed capability, returning plain dictionaries for the MCP layer.

Everything below is a thin composition of :mod:`spec`, :mod:`gerber`, :mod:`bomcpl`,
:mod:`drcrules`, :mod:`gates`, :mod:`seal`, :mod:`geometry`, :mod:`schspec`, :mod:`pitfalls`.
No new thresholds are introduced here.
"""
import json
import os

from . import bomcpl, drcrules, gates, gerber, geometry, pitfalls, schspec, seal, spec


# ---------------------------------------------------------------- declarations
def process_template():
    return spec.load("process.template.json")


def process_check(process):
    """A process declaration is accepted only when the two fields without a defensible default
    are actually declared."""
    missing = [k for k in ("layers", "copper_oz") if not process or process.get(k) is None]
    problems = []
    if process:
        if process.get("layers") is not None and (not isinstance(process["layers"], int)
                                                 or process["layers"] < 1):
            problems.append("layers must be a positive integer")
        if process.get("copper_oz") is not None and not isinstance(process["copper_oz"], (int, float)):
            problems.append("copper_oz must be a number")
        if process.get("smt") not in (None, "economy", "standard"):
            problems.append("smt must be null / economy / standard")
    if missing:
        problems.append("undeclared: %s - the process is never guessed, ask the user" % missing)
    return {"ok": not problems, "missing": missing, "problems": problems,
            "echo": process}


# ------------------------------------------------------------------ capability
def capabilities(kind="pcb", rule_id=None):
    data = spec.load("jlc-pcb-capabilities.json" if kind == "pcb" else "jlc-smt-capabilities.json")
    rows = data["rules"]
    if rule_id:
        rows = [r for r in rows if r.get("id") == rule_id]
    return {"kind": kind, "site": data.get("site"), "unit": data.get("unit"),
            "tolerance_epsilon": data["tolerance_epsilon"],
            "sources": data["sources"], "count": len(rows), "rules": rows}


def _validate_bomcpl(data):
    """The BOM/CPL library has its own (simpler) rule shape: id + severity + src + quote.
    It is validated here with that shape instead of the numeric spec validator, which would
    report every rule as malformed. No threshold lives in this function."""
    problems = []
    srcs = data.get("sources", {})
    for sid, s in srcs.items():
        for f in ("tier", "url", "fetched_at"):
            if not s.get(f):
                problems.append("bomcpl: source %s lacks %s" % (sid, f))
        if s.get("tier") not in ("T0", "T1", "T2"):
            problems.append("bomcpl: source %s tier=%s not allowed (T0/T1/T2 only)"
                            % (sid, s.get("tier")))
    if not isinstance(data.get("params"), dict) or not data["params"]:
        problems.append("bomcpl: params missing (thresholds must be declared in the library)")
    seen = set()
    for r in data.get("rules", []):
        rid = r.get("id") or "<no-id>"
        if rid in seen:
            problems.append("bomcpl: duplicate rule id %s" % rid)
        seen.add(rid)
        if not r.get("id"):
            problems.append("bomcpl: rule without id")
        if r.get("severity") not in ("BLOCK", "WARN"):
            problems.append("bomcpl: %s illegal severity=%s" % (rid, r.get("severity")))
        if r.get("src") not in srcs:
            problems.append("bomcpl: %s src=%s not registered in sources" % (rid, r.get("src")))
        if not str(r.get("quote", "")).strip():
            problems.append("bomcpl: %s lacks quote" % rid)
    return problems


def validate_library():
    problems = spec.validate(spec.load("jlc-pcb-capabilities.json"), "pcb")
    problems += spec.validate(spec.load("jlc-smt-capabilities.json"), "smt")
    problems += _validate_bomcpl(spec.load("jlc-bom-cpl-spec.json"))
    return {"ok": not problems, "problems": problems}


# ---------------------------------------------------------------------- checks
def spec_check(process, measurements, partial=False):
    return spec.check(process, measurements, partial=partial)


def gerber_guard(package, process=None, strict=False, structure_only=False,
                 extra_measurements=None):
    return gerber.guard(package, process, strict=strict, structure_only=structure_only,
                        extra_measurements=extra_measurements)


def bom_cpl_check(bom=None, cpl=None, board_size=None, gerber_path=None):
    if not bom and not cpl:
        raise ValueError("give at least one of bom / cpl")
    board = None
    if board_size:
        board = (float(board_size[0]), float(board_size[1]))
    elif gerber_path:
        board = bomcpl.board_from_gerber(gerber_path)
    return bomcpl.run_checks(bom, cpl, board)


def bom_cpl_normalize(bom=None, cpl=None, out_dir=None):
    if not out_dir or not os.path.isdir(out_dir):
        raise ValueError("out_dir must be an existing directory")
    written = {}
    for kind, src in (("bom", bom), ("cpl", cpl)):
        if not src:
            continue
        stem = os.path.splitext(os.path.basename(src))[0]
        dst = os.path.join(out_dir, stem + ".normalized.csv")
        if os.path.exists(dst):
            raise ValueError("%s already exists - refusing to overwrite" % dst)
        written[dst] = bomcpl.normalize(src, dst, kind)
    return {"written": written}


def rules_check(process, config_path=None, preset=None):
    if not config_path and not preset:
        raise ValueError("give config_path or preset")
    config, label = drcrules.load_config(config_path, preset)
    return drcrules.check(config, process, label)


def rules_presets():
    presets = spec.load(drcrules.PRESET_FILE)
    return {"names": list(presets), "dump_js": drcrules.DUMP_JS}


# ---------------------------------------------------------------- gates / seal
def gate(action, project, name=None, gate=None, choice=None, user_reply=None,
         bind=None, note=None, apply=False):
    if action == "init":
        return gates.init(project, name, apply)
    if action == "status":
        return gates.status(project)
    if action == "confirm":
        if isinstance(bind, str):
            bind = [bind]   # a single path is accepted, but it is always recorded as a list
        return gates.confirm(project, gate, choice, user_reply, bind, note)
    raise ValueError("action must be init / status / confirm")


def release(project, version, gerber_path, bom=None, cpl=None, extra=None, apply=False):
    return seal.seal(project, version, gerber_path, bom, cpl, extra, apply)


# ------------------------------------------------------- geometry / schematic
def geometry_audit(evidence):
    if isinstance(evidence, str):
        evidence = spec.load_json(evidence)
    return geometry.audit(evidence)


def schematic_spec(spec_doc, evidence):
    if isinstance(spec_doc, str):
        spec_doc = spec.load_json(spec_doc)
    if isinstance(evidence, str):
        evidence = spec.load_json(evidence)
    return schspec.audit(spec_doc, evidence)


def schematic_template():
    return spec.load("schematic-project.template.json")


def extra_measurements_template():
    return spec.load("extra-measurements.template.json")


# ------------------------------------------------------------------ knowledge
def known_pitfalls(area=None, query=None):
    return {"areas": pitfalls.areas(), "count": len(pitfalls.entries(area, query)),
            "pitfalls": pitfalls.entries(area, query)}


def dump(obj):
    return json.dumps(obj, ensure_ascii=False, indent=2)
