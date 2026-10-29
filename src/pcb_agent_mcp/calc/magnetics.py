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

What it does, in three entry points:

  inductor_ccm      the CCM design point (duty, L, Ipk/Ivalley/Irms), plus the
                    winding loss if you supply `dcr_ohm`;
  duty_dcm          the DCM steady state (a different duty, a second interval
                    where the diode carries the inductor current), refused
                    outright when the load is really in CCM;
  core_loss_steinmetz
                    Steinmetz P_v = k*f^alpha*B^beta with the coefficients YOU
                    supply and the unit convention NAMED, because the published
                    fits differ by ~10^12 in k purely through units,
                    and a Steinmetz fit outside its (f, B) window is a guess.
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
