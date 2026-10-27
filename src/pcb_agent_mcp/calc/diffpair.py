"""Differential-pair impedance (edge-coupled) and its reverse solve.

Why this is a separate module from `impedance.py`: the single-ended closed forms
cannot answer "90 ohm differential" at all. A model is needed that contains the
coupling term, otherwise the answer is off by 10..20% - exactly the error that
makes a USB 2.0 link fail intermittently and pass every DRC.

The models used here are the widely published edge-coupled approximations built
on the single-ended IPC-2141A forms:

    microstrip (edge coupled, same layer, single ground reference)
        Zdiff = 2 * Z0 * (1 - 0.48 * exp(-0.96 * s / h))
    stripline (edge coupled, centred between two references)
        Zdiff = 2 * Z0 * (1 - 0.347 * exp(-2.9 * s / b))     b = 2h

These are approximations, not field solutions. They ignore solder mask, etch
trapezoid, finite copper thickness effects on coupling, and any non-uniform
dielectric. `precision` says so, and the caller is expected to check the result
against a field solver or the fabricator's impedance report before release.
"""
import math

from .result import ok, err

VALID_KINDS = ("microstrip", "stripline")

BASIS = {
    "microstrip": ("Edge-coupled microstrip approximation on the IPC-2141A single-ended form: "
                   "Zdiff = 2*Z0*(1 - 0.48*exp(-0.96*s/h))"),
    "stripline": ("Edge-coupled stripline approximation on the IPC-2141A single-ended form: "
                  "Zdiff = 2*Z0*(1 - 0.347*exp(-2.9*s/b)), b = 2h"),
}

# Validity window for the coupling term. Outside s/h < 0.1 the pair is not
# really coupled; above s/h ~ 3 the exponential has decayed to noise and the
# answer collapses to 2*Z0 (i.e. the model stops being informative).
S_OVER_H_MIN = 0.1
S_OVER_H_MAX = 3.0

PRECISION = ("+/-15% (edge-coupled closed form, no field solver, no solder mask, "
             "no etch trapezoid, no copper-thickness coupling correction)")


def _single_ended(kind, w, h, t, er):
    if kind == "microstrip":
        arg = 5.98 * h / (0.8 * w + t)
        if arg <= 1.0:
            return None
        return 87.0 / math.sqrt(er + 1.41) * math.log(arg)
    b = 2.0 * h
    arg = 4.0 * b / (0.67 * math.pi * (0.8 * w + t))
    if arg <= 1.0:
        return None
    return 60.0 / math.sqrt(er) * math.log(arg)


def _coupling_factor(kind, s, h):
    if kind == "microstrip":
        return 1.0 - 0.48 * math.exp(-0.96 * s / h)
    return 1.0 - 0.347 * math.exp(-2.9 * s / (2.0 * h))


def _check(kind, h, t_um, er, s):
    kind = str(kind or "").strip().lower()
    if kind not in VALID_KINDS:
        return None, err("kind must be microstrip or stripline", "ARG_INVALID")
    try:
        h = float(h)
        t_um = float(t_um)
        er = float(er)
        s = float(s)
    except (TypeError, ValueError):
        return None, err("numeric arguments required", "ARG_INVALID")
    if h <= 0 or t_um <= 0 or er <= 0:
        return None, err("dielectric_mm, copper_um and er must be > 0", "ARG_RANGE")
    if s <= 0:
        return None, err("spacing_mm must be > 0 (edge-to-edge gap between the pair)",
                         "ARG_RANGE")
    if not (1.0 <= er <= 12.0):
        return None, err("er outside 1..12 (formula validity)", "OUT_OF_VALIDITY")
    return (kind, h, t_um / 1000.0, er, s), None


def diff_impedance(width_mm, spacing_mm, dielectric_mm, copper_um=35.0, er=4.3,
                   kind="microstrip"):
    """Differential impedance from geometry.

    width_mm   : single trace width (not the pair pitch)
    spacing_mm : edge-to-edge gap between the two traces
    dielectric_mm : height to the reference plane (stripline: to ONE plane)
    """
    ctx, e = _check(kind, dielectric_mm, copper_um, er, spacing_mm)
    if e:
        return e
    kind, h, t, er, s = ctx
    try:
        w = float(width_mm)
    except (TypeError, ValueError):
        return err("width_mm numeric required", "ARG_INVALID")
    if w <= 0:
        return err("width_mm must be > 0", "ARG_RANGE")

    z0 = _single_ended(kind, w, h, t, er)
    if z0 is None:
        return err("width too wide for this stackup: the single-ended form would return a "
                   "non-positive Z0", "OUT_OF_VALIDITY",
                   hint="keep 0.8*w+t below 5.98*h (microstrip) / 4*b/(0.67*pi) (stripline)")
    k = _coupling_factor(kind, s, h)
    z_diff = 2.0 * z0 * k
    s_over_h = s / h

    extra = {
        "z_single_ended_ohm": round(z0, 2),
        "coupling_factor": round(k, 5),
        "impedance_drop_pct": round((1.0 - k) * 100.0, 2),
        "spacing_over_height": round(s_over_h, 3),
        "validity_window_s_over_h": [S_OVER_H_MIN, S_OVER_H_MAX],
    }
    if not (S_OVER_H_MIN <= s_over_h <= S_OVER_H_MAX):
        extra["validity_note"] = (
            "s/h %.3f is outside %.1f..%.1f: the coupling term is not trustworthy there "
            "(uncoupled or saturated)" % (s_over_h, S_OVER_H_MIN, S_OVER_H_MAX))

    return ok(
        value=round(z_diff, 2),
        unit="ohm",
        model=kind + "_edge_coupled_closed_form",
        basis=BASIS[kind],
        precision=PRECISION,
        assumptions={
            "width_mm": w,
            "spacing_mm": s,
            "dielectric_mm": h,
            "copper_um": float(copper_um),
            "er": er,
            "kind": kind,
            "solder_mask_ignored": True,
            "both_traces_same_layer": True,
            "reference_plane": "single" if kind == "microstrip" else "dual (stripline)",
        },
        extra=extra,
    )


def width_for_diff_impedance(target_ohm, spacing_mm, dielectric_mm, copper_um=35.0,
                             er=4.3, kind="microstrip"):
    """Reverse solve: which single-trace width gives `target_ohm` differential.

    Bisection on width with the spacing held fixed - that is the knob a designer
    actually turns after the stackup and the pair gap are fixed by the connector
    and the fabricator's minimum gap.
    """
    ctx, e = _check(kind, dielectric_mm, copper_um, er, spacing_mm)
    if e:
        return e
    kind, h, t, er, s = ctx
    try:
        target = float(target_ohm)
    except (TypeError, ValueError):
        return err("target_ohm numeric required", "ARG_INVALID")
    if not (10.0 <= target <= 400.0):
        return err("target_ohm outside 10..400 (differential range this model can express)",
                   "OUT_OF_VALIDITY")

    def f(w):
        z0 = _single_ended(kind, w, h, t, er)
        if z0 is None:
            return None
        return 2.0 * z0 * _coupling_factor(kind, s, h)

    lo, hi = 0.005 * h, 3.0 * h   # same w/h 0.05..3.0 window as the single-ended model
    z_narrow, z_wide = f(lo), f(hi)
    if z_narrow is None or z_wide is None:
        return err("stackup outside the model's validity", "OUT_OF_VALIDITY")
    if not (z_wide <= target <= z_narrow):
        return err(
            "target differential impedance unreachable with this stackup and spacing",
            "UNREACHABLE",
            hint="reachable Zdiff range at s=%.4f mm is %.1f..%.1f ohm; change s, h or er"
                 % (s, z_wide, z_narrow),
            extra={"reachable_min_ohm": round(z_wide, 1),
                   "reachable_max_ohm": round(z_narrow, 1)},
        )
    a, b = lo, hi
    for _ in range(200):
        mid = 0.5 * (a + b)
        z = f(mid)
        if z > target:
            a = mid
        else:
            b = mid
        if b - a < 1e-7:
            break
    w = 0.5 * (a + b)
    return ok(
        value=round(w, 5),
        unit="mm",
        model=kind + "_edge_coupled_closed_form_inverse",
        basis=BASIS[kind],
        precision="+/-15% (inherits the coupling model error; verify with the fab's "
                  "impedance coupon or a field solver)",
        assumptions={
            "target_ohm": target,
            "spacing_mm": s,
            "dielectric_mm": h,
            "copper_um": float(copper_um),
            "er": er,
            "kind": kind,
            "solved_by": "bisection on width, spacing held fixed, tol 1e-7 mm",
            "solder_mask_ignored": True,
        },
        extra={"zdiff_recomputed": round(f(w), 2),
               "spacing_over_height": round(s / h, 3)},
    )
