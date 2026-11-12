"""A plane pair out of the board file: where does this board ring?

`plane_resonance` has answered "where are the modes" since long ago, but it takes
`a_mm x b_mm x er` - three numbers you type. On a real board they are all in the
file: the plates are two copper layers, the resonator is the board itself, and
the dielectric constant is between those two layers. This module reads them.

What it deliberately does NOT do:
  * it does not pretend the board is a rectangle. The mode formula is for a
    rectangle, so the cavity is the OUTLINE'S BOUNDING BOX and the answer says
    so, with the rectangle's area beside the outline's - a board that is 30 %
    empty in its corner rings like the box, not like its copper.
  * it does not invent `er` when the stackup has none. A missing dielectric
    constant is a refusal: substituting 4.3 would silently set every mode.
  * it does not judge. A mode is not a problem without an excitation and a
    coupling, and neither is in the file (via_fence / plane_q are separate
    questions, and they stay separate).
"""
from __future__ import annotations

import math

from ..calc import cavity as _C


def _err(message, code, hint=None, extra=None):
    out = {"status": "error", "reason_code": code, "message": message}
    if hint:
        out["hint"] = hint
    if extra:
        out["extra"] = extra
    return out


def _num(x):
    try:
        return None if x is None else float(x)
    except (TypeError, ValueError):
        return None


def outline_box(outline):
    """(x0, y0, width, height) of the board outline, or None.

    `outline` is one ring of points at this layer's API; the bounding box is the
    rectangle the cavity formula can actually use.
    """
    pts = []
    for ring in (outline or []):
        if isinstance(ring, (list, tuple)) and ring and isinstance(ring[0], (list, tuple)) \
                and len(ring[0]) == 2 and isinstance(ring[0][0], (int, float)):
            pts.extend(ring)
        elif isinstance(ring, (list, tuple)) and len(ring) == 2 \
                and all(isinstance(v, (int, float)) for v in ring):
            pts.append(ring)
    if len(pts) < 3:
        return None
    xs = [float(p[0]) for p in pts]
    ys = [float(p[1]) for p in pts]
    return (min(xs), min(ys), max(xs) - min(xs), max(ys) - min(ys))


def _ring_area(pts):
    n = len(pts)
    if n < 3:
        return 0.0
    s = 0.0
    for i in range(n):
        x1, y1 = pts[i]
        x2, y2 = pts[(i + 1) % n]
        s += x1 * y2 - x2 * y1
    return abs(s) / 2.0


def _rings(shape):
    """Every ring in a shape, whatever nesting the adapter used.

    The file's zone outline arrives as one ring of points, board.outline as a
    list of rings, and holes nest one deeper again; a resolver that assumes one
    of those silently reports zero area for the others (measured: the same
    board's GND pour read as 0 mm^2 until this walked the nesting).
    """
    out = []
    if not isinstance(shape, (list, tuple)) or not shape:
        return out
    if len(shape) == 2 and all(isinstance(v, (int, float)) for v in shape):
        return out          # a lone point is not a ring
    if all(isinstance(p, (list, tuple)) and len(p) == 2
           and all(isinstance(v, (int, float)) for v in p) for p in shape):
        return [[(float(p[0]), float(p[1])) for p in shape]]
    for item in shape:
        out.extend(_rings(item))
    return out


def outline_area(outline):
    total = 0.0
    for pts in _rings(outline):
        if len(pts) >= 3:
            total += _ring_area(pts)
    return total


def copper_layers(board):
    return [l["name"] for l in (board.stackup or []) if l.get("type") == "copper"]


def layer_dielectric(board, layer_a, layer_b):
    """Thickness and effective er BETWEEN two copper layers, plus what was found.

    The plates are the two copper foils; everything between them is dielectric.
    With more than one dielectric sublayer, er is the series-average
    (sum t / sum(t/er)) - the one that reproduces the same capacitance, which is
    what a cavity mode depends on.
    """
    stack = board.stackup or []
    names = [l.get("name") for l in stack]
    if layer_a not in names or layer_b not in names:
        return None
    i, j = names.index(layer_a), names.index(layer_b)
    if i > j:
        i, j = j, i
    total_mm, series, missing = 0.0, 0.0, []
    for l in stack[i + 1:j]:
        if l.get("type") == "copper":
            continue
        t = _num(l.get("thickness_mm"))
        if not t or t <= 0:
            continue
        er = _num(l.get("er"))
        total_mm += t
        if er and er > 0:
            series += t / er
        else:
            missing.append(l.get("name"))
    if total_mm <= 0:
        return None
    return {"thickness_mm": total_mm, "er": (total_mm / series) if series else None,
            "missing_er_in": missing, "layers_between": j - i - 1}


def plane_coverage(board, layer, box):
    """How much of the outline's box a layer's pours cover, and the biggest net.

    A "plate" is a pour, and the file says which net and how big. Keepouts are
    not copper. Coverage is measured against the bounding box, so 1.0 means the
    layer is copper everywhere the cavity formula assumes it is.
    """
    if not box:
        return None
    box_area = box[2] * box[3]
    if box_area <= 0:
        return None
    area, by_net = 0.0, {}
    for z in (board.zones or []):
        if z.get("is_keepout"):
            continue
        lays = z.get("layers") or ([z.get("layer")] if z.get("layer") else [])
        if layer not in lays and "*" not in [str(l) for l in lays]:
            continue
        a = outline_area(z.get("outline"))
        if a <= 0:
            continue
        area += a
        net = str(z.get("net") or "")
        by_net[net] = by_net.get(net, 0.0) + a
    if not by_net:
        return None
    net, biggest = max(by_net.items(), key=lambda kv: kv[1])
    return {"coverage_of_box": min(1.0, area / box_area),
            "biggest_net": net, "biggest_net_area_mm2": biggest,
            "copper_area_mm2": area}


def _pick_pair(board, box, args):
    """The two layers the caller named, or the two whose pours cover most."""
    cu = copper_layers(board)
    a = args.get("layer_a")
    b = args.get("layer_b")
    if a or b:
        if not (a and b):
            return None, _err("give both layer_a and layer_b, or neither: a plane "
                              "pair is two layers", "ARG_MISSING")
        a, b = str(a), str(b)
        if a == b:
            return None, _err("layer_a and layer_b are the same layer", "ARG_INVALID")
        for name in (a, b):
            if name not in cu:
                return None, _err("%r is not a copper layer; the stackup has %s"
                                  % (name, ", ".join(cu)), "NOT_FOUND")
        return (a, b, False), None
    scored = []
    for name in cu:
        cov = plane_coverage(board, name, box)
        if cov:
            scored.append((cov["coverage_of_box"], name, cov))
    scored.sort(reverse=True)
    found = [{"layer": n, "coverage": c["coverage_of_box"]} for _, n, c in scored]
    if not cu:
        # Nothing in the stackup at all: this is a different failure from "poured
        # on one layer only", and the message has to say which one it is.
        return None, _err(
            "this file states no stackup, so it names neither the copper layers "
            "nor the dielectric between them; a plane pair and an er are both "
            "missing, and neither is something to invent", "NOT_APPLICABLE",
            hint="give layer_a/layer_b and er explicitly if you know the stack"
                 "the board was built on",
            extra={"copper_layers": [], "pours_found": found})
    if len(scored) < 2:
        return None, _err(
            "this board does not name two copper layers with pours on them, so "
            "there is no plane pair to find; name layer_a/layer_b if the plates "
            "are your assumption rather than the file's", "NOT_APPLICABLE",
            extra={"copper_layers": cu, "pours_found": found})
    # A plate with a hole in it is one thing; a layer that is 0.2 % copper is not
    # a plate at all, and a mode computed over it is a mode of some other
    # resonator. Measured on a real demo board (complex_hierarchy): the second
    # best layer carries a single small pour, and scoring alone would happily
    # name it as the other side of a cavity.
    if scored[1][0] < 0.5:
        return None, _err(
            "the second best poured layer (%r) covers only %.1f %% of the box, "
            "which is not a plate; name layer_a/layer_b if you mean it anyway"
            % (scored[1][1], 100.0 * scored[1][0]), "NOT_APPLICABLE",
            extra={"copper_layers": cu, "pours_found": found})
    return (scored[0][1], scored[1][1], True), None


def board_plane_resonance(board, args):
    """Cavity modes of the board's own plane pair."""
    box = outline_box(board.outline)
    if not box:
        return _err("the board has no closed outline, so there is no cavity; a "
                    "plane pair is a resonator whose walls are the board edge",
                    "NOT_APPLICABLE")
    if box[2] <= 0 or box[3] <= 0:
        return _err("the outline's bounding box is degenerate", "ARG_INVALID",
                    extra={"box": list(box)})

    pair, bad = _pick_pair(board, box, args)
    if bad:
        return bad
    layer_a, layer_b, auto = pair

    stack = layer_dielectric(board, layer_a, layer_b)
    if not stack:
        return _err("%r and %r are the same layer or have nothing between them"
                    % (layer_a, layer_b), "ARG_INVALID")
    er = _num(args.get("er"))
    note_er = None
    if er is None:
        er = stack["er"]
        if not er:
            return _err("the stackup between %r and %r has no dielectric constant, "
                        "so every mode would be a number you did not give"
                        % (layer_a, layer_b), "ARG_MISSING",
                        hint="pass er explicitly, or the layer names whose pours "
                             "are the plates",
                        extra={"layers_between": stack["layers_between"],
                               "missing_er_in": stack["missing_er_in"]})
        note_er = ("er = %.4g is the series average (sum t / sum(t/er)) of the "
                   "%.4g mm between the plates" % (er, stack["thickness_mm"]))
    else:
        if stack["missing_er_in"]:
            note_er = ("the stackup has no er for %s; the %.4g you passed is used"
                       % (", ".join(stack["missing_er_in"]), er))

    out = _C.plane_resonance(box[2], box[3], er=er,
                             m_max=int(args.get("m_max") or 2),
                             n_max=int(args.get("n_max") or 2),
                             f_operate_hz=_num(args.get("f_operate_hz")),
                             top=int(args.get("top") or 12))
    if out.get("status") != "ok":
        return out

    cov_a = plane_coverage(board, layer_a, box)
    cov_b = plane_coverage(board, layer_b, box)
    rows = out.get("value") if isinstance(out.get("value"), list) else []
    info = {
        "layer_a": layer_a, "layer_b": layer_b, "pair_from_file": auto,
        "box": {"x_mm": box[0], "y_mm": box[1],
                "width_mm": box[2], "height_mm": box[3],
                "area_mm2": box[2] * box[3]},
        "outline_area_mm2": outline_area(board.outline),
        "dielectric_mm": stack["thickness_mm"],
        "er": er,
        "er_from_stackup": stack["er"],
        "pour_a": cov_a, "pour_b": cov_b,
        "cavity_is_the_box": True,
    }
    # `plane_resonance` flattens its extras to the top of the envelope, so the
    # mode list is moved under `value` and the rest goes with the board block:
    # one place to read a mode, one place to read where the plates came from.
    out["value"] = {
        "modes": rows,
        "lowest_mode": out.pop("lowest_mode", None),
        "mode_count_returned": out.pop("mode_count_returned", None),
        "uniform_field_below_hz": out.pop("cavity_is_uniform_field_below_hz",
                                          None),
        "operating_point": out.pop("operating_point", None),
        "board": info,
    }
    out.pop("modes", None)

    notes = out.setdefault("notes", [])
    if not isinstance(notes, list):
        notes = out["notes"] = []
    notes.append(
        "the cavity is the outline's BOUNDING BOX (%.4g x %.4g mm, %.6g mm^2); the "
        "outline itself is %.6g mm^2, so %.1f %% of the box is board. A rectangle's "
        "modes are what the formula knows, and a smaller board rings higher"
        % (box[2], box[3], box[2] * box[3], info["outline_area_mm2"],
           100.0 * info["outline_area_mm2"] / (box[2] * box[3])))
    if note_er:
        notes.append(note_er)
    for name, cov in ((layer_a, cov_a), (layer_b, cov_b)):
        if cov is None:
            notes.append("%r has no pour in the file, so its status as a plate is "
                         "your assumption" % name)
        elif cov["coverage_of_box"] < 0.9:
            notes.append("%r is poured over %.1f %% of the box (net %r), so the "
                         "plate has holes where the mode assumes copper"
                         % (name, 100.0 * cov["coverage_of_box"],
                            cov["biggest_net"]))
    notes.append("this is a mode LOCATION, not a problem: a resonance needs an "
                 "excitation and a coupling, and neither is in the file. Via pitch "
                 "is cavity_via_fence, damping is cavity_plane_q")
    out["basis"] = (out.get("basis") or "") + (
        "; the plates, the gap and er come from the .kicad_pcb (two copper layers, "
        "the stackup between them, the pours on each), the extent is the outline box")
    out.setdefault("assumptions", {})
    out["assumptions"]["board_plane_resonance"] = (
        "rectangular cavity over the outline's bounding box; er is the "
        "capacitance-equivalent average of the stackup between the plates; the "
        "side walls are PMC as in plane_resonance")
    return out
