# -*- coding: utf-8 -*-
"""Copper-layer snapshot: rasterise the parsed board into a PNG, stdlib only.

Why this exists: every "the routing looks wrong" conversation so far had to be
held in prose, because the tools could measure the board but not *show* it. This
module draws what the file actually contains - pour polygons, tracks, vias, pads
and the edge - so a human (or a vision model) can look at the same picture.

It is a PREVIEW, not a fab view: no solder mask, no silkscreen, no drill table,
no layer transparency. Rotation is honoured for rectangular pads; pads with a
custom polygon are drawn as their rotated bounding rectangle.
"""
import math
import os
import re
import struct
import tempfile
import zlib

from .calc.result import ok, err

BG = (18, 18, 22, 255)
COL = {
    "pour": (26, 62, 46, 255),
    "track": (198, 150, 62, 255),
    "via": (206, 206, 206, 255),
    "hole": (18, 18, 22, 255),
    "pad": (232, 196, 110, 255),
    "edge": (240, 240, 240, 255),
    # difference image: ink on both / only here / only there
    "diff_both": (96, 96, 104, 255),
    "diff_only_a": (232, 72, 72, 255),
    "diff_only_b": (86, 148, 232, 255),
}


def _png(path, w, h, pix):
    stride = w * 4
    raw = bytearray()
    for y in range(h):
        raw.append(0)
        raw += pix[y * stride:(y + 1) * stride]

    def chunk(tag, data):
        return (struct.pack(">I", len(data)) + tag + data +
                struct.pack(">I", zlib.crc32(tag + data) & 0xFFFFFFFF))

    blob = (b"\x89PNG\r\n\x1a\n" +
            chunk(b"IHDR", struct.pack(">IIBBBBB", w, h, 8, 6, 0, 0, 0)) +
            chunk(b"IDAT", zlib.compress(bytes(raw), 6)) +
            chunk(b"IEND", b""))
    with open(path, "wb") as fh:
        fh.write(blob)
    return len(blob)


def _put(pix, w, h, x, y, c):
    if 0 <= x < w and 0 <= y < h:
        i = (y * w + x) * 4
        pix[i:i + 4] = bytes(c)


def _fill_polys(pix, w, h, polys, c, tf):
    """Even-odd scanline over a list of polygons (holes survive as extra rings)."""
    edges = []
    for poly in polys:
        pts = [tf(x, y) for (x, y) in poly]
        if len(pts) < 3:
            continue
        for i in range(len(pts)):
            x0, y0 = pts[i]
            x1, y1 = pts[(i + 1) % len(pts)]
            if y0 != y1:
                edges.append((x0, y0, x1, y1))
    if not edges:
        return
    ymin = max(0, int(min(min(e[1], e[3]) for e in edges)))
    ymax = min(h - 1, int(max(max(e[1], e[3]) for e in edges)) + 1)
    for y in range(ymin, ymax + 1):
        yc = y + 0.5
        xs = []
        for (x0, y0, x1, y1) in edges:
            if (y0 <= yc < y1) or (y1 <= yc < y0):
                xs.append(x0 + (yc - y0) * (x1 - x0) / (y1 - y0))
        xs.sort()
        for i in range(0, len(xs) - 1, 2):
            a, b = int(round(xs[i])), int(round(xs[i + 1]))
            for x in range(max(0, a), min(w - 1, b) + 1):
                _put(pix, w, h, x, y, c)


def _capsule(pix, w, h, p0, p1, r, c):
    x0, y0 = p0
    x1, y1 = p1
    dx, dy = x1 - x0, y1 - y0
    ll = dx * dx + dy * dy
    bx0 = max(0, int(min(x0, x1) - r - 1))
    bx1 = min(w - 1, int(max(x0, x1) + r + 2))
    by0 = max(0, int(min(y0, y1) - r - 1))
    by1 = min(h - 1, int(max(y0, y1) + r + 2))
    r2 = r * r
    for y in range(by0, by1 + 1):
        py = y + 0.5
        for x in range(bx0, bx1 + 1):
            px = x + 0.5
            if ll <= 1e-12:
                d2 = (px - x0) ** 2 + (py - y0) ** 2
            else:
                t = ((px - x0) * dx + (py - y0) * dy) / ll
                t = 0.0 if t < 0 else (1.0 if t > 1 else t)
                qx, qy = x0 + t * dx, y0 + t * dy
                d2 = (px - qx) ** 2 + (py - qy) ** 2
            if d2 <= r2:
                _put(pix, w, h, x, y, c)


def _disc(pix, w, h, p, r, c):
    cx, cy = p
    r2 = r * r
    for y in range(max(0, int(cy - r - 1)), min(h - 1, int(cy + r + 2)) + 1):
        for x in range(max(0, int(cx - r - 1)), min(w - 1, int(cx + r + 2)) + 1):
            if (x + 0.5 - cx) ** 2 + (y + 0.5 - cy) ** 2 <= r2:
                _put(pix, w, h, x, y, c)


def _rot_rect(cx, cy, w, h, deg):
    a = math.radians(deg)
    ca, sa = math.cos(a), math.sin(a)
    out = []
    for dx, dy in ((-w / 2, -h / 2), (w / 2, -h / 2), (w / 2, h / 2), (-w / 2, h / 2)):
        out.append((cx + dx * ca - dy * sa, cy + dx * sa + dy * ca))
    return out


def _bboxes(board):
    xs, ys = [], []
    for poly in board.outline or []:
        for (x, y) in poly:
            xs.append(x)
            ys.append(y)
    if not xs:
        for t in board.tracks:
            for p in (t.get("start"), t.get("end")):
                if p:
                    xs.append(p[0])
                    ys.append(p[1])
        for v in board.vias:
            xs.append(v.get("x") or 0)
            ys.append(v.get("y") or 0)
    if not xs:
        return None
    return min(xs), min(ys), max(xs), max(ys)


def _box_from_region(region):
    """`[x0, y0, x1, y1]` in mm, view frame (Y up), or None when unusable."""
    if region is None:
        return None
    try:
        vals = [float(v) for v in region]
    except (TypeError, ValueError):
        return None
    if len(vals) != 4:
        return None
    x0, y0, x1, y1 = vals
    if x1 <= x0 or y1 <= y0:
        return None
    return (x0, y0, x1, y1)

def _canvas(board, layer, s, box, opts):
    """Draw one board model into a fixed transform. Shared by `render`/compare."""
    x0, y0, x1, y1 = box
    w = int((x1 - x0) * s) + 2
    h = int((y1 - y0) * s) + 2

    def tf(x, y):
        return ((x - x0) * s + 1, (y1 - y) * s + 1)

    pix = bytearray(bytes(BG) * (w * h))
    drawn = {"pour_polygons": 0, "tracks": 0, "vias": 0, "pads": 0, "edge_segments": 0}

    if opts.get("pour"):
        for z in board.zones:
            if z.get("keepout"):
                continue
            layers = z.get("layers") or []
            if layer not in layers:
                continue
            polys = [p for p in (z.get("filled_polygons") or []) if len(p) >= 3]
            if polys:
                _fill_polys(pix, w, h, polys, COL["pour"], tf)
                drawn["pour_polygons"] += len(polys)

    if opts.get("track"):
        for t in board.tracks:
            if t.get("layer") != layer:
                continue
            st, en, wd = t.get("start"), t.get("end"), t.get("width") or 0
            if not st or not en:
                continue
            r = max(0.35, (wd * s) / 2.0)
            _capsule(pix, w, h, tf(*st), tf(*en), r, COL["track"])
            drawn["tracks"] += 1

    if opts.get("pad"):
        for fp in board.footprints:
            for pd in fp.get("pads") or []:
                layers = pd.get("layers") or []
                if layer not in layers:
                    continue
                cx = pd.get("x")
                cy = pd.get("y")
                if cx is None or cy is None:
                    continue
                pw = (pd.get("w") or 0) * s
                ph = (pd.get("h") or 0) * s
                if pw <= 0 or ph <= 0:
                    continue
                px, py = tf(cx, cy)
                if pd.get("shape") == "circle":
                    _disc(pix, w, h, (px, py), max(pw, ph) / 2.0, COL["pad"])
                else:
                    _fill_polys(pix, w, h,
                                [_rot_rect(px, py, pw, ph, pd.get("rotation") or 0)],
                                COL["pad"], lambda a, b: (a, b))
                drawn["pads"] += 1

    if opts.get("via"):
        for v in board.vias:
            fl, tl = v.get("from"), v.get("to")
            if fl and tl and layer not in (fl, tl):
                continue
            cx, cy = v.get("x"), v.get("y")
            if cx is None:
                continue
            px, py = tf(cx, cy)
            r = max(1.5, ((v.get("diameter") or 0.6) * s) / 2.0)
            _disc(pix, w, h, (px, py), r, COL["via"])
            dr = max(0.8, ((v.get("drill") or 0.3) * s) / 2.0)
            _disc(pix, w, h, (px, py), dr, COL["hole"])
            drawn["vias"] += 1

    if opts.get("edge"):
        chains = list(board.outline or []) + list(board.cutouts or []) + \
                 list(board.outline_open or [])
        for ch in chains:
            for i in range(len(ch) - 1):
                _capsule(pix, w, h, tf(*ch[i]), tf(*ch[i + 1]), 1.2, COL["edge"])
                drawn["edge_segments"] += 1

    return pix, w, h, drawn, tf

def _ink(pix):
    """Which pixels carry anything that is not background."""
    n = len(pix) // 4
    bg = bytes(BG)
    out = bytearray(n)
    for i in range(n):
        j = i * 4
        if pix[j:j + 4] != bg:
            out[i] = 1
    return out

def _load_compare(compare):
    """A board model from an already-parsed model or from a path.

    The canonical model is an OBJECT (`adapters.canonical.Board`), not a dict, so
    "is this a board" is `hasattr(tracks)`, not a type check on one shape.
    """
    if not isinstance(compare, str):
        return compare
    from .adapters import open_board
    return open_board(str(compare))

def render(board, layer="F.Cu", px_per_mm=12.0, out=None,
           pour=True, track=True, via=True, pad=True, edge=True,
           region=None, compare=None, compare_layer=None):
    """Draw one copper layer, or the DIFFERENCE between two boards.

    ``region`` crops the canvas to `[x0, y0, x1, y1]` in mm, in the SAME frame the
    rest of this server reports (`board_components`'s `y_mm`: the file's Y
    negated, so a board drawn at file y 0..40 spans y -40..0 here), instead of
    the whole board. Asking "what is going on around U7" otherwise
    means reading a 300 mm board at 3 px/mm, which shows nothing: at 40 px/mm on
    a 30 mm window it shows the copper.

    ``compare`` (a board path, or an already-parsed model) draws the same region
    of both boards through the same transform and returns a difference image:
    grey is copper both boards have, red is only on THIS board, blue is only on
    the other. The counts are in the answer, so "did that edit do what I meant"
    is a number as well as a picture - a pixel-level difference of the same
    drawing model, not a claim that the boards are electrically equivalent.
    """
    opts = {"pour": pour, "track": track, "via": via, "pad": pad, "edge": edge}
    other = None
    if compare is not None:
        try:
            other = _load_compare(compare)
        except Exception as exc:  # noqa: BLE001
            return err("the comparison board could not be read: %s: %s"
                       % (type(exc).__name__, exc), "PARSE_FAILED")
        if not hasattr(other, "tracks") or not hasattr(other, "zones"):
            return err("the comparison board parsed into something that is not a "
                       "board model", "PARSE_FAILED")

    box = _box_from_region(region)
    if box is None and region is not None:
        return err("region must be [x0, y0, x1, y1] with x1 > x0 and y1 > y0 "
                   "(mm, Y up)", "ARG_INVALID")
    if box is None:
        box = _bboxes(board)
        if other is not None:
            ob = _bboxes(other)
            if ob is not None:
                box = ob if box is None else (min(box[0], ob[0]), min(box[1], ob[1]),
                                              max(box[2], ob[2]), max(box[3], ob[3]))
    if not box:
        return err("the board model has no geometry to draw", "NO_GEOMETRY")
    x0, y0, x1, y1 = box
    s = float(px_per_mm)
    if s <= 0:
        return err("px_per_mm must be positive", "ARG_RANGE")
    # A 300 mm board at 40 px/mm is 12000 px wide: cap the canvas, never OOM a call.
    cap = 4000
    w = int((x1 - x0) * s) + 2
    h = int((y1 - y0) * s) + 2
    if max(w, h) > cap:
        s *= cap / float(max(w, h))

    pix, w, h, drawn, _tf = _canvas(board, layer, s, box, opts)

    value = {"png": None, "width_px": w, "height_px": h,
             "px_per_mm": round(s, 3), "layer": layer,
             "drawn": drawn,
             "bbox_mm": [round(x0, 3), round(y0, 3), round(x1, 3), round(y1, 3)]}
    if region is not None:
        value["region_mm"] = [round(v, 3) for v in box]

    if other is not None:
        olayer = compare_layer or layer
        opix, ow, oh, odrawn, _otf = _canvas(other, olayer, s, box, opts)
        a_ink = _ink(pix)
        b_ink = _ink(opix)
        diff = bytearray(bytes(BG) * (w * h))
        both = only_a = only_b = 0
        changed_bbox = None
        for i in range(w * h):
            a, b = a_ink[i], b_ink[i]
            if not a and not b:
                continue
            j = i * 4
            if a and b:
                both += 1
                c = COL["diff_both"]
            elif a:
                only_a += 1
                c = COL["diff_only_a"]
            else:
                only_b += 1
                c = COL["diff_only_b"]
            diff[j:j + 4] = bytes(c)
            if not (a and b):
                x = i % w
                y = i // w
                if changed_bbox is None:
                    changed_bbox = [x, y, x, y]
                else:
                    changed_bbox[0] = min(changed_bbox[0], x)
                    changed_bbox[1] = min(changed_bbox[1], y)
                    changed_bbox[2] = max(changed_bbox[2], x)
                    changed_bbox[3] = max(changed_bbox[3], y)
        if out is None:
            # The compare name carries a tail of the OTHER file's name. Two
            # comparisons in a row otherwise write the same default path and the
            # second silently replaces the first - measured: a self-compare
            # (all grey) overwrote the real difference image of the pair before it.
            tail = str(compare) if isinstance(compare, str) else "<model>"
            stem = re.sub(r"[^A-Za-z0-9]", "_", os.path.basename(tail))[-24:]
            out = os.path.join(tempfile.gettempdir(),
                               "pcb_diff_%s_vs_%s.png"
                               % (re.sub(r"[^A-Za-z0-9]", "_", layer), stem))
        out = os.path.abspath(out)
        _png(out, w, h, diff)
        changed_mm = None
        if changed_bbox is not None:
            # pixel -> mm through the same transform the drawing used
            changed_mm = [round(x0 + (changed_bbox[0] - 1) / s, 3),
                          round(y1 - (changed_bbox[3] - 1) / s, 3),
                          round(x0 + (changed_bbox[2] - 1) / s, 3),
                          round(y1 - (changed_bbox[1] - 1) / s, 3)]
        total = max(1, both + only_a + only_b)
        value.update({
            "png": out,
            "compare_to": compare if isinstance(compare, str) else "<model>",
            "compare_layer": olayer,
            "drawn_other": odrawn,
            "pixels_shared": both,
            "pixels_only_here": only_a,
            "pixels_only_there": only_b,
            "changed_fraction": round((only_a + only_b) / float(total), 6),
            "changed_bbox_mm": changed_mm,
            "identical": only_a == 0 and only_b == 0,
        })
        return ok(
            value=value, unit="px",
            model="pixel difference of two rasters of the same drawing "
                  "model: grey = ink on both boards, red = only on the board "
                  "named by `path`, blue = only on `compare`. A pixel counts as "
                  "ink when it is not the background colour.",
            basis="both boards were parsed by this server and drawn through the "
                  "same transform, so the difference is exactly the drawn "
                  "difference - no registration, no resampling",
            precision="pixel-level and transform-level only: two boards whose "
                      "geometry differs by less than one pixel at this px_per_mm "
                      "read as identical, and a colour change inside one class "
                      "is invisible. It is a picture of what changed, not a DRC.",
            assumptions={"same_layer_unless_compare_layer": True})

    if out is None:
        out = os.path.join(tempfile.gettempdir(),
                           "pcb_render_%s.png" % re.sub(r"[^A-Za-z0-9]", "_", layer))
    out = os.path.abspath(out)
    _png(out, w, h, pix)
    value["png"] = out
    return ok(
        value=value,
        unit="px",
        model="flat raster: pour polygons (even-odd), tracks as capsules, "
              "via rings, pads, board edge",
        basis="the geometry the parsed file contains, drawn at one colour per "
              "object class; nothing is inferred and nothing is hidden",
        precision="preview only: no mask, no silkscreen, no drill table, no "
                  "transparency between layers. A custom-polygon pad is drawn as "
                  "its rotated bounding rectangle. Canvas is capped at 4000 px on "
                  "the long side (px_per_mm is scaled down and reported back).",
        assumptions={"objects_off_layer_hidden": True,
                     "via_hidden_if_layer_not_an_endpoint": True})
