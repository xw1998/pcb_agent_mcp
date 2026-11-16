#!/usr/bin/env python3
"""Which step of this board is actually wrong, and what may I judge at all?

The gap this file exists to close is not a rule, it is an ORDER.  Every earlier
tool in this repo answers "does this board break rule X".  None of them answers
the question that actually decides whether a board can be good:

    what must be finished before rule X is even meaningful?

Layout is not a bag of independent checks, it is a dependency chain.  A board
whose parts point away from their half-hole row cannot be rescued by better
routing: 36 nets have to cross the board and every corridor decision downstream
is a consequence.  A board that has no snake corridor beside a bus cannot be
rescued by a better meander: the length budget was spent before anybody drew a
tooth.  Judging the meander first reads "35/39 short" and blames the meander.

So this file is a linear playbook.  Each step carries

    why        the physical reason the step exists
    gate       a measurable check, computed from the board itself
    requires   the step before it

and the evaluation is deliberately chain-strict: a step is `fail` only if every
step before it passed, otherwise it is `blocked` - measured, printed, and NOT
judged.  That is the whole point.  "Step 6 fails" and "step 6 fails because step
2 was never done" are different sentences, and the second one is actionable.

    plan_sequence.py --board B.kicad_pcb            # this board's chain
    plan_sequence.py --corpus DIR                   # do good boards pass? (rules)

The corpus mode is the other half of the user's original ask: collect good
boards and confirm the rules are reasonable.  A step that fails on most of the
corpus is a rule that is too strict, not a corpus that is broken - the report
says so instead of hiding it.  Both directions matter: a rule that only my board
fails is a real defect, a rule that everything fails is my mistake.

Nothing here writes a board, moves a part or routes a net.
"""
import argparse
import glob
import io
import json
import math
import os
import subprocess
import sys
import time

def io_open(path, newline=None):
    """Text open with an explicit encoding - the corpus holds GBK-era comments."""
    return io.open(path, "r", encoding="utf-8", newline=newline)

HERE = os.path.dirname(os.path.abspath(__file__))
REPO = os.path.dirname(HERE)
sys.path.insert(0, HERE)
sys.path.insert(0, os.path.join(REPO, "src"))

#: Steps that must be settled before the next one can be judged at all.  The
#: order is the argument of this file, so it is written down once, here:
#:
#:   frame    you cannot place anything until the edge and its keepout exist
#:   floorplan  a part facing the wrong way makes every later net a detour
#:   stackup  layer count and reference planes decide how many corridors exist
#:   fanout   a BGA escape decides where those corridors can start
#:   power    planes are shaped around the fanout vias, not the other way round
#:   bus      a bus corridor + its length budget must exist before any copper
#:   route    signal copper is the last thing that gets drawn
#:   closure  only now do "unconnected / DRC / length" mean anything
ORDER = ("frame", "floorplan", "stackup", "fanout", "power", "bus", "route",
         "closure")

#: A crossing net is one whose terminal sits on the far half of the board from
#: the part that owns it.  Some are unavoidable; the gate only fires when the
#: share gets large enough that the floorplan, not the router, is the cause.
CROSS_FRAC_MAX = 0.15

#: How much of the board's area one adjacent plane layer must cover before it
#: counts as a reference plane.  Below this a return current has to detour.
PLANE_COVER_MIN = 0.25

#: A ball/pin counts as escaped if a via sits this far beyond its pad half-size,
#: or if a track endpoint lands on the pad.  0.30 mm clears the 0.10 mm escape
#: stub plus a 0.20/0.10 via radius at this board's rules.
ESCAPE_REACH_MM = 0.30

#: A power net needs either a plane or this many segments before "the PDN
#: exists".  Four is the point where a net is a routed tree, not a stub.
POWER_SEG_MIN = 4

#: Nets whose length has a budget.  Matched case-insensitively as substrings; a
#: board with none of these has no bus step (reported as n/a, not as pass).
BUS_PATTERNS = ("DDR",)

#: A footprint this big is an array package: its escape is a real design step.
ARRAY_PADS_MIN = 64

#: A track end this far outside the pad still counts as leaving from the pad.
ESCAPE_TOUCH_MM = 0.06

#: The crossing share the floorplan step is judged at.  The step also reports the
#: same board at every other value, because a threshold with no sensitivity
#: reading is a number somebody picked.
CROSS_SWEEP = (0.10, 0.15, 0.20, 0.25, 0.30)

#: Meander tooth heights the bus step is reported at.  Judged at --amplitude
#: (0.30); the other two exist so "47/49 short" cannot be read as "the number is
#: an artefact of the tooth height I happened to choose".
AMPLITUDE_SWEEP = (0.20, 0.25, 0.30)

#: One board gets this long inside its own process before the run calls it a
#: hang.  Measured worst case on the 12-board corpus is 125 s (a real file that
#: spins inside `pcbnew.LoadBoard`); 180 keeps a slow-but-honest board and cuts
#: a hung one.
BOARD_TIMEOUT = 180.0

#: Written by the child process in front of its JSON.  pcbnew prints
#: "Adding duplicate image handler for ..." to stdout, so the parent cannot
#: assume stdout is clean JSON.
_EMIT = "<<<PLAN_SEQUENCE_REP>>>"

#: What a corpus directory may hold.  The gate chain reads the *canonical* board,
#: not a `pcbnew` object, so a 立创EDA container (`.epro`/`.eprj`) is judgeable
#: too - only the two steps that need pcbnew (`bus` needs a meander search on the
#: raw board, `closure` needs `GetUnconnectedCount`) come back `n/a` with the
#: reason written down.  Leaving them out would keep the rules validated on
#: nothing but KiCad demo boards while 20 real hand-drawn boards sat unread.
CORPUS_SUFFIXES = (".kicad_pcb", ".epro", ".eprj", ".eprj2")


# --------------------------------------------------------------------------- #
# geometry helpers (canonical, mm, y up)
# --------------------------------------------------------------------------- #
def shoelace(points):
    """Signed area of a closed polygon; absolute value is what we use."""
    if not points or len(points) < 3:
        return 0.0
    total = 0.0
    for i in range(len(points)):
        x1, y1 = points[i][0], points[i][1]
        x2, y2 = points[(i + 1) % len(points)][0], points[(i + 1) % len(points)][1]
        total += x1 * y2 - x2 * y1
    return abs(total) / 2.0


def pt_seg(px, py, x1, y1, x2, y2):
    dx, dy = x2 - x1, y2 - y1
    L2 = dx * dx + dy * dy
    if L2 < 1e-12:
        return math.hypot(px - x1, py - y1)
    u = max(0.0, min(1.0, ((px - x1) * dx + (py - y1) * dy) / L2))
    return math.hypot(px - (x1 + u * dx), py - (y1 + u * dy))


def seg_seg(a, b):
    """Distance between two segments, a=((x,y),(x,y)), b likewise."""
    (ax1, ay1), (ax2, ay2) = a
    (bx1, by1), (bx2, by2) = b
    d = min(pt_seg(ax1, ay1, bx1, by1, bx2, by2),
            pt_seg(ax2, ay2, bx1, by1, bx2, by2),
            pt_seg(bx1, by1, ax1, ay1, ax2, ay2),
            pt_seg(bx2, by2, ax1, ay1, ax2, ay2))
    # a proper crossing is distance zero, which no corner distance catches
    def cross(ox, oy, px, py, qx, qy):
        return (px - ox) * (qy - oy) - (py - oy) * (qx - ox)
    d1 = cross(bx1, by1, bx2, by2, ax1, ay1)
    d2 = cross(bx1, by1, bx2, by2, ax2, ay2)
    d3 = cross(ax1, ay1, ax2, ay2, bx1, by1)
    d4 = cross(ax1, ay1, ax2, ay2, bx2, by2)
    if ((d1 > 0) != (d2 > 0)) and ((d3 > 0) != (d4 > 0)):
        return 0.0
    return d


def outline_segments(board):
    """Every board-edge segment, with the ring CLOSED.

    KiCad usually repeats the first point at the end of a contour, but not
    always, and nothing guarantees it for a board built by this repo.  When the
    closing edge is missing the edge-clearance rule loses one whole side of the
    board without saying so (measured: a 45x45 board read 19.9 mm instead of
    0.05 mm to the left edge), which is a false green, i.e. the worst kind.
    """
    out = []
    for contour in board.outline or []:
        n = len(contour)
        if n < 2:
            continue
        for i in range(n - 1):
            out.append(((contour[i][0], contour[i][1]),
                        (contour[i + 1][0], contour[i + 1][1])))
        first, last = contour[0], contour[-1]
        if abs(first[0] - last[0]) > 1e-6 or abs(first[1] - last[1]) > 1e-6:
            out.append(((last[0], last[1]), (first[0], first[1])))
    return out


def board_area(board):
    if not board.outline:
        return 0.0
    area = shoelace(board.outline[0])
    for cut in board.cutouts or []:
        area -= shoelace(cut)
    return max(0.0, area)


def pad_wh(pad):
    """A pad's (w, h) in mm, never None.

    A custom (polygon) pad carries a point list instead of a size, so the 立创EDA
    adapter stores `w`/`h` as null.  `max(None, None)` raised inside `gate_frame`
    and turned the whole board into `error` (measured: 13 such pads on
    1dc6aafb…__52x_), so the extent is taken from the polygon itself.
    """
    w, h = pad.get("w"), pad.get("h")
    if w is not None and h is not None:
        return float(w), float(h)
    poly = pad.get("polygon") or []
    if poly:
        xs = [float(pt[0]) for pt in poly]
        ys = [float(pt[1]) for pt in poly]
        if w is None:
            w = max(xs) - min(xs)
        if h is None:
            h = max(ys) - min(ys)
    return float(w or 0.0), float(h or 0.0)

def pad_half(pad):
    """Half of the pad's larger side, in mm (the `copper_items` convention)."""
    w, h = pad_wh(pad)
    return max(w, h) / 2.0

def copper_items(board, with_pads=True):
    """Every copper object as (kind, net, layer, half, geom) in canonical mm.

    Pads are inflated to `max(w,h)/2` on purpose: for an edge-clearance lower
    bound a slightly-too-big pad can only under-report margin, so the number we
    print never flatters the board.

    `with_pads=False` is what the edge gate uses.  A castellated ring (this
    board's J1) is 140 pads whose copper is *meant* to be cut by the board edge;
    counting them as edge violations reads 268 offenders on a board whose DRC is
    clean.  The board-edge rule is about tracks and vias - pads on the edge are a
    process decision, and they are reported separately as `castellated_pads`.
    """
    items = []
    for t in board.tracks:
        x1, y1 = t["start"]
        x2, y2 = t["end"]
        items.append({"kind": "seg", "net": t["net"], "layer": t["layer"],
                      "half": t["width"] / 2.0, "geom": (x1, y1, x2, y2)})
    for v in board.vias:
        items.append({"kind": "disc", "net": v["net"], "layer": "*",
                      "half": v["diameter"] / 2.0,
                      "geom": (v["x"], v["y"], v["x"], v["y"])})
    if not with_pads:
        return items
    for p in board.pads():
        half = pad_half(p)
        layers = p["layers"] or []
        items.append({"kind": "disc", "net": p["net"],
                      "layer": layers[0] if len(layers) == 1 else "*",
                      "half": half, "geom": (p["x"], p["y"], p["x"], p["y"])})
    return items


def item_segment(it):
    g = it["geom"]
    return ((g[0], g[1]), (g[2], g[3]))


def all_nets(board):
    """Every net name that appears anywhere on the board.

    `Board.nets` is the code->name table and it is empty unless `resolve_nets()`
    ran, so reading it silently yields zero power nets - a rule that then passes
    because it found nothing to check.
    """
    names = set()
    for t in board.tracks:
        if t["net"]:
            names.add(t["net"])
    for v in board.vias:
        if v["net"]:
            names.add(v["net"])
    for p in board.pads():
        if p["net"]:
            names.add(p["net"])
    for z in board.zones or []:
        if z.get("net"):
            names.add(z["net"])
    return names


def castellated_nets(board):
    """Nets that land on a pad whose copper is cut by the board edge.

    A castellation is a half hole: the pad is meant to be sliced by the edge, and
    the track that feeds it must reach the edge too.  Exempting those nets is not
    leniency, it is the only way the edge rule can be stated at all - this board
    has 268 such pads and 128 tracks/vias inside 0.30 mm of the edge, every one of
    them a castellation feed.
    """
    segs = outline_segments(board)
    if not segs:
        return set(), []
    out, pts = set(), []
    for p in board.pads():
        half = pad_half(p)
        d = min(pt_seg(p["x"], p["y"], a[0], a[1], b[0], b[1])
                for a, b in segs) - half
        if d < 0.0:
            out.add(p["net"])
            pts.append((p["x"], p["y"]))
    return out, pts


#: A track feeding a castellated pad is exempt from the edge rule only while it
#: is still inside this reach of that pad.  Exempting the whole net instead is a
#: no-op: this board has 98 nets and all 98 reach the castellation ring, so the
#: rule would stop existing (measured: exemption by net leaves nothing to check,
#: exemption by stub keeps 0.55 mm as the real minimum).
FEED_REACH_MM = 0.65

#: The only copper-to-edge distance that is a *physical* error: closer than this
#: and the etchant/route tolerance can open the copper or short it.  Anything
#: between this and a house rule (0.30 mm here) is a policy, not a defect - the
#: corpus contains shipped boards at 0.24 mm (stickhub), so a corpus cannot
#: convict them of a physical fault.
EDGE_HARD_MM = 0.10


def edge_clearance(board, items, exempt=(), exempt_pts=()):
    """Smallest copper-to-board-edge distance, and every item under it.

    The comparison is centreline distance minus the item's own half width, so a
    0.10 mm track 0.35 mm from the edge reads 0.30.  Any outline segment counts:
    the board edge is a closed ring and a cutout is an edge too.
    """
    segs = outline_segments(board)
    if not segs:
        return None, []
    worst = None
    under = []
    exempt = set(exempt or ())
    pts = list(exempt_pts or ())
    for it in items:
        if it["net"] in exempt and pts:
            g = it["geom"]
            near_feed = any(math.hypot(px - g[0], py - g[1]) <= FEED_REACH_MM
                            or math.hypot(px - g[2], py - g[3]) <= FEED_REACH_MM
                            for px, py in pts)
            if near_feed:
                continue
        seg = item_segment(it)
        best = min(seg_seg(seg, s) for s in segs)
        gap = best - it["half"]
        if worst is None or gap < worst:
            worst = gap
        if gap < 0.0:
            under.append({"net": it["net"], "gap_mm": round(gap, 4)})
    return worst, under


# --------------------------------------------------------------------------- #
# gates: each returns (measured dict, ok, note)
# --------------------------------------------------------------------------- #
def gate_frame(board, ctx):
    cast, cast_pts = castellated_nets(board)
    items = copper_items(board, with_pads=False)
    worst, under = edge_clearance(board, items, exempt=cast, exempt_pts=cast_pts)
    pad_worst, pad_under = edge_clearance(board, copper_items(board))
    rule = (board.rules or {}).get("clearance", {}).get("min_copper_edge_clearance")
    need = rule if rule else 0.30            # house number, reported not judged
    closed = bool(board.outline) and len(board.outline[0]) >= 4
    open_note = (board.source or {}).get("outline_note")
    # No closed edge means no distance to measure, not a copper fault.  Calling it
    # `fail` let three real 立创EDA boards (shipped products) convict the rule in a
    # corpus run - reported as "好板子多数不过 => 这条规则是错的" when the truth is
    # "板边没连成闭环，这一关无法测量".  Whether an outline closes is DRC's entry
    # (`malformed outline`); this gate only judges copper-to-edge distance.
    if not closed:
        ok = None
    elif worst is None and not under:
        # nothing routed / no via at all: the "copper near the edge" question has
        # no object to measure.  Vacuously "passing" would be a false green if the
        # extractor ever dropped tracks, so report n/a instead of pass.
        ok = None
    else:
        ok = bool(worst >= EDGE_HARD_MM - 1e-9 and not under)
    if ok is None and not closed:
        note = ("板框没闭合（%s）：没有板边，铜到板边的距离无从测量——这一条是"
                "「测不出」，不是「不合格」" % (open_note or "没有任何闭合板框"))
    elif ok is None:
        note = "这块板上没有走线也没有过孔，板边间距无从测量"
    elif not ok:
        note = ("铜离板边不到 %.2f mm（蚀刻公差内就会破）：这一条不是「以后会出问题」，"
                "是已经存在" % EDGE_HARD_MM)
    else:
        note = "板框闭合，铜离板边 %.4f mm ≥ 物理下限 %.2f mm" % (worst, EDGE_HARD_MM)
        if worst < need - 1e-9:
            note += ("；比本项目 %.2f mm 的用户规则紧 %.4f mm（house，不判）"
                     % (need, need - worst))
    return {"outline_contours": len(board.outline or []),
            "outline_closed": closed,
            "outline_note": open_note,
            "edge_min_mm": None if worst is None else round(worst, 4),
            "edge_needed_mm": round(need, 4),
            "edge_hard_mm": EDGE_HARD_MM,
            "edge_needed_origin": "house" if rule else "house-default",
            "tracks_vias_under": len(under),
            "castellated_nets": len(cast),
            "castellated_pads": len(pad_under),
            "pad_edge_min_mm": None if pad_worst is None else round(pad_worst, 4)}, ok, note

def gate_floorplan(board, ctx):
    import plan_floorplan
    # No closed board edge means there are no four sides, so "is this
    # connector on the wrong edge" has no object to measure.  That is the
    # same unmeasurable case as the frame gate above, not a copper fault -
    # but plan_floorplan raises for it, and a raise reads as `error` and
    # stops the whole chain.  Measured: three shipped 立创EDA boards
    # (5c71f32e / 99593ab6 / fadd8cdd) turned floorplan into `error` in a
    # corpus run purely because their outline never closes.
    if board is not None and not (getattr(board, "outline", None) or []):
        return {"crossing_nets": 0, "nets_priced": 0,
                "outline_note": (getattr(board, "source", None) or {}).get(
                    "outline_note")}, None, \
               ("这块板的板边没有连成闭环，没有四条边可量，「朝向错」这一问测不出"
                "（与 frame 同一类：板框闭不闭合是 DRC 的入口，不是这一关的判据）")
    rep = plan_floorplan.analyse(ctx["path"])
    priced = rep.get("nets_priced") or 0
    cross = rep.get("crossing_total") or 0
    if not priced:
        return {"crossing_nets": 0, "nets_priced": 0,
                "note": "no net reaches a board-edge terminal"}, None, \
               "这块板上没有到板边的网（没有接插件半孔），「朝向错」这一问不适用"
    frac = (cross / float(priced)) if priced else 0.0
    overlaps = rep.get("overlaps") or []
    saved = rep.get("airwire_saved_frac") or 0.0
    ok = (not overlaps) and frac <= CROSS_FRAC_MAX
    sweep = {("%.2f" % t): ("pass" if frac <= t else "fail") for t in CROSS_SWEEP}
    return {"crossing_nets": cross, "nets_priced": priced,
            "crossing_frac": round(frac, 4), "cross_frac_max": CROSS_FRAC_MAX,
            "by_threshold": sweep,
            "airwire_mm_now": rep.get("airwire_mm_now"),
            "airwire_mm_best": rep.get("airwire_mm_best"),
            "airwire_saved_frac": saved,
            "hub_group": rep.get("hub_group"),
            "proposal_overlaps": len(overlaps),
            "overlap_basis": rep.get("overlap_basis"),
            "crossing_by_group": rep.get("crossing_by_group")}, ok, (
        "%d 条网要跨板（占 %d 条可定价网的 %.0f%%），这些网无论后面怎么走都是绕大圈"
        % (cross, priced, frac * 100) if not ok else "没有跨板网堆积")


def gate_stackup(board, ctx):
    area = board_area(board)
    stack = [s["name"] for s in (board.stackup or [])]
    layers = [n for n in stack if n.endswith(".Cu")] or ["F.Cu", "B.Cu"]
    cover = {}
    for z in board.zones or []:
        pts = z.get("outline") or []
        a = shoelace(pts)
        for L in (z.get("layers") or []):
            cover[L] = cover.get(L, 0.0) + a
    signal = sorted({t["layer"] for t in board.tracks})
    holes = []
    if area > 0:
        for i, name in enumerate(layers):
            if name not in signal:
                continue
            adj = [layers[j] for j in (i - 1, i + 1) if 0 <= j < len(layers)]
            best = max([min(1.0, cover.get(a, 0.0) / area) for a in adj] or [0.0])
            if best < PLANE_COVER_MIN:
                holes.append({"layer": name,
                              "adjacent": adj,
                              "cover": round(best, 3)})
    # A 2-layer board has no inner plane to reference at all: it returns through
    # its own pours on F/B.  Demanding "adjacent plane cover >= 25%" would fail
    # every 2005-era 2-layer board in the corpus, which is a category error, not
    # a bad board.  So: fewer than 4 copper layers => n/a (measured, not judged).
    ok = None if len(layers) < 4 else (bool(layers) and not holes)
    return {"copper_layers": len(layers),
            "signal_layers": signal,
            "plane_cover": {k: round(min(1.0, v / area), 3) for k, v in cover.items()}
                            if area else {},
            "reference_holes": holes,
            "need_cover": PLANE_COVER_MIN}, ok, (
        ("%d 层板没有内层平面可参考，靠 F/B 铺铜回流 => 本关不适用"
         % len(layers)) if ok is None else
        (("%d 个走线层没有相邻参考平面（覆盖率 < %.0f%%）"
          % (len(holes), PLANE_COVER_MIN * 100)) if not ok
         else "每个走线层都有参考平面"))


def _in_poly(x, y, poly):
    """Ray casting.  Board pours are simple polygons; a concave one is handled."""
    c = False
    n = len(poly)
    for i in range(n):
        x1, y1 = poly[i][0], poly[i][1]
        x2, y2 = poly[(i + 1) % n][0], poly[(i + 1) % n][1]
        if (y1 > y) != (y2 > y) and x < (x2 - x1) * (y - y1) / (y2 - y1) + x1:
            c = not c
    return c

def zone_cover(board):
    """(net, layer-set, polygon) for every pour, so a pad can be found *poured*
    rather than only *tracked*.

    This was a real defect: on cm5_minima 45% of a 102-pad module read as "not
    fanned out" because its pads go straight into a same-net pour and never see a
    track end.  A good board was being called unfinished.
    """
    out = []
    for z in board.zones or []:
        net = (z.get("net") or "").strip()
        poly = z.get("outline") or []
        if not net or len(poly) < 3 or z.get("keepout"):
            continue
        out.append((net, set(z.get("layers") or []), poly))
    return out

def _poured(pad, zones):
    for net, layers, poly in zones:
        if net != pad.get("net"):
            continue
        pl = set(pad.get("layers") or [])
        if pl and layers and not (pl & layers):
            continue
        if _in_poly(pad["x"], pad["y"], poly):
            return True
    return False

def _in_pad(pad, x, y, extra=0.0):
    """Is (x,y) inside the pad, grown by `extra`, in the PAD's own frame?

    Rotation matters and was missing: on cm5_minima the module's 0.20 x 0.70 pads
    are rotated, so an axis-aligned box test called 30 of 100 fanned-out balls
    "not fanned out".  A good board was being convicted by a coordinate bug.
    """
    dx, dy = x - pad["x"], y - pad["y"]
    th = math.radians(pad.get("rotation") or 0.0)
    c, s = math.cos(th), math.sin(th)
    u, v = dx * c + dy * s, -dx * s + dy * c
    pw, ph = pad_wh(pad)
    return abs(u) <= pw / 2.0 + extra and abs(v) <= ph / 2.0 + extra

def _seg_in_pad(pad, x1, y1, x2, y2, extra=0.0):
    """Does the segment touch the pad rectangle (grown by `extra`)?

    Endpoints alone are not enough: an FPC connector pad routinely has a wide
    trace running THROUGH it, with both ends far outside.  Endpoint-only testing
    called 30 of those pads "not fanned out" on cm5_minima - a good board read as
    unfinished because of how the copper was drawn, not because of a defect.
    Liang-Barsky clip in the pad's own frame, so rotation is handled exactly.
    """
    th = math.radians(pad.get("rotation") or 0.0)
    c, s = math.cos(th), math.sin(th)

    def local(x, y):
        dx, dy = x - pad["x"], y - pad["y"]
        return dx * c + dy * s, -dx * s + dy * c

    ax, ay = local(x1, y1)
    bx, by = local(x2, y2)
    pw, ph = pad_wh(pad)
    hx = pw / 2.0 + extra
    hy = ph / 2.0 + extra
    t0, t1 = 0.0, 1.0
    dx, dy = bx - ax, by - ay
    for pp, qq in ((-dx, ax + hx), (dx, hx - ax), (-dy, ay + hy), (dy, hy - ay)):
        if abs(pp) < 1e-12:
            if qq < 0:
                return False
            continue
        t = qq / pp
        if pp < 0:
            if t > t1:
                return False
            t0 = max(t0, t)
        else:
            if t < t0:
                return False
            t1 = min(t1, t)
    return t0 <= t1

def gate_fanout(board, ctx):
    place = ctx["place"]
    hole_rule = [r for r in place["rules"] if r["id"] == "hole_in_pad"]
    hole_n = hole_rule[0]["measured"] if hole_rule else 0
    vias = [(v["x"], v["y"]) for v in board.vias]
    ends = []
    for t in board.tracks:
        ends.append(tuple(t["start"]))
        ends.append(tuple(t["end"]))
    zones = zone_cover(board)
    # A net with exactly one pad has nothing to connect to, so its pad needs no
    # way out.  (Measured on cm5_minima: 41 module/connector pads sit on
    # single-pad nets - unused module pins - and were being counted as
    # "not fanned out" on a board whose unconnected count is 0.)
    pad_count = {}
    for fp in board.footprints:
        for p in fp.get("pads") or []:
            n = (p.get("net") or "").strip()
            if n:
                pad_count[n] = pad_count.get(n, 0) + 1
    arrays = []
    for fp in board.footprints:
        pads = fp.get("pads") or []
        if len(pads) < ARRAY_PADS_MIN:
            continue
        esc = 0
        reach_pad = 0
        needs = [p for p in pads if (p.get("net") or "").strip()]
        for p in pads:
            if not (p.get("net") or "").strip():
                esc += 1                    # an unconnected ball needs no way out
                continue
            if p.get("hole_mm"):
                esc += 1                    # a through-hole is its own escape
                continue
            if pad_count.get((p.get("net") or "").strip(), 0) <= 1:
                esc += 1                    # single-pad net: nothing to connect
                continue
            r = pad_half(p) + ESCAPE_REACH_MM
            got = any((x - p["x"]) ** 2 + (y - p["y"]) ** 2 <= r * r for x, y in vias)
            if not got:
                got = any(_in_pad(p, x, y, ESCAPE_TOUCH_MM) for x, y in ends)
            if not got:
                got = any(_seg_in_pad(p, t["start"][0], t["start"][1],
                                      t["end"][0], t["end"][1], ESCAPE_TOUCH_MM)
                          for t in board.tracks)
            if not got and _poured(p, zones):
                got = True                # poured, not tracked: still has a way out
            if got:
                esc += 1
            else:
                reach_pad += 1
        arrays.append({"ref": fp["ref"], "pads": len(pads),
                       "with_net": len(needs), "escaped": esc,
                       "un_escaped": reach_pad,
                       "frac": round(esc / float(len(pads)), 4)})
    arr_bad = [a for a in arrays if a["frac"] < 0.98]
    # Only the escape side is judged here.  `hole_in_pad` is a process policy of
    # THIS project, not a law of boards: the corpus contains shipped boards with
    # 89 of them (royalblue54L), so it is reported and tagged `house`, never
    # allowed to convict a good board.
    ok = not arr_bad
    bits = []
    if arr_bad:
        bits.append("%d 个阵列封装没扇出完" % len(arr_bad))
    note = ("；".join(bits) if not ok else "阵列封装全部扇出")
    if hole_n:
        note += "（house 判据：盘中孔 %d 个，不参与本关判定）" % hole_n
    return {"arrays": arrays, "arrays_incomplete": len(arr_bad),
            "hole_in_pad": hole_n, "hole_in_pad_origin": "house"}, ok, note


def gate_power(board, ctx):
    place = ctx["place"]
    dec = [r for r in place["rules"] if r["id"] == "decoupling_far"]
    hard = 0
    if dec:
        it = (dec[0].get("items") or [])
        hard = len([r for r in it if r.get("severity") == "hard"])
    zone_nets = set()
    for z in board.zones or []:
        if z.get("net"):
            zone_nets.add(z["net"])
    segs = {}
    widths = {}
    for t in board.tracks:
        segs[t["net"]] = segs.get(t["net"], 0) + 1
        widths.setdefault(t["net"], []).append(t["width"])
    def base(name):
        return (name or "").lstrip("/").upper()
    power = [n for n in all_nets(board)
             if not base(n).startswith("UNCONNECTED")
             and (base(n).startswith("+") or "VDD" in base(n) or "VCC" in base(n))]
    # What CAN be judged without knowing the current: a power net that has a pad
    # and no copper at all is undelivered, full stop.  What CANNOT: "3 segments
    # is too few" - measured, this repo's own board delivers 9 power nets on
    # 0.10 mm tracks with 29..91 segments each, while a good 2002 demo board uses
    # 0.8 mm tracks and 40 segments.  Segment count and width are proxies for a
    # current nobody has declared, so they are reported and not judged.
    dead, thin = [], []
    for n in sorted(power):
        if n in zone_nets or segs.get(n, 0) >= POWER_SEG_MIN:
            if n not in zone_nets and widths.get(n):
                thin.append({"net": n, "segments": segs.get(n, 0),
                             "min_width_mm": round(min(widths[n]), 3)})
            continue
        if not segs.get(n, 0):
            dead.append({"net": n, "segments": 0, "plane": n in zone_nets})
        else:
            thin.append({"net": n, "segments": segs.get(n, 0),
                         "min_width_mm": round(min(widths.get(n) or [0]), 3)})
    ok = not dead
    return {"power_nets": len(power), "planes": sorted(zone_nets),
            "undelivered_power_nets": dead,
            "thin_power_nets": thin, "thin_origin": "house",
            "decoupling_hard": hard, "decoupling_origin": "house"}, ok, (
        "%d 个电源网只有焊盘没有铜（彻底没交付）" % len(dead) if not ok
        else ("电源网都至少有一条铜"
              + ("（house 判据：%d 条偏细、%d 个去耦超距，不参与本关判定）"
                 % (len(thin), hard) if (thin or hard) else "")))


def gate_bus(board, ctx):
    """Length budget: is there room beside the bus for the copper it still owes?"""
    try:
        import pcbnew                                          # noqa: F401
    except ImportError:
        return {"skipped": "pcbnew unavailable - run under KiCad's python"}, None, \
               "这条要读铜的几何，必须在 KiCad 自带 python 下跑"
    # Cheap pre-filter on the canonical net names FIRST: if no net matches, this
    # step is n/a and there is no reason to pay for a pcbnew load.
    names = [n for n in all_nets(board) if any(p in n.upper() for p in BUS_PATTERNS)]
    if not names:
        return {"skipped": "no net matched %r" % (BUS_PATTERNS,)}, None, \
               "这块板上没有总线网，这一关不适用"
    raw = _raw(ctx)
    if raw is None:
        return {"skipped": "pcbnew could not load: %s" % ctx.get("raw_error")}, None, \
               "pcbnew 读不动这块板，布线后的空间无从测量"
    import plan_meander
    rep = plan_meander.analyse(raw, BUS_PATTERNS,
                              ctx.get("amplitude", 0.30),
                              clr=ctx.get("clearance", 0.10),
                              group_by="prefix")
    if rep.get("error") or not rep.get("rows"):
        return {"skipped": rep.get("error", "no bus nets")}, None, \
               "这块板上没有总线网，这一关不适用"
    rows = rep["rows"]
    short = [r for r in rows if not r["fits"]]
    # Same board, three tooth heights.  A judgement that only holds at one of
    # them is a judgement about the number I typed, not about the board.
    sweep = {}
    judged_amp = ctx.get("amplitude", 0.30)
    for amp in AMPLITUDE_SWEEP:
        if abs(amp - judged_amp) < 1e-9:
            sweep["%.2f" % amp] = "%d/%d" % (len(short), len(rows))
            continue
        try:
            alt = plan_meander.analyse(raw, BUS_PATTERNS, amp,
                                      clr=ctx.get("clearance", 0.10),
                                      group_by="prefix")
            alt_rows = alt.get("rows") or []
            sweep["%.2f" % amp] = "%d/%d" % (
                len([r for r in alt_rows if not r["fits"]]), len(alt_rows))
        except Exception as exc:                               # noqa: BLE001
            sweep["%.2f" % amp] = "error: %r" % exc
    by_group = {}
    for r in rows:
        g = by_group.setdefault(r["group"], {"nets": 0, "short": 0,
                                            "target_mm": r["group_target_mm"]})
        g["nets"] += 1
        if not r["fits"]:
            g["short"] += 1
    ok = not short
    return {"grouping": ("by net-name prefix; a DDR lane is not resolved, so `need` "
                        "is an upper bound (the lane spread is smaller)"),
            "nets": len(rows), "groups": len(by_group), "not_fitting": len(short),
            "per_group_short": {k: "%d/%d" % (v["short"], v["nets"])
                                for k, v in sorted(by_group.items())},
            "worst": [{"net": r["net"], "group": r["group"],
                       "need_mm": r["need_mm"], "room_short_mm": r.get("room_short_mm")}
                      for r in sorted(short, key=lambda r: -(r.get("room_short_mm") or 0))[:5]],
            "amplitude_mm": rep["amplitude_mm"],
            "gain_per_mm": rep["gain_per_mm"],
            "short_by_amplitude": sweep}, ok, (
        "%d/%d 条总线在布线后找不到 %0.2f mm 的蛇形带 ⇒ 长度预算在布线阶段就没地方兑现"
        % (len(short), len(rows), ctx.get("amplitude", 0.30)) if not ok
        else "总线旁都留着蛇形带")


def gate_route(board, ctx):
    shape = ctx["shape"]
    blocking = [r for r in shape["rules"] if r["blocking"] and r["measured"]]
    ok = not blocking
    return {"shape_verdict": shape["verdict"],
            "blocking": [{"id": r["id"], "measured": r["measured"]} for r in blocking],
            "square_corner": next((r["measured"] for r in shape["rules"]
                                   if r["id"] == "square_corner"), None)}, ok, (
        "形状闸门不过：" + ", ".join("%s %d" % (r["id"], r["measured"]) for r in blocking)
        if not ok else "45°/锐角/重复铜/死端全 0")


def gate_closure(board, ctx):
    shape, place = ctx["shape"], ctx["place"]
    hole = next((r["measured"] for r in place["rules"]
                 if r["id"] == "hole_in_pad"), 0)
    unconn, why = unconnected_count(ctx["path"])
    if unconn is None:
        # Measured, printed, deliberately not judged - a load failure must never
        # read as "0 unconnected".
        return {"skipped": why, "hole_in_pad": hole,
                "shape_verdict": shape["verdict"]}, None, why
    # The shape verdict is NOT part of this step.  Closure asks "is the netlist
    # actually connected"; the shape rules are house style, and mixing them in
    # made 10 of 12 good boards read as "not closed" when their unconnected count
    # was 0.  One step, one claim.
    ok = (unconn == 0)
    extra = {}
    if ctx.get("drc"):
        # Same reader board_loop.py uses, so the two tools cannot drift into
        # "30 here, 41 there" for the same file.  Opt-in: it costs a kicad-cli
        # run per board and it never decides this step.
        try:
            import board_loop
            got = board_loop.drc(os.path.abspath(ctx["path"]),
                                 os.path.abspath(ctx["path"]) + ".drc.json")
            extra = {"drc_real": got.get("real"),
                     "drc_artefact": got.get("artefact"),
                     "drc_by_type": got.get("by_type"),
                     "drc_origin": "reported only, never judged"}
        except Exception as exc:                               # noqa: BLE001
            extra = {"drc_error": repr(exc)}
    if extra:
        return dict({"unconnected": unconn, "hole_in_pad": hole,
                     "hole_in_pad_origin": "house",
                     "shape_verdict_reported_only": shape["verdict"]}, **extra), ok, (
            "还有 %d 条网没连通" % unconn if not ok else "网表全连通")
    return {"unconnected": unconn, "hole_in_pad": hole,
            "hole_in_pad_origin": "house",
            "shape_verdict_reported_only": shape["verdict"]}, ok, (
        "还有 %d 条网没连通" % unconn if not ok
        else ("网表全连通" + ("（house 判据：盘中孔 %d，不参与本关判定）" % hole
                              if hole else "")))


GATES = {
    "frame": (gate_frame, "板框与禁布"),
    "floorplan": (gate_floorplan, "器件落位与朝向"),
    "stackup": (gate_stackup, "叠层与参考平面"),
    "fanout": (gate_fanout, "封装扇出"),
    "power": (gate_power, "电源交付与去耦"),
    "bus": (gate_bus, "总线走廊与长度预算"),
    "route": (gate_route, "信号布线与形状合法性"),
    "closure": (gate_closure, "连通性与收口"),
}

WHY = {
    "frame": "板框与禁布区决定「哪里根本没有位置」，它是所有坐标的前提",
    "floorplan": "器件朝向错了，它连的网就必然跨板；这一步的误差要用整块板重布来偿还",
    "stackup": "层数与参考平面决定有几条通道、回流从哪里回去；改它等于重画",
    "fanout": "阵列封装的出口位置决定走廊入口；盘中孔在这里产生，不是布线阶段",
    "power": "平面要绕开扇出过孔成形，反过来做就得重新扇出",
    "bus": "总线要有走廊、还要有长度预算（蛇形带）—— 两样都在布线前定，布线后没有",
    "route": "信号铜是最后画的，它只能使用前面几步留下的空间",
    "closure": "只有前面全过，「还有几条没连通/DRC 几条/等长差多少」才是一句有意义的话",
}


#: A corpus verdict below this many judged boards is not a verdict.  A rule
#: "convicted" by one board is the mirror image of a false green.
MIN_JUDGED = 3

def corpus_paths(spec):
    """Board files under `spec` (a directory, or a glob when it names one).

    A directory is scanned for every supported suffix, deepest-first so a
    project folder (`project.epro` + `PCB/*.epcb`) is picked up at its container.
    """
    if os.path.isdir(spec):
        hits = []
        for root, _dirs, names in os.walk(spec):
            for n in names:
                if n.lower().endswith(CORPUS_SUFFIXES):
                    hits.append(os.path.join(root, n))
        return sorted(hits)
    hits = sorted(glob.glob(spec))
    if hits:
        return hits
    return sorted(p for p in glob.glob(spec + "*")
                  if p.lower().endswith(CORPUS_SUFFIXES))


#: What an official DRC report says about a board - used to stratify a corpus by
#: something OTHER than the rule under test.
#:
#: The rule under test is mine. Stratifying the corpus by my own verdicts would be
#: circular ("the boards my rule passes are the good ones, so my rule is right"),
#: so the stratum comes from `kicad-cli pcb drc` - a different engine, and the
#: authority this repo defers to everywhere else. The report is written next to
#: the board as `<board>.drc.json`; a board without one is `unverified`, and an
#: unverified board never convicts a rule.
DRC_EVIDENCE_SUFFIX = ".drc.json"

#: Violation types that are about the footprint LIBRARY an old project was drawn
#: with, not about its copper. The shipped demos carry 68, 114 and 199 of these
#: because the libraries in this checkout differ from the ones the board was drawn
#: against - counting them would rank a board by its library age.
LIBRARY_ONLY_TYPES = ("lib_footprint_issues",)

#: Stratum names, cleanest first. Buckets are by RANK, not by an absolute cut: a
#: 2005 demo drawn to looser rules and a 2024 board drawn to today's defaults do
#: not share a scale, so "defects" only means anything relative to the corpus.
STRATUM_NAMES = ("low", "mid", "high")

def corpus_evidence(path):
    """The official DRC report sitting next to this board, or a note saying why not.

    `defects` = board-attributable errors + unconnected items, both taken verbatim
    from KiCad's own report. It is the number the stratification uses, and it was
    produced by a different program than the rules being audited - which is the
    only reason it is allowed to stratify them.
    """
    stem = os.path.splitext(path)[0]
    cand = [path + DRC_EVIDENCE_SUFFIX, stem + DRC_EVIDENCE_SUFFIX]
    for c in cand:
        if not os.path.isfile(c):
            continue
        try:
            with io_open(c) as fh:
                doc = json.load(fh)
        except (OSError, ValueError) as exc:
            return {"file": os.path.basename(c), "defects": None,
                    "why": "unreadable: %r" % exc}
        viol = doc.get("violations") or []
        errors = [v for v in viol if v.get("severity") == "error"]
        warns = [v for v in viol if v.get("severity") == "warning"]
        lib = sum(1 for v in viol if v.get("type") in LIBRARY_ONLY_TYPES)
        board_errors = sum(1 for v in errors
                           if v.get("type") not in LIBRARY_ONLY_TYPES)
        unconn = len(doc.get("unconnected_items") or [])
        return {"file": os.path.basename(c),
                "errors": len(errors), "board_errors": board_errors,
                "warnings": len(warns), "library_warnings": lib,
                "unconnected": unconn, "defects": board_errors + unconn,
                "kicad_version": doc.get("kicad_version"),
                "date": doc.get("date"),
                "how": ("kicad-cli pcb drc - KiCad's own engine, not this repo's "
                        "checker. %s is excluded from `defects` because it reports "
                        "the footprint library, not the copper"
                        % (LIBRARY_ONLY_TYPES,))}
    return {"file": None, "defects": None,
            "why": ("no %s next to this board: `kicad-cli pcb drc --format json -o "
                    "<board>%s <board>` gives it a stratum"
                    % (DRC_EVIDENCE_SUFFIX, DRC_EVIDENCE_SUFFIX))}

def strata(rows, parts=3):
    """Bucket the evidenced boards by official defect count, cleanest first.

    Returns (names, why_not) - `names` is None when there is nothing to stratify
    on, and `why_not` then says how many boards were short of the requirement.
    Rows without evidence are marked `unverified` and are excluded from every
    stratum rate: "we did not look" is not "it is clean".
    """
    known = [r for r in rows
             if (r.get("evidence") or {}).get("defects") is not None]
    if len(known) < parts:
        return None, ("只有 %d 块板带官方 DRC 证据（<%d）=> 不做分层，"
                      "整份语料按同一层读" % (len(known), parts))
    known.sort(key=lambda r: r["evidence"]["defects"])
    n = len(known)
    for idx, row in enumerate(known):
        row["stratum"] = STRATUM_NAMES[min(parts - 1, idx * parts // n)]
    for r in rows:
        if not r.get("stratum"):
            r["stratum"] = "unverified"
    spans = {}
    for row in known:
        name = row["stratum"]
        d = row["evidence"]["defects"]
        lo, hi = spans.get(name, (d, d))
        spans[name] = (min(lo, d), max(hi, d))
    return {"names": STRATUM_NAMES, "spans": spans, "n": n}, None

def judge_strata(origin, per, base, overall=None):
    """What the corpus says once its boards are split by exogenous evidence.

    `base` is the unstratified sentence from judge_corpus. This adds the part a
    flat pass-rate cannot say: a rule that only fails on the officially-dirtiest
    third is weakly supported, while a rule that fails on the officially-cleanest
    third is refuted by boards nobody can call sloppy.

    Returns (sentence, code). The code is what a caller switches on; the sentence
    is what a person reads.
    """
    if not per:
        return base, "no_strata"
    low = per.get("low") or {}
    high = per.get("high") or {}
    if origin == "house":
        # A house rule is not refuted by the corpus at all, so splitting the
        # corpus cannot make it more refuted. Saying anything stronger here would
        # turn "these boards were routed to another house's style" into evidence.
        return base, "house_rule_not_stratified"
    judged_low = low.get("judged") or 0
    if judged_low < MIN_JUDGED:
        if overall is not None and overall >= 0.9:
            # The rule passes everywhere; the cleanest stratum simply has too few
            # applicable boards (a 2-layer demo has no inner plane to judge).
            # Appending "the corpus cannot convict" here would read as doubt about
            # a rule nothing failed.
            return (base + "；最干净的一层只有 %d 块判得了（<%d），分层不追加证据"
                    % (judged_low, MIN_JUDGED)), "layering_silent"
        return (base + "；最低违规层只判了 %d 块（<%d）=> 语料不能定罪"
                % (judged_low, MIN_JUDGED)), "low_stratum_thin"
    low_rate = low.get("pass_rate")
    high_rate = high.get("pass_rate")
    if low_rate is None:
        return base, "low_stratum_unmeasured"
    if low_rate >= 0.9:
        return (base + "；最干净的一层也过（%.2f，n=%d）=> 分层不改变判决"
                % (low_rate, judged_low)), "hold_across_strata"
    if high_rate is not None and low_rate < high_rate - 0.15:
        return (base + "；最干净的一层反而过得最少（low %.2f < high %.2f）=> "
                "这条规则分不出干净板，问题在规则不在板子"
                % (low_rate, high_rate)), "rule_flags_cleanest"
    return (base + "；最干净的一层也不过（%.2f，n=%d）=> 官方 DRC 认定的好板也过不了，"
            "这条规则是错的" % (low_rate, judged_low)), "refuted_on_cleanest"

#: Where a rule comes from decides what a corpus can say about it.
#:
#:   physical  the board physically cannot work otherwise (castellation feeds,
#:             reference planes, a hole in a pad wicking solder, an unrouted net)
#:   house     this project's house style: 45 degrees only, no arcs at corners,
#:             no dead ends, no square corners.  A good board that was routed by
#:             hand in 2005 breaks these on purpose, so a corpus of good boards
#:             is NOT evidence against them - and must not be reported as if it
#:             were.  `docs/173` and the user's own corrections are the source.
ORIGIN = {
    "frame": "physical",
    "floorplan": "physical",
    "stackup": "physical",
    "fanout": "physical",
    "power": "physical",
    "bus": "physical",
    "route": "house",
    "closure": "physical",
}


def _ctx(path):
    from pcb_agent_mcp.adapters import open_board
    from pcb_agent_mcp import place_gate, shape_gate
    canon = open_board(path)
    ctx = {"path": path, "canonical": canon}
    ctx["place"] = place_gate.gate(canon)["value"]
    ctx["shape"] = shape_gate.gate(canon)["value"]
    # The pcbnew board is loaded LAZILY on purpose.  `GetUnconnectedCount` and
    # the meander search need it, nothing else does - and a load costs ~14 s.
    # Worse: `pcbnew.LoadBoard` can block forever on some real files (measured:
    # _corpus/royalblue54L_feather.kicad_pcb spins at 0% CPU inside the call), so
    # an eager load turns one bad file into a hung corpus run.
    ctx["raw"] = None
    ctx["raw_loaded"] = False
    return ctx

def _raw(ctx):
    """Load the pcbnew board once, on first use.  None when it cannot be loaded.

    A failed load is NOT a pass and NOT a fail - the caller must report `n/a`
    with the reason, otherwise "pcbnew could not read this file" silently turns
    into "no unconnected nets".
    """
    if ctx.get("raw_loaded"):
        return ctx.get("raw")
    ctx["raw_loaded"] = True
    try:
        import pcbnew
        ctx["raw"] = pcbnew.LoadBoard(os.path.abspath(ctx["path"]))
    except Exception as exc:                                    # noqa: BLE001
        ctx["raw"] = None
        ctx["raw_error"] = repr(exc)
    return ctx.get("raw")

_UNCONN_SNIPPET = (
    "import sys\n"
    "import pcbnew\n"
    "b = pcbnew.LoadBoard(sys.argv[1])\n"
    "b.BuildConnectivity()\n"
    "sys.stdout.write('UNCONNECTED=%d\\n' % "
    "b.GetConnectivity().GetUnconnectedCount(False))\n"
)

def unconnected_count(path, timeout=120):
    """`GetUnconnectedCount` in a subprocess, so a file pcbnew chokes on cannot
    hang the run.  Returns (count|None, note)."""
    env = dict(os.environ)
    env.pop("PYTHONPATH", None)
    env.pop("PYTHONHOME", None)
    try:
        out = subprocess.run([sys.executable, "-u", "-c", _UNCONN_SNIPPET,
                              os.path.abspath(path)],
                             capture_output=True, text=True, timeout=timeout,
                             env=env)
    except subprocess.TimeoutExpired:
        return None, "pcbnew 在 %d s 内没读完这块板（LoadBoard 卡死）=> 未连接数不能判" % timeout
    except OSError as exc:
        return None, "起不了 pcbnew 子进程：%r" % exc
    for line in reversed((out.stdout or "").splitlines()):
        if line.startswith("UNCONNECTED="):
            try:
                return int(line.split("=", 1)[1]), ""
            except ValueError:
                break
    tail = ((out.stderr or "").strip().splitlines() or ["<no stderr>"])[-1]
    return None, "pcbnew 子进程没给出未连接数：%s" % tail


def evaluate(path, amplitude=0.30, clearance=0.10, chain=True, drc=False):
    """Walk the chain.  `fail` only when everything before it passed.

    `chain=False` judges every step on its own merits and skips the blocking,
    which is a DIFFERENT question and the right one for a corpus: "does a good
    board violate this rule at all" must not be filtered through "did it get
    this far".  With `chain=False` a 2-layer board still yields a route verdict
    even though its stackup step is n/a, so the sample size per rule is the
    number of boards, not the number that survived the earlier steps.
    """
    ctx = _ctx(path)
    ctx["amplitude"] = amplitude
    ctx["clearance"] = clearance
    # Opt-in DRC in the closure step.  It was a flag on the CLI before it was a
    # flag here, so `--drc` parsed, said nothing, and printed no DRC reading -
    # a flag that cannot reach the code it names is worse than no flag.
    ctx["drc"] = bool(drc)
    steps = []
    broken = None
    for i, sid in enumerate(ORDER):
        fn, title = GATES[sid]
        rec = {"id": sid, "title": title, "why": WHY[sid],
               "requires": ORDER[i - 1] if i else None}
        try:
            measured, ok, note = fn(ctx["canonical"], ctx)
        except (Exception, SystemExit) as exc:                 # noqa: BLE001
            # SystemExit included on purpose: the helper tools are written to be
            # run from a shell and some of them `raise SystemExit("board has no
            # outline")`.  Called as a library that kills the whole corpus run.
            rec.update({"status": "error", "measured": {"error": str(exc)},
                        "note": "这一关算不出来，不能当成通过"})
            steps.append(rec)
            if broken is None:
                broken = sid
            continue
        rec["measured"] = measured
        rec["note"] = note
        if ok is None:
            rec["status"] = "n/a"
        elif not chain:
            rec["status"] = "pass" if ok else "fail"
            if not ok and broken is None:
                broken = sid
        elif broken is not None:
            # measured, printed, deliberately not judged
            rec["status"] = "blocked"
            rec["after"] = broken
        elif ok:
            rec["status"] = "pass"
        else:
            rec["status"] = "fail"
            broken = sid
        steps.append(rec)
    frontier = next((s["id"] for s in steps if s["status"] == "fail"), None)
    blocked = [s["id"] for s in steps if s["status"] == "blocked"]
    return {
        "board": os.path.abspath(path),
        "order": list(ORDER),
        "mode": "chain" if chain else "independent",
        "steps": steps,
        "first_failing_step": frontier,
        "not_judged": blocked,
        "verdict": ("sequence_ok" if not frontier and not blocked
                    else ("blocked_at_%s" % frontier if frontier else "partly_unknown")),
        "how": ("linear chain: a step is fail only if every step before it passed; "
                "otherwise it is blocked - measured and printed, not judged"),
    }


def _unrun_report(path, why, mode="isolated"):
    """Every step `error`.  A board whose process timed out has NO verdict.

    The tempting shortcut is one `n/a` or one `blocked` at the top - both would
    let the corpus count a hung file as "not judged" on some steps and leave the
    rest to be judged by whatever happened to finish.
    """
    steps = []
    for i, sid in enumerate(ORDER):
        steps.append({"id": sid, "title": GATES[sid][1], "why": WHY[sid],
                      "requires": ORDER[i - 1] if i else None,
                      "status": "error", "measured": {"error": why},
                      "note": "这块板整块没跑完 ⇒ 每一关都是未判，不能当成通过"})
    return {"board": os.path.abspath(path), "order": list(ORDER), "mode": mode,
            "steps": steps, "first_failing_step": None,
            "not_judged": list(ORDER), "verdict": "not_run", "note": why,
            "how": "one board, one child process"}

def evaluate_isolated(path, amplitude=0.30, clearance=0.10, chain=False,
                      timeout=BOARD_TIMEOUT, drc=False):
    """Evaluate one board in a child process with a hard deadline.

    Why a process and not a thread: `pcbnew.LoadBoard` blocks inside C++ and
    holds the GIL, so a Python-level timer cannot interrupt it.  Measured on
    _corpus/royalblue54L_feather.kicad_pcb: 124.6 s in the main process, 0% CPU
    inside the call, and no way to cancel it.  The only honest containment is a
    boundary the OS can enforce.
    """
    cmd = [sys.executable, "-u", os.path.abspath(__file__),
           "--board", os.path.abspath(path), "--emit-json",
           "--amplitude", repr(float(amplitude)),
           "--clearance", repr(float(clearance))]
    if not chain:
        cmd.append("--independent")
    if drc:
        cmd.append("--drc")
    env = dict(os.environ)
    env.pop("PYTHONPATH", None)
    env.pop("PYTHONHOME", None)
    env["PYTHONPATH"] = os.pathsep.join([os.path.join(REPO, "src"), HERE])
    t0 = time.time()
    try:
        got = subprocess.run(cmd, capture_output=True, text=True,
                             timeout=timeout, env=env, cwd=REPO)
    except subprocess.TimeoutExpired:
        return _unrun_report(path, "%s 秒内没跑完（pcbnew.LoadBoard 卡死，或某一关算不完）"
                             % timeout)
    except OSError as exc:
        return _unrun_report(path, "起不了子进程：%r" % exc)
    out = got.stdout or ""
    cut = out.rfind(_EMIT)
    if cut < 0:
        tail = ((got.stderr or "").strip().splitlines() or ["<no stderr>"])[-1]
        return _unrun_report(path, "子进程没吐出报告：%s" % tail)
    try:
        rep = json.loads(out[cut + len(_EMIT):].strip())
    except ValueError as exc:
        return _unrun_report(path, "子进程报告解析失败：%r" % exc)
    rep["elapsed_s"] = round(time.time() - t0, 1)
    return rep

# --------------------------------------------------------------------------- #
# reporting
# --------------------------------------------------------------------------- #
MARK = {"pass": "PASS", "fail": "FAIL", "blocked": "----", "n/a": "n/a ", "error": "ERR "}


def print_board(rep):
    print("board  %s" % rep["board"])
    print("chain  " + "->".join(rep["order"]))
    print()
    for s in rep["steps"]:
        star = "*" if s["id"] == rep["first_failing_step"] else " "
        print("%s %-9s %-5s %s" % (star, s["id"], MARK[s["status"]], s["title"]))
        print("           why   %s" % s["why"])
        for k, v in s["measured"].items():
            print("           %-22s %s" % (k, v))
        if s.get("note"):
            print("           =>    %s" % s["note"])
        if s["status"] == "blocked":
            print("           =>    这一关**不作判**：前面 `%s` 没做完，量出来的数只是情报"
                  % s.get("after"))
        print()
    print("verdict  %s" % rep["verdict"])
    if rep["first_failing_step"]:
        print("next     %s —— %s" % (rep["first_failing_step"],
                                     WHY[rep["first_failing_step"]]))
    if rep["not_judged"]:
        print("held     %s 未判（被上游挡住）" % ", ".join(rep["not_judged"]))


def judge_corpus(origin, rate, n=None):
    """What a corpus of good boards is allowed to say about one step.

    Two things must not be confused: a `physical` rule that most good boards
    break is MY rule being wrong; a `house` rule that they break says nothing
    about the rule at all, because those boards were routed to a different
    house style on purpose.
    """
    if rate is None:
        return "无可判样本"
    if n is not None and n < MIN_JUDGED:
        # One board failing a rule is not a rule audit.  Saying "this rule is
        # wrong" off n=1 is the same class of error as reporting a false green.
        return "可判样本只有 %d 份（<%d）=> 样本不足，不下结论" % (n, MIN_JUDGED)
    if origin == "house":
        return ("house rule（本项目方针）—— 语料不过不算反证" if rate < 0.9
                else "house rule，且语料也满足")
    if rate >= 0.9:
        return "好板子都过 => 规则成立"
    if rate >= 0.5:
        return "好板子半数不过 => 阈值可能过严，待复核"
    return "好板子多数不过 => 这条规则是错的（不是板子错）"

def run_corpus(paths, amplitude, clearance, quiet=False,
               timeout=BOARD_TIMEOUT, drc=False, inprocess=False):
    rows = []
    for i, p in enumerate(paths, 1):
        t0 = time.time()
        print("[%d/%d] %s ..." % (i, len(paths), os.path.basename(p)),
              file=sys.stderr, flush=True)
        row = {"board": os.path.basename(p), "path": p}
        try:
            if inprocess:
                rep = evaluate(p, amplitude, clearance, chain=False)
                if drc:
                    # in-process mode has no child to carry --drc; report the
                    # gap instead of silently dropping the flag
                    rep["drc_note"] = "--drc needs the isolated runner"
            else:
                rep = evaluate_isolated(p, amplitude, clearance, chain=False,
                                        timeout=timeout, drc=drc)
            print("        %.1fs  %s" % (time.time() - t0, rep["verdict"]),
                  file=sys.stderr, flush=True)
        except Exception as exc:                                # noqa: BLE001
            row["error"] = repr(exc)
            rows.append(row)
            continue
        row["rep"] = rep
        rows.append(row)
    # Exogenous evidence first: the stratum of a board comes from kicad-cli, never
    # from the rule being audited. Rows without a report are `unverified`.
    for r in rows:
        r["evidence"] = corpus_evidence(r["path"]) if r.get("path") else {
            "file": None, "defects": None, "why": "the board was not reached"}
    layering, why_no_layering = strata(rows)

    summary = {}
    for sid in ORDER:
        got = {"pass": 0, "fail": 0, "blocked": 0, "n/a": 0, "error": 0,
               "not_run": 0}
        per = {}
        fail_on = []
        for r in rows:
            if "rep" not in r:
                continue
            if r["rep"].get("verdict") == "not_run":
                # The process never finished: nothing about this board is known.
                # Counting its 8 `error` steps as "measured" would blur a hang
                # into a measurement.
                got["not_run"] += 1
                continue
            for s in r["rep"]["steps"]:
                if s["id"] != sid:
                    continue
                got[s["status"]] += 1
                bucket = per.setdefault(r.get("stratum") or "unverified",
                                        {"pass": 0, "fail": 0})
                if s["status"] in ("pass", "fail"):
                    bucket[s["status"]] += 1
                if s["status"] == "fail":
                    fail_on.append(r["board"])
        judged = got["pass"] + got["fail"]
        per_stratum = {}
        for name, c in per.items():
            total_here = c["pass"] + c["fail"]
            per_stratum[name] = {
                "pass": c["pass"], "fail": c["fail"], "judged": total_here,
                "pass_rate": (round(c["pass"] / float(total_here), 3)
                              if total_here else None)}
        base = judge_corpus(ORIGIN[sid], (round(got["pass"] / float(judged), 3)
                                          if judged else None), judged)
        said, code = judge_strata(ORIGIN[sid], per_stratum, base,
                                  overall=(round(got["pass"] / float(judged), 3)
                                           if judged else None))
        summary[sid] = {"counts": got, "fail_on": fail_on, "origin": ORIGIN[sid],
                        "judged": judged,
                        "pass_rate": (round(got["pass"] / float(judged), 3)
                                      if judged else None),
                        "strata": per_stratum, "judge_base": base,
                        "judge": said, "judge_code": code}
    out = {"boards": len(rows), "steps": summary, "rows": rows,
           "layering": ({"strata": layering, "why_not": why_no_layering}
                        if layering else {"strata": None,
                                          "why_not": why_no_layering,
                                          "evidence_present": sum(
                                              1 for r in rows
                                              if (r.get("evidence") or {}
                                                  ).get("defects") is not None)}),
           "how": ("a step that fails on most of a corpus of good boards is a rule "
                   "that is too strict, not a corpus that is broken; the strata "
                   "come from kicad-cli's own DRC report, never from this checker")}
    if quiet:
        return out
    hung = [r["board"] for r in rows if r.get("rep", {}).get("verdict") == "not_run"]
    print("corpus %d board(s), bus amplitude %.2f, each step judged on its own "
          "(no chain blocking)%s\n"
          % (len(rows), amplitude,
             (", one child process per board, %g s deadline" % timeout)
             if not inprocess else ", in-process (no deadline)"))
    if layering:
        print("strata by official DRC defects (kicad-cli, library-mismatch excluded):")
        for name in STRATUM_NAMES:
            members = [r for r in rows if r.get("stratum") == name]
            if not members:
                continue
            lo, hi = layering["spans"].get(name, (None, None))
            print("  %-5s n=%-2d defects %s..%s  %s"
                  % (name, len(members), lo, hi,
                     ", ".join(r["board"] for r in members)))
        rest = [r for r in rows if r.get("stratum") == "unverified"]
        if rest:
            print("  unverified n=%-2d (no .drc.json)  %s"
                  % (len(rest), ", ".join(r["board"] for r in rest[:6])))
        print()
    else:
        print("no layering: %s\n" % why_no_layering)
    if hung:
        print("not run (process did not finish, NOT counted as pass): %s\n"
              % ", ".join(hung))
    print("%-9s %-5s %-5s %-5s %-4s %-3s %-7s %-3s  %s"
          % ("step", "pass", "fail", "blk", "n/a", "nr", "rate", "n", "judgement"))
    for sid in ORDER:
        s = summary[sid]
        c = s["counts"]
        rate = "-" if s["pass_rate"] is None else "%.2f" % s["pass_rate"]
        judge = s["judge"]
        print("%-9s %-5d %-5d %-5d %-4d %-3d %-7s %-3d  %s"
              % (sid, c["pass"], c["fail"], c["blocked"], c["n/a"],
                 c["not_run"], rate, s["judged"], judge))
        if s["fail_on"]:
            print("          fail on: %s" % ", ".join(s["fail_on"][:8]))
        if layering and s["strata"]:
            bits = []
            for name in STRATUM_NAMES:
                got = s["strata"].get(name)
                if not got or not got["judged"]:
                    continue
                bits.append("%s %.2f (n=%d)" % (name, got["pass_rate"],
                                                got["judged"]))
            if bits:
                print("          by stratum: %s" % "  ".join(bits))
    return out


def main(argv=None):
    ap = argparse.ArgumentParser(description=__doc__,
                                 formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--board", help="one board to walk through the chain")
    ap.add_argument("--corpus", help="directory (or glob) of good boards to test the rules on")
    ap.add_argument("--amplitude", type=float, default=0.30,
                    help="meander tooth height used by the bus step (mm)")
    ap.add_argument("--clearance", type=float, default=0.10)
    ap.add_argument("--json", help="write the full report here")
    ap.add_argument("--quiet", action="store_true")
    ap.add_argument("--timeout", type=float, default=BOARD_TIMEOUT,
                    help="seconds one board gets inside its own process (default %g)"
                         % BOARD_TIMEOUT)
    ap.add_argument("--drc", action="store_true",
                    help="also run kicad-cli DRC in the closure step (reported, "
                         "never judged; costs one kicad-cli run per board)")
    ap.add_argument("--inprocess", action="store_true",
                    help="run in this process instead of a child - no deadline, "
                         "for debugging only")
    ap.add_argument("--independent", action="store_true",
                    help="judge every step on its own (no chain blocking)")
    ap.add_argument("--emit-json", dest="emit_json", action="store_true",
                    help=argparse.SUPPRESS)
    args = ap.parse_args(argv)
    chain = not args.independent

    if args.emit_json and args.board:
        # child mode: stdout carries one marker line and one JSON blob, nothing else
        rep = evaluate(args.board, args.amplitude, args.clearance, chain=chain,
                       drc=args.drc)
        if args.drc:
            rep["drc_requested"] = True
        sys.stdout.write(_EMIT + json.dumps(rep))
        sys.stdout.flush()
        return 0

    if args.corpus:
        paths = corpus_paths(args.corpus)
        if not paths:
            print("no board under %r (looked for %s)"
                  % (args.corpus, ", ".join(CORPUS_SUFFIXES)), file=sys.stderr)
            return 2
        out = run_corpus(paths, args.amplitude, args.clearance, args.quiet,
                         timeout=args.timeout, drc=args.drc,
                         inprocess=args.inprocess)
    elif args.board:
        if args.inprocess:
            rep = evaluate(args.board, args.amplitude, args.clearance, chain=chain,
                           drc=args.drc)
        else:
            rep = evaluate_isolated(args.board, args.amplitude, args.clearance,
                                    chain=chain, timeout=args.timeout,
                                    drc=args.drc)
        if not args.quiet:
            print_board(rep)
        out = rep
    else:
        ap.error("give --board or --corpus")
        return 2

    if args.json:
        with open(args.json, "w", encoding="utf-8") as fh:
            json.dump(out, fh, indent=1, sort_keys=True)
        if not args.quiet:
            print("\nwrote %s" % args.json)
    return 0


if __name__ == "__main__":
    sys.exit(main())
