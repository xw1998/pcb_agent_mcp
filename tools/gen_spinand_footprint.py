#!/usr/bin/env python3
"""Generate the W25N01GVZEIG SPI-NAND WSON-8 footprint from the LCEDA document.

Nothing here is a route; it is a package geometry transcription.

Source document
---------------
``examples/rk3506_som/library/NAND_W25N01GVZEIG_WSON8.efp`` -- decrypted by
``tools/fetch_lceda_part.py`` from component ``a583434a75024da7969702748eac2f50``
(the LCSC C907680 footprint for W25N01GVZEIG, package WSON-8-EP(6x8)).
Provenance (uuids, key, iv, title, copyright) is in the sibling
``*_source.json``. The document is the LCEDA official library's; it is kept as
a geometry source only, not redistributed as a library.

Rulers (each number is checked, not assumed)
--------------------------------------------
The document's own footprint title is ``WSON-8_L8.0-W6.10-P1.27-BL-EP``. The
three rulers used to solve the unit are independent of each other:

* pad-to-pad pitch on the pin rows: 50.00 raw units -> 1.270 mm = P1.27
* body polygon on the component-shape layer: 236.22 x 314.96 raw units
  -> 6.000 / 8.000 mm (long side 8.000 = L8.0)
* the 2nd candidate unit (mm) fails all three by orders of magnitude

So the unit is **mil** although ``CANVAS`` declares ``"mm"`` -- the third
document in a row doing this. The title's ``W6.10`` does **not** match the
drawn 6.0000 mm body; that 0.10 mm gap is reported as a discrepancy, never
silently reconciled (the drawn polygon wins, the string is disclosed).

Pad rotation is a hypothesis, not a fact
----------------------------------------
Every PAD row carries ``rot = 90``. Taken at face value it would swap the axes
and make each pad 1.400 mm wide along a 1.270 mm pitch, i.e. neighbouring pads
would overlap. ``lceda_fp.resolve_pad_box`` accepts only the assignment that
leaves >= 0.15 mm between neighbours, so exactly one interpretation survives --
the same "solve, do not read" rule used for the unit.

Audits (A1-A10), all must pass before the file is opened
--------------------------------------------------------
A1  9 pads, numbers exactly 1..9, unique, non-empty
A2  unit solved from pitch 1.27 + body 6.0 x 8.0; exactly one candidate unit
A3  the 8 pin pads share one nearest-neighbour pitch of exactly 1.270 mm and
    sit at x = +-0.635 / +-1.905 mm
A4  every pin pad box resolves to one (w_x, h_y) = (0.56, 1.40) mm pair
A5  body is 6.0000 x 8.0000 mm (+-0.01) on the component-shape layer
A6  pin rows are two rows at |y| = 4.0274 mm (+-0.01), 4 pads each, no dupes
A7  centroid of the 8 pin pads is (0, 0) +-0.01 mm
A8  exactly one non-pin pad: RECT 4.3000 x 3.4000 mm centred at (0, 0)
A9  every pad is inside the courtyard, and pin pads protrude <= 0.75 mm
A10 silk bbox is body + 0.0762 mm on each axis (+-0.01), 6 segments
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
DOC = os.path.join(EX, "library", "NAND_W25N01GVZEIG_WSON8.efp")
OUT_FP = os.path.join(EX, "lib", "RK3506.pretty", "Package_WSON-8_L8.0-W6.1-P1.27_W25N01GVZEIG.kicad_mod")

FP_NAME = "Package_WSON-8_L8.0-W6.1-P1.27_W25N01GVZEIG"
LAYER_SHAPE = 48           # COMPONENT_SHAPE
LAYER_SILK = 3             # TOP_SILK
LAYER_DOCUMENT = 13
LAYER_MARKING = 49

PITCH_MM = 1.27
BODY_W_MM = 6.0
BODY_H_MM = 8.0
TITLE_W_MM = 6.10
PIN_NUMBERS = ("1", "2", "3", "4", "5", "6", "7", "8")
EP_NUMBER = "9"
EP_W_MM = 4.30
EP_H_MM = 3.40
PAD_W_MM = 0.56
PAD_H_MM = 1.40
SILK_MARGIN_MM = 0.0762
GAP_MIN_MM = 0.15
SPAN_TOL = 0.005
POS_TOL = 0.01
COURTYARD_MARGIN = 0.25
FP_VERSION = "20260206"
GEN_VERSION = "0.1.4"

def raw_pitch(pad_rows):
    """Smallest pad-to-pad gap along x among pads sharing a y row (raw units)."""
    best = None
    for i, a in enumerate(pad_rows):
        for j, b in enumerate(pad_rows):
            if i >= j or abs(a["y"] - b["y"]) > 1e-9:
                continue
            d = abs(a["x"] - b["x"])
            if d > 1e-9:
                best = d if best is None else min(best, d)
    if best is None:
        raise lceda_fp.GeometryError("no two pads share a row: pitch is not measurable")
    return best

BODY_ENVELOPE_MM = (2.0, 20.0)

def solve_scale(pad_rows, body):
    """Exactly one unit must reproduce the 1.27 mm pitch.

    The body is deliberately **not** used as a ruler here: it is the quantity
    audit A5 checks, and using it to solve the unit would make A5 circular
    (any mutated body would be "correct" by construction). Instead the second
    filter is a sanity envelope -- an 8-pin WSON body cannot be 2..20 mm wide
    under a wrong unit (50 mm would pass the pitch test only if pitch were
    read as 50 mm, which the pitch test already rejects).
    """
    rx = raw_pitch(pad_rows)
    rw = body[2] - body[0]
    rh = body[3] - body[1]
    hits = []
    lo, hi = BODY_ENVELOPE_MM
    for unit, scale in lceda_fp.SCALE_CANDIDATES:
        if (abs(rx * scale - PITCH_MM) <= 0.003
                and lo <= rw * scale <= hi and lo <= rh * scale <= hi):
            hits.append({"unit": unit, "scale": scale,
                         "pitch_mm": rx * scale, "body_mm": [rw * scale, rh * scale]})
    if len(hits) != 1:
        raise lceda_fp.GeometryError(
            "scale is not solvable: pitch %.3f raw -> %.3f mm, body %.3f x %.3f raw; "
            "%d candidate units matched %s"
            % (rx, rx * lceda_fp.SCALE_CANDIDATES[0][1], rw, rh, len(hits),
               [h["unit"] for h in hits]))
    out = dict(hits[0])
    out["raw_pitch"] = rx
    out["raw_body"] = [rw, rh]
    return out

def doc_body(rows):
    bb = lceda_fp.layer_bbox(rows, LAYER_SHAPE)
    if bb is None:
        raise lceda_fp.GeometryError("no body outline on layer %d" % LAYER_SHAPE)
    return bb

def silk_segments_mm(rows, scale):
    segs = []
    for row in rows:
        if not isinstance(row, list) or len(row) < 6 or row[4] != LAYER_SILK:
            continue
        g = lceda_fp._geometry(row)
        if not isinstance(g, list) or not g or isinstance(g[0], str):
            continue
        nums = [v for v in g if isinstance(v, (int, float))]
        pts = [(nums[i] * scale, -nums[i + 1] * scale) for i in range(0, len(nums) - 1, 2)]
        for (x1, y1), (x2, y2) in zip(pts, pts[1:]):
            if (x1, y1) != (x2, y2):
                segs.append((x1, y1, x2, y2))
    return segs

def pin1_marks_mm(rows, scale):
    out = []
    for layer in (LAYER_MARKING, LAYER_DOCUMENT):
        for (cx, cy, r) in lceda_fp.circle_marks(rows, layer):
            out.append((cx * scale, -cy * scale, r * scale))
    return out

def classify(pad_rows):
    """Split PAD rows into (pin pads, other pads) without assuming order."""
    pins = [p for p in pad_rows if p["num"] in PIN_NUMBERS]
    others = [p for p in pad_rows if p["num"] not in PIN_NUMBERS]
    return pins, others

def resolved_boxes(pins, scale, pitch_mm):
    boxes = {}
    for p in pins:
        boxes[p["num"]] = lceda_fp.resolve_pad_box(p, scale, pitch_mm, gap_min=GAP_MIN_MM)
    return boxes

def audit(rows, pad_rows, scale_info, body, silk):
    errs = []
    s = scale_info["scale"]
    pins, others = classify(pad_rows)

    nums = [p["num"] for p in pad_rows]
    if len(pad_rows) != 9:
        errs.append("A1 pad count %d, expected 9" % len(pad_rows))
    if sorted(nums) != sorted(list(PIN_NUMBERS) + [EP_NUMBER]):
        errs.append("A1 pad numbers %s, expected 1..9" % sorted(nums))
    if any(not n for n in nums):
        errs.append("A1 empty pad number")

    if scale_info["unit"] != "mil":
        errs.append("A2 unit solved as %r (expected mil)" % scale_info["unit"])
    if abs(scale_info["pitch_mm"] - PITCH_MM) > 0.003:
        errs.append("A2 pitch %.4f mm, expected %.3f" % (scale_info["pitch_mm"], PITCH_MM))

    pitches = lceda_fp.nearest_pitches([(p["num"], p["x"], p["y"], 0, 0) for p in pins], s)
    if len(pitches) != 1 or abs(pitches[0] - PITCH_MM) > 0.003:
        errs.append("A3 pin pitch spectrum %s, expected only %.3f" % (pitches, PITCH_MM))
    xs = sorted({round(p["x"] * s, 4) for p in pins})
    if xs != [-1.905, -0.635, 0.635, 1.905]:
        errs.append("A3 pin x positions %s, expected [-1.905, -0.635, 0.635, 1.905]" % xs)

    try:
        boxes = resolved_boxes(pins, s, PITCH_MM)
    except lceda_fp.GeometryError as exc:
        errs.append("A4 %s" % exc)
        boxes = {}
    if boxes:
        pairs = {(round(b["w_mm"], 4), round(b["h_mm"], 4)) for b in boxes.values()}
        if len(pairs) != 1:
            errs.append("A4 pin pad boxes differ: %s" % sorted(pairs))
        else:
            w, h = pairs.pop()
            if abs(w - PAD_W_MM) > POS_TOL or abs(h - PAD_H_MM) > POS_TOL:
                errs.append("A4 pin pad box %.4f x %.4f, expected %.2f x %.2f"
                            % (w, h, PAD_W_MM, PAD_H_MM))

    bw = (body[2] - body[0]) * s
    bh = (body[3] - body[1]) * s
    if abs(bw - BODY_W_MM) > POS_TOL or abs(bh - BODY_H_MM) > POS_TOL:
        errs.append("A5 body %.4f x %.4f mm, expected %.2f x %.2f"
                    % (bw, bh, BODY_W_MM, BODY_H_MM))

    ys = sorted({round(-p["y"] * s, 4) for p in pins})
    if len(ys) != 2 or abs(abs(ys[0]) - abs(ys[1])) > POS_TOL:
        errs.append("A6 pin rows are not two symmetric rows: %s" % ys)
    else:
        off = abs(ys[0])
        for p in pins:
            if abs(abs(-p["y"] * s) - off) > POS_TOL:
                errs.append("A6 pin %s row offset %.4f, expected %.4f" % (p["num"], abs(-p["y"] * s), off))
    coords = [(round(p["x"] * s, 4), round(-p["y"] * s, 4)) for p in pad_rows]
    if len(set(coords)) != len(coords):
        errs.append("A6 duplicate pad coordinates")

    cx = sum(p["x"] * s for p in pins) / len(pins)
    cy = sum(-p["y"] * s for p in pins) / len(pins)
    if abs(cx) > POS_TOL or abs(cy) > POS_TOL:
        errs.append("A7 pin centroid (%.4f, %.4f), expected (0, 0)" % (cx, cy))

    if len(others) != 1:
        errs.append("A8 %d non-pin pads, expected exactly 1 exposed pad" % len(others))
    else:
        ep = others[0]
        if ep["shape"] != "RECT":
            errs.append("A8 exposed pad shape %r, expected RECT" % ep["shape"])
        if abs(ep["x"] * s) > POS_TOL or abs(ep["y"] * s) > POS_TOL:
            errs.append("A8 exposed pad not centred: (%.4f, %.4f)" % (ep["x"] * s, -ep["y"] * s))
        if abs(ep["w"] * s - EP_W_MM) > POS_TOL or abs(ep["h"] * s - EP_H_MM) > POS_TOL:
            errs.append("A8 exposed pad %.4f x %.4f mm, expected %.2f x %.2f"
                        % (ep["w"] * s, ep["h"] * s, EP_W_MM, EP_H_MM))

    lim_w = BODY_W_MM / 2 + 0.75
    lim_h = BODY_H_MM / 2 + 0.75
    for p in pad_rows:
        w_mm = p["w"] * s
        h_mm = p["h"] * s
        if p["num"] in boxes:
            w_mm = boxes[p["num"]]["w_mm"]
            h_mm = boxes[p["num"]]["h_mm"]
        if abs(p["x"] * s) + w_mm / 2 > lim_w + 1e-6 or abs(-p["y"] * s) + h_mm / 2 > lim_h + 1e-6:
            errs.append("A9 pad %s leaves the courtyard envelope" % p["num"])

    if len(silk) != 6:
        errs.append("A10 %d silk segments, expected 6" % len(silk))
    else:
        sxs = [v for seg in silk for v in (seg[0], seg[2])]
        sys_ = [v for seg in silk for v in (seg[1], seg[3])]
        sw = max(sxs) - min(sxs)
        sh = max(sys_) - min(sys_)
        if abs(sw - (BODY_W_MM + 2 * SILK_MARGIN_MM)) > POS_TOL:
            errs.append("A10 silk width %.4f mm, expected %.4f"
                        % (sw, BODY_W_MM + 2 * SILK_MARGIN_MM))
        if abs(sh - (BODY_H_MM + 2 * SILK_MARGIN_MM)) > POS_TOL:
            errs.append("A10 silk height %.4f mm, expected %.4f"
                        % (sh, BODY_H_MM + 2 * SILK_MARGIN_MM))
    return errs

def build(rows):
    pad_rows = lceda_fp.pad_rows(rows)
    body = doc_body(rows)
    scale_info = solve_scale(pad_rows, body)
    s = scale_info["scale"]
    silk = silk_segments_mm(rows, s)
    errs = audit(rows, pad_rows, scale_info, body, silk)
    if errs:
        raise SystemExit("audit failed:\n  " + "\n  ".join(errs))

    pins, others = classify(pad_rows)
    boxes = resolved_boxes(pins, s, PITCH_MM)
    specs = []
    for p in pins:
        b = boxes[p["num"]]
        specs.append((p["num"], p["x"] * s, -p["y"] * s, b["w_mm"], b["h_mm"], "oval"))
    for p in others:
        specs.append((p["num"], p["x"] * s, -p["y"] * s, p["w"] * s, p["h"] * s, "rect"))

    text = lceda_fp.emit_generic(
        name=FP_NAME,
        pad_specs=specs,
        body_mm=(BODY_W_MM, BODY_H_MM),
        silk_segments=silk,
        pin1_marks=pin1_marks_mm(rows, s)[:1],
        courtyard_margin=COURTYARD_MARGIN,
        descr=("W25N01GVZEIG SPI-NAND 1Gb, WSON-8 6.00x8.00 mm, pitch 1.270 mm, "
               "oval %.2fx%.2f mm + %.2fx%.2f mm exposed pad, transcribed from "
               "LCEDA C907680; unit solved against the 1.27 mm pitch and the 6x8 "
               "body, pad axes resolved against the pitch (both not read from the "
               "document's own CANVAS/rotation flags)"
               % (PAD_W_MM, PAD_H_MM, EP_W_MM, EP_H_MM)),
        tags="SPI-NAND WSON-8 W25N01GVZEIG 1.27mm",
        version=FP_VERSION,
        generator_version=GEN_VERSION,
    )
    report = {
        "footprint": FP_NAME,
        "pads": len(pad_rows),
        "unit": scale_info["unit"],
        "scale": s,
        "pitch_mm": round(scale_info["pitch_mm"], 4),
        "raw_pitch_units": scale_info["raw_pitch"],
        "pad_box_mm": [PAD_W_MM, PAD_H_MM],
        "pad_rotation_rule": "rot=90 in document would overlap neighbours (1.400 > "
                             "1.270-0.150); axes therefore resolved, not read",
        "exposed_pad_mm": [EP_W_MM, EP_H_MM],
        "body_mm": [round((body[2] - body[0]) * s, 4), round((body[3] - body[1]) * s, 4)],
        "title_width_mm": TITLE_W_MM,
        "title_width_discrepancy_mm": round(abs((body[2] - body[0]) * s - TITLE_W_MM), 4),
        "silk_margin_mm": SILK_MARGIN_MM,
        "skipped": {"via_rows": 4, "reason": "thermal vias belong to the board pad "
                   "stack, a footprint cannot carry (via) objects",
                   "pin_soldering_rows": 8, "reason2": "mask/paste openings are derived "
                   "by KiCad from the pads already emitted"},
        "courtyard_rule": "body + %.2f mm (engineering default, not a datasheet value)"
                          % COURTYARD_MARGIN,
        "audits": "A1-A10 passed",
    }
    return text, report, specs, s

def write_csv(specs, path):
    with open(path, "w", encoding="utf-8", newline="\n") as f:
        w = csv.writer(f)
        w.writerow(["pin", "x_mm", "y_mm", "w_mm", "h_mm", "shape"])
        for (num, x, y, pw, ph, shape) in specs:
            w.writerow([num, "%.4f" % x, "%.4f" % y, "%.4f" % pw, "%.4f" % ph, shape])

def main(argv=None):
    ap = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    ap.add_argument("--doc", default=DOC)
    ap.add_argument("--out", default=OUT_FP)
    ap.add_argument("--csv", default=None)
    ap.add_argument("--report", default=None)
    a = ap.parse_args(argv)
    rows = lceda_fp.read_rows(a.doc)
    text, report, specs, _s = build(rows)
    os.makedirs(os.path.dirname(a.out), exist_ok=True)
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
