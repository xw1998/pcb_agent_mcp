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
  material_core_loss
                    the same Steinmetz/iGSE machinery, but with the coefficients taken
                    from the VENDOR's own printed fit in data/magnetic_materials.json
                    (powder cores: P = a*Bpk^b*f^c per permeability and per shape) so
                    nobody has to hand-convert a printed table into k/alpha/beta;
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
    # Magnetics prints its powder-core fit as P = a*B^b*f^c with B in TESLA and f in
    # kHz - tesla, not milli-tesla, which is why this is its own convention and not
    # khz_mt. Feeding a printed coefficient in as khz_mt is off by 1000^b ~ 1e6.
    "khz_t": (1e3, 1.0, "mW/cm^3 per (kHz^alpha * T^beta)", 1e3),
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
        term - Dowell's method cannot see it; magnetics_fringing_loss computes
        it separately and the two are added as powers), no litz strand-level
        effect, no bundle-level skin, no end/lead effects, no self-resonance;
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
        "AC winding loss - Dowell's 1-D field cannot see it; magnetics_fringing_loss "
        "computes that term, so add it beside this number rather than inside it)",
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
# the field that leaks out of an air gap, and what it does to the winding
#
# Dowell's 1-D stack field cannot see this term, and in a gapped inductor it is
# often larger than the term it cannot see.  The field here is the one of two
# oppositely charged strips standing in for the two pole faces: a 2-D magnetic
# charge model, i.e. the pole faces are treated as perfect magnetically
# conducting sheets of width 2a = leg width, separated by g, and the winding
# sits outside them.  Nothing in this block is a fit and nothing is copied: the
# two closed forms below are derived in docs/75 and each one is checked against
# an independent numerical answer (Biot-Savart quadrature for the field,
# complex 1-D solution for the slab factor) in tests/test_fringing_loss.py.
#
# Sign conventions: the strips carry q (A/m) and -q, so the axial field is
# q/2 just outside either face and 0 at the mid plane far away.  Only |H| enters
# the loss, so the overall sign is irrelevant here - but NOT interchangeable
# with atan2: `atan((x+a)/c) - atan((x-a)/c)` is the continuous antiderivative of
# `2a/((u^2+a^2))` for c of either sign, while atan2 wraps by pi once c < 0 and
# silently gives a field that is wrong by a factor of ~300.
# --------------------------------------------------------------------------

# ring of the axial quadrature. The integrand is smooth away from z = +-g/2 and
# decays like 1/z^4, so the fine grid is squared (dense at the gap) and the tail
# geometric. 320/480 already agrees with 640/960 to 2e-5 relative.
FRINGE_MESH_GAP = 400
FRINGE_MESH_TAIL = 600

# below this D = t/delta the closed slab factor cancels two cubes of O(D) and
# loses digits; the series is exact to O(D^8) there
SLAB_SERIES_BELOW = 1e-2


def _fringe_hz(x, z, a, g, q):
    """Axial field of the two charged strips, closed form (A/m)."""
    def term(c):
        if c == 0.0:
            c = 1e-18
        return math.atan((x + a) / c) - math.atan((x - a) / c)
    return q * (term(z - g / 2.0) - term(z + g / 2.0)) / (2.0 * math.pi)


def _fringe_hx(x, z, a, g, q):
    """Radial field of the two charged strips, closed form (A/m).

    Log-singular on the pole edge (x = a, z = +-g/2); both singularities are
    guarded so a probe placed exactly there returns a large finite number
    instead of raising.
    """
    def term(c):
        c2 = c * c if c != 0.0 else 1e-36
        u1 = (x + a) ** 2 + c2
        u2 = (x - a) ** 2 + c2
        if u1 <= 0.0:
            u1 = 1e-300
        if u2 <= 0.0:
            u2 = 1e-300
        return 0.5 * (math.log(u1) - math.log(u2))
    return q * (term(z - g / 2.0) - term(z + g / 2.0)) / (2.0 * math.pi)


def _fringe_gap_mmf(a, g):
    """|int_{-g/2}^{g/2} H_z(0,z) dz| per unit pole density, closed form (m).

    = (a*log(1+(g/a)^2) + 2g*atan(a/g))/pi.  For g << a it tends to g (an
    infinitely wide pole leaks nothing); the shortfall below 1 IS the fringing,
    i.e. the fraction of gap MMF that the pole face has to make up by carrying a
    higher density than the 1-D NI/g.
    """
    return (a * math.log1p((g / a) ** 2) + 2.0 * g * math.atan(a / g)) / math.pi


def _slab_eddy_factor(rho, freq_hz, thickness_m):
    """p_vol / H_rms^2 for a slab of thickness t with the AC field normal to it.

    Returns (factor, D, delta_m, series_factor) where `factor` is the exact 1-D
    answer divided by the textbook thin-sheet factor
    sigma*w^2*mu0^2*t^2/12, i.e. factor = 1 exactly in the thin limit and
    factor ~ 3/(2D) once the foil is thicker than a few skin depths (the loss
    then stops growing like t^2 - a thin-sheet formula over-states it by
    ~1/factor, which is 36x at t = 1 mm / 100 kHz).

    The exact answer is
        p = sigma*w^2*mu0^2*t^2/12 * 3*(sinhD - sinD)/(D^3*(sinh^2(D/2)+cos^2(D/2)))
    with D = t/delta.  Verified against an independent complex 1-D solution
    (E = A sinh(kx), 4001-point integral) to 2e-7 relative.
    """
    om = 2.0 * math.pi * freq_hz
    delta = math.sqrt(2.0 * rho / (om * MU0))
    D = thickness_m / delta
    if D < SLAB_SERIES_BELOW:
        # 3*(sinhD-sinD)/(D^3*(sinh^2(D/2)+cos^2(D/2))) = 1 - D^4*(1/24 - 1/840) + O(D^8)
        return 1.0 - D ** 4 * (1.0 / 24.0 - 1.0 / 840.0), D, delta
    num = math.sinh(D) - math.sin(D)
    den = 0.5 * (math.cosh(D) + math.cos(D))          # sinh^2(D/2)+cos^2(D/2)
    return 3.0 * num / (D ** 3 * den), D, delta


def _fringe_field_weight(x, a, g, q, height_m, n_gap=None, n_tail=None):
    """int_{-H/2}^{H/2} H_z(x,z)^2 dz in A^2/m (H_z is odd in ... even in z)."""
    ng = n_gap or FRINGE_MESH_GAP
    nt = n_tail or FRINGE_MESH_TAIL
    half = 0.5 * height_m
    span = min(half, 6.0 * g)
    zs = [span * (i / float(ng)) ** 2 for i in range(ng)]
    if half > span:
        ratio = half / span
        zs.extend([span * ratio ** (i / float(nt)) for i in range(1, nt + 1)])
    tot = 0.0
    prev = zs[0]
    fprev = _fringe_hz(x, prev, a, g, q) ** 2
    for z in zs[1:]:
        fz = _fringe_hz(x, z, a, g, q) ** 2
        tot += 0.5 * (fprev + fz) * (z - prev)          # trapezoid
        prev, fprev = z, fz
    return 2.0 * tot


def fringing_loss(freq_hz, gap_mm, leg_width_mm, leg_depth_mm, layers,
                  thickness_mm, winding_height_mm, standoff_mm=0.0,
                  insulation_mm=0.0, current_rms_a=None, ampere_turns=None,
                  resistivity_ohm_m=None, temperature_c=None,
                  probe_radii_mm=None):
    """AC copper loss driven by the field that fringes out of the air gap.

    Geometry: a leg of width `leg_width_mm` (the 2-D model is infinite in the
    other direction, so `leg_depth_mm` only enters through the turn perimeter),
    two pole faces separated by `gap_mm`, and `layers` concentric foil shells of
    thickness `thickness_mm`, of height `winding_height_mm`, the innermost one
    `standoff_mm` away from the leg surface and every further one a pitch
    `thickness_mm + insulation_mm` further out.

    One shell is one turn, so the MMF is `layers * current_rms_a`; if you want to
    scale from a measured MMF instead, pass `ampere_turns` and leave the current
    out.

    Every number returned is the 2-D field of the two pole faces alone. It is
    NOT the total winding loss: add Dowell's proximity term for the winding's own
    field (`magnetics_winding_ac`). Adding the two powers rather than superposing
    the two fields has no cross term - that is the honest limit of this model,
    not an approximation that is corrected somewhere inside.
    """
    try:
        f = _f("freq_hz", freq_hz)
        g_mm = _f("gap_mm", gap_mm)
        w_mm = _f("leg_width_mm", leg_width_mm)
        d_mm = _f("leg_depth_mm", leg_depth_mm)
        t_mm = _f("thickness_mm", thickness_mm)
        h_mm = _f("winding_height_mm", winding_height_mm)
        s_mm = _f("standoff_mm", standoff_mm)
        ins_mm = _f("insulation_mm", insulation_mm)
    except ValueError as exc:
        return err(str(exc), "ARG_INVALID")
    if f <= 0:
        return err("freq_hz must be > 0", "ARG_RANGE",
                   hint="there is no fringing LOSS without an alternating field; "
                        "the DC field sees resistance only and that is the DC "
                        "resistance")
    if g_mm <= 0:
        return err("gap_mm must be > 0", "ARG_RANGE",
                   hint="with no gap there are no pole faces to fringe from; the "
                        "gap-less case is magnetics_winding_ac's own field")
    if w_mm <= 0 or d_mm <= 0:
        return err("leg_width_mm and leg_depth_mm must be > 0", "ARG_RANGE")
    if t_mm <= 0:
        return err("thickness_mm must be > 0", "ARG_RANGE")
    if h_mm <= 0:
        return err("winding_height_mm must be > 0", "ARG_RANGE")
    if s_mm < 0:
        return err("standoff_mm must be >= 0", "ARG_RANGE",
                   hint="a negative standoff would put the first shell inside the "
                        "core leg; 0 means it touches the leg")
    if ins_mm < 0:
        return err("insulation_mm must be >= 0", "ARG_RANGE")
    try:
        n_layers = int(layers)
    except (TypeError, ValueError):
        return err("layers must be an integer", "ARG_INVALID")
    if n_layers < 1:
        return err("layers must be >= 1", "ARG_RANGE")
    if n_layers > 200:
        return err("layers=%d is outside this model" % n_layers, "ARG_RANGE",
                   hint="200 shells is already 200 axial quadratures; a winding "
                        "that deep is at the leg's own flux return and the 2-D "
                        "pole-face field is not the field any more")

    if (current_rms_a is None) == (ampere_turns is None):
        return err("give exactly one of current_rms_a and ampere_turns",
                   "ARG_MISSING",
                   hint="layers x current_rms_a is the MMF of the stack; "
                        "ampere_turns sets it directly when the current is not "
                        "the quantity you know")
    if current_rms_a is not None:
        try:
            i_rms = _f("current_rms_a", current_rms_a)
        except ValueError as exc:
            return err(str(exc), "ARG_INVALID")
        if i_rms <= 0:
            return err("current_rms_a must be > 0", "ARG_RANGE")
        ni = n_layers * i_rms
    else:
        try:
            ni = _f("ampere_turns", ampere_turns)
        except ValueError as exc:
            return err(str(exc), "ARG_INVALID")
        if ni <= 0:
            return err("ampere_turns must be > 0", "ARG_RANGE")
        i_rms = ni / float(n_layers)

    rho = COPPER_RESISTIVITY_OHM_M
    temp_used = None
    if resistivity_ohm_m is not None and temperature_c is not None:
        return err("give resistivity_ohm_m or temperature_c, not both",
                   "ARG_INVALID",
                   hint="temperature_c already implies copper's rho(T)")
    if resistivity_ohm_m is not None:
        try:
            rho = _f("resistivity_ohm_m", resistivity_ohm_m)
        except ValueError as exc:
            return err(str(exc), "ARG_INVALID")
        if rho <= 0:
            return err("resistivity_ohm_m must be > 0", "ARG_RANGE")
    elif temperature_c is not None:
        try:
            tc = _f("temperature_c", temperature_c)
        except ValueError as exc:
            return err(str(exc), "ARG_INVALID")
        if tc <= -273:
            return err("temperature_c below absolute zero", "ARG_RANGE")
        rho = COPPER_RESISTIVITY_OHM_M * (1.0 + COPPER_TEMPCO_PER_K * (tc - 20.0))
        if rho <= 0:
            return err("temperature_c=%g gives a non-physical resistivity" % tc,
                       "ARG_RANGE")
        temp_used = tc

    a = w_mm * 1e-3 / 2.0
    g = g_mm * 1e-3
    t = t_mm * 1e-3
    h = h_mm * 1e-3
    depth = d_mm * 1e-3

    warnings = []
    if g > a:
        warnings.append(
            "gap_mm=%.4g exceeds half the leg width: the two pole faces are then "
            "not facing each other over most of their area and the 2-D strip "
            "model's premise (a thin gap) is gone" % g_mm)
    if 0.5 * t > s_mm and s_mm > 0:
        warnings.append(
            "standoff_mm is smaller than half the foil thickness: the innermost "
            "shell's mid-plane is inside the leg surface, so its field is read "
            "at a radius that does not exist")

    gap_mmf = _fringe_gap_mmf(a, g)          # metres per unit q
    q = ni / gap_mmf                         # A/m on each pole face
    h_mean = ni / g
    factor, D, delta = _slab_eddy_factor(rho, f, t)
    thin_factor = ((2.0 * math.pi * f) ** 2 * MU0 ** 2 * t ** 2 /
                   (12.0 * rho))

    rows = []
    total = 0.0
    r_dc = 0.0
    for i in range(n_layers):
        s = s_mm * 1e-3 + i * (t + ins_mm * 1e-3) + 0.5 * t
        x = a + s
        perim = 2.0 * ((w_mm * 1e-3 + 2.0 * s) + (depth + 2.0 * s))
        weight = _fringe_field_weight(x, a, g, q, h)
        p = thin_factor * factor * t * perim * weight
        r_layer_dc = rho * perim / (h * t)
        total += p
        r_dc += r_layer_dc
        rows.append({
            "layer": i + 1,
            "radius_mm": round(s * 1e3, 6),
            "perimeter_mm": round(perim * 1e3, 4),
            "field_weight_a2_m": float("%.6g" % weight),
            "peak_field_a_m": round(abs(_fringe_hz(x, 0.0, a, g, q)), 1),
            "loss_w": float("%.6g" % p),
            "dc_resistance_ohm": float("%.6g" % r_layer_dc),
        })


    # measured size of the component this model does not carry: the radial one
    s1 = s_mm * 1e-3 + 0.5 * t
    hx_ratio = None
    if s1 > 0:
        hz_pt = _fringe_hz(a + s1, 0.2 * g, a, g, q)
        hx_pt = _fringe_hx(a + s1, 0.2 * g, a, g, q)
        if hz_pt:
            hx_ratio = abs(hx_pt / hz_pt)

    probes = []
    if probe_radii_mm is not None:
        try:
            radii = list(probe_radii_mm)
        except TypeError:
            return err("probe_radii_mm must be a list of numbers", "ARG_INVALID")
        for r_mm in radii:
            try:
                rr = _f("probe_radii_mm entry", r_mm)
            except ValueError as exc:
                return err(str(exc), "ARG_INVALID")
            if rr < 0:
                return err("probe_radii_mm entries must be >= 0", "ARG_RANGE")
            xp = a + rr * 1e-3
            wgt = _fringe_field_weight(xp, a, g, q, h)
            probes.append({
                "standoff_mm": round(rr, 6),
                "field_weight_a2_m": float("%.6g" % wgt),
                "peak_field_a_m": round(abs(_fringe_hz(xp, 0.0, a, g, q)), 1),
                "loss_w_for_1mm_of_foil": float(
                    "%.6g" % (thin_factor * factor * 1e-3 *
                              2.0 * ((w_mm * 1e-3 + 2.0 * rr * 1e-3) +
                                     (depth + 2.0 * rr * 1e-3)) * wgt)),
            })

    not_modelled = [
        "the radial (in-plane) field component: it drives a circulating current "
        "whose loop closes over the layer stack, so its factor depends on the "
        "stack geometry and not on the foil thickness. Here it is %.2f of the "
        "axial component one gap-width above the pole face - not small, and "
        "simply absent from this number"
        % (hx_ratio if hx_ratio is not None else float("nan")),
        "the 3-D ends of the leg: the model is 2-D (infinite along leg_depth_mm), "
        "so the two end faces of the winding see the field of a face that never "
        "ends - this over-estimates there",
        "superposition: Dowell's proximity loss of the winding's own field and "
        "this one are added as powers, with no cross term. The true combined "
        "loss is not exactly their sum",
        "round wire: the transverse-field eddy factor of a cylinder needs Kelvin "
        "functions. Pass probe_radii_mm and apply your own wire factor to "
        "field_weight_a2_m rather than getting a foil answer for a round wire",
        "permeability of the pole faces: they are perfect magnetic conductors "
        "here, so no flux is lost INTO the core and no saturation shrinks the "
        "fringing field (a saturated gap fringes more, not less)",
        "the winding's own field inside the gap (it modifies the MMF the gap "
        "sees when the winding is not concentric or the core is not symmetric)",
        "non-sinusoidal current: sum P over the harmonics yourself, with "
        "field_weight scaling as I_k^2",
        "insulation between shells thicker than insulation_mm assumes a straight "
        "radial stack (no spiral, no layer crossover)",
    ]

    out = {
        "ampere_turns": round(ni, 6),
        "turns_implied": n_layers,
        "current_rms_a": float("%.6g" % i_rms),
        "gap_field_uniform_a_m": round(h_mean, 1),
        "pole_density_a_m": round(q, 1),
        "pole_concentration": round(q * g / ni, 6),
        "gap_mmf_fraction": round(gap_mmf / g, 6),
        "skin_depth_um": round(delta * 1e6, 4),
        "foil_over_delta": round(D, 5),
        "slab_factor": round(factor, 6),
        "thin_sheet_over_exact": round(1.0 / factor, 6),
        "layers": rows,
        "total_loss_w": float("%.6g" % total),
        "fringe_resistance_ohm": float("%.6g" % (total / (i_rms * i_rms))),
        "dc_resistance_ohm": float("%.6g" % r_dc),
        "loss_over_dc": round(total / (i_rms * i_rms * r_dc), 6),
        "radial_over_axial_at_0p2g": (round(hx_ratio, 4) if hx_ratio is not None
                                      else None),
        "warnings": warnings,
        "not_modelled": not_modelled,
    }
    if probe_radii_mm is not None:
        out["probes"] = probes

    return ok(
        out, "W (fringing-field copper loss in the layers)",
        "2-D magnetic-charge (pole-face) field + exact 1-D slab eddy factor",
        "H_z,H_x = closed forms of two oppositely charged strips of width 2a and "
        "separation g; q set so that int_{-g/2}^{g/2} H_z dz = NI exactly "
        "(closed form); per shell P = sigma*w^2*mu0^2*t^2/12 * "
        "3(sinhD-sinD)/(D^3*(sinh^2(D/2)+cos^2(D/2))) * t * perimeter * "
        "int H_z^2 dz, with D = t/delta",
        "field: closed form checked against Biot-Savart quadrature (2e-6 "
        "relative) and against div H = curl H = 0 by finite differences; gap "
        "integral checked against that quadrature (3e-5) and against the exact "
        "limit int/(q g) -> 1 as g/a -> 0 (2e-8); slab factor checked against an "
        "independent complex 1-D solution (2e-7) and against the thin-sheet "
        "limit. The model error that dominates in hardware is the one listed in "
        "not_modelled, above all the radial field component and the 2-D ends",
        {"geometry": "concentric foil shells, one shell = one turn, straight "
                     "radial stack, no spiral",
         "field": "2-D; the pole faces are perfect magnetic conductors and the "
                  "winding does not perturb them",
         "waveform": "sine at freq_hz, RMS amplitudes",
         "temperature": ("via resistivity at %g C" % temp_used) if temp_used is not None
                        else "20 C copper unless resistivity_ohm_m was given",
         "field_sampled_at": "each shell's mid-radius, applied to the whole turn "
                             "perimeter"},
        None,
    )

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


# --------------------------------------------------------------------------
# powder-core toroids: geometry and AL, kept in a second file
#
# data/powder_cores.json holds the 43 toroid sizes the 210-page powder catalog
# prints (printed p.155-197), each with its permeability rows and the part number
# that exists in each of the nine material columns. The MATERIAL half - Bsat,
# density, Curie, and the loss fits - stays in data/magnetic_materials.json, so
# neither file repeats the other: this one answers "which shapes exist and what AL
# does each permeability give", that one answers "what is the material".
#
# Two structural facts about the printed pages are load-bearing here:
#   * a combination the vendor does not make has NO printed cell (not a dash), so
#     an absent part is '-' or null in the data and is never carried as a guess;
#   * a powder core has no discrete gap - the gap is distributed through the
#     volume - so AL cannot be trimmed by cutting one. select_core therefore
#     refuses to invent a gap length for these parts and says to change the
#     permeability instead.
# --------------------------------------------------------------------------

POWDER_PATH = os.path.join(os.path.dirname(DATA_PATH), "powder_cores.json")

# The nine column headings the toroid pages print, mapped onto the material names
# data/magnetic_materials.json already uses plus the variant label its loss_fit
# rows use. Every column is a material (or a variant of one); a heading this code
# does not know aborts the load rather than being folded onto a neighbour, because
# the column IS the material and merging two would silently relabel 200 parts.
POWDER_COLUMNS = {
    "Kool M\u00b5": ("Kool Mu", None),
    "Kool M\u00b5 MAX": ("Kool Mu", "MAX"),
    "Kool M\u00b5 H\u0192": ("Kool Mu", "HF"),
    "Kool M\u00b5 Ultra": ("Kool Mu", "Ultra"),
    "XFlux": ("XFlux", None),
    "XFlux Ultra": ("XFlux", "Ultra"),
    "Edge": ("Edge", None),
    "High Flux": ("High Flux", None),
    "MPP": ("MPP", None),
}

POWDER_SCHEMA = "powder-core-toroid"

# printed p.18: a C0 prefix grades the inductance into a 2 % band above 4.65 mm OD
# and a 5 % band at or below it; an 00 prefix is the single printed value with the
# +-8 % band stated above the column.
POWDER_GRADE_OD_MM = 4.65

def _load_powder_cores(path=None):
    """(data, None) or (None, why-not). The table's shape is part of the contract."""
    p = path or POWDER_PATH
    if not os.path.exists(p):
        return None, "powder-core table missing: " + p
    try:
        with open(p, "r", encoding="utf-8") as fh:
            data = json.load(fh)
    except Exception as exc:  # noqa: BLE001
        return None, "powder-core table unreadable: %s" % exc
    if data.get("schema") != POWDER_SCHEMA:
        return None, ("powder-core table has schema %r, not %r; the shape of the table "
                      "is checked rather than assumed, because a reader that guessed "
                      "would answer from the wrong field"
                      % (data.get("schema"), POWDER_SCHEMA))
    if not data.get("sizes"):
        return None, "powder-core table has no sizes"
    cols = data.get("column_order") or []
    if not cols:
        return None, "powder-core table has no column_order"
    unknown = [c for c in cols if c not in POWDER_COLUMNS]
    if unknown:
        return None, ("powder-core table has column headings this code cannot map onto "
                      "a material: " + ", ".join(map(str, unknown)))
    return data, None

def _powder_tolerance(part, od_mm, rule):
    """The AL band the part number's own prefix promises, or None if it promises none."""
    if not part or not rule:
        return None
    head = part[:2].upper().replace("O", "0")
    if head == "C0":
        if od_mm is None:
            return None
        return (rule.get("od_le_4p65") if float(od_mm) <= POWDER_GRADE_OD_MM
                else rule.get("od_gt_4p65"))
    if head == "00":
        return rule.get("default_without_c0")
    return None

def _powder_core_rows(data, material=None, permeability=None):
    """One dict per purchasable part, shaped like a row of data["cores"].

    The file is a printed-page transcription (size -> permeability row -> nine
    columns), so this is the expansion of that grid into the flat "part" view
    select_core works on. Nothing is computed except the mass estimate, and when
    the printed density or the printed permeability factor is missing the mass is
    None with the reason rather than an interpolation.
    """
    cols = data.get("column_order") or []
    props = data.get("material_properties_printed_page_16") or {}
    p10 = (data.get("material_comparison_printed_page_10") or {}).get("rows") or {}
    density_rows = props.get("rows") or {}
    factors = props.get("permeability_density_factors_60u") or {}
    tol_rule = data.get("al_tolerance_pct_c0") or {}
    vendor = (data.get("source") or {}).get("vendor")
    want_mat = _norm_key(material) if material else None
    want_mu = _f("permeability", permeability) if permeability is not None else None
    out = []
    for size in data.get("sizes") or []:
        geo = size.get("geometry") or {}
        ve_mm3 = geo.get("ve_mm3")
        od = size.get("od_mm")
        for r in size.get("rows") or []:
            perm = r.get("perm")
            if perm is None:
                continue
            perm_f = float(perm)
            if want_mu is not None and abs(perm_f - want_mu) > 1e-9:
                continue
            for i, cell in enumerate(r.get("parts") or []):
                if i >= len(cols) or not isinstance(cell, str):
                    continue
                part = cell.strip()
                if not part or part == "-":
                    continue
                col = cols[i]
                mname, variant = POWDER_COLUMNS[col]
                if want_mat and _norm_key(mname) != want_mat:
                    continue
                dens = (density_rows.get(col) or {}).get("density_60u_g_cm3")
                fac = factors.get("%g" % perm_f)
                if dens is None:
                    mass, mass_note = None, ("p.16 prints no density for the %s column"
                                             % col)
                elif fac is None:
                    mass, mass_note = None, (
                        "p.16 prints eleven density factors for fourteen permeabilities "
                        "and %g u is one of the three without one, so the mass is "
                        "refused rather than interpolated" % perm_f)
                elif ve_mm3 is None:
                    mass, mass_note = None, "this size carries no effective volume"
                else:
                    mass = float(ve_mm3) * 1e-3 * float(dens) * float(fac)
                    mass_note = ("Ve x the %s column's 60u density (%g g/cm3) x the "
                                 "printed %g u factor %g; the catalog defers the nominal "
                                 "weight to the individual data sheet, so this is an "
                                 "estimate and not the vendor's part weight"
                                 % (col, dens, perm_f, fac))
                out.append({
                    "part": part,
                    "vendor": vendor,
                    "shape": "toroid",
                    "material": mname,
                    "material_variant": variant,
                    "material_column": col,
                    "permeability": _fmt(perm_f),
                    "ae_mm2": geo.get("ae_mm2"),
                    "le_mm": geo.get("le_mm"),
                    "ve_mm3": ve_mm3,
                    "al_nh": r.get("al_nh"),
                    "gap_um": 0,
                    "gap_is_distributed": True,
                    "gap_note": ("distributed gap: a powder core's gap is spread through "
                                 "the whole volume, so AL cannot be trimmed by cutting a "
                                 "gap - change the permeability or the turns instead"),
                    "aw_mm2": geo.get("window_area_mm2"),
                    "mlt_mm": None,
                    "mlt_note": ("the catalog prints no mean turn length for the "
                                 "toroids, and a toroid's MLT depends on the winding "
                                 "build, so copper resistance is not estimated here"),
                    "mass_g": _fmt(mass) if mass is not None else None,
                    "mass_basis": mass_note,
                    "bsat_typical_t": (p10.get(col) or {}).get("sat_flux_density_t"),
                    "bsat_basis": ("the catalog's comparison page (printed p.10) prints "
                                   "this one saturation value for the %s column and no "
                                   "temperature against it" % col),
                    "al_tolerance_pct": _powder_tolerance(part, od, tol_rule),
                    "finish": part[-2:],
                    "od_mm": od,
                    "id_mm": size.get("id_mm"),
                    "ht_mm": size.get("ht_mm"),
                    "size": size.get("size"),
                    "printed_page": size.get("printed_page"),
                    "loss_fit_lookup": {"material": mname,
                                        "permeability": perm_f,
                                        "shape": "toroid",
                                        "variant": variant},
                })
    return out

def _magnetic_data_powder(kind, material=None, permeability=None, limit=None,
                          name=None, powder_data_path=None):
    """Read back the powder-core table: the size blocks, or the flat part list.

    Two shapes, because two questions are asked of this file. `powder_cores` is
    the printed page as printed - 43 sizes, each with its permeability rows and
    nine columns - and `powder_parts` is the same grid flattened to one row per
    purchasable part, which is what a caller filtering by material and
    permeability actually wants.
    """
    data, why = _load_powder_cores(powder_data_path)
    if data is None:
        return err(why, "DATA_UNAVAILABLE",
                   hint="data/powder_cores.json ships with the package; check that it "
                        "is present and has schema %r" % POWDER_SCHEMA)
    try:
        lim = None
        if limit is not None:
            lim = int(limit)
            if lim <= 0:
                return err("limit must be >= 1", "ARG_RANGE")
    except (TypeError, ValueError):
        return err("limit must be an integer", "ARG_INVALID")
    want_mat = _norm_key(material) if material else None
    want_mu = _f("permeability", permeability) if permeability is not None else None

    def col_kept(col):
        if want_mat is None:
            return True
        mname, variant = POWDER_COLUMNS[col]
        return _norm_key(mname) == want_mat or _norm_key(col) == want_mat

    if kind == "powder_cores":
        blocks = []
        for size in data.get("sizes") or []:
            rows = []
            for r in size.get("rows") or []:
                perm = r.get("perm")
                if perm is None:
                    continue
                if want_mu is not None and abs(float(perm) - want_mu) > 1e-9:
                    continue
                cols = data.get("column_order") or []
                keep = [i for i, c in enumerate(cols) if col_kept(c)]
                if want_mat is not None and not any(
                        isinstance(c, str) and c.strip() not in ("", "-")
                        for i, c in enumerate(r.get("parts") or []) if i in keep):
                    continue
                entry = {"perm": perm, "al_nh": r.get("al_nh")}
                entry["parts"] = {cols[i]: (r.get("parts") or [None] * len(cols))[i]
                                  for i in keep}
                rows.append(entry)
            if not rows:
                continue
            blocks.append(dict(size, rows=rows))
        if lim is not None:
            blocks = blocks[:lim]
        if not blocks:
            return err("no size/row matches those filters", "NOT_FOUND",
                       hint="drop material/permeability to see the whole grid",
                       extra={"sizes_in_table": len(data.get("sizes") or [])})
        return ok(
            {"kind": "powder_cores", "count": len(blocks), "sizes": blocks,
             "column_order": data.get("column_order"),
             "material_comparison_printed_page_10":
                 data.get("material_comparison_printed_page_10"),
             "material_properties_printed_page_16":
                 data.get("material_properties_printed_page_16"),
             "part_number_reading": data.get("part_number_reading"),
             "al_tolerance_pct_c0": data.get("al_tolerance_pct_c0"),
             "printing_anomalies": data.get("printing_anomalies"),
             "source": data.get("source"),
             "table_retrieved": data.get("source", {}).get("retrieved"),
             "table_caveats": data.get("what_this_is")},
            "printed toroid sizes",
            "transcription of the vendor's powder-core catalog, printed p.155-197",
            "every size block carries the printed page it came from; the geometry is "
            "printed, the mass estimate is the only computed field and it is absent "
            "when a printed input is absent",
            "as printed - the vendor's own typical values at the stated conditions",
            {"cells": data.get("cells"),
             "printing": "five vendor printing anomalies are listed in "
                         "`printing_anomalies` and are kept verbatim, not corrected"},
            {"table_path": POWDER_PATH})

    rows = _powder_core_rows(data, material=material, permeability=permeability)
    if name is not None:
        key = _norm_key(name)
        rows = [r for r in rows if _norm_key(r.get("part")) == key]
    if not rows:
        extra = {"parts_in_table": len(_powder_core_rows(data))}
        if want_mu is not None:
            same_mat = _powder_core_rows(data, material=material)
            extra["permeabilities_printed"] = sorted(
                {r["permeability"] for r in same_mat})
        return err("no powder-core part matches those filters", "NOT_FOUND",
                   hint="magnetic_data(kind='powder_parts') with no filters lists "
                        "all of them",
                   extra=extra)
    total = len(rows)
    if lim is not None:
        rows = rows[:lim]
    return ok(
        {"kind": "powder_parts", "count": len(rows), "total_matching": total,
         "rows": rows,
         "source": data.get("source"),
         "table_caveats": data.get("what_this_is")},
        "powder-core parts",
        "one row per purchasable part number: size geometry joined with the printed "
        "AL and material column",
        "geometry and AL printed on p.155-197; density and the permeability factor "
        "printed on p.16; the mass is Ve x density x factor and is an estimate",
        "as printed; only the mass is computed, and it is withheld when a printed "
        "input is missing",
        {"mass": "an estimate, not the vendor's nominal part weight",
         "gap": "distributed - there is no discrete gap and AL cannot be trimmed",
         "mlt": "not printed, so no copper estimate is made from this table"},
        {"table_path": POWDER_PATH})

def magnetic_data(kind="materials", name=None, material=None, shape=None,
                  vendor=None, data_path=None, permeability=None, limit=None,
                  powder_data_path=None):
    """Read back the vendor table: materials, cores, or both.

    Nothing here is computed - it is the transcription, with the source URL, so a
    user can check a number instead of trusting it.
    """
    data, why = _load_magnetic_data(data_path)
    if data is None:
        return err(why, "DATA_UNAVAILABLE",
                   hint="fill data/magnetic_materials.json with verified rows")
    k = str(kind or "materials").strip().lower()
    if k in ("powder_cores", "powder_core", "powder", "toroids", "toroid"):
        return _magnetic_data_powder(k, material, permeability, limit, name,
                                     powder_data_path)
    if k in ("powder_parts", "powder_part"):
        return _magnetic_data_powder("powder_parts", material, permeability, limit,
                                     name, powder_data_path)
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
        {"fits": "no material-level si triple (k/alpha/beta) is stored anywhere in "
                 "the table; what the powder vendor prints instead - a fit per "
                 "permeability, per core shape and, for its High DC Bias lines, per "
                 "variant - is in `loss_fit` (93 printed rows over five materials), "
                 "and magnetics_material_core_loss evaluates it",
         "thermal": "no Rth for any part, so no temperature rise is offered"},
        {"table_path": data_path or DATA_PATH})

# ---------------------------------------------------------------------------
# the vendor's own loss fit: PL = a * Bpk^b * f^c   (mW/cm3, T, kHz)
#
# A powder-core vendor does not publish a material-level k/alpha/beta: the
# coefficients depend on the permeability AND on the core shape, so "the loss of
# Kool Mu" is not one number and every single-number answer to that question is
# wrong by a factor the caller cannot see. The catalog prints a fit per
# (permeability, shape) pair, and `loss_fit` in the table keeps every printed row
# with the page it came from. What this section does is resolve a question that is
# stated precisely enough for the fit, and REFUSE otherwise - it never picks a
# permeability the caller did not name, because two rows two pages apart differ by
# more than the answer's own accuracy.
#
# Three independent facts were checked against the catalog's worked examples
# (printed p. 23-24): a = 44.30, b = 1.988, c = 1.541 at Bpk = 0.014 T / 100 kHz
# gives 11.04 mW/cm3 where the catalog prints 11.0, and 0.055 T gives 167.6 where
# it prints 168, and 0.092 T gives 466.03 where it prints 466. That both pins the
# readings AND pins which exponent is which, which is the easy mistake here: in
# P = a*B^b*f^c the SECOND exponent (c) is the frequency one, so c is alpha and b
# is beta. Get them the wrong way round and the number stays plausible.
# ---------------------------------------------------------------------------

# The four shape families the catalog prints separate fits for, and the spellings
# a caller may use for them.
LOSS_FIT_SHAPES = {
    "toroid": ("toroid", "toroids", "toroidal", "ring", "rings"),
    "E/U/EER": ("E/U/EER", "e", "u", "eer", "e core", "u core", "eer core",
                "e cores", "u cores", "eer cores"),
    "EQ/LP": ("EQ/LP", "eq", "lp", "eq lp", "eq&lp", "eq cores", "lp cores"),
    "block": ("block", "blocks", "cylinder", "cylinders", "round block",
              "round blocks"),
}

# The axes of the catalog's own loss-density charts (printed p. 99-108): the fits
# were made to those curves, so a frequency or a flux outside them is extrapolation
# and is flagged. These are READINGS of the printed axes, not a vendor promise.
LOSS_FIT_CHART_F_KHZ = (5.0, 2000.0)
LOSS_FIT_CHART_B_T = 0.5

def _loss_shape(name):
    """Canonical shape family, or None when the name is not one of the four."""
    key = _norm_key(name)
    if not key:
        return None
    for canon in LOSS_FIT_SHAPES:
        if key == _norm_key(canon):
            return canon
        for spell in LOSS_FIT_SHAPES[canon]:
            if key == _norm_key(spell):
                return canon
    return None

def _variant_key(v):
    """Collapse the vendor's product-line names onto the table's own labels."""
    key = _norm_key(v)
    if key in ("", "base", "none", "plain", "standard"):
        return ""
    if key in ("hf", "maxhf"):
        return "hf"
    if key.startswith("max"):
        return "maxhp" if ("hp" in key or "highperf" in key) else "max"
    if key.startswith("ultra"):
        return "ultra"
    return key

def _loss_fit_of(material):
    """{'fit': the loss_fit block, 'rows': the usable rows} or None."""
    lf = (material or {}).get("loss_fit")
    if not isinstance(lf, dict):
        return None
    rows = []
    for r in lf.get("rows") or []:
        if not isinstance(r, dict):
            continue
        if not all(k in r for k in ("shape", "permeability", "a", "b", "c")):
            continue
        if not isinstance(r["permeability"], (list, tuple)):
            continue
        rows.append(r)
    return {"fit": lf, "rows": rows}

def _loss_row_label(r):
    """One line that names a row precisely enough to look it up in the catalog."""
    bits = [str(r.get("shape"))]
    bits.append("/".join("%g" % p for p in r["permeability"]) + "u")
    if r.get("variant"):
        bits.append(str(r["variant"]))
    if "frequency_khz_below" in r:
        bits.append("<%g kHz" % r["frequency_khz_below"])
    if "frequency_khz_above" in r:
        bits.append(">%g kHz" % r["frequency_khz_above"])
    return " ".join(bits)

def _resolve_loss_fit(material, permeability, shape, variant, freq_hz):
    """(row, fit, None) for one printed row, or (None, fit, why-not).

    Every filter is a question the fit itself asks: which product line, which
    shape, which permeability, and (for the rows the catalog splits) which side of
    100 kHz. A question narrower than the fit gets one row; anything else is
    refused with the list, never resolved by picking.
    """
    name = (material or {}).get("name") or "this material"
    lf = _loss_fit_of(material)
    if lf is None or not lf["rows"]:
        return None, lf, ("%s carries no vendor loss fit in this table, so its core "
                          "loss density cannot be looked up - pass k/alpha/beta to "
                          "magnetics_core_loss if you have them from elsewhere" % name)
    fit, rows = lf["fit"], lf["rows"]

    want_variant = _variant_key(variant)
    pool = [r for r in rows if _variant_key(r.get("variant")) == want_variant]
    if not pool:
        have = sorted({_variant_key(r.get("variant")) for r in rows})
        return None, fit, ("%s has no loss fit for variant %r; the catalog prints "
                           "one for %s" % (name, variant or "base",
                                           ", ".join(v or "base" for v in have)))

    if shape is not None:
        canon = _loss_shape(shape)
        if canon is None:
            return None, fit, ("shape must be one of " + ", ".join(LOSS_FIT_SHAPES)
                               + " (got %r)" % (shape,))
        narrowed = [r for r in pool if _loss_shape(r.get("shape")) == canon]
        if not narrowed:
            have = sorted({str(r.get("shape")) for r in pool})
            return None, fit, ("%s prints no %s fit for variant %r; it has %s"
                               % (name, canon, variant or "base", ", ".join(have)))
        pool = narrowed

    if permeability is not None:
        try:
            mu = float(permeability)
        except (TypeError, ValueError):
            return None, fit, "permeability must be numeric (the vendor quotes it in u)"
        narrowed = [r for r in pool if any(abs(float(p) - mu) < 1e-9
                                           for p in r["permeability"])]
        if not narrowed:
            have = sorted({p for r in pool for p in r["permeability"]})
            return None, fit, ("%s has no fit at %g u for that shape/variant; the "
                               "printed rows cover %s u"
                               % (name, mu, ", ".join("%g" % p for p in have)))
        pool = narrowed

    if freq_hz is not None:
        f_khz = freq_hz / 1e3
        keep = []
        split_note = []
        for r in pool:
            lo = r.get("frequency_khz_below")
            hi = r.get("frequency_khz_above")
            if lo is not None and f_khz >= float(lo):
                split_note.append("%s starts above %g kHz" % (_loss_row_label(r), lo))
                continue
            if hi is not None and f_khz <= float(hi):
                split_note.append("%s stops below %g kHz" % (_loss_row_label(r), hi))
                continue
            keep.append(r)
        if not keep:
            return None, fit, ("no printed row covers %g kHz: %s - the catalog splits "
                               "this fit at the boundary and neither side claims the "
                               "boundary itself, so say which one you mean by using a "
                               "frequency that is inside it"
                               % (f_khz, "; ".join(split_note) or "no row matched"))
        pool = keep

    if len(pool) > 1:
        return None, fit, ("%d printed rows match that and they are not the same fit; "
                           "name permeability and shape: %s"
                           % (len(pool), " | ".join(_loss_row_label(r) for r in pool)))
    return pool[0], fit, None

def material_core_loss(material, freq_hz, b_peak_t, permeability=None, shape=None,
                       variant=None, waveform="sine", duty=None, segments=None,
                       ve_mm3=None, data_path=None):
    """Core loss density from a VENDOR's own printed fit, looked up by name.

    This is magnetics_core_loss with the coefficients taken from the table instead
    of from you, and the lookup is deliberately literal: the vendor fit is per
    permeability AND per core shape, so `material`, `permeability`, `shape` and
    (where the product line has variants) `variant` are all part of the question.
    Name them; an under-specified question is refused with the list of printed rows
    rather than answered with one of them.

    The printed equation is

        PL = a * Bpk^b * f^c        PL in mW/cm3, Bpk in TESLA, f in kHz

    and it is evaluated in the `khz_t` unit convention, so nothing is converted by
    hand. Note which exponent is which: in that form `c` is the FREQUENCY exponent
    and `b` the flux exponent, so the Steinmetz triple this becomes is
    (k, alpha, beta) = (a converted to W/m3, c, b). Swapping them keeps the answer
    plausible and wrong, which is why the catalog's own worked examples are
    reproduced in the test suite.

    Bpk is HALF the AC swing (the amplitude), the same `b_peak_t` every other
    Steinmetz call here takes. waveform/duty/segments work exactly as in
    magnetics_core_loss (iGSE shape correction, alpha = the frequency exponent c).
    """
    data, why = _load_magnetic_data(data_path)
    if data is None:
        return err(why, "DATA_UNAVAILABLE",
                   hint="fill data/magnetic_materials.json with verified rows")
    mat = None
    key = _norm_key(material)
    for m in data.get("materials") or []:
        if _norm_key(m.get("name")) == key:
            mat = m
            break
    if mat is None:
        have = [m.get("name") for m in (data.get("materials") or [])
                if isinstance(m.get("loss_fit"), dict)]
        return err("no material named %r in the table" % (material,), "NOT_FOUND",
                   hint="materials with a vendor loss fit: "
                        + (", ".join(have) if have else "none"))
    try:
        f = _f("freq_hz", freq_hz)
    except ValueError as exc:
        return err(str(exc), "ARG_INVALID")
    if f <= 0:
        return err("freq_hz must be > 0", "ARG_RANGE")

    row, fit, why = _resolve_loss_fit(mat, permeability, shape, variant, f)
    if row is None:
        if fit is None or not fit.get("rows"):
            return err(why, "FIT_NOT_RESOLVED",
                       hint="magnetics_core_loss takes k/alpha/beta from you for any "
                            "material; only a vendor's own printed fit can be looked "
                            "up here, and inventing one is not an option")
        return err(why, "FIT_NOT_RESOLVED",
                   hint="magnetic_data(kind='materials') lists every printed row; the "
                        "catalog page is in loss_fit.sources",
                   extra={"material": mat.get("name"),
                          "rows_available": [_loss_row_label(r)
                                             for r in (fit or {}).get("rows") or []]})

    # a = k, c = alpha (frequency), b = beta (flux) - see the note above
    sub = core_loss_steinmetz(f, b_peak_t, row["a"], row["c"], row["b"],
                              units="khz_t", ve_mm3=ve_mm3, waveform=waveform,
                              duty=duty, segments=segments)
    if sub.get("status") != "ok":
        return sub
    val = sub["value"]
    src = (fit.get("sources") or [None])[0]
    page = row.get("printed_page")
    if page is not None:
        src = "%s#page=%d" % (str(src).split("#")[0], page) if src else None
    val.update({
        "fit_material": mat.get("name"),
        "fit_vendor": mat.get("vendor"),
        "fit_row": _loss_row_label(row),
        "fit_variant": row.get("variant"),
        "fit_shape": row.get("shape"),
        "fit_permeability_u": [float(p) for p in row["permeability"]],
        "fit_coefficients": {"a": row["a"], "b": row["b"], "c": row["c"],
                             "form": fit.get("form"),
                             "b_is": fit.get("b_is")},
        "fit_units": fit.get("units"),
        "fit_printed_page": page,
        "fit_source": src,
        "fit_is_a_curve_fit_not_a_limit": fit.get("not_a_spec"),
    })
    warns = []
    f_lo, f_hi = LOSS_FIT_CHART_F_KHZ
    if f / 1e3 < f_lo or f / 1e3 > f_hi:
        warns.append("%g kHz is outside the %g-%g kHz span of the vendor's loss "
                     "charts that this fit was made from, so it is extrapolation"
                     % (f / 1e3, f_lo, f_hi))
    if val.get("b_peak_t") is not None and val["b_peak_t"] > LOSS_FIT_CHART_B_T:
        warns.append("%g T is above the %g T top of the vendor's loss charts; a "
                     "powder core is also close to its own saturation there, and this "
                     "fit has no saturation term" % (val["b_peak_t"],
                                                      LOSS_FIT_CHART_B_T))
    if warns:
        val["warnings"] = warns
    val["not_modelled"].append(
        "the fit's own validity: the vendor curve-fitted charts over %g-%g kHz and up "
        "to %g T, and no fit is a guarantee even inside that window"
        % (f_lo, f_hi, LOSS_FIT_CHART_B_T))
    sub["assumptions"] = dict(sub.get("assumptions") or {}, **{
        "coefficients": "the vendor's printed fit for %s (%s), page %s - not fitted "
                        "or adjusted here" % (_loss_row_label(row),
                                              mat.get("name"), page),
        "b_peak": "half the AC swing, which is the vendor's own Bpk definition",
    })
    return sub


def _core_loss_for(core, material, freq_hz, b_ac_t, exponents,
                   waveform="sine", duty=None):
    """Core loss in watts, or (None, why-not).

    `waveform`/`duty` change the SHAPE of the flux only: the sine answer is what
    the caller's k/alpha/beta (or the vendor's single measured point) is, and the
    correction is the iGSE shape ratio, a pure number that depends on the shape and
    on alpha, not on the amplitude.
    """
    ref = core.get("loss_ref")
    lookup = core.get("loss_fit_lookup")
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
    # (2) a vendor's own printed fit, looked up by the row this part belongs to
    #     (permeability, shape, product line) - the catalog fits per permeability,
    #     so the lookup is as narrow as the question and refuses when it cannot be
    if lookup and core.get("ve_mm3"):
        row, fit, why = _resolve_loss_fit(material, lookup.get("permeability"),
                                         lookup.get("shape"), lookup.get("variant"),
                                         freq_hz)
        if row is None:
            return None, ("no vendor loss fit could be resolved for this part: "
                          + str(why))
        sub = core_loss_steinmetz(freq_hz, b_ac_t, row["a"], row["c"], row["b"],
                                  units="khz_t", ve_mm3=core["ve_mm3"],
                                  waveform=waveform,
                                  duty=(duty if waveform != "sine" else None))
        if sub.get("status") != "ok":
            return None, ("the vendor fit for %s could not be evaluated: %s"
                          % (_loss_row_label(row), sub.get("message")))
        pv = sub["value"]
        note = ("vendor printed fit %s (a=%g, b=%g, c=%g; mW/cm3, T, kHz) x Ve"
                % (_loss_row_label(row), row["a"], row["b"], row["c"]))
        if waveform != "sine":
            note += " x iGSE shape ratio %g" % pv.get("ratio_to_sine", 1.0)
        return float(pv["core_loss_w"]), note
    # (3) the part's own measured reference point, scaled only with exponents
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
                flux_waveform="sine", duty=None, include_powder_cores=False,
                permeability=None, powder_data_path=None):
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

    `include_powder_cores` (default False) adds the powder-core toroids from
    data/powder_cores.json to the candidate pool, and `permeability` narrows them
    to one printed permeability - a powder part's AL depends on a permeability the
    part number does not spell out, so the material alone does not name a part.
    Off by default: the pool, the ordering and every number for the solid cores are
    then exactly what they were before this option existed.

    Two things change for a powder part and neither is a guess: its Bsat comes from
    the catalog's comparison page (one printed value per column, no temperature
    against it), and it has no gap to compute because the gap is distributed
    through the volume - AL can only be changed by choosing another permeability.

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
    cores = list(data.get("cores") or [])
    solids = len(cores)
    powder_info = None
    powder_mu = _f("permeability", permeability) if permeability is not None else None
    if include_powder_cores:
        pdata, pwhy = _load_powder_cores(powder_data_path)
        if pdata is None:
            return err(pwhy, "DATA_UNAVAILABLE",
                       hint="data/powder_cores.json ships with the package; drop "
                            "include_powder_cores if only the solid cores are wanted")
        try:
            powder = _powder_core_rows(pdata, material=material)
        except ValueError as exc:
            return err(str(exc), "ARG_INVALID")
        cores = cores + powder
        powder_info = {"parts": None, "solid_cores": solids,
                       "table": POWDER_PATH,
                       "retrieved": (pdata.get("source") or {}).get("retrieved")}
    elif permeability is not None:
        return err("permeability only applies to powder cores", "ARG_INVALID",
                   hint="a solid core's AL is fixed by its part number; pass "
                        "include_powder_cores=True to select a toroid by permeability")

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
    if powder_mu is not None:
        # the permeability narrows the powder column - it is not a statement that
        # the material is absent, so it is applied after the two checks above
        printed_mu = sorted({c.get("permeability") for c in cores
                             if c.get("permeability") is not None})
        cores = [c for c in cores if c.get("permeability") == powder_mu]
        if not cores:
            return err("no powder core is printed with permeability %g" % powder_mu,
                       "NOT_FOUND",
                       hint="the catalog prints one column per permeability; ask for "
                            "one of the permeabilities this material prints, or drop "
                            "permeability to see them all",
                       extra={"permeabilities_printed": printed_mu,
                              "material": material})
    if powder_info is not None:
        # the pool is counted after the narrow, so "parts" is the number of powder
        # parts this call actually considered, not the whole table
        powder_info["parts"] = sum(1 for c in cores if c.get("gap_is_distributed"))
        if powder_mu is not None:
            powder_info["permeability_filter"] = powder_mu

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
        if core.get("permeability") is not None:
            row["permeability"] = core.get("permeability")
            row["al_tolerance_pct"] = core.get("al_tolerance_pct")
        if core.get("material_variant"):
            row["material_variant"] = core["material_variant"]

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
        elif core.get("bsat_typical_t") is not None:
            limit_t = float(core["bsat_typical_t"]) * derate
            limit_note = ("%g%% of the %g T the catalog prints for the %s column "
                          "(printed p.10); that page prints no temperature against it"
                          % (derate * 100.0, float(core["bsat_typical_t"]),
                             core.get("material_column")))
            row["b_limit_temperature"] = ("not printed by the vendor - one typical "
                                          "value per column, not a 25 C or a 100 C "
                                          "number")
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
        elif core.get("bsat_typical_t") is not None:
            b_sat = float(core["bsat_typical_t"])
            row["bsat_t"] = _fmt(b_sat)
            row["saturation"] = "over" if b_peak > b_sat else "ok"
            row["saturation_note"] = ("peak %g mT of the %g mT the catalog prints for "
                                      "the %s column (no temperature printed for it)"
                                      % (b_peak * 1e3, b_sat * 1e3,
                                         core.get("material_column")))
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
            if core.get("gap_is_distributed"):
                row["gap_mm"] = None
                row["gap_because"] = core.get("gap_note") or (
                    "a powder core's gap is distributed, so there is nothing to cut")
            elif mu_i:
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
                rectx["missing_wire"] = (core.get("mlt_note") or
                                         "no mean turn length for this core in the "
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
                rectx["window_basis"] = (
                    ("the toroid's window is its printed inside-bore area, with fill "
                     "factor %g - a toroid is wound through the bore, so this is the "
                     "area the wire has to pass, and the vendor's geometry, the fill "
                     "factor is yours" % ku) if core.get("gap_is_distributed") else
                    ("former winding area with fill factor %g; "
                     "the former geometry is the vendor's, the "
                     "fill factor is yours" % ku))
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
            "and the extra copper loss near the gap is absent (magnetics_fringing_loss "
            "computes that copper loss; the flux, and with it the inductance, is not "
            "corrected here)",
            "winding AC resistance (magnetics_winding_ac) and skin effect",
            "tolerance: AL is quoted +/-25% on many parts, so the turns from AL are "
            "a nominal value, not a guarantee",
            "any Steinmetz fit for the ferrites: no vendor coefficients are stored, "
            "so a loss away from a measured point needs loss_exponents from you",
        ],
    }
    if powder_info:
        out["not_modelled"].extend([
            "a powder core's DC-bias roll-off: a powder core's AL falls with DC "
            "premagnetisation too, and the printed AL is the unbiased value - only the "
            "Bsat point is judged, exactly as for the solid cores",
            "copper loss for a toroid: the catalog prints no mean turn length, so this "
            "build does not estimate one",
            "fringing at a gap: a powder core has no discrete gap, so magnetics_fringing_loss "
            "does not apply to these parts",
        ])
        out["not_modelled"].append(
            "the mass column: Ve x density x permeability factor is an estimate, not the "
            "vendor's nominal part weight")
    if powder_info:
        out["core_pool"] = powder_info
        out["core_pool"]["note"] = ("powder parts carry a printed Bsat for their column "
                                    "and no discrete gap; their loss comes from the "
                                    "vendor's per-permeability toroid fit")
    if not feasible and powder_info and any(
            r.get("reason") == "saturation_unknown" for r in rejected):
        out["powder_cores_unjudged"] = (
            "no powder part could be judged on saturation: the catalog prints one "
            "typical Bsat per column with no temperature, and this build only uses it "
            "when the part carries `bsat_typical_t` - pass b_max_t to judge against a "
            "limit of your own instead")
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
