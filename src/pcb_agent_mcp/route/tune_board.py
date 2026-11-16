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
* **arcs ARE length.** The length of a track arc is measured through its three
  points (`edit.ops.arc_length_mm`), not guessed from a chord polyline, and it is
  counted in every number this module reports (`arcs_counted` says how many).
  `style="arc"` writes arcs (a rounded meander - the user rejected 45-degree
  teeth on sight); `style="chamfer"` writes the 45-degree version.
* **a meander only ever ADDS copper.** The reference of a group is its longest
  member (or `target_mm`); a net that is too LONG is reported as
  `needs_reroute`, never shortened by moving its copper.
* **only 0/45/90/135 segments are tuned.** A tooth fanned off a segment at 30
  degrees comes out at 75/105 degrees - exactly the angle nobody asked for, and
  this package's own rule is "45-degree bends only". An arc meander is exempt by
  construction: every piece is either on the segment's own axis or a circular arc
  tangent to it, so no new angle is ever introduced.
* **locked copper is refused by name** (`copper_is_locked`) rather than moved.
"""
import math

from .. import geometry as G
from ..edit import checks as CHK
from ..edit import ops as OPS
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


def _node_arc_length(node):
    """Length of one arc node, through its three points when the file kept them.

    A chord polyline is the fallback (an older parse), and it UNDER-measures by
    the sagitta - which is why it is the fallback and not the rule.
    """
    s, m, e = node.get("start"), node.get("mid"), node.get("end")
    if s and m and e:
        return OPS.arc_length_mm(_pt(s), _pt(m), _pt(e))
    pts = node.get("_points") or []
    return sum(G.dist_point_point(pts[k], pts[k + 1])
               for k in range(len(pts) - 1))


def _arc_count(board, net=None):
    n = 0
    for node in board.tracks:
        if not node.get("_arc"):
            continue
        if net is None or node.get("net") == net:
            n += 1
    return n


def _net_lengths(board):
    """Copper length per net: straight segments AND arcs."""
    out = {}
    for seg in segments(board):
        out[seg["net"]] = out.get(seg["net"], 0.0) + seg["length_mm"]
    for node in board.tracks:
        if node.get("_arc"):
            net = node.get("net")
            out[net] = out.get(net, 0.0) + _node_arc_length(node)
    return out


def measure(board, nets=None):
    """Track length per net, plus what the number does not include.

    An arc's length is measured through its three points and IS added to
    `length_mm` - `arcs_counted` says how many arcs that number includes, so a
    reader can tell "tracks only" from "all the copper" by looking.
    """
    out = {}
    for seg in segments(board):
        if nets and seg["net"] not in nets:
            continue
        got = out.setdefault(seg["net"], {"length_mm": 0.0, "segments": 0,
                                          "layers": {}, "locked": 0, "arcs": 0})
        got["length_mm"] += seg["length_mm"]
        got["segments"] += 1
        got["layers"][seg["layer"]] = got["layers"].get(seg["layer"], 0) + 1
        got["locked"] += 1 if seg["locked"] else 0
    for node in board.tracks:
        if not node.get("_arc"):
            continue
        net = node.get("net")
        if nets and net not in nets:
            continue
        got = out.setdefault(net, {"length_mm": 0.0, "segments": 0,
                                   "layers": {}, "locked": 0, "arcs": 0})
        got["length_mm"] += _node_arc_length(node)
        got["arcs"] += 1
        got["layers"][node.get("layer")] = got["layers"].get(
            node.get("layer"), 0) + 1
        got["locked"] += 1 if node.get("locked") else 0
    for net in list(out):
        out[net]["length_mm"] = round(out[net]["length_mm"], 6)
        out[net]["arcs_counted"] = out[net].pop("arcs")
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
        # A group is a COMPARISON, so one net is normally not a group. It is,
        # though, when the caller states the length it wants (`target_mm`): that
        # is the well-posed question "stretch this one net to X", and
        # `headroom()` asks it once per net to find out how much copper each has
        # left. Without this the probe could not measure a single net at all.
        if len(resolved) < 2 and (not resolved or g.get("target_mm") is None):
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


def _first_other(refusal):
    """First refusal in a `too_shallow` chain that is NOT itself `too_shallow`.

    The arc branch records one refusal per segment, and the last thing it has to
    say is usually `too_shallow`. Each attempt used to wrap the previous value
    under `also_blocked`, so a segment tried at 7 length factors x 5 shifts x 2
    sides came back as a 70-level self-referential dict - unreadable, unbounded,
    and it grew with the ladder rather than with the copper. The evidence worth
    keeping is the first NON-shallow hit (a clearance blocker, a locked track),
    and there is at most one of those per segment.
    """
    seen = refusal
    for _ in range(8):
        if not isinstance(seen, dict):
            return None
        if seen.get("reason") != "too_shallow":
            return {k: v for k, v in seen.items() if k != "also_blocked"}
        seen = seen.get("also_blocked")
    return None


# ---------------------------------------------------------------- arc meander
# A 45-degree tooth is a chamfer: the user rejected it on sight ("应该是圆弧蛇形
# 线不是这样的三角线"). An arc meander is the same length added, drawn as a
# rounded square wave: straight along the axis, a 90-degree arc of radius r, a
# straight run of height h across, a 90-degree arc back, and so on. Every turn is
# tangent-continuous (no kinks), it stays on ONE side of the axis (a tight bundle
# still has room on the other side), and its length is closed-form:
#
#   added per tooth = h + r * (pi - 2)
#
# so the number the tool reports is the number the file carries - not a sampled
# approximation of it.
# The ladder runs from the widest radius that still fits down to the smallest
# one that keeps a bump visible: a 3 mm correction on a 10 mm run wants a
# large radius (few teeth), a 0.3 mm correction on a 1.3 mm run only fits a
# small one - and without the small ones the short segments would all answer
# "does_not_fit", which would be a limit of the ladder, not of the copper.
ARC_R_LADDER = (0.35, 0.30, 0.25, 0.20, 0.15, 0.12, 0.10, 0.08, 0.06)
ARC_H_MIN = 0.05
#: How tall the CROSS-BAR of a tooth has to be before it reads as a wave.
#:
#: `ARC_H_MIN` is a geometric floor (below it the piece is not distinguishable
#: from its neighbours by the blocker probe); this one is about what the copper
#: LOOKS like, and it was added after measuring a case that passed every other
#: test on this board: a 0.835 mm correction on an 8.6 mm run came out as six
#: teeth of `h = 0.070 mm`, `r = 0.06 mm` - a cross-bar narrower than the 0.1 mm
#: trace that carries it. Drawn at 900 px/mm that is a right-angle step with a
#: fuzz of 0.06 mm arcs, i.e. the shape the user rejected ("布线要 45 度你怎么又
#: 直角"), and it bought its 0.835 mm by shifting the whole 7 mm run 0.19 mm
#: sideways - which changes the intra-pair gap from 0.30 to 0.49 mm over that
#: stretch and pulls another net down to 0.1255 mm.
#:
#: So the climb is held to 1.5x the trace width this package routes DDR on
#: (0.1 mm). Below it the meander is REFUSED BY NAME (`too_shallow`, with the
#: height it would have had) rather than drawn: the length would be real, but the
#: answer to "this bundle has 0.19 mm of room" is "there is no meander here", not
#: a ripple that eats the clearance margin.
#: The same measuring pass found this family is not a one-off. On the board that
#: DID pass this package's own checks (`examples/rk3506_som/_rl1_s1d.kicad_pcb`,
#: commit 95c8f86) every equalising shape is of it: `/DDR_DQS0P` gets two 90-degree
#: detours of `r = 0.12` with a cross-bar of `h = 0.0759` mm, `/DDR_DQS1P` two more
#: of `r = 0.12` / `0.10` with `h = 0.0517` / `0.0655` mm - 0.5x to 0.76x the trace
#: width in every case. Each detour displaces the run 0.25-0.32 mm sideways
#: (DQS1's F.Cu run hops between x = 16.2345 and 16.5262 over a 1.6 mm stretch,
#: DQS1N sitting at x = 16.8), and those 0.19 / 0.18 mm per hop are what closed
#: the skew. So this floor does not only refuse the next attempt: it names the
#: shape the last accepted one was made of.
ARC_H_LEGIBLE_MM = 0.15
ARC_RUN_MIN = 0.10
# Where inside the segment's spare room the wave may sit, as a fraction of that
# room: centred first (0.0), then either end. A bundle rarely blocks symmetrically.
ARC_SHIFTS = (0.0, 0.5, -0.5, 0.25, -0.25)
# A rounded wave has a floor on how little it can add (a bump is never free), and
# its blocker set is not monotone in the added length: a 0.35 mm wave can be
# refused where a 0.30 mm one fits. So the deficit is tried at these fractions of
# the room available - a smaller wave is still progress, and the caller's loop
# asks for the rest afterwards instead of being told "cannot".
ARC_EXTRA_FACTORS = (1.0, 0.85, 0.7, 0.55, 0.4, 0.28, 0.18)
ARC_EXTRA_MIN_MM = 0.10

def arc_meander(seg, extra_mm, amp_max, side=1, r_ladder=None, shift=0.0,
                h_min=None):
    """Pieces of a ROUNDED meander that adds `extra_mm` to `seg`.

    Returns {"pieces": [("line", a, b) | ("arc", s, m, e)], ...} or None when no
    (r, teeth) choice both fits the segment and stays under `amp_max`, or
    {"pieces": None, "too_shallow": True, ...} when the only shapes that fit have
    a cross-bar below `h_min` (default `ARC_H_LEGIBLE_MM`) - a refusal with the
    height it reached, because "no room" and "no shape worth drawing" are two
    different answers and a caller that cannot tell them apart re-runs the tool
    with the same inputs. `side` is
    +1 / -1: the whole wave lives on that side of the axis, so the other side is
    untouched. `shift` in [-0.5, 0.5] slides the wave inside the room it has
    (0 = centred): a wave that is refused at one end of a bundle is often legal a
    little further along, and the two end points do not move either way.

    The shape is a rounded square wave. Per tooth: a straight run along the axis,
    a 90-degree arc of radius r, a straight climb h, a 90-degree arc back to the
    axis direction. Both arcs are tangent to what they join, so there is no kink
    anywhere, and every piece is a circle or a line - nothing here is an
    approximation of a curve. Length is closed form, per tooth:

        added = h + r * (pi - 2)

    which is exactly what `measure()` re-reads out of the written file.
    """
    L = seg["length_mm"]
    if extra_mm <= 1e-9 or L <= 0:
        return None
    ax, ay = seg["start"]
    bx, by = seg["end"]
    ux, uy = (bx - ax) / L, (by - ay) / L
    nx, ny = -uy * side, ux * side          # one normal, flipped by `side`

    need_h = ARC_H_LEGIBLE_MM if h_min is None else max(ARC_H_MIN, float(h_min))
    best = None
    shallow = None                      # tallest shape that was still too flat
    for r in (r_ladder or ARC_R_LADDER):
        for teeth in range(2, 65, 2):
            h = extra_mm / teeth - r * (math.pi - 2.0)
            if h < ARC_H_MIN:
                continue
            amp = h + 2.0 * r
            if amp > amp_max + 1e-9:
                continue
            run = L / teeth - 2.0 * r
            if run < ARC_RUN_MIN:
                continue
            if h < need_h - 1e-9:
                # More teeth make h SMALLER, so this radius can only get flatter;
                # keep the tallest refusal for the reason and stop scanning it.
                if shallow is None or h > shallow["h"]:
                    shallow = {"r": r, "teeth": teeth, "h": h, "amp": amp,
                               "run": run}
                break
            if best is None or teeth < best["teeth"]:
                best = {"r": r, "teeth": teeth, "h": h, "amp": amp, "run": run}
            break                           # fewest teeth for this radius wins
        if best is not None:
            break
    if best is None:
        if shallow is None:
            return None
        return {"pieces": None, "too_shallow": True, "h_mm": shallow["h"],
                "amp_mm": shallow["amp"], "teeth": shallow["teeth"],
                "r_mm": shallow["r"], "h_min_mm": need_h}

    r, teeth = best["r"], best["teeth"]
    h, amp, run = best["h"], best["amp"], best["run"]
    advance = teeth * (run + 2.0 * r)
    slack = L - advance
    if slack < -1e-9:
        return None
    t0 = slack / 2.0 + float(shift) * slack   # both end points keep their position;
    if t0 < -1e-9 or t0 + advance > L + 1e-9:  # the wave is the only new copper
        return None                            # inside the segment's span

    def W(t, y, from_amp=False):
        """Local (along-axis t, off-axis y) -> world, mirrored for a down tooth."""
        if from_amp:
            y = amp - y
        return (ax + ux * t + nx * y, ay + uy * t + ny * y)

    def near2(v):
        return (round(v[0], 6), round(v[1], 6))

    d45 = 0.7071067811865476
    pieces = []
    if t0 > 1e-6:
        # The wave does not have to begin at the segment's start, but the copper
        # does: without this lead-in the net would be open at that end.
        pieces.append(("line", (round(ax, 6), round(ay, 6)), near2(W(t0, 0.0))))
    t = t0
    for k in range(teeth):
        down = (k % 2 == 1)
        m = (lambda y: amp - y) if down else (lambda y: y)
        pieces.append(("line", near2(W(t, m(0.0))), near2(W(t + run, m(0.0)))))
        pieces.append(("arc", near2(W(t + run, m(0.0))),
                       near2(W(t + run + r * d45, m(r - r * d45))),
                       near2(W(t + run + r, m(r)))))
        pieces.append(("line", near2(W(t + run + r, m(r))),
                       near2(W(t + run + r, m(r + h)))))
        pieces.append(("arc", near2(W(t + run + r, m(r + h))),
                       near2(W(t + run + 2.0 * r - r * d45,
                              m(r + h + r * d45))),
                       near2(W(t + run + 2.0 * r, m(h + 2.0 * r)))))
        t += run + 2.0 * r
    # `teeth` is always even, so the wave closes back onto the axis exactly; the
    # last piece carries the path to the segment end - and when the wave already
    # ended there, it is dropped rather than written as a zero-length track (a
    # track between two identical points is not copper, it is a refusal).
    tail_a, tail_b = near2(W(t, 0.0)), (round(bx, 6), round(by, 6))
    if abs(tail_a[0] - tail_b[0]) > 1e-6 or abs(tail_a[1] - tail_b[1]) > 1e-6:
        pieces.append(("line", tail_a, tail_b))
    return {"pieces": pieces, "teeth": teeth, "r": r, "h": h, "amp": amp,
            "added_mm": round(extra_mm, 6), "advance_mm": round(advance, 4),
            "start_offset_mm": round(t0, 4), "shift": round(float(shift), 3)}

# A chord stands in for an arc during the blocking check, so the check has to be
# told how far the chord can be from the copper it replaces: that is the sagitta,
# and the probe is widened by exactly that much. 0.01 mm is an order of magnitude
# under the tightest net rule this package will tune (0.1 mm), and the arc's length
# is never taken from the chords - only its clearance is.
ARC_CHORD_SAG_MM = 0.01


def _arc_geom(s, m, e):
    """(centre, radius, start_deg, swept_deg) of the circle through three points."""
    ax, ay = s
    bx, by = m
    cx, cy = e
    d = 2.0 * (ax * (by - cy) + bx * (cy - ay) + cx * (ay - by))
    if abs(d) < 1e-12:
        return None
    ux = ((ax * ax + ay * ay) * (by - cy) + (bx * bx + by * by) * (cy - ay)
          + (cx * cx + cy * cy) * (ay - by)) / d
    uy = ((ax * ax + ay * ay) * (cx - bx) + (bx * bx + by * by) * (ax - cx)
          + (cx * cx + cy * cy) * (bx - ax)) / d
    r = math.hypot(ax - ux, ay - uy)
    a0 = math.degrees(math.atan2(ay - uy, ax - ux))
    am = math.degrees(math.atan2(by - uy, bx - ux))
    a1 = math.degrees(math.atan2(cy - uy, cx - ux))

    def _n(a):
        while a < 0:
            a += 360.0
        while a >= 360.0:
            a -= 360.0
        return a

    sweep = _n(a1 - a0)
    if not (_n(am - a0) <= sweep + 1e-6):
        sweep -= 360.0                 # the mid point decides which way round
    return (ux, uy), r, a0, sweep


def _arc_chords(s, m, e):
    """Chord polyline of a 3-point arc, and the sagitta those chords can hide.

    The end points are the caller's exact points (not re-derived), so a chord
    chain always joins the pieces it sits between.
    """
    got = _arc_geom(s, m, e)
    if got is None:
        return [(tuple(s), tuple(e))], 0.0
    (ux, uy), r, a0, sweep = got
    if r <= 1e-9 or abs(sweep) < 1e-9:
        return [(tuple(s), tuple(e))], 0.0
    if ARC_CHORD_SAG_MM >= r:
        step_max = 90.0
    else:
        step_max = math.degrees(2.0 * math.acos(1.0 - ARC_CHORD_SAG_MM / r))
    n = max(1, int(math.ceil(abs(sweep) / max(step_max, 1.0))))
    pts = []
    for k in range(n + 1):
        ang = math.radians(a0 + (sweep / n) * k)
        pts.append((round(ux + r * math.cos(ang), 6),
                    round(uy + r * math.sin(ang), 6)))
    pts[0] = (round(float(s[0]), 6), round(float(s[1]), 6))
    pts[-1] = (round(float(e[0]), 6), round(float(e[1]), 6))
    return [(pts[k], pts[k + 1]) for k in range(n)], ARC_CHORD_SAG_MM


def _pieces_probe(pieces, width):
    """(a, b, probe_width) for every stretch of copper the blockers must see."""
    for piece in pieces:
        if piece[0] == "line":
            yield piece[1], piece[2], width
        else:
            chords, sag = _arc_chords(piece[1], piece[2], piece[3])
            for a, b in chords:
                yield a, b, width + 2.0 * sag


def _pieces_blockers(grid, net, seg, pieces, floor_mm=0.0):
    """The first blocker of a whole path, or [] - arcs are checked chord by chord."""
    blocked = []
    for a, b, w in _pieces_probe(pieces, seg["width"]):
        hits = grid.seg_blockers(a, b, seg["layer"], net, w, floor_mm=floor_mm)
        if hits:
            for h in hits[:1]:
                h["at"] = [round(a[0], 3), round(a[1], 3)]
            blocked.extend(hits[:1])
            break
    return blocked


def _pieces_floor_hits(grid, net, seg, pieces):
    """Pairs a path brings below the APPLY floor - see `_floor_hits`."""
    out = []
    for a, b, w in _pieces_probe(pieces, seg["width"]):
        for h in grid.seg_blockers(a, b, seg["layer"], net, w,
                                   floor_mm=FLOOR_MM):
            if h.get("reason") == "clearance":
                out.append({"at": [round(a[0], 3), round(a[1], 3)],
                            "net": h.get("net"), "gap_mm": h.get("actual_mm"),
                            "needed_mm": h.get("needed_mm")})
                break
    return out


def _occupy_pieces(grid, net, seg, pieces):
    """Teach the raster the new copper, so the NEXT candidate sees it."""
    for a, b, w in _pieces_probe(pieces, seg["width"]):
        grid.add_track(net, seg["layer"], a, b, w)


def plan(board, groups=None, bundle=None, keepout_rects=None, pitch_mm=0.1,
         amp_ladder=None, max_replacements=24, layers=None, allow_locked=False,
         floor_mm=0.0, style="chamfer", with_headroom=False):
    """Plan meanders on the board's EXISTING copper.

    Returns ops in the standard shape (delete_track + add_track [+ add_track_arc]),
    so the caller feeds them to `edit.propose` + `edit.apply_plan` and every gate
    runs on them.

    `style` picks the shape: "chamfer" (the 45-degree tooth, default) or "arc"
    (a rounded meander - 90-degree arcs tangent to the axis, no kink anywhere).

    `with_headroom` (default OFF, because it is expensive) adds a `headroom` block
    to every group that comes back `length_not_matched`: each net measured ALONE
    on the same board, so the number says how much length that net's own copper
    could ever absorb. A group whose reference member has less room than the
    length it is missing can never be matched by tuning, and that is a layout
    answer, not a routing one - see `headroom()`. Cost is one whole `plan()`
    pass per net (about 10 s each on a DDR lane), which is why it is opt-in
    and not implied by a normal plan.
    """
    if style not in ("chamfer", "arc"):
        raise TuneError("style must be 'chamfer' or 'arc', got %r" % (style,),
                        "ARG_INVALID")
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
    # Ops are emitted per ORIGIN segment, once, at the END - not appended as each
    # tooth is decided. A piece this plan creates carries no uuid yet, so a second
    # tooth on copper that already got one could not be named by a later
    # delete_track; asking for more length than one tooth gives therefore crashed
    # at apply time ("delete_track needs a selector"). Each block remembers the
    # segment the FILE has (that uuid) and the atom list the plan is building for
    # it, and only the final list is written.
    blocks = {}
    # Lengths come from `_net_lengths`, not from the segment inventory: an arc
    # already in the file is real copper and has to be in the number this tool
    # compares, or every group would look short by however much arced.
    lengths = _net_lengths(board)

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
                    if seg.get("_arc"):
                        continue     # an arc is a shape; only straight copper is cut
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
                        # `T.max_extra` is the CHAMFER's capacity (a tooth spends
                        # 2 mm of run per mm of amplitude). An arc tooth spends
                        # `run + 2r` instead, so capping the arc branch with that
                        # formula would silently shrink every candidate on the
                        # short segments - `arc_meander` already refuses what does
                        # not fit, so the arc branch asks for the whole deficit.
                        extra = (deficit if style == "arc"
                                 else min(deficit,
                                          T.max_extra(seg["length_mm"], amp)))
                        if extra <= 1e-6:
                            refusal = refusal or {
                                "reason": "does_not_fit",
                                "detail": "segment_too_short_for_a_tooth",
                                "segment_mm": round(seg["length_mm"], 3),
                                "amplitude_mm": round(amp, 4),
                                "uuid": (seg.get("_block") or {}).get("uuid")
                                or seg["uuid"]}
                            continue
                        for side in (1, -1):
                            if style == "arc":
                                got = None
                                flat = None
                                for factor in ARC_EXTRA_FACTORS:
                                    step = extra * factor
                                    if step < ARC_EXTRA_MIN_MM:
                                        continue
                                    for shift in ARC_SHIFTS:
                                        cand = arc_meander(seg, step, amp, side,
                                                           shift=shift)
                                        if cand is None:
                                            continue
                                        if cand.get("pieces") is None:
                                            # Every shape that fits is too flat to
                                            # read as a wave; remember the tallest
                                            # and keep looking for a real one.
                                            if flat is None or (cand["h_mm"] >
                                                                flat["h_mm"]):
                                                flat = cand
                                            continue
                                        blocked = _pieces_blockers(
                                            grid, net, seg, cand["pieces"],
                                            floor_mm=floor_mm)
                                        if blocked:
                                            refusal = refusal or dict(
                                                blocked[0], uuid=seg["uuid"])
                                            continue
                                        got = cand
                                        break
                                    if got is not None:
                                        break
                                if got is None:
                                    if flat is not None:
                                        # `too_shallow` wins over the clearance hit
                                        # that usually comes with it: "a legible wave
                                        # exists geometrically but something is in
                                        # its way, and the only shapes that fit are
                                        # flat" is the actionable sentence, and the
                                        # clearance hit travels as evidence.
                                        earlier = _first_other(refusal)
                                        refusal = {
                                            "reason": "too_shallow",
                                            "style": "arc",
                                            "climb_mm": round(flat["h_mm"], 4),
                                            "min_climb_mm": flat["h_min_mm"],
                                            "would_amplitude_mm":
                                                round(flat["amp_mm"], 4),
                                            "teeth": flat["teeth"],
                                            "detail": "the only rounded waves that "
                                                      "fit here have a cross-bar "
                                                      "narrower than a line width and a "
                                                      "half; drawn, that is a "
                                                      "right-angle step, not a "
                                                      "meander",
                                            "uuid": seg["uuid"]}
                                        if earlier is not None:
                                            refusal["also_blocked"] = earlier
                                    else:
                                        refusal = refusal or {
                                            "reason": "does_not_fit",
                                            "style": "arc",
                                            "amplitude_mm": round(amp, 4),
                                            "uuid": seg["uuid"]}
                                    continue
                                pieces = got["pieces"]
                                hits = _pieces_floor_hits(grid, net, seg, pieces)
                            else:
                                pts, blocked = _insert_meander(
                                    grid, net, seg, extra, amp, side,
                                    floor_mm=floor_mm)
                                if pts is None:
                                    # "too short for a tooth" is an answer: without
                                    # it the group reports not_matched and no reason,
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
                                pieces = [("line", pts[k], pts[k + 1])
                                          for k in range(len(pts) - 1)]
                                hits = _floor_hits(grid, net, seg, pts)
                            # Accepted: the segment becomes this path. A line piece
                            # is a track, an arc piece is a copper arc; each carries
                            # the length it actually has, and both are measured the
                            # same way afterwards.
                            for h in hits:
                                floor_pairs.append(dict(
                                    h, net=net,
                                    uuid=(seg.get("_block") or {}).get("uuid")
                                    or seg["uuid"]))
                            entry["below_apply_floor"] = (
                                int(entry.get("below_apply_floor") or 0)
                                + len(hits))
                            inventory[net].remove(seg)
                            inv_added = []
                            made_atoms = []
                            for piece in pieces:
                                if piece[0] == "line":
                                    a, b = piece[1], piece[2]
                                    grid.add_track(net, seg["layer"], a, b,
                                                   seg["width"])
                                    length = G.dist_point_point(a, b)
                                    atom = ("line", tuple(a), tuple(b))
                                    inv = {"_arc": False}
                                else:
                                    a, m, b = piece[1], piece[2], piece[3]
                                    _occupy_pieces(grid, net, seg, [piece])
                                    length = OPS.arc_length_mm(a, m, b)
                                    atom = ("arc", tuple(a), tuple(m), tuple(b))
                                    inv = {"_arc": True}
                                made_atoms.append(atom)
                                inv.update({"uuid": None, "net": net,
                                            "layer": seg["layer"],
                                            "width": seg["width"],
                                            "start": piece[1], "end": piece[-1],
                                            "locked": False,
                                            "length_mm": length})
                                inventory[net].append(inv)
                                inv_added.append(inv)
                            blk = seg.get("_block")
                            if blk is None:
                                # First tooth on the file's own segment: the whole
                                # segment becomes this path, so its atom list is the
                                # new one outright.
                                blk = {"uuid": seg["uuid"], "net": net,
                                       "layer": seg["layer"],
                                       "width": seg["width"],
                                       "items": [{"atom": ("line",
                                                           tuple(seg["start"]),
                                                           tuple(seg["end"]),
                                                           ), "inv": None}]}
                                blocks.setdefault(net, []).append(blk)
                                at = 0
                            else:
                                at = int(seg["_atom"])
                            blk["items"][at:at + 1] = [{"atom": a_, "inv": i_}
                                                       for a_, i_ in
                                                       zip(made_atoms, inv_added)]
                            for idx, item in enumerate(blk["items"]):
                                if item["inv"] is not None:
                                    item["inv"]["_block"] = blk
                                    item["inv"]["_atom"] = idx
                            gained = (sum(i["length_mm"] for i in inv_added)
                                      - seg["length_mm"])
                            lengths[net] += gained
                            deficit = ref - lengths[net]
                            entry["meanders"] += 1
                            entry["last"] = {
                                "segment_mm": round(seg["length_mm"], 3),
                                "style": style,
                                "amplitude_mm": amp,
                                "added_mm": round(gained, 4),
                                "pieces": len(made_atoms),
                                "arcs": sum(1 for q in pieces if q[0] == "arc")}
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
            entry["arcs_counted"] = _arc_count(board, net)
            per_net.append(entry)
        after_skew = max(e["achieved_skew_mm"] for e in per_net)
        rep = {
            "group": g["name"], "kind": g.get("kind"), "nets": names,
            "reference_mm": round(ref, 6), "tol_mm": tol,
            "before_skew_mm": round(max(abs(ref - v) for v in before.values()), 6),
            "after_skew_mm": after_skew,
            "status": "matched" if after_skew <= tol else "length_not_matched",
            "per_net": per_net,
            "missing": g.get("missing")}
        if with_headroom and rep["status"] == "length_not_matched":
            # The per-net refusals above are the evidence of THIS pass, and in a
            # lane they are contaminated: whether a wave fits depends on whether
            # a neighbour already moved. So the question "can this group ever be
            # matched" is answered on a clean board, one net at a time, and
            # carried next to the numbers rather than implied by them.
            rep["headroom"] = headroom(
                board, [{"name": g["name"], "nets": names, "tol_mm": tol}],
                bundle=bundle, keepout_rects=keepout_rects, pitch_mm=pitch_mm,
                amp_ladder=amp_ladder, layers=layers, floor_mm=floor_mm,
                style=style)["groups"][0]
        reports.append(rep)

    # One delete per origin segment, then the whole final path: the file's uuid is
    # the only selector that exists before the write.
    for net in blocks:
        for blk in blocks[net]:
            ops.append({"op": "delete_track", "uuid": blk["uuid"]})
            for item in blk["items"]:
                atom = item["atom"]
                a = atom[1]
                b = atom[-1]
                common = {"x1": round(a[0], 6), "y1": round(a[1], 6),
                          "x2": round(b[0], 6), "y2": round(b[1], 6),
                          "width": blk["width"],
                          "layer": blk["layer"], "net": net}
                if atom[0] == "line":
                    ops.append(dict(common, op="add_track"))
                else:
                    ops.append(dict(common, op="add_track_arc",
                                    mx=round(atom[2][0], 6),
                                    my=round(atom[2][1], 6)))

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
            "arcs_are_length": "a track arc's length is measured through its three "
                               "points and IS in length_mm; arcs_counted says how "
                               "many arcs each number includes",
            "arc_clearance": "an arc is checked chord by chord with the probe "
                             "widened by the chord sagitta (%.2f mm), so a chord can "
                             "never read farther from a neighbour than the arc it "
                             "stands for" % ARC_CHORD_SAG_MM,
            "meander_adds_only": "the reference of a group is its longest member "
                                 "(or target_mm); a too-long net is `needs_reroute`",
            "angles": "only segments on the 0/45/90/135 grid are meandered, so "
                      "every new segment is on that grid too",
            "clearance": "candidates are judged against each pair's own net rule; "
                         "the apply-time 0.2 mm floor is COUNTED, not enforced, "
                         "because this board's bundles are already at 0.1 mm",
        },
    }

# How much MORE than the shortfall a headroom probe asks for. The probe only has
# to answer "can this net absorb what it is missing", so it aims just past the
# number and stops when the planner runs out of room; asking for a huge number
# would only make the search slower, never the answer different.
HEADROOM_EXTRA_MM = 0.2


def headroom(board, groups=None, bundle=None, keepout_rects=None, pitch_mm=0.1,
             amp_ladder=None, layers=None, floor_mm=FLOOR_MM, style="arc",
             max_replacements=32):
    """How much length each net's OWN copper could ever absorb, measured ALONE.

    This exists because a lane's per-net refusals are not evidence about the
    board: in a band nine nets share one corridor, so whether a wave fits
    depends on whether a neighbour has already moved. The question worth asking
    is therefore asked on a clean board, one net at a time - "give this net a
    target it cannot reach and see how much length the planner can still find" -
    and it is asked of the SAME planner, with the same rules, floors and shape,
    so the number cannot contradict `plan()`.

    Why not a closed-form geometric ceiling (amplitude x radius x tooth count)?
    Because that ceiling is easy to compute wrong and every way of getting it
    wrong is a FAKE `blocked_by_geometry` - a wrong answer, which this package
    does not produce. Measured on this board: the densest wave (teeth = t_max)
    has slack = 0, so it starts at the segment's own endpoint, and DQ3's endpoint
    sits 0.15 mm from `/DDR_DQS0N` - the ceiling said 0 while `plan()` really did
    close the net. Any ceiling has to descend through tooth counts to notice
    that, and once it does it is no longer closed-form, it is a search. So the
    search IS the answer.

    Direction of the reading, and it is one-way:

    * `verdict="blocked_by_geometry"` is FINAL. A net alone, with the whole
      board free and no neighbour to fight, still cannot take what it is
      missing. No amount of tuning, re-ordering or re-running fixes that; it is
      a layout answer (ball map, escape order, or corridor reserved for waves).
    * `verdict="tunable"` is NOT a promise. It says every member has room on its
      own; it does not say they all get it at once, because they share copper.

    Each net costs one whole `plan()` pass (~10 s on a DDR lane), so this is
    opt-in (`plan(..., with_headroom=True)`) and is not run unless asked.

    The floor it is measured at is the caller's (`plan()` forwards its own),
    and the reading is MONOTONE in it: a shortfall measured under a looser
    floor stays a shortfall under every stricter one, so a `blocked_by_geometry`
    never becomes wrong by measuring it with less safety margin - it only
    becomes rarer. That is why `plan(..., with_headroom=True)` on a plan judged
    by the net rules (floor 0) reports FEWER blocked nets than the same board
    at the 0.2 mm write gate, and both numbers are true statements about
    different floors.
    """
    segs = segments(board)
    if not segs:
        raise TuneError("the board carries no track segments, so there is no "
                        "copper whose room could be measured", "TUNE_NO_COPPER")
    if layers is None:
        layers = sorted(set(s["layer"] for s in segs))
    lengths = _net_lengths(board)
    groups_n = _norm_groups(board, groups if groups is not None
                            else ddr_groups(board))
    out = []
    for g in groups_n:
        if g.get("unmatched"):
            out.append({"group": g["name"], "kind": g.get("kind"),
                        "nets": g.get("nets"), "missing": g.get("missing"),
                        "reference_mm": None, "tol_mm": g["tol_mm"],
                        "verdict": "unmeasurable", "binding_net": None,
                        "shortfall_mm": None, "per_net": [],
                        "note": "fewer than two of its nets carry copper"})
            continue
        names = [n for n in g["nets"] if lengths.get(n)]
        ref = g.get("target_mm")
        if ref is None:
            ref = max(lengths[n] for n in names)
            # Same reference as `plan()`: the longest member, with a declared
            # reference_net competing rather than replacing it.
            ref_net = g.get("reference_net")
            if ref_net and lengths.get(ref_net):
                ref = max(ref, lengths[ref_net])
        tol = g["tol_mm"]
        per_net = []
        for net in names:
            before = lengths[net]
            need = ref - before
            entry = {"net": net, "before_mm": round(before, 6),
                     "reference_mm": round(ref, 6), "need_mm": round(need, 6)}
            if need <= tol:
                entry.update(status="already_within_tol", reachable_add_mm=0.0,
                             gap_mm=0.0, blocks=[],
                             reason="already within tol of the reference, so "
                                    "there is nothing for this net to absorb")
                per_net.append(entry)
                continue
            want = before + need + HEADROOM_EXTRA_MM
            sub = plan(board,
                       [{"name": net, "kind": g.get("kind"), "nets": [net],
                         "target_mm": want, "tol_mm": 0.01}],
                       bundle=bundle, keepout_rects=keepout_rects,
                       pitch_mm=pitch_mm, amp_ladder=amp_ladder,
                       max_replacements=max_replacements, layers=layers,
                       floor_mm=floor_mm, style=style, with_headroom=False)
            e = sub["groups"][0]["per_net"][0]
            reach = max(0.0, float(e["after_mm"]) - before)
            gap = need - reach
            blocks = []
            for r in e.get("refusals") or []:
                code = r.get("reason")
                if code and code not in blocks:
                    blocks.append(code)
            entry.update({
                "reachable_add_mm": round(reach, 6),
                "gap_mm": round(max(0.0, gap), 6),
                "status": "shortfall" if gap > tol else "reachable",
                "blocks": blocks,
                "meanders": e.get("meanders"),
                "reason": ("this net alone absorbs %+.4f mm of the %+.4f mm it "
                           "owes, so it is short by %.4f mm with the board to "
                           "itself" % (reach, need, max(0.0, gap)))
                          if gap > tol else
                          ("this net alone absorbs %+.4f mm and owes %+.4f mm, so "
                           "its own copper has room - whether it KEEPS that room "
                           "with its neighbours moved is not answered here"
                           % (reach, need))})
            per_net.append(entry)
        short = [e for e in per_net if e["status"] == "shortfall"]
        binding = max(short, key=lambda e: e["gap_mm"]) if short else None
        out.append({
            "group": g["name"], "kind": g.get("kind"), "nets": names,
            "reference_mm": round(ref, 6), "tol_mm": tol,
            "verdict": "blocked_by_geometry" if short else "tunable",
            "binding_net": binding["net"] if binding else None,
            "shortfall_mm": round(binding["gap_mm"], 6) if binding else 0.0,
            "per_net": per_net,
            "note": ("at least one member cannot take what it is missing even "
                     "with the whole board to itself: tuning cannot close this "
                     "group, the layout has to")
                    if short else
                    ("every member has room on its own; this says nothing about "
                     "whether they can all take it at the same time, because "
                     "they share the same corridor")})
    blocked = [x["group"] for x in out if x["verdict"] == "blocked_by_geometry"]
    return {
        "status": "blocked_by_geometry" if blocked else "tunable",
        "groups": out,
        "blocked": blocked,
        "floor_mm": floor_mm,
        "assumptions": {
            "alone_is_an_upper_bound": "each net is measured with every other "
                                       "net still in place but not competing for "
                                       "the same corridor, so the number is what "
                                       "this net could take ON ITS OWN - an upper "
                                       "bound on what it gets in a pass",
            "same_planner": "measured by `plan()` itself with the same rules, "
                            "floor, layers and style, so it cannot report a "
                            "ceiling the planner would not honour",
            "one_way": "blocked_by_geometry is final (a private corridor would "
                       "not help); tunable is not a promise",
            "floor_mm": "candidates are judged with the same apply-time floor "
                        "`plan()` uses (%.2f mm here)" % floor_mm,
            "monotone": "a shortfall under this floor is a shortfall under "
                        "every stricter floor too, so blocked_by_geometry "
                        "cannot be invalidated by asking for less safety "
                        "margin - only fewer nets qualify",
            "extra_mm": "the probe aims %.2f mm past the shortfall and stops "
                        "when the planner runs out of room" % HEADROOM_EXTRA_MM,
            "angles": "only 0/45/90/135 segments are meandered; arcs are exempt "
                      "by construction (tangent, no new angle)",
        },
    }
