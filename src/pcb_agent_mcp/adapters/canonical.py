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

SCHEMA = 1

# Fact keys used in `present` / in `absent[].fact`. Keeping them as a closed set
# means a missing key is detectable: a new fact must be added here too.
FACT_KEYS = (
    "outline",            # board edge geometry
    "cutouts",            # internal slots / holes in the outline
    "footprints",         # placed components
    "pads",               # pad geometry + net assignment
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
        self.footprints = []       # [{ref, value, lib_id, x, y, rotation, layer, bbox, pads}]
        self.tracks = []           # [{net, layer, width, start, end}]
        self.vias = []             # [{net, x, y, drill, diameter, from, to, kind}]
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
            "nets": len(self.nets),
            "outline_contours": len(self.outline),
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
            "outline_bbox": _bbox(self.outline),
        }
        if items:
            out.update({
                "outline": self.outline,
                "cutouts": self.cutouts,
                "footprints": self.footprints,
                "tracks": self.tracks,
                "vias": self.vias,
                "zones": self.zones,
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


def pad_points(pad):
    """Pad outline as a polygon in canonical coordinates (cached on the dict)."""
    pts = pad.get("_points")
    if pts is not None:
        return pts
    from .. import geometry as G
    cx, cy = pad["x"], pad["y"]
    w, h = pad.get("w") or 0.0, pad.get("h") or 0.0
    shape = (pad.get("shape") or "rect").lower()
    ang = pad.get("rotation") or 0.0
    if shape == "circle":
        pts = G.circle_points(cx, cy, max(w, h) / 2.0, 24)
    elif shape == "oval":
        # stadium: two half circles at the ends of the long axis
        pts = []
        r = min(w, h) / 2.0
        if w >= h:
            c1, c2 = (cx - w / 2.0 + r, cy), (cx + w / 2.0 - r, cy)
            pts = (G.circle_points(c1[0], c1[1], r, 12)[3:9] +
                   G.circle_points(c2[0], c2[1], r, 12)[9:] )
        else:
            c1, c2 = (cx, cy - h / 2.0 + r), (cx, cy + h / 2.0 - r)
            pts = (G.circle_points(c1[0], c1[1], r, 12)[:6] +
                   G.circle_points(c2[0], c2[1], r, 12)[6:])
        if ang:
            pts = [G.rotate(x, y, cx, cy, ang) for x, y in pts]
    elif shape == "custom" and pad.get("polygon"):
        pts = [(cx + x, cy + y) for x, y in pad["polygon"]]
        if ang:
            pts = [G.rotate(x, y, cx, cy, ang) for x, y in pts]
    else:
        pts = G.rect_points(cx, cy, w, h, ang)
    pad["_points"] = pts
    return pts
