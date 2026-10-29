"""BGA fan-out: get each ball out of the array, or say which ones cannot come out.

A ball grid array is the one package where "route it" is not a routing problem
but an ordering problem: every escape has to leave through a gap that the next
ball's escape will not need. This module does the cheap, honest half of it:

* find the array (pads on a regular grid), read its pitch off the pads;
* for every ball, try a short stub straight outwards from the array centre,
  then the four rotations of that direction by up to 90 degrees;
* judge each candidate with the SAME exact geometry the router uses
  (`route.grid.Grid.seg_ok` / `via_ok`) - not a raster cell, not a distance
  rule of its own - and only then accept it;
* add the accepted stub and via to the grid, so the next ball has to escape
  around copper that already exists.

What it does not do, and says so: it does not do via-in-pad, it does not try
dogbone-vs-inline alternatives beyond a fixed direction ladder, it does not
rip up an accepted escape to make room for a later one, and it does not claim
the result is a complete escape - `skipped` is the list of balls that stay
buried, and a caller who wants them out has to change the stack, the via size
or the fanout style.

Coordinates in and out are the board's Y-up frame (the frame every `board_*`
operation takes), because a fan-out that silently swaps the Y sign buries the
vias on the wrong side of the balls.
"""
import math

from .adapters import canonical  # noqa: F401  (pad geometry lives there)
from .route import rules as R
from .route.grid import Grid


class FanoutError(Exception):
    def __init__(self, message, reason_code="FANOUT_FAILED", hint=None):
        Exception.__init__(self, message)
        self.message = message
        self.reason_code = reason_code
        self.hint = hint


def _copper_layers(board):
    out = []
    for layer in (board.stackup or []):
        name = layer.get("name")
        if name and name.endswith(".Cu") and name not in out:
            out.append(name)
    if not out:
        for fp in board.footprints:
            for pad in fp.get("pads") or []:
                for l in (pad.get("layers") or []):
                    if l.endswith(".Cu") and l not in out and "/" not in l:
                        out.append(l)
    order = ["F.Cu", "In1.Cu", "In2.Cu", "In3.Cu", "In4.Cu", "B.Cu"]
    return sorted(out, key=lambda n: (order.index(n) if n in order else 99, n))


def _pad_is_copper(pad):
    """A drilled hole with no annular ring is not a place to escape from."""
    hole = pad.get("hole_mm")
    if (pad.get("type") == "np_thru_hole" and hole
            and float(pad.get("w") or 0) <= float(hole) + 1e-6
            and float(pad.get("h") or 0) <= float(hole) + 1e-6):
        return False
    return True


def _pitch_of(pads):
    """The array pitch, read off the pad coordinates - or None if it is not a grid.

    A BGA's pads sit on a lattice, so every gap between two distinct coordinates
    is a whole number of pitches and both axes share the same pitch. Anything
    else (a random scatter, a two-row connector) is refused rather than fanned
    out at a made-up pitch: escape geometry is measured in pitch units, so a
    wrong pitch is a wrong fan-out that looks perfectly fine in the file.
    """
    smalls = []
    for axis in ("x", "y"):
        vals = sorted({round(float(p[axis]), 4) for p in pads
                       if p.get(axis) is not None})
        gaps = [round(b - a, 4) for a, b in zip(vals, vals[1:])]
        gaps = [g for g in gaps if g > 1e-6]
        if not gaps:
            return None                    # one row or one column is not an array
        small = min(gaps)
        if not all(abs(g / small - round(g / small)) < 0.01 for g in gaps):
            return None
        smalls.append(small)
    if abs(smalls[0] - smalls[1]) > 0.01 * min(smalls):
        return None
    return min(smalls)


def _ladder(u0, directions):
    """The radial direction, then rotations of it by up to (directions//2) x 45.

    A ball on the array's centre line has no radial direction; the caller passes
    +x for it. Escaping straight along the radius is both the shortest way out
    and the way that keeps the stub inside the ball's own channel, which is why
    it is tried first and the rotations after it.
    """
    out = []
    for k in range(max(1, int(directions))):
        if not k:
            out.append(u0)
            continue
        ang = math.radians(45.0 * ((k + 1) // 2) * (1 if k % 2 else -1))
        ca, sa = math.cos(ang), math.sin(ang)
        out.append((u0[0] * ca - u0[1] * sa, u0[0] * sa + u0[1] * ca))
    return out

def _escape_shapes(grid, px, py, u, escape, own, others, net, t_w, v_d,
                   pad_half):
    """Every escape shape this module knows for one direction, best first.

    Returns `[(steps, [])]` for a shape that passed the exact geometry, or
    `(None, [why, ...])` for one that did not - the refusal text is carried out
    to `skipped` so the caller can see which wall the ball hit, not just that a
    ball is buried.

    Two shapes exist, and the second is the standard dogbone:

    * ``direct`` - one stub outwards from the pad, via on its end;
    * ``dogbone`` - track from the pad to a via just clear of the pad, then the
      rest of the escape on another copper layer. This is the only way a ball
      that is *surrounded* by other balls can leave the array, because its own
      layer's channel is occupied by its neighbours while the far layer is empty.
    """
    steps = []
    why = []
    ex, ey = px + u[0] * escape, py + u[1] * escape
    if not grid.seg_ok((px, py), (ex, ey), own, net, t_w):
        why.append("direct stub blocked on %s" % own)
    elif not grid.via_ok(ex, ey, net):
        why.append("via does not fit at the end of a direct stub")
    else:
        steps = [("track", own, (px, py), (ex, ey)), ("via", ex, ey)]
        return [(steps, [])]

    if not others:
        why.append("no second copper layer for a dogbone")
        return [(None, why)]

    # the via has to sit clear of the ball it belongs to, or it is via-in-pad
    # wearing a dogbone's name - and this module does not do via-in-pad.
    floor = pad_half + v_d / 2.0 + 0.02
    cands = []
    for d in (floor, escape * 0.5, escape * 0.75, escape, escape * 1.5):
        d = round(float(d), 4)
        if d >= floor - 1e-9 and d not in cands:
            cands.append(d)
    for d in cands:
        vx, vy = px + u[0] * d, py + u[1] * d
        if not grid.seg_ok((px, py), (vx, vy), own, net, t_w):
            why.append("dogbone stub blocked on %s at %.2f mm" % (own, d))
            continue
        if not grid.via_ok(vx, vy, net):
            why.append("dogbone via does not fit at %.2f mm" % d)
            continue
        for alt in others:
            ax, ay = vx + u[0] * escape, vy + u[1] * escape
            if not grid.seg_ok((vx, vy), (ax, ay), alt, net, t_w):
                continue
            return [([("track", own, (px, py), (vx, vy)), ("via", vx, vy),
                      ("track", alt, (vx, vy), (ax, ay))], [])]
        why.append("dogbone escape blocked on every other copper layer")
    return [(None, why)]

def find_arrays(board, min_pads=9, max_pitch=None):
    """Footprints whose pads sit on a lattice - the candidates for a fan-out."""
    out = []
    for fp in board.footprints:
        pads = [p for p in (fp.get("pads") or []) if _pad_is_copper(p)]
        if len(pads) < min_pads:
            continue
        pitch = _pitch_of(pads)
        if pitch is None or pitch <= 0:
            continue
        if max_pitch is not None and pitch > max_pitch + 1e-9:
            continue
        xs = [float(p["x"]) for p in pads]
        ys = [float(p["y"]) for p in pads]
        out.append({"ref": fp.get("ref") or fp.get("reference"),
                    "pads": len(pads), "pitch_mm": round(pitch, 4),
                    "centre": [round((min(xs) + max(xs)) / 2.0, 4),
                               round((min(ys) + max(ys)) / 2.0, 4)],
                    "bbox_mm": [round(min(xs), 4), round(min(ys), 4),
                                round(max(xs), 4), round(max(ys), 4)]})
    return out


def fanout(board, ref=None, escape_mm=None, pitch_mm=0.1, layer=None,
           clearance_mm=None, track_mm=None, via_diameter_mm=None,
           via_drill_mm=None, min_pads=9, max_pitch=None, max_balls=None,
           directions=5, ignore_zone_areas=False):
    """Plan an escape for every ball of the array(s). Returns ops, never writes."""
    if ref:
        targets = [fp for fp in find_arrays(board, min_pads=1, max_pitch=max_pitch)
                   if fp["ref"] == ref]
        if not targets:
            raise FanoutError(
                "no footprint %r has its pads on a lattice, so there is no array "
                "to fan out" % ref, "NO_ARRAY",
                hint="pass the reference of the BGA; find_arrays() lists the ones "
                     "this server can see")
    else:
        targets = find_arrays(board, min_pads=min_pads, max_pitch=max_pitch)
        if not targets:
            raise FanoutError(
                "no footprint on this board has %d or more pads on a regular "
                "lattice, so nothing looks like a ball grid array" % min_pads,
                "NO_ARRAY")

    bundle = R.resolve(board, clearance_mm=clearance_mm, track_mm=track_mm,
                       via_diameter_mm=via_diameter_mm, via_drill_mm=via_drill_mm)
    copper = _copper_layers(board)
    if not copper:
        raise FanoutError("this board declares no copper layers", "NO_GEOMETRY")
    grid_layers = list(copper)
    grid = Grid(board, grid_layers, float(pitch_mm), bundle,
                ignore_zone_areas=ignore_zone_areas)

    by_ref = {}
    for fp in board.footprints:
        by_ref[fp.get("ref") or fp.get("reference")] = fp

    ops = []
    skipped = []
    styles = {"direct": 0, "dogbone": 0}
    done = 0
    for target in targets:
        fp = by_ref.get(target["ref"]) or {}
        pads = [p for p in (fp.get("pads") or []) if _pad_is_copper(p)]
        cx, cy = target["centre"]
        pitch = target["pitch_mm"]
        escape = float(escape_mm) if escape_mm else pitch
        spec = R.for_net(bundle, None)
        t_w = float(spec["track_mm"])
        v_d = float(spec["via_diameter_mm"])
        v_drill = float(spec["via_drill_mm"])
        for pad in sorted(pads, key=lambda p: (round(float(p["y"]), 4),
                                              round(float(p["x"]), 4))):
            if max_balls is not None and done >= int(max_balls):
                skipped.append({"reference": target["ref"],
                                "pad": pad.get("number"),
                                "reason": "max_balls reached"})
                continue
            net = pad.get("net")
            number = pad.get("number")
            if not net:
                skipped.append({"reference": target["ref"], "pad": number,
                                "reason": "pad has no net"})
                continue
            raw_layers = pad.get("layers") or []
            if "*.Cu" in raw_layers:
                pad_layers = list(grid_layers)
            else:
                pad_layers = [l for l in grid_layers if l in raw_layers]
            if not pad_layers:
                skipped.append({"reference": target["ref"], "pad": number,
                                "reason": "pad is on no copper layer the router "
                                          "knows"})
                continue
            use = layer if layer else pad_layers[0]
            if use not in grid_layers:
                raise FanoutError("layer %r is not a copper layer of this board "
                                  "(%s)" % (use, ", ".join(grid_layers)),
                                  "LAYER_NOT_IN_BOARD")
            px, py = float(pad["x"]), float(pad["y"])
            # radially outwards from the array centre; a ball on the centre line
            # has no radial direction, so the ladder starts at +x for it
            dx, dy = px - cx, py - cy
            d = math.hypot(dx, dy)
            if d < 1e-9:
                dx, dy, d = 1.0, 0.0, 1.0
            u0 = (dx / d, dy / d)
            others = [l for l in reversed(grid_layers) if l != use]
            pad_half = max(float(pad.get("w") or 0.0), float(pad.get("h") or 0.0)) / 2.0
            steps = None
            tried = []
            for u in _ladder(u0, directions):
                shapes = _escape_shapes(grid, px, py, u, escape, use, others, net,
                                        t_w, v_d, pad_half)
                for shape, why in shapes:
                    tried.extend(why)
                    if shape:
                        steps = shape
                        break
                if steps:
                    break
            if not steps:
                skipped.append({"reference": target["ref"], "pad": number,
                                "net": net,
                                "reason": "; ".join(sorted(set(tried)))
                                or "no direction fits"})
                continue
            styles["dogbone" if len(steps) > 2 else "direct"] += 1
            for step in steps:
                if step[0] == "track":
                    # NOT `layer`: that is the function parameter (the caller's
                    # "put every escape on this layer"), and rebinding it here
                    # made every ball after the first dogbone escape on B.Cu.
                    _kind, seg_layer, a, b = step
                    ops.append({"op": "add_track", "x1": round(a[0], 4),
                                "y1": round(a[1], 4), "x2": round(b[0], 4),
                                "y2": round(b[1], 4), "width": t_w,
                                "layer": seg_layer, "net": net})
                    # the next ball has to escape around this one
                    grid.add_track(net, seg_layer, a, b, t_w)
                else:
                    _kind, vx, vy = step
                    ops.append({"op": "add_via", "x": round(vx, 4),
                                "y": round(vy, 4), "size": v_d,
                                "drill": v_drill, "net": net})
                    grid.add_via(net, vx, vy, v_d)
            done += 1
    advice = None
    if skipped:
        spec = R.for_net(bundle, None)
        advice = (
            "%d of %d balls stay buried. The escape is judged with the SAME "
            "net-class geometry the router uses (%0.2f mm via, %0.2f mm drill, "
            "%0.2f mm clearance, %0.2f mm track), so a buried ball is a ball this "
            "stack cannot get out - not a ball this module gave up on. Options, "
            "in the order worth trying: a smaller via (via_diameter_mm / "
            "via_drill_mm), a dogbone on a layer that is still empty here, or a "
            "via in the pad itself for the interior balls (op add_via_in_pad, "
            "which needs a filled/plugged via from the fab)."
            % (len(skipped), sum(a["pads"] for a in targets),
               float(spec["via_diameter_mm"]), float(spec["via_drill_mm"]),
               float(bundle["clearance_mm"]), float(spec["track_mm"])))
    return {"arrays": targets, "balls_total": sum(a["pads"] for a in targets),
            "escaped": done, "skipped": skipped, "ops": ops, "styles": styles,
            "advice": advice,
            "rules": {"clearance_mm": float(bundle["clearance_mm"]),
                      "track_mm": float(R.for_net(bundle, None)["track_mm"]),
                      "via_mm": [float(R.for_net(bundle, None)["via_diameter_mm"]),
                                 float(R.for_net(bundle, None)["via_drill_mm"])],
                      "sources": bundle.get("sources"),
                      "assumed": bundle.get("assumed")},
            "method": ("for every ball, try a stub radially outwards from the array "
                       "centre and then rotations of it by up to %d x 45 degrees; "
                       "for each direction try a direct stub (via on its end) and "
                       "then a dogbone (via clear of the pad, rest of the escape on "
                       "another copper layer). Every candidate is judged by "
                       "route.grid's exact geometry against the copper that already "
                       "exists PLUS the escapes accepted before it, and an accepted "
                       "escape is never ripped up - so ball order matters and the "
                       "result is one valid fan-out, not the smallest one."
                       % (int(directions) // 2))}
