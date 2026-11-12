"""Node construction for the write path.

Three rules, and every one of them exists because the opposite fails silently:

1. **Coordinate frame at the boundary only.** Ops speak the *same* frame that
   `board_view` reports: millimetres with **Y up**. The file stores Y down. The
   negation happens here, once, in `y_file()`. If a sign flipped anywhere else
   the result would still be a valid board - just mirrored - and DRC would not
   notice.

2. **Version-aware spelling.** KiCad 7 writes `(tstamp <uuid>)` (bare atom) and
   v8+ writes `(uuid "<uuid>")` (quoted). Writing the wrong one is not a syntax
   error, it is an *ignored* field, which then makes every object look
   identical to KiCad's deduplicator.

3. **Units printed the way KiCad prints them.** `1e-3` is valid S-expression and
   valid float, but no KiCad version writes it; keeping to plain decimal means a
   diff of the file shows only what actually changed.
"""
import uuid as _uuid

from ..fidelity import sexpr as S

# KiCad switched tstamp -> uuid (and made it quoted) with the v8 format.
V8_VERSION = 20240000

def num(value):
    """Plain-decimal millimetre string, no exponent, no trailing zeros."""
    v = float(value)
    if v != v or v in (float("inf"), float("-inf")):
        raise ValueError("not a finite number: %r" % (value,))
    s = "%.6f" % v
    if "." in s:
        s = s.rstrip("0").rstrip(".")
    if s in ("-0", "", "-"):
        s = "0"
    return s

def y_file(y_up):
    """View frame (Y up) -> file frame (Y down). Exact negation, no rounding."""
    return -float(y_up)

def new_id(seed=None):
    """A fresh uuid4, or a deterministic one when a seed is given (tests)."""
    if seed is None:
        return str(_uuid.uuid4())
    return str(_uuid.uuid5(_uuid.NAMESPACE_URL, "pcb-agent-mcp:%s" % (seed,)))

class Spelling(object):
    """How one board file spells an object id."""

    def __init__(self, version):
        try:
            self.version = int(version)
        except (TypeError, ValueError):
            self.version = 0
        self.modern = self.version >= V8_VERSION

    def id_node(self, uid):
        if self.modern:
            return S.Seq([S.Atom("uuid"), S.Atom(uid, True)])
        return S.Seq([S.Atom("tstamp"), S.Atom(uid)])

    def id_of(self, node):
        """Read the object id out of a node, whichever spelling it uses."""
        for name in ("tstamp", "uuid"):
            n = S.child(node, name)
            if n is not None:
                v = S.arg(n, 1)
                if isinstance(v, S.Atom):
                    return v.text
        return None

# --------------------------------------------------------------- builders
def A(text, quoted=False):
    return S.Atom(text, quoted)

def L(*items):
    return S.Seq(list(items))

def xy(x, y_up):
    """`(xy X Y)` in file coordinates."""
    return L(A("xy"), A(num(x)), A(num(y_file(y_up))))

def pts(points):
    return L(A("pts"), *[xy(x, y) for x, y in points])

def at(x, y_up, rot=None):
    items = [A("at"), A(num(x)), A(num(y_file(y_up)))]
    if rot is not None:
        items.append(A(num(rot)))
    return L(*items)

def size(w, h):
    return L(A("size"), A(num(w)), A(num(h)))

def line2(kind, x1, y1, x2, y2, layer, width, sp, uid, extra=()):
    return L(A(kind), L(A("start"), A(num(x1)), A(num(y_file(y1)))),
             L(A("end"), A(num(x2)), A(num(y_file(y2)))),
             L(A("layer"), A(layer, True)), L(A("width"), A(num(width))),
             sp.id_node(uid), *extra)

def arc3(kind, sx, sy, mx, my, ex, ey, layer, width, sp, uid):
    return L(A(kind),
             L(A("start"), A(num(sx)), A(num(y_file(sy)))),
             L(A("mid"), A(num(mx)), A(num(y_file(my)))),
             L(A("end"), A(num(ex)), A(num(y_file(ey)))),
             L(A("layer"), A(layer, True)), L(A("width"), A(num(width))),
             sp.id_node(uid))

def rect_as_lines(x0, y0, x1, y1, layer, width, sp, seed):
    """KiCad has `gr_rect`, but a rectangular outline made of 4 segments is the
    form every version reads identically and the form that can be edited one
    edge at a time. Returns the 4 nodes."""
    x0, x1 = sorted((float(x0), float(x1)))
    y0, y1 = sorted((float(y0), float(y1)))
    corners = [(x0, y0), (x1, y0), (x1, y1), (x0, y1)]
    out = []
    for i in range(4):
        (ax, ay), (bx, by) = corners[i], corners[(i + 1) % 4]
        out.append(line2("gr_line", ax, ay, bx, by, layer, width, sp,
                         new_id("%s:%d" % (seed, i))))
    return out

def rounded_rect(x0, y0, x1, y1, r, layer, width, sp, seed):
    """An outline with four equal corner radii: 4 straight edges + 4 arcs.

    A router-cut board has radii at the corners; it is not four sharp corners.
    The arcs are OUTLINE geometry (Edge.Cuts), not copper - the 45-degree rule
    that bans arcs is about traces. Each arc's mid point is on the 45-degree
    diagonal of its corner, which is the definition of a quarter circle and the
    only mid point KiCad will accept for one.
    """
    x0, x1 = sorted((float(x0), float(x1)))
    y0, y1 = sorted((float(y0), float(y1)))
    r = float(r)
    d = r * 0.7071067811865476
    out = [
        line2("gr_line", x0 + r, y0, x1 - r, y0, layer, width, sp, seed + ":s0"),
        arc3("gr_arc", x1 - r, y0, x1 - r + d, y0 + d, x1, y0 + r,
             layer, width, sp, seed + ":a1"),
        line2("gr_line", x1, y0 + r, x1, y1 - r, layer, width, sp, seed + ":s1"),
        arc3("gr_arc", x1, y1 - r, x1 - r + d, y1 - r + d, x1 - r, y1,
             layer, width, sp, seed + ":a2"),
        line2("gr_line", x1 - r, y1, x0 + r, y1, layer, width, sp, seed + ":s2"),
        arc3("gr_arc", x0 + r, y1, x0 + r - d, y1 - r + d, x0, y1 - r,
             layer, width, sp, seed + ":a3"),
        line2("gr_line", x0, y1 - r, x0, y0 + r, layer, width, sp, seed + ":s3"),
        arc3("gr_arc", x0, y0 + r, x0 + r - d, y0 + r - d, x0 + r, y0,
             layer, width, sp, seed + ":a4"),
    ]
    return out

def text_node(text, x, y_up, layer, height, thickness, sp, uid, rot=None):
    return L(A("gr_text"), A(text, True), at(x, y_up, rot),
             L(A("layer"), A(layer, True)), sp.id_node(uid),
             L(A("effects"), L(A("font"), size(height, height),
                               L(A("thickness"), A(num(thickness))))))

def segment(x1, y1, x2, y2, width, layer, net_code, sp, uid):
    return L(A("segment"),
             L(A("start"), A(num(x1)), A(num(y_file(y1)))),
             L(A("end"), A(num(x2)), A(num(y_file(y2)))),
             L(A("width"), A(num(width))),
             L(A("layer"), A(layer, True)),
             L(A("net"), A(str(net_code))),
             sp.id_node(uid))

def via(x, y_up, dia, drill, net_code, sp, uid, layers=("F.Cu", "B.Cu")):
    return L(A("via"), at(x, y_up),
             L(A("size"), A(num(dia))), L(A("drill"), A(num(drill))),
             L(A("layers"), *[A(l, True) for l in layers]),
             L(A("net"), A(str(net_code))),
             sp.id_node(uid))

def zone_polygon_conf(net_code, net_name, layer, points, sp, uid,
                      clearance=0.2, min_thickness=0.25, priority=None,
                      hatch=0.5, keepout=None, pad_connection=None,
                      thermal_gap=None, thermal_bridge_width=None, holes=None):
    """A zone: keepout when `keepout` is given, copper pour otherwise.

    A pour written this way carries `(fill yes)` but no `filled_polygon`: the
    fill result is computed by the vendor engine when it opens or plots the
    board, and this server refuses to invent one. Callers are told that with
    `fill_pending`.

    The header is written the way KiCad 10.0.7 writes one (`SaveBoard` on a zone
    whose defaults were not touched was read back token by token):

        (connect_pads [yes|no|thru_hole_only] (clearance X))
        (min_thickness T)
        (filled_areas_thickness no)
        (fill yes [(thermal_gap G)] [(thermal_bridge_width W)])

    Three of those are not decoration:

    * `filled_areas_thickness no` says the stored `filled_polygon` IS the copper
      edge. Every board this server writes declares format 20221018, and a
      pre-v7 zone without that token is read as the CENTRELINE of a
      min_thickness-wide stroke - KiCad 10 DRC then measures `min_thickness/2`
      further out than the copper is. Measured on a real 199 KB board: the same
      polygons gave 386 clearance ERRORS without the token and 0 with it.
    * the mode word is written only when `pad_connection` says so, because that
      is what KiCad does: a thermal relief is spelled by writing NOTHING (`yes`
      is solid copper, `no` is no connection - verified by loading each spelling
      in pcbnew and reading `GetPadConnection()` back).
    * `thermal_gap` / `thermal_bridge_width` are written ONLY when the caller
      gave them. Left out, KiCad applies its own defaults (measured: 0.5 mm /
      0.5 mm), so this function never invents a number a caller did not ask for.
    """
    if pad_connection not in (None, "thermal", "solid", "yes", "none", "no",
                              "thru_hole_only"):
        raise ValueError("pad_connection %r is not thermal/solid/none/thru_hole_only"
                         % (pad_connection,))
    items = [A("zone"), L(A("net"), A(str(net_code))),
             L(A("net_name"), A(net_name, True)), L(A("layer"), A(layer, True))]
    if priority is not None:
        items.append(L(A("priority"), A(str(int(priority)))))
    items.append(sp.id_node(uid))
    items.append(L(A("hatch"), A("edge"), A(num(hatch))))
    if keepout is not None:
        # Every rule flag has to be spelled out, allowed ones included. KiCad reads
        # a MISSING flag as not_allowed (its parser defaults the whole mask to
        # "prohibit" and then clears what it finds in the file), so writing only
        # `(copperpour not_allowed)` really means "forbid tracks, vias and pads
        # too". Measured on the 96-footprint clone board: 14 rule areas written
        # the short way produced 199 `items_not_allowed` errors on ordinary
        # tracks; the same 14 areas with the four `allowed` flags explicit gave 0.
        _flags = ("tracks", "vias", "pads", "copperpour", "footprints")
        items.append(L(A("keepout"),
                       *[L(A(rule), A("not_allowed" if rule in keepout else "allowed"))
                         for rule in _flags]))
    else:
        # a thermal relief is spelled by writing no mode word at all
        words = {"solid": "yes", "yes": "yes", "none": "no", "no": "no",
                 "thru_hole_only": "thru_hole_only", "thermal": None, None: None}
        mode = words.get(pad_connection)
        head = [A("connect_pads")] + ([A(mode)] if mode else [])
        head.append(L(A("clearance"), A(num(clearance))))
        items.append(L(*head))
        items.append(L(A("min_thickness"), A(num(min_thickness))))
        # see the docstring: without this a pre-v7 zone's polygons are stroked
        items.append(L(A("filled_areas_thickness"), A("no")))
        fill = [A("fill"), A("yes")]
        if thermal_gap is not None:
            fill.append(L(A("thermal_gap"), A(num(thermal_gap))))
        if thermal_bridge_width is not None:
            fill.append(L(A("thermal_bridge_width"), A(num(thermal_bridge_width))))
        items.append(L(*fill))
    items.append(L(A("polygon"), pts(points)))
    # Holes are extra `(polygon ...)` nodes: KiCad writes the first polygon as the
    # outline and every further one as a hole (read back with `pcbnew` on a zone
    # that pcbnew itself had saved). A hole is how a rule area can leave copper
    # alone in a spot inside the area - a pour zone that carries its own hand-drawn
    # copper needs that, because a keepout would otherwise clip the pour to zero.
    for ring in (holes or []):
        items.append(L(A("polygon"), pts(ring)))
    return L(*items)

def netclass(name, params, sp=None):
    """`(net_class ...)` with the caller's spelling of each knob kept."""
    items = [A("net_class"), A(name, True), A("", True)]
    for key, value in params:
        items.append(L(A(key), A(num(value))))
    return L(*items)
