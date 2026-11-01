"""Power-inductor sizing for the three non-isolated switching topologies.

Source of the relations (verified against the skill text and every textbook
derivation in the same form):

    boost-inductor-circuit-calc / SKILL.md  (CCM Boost, the reference case)
    D = 1 - Vin/Vout                       ideal duty
    dI = r * Iavg                          ripple is a DESIGN CHOICE, not a given
    L  = Vin * D / (dI * fs)               volt-seconds across the inductor
    Ipk = Iavg + dI/2 ,  Ivalley = Iavg - dI/2   (valley < 0 -> the converter
                                           would be in DCM, i.e. CCM was
                                           assumed where it does not hold)
    Irms = sqrt(Iavg^2 + dI^2/12)           triangular waveform
    Iavg = Pout / (eta * Vin)              when the load is given as power

What this module still does NOT do:

  * saturation is judged against a REAL vendor material only in select_core,
    and only at the temperatures that material actually publishes (25 C / 100 C);
    inductor_ccm still just reports the peak/valley/RMS the winding must survive
    and, only if you supply a saturation current, whether that part is enough;
  * no CONTROL loop, no soft-start, no slope compensation, no stability;
  * efficiency is one number, not a curve, and is assumed constant.

What it does, in four entry points:

  inductor_ccm      the CCM design point (duty, L, Ipk/Ivalley/Irms), plus the
                    winding loss if you supply `dcr_ohm`;
  duty_dcm          the DCM steady state (a different duty, a second interval
                    where the diode carries the inductor current), refused
                    outright when the load is really in CCM;
  core_loss_steinmetz
                    Steinmetz P_v = k*f^alpha*B^beta with the coefficients YOU
                    supply and the unit convention NAMED, because the published
                    fits differ by ~10^12 in k purely through units,
                    and a Steinmetz fit outside its (f, B) window is a guess;
  winding_ac_resistance
                    Dowell's 1-D F_R = R_ac/R_dc for a layer stack (foil or round
                    wire), so the DC resistance an ohmmeter reads can be turned
                    into the resistance the switching frequency sees;
  select_core       pick a real core from data/magnetic_materials.json for an
                    (L, Ipk) requirement: turns from AL, turns from the flux
                    limit, the peak flux density, the gap it would need, the
                    core loss at the one measured reference point (extrapolated
                    only if YOU give the exponents) and the winding window - and
                    a named reason for every part that is rejected;
  magnetic_data     read that table back, with the URL behind every row.
"""
import json
import math
import os

from .result import ok, err

TOPOLOGIES = ("boost", "buck", "buck_boost")

# ripple ratio is a design choice; 0.2..0.4 is the usual CCM band
RIPPLE_TYPICAL_MIN = 0.20
RIPPLE_TYPICAL_MAX = 0.40


def _f(name, v):
    try:
        x = float(v)
    except (TypeError, ValueError):
        raise ValueError(name + " must be numeric")
    if not math.isfinite(x):
        raise ValueError(name + " must be finite")
    return x


def _fmt(x):
    """Readable float: plain decimals in the useful range, 6 significant digits
    outside it, because Steinmetz densities span 1e-3 .. 1e6."""
    if x == 0:
        return 0.0
    if 1e-3 <= abs(x) < 1e6:
        return round(x, 6)
    return float("%.6g" % x)


def inductor_ccm(topology, vin_v, vout_v, f_sw_hz, ripple=0.30,
                 iout_a=None, pout_w=None, efficiency=1.0, isat_a=None,
                 sat_margin=0.20, dcr_ohm=None):
    """Inductance, duty, peak / valley / RMS current for a CCM design point.

    `dcr_ohm` (optional) turns the RMS current into the winding's I^2R loss. It is
    the WINDING only: core loss, switch loss and the AC (skin/proximity) part of
    the winding resistance are not in it, and the duty is computed without DCR's
    voltage drop, so the loss is a lower bound and the duty a little optimistic.
    """
    top = str(topology or "").strip().lower().replace("-", "_")
    if top not in TOPOLOGIES:
        return err("topology must be one of " + ", ".join(TOPOLOGIES),
                   "ARG_INVALID",
                   hint="only the three non-isolated CCM topologies are modelled")
    try:
        vin = _f("vin_v", vin_v)
        vout = _f("vout_v", vout_v)
        fs = _f("f_sw_hz", f_sw_hz)
        r = _f("ripple", ripple)
        eta = _f("efficiency", efficiency)
        margin = _f("sat_margin", sat_margin)
    except ValueError as exc:
        return err(str(exc), "ARG_INVALID")

    if vin <= 0 or vout <= 0 or fs <= 0:
        return err("vin_v, vout_v and f_sw_hz must all be > 0", "ARG_RANGE")
    if not (0 < eta <= 1):
        return err("efficiency must be in (0, 1]", "ARG_RANGE")
    if r <= 0:
        return err("ripple must be > 0", "ARG_RANGE")
    if r >= 2:
        return err("ripple ratio %g cannot be CCM" % r, "ARG_RANGE",
                   hint="a valley of Iavg - dI/2 needs r < 2; r = 2 is exactly "
                        "the CCM/DCM boundary, and above it the converter is in "
                        "DCM where D = 1 - Vin/Vout no longer holds")
    if margin < 0:
        return err("sat_margin must be >= 0", "ARG_RANGE")

    # topology feasibility does not depend on the load, so it is checked first
    if top == "boost":
        duty = 1.0 - eta * vin / vout
        if duty <= 0 or duty >= 1:
            return err("boost needs Vout > Vin / efficiency (duty came out %g)"
                       % round(duty, 4), "ARG_RANGE",
                       hint="with Vout <= Vin a boost cannot regulate; use buck")
    elif top == "buck":
        duty = vout / (eta * vin)
        if duty <= 0 or duty >= 1:
            return err("buck needs Vout < efficiency * Vin (duty came out %g)"
                       % round(duty, 4), "ARG_RANGE",
                       hint="with Vout >= Vin a buck cannot regulate; use boost")
    else:
        duty = vout / (vout + eta * vin)
        if not (0 < duty < 1):
            return err("buck-boost duty came out %g" % round(duty, 4), "ARG_RANGE")

    # load: either a current or a power, never both silently
    if iout_a is not None and pout_w is not None:
        return err("give iout_a or pout_w, not both", "ARG_INVALID",
                   hint="the two imply different assumptions about where the "
                        "efficiency loss sits; pick the one you actually know")
    if iout_a is None and pout_w is None:
        return err("one of iout_a / pout_w is required", "ARG_MISSING",
                   hint="without a load there is no ripple current, hence no L")
    if iout_a is not None:
        try:
            iout = _f("iout_a", iout_a)
        except ValueError as exc:
            return err(str(exc), "ARG_INVALID")
        if iout <= 0:
            return err("iout_a must be > 0", "ARG_RANGE")
        pout = vout * iout
    else:
        try:
            pout = _f("pout_w", pout_w)
        except ValueError as exc:
            return err(str(exc), "ARG_INVALID")
        if pout <= 0:
            return err("pout_w must be > 0", "ARG_RANGE")
        iout = pout / vout

    pin = pout / eta
    iin = pin / vin

    if top == "boost":
        i_l_avg = iin
        v_off = vout - vin
        l_h = vin * duty / (r * i_l_avg * fs)
    elif top == "buck":
        i_l_avg = iout
        v_off = vin - vout
        l_h = v_off * duty / (r * i_l_avg * fs)
    else:  # buck_boost, inverting, |Vout| reported
        # The winding carries the INPUT current during D*T and the OUTPUT current
        # during (1-D)*T, so its average is Iout/(1-D) = Iin/D - not Iin. Up to
        # 0.0.17 this branch used Iin, which under-sized L by (1-D) and overstated
        # the ripple ratio it was asked to hit. Corrected in 0.0.18.
        i_l_avg = iout / (1.0 - duty)
        v_off = vout
        l_h = vin * duty / (r * i_l_avg * fs)

    d_i = r * i_l_avg
    i_pk = i_l_avg + d_i / 2.0
    i_valley = i_l_avg - d_i / 2.0
    i_rms = math.sqrt(i_l_avg ** 2 + d_i ** 2 / 12.0)

    winding = None
    if dcr_ohm is not None:
        try:
            dcr = _f("dcr_ohm", dcr_ohm)
        except ValueError as exc:
            return err(str(exc), "ARG_INVALID")
        if dcr < 0:
            return err("dcr_ohm must be >= 0", "ARG_RANGE")
        pcu = i_rms ** 2 * dcr
        winding = {
            "dcr_ohm": round(dcr, 6),
            "rms_current_a": round(i_rms, 4),
            "winding_loss_w": round(pcu, 4),
            "winding_loss_over_pout_pct": round(pcu / pout * 100.0, 2),
            "efficiency_ceiling_if_dcr_were_the_only_loss": round(
                pout / (pout + pcu), 5),
            "dc_only": ("R_AC (skin + proximity) is not in this: at the switching "
                        "frequency the winding's resistance is higher than the meter "
                        "reading, so the real loss is larger and the ceiling lower"),
            "duty_effect": ("the duty here is the ideal one; DCR drops voltage in "
                            "the on-interval, so a real converter runs a few percent "
                            "of duty HIGHER and the current a little higher again"),
        }
        if eta < 1.0:
            winding["double_counting"] = (
                "efficiency=%.4g was supplied AND the DCR loss is computed here. "
                "The efficiency number is applied to the input side when sizing L, "
                "so it already contains (some of) this loss - do not add the two "
                "percentages together, pick one model" % eta)

    # where CCM ends: the load at which the valley reaches zero
    if top == "buck":
        iout_min_ccm = d_i / 2.0
    elif top == "boost":
        iout_min_ccm = eta * vin * (d_i / 2.0) / vout
    else:
        # same "the valley reaches zero" condition, expressed through the winding
        # current this topology actually has: Iout = (1-D) * I_L,avg
        iout_min_ccm = (1.0 - duty) * (d_i / 2.0)

    out = {
        "topology": top,
        "duty": round(duty, 5),
        "inductor_uh": round(l_h * 1e6, 3),
        "inductor_h": l_h,
        "ripple_ratio": r,
        "ripple_current_a": round(d_i, 4),
        "inductor_avg_a": round(i_l_avg, 4),
        "inductor_peak_a": round(i_pk, 4),
        "inductor_valley_a": round(i_valley, 4),
        "inductor_rms_a": round(i_rms, 4),
        "input_current_avg_a": round(iin, 4),
        "output_current_a": round(iout, 4),
        "output_power_w": round(pout, 4),
        "volts_off_v": round(v_off, 4),
        "ccm_min_output_a": round(iout_min_ccm, 4),
        "ripple_in_typical_band": RIPPLE_TYPICAL_MIN <= r <= RIPPLE_TYPICAL_MAX,
        "saturation": None,
        "winding": winding,
    }

    if isat_a is None:
        out["saturation"] = {
            "verdict": "undetermined",
            "what_is_missing": "the part's saturation current (Isat) and its "
                               "current derating at the operating temperature",
            "why": "a peak current alone says nothing about whether the core "
                   "survives it; every candidate part has its own Isat curve",
            "cheapest_next_check": "L0: read Isat off the candidate part's "
                                   "datasheet at the hottest ambient",
        }
    else:
        try:
            isat = _f("isat_a", isat_a)
        except ValueError as exc:
            return err(str(exc), "ARG_INVALID")
        need = i_pk * (1.0 + margin)
        out["saturation"] = {
            "isat_a": round(isat, 4),
            "required_with_margin_a": round(need, 4),
            "margin_used": margin,
            "verdict": "pass" if isat >= need else "fail",
        }

    extra = {
        "design_point": {
            "vin_v": vin, "vout_v": vout, "f_sw_hz": fs,
            "efficiency": eta, "ripple_ratio": r,
        },
        "answers_only_with_the_load": [
            "ripple_current_a", "inductor_uh", "inductor_peak_a",
            "inductor_rms_a", "ccm_min_output_a",
        ],
    }
    if top == "buck_boost":
        extra["note"] = ("inverting topology: the reported |Vout| is the "
                         "magnitude, the real output is negative")
    return ok(
        value=out,
        unit="design_point",
        model="ccm_inductor_volt_seconds",
        basis=("boost: D = 1 - eta*Vin/Vout, L = Vin*D/(r*Iin*fs); "
               "buck: D = Vout/(eta*Vin), L = (Vin-Vout)*D/(r*Iout*fs); "
               "buck-boost: D = Vout/(Vout+eta*Vin), L = Vin*D/(r*Iin*fs); "
               "Irms = sqrt(Iavg^2 + dI^2/12)"),
        precision=("closed form on ideal switches and a constant efficiency; "
                   "dead time, diode drop and core loss are outside the model, so "
                   "the real duty is a few percent higher and the real ripple "
                   "slightly larger. DCR enters only as a loss term (optional) - "
                   "it does not change the duty here"),
        assumptions={
            "conduction_mode": "CCM (valley > 0 by construction when r < 2)",
            "efficiency": "single constant number, applied to the input side",
            "switch_drop": 0.0,
            "core": "no core, no saturation model unless isat_a is supplied",
        },
        extra=extra,
    )


# ---------------------------------------------------------------------------
# DCM steady state
# ---------------------------------------------------------------------------
def duty_dcm(topology, vin_v, vout_v, f_sw_hz, l_h, iout_a=None, pout_w=None):
    """Duty and currents when the converter is in discontinuous conduction.

    DCM is not CCM with a smaller ripple. There are three intervals per period
    (switch on, diode on, nothing on) and the output relation changes completely:

        boost       D = sqrt(K * M * (M - 1))
        buck        D = M * sqrt(K / (1 - M))
        buck-boost  D = M * sqrt(K)                 M = Vout/Vin, K = 2*L*fs/R

    with K the normalised load parameter and R = Vout/Iout. Seconds, volts, ohms,
    henries - no ripple ratio anywhere: in DCM the ripple is not a design choice,
    it is whatever the load and L make it (dI = 2*I_L,avg by construction).

    The answer is REFUSED when the load turns out to be above the CCM boundary,
    because then the formula is answering a question that is not the one asked:
    K_crit is (M-1)/M^3 for boost, 1-M for buck, (1-D)^2 for buck-boost, and
    above it the converter is in CCM where the duty is the other formula. That
    boundary is checked against inductor_ccm in the tests, so the two entry
    points cannot drift apart silently.

    Ideal (lossless) voltages throughout: efficiency is not applied, because in
    DCM the duty also depends on the diode drop and the winding resistance in a
    way one constant efficiency number cannot express. At the CCM boundary with
    efficiency=1 this agrees exactly with inductor_ccm.
    """
    top = str(topology or "").strip().lower().replace("-", "_")
    if top not in TOPOLOGIES:
        return err("topology must be one of " + ", ".join(TOPOLOGIES),
                   "ARG_INVALID",
                   hint="only the three non-isolated topologies are modelled")
    try:
        vin = _f("vin_v", vin_v)
        vout = _f("vout_v", vout_v)
        fs = _f("f_sw_hz", f_sw_hz)
        l = _f("l_h", l_h)
    except ValueError as exc:
        return err(str(exc), "ARG_INVALID")
    if vin <= 0 or vout <= 0 or fs <= 0 or l <= 0:
        return err("vin_v, vout_v, f_sw_hz and l_h must all be > 0", "ARG_RANGE")

    # load, exactly one of the two
    if iout_a is not None and pout_w is not None:
        return err("give iout_a or pout_w, not both", "ARG_INVALID")
    if iout_a is None and pout_w is None:
        return err("one of iout_a / pout_w is required", "ARG_MISSING",
                   hint="in DCM the duty depends on the load: R = Vout/Iout")
    try:
        if iout_a is not None:
            iout = _f("iout_a", iout_a)
            if iout <= 0:
                return err("iout_a must be > 0", "ARG_RANGE")
            pout = vout * iout
        else:
            pout = _f("pout_w", pout_w)
            if pout <= 0:
                return err("pout_w must be > 0", "ARG_RANGE")
            iout = pout / vout
    except ValueError as exc:
        return err(str(exc), "ARG_INVALID")

    m = vout / vin          # conversion ratio magnitude
    r_load = vout / iout
    k = 2.0 * l * fs / r_load

    if top == "boost":
        if m <= 1.0:
            return err("boost needs Vout > Vin (M came out %g)" % round(m, 4),
                       "ARG_RANGE", hint="with Vout <= Vin a boost cannot regulate")
        d_ccm = 1.0 - 1.0 / m
        k_crit = (m - 1.0) / m ** 3
        d2 = None            # filled after d is known
        i_l_avg = m * iout   # Pout/(Vin) with ideal efficiency
    elif top == "buck":
        if m >= 1.0:
            return err("buck needs Vout < Vin (M came out %g)" % round(m, 4),
                       "ARG_RANGE", hint="with Vout >= Vin a buck cannot regulate")
        d_ccm = m
        k_crit = 1.0 - m
        i_l_avg = iout
    else:
        d_ccm = m / (1.0 + m)
        k_crit = (1.0 - d_ccm) ** 2
        i_l_avg = iout / (1.0 - d_ccm)

    # the load at which the valley reaches zero for THIS l - derived from the same
    # "dI at the boundary equals twice the average" condition inductor_ccm uses
    if top == "boost":
        iout_b = vin ** 2 * d_ccm / (2.0 * l * fs * vout)
    elif top == "buck":
        iout_b = (vin - vout) * d_ccm / (2.0 * l * fs)
    else:
        iout_b = (1.0 - d_ccm) * vin * d_ccm / (2.0 * l * fs)

    if k >= k_crit:
        return err(
            "this load is in CCM: K = %.4g >= K_crit = %.4g, so the DCM duty does "
            "not apply" % (k, k_crit), "MODE_IS_CCM",
            hint=("either raise L (smaller K), lower the load below %.4g A, or ask "
                  "inductor_ccm for this operating point - it is the CCM answer and "
                  "its duty is %.4g at efficiency 1" % (iout_b, d_ccm)),
            extra={"K": round(k, 6), "K_crit": round(k_crit, 6),
                   "M": round(m, 6), "ccm_duty_at_efficiency_1": round(d_ccm, 6),
                   "load_at_ccm_boundary_a": round(iout_b, 6)})

    if top == "boost":
        d = math.sqrt(k * m * (m - 1.0))
        d2 = d / (m - 1.0)                 # diode carries the winding for d2
        v_on = vin
    elif top == "buck":
        d = m * math.sqrt(k / (1.0 - m))
        d2 = d * (1.0 - m) / m
        v_on = vin - vout
    else:
        d = m * math.sqrt(k)
        d2 = d / m
        v_on = vin

    i_pk = v_on * d / (l * fs)
    i_rms = i_pk * math.sqrt((d + d2) / 3.0)

    out = {
        "topology": top,
        "mode": "DCM",
        "duty": round(d, 6),
        "duty_diode": round(d2, 6),
        "duty_idle": round(max(0.0, 1.0 - d - d2), 6),
        "duty_total": round(d + d2, 6),
        "conversion_ratio_M": round(m, 6),
        "K": round(k, 6),
        "K_crit": round(k_crit, 6),
        "ccm_duty_at_same_voltages": round(d_ccm, 6),
        "load_at_ccm_boundary_a": round(iout_b, 6),
        "inductor_peak_a": round(i_pk, 4),
        "inductor_avg_implied_by_load_a": round(i_l_avg, 4),
        "inductor_avg_implied_by_waveform_a": round(i_pk * (d + d2) / 2.0, 4),
        "inductor_rms_a": round(i_rms, 4),
        "input_current_avg_a": round(pout / vin, 4),
        "output_current_a": round(iout, 4),
        "output_power_w": round(pout, 4),
    }
    out["duty_is_dcm_not_ccm_by_pct"] = round((d - d_ccm) / d_ccm * 100.0, 2)
    return ok(
        value=out,
        unit="design_point",
        model="dcm_inductor_steady_state",
        basis=("boost D = sqrt(K*M*(M-1)); buck D = M*sqrt(K/(1-M)); "
               "buck-boost D = M*sqrt(K); M = Vout/Vin, K = 2*L*fs/R, R = Vout/Iout; "
               "Ipk = V_on*D/(L*fs); Irms = Ipk*sqrt((D+D2)/3)"),
        precision=("closed form on the ideal switches, ideal (lossless) voltages and "
                   "one constant L. Not modelled: diode forward drop and its effect on "
                   "D2, winding resistance, core loss, and CCM/DCM chatter near the "
                   "boundary. At K just below K_crit the duty is very sensitive to "
                   "all of them"),
        assumptions={
            "conduction_mode": "DCM (three intervals, idle interval > 0)",
            "efficiency": "1.0 by construction - a constant efficiency number cannot "
                          "express the DCM duty shift",
            "ripple": "not an input: dI = 2*I_L,avg is set by the mode",
            "inductor": "constant L, no saturation roll-off",
        },
        extra={
            "cross_check": {
                "two_ways_to_get_the_inductor_average": {
                    "by_load": round(i_l_avg, 4),
                    "by_waveform": round(i_pk * (d + d2) / 2.0, 4),
                    "note": "these are computed from different data (load and output "
                            "power vs duty and peak current) and must agree; a "
                            "disagreement means the topology relation is wrong",
                },
            },
            "boundary": {
                "K": round(k, 6),
                "K_crit": round(k_crit, 6),
                "headroom": round((k_crit - k) / k_crit, 4),
                "note": "headroom is how far (relatively) the load sits below the CCM "
                        "boundary; near 0 the answer is unusable in hardware even "
                        "though the arithmetic is right",
            },
            "how_to_go_back_to_ccm": "increase L, or reduce the load",
        },
    )


# ---------------------------------------------------------------------------
# Core loss (Steinmetz), with the coefficients and the units coming from the caller
# ---------------------------------------------------------------------------
STEINMETZ_UNITS = {
    # convention id -> (f unit, B unit, what k is in, scale from the printed P_v
    # to W/m^3). Every published fit prints one of these; the k values differ by
    # ~10^12 between them, so the convention is part of the question, not a detail.
    "si": (1.0, 1.0, "W/m^3 per (Hz^alpha * T^beta)", 1.0),
    "khz_mt": (1e3, 1e-3, "mW/cm^3 per (kHz^alpha * mT^beta)", 1e3),
    "khz_gauss": (1e3, 1e-4, "mW/cm^3 per (kHz^alpha * Gauss^beta)", 1e3),
}


# ---------------------------------------------------------------------------
# iGSE: the same Steinmetz triple, but the flux does not have to be a sine
# ---------------------------------------------------------------------------
# Li, J., Abdallah, T., Sullivan, C. R., "Improved calculation of core loss with
# nonsinusoidal waveforms", IEEE Industry Applications Conference 2001 (IEEE
# Xplore document 955931) - the definition of the improved form:
#
#     P_v = (1/T) * Int_0^T k_i |dB/dt|^alpha * (dB_pp)^(beta - alpha) dt
#
# dB_pp is the PEAK-TO-PEAK swing (max B - min B over one period), not the
# amplitude: that is the paper's definition, and reading it as an amplitude moves
# the answer by 2^(beta-alpha) - 2.5x at the usual beta-alpha = 1.3. The
# normalization k_i is fixed by the one requirement that makes the form
# "improved" rather than "different": a sine must give back the plain Steinmetz
# answer the caller's k/alpha/beta were fitted for. Solving that identity,
#
#     sine:  dB/dt = 2*pi*f*B_p*cos(2*pi*f*t),  dB_pp = 2*B_p,
#            (1/T) Int |cos|^alpha dt = I_alpha/(2*pi)
#     -> P = k_i * (2*pi)^(alpha-1) * 2^(beta-alpha) * I_alpha * f^alpha * B_p^beta
#     -> k_i = k / [ (2*pi)^(alpha-1) * 2^(beta-alpha) * I_alpha ]
#        I_alpha = Int_0^2pi |cos|^alpha d_theta
#                = 2*sqrt(pi)*Gamma((alpha+1)/2)/Gamma((alpha+2)/2)
#
# k_i is DERIVED here and then measured against a direct numerical integral of
# B(t) (tests/test_core_loss_igse.py); it is not copied from a table, because the
# paper's own constants are printed for its own example and the transcription was
# not available to verify. The identity above is checkable, which is better.
def _i_alpha(alpha):
    """Int_0^2pi |cos|^alpha d_theta, in log space so a large alpha cannot overflow."""
    return math.exp(math.log(2.0) + 0.5 * math.log(math.pi)
                    + math.lgamma(0.5 * (alpha + 1.0))
                    - math.lgamma(0.5 * (alpha + 2.0)))

def _igse_ki(k, alpha, beta):
    """The normalization that makes iGSE reduce to Steinmetz on a sine."""
    return k / ((2.0 * math.pi) ** (alpha - 1.0) * 2.0 ** (beta - alpha)
                * _i_alpha(alpha))

def _igse_span(segs):
    """Peak-to-peak flux swing of a closed piecewise trajectory (start at 0)."""
    b = lo = hi = 0.0
    for db, _dtf in segs:
        b += db
        if b < lo:
            lo = b
        if b > hi:
            hi = b
    return hi - lo

def _igse_pv(k, alpha, beta, f_native, segs):
    """iGSE over one period for a closed trajectory given as (dB, dt_fraction) rows.

    Everything is in the caller's own unit convention: f in the unit k was fitted
    for and dB in the unit k was fitted for, so k_i carries k's units and no
    conversion leaks into the integral.
    """
    dbpp = _igse_span(segs)
    acc = 0.0
    for db, dtf in segs:
        acc += (abs(f_native * db / dtf)) ** alpha * dtf
    return _igse_ki(k, alpha, beta) * dbpp ** (beta - alpha) * acc, dbpp

def _igse_shape_ratio(alpha, segs):
    """P_v(iGSE) / P_v(sine) at the SAME peak-to-peak swing, from the shape alone.

    dB and f both cancel out of that ratio (the two answers carry the same
    dB_pp^beta and the same f^alpha), so any consistent dB unit works. What is left
    is a pure number that answers one question: by how much does the plain
    Steinmetz equation - which assumes a sine - miss this waveform?
    """
    dbpp = _igse_span(segs)
    if dbpp <= 0:
        return None
    acc = 0.0
    for db, dtf in segs:
        acc += (abs(db / dtf)) ** alpha * dtf
    return acc / (dbpp ** alpha * (2.0 * math.pi) ** (alpha - 1.0)
                  * 2.0 ** (-alpha) * _i_alpha(alpha))

_WAVEFORMS = {
    # canonical id -> the spelling(s) accepted for it
    "sine": ("sine", "sin", "sinusoid", "sinusoidal"),
    "triangle": ("triangle", "tri", "triangular", "symmetric_triangle"),
    "piecewise": ("piecewise", "piecewise_linear", "segments", "arbitrary"),
}

def _waveform_canon(name):
    """Canonical waveform id, or None when the name is not one we model."""
    key = str(name if name is not None else "sine").strip().lower()
    for sep in ("-", " ", "/"):
        key = key.replace(sep, "_")
    for canon, spellings in _WAVEFORMS.items():
        if key in spellings:
            return canon
    return None


def core_loss_steinmetz(f_hz, b_peak_t, k, alpha, beta, units="si", ve_mm3=None,
                        waveform="sine", duty=None, segments=None):
    """Best-known core loss for ONE period of a flux trajectory, in the caller's units.

    waveform="sine" (default) is the plain Steinmetz fit the caller supplied:

        P_v = k * f^alpha * B_peak^beta

    waveform="triangle" or "piecewise" evaluates the iGSE on the trajectory you
    declare instead of pretending it is a sine:

        P_v = (1/T) * Int k_i * |dB/dt|^alpha * dB_pp^(beta-alpha) dt

    `k`, `alpha`, `beta` are the MATERIAL's fit and are never invented here: a
    Steinmetz fit is a curve fit valid inside a (frequency, flux) window, and the
    same material fitted by two vendors gives two different triples. Say which
    convention the triple came from:

        si         f in Hz,  B in T,     k in W/m^3/(Hz^a T^b)
        khz_mt     f in kHz, B in mT,    k in mW/cm^3/(kHz^a mT^b)   (Ferroxcube style)
        khz_gauss  f in kHz, B in Gauss, k in mW/cm^3/(kHz^a G^b)    (Magnetics style)

    The answer always carries P_v in BOTH W/m^3 and mW/cm^3 so a unit mistake
    shows up as an absurd magnitude instead of a plausible wrong number.

    `b_peak_t` is the AC amplitude (half of the peak-to-peak swing), which is what
    every published fit uses. For waveform="piecewise" it may be omitted (the
    segments carry the swing) or given as a check, in which case it must agree
    with 2*dB_pp; a disagreement is refused rather than resolved silently.

    `duty` is the rising fraction of the period for a triangle (default 0.5, the
    symmetric case). `segments` is a closed list of [dB_t, dt_fraction] rows whose
    dt_fractions sum to 1: that is the waveform, so nothing is guessed about it.

    Every non-sine answer also prints what the plain Steinmetz equation would have
    said at the same amplitude, the ratio, and which way the shortcut errs.

    Not modelled: DC bias (a gapped or DC-biased core loses MORE than this at the
    same AC amplitude - a revised-Steinmetz bias term is not implemented),
    temperature dependence of the coefficients, the gap/fringing loss, and the
    narrow-pulse over-prediction that the GSE/WGSE variants exist to fix. Also not
    modelled: how much of the total loss this is - core loss says nothing about
    the winding.
    """
    units = str(units or "si").strip().lower().replace("-", "_").replace("/", "_")
    if units not in STEINMETZ_UNITS:
        return err("units must be one of " + ", ".join(sorted(STEINMETZ_UNITS)),
                   "ARG_INVALID",
                   hint="the k in a Steinmetz fit is meaningless without its unit "
                        "convention: si (Hz/T), khz_mt (kHz/mT), khz_gauss (kHz/G)")
    wf = _waveform_canon(waveform)
    if wf is None:
        return err("waveform must be one of sine, triangle, piecewise (got %r)"
                   % (waveform,), "ARG_INVALID",
                   hint="sine keeps the plain Steinmetz closed form; triangle takes "
                        "duty (rising fraction, default 0.5); piecewise takes "
                        "segments=[[dB_t, dt_fraction], ...] summing to 1 over a "
                        "closed period")
    if wf != "triangle" and duty is not None:
        return err("duty only applies to waveform='triangle' (got waveform=%r)"
                   % wf, "ARG_INVALID",
                   hint="piecewise carries its own timing in the dt_fraction column; "
                        "a sine has no duty. Ignoring it would answer a different "
                        "question than the one asked")
    if wf != "piecewise" and segments is not None:
        return err("segments only apply to waveform='piecewise' (got waveform=%r)"
                   % wf, "ARG_INVALID",
                   hint="a triangle is two segments with the duty you named; if the "
                        "trajectory is the real question, say waveform='piecewise'")
    try:
        f = _f("f_hz", f_hz)
        kk = _f("k", k)
        a = _f("alpha", alpha)
        bb = _f("beta", beta)
        b = None if (wf == "piecewise" and b_peak_t is None) else _f("b_peak_t", b_peak_t)
    except ValueError as exc:
        return err(str(exc), "ARG_INVALID")
    if f <= 0:
        return err("f_hz must be > 0", "ARG_RANGE")
    if kk <= 0:
        return err("k must be > 0 (a Steinmetz coefficient is never zero or "
                   "negative)", "ARG_RANGE")
    if a <= 0 or bb <= 0:
        return err("alpha and beta are the fit's exponents and must be > 0",
                   "ARG_RANGE")

    f_unit, b_unit, k_unit, scale = STEINMETZ_UNITS[units]
    f_native = f / f_unit

    if wf == "sine":
        if b <= 0:
            return err("b_peak_t must be > 0", "ARG_RANGE",
                       hint="B_peak is the AC amplitude in tesla; a DC-only core has "
                            "no Steinmetz loss")
        pv_native = kk * f_native ** a * (b / b_unit) ** bb
        dbpp_t = 2.0 * b
        rows_t = None
    else:
        if wf == "triangle":
            if b <= 0:
                return err("b_peak_t must be > 0", "ARG_RANGE",
                           hint="B_peak is the AC amplitude in tesla; the flux swings "
                                "between -B_peak and +B_peak")
            if duty is None:
                d = 0.5
            else:
                try:
                    d = _f("duty", duty)
                except ValueError as exc:
                    return err(str(exc), "ARG_INVALID")
                if not (0.0 < d < 1.0):
                    return err("duty must be in (0, 1) - it is the fraction of the "
                               "period spent rising", "ARG_RANGE",
                               hint="duty=0 or duty=1 means one infinite slope, which "
                                    "is a square wave; give it as two piecewise rows "
                                    "with the finite edge time")
            rows_t = [(2.0 * b, d), (-2.0 * b, 1.0 - d)]
        else:
            if not isinstance(segments, (list, tuple)) or len(segments) < 2:
                return err("waveform='piecewise' needs segments=[[dB_t, "
                           "dt_fraction], ...] with at least 2 rows", "ARG_INVALID",
                           hint="dB_t is the flux CHANGE over the row in tesla "
                                "(signed), dt_fraction is that row's share of the "
                                "switching period; the rows must sum to 1 in time "
                                "and to 0 in flux")
            rows_t = []
            for i, row in enumerate(segments):
                if not isinstance(row, (list, tuple)) or len(row) != 2:
                    return err("segments[%d] must be [dB_t, dt_fraction]" % i,
                               "ARG_INVALID")
                try:
                    db = _f("segments[%d][0]" % i, row[0])
                    dtf = _f("segments[%d][1]" % i, row[1])
                except ValueError as exc:
                    return err(str(exc), "ARG_INVALID")
                if dtf <= 0:
                    return err("segments[%d][1] (dt_fraction) must be > 0" % i,
                               "ARG_RANGE",
                               hint="a zero-length row has no defined dB/dt")
                rows_t.append((db, dtf))
            tsum = 0.0
            for _db, dtf in rows_t:
                tsum += dtf
            if abs(tsum - 1.0) > 1e-6:
                return err("the dt_fractions must sum to 1 (got %r)" % tsum,
                           "ARG_RANGE",
                           hint="the second number in each row is a FRACTION of the "
                                "switching period, not a time in seconds")
            net = 0.0
            span = 0.0
            for db, _dtf in rows_t:
                net += db
                span += abs(db)
            if span <= 0:
                return err("the segments carry no flux swing (every dB_t is 0)",
                           "ARG_RANGE")
            if abs(net) > 1e-6 * span:
                return err("the trajectory does not close: the dB_t values sum to "
                           "%r, not 0" % net, "ARG_RANGE",
                           hint="a steady-state period returns to the flux it "
                                "started at; an open trajectory is a transient, not "
                                "a rated operating point")
            dbpp_t = _igse_span(rows_t)
            if dbpp_t <= 0:
                return err("the flux swing is zero (dB_pp = 0)", "ARG_RANGE",
                           hint="no swing, no core loss")
            if b is not None and abs(2.0 * b - dbpp_t) > 1e-6 * max(2.0 * b, dbpp_t):
                return err("b_peak_t (%r) contradicts the segments, whose peak-to-peak "
                           "swing is %r" % (b, dbpp_t), "ARG_INVALID",
                           hint="b_peak_t is HALF the swing; drop it and the segments "
                                "speak for themselves, or correct one of the two")
            b = 0.5 * dbpp_t
        segs_native = [(db / b_unit, dtf) for db, dtf in rows_t]
        pv_native, dbpp_native = _igse_pv(kk, a, bb, f_native, segs_native)
        dbpp_t = dbpp_native * b_unit
        pv_sine_native = kk * f_native ** a * (b / b_unit) ** bb

    pv_wm3 = pv_native * scale
    pv_mwcm3 = pv_wm3 * 1e-3

    out = {
        "P_v_w_m3": _fmt(pv_wm3),
        "P_v_mw_cm3": _fmt(pv_mwcm3),
        "units_used": units,
        "k_means": k_unit,
        "f_hz": _fmt(f),
        "b_peak_t": _fmt(b),
        "b_peak_mt": _fmt(b * 1e3),
        "alpha": a,
        "beta": bb,
    }
    if wf != "sine":
        ratio = pv_native / pv_sine_native
        out["waveform"] = wf
        out["flux_swing_pp_t"] = _fmt(dbpp_t)
        out["segments_used"] = [
            {"dB_t": _fmt(db), "dt_fraction": _fmt(dtf),
             "dB_dt_t_s": _fmt(f * db / dtf)} for db, dtf in rows_t]
        out["igse"] = {
            "k_i": _fmt(_igse_ki(kk, a, bb)),
            "k_i_in": k_unit,
            "i_alpha": _fmt(_i_alpha(a)),
            "dB_pp_t": _fmt(dbpp_t),
            "k_i_from": "derived so that a sine reduces to P_v = k*f^alpha*B_peak^beta",
        }
        out["P_v_sine_equiv_w_m3"] = _fmt(pv_sine_native * scale)
        out["ratio_to_sine"] = _fmt(ratio)
        out["sine_equiv_is"] = ("underestimate" if ratio > 1.0 + 1e-12 else
                                "overestimate" if ratio < 1.0 - 1e-12 else "equal")
        if wf == "triangle":
            out["duty"] = _fmt(rows_t[0][1])
    if ve_mm3 is not None:
        try:
            ve = _f("ve_mm3", ve_mm3)
        except ValueError as exc:
            return err(str(exc), "ARG_INVALID")
        if ve <= 0:
            return err("ve_mm3 must be > 0", "ARG_RANGE")
        out["ve_mm3"] = _fmt(ve)
        out["core_loss_w"] = _fmt(pv_wm3 * ve * 1e-9)
        out["core_loss_per_gram_mw"] = None
    if wf == "sine":
        not_modelled = [
            "DC bias / premagnetisation (the same AC amplitude on a biased core loses more)",
            "non-sinusoidal flux (use an iGSE/GSE variant; the plain Steinmetz form is "
            "defined for a sine)",
            "temperature dependence of k, alpha, beta",
            "gap and fringing loss",
            "how this compares with the WINDING loss - that is a separate number",
        ]
    else:
        not_modelled = [
            "DC bias / premagnetisation (iGSE here is a function of the AC swing only; "
            "a revised Steinmetz with a bias term is not implemented)",
            "a flux trajectory other than the one declared (iGSE uses exactly the path "
            "you gave: a DC-biased minor loop, a resonant ring or a partial swing is a "
            "different problem)",
            "temperature dependence of k, alpha, beta",
            "gap and fringing loss",
            "how this compares with the WINDING loss - that is a separate number",
            "narrow-pulse bias: iGSE is known to over-predict for very small duty (the "
            "period-averaged-slope term that GSE/WGSE add is not implemented here), so "
            "treat duty below ~0.05 as directional rather than quantitative",
        ]
    out["not_modelled"] = not_modelled
    out["fit_is_extrapolation_if"] = ("f or B sits outside the window the triple was "
                                      "fitted on; the fit's error grows fast outside it")
    if wf == "sine":
        return ok(out, "power loss density (W/m^3 and mW/cm^3)",
                  "Steinmetz equation P_v = k*f^alpha*B^beta",
                  "P_v = k*f^alpha*B_peak^beta with k, alpha, beta supplied by the "
                  "caller and interpreted in the named unit convention",
                  "as good as the caller's fit inside its own window: typically 10-20% "
                  "there and unbounded outside it. Units are the dominant error source - "
                  "the same triple read in the wrong convention is off by orders of "
                  "magnitude, which is why both unit systems are printed",
                  {"material": "caller-supplied k/alpha/beta for a named convention",
                   "waveform": "sine, B_peak = AC amplitude",
                   "dc_bias": "not modelled",
                   "temperature": "not modelled"},
                  out)
    return ok(out, "power loss density (W/m^3 and mW/cm^3)",
              "iGSE, improved Generalized Steinmetz Equation (Li/Abdallah/Sullivan 2001)",
              "P_v = (1/T)*Int k_i*|dB/dt|^alpha*dB_pp^(beta-alpha) dt over the declared "
              "trajectory, with k_i = k/((2*pi)^(alpha-1)*2^(beta-alpha)*I_alpha) so "
              "that a sine reproduces the caller's own Steinmetz triple exactly",
              "the caller's fit on a sine is the anchor (iGSE reproduces it to 1e-10 in "
              "this implementation, checked against a numerical integral); how far the "
              "waveform correction is from the truth is NOT measured in this repo - it is "
              "model output, and its usual stated validity is inside the fit window for "
              "triangle/trapezoid flux. k_i is derived from the sine identity above and "
              "verified numerically, not transcribed from a paper",
              {"material": "caller-supplied k/alpha/beta for a named convention, same "
                           "triple a sine would use",
               "waveform": wf + " (declared, not inferred); dB_pp = " + repr(_fmt(dbpp_t))
                          + " T",
               "flux_swing": "peak-to-peak (max B - min B), as iGSE defines it - reading "
                             "it as an amplitude is a 2^(beta-alpha) error",
               "dc_bias": "not modelled",
               "temperature": "not modelled"},
              out)

# ---------------------------------------------------------------------------
# Winding AC resistance (Dowell's 1-D method)
# ---------------------------------------------------------------------------
# Dowell, P. L., "Effects of eddy currents in transformer windings",
# Proc. IEE 113(8), Aug 1966, pp. 1387-1394. The closed form below is his
# one-dimensional solution (field varies only through the winding thickness),
# and every layer carries the same current, so the MMF steps by I at every layer.
#
#   Delta = h / delta_skin              foil thickness over skin depth
#   F_R   = Delta * (sinh 2D + sin 2D) / (cosh 2D - cos 2D)
#         + (2/3)(m^2 - 1) * Delta * (sinh D - sin D) / (cosh D + cos D)
#
# Both terms carry the Delta factor: the first is the layer's own field (it is
# the whole answer for m = 1), the second is the proximity field of the other
# layers. Leaving Delta off the second term is a classic transcription error and
# is wrong by up to 2x at high frequency - checked here against an independent
# 1-D solution (see the test file): F_R -> Delta*(2m^2+1)/3 when Delta >> 1,
# which is the surface-impedance sum (Rs/2)*sum_k(|H_k-1|^2 + |H_k|^2).

MU0 = 4e-7 * math.pi

# annealed copper at 20 C. The temperature coefficient is the linear one for
# 0..100 C; it is 0.4 %/K, i.e. bigger than most of the design margins here, so
# it is an input and not a hidden constant.
COPPER_RESISTIVITY_OHM_M = 1.724e-8
COPPER_TEMPCO_PER_K = 0.00393

WINDING_SHAPES = ("foil", "round")


def _dowell_fr(layers, delta):
    """Dowell's F_R, plus the two terms it is the sum of."""
    m = layers
    d = delta
    s2, sn2 = math.sinh(2 * d), math.sin(2 * d)
    c2, cs2 = math.cosh(2 * d), math.cos(2 * d)
    s1, sn1 = math.sinh(d), math.sin(d)
    c1, cs1 = math.cosh(d), math.cos(d)
    own = d * (s2 + sn2) / (c2 - cs2)
    prox = (2.0 / 3.0) * (m * m - 1) * d * (s1 - sn1) / (c1 + cs1)
    return own + prox, own, prox


def winding_ac_resistance(freq_hz, layers, shape="foil", thickness_mm=None,
                          wire_diameter_mm=None, pitch_mm=None,
                          resistivity_ohm_m=None, temperature_c=None,
                          turns=None, mean_length_mm=None,
                          dc_resistance_ohm=None, porosity_corrected=None):
    """AC/DC resistance ratio of a winding layer stack at ONE frequency.

        Delta = h / delta_skin
        F_R   = Delta*(sinh2D+sin2D)/(cosh2D-cos2D)
              + (2/3)(m^2-1)*Delta*(sinhD-sinD)/(coshD+cosD)

    `shape="foil"`: `thickness_mm` is the foil thickness and one layer is one
    turn, so the `layers` count is also the turn count of that stack.

    `shape="round"`: a layer is `width/pitch` round wires of `wire_diameter_mm`.
    The layer is replaced by a solid foil whose thickness carries the same copper
    per unit layer width, `h_eq = (pi/4)*d^2/p`, which makes the DC resistance
    identical by construction. A sparse layer is NOT a solid foil - the published
    porosity correction scales the penetration ratio by sqrt(eta) with
    `eta = h_eq/p` - so `porosity_corrected` decides which one is reported as
    `fr`: None (default) reports BOTH (`fr`, `fr_area_equivalent`,
    `fr_porosity_corrected`) rather than picking one.

    Model limits, all of them real:
      * the field is 1-D through the stack, so one winding block: with an
        interleaved primary/secondary the MMF diagram is not the staircase this
        assumes and you must pass the layer count of ONE block;
      * sinusoid at `freq_hz` only. A triangular ripple has harmonics: sum the
        losses, `P = SUM_k I_k^2 * R(f_k)`, not `I_total^2 * R(f_sw)`;
      * no gap/fringing field (in a gapped inductor that is usually the biggest
        term - Dowell's method cannot see it), no litz strand-level effect, no
        bundle-level skin, no end/lead effects, no self-resonance;
      * temperature enters only through the copper resistivity.
    """
    sh = str(shape or "foil").strip().lower()
    if sh not in WINDING_SHAPES:
        return err("shape must be one of " + ", ".join(WINDING_SHAPES),
                   "ARG_INVALID",
                   hint="foil (thickness_mm, one layer = one turn) or round "
                        "(wire_diameter_mm + pitch_mm)")
    try:
        f = _f("freq_hz", freq_hz)
    except ValueError as exc:
        return err(str(exc), "ARG_INVALID")
    if f <= 0:
        return err("freq_hz must be > 0", "ARG_RANGE",
                   hint="the AC resistance ratio is a function of frequency; "
                        "DC has no skin effect and F_R = 1")
    try:
        m = int(layers)
    except (TypeError, ValueError):
        return err("layers must be an integer", "ARG_INVALID")
    if m < 1:
        return err("layers must be >= 1", "ARG_RANGE")
    if m > 64:
        return err("layers=%d is outside this model" % m, "ARG_RANGE",
                   hint="Dowell's staircase assumes a winding you can count; "
                        "past ~64 layers the 1-D field never penetrates the stack "
                        "anyway and the answer is F_R ~ Delta*(2m^2+1)/3, which you "
                        "can read off directly")

    rho = COPPER_RESISTIVITY_OHM_M
    if resistivity_ohm_m is not None:
        try:
            rho = _f("resistivity_ohm_m", resistivity_ohm_m)
        except ValueError as exc:
            return err(str(exc), "ARG_INVALID")
        if rho <= 0:
            return err("resistivity_ohm_m must be > 0", "ARG_RANGE")
    temp_used = None
    if temperature_c is not None:
        try:
            tc = _f("temperature_c", temperature_c)
        except ValueError as exc:
            return err(str(exc), "ARG_INVALID")
        if tc <= -273:
            return err("temperature_c below absolute zero", "ARG_RANGE")
        if resistivity_ohm_m is not None:
            return err("give resistivity_ohm_m or temperature_c, not both",
                       "ARG_INVALID",
                       hint="temperature_c already implies copper's rho(T); an "
                            "explicit resistivity would silently contradict it")
        rho = COPPER_RESISTIVITY_OHM_M * (1.0 + COPPER_TEMPCO_PER_K * (tc - 20.0))
        if rho <= 0:
            return err("temperature_c=%g gives a non-physical resistivity" % tc,
                       "ARG_RANGE")
        temp_used = tc

    # the two shapes take different geometry; sending both is a mistake, not a
    # degree of freedom, and sending the wrong one is caught below
    if thickness_mm is not None and wire_diameter_mm is not None:
        return err("shape=foil takes thickness_mm; shape=round takes "
                   "wire_diameter_mm (not both)", "ARG_INVALID")

    if sh == "foil":
        try:
            h_mm = _f("thickness_mm", thickness_mm)
        except ValueError as exc:
            return err(str(exc), "ARG_INVALID")
        if h_mm <= 0:
            return err("thickness_mm must be > 0", "ARG_RANGE")
        h_eq_mm = h_mm
        eta = 1.0
        copper_area_mm2 = h_mm * 1.0        # per unit layer width
        extra_geom = {}
    else:
        try:
            d_mm = _f("wire_diameter_mm", wire_diameter_mm)
        except ValueError as exc:
            return err(str(exc), "ARG_INVALID")
        if d_mm <= 0:
            return err("wire_diameter_mm must be > 0", "ARG_RANGE")
        if pitch_mm is None:
            return err("pitch_mm is required for shape=round", "ARG_MISSING",
                       hint="a layer of round wire is defined by the wire and the "
                            "turn pitch; the copper fraction of the layer, and "
                            "therefore the answer, depends on it")
        try:
            p_mm = _f("pitch_mm", pitch_mm)
        except ValueError as exc:
            return err(str(exc), "ARG_INVALID")
        if p_mm <= 0:
            return err("pitch_mm must be > 0", "ARG_RANGE")
        if d_mm > p_mm * (1.0 + 1e-9):
            return err("wire_diameter_mm (%g) exceeds pitch_mm (%g)" % (d_mm, p_mm),
                       "ARG_RANGE",
                       hint="turns cannot overlap; a diameter above the pitch is a "
                            "geometry that does not exist")
        h_eq_mm = (math.pi / 4.0) * d_mm * d_mm / p_mm
        eta = h_eq_mm / p_mm
        copper_area_mm2 = math.pi * d_mm * d_mm / 4.0
        extra_geom = {
            "wire_diameter_mm": round(d_mm, 6),
            "pitch_mm": round(p_mm, 6),
            "copper_fraction_of_layer": round(eta, 5),
        }

    if sh == "foil" and turns is not None:
        try:
            n_t = int(turns)
        except (TypeError, ValueError):
            return err("turns must be an integer", "ARG_INVALID")
        if n_t != m:
            return err("shape=foil with turns=%d but layers=%d" % (n_t, m),
                       "ARG_INVALID",
                       hint="a foil layer IS one turn, so the two counts are the "
                            "same object; sending both differently is a mistake, not "
                            "a degree of freedom")

    # skin depth in the caller's copper (temperature already baked into rho)
    delta_m = math.sqrt(rho / (math.pi * f * MU0))
    delta_um = delta_m * 1e6
    h_m = h_eq_mm * 1e-3
    delta_eq = h_m / delta_m

    fr, own, prox = _dowell_fr(m, delta_eq)
    out = {
        "shape": sh,
        "layers": m,
        "freq_hz": _fmt(f),
        "skin_depth_um": round(delta_um, 4),
        "resistivity_ohm_m": float("%.6g" % rho),
        "equivalent_foil_mm": round(h_eq_mm, 6),
        "delta_eq": round(delta_eq, 5),
        "fr": round(fr, 5),
        "fr_own_field": round(own, 5),
        "fr_proximity": round(prox, 5),
        "fr_terms_add_up": True,
        "r_ac_over_r_dc": round(fr, 5),
        "dc_is_unity": "F_R -> 1 as the frequency falls; there is no DC skin effect",
    }
    out.update(extra_geom)

    if sh == "round":
        delta_porous = delta_eq * math.sqrt(eta) if eta > 0 else delta_eq
        frp, ownp, proxp = _dowell_fr(m, delta_porous)
        out["delta_eq_porosity_corrected"] = round(delta_porous, 5)
        out["fr_porosity_corrected"] = round(frp, 5)
        out["fr_area_equivalent"] = round(fr, 5)
        out["round_wire_bracket"] = [round(min(fr, frp), 5), round(max(fr, frp), 5)]
        out["round_wire_note"] = (
            "the layer is replaced by a solid foil carrying the same copper per unit "
            "layer width (fr_area_equivalent, DC-exact) or by that foil with the "
            "published porosity correction Delta*sqrt(eta) (fr_porosity_corrected); "
            "the two differ by %.1f %% here and the truth for round wire sits between "
            "them for pitch >= 1.5*wire" % (abs(fr - frp) / max(fr, frp) * 100.0))
        if porosity_corrected is True:
            out["fr"] = round(frp, 5)
            out["r_ac_over_r_dc"] = round(frp, 5)
            out["fr_own_field"] = round(ownp, 5)
            out["fr_proximity"] = round(proxp, 5)
            out["reported"] = "fr_porosity_corrected"
        else:
            out["reported"] = "fr_area_equivalent"
    else:
        out["reported"] = "fr"

    reported = out["fr"]

    if dc_resistance_ohm is not None:
        try:
            rdc = _f("dc_resistance_ohm", dc_resistance_ohm)
        except ValueError as exc:
            return err(str(exc), "ARG_INVALID")
        if rdc <= 0:
            return err("dc_resistance_ohm must be > 0", "ARG_RANGE",
                       hint="a winding always has some resistance; 0 would make "
                            "every ratio infinite")
        out["dc_resistance_ohm"] = round(rdc, 6)
        out["ac_resistance_ohm"] = round(rdc * reported, 6)
    elif turns is not None and mean_length_mm is not None:
        try:
            n_t = _f("turns", turns)
            l_t = _f("mean_length_mm", mean_length_mm)
        except ValueError as exc:
            return err(str(exc), "ARG_INVALID")
        if n_t < 1 or l_t <= 0:
            return err("turns must be >= 1 and mean_length_mm > 0", "ARG_RANGE")
        area_m2 = copper_area_mm2 * 1e-6
        length_m = n_t * l_t * 1e-3
        rdc = rho * length_m / area_m2
        out["turns"] = int(n_t)
        out["mean_length_mm"] = round(l_t, 4)
        out["copper_area_mm2"] = round(copper_area_mm2, 6)
        out["dc_resistance_ohm"] = float("%.6g" % rdc)
        out["ac_resistance_ohm"] = float("%.6g" % (rdc * reported))
    elif turns is not None or mean_length_mm is not None:
        return err("turns and mean_length_mm go together", "ARG_MISSING",
                   hint="to get ohms from geometry both are needed; otherwise pass "
                        "the measured dc_resistance_ohm")

    out["not_modelled"] = [
        "gap / fringing field (in a gapped inductor this is usually the largest "
        "AC winding loss - Dowell's 1-D field cannot see it)",
        "litz wire (strand-level skin and bundle-level effects need a different model)",
        "interleaved windings (the MMF staircase is not the one assumed; pass the "
        "layer count of a single block and the result is that block's)",
        "non-sinusoidal current (sum P = SUM I_k^2 R(f_k) over the harmonics yourself)",
        "end turns, leads, and self-resonance",
        "core loss (magnetics_core_loss) and how the total compares with it",
    ]
    return ok(
        out, "AC/DC resistance ratio (dimensionless)",
        "Dowell 1-D (Proc. IEE 113(8) 1966) layer solution",
        "F_R = D*(sinh2D+sin2D)/(cosh2D-cos2D) + (2/3)(m^2-1)*D*(sinhD-sinD)/"
        "(coshD+cosD) with D = h/delta_skin; both terms carry the D factor",
        "exact for the 1-D model (verified against an independent per-layer ODE "
        "solution to 3e-6 relative over m=1..16 and D=0.05..8). The model's own "
        "error - 1-D field, no fringing - is the dominant one in real hardware: "
        "expect it to be optimistic for a gapped inductor and within ~10% for a "
        "well-behaved foil or round-wire layer stack",
        {"field": "1-D through the stack, all layers in series carrying the same current",
         "waveform": "sine at freq_hz",
         "temperature": ("via resistivity at %g C" % temp_used) if temp_used is not None
                        else "20 C copper unless resistivity_ohm_m was given",
         "gap_field": "not modelled",
         "litz": "not modelled"})

# --------------------------------------------------------------------------
# core selection against the vendor table
#
# data/magnetic_materials.json holds published material constants and effective
# core parameters, every row carrying the URL it was transcribed from. It is
# data, not a fit: where a vendor number was unreachable the field is null and
# every answer below says WHICH field is missing rather than filling it in.
# --------------------------------------------------------------------------

DATA_PATH = os.path.join(
    os.path.dirname(os.path.dirname(os.path.dirname(os.path.dirname(
        os.path.abspath(__file__))))),
    "data", "magnetic_materials.json",
)

# The two temperatures the vendor tables actually publish a Bsat at. A value in
# between is not interpolated: the curve between them is not linear and nothing
# in this table measures it.
BSAT_TEMPERATURES = (25.0, 100.0)

# A design choice, not a material property: how much of the published Bsat an
# inductor is allowed to use at its peak current. 0.8 is a common margin; the
# caller can move it, and b_max_t overrides the whole material path.
BSAT_DERATE_TYPICAL = 0.80

# How far below the part's own AL the required AL has to be before this build says
# "gap it" rather than "use it as-is". A DESIGN tolerance, not a material law: AL is
# quoted +/-25% on many of these parts, so a shortfall inside 10% is inside the
# spread you already accept.
GAP_NEEDED_BELOW = 0.90


def _load_magnetic_data(path=None):
    p = path or DATA_PATH
    if not os.path.exists(p):
        return None, "core/material table missing: " + p
    try:
        with open(p, "r", encoding="utf-8") as fh:
            data = json.load(fh)
    except Exception as exc:  # noqa: BLE001
        return None, "core/material table unreadable: %s" % exc
    if not data.get("verified"):
        return None, ("core/material table present but not marked verified; rows "
                      "would have to be trusted unexamined")
    if not data.get("cores"):
        return None, "core/material table has no cores"
    return data, None


def _norm_key(text):
    return "".join(ch for ch in str(text or "").lower() if ch.isalnum())


def _bsat_at(material, temperature_c):
    """(bsat_mt, None) or (None, why-not)."""
    if not material:
        return None, "material not in the table"
    if abs(temperature_c - 25.0) < 1e-9:
        v = material.get("bsat_mt_25c")
    elif abs(temperature_c - 100.0) < 1e-9:
        v = material.get("bsat_mt_100c")
    else:
        return None, ("%s publishes no Bsat at %g C (only %s C); a value between "
                      "two published points is not interpolated because the curve "
                      "between them is not linear"
                      % (material.get("name"), temperature_c,
                         " / ".join("%g" % t for t in BSAT_TEMPERATURES)))
    if v is None:
        return None, ("%s has no published Bsat at %g C in this table"
                      % (material.get("name"), temperature_c))
    return float(v), None


def magnetic_data(kind="materials", name=None, material=None, shape=None,
                  vendor=None, data_path=None):
    """Read back the vendor table: materials, cores, or both.

    Nothing here is computed - it is the transcription, with the source URL, so a
    user can check a number instead of trusting it.
    """
    data, why = _load_magnetic_data(data_path)
    if data is None:
        return err(why, "DATA_UNAVAILABLE",
                   hint="fill data/magnetic_materials.json with verified rows")
    k = str(kind or "materials").strip().lower()
    if k in ("materials", "material"):
        rows = data.get("materials") or []
    elif k in ("cores", "core"):
        rows = data.get("cores") or []
    elif k in ("all", "both", "everything"):
        rows = list(data.get("materials") or []) + list(data.get("cores") or [])
    else:
        return err("kind must be materials, cores or all", "ARG_INVALID")

    def keep(row):
        if name is not None:
            key = _norm_key(name)
            if key not in (_norm_key(row.get("name")), _norm_key(row.get("part"))):
                return False
        if material is not None:
            if _norm_key(material) != _norm_key(row.get("material", row.get("name"))):
                return False
        if shape is not None:
            if _norm_key(shape) not in _norm_key(row.get("shape")):
                return False
        if vendor is not None:
            if _norm_key(vendor) not in _norm_key(row.get("vendor")):
                return False
        return True

    rows = [r for r in rows if keep(r)]
    if not rows:
        return err("no row matches those filters", "NOT_FOUND",
                   hint="magnetic_data with no filters lists everything in the table",
                   extra={"kind": k})
    return ok(
        {"kind": k, "count": len(rows), "rows": rows,
         "table_retrieved": data.get("retrieved"),
         "table_caveats": data.get("what_this_is_not")},
        "vendor rows",
        "data/magnetic_materials.json (as published by the vendor, not re-fitted here)",
        "every row carries `source`: the URL it was transcribed from",
        "as published - the vendor's own typical values at the condition stated",
        {"fits": "none performed: no Steinmetz triple is stored anywhere in the table",
         "thermal": "no Rth for any part, so no temperature rise is offered"},
        {"table_path": data_path or DATA_PATH})


def _core_loss_for(core, material, freq_hz, b_ac_t, exponents,
                   waveform="sine", duty=None):
    """Core loss in watts, or (None, why-not).

    `waveform`/`duty` change the SHAPE of the flux only: the sine answer is what
    the caller's k/alpha/beta (or the vendor's single measured point) is, and the
    correction is the iGSE shape ratio, a pure number that depends on the shape and
    on alpha, not on the amplitude.
    """
    ref = core.get("loss_ref")
    if freq_hz is None or b_ac_t is None:
        return None, ("core loss needs the AC excitation: pass freq_hz and b_ac_t "
                      "(the AC flux amplitude, i.e. half the peak-to-peak swing)")
    if b_ac_t <= 0:
        return None, "b_ac_t must be > 0: a DC-only core has no core loss"
    d = 0.5 if duty is None else duty
    segs = [(2.0 * b_ac_t, d), (-2.0 * b_ac_t, 1.0 - d)]
    # (1) the material's own Steinmetz fit, if the table ever gets one
    st = (material or {}).get("steinmetz")
    if st and core.get("ve_mm3"):
        al = float(st["alpha"])
        pv = (float(st["k"]) * freq_hz ** al * b_ac_t ** float(st["beta"]))
        note = "material Steinmetz fit, P_v * Ve"
        if waveform != "sine":
            ratio = _igse_shape_ratio(al, segs)
            pv *= ratio
            note += (" x iGSE shape ratio %g (triangle, duty %g, alpha %g)"
                     % (ratio, d, al))
        return pv * core["ve_mm3"] * 1e-9, note
    # (2) the part's own measured reference point, scaled only with exponents
    #     the caller supplied - the exponents are NOT invented here
    if not ref:
        return None, ("this part has no measured loss point in the table and its "
                      "material has no Steinmetz fit, so the loss at this "
                      "operating point is unknown (not zero)")
    f_ref = float(ref["f_khz"]) * 1e3
    b_ref = float(ref["b_mt"]) * 1e-3
    x = y = None
    if exponents:
        x, y = exponents
    ratio, shape_note = 1.0, ""
    if waveform != "sine":
        if x is None:
            return None, ("a %s flux needs alpha to turn the sine loss into the iGSE "
                          "one, and this part carries a single measured point: pass "
                          "loss_exponents=(x, y) from the material's fit - its x is "
                          "the frequency exponent and is used as alpha" % waveform)
        ratio = _igse_shape_ratio(x, segs)
        shape_note = (" x iGSE shape ratio %g (triangle, duty %g; alpha taken as the "
                      "frequency exponent x=%g)" % (ratio, d, x))
    same = (abs(freq_hz - f_ref) <= 1e-6 * f_ref
            and abs(b_ac_t - b_ref) <= 1e-6 * b_ref)
    if same:
        return float(ref["p_w"]) * ratio, ("the vendor's own measured point: %g W/set "
                                           "at %g mT / %g kHz / %g C"
                                           % (ref["p_w"], ref["b_mt"], ref["f_khz"],
                                              ref["temp_c"]) + shape_note)
    if not exponents:
        return None, ("the operating point is not the measured one (%g mT / %g kHz "
                      "/ %g C); scaling a single point away from its condition needs "
                      "loss_exponents=(x, y) from the material's fit"
                      % (ref["b_mt"], ref["f_khz"], ref["temp_c"]))
    p = float(ref["p_w"]) * (freq_hz / f_ref) ** x * (b_ac_t / b_ref) ** y * ratio
    return p, ("scaled from %g W/set at %g mT / %g kHz with the caller's "
               "exponents x=%g y=%g" % (ref["p_w"], ref["b_mt"], ref["f_khz"], x, y)
               + shape_note)

def select_core(inductance_h, i_peak_a, i_rms_a=None, freq_hz=None, b_ac_t=None,
                b_max_t=None, material=None, shape=None, temperature_c=100.0,
                bsat_derate=BSAT_DERATE_TYPICAL, loss_exponents=None,
                current_density_a_mm2=None, fill_factor=None, limit=None,
                require_window_fit=False, data_path=None,
                flux_waveform="sine", duty=None):
    """Choose a real core for an (L, Ipk) requirement, or say why none fits.

    The two constraints that decide the turns:

        L  = AL * N^2                  -> N_al   = sqrt(L / AL)
        B  = L * Ipk / (N * Ae)        -> N_b    = L * Ipk / (B_limit * Ae)

    N = max(ceil(N_al), ceil(N_b), 1). Current goes beyond B_limit if N_b > N_al,
    so that part needs a gap or more turns; a part whose AL is already small
    enough needs no gap.

    `B_limit` is the caller's `b_max_t` when given, otherwise
    `bsat_derate * Bsat(material, temperature_c)` - and Bsat is only used at the
    temperatures the material publishes (25 C / 100 C). The derate is a DESIGN
    MARGIN, not a material property.

    `flux_waveform`/`duty` (default sine / 0.5) decide the SHAPE the core loss is
    judged on: an inductor carries a triangular flux, not a sine, and the plain
    Steinmetz number is only right for the latter. The iGSE shape ratio is applied
    to `core_loss_w` alone - the turns, the flux limit and the gap do not care
    about the shape.

    What is refused rather than guessed: saturation at a temperature the material
    does not publish; a gap length when the material's mu_i is unknown; core loss
    away from a measured point without caller-supplied exponents; a non-sine
    waveform when there is no alpha to compute the shape ratio from; copper loss or
    a winding-window verdict when the table has neither a former's winding area
    nor a mean turn length for that core; and any temperature rise at all, because
    no part in the table has a published thermal resistance.
    """
    data, why = _load_magnetic_data(data_path)
    if data is None:
        return err(why, "DATA_UNAVAILABLE",
                   hint="fill data/magnetic_materials.json with verified rows")
    try:
        L = _f("inductance_h", inductance_h)
        ipk = _f("i_peak_a", i_peak_a)
        temp = _f("temperature_c", temperature_c)
        derate = _f("bsat_derate", bsat_derate)
    except ValueError as exc:
        return err(str(exc), "ARG_INVALID")
    if L <= 0:
        return err("inductance_h must be > 0", "ARG_RANGE")
    if ipk <= 0:
        return err("i_peak_a must be > 0", "ARG_RANGE",
                   hint="the peak current is what sets the flux; a zero-current "
                        "inductor needs no core")
    if not (0 < derate <= 1):
        return err("bsat_derate must be in (0, 1]", "ARG_RANGE",
                   hint="it multiplies the material's Bsat; > 1 would allow the "
                        "core to saturate by construction")
    if b_max_t is not None:
        try:
            b_lim = _f("b_max_t", b_max_t)
        except ValueError as exc:
            return err(str(exc), "ARG_INVALID")
        if b_lim <= 0:
            return err("b_max_t must be > 0", "ARG_RANGE")
        b_lim_basis = "given by the caller"
    else:
        b_lim = None
        b_lim_basis = None

    expo = None
    if loss_exponents is not None:
        try:
            expo = (_f("loss_exponents[0]", loss_exponents[0]),
                    _f("loss_exponents[1]", loss_exponents[1]))
        except (TypeError, ValueError, IndexError):
            return err("loss_exponents must be a pair (x, y)",
                       "ARG_INVALID",
                       hint="x is the frequency exponent, y the flux exponent, from "
                            "the material's own fit - they are not derived here")
        if expo[0] <= 0 or expo[1] <= 0:
            return err("loss_exponents must both be > 0", "ARG_RANGE")

    wf = _waveform_canon(flux_waveform)
    if wf is None:
        return err("flux_waveform must be sine or triangle (got %r)"
                   % (flux_waveform,), "ARG_INVALID",
                   hint="an inductor's ripple is a triangle; a sine is the default "
                        "the vendor loss fits are written for")
    if wf == "piecewise":
        return err("select_core judges a SHAPE, not an arbitrary trajectory: use "
                   "flux_waveform='triangle', or evaluate magnetics_core_loss with "
                   "segments= yourself and scale the watts", "ARG_INVALID")
    if wf != "triangle" and duty is not None:
        return err("duty only applies to flux_waveform='triangle'", "ARG_INVALID",
                   hint="a sine has no duty; passing one and having it ignored would "
                        "answer a different question than the one asked")
    duty_v = 0.5
    if duty is not None:
        try:
            duty_v = _f("duty", duty)
        except ValueError as exc:
            return err(str(exc), "ARG_INVALID")
        if not (0.0 < duty_v < 1.0):
            return err("duty must be in (0, 1) - it is the fraction of the switching "
                       "period spent rising", "ARG_RANGE")

    mats = {m.get("name"): m for m in (data.get("materials") or [])}
    cores = data.get("cores") or []

    want_mat = _norm_key(material) if material else None
    want_shape = _norm_key(shape) if shape else None

    # an empty answer is not an answer: name the filter that nothing matched
    if want_mat and not any(_norm_key(c.get("material")) == want_mat
                            for c in cores):
        return err("no core in the table uses material " + str(material),
                   "NOT_FOUND",
                   hint="the table is data, not a catalogue: magnetic_data lists "
                        "what is actually in it",
                   extra={"materials_in_table": sorted(
                       {c.get("material") for c in cores if c.get("material")})})
    if want_shape and not any(want_shape in _norm_key(c.get("shape"))
                              for c in cores):
        return err("no core in the table has a shape containing " + str(shape),
                   "NOT_FOUND",
                   hint="magnetic_data(kind='cores') lists the parts that are in it",
                   extra={"shapes_in_table": sorted(
                       {c.get("shape") for c in cores if c.get("shape")})})

    considered, rejected = [], []
    for core in cores:
        part = core.get("part")
        if want_mat and _norm_key(core.get("material")) != want_mat:
            rejected.append({"part": part, "reason": "material_filter"})
            continue
        if want_shape and want_shape not in _norm_key(core.get("shape")):
            rejected.append({"part": part, "reason": "shape_filter"})
            continue
        if not core.get("ae_mm2"):
            rejected.append({"part": part, "reason": "no_geometry",
                             "because": core.get("incomplete_note")
                             or "effective area is null in the table"})
            continue
        mat = mats.get(core.get("material"))
        row = {"part": part, "vendor": core.get("vendor"),
               "shape": core.get("shape"), "material": core.get("material"),
               "ae_mm2": core.get("ae_mm2"), "le_mm": core.get("le_mm"),
               "ve_mm3": core.get("ve_mm3"), "al_nh": core.get("al_nh"),
               "gap_um": core.get("gap_um"), "mass_g": core.get("mass_g"),
               "missing": []}

        ae_m2 = float(core["ae_mm2"]) * 1e-6

        # --- how many turns: the inductance and the flux limit each want a number
        n_al = None
        if core.get("al_nh"):
            n_al = math.sqrt(L / (float(core["al_nh"]) * 1e-9))
        else:
            row["missing"].append("al_nh")

        # --- the flux limit for THIS part
        if b_lim is not None:
            limit_t, limit_note = b_lim, b_lim_basis
        else:
            bsat_mt, sat_note = _bsat_at(mat, temp)
            if bsat_mt is None:
                row["b_limit_t"] = None
                row["saturation"] = "unknown"
                row["missing"].append("b_limit_t")
                row["b_limit_because"] = sat_note
                limit_t, limit_note = None, sat_note
            else:
                limit_t = bsat_mt * 1e-3 * derate
                limit_note = ("%g%% of %s's published %g mT at %g C"
                              % (derate * 100.0, mat.get("name"), bsat_mt, temp))
        row["b_limit_t"] = _fmt(limit_t) if limit_t is not None else None
        row["b_limit_basis"] = limit_note
        if core.get("amin_mm2") and core["amin_mm2"] < core["ae_mm2"]:
            row["flux_area_mm2"] = core["amin_mm2"]
            row["flux_area_note"] = ("Amin < Ae, so the peak flux density is judged "
                                     "on Amin")
            ae_flux = float(core["amin_mm2"]) * 1e-6
        else:
            ae_flux = ae_m2

        if limit_t is None:
            # This part cannot be judged here, but that is no reason to refuse the
            # whole question: the caller may still want the parts that can be.
            rejected.append({"part": part, "reason": "saturation_unknown",
                             "because": limit_note,
                             "material": core.get("material")})
            continue

        n_b = L * ipk / (limit_t * ae_flux)
        n = int(max(1, math.ceil(max(n_al if n_al is not None else 1.0, n_b))))
        b_peak = L * ipk / (n * ae_flux)

        row["turns_from_al"] = _fmt(n_al) if n_al is not None else None
        row["turns_from_flux"] = _fmt(n_b)
        row["turns"] = n
        row["b_peak_t"] = _fmt(b_peak)
        row["b_peak_over_limit"] = _fmt(b_peak / limit_t) if limit_t else None

        # --- saturation verdict
        bsat_mt, sat_note = _bsat_at(mat, temp)
        if bsat_mt is not None:
            b_sat = bsat_mt * 1e-3
            row["bsat_t"] = _fmt(b_sat)
            if b_peak > b_sat:
                row["saturation"] = "over"
                row["saturation_note"] = ("peak %g mT exceeds the published %g mT at "
                                          "%g C even with %d turns"
                                          % (b_peak * 1e3, bsat_mt, temp, n))
            else:
                row["saturation"] = "ok"
                row["saturation_note"] = ("peak %g mT of the published %g mT at %g C"
                                          % (b_peak * 1e3, bsat_mt, temp))
        elif b_lim is not None:
            row["saturation"] = "ok" if b_peak <= b_lim else "over"
            row["saturation_note"] = "judged against the caller's b_max_t only"
        # else: set above as "unknown"

        # --- does it need a gap?
        al_req = L / float(n) ** 2 * 1e9
        row["al_required_nh"] = _fmt(al_req)
        if core.get("al_nh"):
            ratio = al_req / float(core["al_nh"])
            row["al_ratio"] = _fmt(ratio)
            row["l_at_n_ungapped_h"] = _fmt(float(core["al_nh"]) * 1e-9 * n * n)
            row["gap_required"] = bool(ratio < GAP_NEEDED_BELOW)
        else:
            row["gap_required"] = None
            row["al_because"] = (core.get("al_note")
                                 or "AL is null in the table")
        mu_i = (mat or {}).get("mu_i")
        if row["gap_required"]:
            if mu_i:
                # standard gap estimate, no fringing correction: the gap's
                # reluctance and the core's are in series
                g = MU0 * ae_m2 * n ** 2 / L - (float(core["le_mm"]) * 1e-3
                                                / float(mu_i))
                if g > 0:
                    row["gap_mm"] = _fmt(g * 1e3)
                    row["gap_basis"] = ("series reluctance with the ungapped core, "
                                        "NO fringing correction (fringing makes the "
                                        "real gap a little smaller)")
                else:
                    row["gap_mm"] = None
                    row["gap_because"] = ("the series-reluctance estimate came out "
                                          "negative, i.e. %d turns already give more "
                                          "than the required reluctance with no gap"
                                          % n)
            else:
                row["gap_mm"] = None
                row["missing"].append("gap_mm")
                row["gap_because"] = ("mu_i is unknown for this material, so the "
                                      "core's own reluctance cannot be separated from "
                                      "the gap's")
        else:
            row["gap_mm"] = 0.0 if core.get("gap_um") == 0 else None
            if row["gap_mm"] is None:
                row["gap_because"] = core.get("gap_note") or "gap not stated"

        # --- core loss
        p_core, p_note = _core_loss_for(core, mat, freq_hz, b_ac_t, expo,
                                        waveform=wf, duty=duty_v)
        row["core_loss_w"] = _fmt(p_core) if p_core is not None else None
        row["core_loss_basis"] = p_note
        if p_core is None:
            row["missing"].append("core_loss_w")
        ref = core.get("loss_ref")
        if ref:
            row["core_loss_at_ref_w"] = ref.get("p_w")
            row["core_loss_ref_condition"] = {
                "b_mt": ref.get("b_mt"), "f_khz": ref.get("f_khz"),
                "temp_c": ref.get("temp_c")}

        # --- copper loss and the winding window
        rectx = None
        if i_rms_a is not None and current_density_a_mm2 is not None:
            try:
                irms = _f("i_rms_a", i_rms_a)
                j = _f("current_density_a_mm2", current_density_a_mm2)
            except ValueError as exc:
                return err(str(exc), "ARG_INVALID")
            if irms <= 0 or j <= 0:
                return err("i_rms_a and current_density_a_mm2 must be > 0",
                           "ARG_RANGE")
            a_wire = irms / j
            rectx = {"i_rms_a": _fmt(irms), "current_density_a_mm2": j,
                     "wire_area_mm2": _fmt(a_wire),
                     "copper_area_mm2": _fmt(a_wire * n)}
            if core.get("mlt_mm"):
                rho = COPPER_RESISTIVITY_OHM_M * (1.0 + COPPER_TEMPCO_PER_K
                                                  * (temp - 20.0))
                r_dc = rho * n * float(core["mlt_mm"]) * 1e-3 / (a_wire * 1e-6)
                rectx["r_dc_ohm"] = _fmt(r_dc)
                rectx["p_copper_w"] = _fmt(irms ** 2 * r_dc)
                rectx["p_copper_basis"] = ("rho at %g C, N * mean-turn-length / wire "
                                           "area, DC only (skin/proximity: "
                                           "magnetics_winding_ac)" % temp)
            else:
                rectx["p_copper_w"] = None
                rectx["missing_wire"] = ("no mean turn length for this core in the "
                                         "table, so the turn length is unknown")
                row["missing"].append("p_copper_w")
            if fill_factor is not None and core.get("aw_mm2"):
                try:
                    ku = _f("fill_factor", fill_factor)
                except ValueError as exc:
                    return err(str(exc), "ARG_INVALID")
                needed = a_wire * n / ku
                rectx["fill_factor"] = ku
                rectx["window_area_mm2"] = core["aw_mm2"]
                rectx["window_area_needed_mm2"] = _fmt(needed)
                rectx["window_fits"] = bool(needed <= core["aw_mm2"])
                rectx["window_basis"] = ("former winding area with fill factor %g; "
                                         "the former geometry is the vendor's, the "
                                         "fill factor is yours" % ku)
            else:
                rectx["window_fits"] = None
                rectx["window_because"] = (
                    "the table has no winding area for this core" if not
                    core.get("aw_mm2") else "pass fill_factor to check the window")
        row["winding"] = rectx

        # --- verdict
        verdicts = []
        if row["saturation"] == "over":
            verdicts.append("saturates")
        if rectx and rectx.get("window_fits") is False:
            verdicts.append("window_overflow")
        if require_window_fit and (not rectx or rectx.get("window_fits") is None):
            verdicts.append("window_unchecked")
        row["verdict"] = verdicts[0] if verdicts else "feasible"
        if verdicts:
            # one shape for every rejection: a part, a name for the reason, and
            # whatever numbers could still be computed
            row["reason"] = verdicts[0]
            rejected.append(row)
        else:
            considered.append(row)

    feasible = sorted(considered, key=lambda r: (r.get("ve_mm3") or 1e30,
                                                 r.get("mass_g") or 1e30))
    if limit is not None:
        try:
            lim = int(limit)
        except (TypeError, ValueError):
            return err("limit must be an integer", "ARG_INVALID")
        if lim <= 0:
            return err("limit must be >= 1", "ARG_RANGE")
        shown = feasible[:lim]
    else:
        shown = feasible

    unchecked = [r["part"] for r in feasible
                 if not (r.get("winding") and r["winding"].get("window_fits")
                         in (True, False))]
    out = {
        "requirement": {"inductance_h": _fmt(L), "i_peak_a": _fmt(ipk),
                        "temperature_c": temp,
                        "freq_hz": freq_hz, "b_ac_t": b_ac_t},
        "b_limit_t": (_fmt(b_lim) if b_lim is not None
                      else "%g x Bsat of the part's material" % derate),
        "candidates": shown,
        "rejected": rejected,
        "feasible_count": len(feasible),
        "table_rows": len(cores),
        "window_image": ("the winding window could not be checked for "
                         + ", ".join(unchecked)) if unchecked else None,
        "not_modelled": [
            "temperature rise: no part in the table has a published thermal "
            "resistance, so these are watts and nothing else",
            "the DC-bias roll-off of AL (a gapped ferrite loses inductance long "
            "before it saturates at Bsat) - only the Bsat point is judged",
            "fringing flux at the gap: the gap estimate is series reluctance only, "
            "and fringing loss and the extra copper loss near the gap are absent",
            "winding AC resistance (magnetics_winding_ac) and skin effect",
            "tolerance: AL is quoted +/-25% on many parts, so the turns from AL are "
            "a nominal value, not a guarantee",
            "any Steinmetz fit for the ferrites: no vendor coefficients are stored, "
            "so a loss away from a measured point needs loss_exponents from you",
        ],
    }
    if wf != "sine":
        out["flux_waveform"] = {
            "waveform": wf, "duty": _fmt(duty_v),
            "applied_to": "core_loss_w only (turns, flux limit and gap do not "
                          "depend on the shape)",
            "shape_ratio_basis": "iGSE: the ratio depends on alpha, and alpha here is "
                                 "the caller's loss_exponents[0] (x) - the same "
                                 "exponent the F^alpha scaling already uses",
        }
    return ok(
        out, "core sets, smallest effective volume first",
        "Faraday + inductance relation on vendor effective parameters "
        "(L = AL*N^2, B = L*Ipk/(N*Ae))",
        "Ae / le / Ve / AL / Amin / mass / loss point from "
        "data/magnetic_materials.json, which carries the vendor URL for every row",
        "exact arithmetic on the vendor's published typical values; the uncertainty "
        "that matters is theirs (AL tolerance, Bsat read off a curve, loss at one "
        "condition), not the arithmetic",
        {"flux_area": "Amin is used instead of Ae where the vendor publishes both",
         "bsat_derate": "a design margin chosen by the caller, not a material law",
         "gap": "series reluctance, no fringing; requires the material's mu_i",
         "verdicts": "saturates / window_overflow / window_unchecked / feasible - a "
                     "feasible verdict means the flux and the data that was present "
                     "passed, never that the part is a good design"},
        {"rejected_count": len(rejected),
         "unjudged": [r for r in rejected
                      if r.get("reason") == "saturation_unknown"],
         "feasible_parts": [r["part"] for r in feasible]})
