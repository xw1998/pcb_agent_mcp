"""Where a high-frequency capacitor has to stand: sites, distances, and the hole
in the copper its rail pad will have to reach.

`layout.hf_per_rail` (docs/183) answers "is there one". This module answers the
next question a generator hits immediately: **where**, and how far is that from
the pin it is supposed to serve. Without it the answer is a wall of text - "put a
100 nF on each of eight rails" - and the caller is back to eyeballing a 333-ball
BGA.

The rules that make a site legal are the same ones `place_gate` refuses on,
measured rather than restated:

* the body must not share a square millimetre with another body (courtyard gap);
* each pad must keep `clearance_mm` from foreign copper on the layers it is on;
* the body must stay `edge_keepout_mm` inside the board edge;
* the via that takes the rail pad down must stand **outside** the pad, so the
  stub-and-via pair is a dogbone and not a via-in-pad.

The distance that matters is reported twice:

* ``nearest_pin_mm`` - rail pad centre to the nearest **pad of that rail on the
  target part**. This is the number a placement rule can compare against.
* ``lower_bound_mm`` - how far that pin is from the part's own courtyard edge.
  A capacitor cannot be closer than this plus its own half-length, so a site at
  ``lower_bound + 0.5`` is the geometric optimum and a site at ``3 x`` that is a
  bad site, whatever the absolute number says.

Reporting the bound is not decoration. On the RK3506 SOM every rail cluster's
centroid sits 6+ mm inside U1's courtyard, so the prose rule "within 3 mm of the
cluster centre" is unsatisfiable by construction; measured against the nearest
ball plus its bound, the same sites read as optimal-or-not, which is a question
that has an answer.

One more thing is measured rather than assumed: whether the rail reaches the
site at all. ``connection`` is either a plane (a filled zone of that net covers
the via point on a layer) or a distance to the nearest copper of that net - and
when the rail has no copper anywhere near, it says ``none`` with the reason,
because a capacitor soldered to nothing is the defect this whole module exists to
not produce.
"""
import math

from . import place_gate as _gate
from .ruleset import measure as _m

#: The 0402 the SOM uses. Passed as a callable so the numbers cannot drift from
#: the file: `footprint()` reads pad and courtyard sizes out of the .kicad_mod.
DEFAULT_FOOTPRINT = ("Capacitor_SMD.pretty", "C_0402_1005Metric")

#: Pad-to-foreign-copper gap a site must keep. The board's own minimum is 0.1 mm;
#: a placement that lands exactly on the rule is what `place_gate` exists to
#: refuse later, so the default leaves a margin.
_CLEARANCE_MM = 0.15

#: Body-to-body gap on top of what the courtyards already enclose. Zero by
#: default, because a courtyard is *already* the body plus its margin: asking for
#: another 0.05 mm on top of a margin double-counts it and refuses sites the DRC
#: accepts (`place_gate.courtyard_overlap` refuses on overlap, not on touching).
#: A caller who wants rework space passes a number.
_BODY_GAP_MM = 0.0

#: Via that carries a pad down. Diameter/drill are the board's minimum via, so a
#: site plan never proposes a via the fab cannot drill.
_VIA_DIA_MM = 0.3
_VIA_DRILL_MM = 0.15

#: Rail-pad centre to via centre. Pad half-extent (0.31) + via radius (0.15) +
#: clearance (0.15) is 0.61; the default leaves 0.04 of slack.
_STUB_MM = 0.65

#: How far a site may sit from the part it serves, and how far copper of the rail
#: is looked for. Beyond this the answer is still a number, but it is not a
#: decoupling capacitor any more.
_REACH_MM = 6.0

#: A rail-side track may not be laid inside the courtyard of a fine-pitch array.
#: `+1V35_DDR`, `+0V9_CPU` and `+3V3` all have their nearest F.Cu copper on the
#: SoC's own balls, and a 0.10 mm trace through 0.65 mm pitch has 0.30 mm of gap
#: to work with - exactly the width plus twice the clearance, i.e. no margin at
#: all. "The copper is 5.6 mm away" is then not a plan, it is a manufacturing
#: risk with a number attached; the site is refused instead and the rail is
#: reported as needing a route of its own first.
_ARRAY_PADS = 64

#: Longest track a site may need to reach copper of its rail on the same layer.
#: Anything beyond this is not decoupling, it is a wire.
_MAX_TRACK_MM = 1.5

_SIDE_STEP_MM = 0.25
_OUT_STEP_MM = 0.05


# --------------------------------------------------------------- geometry ---
def _pad_box(pad):
    """(cx, cy, half_x, half_y, rotation) for the pad's own footprint frame."""
    return (float(pad.get("x") or 0.0), float(pad.get("y") or 0.0),
            float(pad.get("w") or pad.get("size_x") or 0.0) / 2.0,
            float(pad.get("h") or pad.get("size_y") or 0.0) / 2.0,
            float(pad.get("rotation") or 0.0))


def _pt_pad(x, y, pad):
    """Signed distance from a point to a pad outline (negative = inside)."""
    return _pt_geom(x, y, _pad_box(pad))


def _pt_geom(x, y, geom):
    """The same distance from a cached `_pad_box` result.

    Cached, because the search asks this tens of thousands of times and rebuilding
    the rotated box each time was a third of the first version's run time. A pad
    is NOT an axis-aligned rectangle - it has its own rotation, and the first
    attempt to "simplify" this into `_pt_rect(x, y, box)` returned 38 mm for a
    pad 5.7 mm away, which is why this function exists.
    """
    cx, cy, hx, hy, rot = geom
    a = math.radians(-rot)
    dx, dy = x - cx, y - cy
    lx = dx * math.cos(a) - dy * math.sin(a)
    ly = dx * math.sin(a) + dy * math.cos(a)
    ox = max(abs(lx) - hx, 0.0)
    oy = max(abs(ly) - hy, 0.0)
    if ox == 0.0 and oy == 0.0:
        return -min(hx - abs(lx), hy - abs(ly))
    return math.hypot(ox, oy)


def _pt_seg(x, y, x1, y1, x2, y2):
    vx, vy = x2 - x1, y2 - y1
    L2 = vx * vx + vy * vy
    t = 0.0 if L2 == 0.0 else max(0.0, min(1.0, ((x - x1) * vx + (y - y1) * vy) / L2))
    return math.hypot(x - (x1 + t * vx), y - (y1 + t * vy))


def _rect_gap(a, b):
    """Gap between two axis-aligned rectangles; negative when they overlap."""
    ax0, ay0, ax1, ay1 = a
    bx0, by0, bx1, by1 = b
    if bx0 <= ax1 and ax0 <= bx1 and by0 <= ay1 and ay0 <= by1:
        return -min(min(ax1, bx1) - max(ax0, bx0), min(ay1, by1) - max(ay0, by0))
    dx = max(bx0 - ax1, ax0 - bx1, 0.0)
    dy = max(by0 - ay1, ay0 - by1, 0.0)
    return math.hypot(dx, dy)


def _on_layer(pad, layer):
    """Does this pad have copper on `layer`? Wildcards count, and they matter.

    A stamp-hole / through-hole pad states ``*.Cu`` rather than listing six
    layers, so a naive ``layer in pad["layers"]`` reads every J1 pin as absent
    from F.Cu - which is how the first version of this module concluded the
    5 V inlet had no copper anywhere near its own connector.
    """
    lay = pad.get("layers") or []
    if "*.Cu" in lay:
        return True
    if layer.startswith("B.") and "F&B.Cu" in lay:
        return True
    if layer.startswith("F.") and "F&B.Cu" in lay:
        return True
    return layer in lay


def _pt_rect(x, y, r):
    """Distance from a point to an axis-aligned rectangle; 0 inside it."""
    dx = max(r[0] - x, 0.0, x - r[2])
    dy = max(r[1] - y, 0.0, y - r[3])
    return math.hypot(dx, dy)


def _point_in_poly(x, y, poly):
    inside = False
    n = len(poly)
    for i in range(n):
        x1, y1 = poly[i]
        x2, y2 = poly[(i + 1) % n]
        if (y1 > y) != (y2 > y):
            xin = x1 + (y - y1) * (x2 - x1) / (y2 - y1)
            if xin > x:
                inside = not inside
    return inside


def _segments(board):
    """Every straight copper run as (net, layer, x1, y1, x2, y2, half_width)."""
    out = []
    for t in board.tracks:
        pts = t.get("_points") or [t.get("start"), t.get("end")]
        hw = float(t.get("width") or 0.0) / 2.0
        for i in range(len(pts) - 1):
            x1, y1 = pts[i]
            x2, y2 = pts[i + 1]
            if x1 is None or x2 is None:
                continue
            out.append((t.get("net"), t.get("layer"), x1, y1, x2, y2, hw))
    return out


def _via_radius_mm(v):
    """Radius of a via, from whichever width the adapter spelled.

    `diameter` is what `parse_board` writes; `size` is what the edit layer
    writes. Either may be missing on a hand-built board, and a missing width
    must not silently become zero: that is a clearance check that approves
    shorts. So the last resort is the board minimum, not 0.
    """
    w = v.get("diameter")
    if w in (None, 0, 0.0):
        w = v.get("size")
    if w in (None, 0, 0.0):
        w = _VIA_DIA_MM
    return float(w) / 2.0

class _Copper(object):
    """Copper of one board, bucketed so a site search is not O(sites x objects).

    Every query takes an ``own`` set of nets that does not count as an obstacle:
    a decoupling site may overlap its own rail's copper, and doing so is how a
    pad connects without a stub at all.
    """

    CELL = 1.0

    def __init__(self, board):
        pads = board.pads() if callable(getattr(board, "pads", None)) else board.pads
        self.pads = [(p.get("ref"), p.get("number"), p.get("net"), p)
                     for p in pads]
        self.pad_geoms = [_pad_box(p) for p in pads]
        self.segs = _segments(board)
        # A via is a disc, and `parse_board` spells its width `diameter`.
        # Reading `size` made every via a point: every clearance answer in
        # this module was then optimistic by the via radius (0.10-0.15 mm), and
        # `_connection` would happily route a stub 0.05 mm from a via barrel.
        self.vias = [(v.get("net"), float(v.get("x")), float(v.get("y")),
                      _via_radius_mm(v)) for v in board.vias]
        self.buckets = {}
        for item in self._items():
            self._add(item)

    def _items(self):
        """(kind, index, net, x0, y0, x1, y1) - the item's extent, not its centre.

        Bucketing by the centre is the bug this replaced: an In4 line running
        9.4 mm down the board has its midpoint 2.5 mm away from a query that sits
        0.05 mm from it, so the query never visited it and every clearance test
        in this module answered "clear". A raster that is wrong in the optimistic
        direction is worse than no raster: it approves shorts.
        """
        for i, (_ref, _num, net, pad) in enumerate(self.pads):
            cx, cy, hx, hy, _rot = self.pad_geoms[i]
            r = max(hx, hy)
            yield ("pad", i, net, cx - r, cy - r, cx + r, cy + r)
        for i, s in enumerate(self.segs):
            hw = s[6]
            yield ("seg", i, s[0],
                   min(s[2], s[4]) - hw, min(s[3], s[5]) - hw,
                   max(s[2], s[4]) + hw, max(s[3], s[5]) + hw)
        for i, v in enumerate(self.vias):
            yield ("via", i, v[0], v[1] - v[3], v[2] - v[3], v[1] + v[3], v[2] + v[3])

    def _key(self, x, y):
        return (int(math.floor(x / self.CELL)), int(math.floor(y / self.CELL)))

    def _add(self, item):
        _k, _i, _n, x0, y0, x1, y1 = item
        k0 = self._key(x0, y0)
        k1 = self._key(x1, y1)
        for kx in range(k0[0], k1[0] + 1):
            for ky in range(k0[1], k1[1] + 1):
                self.buckets.setdefault((kx, ky), []).append(item)

    def _near(self, x, y, reach):
        k0 = self._key(x - reach, y - reach)
        k1 = self._key(x + reach, y + reach)
        seen = set()
        for kx in range(k0[0], k1[0] + 1):
            for ky in range(k0[1], k1[1] + 1):
                for item in self.buckets.get((kx, ky), ()):
                    key = (item[0], item[1])
                    if key in seen:
                        continue
                    seen.add(key)
                    yield (item[0], item[1], item[2],
                           (item[3] + item[5]) / 2.0, (item[4] + item[6]) / 2.0)

    def nearest(self, x, y, own, layers=None, reach=1e9):
        """(distance, what) to the nearest copper not in `own`."""
        best, who = 1e9, None
        for kind, i, net, ix, iy in self._near(x, y, min(reach, _REACH_MM * 4)):
            if net in own:
                continue
            if kind == "pad":
                pad = self.pads[i][3]
                if layers and not any(_on_layer(pad, l) for l in layers):
                    continue
                d = _pt_geom(x, y, self.pad_geoms[i])
            elif kind == "seg":
                s = self.segs[i]
                if layers and s[1] not in layers:
                    continue
                d = _pt_seg(x, y, s[2], s[3], s[4], s[5]) - s[6]
            else:
                v = self.vias[i]
                d = math.hypot(x - v[1], y - v[2]) - v[3]
            if d < best:
                best, who = d, (kind, i, net)
        return best, who

    def nearest_own(self, x, y, net, layers=None, reach=_REACH_MM):
        """(distance, where, layer) to the nearest copper OF `net`."""
        best, where, lay = 1e9, None, None
        for kind, i, n, ix, iy in self._near(x, y, reach):
            if n != net:
                continue
            if kind == "pad":
                pad = self.pads[i][3]
                if layers and not any(_on_layer(pad, l) for l in layers):
                    continue
                d = _pt_geom(x, y, self.pad_geoms[i])
                at, l = (pad.get("x"), pad.get("y")), (pad.get("layers") or [None])[0]
            elif kind == "seg":
                s = self.segs[i]
                if layers and s[1] not in layers:
                    continue
                d = max(_pt_seg(x, y, s[2], s[3], s[4], s[5]) - s[6], 0.0)
                at = _closest_on_seg(x, y, s[2], s[3], s[4], s[5])
                l = s[1]
            else:
                v = self.vias[i]
                d = max(math.hypot(x - v[1], y - v[2]) - v[3], 0.0)
                at, l = (v[1], v[2]), None
            if d < best:
                best, where, lay = d, at, l
        return best, where, lay


def _closest_on_seg(x, y, x1, y1, x2, y2):
    vx, vy = x2 - x1, y2 - y1
    L2 = vx * vx + vy * vy
    t = 0.0 if L2 == 0.0 else max(0.0, min(1.0, ((x - x1) * vx + (y - y1) * vy) / L2))
    return (x1 + t * vx, y1 + t * vy)


# ------------------------------------------------------------- the rails ---
def rails(board):
    """Rails that carry a bulk part but no small one: the nets worth a site.

    Deliberately the SAME population `layout.hf_per_rail` grades, and the same
    filter it applies, both taken from the rule rather than re-derived: a planner
    that offers a site for a net the rule never complained about is a planner that
    argues with its own report. The two would drift the day one of them learned a
    new name for a rail, so `_POWER_NAME`, `_RETURN_NAME`, `_CAP_REF`, `_farads`
    and `_HF_FARADS` are all imported.

    A net that reaches only two pads is normally not a rail - `/USB0_VBUSDET`
    carries the word VBUS and is a sense line. It becomes one here exactly when
    the rule makes it one: it already has a capacitor on it. That is the case
    this correction exists for (`+0V9_ANA` reaches 2 balls of U1 and one 10 uF).

    Each entry also names the part whose pins the capacitor is for: the part with
    the most pads on that net. That is a guess about intent, so `plan()` lets the
    caller overrule it per net.
    """
    caps = []
    for f in board.footprints:
        if not _m._CAP_REF.match(str(f.get("ref") or "")):
            continue
        far = _m._farads(f.get("value"))
        if far is None:
            continue
        caps.append(({p.get("net") for p in (f.get("pads") or []) if p.get("net")}, far))

    pours = _m._pour_area_by_net(board)
    return_net = max(pours, key=pours.get) if pours else None
    pins = {}
    holders = {}
    for f in board.footprints:
        pads = f.get("pads") or []
        if len(pads) < 6:
            continue
        for p in pads:
            net = p.get("net")
            if not net:
                continue
            pins[net] = pins.get(net, 0) + 1
            holders.setdefault(net, {}).setdefault(str(f.get("ref") or "?"), []).append(p)
    out = {}
    for net, k in pins.items():
        if not (_m._POWER_NAME.search(str(net)) or (net in pours and k >= 3)):
            continue
        if net == return_net or _m._RETURN_NAME.search(str(net)):
            continue
        has_cap = any(net in cnets for cnets, _f in caps)
        if k < 3 and net not in pours and not has_cap:
            continue
        if any(net in cnets and far <= _m._HF_FARADS for cnets, far in caps):
            continue                      # already bypassed: no site to offer
        part = max(holders[net].items(), key=lambda kv: len(kv[1]))
        out[net] = {"target_part": part[0], "pins": part[1], "pin_count": k,
                    "has_cap": has_cap}
    return out


# ---------------------------------------------------------------- the site ---
def _courtyard(fp):
    cy = fp.get("courtyard") or {}
    box = (cy.get("bbox") or {})
    if box:
        return (float(box["x_min"]), float(box["y_min"]),
                float(box["x_max"]), float(box["y_max"]))
    b = fp.get("bbox") or {}
    if b:
        return (float(b["x_min"]), float(b["y_min"]),
                float(b["x_max"]), float(b["y_max"]))
    return None


def _courtyard_boxes(fp):
    """A footprint's courtyard as boxes, one per contour.

    A ring must stay a ring. J1's courtyard is the 1 mm stamp-hole frame written
    as four separate contours; collapsing them to the overall bbox turns the whole
    45 mm board into one obstacle (and, with the area exemption, into no obstacle
    at all) - which is how a 0402 ended up on top of the castellations.
    """
    out = []
    for poly in (((fp.get("courtyard") or {}).get("contours")) or ()):
        if len(poly) < 3:
            continue
        xs = [p[0] for p in poly]
        ys = [p[1] for p in poly]
        out.append((min(xs), min(ys), max(xs), max(ys)))
    if not out:
        c = _courtyard(fp)
        if c:
            out.append(c)
    return out

def _courtyard_area(fp):
    """Copper-free area the courtyard actually covers, contours not bbox.

    Bounding boxes lie about rings: the stamp-hole frame's bbox is the whole board
    (2025 mm2) while the frame itself is 180 mm2. The exemption below asks "is this
    the module footprint?", and only the contour area answers it.
    """
    tot = 0.0
    for poly in (((fp.get("courtyard") or {}).get("contours")) or ()):
        if len(poly) >= 3:
            tot += abs(_m.G.polygon_area(poly))
    if tot > 0.0:
        return tot
    c = _courtyard(fp)
    return (c[2] - c[0]) * (c[3] - c[1]) if c else 0.0

def _bodies(board):
    """Footprint courtyards that a new body must not enter.

    A body covering most of the board is the module, not an obstacle: exempting it
    by AREA (not by name) is what `place_gate` does, and the same measurement is
    reused so the two cannot disagree. Everything else contributes one box per
    contour, so a frame still blocks the strip it occupies.
    """
    boxes = []
    limit = _gate.MODULE_AREA_FRAC * _board_area(board)
    for f in board.footprints:
        if _courtyard_area(f) > limit:
            continue
        for box in _courtyard_boxes(f):
            boxes.append((str(f.get("ref") or "?"), box))
    return boxes


def _board_area(board):
    best = 0.0
    for poly in (board.outline or []):
        if len(poly) >= 3:
            best = max(best, abs(_m.G.polygon_area(poly)))
    return best or 1.0


def footprint_shape(pad_pitch=0.48, pad_w=0.56, pad_h=0.62,
                    court_x=0.91, court_y=0.46):
    """The geometry a 0402 imposes on a site, as numbers rather than a file read.

    The defaults are `C_0402_1005Metric`: pads 0.56 x 0.62 at +-0.48, courtyard
    1.82 x 0.92. They can be overridden, and the site search reports the pair it
    used so a caller placing a different package knows what moved.
    """
    return {"pad_pitch": pad_pitch, "pad_w": pad_w, "pad_h": pad_h,
            "court_x": court_x, "court_y": court_y}


def _cap_geom(cx, cy, rot, shp):
    if rot % 180 == 0:
        court = (cx - shp["court_x"], cy - shp["court_y"],
                 cx + shp["court_x"], cy + shp["court_y"])
        p1 = (cx - shp["pad_pitch"], cy)
        p2 = (cx + shp["pad_pitch"], cy)
    else:
        court = (cx - shp["court_y"], cy - shp["court_x"],
                 cx + shp["court_y"], cy + shp["court_x"])
        p1 = (cx, cy - shp["pad_pitch"])
        p2 = (cx, cy + shp["pad_pitch"])
    return court, p1, p2


def _pad_rect(p, shp, rot):
    hx, hy = (shp["pad_w"] / 2.0, shp["pad_h"] / 2.0) if rot % 180 == 0 \
        else (shp["pad_h"] / 2.0, shp["pad_w"] / 2.0)
    return (p[0] - hx, p[1] - hy, p[0] + hx, p[1] + hy)


def site_ok(copper, bodies, board, cx, cy, rot, own, shp,
            clearance_mm=_CLEARANCE_MM, body_gap_mm=_BODY_GAP_MM,
            edge_keepout_mm=_gate.EDGE_KEEPOUT_MM, placed=None):
    """Why this site is refused, or None when it is legal. A reason, never a bool."""
    court, p1, p2 = _cap_geom(cx, cy, rot, shp)
    edge = _edge_box(board, edge_keepout_mm)
    if edge and not (court[0] >= edge[0] and court[1] >= edge[1]
                     and court[2] <= edge[2] and court[3] <= edge[3]):
        # Containment, not "does not overlap": a body parked entirely OUTSIDE the
        # inset box misses it too, and that was the first bug this check had - it
        # passed caps whose courtyard hung off the board edge.
        return "body not inside the %.2f mm edge keepout" % edge_keepout_mm
    for ref, box in bodies:
        if _rect_gap(court, box) < body_gap_mm:
            return "body into %s's courtyard" % ref
    # The reach is short on purpose: these two questions are "is it closer than
    # 0.15 mm" and "is there copper under the body", and a query that widens to
    # 24 mm to answer them scans 2304 buckets per call - it was most of the run
    # time of the first version.
    for p, lay in ((p1, own), (p2, own)):
        d, who = copper.nearest(p[0], p[1], lay, layers=("F.Cu",), reach=1.5)
        if d < clearance_mm:
            return "pad %.3f mm from %s (%s)" % (d, who[2], who[0])
    # Capacitors chosen earlier in the same pass are not on the board yet, so the
    # board's own copper cannot answer for them: they are passed in as rectangles
    # and checked by the same two numbers - pad clearance, and body over pad.
    for ref, court2, pads2 in (placed or ()):
        if _rect_gap(court, court2) < body_gap_mm:
            return "body into %s, already placed" % ref
        for rect, pnet in pads2:
            if pnet in own:
                continue
            for p in (p1, p2):
                d = _pt_rect(p[0], p[1], rect)
                if d < clearance_mm:
                    return "pad %.3f mm from placed %s" % (d, ref)
            if _rect_gap(court, rect) < 0.0:
                return "body over a pad of %s, already placed" % ref
    # No foreign copper under the body either: a 0402 on legs is not a thing.
    d, who = copper.nearest(cx, cy, own, layers=("F.Cu",), reach=1.5)
    if d < 0.0:
        return "copper of %s under the body" % (who[2],)
    return None


def _edge_box(board, inset):
    xs, ys = [], []
    for poly in (board.outline or []):
        for p in poly:
            xs.append(p[0])
            ys.append(p[1])
    if not xs:
        return None
    return (min(xs) + inset, min(ys) + inset, max(xs) - inset, max(ys) - inset)


class _Fill(object):
    """A filled zone polygon, indexed so a point query is local, not O(vertices).

    The pour on this board has 8011 vertices (GND) and 6263 (+3V3). Asking "is
    this pad inside it" with a plain ray cast is fine once and fatal in a search
    loop that asks thousands of times - it was the single reason a first pass took
    a hundred seconds. Two indexes fix it without changing an answer:

    * edges bucketed by horizontal band, so the crossing count only visits edges
      that can possibly straddle the query's y;
    * edges bucketed by 2D cell, so a distance query starts in its own cell and
      widens only as far as it must.
    """

    BAND = 0.5
    CELL = 1.0

    def __init__(self, poly):
        self.poly = poly
        self.bbox = (min(p[0] for p in poly), min(p[1] for p in poly),
                     max(p[0] for p in poly), max(p[1] for p in poly))
        self.bands = {}
        self.cells = {}
        n = len(poly)
        for i in range(n):
            x1, y1 = poly[i]
            x2, y2 = poly[(i + 1) % n]
            e = (x1, y1, x2, y2)
            for ky in range(self._k(min(y1, y2)), self._k(max(y1, y2)) + 1):
                self.bands.setdefault(ky, []).append(e)
            for kx in range(self._kc(min(x1, x2)), self._kc(max(x1, x2)) + 1):
                for ky in range(self._kc(min(y1, y2)), self._kc(max(y1, y2)) + 1):
                    self.cells.setdefault((kx, ky), []).append(e)

    @classmethod
    def _k(cls, v):
        """Band index - horizontal strips, used by the crossing count."""
        return int(math.floor(v / cls.BAND))

    @classmethod
    def _kc(cls, v):
        """Cell index for the 2D bucket grid, used by the distance query.

        Separate from the band index on purpose. Both used `_k` (0.5 mm) while
        `edge_distance` walked rings of `CELL` (1.0 mm), so every distance query
        looked in cells that had been keyed at half the granularity and mostly
        missed: `edge_distance` answered None for a point 0.5 mm from a plane
        edge, which silently turned every "a short run of copper reaches the
        flood" site into "no copper of this net at all".
        """
        return int(math.floor(v / cls.CELL))

    def near_bbox(self, x, y, reach):
        b = self.bbox
        return _pt_rect(x, y, b) <= reach

    def covers(self, x, y):
        b = self.bbox
        if x < b[0] or x > b[2] or y < b[1] or y > b[3]:
            return False
        inside = False
        for x1, y1, x2, y2 in self.bands.get(self._k(y), ()):
            if (y1 > y) != (y2 > y):
                xin = x1 + (y - y1) * (x2 - x1) / (y2 - y1)
                if xin > x:
                    inside = not inside
        return inside

    def edge_distance(self, x, y, reach):
        """Distance to the boundary, or None when it is further than `reach`."""
        best = None
        seen = set()
        rings = max(1, int(math.ceil(reach / self.CELL)))
        for r in range(rings + 1):
            kx0, ky0 = int(math.floor(x / self.CELL)), int(math.floor(y / self.CELL))
            for kx in range(kx0 - r, kx0 + r + 1):
                for ky in range(ky0 - r, ky0 + r + 1):
                    for e in self.cells.get((kx, ky), ()):
                        if e in seen:
                            continue
                        seen.add(e)
                        d = _pt_seg(x, y, e[0], e[1], e[2], e[3])
                        if best is None or d < best:
                            best = d
            # One extra ring of slack before trusting a ring result: an edge that
            # starts closer can sit in a cell the ring has not reached yet.
            if best is not None and best <= max(0.0, (r - 1) * self.CELL):
                return best
        if best is not None and best <= reach:
            return best
        return None


#: The fill index, for ONE board at a time. Keyed on `id(board)` it would be
#: cheap and wrong: a board that has been garbage collected hands its bucket
#: grid to whatever object lands on the same address. Comparing the object itself
#: costs nothing and cannot be fooled - and holding the board alive means the
#: address cannot be recycled underneath it. `reset_fill_cache()` exists for
#: tests, which build a fresh board per case.
_FILL_CACHE = {"board": None, "fills": {}}

def reset_fill_cache():
    """Drop the fill index. Exposed for tests; `plan()` does it on a board change."""
    _FILL_CACHE["board"] = None
    _FILL_CACHE["fills"] = {}

def _fills(board, net):
    """The fills of `net`, indexed once and reused across every site query."""
    if _FILL_CACHE["board"] is not board:
        _FILL_CACHE["board"] = board
        _FILL_CACHE["fills"] = {}
    cache = _FILL_CACHE["fills"]
    key = (id(board), net)
    if key not in cache:
        out = []
        for z in board.zones:
            if z.get("is_keepout") or z.get("net") != net:
                continue
            for poly in (z.get("filled_polygons") or []):
                if len(poly) >= 3:
                    out.append((_Fill(poly), list(z.get("layers") or [])))
        cache[key] = out
    return cache[key]


def _via_points(pad_xy, stub_mm, away=None):
    """The four points a dogbone via can sit at: `stub_mm` out along each axis.

    Ordered so the caller's preferred direction comes first - `away` is the
    direction pointing off the capacitor body, which is where the stub has to go
    to keep the via outside the pad.
    """
    x, y = pad_xy
    pts = [(x, y + stub_mm), (x, y - stub_mm), (x + stub_mm, y), (x - stub_mm, y)]
    if away:
        ax, ay = away
        pts.sort(key=lambda p: -((p[0] - x) * ax + (p[1] - y) * ay))
    return pts


def _crosses(p, q, rects, step_mm=0.1):
    """Does the straight run p->q pass through any of `rects`?

    Sampled rather than solved: the rectangles are courtyards a couple of
    millimetres across, the runs are shorter than `_REACH_MM`, and a sampled
    answer that is wrong by 0.1 mm only ever makes the check stricter.
    """
    if not rects:
        return False
    L = math.hypot(q[0] - p[0], q[1] - p[1])
    n = max(1, int(L / step_mm))
    for i in range(n + 1):
        t = i / float(n)
        x = p[0] + (q[0] - p[0]) * t
        y = p[1] + (q[1] - p[1]) * t
        for a, b, c, d in rects:
            if a <= x <= c and b <= y <= d:
                return True
    return False

def _connection(board, copper, net, pad_xy, reach=_REACH_MM, stub_mm=_STUB_MM,
                away=None, no_track=(), max_track_mm=_MAX_TRACK_MM):
    """How the rail pad can reach its rail, and what stands in the way.

    ``under_pad`` planes are the easy case: the zone floods the very point the
    pad sits on, so a via straight down lands in copper. A plane that exists but
    does not reach the pad is reported as ``plane`` with the distance, because
    that is a real route (a short run of copper on the plane's layer) rather than
    a failure - and it is how ``+5V_IN`` connects: its only flood is the In3
    island in the south-east corner, 1 mm or more away from the stamp-hole pins.
    """
    best = None
    fills = _fills(board, net)
    under = None
    for i, (fill, layers) in enumerate(fills):
        if fill.covers(pad_xy[0], pad_xy[1]):
            under = (i, layers)
            break
    if under is not None:
        # A flood over the pad still needs a via to reach an inner layer, and a
        # via in the pad is a process defect (the user's rule, and the reason
        # `_STUB_MM` exists at all). So the pad is reported as `under_pad` - the
        # flood is right there, no run of copper is needed - but the via still
        # goes `stub_mm` out, and only if the flood covers that point too.
        for via_at in _via_points(pad_xy, stub_mm, away):
            if fills[under[0]][0].covers(via_at[0], via_at[1]):
                return {"kind": "plane", "under_pad": True,
                        "via_at": [round(via_at[0], 3), round(via_at[1], 3)],
                        "layers": under[1], "layer_index": under[0],
                        "note": "the flood covers the pad; the via sits %.2f mm "
                                "out so the drill never enters the pad" % stub_mm}
        return {"kind": "plane", "under_pad": True, "layers": under[1],
                "layer_index": under[0],
                "note": "the flood covers the pad but no %.2f mm stub point is "
                        "inside it - the via would have to be in the pad" % stub_mm}
    # A via `stub_mm` out along a stub is the normal way a 0402 on the top layer
    # reaches a plane, and it is strictly better than running copper to wherever
    # the net happens to surface: two of the rails on this board have their only
    # F.Cu copper inside the SoC's own courtyard, 3-6 mm of track through a
    # 0.65 mm ball field. So the via point is asked first, and `away` matters -
    # the stub may not run back across the capacitor body.
    for via_at in _via_points(pad_xy, stub_mm, away):
        for i, (fill, layers) in enumerate(fills):
            if fill.covers(via_at[0], via_at[1]):
                return {"kind": "plane", "under_pad": False, "via_in_plane": True,
                        "via_at": [round(via_at[0], 3), round(via_at[1], 3)],
                        "layers": layers, "layer_index": i,
                        "note": "a %.2f mm stub puts the via inside the flood; no "
                                "track is needed on the plane layer" % stub_mm}
    for i, (fill, layers) in enumerate(fills):
        d = fill.edge_distance(pad_xy[0], pad_xy[1], min(reach, max_track_mm))
        if d is None:
            continue
        if best is None or d < best["plane_distance_mm"]:
            best = {"kind": "plane", "under_pad": False,
                    "plane_distance_mm": round(d, 3), "layers": layers,
                    "layer_index": i,
                    "note": "the flood of this net is %.2f mm away; the via "
                            "lands outside it and a short run of copper on "
                            "that layer carries it in" % d}
    # Same-layer copper is the cheap connection only while the run stays short
    # AND stays out of a fine-pitch array: the nearest copper of three rails on
    # this board is a ball of the SoC itself, and a "5.58 mm" reading there is a
    # trace through 0.65 mm pitch. `nearest_own` cannot express either condition,
    # so the candidates are walked here.
    best_c = None
    for kind, i, n, _ix, _iy in copper._near(pad_xy[0], pad_xy[1], reach):
        if n != net:
            continue
        if kind == "pad":
            pad = copper.pads[i][3]
            if not _on_layer(pad, "F.Cu"):
                continue
            at = (pad.get("x"), pad.get("y"))
            d = _pt_geom(pad_xy[0], pad_xy[1], copper.pad_geoms[i])
            lay = (pad.get("layers") or [None])[0]
        elif kind == "seg":
            s = copper.segs[i]
            if s[1] != "F.Cu":
                continue
            d = max(_pt_seg(pad_xy[0], pad_xy[1], s[2], s[3], s[4], s[5]) - s[6], 0.0)
            at = _closest_on_seg(pad_xy[0], pad_xy[1], s[2], s[3], s[4], s[5])
            lay = s[1]
        else:
            v = copper.vias[i]
            at = (v[1], v[2])
            d = max(math.hypot(pad_xy[0] - v[1], pad_xy[1] - v[2]) - v[3], 0.0)
            lay = None
        if d > max_track_mm:
            continue
        if _crosses(pad_xy, at, no_track):
            continue
        if best_c is None or d < best_c[0]:
            best_c = (d, at, lay)
    if best_c is not None:
        return {"kind": "copper", "layer": best_c[2],
                "to": [round(best_c[1][0], 3), round(best_c[1][1], 3)],
                "distance_mm": round(best_c[0], 3)}
    if best is not None and best["plane_distance_mm"] <= reach:
        return best
    return {"kind": "none", "why": ("no filled zone of this net reaches within "
                                    "%.1f mm and no copper of it does either"
                                    % reach)}


_ROUTE_STEP_MM = 0.05

#: Width of the two little runs a 0402 needs. Wide enough for a fab, narrow
#: enough that two of them fit between 0.5 mm pads.
_TRACK_MM = 0.2

def _layers_of(board):
    out = []
    st = board.stackup or []
    if isinstance(st, dict):
        st = st.get("layers") or []
    for l in st:
        name = l.get("name") if isinstance(l, dict) else l
        if name and str(name).endswith(".Cu") and str(name) not in out:
            out.append(str(name))
    if not out:
        for tr in board.tracks:
            n = tr.get("layer")
            if n and n not in out:
                out.append(n)
    return out or ["F.Cu", "B.Cu"]

def _clearance_at(copper, x, y, net, layers, reach=1.2):
    """(gap, what) to the nearest copper that is not `net` on `layers`.

    A via belongs to every layer it passes through, so the caller passes the
    whole stackup rather than the layer it is drawn on. That is not pedantry: on
    this board the second candidate for a ground via cleared F.Cu by 0.6 mm and
    sat exactly on an In2 data line, which is a short no DRC would forgive.
    """
    return copper.nearest(x, y, {net}, layers=layers, reach=reach)

def _inside_pad(copper, x, y, own=()):
    """Is the point inside a pad's copper? A drill there is a via in a pad."""
    for kind, i, net, _ix, _iy in copper._near(x, y, 1.2):
        if kind != "pad" or net in own:
            continue
        if _pt_geom(x, y, copper.pad_geoms[i]) <= 0.0:
            return (copper.pads[i][0], copper.pads[i][1], net)
    return None

def _run_clear(copper, p, q, net, layers, width, clearance):
    """Worst clearance along the straight run p->q, and who is worst."""
    L = math.hypot(q[0] - p[0], q[1] - p[1])
    n = max(1, int(L / _ROUTE_STEP_MM))
    best, who = 1e9, None
    for i in range(n + 1):
        t = i / float(n)
        d, w = _clearance_at(copper, p[0] + (q[0] - p[0]) * t,
                             p[1] + (q[1] - p[1]) * t, net, layers)
        d -= width / 2.0
        if d < best:
            best, who = d, w
            if best < clearance:
                return best, who
    return best, who

def _route45(copper, p, q, net, layers, width, clearance):
    """Shortest legal run from p to q using only 0/45/90/135 segments.

    Four shapes are tried (straight when the pair is axis-aligned or exactly
    diagonal, then the two ways to put a 45 across the difference). The one with
    the most clearance wins; a run that clears by less than `clearance` on every
    shape is refused, and the refusal names the copper in the way - "no route"
    with no name is the answer that sends the reader looking for the wrong bug.
    """
    def one(b):
        d, who = _run_clear(copper, p, b, net, layers, width, clearance)
        return (None, d, who, [p, b]) if d >= clearance else (None, d, who, None)

    dx, dy = q[0] - p[0], q[1] - p[1]
    outs = []
    if abs(dx) < 1e-9 or abs(dy) < 1e-9 or abs(abs(dx) - abs(dy)) < 1e-9:
        outs.append(one(q))
    if abs(dx) > abs(dy) + 1e-9:
        k = abs(dy) if dx > 0 else -abs(dy)
        outs.append(_two(copper, p, (p[0] + k, q[1]), q, net, layers,
                         width, clearance))
        k = abs(dy) if dx > 0 else -abs(dy)
        outs.append(_two(copper, p, (q[0] - k, p[1]), q, net, layers,
                         width, clearance))
    if abs(dy) > abs(dx) + 1e-9:
        k = abs(dx) if dy > 0 else -abs(dx)
        outs.append(_two(copper, p, (q[0], p[1] + k), q, net, layers,
                         width, clearance))
        outs.append(_two(copper, p, (p[0], q[1] - k), q, net, layers,
                         width, clearance))
    good = [o for o in outs if o[3]]
    if not good:
        worst = max(outs, key=lambda o: o[1]) if outs else (None, 0.0, None, None)
        return None, {"clearance_mm": round(worst[1], 3), "blocked_by": worst[2]}
    good.sort(key=lambda o: -o[1])
    return good[0][3], {"clearance_mm": round(good[0][1], 3)}

def _run_len(leg):
    """Length of a polyline leg, as the plan reports it."""
    pts = (leg or {}).get("run") or []
    return sum(math.hypot(pts[i + 1][0] - pts[i][0], pts[i + 1][1] - pts[i][1])
               for i in range(len(pts) - 1))

def _two(copper, p, a, q, net, layers, width, clearance):
    d1, w1 = _run_clear(copper, p, a, net, layers, width, clearance)
    if d1 < clearance:
        return (None, d1, w1, None)
    d2, w2 = _run_clear(copper, a, q, net, layers, width, clearance)
    if d2 < clearance:
        return (None, d2, w2, None)
    d = min(d1, d2)
    return (None, d, (w1 if d1 <= d2 else w2), [p, a, q])

#: How far a 0402's ground via may stand from its pad. The whole point of a
#: 100 nF is the loop it closes; a 2 mm stub to the plane is a different part.
_GND_ESCAPE_MM = 1.6

def _via_legal(board, bodies, x, y, own_ref=None, edge_keepout_mm=None,
               margin_mm=0.0):
    """Why a via may not sit here, or None.

    Two rules that the copper checks cannot see: a via dropped into the stamp-hole
    frame is inside the module's pin land (0.85 mm from the board edge), and one
    dropped inside somebody's courtyard is under a body. Both are placement
    mistakes that pass every clearance test, which is exactly why they need their
    own gate.
    """
    keep = _gate.EDGE_KEEPOUT_MM if edge_keepout_mm is None else edge_keepout_mm
    edge = _edge_box(board, keep)
    if edge and not (edge[0] <= x <= edge[2] and edge[1] <= y <= edge[3]):
        return "via at [%.3f, %.3f] is inside the %.2f mm edge keepout" % (x, y, keep)
    for ref, box in (bodies or ()):
        if ref == own_ref:
            continue
        # The annulus counts, not the centre: a via whose copper pokes into a
        # courtyard is in the same place as a via whose centre is there.
        if (box[0] - margin_mm <= x <= box[2] + margin_mm
                and box[1] - margin_mm <= y <= box[3] + margin_mm):
            return "via at [%.3f, %.3f] is inside %s's courtyard" % (x, y, ref)
    return None

def _escape(board, copper, net, rail_pad, conn, gnd_pad, stem=_STUB_MM,
            clearance_mm=_CLEARANCE_MM, via_dia=_VIA_DIA_MM,
            gnd_reach_mm=_GND_ESCAPE_MM, width=_TRACK_MM,
            layers=None, bodies=(), own_ref=None):
    """Both legs of one site: the rail side and the ground side.

    The rail side is whatever `_connection` already found (a plane via, a run to
    the rail's own copper); this checks that a real 45-degree run reaches it and
    that the via - if any - is not in a pad. The ground side is searched, because
    a site that has nowhere to put its ground via is not a decoupling site at
    all: an F.Cu 0402 whose ground pad reaches the In1 plane 2 mm away has an
    inductance that swamps the part.
    """
    allcu = layers or _layers_of(board)
    vr = via_dia / 2.0
    out = {"rail": None, "gnd": None}
    if conn.get("kind") == "none":
        out["why"] = "no copper of the rail to reach"
        return out
    if conn.get("kind") == "copper":
        run, why = _route45(copper, rail_pad, tuple(conn["to"]), net, ["F.Cu"],
                            width, clearance_mm)
        if run is None:
            out["why"] = "rail run blocked: %s at %.3f mm" % (
                why.get("blocked_by"), why.get("clearance_mm") or 0.0)
            return out
        out["rail"] = {"kind": "copper", "run": [[round(t[0], 3), round(t[1], 3)]
                                                for t in run],
                       "clearance_mm": why["clearance_mm"]}
        return out
    # plane, either straight under the pad or through a stub via
    if conn.get("via_in_plane") or conn.get("under_pad"):
        at = tuple(conn["via_at"])
        bad = _via_legal(board, bodies, at[0], at[1], own_ref,
                         margin_mm=vr + clearance_mm)
        if bad:
            out["why"] = bad
            return out
        d, who = _clearance_at(copper, at[0], at[1], net, allcu)
        if d < vr + clearance_mm:
            out["why"] = "rail via has %.3f mm to %s" % (d, who)
            return out
        hit = _inside_pad(copper, at[0], at[1], own=(net,))
        if hit:
            out["why"] = "rail via would sit in %s.%s" % (hit[0], hit[1])
            return out
        run, why = _route45(copper, rail_pad, at, net, ["F.Cu"], width,
                            clearance_mm)
        if run is None:
            out["why"] = "rail stub blocked: %s at %.3f mm" % (
                why.get("blocked_by"), why.get("clearance_mm") or 0.0)
            return out
        out["rail"] = {"kind": "plane", "via_at": [round(at[0], 3), round(at[1], 3)],
                       "run": [[round(t[0], 3), round(t[1], 3)] for t in run],
                       "clearance_mm": why["clearance_mm"]}
        return out
    out["why"] = "the flood of this rail is %.2f mm away; that is a route, not a stub" % (
        conn.get("plane_distance_mm") or 0.0)
    return out

def _gnd_escape(board, copper, gnd_pad, stem=_STUB_MM, clearance_mm=_CLEARANCE_MM,
                via_dia=_VIA_DIA_MM, reach_mm=_GND_ESCAPE_MM, layers=None,
                away=None, bodies=(), own_ref=None):
    """Nearest legal spot for the ground via, with the run that reaches it."""
    allcu = layers or _layers_of(board)
    fills = [(f, l) for f, l in _fills(board, "/GND") if "In1.Cu" in
             (l or _layers_of(board))]
    if not fills:
        return None, {"why": "no filled /GND flood on In1"}
    vr = via_dia / 2.0
    dirs = [(1, 0), (-1, 0), (0, 1), (0, -1),
            (0.70710678, 0.70710678), (-0.70710678, 0.70710678),
            (0.70710678, -0.70710678), (-0.70710678, -0.70710678)]
    cands = []
    n = int(reach_mm / _ROUTE_STEP_MM)
    for dx, dy in dirs:
        for i in range(int(round(stem / _ROUTE_STEP_MM)), n + 1):
            r = i * _ROUTE_STEP_MM
            x, y = gnd_pad[0] + dx * r, gnd_pad[1] + dy * r
            if not any(f.covers(x, y) for f, _l in fills):
                continue
            if _via_legal(board, bodies, x, y, own_ref,
                          margin_mm=vr + clearance_mm):
                continue
            d, who = _clearance_at(copper, x, y, "/GND", allcu)
            if d < vr + clearance_mm:
                continue
            if _inside_pad(copper, x, y, own=("/GND",)):
                continue
            run, why = _route45(copper, gnd_pad, (x, y), "/GND", ["F.Cu"],
                                _TRACK_MM, clearance_mm)
            if run is None:
                continue
            bias = 0.0 if not away else -0.05 * ((x - gnd_pad[0]) * away[0] +
                                                 (y - gnd_pad[1]) * away[1])
            cands.append((r + bias, {"via_at": [round(x, 3), round(y, 3)],
                                     "run": [[round(t[0], 3), round(t[1], 3)]
                                             for t in run],
                                     "length_mm": round(r, 3),
                                     "clearance_mm": round(d, 3),
                                     "layer": "In1.Cu"}))
            break
    if not cands:
        return None, {"why": "no point within %.1f mm holds a via clear of "
                             "foreign copper and inside the In1 flood" % reach_mm}
    cands.sort(key=lambda t: t[0])
    return cands[0][1], {}

def _pin_sites(board, copper, bodies, net, pins, shp, clearance_mm, body_gap_mm,
               reach_mm=3.0, step_mm=0.25, placed=None):
    """Sites in a square around a pin, for a target whose courtyard edge means nothing.

    A module-scale part (the stamp-hole ring) has a courtyard equal to the board,
    so "stand beside its edge" is a search that can only fail. Its pins are still
    where the current enters and leaves, so the square around the pin is the only
    region worth walking - and walking it, rather than assuming a side, is the
    difference between a plan and a guess.
    """
    out = []
    n = int(reach_mm / step_mm)
    for p in pins:
        for iy in range(-n, n + 1):
            for ix in range(-n, n + 1):
                cx = p.get("x") + ix * step_mm
                cy = p.get("y") + iy * step_mm
                for rot in (0, 90):
                    if site_ok(copper, bodies, board, cx, cy, rot, {net}, shp,
                               clearance_mm, body_gap_mm, placed=placed) is None:
                        out.append((cx, cy, rot))
    return out


def _plane_sites(board, copper, bodies, net, shp, clearance_mm, body_gap_mm,
                 step_mm=0.5, want=40, limit=4000, placed=None, rank=None):
    """Candidate sites inside the flood of `net`, in both orientations.

    Only used when the courtyard sweep found nowhere the rail actually reaches.
    Walks the bounding box of every filled polygon of the net, keeps the points
    inside one, and hands back the legal ones - legality is the same test, this
    only changes where the search looks.
    """
    seen, out = set(), []
    for z in board.zones:
        if len(out) >= want:
            break
        if z.get("is_keepout") or z.get("net") != net:
            continue
        for poly in (z.get("filled_polygons") or []):
            if len(poly) < 3:
                continue
            fill = _Fill(poly)
            xs = [fill.bbox[0], fill.bbox[2]]
            ys = [fill.bbox[1], fill.bbox[3]]
            ny = max(1, int((max(ys) - min(ys)) / step_mm))
            nx = max(1, int((max(xs) - min(xs)) / step_mm))
            for iy in range(ny + 1):
                for ix in range(nx + 1):
                    if len(out) >= limit:
                        if rank:
                            out.sort(key=lambda t: min(
                                math.hypot(t[0] - p.get("x"), t[1] - p.get("y"))
                                for p in rank))
                        return out[:want]
                    x = min(xs) + ix * step_mm
                    y = min(ys) + iy * step_mm
                    for rot in (0, 90):
                        key = (round(x, 2), round(y, 2), rot)
                        if key in seen:
                            continue
                        seen.add(key)
                        _c, p1, p2 = _cap_geom(x, y, rot, shp)
                        if not (fill.covers(p1[0], p1[1])
                                or fill.covers(p2[0], p2[1])):
                            continue
                        if site_ok(copper, bodies, board, x, y, rot, {net}, shp,
                                   clearance_mm, body_gap_mm, placed=placed):
                            continue
                        out.append((x, y, rot))
    if rank:
        out.sort(key=lambda t: min(math.hypot(t[0] - p.get("x"), t[1] - p.get("y"))
                                   for p in rank))
    return out[:want]

def _copper_sites(board, copper, bodies, net, shp, clearance_mm, body_gap_mm,
                  no_track=(), reach_mm=2.0, step_mm=0.25, want=24, placed=None):
    """Sites standing beside the rail's OWN copper, for a rail that never reaches U1.

    `+0V9_CPU` is the case: three balls on the SoC, no track, no flood, and its
    only copper outside the ball field is the regulator's inductor and its bulk.
    The courtyard sweep around the SoC can never find that copper, so the honest
    answer is found the other way round - walk the rail's accessible pads and
    stand next to them. The distance to the SoC is then reported as it is, which
    is the point: a 100nF at a regulator output is a different part from a 100nF
    at a pin, and the report says which one this turned out to be.
    """
    anchors = []
    # Straight over the pad list: `_near` with a whole-board reach walks
    # every empty bucket in a two-billion-squared grid, which is not a lookup.
    for i, (_ref, _num, n, pad) in enumerate(copper.pads):
        if n != net:
            continue
        if not _on_layer(pad, "F.Cu"):
            continue
        at = (pad.get("x"), pad.get("y"))
        if any(a <= at[0] <= c and b <= at[1] <= d for a, b, c, d in no_track):
            continue
        anchors.append(at)
    out, done = [], set()
    n = int(reach_mm / step_mm)
    for ax, ay in anchors:
        for iy in range(-n, n + 1):
            for ix in range(-n, n + 1):
                cx, cy = ax + ix * step_mm, ay + iy * step_mm
                for rot in (0, 90):
                    key = (round(cx, 2), round(cy, 2), rot)
                    if key in done:
                        continue
                    done.add(key)
                    if site_ok(copper, bodies, board, cx, cy, rot, {net}, shp,
                               clearance_mm, body_gap_mm, placed=placed):
                        continue
                    out.append((cx, cy, rot))
        if len(out) >= want:
            break
    return out

def plan(board, shape=None, *, clearance_mm=_CLEARANCE_MM,
         body_gap_mm=_BODY_GAP_MM, via_diameter_mm=_VIA_DIA_MM,
         via_drill_mm=_VIA_DRILL_MM, stub_mm=_STUB_MM, reach_mm=_REACH_MM,
         side_reach_mm=3.0, sides=None, nets=None, targets=None, top=3,
         placed=None, _copper=None, max_track_mm=_MAX_TRACK_MM):
    """One legal site per rail, ranked, with the numbers that made it legal.

    `sides` is a list of "left"/"right"/"top"/"bottom" to restrict the search -
    a caller who knows a corridor is reserved (the SOM reserves the whole top
    edge of U1 for the DDR escape) says so instead of trusting a clearance test
    to keep the corridor empty.

    `targets` overrules which part's pins a capacitor is for, per net. The
    default is the part with the most pads on the rail, which is right for a
    load but wrong for a rail that leaves a BGA and feeds a memory: `+1V35_DDR`
    has 18 pads on U2 and 5 balls on U1, and a bypass part for the SoC belongs
    beside the SoC. The choice is reported back in ``target_part`` so a reader can
    see which one was used rather than infer it.

    Where the rail's own flood is what makes the connection, a second sweep walks
    the flood itself: standing a capacitor beside a courtyard edge is meaningless
    when the rail does not reach that edge (`+5V_IN` floods only a south-east
    island), and the honest answer there is a site inside the island.
    """
    shp = shape or footprint_shape()
    # The copper index costs more to build than the search costs to run, and a
    # caller placing eight capacitors asks eight times: `fill()` passes its own.
    copper = _copper or _Copper(board)
    bodies = _bodies(board)
    cu_layers = _layers_of(board)
    # `bodies` holds the obstacles, i.e. everything that is NOT module-scale. The
    # exempted ones are the complement, and they are what makes a lower bound
    # meaningless: the stamp-hole ring's courtyard edge is the board edge.
    courted = {}
    for f in board.footprints:
        c = _courtyard(f)
        if c:
            courted[str(f.get("ref") or "?")] = c
    exempt = {ref for ref, c in courted.items()
              if (c[2] - c[0]) * (c[3] - c[1])
              > _gate.MODULE_AREA_FRAC * _board_area(board)}
    # Fine-pitch arrays: no new rail track across them. A 333-ball 0.65 mm field
    # leaves 0.30 mm between pads, which is exactly 0.10 width + 2 x 0.10
    # clearance - a trace that "fits" with zero margin is not a route. The
    # stamp-hole ring is excluded by the same area exemption that makes its
    # courtyard the whole board, and a 140-pin single row is not an array anyway.
    array_pads = {str(f.get("ref") or "?"): len(f.get("pads") or [])
                  for f in board.footprints}
    no_track = [c for ref, c in courted.items()
                if ref not in exempt
                and array_pads.get(ref, 0) >= _ARRAY_PADS]
    overrides = targets or {}
    found = {}
    for net, info in sorted(rails(board).items()):
        if nets is not None and net not in nets:
            continue
        pins = info["pins"]
        want = overrides.get(net) or info["target_part"]
        parts = [f for f in board.footprints if str(f.get("ref") or "?") == want]
        fp = parts[0] if parts else None
        court = _courtyard(fp) if fp else None
        if not court:
            found[net] = {"error": "the target part %s has no courtyard, so there "
                                   "is no edge to stand beside" % want}
            continue
        if want != info["target_part"]:
            # The caller moved the target, so the pins have to move with it: the
            # distances are measured to pads of THIS part, not to the busiest one.
            pins = [p for p in (fp.get("pads") or []) if p.get("net") == net]
        if not pins:
            found[net] = {"error": "%s has no pad on %s" % (want, net)}
            continue

        def d_edge(p):
            return min(p.get("x") - court[0], court[2] - p.get("x"),
                       p.get("y") - court[1], court[3] - p.get("y"))

        anchor = min(pins, key=d_edge)
        # A module-scale courtyard (the stamp-hole ring) is exempt from the body
        # test by area, and its "edge" is the board edge - so the bound it would
        # produce is zero and says nothing. Report that instead of a fake 0.
        lb = None if want in exempt else max(d_edge(anchor), 0.0)
        sides_try = sides or ["left", "right", "bottom", "top"]
        sites = []
        for side in sides_try:
            if side in ("left", "right"):
                rot = 0
                # The whole edge, not a window around the nearest pin. The anchor
                # is the pin closest to ANY edge, so a 3 mm window around it can
                # miss the part of the edge where the rail actually comes out -
                # on `+3V3` the anchor is the left-most ball and the flood only
                # surfaces on the right side, 8 mm away along the same edge.
                y0 = court[1] + shp["court_y"]
                y1 = court[3] - shp["court_y"]
                cy = y0
                while cy <= y1 + 1e-9:
                    for step in range(0, int(reach_mm / _OUT_STEP_MM) + 1):
                        g = step * _OUT_STEP_MM
                        cx = (court[0] - shp["court_x"] - g if side == "left"
                              else court[2] + shp["court_x"] + g)
                        why = site_ok(copper, bodies, board, cx, cy, rot, {net},
                                      shp, clearance_mm, body_gap_mm,
                                      placed=placed)
                        if not why:
                            sites.append((cx, cy, rot))
                            break
                    cy += _SIDE_STEP_MM
            else:
                rot = 90
                x0 = court[0] + shp["court_y"]
                x1 = court[2] - shp["court_y"]
                cx = x0
                while cx <= x1 + 1e-9:
                    for step in range(0, int(reach_mm / _OUT_STEP_MM) + 1):
                        g = step * _OUT_STEP_MM
                        cy = (court[1] - shp["court_x"] - g if side == "top"
                              else court[3] + shp["court_x"] + g)
                        why = site_ok(copper, bodies, board, cx, cy, rot, {net},
                                      shp, clearance_mm, body_gap_mm,
                                      placed=placed)
                        if not why:
                            sites.append((cx, cy, rot))
                            break
                    cx += _SIDE_STEP_MM
        if want in exempt:
            sites.extend(_pin_sites(board, copper, bodies, net, pins, shp,
                                    clearance_mm, body_gap_mm,
                                    side_reach_mm, _SIDE_STEP_MM, placed=placed))

        def _rows(cands):
            out = []
            for cx, cy, rot in cands:
                _court, p1, p2 = _cap_geom(cx, cy, rot, shp)
                rail_pad = min((p1, p2),
                               key=lambda q: min(math.hypot(q[0] - p.get("x"),
                                                            q[1] - p.get("y"))
                                                 for p in pins))
                gnd_pad = p2 if rail_pad is p1 else p1
                dmin, near = min(((math.hypot(rail_pad[0] - p.get("x"),
                                              rail_pad[1] - p.get("y")), p)
                                  for p in pins), key=lambda t: t[0])
                out.append({
                    "x": round(cx, 3), "y": round(cy, 3), "rotation": rot,
                    "rail_pad": [round(rail_pad[0], 3), round(rail_pad[1], 3)],
                    "gnd_pad": [round(gnd_pad[0], 3), round(gnd_pad[1], 3)],
                    "nearest_pin": near.get("number"),
                    "nearest_pin_mm": round(dmin, 3),
                    "lower_bound_mm": None if lb is None else round(lb, 3),
                    "over_lower_bound_mm": None if lb is None else round(dmin - lb, 3),
                    "connection": _connection(
                        board, copper, net, rail_pad, reach_mm, stub_mm,
                        away=((1.0 if rail_pad[0] > cx else -1.0),
                              (1.0 if rail_pad[1] > cy else -1.0)),
                        no_track=no_track, max_track_mm=max_track_mm),
                })
            out.sort(key=lambda r: (r["connection"]["kind"] == "none",
                                    round(r["nearest_pin_mm"], 2)))
            return out

        def escaped(cands, keep=int(top) + 6):
            """Attach both legs to each candidate and keep only the connectable.

            A site is only a site if copper can reach it on one side and a
            ground via can be dropped on the other. Checking that here, rather
            than leaving it to whoever emits the tracks, is the difference
            between a plan and a suggestion: three of the eight rails on this
            board have a perfectly legal body position whose ground pad has no
            legal via inside 1.6 mm, and calling those "sites" is how a
            generator ships eight capacitors and four open nets.

            Escape is computed for the candidates in rank order and stops after
            `keep` survivors - the search is local but not free, and the answer
            only ever needs the best few.
            """
            out = []
            for r in cands:
                if r["connection"]["kind"] == "none":
                    continue
                if "esc" in r:
                    out.append(r)
                    continue
                esc = _escape(board, copper, net, tuple(r["rail_pad"]),
                              r["connection"], tuple(r["gnd_pad"]),
                              stem=stub_mm, clearance_mm=clearance_mm,
                              via_dia=via_diameter_mm, layers=cu_layers,
                              bodies=bodies)
                if esc["rail"] is None:
                    r["refused"] = esc["why"]
                    continue
                gnd, why = _gnd_escape(board, copper, tuple(r["gnd_pad"]),
                                       stem=stub_mm, clearance_mm=clearance_mm,
                                       via_dia=via_diameter_mm, layers=cu_layers,
                                       bodies=bodies,
                                       away=((-1.0 if r["gnd_pad"][0] < r["x"]
                                              else 1.0), 0.0))
                if gnd is None:
                    r["refused"] = why["why"]
                    continue
                esc["gnd"] = gnd
                r["esc"] = esc
                r["escape_mm"] = round(0.5 * (gnd["length_mm"] +
                                              _run_len(esc["rail"])), 3)
                out.append(r)
                if len(out) >= keep:
                    break
            # The loop that closes a decoupling capacitor is the whole thing:
            # rank by rail leg + ground leg, not by the pin distance alone.
            out.sort(key=lambda r: (r["escape_mm"], round(r["nearest_pin_mm"], 2)))
            return out

        rows = escaped(_rows(sites))
        if not rows:
            # Standing beside a courtyard edge bought nothing: the rail does not
            # reach out there. The rail's own flood is then the only real ground,
            # so walk the flood and take a site that sits in it.
            alt = _rows(sites + _plane_sites(board, copper, bodies, net, shp,
                                             clearance_mm, body_gap_mm,
                                             placed=placed, rank=pins)
                        + _copper_sites(board, copper, bodies, net, shp,
                                        clearance_mm, body_gap_mm,
                                        no_track=no_track, placed=placed))
            if alt:
                rows = escaped(alt)
        found[net] = {"target_part": want,
                      "pins_on_target": len(pins),
                      "sites": rows[:max(1, int(top))],
                      "sites_considered": len(rows)}
        if not rows:
            found[net]["error"] = ("no candidate site has both a legal rail run "
                                   "and a legal ground via within %.1f mm"
                                   % _GND_ESCAPE_MM)
    out = {"shape": shp, "via": {"diameter_mm": via_diameter_mm,
                                 "drill_mm": via_drill_mm,
                                 "stub_mm": stub_mm},
           "clearance_mm": clearance_mm, "body_gap_mm": body_gap_mm,
           "max_track_mm": max_track_mm,
           "no_track_rects": [[round(v, 3) for v in c] for c in no_track],
           "rails": found}
    bare = [n for n, r in found.items() if not r.get("sites")]
    if bare:
        out["note"] = ("no legal site was found for %d rail(s): %s"
                       % (len(bare), ", ".join(sorted(bare)[:8])))
    return out


def _cap_rects(cx, cy, rot, shp):
    """A placed capacitor as (court, [(pad_rect, pad_net_slot)]) for the next pass."""
    court, p1, p2 = _cap_geom(cx, cy, rot, shp)
    return court, [(_pad_rect(p1, shp, rot), "1"), (_pad_rect(p2, shp, rot), "2")]


def fill(board, *, nets=None, targets=None, sides=None, shape=None,
         clearance_mm=_CLEARANCE_MM, body_gap_mm=_BODY_GAP_MM,
         via_diameter_mm=_VIA_DIA_MM, via_drill_mm=_VIA_DRILL_MM,
         stub_mm=_STUB_MM, reach_mm=_REACH_MM, side_reach_mm=3.0,
         max_track_mm=_MAX_TRACK_MM):
    """One capacitor per rail, tightest rail first, each one an obstacle for the next.

    `plan()` answers per rail as if the board were empty of capacitors. That is the
    wrong question to answer eight times: the sites it likes for `+0V9_LOGIC` and
    for `+0V9_CPU` sit 0.05 mm apart in the same bottom strip, and nothing about
    either answer says so. So a placement is a serial act - the cheapest-looking
    set of eight independent answers is not a layout.

    Order matters and is not a preference: the rail with the fewest legal sites is
    placed while the board is still open, because a rail that has four options must
    not be starved by a rail that had a hundred. Ties break toward the rail whose
    best site already sits furthest above its geometric lower bound - that is the
    one with the least room to give.

    Nothing here invents a connection: a rail whose copper does not reach any legal
    site comes back with ``kind: none`` and a reason, and the caller decides whether
    to move a part or to accept a longer stub.
    """
    shp = shape or footprint_shape()
    copper = _Copper(board)
    probes = plan(board, shp, clearance_mm=clearance_mm, body_gap_mm=body_gap_mm,
                  stub_mm=stub_mm, reach_mm=reach_mm, side_reach_mm=side_reach_mm,
                  sides=sides, nets=nets, targets=targets, top=1,
                  _copper=copper, max_track_mm=max_track_mm)
    room = {}
    for net, info in probes["rails"].items():
        rows = info.get("sites") or []
        over = rows[0]["over_lower_bound_mm"] if rows else 1e9
        room[net] = (0 if rows else 1, len(rows) and info["sites_considered"],
                     -(over or 0.0))
    order = [n for n in sorted(room, key=lambda n: room[n])]

    placed, out = [], {}
    for net in order:
        res = plan(board, shp, clearance_mm=clearance_mm, body_gap_mm=body_gap_mm,
                   stub_mm=stub_mm, reach_mm=reach_mm,
                   side_reach_mm=side_reach_mm, sides=sides, nets=[net],
                   targets=targets, top=3, placed=placed, _copper=copper,
                   max_track_mm=max_track_mm)
        info = res["rails"].get(net) or {"error": "rail vanished between passes"}
        rows = info.get("sites") or []
        if not rows:
            out[net] = dict(info, chosen=None)
            continue
        best = rows[0]
        if best["connection"]["kind"] == "none":
            # A site nobody can connect to is not a placement: it would land a
            # part, add two unconnected pads and make the board read worse.
            out[net] = dict(info, chosen=None,
                            refused="no reachable copper of this rail within "
                                    "%.2f mm and no flood under or beside the pad"
                                    % max_track_mm)
            continue
        court, pads = _cap_rects(best["x"], best["y"], best["rotation"], shp)
        placed.append(("cap:%s" % net, court,
                       [(r, net if k == "1" else "/GND") for r, k in pads]))
        out[net] = dict(info, chosen=best, alternatives=rows[1:])
    return {"shape": shp,
            "via": {"diameter_mm": via_diameter_mm, "drill_mm": via_drill_mm,
                    "stub_mm": stub_mm},
            "order": order, "rails": out}
