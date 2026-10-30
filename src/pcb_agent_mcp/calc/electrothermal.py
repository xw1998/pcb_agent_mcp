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
from .thermal import _barrel_profile_rth

RHO20_CU = 1.724e-8      # ohm*m, IACS copper at 20 C
ALPHA_CU = 0.00393       # 1/K, linear region 0..100 C
T20 = 20.0               # C, the reference temperature of both constants
K_CU = 400.0             # W/(m*K), bulk copper. 401 at 300 K, 393 at 373 K;
                         # this module is a screening estimate, so one number in
                         # the middle of that range, stated rather than tuned.
SIGMA_SB = 5.670374419e-8  # W/(m^2*K^4), Stefan-Boltzmann (CODATA). Radiation
                           # is the one loss that does not need a material table:
                           # it needs an emissivity, and that one is yours.


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

def _rect_cover(nx, ny, dx, dy, x1, y1, x2, y2):
    """Per-cell area overlap fraction of an axis-aligned rectangle (mm in).

    Exact area overlap, not "cell centre inside": a pour edge that falls in the
    middle of a cell has to show up as 0.5 in that cell, otherwise the sheet
    conductance of a partly covered cell is a coin flip on where the grid lands.
    """
    cov = [[0.0] * ny for _ in range(nx)]
    xlo, xhi = (x1, x2) if x1 <= x2 else (x2, x1)
    ylo, yhi = (y1, y2) if y1 <= y2 else (y2, y1)
    cw = dx * 1e3
    ch = dy * 1e3
    i0 = max(0, int(math.floor(xlo / cw)))
    i1 = min(nx - 1, int(math.floor(xhi / cw)))
    j0 = max(0, int(math.floor(ylo / ch)))
    j1 = min(ny - 1, int(math.floor(yhi / ch)))
    for i in range(i0, i1 + 1):
        ox = min(xhi, (i + 1) * cw) - max(xlo, i * cw)
        if ox <= 0.0:
            continue
        for j in range(j0, j1 + 1):
            oy = min(yhi, (j + 1) * ch) - max(ylo, j * ch)
            if oy <= 0.0:
                continue
            cov[i][j] = (ox / cw) * (oy / ch)
    return cov

def _sor_plate_map(nx, ny, fx, fy, hc, ta, qc, qs, capdt, prev, edge,
                   tol_k, max_iter):
    """Same SOR as `_sor_plate`, but the face conductance varies per cell.

    `fx[i][j]` is the conductance (W/K) of the face between (i, j) and
    (i+1, j); `fy[i][j]` between (i, j) and (i, j+1). Kept as a separate
    function on purpose: the uniform-k path must stay bit-for-bit what it was,
    and a per-cell `isinstance` inside the innermost loop would tax every sweep
    for a feature that is usually off.
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
                    g = fx[i - 1][j]
                    num += g * temp[i - 1][j]
                    den += g
                if i < nx - 1:
                    g = fx[i][j]
                    num += g * temp[i + 1][j]
                    den += g
                if j > 0:
                    g = fy[i][j - 1]
                    num += g * temp[i][j - 1]
                    den += g
                if j < ny - 1:
                    g = fy[i][j]
                    num += g * temp[i][j + 1]
                    den += g
                if den < diag_min:
                    diag_min = den
                new = num / den
                old = temp[i][j]
                dd = omega * (new - old)
                temp[i][j] = old + dd
                ad = abs(dd)
                if ad > dmax:
                    dmax = ad
        if dmax < tol_k:
            return temp, it, dmax, True, diag_min
    return temp, it, dmax, False, diag_min

def _sheet_faces(nx, ny, dx, dy, S):
    """Face conductances (W/K) from a per-cell sheet conductance S (W/K).

    Harmonic mean across the face = two half-cells in series, which is the
    finite-volume answer for a material discontinuity. With a uniform S it
    reduces to S*dy/dx, i.e. exactly the uniform-k operator.
    """
    fx = [[0.0] * ny for _ in range(nx - 1)]
    fy = [[0.0] * (ny - 1) for _ in range(nx)]
    for i in range(nx - 1):
        for j in range(ny):
            a = S[i][j]
            b = S[i + 1][j]
            fx[i][j] = (2.0 * a * b / (a + b)) * dy / dx if (a + b) > 0 else 0.0
    for i in range(nx):
        for j in range(ny - 1):
            a = S[i][j]
            b = S[i][j + 1]
            fy[i][j] = (2.0 * a * b / (a + b)) * dx / dy if (a + b) > 0 else 0.0
    return fx, fy

def _h_rad_w_m2k(t_c, t_amb_c, emissivity):
    """Linearised grey-body coefficient, W/(m^2*K), at the film temperature.

    q_rad = eps*sigma*(T^4 - Ta^4) = h_rad*(T - Ta) with
    h_rad = eps*sigma*(T + Ta)*(T^2 + Ta^2) in kelvin - an EXACT identity, no
    small-dT expansion. It is still temperature dependent, so the plate solve
    lags it on the previous iterate and repeats the whole field until it stops
    moving (see `emissivity` in `board_electrothermal`).
    """
    t1 = t_c + 273.15
    t2 = t_amb_c + 273.15
    return emissivity * SIGMA_SB * (t1 + t2) * (t1 * t1 + t2 * t2)

def _via_centres(nx, ny, dx, dy, x1, y1, x2, y2, pitch):
    """Cell indices of an axis-aligned via array; centres start at (x1, y1).

    Returns (cells, n_x, n_y). Unlike a pour, a via is a POINT: its whole
    conductance lands in the one cell that contains its centre, which is why a
    half-cell shift can move it to a neighbouring cell. That grid dependence is
    stated in the answer instead of being smeared away - smearing a point into
    four cells would make 100 vias in one cell look like 25 in each of four.
    """
    def axis(lo, hi, p, n, d):
        span = hi - lo
        if span < 0:
            lo, hi, span = hi, lo, -span
        k = int(math.floor(span / p + 1e-9))
        return [int(math.floor((lo + m * p) * 1e-3 / d)) for m in range(k + 1)]
    xs = axis(x1, x2, pitch, nx, dx)
    ys = axis(y1, y2, pitch, ny, dy)
    cells = []
    for i in xs:
        if i < 0 or i >= nx:
            continue
        for j in ys:
            if j < 0 or j >= ny:
                continue
            cells.append((i, j))
    return cells, len(xs), len(ys)

def _sor_plate_add(nx, ny, fx, fy, gadd, gt, hc, ta, qc, qs, capdt, prev,
                   edge, tol_k, max_iter):
    """`_sor_plate_map` plus a per-cell conductance to a node we do not solve.

    `gadd[i][j]` is that conductance (W/K) and `gt[i][j]` is the same conductance
    already multiplied by the node temperature (W, i.e. gadd*T_node). Radiation
    and a thermal via are BOTH exactly this shape - a linear link to a temperature
    outside the field - so one loop serves both. Its own function for the same
    reason as `_sor_plate_map`: the laminate-only and pours-only paths have to
    stay bit-for-bit what they were, and both of the additions above are off by
    default.
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
                num = (hc * ta + qc[i][j] + capdt[i][j] * prev[i][j]
                       + gt[i][j])
                den = hc + capdt[i][j] - qs[i][j] + gadd[i][j]
                if i > 0:
                    g = fx[i - 1][j]
                    num += g * temp[i - 1][j]
                    den += g
                if i < nx - 1:
                    g = fx[i][j]
                    num += g * temp[i + 1][j]
                    den += g
                if j > 0:
                    g = fy[i][j - 1]
                    num += g * temp[i][j - 1]
                    den += g
                if j < ny - 1:
                    g = fy[i][j]
                    num += g * temp[i][j + 1]
                    den += g
                if den < diag_min:
                    diag_min = den
                new = num / den
                old = temp[i][j]
                dd = omega * (new - old)
                temp[i][j] = old + dd
                ad = abs(dd)
                if ad > dmax:
                    dmax = ad
        if dmax < tol_k:
            return temp, it, dmax, True, diag_min
    return temp, it, dmax, False, diag_min

def board_electrothermal(width_mm, height_mm, thickness_mm, k_w_mk, h_w_m2k,
                         conductors, t_ambient_c=25.0, cell_mm=None,
                         edge_temp_c=None, uniform_power_w=None,
                         copper_pours=None, k_copper_w_mk=None,
                         emissivity=None, thermal_vias=None,
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

    `copper_pours` (optional) is how a plane or a pour gets into the answer.
    Without it the spreading medium is the laminate k alone, which understates
    every board that has copper under the hot part. Each pour is

        {"x1_mm", "y1_mm", "x2_mm", "y2_mm", "thickness_um", "layers" (default 1)}

    and is turned into a per-cell sheet conductance

        S[i][j] = k_laminate*t_board + sum_over_pours(frac[i][j]*t_cu*N)*k_cu

    where frac is the EXACT area overlap of the rectangle with that cell (a pour
    edge in the middle of a cell is 0.5, not 0 or 1). Faces then use the
    harmonic mean of the two cell conductances. This is the standard smeared-
    copper model: it is *in-plane* sheet conduction only, so it adds copper
    where copper actually is, and it says nothing about the vertical path (that
    stays inside h). `k_copper_w_mk` defaults to K_CU = 400.

    `emissivity` (optional, 0..1) turns on radiation. It is NOT a fudge factor on
    h: q_rad = eps*sigma*(T^4 - Ta^4) is rewritten exactly as h_rad*(T - Ta) with
    h_rad = eps*sigma*(T+Ta)*(T^2+Ta^2) in kelvin, applied to both faces. Because
    h_rad depends on T, the field is solved with h_rad lagged on the previous
    iterate and the whole solve repeats until the field stops moving - the answer
    is the fixed point, not one pass. Leaving it out is the same as eps = 0 and
    changes nothing, bit for bit.

    `thermal_vias` (optional) is how a via field gets into the answer: each array
    is

        {"x1_mm", "y1_mm", "x2_mm", "y2_mm", "pitch_mm", "d_mm",
         "plating_um", "span_mm" (default = plate thickness), "sink_c",
         "barrel_profile"?}

    and every via becomes ONE conductance to the sink node you name,

        G = k_cu * pi*((d/2)^2 - (d/2 - plating)^2) / span        [W/K]

    `barrel_profile` (optional, per array) replaces that single wall with a wall
    that varies along the barrel: [{"length_mm": .., "plating_um": ..}, ...] from
    one end to the other, whose lengths add up to span_mm. The segments are in
    SERIES, G = 1/sum(L_i/(k_cu*A_i)), so a thin middle is not averaged away.
    Leaving it out is the uniform barrel, bit for bit; the answer always says
    which wall it used.

    i.e. the annulus of the barrel (the drill area is the classic factor-of-10
    exaggeration), landing in the cell that contains the via centre. `sink_c`
    defaults to the ambient and the answer says which one it used - a via to a
    heatsink at 60 C and a via to 25 C are different boards. The laminate's own
    vertical conduction is already lumped inside h, so it is not added in
    parallel here.
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

    # ---- optional copper: a per-cell sheet conductance, off unless pours given
    pours = []
    t_cu = None
    tof_cu = 0.0
    kcu = K_CU if k_copper_w_mk is None else _num(k_copper_w_mk)
    if kcu is None or kcu <= 0:
        return err("k_copper_w_mk must be a number > 0", "ARG_RANGE")
    if copper_pours is not None:
        if not isinstance(copper_pours, (list, tuple)):
            return err("copper_pours must be a list of rectangles",
                       "ARG_INVALID",
                       hint='each pour is {"x1_mm","y1_mm","x2_mm","y2_mm",'
                            '"thickness_um","layers"?}')
        t_cu = [[0.0] * ny for _ in range(nx)]
        covered_cells = set()
        for idx, pz in enumerate(copper_pours):
            if not isinstance(pz, dict):
                return err("copper pour #%d is not an object" % idx,
                           "ARG_INVALID")
            try:
                px1 = float(pz["x1_mm"]); py1 = float(pz["y1_mm"])
                px2 = float(pz["x2_mm"]); py2 = float(pz["y2_mm"])
                ptu = float(pz["thickness_um"])
            except (KeyError, TypeError, ValueError):
                return err("copper pour #%d needs numeric x1_mm, y1_mm, x2_mm, "
                           "y2_mm, thickness_um" % idx, "ARG_INVALID")
            pl = pz.get("layers", 1)
            try:
                pl = int(pl)
            except (TypeError, ValueError):
                return err("copper pour #%d: layers must be an integer" % idx,
                           "ARG_INVALID")
            if pl < 1:
                return err("copper pour #%d: layers must be >= 1" % idx,
                           "ARG_RANGE")
            if ptu < 0:
                return err("copper pour #%d: thickness_um must be >= 0 "
                           "(0 is allowed and must change nothing)" % idx,
                           "ARG_RANGE")
            if not (0.0 <= px1 <= W and 0.0 <= px2 <= W and
                    0.0 <= py1 <= H and 0.0 <= py2 <= H):
                return err("copper pour #%d runs outside the plate %.6g x %.6g mm"
                           % (idx, W, H), "ARG_RANGE")
            if abs(px2 - px1) <= 0.0 or abs(py2 - py1) <= 0.0:
                return err("copper pour #%d has zero area" % idx, "ARG_RANGE")
            cov = _rect_cover(nx, ny, dx, dy, px1, py1, px2, py2)
            cells = 0
            area = 0.0
            t_here = ptu * 1e-6 * pl
            for i in range(nx):
                for j in range(ny):
                    f = cov[i][j]
                    if f <= 0.0:
                        continue
                    t_cu[i][j] += f * t_here
                    cells += 1
                    area += f
                    covered_cells.add((i, j))
            if cells <= 0:
                return err("copper pour #%d is thinner than the grid: no cell is "
                           "overlapped at all. Lower cell_mm." % idx, "ARG_RANGE")
            tof_cu += t_here
            pours.append({"index": idx,
                          "x1_mm": _sig(px1), "y1_mm": _sig(py1),
                          "x2_mm": _sig(px2), "y2_mm": _sig(py2),
                          "thickness_um": _sig(ptu), "layers": pl,
                          "cells": cells,
                          "area_fraction_of_board": _sig(area / (nx * ny))})
    S = None
    fx = fy = None
    if pours:
        S = [[gp + kcu * t_cu[i][j] for j in range(ny)] for i in range(nx)]
        fx, fy = _sheet_faces(nx, ny, dx, dy, S)

    # ---- optional radiation: a linearised grey body on both faces
    emis = None
    if emissivity is not None:
        emis = _num(emissivity)
        if emis is None:
            return err("emissivity must be a number", "ARG_INVALID")
        if emis < 0.0 or emis > 1.0:
            return err("emissivity must be between 0 and 1 (black-body = 1; a "
                       "solder-mask surface is ~0.9, bare copper ~0.05..0.1 but "
                       "that spread is real, so it is your number, not mine)",
                       "ARG_RANGE")
        if emis == 0.0:
            emis = None          # eps = 0 is exactly "no radiation"

    # ---- optional thermal vias: point conductances to a sink node
    g_via = None
    gt_via = None
    via_arrays = []
    if thermal_vias is not None:
        if not isinstance(thermal_vias, (list, tuple)):
            return err("thermal_vias must be a list of via arrays",
                       "ARG_INVALID",
                       hint='each array is {"x1_mm","y1_mm","x2_mm","y2_mm",'
                            '"pitch_mm","d_mm","plating_um","sink_c"?,'
                            '"span_mm"?}; centres start at (x1_mm, y1_mm)')
        g_via = [[0.0] * ny for _ in range(nx)]
        gt_via = [[0.0] * ny for _ in range(nx)]
        for idx, va in enumerate(thermal_vias):
            if not isinstance(va, dict):
                return err("thermal via array #%d is not an object" % idx,
                           "ARG_INVALID")
            try:
                vx1 = float(va["x1_mm"]); vy1 = float(va["y1_mm"])
                vx2 = float(va["x2_mm"]); vy2 = float(va["y2_mm"])
                vpitch = float(va["pitch_mm"]); vd = float(va["d_mm"])
                vpl = float(va["plating_um"])
            except (KeyError, TypeError, ValueError):
                return err("thermal via array #%d needs numeric x1_mm, y1_mm, "
                           "x2_mm, y2_mm, pitch_mm, d_mm, plating_um" % idx,
                           "ARG_INVALID")
            for nm, v in (("pitch_mm", vpitch), ("d_mm", vd),
                          ("plating_um", vpl)):
                if v <= 0:
                    return err("thermal via array #%d: %s must be > 0" %
                               (idx, nm), "ARG_RANGE")
            if not (0.0 <= vx1 <= W and 0.0 <= vx2 <= W and
                    0.0 <= vy1 <= H and 0.0 <= vy2 <= H):
                return err("thermal via array #%d runs outside the plate "
                           "%.6g x %.6g mm" % (idx, W, H), "ARG_RANGE")
            vpl_mm = vpl / 1000.0
            if 2.0 * vpl_mm >= vd:
                return err("thermal via array #%d: plating_um is not smaller "
                           "than the drill radius, so the barrel would be solid "
                           "and the annulus stops meaning anything" % idx,
                           "ARG_RANGE")
            span_mm = th
            if va.get("span_mm") is not None:
                span_mm = _num(va.get("span_mm"))
                if span_mm is None or span_mm <= 0:
                    return err("thermal via array #%d: span_mm must be > 0" % idx,
                               "ARG_RANGE")
            sink = ta
            sink_src = "ambient"
            if va.get("sink_c") is not None:
                sink = _num(va.get("sink_c"))
                if sink is None:
                    return err("thermal via array #%d: sink_c must be a number"
                               % idx, "ARG_INVALID")
                sink_src = "caller"
            r_out = vd / 2.0
            r_in = r_out - vpl_mm
            a_mm2 = math.pi * (r_out ** 2 - r_in ** 2)
            vprof = va.get("barrel_profile")
            r_barrel = None
            prof_detail = []
            t_eq_um = None
            if vprof is not None:
                if not isinstance(vprof, (list, tuple)) or not vprof:
                    return err("thermal via array #%d: barrel_profile must be a "
                               "non-empty list of {\"length_mm\", \"plating_um\"} "
                               "segments" % idx, "ARG_INVALID",
                               hint='[{"length_mm": 0.55, "plating_um": 25}, '
                                    '{"length_mm": 0.5, "plating_um": 15}, '
                                    '{"length_mm": 0.55, "plating_um": 25}]')
                r_barrel, prof_detail, t_eq_um, msg, code = _barrel_profile_rth(
                    vd, list(vprof), span_mm, kcu)
                if r_barrel is None:
                    return err("thermal via array #%d: %s" % (idx, msg), code)
                g_one = 1.0 / r_barrel
            else:
                g_one = kcu * (a_mm2 * 1e-6) / (span_mm * 1e-3)
            cells, n_x, n_y = _via_centres(nx, ny, dx, dy, vx1, vy1, vx2, vy2,
                                           vpitch)
            on_grid = len(cells)
            asked = n_x * n_y
            if on_grid <= 0:
                return err("thermal via array #%d puts no via centre inside the "
                           "plate: check that (x1_mm, y1_mm) is on the board and "
                           "pitch_mm <= the span" % idx, "ARG_RANGE")
            for (i, j) in cells:
                g_via[i][j] += g_one
                gt_via[i][j] += g_one * sink
            via_arrays.append({
                "index": idx,
                "x1_mm": _sig(vx1), "y1_mm": _sig(vy1),
                "x2_mm": _sig(vx2), "y2_mm": _sig(vy2),
                "pitch_mm": _sig(vpitch), "d_mm": _sig(vd),
                "plating_um": _sig(vpl),
                "drill_mm": _sig(vd),
                "span_mm": _sig(span_mm),
                "sink_c": sink, "sink_c_source": sink_src,
                "vias_per_axis": [n_x, n_y],
                "vias_counted": on_grid,
                "vias_asked": asked,
                "vias_off_grid": asked - on_grid,
                "barrel_area_mm2": _sig(a_mm2),
                "barrel_area_over_drill_area_pct": _sig(
                    100.0 * a_mm2 / (math.pi * r_out ** 2)),
                "conductance_per_via_w_per_k": _sig(g_one),
                "total_conductance_w_per_k": _sig(g_one * on_grid),
            })
            if r_barrel is not None:
                via_arrays[-1]["barrel_profile_modelled"] = True
                via_arrays[-1]["barrel_profile"] = [
                    {"index": seg["index"],
                     "length_mm": _sig(seg["length_mm"]),
                     "plating_um": _sig(seg["plating_um"]),
                     "barrel_area_mm2": _sig(seg["barrel_area_mm2"]),
                     "rth_k_per_w": _sig(seg["rth_k_per_w"]),
                     "share_of_barrel_pct": _sig(
                         100.0 * seg["rth_k_per_w"] / r_barrel)} for seg in prof_detail]
                via_arrays[-1]["plating_equivalent_um"] = _sig(t_eq_um)
                via_arrays[-1]["rth_per_via_k_per_w"] = _sig(r_barrel)

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
    # Radiation and vias both ADD to that diagonal, so the pre-check must know
    # about them or it would refuse boards that are perfectly stable: vias are a
    # constant conductance, and radiation is floored at its value at the ambient
    # (h_rad rises with T, so the ambient value is the smallest it can be).
    h_rad_floor = 0.0
    if emis is not None:
        h_rad_floor = 2.0 * ac * _h_rad_w_m2k(ta, ta, emis)
    diag_worst = None
    for i in range(nx):
        for j in range(ny):
            dv = hc + capdt[i][j] - q_slope[i][j] + h_rad_floor
            if g_via is not None:
                dv += g_via[i][j]
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

    # Three numeric paths, chosen once here and never inside the sweep: the
    # laminate-only loop, the pours-only loop, and the loop that carries an
    # extra per-cell conductance (vias and/or radiation). The first two are the
    # exact code that shipped before vias and radiation existed.
    need_add = (g_via is not None) or (emis is not None)
    if need_add and g_via is None:
        g_via = [[0.0] * ny for _ in range(nx)]
        gt_via = [[0.0] * ny for _ in range(nx)]
    fx_a = fy_a = None
    if need_add:
        if fx is not None:
            fx_a, fy_a = fx, fy
        else:
            fx_a = [[gx] * ny for _ in range(nx - 1)]
            fy_a = [[gy] * (ny - 1) for _ in range(nx)]

    rad_outer_total = [0]

    def _hrad_field(fld):
        return [[_h_rad_w_m2k(fld[i][j], ta, emis) for j in range(ny)]
                for i in range(nx)]

    def _solve(prev_):
        if need_add:
            hf = _hrad_field(prev_) if emis is not None else None
            base = prev_
            last = None
            sw = 0
            out = None
            for _outer in range(80):
                if hf is None:
                    gadd, gt = g_via, gt_via
                else:
                    gadd = [[g_via[i][j] + 2.0 * ac * hf[i][j]
                             for j in range(ny)] for i in range(nx)]
                    gt = [[gt_via[i][j] + 2.0 * ac * hf[i][j] * ta
                           for j in range(ny)] for i in range(nx)]
                cur, it, dmax, conv, diag_min = _sor_plate_add(
                    nx, ny, fx_a, fy_a, gadd, gt, hc, ta, q_const, q_slope,
                    capdt, base, edge, tol_k, sor_max_iter)
                sw += it
                rad_outer_total[0] += 1
                out = (cur, sw, dmax, conv, diag_min)
                if not conv or hf is None:
                    return out
                if last is not None:
                    moved = 0.0
                    for i in range(nx):
                        for j in range(ny):
                            d = abs(cur[i][j] - last[i][j])
                            if d > moved:
                                moved = d
                    if moved < tol_k:
                        return out
                last = cur
                base = cur
                hf = _hrad_field(cur)
            # the radiation linearisation itself did not settle
            return (out[0], sw, float("inf"), False, out[4])
        if pours:
            return _sor_plate_map(nx, ny, fx, fy, hc, ta, q_const, q_slope,
                                  capdt, prev_, edge, tol_k, sor_max_iter)
        return _sor_plate(nx, ny, gx, gy, hc, ta, q_const, q_slope,
                          capdt, prev_, edge, tol_k, sor_max_iter)

    iters_total = 0
    prev = field
    final = None
    for s in range(steps):
        cur_field, it, dmax, conv, diag_min = _solve(prev)
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
    # The slack is the solver's own tolerance, not a fudge: a field that stopped
    # moving at tol_k can sit that far under the ambient in a cell whose only job
    # is to drain heat, and a gate that fires on its own residual rejects correct
    # answers. A genuinely non-physical field is off by degrees, not by 1e-5.
    # The floor is the coldest node the field is pinned to: the ambient, or a
    # sink you named that is colder than the ambient - a via to a 0 C cold plate
    # legitimately drags cells below Ta, and that is not runaway.
    floor_ref = ta
    floor_why = "the ambient %.6g C" % ta
    for a in via_arrays:
        if a["sink_c"] < floor_ref - 1e-9:
            floor_ref = a["sink_c"]
            floor_why = "the coldest sink you gave (%.6g C)" % floor_ref
    if tmin < floor_ref - max(1e-6, 10.0 * tol_k):
        return err(
            "the solved field is not physical: minimum temperature %.6g C is "
            "below %s while every source is a heater" % (tmin, floor_why),
            "THERMAL_RUNAWAY",
            hint="this is what a not-diagonally-dominant electro-thermal matrix "
                 "looks like when it still factorises. Treat it as runaway, not as "
                 "a numerical curiosity")

    # energy balance, computed rather than assumed
    out_conv = 0.0
    out_rad = 0.0
    out_via = 0.0
    for i in range(nx):
        for j in range(ny):
            d = temp[i][j] - ta
            out_conv += hc * d
            if emis is not None:
                out_rad += 2.0 * ac * _h_rad_w_m2k(temp[i][j], ta, emis) * d
            if g_via is not None:
                out_via += g_via[i][j] * temp[i][j] - gt_via[i][j]
    out_edge = 0.0
    if edge is not None:
        for i in range(nx):
            for j in range(ny):
                if i not in (0, nx - 1) and j not in (0, ny - 1):
                    continue
                sv = 0.0
                if i > 0:
                    sv += (fx[i - 1][j] if pours else gx) * (
                        temp[i][j] - temp[i - 1][j])
                if i < nx - 1:
                    sv += (fx[i][j] if pours else gx) * (
                        temp[i][j] - temp[i + 1][j])
                if j > 0:
                    sv += (fy[i][j - 1] if pours else gy) * (
                        temp[i][j] - temp[i][j - 1])
                if j < ny - 1:
                    sv += (fy[i][j] if pours else gy) * (
                        temp[i][j] - temp[i][j + 1])
                out_edge -= sv
    p_in = 0.0
    for i in range(nx):
        for j in range(ny):
            p_in += q_ref[i][j] * (1.0 + alpha * (temp[i][j] - T20))
    residual = p_in - out_conv - out_rad - out_via - out_edge
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

    nc_list = list(_PLATE_NOT_COMPUTED)
    copper_report = None
    if pours:
        k_eff = [[(gp + kcu * t_cu[i][j]) / t_m for j in range(ny)]
                 for i in range(nx)]
        flat = [v for row in k_eff for v in row]
        k_avg = sum(flat) / len(flat)
        ratio_max = max(flat) / k if k > 0 else None
        nc_list[0] = ("the copper is smeared into ONE in-plane sheet conductance "
                      "per cell (area fraction x thickness x layers x k_cu): the "
                      "shape inside a cell, the copper-to-laminate interface "
                      "and any copper you did not list are not resolved" +
                      ("" if via_arrays else
                       ", and thermal vias are not in it"))
        copper_report = {
            "modelled": True,
            "k_copper_w_mk": _sig(kcu),
            "pours": pours,
            "total_copper_thickness_um": _sig(tof_cu * 1e6),
            "k_eff_w_mk": {"area_avg": _sig(k_avg),
                           "min": _sig(min(flat)),
                           "max": _sig(max(flat))},
            "k_eff_over_laminate_max": _sig(ratio_max),
            "sheet_conductance_w_per_k": {
                "laminate_only": _sig(gp),
                "area_avg": _sig(gp + kcu * sum(sum(r) for r in t_cu) /
                                 float(nx * ny))},
            "faces": "harmonic mean of the two cell sheet conductances",
            "assumption": ("in-plane sheet conduction only; the vertical path "
                           "stays inside h, so copper does not change how much "
                           "heat leaves the two surfaces, only how far it "
                           "spreads before it leaves"),
        }
    else:
        copper_report = {
            "modelled": False,
            "why": ("no copper_pours given, so the spreading medium is the "
                    "laminate k alone - a board with a plane under the hot part "
                    "is cooler than this says"),
        }
    if emis is not None:
        nc_list[1] = ("radiation is a linearised grey body with ONE emissivity "
                      "for both faces and the cell temperature as the surface "
                      "temperature: no view factors, no enclosure correction "
                      "(a plate in a box sees itself), no wavelength or "
                      "angle dependence, no solar or other external flux")
    if via_arrays:
        if any(a.get("barrel_profile_modelled") for a in via_arrays):
            nc_list[2] = ("each via is one barrel conducting to the sink you named "
                          "with the wall profile you gave (the segments are in "
                          "series, so a thin middle is not averaged away), but "
                          "there is no pad/plane spreading at either end, no "
                          "copper barrel resistance variation with temperature "
                          "(k_cu is one number), and no interaction with the "
                          "neighbouring barrels")
        else:
            nc_list[2] = ("each via is one barrel with uniform plating conducting "
                          "to the sink you named: no plating thinning mid-barrel, "
                          "no pad/plane spreading at either end, no copper barrel "
                          "resistance variation with temperature (k_cu is one "
                          "number), and no interaction with the neighbouring "
                          "barrels")

    rad_report = None
    if emis is not None:
        h_at_ta = _h_rad_w_m2k(ta, ta, emis)
        h_at_max = _h_rad_w_m2k(tmax, ta, emis)
        rad_report = {
            "modelled": True,
            "emissivity": _sig(emis),
            "stefan_boltzmann_w_m2_k4": SIGMA_SB,
            "h_rad_w_m2k": {"at_t_ambient": _sig(h_at_ta),
                            "at_t_max": _sig(h_at_max)},
            "faces": 2,
            "heat_out_w": _sig(out_rad),
            "share_of_heat_out_pct": _sig(100.0 * out_rad /
                                          (out_conv + out_rad + out_via
                                           + out_edge)) if
            (out_conv + out_rad + out_via + out_edge) else None,
            "linearisation": ("h_rad lagged on the previous iterate; the whole "
                              "field is re-solved until it stops moving, so the "
                              "answer is the fixed point of T^4, not one pass"),
            "outer_iterations": rad_outer_total[0],
            "assumption": ("one emissivity for both faces, and the surface is "
                           "at the cell temperature (no coating on top of a "
                           "copper plane, no view factors to nearby objects - "
                           "in an enclosure a plate really does see itself)"),
        }
    else:
        rad_report = {
            "modelled": False,
            "why": ("no emissivity given, so only convection removes heat from "
                    "the two surfaces - past ~60 C that understates the "
                    "cooling, maybe by a factor of 1.5 in still air"),
        }
    via_report = None
    if g_via is not None:
        g_total = sum(sum(r) for r in g_via)
        via_report = {
            "modelled": True,
            "k_copper_w_mk": _sig(kcu),
            "arrays": via_arrays,
            "vias_counted": sum(a["vias_counted"] for a in via_arrays),
            "total_conductance_w_per_k": _sig(g_total),
            "heat_out_w": _sig(out_via),
            "equivalent_h_w_m2k": _sig(g_total / (2.0 * ac * nx * ny)),
            "assumption": ("each via is ONE copper barrel from its cell to the "
                           "sink you named (G = k_cu*annulus/span, or "
                           "1/sum(L_i/(k_cu*A_i)) when a barrel_profile is "
                           "given), radial isothermal, "
                           + ("no pad " if any(a.get("barrel_profile_modelled")
                                                for a in via_arrays)
                              else "no plating thinning mid-barrel, no pad ")
                           + "or plane spreading at either end; the laminate's own "
                           "vertical path is already inside h and is NOT added in "
                           "parallel, and a via lands in the single cell that "
                           "contains its centre"),
        }
    else:
        via_report = {
            "modelled": False,
            "why": ("no thermal_vias given, so the only path between the two "
                    "surfaces is h - a via field under a hot part is far cooler "
                    "than this says"),
        }

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
                           "power_out_radiation_w": _sig(out_rad),
                           "power_out_vias_w": _sig(out_via),
                           "power_out_fixed_edges_w": _sig(out_edge),
                           "residual_pct": _sig(resid_pct)},
        "effective_rth_k_per_w": _sig((tmax - ta) / p_in) if p_in else None,
        "map": rows,
        "map_legend": "row 0 is y=0; '@' is the hottest cell",
        "copper_spreading": copper_report,
        "radiation": rad_report,
        "thermal_vias": via_report,
        "not_computed": nc_list,
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
              if tr is not None else "") +
             (" + radiation (eps = %.3g, h_rad from T^4)" % emis
              if emis is not None else "") +
             (" + thermal vias (%d arrays)" % len(via_arrays)
              if via_arrays else ""))
    prec = ("as good as k and h, and the hot spot is grid dependent: compare "
            "two layouts only at the same cell_mm. The spreading medium is "
            "your k plus the pours you listed, smeared per cell; a plane you "
            "did not list is still missing"
            if pours else
            "as good as k and h, and the hot spot is grid dependent: compare "
            "two layouts only at the same cell_mm. The spreading medium here "
            "is the laminate k you passed, not the real copper pours, so a "
            "design with a plane under the trace is cooler than this says")
    if emis is not None:
        prec += ("; radiation is one grey body per face at the cell temperature, "
                 "so the answer is the fixed point of a T^4 loop, not one pass")
    if via_arrays:
        prec += ("; each via is a "
                 + ("copper barrel with the wall profile you gave"
                    if any(a.get("barrel_profile_modelled") for a in via_arrays)
                    else "uniform-plating copper barrel")
                 + " between the cell and the sink you named, and that sink "
                   "temperature is an input - it is not solved for")
    return ok({"t_max_c": round(tmax, 6),
               "t_min_c": round(tmin, 6),
               "t_avg_c": round(tavg, 6),
               "delta_t_max_k": round(tmax - ta, 6)},
              "C", model,
              _BASIS_TRACE, prec,
              {"rth_source": "emerging from the 2-D field, not supplied",
               "copper_layers": "parallel copper assumed stitched at both ends",
               "heat_capacity": ("plate volume only (%s)"
                                 % ("rho*cp*dx*dy*t" if tr is not None
                                    else "not modelled"))},
              extra)
