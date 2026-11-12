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
import os
import time

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


JOBS_ENV = "PCB_AGENT_JOBS"
MAX_JOBS = 8          # the same cap max_runs has: beyond it the answer is the same

def _solve_one_run(kw):
    """One coupled run: its own full 2-D field solution.

    Module level, and taking a plain dict, so a spawned worker can find it. The
    solve is pure arithmetic on floats - no board object, no globals - which is
    the whole reason the runs can be handed to separate processes at all.
    """
    started = time.time()
    one = _X.coupled_lines(**kw)
    one["elapsed_s"] = round(time.time() - started, 2)
    return one

def _jobs_asked(args, n_specs):
    """How many runs to solve at once, and the reason if it is one.

    `jobs` is the caller's, `PCB_AGENT_JOBS` is the machine's; 1 means "one after
    another" and is BOTH the default and the fallback whenever a pool cannot be
    started, because a wrong pool is worse than a slow answer - a library that
    spawns processes unasked is a library that can re-run a caller's unguarded
    `__main__` once per worker. Returns (jobs, why) or (None, error).
    """
    raw = args.get("jobs")
    if raw is None or raw == "":
        raw = os.environ.get(JOBS_ENV)
    if raw is None or raw == "":
        return 1, ("default: one after another - starting processes behind the "
                   'caller\'s back is not something a library does; ask with '
                   'jobs="auto" (or %s=auto)' % JOBS_ENV)
    if isinstance(raw, str) and raw.strip().lower() == "auto":
        return (max(1, min(n_specs, MAX_JOBS, os.cpu_count() or 1)),
                "auto: min(%d runs, %d cores, %d)" % (
                    n_specs, os.cpu_count() or 1, MAX_JOBS))
    n = _num(raw)
    if n is None:
        return None, _err("jobs must be a number: it is how many runs are solved at "
                          "once", "ARG_INVALID")
    n = int(n)
    if n < 1:
        return None, _err("jobs must be >= 1: jobs=0 would be a request to solve "
                          "nothing, and a run that is not solved contributes "
                          "nothing", "ARG_RANGE")
    return min(n, MAX_JOBS, n_specs), "jobs=%d capped at min(%d runs, %d)" % (
        n, n_specs, MAX_JOBS)

def _solve_runs(specs, jobs):
    """Solve every (rank, run, kwargs) spec, in parallel when asked and able.

    Returns (list of (rank, run, one) in rank order, how) where `how` says what
    actually happened. Order is preserved by `Pool.map`, so a parallel answer is
    the same answer; only the wall clock and `how` differ.
    """
    kws = [kw for _rank, _run, kw in specs]
    if jobs <= 1 or len(kws) <= 1:
        return [(specs[i][0], specs[i][1], _solve_one_run(kw))
                for i, kw in enumerate(kws)], {
                    "used": False, "jobs": 1,
                    "why": ("one run to solve: there is nothing to overlap"
                            if len(kws) <= 1 else "jobs=1: runs were solved one "
                            "after another")}
    try:
        import multiprocessing as mp
        ctx = mp.get_context("spawn")
        with ctx.Pool(processes=jobs) as pool:
            outs = pool.map(_solve_one_run, kws)
    except Exception as exc:                     # pragma: no cover - see the test
        return [(specs[i][0], specs[i][1], _solve_one_run(kw))
                for i, kw in enumerate(kws)], {
                    "used": False, "jobs": 1,
                    "why": "the worker pool could not be started (%s: %s), so the "
                           "runs were solved one after another - the numbers are "
                           "the same and only the wall clock pays"
                           % (type(exc).__name__, exc)}
    return [(specs[i][0], specs[i][1], outs[i]) for i in range(len(kws))], {
        "used": True, "jobs": jobs,
        "why": "%d runs solved in %d spawned process(es): each run is its own "
               "field solution and they do not share anything" % (len(kws), jobs)}

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

    # `args.get("max_runs") or 1` turns 0 into 1, which silently solves a run the
    # caller asked NOT to solve - so 0 has to survive to the range check.
    raw_runs = args.get("max_runs")
    if raw_runs is None or raw_runs == "":
        want_runs = 1
    else:
        n_runs = _num(raw_runs)
        if n_runs is None:
            return _err("max_runs must be a number: it is how many coupled runs "
                        "get their own field solve", "ARG_INVALID")
        want_runs = int(n_runs)
    if want_runs < 1:
        return _err("max_runs must be >= 1: a run that is not solved contributes "
                    "nothing, so asking for zero solves is not a request",
                    "ARG_RANGE")
    want_runs = min(want_runs, 8)

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
    kind = str(args.get("kind") or ("microstrip" if ref["outer"] else "stripline"))

    # Solve the runs in rank order, up to max_runs of them. A run whose two lines
    # are different widths is REFUSED BY NAME rather than dropped: the solver has
    # one width for a symmetric pair, so that run is not the pair on the board,
    # and "the second-closest run was skipped and nobody said why" is how a tool
    # reports crosstalk for a pair that does not exist.
    specs, refused = [], []
    for rank, run in enumerate(runs[:want_runs], 1):
        wa, wb = run["width_a_mm"], run["width_b_mm"]
        if abs(wa - wb) / max(wa, wb) > 0.10:
            refused.append({
                "rank": rank, "width_a_mm": wa, "width_b_mm": wb,
                "gap_mm": run["gap_mm"], "overlap_mm": run["overlap_mm"],
                "at_mm": list(run["at_mm"]),
                "why": "the two lines are %.4g mm and %.4g mm wide and the solver "
                       "has ONE width for a symmetric pair, so this run is not the "
                       "pair on the board" % (wa, wb)})
            continue
        specs.append((rank, run, {
            "kind": kind, "width_mm": wa, "spacing_mm": run["gap_mm"],
            "dielectric_mm": dielectric_mm, "length_mm": run["overlap_mm"],
            "rise_time_ns": rise,
            "copper_um": float(copper_um), "er": float(er),
            "amplitude_v": float(args.get("amplitude_v") or 1.0),
            "termination_ohm": (None if args.get("termination_ohm") is None
                                else float(args["termination_ohm"])),
            "mesh_div": int(args.get("mesh_div") or 12),
            "samples_per_delay": int(args.get("samples_per_delay") or 60),
            "waveform_points": int(args.get("waveform_points") or 101)}))

    jobs, jobs_why = _jobs_asked(args, len(specs))
    if jobs is None:
        return jobs_why                      # already an error envelope
    started_all = time.time()
    solved, how = _solve_runs(specs, jobs)
    how["wall_s"] = round(time.time() - started_all, 2)
    how["asked"] = jobs_why
    for rank, run, one in solved:
        if one.get("status") != "ok":
            # A solver failure is about the stackup or the arguments, not about
            # which run it was: say which run, then hand the error up unchanged.
            one["run_rank"] = rank
            one["run_at_mm"] = list(run["at_mm"])
            return one

    solved = [{"rank": r, "run": run, "solve": one} for r, run, one in solved]
    if not solved:
        first = refused[0]
        return _err("the closest run has %s at %.4g mm wide and %s at %.4g mm: the "
                    "solver has ONE width and the pair is symmetric, so solving "
                    "this would be solving a pair that is not on the board"
                    % (net_a, first["width_a_mm"], net_b, first["width_b_mm"]),
                    "ARG_INVALID",
                    extra={"runs": runs[:5], "refused_runs": refused})

    best = solved[0]
    worst = best["run"]
    wa = worst["width_a_mm"]
    out = best["solve"]

    info = {
        "net_a": net_a, "net_b": net_b, "layer": layer,
        "kind": kind,
        "width_mm": wa, "spacing_mm": worst["gap_mm"],
        "length_mm": worst["overlap_mm"], "dielectric_mm": dielectric_mm,
        "er": float(er), "rise_time_ns": rise, "copper_um": float(copper_um),
        "run_at_mm": worst["at_mm"], "run_angle_deg": worst["angle_deg"],
        "run_rank": best["rank"],
        "candidate_runs": len(runs),
        "runs": [{k: r[k] for k in ("gap_mm", "overlap_mm", "width_a_mm",
                                    "width_b_mm", "angle_deg", "at_mm")}
                 for r in runs[:10]],
        "reference": {k: ref[k] for k in ("reference_below", "reference_above",
                                          "nearest", "outer")},
        "er_from_stackup": ref["er"],
    }

    def _brief(s):
        v = s["solve"].get("value") or {}
        return {"rank": s["rank"],
                "gap_mm": s["run"]["gap_mm"],
                "overlap_mm": s["run"]["overlap_mm"],
                "angle_deg": s["run"]["angle_deg"],
                "at_mm": list(s["run"]["at_mm"]),
                "width_a_mm": s["run"]["width_a_mm"],
                "width_b_mm": s["run"]["width_b_mm"],
                "elapsed_s": s["solve"].get("elapsed_s"),
                "next": v.get("next"), "fext": v.get("fext")}

    rows = [_brief(s) for s in solved]
    value = out.get("value")
    out["value"] = {"crosstalk": value, "board": info,
                    "runs_solved": rows, "runs_refused": refused,
                    "parallel": how}
    for key in ("not_modelled", "authoritative", "where_the_rest_is"):
        if key in out:
            out["value"][key] = out.pop(key)

    notes = out.get("notes")
    notes = notes if isinstance(notes, list) else []
    out["notes"] = notes
    notes.append(
        "solved %d of %d coupled run(s) (max_runs=%d): run #%d is %s and %s, "
        "parallel to %.1f deg, %.4g mm apart edge to edge, for %.4g mm around "
        "(%.4g, %.4g) mm, computed in %.1f s. Every run is its own full 2-D field "
        "solution of its own pair, which is why the NUMBER solved is the caller's "
        "choice - pass max_runs to solve more of them (up to 8)"
        % (len(solved), len(runs), want_runs, best["rank"], net_a, net_b,
           worst["angle_deg"], worst["gap_mm"], worst["overlap_mm"],
           worst["at_mm"][0], worst["at_mm"][1],
           best["solve"].get("elapsed_s") or 0.0))
    if len(solved) > 1:
        # The cost of a second run is the whole point of `max_runs`, so the answer
        # says what the runs actually cost together and whether they overlapped.
        # A pool that could not start is reported here rather than swallowed: the
        # caller asked for a speed-up and did not get one.
        notes.append(
            "%d runs cost %.1f s of field solving in %.1f s of wall clock (%s); "
            "raise max_runs to solve more of them and `jobs` to solve more at "
            "once, and note that each extra run is a full solution of its own pair"
            % (len(solved), sum((r.get("elapsed_s") or 0.0) for r in rows),
               how.get("wall_s") or 0.0,
               "in %d processes" % how["jobs"] if how.get("used")
               else "one after another, because %s" % how.get("why")))
    if len(solved) < len(runs):
        notes.append(
            "%d of the %d coupled run(s) are not solved here: max_runs=%d covers "
            "the %d closest and %d of those could not be solved as a symmetric "
            "pair; the rest are listed in board.runs with their own gap and "
            "overlap so a tighter run can be picked by hand"
            % (len(runs) - len(solved), len(runs), want_runs, len(runs[:want_runs]),
               len(refused)))
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
        "each solved run is one straight parallel pair over a single reference "
        "plane with the same termination at all four ports as crosstalk_coupled_"
        "lines documents; runs are solved independently of each other")
    return out
