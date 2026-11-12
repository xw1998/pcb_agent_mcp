#!/usr/bin/env python3
"""Generate the MT41K256M16 DDR3L x16 FBGA-96 footprint from the LCEDA document.

Nothing here is a route; it is a package geometry transcription.

Source document
---------------
``examples/rk3506_som/library/DDR3L_MT41K256M16HA-125E.efp`` -- decrypted by
``tools/fetch_lceda_part.py`` from component ``3cba6f1855a54f9b86422721c15414e5``
(the LCSC C248057 footprint for MT41K256M16HA-125:E, package FBGA-96(9x14)).
Provenance (uuids, key, iv, title, copyright) is in the sibling
``*_source.json``. The document is the LCEDA official library's; it is kept as
a geometry source only, not redistributed as a library.

Rulers (each number is checked, not assumed)
--------------------------------------------
The document's own footprint title is ``FBGA-96_L14.0-W9.0-R16-C6-P0.80-TL``:
14.0 x 9.0 body, 16 rows, 6 columns, 0.8 mm pitch. Measured:

* body polyline on the component-shape layer: 354.33 x 551.19 raw units
  -> 9.000 / 14.000 mm at 0.0254 mm per unit
* pad span: 251.96 x 472.44 raw units -> 6.400 / 12.000 mm = 8 x 0.8 pitch
  (columns 1..9 populated at 1,2,3,7,8,9) and 15 x 0.8 (rows A..T)
* pad diameter 18 raw units -> 0.4572 mm

So the unit is **mil** even though the CANVAS row says ``"mm"`` -- the same
trap measured on the RK3506J document. ``lceda_fp.solve_scale`` is handed the
two expected spans and must match exactly one candidate unit; zero or two
matches is a hard failure with no bytes written.

Audits (A1-A8), all must pass before the file is opened
-------------------------------------------------------
A1  96 pads, numbers unique and non-empty
A2  unit solved against the span pair; pitch spectrum is exactly {0.8}
A3  pad diameter is a single value and within 0.03 mm of the 0.45 mm ball
A4  body is 9.000 x 14.000 mm (+-0.01) on the component-shape layer
A5  16 row letters A,B,C,D,E,F,G,H,J,K,L,M,N,P,R,T (KiCad convention: no I/O)
    and 6 columns {1,2,3,7,8,9}; 16 x 6 = 96 with no hole
A6  no two pads share a coordinate
A7  pad centroid is (0, 0) +-0.01 mm
A8  every pad lands inside the body outline
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
DOC = os.path.join(EX, "library", "DDR3L_MT41K256M16HA-125E.efp")
OUT_FP = os.path.join(EX, "lib", "RK3506.pretty", "Package_FBGA-96_L14.0-W9.0_MT41K256M16.kicad_mod")

FP_NAME = "Package_FBGA-96_L14.0-W9.0_MT41K256M16"
LAYER_SHAPE = 48           # COMPONENT_SHAPE
LAYER_SILK = 3             # TOP_SILK
LAYER_DOCUMENT = 13
LAYER_MARKING = 49

PITCH_MM = 0.8
BODY_W_MM = 9.0
BODY_H_MM = 14.0
COLUMNS = (1, 2, 3, 7, 8, 9)
ROW_LETTERS = tuple("ABCDEFGHJKLMNPRT")        # 16, KiCad/DDR ball-map convention
BALL_MM = 0.45
SPAN_TOL = 0.005
COURTYARD_MARGIN = 0.25
FP_VERSION = "20260206"
GEN_VERSION = "0.1.4"


def expected_spans():
    dx = (max(COLUMNS) - min(COLUMNS)) * PITCH_MM
    dy = (len(ROW_LETTERS) - 1) * PITCH_MM
    return dx, dy


def doc_body(rows):
    bb = lceda_fp.layer_bbox(rows, LAYER_SHAPE)
    if bb is None:
        raise lceda_fp.GeometryError("no body outline on layer %d" % LAYER_SHAPE)
    return bb


def silk_segments_mm(rows, scale):
    """Transcribe silk polylines as mm segments, one per consecutive pair."""
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
    for layer in (LAYER_DOCUMENT, LAYER_MARKING):
        for (cx, cy, r) in lceda_fp.circle_marks(rows, layer):
            out.append((cx * scale, -cy * scale, r * scale))
    return out


def audit(rows, pads, scale_info, body):
    errs = []
    s = scale_info["scale"]

    if len(pads) != 96:
        errs.append("A1 pad count %d, expected 96" % len(pads))
    nums = [p[0] for p in pads]
    if len(set(nums)) != len(nums):
        errs.append("A1 duplicate pad numbers: %s" % sorted({n for n in nums if nums.count(n) > 1}))
    if any(not n for n in nums):
        errs.append("A1 empty pad number")

    dx, dy = expected_spans()
    if scale_info["unit"] != "mil":
        errs.append("A2 unit solved as %r (expected mil)" % scale_info["unit"])
    if abs(scale_info["dx_mm"] - dx) > SPAN_TOL or abs(scale_info["dy_mm"] - dy) > SPAN_TOL:
        errs.append("A2 span %.4f x %.4f mm, expected %.4f x %.4f"
                    % (scale_info["dx_mm"], scale_info["dy_mm"], dx, dy))
    pitches = lceda_fp.nearest_pitches(pads, s)
    if len(pitches) != 1 or abs(pitches[0] - PITCH_MM) > 0.003:
        errs.append("A2 pitch spectrum %s, expected only %.3f" % (pitches, PITCH_MM))

    dias = {round(p[3] * s, 4) for p in pads} | {round(p[4] * s, 4) for p in pads}
    if len(dias) != 1:
        errs.append("A3 pad diameter is not a single value: %s" % sorted(dias))
    else:
        d = dias.pop()
        if abs(d - BALL_MM) > 0.03:
            errs.append("A3 pad %.4f mm is not within 0.03 of the %.2f mm ball" % (d, BALL_MM))

    bw = (body[2] - body[0]) * s
    bh = (body[3] - body[1]) * s
    if abs(bw - BODY_W_MM) > 0.01 or abs(bh - BODY_H_MM) > 0.01:
        errs.append("A4 body %.4f x %.4f mm, expected %.2f x %.2f" % (bh, bw, BODY_H_MM, BODY_W_MM))

    letters = sorted({n[0] for n in nums})
    if tuple(letters) != ROW_LETTERS:
        errs.append("A5 row letters %s, expected %s" % (letters, list(ROW_LETTERS)))
    cols = sorted({int(n[1:]) for n in nums})
    if tuple(cols) != COLUMNS:
        errs.append("A5 columns %s, expected %s" % (cols, list(COLUMNS)))
    if len(nums) != len(ROW_LETTERS) * len(COLUMNS):
        errs.append("A5 %d pads, expected %d x %d" % (len(nums), len(ROW_LETTERS), len(COLUMNS)))

    coords = [(round(p[1] * s, 3), round(p[2] * s, 3)) for p in pads]
    if len(set(coords)) != len(coords):
        errs.append("A6 duplicate coordinates")

    cx = sum(c[0] for c in coords) / len(coords)
    cy = sum(c[1] for c in coords) / len(coords)
    if abs(cx) > 0.01 or abs(cy) > 0.01:
        errs.append("A7 pad centroid (%.4f, %.4f), expected (0, 0)" % (cx, cy))

    for name, x, y in ((p[0], p[1] * s, p[2] * s) for p in pads):
        # the array is 6.4 wide on the 9.0 side (x) and 12.0 tall on the 14.0 side
        if abs(x) > body_w_limit() or abs(y) > body_h_limit():
            errs.append("A8 pad %s at (%.4f, %.4f) leaves the body" % (name, x, y))
    return errs


def body_w_limit():
    return BODY_W_MM / 2


def body_h_limit():
    return BODY_H_MM / 2


def build(rows):
    pads = lceda_fp.pads(rows)
    dx, dy = expected_spans()
    scale_info = lceda_fp.solve_scale(pads, dx, dy, tol=SPAN_TOL)
    body = doc_body(rows)
    errs = audit(rows, pads, scale_info, body)
    if errs:
        raise SystemExit("audit failed:\n  " + "\n  ".join(errs))
    s = scale_info["scale"]
    text = lceda_fp.emit_footprint(
        name=FP_NAME,
        scale_info=scale_info,
        doc_pads=pads,
        body_mm=(BODY_W_MM, BODY_H_MM),
        silk_segments=silk_segments_mm(rows, s),
        pin1_marks=pin1_marks_mm(rows, s)[:1],
        pad_mm=round(pads[0][3] * s, 4),
        courtyard_margin=COURTYARD_MARGIN,
        descr=("MT41K256M16 DDR3L 4Gb x16, FBGA-96 9.00x14.00 mm, pitch 0.800 mm, "
               "pad %.4f mm transcribed from LCEDA C248057; unit solved against the "
               "0.8 mm pitch and the 9x14 body, not read from CANVAS" % (pads[0][3] * s)),
        tags="DDR3L FBGA-96 MT41K256M16 0.8mm",
        version=FP_VERSION,
        generator_version=GEN_VERSION,
    )
    report = {
        "footprint": FP_NAME,
        "pads": len(pads),
        "unit": scale_info["unit"],
        "scale": s,
        "span_mm": [round(scale_info["dx_mm"], 4), round(scale_info["dy_mm"], 4)],
        "pitch_mm": lceda_fp.nearest_pitches(pads, s),
        "pad_mm": round(pads[0][3] * s, 4),
        "body_mm": [round((body[2] - body[0]) * s, 4), round((body[3] - body[1]) * s, 4)],
        "rows": list(ROW_LETTERS),
        "columns": list(COLUMNS),
        "courtyard_rule": "body + %.2f mm (engineering default, not a datasheet value)"
                          % COURTYARD_MARGIN,
        "unit_rule": "mil solved from span pair; CANVAS declares 'mm' and is wrong",
        "audits": "A1-A8 passed",
    }
    return text, report, pads, s


def write_csv(pads, s, path):
    with open(path, "w", encoding="utf-8", newline="\n") as f:
        w = csv.writer(f)
        w.writerow(["ball", "x_mm", "y_mm", "pad_mm", "shape"])
        for (num, px, py, pw, ph) in sorted(pads):
            w.writerow([num, "%.4f" % (px * s), "%.4f" % (-py * s), "%.4f" % (pw * s), "circle"])


def main(argv=None):
    ap = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    ap.add_argument("--doc", default=DOC)
    ap.add_argument("--out", default=OUT_FP)
    ap.add_argument("--csv", default=None)
    ap.add_argument("--report", default=None)
    a = ap.parse_args(argv)
    rows = lceda_fp.read_rows(a.doc)
    text, report, pads, s = build(rows)
    os.makedirs(os.path.dirname(a.out), exist_ok=True)
    with open(a.out, "w", encoding="utf-8", newline="\n") as f:
        f.write(text)
    out = {"fp": a.out, "report": report}
    if a.csv:
        write_csv(pads, s, a.csv)
        out["csv"] = a.csv
    print(json.dumps(out, ensure_ascii=False, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
