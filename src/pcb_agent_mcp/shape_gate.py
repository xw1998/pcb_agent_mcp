"""A board either passes the drawing rules or it does not, and the rules are ranked.

Why this module exists, in the reviewer's words: "the MCP does not constrain you
enough". That complaint is not about a missing measurement - `angle_audit`,
`net_geometry_report` and `house_style` already measure every number below. What
was missing is a place that turns the measurements into **one verdict with a
ranked list of what is blocking**, so a generator has something to be refused by
instead of a pile of numbers it can talk past.

The ranking is part of the answer, not decoration. When a board fails two rules
at once, the higher one is what to fix first, and a caller told "fail" without an
order has to re-derive the order itself (and will pick wrong - the cheapest rule
to satisfy is rarely the one that matters).

The order is by "how unrecoverable is this in copper":

1. `acute_corner` - an interior angle under 90 degrees is an acid trap and an
   etch defect. It cannot be tuned away later; the shape has to be redrawn.
2. `off_45_heading` - a segment that is neither H, V nor 45. Nothing in this
   house is drawn at 30 degrees, and one such segment is a straight violation
   rather than a style opinion.
3. `duplicate_copper` - two tracks with the same net, layer and both endpoints.
   Pure waste, and it silently doubles every length and detour number the other
   audits report (`/FSPI_CLK` on the SOM board carried exactly this pair).
4. `square_corner` - a 90 degree turn where 135 was available. Reported always,
   blocking only in `strict`. A pocket between pads can force a 90, so this one
   is a judgement and says so.
5. `detour_ratio` - routed length over the minimum spanning tree of the net's pad
   centres. Blocking only past `hard_ratio`, because the denominator is a LOWER
   bound: a multi-drop net or a net whose parts sit on opposite edges of the board
   legitimately scores high.

Every threshold here is a judgement, and each one is echoed back with the number
that used it so the verdict can be argued with. Nothing in this module writes,
routes or places; it reads a `canonical.Board` and returns a dict.

Nothing here is a route.
"""
import math

from . import oracle as _oracle
from .calc.result import ok, err

#: The rules, highest priority first. `blocking_when` is prose for the reader; the
#: code below is the same statement in branches. Kept as data so a caller can
#: print the ordering without running anything.
RULES = (
    {"priority": 1, "id": "acute_corner", "blocking": True,
     "rule": "no interior corner under 90 degrees"},
    {"priority": 2, "id": "off_45_heading", "blocking": True,
     "rule": "every segment heading is 0/45/90 degrees"},
    {"priority": 3, "id": "duplicate_copper", "blocking": True,
     "rule": "no two tracks share net, layer and both endpoints"},
    {"priority": 4, "id": "square_corner", "blocking": False,
     "rule": "a 90 degree turn where a 135 chamfer was available"},
    {"priority": 5, "id": "detour_ratio", "blocking": False,
     "rule": "routed length stays near the pad spanning tree"},
)

#: A net whose name looks like a rail may legitimately run a longer path than a
#: signal, because it also has to be wide and reach several pins. Judgement.
POWER_HINTS = ("3v3", "5v", "12v", "1v8", "1v35", "2v5", "0v9", "vcc", "vdd",
               "vbat", "vbus", "vref", "vcap", "vin", "pwr", "vsys", "vcore",
               "vddio", "avdd", "dvdd")

#: Above this ratio the detour stops being explainable by topology and becomes a
#: loop. Twice the spanning tree on a signal net is already twice the copper.
HARD_RATIO_SIGNAL = 2.5
HARD_RATIO_POWER = 3.2


def _is_powerish(name):
    low = (name or "").lower()
    return any(h in low for h in POWER_HINTS)


def _duplicate_copper(board, tol_mm=0.002):
    """Tracks that are the same piece of copper twice.

    Keyed on the unordered endpoint pair so the direction of the second copy does
    not matter. Arcs are keyed on chord plus net and layer, which is enough to
    catch a copy that was cloned rather than drawn.
    """
    seen = {}
    dups = []
    for t in board.tracks or []:
        net = t.get("net")
        lay = t.get("layer")
        st, en = t.get("start"), t.get("end")
        if net is None or st is None or en is None:
            continue
        a = (round(float(st[0]) / tol_mm), round(float(st[1]) / tol_mm))
        b = (round(float(en[0]) / tol_mm), round(float(en[1]) / tol_mm))
        key = (net, lay, min(a, b), max(a, b))
        if key in seen:
            dups.append({"net": net, "layer": lay,
                         "start": [round(float(st[0]), 3), round(float(st[1]), 3)],
                         "end": [round(float(en[0]), 3), round(float(en[1]), 3)],
                         "width_mm": round(float(t.get("width") or 0.0), 3)})
        else:
            seen[key] = t
    return dups


def gate(board, strict=False, angle_tol_deg=1.0,
         hard_ratio_signal=None, hard_ratio_power=None, top=20):
    """One verdict for "is this copper drawn the way the house draws it".

    `strict` promotes `square_corner` and every detour over the soft threshold to
    blocking. The default is deliberately not strict: a strict-by-default gate on
    a board with pad pockets would refuse everything and be turned off, and a gate
    that gets turned off constrains nobody.

    Returns the ranked rule table with each rule's measured value, the blocking
    subset, and a `next_fix` naming the single highest-priority item. It is not a
    DRC: clearance, holes, mask and connectivity are `board_drc`'s job, and a board
    that passes here can still be electrically broken.
    """
    ang = _oracle.angle_audit(board, layer=None, angle_tol_deg=angle_tol_deg,
                              top_other=int(top))
    if isinstance(ang, dict) and ang.get("status") != "ok":
        return ang
    av = (ang.get("value") if isinstance(ang, dict) else None) or {}

    acute_list, square_list = [], []
    other_pct_max = 0.0
    other_segs = []
    for lay, c in (av.get("corners") or {}).items():
        for row in (c.get("acute_list") or []):
            acute_list.append(dict(row, layer=lay))
        for row in (c.get("right_angle_list") or []):
            square_list.append(dict(row, layer=lay))
    for lay, st in (av.get("per_layer") or {}).items():
        other_pct_max = max(other_pct_max, float(st.get("other_pct") or 0.0))
        for row in (st.get("other_segments") or []):
            other_segs.append(dict(row, layer=lay))
    acute_total = sum(int(c.get("corner_acute") or 0)
                      for c in (av.get("corners") or {}).values())
    square_total = sum(int(c.get("corner_90") or 0)
                       for c in (av.get("corners") or {}).values())
    other_total = sum(int(s.get("other") or 0)
                      for s in (av.get("per_layer") or {}).values())
    arc_tracks = sum(int(s.get("arc_tracks") or 0)
                     for s in (av.get("per_layer") or {}).values())

    dups = _duplicate_copper(board)

    geo = _oracle.net_geometry_report(board, min_segments=1, top=0, detour_flag=1.8)
    gv = (geo.get("value") if isinstance(geo, dict) else None) or {}
    hard_s = float(HARD_RATIO_SIGNAL if hard_ratio_signal is None else hard_ratio_signal)
    hard_p = float(HARD_RATIO_POWER if hard_ratio_power is None else hard_ratio_power)
    soft = 1.8
    detour_soft, detour_hard = [], []
    for row in (gv.get("nets") or []):
        ratio = row.get("detour_ratio")
        if ratio is None:
            continue
        cap = hard_p if _is_powerish(row.get("net")) else hard_s
        item = {"net": row["net"], "detour_ratio": ratio,
                "length_mm": row.get("length_mm"),
                "pad_mst_mm": row.get("pad_mst_mm"),
                "segments": row.get("segments"), "hard_cap": cap}
        if ratio >= cap:
            detour_hard.append(item)
        elif ratio >= soft:
            detour_soft.append(item)
    detour_hard.sort(key=lambda r: -r["detour_ratio"])
    detour_soft.sort(key=lambda r: -r["detour_ratio"])

    rules = []
    def _rule(r, value, blocked, detail, items=None):
        rules.append({"priority": r["priority"], "id": r["id"],
                      "rule": r["rule"], "measured": value,
                      "blocking": bool(blocked and (r["blocking"] or strict)),
                      "detail": detail,
                      "items": (items or [])[:int(top)]})

    _rule(RULES[0], acute_total, acute_total > 0,
          "%d corner(s) with an interior angle under 90 deg - acid trap, redraw "
          "the shape; it does not tune away" % acute_total, acute_list)
    _rule(RULES[1], other_total, other_total > 0,
          "%d segment(s) are neither H, V nor 45 (worst layer %.2f%%)"
          % (other_total, other_pct_max), other_segs)
    _rule(RULES[2], len(dups), bool(dups),
          "%d duplicated track(s): same net, layer and both endpoints" % len(dups),
          dups)
    _rule(RULES[3], square_total, False,
          "%d square (90 deg) corner(s); a 135 chamfer may or may not have been "
          "available - blocking only with strict=true" % square_total, square_list)
    _rule(RULES[4], len(detour_hard), bool(detour_hard),
          "%d net(s) past the hard detour cap (%d more between 1.8 and the cap)"
          % (len(detour_hard), len(detour_soft)),
          [dict(r, severity="hard") for r in detour_hard]
          + [dict(r, severity="soft") for r in detour_soft])

    blocking = [r for r in rules if r["blocking"]]
    verdict = "shape_pass" if not blocking else "shape_fail"
    next_fix = None
    if blocking:
        top_rule = min(blocking, key=lambda r: r["priority"])
        next_fix = {"rule": top_rule["id"], "priority": top_rule["priority"],
                    "measured": top_rule["measured"],
                    "detail": top_rule["detail"]}

    return ok(
        value={
            "verdict": verdict,
            "strict": bool(strict),
            "blocking": [r["id"] for r in blocking],
            "arc_tracks": arc_tracks,
            "next_fix": next_fix,
            "rules": rules,
            "rule_order": [{"priority": r["priority"], "id": r["id"],
                            "blocking_by_default": r["blocking"],
                            "rule": r["rule"]} for r in RULES],
            "read_it_like_this": (
                "verdict is pass only when no rule that is blocking by default "
                "fires; strict=true also blocks square corners and every detour "
                "past 1.8. `rules` is already sorted by priority, so fix rules[0]"
                " first. This is NOT a DRC: clearance, holes, mask and "
                "connectivity are board_drc's job, and a board that passes here "
                "can still be electrically broken. Arcs are not a violation: "
                "they are counted in arc_tracks and excluded from the heading and "
                "corner rows, because a curve sampled into chords looks like 1165 "
                "segments at random angles"),
        },
        unit="count / ratio",
        model="angle_audit (segment headings, corner interior angles) + "
              "net_geometry_report (length over pad MST) + an endpoint duplicate "
              "scan, each folded into a ranked pass/fail",
        basis="the board's own track list; corners are seen only where two "
              "segments share an endpoint to 0.001 mm; the detour denominator is "
              "the pad-centre spanning tree, a lower bound",
        precision="duplicates are matched to %.2f mm on the endpoints; headings to "
                  "%.2f deg; the detour caps are judgements, echoed per net"
                  % (0.002, float(angle_tol_deg)),
        assumptions={"strict": bool(strict),
                     "angle_tol_deg": float(angle_tol_deg),
                     "hard_ratio_signal": hard_s,
                     "hard_ratio_power": hard_p,
                     "soft_ratio": soft})
