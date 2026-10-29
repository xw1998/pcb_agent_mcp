"""Board operations: the vocabulary the write path accepts.

Every operation has three parts, and the third is the one that makes writing
safe:

* `run(ed, op)`    - mutate the (private copy of the) tree
* `verify(ed, op)` - re-read the **file that was just written** and check the
                     postcondition. "no exception was raised" is not evidence.
* `affects`        - what the operation is allowed to change, so the vendor
                     engine's output (Gerbers) can act as a second witness: an
                     operation that claims not to touch copper must leave the
                     plotted copper byte-identical.

Coordinates are **millimetres, Y up** - the same frame `board_view` reports, so
a coordinate read from a view can be passed straight back in.

Anything that is not in `OPS` is refused. Anything whose layer or net is not in
the file is refused (a typo'd layer name silently creates a drawing on nothing).
"""
import os

from ..fidelity import sexpr as S
from . import build as B

class OpError(ValueError):
    def __init__(self, message, code="OP_INVALID", **extra):
        ValueError.__init__(self, message)
        self.code = code
        self.extra = extra

OUTLINE_LAYER = "Edge.Cuts"
MECH_LAYERS_RO = ("Dwgs.User", "Cmts.User", "User.Drawings", "User.Comments",
                  "User.Eco1", "User.Eco2", "User.1", "User.2", "User.3",
                  "User.4", "User.5", "User.6", "User.7", "User.8", "User.9")
SILK_LAYERS = ("F.SilkS", "B.SilkS")

# ----------------------------------------------------------------- editor
class Editor(object):
    """A mutable board tree with the lookups the operations need."""

    def __init__(self, doc):
        self.doc = doc
        self.board = doc.board()
        version = S.arg_text(S.child(self.board, "version"), 1)
        self.sp = B.Spelling(version)
        self.layers = self._layers()
        self.net_by_name = {}
        self.name_by_code = {}
        for node in self.items("net"):
            code = S.arg_text(node, 1)
            name = S.arg_text(node, 2) or ""
            self.net_by_name[name] = code
            self.name_by_code[code] = name

    # ---------------------------------------------------------- lookups
    def _layers(self):
        out = set()
        node = S.child(self.board, "layers")
        if node is not None:
            for item in node.items[1:]:
                if isinstance(item, S.Seq) and len(item.items) > 1:
                    name = item.items[1]
                    if isinstance(name, S.Atom):
                        out.add(name.text)
        return out

    def items(self, name=None):
        out = []
        for item in self.board.items[1:]:
            if isinstance(item, S.Seq) and (name is None or S.head(item) == name):
                out.append(item)
        return out

    def require_layer(self, layer, allowed=None):
        if layer not in self.layers:
            raise OpError("layer %r is not declared in this board's (layers) table "
                          "(%d layers: %s)" % (layer, len(self.layers),
                                               ", ".join(sorted(self.layers)[:12])),
                          "LAYER_NOT_IN_BOARD")
        if allowed and layer not in allowed:
            raise OpError("%r is not one of the layers this operation may write "
                          "(%s)" % (layer, ", ".join(allowed)), "LAYER_NOT_ALLOWED")
        return layer

    def net_code(self, name, allow_create=False):
        if name in self.net_by_name:
            return self.net_by_name[name]
        if allow_create:
            code = str(max([int(c) for c in self.name_by_code if (c or "").isdigit()]
                           or [0]) + 1)
            self.append(B.L(B.A("net"), B.A(code), B.A(name, True)))
            self.net_by_name[name] = code
            self.name_by_code[code] = name
            return code
        raise OpError("net %r does not exist in this board (nets: %s)"
                      % (name, ", ".join(sorted(n for n in self.net_by_name if n)[:12])),
                      "NET_NOT_FOUND")

    def footprints(self):
        return self.items("footprint") or self.items("module")

    def footprint_ref(self, node):
        for t in S.children(node, "fp_text"):
            if S.arg_text(t, 1) == "reference":
                return S.arg_text(t, 2)
        for p in S.children(node, "property"):
            if S.arg_text(p, 1) in ("Reference", "reference"):
                return S.arg_text(p, 2)
        return None

    def footprint(self, ref):
        for node in self.footprints():
            if self.footprint_ref(node) == ref:
                return node
        raise OpError("no footprint with reference %r on this board" % ref,
                      "REFERENCE_NOT_FOUND")

    # ---------------------------------------------------------- mutation
    def append(self, node):
        # indent like its siblings: a hand-built node carries no trivia, and an
        # unindented insert turns one line of the diff into the whole block
        S.append_child(self.board, node)

    def remove(self, node):
        for i, item in enumerate(self.board.items):
            if item is node:
                del self.board.items[i]
                return True
        return False

    def edge_items(self):
        out = []
        for node in self.items():
            if S.arg_text(S.child(node, "layer"), 1) == OUTLINE_LAYER:
                out.append(node)
        return out

# ----------------------------------------------------------------- params
def _num(op, key, required=True, default=None, positive=False):
    v = op.get(key, default)
    if v is None:
        if required:
            raise OpError("missing parameter %r" % key, "ARG_MISSING")
        return None
    try:
        v = float(v)
    except (TypeError, ValueError):
        raise OpError("parameter %r must be a number, got %r" % (key, v), "ARG_INVALID")
    if v != v:
        raise OpError("parameter %r is NaN" % key, "ARG_INVALID")
    if abs(v) > 1e5:
        raise OpError("parameter %r = %r mm is outside any plausible board"
                      % (key, v), "ARG_INVALID")
    if positive and v <= 0:
        raise OpError("parameter %r must be > 0, got %r" % (key, v), "ARG_INVALID")
    return v

def _str(op, key, required=True, default=None):
    v = op.get(key, default)
    if v is None or v == "":
        if required:
            raise OpError("missing parameter %r" % key, "ARG_MISSING")
        return None
    if not isinstance(v, str):
        raise OpError("parameter %r must be a string" % key, "ARG_INVALID")
    return v

def _bool(op, key, default=False):
    v = op.get(key, default)
    return bool(v)

def _points(op, key="points"):
    raw = op.get(key)
    if not isinstance(raw, (list, tuple)) or len(raw) < 3:
        raise OpError("parameter 'points' must be a list of at least 3 [x, y] "
                      "pairs (mm, Y up)", "ARG_INVALID")
    out = []
    for p in raw:
        if not isinstance(p, (list, tuple)) or len(p) != 2:
            raise OpError("every point must be [x, y], got %r" % (p,), "ARG_INVALID")
        out.append((_num({"x": p[0]}, "x"), _num({"y": p[1]}, "y")))
    return out

def _rect(op, prefix=""):
    x0 = _num(op, prefix + "x0")
    y0 = _num(op, prefix + "y0")
    x1 = _num(op, prefix + "x1")
    y1 = _num(op, prefix + "y1")
    if abs(x1 - x0) < 1e-6 or abs(y1 - y0) < 1e-6:
        raise OpError("degenerate rectangle: width or height is zero", "ARG_INVALID")
    return (min(x0, x1), min(y0, y1), max(x0, x1), max(y0, y1))

def _layer(op, ed, key="layer", allowed=None):
    return ed.require_layer(_str(op, key), allowed)

def _width(op, ed, default=None):
    w = _num(op, "width", required=False, default=default)
    if w is None:
        cls = _default_netclass(ed)
        w = (cls or {}).get("trace_width") or 0.1
    if w <= 0:
        raise OpError("width must be > 0", "ARG_INVALID")
    return w

def _default_netclass(ed):
    for node in ed.items("net_class"):
        out = {"name": S.arg_text(node, 1)}
        for knob, names in (("clearance", ("clearance",)),
                            ("trace_width", ("trace_width", "track_width")),
                            ("via_dia", ("via_dia", "via_diameter")),
                            ("via_drill", ("via_drill",))):
            for name in names:
                n = S.child(node, name)
                if n is not None and S.arg_num(n, 1) is not None:
                    out[knob] = S.arg_num(n, 1)
                    break
        return out
    return None

# ----------------------------------------------------------------- shapes
def _obround(p1, p2, width, sp, seed):
    """Two straight sides + two 180-degree arcs, i.e. the shape a router cuts."""
    x1, y1 = p1
    x2, y2 = p2
    r = width / 2.0
    dx, dy = x2 - x1, y2 - y1
    d = (dx * dx + dy * dy) ** 0.5
    if d <= 1e-9:
        raise OpError("a slot needs two distinct end points", "ARG_INVALID")
    if width <= 0:
        raise OpError("slot width must be > 0", "ARG_INVALID")
    ux, uy = dx / d, dy / d
    nx, ny = -uy, ux
    nodes = []
    nodes.append(B.line2("gr_line", x1 + nx * r, y1 + ny * r, x2 + nx * r,
                         y2 + ny * r, OUTLINE_LAYER, 0.1, sp, B.new_id(seed + ":a")))
    nodes.append(B.arc3("gr_arc", x2 + nx * r, y2 + ny * r, x2 + ux * r, y2 + uy * r,
                        x2 - nx * r, y2 - ny * r, OUTLINE_LAYER, 0.1, sp,
                        B.new_id(seed + ":b")))
    nodes.append(B.line2("gr_line", x2 - nx * r, y2 - ny * r, x1 - nx * r,
                         y1 - ny * r, OUTLINE_LAYER, 0.1, sp, B.new_id(seed + ":c")))
    nodes.append(B.arc3("gr_arc", x1 - nx * r, y1 - ny * r, x1 - ux * r, y1 - uy * r,
                        x1 + nx * r, y1 + ny * r, OUTLINE_LAYER, 0.1, sp,
                        B.new_id(seed + ":d")))
    return nodes

def _circle_nodes(cx, cy, r, layer, width, sp, seed, kind="gr_circle"):
    node = B.L(B.A(kind), B.L(B.A("center"), B.A(B.num(cx)), B.A(B.num(B.y_file(cy)))),
               B.L(B.A("end"), B.A(B.num(cx + r)), B.A(B.num(B.y_file(cy)))),
               B.L(B.A("layer"), B.A(layer, True)),
               B.L(B.A("width"), B.A(B.num(width))),
               sp.id_node(B.new_id(seed)))
    return [node]

def _polygon_nodes(points, layer, width, sp, seed, close=True):
    nodes = []
    if close and len(points) >= 3:
        nodes.append(B.L(B.A("gr_poly"), B.pts(points),
                         B.L(B.A("layer"), B.A(layer, True)),
                         B.L(B.A("width"), B.A(B.num(width))),
                         sp.id_node(B.new_id(seed + ":poly"))))
        return nodes
    for i in range(len(points) - 1):
        (ax, ay), (bx, by) = points[i], points[i + 1]
        nodes.append(B.line2("gr_line", ax, ay, bx, by, layer, width, sp,
                             B.new_id("%s:%d" % (seed, i))))
    return nodes

# ----------------------------------------------------------------- ops
OPS = {}

def op(name, affects, summary, params, run, verify):
    # `affects` is normalised to a sorted list here, not at describe() time: the
    # capability report is JSON, and a set would make it unserialisable.
    if isinstance(affects, str):
        affects = [affects]
    OPS[name] = {"affects": sorted(affects), "summary": summary,
                 "params": params, "run": run, "verify": verify}

def describe():
    """Machine-readable list for the capability report."""
    out = []
    for name, spec in OPS.items():
        out.append({"op": name, "affects": spec["affects"],
                    "summary": spec["summary"], "params": spec["params"]})
    return out

# --- board frame ------------------------------------------------------
def _run_outline_rect(ed, o, seed):
    x0, y0, x1, y1 = _rect(o)
    w = _width(o, ed, default=0.1)
    nodes = B.rect_as_lines(x0, y0, x1, y1, OUTLINE_LAYER, w, ed.sp, seed)
    for n in nodes:
        ed.append(n)
    return {"nodes_added": len(nodes), "bbox": [x0, y0, x1, y1]}

def _verify_outline_rect(ed, o):
    x0, y0, x1, y1 = _rect(o)
    want = {(round(x0, 4), round(y0, 4)), (round(x1, 4), round(y0, 4)),
            (round(x1, 4), round(y1, 4)), (round(x0, 4), round(y1, 4))}
    got = set()
    for node in ed.edge_items():
        if S.head(node) != "gr_line":
            continue
        st, en = S.child(node, "start"), S.child(node, "end")
        if st is None or en is None:
            continue
        for pair in ((st, en), (en, st)):
            p = (round(S.arg_num(pair[0], 1, 0.0), 4),
                 round(-S.arg_num(pair[0], 2, 0.0), 4))
            if p in want:
                got.add(p)
    return {"ok": got == want, "corners_found": len(got), "corners_expected": 4}

op("add_outline_rect", {"outline"},
   "Board outline as a single closed rectangle on Edge.Cuts (4 segments).",
   {"x0": "mm", "y0": "mm", "x1": "mm", "y1": "mm", "width": "mm (default: netclass)"},
   _run_outline_rect, _verify_outline_rect)

def _run_outline_polygon(ed, o, seed):
    points = _points(o)
    w = _width(o, ed, default=0.1)
    if _bool(o, "closed", True) and (points[0] != points[-1]):
        points = points + [points[0]]
    nodes = _polygon_nodes(points, OUTLINE_LAYER, w, ed.sp, seed,
                           close=_bool(o, "closed", True))
    for n in nodes:
        ed.append(n)
    return {"nodes_added": len(nodes), "vertices": len(points)}

def _verify_outline_polygon(ed, o):
    points = _points(o)
    xs = [p[0] for p in points]
    ys = [p[1] for p in points]
    found = False
    for node in ed.edge_items():
        if S.head(node) != "gr_poly":
            continue
        pts_node = S.child(node, "pts")
        if pts_node is None:
            continue
        got = [(round(S.arg_num(p, 1, 0.0), 4), round(-S.arg_num(p, 2, 0.0), 4))
               for p in S.children(pts_node, "xy")]
        if got and abs(max(x for x, _ in got) - max(xs)) < 1e-4 \
                and abs(min(y for _, y in got) - min(ys)) < 1e-4:
            found = True
    return {"ok": found, "expected_bbox": [min(xs), min(ys), max(xs), max(ys)]}

op("add_outline_polygon", {"outline"},
   "Board outline as a closed polygon on Edge.Cuts (gr_poly).",
   {"points": "[[x,y], ...] mm Y-up, >= 3 points", "width": "mm"},
   _run_outline_polygon, _verify_outline_polygon)

def _run_outline_circle(ed, o, seed):
    cx, cy = _num(o, "cx"), _num(o, "cy")
    r = _num(o, "r", positive=True)
    w = _width(o, ed, default=0.1)
    for n in _circle_nodes(cx, cy, r, OUTLINE_LAYER, w, ed.sp, seed):
        ed.append(n)
    return {"nodes_added": 1, "radius": r}

def _verify_outline_circle(ed, o):
    cx, cy, r = _num(o, "cx"), _num(o, "cy"), _num(o, "r")
    for node in ed.edge_items():
        if S.head(node) != "gr_circle":
            continue
        c = S.child(node, "center")
        e = S.child(node, "end")
        if c is None or e is None:
            continue
        if (abs(S.arg_num(c, 1, 0) - cx) < 1e-4 and
                abs(-S.arg_num(c, 2, 0) - cy) < 1e-4 and
                abs((S.arg_num(e, 1, 0) - S.arg_num(c, 1, 0)) - r) < 1e-4):
            return {"ok": True}
    return {"ok": False}

op("add_outline_circle", {"outline"},
   "Circular board outline on Edge.Cuts (gr_circle).",
   {"cx": "mm", "cy": "mm", "r": "mm > 0", "width": "mm"},
   _run_outline_circle, _verify_outline_circle)

def _run_outline_line(ed, o, seed):
    w = _width(o, ed, default=0.1)
    node = B.line2("gr_line", _num(o, "x1"), _num(o, "y1"), _num(o, "x2"),
                   _num(o, "y2"), OUTLINE_LAYER, w, ed.sp, B.new_id(seed))
    ed.append(node)
    return {"nodes_added": 1}

def _verify_outline_line(ed, o):
    want = {(round(_num(o, "x1"), 4), round(_num(o, "y1"), 4)),
            (round(_num(o, "x2"), 4), round(_num(o, "y2"), 4))}
    for node in ed.edge_items():
        if S.head(node) != "gr_line":
            continue
        st, en = S.child(node, "start"), S.child(node, "end")
        if st is None or en is None:
            continue
        got = {(round(S.arg_num(st, 1, 0), 4), round(-S.arg_num(st, 2, 0), 4)),
               (round(S.arg_num(en, 1, 0), 4), round(-S.arg_num(en, 2, 0), 4))}
        if got == want:
            return {"ok": True}
    return {"ok": False}

op("add_outline_line", {"outline"},
   "One straight Edge.Cuts segment (for building or extending an outline).",
   {"x1": "mm", "y1": "mm", "x2": "mm", "y2": "mm", "width": "mm"},
   _run_outline_line, _verify_outline_line)

def _run_outline_arc(ed, o, seed):
    w = _width(o, ed, default=0.1)
    node = B.arc3("gr_arc", _num(o, "sx"), _num(o, "sy"), _num(o, "mx"),
                  _num(o, "my"), _num(o, "ex"), _num(o, "ey"), OUTLINE_LAYER, w,
                  ed.sp, B.new_id(seed))
    ed.append(node)
    return {"nodes_added": 1}

def _verify_outline_arc(ed, o):
    want = (round(_num(o, "sx"), 4), round(_num(o, "sy"), 4),
            round(_num(o, "ex"), 4), round(_num(o, "ey"), 4))
    for node in ed.edge_items():
        if S.head(node) != "gr_arc":
            continue
        st, en = S.child(node, "start"), S.child(node, "end")
        if st is None or en is None:
            continue
        got = (round(S.arg_num(st, 1, 0), 4), round(-S.arg_num(st, 2, 0), 4),
               round(S.arg_num(en, 1, 0), 4), round(-S.arg_num(en, 2, 0), 4))
        if got == want:
            return {"ok": True}
    return {"ok": False}

op("add_outline_arc", {"outline"},
   "One Edge.Cuts arc, given as start / mid / end (all three on the arc).",
   {"sx": "mm", "sy": "mm", "mx": "mm", "my": "mm", "ex": "mm", "ey": "mm"},
   _run_outline_arc, _verify_outline_arc)

def _run_slot(ed, o, seed):
    p1 = (_num(o, "x1"), _num(o, "y1"))
    p2 = (_num(o, "x2"), _num(o, "y2"))
    width = _num(o, "slot_width", positive=True)
    nodes = _obround(p1, p2, width, ed.sp, seed)
    for n in nodes:
        ed.append(n)
    return {"nodes_added": len(nodes), "slot_width": width,
            "length_center_to_center": ((p2[0] - p1[0]) ** 2 + (p2[1] - p1[1]) ** 2) ** 0.5}

def _verify_slot(ed, o):
    """A slot is 2 lines + 2 arcs whose arc mid points sit on the slot axis."""
    arcs = [n for n in ed.edge_items() if S.head(n) == "gr_arc"]
    lines = [n for n in ed.edge_items() if S.head(n) == "gr_line"]
    want_r = _num(o, "slot_width") / 2.0
    ok = False
    for a in arcs:
        st, en = S.child(a, "start"), S.child(a, "end")
        if st is None or en is None:
            continue
        chord = ((S.arg_num(st, 1, 0) - S.arg_num(en, 1, 0)) ** 2 +
                 (S.arg_num(st, 2, 0) - S.arg_num(en, 2, 0)) ** 2) ** 0.5
        if abs(chord - 2 * want_r) < 1e-3:
            ok = True
    return {"ok": ok and len(arcs) >= 2 and len(lines) >= 2,
            "arcs": len(arcs), "lines": len(lines),
            "expected_arc_chord": 2 * want_r}

op("add_slot", {"outline"},
   "A routed slot (obround: 2 sides + 2 arcs) on Edge.Cuts - the standard way to "
   "ask a board house to mill a slot rather than drill a hole.",
   {"x1": "mm", "y1": "mm", "x2": "mm", "y2": "mm", "slot_width": "mm > 0"},
   _run_slot, _verify_slot)

# --- cutouts ----------------------------------------------------------
def _run_cutout_rect(ed, o, seed):
    x0, y0, x1, y1 = _rect(o)
    nodes = B.rect_as_lines(x0, y0, x1, y1, OUTLINE_LAYER, 0.1, ed.sp, seed)
    for n in nodes:
        ed.append(n)
    return {"nodes_added": len(nodes), "bbox": [x0, y0, x1, y1],
            "meaning": "a closed loop on Edge.Cuts inside the board is a cutout"}

def _verify_cutout_rect(ed, o):
    return _verify_outline_rect(ed, o)

op("add_cutout_rect", {"outline"},
   "Rectangular hole milled through the board (a closed Edge.Cuts loop inside the "
   "outline).",
   {"x0": "mm", "y0": "mm", "x1": "mm", "y1": "mm"},
   _run_cutout_rect, _verify_cutout_rect)

def _run_cutout_circle(ed, o, seed):
    cx, cy = _num(o, "cx"), _num(o, "cy")
    r = _num(o, "r", positive=True)
    for n in _circle_nodes(cx, cy, r, OUTLINE_LAYER, 0.1, ed.sp, seed):
        ed.append(n)
    return {"nodes_added": 1, "radius": r}

op("add_cutout_circle", {"outline"},
   "Circular hole milled through the board (gr_circle on Edge.Cuts).",
   {"cx": "mm", "cy": "mm", "r": "mm > 0"},
   _run_cutout_circle, _verify_outline_circle)

def _run_cutout_polygon(ed, o, seed):
    points = _points(o)
    if points[0] != points[-1]:
        points = points + [points[0]]
    for n in _polygon_nodes(points, OUTLINE_LAYER, 0.1, ed.sp, seed, close=True):
        ed.append(n)
    return {"nodes_added": 1, "vertices": len(points)}

op("add_cutout_polygon", {"outline"},
   "Arbitrary polygonal hole milled through the board (closed polygon on Edge.Cuts).",
   {"points": "[[x,y], ...] mm Y-up, >= 3 points"},
   _run_cutout_polygon, _verify_outline_polygon)

# --- mechanical / documentation layers --------------------------------
def _run_mech_line(ed, o, seed):
    layer = _layer(o, ed, allowed=list(MECH_LAYERS_RO) + list(SILK_LAYERS))
    w = _num(o, "width", required=False, default=0.15)
    ed.append(B.line2("gr_line", _num(o, "x1"), _num(o, "y1"), _num(o, "x2"),
                      _num(o, "y2"), layer, w, ed.sp, B.new_id(seed)))
    return {"nodes_added": 1, "layer": layer}

def _verify_mech_line(ed, o):
    layer = _str(o, "layer")
    for node in ed.items("gr_line"):
        if S.arg_text(S.child(node, "layer"), 1) != layer:
            continue
        st, en = S.child(node, "start"), S.child(node, "end")
        if st is None or en is None:
            continue
        if (abs(S.arg_num(st, 1, 0) - _num(o, "x1")) < 1e-4 and
                abs(-S.arg_num(st, 2, 0) - _num(o, "y1")) < 1e-4):
            return {"ok": True}
    return {"ok": False}

op("add_mech_line", {"mech"},
   "A line on a documentation/mechanical user layer (Dwgs.User, Cmts.User, silk).",
   {"x1": "mm", "y1": "mm", "x2": "mm", "y2": "mm", "layer": "layer name",
    "width": "mm"},
   _run_mech_line, _verify_mech_line)

def _run_mech_rect(ed, o, seed):
    layer = _layer(o, ed, allowed=list(MECH_LAYERS_RO) + list(SILK_LAYERS))
    w = _num(o, "width", required=False, default=0.15)
    x0, y0, x1, y1 = _rect(o)
    for n in B.rect_as_lines(x0, y0, x1, y1, layer, w, ed.sp, seed):
        ed.append(n)
    return {"nodes_added": 4, "layer": layer}

def _verify_mech_rect(ed, o):
    layer = _str(o, "layer")
    x0, y0, x1, y1 = _rect(o)
    corners = {(round(x0, 4), round(y0, 4)), (round(x1, 4), round(y1, 4))}
    found = set()
    for node in ed.items("gr_line"):
        if S.arg_text(S.child(node, "layer"), 1) != layer:
            continue
        for name in ("start", "end"):
            n = S.child(node, name)
            if n is None:
                continue
            p = (round(S.arg_num(n, 1, 0), 4), round(-S.arg_num(n, 2, 0), 4))
            if p in corners:
                found.add(p)
    return {"ok": found == corners}

op("add_mech_rect", {"mech"},
   "A rectangle on a documentation/mechanical user layer (keep-out of a connector, "
   "assembly note box, ...).",
   {"x0": "mm", "y0": "mm", "x1": "mm", "y1": "mm", "layer": "layer name",
    "width": "mm"},
   _run_mech_rect, _verify_mech_rect)

def _run_mech_circle(ed, o, seed):
    layer = _layer(o, ed, allowed=list(MECH_LAYERS_RO) + list(SILK_LAYERS))
    for n in _circle_nodes(_num(o, "cx"), _num(o, "cy"), _num(o, "r", positive=True),
                           layer, 0.15, ed.sp, seed):
        ed.append(n)
    return {"nodes_added": 1, "layer": layer}

op("add_mech_circle", {"mech"},
   "A circle on a documentation/mechanical user layer.",
   {"cx": "mm", "cy": "mm", "r": "mm > 0", "layer": "layer name"},
   _run_mech_circle, lambda ed, o: {"ok": any(
       S.head(n) == "gr_circle" and S.arg_text(S.child(n, "layer"), 1) == _str(o, "layer")
       for n in ed.items("gr_circle"))})

def _run_board_text(ed, o, seed):
    layer = _layer(o, ed, allowed=list(MECH_LAYERS_RO) + list(SILK_LAYERS))
    h = _num(o, "height", required=False, default=1.0)
    th = _num(o, "thickness", required=False, default=0.15)
    ed.append(B.text_node(_str(o, "text"), _num(o, "x"), _num(o, "y"), layer, h,
                          th, ed.sp, B.new_id(seed),
                          rot=_num(o, "rot", required=False)))
    return {"nodes_added": 1, "layer": layer, "text": _str(o, "text")}

def _verify_board_text(ed, o):
    text = _str(o, "text")
    for node in ed.items("gr_text"):
        if S.arg_text(node, 1) == text:
            return {"ok": True}
    return {"ok": False}

op("add_board_text", {"mech", "silk"},
   "Board-level text on silk or a documentation layer (revision, connector labels, "
   "assembly notes).",
   {"text": "string", "x": "mm", "y": "mm", "layer": "layer name",
    "height": "mm", "thickness": "mm", "rot": "deg"},
   _run_board_text, _verify_board_text)

# --- placement --------------------------------------------------------
_FOOTPRINT_DROP = ("version", "generator", "generator_version", "tedit",
                   "layer", "at", "tstamp", "uuid")

# What a footprint node may carry in the v7 (20221018) board format - read off
# KiCad's own complaint when a newer library footprint is dropped into an older
# board ("Expecting at, descr, locked, ... or component_classes"). A newer board
# has no such list to keep, so it embeds the body unchanged.
_FOOTPRINT_TOKENS = frozenset((
    "at", "descr", "locked", "placed", "tedit", "tstamp", "uuid", "variant",
    "autoplace_cost90", "autoplace_cost180", "attr", "clearance",
    "embedded_files", "fp_arc", "fp_circle", "fp_curve", "fp_line", "fp_poly",
    "fp_rect", "fp_text", "pad", "group", "generator", "model", "path",
    "solder_mask_margin", "solder_paste_margin", "solder_paste_margin_ratio",
    "tags", "thermal_gap", "version", "zone", "zone_connect",
    "component_classes"))
_NO_LIMIT = None

def _read_footprint_source(o):
    """The footprint body, from a .kicad_mod file or a .pretty directory.

    Nothing is invented here: pads, drills and silk come from the file, and a
    missing library entry is an error rather than an empty footprint that merely
    carries the right reference.
    """
    path = o.get("footprint_file") or o.get("library")
    if not path:
        raise OpError("add_footprint needs `library` (a .pretty directory) + `name`, "
                      "or `footprint_file` (one .kicad_mod) - refusing to invent a "
                      "footprint body", "MISSING_FOOTPRINT_DEFINITION")
    name = o.get("name")
    if os.path.isdir(path):
        if not name:
            raise OpError("`library` is a directory, so `name` (the footprint inside "
                          "it) is required", "ARG_MISSING")
        fpath = os.path.join(path, name + ".kicad_mod")
        lib = os.path.basename(os.path.normpath(path))
        if lib.endswith(".pretty"):
            lib = lib[:-len(".pretty")]
    else:
        fpath = path
        lib = os.path.basename(os.path.normpath(os.path.dirname(path)))
        if lib.endswith(".pretty"):
            lib = lib[:-len(".pretty")]
        name = name or os.path.splitext(os.path.basename(path))[0]
    if not os.path.isfile(fpath):
        raise OpError("footprint file does not exist: %r" % fpath, "FILE_NOT_FOUND")
    with open(fpath, "rb") as fh:
        data = fh.read()
    try:
        tree = S.parse(data.decode("utf-8", "surrogateescape"))
    except S.SexprError as exc:
        raise OpError("%s is not a valid .kicad_mod: %s"
                      % (os.path.basename(fpath), exc), "PARSE_FAILED")
    if not tree.items or S.head(tree.items[0]) not in ("footprint", "module"):
        raise OpError("%s is not a KiCad footprint (expected `(footprint ...)`)"
                      % fpath, "FORMAT_NOT_SUPPORTED")
    return tree.items[0], {"path": fpath, "library": lib, "name": name}

def _pad_numbers(fp):
    out = []
    for pad in S.children(fp, "pad"):
        number = S.arg_text(pad, 1)
        out.append("" if number is None else number)
    return out

def _copy_node(node):
    """A deep copy, so a library node can be re-used without being shared."""
    if isinstance(node, S.Seq):
        out = S.Seq(lead=node.lead, tail=node.tail)
        for item in node.items:
            out.items.append(_copy_node(item))
        return out
    return S.Atom(node.text, node.quoted, node.lead, node.raw)


def _lib_field(lib_node, name):
    """The library's own Reference/Value field, if it has one.

    Its position, layer and text size are the footprint author's choice - a
    value text stuck at a guessed offset lands on top of the pads and DRC says
    so (63 warnings on the first board this tool built). So it is copied, not
    invented; only the text is replaced later.
    """
    for kind in ("property", "fp_text"):
        for node in S.children(lib_node, kind):
            if (S.arg_text(node, 1) or "") in (name, name.lower()):
                return node
    return None


def _field_node(name, value, x, y_up, sp, seed, template=None, size=None):
    """A Reference/Value field spelled the way this board file expects."""
    at = B.at(x, y_up, 0)
    layer = B.L(B.A("layer"), B.A("F.SilkS", True))
    effects = B.L(B.A("effects"), B.L(B.A("font"), B.size(1, 1)))
    if template is not None:
        t_at = S.child(template, "at")
        t_layer = S.child(template, "layer")
        t_effects = S.child(template, "effects")
        if t_at is not None:
            at = _copy_node(t_at)
        if t_layer is not None:
            layer = _copy_node(t_layer)
        if t_effects is not None:
            effects = _copy_node(t_effects)
    if size:
        font = S.child(effects, "font")
        if font is None:
            font = B.L(B.A("font"))
            effects.items.append(font)
        node = S.child(font, "size")
        if node is None:
            font.items.append(B.size(float(size), float(size)))
        else:
            node.items[1].text = B.num(float(size))
            node.items[2].text = B.num(float(size))
    if sp.modern:
        return B.L(B.A("property"), B.A(name, True), B.A(value, True),
                   at, layer, sp.id_node(B.new_id(seed)), effects)
    return B.L(B.A("fp_text"), B.A(name.lower()), B.A(value, True), at,
               layer, sp.id_node(B.new_id(seed)), effects)

def _set_font_size(node, size):
    """Force a field's font size, adding `(size ...)` when the field has none.

    A field copied from the library keeps the position the footprint author
    chose, but `field_size` is the caller's decision: applying it only on the
    invented-field path made the argument silently do nothing whenever the
    library already carried a Reference/Value (measured, not assumed).
    """
    effects = S.child(node, "effects")
    if effects is None:
        effects = B.L(B.A("effects"))
        node.items.append(effects)
    font = S.child(effects, "font")
    if font is None:
        font = B.L(B.A("font"))
        effects.items.append(font)
    existing = S.child(font, "size")
    if existing is None:
        font.items.append(B.size(float(size), float(size)))
    else:
        existing.items[1].text = B.num(float(size))
        existing.items[2].text = B.num(float(size))

def _set_field(fp, name, value, x, y_up, sp, seed, template=None, size=None):
    """Replace an existing Reference/Value field in place, keeping its position."""
    for kind in ("property", "fp_text"):
        for node in S.children(fp, kind):
            if S.arg_text(node, 1) in (name, name.lower()) and len(node.items) > 2:
                node.items[2] = B.A(value, True)
                if size:
                    _set_font_size(node, size)
                return node
    node = _field_node(name, value, x, y_up, sp, seed, template=template, size=size)
    S.append_child(fp, node)
    return node

def _run_add_footprint(ed, o, seed):
    ref = _str(o, "ref")
    for node in ed.footprints():
        if ed.footprint_ref(node) == ref:
            raise OpError("reference %r is already on this board" % ref,
                          "REFERENCE_EXISTS")
    x = _num(o, "x")
    y = _num(o, "y")
    rot = _num(o, "rot", required=False, default=0)
    layer = _str(o, "layer", required=False, default="F.Cu")
    if layer not in ("F.Cu", "B.Cu"):
        raise OpError("a footprint sits on F.Cu or B.Cu, not %r" % layer,
                      "LAYER_NOT_ALLOWED")
    ed.require_layer(layer)
    lib_node, info = _read_footprint_source(o)
    lib_id = o.get("lib_id") or "%s:%s" % (info["library"], info["name"])
    value = o.get("value") or info["name"]
    pads = _pad_numbers(lib_node)
    if not pads:
        raise OpError("%s has no pads - that is not a placeable footprint"
                      % os.path.basename(info["path"]), "ARG_INVALID")

    pad_nets = o.get("pad_nets") or {}
    if not isinstance(pad_nets, dict):
        raise OpError("pad_nets must be an object {pad number: net name}",
                      "ARG_INVALID")
    unknown = sorted(set(pad_nets) - set(pads))
    if unknown:
        raise OpError("pad_nets names pad(s) %s that %s does not have (it has %d: %s)"
                      % (", ".join(unknown), info["name"], len(set(pads)),
                         ", ".join(sorted(set(pads))[:20])), "PAD_NOT_FOUND")

    allowed = _FOOTPRINT_TOKENS if not ed.sp.modern else _NO_LIMIT
    body = []
    dropped = []
    for child in lib_node.items[1:]:
        if isinstance(child, S.Atom):
            # the library footprint's own name atom: the header already took its
            # name from lib_id, and a second one is a syntax error
            continue
        if S.head(child) in _FOOTPRINT_DROP:
            continue
        if S.head(child) in ("property", "fp_text") \
                and S.arg_text(child, 1) in ("Reference", "Value"):
            continue
        if allowed is not None and S.head(child) not in allowed:
            # a token this board format does not define. Dropping it is a loss,
            # so it is reported rather than quietly swallowed.
            if S.head(child) not in dropped:
                dropped.append(S.head(child))
            continue
        body.append(child)

    # A back-side footprint is stored mirrored: KiCad writes `180 - angle` here
    # (measured against a file KiCad itself flipped). The angle the caller asks
    # for is the front-side angle; the number in the file is its mirror.
    file_rot = (180.0 - float(rot)) % 360.0 if layer == "B.Cu" else float(rot)
    node = B.L(B.A("footprint"), B.A(lib_id, True))
    node.items.append(B.L(B.A("layer"), B.A(layer, True)))
    node.items.append(ed.sp.id_node(B.new_id("%s:%s" % (seed, ref))))
    node.items.append(B.at(x, y, file_rot))
    node.items.extend(body)

    # Field positions inside a footprint are **relative to the footprint**, not
    # board coordinates - KiCad's own files show `(property "Reference" (at 0
    # -3.6 ...))` for a part sitting at 149 mm. Writing board coordinates here
    # parked the reference 20 mm away from the part.
    field_size = o.get("field_size")
    if field_size is not None:
        field_size = _num(o, "field_size", required=False)
        if field_size <= 0:
            raise OpError("field_size must be > 0 mm", "ARG_INVALID")
    _set_field(node, "Reference", ref, 0.0, 2.5, ed.sp, "%s:ref" % ref,
               template=_lib_field(lib_node, "Reference"), size=field_size)
    _set_field(node, "Value", value, 0.0, -2.5, ed.sp, "%s:val" % ref,
               template=_lib_field(lib_node, "Value"), size=field_size)

    # Pad nets: an explicit map, applied to pads that exist. A pad the map does
    # not name stays net-less (unconnected) and DRC reports it - that is the
    # truth, not a blank.
    # A pad's `(at dx dy angle)` angle in a .kicad_pcb is **absolute**, while its
    # position is relative to the footprint. Copying a library pad verbatim into
    # a rotated footprint therefore leaves the pad pointing the old way - on the
    # USB connector that made five 0.4 x 1.35 mm pads 0.65 mm apart overlap by
    # 0.7 mm, which DRC reported as a short. So the rotation is added here.
    if layer == "B.Cu":
        _flip_body(node, rot)
    elif rot:
        for pad in S.children(node, "pad"):
            _rotate_pad(pad, rot)
        # Field angles are stored **absolute** too (a library field says 0, a
        # footprint rotated 90 must say 90). Leaving them at 0 turns a vertical
        # label horizontal, and on the 1x20 headers that stuck 4 mm out of the
        # board edge.
        for kind in ("fp_text", "property"):
            for field in S.children(node, kind):
                at = S.child(field, "at")
                if at is not None:
                    _set_angle(at, (S.arg_num(at, 3, 0.0) or 0.0) + rot)

    assigned = {}
    for pad in S.children(node, "pad"):
        number = S.arg_text(pad, 1) or ""
        for old in list(S.children(pad, "net")):
            pad.items.remove(old)
        if number in pad_nets:
            net_name = pad_nets[number]
            if not isinstance(net_name, str) or not net_name:
                raise OpError("pad_nets[%r] must be a net name" % number, "ARG_INVALID")
            code = ed.net_code(net_name, allow_create=_bool(o, "create_nets", False))
            pad.items.append(B.L(B.A("net"), B.A(code), B.A(net_name, True)))
            assigned[number] = [code, net_name]
    ed.append(node)
    return {"reference": ref, "lib_id": lib_id, "at": [x, y, rot],
            "rotation_in_file": file_rot,
            "layer": layer, "pads": len(pads), "pad_numbers": sorted(set(pads)),
            "nets_assigned": assigned, "source": info["path"],
            "dropped_for_this_board_format": dropped}

_LAYER_FLIP = {
    "F.Cu": "B.Cu", "F.SilkS": "B.SilkS", "F.Mask": "B.Mask",
    "F.Paste": "B.Paste", "F.CrtYd": "B.CrtYd", "F.Fab": "B.Fab",
    "F.Adhes": "B.Adhes", "F.User": "B.User",
}
_LAYER_FLIP.update({v: k for k, v in list(_LAYER_FLIP.items())})
# layers that mean "both sides" never flip
_LAYER_BOTH = (".Cu", ".Mask", ".Paste", ".SilkS")


def _set_angle(node, value):
    """Write the angle argument of an `(at x y angle)` / `(center ...)` node."""
    value = float(value) % 360.0
    if abs(value) < 1e-9:
        value = 0.0
    if len(node.items) > 3:
        node.items[3].text = B.num(value)
    else:
        node.items.append(B.A(B.num(value)))


def _mirror_justify(effects):
    """Make text render mirrored - what KiCad writes for back-side text."""
    for node in S.children(effects, "justify"):
        if not any(isinstance(it, S.Atom) and it.text == "mirror"
                   for it in node.items[1:]):
            node.items.append(B.A("mirror"))
        return
    S.append_child(effects, B.L(B.A("justify"), B.A("mirror")))


def _flip_body(node, rot):
    """Turn a front-side library body into a back-side one, the way KiCad does.

    This is measured, not reasoned: a footprint flipped by KiCad itself (KiCad
    10.0 `pcbnew.FOOTPRINT.Flip(pos, False)`, then saved) writes

      * footprint `(at x y ang)` with `ang = 180 - ang`, layer B.Cu
      * every pad `(at dx dy padang)` with **dy negated** and
        `padang = 180 - (padang + ang)`, layers B.Cu/B.Mask/B.Paste
      * every graphic point (`start`/`end`/`center`/`mid`/`xy`) with dy negated
      * text (`fp_text`/`property`) with dy negated, angle = the new footprint
        angle, and `(justify mirror)` in its effects

    Copying a front body onto B.Cu without this writes a part that KiCad still
    draws on the front - DRC then reports its pads shorting against whatever is
    really on the front at those coordinates.
    """
    back = (180.0 - float(rot)) % 360.0

    def neg_y(seq):
        if len(seq.items) > 2:
            seq.items[2].text = B.num(-(S.arg_num(seq, 2, 0.0)))

    def one(child, in_pad):
        head = S.head(child) if isinstance(child, S.Seq) else None
        if head == "layer":
            name = S.arg_text(child, 1)
            if name in _LAYER_FLIP:
                child.items[1].text = _LAYER_FLIP[name]
        elif head == "layers":
            for item in child.items[1:]:
                if isinstance(item, S.Atom) and item.text in _LAYER_FLIP:
                    item.text = _LAYER_FLIP[item.text]
        elif head in ("start", "end", "center", "mid", "xy"):
            neg_y(child)
            if head == "center":
                _set_angle(child, back)
        elif head == "at":
            local = S.arg_num(child, 3, 0.0) or 0.0
            neg_y(child)
            if in_pad:
                _set_angle(child, 180.0 - (float(local) + float(rot)))
            else:
                _set_angle(child, back)
        elif head == "effects":
            _mirror_justify(child)
            for sub in child.items[1:]:
                one(sub, in_pad)
        elif isinstance(child, S.Seq):
            child_pad = (head == "pad") or in_pad
            for sub in child.items[1:]:
                one(sub, child_pad)

    # The footprint's own header tokens (its `at`, `layer`, `uuid`, ...) belong
    # to this operation, not to the library: mirroring them would move the part
    # to a different place and rename the side back to the front.
    for child in node.items[1:]:
        if isinstance(child, S.Seq) and S.head(child) in _FOOTPRINT_DROP:
            continue
        one(child, False)


def _rotate_pad(pad, rot):
    """Add `rot` degrees to a pad's stored angle (see the caller for why)."""
    at = S.child(pad, "at")
    if at is None:
        return
    angle = S.arg_num(at, 3, 0.0) or 0.0
    _set_angle(at, float(angle) + float(rot))


def _verify_add_footprint(ed, o):
    ref = _str(o, "ref")
    x = _num(o, "x")
    y = _num(o, "y")
    rot = _num(o, "rot", required=False, default=0)
    lib_node, info = _read_footprint_source(o)
    want_pads = _pad_numbers(lib_node)
    pad_nets = o.get("pad_nets") or {}
    try:
        fp = ed.footprint(ref)
    except OpError as exc:
        return {"ok": False, "error": str(exc)}
    problems = []
    at = S.child(fp, "at")
    got = [S.arg_num(at, 1, 0.0), -S.arg_num(at, 2, 0.0),
           S.arg_num(at, 3) or 0.0] if at is not None else [None, None, None]
    if abs((got[0] or 0.0) - x) > 1e-6 or abs((got[1] or 0.0) - y) > 1e-6:
        problems.append("position is %r, wanted %r" % (got[:2], [x, y]))
    # On the back side the file stores the mirrored angle, so 180 - rot is as
    # correct as rot itself.
    wanted = [float(rot) % 360.0, (180.0 - float(rot)) % 360.0]
    have = (got[2] or 0.0) % 360.0
    if all(abs(have - w) > 1e-6 for w in wanted):
        problems.append("rotation is %r, wanted %r" % (got[2], rot))
    got_pads = _pad_numbers(fp)
    if sorted(got_pads) != sorted(want_pads):
        problems.append("pads are %r, the library has %r"
                        % (sorted(set(got_pads)), sorted(set(want_pads))))
    wrong = {}
    for pad in S.children(fp, "pad"):
        number = S.arg_text(pad, 1) or ""
        net = S.child(pad, "net")
        got_name = S.arg_text(net, 2) if net is not None else None
        want_name = pad_nets.get(number)
        if (got_name or None) != (want_name or None):
            wrong[number] = [want_name, got_name]
    if wrong:
        problems.append("pad nets differ: %r" % (wrong,))
    return {"ok": not problems, "problems": problems, "pads": len(got_pads),
            "at": got}

op("add_footprint", {"copper", "silk", "meta"},
   "Place one footprint from a library, embedding its body and assigning pad nets. "
   "The body (pads, drills, silk) comes from the .kicad_mod, never from this "
   "server; reference, position, rotation, value and the pad->net map are the "
   "arguments. A pad the map does not name stays net-less, and DRC says so.",
   {"ref": "reference designator", "x": "mm", "y": "mm", "rot": "deg",
    "library": ".pretty directory (with `name`), or `footprint_file` (.kicad_mod)",
    "name": "footprint name inside the .pretty",
    "footprint_file": "one .kicad_mod, instead of library+name",
    "lib_id": "optional Library:Name override",
    "value": "optional Value field (default: the footprint name)",
    "layer": "F.Cu (default) or B.Cu",
    "pad_nets": "{pad number: net name}",
    "field_size": "mm - shrink/grow the Reference and Value text (default: the library's own size)",
    "create_nets": "bool - allow new nets (default false: a typo must not make a net)"},
   _run_add_footprint, _verify_add_footprint)

# --- placement --------------------------------------------------------
def _run_move_footprint(ed, o, seed):
    ref = _str(o, "ref")
    fp = ed.footprint(ref)
    at = S.child(fp, "at")
    cur_x = S.arg_num(at, 1, 0.0) if at is not None else 0.0
    cur_y = -S.arg_num(at, 2, 0.0) if at is not None else 0.0
    cur_rot = S.arg_num(at, 3) if at is not None else None
    x = _num(o, "x", required=False)
    y = _num(o, "y", required=False)
    rot = _num(o, "rot", required=False)
    if x is None and y is None and rot is None:
        raise OpError("move_footprint needs at least one of x / y / rot", "ARG_MISSING")
    new_x = cur_x if x is None else x
    new_y = cur_y if y is None else y
    new_rot = cur_rot if rot is None else rot
    if rot is not None and cur_rot is None and abs(rot) < 1e-12:
        new_rot = None
    node = B.at(new_x, new_y, new_rot)
    if at is not None:
        for i, item in enumerate(fp.items):
            if item is at:
                fp.items[i] = node
                break
    else:
        S.insert_child(fp, 1, node)
    return {"reference": ref, "before": [cur_x, cur_y, cur_rot],
            "after": [new_x, new_y, new_rot]}

def _verify_move_footprint(ed, o):
    fp = ed.footprint(_str(o, "ref"))
    at = S.child(fp, "at")
    got = [S.arg_num(at, 1, 0.0), -S.arg_num(at, 2, 0.0),
           S.arg_num(at, 3)] if at is not None else [0.0, 0.0, None]
    want_x = _num(o, "x", required=False)
    want_y = _num(o, "y", required=False)
    want_rot = _num(o, "rot", required=False)
    ok = True
    if want_x is not None:
        ok = ok and abs(got[0] - want_x) < 1e-6
    if want_y is not None:
        ok = ok and abs(got[1] - want_y) < 1e-6
    if want_rot is not None:
        ok = ok and got[2] is not None and abs(got[2] - want_rot) < 1e-6
    return {"ok": ok, "at": got}

op("move_footprint", {"copper", "silk"},
   "Move and/or rotate a placed footprint. Coordinates are the board frame (mm, "
   "Y up) - the same numbers board_view reports for that footprint.",
   {"ref": "reference designator", "x": "mm", "y": "mm", "rot": "deg"},
   _run_move_footprint, _verify_move_footprint)

def _run_set_dnp(ed, o, seed):
    ref = _str(o, "ref")
    fp = ed.footprint(ref)
    want = _bool(o, "dnp", True)
    attr = S.child(fp, "attr")
    if attr is None:
        attr = B.L(B.A("attr"))
        S.insert_child(fp, 2, attr)
    words = [a.text for a in attr.items[1:] if isinstance(a, S.Atom)]
    has = "dnp" in words
    if want and not has:
        attr.items.append(B.A("dnp"))
    elif not want and has:
        attr.items = [attr.items[0]] + [a for a in attr.items[1:]
                                        if not (isinstance(a, S.Atom) and a.text == "dnp")]
    return {"reference": ref, "dnp": want, "attr_before": words}

def _verify_set_dnp(ed, o):
    fp = ed.footprint(_str(o, "ref"))
    attr = S.child(fp, "attr")
    words = [a.text for a in (attr.items[1:] if attr is not None else [])
             if isinstance(a, S.Atom)]
    return {"ok": ("dnp" in words) == _bool(o, "dnp", True), "attr": words}

op("set_footprint_dnp", {"meta"},
   "Mark or unmark a footprint as 'do not populate' (KiCad's `dnp` attribute).",
   {"ref": "reference designator", "dnp": "bool"},
   _run_set_dnp, _verify_set_dnp)

# --- copper -----------------------------------------------------------
def _run_add_track(ed, o, seed):
    layer = _layer(o, ed)
    if layer not in ("F.Cu", "B.Cu", "In1.Cu", "In2.Cu", "In3.Cu", "In4.Cu",
                     "In5.Cu", "In6.Cu", "In7.Cu", "In8.Cu", "In9.Cu", "In10.Cu",
                     "In11.Cu", "In12.Cu", "In13.Cu", "In14.Cu", "In15.Cu",
                     "In16.Cu", "In17.Cu", "In18.Cu", "In19.Cu", "In20.Cu",
                     "In21.Cu", "In22.Cu", "In23.Cu", "In24.Cu", "In25.Cu",
                     "In26.Cu", "In27.Cu", "In28.Cu", "In29.Cu", "In30.Cu"):
        raise OpError("%r is not a copper layer" % layer, "LAYER_NOT_ALLOWED")
    net = _str(o, "net")
    code = ed.net_code(net, allow_create=_bool(o, "create_net", False))
    w = _width(o, ed, default=0.25)
    x1, y1 = _num(o, "x1"), _num(o, "y1")
    x2, y2 = _num(o, "x2"), _num(o, "y2")
    if abs(x1 - x2) < 1e-9 and abs(y1 - y2) < 1e-9:
        raise OpError("a track needs two distinct end points", "ARG_INVALID")
    ed.append(B.segment(x1, y1, x2, y2, w, layer, code, ed.sp, B.new_id(seed)))
    return {"nodes_added": 1, "net": net, "net_code": code, "width": w,
            "length_mm": ((x2 - x1) ** 2 + (y2 - y1) ** 2) ** 0.5}

def _verify_add_track(ed, o):
    layer = _str(o, "layer")
    net = _str(o, "net")
    code = ed.net_by_name.get(net)
    x1, y1 = _num(o, "x1"), _num(o, "y1")
    for node in ed.items("segment"):
        if S.arg_text(S.child(node, "layer"), 1) != layer:
            continue
        if S.arg_text(S.child(node, "net"), 1) != code:
            continue
        st = S.child(node, "start")
        if st is None:
            continue
        if (abs(S.arg_num(st, 1, 0) - x1) < 1e-4 and
                abs(-S.arg_num(st, 2, 0) - y1) < 1e-4):
            return {"ok": True, "net_code": code}
    return {"ok": False}

op("add_track", {"copper"},
   "Add one track segment on a copper layer. The net must already exist unless "
   "create_net is true (a typo'd net name must not become a new net).",
   {"x1": "mm", "y1": "mm", "x2": "mm", "y2": "mm", "width": "mm",
    "layer": "copper layer", "net": "net name", "create_net": "bool"},
   _run_add_track, _verify_add_track)

def _select_tracks(ed, o):
    uid = o.get("uuid")
    near = o.get("near")
    tol = _num(o, "tol", required=False, default=1.0)
    out = []
    for node in ed.items("segment"):
        node_uid = ed.sp.id_of(node)
        if uid and node_uid != uid:
            continue
        if near is not None:
            if not isinstance(near, (list, tuple)) or len(near) != 2:
                raise OpError("'near' must be [x, y]", "ARG_INVALID")
            nx, ny = float(near[0]), float(near[1])
            st, en = S.child(node, "start"), S.child(node, "end")
            if st is None or en is None:
                continue
            ok = False
            for p in (st, en):
                px, py = S.arg_num(p, 1, 0.0), -S.arg_num(p, 2, 0.0)
                if abs(px - nx) <= tol and abs(py - ny) <= tol:
                    ok = True
            if not ok:
                continue
        out.append(node)
    return out

def _run_delete_track(ed, o, seed):
    if not o.get("uuid") and o.get("near") is None:
        raise OpError("delete_track needs a selector: 'uuid' or 'near' "
                      "(a bare 'delete all tracks' is not an operation)", "ARG_MISSING")
    targets = _select_tracks(ed, o)
    if not targets:
        raise OpError("no track matches that selector", "TARGET_NOT_FOUND")
    for node in targets:
        ed.remove(node)
    return {"nodes_removed": len(targets),
            "uuids": [ed.sp.id_of(n) for n in targets]}

def _verify_delete_track(ed, o):
    uid = o.get("uuid")
    if uid:
        alive = [ed.sp.id_of(n) for n in ed.items("segment")]
        return {"ok": uid not in alive}
    left = _select_tracks(ed, o)
    return {"ok": not left, "remaining_matches": len(left)}

op("delete_track", {"copper"},
   "Delete track segments by uuid, or by a point within 'tol' of an end point. "
   "Refuses without a selector.",
   {"uuid": "segment uuid", "near": "[x, y] mm", "tol": "mm"},
   _run_delete_track, _verify_delete_track)

def _run_set_track_width(ed, o, seed):
    width = _num(o, "width", positive=True)
    net = o.get("net")
    layer = o.get("layer")
    if not net and not layer:
        raise OpError("set_track_width needs 'net' or 'layer' (or both) so it cannot "
                      "accidentally retarget the whole board", "ARG_MISSING")
    code = ed.net_by_name.get(net) if net else None
    if net and code is None:
        raise OpError("net %r does not exist" % net, "NET_NOT_FOUND")
    changed = 0
    for node in ed.items("segment"):
        if code is not None and S.arg_text(S.child(node, "net"), 1) != code:
            continue
        if layer and S.arg_text(S.child(node, "layer"), 1) != layer:
            continue
        wnode = S.child(node, "width")
        if wnode is not None:
            for i, item in enumerate(node.items):
                if item is wnode:
                    node.items[i] = B.L(B.A("width"), B.A(B.num(width)))
                    changed += 1
                    break
    return {"segments_changed": changed, "width": width}

def _verify_set_track_width(ed, o):
    width = _num(o, "width")
    net = o.get("net")
    layer = o.get("layer")
    code = ed.net_by_name.get(net) if net else None
    bad = 0
    checked = 0
    for node in ed.items("segment"):
        if code is not None and S.arg_text(S.child(node, "net"), 1) != code:
            continue
        if layer and S.arg_text(S.child(node, "layer"), 1) != layer:
            continue
        checked += 1
        w = S.arg_num(S.child(node, "width"), 1)
        if w is None or abs(w - width) > 1e-6:
            bad += 1
    return {"ok": bad == 0 and checked > 0, "checked": checked, "wrong_width": bad}

op("set_track_width", {"copper"},
   "Set the width of every track of one net and/or one layer. Refuses a bare call "
   "with neither filter.",
   {"width": "mm > 0", "net": "net name", "layer": "copper layer"},
   _run_set_track_width, _verify_set_track_width)

def _run_add_via(ed, o, seed):
    net = _str(o, "net")
    code = ed.net_code(net, allow_create=_bool(o, "create_net", False))
    cls = _default_netclass(ed) or {}
    dia = _num(o, "size", required=False, default=cls.get("via_dia") or 0.6)
    drill = _num(o, "drill", required=False, default=cls.get("via_drill") or 0.3)
    if drill >= dia:
        raise OpError("drill (%r) must be smaller than the pad diameter (%r)"
                      % (drill, dia), "ARG_INVALID")
    layers = o.get("layers") or ["F.Cu", "B.Cu"]
    for l in layers:
        ed.require_layer(l)
    ed.append(B.via(_num(o, "x"), _num(o, "y"), dia, drill, code, ed.sp,
                    B.new_id(seed), layers=layers))
    return {"nodes_added": 1, "net": net, "size": dia, "drill": drill}

def _verify_add_via(ed, o):
    code = ed.net_by_name.get(_str(o, "net"))
    for node in ed.items("via"):
        at = S.child(node, "at")
        if at is None:
            continue
        if (abs(S.arg_num(at, 1, 0) - _num(o, "x")) < 1e-4 and
                abs(-S.arg_num(at, 2, 0) - _num(o, "y")) < 1e-4 and
                S.arg_text(S.child(node, "net"), 1) == code):
            return {"ok": True}
    return {"ok": False}

op("add_via", {"copper"},
   "Add a through via at a point. Size/drill default to the net class.",
   {"x": "mm", "y": "mm", "size": "mm", "drill": "mm", "net": "net name",
    "layers": "[layer, layer]"},
   _run_add_via, _verify_add_via)

# --- zones ------------------------------------------------------------
def _zone_points(o):
    if o.get("points"):
        return _points(o)
    if all(o.get(k) is not None for k in ("x0", "y0", "x1", "y1")):
        x0, y0, x1, y1 = _rect(o)
        return [(x0, y0), (x1, y0), (x1, y1), (x0, y1)]
    raise OpError("a zone needs 'points' or x0/y0/x1/y1", "ARG_MISSING")

def _run_add_keepout(ed, o, seed):
    layers = o.get("layers") or ([_str(o, "layer")] if o.get("layer") else None)
    if not layers:
        raise OpError("a keepout needs 'layer' or 'layers'", "ARG_MISSING")
    for l in layers:
        ed.require_layer(l)
    rules = o.get("rules") or ["tracks", "vias", "pads", "copperpour"]
    if isinstance(rules, str):
        rules = [rules]
    valid = {"tracks", "vias", "pads", "copperpour", "footprints"}
    bad = [r for r in rules if r not in valid]
    if bad:
        raise OpError("unknown keepout rule(s): %s (valid: %s)"
                      % (", ".join(bad), ", ".join(sorted(valid))), "ARG_INVALID")
    points = _zone_points(o)
    for l in layers:
        ed.append(B.zone_polygon_conf(0, "", l, points, ed.sp, B.new_id("%s:%s" % (seed, l)),
                                      keepout=rules))
    return {"nodes_added": len(layers), "layers": layers, "rules": rules,
            "vertices": len(points)}

def _verify_add_keepout(ed, o):
    rules = o.get("rules") or ["tracks", "vias", "pads", "copperpour"]
    if isinstance(rules, str):
        rules = [rules]
    found = 0
    for node in ed.items("zone"):
        ko = S.child(node, "keepout")
        if ko is None:
            continue
        have = {S.head(item) for item in ko.items[1:] if isinstance(item, S.Seq)}
        if set(rules) <= have:
            found += 1
    return {"ok": found >= 1, "keepout_zones_with_those_rules": found}

op("add_keepout", {"copper"},
   "A keep-out area (no tracks / vias / pads / pour) on one or more layers.",
   {"points": "[[x,y], ...] or x0/y0/x1/y1", "layer": "layer", "layers": "[layers]",
    "rules": "subset of tracks, vias, pads, copperpour, footprints"},
   _run_add_keepout, _verify_add_keepout)

def _run_add_zone(ed, o, seed):
    layer = _layer(o, ed)
    net = _str(o, "net")
    allow_new = _bool(o, "create_net", False)
    code = ed.net_code(net, allow_create=allow_new)
    points = _zone_points(o)
    clearance = _num(o, "clearance", required=False, default=None)
    if clearance is None:
        clearance = (_default_netclass(ed) or {}).get("clearance") or 0.2
    ed.append(B.zone_polygon_conf(code, net, layer, points, ed.sp, B.new_id(seed),
                                  clearance=clearance,
                                  min_thickness=_num(o, "min_thickness",
                                                     required=False, default=0.25),
                                  priority=o.get("priority")))
    return {"nodes_added": 1, "layer": layer, "net": net, "vertices": len(points),
            "fill_pending": True}

def _verify_add_zone(ed, o):
    net = _str(o, "net")
    layer = _str(o, "layer")
    for node in ed.items("zone"):
        if S.child(node, "keepout") is not None:
            continue
        if S.arg_text(S.child(node, "net_name"), 1) != net:
            continue
        if S.arg_text(S.child(node, "layer"), 1) != layer:
            continue
        if S.child(node, "polygon") is not None:
            return {"ok": True,
                    "filled_polygon_present": S.child(node, "filled_polygon") is not None}
    return {"ok": False}

op("add_zone", {"copper"},
   "Add a copper pour on one layer. The fill result is NOT computed here: the file "
   "gets (fill yes) and no filled_polygon, so the vendor engine fills it on open or "
   "plot. Reply carries fill_pending=true, and board_pour_connectivity will say "
   "outline_only until KiCad has filled it.",
   {"points": "[[x,y], ...] or x0/y0/x1/y1", "layer": "copper layer",
    "net": "net name", "clearance": "mm", "min_thickness": "mm", "priority": "int"},
   _run_add_zone, _verify_add_zone)

# --- connectivity / setup ---------------------------------------------
def _run_add_net(ed, o, seed):
    name = _str(o, "name")
    if name in ed.net_by_name:
        raise OpError("net %r already exists (code %s)"
                      % (name, ed.net_by_name[name]), "NET_EXISTS")
    code = ed.net_code(name, allow_create=True)
    return {"net": name, "net_code": code}

def _verify_add_net(ed, o):
    name = _str(o, "name")
    return {"ok": name in ed.net_by_name, "net_code": ed.net_by_name.get(name)}

op("add_net", {"meta"},
   "Declare a net by name (code assigned as max+1).",
   {"name": "net name"},
   _run_add_net, _verify_add_net)

_NETCLASS_KNOBS = ("clearance", "trace_width", "via_dia", "via_drill",
                   "uvia_dia", "uvia_drill", "diff_pair_gap", "diff_pair_width")

# KiCad 7 spells two of these differently from 8+. Write the spelling the file
# already uses, and if there is none, the spelling this file's version expects.
_KNOB_SPELLINGS = {
    "trace_width": ("trace_width", "track_width"),
    "via_dia": ("via_dia", "via_diameter"),
    "uvia_dia": ("uvia_dia", "uvia_diameter"),
}

def _run_set_netclass(ed, o, seed):
    name = _str(o, "name", required=False, default="Default")
    given = [(k, _num(o, k, required=False)) for k in _NETCLASS_KNOBS]
    given = [(k, v) for k, v in given if v is not None]
    if not given:
        raise OpError("set_netclass needs at least one of: %s"
                      % ", ".join(_NETCLASS_KNOBS), "ARG_MISSING")
    target = None
    for node in ed.items("net_class"):
        if S.arg_text(node, 1) == name:
            target = node
            break
    created = target is None
    if created:
        target = B.L(B.A("net_class"), B.A(name, True), B.A("", True))
        ed.append(target)
    changed = []
    for knob, value in given:
        hit = None
        for spelling in _KNOB_SPELLINGS.get(knob, (knob,)):
            node = S.child(target, spelling)
            if node is not None:
                for i, item in enumerate(target.items):
                    if item is node:
                        target.items[i] = B.L(B.A(spelling), B.A(B.num(value)))
                        hit = spelling
                        break
                break
        if hit is None:
            # nothing to extend: write the spelling this file's format expects
            aliases = _KNOB_SPELLINGS.get(knob)
            hit = aliases[1 if ed.sp.modern else 0] if aliases else knob
            S.append_child(target, B.L(B.A(hit), B.A(B.num(value))))
        changed.append({"knob": knob, "written_as": hit, "value": value})
    return {"net_class": name, "created": created, "changed": changed}

def _verify_set_netclass(ed, o):
    name = _str(o, "name", required=False, default="Default")
    for node in ed.items("net_class"):
        if S.arg_text(node, 1) != name:
            continue
        out = {}
        for knob in _NETCLASS_KNOBS:
            v = _num(o, knob, required=False)
            if v is None:
                continue
            spellings = _KNOB_SPELLINGS.get(knob, (knob,))
            got = None
            for s in spellings:
                n = S.child(node, s)
                if n is not None:
                    got = S.arg_num(n, 1)
                    break
            out[knob] = got
            if got is None or abs(got - v) > 1e-9:
                return {"ok": False, "read_back": out}
        return {"ok": True, "read_back": out}
    return {"ok": False, "reason": "net class not present after write"}

op("set_netclass", {"meta", "copper"},
   "Set design-rule numbers on a net class (clearance, track width, via size), "
   "creating the class if it does not exist. These are the rules later tools quote; "
   "they are not a DRC run.",
   dict({"name": "net class name (default 'Default')"},
        **{k: "mm" for k in _NETCLASS_KNOBS}),
   _run_set_netclass, _verify_set_netclass)

def _run_set_thickness(ed, o, seed):
    mm = _num(o, "mm", positive=True)
    general = S.child(ed.board, "general")
    if general is None:
        general = B.L(B.A("general"))
        S.insert_child(ed.board, 1, general)
    node = S.child(general, "thickness")
    if node is None:
        general.items.append(B.L(B.A("thickness"), B.A(B.num(mm))))
    else:
        for i, item in enumerate(general.items):
            if item is node:
                general.items[i] = B.L(B.A("thickness"), B.A(B.num(mm)))
                break
    return {"thickness_mm": mm}

def _verify_set_thickness(ed, o):
    want = _num(o, "mm")
    general = S.child(ed.board, "general")
    got = S.arg_num(S.child(general, "thickness"), 1) if general is not None else None
    return {"ok": got is not None and abs(got - want) < 1e-9, "thickness_mm": got}

op("set_board_thickness", {"meta"},
   "Set (general (thickness ...)) in millimetres.",
   {"mm": "mm > 0"},
   _run_set_thickness, _verify_set_thickness)

# ----------------------------------------------------------------- driver
def validate(op):
    """Static validation: name, shape. Board-dependent checks happen in `run`."""
    if not isinstance(op, dict):
        raise OpError("each operation must be an object, got %r" % (op,), "ARG_INVALID")
    name = op.get("op")
    if not name or name not in OPS:
        raise OpError("unknown op %r (known: %s)"
                      % (name, ", ".join(sorted(OPS))), "OP_UNKNOWN")
    return name

def run_all(doc, ops, seed_base="op", index_offset=0):
    """Run every operation on `doc` (a private copy). Returns one record per op.

    Raises OpError on the first failure, naming the index: a plan is
    all-or-nothing, so a half-applied board is never a plan.

    ``index_offset`` keeps the generated ids a function of (seed_base, absolute
    operation index) rather than of where a batch starts, which is what lets a
    stepped write produce the same bytes as a one-shot write.
    """
    ed = Editor(doc)
    records = []
    for idx, op in enumerate(ops):
        name = validate(op)
        seed = "%s:%d" % (seed_base, idx + index_offset)
        try:
            detail = OPS[name]["run"](ed, op, seed) or {}
        except OpError as exc:
            exc.extra.setdefault("op_index", idx)
            exc.extra.setdefault("op", name)
            raise
        records.append({"index": idx, "op": name, "affects":
                        sorted(OPS[name]["affects"]), "detail": detail})
    return records

def verify_all(doc, ops):
    """Re-read postconditions on a document loaded from the written file."""
    ed = Editor(doc)
    out = []
    ok = True
    for idx, op in enumerate(ops):
        name = validate(op)
        try:
            res = OPS[name]["verify"](ed, op) or {"ok": False}
        except OpError as exc:
            res = {"ok": False, "error": "%s: %s" % (exc.code, exc)}
        except Exception as exc:  # noqa: BLE001
            res = {"ok": False, "error": "%s: %s" % (type(exc).__name__, exc)}
        if not res.get("ok"):
            ok = False
        out.append({"index": idx, "op": name, "verified": bool(res.get("ok")),
                    "detail": res})
    return {"all_verified": ok, "operations": out}

def affects(ops):
    out = set()
    for op in ops:
        out |= set(OPS[validate(op)]["affects"])
    return sorted(out)
