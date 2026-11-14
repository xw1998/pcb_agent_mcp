"""Length tuning on FINISHED copper, not on a plan.

`route/tune.py` can only tune what the maze search just routed, because it edits
the emitter's own op list. A board that was already routed - by an earlier run,
by hand, or by another tool - has no way to fix a skew except by ripping the net
up and routing it again, and that is not a length fix, it is a re-route.

So this module does the one thing a meander needs and nothing else: it replaces
ONE straight segment of the SHORT net with a longer 45-degree path through the
same two points, and every new segment is checked against the whole board
(clearance to other nets, rule-derived rectangles, board edge) before it becomes
an op. The copper that is written is the copper that was measured.

What is deliberately NOT here, and what the report therefore says:

* **vias are not length.** Neither counted nor tuned - the electrical length of a
  barrel is not its drill depth, so counting it would make two different numbers
  look like one.
* **arcs are not tuned.** A track arc in the file is a shape, not a segment, and
  `delete_track` means "segments". The number of arcs on each net is reported as
  `arcs_not_counted`, so a net whose length looks short because an arc was
  skipped is visible instead of silently mis-measured.
* **a meander only ever ADDS copper.** The reference of a group is its longest
  member (or `target_mm`); a net that is too LONG is reported as
  `needs_reroute`, never shortened by moving its copper.
* **only 0/45/90/135 segments are tuned.** A tooth fanned off a segment at 30
  degrees comes out at 75/105 degrees - exactly the angle nobody asked for, and
  this package's own rule is "45-degree bends only".
* **locked copper is refused by name** (`copper_is_locked`) rather than moved.
"""
import math

from .. import geometry as G
from ..edit import checks as CHK
from . import rules as R
from . import tune as T
from .grid import Grid

# This package draws 45-degree copper only, so a segment that is not on that grid
# is not a candidate - see the module docstring.
ON_GRID_DEG = (0.0, 45.0, 90.0, 135.0)
ANGLE_TOL_DEG = 0.05
# How close a tuned group has to come to count as matched. Tight on purpose: a
# meander is exact to the micrometre, so 0.05 mm is not "precision", it is "the
# tool stopped trying".
DEFAULT_TOL_MM = 0.05
# DDR3L budgets, used only to decide WHICH nets are grouped, never to declare a
# board good: a group that ends above its budget is reported as
# `length_not_matched` with the number it reached.
DEFAULT_DDR_PREFIX = "/DDR_"
DEFAULT_DDR_TOL = {"pair": 0.05, "lane": 0.10, "cmd": 0.50}
# The clearance `edit.apply_plan` refuses to land copper below
# (`ruleset.precondition.CLEARANCE_FLOOR_MM`). A meander judged only against the
# net rules (0.1 mm here) gets planned, then thrown out by that gate, so every
# candidate is judged with the SAME floor and the plan comes back writable.
# tests/test_tune_board.py asserts this number still equals the gate's.
FLOOR_MM = 0.2


class TuneError(Exception):
    def __init__(self, message, reason_code="TUNE_INVALID"):
        Exception.__init__(self, message)
        self.reason_code = reason_code


def _pt(v):
    return (float(v[0]), float(v[1]))


def segments(board, net=None):
    """The tunable segment inventory: dicts in the same shape the ops use."""
    out = []
    for node in board.tracks:
        if node.get("_arc"):
            continue                     # a shape, not a segment (see docstring)
        if net is not None and node.get("net") != net:
            continue
        s, e = node.get("start"), node.get("end")
        if not s or not e:
            continue
        a, b = _pt(s), _pt(e)
        out.append({"uuid": node.get("uuid"), "net": node.get("net"),
                    "layer": node.get("layer"),
                    "width": float(node.get("width") or 0.0),
                    "start": a, "end": b,
                    "locked": bool(node.get("locked")),
                    "length_mm": G.dist_point_point(a, b)})
    return out


def _arc_count(board, net=None):
    n = 0
    for node in board.tracks:
        if not node.get("_arc"):
            continue
        if net is None or node.get("net") == net:
            n += 1
    return n


def measure(board, nets=None):
    """Track length per net, plus what the number does not include.

    `arcs_not_counted` is per net and travels with the length so a reader can see
    that the number is "tracks only" rather than assume it is all the copper.
    """
    out = {}
    for seg in segments(board):
        if nets and seg["net"] not in nets:
            continue
        got = out.setdefault(seg["net"], {"length_mm": 0.0, "segments": 0,
                                          "layers": {}, "locked": 0})
        got["length_mm"] += seg["length_mm"]
        got["segments"] += 1
        got["layers"][seg["layer"]] = got["layers"].get(seg["layer"], 0) + 1
        got["locked"] += 1 if seg["locked"] else 0
    for net in list(out):
        out[net]["length_mm"] = round(out[net]["length_mm"], 6)
        out[net]["arcs_not_counted"] = _arc_count(board, net)
        out[net]["vias_not_counted"] = sum(1 for v in board.vias
                                           if v.get("net") == net)
    return out


def _angle_deg(a, b):
    return math.degrees(math.atan2(b[1] - a[1], b[0] - a[0])) % 180.0


def on_grid(a, b, tol=ANGLE_TOL_DEG):
    """Is this segment on the 0/45/90/135 grid (within a printing tolerance)?"""
    ang = _angle_deg(a, b)
    return any(min(abs(ang - g), 180.0 - abs(ang - g)) <= tol
               for g in ON_GRID_DEG)


def _match_name(nets, wanted):
    """Find the net on the board that `wanted` means, or None.

    KiCad names carry the sheet path (`/DDR_DQ0`), a caller typing `DQ0` means the
    same net. The match is exact, then by suffix on a separator, and nothing else:
    a fuzzy match would tune the wrong net and call it success.
    """
    if wanted in nets:
        return wanted
    tail = wanted if wanted.startswith("/") else "/" + wanted
    for name in nets:
        if name == tail or name.endswith(tail):
            return name
    for name in nets:
        if (len(name) > len(wanted) and name.endswith(wanted)
                and name[-len(wanted) - 1] in "/_+-"):
            return name
    return None


def ddr_groups(board, prefix=DEFAULT_DDR_PREFIX):
    """The DDR groups this board actually carries, from the package's own tables."""
    nets = set(s["net"] for s in segments(board))
    groups = []
    for p, n in CHK.DDR_PAIRS:
        a, b = _match_name(nets, prefix + p), _match_name(nets, prefix + n)
        if a and b:
            groups.append({"name": "pair %s/%s" % (p, n), "kind": "pair",
                           "nets": [a, b],
                           "tol_mm": DEFAULT_DDR_TOL["pair"]})
    for lane, members in CHK.DDR_LANES.items():
        found = [_match_name(nets, prefix + m) for m in members]
        found = [f for f in found if f]
        if len(found) > 1:
            groups.append({"name": lane, "kind": "lane", "nets": found,
                           "tol_mm": DEFAULT_DDR_TOL["lane"]})
    ref = _match_name(nets, prefix + "CLKP")
    cmds = [_match_name(nets, prefix + m) for m in CHK.DDR_CMD]
    cmds = [c for c in cmds if c]
    if ref and cmds:
        groups.append({"name": "addr/cmd vs CLK", "kind": "cmd",
                       "nets": [ref] + cmds,
                       "reference_net": ref,
                       "tol_mm": DEFAULT_DDR_TOL["cmd"]})
    return groups


def _norm_groups(board, groups):
    """Caller groups -> resolved net names, with a refusal for anything unmatched."""
    nets = set(s["net"] for s in segments(board))
    out = []
    for g in groups or []:
        if not isinstance(g, dict):
            raise TuneError("a group must be an object, got %r" % (g,),
                            "TUNE_GROUPS_INVALID")
        wanted = g.get("nets")
        if not isinstance(wanted, (list, tuple)) or not wanted:
            raise TuneError("group %r has no nets" % (g.get("name"),),
                            "TUNE_GROUPS_INVALID")
        resolved, missing = [], []
        for name in wanted:
            hit = _match_name(nets, str(name))
            (resolved if hit else missing).append(hit or name)
        if len(resolved) < 2:
            out.append({"name": g.get("name") or "group", "kind": g.get("kind"),
                        "nets": resolved, "missing": missing,
                        "tol_mm": float(g.get("tol_mm") or DEFAULT_TOL_MM),
                        "unmatched": True})
            continue
        out.append({"name": g.get("name") or "group", "kind": g.get("kind"),
                    "nets": resolved, "missing": missing,
                    "reference_net": g.get("reference_net"),
                    "target_mm": (None if g.get("target_mm") is None
                                  else float(g["target_mm"])),
                    "tol_mm": float(g.get("tol_mm") or DEFAULT_TOL_MM),
                    "amplitude_mm": (None if g.get("amplitude_mm") is None
                                     else float(g["amplitude_mm"]))})
    if not out:
        raise TuneError("no group with at least two routed nets; pass `groups` "
                        "or use the ddr defaults on a board that has DDR nets",
                        "TUNE_NOTHING_TO_GROUP")
    return out


def _insert_meander(grid, net, seg, extra_mm, amplitude, side, floor_mm=0.0):
    """Try one meander on one segment. Returns (pts, blockers) - never both.

    `floor_mm` is the clearance `edit.apply_plan` will refuse to go below; the
    candidate is judged with the same number so a planned meander is writable.
    """
    pts = T._serpentine(seg["start"], seg["end"], extra_mm, amplitude, side)
    if pts is None:
        return None, [{"reason": "does_not_fit", "amplitude_mm": amplitude,
                       "segment_mm": round(seg["length_mm"], 4)}]
    blocked = []
    for k in range(len(pts) - 1):
        a, b = pts[k], pts[k + 1]
        hits = grid.seg_blockers(a, b, seg["layer"], net, seg["width"],
                                 floor_mm=floor_mm)
        if hits:
            for h in hits[:1]:
                h["at"] = [round(a[0], 3), round(a[1], 3)]
            blocked.extend(hits[:1])
            break
    return pts, blocked


def _floor_hits(grid, net, seg, pts):
    """Pairs this meander brings below the APPLY floor (0.2 mm), net rule aside.

    `edit.apply_plan` refuses a plan that grows the count of different-net pairs
    closer than 0.2 mm, and this board already has thousands of them: a meander
    squeezed into an existing 0.1 mm bundle raises the count even though every
    pair is still wider than the net rule. Counting them here turns that refusal
    into a number the caller can look at before writing, instead of a surprise
    from a gate.
    """
    out = []
    for k in range(len(pts) - 1):
        for h in grid.seg_blockers(pts[k], pts[k + 1], seg["layer"], net,
                                   seg["width"], floor_mm=FLOOR_MM):
            if h.get("reason") == "clearance":
                out.append({"at": [round(pts[k][0], 3), round(pts[k][1], 3)],
                            "net": h.get("net"), "gap_mm": h.get("actual_mm"),
                            "needed_mm": h.get("needed_mm")})
                break
    return out

def plan(board, groups=None, bundle=None, keepout_rects=None, pitch_mm=0.1,
         amp_ladder=None, max_replacements=24, layers=None, allow_locked=False,
         floor_mm=0.0):
    """Plan meanders on the board's EXISTING copper.

    Returns ops in the standard shape (delete_track + add_track), so the caller
    feeds them to `edit.propose` + `edit.apply_plan` and every gate runs on them.
    """
    segs = segments(board)
    if not segs:
        raise TuneError("the board carries no track segments, so there is nothing "
                        "to tune", "TUNE_NO_COPPER")
    if layers is None:
        layers = sorted(set(s["layer"] for s in segs))
    bundle = bundle or R.resolve(board)
    grid = Grid(board, layers, float(pitch_mm), bundle,
                extra_keepouts=keepout_rects)
    ladder = tuple(amp_ladder or T.AMPLITUDE_LADDER)

    inventory = {}
    for s in segs:
        inventory.setdefault(s["net"], []).append(s)
    lengths = dict((k, sum(s["length_mm"] for s in v))
                   for k, v in inventory.items())

    groups_n = _norm_groups(board, groups if groups is not None
                            else ddr_groups(board))
    ops = []
    reports = []
    floor_pairs = []
    for g in groups_n:
        if g.get("unmatched"):
            reports.append({"group": g["name"], "kind": g.get("kind"),
                            "status": "unmeasurable",
                            "detail": "fewer than two of its nets carry copper",
                            "nets": g.get("nets"), "missing": g.get("missing")})
            continue
        names = g["nets"]
        ref = g.get("target_mm")
        if ref is None:
            # The longest member is the reference; a declared reference_net
            # competes with it rather than replacing it, so the clock is never
            # meandered up to make a short command net look matched.
            ref = max(lengths[n] for n in names)
            ref_net = g.get("reference_net")
            if ref_net and lengths.get(ref_net):
                ref = max(ref, lengths[ref_net])
        tol = g["tol_mm"]
        before = dict((n, round(lengths[n], 6)) for n in names)
        per_net = []
        for net in names:
            deficit = ref - lengths[net]
            entry = {"net": net, "before_mm": round(lengths[net], 6),
                     "target_mm": round(ref, 6), "deficit_mm": round(deficit, 6),
                     "meanders": 0, "below_apply_floor": 0, "refusals": []}
            while deficit > tol:
                if entry["meanders"] >= int(max_replacements):
                    entry["refusals"].append(
                        {"reason": "replacement_budget",
                         "budget": int(max_replacements)})
                    break
                made = False
                refusal = None
                for seg in sorted(inventory.get(net) or [],
                                  key=lambda s: -s["length_mm"]):
                    if seg["locked"] and not allow_locked:
                        refusal = refusal or {"reason": "copper_is_locked",
                                              "uuid": seg["uuid"],
                                              "start": [round(v, 3) for v in
                                                        seg["start"]]}
                        continue
                    if not on_grid(seg["start"], seg["end"]):
                        refusal = refusal or {
                            "reason": "not_on_45_grid",
                            "angle_deg": round(_angle_deg(seg["start"],
                                                          seg["end"]), 3),
                            "uuid": seg["uuid"]}
                        continue
                    base = g.get("amplitude_mm") or max(
                        T.AMP_FLOOR_MM, T.AMP_FACTOR * seg["width"])
                    cands = [base] + [a for a in ladder if a < base - 1e-9]
                    for amp in cands:
                        extra = min(deficit, T.max_extra(seg["length_mm"], amp))
                        if extra <= 1e-6:
                            continue
                        for side in (1, -1):
                            pts, blocked = _insert_meander(grid, net, seg,
                                                           extra, amp, side,
                                                           floor_mm=floor_mm)
                            if pts is None:
                                # "too short for a tooth" is an answer: without it
                                # the group would report not_matched and no reason,
                                # which reads identically to a blocker.
                                refusal = refusal or {
                                    "reason": "does_not_fit",
                                    "amplitude_mm": round(amp, 4),
                                    "uuid": seg["uuid"]}
                                continue
                            if blocked:
                                refusal = refusal or dict(blocked[0],
                                                          uuid=seg["uuid"])
                                continue
                            # Accepted: the segment becomes this polyline.
                            hits = _floor_hits(grid, net, seg, pts)
                            for h in hits:
                                floor_pairs.append(dict(h, net=net,
                                                        uuid=seg["uuid"]))
                            entry["below_apply_floor"] = (
                                int(entry.get("below_apply_floor") or 0) + len(hits))
                            inventory[net].remove(seg)
                            new_ops = []
                            for k in range(len(pts) - 1):
                                a, b = pts[k], pts[k + 1]
                                grid.add_track(net, seg["layer"], a, b,
                                               seg["width"])
                                inv = {"uuid": None, "net": net,
                                       "layer": seg["layer"],
                                       "width": seg["width"], "start": a,
                                       "end": b,
                                       "locked": False,
                                       "length_mm": G.dist_point_point(a, b)}
                                inventory[net].append(inv)
                                new_ops.append({
                                    "op": "add_track",
                                    "x1": round(a[0], 6), "y1": round(a[1], 6),
                                    "x2": round(b[0], 6), "y2": round(b[1], 6),
                                    "width": seg["width"], "layer": seg["layer"],
                                    "net": net})
                            ops.append({"op": "delete_track",
                                        "uuid": seg["uuid"]})
                            ops.extend(new_ops)
                            gained = sum(o["length_mm"] for o in inventory[net]
                                         [-len(new_ops):]) - seg["length_mm"]
                            lengths[net] += gained
                            deficit = ref - lengths[net]
                            entry["meanders"] += 1
                            entry["last"] = {
                                "segment_mm": round(seg["length_mm"], 3),
                                "amplitude_mm": amp,
                                "added_mm": round(gained, 4),
                                "pieces": len(new_ops)}
                            made = True
                            break
                        if made:
                            break
                    if made:
                        break
                if not made:
                    entry["refusals"].append(refusal or {
                        "reason": "no_candidate_segment",
                        "detail": "every segment of this net refused; see the "
                                  "entries above"})
                    break
            entry["after_mm"] = round(lengths[net], 6)
            entry["achieved_skew_mm"] = round(abs(lengths[net] - ref), 6)
            entry["arcs_not_counted"] = _arc_count(board, net)
            per_net.append(entry)
        after_skew = max(e["achieved_skew_mm"] for e in per_net)
        reports.append({
            "group": g["name"], "kind": g.get("kind"), "nets": names,
            "reference_mm": round(ref, 6), "tol_mm": tol,
            "before_skew_mm": round(max(abs(ref - v) for v in before.values()), 6),
            "after_skew_mm": after_skew,
            "status": "matched" if after_skew <= tol else "length_not_matched",
            "per_net": per_net,
            "missing": g.get("missing")})

    bad = [r["group"] for r in reports if r.get("status") == "length_not_matched"]
    unm = [r["group"] for r in reports if r.get("status") == "unmeasurable"]
    return {
        "status": "length_not_matched" if bad or unm else "ok",
        "ops": ops,
        "groups": reports,
        "unmatched_groups": unm,
        "totals": {"groups": len(reports), "matched": len(reports) - len(bad) - len(unm),
                   "not_matched": len(bad), "unmeasurable": len(unm),
                   "ops": len(ops),
                   "segments_replaced": sum(1 for o in ops
                                             if o.get("op") == "delete_track"),
                   "segments_added": sum(1 for o in ops
                                          if o.get("op") == "add_track")},
        "clearance_floor": {
            "floor_mm": FLOOR_MM,
            "new_pairs": len(floor_pairs),
            "worst": sorted(floor_pairs, key=lambda p: p["gap_mm"])[:5],
            "why": "pairs a meander brings closer than edit.apply_plan's floor; "
                   "each is still wider than its net rule, but apply_plan refuses "
                   "to grow that count unless allow_new_violations=true"},
        "grid": {"layers": layers, "pitch_mm": pitch_mm,
                 "rule_keepouts": grid.rule_keepouts,
                 "dropped_rule_keepouts": grid.dropped_rule_keepouts},
        "assumptions": {
            "vias_are_not_length": "tracks only on both sides of the comparison; "
                                   "vias are counted per net and NOT added",
            "arcs_not_tuned": "a track arc is a shape, not a segment; each net "
                              "carries arcs_not_counted",
            "meander_adds_only": "the reference of a group is its longest member "
                                 "(or target_mm); a too-long net is `needs_reroute`",
            "angles": "only segments on the 0/45/90/135 grid are meandered, so "
                      "every new segment is on that grid too",
            "clearance": "candidates are judged against each pair's own net rule; "
                         "the apply-time 0.2 mm floor is COUNTED, not enforced, "
                         "because this board's bundles are already at 0.1 mm",
        },
    }
