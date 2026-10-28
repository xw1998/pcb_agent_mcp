"""Does the measurement agree with the verdict?

The honest list says five oracle answers are *derived* - this server computed them,
so they carry `authoritative: false`. That is honest, but it leaves the real
question unanswered: **are they right?** For exactly one of the five there is a
vendor ruling to compare against - `board_clearance` versus KiCad's own
`clearance` DRC check. This module runs both and reports the overlap, in both
directions:

* **recall** - of the violations DRC saw, how many did our measurement also flag?
  A miss is the dangerous direction: our answer said "fine" about something the
  vendor says is not fine.
* **ours_only** - pairs we flagged that DRC did not. This is expected to be
  non-empty, because our geometry is deliberately conservative (a track is a
  stroked segment, arcs are polylines, corner radius is ignored). What would be a
  red flag is a large *systematic* gap, so the worst cases are listed with their
  gap so the difference can be read.

The other four derived answers have **no vendor counterpart at all**, and this
module says so per metric instead of inventing a comparison:

* `free_space` - KiCad has no "where can a part go" query.
* `pour_connectivity` - DRC does not test whether a pad is inside the fill.
* `cross_split` - reference-plane continuity is not a DRC rule.
* `loop_area` - emits no verdict anywhere in either EDA.

No report file is written next to the project: DRC output goes to a temp dir
inside `engine.drc`.
"""
import math
import os
import re

from .. import geometry as G
from ..adapters import open_board as _open_board
from .. import oracle as _oracle
from . import engine as _engine

# DRC's reported position for an item is a point that lies on (or inside) that
# item's copper: a pad centre, a track endpoint, a via centre. 0.1 mm is enough to
# absorb our own "arc -> polyline" and "round pad -> polygon" conversions without
# letting a match happen between two different objects.
DEFAULT_TOL_MM = 0.1
LIMIT_BAND = 0.002          # mm; see `at_the_limit` below

# Hard cap on how many candidate pairs are enumerated. A real 10-layer board at a
# loose threshold can produce six figures of pairs; the cap is reported when it is
# hit, so a truncated answer never looks like a complete one.
PAIR_CAP = 20000

NO_VENDOR_COUNTERPART = {
    "free_space": "KiCad has no 'where can a component go' query, so there is no "
                  "verdict to compare: this answer stays derived",
    "pour_connectivity": "DRC does not test whether a pad really sits inside a zone "
                         "fill (its `isolated_copper` is about unconnected copper, a "
                         "different question), so this answer stays derived",
    "cross_split": "reference-plane continuity is not a DRC rule in either EDA",
    "loop_area": "neither EDA emits a loop-area verdict",
}


def _point_on_item(p, item, tol):
    """Is the point `p` on (or within tol of) this item's copper?"""
    px, py = float(p[0]), float(p[1])
    if "poly" in item:
        # Inside counts: a DRC anchor is often the CENTRE of a pad or a point
        # inside a filled polygon, and `dist_point_polygon` measures the distance
        # to the OUTLINE, so a point deep inside a large polygon scores its half
        # width instead of 0. That silently failed every pad-inside-polygon match
        # on microwave (the anchor sat 0.306 mm "away" from the middle of a
        # 0.612 mm pad) and turned four real agreements into "missed violations".
        if G.point_in_polygon((px, py), item["poly"]):
            return True
        return G.dist_point_polygon((px, py), item["poly"]) <= tol
    a, b, width = item["seg"]
    return max(0.0, G.dist_point_segment((px, py), a, b) - width / 2.0) <= tol


def _drc_positions(violation):
    """DRC item positions, converted into the canoncial frame.

    kicad-cli reports positions in the file's own frame (Y grows DOWN, exactly the
    numbers in the `.kicad_pcb`), while the canonical model this module measures on
    negates y (`adapters/kicad_pcb.py`: "KiCad grows down, canonical grows up").
    Leaving them unconverted makes every comparison miss by the board's height - a
    silent mismatch that looks exactly like "our geometry found nothing".
    """
    out = []
    for it in violation.get("items") or []:
        pos = it.get("pos") or {}
        if pos.get("x") is None or pos.get("y") is None:
            continue
        out.append((float(pos["x"]), -float(pos["y"])))
    return out


def _drc_uuids(violation):
    """The uuids kicad-cli attaches to each item of a violation.

    This is the identity to match on: the report's `pos` is the item's ANCHOR
    (`kicad-cli pcb drc` on multichannel_mixer reports a track at its start point
    2.7 mm away from the track it collides with), so "does this position lie on
    our shape" silently fails on every long track. The uuid is the same string in
    the `.kicad_pcb` file, so it matches exactly.
    """
    out = []
    for it in violation.get("items") or []:
        u = it.get("uuid")
        if u:
            out.append(u)
    return out


# kicad-cli localises every string in its report, so a zone is recognised by the
# word its own item line uses in each language we have actually seen a report in.
# This matters only for CLASSIFYING a violation as "not modelled" - the geometry
# decision never reads these strings.
ZONE_WORDS = ("填充区", "zone", "flood", "pour")

def _involves_zone(violation):
    """Does either side of this violation name a filled zone?"""
    for it in violation.get("items") or []:
        d = str(it.get("description") or "").lower()
        if any(w in d for w in ZONE_WORDS):
            return True
    return False

# Both spellings KiCad projects use for the two halves of a differential pair:
# `/USB/D+` & `/USB/D-`, and `...CLK_P` & `...CLK_N`.
_POLARITY = (re.compile(r"[_\-.]?[PN]$"), re.compile(r"[+-]$"))

def _diff_pair_key(net):
    """The shared part of a net name, so the two halves collapse to one key."""
    if not net:
        return None
    for rx in _POLARITY:
        if rx.search(net):
            return rx.sub("", net)
    return None

def _is_diff_pair_pair(a, b):
    ka = _diff_pair_key(a)
    return bool(ka) and ka == _diff_pair_key(b)

def _class_map(board):
    """net name -> net class name, from the board's own rules."""
    out = {}
    for name, spec in ((board.rules or {}).get("net_classes") or {}).items():
        if not isinstance(spec, dict):
            continue
        for net in spec.get("nets") or []:
            out[net] = name
    return out


def _rule_clearance(board, override=None):
    """The clearance each pair has to meet, per net class.

    Returns `(global_mm, source, per_net)` where `per_net` maps a net name to the
    clearance of ITS class. One global number is what the measurement uses to
    collect candidates (the loosest of the lot, so nothing is missed), and the
    per-net map is what decides whether a candidate is really a violation.

    This matters: on a real 10-layer board the net classes range over 0.1 mm
    (Default) and several diff-pair classes, so a single threshold both invents
    violations and hides them.
    """
    per_net = {}
    classes = {}
    rules = board.rules or {}
    for name, spec in (rules.get("net_classes") or {}).items():
        if isinstance(spec, dict) and spec.get("clearance") is not None:
            classes[name] = float(spec["clearance"])
            for net in spec.get("nets") or []:
                per_net[net] = classes[name]
    # A net that no class lists explicitly belongs to the default class, so every
    # unlisted net still HAS a clearance. Leaving it out made the caller fall back to
    # the loosest class in the file (0.28 mm on pic_programmer instead of 0.25) and
    # report 19 violations on a board KiCad's own DRC passes.
    default_mm = classes.get("Default")
    if default_mm is None and classes:
        default_mm = min(classes.values())
    # `.kicad_pro`'s `design_settings.rules.min_clearance` is a GLOBAL FLOOR that KiCad
    # applies on top of the net classes: multichannel_mixer reports "board minimum
    # clearance 0.3 mm" while its Default class says 0.2, and the net classes are what
    # our rules dict carries. Using only the classes measured that board as clean when
    # DRC had 12 violations. So every requirement is raised to the floor.
    floor = (rules.get("clearance") or {}).get("min_clearance")
    floor = float(floor) if floor else None
    if floor is not None:
        classes = dict((k, max(v, floor)) for k, v in classes.items())
        per_net = dict((k, max(v, floor)) for k, v in per_net.items())
        if default_mm is not None:
            default_mm = max(default_mm, floor)
    if override is not None:
        return float(override), "caller", per_net, default_mm
    if classes:
        src = ("board rules: %d net class(es), clearance %s"
               % (len(classes), ", ".join("%s=%s" % (k, v)
                                          for k, v in sorted(classes.items())
                                          if v is not None)))
        if floor is not None:
            src += "; raised to the project-wide min_clearance floor %s" % floor
        return max(classes.values()), src, per_net, default_mm
    if (rules.get("clearance") or {}).get("min_clearance"):
        v = float(rules["clearance"]["min_clearance"])
        return v, "board rules: min_clearance", per_net, v
    return None, "no clearance rule in the board file", per_net, None


def clearance_vs_drc(path, threshold_mm=None, tol_mm=DEFAULT_TOL_MM, worst=20,
                     drc_timeout=900):
    """Our clearance measurement against KiCad's own clearance DRC check."""
    if not path or not os.path.isfile(path):
        return {"status": "error", "reason_code": "FILE_NOT_FOUND",
                "message": "file does not exist: %r" % path}
    cli = _engine.find_kicad_cli()
    if not cli:
        return {"status": "error", "reason_code": "KICAD_CLI_NOT_FOUND",
                "message": "kicad-cli was not found, so there is no verdict to "
                           "compare against (this cross-check refuses rather than "
                           "comparing the measurement to itself)"}
    try:
        board = _open_board(path)
    except Exception as exc:                       # noqa: BLE001
        return {"status": "error", "reason_code": "FORMAT_NOT_SUPPORTED",
                "message": "%s: %s" % (type(exc).__name__, exc)}
    threshold, threshold_source, per_net, default_mm = _rule_clearance(board,
                                                                        threshold_mm)

    # Collect candidates at the LOOSEST rule so nothing is missed, then decide each
    # pair against its own nets' clearance. Counting without that second step is
    # how a measurement turns into 146,339 "violations" on a real board.
    ours = _oracle.clearance(board, threshold_mm=threshold or 0.2,
                             worst=PAIR_CAP, with_items=True)
    measured = ours.get("value") or {}
    candidates = measured.get("worst") or []

    def required(pair):
        if threshold_mm is not None:
            return float(threshold_mm)
        # A local clearance on either item replaces the net class value (KiCad gives
        # the local override priority): cm5_minima's U702 footprint declares 0.125
        # while its class says 0.13, and the vendor accepts a 0.125 gap there.
        ia, ib = pair.get("_item_a") or {}, pair.get("_item_b") or {}
        local = [v for v in (ia.get("local_clearance"), ib.get("local_clearance"))
                 if v is not None]
        if local:
            return max(float(v) for v in local)
        a, b_ = per_net.get(pair.get("net_a")), per_net.get(pair.get("net_b"))
        known = [v for v in (a, b_) if v is not None]
        if known:
            return max(known)
        # Unlisted nets sit in the default class; `threshold` is the loosest class in
        # the file, which would invent violations on every other class's copper.
        return default_mm if default_mm is not None else (threshold or 0.2)

    # Two shapes whose gap lands within LIMIT_BAND of the required clearance cannot be
    # decided by our own arithmetic: our polygon distance and KiCad's shape distance
    # agree only to ~1e-3 mm, so a 0.2496 mm gap against a 0.25 mm rule is a tie, not a
    # violation. Those pairs are reported as `at_the_limit` and left to the vendor
    # engine, instead of being counted as violations we cannot back up.
    below = [p for p in candidates if p["gap_mm"] < required(p) - LIMIT_BAND]
    at_limit = [p for p in candidates
                if required(p) - LIMIT_BAND <= p["gap_mm"] < required(p)]
    # Two nets that differ only by their polarity marker (D+/D-, CLK_P/CLK_N) are ONE
    # differential pair, and the vendor measures the pair's ROUTING (tracks and vias)
    # against `diff_pair_gap` / `diff_pair_via_gap` instead of the class clearance - so
    # a 0.154 mm via-to-via gap inside a 100 ohm pair is clean to KiCad while a
    # class-clearance rule calls it a violation. On cm5_minima that is all of the
    # remaining extra pairs; mixing them into the count made the measurement look
    # thousands of times too nervous with no visible reason.
    #
    # PADS ARE NOT EXEMPT, and that boundary is measured, not guessed: on
    # royalblue54L_feather the vendor reports J3.A6 vs J3.A7 and U5.12 vs U5.13 -
    # pads of the very same USB pair, 0.15-0.2 mm apart against the 0.2032 mm USB_DIFF
    # class. A first version exempted every pair and silently dropped 4 of those 13
    # real violations. So the rule is: same diff pair AND not pad-vs-pad.
    def _dp(p):
        if p.get("kind_a") == "pad" and p.get("kind_b") == "pad":
            return False
        return _is_diff_pair_pair(p["net_a"], p["net_b"])

    dp_pairs = [p for p in below if _dp(p)]
    our_pairs = [p for p in below if not _dp(p)]
    our_total = len(our_pairs)
    dropped = len(candidates) - len(below)        # cleared by their own class rule

    drc = _engine.drc(path, severity="all", timeout=int(drc_timeout))
    if drc.get("status") != "ok":
        return {"status": "error", "reason_code": drc.get("reason_code")
                or "VERDICT_SOURCE_UNAVAILABLE",
                "message": "KiCad DRC did not return a verdict: %s"
                           % (drc.get("message") or drc.get("status")),
                "ours": {"violations": our_total, "threshold_mm": threshold}}

    clearance_checks = []
    short_checks = []
    for v in drc.get("violations") or []:
        t = (v.get("type") or "").lower()
        if "clearance" in t and "edge" not in t:
            clearance_checks.append(v)
        elif t == "shorting_items":
            # Two pieces of copper of DIFFERENT nets that touch or overlap. Our
            # measurement reports this as a pair with gap 0.0 and cannot tell
            # "touching" from "overlapping", so KiCad splitting the same physical
            # condition into `clearance (actual 0.0000)` and `shorting_items` is a
            # reporting difference, not a disagreement. Measured on microwave: it
            # has 2 clearance + 6 shorting_items among copper, and our eight 0.0 mm
            # pairs are exactly those eight. Counting only `clearance` reported
            # that board as "2 missed violations" when nothing was missed.
            short_checks.append(v)
    # A zone fill is not part of our item list at all (see `not_measured`), so a
    # violation whose other side is a filled zone cannot be reproduced here - not
    # because our geometry is wrong but because that copper was never measured.
    # Both remaining misses on the demo boards are exactly this (tiny_tapeout
    # J18.1 vs the GND pour, openair-max I2C3.6 vs the GND pour), so they are
    # separated out instead of being counted as "our answer was wrong".
    modelled_checks = [v for v in clearance_checks + short_checks
                       if not _involves_zone(v)]
    zone_checks = [v for v in clearance_checks + short_checks
                   if _involves_zone(v)]
    verdict_checks = modelled_checks
    all_types = sorted({(v.get("type") or "?") for v in drc.get("violations") or []})
    other_checks = sorted(set(all_types) - {"clearance", "shorting_items"})

    # `at_limit` pairs are matched against DRC too: when the vendor flags a pair that
    # sits inside our undecidable band, the vendor has decided it and we say so instead
    # of reporting a miss we did not make.
    matched, drc_only, ours_used, limit_used = [], [], set(), set()
    for v in verdict_checks:
        poss = _drc_positions(v)
        uuids = _drc_uuids(v)
        hit = None
        for idx, pair in enumerate(our_pairs + at_limit):
            ia, ib = pair.get("_item_a"), pair.get("_item_b")
            if ia is None or ib is None:
                continue
            if len(uuids) == 2:
                # Both sides name the same two items: that is a match whatever the
                # gap arithmetic says. When one of our items has no uuid (an older
                # parse, or an item kind we do not tag yet) fall through to the
                # position test rather than declaring a miss.
                got = {ia.get("uuid"), ib.get("uuid")}
                if None not in got:
                    if got != set(uuids):
                        continue
                    hit = (idx, pair)
                    break
            if len(poss) >= 2:
                ok_ab = (_point_on_item(poss[0], ia, tol_mm)
                         and _point_on_item(poss[1], ib, tol_mm))
                ok_ba = (_point_on_item(poss[0], ib, tol_mm)
                         and _point_on_item(poss[1], ia, tol_mm))
                if not (ok_ab or ok_ba):
                    continue
            elif len(poss) == 1:
                # DRC gave us one position only: require it to sit between the two
                # shapes we flagged, i.e. on one of them.
                if not (_point_on_item(poss[0], ia, tol_mm)
                        or _point_on_item(poss[0], ib, tol_mm)):
                    continue
            else:
                continue
            hit = (idx, pair)
            break
        if hit is None:
            drc_only.append({"type": v.get("type"), "description": v.get("description"),
                             "pos": [list(p) for p in poss] or None,
                             "note": "KiCad flagged this and our measurement did not "
                                     "- our answer was wrong in the dangerous "
                                     "direction"})
        else:
            decided_by = "our measurement" if hit[0] < len(our_pairs) else "the limit band"
            if hit[0] < len(our_pairs):
                ours_used.add(hit[0])
            else:
                limit_used.add(hit[0] - len(our_pairs))
            matched.append({"drc": v.get("description"),
                            "decided_by": decided_by,
                            "ours": {"a": hit[1]["item_a"], "b": hit[1]["item_b"],
                                     "gap_mm": hit[1]["gap_mm"]}})

    drc_only_not_modelled = [
        {"type": v.get("type"), "description": v.get("description"),
         "pos": [list(p) for p in _drc_positions(v)] or None,
         "note": "one side is a filled ZONE, which this measurement does not model "
                 "- reported, not counted as a miss"}
        for v in zone_checks]

    def _pair_row(p):
        return {"a": p["item_a"], "b": p["item_b"], "net_a": p["net_a"],
                "net_b": p["net_b"], "gap_mm": p["gap_mm"]}

    ours_only = [_pair_row(p) for i, p in enumerate(our_pairs)
                 if i not in ours_used]
    ours_only.sort(key=lambda p: p["gap_mm"])
    dp_rows = sorted((_pair_row(p) for p in dp_pairs), key=lambda p: p["gap_mm"])

    if verdict_checks:
        recall = round(len(matched) / float(len(verdict_checks)), 4)
    else:
        recall = None

    if recall is None and not our_total:
        verdict = "no_signal"
        verdict_text = ("neither side found a clearance violation, so this run proves "
                        "nothing about agreement - it is only meaningful on a board "
                        "that has something to find")
    elif recall is None:
        # We flagged pairs but the vendor engine flagged none of this kind: there is
        # no verdict to agree with, so this is NOT "covered" and not a miss either.
        # Saying "missed_violations" here (0 violations missed) read as if something
        # had gone wrong; the honest state is "our extra pairs are untested".
        verdict = "ours_only_untested"
        verdict_text = ("KiCad reported no clearance violation, while our measurement "
                        "flags %d pair(s) below the rule: nothing to agree with on "
                        "the vendor side, so this run cannot confirm or refute them "
                        % our_total)
    elif recall == 1.0:
        verdict = "drc_covered"
        verdict_text = ("every clearance violation DRC reported is also in our list; "
                        "our extra pairs are the conservative side of the model")
    else:
        verdict = "missed_violations"
        verdict_text = ("%d clearance violation(s) DRC reported are NOT in our list: "
                        "our geometry missed them, and a model that says 'fine' where "
                        "the vendor says 'not fine' must not be trusted on this board"
                        % len(drc_only))

    if zone_checks:
        verdict_text += (" %d of the vendor's violation(s) involve a filled ZONE, "
                         "which this measurement does not model at all; they are "
                         "listed in agreement.drc_only_not_modelled and are NOT "
                         "part of the recall figure." % len(zone_checks))

    return {
        "status": "ok",
        "path": os.path.abspath(path),
        "authoritative": False,
        "verdict_source": "KiCad DRC engine (kicad-cli %s)"
                          % ((drc.get("engine") or {}).get("version") or "?"),
        "what_this_is": "a cross-check: our derived clearance measurement against the "
                        "vendor's own clearance verdict, on the same file, in both "
                        "directions",
        "frame": "canonical board frame (millimetres, Y UP) - kicad-cli's reported "
                 "positions are negated in y to get here, exactly as the adapter does",
        "thresholds": {
            "ours_mm": threshold,
            "source": threshold_source,
            "note": "our measurement flags every pair below this gap; DRC uses each "
                    "net class's own rule plus its (unmodelled here) board-edge and "
                    "hole rules",
            "match_tolerance_mm": tol_mm,
        },
        "drc": {
            "clearance_checks": len(clearance_checks),
            "shorting_checks": len(short_checks),
            "comparable_checks": len(verdict_checks),
            "zone_involved_checks": len(zone_checks),
            "check_types_in_report": all_types,
            "other_check_types_ignored": other_checks,
            "note": "comparable: clearance* (not copper_edge_clearance, which is a "
                    "board-edge rule our geometry does not model at all) plus "
                    "shorting_items, which is the same physical condition as our "
                    "gap==0 pairs - KiCad just separates 'just touching' (clearance "
                    "0.0000) from 'overlapping'",
        },
        "ours": {
            "pairs_below_threshold": our_total,
            "candidates_before_per_class_filter": len(candidates),
            "dropped_by_their_own_class_rule": dropped,
            "candidates_truncated": len(candidates) >= PAIR_CAP,
            "items_checked": measured.get("items_checked"),
            # Say what the measurement does NOT look at, rather than letting a small
            # `items_checked` imply the board is nearly empty: pour copper is not in
            # our item list, so a board whose routing is done entirely in zones
            # (microwave: 8 items) is measured on almost nothing.
            "not_measured": ["zone / pour copper (a clearance between a track and a "
                             "filled zone is not compared)",
                             "board edge and hole rules",
                             "solder mask and paste layers"],
            "min_gap_mm": measured.get("min_gap_mm"),
            "at_the_limit": len(at_limit),
            "at_the_limit_sample": [
                {"a": p["item_a"], "b": p["item_b"], "gap_mm": p["gap_mm"],
                 "required_mm": required(p), "vendor_flagged": i in limit_used}
                for i, p in enumerate(at_limit[:worst])],
            "at_the_limit_note": "gap and rule differ by less than %.4f mm: our "
                                 "measurement cannot decide this pair, the vendor DRC "
                                 "has the verdict" % LIMIT_BAND,
        },
        "agreement": {
            "matched": len(matched),
            "recall": recall,
            "drc_only": drc_only[:worst],
            "drc_only_total": len(drc_only),
            "ours_only": ours_only[:worst],
            "ours_only_total": len(ours_only),
            # Inside one differential pair the vendor applies `diff_pair_gap`, not
            # the net class clearance, so these pairs are expected and listed apart.
            "ours_only_diff_pair_total": len(dp_rows),
            "ours_only_diff_pair_sample": dp_rows[:5],
        "drc_only_not_modelled": drc_only_not_modelled,
        "drc_only_not_modelled_total": len(drc_only_not_modelled),
        },
        "verdict": verdict,
        "verdict_explained": verdict_text,
        "no_vendor_counterpart": NO_VENDOR_COUNTERPART,
        "not_written": "the DRC report is written to a temp directory and removed; "
                       "nothing is placed beside the project",
    }


def derive_all(path, metrics=None, **kwargs):
    """One call that runs each derived answer and says how it can be checked."""
    metrics = metrics or ["clearance"]
    out = {"status": "ok", "path": os.path.abspath(path), "metrics": {}}
    if "clearance" in metrics:
        res = clearance_vs_drc(path, **kwargs)
        out["metrics"]["clearance"] = res
        if res.get("status") != "ok":
            out["status"] = "partial"
    for name in metrics:
        if name in NO_VENDOR_COUNTERPART:
            out["metrics"].setdefault(name, {"status": "not_comparable",
                                             "reason": NO_VENDOR_COUNTERPART[name]})
    out["summary"] = {
        "comparable_metrics": [k for k, v in out["metrics"].items()
                               if v.get("status") not in ("not_comparable",)],
        "not_comparable_metrics": sorted(k for k, v in out["metrics"].items()
                                         if v.get("status") == "not_comparable"),
        "note": "a derived answer with no vendor counterpart is not 'wrong' - it is "
                "unverified, and it keeps saying so",
    }
    return out
