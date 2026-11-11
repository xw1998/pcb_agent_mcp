"""The field on the board's own copper: J, and where.

`current_density_field` solves div(sigma*grad V) = 0 on a region YOU draw, and
that is the right primitive - the 1-D answer (J = I/(w*t)) is correct on a
straight run and wrong exactly where boards fail: a bend, a width step, a
pad-to-track funnel. What was missing is the same thing `134` fixed for heat:
the region (and the contacts) coming out of the FILE.

So this module hands the field solver the copper of one net on one layer,
taken from `board_ir_drop(return_network=True)` - the same graph the resistance
and the heat are solved on - and drives it between two pads the caller names.

Two numbers are reported side by side rather than one being picked: the field's
own resistance and `board_ir_drop`'s. They are NOT the same measurement and do
not have to agree: the IR drop is one resistor per edge with the current spread
evenly across the track's width, so it cannot see the funnel at a pad; the
field sees the funnel and charges for the grid. When they disagree by a lot,
one of them is wrong - which is why both are printed with their difference.
"""
from __future__ import annotations

from .. import oracle as _O
from ..calc import currentfield as _CF

# Copper at 20 C, ohm*mm; the field solver's own default, restated here because
# the temperature argument has to move it.
_RHO_MM_20 = 1.724e-5
_ALPHA = 0.00393


def _err(message, code, hint=None, extra=None):
    out = {"status": "error", "reason_code": code, "message": message}
    if hint:
        out["hint"] = hint
    if extra:
        out["extra"] = extra
    return out


def _pad_shapes(board, net, layer):
    """The pads of `net` that have copper on `layer`, as rectangles.

    A pad is copper in the file, so it goes into the region: without it a track
    ending at a pad is a strip with a gap under the contact, and the funnel that
    the field was asked about is missing. Rotation is honoured for the 90/270
    case that swaps the sides; an arbitrary angle is reported as an
    approximation instead of being silently squared up.
    """
    out, approx = [], []
    for fp in (board.footprints or []):
        for pad in (fp.get("pads") or []):
            if str(pad.get("net") or "") != net:
                continue
            lays = pad.get("layers") or []
            if not ("*.Cu" in lays or layer in lays):
                continue
            w = float(pad.get("w") or 0.0)
            h = float(pad.get("h") or 0.0)
            if not (w > 0 and h > 0):
                continue
            rot = float(pad.get("rotation") or 0.0) % 180.0
            if abs(rot - 90.0) < 1e-9:
                w, h = h, w
            elif rot:
                approx.append("%s.%s is rotated %.1f deg and is squared up"
                              % (fp.get("ref"), pad.get("number"), rot))
            x, y = float(pad.get("x")), float(pad.get("y"))
            out.append({"pad": "%s.%s" % (fp.get("ref"), pad.get("number")),
                        "x_mm": x, "y_mm": y, "w_mm": w, "h_mm": h,
                        "polygon": [[x - w / 2.0, y - h / 2.0],
                                    [x + w / 2.0, y - h / 2.0],
                                    [x + w / 2.0, y + h / 2.0],
                                    [x - w / 2.0, y + h / 2.0]]})
    return out, approx


def board_current_field(board, args):
    """J and R on one net's copper, driven between two named pads."""
    net = str(args.get("net") or "")
    if not net:
        return _err("net is required: the field is solved on one net's copper",
                    "ARG_MISSING")
    src = args.get("source_pad")
    snk = args.get("sink_pad")
    if not src or not snk:
        return _err("both source_pad and sink_pad are required: a field needs two "
                    "contacts, and which two is a design question, not a property "
                    "of the file", "ARG_MISSING",
                    hint="pads are named REF.number (U1.1); board_nets lists the "
                         "pads on each net")
    src, snk = str(src), str(snk)
    if src == snk:
        return _err("source_pad and sink_pad are the same pad, so there is no "
                    "field", "ARG_INVALID")

    temp_c = float(args.get("temp_c") if args.get("temp_c") is not None else 20.0)
    g = _O.board_ir_drop(board, net, None, None,
                         copper_um=args.get("copper_um"),
                         plating_um=args.get("plating_um"),
                         quantize_mm=(args.get("quantize_mm")
                                      if args.get("quantize_mm") is not None
                                      else 0.01),
                         temp_c=temp_c,
                         max_nodes=int(args.get("max_nodes") or 60000),
                         return_network=True)
    if g.get("status") != "ok":
        return g
    val = g["value"]
    nodes = {n["id"]: n for n in val["nodes"]}
    term = {t["pad"]: t for t in (val["terminals"] or [])}
    for name in (src, snk):
        if name not in term:
            return _err("pad %r is not on net %r, so no field runs through it"
                        % (name, net), "ARG_INVALID",
                        hint="the pads on this net are: %s"
                             % ", ".join(sorted(term)[:12]))

    # One layer at a time: a net that hops layers through a via is two fields,
    # and merging them into one 2-D solve would be a board that does not exist.
    by_layer = {}
    for e in val["edges"]:
        if e.get("kind") != "track":
            continue
        lay = str(e.get("layer") or "")
        by_layer.setdefault(lay, []).append(e)
    layers = [l for l in sorted(by_layer, key=lambda l: -len(by_layer[l])) if l]
    if not layers:
        return _err("net %r has no track on any layer, so there is no region to "
                    "solve" % net, "NOT_APPLICABLE",
                    hint="a net made only of a pour has no 2-D region here; "
                         "board_ir_drop(model_zones=true) or board_pour_audit "
                         "answers about pours")
    layer = str(args.get("layer") or layers[0])
    if layer not in by_layer:
        return _err("net %r has no track on %r; it has copper on %s"
                    % (net, layer, ", ".join(layers)), "NOT_APPLICABLE")
    for name in (src, snk):
        if layer not in (term[name].get("layers") or []) and "*.Cu" not in (
                term[name].get("layers") or []):
            return _err("pad %r has no copper on %r, so it cannot be a contact "
                        "there" % (name, layer), "NOT_CONNECTED")

    pads, approx = _pad_shapes(board, net, layer)
    at = {p["pad"]: p for p in pads}
    contacts = {}
    for name in (src, snk):
        if name in at:
            p = at[name]
            contacts[name] = {"x_mm": p["x_mm"], "y_mm": p["y_mm"],
                              "radius_mm": 0.4 * min(p["w_mm"], p["h_mm"])}
        else:
            t = term[name]
            contacts[name] = {"x_mm": float(t["x_mm"]), "y_mm": float(t["y_mm"]),
                              "radius_mm": 0.2}

    paths = []
    for e in by_layer[layer]:
        a, b = nodes.get(e["a"]), nodes.get(e["b"])
        if a is None or b is None:
            continue
        paths.append({"points": [[float(a["x_mm"]), float(a["y_mm"])],
                                 [float(b["x_mm"]), float(b["y_mm"])]],
                      "width_mm": float(e.get("width_mm") or 0.0)})
    paths = [p for p in paths if p["width_mm"] > 0]
    if not paths:
        return _err("the tracks on %r carry no width, so the region is degenerate"
                    % layer, "NOT_APPLICABLE")

    cu_um = float(args.get("copper_um") or val.get("copper_um")
                  or (by_layer[layer][0].get("copper_um") or 0.0))
    if not cu_um:
        return _err("neither the stackup nor the call states the copper thickness "
                    "of %r" % layer, "ARG_MISSING",
                    hint="copper_um is the sheet the field is averaged over")

    current_a = float(args.get("current_a") if args.get("current_a") is not None
                      else 1.0)

    # The grid has to RESOLVE the narrowest copper, and the solver's default
    # pitch (bounding box over the unknown budget) does not. Measured on a 30 mm
    # strip between 2 mm pads, against rho*L/(w*t): at width/3 the answer was
    # 25 % low, and at width/4 an intermediate pitch refused the region as
    # DISCONNECTED - a coarse mask that loses a thin track is not an error the
    # solver can report as accuracy. At width/6 and width/12 the same strip reads
    # 0.014016 / 0.013989 ohm (1 mm wide) and 0.027915 / 0.027892 (0.5 mm) for
    # an analytic 0.013989 / 0.027978, so the default pitch is width/6 and the
    # answer is CONFIRMED on a grid twice as fine before it is published.
    w_min = min([float(p["width_mm"]) for p in paths]
                + [min(p["w_mm"], p["h_mm"]) for p in pads] or [0.0])
    budget = int(args.get("max_unknowns") or 40000)
    area_mm2 = 0.0
    for p in paths:
        (x1, y1), (x2, y2) = p["points"]
        area_mm2 += p["width_mm"] * ((x2 - x1) ** 2 + (y2 - y1) ** 2) ** 0.5
    for p in pads:
        area_mm2 += p["w_mm"] * p["h_mm"]
    h_mm = args.get("h_mm")
    h_mm = float(h_mm) if h_mm is not None else w_min / 6.0
    need = area_mm2 / (h_mm * h_mm)
    if need > budget:
        return _err("this net cannot be resolved on %d unknowns: the narrowest "
                    "copper is %.3g mm, which needs a %.3g mm grid, and %.6g mm^2 "
                    "of copper at that pitch is %.3g cells"
                    % (budget, w_min, h_mm, area_mm2, need), "MESH_TOO_FINE",
                    hint="raise max_unknowns above %.0f, or give h_mm if a coarser "
                         "grid is acceptable (a grid coarser than width/4 can "
                         "disconnect a track and read low)" % need)

    def _solve(pitch):
        return _CF.current_density_field(
            paths=paths,
            polygons=[p["polygon"] for p in pads],
            thickness_mm=cu_um / 1000.0,
            rho_ohm_mm=_RHO_MM_20 * (1.0 + _ALPHA * (temp_c - 20.0)),
            source=contacts[src], sink=contacts[snk],
            current_a=current_a,
            h_mm=pitch,
            max_unknowns=budget,
            levels=int(args.get("levels") or 4),
            frequency_hz=args.get("frequency_hz"))

    out = _solve(h_mm)
    if out.get("status") != "ok":
        return out

    # The finer grid is the answer; the coarse one is the check on it. When the
    # fine grid cannot be afforded or cannot solve, the coarse number is
    # published with is_grid_convergent false rather than as if it were confirmed.
    conv = {"h_mm": h_mm, "h_fine_mm": None,
            "resistance_ohm": (out.get("value") or {}).get("resistance_ohm"),
            "resistance_fine_ohm": None, "relative_change": None,
            "is_grid_convergent": False}
    fine = None
    if 4.0 * need <= 4.0 * budget:
        fine = _solve(h_mm / 2.0)
    if fine is not None and fine.get("status") == "ok":
        fv = fine.get("value") or {}
        conv["h_fine_mm"] = h_mm / 2.0
        conv["resistance_fine_ohm"] = fv.get("resistance_ohm")
        r0 = float(conv["resistance_ohm"] or 0.0)
        r1 = float(conv["resistance_fine_ohm"] or 0.0)
        if r0 > 0.0:
            conv["relative_change"] = (r1 - r0) / r0
            conv["is_grid_convergent"] = abs(conv["relative_change"]) < 0.03
        out = fine
    elif fine is not None:
        conv["fine_refused"] = {"reason_code": fine.get("reason_code"),
                                "message": fine.get("message")}

    fd = out.get("value") or {}
    # The 1-D answer on the same pair, so the two are read together: they are
    # different measurements and the difference is the point.
    one_d = _O.board_ir_drop(board, net, src, [snk], current_a=current_a,
                             copper_um=args.get("copper_um"),
                             plating_um=args.get("plating_um"),
                             quantize_mm=(args.get("quantize_mm")
                                          if args.get("quantize_mm") is not None
                                          else 0.01),
                             temp_c=temp_c,
                             max_nodes=int(args.get("max_nodes") or 60000))
    cmp = None
    if one_d.get("status") == "ok":
        ov = one_d["value"]
        r_1d = (float(ov.get("max_drop_mv") or 0.0) / 1000.0) / current_a
        r_fd = float(fd.get("resistance_ohm") or 0.0)
        cmp = {
            "resistance_field_ohm": r_fd,
            "resistance_1d_ohm": r_1d,
            "relative_difference": ((r_fd - r_1d) / r_1d if r_1d else None),
            "peak_j_field_a_per_mm2": fd.get("peak_j_a_per_mm2"),
            "peak_j_1d_a_per_mm2": (ov.get("worst_current_density") or {})
                                   .get("current_density_a_per_mm2"),
            "why_they_differ": (
                "the 1-D answer is one resistor per edge with the current spread "
                "evenly across the width, and a pad is a point, so it cannot see "
                "either the pad's own copper or the funnel into it. The field has "
                "both: expect a HIGHER peak J at the contact and, because the pad "
                "copper widens the path the 1-D graph charges for, often a LOWER "
                "resistance (ecc83, C1.1->P3.1 at 0.1 A: 0.007399 against "
                "0.008472 ohm, peak J 5.56 against 3.57 A/mm^2)"),
        }

    out.setdefault("value", {})
    out["value"]["board"] = {
        "net": net, "layer": layer, "layers_with_track": layers,
        "source_pad": src, "sink_pad": snk,
        "current_a": current_a, "copper_um": cu_um, "temp_c": temp_c,
        "paths": len(paths), "pad_polygons": len(pads),
        "nodes": val.get("node_count"), "edges": val.get("edge_count"),
        "contacts": contacts,
    }
    out["value"]["against_the_1d_answer"] = cmp
    out["value"]["grid_convergence"] = conv
    out.setdefault("notes", [])
    if isinstance(out.get("notes"), list):
        out["notes"].extend(approx)
        if len(layers) > 1:
            out["notes"].append(
                "net %r also has tracks on %s; those are a separate field and are "
                "not in this one (a 2-D solve is one layer)"
                % (net, ", ".join([l for l in layers if l != layer])))
        out["notes"].append("the region is the file's copper on one layer plus its "
                            "pads; vias, pours and the other layers of the same net "
                            "are not in it")
        if conv["is_grid_convergent"]:
            out["notes"].append(
                "the resistance is confirmed on a grid twice as fine: %.6g ohm at "
                "%.3g mm against %.6g at %.3g mm (%.1f %%)"
                % (conv["resistance_ohm"], conv["h_mm"],
                   conv["resistance_fine_ohm"], conv["h_fine_mm"],
                   100.0 * (conv["relative_change"] or 0.0)))
        else:
            out["notes"].append(
                "the resistance is NOT confirmed: the grid was not halved or the "
                "two grids disagree by more than 3 % (see value.grid_convergence). "
                "Treat the number as a shape, not a resistance")
    out["basis"] = (out.get("basis") or "") + (
        "; the region and the contacts come from the .kicad_pcb: tracks of one net "
        "on one layer as the graph publishes them, pads as rectangles, thickness "
        "from the stackup")
    out.setdefault("assumptions", {})
    out["assumptions"]["board_current_field"] = (
        "the current is yours to give; the two contacts are yours to name; one "
        "layer per answer")
    return out
