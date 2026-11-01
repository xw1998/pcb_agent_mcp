"""AC electro-thermal: the current heats the copper, the hot copper has a bigger
skin depth, and a bigger skin depth means a thinner effective shell.

The DC loop (`electrothermal`) is linear in T, so it has a closed form and an
exact runaway test. This loop is NOT linear: at high frequency the current is
pushed into a shell of depth delta ~ sqrt(rho/(pi*f*mu)), so

    R_ac ~ Rs = rho/delta ~ sqrt(rho)        (skin-dominated)
    R_ac ~ rho*L/A                            (wall-limited: the wall is
                                               thinner than delta)

i.e. d lnR / d lnrho is 1/2 in the first case and 1 in the second, while the DC
answer is the second one always. That single exponent decides whether the loop
can run away at all:

    dT = k*R(dT),   k = I^2 * Rth

  * dlnR/dlnrho = 1  (DC / wall-limited): R is affine in dT, the gain
    G = k*dR/dT is constant, and G >= 1 means NO steady state exists.
  * dlnR/dlnrho = 1/2: R grows like sqrt(dT), and sqrt grows slower than dT, so
    a steady state ALWAYS exists (for the linear rho(T) law) - it can just be
    very hot. Calling that "runaway" would be wrong; the answer says which one
    it is, and refuses above the melting point because past there the linear law
    and the geometry are both gone.

This module measures the exponent instead of assuming it: `dlnr_dlnrho` is a
central difference on the actual solver at the solution point, and the checks
compare it against 0.5 / 1.0 with the regime named.

Rth is the caller's, always - same reason as in `electrothermal`: there is no
honest default for a thermal path, and a made-up one would be the only number in
this whole layer that was not derived from something.
"""
import math

from .result import ok, err
from . import power as _power
from . import skin as _skin
from . import roundwire as _round
from .roughness import roughness_factor

MU0 = 4e-7 * math.pi
RHO20_CU = _power.RHO_CU_20      # ohm*m at 20 C
ALPHA_CU = _power.ALPHA_CU       # 1/K
T20 = 20.0                       # C

MELT_CU_C = 1084.62              # C, copper melting point (1 atm)
# The fixed point is searched below this temperature.  It is the melting point
# itself, not a round number: above it the geometry (and the linear rho law)
# describe a liquid.
DT_CAP_C = MELT_CU_C

# Used ONLY to answer "does the loop have a fixed point at all" when the exponent
# is below 1. Past the melting point both the linear rho law and the geometry are
# fiction, so the number that comes out of this bracket is reported as an
# extrapolation and never as a temperature.
_EXTRAP_CAP_C = 1.0e6

_NEWTON_H = 1e-3                 # K, step for the numerical dlnR/dlnrho


def _sig(x, digits=6):
    if x is None:
        return None
    x = float(x)
    if x == 0 or x != x:
        return 0.0 if x == 0 else None
    if abs(x) >= 1e-4 and abs(x) < 1e7:
        n = digits - 1 - int(math.floor(math.log10(abs(x))))
        return round(x, n) if n > 0 else float(round(x, n))
    return float("%.*e" % (digits - 1, x))


# ------------------------------------------------------------------ geometry
def _geometry(shape, length_mm, width_mm, thickness_um, thickness_mm,
              faces, diameter_mm, drill_mm, plating_um, wall_um):
    """One dict per shape, or (None, err-envelope)."""
    if shape not in ("sheet", "round"):
        return None, err(
            "shape must be 'sheet' or 'round', not %r. It is not inferred from "
            "which arguments happen to be present: a sheet and a round wire at "
            "the same thickness are two different problems (1-D Cartesian vs "
            "Bessel) and the same numbers would mean different things"
            % (shape,), "BAD_PARAMETER")

    try:
        L = float(length_mm)
    except (TypeError, ValueError):
        return None, err("length_mm is required and must be a number - a "
                         "temperature needs an absolute resistance, not ohms per "
                         "square", "ARG_REQUIRED")
    if not (L > 0.0):
        return None, err("length_mm must be > 0", "ARG_RANGE")

    if shape == "sheet":
        if (thickness_mm is None) == (thickness_um is None):
            return None, err("give exactly one of thickness_mm or thickness_um "
                             "(35 um is 1 oz, 17 um is half an ounce, and the "
                             "skin term depends on which one the board has)",
                             "ARG_REQUIRED")
        try:
            t = (float(thickness_mm) if thickness_mm is not None
                 else float(thickness_um) / 1e3)
            w = float(width_mm)
        except (TypeError, ValueError):
            return None, err("thickness and width_mm must be numbers",
                             "BAD_PARAMETER")
        if not (t > 0.0 and w > 0.0):
            return None, err("thickness and width_mm must be > 0", "ARG_RANGE")
        if diameter_mm is not None or drill_mm is not None:
            return None, err("a sheet has width_mm + thickness, not a diameter "
                             "or a drill", "ARG_INVALID")
        if faces not in _skin._FACE_FACTOR:
            return None, err("faces must be 'one' (return current on one side: "
                             "microstrip, a plane over its return) or 'two' "
                             "(symmetric: stripline, a plane between two "
                             "returns), not %r - it is a factor of two in the "
                             "effective thickness, not a cosmetic choice"
                             % (faces,), "BAD_PARAMETER")
        return {"kind": "sheet", "t_mm": t, "w_mm": w, "l_mm": L,
                "faces": faces, "k_face": _skin._FACE_FACTOR[faces]}, None

    # ---- round: exactly one of diameter_mm | (diameter_mm, wall_um) |
    #      (drill_mm, plating_um)
    if wall_um is not None and drill_mm is not None:
        return None, err("wall_um goes with diameter_mm; a drill uses "
                         "plating_um. Two descriptions of one wall is one too "
                         "many", "ARG_INVALID")
    if drill_mm is not None:
        if diameter_mm is not None:
            return None, err("give either diameter_mm or drill_mm, not both - "
                             "they are two descriptions of the same outer "
                             "diameter", "ARG_INVALID")
        if plating_um is None:
            return None, err("a drill needs plating_um: a bare hole is not a "
                             "conductor", "ARG_REQUIRED")
        try:
            d = float(drill_mm)
            p = float(plating_um)
        except (TypeError, ValueError):
            return None, err("drill_mm and plating_um must be numbers",
                             "BAD_PARAMETER")
        if not (d > 0.0 and p > 0.0):
            return None, err("drill_mm and plating_um must be > 0", "ARG_RANGE")
        if 2.0 * p / 1e3 >= d:
            return None, err("plating %.3g um closes a %.4g mm drill" % (p, d),
                             "ARG_RANGE",
                             hint="check the hole convention: this reads "
                                  "drill_mm as the FINISHED hole with the copper "
                                  "outside it (OD = drill + 2*plating), which is "
                                  "what ac_round_conductor uses")
        a1 = d / 2.0
        a2 = a1 + p / 1e3
        return {"kind": "round", "a1": a1, "a2": a2, "l_mm": L,
                "hollow": True}, None
    try:
        a2 = float(diameter_mm) / 2.0
    except (TypeError, ValueError):
        return None, err("a round conductor needs diameter_mm (or drill_mm + "
                         "plating_um)", "ARG_REQUIRED")
    if not (a2 > 0.0):
        return None, err("diameter_mm must be > 0", "ARG_RANGE")
    a1 = 0.0
    if wall_um is not None:
        try:
            wall = float(wall_um) / 1e3
        except (TypeError, ValueError):
            return None, err("wall_um must be a number", "BAD_PARAMETER")
        if not (wall > 0.0):
            return None, err("wall_um must be > 0", "ARG_RANGE")
        if wall >= a2:
            return None, err("wall_um %.4g mm is not thinner than the radius "
                             "%.4g mm" % (wall, a2), "ARG_RANGE")
        a1 = a2 - wall
    return {"kind": "round", "a1": a1, "a2": a2, "l_mm": L,
            "hollow": a1 > 0.0}, None


def _rho_ohm_mm(t_c, rho20=None, alpha=None):
    """rho in ohm*mm at temperature t_c (the unit `skin`/`roundwire` take).

    rho20/alpha are the caller's pair when given (annealed vs electro-deposited
    copper, a different alloy) and the IACS constants when not. They travel
    together: a rho20 without its alpha is a different metal with copper's
    temperature coefficient.
    """
    rho20 = RHO20_CU if rho20 is None else rho20
    alpha = ALPHA_CU if alpha is None else alpha
    return rho20 * (1.0 + alpha * (float(t_c) - T20)) * 1e3


def _one(geom, f, t_c, mur, rq_m, rho20=None, alpha=None):
    """Resistance at one temperature: (ohms, dict of the local state).

    This is the ONLY place the AC resistance is evaluated; the root finder and
    the exponent measurement both come through here, so a fixed point and a
    dlnR/dlnrho can never be computed from two slightly different models.
    """
    rho = _rho_ohm_mm(t_c, rho20, alpha)         # ohm*mm
    rho_m = rho * 1e-3                           # ohm*m
    delta_m = math.sqrt(rho_m / (math.pi * f * MU0 * mur))
    delta_mm = delta_m * 1e3
    k_rgh = 1.0
    if geom["kind"] == "sheet":
        t = geom["t_mm"]
        s = (t * 1e-3) / (geom["k_face"] * delta_m)
        r_ratio, _x_ratio = _skin._ratio_from_s(s)
        r_dc_sq = rho / t                        # ohm per square
        r = r_dc_sq * r_ratio * geom["l_mm"] / geom["w_mm"]
        if rq_m:
            k_rgh = roughness_factor(rq_m, delta_m)
            r *= k_rgh
        return r, {"delta_um": delta_mm * 1e3, "s": s, "r_over_rdc": r_ratio,
                   "roughness_k": k_rgh, "x_out": None,
                   "regime": ("dc" if s <= 0.5 else
                              "transition" if s < 3.0 else "skin-dominated")}
    a1, a2 = geom["a1"], geom["a2"]
    x_out = a2 / delta_mm
    x_in = a1 / delta_mm
    if geom["hollow"]:
        z = _round.riccati_ratio(x_out, x_in)[0]
        x_wall = x_out - x_in
        regime = ("wall-limited" if x_wall <= 1.0 else
                  "transition" if x_out < 3.0 else "skin-dominated")
    else:
        z = _round._solid_ratio(x_out)
        x_wall = x_out
        regime = ("dc" if x_out <= 0.5 else
                  "transition" if x_out < 3.0 else "skin-dominated")
    area = math.pi * (a2 * a2 - a1 * a1)         # mm^2
    # rho is ohm*mm here, so ohm*mm * mm / mm^2 is already ohm: the 1e3 that a
    # ohm*m rho would need is the 1e3 that turns ohm*m into ohm*mm above
    r = rho * geom["l_mm"] / area                # ohm
    r *= z.real
    if rq_m:
        k_rgh = roughness_factor(rq_m, delta_m)
        r *= k_rgh
    return r, {"delta_um": delta_mm * 1e3, "s": None, "r_over_rdc": z.real,
               "roughness_k": k_rgh, "x_out": x_out, "x_wall": x_wall,
               "regime": regime}


def _beta_at(geom, f, t_c, mur, rq_m, r0=None, rho20=None, alpha=None):
    """dlnR/dlnrho at fixed T, by a central difference in T.

    This is the measured exponent, not an assumed 1/2: T -> rho is affine, so
    a +-h step in T is a +-alpha*h/rho step in ln(rho), and the slope between
    the two resistances is the slope in rho.
    """
    r_hi, _ = _one(geom, f, t_c + _NEWTON_H, mur, rq_m, rho20, alpha)
    r_lo, _ = _one(geom, f, t_c - _NEWTON_H, mur, rq_m, rho20, alpha)
    r_mid = (r0 if r0 is not None
             else _one(geom, f, t_c, mur, rq_m, rho20, alpha)[0])
    dlr = math.log(r_hi) - math.log(r_lo)
    # dlnrho/dT = alpha/(1+alpha*(T-20)) exactly, so chain back to rho
    dlrho = (math.log(_rho_ohm_mm(t_c + _NEWTON_H, rho20, alpha))
             - math.log(_rho_ohm_mm(t_c - _NEWTON_H, rho20, alpha)))
    if dlrho == 0.0:
        return 1.0, r_mid
    return dlr / dlrho, r_mid


# --------------------------------------------------------------------- solve
_MAX_HARMONICS = 32

def _r_dc(geom, t_c, rho20=None, alpha=None):
    """The DC resistance at one temperature, straight from the geometry.

    Not r / r_over_rdc: that ratio still carries the roughness multiplier and the
    solver's own rounding, and the DC term of a spectrum has neither. rho is in
    ohm*mm here, so ohm*mm * mm / mm^2 is already ohm (the 1e3 that turns ohm*m
    into ohm*mm was already spent).
    """
    rho = _rho_ohm_mm(t_c, rho20, alpha)
    if geom["kind"] == "sheet":
        return rho * geom["l_mm"] / (geom["w_mm"] * geom["t_mm"])
    area = math.pi * (geom["a2"] * geom["a2"] - geom["a1"] * geom["a1"])
    return rho * geom["l_mm"] / area

def ac_self_heating(shape=None, frequency_hz=None, current_a=None,
                    rth_c_per_w=None, length_mm=None, width_mm=None, thickness_um=None,
                    thickness_mm=None, faces="one", diameter_mm=None,
                    drill_mm=None, plating_um=None, wall_um=None,
                    t_ambient_c=25.0, mu_r=1.0, roughness_rq_um=None,
                    alpha_per_k=None, rho20_ohm_m=None, tol_c=1e-9,
                    max_iter=400, harmonics=None, dc_current_a=None):
    """Temperature of a conductor carrying AC current, solved self-consistently.

    dT = I^2 * R_ac(f, rho(T_amb + dT)) * Rth, root-found by bisection on
    g(dT) = dT - k*R(dT) in [0, melt].  Bisection, not a fixed-point iteration,
    because the loop is not a contraction here: it is a root of a monotone
    (convex) function, so a sign change IS the existence proof and no iterate
    can be reported as an answer while the loop is still moving.

    A pulse train is not one frequency. Give `harmonics` (a list of
    {frequency_hz, current_a} RMS magnitudes) and optionally `dc_current_a`, and
    the loop becomes g(dT) = dT - Rth*sum_k I_k^2*R_k(T). Every harmonic
    dissipates in the SAME copper, so the powers add and the temperatures do
    not; the DC term sees the whole cross-section while the top harmonic sees a
    skin. `harmonics=None, dc_current_a=None` is the original one-frequency
    path, unchanged.
    """
    if shape is None:
        return err("shape is required ('sheet' or 'round'): a sheet and a round "
                   "conductor at the same thickness are two different problems "
                   "and it is not inferred from which arguments are present",
                   "ARG_REQUIRED")
    if harmonics is not None and current_a is not None:
        return err("give current_a (one frequency) OR harmonics (a spectrum), not "
                   "both: the two mean different things and a tool that silently "
                   "preferred one of them would be lying about the other",
                   "ARG_INVALID")
    if harmonics is not None and frequency_hz is not None:
        return err("frequency_hz goes with current_a; with harmonics every entry "
                   "carries its own frequency_hz, and one top-level frequency "
                   "would silently apply to all of them", "ARG_INVALID")
    if harmonics is None and current_a is None and dc_current_a is not None:
        return err("a DC-only call is electrothermal_conductor / "
                   "electrothermal_trace: those have the exact closed form and the "
                   "exact runaway test, and this loop exists for the frequency "
                   "dependence. Give a harmonic, or use the DC tool",
                   "ARG_INVALID")
    if harmonics is None and current_a is None:
        return err("current_a is required (for a spectrum give harmonics and/or "
                   "dc_current_a)", "ARG_REQUIRED")
    if rth_c_per_w is None:
        return err("rth_c_per_w is required: it is never assumed here (a default "
                   "thermal resistance would be the only number in this answer "
                   "that came from nowhere)", "ARG_REQUIRED")
    if harmonics is None:
        try:
            f = float(frequency_hz)
        except (TypeError, ValueError):
            return err("frequency_hz is required and must be a number: this is the AC "
                       "loop, and without a frequency it is `electrothermal_conductor`"
                       , "ARG_REQUIRED")
        if not (f > 0.0):
            return err("frequency_hz must be > 0 (for DC use electrothermal_trace / "
                       "electrothermal_conductor, where the loop is linear in T)",
                       "BAD_PARAMETER")
        try:
            i = float(current_a)
        except (TypeError, ValueError):
            return err("current_a, rth_c_per_w, t_ambient_c and mu_r must be numbers",
                       "BAD_PARAMETER")
    else:
        f = None
        i = None
    try:
        rth = float(rth_c_per_w)
        ta = float(t_ambient_c)
        mur = float(mu_r) if mu_r is not None else 1.0
    except (TypeError, ValueError):
        return err("current_a, rth_c_per_w, t_ambient_c and mu_r must be numbers",
                   "BAD_PARAMETER")
    if i is not None and not (i >= 0.0):
        return err("current_a must be >= 0", "ARG_RANGE")
    if not (rth > 0.0):
        return err("rth_c_per_w must be > 0: the thermal path is never assumed "
                   "here, because a default Rth would be the only number in this "
                   "answer that was not derived from something",
                   "ARG_RANGE")
    if not (mur >= 1.0):
        return err("mu_r must be >= 1 (copper is 1.0)", "BAD_PARAMETER")

    alpha = ALPHA_CU if alpha_per_k is None else float(alpha_per_k)
    rho20 = RHO20_CU if rho20_ohm_m is None else float(rho20_ohm_m)
    if not (alpha >= 0.0):
        return err("alpha_per_k must be >= 0", "ARG_RANGE")
    if not (rho20 > 0.0):
        return err("rho20_ohm_m must be > 0", "ARG_RANGE")

    geom, e = _geometry(shape, length_mm, width_mm, thickness_um, thickness_mm,
                        faces, diameter_mm, drill_mm, plating_um, wall_um)
    if e is not None:
        return e

    rq_m = None
    if roughness_rq_um is not None:
        try:
            rq_m = float(roughness_rq_um) * 1e-6
        except (TypeError, ValueError):
            return err("roughness_rq_um must be a number (RMS height Rq, not Rz)",
                       "BAD_PARAMETER")
        if rq_m < 0.0:
            return err("roughness_rq_um must be >= 0", "ARG_RANGE")
        if rq_m == 0.0:
            rq_m = None

    # ---- the current spectrum ------------------------------------------------
    #
    # Each entry is (kind, frequency or None, RMS magnitude). A DC term and an AC
    # term are not the same object: the DC current sees the whole cross-section,
    # the AC current sees a shell, so they cannot be collapsed into one "RMS
    # current at one frequency" without an error that grows with the skin effect.
    terms = []
    if harmonics is None:
        terms.append(("ac", f, i))
    else:
        if not isinstance(harmonics, (list, tuple)):
            return err("harmonics must be a list of {frequency_hz, current_a} "
                       "objects, not %s: one number cannot describe a spectrum"
                       % type(harmonics).__name__, "BAD_PARAMETER")
        if len(harmonics) == 0:
            return err("harmonics is empty: a spectrum with no entries carries no "
                       "current, and there is no such thing as the temperature of "
                       "'nothing'", "ARG_INVALID")
        if len(harmonics) > _MAX_HARMONICS:
            return err("harmonics has %d entries; at most %d are accepted"
                       % (len(harmonics), _MAX_HARMONICS), "ARG_RANGE",
                       hint="past harmonic %d of a switching waveform the "
                            "amplitude is far below the temperature-coefficient "
                            "spread, and a longer list would look like precision "
                            "that is not there" % _MAX_HARMONICS)
        f_seen = set()
        for idx, h in enumerate(harmonics):
            if not isinstance(h, dict):
                return err("harmonics[%d] must be an object with frequency_hz and "
                           "current_a, not %s" % (idx, type(h).__name__),
                           "BAD_PARAMETER")
            unknown = set(h) - {"frequency_hz", "current_a"}
            if unknown:
                return err("harmonics[%d] has unknown key(s) %s: each entry is "
                           "exactly {frequency_hz, current_a}"
                           % (idx, ", ".join(sorted(unknown))), "BAD_PARAMETER")
            if "frequency_hz" not in h:
                return err("harmonics[%d] has no frequency_hz: a term with no "
                           "frequency is a DC term, and that is dc_current_a" % idx,
                           "ARG_REQUIRED")
            if "current_a" not in h:
                return err("harmonics[%d] has no current_a" % idx, "ARG_REQUIRED")
            try:
                fk = float(h["frequency_hz"])
                ik = float(h["current_a"])
            except (TypeError, ValueError):
                return err("harmonics[%d]: frequency_hz and current_a must be "
                           "numbers" % idx, "BAD_PARAMETER")
            if not (fk > 0.0):
                return err("harmonics[%d].frequency_hz must be > 0: the DC term is "
                           "dc_current_a, not a harmonic at 0 Hz" % idx,
                           "BAD_PARAMETER")
            if not (ik >= 0.0):
                return err("harmonics[%d].current_a must be >= 0: these are RMS "
                           "magnitudes, and I^2*R of a negative current is the same "
                           "number" % idx, "ARG_RANGE")
            if fk in f_seen:
                return err("harmonics[%d].frequency_hz = %g repeats an earlier "
                           "entry: two currents at ONE frequency have to be added "
                           "as phasors, and this model has no phase, so adding "
                           "their magnitudes would overstate the loss by up to 2x"
                           % (idx, fk), "ARG_INVALID")
            f_seen.add(fk)
            terms.append(("ac", fk, ik))
    if dc_current_a is not None:
        try:
            i_dc = float(dc_current_a)
        except (TypeError, ValueError):
            return err("dc_current_a must be a number", "BAD_PARAMETER")
        if not (i_dc >= 0.0):
            return err("dc_current_a must be >= 0", "ARG_RANGE")
        terms.append(("dc", None, i_dc))
    # every spectrum that reaches this line has at least one AC term: a call with
    # only dc_current_a was refused above, so there is no DC-only case here
    multi = not (harmonics is None and dc_current_a is None)

    i_rms = math.sqrt(sum(t[2] * t[2] for t in terms))
    p_cold = None

    # one model, one entry point: r_at() is a thin wrapper, so the bisection and
    # the exponent measurement below cannot drift apart
    def r_at(t_c):
        return _one(geom, f, t_c, mur, rq_m, rho20, alpha)

    def p_at(t_c):
        """Total dissipated power at one temperature, in W (for reporting)."""
        tot = 0.0
        for kind, fk, ik in terms:
            rk = (_r_dc(geom, t_c, rho20, alpha) if kind == "dc"
                  else _one(geom, fk, t_c, mur, rq_m, rho20, alpha)[0])
            tot += (ik * ik) * rk
        return tot

    def w_at(t_c):
        """Total I^2*Rth*R in K: the temperature the sum of the powers causes.

        The grouping is (I^2*Rth)*R, the SAME one the one-frequency branch uses,
        so a spectrum with a single AC term reproduces that branch bit for bit
        instead of merely to within a rounding error.
        """
        tot = 0.0
        for kind, fk, ik in terms:
            rk = (_r_dc(geom, t_c, rho20, alpha) if kind == "dc"
                  else _one(geom, fk, t_c, mur, rq_m, rho20, alpha)[0])
            tot += (ik * ik * rth) * rk
        return tot

    def beta_p(t_c, p0=None):
        """dlnP/dlnrho of the SUM, at fixed T, by a central difference in T.

        This is not the exponent of any one harmonic: it is the power-weighted
        average of theirs, because each term's dT contribution is weighted by
        its own power. The DC term's exponent is exactly 1 (R_dc is linear in
        rho, no difference needed), and with a single AC term the arithmetic is
        `_beta_at`'s own, so the two paths agree exactly.
        """
        betas, weights = [], []
        for kind, fk, ik in terms:
            c = ik * ik
            if c == 0.0:
                continue                    # carries no power, so no weight
            if kind == "dc":
                betas.append(1.0)
                weights.append(c * _r_dc(geom, t_c, rho20, alpha))
            else:
                rk, _ = _one(geom, fk, t_c, mur, rq_m, rho20, alpha)
                b, _ = _beta_at(geom, fk, t_c, mur, rq_m, rk, rho20, alpha)
                betas.append(b)
                weights.append(c * rk)
        tot = sum(weights)
        p_mid = p0 if p0 is not None else tot
        if tot <= 0.0 or len(betas) == 0:
            return 1.0, p_mid
        if len(betas) == 1:
            return betas[0], p_mid
        return sum(w * b for w, b in zip(weights, betas)) / tot, p_mid

    k = (i * i * rth) if not multi else None    # K per ohm, one-frequency path
    if multi:
        p_cold = p_at(ta)
        w_cold = w_at(ta)
        r_cold, st_cold = None, None
        beta_cold, _ = beta_p(ta, None)
        cold_gain = w_cold * alpha * beta_cold / (1.0 + alpha * (ta - T20))
    else:
        r_cold, st_cold = r_at(ta)
        beta_cold, _ = _beta_at(geom, f, ta, mur, rq_m, r_cold, rho20, alpha)
        w_cold = k * r_cold
        # the loop gain AT AMBIENT: it is the only gain that is a property of the
        # design rather than of a solution that may not exist. With an exponent of
        # 1 it decides existence outright; with 1/2 it only decides where
        # "settles" starts to mean "settles above melting".
        cold_gain = w_cold * alpha * beta_cold / (1.0 + alpha * (ta - T20))

    def unit_gain_current(beta):
        """I at which the COLD loop gain reaches 1.

        A runaway threshold only when the exponent is 1: with the 1/2 of a
        skin-dominated conductor the gain falls as the copper heats, so the loop
        settles instead of diverging and this is just where "settles" starts to
        mean "settles above melting".
        """
        if alpha <= 0.0 or beta <= 0.0:
            return None
        return math.sqrt((1.0 + alpha * (ta - T20))
                         / (rth * r_cold * alpha * beta))

    i_unit = unit_gain_current(beta_cold) if not multi else None
    # With a spectrum there is no single current to quote: what is a property of
    # the design is the FACTOR every magnitude has to be multiplied by to reach
    # a cold loop gain of 1 - sqrt(1/cold_gain), because the gain is quadratic in
    # the currents and everything else is fixed.
    unit_gain_scale = None
    if multi and alpha > 0.0 and beta_cold > 0.0 and w_cold > 0.0:
        unit_gain_scale = math.sqrt((1.0 + alpha * (ta - T20))
                                    / (w_cold * alpha * beta_cold))

    if not multi:
        cur_txt = "the current at most %.3f A" % (i_unit if i_unit else 0.0)
        g_txt = "g(dT) = dT - I^2*Rth*R(dT)"
        diverge_txt = (
            "the cold loop gain I^2*Rth*dR/dT is %.3f with an exponent of 1: the "
            "resistance grows exactly as fast as the heat that drives it, so no "
            "fixed point exists at any temperature" % (cold_gain,))
    else:
        cur_txt = ("every magnitude scaled by at most %.4f" % (unit_gain_scale,)
                   if unit_gain_scale is not None else "a smaller current")
        g_txt = "g(dT) = dT - Rth*P(dT)"
        diverge_txt = (
            "the cold loop gain Rth*dP/dT is %.3f with a power-weighted exponent "
            "of 1: the total I^2*R grows exactly as fast as the heat that drives "
            "it, so no fixed point exists at any temperature" % (cold_gain,))

    def g(dt):
        if multi:
            return dt - w_at(ta + dt)
        return dt - k * r_at(ta + dt)[0]

    # A zero current is a legitimate, trivial answer: dT = 0.
    zero_power = (w_cold == 0.0)
    if zero_power:
        dt = 0.0
        it = 0
        bracket = None
        w_hot = w_cold
        if not multi:
            r_hot, st_hot = r_cold, st_cold
    else:
        lo, hi = 0.0, 1.0
        while g(hi) < 0.0:
            lo = hi
            hi *= 2.0
            if hi > DT_CAP_C:
                gain_cold = cold_gain
                # Two different failures wear the same name. With exponent 1 the
                # loop is linearly divergent and NO temperature is a fixed point.
                # With exponent < 1, dT - k*R(dT) still runs off to +infinity, so
                # a fixed point DOES exist - it is just beyond the melting point,
                # where the model stops.
                #
                # So the uncapped search is run UNCONDITIONALLY. Gating it on
                # "exponent < 1" was wrong: with an exponent of exactly 1 the loop
                # is still algebraic, and whenever the cold gain is below 1 it has
                # a root - at about k*r_cold/(1 - cold_gain) K, far above melting.
                # That gate reported "no steady state at any temperature" for a
                # loop that demonstrably has one (11 A on a 0.1 mm trace: gain
                # 0.937, root near dT 3850 K). Finding the root first, and only
                # then choosing the wording from the exponent, is the only order
                # that cannot produce that wrong answer.
                lo2, hi2 = DT_CAP_C, 2.0 * DT_CAP_C
                while g(hi2) < 0.0 and hi2 < _EXTRAP_CAP_C:
                    lo2 = hi2
                    hi2 *= 2.0
                dt_x = None
                if g(hi2) >= 0.0:
                    n = 0
                    while n < 400 and (hi2 - lo2) > 1e-6 * hi2:
                        mid = 0.5 * (lo2 + hi2)
                        if g(mid) < 0.0:
                            lo2 = mid
                        else:
                            hi2 = mid
                        n += 1
                    dt_x = 0.5 * (lo2 + hi2)
                # The actionable number in every case: the Rth that would put the
                # root at (not above) the melting point, i.e. the largest lump the
                # copper can live with at this current and frequency.
                dt_melt = MELT_CU_C - ta
                if multi:
                    p_melt = p_at(ta + dt_melt)
                    rth_cap = (dt_melt / p_melt) if p_melt > 0.0 else None
                else:
                    r_melt = r_at(ta + dt_melt)[0]
                    rth_cap = (dt_melt / (i * i * r_melt)) if r_melt > 0.0 else None
                extra = {"runaway_gain_cold": _sig(gain_cold),
                         "dlnr_dlnrho_cold": _sig(beta_cold),
                         "checked_up_to_c": _sig(ta + DT_CAP_C)}
                if multi:
                    extra["unit_gain_current_scale"] = (
                        _sig(unit_gain_scale) if unit_gain_scale else None)
                    extra["power_w_cold"] = _sig(p_cold)
                    extra["n_terms"] = len(terms)
                else:
                    extra["r_cold_ohm"] = _sig(r_cold)
                    extra["unit_gain_current_a"] = (_sig(i_unit) if i_unit
                                                    else None)
                extra["rth_for_root_at_melt_c_per_w"] = (_sig(rth_cap)
                                                         if rth_cap else None)
                if dt_x is not None:
                    extra["root_beyond_melt_dt_c"] = _sig(dt_x)
                    extra["root_beyond_melt_t_c"] = _sig(ta + dt_x)
                    msg = ("above the melting point: the self-consistent "
                           "temperature would be %.0f C (dT %.0f K), far past "
                           "the %.0f C where both the linear rho law and the "
                           "geometry stop meaning anything"
                           % (ta + dt_x, dt_x, MELT_CU_C))
                    if beta_cold > 0.995:
                        hint = ("dlnR/dlnrho = %.3f, so the resistance grows as "
                                "fast as the heat that drives it: the loop sits "
                                "ON the algebraic edge, and the root exists only "
                                "because the cold-point gain (%.4f) is below 1. "
                                "It therefore moves by orders of magnitude for a "
                                "small change in current, and it is at dT = %.0f "
                                "K, far past melting. Rth must be at most %.4g "
                                "K/W or %s. The %.0f C "
                                "figure is extrapolated past melting: an "
                                "ordering, not a reading"
                                % (beta_cold, gain_cold, dt_x,
                                   rth_cap if rth_cap else 0.0,
                                   cur_txt, ta + dt_x))
                    else:
                        hint = ("dlnR/dlnrho = %.3f, so the resistance grows "
                                "slower than dT and the loop DOES have a fixed "
                                "point - this is not the algebraic divergence of "
                                "a DC trace. From here, Rth must be at most %.4g "
                                "K/W or %s. The %.0f C "
                                "figure is extrapolated past melting: an "
                                "ordering, not a reading"
                                % (beta_cold, rth_cap if rth_cap else 0.0,
                                   cur_txt, ta + dt_x))
                else:
                    if beta_cold > 0.999:
                        msg = ("no steady state at any temperature: %s is still "
                               "negative at %.0f C (dT %.0f K), and with "
                               "dlnR/dlnrho = %.4f it stays negative for ever"
                               % (g_txt, ta + DT_CAP_C, DT_CAP_C, beta_cold))
                        hint = ("%s and a higher search ceiling cannot find one. "
                                "Rth must be at most %.4g K/W, or %s"
                                % (diverge_txt, rth_cap if rth_cap else 0.0,
                                   cur_txt))
                    else:
                        msg = ("no steady state below dT = %.0f K: the loop "
                               "closes - dlnR/dlnrho = %.4f < 1 guarantees a "
                               "fixed point - but it sits beyond the ceiling "
                               "used to ask that question, and g(dT) is still "
                               "negative at %.0f C"
                               % (_EXTRAP_CAP_C, beta_cold, ta + DT_CAP_C))
                        hint = ("with an exponent below 1 the loop cannot run "
                                "away algebraically, so this is not the DC "
                                "divergence; at this gain (%0.3f at the cold "
                                "point) the fixed point exists only at a "
                                "temperature this model has no business "
                                "describing. Rth must be at most %.4g K/W, or "
                                "%s"
                                % (gain_cold, rth_cap if rth_cap else 0.0,
                                   cur_txt))
                return err(msg, "THERMAL_RUNAWAY", hint=hint, extra=extra)
        it = 0
        bracket = (lo, hi)
        while it < int(max_iter):
            mid = 0.5 * (lo + hi)
            if g(mid) < 0.0:
                lo = mid
            else:
                hi = mid
            it += 1
            if (hi - lo) <= tol_c:
                break
        dt = 0.5 * (lo + hi)
        w_hot = w_at(ta + dt) if multi else k * r_at(ta + dt)[0]
        if g(dt) > 0.0:
            # belt and braces: the accepted point must be a real root
            dt = lo
            w_hot = w_at(ta + dt) if multi else k * r_at(ta + dt)[0]
        if not multi:
            r_hot, st_hot = r_at(ta + dt)

    t_final = ta + dt
    p = p_at(t_final) if multi else i * i * r_hot
    if multi:
        beta, _ = beta_p(t_final, None)
    else:
        beta, _ = _beta_at(geom, f, t_final, mur, rq_m, r_hot, rho20, alpha)
    gain = w_hot * alpha * beta / (1.0 + alpha * (t_final - T20))
    # energy balance is an identity here, but it is the one thing a wrong
    # bisection branch would break, so it is measured and reported
    resid = abs(dt - w_hot)

    # ---- per-term bookkeeping (a spectrum has no single R, R/R_dc or delta)
    term_rows = None
    _raw_betas = []
    if multi:
        term_rows = []
        for kind, fk, ik in terms:
            if kind == "dc":
                rk = _r_dc(geom, t_final, rho20, alpha)
                _raw_betas.append((ik * ik * rk, 1.0))
                term_rows.append({
                    "kind": "dc", "frequency_hz": None,
                    "current_a": _sig(ik),
                    "resistance_hot_ohm": _sig(rk),
                    "power_hot_w": _sig(ik * ik * rk),
                    "r_over_rdc_hot": 1.0,
                    "regime_hot": "dc",
                    "skin_depth_um_hot": None,
                    "roughness_k_hot": 1.0,
                    "dlnr_dlnrho_term": 1.0,
                    "x_outer_hot": None, "x_wall_hot": None,
                })
            else:
                rk, stk = _one(geom, fk, t_final, mur, rq_m, rho20, alpha)
                bterm, _ = _beta_at(geom, fk, t_final, mur, rq_m, rk, rho20, alpha)
                _raw_betas.append((ik * ik * rk, bterm))
                term_rows.append({
                    "kind": "ac", "frequency_hz": _sig(fk),
                    "current_a": _sig(ik),
                    "resistance_hot_ohm": _sig(rk),
                    "power_hot_w": _sig(ik * ik * rk),
                    "r_over_rdc_hot": _sig(stk["r_over_rdc"], 6),
                    "regime_hot": stk["regime"],
                    "skin_depth_um_hot": _sig(stk["delta_um"], 6),
                    "roughness_k_hot": _sig(stk["roughness_k"], 6),
                    "dlnr_dlnrho_term": _sig(bterm, 6),
                    "x_outer_hot": _sig(stk.get("x_out"), 6),
                    "x_wall_hot": _sig(stk.get("x_wall"), 6),
                })
        for row in term_rows:
            row["power_share"] = (_sig(row["power_hot_w"] / p, 4)
                                  if p > 0.0 else 0.0)

    checks = {
        "energy_balance_residual_k": _sig(resid, 3),
        "energy_balance_ok": bool(resid <= 1e-9 * max(1.0, dt)),
        "dlnr_dlnrho": _sig(beta, 6),
        "runaway_gain_at_solution": _sig(gain, 6),
        "stability_margin": _sig(1.0 - gain, 6),
        "power_w": _sig(p),
        "delta_t_c": _sig(dt),
        "t_final_c": _sig(t_final),
        "iterations": it,
        "bracket_k": ([_sig(bracket[0]), _sig(bracket[1])] if bracket else None),
        "melt_margin_c": _sig(MELT_CU_C - t_final),
        "above_100c_linear_law": bool(t_final > 120.0),
        "dlnr_dlnrho_cold": _sig(beta_cold, 6),
    }
    if multi:
        checks["n_terms"] = len(terms)
        checks["rms_current_a"] = _sig(i_rms)
        checks["power_cold_w"] = _sig(p_cold)
        checks["unit_gain_current_scale"] = (_sig(unit_gain_scale)
                                            if unit_gain_scale else None)
        checks["terms"] = term_rows
        # the exponent the sum must land on is the power-weighted mean of the
        # exponents of its parts - an identity, so any drift here is a bug in
        # beta_p and not physics. The raw powers are used, not the 6-digit ones
        # in terms[]: rounding them first would put a 1e-6 wobble into a check
        # whose tolerance is smaller than that.
        wsum = sum(pw * b for pw, b in _raw_betas)
        checks["dlnP_dlnrho_weighted_check"] = (
            _sig(wsum / p, 6) if p > 0.0 else None)
        checks["dlnP_dlnrho_weighted_ok"] = bool(
            p > 0.0 and abs(wsum / p - beta) <= 1e-9 * max(1.0, abs(beta)))
    else:
        checks["resistance_cold_ohm"] = _sig(r_cold)
        checks["resistance_hot_ohm"] = _sig(r_hot)
        checks["resistance_gain"] = _sig(r_hot / r_cold, 6)
        checks["r_over_rdc_hot"] = _sig(st_hot["r_over_rdc"], 6)
        checks["r_over_rdc_cold"] = _sig(st_cold["r_over_rdc"], 6)
        checks["skin_depth_um_hot"] = _sig(st_hot["delta_um"], 6)
        checks["skin_depth_um_cold"] = _sig(st_cold["delta_um"], 6)
        checks["roughness_k_hot"] = _sig(st_hot["roughness_k"], 6)
        checks["regime_hot"] = st_hot["regime"]
        checks["regime_cold"] = st_cold["regime"]
        checks["unit_gain_current_a"] = (_sig(i_unit) if i_unit else None)
        checks["headroom_current"] = (_sig(i_unit / i, 4) if i_unit and i > 0.0
                                      else None)

    # ---- the regime's own exponent, measured against what it should be
    if rq_m:
        # roughness_factor(delta) itself depends on rho, so the exponent is no
        # longer the clean 1/2 - suppressing the expectation is the honest move
        # (measured 0.349 for 1 um Rq at 1 GHz, i.e. better behaved than 1/2)
        checks["beta_expectation"] = None
        checks["beta_deviation"] = None
        checks["beta_expectation_note"] = (
            "roughness is in the answer: the multiplier itself falls as delta "
            "grows (delta ~ sqrt(rho)), so dlnR/dlnrho is below the smooth 1/2 "
            "and there is no prediction to compare against")
    elif multi:
        _EXPECT = {"dc": 1.0, "skin-dominated": 0.5, "wall-limited": 1.0}
        unknown = [r for r in term_rows if r["regime_hot"] not in _EXPECT]
        if unknown:
            checks["beta_expectation"] = None
            checks["beta_deviation"] = None
            checks["beta_expectation_note"] = (
                "at least one term sits in the transition region "
                "(frequency_hz = %s), where the exponent is neither 1/2 nor 1, so "
                "there is nothing to compare the measured %.3f against"
                % (", ".join("%g" % r["frequency_hz"] for r in unknown), beta))
        else:
            wsum = sum(r["power_hot_w"] for r in term_rows)
            exp = (sum(r["power_hot_w"] * _EXPECT[r["regime_hot"]]
                       for r in term_rows) / wsum) if wsum > 0.0 else None
            checks["beta_expectation"] = _sig(exp, 6) if exp is not None else None
            checks["beta_deviation"] = (_sig(abs(beta - exp), 3)
                                        if exp is not None else None)
            checks["beta_expectation_note"] = (
                "power-weighted mean of the per-term exponents (1/2 in a "
                "skin-dominated conductor, 1 where the wall or the DC path sees "
                "the whole section); each term is listed in terms[].regime_hot")
    elif st_hot["regime"] == "skin-dominated":
        checks["beta_expectation"] = 0.5
        checks["beta_deviation"] = _sig(abs(beta - 0.5), 3)
    elif st_hot["regime"] == "wall-limited":
        checks["beta_expectation"] = 1.0
        checks["beta_deviation"] = _sig(abs(beta - 1.0), 3)
    elif st_hot["regime"] == "dc":
        checks["beta_expectation"] = 1.0
        checks["beta_deviation"] = _sig(abs(beta - 1.0), 3)
    else:
        checks["beta_expectation"] = None
        checks["beta_deviation"] = None

    # ---- what a simpler model would have said
    from . import electrothermal as _et
    if multi:
        # (a) the whole spectrum treated as DC: the model a DC-only tool gives.
        # This is NOT the same question as the one-frequency branch's
        # dc_linear_model_*, which holds the AC resistance fixed and drops only
        # the exponent - here the frequency difference is dropped too, so it has
        # its own name instead of quietly changing what the old one means.
        r20c_dc = _r_dc(geom, T20, rho20, alpha)
        dc = _et.conductor_self_heating(r20c_dc, i_rms, rth, ta, alpha_per_k=alpha)
        if dc.get("status") == "ok" and dt > 0.0:
            lin_dt = dc["delta_t_c"]
            checks["all_dc_model_delta_t_c"] = _sig(lin_dt)
            checks["all_dc_model_rel_diff"] = _sig(abs(lin_dt - dt) / dt, 4)
            checks["all_dc_model_note"] = (
                "every term lumped into ONE DC current at the RMS value through "
                "R_dc = rho*L/A: it uses R_dc for all of them, so it understates "
                "each AC term's I^2*R by its own r_over_rdc (listed in terms[]). "
                "Reported as a difference, not as an error bar")
        # (b) the whole spectrum treated as ONE frequency: the model a
        # single-frequency tool gives, with all the RMS current at the frequency
        # of the term that dissipates the most. It OVERSTATES the low harmonics
        # (they are charged the top frequency's R) and understates nothing, so
        # the sign of the difference is the tell.
        dom = max((t for t in terms if t[0] == "ac"),
                  key=lambda t: t[2] * t[2] * _one(geom, t[1], t_final, mur,
                                                   rq_m, rho20, alpha)[0])
        sf = ac_self_heating(
            shape=shape, frequency_hz=dom[1], current_a=i_rms,
            rth_c_per_w=rth, length_mm=length_mm, width_mm=width_mm,
            thickness_um=thickness_um, thickness_mm=thickness_mm, faces=faces,
            diameter_mm=diameter_mm, drill_mm=drill_mm, plating_um=plating_um,
            wall_um=wall_um, t_ambient_c=ta, mu_r=mur,
            roughness_rq_um=roughness_rq_um, alpha_per_k=alpha,
            rho20_ohm_m=rho20, tol_c=tol_c, max_iter=max_iter)
        # sf is a full envelope here (unlike electrothermal's flat dict), so its
        # answer lives under "value"
        if sf.get("status") == "ok" and dt > 0.0:
            sf_dt = sf["value"]["delta_t_c"]
            checks["single_frequency_model_delta_t_c"] = _sig(sf_dt)
            checks["single_frequency_model_rel_diff"] = _sig(abs(sf_dt - dt) / dt, 4)
            checks["single_frequency_model_direction"] = (
                "over" if sf_dt > dt else "under" if sf_dt < dt else "same")
            checks["single_frequency_model_note"] = (
                "the RMS current put entirely at %.6g Hz, the term that "
                "dissipates the most: the low harmonics are charged that "
                "frequency's R, so this is the number a single-frequency tool "
                "would have reported. Reported as a difference, not as an error "
                "bar" % dom[1])
    else:
        # electrothermal's closed form is parameterised by R at 20 C, not at Ta,
        # and feeding it r_cold would inflate dT by the (1+alpha*(Ta-20)) factor
        # - about 2 % at a 25 C ambient, which is exactly the size of a
        # comparison that looks like physics but is a unit slip
        r20c = r_at(T20)[0]
        dc = _et.conductor_self_heating(r20c, i, rth, ta, alpha_per_k=alpha)
        if dc.get("status") == "ok" and dt > 0.0:
            # same fixed point when the exponent is 1; with beta = 1/2 the DC
            # formula is the wrong model, so it is reported, not compared. The
            # extra keys of an envelope are merged at the top level
            # (result.ok), so dT is dc[...]
            lin_dt = dc["delta_t_c"]
            checks["dc_linear_model_delta_t_c"] = _sig(lin_dt)
            checks["dc_linear_model_rel_diff"] = _sig(abs(lin_dt - dt) / dt, 4)
            checks["dc_linear_model_note"] = (
                "the linear-alpha DC loop solved at the SAME cold resistance; it "
                "is the right model only when dlnR/dlnrho == 1 (DC or "
                "wall-limited). Here dlnR/dlnrho = %.3f, so the difference is the "
                "physics, not an error" % beta)

    limits = [
        "Rth is the caller's single lump - this is not a thermal solution, only "
        "the electro-thermal feedback on top of one (feed it "
        "thermal_stack / via_array_thermal / plate_temperature or a measurement)",
        "one conductor, one frequency: no proximity effect from a neighbouring "
        "trace or via, no coupling to a plane's return current",
        "the linear rho(T) law is used past 100 C without a curve; alpha itself "
        "grows with T in real copper, so a hot answer here is optimistic",
        "no melting, no fusing, no electromigration: this reports a temperature, "
        "not whether the copper survives it (fusing_current is the pulse model)",
        "the melting point only caps the search - the geometry and the 1-D skin "
        "solution both stop meaning anything long before it",
    ]
    if multi:
        limits[1] = ("one conductor: no proximity effect from a neighbouring trace "
                     "or via, no coupling to a plane's return current, and the "
                     "same lump Rth for every harmonic (a thin film's thermal "
                     "impedance is frequency dependent; this is not)")
        limits.append("the spectrum is MAGNITUDES only: no phase, so two entries "
                      "at one frequency are refused rather than added as scalars, "
                      "and no intermodulation, no peak-vs-RMS crest factor - the "
                      "currents are RMS, and a pulsed current's average power is "
                      "the sum of its harmonics'")
    else:
        limits.append("single frequency: a pulse train with real harmonic content "
                      "needs each harmonic, and the DC term dominates the heating "
                      "in most of them (harmonics=[{frequency_hz, current_a}], "
                      "dc_current_a=<A>)")

    value = {
        "t_final_c": _sig(t_final),
        "delta_t_c": _sig(dt),
        "t_ambient_c": _sig(ta),
        "power_w": _sig(p),
        "dlnr_dlnrho": _sig(beta, 6),
        "runaway_gain": _sig(gain, 6),
        "runaway_gain_cold": _sig(cold_gain, 6),
        "steady_state_unstable": bool(gain > 1.0),
        "cold_loop_gain_above_one": bool(cold_gain >= 1.0),
        "iterations": it,
        "melt_margin_c": _sig(MELT_CU_C - t_final),
        "roughness_in_the_answer": bool(rq_m),
        "dlnr_dlnrho_cold": _sig(beta_cold, 6),
        "checks": checks,
        "limits": limits,
    }
    if multi:
        value["model_kind"] = "spectrum"
        value["n_terms"] = len(terms)
        value["rms_current_a"] = _sig(i_rms)
        value["power_cold_w"] = _sig(p_cold)
        value["unit_gain_current_scale"] = (_sig(unit_gain_scale)
                                            if unit_gain_scale else None)
        value["terms"] = term_rows
    else:
        value["model_kind"] = "one_frequency"
        value["current_a"] = _sig(i)
        value["frequency_hz"] = _sig(f)
        value["resistance_cold_ohm"] = _sig(r_cold)
        value["resistance_hot_ohm"] = _sig(r_hot)
        value["resistance_gain"] = _sig(r_hot / r_cold, 6)
        value["r_over_rdc_hot"] = _sig(st_hot["r_over_rdc"], 6)
        value["r_over_rdc_cold"] = _sig(st_cold["r_over_rdc"], 6)
        value["skin_depth_um_hot"] = _sig(st_hot["delta_um"], 6)
        value["regime_hot"] = st_hot["regime"]
        value["regime_cold"] = st_cold["regime"]
        value["roughness_k_hot"] = _sig(st_hot["roughness_k"], 6)
        value["unit_gain_current_a"] = (_sig(i_unit) if i_unit else None)
        if geom["kind"] == "round":
            value["x_outer_hot"] = _sig(st_hot["x_out"], 6)
            value["x_wall_hot"] = _sig(st_hot["x_wall"], 6)

    if multi:
        model = (
            "self-consistent AC electro-thermal over a CURRENT SPECTRUM: R_ac(f_k,"
            " rho(T)) from the 1-D exact skin solution (%s) for each AC term plus "
            "R_dc = rho*L/A for the DC term, P(T) = sum_k I_k^2*R_k(T), T = T_amb "
            "+ P*Rth, solved as the root of g(dT) = dT - Rth*P(dT) by bisection on "
            "[0, %.0f C] - a sign change is the existence proof, so a "
            "non-existent steady state comes back as THERMAL_RUNAWAY instead of an "
            "iterate. dlnP/dlnrho is MEASURED on the sum (central difference), so "
            "it is the power-weighted exponent of the terms, not any one "
            "harmonic's"
            % ("1-D Cartesian slab" if geom["kind"] == "sheet"
               else "Bessel / Riccati walk in the annulus", MELT_CU_C))
    else:
        model = (
            "self-consistent AC electro-thermal: R_ac(f, rho(T)) from the 1-D exact "
            "skin solution (%s), rho(T) = rho20*(1+alpha*(T-20)), P = I^2*R, "
            "T = T_amb + P*Rth, solved as the root of g(dT) = dT - I^2*Rth*R(dT) by "
            "bisection on [0, %.0f C] - a sign change is the existence proof, so a "
            "non-existent steady state comes back as THERMAL_RUNAWAY instead of an "
            "iterate. dlnR/dlnrho is MEASURED at the solution (central difference), "
            "not assumed: 1/2 in a skin-dominated conductor, 1 when the wall is "
            "thinner than the skin depth"
            % ("1-D Cartesian slab" if geom["kind"] == "sheet"
               else "Bessel / Riccati walk in the annulus", MELT_CU_C))

    basis = ("rho20 = 1.724e-8 ohm*m, alpha = 0.00393 /K (IACS copper, linear "
             "0..100 C), delta = sqrt(rho/(pi*f*mu)); T is exact for the given "
             "Rth and the given law - the Rth and the law are the caller's")

    precision = (
        "the root is located to %.0e K; the number inherits +-10..20 %% from the "
        "caller's lump Rth, and grows optimistic above ~100 C where alpha(T) is "
        "no longer 0.00393" % (tol_c,))

    assumptions = {
        "rth_source": "caller-supplied single lump, not derived here",
        "rho_law": "linear in T, one alpha, no curve",
        "shape": shape,
        "rth_c_per_w": _sig(rth),
        "t_ambient_c": _sig(ta),
        "alpha_per_k": _sig(alpha),
        "rho20_ohm_m": _sig(rho20),
        "roughness_in_the_answer": bool(rq_m),
    }
    if multi:
        assumptions["frequency"] = ("spectrum of %d term(s), RMS magnitudes only, "
                                    "no phase, one shared Rth" % len(terms))
        assumptions["current_a"] = _sig(i_rms)
    else:
        assumptions["frequency"] = "one frequency, one conductor (no proximity)"
        assumptions["current_a"] = _sig(i)

    # result.ok() merges `extra` at the top level, so an extra key named
    # "assumptions" would REPLACE the assumptions dict (that is how rth_source
    # disappeared once). Everything that must stay in assumptions is put there,
    # and only checks/value travel through extra.
    return ok(_sig(t_final), "C", model, basis, precision, assumptions,
              extra={"checks": checks, "value": value})
