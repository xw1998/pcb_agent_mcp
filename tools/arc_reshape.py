#!/usr/bin/env python3
"""Rewrite the triangular meanders ALREADY on a board as arc meanders.

`board_tune_length` grows a rounded meander (90-degree arcs, `h + r*(pi-2)` per
tooth) out of a STRAIGHT segment. It cannot touch copper that has already been
meandered: a 45-degree triangle wave that went into the file in 0.1.22 is
existing copper, and the tool that made it only ever added to a straight run. So
a board keeps its triangles forever, and the user sees them again on every
render - which is exactly what happened on rk3506_som (`/DDR_CASN` on In2.Cu:
32 legs alternating 315/225 degrees, amplitude 0.255, no arc anywhere on the
whole board).

This tool reads the triangles out of the copper and re-cuts the SAME corridor as
a rounded meander:

  * a meander is found as a maximal chain of short segments where every joint
    turns exactly 90 degrees (+-1) and every vertex stays on ONE side of the
    chain's own chord. One-sided is the test that separates a meander from a
    45-degree fan-out staircase: a staircase's intermediate vertices do not all
    fall on the same side of the line its ends define;
  * the chain's chord becomes the segment, its `sum(length) - chord` becomes the
    extra length, and `route.tune_board.arc_meander` (the shape the user asked
    for, already tested) cuts the wave. `amp_max` is the amplitude the OLD copper
    already occupied, so the new wave cannot reach further than the copper it
    replaces - the corridor it was checked in is the corridor it is drawn in;
  * every piece is verified before anything is written: the pieces join end to
    end, their length equals the chain's length to 1e-6, and no piece comes
    within clearance of foreign copper on its own layer (arcs are probed chord by
    chord with the sagitta added, the same way tune_board does it);
  * the write goes through the MCP's own `board_propose` + `board_apply`, so the
    four gates (bytes == plan, re-parse, per-op postcondition, KiCad DRC diff)
    judge it, not this script.

Nothing here routes, moves a pad, or tunes a net that is not already matched:
the length before and the length after are the same number.

    python tools/arc_reshape.py BOARD                 # report only
    python tools/arc_reshape.py BOARD --apply OUT.kicad_pcb
"""
import argparse
import json
import math
import os
import sys

sys.path.insert(0, os.path.join(os.path.dirname(os.path.dirname(
    os.path.abspath(__file__))), "src"))

from pcb_agent_mcp.adapters import open_board  # noqa: E402
from pcb_agent_mcp.adapters.canonical import pad_points  # noqa: E402
from pcb_agent_mcp.route import tune_board as TB  # noqa: E402
from pcb_agent_mcp.shape_gate import (  # noqa: E402
    _point_in_polygon, _seg_point_distance)

#: A meander leg is short. 1.5 mm is above every tooth on rk3506_som (0.36-0.51
#: per leg) and below the 2 mm stubs ordinary routing leaves around a BGA.
SHORT_MM = float(os.environ.get("ARS_SHORT", "1.5"))

#: The gate's own corner tolerance. The same number is used for "this joint is
#: 90 degrees" because being 1 degree off here is being 1 degree off there.
TOL_DEG = 1.0

#: Body-to-body gap a new piece must keep from foreign copper on its layer.
CLR = float(os.environ.get("ARS_CLR", "0.15"))

#: Body to board edge.
EDGE = float(os.environ.get("ARS_EDGE", "0.50"))

#: The wave may not reach further than the copper it replaces, by more than this.
AMP_SLACK = float(os.environ.get("ARS_AMP", "0.002"))

#: Radius ladder for the wave: small radii first, because the corridor being
#: re-cut is an existing meander's, not a fresh segment's.
R_LADDER = (0.20, 0.15, 0.12, 0.10, 0.08, 0.06, 0.05, 0.04, 0.03)

H_MIN = TB.ARC_H_MIN          # geometric floor; a shallower bar is not a bar

#: How far the re-measured length may drift from the chain's length. The pieces
#: are written with points rounded to 1e-6 mm, and an arc's LENGTH is re-derived
#: from its three points, so a 40-piece wave carries ~2e-5 mm of that rounding.
#: Measured: /DDR_CASN 11.539983 -> 11.540004 (+2.117e-05). Anything larger is a
#: different length, not a rounded one, and is refused with both numbers.
LEN_TOL = float(os.environ.get("ARS_LTOL", "1e-4"))


def _k(pt):
    return (round(float(pt[0]), 3), round(float(pt[1]), 3))


def _hdg(a, b):
    return math.degrees(math.atan2(b[1] - a[1], b[0] - a[0])) % 360.0


class Model(object):
    """The board's straight copper, indexed for chain walking."""

    def __init__(self, board):
        self.board = board
        self.segs = []
        self.arcs = 0
        for t in board.tracks:
            if t.get("_arc"):
                self.arcs += 1
                continue
            s, e = _k(t["start"]), _k(t["end"])
            L = math.hypot(e[0] - s[0], e[1] - s[1])
            if L <= 1e-9:
                continue
            self.segs.append({"net": t["net"], "layer": t["layer"],
                              "w": float(t["width"]), "s": s, "e": e, "L": L,
                              "h": _hdg(s, e), "uuid": t["uuid"],
                              "locked": bool(t.get("locked"))})
        self.at = {}
        for i, g in enumerate(self.segs):
            key = (g["net"], g["layer"], round(g["w"], 3))
            d = self.at.setdefault(key, {})
            d.setdefault(g["s"], []).append(i)
            d.setdefault(g["e"], []).append(i)
        # Every object whose copper touches a point, so "does this chain end land
        # on copper that STAYS behind" can be asked before anything is deleted.
        # An arc counts at its two endpoints only: KiCad connects an arc there, not
        # along its body. Points are keyed at 1 um - this is a yes/no gate, not a
        # measurement.
        self.touch = {}

        def _t(net, layer, pt, uid):
            self.touch.setdefault((net, layer, _k(pt)), []).append(uid)

        for t in board.tracks:
            for p in (t["start"], t["end"]):
                _t(t["net"], t["layer"], p, t["uuid"])
        for v in board.vias:
            for layer in v.get("layers") or []:
                _t(v["net"], layer, (v["x"], v["y"]), v["uuid"])
        for pd in board.pads():
            for layer in pd.get("layers") or []:
                for p in pd.get("points") or []:
                    _t(pd.get("net"), layer, p, pd["uuid"])

    def touches(self, net, layer, pt, exclude=()):
        """How many copper objects other than `exclude` meet this point."""
        return sum(1 for uid in self.touch.get((net, layer, _k(pt)), [])
                   if uid not in exclude)

    def key(self, g):
        return (g["net"], g["layer"], round(g["w"], 3))

    def deg(self, i):
        g = self.segs[i]
        return (len(self.at[self.key(g)].get(g["s"], [])) - 1
                + len(self.at[self.key(g)].get(g["e"], [])) - 1)


def _is_90(d):
    d %= 360.0
    return abs(d - 90.0) <= TOL_DEG or abs(d - 270.0) <= TOL_DEG


def chains(model):
    """Maximal short-segment chains whose joints are all 90 degrees."""
    used = set()
    out = []
    for i in sorted(range(len(model.segs)), key=lambda x: model.segs[x]["L"]):
        if i in used or model.segs[i]["L"] > SHORT_MM:
            continue

        def walk(start, from_pt, mine):
            cur, pt = start, from_pt
            got = []
            while True:
                g = model.segs[cur]
                nxt = g["e"] if pt == g["s"] else g["s"]
                step = None
                for j in model.at[model.key(g)].get(nxt, []):
                    if j == cur or j in used or j in mine:
                        continue
                    gj = model.segs[j]
                    if gj["L"] > SHORT_MM:
                        continue
                    if _is_90(((gj["h"] + 180.0) % 360.0) - g["h"]):
                        step = j
                        break
                if step is None:
                    return got, nxt
                got.append(step)
                cur, pt = step, nxt

        g = model.segs[i]
        a_side = walk(i, g["e"], [i])
        b_side = walk(i, g["s"], [i])
        if len(a_side[0]) + len(b_side[0]) + 1 < 3:
            continue
        order = list(reversed(b_side[0])) + [i] + a_side[0]
        # the walk may enter the same leg twice from a T junction; keep the first
        if len(set(order)) != len(order):
            continue
        gs = [model.segs[x] for x in order]
        # The chain's two ATTACH points are its FREE ends - the ends that are not
        # shared with the next leg. On a 45-degree zigzag the first and last
        # VERTICES are tooth tips, not the line the copper actually runs on: this
        # chain hangs its teeth off x=21.843 and its first/last vertices sit at
        # x=21.588, 0.255 mm in. Taking the vertices as the chord put the whole
        # re-cut wave on the tip line, so both ends finished 0.11 mm short of the
        # run they were supposed to join - 32 "track has unconnected end" in the
        # engine's DRC, and the length still measured right because the wave was
        # the right LENGTH on the wrong LINE.
        def _free_end(i):
            g = gs[i]
            nb = gs[i + 1] if i == 0 else gs[i - 1]
            shared = (_k(nb["s"]), _k(nb["e"]))
            return g["e"] if _k(g["s"]) in shared else g["s"]

        mine = [g["uuid"] for g in gs]
        a, b = _free_end(0), _free_end(len(gs) - 1)
        chord = math.hypot(b[0] - a[0], b[1] - a[1])
        if chord < 1e-6:
            continue
        ux, uy = (b[0] - a[0]) / chord, (b[1] - a[1]) / chord
        offs = []
        for g2 in gs:
            for pt in (g2["s"], g2["e"]):
                offs.append((pt[0] - a[0]) * (-uy) + (pt[1] - a[1]) * ux)
        sign = [1 if o > 1e-9 else (-1 if o < -1e-9 else 0) for o in offs]
        nz = [s for s in sign if s]
        if not nz or len(set(nz)) != 1:
            continue                                   # two-sided: not this shape
        total = sum(g["L"] for g in gs)
        out.append({
            "net": gs[0]["net"], "layer": gs[0]["layer"], "w": gs[0]["w"],
            "segs": order, "uuids": [g["uuid"] for g in gs],
            "start": a, "end": b, "chord_mm": round(chord, 6),
            "length_mm": round(total, 6), "extra_mm": round(total - chord, 6),
            "amp_mm": round(max(abs(o) for o in offs), 6), "side": nz[0],
            "locked": any(g["locked"] for g in gs),
            # both ends must land on copper that stays behind, or the re-cut wave
            # floats and every one of its ends reads as an unconnected track
            "attach_connected": [
                model.touches(gs[0]["net"], gs[0]["layer"], a, mine) > 0,
                model.touches(gs[0]["net"], gs[0]["layer"], b, mine) > 0],
        })
        used.update(order)
    out.sort(key=lambda r: -r["extra_mm"])
    return out


# ---- clearance pre-check ---------------------------------------------------

class Copper(object):
    """Foreign copper per layer, bucketed so a probe does not scan the board."""

    CELL = 1.0

    def __init__(self, board):
        self.items = {}          # layer -> {cell: [(kind, ...)]}
        self.vias = {}
        for t in board.tracks:
            if t.get("_arc"):
                pts = t.get("_points") or [t["start"], t["end"]]
                for k in range(len(pts) - 1):
                    self._add(t["layer"], t["net"], t["width"],
                              (pts[k][0], pts[k][1]), (pts[k + 1][0], pts[k + 1][1]))
            else:
                self._add(t["layer"], t["net"], t["width"],
                          tuple(t["start"]), tuple(t["end"]))
        for fp in board.footprints:
            for pd in fp.get("pads") or []:
                pts = pad_points(pd)
                if not pts:
                    continue
                for lay in pd.get("layers") or []:
                    self._add_poly(lay, pd.get("net"), pts)
        for v in board.vias:
            r = (v.get("dia") or v.get("size") or 0.2) / 2.0
            for lay in v.get("layers") or []:
                self._add_circle(lay, v.get("net"), v["x"], v["y"], r)

    def _cells(self, x0, y0, x1, y1):
        c = self.CELL
        for ix in range(int(math.floor(min(x0, x1) / c)) - 1,
                        int(math.floor(max(x0, x1) / c)) + 2):
            for iy in range(int(math.floor(min(y0, y1) / c)) - 1,
                            int(math.floor(max(y0, y1) / c)) + 2):
                yield (ix, iy)

    def _put(self, layer, cell, item):
        self.items.setdefault(layer, {}).setdefault(cell, []).append(item)

    def _add(self, layer, net, w, a, b):
        for cell in self._cells(a[0], a[1], b[0], b[1]):
            self._put(layer, cell, ("seg", net, float(w), a, b))

    def _add_poly(self, layer, net, pts):
        for k in range(len(pts)):
            a, b = pts[k], pts[(k + 1) % len(pts)]
            for cell in self._cells(a[0], a[1], b[0], b[1]):
                self._put(layer, cell, ("seg", net, 0.0, tuple(a), tuple(b)))
        xs = [p[0] for p in pts]
        ys = [p[1] for p in pts]
        for cell in self._cells(min(xs), min(ys), max(xs), max(ys)):
            self._put(layer, cell, ("poly", net, 0.0, pts))

    def _add_circle(self, layer, net, x, y, r):
        for cell in self._cells(x - r, y - r, x + r, y + r):
            self._put(layer, cell, ("disc", net, r, (x, y)))

    def gap(self, layer, net, px, py, half_w):
        """Smallest body-to-body gap from (px,py) to copper that is not `net`."""
        best = 1e9
        seen = set()
        for cell in self._cells(px, py, px, py):
            for it in self.items.get(layer, {}).get(cell, ()):
                k = id(it)
                if k in seen:
                    continue
                seen.add(k)
                if it[1] == net:
                    continue
                kind = it[0]
                if kind == "seg":
                    d = _seg_point_distance(px, py, it[3][0], it[3][1],
                                            it[4][0], it[4][1]) - it[2] / 2.0
                elif kind == "disc":
                    d = math.hypot(px - it[3][0], py - it[3][1]) - it[2]
                else:
                    pts = it[3]
                    if _point_in_polygon(px, py, pts):
                        d = -1.0
                    else:
                        d = min(_seg_point_distance(px, py, pts[k][0], pts[k][1],
                                                    pts[(k + 1) % len(pts)][0],
                                                    pts[(k + 1) % len(pts)][1])
                                for k in range(len(pts)))
                best = min(best, d - half_w)
        return best


def _sample_pieces(pieces, step=0.05):
    """Points along a piece list, arcs included (chord-probed with the sag)."""
    for piece in pieces:
        if piece[0] == "line":
            pairs = [(piece[1], piece[2])]
        else:
            pairs, _sag = TB._arc_chords(piece[1], piece[2], piece[3])
        for a, b in pairs:
            L = math.hypot(b[0] - a[0], b[1] - a[1])
            n = max(1, int(math.ceil(L / step)))
            for k in range(n + 1):
                t = k / float(n)
                yield (a[0] + (b[0] - a[0]) * t, a[1] + (b[1] - a[1]) * t)


def _piece_len(piece):
    if piece[0] == "line":
        return math.hypot(piece[2][0] - piece[1][0], piece[2][1] - piece[1][1])
    got = TB._arc_geom(piece[1], piece[2], piece[3])
    if got is None:
        return math.hypot(piece[3][0] - piece[1][0], piece[3][1] - piece[1][1])
    return abs(math.radians(got[3])) * got[1]


def plan_chain(ch, copper):
    """The re-cut wave for one chain, or a refusal with the reason it stopped."""
    if ch["locked"]:
        return None, {"reason": "copper_is_locked"}
    if not all(ch.get("attach_connected") or [False, False]):
        # An end that meets nothing means the delete would leave a floating wave
        # behind: the length would still measure right and every gate on length
        # would pass, which is exactly the failure this line refuses.
        return None, {"reason": "attach_floating",
                      "attach_connected": ch.get("attach_connected")}
    seg = {"start": tuple(ch["start"]), "end": tuple(ch["end"]),
           "length_mm": ch["chord_mm"], "width": ch["w"],
           "layer": ch["layer"], "net": ch["net"]}
    got = TB.arc_meander(seg, ch["extra_mm"], ch["amp_mm"] + AMP_SLACK,
                         side=ch["side"], r_ladder=R_LADDER, h_min=H_MIN)
    if got is None:
        return None, {"reason": "does_not_fit",
                      "amp_max_mm": round(ch["amp_mm"], 4)}
    if got.get("pieces") is None:
        return None, {"reason": got.get("reason") or "too_shallow",
                      "detail": {k: v for k, v in got.items() if k != "pieces"}}
    pieces = got["pieces"]
    # 1. the pieces join end to end, from the chain's first point to its last
    for k in range(len(pieces) - 1):
        if abs(pieces[k][-1][0] - pieces[k + 1][1][0]) > 1e-6 or \
                abs(pieces[k][-1][1] - pieces[k + 1][1][1]) > 1e-6:
            return None, {"reason": "pieces_disconnected", "at_piece": k}
    if abs(pieces[0][1][0] - ch["start"][0]) > 1e-6 or \
            abs(pieces[0][1][1] - ch["start"][1]) > 1e-6:
        return None, {"reason": "start_moved"}
    if abs(pieces[-1][-1][0] - ch["end"][0]) > 1e-6 or \
            abs(pieces[-1][-1][1] - ch["end"][1]) > 1e-6:
        return None, {"reason": "end_moved"}
    # 2. the length is the SAME number, not a new one
    after = sum(_piece_len(p) for p in pieces)
    if abs(after - ch["length_mm"]) > LEN_TOL:
        return None, {"reason": "length_changed",
                      "before_mm": round(ch["length_mm"], 6),
                      "after_mm": round(after, 6),
                      "delta_mm": round(after - ch["length_mm"], 8)}
    # 3. clearance, chord by chord, arcs widened by their sagitta
    half = ch["w"] / 2.0
    for a, b, w in TB._pieces_probe(pieces, ch["w"]):
        for (px, py) in _sample_pieces([("line", a, b)]):
            g = copper.gap(ch["layer"], ch["net"], px, py, w / 2.0)
            if g < CLR - 1e-9:
                return None, {"reason": "clearance", "at": [round(px, 3), round(py, 3)],
                              "gap_mm": round(g, 4), "need_mm": CLR}
    for (px, py) in _sample_pieces(pieces):
        if not (-1e-9 <= px <= 45.0 + 1e-9 and -1e-9 <= py <= 45.0 + 1e-9):
            return None, {"reason": "outside_frame", "at": [round(px, 3), round(py, 3)]}
    return {"pieces": pieces, "shape": got}, None


def ops_for(ch, plan):
    ops = [{"op": "delete_track", "uuid": u} for u in ch["uuids"]]
    for piece in plan["pieces"]:
        if piece[0] == "line":
            a, b = piece[1], piece[2]
            ops.append({"op": "add_track", "x1": a[0], "y1": a[1],
                        "x2": b[0], "y2": b[1], "width": ch["w"],
                        "layer": ch["layer"], "net": ch["net"]})
        else:
            s, m, e = piece[1], piece[2], piece[3]
            ops.append({"op": "add_track_arc",
                        "x1": s[0], "y1": s[1], "mx": m[0], "my": m[1],
                        "x2": e[0], "y2": e[1], "width": ch["w"],
                        "layer": ch["layer"], "net": ch["net"]})
    return ops


def main(argv=None):
    ap = argparse.ArgumentParser()
    ap.add_argument("board")
    ap.add_argument("--apply", metavar="OUT", default=None,
                    help="write through board_propose/board_apply to OUT")
    ap.add_argument("--limit", type=int, default=0)
    ap.add_argument("--json", default=None)
    a = ap.parse_args(argv)

    board = open_board(a.board)
    m = Model(board)
    chs = chains(m)
    copper = Copper(board)
    print("board arcs: %d   segments: %d   meander chains: %d"
          % (m.arcs, len(m.segs), len(chs)), flush=True)

    jobs, refused = [], []
    for ch in (chs[:a.limit] if a.limit else chs):
        plan, why = plan_chain(ch, copper)
        if plan is None:
            refused.append((ch, why))
            print("  REFUSED %-14s %-7s extra %5.2f  %s"
                  % (ch["net"], ch["layer"], ch["extra_mm"], why["reason"]), flush=True)
        else:
            sh = plan["shape"]
            jobs.append((ch, plan))
            print("  plan    %-14s %-7s n=%2d extra %5.2f -> teeth %2d r %.2f h %.3f "
                  "(amp %.2f) pieces %d"
                  % (ch["net"], ch["layer"], len(ch["segs"]), ch["extra_mm"],
                     sh["teeth"], sh["r"], sh["h"], sh["amp"], len(plan["pieces"])),
                  flush=True)

    extra = sum(c["extra_mm"] for c, _ in jobs)
    print("planned %d chains / refused %d / extra preserved %.3f mm"
          % (len(jobs), len(refused), extra), flush=True)

    if a.json:
        with open(a.json, "w") as fh:
            json.dump({"jobs": [{"net": c["net"], "layer": c["layer"],
                                 "extra_mm": c["extra_mm"]} for c, _ in jobs],
                       "refused": [{"net": c["net"], "why": w} for c, w in refused]},
                      fh, indent=1)

    if not a.apply:
        return 0
    ops = []
    for ch, plan in jobs:
        ops.extend(ops_for(ch, plan))
    print("operations: %d" % len(ops), flush=True)

    # The .kicad_pro carries the design rules (clearance minimums, severities).
    # `board_apply`'s DRC gate reads the written file against the project BESIDE
    # it, so a board copied to a directory without its project is judged by
    # KiCad's defaults instead of by this design's rules - and a pair of tracks
    # that the project allows at 0.1449 mm then reads as a brand-new clearance
    # error at a place this edit never touched. Copy the project along.
    src_pro = os.path.splitext(os.path.abspath(a.board))[0] + ".kicad_pro"
    out_pro = os.path.splitext(os.path.abspath(a.apply))[0] + ".kicad_pro"
    if os.path.isfile(src_pro) and not os.path.isfile(out_pro):
        import shutil
        shutil.copyfile(src_pro, out_pro)
        print("project %s -> %s" % (os.path.basename(src_pro),
                                    os.path.basename(out_pro)), flush=True)

    from pcb_agent_mcp.tools import TOOLS
    prop = TOOLS["board_propose"]["handler"]({
        "path": a.board, "operations": ops,
        "options": {"engine": True, "mode": "new_file",
                    "out_path": os.path.abspath(a.apply)}})
    if prop.get("status") != "ok":
        print("PROPOSE FAILED: %s" % json.dumps(prop)[:2000])
        return 2
    pv = prop.get("value") or prop      # propose answers flat, not wrapped
    print("plan_id %s  blocking %s" % (pv.get("plan_id"), pv.get("blocking")))
    if pv.get("blocking"):
        return 3
    app = TOOLS["board_apply"]["handler"]({
        "plan_id": pv["plan_id"], "confirm": True,
        "options": {"mode": "new_file",
                    "out_path": os.path.abspath(a.apply)}})
    print("apply: %s" % app.get("status"))
    print(json.dumps(app.get("value", app))[:3000])
    return 0 if app.get("status") == "ok" else 4


if __name__ == "__main__":
    sys.exit(main())
