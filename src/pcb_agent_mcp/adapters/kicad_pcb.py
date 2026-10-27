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
import os

from .canonical import Board
from .. import geometry as G

# --------------------------------------------------------------- tokenizer
_ESCAPES = {"n": "\n", "t": "\t", "\\": "\\", '"': '"'}


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
                    buf.append(_ESCAPES.get(nxt, nxt))
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
    return G.arc_to_points((ux, uy), r, a0, sweep)


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
                    pts = G.arc_to_points(center, r, a0, ang)
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
def _pad_from_node(pad_node):
    number = atom(pad_node, 1, "") or ""
    kind = atom(pad_node, 2, "smd") or "smd"          # smd | thru_hole | np_thru_hole | connect
    shape = atom(pad_node, 3, "rect") or "rect"
    at = child(pad_node, "at") or []
    x, y = xy(at)
    rot = num(at, 3, 0.0) or 0.0
    size = child(pad_node, "size")
    w = num(size, 1, 0.0) or 0.0
    h = num(size, 2, w) if num(size, 2) is not None else w
    drill = child(pad_node, "drill")
    hole = None
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
    if shape == "roundrect":
        shape = "rect"        # corner radius is not measurable by the Oracle yet
    elif shape in ("custom",):
        pts = None
        opts = child(pad_node, "primitives") or child(pad_node, "options")
        for prim in (opts or []):
            if isinstance(prim, list) and prim and prim[0] in ("gr_poly",):
                pn = child(prim, "pts")
                pts = [xy(p) for p in children(pn, "xy")] if pn else None
        if pts:
            cx = sum(p[0] for p in pts) / len(pts)
            cy = sum(p[1] for p in pts) / len(pts)
            return {"number": number, "net": net_name or net_code, "x": cx, "y": cy,
                    "w": (max(p[0] for p in pts) - min(p[0] for p in pts)),
                    "h": (max(p[1] for p in pts) - min(p[1] for p in pts)),
                    "shape": "custom", "rotation": rot, "layers": layers,
                    "hole_mm": hole, "smd": kind == "smd",
                    "polygon": [(p[0] - cx, p[1] - cy) for p in pts]}
    return {"number": number, "net": net_name or net_code, "x": x, "y": y,
            "w": w, "h": h, "shape": shape, "rotation": rot, "layers": layers,
            "hole_mm": hole, "smd": (kind == "smd")}


def _footprint(node):
    lib_id = atom(node, 1, "")
    layer = atom(child(node, "layer"), 1, "F.Cu")
    at = child(node, "at")
    x, y = xy(at)
    rot = num(at, 3, 0.0) or 0.0
    # Y flip reverses the sense of rotation
    rot = -rot
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
    # pads are stored relative to the footprint origin; place them in board space
    import math
    ca, sa = math.cos(math.radians(rot)), math.sin(math.radians(rot))
    for pad in pads:
        px, py = pad["x"], pad["y"]
        pad["x"] = x + px * ca - py * sa
        pad["y"] = y + px * sa + py * ca
        pad["rotation"] = rot + (pad.get("rotation") or 0.0)
        pad["ref"] = ref
    bbox = None
    if pads:
        xs, ys = [], []
        for pad in pads:
            half = max(pad["w"], pad["h"]) / 2.0
            xs.extend([pad["x"] - half, pad["x"] + half])
            ys.extend([pad["y"] - half, pad["y"] + half])
        bbox = (min(xs), min(ys), max(xs), max(ys))
    return {"ref": ref, "value": value, "lib_id": lib_id, "x": round(x, 4),
            "y": round(y, 4), "rotation": rot, "layer": layer,
            "bbox": _bbox_dict(bbox), "pads": pads,
            "dnp": child(node, "dnp") is not None}


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
    }


# ---------------------------------------------- side files: rules / stackup
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
    classes = (data.get("net_settings") or {}).get("classes") or []
    for cls in classes:
        out["net_classes"][cls.get("name") or "Default"] = {
            "clearance": cls.get("clearance"),
            "track_width": cls.get("track_width"),
            "via_diameter": cls.get("via_diameter"),
            "via_drill": cls.get("via_drill"),
            "nets": cls.get("nets") or [],
        }
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

    for fp_node in children(tree, "footprint") + children(tree, "module"):
        board.footprints.append(_footprint(fp_node))

    for seg in children(tree, "segment"):
        board.tracks.append({
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
            "net": atom(child(via, "net"), 1),
            "x": xy(child(via, "at"))[0],
            "y": xy(child(via, "at"))[1],
            "drill": num(child(via, "drill"), 1, 0.0),
            "diameter": num(child(via, "size"), 1, 0.0),
            "from": lname[0] if lname else None,
            "to": lname[-1] if len(lname) > 1 else (lname[0] if lname else None),
            "kind": vtype if vtype in ("blind", "micro") else "through",
        })

    for znode in children(tree, "zone"):
        board.zones.append(_zone(znode))

    polys, contours = _edge_polygons(tree)
    if polys:
        # gr_rect / gr_circle / gr_poly: first is the outer contour by convention
        board.outline = [polys[0]] + polys[1:]
    if contours:
        if not board.outline:
            outer = max(contours, key=lambda p: abs(G.polygon_area(p)))
            board.outline = [outer]
            board.cutouts = [c for c in contours if c is not outer]
        else:
            board.cutouts.extend(contours)

    board.stackup = _stackup(tree, thickness)
    rules = load_project_rules(project_path or path)
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
    board.mark("design_rules", bool(rules.get("source")),
               None if rules.get("source") else "rules live in .kicad_pro")
    board.mark("pad_to_mask", False, "mask expansion lives in (setup (pad_to_mask_clearance)) "
                                     "and per-pad (solder_mask_margin); parsed only on demand")
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
