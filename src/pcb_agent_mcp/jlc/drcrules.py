"""Rule gate: are the EDA's *design rule settings* at least as tight as the fab's process?

Why this exists next to the EDA's own DRC: DRC only enforces whatever rules are configured.
Loosen a spacing rule and DRC still passes while the board cannot be made. So geometric
checking stays with the native DRC; this module checks **the ruler handed to the DRC**.

Ported from jlceda-pcb-ai-workflow (MIT) ``scripts/rules_check.py``. The input is the JSON
returned by LCEDA Pro's ``pcb_Drc.getCurrentRuleConfiguration()``, or a named preset from
``data/rule-presets/_all_raw.json``.

The numbers compared here are *rule minima*, not measured geometry, and the report says so.
"""
import json

from . import spec

DUMP_JS = "return await eda.pcb_Drc.getCurrentRuleConfiguration();"

PRESET_FILE = "rule-presets/_all_raw.json"


def _oz_key(oz):
    return "copperThickness%soz" % spec._fmt_oz(oz)


def _cell(table, rows, a, b):
    i, j = rows.index(a), rows.index(b)
    if i < j:
        i, j = j, i
    try:
        return table[i][j]
    except (IndexError, TypeError):
        return None


def extract_measurements(config, copper_oz):
    """({rule key: minimum the rule allows}, [notes]). Keys that cannot be read are absent,
    which turns into NEEDS_EVIDENCE downstream - never into a guess."""
    measurements, notes = {}, []
    oz_key = _oz_key(copper_oz)
    try:
        track = config["Physics"]["Track"].get(oz_key)
        if track:
            measurements["min_track_width"] = track["form"]["strokeWidthMin"]
        else:
            notes.append("no %s in the track-width rules, width not checked" % oz_key)
    except (KeyError, TypeError):
        notes.append("cannot read Physics.Track")
    try:
        spacing = config["Spacing"]["Safe Spacing"].get(oz_key)
        if spacing:
            rows, table = spacing["row"], spacing["table"]
            measurements["min_track_space"] = _cell(table, rows, "Track", "Track")
            pad_to_track = [_cell(table, rows, "SMD Pad", "Track"), _cell(table, rows, "TH Pad", "Track")]
            measurements["min_pad_to_track"] = min(v for v in pad_to_track if v is not None)
            measurements["min_mask_bridge"] = _cell(table, rows, "SMD Pad", "SMD Pad")
            edge = [_cell(table, rows, "Board Outline", k)
                    for k in ("Track", "SMD Pad", "TH Pad", "Via")]
            measurements["min_copper_to_edge"] = min(v for v in edge if v is not None)
        else:
            notes.append("no %s in safe spacing, spacing rules not checked" % oz_key)
    except (KeyError, TypeError, ValueError):
        notes.append("cannot read Spacing.Safe Spacing")
    try:
        form = config["Physics"]["Via Size"]["viaSize"]["form"]
        measurements["min_plated_drill"] = form["viaInnerdiameterMin"]
        measurements["min_via_outer"] = form["viaOuterdiameterMin"]
    except (KeyError, TypeError):
        notes.append("cannot read Physics.Via Size")
    try:
        measurements["min_via_hole_edge_gap"] = \
            config["Spacing"]["Other Spacing"]["otherClearance"]["form"]["hole2Hole"]
    except (KeyError, TypeError):
        notes.append("cannot read Other Spacing.hole2Hole")
    try:
        form = config["Expansion"]["Solder Mask Expansion"]["solderMaskExpansion"]["form"]
        measurements["min_mask_expansion"] = min(form["padToplayerExpansion"],
                                                form["padBottomlayerExpansion"])
    except (KeyError, TypeError):
        notes.append("cannot read Expansion.Solder Mask Expansion")
    return {k: v for k, v in measurements.items() if v is not None}, notes


def load_config(path=None, preset=None):
    if preset:
        presets = spec.load(PRESET_FILE)
        if preset not in presets:
            raise KeyError("preset %r not found; available: %s" % (preset, list(presets)))
        return presets[preset], "official preset: %s" % preset
    with open(path, encoding="utf-8") as handle:
        data = json.load(handle)
    if isinstance(data, dict) and "config" in data and "Spacing" not in data:
        data = data["config"]
    if not isinstance(data, dict) or "Spacing" not in data:
        raise KeyError("this is not a rule configuration JSON (no Spacing section)")
    return data, "file: %s" % path


def check(config, process, label="rules"):
    """Compare one rule configuration against the capability library."""
    for need in ("layers", "copper_oz"):
        if process.get(need) is None:
            raise ValueError("process lacks required field %s (the process is never guessed)" % need)
    measurements, notes = extract_measurements(config, process["copper_oz"])
    results, summary, _code = spec.evaluate(
        process, measurements,
        [(spec.load("jlc-pcb-capabilities.json"), "pcb")], partial=True)
    keep = set(measurements)
    results = [r for r in results if r["key"] in keep]
    counts = {}
    for row in results:
        counts[row["verdict"]] = counts.get(row["verdict"], 0) + 1
    code = 1 if counts.get("BLOCK") else 0
    evaluated = sum(v for k, v in counts.items() if k not in ("NEEDS_EVIDENCE", "N/A"))
    return {"label": label, "results": results,
            "summary": {"total_applicable": len(results), "evaluated": evaluated, "counts": counts,
                        "cost_triggers": sum(1 for r in results if r["flags"]), "partial": True},
            "measurements": measurements, "notes": notes, "exit": code,
            "not_covered": ["silkscreen height", "silk-to-pad", "annular ring measurement",
                            "through-hole spacing", "measured mask bridge"],
            "caveat": "the 'measured' column here is the minimum the DRC rule allows, not the "
                      "geometry on the board; DRC passing only means the board obeys these rules"}
