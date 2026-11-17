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

A pour is called a **plane** when its outline covers at least
``PLANE_SHARE`` of the board's outline area (default 0.5). That is a
declaration, not a proof: a 30% split power plane (this board's In3) is not
called a plane, so a track over it is *reported* but not blocked. Every pour a
plan crosses is reported with its share, so a caller can see the whole list
rather than only the ones a threshold let through.

The raster side (`route/grid.py`) deliberately lets a track cross a pour, and
that is right for a GND pour on a signal layer. The difference is which layer,
and that is what this measures - at the write gate, where a plan becomes bytes.
"""

from .. import geometry as G

#: A pour whose outline covers this much of the board is treated as a plane.
PLANE_SHARE = 0.5

#: How many points along a segment are tested. A pour is large next to a track,
#: so a midpoint alone already answers it; the extra samples exist so a segment
#: that starts outside the pour and ends inside is still caught.
_SAMPLES = (0.0, 0.25, 0.5, 0.75, 1.0)


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
        out.append({"net": z.get("net"), "layers": list(z.get("layers") or []),
                    "ring": [(float(p[0]), float(p[1])) for p in ring],
                    "area_mm2": round(area, 3),
                    "share": None if share is None else round(share, 4),
                    "bbox": list(G.bbox_of(ring))})
    return out


def _points_of(op, layers):
    """The (x, y, layer, net) samples an operation puts copper at.

    Operations are in the canonical frame, the same one the adapter Board is in
    (`edit/build.py` writes `y_file(y)`, i.e. it flips only at the moment a node
    becomes file text). So an operation's coordinates can be compared with a pour
    outline straight away - measured against U2.T8 on the RK3506 SOM, whose pad
    reads (23.4, 4.0) and is the start point the router emits for /DDR_A8.
    """
    kind = op.get("op") or ""
    net = op.get("net")
    if kind == "add_track":
        x1, y1 = float(op["x1"]), float(op["y1"])
        x2, y2 = float(op["x2"]), float(op["y2"])
        layer = op.get("layer")
        return [(x1 + (x2 - x1) * t, y1 + (y2 - y1) * t, layer, net)
                for t in _SAMPLES]
    if kind == "add_via":
        x, y = float(op["x"]), float(op["y"])
        span = op.get("layers") or layers
        return [(x, y, l, net) for l in span]
    return []


def crossings(board, operations, board_mm2=None, plane_share=PLANE_SHARE):
    """Which pours would this plan land another net's copper on.

    Returns ``{"board_mm2", "planes", "crossings", "by_net"}``. ``crossings`` is
    a de-duplicated list (one entry per net/layer/pour), each carrying
    ``plane`` - True when the pour is a plane by ``plane_share``. Nothing here
    refuses anything; the precondition in ``ruleset/precondition.py`` does
    that, so the plan can report the fact even when the write is allowed.
    """
    if board_mm2 is None:
        board_mm2 = board_area(board)
    ps = pours(board, board_mm2)
    planes = [p for p in ps if p["share"] is not None and p["share"] >= plane_share]
    layers = [p["layers"][0] for p in planes] or []
    seen = set()
    hits = []
    for op in operations or []:
        for x, y, layer, net in _points_of(op, layers):
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
                if key in seen:
                    continue
                seen.add(key)
                hits.append({"net": net, "layer": layer,
                             "pour_net": p["net"],
                             "pour_area_mm2": p["area_mm2"],
                             "share": p["share"],
                             "plane": p["share"] is not None and
                                      p["share"] >= plane_share,
                             # canonical frame, the caller's own space
                             "at": [round(x, 4), round(y, 4)]})
    by_net = {}
    for h in hits:
        by_net.setdefault(h["net"], []).append(h)
    return {"board_mm2": board_mm2,
            "planes": [{"layer": p["layers"][0], "net": p["net"],
                        "area_mm2": p["area_mm2"], "share": p["share"]}
                       for p in planes],
            "crossings": hits, "by_net": by_net}


def plane_hits(report):
    """Only the crossings that landed on a plane (what the gate blocks on)."""
    return [h for h in (report or {}).get("crossings") or [] if h.get("plane")]


__all__ = ["PLANE_SHARE", "board_area", "pours", "crossings", "plane_hits"]
