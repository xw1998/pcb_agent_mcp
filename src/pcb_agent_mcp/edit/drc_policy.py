"""Which new DRC violations block a write, and which are only reported.

The first version of the apply gate treated *every* new violation as blocking,
which in practice meant a board could never be saved once its silkscreen had a
text line under the rule minimum: 69 violations on the STM32F401 core board were
all KiCad *warnings*, and every one of them stopped the write.

The policy here is deliberately built on the one classification this project does
not have to invent: **KiCad's own severity**. That field comes from the project's
rule table (`.kicad_dru` plus the built-in defaults), so:

* a project that promotes a rule to error sees it block - the decision stays with
  the person who owns the board, not with this file;
* anything NEW at severity ``error`` blocks;
* anything NEW at severity ``warning``/``exclusion`` is reported, counted, and
  given a one-line fix hint, but does not roll the write back;
* a violation whose severity this server cannot read is treated as blocking
  (fail closed). Guessing "probably cosmetic" is exactly the failure mode that
  produces a shipped board nobody looked at.

``strict=True`` restores the old behaviour for callers who want it (every new
violation blocks). The measured severities on the STM32F401 board that motivated
this file are in ``MEASURED`` below; the type→hint table only contains types that
were actually seen in a KiCad report, because a made-up hint is worse than none.
"""

BLOCKING_SEVERITIES = frozenset(["error"])

# Measured on _scratch/core/stm32f401re_core.kicad_pcb with kicad-cli 10.0.7:
# 69 violations, all severity "warning":
#   silk_edge_clearance 2, text_height 32, silk_overlap 14, silk_over_copper 21
MEASURED = {
    "silk_edge_clearance": "warning",
    "text_height": "warning",
    "silk_overlap": "warning",
    "silk_over_copper": "warning",
}

# What to do about a type we have actually seen. A type that is not here still
# gets counted and still obeys its severity - it just has no tailored advice.
HINTS = {
    "silk_edge_clearance": "silkscreen is within the edge clearance of the board "
                           "outline; move the marking inside, or narrow the rule",
    "silk_over_copper": "silkscreen covers an exposed pad/copper area; a board "
                        "house will clip it - move the marking or remove the mask "
                        "opening under it",
    "silk_overlap": "two silkscreen items overlap; shorten/relocate one of them",
    "text_height": "text is smaller than the minimum height in the design rules "
                   "(0.8 mm by default); raise the text height above that, or "
                   "lower the rule if the marking is only a reference",
    "clearance": "copper clearance is below the netclass minimum: move the items "
                 "apart, narrow the track, or reassign the netclass",
    "shorting_items": "two different nets touch: this is a short, always fix it",
    "copper_edge_clearance": "copper is closer to the board edge than allowed; "
                             "move it inboard or change the edge clearance rule",
    "hole_clearance": "a hole is too close to copper; move the via/pad or shrink "
                      "the drill",
    "unconnected_items": "the netlister says these pads are not joined; draw the "
                         "missing track or fix the net assignment",
    "track_dangling": "a track ends in mid-air; connect it or delete it",
    "via_dangling": "a via connects to nothing on one side; connect it or delete it",
    "courtyard_overlap": "two footprints' courtyards overlap; move them apart so "
                         "the assembler can place both",
}


def _severity(item):
    value = (item or {}).get("severity")
    if value is None:
        return None
    return str(value).strip().lower()


def is_blocking(item, strict=False):
    """Does this single violation block a write?

    Fail closed: an unreadable severity blocks, because "I could not tell" is
    not the same answer as "KiCad says it is cosmetic".
    """
    if strict:
        return True
    severity = _severity(item)
    if severity is None:
        return True
    return severity in BLOCKING_SEVERITIES


def hint_for(item):
    return HINTS.get(str((item or {}).get("type") or ""))


def classify(items, strict=False, limit=20):
    """Split violations into blocking/advisory with counts and fix hints."""
    items = list(items or [])
    blocking, advisory = [], []
    for item in items:
        entry = {"type": item.get("type"), "severity": item.get("severity"),
                 "description": item.get("description")}
        if item.get("pos") is not None:
            entry["pos"] = item.get("pos")
        hint = hint_for(item)
        if hint:
            entry["fix"] = hint
        (blocking if is_blocking(item, strict=strict) else advisory).append(entry)
    counts = {}
    for item in items:
        key = _severity(item) or "unreadable"
        counts[key] = counts.get(key, 0) + 1
    return {
        "policy": ("a NEW violation blocks only at KiCad severity 'error'; "
                   "'warning'/'exclusion' are reported and counted; a violation "
                   "whose severity cannot be read blocks as well (fail closed)"),
        "strict": bool(strict),
        "total": len(items),
        "by_severity": counts,
        "blocking_total": len(blocking),
        "advisory_total": len(advisory),
        "blocking": blocking[:limit],
        "advisory": advisory[:limit],
    }
