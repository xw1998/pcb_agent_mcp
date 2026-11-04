"""Stitching vias: the holes that sew two ground planes into one conductor.

Why this module exists at all
-----------------------------
Every via-related tool in this server *computed* about vias without ever putting
one on a board: `via_resistance`, `via_current_allowed`, `thermal_via_array`,
`cavity_via_fence`, `pdn_plane_*`. So a two-layer board with two ground pours and
**zero** vias between them was, as far as the checks were concerned, fine - and a
human looking at it saw a board that "does not look like the good ones", because
every good two-layer board is sewn shut with ground vias.

That is a capability hole, not a taste question:

* two pours on two layers only become one plane where copper crosses between
  them; unwired, they are two islands, and a return current that hops from one to
  the other has only the long way round;
* a signal that changes layer drags its return current with it - if there is no
  ground via within a via pitch of that layer change, the return has to find
  another way, and the loop area grows to whatever that detour is;
* copper pours that are stitched also stop being "isolated copper" islands at the
  edges, which is the thing `board_drc` reports as `isolated_copper`.

What is decided here, and what is left to the caller
----------------------------------------------------
The geometry is decided here (where a hole may go, and why it may not), and the
*policy* is a parameter: pitch, diameter, drill, edge distance, clearance. Nothing
about "how many is enough" is invented - the audit reports the number and the
gaps, and `pitch_mm` is whatever the caller asked for.

Two hard refusals, because a via placed without them is a lie:

1. **A stitching via has to be inside the same-net pour on BOTH layers.** A hole
   in the middle of a routing channel "connects" nothing; it just adds a drill.
   The test is point-in-`filled_polygon`, not point-in-zone-outline: the outline
   is where the pour *could* go, the fill is where copper *is*.
2. **A fill that exists but is empty is not coverage.** A zone with `(fill yes)`
   and no `filled_polygon` means "the vendor engine has not poured this yet" - the
   only honest answer is `pour_not_filled`, not "no obstacle found".

And one more, because a board file is not a DRC engine: this module measures the
point against existing copper *itself*. The verdict still belongs to `board_drc`
after the write - and it has to be read with `refill_zones=true`, because the
fills are a snapshot (see docs/116).
"""
import math

# Points closer than this are the same point. A grid does not need microns.
_EPS = 1e-9

# KiCad's default hole-to-hole clearance. It is a board-level rule, so it applies
# between two holes of the SAME net as well: two drills that overlap are a
# manufacturing defect whatever the net says.
HOLE_CLEARANCE_MM = 0.25

# Directions tried, in order, when a ground via is placed beside a signal via.
# Diagonals first: a return via at 45 degrees still sits inside the same via pitch
# and does not line up with the signal via's own axis (where the next signal via
# is likely to go).
_BESIDE_DIRS = (
    (0.7071, 0.7071), (-0.7071, 0.7071), (0.7071, -0.7071), (-0.7071, -0.7071),
    (0.0, 1.0), (1.0, 0.0), (0.0, -1.0), (-1.0, 0.0),
)


# --------------------------------------------------------------- polygons
def _pip(x, y, poly):
    """Ray casting. A point exactly on an edge is unstable here - which is why
    every caller also measures distance to the boundary and refuses near it."""
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


def _dist_point_seg(px, py, ax, ay, bx, by):
    dx, dy = bx - ax, by - ay
    L2 = dx * dx + dy * dy
    if L2 <= _EPS:
        return math.hypot(px - ax, py - ay)
    t = ((px - ax) * dx + (py - ay) * dy) / L2
    t = 0.0 if t < 0.0 else (1.0 if t > 1.0 else t)
    return math.hypot(px - (ax + t * dx), py - (ay + t * dy))


def _poly_boundary_dist(x, y, poly):
    n = len(poly)
    return min(_dist_point_seg(x, y, poly[i][0], poly[i][1],
                               poly[(i + 1) % n][0], poly[(i + 1) % n][1])
               for i in range(n))


def _rect_dist(x, y, cx, cy, hw, hh):
    dx = max(abs(x - cx) - hw, 0.0)
    dy = max(abs(y - cy) - hh, 0.0)
    return math.hypot(dx, dy)


def _polys(zones, net, layer, filled_only=True):
    """The filled copper of `net` on `layer`, as polygons.

    `filled_only=False` returns the outlines too, for the "you have a zone but no
    fill" diagnosis - never for coverage, because an outline is not copper.
    """
    out, declared, filled = [], 0, 0
    for z in zones or ():
        if z.get("is_keepout") or z.get("keepout"):
            continue
        if z.get("net") != net:
            continue
        lays = z.get("layers") or ([z.get("layer")] if z.get("layer") else [])
        if layer not in lays:
            continue
        declared += 1
        fp = [p for p in (z.get("filled_polygons") or []) if p and len(p) >= 3]
        filled += 1 if fp else 0
        if fp:
            out.extend(fp)
        elif not filled_only:
            ol = z.get("outline") or []
            if len(ol) >= 3:
                out.append(ol)
    return out, {"zones_declared": declared, "zones_filled": filled}


def _inside_any(x, y, polys):
    return any(_pip(x, y, p) for p in polys)


def _near_any_boundary(x, y, polys, limit):
    """True when the point is inside the fill but closer than `limit` to its edge.

    A hole punched within a hair of the fill boundary lands half in copper and half
    in the clearance gap the pour itself left - it is not a stitch, it is a defect
    that only looks like one on screen.
    """
    for p in polys:
        if _pip(x, y, p):
            if _poly_boundary_dist(x, y, p) < limit:
                return True, _poly_boundary_dist(x, y, p)
    return False, None


# ------------------------------------------------------------- obstacles
def _obstacles(board):
    """Existing copper as flat lists, in the canonical (mm, y-up) frame."""
    segs = []
    for t in (getattr(board, "tracks", None) or ()):
        if t.get("_arc") or not t.get("start") or not t.get("end"):
            continue
        segs.append((t.get("net"), t.get("layer"),
                     float(t["start"][0]), float(t["start"][1]),
                     float(t["end"][0]), float(t["end"][1]),
                     float(t.get("width") or 0.0)))
    pads = []
    for p in (getattr(board, "pads", None) and board.pads() or ()):
        w, h = float(p.get("w") or 0.0), float(p.get("h") or 0.0)
        if float(p.get("rotation") or 0.0) % 180.0 == 90.0:
            w, h = h, w
        pads.append((p.get("net"), p.get("layers") or [],
                     float(p.get("x") or 0.0), float(p.get("y") or 0.0),
                     w / 2.0, h / 2.0, float(p.get("hole_mm") or 0.0),
                     p.get("number")))
    vias = []
    for v in (getattr(board, "vias", None) or ()):
        at = (v.get("x"), v.get("y"))
        if at[0] is None:
            at = v.get("start") or (None, None)
        vias.append((v.get("net"), float(at[0]), float(at[1]),
                     float(v.get("diameter") or 0.0),
                     float(v.get("drill") or 0.0)))
    return segs, pads, vias


def _why_not(x, y, net, radius, drill, obs, clearance_mm, layers,
             hole_clearance_mm=HOLE_CLEARANCE_MM, allow_same_net_pad=False):
    """The first reason this hole may not go here, or None.

    Copper spacing is checked against **both** layers at once (a through via is
    copper on every layer it spans), so `layers` is only used to skip pads that
    are not on a copper layer at all.
    """
    segs, pads, vias = obs
    for one, o_lay, ax, ay, bx, by, ow in segs:
        if one == net or o_lay not in layers:
            continue
        gap = _dist_point_seg(x, y, ax, ay, bx, by) - ow / 2.0 - radius
        if gap < clearance_mm - 1e-9:
            return {"reason": "too_close", "with": "track", "with_net": one,
                    "gap_mm": round(gap, 4)}
    for one, p_lays, px, py, hw, hh, phole, pnum in pads:
        copper_here = ("*.Cu" in p_lays) or any(l in p_lays for l in layers)
        if not copper_here:
            continue
        if phole > 0.0:
            hg = math.hypot(x - px, y - py) - phole / 2.0 - drill / 2.0
            if hg < hole_clearance_mm - 1e-9:
                return {"reason": "hole_too_close", "with": "pad_hole",
                        "with_net": one, "with_pad": pnum,
                        "gap_mm": round(hg, 4)}
        if one == net:
            if allow_same_net_pad:
                continue
            if _rect_dist(x, y, px, py, hw, hh) <= radius + 1e-9:
                return {"reason": "on_same_net_pad", "with": "pad",
                        "with_net": one, "with_pad": pnum}
            continue
        gap = _rect_dist(x, y, px, py, hw, hh) - radius
        if gap < clearance_mm - 1e-9:
            return {"reason": "too_close", "with": "pad", "with_net": one,
                    "with_pad": pnum, "gap_mm": round(gap, 4)}
    for one, vx, vy, vdia, vdrill in vias:
        hg = math.hypot(x - vx, y - vy) - (vdrill or vdia) / 2.0 - drill / 2.0
        if hg < hole_clearance_mm - 1e-9:
            return {"reason": "hole_too_close", "with": "via", "with_net": one,
                    "gap_mm": round(hg, 4)}
        if one == net:
            continue
        gap = math.hypot(x - vx, y - vy) - vdia / 2.0 - radius
        if gap < clearance_mm - 1e-9:
            return {"reason": "too_close", "with": "via", "with_net": one,
                    "gap_mm": round(gap, 4)}
    return None


# ----------------------------------------------------------------- grid
def _bbox(polys):
    xs = [p[0] for poly in polys for p in poly]
    ys = [p[1] for poly in polys for p in poly]
    return min(xs), min(ys), max(xs), max(ys)


def _grid(x0, y0, x1, y1, pitch):
    """Grid points inside the box, centred so the margins are equal.

    Centring matters for the thing the eye reads: an off-centre grid leaves one
    edge of the board visibly bare and the opposite edge crowded.
    """
    if pitch <= 0:
        raise ValueError("pitch must be > 0")
    w, h = x1 - x0, y1 - y0
    nx = int(math.floor(w / pitch)) + 1
    ny = int(math.floor(h / pitch)) + 1
    if nx <= 0 or ny <= 0:
        return []
    sx = x0 + (w - (nx - 1) * pitch) / 2.0
    sy = y0 + (h - (ny - 1) * pitch) / 2.0
    out = []
    for i in range(nx):
        for j in range(ny):
            out.append((round(sx + i * pitch, 4), round(sy + j * pitch, 4)))
    return out


def _board_edges(board):
    """Every contour the board's edge is drawn with, plus open chains."""
    edges = []
    for poly in (getattr(board, "outline", None) or ()):
        if poly and len(poly) >= 3:
            edges.append(list(poly))
    for chain in (getattr(board, "outline_open", None) or ()):
        if chain and len(chain) >= 2:
            edges.append(list(chain))
    return edges


def _edge_dist(x, y, edges):
    best = None
    for poly in edges:
        d = _poly_boundary_dist(x, y, poly)
        best = d if best is None else min(best, d)
    return best


def _inside_board(x, y, edges):
    return any(_pip(x, y, poly) for poly in edges)


# ---------------------------------------------------------------- public
def coverage(board, net="GND", layers=("F.Cu", "B.Cu")):
    """What poured copper exists, per layer - the precondition of everything else.

    A stitching via needs the same net on both layers. If one side has no fill,
    the honest answer is not "0 vias placed", it is "this board cannot be stitched
    on these layers, here is why".
    """
    out = {}
    for layer in layers:
        polys, info = _polys(getattr(board, "zones", None), net, layer)
        info["filled_polygons"] = len(polys)
        info["has_fill"] = bool(polys)
        info["area_mm2"] = round(sum(_poly_area(p) for p in polys), 3)
        out[layer] = info
    return out


def _poly_area(poly):
    a = 0.0
    n = len(poly)
    for i in range(n):
        x1, y1 = poly[i]
        x2, y2 = poly[(i + 1) % n]
        a += x1 * y2 - x2 * y1
    return abs(a) * 0.5


def audit(board, net="GND", pitch_mm=3.0, near_via_mm=None,
          layers=("F.Cu", "B.Cu"), edge_mm=0.5):
    """How well is the plane sewn right now? Reads, changes nothing."""
    if near_via_mm is None:
        near_via_mm = pitch_mm
    cov = coverage(board, net, layers)
    per_layer = [l for l in layers if cov[l]["has_fill"]]
    edges = _board_edges(board)
    vias = getattr(board, "vias", None) or []
    mine = [(float(v.get("x")), float(v.get("y")))
            for v in vias if v.get("net") == net and v.get("x") is not None]
    others = [(v.get("net"), float(v.get("x")), float(v.get("y")))
              for v in vias if v.get("net") != net and v.get("x") is not None]

    value = {
        "net": net,
        "layers": list(layers),
        "pour": cov,
        "stitchable": len(per_layer) == len(layers),
        "existing_stitch_vias": len(mine),
        "pitch_mm": pitch_mm,
        "near_via_mm": near_via_mm,
        "edge_mm": edge_mm,
    }
    if len(per_layer) != len(layers):
        missing = [l for l in layers if not cov[l]["has_fill"]]
        value["verdict"] = "not_stitchable"
        value["why"] = {
            "reason": "no_same_net_pour",
            "layers_without_fill": missing,
        }
        value["note"] = ("a via between two layers only stitches them where BOTH "
                         "layers carry that net's copper; on %s there is no %s fill"
                         % (", ".join(missing), net))
        return value

    polys = {}
    for layer in layers:
        polys[layer] = _polys(getattr(board, "zones", None), net, layer)[0]
    x0, y0, x1, y1 = _bbox(polys[layers[0]])
    pts = _grid(x0, y0, x1, y1, pitch_mm)
    usable, gaps, skipped = 0, [], {"outside_pour": 0, "too_near_board_edge": 0,
                                    "track_or_pad_in_the_way": 0}
    obs = _obstacles(board)
    radius = 0.3
    for (x, y) in pts:
        if not all(_inside_any(x, y, polys[l]) for l in layers):
            skipped["outside_pour"] += 1
            continue
        if edges and (not _inside_board(x, y, edges) or
                      (_edge_dist(x, y, edges) or 0.0) < edge_mm):
            skipped["too_near_board_edge"] += 1
            continue
        if _why_not(x, y, net, radius, 0.3, obs, 0.25, layers) is not None:
            skipped["track_or_pad_in_the_way"] += 1
            continue
        usable += 1
        near = min([math.hypot(x - vx, y - vy) for vx, vy in mine] or [None]) \
            if mine else None
        if near is None or near > pitch_mm / 2.0:
            gaps.append({"x": x, "y": y,
                         "nearest_stitch_via_mm": (round(near, 3)
                                                   if near is not None else None)})
    value.update({
        "candidate_points": len(pts),
        "candidate_points_usable": usable,
        "points_without_a_via_half_pitch": len(gaps),
        "gaps": gaps[:20],
        "gaps_truncated": max(0, len(gaps) - 20),
        "points_skipped": skipped,
    })

    if others and mine:
        no_return = []
        for one, vx, vy in others:
            near = min(math.hypot(vx - gx, vy - gy) for gx, gy in mine)
            if near > near_via_mm:
                no_return.append({"net": one, "x": vx, "y": vy,
                                  "nearest_stitch_via_mm": round(near, 3)})
        value["signal_vias"] = len(others)
        value["signal_vias_without_nearby_return"] = len(no_return)
        value["signal_vias_without_nearby_return_shortlist"] = no_return[:20]
    else:
        value["signal_vias"] = len(others)
        value["signal_vias_without_nearby_return"] = len(others)
        value["signal_vias_without_nearby_return_shortlist"] = [
            {"net": n, "x": x, "y": y, "nearest_stitch_via_mm": None}
            for n, x, y in others[:20]]
    value["verdict"] = ("no_stitching_at_all" if not mine else
                        ("under_stitched" if gaps else "stitched"))
    return value


def plan(board, net="GND", pitch_mm=3.0, dia_mm=0.6, drill_mm=0.3,
         edge_mm=0.5, clearance_mm=0.25, layers=("F.Cu", "B.Cu"),
         beside_vias=True, near_via_mm=None, max_ops=400, nudge_mm=0.4):
    """Plan the holes: a grid of stitching vias plus one beside every signal via.

    Returns ``{ops, placed, skipped, ...}``. `ops` uses the vocabulary
    `board_propose`/`board_apply` already speak (`add_via` with x/y/size/drill/net),
    so nothing here writes to a file.
    """
    if drill_mm >= dia_mm:
        raise ValueError("drill (%r) must be smaller than the pad diameter (%r)"
                         % (drill_mm, dia_mm))
    if near_via_mm is None:
        near_via_mm = pitch_mm
    cov = coverage(board, net, layers)
    per_layer = [l for l in layers if cov[l]["has_fill"]]
    if len(per_layer) != len(layers):
        return {
            "ops": [], "placed": 0, "skipped": [],
            "skip_counts": {}, "pour": cov,
            "refused": "no_same_net_pour",
            "layers_without_fill": [l for l in layers if not cov[l]["has_fill"]],
            "why": ("a via stitches two layers only where both carry %s copper; "
                    "pouring it on one layer makes the hole a stub" % net),
        }

    polys = {l: _polys(getattr(board, "zones", None), net, l)[0] for l in layers}
    edges = _board_edges(board)
    obs = _obstacles(board)
    radius = dia_mm / 2.0
    vias = getattr(board, "vias", None) or []
    mine = [(float(v.get("x")), float(v.get("y")))
            for v in vias if v.get("net") == net and v.get("x") is not None]
    others = [(v.get("net"), float(v.get("x")), float(v.get("y")))
              for v in vias if v.get("net") != net and v.get("x") is not None]

    x0, y0, x1, y1 = _bbox(polys[layers[0]])
    pts = _grid(x0, y0, x1, y1, pitch_mm)
    placed, skipped = [], []
    taken = list(mine)

    def _try(x, y, source, require_pour=True):
        if edges and not _inside_board(x, y, edges):
            return {"x": round(x, 4), "y": round(y, 4), "source": source,
                    "reason": "outside_board"}
        if edges:
            d = _edge_dist(x, y, edges)
            if d is None or d < edge_mm:
                return {"x": round(x, 4), "y": round(y, 4), "source": source,
                        "reason": "too_near_board_edge",
                        "gap_mm": (round(d, 4) if d is not None else None)}
        if require_pour:
            for l in layers:
                if not _inside_any(x, y, polys[l]):
                    return {"x": round(x, 4), "y": round(y, 4), "source": source,
                            "reason": "no_pour_here", "layer": l}
            # inside is not enough: a hole whose annular ring crosses the pour's own
            # clearance gap is not a stitch.
            for l in layers:
                near, d = _near_any_boundary(x, y, polys[l], radius + clearance_mm)
                if near:
                    return {"x": round(x, 4), "y": round(y, 4), "source": source,
                            "reason": "pour_edge_too_close", "layer": l,
                            "gap_mm": round(d, 4)}
        why = _why_not(x, y, net, radius, drill_mm, obs, clearance_mm, layers)
        if why:
            out = {"x": round(x, 4), "y": round(y, 4), "source": source}
            out.update(why)
            return out
        for (tx, ty) in taken:
            gap = math.hypot(x - tx, y - ty) - dia_mm
            if gap < clearance_mm - 1e-9:
                return {"x": round(x, 4), "y": round(y, 4), "source": source,
                        "reason": "existing_via", "gap_mm": round(gap, 4)}
        return None

    remaining = max_ops
    nudged = 0
    for (x, y) in pts:
        if remaining <= 0:
            break
        bad = _try(x, y, "grid")
        if bad is None:
            placed.append((x, y))
            taken.append((x, y))
            remaining -= 1
            continue
        # A grid point that lands 0.05 mm too close to the pour's own clearance gap
        # is not a reason to leave a hole out of the plane; it is a reason to move
        # the hole by a fifth of a millimetre. Only the LOCAL reasons are nudged - "no
        # copper on this layer at all" means the grid is in the wrong place, and
        # moving 0.4 mm will not pour copper that is not there.
        best = None
        if nudge_mm > 1e-9 and bad["reason"] in (
                "pour_edge_too_close", "too_close", "hole_too_close",
                "on_same_net_pad", "existing_via"):
            for r in (nudge_mm / 2.0, nudge_mm):
                for dx, dy in _BESIDE_DIRS:
                    cand = (x + dx * r, y + dy * r)
                    if _try(cand[0], cand[1], "grid") is None:
                        best = cand
                        break
                if best:
                    break
        if best is None:
            skipped.append(bad)
            continue
        placed.append(best)
        taken.append(best)
        nudged += 1
        remaining -= 1

    if beside_vias and remaining > 0:
        for net_name, vx, vy in others:
            if remaining <= 0:
                break
            near = min([math.hypot(vx - tx, vy - ty) for tx, ty in mine] or [None]) \
                if mine else None
            if near is not None and near <= near_via_mm:
                continue
            other_dia = 0.6
            for v in vias:
                if v.get("x") is not None and abs(float(v["x"]) - vx) < 1e-6 \
                        and abs(float(v["y"]) - vy) < 1e-6:
                    other_dia = float(v.get("diameter") or 0.6)
                    break
            step = other_dia / 2.0 + radius + clearance_mm
            best = None
            # One clearance-step is the ideal (annular rings just clear); if the
            # ring of space around the signal via is full - which it is inside a
            # fan-out - one step and a half is still "next to it" on any via pitch,
            # and a return hole 0.9 mm away is worth incomparably more than none.
            for mult in (1.0, 1.6):
                for dx, dy in _BESIDE_DIRS:
                    cand = (vx + dx * step * mult, vy + dy * step * mult)
                    bad = _try(cand[0], cand[1], "beside_via:" + str(net_name))
                    if bad is None:
                        best = cand
                        break
                if best:
                    break
            if best is None:
                skipped.append({"x": round(vx, 4), "y": round(vy, 4),
                                "source": "beside_via:" + str(net_name),
                                "reason": "no_room_around_signal_via",
                                "signal_via_net": net_name})
                continue
            placed.append(best)
            taken.append(best)
            remaining -= 1

    ops = [{"op": "add_via", "x": round(x, 4), "y": round(y, 4),
            "size": dia_mm, "drill": drill_mm, "net": net} for (x, y) in placed]
    counts = {}
    for s in skipped:
        counts[s["reason"]] = counts.get(s["reason"], 0) + 1
    return {
        "writes_nothing": True,
        "ops": ops,
        "placed": len(placed),
        "nudged": nudged,
        "skipped": skipped[:40],
        "skipped_total": len(skipped),
        "skip_counts": counts,
        "pour": cov,
        "params": {"net": net, "pitch_mm": pitch_mm, "dia_mm": dia_mm,
                   "drill_mm": drill_mm, "edge_mm": edge_mm,
                   "clearance_mm": clearance_mm, "layers": list(layers),
                   "beside_vias": beside_vias, "near_via_mm": near_via_mm,
                   "nudge_mm": nudge_mm},
    }
