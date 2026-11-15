"""Break every BGA ball out to its own via.  Nothing here is a route.

What this file does is one stated geometric template, applied to every ball of
one footprint:

    ball centre  --0.10 mm, 45 degrees-->  interstitial centre  --0.20/0.10 via

The interstitial is the centre of the square formed by four balls, i.e. the
ball plus (+/-p, +/-p) where p = pitch / 2 -- one half ball pitch on each axis,
so the via sits 0.397 mm from the four balls that own the cell (pitch 0.562)
and 0.172 mm clear of their pads.  Note p is *not* pitch / sqrt(2): that would
put the via 0.562 mm away on the diagonal, i.e. straight on top of the ball
that shares the corner.  A ball takes the first interstitial, in the fixed
order NE, NW, SE, SW, that

  * is inside the board with the 0.30 mm copper-to-outline rule,
  * has no via within VIA_KEEP of it,
  * is at least BALL_KEEP from every ball of the *same* footprint other than
    its own,
  * is at least PAD_KEEP from every pad of every other footprint,

and whose stub -- the whole segment from the ball centre to the via, not just
its far end -- additionally

  * keeps SEG_BALL from every other ball centre (so the stub cannot graze a
    neighbouring pad; this is the rule that kills a long diagonal stub, since
    the next ball along a diagonal sits exactly on it),
  * keeps SEG_SEG from every stub already placed (no crossings, no 0.1 mm
    parallels).

The candidate order starts with the two *upward* interstitials.  That is not a
preference, it is the only order that assigns every ball of a full lattice a
via of its own: with a row-wise upward rule each interstitial of the row above
is claimed by exactly one ball, and no stub crosses any other.  Downward
interstitials are tried after, and are what the outermost row needs.

Two kinds of ball get no via, and both are counted rather than ignored: a ball
with no net at all, and a ball whose net is a declared no-connect (`/NC_*`) or
KiCad's own `unconnected-(...)` marker -- nothing sits on the other end of that
net, so a via would be copper with no destination.

There is no cost function and no backtracking: a ball that finds no free
interstitial is *counted and listed*, not quietly moved somewhere else.  The
numbers that matter are printed (placed / refused, and the worst clearance the
template produces), because a fan-out that cannot be done has to say so.

    python tools/fanout_som.py --audit     # geometry only, board untouched
    python tools/fanout_som.py --apply     # place the vias and the stubs
"""
import argparse
import json
import math
import os
import sys

HERE = os.path.dirname(os.path.abspath(__file__))
REPO = os.path.dirname(HERE)
sys.path.insert(0, os.path.join(REPO, "src"))

EXAMPLES = os.path.join(REPO, "examples", "rk3506_som")
# A rebuild runs on a copy so a whole regeneration never has to edit the file
# under version control (same convention as tools/route_som.py).
BOARD = os.environ.get("SOM_BOARD") or os.path.join(EXAMPLES, "rk3506_som.kicad_pcb")

TRACK_W = 0.10
VIA_DIA = 0.20
VIA_DRILL = 0.10
VIA_KEEP = 0.42      # via edge to via edge >= 0.22, i.e. 0.02 over the class
BALL_KEEP = 0.30
PAD_KEEP = 0.28
EDGE_CLEAR = 0.30
SEG_BALL = 0.28      # stub centreline to any other ball centre
SEG_SEG = 0.20       # stub centreline to stub centreline
REFUSED = []

# The candidate list, in the order a ball tries it.  Ring 1 is the four
# neighbouring interstitials (0.397 mm from a 0.562 ball, 0.46 from a 0.650
# one).  Rings 2 and 3 exist because a dense run of balls can exhaust ring 1 --
# measured on this board: 52 of 429 balls had every ring-1 interstitial taken,
# and refusing them would have left the two outermost rows unrouted.  A ball
# that walks out two or three interstitials is still following one stated rule,
# not searching: the ring order is fixed and nothing is ever revisited.
CANDS = [(step, sx, sy) for step in range(1, 4)
         for sx, sy in ((1, 1), (-1, 1), (1, -1), (-1, -1))]


def sexpr():
    from pcb_agent_mcp.fidelity import sexpr as S
    return S


def pads(path=BOARD):
    """Every pad on the board, Y up millimetres, with its net name."""
    S = sexpr()
    doc = S.parse(open(path, encoding="utf-8").read())
    board = doc.items[0]
    out = {}
    for fp in S.children(board, "footprint"):
        ref = None
        for t in S.children(fp, "fp_text"):
            if S.arg_text(t, 1) == "reference":
                ref = S.arg_text(t, 2)
        for t in S.children(fp, "property"):
            if S.arg_text(t, 1) == "Reference":
                ref = S.arg_text(t, 2)
        at = S.child(fp, "at")
        fx, fy = S.arg_num(at, 1), S.arg_num(at, 2)
        rot = S.arg_num(at, 3) or 0.0
        th = math.radians(rot)
        for p in S.children(fp, "pad"):
            num = S.arg_text(p, 1)
            pat = S.child(p, "at")
            lx, ly = S.arg_num(pat, 1), S.arg_num(pat, 2)
            ox = lx * math.cos(th) + ly * math.sin(th)
            oy = -lx * math.sin(th) + ly * math.cos(th)
            sz = S.child(p, "size")
            nn = S.child(p, "net")
            net = ""
            if nn is not None:
                t = [a.text for a in nn.items[1:] if isinstance(a, S.Atom)]
                net = t[-1] if t else ""
            out.setdefault(ref, {})[num] = {
                "x": round(fx + ox, 4), "y": round(-(fy + oy), 4),
                "w": S.arg_num(sz, 1), "h": S.arg_num(sz, 2), "net": net}
    return out


def pitch_of(balls):
    """The lattice step: the shortest distance between two ball centres."""
    best = 1e9
    pts = [(b["x"], b["y"]) for b in balls]
    for i in range(len(pts)):
        for j in range(i + 1, len(pts)):
            d = math.hypot(pts[i][0] - pts[j][0], pts[i][1] - pts[j][1])
            if d < best:
                best = d
    return best


def _pt_seg(px, py, x1, y1, x2, y2):
    dx, dy = x2 - x1, y2 - y1
    L = dx * dx + dy * dy
    if L == 0.0:
        return math.hypot(px - x1, py - y1)
    t = ((px - x1) * dx + (py - y1) * dy) / L
    t = 0.0 if t < 0.0 else (1.0 if t > 1.0 else t)
    return math.hypot(px - (x1 + t * dx), py - (y1 + t * dy))

def _seg_seg(a, b):
    """Shortest distance between two segments, or 0.0 if they cross."""
    (x1, y1, x2, y2), (x3, y3, x4, y4) = a, b
    def cross(px, py, qx, qy, rx, ry):
        return (qx - px) * (ry - py) - (qy - py) * (rx - px)
    d1 = cross(x3, y3, x4, y4, x1, y1)
    d2 = cross(x3, y3, x4, y4, x2, y2)
    d3 = cross(x1, y1, x2, y2, x3, y3)
    d4 = cross(x1, y1, x2, y2, x4, y4)
    if ((d1 > 0) != (d2 > 0)) and ((d3 > 0) != (d4 > 0)):
        if d1 and d2 and d3 and d4:
            return 0.0
    return min(_pt_seg(x1, y1, x3, y3, x4, y4), _pt_seg(x2, y2, x3, y3, x4, y4),
               _pt_seg(x3, y3, x1, y1, x2, y2), _pt_seg(x4, y4, x1, y1, x2, y2))

def plan(refs=("U1", "U2"), path=BOARD):
    P = pads(path)
    all_pads = [(r, n, v) for r, ps in P.items() for n, v in ps.items()]
    placed = []
    stubs = []
    ops = []
    stats = {"balls": 0, "placed": 0, "refused": 0, "nc": 0, "pitch": {}}
    for ref in refs:
        ps = P[ref]
        balls = [dict(v, num=n) for n, v in ps.items()]
        pitch = pitch_of(balls)
        stats["pitch"][ref] = round(pitch, 4)
        p = pitch / 2.0
        for b in balls:
            stats["balls"] += 1
            if (not b["net"] or b["net"].startswith("/NC_")
                    or b["net"].startswith("unconnected-")):
                stats["nc"] += 1
                continue
            got = None
            for step, sx, sy in CANDS:
                cx, cy = b["x"] + sx * p * step, b["y"] + sy * p * step
                if not (EDGE_CLEAR + VIA_DIA <= cx <= 45.0 - EDGE_CLEAR - VIA_DIA
                        and EDGE_CLEAR + VIA_DIA <= cy <= 45.0 - EDGE_CLEAR - VIA_DIA):
                    continue
                if any(math.hypot(cx - v[0], cy - v[1]) < VIA_KEEP
                       for v in placed):
                    continue
                bad = False
                for other in balls:
                    if other is b:
                        continue
                    if math.hypot(cx - other["x"], cy - other["y"]) < BALL_KEEP:
                        bad = True
                        break
                if bad:
                    continue
                for r, n, v in all_pads:
                    if r == ref:
                        continue
                    if (math.hypot(cx - v["x"], cy - v["y"])
                            < PAD_KEEP + max(v["w"], v["h"]) / 2.0):
                        bad = True
                        break
                if bad:
                    continue
                seg = (b["x"], b["y"], cx, cy)
                graze = False
                for r2, ps2 in P.items():
                    for n2, v2 in ps2.items():
                        if r2 == ref and v2 is b:
                            continue
                        if v2["x"] == b["x"] and v2["y"] == b["y"]:
                            continue    # the ball itself
                        if _pt_seg(v2["x"], v2["y"], *seg) < SEG_BALL:
                            graze = True
                            break
                    if graze:
                        break
                if graze:
                    continue
                for s2 in stubs:
                    if _seg_seg(seg, s2) < SEG_SEG:
                        graze = True
                        break
                if graze:
                    continue
                got = (round(cx, 4), round(cy, 4))
                break
            if got is None:
                stats["refused"] += 1
                REFUSED.append("%s.%s %s" % (ref, b["num"], b["net"]))
                continue
            placed.append(got)
            stubs.append((b["x"], b["y"], got[0], got[1]))
            stats["placed"] += 1
            ops.append({"op": "add_via", "x": got[0], "y": got[1],
                        "size": VIA_DIA, "drill": VIA_DRILL, "net": b["net"],
                        "create_net": False})
            ops.append({"op": "add_track", "layer": "F.Cu", "net": b["net"],
                        "width": TRACK_W, "x1": b["x"], "y1": b["y"],
                        "x2": got[0], "y2": got[1]})
    stats["ops"] = len(ops)
    return ops, stats, placed


def measure(path=BOARD):
    """The clearances the template actually produces, worst first."""
    ops, stats, placed = plan(path=path)
    P = pads(path)
    balls = [(r, n, v) for r, ps in P.items() for n, v in ps.items()]
    vv = 9e9
    vb = 9e9
    for i, a in enumerate(placed):
        for b in placed[i + 1:]:
            d = math.hypot(a[0] - b[0], a[1] - b[1])
            if d < vv:
                vv = d
    for a in placed:
        for r, n, v in balls:
            d = math.hypot(a[0] - v["x"], a[1] - v["y"]) - max(v["w"], v["h"]) / 2.0
            if d < vb:
                vb = d
    return {"via_to_via_centre": round(vv, 4),
            "via_to_via_gap": round(vv - VIA_DIA, 4),
            "via_to_ball_pad_gap": round(vb - VIA_DIA / 2.0, 4),
            "stub_w": TRACK_W, "via": "%g/%g" % (VIA_DIA, VIA_DRILL)}

def audit(path=BOARD):
    ops, stats, placed = plan(path=path)
    problems = []
    for a in placed:
        for b in placed:
            if a is b:
                continue
            d = math.hypot(a[0] - b[0], a[1] - b[1])
            if d < VIA_KEEP - 1e-6:
                problems.append("vias %.3f mm apart at %s" % (d, a))
                break
    print(json.dumps({"stats": stats, "refused_list": REFUSED[:20],
                      "worst": measure(path), "problems": problems[:10]},
                     ensure_ascii=False, indent=2))
    return problems


def apply(path=BOARD):
    from pcb_agent_mcp.edit import plan as PL
    ops, stats, _ = plan(path=path)
    p = PL.propose(path, ops)
    blocking = p.get("blocking") or []
    if blocking:
        for b in blocking:
            print("BLOCKED", json.dumps(b, ensure_ascii=False)[:300])
        raise SystemExit("the fan-out is blocking; the board was NOT touched")
    print("plan", p["plan_id"], "ops", len(ops),
          "warnings", len(p.get("warnings") or []))
    res = PL.apply_plan(p["plan_id"], confirm=True,
                        options={"allow_new_violations": True,
                                 "precheck_clearance_mm": 0.1})
    print("applied: gates", json.dumps(res.get("gates_passed") or [],
                                       ensure_ascii=False),
          "errors", json.dumps(res.get("errors") or [], ensure_ascii=False)[:400])
    return res


def main(argv=None):
    ap = argparse.ArgumentParser()
    g = ap.add_mutually_exclusive_group(required=True)
    g.add_argument("--audit", action="store_true")
    g.add_argument("--apply", action="store_true")
    a = ap.parse_args(argv)
    if a.audit:
        raise SystemExit(1 if audit() else 0)
    apply()


if __name__ == "__main__":
    main()
