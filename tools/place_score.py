#!/usr/bin/env python3
"""Score a floorplan: airwire length, and whether the SoC rotation is right.

Nothing here is a placement tool and nothing here is a route.  This file only
*measures* the placement that is already on the board, because "move the parts
around" is not answerable without a number: the question the numbers below
answer is

  * how long is each net's bounding-box (HPWL) airwire, and
  * is the big BGA turned the way that puts each pin group on the board edge
    its daughterboard pins are on?

The second one is the real floorplan decision on a stamped-module carrier: the
SoC's DDR, LCDC, SDMMC/RMII and SPI/SAI/UART balls sit on four different sides
of the package, and the 140 half-holes on the board's four edges are fixed by
the module pinout.  Four of the 24 assignments are reachable by rotating the
footprint, so the honest way to choose one is to price all four, not to look at
a picture.  `rotation_table` does exactly that: it rotates every SoC pad about
the package centre, re-prices the airwires to the half-holes, and prints the
total for 0/90/180/270.

A net that ends on neither the SoC nor a half-hole (a buck, a divider, a
decoupling cap) is priced once, at the measured placement, and is NOT moved by
the rotation experiment - rotating the SoC does not move it, so charging it to
the rotation would be a fake difference.

    python tools/place_score.py --board examples/rk3506_som/rk3506_som.kicad_pcb
    python tools/place_score.py --board B --top 20 --json
"""
import argparse
import json
import math
import os
import sys

HERE = os.path.dirname(os.path.abspath(__file__))
REPO = os.path.dirname(HERE)
sys.path.insert(0, os.path.join(REPO, "src"))

POWER = {"/GND", "/+5V_IN", "/+3V3", "/+1V35_DDR", "/+0V9_CPU", "/+0V9_LOGIC",
         "/+0V9_ANA", "/+0V9_PMU", "/+1V8_ANA", "/+0V675_REF"}
SIDES = ("W", "E", "S", "N")


def load(path):
    from pcb_agent_mcp.adapters import open_board
    b = open_board(path)
    pads = {}
    for info in (b.footprints or []):
        ref = info.get("ref")
        for p in info.get("pads") or []:
            net = p.get("net")
            if not net:
                continue
            pads.setdefault(net, []).append(
                (ref, str(p.get("number")), float(p["x"]), float(p["y"])))
    return b, pads


def side_of(x, y, lo=0.05, hi=44.95):
    """Which board edge a point sits on, or None for the interior."""
    if y >= hi:
        return "N"
    if y <= lo:
        return "S"
    if x >= hi:
        return "E"
    if x <= lo:
        return "W"
    return None


def hpwl(ps):
    xs = [p[2] for p in ps]
    ys = [p[3] for p in ps]
    return (max(xs) - min(xs)) + (max(ys) - min(ys))


def board_centre(b):
    """Centre of the board outline.  The outline arrives as a list of rings of
    (x, y) points, so the bounding box is measured, not assumed to be 45 x 45."""
    xs, ys = [], []
    for ring in (b.outline or []):
        for pt in ring:
            xs.append(float(pt[0]))
            ys.append(float(pt[1]))
    if not xs:
        return 22.5, 22.5
    return (min(xs) + max(xs)) / 2.0, (min(ys) + max(ys)) / 2.0


def rotation_table(pads, ref, centre, group=None):
    """Total airwire of every net that touches `ref` or a half-hole, per rotation.

    A net is priced only when it has a pad on `ref` and its other pads are fixed
    (a half-hole or another part).  Rotating `ref` moves its pads; nothing else
    moves.  A net that does not touch `ref` is charged to every rotation with
    the same value, so it cancels out of the comparison and is left out.
    """
    moving = set(group or [ref])
    cx, cy = centre
    out = {}
    for deg in (0, 90, 180, 270):
        a = math.radians(deg)
        ca, sa = math.cos(a), math.sin(a)
        tot = 0.0
        moved = []
        for net, ps in pads.items():
            mine = [p for p in ps if p[0] in moving]
            if not mine:
                continue
            others = [p for p in ps if p[0] not in moving]
            if not others and len(mine) < 2:
                continue
            pts = []
            for ref_i, num, x, y in mine:
                dx, dy = x - cx, y - cy
                pts.append((ref_i, num, cx + dx * ca - dy * sa,
                            cy + dx * sa + dy * ca))
            tot += hpwl(pts + others)
            moved.append(net)
        out[deg] = {"airwire_mm": round(tot, 2),
                    "nets_priced": len(moved)}
    return out


def group_sides(pads, ref, centre):
    """For every pin group of `ref`, which side the package puts it on."""
    cx, cy = centre
    groups = {}
    for net, ps in pads.items():
        mine = [p for p in ps if p[0] == ref]
        if not mine:
            continue
        ref_i, num, x, y = mine[0]
        dx, dy = x - cx, y - cy
        side = "E" if abs(dx) >= abs(dy) else "N"
        if (side == "E" and dx < 0) or (side == "N" and dy < 0):
            side = "W" if side == "E" else "S"
        groups.setdefault(side, []).append(net)
    return dict((k, sorted(v)) for k, v in groups.items())


def castellation_pairs(pads, ref, j_ref="J1"):
    """(net, soc_side_angle, board_side) for every SoC pad whose net ends on J1."""
    out = []
    for net, ps in pads.items():
        mine = [p for p in ps if p[0] == ref]
        js = [p for p in ps if p[0] == j_ref]
        if not mine or not js:
            continue
        out.append((net, (mine[0][2], mine[0][3]), side_of(js[0][2], js[0][3])))
    return out


def main(argv=None):
    ap = argparse.ArgumentParser()
    ap.add_argument("--board", default=os.path.join(
        REPO, "examples", "rk3506_som", "rk3506_som.kicad_pcb"))
    ap.add_argument("--ref", default=None, help="the package to rotate (auto: "
                    "the one with the most pads)")
    ap.add_argument("--group", default=None,
                    help="comma separated refs rotated together with --ref, e.g. "
                         "U2 so the DDR pair keeps its offset")
    ap.add_argument("--top", type=int, default=15)
    ap.add_argument("--json", action="store_true")
    a = ap.parse_args(argv)

    b, pads = load(a.board)
    ref = a.ref
    if ref is None:
        counts = {}
        for net, ps in pads.items():
            for p in ps:
                counts[p[0]] = counts.get(p[0], 0) + 1
        ref = max(counts, key=lambda r: counts[r])
    centre = board_centre(b)

    rows = sorted(((hpwl(ps), net, len(ps)) for net, ps in pads.items()),
                  key=lambda r: -r[0])
    sig = [(h, n, k) for h, n, k in rows if n not in POWER]
    group = [g.strip() for g in (a.group or "").split(",") if g.strip()]
    rot = rotation_table(pads, ref, centre, group)
    best = min(rot, key=lambda d: rot[d]["airwire_mm"])
    deltas = dict((d, round(rot[d]["airwire_mm"] - rot[0]["airwire_mm"], 2))
                  for d in rot)

    # side-by-side table: where the module pins are, and where the SoC puts them
    pairs = castellation_pairs(pads, ref)
    mix = {}
    for net, (x, y), bside in pairs:
        dx, dy = x - centre[0], y - centre[1]
        s = ("E" if dx > 0 else "W") if abs(dx) >= abs(dy) else ("N" if dy > 0 else "S")
        mix.setdefault((s, bside), []).append(net)
    matched = sum(len(v) for (s, bside), v in mix.items() if s == bside)
    crossed = [(k, sorted(v)) for k, v in mix.items() if k[0] != k[1]]

    report = {
        "board": os.path.abspath(a.board),
        "pads_ref": ref,
        "nets": len(pads),
        "airwire_total_mm": round(sum(r[0] for r in rows), 2),
        "airwire_signal_mm": round(sum(r[0] for r in sig), 2),
        "signal_nets_over_30mm": sum(1 for h, _, _ in sig if h > 30),
        "rotation_group": sorted(set([ref] + group)),
        "rotation_airwire_mm": rot,
        "rotation_delta_vs_0_mm": deltas,
        "best_rotation_deg": best,
        "module_pins_on_their_own_side": matched,
        "module_pins_on_the_far_side": sum(len(v) for _, v in crossed),
        "far_side_groups": [{"soc_side": k[0], "board_side": k[1], "nets": v}
                            for k, v in crossed],
        "worst_nets": [{"net": n, "hpwl_mm": round(h, 2), "pads": k}
                       for h, n, k in rows[:a.top]],
    }
    if a.json:
        print(json.dumps(report, ensure_ascii=False, indent=2))
    else:
        print("board %s  (%d nets, package %s)"
              % (report["board"], report["nets"], ref))
        print("airwire total %.1f mm, signal-only %.1f mm, %d signal nets > 30 mm"
              % (report["airwire_total_mm"], report["airwire_signal_mm"],
                 report["signal_nets_over_30mm"]))
        print("\n--- rotating %s about the board centre" % ref)
        print("   deg   airwire mm   vs 0 deg   nets priced")
        for d in (0, 90, 180, 270):
            print("  %4d   %10.2f   %+9.2f   %d"
                  % (d, rot[d]["airwire_mm"], deltas[d], rot[d]["nets_priced"]))
        print("  best: %d deg (%s)" % (best, "same as built" if best == 0
                                       else "NOT the built rotation"))
        print("\n--- module pins: %d on the edge they belong to, %d on another edge"
              % (matched, report["module_pins_on_the_far_side"]))
        for k, v in crossed:
            print("   SoC %s -> board %s : %d nets  %s"
                  % (k[0], k[1], len(v), ", ".join(v[:6]) + (" ..." if len(v) > 6 else "")))
        print("\n--- worst %d nets by airwire" % a.top)
        for h, n, k in rows[:a.top]:
            print("  %7.2f  %-22s %d pads%s" % (h, n, k,
                                                "  POWER" if n in POWER else ""))
    return 0


if __name__ == "__main__":
    sys.exit(main())
