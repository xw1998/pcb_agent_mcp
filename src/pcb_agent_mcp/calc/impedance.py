"""Trace impedance: forward solve (geometry -> Z0) and reverse solve (Z0 -> geometry).

The reverse direction is what a designer actually needs: "I need 90 ohm, how wide?"
Models are closed-form approximations (IPC-2141A family). They are NOT field
solvers: `calc/field2d.py` now provides one, and the difference between the two
is measured in its VALIDATION table instead of being asserted here. When a board
has to actually hit an impedance, run the field solver and say which answer the
design used.
"""
import math

from .result import ok, err

VALID_KINDS = ("microstrip", "stripline")

MICROSTRIP_BASIS = "IPC-2141A microstrip approximation: Z0 = 87/sqrt(er+1.41) * ln(5.98*h/(0.8*w+t))"
STRIPLINE_BASIS = "IPC-2141A stripline approximation: Z0 = 60/sqrt(er) * ln(4*b/(0.67*pi*(0.8*w+t)))"


def _z0_microstrip(w, h, t, er):
    arg = 5.98 * h / (0.8 * w + t)
    return 87.0 / math.sqrt(er + 1.41) * math.log(arg)


def _z0_stripline(w, h, t, er):
    # h is the dielectric thickness between the trace and ONE reference plane.
    # total separation b = 2h for a centred stripline.
    b = 2.0 * h
    arg = 4.0 * b / (0.67 * math.pi * (0.8 * w + t))
    return 60.0 / math.sqrt(er) * math.log(arg)


_FORWARD = {"microstrip": _z0_microstrip, "stripline": _z0_stripline}
_BASIS = {"microstrip": MICROSTRIP_BASIS, "stripline": STRIPLINE_BASIS}

# Practical validity window of the closed-form fits. Outside it the log argument
# can go below 1 and the formula returns a negative (meaningless) impedance.
W_OVER_H_MIN = 0.05
W_OVER_H_MAX = 3.0

def _max_width_mm(kind, h, t):
    """Widest trace for which the closed form still yields a positive Z0."""
    if kind == "microstrip":
        arg_h = 5.98 * h
        k = 0.67 * math.pi  # not used for microstrip
        return max(0.0, (arg_h - t) / 0.8)
    b = 2.0 * h
    return max(0.0, (4.0 * b / (0.67 * math.pi) - t) / 0.8)

def _width_window(kind, h, t):
    lo = W_OVER_H_MIN * h
    hi = min(W_OVER_H_MAX * h, 0.99 * _max_width_mm(kind, h, t))
    return lo, hi


def _check(kind, dielectric_mm, copper_um, er):
    kind = str(kind or "").strip().lower()
    if kind not in VALID_KINDS:
        return None, err("kind must be microstrip or stripline", "ARG_INVALID")
    try:
        dielectric_mm = float(dielectric_mm)
        copper_um = float(copper_um)
        er = float(er)
    except (TypeError, ValueError):
        return None, err("numeric arguments required", "ARG_INVALID")
    if dielectric_mm <= 0 or copper_um <= 0 or er <= 0:
        return None, err("dielectric_mm, copper_um and er must be > 0", "ARG_RANGE")
    if not (1.0 <= er <= 12.0):
        return None, err("er outside 1..12 (formula validity)", "OUT_OF_VALIDITY")
    return (kind, dielectric_mm, copper_um, er), None


def trace_impedance(width_mm, dielectric_mm, copper_um=35.0, er=4.3, kind="microstrip"):
    ctx, e = _check(kind, dielectric_mm, copper_um, er)
    if e:
        return e
    kind, h, t_um, er = ctx
    try:
        w = float(width_mm)
    except (TypeError, ValueError):
        return err("width_mm numeric required", "ARG_INVALID")
    if w <= 0:
        return err("width_mm must be > 0", "ARG_RANGE")

    t = t_um / 1000.0
    lo_w, hi_w = _width_window(kind, h, t)
    if w > _max_width_mm(kind, h, t):
        return err(
            "width too wide for this stackup: the closed form would return a non-positive Z0",
            "OUT_OF_VALIDITY",
            hint="keep width under %.4f mm (w/h <= %.1f) for h=%.4f mm; use a field solver beyond this"
                 % (_max_width_mm(kind, h, t), W_OVER_H_MAX, h),
        )
    if w < lo_w or w > hi_w:
        validity_note = "outside the w/h %.2f..%.1f validity window; treat as rough" % (W_OVER_H_MIN, W_OVER_H_MAX)
    else:
        validity_note = None

    z0 = _FORWARD[kind](w, h, t, er)
    return ok(
        value=round(z0, 2),
        unit="ohm",
        model=kind + "_closed_form",
        basis=_BASIS[kind],
        precision="+/-12% (closed-form approximation, no field solver, no solder mask, no etch trapezoid)",
        assumptions={
            "width_mm": w,
            "dielectric_mm": h,
            "copper_um": t_um,
            "er": er,
            "kind": kind,
            "solder_mask_ignored": True,
            "reference_plane": "single" if kind == "microstrip" else "dual (stripline)",
            "validity_window": "w/h %.2f..%.1f" % (W_OVER_H_MIN, W_OVER_H_MAX),
        },
        extra={"validity_note": validity_note} if validity_note else None,
    )


def width_for_impedance(target_ohm, dielectric_mm, copper_um=35.0, er=4.3, kind="microstrip"):
    """Reverse solve by bisection. This is the direction designers need."""
    ctx, e = _check(kind, dielectric_mm, copper_um, er)
    if e:
        return e
    kind, h, t_um, er = ctx
    try:
        target = float(target_ohm)
    except (TypeError, ValueError):
        return err("target_ohm numeric required", "ARG_INVALID")
    if not (10.0 <= target <= 200.0):
        return err("target_ohm outside 10..200", "OUT_OF_VALIDITY")

    f = _FORWARD[kind]
    t = t_um / 1000.0
    win_lo, win_hi = _width_window(kind, h, t)
    lo, hi = win_lo, win_hi
    z_wide, z_narrow = f(hi, h, t, er), f(lo, h, t, er)
    # Z0 decreases with width, so z_narrow is the maximum this stackup can offer.
    if not (z_wide <= target <= z_narrow):
        return err(
            "target impedance unreachable with this stackup",
            "UNREACHABLE",
            hint="reachable Z0 range for this stackup is %.1f..%.1f ohm "
                 "(microstrip/stripline, w/h %.2f..%.1f, h=%.4f mm, er=%.2f); "
                 "widen the dielectric, change er, or use a different trace kind"
                 % (z_wide, z_narrow, W_OVER_H_MIN, W_OVER_H_MAX, h, er),
            extra={"reachable_min_ohm": round(z_wide, 1), "reachable_max_ohm": round(z_narrow, 1)},
        )
    for _ in range(200):
        mid = 0.5 * (lo + hi)
        z = f(mid, h, t, er)
        if z > target:
            lo = mid
        else:
            hi = mid
        if hi - lo < 1e-7:
            break
    w = 0.5 * (lo + hi)
    z_check = f(w, h, t, er)

    return ok(
        value=round(w, 5),
        unit="mm",
        model=kind + "_closed_form_inverse",
        basis=_BASIS[kind],
        precision="+/-12% (inherits forward model error; verify against a field solver or the fab's impedance report)",
        assumptions={
            "target_ohm": target,
            "dielectric_mm": h,
            "copper_um": t_um,
            "er": er,
            "kind": kind,
            "solved_by": "bisection, tol 1e-7 mm",
            "search_window_mm": [round(win_lo, 4), round(win_hi, 4)],
            "solder_mask_ignored": True,
        },
        extra={"z0_recomputed": round(z_check, 2)},
    )
