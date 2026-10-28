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
