"""Currents in, heat out - on the same graph the IR drop is solved on.

A .kicad_pcb says where the copper is. It never says how much current runs in
it, and there is no honest way to read a current out of geometry. So the
currents are the caller's, given per PAD, and two things are checked before a
single number comes out:

  * Kirchhoff on every net: what enters must leave on the SAME net. A board
    file carries no component models, so "2 A in, nothing out" is not a hot
    trace, it is half a question - and answering half a question is how a
    thermal picture gets invented.

  * the graph has to solve at all. `resistive_network` refuses a singular one
    instead of returning a field that is only defined up to a constant.

The heat is I^2R on the edges `board_ir_drop` publishes, so a track's
dissipation and its IR drop are the same solve and cannot drift apart. The
plate itself is `board_electrothermal`, unchanged: what this module adds is
where the sources are and how much they make.
"""
from __future__ import annotations

from .. import oracle as _O
from ..calc import power as _PW
from ..calc import electrothermal as _ET

# Above this the dense Kirchhoff solve is not worth forming (it is O(n^3) and
# allocates an n x n list of lists); the same system goes to the CG solver.
_DENSE_MAX = 800


def _err(message, code, hint=None, extra=None):
    out = {"status": "error", "reason_code": code, "message": message}
    if hint:
        out["hint"] = hint
    if extra:
        out["extra"] = extra
    return out


def pad_nets(board):
    """{"U1.1": "GND", ...} - the only place a pad's net is written down."""
    idx = {}
    for fp in (board.footprints or []):
        ref = fp.get("ref")
        for pad in (fp.get("pads") or []):
            net = pad.get("net")
            if net:
                idx["%s.%s" % (ref, pad.get("number"))] = str(net)
    return idx


def _bbox(board):
    pts = []
    for ring in (board.outline or []):
        for p in ring:
            pts.append((float(p[0]), float(p[1])))
    if not pts:
        return None
    xs = [p[0] for p in pts]
    ys = [p[1] for p in pts]
    return {"x_mm": min(xs), "y_mm": min(ys),
            "width_mm": max(xs) - min(xs), "height_mm": max(ys) - min(ys)}


def dissipation(board, currents, copper_um=None, plating_um=None,
                quantize_mm=0.01, temp_c=20.0, model_zones=False,
                max_nodes=60000):
    """Every track segment's current and I^2R, from currents given per pad.

    `currents` = [{"at": "U1.1", "a": 2.0}, ...]; positive = the pad PUSHES
    current into the copper. On each net the sum must be zero, because a net
    that gains charge has nowhere to put it.

    Returns value with:
      nets       one row per solved net (injected, drawn, power)
      segments   one row per TRACK segment: end points, width, copper, current,
                 resistance and power - the plate's sources, already in board
                 coordinates (board_thermal shifts them)
      via_power  barrels: they dissipate, but a 2-D in-plane plate has nowhere
                 to put a vertical barrel, so they are reported and NOT placed
      other      sheet squares (model_zones) and pad plating that carried heat
      total_power_w
      refusals   one row per net that could not be solved, with its reason
    """
    if not isinstance(currents, (list, tuple)) or not currents:
        return _err("currents is required: [{at: pad, a: amps}, ...]; a board file "
                    "does not carry currents, so they cannot be derived",
                    "ARG_MISSING",
                    hint="positive a = that pad pushes current INTO the copper; on "
                         "each net the values must add up to zero, because a board "
                         "file has no component models, so nothing else can carry "
                         "the current back")

    nets = pad_nets(board)
    by_net = {}
    for i, row in enumerate(currents):
        if not isinstance(row, dict):
            return _err("currents[%d] is not an object" % i, "ARG_INVALID")
        at = str(row.get("at") or "")
        if not at:
            return _err("currents[%d] has no 'at' pad" % i, "ARG_MISSING",
                        hint="name a pad the way board_summary/board_components "
                             "name it: U1.1")
        try:
            a = float(row.get("a"))
        except (TypeError, ValueError):
            return _err("currents[%d].a is not a number: %r"
                        % (i, row.get("a")), "ARG_INVALID")
        if at in nets:
            net = nets[at]
        else:
            # A pad may exist and be on no net at all; say which case it is.
            on_no_net = False
            for fp in (board.footprints or []):
                for pad in (fp.get("pads") or []):
                    if "%s.%s" % (fp.get("ref"), pad.get("number")) == at:
                        on_no_net = True
            if on_no_net:
                return _err("pad %r is on no net in this file, so no current can "
                            "run from it" % at, "ARG_INVALID",
                            hint="a pad with no net is mechanical copper; the file "
                                 "has to assign the net before it can carry current")
            return _err("no pad named %r on this board" % at, "NOT_FOUND",
                        hint="pads are named REF.number (U1.1); board_components "
                             "lists them")
        by_net.setdefault(net, []).append((at, a, i))

    segments, vias, others, net_rows, refusals, islands = [], [], [], [], [], []
    total_w = 0.0
    for net in sorted(by_net):
        rows = by_net[net]
        tot = sum(a for _, a, _ in rows)
        mag = sum(abs(a) for _, a, _ in rows)
        if abs(tot) > max(1e-9, 1e-6 * mag):
            refusals.append({
                "net": net, "reason_code": "ARG_INVALID",
                "message": "the currents on net %r add up to %.6g A, not zero: a "
                           "DC solve on one net needs the current to come back "
                           "somewhere on the SAME net - a component that would "
                           "carry it to another net is not in the board file"
                           % (net, tot)})
            continue
        if mag == 0.0:
            continue

        g = _O.board_ir_drop(board, net, None, None, copper_um=copper_um,
                             plating_um=plating_um, quantize_mm=quantize_mm,
                             temp_c=temp_c, model_zones=model_zones,
                             max_nodes=max_nodes, return_network=True)
        if g.get("status") != "ok":
            refusals.append({"net": net,
                             "reason_code": g.get("reason_code") or "NOT_APPLICABLE",
                             "message": g.get("message")
                                        or "the net could not be extracted"})
            continue
        val = g["value"]
        pos = {n["id"]: (float(n["x_mm"]), float(n["y_mm"]))
               for n in val["nodes"]}
        term = {t["pad"]: t["node"] for t in val["terminals"]}
        # The graph is built without a source, so a pad whose net has no copper
        # reaching it still gets a node - a node with no edge at all. In a solve
        # that is a floating unknown and the matrix is singular (measured:
        # "singular at 2" on a board whose only sin was one unwired pad). It is
        # also exactly the pad a caller is most likely to put a current into, so
        # it is named instead of dropped.
        wired_nodes = set()
        for e in val["edges"]:
            wired_nodes.add(e["a"])
            wired_nodes.add(e["b"])
        injections, blind = {}, []
        for at, a, _i in rows:
            nd = term.get(at)
            if nd is None or nd not in wired_nodes:
                blind.append(at)
                continue
            injections[nd] = injections.get(nd, 0.0) + a
        if blind:
            refusals.append({
                "net": net, "reason_code": "NOT_CONNECTED",
                "message": "on net %r, %s reach no copper, so no current can be "
                           "put into them (no track and no via on that net)"
                           % (net, ", ".join(sorted(blind)))})
            continue
        ids = [n["id"] for n in val["nodes"] if n["id"] in wired_nodes]
        edges = [{"a": e["a"], "b": e["b"],
                  "resistance_ohm": e["resistance_ohm"]} for e in val["edges"]
                 if e["a"] in wired_nodes and e["b"] in wired_nodes]
        if not edges:
            refusals.append({"net": net, "reason_code": "NOT_APPLICABLE",
                             "message": "net %r has no conductor in the graph"
                                        % (net,)})
            continue
        # A net on a finished board is not always one piece of copper. Measured on
        # ecc83: net Net-(P3-P1) splits, and the dense solve died "singular at 11"
        # - an island is an unknown with no equation, not a defect of the board.
        # An island cannot carry current (nothing leads into it), so it is dropped
        # and counted. But if the currents themselves land on two pieces, no
        # current can flow at all: that is a refusal, not a zero.
        adj = {}
        for e in edges:
            adj.setdefault(e["a"], []).append(e["b"])
            adj.setdefault(e["b"], []).append(e["a"])
        comp, nc = {}, 0
        for n in ids:
            if n in comp:
                continue
            nc += 1
            comp[n] = nc
            todo = [n]
            while todo:
                k = todo.pop()
                for nb in adj.get(k, ()):
                    if nb not in comp:
                        comp[nb] = nc
                        todo.append(nb)
        live = sorted(set(comp[nd] for nd in injections if nd in comp))
        if len(live) > 1:
            refusals.append({
                "net": net, "reason_code": "NOT_CONNECTED",
                "message": "the pads you drove on net %r sit on %d separate "
                           "pieces of copper, so nothing can flow between them; "
                           "a pour or a track that joins them is missing from "
                           "the file" % (net, len(live))})
            continue
        if live:
            kept = set(n for n in ids if comp.get(n) == live[0])
            lost = [n for n in ids if n not in kept]
            if lost:
                islands.append({
                    "net": net, "nodes": len(lost),
                    "edges": sum(1 for e in edges
                                 if e["a"] not in kept or e["b"] not in kept)})
                ids = [n for n in ids if n in kept]
                edges = [e for e in edges
                         if e["a"] in kept and e["b"] in kept]
        ground = min(injections, key=lambda k: injections[k])
        if len(ids) <= _DENSE_MAX:
            sol, why = _PW.resistive_network(ids, edges, injections, ground)
        else:
            sol, why = _PW.resistive_network_cg(ids, edges, injections, ground)
            if sol is not None and not sol.get("converged"):
                refusals.append({
                    "net": net, "reason_code": "NOT_CONVERGED",
                    "message": "the %d-node solve on net %r stopped at residual "
                               "%.3g; a current from a non-converged field is noise"
                               % (len(ids), net, sol.get("residual_rel") or -1.0)})
                continue
        if sol is None:
            refusals.append({"net": net, "reason_code": "NOT_CONNECTED",
                             "message": "net %r could not be solved: %s"
                                        % (net, why)})
            continue

        geo = {(e["a"], e["b"]): e for e in val["edges"]}
        p_net = 0.0
        for c in sol["edges"]:
            p = abs(float(c["power_w"]))
            i_a = abs(float(c["current_a"]))
            p_net += p
            e = geo.get((c["a"], c["b"])) or {}
            kind = str(e.get("kind") or "?")
            if kind == "track":
                x1, y1 = pos.get(c["a"], (None, None))
                x2, y2 = pos.get(c["b"], (None, None))
                if x1 is None or x2 is None:
                    continue
                segments.append({
                    "net": net, "layer": e.get("layer"), "label": e.get("label"),
                    "x1_mm": x1, "y1_mm": y1, "x2_mm": x2, "y2_mm": y2,
                    "width_mm": e.get("width_mm"),
                    "copper_um": e.get("copper_um"),
                    "length_mm": e.get("length_mm"),
                    "resistance_ohm": e.get("resistance_ohm"),
                    "current_a": i_a, "power_w": p})
            elif kind == "via":
                x1, y1 = pos.get(c["a"], (None, None))
                vias.append({"net": net, "x_mm": x1, "y_mm": y1,
                             "label": e.get("label"), "current_a": i_a,
                             "power_w": p})
            else:
                others.append({"net": net, "kind": kind, "label": e.get("label"),
                               "current_a": i_a, "power_w": p})
        total_w += p_net
        net_rows.append({"net": net, "injected_a": sol["injected_a"],
                         "drawn_a": sol["drawn_a"], "nodes": len(ids),
                         "edges": len(edges), "power_w": p_net})

    if not net_rows:
        first = (refusals[0] if refusals else
                 {"reason_code": "NOT_APPLICABLE",
                  "message": "no net carried any current"})
        return _err(first["message"], first["reason_code"],
                    hint="value.refusals names every net that was refused, with "
                         "its own reason" if len(refusals) > 1 else None,
                    extra={"refusals": refusals} if refusals else None)

    # Largest slice first: the hot segments are what a thermal answer is about,
    # and the whole list goes to extra when the caller wants all of it.
    segments.sort(key=lambda s: -s["power_w"])
    return {"status": "ok", "value": {
        "nets": net_rows,
        "segments": segments[:200],
        "segment_count": len(segments),
        "via_power": sorted(vias, key=lambda v: -v["power_w"])[:50],
        "via_count": len(vias),
        "other_heat": sorted(others, key=lambda v: -v["power_w"])[:50],
        "other_count": len(others),
        "total_power_w": total_w,
        "track_power_w": sum(s["power_w"] for s in segments),
        "via_power_w": sum(v["power_w"] for v in vias),
        "other_power_w": sum(v["power_w"] for v in others),
        "refusals": refusals,
        "islands": islands,
    }}


def _rings(outline):
    """The polygon(s) of a zone, whichever shape the reader handed over.

    KiCad writes one outer ring plus optional cut-outs, and the adapter is free
    to flatten a single ring - so both [[(x,y),...]] and [(x,y),...] arrive here
    and a pour that silently became zero rectangles would be a board with no
    copper at all.
    """
    rings = []
    if not outline:
        return rings
    first = outline[0]
    if isinstance(first, (int, float)):
        rings.append([(float(outline[i]), float(outline[i + 1]))
                      for i in range(0, len(outline) - 1, 2)])
        return rings
    if isinstance(first, (tuple, list)) and first and isinstance(first[0], (int, float)):
        return [[(float(p[0]), float(p[1])) for p in outline]]
    for ring in outline:
        rings.append([(float(p[0]), float(p[1])) for p in ring])
    return rings


def _pour_rectangles(board, copper_um, pour_layers):
    """Every filled pour as ONE rectangle per ring, in board coordinates.

    A pour is a polygon and the plate takes rectangles, so this is the outline's
    own bounding box - exact for the full-board planes that are the common case
    and an OVER-estimate for an L-shaped pour, which is said out loud per zone
    rather than averaged away.
    """
    out, notes = [], []
    for z in (board.zones or []):
        if z.get("is_keepout") or z.get("keepout"):
            continue
        for lay in (z.get("layers") or []):
            lay = str(lay)
            if lay not in pour_layers:
                continue
            t_um = copper_um.get(lay)
            if not t_um:
                notes.append("a pour on %s was left out: the stackup does not say "
                             "how thick that layer is" % lay)
                continue
            for pts in _rings(z.get("outline")):
                if len(pts) < 3:
                    continue
                xs = [p[0] for p in pts]
                ys = [p[1] for p in pts]
                x1, x2 = min(xs), max(xs)
                y1, y2 = min(ys), max(ys)
                area_ring = abs(_ring_area(pts))
                area_box = (x2 - x1) * (y2 - y1)
                out.append({"x1_mm": x1, "y1_mm": y1, "x2_mm": x2, "y2_mm": y2,
                            "thickness_um": t_um, "layers": 1,
                            "net": z.get("net"), "layer": lay})
                if area_box > 0 and area_ring < 0.97 * area_box:
                    notes.append(
                        "pour on %s (net %s) covers %.0f%% of its bounding "
                        "rectangle; it is passed to the plate as that rectangle, "
                        "so this answer spreads heat through copper the board does "
                        "not have" % (lay, z.get("net"),
                                      100.0 * area_ring / area_box))
    return out, notes


def _ring_area(pts):
    s = 0.0
    for i in range(len(pts)):
        x1, y1 = pts[i]
        x2, y2 = pts[(i + 1) % len(pts)]
        s += x1 * y2 - x2 * y1
    return 0.5 * s


def board_thermal(board, args):
    """The whole chain: currents -> per-track I^2R -> the 2-D plate.

    Plate geometry, thickness and copper come from the FILE: the plate is the
    outline's bounding box, the thickness is the stackup's, and every filled
    pour on a copper layer becomes a spreading rectangle. The currents and the
    convection coefficient are the caller's, because neither is in the file.

    Refusals, all of them about half a circuit: currents that do not add up to
    zero on a net, a pad on no net, a pad that no copper reaches, and driven pads
    that sit on two separate pieces of the same net (nothing can flow between
    them). Copper pieces no driven pad reaches are islands: counted, named, and
    carrying no current, so they are not in the solve and add no heat.
    """
    h = args.get("h_w_m2k")
    if h is None:
        return _err("h_w_m2k is required: convection is the dominant loss on a "
                    "board in air and it is not in the file",
                    "ARG_MISSING",
                    hint="still air on a horizontal board is about 5-10 W/(m^2 K); "
                         "a fan moves it to 20-50. board_electrothermal takes the "
                         "same number for a bare plate")
    bbox = _bbox(board)
    if bbox is None:
        return _err("the board has no outline, so there is no plate to solve",
                    "NOT_APPLICABLE",
                    hint="a plate needs a width and a height; without Edge.Cuts "
                         "there is nothing to take them from")

    th = args.get("thickness_mm")
    if th is None:
        th = board.thickness_mm
    if not th:
        th = args.get("thickness_mm")
    if not th:
        return _err("the file does not state the board thickness and none was "
                    "given", "ARG_REQUIRED",
                    hint="thickness_mm is the thermal mass and the conduction "
                         "length; the stackup usually carries it")

    copper_um = {}
    for s in (board.stackup or []):
        if str(s.get("type") or "").lower() == "copper" and s.get("copper_um"):
            copper_um[str(s.get("name"))] = float(s["copper_um"])
    explicit_cu = args.get("copper_um")

    layer_stack = args.get("layer_stack")
    want_pours = args.get("copper_from_board")
    if want_pours is None:
        want_pours = True

    dis = dissipation(board, args.get("currents"),
                      copper_um=explicit_cu, plating_um=args.get("plating_um"),
                      quantize_mm=(args.get("quantize_mm")
                                   if args.get("quantize_mm") is not None else 0.01),
                      temp_c=(args.get("t_ambient_c")
                              if args.get("t_ambient_c") is not None else 25.0),
                      model_zones=bool(args.get("model_zones")),
                      max_nodes=int(args.get("max_nodes") or 60000))
    if dis.get("status") != "ok":
        return dis
    dv = dis["value"]

    x0, y0 = bbox["x_mm"], bbox["y_mm"]
    conductors = [{"x1_mm": s["x1_mm"] - x0, "y1_mm": s["y1_mm"] - y0,
                   "x2_mm": s["x2_mm"] - x0, "y2_mm": s["y2_mm"] - y0,
                   "width_mm": s["width_mm"],
                   "thickness_um": s["copper_um"],
                   "current_a": s["current_a"]}
                  for s in dv["segments"]
                  if s.get("width_mm") and s.get("copper_um")]
    if not conductors:
        return _err("no track carried current, so the plate would have no source",
                    "NOT_APPLICABLE",
                    hint="value.dissipation inside this message names what did "
                         "carry current (vias and pours are not track segments)")

    pours, pour_notes = [], []
    if want_pours and not layer_stack:
        thick = {}
        for lay, um in copper_um.items():
            thick[lay] = float(explicit_cu) if explicit_cu is not None else um
        pours, pour_notes = _pour_rectangles(board, thick, set(thick))
        for p in pours:
            p["x1_mm"] -= x0
            p["x2_mm"] -= x0
            p["y1_mm"] -= y0
            p["y2_mm"] -= y0

    # The plate's convergence tolerance is ABSOLUTE kelvin, and that default
    # (1e-4 K) was chosen for a part dumping watts. A board carrying 2.5 mW into
    # 10 W/(m^2 K) air rises by a tenth of a kelvin, so at 1e-4 K the energy
    # balance reports the LAST SWEEP instead of the model - measured on ecc83: a
    # 5.1 % residual that is not a defect of the board. The scale is therefore
    # taken from the physics the caller already gave: the uniform-plate rise
    # P/(2hA), four decades below it.
    _area_m2 = (bbox["width_mm"] * bbox["height_mm"]) * 1e-6
    _rise_k = (dv["total_power_w"] / (2.0 * float(h) * _area_m2)
               if (float(h) > 0.0 and _area_m2 > 0.0) else 0.0)
    _tol = float(args.get("tol_k") if args.get("tol_k") is not None else 1e-4)
    if _rise_k > 0.0:
        _tol = min(_tol, max(1e-9, 1e-5 * _rise_k))

    def _plate(tol_k):
        return _ET.board_electrothermal(
            bbox["width_mm"], bbox["height_mm"], float(th),
            float(args.get("k_w_mk") if args.get("k_w_mk") is not None else 0.3),
            float(h), conductors,
            t_ambient_c=(float(args["t_ambient_c"])
                         if args.get("t_ambient_c") is not None else 25.0),
            cell_mm=args.get("cell_mm"),
            edge_temp_c=args.get("edge_temp_c"),
            copper_pours=(pours or None),
            k_copper_w_mk=args.get("k_copper_w_mk"),
            emissivity=args.get("emissivity"),
            thermal_vias=args.get("thermal_vias"),
            layer_stack=layer_stack,
            alpha_per_k=(float(args["alpha_per_k"])
                         if args.get("alpha_per_k") is not None else 0.00393),
            rho20_ohm_m=(float(args["rho20_ohm_m"])
                         if args.get("rho20_ohm_m") is not None else 1.724e-8),
            tol_k=tol_k,
            max_iter=200,
            sor_max_iter=int(args.get("sor_max_iter") or 60000),
            max_cells=int(args.get("max_cells") or 20000),
            transient=args.get("transient"))

    # The plate's own balance gate is 0.5 % of the input power, and on a board
    # that rises by a fraction of a kelvin that gate is measuring the last SOR
    # sweep. Rather than reporting a looser balance (which would be an invented
    # temperature) the tolerance is tightened until the model balances, and the
    # tolerance that was ACTUALLY used is reported.
    et, _used = None, _tol
    for _try in (_tol, _tol / 100.0, _tol / 1e4):
        _try = max(1e-11, _try)
        et, _used = _plate(_try), _try
        if et.get("status") == "ok":
            break
        if et.get("reason_code") != "INTERNAL" or "does not balance" not in str(
                et.get("message")):
            break
    _tol = _used
    if et.get("status") != "ok":
        et.setdefault("extra", {})["dissipation"] = {
            "nets": dv["nets"], "total_power_w": dv["total_power_w"],
            "refusals": dv["refusals"]}
        return et

    notes = list(pour_notes)
    if _rise_k > 0.0 and _tol < float(args.get("tol_k") if args.get("tol_k")
                                      is not None else 1e-4):
        notes.append("the plate's tolerance was tightened to %.3g K because this "
                     "board rises by roughly %.3g K: at the 1e-4 K default the "
                     "energy balance reports the last sweep, not the model"
                     % (_tol, _rise_k))
    notes.append("the plate is the outline's bounding box: a board whose outline "
                 "is not a filled rectangle is solved on slightly more area than "
                 "it has")
    if dv.get("islands"):
        notes.append("%s are copper that no driven pad reaches (a split net on a "
                     "finished board); no current runs in them, so they are not in "
                     "the solve and add no heat"
                     % "; ".join("%d node(s) of %s" % (i["nodes"], i["net"])
                                 for i in dv["islands"][:4]))
    if dv["refusals"]:
        notes.append("%d net(s) were refused and contribute NO heat: %s"
                     % (len(dv["refusals"]),
                        "; ".join("%s (%s)" % (r["net"], r["reason_code"])
                                  for r in dv["refusals"][:4])))
    if dv["via_power_w"] > 0.0:
        notes.append("the vias dissipate %.6g W in all, and that power is NOT a "
                     "source in this plate: a barrel is vertical and a 2-D "
                     "in-plane field has nowhere to put it. Their numbers are in "
                     "value.dissipation.via_power" % dv["via_power_w"])
    if dv["other_power_w"] > 0.0:
        notes.append("%.6g W went into pour squares or pad plating (model_zones) "
                     "and is likewise not placed as a source"
                     % dv["other_power_w"])

    v = et.setdefault("value", {})
    v["dissipation"] = {
        "nets": dv["nets"],
        "total_power_w": dv["total_power_w"],
        "track_power_w": dv["track_power_w"],
        "via_power_w": dv["via_power_w"],
        "other_power_w": dv["other_power_w"],
        "segment_count": dv["segment_count"],
        "hot_segments": dv["segments"][:10],
        "via_power": dv["via_power"][:10],
        "refusals": dv["refusals"],
        "islands": dv.get("islands") or [],
    }
    v["plate"] = {"x_mm": x0, "y_mm": y0, "width_mm": bbox["width_mm"],
                  "height_mm": bbox["height_mm"], "thickness_mm": float(th),
                  "copper_from_board": bool(want_pours and not layer_stack),
                  "pour_rectangles": len(pours)}
    v["conductor_count"] = len(conductors)
    v.setdefault("notes", [])
    if isinstance(v.get("notes"), list):
        v["notes"].extend(notes)
    et["notes"] = notes
    et["basis"] = (et.get("basis") or "") + (
        "; the sources are the board's own track segments (I^2R on "
        "board_ir_drop's graph, solved for the currents you gave per pad), the "
        "plate is the outline's bounding box, the copper is the file's stackup "
        "and fills")
    et["assumptions"] = dict(et.get("assumptions") or {})
    et["assumptions"]["board_thermal"] = (
        "currents are per pad and must add up to zero on each net; nothing in a "
        ".kicad_pcb states a current")
    return et
