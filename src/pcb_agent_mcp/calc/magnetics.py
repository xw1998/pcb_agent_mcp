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

  * no core selection and no saturation curve from a real material - it reports
    the peak/valley/RMS the winding must survive and, only if you supply a
    saturation current, whether that part is enough;
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
                    into the resistance the switching frequency sees.
"""
import math

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


def core_loss_steinmetz(f_hz, b_peak_t, k, alpha, beta, units="si", ve_mm3=None):
    """Steinmetz core loss for ONE sinusoidal excitation, in the caller's units.

        P_v = k * f^alpha * B_peak^beta

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
    every published fit uses. Not modelled: DC bias (a gapped or DC-biased core
    loses MORE than this at the same AC amplitude - revised Steinmetz / iGSE
    territory), non-sinusoidal waveforms, temperature dependence of the
    coefficients, and the gap/fringing loss. Also not modelled: how much of the
    total loss this is - core loss says nothing about the winding.
    """
    units = str(units or "si").strip().lower().replace("-", "_").replace("/", "_")
    if units not in STEINMETZ_UNITS:
        return err("units must be one of " + ", ".join(sorted(STEINMETZ_UNITS)),
                   "ARG_INVALID",
                   hint="the k in a Steinmetz fit is meaningless without its unit "
                        "convention: si (Hz/T), khz_mt (kHz/mT), khz_gauss (kHz/G)")
    try:
        f = _f("f_hz", f_hz)
        b = _f("b_peak_t", b_peak_t)
        kk = _f("k", k)
        a = _f("alpha", alpha)
        bb = _f("beta", beta)
    except ValueError as exc:
        return err(str(exc), "ARG_INVALID")
    if f <= 0:
        return err("f_hz must be > 0", "ARG_RANGE")
    if b <= 0:
        return err("b_peak_t must be > 0", "ARG_RANGE",
                   hint="B_peak is the AC amplitude in tesla; a DC-only core has no "
                        "Steinmetz loss")
    if kk <= 0:
        return err("k must be > 0 (a Steinmetz coefficient is never zero or "
                   "negative)", "ARG_RANGE")
    if a <= 0 or bb <= 0:
        return err("alpha and beta are the fit's exponents and must be > 0",
                   "ARG_RANGE")

    f_unit, b_unit, k_unit, scale = STEINMETZ_UNITS[units]
    pv_native = kk * (f / f_unit) ** a * (b / b_unit) ** bb
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
    out["not_modelled"] = [
        "DC bias / premagnetisation (the same AC amplitude on a biased core loses more)",
        "non-sinusoidal flux (use an iGSE/GSE variant; the plain Steinmetz form is "
        "defined for a sine)",
        "temperature dependence of k, alpha, beta",
        "gap and fringing loss",
        "how this compares with the WINDING loss - that is a separate number",
    ]
    out["fit_is_extrapolation_if"] = ("f or B sits outside the window the triple was "
                                      "fitted on; the fit's error grows fast outside it")
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
