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
import hashlib
import inspect as _inspect
import math
import os

from ..fidelity import sexpr as S
from . import build as B

class OpError(ValueError):
    """A refusal from one operation.

    Carries the same four attributes as `jobs.JobError` and `RouteError`
    (`message` / `reason_code` / `hint` / `extra`) so a caller can handle all
    three the same way. `code` stays as an alias of `reason_code`: it is the name
    the write path already used, and renaming a public attribute to make three
    exceptions LOOK alike would break the callers it is meant to help.
    """
    def __init__(self, message, code="OP_INVALID", hint=None, **extra):
        ValueError.__init__(self, message)
        self.message = message
        self.code = code
        self.reason_code = code
        self.hint = hint
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
        # KiCad 10's board format drops the `(net <code> "name")` table: every
        # object carries `(net "name")` instead and nets live in the project.
        # Deriving the map from the references keeps every name lookup - read,
        # verify and delete - working on those boards too, so this is a fact
        # about the file, recorded rather than papered over.
        self.name_only = not self.net_by_name
        if self.name_only:
            self._derive_nets()

    def _derive_nets(self):
        """Build the net map from `(net "name")` references (name-only formats)."""
        def take(node):
            net = S.child(node, "net")
            if net is None:
                return
            name = S.arg_text(net, 2) or S.arg_text(net, 1) or ""
            if name:
                self.net_by_name[name] = name
                self.name_by_code[name] = name
        for fp in self.footprints():
            for pad in S.children(fp, "pad"):
                take(pad)
        for kind in ("segment", "arc", "via", "zone", "group"):
            for node in self.items(kind):
                take(node)

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
            if self.name_only:
                raise OpError(
                    "this board's format has no net table to add to - a net "
                    "exists here only because an object names it, so assign %r "
                    "to the pad/zone that uses it instead" % name,
                    "NET_CANNOT_CREATE")
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

def _run_set_board_outline(ed, o, seed):
    """Replace the board edge with a new rectangle.

    `add_outline_rect` can only ADD an edge, so a board could never be resized:
    a re-layout that needs a different board shape had to be done by editing the
    file by hand, outside every gate. Deleting the old edge is exactly the
    operation that needs those gates - a board left with a stale inner rectangle
    looks fine in a viewer and is two outlines to every rule that matters.
    """
    old = ed.edge_items()
    fp_edges = 0
    for node in list(old):
        if S.head(node) not in ("gr_line", "gr_arc", "gr_rect", "gr_circle",
                               "gr_poly", "gr_curve"):
            continue
        ed.remove(node)
        fp_edges += 1
    x0, y0, x1, y1 = _rect(o)
    if x1 <= x0 or y1 <= y0:
        raise OpError("outline needs x1 > x0 and y1 > y0", "ARG_INVALID")
    w = _width(o, ed, default=0.1)
    nodes = B.rect_as_lines(x0, y0, x1, y1, OUTLINE_LAYER, w, ed.sp, seed)
    for n in nodes:
        ed.append(n)
    return {"nodes_removed": fp_edges, "nodes_added": len(nodes),
            "bbox": [x0, y0, x1, y1]}

def _verify_set_board_outline(ed, o, record=None):
    x0, y0, x1, y1 = _rect(o)
    want = {(round(x0, 4), round(y0, 4)), (round(x1, 4), round(y0, 4)),
            (round(x1, 4), round(y1, 4)), (round(x0, 4), round(y1, 4))}
    got = set()
    extra = 0
    for node in ed.edge_items():
        if S.head(node) != "gr_line":
            extra += 1
            continue
        st, en = S.child(node, "start"), S.child(node, "end")
        if st is None or en is None:
            extra += 1
            continue
        for pair in ((st, en), (en, st)):
            p = (round(S.arg_num(pair[0], 1, 0.0), 4),
                 round(-S.arg_num(pair[0], 2, 0.0), 4))
            if p in want:
                got.add(p)
    lines = sum(1 for n in ed.edge_items() if S.head(n) == "gr_line")
    return {"ok": got == want and lines == 4 and extra == 0,
            "corners_found": len(got), "corners_expected": 4,
            "edge_lines": lines, "other_edge_items": extra}

op("set_board_outline", {"outline"},
   "Replace the board edge with a new rectangle: the old Edge.Cuts geometry is "
   "removed first, then four new lines are written. Use this to RESIZE a board - "
   "add_outline_rect alone leaves the old edge behind, and a board with two "
   "outlines is two boards to every rule that reads Edge.Cuts. Footprint-level "
   "edge graphics are not touched.",
   {"x0": "mm", "y0": "mm", "x1": "mm", "y1": "mm",
    "width": "mm (default 0.1)"},
   _run_set_board_outline, _verify_set_board_outline)

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

def _run_set_board_outline_polygon(ed, o, seed):
    """Replace the board edge with ONE closed polygon.

    `set_board_outline` can only write a rectangle, so an arbitrarily shaped
    board (rounded corners, a notched outline, the shape a real enclosure
    forces) could not be produced by the gated path at all: it had to be
    hand-edited outside every gate. Two outlines on one board is not a
    cosmetic problem - every rule that reads Edge.Cuts sees two boards.
    """
    removed = 0
    for node in list(ed.edge_items()):
        if S.head(node) not in ("gr_line", "gr_arc", "gr_rect", "gr_circle",
                                "gr_poly", "gr_curve"):
            continue
        ed.remove(node)
        removed += 1
    points = _points(o)
    if len(points) < 3:
        raise OpError("outline polygon needs at least 3 points", "ARG_INVALID")
    w = _width(o, ed, default=0.1)
    if points[0] != points[-1]:
        points = points + [points[0]]
    nodes = _polygon_nodes(points, OUTLINE_LAYER, w, ed.sp, seed, close=True)
    for n in nodes:
        ed.append(n)
    return {"nodes_removed": removed, "nodes_added": len(nodes),
            "vertices": len(points) - 1}

def _verify_set_board_outline_polygon(ed, o, record=None):
    points = _points(o)
    xs = [p[0] for p in points]
    ys = [p[1] for p in points]
    edge = list(ed.edge_items())
    polys = [n for n in edge if S.head(n) == "gr_poly"]
    ok = len(edge) == 1 and len(polys) == 1
    got = None
    if len(polys) == 1:
        pts_node = S.child(polys[0], "pts")
        if pts_node is not None:
            got = [(round(S.arg_num(p, 1, 0.0), 4), round(-S.arg_num(p, 2, 0.0), 4))
                   for p in S.children(pts_node, "xy")]
    if got:
        ok = ok and abs(max(x for x, _ in got) - max(xs)) < 1e-3 \
                and abs(min(y for _, y in got) - min(ys)) < 1e-3 \
                and abs(max(y for _, y in got) - max(ys)) < 1e-3 \
                and abs(min(x for x, _ in got) - min(xs)) < 1e-3
    return {"ok": bool(ok), "edge_items": len(edge), "polygons": len(polys),
            "expected_bbox": [min(xs), min(ys), max(xs), max(ys)]}

op("set_board_outline_polygon", {"outline"},
   "Replace the board edge with ONE closed polygon on Edge.Cuts: the old "
   "Edge.Cuts geometry is removed first, then the polygon is written. This is "
   "the only way to give a board a shape that is not a rectangle (rounded or "
   "notched outline) without leaving a second outline behind.",
   {"points": "[[x,y], ...] mm Y-up, >= 3 points", "width": "mm (default 0.1)"},
   _run_set_board_outline_polygon, _verify_set_board_outline_polygon)

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

# --- whole board ------------------------------------------------------
# Board coordinates are the file's own numbers, and KiCad plots at (almost) those
# numbers: a board whose geometry sits at negative Y renders off the page and the
# export comes out blank. Moving every object by one delta is the fix, and it is
# only safe if NOTHING is left behind - hence the two closed lists below. An item
# kind this code has never seen refuses the whole move (UNSUPPORTED_ITEM) instead
# of being silently parked at the old coordinates.
_MOVE_TOP = ("segment", "arc", "via", "zone", "footprint", "module", "group",
             "gr_line", "gr_arc", "gr_rect", "gr_circle", "gr_curve", "gr_poly",
             "gr_text", "gr_text_box", "dimension", "target", "image")

# Nodes that carry no board geometry. `setup` is listed as a whole on purpose: it
# holds aux_axis_origin / grid_origin, which are view aids, and moving a drawing
# origin while moving the copper would be a change nobody asked for.
_META_TOP = frozenset((
    "version", "generator", "generator_version", "general", "paper", "layers",
    "setup", "title_block", "net", "net_class", "property", "embedded_fonts",
    "embedded_files", "lib_symbols"))

# The spellings KiCad uses for a single position.
_POINT_HEADS = frozenset(("at", "start", "end", "mid", "center", "xy"))

# Geometry inside a footprint is in the footprint's OWN frame and travels with it,
# so the walk stops at the footprint node and shifts only its `at`.
_LOCAL_FRAME = frozenset(("footprint", "module"))


def _point(node):
    """(x, y) of a position node in file units, or None when it carries none."""
    if node is None or len(node.items) < 3:
        return None
    x, y = S.arg_num(node, 1), S.arg_num(node, 2)
    if x is None or y is None:
        return None
    return x, y


def _board_points(board):
    """Every position in the board, in traversal order, in file units.

    The walk and `_shift_tree` below must agree exactly: the digest of this list is
    the postcondition of the move, so anything one of them skips must be skipped by
    both or the move would report success while something stayed behind.
    """
    out = []

    def walk(node):
        for child in node.items[1:]:
            if not isinstance(child, S.Seq):
                continue
            head = S.head(child)
            if head in _META_TOP:
                continue
            if head in _LOCAL_FRAME:
                p = _point(S.child(child, "at"))
                if p is not None:
                    out.append(("at",) + p)
                continue
            if head in _POINT_HEADS:
                p = _point(child)
                if p is not None:
                    out.append((head,) + p)
                continue
            walk(child)

    walk(board)
    return out


def _points_digest(points):
    h = hashlib.sha256()
    for head, x, y in points:
        h.update(("%s %.10g %.10g\n" % (head, x, y)).encode("utf-8"))
    return h.hexdigest()


def _shift_point(node, dx, dy):
    p = _point(node)
    if p is None:
        raise OpError("cannot move (%s ...): it has no numeric position"
                      % S.head(node), "ARG_INVALID")
    items = list(node.items)
    items[1] = S.Atom(B.num(p[0] + dx))
    items[2] = S.Atom(B.num(p[1] + dy))
    node.items = items


def _shift_tree(node, dx, dy):
    for child in list(node.items[1:]):
        if not isinstance(child, S.Seq):
            continue
        head = S.head(child)
        if head in _META_TOP:
            continue
        if head in _LOCAL_FRAME:
            at = S.child(child, "at")
            if at is not None:
                _shift_point(at, dx, dy)
            continue
        if head in _POINT_HEADS:
            _shift_point(child, dx, dy)
            continue
        _shift_tree(child, dx, dy)


def _run_translate_board(ed, o, seed):
    dx = _num(o, "dx", required=False, default=0.0)
    dy = _num(o, "dy", required=False, default=0.0)
    if abs(dx) < 1e-12 and abs(dy) < 1e-12:
        raise OpError("dx = dy = 0 moves nothing - that is a typo, not an operation",
                      "ARG_INVALID")
    unknown = []
    moved = {}
    for item in ed.board.items[1:]:
        if not isinstance(item, S.Seq):
            continue
        head = S.head(item)
        if head in _META_TOP:
            continue
        if head not in _MOVE_TOP:
            unknown.append(head)
            continue
        moved[head] = moved.get(head, 0) + 1
    if unknown:
        raise OpError(
            "this board carries item kinds this operation cannot move (%s); moving "
            "the rest would leave them at the old place, so nothing is moved"
            % ", ".join(sorted(set(unknown))), "UNSUPPORTED_ITEM")
    before = _board_points(ed.board)
    # KiCad's file stores Y downwards; this server's frames are Y up.
    _shift_tree(ed.board, dx, -dy)
    after = _board_points(ed.board)
    if len(after) != len(before):
        raise OpError("internal: %d positions before the move, %d after"
                      % (len(before), len(after)), "OP_INVALID")
    return {"dx": dx, "dy": dy, "items": moved,
            "items_total": sum(moved.values()), "positions": len(after),
            "digest_before": _points_digest(before),
            "digest_after": _points_digest(after)}


def _verify_translate_board(ed, o, record=None):
    dx = _num(o, "dx", required=False, default=0.0)
    dy = _num(o, "dy", required=False, default=0.0)
    # `record` is the wrapper run_all returns ({"index", "op", "affects",
    # "detail"}); the numbers the run computed live under "detail".
    rec = (record or {}).get("detail") or {}
    if not rec.get("digest_after"):
        return {"ok": False,
                "error": "this postcondition is the digest of the move itself, so it "
                         "needs the record the run returned; none was passed"}
    now = _board_points(ed.board)
    problems = []
    if rec.get("positions") != len(now):
        problems.append("the file has %s positions, the run moved %s"
                        % (len(now), rec.get("positions")))
    if rec.get("digest_after") != _points_digest(now):
        problems.append("the positions on disk are not the ones the run computed "
                        "(something moved differently or stayed behind)")
    bad_delta = (abs(float(rec.get("dx") or 0.0) - dx) > 1e-9 or
                 abs(float(rec.get("dy") or 0.0) - dy) > 1e-9)
    if bad_delta:
        problems.append("the recorded delta is not the requested one")
    if rec.get("digest_before") == rec.get("digest_after"):
        problems.append("the digest did not change: nothing moved")
    return {"ok": not problems, "positions": len(now), "problems": problems,
            "verify_limit": "the digest proves the whole set of positions moved by "
                            "exactly this delta; on its own it cannot prove which "
                            "object each position belongs to, so this verifier also "
                            "checks the position count and the recorded delta"}


op("translate_board", {"outline", "copper", "silk", "mech"},
   "Move every object on the board by the same delta (mm, Y up). It exists because "
   "KiCad plots a board at the file's own coordinates: geometry that sits at "
   "negative Y lands off the PDF/SVG page and the export comes out blank. Nothing "
   "is moved unless every item kind on the board is one this operation knows.",
   {"dx": "mm (default 0)", "dy": "mm (default 0)"},
   _run_translate_board, _verify_translate_board)

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
            if ed.name_only:
                # name-only boards: `(net "GND")`, no code column
                pad.items.append(B.L(B.A("net"), B.A(net_name, True)))
            else:
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
        got_name = (S.arg_text(net, 2) or S.arg_text(net, 1)) if net is not None else None
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
def _fmt_deg(v):
    v = round(float(v), 6)
    if abs(v) < 1e-9:
        return "0"
    return ("%.6f" % v).rstrip("0").rstrip(".")


def _rotate_pads(fp, delta):
    """Turn every pad's `at` angle by `delta` degrees.

    Pad angles are stored **in the board frame**, not relative to the footprint:
    `(pad "A1" ... (at -3.2 -3.745 90) (size 0.6 1.3))` is a 0.6x1.3 pad whose
    long axis follows that 90 whether the parent footprint sits at 0 or at 90.
    Rotating only the footprint's own `at` therefore leaves every pad pointing
    the old way, which on a row of 0.8 mm pitch pads turns a legal fan-out into
    a short between neighbours (found by rotating a USB-C receptacle: A1[GND]
    and A4[VBUS] shorted although nothing had been routed). The pad POSITION is
    relative to the footprint and is rotated by KiCad itself; only the angle
    needs help.
    """
    n = 0
    for pad in S.children(fp, "pad"):
        at = S.child(pad, "at")
        if at is None:
            continue
        ang = S.arg_num(at, 3, 0.0) or 0.0
        new = (ang + float(delta) + 180.0) % 360.0 - 180.0
        text = _fmt_deg(new)
        if len(at.items) > 3:
            at.items[3].text = text
        else:
            at.items.append(S.Atom(text))
        n += 1
    return n


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
    delta = 0.0
    if rot is not None:
        delta = (new_rot or 0.0) - (cur_rot or 0.0)
    pads_rotated = _rotate_pads(fp, delta) if abs(delta) > 1e-9 else 0
    return {"reference": ref, "before": [cur_x, cur_y, cur_rot],
            "after": [new_x, new_y, new_rot],
            "pad_angle_delta": delta if pads_rotated else 0.0,
            "pads_rotated": pads_rotated}

def _verify_move_footprint(ed, o, record=None):
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
        # A footprint written as `(at x y)` has angle 0 -- KiCad's default. The
        # old check demanded an explicit angle token, so every rotate-to-0 op on
        # a footprint that never had one failed its own postcondition and the
        # whole plan rolled back (17 of 19 moves on a real board, 2026-11-04).
        got_rot = S.arg_num(at, 3, 0.0) if at is not None else 0.0
        ok = ok and abs(got_rot - want_rot) < 1e-6
    # Every pad must have been turned when the footprint was: a skipped pad is a
    # pad still pointing the old way, which is a short on a fine-pitch row.
    pads_rotated = ((record or {}).get("detail") or {}).get("pads_rotated")
    pads_with_at = sum(1 for p in S.children(fp, "pad") if S.child(p, "at") is not None)
    pads_ok = True if not pads_rotated else pads_rotated == pads_with_at
    return {"ok": ok and pads_ok, "at": got,
            "pads_rotated": pads_rotated, "pads_with_at": pads_with_at}

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

def _run_add_track_arc(ed, o, seed):
    layer = _layer(o, ed)
    if not layer.endswith(".Cu"):
        raise OpError("%r is not a copper layer" % layer, "LAYER_NOT_ALLOWED")
    net = _str(o, "net")
    code = ed.net_code(net, allow_create=_bool(o, "create_net", False))
    w = _width(o, ed, default=0.25)
    sx, sy = _num(o, "x1"), _num(o, "y1")
    mx, my = _num(o, "mx"), _num(o, "my")
    ex, ey = _num(o, "x2"), _num(o, "y2")
    # Three collinear points are not an arc: KiCad would have to invent a radius
    # of infinity, and the file would be unreadable by pcbnew. That is checked
    # here rather than at read time because the file must never be written wrong.
    cross = ((mx - sx) * (ey - sy) - (my - sy) * (ex - sx))
    if abs(cross) < 1e-9:
        raise OpError("an arc needs a mid point OFF the chord; these three points "
                      "are collinear (cross=%r)" % (cross,), "ARG_INVALID")
    if abs(sx - ex) < 1e-9 and abs(sy - ey) < 1e-9:
        raise OpError("an arc needs two distinct end points", "ARG_INVALID")
    ed.append(B.track_arc(sx, sy, mx, my, ex, ey, w, layer, code, ed.sp,
                          B.new_id(seed)))
    length = arc_length_mm((sx, sy), (mx, my), (ex, ey))
    return {"nodes_added": 1, "net": net, "net_code": code, "width": w,
            "length_mm": round(length, 6)}

def arc_length_mm(s, m, e):
    """Length along the arc through three points.

    The radius comes from the circle through them; the swept angle is measured
    from the centre and clamped to (0, 2*pi). A file arc that spans nearly the
    whole circle is legal, so "the bigger angle" is the one to keep when the mid
    point sits past the half-way mark - choosing the minor arc there would report
    a 340-degree arc as 20 degrees.
    """
    ax, ay = s
    bx, by = m
    cx, cy = e
    d = 2.0 * (ax * (by - cy) + bx * (cy - ay) + cx * (ay - by))
    if abs(d) < 1e-12:
        return ((cx - ax) ** 2 + (cy - ay) ** 2) ** 0.5
    ux = ((ax ** 2 + ay ** 2) * (by - cy) + (bx ** 2 + by ** 2) * (cy - ay)
          + (cx ** 2 + cy ** 2) * (ay - by)) / d
    uy = ((ax ** 2 + ay ** 2) * (cx - bx) + (bx ** 2 + by ** 2) * (ax - cx)
          + (cx ** 2 + cy ** 2) * (bx - ax)) / d
    r = ((ax - ux) ** 2 + (ay - uy) ** 2) ** 0.5
    a0 = math.atan2(ay - uy, ax - ux)
    a1 = math.atan2(by - uy, bx - ux)
    a2 = math.atan2(cy - uy, cx - ux)
    two = 2.0 * math.pi
    sweep = (a2 - a0) % two
    mid = (a1 - a0) % two
    if mid > sweep:                 # the mid point lies on the long way round
        sweep = two - sweep
    return r * sweep

def _verify_add_track_arc(ed, o):
    layer = _str(o, "layer")
    code = ed.net_by_name.get(_str(o, "net"))
    sx, sy = _num(o, "x1"), _num(o, "y1")
    for node in ed.items("arc"):
        if S.arg_text(S.child(node, "layer"), 1) != layer:
            continue
        if S.arg_text(S.child(node, "net"), 1) != code:
            continue
        st = S.child(node, "start")
        if st is None:
            continue
        if (abs(S.arg_num(st, 1, 0) - sx) < 1e-4 and
                abs(-S.arg_num(st, 2, 0) - sy) < 1e-4):
            return {"ok": True, "net_code": code}
    return {"ok": False}

op("add_track_arc", {"copper"},
   "Add one COPPER arc (a rounded meander piece) on a copper layer: start, a "
   "mid point that must sit off the chord, end. Same net rules as add_track. "
   "Length is the swept arc length, reported so a caller never has to guess.",
   {"x1": "mm", "y1": "mm", "mx": "mm", "my": "mm", "x2": "mm", "y2": "mm",
    "width": "mm", "layer": "copper layer", "net": "net name",
    "create_net": "bool"},
   _run_add_track_arc, _verify_add_track_arc)

def _select_tracks(ed, o):
    uid = o.get("uuid")
    near = o.get("near")
    tol = _num(o, "tol", required=False, default=1.0)
    # KiCad stores an arc as a track too (`(arc (start) (mid) (end) ...)`), and a
    # dangling chain can run into one. `include_arcs` is off by default because a
    # bare `delete_track` has always meant "segments" and a selector that quietly
    # widened would delete copper nobody asked it to touch.
    heads = ("segment", "arc") if _bool(o, "include_arcs", False) else ("segment",)
    nodes = [n for h in heads for n in ed.items(h)]
    out = []
    for node in nodes:
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

def _fixed_nodes(nodes):
    """The ones of `nodes` the FILE marks `(locked)`.

    Specctra spells this `(type fix)`; our own files spell it `(locked)`, and it
    is the same claim: this copper is somebody's decision, not a candidate. It is
    read from the file and never inferred, so a board that locks nothing behaves
    exactly as before.
    """
    return [n for n in nodes if S.child(n, "locked") is not None]

def _refuse_fixed(ed, what, fixed, total, o):
    """Refuse a write that would move locked copper, unless told to explicitly."""
    if fixed and not _bool(o, "allow_fixed", False):
        raise OpError(
            "%d of %d %s are LOCKED - the file marks them `(locked)`, the same "
            "claim Specctra writes as `(type fix)`: this copper is not a candidate "
            "for anything. First: %s. Pass allow_fixed=true if you really mean to "
            "move it." % (len(fixed), total, what,
                          ", ".join(ed.sp.id_of(n) for n in fixed[:6])),
            "ARG_CONFLICT")

def _run_delete_track(ed, o, seed):
    if not o.get("uuid") and o.get("near") is None:
        raise OpError("delete_track needs a selector: 'uuid' or 'near' "
                      "(a bare 'delete all tracks' is not an operation)", "ARG_MISSING")
    targets = _select_tracks(ed, o)
    if not targets:
        raise OpError("no track matches that selector", "TARGET_NOT_FOUND")
    _refuse_fixed(ed, "selected segment(s)", _fixed_nodes(targets), len(targets), o)
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
   {"uuid": "segment uuid", "near": "[x, y] mm", "tol": "mm",
    "include_arcs": "bool: also match track arcs (default false)",
    "allow_fixed": "bool: also move copper the file marks (locked)"},
   _run_delete_track, _verify_delete_track)

def _select_vias(ed, o):
    uid = o.get("uuid")
    near = o.get("near")
    tol = _num(o, "tol", required=False, default=0.05)
    out = []
    for node in ed.items("via"):
        node_uid = ed.sp.id_of(node)
        if uid and node_uid != uid:
            continue
        if near is not None:
            if not isinstance(near, (list, tuple)) or len(near) != 2:
                raise OpError("'near' must be [x, y]", "ARG_INVALID")
            at = S.child(node, "at")
            if at is None:
                continue
            if (abs(S.arg_num(at, 1, 0.0) - float(near[0])) > tol or
                    abs(-S.arg_num(at, 2, 0.0) - float(near[1])) > tol):
                continue
        out.append(node)
    return out

def _run_delete_via(ed, o, seed):
    """Delete one via by uuid, or the via(s) within 'tol' of a point.

    The companion of `delete_track`: a dangling chain can end in a via that then
    carries copper on one layer only, and a board that cannot lose that via keeps
    a stub nobody drew on purpose. Same rule as every other write on copper: the
    file's `(locked)` wins unless `allow_fixed` says otherwise.
    """
    if not o.get("uuid") and o.get("near") is None:
        raise OpError("delete_via needs a selector: 'uuid' or 'near' (a bare "
                      "'delete all vias' is not an operation)", "ARG_MISSING")
    targets = _select_vias(ed, o)
    if not targets:
        raise OpError("no via matches that selector", "TARGET_NOT_FOUND")
    _refuse_fixed(ed, "selected via(s)", _fixed_nodes(targets), len(targets), o)
    out = []
    for node in targets:
        out.append(ed.sp.id_of(node))
        ed.remove(node)
    return {"nodes_removed": len(targets), "uuids": out}

def _verify_delete_via(ed, o):
    if o.get("uuid"):
        left = [n for n in ed.items("via") if ed.sp.id_of(n) == o["uuid"]]
        return {"ok": not left, "vias_with_that_uuid": len(left)}
    return {"ok": not _select_vias(ed, o), "vias_left_near": len(_select_vias(ed, o))}

op("delete_via", {"copper"},
   "Delete a via by uuid, or by a point within 'tol' of it. Refuses without a "
   "selector.",
   {"uuid": "via uuid", "near": "[x, y] mm", "tol": "mm (default 0.05)",
    "allow_fixed": "bool: also move copper the file marks (locked)"},
   _run_delete_via, _verify_delete_via)

def _track_nodes(ed):
    """Every copper track on the board: `segment` AND `arc`.

    KiCad 10 writes a curved track as `(arc (start) (mid) (end) (width) (layer)
    (net) (uuid))`. It is the same copper, carries the same width attribute and
    is selected by the same net - a width rule that only walks `segment` leaves
    157 of this repository's example board's 862 tracks at the signal width and
    says nothing about it. Both kinds are tracks; they are iterated together.
    """
    return ed.items("segment") + ed.items("arc")

def _uuid_set(o):
    """The `uuids` selector, or None when the call does not use it."""
    raw = o.get("uuids")
    if raw is None:
        return None
    if not isinstance(raw, (list, tuple)) or not raw:
        raise OpError("'uuids' must be a non-empty list of segment ids; an empty list "
                      "matches nothing and an empty match is not an edit",
                      "ARG_INVALID")
    return {str(u) for u in raw}

def _run_set_track_width(ed, o, seed):
    width = _num(o, "width", positive=True)
    net = o.get("net")
    layer = o.get("layer")
    uuids = _uuid_set(o)
    if not net and not layer and not uuids:
        raise OpError("set_track_width needs 'net' or 'layer' or 'uuids' so it cannot "
                      "accidentally retarget the whole board", "ARG_MISSING")
    code = ed.net_by_name.get(net) if net else None
    if net and code is None:
        raise OpError("net %r does not exist" % net, "NET_NOT_FOUND")
    matched = []
    for node in _track_nodes(ed):
        if uuids is not None and ed.sp.id_of(node) not in uuids:
            continue
        if code is not None and S.arg_text(S.child(node, "net"), 1) != code:
            continue
        if layer and S.arg_text(S.child(node, "layer"), 1) != layer:
            continue
        matched.append(node)
    if uuids is not None:
        found = {ed.sp.id_of(n) for n in matched}
        missing = sorted(uuids - found)
        if missing:
            raise OpError("%d of %d requested segment id(s) are not on this board "
                          "(net/layer filters do not explain it): %s"
                          % (len(missing), len(uuids), ", ".join(missing[:6])),
                          "SEGMENT_NOT_FOUND")
    _refuse_fixed(ed, "matching segment(s)", _fixed_nodes(matched),
                  len(matched), o)
    changed = 0
    for node in matched:
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
    uuids = _uuid_set(o)
    code = ed.net_by_name.get(net) if net else None
    bad = 0
    checked = 0
    for node in _track_nodes(ed):
        if uuids is not None and ed.sp.id_of(node) not in uuids:
            continue
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
   "Set the width of the tracks matched by net, layer and/or a list of segment "
   "ids. Refuses a bare call with none of the three filters, and refuses ids that "
   "are not on the board - a width applied to nothing is not an edit.",
   {"width": "mm > 0", "net": "net name", "layer": "copper layer",
    "uuids": "[segment id, ...] - one width per segment is how a net is widened "
             "as far as its neighbours allow",
    "allow_fixed": "bool: also resize copper the file marks (locked)"},
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

def _run_set_via_size(ed, o, seed):
    size = _num(o, "size", required=False)
    drill = _num(o, "drill", required=False)
    net = o.get("net")
    uuids = _uuid_set(o)
    if size is None and drill is None:
        raise OpError("set_via_size needs 'size' and/or 'drill'; naming neither "
                      "changes nothing", "ARG_MISSING")
    if not net and not uuids:
        raise OpError("set_via_size needs 'net' or 'uuids' so it cannot retarget "
                      "every via on the board", "ARG_MISSING")
    code = ed.net_by_name.get(net) if net else None
    if net and code is None:
        raise OpError("net %r does not exist" % net, "NET_NOT_FOUND")
    matched = []
    for node in ed.items("via"):
        if uuids is not None and ed.sp.id_of(node) not in uuids:
            continue
        if code is not None and S.arg_text(S.child(node, "net"), 1) != code:
            continue
        matched.append(node)
    if not matched:
        raise OpError("no via matched the selector", "NOTHING_MATCHED")
    _refuse_fixed(ed, "matching via(s)", _fixed_nodes(matched), len(matched), o)
    for node in matched:
        cur_size = S.arg_num(S.child(node, "size"), 1)
        cur_drill = S.arg_num(S.child(node, "drill"), 1)
        new_size = size if size is not None else cur_size
        new_drill = drill if drill is not None else cur_drill
        if new_size is None or new_drill is None:
            raise OpError("via %r carries no size/drill to keep; name both"
                          % ed.sp.id_of(node), "ARG_MISSING")
        if new_drill >= new_size:
            raise OpError("drill (%r) must stay smaller than the pad (%r) or the "
                          "via has no copper left" % (new_drill, new_size),
                          "ARG_INVALID")
        for key, val in (("size", new_size), ("drill", new_drill)):
            child = S.child(node, key)
            for i, item in enumerate(node.items):
                if item is child:
                    node.items[i] = B.L(B.A(key), B.A(B.num(val)))
                    break
    return {"vias_changed": len(matched), "size": size, "drill": drill}

def _verify_set_via_size(ed, o):
    size = _num(o, "size", required=False)
    drill = _num(o, "drill", required=False)
    net = o.get("net")
    uuids = _uuid_set(o)
    code = ed.net_by_name.get(net) if net else None
    bad = 0
    checked = 0
    for node in ed.items("via"):
        if uuids is not None and ed.sp.id_of(node) not in uuids:
            continue
        if code is not None and S.arg_text(S.child(node, "net"), 1) != code:
            continue
        checked += 1
        if size is not None:
            w = S.arg_num(S.child(node, "size"), 1)
            if w is None or abs(w - size) > 1e-6:
                bad += 1
                continue
        if drill is not None:
            d = S.arg_num(S.child(node, "drill"), 1)
            if d is None or abs(d - drill) > 1e-6:
                bad += 1
    return {"ok": bad == 0 and checked > 0, "checked": checked, "wrong_size": bad}

op("set_via_size", {"copper"},
   "Set a via family: the pad diameter and/or the drill, for one net or a list "
   "of via ids. Refuses a drill that is not smaller than the pad, and refuses a "
   "call that names neither size nor drill.",
   {"size": "mm - pad diameter", "drill": "mm - finished hole",
    "net": "net name", "uuids": "[via id, ...]",
    "allow_fixed": "bool: also resize copper the file marks (locked)"},
   _run_set_via_size, _verify_set_via_size)

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
    holes = o.get("holes") or []
    parsed_holes = []
    for ring in holes:
        if not isinstance(ring, (list, tuple)) or len(ring) < 3:
            raise OpError("every entry of 'holes' must be a list of at least 3 "
                          "[x, y] pairs (mm, Y up)", "ARG_INVALID")
        parsed_holes.append(_points({"points": ring}))
    for l in layers:
        ed.append(B.zone_polygon_conf(0, "", l, points, ed.sp, B.new_id("%s:%s" % (seed, l)),
                                      keepout=rules, holes=parsed_holes))
    return {"nodes_added": len(layers), "layers": layers, "rules": rules,
            "vertices": len(points), "holes": len(parsed_holes)}

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
    "rules": "subset of tracks, vias, pads, copperpour, footprints",
    "holes": "[[[x,y], ...], ...] rings that the area does NOT cover"},
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
    connection = ("thermal" if o.get("connect_pads") is None
                  else _zone_connection_word(o.get("connect_pads")))
    thermal_gap = _num(o, "thermal_gap", required=False, default=None)
    thermal_bridge = _num(o, "thermal_bridge_width", required=False, default=None)
    ed.append(B.zone_polygon_conf(code, net, layer, points, ed.sp, B.new_id(seed),
                                  clearance=clearance,
                                  min_thickness=_num(o, "min_thickness",
                                                     required=False, default=0.25),
                                  priority=o.get("priority"),
                                  pad_connection=connection,
                                  thermal_gap=thermal_gap,
                                  thermal_bridge_width=thermal_bridge))
    # What the header holds is reported back as WRITTEN, and anything left to the
    # engine is named as left to the engine: a caller who asks "did you choose my
    # thermal gap" deserves to know it did not.
    return {"nodes_added": 1, "layer": layer, "net": net, "vertices": len(points),
            "fill_pending": True,
            "header": {
                "connect_pads": None if connection == "thermal" else connection,
                "connect_pads_effective": connection,
                "connect_pads_note": (
                    "a thermal relief is spelled by writing no mode word - that "
                    "is the form KiCad itself saves (verified: pcbnew 10.0.7 "
                    "reads (connect_pads (clearance X)) as "
                    "ZONE_CONNECTION_THERMAL)"
                    if connection == "thermal" else
                    "the mode word is written explicitly, because a solid pour "
                    "and a thermal relief are different copper"),
                "filled_areas_thickness": "no",
                "filled_areas_thickness_note": (
                    "written because this board declares an older format "
                    "generation, where a filled_polygon without this token is "
                    "read as the centreline of a min_thickness-wide stroke and "
                    "KiCad DRC then measures min_thickness/2 further out than "
                    "the copper is"),
                "thermal_gap": thermal_gap,
                "thermal_bridge_width": thermal_bridge,
                "thermal_source": ("caller" if thermal_gap is not None
                                   or thermal_bridge is not None else
                                   "eda_default: not written; KiCad applies its "
                                   "own 0.5 mm / 0.5 mm (measured on 10.0.7)"),
            }}

def _verify_add_zone(ed, o):
    net = _str(o, "net")
    layer = _str(o, "layer")
    want = ("thermal" if o.get("connect_pads") is None
            else _zone_connection_word(o.get("connect_pads")))
    for node in ed.items("zone"):
        if S.child(node, "keepout") is not None:
            continue
        if S.arg_text(S.child(node, "net_name"), 1) != net:
            continue
        if S.arg_text(S.child(node, "layer"), 1) != layer:
            continue
        if S.child(node, "polygon") is None:
            continue
        written, effective, source = _zone_connection_mode(node)
        fat = S.arg_text(S.child(node, "filled_areas_thickness"), 1)
        # The promise is the HEADER, not just "a zone exists": a zone written
        # without `(filled_areas_thickness ...)` is the file that produced 386
        # clearance errors on a real board, so it must not pass silently.
        #
        # It is NOT checked against `want` exactly, because postconditions are
        # re-read from the FINAL tree: a plan may legitimately add a zone and then
        # call set_zone_params on it (that is the whole point of the pair), and a
        # postcondition that demanded the value this op wrote would fail on a plan
        # that did exactly what was asked. "The header states its fill
        # interpretation and its pad connection at all" is what this op owns.
        return {"ok": fat in ("yes", "no") and S.child(node, "connect_pads") is not None,
                "connect_pads": written,
                "connect_pads_effective": effective,
                "connect_pads_source": source,
                "connect_pads_asked_for": want,
                "filled_areas_thickness": fat,
                "filled_polygon_present":
                    S.child(node, "filled_polygon") is not None}
    return {"ok": False}

op("add_zone", {"copper"},
   "Add a copper pour on one layer, written in the form KiCad itself saves: "
   "(connect_pads [yes|no|thru_hole_only] (clearance X)), (min_thickness T), "
   "(filled_areas_thickness no), (fill yes [(thermal_gap G)] "
   "[(thermal_bridge_width W)]). No filled_polygon: the vendor engine pours it on "
   "open, plot, or when board_fill_zones is called. Reply carries fill_pending=true "
   "and a `header` block saying exactly what was written versus left to the EDA's "
   "own defaults.",
   {"points": "[[x,y], ...] or x0/y0/x1/y1", "layer": "copper layer",
    "net": "net name", "clearance": "mm", "min_thickness": "mm", "priority": "int",
    "connect_pads": "thermal (default) | solid | none | thru_hole_only",
    "thermal_gap": "mm (left to the EDA's own default when not given)",
    "thermal_bridge_width": "mm (same)"},
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
   "they are not a DRC run. KiCad 7+ reads net classes from the PROJECT file and "
   "IGNORES the ones written here - for those, use board_rules_write.",
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

# --- editing parts that are already on the board ----------------------
def _run_delete_footprint(ed, o, seed):
    ref = _str(o, "ref")
    fp = ed.footprint(ref)
    layer = S.arg_text(S.child(fp, "layer"), 1)
    at = S.child(fp, "at")
    pos = [S.arg_num(at, 1, 0.0), -S.arg_num(at, 2, 0.0)] if at is not None else None
    pads = S.children(fp, "pad")
    nets = []
    for pad in pads:
        node = S.child(pad, "net")
        name = S.arg_text(node, 2) if node is not None else None
        if name and name not in nets:
            nets.append(name)
    if not ed.remove(fp):
        raise OpError("footprint %r is not a child of the board" % ref, "OP_INVALID")
    return {"reference": ref, "at": pos, "layer": layer,
            "pads_removed": len(pads), "nets_released": sorted(nets)}

def _verify_delete_footprint(ed, o):
    ref = _str(o, "ref")
    alive = [ed.footprint_ref(n) for n in ed.footprints()]
    return {"ok": ref not in alive, "footprints_left": len(alive)}

op("delete_footprint", {"copper", "silk", "mech"},
   "Remove a placed footprint (its pads and silk go with it; nets keep whatever "
   "else still uses them). The board's outline and the libraries are untouched.",
   {"ref": "reference designator"},
   _run_delete_footprint, _verify_delete_footprint)

def _ensure_effects(node):
    """The text node's `(effects ...)`, created when the file has none.

    A hand-written or minimal footprint may carry `(fp_text value "10k" (at 0 2)
    (layer "F.Fab"))` with no effects block at all. Flipping it to the back must
    still produce mirrored text, so the empty block is created rather than the
    `mirror` word being silently dropped.
    """
    eff = S.child(node, "effects")
    if eff is None:
        eff = S.append_child(node, B.L(B.A("effects")))
    return eff

def _unmirror_justify(effects):
    """The inverse of `_mirror_justify`: drop the `mirror` word, keep the rest."""
    for node in S.children(effects, "justify"):
        node.items = [it for it in node.items
                      if not (isinstance(it, S.Atom) and it.text == "mirror")]
        return

def _flip_in_place(node, cur_angle, to_back):
    """Flip a placed footprint's body about its own vertical axis.

    Measured against KiCad 10.0 (`pcbnew.FOOTPRINT.Flip`, six combinations of
    footprint angle {0, 30, 90} x pad angle {0, 30}) rather than derived:

      * the footprint's `(at x y a)` becomes `180 - a`; the layer goes F.Cu <-> B.Cu
        (the caller writes those two, this function skips them like `_flip_body`)
      * a pad `(at dx dy q)` becomes `(at dx -dy 180-q)`. The pad angle in a board
        file is **absolute**, so `q` alone flips - subtracting the footprint
        angle as well is the mistake `_flip_body` cannot make because it always
        receives a library body whose pads were not rotated yet
      * every graphic point's dy is negated, an arc `(center x y a)` angle becomes
        `180 - a`
      * field text: dy negated, angle `180 - a`, and `mirror` in `justify` toggles

    The map is its own inverse (verified by the round-trip test), so one function
    serves both directions; `to_back` only says which way the `mirror` toggle goes.
    """
    back = (180.0 - float(cur_angle)) % 360.0

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
                _set_angle(child, (180.0 - float(local)) % 360.0)
            else:
                _set_angle(child, back)
        elif head == "effects":
            if to_back:
                _mirror_justify(child)
            else:
                _unmirror_justify(child)
            for sub in child.items[1:]:
                one(sub, in_pad)
        elif isinstance(child, S.Seq):
            child_pad = (head == "pad") or in_pad
            if to_back and head in ("fp_text", "property"):
                _mirror_justify(_ensure_effects(child))
            for sub in child.items[1:]:
                one(sub, child_pad)

    for child in node.items[1:]:
        if isinstance(child, S.Seq) and S.head(child) in _FOOTPRINT_DROP:
            continue
        one(child, False)

def _run_set_footprint_layer(ed, o, seed):
    ref = _str(o, "ref")
    want = _str(o, "layer")
    if want not in ("F.Cu", "B.Cu"):
        raise OpError("a footprint sits on F.Cu or B.Cu, not %r" % want,
                      "LAYER_NOT_ALLOWED")
    ed.require_layer(want)
    fp = ed.footprint(ref)
    lnode = S.child(fp, "layer")
    cur = S.arg_text(lnode, 1) if lnode is not None else None
    if cur not in ("F.Cu", "B.Cu"):
        raise OpError("footprint %r sits on %r, which is not a side" % (ref, cur),
                      "LAYER_NOT_ALLOWED")
    if cur == want:
        return {"reference": ref, "from": cur, "to": want, "changed": False}
    at = S.child(fp, "at")
    cur_angle = (S.arg_num(at, 3, 0.0) or 0.0) if at is not None else 0.0
    _flip_in_place(fp, cur_angle, want == "B.Cu")
    if at is not None:
        _set_angle(at, (180.0 - float(cur_angle)) % 360.0)
    if lnode is not None:
        lnode.items[1].text = want
    else:
        S.insert_child(fp, 1, B.L(B.A("layer"), B.A(want, True)))
    return {"reference": ref, "from": cur, "to": want, "changed": True,
            "file_angle_before": cur_angle,
            "file_angle_after": (180.0 - float(cur_angle)) % 360.0}

def _verify_set_footprint_layer(ed, o):
    fp = ed.footprint(_str(o, "ref"))
    want = _str(o, "layer")
    lnode = S.child(fp, "layer")
    cur = S.arg_text(lnode, 1) if lnode is not None else None
    opposite = "F." if want == "B.Cu" else "B."
    wrong = 0
    for pad in S.children(fp, "pad"):
        for lay in S.children(pad, "layers"):
            names = [it.text for it in lay.items[1:] if isinstance(it, S.Atom)]
            if any(n.startswith(opposite) for n in names):
                wrong += 1
    return {"ok": cur == want and wrong == 0, "layer": cur,
            "pads_on_the_other_side": wrong}

op("set_footprint_layer", {"copper", "silk", "mech"},
   "Flip a placed footprint between the front and the back of the board, mirroring "
   "its pads, silk and fields the way KiCad's own 'flip' does (measured against "
   "pcbnew, not derived). The footprint keeps its position.",
   {"ref": "reference designator", "layer": "F.Cu or B.Cu"},
   _run_set_footprint_layer, _verify_set_footprint_layer)

def _net_uses(ed, code):
    """Every place a net code is referenced, with enough detail to report it."""
    use = {"pads": [], "tracks": 0, "vias": 0, "zones": 0}
    for fp in ed.footprints():
        ref = ed.footprint_ref(fp)
        for pad in S.children(fp, "pad"):
            node = S.child(pad, "net")
            if node is not None and S.arg_text(node, 1) == code:
                use["pads"].append([ref, S.arg_text(pad, 1)])
    for kind in ("segment", "arc"):
        for node in ed.items(kind):
            if S.arg_text(S.child(node, "net"), 1) == code:
                use["tracks"] += 1
    for node in ed.items("via"):
        if S.arg_text(S.child(node, "net"), 1) == code:
            use["vias"] += 1
    for node in ed.items("zone"):
        if S.arg_text(S.child(node, "net"), 1) == code:
            use["zones"] += 1
    return use

def _strip_net_from(node, code):
    for child in list(node.items[1:]):
        if isinstance(child, S.Seq) and S.head(child) == "net" \
                and S.arg_text(child, 1) == code:
            node.items.remove(child)
            return True
    return False

def _run_delete_net(ed, o, seed):
    name = _str(o, "net")
    if name not in ed.net_by_name:
        raise OpError("net %r does not exist on this board" % name, "NET_NOT_FOUND")
    code = ed.net_by_name[name]
    if not name or str(code) in ("0", "None"):
        raise OpError("net 0 is the unnamed net; it is not an editable net",
                      "NET_NOT_DELETABLE")
    use = _net_uses(ed, code)
    total = len(use["pads"]) + use["tracks"] + use["vias"] + use["zones"]
    force = _bool(o, "force", False)
    if total and not force:
        raise OpError(
            "net %r is still used by %d pads, %d tracks, %d vias and %d zones; "
            "pass force=true to unassign all of them and delete the net"
            % (name, len(use["pads"]), use["tracks"], use["vias"], use["zones"]),
            "NET_IN_USE",
            hint="deleting the declaration alone would leave copper claiming a net that "
            "no longer exists, which DRC reports as a phantom net")
    unassigned = 0
    if force:
        for fp in ed.footprints():
            for pad in S.children(fp, "pad"):
                if _strip_net_from(pad, code):
                    unassigned += 1
        for kind in ("segment", "arc"):
            for node in ed.items(kind):
                if _strip_net_from(node, code):
                    unassigned += 1
        for node in ed.items("via"):
            if _strip_net_from(node, code):
                unassigned += 1
        for node in ed.items("zone"):
            if S.arg_text(S.child(node, "net"), 1) == code:
                ed.remove(node)
    for node in ed.items("net"):
        if S.arg_text(node, 1) == code:
            ed.remove(node)
    if name in ed.net_by_name:
        del ed.net_by_name[name]
    return {"net": name, "net_code": code, "uses_before": {
                "pads": len(use["pads"]), "tracks": use["tracks"],
                "vias": use["vias"], "zones": use["zones"]},
            "unassigned": unassigned, "force": force}

def _verify_delete_net(ed, o):
    name = _str(o, "net")
    left = _net_uses(ed, ed.net_by_name.get(name))
    total = len(left["pads"]) + left["tracks"] + left["vias"] + left["zones"]
    return {"ok": name not in ed.net_by_name and total == 0,
            "still_declared": name in ed.net_by_name, "references_left": total}

op("delete_net", {"copper", "meta"},
   "Delete a net declaration. Refuses while pads, tracks, vias or zones still "
   "belong to it unless force=true, which unassigns them first - a declaration "
   "that vanished while copper still names it is a phantom net in DRC.",
   {"net": "net name", "force": "bool - unassign every user of the net first"},
   _run_delete_net, _verify_delete_net)

def _select_zones(ed, o):
    net = o.get("net")
    layer = o.get("layer")
    uid = o.get("uuid")
    if not net and not layer and not uid:
        raise OpError("this operation needs a selector: 'net', 'layer' or 'uuid'",
                      "ARG_MISSING")
    code = None
    if net:
        code = ed.net_by_name.get(net)
        if code is None:
            raise OpError("net %r does not exist on this board" % net, "NET_NOT_FOUND")
    out = []
    for node in ed.items("zone"):
        if uid and ed.sp.id_of(node) != uid:
            continue
        if code is not None and S.arg_text(S.child(node, "net"), 1) != code:
            continue
        if layer and S.arg_text(S.child(node, "layer"), 1) != layer:
            continue
        out.append(node)
    return out

def _zone_label(ed, node):
    return {"net": S.arg_text(S.child(node, "net_name"), 1) or "",
            "layer": S.arg_text(S.child(node, "layer"), 1),
            "uuid": ed.sp.id_of(node)}

def _zone_uuids(ed, nodes):
    """The object ids of these zones, in whichever spelling the file uses.

    A pre-v7 board writes `(tstamp ...)` and KiCad 10 writes `(uuid ...)`; the
    postcondition is about *these objects*, so it has to read both - an id reader
    that only knows `uuid` reports "deleted 0" and then verifies nothing.
    """
    out = []
    for node in nodes:
        uid = ed.sp.id_of(node)
        if uid:
            out.append(uid)
    return out

def _run_delete_zone(ed, o, seed):
    targets = _select_zones(ed, o)
    if not targets:
        raise OpError("no zone matches that selector", "TARGET_NOT_FOUND")
    gone = [_zone_label(ed, n) for n in targets]
    uuids = _zone_uuids(ed, targets)
    for node in targets:
        ed.remove(node)
    return {"zones_removed": len(gone), "zones": gone, "deleted_uuids": uuids}

def _zones_by_uuid(ed, uuids):
    want = set(u or "" for u in uuids)
    out = []
    for node in S.children(ed.doc.tree, "zone"):
        if ed.sp.id_of(node) in want:
            out.append(node)
    return out

def _verify_delete_zone(ed, o, record=None):
    """Did the zones THIS operation removed actually go?

    Checked by uuid, not by "does the selector still match anything". The
    selector form breaks a legitimate plan - delete the GND pour and add a new
    one on the same net/layer in the same apply recorded `remaining_matches: 1`
    and was reported as a failed postcondition, with a message that named neither
    the cause nor the cure. The zones this operation deleted are known objects,
    so their absence is the postcondition; a zone added afterwards by a later
    operation is not this operation's business.
    """
    try:
        left = _select_zones(ed, o)
    except OpError:
        left = []
    deleted = ((record or {}).get("detail") or {}).get("deleted_uuids") or []
    if not deleted:
        # no record (older caller): fall back to the selector, and say which
        # reading was used so a false red is not mistaken for a real one
        return {"ok": not left, "remaining_matches": len(left),
                "checked_by": "selector (no run record was supplied)"}
    still = _zones_by_uuid(ed, deleted)
    return {"ok": not still,
            "checked_by": "uuid of the zone(s) this operation removed",
            "deleted_uuids": len(deleted),
            "still_present": len(still),
            "remaining_matches": len(left),
            "remaining_matches_are_other_zones": len(left) if not still else 0}

# ------------------------------------------------------------------ bulk deletes
# Why a bulk delete exists at all: ripping up a pour region or clearing every
# track of a net used to mean one `delete_track(uuid=...)` per object - a real
# session emitted 528 of them for one board. A plan with 528 operations is not
# more auditable than one; it is 528 chances for an index to be off by one, and
# it makes the plan unreadable to a person.
#
# The rules are the same as the single-object deletes, only the selector is
# wider: at least one selector (net / layer / region) is REQUIRED, a bare
# "delete all copper" is still not an operation, locked copper still refuses,
# and the postcondition is checked against the uuids that were actually removed.

def _region(o):
    r = o.get("region")
    if r is None:
        return None
    if not isinstance(r, (list, tuple)) or len(r) != 4:
        raise OpError("'region' must be [x1, y1, x2, y2] in mm, Y up",
                      "ARG_INVALID",
                      hint="the frame is the one board_view reports: millimetres, "
                           "Y up, origin at the board's own origin")
    x1, y1, x2, y2 = [float(v) for v in r]
    return (min(x1, x2), min(y1, y2), max(x1, x2), max(y1, y2))

def _xy_in(rect, x, y):
    return rect[0] <= x <= rect[2] and rect[1] <= y <= rect[3]

def _node_xy(node):
    """Every (x, y) this node spells, in mm Y up - the frame the caller uses.

    The file stores Y DOWN (`(at 10 -20)` is y = +20 mm up), so every point is
    negated on the way out. Only the tags that carry points are read; a `(width)`
    or a `(layer)` has no coordinate and must never be mistaken for one.
    """
    out = []
    for tag in ("start", "end", "mid", "at", "center"):
        n = S.child(node, tag)
        if n is None:
            continue
        x, y = S.arg_num(n, 1), S.arg_num(n, 2)
        if x is not None and y is not None:
            out.append((x, -y))
    for path, sub in S.walk(node):
        if S.head(sub) == "xy":
            x, y = S.arg_num(sub, 1), S.arg_num(sub, 2)
            if x is not None and y is not None:
                out.append((x, -y))
    return out

def _node_layers(node):
    got = []
    for tag in ("layer", "layers"):
        n = S.child(node, tag)
        if n is None:
            continue
        for i in range(1, len(n.items)):
            text = S.arg_text(n, i)
            if text:
                got.append(str(text))
    return got

def _code_name(ed, node):
    code = S.arg_text(S.child(node, "net"), 1)
    if not code:
        return None
    return ed.name_by_code.get(code, code)

def _select_copper(ed, o):
    kinds = o.get("kinds") or "all"
    if isinstance(kinds, str):
        kinds = [kinds]
    kinds = [str(k).lower() for k in kinds]
    if "all" in kinds:
        kinds = ["tracks", "vias", "zones"]
    unknown = [k for k in kinds if k not in ("tracks", "vias", "zones")]
    if unknown:
        raise OpError("unknown kind(s) %s" % ", ".join(unknown), "ARG_INVALID",
                      hint="kinds is any of tracks, vias, zones, all")
    net = o.get("net")
    layer = o.get("layer")
    rect = _region(o)
    if not net and not layer and rect is None:
        raise OpError("delete_copper needs at least one selector: 'net', 'layer' "
                      "or 'region' (a bare 'delete all copper' is not an "
                      "operation)", "ARG_MISSING")
    if net and net not in ed.net_by_name:
        raise OpError("net %r does not exist" % net, "NET_NOT_FOUND")
    heads = {"tracks": ("segment", "arc"), "vias": ("via",), "zones": ("zone",)}
    out, zones_not_inside = [], 0
    for kind in kinds:
        for head in heads[kind]:
            for node in ed.items(head):
                if net and _code_name(ed, node) != net:
                    continue
                if layer and layer not in _node_layers(node):
                    continue
                if rect is not None:
                    pts = _node_xy(node)
                    if not pts:
                        continue
                    if kind == "zones":
                        # A zone is deleted WHOLE or not at all: deleting a pour
                        # that merely crosses the region would take copper the
                        # caller never saw. Partial overlap is counted and
                        # reported instead of being acted on.
                        if not all(_xy_in(rect, x, y) for x, y in pts):
                            zones_not_inside += 1
                            continue
                    elif not any(_xy_in(rect, x, y) for x, y in pts):
                        continue
                out.append((kind, node))
    return out, zones_not_inside

def _run_delete_copper(ed, o, seed):
    targets, zones_not_inside = _select_copper(ed, o)
    if not targets:
        raise OpError("no copper matches those selectors", "TARGET_NOT_FOUND",
                      hint=("a zone is only deleted when its WHOLE outline is "
                            "inside 'region'; %d zone(s) crossed it" % zones_not_inside
                            if zones_not_inside else None))
    nodes = [n for _, n in targets]
    _refuse_fixed(ed, "selected copper object(s)", _fixed_nodes(nodes),
                  len(nodes), o)
    by_kind = {}
    uuids = []
    for kind, node in targets:
        by_kind[kind] = by_kind.get(kind, 0) + 1
        uid = ed.sp.id_of(node)
        if uid:
            uuids.append(uid)
        ed.remove(node)
    return {"nodes_removed": len(nodes), "by_kind": by_kind,
            "uuids": uuids,
            "zones_skipped_partial": zones_not_inside}

def _verify_delete_copper(ed, o, record=None):
    """Did the objects THIS operation removed go? Checked by uuid, never by
    re-running the selector: a later operation may legitimately add copper back
    in the same region."""
    deleted = ((record or {}).get("detail") or {}).get("uuids") or []
    if not deleted:
        left, _ = _select_copper(ed, o)
        return {"ok": not left, "remaining_matches": len(left),
                "checked_by": "selectors (no run record was supplied)"}
    alive = set()
    for kind, heads in (("tracks", ("segment", "arc")), ("vias", ("via",)),
                        ("zones", ("zone",))):
        for head in heads:
            for node in ed.items(head):
                uid = ed.sp.id_of(node)
                if uid:
                    alive.add(uid)
    still = [u for u in deleted if u in alive]
    return {"ok": not still, "checked_by": "uuid of the object(s) this operation "
                                          "removed",
            "deleted": len(deleted), "still_present": len(still),
            "remaining_matches": len(_select_copper(ed, o)[0]),
            "remaining_matches_are_other_objects": not still}

op("delete_copper", {"copper"},
   "Delete copper in BULK by net / layer / region, instead of one uuid per "
   "operation (ripping up a region used to need one delete_track per segment). At "
   "least one selector is required. A zone is deleted only when its whole outline "
   "is inside 'region'. Locked copper refuses unless allow_fixed=true. The "
   "postcondition is checked against the uuids actually removed.",
   {"net": "net name", "layer": "layer name",
    "region": "[x1, y1, x2, y2] mm, Y up - segment/via end points, zone outline",
    "kinds": "tracks | vias | zones | all (default all); list allowed",
    "allow_fixed": "bool: also delete copper the file marks (locked)"},
   _run_delete_copper, _verify_delete_copper)

op("delete_zone", {"copper"},
   "Delete copper zones (pours or keep-outs) by net, layer, uuid or a combination. "
   "Refuses without a selector - 'delete every zone' is not an operation. Safe to "
   "combine with add_zone on the same net/layer in one plan: the postcondition "
   "checks the uuids this operation removed, not 'nothing matches the selector any "
   "more' (use delete_copper for a bulk/region delete).",
   {"net": "net name", "layer": "layer name", "uuid": "zone uuid"},
   _run_delete_zone, _verify_delete_zone)

def _run_set_zone_priority(ed, o, seed):
    priority = int(_num(o, "priority"))
    if priority < 0:
        raise OpError("priority must be >= 0", "ARG_INVALID")
    targets = _select_zones(ed, o)
    if not targets:
        raise OpError("no zone matches that selector", "TARGET_NOT_FOUND")
    changed = []
    for node in targets:
        node_ = S.child(node, "priority")
        if node_ is not None:
            for i, item in enumerate(node.items):
                if item is node_:
                    node.items[i] = B.L(B.A("priority"), B.A(str(priority)))
                    break
        else:
            # KiCad reads `(priority N)` as a child of `(zone ...)`; verified by
            # loading the written board in pcbnew and asking the zone for its
            # assigned priority.
            S.append_child(node, B.L(B.A("priority"), B.A(str(priority))))
        changed.append(_zone_label(ed, node))
    return {"zones_changed": len(changed), "priority": priority, "zones": changed}

def _verify_set_zone_priority(ed, o):
    want = int(_num(o, "priority"))
    got = []
    for node in _select_zones(ed, o):
        p = S.arg_num(S.child(node, "priority"), 1)
        got.append(None if p is None else int(round(p)))
    return {"ok": bool(got) and all(g == want for g in got), "priorities": got}

op("set_zone_priority", {"copper"},
   "Set the fill priority of the zones a selector matches (higher wins where pours "
   "overlap). Same selector rules as delete_zone.",
   {"priority": "integer >= 0", "net": "net name", "layer": "layer name",
    "uuid": "zone uuid"},
   _run_set_zone_priority, _verify_set_zone_priority)

ISLAND_MODES = {"always": 0, "never": 1, "area": 2}

def _island_mode(raw):
    """KiCad spells island removal as an integer: 0 always, 1 never, 2 by area."""
    if isinstance(raw, bool) or raw is None:
        raise OpError("island_removal_mode takes always/never/area (or 0/1/2), "
                      "not %r" % (raw,), "ARG_INVALID")
    if isinstance(raw, int):
        if raw not in (0, 1, 2):
            raise OpError("island_removal_mode must be 0, 1 or 2", "ARG_INVALID")
        return raw
    text = str(raw).strip().lower()
    if text in ISLAND_MODES:
        return ISLAND_MODES[text]
    if text.isdigit() and int(text) in (0, 1, 2):
        return int(text)
    raise OpError("island_removal_mode takes always/never/area (or 0/1/2), "
                  "not %r" % (raw,), "ARG_INVALID")

def _zone_flag(raw):
    """yes/no as KiCad spells it; booleans are accepted as the same thing."""
    if isinstance(raw, bool):
        return "yes" if raw else "no"
    text = str(raw).strip().lower()
    if text in ("yes", "no"):
        return text
    raise OpError("filled_areas_thickness takes yes/no (or true/false), not %r"
                  % (raw,), "ARG_INVALID")

def _zone_set_child(zone, name, value_node, after=None):
    """Replace `(name ...)` in place, or insert it right after `after`.

    Position matters only for diffs, not for the parser, but keeping the field
    where KiCad itself writes it (after `min_thickness`) means a file written
    by this server and a file written by KiCad differ in exactly the values.
    """
    node = S.child(zone, name)
    if node is not None:
        zone.items[list(zone.items).index(node)] = value_node
        return
    anchor = S.child(zone, after) if after else None
    if anchor is None:
        S.append_child(zone, value_node)
    else:
        zone.items.insert(list(zone.items).index(anchor) + 1, value_node)

def _zone_connection_mode(node):
    """What the file states about pad connection, and what KiCad reads out of it.

    `(connect_pads yes ...)` is SOLID copper, `no` is no connection,
    `thru_hole_only` is the third mode, and a THERMAL relief is spelled by
    writing no mode word at all - that is what KiCad 10.0.7's own `SaveBoard`
    emits for `SetPadConnection(ZONE_CONNECTION_THERMAL)`. Loading each spelling
    back in pcbnew and asking `GetPadConnection()` returns THERMAL for the
    word-less form, so "nothing written" is a fact about the meaning, not a
    guess about a default.
    """
    conn = S.child(node, "connect_pads")
    if conn is None:
        return None, "thermal", "absent"
    words = [item.text for item in conn.items[1:] if isinstance(item, S.Atom)]
    for word, mode in (("yes", "solid"), ("no", "none"),
                       ("thru_hole_only", "thru_hole_only")):
        if word in words:
            return mode, mode, "file"
    return None, "thermal", "kicad_default"

_ZONE_CONNECTION_MODES = {"thermal": None, "solid": "yes", "yes": "yes",
                          "none": "no", "no": "no",
                          "thru_hole_only": "thru_hole_only"}

def _zone_connection_word(raw):
    text = str(raw).strip().lower()
    if text not in _ZONE_CONNECTION_MODES:
        raise OpError("connect_pads takes thermal / solid / none / thru_hole_only "
                      "(got %r). A thermal relief is the word-less form; `yes` is "
                      "solid copper" % (raw,), "ARG_INVALID")
    return text

def _run_set_zone_params(ed, o, seed):
    given = {}
    if o.get("filled_areas_thickness") is not None:
        given["filled_areas_thickness"] = _zone_flag(o.get("filled_areas_thickness"))
    for key in ("clearance", "min_thickness", "thermal_gap",
                "thermal_bridge_width"):
        if o.get(key) is not None:
            value = _num(o, key)
            if value <= 0:
                raise OpError("%s must be > 0 (got %r)" % (key, value),
                              "ARG_INVALID")
            given[key] = value
    if o.get("connect_pads") is not None:
        given["connect_pads"] = _zone_connection_word(o.get("connect_pads"))
    if o.get("island_removal_mode") is not None:
        given["island_removal_mode"] = _island_mode(o.get("island_removal_mode"))
    if o.get("island_removal_area_threshold") is not None:
        value = _num(o, "island_removal_area_threshold")
        if value < 0:
            raise OpError("island_removal_area_threshold must be >= 0 (mm2)",
                          "ARG_INVALID")
        given["island_removal_area_threshold"] = value
    if not given:
        raise OpError("set_zone_params needs at least one of: "
                      "filled_areas_thickness, clearance, min_thickness, "
                      "connect_pads, thermal_gap, thermal_bridge_width, "
                      "island_removal_mode, island_removal_area_threshold",
                      "ARG_MISSING")
    targets = _select_zones(ed, o)
    if not targets:
        raise OpError("no zone matches that selector", "TARGET_NOT_FOUND")
    changed = []
    dropped = 0
    for node in targets:
        if S.child(node, "keepout") is not None:
            raise OpError(
                "the zone on %s is a keep-out, not a pour; a keep-out has no "
                "fill parameters" % (_zone_label(ed, node)["layer"],),
                "ARG_INVALID")
        before = _zone_params(node)
        if "filled_areas_thickness" in given:
            _zone_set_child(node, "filled_areas_thickness",
                            B.L(B.A("filled_areas_thickness"),
                                B.A(given["filled_areas_thickness"])),
                            after="min_thickness")
        if "min_thickness" in given:
            _zone_set_child(node, "min_thickness",
                            B.L(B.A("min_thickness"),
                                B.A(B.num(given["min_thickness"]))))
        if "connect_pads" in given:
            _zone_set_connection(node, given["connect_pads"],
                                 given.get("clearance"))
        elif "clearance" in given:
            conn = S.child(node, "connect_pads")
            if conn is None:
                S.append_child(node, B.L(
                    B.A("connect_pads"),
                    B.L(B.A("clearance"), B.A(B.num(given["clearance"])))))
            else:
                _zone_set_child(conn, "clearance",
                                B.L(B.A("clearance"),
                                    B.A(B.num(given["clearance"]))))
        for key in ("thermal_gap", "thermal_bridge_width"):
            if key in given:
                _zone_set_fill_number(node, key, given[key])
        if "island_removal_mode" in given:
            # Measured: pcbnew writes these INSIDE `(fill ...)`, next to the
            # thermal numbers - `(fill (thermal_gap 0.5) ... (island_removal_mode
            # 1))`. Written one level up, KiCad 10 refuses the whole board with
            # "应为 net, layer/layers, tstamp, hatch, priority, connect_pads,
            # min_thickness, fill, polygon ...".
            _zone_set_fill_atom(node, "island_removal_mode",
                                given["island_removal_mode"])
        if "island_removal_area_threshold" in given:
            _zone_set_fill_atom(node, "island_removal_area_threshold",
                                given["island_removal_area_threshold"])
        # The filled polygons describe the copper of the OLD parameters. Keeping
        # them would leave the file claiming a pour that the new rules do not
        # produce - the exact "intermediate layer says one thing, the board says
        # another" failure this project exists to avoid.
        for poly in list(S.children(node, "filled_polygon")):
            S.remove_child(node, poly)
            dropped += 1
        after = _zone_params(node)
        if after != before:
            changed.append(_zone_label(ed, node))
    # `zones_matched` is reported separately from `zones_changed` because a zone
    # already carrying the asked-for value is still re-poured (its fill is
    # dropped below), so "0 changed / 2 matched" is a real state and not a
    # contradiction - the counts would otherwise read as "nothing happened".
    return {"zones_changed": len(changed), "zones": changed,
            "zones_matched": len(targets),
            "params": given, "dropped_filled_polygons": dropped,
            "refill_required": True}

def _zone_set_connection(node, mode, clearance=None):
    """Rewrite `connect_pads` with an explicit mode word (or none, for thermal).

    Rebuilt rather than patched because the mode word lives at a variable
    position: KiCad writes at most one, and a second one would be a mode this
    server invented. A zone that states no `connect_pads` at all can only be
    given one when the caller also says what clearance it asks for - the
    clearance is not the EDA's default to invent here.
    """
    word = _ZONE_CONNECTION_MODES[mode]
    conn = S.child(node, "connect_pads")
    if clearance is None and conn is not None:
        clearance = S.arg_num(S.child(conn, "clearance"), 1)
    if clearance is None:
        raise OpError("this zone states no connect_pads (its clearance is not in "
                      "the file either), so pass clearance with connect_pads",
                      "ARG_INVALID")
    head = [B.A("connect_pads")] + ([B.A(word)] if word else [])
    head.append(B.L(B.A("clearance"), B.A(B.num(clearance))))
    new = B.L(*head)
    if conn is not None:
        S.reformat(new, conn.lead)
        node.items[list(node.items).index(conn)] = new
    else:
        S.append_child(node, new)


def _zone_set_fill_atom(node, key, value):
    """Write `(key atom)` inside the zone's `(fill ...)` block, verbatim."""
    fill = S.child(node, "fill")
    if fill is None or (S.arg_text(fill, 1) or "").lower() in ("no", "false"):
        raise OpError("%s only describes a pour, and this zone does not fill "
                      "(no `(fill yes ...)`), so there is nothing to write it "
                      "into" % (key,), "ARG_INVALID")
    text = B.num(value) if isinstance(value, float) else str(value)
    existing = S.child(fill, key)
    node_new = B.L(B.A(key), B.A(text))
    if existing is not None:
        S.reformat(node_new, existing.lead)
        fill.items[list(fill.items).index(existing)] = node_new
    else:
        S.append_child(fill, node_new)

def _zone_set_fill_number(node, key, value):
    """Put `(key value)` inside the zone's `(fill ...)` block.

    The thermal numbers live there, not next to `min_thickness`. A zone that
    does not fill (`(fill no)`, or no `fill` node at all) has no thermal relief
    to describe, so asking for one is refused rather than written into a block
    KiCad will ignore.
    """
    fill = S.child(node, "fill")
    if fill is None or (S.arg_text(fill, 1) or "").lower() in ("no", "false"):
        raise OpError("%s only describes a thermal relief, and this zone does not "
                      "fill (no `(fill yes ...)`), so there is nothing to write it "
                      "into" % (key,), "ARG_INVALID")
    existing = S.child(fill, key)
    node_new = B.L(B.A(key), B.A(B.num(value)))
    if existing is not None:
        S.reformat(node_new, existing.lead)
        fill.items[list(fill.items).index(existing)] = node_new
    else:
        S.append_child(fill, node_new)


def _zone_params(node):
    conn = S.child(node, "connect_pads")
    fill = S.child(node, "fill")
    written, effective, source = _zone_connection_mode(node)
    return {
        "filled_areas_thickness": S.arg_text(
            S.child(node, "filled_areas_thickness"), 1),
        "clearance": (S.arg_num(S.child(conn, "clearance"), 1)
                      if conn is not None else None),
        "min_thickness": S.arg_num(S.child(node, "min_thickness"), 1),
        "connect_pads": written,
        "connect_pads_effective": effective,
        "connect_pads_source": source,
        "thermal_gap": S.arg_num(S.child(fill, "thermal_gap"), 1)
                       if fill is not None else None,
        "thermal_bridge_width": S.arg_num(
            S.child(fill, "thermal_bridge_width"), 1)
            if fill is not None else None,
        "island_removal_mode": S.arg_num(
            S.child(fill, "island_removal_mode"), 1)
            if fill is not None else None,
        "island_removal_area_threshold": S.arg_num(
            S.child(fill, "island_removal_area_threshold"), 1)
            if fill is not None else None,
    }

def _verify_set_zone_params(ed, o):
    want = {}
    if o.get("filled_areas_thickness") is not None:
        want["filled_areas_thickness"] = _zone_flag(o.get("filled_areas_thickness"))
    for key in ("clearance", "min_thickness", "thermal_gap",
                "thermal_bridge_width"):
        if o.get(key) is not None:
            want[key] = float(_num(o, key))
    if o.get("connect_pads") is not None:
        want["connect_pads"] = _zone_connection_word(o.get("connect_pads"))
    if o.get("island_removal_mode") is not None:
        want["island_removal_mode"] = _island_mode(o.get("island_removal_mode"))
    if o.get("island_removal_area_threshold") is not None:
        want["island_removal_area_threshold"] = float(
            _num(o, "island_removal_area_threshold"))
    checked = []
    ok = True
    for node in _select_zones(ed, o):
        got = _zone_params(node)
        row = dict(_zone_label(ed, node))
        for key, value in want.items():
            if key == "filled_areas_thickness":
                row[key] = got[key]
                ok = ok and got[key] == value
            elif key == "connect_pads":
                # the postcondition is about the COPPER KiCad will pour, so it is
                # checked on what pcbnew reads out of the spelling, not on which
                # word happens to be written
                row["connect_pads"] = got["connect_pads"]
                row["connect_pads_effective"] = got["connect_pads_effective"]
                ok = ok and got["connect_pads_effective"] == value
            elif key in ("island_removal_mode", "island_removal_area_threshold"):
                have = got.get(key)
                row[key] = have
                ok = ok and have is not None and abs(have - value) < 1e-9
            else:
                have = got.get(key)
                row[key] = have
                ok = ok and have is not None and abs(have - value) < 1e-9
        # Whatever the previous fill said, it described the old parameters.
        row["filled_polygons"] = len(S.children(node, "filled_polygon"))
        ok = ok and row["filled_polygons"] == 0
        checked.append(row)
    return {"ok": bool(checked) and ok, "zones": checked}

op("set_zone_params", {"copper"},
   "Change a pour's fill parameters in place - filled_areas_thickness (yes/no), "
   "clearance, min_thickness, connect_pads (thermal/solid/none/thru_hole_only), "
   "thermal_gap, thermal_bridge_width. No delete-and-re-add: the zone keeps its "
   "outline, uuid and priority. The zones' filled_polygon children are dropped, "
   "because they were computed from the old parameters; call fill afterwards. "
   "filled_areas_thickness=no tells KiCad the stored polygon IS the copper edge "
   "instead of a centreline to be stroked by min_thickness/2 - without it DRC "
   "reads every pour as half a line thicker than the polygon in the file. "
   "connect_pads=thermal is the word-less spelling KiCad itself writes; `solid` "
   "writes `yes`, which is a different pour. island_removal_mode decides what "
   "the engine does with copper that reaches no pad: `always` (KiCad's default) "
   "drops it, `never` keeps it, `area` keeps islands above the threshold - a "
   "copied board that leaves islands on pours unconnected loses them under the "
   "default.",
   {"filled_areas_thickness": "yes/no (bool accepted)", "clearance": "mm",
    "min_thickness": "mm",
    "connect_pads": "thermal | solid | none | thru_hole_only",
    "thermal_gap": "mm (only a pouring zone has a relief to describe)",
    "thermal_bridge_width": "mm",
    "island_removal_mode": "always | never | area",
    "island_removal_area_threshold": "mm2, only with mode=area",
    "net": "net name", "layer": "layer name", "uuid": "zone uuid"},
   _run_set_zone_params, _verify_set_zone_params)

def _netclass_members(ed, node):
    return [S.arg_text(n, 1) for n in S.children(node, "add_net")]

def _run_assign_netclass(ed, o, seed):
    net = _str(o, "net")
    cls = _str(o, "netclass")
    if net not in ed.net_by_name:
        raise OpError("net %r does not exist on this board" % net, "NET_NOT_FOUND")
    target = None
    classes = []
    for node in ed.items("net_class"):
        name = S.arg_text(node, 1)
        classes.append(name)
        if name == cls:
            target = node
    if target is None:
        raise OpError("net class %r does not exist (classes: %s)"
                      % (cls, ", ".join(c or "?" for c in classes)),
                      "NETCLASS_NOT_FOUND",
                      hint="create the class first with set_netclass, which also decides "
                      "its clearance and widths")
    moved_from = []
    already = False
    for node in ed.items("net_class"):
        name = S.arg_text(node, 1)
        members = _netclass_members(ed, node)
        if net not in members:
            continue
        if name == cls:
            already = True
            continue
        # A net belongs to exactly one class in KiCad; moving it is the honest
        # reading of "assign", and the old membership is reported, not hidden.
        members_now = []
        for child in list(S.children(node, "add_net")):
            if S.arg_text(child, 1) == net:
                node.items.remove(child)
            else:
                members_now.append(S.arg_text(child, 1))
        moved_from.append({"netclass": name, "members_left": members_now})
    if not already:
        S.append_child(target, B.L(B.A("add_net"), B.A(net, True)))
    return {"net": net, "netclass": cls, "changed": not already,
            "moved_from": moved_from}

def _verify_assign_netclass(ed, o):
    net = _str(o, "net")
    cls = _str(o, "netclass")
    homes = []
    for node in ed.items("net_class"):
        if net in _netclass_members(ed, node):
            homes.append(S.arg_text(node, 1))
    return {"ok": homes == [cls], "net_classes_containing_the_net": homes}

op("assign_netclass", {"meta", "copper"},
   "Put a net in a net class (creating the membership, moving it out of any other "
   "class, because a net belongs to exactly one). The class itself must exist - "
   "its rules are what later tools quote.",
   {"net": "net name", "netclass": "existing net class name"},
   _run_assign_netclass, _verify_assign_netclass)

def _run_set_pad_size(ed, o, seed):
    ref = _str(o, "ref")
    number = _str(o, "pad")
    fp = ed.footprint(ref)
    target = None
    have = []
    for pad in S.children(fp, "pad"):
        have.append(S.arg_text(pad, 1))
        if (S.arg_text(pad, 1) or "") == number:
            target = pad
    if target is None:
        raise OpError("footprint %r has no pad %r (it has: %s)"
                      % (ref, number, ", ".join(sorted(h for h in have if h))),
                      "PAD_NOT_FOUND")
    w = _num(o, "width", positive=True)
    h = _num(o, "height", required=False) or w
    size = S.child(target, "size")
    if size is None:
        raise OpError("pad %r has no (size ...) node - the file is malformed" % number,
                      "PAD_UNSUPPORTED")
    target.items = (target.items[:target.items.index(size)]
                    + [B.size(w, h)]
                    + target.items[target.items.index(size) + 1:])
    return {"reference": ref, "pad": number, "size": [w, h],
            "before": [S.arg_num(size, 1), S.arg_num(size, 2)]}

def _verify_set_pad_size(ed, o):
    ref = _str(o, "ref")
    number = _str(o, "pad")
    w = _num(o, "width")
    h = _num(o, "height", required=False) or w
    for pad in S.children(ed.footprint(ref), "pad"):
        if (S.arg_text(pad, 1) or "") != number:
            continue
        size = S.child(pad, "size")
        gw, gh = S.arg_num(size, 1), S.arg_num(size, 2)
        ok = (gw is not None and gh is not None and abs(gw - w) < 1e-9
              and abs(gh - h) < 1e-9)
        return {"ok": ok, "size": [gw, gh]}
    return {"ok": False, "error": "pad vanished"}

op("set_pad_size", {"copper"},
   "Resize one pad of a placed footprint (width, and height if it differs). The pad "
   "shape, drill and net stay as they are; DRC is the judge of whether the new "
   "size still meets clearance.",
   {"ref": "reference designator", "pad": "pad number", "width": "mm > 0",
    "height": "mm > 0 (default: the width)"},
   _run_set_pad_size, _verify_set_pad_size)

FIELD_SILK_OR_DOC_LAYERS = ("F.SilkS", "B.SilkS", "F.Fab", "B.Fab",
                           "F.CrtYd", "B.CrtYd", "Dwgs.User", "Cmts.User")


def _opt_mm(o, key):
    """An optional millimetre parameter: absent -> None, present -> a float.

    Deliberately stricter than `float()`: a string that is not a number, a bool
    and a NaN all mean the caller and the server disagree about units, and
    writing is not the place to find out.
    """
    if key not in o or o.get(key) is None:
        return None
    v = o.get(key)
    if isinstance(v, bool) or not isinstance(v, (int, float)):
        raise OpError("%r must be a number in millimetres (got %r)"
                      % (key, v), "ARG_INVALID")
    v = float(v)
    if v != v or v in (float("inf"), float("-inf")):
        raise OpError("%r must be finite" % key, "ARG_INVALID")
    return v


def _run_set_field(ed, o, seed):
    ref = _str(o, "ref")
    name = _str(o, "field")
    if "value" not in o:
        raise OpError("missing parameter 'value'", "ARG_MISSING")
    value = o.get("value")
    if not isinstance(value, str):
        raise OpError("'value' must be a string (use \"\" to clear a field)",
                      "ARG_INVALID")
    fp = ed.footprint(ref)
    target = None
    for kind in ("property", "fp_text"):
        for cand in S.children(fp, kind):
            if (S.arg_text(cand, 1) or "").lower() == name.lower():
                target = cand
                break
        if target is not None:
            break
    created = False
    if target is None:
        if not ed.sp.modern and name.lower() not in ("reference", "value"):
            raise OpError(
                "this board's format stores only Reference and Value as fp_text, "
                "so a new field called %r has nowhere to go; use a newer board "
                "format (KiCad 7+ writes `property`) or set an existing field"
                % name, "FIELD_NOT_FOUND")
        target = _field_node(name, value, 0.0, 0.0, ed.sp, "%s:%s" % (seed, name))
        S.append_child(fp, target)
        created = True
    else:
        if len(target.items) < 3:
            raise OpError("field %r is malformed (no value atom)" % name,
                          "FIELD_UNSUPPORTED")
        target.items[2] = B.A(value, True)
    visible = o.get("visible")
    if visible is not None:
        # `visible` is what the caller wants; `(hide yes)` is how the file spells
        # "not visible", so the two conditions read opposite ways on purpose.
        want = bool(visible)
        hide = S.child(target, "hide")
        if not want and hide is None:
            # `(hide yes)` as a direct child of the field is what both the v7
            # board format and the newer one accept - checked by loading the
            # written file in pcbnew and asking the field for IsVisible().
            S.append_child(target, B.L(B.A("hide"), B.A("yes")))
        elif want and hide is not None:
            target.items = [it for it in target.items if it is not hide]
    # --- geometry: which layer the text prints on, where, how big ----------
    # All three are optional and independent. A caller that names none of them
    # gets exactly the behaviour this operation had before it grew them: the
    # text changes and visibility changes, the geometry is left alone.
    moved = {}
    layer = o.get("layer")
    if layer is not None:
        if not isinstance(layer, str) or not layer:
            raise OpError("'layer' must be a layer name", "ARG_INVALID")
        ed.require_layer(layer, allowed=FIELD_SILK_OR_DOC_LAYERS)
        ln = S.child(target, "layer")
        if ln is None:
            S.append_child(target, B.L(B.A("layer"), B.A(layer, True)))
        else:
            ln.items = [B.A("layer"), B.A(layer, True)]
        moved["layer"] = layer

    want_x = _opt_mm(o, "x")
    want_y = _opt_mm(o, "y")
    if want_x is not None or want_y is not None:
        at = S.child(target, "at")
        if at is None:
            at = B.at(0.0, 0.0, 0)
            S.append_child(target, at)
        # `(at x y)` inside a footprint is in the footprint's own frame, whose Y
        # points DOWN like the file's. The parameter is Y-UP like every other
        # coordinate this server takes, so the sign flips exactly once here.
        nx = S.arg_num(at, 1, 0.0) if want_x is None else want_x
        ny_file = S.arg_num(at, 2, 0.0) if want_y is None else -want_y
        rot = S.arg_num(at, 3, None)
        at.items = [B.A("at"), B.A(B.num(nx)), B.A(B.num(ny_file))]
        if rot is not None:
            at.items.append(B.A(B.num(rot)))
        moved["x"] = nx
        moved["y"] = -ny_file
        moved["angle"] = rot

    want_h = _opt_mm(o, "text_height")
    want_w = _opt_mm(o, "text_width")
    if want_h is not None or want_w is not None:
        for nm, v in (("text_height", want_h), ("text_width", want_w)):
            if v is not None and v <= 0:
                raise OpError("%s must be positive (got %s mm)" % (nm, v),
                              "ARG_INVALID")
        effects = S.child(target, "effects")
        if effects is None:
            effects = B.L(B.A("effects"))
            S.append_child(target, effects)
        font = S.child(effects, "font")
        if font is None:
            font = B.L(B.A("font"))
            effects.items.append(font)
        node = S.child(font, "size")
        cur_w = S.arg_num(node, 1, 1.0) if node is not None else 1.0
        cur_h = S.arg_num(node, 2, 1.0) if node is not None else 1.0
        nw = cur_w if want_w is None else want_w
        nh = cur_h if want_h is None else want_h
        if node is None:
            font.items.append(B.size(nw, nh))
        else:
            node.items = [B.A("size"), B.A(B.num(nw)), B.A(B.num(nh))]
        moved["text_width"] = nw
        moved["text_height"] = nh

    return {"reference": ref, "field": name, "value": value,
            "created": created, "visible": visible,
            "geometry": moved or None}

def _verify_set_field(ed, o):
    ref = _str(o, "ref")
    name = _str(o, "field")
    value = o.get("value")
    try:
        fp = ed.footprint(ref)
    except OpError:
        # Renaming a Reference is the one case where the footprint can no longer
        # answer to the name the plan used: after the write the only handle left
        # is the new name. Before 0.0.98 this path raised REFERENCE_NOT_FOUND
        # and the write was rolled back, which made "rename a part" impossible.
        if name.lower() != "reference" or not isinstance(value, str) or not value:
            raise
        fp = ed.footprint(value)
    for kind in ("property", "fp_text"):
        for cand in S.children(fp, kind):
            if (S.arg_text(cand, 1) or "").lower() != name.lower():
                continue
            got = S.arg_text(cand, 2)
            out = {"field": name, "value": got, "ok": got == value}
            if o.get("visible") is not None:
                out["visible"] = S.child(cand, "hide") is None
                out["ok"] = out["ok"] and out["visible"] == bool(o.get("visible"))
            if o.get("layer"):
                ln = S.child(cand, "layer")
                out["layer"] = S.arg_text(ln, 1) if ln is not None else None
                out["ok"] = out["ok"] and out["layer"] == o["layer"]
            # Only the axes the caller named are checked: the other one keeps
            # whatever it had, and pretending to verify a number nobody asked
            # to change would turn a pass into a decoration.
            if o.get("x") is not None or o.get("y") is not None:
                at = S.child(cand, "at")
                if at is None:
                    out["ok"] = False
                    out["reason"] = "field has no (at) after the write"
                else:
                    checks = {}
                    if _opt_mm(o, "x") is not None:
                        checks["x"] = (S.arg_num(at, 1, None), _opt_mm(o, "x"))
                    if _opt_mm(o, "y") is not None:
                        got_y = S.arg_num(at, 2, None)
                        checks["y"] = (None if got_y is None else -got_y,
                                       _opt_mm(o, "y"))
                    out["position"] = {k: v[0] for k, v in checks.items()}
                    for k, (got, want) in checks.items():
                        if got is None or abs(got - want) > 1e-4:
                            out["ok"] = False
                            out["reason"] = ("%s reads %r, the plan said %r"
                                             % (k, got, want))
            if o.get("text_height") is not None or o.get("text_width") is not None:
                node = None
                effects = S.child(cand, "effects")
                font = S.child(effects, "font") if effects is not None else None
                if font is not None:
                    node = S.child(font, "size")
                sizes = {}
                if node is not None:
                    if _opt_mm(o, "text_width") is not None:
                        sizes["text_width"] = (S.arg_num(node, 1, None),
                                               _opt_mm(o, "text_width"))
                    if _opt_mm(o, "text_height") is not None:
                        sizes["text_height"] = (S.arg_num(node, 2, None),
                                                _opt_mm(o, "text_height"))
                else:
                    out["ok"] = False
                    out["reason"] = "field has no (effects (font (size ...)))"
                if sizes:
                    out["text_size"] = {k: v[0] for k, v in sizes.items()}
                for k, (got, want) in sizes.items():
                    if got is None or abs(got - want) > 1e-4:
                        out["ok"] = False
                        out["reason"] = ("%s reads %r, the plan said %r"
                                         % (k, got, want))
            return out
    return {"ok": False, "reason": "field not present after write"}

op("set_footprint_field", {"silk", "mech", "meta"},
   "Set the text of one field of a placed footprint (Reference, Value, or any "
   "property), show or hide it, and optionally move it to another "
   "silkscreen/documentation layer, another local position or another text "
   "size. Every geometry parameter is optional: name none of them and the "
   "field keeps the layer, position and size it had.",
   {"ref": "reference designator", "field": "field name, e.g. Reference",
    "value": "string (\"\" clears it)", "visible": "bool - optional",
    "layer": "layer name (F.SilkS / B.SilkS / F.Fab / B.Fab / *.CrtYd / "
             "Dwgs.User / Cmts.User) - optional",
    "x": "mm, footprint-local X, Y up - optional",
    "y": "mm, footprint-local Y up (the file's own Y is the negation) - optional",
    "text_height": "mm - optional", "text_width": "mm - optional"},
   _run_set_field, _verify_set_field)

def _run_mech_arc(ed, o, seed):
    layer = _layer(o, ed, allowed=list(MECH_LAYERS_RO) + list(SILK_LAYERS))
    w = _num(o, "width", required=False, default=0.15)
    ed.append(B.arc3("gr_arc", _num(o, "sx"), _num(o, "sy"), _num(o, "mx"),
                     _num(o, "my"), _num(o, "ex"), _num(o, "ey"), layer, w,
                     ed.sp, B.new_id(seed)))
    return {"nodes_added": 1, "layer": layer}

def _verify_mech_arc(ed, o):
    layer = _str(o, "layer")
    for node in ed.items("gr_arc"):
        if S.arg_text(S.child(node, "layer"), 1) != layer:
            continue
        st = S.child(node, "start")
        if st is None:
            continue
        if (abs(S.arg_num(st, 1, 0) - _num(o, "sx")) < 1e-4 and
                abs(-S.arg_num(st, 2, 0) - _num(o, "sy")) < 1e-4):
            return {"ok": True}
    return {"ok": False}

op("add_mech_arc", {"mech"},
   "An arc on a documentation/mechanical user layer (rounded assembly outline, "
   "connector keep-out arc).",
   {"sx": "mm start", "sy": "mm start", "mx": "mm mid (on the arc)",
    "my": "mm mid", "ex": "mm end", "ey": "mm end", "layer": "layer name",
    "width": "mm"},
   _run_mech_arc, _verify_mech_arc)

def _run_set_pad_net(ed, o, seed):
    ref = _str(o, "ref")
    number = _str(o, "pad")
    if "net" not in o:
        raise OpError("missing parameter 'net' (use \"\" to leave the pad unassigned)",
                      "ARG_MISSING")
    net = o.get("net")
    if net is not None and not isinstance(net, str):
        raise OpError("'net' must be a net name or \"\" to clear it", "ARG_INVALID")
    net = net or ""
    fp = ed.footprint(ref)
    target = None
    have = []
    for pad in S.children(fp, "pad"):
        name = S.arg_text(pad, 1) or ""
        have.append(name)
        if name == number:
            target = pad
    if target is None:
        raise OpError("footprint %r has no pad %r (it has: %s)"
                      % (ref, number, ", ".join(sorted(h for h in have if h))),
                      "PAD_NOT_FOUND")
    before = S.child(target, "net")
    before_name = ""
    if before is not None:
        before_name = S.arg_text(before, 2) or S.arg_text(before, 1) or ""
    for old in list(S.children(target, "net")):
        target.items.remove(old)
    code = None
    if net:
        code = ed.net_code(net, allow_create=_bool(o, "create_nets", False))
        if ed.name_only:
            target.items.append(B.L(B.A("net"), B.A(net, True)))
        else:
            target.items.append(B.L(B.A("net"), B.A(code), B.A(net, True)))
    return {"reference": ref, "pad": number, "before": before_name,
            "after": net, "net_code": code}

def _verify_set_pad_net(ed, o):
    ref = _str(o, "ref")
    number = _str(o, "pad")
    want = o.get("net") or ""
    for pad in S.children(ed.footprint(ref), "pad"):
        if (S.arg_text(pad, 1) or "") != number:
            continue
        net = S.child(pad, "net")
        got = ""
        if net is not None:
            got = S.arg_text(net, 2) or S.arg_text(net, 1) or ""
        return {"ok": got == want, "net": got}
    return {"ok": False, "error": "pad vanished"}

op("set_pad_net", {"copper", "nets"},
   "Assign one pad of a placed footprint to a net, or clear it (net \"\"). This is "
   "the operation a schematic-to-board comparison needs: the netlist says pin 7 is "
   "on SDA, the pad has to say the same, and DRC compares the two.",
   {"ref": "reference designator", "pad": "pad number", "net": "net name (\"\" clears)",
    "create_nets": "bool - allow a net that the board does not declare yet "
                   "(default false)"},
   _run_set_pad_net, _verify_set_pad_net)

# --- drilled pads -----------------------------------------------------
def _pad_widgets(ed, ref, numbers, occurrences=None):
    """The pad nodes of one footprint, in the order asked for.

    Missing pads are named rather than skipped: a plan that says "drill pad 3 of
    P1" and silently drills nothing because P1 has 1..2 is the kind of quiet
    no-op that reads as success.

    Duplicate pad numbers are real - a thermal via star, a shield, a jumper are
    all one number many times (the file even carries
    `duplicate_pad_numbers_are_jumpers`). So a number that matches more than one
    pad is refused unless the caller says which one (`occurrence=3`) or that it
    means all of them (`occurrence="all"`). Drilling "the" pad 1 of a nine-pad
    star without saying which is a guess, and a guess here writes copper.
    """
    fp = ed.footprint(ref)
    have = {}
    for pad in S.children(fp, "pad"):
        n = S.arg_text(pad, 1)
        if n is not None:
            have.setdefault(n, []).append(pad)
    missing = [n for n in numbers if n not in have]
    if missing:
        raise OpError("footprint %r has no pad %s (it has: %s)"
                      % (ref, ", ".join(repr(m) for m in missing),
                         ", ".join(sorted(have))), "PAD_NOT_FOUND")
    occ = occurrences if occurrences is not None else [None] * len(numbers)
    if not isinstance(occ, (list, tuple)):
        occ = [occ] * len(numbers)
    if len(occ) != len(numbers):
        raise OpError("'occurrence' must be one value or one per pad (%d pads, "
                      "%d occurrences)" % (len(numbers), len(occ)), "ARG_INVALID")
    out = []
    for number, which in zip(numbers, occ):
        pads = have[number]
        if which in (None, ""):
            if len(pads) > 1:
                raise OpError(
                    "pad %r of %r appears %d times, so 'which one' is missing: "
                    "pass occurrence=<n> (1-based) or occurrence=\"all\""
                    % (number, ref, len(pads)), "PAD_AMBIGUOUS")
            out.append((number, 1, pads[0]))
            continue
        if isinstance(which, str) and which.strip().lower() == "all":
            out.extend((number, i + 1, pad) for i, pad in enumerate(pads))
            continue
        try:
            k = int(which)
        except (TypeError, ValueError):
            raise OpError("occurrence must be an integer or \"all\" (got %r)"
                          % (which,), "ARG_INVALID")
        if not 1 <= k <= len(pads):
            raise OpError("pad %r of %r has %d occurrence(s), so no %d"
                          % (number, ref, len(pads), k), "PAD_NOT_FOUND")
        out.append((number, k, pads[k - 1]))
    return fp, out

def _pad_type(pad):
    return (S.arg_text(pad, 2) or "").strip()

def _set_pad_seq(pad, head, node):
    """Replace the (head ...) child of a pad, or put it after (size ...)."""
    items = list(pad.items)
    old = S.child(pad, head)
    if old is not None:
        items[items.index(old)] = node
    else:
        size = S.child(pad, "size")
        at = items.index(size) + 1 if size is not None else len(items)
        items.insert(at, node)
    pad.items = items

def _drill_shape(raw):
    """(width, height) of the finished hole in mm, or a loud refusal."""
    if raw is None:
        raise OpError("'drill' is required: the finished hole diameter in mm "
                      "(or [w, h] for an oval slot)", "ARG_MISSING")
    if isinstance(raw, (list, tuple)):
        if len(raw) != 2:
            raise OpError("an oval drill is [width, height] in mm", "ARG_INVALID")
        dw, dh = float(raw[0]), float(raw[1])
    else:
        dw = dh = float(raw)
    if min(dw, dh) <= 0:
        raise OpError("a drill must be > 0 mm (got %r)" % (raw,), "ARG_INVALID")
    return dw, dh

def _fit_drill(ref, number, w, h, dw, dh):
    """Check the hole fits the pad, and say how much copper is left around it.

    Round drill: it has to fit inside the pad with copper left on every side, so
    `d < min(w, h)` - equal is a hole with no ring, which is an NPTH hole.

    Oval drill: a slot's long axis is allowed to cross the pad's narrow axis,
    because that is what a slot is for (a USB-C shell slot in a 1.0 x 2.0 pad).
    What is not allowed is a slot that pokes out of the pad in both directions.
    The ring reported is the one measured across the slot's narrow side, which
    is the only side that has one.
    """
    if abs(dw - dh) < 1e-9:
        if min(w, h) <= dw + 1e-9:
            raise OpError(
                "pad %s.%s is %s x %s mm and the drill is %s x %s mm: there "
                "would be no annular ring left. Grow the pad first "
                "(set_pad_size), ask for a smaller hole, or make it np_thru_hole "
                "(a bare hole has no ring by definition)."
                % (ref, number, B.num(w), B.num(h), B.num(dw), B.num(dh)),
                "DRILL_TOO_LARGE")
        return round((min(w, h) - dw) / 2.0, 4)
    lo_p, hi_p = min(w, h), max(w, h)
    lo_d, hi_d = min(dw, dh), max(dw, dh)
    if hi_d > hi_p + 1e-9 or lo_d >= lo_p - 1e-9:
        raise OpError(
            "pad %s.%s is %s x %s mm and the slot is %s x %s mm: the slot does "
            "not fit inside the pad. Grow the pad first (set_pad_size) or make "
            "it np_thru_hole."
            % (ref, number, B.num(lo_p), B.num(hi_p), B.num(lo_d), B.num(hi_d)),
            "DRILL_TOO_LARGE")
    return round((lo_p - lo_d) / 2.0, 4)

def _run_set_pad_drill(ed, o, seed):
    ref = _str(o, "ref")
    asked = o.get("pad")
    numbers = [asked] if isinstance(asked, str) else list(asked or [])
    if not numbers:
        raise OpError("'pad' is required: which pad(s) of %r get a hole" % ref,
                      "ARG_MISSING")
    numbers = [str(n) for n in numbers]
    kind = (o.get("kind") or "thru_hole")
    if kind not in ("thru_hole", "np_thru_hole"):
        raise OpError("kind must be thru_hole or np_thru_hole (got %r)" % kind,
                      "ARG_INVALID")
    dw, dh = _drill_shape(o.get("drill"))
    paste = _bool(o, "paste", False)
    keep_size = _bool(o, "keep_size", False)
    fp, pads = _pad_widgets(ed, ref, numbers, o.get("occurrence"))
    done = []
    for number, occ, pad in pads:
        size = S.child(pad, "size")
        w = S.arg_num(size, 1, None) if size is not None else None
        h = S.arg_num(size, 2, None) if size is not None else None
        if w is None or h is None:
            raise OpError("pad %s.%s has no (size ...) node, so there is nothing "
                          "to put a hole through" % (ref, number),
                          "PAD_UNSUPPORTED")
        before = _pad_type(pad)
        ring = None
        if kind == "np_thru_hole":
            # NPTH is a bare hole: copper around a mounting hole is a decision,
            # not a default, so the pad is set to the drill size - unless the
            # caller wants the drawn pad shape kept (a slot that is wider than
            # the pad it belongs to).
            if not keep_size:
                w = h = max(dw, dh)
                items = list(pad.items)
                items[items.index(size)] = B.size(w, h)
                pad.items = items
        else:
            ring = _fit_drill(ref, number, w, h, dw, dh)
        # the pad type is argument 2 of the node: `(pad "1" smd rect ...)`
        items = list(pad.items)
        for i, item in enumerate(items):
            if isinstance(item, S.Atom) and item.text in ("smd", "thru_hole",
                                                          "np_thru_hole",
                                                          "connect"):
                items[i] = B.A(kind)
                break
        else:                                       # no type atom at all
            raise OpError("pad %s.%s has no pad-type atom, so the file is not a "
                          "board file this op understands" % (ref, number),
                          "PAD_UNSUPPORTED")
        pad.items = items
        drill_node = (B.L(B.A("drill"), B.A(B.num(dw)))
                      if abs(dw - dh) < 1e-9 else
                      B.L(B.A("drill"), B.A("oval"), B.A(B.num(dw)),
                          B.A(B.num(dh))))
        _set_pad_seq(pad, "drill", drill_node)
        layers = [B.A("*.Cu", True), B.A("*.Mask", True)]
        if paste:
            layers.append(B.A("*.Paste", True))
        _set_pad_seq(pad, "layers", B.L(B.A("layers"), *layers))
        done.append({"pad": number, "occurrence": occ, "before": before,
                     "type": kind, "size": [w, h],
                     "drill": [dw, dh] if dw != dh else dw,
                     "annular_ring_mm": ring})
    if _bool(o, "footprint_attr", True) and kind == "thru_hole":
        attr = S.child(fp, "attr")
        words = [S.arg_text(attr, i) for i in range(1, 8)] if attr is not None else []
        words = [x for x in words if x]
        if "through_hole" not in words:
            words = [x for x in words if x != "smd"] + ["through_hole"]
            node = B.L(B.A("attr"), *[B.A(x) for x in words])
            items = list(fp.items)
            if attr is not None:
                items[items.index(attr)] = node
            else:
                items.insert(1, node)
            fp.items = items
    return {"reference": ref, "pads": done,
            "why_drilled": ("a drilled pad is on every copper layer at once - "
                            "it is the only pad type that can join a track on "
                            "one side to a track or pour on the other")}

def _verify_set_pad_drill(ed, o):
    ref = _str(o, "ref")
    asked = o.get("pad")
    numbers = [asked] if isinstance(asked, str) else list(asked or [])
    numbers = [str(n) for n in numbers]
    kind = (o.get("kind") or "thru_hole")
    dw, dh = _drill_shape(o.get("drill"))
    paste = _bool(o, "paste", False)
    _fp, pads = _pad_widgets(ed, ref, numbers, o.get("occurrence"))
    rows = []
    ok = True
    for number, occ, pad in pads:
        drill = S.child(pad, "drill")
        got = None
        if drill is not None:
            if (S.arg_text(drill, 1) or "") == "oval":
                got = (S.arg_num(drill, 2, None), S.arg_num(drill, 3, None))
            else:
                got = (S.arg_num(drill, 1, None),)
        layers = S.child(pad, "layers")
        have = [S.arg_text(layers, i) for i in range(1, 5)] if layers else []
        have = [x for x in have if x]
        want = ["*.Cu", "*.Mask"] + (["*.Paste"] if paste else [])
        # oval reads back as (w, h); round as (d,) - compare like with like
        same_drill = (got == (dw, dh) if abs(dw - dh) > 1e-9
                      else got == (dw,))
        good = (_pad_type(pad) == kind and same_drill and have == want)
        ok = ok and good
        rows.append({"pad": number, "occurrence": occ, "type": _pad_type(pad),
                     "drill": got, "layers": have, "ok": good})
    attr = S.child(_fp, "attr")
    words = [S.arg_text(attr, i) for i in range(1, 8)] if attr is not None else []
    return {"ok": ok, "reference": ref, "pads": rows,
            "footprint_attr": [x for x in words if x]}

op("set_pad_drill", {"copper", "mech"},
   "Turn pad(s) of a placed footprint into drilled pads: say how big the finished "
   "hole is (a number for a round drill, [w, h] for an oval slot) and the pad "
   "goes on every copper layer at once with a mask opening on both sides, which "
   "is the only way a pad can join copper on the far side. This is what a "
   "through-hole part needs and what an SMD pad cannot do: an SMD pad that was "
   "meant to be a header pin looks right in the editor and leaves the bottom "
   "side unreachable. The hole must fit the pad: a round drill needs copper "
   "left all round it, an oval slot may cross the pad's narrow axis but not "
   "poke out of both, and a pad with no ring at all is refused unless it is "
   "asked for as np_thru_hole. np_thru_hole is a bare hole (mounting), so the "
   "pad is set to the drill size - copper around a mounting hole is a decision, "
   "not a default.",
   {"ref": "reference designator", "pad": "pad number, or a list of them",
    "drill": "mm, or [w, h] for an oval slot",
    "kind": "thru_hole (default) | np_thru_hole",
    "occurrence": "when one pad number appears more than once (thermal via "
                  "star, shield, jumper): the 1-based one, or \"all\"; one "
                  "value or one per pad (default: only allowed if unique)",
    "paste": "bool - keep a paste opening on the drilled pad (default false)",
    "keep_size": "bool - np_thru_hole only: keep the drawn pad size instead of "
                 "shrinking it to the hole (a slot wider than its pad)",
    "footprint_attr": "bool - also mark the footprint through_hole (default true)"},
   _run_set_pad_drill, _verify_set_pad_drill)

# --- via in pad -------------------------------------------------------
def _pad_abs(ed, ref, number):
    """Absolute position of one pad in the FILE frame, plus its net name.

    A pad's `(at dx dy [angle])` is stored relative to the footprint origin and
    turned by the footprint's own angle. The board file grows *down*, so the turn
    is done in the Y-up frame - exactly the arithmetic ``adapters.kicad_pcb`` uses
    to place pads for the clearance oracle, which was checked pad-by-pad against
    pcbnew (2118 back-side pads included). Doing it directly in file coordinates
    puts every pad of a rotated footprint on the wrong side: U1 of the test board
    is at 30 deg and its pads came out 1.8 mm from where pcbnew reports them
    (`tests/test_ops_edit.py:ViaInPadAgainstPcbnew`).
    """
    import math
    fp = ed.footprint(ref)
    at = S.child(fp, "at")
    fx = S.arg_num(at, 1, 0.0) or 0.0
    fy = S.arg_num(at, 2, 0.0) or 0.0
    fa = S.arg_num(at, 3) or 0.0
    have = []
    for pad in S.children(fp, "pad"):
        name = S.arg_text(pad, 1) or ""
        have.append(name)
        if name != number:
            continue
        pat = S.child(pad, "at")
        px = (S.arg_num(pat, 1, 0.0) or 0.0) if pat is not None else 0.0
        py = (S.arg_num(pat, 2, 0.0) or 0.0) if pat is not None else 0.0
        # into the Y-up frame (y of the file points down)
        cx, cy = fx, -fy
        rx, ry = px, -py
        ca, sa = math.cos(math.radians(fa)), math.sin(math.radians(fa))
        x = cx + rx * ca - ry * sa
        y = -1.0 * (cy + rx * sa + ry * ca)
        net = S.child(pad, "net")
        net_name = ""
        if net is not None:
            net_name = S.arg_text(net, 2) or S.arg_text(net, 1) or ""
        pad_type = (S.arg_text(pad, 2) or "").lower()
        size = S.child(pad, "size")
        w = S.arg_num(size, 1) if size is not None else None
        h = S.arg_num(size, 2) if size is not None else None
        return {"node": pad, "x": x, "y": y, "net": net_name, "type": pad_type,
                "w": w, "h": h}
    raise OpError("footprint %r has no pad %r (it has: %s)"
                  % (ref, number, ", ".join(sorted(h for h in have if h))),
                  "PAD_NOT_FOUND")

def _copper_stack(ed):
    """Copper layer names in stack order, as declared in `(layers ...)`."""
    out = []
    node = S.child(ed.board, "layers")
    if node is None:
        return []
    for item in node.items[1:]:
        if not isinstance(item, S.Seq) or len(item.items) < 3:
            continue
        name = item.items[1].text if isinstance(item.items[1], S.Atom) else ""
        kind = item.items[2].text if isinstance(item.items[2], S.Atom) else ""
        if name.endswith(".Cu") and kind in ("signal", "power", "mixed", "jumper"):
            out.append(name)
    return out

def _run_add_via_in_pad(ed, o, seed):
    ref = _str(o, "ref")
    number = _str(o, "pad")
    info = _pad_abs(ed, ref, number)
    net = o.get("net")
    if net is not None and not isinstance(net, str):
        raise OpError("'net' must be a net name", "ARG_INVALID")
    if net:
        net = net.strip()
    else:
        net = info["net"]
    if not net:
        raise OpError(
            "pad %s.%s has no net and no 'net' was given - a via in a pad is "
            "electrically the pad's net, so say which net to bore through it"
            % (ref, number), "PAD_HAS_NO_NET")
    code = ed.net_code(net, allow_create=_bool(o, "create_net", False))
    cls = _default_netclass(ed) or {}
    dia = _num(o, "size", required=False, default=cls.get("via_dia") or 0.6)
    drill = _num(o, "drill", required=False, default=cls.get("via_drill") or 0.3)
    if drill >= dia:
        raise OpError("drill (%r) must be smaller than the via diameter (%r)"
                      % (drill, dia), "ARG_INVALID")
    if drill <= 0 or dia <= 0:
        raise OpError("via size and drill must both be > 0", "ARG_INVALID")
    layers = o.get("layers") or None
    stack = _copper_stack(ed)
    if layers:
        if not isinstance(layers, (list, tuple)) or len(layers) != 2:
            raise OpError("'layers' must be [first, last] copper layers",
                          "ARG_INVALID")
        layers = [str(x) for x in layers]
        for l in layers:
            ed.require_layer(l)
        if stack:
            try:
                i0, i1 = stack.index(layers[0]), stack.index(layers[1])
            except ValueError:
                raise OpError("layers %r are not both in the copper stack (%s)"
                              % (layers, ", ".join(stack)), "LAYER_NOT_IN_COPPER")
            if i0 > i1:
                layers = [layers[1], layers[0]]
                i0, i1 = i1, i0
            span = stack[i0:i1 + 1]
        else:
            span = list(layers)
    else:
        span = stack or ["F.Cu", "B.Cu"]
        layers = [span[0], span[-1]]
    # A via in a pad is a drilled hole through copper that is already there: the
    # collision model that governs ordinary vias would reject every one of them,
    # so it is deliberately not consulted. What IS checked is that the point is
    # free of *other* vias, because two drills in one pad is a fabrication error
    # that looks fine in the file.
    x_file, y_file_ = info["x"], info["y"]
    y_up = -y_file_
    reach = dia / 2.0
    for node in ed.items("via"):
        vat = S.child(node, "at")
        if vat is None:
            continue
        vx = S.arg_num(vat, 1, 0.0) or 0.0
        vy = S.arg_num(vat, 2, 0.0) or 0.0
        if abs(vx - x_file) < reach and abs(vy - y_file_) < reach:
            raise OpError(
                "there is already a via at (%s, %s) mm, %.3f mm from the pad "
                "centre - two drills in one pad is not a via in pad"
                % (B.num(vx), B.num(-vy),
                   ((vx - x_file) ** 2 + (vy - y_file_) ** 2) ** 0.5),
                "VIA_EXISTS")
    ed.append(B.via(x_file, y_up, dia, drill, code, ed.sp, B.new_id(seed),
                    layers=[span[0], span[-1]]))
    return {"reference": ref, "pad": number, "net": net, "x": round(x_file, 4),
            "y": round(y_up, 4), "size": dia, "drill": drill,
            "layers": [span[0], span[-1]], "span": span,
            "pad_type": info["type"],
            "pad_size": [info["w"], info["h"]],
            "manufacturing": (
                "a via inside a pad must be filled and plated over (IPC-4761 "
                "type VII) or capped, otherwise solder wicks down the barrel "
                "during reflow; this op writes the drill, the filling is a "
                "fabrication note, not something the board file carries"),
            "drc_note": ("KiCad's DRC flags a hole this close to the pad edge as "
                         "'via hole too close to pad' unless the pad is marked as "
                         "a via-in-pad pad; the geometry here is what the "
                         "manufacturer needs, the check is the caller's call")}

def _verify_add_via_in_pad(ed, o):
    ref = _str(o, "ref")
    number = _str(o, "pad")
    info = _pad_abs(ed, ref, number)
    net = (o.get("net") or "").strip() or info["net"]
    code = ed.net_by_name.get(net)
    best = None
    for node in ed.items("via"):
        vat = S.child(node, "at")
        if vat is None:
            continue
        vx = S.arg_num(vat, 1, 0.0) or 0.0
        vy = S.arg_num(vat, 2, 0.0) or 0.0
        d = ((vx - info["x"]) ** 2 + (vy - info["y"]) ** 2) ** 0.5
        if d > 1e-3:
            continue
        got_net = S.arg_text(S.child(node, "net"), 1)
        if got_net in (code, net):
            return {"ok": True, "x": round(vx, 4), "offset_from_pad": round(d, 4)}
        best = {"x": round(vx, 4), "net": got_net}
    return {"ok": False, "note": "no via of that net at the pad centre",
            "nearest_same_point": best, "want_net": net}

op("add_via_in_pad", {"copper"},
   "Bore a via through the centre of a placed pad (via-in-pad / via-in-pad for a "
   "BGA escape, a thermal pad, or a decoupling cap whose ground must reach an "
   "inner plane). The pad centre is computed from the footprint's placement and "
   "angle, and the via takes the pad's net unless one is given. The hole is "
   "written; filling it is a fabrication note, not a file fact.",
   {"ref": "reference designator", "pad": "pad number", "size": "mm (default: net class)",
    "drill": "mm (default: net class)", "net": "net name (default: the pad's net)",
    "layers": "[first, last] copper layers (default: the whole stack)",
    "create_net": "bool - allow a net the board does not declare yet"},
   _run_add_via_in_pad, _verify_add_via_in_pad)

# --- teardrops --------------------------------------------------------
def _filled_poly(points, layer, net_code, net_name, name_only, sp, uid):
    """A filled polygon on a copper layer carrying a net - the shape KiCad's own
    teardrop generator makes (`PCB_SHAPE` with POLY + fill, net set). Verified by
    loading the written board in pcbnew: the drawing comes back on F.Cu with the
    net name, and DRC treats it as copper of that net."""
    net = (B.L(B.A("net"), B.A(net_name, True)) if name_only
           else B.L(B.A("net"), B.A(str(net_code))))
    return B.L(B.A("gr_poly"), B.pts(points),
               B.L(B.A("layer"), B.A(layer, True)),
               B.L(B.A("width"), B.A("0")),
               B.L(B.A("fill"), B.A("yes")),
               net, sp.id_node(uid))

def _pad_radius(info):
    """Half the pad's smaller extent: a circle of this radius stays INSIDE the pad,
    so a teardrop built on it never adds copper outside the pad's own outline."""
    w = info["w"] or 0.0
    h = info["h"] or 0.0
    if w <= 0 or h <= 0:
        raise OpError("pad %s.%s has no usable size in the file (%r x %r)"
                      % (info["ref"], info["number"], w, h), "PAD_UNSUPPORTED")
    return min(w, h) / 2.0

def _teardrop_outline(P, u, r, w, L, curve, samples=16):
    """Outline of one teardrop, in board (Y-up) coordinates.

    The copper boundary is the pad circle, then a concave fillet out to the track
    edge. The fillet is the circle that touches the track edge line at the
    shoulder ``S = P + L*u + b*n`` (``b = w/2``) and touches the pad circle from
    outside, so its centre sits ``R`` further out along ``n`` and its distance to
    the pad centre is ``r + R``:

        ``L^2 + (b + R)^2 = (r + R)^2  =>  R = (L^2 + b^2 - r^2) / (2 (r - b))``

    ``r > b`` is required - a track as wide as the pad radius has a straight
    boundary where the fillet should be. Getting the sign of ``n`` wrong puts the
    arc on the inside of the copper, where it changes nothing (that was the first
    version of this function: it produced a 35-vertex polygon with every vertex
    within the pad+track union, which looks plausible and adds no copper at all).

    ``curve=False`` joins the same two tangent points with chords: the shape a
    hand-drawn teardrop has, and what a fab house cuts identically at 0.1 mm.
    """
    import math
    ux, uy = u
    nx, ny = -uy, ux
    b = w / 2.0
    def at(a, c):
        return (P[0] + ux * a + nx * c, P[1] + uy * a + ny * c)
    S1 = at(L, b)
    S2 = at(L, -b)
    T1 = at(0.0, r)
    T2 = at(0.0, -r)
    if not curve:
        return [T1, S1, S2, T2]
    if r <= b:
        raise OpError(
            "the pad's inner radius (%r mm) is not larger than half the track "
            "width (%r mm), so there is no fillet to draw between them - pass "
            "curve=false for straight chords, or a narrower track_width"
            % (r, b), "ARG_INVALID")
    num = L * L + b * b - r * r
    if num <= 1e-9:
        raise OpError(
            "a teardrop of length %r mm reaches no further than the pad's own "
            "radius %r mm, so the fillet would start behind the pad edge; use a "
            "length > %.3f mm" % (L, r, math.sqrt(max(r * r - b * b, 0.0))),
            "ARG_INVALID")
    R = num / (2.0 * (r - b))
    out = [T1]
    for sign in (+1.0, -1.0):
        S = S1 if sign > 0 else S2
        n = (nx * sign, ny * sign)
        C = (S[0] + R * n[0], S[1] + R * n[1])
        dl = math.hypot(C[0] - P[0], C[1] - P[1])
        T = (P[0] + (C[0] - P[0]) * r / dl, P[1] + (C[1] - P[1]) * r / dl)
        a0 = math.atan2(T[1] - C[1], T[0] - C[0])
        a1 = math.atan2(S[1] - C[1], S[0] - C[0])
        da = (a1 - a0 + math.pi) % (2 * math.pi) - math.pi
        pts = []
        for i in range(samples + 1):
            a = a0 + da * i / float(samples)
            pts.append((C[0] + R * math.cos(a), C[1] + R * math.sin(a)))
        if sign > 0:
            out.extend(pts)
        else:
            out.extend(reversed(pts))
    return out

def _track_directions(ed, info, layer, r_big):
    """Where copper leaves this pad: unit directions from the pad centre, deduped."""
    import math
    P = (info["x"], info["y"])
    code = ed.net_by_name.get(info["net"])
    out = []
    for kind in ("segment", "arc"):
        for node in ed.items(kind):
            if S.arg_text(S.child(node, "layer"), 1) != layer:
                continue
            if code is not None and S.arg_text(S.child(node, "net"), 1) != code:
                continue
            ends = [S.child(node, "start"), S.child(node, "end")]
            pts = [(S.arg_num(e, 1, 0.0) or 0.0, S.arg_num(e, 2, 0.0) or 0.0)
                   for e in ends if e is not None]
            if len(pts) != 2:
                continue
            (ax, ay), (bx, by) = pts
            for near, far in ((( ax, ay), (bx, by)), ((bx, by), (ax, ay))):
                if math.hypot(near[0] - P[0], near[1] - P[1]) > r_big:
                    continue
                dx, dy = far[0] - near[0], far[1] - near[1]
                d = math.hypot(dx, dy)
                if d < 1e-9:
                    continue
                u = (dx / d, -dy / d)      # into the Y-up frame
                for have, _hw in out:
                    if have[0] * u[0] + have[1] * u[1] > 0.996:
                        break
                else:
                    out.append((u, S.arg_num(S.child(node, "width"), 1, 0.0) or 0.0))
    return out

def _run_add_teardrop(ed, o, seed):
    import math
    ref = _str(o, "ref")
    number = _str(o, "pad")
    info = _pad_abs(ed, ref, number)
    info["ref"] = ref
    info["number"] = number
    if not info["net"]:
        raise OpError("pad %s.%s has no net - a teardrop is copper of the pad's "
                      "net and has nowhere to belong" % (ref, number),
                      "PAD_HAS_NO_NET")
    code = ed.net_code(info["net"])
    r = _pad_radius(info)
    layer = o.get("layer") or (info["node"] and
                               _pad_layer(info["node"])) or "F.Cu"
    ed.require_layer(layer)
    dirs = _track_directions(ed, info, layer, r * math.sqrt(2.0) + 1e-6)
    if not dirs and o.get("dx") is not None:
        dx = _num(o, "dx")
        dy = _num(o, "dy", required=False, default=0.0)
        d = math.hypot(dx, dy)
        if d < 1e-9:
            raise OpError("'dx'/'dy' must not both be zero", "ARG_INVALID")
        dirs = [((dx / d, dy / d), 0.0)]
    if not dirs:
        raise OpError(
            "no track of net %r leaves pad %s.%s on %s, so there is no corner to "
            "tear; route the pad first, or give 'dx'/'dy' to state the direction "
            "yourself" % (info["net"], ref, number, layer), "NO_TRACK")
    if not _bool(o, "all", False) and len(dirs) > 1:
        dirs = dirs[:1]
    W = _num(o, "track_width", required=False, default=None)
    added = []
    for u, tw in dirs:
        w = W or tw or (_default_netclass(ed) or {}).get("trace_width") or 0.25
        if w <= 0:
            raise OpError("track width must be > 0", "ARG_INVALID")
        L = _num(o, "length", required=False,
                 default=1.5 * max(info["w"] or 0.0, info["h"] or 0.0))
        if L <= w / 2.0:
            raise OpError("a teardrop of length %r mm is shorter than half the "
                          "track width (%r mm) - it would be hidden inside the "
                          "track" % (L, w), "ARG_INVALID")
        pts = _teardrop_outline((info["x"], -info["y"]), u, r, w, L,
                                _bool(o, "curve", True))
        ed.append(_filled_poly(pts, layer, code, info["net"], ed.name_only,
                               ed.sp, B.new_id("%s:%d" % (seed, len(added)))))
        added.append({"direction": [round(u[0], 6), round(u[1], 6)],
                      "track_width": w, "length": round(L, 4),
                      "vertices": len(pts), "layer": layer})
    return {"reference": ref, "pad": number, "net": info["net"],
            "pad_radius": round(r, 4), "teardrops": added,
            "count": len(added),
            "note": ("filled copper polygon of the pad's net, which is the shape "
                     "KiCad's own teardrop generator writes - it is copper, not an "
                     "annotation, so DRC measures it like any other copper")}

def _pad_layer(node):
    """First copper layer a pad is on."""
    ln = S.child(node, "layers")
    if ln is None:
        return None
    for item in ln.items[1:]:
        if isinstance(item, S.Atom) and item.text.endswith(".Cu"):
            return item.text
    return None

def _verify_add_teardrop(ed, o):
    import math
    ref = _str(o, "ref")
    number = _str(o, "pad")
    info = _pad_abs(ed, ref, number)
    code = ed.net_by_name.get(info["net"])
    layer = o.get("layer") or (_pad_layer(info["node"]) if info["node"] else None)
    want_net = info["net"]
    polys = []
    for node in ed.items("gr_poly"):
        if S.arg_text(S.child(node, "layer"), 1) != layer:
            continue
        net = S.child(node, "net")
        got = ""
        if net is not None:
            got = S.arg_text(net, 2) or S.arg_text(net, 1) or ""
        if got not in (code, want_net):
            continue
        pts_node = S.child(node, "pts")
        if pts_node is None:
            continue
        pts = []
        for item in pts_node.items[1:]:
            if isinstance(item, S.Seq):
                pts.append((S.arg_num(item, 1, 0.0) or 0.0,
                            -(S.arg_num(item, 2, 0.0) or 0.0)))
        if len(pts) >= 3:
            polys.append(pts)
    P = (info["x"], -info["y"])
    r = _pad_radius(info)
    # "anchored on the pad" = some vertex lies on the pad's own circle (the fillet
    # has to start at the pad edge, not float next to it), and "reaches out" = the
    # polygon spans further than the pad radius.
    best = None
    for poly in polys:
        dists = [math.hypot(x - P[0], y - P[1]) for x, y in poly]
        on_circle = min(abs(d - r) for d in dists)
        reach = max(dists)
        if best is None or on_circle < best[0]:
            best = (on_circle, reach, len(poly))
    if best is None:
        return {"ok": False, "filled_polys_of_that_net": 0}
    on_circle, reach, npts = best
    ok = on_circle < 0.02 and reach > r * 1.05
    return {"ok": ok, "filled_polys_of_that_net": len(polys),
            "vertex_off_pad_circle_mm": round(on_circle, 6),
            "reach_from_pad_centre_mm": round(reach, 4),
            "vertices": npts}

op("add_teardrop", {"copper"},
   "Tear the corner where a track leaves a pad: a filled polygon of the pad's net "
   "that widens from the pad's edge into the track. It is what stops a thin track "
   "from peeling off a pad after thermal cycling, and what a drill breakout needs. "
   "The shape is the one KiCad's own teardrop generator writes (filled polygon with "
   "the net set), so DRC measures it as copper.",
   {"ref": "reference designator", "pad": "pad number",
    "layer": "copper layer (default: the pad's first copper layer)",
    "track_width": "mm (default: the connected track's width)",
    "length": "mm from the pad centre (default: 1.5 x the pad's larger side)",
    "curve": "bool - curved fillet (default true) or straight chords",
    "all": "bool - tear every track that leaves the pad (default false: the first)",
    "dx": "mm - direction to tear when no track is routed yet",
    "dy": "mm"},
   _run_add_teardrop, _verify_add_teardrop)

# ----------------------------------------------------------------- driver
# Keys that read as if they were parameters but are not. A misspelled key is
# not an error anywhere in the op runner: the getter simply reports "not given"
# and the op falls back to its default. A via written with `diameter` instead of
# `size` therefore came out at the net-class 0.6/0.3 default - three times the
# copper asked for - and passed every check on the way (the clearance judge then
# saw 0.6 mm vias where 0.2 mm were meant, and 94 pairs overlap that never
# should have existed). Refuse them by name instead of guessing.
_PARAM_ALIASES = {
    "diameter": "size",
    "dia": "size",
    "via_diameter": "size",
    "via_size": "size",
    "pad_diameter": "size",
    "hole": "drill",
}


def validate(op):
    """Static validation: name, shape. Board-dependent checks happen in `run`."""
    if not isinstance(op, dict):
        raise OpError("each operation must be an object, got %r" % (op,), "ARG_INVALID")
    name = op.get("op")
    if not name or name not in OPS:
        raise OpError("unknown op %r (known: %s)"
                      % (name, ", ".join(sorted(OPS))), "OP_UNKNOWN")
    for bad, good in _PARAM_ALIASES.items():
        if bad in op:
            raise OpError(
                "%r is not a parameter of %r - write %r. A key the op does not "
                "know is silently ignored, and the op then falls back to its "
                "default (for a via that is the net class, 0.6/0.3 mm), which "
                "produces copper nobody asked for and no check refuses"
                % (bad, name, good), "ARG_ALIAS")
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

def verify_all(doc, ops, records=None):
    """Re-read postconditions on a document loaded from the written file.

    `records` is what `run_all` returned. A postcondition that has to know WHICH
    objects the operation touched needs it: "delete every zone with net GND on
    In1.Cu" cannot be re-checked from the final tree alone when the same plan
    also adds one back, and that is a real use (replace a pour, not grow one).
    Verifiers that take only (ed, op) are unaffected.
    """
    ed = Editor(doc)
    out = []
    ok = True
    for idx, op in enumerate(ops):
        name = validate(op)
        record = None
        if records and idx < len(records):
            record = records[idx]
        try:
            res = _call_verify(OPS[name]["verify"], ed, op, record) or {"ok": False}
        except OpError as exc:
            res = {"ok": False, "error": "%s: %s" % (exc.code, exc)}
        except Exception as exc:  # noqa: BLE001
            res = {"ok": False, "error": "%s: %s" % (type(exc).__name__, exc)}
        if not res.get("ok"):
            ok = False
        out.append({"index": idx, "op": name, "verified": bool(res.get("ok")),
                    "detail": res})
    return {"all_verified": ok, "operations": out}

def _call_verify(fn, ed, op, record):
    """Call a verifier with the record when it asks for one."""
    try:
        wants = len(_inspect.signature(fn).parameters)
    except (TypeError, ValueError):
        wants = 2
    return fn(ed, op, record) if wants >= 3 else fn(ed, op)

def affects(ops):
    out = set()
    for op in ops:
        out |= set(OPS[validate(op)]["affects"])
    return sorted(out)

# ------------------------------------------------------- regions and clusters
# Three things the write path could not say before this section existed, and all
# three came out of the same read of RK3506 (`docs/173` §5): five of the twelve
# defects on that board had no operation that could express their answer.
#
# * every generative op took a 1-D object - a point, a segment, a via, a pad.
#   Four of the five remaining defects are "make room here", and the only way to
#   fix them is to move a GROUP of parts plus the copper that belongs to it in
#   one rigid step. `move_cluster` is that step. It refuses unless nothing is
#   orphaned, because a footprint that moves while its tracks stay behind is a
#   net that silently disconnects - and that failure is invisible in the file.
# * `add_keepout` can only forbid and `add_zone` can only add copper. Neither
#   says "this band is being held for these nets", which is what routing a
#   length-matched group BEFORE routing anything else needs. `reserve_region`
#   writes a NAMED rule area and reports how much foreign copper already sits
#   inside it; `release_region` takes it back.
# * `set_pad_net` changes one pad, so swapping two nets takes two operations and
#   any board read between them has both pads on the same net. `swap_pad_nets`
#   rotates the assignment around a ring in one step: on a BGA whose ball order
#   is wrong, the ring is the whole fix and the intermediate state is a short.

def _pts_bbox(pts):
    xs = [p[0] for p in pts]
    ys = [p[1] for p in pts]
    return (min(xs), min(ys), max(xs), max(ys))

def _pt_in_poly(x, y, poly):
    """Ray casting. Points exactly on an edge are not tested: a rule area's
    boundary is not a place copper is either inside or outside of, and pretending
    otherwise would turn a 1e-9 rounding error into a refusal."""
    inside = False
    n = len(poly)
    j = n - 1
    for i in range(n):
        xi, yi = poly[i]
        xj, yj = poly[j]
        if (yi > y) != (yj > y):
            xc = (xj - xi) * (y - yi) / (yj - yi) + xi
            if x < xc:
                inside = not inside
        j = i
    return inside

def _sub_xy(node, sub):
    """`(sub x y)` in the view frame (mm, Y up), or None when the node has none."""
    n = S.child(node, sub)
    if n is None or len(n.items) < 3:
        return None
    try:
        return (S.arg_num(n, 1), -S.arg_num(n, 2))
    except (TypeError, ValueError):
        return None

def _set_sub_xy(node, sub, x, y_up):
    n = S.child(node, sub)
    if n is None or len(n.items) < 3:
        return False
    n.items[1].text = B.num(x)
    n.items[2].text = B.num(-float(y_up))
    return True

_ITEM_POINTS = ("start", "mid", "end", "at")

def _item_points(node):
    out = []
    for sub in _ITEM_POINTS:
        p = _sub_xy(node, sub)
        if p is not None:
            out.append(p)
    return out

def _shift_item(node, fn):
    moved = 0
    for sub in _ITEM_POINTS:
        p = _sub_xy(node, sub)
        if p is None:
            continue
        x, y = fn(p)
        if _set_sub_xy(node, sub, x, y):
            moved += 1
    return moved

def _item_layers(node):
    ls = S.child(node, "layers")
    if ls is not None:
        return [S.arg_text(ls, i) for i in range(1, len(ls.items))
                if S.arg_text(ls, i)]
    one = S.arg_text(S.child(node, "layer"), 1)
    return [one] if one else []

def _item_net(node):
    n = S.child(node, "net")
    if n is None:
        return ""
    return S.arg_text(n, 2) or S.arg_text(n, 1) or ""

def _net_name(ed, node):
    """The net a copper item declares, by NAME.

    Tracks, vias and zones written by this server carry `(net <code>)` only - the
    name lives in the board's `(net <code> "name")` table, which is where KiCad
    keeps it - so reporting the raw token tells a user "net 1" when the answer
    they asked for is "SIG". Measured need: the orphan refusal named `(1)` before
    this, and the reservation's foreign list did the same. A name-only board has
    no table and its references already are names.
    """
    raw = _item_net(node)
    if not raw:
        return ""
    return ed.name_by_code.get(raw, raw)

def _fp_origin(node):
    at = S.child(node, "at")
    if at is None:
        return (0.0, 0.0, 0.0)
    return (S.arg_num(at, 1, 0.0), -S.arg_num(at, 2, 0.0),
            S.arg_num(at, 3, 0.0) or 0.0)

def _set_fp_origin(ed, node, x, y_up, rot):
    """Write `(at x y rot)` the way `move_footprint` does, pads' angles included."""
    at = S.child(node, "at")
    old = _fp_origin(node)
    want_rot = rot
    if at is not None and len(at.items) < 4 and abs(float(rot or 0.0)) < 1e-12:
        want_rot = None          # a footprint that never had an angle keeps none
    new = B.at(x, y_up, want_rot)
    if at is not None:
        for i, item in enumerate(node.items):
            if item is at:
                node.items[i] = new
                break
    else:
        S.insert_child(node, 1, new)
    delta = float(want_rot or 0.0) - float(old[2] or 0.0)
    pads = _rotate_pads(node, delta) if abs(delta) > 1e-9 else 0
    return pads

def _fp_pads_abs(node):
    """(x, y, net, pad number) for every pad, in the VIEW frame.

    The measured convention, not a guess: pad `(at x y)` is relative to the
    footprint in the FILE frame (Y down), and the footprint's angle turns it
    **counter-clockwise on screen**, which in the file frame is clockwise.
    Measured on `_corpus/multichannel.kicad_pcb` J5 (ori 90, pad 1 rel (0, -6.2),
    fp at (191.285, 87.385), pcbnew reports the pad at (185.085, 87.385)):
    rotating the view-frame offset (0, +6.2) by +90 gives (-6.2, 0). The other
    sign lands the pad on the wrong side by 12.4 mm.
    """
    ox, oy, rot = _fp_origin(node)
    th = math.radians(float(rot or 0.0))
    co, si = math.cos(th), math.sin(th)
    out = []
    for pad in S.children(node, "pad"):
        at = S.child(pad, "at")
        if at is None:
            continue
        rx = S.arg_num(at, 1, 0.0) or 0.0
        ry = -(S.arg_num(at, 2, 0.0) or 0.0)
        out.append((ox + rx * co - ry * si, oy + rx * si + ry * co,
                    _item_net(pad), S.arg_text(pad, 1) or ""))
    return out

def _rot_about(p, c, deg):
    th = math.radians(float(deg))
    co, si = math.cos(th), math.sin(th)
    dx, dy = p[0] - c[0], p[1] - c[1]
    return (c[0] + dx * co - dy * si, c[1] + dx * si + dy * co)

def _run_move_cluster(ed, o, seed):
    refs = o.get("members") or o.get("refs")
    if not isinstance(refs, (list, tuple)) or not refs:
        raise OpError("move_cluster needs 'members': a list of reference "
                      "designators to move as one rigid body", "ARG_MISSING")
    dx = _num(o, "dx", required=False, default=0.0)
    dy = _num(o, "dy", required=False, default=0.0)
    rot = _num(o, "rot", required=False, default=0.0)
    grow = _num(o, "grow_mm", required=False, default=0.0)
    with_copper = _bool(o, "with_copper", True)
    allow_orphan = _bool(o, "allow_orphan", False)
    if abs(dx) < 1e-12 and abs(dy) < 1e-12 and abs(rot) < 1e-12:
        raise OpError("move_cluster would not move anything (dx, dy and rot are "
                      "all zero)", "ARG_INVALID")

    nodes = []
    missing = []
    for ref in refs:
        try:
            nodes.append((ref, ed.footprint(ref)))
        except OpError:
            missing.append(ref)
    if missing:
        raise OpError("no footprint with reference(s) %s on this board"
                      % ", ".join(missing), "REFERENCE_NOT_FOUND")

    origins = [(ref, node) + _fp_origin(node) for ref, node in nodes]
    cx = sum(e[2] for e in origins) / float(len(origins))
    cy = sum(e[3] for e in origins) / float(len(origins))
    centre = (cx, cy)

    pts = [(e[2], e[3]) for e in origins]
    pads_of = {}
    for ref, node in nodes:
        pads = _fp_pads_abs(node)
        pads_of[ref] = pads
        pts.extend([(p[0], p[1]) for p in pads])
    x0, y0, x1, y1 = _pts_bbox(pts)
    box = (x0 - grow, y0 - grow, x1 + grow, y1 + grow)

    def inside(p):
        return (box[0] - 1e-9 <= p[0] <= box[2] + 1e-9
                and box[1] - 1e-9 <= p[1] <= box[3] + 1e-9)
    box_area = max(0.0, box[2] - box[0]) * max(0.0, box[3] - box[1])
    box_perimeter = 2.0 * ((box[2] - box[0]) + (box[3] - box[1]))
    # A bbox that has collapsed to a line is not a cluster; it would sweep every
    # track on the board into the move set.
    if box_area < 1e-6 or box_perimeter < 1e-6:
        raise OpError("the %d member(s) have a degenerate bounding box (%0.4f mm "
                      "area) - name the parts that actually make up the cluster"
                      % (len(nodes), box_area), "ARG_INVALID")

    # Which copper belongs to the cluster: anything with a point inside the box,
    # PLUS anything that lands on a pad of a moved part. The second half is what
    # keeps a bbox that happens to sit over an unrelated track from dragging it.
    pad_tol = 0.05
    pad_pts = [p for pads in pads_of.values() for p in pads]
    copper = []
    for kind in ("segment", "arc", "via"):
        for node in ed.items(kind):
            pts_i = _item_points(node)
            if not pts_i:
                continue
            if not any(inside(p) for p in pts_i):
                continue
            on_pad = any(abs(p[0] - q[0]) <= pad_tol and abs(p[1] - q[1]) <= pad_tol
                         for p in pts_i for q in pad_pts)
            copper.append({"node": node, "points": pts_i, "on_pad": on_pad,
                           "net": _net_name(ed, node), "layers": _item_layers(node),
                           "kind": kind, "whole": all(inside(p) for p in pts_i)})

    move_set = [c for c in copper if c["whole"]]
    orphan = [c for c in copper if c["on_pad"] and not c["whole"]]
    if not with_copper:
        orphan = [c for c in copper if c["on_pad"]]
        move_set = []
    orphan_nets = sorted({c["net"] for c in orphan if c["net"]})
    if orphan and not allow_orphan:
        raise OpError(
            "%d copper item(s) land on a moved pad but reach outside the cluster, "
            "so moving the parts would leave them behind (%s)%s. Widen the cluster "
            "(more members, or grow_mm) so they ride along, or pass "
            "allow_orphan=true and re-route them."
            % (len(orphan), ", ".join(orphan_nets[:6]) or "no net",
               "" if len(orphan_nets) <= 6 else " and %d more" % (len(orphan_nets) - 6)),
            "ORPHAN_COPPER",
            hint="the measured cost on RK3506 is that every one of these is a net "
                 "that opens, and board_drc's unconnected_total is the only thing "
                 "that sees it afterwards",
            orphaned=len(orphan), orphaned_nets=orphan_nets[:20])

    def xf(p):
        q = _rot_about(p, centre, rot) if abs(rot) > 1e-12 else p
        return (q[0] + dx, q[1] + dy)

    expect = {}
    for ref, node, ox, oy, orot in origins:
        nx, ny = xf((ox, oy))
        nrot = ((orot or 0.0) + float(rot) + 180.0) % 360.0 - 180.0
        pads = _set_fp_origin(ed, node, nx, ny, nrot)
        expect[ref] = [round(nx, 6), round(ny, 6), round(nrot, 6), pads]

    moved_items = 0
    sample = []
    for c in move_set:
        if _shift_item(c["node"], xf):
            moved_items += 1
            if len(sample) < 8:
                first = _item_points(c["node"])
                if first:
                    sample.append([c["kind"], round(first[0][0], 6),
                                   round(first[0][1], 6)])
    return {"members": [e[0] for e in origins],
            "centre": [round(cx, 6), round(cy, 6)],
            "delta": [dx, dy, rot],
            "bbox": [round(v, 4) for v in box],
            "bbox_area_mm2": round(box_area, 3),
            "copper_considered": len(copper),
            "copper_moved": moved_items,
            "copper_orphaned": len(orphan),
            "copper_orphaned_nets": orphan_nets[:20],
            "expect": expect, "expect_sample": sample}

def _verify_move_cluster(ed, o, record=None):
    detail = (record or {}).get("detail") or {}
    expect = detail.get("expect") or {}
    if not expect:
        return {"ok": False, "why": "no expected positions were recorded, so the "
                                    "move cannot be checked against anything"}
    bad = []
    for ref, want in expect.items():
        try:
            node = ed.footprint(ref)
        except OpError:
            bad.append([ref, "gone"])
            continue
        got = _fp_origin(node)
        if (abs(got[0] - want[0]) > 1e-5 or abs(got[1] - want[1]) > 1e-5
                or abs((got[2] or 0.0) - (want[2] or 0.0)) > 1e-5):
            bad.append([ref, [round(got[0], 6), round(got[1], 6), round(got[2], 6)]])
    sample_bad = []
    for kind, x, y in (detail.get("expect_sample") or []):
        hit = False
        for node in ed.items(kind):
            p = _item_points(node)
            if p and abs(p[0][0] - x) <= 1e-4 and abs(p[0][1] - y) <= 1e-4:
                hit = True
                break
        if not hit:
            sample_bad.append([kind, x, y])
    return {"ok": not bad and not sample_bad, "members_checked": len(expect),
            "moved_wrong": bad, "copper_sample_missing": sample_bad,
            "copper_sample": len(detail.get("expect_sample") or [])}

op("move_cluster", {"copper", "silk"},
   "Move and/or rotate a GROUP of footprints, plus the copper that belongs to "
   "them, as one rigid body. This is the only operation that can express 'make "
   "room here' - the fix for four of the five layout debts on RK3506 - and it is "
   "NOT an auto-placer: it moves exactly the parts you name, by exactly the "
   "offset you give. Before writing it measures which copper rides along (whole "
   "inside the cluster box, or landing on a moved pad) and which would be left "
   "behind; it REFUSES when anything would be orphaned unless allow_orphan=true, "
   "because a footprint that moves while its tracks stay put is a net that opens "
   "silently. Coordinates are the board frame (mm, Y up). `rot` turns the cluster "
   "counter-clockwise about its own centroid.",
   {"members": "[reference, ...] moved as one rigid body",
    "dx": "mm (+X to the right)", "dy": "mm (+Y up)",
    "rot": "deg, counter-clockwise about the cluster centroid",
    "with_copper": "move the copper that belongs to the cluster (default true)",
    "grow_mm": "widen the cluster box by this much before deciding what is inside",
    "allow_orphan": "proceed even though some copper would be left behind"},
   _run_move_cluster, _verify_move_cluster)

def _run_swap_pad_nets(ed, o, seed):
    ring = o.get("pads") or o.get("swaps")
    if not isinstance(ring, (list, tuple)) or len(ring) < 2:
        raise OpError("swap_pad_nets needs 'pads': at least two [ref, pad] pairs",
                      "ARG_MISSING")
    targets = []
    for i, entry in enumerate(ring):
        if not isinstance(entry, (list, tuple)) or len(entry) != 2:
            raise OpError("entry %d must be [reference, pad number], got %r"
                          % (i, entry), "ARG_INVALID")
        ref, number = str(entry[0]), str(entry[1])
        fp = ed.footprint(ref)
        pad = None
        for p in S.children(fp, "pad"):
            if (S.arg_text(p, 1) or "") == number:
                pad = p
                break
        if pad is None:
            raise OpError("footprint %r has no pad %r" % (ref, number), "PAD_NOT_FOUND")
        targets.append((ref, number, pad, _net_name(ed, pad)))

    # Rotate the assignment one step: pad i gets pad i+1's net. A cycle of length
    # n is n assignments but only ONE board state, which is the point - a
    # two-step version leaves two pads on the same net in between, and that is
    # the short this operation exists to avoid.
    before = [t[3] for t in targets]
    after = before[1:] + before[:1]
    for (ref, number, pad, _old), net in zip(targets, after):
        for old_node in list(S.children(pad, "net")):
            pad.items.remove(old_node)
        if net:
            if ed.name_only:
                pad.items.append(B.L(B.A("net"), B.A(net, True)))
            else:
                pad.items.append(B.L(B.A("net"), B.A(ed.net_code(net)),
                                     B.A(net, True)))
    return {"pads": [[t[0], t[1]] for t in targets],
            "before": before, "after": after}

def _verify_swap_pad_nets(ed, o, record=None):
    detail = (record or {}).get("detail") or {}
    want = detail.get("after")
    pads = detail.get("pads")
    if not want or not pads:
        return {"ok": False, "why": "nothing to check against"}
    bad = []
    for (ref, number), net in zip(pads, want):
        fp = ed.footprint(ref)
        got = ""
        for p in S.children(fp, "pad"):
            if (S.arg_text(p, 1) or "") == number:
                got = _net_name(ed, p)
                break
        if got != net:
            bad.append([ref, number, got, net])
    return {"ok": not bad, "checked": len(pads), "wrong": bad}

op("swap_pad_nets", {"nets"},
   "Rotate the net assignment around a ring of pads in ONE step. Board books this "
   "as a BGA whose ball order does not match the pinout: moving the assignment "
   "costs nothing and swapping pads two at a time leaves an intermediate board "
   "where both pads sit on the same net. Nothing moves - only which net each pad "
   "declares.",
   {"pads": "[[ref, pad], ...] - pad i takes pad i+1's net, last takes first's"},
   _run_swap_pad_nets, _verify_swap_pad_nets)

def _reserve_label(group):
    return "reserve:" + str(group).strip()

def _zone_name(node):
    return S.arg_text(S.child(node, "name"), 1) or ""

def _run_reserve_region(ed, o, seed):
    group = _str(o, "group")
    label = _reserve_label(group)
    layers = o.get("layers") or ([_str(o, "layer")] if o.get("layer") else None)
    if not layers:
        raise OpError("a reservation needs 'layer' or 'layers'", "ARG_MISSING")
    for l in layers:
        ed.require_layer(l)
    points = _zone_points(o)
    rules = o.get("rules") or ["tracks", "vias", "copperpour"]
    if isinstance(rules, str):
        rules = [rules]
    allow_existing = _bool(o, "allow_existing", False)
    for node in ed.items("zone"):
        if _zone_name(node) == label:
            raise OpError("a reservation named %r already exists on this board - "
                          "release it first (release_region) or use a different "
                          "group label" % group, "RESERVE_EXISTS")

    # Who the band is held FOR. If every comma-separated token is a net on this
    # board, the label IS a net list and occupancy can be judged (foreign copper
    # is a conflict). If not, it is just a label: the area is still written, but
    # nothing is called foreign, because guessing which tokens are nets is how a
    # checker starts inventing refusals.
    tokens = [t.strip() for t in str(group).split(",") if t.strip()]
    known = all(t in ed.net_by_name for t in tokens) if tokens else False
    inside = {"tracks": [], "vias": []}
    nets_inside = []
    for kind in ("segment", "arc"):
        for node in ed.items(kind):
            if not (set(_item_layers(node)) & set(layers)):
                continue
            pts = _item_points(node)
            if any(_pt_in_poly(p[0], p[1], points) for p in pts):
                inside["tracks"].append(node)
                nets_inside.append(_net_name(ed, node))
    for node in ed.items("via"):
        if not (set(_item_layers(node)) & set(layers)):
            continue
        p = _sub_xy(node, "at")
        if p is not None and _pt_in_poly(p[0], p[1], points):
            inside["vias"].append(node)
            nets_inside.append(_net_name(ed, node))
    foreign = sorted({n for n in nets_inside if n and n not in tokens}) if known else []
    if foreign and not allow_existing:
        raise OpError(
            "the area already carries %d copper item(s) of nets that are not in "
            "the reservation (%s). A reservation that overlaps someone else's "
            "copper is not a reservation - pick a free band, or pass "
            "allow_existing=true to record the overlap as known."
            % (len(inside["tracks"]) + len(inside["vias"]), ", ".join(foreign[:6])),
            "RESERVE_OCCUPIED",
            occupied={"tracks": len(inside["tracks"]), "vias": len(inside["vias"])},
            foreign_nets=foreign[:20])
    for l in layers:
        ed.append(B.zone_polygon_conf(0, "", l, points, ed.sp,
                                      B.new_id("%s:reserve:%s" % (seed, l)),
                                      keepout=rules, name=label))
    return {"group": group, "label": label, "layers": list(layers),
            "rules": rules, "vertices": len(points),
            "nets_are_known": known, "nets": tokens if known else [],
            "occupied_tracks": len(inside["tracks"]),
            "occupied_vias": len(inside["vias"]),
            "foreign_nets": foreign[:20],
            "bbox": [round(v, 4) for v in _pts_bbox(points)]}

def _verify_reserve_region(ed, o, record=None):
    detail = (record or {}).get("detail") or {}
    label = detail.get("label") or _reserve_label(_str(o, "group"))
    want_layers = set(detail.get("layers") or (
        o.get("layers") or ([o.get("layer")] if o.get("layer") else [])))
    # One zone per layer, so the layers the plan asked for are the UNION of what
    # the matching zones cover - asking every single zone to cover all of them
    # would fail the very case this operation is for (a band held on F.Cu and
    # B.Cu). Measured: the first version required each zone to hold the full set
    # and refused its own two-layer write.
    found = []
    covered = set()
    for node in ed.items("zone"):
        if _zone_name(node) != label:
            continue
        poly = S.child(node, "polygon")
        pts = S.child(poly, "pts") if poly is not None else None
        ok_pts = pts is not None and len(S.children(pts, "xy")) >= 3
        ko = S.child(node, "keepout")
        layers = _item_layers(node)
        covered |= set(layers)
        found.append({"layer": layers, "polygon_points": ok_pts,
                      "keepout": ko is not None})
    missing = sorted(want_layers - covered)
    return {"ok": bool(found) and not missing
                    and all(f["polygon_points"] and f["keepout"] for f in found),
            "reservations_found": found, "layers_missing": missing,
            "label": label}

op("reserve_region", {"copper"},
   "Hold an area for a named group of nets by writing a NAMED rule area "
   "(`reserve:<nets>`), and report what already sits inside it. add_keepout can "
   "only forbid and add_zone can only add copper; neither can say 'this band is "
   "being held for these nets', which is what routing a length-matched group "
   "BEFORE anything else needs. If every comma-separated token is a net on the "
   "board the label is a net list and existing copper of any other net is "
   "refused (allow_existing=true records it instead). Nothing about the board's "
   "existing copper is changed - this only adds the area.",
   {"group": "label, or a comma-separated net list e.g. \"DDR_D0,DDR_D1\"",
    "points": "[[x,y], ...] or x0/y0/x1/y1",
    "layer": "layer", "layers": "[layers]",
    "rules": "subset of tracks, vias, pads, copperpour, footprints (default "
             "tracks, vias, copperpour)",
    "allow_existing": "record existing foreign copper instead of refusing"},
   _run_reserve_region, _verify_reserve_region)

def _run_release_region(ed, o, seed):
    group = _str(o, "group")
    label = _reserve_label(group)
    removed = 0
    for node in list(ed.items("zone")):
        if _zone_name(node) == label:
            ed.remove(node)
            removed += 1
    if not removed:
        raise OpError("no reservation named %r on this board (it has: %s)"
                      % (label, ", ".join(sorted(_zone_name(z) for z in ed.items("zone")
                                                 if _zone_name(z))) or "no named zones"),
                      "RESERVE_NOT_FOUND")
    return {"group": group, "label": label, "zones_removed": removed}

def _verify_release_region(ed, o, record=None):
    label = _reserve_label(_str(o, "group"))
    left = [z for z in ed.items("zone") if _zone_name(z) == label]
    return {"ok": not left, "left_behind": len(left), "label": label}

op("release_region", {"copper"},
   "Delete a reservation written by reserve_region (every layer it was written "
   "on). Refuses when there is nothing with that name, and lists the names that "
   "do exist, so a typo is a refusal instead of a silent no-op.",
   {"group": "the same label reserve_region was given"},
   _run_release_region, _verify_release_region)
