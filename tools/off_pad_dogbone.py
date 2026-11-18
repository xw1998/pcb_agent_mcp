#!/usr/bin/env python3
"""Slide a via that is drilled through a pad off that pad, and prove the move
before writing it.

`via_off_pad.py` moved these vias too, but its site search was a breadth-first
walk on the 0.05 mm grid and its stub was whatever path the walk found. That
produced copper the house's own gate refuses: collinear overlaps read as 0 deg
corners, short parallel runs read as duplicate copper, and the extra branches
closed rings. This tool searches differently, and it starts from what the hole
is actually touching rather than from what is near the pad:

  * every same-net track whose end sits ON the hole is a **contact**; the hole
    cannot leave a contact behind, so a contact is either **extended** (its
    endpoint moves to the new site, no new corner at all) or **stitched** (one
    straight segment from the old site to the new one, same layer, same net);
  * the site is on a **45 degree ray** out of the hole, so every stitch is one
    straight segment and every corner it makes is 90 or 135 degrees -- a stitch
    that would make a corner under 90 with a contact is refused rather than
    drawn, because that is the acid trap the old run created 18 times;
  * the stitch from the old site also carries the pad, because the old site is
    inside the pad's own copper: no second stitch is needed to reach the anchor;
  * the site clears the pad's own copper by drill radius + 0.15 mm (a hole whose
    wall touches the pad is still a via in a pad for the fab), clears every
    foreign item by via radius + 0.15 mm on every layer the hole spans, and
    keeps 0.15 mm hole to hole, 0.5 mm off the board edge;
  * after the move the hole still has same-net copper on at least two layers,
    and KiCad's own unconnected count must not go up. Both are checked, and a
    site that fails either is not used.

Nothing here routes a net and nothing moves a pad: the copper that appears is
the stitch a hand engineer draws to get a dogbone, and every number is printed
next to the decision that used it.

    python tools/off_pad_dogbone.py BOARD [--apply OUT] [--limit N]
"""
import argparse
import math
import os
import sys

sys.path.insert(0, os.path.join(os.path.dirname(os.path.dirname(
    os.path.abspath(__file__))), "src"))

from pcb_agent_mcp.adapters import open_board  # noqa: E402
from pcb_agent_mcp.adapters.canonical import pad_points  # noqa: E402
from pcb_agent_mcp.shape_gate import _point_in_polygon, _seg_point_distance  # noqa: E402

#: The hole wall must stay this far from pad copper. A dogbone via is drilled
#: beside the pad, not through its edge; 0.15 is the gap the board's own
#: clearance matrix already asks between different nets, so using it for the
#: same net too keeps the repair invisible to a fab review.
DRILL_PAD_GAP = float(os.environ.get("OPD_DPG", "0.15"))

#: Via body, and stitch body, to foreign copper on the same layer.
CLR = float(os.environ.get("OPD_CLR", "0.15"))

#: Drill wall to drill wall.
H2H = 0.15

#: Body to board edge.
EDGE = float(os.environ.get("OPD_EDGE", "0.50"))

#: A track end this close to the hole is a contact of the hole.
TOUCH = 0.02

STEP = 0.05
D_MIN = 0.10

#: How far off a contact's own axis a repair may aim and still be treated as
#: "along that axis". This is the tolerance the gate itself uses when it folds a
#: heading onto 0/45/90/135, so a joint this far off straight is still reported
#: as collinear, not as a new heading.
#:
#: It is deliberately NOT tightened. Two attempts measured why: at 0.05 deg the
#: SOM board refused 8 holes that a 1 deg acceptance solves, because a contact
#: 0.5 deg off a lattice ray is fine in copper. What is NOT fine is the segment
#: that comes out of the slide - when the length left over is short, a 1 deg
#: aiming error is amplified into a 6.3 deg heading (`/FSPI_D1`: 0.19 mm at
#: 96.3 deg) or a 1.4 deg one (`/FB_U7`). So the aiming stays loose and the
#: DRAWN segment is checked against the grid before the site is accepted.
COL_EPS = 1.0

#: How far a site may be pulled sideways so that it sits exactly ON the line of a
#: track this move continues. A slid track end draws one straight segment from
#: the track's fixed end to the new site, so 0.02 mm of sideways drift is enough
#: to turn it into a 1.4 or 1.7 degree heading (`/FB_U7` R8.1, `/FSPI_D1` U3.2),
#: and the angle gate reports any heading more than a degree off 0/45/90/135.
#: Snapping keeps the copper on the line it continues; the shift is a fraction of
#: the hole body and the site is re-checked against clearance after the snap.
SNAP_MAX = 0.10

#: Side of the square bucket the clearance queries walk. The site search asks a
#: clearance question tens of thousands of times per board, and a straight scan
#: over 5k items makes that minutes instead of seconds.
CELL = 1.0
D_MAX = float(os.environ.get("OPD_DMAX", "1.60"))

POWER_HINTS = ("3v3", "5v", "12v", "1v8", "1v35", "2v5", "0v9", "vcc", "vdd",
               "vin", "vbus", "vbat", "vref", "gnd", "pwr")

LATTICE = tuple((round(math.cos(math.radians(a)), 12),
                 round(math.sin(math.radians(a)), 12))
                for a in (0, 45, 90, 135, 180, 225, 270, 315))


def _powerish(net):
    low = (net or "").lower()
    return any(h in low for h in POWER_HINTS)


def _on(layers, lay):
    return lay in layers or "*.Cu" in layers


def _ang(u, v):
    d = max(-1.0, min(1.0, u[0] * v[0] + u[1] * v[1]))
    return math.degrees(math.acos(d))


def _poly_dist(x, y, pts):
    """Distance from a point to a closed polygon outline (0.0 when inside)."""
    if len(pts) < 3:
        return 1e9
    if _point_in_polygon(x, y, pts):
        return 0.0
    n = len(pts)
    best = 1e9
    for i in range(n):
        d = _seg_point_distance(x, y, pts[i][0], pts[i][1],
                               pts[(i + 1) % n][0], pts[(i + 1) % n][1])
        if d < best:
            best = d
    return best


def _seg_seg(a, b):
    """Distance between two segments."""
    (x1, y1, x2, y2), (x3, y3, x4, y4) = a, b

    def cross(o, p, q):
        return (p[0] - o[0]) * (q[1] - o[1]) - (p[1] - o[1]) * (q[0] - o[0])

    d1 = cross((x3, y3), (x4, y4), (x1, y1))
    d2 = cross((x3, y3), (x4, y4), (x2, y2))
    d3 = cross((x1, y1), (x2, y2), (x3, y3))
    d4 = cross((x1, y1), (x2, y2), (x4, y4))
    if ((d1 > 0) != (d2 > 0)) and ((d3 > 0) != (d4 > 0)):
        return 0.0
    return min(_seg_point_distance(x1, y1, x3, y3, x4, y4),
               _seg_point_distance(x2, y2, x3, y3, x4, y4),
               _seg_point_distance(x3, y3, x1, y1, x2, y2),
               _seg_point_distance(x4, y4, x1, y1, x2, y2))


class Model(object):
    def __init__(self, board):
        self.pads = []
        for p in board.pads() or []:
            if p.get("net") is None or p.get("x") is None:
                continue
            pts = pad_points(p)
            self.pads.append({
                "net": p["net"],
                "layers": [str(s) for s in (p.get("layers") or [])],
                "poly": pts, "x": float(p["x"]), "y": float(p["y"]),
                "ref": "%s.%s" % (p.get("ref"), p.get("number")),
                "drill": float(p.get("hole_mm") or 0.0)})

        self.tracks = []
        for t in board.tracks or []:
            if t.get("net") is None or not t.get("start") or not t.get("end"):
                continue
            self.tracks.append({
                "net": t["net"], "layer": str(t.get("layer")),
                "x1": float(t["start"][0]), "y1": float(t["start"][1]),
                "x2": float(t["end"][0]), "y2": float(t["end"][1]),
                "w": float(t.get("width") or 0.0),
                "uuid": t.get("uuid")})

        self.vias = []
        for v in board.vias or []:
            if v.get("net") is None or v.get("x") is None:
                continue
            self.vias.append({
                "net": v["net"], "x": float(v["x"]), "y": float(v["y"]),
                "r": float(v.get("diameter") or 0.0) / 2.0,
                "drill": float(v.get("drill") or 0.0),
                "layers": [str(s) for s in (v.get("layers") or [])]})

        self.zones = []
        for z in board.zones or []:
            if z.get("net") is None or len(z.get("outline") or []) < 3:
                continue
            self.zones.append({
                "net": z["net"],
                "layers": [str(s) for s in (z.get("layers") or [])],
                "poly": [(float(a), float(b)) for a, b in z["outline"]]})

        #: (net, layer) -> tracks. The coverage question "is this stitch already
        #: copper" asks over one net on one layer, and walking 4k tracks to build
        #: that list per candidate made the planner take minutes on the SOM board.
        self.by_net_layer = {}
        for t in self.tracks:
            self.by_net_layer.setdefault((t["net"], t["layer"]), []).append(t)

        self.edges = []
        for chain in (board.outline or []):
            for i in range(len(chain) - 1):
                self.edges.append((float(chain[i][0]), float(chain[i][1]),
                                   float(chain[i + 1][0]), float(chain[i + 1][1])))
        self._index()

    # -- 1 mm bucket index ------------------------------------------------
    def _index(self):
        self.cells = {}
        grow = 1.0

        def put(kind, idx, x0, y0, x1, y1):
            for ix in range(int(math.floor((min(x0, x1) - grow) / CELL)),
                            int(math.floor((max(x0, x1) + grow) / CELL)) + 1):
                for iy in range(int(math.floor((min(y0, y1) - grow) / CELL)),
                                int(math.floor((max(y0, y1) + grow) / CELL)) + 1):
                    self.cells.setdefault((ix, iy), []).append((kind, idx))

        for i, p in enumerate(self.pads):
            xs = [q[0] for q in p["poly"]]
            ys = [q[1] for q in p["poly"]]
            put("pad", i, min(xs), min(ys), max(xs), max(ys))
        for i, t in enumerate(self.tracks):
            put("track", i, t["x1"], t["y1"], t["x2"], t["y2"])
        for i, v in enumerate(self.vias):
            put("via", i, v["x"], v["y"], v["x"], v["y"])
        for i, e in enumerate(self.edges):
            put("edge", i, e[0], e[1], e[2], e[3])

    def near(self, x, y):
        seen = {}
        out = {"pad": [], "track": [], "via": [], "edge": []}
        for ix in range(int(math.floor(x / CELL)) - 1, int(math.floor(x / CELL)) + 2):
            for iy in range(int(math.floor(y / CELL)) - 1,
                            int(math.floor(y / CELL)) + 2):
                for kind, idx in self.cells.get((ix, iy), ()):
                    if (kind, idx) in seen:
                        continue
                    seen[(kind, idx)] = True
                    out[kind].append(idx)
        return out

    # -- clearance -------------------------------------------------------
    def point_ok(self, x, y, via, layers):
        rd, rv = via["drill"] / 2.0, via["r"]
        nb = self.near(x, y)
        for p in [self.pads[i] for i in nb["pad"]]:
            if not any(_on(p["layers"], L) for L in layers):
                continue
            d = _poly_dist(x, y, p["poly"])
            if p["net"] == via["net"]:
                if d < rd + DRILL_PAD_GAP - 1e-6:
                    return "pad %s copper %.3f mm away (need %.3f)" % (
                        p["ref"], d, rd + DRILL_PAD_GAP)
            elif d < rv + CLR - 1e-6:
                return "foreign pad %s %.3f mm away (need %.3f)" % (
                    p["ref"], d, rv + CLR)
        for t in [self.tracks[i] for i in nb["track"]]:
            if t["layer"] not in layers or t["net"] == via["net"]:
                continue
            d = _seg_point_distance(x, y, t["x1"], t["y1"], t["x2"], t["y2"])
            if d - t["w"] / 2.0 < rv + CLR:
                return "foreign track %s on %s %.3f mm away (need %.3f)" % (
                    t["net"], t["layer"], d - t["w"] / 2.0, rv + CLR)
        for v in [self.vias[i] for i in nb["via"]]:
            if abs(v["x"] - x) < 1e-9 and abs(v["y"] - y) < 1e-9:
                continue
            d = math.hypot(v["x"] - x, v["y"] - y)
            if v["drill"] > 0 and d < (via["drill"] + v["drill"]) / 2.0 + H2H:
                return "via hole %s %.3f mm away (need %.3f)" % (
                    v["net"], d, (via["drill"] + v["drill"]) / 2.0 + H2H)
            if v["net"] != via["net"] and d < rv + v["r"] + CLR:
                return "foreign via body %s %.3f mm away (need %.3f)" % (
                    v["net"], d, rv + v["r"] + CLR)
        for i in nb["edge"]:
            x1, y1, x2, y2 = self.edges[i]
            d = _seg_point_distance(x, y, x1, y1, x2, y2)
            if d < EDGE + rv:
                return "board edge %.3f mm away (need %.3f)" % (d, EDGE + rv)
        return None

    def stitch_ok(self, ax, ay, bx, by, lay, net, width):
        r = width / 2.0
        seg = (ax, ay, bx, by)
        for p in self.pads:
            if not _on(p["layers"], lay) or p["net"] == net:
                continue
            n = len(p["poly"])
            for i in range(n):
                e = (p["poly"][i][0], p["poly"][i][1],
                     p["poly"][(i + 1) % n][0], p["poly"][(i + 1) % n][1])
                if _seg_seg(seg, e) < r + CLR:
                    return "returns through foreign pad %s (need %.3f)" % (
                        p["ref"], r + CLR)
            if _point_in_polygon(ax, ay, p["poly"]):
                return "starts inside foreign pad %s" % p["ref"]
        for t in self.tracks:
            if t["layer"] != lay or t["net"] == net:
                continue
            if _seg_seg(seg, (t["x1"], t["y1"], t["x2"], t["y2"])) < \
                    r + t["w"] / 2.0 + CLR:
                return "runs into foreign track %s on %s" % (t["net"], lay)
        for v in self.vias:
            if v["net"] == net:
                continue
            if _seg_point_distance(v["x"], v["y"], ax, ay, bx, by) < r + v["r"] + CLR:
                return "runs into foreign via %s" % v["net"]
        return None

    # -- connectivity ----------------------------------------------------
    def held(self, x, y, via, extra):
        """Layers of the hole that will carry same-net copper at the new site."""
        got = []
        for L in via["layers"]:
            if any(L == el for el, _w in extra):
                got.append(L)
                continue
            hit = False
            for t in self.tracks:
                if t["layer"] != L or t["net"] != via["net"]:
                    continue
                if _seg_point_distance(x, y, t["x1"], t["y1"],
                                       t["x2"], t["y2"]) <= t["w"] / 2.0:
                    hit = True
                    break
            if not hit:
                for z in self.zones:
                    if z["net"] == via["net"] and L in z["layers"] and \
                            _point_in_polygon(x, y, z["poly"]):
                        hit = True
                        break
            if not hit:
                for v in self.vias:
                    if v["net"] != via["net"] or not _on(v["layers"], L):
                        continue
                    if abs(v["x"] - x) < 1e-9 and abs(v["y"] - y) < 1e-9:
                        continue
                    if math.hypot(v["x"] - x, v["y"] - y) <= v["r"] + via["r"]:
                        hit = True
                        break
            if hit:
                got.append(L)
        return got


def _lattice_ok(ux, uy):
    """Is this unit vector one of the eight 45 degree headings?"""
    a = math.degrees(math.atan2(uy, ux)) % 45.0
    return min(a, 45.0 - a) * math.pi / 180.0 < 0.5 * math.pi / 180.0


def _pad_covers(model, net, layer, x, y):
    """True if same-net pad copper on `layer` already holds this point."""
    nb = model.near(x, y)
    for p in [model.pads[i] for i in nb["pad"]]:
        if p["net"] == net and _on(p["layers"], layer) and \
                _point_in_polygon(x, y, p["poly"]):
            return True
    return False

def _in_own_copper(model, net, layer, x, y, inset=0.0):
    """True if `(x,y)` is inside same-net copper on `layer` with `inset` slack.

    `inset` is the half-width of the copper that would be drawn there: asking
    for `inset = w/2` means "a `w`-wide trace laid at this point adds nothing",
    which is exactly the question a stitch has to answer before it is drawn.
    """
    nb = model.near(x, y)
    for i in nb["track"]:
        t = model.tracks[i]
        if t["layer"] != layer or t["net"] != net:
            continue
        d = _seg_point_distance(x, y, t["x1"], t["y1"], t["x2"], t["y2"])
        if d <= t["w"] / 2.0 - inset + 1e-6:
            return True
    for i in nb["pad"]:
        p = model.pads[i]
        if p["net"] == net and _on(p["layers"], layer) and \
                _point_in_polygon(x, y, p["poly"]):
            return True
    # zones are not in the 1 mm bucket table (they are few), so they are walked
    for z in model.zones:
        if z["net"] == net and layer in z["layers"] and \
                _point_in_polygon(x, y, z["poly"]):
            return True
    return False

def _covered_by_own(model, net, layer, ax, ay, bx, by, width):
    """Is the whole segment already same-net copper of at least `width`?

    A stitch that runs along copper that is already there is a second trace on
    top of the first. That is what `shape_gate` now reports as an overlapping
    copper pair, and the answer here is to NOT draw it: the layers it would have
    carried are still carried, because `held()` counts the copper that is
    already present. Measured on `_dg_h.kicad_pcb`: `/+3V3`, `/+5V_IN` and
    `/+1V35_DDR` each got a stitch laid exactly on an existing trace and each
    came back as a 0 degree corner.

    Tracks are handled exactly (project every collinear same-net track onto the
    segment and ask whether the intervals cover it end to end), because this
    runs inside the site search and a per-sample walk over `near()` made the
    planner take minutes. Pads and zones are sampled at a handful of points; a
    stitch that is entirely inside a pad or a pour needs no copper either.
    """
    L = math.hypot(bx - ax, by - ay)
    if L < 1e-9:
        return True
    ux, uy = (bx - ax) / L, (by - ay) / L
    need = width / 2.0 - 1e-6

    idx = getattr(model, "by_net_layer", None)
    if idx is None:
        pool = [t for t in model.tracks
                if t["layer"] == layer and t["net"] == net]
    else:
        pool = idx.get((net, layer), ())
    spans = []
    for t in pool:
        if t["w"] / 2.0 < need:
            continue
        tx, ty = t["x2"] - t["x1"], t["y2"] - t["y1"]
        tl = math.hypot(tx, ty)
        if tl < 1e-9:
            continue
        if abs((tx / tl) * uy - (ty / tl) * ux) > 1e-4:
            continue
        if _seg_point_distance(ax, ay, t["x1"], t["y1"],
                               t["x2"], t["y2"]) > t["w"] / 2.0 - need:
            continue
        s1 = (t["x1"] - ax) * ux + (t["y1"] - ay) * uy
        s2 = (t["x2"] - ax) * ux + (t["y2"] - ay) * uy
        spans.append((min(s1, s2), max(s1, s2)))
    if spans:
        spans.sort()
        reach = 0.0
        for lo, hi in spans:
            if lo > reach + 1e-6:
                break
            if hi > reach:
                reach = hi
            if reach >= L - 1e-6:
                return True

    for k in (0.0, 0.25, 0.5, 0.75, 1.0):
        if not _in_own_copper(model, net, layer,
                              ax + (bx - ax) * k, ay + (by - ay) * k, need):
            return False
    return True

VROUND = 3


def _ray_angle(u, v):
    """The corner number `angle_audit` reports: the angle between two rays.

    The gate folds a pair of outgoing directions to `0..180` and calls anything
    under 90 an acute corner, 90 a square one and 135 a chamfer. Reproducing
    that number here is the whole point of this helper - a second, "reasonable"
    angle convention would refuse sites the gate accepts and accept sites it
    refuses, which is the same mistake in two directions.
    """
    d = max(-1.0, min(1.0, u[0] * v[0] + u[1] * v[1]))
    return math.degrees(math.acos(d))


def _shape_local(model, net, drawn, slid, tol_deg=1.0):
    """Would the copper this candidate draws leave a corner the gate refuses?

    `drawn` is copper to be written, `(layer, width, a, b)` per piece. `slid`
    is an existing track whose near end moves onto the new site,
    `(layer, at, far, old)`: the piece it leaves behind is `at`..`far`, so it
    contributes a leaving direction at the NEW site and takes one away at `at`
    (writing it in `drawn` as well would be the duplicate the gate refuses).
    `old` is where that end sat before the slide, so the track's own copper can
    be recognised at its old place and dropped: a stale ray there would pair
    with the stitch that continues it and read as 0 degrees.

    The audit is the gate's own, run on the vertices this move touches: at each
    such vertex every pair of outgoing rays on that layer has to keep an angle
    of at least 90. Two rays in the SAME direction are the overlap the gate
    calls duplicate copper and come out of the same arithmetic as 0 degrees.

    Priority, and the reason this runs before a site is accepted: an acute
    corner is rule 1 and blocks the board outright, while one more hole left in
    its pad is rule 1 of `place_gate` - so a site that buys a hole with an acid
    trap is refused rather than reported. Measured on `_dg_i.kicad_pcb`: 45
    holes moved, 17 left behind, and the board still read `shape_fail` on two
    45 degree corners at `/+3V3` 11.8,11.12 and 11.8,13.92 - both made by a
    stitch that started at an existing track end where a 45 degree branch
    already left the same point.
    """
    if not drawn and not slid:
        return None
    layers = [p[0] for p in drawn] + [s[0] for s in slid]
    seen_layer = []
    for L in layers:
        if L not in seen_layer:
            seen_layer.append(L)
    idx = getattr(model, "by_net_layer", None)

    def pool(L):
        if idx is None:
            return [t for t in model.tracks
                    if t["layer"] == L and t["net"] == net]
        return idx.get((net, L), ())

    # the tracks being slid are not at `at` any more, so they must not be read
    # as copper that still leaves from there
    gone = set()
    for s in slid:
        L, at, far = s[0], s[1], s[2]
        old = s[3] if len(s) > 3 else s[1]
        for a, b in ((at, far), (old, far)):
            gone.add((L, round(a[0], VROUND), round(a[1], VROUND),
                      round(b[0], VROUND), round(b[1], VROUND)))
            gone.add((L, round(b[0], VROUND), round(b[1], VROUND),
                      round(a[0], VROUND), round(a[1], VROUND)))

    rays = {}

    def add(L, px, py, qx, qy):
        d = math.hypot(qx - px, qy - py)
        if d < 1e-9:
            return
        rays.setdefault((L, round(px, VROUND), round(py, VROUND)), []).append(
            ((qx - px) / d, (qy - py) / d))

    for L in seen_layer:
        for t in pool(L):
            k1 = (L, round(t["x1"], VROUND), round(t["y1"], VROUND),
                  round(t["x2"], VROUND), round(t["y2"], VROUND))
            if k1 in gone:
                continue
            add(L, t["x1"], t["y1"], t["x2"], t["y2"])
            add(L, t["x2"], t["y2"], t["x1"], t["y1"])

    touch = set()
    for L, _w, a, b in drawn:
        add(L, a[0], a[1], b[0], b[1])
        touch.add((L, round(a[0], VROUND), round(a[1], VROUND)))
        touch.add((L, round(b[0], VROUND), round(b[1], VROUND)))
    for s in slid:
        L, at, far = s[0], s[1], s[2]
        add(L, at[0], at[1], far[0], far[1])
        touch.add((L, round(at[0], VROUND), round(at[1], VROUND)))

    for k in touch:
        lst = rays.get(k, ())
        for i in range(len(lst)):
            for j in range(i + 1, len(lst)):
                a = _ray_angle(lst[i], lst[j])
                if a < 90.0 - tol_deg:
                    return ("this site would leave a %.1f deg corner at "
                            "%.3f,%.3f on %s" % (a, k[1], k[2], k[0]))
    return None


def _off_grid(ax, ay, bx, by, tol_deg=1.0):
    """Is this segment off the 0/45/90/135 headings by more than `tol_deg`?

    The tolerance is the gate's own: `angle_audit(angle_tol_deg=1.0)` folds a
    heading onto 0/45/90/135 within a degree, so a segment inside that degree is
    reported as on-grid and rebuilding it is not a violation. Refusing what the
    gate would accept is the same class of mistake as accepting what it rejects.
    """
    h = math.degrees(math.atan2(by - ay, bx - ax)) % 180.0
    a = h % 45.0
    return min(a, 45.0 - a) > tol_deg

def _snap_to_axis(contacts, pinned, u, x, y):
    """Pull a site onto the line of a track this move carries on.

    Three ways of leaving a hole all draw a segment that is supposed to be the
    SAME line as an existing track: sliding a track end along its own axis (in
    either direction), and carrying on from a track's far end. In all three the
    site has to sit exactly on that track's line, otherwise the segment comes
    out a degree or two off the grid - which is not a routing style question but
    the difference between `45deg` and `other` in the angle gate.

    Only axes this ray is already heading along are snapped (the angle tests
    below are the same ones the caller uses), and only when the pull is shorter
    than `SNAP_MAX`; a bigger pull would be a different site, not the same one.
    """
    lines = []
    for c in contacts:
        a = _ang(c["u"], u)
        if a < COL_EPS or a > 180.0 - COL_EPS:
            lines.append((c["end"], c["u"]))
    for (ex, ey, _lay, _dd, _w, far) in pinned:
        fl = math.hypot(far[0] - ex, far[1] - ey)
        dl = math.hypot(x - ex, y - ey)
        if fl < 1e-9 or dl < 1e-9:
            continue
        du = ((far[0] - ex) / fl, (far[1] - ey) / fl)
        if (x - ex) / dl * du[0] + (y - ey) / dl * du[1] > 0.9995:
            lines.append(((ex, ey), du))
    best = None
    for (px, py), (dx, dy) in lines:
        t = (x - px) * dx + (y - py) * dy
        nx, ny = px + t * dx, py + t * dy
        s = math.hypot(nx - x, ny - y)
        if s <= SNAP_MAX and (best is None or s < best[0]):
            best = (s, nx, ny)
    if best is not None:
        return best[1], best[2], best[1] - x, best[2] - y
    return x, y, 0.0, 0.0


def contacts_of(model, via):
    """Same-net track ends sitting on the hole, with the heading they leave by."""
    out = []
    for t in model.tracks:
        if t["net"] != via["net"] or t["layer"] not in via["layers"]:
            continue
        for (ex, ey), (fx, fy) in (((t["x1"], t["y1"]), (t["x2"], t["y2"])),
                                   ((t["x2"], t["y2"]), (t["x1"], t["y1"]))):
            if math.hypot(ex - via["x"], ey - via["y"]) > TOUCH:
                continue
            L = math.hypot(fx - ex, fy - ey)
            if L < 1e-9:
                continue
            out.append({"layer": t["layer"], "u": ((fx - ex) / L, (fy - ey) / L),
                        "len": L, "w": t["w"], "track": t,
                        "end": (ex, ey), "far": (fx, fy)})
            break
    return out


#: The board's own DRC rules asked for 0.1 mm clearance, a 0.2 mm via and an
#: 0.05 mm annulus. When the only way out of a pad is a gap narrower than the
#: via body, the repair is the smallest via those rules still allow - the same
#: decision a hand engineer makes - so the search retries at these diameters and
#: every row that changes one says so.
SHRINK_TO = (0.40, 0.35, 0.30, 0.25, 0.20)
ANNULAR_MIN = 0.05

#: A hole wedged between BGA balls often has no straight way out, but a short
#: jog followed by a second leg reaches around the ball. Two legs whose
#: headings differ by 45 or 90 degrees leave a 135 or 90 degree corner, both of
#: which the board's own angle rule accepts; 135 degrees would leave an acute
#: corner and is never generated.
DOG_HOPS = tuple(float(x) for x in os.environ.get(
    "OPD_HOPS", "0.30,0.45,0.60,0.80").split(","))


def _dogleg_at(model, via, pad, r, contacts, pad_layers):
    V = (via["x"], via["y"])
    layers = list(via["layers"])
    ref = contacts[0] if contacts else None
    need = []
    for L in pad_layers + [c["layer"] for c in contacts]:
        if L not in need:
            need.append(L)
    pw = 0.20 if _powerish(via["net"]) else 0.12
    widths = {}
    for L in need:
        widths[L] = pw
    for c in contacts:
        widths[c["layer"]] = max(widths.get(c["layer"], 0.0),
                                 max(c["w"], pw))

    pinned = []
    for t in model.tracks:
        if t["net"] != via["net"] or t["layer"] not in layers:
            continue
        for (ex, ey) in ((t["x1"], t["y1"]), (t["x2"], t["y2"])):
            dd = math.hypot(ex - V[0], ey - V[1])
            if TOUCH < dd <= r + 0.06:
                if not _pad_covers(model, via["net"], t["layer"], ex, ey):
                    pinned.append((ex, ey, t["layer"], dd))
                break

    best = None
    for u1 in LATTICE:
        for u2 in LATTICE:
            dt = u1[0] * u2[0] + u1[1] * u2[1]
            if abs(dt) > 0.999:          # collinear: that is the straight case
                continue
            if dt < -0.7:                # 135 degree turn leaves a 45 interior
                continue
            # the first leg leaves the hole the same way a straight stitch
            # would, so the corner rule against every contact is unchanged
            ok = True
            for c in contacts:
                a = _ang(c["u"], u1)
                if 1.0 < a < 89.0:
                    ok = False
                    break
            if not ok:
                continue
            for h1 in DOG_HOPS:
                bx, by = V[0] + h1 * u1[0], V[1] + h1 * u1[1]
                bad = False
                for L in need:
                    if model.stitch_ok(V[0], V[1], bx, by, L, via["net"],
                                       widths[L]):
                        bad = True
                        break
                if bad:
                    continue
                d2 = D_MIN
                while d2 <= D_MAX + 1e-9:
                    ex, ey = bx + d2 * u2[0], by + d2 * u2[1]
                    if model.point_ok(ex, ey, via, layers):
                        d2 = round(d2 + STEP, 4)
                        continue
                    if any(math.hypot(q[0] - ex, q[1] - ey) > r + 1e-6
                           for q in pinned):
                        d2 = round(d2 + STEP, 4)
                        continue
                    bad = False
                    for L in need:
                        if model.stitch_ok(bx, by, ex, ey, L, via["net"],
                                           widths[L]):
                            bad = True
                            break
                    if bad:
                        d2 = round(d2 + STEP, 4)
                        continue
                    carry = [(L, widths[L]) for L in need]
                    if len(model.held(ex, ey, via, carry)) < 2:
                        d2 = round(d2 + STEP, 4)
                        continue
                    # the dogleg is the one path that writes two pieces per
                    # layer straight out of the hole, so it is the one that
                    # lays a leg on an existing trace: measured on
                    # `_dg_i.kicad_pcb`, `/+0V9_CPU` L1.2's first leg ran 0.8 mm
                    # down exactly where the pad's own 0.4 mm In4 trace already
                    # ran, and the gate read it as 0 degrees plus a ring.
                    shed = _shape_local(model, via["net"],
                                        [(L, widths[L], V, (bx, by))
                                         for L in need] +
                                        [(L, widths[L], (bx, by), (ex, ey))
                                         for L in need], [])
                    if shed:
                        d2 = round(d2 + STEP, 4)
                        continue
                    stitch = [(L, widths[L], [V, (bx, by), (ex, ey)])
                              for L in need]
                    cand = {"x": round(ex, 4), "y": round(ey, 4),
                            "d": round(h1 + d2, 4),
                            "u": (round(u2[0], 6), round(u2[1], 6)),
                            "why": "dogleg", "stitch": stitch, "extend": [],
                            "held": model.held(ex, ey, via, carry)}
                    key = (len(stitch), 1, round(h1 + d2, 4))
                    if best is None or key < best[0]:
                        best = (key, cand)
                    break
                if best is not None and best[0][2] <= h1 + D_MIN:
                    break
    return best


def plan_one(model, via, pad):
    """Best legal site for this hole, trying the current body first.

    A refused site is retried with a thinner via body rather than with a looser
    clearance: the clearance was read off the board's own rule, so relaxing it
    would be inventing a rule the board does not have, while a 0.35 mm via in a
    0.71 mm gap is something a fab will build without a question.
    """
    cand, why, alts = _plan_at(model, via, pad, via["r"])
    if cand is not None:
        return cand, None, alts
    for dia in SHRINK_TO:
        r = dia / 2.0
        if r >= via["r"] - 1e-9:
            continue
        if dia - via["drill"] < 2.0 * ANNULAR_MIN - 1e-9:
            continue
        c2, why2, alts2 = _plan_at(model, via, pad, r)
        if c2 is not None:
            c2["shrink_to"] = round(dia, 3)
            for k, c in alts2:
                c["shrink_to"] = round(dia, 3)
            return c2, None, alts2
    return None, why, []

def _plan_at(model, via, pad, r):
    via = dict(via, r=r)
    V = (via["x"], via["y"])
    layers = list(via["layers"])
    contacts = contacts_of(model, via)
    pad_layers = [L for L in layers if _on(pad["layers"], L)]

    # a same-net track end that sits *inside* this hole's copper but not on its
    # centre is held up by the barrel alone on inner layers - there is no pad
    # there. Moving the hole may not leave such an end behind: either the new
    # copper still covers it, or the site is refused.
    pinned = []
    for t in model.tracks:
        if t["net"] != via["net"] or t["layer"] not in layers:
            continue
        for (ex, ey), (fx, fy) in (((t["x1"], t["y1"]), (t["x2"], t["y2"])),
                                   ((t["x2"], t["y2"]), (t["x1"], t["y1"]))):
            dd = math.hypot(ex - V[0], ey - V[1])
            if TOUCH < dd <= r + 0.06:
                if not _pad_covers(model, via["net"], t["layer"], ex, ey):
                    pinned.append((ex, ey, t["layer"], dd, t["w"], (fx, fy)))
                break

    dirs = []
    for c in contacts:
        dirs.append((c["u"], "extend %s" % c["layer"]))
        # the opposite way lengthens the same track instead of shortening it:
        # the endpoint slides back along its own axis, so the copper still has
        # no corner at all and no length limit applies
        dirs.append(((-c["u"][0], -c["u"][1]), "extend %s back" % c["layer"]))
    for u in LATTICE:
        dirs.append((u, "lattice"))

    best = None
    alts = []
    killed = []
    _dbg = bool(os.environ.get("OPD_DEBUG"))
    def _why(tag, msg):
        if _dbg:
            print("      [%s d=%.2f u=%.0f] %s" % (
                tag, d, math.degrees(math.atan2(u[1], u[0])) % 360, msg))
    for u, why in dirs:
        seen = None
        got_site = False
        d = D_MIN
        while d <= D_MAX + 1e-9:
            x, y = V[0] + d * u[0], V[1] + d * u[1]
            # a move that carries a track on along its own line has to land ON
            # that line, or the piece it draws comes out a degree or two off the
            # grid; the clearance checks below then run at the snapped point
            x, y, sx, sy = _snap_to_axis(contacts, pinned, u, x, y)
            if sx or sy:
                # a stitch that starts at the hole centre would be left a degree
                # off the ray by the snap, so it starts at the same sideways
                # offset instead. That start has to stay inside copper the hole
                # already has (the pad on its layers, the barrel on the rest), so
                # a snap is dropped rather than turned into a disconnected piece.
                if math.hypot(sx, sy) > max(0.0, r - 0.03) or \
                        not _point_in_polygon(V[0] + sx, V[1] + sy, pad["poly"]):
                    x, y, sx, sy = x - sx, y - sy, 0.0, 0.0
            S = (V[0] + sx, V[1] + sy)
            why_bad = model.point_ok(x, y, via, layers)
            if why_bad and (sx or sy):
                # the snap exists only to keep the drawn copper on the grid; when
                # it pushes the site into clearance trouble, keep the point the
                # search asked for and let the grid gate judge the segment that
                # comes out of it (measured: `/FB_U7` R7.2 sat 9 um too close to
                # its own pad edge only after the snap)
                x, y, sx, sy = x - sx, y - sy, 0.0, 0.0
                S = (V[0], V[1])
                why_bad = model.point_ok(x, y, via, layers)
            if why_bad:
                seen = why_bad   # keep the reason at the furthest site tried
                _why("clear", why_bad)
                d = round(d + STEP, 4)
                continue
            # a pinned end either stays under the new copper, or is picked up
            # by a stitch that starts at that very end - which is where the
            # track already is - or is slid along its own axis like a contact
            pin_ext = []
            pin_st = []
            pin_bad = None
            for (ex, ey, play, pdd, pw, far) in pinned:
                if math.hypot(ex - x, ey - y) <= r + 1e-6:
                    continue
                dx, dy = x - ex, y - ey
                dl = math.hypot(dx, dy)
                if dl < 1e-9:
                    continue
                ux, uy = dx / dl, dy / dl
                fl = math.hypot(far[0] - ex, far[1] - ey)
                fu = ((far[0] - ex) / fl, (far[1] - ey) / fl) if fl > 0 else (0.0, 0.0)
                dot = ux * fu[0] + uy * fu[1]
                # "nearly its own axis" is enough to aim, because the segment
                # the slide produces is checked against the grid before the site
                # is accepted; a sideways drift of this size is exactly what made
                # the 96.3 deg stub on `/FSPI_D1` and it is now caught there.
                if dot > 0.9995:
                    pin_ext.append({"layer": play, "w": pw,
                                    "a": [ex, ey], "b": [far[0], far[1]]})
                    continue
                if not _lattice_ok(ux, uy):
                    pin_bad = "a %s track end would be left %.2f mm behind " \
                              "and the way out is not a 45 degree heading" % (
                                  play, pdd)
                    break
                if dot > -0.017:
                    pin_bad = "a %s track end would be left %.2f mm behind " \
                              "and stitching from it makes a corner sharper " \
                              "than 90 degrees" % (play, pdd)
                    break
                pw2 = max(pw, 0.12)
                why_s = model.stitch_ok(ex, ey, x, y, play, via["net"], pw2)
                if why_s:
                    pin_bad = "pick-up stitch from a %s track end: %s" % (
                        play, why_s)
                    break
                pin_st.append((play, pw2, [(ex, ey), (x, y)]))
            if pin_bad:
                seen = pin_bad
                _why("pin", pin_bad)
                d = round(d + STEP, 4)
                continue

            extend, stitch, bad = [], [], None
            for c in contacts:
                a = _ang(c["u"], u)
                if a > 180.0 - COL_EPS:
                    # sliding the endpoint back along the track's own axis keeps
                    # it one straight segment: no corner, and no length limit
                    extend.append(c)
                elif a < COL_EPS and c["len"] >= d - 1e-9:
                    extend.append(c)
                elif a < COL_EPS:
                    # the track is shorter than the move, so its endpoint
                    # cannot be slid: carry the same heading on with a stitch.
                    # It has to start at the track's FAR end, not at the hole:
                    # starting at the hole lays the new copper on top of the
                    # old track (measured on `/+3V3` In2 11.4,13.92, where the
                    # existing 11.4->11.8 piece got a second copy on it and the
                    # pair read as a 0 degree corner).
                    far = c["far"]
                    if math.hypot(far[0] - x, far[1] - y) <= 1e-6:
                        continue        # the track already reaches the new site
                    cc = dict(c, frm=far)
                    stitch.append(cc)
                elif a < 90.0 - 1.0:
                    bad = "stitch would make a %.1f deg corner with the %s " \
                          "track" % (a, c["layer"])
                else:
                    stitch.append(c)
                if bad:
                    break
            if bad:
                seen = bad
                _why("corner", bad)
                break

            # the pad itself: the old site is inside its copper, so one stitch on
            # a pad layer carries it out; a pad layer that already has a contact
            # going this way is extended instead and needs nothing
            pad_width = 0.20 if _powerish(via["net"]) else 0.12
            pad_done = any(L in pad_layers for L in
                           [c["layer"] for c in extend] +
                           [c["layer"] for c in stitch])
            extra_pad = []
            # a pad layer whose copper already reaches the new site needs no
            # stitch at all - and it still COUNTS as carried, otherwise skipping
            # the stitch would talk the planner out of a site that is fine.
            # Measured: `/+5V_IN` U6.4 sat 0.30 mm under its own F.Cu trace and
            # got a 0.5 mm stitch laid on that trace (a 0 degree corner); with
            # the stitch skipped but not counted, the same hole was refused.
            pad_carried = False
            for L in pad_layers:
                if L in [c["layer"] for c in extend] + \
                        [c["layer"] for c in stitch]:
                    continue
                if _covered_by_own(model, via["net"], L,
                                   S[0], S[1], x, y, pad_width):
                    pad_carried = True
                    continue
                extra_pad.append(L)

            plan_stitch = []
            okc = True
            for c in stitch:
                width = max(c["w"], 0.12)
                sx0, sy0 = c.get("frm", S)
                why_s = model.stitch_ok(sx0, sy0, x, y, c["layer"],
                                        via["net"], width)
                if why_s:
                    okc = False
                    seen = "stitch %s/%.0fdeg: %s" % (c["layer"], math.degrees(
                        math.atan2(u[1], u[0])) % 360, why_s)
                    break
                sx1, sy1 = c.get("frm", S)
                plan_stitch.append((c["layer"], width,
                                    [(sx1, sy1), (x, y)]))
            if not okc:
                d = round(d + STEP, 4)
                continue
            for L in extra_pad:
                why_s = model.stitch_ok(S[0], S[1], x, y, L, via["net"], pad_width)
                if why_s:
                    okc = False
                    seen = "pad stitch %s/%.0fdeg: %s" % (L, math.degrees(
                        math.atan2(u[1], u[0])) % 360, why_s)
                    break
                plan_stitch.append((L, pad_width,
                                    [S, (x, y)]))
            if not okc:
                d = round(d + STEP, 4)
                continue
            if not pad_done and not extra_pad and not pad_carried:
                d = round(d + STEP, 4)
                continue

            plan_stitch.extend(pin_st)
            extend = extend + pin_ext

            # every extended track also has to be clear along the piece it grows
            for c in extend:
                ax0, ay0 = (c["end"] if "end" in c
                            else (c["a"][0], c["a"][1]))
                why_s = model.stitch_ok(ax0, ay0, x, y, c["layer"],
                                        via["net"], max(c["w"], 0.12))
                if why_s:
                    okc = False
                    seen = "extend %s/%.0fdeg: %s" % (c["layer"], math.degrees(
                        math.atan2(u[1], u[0])) % 360, why_s)
                    break
            if not okc:
                d = round(d + STEP, 4)
                continue

            # Last gate before the site is accepted: every piece of copper this
            # candidate would draw has to be on the 0/45/90/135 grid. The site
            # search works on ray directions, but a stitch that continues a
            # contact starts at that contact's far end, and a slid track end
            # recently sat up to 1.8 degrees off its own axis - so the direction
            # of the ACTUAL segment is what gets checked, not the ray.
            off = None
            for L, _w, pts in plan_stitch:
                for i in range(len(pts) - 1):
                    if _off_grid(pts[i][0], pts[i][1],
                                 pts[i + 1][0], pts[i + 1][1]):
                        off = "planned %s stitch %.1f,%.1f -> %.1f,%.1f is off " \
                              "the 45 grid" % ((L,) + tuple(pts[i]) + tuple(pts[i + 1]))
                        break
                if off:
                    break
            if off is None:
                for c in extend:
                    bx_, by_ = (c["far"] if "far" in c
                                else (c["b"][0], c["b"][1]))
                    if _off_grid(x, y, bx_, by_):
                        off = "sliding the %s track end to the new site leaves " \
                              "a segment off the 45 grid" % c["layer"]
                        break
            if off:
                seen = off
                _why("grid", off)
                d = round(d + STEP, 4)
                continue

            drawn = []
            for L, w, pts in plan_stitch:
                for i in range(len(pts) - 1):
                    drawn.append((L, w, pts[i], pts[i + 1]))
            slid = [(c["layer"], (x, y),
                     tuple(c["far"]) if "far" in c
                     else (c["b"][0], c["b"][1]),
                     tuple(c["end"]) if "end" in c
                     else (c["a"][0], c["a"][1])) for c in extend]
            shed = _shape_local(model, via["net"], drawn, slid)
            if shed:
                seen = shed
                _why("shape", shed)
                d = round(d + STEP, 4)
                continue

            carry = [(L, w) for L, w, _pts in plan_stitch] + \
                    [(c["layer"], c["w"]) for c in extend]
            got = model.held(x, y, via, carry)
            if len(got) < 2:
                seen = "held on %d layers only (%.0fdeg)" % (len(got), math.degrees(
                    math.atan2(u[1], u[0])) % 360)
                d = round(d + STEP, 4)
                continue

            cand = {"x": round(x, 4), "y": round(y, 4), "d": round(d, 4),
                    "u": (round(u[0], 6), round(u[1], 6)), "why": why,
                    "stitch": plan_stitch,
                    "extend": [{"layer": c["layer"], "w": c["w"],
                                "a": (list(c["end"]) if "end" in c
                                      else list(c["a"])),
                                "b": (list(c["far"]) if "far" in c
                                      else list(c["b"]))}
                               for c in extend],
                    "held": got}
            key = (len(plan_stitch), 0 if why != "lattice" else 1, d)
            alts.append((key, cand))
            if best is None or key < best[0]:
                best = (key, cand)
            got_site = True
            break
        if not got_site:
            killed.append(("%s %.0fdeg" % (why, math.degrees(
                math.atan2(u[1], u[0])) % 360), seen or "stitch/clearance"))
    if best is None:
        layers_txt = "; ".join("%s: %s" % (why, msg) for why, msg in killed[:4])
        dog = _dogleg_at(model, via, pad, r, contacts, pad_layers)
        if dog is not None:
            return dog[1], None, [dog]
        return None, "no legal site on %d rays (%d contacts) %s" % (
            len(dirs), len(contacts), layers_txt), []
    alts.sort(key=lambda kv: kv[0])
    return best[1], None, alts


def main(argv=None):
    ap = argparse.ArgumentParser()
    ap.add_argument("board")
    ap.add_argument("--apply", metavar="OUT")
    ap.add_argument("--limit", type=int, default=0)
    a = ap.parse_args(argv)

    board = open_board(a.board)
    model = Model(board)

    targets = []
    for v in model.vias:
        for p in model.pads:
            if p["net"] != v["net"] or v["drill"] <= 0:
                continue
            if not any(_on(p["layers"], L) for L in v["layers"]):
                continue
            if _poly_dist(v["x"], v["y"], p["poly"]) < v["drill"] / 2.0:
                targets.append((v, p))
                break
    print("via in pad: %d of %d vias" % (len(targets), len(model.vias)))
    if a.limit:
        targets = targets[:a.limit]

    plans, refused = [], []
    for v, p in targets:
        cand, why, alts = plan_one(model, v, p)
        if cand is not None:
            # two holes may not be handed the same little patch of board: the
            # first choice can land on another plan's new site, so walk the
            # alternatives before giving up
            tried = []
            picked = None
            clash = None
            for _key, c in [(None, cand)] + list(alts):
                if (c["x"], c["y"]) in tried:
                    continue
                tried.append((c["x"], c["y"]))
                hit = None
                for v2, _p2, c2 in plans:
                    if math.hypot(c["x"] - c2["x"], c["y"] - c2["y"]) < \
                            (v["drill"] + v2["drill"]) / 2.0 + H2H - 1e-6:
                        hit = (v2, c2)
                        break
                if hit is None:
                    picked = c
                    break
                if clash is None:
                    clash = (c, hit)
            if picked is None:
                cand = None
                # the refusal has to carry the arithmetic, not just the fact:
                # how far from the accepted site this hole's only option sits,
                # and how far two holes of these drills have to sit apart. That
                # is the difference between "no room" and "one more millimetre
                # of board and it works".
                c, (v2, c2) = clash
                gap = (v["drill"] + v2["drill"]) / 2.0 + H2H
                why = "every legal site lands on top of another hole's new " \
                      "site (first clash: %s at %.3f,%.3f; best site is " \
                      "%.3f mm away, two %.2f/%.2f mm holes need %.3f)" % (
                          v2["net"], c2["x"], c2["y"],
                          math.hypot(c["x"] - c2["x"], c["y"] - c2["y"]),
                          v["drill"], v2["drill"], gap)
            else:
                cand = picked
        if cand is None:
            refused.append((v, p, why))
            print("   REFUSED %-13s %-8s (%7.3f,%7.3f): %s"
                  % (v["net"], p["ref"], v["x"], v["y"], why))
            continue
        plans.append((v, p, cand))
        print("   plan    %-13s %-8s (%7.3f,%7.3f) -> (%7.3f,%7.3f) %.2f mm "
              "ext=%s stitch=%s held=%d%s"
              % (v["net"], p["ref"], v["x"], v["y"], cand["x"], cand["y"],
                 cand["d"], ",".join(c["layer"] for c in cand["extend"]) or "-",
                 ",".join("%s/%.2f" % (L, w) for L, w, _p in cand["stitch"]) or "-",
                 len(cand["held"]),
                 "" if "shrink_to" not in cand else "  shrink dia %.2f->%.2f"
                 % (v["r"] * 2.0, cand["shrink_to"])))

    print("planned %d, refused %d" % (len(plans), len(refused)))
    if not a.apply:
        return 0 if not refused else 1

    import pcbnew
    b = pcbnew.LoadBoard(a.board)

    def mm(v):
        return int(round(v * 1e6))

    def key(x, y):
        return (int(round(x * 1e6)), int(round(-y * 1e6)))

    # one pass over the whole track list: iterating GetTracks() again after a
    # Remove() makes the swig wrapper throw "SwigPyObject is not iterable", so
    # the index is built once and never rebuilt
    via_ix = {}
    trk_ix = {}
    for t in list(b.GetTracks()):
        net = t.GetNetname()
        if isinstance(t, pcbnew.PCB_VIA):
            q = t.GetPosition()
            via_ix[(net, q.x, q.y)] = t
            continue
        s, f = t.GetStart(), t.GetEnd()
        lid = t.GetLayer()
        # keyed by the unordered pair: which endpoint has to move is decided
        # later from the planned contact end, not from the file's point order
        trk_ix[(net, lid, (s.x, s.y), (f.x, f.y))] = t
        trk_ix[(net, lid, (f.x, f.y), (s.x, s.y))] = t

    done = 0
    for v, p, cand in plans:
        obj = via_ix.get((v["net"],) + key(v["x"], v["y"]))
        if obj is None:
            print("   MISSING via %s at %.3f,%.3f" % (v["net"], v["x"], v["y"]))
            continue
        b.BuildConnectivity()
        uc0 = b.GetConnectivity().GetUnconnectedCount(False)

        undo = []
        dead = []
        good = True
        # extend the contacts first: their endpoint moves from the old site to
        # the new one, which is the same straight line, so no corner appears
        for e in cand["extend"]:
            lid = b.GetLayerID(e["layer"])
            ka = key(e["a"][0], e["a"][1])
            kb = key(e["b"][0], e["b"][1])
            tr = trk_ix.get((v["net"], lid, ka, kb))
            if tr is None:
                print("   MISSING track %s %s for %s" % (e["layer"], e, v["net"]))
                good = False
                break
            pos = pcbnew.VECTOR2I(mm(cand["x"]), mm(-cand["y"]))
            which = "start" if (tr.GetStart().x, tr.GetStart().y) == ka else "end"
            if which == "end":
                undo.append((tr, "end", (tr.GetEnd().x, tr.GetEnd().y)))
                tr.SetEnd(pos)
            else:
                undo.append((tr, "start", (tr.GetStart().x, tr.GetStart().y)))
                tr.SetStart(pos)
            if tr.GetStart().x == tr.GetEnd().x and \
                    tr.GetStart().y == tr.GetEnd().y:
                # the endpoint slid all the way onto the far end: the piece is
                # now a zero-length leftover, so it goes away rather than
                # becoming a duplicate-copper artefact
                dead.append(tr)
        if not good:
            for tr, side, old in undo:
                (tr.SetEnd if side == "end" else tr.SetStart)(
                    pcbnew.VECTOR2I(old[0], old[1]))
            continue

        obj.SetPosition(pcbnew.VECTOR2I(mm(cand["x"]), mm(-cand["y"])))
        if "shrink_to" in cand:
            # only the body shrinks: the drill keeps its size, so the annulus
            # stays at (dia - drill)/2 which the planner already checked
            obj.SetWidth(mm(cand["shrink_to"]))
        new = []
        for L, w, pts in cand["stitch"]:
            for i in range(len(pts) - 1):
                seg = pcbnew.PCB_TRACK(b)
                seg.SetStart(pcbnew.VECTOR2I(mm(pts[i][0]), mm(-pts[i][1])))
                seg.SetEnd(pcbnew.VECTOR2I(mm(pts[i + 1][0]),
                                           mm(-pts[i + 1][1])))
                seg.SetWidth(mm(w))
                seg.SetLayer(b.GetLayerID(L))
                seg.SetNet(obj.GetNet())
                b.Add(seg)
                new.append(seg)

        b.BuildConnectivity()
        uc1 = b.GetConnectivity().GetUnconnectedCount(False)
        if uc1 > uc0:
            for sg in new:
                b.Remove(sg)
            obj.SetPosition(pcbnew.VECTOR2I(mm(v["x"]), mm(-v["y"])))
            for tr, side, old in undo:
                (tr.SetEnd if side == "end" else tr.SetStart)(
                    pcbnew.VECTOR2I(old[0], old[1]))
            print("   REVERTED %-13s %-8s: unconnected %d -> %d"
                  % (v["net"], p["ref"], uc0, uc1))
            continue
        for tr in dead:
            b.Remove(tr)
        done += 1
        print("   moved   %-13s %-8s -> (%7.3f,%7.3f) %d stitch(es), %d extended%s"
              % (v["net"], p["ref"], cand["x"], cand["y"], len(new),
                 len(cand["extend"]),
                 "" if "shrink_to" not in cand else ", dia -> %.2f"
                 % cand["shrink_to"]))

    b.BuildConnectivity()
    print("moved %d of %d" % (done, len(plans)))
    b.Save(a.apply)
    print("saved %s" % a.apply)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
