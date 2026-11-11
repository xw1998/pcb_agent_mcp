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
    return {"value": float(value), "where": where, "method": method}


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

def _split_pair(name, fam):
    """Return (base, suffix) when `name` ends in one of `fam`'s suffixes."""
    for suf in fam:
        if name.endswith(suf) and len(name) > len(suf) + 1:
            return name[:len(name) - len(suf)], suf
    return None, None

_XTAL_WORDS = ("crystal", "resonator", "xtal", "quartz")

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
    cu_items = [it for it in cu_all if it.get("net")]
    m["copper_items_without_net"] = {
        "value": float(len(cu_all) - len(cu_items)),
        "where": ", ".join(str(it.get("ref")) for it in cu_all
                           if not it.get("net"))[:200],
        "method": "copper items (pads/tracks/vias/graphics) that state no net and "
                  "are therefore excluded from every different-net spacing rule",
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
    via_holes = [h for h in holes if h["kind"] == "via"]
    pad_holes = [h for h in holes if h["kind"] == "pad"]
    # Plated vs non-plated is not cosmetic: an NPTH mounting hole has no copper
    # collar, so an annular-ring or plated-hole rule that counted it would report
    # ring = 0 and condemn a perfectly buildable board (pic_programmer P101).
    def _plated(h):
        return (h.get("pad_type") in (None, "thru_hole")
                and h["diameter_mm"] > h["drill_mm"] + 1e-9)
    plated_holes = [h for h in pad_holes if _plated(h)]
    npth_holes = [h for h in pad_holes if not _plated(h)]
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
    m["min_copper_to_edge"] = _norm(
        _min_copper_to_edge(board, cu_items, _edge_segments(board),
                            window_mm=window_mm),
        "edge-to-edge distance from copper to the board outline / cut-out")

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
    candidates = {}
    for fam in _PAIR_SUFFIXES:
        for name in lens:
            base, suf = _split_pair(name, fam)
            if base is not None:
                candidates.setdefault((fam, base), set()).add(name)
    pairs, done = [], set()
    for (fam, base), members in candidates.items():
        if len(members) != 2:
            continue
        n1, n2 = sorted(members)
        if (n1, n2) in done:
            continue
        done.add((n1, n2))
        pairs.append({"a": n1, "b": n2, "skew_mm": abs(lens[n1] - lens[n2])})
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
        for f in xtals:
            box = _bbox4((f.get("courtyard") or {}).get("bbox")) or _bbox4(f.get("bbox"))
            if box is None:
                continue
            own = {p.get("net") for p in (f.get("pads") or []) if p.get("net")}
            x0, y0, x1, y1 = box
            for t in board.tracks:
                if not t.get("net") or t["net"] in own:
                    continue
                a, b = t.get("start"), t.get("end")
                if not (a and b):
                    continue
                hw = float(t.get("width") or 0.0) / 2.0
                sx0, sx1 = min(a[0], b[0]) - hw, max(a[0], b[0]) + hw
                sy0, sy1 = min(a[1], b[1]) - hw, max(a[1], b[1]) + hw
                if sx0 <= x1 and sx1 >= x0 and sy0 <= y1 and sy1 >= y0:
                    hits.append({"ref": f.get("ref"), "net": t["net"],
                                 "layer": t.get("layer")})
                    break
        m["trace_under_crystal"] = {
            "value": float(len(hits)),
            "where": ", ".join("%s under %s" % (h["net"], h["ref"]) for h in hits[:5]),
            "method": "other-net tracks whose centreline (grown by half its width) "
                      "meets the crystal's courtyard box",
            "examples": hits[:5],
        }

    # ------------------------------------------------- courtyard collisions
    fps = []
    for f in board.footprints:
        bb = _bbox4(f.get("bbox"))
        if bb is not None:
            fps.append((f, bb))
    overlaps = []
    for i in range(len(fps)):
        for j in range(i + 1, len(fps)):
            a, abb = fps[i]
            b, bbb = fps[j]
            if (a.get("layer") or "") != (b.get("layer") or ""):
                continue
            ax0, ay0, ax1, ay1 = abb
            bx0, by0, bx1, by1 = bbb
            ox = min(ax1, bx1) - max(ax0, bx0)
            oy = min(ay1, by1) - max(ay0, by0)
            if ox > 1e-6 and oy > 1e-6:
                overlaps.append({"a": a.get("ref"), "b": b.get("ref"),
                                 "overlap_mm": [round(ox, 3), round(oy, 3)]})
    m["courtyard_overlaps"] = {
        "value": float(len(overlaps)),
        "where": "footprint bbox overlap on the same side, %d pairs" % len(overlaps),
        "method": "footprint bbox intersection (bbox, not courtyard layer: a part "
                  "whose courtyard layer was never drawn has none to read)",
        "supports": "undetermined",
        "why_that_cap": "a body-shaped courtyard (a D-sub shell, a switch with a "
                         "notch) is wider in nothing but its bbox corner, so an "
                         "bbox overlap is a candidate and never a verdict; this "
                         "adapter keeps the courtyard as a bbox only, so the "
                         "audit reports the pair and stops there",
        "examples": overlaps[:5],
    }

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

    return m

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


def _reference_plane(board):
    """Does a signal run over a place where its reference layer has no copper?

    For each pair of ADJACENT copper layers the copper on the other one is taken
    as the return path, and every track on the near layer is walked along its
    centreline. A **split crossing** is a run of uncovered samples with covered
    samples on BOTH sides: the track enters a hole in the return copper and
    leaves it again. A run that reaches one end of the track is counted
    separately as ``off_plane`` - the track runs off the plane's edge, which is a
    different and usually deliberate thing.

    Two passes, because neither alone is honest. A raster at
    ``_PLANE_STEP_MM`` decides which tracks are worth a close look (the exact
    even-odd ray cast costs O(vertices) per sample and 34 000 vertices per real
    board would cost a minute); every track it flags is then re-read at
    ``_FINE_MM`` by ``_Scan``, and only what the re-read agrees with is reported.
    The one thing the coarse pass can still do is MISS: a gap narrower than
    ``_PLANE_STEP_MM`` under a track that is otherwise covered reads as copper.
    """
    order = _copper_order(board)
    if not order:
        why = ("the copper layers cannot be ordered from the file (no stackup, and "
               "not a two-layer board), so which layer is the reference is unknown")
        return {"signal_crosses_plane_split": _na(why)}
    pours = {}
    for z in board.zones:
        if z.get("is_keepout"):
            continue
        for l in (z.get("layers") or []):
            for poly in (z.get("filled_polygons") or []):
                if len(poly) >= 3:
                    pours.setdefault(str(l), []).append(
                        [(float(x), float(y)) for x, y in poly])
    crossings, off_plane, unmeasured = [], [], []
    for idx, layer in enumerate(order):
        refs = [order[j] for j in (idx - 1, idx + 1) if 0 <= j < len(order)]
        if not refs:
            continue
        rings = []
        for r in refs:
            rings.extend(pours.get(r) or [])
        if not rings:
            unmeasured.append("no filled pour on %s" % "/".join(refs))
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
                   "reference": "/".join(refs),
                   "uncovered_mm": round(inside, 3),
                   "where": "%.3f, %.3f" % (seq[0][0], seq[0][1])}
            if inside > 0:
                crossings.append(row)
            if edge:
                off_plane.append(row)
    if not pours:
        return {"signal_crosses_plane_split": _na(
            "the board has no filled pour, so there is no reference plane to cross")}
    if not crossings and len(unmeasured) == len(order):
        return {"signal_crosses_plane_split": _na(
            "; ".join(unmeasured) or "no reference copper to test against")}
    widest = max(crossings, key=lambda c: c["uncovered_mm"]) if crossings else None
    counts = {}
    for c in crossings:
        key = "%s over %s" % (c["layer"], c["reference"])
        counts[key] = counts.get(key, 0) + 1
    out = {
        "value": bool(crossings),
        "where": ("%d track(s) run over a gap in the reference copper and come back "
                  "onto it%s"
                  % (len(crossings),
                     ("; the widest is %s on %s over %s, uncovered for %.2f mm"
                      % (widest["net"], widest["layer"], widest["reference"],
                         widest["uncovered_mm"])) if widest else "")),
        "method": "the copper on the adjacent layer is taken as the return path; a "
                  "raster at %.1f mm picks the tracks that are not covered end to "
                  "end, and each of those is re-read at %.2f mm by an exact even-odd "
                  "ray cast over every filled polygon of every pour, so a hole reads "
                  "as a hole and a boundary artefact does not read as one"
                  % (_PLANE_STEP_MM, _FINE_MM),
        "examples": sorted(crossings, key=lambda c: -c["uncovered_mm"])[:8],
        "counts": counts,
    }
    if off_plane:
        out["note"] = ("%d track(s) run over the edge of the reference copper (the "
                       "uncovered run reaches one end) - a different thing and not "
                       "counted as a split crossing" % len(off_plane))
    if unmeasured:
        out["note"] = (out.get("note", "") + "; " + "; ".join(unmeasured)).strip("; ")
    return {"signal_crosses_plane_split": out}

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
    """Map the measurements onto the fact names ``calc/rules.py`` grades."""
    facts = {}

    def take(key, fact, scale=1.0):
        row = m.get(key) or {}
        if "value" in row:
            facts[fact] = row["value"] * scale

    take("min_track_width", "min_trace_width_mm")
    take("min_plated_drill", "min_via_hole_mm")
    take("diffpair_length_skew_mm", "diffpair_length_skew_mm")
    take("trace_under_crystal", "trace_under_crystal")
    take("decoupling_max_distance_to_pin_mm", "decoupling_max_distance_to_pin_mm")
    take("layer_changes_without_return_via", "layer_changes_without_return_via")
    take("power_trace_width_mm", "power_trace_width_mm")
    row = m.get("signal_crosses_plane_split") or {}
    if isinstance(row.get("value"), bool):
        facts["signal_crosses_plane_split"] = row["value"]
    # A bool must stay a bool: `take` multiplies by 1.0 and would hand the
    # grader the float 1.0, which it rejects as "must be true/false".
    row = m.get("decoupling_via_in_path") or {}
    if isinstance(row.get("value"), bool):
        facts["decoupling_via_in_path"] = row["value"]
    return facts


# Facts whose measurement this module can make but whose POPULATION it cannot
# bound, so they are reported and not graded (see `grade_caps`).
CAP_FACTS = ("decoupling_max_distance_to_pin_mm", "decoupling_via_in_path",
             "layer_changes_without_return_via")


def failing_examples(m, key, limit):
    row = m.get(key) or {}
    return row.get("examples") or []
