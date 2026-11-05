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


def render(board, layer="F.Cu", px_per_mm=12.0, out=None,
           pour=True, track=True, via=True, pad=True, edge=True):
    box = _bboxes(board)
    if not box:
        return err("the board model has no geometry to draw", "NO_GEOMETRY")
    x0, y0, x1, y1 = box
    s = float(px_per_mm)
    if s <= 0:
        return err("px_per_mm must be positive", "ARG_RANGE")
    # A 300 mm board at 40 px/mm is 12000 px wide: cap the canvas, never OOM a call.
    w = int((x1 - x0) * s) + 2
    h = int((y1 - y0) * s) + 2
    cap = 4000
    shrink = 1.0
    if max(w, h) > cap:
        shrink = cap / float(max(w, h))
        s *= shrink
        w = int((x1 - x0) * s) + 2
        h = int((y1 - y0) * s) + 2

    def tf(x, y):
        return ((x - x0) * s + 1, (y1 - y) * s + 1)

    pix = bytearray(bytes(BG) * (w * h))
    drawn = {"pour_polygons": 0, "tracks": 0, "vias": 0, "pads": 0, "edge_segments": 0}

    if pour:
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

    if track:
        for t in board.tracks:
            if t.get("layer") != layer:
                continue
            st, en, wd = t.get("start"), t.get("end"), t.get("width") or 0
            if not st or not en:
                continue
            r = max(0.35, (wd * s) / 2.0)
            _capsule(pix, w, h, tf(*st), tf(*en), r, COL["track"])
            drawn["tracks"] += 1

    if pad:
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

    if via:
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

    if edge:
        chains = list(board.outline or []) + list(board.cutouts or []) + \
                 list(board.outline_open or [])
        for ch in chains:
            for i in range(len(ch) - 1):
                _capsule(pix, w, h, tf(*ch[i]), tf(*ch[i + 1]), 1.2, COL["edge"])
                drawn["edge_segments"] += 1

    if out is None:
        out = os.path.join(tempfile.gettempdir(),
                           "pcb_render_%s.png" % re.sub(r"[^A-Za-z0-9]", "_", layer))
    out = os.path.abspath(out)
    nbytes = _png(out, w, h, pix)
    return ok(
        value={"png": out, "width_px": w, "height_px": h,
               "px_per_mm": round(s, 3), "layer": layer,
               "drawn": drawn,
               "bbox_mm": [round(x0, 3), round(y0, 3), round(x1, 3), round(y1, 3)]},
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
