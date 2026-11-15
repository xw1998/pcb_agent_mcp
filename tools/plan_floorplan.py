#!/usr/bin/env python3
"""Nothing here moves a part and nothing here is a route.

The missing layer between `layout_quality.topology()` and a coordinate.  That
module answers "which parts do the same job and which groups talk to each
other"; `place_score.py` prices one arrangement after the fact.  Neither of them
answers the question a person actually works in:

    this group of parts has a job, the nets it must reach leave the board on
    four different sides, so WHICH SIDE does this group belong on, and how much
    airwire does the current answer waste?

That question has an answer with a denominator.  Treat every functional group
as one rigid body (its parts keep their relative offsets - this is a floorplan
move, not a re-placement), give each body four candidate sides, and price every
candidate by the total half-perimeter airwire of the nets it participates in.
With N groups the space is 4**N, which for this board is 4**7 = 16384 - small
enough to enumerate exactly instead of heuristically, so the number reported is
the best assignment in that space, not "an improvement".

What is deliberately not done:

  * no part is moved and no file is written - `--apply` does not exist;
  * a group is only translated as a whole; nothing is re-placed inside it;
  * a net that touches no board-edge terminal is priced once at the measured
    placement and cancels out of the comparison, so it cannot fake a difference;
  * the four candidate positions keep the group's distance from the edge it
    faces, and clamp inside the board, so a "better" side never means "off the
    board".

Readings are in canonical millimetres (Y up), same as the rest of the tools.

    python tools/plan_floorplan.py --board examples/rk3506_som/rk3506_som.kicad_pcb
    python tools/plan_floorplan.py --board B --json --top 20
"""
import argparse
import itertools
import json
import math
import os
import sys

HERE = os.path.dirname(os.path.abspath(__file__))
REPO = os.path.dirname(HERE)
sys.path.insert(0, os.path.join(REPO, "src"))

#: A pad closer than this to a board edge is a board-edge terminal (a castellation
#: half-hole on a stamped module).  Only terminals tell us where a net must leave
#: the board; interior pads say nothing about sides.
EDGE_MM = 1.00

#: A group with fewer pads than this is priced but not moved: a lone decoupling
#: cap's side is decided by its IC, not by enumeration.
MIN_PADS_TO_MOVE = 2

#: How far a moved group's body is proposed to sit from the edge it takes.  This
#: is an assumption of a judgement (the same 0.50 mm the placement gate uses for
#: the board edge), written into the answer with the number that used it.  Using
#: the group's OWN current inset instead makes every translation zero, which is
#: the bug this constant exists to prevent.
TARGET_INSET_MM = 0.50

SIDES = ("W", "E", "S", "N")

#: How many side assignments the exact search may look at before it gives up on
#: being exact.  4**8 = 65536 already takes a second on a 400-net board; a board
#: with 12 movable groups would be 16.7 million and look like a hang.  Past this
#: budget the search degrades to coordinate descent and the answer SAYS so in
#: `method` - a number from a heuristic must never be printed as "exact".
ENUM_BUDGET = 30000000


def load(path):
    from pcb_agent_mcp.adapters import open_board
    return open_board(path)


def outline_box(board):
    xs, ys = [], []
    for ring in (getattr(board, "outline", None) or []):
        for pt in ring:
            xs.append(float(pt[0]))
            ys.append(float(pt[1]))
    if not xs:
        raise ValueError("board has no outline - cannot measure sides")
    return min(xs), min(ys), max(xs), max(ys)


def side_of_edge(x, y, box, limit=EDGE_MM):
    """Which board edge a point sits on, or None for the interior."""
    x0, y0, x1, y1 = box
    if y >= y1 - limit:
        return "N"
    if y <= y0 + limit:
        return "S"
    if x >= x1 - limit:
        return "E"
    if x <= x0 + limit:
        return "W"
    return None


def side_of_position(x, y, box):
    """Which half of the board a point sits in, by the nearer edge."""
    x0, y0, x1, y1 = box
    cx, cy = (x0 + x1) / 2.0, (y0 + y1) / 2.0
    d = {"W": x - x0, "E": x1 - x, "S": y - y0, "N": y1 - y}
    return min(d, key=lambda k: d[k])


def hpwl(points):
    xs = [p[0] for p in points]
    ys = [p[1] for p in points]
    return (max(xs) - min(xs)) + (max(ys) - min(ys))


def netballs(board):
    """Every net's pads as (ref, x, y), and the board-edge terminals per net."""
    pads = {}
    terminals = {}
    refs = set()
    for fp in (getattr(board, "footprints", None) or []):
        ref = str(fp.get("ref") or "")
        if not ref:
            continue
        refs.add(ref)
        for p in (fp.get("pads") or []):
            net = p.get("net")
            if not net:
                continue
            x, y = float(p["x"]), float(p["y"])
            pads.setdefault(net, []).append({"ref": ref, "x": x, "y": y,
                                             "number": str(p.get("number") or "")})
    return pads


def group_membership(board):
    from pcb_agent_mcp.layout_quality import groups
    got = groups(board)
    member = {}
    for entry in got["groups"]:
        for ref in entry["refs"]:
            member[ref] = entry["name"]
    return got, member


def shift_target(group_pads, side, box, inset=TARGET_INSET_MM):
    """Where the group lands if it takes `side`: hug that edge at `inset`, keep
    the tangential coordinate, clamp the bbox inside the board."""
    x0, y0, x1, y1 = box
    xs = [p["x"] for p in group_pads]
    ys = [p["y"] for p in group_pads]
    gx0, gy0, gx1, gy1 = min(xs), min(ys), max(xs), max(ys)
    w, h = gx1 - gx0, gy1 - gy0

    if side == "W":
        dx = (x0 + inset) - gx0
        dy = 0.0
    elif side == "E":
        dx = (x1 - inset) - gx1
        dy = 0.0
    elif side == "S":
        dx = 0.0
        dy = (y0 + inset) - gy0
    else:
        dx = 0.0
        dy = (y1 - inset) - gy1

    # never push the body off the board, and never through the far edge
    if w <= (x1 - x0):
        if gx0 + dx < x0:
            dx = x0 - gx0
        if gx1 + dx > x1:
            dx = x1 - gx1
    if h <= (y1 - y0):
        if gy0 + dy < y0:
            dy = y0 - gy0
        if gy1 + dy > y1:
            dy = y1 - gy1
    return dx, dy


def analyse(path, top=12):
    board = load(path)
    box = outline_box(board)
    got, member = group_membership(board)
    pads = netballs(board)

    movable = []
    for entry in got["groups"]:
        name = entry["name"]
        gpads = []
        for net, ps in pads.items():
            gpads.extend([p for p in ps if member.get(p["ref"]) == name])
        if len(gpads) >= MIN_PADS_TO_MOVE:
            movable.append({"name": name, "pads": gpads,
                            "refs": entry["refs"], "count": entry["count"]})

    # terminals: pads on a board edge, grouped by the side they sit on
    term = {}
    for net, ps in pads.items():
        for p in ps:
            s = side_of_edge(p["x"], p["y"], box)
            if s:
                term.setdefault(net, []).append({"side": s, "x": p["x"], "y": p["y"],
                                                 "ref": p["ref"]})

    # --- which side each group's netlist asks for, by terminal votes
    for g in movable:
        votes = {s: 0 for s in SIDES}
        nets = set(p["ref"] for p in [])  # placeholder, filled below
        mine = set()
        for net, ps in pads.items():
            if any(member.get(p["ref"]) == g["name"] for p in ps):
                mine.add(net)
        for net in mine:
            for t in term.get(net, []):
                votes[t["side"]] += 1
        g["wanted"] = {s: v for s, v in votes.items() if v}
        g["has_terminal_nets"] = sorted(n for n in mine if term.get(n))
        g["actual_side"] = side_of_position(
            sum(p["x"] for p in g["pads"]) / len(g["pads"]),
            sum(p["y"] for p in g["pads"]) / len(g["pads"]), box)

    # --- crossing nets: a net whose terminal is on the opposite half of the
    #     board from the group that owns it.  These are the lines that must run
    #     the long way; counting them is the "43 of 97" reading, derived, not
    #     quoted.
    x0, y0, x1, y1 = box
    cx, cy = (x0 + x1) / 2.0, (y0 + y1) / 2.0
    crosses = []
    for net, ps in pads.items():
        ts = term.get(net) or []
        if not ts:
            continue
        t = ts[0]
        tv = (t["x"] - cx, t["y"] - cy)
        for g in movable:
            gp = [p for p in ps if member.get(p["ref"]) == g["name"]]
            if not gp:
                continue
            gv = (sum(p["x"] for p in gp) / len(gp) - cx,
                  sum(p["y"] for p in gp) / len(gp) - cy)
            if tv[0] * gv[0] + tv[1] * gv[1] < 0 and abs(tv[0]) + abs(tv[1]) > 1e-9:
                length = hpwl([(p["x"], p["y"]) for p in ps])
                crosses.append({"net": net, "group": g["name"],
                                "terminal_side": t["side"],
                                "group_side": side_of_position(
                                    sum(p["x"] for p in gp) / len(gp),
                                    sum(p["y"] for p in gp) / len(gp), box),
                                "airwire_mm": round(length, 3)})

    # --- exact side assignment: 4**N over rigid translations
    nets_worth = sorted(n for n, ps in pads.items() if term.get(n))
    base_place = {}
    for net in nets_worth:
        base_place[net] = [(p["x"], p["y"]) for p in pads[net]]
    base_total = sum(hpwl(base_place[n]) for n in nets_worth)

    names = [g["name"] for g in movable]
    targets = {}
    for g in movable:
        targets[g["name"]] = {s: shift_target(g["pads"], s, box) for s in SIDES}

    # pads per (net, group) so a translation is a constant offset
    by_net_group = {}
    for net in nets_worth:
        for p in pads[net]:
            gname = member.get(p["ref"])
            by_net_group.setdefault(net, {}).setdefault(gname, []).append(
                (p["x"], p["y"]))

    def total_for(assign):
        tot = 0.0
        for net in nets_worth:
            pts = []
            for gname, gp in by_net_group[net].items():
                if gname in assign:
                    dx, dy = targets[gname][assign[gname]]
                    pts.extend([(x + dx, y + dy) for x, y in gp])
                else:
                    pts.extend(gp)
            tot += hpwl(pts)
        return tot

    # --- the hub: the group holding the widest part (the module itself).  Moving
    #     it to one side is never the answer - it has to reach terminals on every
    #     side - and letting the search move it would make every other proposal
    #     look bad, so it is priced but held at its measured place.
    widest = None
    for fp in (getattr(board, "footprints", None) or []):
        ref = str(fp.get("ref") or "")
        n = len(fp.get("pads") or [])
        if widest is None or n > widest[0]:
            widest = (n, ref)
    hub = member.get(widest[1]) if widest else None

    names = [g["name"] for g in movable if g["name"] != hub]
    best = None
    method = "exact"
    if names and 4 ** len(names) <= ENUM_BUDGET:
        for combo in itertools.product(SIDES, repeat=len(names)):
            assign = dict(zip(names, combo))
            t = total_for(assign)
            if best is None or t < best[0] - 1e-9:
                best = (t, assign)
    elif names:
        # Too many groups for 4**N.  Coordinate descent: sweep one group at a
        # time until no single move helps.  Said out loud in `method`.
        method = "coordinate_descent"
        actual = {g["name"]: g["actual_side"] for g in movable}
        assign = {n: actual[n] for n in names}
        t = total_for(assign)
        for _ in range(4):
            moved = False
            for n in names:
                keep = assign[n]
                for s in SIDES:
                    if s == keep:
                        continue
                    assign[n] = s
                    cand = total_for(assign)
                    if cand < t - 1e-9:
                        t, keep, moved = cand, s, True
                assign[n] = keep
            if not moved:
                break
        best = (t, dict(assign))
    if best is None:
        best = (base_total, {})
    best_total, best_assign = best

    # --- marginal price of each side for each group, one group at a time.  This
    #     is the reading that says why the global answer is what it is: the hub's
    #     four sides all cost more, so "centre" wins without being an option.
    marginal = {}
    for g in movable:
        row = {}
        for s in SIDES:
            row[s] = round(total_for({g["name"]: s}) - base_total, 3)
        marginal[g["name"]] = row

    # --- collisions: the proposals are rigid translations, so two groups can land
    #     on top of each other.  Reporting that is the difference between a
    #     floorplan and a wish.
    placed_box = {}
    for g in movable:
        s = best_assign.get(g["name"])
        xs = [p["x"] for p in g["pads"]]
        ys = [p["y"] for p in g["pads"]]
        if s:
            dx, dy = targets[g["name"]][s]
        else:
            dx = dy = 0.0
        placed_box[g["name"]] = (min(xs) + dx, min(ys) + dy,
                                 max(xs) + dx, max(ys) + dy)
    overlaps = []
    keys = list(placed_box)
    for i in range(len(keys)):
        for j in range(i + 1, len(keys)):
            a, b = placed_box[keys[i]], placed_box[keys[j]]
            ox = min(a[2], b[2]) - max(a[0], b[0])
            oy = min(a[3], b[3]) - max(a[1], b[1])
            if ox > 0 and oy > 0:
                overlaps.append({"a": keys[i], "b": keys[j],
                                 "overlap_mm2": round(ox * oy, 3)})

    now_side = {g["name"]: g["actual_side"] for g in movable}
    rows = []
    for g in movable:
        rows.append({
            "group": g["name"], "parts": g["count"], "pads_moved": len(g["pads"]),
            "current_side": now_side[g["name"]],
            "suggested_side": best_assign.get(g["name"], now_side[g["name"]]),
            "is_hub": g["name"] == hub,
            "cost_if_only_this_moves_mm": marginal[g["name"]],
            "netlist_wants": sorted(g["wanted"].items(), key=lambda kv: -kv[1])[:3],
        })
    rows.sort(key=lambda r: (r["current_side"] == r["suggested_side"], r["group"]))

    cross_by_group = {}
    for c in crosses:
        cross_by_group.setdefault(c["group"], []).append(c)

    return {
        "board": os.path.abspath(path),
        "outline_box": [round(v, 3) for v in box],
        "how": ("every functional group is one rigid body; each takes one of W/E/S/N; "
                "the total is the half-perimeter airwire of every net that reaches a "
                "board-edge terminal; the search is %s over 4**N" % method),
        "method": method,
        "enum_budget": ENUM_BUDGET,
        "airwire_mm_now": round(base_total, 3),
        "airwire_mm_best": round(best_total, 3),
        "airwire_saved_mm": round(base_total - best_total, 3),
        "airwire_saved_frac": (round((base_total - best_total) / base_total, 4)
                               if base_total else None),
        "groups": rows,
        "crossing_nets": sorted(crosses, key=lambda c: -c["airwire_mm"]),
        "crossing_by_group": {k: len(v) for k, v in sorted(cross_by_group.items())},
        "crossing_total": len(crosses),
        "nets_priced": len(nets_worth),
        "hub_group": hub,
        "overlaps": overlaps,
        "targets": {n: {"side": s,
                         "dx_mm": round(targets[n][s][0], 3),
                         "dy_mm": round(targets[n][s][1], 3)}
                    for n, s in best_assign.items()},
        "note": ("a translation is a floorplan question, not a placement: nothing "
                 "inside a group moves. Applying one is a hand edit; this file "
                 "writes nothing."),
    }


def main(argv=None):
    ap = argparse.ArgumentParser(description=__doc__,
                                 formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--board", required=True)
    ap.add_argument("--json", action="store_true")
    ap.add_argument("--top", type=int, default=12)
    a = ap.parse_args(argv)

    try:
        got = analyse(a.board, top=a.top)
    except ValueError as exc:
        print("error : %s" % exc, file=sys.stderr)
        return 2
    if a.json:
        print(json.dumps(got, indent=2, ensure_ascii=False))
        return 0

    print("board   : %s" % got["board"])
    print("outline : %s" % got["outline_box"])
    print("airwire : %.3f mm now -> %.3f mm best  (saved %.3f mm, %s)"
          % (got["airwire_mm_now"], got["airwire_mm_best"], got["airwire_saved_mm"],
             ("%.1f%%" % (100.0 * got["airwire_saved_frac"]))
             if got["airwire_saved_frac"] is not None else "n/a"))
    print("nets priced: %d" % got["nets_priced"])
    print()
    print("hub group: %s  (has to reach terminals on every side; held at its place)"
          % got["hub_group"])
    print()
    print("%-10s %5s %5s %4s %4s %4s  %s"
          % ("group", "parts", "pads", "now", "best", "hub", "netlist wants"))
    for r in got["groups"]:
        print("%-10s %5d %5d %4s %4s %4s  %s"
              % (r["group"], r["parts"], r["pads_moved"], r["current_side"],
                 r["suggested_side"], "yes" if r["is_hub"] else "",
                 r["netlist_wants"]))
    print()
    print("cost of moving THAT group alone, by side (mm of airwire vs now):")
    for r in got["groups"]:
        cost = r["cost_if_only_this_moves_mm"]
        print("  %-10s %s" % (r["group"],
                               "  ".join("%s %+8.1f" % (s, cost[s]) for s in ("W", "E", "S", "N"))))
    if got["overlaps"]:
        print()
        print("proposals that land on each other: %d" % len(got["overlaps"]))
        for o in got["overlaps"]:
            print("  %s x %s  %.3f mm2" % (o["a"], o["b"], o["overlap_mm2"]))
    print()
    print("crossing nets (terminal on the opposite half from the group that owns it): %d"
          % got["crossing_total"])
    for k, v in got["crossing_by_group"].items():
        print("  %-10s %d" % (k, v))
    print()
    for c in got["crossing_nets"][:a.top]:
        print("  %-16s %-8s terminal %s / group %s   %8.3f mm airwire"
              % (c["net"], c["group"], c["terminal_side"], c["group_side"],
                 c["airwire_mm"]))
    print()
    print("targets (translation of the whole group, hand-applied):")
    for name, t in got["targets"].items():
        print("  %-10s %s  dx %+7.3f  dy %+7.3f" % (name, t["side"], t["dx_mm"], t["dy_mm"]))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
