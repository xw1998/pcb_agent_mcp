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

from .. import geometry as G
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

    return m


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
    return facts


def failing_examples(m, key, limit):
    row = m.get(key) or {}
    return row.get("examples") or []
