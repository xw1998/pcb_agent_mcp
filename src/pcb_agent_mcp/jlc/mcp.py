"""MCP registration for the absorbed JLC fabrication gates.

Every handler here is a thin wrapper: the decision logic lives in ``jlc.api``, the
thresholds live in ``jlc/data/*.json`` (each with src / tier / url / fetched_at /
quote), and this module only shapes the answer into the server's envelope.

What is deliberately NOT here:
  * no order placement, no upload, no payment call - ``release`` only copies files
    into a local ``release/<version>/`` directory and writes a manifest;
  * no default process: ``layers`` and ``copper_oz`` must be declared by the user,
    the library has no defensible default and the code refuses rather than guesses;
  * no gate answered on the user's behalf: ``jlc_gate`` with action=confirm demands
    the user's own words in ``user_reply`` and a file hash binding.
"""
from ..calc.result import ok, err

MODEL = "JLCPCB published capability library (jlc/data/*.json, T0/T1/T2 sources only)"
BASIS = ("every limit carries src + quote + url + fetched_at; BLOCK = below the "
         "published limit, WARN = between limit and recommended, PASS = at/above "
         "recommended, NEEDS_EVIDENCE = applicable but unmeasured (never a pass), "
         "N/A = no such object on this board")
PRECISION = "limits as published (mm unless the rule says otherwise); comparison tolerance declared per library"


def _env(value, unit="verdict", assumptions=None):
    return ok(value=value, unit=unit, model=MODEL, basis=BASIS, precision=PRECISION,
              assumptions=assumptions or {})


def _fail(exc, code="JLC_INPUT"):
    return err("%s: %s" % (type(exc).__name__, exc), code,
               hint="the JLC gates never guess: an undeclared process, a missing "
                    "measurement or an unparsable file comes back as an error, not as "
                    "a pass")


def register(_tool, _str, _num, _bool, _arr):
    from . import api

    # ------------------------------------------------------------- library
    def jlc_library_validate(args):
        try:
            report = api.validate_library()
        except Exception as exc:                       # noqa: BLE001
            return _fail(exc)
        return _env(report, unit="problems", assumptions={
            "rule": "a rule without a source / quote / tier is not allowed in the library"})

    _tool(
        "jlc_library_validate",
        "Self-check of the bundled JLCPCB capability library (29 PCB + 20 SMT + 24 "
        "BOM/CPL rules, 6 official rule presets, 19 known pitfalls): every rule must "
        "carry a source id registered with tier T0/T1/T2, a url, a fetched_at and a "
        "verbatim quote, and every limit must resolve to a value/by/set. Returns the "
        "list of problems; empty means the numbers may be cited. T3 (third-party) "
        "sources are refused by the loader. Run this before quoting any limit.",
        {"type": "object", "properties": {}},
        jlc_library_validate,
    )

    def jlc_capabilities(args):
        try:
            report = api.capabilities(args.get("kind") or "pcb", args.get("rule_id"))
        except Exception as exc:                       # noqa: BLE001
            return _fail(exc)
        return _env(report, unit="rules", assumptions={
            "scope": "capability = what the fab says it can build, not what the board is"})

    _tool(
        "jlc_capabilities",
        "Read the bundled JLCPCB capability rows (kind=pcb 29 rules / smt 20 rules) with "
        "their limit/recommended values keyed by process (layers, copper weight), the "
        "cost_trigger bands, and the source label of each number. Read-only lookup - it "
        "does not compare anything against a board; use jlc_spec_check for that.",
        {"type": "object", "properties": {
            "kind": _str("which library", ["pcb", "smt"], "pcb"),
            "rule_id": _str("return only this rule id (e.g. PCB.TRACE.WIDTH_MIN)"),
        }},
        jlc_capabilities,
    )

    def jlc_process(args):
        try:
            if args.get("action") == "template":
                return _env(api.process_template(), unit="template")
            report = api.process_check(args.get("process"))
        except Exception as exc:                       # noqa: BLE001
            return _fail(exc)
        return _env(report, unit="problems", assumptions={
            "never_guessed": "layers and copper_oz have no defensible default"})

    _tool(
        "jlc_process",
        "Fabrication process declaration for the JLCPCB gates. action=template returns "
        "the JSON skeleton (layers / copper_oz / thickness_mm / surface / edge / "
        "mask_color / smt); action=check (default) validates a filled declaration. "
        "layers and copper_oz are REQUIRED and are never defaulted - every downstream "
        "rule resolves its limit from these two, so a guessed value would silently pick "
        "the wrong row. smt must be null / economy / standard.",
        {"type": "object", "properties": {
            "action": _str("template or check", ["template", "check"], "check"),
            "process": {"type": "object", "description":
                        "the declaration, e.g. {layers: 2, copper_oz: 1, thickness_mm: 1.6, "
                        "surface: HASL_LEADFREE, edge: routed, smt: null}"},
        }},
        jlc_process,
    )

    # --------------------------------------------------------------- checks
    def jlc_spec_check(args):
        try:
            report = api.spec_check(args.get("process"), args.get("measurements") or {},
                                    partial=bool(args.get("partial")))
        except Exception as exc:                       # noqa: BLE001
            return _fail(exc)
        return _env(report, unit="verdict", assumptions={
            "partial": "when partial=true, NEEDS_EVIDENCE no longer exits 2 but the report "
                       "says the uncovered rules are not a pass"})

    _tool(
        "jlc_spec_check",
        "Compare declared measurements against the JLCPCB capability library, three "
        "tiers per rule: BLOCK (below the published limit), WARN (between limit and "
        "recommended), PASS (at/above recommended), plus NEEDS_EVIDENCE for an "
        "applicable rule with no measured value and N/A when the board has no such "
        "object (write the string NONE_PRESENT as the measured value, e.g. no "
        "through-holes at all). exit 0 = no blocker inside the declared scope, 1 = a "
        "blocker, 2 = BLOCKED because evidence is missing (fail-closed; partial=true "
        "turns that off but the summary still says `partial`). It never fills a missing "
        "measurement with a typical value and exit 0 does not mean the circuit is right.",
        {"type": "object", "properties": {
            "process": {"type": "object", "description": "declaration; layers and copper_oz required"},
            "measurements": {"type": "object", "description":
                             "measured minima keyed by rule key (min_track_width, "
                             "min_track_space, min_plated_drill, ...); NONE_PRESENT for "
                             "an object the board does not have"},
            "partial": _bool("missing evidence is reported, not treated as a blocker", False),
        }, "required": ["process"]},
        jlc_spec_check,
    )

    def jlc_gerber_guard(args):
        try:
            report = api.gerber_guard(
                args.get("package"), args.get("process"),
                strict=bool(args.get("strict")),
                structure_only=bool(args.get("structure_only")),
                extra_measurements=args.get("extra_measurements"))
        except Exception as exc:                       # noqa: BLE001
            return _fail(exc)
        return _env(report, unit="verdict", assumptions={
            "layers_by_role": "layers are identified by role (copper_top / mask_bottom / "
                              "outline / drill_plated / ...) from the extension, the KiCad "
                              "layer name or %TF.FileFunction%, so both JLCPCB and KiCad "
                              "naming work",
            "uncovered": "track spacing, pad-to-track, mask bridge, silk height, "
                         "silk-to-pad and copper-to-edge cannot be read out of Gerber; "
                         "they are listed under `uncovered` and never counted as a pass"})

    _tool(
        "jlc_gerber_guard",
        "Independent review of an exported Gerber + Excellon package - it parses RS274X "
        "itself instead of trusting the EDA's own DRC. Measures: board outline closure and "
        "bbox, minimum track width, minimum aperture, smallest plated / non-plated / slot "
        "drill, via count, minimum silkscreen width, unit and coordinate-format "
        "consistency, M02*/M30 terminators, and that the declared layer count matches the "
        "inner-layer files present. Answers BLOCK / WARN / PASS / NEEDS_EVIDENCE per item "
        "and lists what it cannot see (`uncovered`). `package` is a zip or a directory. "
        "structure_only skips the spec comparison; strict makes missing evidence an "
        "error; extra_measurements supplies what a Gerber cannot carry.",
        {"type": "object", "properties": {
            "package": _str("zip of the exported package, or a directory of the files"),
            "process": {"type": "object", "description":
                        "declaration; omit to run structure checks only"},
            "structure_only": _bool("skip the capability comparison", False),
            "strict": _bool("missing evidence becomes an error", False),
            "extra_measurements": {"type": "object", "description":
                                   "values from the EDA API (min_track_space, "
                                   "min_pad_to_track, min_via_outer, ...)"},
        }, "required": ["package"]},
        jlc_gerber_guard,
    )

    def jlc_bom_cpl(args):
        try:
            action = args.get("action") or "check"
            if action == "normalize":
                report = api.bom_cpl_normalize(bom=args.get("bom"), cpl=args.get("cpl"),
                                               out_dir=args.get("out_dir"))
            else:
                report = api.bom_cpl_check(bom=args.get("bom"), cpl=args.get("cpl"),
                                           board_size=args.get("board_size"),
                                           gerber_path=args.get("gerber_path"))
        except Exception as exc:                       # noqa: BLE001
            return _fail(exc)
        return _env(report, unit="verdict", assumptions={
            "encodings": "EDA exports UTF-16 + TAB; a BOM/CPL in that form is a WARN with "
                         "a normalization offer, not a silent mis-parse",
            "not_checked": "part number correctness and polarity orientation are not "
                           "verifiable from the files"})

    _tool(
        "jlc_bom_cpl",
        "JLCPCB assembly-file review. action=check parses a BOM and/or a CPL, reports the "
        "real encoding (EDA-native UTF-16 TAB gets a WARN plus a normalization offer, "
        "never a silent mis-parse), required columns, designator grammar (length, "
        "forbidden characters, separator style, duplicates, case duplicates), quantity "
        "consistency, LCSC part-number format, coordinate units, rotation decimals, and "
        "cross-checks that BOM and CPL carry the same designator set and that every CPL "
        "coordinate lies inside the board (from the Gerber outline via gerber_path, or an "
        "explicit board_size). action=normalize writes UTF-8 comma CSV copies into "
        "out_dir and refuses to overwrite. It cannot verify that a part number is the "
        "right part, and it says so.",
        {"type": "object", "properties": {
            "action": _str("check or normalize", ["check", "normalize"], "check"),
            "bom": _str("BOM file (csv / txt)"),
            "cpl": _str("pick-and-place file (csv / txt)"),
            "gerber_path": _str("Gerber package, used to read the board size for the "
                                "coordinate-range check"),
            "board_size": {"type": "array", "items": {"type": "number"},
                           "description": "[width_mm, height_mm] when no Gerber is at hand"},
            "out_dir": _str("existing directory for action=normalize"),
        }},
        jlc_bom_cpl,
    )

    def jlc_rules_check(args):
        try:
            if not args.get("preset") and not args.get("config_path"):
                return _env(api.rules_presets(), unit="presets")
            report = api.rules_check(args.get("process"), args.get("config_path"),
                                     args.get("preset"))
        except Exception as exc:                       # noqa: BLE001
            return _fail(exc)
        return _env(report, unit="verdict", assumptions={
            "reading": "the `measured` column is the minimum the DRC rule allows, not the "
                       "geometry on the board - passing these rules only means the board "
                       "obeys them"})

    _tool(
        "jlc_rules_check",
        "Compare one DRC rule configuration against the capability library: extract the "
        "minimum the preset allows (track width, spacing per copper weight, drill and via "
        "diameters, hole-to-hole, mask expansion) and grade each against the published "
        "limits. Give `preset` (one of the six official JLCPCB Capability presets, list "
        "them by calling with neither argument) or `config_path` (a rule JSON dumped from "
        "the EDA). This checks the RULES, not the board - an overly permissive preset is "
        "the thing it catches.",
        {"type": "object", "properties": {
            "process": {"type": "object", "description": "declaration; layers and copper_oz required"},
            "preset": _str("official preset name (see the list)"),
            "config_path": _str("path to a rule configuration JSON instead of a preset"),
        }},
        jlc_rules_check,
    )

    # ---------------------------------------------------------- dialogue gates
    def jlc_gate(args):
        try:
            report = api.gate(args.get("action") or "status", args.get("project"),
                              name=args.get("name"), gate=args.get("gate"),
                              choice=args.get("choice"), user_reply=args.get("user_reply"),
                              bind=args.get("bind"), note=args.get("note"),
                              apply=bool(args.get("apply")))
        except Exception as exc:                       # noqa: BLE001
            return _fail(exc, "JLC_GATE")
        return _env(report, unit="state", assumptions={
            "meaning": "CONFIRMED means the user accepted that version of the stage, not "
                       "that the stage is technically correct",
            "stale": "editing a bound file turns the confirmation STALE, and a STALE gate "
                     "invalidates every later one"})

    _tool(
        "jlc_gate",
        "Five-stage dialogue gate for a JLCPCB project: G1 schematic, G2 library/footprint, "
        "G3 placement, G4 PCB, G5 export release. action=init writes workflow.json + "
        "process.json into the project dir (apply=true needed), action=status reports each "
        "gate as PENDING / CONFIRMED / STALE plus whether the process declaration is "
        "complete, action=confirm records one gate. A confirmation REQUIRES the user's own "
        "words in user_reply (the agent must never answer a gate for the user) and, for a "
        "pass, at least one file bound by sha256 in bind=[...]; editing that file turns the "
        "gate STALE. Gates run in order and a broken earlier gate blocks the later ones. "
        "choice=1 pass / 2 revise and re-review / 3 pause.",
        {"type": "object", "properties": {
            "action": _str("init / status / confirm", ["init", "status", "confirm"], "status"),
            "project": _str("project directory"),
            "name": _str("project name (action=init)"),
            "gate": _str("which gate (G1..G5)"),
            "choice": _num("1 pass / 2 revise / 3 pause (action=confirm)"),
            "user_reply": _str("the user's own words - mandatory for a confirmation"),
            "bind": _arr("file(s) the confirmation is bound to, relative to the project dir"),
            "note": _str("optional note recorded with the confirmation"),
            "apply": _bool("really write workflow.json / process.json (action=init)", False),
        }, "required": ["project"]},
        jlc_gate,
    )

    def jlc_release(args):
        try:
            report = api.release(args.get("project"), args.get("version"),
                                 args.get("gerber_path"), bom=args.get("bom"),
                                 cpl=args.get("cpl"), extra=args.get("extra"),
                                 apply=bool(args.get("apply")))
        except Exception as exc:                       # noqa: BLE001
            return _fail(exc, "JLC_RELEASE")
        return _env(report, unit="files", assumptions={
            "local_only": "the release is a local directory + manifest: nothing is "
                          "uploaded and no order is placed",
            "recut": "the guards are re-run at seal time - an old report is not trusted"})

    _tool(
        "jlc_release",
        "Seal a release package: require G1-G5 all CONFIRMED and a complete process "
        "declaration, RE-RUN the Gerber guard and the BOM/CPL checks at seal time (an old "
        "report is not trusted), then copy the artefacts into "
        "<project>/release/<version>/ with manifest.json (sha256 of every file, the gate "
        "trail, the confirmations and a report snapshot) plus reports/*.json, process.json "
        "and workflow.snapshot.json. apply=false reports what would be written. It never "
        "uploads, never places an order and never touches the source files.",
        {"type": "object", "properties": {
            "project": _str("project directory holding workflow.json"),
            "version": _str("release label, e.g. v1"),
            "gerber_path": _str("the Gerber package (zip or directory)"),
            "bom": _str("BOM file"),
            "cpl": _str("position file"),
            "extra": _str("extra measurements JSON"),
            "apply": _bool("really write the release directory", False),
        }, "required": ["project", "version", "gerber_path"]},
        jlc_release,
    )

    # ------------------------------------------------------ layout / schematic
    def jlc_geometry_audit(args):
        try:
            report = api.geometry_audit(args.get("evidence"))
        except Exception as exc:                       # noqa: BLE001
            return _fail(exc)
        return _env(report, unit="issues", assumptions={
            "input": "measured EDA JSON only: schema_version=1, domain=pcb|schematic, "
                     "capture_source=eda_readback, per-object bbox from the EDA; synthetic "
                     "or estimated geometry is refused",
            "not": "not an ERC/DRC and not a layout solver"})

    _tool(
        "jlc_geometry_audit",
        "Read-only geometry preflight on a measured layout: overlap between solid objects, "
        "objects outside the board frame, mounting holes or keep-outs covered by copper or "
        "components, components outside the frame, wires crossing a body, and text/annotation "
        "collisions - each reported as BLOCK / WARN / SUGGEST / NEEDS_EVIDENCE with the object "
        "ids it concerns. Takes EDA read-back JSON (schema_version=1, domain=pcb, "
        "capture_source=eda_readback, per-object bbox); missing evidence is reported, never "
        "assumed, and it refuses documents that do not declare a real read-back. It flags "
        "geometry, it does not place anything.",
        {"type": "object", "properties": {
            "evidence": {"type": "object", "description": "the measured layout JSON"},
        }, "required": ["evidence"]},
        jlc_geometry_audit,
    )

    def jlc_schematic_spec(args):
        try:
            if args.get("action") == "template":
                return _env(api.schematic_template(), unit="template")
            report = api.schematic_spec(args.get("spec"), args.get("evidence") or {})
        except Exception as exc:                       # noqa: BLE001
            return _fail(exc)
        return _env(report, unit="issues", assumptions={
            "input": "the schematic PROJECT SPEC plus normalized read-back evidence",
            "not": "it checks the drawing against the spec's declared rules; it is not an ERC"})

    _tool(
        "jlc_schematic_spec",
        "Schematic-level preflight: take the schematic project spec (blocks, required "
        "labels, power flags, annotation completeness, sheet frame) and the read-back "
        "evidence, then report missing/overlapping/misplaced items with severity and the "
        "objects concerned. action=template returns the spec skeleton. It judges the "
        "drawing against the declared spec, not the circuit's electrical correctness.",
        {"type": "object", "properties": {
            "action": _str("audit or template", ["audit", "template"], "audit"),
            "spec": {"type": "object", "description": "the schematic project spec"},
            "evidence": {"type": "object", "description": "normalized EDA read-back"},
        }},
        jlc_schematic_spec,
    )

    def jlc_pitfalls(args):
        try:
            report = api.known_pitfalls(args.get("area"), args.get("query"))
        except Exception as exc:                       # noqa: BLE001
            return _fail(exc)
        return _env(report, unit="entries", assumptions={
            "source": "19 pitfalls recorded with the official documentation that produced them"})

    _tool(
        "jlc_pitfalls",
        "Look up the bundled known-pitfall list (19 entries: gerber export, drill, BOM/CPL, "
        "layer naming, mask, panelisation, version drift ...). Filter by `area` (one of the "
        "listed areas) and/or a keyword `query`. Each entry names the pitfall, why it "
        "happens and what to check - it is a checklist, not a verdict.",
        {"type": "object", "properties": {
            "area": _str("pitfall area"),
            "query": _str("keyword filter"),
        }},
        jlc_pitfalls,
    )
