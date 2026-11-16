"""Routing aesthetics as numbers that can ORDER two boards which are both legal.

Why this module exists. `detour_ratio` finds the one net that took the scenic
route, and `shape_gate` refuses copper the house will not ship. Neither can answer
the question the user actually asks - "these two are both clean, which one is
better to look at?" - because both are flat across the wide region where nothing
is violated. `layout_quality` covers placement (spacing rhythm, alignment, group
separation); this covers the copper.

Three rules this module is built on, each of them a lesson that cost a round:

1. **A direction is a claim.** Every reading names `better_when` or says
   `no_direction`. A reading with no direction is reported and never ranked:
   `via_per_100mm` is high on a dense BGA board because the board is dense, not
   because it was routed badly.

2. **No invented weights.** There is no composite score here. Two boards are
   ordered by DOMINANCE (no worse on every directed reading, strictly better on
   at least one) and the pairs that do not dominate each other are reported as
   `incomparable` with the readings that disagree. A single ordering is also
   given - `borda`, the mean percentile across directed readings - and it is
   stated that its only weighting assumption is "each reading counts once".

3. **Direction is calibrated on real boards, not argued.** Measured 2026-11-16
   on this repo's own material: piece length (`chunk_median_mm`), direction
   changes per 100 mm of copper (`bends_per_100mm`) and per pad
   (`bends_per_pad`), the share of copper running alongside another net
   (`bundle_share`) and the share in long runs (`long_seg_share`) all put the
   hand-routed board on the good side of every board this project drew. The
   numbers are in `CALIBRATION`, and `readings()` reports where a board sits
   against them - a percentile without a denominator is the thing this repo keeps
   getting wrong.

4. **A reading that does not separate is labelled as such.** `collinear_per_100mm`
   is a waste counter (one straight run stored as two pieces) and it is ranked -
   waste is waste - but on the calibration set the hand-routed board has MORE of
   it (0.553 per 100 mm) than this project's own board (0.119). So it is flagged
   `separates: False` and must never be quoted as a craft verdict. Five of six
   readings separate; the sixth is kept because silence about it would let a
   reader assume all six do.

What it cannot see: pad-level escape geometry. A 0.4 mm-pitch BGA needs doglegs
per ball, so a fanout-heavy board will always bend more often than a through-hole
board of the same area. `bends_per_pad` is the density-normalised twin of
`bends_per_100mm` for exactly that reason, and a reading that moves one way while
its twin moves the other is reported as `confounded` instead of counted as a win.
"""
import math

from . import geometry
from .calc.result import ok, err

#: Two segment endpoints are the same joint inside this distance. 10 um is under
#: any router grid and over the rounding noise of a file persisted in mm.
JOIN_MM = 0.010
#: A joint is a bend unless the two pieces leave within this of opposite (180).
STRAIGHT_TOL_DEG = 1.0
#: Two segments run "alongside" when their headings agree this closely...
PARALLEL_DEG = 3.0
#: ...and the perpendicular gap is inside this band (one trace + one clearance).
PARALLEL_MIN_MM = 0.15
PARALLEL_MAX_MM = 0.70
#: A segment at least this long is a straight run worth having: it is the thing
#: the eye reads as "clean", and it is what a zigzag routing has none of.
LONG_SEG_MM = 5.0

#: Readouts that are ordered, and which way. +1 = higher is better.
DIRECTED = {
    "chunk_median_mm": +1,
    "bends_per_100mm": -1,
    "bends_per_pad": -1,
    "bundle_share": +1,
    "long_seg_share": +1,
    "collinear_per_100mm": -1,
}

#: Measured 2026-11-16, canonical adapter, whole board, all copper layers.
#:   hand-routed `examples/sky_star/sky_star_routed.kicad_pcb`
#:   drawn by this project `examples/rk3506_som/rk3506_som.kicad_pcb`
#:   twelve boards in `_corpus/` (KiCad demos + shipped third-party boards)
#: `n` is how many of the twelve corpus boards were available for that reading.
#: Kept as data, not as thresholds: nothing is refused for crossing these.
CALIBRATION = {
    "chunk_median_mm": {
        "hand": 1.3439, "machine": 0.3606,
        "corpus_median": 1.7961, "corpus_n": 11,
        "corpus_range": [0.2767, 3.4925], "separates": True,
        "note": "the hand board sits BELOW the corpus median: it is dense (462 "
                "pads), so its runs are broken by fanout stubs even though the "
                "long pieces it does have are 40 mm"},
    "bends_per_100mm": {
        "hand": 33.2362, "machine": 77.3997,
        "corpus_median": 21.4903, "corpus_n": 11,
        "corpus_range": [9.1542, 136.5284], "separates": True,
        "note": "the hand board is WORSE than the corpus median here for the same "
                "density reason; what it does separate is hand vs machine, by "
                "2.3x, and pads differ only 1.48x, so density does not explain it"},
    "bends_per_pad": {
        "hand": 1.4307, "machine": 4.7757,
        "corpus_median": 1.6485, "corpus_n": 11,
        "corpus_range": [1.1012, 3.6367], "separates": True,
        "note": "the density-normalised twin, and here the hand board is better "
                "than the corpus median as well as 3.3x better than this project"},
    "bundle_share": {
        "hand": 0.2454, "machine": 0.1603,
        "corpus_median": 0.1179, "corpus_n": 11,
        "corpus_range": [0.0, 0.3510], "separates": True,
        "note": "the weakest of the five separators: this reading needs net names "
                "and eight of the twelve corpus boards route almost nothing "
                "alongside anything, which pulls the median down"},
    "long_seg_share": {
        "hand": 0.1087, "machine": 0.0533,
        "corpus_median": 0.1876, "corpus_n": 11,
        "corpus_range": [0.0085, 0.3926], "separates": True,
        "note": "separates hand from machine, but the two-layer demo boards in "
                "the corpus are mostly long power trunks and read higher"},
    "collinear_per_100mm": {
        "hand": 0.5531, "machine": 0.1188,
        "corpus_median": 1.6592, "corpus_n": 11,
        "corpus_range": [0.8233, 8.4257], "separates": False,
        "note": "does NOT separate: the hand board carries MORE stitches than "
                "this project's board (0.553 vs 0.119). It is a waste counter - "
                "an extra piece of copper is an extra piece - and it is ranked, "
                "but it is never a craft verdict and never quoted as one"},
}


# ------------------------------------------------------------------ geometry
def _segments(board, layer=None):
    """Every track as {net, layer, s, e, len, ang}, degenerate ones dropped."""
    out = []
    for t in board.tracks:
        st, en = t.get("start"), t.get("end")
        if not st or not en:
            continue
        lay = t.get("layer")
        if layer and lay != layer:
            continue
        dx, dy = float(en[0]) - float(st[0]), float(en[1]) - float(st[1])
        length = math.hypot(dx, dy)
        if length <= 1e-9:
            continue
        out.append({"net": t.get("net"), "layer": lay,
                    "s": (float(st[0]), float(st[1])),
                    "e": (float(en[0]), float(en[1])),
                    "len": length,
                    "ang": math.degrees(math.atan2(dy, dx)) % 360.0})
    return out


def _angle_between(a, b):
    d = abs((a - b) % 360.0)
    return min(d, 360.0 - d)


def _key(net, layer, point):
    return (net, layer, int(round(point[0] / JOIN_MM)),
            int(round(point[1] / JOIN_MM)))


#: Cell size of the bucket grid used to answer "does this end land inside some
#: other piece?". Two millimetres keeps a candidate list short even on a board
#: with thousands of segments, and it never changes an answer: the tolerance is
#: JOIN_MM, so any segment within reach is in the same or an adjacent cell.
_TEE_CELL_MM = 2.0

def _segment_grid(segs):
    """Every segment indexed by each cell of its bounding box, per layer."""
    cells = {}
    for i, s in enumerate(segs):
        xs = (s["s"][0], s["e"][0])
        ys = (s["s"][1], s["e"][1])
        for cx in range(int(math.floor(min(xs) / _TEE_CELL_MM)),
                        int(math.floor(max(xs) / _TEE_CELL_MM)) + 1):
            for cy in range(int(math.floor(min(ys) / _TEE_CELL_MM)),
                            int(math.floor(max(ys) / _TEE_CELL_MM)) + 1):
                cells.setdefault((s["layer"], cx, cy), []).append(i)
    return cells

def _lands_inside(segs, grid, i, p):
    """True when point p sits on the INTERIOR of another piece of the same net.

    An end-to-end meeting is already a joint in `_joints`; this is the other way
    a trunk gets a branch: the branch's end is dropped onto the middle of a run.
    Stored as two pieces, that looks like nothing at all from the ends' point of
    view, which is how a tee used to disappear from the readings.
    """
    me = segs[i]
    cx = int(math.floor(p[0] / _TEE_CELL_MM))
    cy = int(math.floor(p[1] / _TEE_CELL_MM))
    for j in grid.get((me["layer"], cx, cy), ()):
        if j == i:
            continue
        o = segs[j]
        if o["net"] != me["net"]:
            continue
        a, b = o["s"], o["e"]
        dx, dy = b[0] - a[0], b[1] - a[1]
        den = dx * dx + dy * dy
        if den <= 0.0:
            continue
        t = ((p[0] - a[0]) * dx + (p[1] - a[1]) * dy) / den
        if t * o["len"] <= JOIN_MM or (1.0 - t) * o["len"] <= JOIN_MM:
            continue
        if geometry.dist_point_segment(p, a, b) <= JOIN_MM:
            return True
    return False

def _joints(segs):
    """Group segment ends by position. Returns (pairs, dangling, tees)."""
    index = {}
    for i, s in enumerate(segs):
        index.setdefault(_key(s["net"], s["layer"], s["s"]), []).append((i, "s"))
        index.setdefault(_key(s["net"], s["layer"], s["e"]), []).append((i, "e"))
    pairs, loose, crowded = [], [], 0
    for hits in index.values():
        if len(hits) == 1:
            loose.append(hits[0])
        elif len(hits) == 2:
            pairs.append(hits)
        else:
            # Three or more pieces meeting at one point is a TEE or a star, not a
            # bend: a 90-degree branch off a trunk is correct routing, and counting
            # it as a square corner is how a good board reads as a bad one.
            crowded += 1
    grid = None
    dangling, tees = 0, crowded
    for i, end in loose:
        if grid is None:
            grid = _segment_grid(segs)
        p = segs[i]["s"] if end == "s" else segs[i]["e"]
        if _lands_inside(segs, grid, i, p):
            tees += 1
        else:
            dangling += 1
    return pairs, dangling, tees


def _leave_heading(seg, end):
    """Heading that LEAVES the joint along this piece."""
    return seg["ang"] if end == "s" else (seg["ang"] + 180.0) % 360.0


# ------------------------------------------------------------------ readings
def _spread(values):
    if not values:
        return {}
    ordered = sorted(values)
    return {"median": round(ordered[len(ordered) // 2], 4),
            "min": round(ordered[0], 4),
            "max": round(ordered[-1], 4)}


def readings(board, layer=None):
    """Every aesthetic reading, with the number that produced it."""
    segs = _segments(board, layer)
    total = sum(s["len"] for s in segs)
    pads = 0
    for fp in board.footprints:
        pads += len(fp.get("pads") or [])
    nets_known = bool(board.nets) and any(s["net"] not in (None, "") for s in segs)

    out = {"board": (getattr(board, "source", None) or {}).get("path"),
           "layer": layer or "all copper",
           "segments": len(segs), "copper_mm": round(total, 3),
           "vias": len(board.vias), "pads": pads, "nets_known": nets_known}
    if not segs or total <= 0:
        out["why_nothing_measured"] = (
            "no routed copper on this board (or on this layer): every reading "
            "below is about segments, so there is nothing to order")
        return out

    per100 = total / 100.0
    out["chunk_median_mm"] = {
        "value": round(_spread([s["len"] for s in segs])["median"], 4),
        "unit": "mm", "better_when": "higher",
        "how": "median length of one track segment: long runs are what a person "
               "sees as clean routing, and a board built from 0.4 mm pieces is a "
               "staircase even when every piece is at exactly 45 degrees"}

    pairs, dangling, crowded = _joints(segs)
    bends = 0
    turns = {45: 0, 90: 0, 135: 0}
    other = 0
    collinear = 0
    for (i, ki), (j, kj) in pairs:
        # `_leave_heading` points AWAY from the joint along each piece, so two
        # pieces of one straight run leave in opposite directions (d = 180) and a
        # real reversal of travel is d = 0. `change` is the turn the copper takes.
        d = _angle_between(_leave_heading(segs[i], ki), _leave_heading(segs[j], kj))
        change = 180.0 - d
        if change <= STRAIGHT_TOL_DEG:
            # One straight run stored as two: harmless to the copper, pure noise to
            # every length and detour reading on this board.
            collinear += 1
            continue
        bends += 1
        hit = None
        for size in (45, 90, 135):
            if abs(change - size) <= 1.5:
                hit = size
                break
        if hit:
            turns[hit] += 1
        else:
            other += 1

    out["joints"] = {"matched_pairs": len(pairs), "dangling_ends": dangling,
                     "tees_or_stars": crowded, "snap_mm": JOIN_MM,
                     "how": "segment ends snapped to a %.3f mm grid and grouped; a "
                            "group of three or more is a tee (a branch), counted "
                            "separately and never as a bend" % JOIN_MM}
    out["bends_per_100mm"] = {
        "value": round(bends / per100, 4),
        "unit": "bends/100mm", "better_when": "lower",
        "count": bends,
        "how": "a bend is a joint where the two pieces leave at anything other "
               "than 180 degrees, i.e. a change of travel direction"}
    out["turn_mix"] = {"change_45": turns[45], "change_90": turns[90],
                       "change_135": turns[135], "change_other": other,
                       "how": "45 = a proper chamfer, 90 = a square turn, 135 = a "
                              "hairpin; `other` is an off-grid direction change"}
    out["bends_per_pad"] = {
        "value": round(bends / float(pads), 4) if pads else None,
        "unit": "bends/pad", "better_when": "lower",
        "how": "the density-normalised twin of bends_per_100mm: a 0.4 mm-pitch BGA "
               "needs an escape dogleg per ball, so a fanout-heavy board bends more "
               "without being routed worse"}
    out["collinear_per_100mm"] = {
        "value": round(collinear / per100, 4),
        "unit": "stitches/100mm", "better_when": "lower",
        "count": collinear,
        "how": "joints where two pieces of one net are collinear - one straight run "
               "stored as two. It is a defect counter, not a beauty reading, and "
               "on clean boards it is often exactly zero"}
    out["long_seg_share"] = {
        "value": round(sum(1 for s in segs if s["len"] >= LONG_SEG_MM) / float(len(segs)), 4),
        "unit": "share", "better_when": "higher",
        "threshold_mm": LONG_SEG_MM,
        "how": "share of segments at least %.1f mm long" % LONG_SEG_MM}
    out["long_copper_share"] = {
        "value": round(sum(s["len"] for s in segs if s["len"] >= LONG_SEG_MM) / total, 4),
        "unit": "share", "better_when": "higher",
        "threshold_mm": LONG_SEG_MM,
        "how": "share of copper that lives in those long segments - the length "
               "version of long_seg_share, and the one to quote when a board has "
               "many short fanout stubs but long trunks"}
    out["via_per_100mm"] = {
        "value": round(len(board.vias) / per100, 4),
        "unit": "vias/100mm", "better_when": "no_direction",
        "how": "reported, never ranked: an inner-layer board and a dense BGA use "
               "more vias to route BETTER, so a low number is not a virtue"}
    bundle = _bundle_share(segs)
    out["bundle_share"] = {
        "value": round(bundle, 4) if bundle is not None else None,
        "unit": "share", "better_when": "higher",
        "degraded": not nets_known,
        "how": "share of segments that have another net's copper running alongside "
               "them, parallel within %g deg at %g..%g mm. Two nets side by side "
               "is what a bundle looks like; a board where nothing runs alongside "
               "anything has every trace solving its own maze" % (
                   PARALLEL_DEG, PARALLEL_MIN_MM, PARALLEL_MAX_MM)}
    if bundle is None:
        out["bundle_share"]["why"] = "fewer than two segments: nothing to run alongside"
    if not nets_known:
        out["bundle_share"]["why_degraded"] = (
            "this file does not carry net names on its tracks, so same-net "
            "suppression is impossible: a meander's own teeth count as a bundle. "
            "Comparable between boards in the same state, not across states")

    out["offgrid_share"] = {
        "value": round(sum(1 for s in segs
                           if abs(s["ang"] - round(s["ang"] / 45.0) * 45.0) > 1.0)
                       / float(len(segs)), 4),
        "unit": "share", "better_when": "lower",
        "how": "share of segments that are not a multiple of 45 degrees. This is "
               "house compliance (shape_gate owns the verdict), reported here only "
               "because it is the reading people reach for first"}
    out["place_quality"] = _calibration_row(out)
    return out


def _calibration_row(rows):
    """Where this board sits against the measured hand/corpus material."""
    where = {}
    for key, table in CALIBRATION.items():
        row = rows.get(key) or {}
        value = row.get("value")
        if not isinstance(value, (int, float)) or isinstance(value, bool):
            continue
        value = float(value)
        sign = DIRECTED.get(key, 0)
        entry = {"value": round(value, 4),
                 "hand_routed": table["hand"], "drawn_by_this_project": table["machine"],
                 "corpus_median": table["corpus_median"],
                 "corpus_range": table["corpus_range"], "corpus_n": table["corpus_n"],
                 "separates_hand_from_machine": table.get("separates"),
                 "note": table.get("note")}
        if sign and table["corpus_n"]:
            # Percentile rank inside [min, max] of the corpus, flipped so that 1.0
            # is always "better" whatever the raw direction is.
            lo, hi = table["corpus_range"]
            frac = 0.0 if hi == lo else (value - lo) / float(hi - lo)
            frac = min(1.0, max(0.0, frac))
            entry["corpus_percentile_from_worst"] = round(frac, 3)
            entry["better_than_corpus_share"] = round(
                frac if sign > 0 else 1.0 - frac, 3)
        where[key] = entry
    return {"against": "hand-routed sky_star, this project's rk3506_som, and "
                       "%d corpus boards measured 2026-11-16" % CALIBRATION[
                           "bends_per_100mm"]["corpus_n"],
            "readings": where,
            "not_a_threshold": "crossing a corpus number refuses nothing; it says "
                               "where this board sits among boards that ship"}


def _bundle_share(segs):
    if len(segs) < 2:
        return None
    by_layer = {}
    for s in segs:
        by_layer.setdefault(s["layer"], []).append(s)
    paired = 0
    for group in by_layer.values():
        if len(group) < 2:
            continue
        for s in group:
            ux, uy = math.cos(math.radians(s["ang"])), math.sin(math.radians(s["ang"]))
            for o in group:
                if o is s:
                    continue
                if s["net"] is not None and o["net"] is not None and s["net"] == o["net"]:
                    continue
                d = min(_angle_between(o["ang"], s["ang"]),
                        _angle_between(o["ang"], (s["ang"] + 180.0) % 360.0))
                if d > PARALLEL_DEG:
                    continue
                mx = (o["s"][0] + o["e"][0]) / 2.0 - s["s"][0]
                my = (o["s"][1] + o["e"][1]) / 2.0 - s["s"][1]
                perp = abs(-uy * mx + ux * my)
                if not (PARALLEL_MIN_MM <= perp <= PARALLEL_MAX_MM):
                    continue
                p0 = (o["s"][0] - s["s"][0]) * ux + (o["s"][1] - s["s"][1]) * uy
                p1 = (o["e"][0] - s["s"][0]) * ux + (o["e"][1] - s["s"][1]) * uy
                if max(p0, p1) > 0.5 and min(p0, p1) < s["len"] - 0.5:
                    paired += 1
                    break
    return paired / float(len(segs))


# ------------------------------------------------------------------ report
def report(board, layer=None):
    """One board's readings, and where it sits against the calibration set."""
    rows = readings(board, layer=layer)
    if rows.get("why_nothing_measured"):
        return ok(value=rows, unit="mixed",
                  model="routing shape: segment length, direction changes, bundle "
                        "adjacency",
                  basis="every track segment of the board, straight-line geometry",
                  precision="exact for lengths and angles; joints snapped to a "
                            "%.3f mm grid" % JOIN_MM,
                  assumptions={"join_mm": JOIN_MM, "long_seg_mm": LONG_SEG_MM,
                               "parallel_deg": PARALLEL_DEG,
                               "parallel_band_mm": [PARALLEL_MIN_MM, PARALLEL_MAX_MM]})
    return ok(value=rows, unit="mixed",
              model="routing shape: segment length, direction changes, bundle "
                    "adjacency",
              basis="every track segment of the board, straight-line geometry, "
                    "layers kept apart",
              precision="lengths and angles are exact; 'bundle' is a band test "
                        "(%g..%g mm) not a distance transform" % (
                            PARALLEL_MIN_MM, PARALLEL_MAX_MM),
              assumptions={"join_mm": JOIN_MM, "long_seg_mm": LONG_SEG_MM,
                           "parallel_deg": PARALLEL_DEG,
                           "straight_tol_deg": STRAIGHT_TOL_DEG})


# ------------------------------------------------------------------ ordering
def _short(label):
    """A board name that fits in a table cell, taken from a path."""
    text = str(label or "").replace("\\", "/")
    return text.rsplit("/", 1)[-1] or text


def _confound(better, worse):
    """Two readings that disagree about the SAME physical quantity.

    `bends_per_100mm` and `bends_per_pad` are one measurement normalised two ways.
    When one board wins on one and loses on the other, the boards differ in copper
    DENSITY, not in how well they were routed - and calling that a win is exactly
    the kind of false green this repo keeps paying for.
    """
    twins = ({"bends_per_100mm", "bends_per_pad"},)
    for pair in twins:
        if (pair & set(better)) and (pair & set(worse)):
            return "density: %s disagree" % " vs ".join(sorted(pair))
    return None


def _value(rows, key):
    row = (rows or {}).get(key) or {}
    value = row.get("value")
    if isinstance(value, bool) or not isinstance(value, (int, float)):
        return None
    return float(value)


def compare(boards, names=None, layer=None):
    """Order boards that are already legal. Dominance first, one aggregate second.

    `boards` is a list of parsed boards; `names` labels them (defaults to the file
    name). Nothing here looks at DRC, and nothing here needs a human label: the
    ordering comes from the readings themselves, and the calibration set is only
    used to say where a board sits, never to decide the order.
    """
    boards = [b for b in (boards or []) if b is not None]
    if len(boards) < 2:
        return err("ordering needs at least two boards, got %d" % len(boards),
                   "NOT_ENOUGH_BOARDS")
    names = list(names or [])
    rows = []
    for i, board in enumerate(boards):
        label = names[i] if i < len(names) and names[i] else \
            ((getattr(board, "source", None) or {}).get("path") or "board%d" % i)
        rows.append({"name": _short(label), "path": label,
                     "readings": readings(board, layer=layer)})

    usable = [r for r in rows if _value(r["readings"], "bends_per_100mm") is not None]
    skipped = [{"name": r["name"], "path": r["path"],
                "why": r["readings"].get("why_nothing_measured")
                       or "no bends_per_100mm reading"}
               for r in rows if r not in usable]
    if not usable:
        return err("none of the %d boards carries routed copper, so nothing can be "
                   "ordered" % len(rows), "NO_GEOMETRY")

    # percentile per directed reading; ties share the middle rank
    rank = {r["name"]: {} for r in usable}
    not_ranked = {}
    for key, sign in DIRECTED.items():
        values = [(r["name"], _value(r["readings"], key)) for r in usable]
        values = [(n, v) for n, v in values if v is not None]
        if len(values) < 2:
            not_ranked[key] = "fewer than two boards reported this reading"
            continue
        ordered = sorted(values, key=lambda pair: pair[1])
        if ordered[0][1] == ordered[-1][1]:
            not_ranked[key] = ("every board reads %.4f - the reading cannot tell "
                               "them apart, so it contributes nothing" % ordered[0][1])
            for name, _v in values:
                rank[name][key] = 0.5
            continue
        span = float(len(values) - 1)
        position = {}
        i = 0
        while i < len(ordered):
            j = i
            while j + 1 < len(ordered) and ordered[j + 1][1] == ordered[i][1]:
                j += 1
            share = ((i + j) / 2.0) / span
            for k in range(i, j + 1):
                position[ordered[k][0]] = share
            i = j + 1
        for name, _v in values:
            rank[name][key] = position[name] if sign > 0 else 1.0 - position[name]

    scored = []
    for r in usable:
        got = rank.get(r["name"]) or {}
        scored.append({"name": r["name"], "path": r["path"],
                       "borda": round(sum(got.values()) / len(got), 4) if got else None,
                       "readings_used": sorted(got),
                       "per_reading": {k: round(v, 3) for k, v in sorted(got.items())},
                       "raw": {k: _value(r["readings"], k) for k in DIRECTED}})
    scored.sort(key=lambda r: (-(r["borda"] if r["borda"] is not None else -1),
                               r["name"]))

    pairs = []
    for i, a in enumerate(scored):
        for b in scored[i + 1:]:
            better, worse, same = [], [], []
            for key in DIRECTED:
                va, vb = a["raw"].get(key), b["raw"].get(key)
                if va is None or vb is None:
                    continue
                sign = DIRECTED[key]
                if abs(va - vb) <= 1e-9:
                    same.append(key)
                elif (va - vb) * sign > 0:
                    better.append(key)
                else:
                    worse.append(key)
            verdict = ("a_dominates" if better and not worse else
                       "b_dominates" if worse and not better else
                       "tie" if not better and not worse else
                       "incomparable")
            pairs.append({"a": a["name"], "b": b["name"], "verdict": verdict,
                          "a_better_on": better, "b_better_on": worse,
                          "equal_on": same,
                          "confounded": _confound(better, worse)})

    front = sorted({p["a"] for p in pairs if p["verdict"] != "b_dominates"} |
                   {p["b"] for p in pairs if p["verdict"] != "a_dominates"})
    return ok(
        value={"boards": scored, "pairs": pairs, "pareto_front": front,
               "order_by_borda": [r["name"] for r in scored],
               "not_ranked": not_ranked,
               "skipped": skipped,
               "how_to_read": (
                   "`borda` is the mean percentile over the directed readings: 1.0 "
                   "means this board was best or tied-best on every reading that "
                   "could tell them apart. Its ONLY weighting assumption is that "
                   "each reading counts once - there is no tuned composite here. "
                   "`pairs` gives the honest part: a pair verdict of `incomparable` "
                   "means the two boards each win on a different reading, and the "
                   "names of those readings are listed rather than averaged away"),
               "confounded": ("a pair whose two readings point opposite ways on "
                              "bends_per_100mm and bends_per_pad is reported as "
                              "confounded: the difference is copper density, not "
                              "routing craft"),
               "skipped_how": ("boards that carry no routed copper are named here with "
                           "their reason and are NOT part of the ordering: an empty "
                           "board has no shape to compare, and scoring it zero "
                           "would be a fact about the file, not about the copper")},
        unit="percentile",
        model="dominance + mean percentile over %d directed readings" % len(DIRECTED),
        basis="per-board readings from aesthetics.readings()",
        precision="percentiles are ranks inside the boards passed in, not inside "
                  "the corpus: compare like with like",
        assumptions={"directed_readings": sorted(DIRECTED),
                     "weights": "each reading counts once"})
