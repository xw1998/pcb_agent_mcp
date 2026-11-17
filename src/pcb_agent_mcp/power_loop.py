"""The five legs of one buck cell: what has to be copper, how wide, how long.

`decap_sites` answers "where does the bypass capacitor go". `power_loop` answers
the next question down the same rail: given a switch-mode cell already placed,
**which pad has to reach which pad**, and what does the copper between them cost.

The legs are named by what they carry, not by what they connect, because the
width and the length budget are properties of the current, not of the part:

* ``vin``  - input pad to input capacitor. Narrow current, but it closes the hot
  loop together with ``gnd``, so it is short and not thin.
* ``sw``   - switch pin to inductor. **This is the antenna.** It swings the full
  input voltage at the switching edge, so its length is a budget that a placement
  can blow and a router cannot fix; a legal route that is 13 mm long is reported
  as the placement defect it is, together with the copper that forced the detour.
* ``out``  - inductor to output capacitor, and the sense tap to the divider top.
* ``bs``   - bootstrap capacitor across switch and the switch node.
* ``fb``   - the divider node. Thin, and it cannot borrow the output current's
  copper or it stops sensing and starts measuring a voltage drop.
* ``gnd``  - every ground pad of the cell to the ground flood through its own
  stitching via. A cell whose ground pads reach the plane on the far side of the
  board is a cell with an inductor in series with its return path.

Everything here is measured before it is emitted. A leg is routed with
0/45/90/135-only runs (`decap_sites._route45`), every emitted run is **absorbed
back into the obstacle set** so the next leg has to go around it, and a leg that
cannot be closed comes back with the reason and the copper that blocked it.
Emitting a `blocked` leg is the one thing this module will not do: two pads and
nothing between them is exactly what the DRC already reports as unconnected.

Discovery is by net, not by reference: the cell is found from ``/SW_<ref>``, the
inductor as the *other* footprint on that net, and each passive by the pair of
nets it bridges. A hand-written reference list would be right for exactly one
board and silently wrong for the next revision that renames a part.
"""
import math

from . import decap_sites as D

#: Width per leg class, in mm. The switch node and both ends of the inductor
#: carry the full inductor current; the bootstrap and the divider carry
#: milliamps. Handing them one number is how a 0.2 mm sw trace happens.
_WIDTH = {"vin": 0.6, "sw": 0.6, "out": 0.6, "bs": 0.3, "fb": 0.25, "gnd": 0.4}

#: Length budget per leg, in mm, for the *reported* `over` field. These are not
#: gates: a longer leg is still copper, it is just a number the caller has to
#: look at. The one number that is a rule is `sw`, whose loop area is the
#: radiator, and even there the refusal is "this is the placement, not the run".
_TARGET = {"vin": 1.5, "sw": 2.0, "out": 2.5, "bs": 2.0, "fb": 2.0, "gnd": 1.5}

#: A leg that cannot be closed with the copper allowed here is reported, never
#: silently shortened. Kept as a module constant so the tests can pin it.
_LAYER = "F.Cu"

#: Hard limit on any run that carries the switch node. `_TARGET["sw"]` is a
#: number to look at; this one is a rule, because a switch node routed 13 mm
#: around an inductor is not a buck converter any more - it is the radiator the
#: part was chosen to avoid. A legal detour longer than this is measured and
#: reported as the placement defect it is, and **not emitted**: the caller has to
#: move a part, and a 13 mm stub on the board would only hide that.
_SW_HARD_MM = 6.0

#: How far a rail's flood may be from the pad before the connection stops being a
#: leg and starts being a bus. 1.5 mm is inside one part's own land; beyond that
#: the copper has to be drawn somewhere on purpose, and where is a decision.
_RAIL_MAX_MM = 1.5

#: Grid the detour search walks when the direct run is blocked. 0.5 mm is half a
#: 0402 land, so a corridor a part could actually be routed through is reachable.
_DETOUR_STEP_MM = 0.25

#: Step for the second-stage search. Coarser on purpose: it only runs when the
#: fine grid came back empty, and by then the question is "does any copper
#: exist", which a 0.5 mm grid answers for a third of the price.
_DETOUR_COARSE_MM = 0.5

#: How far a rail pad may run to reach a legal via of its own flood. A buck's
#: output is 1-2 mm from the plane edge on a board whose planes were drawn for
#: the module pin field; beyond a couple of millimetres the honest answer is that
#: the plane, not the leg, is what has to change.
_RAIL_REACH_MM = 2.0
_RAIL_STEP_MM = 0.25

#: How far a ground pad's stitching via is allowed to stand from the pad. Same
#: number as the decoupling module uses, and for the same reason: the loop the
#: via closes is the loop the capacitor was bought for.
_GND_REACH_MM = D._GND_ESCAPE_MM

_SW_PREFIX = "/SW_"


def _pad_xy(pad):
    return (float(pad.get("x")), float(pad.get("y")))


def _dist(a, b):
    return math.hypot(a[0] - b[0], a[1] - b[1])

def _seg_seg(a, b, c, d):
    """Distance between two segments, or 0.0 when they cross."""
    d1 = _cross(a, b, c)
    d2 = _cross(a, b, d)
    d3 = _cross(c, d, a)
    d4 = _cross(c, d, b)
    if d1 * d2 < 0 and d3 * d4 < 0:
        return 0.0
    return min(_pt_seg(a, c, d), _pt_seg(b, c, d),
               _pt_seg(c, a, b), _pt_seg(d, a, b))


def _cross(a, b, c):
    """Sign of the turn a->b->c. Used only to detect a crossing."""
    return ((b[0] - a[0]) * (c[1] - a[1])
            - (b[1] - a[1]) * (c[0] - a[0]))


def _pt_seg(p, a, b):
    """Distance from a point to a segment."""
    ax, ay = a
    bx, by = b
    dx, dy = bx - ax, by - ay
    L2 = dx * dx + dy * dy
    if L2 <= 1e-18:
        return _dist(p, a)
    t = ((p[0] - ax) * dx + (p[1] - ay) * dy) / L2
    t = 0.0 if t < 0.0 else (1.0 if t > 1.0 else t)
    return math.hypot(p[0] - (ax + t * dx), p[1] - (ay + t * dy))


def _seg_box(p, q, box):
    """Distance from a segment to an axis-aligned box; 0.0 when it touches."""
    x0, y0, x1, y1 = box
    if x0 <= p[0] <= x1 and y0 <= p[1] <= y1:
        return 0.0
    if x0 <= q[0] <= x1 and y0 <= q[1] <= y1:
        return 0.0
    ring = [(x0, y0), (x1, y0), (x1, y1), (x0, y1)]
    best = 1e9
    for i in range(4):
        d = _seg_seg(p, q, ring[i], ring[(i + 1) % 4])
        if d < best:
            best = d
        if best <= 0.0:
            return 0.0
    return best


def _clip_len(p, q, box):
    """Length of the segment pq that lies inside the box (Liang-Barsky)."""
    x0, y0, x1, y1 = box
    dx, dy = q[0] - p[0], q[1] - p[1]
    t0, t1 = 0.0, 1.0
    for pv, qv in ((-dx, p[0] - x0), (dx, x1 - p[0]),
                   (-dy, p[1] - y0), (dy, y1 - p[1])):
        if abs(pv) < 1e-15:
            if qv < 0.0:
                return 0.0
            continue
        r = qv / pv
        if pv < 0.0:
            if r > t1:
                return 0.0
            if r > t0:
                t0 = r
        else:
            if r < t0:
                return 0.0
            if r < t1:
                t1 = r
    return max(0.0, (t1 - t0) * math.hypot(dx, dy))


#: How much of a magnetic's body a run may cross where that body is not a pad.
#: Getting to an inductor's own pad means crossing its outline - the pad stands
#: under the body edge, and on this board the two pads are 1.7 x 3.7 mm each, so
#: almost the whole footprint IS pads. What is left is the 1 mm strip between
#: them and the skirt around them, and crossing that is what the number refuses:
#: 0.5 mm is one track's width of skirt, 1 mm and up is a track through the part.
_MAG_CROSS_MM = 0.5

#: Sampling step for the "inside a part and not on a pad of it" length. The
#: quantity is a length of track, so the step is the resolution of that length.
_MAG_STEP_MM = 0.05


def _cross_out_of_pads(p, q, box, pads):
    """Length of pq that lies inside `box` yet outside every pad of the part."""
    L = _dist(p, q)
    if L <= 0.0:
        return 0.0
    n = max(1, int(math.ceil(L / _MAG_STEP_MM)))
    step = L / n
    out = 0.0
    for i in range(n):
        t = (i + 0.5) / n
        x = p[0] + t * (q[0] - p[0])
        y = p[1] + t * (q[1] - p[1])
        if not (box[0] <= x <= box[2] and box[1] <= y <= box[3]):
            continue
        if any(px0 <= x <= px1 and py0 <= y <= py1
               for px0, py0, px1, py1 in pads):
            continue
        out += step
    return out


def _run_hits(run, width, boxes, clearance):
    """The first body a run crosses the middle of, or None.

    `run` is a polyline in canonical coordinates; each entry of `boxes` is
    (ref, body_box, pad_rects) for a part copper must not pass beneath. Two
    things are deliberately not counted: a track whose *edge* grazes a courtyard
    (that is what routing beside a part looks like), and a track that reaches the
    part's own pad (that pad is under the body, and the copper has to get there).
    What is left is a track through the part, which no length budget redeems.
    """
    for i in range(len(run) - 1):
        for item in boxes:
            ref, box = item[0], item[1]
            pads = item[2] if len(item) > 2 else ()
            if _cross_out_of_pads(run[i], run[i + 1], box, pads) > _MAG_CROSS_MM:
                return ref
    return None


def _pad_rects(by_ref, ref):
    """Axis-aligned extents of one footprint's pads, as (x0, y0, x1, y1)."""
    out = []
    for pad in by_ref.get(ref) or ():
        cx, cy, hx, hy = D._pad_box(pad)[:4]
        out.append((cx - hx, cy - hy, cx + hx, cy + hy))
    return out


def _refs(board):
    """{ref: [pad, ...]} and {ref: set(nets)} from one pass over the pads."""
    pads = board.pads() if callable(getattr(board, "pads", None)) else board.pads
    by_ref = {}
    for p in pads:
        by_ref.setdefault(p.get("ref"), []).append(p)
    nets = {r: set(p.get("net") for p in ps) for r, ps in by_ref.items()}
    return by_ref, nets


def _find(by_ref, nets, want, near, skip=()):
    """Nearest footprint whose nets contain every one of `want`.

    `near` is a point; the tie-break matters on this board because four cells
    share `/GND` and a divider's bottom resistor is on `/GND` too. Picking by
    "whichever comes first in the file" would put U5's divider under U7.
    """
    best = None
    for ref, have in nets.items():
        if ref in skip:
            continue
        if not all(w in have for w in want):
            continue
        pads = by_ref[ref]
        cx = sum(_pad_xy(p)[0] for p in pads) / len(pads)
        cy = sum(_pad_xy(p)[1] for p in pads) / len(pads)
        d = _dist((cx, cy), near)
        if best is None or d < best[0]:
            best = (d, ref)
    return best[1] if best else None


def _pad_on(by_ref, ref, net):
    """The pad of `ref` that is on `net` (first one; a pad count > 1 is reported)."""
    hits = [p for p in by_ref[ref] if p.get("net") == net]
    return hits


def cells(board, vin_net="/+5V_IN", gnd_net="/GND"):
    """Every buck cell on the board, keyed by its IC's reference.

    A cell is anything that has a ``/SW_`` net with a *separate* footprint on the
    far side of it: one switch pin, one inductor. The rest of the parts are found
    by the net pairs they bridge, which is also how a part that is missing (no
    bootstrap capacitor fitted, divider absent) turns into a named gap instead of
    a silent skip.
    """
    by_ref, nets = _refs(board)
    out = {}
    boxes = {}
    for name, box in D._bodies(board):
        boxes.setdefault(name, []).append(box)
    for ref in sorted(nets):
        mine = [n for n in nets[ref] if isinstance(n, str) and n.startswith(_SW_PREFIX)]
        if not mine:
            continue
        # The IC has a bootstrap leg and a feedback leg; the inductor has neither.
        bs = [n for n in nets[ref] if n.startswith("/BS_")]
        fb = [n for n in nets[ref] if n.startswith("/FB_")]
        if not (bs and fb):
            continue
        sw_net = mine[0]
        suffix = ref[1:]
        # The inductor: the footprint on the switch node whose OTHER net is
        # neither a control net of this IC nor ground. Every 0402 that hangs off
        # the switch node - the bootstrap capacitor - is caught by that filter:
        # its far side is `/BS_<ref>`. Nearest-footprint would have picked the
        # bootstrap cap on this board, because a 0402 stands closer than a 4.3 uH
        # and the plan then routed the whole inductor current through it.
        control = set(bs) | set(fb) | {gnd_net}
        cands = []
        for other in nets:
            if other == ref:
                continue
            two = [n for n in nets[other] if n != sw_net]
            if sw_net not in nets[other] or len(two) != 1:
                continue
            if two[0] in control:
                continue
            pads = [p for p in by_ref[other]]
            small = min(max(float(p.get("w") or 0), float(p.get("h") or 0))
                        for p in pads) if pads else 0.0
            cands.append((-small, other, two[0]))
        if not cands:
            out[ref] = {"error": "no footprint on %s has a far side that is not a "
                                 "control net or ground: no inductor found, and the "
                                 "bootstrap/divider parts must not be mistaken for "
                                 "one" % sw_net,
                        "sw_net": sw_net}
            continue
        cands.sort()
        ind, out_net = cands[0][1], cands[0][2]
        also = [c[1] for c in cands[1:]]
        ic_pad = _pad_on(by_ref, ref, sw_net)[0]
        near = _pad_xy(ic_pad)

        cell = {"ic": ref, "inductor": ind, "sw_net": sw_net,
                # No copper under a magnetic. The courtyard is the body, and a
                # buck's inductor sits over the loop it is filtering - which is
                # exactly why routing beneath it is the one detour that is not
                # allowed however short it is.
                "body": [(ind, b, _pad_rects(by_ref, ind))
                         for b in boxes.get(ind, [])],
                "bs_net": bs[0], "fb_net": fb[0], "out_net": out_net,
                "missing": []}
        if also:
            cell["also_on_sw"] = also
        want = {
            "cin": [vin_net, gnd_net],
            "cout": [out_net, gnd_net],
            "boot": [bs[0], sw_net],
            "rtop": [out_net, fb[0]],
            "rbot": [fb[0], gnd_net],
        }
        for role, ws in want.items():
            found = _find(by_ref, nets, ws, near, skip=(ref, ind))
            if found is None:
                cell["missing"].append({"role": role, "nets": ws})
            cell[role] = found
        cell["_suffix"] = suffix
        out[ref] = cell
    return out


def _centre(pads):
    return (sum(_pad_xy(p)[0] for p in pads) / len(pads),
            sum(_pad_xy(p)[1] for p in pads) / len(pads))


def _absorb(copper, legs, net, layer, width):
    """Put a run just emitted into the obstacle set the next leg must avoid.

    Without this a plan is four independent answers to the same question: the
    switch node would happily run through the circle the sense line already
    occupies, and the emitted ops would short. The rounding matches what the op
    carries (4 decimals), so what is checked is what gets written.
    """
    hw = width / 2.0
    for i in range(len(legs) - 1):
        a, b = legs[i], legs[i + 1]
        idx = len(copper.segs)
        copper.segs.append((net, layer, float(a[0]), float(a[1]),
                            float(b[0]), float(b[1]), hw))
        copper._add(("seg", idx, net,
                     min(a[0], b[0]) - hw, min(a[1], b[1]) - hw,
                     max(a[0], b[0]) + hw, max(a[1], b[1]) + hw))


def _absorb_via(copper, net, x, y, dia):
    r = dia / 2.0
    idx = len(copper.vias)
    copper.vias.append((net, float(x), float(y), r))
    copper._add(("via", idx, net, x - r, y - r, x + r, y + r))


def _route(copper, p, q, net, width, clearance, avoid=()):
    """One leg, as (points, clearance) or (None, why).

    `avoid` is the list of (ref, box) bodies copper may not pass beneath. It is
    checked here rather than inside `_route45` because it is a rule of this
    factory, not a property of the copper: a run under a 4.3 uH clears DRC.
    """
    run, info = D._route45(copper, p, q, net, [_LAYER], width, clearance)
    if run is None:
        return None, {"reason": "no 0/45/90/135 run keeps %.2f mm; worst was "
                                "%.3f mm to %s" % (clearance,
                                                   info.get("clearance_mm") or 0.0,
                                                   _who(info.get("blocked_by")))}
    hit = _run_hits(run, width, avoid, clearance) if avoid else None
    if hit is not None:
        return None, {"reason": "the only legal run passes under %s, and copper "
                                "does not run under a magnetic" % hit}
    return run, {"clearance_mm": info.get("clearance_mm")}


def _plane_leg(board, copper, bodies, pad_xy, net, own_ref, stub_mm, clr,
               via_dia, reach_mm=_RAIL_REACH_MM):
    """How a rail pad reaches its own flood, or how far that flood is.

    Deliberately NOT `decap_sites._connection`: that function answers for a pad
    that does not exist yet (a proposed capacitor site), so on a pad that is
    already copper of this net it finds "copper at distance -0.28" - itself. The
    question here is different: is there a flood of this net under a point I am
    allowed to drill?

    Two shapes of answer, in that order. When the flood is already under the pad
    the via goes at one stub distance, which is the cheapest copper there is.
    When it is not - the usual case on a board whose planes were drawn for the
    module's pin field and stop a few tenths short of the regulator - the answer
    is a short run to a legal via inside the flood, up to `reach_mm`.

    When neither shape answers, the refusal carries **which** rule ate every
    candidate, counted. This board has a case where the flood is 0.33 mm from
    the pad and still unreachable: every holeable point of it within reach lies
    under the inductor's body. "The nearest flood is 0.33 mm away, draw a bus"
    would be a wrong answer to that - the bus is there, and the via is what is
    forbidden. A refusal that names the wrong blocker is worse than one that
    gives a distance, because the reader acts on it.
    """
    fills = D._fills(board, net)
    if not fills:
        zones = [z for z in (getattr(board, "zones", None) or [])
                 if z.get("net") == net]
        if zones:
            layers = sorted({l for z in zones for l in (z.get("layers") or [])})
            return None, {"why": "%s has %d zone outline(s) on %s but none of them "
                                 "carries pour data: the plane was drawn and never "
                                 "filled, so there is no copper to reach"
                                 % (net, len(zones), ", ".join(layers) or "?")}
        return None, {"why": "this board defines no zone for %s at all, so the "
                             "rail has no plane to reach: closing it means a bus "
                             "across the board, which is a layout decision"
                             % net}
    vr = via_dia / 2.0
    pad = pad_xy
    blockers = {}

    def note(key, at):
        rec = blockers.setdefault(key, {"n": 0, "at": [round(at[0], 3),
                                                       round(at[1], 3)]})
        rec["n"] += 1

    if any(f.covers(pad[0], pad[1]) for f, _l in fills):
        for at in D._via_points(pad, stub_mm, None):
            hit, why = _via_here(board, bodies, copper, fills, at, net, own_ref,
                                 vr, clr, pad)
            if hit is not None:
                return hit, {}
            note(why["key"], at)
    best = None
    steps = int(round(reach_mm / _RAIL_STEP_MM))
    for fill, layers in fills:
        for d in _ESCAPE_DIRS:
            for i in range(int(round(stub_mm / _RAIL_STEP_MM)), steps + 1):
                r = i * _RAIL_STEP_MM
                at = (pad[0] + d[0] * r, pad[1] + d[1] * r)
                if not fill.covers(at[0], at[1]):
                    continue
                hit, why = _via_here(board, bodies, copper, [(fill, layers)], at,
                                     net, own_ref, vr, clr, pad)
                if hit is None:
                    note(why["key"], at)
                    continue
                if best is None or hit["length_mm"] < best["length_mm"]:
                    best = hit
    if best is not None:
        return best, {}
    # Measured from the stub point (a via may not sit in the pad) and probed
    # further than `reach_mm` on purpose: the number is "how far is this net's
    # copper", and it is the only useful reading when the answer is "too far".
    # Passing `reach_mm` as the stub distance used to report a distance from a
    # point 2 mm off the pad, which is not a distance anything is missing.
    short = D._fill_shortfall(board, net, pad, stub_mm=stub_mm,
                              probe=max(D._FILL_PROBE_MM, reach_mm))
    common = None
    if blockers:
        common = max(blockers, key=lambda k: blockers[k]["n"])
    if common is not None:
        return None, {"why": _plane_refusal(net, common, blockers, short,
                                            reach_mm),
                      "plane": {"blocked_by": common,
                                "candidates": sum(b["n"] for b in blockers.values()),
                                "blockers": blockers,
                                "reach_mm": reach_mm},
                      "fill_shortfall": short or None}
    if short.get("distance_mm") is None:
        return None, {"why": "no flood of %s within %.1f mm"
                             % (net, short.get("probe_mm") or 0.0),
                      "fill_shortfall": short}
    return None, {"why": "the nearest %s flood is %.2f mm away: a bus has to be "
                         "drawn there, and that is a layout decision, not a leg"
                         % (net, short["distance_mm"]),
                  "fill_shortfall": short}


def _plane_refusal(net, key, blockers, short, reach_mm=0.0):
    """One sentence for the rule that refused every in-flood candidate."""
    n = blockers[key]["n"]
    where = blockers[key]["at"]
    far = (short or {}).get("distance_mm")
    far = ("%.2f mm apart" % far) if far is not None else "just out of reach"
    if key.startswith("body:"):
        ref = key.split(":", 1)[1]
        return ("the nearest %s flood is %s but every point of it within %.1f mm "
                "of this pad is under %s's body: a via may not be drilled there, "
                "so closing this rail is a plane change or a placement change, "
                "not a leg" % (net, far, reach_mm, ref))
    if key == "clearance":
        return ("%d point(s) of this rail's flood are within %.1f mm but none "
                "keeps %.2f mm from other copper (closest candidate at %s)"
                % (n, reach_mm, D._CLEARANCE_MM, where))
    if key == "no_run":
        return ("this rail's flood is within %.1f mm but no 0/45/90/135 run from "
                "the pad keeps %.2f mm to get there (closest candidate at %s)"
                % (reach_mm, D._CLEARANCE_MM, where))
    if key == "in_pad":
        return ("the only flood points within %.1f mm sit inside another pad's "
                "copper, and a via there is a hole in a pad" % reach_mm)
    return ("no point of this rail's flood within %.1f mm takes a via (%s)"
            % (reach_mm, key))


def _via_here(board, bodies, copper, fills, at, net, own_ref, vr, clr, pad):
    """The run and via record if a via may stand here, else why it may not.

    Returns `(hit, why)` with exactly one non-None. `why["key"]` is the rule
    that refused, because the refusal the caller prints has to name it:
    descending from "no via here" to "draw a bus" hides a body rule inside a
    distance.
    """
    if not any(f.covers(at[0], at[1]) for f, _l in fills):
        return None, {"key": "outside", "text": "not in the fill"}
    bad = D._via_legal(board, bodies, at[0], at[1], own_ref, margin_mm=vr + clr)
    if bad:
        ref = bad.split("is inside ")[-1].split("'s ")[0]
        return None, {"key": "body:%s" % ref, "text": bad}
    # A through via carries copper on EVERY layer of the stackup, so its
    # annulus has to clear every layer - not just the one this plane is drawn
    # on. Asking only the plane's layer is how a hole was planned straight
    # through an In2 data line: the fill answered "clear" (-0.155 mm to
    # /LCDC_CLK on In2.Cu) because the track was on a layer nobody asked about,
    # and the pre-check caught it only after the copper existed.
    stack = _stack_layers(board)
    reach = max(1.2, vr + clr + 0.9)
    d, who = D._clearance_at(copper, at[0], at[1], net, stack, reach=reach)
    if d < vr + clr:
        return None, {"key": "clearance",
                      "text": "the annulus has %.3f mm to %s on %s"
                              % (d, _who(who), _who_layer(copper, who))}
    layers = sorted({l for _f, ls in fills for l in ls})
    if D._inside_pad(copper, at[0], at[1], own=(net,)):
        return None, {"key": "in_pad", "text": "inside a pad"}
    run, why = _route(copper, pad, at, net, _WIDTH["out"], clr)
    if run is None:
        return None, {"key": "no_run", "text": why.get("reason")}
    return {"via_at": [round(at[0], 4), round(at[1], 4)],
            "run": [[round(a, 4), round(b, 4)] for a, b in run],
            "length_mm": round(_plen(run), 4),
            "clearance_mm": why.get("clearance_mm"),
            "layers": layers}, None


def _plen(run):
    return sum(_dist(run[i], run[i + 1]) for i in range(len(run) - 1))


def _grid_best(copper, p, q, net, width, clearance, margin_mm, step_mm,
               avoid=()):
    """Shortest legal "run through one waypoint" over a grid covering p and q.

    Returns (length_mm, run, waypoint) or None. The waypoint has to be grid
    aligned, which is why this is a search and not a formula: on this board the
    only legal corridor between the buck IC and its divider is 0.41 mm tall, and
    a grid that misses it by 0.07 mm reports "no route" for copper that plainly
    exists. False negatives are wrong answers too, so the caller widens the grid
    instead of trusting a single step size.
    """
    x0 = min(p[0], q[0]) - margin_mm
    x1 = max(p[0], q[0]) + margin_mm
    y0 = min(p[1], q[1]) - margin_mm
    y1 = max(p[1], q[1]) + margin_mm
    best = None
    ny = int(round((y1 - y0) / step_mm))
    nx = int(round((x1 - x0) / step_mm))
    for iy in range(ny + 1):
        for ix in range(nx + 1):
            w = (x0 + ix * step_mm, y0 + iy * step_mm)
            # No run through w can be shorter than p->w->q, so once a candidate
            # exists every point outside that ellipse is answered by arithmetic.
            # On the board this is the difference between 3 s and 60 s per
            # blocked leg: the corridor that works is a handful of points, and
            # the other 1,600 are the ones the bound skips.
            lb = _dist(p, w) + _dist(w, q)
            if best is not None and lb >= best[0]:
                continue
            a, _ = _route(copper, p, w, net, width, clearance, avoid)
            if a is None:
                continue
            b, _ = _route(copper, w, q, net, width, clearance, avoid)
            if b is None:
                continue
            run = a + b[1:]
            L = _plen(run)
            if best is None or L < best[0]:
                best = (L, run, w)
    return best


#: How far a route may step away from a pin before the waypoint search starts
#: looking around the corner. `_route45` only offers "straight, or one 45
#: chamfer at one end", and both chamfers bend *towards* the far end. A pin
#: whose far end lies across its own package therefore has no legal chamfer at
#: all - not because copper is missing, but because the two shapes it is allowed
#: to draw both cut the corner off the IC. Stepping out first is what a person
#: does, and it is cheap to enumerate: eight directions, two lengths.
_ESCAPE_MM = (0.8, 1.6)
_ESCAPE_DIRS = ((1, 0), (-1, 0), (0, 1), (0, -1),
                (0.7071, 0.7071), (0.7071, -0.7071),
                (-0.7071, 0.7071), (-0.7071, -0.7071))


def _escape_points(copper, p, net, width, clearance, avoid=()):
    """Points one short legal straight stub away from p, farthest first."""
    out = []
    for d in _ESCAPE_DIRS:
        for L in _ESCAPE_MM:
            q = (p[0] + d[0] * L, p[1] + d[1] * L)
            dist, _ = D._run_clear(copper, p, q, net, [_LAYER], width, clearance)
            if dist >= clearance and (not avoid
                                      or _run_hits([p, q], width, avoid,
                                                   clearance) is None):
                out.append((L, q))
    out.sort(key=lambda t: -t[0])
    return out


def _route_any(copper, p, q, net, width, clearance, margin_mm=3.0,
               avoid=()):
    """Direct run, or the shortest legal run through one waypoint - stepping out
    of a pin's own shadow first if the chamfer has nowhere to go.

    The direct run is asked first because it is the one you want. When it is
    blocked the answer is not "no route": on this board the bootstrap capacitor's
    switch-side pad sits on the far side of the buck IC from the switch node, and
    there IS copper that closes it - around the IC. Whether that copper is usable
    is a length question, not an existence question, and the two are answered
    here separately: `run` says the copper exists, `length_mm` says what it costs.

    `search` in the info records which stage found it, because "shortest found"
    is not "shortest possible" and the difference has to be readable.
    """
    run, info = _route(copper, p, q, net, width, clearance, avoid)
    if run is not None:
        return run, info, None
    best = _grid_best(copper, p, q, net, width, clearance,
                      margin_mm, _DETOUR_STEP_MM, avoid)
    if best is not None:
        return best[1], {"clearance_mm": None, "detour": True,
                         "search": "grid %.2f" % _DETOUR_STEP_MM,
                         "via_waypoint": [round(best[2][0], 4),
                                          round(best[2][1], 4)]}, best[2]
    # The chamfer is pinned. Step out of the pin's shadow from either end and
    # let the same grid search run from there.
    coarse = _DETOUR_COARSE_MM
    cands = []
    for stub, p2 in _escape_points(copper, p, net, width, clearance, avoid):
        sub = _grid_best(copper, p2, q, net, width, clearance, margin_mm, coarse,
                         avoid)
        if sub is not None:
            cands.append((stub + sub[0], [p, p2] + sub[1][1:], sub[2]))
    if not cands:
        # Only pay for the far end when the near end had nothing: this is a
        # fallback, not a tie-break, and each side costs a full grid sweep.
        for stub, q2 in _escape_points(copper, q, net, width, clearance, avoid):
            sub = _grid_best(copper, p, q2, net, width, clearance, margin_mm,
                             coarse, avoid)
            if sub is not None:
                cands.append((stub + sub[0], sub[1] + [q], sub[2]))
    if cands:
        cands.sort(key=lambda t: t[0])
        best = cands[0]
    if best is None:
        return None, info, None
    return best[1], {"clearance_mm": None, "detour": True,
                     "search": "escape+grid %.2f" % coarse,
                     "via_waypoint": [round(best[2][0], 4),
                                      round(best[2][1], 4)]}, best[2]


def _stack_layers(board):
    """Every copper layer of this board, which is what a through via touches."""
    got = getattr(board, "_pl_stack_layers", None)
    if got is None:
        got = D._layers_of(board)
        try:
            board._pl_stack_layers = got
        except Exception:
            pass
    return got

def _who_layer(copper, who):
    """The layer a `_clearance_at` hit sits on, for a refusal a human can act on."""
    if not who:
        return "the stackup"
    kind, i, _net = who
    try:
        if kind == "seg":
            return copper.segs[i][1]
        if kind == "pad":
            las = (copper.pads[i][3].get("layers") or [None])
            return las[0] or "the stackup"
    except Exception:
        pass
    return "every layer it passes through"

def _who(who):
    if not who:
        return "copper (unnamed)"
    kind, i, net = who
    return "%s of %s" % (kind, net)


def _leg(legs, name, kind, p, q, net, note=None):
    """Append one leg descriptor; the caller routes it in a fixed order."""
    legs.append({"name": name, "kind": kind, "net": net,
                 "from": [round(p[0], 4), round(p[1], 4)],
                 "to": [round(q[0], 4), round(q[1], 4)],
                 "width_mm": _WIDTH[kind], "target_mm": _TARGET[kind],
                 # The floor under any routing: the straight line between the
                 # two pads. It is the number that says "no router can fix this"
                 # without a search, and the one a placement has to change.
                 "straight_mm": round(_dist((p[0], p[1]), (q[0], q[1])), 4),
                 "note": note})


def _legs_for(cell, by_ref, vin_net, gnd_net):
    """The legs of one cell, in the order they must be routed.

    Order is not cosmetic: `sw` is routed before `out` so the output rail has to
    go around the switch node rather than the other way round, and the divider is
    routed last because it is the one that is allowed to be thin and long.
    """
    ic, ind = cell["ic"], cell["inductor"]
    legs = []
    ic_vin = _pad_on(by_ref, ic, vin_net)
    if len(ic_vin) > 1:
        # Two input pins, 0.95 mm apart: tie them together first, or the second
        # one is a pad the plan never mentions.
        a, b = sorted(ic_vin, key=lambda p: _pad_xy(p)[1])
        _leg(legs, "vin_ic", "vin", _pad_xy(a), _pad_xy(b), vin_net,
             note="tie the two input pins")
    if cell.get("cin") and ic_vin:
        ref = cell["cin"]
        cin_p = _pad_on(by_ref, ref, vin_net)
        if cin_p:
            # Both ends are a choice: the IC has two input pins and the capacitor
            # has two pads on this rail. The pair that is closest is the one whose
            # copper is short; taking "the first pad of each" put a 0.6 mm run
            # across the capacitor's own ground land.
            best = min(((a, c) for a in ic_vin for c in cin_p),
                       key=lambda t: _dist(_pad_xy(t[0]), _pad_xy(t[1])))
            _leg(legs, "vin_cap", "vin", _pad_xy(best[0]), _pad_xy(best[1]),
                 vin_net, note="input capacitor")
    sw_ic = _pad_on(by_ref, ic, cell["sw_net"])
    sw_l = _pad_on(by_ref, ind, cell["sw_net"])
    if sw_ic and sw_l:
        _leg(legs, "sw", "sw", _pad_xy(sw_ic[0]), _pad_xy(sw_l[0]),
             cell["sw_net"], note="the switch node - loop area is the radiator")
    l_out = _pad_on(by_ref, ind, cell["out_net"])
    if l_out and cell.get("cout"):
        c_out = _pad_on(by_ref, cell["cout"], cell["out_net"])
        if c_out:
            near = min(c_out, key=lambda p: _dist(_pad_xy(p), _pad_xy(l_out[0])))
            _leg(legs, "out", "out", _pad_xy(l_out[0]), _pad_xy(near),
                 cell["out_net"], note="inductor to output capacitor")
    if l_out and cell.get("rtop"):
        tap = _pad_on(by_ref, cell["rtop"], cell["out_net"])
        if tap:
            # Sense at the output capacitor, not at the inductor. Both pads carry
            # the same net, so this is not an electrical question - it is the one
            # the divider asks: the voltage the loop regulates is the one the load
            # sees, and the inductor's own pad is inside the magnetic, where the
            # only legal way out is through the gap between its two pads. That
            # gap is 1 mm of copper under a 4.3 uH - the last detour - and taking
            # the tap at the cap removes the question instead of answering it.
            src = _pad_on(by_ref, cell["cout"], cell["out_net"]) \
                if cell.get("cout") else []
            start = min(src, key=lambda p: _dist(_pad_xy(p), _pad_xy(tap[0]))) \
                if src else l_out[0]
            _leg(legs, "sense", "fb", _pad_xy(start), _pad_xy(tap[0]),
                 cell["out_net"],
                 note="output sense tap at the divider top, taken at the output "
                      "capacitor")
    bs_ic = _pad_on(by_ref, ic, cell["bs_net"])
    if bs_ic and cell.get("boot"):
        bp = _pad_on(by_ref, cell["boot"], cell["bs_net"])
        if bp:
            _leg(legs, "boot_bs", "bs", _pad_xy(bs_ic[0]), _pad_xy(bp[0]),
                 cell["bs_net"], note="bootstrap capacitor")
    if cell.get("boot") and sw_l:
        bp = _pad_on(by_ref, cell["boot"], cell["sw_net"])
        if bp:
            _leg(legs, "boot_sw", "bs", _pad_xy(bp[0]), _pad_xy(sw_l[0]),
                 cell["sw_net"], note="bootstrap return to the switch node")
    fb_ic = _pad_on(by_ref, ic, cell["fb_net"])
    if fb_ic and cell.get("rtop"):
        r1 = _pad_on(by_ref, cell["rtop"], cell["fb_net"])
        if r1:
            _leg(legs, "fb_top", "fb", _pad_xy(fb_ic[0]), _pad_xy(r1[0]),
                 cell["fb_net"], note="feedback pin to divider top")
            if cell.get("rbot"):
                r2 = _pad_on(by_ref, cell["rbot"], cell["fb_net"])
                if r2:
                    _leg(legs, "fb_div", "fb", _pad_xy(r1[0]), _pad_xy(r2[0]),
                         cell["fb_net"], note="divider")
    return legs


def _emit(out, view, leg, run, why, ref):
    """Record one closed leg and the ops that make it copper."""
    length = sum(_dist(run[i], run[i + 1]) for i in range(len(run) - 1))
    rec = dict(leg)
    rec.update({"status": "closed", "run": [[round(a, 4), round(b, 4)]
                                           for a, b in run],
                "length_mm": round(length, 4),
                "clearance_mm": why.get("clearance_mm"),
                "over_mm": round(length - leg["target_mm"], 4)})
    if why.get("detour"):
        rec["detour_via"] = why["via_waypoint"]
    view["legs"][leg["name"]] = rec
    view["length_mm"] = round(view["length_mm"] + length, 4)
    for i in range(len(run) - 1):
        out["ops"].append({"op": "add_track",
                           "x1": round(run[i][0], 4), "y1": round(run[i][1], 4),
                           "x2": round(run[i + 1][0], 4),
                           "y2": round(run[i + 1][1], 4),
                           "width": leg["width_mm"], "layer": _LAYER,
                           "net": leg["net"]})
    return rec


def _power_leg(copper, out, view, leg, clr, ref, avoid=()):
    """One leg of the cell. Returns 'closed' / 'blocked' / 'over_budget'."""
    p = tuple(leg["from"])
    q = tuple(leg["to"])
    run, why, _wp = _route_any(copper, p, q, leg["net"], leg["width_mm"], clr,
                               avoid=avoid)
    if run is None:
        rec = dict(leg)
        rec.update({"status": "blocked", "why": why["reason"]})
        view["legs"][leg["name"]] = rec
        out["refused"].append({"cell": ref, "leg": leg["name"],
                               "reason": why["reason"], "from": leg["from"],
                               "to": leg["to"]})
        return "blocked"
    length = sum(_dist(run[i], run[i + 1]) for i in range(len(run) - 1))
    # The switch node's hard budget: real copper, unusable copper.
    if leg["net"].startswith(_SW_PREFIX) and length > _SW_HARD_MM:
        rec = dict(leg)
        rec.update({"status": "over_budget", "legal_mm": round(length, 4),
                    "budget_mm": _SW_HARD_MM,
                    "why": "a legal run exists but is %.2f mm long (budget %.2f): "
                           "the parts are placed so the switch node has to go "
                           "around them, and that is a placement change, not a "
                           "route" % (length, _SW_HARD_MM)})
        view["legs"][leg["name"]] = rec
        out["refused"].append({"cell": ref, "leg": leg["name"],
                               "reason": rec["why"], "legal_mm": round(length, 4)})
        return "over_budget"
    _emit(out, view, leg, run, why, ref)
    _absorb(copper, run, leg["net"], _LAYER, leg["width_mm"])
    return "closed"


def _ground_legs(board, copper, bodies, out, view, cell, by_ref, ref, gnd_net,
                 clr, stub_mm, via_dia, gnd_reach_mm, width):
    """Every ground pad of the cell gets its own stitching via into the flood."""
    for role in ("ic", "cin", "cout", "rbot", "boot"):
        if role == "ic":
            gpads = _pad_on(by_ref, ref, gnd_net)
            own = ref
        elif cell.get(role):
            gpads = _pad_on(by_ref, cell[role], gnd_net)
            own = cell[role]
        else:
            gpads = []
            own = None
        for gp in gpads:
            key = "gnd_%s_%s" % (role, gp.get("number"))
            got, why = D._gnd_escape(
                board, copper, _pad_xy(gp), stem=stub_mm, clearance_mm=clr,
                via_dia=via_dia, reach_mm=gnd_reach_mm, layers=None,
                bodies=bodies, own_ref=own, net=gnd_net, track_mm=width)
            rec = {"name": key, "kind": "gnd", "net": gnd_net,
                   "from": [round(_pad_xy(gp)[0], 4), round(_pad_xy(gp)[1], 4)],
                   "width_mm": width, "target_mm": _TARGET["gnd"]}
            if got is None:
                rec.update({"status": "blocked", "why": why.get("why")})
                view["legs"][key] = rec
                out["refused"].append({"cell": ref, "leg": key,
                                       "reason": why.get("why")})
                continue
            length = got["length_mm"]
            rec.update({"status": "closed", "via_at": got["via_at"],
                        "run": got["run"], "length_mm": length,
                        "clearance_mm": got["clearance_mm"],
                        "over_mm": round(length - _TARGET["gnd"], 4)})
            view["legs"][key] = rec
            view["length_mm"] = round(view["length_mm"] + length, 4)
            pts = got["run"]
            for i in range(len(pts) - 1):
                out["ops"].append({"op": "add_track",
                                   "x1": pts[i][0], "y1": pts[i][1],
                                   "x2": pts[i + 1][0], "y2": pts[i + 1][1],
                                   "width": width, "layer": _LAYER,
                                   "net": gnd_net})
            _absorb(copper, pts, gnd_net, _LAYER, width)
            out["ops"].append({"op": "add_via",
                               "x": got["via_at"][0], "y": got["via_at"][1],
                               "size": via_dia, "drill": D._VIA_DRILL_MM,
                               "net": gnd_net})
            _absorb_via(copper, gnd_net, got["via_at"][0], got["via_at"][1],
                        via_dia)


def _rail(board, copper, bodies, out, view, cell, by_ref, ref, net, part, clr,
          stub_mm, via_dia):
    """Connect one pad of a cell to its rail's flood (or say how far that is)."""
    key = "vin_rail" if part == "cin" else "out_rail"
    if not cell.get(part):
        out["refused"].append({"cell": ref, "leg": key,
                               "reason": "no %s found, so nothing to connect the "
                                         "rail through" % part})
        return "blocked"
    pads = _pad_on(by_ref, cell[part], net)
    if not pads:
        out["refused"].append({"cell": ref, "leg": key,
                               "reason": "%s has no pad on %s"
                                         % (cell[part], net)})
        return "blocked"
    pad = _pad_xy(pads[0])
    got, why = _plane_leg(board, copper, bodies, pad, net, cell[part], stub_mm,
                          clr, via_dia)
    rec = {"name": key, "kind": "rail", "net": net, "part": cell[part],
           "from": [round(pad[0], 4), round(pad[1], 4)],
           "width_mm": _WIDTH["out"], "target_mm": _RAIL_MAX_MM}
    if got is None:
        rec.update({"status": "blocked", "why": why.get("why")})
        for k in ("plane", "fill_shortfall"):
            if why.get(k):
                rec[k] = why[k]
        view["legs"][key] = rec
        item = {"cell": ref, "leg": key, "reason": why.get("why")}
        for k in ("plane", "fill_shortfall"):
            if why.get(k):
                item[k] = why[k]
        out["refused"].append(item)
        return "blocked"
    rec.update({"status": "closed", "via_at": got["via_at"], "run": got["run"],
                "length_mm": got["length_mm"],
                "clearance_mm": got.get("clearance_mm"),
                "over_mm": round(got["length_mm"] - _RAIL_MAX_MM, 4)})
    view["legs"][key] = rec
    view["length_mm"] = round(view["length_mm"] + got["length_mm"], 4)
    pts = got["run"]
    for i in range(len(pts) - 1):
        out["ops"].append({"op": "add_track",
                           "x1": pts[i][0], "y1": pts[i][1],
                           "x2": pts[i + 1][0], "y2": pts[i + 1][1],
                           "width": _WIDTH["out"], "layer": _LAYER, "net": net})
    _absorb(copper, pts, net, _LAYER, _WIDTH["out"])
    out["ops"].append({"op": "add_via", "x": got["via_at"][0],
                       "y": got["via_at"][1], "size": via_dia,
                       "drill": D._VIA_DRILL_MM, "net": net})
    _absorb_via(copper, net, got["via_at"][0], got["via_at"][1], via_dia)
    return "closed"


def plan(board, *, vin_net="/+5V_IN", gnd_net="/GND", nets=None,
         clearance_mm=None, width=None, gnd_reach_mm=_GND_REACH_MM,
         stub_mm=D._STUB_MM, via_dia=D._VIA_DIA_MM):
    """Plan every leg of every cell; return the copper that closes them.

    `ops` is what to hand to the edit layer. A leg that did not close is not in
    `ops` and appears in `refused` with the number and the copper that refused
    it - a plan that emits a half leg is a plan that produces two more
    unconnected pads, which is the defect the caller is trying to remove.

    Routing order inside a cell is part of the answer, not an implementation
    detail: **ground stitching goes before the sense line**. The divider tap is a
    thin line that has to cross the middle of the cell; if it is laid first it
    takes the only spot the IC's own ground via had, and the via is the leg that
    cannot be moved elsewhere.
    """
    clr = D._CLEARANCE_MM if clearance_mm is None else float(clearance_mm)
    wid = dict(_WIDTH)
    if width:
        for k, v in width.items():
            if k in wid:
                wid[k] = float(v)
    copper = D._Copper(board)
    bodies = D._bodies(board)
    found = cells(board, vin_net=vin_net, gnd_net=gnd_net)
    order = [r for r in sorted(found)
             if nets is None or found[r].get("out_net") in nets
             or found[r].get("sw_net") in nets]
    by_ref, _nets = _refs(board)
    out = {"cells": {}, "ops": [], "refused": [], "rules": {}, "summary": {}}
    total = closed = 0
    for ref in order:
        cell = found[ref]
        if "error" in cell:
            out["cells"][ref] = {"error": cell["error"]}
            out["refused"].append({"cell": ref, "leg": None,
                                   "reason": cell["error"]})
            continue
        view = {"ic": ref, "inductor": cell["inductor"],
                "sw_net": cell["sw_net"], "out_net": cell["out_net"],
                "parts": {k: cell.get(k) for k in
                          ("cin", "cout", "boot", "rtop", "rbot")},
                "missing": cell["missing"], "legs": {}, "length_mm": 0.0}
        if cell.get("also_on_sw"):
            view["also_on_sw"] = cell["also_on_sw"]
        for m in cell["missing"]:
            out["refused"].append({"cell": ref, "leg": m["role"],
                                   "reason": "no footprint bridges %s"
                                             % " and ".join(m["nets"])})
        legs = _legs_for(cell, by_ref, vin_net, gnd_net)
        avoid = cell.get("body") or ()
        power = [l for l in legs if l["kind"] != "fb"]
        # Signal legs go shortest first. The divider tap and the feedback pin sit
        # 0.9 and 1.6 mm apart; the output sense line is a 10 mm run across the
        # cell. Routing the long one first hands it the only corridor and turns
        # the two short ones into 6-7 mm doglegs - the same mistake as routing the
        # sense line before the ground vias, one layer down: the leg that can go
        # elsewhere has to be the one that goes elsewhere.
        signal = sorted((l for l in legs if l["kind"] == "fb"),
                        key=lambda l: _dist(tuple(l["from"]), tuple(l["to"])))
        for leg in power:
            total += 1
            if _power_leg(copper, out, view, leg, clr, ref, avoid) == "closed":
                closed += 1
        for part, net, key in (("cin", vin_net, "vin_rail"),
                               ("cout", cell["out_net"], "out_rail")):
            total += 1
            if _rail(board, copper, bodies, out, view, cell, by_ref, ref, net,
                     part, clr, stub_mm, via_dia) == "closed":
                closed += 1
        _ground_legs(board, copper, bodies, out, view, cell, by_ref, ref,
                     gnd_net, clr, stub_mm, via_dia, gnd_reach_mm, wid["gnd"])
        for l in view["legs"].values():
            if l.get("kind") == "gnd":
                total += 1
                if l.get("status") == "closed":
                    closed += 1
        for leg in signal:
            total += 1
            if _power_leg(copper, out, view, leg, clr, ref, avoid) == "closed":
                closed += 1
        view["closed"] = sum(1 for l in view["legs"].values()
                             if l.get("status") == "closed")
        view["blocked"] = sum(1 for l in view["legs"].values()
                              if l.get("status") in ("blocked", "over_budget"))
        notes = []
        sw = view["legs"].get("sw")
        if sw and sw.get("status") == "closed" and sw["length_mm"] > _TARGET["sw"]:
            notes.append(
                "the switch node is %.2f mm, over the %.2f mm budget: the inductor "
                "sits that far from the switch pin, and no router shortens a loop "
                "- this is a placement change, not a routing one"
                % (sw["length_mm"], _TARGET["sw"]))
        sense = view["legs"].get("sense")
        if (sense and sense.get("status") == "closed"
                and sense["length_mm"] > sense["target_mm"] * 2.0):
            # The tap is taken at the output capacitor on purpose (sensing at the
            # inductor's pad would have to leave through the gap between the
            # winding's own pads), so the length is the divider's distance from
            # the cap, not a routing choice. Quoting it as a routing failure
            # would send the reader to the router for a placement problem.
            notes.append(
                "the output sense line is %.2f mm against a %.2f mm target, and "
                "%.2f mm of that is the straight gap between the divider tap and "
                "the output capacitor: the tap belongs at the cap, so the length "
                "is where the divider sits"
                % (sense["length_mm"], sense["target_mm"], sense["straight_mm"]))
        if notes:
            view["placement_note"] = "; ".join(notes)
        out["cells"][ref] = view
    out["rules"] = {"width_mm": wid, "target_mm": dict(_TARGET),
                    "clearance_mm": clr, "layer": _LAYER,
                    "stub_mm": stub_mm, "via_dia_mm": via_dia,
                    "gnd_reach_mm": gnd_reach_mm, "sw_hard_mm": _SW_HARD_MM,
                    "note": "target is a length to look at, not a gate; the rules "
                            "stated as rules are (1) a switch-node run over %.2f mm "
                            "is not emitted and is a placement defect, (2) a leg "
                            "that cannot be closed is refused with its blocker"
                            % _SW_HARD_MM}
    out["summary"] = {"cells": len(out["cells"]),
                      "cells_with_missing_parts": sum(
                          1 for c in out["cells"].values() if c.get("missing")),
                      "legs": total, "closed": closed,
                      "blocked": total - closed,
                      "tracks": sum(1 for o in out["ops"] if o["op"] == "add_track"),
                      "vias": sum(1 for o in out["ops"] if o["op"] == "add_via")}
    return out
