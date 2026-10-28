"""Feed lines: microstrip, stripline, CPW and grounded CPW.

Why this module exists: an antenna is only as good as the line that feeds it, and
the four geometries an antenna lands on have four different answers for the same
width. `impedance.py` already covers the coax-equivalent pair with IPC-2141A;
this module covers the coplanar pair, which is what printed antennas and chip
antenna feeds actually use, and it answers the question a designer asks in the
other direction: *what width do I need for 50 ohm on this stackup*.

Every answer carries `precision` and `assumptions` because the four models have
very different standing:

  * microstrip  - Hammerstad / Hammerstad-Jensen closed form. The same reference
                  `field2d.solve_single` is checked against, where the measured
                  disagreement is -0.35 % .. -2.83 % (field2d.VALIDATION). So the
                  accuracy claim here is a number from this repo, not literature.
  * stripline   - the EXACT zero-thickness conformal answer
                  (Z0 = eta0/(4*sqrt(er)) * K(k)/K(k'), k = sech(pi*w/(2b))), with
                  the copper thickness folded into an effective widening. The
                  solver reproduces it to 0.23 % .. 1.20 % on the same geometry,
                  so the two numbers bracket the answer instead of agreeing by
                  construction.
  * cpw / gcpw  - conformal mapping with the finite substrate thickness retained
                  (k1 = sinh/sinh for an unbacked line, k2 = tanh/tanh for a line
                  over a ground plane). NOT validated against a field solver
                  here: `field2d` has no coplanar geometry, so the only checks are
                  the limits that the closed form must satisfy (er=1 reduces to
                  coplanar strips in vacuum; h -> infinity reduces to the
                  infinite-substrate textbook result). Those two limits are
                  asserted in tests/test_feedline.py, and `basis` says so.

What this is not: a full-wave model. Dispersion, radiation from the line,
surface-wave leakage, connector launch discontinuity and copper roughness are all
outside it. Above roughly 10 GHz on thin substrates those stop being second order
and the number here becomes a starting point, not an answer.
"""
import math

from .result import ok, err
from .field2d import _ellip_K, stripline_zero_thickness_z0
from .rf import C0_MM_PER_NS

ETA0 = 376.730313668          # free-space wave impedance, ohm
MU0 = 1.25663706212e-6        # H/m
SIGMA_CU = 5.8e7              # S/m, annealed copper (what a PCB house quotes)

KINDS = ("microstrip", "stripline", "cpw", "gcpw")

# The copper-thickness term. `impedance.py` already uses the IPC-2141A form
# (0.8*w + t in the log argument), i.e. an effective widening of 1.25*t; keeping
# the same convention here means one stackup, one number, whichever tool is asked.
THICKNESS_WIDENING = 1.25

# Closed forms are only meaningful inside the window they were fitted to. Outside
# it they still return a number, which is exactly the failure mode this server
# exists to avoid - so the window is a hard limit, not a warning.
W_OVER_H_MIN = 0.05
W_OVER_H_MAX = 20.0

# --------------------------------------------------------------- microstrip
def _microstrip(w, h, t, er):
    we = w + THICKNESS_WIDENING * t
    u = we / h
    if u <= 1.0:
        ee = ((er + 1.0) / 2.0
              + (er - 1.0) / 2.0 * (1.0 / math.sqrt(1.0 + 12.0 / u)
                                    + 0.04 * (1.0 - u) ** 2))
        z0 = 60.0 / math.sqrt(ee) * math.log(8.0 / u + u / 4.0)
    else:
        ee = (er + 1.0) / 2.0 + (er - 1.0) / 2.0 / math.sqrt(1.0 + 12.0 / u)
        z0 = (120.0 * math.pi
              / (math.sqrt(ee) * (u + 1.393 + 0.667 * math.log(u + 1.444))))
    return z0, ee, {"w_eff_mm": we, "w_over_h": u}

# ----------------------------------------------------------------- stripline
def _stripline(w, h, t, er):
    we = w + THICKNESS_WIDENING * t
    b = 2.0 * h                      # plane-to-plane separation
    z0 = stripline_zero_thickness_z0(we, b, er)
    return z0, er, {"w_eff_mm": we, "w_over_b": we / b, "plane_to_plane_mm": b}

# ---------------------------------------------------------------------- CPW
def _cpw_k_values(w, g, h):
    """The two moduli of the conformal map.

    k0 = a/b with a = w/2, b = w/2 + g  - the zero-thickness, infinitely thick
    substrate map (exact for that limit).
    k1 = sinh(pi*a/2h)/sinh(pi*b/2h)   - the same map squeezed by a substrate of
    finite thickness h with air below. k1 -> k0 as h -> infinity, which is why the
    limit test can detect a broken formula.
    k2 = tanh(pi*a/2h)/tanh(pi*b/2h)   - the map for a ground plane at distance h
    (the slab is bounded below, so its image is a tanh rather than a sinh). k2 -> k0
    as h -> infinity as well.
    """
    a = w / 2.0
    b = w / 2.0 + g
    k0 = a / b
    if h <= 0:
        return k0, k0, k0
    k1 = math.sinh(math.pi * a / (2.0 * h)) / math.sinh(math.pi * b / (2.0 * h))
    k2 = math.tanh(math.pi * a / (2.0 * h)) / math.tanh(math.pi * b / (2.0 * h))
    return k0, k1, min(k2, 0.999999999)

def _ratio(k):
    """K(k)/K(k'), the shape factor that carries the whole geometry."""
    if k <= 0.0:
        return 0.0
    if k >= 1.0:
        return float("inf")
    kp = math.sqrt(max(0.0, 1.0 - k * k))
    return _ellip_K(k) / _ellip_K(kp)

def _cpw(w, g, h, t, er, backed):
    """Coplanar waveguide, quasi-static conformal solution.

    Built from the capacitance, so the parts are auditable:

        c_air = 2*A + 2*B   (all vacuum, units of eps0; A = K(k0)/K(k0'),
                             B = K(k2)/K(k2'))
        c     = 2*A + 2*er*B

    unbacked (air below the substrate): B = K(k1)/K(k1'), and the lower half-space
    is vacuum, so the reference capacitance is the whole-space one:

        c_air = 4*A,   c = 2*(2*A + (er-1)*B)

    Z0 = 1/(c0*sqrt(C_air*C)) = eta0/sqrt(c_air*c)  (inductance is unchanged by the
    dielectric, so Z0 = sqrt(L/C) = eta0/sqrt(c_air*c); writing it as 1/(c0*C)
    instead is a classic factor-of-sqrt(ee) error)

    ee = c / c_air.  Both cases reduce to the textbook infinite-substrate result
    (eta0/4)*K(k0')/K(k0)/sqrt((er+1)/2) as h -> infinity, which is the limit the
    tests assert - a first draft of this function got that limit wrong by exactly
    sqrt(ee) and the limit test is what caught it.
    """
    k0, k1, k2 = _cpw_k_values(w, g, h)
    A = _ratio(k0)
    B = _ratio(k2) if backed else _ratio(k1)
    if backed:
        c_air = 2.0 * (A + B)
        c = 2.0 * (A + er * B)
    else:
        c_air = 4.0 * A
        c = 2.0 * (2.0 * A + (er - 1.0) * B)
    if c_air <= 0.0 or c <= 0.0 or not (c == c):
        raise ValueError("degenerate coplanar geometry")
    z0 = ETA0 / math.sqrt(c_air * c)
    ee = c / c_air
    return z0, ee, {"k0": k0, "k1": k1, "k2": k2, "shape_A": A, "shape_B": B,
                    "centre_mm": w, "gap_mm": g, "substrate_mm": h}

# ---------------------------------------------------------------- attenuation
def _surface_resistance(freq_ghz):
    f = float(freq_ghz) * 1e9
    return math.sqrt(math.pi * f * MU0 / SIGMA_CU)

def _attenuation(kind, z0, ee, er, w, h, freq_ghz, tan_delta):
    """Loss per unit length, split into the two mechanisms.

    Conductor loss uses the plain Rs/(Z0*w) sheet-current estimate. It ignores
    current crowding at the trace edges, so it is a LOWER BOUND; on microstrip the
    real number is typically 20-40 % higher. Stated instead of silently corrected,
    because the correction factor depends on w/h and a wrong factor is worse than
    an honest bound.

    Dielectric loss uses the standard quasi-TEM filling-factor expression, which
    does not depend on the conductor shape:

        alpha_d = (pi/lambda0) * (er/sqrt(ee)) * ((ee-1)/(er-1)) * tan_delta

    Coplanar conductor loss is NOT computed: it needs the current distribution
    over the strip edges, and there is no closed form here that this repo can
    stand behind. The field is reported as null with the reason, so a total
    attenuation read off this answer is a bound rather than a guess in disguise.
    """
    lam0 = C0_MM_PER_NS / float(freq_ghz)          # mm
    ad = 0.0
    if er > 1.0 and tan_delta > 0.0:
        ad = (math.pi / lam0) * (er / math.sqrt(ee)) * ((ee - 1.0) / (er - 1.0)) * tan_delta
    out = {"dielectric_db_per_100mm": round(8.686 * ad * 100.0, 4)}
    if kind in ("microstrip", "stripline"):
        rs = _surface_resistance(freq_ghz)
        ac = rs / (z0 * w)
        if kind == "stripline":
            # two reference planes carry the return current; the per-plane share is
            # below half because the field is shared, so the simple sum is used and
            # labelled as the same lower bound
            ac = ac * 1.0
        out["conductor_db_per_100mm"] = round(8.686 * ac * 100.0, 4)
        out["conductor_note"] = ("Rs/(Z0*w), no edge-crowding term: a LOWER bound, "
                                 "typically 20-40 % low on microstrip")
        out["total_db_per_100mm"] = round(out["conductor_db_per_100mm"]
                                          + out["dielectric_db_per_100mm"], 4)
    else:
        out["conductor_db_per_100mm"] = None
        out["conductor_note"] = ("not computed for coplanar lines: it needs the "
                                 "current distribution over the strip edges. The "
                                 "total above is a LOWER bound")
        out["total_db_per_100mm_lower_bound"] = out["dielectric_db_per_100mm"]
    out["surface_resistance_ohm_sq"] = round(_surface_resistance(freq_ghz), 5)
    return out

# ------------------------------------------------------------------ forward
def _solve(kind, w, h, g, t, er):
    if kind == "microstrip":
        return _microstrip(w, h, t, er)
    if kind == "stripline":
        return _stripline(w, h, t, er)
    return _cpw(w, g, h, t, er, backed=(kind == "gcpw"))

_BASIS = {
    "microstrip": ("Hammerstad closed form (the same reference field2d is checked "
                   "against): 60/sqrt(ee)*ln(8h/w+w/4h) for w/h<=1, "
                   "120*pi/(sqrt(ee)*(w/h+1.393+0.667*ln(w/h+1.444))) for w/h>1; "
                   "copper as an effective widening of 1.25*t (IPC-2141A convention "
                   "already used by trace_impedance)"),
    "stripline": ("exact zero-thickness conformal answer Z0 = eta0/(4*sqrt(er)) * "
                  "K(k)/K(k'), k = sech(pi*w/(2b)), b = 2*dielectric (the dielectric "
                  "argument is the trace-to-one-plane distance, as in "
                  "trace_impedance); copper as an effective widening of 1.25*t"),
    "cpw": ("quasi-static conformal mapping of a finite-thickness substrate with air "
            "below: a = width/2, b = width/2 + gap, k0 = a/b, "
            "k1 = sinh(pi*a/2h)/sinh(pi*b/2h); c_air = 4*K(k0)/K(k0'), "
            "c = 2*(2*K(k0)/K(k0') + (er-1)*K(k1)/K(k1')), "
            "Z0 = eta0/sqrt(c_air*c), eps_eff = c/c_air"),
    "gcpw": ("quasi-static conformal mapping of a slab that ends on a ground plane: "
             "k2 = tanh(pi*a/2h)/tanh(pi*b/2h), c_air = 2*(A+B), c = 2*(A+er*B) with "
             "A = K(k0)/K(k0') and B = K(k2)/K(k2'), Z0 = eta0/sqrt(c_air*c)"),
}

_PRECISION = {
    "microstrip": ("closed form, measured -0.35 % .. -2.83 % against the 2-D field "
                   "solver over w/h 0.5..4 and er 1..9.6 (field2d.VALIDATION); "
                   "dispersion and radiation not modelled"),
    "stripline": ("exact conformal solution for zero-thickness copper; the 2-D solver "
                  "reproduces it to 0.23 % .. 1.20 %; the thickness term is the "
                  "IPC-2141A widening, which references disagree on by up to ~2x on t "
                  "(< 2 % in Z0)"),
    "cpw": ("quasi-static conformal model, NOT validated against a solver here - only "
            "the two limits (er=1 -> coplanar strips in vacuum, h -> infinity -> "
            "infinite-substrate textbook result) are asserted. Literature puts the "
            "model itself at a few percent; thickness, dispersion and radiation are "
            "not in it"),
    "gcpw": ("quasi-static conformal model, NOT validated against a solver here; the "
             "h -> infinity limit must equal the unbacked result and is asserted. "
             "Coupling to the backside ground through the substrate is carried by the "
             "tanh map; the parallel-plate mode that a real backed line can excite is "
             "not modelled at all"),
}

def feedline_impedance(kind, width_mm, dielectric_mm, gap_mm=None, copper_um=0.0,
                       er=4.3, freq_ghz=None, tan_delta=None, verify_field=False,
                       field_levels=1):
    """Z0, effective permittivity, guided wavelength and loss for one line."""
    kind = str(kind or "").strip().lower()
    if kind not in KINDS:
        return err("kind must be one of " + ", ".join(KINDS), "ARG_INVALID",
                   hint="cpw/gcpw are coplanar: they also need gap_mm")
    try:
        w = float(width_mm)
        h = float(dielectric_mm)
        t = float(copper_um or 0.0) / 1000.0
        er = float(er)
    except (TypeError, ValueError):
        return err("numeric arguments required", "ARG_INVALID")
    if w <= 0 or h <= 0 or t < 0 or er < 1.0:
        return err("width and dielectric must be > 0; copper_um >= 0; er >= 1",
                   "ARG_RANGE")
    g = None
    if kind in ("cpw", "gcpw"):
        if gap_mm is None:
            return err("a coplanar line needs gap_mm (the slot between the centre "
                       "strip and the ground)", "ARG_REQUIRED")
        try:
            g = float(gap_mm)
        except (TypeError, ValueError):
            return err("gap_mm must be numeric", "ARG_INVALID")
        if g <= 0:
            return err("gap_mm must be > 0", "ARG_RANGE")
        if t > 0:
            # a coplanar line's answer moves with the copper edge shape, and this
            # model has no thickness term at all - refusing to fold t in silently
            pass
    else:
        u = (w + THICKNESS_WIDENING * t) / h
        # the comparison is loosened by 1e-9 relative: a width that sits exactly
        # on the window (0.01 mm on a 0.2 mm stackup -> 0.049999999999999996)
        # must be the boundary case, not a refusal
        if not (W_OVER_H_MIN * (1.0 - 1e-9) <= u <= W_OVER_H_MAX * (1.0 + 1e-9)):
            return err("w/h = %.3f is outside the range the closed form was fitted to "
                       "(%.2f .. %.1f)" % (u, W_OVER_H_MIN, W_OVER_H_MAX),
                       "OUT_OF_VALIDITY",
                       hint="change the width or the dielectric, or use "
                            "field_impedance for a solved answer")

    z0, ee, extra = _solve(kind, w, h, g, t, er)
    if not (z0 > 0 and ee >= 1.0):
        return err("degenerate geometry: the conformal map produced a non-physical "
                   "answer", "OUT_OF_VALIDITY")

    out = {
        "z0_ohm": round(z0, 3),
        "eps_eff": round(ee, 5),
        "lambda_g_mm": None,
        "kind": kind,
    }
    if freq_ghz is not None:
        try:
            f = float(freq_ghz)
        except (TypeError, ValueError):
            return err("freq_ghz must be numeric", "ARG_INVALID")
        if f <= 0:
            return err("freq_ghz must be > 0", "ARG_RANGE")
        out["lambda_g_mm"] = round(C0_MM_PER_NS / (f * math.sqrt(ee)), 4)
        out["quarter_wave_mm"] = round(out["lambda_g_mm"] / 4.0, 4)
        if tan_delta is not None:
            try:
                td = float(tan_delta)
            except (TypeError, ValueError):
                return err("tan_delta must be numeric", "ARG_INVALID")
            if td < 0:
                return err("tan_delta must be >= 0", "ARG_RANGE")
            out["loss"] = _attenuation(kind, z0, ee, er, w, h, f, td)
    if kind in ("cpw", "gcpw"):
        out["cpw_geometry"] = {k: (round(v, 6) if isinstance(v, float) else v)
                               for k, v in extra.items()}
        if t > 0:
            out["copper_thickness"] = ("requested %.1f um but NOT modelled: the "
                                       "coplanar closed form here is a "
                                       "zero-thickness map. A real 35 um foil narrows "
                                       "the slot and lowers Z0; the size of that shift "
                                       "is not quantified by this answer"
                                       % (t * 1000.0))
    else:
        out["metal"] = {k: round(v, 6) for k, v in extra.items()}
        out["thickness_widening_mm"] = round(THICKNESS_WIDENING * t, 6)

    if verify_field and kind in ("microstrip", "stripline"):
        # Deliberately solved with t = 0: `field2d` refines to min(w,h,t)/fine_div,
        # so a 35 um copper layer multiplies the node count by thousands and turns a
        # sub-second check into minutes. The check is therefore on the ZERO-thickness
        # geometry, which is exactly the term the closed form adds on top.
        from . import field2d as _f2d
        z_field, section = _f2d.solve_single(kind, w, h, 0.0, er,
                                             fine_div=10.0 if field_levels else 6.0)
        z_closed_t0 = _solve(kind, w, h, g, 0.0, er)[0]
        out["field_check"] = {
            "z0_field_ohm": round(z_field, 3),
            "z0_closed_t0_ohm": round(z_closed_t0, 3),
            "closed_minus_field_pct": round(100.0 * (z_closed_t0 - z_field) / z_field, 2),
            "geometry": ("zero-thickness copper at the same w/h: the solver is run "
                         "with t = 0 on purpose, because a 35 um copper edge needs a "
                         "mesh thousands of times finer and would not finish, so this "
                         "checks the CLOSED FORM's geometry term and not the "
                         "thickness term"),
            "note": ("closed_minus_field_pct compares the closed form at t = 0 with "
                     "the solved value at t = 0 - a positive number means the closed "
                     "form reads high on this geometry"),
            "mesh": {"nx": section.nx, "ny": section.ny,
                     "nodes": section.nx * section.ny},
        }

    return ok(
        value=out,
        unit="ohm",
        model="feedline_" + kind,
        basis=_BASIS[kind],
        precision=_PRECISION[kind],
        assumptions={
            "kind": kind, "width_mm": w, "dielectric_mm": h, "gap_mm": g,
            "copper_um": t * 1000.0, "er": er,
            "stripline_dielectric_arg": ("trace to ONE reference plane; the "
                                         "plane-to-plane gap is 2x this" if
                                         kind == "stripline" else None),
            "backside_ground": (kind == "gcpw"),
            "dispersion": "not modelled",
        },
    )

# ------------------------------------------------------------------ reverse
def feedline_width(target_ohm, kind, dielectric_mm, gap_mm=None, copper_um=0.0,
                   er=4.3, tol_ohm=1e-4):
    """The width that gives a target Z0, or the reachable band.

    Bisection on the forward model, so the answer is the same function the caller
    can re-run - no separate fit that could disagree with it. When the target sits
    outside what a sane width range can reach, the reachable band is returned and
    the status is an error rather than a width that silently misses.
    """
    kind = str(kind or "").strip().lower()
    if kind not in KINDS:
        return err("kind must be one of " + ", ".join(KINDS), "ARG_INVALID")
    try:
        target = float(target_ohm)
        h = float(dielectric_mm)
        t = float(copper_um or 0.0) / 1000.0
        er = float(er)
    except (TypeError, ValueError):
        return err("numeric arguments required", "ARG_INVALID")
    if target <= 0 or h <= 0 or t < 0 or er < 1.0:
        return err("target_ohm and dielectric_mm must be > 0", "ARG_RANGE")
    g = None
    if kind in ("cpw", "gcpw"):
        if gap_mm is None:
            return err("a coplanar line needs gap_mm", "ARG_REQUIRED")
        g = float(gap_mm)
        if g <= 0:
            return err("gap_mm must be > 0", "ARG_RANGE")

    def z_at(width):
        r = _solve(kind, width, h, g, t, er)[0]
        return r

    # Z0 falls monotonically as the width grows, for all four geometries. The
    # search window is in units of the dielectric so it means the same thing on a
    # 0.1 mm and a 3 mm stackup - and it is the SAME window the forward model
    # accepts, so this function can never return a width that the answer it was
    # solved from would refuse (a first draft stopped at 1e-4*h and handed back
    # w/h = 0.03 for a 200 ohm target, which the forward model rejects).
    lo, hi = W_OVER_H_MIN * h, W_OVER_H_MAX * h
    z_lo, z_hi = z_at(lo), z_at(hi)
    if z_hi == z_hi and z_lo == z_lo:
        if not (z_hi <= target <= z_lo):
            return err(
                "target %.3f ohm is outside what w/h = %.2f..%.1f on this dielectric "
                "can reach (%.1f .. %.1f ohm)"
                % (target, W_OVER_H_MIN, W_OVER_H_MAX, z_hi, z_lo),
                "UNREACHABLE",
                hint=("move the target by changing er or the dielectric height, or "
                      "(for coplanar) the gap - the width alone cannot get there"),
                extra={"reachable_ohm": {"widest_width_mm": round(hi, 6),
                                         "widest_z_ohm": round(z_hi, 3),
                                         "narrowest_width_mm": round(lo, 6),
                                         "narrowest_z_ohm": round(z_lo, 3)}})
    for _ in range(200):
        mid = 0.5 * (lo + hi)
        z = z_at(mid)
        if z > target:
            lo = mid
        else:
            hi = mid
        if abs(z_at(0.5 * (lo + hi)) - target) < tol_ohm:
            break
    width = 0.5 * (lo + hi)
    z0, ee, extra = _solve(kind, width, h, g, t, er)
    return ok(
        value={"width_mm": round(width, 6), "z0_ohm": round(z0, 4),
               "eps_eff": round(ee, 5), "kind": kind},
        unit="mm",
        model="feedline_width_" + kind,
        basis=_BASIS[kind] + "; solved by bisection on that model, so the width and the "
               "forward answer cannot disagree",
        precision=_PRECISION[kind],
        assumptions={
            "target_ohm": target, "dielectric_mm": h, "gap_mm": g,
            "copper_um": t * 1000.0, "er": er,
            "tolerance_ohm": tol_ohm,
            "sanity_note": ("the search window is the forward model's own validity "
                            "window, w/h in %.2f..%.1f, so the width returned here can "
                            "always be re-checked with feedline_impedance"
                            % (W_OVER_H_MIN, W_OVER_H_MAX)),
        },
    )
