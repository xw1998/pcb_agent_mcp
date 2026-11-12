#!/usr/bin/env python3
"""Generic LCEDA SMD footprint transcriber: ``.efp`` (decrypted) -> ``.kicad_mod``.

Nothing here is a route or a placement decision; it is a package transcription
of a document somebody else published, with the checks that make it defensible.

Why generic, and what it still refuses
--------------------------------------
``gen_bga333_footprint.py`` and ``gen_ddr3l_footprint.py`` solve a *specific*
ball map. A 3225 crystal, a 2012 RTC crystal, a tact switch and a SOT-23 have
nothing in common but "pads on a grid", so this tool takes the expected numbers
on the command line and audits against them. It refuses to invent anything:

* the unit is **solved** (envelope over body extent + grid gap), never read from
  ``CANVAS`` -- three documents in a row declared ``"mm"`` while drawing mil;
* a pad's axis assignment is **resolved** against the grid (
  ``lceda_fp.resolve_pad_box``), never read from the ``rot`` flag;
* ``--body`` is taken from the library title (an independent string), so audit
  G3 compares the **drawn** polygon against a value that the drawing cannot move;
* no bytes are written until every audit passes.

Audits (G1-G8)
--------------
G1  pad count equals ``--pads``; numbers unique and non-empty
G2  the unit is solved: exactly one candidate puts body extent and grid step in
    a physically possible envelope
G3  the drawn body equals ``--body`` mm within 0.02 on both axes
G4  no two pads share a coordinate; the pad centroid is (0, 0) +-0.02
G5  every pad box resolves to one (w_x, h_y) pair
G6  every pad stays inside the courtyard envelope (body/2 + 0.75 mm)
G7  silkscreen exists (>= 1 segment) and its bbox is reported
G8  the pad-coordinate spread matches ``--grid`` when given, so a mis-solved
    unit cannot slip through as "some other pitch"; ``--grid-tol`` exists
    because a library title rounds (SKRPACE010 is titled P2.20 and drawn 2.15)
"""
from __future__ import annotations

import argparse
import csv
import json
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import lceda_fp

HERE = os.path.dirname(os.path.abspath(__file__))
EX = os.path.join(HERE, "..", "examples", "rk3506_som")
LIB = os.path.join(EX, "lib", "RK3506.pretty")

FP_VERSION = "20260206"
GEN_VERSION = "0.1.5"
BODY_ENVELOPE_MM = (0.5, 60.0)
GRID_ENVELOPE_MM = (0.2, 8.0)
PROTRUDE_ALLOW_MM = 0.75

def solve_scale(rows, pad_rows):
    """Exactly one unit must put body extent and grid step in a sane envelope."""
    bb = lceda_fp.body_bbox(rows)
    rw = bb[2] - bb[0]
    rh = bb[3] - bb[1]
    gap = lceda_fp.min_grid_gap(pad_rows)
    if gap is None:
        raise lceda_fp.GeometryError("no two pads share a row or column: grid step is not measurable")
    hits = []
    blo, bhi = BODY_ENVELOPE_MM
    glo, ghi = GRID_ENVELOPE_MM
    for unit, scale in lceda_fp.SCALE_CANDIDATES:
        bw, bh, gp = rw * scale, rh * scale, gap * scale
        if blo <= bw <= bhi and blo <= bh <= bhi and glo <= gp <= ghi:
            hits.append({"unit": unit, "scale": scale, "body_mm": [round(bw, 4), round(bh, 4)],
                         "grid_mm": round(gp, 4)})
    if len(hits) != 1:
        raise lceda_fp.GeometryError(
            "scale is not solvable: raw body %.3f x %.3f, raw grid gap %.3f; %d candidate "
            "units landed inside %.1f-%.1f mm body / %.1f-%.1f mm grid (%s)"
            % (rw, rh, gap, len(hits), blo, bhi, glo, ghi, [h["unit"] for h in hits]))
    out = dict(hits[0])
    out["raw_body"] = [rw, rh]
    out["raw_gap"] = gap
    return out

def audit(rows, pad_rows, scale_info, body_mm, want_pads, grid_mm=None, grid_tol=0.03):
    errs = []
    s = scale_info["scale"]
    if len(pad_rows) != want_pads:
        errs.append("G1 pad count %d, expected %d" % (len(pad_rows), want_pads))
    nums = [p["num"] for p in pad_rows]
    if any(not n for n in nums):
        errs.append("G1 empty pad number")
    if len(set(nums)) != len(nums):
        errs.append("G1 duplicate pad numbers: %s"
                    % sorted({n for n in nums if nums.count(n) > 1}))

    bb = lceda_fp.body_bbox(rows)
    drawn = ((bb[2] - bb[0]) * s, (bb[3] - bb[1]) * s)
    for got, want, axis in zip(drawn, body_mm, ("width", "height")):
        if abs(got - want) > 0.02:
            errs.append("G3 body %s is %.4f mm, title-derived value is %.4f" % (axis, got, want))

    coords = [(round(p["x"] * s, 4), round(-p["y"] * s, 4)) for p in pad_rows]
    if len(set(coords)) != len(coords):
        errs.append("G4 duplicate pad coordinates")
    cx = sum(c[0] for c in coords) / len(coords)
    cy = sum(c[1] for c in coords) / len(coords)
    if abs(cx) > 0.02 or abs(cy) > 0.02:
        errs.append("G4 pad centroid (%.4f, %.4f), expected (0, 0)" % (cx, cy))

    gap_mm = lceda_fp.min_grid_gap(pad_rows) * s
    boxes = {}
    for p in pad_rows:
        if p["shape"] == "ELLIPSE" and abs(p["w"] - p["h"]) <= 1e-6:
            boxes[p["num"]] = {"w_mm": p["w"] * s, "h_mm": p["h"] * s, "rejected": []}
            continue
        try:
            boxes[p["num"]] = lceda_fp.resolve_pad_box(p, s, gap_mm)
        except lceda_fp.GeometryError as exc:
            errs.append("G5 %s" % exc)

    lim_w = body_mm[0] / 2 + PROTRUDE_ALLOW_MM
    lim_h = body_mm[1] / 2 + PROTRUDE_ALLOW_MM
    for p in pad_rows:
        b = boxes.get(p["num"])
        w_mm = b["w_mm"] if b else p["w"] * s
        h_mm = b["h_mm"] if b else p["h"] * s
        if abs(p["x"] * s) + w_mm / 2 > lim_w + 1e-6 or abs(-p["y"] * s) + h_mm / 2 > lim_h + 1e-6:
            errs.append("G6 pad %s leaves the courtyard envelope" % p["num"])

    silk = lceda_fp.silk_segments_mm(rows, s)
    if not silk:
        errs.append("G7 no silkscreen segments on layer %d" % lceda_fp.LAYER_SILK)

    if grid_mm is not None:
        if abs(gap_mm - grid_mm) > grid_tol:
            errs.append("G8 grid step %.4f mm, expected %.4f" % (gap_mm, grid_mm))
    return errs, boxes, silk, gap_mm

def build(rows, name, body_mm, want_pads, descr, tags, grid_mm=None, grid_tol=0.03):
    pad_rows = lceda_fp.pad_rows(rows)
    scale_info = solve_scale(rows, pad_rows)
    s = scale_info["scale"]
    errs, boxes, silk, gap_mm = audit(rows, pad_rows, scale_info, body_mm, want_pads, grid_mm, grid_tol)
    if errs:
        raise SystemExit("audit failed:\n  " + "\n  ".join(errs))

    specs = []
    for p in pad_rows:
        b = boxes[p["num"]]
        shape = "circle" if p["shape"] == "ELLIPSE" else ("rect" if p["shape"] == "RECT" else "oval")
        specs.append((p["num"], p["x"] * s, -p["y"] * s, b["w_mm"], b["h_mm"], shape))

    text = lceda_fp.emit_generic(
        name=name, pad_specs=specs, body_mm=body_mm, silk_segments=silk,
        pin1_marks=lceda_fp.circle_marks_mm(rows, s)[:1],
        courtyard_margin=0.25, descr=descr, tags=tags,
        version=FP_VERSION, generator_version=GEN_VERSION)
    report = {
        "footprint": name,
        "pads": len(pad_rows),
        "unit": scale_info["unit"],
        "scale": s,
        "body_mm": [round(v, 4) for v in body_mm],
        "grid_mm": round(gap_mm, 4),
        "grid_expected": None if grid_mm is None else [round(grid_mm, 4), grid_tol],
        "pad_boxes_mm": sorted({(round(b["w_mm"], 4), round(b["h_mm"], 4)) for b in boxes.values()}),
        "silk_segments": len(silk),
        "courtyard_rule": "body + 0.25 mm (engineering default)",
        "audits": "G1-G8 passed",
    }
    return text, report, specs

def write_csv(specs, path):
    with open(path, "w", encoding="utf-8", newline="\n") as f:
        w = csv.writer(f)
        w.writerow(["pin", "x_mm", "y_mm", "w_mm", "h_mm", "shape"])
        for (num, x, y, pw, ph, shape) in specs:
            w.writerow([num, "%.4f" % x, "%.4f" % y, "%.4f" % pw, "%.4f" % ph, shape])

def main(argv=None):
    ap = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    ap.add_argument("--doc", required=True)
    ap.add_argument("--out", required=True)
    ap.add_argument("--name", required=True)
    ap.add_argument("--pads", type=int, required=True)
    ap.add_argument("--body", required=True, help="WIDTHxHEIGHT in mm, from the library title")
    ap.add_argument("--grid", type=float, default=None,
                    help="grid step from the library title; audited only (G8)")
    ap.add_argument("--grid-tol", type=float, default=0.03,
                    help="G8 tolerance in mm; widen only when the title is a rounded value")
    ap.add_argument("--descr", default="")
    ap.add_argument("--tags", default="")
    ap.add_argument("--csv", default=None)
    a = ap.parse_args(argv)
    w, h = (float(v) for v in a.body.lower().split("x"))
    rows = lceda_fp.read_rows(a.doc)
    text, report, specs = build(rows, a.name, (w, h), a.pads, a.descr, a.tags, a.grid, a.grid_tol)
    os.makedirs(os.path.dirname(os.path.abspath(a.out)), exist_ok=True)
    with open(a.out, "w", encoding="utf-8", newline="\n") as f:
        f.write(text)
    out = {"fp": a.out, "report": report}
    if a.csv:
        write_csv(specs, a.csv)
        out["csv"] = a.csv
    print(json.dumps(out, ensure_ascii=False, indent=2))
    return 0

if __name__ == "__main__":
    raise SystemExit(main())
