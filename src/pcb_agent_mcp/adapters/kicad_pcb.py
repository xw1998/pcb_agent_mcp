"""KiCad `.kicad_pcb` adapter (read-only).

The file is an S-expression tree, so the whole job is: tokenize, parse, then pick
out the handful of nodes that carry a fact the Oracle can measure.

Two conversions happen here and nowhere else (docs/07 §3 C1):

* **Y axis**: KiCad's Y grows downwards, the canonical frame grows upwards, so
  every y is negated on the way in. Angles therefore flip sign too.
* **units**: KiCad stores millimetres already, so no scaling - but the fact is
  asserted (and recorded in `source`) rather than assumed silently.

Facts KiCad does not put in the board file (clearance matrix, net classes,
board-level DRC severities) live in `.kicad_pro` / `.kicad_dru`; when they are
not passed in, they are declared `absent` with the reason, never defaulted.
"""
import json
import math
import os

from .canonical import Board
from .. import geometry as G

# --------------------------------------------------------------- tokenizer
# Same table as fidelity/sexpr.py on purpose: two readers that disagree about
# escapes disagree about the board. An escape outside the table keeps its
# backslash instead of quietly turning "\x" into "x".
_ESCAPES = {"n": "\n", "t": "\t", "r": "\r", "\\": "\\", '"': '"'}


def tokenize(text):
    i, n = 0, len(text)
    while i < n:
        c = text[i]
        if c in " \t\r\n":
            i += 1
            continue
        if c == "(" or c == ")":
            yield c
            i += 1
            continue
        if c == '"':
            i += 1
            buf = []
            while i < n:
                ch = text[i]
                if ch == "\\" and i + 1 < n:
                    nxt = text[i + 1]
                    buf.append(_ESCAPES.get(nxt, "\\" + nxt))
                    i += 2
                    continue
                if ch == '"':
                    i += 1
                    break
                buf.append(ch)
                i += 1
            yield ("str", "".join(buf))
            continue
        j = i
        while j < n and text[j] not in ' \t\r\n()"':
            j += 1
        yield ("atom", text[i:j])
        i = j


def parse_sexpr(text):
    """Parse one S-expression into nested lists. Atoms become str, quoted -> str."""
    stack = [[]]
    for tok in tokenize(text):
        if tok == "(":
            new = []
            stack[-1].append(new)
            stack.append(new)
        elif tok == ")":
            if len(stack) == 1:
                raise ValueError("unbalanced ')' in S-expression")
            stack.pop()
        else:
            stack[-1].append(tok[1])
    if len(stack) != 1:
        raise ValueError("unbalanced '(' in S-expression (%d unclosed)" % (len(stack) - 1))
    return stack[0][0] if stack[0] else []


# ------------------------------------------------------------------ helpers
def child(node, key):
    if not isinstance(node, list):
        return None
    for item in node[1:]:
        if isinstance(item, list) and item and item[0] == key:
            return item
    return None


def children(node, key):
    out = []
    if isinstance(node, list):
        for item in node[1:]:
            if isinstance(item, list) and item and item[0] == key:
                out.append(item)
    return out


def atom(node, idx, default=None):
    if not isinstance(node, list) or len(node) <= idx:
        return default
    v = node[idx]
    return default if isinstance(v, list) else v


def num(node, idx, default=None):
    v = atom(node, idx)
    if v is None:
        return default
    try:
        return float(v)
    except (TypeError, ValueError):
        return default


def xy(node, default=(0.0, 0.0)):
    """Read `(at x y [rot])` / `(start x y)` style nodes into canonical coords."""
    if not isinstance(node, list):
        return default
    x = num(node, 1, default[0])
    y = num(node, 2, default[1])
    return (x, -y)          # Y flip: KiCad grows down, canonical grows up


def xy_raw(node):
    """Raw (unflipped) coordinates, for arcs where orientation matters."""
    return (num(node, 1, 0.0), num(node, 2, 0.0))


# ------------------------------------------------------------------- outline
# 1 um: a pad edge that is a polygon approximation must never be the reason a
# clearance check disagrees with KiCad by a measurable amount.
_ARC_CHORD_TOL = 0.001

def _arc_points(start, mid, end):
    """Three-point arc -> polyline (KiCad 6+ `gr_arc` stores start/mid/end)."""
    ax, ay = start
    bx, by = mid
    cx, cy = end
    d = 2.0 * (ax * (by - cy) + bx * (cy - ay) + cx * (ay - by))
    if abs(d) < 1e-12:
        return [(ax, ay), (cx, cy)]
    ux = ((ax * ax + ay * ay) * (by - cy) + (bx * bx + by * by) * (cy - ay) +
          (cx * cx + cy * cy) * (ay - by)) / d
    uy = ((ax * ax + ay * ay) * (cx - bx) + (bx * bx + by * by) * (ax - cx) +
          (cx * cx + cy * cy) * (bx - ax)) / d
    r = ((ax - ux) ** 2 + (ay - uy) ** 2) ** 0.5
    import math
    a0 = math.degrees(math.atan2(ay - uy, ax - ux))
    am = math.degrees(math.atan2(by - uy, bx - ux))
    a1 = math.degrees(math.atan2(cy - uy, cx - ux))
    # choose the sweep that passes through the mid point
    def _norm(a):
        while a < 0:
            a += 360.0
        while a >= 360.0:
            a -= 360.0
        return a
    sweep = _norm(a1 - a0)
    if not (_norm(am - a0) <= sweep + 1e-6):
        sweep -= 360.0
    return G.arc_to_points((ux, uy), r, a0, sweep, chord_tol_mm=_ARC_CHORD_TOL)


def _chain_segments(segments, tol=1e-6):
    """Chain unordered segments into closed contours [(x,y), ...]."""
    segs = [(a, b) for a, b in segments if a != b]
    if not segs:
        return []

    def close(p, q):
        return abs(p[0] - q[0]) <= tol and abs(p[1] - q[1]) <= tol

    contours = []
    used = [False] * len(segs)
    for i, (a, b) in enumerate(segs):
        if used[i]:
            continue
        used[i] = True
        poly = [a, b]
        extended = True
        while extended and not close(poly[0], poly[-1]):
            extended = False
            for j, (c, d) in enumerate(segs):
                if used[j]:
                    continue
                if close(poly[-1], c):
                    poly.append(d)
                elif close(poly[-1], d):
                    poly.append(c)
                elif close(poly[0], d):
                    poly.insert(0, c)
                elif close(poly[0], c):
                    poly.insert(0, d)
                else:
                    continue
                used[j] = True
                extended = True
                break
        if len(poly) >= 3:
            if close(poly[0], poly[-1]):
                poly = poly[:-1]
            contours.append(poly)
    return contours


def _edge_polygons(board_node):
    segments, polys = [], []
    for kind in ("gr_line", "gr_arc", "gr_rect", "gr_circle", "gr_poly", "gr_curve"):
        for node in children(board_node, kind):
            layer_node = child(node, "layer")
            if atom(layer_node, 1) != "Edge.Cuts":
                continue
            if kind == "gr_line":
                segments.append((xy(child(node, "start")), xy(child(node, "end"))))
            elif kind == "gr_arc":
                s, m, e = child(node, "start"), child(node, "mid"), child(node, "end")
                if s and m and e:
                    pts = _arc_points(xy(s), xy(m), xy(e))
                else:
                    # legacy form: (start center) (end arc-start) (angle deg)
                    center = xy(child(node, "start"))
                    arc_start = xy(child(node, "end"))
                    ang = num(child(node, "angle"), 1, 0.0)
                    import math
                    r = G.dist_point_point(center, arc_start)
                    a0 = math.degrees(math.atan2(arc_start[1] - center[1],
                                                 arc_start[0] - center[0]))
                    pts = G.arc_to_points(center, r, a0, ang,
                                          chord_tol_mm=_ARC_CHORD_TOL)
                for i in range(len(pts) - 1):
                    segments.append((pts[i], pts[i + 1]))
            elif kind == "gr_rect":
                x0, y0 = xy(child(node, "start"))
                x1, y1 = xy(child(node, "end"))
                polys.append([(x0, y0), (x1, y0), (x1, y1), (x0, y1)])
            elif kind == "gr_circle":
                c = xy(child(node, "center"))
                e = xy(child(node, "end"))
                polys.append(G.circle_points(c[0], c[1],
                                             G.dist_point_point(c, e), 64))
            elif kind == "gr_poly":
                pts = child(child(node, "pts"), "xy")
                pl = [xy(p) for p in children(child(node, "pts"), "xy")]
                if pl:
                    polys.append(pl)
                del pts
            elif kind == "gr_curve":
                segments.extend(_bezier_segments(node))
    contours = _chain_segments(segments)
    return polys, contours


def _bezier_segments(node, steps=16):
    pts_node = child(node, "pts")
    raw = [xy(p) for p in children(pts_node, "xy")] if pts_node else []
    if len(raw) != 4:
        return []
    p0, p1, p2, p3 = raw
    out, prev = [], p0
    for i in range(1, steps + 1):
        t = i / float(steps)
        mt = 1 - t
        x = (mt ** 3 * p0[0] + 3 * mt * mt * t * p1[0] +
             3 * mt * t * t * p2[0] + t ** 3 * p3[0])
        y = (mt ** 3 * p0[1] + 3 * mt * mt * t * p1[1] +
             3 * mt * t * t * p2[1] + t ** 3 * p3[1])
        out.append((prev, (x, y)))
        prev = (x, y)
    return out


# --------------------------------------------------------------- footprints
def _uuid(node):
    """The item's own uuid, or None.

    kicad-cli's DRC report identifies each offending item by uuid only (its
    descriptions are translated, its `pos` is the item's ANCHOR, not the closest
    point), so the uuid is the one identity both sides can agree on.
    """
    return (atom(child(node, "uuid"), 1, None) or None)


def _pad_from_node(pad_node):
    number = atom(pad_node, 1, "") or ""
    kind = atom(pad_node, 2, "smd") or "smd"          # smd | thru_hole | np_thru_hole | connect
    shape = atom(pad_node, 3, "rect") or "rect"
    at = child(pad_node, "at") or []
    x, y = xy(at)
    # KiCad stores a pad's angle as an ABSOLUTE angle (pcbnew reports exactly what the
    # file says, even inside a rotated footprint), so it is used as-is and never
    # composed with the footprint angle.
    rot = num(at, 3, 0.0) or 0.0
    size = child(pad_node, "size")
    w = num(size, 1, 0.0) or 0.0
    h = num(size, 2, w) if num(size, 2) is not None else w
    drill = child(pad_node, "drill")
    hole = None
    off = None
    if drill:
        vals = [v for v in drill[1:] if not isinstance(v, list)]
        nums = []
        for v in vals:
            try:
                nums.append(float(v))
            except (TypeError, ValueError):
                pass
        if nums:
            hole = max(nums)
        # `(drill 0.75 (offset 0 0.4))` shifts the PAD COPPER, not just the hole:
        # pcbnew's bounding box for Q201.1 on complex_hierarchy sits at y+0.4 and
        # HitTest says the whole 1.1x1.8 rectangle moved with it. Ignoring it put 27
        # pads of that board up to 0.4 mm off. Kept in the pad's own frame with Y
        # canonicalised (file y grows down); `canonical.pad_points` turns it by the
        # pad's absolute angle. Checked against pcbnew on 412 pads / 20188 probes.
        on = child(drill, "offset")
        if on is not None:
            ox = num(on, 1, 0.0) or 0.0
            oy = num(on, 2, 0.0) or 0.0
            if ox or oy:
                off = [ox, -oy]
    # Mask expansion can be set per pad, and the mask web a fab has to hold is
    # exactly this number. It was never read before 0.0.56: `pad_to_mask` was a
    # blanket "parsed only on demand" and the mask web could not be measured.
    mask_m = num(child(pad_node, "solder_mask_margin"), 1, None)
    net_node = child(pad_node, "net")
    net_code = None
    net_name = None
    if net_node is not None:
        net_code = atom(net_node, 1)
        net_name = atom(net_node, 2)
    layers = []
    ln = child(pad_node, "layers")
    if ln:
        layers = [v for v in ln[1:] if not isinstance(v, list)]
    # A roundrect keeps its corner radius: on pic_programmer two neighbouring pads
    # with r = 0.25 mm are 0.25 mm further apart than their bounding rectangles, which
    # is exactly the difference between "violation" and "clean".
    rratio = num(child(pad_node, "roundrect_rratio"), 1, None)
    if shape in ("custom",):
        # A custom pad is the UNION of its primitives, and taking only the first
        # `gr_poly` measured a pad smaller than the copper on the board - an
        # optimistic error, the one direction a clearance check must not make.
        # Every primitive this file format can express is collected into ONE
        # polygon. A single primitive is kept EXACTLY as drawn, has to be: a
        # primitive may be concave (pic_programmer JP1.2 is a notched rectangle),
        # and hulling it filled the notch with copper that is not on the board, so
        # pcbnew's HitTest and this outline disagreed inside a pad.
        # Several primitives are hulled, because their union need not be convex
        # and the hull is never smaller than that union: too strict, never too
        # loose. `outline` says which of the two happened. Kinds that cannot be
        # represented at all are listed in `ignored_primitives` instead of
        # vanishing.
        pts = None
        polys = []
        ignored = []
        opts = child(pad_node, "primitives") or child(pad_node, "options")
        for prim in (opts or []):
            if not (isinstance(prim, list) and prim):
                continue
            if prim[0] in ("gr_poly", "gr_rect", "gr_circle", "gr_line", "gr_arc"):
                poly = _graphic_poly(prim)
                if poly:
                    polys.append(poly)
                else:
                    ignored.append(prim[0])
            elif prim[0] in ("gr_curve", "gr_bbox", "gr_vector",
                             "gr_text", "gr_arc_ex", "gr_bezier"):
                ignored.append(prim[0])
        if len(polys) == 1:
            pts = polys[0]
        elif polys:
            pts = _convex_hull([p for poly in polys for p in poly])
        if pts:
            # `x`/`y` stay the pad ANCHOR, the same point pcbnew reports; the outline
            # lives in `polygon` relative to that anchor. Reporting the polygon
            # centroid instead lost the anchor offset and moved custom pads up to
            # 0.9 mm away from the hole they belong to (seen on pic_programmer/JP1).
            out = {"number": number, "net": net_name or net_code, "x": x, "y": y,
                   "uuid": _uuid(pad_node),
                   "w": (max(p[0] for p in pts) - min(p[0] for p in pts)),
                   "h": (max(p[1] for p in pts) - min(p[1] for p in pts)),
                   "shape": "custom", "rotation": rot, "layers": layers,
                   "hole_mm": hole, "smd": kind == "smd", "type": kind,
                   "polygon": list(pts),
                   "outline": ("convex_hull_of_primitives" if len(polys) > 1
                               else "primitive"),
                   "primitives": len(polys),
                   "ignored_primitives": ignored}
            if off:
                out["offset"] = off
            if mask_m is not None:
                out["solder_mask_margin"] = mask_m
            return out
    out = {"number": number, "net": net_name or net_code, "x": x, "y": y,
           "uuid": _uuid(pad_node),
           "w": w, "h": h, "shape": shape, "rotation": rot, "layers": layers,
           "hole_mm": hole, "smd": (kind == "smd"), "type": kind}
    # A pad can carry its OWN clearance: `(pad "9" ... (clearance 0.125))`.
    pad_clear = num(child(pad_node, "clearance"), 1, None)
    if pad_clear is not None:
        out["clearance"] = pad_clear
    if mask_m is not None:
        out["solder_mask_margin"] = mask_m
    if shape == "roundrect":
        # 0.25 is the KiCad file format's documented default corner ratio, not a guess
        # about this board: it applies when the file does not spell the ratio out.
        out["rratio"] = 0.25 if rratio is None else float(rratio)
    if off:
        out["offset"] = off
    return out


def _convex_hull(points):
    """Monotone-chain hull. `None` when there is nothing to hull."""
    pts = sorted(set((round(x, 6), round(y, 6)) for x, y in points))
    if len(pts) < 3:
        return pts or None
    def cross(o, a, b):
        return (a[0] - o[0]) * (b[1] - o[1]) - (a[1] - o[1]) * (b[0] - o[0])
    lower = []
    for p in pts:
        while len(lower) >= 2 and cross(lower[-2], lower[-1], p) <= 0:
            lower.pop()
        lower.append(p)
    upper = []
    for p in reversed(pts):
        while len(upper) >= 2 and cross(upper[-2], upper[-1], p) <= 0:
            upper.pop()
        upper.append(p)
    return lower[:-1] + upper[:-1]

def _is_copper(name):
    n = str(name or "")
    return n == "*.Cu" or n.endswith(".Cu")

def _is_silk(name):
    return str(name or "").endswith(".SilkS")

def _is_mask(name):
    n = str(name or "")
    return n == "*.Mask" or n.endswith(".Mask")

_MASK_SIDES = ("F.Mask", "B.Mask")

def mask_layers(layers):
    """The concrete solder-mask layers an item names, `*.Mask` expanded to both.

    Kept separate from the raw `layers` list because a mask opening is per SIDE:
    two pads that both write `*.Mask` have an opening on each side, and a mask web
    is the gap between two openings on the SAME side. Measuring `*.Mask` as one
    pseudo-layer would compare a front opening with a back one across the board
    thickness.
    """
    out = []
    for name in layers or []:
        if not _is_mask(name):
            continue
        if str(name) == "*.Mask":
            for side in _MASK_SIDES:
                if side not in out:
                    out.append(side)
        elif str(name) not in out:
            out.append(str(name))
    return out


def _graphic_poly(g):
    """A copper graphic as one polygon in its OWN frame (caller rotates it).

    Filled polygons/rectangles/circles are their outline; a stroked line becomes the
    rectangle around its stroke, which is enough for an edge-to-edge distance (the
    only difference from a capsule is at the two ends).
    """
    kind = g[0] if g else ""
    if kind in ("fp_poly", "gr_poly"):
        pn = child(g, "pts")
        return [xy(p) for p in children(pn, "xy")] if pn else None
    if kind in ("fp_rect", "gr_rect"):
        a, b = xy(child(g, "start")), xy(child(g, "end"))
        return [(min(a[0], b[0]), min(a[1], b[1])), (max(a[0], b[0]), min(a[1], b[1])),
                (max(a[0], b[0]), max(a[1], b[1])), (min(a[0], b[0]), max(a[1], b[1]))]
    if kind in ("fp_line", "gr_line", "fp_arc", "gr_arc"):
        if kind in ("fp_arc", "gr_arc"):
            s_, m_, e_ = child(g, "start"), child(g, "mid"), child(g, "end")
            if not (s_ and m_ and e_):
                return None
            pts = _arc_points(xy(s_), xy(m_), xy(e_))
        else:
            s_, e_ = child(g, "start"), child(g, "end")
            if not (s_ and e_):
                return None
            pts = [xy(s_), xy(e_)]
        w = num(child(child(g, "stroke"), "width"), 1, 0.0) or 0.0
        half = w / 2.0
        if half <= 0:
            return None
        out = []
        for i in range(len(pts) - 1):
            (x1, y1), (x2, y2) = pts[i], pts[i + 1]
            d = math.hypot(x2 - x1, y2 - y1) or 1.0
            nx, ny = -(y2 - y1) / d * half, (x2 - x1) / d * half
            out.extend([(x1 + nx, y1 + ny), (x2 + nx, y2 + ny),
                        (x2 - nx, y2 - ny), (x1 - nx, y1 - ny)])
        return out or None
    if kind in ("fp_circle", "gr_circle"):
        c = xy(child(g, "center"))
        e = xy(child(g, "end"))
        r = math.hypot(e[0] - c[0], e[1] - c[1])
        return G.circle_points(c[0], c[1], r, 24) if r > 0 else None
    return None


_GRAPHIC_KINDS = ("fp_poly", "fp_rect", "fp_circle", "fp_line", "fp_arc")
# Board-level graphics use the `gr_` prefix, footprint-level ones `fp_`. They are two
# different name spaces, and a board-level `gr_line` is not in `_GRAPHIC_KINDS`:
# reusing the footprint tuple there silently dropped every board-level silk line,
# mask opening and copper graphic the file draws at top level.
_BOARD_GRAPHIC_KINDS = ("gr_poly", "gr_rect", "gr_circle", "gr_line", "gr_arc")
_SILK_TEXT_KINDS = ("fp_text", "gr_text")

def _stroke_width(g):
    """A graphic's stroke width, or None when the file does not state one.

    None is never replaced by a default: KiCad's fallback is a program setting,
    not a property of this board, and a silk line measured at an invented width
    is exactly the kind of fabricated number this project refuses to give.
    """
    w = num(child(child(g, "stroke"), "width"), 1, None)
    if w is None:
        w = num(child(g, "width"), 1, None)      # pre-6 files write it inline
    return w

def _text_metrics(node):
    """(character height, stroke thickness) from `(effects (font ...))`, mm.

    Height is the SECOND number of `(size w h)`: that is the glyph height a fab's
    "minimum legend height" rule is about. Either value is None when the file does
    not state it (`(size 1 1)` with no thickness is common), and None stays None
    all the way into the answer.
    """
    eff = child(node, "effects")
    font = child(eff, "font") if eff else None
    if not font:
        return None, None
    sz = child(font, "size")
    h = None
    if sz is not None:
        h = num(sz, 2, None)
        if h is None:
            h = num(sz, 1, None)
    return h, num(child(font, "thickness"), 1, None)

def _silk_item(kind, node):
    """One silkscreen item: a stroked shape, or a text with a size and a stroke."""
    layer = atom(child(node, "layer"), 1, "")
    item = {"layer": layer, "kind": kind, "uuid": _uuid(node)}
    if kind in _SILK_TEXT_KINDS:
        item["text"] = (atom(node, 2, "") if kind == "fp_text"
                        else atom(node, 1, "")) or ""
        item["height_mm"], item["width_mm"] = _text_metrics(node)
    else:
        item["width_mm"] = _stroke_width(node)
    return item


def _footprint(node, mask_expansion=0.0):
    """One placed footprint. `mask_expansion` is the board's
    `(setup (pad_to_mask_clearance ...))`, used when neither the pad nor the
    footprint states an expansion of its own."""
    lib_id = atom(node, 1, "")
    layer = atom(child(node, "layer"), 1, "F.Cu")
    at = child(node, "at")
    x, y = xy(at)
    rot = num(at, 3, 0.0) or 0.0
    # Canonical space is the file frame mirrored in Y. Mirroring flips the sign of a
    # rotation *and* the sign of the angle, so a footprint at `a` degrees in the file
    # is at `a` degrees in canonical space as well. Negating it here -- the intuitive
    # "a Y flip reverses the sense of rotation" reading -- mirrored every rotated
    # footprint's pads about the footprint origin: pads landed up to 111 mm away from
    # where pcbnew puts them, and the Oracle then reported phantom clearance
    # violations on copper that is nowhere near itself. Checked pad-by-pad against
    # pcbnew in tests/test_kicad_rotation.py.
    ref, value = "", ""
    for prop in children(node, "property"):
        name = atom(prop, 1, "")
        if name == "Reference":
            ref = atom(prop, 2, "") or ""
        elif name == "Value":
            value = atom(prop, 2, "") or ""
    for txt in children(node, "fp_text"):
        kind = atom(txt, 1, "")
        if kind == "reference":
            ref = ref or (atom(txt, 2, "") or "")
        elif kind == "value":
            value = value or (atom(txt, 2, "") or "")
    pads = [_pad_from_node(p) for p in children(node, "pad")]
    # A footprint can override the clearance of all its pads: `(clearance 0.125)`
    # sits directly in the `(footprint ...)` node and KiCad uses it INSTEAD of the
    # net class clearance. Ignoring it measured cm5_minima's U702 (a DFN whose
    # footprint says 0.125 while the class says 0.13) as 7 violations that the
    # vendor DRC does not have - a 5 micrometre difference deciding a verdict.
    fp_clear = num(child(node, "clearance"), 1, None)
    # A footprint can set one mask expansion for all of its pads. The precedence
    # is KiCad's: pad `solder_mask_margin` > footprint `solder_mask_margin` >
    # `(setup (pad_to_mask_clearance ...))`.
    fp_mask = num(child(node, "solder_mask_margin"), 1, None)
    # `(net_tie_pad_groups "1, 2")` declares pads of one footprint that are MEANT to
    # touch: KiCad's DRC does not report a clearance violation inside a group, and a
    # net tie drawn as two abutting pads is common (royalblue54L_feather JP1, a
    # bridged solder jumper). Without this the Oracle reported a 0.0 mm violation on
    # copper the vendor deliberately connected.
    tie_groups = []
    for g in children(node, "net_tie_pad_groups"):
        if isinstance(g, list) and len(g) > 1:
            names = [str(n).strip() for n in g[1:] if not isinstance(n, list)]
            nums = set()
            for chunk in names:
                nums.update(x.strip() for x in chunk.split(",") if x.strip())
            if nums:
                tie_groups.append(nums)
    for pad in pads:
        if fp_clear is not None and pad.get("clearance") is None:
            pad["clearance"] = fp_clear
        sides = mask_layers(pad.get("layers"))
        if sides:
            own = pad.get("solder_mask_margin")
            if own is not None:
                pad["mask_margin"], pad["mask_margin_source"] = float(own), "pad"
            elif fp_mask is not None:
                pad["mask_margin"], pad["mask_margin_source"] = float(fp_mask), "footprint"
            else:
                # Absent everywhere means zero: that is what KiCad itself writes
                # into `(setup (pad_to_mask_clearance ...))` when the field is
                # empty, and the source is recorded so the answer can say so.
                pad["mask_margin"] = float(mask_expansion or 0.0)
                pad["mask_margin_source"] = ("setup" if mask_expansion
                                             else "absent_treated_as_zero")
            pad["mask_layers"] = sides
        pad["net_tie_group"] = None
        for gi, nums in enumerate(tie_groups):
            if str(pad.get("number") or "") in nums:
                pad["net_tie_group"] = gi
                break
    # pads are stored relative to the footprint origin; place them in board space
    import math
    ca, sa = math.cos(math.radians(rot)), math.sin(math.radians(rot))
    # Back-side footprints need no extra mirroring here: the file already stores the
    # pad offsets as they are drawn (checked against pcbnew on 2118 back-side pads in
    # the `video` demo board). Adding a mirror put 260 of them 3.1 mm off.
    for pad in pads:
        px, py = pad["x"], pad["y"]
        pad["x"] = x + px * ca - py * sa
        pad["y"] = y + px * sa + py * ca
        if pad.get("polygon"):
            # A custom pad's outline is stored relative to its anchor, so it has to
            # turn with the footprint as well.
            pad["polygon"] = [(qx * ca - qy * sa, qx * sa + qy * ca)
                              for qx, qy in pad["polygon"]]
        # pad["rotation"] stays as written: it is already absolute.
        pad["ref"] = ref
    # Copper drawn as a footprint graphic is real copper (microwave draws its whole
    # board out of `POLY` footprints); it has no net, so it is not a track and not a
    # pad, but its clearance is measured by KiCad's DRC all the same.
    graphics = []
    for g in node:
        if not (isinstance(g, list) and g and g[0] in _GRAPHIC_KINDS):
            continue
        layer = atom(child(g, "layer"), 1, "")
        if not _is_copper(layer):
            continue
        poly = _graphic_poly(g)
        if not poly:
            continue
        graphics.append({"layer": layer, "uuid": _uuid(g),
                         # Same placement as a pad: the outline is stored in the
                         # footprint frame, so it must turn AND translate. Without
                         # the translation every polygon stayed at the board origin
                         # (microwave's four POLY outlines all spanned
                         # x -135.75..135.75 while the footprints sit at x 67 and
                         # 338), so the two real pad-vs-polygon DRC violations of
                         # that board were unreachable and four phantom
                         # polygon-vs-polygon 0.0 mm pairs took their place.
                         "poly": [(x + qx * ca - qy * sa, y + qx * sa + qy * ca)
                                  for qx, qy in poly]})
    # Silkscreen and solder-mask openings drawn as footprint graphics. Neither is
    # copper (so neither enters `graphics`), and both are per side.
    silk, mask = [], []
    for g in node:
        if not (isinstance(g, list) and g):
            continue
        if g[0] not in _GRAPHIC_KINDS and g[0] not in _SILK_TEXT_KINDS:
            continue
        glayer = atom(child(g, "layer"), 1, "")
        if _is_silk(glayer):
            silk.append(_silk_item(g[0], g))
            continue
        if _is_mask(glayer) and g[0] in _GRAPHIC_KINDS:
            mpoly = _graphic_poly(g)
            if mpoly:
                mask.append({"layer": glayer, "uuid": _uuid(g), "margin_mm": 0.0,
                             "margin_source": "graphic",
                             "poly": [(x + qx * ca - qy * sa, y + qx * sa + qy * ca)
                                      for qx, qy in mpoly]})
    bbox = None
    if pads:
        xs, ys = [], []
        for pad in pads:
            half = max(pad["w"], pad["h"]) / 2.0
            xs.extend([pad["x"] - half, pad["x"] + half])
            ys.extend([pad["y"] - half, pad["y"] + half])
        bbox = (min(xs), min(ys), max(xs), max(ys))
    # DNP lives inside the attribute list in both KiCad 7 and 8:
    #   (attr smd dnp)  /  (attr through_hole)
    attr = child(node, "attr")
    flags = [atom(attr, i, "") for i in range(1, len(attr or []))] if attr else []
    if isinstance(flags, list) and any(not isinstance(f, str) for f in flags):
        flags = [f for f in flags if isinstance(f, str)]
    dnp = ("dnp" in flags) or (child(node, "dnp") is not None)
    return {"ref": ref, "value": value, "lib_id": lib_id, "graphics": graphics,
            "silk": silk, "mask": mask,
            "x": round(x, 4),
            "y": round(y, 4), "rotation": rot, "layer": layer,
            "bbox": _bbox_dict(bbox), "pads": pads,
            "attr": flags, "dnp": dnp}


def _bbox_dict(b):
    if not b:
        return None
    return {"x_min": round(b[0], 4), "y_min": round(b[1], 4),
            "x_max": round(b[2], 4), "y_max": round(b[3], 4)}


# ------------------------------------------------------------------- zones
def _zone(node):
    net_name = atom(child(node, "net_name"), 1)
    net_code = atom(child(node, "net"), 1)
    layer = atom(child(node, "layer"), 1)
    layers_node = child(node, "layers")
    layer_list = ([v for v in layers_node[1:] if not isinstance(v, list)]
                  if layers_node else ([layer] if layer else []))
    outline = []
    poly_node = child(node, "polygon")
    if poly_node:
        pts = child(poly_node, "pts")
        outline = [xy(p) for p in children(pts, "xy")] if pts else []
    keepout = None
    ko = child(node, "keepout")
    if ko is not None:
        keepout = {}
        for key in ("tracks", "vias", "pads", "copperpour", "footprints"):
            item = child(ko, key)
            if item is not None:
                keepout[key] = atom(item, 1, "not_allowed")
    fill = child(node, "fill")
    fill_mode = "none"
    if fill is not None:
        v = atom(fill, 1, "yes")
        fill_mode = "solid" if v in ("yes", "true", "solid") else str(v)
        if child(fill, "mode") is not None:
            fill_mode = atom(child(fill, "mode"), 1, fill_mode)
    filled = []
    for fp in children(node, "filled_polygon"):
        pts = child(fp, "pts")
        if pts:
            filled.append([xy(p) for p in children(pts, "xy")])
    priority = atom(child(node, "priority"), 1)
    # Thermal reliefs: the numbers a relief is made of live in two places, and they
    # are READ here but never used as a default anywhere - the tool that models a
    # relief asks the caller for the geometry, so a file value can only ever be
    # reported next to the answer, never silently adopted.
    #   (connect_pads [yes|thru_hole_only|no] (clearance X))
    #   (fill yes (thermal_gap G) (thermal_bridge_width W))
    pad_connection = None
    cp = child(node, "connect_pads")
    if cp is not None:
        words = [w for w in cp[1:] if not isinstance(w, list)]
        if "yes" in words:
            pad_connection = "thermal"
        elif "thru_hole_only" in words:
            pad_connection = "thru_hole_only"
        elif "no" in words:
            pad_connection = "none"
        else:
            pad_connection = "solid"
    thermal_gap = num(child(fill, "thermal_gap"), 1) if fill is not None else None
    thermal_bridge = (num(child(fill, "thermal_bridge_width"), 1)
                      if fill is not None else None)
    return {
        "net": net_name or net_code,
        "layers": layer_list,
        "outline": outline,
        "filled_polygons": filled,
        "fill_mode": fill_mode,
        "priority": int(priority) if priority not in (None, "") else 0,
        "keepout": keepout,
        "is_keepout": keepout is not None,
        "min_thickness": num(child(node, "min_thickness"), 1),
        "connect_pads": pad_connection,
        "thermal_gap_mm": thermal_gap if thermal_gap not in (None, "") else None,
        "thermal_bridge_width_mm": (thermal_bridge
                                    if thermal_bridge not in (None, "") else None),
    }


# ---------------------------------------------- side files: rules / stackup
def board_net_classes(board_node):
    """The `(net_class ...)` blocks stored INSIDE the board file.

    Before net classes moved to `.kicad_pro` (v6/v7 era files, and every board this
    server writes) the board file carried them itself:

        (net_class "Default" "" (clearance 0.2) (trace_width 0.25)
                   (via_dia 0.6) (via_drill 0.3) (add_net "GND") ...)

    Ignoring these made a board with no `.kicad_pro` report "design rules absent"
    while its own rules sat in the file - which is exactly the kind of silent gap
    that turns into a wrong clearance verdict later.
    """
    out = {}
    for nc in children(board_node, "net_class"):
        name = atom(nc, 1) or "Default"
        spec = {"clearance": None, "track_width": None, "via_diameter": None,
                "via_drill": None, "nets": []}
        pairs = {"clearance": "clearance", "trace_width": "track_width",
                 "via_dia": "via_diameter", "via_drill": "via_drill"}
        for key, field in pairs.items():
            node = child(nc, key)
            if node is not None:
                spec[field] = num(node, 1)
        for add in children(nc, "add_net"):
            name_of_net = atom(add, 1)
            if name_of_net is not None:
                spec["nets"].append(name_of_net)
        out[name] = spec
    return out

def load_project_rules(project_path):
    """Read `.kicad_pro` design settings. Never invents a value that is absent."""
    out = {"source": None, "clearance": {}, "track_width": {}, "via": {},
           "net_classes": {}, "dru_text": None, "absent": []}
    pro = project_path
    if pro and pro.lower().endswith(".kicad_pcb"):
        pro = os.path.splitext(pro)[0] + ".kicad_pro"
    if not pro or not os.path.isfile(pro):
        out["absent"].append({"fact": "design_rules",
                              "reason": "no .kicad_pro next to the board file"})
        return out
    try:
        with open(pro, "r", encoding="utf-8", errors="replace") as fh:
            data = json.load(fh)
    except Exception as exc:                                    # noqa: BLE001
        out["absent"].append({"fact": "design_rules",
                              "reason": ".kicad_pro unreadable: %s" % exc})
        return out
    out["source"] = pro
    ds = (data.get("board") or {}).get("design_settings") or {}
    rules = ds.get("rules") or {}
    for key in ("min_clearance", "min_track_width", "min_via_diameter",
                "min_via_annular_width", "min_hole_to_hole", "min_through_hole_diameter",
                "min_copper_edge_clearance", "min_microvia_diameter"):
        if key in rules:
            out["clearance" if "clearance" in key else "track_width" if "track" in key
                else "via"][key] = rules[key]
    net_settings = data.get("net_settings") or {}
    classes = net_settings.get("classes") or []
    for cls in classes:
        out["net_classes"][cls.get("name") or "Default"] = {
            "clearance": cls.get("clearance"),
            "track_width": cls.get("track_width"),
            "via_diameter": cls.get("via_diameter"),
            "via_drill": cls.get("via_drill"),
            "nets": cls.get("nets") or [],
        }
    # KiCad 7+ stores WHICH NET BELONGS TO WHICH CLASS outside the class itself:
    # `net_settings.netclass_assignments` maps a net path ("/USB/D+") to a list of
    # class names, and `netclass_patterns` maps wildcards. Reading only `classes`
    # left every class with `nets: []`, so a net in the USB_DIFF class (clearance
    # 0.2032) was measured against Default (0.15) - on royalblue54L_feather that
    # silently dropped all 13 real DRC violations and declared the board clean.
    out["netclass_assignments"] = net_settings.get("netclass_assignments") or {}
    out["netclass_patterns"] = net_settings.get("netclass_patterns") or []

    dru = os.path.splitext(pro)[0] + ".kicad_dru"
    if os.path.isfile(dru):
        try:
            with open(dru, "r", encoding="utf-8", errors="replace") as fh:
                out["dru_text"] = fh.read()
        except OSError:
            out["dru_text"] = None
        out["absent"].append({
            "fact": "custom_dru_constraints",
            "reason": ".kicad_dru text captured raw but NOT interpreted: conditional "
                      "custom rules have no machine-checkable schema here"})
    return out


def _stackup(board_node, thickness):
    setup = child(board_node, "setup")
    layers = []
    if setup:
        stack = child(setup, "stackup")
        if stack:
            for layer in children(stack, "layer"):
                name = atom(layer, 1, "")
                typ = None
                t = None
                er = None
                tand = None
                for item in layer[2:]:
                    if not isinstance(item, list) or not item:
                        continue
                    if item[0] == "type":
                        typ = atom(item, 1)
                    elif item[0] == "thickness":
                        t = num(item, 1)
                    elif item[0] == "epsilon_r":
                        er = num(item, 1)
                    elif item[0] == "loss_tangent":
                        tand = num(item, 1)
                layers.append({
                    "name": name,
                    "type": typ,
                    "thickness_mm": t,
                    "er": er,
                    "tand": tand,
                    "copper_um": (t * 1000.0) if typ == "copper" and t else None,
                })
    return layers


def _mask_source_counts(board):
    """How many mask-bearing pads got their expansion from each level."""
    out = {}
    for fp in board.footprints:
        for pad in fp.get("pads") or []:
            if not pad.get("mask_layers"):
                continue
            src = pad.get("mask_margin_source") or "?"
            out[src] = out.get(src, 0) + 1
    return out

def _apply_netclass_assignments(rules, net_names):
    """Put each net into the class the PROJECT says it is in.

    The class table alone says what each class costs, not who is in it: KiCad 7+
    keeps membership in `net_settings.netclass_assignments` (exact net paths) and
    `netclass_patterns` (wildcards). A class with no members had no effect at all
    here, which is why a board whose USB pair sits in a 0.2032 mm class was
    measured at the Default 0.15 mm and came out clean against 13 real
    violations.
    """
    classes = rules.get("net_classes") or {}
    if not classes:
        return
    assign = rules.get("netclass_assignments") or {}
    patterns = rules.get("netclass_patterns") or []
    if not assign and not patterns:
        return
    import fnmatch
    known = set(net_names)
    used = 0
    for net in sorted(known):
        names = list(assign.get(net) or [])
        if not names and assign:
            # a net stored WITHOUT its sheet path in the board but with it in the
            # project (or the other way round): match on the trailing segments
            for key, cls_list in assign.items():
                if key == net or key.endswith(net) or net.endswith(key):
                    names = list(cls_list or [])
                    break
        for pat in patterns:
            if not isinstance(pat, dict):
                continue
            if fnmatch.fnmatchcase(net, str(pat.get("pattern") or "")):
                names.append(pat.get("netclass"))
        for name in names:
            spec = classes.get(name)
            if not isinstance(spec, dict):
                continue
            if net not in (spec.get("nets") or []):
                spec.setdefault("nets", []).append(net)
                used += 1
    if used:
        src = rules.get("netclass_source")
        extra = "net_settings.netclass_assignments"
        rules["netclass_source"] = ("%s; %s" % (src, extra)) if src else extra
        rules["netclass_members_assigned"] = used

# ------------------------------------------------------------------- parse
def parse_board(path, project_path=None):
    """Parse a `.kicad_pcb` file into a canonical Board. Read-only, never writes."""
    with open(path, "rb") as fh:
        raw = fh.read()
    return parse_board_text(raw.decode("utf-8", errors="replace"),
                            path=path, project_path=project_path)

def parse_board_text(text, path="<memory>", project_path=None):
    """Same as parse_board for text already in memory (tests, pasted snippets)."""
    tree = parse_sexpr(text)
    if not tree or tree[0] != "kicad_pcb":
        raise ValueError("not a kicad_pcb file (root node: %r)" % (tree[0] if tree else None))

    board = Board("kicad_pcb", path)
    gen = child(tree, "general")
    thickness = num(child(gen, "thickness"), 1) if gen else None
    board.thickness_mm = thickness

    for net in children(tree, "net"):
        code = atom(net, 1)
        name = atom(net, 2, "") or ""
        try:
            board.nets[int(code)] = name
        except (TypeError, ValueError):
            board.nets[code] = name

    # The board-wide default for how far a solder-mask opening grows past its pad.
    # Absent means 0, which is what KiCad itself writes when the field is empty;
    # each aperture records which of the three levels supplied its number, so a
    # measured mask web can always be traced back to where the expansion came from.
    _setup = child(tree, "setup")
    mask_expansion = num(child(_setup, "pad_to_mask_clearance"), 1, 0.0) if _setup else 0.0
    if mask_expansion is None:
        mask_expansion = 0.0

    for fp_node in children(tree, "footprint") + children(tree, "module"):
        board.footprints.append(_footprint(fp_node, mask_expansion=mask_expansion))

    for seg in children(tree, "segment"):
        board.tracks.append({
            "uuid": _uuid(seg),
            "net": atom(child(seg, "net"), 1),
            "layer": atom(child(seg, "layer"), 1),
            "width": num(child(seg, "width"), 1, 0.0),
            "start": list(xy(child(seg, "start"))),
            "end": list(xy(child(seg, "end"))),
        })
    for arc in children(tree, "arc"):
        s, m, e = child(arc, "start"), child(arc, "mid"), child(arc, "end")
        if s and e:
            pts = _arc_points(xy(s), xy(m), xy(e)) if m else [xy(s), xy(e)]
            board.tracks.append({
                "uuid": _uuid(arc),
                "net": atom(child(arc, "net"), 1),
                "layer": atom(child(arc, "layer"), 1),
                "width": num(child(arc, "width"), 1, 0.0),
                "start": list(pts[0]), "end": list(pts[-1]),
                "_arc": True,
                "_points": [list(p) for p in pts],
            })

    for via in children(tree, "via"):
        vtype = atom(via, 1, "")
        layers_node = child(via, "layers")
        lname = [v for v in layers_node[1:] if not isinstance(v, list)] if layers_node else []
        board.vias.append({
            "uuid": _uuid(via),
            "net": atom(child(via, "net"), 1),
            "x": xy(child(via, "at"))[0],
            "y": xy(child(via, "at"))[1],
            "drill": num(child(via, "drill"), 1, 0.0),
            "diameter": num(child(via, "size"), 1, 0.0),
            "from": lname[0] if lname else None,
            "to": lname[-1] if len(lname) > 1 else (lname[0] if lname else None),
            "kind": vtype if vtype in ("blind", "micro") else "through",
        })

    for gnode in tree:
        if not (isinstance(gnode, list) and gnode):
            continue
        gkind = gnode[0]
        if gkind not in _BOARD_GRAPHIC_KINDS and gkind not in _SILK_TEXT_KINDS:
            continue
        layer = atom(child(gnode, "layer"), 1, "")
        if _is_silk(layer):
            board.silk.append(_silk_item(gkind, gnode))
            continue
        if _is_mask(layer) and gkind in _BOARD_GRAPHIC_KINDS:
            # A mask opening drawn as a graphic: it is the opening itself, with no
            # expansion to add (KiCad applies `pad_to_mask_clearance` to pads only).
            mpoly = _graphic_poly(gnode)
            if mpoly:
                board.mask_apertures.append({"layer": layer, "poly": mpoly,
                                             "margin_mm": 0.0,
                                             "margin_source": "graphic",
                                             "uuid": _uuid(gnode)})
            continue
        if not _is_copper(layer):
            continue
        poly = _graphic_poly(gnode)
        if poly:
            board.graphics.append({"layer": layer, "poly": poly,
                                   "uuid": _uuid(gnode)})

    for znode in children(tree, "zone"):
        board.zones.append(_zone(znode))

    polys, contours = _edge_polygons(tree)
    if polys:
        # gr_rect / gr_circle / gr_poly: the FIRST is the outer contour, and every
        # later one is a cut-out. Putting them all in `outline` (what this did
        # before) made an internal slot look like board edge: the outline bbox
        # then spanned every contour and `cutouts` stayed empty, so a 1 mm routed
        # slot was silently indistinguishable from the board's own boundary.
        board.outline = [polys[0]]
        board.cutouts = list(polys[1:])
    if contours:
        if not board.outline:
            outer = max(contours, key=lambda p: abs(G.polygon_area(p)))
            board.outline = [outer]
            board.cutouts = [c for c in contours if c is not outer]
        else:
            board.cutouts.extend(contours)

    board.stackup = _stackup(tree, thickness)
    rules = load_project_rules(project_path or path)
    if not rules.get("net_classes"):
        # the project file is absent (or has no classes): the board file's own
        # blocks are then the only rules that exist, and they are real rules
        own = board_net_classes(tree)
        if own:
            rules["net_classes"] = own
            rules["net_class_source"] = "the board file's own (net_class ...) blocks"
            rules["absent"] = [a for a in rules.get("absent") or []
                               if a.get("fact") != "design_rules"]
    # Resolve net -> class from the project's assignments/patterns now that the
    # net names are known (they are only known after the board is parsed).
    _apply_netclass_assignments(rules, [n for n in board.nets.values() if n])
    board.rules = rules

    # ---- present / absent bookkeeping (C2: declare, never default)
    board.mark("outline", bool(board.outline),
               None if board.outline else "no Edge.Cuts geometry found")
    board.mark("cutouts", bool(board.cutouts),
               None if board.cutouts else "no closed inner contour on Edge.Cuts")
    board.mark("footprints", bool(board.footprints))
    board.mark("pads", bool(board.pads()))
    board.mark("tracks", bool(board.tracks))
    board.mark("vias", bool(board.vias))
    board.mark("zones", bool(board.zones))
    filled = sum(len(z.get("filled_polygons") or []) for z in board.zones)
    board.mark("zone_fill", filled > 0,
               None if filled else "zones present but no filled_polygon: KiCad writes the "
                                   "fill result only after a fill/cache run in the GUI")
    board.mark("nets", bool(board.nets))
    board.mark("stackup", bool(board.stackup),
               None if board.stackup else "no (setup (stackup ...)) block: stackup is "
                                          "stored but this file has not been through the "
                                          "Board Setup dialog")
    board.mark("design_rules",
               bool(rules.get("source") or rules.get("net_class_source")),
               None if (rules.get("source") or rules.get("net_class_source"))
               else "no .kicad_pro and no (net_class ...) block in the board file")
    board.mark("silk",
               bool(board.silk or any(f.get("silk") for f in board.footprints)),
               None if (board.silk or any(f.get("silk") for f in board.footprints))
               else "no item names a *.SilkS layer")
    _mask_items = sum(len(f.get("mask") or []) for f in board.footprints) + \
        len(board.mask_apertures)
    _mask_pads = sum(1 for f in board.footprints for p in (f.get("pads") or [])
                     if p.get("mask_layers"))
    board.mark("pad_to_mask", bool(_mask_items or _mask_pads),
               None if (_mask_items or _mask_pads)
               else "no pad and no graphic names a *.Mask layer, so there is no "
                    "solder-mask opening to measure")
    if _mask_items or _mask_pads:
        board.present["pad_to_mask"]["expansion"] = {
            "rule": "pad (solder_mask_margin) > footprint (solder_mask_margin) > "
                    "(setup (pad_to_mask_clearance ...)), absent = 0",
            "board_default_mm": float(mask_expansion),
            "pads_from": _mask_source_counts(board),
        }
    board.mark("component_3d", any(child(n, "model") is not None
                                   for n in children(tree, "footprint")),
               "3D model presence is recorded, geometry is not evaluated")
    for item in rules.get("absent") or []:
        board.declare_absent(item["fact"], item["reason"])
    if not board.stackup:
        board.declare_absent("stackup",
                             "kicad_pcb stores the stackup only if Board Setup was used; "
                             "no default thickness/er is substituted")
    board.resolve_nets()
    return board
