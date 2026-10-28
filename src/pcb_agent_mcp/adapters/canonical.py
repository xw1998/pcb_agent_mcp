"""Canonical board model: the single frame every adapter must land in.

Two rules that the whole architecture depends on (see docs/07 §3):

C1  Unit and axis normalisation happens ONLY at the adapter boundary. Inside the
    canonical frame there is exactly one unit (mm) and one Y direction (up).
    Nothing downstream may see `0.01 inch`, `ticket`, `.epcb` or a negated Y.

C2  A fact that one format does not carry is declared, never filled with the
    other backend's default. Every board carries `present` (facts that were
    actually read) and `absent` (facts the format does not store, with why).

The canonical model keeps a *convenience view*: a hand-picked, normalised subset
that is easy to measure on. It is NOT the truth about the board - `fidelity/`
holds the whole document and is the layer that may claim losslessness. Anything
this view leaves out is still in the fidelity tree and reachable through
`board_view`, so the view may be incomplete without the system being lossy.

Continuous quantities (clearance, loop area, occupancy) are computed by the
Oracle on demand and are marked `authoritative: false`: they are this server's
measurement, not a verdict. KiCad's own engine has the verdict (`board_drc`).
"""

import math

SCHEMA = 1

# Fact keys used in `present` / in `absent[].fact`. Keeping them as a closed set
# means a missing key is detectable: a new fact must be added here too.
FACT_KEYS = (
    "outline",            # board edge geometry
    "cutouts",            # internal slots / holes in the outline
    "footprints",         # placed components
    "pads",               # pad geometry + net assignment
    "free_pads",          # pads placed as board objects (not part of a footprint)
    "tracks",             # routed copper as line segments
    "vias",               # layer transitions
    "zones",              # pours / keepouts
    "zone_fill",          # actual filled copper (not just the outline)
    "nets",               # net names / codes
    "stackup",            # layer order, thickness, er, tand, copper weight
    "design_rules",       # clearance matrix, track widths (often in a side file)
    "pad_to_mask",        # solder mask expansion
    "component_3d",       # height / courtyard information
)


class Board(object):
    """Mutable container filled by an adapter. Units: mm, Y up, degrees CCW."""

    def __init__(self, fmt, path):
        self.source = {
            "format": fmt,
            "path": path,
            "schema": SCHEMA,
            "unit_canonical": "mm",
            "y_axis_canonical": "up",
        }
        self.outline = []          # [ [(x,y), ...] ] one polygon per contour, outer first
        self.cutouts = []          # [ [(x,y), ...] ]
        # Edge chains that do NOT close into a contour. A board edge drawn by
        # hand can be a few tenths of a mil off; fitting it into a polygon would
        # report an outline the file does not contain, so the segments are kept
        # as they are and the fact is declared (see `source.outline_note`).
        self.outline_open = []
        self.footprints = []       # [{ref, value, lib_id, x, y, rotation, layer, bbox, pads}]
        self.tracks = []           # [{net, layer, width, start, end}]
        self.vias = []             # [{net, x, y, drill, diameter, from, to, kind}]
        self.graphics = []         # board-level copper graphics (gr_poly/gr_line on *.Cu)
        self.zones = []            # [{net, layer, outline, fill_mode, priority, keepout, points}]
        self.nets = {}             # code -> name
        self.stackup = []          # [{name, type, thickness_mm, er, tand, copper_um}]
        self.rules = {}            # {"clearance": {...}, "track_width": {...}, "source": ...}
        self.thickness_mm = None
        self.present = {}
        self.absent = []           # [{fact, reason}]

    # -------------------------------------------------------------- facts
    def mark(self, fact, ok_flag=True, note=None):
        """Record whether a fact was actually read out of the file."""
        if fact not in FACT_KEYS:
            raise ValueError("unknown fact key: %s" % fact)
        entry = {"present": bool(ok_flag)}
        if note:
            entry["note"] = note
        self.present[fact] = entry

    def declare_absent(self, fact, reason):
        """Declare a fact the format does not carry. Never invent a default."""
        self.absent.append({"fact": fact, "reason": reason})

    def pads(self):
        out = []
        for fp in self.footprints:
            out.extend(fp.get("pads") or [])
        return out

    def resolve_nets(self):
        """Normalise every item's `net` to a net NAME.

        Formats store a net *code* on copper items and a *name* in the net table
        (and some store the name on pads only). Leaving both in the model means
        two spellings of the same net walk downstream, and every Oracle query
        that compares nets silently answers "no geometry on that net". So the
        code->name resolution happens once, here, at the adapter boundary.
        """
        for it in list(self.tracks) + list(self.vias) + list(self.zones) + self.pads():
            n = it.get("net")
            if n is not None:
                it["net"] = self.net_name(n)
        return self

    def net_name(self, code):
        """Code -> name. Accepts both spellings of a code (2 and "2")."""
        if code is None:
            return None
        if code in self.nets:
            return self.nets[code]
        try:
            k = int(code)
        except (TypeError, ValueError):
            return str(code)
        return self.nets.get(k, str(code))

    def counts(self):
        return {
            "footprints": len(self.footprints),
            "pads": len(self.pads()),
            "tracks": len(self.tracks),
            "vias": len(self.vias),
            "zones": len(self.zones),
            "copper_graphics": len(self.graphics) + sum(
                len(f.get("graphics") or []) for f in self.footprints),
            "nets": len(self.nets),
            "outline_contours": len(self.outline),
            "outline_open_chains": len(self.outline_open),
            "cutouts": len(self.cutouts),
            "stackup_layers": len(self.stackup),
        }

    def as_dict(self, items=False):
        out = {
            "source": self.source,
            "thickness_mm": self.thickness_mm,
            "counts": self.counts(),
            "present": self.present,
            "absent": self.absent,
            "stackup": self.stackup,
            "rules": self.rules,
            # Extents of every measured edge, open chains included: this is a
            # measurement of the geometry that exists, not a fitted outline.
            "outline_bbox": _bbox(self.outline + self.outline_open),
            "outline_closed": bool(self.outline) and not self.outline_open,
        }
        if items:
            out.update({
                "outline": self.outline,
                "outline_open": self.outline_open,
                "cutouts": self.cutouts,
                "footprints": self.footprints,
                "tracks": self.tracks,
                "vias": self.vias,
                "zones": self.zones,
                "graphics": self.graphics,
                "nets": self.nets,
            })
        return out


def _bbox(polys):
    xs, ys = [], []
    for poly in polys or []:
        for x, y in poly:
            xs.append(x)
            ys.append(y)
    if not xs:
        return None
    return {"x_min": round(min(xs), 4), "y_min": round(min(ys), 4),
            "x_max": round(max(xs), 4), "y_max": round(max(ys), 4),
            "width_mm": round(max(xs) - min(xs), 4),
            "height_mm": round(max(ys) - min(ys), 4)}


def _stadium(w, h, n=24):
    """Oval (stadium) outline in the pad's own frame: rectangle plus two half caps."""
    r = min(w, h) / 2.0
    half = n // 2
    pts = []
    if w >= h:
        caps = ((w / 2.0 - r, 0.0, -90.0), (-(w / 2.0 - r), 0.0, 90.0))
    else:
        caps = ((0.0, h / 2.0 - r, 0.0), (0.0, -(h / 2.0 - r), 180.0))
    for (ccx, ccy, a0) in caps:
        for i in range(half + 1):
            a = math.radians(a0 + 180.0 * i / half)
            pts.append((ccx + r * math.cos(a), ccy + r * math.sin(a)))
    return pts


def _rounded_rect(w, h, rratio, per_corner=5):
    r = float(rratio or 0.25) * min(w, h)
    r = max(0.0, min(r, min(w, h) / 2.0))
    hx, hy = w / 2.0 - r, h / 2.0 - r
    pts = []
    for (sx, sy, a0) in ((1, 1, 0), (-1, 1, 90), (-1, -1, 180), (1, -1, 270)):
        for k in range(per_corner + 1):
            a = math.radians(a0 + 90.0 * k / per_corner)
            pts.append((sx * hx + r * math.cos(a), sy * hy + r * math.sin(a)))
    return pts


def pad_points(pad):
    """Pad outline as a polygon in canonical coordinates (cached on the dict).

    Built once in the pad's own frame (origin = the anchor `x`/`y`, which is the
    point pcbnew reports as GetPosition), then turned by the pad's absolute angle
    and shifted into board space:

    * a custom pad's outline is already stored relative to its anchor;
    * every other pad shape is centred on the anchor plus the DRILL OFFSET, because
      pcbnew moves the copper with `(drill ... (offset ...))` (complex_hierarchy
      Q201.1: bbox centre y+0.4, HitTest agrees over the whole pad).

    The shapes are exact enough to reproduce pcbnew's own HitTest on 412 pads /
    20188 probe points with zero disagreements -- see
    tests/test_kicad_pad_placement.py, which reruns that sweep against the KiCad
    install on this machine. The earlier oval was a 1.2 mm wide sliver for a
    1.6 x 2.8 pad (it sliced the wrong arcs out of `circle_points`).
    """
    pts = pad.get("_points")
    if pts is not None:
        return pts
    from .. import geometry as G
    cx, cy = pad["x"], pad["y"]
    w, h = pad.get("w") or 0.0, pad.get("h") or 0.0
    shape = (pad.get("shape") or "rect").lower()
    ang = pad.get("rotation") or 0.0
    if shape == "custom" and pad.get("polygon"):
        local = [(float(px), float(py)) for px, py in pad["polygon"]]
    else:
        ox, oy = pad.get("offset") or (0.0, 0.0)
        if shape == "circle":
            r = max(w, h) / 2.0
            local = G.circle_points(0.0, 0.0, r, 24)
        elif shape == "oval":
            local = _stadium(w, h)
        elif shape == "roundrect":
            # A plain rectangle would measure the gap between two neighbouring pads up
            # to 2*r too small (pic_programmer Q1..Q3: 0.48 mm became 0.23 mm).
            local = _rounded_rect(w, h, pad.get("rratio"))
        else:
            local = [(-w / 2.0, -h / 2.0), (w / 2.0, -h / 2.0),
                     (w / 2.0, h / 2.0), (-w / 2.0, h / 2.0)]
        local = [(px + ox, py + oy) for px, py in local]
    if ang:
        local = [G.rotate(px, py, 0.0, 0.0, ang) for px, py in local]
    pts = [(cx + px, cy + py) for px, py in local]
    pad["_points"] = pts
    return pts
