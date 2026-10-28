"""Electro-thermal: the current heats the copper, the hot copper carries less.

Every number here is copper at temperature T, and T depends on the losses, and
the losses depend on the resistance at T. That loop has exactly two honest
outcomes: it converges to a temperature, or it runs away. This module solves the
loop and reports which one happened - it never returns a temperature from a
single pass at 20 C and calls it the operating point.

    R(T)   = rho20 * (1 + alpha*(T - 20)) * L / (w*t)
    P      = I^2 * R(T)                       (resistive only, DC)
    T      = T_amb + P * Rth                  (Rth comes from the caller)

Iterating dT_{n+1} = I^2 * R(T_amb + dT_n) * Rth is a contraction iff

    G = I^2 * Rth * alpha * R20 / (1 + 0)  ...  i.e.  dP/dT * Rth < 1

which is checked exactly (not by watching the loop): above 1 the fixed point does
not exist, the temperature runs away, and reporting "converged: false" would be
too weak - the envelope returns THERMAL_RUNAWAY with the gain G.

Rth is ALWAYS the caller's. This module does not know your stackup, your copper
area, your vias, or your airflow: feed it `thermal_stack` / `via_array_thermal` /
`plate_temperature` results, or a measured value. A default Rth would be the one
number in the whole layer that is not derived, which is the same reason `dfm`
ships no fab table and `steinberg_fatigue` demands z_allow_mm.
"""
import math

from .result import ok, err

RHO20_CU = 1.724e-8      # ohm*m, IACS copper at 20 C
ALPHA_CU = 0.00393       # 1/K, linear region 0..100 C
T20 = 20.0               # C, the reference temperature of both constants


def _num(x):
    try:
        v = float(x)
    except (TypeError, ValueError):
        return None
    if v != v or v in (float("inf"), float("-inf")):
        return None
    return v


def _sig(x, digits=6):
    if x is None:
        return None
    if x == 0:
        return 0.0
    ax = abs(x)
    if ax >= 1e-4 and ax < 1e7:
        n = digits - 1 - int(math.floor(math.log10(ax)))
        return round(x, n) if n > 0 else float(round(x, n))
    return float("%.*e" % (digits - 1, x))


def _solve(r20, i, rth, t_amb, alpha, tol_c, max_iter, label):
    """Fixed-point loop in dT, with the contraction test done exactly.

    dT(n+1) = I^2 * R20 * (1 + alpha*(T_amb - 20 + dT(n))) * Rth
            = A + B*dT(n)
    A = I^2*R20*Rth*(1 + alpha*(T_amb - 20))
    B = I^2*R20*Rth*alpha          <- this is dP/dT * Rth, the runaway gain
    """
    gain = i * i * r20 * rth * alpha
    if gain >= 1.0:
        return err(
            "thermal runaway: dP/dT * Rth = %.4g >= 1, so no steady state exists"
            % gain, "THERMAL_RUNAWAY",
            hint="either the current is too high for this Rth, or the Rth is "
                 "optimistic. The fixed point dT = A + B*dT only exists for "
                 "B < 1; nothing else in this module can fix that",
            extra={"runaway_gain": _sig(gain),
                   "r20_ohm": _sig(r20),
                   "rth_c_per_w": _sig(rth),
                   "current_a": _sig(i),
                   "alpha_per_k": _sig(alpha)})
    a = i * i * r20 * rth * (1.0 + alpha * (t_amb - T20))
    # Start cold (dT = 0) and iterate, so `iterations` is a real measurement of
    # how hard this loop had to work - it decays by exactly `gain` per step. The
    # closed form dt = a/(1-gain) exists (that is the same fixed point) but
    # reporting it as "0 iterations to converge" would hide the margin.
    dt = 0.0
    it = 0
    residual = None
    while it < max_iter:
        dt_next = a + gain * dt
        residual = abs(dt_next - dt)
        dt = dt_next
        it += 1
        if residual <= tol_c:
            break
    conv = residual is not None and residual <= tol_c
    if not conv:
        return err("the electro-thermal loop did not settle in %d iterations "
                   "(last step %.3g C, tol %.3g)" % (max_iter, residual, tol_c),
                   "NO_CONVERGENCE",
                   hint="the loop is a contraction here, so this means max_iter "
                        "is too small for the gain; raise it rather than "
                        "reporting the last iterate as an answer",
                   extra={"iterations": it, "last_step_c": _sig(residual),
                          "runaway_gain": _sig(gain)})
    t = t_amb + dt
    r = r20 * (1.0 + alpha * (t - T20))
    return {
        "converged": True,
        "iterations": it,
        "last_step_c": _sig(residual),
        "runaway_gain": _sig(gain),
        "r_hot_ohm": _sig(r),
        "resistance_gain": _sig(1.0 + alpha * (t - T20)),
        "power_w": _sig(i * i * r),
        "delta_t_c": _sig(dt),
        "t_final_c": _sig(t),
        "stability_margin": _sig(1.0 - gain),
        "_label": label,
    }


_MODEL_TRACE = ("R(T) = rho20*(1+alpha*(T-20))*L/(w*t); P = I^2*R; "
                "T = T_amb + P*Rth, solved as a fixed point in dT with the "
                "contraction gain dP/dT*Rth checked exactly")
_BASIS_TRACE = ("rho20 = 1.724e-8 ohm*m, alpha = 0.00393 /K (IACS copper, "
                "linear 0..100 C)")
_NOT_COMPUTED = [
    "convection, radiation and spreading - Rth is taken as given (one lump), so "
    "this is not a thermal solution, only the electro-thermal feedback on top "
    "of one",
    "AC resistance: skin/proximity effect at high frequency, so P = I^2*R is "
    "wrong for RF current distribution",
    "temperature-dependent alpha (the linear law is used past 100 C without "
    "warning, where it overstates dR)",
    "solder-joint and laminate limits - this module reports temperature, not "
    "whether the board survives it",
    "thermal capacitance / transients (that is thermal_transient)",
]


def trace_self_heating(length_mm, width_mm, thickness_um, current_a,
                       rth_c_per_w=None, t_ambient_c=25.0,
                       alpha_per_k=ALPHA_CU, rho20_ohm_m=RHO20_CU,
                       tol_c=1e-4, max_iter=2000):
    """Self-consistent temperature of a straight copper trace carrying DC.

    Width/dimension conventions are the same as `power.trace_resistance`: mm, um,
    A, ohm*m. `rth_c_per_w` is REQUIRED - see the module docstring for why.
    """
    L = _num(length_mm)
    w = _num(width_mm)
    t_um = _num(thickness_um)
    i = _num(current_a)
    rth = _num(rth_c_per_w)
    ta = _num(t_ambient_c)
    alpha = _num(alpha_per_k)
    rho = _num(rho20_ohm_m)
    for name, v in (("length_mm", L), ("width_mm", w), ("thickness_um", t_um),
                    ("current_a", i), ("t_ambient_c", ta),
                    ("alpha_per_k", alpha), ("rho20_ohm_m", rho)):
        if v is None:
            return err("%s must be a number" % name, "ARG_INVALID",
                       extra={"offending": name})
    for name, v in (("length_mm", L), ("width_mm", w), ("thickness_um", t_um),
                    ("alpha_per_k", alpha), ("rho20_ohm_m", rho)):
        if v <= 0:
            return err("%s must be > 0" % name, "ARG_RANGE",
                       extra={"offending": name})
    if i < 0:
        return err("current_a must be >= 0", "ARG_RANGE")
    if rth is None:
        return err("rth_c_per_w is required", "ARG_REQUIRED",
                   hint="one lump from junction/copper to ambient - take it from "
                        "thermal_stack / via_array_thermal / plate_temperature, or "
                        "from a measurement. This module does not invent one")
    if rth <= 0:
        return err("rth_c_per_w must be > 0", "ARG_RANGE")

    t_m = _num(thickness_um) * 1e-6
    r20 = rho * (L * 1e-3) / (w * 1e-3 * t_m)
    solved = _solve(r20, i, rth, ta, alpha, _num(tol_c) or 1e-4,
                    int(_num(max_iter) or 2000), "trace")
    if solved.get("status") == "error":
        return solved
    extra = dict(solved)
    extra.pop("_label", None)
    extra.update({
        "r20_ohm": _sig(r20),
        "length_mm": _sig(L),
        "width_mm": _sig(w),
        "thickness_um": _sig(t_um),
        "current_a": _sig(i),
        "rth_c_per_w": _sig(rth),
        "t_ambient_c": _sig(ta),
        "alpha_per_k": _sig(alpha),
        "current_density_a_per_mm2": _sig(i / (w * t_um * 1e-3)) if w * t_um else None,
        "not_computed": list(_NOT_COMPUTED),
        "authoritative": False,
    })
    return ok(solved["t_final_c"], "C",
              "self-consistent 1-D electro-thermal (copper R(T) + one lump Rth)",
              _MODEL_TRACE + "; " + _BASIS_TRACE,
              "R(T) is exact for the linear alpha law; the temperature inherits "
              "+-10..20 % from the caller's single-lump Rth, and the DC-only "
              "assumption is exact only below the skin-depth frequency",
              {"rth_source": "caller-supplied single lump, not derived here",
               "copper": "bulk IACS rho20, no roughness / no grain effects"},
              extra)


def conductor_self_heating(r_ohm_at_20c, current_a, rth_c_per_w,
                          t_ambient_c=25.0, alpha_per_k=ALPHA_CU,
                          tol_c=1e-4, max_iter=2000, note=None):
    """Same loop, but the cold resistance is supplied directly.

    Use it for anything whose R20 you already computed or measured: a via barrel
    (`power.via_resistance`), a stack of segments (`power.ir_drop_chain`), a
    shunt, a connector pin. Keeping this separate is what makes the loop testable
    against hand arithmetic (R20 and Rth given -> dT is a one-line formula).
    """
    r20 = _num(r_ohm_at_20c)
    i = _num(current_a)
    rth = _num(rth_c_per_w)
    ta = _num(t_ambient_c)
    alpha = _num(alpha_per_k)
    for name, v in (("r_ohm_at_20c", r20), ("current_a", i),
                    ("rth_c_per_w", rth), ("t_ambient_c", ta),
                    ("alpha_per_k", alpha)):
        if v is None:
            return err("%s must be a number" % name, "ARG_INVALID",
                       extra={"offending": name})
    if r20 <= 0:
        return err("r_ohm_at_20c must be > 0", "ARG_RANGE")
    if i < 0:
        return err("current_a must be >= 0", "ARG_RANGE")
    if rth <= 0:
        return err("rth_c_per_w must be > 0", "ARG_RANGE")
    if alpha < 0:
        return err("alpha_per_k must be >= 0", "ARG_RANGE")

    solved = _solve(r20, i, rth, ta, alpha, _num(tol_c) or 1e-4,
                    int(_num(max_iter) or 2000), "conductor")
    if solved.get("status") == "error":
        return solved
    extra = dict(solved)
    extra.pop("_label", None)
    extra.update({
        "r_ohm_at_20c": _sig(r20),
        "current_a": _sig(i),
        "rth_c_per_w": _sig(rth),
        "t_ambient_c": _sig(ta),
        "alpha_per_k": _sig(alpha),
        "not_computed": list(_NOT_COMPUTED),
        "authoritative": False,
    })
    if note:
        extra["note"] = str(note)
    return ok(solved["t_final_c"], "C",
              "self-consistent 1-D electro-thermal (given R20 + one lump Rth)",
              _MODEL_TRACE + "; " + _BASIS_TRACE,
              "R(T) is exact for the linear alpha law; the temperature inherits "
              "+-10..20 % from the caller's single-lump Rth",
              {"rth_source": "caller-supplied single lump, not derived here",
               "r20_source": "caller-supplied (measured or computed elsewhere)"},
              extra)

# --------------------------------------------------- board-level 2-D (plate)

_PLATE_NOT_COMPUTED = [
    "the heat actually spreads through the laminate k given by the caller, NOT "
    "through the real copper pours and planes - a ground plane is a far better "
    "spreader than FR4 and is not modelled",
    "radiation (matters above ~60 C)",
    "thermal vias",
    "component thermal mass and component-to-board contact resistance; a "
    "uniform_power_w is spread evenly and says nothing about where the parts are",
    "AC resistance: skin and proximity effects, so I^2*R is wrong for RF current",
    "laminate limits (Tg, decomposition) - this reports temperature, not whether "
    "the board survives it",
]

_PLATE_MODEL = (
    "k*t*lap(T) - 2*h*(T-Tamb) + q(T) + C*dT/dt = 0 on a uniform rectangular "
    "grid, SOR (omega 1.85); q(T) = I^2*rho(T)*dl/(w*th) per cell with "
    "rho(T) = rho20*(1+alpha*(T-20)) carried on the matrix diagonal, so the "
    "steady problem is exactly linear and the transient is backward Euler "
    "(unconditionally stable, no dt limit)")

def _seg_cover(nx, ny, dx, dy, x1, y1, x2, y2, width):
    """Cell indices whose centre lies within `width`/2 of the segment (mm in)."""
    half = max(0.0, width) / 2.0
    vx, vy = x2 - x1, y2 - y1
    L2 = vx * vx + vy * vy
    i0 = max(0, int((min(x1, x2) - half) * 1e-3 / dx))
    i1 = min(nx - 1, int((max(x1, x2) + half) * 1e-3 / dx) + 1)
    j0 = max(0, int((min(y1, y2) - half) * 1e-3 / dy))
    j1 = min(ny - 1, int((max(y1, y2) + half) * 1e-3 / dy) + 1)
    out = []
    for i in range(i0, i1 + 1):
        cx = (i + 0.5) * dx * 1e3
        for j in range(j0, j1 + 1):
            cy = (j + 0.5) * dy * 1e3
            if L2 <= 0.0:
                d = math.hypot(cx - x1, cy - y1)
            else:
                t = ((cx - x1) * vx + (cy - y1) * vy) / L2
                if t < 0.0:
                    t = 0.0
                elif t > 1.0:
                    t = 1.0
                d = math.hypot(cx - (x1 + t * vx), cy - (y1 + t * vy))
            if d <= half:
                out.append((i, j))
    return out

def _sor_plate(nx, ny, gx, gy, hc, ta, qc, qs, capdt, prev, edge,
               tol_k, max_iter):
    """One SOR solve of (hc + capdt - qs + sum g) T = hc*Ta + qc + capdt*prev + sum g T_nb.

    `qs` is the (positive) linear slope of the source in T and sits on the
    diagonal with a minus sign - that is the whole electro-thermal coupling.
    Returns (field, iterations, last update, converged, min_diagonal).
    """
    temp = [row[:] for row in prev]
    fixed = edge is not None
    if fixed:
        for i in range(nx):
            temp[i][0] = edge
            temp[i][ny - 1] = edge
        for j in range(ny):
            temp[0][j] = edge
            temp[nx - 1][j] = edge
    omega = 1.85
    it = 0
    dmax = float("inf")
    diag_min = float("inf")
    while it < max_iter:
        it += 1
        dmax = 0.0
        for i in range(nx):
            for j in range(ny):
                if fixed and (i == 0 or j == 0 or i == nx - 1 or j == ny - 1):
                    continue
                num = hc * ta + qc[i][j] + capdt[i][j] * prev[i][j]
                den = hc + capdt[i][j] - qs[i][j]
                if i > 0:
                    num += gx * temp[i - 1][j]
                    den += gx
                if i < nx - 1:
                    num += gx * temp[i + 1][j]
                    den += gx
                if j > 0:
                    num += gy * temp[i][j - 1]
                    den += gy
                if j < ny - 1:
                    num += gy * temp[i][j + 1]
                    den += gy
                if den < diag_min:
                    diag_min = den
                new = num / den
                old = temp[i][j]
                d = omega * (new - old)
                temp[i][j] = old + d
                ad = abs(d)
                if ad > dmax:
                    dmax = ad
        if dmax < tol_k:
            return temp, it, dmax, True, diag_min
    return temp, it, dmax, False, diag_min

def board_electrothermal(width_mm, height_mm, thickness_mm, k_w_mk, h_w_m2k,
                         conductors, t_ambient_c=25.0, cell_mm=None,
                         edge_temp_c=None, uniform_power_w=None,
                         alpha_per_k=ALPHA_CU, rho20_ohm_m=RHO20_CU,
                         tol_k=1e-4, max_iter=200, sor_max_iter=60000,
                         max_cells=20000, transient=None):
    """Board-level 2-D electro-thermal: copper I^2R feeding a plate solve.

    `electrothermal_trace` closes the loop against ONE lump Rth. A board is not
    one lump: a 3 mm trace dumping 4 W is a different problem from the same 4 W
    spread over 100 mm, and the hot spot is what decides whether the board works.
    So this solves the same R(T) <-> T loop, but T is a 2-D field.

    `conductors` are the heat sources *and* the resistances:

        {"x1_mm", "y1_mm", "x2_mm", "y2_mm", "width_mm", "thickness_um",
         "current_a", "layers" (optional, default 1)}

    Each trace is rasterised onto the grid; its total I^2R is split evenly over
    the covered cells, so the total power is exact and only its distribution is
    quantised by cell_mm. Because rho(T) is linear in T, the source is written as
    q = q_ref*(1-alpha*20) + q_ref*alpha*T and the alpha*T term goes on the
    matrix diagonal: the steady problem is then EXACTLY linear, solved by SOR.

    Runaway: with conduction present the 1-D test dP/dT*Rth < 1 does not
    generalise, so the honest statements are (a) is any diagonal non-positive,
    and (b) is the resulting field physical. Both are checked. `uniform_power_w`
    spreads a total dissipation evenly over the plate; that case has a closed
    form (see docs/20) and is what the tests pin.

    `transient` = {"t_end_s", "points", "cp_j_kg_k", "rho_kg_m3",
    "initial_c" (optional)} steps the same field with backward Euler.
    """
    W = _num(width_mm)
    H = _num(height_mm)
    th = _num(thickness_mm)
    k = _num(k_w_mk)
    h = _num(h_w_m2k)
    ta = _num(t_ambient_c)
    alpha = _num(alpha_per_k)
    rho20 = _num(rho20_ohm_m)
    for name, v in (("width_mm", W), ("height_mm", H), ("thickness_mm", th),
                    ("k_w_mk", k), ("t_ambient_c", ta), ("alpha_per_k", alpha),
                    ("rho20_ohm_m", rho20)):
        if v is None:
            return err("%s must be a number" % name, "ARG_INVALID",
                       extra={"offending": name})
    for name, v in (("width_mm", W), ("height_mm", H), ("thickness_mm", th),
                    ("k_w_mk", k), ("rho20_ohm_m", rho20)):
        if v <= 0:
            return err("%s must be > 0" % name, "ARG_RANGE",
                       extra={"offending": name})
    if alpha < 0:
        return err("alpha_per_k must be >= 0", "ARG_RANGE")
    if alpha * T20 >= 1.0:
        return err("alpha_per_k = %.4g makes 1 - alpha*20 <= 0, so the linear "
                   "resistivity law is not even positive at 0 C" % alpha,
                   "ARG_RANGE")
    if h is None or h <= 0:
        return err("h_w_m2k must be > 0 and stated", "ARG_REQUIRED",
                   hint="natural convection in still air is ~5..10 W/(m^2*K) per "
                        "exposed face, ~2x if both faces see open air; forced air "
                        "is 20..100. Not guessed here")
    if uniform_power_w is not None:
        up = _num(uniform_power_w)
        if up is None or up < 0:
            return err("uniform_power_w must be a number >= 0", "ARG_RANGE")
    else:
        up = 0.0
    conductors = list(conductors) if conductors else []
    if not conductors and up <= 0:
        return err("no heat source: conductors is empty and uniform_power_w is "
                   "not set", "ARG_REQUIRED",
                   hint='a conductor is {"x1_mm","y1_mm","x2_mm","y2_mm",'
                        '"width_mm","thickness_um","current_a"}')
    edge = None
    if edge_temp_c is not None:
        edge = _num(edge_temp_c)
        if edge is None:
            return err("edge_temp_c must be a number", "ARG_INVALID")

    step = _num(cell_mm) if cell_mm is not None else min(W, H) / 40.0
    if step is None or step <= 0:
        return err("cell_mm must be > 0", "ARG_RANGE")
    nx = max(3, int(round(W / step)))
    ny = max(3, int(round(H / step)))
    if nx * ny > max_cells:
        return err("grid would be %dx%d = %d cells (limit %d)" %
                   (nx, ny, nx * ny, max_cells), "TOO_LARGE",
                   hint="raise cell_mm; a hot-spot estimate does not need a fine "
                        "grid everywhere, but it does need the trace widths "
                        "resolved")
    dx = (W / nx) * 1e-3
    dy = (H / ny) * 1e-3
    t_m = th * 1e-3
    gp = k * t_m
    gx = gp * dy / dx
    gy = gp * dx / dy
    ac = dx * dy
    hc = 2.0 * h * ac

    # q_ref = the I^2R each cell would dissipate at 20 C (W)
    q_ref = [[0.0] * ny for _ in range(nx)]
    total_p20 = 0.0
    placed = []
    if up > 0:
        share = up / float(nx * ny)
        for i in range(nx):
            for j in range(ny):
                q_ref[i][j] += share
        total_p20 += up
    for idx, c in enumerate(conductors):
        if not isinstance(c, dict):
            return err("conductor #%d is not an object" % idx, "ARG_INVALID")
        try:
            x1 = float(c["x1_mm"]); y1 = float(c["y1_mm"])
            x2 = float(c["x2_mm"]); y2 = float(c["y2_mm"])
            cw = float(c["width_mm"]); cth = float(c["thickness_um"])
            cur = float(c["current_a"])
        except (KeyError, TypeError, ValueError):
            return err("conductor #%d needs numeric x1_mm, y1_mm, x2_mm, y2_mm, "
                       "width_mm, thickness_um, current_a" % idx, "ARG_INVALID")
        layers = c.get("layers", 1)
        try:
            layers = int(layers)
        except (TypeError, ValueError):
            return err("conductor #%d: layers must be an integer" % idx,
                       "ARG_INVALID")
        if cw <= 0 or cth <= 0:
            return err("conductor #%d: width_mm and thickness_um must be > 0" % idx,
                       "ARG_RANGE")
        if cur < 0:
            return err("conductor #%d: current_a must be >= 0" % idx, "ARG_RANGE")
        if layers < 1:
            return err("conductor #%d: layers must be >= 1" % idx, "ARG_RANGE")
        if not (0.0 <= x1 <= W and 0.0 <= x2 <= W and
                0.0 <= y1 <= H and 0.0 <= y2 <= H):
            return err("conductor #%d runs outside the plate %.6g x %.6g mm" %
                       (idx, W, H), "ARG_RANGE")
        length_mm = math.hypot(x2 - x1, y2 - y1)
        if length_mm <= 0:
            return err("conductor #%d has zero length" % idx, "ARG_RANGE")
        cells = _seg_cover(nx, ny, dx, dy, x1, y1, x2, y2, cw)
        if not cells:
            return err("conductor #%d is thinner than the grid: no cell centre lies "
                       "within width/2 = %.4g mm of the segment. Lower cell_mm."
                       % (idx, cw / 2.0), "ARG_RANGE")
        dl_m = (length_mm / len(cells)) * 1e-3
        w_m = cw * 1e-3
        t_one = cth * 1e-6
        # R of one cell at 20 C; layers are parallel copper (assumes stitched)
        r_cell20 = rho20 * dl_m / (w_m * t_one * layers)
        p_cell20 = cur * cur * r_cell20
        for (i, j) in cells:
            q_ref[i][j] += p_cell20
        total_p20 += p_cell20 * len(cells)
        placed.append({
            "index": idx, "x1_mm": x1, "y1_mm": y1, "x2_mm": x2, "y2_mm": y2,
            "width_mm": cw, "thickness_um": cth, "current_a": cur,
            "layers": layers, "length_mm": _sig(length_mm),
            "cells": len(cells),
            "r20_ohm": _sig(rho20 * (length_mm * 1e-3) / (w_m * t_one * layers)),
            "power_at_20c_w": _sig(cur * cur * rho20 * (length_mm * 1e-3) /
                                   (w_m * t_one * layers)),
        })
    if total_p20 <= 0:
        return err("the sources carry no power", "ARG_RANGE",
                   hint="all currents are zero and uniform_power_w is 0, so the "
                        "whole board is at t_ambient_c and there is nothing to solve")
    if edge is None and hc <= 0:
        return err("no way for heat to leave: h and edge_temp_c are both unset",
                   "ARG_REQUIRED")

    # q = q_ref*(1 + alpha*(T-20)) = q_const + q_slope*T
    c0 = 1.0 - alpha * T20
    q_const = [[q_ref[i][j] * c0 for j in range(ny)] for i in range(nx)]
    q_slope = [[q_ref[i][j] * alpha for j in range(ny)] for i in range(nx)]

    zero_cap = [[0.0] * ny for _ in range(nx)]

    # ---- transient setup (or a single steady solve with capdt = 0)
    steps = 1
    dt = 0.0
    capdt = zero_cap
    tr = None
    if transient is not None:
        if not isinstance(transient, dict):
            return err("transient must be an object", "ARG_INVALID")
        t_end = _num(transient.get("t_end_s"))
        pts = transient.get("points", 40)
        try:
            pts = int(pts)
        except (TypeError, ValueError):
            return err("transient.points must be an integer", "ARG_INVALID")
        cp = _num(transient.get("cp_j_kg_k"))
        rhos = _num(transient.get("rho_kg_m3"))
        for name, v in (("t_end_s", t_end), ("cp_j_kg_k", cp),
                        ("rho_kg_m3", rhos)):
            if v is None or v <= 0:
                return err("transient.%s is required and must be > 0" % name,
                           "ARG_RANGE" if v is not None else "ARG_REQUIRED",
                           hint="FR4 is roughly cp 1150 J/(kg*K), rho 1850 kg/m^3; "
                                "the plate thickness is the heat capacity you are "
                                "modelling, nothing else")
        if pts < 2:
            return err("transient.points must be >= 2", "ARG_RANGE")
        dt = t_end / (pts - 1)
        # `points` counts the samples INCLUDING t = 0, so there are pts-1 steps
        # and the last one lands exactly on t_end_s.
        cc = rhos * cp * ac * t_m          # J/K per cell
        capdt = [[cc / dt] * ny for _ in range(nx)]
        steps = pts - 1
        t0 = ta
        if transient.get("initial_c") is not None:
            t0 = _num(transient.get("initial_c"))
            if t0 is None:
                return err("transient.initial_c must be a number", "ARG_INVALID")
        tr = {"t_end_s": _sig(t_end), "points": pts, "dt_s": _sig(dt),
              "cp_j_kg_k": _sig(cp), "rho_kg_m3": _sig(rhos),
              "heat_capacity_j_per_k": _sig(cc * nx * ny),
              "initial_c": _sig(t0), "history": []}

    # The runaway test does not need the solve: the diagonal of this operator is
    # constant per cell (hc + C/dt - alpha*q_ref), so a non-positive diagonal can
    # be refused BEFORE the SOR dives to infinity. This is the 2-D face of the
    # 1-D rule dP/dT*Rth < 1, and on a uniform plate it reduces to exactly it
    # (P*alpha < 2hA).
    diag_worst = None
    for i in range(nx):
        for j in range(ny):
            dv = hc + capdt[i][j] - q_slope[i][j]
            if diag_worst is None or dv < diag_worst:
                diag_worst = dv
    if diag_worst <= 0.0:
        return err(
            "thermal runaway: the smallest diagonal (convection + conduction - "
            "alpha*I^2*R slope) of the electro-thermal operator is %.4g <= 0, so "
            "no physical steady state exists. The board gets hotter, the copper "
            "gets more resistive, and that adds more heat than the cell can shed"
            % diag_worst, "THERMAL_RUNAWAY",
            hint="lower the current, add copper / layers, thicken the copper, "
                 "increase h, or spread the heat over more board - iterating "
                 "harder cannot fix this",
            extra={"worst_diagonal": _sig(diag_worst),
                   "alpha_per_k": _sig(alpha),
                   "total_power_at_20c_w": _sig(total_p20)})

    field = [[ta] * ny for _ in range(nx)]
    if tr is not None:
        field = [[tr["initial_c"]] * ny for _ in range(nx)]
        tr["history"].append({"t_s": 0.0,
                              "t_max_c": round(tr["initial_c"], 6)})
    elif edge is None:
        # start from the 1-D local answer: already the right size, and it saves
        # a factor of 3 in sweeps because the slowest mode is the plate average
        guess = ta + total_p20 / (hc * nx * ny)
        field = [[guess] * ny for _ in range(nx)]

    iters_total = 0
    prev = field
    final = None
    for s in range(steps):
        cur_field, it, dmax, conv, diag_min = _sor_plate(
            nx, ny, gx, gy, hc, ta, q_const, q_slope, capdt, prev, edge,
            tol_k, sor_max_iter)
        iters_total += it
        if diag_min <= 0.0:
            return err(
                "thermal runaway: at least one cell has (convection + conduction "
                "- alpha*I^2*R) = %.4g <= 0, so the steady operator is not an "
                "M-matrix and no physical steady state exists. The board gets "
                "hotter, the copper gets more resistive, and that adds more heat "
                "than the cell can shed" % diag_min, "THERMAL_RUNAWAY",
                hint="lower the current, add copper / layers, thicken the copper, "
                     "increase h, or move the trace to a bigger board - this "
                     "module cannot fix it by iterating harder",
                extra={"worst_diagonal": _sig(diag_min),
                       "alpha_per_k": _sig(alpha),
                       "total_power_at_20c_w": _sig(total_p20)})
        if not conv:
            return err(
                "the 2-D field did not converge: largest update still %.3g K after "
                "%d sweeps (tol %.3g)" % (dmax, it, tol_k), "NOT_CONVERGED",
                hint="raise sor_max_iter; a half-solved field looks exactly like a "
                     "real one, so nothing is reported from it",
                extra={"sweeps": it, "last_update_k": _sig(dmax),
                       "step_index": s})
        prev = cur_field
        final = cur_field
        if tr is not None:
            tmax = max(max(r) for r in cur_field)
            tr["history"].append({"t_s": _sig((s + 1) * dt),
                                  "t_max_c": round(tmax, 6)})

    temp = final
    for row in temp:
        for v in row:
            if v != v or v in (float("inf"), float("-inf")):
                return err("the 2-D field contains a non-finite value after %d "
                           "sweeps" % iters_total, "NOT_CONVERGED",
                           hint="this is numerical divergence of the SOR, not a "
                                "temperature; nothing is reported from it")
    tmax = max(max(r) for r in temp)
    tmin = min(min(r) for r in temp)
    tavg = sum(sum(r) for r in temp) / (nx * ny)

    # A solution of this system with all sources >= 0 and a fixed ambient must
    # never sit below the ambient it is dumping heat into. If it does, the matrix
    # inverted to something non-physical, which is the other face of runaway.
    if tmin < ta - 1e-6:
        return err(
            "the solved field is not physical: minimum temperature %.6g C is "
            "below the ambient %.6g C while every source is a heater" % (tmin, ta),
            "THERMAL_RUNAWAY",
            hint="this is what a not-diagonally-dominant electro-thermal matrix "
                 "looks like when it still factorises. Treat it as runaway, not as "
                 "a numerical curiosity")

    # energy balance, computed rather than assumed
    out_conv = 0.0
    for i in range(nx):
        for j in range(ny):
            out_conv += hc * (temp[i][j] - ta)
    out_edge = 0.0
    if edge is not None:
        for i in range(nx):
            for j in range(ny):
                if i not in (0, nx - 1) and j not in (0, ny - 1):
                    continue
                sv = 0.0
                if i > 0:
                    sv += gx * (temp[i][j] - temp[i - 1][j])
                if i < nx - 1:
                    sv += gx * (temp[i][j] - temp[i + 1][j])
                if j > 0:
                    sv += gy * (temp[i][j] - temp[i][j - 1])
                if j < ny - 1:
                    sv += gy * (temp[i][j] - temp[i][j + 1])
                out_edge -= sv
    p_in = 0.0
    for i in range(nx):
        for j in range(ny):
            p_in += q_ref[i][j] * (1.0 + alpha * (temp[i][j] - T20))
    residual = p_in - out_conv - out_edge
    resid_pct = 100.0 * residual / p_in if p_in else 0.0
    if tr is None and abs(resid_pct) > 0.5:
        return err("energy does not balance: %.3f%% of the input power is "
                   "unaccounted for" % resid_pct, "INTERNAL",
                   hint="the field is not a solution of the stated model, so it is "
                        "not reported")

    src_temps = []
    for p in placed:
        cells = _seg_cover(nx, ny, dx, dy, p["x1_mm"], p["y1_mm"],
                           p["x2_mm"], p["y2_mm"], p["width_mm"])
        ts = [temp[i][j] for (i, j) in cells] or [ta]
        src_temps.append({"index": p["index"],
                          "r20_ohm": p["r20_ohm"],
                          "r_hot_ohm": _sig(p["r20_ohm"] *
                                            (1.0 + alpha * (max(ts) - T20))),
                          "power_at_20c_w": p["power_at_20c_w"],
                          "power_hot_w": _sig(p["power_at_20c_w"] *
                                              (1.0 + alpha * (max(ts) - T20))),
                          "t_max_c": round(max(ts), 6),
                          "t_avg_c": round(sum(ts) / len(ts), 6),
                          "cells": p["cells"],
                          "length_mm": p["length_mm"],
                          "current_a": p["current_a"]})

    rows = []
    nc, nr = 32, 16
    span = (tmax - ta) or 1.0
    for r in range(nr):
        line = ""
        for c in range(nc):
            i = min(nx - 1, int((c + 0.5) / nc * nx))
            j = min(ny - 1, int((r + 0.5) / nr * ny))
            f = (temp[i][j] - ta) / span
            line += " .:-=+*#%@"[max(0, min(9, int(f * 9.999)))]
        rows.append(line)

    extra = {
        "t_max_c": round(tmax, 6),
        "t_min_c": round(tmin, 6),
        "t_avg_c": round(tavg, 6),
        "t_ambient_c": ta,
        "delta_t_max_k": round(tmax - ta, 6),
        "spread_k": round(tmax - tavg, 6),
        "conductors": src_temps,
        "uniform_power_w": _sig(up) if up > 0 else None,
        "total_power_at_20c_w": _sig(total_p20),
        "total_power_hot_w": _sig(p_in),
        "grid": [nx, ny],
        "cell_mm": [round(dx * 1e3, 9), round(dy * 1e3, 9)],
        "sor_sweeps": iters_total,
        "energy_balance": {"power_in_w": _sig(p_in),
                           "power_out_convection_w": _sig(out_conv),
                           "power_out_fixed_edges_w": _sig(out_edge),
                           "residual_pct": _sig(resid_pct)},
        "effective_rth_k_per_w": _sig((tmax - ta) / p_in) if p_in else None,
        "map": rows,
        "map_legend": "row 0 is y=0; '@' is the hottest cell",
        "not_computed": list(_PLATE_NOT_COMPUTED),
        "authoritative": False,
    }
    if tr is not None:
        extra["transient"] = tr
    if tmax > 100.0:
        extra["warnings"] = [
            "t_max = %.4g C is past 100 C, where the linear rho(T) law starts to "
            "overstate dR (and the temperature coefficient itself stops being "
            "constant)" % tmax]

    model = ("board 2-D electro-thermal: " + _PLATE_MODEL +
             (" + backward Euler (dt = %.4g s, %d steps)" % (dt, steps)
              if tr is not None else ""))
    return ok({"t_max_c": round(tmax, 6),
               "t_min_c": round(tmin, 6),
               "t_avg_c": round(tavg, 6),
               "delta_t_max_k": round(tmax - ta, 6)},
              "C", model,
              _BASIS_TRACE,
              "as good as k and h, and the hot spot is grid dependent: compare "
              "two layouts only at the same cell_mm. The spreading medium here is "
              "the laminate k you passed, not the real copper pours, so a design "
              "with a plane under the trace is cooler than this says",
              {"rth_source": "emerging from the 2-D field, not supplied",
               "copper_layers": "parallel copper assumed stitched at both ends",
               "heat_capacity": ("plate volume only (%s)"
                                 % ("rho*cp*dx*dy*t" if tr is not None
                                    else "not modelled"))},
              extra)
