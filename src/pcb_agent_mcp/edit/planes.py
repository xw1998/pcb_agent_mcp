"""Pours that are reference planes, and the copper a plan would put on them.

The rule this measures already exists: ``calc/rules.py`` carries
``layout.reference_plane_continuous`` at **Critical** severity - the reference
plane under a net has to stay continuous. Nothing in the write path asked it,
and the reason that mattered is not theoretical:

    On the RK3506 SOM the router found DDR_A8/DDR_A11 only by running four
    segments on In1.Cu, which is the board's only GND pour (1934 mm2 of a
    2025 mm2 board, 95.5%). A track crossing a pour is legal copper - the
    official ZONE_FILLER re-cuts the pour around it - so **DRC says nothing**.
    The bill is the plane: the refill gave up 36.96 mm2, i.e. the return path
    under every DDR signal that references In1 was cut in two places.

That is the shape of mistake this module exists to stop: legal copper, silent
electrical damage. It answers one question with geometry only, no judgement
about how much split is too much:

    which pours would this plan land another net's copper on?

A pour is called a **plane** when two things hold:

* its outline covers at least ``PLANE_SHARE`` of the board's outline area
  (default 0.5) - a declaration, not a proof: a 30% split power plane (this
  board's In3) is not called a plane, so a track over it is *reported* but not
  blocked; and
* the layer is an **inner** layer, i.e. it has copper on both sides. An outer
  pour is ground fill: every design cuts it, the official filler re-fills it
  around the new copper, and it is the return plane for nothing (a signal on
  the other outer layer returns on the inner plane between them). A two-layer
  board has no reference plane at all - that is the honest answer, not a
  measurement failure.

Every pour a plan crosses is reported with its share and with
``reference`` (inner layer or not), so a caller sees the whole list rather than
only the ones a threshold let through. A crossing of an outer pour is reported
and let through, the same way a small pour is.

The raster side (`route/grid.py`) deliberately lets a track cross a pour, and
that is right for a GND pour on an outer layer. The difference is which layer,
and that is what this measures - at the write gate, where a plan becomes bytes.
"""

import re as _re

from .. import geometry as G

#: A pour whose outline covers this much of the board is treated as a plane.
PLANE_SHARE = 0.5

#: How many points along a segment are tested. A pour is large next to a track,
#: so a midpoint alone already answers it; the extra samples exist so a segment
#: that starts outside the pour and ends inside is still caught.
_SAMPLES = (0.0, 0.25, 0.5, 0.75, 1.0)


#: Layer names that can only be inner ones when a board has no stackup at all.
_INNER_NAME = _re.compile(r"^In\d+\.Cu$")


def copper_order(board):
    """Copper layers in stackup order, outermost first."""
    names = [str(s.get("name")) for s in (board.stackup or [])
             if str(s.get("name") or "").endswith(".Cu")]
    return names or ["F.Cu", "B.Cu"]


def inner_layers(board):
    """Layers with copper on both sides - the only ones that can be a plane.

    A stackup answers it directly (drop the first and last copper layer). Without
    one, an inner layer can still be recognised by name (``In1.Cu``), which is
    how KiCad names them; a board that only has ``F.Cu``/``B.Cu`` has none, and
    that is the honest answer rather than "cannot measure".
    """
    order = copper_order(board)
    if len(order) >= 3:
        return set(order[1:-1])
    seen = set()
    for z in board.zones or []:
        for l in (z.get("layers") or []):
            seen.add(str(l))
    for t in board.tracks or []:
        seen.add(str(t.get("layer")))
    return set(n for n in seen if _INNER_NAME.match(n))


def board_area(board):
    """Outline bbox area in mm2, or None when the board has no usable outline."""
    try:
        first = (board.outline or [])[0]
    except (IndexError, TypeError):
        return None
    # adapters hand back either a point list or a dict carrying one
    pts = first.get("points") if isinstance(first, dict) else first
    if not pts:
        return None
    if len(pts) < 3:
        return None
    x0, y0, x1, y1 = G.bbox_of(pts)
    w, h = x1 - x0, y1 - y0
    if w <= 0 or h <= 0:
        return None
    return round(w * h, 4)


def pours(board, board_mm2=None):
    """Every non-keepout pour: net, layers, ring, area, share of the board."""
    if board_mm2 is None:
        board_mm2 = board_area(board)
    inner = inner_layers(board)
    out = []
    for z in board.zones or []:
        if z.get("is_keepout"):
            continue
        ring = z.get("outline") or []
        if len(ring) < 3:
            continue
        area = abs(G.polygon_area(ring))
        if area <= 0.0:
            continue
        share = (area / board_mm2) if board_mm2 else None
        lay = [str(l) for l in (z.get("layers") or [])]
        out.append({"net": z.get("net"), "layers": lay,
                    "reference": sorted(l for l in lay if l in inner),
                    "ring": [(float(p[0]), float(p[1])) for p in ring],
                    "area_mm2": round(area, 3),
                    "share": None if share is None else round(share, 4),
                    "bbox": list(G.bbox_of(ring))})
    return out


def _points_of(op, layers):
    """The (x, y, layer, net, kind) samples an operation puts copper at.

    Operations are in the canonical frame, the same one the adapter Board is in
    (`edit/build.py` writes `y_file(y)`, i.e. it flips only at the moment a node
    becomes file text). So an operation's coordinates can be compared with a pour
    outline straight away - measured against U2.T8 on the RK3506 SOM, whose pad
    reads (23.4, 4.0) and is the start point the router emits for /DDR_A8.

    A via is the awkward one. `add_via` carries ``layers: [top, bottom]`` only
    for a PARTIAL span; a through via has no ``layers`` key and its barrel passes
    every copper layer, planes included. That is not a cut - the plane is drilled
    with an antipad and the current flows around it, which is why every board has
    thousands of them - so a via is reported with ``kind="via"`` and never graded
    as a plane crossing.
    """
    kind = op.get("op") or ""
    net = op.get("net")
    if kind == "add_track":
        x1, y1 = float(op["x1"]), float(op["y1"])
        x2, y2 = float(op["x2"]), float(op["y2"])
        layer = op.get("layer")
        return [(x1 + (x2 - x1) * t, y1 + (y2 - y1) * t, layer, net, "track")
                for t in _SAMPLES]
    if kind == "add_via":
        x, y = float(op["x"]), float(op["y"])
        span = op.get("layers") or layers
        return [(x, y, l, net, "via") for l in span]
    return []


def crossings(board, operations, board_mm2=None, plane_share=PLANE_SHARE):
    """Which pours would this plan land another net's copper on.

    Returns ``{"board_mm2", "planes", "crossings", "by_net"}``. ``crossings`` is
    a de-duplicated list (one entry per net/layer/pour), each carrying
    ``plane`` - True when the pour is a plane by ``plane_share`` AND the copper
    that lands on it runs ALONG the layer (a track). A via's barrel passes every
    layer of its span and gets an antipad, which is not a cut, so a via carries
    ``via: true`` and ``plane: false``. Nothing here refuses anything; the
    precondition in ``ruleset/precondition.py`` does that, so the plan can report
    the fact even when the write is allowed.
    """
    if board_mm2 is None:
        board_mm2 = board_area(board)
    ps = pours(board, board_mm2)

    def _plane(p, layer=None):
        if p["share"] is None or p["share"] < plane_share:
            return False
        if layer is None:
            return bool(p["reference"])
        return layer in p["reference"]

    planes = [p for p in ps if _plane(p)]
    layers = [l for p in ps for l in p["layers"]] or []
    seen = {}
    for op in operations or []:
        for x, y, layer, net, kind in _points_of(op, layers):
            if not layer:
                continue
            py = y
            for p in ps:
                if layer not in p["layers"]:
                    continue
                if net is not None and p["net"] == net:
                    continue          # its own pour is its own copper
                bx = p["bbox"]
                if not (bx[0] - 1e-9 <= x <= bx[2] + 1e-9 and
                        bx[1] - 1e-9 <= py <= bx[3] + 1e-9):
                    continue
                if not G.point_in_polygon((x, py), p["ring"]):
                    continue
                key = (net, layer, p["net"])
                hit = {"net": net, "layer": layer,
                       "pour_net": p["net"],
                       "pour_area_mm2": p["area_mm2"],
                       "share": p["share"],
                       # an outer pour is ground fill: reported, never a
                       # reference plane and never blocked
                       "reference": p["reference"],
                       "via": kind == "via",
                       "plane": _plane(p, layer) and kind != "via",
                       # canonical frame, the caller's own space
                       "at": [round(x, 4), round(y, 4)]}
                prev = seen.get(key)
                if prev is None:
                    seen[key] = hit
                    continue
                # One entry per net/layer/pour, graded by the WORST copper on it:
                # a net that both runs a track along the plane and drops a via
                # through it is a cut, and the point quoted must be where the
                # copper runs, not where it drills. (The RK3506 plan emits the
                # track and its via at the same coordinate, so a first-hit-wins
                # de-duplication reported the via and lost the cut.)
                prev["plane"] = prev["plane"] or hit["plane"]
                prev["via"] = prev["via"] and hit["via"]
                if prev["via"] and not hit["via"]:
                    prev["at"] = hit["at"]
    hits = list(seen.values())
    by_net = {}
    for h in hits:
        by_net.setdefault(h["net"], []).append(h)
    return {"board_mm2": board_mm2,
            "planes": [{"layer": p["layers"][0], "net": p["net"],
                        "area_mm2": p["area_mm2"], "share": p["share"],
                        "reference": p["reference"]}
                       for p in planes],
            "crossings": hits, "by_net": by_net}


def plane_hits(report):
    """Only the crossings that landed on a plane (what the gate blocks on)."""
    return [h for h in (report or {}).get("crossings") or [] if h.get("plane")]


__all__ = ["PLANE_SHARE", "copper_order", "inner_layers", "board_area",
           "pours", "crossings", "plane_hits"]
