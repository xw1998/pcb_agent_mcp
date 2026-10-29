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
    if not isinstance(name, str):
        return False
    up = name.upper().replace("_", "").replace("-", "").strip()
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
