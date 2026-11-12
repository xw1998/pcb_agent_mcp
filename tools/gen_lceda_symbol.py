#!/usr/bin/env python3
"""LCEDA symbol document (``.efp`` decrypted by ``fetch_lceda_part.py``) -> ``.kicad_sym``.

Nothing here is a route; it is a pin-table transcription.

Why a separate tool
-------------------
``pytest``'s ``parts.symbol_to_kicad`` was written for one-off use; this tool
wraps it with the checks that make the output defensible, and refuses to write
a library that a footprint cannot be paired with:

* the symbol must have at least one PIN row,
* every pin number must be unique and non-empty,
* the description carries the source declaration the LCEDA licence demands
  (``lceda_fp.with_source``);
* when ``--footprint-pads`` is given, the pin-number set must equal the pad
  number set -- a symbol whose pin 9 has no pad (or the reverse) is an error,
  not a warning.

``parts.symbol_to_kicad`` returns ``(text, report)``; ``report["pins"]`` is an
``int``. Shape rows it cannot classify are dropped and named in
``report["dropped"]`` -- that list is surfaced here, never swallowed.
"""
from __future__ import annotations

import argparse
import json
import os
import sys

sys.path.insert(0, os.path.join(os.path.dirname(os.path.abspath(__file__)), "..", "src"))
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from pcb_agent_mcp import parts          # noqa: E402
import lceda_fp                           # noqa: E402

HERE = os.path.dirname(os.path.abspath(__file__))
EX = os.path.join(HERE, "..", "examples", "rk3506_som")

def audit(doc, pad_numbers):
    errs = []
    nums = doc["pins"]
    numbers = [p["number"] for p in nums]
    if not numbers:
        errs.append("S1 symbol has no pins")
    if any(not n for n in numbers):
        errs.append("S1 empty pin number")
    if len(set(numbers)) != len(numbers):
        dup = sorted({n for n in numbers if numbers.count(n) > 1})
        errs.append("S1 duplicate pin numbers: %s" % dup)
    if pad_numbers is not None:
        missing = sorted(set(pad_numbers) - set(numbers))
        extra = sorted(set(numbers) - set(pad_numbers))
        if missing:
            errs.append("S2 pads with no symbol pin: %s" % missing)
        if extra:
            errs.append("S2 symbol pins with no pad: %s" % extra)
    return errs

def pad_numbers_of(footprint_path):
    """Pad numbers of an already generated ``.kicad_mod`` (text scan, no parser)."""
    import re
    with open(footprint_path, "r", encoding="utf-8") as f:
        text = f.read()
    out = set()
    for m in re.finditer(r'\(pad "([^"]*)"', text):
        if m.group(1):
            out.add(m.group(1))
    return out

def build(doc_text, name, reference, footprint, datasheet, description, pad_numbers):
    description = lceda_fp.with_source(description)
    doc = parts.parse_symbol_doc(doc_text)
    errs = audit(doc, pad_numbers)
    if errs:
        raise SystemExit("audit failed:\n  " + "\n  ".join(errs))
    text, report = parts.symbol_to_kicad(doc_text, name=name, reference=reference,
                                         footprint=footprint, datasheet=datasheet,
                                         description=description)
    out = {
        "symbol": name,
        "reference": reference,
        "pins": report["pins"],
        "shapes": report["shapes"],
        "dropped": report.get("dropped", []),
        "pin_numbers": sorted(p["number"] for p in doc["pins"]),
        "cross_check": "pad set == pin set" if pad_numbers is not None else "not run",
        "audits": "S1-S2 passed",
    }
    return text, out

def main(argv=None):
    ap = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    ap.add_argument("--doc", required=True)
    ap.add_argument("--out", required=True)
    ap.add_argument("--name", required=True)
    ap.add_argument("--reference", default="U")
    ap.add_argument("--footprint", default="")
    ap.add_argument("--datasheet", default="")
    ap.add_argument("--description", default="")
    ap.add_argument("--footprint-pads", default=None,
                    help=".kicad_mod whose pad numbers must equal the pin numbers")
    a = ap.parse_args(argv)
    with open(a.doc, "r", encoding="utf-8") as f:
        doc_text = f.read()
    pads = pad_numbers_of(a.footprint_pads) if a.footprint_pads else None
    text, report = build(doc_text, a.name, a.reference, a.footprint,
                         a.datasheet, a.description, pads)
    os.makedirs(os.path.dirname(os.path.abspath(a.out)), exist_ok=True)
    with open(a.out, "w", encoding="utf-8", newline="\n") as f:
        f.write(text)
    print(json.dumps({"out": a.out, "report": report}, ensure_ascii=False, indent=2))
    return 0

if __name__ == "__main__":
    raise SystemExit(main())
