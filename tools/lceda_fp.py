#!/usr/bin/env python3
"""Shared transcriber: LCEDA footprint document -> audited KiCad footprint.

Nothing here is a route or a placement decision. It reads someone else's
library document (already decrypted by ``tools/fetch_lceda_part.py``), solves
the document's unit against a datasheet dimension, and writes one
``.kicad_mod`` after every audit passes.

Three things this file refuses to do
------------------------------------
1. **Trust the CANVAS unit.** Measured twice: both the RK3506J and the DDR3L
   documents declare ``"mm"`` while their coordinates are mil (0.0254 mm).
   Read ``solve_scale`` -- the scale is *solved* against expected spans, and a
   document that matches zero or two candidate units is rejected, never guessed.
2. **Invent geometry.** Pads come from ``PAD`` rows, the body from a closed
   polyline on the component-shape layer, silkscreen marks from the silk layer.
   A missing body is an error, not something to synthesise.
3. **Write a partial file.** Every audit runs before the caller opens the
   output path.

Document format (measured on both documents)
--------------------------------------------
One JSON array per line. ``row[0]`` is the type. For graphics rows the layer
is ``row[4]``, the line width ``row[5]``, and the geometry ``row[6]``
(``FILL`` puts its geometry at ``row[7]``). Geometry is either
``["CIRCLE", cx, cy, r]`` or a flat point list ``[x0, y0, "L", x1, y1, ...]``.
``PAD`` rows: ``[4]`` layer, ``[5]`` number, ``[6]`` x, ``[7]`` y,
``[10]`` = ``["ELLIPSE", w, h]``.
"""
from __future__ import annotations

import json
from decimal import Decimal

MIL_MM = 0.0254
SCALE_CANDIDATES = (("mil", 0.0254), ("mm", 1.0))


class GeometryError(RuntimeError):
    pass


# ---------------------------------------------------------------- parsing

def read_rows(path):
    rows = []
    with open(path, "r", encoding="utf-8") as f:
        for line in f:
            line = line.strip()
            if not line:
                continue
            try:
                rows.append(json.loads(line))
            except Exception as exc:              # noqa: BLE001
                raise GeometryError("line is not JSON (%s): %r" % (exc, line[:60]))
    return rows


def _geometry(row):
    for idx in (6, 7):
        if len(row) > idx and isinstance(row[idx], list):
            return row[idx]
    return None


def _points(g, out):
    """Collect every numeric coordinate pair from one geometry item."""
    if not isinstance(g, list):
        return
    if g and isinstance(g[0], str):
        if g[0] == "CIRCLE" and len(g) >= 4:
            cx, cy, r = float(g[1]), float(g[2]), float(g[3])
            out.append((cx, cy, r))
        return
    for item in g:
        if isinstance(item, list):
            _points(item, out)          # FILL rows wrap geometry in one more list
    # flat point list: numbers interleaved with "L"/"ARC" markers
    nums = [v for v in g if isinstance(v, (int, float))]
    for i in range(0, len(nums) - 1, 2):
        out.append((float(nums[i]), float(nums[i + 1]), 0.0))


def layer_bbox(rows, layer):
    """bbox (x0, y0, x1, y1) of everything drawn on ``layer``; None if empty."""
    lo = [None, None]
    hi = [None, None]
    for row in rows:
        if not isinstance(row, list) or len(row) < 6:
            continue
        if row[0] not in ("POLY", "FILL", "CIRCLE", "SOLID"):
            continue
        if row[4] != layer:
            continue
        pts = []
        _points(_geometry(row), pts)
        for px, py, r in pts:
            for xv in (px - r, px + r):
                lo[0] = xv if lo[0] is None else min(lo[0], xv)
                hi[0] = xv if hi[0] is None else max(hi[0], xv)
            for yv in (py - r, py + r):
                lo[1] = yv if lo[1] is None else min(lo[1], yv)
                hi[1] = yv if hi[1] is None else max(hi[1], yv)
    if lo[0] is None:
        return None
    return lo[0], lo[1], hi[0], hi[1]


def circle_marks(rows, layer):
    """Every explicit CIRCLE on ``layer``: [(cx, cy, r), ...]."""
    out = []
    for row in rows:
        if not isinstance(row, list) or len(row) < 6 or row[4] != layer:
            continue
        pts = []
        _points(_geometry(row), pts)
        for px, py, r in pts:
            if r:
                out.append((px, py, r))
    return out


def pads(rows):
    """[(number, x, y, w, h), ...] from PAD rows."""
    out = []
    for row in rows:
        if not isinstance(row, list) or not row or row[0] != "PAD":
            continue
        shape = row[10] if len(row) > 10 else None
        if not isinstance(shape, list) or not shape or shape[0] != "ELLIPSE":
            raise GeometryError("pad %r is not ELLIPSE: %r" % (row[5:8], shape))
        out.append((str(row[5]), float(row[6]), float(row[7]),
                    float(shape[1]), float(shape[2])))
    return out


LAYER_SHAPE = 48          # COMPONENT_SHAPE
LAYER_SILK = 3            # TOP_SILK
LAYER_DOCUMENT = 13       # DOCUMENT / assembly
LAYER_MARKING = 49        # COMPONENT_MARKING (pin-1 dot)

def body_bbox(rows, layer=LAYER_SHAPE):
    """Closed body outline bbox on the component-shape layer; error if absent."""
    bb = layer_bbox(rows, layer)
    if bb is None:
        raise GeometryError("no body outline on layer %d" % layer)
    return bb

def silk_segments_mm(rows, scale, layer=LAYER_SILK):
    """Silk polylines as millimetre segments with KiCad's y already flipped."""
    segs = []
    for row in rows:
        if not isinstance(row, list) or len(row) < 6 or row[4] != layer:
            continue
        g = _geometry(row)
        if not isinstance(g, list) or not g or isinstance(g[0], str):
            continue
        nums = [v for v in g if isinstance(v, (int, float))]
        pts = [(nums[i] * scale, -nums[i + 1] * scale) for i in range(0, len(nums) - 1, 2)]
        for (x1, y1), (x2, y2) in zip(pts, pts[1:]):
            if (x1, y1) != (x2, y2):
                segs.append((x1, y1, x2, y2))
    return segs

def circle_marks_mm(rows, scale, layers=(LAYER_MARKING, LAYER_DOCUMENT)):
    """Explicit circles (pin-1 dots) in millimetres, marking layer first."""
    out = []
    for layer in layers:
        for (cx, cy, r) in circle_marks(rows, layer):
            out.append((cx * scale, -cy * scale, r * scale))
    return out

def min_grid_gap(pad_rows):
    """Smallest positive pad-to-pad gap sharing a row or a column (raw units).

    This is the grid step the land pattern was drawn on. It is reported, not
    used as a ruler: with rounding in the library title (P2.20 drawn as 2.15)
    a strict equality test would be a false alarm generator.
    """
    best = None
    for i, a in enumerate(pad_rows):
        for b in pad_rows[i + 1:]:
            for axis in ("x", "y"):
                other = "y" if axis == "x" else "x"
                if abs(a[other] - b[other]) > 1e-9:
                    continue
                d = abs(a[axis] - b[axis])
                if d > 1e-9:
                    best = d if best is None else min(best, d)
    return best

def pad_rows(rows):
    """Full PAD records, shape-agnostic; see ``resolve_pad_box`` for rotation.

    ``row[10]`` is the shape: ``["ELLIPSE", w, h]`` for round, ``["OVAL", w, h]``
    for obround, ``["RECT", w, h, r]`` for rectangular. Measured on three
    documents: index 5 = number, 6 = x, 7 = y, 10 = shape, 14 = rotation
    (index 13 is a small per-pad offset, not the rotation). The rotation flag is
    a *hypothesis*, not a fact -- see ``resolve_pad_box``.
    """
    out = []
    for row in rows:
        if not isinstance(row, list) or not row or row[0] != "PAD":
            continue
        shape = row[10] if len(row) > 10 else None
        if not isinstance(shape, list) or len(shape) < 3:
            raise GeometryError("pad %r has no usable shape: %r" % (row[5:8], shape))
        rot = row[14] if len(row) > 14 and isinstance(row[14], (int, float)) else 0.0
        out.append({"num": str(row[5]), "x": float(row[6]), "y": float(row[7]),
                    "shape": str(shape[0]), "w": float(shape[1]),
                    "h": float(shape[2]), "rot": float(rot)})
    return out

def resolve_pad_box(pad, scale, pitch_mm, gap_min=0.15):
    """Resolve ``(w_x, h_y)`` in mm for one pad on a pitch row.

    A 90/270 flag is the only ambiguity that can change the result: it swaps the
    two axes. The document is not trusted -- the assignment whose pitch-axis
    width would leave less than ``gap_min`` to a neighbour at ``pitch_mm`` is
    rejected; if both survive, the smaller along-pitch extent wins (see the
    KiCad library precedent in the body). Nothing is guessed: zero survivors is
    a ``GeometryError``.
    """
    cands = [(pad["w"] * scale, pad["h"] * scale)]
    if abs(pad["rot"]) % 180 == 90:
        cands.append((pad["h"] * scale, pad["w"] * scale))
    ok = [c for c in cands if c[0] <= pitch_mm - gap_min + 1e-9]
    if len(ok) > 1:
        # Tie-break, measured against a ruler outside this repository: KiCad's
        # own ``Crystal_SMD_2012-2Pin_2.0x1.2mm`` puts a 0.6 x 1.1 mm land on a
        # 1.4 mm pitch, i.e. the land is narrow along the pitch axis and long
        # across the body. Two-terminal chip parts are authored that way, so the
        # assignment with the smaller along-pitch extent wins; a true tie means
        # both boxes are identical and either is the same geometry.
        ok = [min(ok, key=lambda c: (c[0], -c[1]))]
    if len(ok) != 1:
        raise GeometryError(
            "pad %s box is not resolvable at pitch %.3f mm: %d of %d axis "
            "assignments fit (candidates %s)"
            % (pad["num"], pitch_mm, len(ok), len(cands),
               ["%.4f x %.4f" % c for c in cands]))
    out = {"w_mm": ok[0][0], "h_mm": ok[0][1],
           "rejected": [c for c in cands if c != ok[0]]}
    return out

# ---------------------------------------------------------------- scale

def solve_scale(doc_pads, expect_dx_mm, expect_dy_mm, tol=0.005):
    """Pick the unit whose span matches the expected millimetres exactly once."""
    xs = [p[1] for p in doc_pads]
    ys = [p[2] for p in doc_pads]
    dx = max(xs) - min(xs)
    dy = max(ys) - min(ys)
    hits = []
    for unit, scale in SCALE_CANDIDATES:
        if abs(dx * scale - expect_dx_mm) <= tol and abs(dy * scale - expect_dy_mm) <= tol:
            hits.append({"unit": unit, "scale": scale,
                         "dx_mm": dx * scale, "dy_mm": dy * scale})
    if len(hits) != 1:
        raise GeometryError(
            "scale is not solvable: expected %.4f x %.4f mm, measured spans "
            "%.3f x %.3f raw units; %d candidate units matched %s"
            % (expect_dx_mm, expect_dy_mm, dx, dy, len(hits), [h["unit"] for h in hits]))
    out = dict(hits[0])
    out["raw_dx"] = dx
    out["raw_dy"] = dy
    return out


def nearest_pitches(doc_pads, scale, tol=0.001):
    """Sorted unique nearest-neighbour distances (mm). Two parts, one array."""
    pts = [(p[1] * scale, p[2] * scale) for p in doc_pads]
    seen = []
    for i, (x0, y0) in enumerate(pts):
        best = None
        for j, (x1, y1) in enumerate(pts):
            if i == j:
                continue
            d = ((x1 - x0) ** 2 + (y1 - y0) ** 2) ** 0.5
            if best is None or d < best:
                best = d
        seen.append(round(best, 3))
    uniq = []
    for v in sorted(seen):
        if not uniq or v - uniq[-1] > tol:
            uniq.append(v)
    return uniq


# ---------------------------------------------------------------- emitting

def _f(x):
    """Four decimals, negative zero folded to zero."""
    d = Decimal(repr(float(x))).quantize(Decimal("0.0001"))
    if d == 0:
        d = Decimal("0.0000")
    return str(d)


def _xy(x, y):
    return "(%s %s)" % (_f(x), _f(y))


def _pair(x, y):
    """KiCad wants bare numbers for at/start/end/center, not a parenthesised pair."""
    return "%s %s" % (_f(x), _f(y))


def _prop(name, value, x, y, layer, hide=False, justify=None, size="1.27"):
    eff = ("      (effects\n        (font\n          (size %s %s)\n"
           "          (thickness 0.15)\n%s        )\n      )\n"
           % (size, size,
              "          (justify %s)\n" % justify
              if justify and justify != "center" else ""))
    if hide:
        eff = eff.replace("      (effects", "      (effects\n        (hide yes)", 1)
    return ('    (property "%s" "%s"\n      (at %s 0)\n      (layer "%s")\n%s    )\n'
            % (name, value, _pair(x, y), layer, eff))


SOURCE_NOTE = ("Geometry from 嘉立创EDA/EasyEDA官方库 official library "
               "(https://lceda.cn/ , https://easyeda.com/)")

def with_source(descr):
    """Append the mandatory source declaration the LCEDA licence demands.

    The document HEAD carries a licence that *requires* every output to name
    "嘉立创EDA/EasyEDA官方库" and link both home pages. A transcription without
    it is a licence breach, so this is applied at the single point where the
    description is written, not left to each caller. Idempotent: a description
    that already declares the source is returned unchanged, so re-running a
    generator cannot stack the note twice.
    """
    d = " ".join((descr or "").split())
    if "EasyEDA" in d or "嘉立创" in d:
        return d
    return (d + " | " + SOURCE_NOTE) if d else SOURCE_NOTE

def emit_footprint(name, scale_info, doc_pads, body_mm, silk_segments, pin1_marks,
                   pad_mm, courtyard_margin=0.25, descr="", tags="", version="20260206",
                   generator_version="0.0.0", silk_width=0.15, courtyard_width=0.05,
                   fab_layer="F.Fab", silk_layer="F.SilkS", courtyard_layer="F.CrtYd"):
    """Return the .kicad_mod text. ``body_mm`` = (width, height), centred on 0,0."""
    descr = with_source(descr)
    s = scale_info["scale"]
    w, h = body_mm
    hw, hh = w / 2.0, h / 2.0
    L = []
    L.append("(footprint \"%s\"" % name)
    L.append("  (version %s)" % version)
    L.append("  (generator \"pcb_agent_mcp\")")
    L.append("  (generator_version \"%s\")" % generator_version)
    L.append("  (layer \"F.Cu\")")
    L.append("  (descr \"%s\")" % descr.replace('"', "'"))
    L.append("  (tags \"%s\")" % tags.replace('"', "'"))
    L.append("  (attr smd)")
    L.append("  (duplicate_pad_numbers_are_jumpers no)")
    L.append(_prop("Reference", "U**", 0, -(hh + 1.0), silk_layer, justify="center").rstrip("\n"))
    L.append(_prop("Value", name, 0, hh + 1.0, fab_layer, justify="center").rstrip("\n"))
    L.append(_prop("Datasheet", "", 0, 0, fab_layer, hide=True).rstrip("\n"))
    L.append(_prop("Description", descr, 0, 0, fab_layer, hide=True).rstrip("\n"))
    # body on F.Fab
    for (x1, y1, x2, y2) in ((-hw, -hh, hw, -hh), (hw, -hh, hw, hh),
                             (hw, hh, -hw, hh), (-hw, hh, -hw, -hh)):
        L.append("  (fp_line\n    (start %s)\n    (end %s)\n    (stroke (width %s) (type solid))\n"
                 "    (layer \"%s\")\n  )" % (_pair(x1, y1), _pair(x2, y2), _f(0.1), fab_layer))
    # silkscreen: transcribed segments (already in mm, absolute document coords)
    for (x1, y1, x2, y2) in silk_segments:
        L.append("  (fp_line\n    (start %s)\n    (end %s)\n    (stroke (width %s) (type solid))\n"
                 "    (layer \"%s\")\n  )" % (_pair(x1, y1), _pair(x2, y2), _f(silk_width), silk_layer))
    # courtyard
    cw, ch = hw + courtyard_margin, hh + courtyard_margin
    for (x1, y1, x2, y2) in ((-cw, -ch, cw, -ch), (cw, -ch, cw, ch),
                             (cw, ch, -cw, ch), (-cw, ch, -cw, -ch)):
        L.append("  (fp_line\n    (start %s)\n    (end %s)\n    (stroke (width %s) (type solid))\n"
                 "    (layer \"%s\")\n  )" % (_pair(x1, y1), _pair(x2, y2),
                                        _f(courtyard_width), courtyard_layer))
    for (cx, cy, r) in pin1_marks:
        L.append("  (fp_circle\n    (center %s)\n    (end %s)\n    (stroke (width %s) (type solid))\n"
                 "    (fill no)\n    (layer \"%s\")\n  )"
                 % (_pair(cx, cy), _pair(cx + max(r, 0.05), cy), _f(silk_width), silk_layer))
    for (num, px, py, pw, ph) in doc_pads:
        x = px * s
        y = -py * s                     # EasyEDA +Y up, KiCad +Y down
        dia = pad_mm if pad_mm is not None else max(pw, ph) * s
        if abs((pw - ph) * s) > 1e-6:
            raise GeometryError("pad %s is not round: %.4f x %.4f" % (num, pw, ph))
        L.append('  (pad "%s" smd circle\n    (at %s)\n    (size %s %s)\n'
                 '    (layers "F.Cu" "F.Paste" "F.Mask")\n  )'
                 % (num, _pair(x, y), _f(dia), _f(dia)))
    L.append(")")
    return "\n".join(L) + "\n"

def emit_generic(name, pad_specs, body_mm, silk_segments, pin1_marks,
                 courtyard_margin=0.25, descr="", tags="", version="20260206",
                 generator_version="0.0.0", silk_width=0.15, courtyard_width=0.05,
                 roundrect_ratio=0.25, fab_layer="F.Fab", silk_layer="F.SilkS",
                 courtyard_layer="F.CrtYd"):
    """Return ``.kicad_mod`` text for non-circular pads too.

    ``pad_specs`` = [(number, x_mm, y_mm, w_mm, h_mm, shape), ...] with shape in
    {"circle", "oval", "rect"}. Coordinates are already in KiCad millimetres and
    already y-flipped by the caller; this function invents nothing. Unlike
    ``emit_footprint`` (circular pads gathered from ``pads()``), nothing here
    assumes a round pad or a single pad size.
    """
    descr = with_source(descr)
    w, h = body_mm
    hw, hh = w / 2.0, h / 2.0
    L = []
    L.append('(footprint "%s"' % name)
    L.append("  (version %s)" % version)
    L.append('  (generator "pcb_agent_mcp")')
    L.append('  (generator_version "%s")' % generator_version)
    L.append('  (layer "F.Cu")')
    L.append('  (descr "%s")' % descr.replace('"', "'"))
    L.append('  (tags "%s")' % tags.replace('"', "'"))
    L.append("  (attr smd)")
    L.append("  (duplicate_pad_numbers_are_jumpers no)")
    L.append(_prop("Reference", "U**", 0, -(hh + 1.0), silk_layer, justify="center").rstrip("\n"))
    L.append(_prop("Value", name, 0, hh + 1.0, fab_layer, justify="center").rstrip("\n"))
    L.append(_prop("Datasheet", "", 0, 0, fab_layer, hide=True).rstrip("\n"))
    L.append(_prop("Description", descr, 0, 0, fab_layer, hide=True).rstrip("\n"))
    for (x1, y1, x2, y2) in ((-hw, -hh, hw, -hh), (hw, -hh, hw, hh),
                             (hw, hh, -hw, hh), (-hw, hh, -hw, -hh)):
        L.append("  (fp_line\n    (start %s)\n    (end %s)\n    (stroke (width %s) (type solid))\n"
                 "    (layer \"%s\")\n  )" % (_pair(x1, y1), _pair(x2, y2), _f(0.1), fab_layer))
    for (x1, y1, x2, y2) in silk_segments:
        L.append("  (fp_line\n    (start %s)\n    (end %s)\n    (stroke (width %s) (type solid))\n"
                 "    (layer \"%s\")\n  )" % (_pair(x1, y1), _pair(x2, y2), _f(silk_width), silk_layer))
    cw, ch = hw + courtyard_margin, hh + courtyard_margin
    for (x1, y1, x2, y2) in ((-cw, -ch, cw, -ch), (cw, -ch, cw, ch),
                             (cw, ch, -cw, ch), (-cw, ch, -cw, -ch)):
        L.append("  (fp_line\n    (start %s)\n    (end %s)\n    (stroke (width %s) (type solid))\n"
                 "    (layer \"%s\")\n  )" % (_pair(x1, y1), _pair(x2, y2),
                                        _f(courtyard_width), courtyard_layer))
    for (cx, cy, r) in pin1_marks:
        L.append("  (fp_circle\n    (center %s)\n    (end %s)\n    (stroke (width %s) (type solid))\n"
                 "    (fill no)\n    (layer \"%s\")\n  )"
                 % (_pair(cx, cy), _pair(cx + max(r, 0.05), cy), _f(silk_width), silk_layer))
    for (num, x, y, pw, ph, shape) in pad_specs:
        if shape == "circle":
            if abs(pw - ph) > 1e-6:
                raise GeometryError("pad %s is called circle but is %.4f x %.4f" % (num, pw, ph))
            tail = "circle\n    (at %s)\n    (size %s %s)" % (_pair(x, y), _f(pw), _f(pw))
        elif shape == "rect":
            tail = "rect\n    (at %s)\n    (size %s %s)" % (_pair(x, y), _f(pw), _f(ph))
        elif shape in ("oval", "roundrect"):
            kind = "roundrect" if shape == "roundrect" else "oval"
            tail = "%s\n    (at %s)\n    (size %s %s)" % (kind, _pair(x, y), _f(pw), _f(ph))
            if kind == "roundrect":
                tail += "\n    (roundrect_rratio %s)" % _f(roundrect_ratio)
        else:
            raise GeometryError("pad %s has unsupported shape %r" % (num, shape))
        L.append('  (pad "%s" smd %s\n    (layers "F.Cu" "F.Paste" "F.Mask")\n  )' % (num, tail))
    L.append(")")
    return "\n".join(L) + "\n"
