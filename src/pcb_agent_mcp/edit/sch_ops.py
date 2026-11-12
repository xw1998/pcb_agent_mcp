"""The schematic write path: operations on a `.kicad_sch` tree.

**Coordinate frame - read this once.** A schematic is edited in the frame the
*file* uses: millimetres, origin at the page's top-left corner, **Y increasing
downwards**. This is deliberately different from the board write path, which
speaks Y-up because `board_view` reports Y-up. A schematic's own numbers are the
ones every other tool (KiCad's status bar, a `.kicad_sch` open in an editor, the
pin coordinates in the library) shows, and silently negating them would mirror
the drawing - which KiCad would happily accept. So: no negation anywhere in this
module, and every answer repeats the frame it used.

Why the operation set looks the way it does: a schematic is *connectivity*, not
geometry. The interesting operations are therefore the ones that change the
netlist - wires, junctions, labels, symbols - and every one of them is checked
twice: once by re-reading the file (postcondition) and once by KiCad's own
netlister and ERC (see `sch_plan`), because two correctly positioned wire ends
can still fail to connect and only the netlister knows.

Everything here is reversible by the same checkpoint protocol as the board path:
nothing is written until `sch_plan.apply_plan(confirm=True)`.
"""
import copy
import os

from ..fidelity import hierarchy as _hierarchy
from ..fidelity import sexpr as S
from . import build as B
from .ops import OpError

FRAME = ("millimetres in the schematic's own file frame: origin at the page's "
         "top-left, Y increases DOWNWARDS (the numbers you read in the "
         ".kicad_sch). The board write path uses Y-up; this one does not.")

SHAPES = ("input", "output", "bidirectional", "tri_state", "passive")

# ---------------------------------------------------------------- editor
class SchEditor(object):
    """A mutable schematic tree with the lookups the operations need."""

    def __init__(self, doc):
        self.doc = doc
        self.root = doc.root()
        self.sp = B.Spelling(doc.version())
        self.proj = doc.project()
        self.seed = None
        self._n = 0

    def uid(self):
        """A fresh object id: deterministic inside a plan run, random outside.

        Deterministic ids make a proposal reproducible byte for byte (two
        identical proposals in a test produce identical files), which is what
        makes a "the plan is wrong" regression test possible at all.
        """
        if self.seed is None:
            return B.new_id()
        self._n += 1
        return B.new_id("%s:%d" % (self.seed, self._n))

    # ---------------------------------------------------------- insertion
    def insertion_index(self):
        """Where a new drawing object goes: after the last drawing object.

        KiCad writes its own order (`lib_symbols`, objects, `sheet_instances`,
        `embedded_fonts`), so a new object is put in front of the first
        bookkeeping node instead of being appended behind it - that keeps the
        file looking like a file KiCad wrote.
        """
        stop = ("sheet_instances", "embedded_fonts", "bus_alias")
        for i, item in enumerate(self.root.items):
            if isinstance(item, S.Seq) and S.head(item) in stop:
                return i
        return len(self.root.items)

    def add_object(self, node):
        S.insert_child(self.root, self.insertion_index(), node)
        return node

    def add_lib_symbol(self, node):
        """Embed a library definition the schematic does not carry yet.

        `lib_symbols` is a bookkeeping node: KiCad writes it right after the
        title block and before every drawing object, so that is where it goes
        when the schematic has none (a schematic always has one in practice -
        `(lib_symbols)` can be empty, which is the case handled here).
        """
        lib = self.doc.lib_symbols()
        if lib is None:
            lib = S.Seq([S.Atom("lib_symbols")])
            index = None
            for i, item in enumerate(self.root.items):
                if isinstance(item, S.Seq) and S.head(item) in (
                        "version", "generator", "generator_version", "uuid",
                        "paper", "title_block"):
                    index = i + 1
            if index is None:
                index = self.insertion_index()
            S.insert_child(self.root, index, lib)
        S.append_child(lib, node)
        return node

    # ---------------------------------------------------------- lookups
    def symbols(self):
        return self.doc.symbols()

    def find_symbol(self, reference):
        for node in self.doc.items("symbol"):
            entry = self.doc.symbol_entry(node)
            if entry["reference"] == reference:
                return node, entry
        return None, None

    def find_by_uuid(self, uid):
        return self.doc.by_uuid(uid)

    def next_reference(self, prefix):
        best = 0
        for s in self.symbols():
            ref = s["reference"] or ""
            if ref.startswith(prefix) and ref[len(prefix):].isdigit():
                best = max(best, int(ref[len(prefix):]))
        return "%s%d" % (prefix, best + 1)

    def pin_points(self):
        """Absolute sheet position of every pin of every placed symbol.

        `[(x, y, reference, number, lib_id)]`. Needed by `objects_near`: a pin
        is a connection point but it is not a drawing object - its position
        lives in the library definition (`(pin ... (at x y angle))`, y up) and
        is only mapped onto the sheet by the placed symbol's `at`/`mirror`.
        Without this every wire drawn from a pin to a label looks like it
        touches nothing.

        The mapping is `sheet = (X + dx, Y - dy)` with the library's +y flipped
        to the sheet's -y, after the mirror and the rotation have been applied
        inside the library frame. KiCad only places symbols at 90-degree steps,
        so anything else is reported as unrotated rather than guessed.
        """
        out = []
        for node in self.doc.items("symbol"):
            entry = self.doc.symbol_entry(node)
            definition = self.doc.lib_symbol(entry["lib_id"])
            if definition is None or entry["x"] is None or entry["y"] is None:
                continue
            rot = int(round(entry["rotation"] or 0)) % 360
            mirror = entry["mirror"]
            for pin in _all_children(definition, "pin"):
                at = S.child(pin, "at")
                px, py = S.arg_num(at, 1), S.arg_num(at, 2)
                if px is None or py is None:
                    continue
                if mirror == "x":
                    py = -py
                elif mirror == "y":
                    px = -px
                if rot == 90:
                    px, py = -py, px
                elif rot == 180:
                    px, py = -px, -py
                elif rot == 270:
                    px, py = py, -px
                number = S.arg_text(S.child(pin, "number"), 1)
                out.append((entry["x"] + px, entry["y"] - py,
                            entry["reference"], number, entry["lib_id"]))
        return out

    def objects_near(self, x, y, tol=1e-3, kinds=None):
        """Every drawing object whose `at`/endpoint is at (x, y), plus every
        symbol pin sitting there. Used to catch 'the wire you asked for is not
        attached to anything' before writing."""
        hits = []
        if not kinds or "pin" in kinds:
            for px, py, ref, number, lib_id in self.pin_points():
                if abs(px - x) <= tol and abs(py - y) <= tol:
                    hits.append({"kind": "pin", "uuid": None, "reference": ref,
                                 "pin": number, "lib_id": lib_id})
        for item in self.root.items[1:]:
            h = S.head(item)
            if h is None or (kinds and h not in kinds):
                continue
            pts = []
            at = S.child(item, "at")
            if at is not None:
                pts.append((S.arg_num(at, 1), S.arg_num(at, 2)))
            pnode = S.child(item, "pts")
            if pnode is not None:
                for xy in S.children(pnode, "xy"):
                    pts.append((S.arg_num(xy, 1), S.arg_num(xy, 2)))
            for px, py in pts:
                if px is None or py is None:
                    continue
                if abs(px - x) <= tol and abs(py - y) <= tol:
                    hits.append({"kind": h, "uuid": _id(item)})
                    break
        return hits

def _all_children(node, head):
    """Descendant `(head ...)` nodes at any depth (library symbols nest their
    bodies as `(symbol "NAME_0_1" ... (pin ...))`)."""
    out = []
    for child in S.children(node, head):
        out.append(child)
    for sub in S.children(node, "symbol"):
        out.extend(_all_children(sub, head))
    return out

def _id(node):
    for name in ("uuid", "tstamp"):
        n = S.child(node, name)
        if n is not None:
            v = S.arg(n, 1)
            if isinstance(v, S.Atom):
                return v.text
    return None

# ---------------------------------------------------------------- argument helpers
def _num(op, key, required=True, default=None):
    v = op.get(key, default)
    if v is None:
        if required:
            raise OpError("operation %r needs a numeric %r"
                          % (op.get("op"), key), "ARG_MISSING")
        return None
    try:
        f = float(v)
    except (TypeError, ValueError):
        raise OpError("%r must be a number, got %r" % (key, v), "ARG_INVALID")
    if f != f or f in (float("inf"), float("-inf")):
        raise OpError("%r must be finite, got %r" % (key, v), "ARG_INVALID")
    return f

def _str(op, key, required=True, default=None):
    v = op.get(key, default)
    if v is None or (isinstance(v, str) and not v.strip()):
        if required:
            raise OpError("operation %r needs %r" % (op.get("op"), key), "ARG_MISSING")
        return None
    if not isinstance(v, str):
        raise OpError("%r must be a string, got %r" % (key, v), "ARG_INVALID")
    return v

def _bool(op, key, default=False):
    v = op.get(key, default)
    return bool(v)

def _xy(op, prefix=""):
    return (_num(op, prefix + "x"), _num(op, prefix + "y"))

def _points(op, key="points"):
    pts = op.get(key)
    if not pts or not isinstance(pts, (list, tuple)) or len(pts) < 2:
        raise OpError("%r needs at least two [x, y] points (file frame: mm, Y down)"
                      % key, "ARG_MISSING")
    out = []
    for p in pts:
        if not isinstance(p, (list, tuple)) or len(p) < 2:
            raise OpError("each point must be [x, y], got %r" % (p,), "ARG_INVALID")
        out.append((float(p[0]), float(p[1])))
    return out

def _size(op, default=1.27):
    return _num(op, "size", required=False, default=default)

# ---------------------------------------------------------------- builders
def A(text, quoted=False):
    return S.Atom(text, quoted)

def L(*items):
    return S.Seq(list(items))

def num(v):
    return B.num(v)

def xy(x, y):
    return L(A("xy"), A(num(x)), A(num(y)))

def pts(points):
    return L(A("pts"), *[xy(x, y) for x, y in points])

def at(x, y, rot=None):
    items = [A("at"), A(num(x)), A(num(y))]
    if rot is not None:
        items.append(A(num(rot)))
    return L(*items)

def stroke(width=0, kind="default"):
    return L(A("stroke"), L(A("width"), A(num(width))),
             L(A("type"), A(kind)))

# KiCad renders these fields hidden by default: they are data, not silkscreen.
# Footprint used to be written visible, which dumped the footprint UUID / name
# onto the drawing as a floating string.
_HIDDEN_FIELDS = ("Footprint", "Datasheet", "Description")


def effects(size=1.27, justify=None, hide=False, bold=False, italic=False):
    font = [A("font"), L(A("size"), A(num(size)), A(num(size)))]
    if bold:
        font.append(L(A("bold"), A("yes")))
    if italic:
        font.append(L(A("italic"), A("yes")))
    items = [A("effects"), L(*font)]
    if justify:
        items.append(L(A("justify"), *[A(j) for j in justify.split()]))
    if hide:
        items.append(L(A("hide"), A("yes")))
    return L(*items)

def prop(name, value, x, y, rot=0, hide=False, size=1.27, justify=None):
    """A `(property ...)` node.

    `hide` is written *inside* `effects`, which is how 84 502 of the 89 589
    properties in KiCad 10's own demo schematics spell it (the sibling form
    appears 2 505 times and is accepted too - this is the majority form).
    """
    return L(A("property"), A(name, True), A(value, True), at(x, y, rot),
             effects(size=size, justify=justify, hide=hide))

# ---------------------------------------------------------------- registry
OPS = {}

def op(name, affects, summary, params, run, verify):
    OPS[name] = {"affects": sorted([affects] if isinstance(affects, str) else affects),
                 "summary": summary, "params": params, "run": run, "verify": verify}

def describe():
    return [{"op": n, "affects": s["affects"], "summary": s["summary"],
             "params": s["params"], "coordinates": FRAME}
            for n, s in sorted(OPS.items())]

# ---------------------------------------------------------------- wires
def _wire_like(ed, o, head):
    points = _points(o)
    width = _num(o, "width", required=False, default=0)
    stroke_type = _str(o, "stroke_type", required=False, default="default")
    # what the two ends will touch is asked BEFORE drawing: a wire's own uuid is
    # never evidence that it is connected to anything.
    ends_before = [ed.objects_near(p[0], p[1]) for p in (points[0], points[-1])]
    nodes = []
    for i in range(len(points) - 1):
        (x1, y1), (x2, y2) = points[i], points[i + 1]
        if abs(x1 - x2) < 1e-9 and abs(y1 - y2) < 1e-9:
            continue
        node = L(A(head), pts([(x1, y1), (x2, y2)]),
                 stroke(width, stroke_type), ed.sp.id_node(ed.uid()))
        ed.add_object(node)
        nodes.append({"uuid": _id(node), "from": [x1, y1], "to": [x2, y2]})
    if not nodes:
        raise OpError("every segment had zero length - nothing to draw", "OP_INVALID")
    return {"segments": nodes, "segment_count": len(nodes),
            "endpoints_unattached": [
                {"point": list(p), "objects_here": here}
                for p, here in ((points[0], ends_before[0]),
                                (points[-1], ends_before[1]))
                if not here]}

def _verify_wire_like(ed, o, head):
    points = _points(o)
    have = []
    for node in ed.doc.items(head):
        p = S.child(node, "pts")
        if p is None:
            continue
        have.append([(S.arg_num(xy_, 1), S.arg_num(xy_, 2))
                     for xy_ in S.children(p, "xy")])
    missing = []
    for i in range(len(points) - 1):
        seg = [(round(points[i][0], 4), round(points[i][1], 4)),
               (round(points[i + 1][0], 4), round(points[i + 1][1], 4))]
        if not any([(round(a[0], 4), round(a[1], 4)) for a in seg] ==
                   [(round(b[0], 4), round(b[1], 4)) for b in h] for h in have):
            missing.append(seg)
    return {"ok": not missing, "node": head, "segments_written": len(points) - 1,
            "missing_segments": missing,
            "method": "every requested segment is present in the file as its own node"}

def _run_add_wire(ed, o, seed):
    return _wire_like(ed, o, "wire")

def _verify_add_wire(ed, o):
    return _verify_wire_like(ed, o, "wire")

op("add_wire",
   ["wires"],
   "Draw wire segments through the given points (consecutive pairs become one "
   "`(wire ...)` each, exactly like KiCad's own wire tool).",
   {"points": "[[x, y], ...] - at least two, file frame mm Y-down",
    "width": "stroke width in mm, default 0 (KiCad's default)", "stroke_type":
    "default|solid|dash|dot|dash_dot|dash_dot_dot"},
   _run_add_wire, _verify_add_wire)

def _run_add_bus(ed, o, seed):
    return _wire_like(ed, o, "bus")

def _verify_add_bus(ed, o):
    return _verify_wire_like(ed, o, "bus")

op("add_bus", ["wires"],
   "Draw bus segments (the thick trunk a bus label names). Same point list rule "
   "as add_wire; members are attached with add_bus_entry + add_label.",
   {"points": "[[x, y], ...]", "width": "default 0"}, _run_add_bus, _verify_add_bus)

def _run_add_bus_entry(ed, o, seed):
    x, y = _xy(o)
    sx = _num(o, "size_x", required=False, default=2.54)
    sy = _num(o, "size_y", required=False, default=2.54)
    node = L(A("bus_entry"), at(x, y),
             L(A("size"), A(num(sx)), A(num(sy))),
             stroke(0, _str(o, "stroke_type", required=False, default="default")),
             ed.sp.id_node(ed.uid()))
    ed.add_object(node)
    return {"uuid": _id(node), "at": [x, y], "size": [sx, sy]}

def _verify_add_bus_entry(ed, o):
    x, y = _xy(o)
    for node in ed.doc.items("bus_entry"):
        a = S.child(node, "at")
        if a is not None and abs(S.arg_num(a, 1) - x) < 1e-4 \
                and abs(S.arg_num(a, 2) - y) < 1e-4:
            return {"ok": True, "uuid": _id(node)}
    return {"ok": False, "error": "no bus_entry at %s,%s" % (x, y)}

op("add_bus_entry", ["wires"],
   "Attach a wire to a bus: the short 45-degree stub at (x, y).",
   {"x": "mm", "y": "mm", "size_x": "default 2.54", "size_y": "default 2.54"},
   _run_add_bus_entry, _verify_add_bus_entry)

# ---------------------------------------------------------------- dots
def _junction_like(ed, o, head):
    x, y = _xy(o)
    if head == "junction":
        dia = _num(o, "diameter", required=False, default=1.016)
        color = o.get("color") or [0, 0, 0, 0]
        node = L(A("junction"), at(x, y), L(A("diameter"), A(num(dia))),
                 L(A("color"), *[A(num(c)) for c in color]),
                 ed.sp.id_node(ed.uid()))
    else:
        node = L(A("no_connect"), at(x, y), ed.sp.id_node(ed.uid()))
    ed.add_object(node)
    return {"uuid": _id(node), "at": [x, y],
            "objects_here": ed.objects_near(x, y)}

def _verify_dot(ed, o, head):
    x, y = _xy(o)
    for node in ed.doc.items(head):
        a = S.child(node, "at")
        if a is not None and abs(S.arg_num(a, 1) - x) < 1e-4 \
                and abs(S.arg_num(a, 2) - y) < 1e-4:
            return {"ok": True, "uuid": _id(node)}
    return {"ok": False, "error": "no %s at %s,%s" % (head, x, y)}

def _run_add_junction(ed, o, seed):
    return _junction_like(ed, o, "junction")

def _run_add_no_connect(ed, o, seed):
    return _junction_like(ed, o, "no_connect")

op("add_junction", ["wires"],
   "Put a junction dot where three or more wires meet. Without it the crossing "
   "is not a connection and the netlist disagrees with the drawing.",
   {"x": "mm", "y": "mm", "diameter": "default 1.016 mm",
    "color": "[r, g, b, a] 0..1, default transparent"},
   _run_add_junction, lambda ed, o: _verify_dot(ed, o, "junction"))

op("add_no_connect", ["wires"],
   "Mark a pin as deliberately unconnected (the X). It silences ERC's "
   "'pin not connected' for that pin - it does not connect anything.",
   {"x": "mm", "y": "mm"},
   _run_add_no_connect, lambda ed, o: _verify_dot(ed, o, "no_connect"))

# ---------------------------------------------------------------- labels
def _label_node(ed, o, head):
    text = _str(o, "text")
    x, y = _xy(o)
    rot = _num(o, "rotation", required=False, default=0)
    size = _size(o)
    justify = _str(o, "justify", required=False,
                   default="left bottom" if rot in (0, 90) else "right bottom")
    shape = _str(o, "shape", required=False, default="input")
    if head in ("global_label", "hierarchical_label") and shape not in SHAPES:
        raise OpError("shape must be one of %s" % ", ".join(SHAPES), "ARG_INVALID")
    items = [A(head), A(text, True)]
    if head in ("global_label", "hierarchical_label"):
        items.append(L(A("shape"), A(shape)))
    items.append(at(x, y, rot))
    if head == "global_label":
        items.append(L(A("fields_autoplaced"), A("yes")))
    items.append(effects(size=size, justify=justify))
    items.append(ed.sp.id_node(ed.uid()))
    if head == "global_label":
        # every global label KiCad writes carries this field; it is what renders
        # the "where else does this net go" page list. Position is cosmetic and
        # KiCad re-autoplaces it on load (fields_autoplaced yes).
        ox, oy, ojustify = _intersheet_offset(x, y, rot, text, size)
        items.append(prop("Intersheetrefs", "${INTERSHEET_REFS}", ox, oy, 0,
                          hide=True, size=1.27, justify=ojustify))
    return items

def _intersheet_offset(x, y, rot, text, size):
    gap = max(2.54, 0.5 * size * max(4, len(text)))
    if rot == 90:
        return x, y - gap, "left"
    if rot == 180:
        return x - gap, y, "right"
    if rot == 270:
        return x, y + gap, "left"
    return x + gap, y, "left"

def _run_add_label(ed, o, head):
    x, y = _xy(o)
    here = ed.objects_near(x, y)          # before adding: what would it attach to
    node = L(*_label_node(ed, o, head))
    ed.add_object(node)
    return {"uuid": _id(node), "kind": head, "text": _str(o, "text"),
            "at": [x, y], "objects_here": here,
            "note": ("a label only names the net it touches: if nothing is at that "
                     "point the label is dangling and the netlister will say so")}

def _verify_label(ed, o, head):
    text = _str(o, "text")
    x, y = _xy(o)
    for node in ed.doc.items(head):
        if S.arg_text(node, 1) != text:
            continue
        a = S.child(node, "at")
        if a is not None and abs(S.arg_num(a, 1) - x) < 1e-4 \
                and abs(S.arg_num(a, 2) - y) < 1e-4:
            return {"ok": True, "uuid": _id(node)}
    return {"ok": False, "error": "no %s %r at %s,%s" % (head, text, x, y)}

op("add_label", ["labels"],
   "Local net label: names the net at that point, visible on this sheet only.",
   {"text": "net name", "x": "mm", "y": "mm", "rotation": "0|90|180|270",
    "size": "text size mm, default 1.27", "justify": "default follows rotation"},
   lambda ed, o, s: _run_add_label(ed, o, "label"),
   lambda ed, o: _verify_label(ed, o, "label"))

op("add_global_label", ["labels"],
   "Global label: the net of that name is the same net on every sheet of the "
   "project. Use it for power and inter-sheet signals.",
   {"text": "net name", "x": "mm", "y": "mm", "rotation": "0|90|180|270",
    "shape": "input|output|bidirectional|tri_state|passive",
    "size": "default 1.27"},
   lambda ed, o, s: _run_add_label(ed, o, "global_label"),
   lambda ed, o: _verify_label(ed, o, "global_label"))

op("add_hierarchical_label", ["labels"],
   "Hierarchical label: the port of this sheet. It is what a `(sheet ...)` pin "
   "on the parent sheet connects to - it does not cross sheets by itself.",
   {"text": "port name", "x": "mm", "y": "mm", "rotation": "0|90|180|270",
    "shape": "input|output|bidirectional|tri_state|passive", "size": "default 1.27"},
   lambda ed, o, s: _run_add_label(ed, o, "hierarchical_label"),
   lambda ed, o: _verify_label(ed, o, "hierarchical_label"))

def _run_add_netclass_flag(ed, o, seed):
    netclass = _str(o, "netclass")
    x, y = _xy(o)
    rot = _num(o, "rotation", required=False, default=0)
    length = _num(o, "length", required=False, default=2.54)
    shape = _str(o, "shape", required=False, default="round")
    justify = "left bottom" if rot in (0, 90) else "right bottom"
    ox, oy, ojustify = _intersheet_offset(x, y, rot, netclass, 1.27)
    node = L(A("netclass_flag"), A("", True),
             L(A("length"), A(num(length))), L(A("shape"), A(shape)),
             at(x, y, rot), effects(justify=justify),
             ed.sp.id_node(ed.uid()),
             prop("Netclass", netclass, ox, oy, 0, size=1.27, justify=ojustify))
    ed.add_object(node)
    return {"uuid": _id(node), "netclass": netclass, "at": [x, y]}

def _verify_add_netclass_flag(ed, o):
    netclass = _str(o, "netclass")
    x, y = _xy(o)
    for node in ed.doc.items("netclass_flag"):
        a = S.child(node, "at")
        if a is None:
            continue
        if abs(S.arg_num(a, 1) - x) > 1e-4 or abs(S.arg_num(a, 2) - y) > 1e-4:
            continue
        for p in S.children(node, "property"):
            if S.arg_text(p, 1) == "Netclass" and S.arg_text(p, 2) == netclass:
                return {"ok": True, "uuid": _id(node)}
    return {"ok": False, "error": "no netclass_flag %r at %s,%s" % (netclass, x, y)}

op("add_netclass_flag", ["labels"],
   "Netclass flag: assigns the net at that point to a netclass (that is where "
   "diff-pair / impedance constraints attach to a net).",
   {"netclass": "existing netclass name", "x": "mm", "y": "mm",
    "rotation": "0|90|180|270", "length": "default 2.54", "shape": "round|dot|diamond|rectangle"},
   _run_add_netclass_flag, _verify_add_netclass_flag)

# ---------------------------------------------------------------- text / graphics
def _run_add_text(ed, o, seed):
    text = _str(o, "text")
    x, y = _xy(o)
    rot = _num(o, "rotation", required=False, default=0)
    size = _size(o)
    justify = _str(o, "justify", required=False,
                   default="left bottom" if rot in (0, 90) else "right bottom")
    node = L(A("text"), A(text, True),
             L(A("exclude_from_sim"), A("no")), at(x, y, rot),
             effects(size=size, justify=justify,
                     bold=_bool(o, "bold"), italic=_bool(o, "italic")),
             ed.sp.id_node(ed.uid()))
    ed.add_object(node)
    return {"uuid": _id(node), "at": [x, y], "chars": len(text)}

def _verify_add_text(ed, o):
    text = _str(o, "text")
    x, y = _xy(o)
    for node in ed.doc.items("text"):
        if S.arg_text(node, 1) != text:
            continue
        a = S.child(node, "at")
        if a is not None and abs(S.arg_num(a, 1) - x) < 1e-4 \
                and abs(S.arg_num(a, 2) - y) < 1e-4:
            return {"ok": True, "uuid": _id(node)}
    return {"ok": False, "error": "no text node with that content at %s,%s" % (x, y)}

op("add_text", ["text"],
   "Free text on the sheet (notes, not connectivity).",
   {"text": "the string", "x": "mm", "y": "mm", "rotation": "0|90|180|270",
    "size": "default 1.27", "bold": "bool", "italic": "bool"},
   _run_add_text, _verify_add_text)

def _run_add_polyline(ed, o, seed):
    points = _points(o)
    width = _num(o, "width", required=False, default=0)
    fill = _str(o, "fill", required=False, default="none")
    node = L(A("polyline"), pts(points), stroke(width,
                                                _str(o, "stroke_type", required=False,
                                                     default="default")),
             L(A("fill"), L(A("type"), A(fill))), ed.sp.id_node(ed.uid()))
    ed.add_object(node)
    return {"uuid": _id(node), "points": len(points), "note": "graphics only, no net"}

def _verify_add_polyline(ed, o):
    points = _points(o)
    want = [(round(x, 3), round(y, 3)) for x, y in points]
    for node in ed.doc.items("polyline"):
        p = S.child(node, "pts")
        if p is None:
            continue
        got = [(round(S.arg_num(e, 1), 3), round(S.arg_num(e, 2), 3))
               for e in S.children(p, "xy")]
        if got == want:
            return {"ok": True, "uuid": _id(node)}
    return {"ok": False, "error": "no polyline with that point list"}

op("add_polyline", ["text"],
   "Polyline graphic (not a wire - it carries no net).",
   {"points": "[[x, y], ...]", "width": "default 0",
    "fill": "none|outline|background", "stroke_type": "default|solid|dash|..."},
   _run_add_polyline, _verify_add_polyline)

def _run_add_rectangle(ed, o, seed):
    x0, y0 = _xy(o, "start_")
    x1, y1 = _xy(o, "end_")
    width = _num(o, "width", required=False, default=0)
    node = L(A("rectangle"), L(A("start"), A(num(x0)), A(num(y0))),
             L(A("end"), A(num(x1)), A(num(y1))),
             stroke(width, _str(o, "stroke_type", required=False, default="default")),
             L(A("fill"), L(A("type"), A(_str(o, "fill", required=False,
                                             default="none")))),
             ed.sp.id_node(ed.uid()))
    ed.add_object(node)
    return {"uuid": _id(node), "corners": [[x0, y0], [x1, y1]]}

def _verify_add_rectangle(ed, o):
    x0, y0 = _xy(o, "start_")
    x1, y1 = _xy(o, "end_")
    for node in ed.doc.items("rectangle"):
        s, e = S.child(node, "start"), S.child(node, "end")
        if s is None or e is None:
            continue
        if (abs(S.arg_num(s, 1) - x0) < 1e-4 and abs(S.arg_num(s, 2) - y0) < 1e-4
                and abs(S.arg_num(e, 1) - x1) < 1e-4
                and abs(S.arg_num(e, 2) - y1) < 1e-4):
            return {"ok": True, "uuid": _id(node)}
    return {"ok": False, "error": "no rectangle with those corners"}

op("add_rectangle", ["text"],
   "Rectangle graphic (sheet decoration, not a wire).",
   {"start_x": "mm", "start_y": "mm", "end_x": "mm", "end_y": "mm",
    "width": "default 0", "fill": "none|outline|background"},
   _run_add_rectangle, _verify_add_rectangle)


def _run_add_arc(ed, o, seed):
    x0, y0 = _xy(o, "start_")
    xm, ym = _xy(o, "mid_")
    x1, y1 = _xy(o, "end_")
    if (round(x0, 6), round(y0, 6)) == (round(x1, 6), round(y1, 6)):
        raise OpError("arc start and end are the same point", "ARG_INVALID")
    width = _num(o, "width", required=False, default=0)
    node = L(A("arc"), L(A("start"), A(num(x0)), A(num(y0))),
             L(A("mid"), A(num(xm)), A(num(ym))),
             L(A("end"), A(num(x1)), A(num(y1))),
             stroke(width, _str(o, "stroke_type", required=False, default="default")),
             L(A("fill"), L(A("type"), A(_str(o, "fill", required=False,
                                             default="none")))),
             ed.sp.id_node(ed.uid()))
    ed.add_object(node)
    return {"uuid": _id(node), "start": [x0, y0], "mid": [xm, ym],
            "end": [x1, y1]}

def _verify_add_arc(ed, o):
    pts = [(_xy(o, "start_"), "start"), (_xy(o, "mid_"), "mid"),
           (_xy(o, "end_"), "end")]
    for node in ed.doc.items("arc"):
        ok = True
        for (x, y), role in pts:
            n = S.child(node, role)
            if n is None or abs(S.arg_num(n, 1) - x) > 1e-4 \
                    or abs(S.arg_num(n, 2) - y) > 1e-4:
                ok = False
                break
        if ok:
            return {"ok": True, "uuid": _id(node)}
    return {"ok": False, "error": "no arc with those start/mid/end points"}

op("add_arc", ["text"],
   "Arc graphic (not a wire) - three points, KiCad derives the centre itself.",
   {"start_x": "mm", "start_y": "mm", "mid_x": "mm", "mid_y": "mm",
    "end_x": "mm", "end_y": "mm", "width": "default 0",
    "fill": "none|outline|background", "stroke_type": "default|solid|dash|..."},
   _run_add_arc, _verify_add_arc)

def _run_add_circle(ed, o, seed):
    cx, cy = _xy(o, "center_")
    radius = _num(o, "radius")
    if radius <= 0:
        raise OpError("radius must be > 0, got %r" % (radius,), "ARG_INVALID")
    width = _num(o, "width", required=False, default=0)
    node = L(A("circle"), L(A("center"), A(num(cx)), A(num(cy))),
             L(A("radius"), A(num(radius))),
             stroke(width, _str(o, "stroke_type", required=False, default="default")),
             L(A("fill"), L(A("type"), A(_str(o, "fill", required=False,
                                             default="none")))),
             ed.sp.id_node(ed.uid()))
    ed.add_object(node)
    return {"uuid": _id(node), "center": [cx, cy], "radius": radius}

def _verify_add_circle(ed, o):
    cx, cy = _xy(o, "center_")
    radius = _num(o, "radius")
    for node in ed.doc.items("circle"):
        c, r = S.child(node, "center"), S.child(node, "radius")
        if c is None or r is None:
            continue
        if (abs(S.arg_num(c, 1) - cx) < 1e-4 and abs(S.arg_num(c, 2) - cy) < 1e-4
                and abs(S.arg_num(r, 1) - radius) < 1e-4):
            return {"ok": True, "uuid": _id(node)}
    return {"ok": False, "error": "no circle with that centre and radius"}

op("add_circle", ["text"],
   "Circle graphic (not a wire).",
   {"center_x": "mm", "center_y": "mm", "radius": "mm, > 0",
    "width": "default 0", "fill": "none|outline|background",
    "stroke_type": "default|solid|dash|..."},
   _run_add_circle, _verify_add_circle)

# ---------------------------------------------------------------- symbols
def _pin_numbers(definition):
    """Pin numbers declared by a library symbol, in declaration order."""
    out = []

    def visit(node):
        for pin in S.children(node, "pin"):
            number = S.child(pin, "number")
            text = S.arg_text(number, 1) if number is not None else None
            if text and text not in out:
                out.append(text)
        for sub in S.children(node, "symbol"):
            visit(sub)

    visit(definition)
    return out

def _definition_properties(definition):
    out = []
    for p in S.children(definition, "property"):
        name = S.arg_text(p, 1)
        value = S.arg_text(p, 2)
        a = S.child(p, "at")
        hidden = S.child(p, "hide") is not None
        if not hidden:
            eff = S.child(p, "effects")
            if eff is not None:
                hidden = S.child(eff, "hide") is not None
        out.append({"name": name, "value": value,
                    "x": S.arg_num(a, 1, 0.0), "y": S.arg_num(a, 2, 0.0),
                    "rotation": S.arg_num(a, 3, 0.0), "hide": hidden})
    return out

def _rotate(dx, dy, rot):
    """Rotate an offset that is still in the LIBRARY frame (Y is UP there).

    The caller turns the result into the sheet frame, where Y runs DOWN, by
    subtracting the rotated y. Doing that the other way round -- keeping the
    library's sign and only rotating -- mirrored every field about the symbol
    origin: U8's Value printed 5.08 mm *under* the body instead of above it, on
    top of its own GND pin name. Rotation 0 is what exposes it, so nothing here
    was ever caught by a rotation test.
    """
    r = int(round(rot)) % 360
    if r == 90:
        return -dy, dx
    if r == 180:
        return -dx, -dy
    if r == 270:
        return dy, -dx
    return dx, dy

def _load_definition(ed, o):
    lib_id = _str(o, "lib_id")
    node = ed.doc.lib_symbol(lib_id)
    if node is not None:
        return copy.deepcopy(node), {
            "source": "this schematic's own lib_symbols",
            "added_to_lib_symbols": False, "definition_version": None}
    raw = None
    version = None
    if o.get("definition"):
        text = o["definition"]
        raw = text
        try:
            tree = S.parse(text)
        except S.SexprError as exc:
            raise OpError("definition is not a valid S-expression: %s" % exc,
                          "ARG_INVALID")
        if S.head(tree.items[0]) != "symbol":
            raise OpError("definition must be a `(symbol \"NAME\" ...)` node",
                          "ARG_INVALID")
        node = copy.deepcopy(tree.items[0])
        source = "the `definition` argument"
    elif o.get("library"):
        path = o["library"]
        if not os.path.isfile(path):
            raise OpError("library file does not exist: %r" % path, "FILE_NOT_FOUND")
        with open(path, "rb") as fh:
            data = fh.read()
        try:
            tree = S.parse(data.decode("utf-8", "surrogateescape"))
        except S.SexprError as exc:
            raise OpError("%s is not a valid .kicad_sym: %s"
                          % (os.path.basename(path), exc), "PARSE_FAILED")
        if S.head(tree.items[0]) != "kicad_symbol_lib":
            raise OpError("%s is not a KiCad symbol library (expected "
                          "`(kicad_symbol_lib ...)`)" % path, "FORMAT_NOT_SUPPORTED")
        version = S.arg_text(S.child(tree.items[0], "version"), 1)
        want = o.get("symbol") or lib_id.split(":")[-1]
        for s in S.children(tree.items[0], "symbol"):
            if S.arg_text(s, 1) == want:
                node = copy.deepcopy(s)
                break
        if node is None:
            names = [S.arg_text(s, 1) for s in S.children(tree.items[0], "symbol")]
            raise OpError("symbol %r is not in %s (the library has %d symbols; pass "
                          "`symbol` to name the entry)" % (want, os.path.basename(path),
                                                           len(names)),
                          "SYMBOL_NOT_IN_LIBRARY")
        source = "library file %s" % os.path.basename(path)
    else:
        raise OpError("lib_id %r is not embedded in this schematic, and no "
                      "`library` (.kicad_sym path) or `definition` (S-expression) "
                      "was given - refusing to invent a symbol body"
                      % lib_id, "MISSING_SYMBOL_DEFINITION")
    # the embedded entry is the library symbol renamed to "lib:NAME"; the nested
    # body symbols (NAME_0_1, NAME_1_1) keep their own names, which is exactly
    # what KiCad writes into lib_symbols.
    if len(node.items) > 1 and isinstance(node.items[1], S.Atom):
        node.items[1].text = lib_id
        node.items[1].quoted = True
    return node, {"source": source, "added_to_lib_symbols": True,
                  "definition_version": version}

def _field_positions(o):
    """Normalise the optional `property_positions` argument into {name: (x, y, rot)}."""
    raw = o.get("property_positions") or {}
    if not isinstance(raw, dict):
        raise OpError("property_positions must be an object {field: {x, y, rotation}}",
                      "ARG_INVALID")
    out = {}
    for name, v in raw.items():
        if isinstance(v, dict):
            if "x" not in v or "y" not in v:
                raise OpError("property_positions[%r] needs both x and y" % name,
                              "ARG_INVALID")
            out[name] = (float(v["x"]), float(v["y"]), int(v.get("rotation") or 0) % 360)
        elif isinstance(v, (list, tuple)) and len(v) >= 2:
            out[name] = (float(v[0]), float(v[1]),
                         int(v[2]) % 360 if len(v) > 2 else 0)
        else:
            raise OpError("property_positions[%r] must be {x, y, rotation} or [x, y]"
                          % name, "ARG_INVALID")
    return out

def _run_add_symbol(ed, o, seed):
    lib_id = _str(o, "lib_id")
    x, y = _xy(o)
    rot = _num(o, "rotation", required=False, default=0)
    unit = int(_num(o, "unit", required=False, default=1))
    mirror = _str(o, "mirror", required=False)
    if mirror and mirror not in ("x", "y"):
        raise OpError("mirror must be 'x' or 'y'", "ARG_INVALID")
    definition, info = _load_definition(ed, o)
    if info["added_to_lib_symbols"]:
        ed.add_lib_symbol(definition)

    props = _definition_properties(definition)
    prefix = "U"
    for p in props:
        if p["name"] == "Reference" and p["value"]:
            prefix = p["value"]
            break
    reference = _str(o, "reference", required=False)
    if reference:
        if ed.find_symbol(reference)[0] is not None:
            raise OpError("reference %r is already used on this sheet"
                          % reference, "REFERENCE_EXISTS")
    else:
        reference = ed.next_reference(prefix)
        # multi-unit parts share one reference; a second unit of an existing part
        # is asked for explicitly by passing `reference`, never auto-assigned.
    overrides = {
        "Reference": reference,
        "Value": o.get("value"),
        "Footprint": o.get("footprint"),
        "Datasheet": o.get("datasheet"),
        "Description": o.get("description"),
    }
    overrides.update(o.get("properties") or {})
    overrides = {k: v for k, v in overrides.items() if v is not None}

    items = [A("symbol"), L(A("lib_id"), A(lib_id, True)),
             at(x, y, rot)]
    if mirror:
        items.append(L(A("mirror"), A(mirror)))
    items += [L(A("unit"), A(str(unit))),
              L(A("exclude_from_sim"), A("no")),
              L(A("in_bom"), A("no" if _bool(o, "exclude_from_bom") else "yes")),
              L(A("on_board"), A("no" if _bool(o, "exclude_from_board") else "yes")),
              L(A("dnp"), A("yes" if _bool(o, "dnp") else "no")),
              ed.sp.id_node(ed.uid())]

    # `property_positions` lets the caller pin a field where the source drawing put
    # it: {name: {x, y, rotation}} (or [x, y]).  Without it every field lands at the
    # library offset rotated by the placement, which is exactly how two copies of the
    # same designator end up stacked on the body when the caller also draws text.
    fpos = _field_positions(o)

    def _placed(name, fallback):
        got = fpos.get(name)
        if got is None:
            return fallback
        return (got[0], got[1], got[2])

    written = []
    seen = set()
    for p in props:
        if p["name"] in seen or (p["name"] or "").startswith("ki_"):
            continue
        seen.add(p["name"])
        value = overrides.get(p["name"], p["value"] or "")
        dx, dy = _rotate(p["x"], p["y"], rot)
        fx, fy, frot = _placed(p["name"], (x + dx, y - dy,
                                           (p["rotation"] - rot) % 360 if p["rotation"] else 0))
        written.append(prop(p["name"], value, fx, fy, frot, hide=p["hide"]))
    for name, value in overrides.items():
        if name in seen:
            continue
        seen.add(name)
        fx, fy, frot = _placed(name, (x, y - 2.54, 0))
        written.append(prop(name, str(value), fx, fy, frot,
                            hide=name in _HIDDEN_FIELDS))
    # Reference and Value must exist even if the library did not declare them
    for name in ("Reference", "Value"):
        if name not in seen:
            written.insert(0, prop(name, overrides.get(name, ""),
                                   x, y - 2.54, 0))
    items.extend(written)

    pins = _pin_numbers(definition)
    for pin in pins:
        items.append(L(A("pin"), A(pin, True), ed.sp.id_node(ed.uid())))

    proj = ed.proj
    items.append(L(A("instances"),
                   L(A("project"), A(proj["project"] or "", True),
                     L(A("path"), A(proj["root_path"], True),
                       L(A("reference"), A(reference, True)),
                       L(A("unit"), A(str(unit)))))))
    node = L(*items)
    ed.add_object(node)
    o["assigned_reference"] = reference
    warnings = []
    if info.get("definition_version") and ed.doc.version() and \
            info["definition_version"] != ed.doc.version():
        warnings.append("the library symbol was written by format %s and this "
                        "schematic by %s; KiCad ignores fields it does not know, so "
                        "the symbol still loads - but the two were not authored "
                        "together" % (info["definition_version"], ed.doc.version()))
    return {"uuid": _id(node), "reference": reference, "lib_id": lib_id,
            "at": [x, y], "rotation": rot, "unit": unit,
            "pins": pins, "fields": [p["name"] for p in props if p["name"]],
            "definition": info,
            "instance_path": proj["root_path"], "project": proj["project"],
            "field_positions": ("translated from the library definition by the "
                                "placement rotation; KiCad's own 'autoplace fields' "
                                "recomputes them on demand"),
            "warnings": warnings}

def _verify_add_symbol(ed, o):
    reference = o.get("assigned_reference") or _str(o, "reference", required=False)
    if not reference:
        return {"ok": False, "error": "no reference to look for"}
    node, entry = ed.find_symbol(reference)
    if node is None:
        return {"ok": False, "error": "reference %r is not on the sheet" % reference}
    x, y = _xy(o)
    problems = []
    if entry["lib_id"] != _str(o, "lib_id"):
        problems.append("lib_id is %r" % entry["lib_id"])
    if entry["x"] is None or abs(entry["x"] - x) > 1e-4 \
            or abs(entry["y"] - y) > 1e-4:
        problems.append("position is %r" % [(entry["x"], entry["y"])])
    definition = ed.doc.lib_symbol(_str(o, "lib_id"))
    if definition is None:
        problems.append("lib_symbols has no entry for the lib_id")
    else:
        want = _pin_numbers(definition)
        if sorted(entry["pins"]) != sorted(want):
            problems.append("pins %r != library %r" % (entry["pins"], want))
    return {"ok": not problems, "problems": problems, "reference": reference,
            "pins": entry["pins"]}

op("add_symbol", ["symbols", "connectivity"],
   "Place a symbol and - when this schematic does not carry it yet - embed its "
   "library definition in `lib_symbols`, then give it a reference and an "
   "`(instances ...)` block. The body is never invented: it comes from this "
   "schematic, from `library` (.kicad_sym) or from `definition` (S-expression).",
   {"lib_id": "\"Device:R\" - the id written into lib_symbols",
    "x": "mm", "y": "mm", "rotation": "0|90|180|270", "unit": "default 1",
    "mirror": "x|y (optional)",
    "reference": "optional; auto-numbered from the library prefix otherwise",
    "value": "optional Value override", "footprint": "optional footprint id",
    "library": "path to a .kicad_sym, required when lib_id is not embedded",
    "symbol": "entry name inside that library (default: after the colon)",
    "definition": "alternative to `library`: raw (symbol \"NAME\" ...) S-expression",
    "properties": "optional {name: value} for extra fields",
    "property_positions": "optional {name: {x, y, rotation}} or {name: [x, y]} "
                          "- global mm; pins a field where the drawing put it instead of the "
                          "library offset",
    "dnp": "bool", "exclude_from_bom": "bool", "exclude_from_board": "bool"},
   _run_add_symbol, _verify_add_symbol)

def _run_set_symbol_field(ed, o, seed):
    reference = _str(o, "reference")
    field = _str(o, "field")
    if o.get("value") is None or not isinstance(o.get("value"), str):
        raise OpError("set_symbol_field needs a string `value` (an empty string is "
                      "allowed but must be explicit)", "ARG_MISSING")
    value = o["value"]
    node, entry = ed.find_symbol(reference)
    if node is None:
        raise OpError("no symbol with reference %r on this sheet" % reference,
                      "REFERENCE_NOT_FOUND")
    for p in S.children(node, "property"):
        if S.arg_text(p, 1) == field:
            atom = p.items[2]
            old = atom.text
            atom.text = value
            atom.quoted = True
            atom.raw = None
            return {"reference": reference, "field": field, "old": old,
                    "new": value}
    at_node = S.child(node, "at")
    x = S.arg_num(at_node, 1, 0.0)
    y = S.arg_num(at_node, 2, 0.0)
    hide = field in _HIDDEN_FIELDS
    S.append_child(node, prop(field, value, x, y - 2.54, 0, hide=hide))
    return {"reference": reference, "field": field, "old": None, "new": value,
            "field_created": True,
            "note": "the field did not exist on this symbol; it was added"}

def _verify_set_symbol_field(ed, o):
    reference = _str(o, "reference")
    field = _str(o, "field")
    value = o.get("value")
    node, _entry = ed.find_symbol(reference)
    if node is None:
        return {"ok": False, "error": "symbol %r disappeared" % reference}
    for p in S.children(node, "property"):
        if S.arg_text(p, 1) == field:
            got = S.arg_text(p, 2)
            return {"ok": got == value, "field": field, "value": got}
    return {"ok": False, "error": "field %r is not on the symbol" % field}

op("set_symbol_field", ["symbols"],
   "Set one field of a placed symbol (Value, Footprint, Datasheet, any custom "
   "field). The field is created if the symbol does not have it.",
   {"reference": "existing reference, e.g. \"R1\"", "field": "field name",
    "value": "new value (may be \"\")"},
   _run_set_symbol_field, _verify_set_symbol_field)

def _run_move_symbol(ed, o, seed):
    reference = _str(o, "reference")
    node, entry = ed.find_symbol(reference)
    if node is None:
        raise OpError("no symbol with reference %r on this sheet" % reference,
                      "REFERENCE_NOT_FOUND")
    x, y = _xy(o)
    rot = _num(o, "rotation", required=False, default=entry["rotation"] or 0)
    old = (entry["x"], entry["y"])
    dx, dy = x - old[0], y - old[1]
    at_node = S.child(node, "at")
    at_node.items[1] = A(num(x))
    at_node.items[2] = A(num(y))
    if len(at_node.items) > 3:
        at_node.items[3] = A(num(rot))
    else:
        at_node.items.append(A(num(rot)))
    moved = 0
    if _bool(o, "move_fields", True):
        for p in S.children(node, "property"):
            a = S.child(p, "at")
            if a is None:
                continue
            a.items[1] = A(num(S.arg_num(a, 1, 0.0) + dx))
            a.items[2] = A(num(S.arg_num(a, 2, 0.0) + dy))
            moved += 1
    return {"reference": reference, "from": list(old), "to": [x, y],
            "rotation": rot, "fields_moved": moved,
            "note": ("fields move with the symbol by the same offset, which is what "
                     "dragging it in KiCad does; they are NOT re-autoplaced")}

def _verify_move_symbol(ed, o):
    reference = _str(o, "reference")
    x, y = _xy(o)
    node, entry = ed.find_symbol(reference)
    if node is None:
        return {"ok": False, "error": "symbol %r disappeared" % reference}
    return {"ok": entry["x"] is not None and abs(entry["x"] - x) < 1e-4
            and abs(entry["y"] - y) < 1e-4,
            "at": [entry["x"], entry["y"]]}

op("move_symbol", ["symbols"],
   "Move (and optionally rotate) a placed symbol; its fields follow by the same "
   "offset unless move_fields=false.",
   {"reference": "existing reference", "x": "mm", "y": "mm",
    "rotation": "0|90|180|270 (default: keep)", "move_fields": "default true"},
   _run_move_symbol, _verify_move_symbol)

def _run_delete_symbol(ed, o, seed):
    reference = _str(o, "reference")
    node, entry = ed.find_symbol(reference)
    if node is None:
        raise OpError("no symbol with reference %r on this sheet" % reference,
                      "REFERENCE_NOT_FOUND")
    uid = _id(node)
    S.remove_child(ed.root, node)
    return {"reference": reference, "uuid": uid, "removed": True,
            "note": ("wires that used to reach its pins are left in place; the "
                     "netlist gate reports what that did to connectivity")}

def _verify_delete_symbol(ed, o):
    reference = _str(o, "reference")
    node, _entry = ed.find_symbol(reference)
    return {"ok": node is None, "reference": reference}

op("delete_symbol", ["symbols", "connectivity"],
   "Remove a placed symbol. Its library definition stays in lib_symbols (KiCad "
   "does the same), so the deletion never touches a shared symbol body.",
   {"reference": "existing reference"},
   _run_delete_symbol, _verify_delete_symbol)

DELETABLE = ("wire", "bus", "bus_entry", "junction", "no_connect", "label",
             "global_label", "hierarchical_label", "text", "text_box",
             "polyline", "rectangle", "netclass_flag", "image", "table")

def _run_delete_object(ed, o, seed):
    uid = _str(o, "uuid")
    head, node = ed.find_by_uuid(uid)
    if node is None:
        raise OpError("no top-level object with uuid %r on this sheet" % uid,
                      "UUID_NOT_FOUND")
    if head not in DELETABLE:
        raise OpError("delete_object refuses to remove a %r node; use delete_symbol "
                      "for a symbol, and sheets/library definitions are not "
                      "deleted by this tool" % (head,), "OP_REFUSED")
    S.remove_child(ed.root, node)
    return {"uuid": uid, "kind": head, "removed": True}

def _verify_delete_object(ed, o):
    uid = _str(o, "uuid")
    head, _node = ed.find_by_uuid(uid)
    return {"ok": head is None, "uuid": uid, "kind": head}

op("delete_object", ["wires", "labels", "text", "symbols"],
   "Remove one top-level object by uuid (wire, bus, junction, label, text, "
   "graphic). Symbols go through delete_symbol; sheets are refused.",
   {"uuid": "the object's uuid, as reported by schematic_view / the plan"},
   _run_delete_object, _verify_delete_object)

def _run_set_symbol_property_position(ed, o, seed):
    reference = _str(o, "reference")
    field = _str(o, "field")
    node, _entry = ed.find_symbol(reference)
    if node is None:
        raise OpError("no symbol with reference %r on this sheet" % reference,
                      "REFERENCE_NOT_FOUND")
    x, y = _xy(o)
    rot = _num(o, "rotation", required=False, default=0)
    for p in S.children(node, "property"):
        if S.arg_text(p, 1) != field:
            continue
        a = S.child(p, "at")
        if a is None:
            S.append_child(p, at(x, y, rot))
        else:
            a.items[1] = A(num(x))
            a.items[2] = A(num(y))
            if len(a.items) > 3:
                a.items[3] = A(num(rot))
            else:
                a.items.append(A(num(rot)))
        return {"reference": reference, "field": field, "at": [x, y],
                "note": "field position only; the symbol itself does not move"}
    raise OpError("field %r is not on symbol %r" % (field, reference),
                  "FIELD_NOT_FOUND")

def _verify_set_field_position(ed, o):
    reference = _str(o, "reference")
    field = _str(o, "field")
    x, y = _xy(o)
    node, _entry = ed.find_symbol(reference)
    if node is None:
        return {"ok": False, "error": "symbol disappeared"}
    for p in S.children(node, "property"):
        if S.arg_text(p, 1) != field:
            continue
        a = S.child(p, "at")
        if a is None:
            return {"ok": False, "error": "field has no (at ...)"}
        return {"ok": abs(S.arg_num(a, 1) - x) < 1e-4
                and abs(S.arg_num(a, 2) - y) < 1e-4,
                "at": [S.arg_num(a, 1), S.arg_num(a, 2)]}
    return {"ok": False, "error": "field gone"}

op("set_field_position", ["symbols"],
   "Move one field of a symbol without moving the symbol (that is how you stop "
   "a value from sitting on top of the body).",
   {"reference": "existing reference", "field": "field name",
    "x": "mm", "y": "mm", "rotation": "default 0"},
   _run_set_symbol_property_position, _verify_set_field_position)

# ---------------------------------------------------------------- sheets
# A `(sheet ...)` node is a *pointer to another file plus the pins it exposes*.
# Everything below therefore does two things at once: it writes the pointer, and
# it says out loud that the other end (the child file, and the hierarchical
# labels inside it that the pins must land on) is a separate object that this
# plan is not touching. The ERC gate is what turns "the pin has no label on the
# other side" from a warning into a refusal, and that is the whole point: a sheet
# pin with nothing behind it looks perfectly drawn.

SHEET_SHAPES = SHAPES       # input/output/bidirectional/tri_state/passive
SHEET_SIDES = ("left", "right", "top", "bottom")

def _sheet_key(o):
    return (_str(o, "sheet_uuid", required=False) or
            _str(o, "sheet", required=False))

def find_sheet(ed, key):
    """(node, facts) for a `(sheet)` node named by uuid or by Sheetname."""
    if not key:
        raise OpError("pass `sheet` (the Sheetname shown on the page) or "
                      "`sheet_uuid`", "ARG_MISSING")
    nodes = ed.doc.items("sheet")
    for node in nodes:
        if _id(node) == key:
            return node, sheet_facts(node)
    hits = [n for n in nodes if sheet_facts(n)["name"] == key]
    if len(hits) > 1:
        raise OpError("%d sheets on this page are called %r; use sheet_uuid "
                      "instead" % (len(hits), key), "SHEET_AMBIGUOUS")
    if not hits:
        names = [sheet_facts(n)["name"] for n in nodes]
        raise OpError("no sheet called %r on this page (sheets here: %s)"
                      % (key, ", ".join(repr(n) for n in names) or "none"),
                      "SHEET_NOT_FOUND")
    return hits[0], sheet_facts(hits[0])

def sheet_facts(node):
    return _hierarchy.sheet_facts(node)

def _sheet_for_verify(ed, o):
    """The sheet a checker should look at, after the plan may have renamed it.

    Verification runs against the *finished* page, so an operation earlier in the
    same plan can have changed this one's discoverable key (a rename makes
    ``sheet="power"`` stale). The uuid assigned while the operation ran is
    therefore tried first, and the name is only the fallback for a checker that
    is handed an operation it never ran.
    """
    uid = o.get("assigned_sheet_uuid") or o.get("sheet_uuid")
    nodes = ed.doc.items("sheet")
    if uid:
        for n in nodes:
            if _id(n) == uid:
                return n, sheet_facts(n)
    return find_sheet(ed, _sheet_key(o))

def _page_tree(ed):
    """Where the page being edited sits inside its project, read from disk.

    `None` means "do not know": the document is in memory, or the file is not
    there yet. The caller then falls back to what the file itself says, which is
    the right answer for a lone page and a guess for a reused one - so it is
    never presented as the tree.
    """
    path = getattr(ed.doc, "path", None)
    if not path or path == "<memory>" or not os.path.isfile(path):
        return None
    try:
        info = _hierarchy.instance_paths(path)
    except Exception:                                  # noqa: BLE001
        return None
    return info if info.get("instances") else None

def _sheet_pages(ed):
    """Every page number already used on this page's own `(sheet)` nodes."""
    out = []
    for node in ed.doc.items("sheet"):
        page = sheet_facts(node)["page"]
        if page:
            out.append(page)
    return out

def _used_pages(ed, tree):
    """Every page number in use: the project's on disk *and* this page's own.

    The union matters inside one plan: a second `add_sheet` sees the first one
    only in memory, while the project-wide numbers come from disk.
    """
    used = set(_sheet_pages(ed))
    if tree:
        used |= set(tree["pages"] or [])
    return used

def _next_page(ed, tree=None):
    """The first page number after everything in use (project-wide if known)."""
    tree = _page_tree(ed) if tree is None else tree
    used = []
    for p in _sheet_pages(ed):
        try:
            used.append(int(p))
        except (TypeError, ValueError):
            pass
    if tree:
        used.append(int(tree["max_page"] or 0))
    si = S.child(ed.root, "sheet_instances")
    if si is not None:
        for path in S.children(si, "path"):
            page = S.arg_text(S.child(path, "page"), 1)
            try:
                used.append(int(page))
            except (TypeError, ValueError):
                pass
    return str(max(used) + 1 if used else 2)

def _sheet_node(ed, o, seed):
    name = _str(o, "name")
    sheetfile = _str(o, "file")
    if not sheetfile.lower().endswith(".kicad_sch"):
        raise OpError("Sheetfile must name a .kicad_sch file (got %r): a sheet "
                      "points at a schematic, and KiCad will refuse anything else"
                      % sheetfile, "ARG_INVALID")
    if os.path.basename(sheetfile) != sheetfile:
        raise OpError("Sheetfile must be a bare file name such as 'power.kicad_sch' "
                      "(got %r). A path with directories makes the sheet point "
                      "outside the folder the project is saved in, which this "
                      "server cannot verify - move the file or keep the tree flat"
                      % sheetfile, "ARG_INVALID")
    x, y = _xy(o)
    w = _num(o, "width", required=False, default=40.64)
    h = _num(o, "height", required=False, default=25.4)
    if w <= 0 or h <= 0:
        raise OpError("width/height must be positive (got %s x %s)" % (w, h),
                      "ARG_RANGE")
    # a page number is a label, not a quantity: KiCad stores "2", an agent will
    # send 2, and both mean the same page - so an int is accepted rather than
    # refused as "must be a string"
    tree = _page_tree(ed)
    proj = ed.proj
    if tree:
        # pages are numbered once per project and globally, and a sheet node
        # carries one `(path ... (page ...))` entry per place its parent page is
        # instantiated: the same node is read once per instance, so a single
        # entry would give every instance the same page number
        spots = [i["path"] for i in tree["instances"] if i["path"]] or [
            proj["root_path"]]
    else:
        spots = [proj["root_path"]]
    raw_page = o.get("page")
    asked = None
    if isinstance(raw_page, int) and not isinstance(raw_page, bool):
        asked = str(raw_page)
    elif raw_page is not None:
        asked = _str(o, "page")
    if asked is not None and len(spots) > 1:
        raise OpError("this page is instantiated %d times, so a sheet added to it "
                      "needs %d page numbers, not one - leave `page` out and the "
                      "numbers after the highest one in use are assigned"
                      % (len(spots), len(spots)), "ARG_INVALID")
    start = int(asked) if asked is not None else int(_next_page(ed, tree))
    pages = [str(start + i) for i in range(len(spots))]
    used = _used_pages(ed, tree)
    for p in pages:
        if p in used:
            raise OpError("page %r is already in use in this project (in use: %s)"
                          % (p, ", ".join(sorted(used)) or "none"), "PAGE_EXISTS")
    uid = ed.uid()
    items = [A("sheet"),
             at(x, y),
             L(A("size"), A(num(w)), A(num(h))),
             L(A("exclude_from_sim"), A("no")),
             L(A("in_bom"), A("yes")),
             L(A("on_board"), A("yes")),
             L(A("dnp"), A("no")),
             L(A("fields_autoplaced"), A("yes")),
             stroke(0, "solid"),
             L(A("fill"), L(A("color"), A("0"), A("0"), A("0"), A("0.0000"))),
             ed.sp.id_node(uid),
             prop("Sheetname", name, x, y - 0.7625, 0, size=1.524,
                  justify="left bottom"),
             prop("Sheetfile", sheetfile, x, y + h + 0.6099, 0, size=1.524,
                  justify="left top")]
    written_pins = []
    pins = []
    for pin in o.get("pins") or []:
        node_pin, pin_name = _pin_node(ed, pin, x, y, w, h)
        written_pins.append(node_pin)
        pins.append(pin_name)
    items.extend(written_pins)
    items.append(L(A("instances"),
                   L(A("project"), A(proj["project"] or "", True),
                     *[L(A("path"), A(spot, True),
                         L(A("page"), A(pg, True)))
                       for spot, pg in zip(spots, pages)])))
    node = L(*items)
    ed.add_object(node)
    o["assigned_sheet_uuid"] = uid
    return node, {"uuid": uid, "name": name, "file": sheetfile,
                  "page": pages[0], "pages": list(pages),
                  "at": [x, y], "size": [w, h],
                  "pins": pins,
                  "instance_paths": list(spots),
                  "instance_path": spots[0],
                  "project": proj["project"]}

def _pin_node(ed, pin, x, y, w, h):
    if isinstance(pin, str):
        pin = {"name": pin}
    if not isinstance(pin, dict):
        raise OpError("each pin must be a name or {\"name\": ..., \"shape\": ...}",
                      "ARG_INVALID")
    name = pin.get("name")
    if not name:
        raise OpError("a sheet pin needs a name", "ARG_MISSING")
    shape = pin.get("shape") or "input"
    if shape not in SHEET_SHAPES:
        raise OpError("pin shape must be one of %s (got %r)"
                      % (", ".join(SHEET_SHAPES), shape), "ARG_INVALID")
    side = pin.get("side") or "right"
    if side not in SHEET_SIDES:
        raise OpError("pin side must be one of %s (got %r)"
                      % (", ".join(SHEET_SIDES), side), "ARG_INVALID")
    offset = pin.get("offset")
    # KiCad puts pins on the box edge; which edge decides both the coordinate
    # and the label justification, so the side is asked for rather than derived
    # from a guessed coordinate.
    span = w if side in ("left", "right") else h
    if offset is None:
        offset = span / 2.0
    offset = float(offset)
    if not (0 < offset < span):
        raise OpError("pin offset %s is outside the %s edge (0..%s)"
                      % (offset, side, span), "ARG_RANGE")
    if side == "left":
        px, py, rot, justify = x, y + offset, 180, "right"
    elif side == "right":
        px, py, rot, justify = x + w, y + offset, 0, "left"
    elif side == "top":
        px, py, rot, justify = x + offset, y, 90, "left"
    else:
        px, py, rot, justify = x + offset, y + h, 270, "left"
    node = L(A("pin"), A(name, True), A(shape), at(px, py, rot),
             ed.sp.id_node(ed.uid()),
             effects(size=1.27, justify=justify))
    return node, name

def _run_add_sheet(ed, o, seed):
    node, info = _sheet_node(ed, o, seed)
    detail = dict(info)
    detail["child_file_exists"] = os.path.isfile(
        os.path.join(os.path.dirname(os.path.abspath(ed.doc.path or ".")),
                     info["file"]))
    detail["not_touched"] = ("the child file and its hierarchical labels are NOT "
                             "part of this plan; a pin whose label is missing "
                             "inside %r shows up as an ERC violation, and the ERC "
                             "gate refuses the edit" % info["file"])
    return detail

def _later_sets(o, field):
    """Does an operation *after* this one in the same plan rewrite `field`?

    A postcondition is read off the finished page, so "add a sheet, then rename
    it" and "add a sheet, forget to rename it" look identical at the end. Rather
    than weaken the check, the checker is told which later operation is entitled
    to override it - anything else still fails.
    """
    for later in o.get("_verify_later") or []:
        if later.get("op") == "set_sheet_field" and later.get("field") == field:
            return True
    return False

def _verify_add_sheet(ed, o):
    name = _str(o, "name")
    uid = o.get("assigned_sheet_uuid")
    hits = [n for n in ed.doc.items("sheet") if _id(n) == uid] if uid else []
    if not hits:
        hits = [n for n in ed.doc.items("sheet") if sheet_facts(n)["name"] == name]
    if not hits:
        return {"ok": False, "error": "no sheet called %r after the edit" % name}
    facts = sheet_facts(hits[0])
    x, y = _xy(o)
    name_ok = facts["name"] == name or _later_sets(o, "Sheetname")
    file_ok = facts["file"] == _str(o, "file") or _later_sets(o, "Sheetfile")
    placed = (facts["x"] is not None and abs(facts["x"] - x) < 1e-4
              and facts["y"] is not None and abs(facts["y"] - y) < 1e-4)
    return {"ok": bool(name_ok and file_ok and placed), "sheet": facts["name"],
            "file": facts["file"], "page": facts["page"],
            "at": [facts["x"], facts["y"]],
            "name_changed_later": not name_ok or bool(_later_sets(o, "Sheetname")
                                                     and facts["name"] != name),
            "file_changed_later": not file_ok or bool(_later_sets(o, "Sheetfile")
                                                      and facts["file"] != _str(o, "file"))}

op("add_sheet",
   ["sheets", "hierarchy"],
   "Place a child sheet: a (sheet) box on this page that points at another "
   ".kicad_sch file, with the pins this page exposes to it. The child file is "
   "NOT created or edited here - if it does not exist KiCad's netlister will say "
   "so, and if a pin has no matching hierarchical label inside it ERC will say "
   "so (and the ERC gate will refuse the write).",
   {"name": "Sheetname shown on this page",
    "file": "bare .kicad_sch file name",
    "x": "mm", "y": "mm",
    "width": "default 40.64", "height": "default 25.4",
    "page": "page number; default = highest in use + 1",
    "pins": "[{\"name\": ..., \"shape\": input|output|bidirectional|tri_state|"
            "passive, \"side\": left|right|top|bottom, \"offset\": mm}]"},
   _run_add_sheet, _verify_add_sheet)

def _run_add_sheet_pin(ed, o, seed):
    key = _sheet_key(o)
    node, facts = find_sheet(ed, key)
    if facts["x"] is None or facts["width"] is None:
        raise OpError("sheet %r has no (at ...)/(size ...) to put a pin on"
                      % facts["name"], "SHEET_MALFORMED")
    pin, _pin_name = _pin_node(ed, o, facts["x"], facts["y"],
                               facts["width"], facts["height"])
    o["assigned_sheet_uuid"] = facts["uuid"]
    o["assigned_pin_uuid"] = _id(pin)
    pins = S.child(node, "pin")
    # pins live after the properties and before (instances ...); appending is
    # what KiCad does when it adds one, and keeps (instances ...) last
    idx = len(node.items)
    for i, item in enumerate(node.items):
        if isinstance(item, S.Seq) and S.head(item) == "instances":
            idx = i
            break
    S.insert_child(node, idx, pin)
    return {"sheet": facts["name"], "sheet_uuid": facts["uuid"],
            "pin": S.arg_text(pin, 1), "shape": S.arg_text(pin, 2),
            "at": [S.arg_num(S.child(pin, "at"), 1), S.arg_num(S.child(pin, "at"), 2)],
            "uuid": _id(pin),
            "not_touched": "the hierarchical label inside %r is what this pin must "
                           "land on; adding the pin alone is an ERC violation"
                           % facts["file"]}

def _verify_add_sheet_pin(ed, o):
    try:
        node, facts = _sheet_for_verify(ed, o)
    except OpError as exc:
        return {"ok": False, "error": str(exc)}
    pin_uuid = o.get("pin_uuid") or o.get("assigned_pin_uuid")
    names = [S.arg_text(p, 1) for p in S.children(node, "pin")]
    if pin_uuid:
        return {"ok": pin_uuid in [_id(p) for p in S.children(node, "pin")],
                "sheet": facts["name"], "pins": sorted(names)}
    want = _str(o, "name", required=False)
    return {"ok": want in names, "sheet": facts["name"], "pins": sorted(names)}

op("add_sheet_pin",
   ["sheets", "connectivity"],
   "Add one pin to an existing sheet box. The pin is a promise that the child "
   "file has a hierarchical label of the same name - this operation does not go "
   "and add it",
   {"sheet": "Sheetname on this page (or sheet_uuid)",
    "name": "pin name, must match a hierarchical label in the child",
    "shape": "input|output|bidirectional|tri_state|passive (default input)",
    "side": "left|right|top|bottom (default right)",
    "offset": "mm along that edge; default the middle"},
   _run_add_sheet_pin, _verify_add_sheet_pin)

def _run_set_sheet_field(ed, o, seed):
    key = _sheet_key(o)
    node, facts = find_sheet(ed, key)
    field = _str(o, "field")
    if field not in ("Sheetname", "Sheetfile"):
        raise OpError("only Sheetname and Sheetfile are settable here (got %r); a "
                      "sheet's other properties are geometry-owned" % field,
                      "ARG_INVALID")
    value = _str(o, "value")
    if field == "Sheetfile":
        if not value.lower().endswith(".kicad_sch"):
            raise OpError("Sheetfile must name a .kicad_sch file (got %r)"
                          % value, "ARG_INVALID")
        if os.path.basename(value) != value:
            raise OpError("Sheetfile must be a bare file name (got %r)" % value,
                          "ARG_INVALID")
        if value == facts["file"]:
            raise OpError("sheet %r already points at %r - nothing to change"
                          % (facts["name"], value), "OP_NOOP")
    elif value == facts["name"]:
        raise OpError("sheet is already called %r - nothing to change" % value,
                      "OP_NOOP")
    done = False
    for p in S.children(node, "property"):
        if S.arg_text(p, 1) != field:
            continue
        p.items[2].text = value
        p.items[2].quoted = True
        done = True
    if not done:
        raise OpError("sheet %r has no %s property to change"
                      % (facts["name"], field), "FIELD_NOT_FOUND")
    o["assigned_sheet_uuid"] = facts["uuid"]
    out = {"sheet_uuid": facts["uuid"], "field": field, "value": value,
           "was": facts["name"] if field == "Sheetname" else facts["file"]}
    if field == "Sheetfile":
        out["note"] = ("repointing a sheet means the project's netlist is now read "
                       "from a different file; the netlist gate compares before and "
                       "after, so a mismatch is visible rather than assumed")
    return out

def _verify_set_sheet_field(ed, o):
    try:
        node, facts = _sheet_for_verify(ed, o)
    except OpError as exc:
        return {"ok": False, "error": str(exc)}
    field = _str(o, "field")
    have = facts["name"] if field == "Sheetname" else facts["file"]
    return {"ok": have == _str(o, "value"), "field": field, "value": have}

op("set_sheet_field",
   ["sheets", "hierarchy"],
   "Rename a sheet box (Sheetname) or point it at a different child file "
   "(Sheetfile). Changing Sheetfile changes what the whole project's netlist is "
   "read from, so the netlist gate is what judges it",
   {"sheet": "Sheetname on this page (or sheet_uuid)",
    "field": "Sheetname | Sheetfile", "value": "new text"},
   _run_set_sheet_field, _verify_set_sheet_field)

def _run_delete_sheet(ed, o, seed):
    key = _sheet_key(o)
    node, facts = find_sheet(ed, key)
    others = [sheet_facts(n) for n in ed.doc.items("sheet") if n is not node
              and sheet_facts(n)["file"] == facts["file"]]
    S.remove_child(ed.root, node)
    o["assigned_sheet_uuid"] = facts["uuid"]
    return {"sheet_uuid": facts["uuid"], "name": facts["name"],
            "file": facts["file"], "page": facts["page"], "removed": True,
            "file_kept": "the child file %r is still on disk - this operation never "
                         "deletes a file" % facts["file"],
            "other_sheets_using_it": [s["name"] for s in others],
            "orphaned": not others}

def _verify_delete_sheet(ed, o):
    uid = o.get("assigned_sheet_uuid") or o.get("sheet_uuid") or _sheet_key(o)
    nodes = ed.doc.items("sheet")
    for node in nodes:
        if _id(node) == uid:
            return {"ok": False, "error": "the sheet is still there"}
    return {"ok": True, "remaining": [sheet_facts(n)["name"] for n in nodes]}

op("delete_sheet",
   ["sheets", "hierarchy"],
   "Remove a sheet box from this page. The child file stays on disk (this tool "
   "never deletes files); if no other sheet points at it, the answer says the "
   "file is now unreferenced",
   {"sheet": "Sheetname on this page (or sheet_uuid)"},
   _run_delete_sheet, _verify_delete_sheet)

# ---------------------------------------------------------------- driver
def validate(o):
    if not isinstance(o, dict):
        raise OpError("each operation must be an object, got %r" % (o,), "ARG_INVALID")
    name = o.get("op")
    if not name or name not in OPS:
        raise OpError("unknown schematic op %r (known: %s)"
                      % (name, ", ".join(sorted(OPS))), "OP_UNKNOWN")
    return name

def run_all(doc, ops, seed_base="schop", index_offset=0):
    """Run every operation on a private copy. All-or-nothing, like the board path.

    ``index_offset`` keeps the generated ids a function of (seed_base, absolute
    operation index), so a stepped write reproduces a one-shot write exactly.
    """
    ed = SchEditor(doc)
    records = []
    for idx, o in enumerate(ops):
        name = validate(o)
        ed.seed = "%s:%d" % (seed_base, idx + index_offset)
        # The per-object counter has to restart with the seed, otherwise an
        # id depends on how many objects earlier operations made in the same
        # call - and a stepped write (one operation per call) would then
        # produce different uuids than the one-shot apply it must equal.
        ed._n = 0
        try:
            detail = OPS[name]["run"](ed, o, ed.seed) or {}
        except OpError as exc:
            exc.extra.setdefault("op_index", idx)
            exc.extra.setdefault("op", name)
            raise
        records.append({"index": idx, "op": name,
                        "affects": list(OPS[name]["affects"]),
                        "detail": detail})
    return records

def verify_all(doc, ops):
    ed = SchEditor(doc)
    out = []
    ok = True
    for idx, o in enumerate(ops):
        name = validate(o)
        if isinstance(o, dict):
            o["_verify_later"] = ops[idx + 1:]
        try:
            res = OPS[name]["verify"](ed, o) or {"ok": False}
        except OpError as exc:
            res = {"ok": False, "error": "%s: %s" % (exc.code, exc)}
        except Exception as exc:                     # noqa: BLE001
            res = {"ok": False, "error": "%s: %s" % (type(exc).__name__, exc)}
        if not res.get("ok"):
            ok = False
        out.append({"index": idx, "op": name, "verified": bool(res.get("ok")),
                    "detail": res})
    return {"all_verified": ok, "operations": out}

def affects(ops):
    out = set()
    for o in ops:
        out |= set(OPS[validate(o)]["affects"])
    return sorted(out)

# ---------------------------------------------------------------- page sanity
PAPER_MM = {
    "A5": (210, 148), "A4": (297, 210), "A3": (420, 297), "A2": (594, 420),
    "A1": (841, 594), "A0": (1189, 841),
    "A": (279.4, 215.9), "B": (431.8, 279.4), "C": (558.8, 431.8),
    "D": (863.6, 558.8), "E": (1117.6, 863.6),
    "USLetter": (279.4, 215.9), "USLegal": (355.6, 215.9), "USLedger": (431.8, 279.4),
    "User": None,
}

def page_box(doc, margin=0.0):
    """(w, h) of the sheet in mm, or None when the paper size is unknown."""
    paper = doc.paper() or {}
    size = paper.get("size")
    box = PAPER_MM.get(size) if size else None
    if not box:
        return None
    w, h = box
    if paper.get("portrait"):
        w, h = h, w
    return (w - 2 * margin, h - 2 * margin)
