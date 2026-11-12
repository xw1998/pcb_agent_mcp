"""Crosstalk between two nets that actually run side by side on this board.

`crosstalk_coupled_lines` is a full 2-D field solution of a pair, and it asks for
six numbers that a board file already contains except one: width, gap, height to
the reference plane, length of the coupled run, and er are geometry; the RISE
TIME is not in any .kicad_pcb, so that one stays the caller's.

What this module adds is the part that is not arithmetic: FINDING the coupled
run. Two nets are only coupled where their tracks are parallel, close, and
overlapping - and a board has tens of tracks per net, so guessing "the nearest
two" is how a tool reports a number about traces that never actually run
together. So the search is explicit:

* parallel within `angle_tol_deg` (default 10 deg, measured on the segment
  directions, not on the bounding boxes),
* edge-to-edge gap at most `max_gap_mm`,
* longitudinally overlapping by at least `min_overlap_mm`.

Every candidate is returned, ranked, with its own gap and overlap, so the answer
can be argued with. The solve is run on the worst one only - a full field
solution per candidate would be a minute each - and the report says which run
that was and how many others there were.
"""
from __future__ import annotations

import math

from ..calc import crosstalk as _X

from .planes import layer_dielectric


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


def _unit(p, q):
    dx, dy = q[0] - p[0], q[1] - p[1]
    n = math.hypot(dx, dy)
    if n <= 0:
        return None
    return (dx / n, dy / n), n


def coupled_runs(segs_a, segs_b, angle_tol_deg=10.0, max_gap_mm=1.0,
                 min_overlap_mm=2.0):
    """Every pair of segments that run side by side, close, and overlapping.

    `segs` are dicts with start/end/width_mm. Geometry only: no fields, no
    judgement, which is what makes it testable on a synthetic four-line board.
    """
    sin_tol = math.sin(math.radians(max(0.0, angle_tol_deg)))
    runs = []
    for a in segs_a:
        ua = _unit(a["start"], a["end"])
        if not ua:
            continue
        da, la = ua
        for b in segs_b:
            ub = _unit(b["start"], b["end"])
            if not ub:
                continue
            db, lb = ub
            cross = abs(da[0] * db[1] - da[1] * db[0])
            if cross > sin_tol:
                continue                      # not parallel enough to couple
            # gap = perpendicular distance between the two LINES, edge to edge
            rx, ry = b["start"][0] - a["start"][0], b["start"][1] - a["start"][1]
            centre = abs(rx * da[1] - ry * da[0])
            gap = centre - 0.5 * (float(a["width_mm"]) + float(b["width_mm"]))
            if gap < 0:
                gap = 0.0                     # they touch: a short, not a gap
            if gap > max_gap_mm:
                continue
            # longitudinal overlap of b onto a's axis
            t0 = rx * da[0] + ry * da[1]
            t1 = ((b["end"][0] - a["start"][0]) * da[0]
                  + (b["end"][1] - a["start"][1]) * da[1])
            lo, hi = min(t0, t1), max(t0, t1)
            lo, hi = max(lo, 0.0), min(hi, la)
            overlap = hi - lo
            if overlap < min_overlap_mm:
                continue
            if hi - lo <= 0:
                continue
            # the midpoint of the shared run, so a person can find it
            mid = (lo + hi) / 2.0
            runs.append({
                "gap_mm": gap,
                "overlap_mm": overlap,
                "width_a_mm": float(a["width_mm"]),
                "width_b_mm": float(b["width_mm"]),
                "angle_deg": math.degrees(math.asin(min(1.0, cross))),
                "at_mm": [a["start"][0] + da[0] * mid,
                          a["start"][1] + da[1] * mid],
                "seg_a": [list(a["start"]), list(a["end"])],
                "seg_b": [list(b["start"]), list(b["end"])],
            })
    runs.sort(key=lambda r: (r["gap_mm"], -r["overlap_mm"]))
    return runs


def _stack_index(board, layer):
    names = [l.get("name") for l in (board.stackup or [])]
    return names.index(layer) if layer in names else None


def reference_plane(board, layer):
    """The nearest copper on each side of `layer`, and the dielectric to it.

    A microstrip has one reference plane; a stripline has two. Which one the
    solver wants is a different number (`dielectric_mm` is the height to the
    plane for microstrip, half the plane-to-plane gap for stripline), so both
    distances are reported and the caller-visible one is chosen as the SMALLER
    - the plane that couples first, and the one a solver that only takes one
    number can represent.
    """
    stack = board.stackup or []
    idx = _stack_index(board, layer)
    if idx is None:
        return None
    cu = [i for i, l in enumerate(stack) if l.get("type") == "copper"]
    below = [i for i in cu if i > idx]
    above = [i for i in cu if i < idx]
    near_below = min(below) if below else None
    near_above = max(above) if above else None
    out = {"layer": layer, "outer": (near_below is None or near_above is None),
           "reference_below": stack[near_below]["name"] if near_below is not None else None,
           "reference_above": stack[near_above]["name"] if near_above is not None else None}
    dists = {}
    if near_below is not None:
        d = layer_dielectric(board, layer, stack[near_below]["name"])
        if d:
            dists["below"] = d
    if near_above is not None:
        d = layer_dielectric(board, layer, stack[near_above]["name"])
        if d:
            dists["above"] = d
    if not dists:
        return None
    side = "below" if "below" in dists else "above"
    if "above" in dists and "below" in dists:
        side = ("below" if dists["below"]["thickness_mm"] <= dists["above"]["thickness_mm"]
                else "above")
    out["nearest"] = side
    out["dielectric_mm"] = dists[side]["thickness_mm"]
    out["er"] = dists[side]["er"]
    out["both_sides"] = {k: {"dielectric_mm": v["thickness_mm"], "er": v["er"]}
                         for k, v in dists.items()}
    return out


def _tracks(board, net, layer):
    out = []
    for t in (board.tracks or []):
        if str(t.get("net") or "") != net or t.get("layer") != layer:
            continue
        w = _num(t.get("width"))
        if not w or w <= 0:
            continue
        s, e = t.get("start"), t.get("end")
        if not s or not e:
            continue
        out.append({"start": (float(s[0]), float(s[1])),
                    "end": (float(e[0]), float(e[1])), "width_mm": w})
    return out


def board_crosstalk(board, args):
    """NEXT/FEXT between two nets, on the run where they actually couple."""
    net_a = str(args.get("net_a") or "")
    net_b = str(args.get("net_b") or "")
    for name, v in (("net_a", net_a), ("net_b", net_b)):
        if not v:
            return _err("%s is required: crosstalk is between two named nets" % name,
                        "ARG_MISSING")
    if net_a == net_b:
        return _err("net_a and net_b are the same net, which couples to itself",
                    "ARG_INVALID")
    rise = _num(args.get("rise_time_ns"))
    if not rise or rise <= 0:
        return _err("rise_time_ns is required and must be > 0: no .kicad_pcb "
                    "contains a rise time, and FEXT is proportional to 1/rise_time "
                    "in this model, so it is the most sensitive input there is",
                    "ARG_MISSING")

    layers = sorted({str(t.get("layer")) for t in (board.tracks or [])
                     if str(t.get("net") or "") in (net_a, net_b)
                     and t.get("layer")})
    common = [l for l in layers
              if _tracks(board, net_a, l) and _tracks(board, net_b, l)]
    want = args.get("layer")
    if want:
        want = str(want)
        if want not in common:
            return _err("net_a and net_b do not both have tracks on %r; they share %s"
                        % (want, ", ".join(common) or "no layer"), "NOT_APPLICABLE",
                        extra={"layers_a_b": layers})
        layer = want
    elif common:
        # the layer where the two nets actually run closest: that is the coupling
        layer = common[0]
        best = None
        for cand in common:
            runs = coupled_runs(_tracks(board, net_a, cand),
                                _tracks(board, net_b, cand),
                                angle_tol_deg=float(args.get("angle_tol_deg") or 10.0),
                                max_gap_mm=float(args.get("max_gap_mm") or 1.0),
                                min_overlap_mm=float(args.get("min_overlap_mm") or 2.0))
            key = (runs[0]["gap_mm"], -runs[0]["overlap_mm"]) if runs else (1e9, 0)
            if best is None or key < best[0]:
                best = (key, cand)
        layer = best[1]
    else:
        return _err("net_a and net_b never share a copper layer, so they cannot "
                    "couple on this board", "NOT_APPLICABLE",
                    extra={"layers_a_b": layers})

    runs = coupled_runs(_tracks(board, net_a, layer), _tracks(board, net_b, layer),
                        angle_tol_deg=float(args.get("angle_tol_deg") or 10.0),
                        max_gap_mm=float(args.get("max_gap_mm") or 1.0),
                        min_overlap_mm=float(args.get("min_overlap_mm") or 2.0))
    if not runs:
        # A refusal that only says "nothing found" is a dead end: the useful half
        # is the closest pair that WAS found and why it did not qualify, because
        # the thresholds are the caller's and the board is the board.
        near = coupled_runs(_tracks(board, net_a, layer), _tracks(board, net_b, layer),
                            angle_tol_deg=90.0, max_gap_mm=float("inf"),
                            min_overlap_mm=0.0)[:3]
        return _err("no run of %r and %r on %r is parallel within %g deg, closer "
                    "than %g mm and overlapping by %g mm, so there is nothing to "
                    "solve; widen max_gap_mm or lower min_overlap_mm if you "
                    "expected coupling"
                    % (net_a, net_b, layer, float(args.get("angle_tol_deg") or 10.0),
                       float(args.get("max_gap_mm") or 1.0),
                       float(args.get("min_overlap_mm") or 2.0)),
                    "NOT_APPLICABLE",
                    extra={"closest_parallel": near,
                           "layers_with_both": common})

    worst = runs[0]
    wa, wb = worst["width_a_mm"], worst["width_b_mm"]
    if abs(wa - wb) / max(wa, wb) > 0.10:
        return _err("the closest run has %s at %.4g mm wide and %s at %.4g mm: the "
                    "solver has ONE width and the pair is symmetric, so solving "
                    "this would be solving a pair that is not on the board"
                    % (net_a, wa, net_b, wb), "ARG_INVALID",
                    extra={"runs": runs[:5]})

    ref = reference_plane(board, layer)
    if not ref:
        return _err("%r is not in the stackup, or nothing separates it from a "
                    "reference plane" % layer, "NOT_APPLICABLE")
    er = _num(args.get("er")) or ref["er"]
    if not er:
        return _err("the stackup between %r and %r has no dielectric constant, so "
                    "er has to be given" % (layer, ref.get("reference_" + ref["nearest"])),
                    "ARG_MISSING")
    copper_um = _num(args.get("copper_um"))
    if copper_um is None:
        copper_um = next((_num(l.get("copper_um")) for l in (board.stackup or [])
                          if l.get("name") == layer), None) or 0.0

    dielectric_mm = _num(args.get("dielectric_mm")) or ref["dielectric_mm"]
    out = _X.coupled_lines(
        str(args.get("kind") or ("microstrip" if ref["outer"] else "stripline")),
        wa, worst["gap_mm"], dielectric_mm, worst["overlap_mm"], rise,
        copper_um=float(copper_um), er=float(er),
        amplitude_v=float(args.get("amplitude_v") or 1.0),
        termination_ohm=(None if args.get("termination_ohm") is None
                         else float(args["termination_ohm"])),
        mesh_div=int(args.get("mesh_div") or 12),
        samples_per_delay=int(args.get("samples_per_delay") or 60),
        waveform_points=int(args.get("waveform_points") or 101))
    if out.get("status") != "ok":
        return out

    info = {
        "net_a": net_a, "net_b": net_b, "layer": layer,
        "kind": str(args.get("kind") or ("microstrip" if ref["outer"] else "stripline")),
        "width_mm": wa, "spacing_mm": worst["gap_mm"],
        "length_mm": worst["overlap_mm"], "dielectric_mm": dielectric_mm,
        "er": float(er), "rise_time_ns": rise, "copper_um": float(copper_um),
        "run_at_mm": worst["at_mm"], "run_angle_deg": worst["angle_deg"],
        "candidate_runs": len(runs),
        "runs": [{k: r[k] for k in ("gap_mm", "overlap_mm", "width_a_mm",
                                    "width_b_mm", "angle_deg", "at_mm")}
                 for r in runs[:10]],
        "reference": {k: ref[k] for k in ("reference_below", "reference_above",
                                          "nearest", "outer")},
        "er_from_stackup": ref["er"],
    }
    value = out.get("value")
    out["value"] = {"crosstalk": value, "board": info}
    for key in ("not_modelled", "authoritative", "where_the_rest_is"):
        if key in out:
            out["value"][key] = out.pop(key)

    notes = out.get("notes")
    notes = notes if isinstance(notes, list) else []
    out["notes"] = notes
    notes.append(
        "solved on the closest of %d coupled run(s): %s and %s are parallel to "
        "%.1f deg, %.4g mm apart edge to edge, for %.4g mm around (%.4g, %.4g) mm. "
        "The other runs are listed but not solved - each is a full field solution"
        % (len(runs), net_a, net_b, worst["angle_deg"], worst["gap_mm"],
           worst["overlap_mm"], worst["at_mm"][0], worst["at_mm"][1]))
    if ref["both_sides"] and len(ref["both_sides"]) == 2:
        notes.append(
            "the trace has copper on both sides (%.4g mm %s / %.4g mm %s); the "
            "solver takes one height, so %.4g mm to the %s plane is used"
            % (ref["both_sides"]["below"]["dielectric_mm"],
               ref["reference_below"],
               ref["both_sides"]["above"]["dielectric_mm"],
               ref["reference_above"], dielectric_mm, ref["nearest"]))
    notes.append("the run is taken as straight and parallel for its whole length "
                 "(no bend in the shared stretch is modelled); pads, vias and the "
                 "rest of both nets are not in it")
    out["basis"] = (out.get("basis") or "") + (
        "; width, gap, run length, plate height and er come from the .kicad_pcb; "
        "the rise time is the caller's because no board file has one")
    out.setdefault("assumptions", {})
    out["assumptions"]["board_crosstalk"] = (
        "one straight parallel run, single reference plane, same termination at "
        "all four ports as crosstalk_coupled_lines documents")
    return out
