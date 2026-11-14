"""Measure a board for the prioritized audit.

Nothing here routes, moves or writes: it is a space survey. Every number is
read from the file; every number this module cannot get is returned as
`{"unmeasurable": reason}` and the audit reports it as undetermined, because a
rule whose evidence is missing is not a pass.

The foreseen readers of these numbers are the two rule libraries that already
exist in this server:

* the fab capability library (``jlc/data/jlc-pcb-capabilities.json``), whose
  rule keys are the ``min_track_width`` / ``min_plated_drill`` / ... names, and
* the design catalogue (``calc/rules.py``), whose facts are the
  ``min_trace_width_mm`` / ``diffpair_length_skew_mm`` / ... names,
  plus the geometric rules this module can answer on its own
  (``non_45_segments``, ``unconnected_pairs``, ``courtyard_overlaps``, ...).
"""
import math
import re

from .. import geometry as G
from ..adapters.canonical import pad_points as _pad_points
from ..oracle import (_items, _dist, _bucketize, _item_bbox, _hole_list,
                      _hole_web, _min_copper_to_edge, _edge_segments,
                      _silk_facts, _silk_items, _polygon_throat,
                      _dist_item_to_point,
                      net_gaps)

TOUCH = 1e-9
WINDOW_MM = 2.0
# One net may not spend more than this many boundary edges on the
# connectivity walk. A 1 000-part board puts ~1.4 M edges in its ground
# pour, which is 456 M candidate pairs: the walk would answer in minutes and
# the audit needs it in seconds. Over budget, the net is named and skipped.
EDGE_BUDGET_PER_NET = 200000

# Both gap-scan shortcuts (the y-sorted early break and the 'cannot beat the
# current best' test) rest on lower bounds, so they may not change the
# answer. They are switchable so a test can prove exactly that on a real
# board instead of trusting the argument.
_PRUNE = True        # pair search window: every fab limit here is below 0.5 mm


def _rec(value, where, method):
    # A bool must STAY a bool: `calc/rules.py` rejects a number for a
    # true/false rule ("must be true/false"), so `float(True)` turned
    # every flag this module measured into an undetermined rule.
    kept = value if isinstance(value, bool) else float(value)
    return {"value": kept, "where": where, "method": method}


def _na(reason):
    return {"unmeasurable": reason}


def _norm(row, method):
    """Lift an oracle measurement into this module's shape.

    ``oracle`` speaks ``measured`` / ``lower_bound``; this ruleset speaks
    ``value`` / ``lower_bound``. Leaving the two spellings mixed is how a rule
    silently becomes ``undetermined`` on a board that WAS measured.
    """
    if "measured" in row:
        return {"value": float(row["measured"]),
                "where": row.get("where"), "method": method}
    if "lower_bound" in row:
        return {"lower_bound": float(row["lower_bound"]),
                "where": row.get("where"), "method": method}
    return _na(row.get("not_applicable") or row.get("why") or "not measured")

# Kinds whose width_mm describes a filled shape's outline, not a silk stroke.
_SILK_AREA_KINDS = ("fp_poly", "gr_poly", "fp_rect", "gr_rect")

_PAIR_SUFFIXES = (("+", "-"), ("_P", "_N"), ("_DP", "_DN"), ("-P", "-N"))
_PAIR_PARALLEL = math.sin(math.radians(15.0))  # "runs alongside"
_PAIR_COUPLED_MM = 0.60    # beyond this the two are not coupled
_PAIR_GAP_TOL = 0.05        # a gap that moves by less is the same gap
_PAIR_OFF_SHARE = 0.20      # share of the length allowed to move
_PAIR_MIN_SAMPLES = 6       # below this the share is not a share

def _split_pair(name, fam):
    """Return (base, suffix) when `name` ends in one of `fam`'s suffixes."""
    for suf in fam:
        if name.endswith(suf) and len(name) > len(suf) + 1:
            return name[:len(name) - len(suf)], suf
    return None, None

_XTAL_WORDS = ("crystal", "resonator", "xtal", "quartz")

_MAG_WORDS = ("inductor", "ferrite", "bead", "choke")
_MAG_VALUE = ("h",)  # 4.3uH / 600R@100MHz, checked with a number in front

def _is_magnetic(footprint):
    """An inductor, ferrite bead or common-mode choke.

    Three independent tells, because a library id is not always spelled the
    usual way and a value is not always present: the library id / value word
    (inductor, ferrite, bead, choke), the reference prefix (L, FB -- KiCad
    and every ECAD convention agree on these), or a value that is a
    henry-denominated number (4.3uH, 6.2uH). A capacitor value never matches
    the last one. Anything undecidable returns False and the part is skipped:
    a false keep-out on a resistor would move copper for no reason.
    """
    ref = str(footprint.get("ref") or "").strip().upper()
    if ref[:2] == "FB" or ref[:1] == "L":
        # L is also a library designator for a connector in some old
        # libraries; those carry a library id that says connector, so the
        # word test below removes them.
        if "connector" not in str(footprint.get("lib_id") or "").lower():
            return True
    hay = " ".join(str(footprint.get(k) or "")
                   for k in ("lib_id", "value", "ref")).lower()
    if any(w in hay for w in _MAG_WORDS):
        return True
    import re as _re
    # `24 MHz` is a crystal, not a 24 mH choke: without the lookahead the
    # henry test matches the "mh" inside "mhz" and every clocked part becomes
    # an inductor, which moves a keep-out onto the wrong geometry.
    return _re.search(r"[0-9]+(\.[0-9]+)?\s*(u|m|n|µ|μ)h(?![a-z])",
                      hay) is not None


def _is_crystal(footprint):
    """A crystal or resonator, judged by its library id / value / reference."""
    hay = " ".join(str(footprint.get(k) or "") for k in ("lib_id", "value", "ref"))
    return any(w in hay.lower() for w in _XTAL_WORDS)

def _bbox4(obj):
    """Normalise a footprint bbox to ``(x0, y0, x1, y1)`` floats.

    The canonical adapter emits ``{"x_min": .., "y_min": .., "x_max": ..,
    "y_max": ..}``; a caller-built board may still hand over a 4-sequence.
    Anything else returns ``None`` -- callers must then skip the part rather
    than guess a geometry.
    """
    if isinstance(obj, dict):
        try:
            return (float(obj["x_min"]), float(obj["y_min"]),
                    float(obj["x_max"]), float(obj["y_max"]))
        except (KeyError, TypeError, ValueError):
            return None
    if isinstance(obj, (list, tuple)) and len(obj) == 4:
        try:
            return tuple(float(v) for v in obj)
        except (TypeError, ValueError):
            return None
    return None


def _pad_hull(pads, grow=0.0):
    """Axis-aligned box around a part's pads, grown by ``grow``; None if no pad.

    A footprint with no pad position cannot be located, and callers must then
    skip the part instead of guessing a body.
    """
    xs, ys = [], []
    for q in pads or ():
        if q.get("x") is None or q.get("y") is None:
            continue
        w = float(q.get("w") or 0.0) / 2.0
        h = float(q.get("h") or 0.0) / 2.0
        xs.extend((float(q["x"]) - w, float(q["x"]) + w))
        ys.extend((float(q["y"]) - h, float(q["y"]) + h))
    if not xs:
        return None
    return (min(xs) - grow, min(ys) - grow, max(xs) + grow, max(ys) + grow)


def _clip_polygon_to_rect(points, x0, y0, x1, y1):
    """Sutherland-Hodgman clip of a closed outline against an axis-aligned box.

    Returns a (possibly empty) list of points.  Used to answer "which part of
    the courtyard is actually the crystal": the legacy HC18-U library draws a
    13.1 x 17.3 mm courtyard around a 4.9 mm lead pitch, and a track 5 mm from
    the can is inside that courtyard without being under the part.
    """
    poly = [(float(q[0]), float(q[1])) for q in points]
    edges = (
        (lambda q: q[0] >= x0, lambda p, q: (x0, p[1] + (q[1] - p[1]) *
                                            (x0 - p[0]) / (q[0] - p[0]))),
        (lambda q: q[0] <= x1, lambda p, q: (x1, p[1] + (q[1] - p[1]) *
                                            (x1 - p[0]) / (q[0] - p[0]))),
        (lambda q: q[1] >= y0, lambda p, q: (p[0] + (q[0] - p[0]) *
                                            (y0 - p[1]) / (q[1] - p[1]), y0)),
        (lambda q: q[1] <= y1, lambda p, q: (p[0] + (q[0] - p[0]) *
                                            (y1 - p[1]) / (q[1] - p[1]), y1)),
    )
    for keep, cut in edges:
        if len(poly) < 3:
            return []
        out = []
        for i in range(len(poly)):
            cur, prev = poly[i], poly[i - 1]
            if keep(cur):
                if not keep(prev):
                    out.append(cut(prev, cur))
                out.append(cur)
            elif keep(prev):
                out.append(cut(prev, cur))
        poly = out
    return poly if len(poly) > 2 else []


def _segment_meets_polygons(a, b, polygons, grow=0.0):
    """Does segment ``a``-``b`` come within ``grow`` of any of these outlines?

    Used for keep-out questions ("does a track run under the crystal"). A box
    test answers a different question: a 45 degree track that clips the corner
    of the bounding box can be a full millimetre clear of the part, and that is
    exactly how royalblue54L_feather's Net-(U5-DEC3) got called a violation.
    Here the segment is measured against the drawn edges, and a segment end
    inside the outline counts as meeting it.
    """
    ax, ay = float(a[0]), float(a[1])
    bx, by = float(b[0]), float(b[1])
    for poly in polygons or ():
        pts = [(float(q[0]), float(q[1])) for q in poly]
        if len(pts) < 3:
            continue
        if G.point_in_polygon((ax, ay), pts) or G.point_in_polygon((bx, by), pts):
            return True
        if grow <= 0.0:
            if any(G.segments_intersect((ax, ay), (bx, by),
                                        pts[i], pts[(i + 1) % len(pts)])
                   for i in range(len(pts))):
                return True
            continue
        for i in range(len(pts)):
            if G.dist_segment_segment((ax, ay), (bx, by),
                                      pts[i], pts[(i + 1) % len(pts)]) <= grow:
                return True
    return False

def _interiors_meet(pa, pb):
    """Do two closed outlines OVERLAP (share area), as opposed to merely touch?

    ``dist_polygon_polygon`` answers 0 for both, and the difference decides a
    verdict: royalblue54L_feather and stickhub place parts with their courtyards
    exactly flush (R5's bottom edge IS R7's top edge, 27 pairs on royalblue,
    64 on stickhub), which is how those libraries draw them and how KiCad's
    courtyard rule with a 0.0 mm clearance lets them through. Real overlap - one
    part's clearance area inside another's - is a different thing and is what the
    rule is about. Two tests, both needed: an edge that crosses transversally
    (not at an endpoint), or a vertex strictly inside the other outline.
    """
    def _inside(p, poly):
        if not G.point_in_polygon(p, poly):
            return False
        n = len(poly)
        return min(G.dist_point_segment(p, poly[k], poly[(k + 1) % n])
                   for k in range(n)) > 1e-6

    def _midpoints(poly):
        n = len(poly)
        return [((poly[k][0] + poly[(k + 1) % n][0]) / 2.0,
                 (poly[k][1] + poly[(k + 1) % n][1]) / 2.0) for k in range(n)]

    def _side(p, q, r):
        """Orientation with a tolerance, because the coordinates are floats.

        An exact flush pair reads as collinear only up to rounding: U2 ends at
        x = 150.68 and its neighbour starts at 150.67999999999998, and the raw
        cross product then comes out 1.8e-13 instead of 0, which the strict
        ``< 0`` test below reads as "both ends on opposite sides" - a crossing
        that is not there. KiCad works in integer nanometres, where those two
        numbers are the same integer. The tolerance is scaled by the squared
        edge length, since a cross product of lengths has units of mm^2.
        """
        v = G._orient(p, q, r)
        span = max(abs(q[0] - p[0]), abs(q[1] - p[1]), 1e-3)
        return 0.0 if abs(v) <= 1e-9 * span * span else v

    na, nb = len(pa), len(pb)
    for i in range(na):
        a1, a2 = pa[i], pa[(i + 1) % na]
        for j in range(nb):
            b1, b2 = pb[j], pb[(j + 1) % nb]
            if (_side(a1, a2, b1) * _side(a1, a2, b2) < 0
                    and _side(b1, b2, a1) * _side(b1, b2, a2) < 0):
                return True
    for p in pa:
        if _inside(p, pb):
            return True
    for p in pb:
        if _inside(p, pa):
            return True
    # Third test, for the case the other two cannot see: two rectangles that share
    # a pair of edge LINES exactly. U1 [8,12]x[9,11] and U2 [11,13]x[9,11] share
    # area [11,12]x[9,11], yet every corner sits ON the other's boundary and every
    # edge crossing is at an endpoint - so a corner test and a transversal test
    # both say "touch". The midpoint of an edge that runs THROUGH the other
    # outline is strictly interior in exactly that case, and still ON the boundary
    # for a flush pair (their shared edge is the boundary itself). A false
    # negative here would only mean "not reported", which is the safe direction.
    for p in _midpoints(pa):
        if _inside(p, pb):
            return True
    for p in _midpoints(pb):
        if _inside(p, pa):
            return True
    return False

def _castellated(holes, segs, window_mm=2.0):
    """Pad holes whose centre sits inside the board outline: half-holes.

    A pad placed ON the edge is cut in half by the router. It is not a clearance
    violation and it is not a hole-to-hole violation against the through-hole
    1.27 mm away - it is a castellation, a process option the fab prices
    separately. royalblue54L_feather (Adafruit Feather) builds every header pin
    that way: read literally, its castellations were a 0.0 mm copper-to-edge and
    a 0.27 mm hole gap, i.e. "unbuildable", on a board that ships.

    A hole is a half-hole when its centre is closer to an edge than its own
    radius: then the router removes part of it. The edge segments are bucketed
    exactly as `_min_copper_to_edge` buckets them, so this stays a lookup.
    """
    if not segs:
        return set(), []
    cell = float(window_mm)
    grid = {}
    for idx, (a, b, _kind) in enumerate(segs):
        for gx in range(int(math.floor(min(a[0], b[0]) / cell)),
                        int(math.floor(max(a[0], b[0]) / cell)) + 1):
            for gy in range(int(math.floor(min(a[1], b[1]) / cell)),
                            int(math.floor(max(a[1], b[1]) / cell)) + 1):
                grid.setdefault((gx, gy), []).append(idx)
    uuids, refs = set(), []
    for h in holes:
        if h.get("kind") != "pad":
            continue
        reach = h["drill_mm"] / 2.0 + 0.01
        cand = grid.get((int(math.floor(h["x"] / cell)),
                         int(math.floor(h["y"] / cell)))) or ()
        best = None
        for idx in cand:
            a, b, _kind = segs[idx]
            d = G.dist_point_segment((h["x"], h["y"]), a, b)
            if best is None or d < best:
                best = d
        if best is not None and best <= reach:
            if h.get("uuid"):
                uuids.add(h["uuid"])
            refs.append(h["ref"])
    return uuids, refs

def measure(board, window_mm=WINDOW_MM):
    """Return ``{fact_key: {"value"/"unmeasurable", "where", "method"}}``."""
    m = {}
    items = _items(board)
    cu_all = [it for it in items
              if any(str(l).endswith(".Cu") for l in (it.get("layers") or []))]
    # A copper item with no net cannot be part of a DIFFERENT-net spacing
    # answer: "Net-(Q1-D)" against a nameless pad that spans the whole part is
    # how openair-max came out at 0.000 mm. Name them, count them, and leave
    # them out of the spacing sweep instead of reporting a short that may not
    # exist - the count is the honest residue.
    def _named(it):
        """A net name is a statement about intent; KiCad's auto label is not.

        `unconnected-(J18-Pad1)` is what KiCad writes on a pad that connects to
        nothing. tiny_tapeout puts one 0.2 mm placeholder pad exactly on the end
        of a GND track, and treating the label as a net reported a 0.0 mm short
        between copper that is deliberately the same node.
        """
        net = it.get("net")
        return bool(net) and not str(net).startswith("unconnected-(")

    cu_items = [it for it in cu_all if _named(it)]
    m["copper_items_without_net"] = {
        "value": float(len(cu_all) - len(cu_items)),
        "where": ", ".join(str(it.get("ref")) for it in cu_all
                           if not it.get("net"))[:200],
        "method": "copper items (pads/tracks/vias/graphics) that state no net - or "
                  "only KiCad's `unconnected-(...)` auto label - and are therefore "
                  "excluded from every different-net spacing rule",
        "examples": [it.get("ref") for it in cu_all if not it.get("net")][:8],
    }

    # ---------------------------------------------------------- track width
    tracks = [t for t in board.tracks if t.get("width")]
    if tracks:
        t = min(tracks, key=lambda x: float(x["width"]))
        m["min_track_width"] = _rec(t["width"],
                                    "%s track on %s" % ("%.4g mm" % float(t["width"]),
                                                        t.get("layer")),
                                    "narrowest stored track width")
    else:
        m["min_track_width"] = _na("the board has no copper track")

    # ------------------------------------------------- copper-to-copper gap
    # One swept pass so the minimum is the minimum: the fab rules want the
    # closest pair of different nets, split by what the pair is (track-track
    # is the spacing rule, pad-track is the pad rule), and a rule fed a
    # bound instead of a measurement would be a guess.
    best = {"any": None, "track_track": None, "pad_track": None}

    def _offer(slot, d, a, b):
        cur = best[slot]
        if cur is None or d < cur[0]:
            best[slot] = (d, a, b)

    if len(cu_items) >= 2:
        cell = float(window_mm)
        grid = _bucketize(cu_items, cell, pad=cell)
        boxes = [_item_bbox(it) for it in cu_items]
        n_items = len(cu_items)
        seen = set()
        for bucket in grid.values():
            # Sorting a cell by y_min lets the inner loop stop at the first item
            # that starts below the current one's box: on a 1 100-part board a
            # 2 mm cell holds 300 copper items, and 46 M of the 78 M candidate
            # pairs are that far apart.
            bucket.sort(key=lambda k: boxes[k][1])
            nb = len(bucket)
            for i in range(nb):
                a = bucket[i]
                ax0, ay0, ax1, ay1 = boxes[a]
                for j in range(i + 1, nb):
                    b = bucket[j]
                    bx0, by0, bx1, by1 = boxes[b]
                    if _PRUNE and by0 - window_mm > ay1:
                        break
                    key = a * n_items + b if a < b else b * n_items + a
                    if key in seen:
                        continue
                    seen.add(key)
                    it1, it2 = cu_items[a], cu_items[b]
                    # same net is a connection, not a spacing; two UNNAMED items
                    # are still compared (KiCad does), so only a real name match
                    # is skipped
                    if it1.get("net") is not None and it1.get("net") == it2.get("net"):
                        continue
                    # A `connect` pad is the pad a solder bridge lands on; KiCad's
                    # DRC skips it, and StickHub ships two such comb outlines that
                    # touch while `kicad-cli pcb drc` reports ZERO violations.
                    if it1.get("pad_connect") or it2.get("pad_connect"):
                        continue
                    tie = it1.get("net_tie")
                    if tie is not None and tie == it2.get("net_tie"):
                        continue
                    l1, l2 = it1.get("layers"), it2.get("layers")
                    if l1 and l2 and not (set(l1) & set(l2)):
                        continue
                    x0, y0, x1, y1 = ax0, ay0, ax1, ay1
                    u0, v0, u1, v1 = bx0, by0, bx1, by1
                    if (x0 - window_mm > u1 or u0 - window_mm > x1
                            or y0 - window_mm > v1 or v0 - window_mm > y1):
                        continue
                    # Only the minimum is reported, so a pair whose boxes are
                    # further apart than the best already found cannot change
                    # the answer and never pays for an exact polygon distance.
                    cur = best["any"] if _PRUNE else None
                    if cur is not None:
                        c = cur[0]
                        if (x0 - c > u1 or u0 - c > x1
                                or y0 - c > v1 or v0 - c > y1):
                            continue
                    d = _dist(it1, it2)
                    kinds = {it1["kind"], it2["kind"]}
                    _offer("any", d, it1, it2)
                    if kinds == {"track"}:
                        _offer("track_track", d, it1, it2)
                    elif kinds == {"pad", "track"}:
                        _offer("pad_track", d, it1, it2)

    def _gap_rec(slot, rule_key, method):
        hit = best[slot]
        if hit is None:
            m[rule_key] = {
                "lower_bound": float(window_mm),
                "where": "no pair closer than the %.2f mm search window" % window_mm,
                "method": method,
            }
            return
        d, a, b = hit
        m[rule_key] = _rec(d, "%s vs %s" % (a.get("ref"), b.get("ref")), method)

    _gap_rec("any", "min_track_space",
             "edge-to-edge distance between copper of different nets on a shared "
             "layer (all copper kinds, so it is a lower bound for tracks alone)")
    _gap_rec("pad_track", "min_pad_to_track",
             "edge-to-edge distance between a pad and a track of a different net")

    # ------------------------------------------------------------------ holes
    holes = _hole_list(board)
    segs = _edge_segments(board)
    cast_uuids, cast_refs = _castellated(holes, segs)
    m["castellated_pads"] = (
        {"value": float(len(cast_refs)),
         "where": ", ".join(cast_refs[:8]),
         "method": "pad holes whose centre is closer to the board outline than "
                   "their own radius: the router cuts them in half, so their "
                   "copper-to-edge and hole-to-hole readings are properties of "
                   "the half-hole process, not of the layout",
         "examples": cast_refs[:8]}
        if cast_refs else
        {"value": 0.0, "where": "no pad sits on the board outline",
         "method": "pad holes whose centre is closer to the board outline than "
                   "their own radius"})
    via_holes = [h for h in holes if h["kind"] == "via"]
    pad_holes = [h for h in holes if h["kind"] == "pad"]
    solid_pad_holes = [h for h in pad_holes if h.get("uuid") not in cast_uuids]
    # Plated vs non-plated is not cosmetic: an NPTH mounting hole has no copper
    # collar, so an annular-ring or plated-hole rule that counted it would report
    # ring = 0 and condemn a perfectly buildable board (pic_programmer P101).
    def _plated(h):
        return (h.get("pad_type") in (None, "thru_hole")
                and h["diameter_mm"] > h["drill_mm"] + 1e-9)
    plated_holes = [h for h in solid_pad_holes if _plated(h)]
    npth_holes = [h for h in solid_pad_holes if not _plated(h)]
    plated_all = via_holes + plated_holes
    if plated_all:
        h = min(plated_all, key=lambda x: x["drill_mm"])
        m["min_plated_drill"] = _rec(h["drill_mm"], "%s drill" % h["ref"],
                                     "smallest stored PLATED drill diameter "
                                     "(vias + plated through-hole pads; NPTH holes excluded)")
    else:
        m["min_plated_drill"] = _na("no plated hole on this board")
    if npth_holes:
        n = min(npth_holes, key=lambda x: x["drill_mm"])
        m["min_npth_drill"] = _rec(n["drill_mm"], "%s drill" % n["ref"],
                                   "smallest non-plated through hole (mounting/slot)")
        m["min_npth_ring"] = _npth_ring(npth_holes, cu_items, window_mm=window_mm)
    else:
        for k in ("min_npth_drill", "min_npth_ring"):
            m[k] = _na("no non-plated through hole on this board")
    if holes:
        if via_holes:
            v = min(via_holes, key=lambda x: x["diameter_mm"])
            m["min_via_outer"] = _rec(v["diameter_mm"], "%s outer diameter" % v["ref"],
                                      "smallest stored via diameter")
            r = min(via_holes, key=lambda x: (x["diameter_mm"] - x["drill_mm"]))
            m["min_via_outer_minus_drill"] = _rec(
                r["diameter_mm"] - r["drill_mm"],
                "%s: %.3f - %.3f" % (r["ref"], r["diameter_mm"], r["drill_mm"]),
                "(outer diameter - drill) on the closest-fitting via: the rule wants "
                "the copper collar, not the radius")
            m["min_via_hole_edge_gap"] = _norm(
                _hole_web(via_holes, window_mm=window_mm),
                "hole edge to hole edge between two vias")
        else:
            for k in ("min_via_outer", "min_via_outer_minus_drill",
                      "min_via_hole_edge_gap"):
                m[k] = _na("no via on this board")
        if plated_holes:
            p = min(plated_holes,
                    key=lambda x: (min(x["diameter_mm"], x["diameter_mm"]) - x["drill_mm"]))
            m["min_th_annular"] = _rec(
                max(0.0, (p["diameter_mm"] - p["drill_mm"]) / 2.0),
                "%s: (%.3f - %.3f) / 2" % (p["ref"], p["diameter_mm"], p["drill_mm"]),
                "annular ring = (smaller pad side - drill) / 2, on the thinnest PLATED "
                "ring (NPTH holes have no ring and are excluded)")
            m["min_th_drill"] = _rec(
                min(q["drill_mm"] for q in plated_holes),
                "smallest through-hole pad drill",
                "smallest stored PLATED pad drill (NPTH excluded)")
            m["min_th_hole_gap"] = _norm(
                _hole_web(plated_holes, window_mm=window_mm),
                "hole edge to hole edge between two plated through-hole pads")
        else:
            for k in ("min_th_annular", "min_th_drill", "min_th_hole_gap"):
                m[k] = _na("no plated through-hole pad on this board")
    else:
        for k in ("min_via_outer", "min_via_outer_minus_drill",
                  "min_via_hole_edge_gap", "min_th_annular", "min_th_drill",
                  "min_th_hole_gap"):
            m[k] = _na("no drilled hole on this board")

    # ------------------------------------------------------- copper to edge
    cu_solid = [it for it in cu_items if it.get("uuid") not in cast_uuids]
    m["min_copper_to_edge"] = _norm(
        _min_copper_to_edge(board, cu_solid, segs, window_mm=window_mm),
        "edge-to-edge distance from copper to the board outline / cut-out "
        "(half-hole pads excluded: they are castellations)")

    # ---------------------------------------------------------- board shape
    outline = [p for c in (board.outline or []) for p in c]
    if outline:
        bbox = G.bbox_of(outline)
        w, h = bbox[2] - bbox[0], bbox[3] - bbox[1]
        m["board_width"] = _rec(w, "outline bbox x extent", "open outline? see "
                                                           "board_summary.outline_note")
        m["board_height"] = _rec(h, "outline bbox y extent", "outline bbox")
        m["min_board_dim"] = _rec(min(abs(w), abs(h)), "smaller outline bbox side",
                                  "board dimension = outline bbox side")
    else:
        for k in ("board_width", "board_height", "min_board_dim"):
            m[k] = _na("the board has no closed outline")

    # -------------------------------------------------------------- stackup
    if board.thickness_mm:
        m["thickness_mm"] = _rec(board.thickness_mm, "board thickness",
                                 "stackup total (the file's own number)")
    else:
        total = sum(float(l.get("thickness_mm") or 0.0) for l in (board.stackup or []))
        if total > 0:
            m["thickness_mm"] = _rec(total, "sum of stackup layer thicknesses",
                                     "stackup sum")
        else:
            m["thickness_mm"] = _na("the file states no thickness")
    cu = [l for l in (board.stackup or []) if l.get("copper_um")]
    if cu:
        u = float(cu[0]["copper_um"])
        m["copper_oz"] = _rec(round(u / 34.8, 3),
                              "%s: %.1f um" % (cu[0].get("name"), u),
                              "1 oz copper = 34.8 um; this is the file's own stackup")
    else:
        m["copper_oz"] = _na("the stackup states no copper weight")

    # ---------------------------------------------------------- silkscreen
    # A polygon's stroke width is the outline of a filled shape, not a printed
    # line: taking it as "the narrowest silk line" condemns boards whose silk is
    # entirely fine (cm5_minima's fp_poly states 0.01 mm and is filled). Lines,
    # arcs and text are the features a fab can actually under-print.
    silk = dict(_silk_facts(board))
    lines = [(float(i["width_mm"]), i) for i in _silk_items(board)
             if i.get("width_mm") and i.get("kind") not in _SILK_AREA_KINDS]
    if lines:
        w, item = min(lines, key=lambda t: t[0])
        silk["min_silk_line_width"] = {
            "measured": w,
            "where": "%s %s on %s" % (item.get("ref") or "board-level",
                                      item.get("kind"), item.get("layer"))}
    for src, dst in (("min_silk_text_height", "min_silk_height"),
                     ("min_silk_line_width", "min_silk_width")):
        row = silk.get(src) or {}
        if "measured" in row:
            m[dst] = _rec(row["measured"], row.get("where") or "",
                          "silkscreen stroke (polygon outlines excluded: a filled "
                          "shape's outline is not a printed line width)")
        else:
            m[dst] = _na(row.get("not_applicable") or row.get("why")
                         or "not measured")

    # --------------------------------------------------------- slots/cutouts
    cut = [c for c in (board.cutouts or []) if len(c) >= 3]
    widths = [(w, i) for i, c in enumerate(cut) for w in [_polygon_throat(c)] if w]
    if widths:
        w, i = min(widths)
        m["min_slot_npth_width"] = _rec(w, "internal cut-out #%d" % i,
                                        "narrowest distance across a cut-out contour: "
                                        "a board cut-out is a NON-plated slot")
        m["min_slot_plated_width"] = _na("a plated slot is a slot inside a pad; this "
                                        "server does not distinguish one on the board")
    else:
        m["min_slot_npth_width"] = _na("no internal cut-out on this board")
        m["min_slot_plated_width"] = _na("no internal cut-out on this board")

    # ---------------------------------------------------------- connectivity
    # pieces-1 summed over every net IS the number of missing joins, measured
    # from geometry (see oracle.net_gaps for the cross-check against DRC).
    ng = net_gaps(board, max_prims_per_net=EDGE_BUDGET_PER_NET)
    if ng.get("status") != "ok":
        m["unconnected_pairs"] = _na(ng.get("message") or "connectivity walk failed")
    else:
        v = ng["value"]
        if not v.get("complete", True):
            # A skipped net makes the total a floor, and a floor may not be
            # graded as a count: the rule would read a clean pass out of an
            # unfinished walk.
            nm = ", ".join("%s(%d edges)" % (s["net"], s["edges"])
                           for s in v.get("skipped_nets") or [])
            m["unconnected_pairs"] = {
                "lower_bound": float(v["edges_needed_total"]),
                "unmeasurable": (
                    "not a count: %s exceeded the %d-edge budget for one net, so "
                    "the walk covered %d of %d nets; the number below is a floor. "
                    "Run board_net_gaps on that net alone with a larger budget, or "
                    "trust the vendor DRC for it"
                    % (nm, EDGE_BUDGET_PER_NET, len(v["nets"]),
                       len(v["nets"]) + len(v.get("skipped_nets") or []))),
                "where": "measured nets only",
                "method": "union-find over each net's own copper, budgeted per net",
            }
        else:
            m["unconnected_pairs"] = _rec(
                v["edges_needed_total"],
                "sum over nets of (copper pieces - 1); nothing routed, poured or joined",
                "union-find over each net's own copper: pads, tracks, vias, filled pours")
        m["net_pieces"] = {
            "value": v["edges_needed_total"],
            "where": "worst nets: " + ", ".join(
                "%s=%d" % (r["net"], r["edges_needed"])
                for r in v["nets"][:5] if r["edges_needed"]),
            "method": "same walk, per net",
        }

    # ------------------------------------------------------- 45 degree rule
    bad = []
    for t in board.tracks:
        s = t.get("start")
        e = t.get("end")
        if not s or not e:
            continue
        dx, dy = e[0] - s[0], e[1] - s[1]
        if abs(dx) < TOUCH and abs(dy) < TOUCH:
            continue
        ang = math.degrees(math.atan2(abs(dy), abs(dx)))
        if abs(ang - round(ang / 45.0) * 45.0) > 0.5:
            bad.append({"layer": t.get("layer"), "net": t.get("net"),
                        "angle_deg": round(ang, 2),
                        "start": [round(c, 3) for c in s],
                        "end": [round(c, 3) for c in e]})
    if board.tracks:
        m["non_45_segments"] = {
            "value": float(len(bad)),
            "where": ("" if not bad else
                      "e.g. %.1f deg on %s from (%.3f, %.3f)"
                      % (bad[0]["angle_deg"], bad[0]["layer"],
                         bad[0]["start"][0], bad[0]["start"][1])),
            "method": "every track segment's angle measured against the nearest "
                      "multiple of 45 deg, 0.5 deg tolerance; arcs and 0/90 deg "
                      "segments conform",
            "examples": bad[:5],
        }
    else:
        m["non_45_segments"] = _na("the board has no track")

    # ------------------------------------------------------- differential pairs
    # A pair is recognised by its own net names, and only when exactly two nets
    # share a base name and their suffixes belong to one family.  Anything looser
    # pairs GND- with GND+ and reports a skew that is not a skew.
    lens = {}
    for t in board.tracks:
        a, b = t.get("start"), t.get("end")
        if not (a and b) or not t.get("net"):
            continue
        lens[t["net"]] = lens.get(t["net"], 0.0) + math.hypot(
            float(b[0]) - float(a[0]), float(b[1]) - float(a[1]))
    pairs = [{"a": n1, "b": n2, "skew_mm": abs(lens[n1] - lens[n2])}
             for n1, n2 in _diffpairs(board)]
    if pairs:
        worst = max(pairs, key=lambda r: r["skew_mm"])
        m["diffpair_length_skew_mm"] = _rec(
            worst["skew_mm"], "%s vs %s" % (worst["a"], worst["b"]),
            "routed length per net (sum of track centreline length), differenced "
            "inside each recognised pair; %d pair(s) on this board" % len(pairs))
    else:
        m["diffpair_length_skew_mm"] = _na(
            "no net name on this board forms a recognisable pair (names ending "
            "_P/_N, +/- , _DP/_DN with a shared base and exactly two members)")

    # ------------------------------------------------- crystal keep-out
    xtals = [f for f in board.footprints
             if _is_crystal(f)]
    if not xtals:
        m["trace_under_crystal"] = _na("no crystal or resonator footprint on this board")
    else:
        hits = []
        xtal_inner = []
        for f in xtals:
            cy = f.get("courtyard") or {}
            shape = [list(c) for c in (cy.get("contours") or []) if len(c) > 2]
            box = _bbox4(cy.get("bbox")) or _bbox4(f.get("bbox"))
            if box is None:
                continue
            if not shape:
                x0, y0, x1, y1 = box
                shape = [[(x0, y0), (x1, y0), (x1, y1), (x0, y1)]]
            # The part is between its pads.  What counts as "under the
            # crystal" is the courtyard CLIPPED to the pad hull grown by
            # _CRY_BODY_PAD: the legacy Crystal_HC18-U_Horizontal library draws
            # a 13.1 x 17.3 mm courtyard (226.63 mm2) around a 4.9 mm lead
            # pitch, and interf_u / video then read a track 5 mm from the can as
            # a keep-out violation.
            hull = _pad_hull(f.get("pads") or [], _CRY_BODY_PAD)
            if hull is not None:
                clipped = [_clip_polygon_to_rect(c, *hull) for c in shape]
                shape = [c for c in clipped if c]
                if not shape:
                    continue
            own = {q.get("net") for q in (f.get("pads") or []) if q.get("net")}
            side = "F.Cu" if str(f.get("layer") or "F").upper().startswith("F") \
                else "B.Cu"
            for t in board.tracks:
                if not t.get("net") or t["net"] in own:
                    continue
                # The crystal rule proper is about the part's OWN side: a
                # track there runs straight across the body. A track on
                # another layer is a different question with a different
                # answer -- the oscillator node is high impedance and the
                # laminate still couples, so it is graded separately
                # (layout.crystal_keepout_adjacent) instead of being folded
                # into this bool. royalblue has one on In4 four layers down.
                a, b = t.get("start"), t.get("end")
                if not (a and b):
                    continue
                hw_pre = float(t.get("width") or 0.0) / 2.0
                if t.get("layer") != side:
                    if _segment_meets_polygons((a[0], a[1]), (b[0], b[1]),
                                               shape, hw_pre):
                        xtal_inner.append({"ref": f.get("ref"), "net": t["net"],
                                           "layer": t.get("layer")})
                    continue
                hw = float(t.get("width") or 0.0) / 2.0
                # The courtyard's bounding BOX is not the courtyard.  A 45
                # degree track that clips the box's corner is nowhere near the
                # part: royalblue's Net-(U5-DEC3) leaves the 45 degree corner at
                # (132.99, -108.8) 0.65 mm clear and the box test still called it
                # a keep-out violation.  Ask the drawn outline instead.
                if _segment_meets_polygons((a[0], a[1]), (b[0], b[1]), shape, hw):
                    hits.append({"ref": f.get("ref"), "net": t["net"],
                                 "layer": t.get("layer")})
                    break
        m["trace_under_crystal"] = {
            # The rule is "a trace runs under the crystal", which is yes or no.
            # Handing the grader a COUNT made it answer "must be true/false,
            # got 2.0" on every board that had two such traces.
            "value": bool(hits),
            "count": len(hits),
            "where": (", ".join("%s under %s" % (h["net"], h["ref"])
                                for h in hits[:5]) if hits
                      else "no other-net track enters the body of %s (the "
                           "courtyard clipped to the part's own pads)"
                           % ", ".join(str(f.get("ref")) for f in xtals[:3])),
            "method": "other-net tracks whose centreline (grown by half its width) "
                      "meets the crystal's drawn courtyard outline",
            "examples": hits[:5],
        }

        m["trace_under_crystal_inner"] = {
            "value": bool(xtal_inner),
            "count": len(xtal_inner),
            "where": (", ".join("%s on %s under %s"
                                % (h["net"], h.get("layer"), h["ref"])
                                for h in xtal_inner[:6]) if xtal_inner
                      else "no other-net copper crosses the crystal body on "
                           "any layer but its own side"),
            "method": "as the crystal rule, but on the other copper layers",
            "examples": xtal_inner[:8],
        }
    # --------------------------------------------------- magnetic keep-out
    # A switching inductor is a field source sitting on a small closed loop.
    # Copper under it is a shorted turn: the eddy current heats the part and
    # the ripple finds every track that crosses it. The scope is what matters:

    #   same side  - a track crosses the body area. Hard.
    #   adjacent   - one copper layer away. Hard, but a POUR there is the
    #                shield, so this tier counts tracks and vias only.
    #   deeper     - behind at least one solid plane. Real but weak: measured,
    #                graded at P2, never folded into the P1 answer.
    mags = [f for f in board.footprints if _is_magnetic(f)]
    if not mags:
        for k in ("trace_under_magnetic", "via_under_magnetic",
                  "trace_under_magnetic_deep"):
            m[k] = _na("no inductor, ferrite bead or choke on this board")
    else:
        stack = [str(s.get("name")) for s in (board.stackup or [])
                 if str(s.get("name") or "").endswith(".Cu")]
        order = {n: i for i, n in enumerate(stack)}
        def _adjacent(side):
            """The one layer next to the part, in the stackup the board declares."""
            if side not in order or len(stack) < 3:
                return None
            i = order[side]
            # In1 for an F.Cu part, and the layer above B.Cu for a B.Cu part.
            if i == 0:
                return stack[1]
            if i == len(stack) - 1:
                return stack[i - 1]
            return stack[i - 1]
        p1_hits, p1_vias, deep_hits, grazes = [], [], [], []
        for f in mags:
            hull = _pad_hull(f.get("pads") or [], 0.10)
            if hull is None:
                continue
            x0, y0, x1, y1 = hull
            own = {q.get("net") for q in (f.get("pads") or []) if q.get("net")}
            rect = [[(x0, y0), (x1, y0), (x1, y1), (x0, y1)]]
            side = "B.Cu" if str(f.get("layer") or "F").upper().startswith("B") \
                else "F.Cu"
            adj = _adjacent(side)
            for t in board.tracks:
                if t.get("_arc") or not t.get("net"):
                    continue
                a, b = t.get("start"), t.get("end")
                if not (a and b):
                    continue
                hw = float(t.get("width") or 0.0) / 2.0
                layer = t.get("layer")
                # The hull is the bounding rectangle over the pads, so its four
                # corners are empty space: a two-pad part has an L-shaped copper
                # set whose bbox is a full rectangle.  A track whose EDGE merely
                # clips such a corner sits 0.1 mm from real copper and is not
                # routed under the body; one whose AXIS runs inside is.  Grading
                # both with the half-width test turned 9 corner clips into hits
                # out of 17 -- the rule cried wolf on a board that also had a
                # real problem buried inside it.
                if not _segment_meets_polygons((a[0], a[1]), (b[0], b[1]),
                                               rect, 0.0):
                    if _segment_meets_polygons((a[0], a[1]), (b[0], b[1]),
                                               rect, hw):
                        grazes.append({"ref": f.get("ref"),
                                       "net": t["net"], "layer": layer})
                    continue
                # The part's own pad joint has to terminate somewhere. An
                # own-net track that STOPS inside the body is that joint; one
                # that crosses the body and keeps going is the switch node or
                # the rail running under its own inductor.
                ends_inside = ((x0 <= a[0] <= x1 and y0 <= a[1] <= y1)
                               or (x0 <= b[0] <= x1 and y0 <= b[1] <= y1))
                if t["net"] in own and ends_inside:
                    continue
                row = {"ref": f.get("ref"), "net": t["net"], "layer": layer}
                if layer == side or (adj and layer == adj):
                    p1_hits.append(row)
                else:
                    deep_hits.append(row)
            for v in board.vias:
                vx, vy = v.get("x"), v.get("y")
                if vx is None or vy is None:
                    continue
                if x0 <= vx <= x1 and y0 <= vy <= y1 and v.get("net") not in own:
                    p1_vias.append({"ref": f.get("ref"), "net": v.get("net")})
        m["trace_under_magnetic"] = {
            "value": bool(p1_hits),
            "count": len(p1_hits),
            "where": (", ".join("%s on %s under %s" % (h["net"], h.get("layer"),
                                                     h["ref"])
                                for h in p1_hits[:6]) if p1_hits
                      else "no track of another net crosses the body of %s on the "
                           "part's own layer or the layer next to it"
                           % ", ".join(str(f.get("ref")) for f in mags[:4])),
            "method": "tracks whose centreline (grown by half its width) meets a "
                      "magnetic part's pad hull, on the part's own side or the "
                      "single copper layer beneath it; an own-net track counts only "
                      "when it crosses the body instead of terminating on it",
            "examples": p1_hits[:8],
        }
        m["trace_under_magnetic_graze"] = {
            "value": bool(grazes),
            "count": len(grazes),
            "where": (", ".join("%s on %s clips the %s pad-hull corner" %
                                (h["net"], h["layer"], h["ref"])
                                for h in grazes[:6]) if grazes
                      else "no track edge grazes a magnetic pad-hull corner"),
            "method": "tracks whose copper EDGE meets a magnetic pad hull but "
                      "whose AXIS stays outside it; the hull is a rectangle over "
                      "the pads and its corners are empty, so this is advisory "
                      "(P3), never folded into layout.magnetic_keepout",
            "examples": grazes[:8],
        }
        m["via_under_magnetic"] = {
            "value": bool(p1_vias),
            "count": len(p1_vias),
            "where": (", ".join("%s via under %s" % (h["net"], h["ref"])
                                for h in p1_vias[:6]) if p1_vias
                      else "no via of another net sits inside a magnetic part"),
            "method": "board_vias whose centre lies inside a magnetic part's "
                      "pad hull",
            "examples": p1_vias[:8],
        }
        m["trace_under_magnetic_deep"] = {
            "value": bool(deep_hits),
            "count": len(deep_hits),
            "where": (", ".join("%s on %s under %s" % (h["net"], h.get("layer"),
                                                     h["ref"])
                                for h in deep_hits[:6]) if deep_hits
                      else "no copper under a magnetic part on a layer deeper than "
                           "the one next to it"),
            "method": "as layout.magnetic_keepout, but on every copper layer "
                      "below the adjacent one - the field is shielded there, not "
                      "absent, so this is graded P2 and reported apart",
            "examples": deep_hits[:8],
        }
    # ------------------------------------------------- courtyard collisions
    fps = []
    for f in board.footprints:
        bb = _bbox4(f.get("bbox"))
        if bb is not None:
            fps.append((f, bb))
    overlaps, suspects, no_courtyard, gaps, touching = [], [], [], [], []
    for i in range(len(fps)):
        for j in range(i + 1, len(fps)):
            a, abb = fps[i]
            b, bbb = fps[j]
            if (a.get("layer") or "") != (b.get("layer") or ""):
                continue
            ca, cb = a.get("courtyard") or {}, b.get("courtyard") or {}
            ra, rb = ca.get("contours") or [], cb.get("contours") or []
            exact = bool(ra and rb and not ca.get("open_pieces")
                         and not cb.get("open_pieces"))
            if exact:
                pair = [(qa, qb) for qa in ra for qb in rb]
                gap = min(G.dist_polygon_polygon(qa, qb) for qa, qb in pair)
                if gap > 1e-6:
                    gaps.append(gap)
                elif any(_interiors_meet(qa, qb) for qa, qb in pair):
                    overlaps.append({"a": a.get("ref"), "b": b.get("ref"),
                                     "gap_mm": 0.0,
                                     "where": "drawn courtyards overlap"})
                else:
                    touching.append({"a": a.get("ref"), "b": b.get("ref")})
                continue
            # No closed outline on at least one side. The courtyard's own bbox is
            # still better than the pad hull (an open outline is a real area, just
            # not a provable one), and a bbox is bigger than the body at every
            # corner - so this is a candidate, never a verdict.
            ax0, ay0, ax1, ay1 = _bbox4(ca.get("bbox")) or abb
            bx0, by0, bx1, by1 = _bbox4(cb.get("bbox")) or bbb
            ox = min(ax1, bx1) - max(ax0, bx0)
            oy = min(ay1, by1) - max(ay0, by0)
            if ox > 1e-6 and oy > 1e-6:
                suspects.append({"a": a.get("ref"), "b": b.get("ref"),
                                 "bbox_overlap_mm": [round(ox, 3), round(oy, 3)]})
                for f in (a, b):
                    cy = f.get("courtyard") or {}
                    if f.get("ref") in no_courtyard:
                        continue
                    if not (cy.get("contours") or cy.get("open_pieces")):
                        no_courtyard.append(f.get("ref") or "?")
    where = "%d pair(s) of drawn courtyards intersect on the same side" % len(overlaps)
    if gaps:
        where += "; the closest pair of drawn courtyards clears by %.2f mm" % min(gaps)
    if suspects:
        where += "; %d more pair(s) only have a bbox overlap" % len(suspects)
    if touching:
        where += ("; %d pair(s) touch edge to edge with no shared area (flush "
                  "placement, not a collision)" % len(touching))
    out = {
        "value": float(len(overlaps) + len(suspects)),
        "where": where,
        "method": "the courtyard outline the footprint DRAWS (rect/poly/circle, or "
                  "line/arc pieces chained end to end) in board space, tested with "
                  "an exact polygon-to-polygon distance; only a footprint with no "
                  "closed outline falls back to its bbox",
        "examples": (overlaps + suspects)[:5],
    }
    if suspects:
        out["supports"] = "undetermined"
        out["why_that_cap"] = (
            "%d pair(s) collide only as bounding boxes, and %s draws no closed "
            "courtyard, so a bbox corner is the whole case - a candidate, never a "
            "verdict" % (len(suspects),
                         "at least one of those parts" if no_courtyard
                         else "at least one of them"))
    if touching:
        out["touching"] = touching[:8]
        out["note"] = ((out.get("note") + "; ") if out.get("note") else "") + (
            "%d pair(s) touch exactly (gap 0.000 mm) without sharing area and are "
            "not counted: a library that draws the courtyard as the body outline "
            "gives a flush placement a 0.000 mm gap, and the 0.0 mm courtyard "
            "clearance lets it through" % len(touching))
    if not suspects and no_courtyard:
        out["note"] = ((out.get("note") + "; ") if out.get("note") else "") + (
            "%d part(s) draw no courtyard and are not in any pair: an absent "
            "courtyard cannot collide, and pretending it is a pad-sized box would "
            "invent both fixes and failures" % len(no_courtyard))
    m["courtyard_overlaps"] = out

    # ------------------------------------------------------ off-board parts
    if outline:
        poly = board.outline[0]
        off = []
        for f, bb in fps:
            x0, y0, x1, y1 = bb
            cx, cy = (x0 + x1) / 2.0, (y0 + y1) / 2.0
            if not G.point_in_polygon((cx, cy), poly):
                off.append(f.get("ref"))
        m["footprints_off_board"] = {
            "value": float(len(off)),
            "where": ", ".join(off[:8]),
            "method": "footprint bbox centre against the outer outline contour",
            "examples": off[:8],
        }
    else:
        m["footprints_off_board"] = _na("no closed outline to test against")

    # ------------------------------------------------- decoupling capacitors
    m.update(_decoupling(board))
    # -------------------------------------------------- reference plane
    m.update(_reference_plane(board))
    # ------------------------------------------------ return vias, ampacity
    m.update(_return_vias(board))
    m.update(_power_traces(board))
    # ------------------------------------------- assembly, rails and the RF gap
    m.update(_assembly_facts(board))

    return m

# ---------------------------------------------------- assembly and rail facts
# Everything below answers a rule that the catalogue states and this module did
# not measure at all. The line through all of them: measure what the geometry
# says, and when the rule needs something the file does not carry (which nets are
# RF, how much capacitance a capacitor is), say so in a sentence a reader can act
# on instead of leaving the rule with no reading at all.

_BULK_FARADS = 1e-6
_CAP_VALUE = re.compile(r"^\s*([0-9]*\.?[0-9]+)\s*([pnuµmkK]?)\s*[fF]?\s*$")
_CAP_MULT = {"p": 1e-12, "n": 1e-9, "u": 1e-6, "µ": 1e-6, "m": 1e-3, "k": 1e3, "": 1.0}
_SILK_COPPER = (("F.SilkS", "F.Cu"), ("B.SilkS", "B.Cu"))
_THERMAL_PAD_MM2 = 1.0     # a pad this big on a poured net is a heat path, not a join
_EP_DECL = re.compile(r"(?:^|[_\-])1?EP(?:[_\-]|\d|$)|ThermalVias|exposed", re.I)
# Crystal body = the pad hull, clipped into the courtyard.  Growing the hull
# was measured and rejected: 2.0 mm turns interf_u's /PC-A7 (a track 2.5 mm
# from a pad centre) red, 0.5 mm turns video's /GREEN_IN red, and neither
# runs under the part.
_CRY_BODY_PAD = 0.0


def _farads(text):
    """'100n' / '0.1uF' / '4u7' -> farads, or None when the string is not one.

    The value field of a footprint is the only place a board file states a
    capacitance, and it is free text ('100nF 50V' parses, 'DNP' does not). A
    value that does not parse is counted, never defaulted - guessing 100 nF is
    how every board ends up "having" a bulk capacitor.
    """
    raw = str(text or "")
    m = _CAP_VALUE.match(raw)
    if not m:
        return None
    try:
        return float(m.group(1)) * _CAP_MULT.get(m.group(2).lower(), 1.0)
    except ValueError:
        return None


def _pad_poly(pad):
    """Pad copper as ('poly', points) or ('circle', (centre, radius)).

    Shapes that are not a plain rect or circle are reduced to their INSCRIBED
    rectangle (roundrect loses its corner radius, oval loses its caps). A silk
    line that crosses only a rounded corner is then missed rather than reported,
    which is the safe direction for a rule whose verdict is "a defect". A custom
    or wedge pad returns None and is counted as untested.
    """
    try:
        x, y = float(pad["x"]), float(pad["y"])
        w, h = float(pad.get("w") or 0.0), float(pad.get("h") or 0.0)
    except (KeyError, TypeError, ValueError):
        return None, None
    if w <= 0 or h <= 0:
        return None, None
    shape = str(pad.get("shape") or "rect").lower()
    rot = math.radians(float(pad.get("rotation") or 0.0))
    ca, sa = math.cos(rot), math.sin(rot)

    def place(pts):
        return [(x + qx * ca - qy * sa, y + qx * sa + qy * ca) for qx, qy in pts]

    if shape == "circle":
        return "circle", ((x, y), min(w, h) / 2.0)
    if shape == "roundrect":
        r = min(w, h) * float(pad.get("rratio") or 0.25)
        w, h = max(w - 2.0 * r, 0.1 * w), max(h - 2.0 * r, 0.1 * h)
    elif shape == "oval":
        d = min(w, h)
        w, h = (max(w - d, 0.1 * w), h) if w > h else (w, max(h - d, 0.1 * h))
    elif shape != "rect":
        return None, None
    hw, hh = w / 2.0, h / 2.0
    return "poly", place([(-hw, -hh), (hw, -hh), (hw, hh), (-hw, hh)])


def _silk_strokes(item):
    """(a, b, half_width) for every piece of a silk polyline, in board space."""
    pts = item.get("pts") or []
    if len(pts) < 2:
        return []
    half = float(item.get("width_mm") or 0.0) / 2.0
    return [(pts[i], pts[i + 1], half) for i in range(len(pts) - 1)]


def _capsule_gap(a, b, half, kind, geom):
    """Distance from a silk stroke (a capsule) to a pad, 0.0 when they touch."""
    if kind == "circle":
        centre, r = geom
        return max(0.0, G.dist_point_segment(centre, a, b) - r - half)
    if G.point_in_polygon(a, geom) or G.point_in_polygon(b, geom):
        return 0.0
    n = len(geom)
    d = min(G.dist_segment_segment(a, b, geom[k], geom[(k + 1) % n])
            for k in range(n))
    return max(0.0, d - half)


def _silk_over_pad(board):
    """Does any silkscreen line print on top of a solder pad?

    Only stroked silk with a polyline is tested: a text's bbox is an advance box,
    not ink, and reporting a text whose box covers a pad would flag perfectly
    good reference designators. Skipped items are counted and said out loud.
    """
    by_side = {}
    untested_pads = 0
    for f in board.footprints:
        for pad in (f.get("pads") or []):
            layers = pad.get("layers") or []
            for _side, cu in _SILK_COPPER:
                if not (cu in layers or "*.Cu" in layers):
                    continue
                kind, geom = _pad_poly(pad)
                if kind is None:
                    untested_pads += 1
                    continue
                w, h = float(pad.get("w") or 0.0), float(pad.get("h") or 0.0)
                r = max(w, h) / 2.0
                by_side.setdefault(cu, []).append(
                    {"pad": pad, "kind": kind, "geom": geom,
                     "cx": float(pad["x"]), "cy": float(pad["y"]), "r": r})
    if not by_side:
        return _na("no pad copper on a side that carries silkscreen")

    hits, tested, skipped = [], 0, {}
    for it in _silk_items(board):
        side = dict(_SILK_COPPER).get(it.get("layer"))
        if side is None:
            continue
        strokes = _silk_strokes(it)
        if not strokes:
            key = it.get("kind") or "?"
            skipped[key] = skipped.get(key, 0) + 1
            continue
        bb = _bbox_of_item(it)
        tested += 1        # an item examined against the pads on its side
        for p in by_side.get(side, ()):
            if bb is not None:
                # Cheap rejection with room for the stroke: the pad's own circle
                # plus the widest stroke on the item.
                reach = p["r"] + max(h for _a, _b, h in strokes) + 0.01
                if bb[0] > p["cx"] + reach or bb[2] < p["cx"] - reach \
                        or bb[1] > p["cy"] + reach or bb[3] < p["cy"] - reach:
                    continue
            gap = min(_capsule_gap(a, b, half, p["kind"], p["geom"])
                      for a, b, half in strokes)
            if gap <= 1e-9:
                hits.append({"silk": it.get("ref") or "board-level",
                             "kind": it.get("kind"),
                             "pad": "%s.%s" % (p["pad"].get("ref"),
                                               p["pad"].get("number")),
                             "net": p["pad"].get("net"),
                             "layer": it.get("layer")})
                break
    if not tested and not hits:
        return _na("no silkscreen polyline on this board to test against a pad")
    out = {
        "value": bool(hits),
        "where": (", ".join("%s %s over %s" % (h["silk"], h["kind"], h["pad"])
                            for h in hits[:5])
                  if hits else "%d silk line(s) tested against the pads on their "
                               "side, none touches a pad" % tested),
        "method": "each silkscreen stroke treated as a capsule of its own width, "
                  "tested against the pad's copper outline (roundrect and oval "
                  "reduced to their inscribed rectangle, custom pads not tested)",
        "examples": hits[:8],
    }
    if skipped:
        out["note"] = ("%d silk item(s) not tested (%s): a text box is an advance "
                       "box, not ink, and a circle or arc is not carried as a "
                       "polyline" % (sum(skipped.values()),
                                     ", ".join("%s x%d" % kv for kv in sorted(skipped.items()))))
    if untested_pads:
        out["note"] = ((out.get("note") + "; ") if out.get("note") else "") + (
            "%d pad(s) have a shape this module cannot outline (custom/wedge) and "
            "were not tested" % untested_pads)
    return out


def _bbox_of_item(it):
    bb = it.get("bbox") or {}
    try:
        return (float(bb["x_min"]), float(bb["y_min"]),
                float(bb["x_max"]), float(bb["y_max"]))
    except (KeyError, TypeError, ValueError):
        return None


def _sits_in_pour(board, net, layers, x, y):
    """Is the point already inside filled copper of `net` on one of `layers`?

    An exposed pad standing in its own pour is not fed only by vias: the copper it
    sits in carries heat away sideways, into the same net that is stitched
    elsewhere. Whether such a pad still wants a via field under it is a thermal
    survey, not something a board file settles - cm5_minima's 2x2 mm DFN pad sits
    in the ground pour with no via under it, on a board with four ground planes.
    """
    for z in board.zones:
        if z.get("is_keepout") or z.get("net") != net:
            continue
        if not any(str(l) in layers for l in (z.get("layers") or [])):
            continue
        for poly in (z.get("filled_polygons") or []):
            if len(poly) >= 3 and G.point_in_polygon((x, y), poly):
                return True
    return False

def _thermal_vias(board, pours):
    """Does the exposed pad of every part that declares one reach the plane?

    Which pad is a heat path is a decision the board file does not carry, and two
    attempts at guessing it from geometry were wrong on finished boards: an area
    threshold picks up every 1206 land and every USB-C shell tab (royalblue alone
    had 23 of them), and "the biggest pad of the footprint" picks up the shell tab
    of any four-pin connector. So the rule asks the one place that does name it:
    the footprint, whose library id says `_EP3.2x3.2mm` or `_ThermalVias` when
    the part has an exposed pad. A part made without that convention is not
    graded here at all - it is counted and said out loud.

    The heat path itself is measured three ways, because a via pad is drawn as a
    via in some boards and as a plated pad in others: a plated hole in the pad's
    own pad-number group, a via on the same net under the pad, or a plated pad of
    another part on that net under the pad.
    """
    vias = {}
    for v in board.vias:
        if v.get("net"):
            vias.setdefault(v["net"], []).append(v)
    tested, bad, where, ungraded, undeclared = 0, [], [], [], 0
    for f in board.footprints:
        groups = {}
        for pad in (f.get("pads") or []):
            groups.setdefault(pad.get("number"), []).append(pad)
        if not _EP_DECL.search(str(f.get("lib_id") or "")):
            for ps in groups.values():
                for pad in ps:
                    if (pad.get("smd") and not pad.get("hole_mm")
                            and pad.get("net") in pours
                            and float(pad.get("w") or 0) * float(pad.get("h") or 0)
                            >= _THERMAL_PAD_MM2):
                        undeclared += 1
            continue
        for number, ps in groups.items():
            smd = [p for p in ps if p.get("smd") and not p.get("hole_mm")]
            if not smd:
                continue
            area = sum(float(p.get("w") or 0) * float(p.get("h") or 0) for p in smd)
            net = ps[0].get("net")
            if area < _THERMAL_PAD_MM2 or not net or net not in pours:
                continue
            try:
                x = float(smd[0]["x"])
                y = float(smd[0]["y"])
                reach = max(max(float(p.get("w") or 0), float(p.get("h") or 0))
                            for p in ps) / 2.0 + 0.5
            except (KeyError, TypeError, ValueError):
                continue
            tested += 1
            near = len([p for p in ps if p.get("hole_mm")])
            near += sum(1 for v in vias.get(net, ())
                        if (float(v["x"]) - x) ** 2 + (float(v["y"]) - y) ** 2
                        <= reach * reach)
            for f2 in board.footprints:
                for q in (f2.get("pads") or []):
                    if q.get("net") != net or not q.get("hole_mm") or q in ps:
                        continue
                    try:
                        if abs(float(q["x"]) - x) <= reach \
                                and abs(float(q["y"]) - y) <= reach:
                            near += 1
                    except (KeyError, TypeError, ValueError):
                        continue
            ref = "%s.%s" % (f.get("ref"), number)
            if near:
                where.append("%s: %d" % (ref, near))
            elif _sits_in_pour(board, net,
                               set(pad.get("layers") or [])
                               | set(smd[0].get("layers") or []), x, y):
                ungraded.append(ref)
            else:
                bad.append(ref)
    if not tested:
        tail = ("; %d large pad(s) on a poured net sit on a part whose library id "
                "does not declare an exposed pad, so they are not graded" % undeclared
                if undeclared else "")
        return _na("no footprint on this board declares an exposed pad in its "
                   "library id, so there is no thermal pad to feed" + tail)
    out = {
        "value": not bad,
        "where": ("no heat path into the plane under %s" % ", ".join(bad[:5]))
                 if bad else ("%d exposed pad(s), each fed: %s"
                              % (tested, ", ".join(where[:5]))),
        "method": "the exposed pad of a part whose library id says _EP/ThermalVias, "
                  "counted against plated holes in the pad itself, vias on that net "
                  "under it, and plated pads of other parts on that net under it, "
                  "within half the pad plus 0.5 mm",
        "examples": bad[:8],
    }
    notes = []
    if ungraded:
        notes.append("%d exposed pad(s) with no via field stand inside their own "
                     "pour, which carries the heat sideways; whether they still need "
                     "vias is a thermal survey, so they are not graded (%s)"
                     % (len(ungraded), ", ".join(ungraded[:5])))
        if not bad:
            out["value"] = None
            out["unmeasurable"] = notes[-1]
    if undeclared:
        notes.append("%d large pad(s) of parts that do not declare an exposed pad "
                     "were not graded" % undeclared)
    if notes:
        out["note"] = "; ".join(notes)
    return out

def _nn_gaps(pts, cell=2.0):
    """Nearest-neighbour distance for every point, by a grid, not n squared.

    The search widens ring by ring and stops at the first ring that finds
    anything, because a fixed +-1 cell window silently loses every neighbour more
    than two cells away: the first version put two vias 5 mm apart in cells 2 apart
    and reported "the poured-net vias do not have neighbours" - on a board whose
    fence is 4 mm pitch.
    """
    grid = {}
    for i, (x, y) in enumerate(pts):
        grid.setdefault((int(math.floor(x / cell)), int(math.floor(y / cell))),
                        []).append(i)
    out = []
    for i, (x, y) in enumerate(pts):
        gx, gy = int(math.floor(x / cell)), int(math.floor(y / cell))
        best = None
        for r in range(1, 16):
            for dx in range(-r, r + 1):
                for dy in range(-r, r + 1):
                    if max(abs(dx), abs(dy)) != r:
                        continue
                    for j in grid.get((gx + dx, gy + dy), ()):
                        if j == i:
                            continue
                        d = math.hypot(pts[j][0] - x, pts[j][1] - y)
                        best = d if best is None else min(best, d)
            if best is not None:
                break
        if best is None:
            # Nothing inside thirty millimetres: the neighbour is a far one, and
            # dropping the point would hide exactly the isolated via that makes a
            # fence bad. Pay the O(n) for the few points that get here.
            for j, (xj, yj) in enumerate(pts):
                if j == i:
                    continue
                d = math.hypot(xj - x, yj - y)
                best = d if best is None else min(best, d)
        if best is not None:
            out.append(best)
    return out


def _stitching(board, pours):
    """Pitch of the via fence in the poured copper: the worst nearest-neighbour gap."""
    pts = [(float(v["x"]), float(v["y"])) for v in board.vias
           if v.get("net") in pours]
    if len(pts) < 2:
        return _na("fewer than two vias on a poured net: there is no fence to "
                   "measure a pitch on")
    gaps = _nn_gaps(pts)
    if not gaps:
        return _na("the poured-net vias do not have neighbours to measure against")
    gaps.sort()
    med = gaps[len(gaps) // 2]
    return {
        "value": float(gaps[-1]),
        "where": ("%d via(s) on poured nets; nearest-neighbour pitch %.2f mm at the "
                  "median, %.2f mm at the tightest, %.2f mm at the widest"
                  % (len(pts), med, gaps[0], gaps[-1])),
        "method": "nearest-neighbour distance among vias whose net the board pours; "
                  "the reading is the WIDEST of those, because a fence is only as "
                  "good as its coarsest gap and an isolated via is also a gap",
    }


def _diffpairs(board):
    """Nets that form a differential pair by their own names, exactly two each."""
    seen, done = {}, set()
    for t in board.tracks:
        if t.get("net"):
            seen.setdefault(t["net"], 0)
    candidates = {}
    for fam in _PAIR_SUFFIXES:
        for name in seen:
            base, _suf = _split_pair(name, fam)
            if base is not None:
                candidates.setdefault((fam, base), set()).add(name)
    out = []
    for members in candidates.values():
        if len(members) != 2:
            continue
        n1, n2 = sorted(members)
        if (n1, n2) in done:
            continue
        done.add((n1, n2))
        out.append((n1, n2))
    return out


def _pair_terminals(board):
    """Pad and via centres per net: a segment that ends on one is an escape."""
    pts = {}
    for f in board.footprints:
        for pad in (f.get("pads") or []):
            if pad.get("net"):
                pts.setdefault(pad["net"], []).append(
                    (float(pad["x"]), float(pad["y"])))
    for v in board.vias:
        if v.get("net"):
            pts.setdefault(v["net"], []).append((float(v["x"]), float(v["y"])))
    return pts

def _pair_gap_samples(board, a, b, terminals):
    """Edge-to-edge gap where the two nets run alongside each other, and nowhere else.

    Three things are excluded, each because including it reports an escape as a
    routing mistake - and each was caught by a finished board, not guessed:
      * a segment that ends on a pad or a via is the fanout, where the pair splays
        apart to reach its own pins (royalblue's USB pair reads 0.23..2.14 mm on
        the fanout alone, while its routed length is a flat 0.23),
      * a segment that is not parallel to its counterpart within 15 degrees,
      * a sample whose perpendicular foot lands past either end of the other
        segment, where the "gap" is really a distance to a corner.
    A pair that changes layer has no gap on either side of the via, so only same-
    layer segments are compared.
    """
    segs = {}
    for t in board.tracks:
        if t.get("net") in (a, b) and t.get("start") and t.get("end"):
            segs.setdefault(t["net"], []).append(t)
    if len(segs.get(a) or []) < 2 or len(segs.get(b) or []) < 2:
        return []

    def terminal(t):
        for q in (t["start"], t["end"]):
            for px, py in terminals.get(t["net"], ()):
                if (float(q[0]) - px) ** 2 + (float(q[1]) - py) ** 2 < 0.04:
                    return True
        return False

    def unit(t):
        (ax, ay), (bx, by) = t["start"], t["end"]
        dx, dy = float(bx) - float(ax), float(by) - float(ay)
        n = math.hypot(dx, dy)
        return None if n <= 1e-9 else (dx / n, dy / n)

    gaps = []
    for me, other in ((a, b), (b, a)):
        for t in segs[me]:
            if terminal(t):
                continue
            d_t = unit(t)
            if d_t is None:
                continue
            p, q = t["start"], t["end"]
            mid = ((float(p[0]) + float(q[0])) / 2.0,
                   (float(p[1]) + float(q[1])) / 2.0)
            wi = float(t.get("width") or 0.0) / 2.0
            best = None
            for u in segs[other]:
                if u.get("layer") != t.get("layer") or terminal(u):
                    continue
                d_u = unit(u)
                if d_u is None or abs(d_t[0] * d_u[1] - d_t[1] * d_u[0]) \
                        > _PAIR_PARALLEL:
                    continue
                (ux, uy), (vx, vy) = u["start"], u["end"]
                ex, ey = float(vx) - float(ux), float(vy) - float(uy)
                ln2 = ex * ex + ey * ey
                if ln2 <= 1e-12:
                    continue
                s_ = ((mid[0] - float(ux)) * ex + (mid[1] - float(uy)) * ey) / ln2
                if s_ <= 0.0 or s_ >= 1.0:
                    continue
                d = G.dist_point_segment(mid, u["start"], u["end"])
                d -= wi + float(u.get("width") or 0.0) / 2.0
                if d > _PAIR_COUPLED_MM:
                    continue
                best = d if best is None else min(best, d)
            if best is not None:
                gaps.append(best)
    return gaps

def _pair_gap_varies(board, a, b, terminals):
    """(median gap, the widest deviation from it, share of length off it) or None."""
    gaps = _pair_gap_samples(board, a, b, terminals)
    if len(gaps) < _PAIR_MIN_SAMPLES:
        return None
    gaps.sort()
    med = gaps[len(gaps) // 2] if len(gaps) % 2 else \
        0.5 * (gaps[len(gaps) // 2 - 1] + gaps[len(gaps) // 2])
    dev = max(abs(g - med) for g in gaps)
    off = sum(1 for g in gaps if abs(g - med) > _PAIR_GAP_TOL) / float(len(gaps))
    return med, dev, off, len(gaps)

def _diffpair_gap(board):
    """Does any pair run at one gap, or does a share of its length run at another?

    A pair whose gap changes along its coupled length is a pair whose impedance
    changes with it, and no single number describes it. The reading is therefore
    "how much of the coupled length runs at a gap other than the dominant one",
    not "min and max differ": measured on 12 finished boards, every pair shifts
    its gap SOMEWHERE near a pad or a via, and the share of length that does is
    what separates them - clean pairs top out at 11%, the pairs that change gap
    for a stretch sit at 21-27%.
    """
    pairs = _diffpairs(board)
    if not pairs:
        return _na("no net name on this board forms a recognisable pair with a "
                   "suffix (+/-, _P/_N), so there is no gap to compare")
    terminals = _pair_terminals(board)
    worst, seen = None, 0
    for a, b in pairs:
        got = _pair_gap_varies(board, a, b, terminals)
        if got is None:
            continue
        seen += 1
        med, dev, off, n = got
        if worst is None or off > worst[2]:
            worst = (a, b, off, med, dev, n)
    if worst is None:
        return _na("the pair(s) on this board never run alongside each other away "
                   "from a pad or a via for long enough to sample a gap")
    varies = worst[2] > _PAIR_OFF_SHARE
    return {
        "value": bool(varies),
        "where": ("%s vs %s: %.0f%% of %d coupled sample(s) sit at a gap other than "
                  "the dominant %.2f mm, the widest by %.2f mm%s"
                  % (worst[0], worst[1], 100.0 * worst[2], worst[5], worst[3],
                     worst[4], "" if seen < 2 else "; %d pair(s) sampled" % seen)),
        "method": "edge-to-edge gap at the midpoint of every segment that runs "
                  "parallel (15 degrees) to the other net's segment on the same "
                  "layer, away from pads and vias, within %.1f mm; a pair counts as "
                  "varying when more than %.0f%% of those samples miss the dominant "
                  "gap by more than %.2f mm"
                  % (_PAIR_COUPLED_MM, 100.0 * _PAIR_OFF_SHARE, _PAIR_GAP_TOL),
    }

def _bulk_caps(board, pours):
    """ICs on a poured rail with no capacitor of 1 uF or more on the same rail."""
    caps, unparsed = [], 0
    for f in board.footprints:
        ref = str(f.get("ref") or "")
        if not _CAP_REF.match(ref):
            continue
        far = _farads(f.get("value"))
        if far is None:
            unparsed += 1
            continue
        nets = {p.get("net") for p in (f.get("pads") or []) if p.get("net")}
        caps.append((ref, nets, far))
    if not caps:
        return _na("no capacitor on this board states a value that parses as a "
                   "capacitance (%d capacitor(s), none readable: the value field is "
                   "free text)" % unparsed)
    ics = []
    for f in board.footprints:
        pads = f.get("pads") or []
        if len(pads) < 6:
            continue
        nets = {p.get("net") for p in pads if p.get("net")}
        if not (nets & pours):
            continue
        ics.append((str(f.get("ref") or "?"), nets))
    if not ics:
        return _na("no part with six or more pads sits on a poured rail, so there "
                   "is no IC to hold a bulk capacitor for")
    bad = []
    for ref, nets in ics:
        if not any(far >= _BULK_FARADS and (nets & cnets)
                   for _c, cnets, far in caps):
            bad.append(ref)
    out = {
        "value": float(len(bad)),
        "where": ("%d of %d IC(s) on a poured rail have no capacitor of 1 uF or "
                  "more on the same rail: %s"
                  % (len(bad), len(ics), ", ".join(bad[:6]))) if bad
                 else ("all %d IC(s) on a poured rail have a 1 uF or larger "
                       "capacitor on it" % len(ics)),
        "method": "capacitance parsed from each capacitor's value field (100n, "
                  "0.1uF, 4u7); 1 uF separates bulk from the 100 nF edge part; "
                  "an IC is a part with six or more pads on a poured net",
        "examples": bad[:8],
    }
    if unparsed:
        out["note"] = ("%d capacitor(s) state a value that does not parse as a "
                       "capacitance and are not counted either way" % unparsed)
    return out


def _crystal_guard(board, pours):
    """Is each crystal enclosed by copper of the board's own return net?

    A guard ring is drawn as a loop or as a pour; this module can tell the second
    from nothing at all, and says which. The enclosure is sampled at eight points
    on the ring of the crystal's courtyard grown by 0.75 mm: when one net covers at
    least six of them, the crystal sits inside that net's copper. "Grounded" is the
    pour with the largest filled area, the same reading the return-path rule uses,
    because a board that calls its ground `0V` or `PGND` is not unusual.
    """
    xtals = [f for f in board.footprints if _is_crystal(f)]
    if not xtals:
        return _na("no crystal or resonator on this board, so there is no guard "
                   "ring to look for")
    areas = _pour_area_by_net(board)
    if not areas:
        return _na("this board has no filled pour, so there is no ground for a "
                   "guard ring to be connected to")
    ret = max(areas, key=lambda k: areas[k])
    polys = {}
    for z in board.zones:
        if z.get("is_keepout") or z.get("net") != ret:
            continue
        good = [p for p in (z.get("filled_polygons") or []) if len(p) >= 3]
        for l in (z.get("layers") or []):
            polys.setdefault(str(l), []).extend(good)
    guarded, bare = [], []
    for f in xtals:
        box = _bbox4((f.get("courtyard") or {}).get("bbox")) or _bbox4(f.get("bbox"))
        if box is None:
            continue
        side = "F.Cu" if str(f.get("layer") or "F").upper().startswith("F") else "B.Cu"
        x0, y0, x1, y1 = box
        d = 0.75
        ring = [((x0 + x1) / 2.0, y0 - d), ((x0 + x1) / 2.0, y1 + d),
                (x0 - d, (y0 + y1) / 2.0), (x1 + d, (y0 + y1) / 2.0),
                (x0 - d, y0 - d), (x1 + d, y0 - d),
                (x0 - d, y1 + d), (x1 + d, y1 + d)]
        covered = sum(1 for pt in ring
                      if any(G.point_in_polygon(pt, poly)
                             for poly in polys.get(side, ())))
        (guarded if covered >= 6 else bare).append(
            "%s (%d of 8)" % (f.get("ref"), covered))
    if not guarded and not bare:
        return _na("the crystal(s) on this board have no courtyard or bbox to "
                   "sample a ring around")
    return {
        "value": not bare,
        "where": (("no %s copper around %s" % (ret, ", ".join(bare[:4]))) if bare
                  else ("crystal(s) inside %s copper: %s"
                        % (ret, ", ".join(guarded[:4])))),
        "method": "eight points on the crystal's courtyard grown by 0.75 mm, "
                  "counted against the filled copper of the board's largest pour on "
                  "the crystal's own side; six of eight is 'enclosed'",
        "examples": bare[:4],
    }

_RAIL = re.compile(r"(?:^|[+_/\-])(?:[0-9]+V[0-9]*|V(?:CC|DD|SS|EE|BUS|BAT|SYS|IN|OUT)"
                   r"|[AP]?GND|PGND|0V)(?:$|[+_/\-])", re.I)

def _test_points(board, pours):
    """Which rails and connector signals have somewhere a probe can touch?

    A rail is a POURED net whose NAME says power (GND, +3V3, VBUS, VDD...). Using
    every poured net was wrong: royalblue pours 70 nets, most of them signals, and
    the rule then read "no test point on /Connectors/ANALOG.A0", which is a
    connector pin, not a rail.

    A probe point is a TP footprint, the only pad of a footprint, or a plated
    through hole - you can clip a header pin. This module still does not GRADE the
    result: a board tested on a pogo fixture needs pads, a board tested by hand
    needs headers, and no board file says which (see grade_caps).
    """
    rails = sorted(n for n in pours if _RAIL.search(str(n)))
    if not rails:
        return _na("no poured net on this board is named like a power rail, so "
                   "there is no rail to put a test point on")
    have = {}
    for f in board.footprints:
        pads = f.get("pads") or []
        ref = str(f.get("ref") or "")
        dedicated = len(pads) == 1 or ref.upper().startswith("TP")
        for pad in pads:
            net = pad.get("net")
            if not net:
                continue
            if dedicated:
                have.setdefault(net, []).append("%s (test point)" % (ref or "?"))
            elif pad.get("hole_mm"):
                have.setdefault(net, []).append("%s.%s (through hole)"
                                                % (ref or "?", pad.get("number")))
    missing = [n for n in rails if n not in have]
    return {
        "value": not missing,
        "where": ("%d of %d rail(s) have a probe point; none on %s"
                  % (len(rails) - len(missing), len(rails), ", ".join(missing[:6])))
                 if missing else
                 ("all %d rail(s) have a probe point" % len(rails)),
        "method": "rails are poured nets whose names say power; a probe point is a "
                  "TP footprint, the only pad of a footprint, or a plated through "
                  "hole on that net",
        "examples": missing[:8],
    }

def _mask_expansion(board):
    """The narrowest solder-mask expansion the file states, per side."""
    rows = []
    for f in board.footprints:
        for pad in (f.get("pads") or []):
            if not pad.get("mask_layers"):
                continue
            v = pad.get("mask_margin")
            if v is None:
                continue
            rows.append((float(v), "%s.%s" % (f.get("ref"), pad.get("number")),
                         pad.get("mask_margin_source")))
    if not rows:
        return _na("no pad states a solder-mask aperture, so no expansion is "
                   "recorded in the file")
    v, ref, src = min(rows, key=lambda r: r[0])
    out = _rec(v, "narrowest of %d pad(s): %s" % (len(rows), ref),
               "each pad's solder_mask_margin, which is the per-side expansion; a "
               "pad that states none is 0 mm, which is what KiCad plots")
    absent = [r for r in rows if r[2] == "absent_treated_as_zero"]
    if absent:
        out["note"] = ("%d of %d pads state no margin and read as 0 mm by the "
                       "plotter's own default" % (len(absent), len(rows)))
    return out


def _assembly_facts(board):
    """The facts the catalogue asks for that this module measured nowhere until now."""
    pours = _pour_nets(board)
    out = {
        "silkscreen_over_pad": _silk_over_pad(board),
        "power_pad_has_thermal_vias": _thermal_vias(board, pours),
        "stitching_via_pitch_mm": _stitching(board, pours),
        "diffpair_gap_varies": _diffpair_gap(board),
        "ics_without_bulk_cap": _bulk_caps(board, pours),
        "test_points_on_rails_and_connectors": _test_points(board, pours),
        "min_mask_expansion": _mask_expansion(board),
        "crystal_has_guard_ring": _crystal_guard(board, pours),
    }
    out.update(_rf_facts(board))
    return out

# ------------------------------------------------------------- the RF family
# Five of the six RF rules used to be answered with "which nets are RF is not in
# the file", which is true and useless: a board whose net is called RF_IN has
# said it. So the population is read off the NET NAMES, by whole tokens (a
# substring match would put `BT` inside `BAT+`), and the reading says which names
# it took. A board whose RF net is called `Net-(U1-Pad3)` still gets
# `undetermined` - that is the honest answer, and the caller can name the nets
# through `facts`.
_RF_NET_TOKENS = frozenset((
    "RF", "ANT", "ANTENNA", "AERIAL", "FEED", "FEEDLINE", "LNA", "HIRF",
    "MICROSTRIP", "CPW", "CPWG", "WIFI", "WLAN", "BLE", "GNSS", "GPS",
    "LTE", "GSM", "LORA", "NBIOT", "RADAR", "UWB", "ZIGBEE",
))
# Band spellings are tokens of their own: 2G4 / 5G8 / 2.4G / 5.8G / 433M / 868M /
# 915M. `TX`/`RX` are deliberately NOT tokens: on a dev board they are the UART.
_RF_BAND_TOKEN = re.compile(r"^(2[._]?4|5[._]?8)(g|ghz)?$|^(433|868|915)(m|mhz)?$",
                            re.I)
_ANT_TOKEN = frozenset(("ANT", "ANTENNA", "AERIAL"))
_NET_TOKEN_SPLIT = re.compile(r"[^0-9A-Za-z]+")


def _net_tokens(name):
    return [t for t in _NET_TOKEN_SPLIT.split(str(name or "")) if t]


def _rf_population(board):
    """Which nets the file's own names say are RF, and which of those is the feed."""
    names = set()
    for t in board.tracks:
        if t.get("net"):
            names.add(str(t["net"]))
    for v in board.vias:
        if v.get("net"):
            names.add(str(v["net"]))
    for f in board.footprints:
        for pad in (f.get("pads") or []):
            if pad.get("net"):
                names.add(str(pad["net"]))
    rf, ant = [], []
    for n in sorted(names):
        # `unconnected-(U5-ANT-Pad24)` is KiCad's own label for a pad that
        # connects to nothing; it happens to contain the word ANT and is not
        # a net at all (same exclusion the spacing sweep makes).
        if n.startswith("unconnected-("):
            continue
        up = [t.upper() for t in _net_tokens(n)]
        if not any(t in _RF_NET_TOKENS or _RF_BAND_TOKEN.match(t) for t in up):
            continue
        rf.append(n)
        if any(t in _ANT_TOKEN for t in up):
            ant.append(n)
    return {"nets": rf, "antenna": ant,
            "how": "net names matched as WHOLE tokens (RF, ANT, FEED, LNA, GPS, "
                   "WIFI, ...) or band spellings such as 2G4 / 5G8 / 433M; TX and "
                   "RX are not tokens because on a dev board they are the UART"}


def _rf_segments_by_layer(board, nets):
    """Every RF track flattened to segments, grouped by (net, layer)."""
    out = {}
    for t in board.tracks:
        if str(t.get("net") or "") not in nets:
            continue
        pts = t.get("_points") or ([t["start"], t["end"]]
                                   if t.get("start") and t.get("end") else [])
        pts = [(float(p[0]), float(p[1])) for p in pts]
        for i in range(len(pts) - 1):
            if pts[i] == pts[i + 1]:
                continue
            out.setdefault((str(t.get("net")), str(t.get("layer") or "")),
                           []).append((pts[i], pts[i + 1],
                                       float(t.get("width") or 0.0)))
    return out


def _rf_right_angles(board, nets, tol_deg=89.0):
    """Corners on an RF line whose direction turns by 90 degrees or more."""
    groups = _rf_segments_by_layer(board, nets)
    if not groups:
        return None
    hits, worst, corners = [], None, 0
    for (net, layer), segs in groups.items():
        ends = {}
        for a, b, _w in segs:
            for p, q in ((a, b), (b, a)):
                key = (round(p[0], 3), round(p[1], 3))
                ends.setdefault(key, []).append((q[0] - p[0], q[1] - p[1]))
        for key, dirs in ends.items():
            if len(dirs) != 2:
                continue                    # stub, crossing or a redundant point
            corners += 1
            (ax, ay), (bx, by) = dirs
            na, nb = math.hypot(ax, ay), math.hypot(bx, by)
            if na <= 0 or nb <= 0:
                continue
            cos = max(-1.0, min(1.0, (ax * bx + ay * by) / (na * nb)))
            # Both vectors point AWAY from the vertex, so a straight
            # continuation reads as 180 deg and a right-angled corner reads as
            # 90. What the rule is about is how far the line TURNS, i.e. the
            # supplement of that angle: straight = 0, right angle = 90,
            # 45 deg miter = 45. Reporting the angle itself counted every
            # collinear vertex (a track split in two) as a right angle.
            turn = 180.0 - math.degrees(math.acos(cos))
            if worst is None or turn > worst[0]:
                worst = (turn, net, layer, key[0], key[1])
            if turn >= tol_deg:
                hits.append({"net": net, "layer": layer,
                             "x_mm": round(key[0], 3), "y_mm": round(key[1], 3),
                             "turn_deg": round(turn, 2)})
    hits.sort(key=lambda h: -h["turn_deg"])
    if not hits:
        return _rec(False, "%d corner(s) on the RF nets, the sharpest turning %.1f deg"
                    % (corners, worst[0] if worst else 0.0),
                    "turn of the direction vector at every vertex where exactly two "
                    "RF track ends meet; a right angle is 90 deg of turn, a 45 deg "
                    "miter is 45, and anything at or above %g deg is counted"
                    % tol_deg)
    out = _rec(True, "%d corner(s) turn by >= %g deg; the sharpest is %s on %s at "
               "(%.3f, %.3f) mm turning %.1f deg"
               % (len(hits), tol_deg, hits[0]["net"], hits[0]["layer"],
                  hits[0]["x_mm"], hits[0]["y_mm"], hits[0]["turn_deg"]),
               "turn of the direction vector at every vertex where exactly two RF "
               "track ends meet; 90 deg of turn is a right angle and counts, a "
               "45 deg miter does not")
    out["examples"] = hits[:8]
    return out


def _circumradius(p0, p1, p2):
    ax, ay = p1[0] - p0[0], p1[1] - p0[1]
    bx, by = p2[0] - p0[0], p2[1] - p0[1]
    cross = ax * by - ay * bx
    if abs(cross) < 1e-12:
        return None
    la, lb = math.hypot(ax, ay), math.hypot(bx, by)
    lc = math.hypot(p2[0] - p1[0], p2[1] - p1[1])
    return (la * lb * lc) / (2.0 * abs(cross))


def _rf_bend_radius(board, nets):
    """The tightest radius an RF line actually bends on, from its arc tracks."""
    radii, straight = [], 0
    for t in board.tracks:
        if str(t.get("net") or "") not in nets:
            continue
        if t.get("_arc") and t.get("_points") and len(t["_points"]) >= 3:
            pts = [(float(p[0]), float(p[1])) for p in t["_points"]]
            r = _circumradius(pts[0], pts[len(pts) // 2], pts[-1])
            w = float(t.get("width") or 0.0)
            if r and r > 1e-3:
                radii.append((r, w, str(t.get("net")), str(t.get("layer") or "")))
        else:
            straight += 1
    if radii:
        r, w, net, layer = min(radii, key=lambda x: x[0])
        out = _rec(r, "%s on %s: radius %.4g mm on a %.4g mm line (%.2f widths)"
                   % (net, layer, r, w, (r / w) if w else 0.0),
                   "circumradius of each arc track's own three-point geometry; the "
                   "rule wants radius >= 3 line widths")
        out["arc_tracks"] = len(radii)
        return out
    return _na("no arc track on the RF nets, so the bend radius is a number this "
               "board does not have: %d straight segment(s) bend as MITERS, and a "
               "miter's radius is zero by construction. The 3w rule asks about a "
               "curve; grade the miter with layout.rf_right_angle_bend instead"
               % straight)


def _bbox_apart(a, b, best):
    """True when the two boxes are certainly farther apart than the best so far.

    Skipping a pair on its bounding boxes is only allowed to make this faster, so
    the test compares against the CURRENT best: a pair that could beat it is
    always measured exactly.
    """
    if best is None:
        return False
    ax0, ay0, ax1, ay1 = _item_bbox(a)
    bx0, by0, bx1, by1 = _item_bbox(b)
    dx = max(0.0, max(ax0, bx0) - min(ax1, bx1))
    dy = max(0.0, max(ay0, by0) - min(ay1, by1))
    return math.hypot(dx, dy) > best[0]


def _min_gap_between(items_a, items_b, max_gap_mm):
    """Closest edge-to-edge distance between two item sets, up to a window."""
    cell = float(max_gap_mm)
    grid = {}
    for idx, it in enumerate(items_b):
        x0, y0, x1, y1 = _item_bbox(it)
        for gx in range(int(math.floor((x0 - cell) / cell)),
                        int(math.floor((x1 + cell) / cell)) + 1):
            for gy in range(int(math.floor((y0 - cell) / cell)),
                            int(math.floor((y1 + cell) / cell)) + 1):
                grid.setdefault((gx, gy), []).append(idx)
    best = None
    for a in items_a:
        x0, y0, x1, y1 = _item_bbox(a)
        seen = set()
        for gx in range(int(math.floor(x0 / cell)), int(math.floor(x1 / cell)) + 1):
            for gy in range(int(math.floor(y0 / cell)), int(math.floor(y1 / cell)) + 1):
                for idx in grid.get((gx, gy), ()):
                    if idx in seen:
                        continue
                    seen.add(idx)
                    b = items_b[idx]
                    if _bbox_apart(a, b, best):
                        continue
                    d = _dist(a, b)
                    if best is None or d < best[0]:
                        best = (d, a, b)
                        if d == 0.0:
                            return best
    return best


def _rf_min_width(rf_items):
    ws = [float(it["seg"][2]) for it in rf_items if it.get("seg")]
    return min(ws) if ws else 0.0


def _rf_facts(board):
    """The RF rules, measured on the nets the file's names call RF."""
    pop = _rf_population(board)
    nets = set(pop["nets"])
    out = {}
    if not nets:
        return {key: _na("no net name on this board reads as an RF, antenna, feed "
                         "or band net, so there is no population to measure; name "
                         "the feed in the schematic (RF_IN, ANT_2G4, ...) or pass "
                         "the number through `facts`")
                for key in ("rf_line_z0_error_pct", "has_right_angle_bend_on_rf",
                            "rf_min_bend_radius_mm", "rf_min_width_mm",
                            "rf_min_spacing_to_other_trace_mm",
                            "rf_min_distance_to_edge_mm",
                            "antenna_min_distance_to_other_rf_mm")}

    ra = _rf_right_angles(board, nets)
    out["has_right_angle_bend_on_rf"] = ra if ra is not None else _na(
        "the RF net(s) %s carry no track, so there is no corner to measure"
        % ", ".join(sorted(nets)))
    out["rf_min_bend_radius_mm"] = _rf_bend_radius(board, nets)

    items = [it for it in _items(board)
             if it.get("kind") in ("pad", "track", "via") and it.get("net")]
    rf_items = [it for it in items if str(it["net"]) in nets]
    other = [it for it in items if str(it["net"]) not in nets]
    if not rf_items:
        for key in ("rf_min_width_mm", "rf_min_spacing_to_other_trace_mm",
                    "rf_min_distance_to_edge_mm"):
            out[key] = _na("the RF net(s) %s have no copper item that states a net"
                           % ", ".join(sorted(nets)))
    else:
        # The narrowest RF line's own width, on its own: the 3W rules
        # (spacing, bend radius) are three times THE LINE's width, so the
        # catalogue cannot compute their limits without it.
        w_all = _rf_min_width(rf_items)
        out["rf_min_width_mm"] = _rec(
            w_all, "the narrowest RF track on the board",
            "min width over every track on a net the file's names call RF") \
            if w_all > 0 else _na("no RF track states a width")
        rf_layers = set(l for r in rf_items for l in (r.get("layers") or []))
        same_layer = [it for it in other
                      if set(it.get("layers") or []) & rf_layers]
        best = _min_gap_between(rf_items, same_layer, 5.0) if same_layer else None
        if best is None:
            out["rf_min_spacing_to_other_trace_mm"] = {
                "lower_bound": 5.0,
                "where": "no copper of a different net is within 5 mm of an RF item",
                "method": "edge-to-edge distance from every RF copper item to every "
                          "other-net copper item on a shared layer, bucketed at 5 mm",
            }
        else:
            d, a, b = best
            out["rf_min_spacing_to_other_trace_mm"] = _rec(
                d, "%s to %s" % (a.get("ref"), b.get("ref")),
                "edge-to-edge distance from every RF copper item to every other-net "
                "copper item on a layer both use; the rule wants 3 line widths")
            w = _rf_min_width(rf_items)
            if w > 0:
                out["rf_min_spacing_to_other_trace_mm"]["in_line_widths"] = d / w
                out["rf_min_spacing_to_other_trace_mm"]["rf_min_width_mm"] = w
        out["rf_min_distance_to_edge_mm"] = _norm(
            _min_copper_to_edge(board, rf_items, _edge_segments(board),
                                window_mm=5.0),
            "edge-to-edge distance from RF copper to the board outline")

    ant = set(pop["antenna"])
    if not ant:
        out["antenna_min_distance_to_other_rf_mm"] = _na(
            "no net name on this board says which net is the antenna feed (the "
            "names read %s), so the keep-out has nothing to be measured around"
            % (", ".join(sorted(nets)) or "none"))
    else:
        ant_items = [it for it in items if str(it["net"]) in ant]
        rest = [it for it in items if str(it["net"]) not in ant]
        best = _min_gap_between(ant_items, rest, 20.0) if ant_items and rest else None
        if best is None:
            out["antenna_min_distance_to_other_rf_mm"] = {
                "lower_bound": 20.0,
                "where": "nothing of another net is within 20 mm of %s"
                         % ", ".join(sorted(ant)),
                "method": "edge-to-edge distance from the antenna net's copper to "
                          "every other net's copper, bucketed at 20 mm",
            }
        else:
            d, a, b = best
            out["antenna_min_distance_to_other_rf_mm"] = _rec(
                d, "%s to %s" % (a.get("ref"), b.get("ref")),
                "edge-to-edge distance from the antenna net's copper to every other "
                "net's copper, whatever the layer; the rule wants 10 mm")

    # Z0 is NOT measured: the width and the stackup are here, but the impedance the
    # line was DESIGNED to is a decision, and `feedline_impedance` is the tool that
    # says what a given width really is. Assuming 50 ohm would turn a decision into
    # a measurement.
    out["rf_line_z0_error_pct"] = _na(
        "the target impedance is a design decision, not a property of the file: "
        "this board routes a %.4g mm line as RF, and what that width really is on "
        "this stackup is what feedline_impedance answers; pass the target through "
        "`facts` to grade the error" % _rf_min_width(rf_items))
    return out

_CAP_REF = re.compile(r"^C\d")
# A decoupling cap is a capacitor by reference designator (the file states C1,
# C12; nothing is inferred from the value), and the pin it serves is a pad of a
# part with three or more pads - a two-pin part is a passive, and "the cap serves
# the resistor next to it" would make every board pass.
_MULTI_PIN = 3


def _q(v):
    return int(round(float(v) * 10000.0))


def _copper_graph(board, net):
    """Layer-aware copper graph of ONE net: tracks, arcs, vias, pads.

    Nodes are ``(layer, x*1e4, y*1e4)`` so two spellings of the same coordinate
    (-95.10000000000198 and -95.1) are one node. Pad anchors become their own
    node, joined to any track endpoint or via that lands inside the pad polygon.

    This is the only honest way to answer either decoupling rule: pad-to-pad
    distance is a straight line through the laminate, and the rule is about the
    current's path. A pour-connected pair has NO track path at all, which the
    caller reports instead of pretending the straight line is the trace.
    """
    adj = {}

    def link(a, b, w, via):
        adj.setdefault(a, []).append((b, w, via))
        adj.setdefault(b, []).append((a, w, via))

    for t in board.tracks:
        if t.get("net") != net:
            continue
        lay = t.get("layer")
        pts = t.get("_points") if t.get("_arc") else None
        if pts:
            seq = [(float(x), float(y)) for x, y in pts]
        else:
            a, b = t.get("start"), t.get("end")
            if not (a and b):
                continue
            seq = [(float(a[0]), float(a[1])), (float(b[0]), float(b[1]))]
        for i in range(len(seq) - 1):
            x0, y0 = seq[i]
            x1, y1 = seq[i + 1]
            link((lay, _q(x0), _q(y0)), (lay, _q(x1), _q(y1)),
                 math.hypot(x1 - x0, y1 - y0), False)
    for v in board.vias:
        if v.get("net") != net:
            continue
        x, y = float(v["x"]), float(v["y"])
        layers = [l for l in (v.get("layers") or [v.get("from"), v.get("to")]) if l]
        for i in range(len(layers) - 1):
            link((layers[i], _q(x), _q(y)), (layers[i + 1], _q(x), _q(y)), 0.0, True)

    pads = []
    keys = list(adj)
    for f in board.footprints:
        for pad in (f.get("pads") or []):
            if pad.get("net") != net:
                continue
            poly = _pad_points(pad)
            if not poly:
                continue
            idx = len(pads)
            pads.append({"idx": idx, "ref": f.get("ref"),
                         "number": pad.get("number"), "net": net,
                         "node": ("PAD", idx), "poly": poly,
                         "layers": set(pad.get("layers") or [])})
            x0, y0, x1, y1 = G.bbox_of(poly)
            node = ("PAD", idx)
            adj.setdefault(node, [])
            for k in keys:
                if k[0] not in pads[idx]["layers"]:
                    continue
                px, py = k[1] / 10000.0, k[2] / 10000.0
                if not (x0 - 1e-6 <= px <= x1 + 1e-6
                        and y0 - 1e-6 <= py <= y1 + 1e-6):
                    continue
                if not G.point_in_polygon((px, py), poly):
                    continue
                link(node, k, 0.0, False)
    return adj, pads


def _shortest(adj, src, skip_vias):
    """Dijkstra from ``src``. ``skip_vias`` leaves the via edges out.

    No via edge has a length, so the distance is the copper length of the route.
    With ``skip_vias`` the answer is "can these two pads be joined without
    changing layer at all" - the question the same-layer rule actually asks.
    """
    import heapq
    best = {src: 0.0}
    pq = [(0.0, 0, src)]
    tick = 1
    while pq:
        d, _, u = heapq.heappop(pq)
        if d > best.get(u, float("inf")) + 1e-12:
            continue
        for v, w, via in adj.get(u) or ():
            if via and skip_vias:
                continue
            nd = d + w
            if nd < best.get(v, float("inf")) - 1e-12:
                best[v] = nd
                heapq.heappush(pq, (nd, tick, v))
                tick += 1
    return best


def _decoupling(board):
    """How far a decoupling capacitor's copper has to travel to reach its pin.

    Two numbers the P1 catalogue asks for, measured on the ROUTED COPPER rather
    than on pad centres:

    ``decoupling_max_distance_to_pin_mm``
        the worst capacitor on the board: the shortest copper path from one of its
        own pads to any same-net pad of a multi-pin part, taking the most
        favourable pad (that is the lower bound of "the pin it serves").
    ``decoupling_via_in_path``
        whether that path has no via-free alternative - i.e. the connection
        cannot exist without changing layer.

    What this is NOT: it does not know which pin the capacitor was *bought* to
    serve, and it cannot answer for a capacitor whose net is carried by a pour
    (there is no track path in the file to measure). Both residues are counted
    and named instead of being folded into the number.
    """
    out = {}
    caps, targets = [], []
    for f in board.footprints:
        ref = str(f.get("ref") or "")
        pads = [p for p in (f.get("pads") or []) if p.get("net") is not None]
        if not pads:
            continue
        (caps if _CAP_REF.match(ref) else targets).append((ref, pads))
    targets = [(r, p) for r, p in targets if len(p) >= _MULTI_PIN]
    if not caps:
        why = "no footprint whose reference designator starts with C (capacitor)"
        out["decoupling_max_distance_to_pin_mm"] = _na(why)
        out["decoupling_via_in_path"] = _na(why)
        return out
    if not targets:
        why = ("no multi-pin part (3+ pads) to decouple: the denominator of this "
               "rule is absent, so there is no distance to quote")
        out["decoupling_max_distance_to_pin_mm"] = _na(why)
        out["decoupling_via_in_path"] = _na(why)
        return out

    tf = {}
    for ref, pads in targets:
        for p in pads:
            tf.setdefault(p.get("net"), []).append((ref, p))
    graphs = {}

    def graph(net):
        if net not in graphs:
            graphs[net] = _copper_graph(board, net)
        return graphs[net]

    served, unserved = [], []
    for ref, pads in caps:
        best = None
        for p in pads:
            net = p.get("net")
            if not tf.get(net):
                continue
            adj, gnodes = graph(net)
            src = None
            for g in gnodes:
                if g["ref"] == ref and str(g["number"]) == str(p.get("number")):
                    src = g["node"]
                    break
            if src is None:
                continue
            dist = _shortest(adj, src, skip_vias=False)
            for tref, q in tf[net]:
                tgt = None
                for g in gnodes:
                    if g["ref"] == tref and str(g["number"]) == str(q.get("number")) \
                            and g["net"] == net:
                        tgt = g["node"]
                        break
                if tgt is None or tgt not in dist:
                    continue
                if best is None or dist[tgt] < best[0]:
                    best = (dist[tgt], p, q, tref, adj, src, tgt)
        if best is None:
            unserved.append(ref)
        else:
            served.append((ref, best))

    if not served:
        why = ("no capacitor reaches a multi-pin part through routed copper: %s "
               "(a pour-carried net has no track path to measure)"
               % ", ".join(unserved[:6]))
        out["decoupling_max_distance_to_pin_mm"] = _na(why)
        out["decoupling_via_in_path"] = _na(why)
        return out

    served.sort(key=lambda s: -s[1][0])
    d, p, q, tref, adj, src, tgt = served[0][1]
    out["decoupling_max_distance_to_pin_mm"] = {
        "value": float(d),
        "where": "%s.%s to %s.%s on %s" % (served[0][0], p.get("number"), tref,
                                           q.get("number"), p.get("net")),
        "method": "shortest path over the routed copper of the net (tracks, arcs, "
                  "vias, pads; pad anchors joined to any track landing inside the "
                  "pad), from the capacitor pad to the nearest same-net pad of a "
                  "multi-pin part - the most favourable reading of 'the pin it "
                  "serves', so this is a lower bound; %d capacitor(s) measured, "
                  "%d have no track path at all" % (len(served), len(unserved)),
        "examples": [{"cap": r, "part": b[3], "net": b[1].get("net"),
                      "path_mm": round(b[0], 4)} for r, b in served[:6]],
    }
    forced = []
    for ref, b in served:
        free = _shortest(b[4], b[5], skip_vias=True)
        if b[6] not in free:
            forced.append((ref, b))
    out["decoupling_via_in_path"] = {
        "value": bool(forced),
        "where": ("%d of %d measured capacitor(s) have no via-free copper path to "
                  "the pin they reach%s"
                  % (len(forced), len(served),
                     (": " + ", ".join(x[0] for x in forced[:6])) if forced else "")),
        "method": "the same shortest-path graph is walked again with the via edges "
                  "removed: a pad pair that drops out of the reachable set cannot be "
                  "joined without changing layer",
    }
    residues = []
    if unserved:
        residues.append("%d capacitor(s) have no track path (pour-carried net): %s"
                        % (len(unserved), ", ".join(unserved[:8])))
    if residues:
        out["decoupling_max_distance_to_pin_mm"]["note"] = "; ".join(residues)
        out["decoupling_via_in_path"]["note"] = "; ".join(residues)
    return out


def _npth_ring(npth_holes, cu_items, window_mm=5.0):
    """Copper keep-out around an NPTH: hole edge to the nearest copper, any net.

    A fab clears a ring of copper around a non-plated hole (dry-film plugging),
    so this is the number the rule wants - not the pad's own ring, which does not
    exist on an NPTH.
    """
    best = None
    for h in npth_holes:
        pt = (h["x"], h["y"])
        r = h["drill_mm"] / 2.0
        for it in cu_items:
            x0, y0, x1, y1 = _item_bbox(it)
            if not (x0 - window_mm <= pt[0] <= x1 + window_mm
                    and y0 - window_mm <= pt[1] <= y1 + window_mm):
                continue
            gap = max(0.0, _dist_item_to_point(it, pt) - r)
            if gap <= window_mm and (best is None or gap < best[0]):
                best = (gap, h, it)
    if best is None:
        return {"lower_bound": float(window_mm),
                "where": "no copper is within %.2f mm of any NPTH hole" % window_mm,
                "method": "hole edge to nearest copper of any net (NPTH keep-out)"}
    gap, h, it = best
    return _rec(gap, "%s vs %s" % (h["ref"], it.get("ref")),
                "hole edge to nearest copper of any net (NPTH keep-out)")

_PLANE_STEP_MM = 0.2


def _raster(rings, step=_PLANE_STEP_MM):
    """Even-odd raster of a set of rings on a ``step`` grid.

    A scanline per grid row, crossing lists collected from every ring at once, so
    a hole (or an island inside a hole) comes out right without knowing which
    ring is which. Filled by spans, so the cost is the EDGE count and not the
    vertex-by-vertex point-in-polygon a naive test would pay: the reference pour
    on a real board is a 19 766-vertex polygon, and testing 35 000 sample points
    against it directly took 138 s on sky_star.
    """
    if not rings:
        return None
    xs = [p[0] for r in rings for p in r]
    ys = [p[1] for r in rings for p in r]
    x0, y0 = min(xs) - step, min(ys) - step
    nx = int((max(xs) - x0) / step) + 2
    ny = int((max(ys) - y0) / step) + 2
    bits = bytearray(nx * ny)
    rows = {}
    for r in rings:
        n = len(r)
        for i in range(n):
            ax, ay = r[i]
            bx, by = r[(i + 1) % n]
            if ay == by:
                continue
            ylo, yhi = (ay, by) if ay < by else (by, ay)
            # The scanline of row j sits at the CENTRE of the band that row j
            # stands for, so a query point and the crossings that decide it are
            # never more than half a cell apart. Placing the scans on the lattice
            # itself is what made this disagree with a point-in-polygon test: the
            # sky_star pours sit on a 0.05 mm lattice, so vertices landed exactly
            # on the rows, ties doubled the crossing count and the parity invented
            # 24 "holes" in a solid plane.
            j0 = int(math.ceil((ylo - y0) / step - 0.5))
            j1 = int(math.floor((yhi - y0) / step - 0.5))
            for j in range(max(j0, 0), min(j1, ny - 1) + 1):
                y = y0 + (j + 0.5) * step
                # Half-open [ylo, yhi): a vertex that lands exactly on a scanline
                # then counts once (on the edge that starts there), never twice.
                if not (ylo <= y < yhi):
                    continue
                rows.setdefault(j, []).append(ax + (bx - ax) * (y - ay) / (by - ay))
    for j, xcross in rows.items():
        xcross.sort()
        base = j * nx
        for k in range(0, len(xcross) - 1, 2):
            # Fill the cells whose CENTRE lies inside the span, i.e. the same
            # convention ``covered`` reads back with.
            ia = int(math.ceil((xcross[k] - x0) / step - 0.5))
            ib = int(math.floor((xcross[k + 1] - x0) / step - 0.5))
            for i in range(max(ia, 0), min(ib, nx - 1) + 1):
                bits[base + i] = 1

    def covered(x, y):
        i = int(math.floor((x - x0) / step))
        j = int(math.floor((y - y0) / step))
        if i < 0 or j < 0 or i >= nx or j >= ny:
            return False
        return bits[j * nx + i] == 1

    return covered, (nx, ny, step)


def _copper_order(board):
    """The copper layers in stack order, or None when the file does not say."""
    names = [l.get("name") for l in (board.stackup or []) if l.get("name")]
    names = [n for n in names if str(n).endswith(".Cu")]
    if len(names) >= 2:
        return names
    seen = set()
    for t in board.tracks:
        if t.get("layer"):
            seen.add(str(t["layer"]))
    for z in board.zones:
        for l in (z.get("layers") or []):
            seen.add(str(l))
    if seen == {"F.Cu", "B.Cu"}:
        return ["F.Cu", "B.Cu"]
    return None


_FINE_MM = 0.02
_BAND_MM = 0.25

class _Scan(object):
    """Exact even-odd coverage of a set of rings, edges bucketed by y band.

    This exists to CONFIRM what the coarse raster flagged, because the raster is
    half a cell coarse by construction: on sky_star it calls a track uncovered
    whenever the track runs within 0.1 mm of a pour boundary, and two of its
    twenty-seven candidates were exactly that (re-read at 0.02 mm, they are over
    solid copper end to end). Nothing here is reported without this re-read.
    """

    def __init__(self, rings):
        self.ymin = min(p[1] for r in rings for p in r)
        self.bands = {}
        for r in rings:
            n = len(r)
            for i in range(n):
                ax, ay = r[i]
                bx, by = r[(i + 1) % n]
                if ay == by:
                    continue
                lo, hi = (ay, by) if ay < by else (by, ay)
                b0 = int(math.floor((lo - self.ymin) / _BAND_MM))
                b1 = int(math.floor((hi - self.ymin) / _BAND_MM))
                for b in range(b0, b1 + 1):
                    self.bands.setdefault(b, []).append((ax, ay, bx, by))

    def covered(self, x, y):
        n = 0
        for ax, ay, bx, by in self.bands.get(
                int(math.floor((y - self.ymin) / _BAND_MM)), ()):
            if (ay > y) != (by > y):
                if x < (bx - ax) * (y - ay) / (by - ay) + ax:
                    n += 1
        return n % 2 == 1


def _track_seq(t):
    """Sample points along a track's centreline, arcs included, or None."""
    if t.get("_arc") and t.get("_points"):
        return [(float(x), float(y)) for x, y in t["_points"]]
    a, b = t.get("start"), t.get("end")
    if not (a and b):
        return None
    return [(float(a[0]), float(a[1])), (float(b[0]), float(b[1]))]


def _walk(seq, step, cap=20000):
    """Points every ``step`` mm along a polyline, ends included."""
    length = sum(math.hypot(seq[i + 1][0] - seq[i][0], seq[i + 1][1] - seq[i][1])
                 for i in range(len(seq) - 1))
    if length / step > cap:
        step = length / float(cap)
    out = []
    for i in range(len(seq) - 1):
        x0, y0 = seq[i]
        x1, y1 = seq[i + 1]
        steps = max(2, int(math.hypot(x1 - x0, y1 - y0) / step) + 1)
        for k in range(steps):
            f = k / float(steps - 1)
            out.append((x0 + (x1 - x0) * f, y0 + (y1 - y0) * f))
    return out


def _pour_area_by_net(board):
    """Filled copper area per pour net, over every layer.

    The largest flood is the board's return net: it is the one the designer built
    a plane out of. Asking "which net is ground" by NAME fails on a board whose
    ground is `PGND`, `0V` or `AGND`, and on a board whose power is poured too
    (sky_star pours +3V3 and +5V).
    """
    areas = {}
    for z in board.zones:
        if z.get("is_keepout"):
            continue
        net = z.get("net")
        if not net:
            continue
        a = 0.0
        for poly in (z.get("filled_polygons") or []):
            if len(poly) >= 3:
                a += abs(G.polygon_area(poly))
        if a > 0:
            areas[net] = areas.get(net, 0.0) + a
    return areas

def _reference_plane(board):
    """Does a signal run over a place where its return plane has no copper?

    For each pair of ADJACENT copper layers, the return net's copper on the other
    layer is taken as the reference, and every track on the near layer is walked
    along its centreline. A **split crossing** is a run of uncovered samples with
    covered samples on BOTH sides: the track enters a hole in the return copper
    and leaves it again. A run that reaches one end of the track is counted
    separately as ``off_plane``.

    The reference is the net the board pours the MOST copper of, not "whatever is
    on the adjacent layer". On a 4-layer board whose inner layers are GND and +5V,
    pairing every outer track with the +5V plane reported 297 crossings on a
    finished, routed board - a +5V plane is not what a signal returns on, and the
    rule was measuring the wrong copper. Other pours on the adjacent layer are
    named in `note` and not tested.

    Two passes, because neither alone is honest. A raster at ``_PLANE_STEP_MM``
    decides which tracks are worth a close look; every track it flags is re-read
    at ``_FINE_MM`` by ``_Scan``, and only what the re-read agrees with is
    reported. The one thing the coarse pass can still do is MISS: a gap narrower
    than ``_PLANE_STEP_MM`` under a track that is otherwise covered reads as
    copper.

    A void in a plane is not always a split: the clearance around a via or a pad
    of another net is a hole the return current simply flows around, and every
    board has thousands of them. Widths are therefore bucketed, and only a gap
    wider than ``_SPLIT_MIN_MM`` is graded - the narrow ones are counted in
    `bands` and named in `note`, because a rule that reports an antipad ring as a
    return-path break is a rule nobody can act on.
    """
    order = _copper_order(board)
    if not order:
        why = ("the copper layers cannot be ordered from the file (no stackup, and "
               "not a two-layer board), so which layer is the reference is unknown")
        return {"signal_crosses_plane_split": _na(why)}
    areas = _pour_area_by_net(board)
    if not areas:
        return {"signal_crosses_plane_split": _na(
            "the board has no filled pour, so there is no reference plane to cross")}
    ret = max(areas, key=lambda k: areas[k])
    pours, others = {}, {}
    for z in board.zones:
        if z.get("is_keepout") or z.get("net") != ret:
            continue
        for l in (z.get("layers") or []):
            for poly in (z.get("filled_polygons") or []):
                if len(poly) >= 3:
                    pours.setdefault(str(l), []).append(
                        [(float(x), float(y)) for x, y in poly])
    for z in board.zones:
        if z.get("is_keepout") or z.get("net") == ret:
            continue
        for l in (z.get("layers") or []):
            if z.get("filled_polygons"):
                others.setdefault(str(l), set()).add(z.get("net"))
    crossings, off_plane, unmeasured = [], [], []
    for idx, layer in enumerate(order):
        refs = [order[j] for j in (idx - 1, idx + 1) if 0 <= j < len(order)]
        if not refs:
            continue
        rings = []
        for r in refs:
            rings.extend(pours.get(r) or [])
        if not rings:
            alt = "; ".join("%s carries %s" % (r, "/".join(sorted(map(str, others[r]))))
                            for r in refs if others.get(r))
            why = ("no %s pour on %s" % (ret, "/".join(refs))
                   + ((" (the adjacent copper is %s, which is not the return net)"
                       % alt) if alt else ""))
            if why not in unmeasured:
                unmeasured.append(why)
            continue
        cov = _raster(rings)[0]
        scan = None
        for t in board.tracks:
            if str(t.get("layer")) != layer:
                continue
            seq = _track_seq(t)
            if not seq:
                continue
            if all(cov(x, y) for x, y in _walk(seq, _PLANE_STEP_MM)):
                continue                      # nothing the coarse pass doubts
            if scan is None:
                scan = _Scan(rings)
            flags = [scan.covered(x, y) for x, y in _walk(seq, _FINE_MM)]
            inside, edge = 0.0, False
            i, n = 0, len(flags)
            while i < n:
                if flags[i]:
                    i += 1
                    continue
                k = i
                while k < n and not flags[k]:
                    k += 1
                run = (k - i) * _FINE_MM
                if i == 0 or k == n:
                    edge = True
                else:
                    inside = max(inside, run)
                i = k
            row = {"net": t.get("net"), "layer": layer,
                   "reference": "%s on %s" % (ret, "/".join(refs)),
                   "uncovered_mm": round(inside, 3),
                   "where": "%.3f, %.3f" % (seq[0][0], seq[0][1])}
            if inside > 0:
                crossings.append(row)
            if edge:
                off_plane.append(row)
    if not crossings and len(unmeasured) == len(order):
        return {"signal_crosses_plane_split": _na(
            "; ".join(unmeasured) or "no reference copper to test against")}
    widest = max(crossings, key=lambda c: c["uncovered_mm"]) if crossings else None
    counts, bands = {}, {}
    graded = []
    for c in crossings:
        key = "%s over %s" % (c["layer"], c["reference"])
        counts[key] = counts.get(key, 0) + 1
        w = c["uncovered_mm"]
        b = ("under %.1f mm" % _SPLIT_MIN_MM if w < _SPLIT_MIN_MM else
             "%.1f-%.1f mm" % (_SPLIT_MIN_MM, 2 * _SPLIT_MIN_MM) if w < 2 * _SPLIT_MIN_MM
             else "%.1f-%.1f mm" % (2 * _SPLIT_MIN_MM, 5 * _SPLIT_MIN_MM)
             if w < 5 * _SPLIT_MIN_MM else "over %.1f mm" % (5 * _SPLIT_MIN_MM))
        bands[b] = bands.get(b, 0) + 1
        if w >= _SPLIT_MIN_MM:
            graded.append(c)
    width_hist = {}
    for c in crossings:
        width_hist[round(c["uncovered_mm"], 1)] = width_hist.get(
            round(c["uncovered_mm"], 1), 0) + 1
    out = {
        "value": bool(graded),
        "where": ("%d track(s) run over a gap wider than %.1f mm in the %s copper "
                  "and come back onto it%s"
                  % (len(graded), _SPLIT_MIN_MM, ret,
                     ("; the widest is %s on %s over %s, uncovered for %.2f mm"
                      % (widest["net"], widest["layer"], widest["reference"],
                         widest["uncovered_mm"])) if widest else ""))
                 if graded else
                 ("no track runs over a %s gap wider than %.1f mm (%d narrower "
                  "one(s) are counted in `bands`, not graded)"
                  % (ret, _SPLIT_MIN_MM, len(crossings) - len(graded))),
        "method": "the largest copper flood on the board is taken as the return "
                  "net, and its pour on the layer adjacent to each routed layer is "
                  "the reference; a raster at %.1f mm picks the tracks that are "
                  "not covered end to end, each is re-read at %.2f mm by an exact "
                  "even-odd ray cast over every filled polygon, and a gap is graded "
                  "only above %.1f mm because a via or pad clearance is a hole the "
                  "current flows around, not a break"
                  % (_PLANE_STEP_MM, _FINE_MM, _SPLIT_MIN_MM),
        "reference_net": ret,
        "counts": counts,
        "bands": bands,
        "examples": sorted(graded, key=lambda c: -c["uncovered_mm"])[:8],
    }
    if not graded:
        out["examples"] = []
    if len(graded) != len(crossings):
        out["note"] = ("%d uncovered run(s) under %.1f mm are counted in `bands` "
                       "and not graded: at that width they are the clearance around "
                       "a via or a pad, which the return current flows around"
                       % (len(crossings) - len(graded), _SPLIT_MIN_MM))
    if off_plane:
        out["note"] = ((out.get("note", "") + "; ") if out.get("note") else "") + (
            "%d track(s) run over the edge of the reference copper (the uncovered "
            "run reaches one end) - a different thing and not counted as a split "
            "crossing" % len(off_plane))
    if unmeasured:
        out["note"] = ((out.get("note", "") + "; ") if out.get("note") else "") + \
            "; ".join(unmeasured)
    return {"signal_crosses_plane_split": out}

_SPLIT_MIN_MM = 1.0
_RETURN_VIA_MM = 1.0

def _pour_nets(board):
    """Nets the board itself carries by pouring copper.

    Not a name heuristic: a rail that has a filled pour IS the rail the board
    returns current on, whatever it is called.
    """
    nets = set()
    for z in board.zones:
        if z.get("is_keepout"):
            continue
        if not (z.get("filled_polygons") or []):
            continue
        if z.get("net"):
            nets.add(z.get("net"))
    return nets

def _return_vias(board):
    """How far each signal via is from the nearest via into the return copper.

    A layer change is where the return current has to jump the plane pair. If it
    has no via to do that within a fraction of a millimetre it jumps wherever it
    likes, and that loop both reflects and radiates.

    The number is bounded, the POPULATION is not: the rule speaks about RF and
    fast signals, and no board file says which nets those are. Counting every
    signal via condemns a GPIO-only dev board that has been manufactured and
    works, so the count is measured and the grading is left to
    ``grade_caps`` - a caller who knows the net set passes the count in.
    """
    pours = _pour_nets(board)
    if not pours:
        return {"layer_changes_without_return_via": _na(
            "no net is carried by a filled pour on this board, so there is no "
            "return copper for a layer change to return into")}
    ret = [v for v in board.vias if v.get("net") in pours]
    sig = [v for v in board.vias if v.get("net") not in pours]
    if not ret:
        return {"layer_changes_without_return_via": _na(
            "%d via(s) on the board, none of them on a poured net (%s): there is "
            "no return via anywhere, so a distance to the nearest one is not a "
            "measurement" % (len(board.vias), ", ".join(sorted(map(str, pours)))))}
    if not sig:
        return {"layer_changes_without_return_via": _rec(
            0, "no via outside the poured nets", "count of signal vias with no "
            "return via within %.1f mm" % _RETURN_VIA_MM)}
    worst, rows = None, []
    for v in sig:
        d = min(math.hypot(v["x"] - r["x"], v["y"] - r["y"]) for r in ret)
        rows.append((d, v.get("net"), v["x"], v["y"]))
        if worst is None or d > worst[0]:
            worst = (d, v.get("net"), v["x"], v["y"])
    rows.sort(key=lambda r: -r[0])
    far = [r for r in rows if r[0] > _RETURN_VIA_MM]
    out = _rec(len(far),
               "%d of %d signal via(s) have no via into the poured copper (%s) "
               "within %.1f mm; the worst is %s at %.2f, %.2f, %.2f mm away"
               % (len(far), len(sig), "/".join(sorted(map(str, pours))),
                  _RETURN_VIA_MM, worst[1], worst[0], worst[2], worst[3]),
               "distance from each via on a net with no pour to the nearest via on "
               "a poured net, counted when it exceeds %.1f mm (the rule asks for "
               "ground vias 'within a few tenths of a mm'; the threshold only picks "
               "which of the measured distances to count, every distance is in "
               "`examples`)" % _RETURN_VIA_MM)
    out["examples"] = [{"net": r[1], "x": round(r[2], 3), "y": round(r[3], 3),
                        "nearest_return_via_mm": round(r[0], 3)} for r in rows[:8]]
    return {"layer_changes_without_return_via": out}

def _power_traces(board):
    """The narrowest piece of copper on a rail the board pours.

    Ampacity is per trace, and the binding number is the NARROWEST segment: a
    rail can be 2 mm wide for 30 mm and still be a fuse at the one 0.2 mm neck
    the router left by a pad. Which current a rail carries is not in the file,
    so the width is measured here and the limit comes from the caller
    (`current_a` + `current_standard`, see ``_limit_width_for_current``).
    """
    pours = _pour_nets(board)
    if not pours:
        return {"power_trace_width_mm": _na(
            "no net is carried by a filled pour, so which nets are rails cannot be "
            "read from the board")}
    rows, seen, poured_only = [], set(), []
    for t in board.tracks:
        net = t.get("net")
        if net not in pours:
            continue
        w = t.get("width")
        if w is None:
            continue
        seen.add(net)
        start = t.get("start") or (0.0, 0.0)
        rows.append((float(w), net, float(start[0]), float(start[1])))
    for net in pours:
        if net not in seen:
            poured_only.append(str(net))
    if not rows:
        return {"power_trace_width_mm": _na(
            "every poured net (%s) is carried by copper pour alone, so there is no "
            "power TRACK to measure" % "/".join(sorted(map(str, pours))))}
    rows.sort()
    narrow = rows[0]
    out = _rec(narrow[0],
               "narrowest copper on a poured net: %s at %.2f, %.2f (%.3f mm, %d "
               "track(s) on %d poured net(s))"
               % (narrow[1], narrow[2], narrow[3], narrow[0], len(rows),
                  len(seen)),
               "the narrowest track width among the nets this board carries by "
               "pouring; it is a lower bound on the ampacity of the rail because a "
               "rail is only as wide as its neck")
    if poured_only:
        out["note"] = ("%s carried by pour alone, no track to measure"
                       % "/".join(poured_only))
    return {"power_trace_width_mm": out}

def grade_caps(m):
    """Facts this module measured but refuses to GRADE, and why.

    The measurement is real; the population is not the rule's. Feeding it to the
    grader would turn "I measured every capacitor on a routed net" into a verdict
    about decoupling capacitors, which is the exact laundering this package
    exists to prevent. The number stays visible as a diagnostic, and the rule
    stays undetermined until the caller names the cap-to-pin pairs.
    """
    caps = {}
    row = m.get("test_points_on_rails_and_connectors") or {}
    if isinstance(row.get("value"), bool):
        caps["test_points_on_rails_and_connectors"] = (
            "measured, not graded: whether a header pin counts as a test point "
            "depends on the fixture the product is tested on - a pogo bed needs "
            "pads, a hand probe needs headers - and no board file says which "
            "(%s)" % (row.get("where") or "?"))
    row = m.get("crystal_has_guard_ring") or {}
    if isinstance(row.get("value"), bool):
        caps["crystal_has_guard_ring"] = (
            "measured, not graded: copper around a crystal is a practice for a "
            "clock that has to be quiet, and no board file says how quiet this "
            "clock has to be - two finished boards in the corpus (interf_u, video) "
            "put their crystal in no copper at all and they shipped (%s). Name the "
            "crystal whose clock matters and this fact is graded from it."
            % (row.get("where") or "?"))
    row = m.get("decoupling_max_distance_to_pin_mm") or {}
    if "value" in row:
        caps["decoupling_max_distance_to_pin_mm"] = (
            "measured, not graded: the population is every capacitor whose net is "
            "carried by a track to a multi-pin part, and that set contains "
            "debounce and filter capacitors that are not decoupling capacitors "
            "(worst: %s). Name the pairs you meant - the capacitor pad and the pin "
            "it serves - and this fact is graded from them."
            % (row.get("where") or "?"))
    row = m.get("decoupling_via_in_path") or {}
    if isinstance(row.get("value"), bool):
        caps["decoupling_via_in_path"] = (
            "measured, not graded: same population caveat as "
            "decoupling_max_distance_to_pin_mm (%s)"
            % (row.get("where") or "?"))
    row = m.get("signal_crosses_plane_split") or {}
    if isinstance(row.get("value"), bool):
        caps["signal_crosses_plane_split"] = (
            "measured, not graded: the crossing is real, but a gap in the return "
            "copper only breaks a fast signal, and no board file says which nets "
            "are fast. Every finished board in the KiCad demo corpus crosses at "
            "least one %s gap, so a blanket verdict condemns designs that work "
            "(%s). Name the nets you meant and this fact is graded from them."
            % ("1.0 mm", row.get("where") or "?"))
    row = m.get("layer_changes_without_return_via") or {}
    if "value" in row:
        caps["layer_changes_without_return_via"] = (
            "measured, not graded: the population is every via that is not on a "
            "poured net, and the rule asks about RF / fast-signal layer changes "
            "only - no board file says which nets are fast, and counting a GPIO "
            "dev board's vias condemns a design that works (%s). Name the nets you "
            "meant and this fact is graded from them."
            % (row.get("where") or "?"))
    return caps


def catalog_facts(m):
    """Map the measurements onto the fact names ``calc/rules.py`` grades.

    A bool stays a bool: the grader rejects anything else as "must be true/false",
    and multiplying by 1.0 (which is what a blind scale would do) turned every
    boolean rule into a false undetermined.
    """
    facts = {}

    def take(key, fact):
        row = m.get(key) or {}
        val = row.get("value")
        if isinstance(val, bool) or isinstance(val, (int, float)):
            facts[fact] = val

    def take_lb(key, fact):
        """A measured distance, else a BOUND that can only say "at least this far".

        The measured value is always taken. A minimum-distance rule whose scan
        found nothing inside its window has a lower bound, not a value: every
        gap is >= that bound. Handing the bound over as the measurement can only
        be wrong in the direction of a pass, so it is only done where the window
        is wider than the rule's own limit - the three RF keep-outs (5 mm and
        20 mm windows against a 3 mm and a 10 mm limit). Everywhere else a bound
        stays a bound and the rule stays undetermined.

        Supplying only the bound (the first version of this function) dropped
        the measured value on the floor and left the rule undetermined on every
        board that DID have close copper - the exact opposite of the point.
        """
        row = m.get(key) or {}
        val = row.get("value")
        if isinstance(val, (int, float)) and not isinstance(val, bool):
            facts[fact] = float(val)
            return
        if "lower_bound" in row and "value" not in row:
            facts[fact] = float(row["lower_bound"])

    take("min_track_width", "min_trace_width_mm")
    take("min_plated_drill", "min_via_hole_mm")
    take("diffpair_length_skew_mm", "diffpair_length_skew_mm")
    take("trace_under_crystal", "trace_under_crystal")
    take("trace_under_crystal_inner", "trace_under_crystal_inner")
    take("trace_under_magnetic", "trace_under_magnetic")
    take("via_under_magnetic", "via_under_magnetic")
    take("trace_under_magnetic_deep", "trace_under_magnetic_deep")
    take("trace_under_magnetic_graze", "trace_under_magnetic_graze")
    take("decoupling_max_distance_to_pin_mm", "decoupling_max_distance_to_pin_mm")
    take("layer_changes_without_return_via", "layer_changes_without_return_via")
    take("power_trace_width_mm", "power_trace_width_mm")
    take("signal_crosses_plane_split", "signal_crosses_plane_split")
    take("decoupling_via_in_path", "decoupling_via_in_path")
    # The assembly and rail facts: every one of these rules used to answer with
    # "no measurement supplied", which is true of this module and says nothing
    # about the board.
    take("silkscreen_over_pad", "silkscreen_over_pad")
    take("power_pad_has_thermal_vias", "power_pad_has_thermal_vias")
    take("stitching_via_pitch_mm", "stitching_via_pitch_mm")
    take("diffpair_gap_varies", "diffpair_gap_varies")
    take("ics_without_bulk_cap", "ics_without_bulk_cap")
    take("test_points_on_rails_and_connectors", "test_points_on_rails_and_connectors")
    take("min_mask_expansion", "min_mask_expansion")
    take("crystal_has_guard_ring", "crystal_has_guard_ring")
    # The RF family, measured on the nets the file's names call RF.
    take("has_right_angle_bend_on_rf", "has_right_angle_bend_on_rf")
    take("rf_min_bend_radius_mm", "rf_min_bend_radius_mm")
    take("rf_min_width_mm", "rf_line_width_mm")
    take_lb("rf_min_spacing_to_other_trace_mm",
            "rf_min_spacing_to_other_trace_mm")
    take_lb("rf_min_distance_to_edge_mm", "rf_min_distance_to_edge_mm")
    take_lb("antenna_min_distance_to_other_rf_mm",
            "antenna_min_distance_to_other_rf_mm")
    return facts


def fact_refusals(m):
    """The reason a fact has no value, in the measurement's own words."""
    out = {}
    for key, row in m.items():
        if isinstance(row, dict) and row.get("unmeasurable"):
            out[key] = row["unmeasurable"]
    return out

# Facts whose measurement this module can make but whose POPULATION it cannot
# bound, so they are reported and not graded (see `grade_caps`).
CAP_FACTS = ("decoupling_max_distance_to_pin_mm", "decoupling_via_in_path",
             "layer_changes_without_return_via", "signal_crosses_plane_split",
             "test_points_on_rails_and_connectors")


def failing_examples(m, key, limit):
    row = m.get(key) or {}
    return row.get("examples") or []
