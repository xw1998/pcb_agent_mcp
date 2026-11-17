"""Ground stitching: every layer change needs a return path, so give it one.

A signal that changes layer has to change its reference too, or the return
current has no way across: it detours to the nearest stitching via (a loop), or
it does not go at all (an antenna). The rule is one line in every guide a person
follows - "add a ground via beside the signal via" - and until now this router
ignored it entirely, because it routes each net on its own and never asks what
the current comes back through.

What this pass does: for every via that carries a signal, find a legal spot for a
ground via within `GROUND_REACH_MM` and put one there.

Three things it refuses to do, all of them on purpose:

* **Invent a ground.** If the board has no ground net, or more than one net that
  could be the ground (GND and AGND, say), nothing is stitched and the reason is
  the answer. Picking one would be a guess about the stackup.
* **Stitch into bare laminate.** A ground via helps only if ground copper exists
  on BOTH sides of the layer change - a via into nothing is a stub that changes
  no current path, and one placed 1 mm short of the pour is a floating island
  DRC reports as unconnected. So the via lands ON ground copper: its position is
  taken from the copper itself (the nearest point on a ground track's centreline,
  a ground pad's centre, another ground via), and only a pour is allowed to be
  "near" in the loose sense - if the pour already covers the signal via, a ring
  of nearby spots is probed and the first one still inside the pour is used.
  When only one side has ground the answer says `ground_not_on_two_layers` with
  the layers it found.
* **Stack vias on top of each other.** A ground via already within
  `ALREADY_MM` of the signal via is the stitch; adding a second one there is
  copper with no job.

The vias it adds are ordinary vias on the ground net, written the same way the
router writes its own, with `role: "ground_stitch"` in the op so a reader of the
op list can tell them from routed ones.
"""

import math

from . import rules as R

# How far a stitching via may sit from the signal via it serves. 2 mm is about a
# tenth of a wavelength at the frequencies a hand-routed board is checked at, and
# it is far enough that a legal spot usually exists.
GROUND_REACH_MM = 2.0
# A ground VIA this close already IS the stitch.
ALREADY_MM = 0.6
# Rings to probe when the ground is a pour that already covers the signal via,
# nearest first. Only used in that case: everywhere else the position comes from
# the copper itself, which is the only way to be sure the via lands on it.
RINGS_MM = (0.6, 0.8, 1.0, 1.2, 1.4, 1.6, 1.8)
_ANGLES = tuple((math.cos(2 * math.pi * k / 8.0), math.sin(2 * math.pi * k / 8.0))
                for k in range(8))

_GROUND_NAMES = ("GND", "GROUND", "VSS", "0V", "GNDD", "GNDPWR")


def _looks_ground(name):
    """Is this net name a ground?

    KiCad spells every net with its hierarchy in the name - `/GND`, `/PWR/GND` -
    so the test has to look at the last path segment. It read the whole string
    until this was found on the RK3506 board, which meant `ground_net()`
    answered `no_ground_net` on every board parsed off disk: the pass was dead
    on real boards while its tests (all of them written with a plain `GND`)
    stayed green.
    """
    if not isinstance(name, str):
        return False
    leaf = name.strip().split("/")[-1]
    up = leaf.upper().replace("_", "").replace("-", "").strip()
    return up in _GROUND_NAMES


def ground_net(board, bundle, results):
    """`(name, None)` for the board's ground, or `(None, reason)`.

    Exactly one candidate or nothing: a board with GND and AGND is two different
    return networks, and a stitching via on the wrong one is a defect that no DRC
    will flag.
    """
    names = set()
    for src in ((bundle.get("per_net") or {}).keys(),
                (results or {}).keys(),
                (board.nets or {}).values()):
        names.update(src)
    cands = sorted(set(n for n in names if _looks_ground(n)))
    if not cands:
        return None, {"reason": "no_ground_net",
                      "note": "the net list carries no GND/VSS/0V name, so there is "
                              "nothing to stitch to; name the net and pass it "
                              "explicitly if the board calls its ground something "
                              "else"}
    if len(cands) > 1:
        return None, {"reason": "ambiguous_ground_net", "candidates": cands,
                      "note": "more than one net looks like a ground (GND and AGND "
                              "are different return networks); pass the one you "
                              "mean instead of letting this pick"}
    return cands[0], None


def stitch(em, bundle, results, net=None, reach_mm=GROUND_REACH_MM):
    """Add a ground via beside every signal via that changes layer."""
    out = {"ground_net": None, "attempted": 0, "added": [], "skipped": [],
           "status": "not_attempted"}
    if net:
        gnd = net
    else:
        gnd, why = ground_net(em.board, bundle, results)
        if gnd is None:
            out.update({"status": "refused", "why": why})
            return out
    out["ground_net"] = gnd
    spec = R.for_net(bundle, gnd)
    dia = float(spec["via_diameter_mm"])
    drill = float(spec["via_drill_mm"])

    vias = [o for o in em.ops if o.get("op") == "add_via" and o.get("net") != gnd]
    out["attempted"] = len(vias)
    if not vias:
        out["status"] = "nothing_to_stitch"
        return out

    for op in vias:
        x, y = float(op["x"]), float(op["y"])
        sig = op.get("net")
        have = em.grid.copper_layers_near(x, y, gnd, reach_mm)
        if len(have) < 2:
            out["skipped"].append({"net": sig, "at": [round(x, 4), round(y, 4)],
                                   "reason": "ground_not_on_two_layers",
                                   "layers_with_ground": have})
            continue
        if _ground_via_near(em, gnd, x, y):
            out["skipped"].append({"net": sig, "at": [round(x, 4), round(y, 4)],
                                   "reason": "already_stitched"})
            continue
        spot = _spot(em, gnd, x, y, reach_mm)
        if spot is None:
            out["skipped"].append({"net": sig, "at": [round(x, 4), round(y, 4)],
                                   "reason": "no_legal_spot",
                                   "rings_tried_mm": list(RINGS_MM)})
            continue
        px, py, layers = spot
        em.grid.add_via(gnd, px, py, dia)
        em.ops.append({"op": "add_via", "x": round(px, 6), "y": round(py, 6),
                       "net": gnd, "size": spec["via_diameter_mm"],
                       "drill": spec["via_drill_mm"],
                       "role": "ground_stitch", "for_net": sig})
        out["added"].append({"net": sig, "at": [round(px, 4), round(py, 4)],
                             "distance_mm": round(math.hypot(px - x, py - y), 4),
                             "layers_with_ground": layers})
    if gnd in results:
        results[gnd].setdefault("stitch_vias", 0)
        results[gnd]["stitch_vias"] = results[gnd].get("stitch_vias", 0) + \
            len(out["added"])
    out["status"] = ("stitched" if out["added"]
                     else "refused" if out["skipped"] else "nothing_to_stitch")
    return out


def _ground_via_near(em, gnd, x, y):
    """Is there already a ground VIA within `ALREADY_MM`?

    A ground track that close is not a stitch: it connects that layer and leaves
    the other one with the same problem. Only a via crosses the layer change.
    """
    for it, _layer in em.grid.copper_items_near(x, y, gnd, ALREADY_MM):
        if it.get("kind") == "via":
            return True
    return False


def _project(x, y, a, b):
    """Closest point on segment a-b to (x, y)."""
    ax, ay = a
    bx, by = b
    dx, dy = bx - ax, by - ay
    n2 = dx * dx + dy * dy
    if n2 <= 1e-18:
        return ax, ay
    t = ((x - ax) * dx + (y - ay) * dy) / n2
    t = max(0.0, min(1.0, t))
    return ax + t * dx, ay + t * dy


def _points_on_copper(em, gnd, x, y, reach_mm):
    """Points that are certainly ON ground copper, nearest first.

    Taken from the copper's own geometry - the projection onto a track's
    centreline, a pad's centre, another via's centre - because a via has to land
    on copper, not near it. A pour is handled separately: it covers an area, so
    any point inside it will do.
    """
    pts = []
    for it, _layer in em.grid.copper_items_near(x, y, gnd, reach_mm):
        if it.get("seg"):
            a, b, _w = it["seg"]
            pts.append(_project(x, y, a, b))
            continue
        poly = it.get("poly")
        if not poly:
            continue
        cx = sum(p[0] for p in poly) / float(len(poly))
        cy = sum(p[1] for p in poly) / float(len(poly))
        pts.append((cx, cy))
    pts.sort(key=lambda p: math.hypot(p[0] - x, p[1] - y))
    return pts


def _spot(em, gnd, x, y, reach_mm):
    """Where to put the ground via, or `None`.

    Two rules, both of them about landing ON copper: the exact check has to accept
    the via (`via_ok`), and the point has to be on ground copper on at least two
    layers at once - a via connected on one side is the problem it was meant to
    fix.
    """
    for px, py in _points_on_copper(em, gnd, x, y, reach_mm):
        if math.hypot(px - x, py - y) > reach_mm:
            continue
        if not em.grid.via_ok(px, py, gnd):
            continue
        layers = em.grid.copper_layers_near(px, py, gnd, 1e-6)
        if len(layers) < 2:
            continue
        return px, py, layers
    for r in RINGS_MM:
        for cx, cy in _ANGLES:
            px, py = x + r * cx, y + r * cy
            if not em.grid.via_ok(px, py, gnd):
                continue
            layers = em.grid.copper_layers_near(px, py, gnd, 1e-6)
            if len(layers) < 2:
                continue
            return px, py, layers
    return None

# ------------------------------------------------------------------ on a board
#
# Everything above works on an emitter: the vias it serves are the ones the
# *current* call emitted. That is the wrong question in two situations a real
# workflow hits constantly:
#
# * routing happens in stages (escapes, then the search, then the meander). The
#   ground pass at the end of the last stage sees only the meander's vias, and
#   the escape vias written two calls ago are invisible to it - they stay
#   unserved and nothing says so.
# * a board written by anything at all needs the same question asked of it, and
#   `route.stitch.audit` cannot answer it: that module wants the ground POUR on
#   the same layers as the signal, so a six-layer board whose ground is In1 and
#   whose signals are F/In2/B answers `not_stitchable` and never looks at a
#   single via.
#
# So the two functions below read a PARSED BOARD. They ask the same one question
# the pass above asks - "is there a ground via beside this signal via" - and they
# answer it from the copper that is actually in the file, on whatever layers it
# happens to be.

def _copper_layers(board):
    return [str(s.get("name")) for s in (getattr(board, "stackup", None) or [])
            if str(s.get("name") or "").endswith(".Cu")] or ["F.Cu", "B.Cu"]

def _board_net_names(board):
    """Every net name the board actually mentions, from copper not from a table.

    `Board.nets` is populated by the adapter for boards it built itself and is
    empty for a file parsed off disk, so a name list taken from it alone would
    report `no_ground_net` on every real board.
    """
    names = set()
    for t in (getattr(board, "tracks", None) or ()):
        names.add(t.get("net"))
    for v in (getattr(board, "vias", None) or ()):
        names.add(v.get("net"))
    try:
        for p in (board.pads() or ()):
            names.add(p.get("net"))
    except Exception:                  # noqa: BLE001 - a board without pads()
        pass
    for z in (getattr(board, "zones", None) or ()):
        names.add(z.get("net"))
    names.discard(None)
    return names

def _seg_dist(px, py, ax, ay, bx, by):
    dx, dy = bx - ax, by - ay
    n2 = dx * dx + dy * dy
    if n2 <= 1e-18:
        return math.hypot(px - ax, py - ay)
    t = ((px - ax) * dx + (py - ay) * dy) / n2
    t = 0.0 if t < 0.0 else (1.0 if t > 1.0 else t)
    return math.hypot(px - (ax + t * dx), py - (ay + t * dy))

def _inside(x, y, poly):
    inside = False
    n = len(poly)
    for i in range(n):
        x1, y1 = poly[i]
        x2, y2 = poly[(i + 1) % n]
        if (y1 > y) != (y2 > y):
            xc = x1 + (y - y1) * (x2 - x1) / (y2 - y1)
            if xc > x:
                inside = not inside
    return inside

def _ground_copper(board, gnd):
    """(layer -> ground tracks, ground pads, ground vias, layer -> filled pours).

    One pass over the board, because the audit asks this question about every via
    and re-indexing per via would be the same walk a hundred times.
    """
    trk, pad, via, pour = {}, [], [], {}
    for t in (getattr(board, "tracks", None) or ()):
        if t.get("net") != gnd or not t.get("start") or not t.get("end"):
            continue
        trk.setdefault(t.get("layer"), []).append(t)
    try:
        for p in (board.pads() or ()):
            if p.get("net") == gnd:
                pad.append(p)
    except Exception:                  # noqa: BLE001
        pass
    for v in (getattr(board, "vias", None) or ()):
        if v.get("net") == gnd and v.get("x") is not None:
            via.append(v)
    for z in (getattr(board, "zones", None) or ()):
        if z.get("is_keepout") or z.get("net") != gnd:
            continue
        for lay in (z.get("layers") or ([z.get("layer")] if z.get("layer") else [])):
            for poly in (z.get("filled_polygons") or ()):
                if poly and len(poly) >= 3:
                    pour.setdefault(lay, []).append(poly)
    return trk, pad, via, pour

def _layers_with_ground(gc, x, y, reach_mm, copper=None):
    """Which copper layers carry `gnd` copper within `reach_mm` of (x, y).

    A pour counts by containment (it is an area), tracks and pads by distance to
    their geometry, a via on every layer it spans - which is what makes a
    stitching via already present answer "both sides" instead of "one".
    """
    trk, pad, via, pour = gc
    out = {}
    for layer, polys in pour.items():
        for poly in polys:
            if _inside(x, y, poly):
                out[layer] = 0.0
                break
    for layer, trs in trk.items():
        for t in trs:
            d = _seg_dist(x, y, t["start"][0], t["start"][1],
                          t["end"][0], t["end"][1]) - float(t.get("width") or 0) / 2.0
            if d <= reach_mm and (layer not in out or d < out[layer]):
                out[layer] = round(d, 4)
    for p in pad:
        dx = max(abs(x - float(p.get("x") or 0.0)) - float(p.get("w") or 0.0) / 2.0, 0.0)
        dy = max(abs(y - float(p.get("y") or 0.0)) - float(p.get("h") or 0.0) / 2.0, 0.0)
        d = math.hypot(dx, dy)
        if d <= reach_mm:
            for layer in (p.get("layers") or []):
                if layer.endswith(".Cu") and (layer not in out or d < out[layer]):
                    out[layer] = round(d, 4)
    for v in via:
        d = math.hypot(x - v["x"], y - v["y"])
        if d > reach_mm:
            continue
        # A via is copper on every layer it spans. Without the stackup in hand,
        # its own span is the honest list: it is what the file says the hole
        # connects.
        for layer in (copper or list(v.get("layers") or ())):
            if layer not in out:
                out[layer] = round(d, 4)
    return out

def _in_stack_order(layers, copper):
    """The layer names in stack order - a set of names is not a reading order."""
    known = [l for l in copper if l in layers]
    rest = sorted(set(layers) - set(known))
    return known + rest

# ------------------------------------------------------- which plane is "the"
#
# "A ground via beside every layer change" is the rule a person follows on a
# two-layer board, where the only way back is a hole. On a stackup it is the
# wrong rule, and applying it blindly is how a checker ends up demanding 107
# vias it does not need and missing the one defect it does:
#
# * a signal that goes F.Cu -> In2.Cu with ONE plane between them (In1.Cu, GND)
#   has the same reference on both sides. The return current never leaves that
#   plane, so there is nothing to stitch; a hole beside it is copper with no job.
# * a signal that goes In2.Cu -> B.Cu changes reference. If the plane under
#   B.Cu carries the same net, a ground via between the two planes is exactly
#   what is needed. If it carries a DIFFERENT net - or no pour at all - no via
#   can fix it: the design has no return path, and the honest answer says so.
#
# So the question this asks is not "is there a ground via within 2 mm" but
# "does this layer change keep the return current in the same conductor".

def _stack_order(board):
    got = [str(s.get("name")) for s in (getattr(board, "stackup", None) or [])
           if str(s.get("name") or "").endswith(".Cu")]
    return got or ["F.Cu", "B.Cu"]

def _pour_at(board, x, y):
    """layer -> the net whose FILLED copper contains (x, y).

    A layer with several nets on it is a split plane, which is the case that
    matters: the answer is per point, not per layer.
    """
    out = {}
    for z in (getattr(board, "zones", None) or ()):
        if z.get("is_keepout") or not z.get("net"):
            continue
        for lay in (z.get("layers") or ([z.get("layer")] if z.get("layer") else [])):
            if lay in out:
                continue
            for poly in (z.get("filled_polygons") or ()):
                if poly and len(poly) >= 3 and _inside(x, y, poly):
                    out[lay] = z["net"]
                    break
    return out

def _reference_of(order, pours, layer, x, y, depth=3):
    """The nearest layer to `layer` that carries a pour at (x, y), either side.

    Outward from the signal layer, above first then below. `depth` counts layers
    of copper, so a plane two dielectrics away still answers - and it is reported
    as such, because a reference two layers away is not the same design as one
    directly underneath.
    """
    if layer not in order:
        return None
    i = order.index(layer)
    for step in range(1, int(depth) + 1):
        for j in (i - step, i + step):
            if 0 <= j < len(order):
                lay = order[j]
                if lay in pours:
                    return {"layer": lay, "net": pours[lay], "distance": step}
    return None

def _via_layers(board, v):
    """Which layers this via joins, from the tracks that end on it.

    A via's span says where the hole is drilled; the tracks say where current
    actually goes through it. The difference matters: on this board every via is
    a through hole spanning six layers while most of them only join two.
    """
    x, y = float(v["x"]), float(v["y"])
    got = []
    for t in (getattr(board, "tracks", None) or ()):
        if t.get("net") != v.get("net") or t.get("layer") in got:
            continue
        a, b = t.get("start"), t.get("end")
        if not a or not b:
            continue
        # A via is as often in the MIDDLE of a run as at the end of two: testing
        # only the endpoints called those vias dangling, which is the opposite of
        # the truth and would have made the audit useless on a real board.
        if _seg_dist(x, y, a[0], a[1], b[0], b[1]) <= 0.02:
            got.append(t.get("layer"))
    return [l for l in got if l]

def _nearest_ground_via(gc, x, y):
    trk, pad, via, pour = gc
    best = None
    for v in via:
        d = math.hypot(x - v["x"], y - v["y"])
        if best is None or d < best[0]:
            best = (d, v)
    return best

def audit(board, net=None, reach_mm=GROUND_REACH_MM, shortlist=20):
    """Does every signal via on this board have a way home?

    Read-only, and it works on the board as stored: ground copper may be a pour
    on an inner plane layer, a track on an outer one, a pad, or another via - all
    four are asked, because a six-layer board's ground commonly exists in only
    one of those forms. A via counts as served when a ground VIA is within
    `reach_mm`: copper on a layer is not a path across the layer change, a hole
    is.
    """
    if net:
        gnd = net
    else:
        cands = sorted(set(n for n in _board_net_names(board) if _looks_ground(n)))
        if not cands:
            return {"verdict": "refused", "signal_vias": 0,
                    "why": {"reason": "no_ground_net",
                            "note": _NO_GROUND_NOTE}}
        if len(cands) > 1:
            return {"verdict": "refused", "signal_vias": 0,
                    "why": {"reason": "ambiguous_ground_net",
                            "candidates": cands,
                            "note": _AMBIGUOUS_NOTE}}
        gnd = cands[0]
    gc = _ground_copper(board, gnd)
    copper = _copper_layers(board)
    out = {"net": gnd, "reach_mm": reach_mm, "signal_vias": 0, "served": 0,
           "unserved": 0, "already_stitched": 0, "existing_ground_vias": len(gc[2]),
           "by_net": {}, "unserved_shortlist": []}
    worst = []
    for v in (getattr(board, "vias", None) or ()):
        one = v.get("net")
        if not one or one == gnd or v.get("x") is None:
            continue
        out["signal_vias"] += 1
        x, y = float(v["x"]), float(v["y"])
        near = _nearest_ground_via(gc, x, y)
        d = near[0] if near else None
        has = _layers_with_ground(gc, x, y, reach_mm, copper=copper)
        rec = out["by_net"].setdefault(one, {"vias": 0, "served": 0, "unserved": 0})
        rec["vias"] += 1
        if d is not None and d <= ALREADY_MM:
            out["already_stitched"] += 1
            out["served"] += 1
            rec["served"] += 1
            continue
        if d is not None and d <= reach_mm:
            out["served"] += 1
            rec["served"] += 1
            continue
        out["unserved"] += 1
        rec["unserved"] += 1
        worst.append({"net": one, "at": [round(x, 4), round(y, 4)],
                      "nearest_ground_via_mm": (round(d, 4) if d is not None else None),
                      "layers_with_ground": _in_stack_order(has, copper),
                      "placeable": len(has) >= 2})
    worst.sort(key=lambda r: (not r["placeable"],
                              -(r["nearest_ground_via_mm"] or 0.0)))
    out["unserved_shortlist"] = worst[:int(shortlist)]
    out["unserved_shortlist_truncated"] = max(0, len(worst) - int(shortlist))
    if not out["signal_vias"]:
        out["verdict"] = "no_signal_vias"
    elif out["unserved"]:
        out["verdict"] = "unserved"
        out["why"] = {
            "reason": "no_ground_via_within_reach",
            "note": "a layer change whose return via is missing makes the return "
                    "current detour to wherever the nearest ground via is; the "
                    "loop area - and the emissions - grow with that distance",
            "unserved": out["unserved"],
            "placeable": sum(1 for r in worst if r["placeable"]),
            "not_placeable": sum(1 for r in worst if not r["placeable"]),
            "not_placeable_reason": "ground copper is not on two layers within "
                                    "reach of those vias, so a ground via there "
                                    "would be a stub into laminate",
        }
    else:
        out["verdict"] = "all_served"
    return out

_NO_GROUND_NOTE = ("no net name on this board looks like a ground; pass net= if "
                   "it calls the plane something else")
_AMBIGUOUS_NOTE = ("more than one net looks like a ground; they are different "
                   "return networks, so pass the one you mean")

# Rings tried around a signal via when the exact-projection spots are all
# refused. Same ladder the emitter pass uses, so the two agree on what "nearby"
# means.
_PLAN_RINGS = (0.45, 0.6, 0.8, 1.0, 1.2, 1.4, 1.6, 1.8, 2.0)
_PLAN_ANGLES = tuple((math.cos(2 * math.pi * k / 12.0), math.sin(2 * math.pi * k / 12.0))
                     for k in range(12))

def _points_on_ground(gc, x, y, reach_mm):
    """Points that are certainly ON ground copper, nearest first.

    Taken from the copper's own geometry - the projection onto a track's
    centreline, a pad's centre, another ground via's centre - because a hole has
    to land on copper, not near it.
    """
    trk, pad, via, pour = gc
    pts = []
    for _layer, trs in trk.items():
        for t in trs:
            ax, ay = t["start"]
            bx, by = t["end"]
            dx, dy = bx - ax, by - ay
            n2 = dx * dx + dy * dy
            if n2 <= 1e-18:
                p = (ax, ay)
            else:
                u = ((x - ax) * dx + (y - ay) * dy) / n2
                u = 0.0 if u < 0.0 else (1.0 if u > 1.0 else u)
                p = (ax + u * dx, ay + u * dy)
            if math.hypot(p[0] - x, p[1] - y) <= reach_mm:
                pts.append(p)
    for p in pad:
        q = (float(p.get("x") or 0.0), float(p.get("y") or 0.0))
        if math.hypot(q[0] - x, q[1] - y) <= reach_mm:
            pts.append(q)
    for v in via:
        q = (v["x"], v["y"])
        if math.hypot(q[0] - x, q[1] - y) <= reach_mm:
            pts.append(q)
    pts.sort(key=lambda p: math.hypot(p[0] - x, p[1] - y))
    return pts

def plan(board, net=None, reach_mm=GROUND_REACH_MM, dia_mm=None, drill_mm=None,
         clearance_mm=0.25, edge_mm=0.5, hole_clearance_mm=None, max_ops=400):
    """The holes this board is missing: one ground via beside every signal via.

    Same question the emitter pass above answers, asked of the whole board, so a
    board that was routed in several calls - or by something else entirely - can
    be served. Nothing is written: the ops use the vocabulary `board_propose` /
    `board_apply` already speak.

    The size defaults are read off the board's own signal vias rather than
    invented, because a 0.6 mm via beside a 0.2 mm one is a different board.
    """
    from . import stitch as S

    if net:
        gnd = net
    else:
        cands = sorted(set(n for n in _board_net_names(board) if _looks_ground(n)))
        if not cands:
            return {"ops": [], "added": [], "skipped": [], "status": "refused",
                    "why": {"reason": "no_ground_net", "note": _NO_GROUND_NOTE}}
        if len(cands) > 1:
            return {"ops": [], "added": [], "skipped": [], "status": "refused",
                    "why": {"reason": "ambiguous_ground_net",
                            "candidates": cands, "note": _AMBIGUOUS_NOTE}}
        gnd = cands[0]

    copper = _copper_layers(board)
    gc = _ground_copper(board, gnd)
    signals = [v for v in (getattr(board, "vias", None) or ())
               if v.get("net") and v.get("net") != gnd and v.get("x") is not None]
    if dia_mm is None or drill_mm is None:
        got = sorted((float(v.get("diameter") or 0.0),
                      float(v.get("drill") or 0.0)) for v in signals)
        got = [g for g in got if g[0] > 0.0]
        if not got:
            return {"ops": [], "added": [], "skipped": [], "status": "refused",
                    "why": {"reason": "no_signal_via_to_size_from",
                            "note": "the board has no via of another net, so the "
                                    "stitching hole has no size to match; pass "
                                    "dia_mm and drill_mm"}}
        dia_mm = dia_mm if dia_mm is not None else got[len(got) // 2][0]
        drill_mm = drill_mm if drill_mm is not None else (got[len(got) // 2][1]
                                                         or dia_mm / 2.0)
    dia_mm, drill_mm = float(dia_mm), float(drill_mm)
    if drill_mm >= dia_mm:
        raise ValueError("drill (%r) must be smaller than the pad diameter (%r)"
                         % (drill_mm, dia_mm))
    hc = S.HOLE_CLEARANCE_MM if hole_clearance_mm is None else float(hole_clearance_mm)
    radius = dia_mm / 2.0
    obs = S._obstacles(board)
    edges = S._board_edges(board)

    out = {"ground_net": gnd, "reach_mm": reach_mm, "dia_mm": dia_mm,
           "drill_mm": drill_mm, "ops": [], "added": [], "skipped": [],
           "status": "not_attempted"}
    if not signals:
        out["status"] = "nothing_to_stitch"
        return out

    def _ok(px, py, gc_now):
        if edges and (not S._inside_board(px, py, edges) or
                      (S._edge_dist(px, py, edges) or 0.0) < edge_mm):
            return "too_near_board_edge"
        layers = _layers_with_ground(gc_now, px, py, 1e-6, copper=copper)
        if len(layers) < 2:
            return "ground_not_on_two_layers"
        why = S._why_not(px, py, gnd, radius, drill_mm, obs, clearance_mm,
                         copper, hole_clearance_mm=hc)
        if why:
            return why
        return None

    added_vias = []
    for v in signals:
        if len(out["ops"]) >= int(max_ops):
            out["skipped"].append({"net": v.get("net"), "reason": "max_ops"})
            continue
        x, y = float(v["x"]), float(v["y"])
        near = _nearest_ground_via(gc, x, y)
        if near is not None and near[0] <= ALREADY_MM:
            out["skipped"].append({"net": v.get("net"),
                                   "at": [round(x, 4), round(y, 4)],
                                   "reason": "already_stitched",
                                   "nearest_ground_via_mm": round(near[0], 4)})
            continue
        # The holes placed earlier in this plan are obstacles for the next one.
        gc_now = (gc[0], gc[1], list(gc[2]) + added_vias, gc[3])
        has = _layers_with_ground(gc_now, x, y, reach_mm, copper=copper)
        if len(has) < 2:
            out["skipped"].append({"net": v.get("net"),
                                   "at": [round(x, 4), round(y, 4)],
                                   "reason": "ground_not_on_two_layers",
                                   "layers_with_ground": _in_stack_order(has, copper)})
            continue
        spot = None
        refusals = []
        for px, py in _points_on_ground(gc_now, x, y, reach_mm):
            if math.hypot(px - x, py - y) > reach_mm:
                continue
            r = _ok(px, py, gc_now)
            if r is None:
                spot = (px, py)
                break
            refusals.append(r if isinstance(r, str) else r.get("reason"))
        if spot is None:
            for ring in _PLAN_RINGS:
                for cx, cy in _PLAN_ANGLES:
                    px, py = x + ring * cx, y + ring * cy
                    r = _ok(px, py, gc_now)
                    if r is None:
                        spot = (px, py)
                        break
                    refusals.append(r if isinstance(r, str) else r.get("reason"))
                if spot:
                    break
        if spot is None:
            # "no legal spot" on its own is a shrug. The reason it was refused
            # most often is the reading: a board whose design clearance is
            # 0.105 mm refuses every 0.25 mm probe, and that is a parameter,
            # not a geometry.
            counts = {}
            for one in refusals:
                counts[one] = counts.get(one, 0) + 1
            top = sorted(counts.items(), key=lambda kv: -kv[1])[:3]
            out["skipped"].append({"net": v.get("net"),
                                   "at": [round(x, 4), round(y, 4)],
                                   "reason": "no_legal_spot",
                                   "rings_tried_mm": list(_PLAN_RINGS),
                                   "probes": len(refusals),
                                   "refused_by": [{"reason": k, "count": c}
                                                  for k, c in top]})
            continue
        px, py = spot
        out["ops"].append({"op": "add_via", "x": round(px, 6), "y": round(py, 6),
                           "net": gnd, "size": round(dia_mm, 6),
                           "drill": round(drill_mm, 6),
                           "role": "ground_stitch", "for_net": v.get("net")})
        added_vias.append({"x": px, "y": py, "net": gnd,
                           "diameter": dia_mm, "drill": drill_mm})
        out["added"].append({"net": v.get("net"),
                             "for_via": [round(x, 4), round(y, 4)],
                             "at": [round(px, 4), round(py, 4)],
                             "distance_mm": round(math.hypot(px - x, py - y), 4)})
    out["status"] = ("stitched" if out["added"] else
                     ("nothing_to_stitch" if not out["skipped"] else "refused"))
    return out

_REF_MEANING = {
    "shared_reference": "both layers see the same plane, so the return current "
                        "never leaves it and no hole is owed",
    "stitch_needed": "the reference changes layer but keeps its net, so a ground "
                     "via between those two planes is what closes the loop",
    "reference_changes_net": "the two sides reference DIFFERENT nets, so no via "
                             "can carry the return current across; this is a "
                             "stackup/routing defect, not a missing hole",
    "no_reference": "at least one side has no pour within `depth` layers, so that "
                    "copper has no return path at all",
    "dangling_escape": "the via joins one layer only, so it is a stub: it changes "
                       "no current path and DRC reports it as via_dangling",
}

def audit_reference(board, net=None, depth=3, reach_mm=GROUND_REACH_MM, shortlist=20):
    """Does each layer change keep its return current in the same conductor?

    The question `audit()` answers by proximity, answered by the stackup instead.
    Both are reported because they disagree for a reason worth reading: a via can
    be 8 mm from the nearest ground hole and still need nothing (shared plane),
    and a via can have a ground hole 0.4 mm away and still have no return path
    (the hole joins the wrong plane).
    """
    order = _stack_order(board)
    if net:
        gnd = net
    else:
        cands = sorted(set(n for n in _board_net_names(board) if _looks_ground(n)))
        if not cands:
            return {"verdict": "refused",
                    "why": {"reason": "no_ground_net", "note": _NO_GROUND_NOTE}}
        if len(cands) > 1:
            return {"verdict": "refused",
                    "why": {"reason": "ambiguous_ground_net",
                            "candidates": cands, "note": _AMBIGUOUS_NOTE}}
        gnd = cands[0]
    gc = _ground_copper(board, gnd)
    out = {"net": gnd, "stackup": order, "depth": depth, "reach_mm": reach_mm,
           "signal_vias": 0, "counts": {}, "by_net": {}, "cases": []}
    cache = {}
    for v in (getattr(board, "vias", None) or ()):
        one = v.get("net")
        if not one or one == gnd or v.get("x") is None:
            continue
        out["signal_vias"] += 1
        x, y = float(v["x"]), float(v["y"])
        key = (round(x, 3), round(y, 3))
        pours = cache.get(key)
        if pours is None:
            pours = _pour_at(board, x, y)
            cache[key] = pours
        joined = _via_layers(board, v)
        rec = out["by_net"].setdefault(one, {"vias": 0, "counts": {}})
        rec["vias"] += 1
        if len(joined) < 2:
            verdict = "dangling_escape"
            detail = {"layers_joined": joined,
                      "via_span": [v.get("from"), v.get("to")]}
        else:
            refs = [_reference_of(order, pours, l, x, y, depth) for l in joined]
            if any(r is None for r in refs):
                verdict = "no_reference"
                detail = {"layers_joined": joined,
                          "references": refs,
                          "layers_without_pour": [l for l, r in zip(joined, refs)
                                                  if r is None]}
            elif len(set((r["layer"], r["net"]) for r in refs)) == 1:
                verdict = "shared_reference"
                detail = {"layers_joined": joined,
                          "reference": refs[0]}
            elif len(set(r["net"] for r in refs)) == 1:
                verdict = "stitch_needed"
                detail = {"layers_joined": joined,
                          "reference": refs[0],
                          "reference_after": refs[-1],
                          "nearest_ground_via_mm": None}
                near = _nearest_ground_via(gc, x, y)
                if near is not None:
                    detail["nearest_ground_via_mm"] = round(near[0], 4)
                    if near[0] <= reach_mm:
                        verdict = "stitched"
            else:
                verdict = "reference_changes_net"
                detail = {"layers_joined": joined,
                          "reference": refs[0],
                          "reference_after": refs[-1],
                          "note": "the two planes carry %r and %r"
                                  % (refs[0]["net"], refs[-1]["net"])}
        out["counts"][verdict] = out["counts"].get(verdict, 0) + 1
        rec["counts"][verdict] = rec["counts"].get(verdict, 0) + 1
        rec.setdefault("first", {})
        if verdict not in rec["first"]:
            rec["first"][verdict] = {"at": [round(x, 4), round(y, 4)],
                                     "detail": detail}
        if verdict not in ("shared_reference", "stitched"):
            out["cases"].append({"net": one, "at": [round(x, 4), round(y, 4)],
                                 "verdict": verdict, "detail": detail})
    bad = [c for c in out["cases"]
           if c["verdict"] in ("reference_changes_net", "no_reference")]
    out["verdict"] = ("refused" if not out["counts"] else
                      ("return_paths_intact" if not out["cases"] else "defects"))
    out["meaning"] = _REF_MEANING
    out["defect_cases"] = bad[:int(shortlist)]
    out["defect_cases_truncated"] = max(0, len(bad) - int(shortlist))
    return out
