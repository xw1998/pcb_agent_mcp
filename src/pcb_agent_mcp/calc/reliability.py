"""Reliability: how long the solder joint lasts, and what a drop puts on it.

Two questions, three tools, and one rule that governs all of them: a number that
belongs to the part -- a fatigue ductility, a critical strain, an activation
energy -- is either an argument or it is named as a fit that was used. This file
invents no threshold and owns no "typical" allowable.

  * thermal cycling            -> cycles to crack        -> solder_joint_fatigue
        shear strain range from the CTE mismatch over the joint height:

            delta_gamma = F * DNP * (alpha_c - alpha_b) * delta_T / h

        then Engelmaier's strain-life form of Coffin-Manson:

            Nf = 0.5 * (delta_gamma / (2 eps_f)) ** (1/c)
            c  = -0.442 - 6e-4 Tbar_sj + 1.74e-2 ln(1 + 360 / t_D)

        with Miner's rule over several environments, and the Norris-Landzberg
        acceleration factor between a test condition and a use condition.

  * a drop or a shock pulse    -> board surface strain   -> drop_board_strain
        first plate mode (vibration.plate_modes) gives f1, a single-DOF shock
        response to the same pulse (vibration.shock_sdof) gives the peak
        deflection, and vibration.plate_stress turns that deflection into the
        surface strain. The verdict needs a critical strain the CALLER states --
        the strain a joint survives belongs to the component and its
        qualification report, not to this file.

  * what the pulse costs by frequency                    -> shock_spectrum
        the max-abs response of a bank of single-DOF oscillators, i.e. a shock
        response spectrum, computed by the same Newmark integrator.

What is NOT modelled, and is said so in every envelope: creep-fatigue
interaction beyond what Engelmaier's c already wraps up, underfill, voids and
IMC growth, the crack-propagation phase after initiation, corner counting,
bending direction (a drop has one), and -- on the drop path -- the component
mass, which lowers f1 and would therefore make this optimistic. The drop path is
also single-mode: a real board responds in several.

Everything is stdlib; the plate and shock solvers are imported, not re-derived,
so a fix in vibration.py reaches this file too.
"""
import math

from .result import ok, err
from . import vibration as _vib

K_B_EV = 8.617333262e-5          # eV/K

# Engelmaier's fatigue ductility coefficient. It is a FIT to SnPb solder
# strain-life data, not a material property: published SAC305 fits spread over
# roughly 0.2..0.35. So it is an argument with a labelled default, and whatever
# value was used comes back in the envelope.
EPS_F_DEFAULT = 0.325

# Norris-Landzberg: N = A f^n dT^-m exp(Ea / k T_max). The three constants are
# the widely used SnPb set (Norris & Landzberg 1969). SAC305 fits in the
# literature move m over ~1.9..2.65 and Ea over ~0.12..0.14 eV; they are
# arguments here, not constants of nature.
NL_M = 1.75
NL_N = 1.0 / 3.0
NL_EA_EV = 0.141

# In-plane CTE, ppm/K. These are TYPICAL values with a wide spread, and FR-4 is
# the worst offender: it is anisotropic (glass weave vs across), it steps at Tg,
# and the number below 100 C is nothing like the number above it.
ALPHA_PPM = {
    "fr4":        16.0,
    "fr4_above_tg": 60.0,
    "polyimide":  20.0,
    "copper":     17.0,
    "aluminum":   23.1,
    "silicon":     2.6,
    "alumina":     7.0,
    "ltcc":        6.0,
    "solder_sn63": 24.5,
    "solder_sac305": 21.0,
}

ALPHA_NOTE = ("preset = typical in-plane CTE; FR-4 is anisotropic and steps at Tg "
              "(16 ppm/K below vs ~60 above), so a large delta_T wants the "
              "above-Tg number or an effective value. Pass alpha_ppm to override")


def _num(x):
    try:
        v = float(x)
    except (TypeError, ValueError):
        return None
    if v != v or v in (float("inf"), float("-inf")):
        return None
    return v


def _pos(x):
    v = _num(x)
    return v if (v is not None and v > 0.0) else None


def _sig(x, digits=6):
    if x is None:
        return None
    if x == 0.0:
        return 0.0
    return float("%.*e" % (digits - 1, x))


def _alpha_of(ppm, preset, label):
    """Resolve an alpha given either as a number or as a preset name."""
    if ppm is not None:
        v = _num(ppm)
        if v is None:
            return None, None, err("%s must be a number in ppm/K" % label,
                                   "ARG_INVALID")
        return v, "stated", None
    key = str(preset or "").strip().lower()
    if key in ALPHA_PPM:
        return ALPHA_PPM[key], "preset:%s" % key, None
    return None, None, err(
        "give %s (ppm/K) or name a preset" % label, "ARG_REQUIRED",
        hint="known presets: %s" % ", ".join(sorted(ALPHA_PPM)))


# ------------------------------------------------------------- Engelmaier

def engelmaier_c(mean_joint_temp_c, dwell_min):
    """The fatigue ductility exponent of Engelmaier's strain-life form.

    c = -0.442 - 6e-4 * Tbar_sj + 1.74e-2 * ln(1 + 360 / t_D)

    Tbar_sj in C (the mean cyclic joint temperature, not the ambient), t_D the
    half-cycle dwell in minutes. Both terms make c MORE negative: a longer dwell
    and a hotter joint each shave Nf. That is the point of the model -- with
    time at temperature the joint creeps and the whole strain range is spent as
    inelastic work, so a slow cycle with dwell is worse than the same range run
    quickly. (1/c is negative, so a more negative c also means a steeper
    strain-life slope.)

    This is a fit: -0.442 is the extrapolated Tbar=0, t_D -> inf limit.
    """
    return -0.442 - 6e-4 * mean_joint_temp_c + 1.74e-2 * math.log(
        1.0 + 360.0 / dwell_min)


def engelmaier_nf(delta_gamma, c, eps_f=EPS_F_DEFAULT):
    """Nf(50%) from a shear strain range, Engelmaier form of Coffin-Manson."""
    return 0.5 * (delta_gamma / (2.0 * eps_f)) ** (1.0 / c)


# ------------------------------------------------------ solder_joint_fatigue

_COND_DEFAULTS = {"mean_joint_temp_c": 25.0, "dwell_min": 15.0}


def solder_joint_fatigue(conditions=None, joint_height_mm=None,
                         delta_t_k=None, mean_joint_temp_c=None,
                         dwell_min=None, cycles=None,
                         alpha_component_ppm=None, alpha_board_ppm=None,
                         alpha_component=None, alpha_board=None,
                         dnp_mm=None, body_a_mm=None, body_b_mm=None,
                         fatigue_ductility=None, compliance_factor=None,
                         nl_test=None, nl_use=None, nl_m=NL_M, nl_n=NL_N,
                         nl_ea_ev=NL_EA_EV):
    """Thermal-cycling life of a solder joint from the CTE mismatch.

    The strain range is geometry first:

        delta_gamma = F * DNP * (alpha_c - alpha_b) * delta_T / h

    with DNP the distance from the neutral point to the joint being judged (the
    diagonal corner for an area array), h the joint height (the solder standoff,
    i.e. the gap the mismatch has to be absorbed across), and F a compliance
    factor (1.0 = the joint absorbs all of it; below 1 for compliant leads or
    underfill, and F is a FIT, say so if you move it).

    conditions: a list of cyclic environments, each
        {"name": str, "delta_t_k": K, "mean_joint_temp_c": C, "dwell_min": min,
         "cycles": n per block, "blocks_per_year": n}
      Only delta_t_k is mandatory in a condition. The first entry is the primary
      one; every entry gets its own Nf and damage, and Miner's rule combines
      them. A bare delta_t_k argument builds the single condition "stated".

    dnp_mm, or body_a_mm + body_b_mm (then DNP = the diagonal corner,
    sqrt((a/2)^2 + (b/2)^2) from the package centre).

    nl_test / nl_use: optional {"delta_t_k", "frequency_cpd", "t_max_c"} pairs.
        When both are given the Norris-Landzberg acceleration factor is applied
        and the test life is converted into a use-condition life. The frequency
        here is cycles per day INCLUDING dwell; it is a different knob from the
        dwell inside Engelmaier's c, and the two are not interchangeable.
    """
    h = _pos(joint_height_mm)
    if h is None:
        return err("joint_height_mm is required and must be > 0",
                   "ARG_REQUIRED",
                   hint="the solder standoff / gap under the component: the strain "
                        "is the mismatch displacement divided by this height, so it "
                        "is the single most sensitive input here")

    # ---- distance from the neutral point
    dnp = _pos(dnp_mm)
    dnp_source = "stated"
    if dnp is None:
        a = _pos(body_a_mm)
        b = _pos(body_b_mm)
        if a is not None and b is not None:
            dnp = math.hypot(a * 0.5, b * 0.5)
            dnp_source = "diagonal corner of a %.4g x %.4g mm body" % (a, b)
        else:
            return err("give dnp_mm, or body_a_mm and body_b_mm",
                       "ARG_REQUIRED",
                       hint="DNP is the distance from the neutral point to the joint "
                            "you are judging - the diagonal corner is the worst one "
                            "on an area array")

    ac, ac_src, e = _alpha_of(alpha_component_ppm, alpha_component,
                              "alpha_component_ppm")
    if e is not None:
        return e
    ab, ab_src, e = _alpha_of(alpha_board_ppm, alpha_board, "alpha_board_ppm")
    if e is not None:
        return e
    dalpha_ppm = ac - ab

    eps_f = EPS_F_DEFAULT if fatigue_ductility is None else _num(fatigue_ductility)
    if eps_f is None or eps_f <= 0.0:
        return err("fatigue_ductility must be > 0", "ARG_INVALID")
    comp = 1.0 if compliance_factor is None else _num(compliance_factor)
    if comp is None or comp <= 0.0:
        return err("compliance_factor must be > 0", "ARG_INVALID")

    # ---- assemble the condition list
    if conditions is None:
        if delta_t_k is None:
            return err("give conditions, or a single delta_t_k", "ARG_REQUIRED",
                       hint='conditions=[{"name": "day", "delta_t_k": 100, '
                            '"cycles": 1, "blocks_per_year": 365}]')
        conditions = [{"name": "stated", "delta_t_k": delta_t_k,
                       "mean_joint_temp_c": mean_joint_temp_c,
                       "dwell_min": dwell_min, "cycles": cycles}]
    if not isinstance(conditions, (list, tuple)) or not conditions:
        return err("conditions must be a non-empty list", "ARG_INVALID")

    rows = []
    raw = []          # unrounded (c, nf, damage) for the self-checks
    for idx, cond in enumerate(conditions):
        if not isinstance(cond, dict):
            return err("condition %d must be an object" % idx, "ARG_INVALID")
        dT = _pos(cond.get("delta_t_k"))
        if dT is None:
            return err("condition %r needs delta_t_k > 0" % (cond.get("name"),),
                       "ARG_REQUIRED")
        tbar = _num(cond.get("mean_joint_temp_c"))
        if tbar is None:
            tbar = _COND_DEFAULTS["mean_joint_temp_c"]
        dwell = _pos(cond.get("dwell_min"))
        if dwell is None:
            dwell = _COND_DEFAULTS["dwell_min"]
        c = engelmaier_c(tbar, dwell)
        # the sign of the mismatch only decides which side is in compression; the
        # strain range is a magnitude, so take the absolute difference
        dgamma = comp * dnp * (abs(dalpha_ppm) * 1e-6) * dT / h
        if dgamma <= 0.0:
            return err("shear strain range came out <= 0: the component and the "
                       "board have the same CTE, or delta_T is zero", "ARG_RANGE",
                       hint="nothing to fatigue if nothing moves")
        nf = engelmaier_nf(dgamma, c, eps_f)
        ncyc = _num(cond.get("cycles"))
        bpy = _num(cond.get("blocks_per_year"))
        damage = (ncyc / nf) if (ncyc is not None and nf > 0) else None
        years = None
        if damage and damage > 0.0 and bpy and bpy > 0.0:
            years = 1.0 / (damage * bpy)
        row = {
            "name": cond.get("name", "condition %d" % (idx + 1)),
            "delta_t_k": _sig(dT),
            "mean_joint_temp_c": _sig(tbar),
            "dwell_min": _sig(dwell),
            "engelmaier_c": _sig(c),
            "shear_strain_range": _sig(dgamma),
            "cycles_to_failure": _sig(nf),
            "cycles": None if ncyc is None else _sig(ncyc),
            "damage_per_block": None if damage is None else _sig(damage),
            "blocks_per_year": None if bpy is None else _sig(bpy),
            "years_to_failure": None if years is None else _sig(years),
            "verdict": None if damage is None else (
                "fail" if damage >= 1.0 else "pass"),
            "_defaulted": [k for k, v in
                           (("mean_joint_temp_c", cond.get("mean_joint_temp_c")),
                            ("dwell_min", cond.get("dwell_min")))
                           if v is None],
        }
        rows.append(row)
        raw.append((c, nf, damage))

    total = sum(r["damage_per_block"] for r in rows
                if r["damage_per_block"] is not None)
    any_damage = any(r["damage_per_block"] is not None for r in rows)
    miner = {"conditions_with_cycle_counts": sum(
                 1 for r in rows if r["damage_per_block"] is not None),
             "total_damage_per_block": _sig(total) if any_damage else None,
             "blocks_to_failure": _sig(1.0 / total) if total > 0.0 else None}
    per_year = [r["damage_per_block"] * r["blocks_per_year"] for r in rows
                if r["damage_per_block"] is not None
                and r["blocks_per_year"] is not None]
    if per_year:
        rate = sum(per_year)
        miner["years_to_failure"] = _sig(1.0 / rate) if rate > 0 else None
        miner["basis"] = ("damage per YEAR summed over the conditions that state "
                          "blocks_per_year")

    # ---- Norris-Landzberg
    nl = None
    if nl_test is not None or nl_use is not None:
        if not (isinstance(nl_test, dict) and isinstance(nl_use, dict)):
            return err("nl_test and nl_use must both be given, and both be objects",
                       "ARG_REQUIRED",
                       hint='{"delta_t_k": 165, "frequency_cpd": 2, '
                            '"t_max_c": 125}')
        parsed = {}
        for key, spec in (("test", nl_test), ("use", nl_use)):
            dT = _pos(spec.get("delta_t_k"))
            fr = _pos(spec.get("frequency_cpd"))
            tx = _num(spec.get("t_max_c"))
            if dT is None or fr is None or tx is None:
                return err("nl_%s needs delta_t_k > 0, frequency_cpd > 0 and "
                           "t_max_c" % key, "ARG_REQUIRED")
            parsed[key] = {"delta_t_k": dT, "frequency_cpd": fr,
                           "t_max_c": tx}
        m = NL_M if nl_m is None else _num(nl_m)
        n = NL_N if nl_n is None else _num(nl_n)
        ea = NL_EA_EV if nl_ea_ev is None else _num(nl_ea_ev)
        if m is None or n is None or ea is None:
            return err("nl_m, nl_n and nl_ea_ev must be numbers", "ARG_INVALID")
        t = parsed["test"]
        u = parsed["use"]
        af = _nl_af(t, u, m, n, ea)
        nf_test = engelmaier_nf(
            comp * dnp * (abs(dalpha_ppm) * 1e-6) * t["delta_t_k"] / h,
            engelmaier_c(t["t_max_c"], _COND_DEFAULTS["dwell_min"]), eps_f)
        nl = {
            "c_dwell": ("the test-condition Nf uses Engelmaier's c at t_max_c with "
                         "the default %.4g min dwell, which is NOT the same as the "
                         "dwell the test really ran" % _COND_DEFAULTS["dwell_min"]),
            "test": {"delta_t_k": _sig(t["delta_t_k"]),
                     "frequency_cpd": _sig(t["frequency_cpd"]),
                     "t_max_c": _sig(t["t_max_c"])},
            "use": {"delta_t_k": _sig(u["delta_t_k"]),
                    "frequency_cpd": _sig(u["frequency_cpd"]),
                    "t_max_c": _sig(u["t_max_c"])},
            "constants": {"m": _sig(m), "n": _sig(n), "ea_ev": _sig(ea)},
            "acceleration_factor": _sig(af),
            "cycles_to_failure_at_test": _sig(nf_test),
            "cycles_to_failure_equivalent_at_use": _sig(nf_test * af),
            "self_check_af_identity": _sig(abs(_nl_af(u, u, m, n, ea) - 1.0)),
            "note": ("AF = N_use / N_test, from N ~ f^n dT^-m exp(Ea/kT). Only the "
                     "exponent laws cross over; the Engelmaier c of the test "
                     "condition is used for Nf_test, which is not the same as a "
                     "full re-fit at the use condition"),
        }

    # ---- self-checks: the closed forms must reproduce their own identities
    c_probe, nf_probe, dmg_probe = raw[0]
    nf_unit = engelmaier_nf(2.0 * eps_f, c_probe, eps_f)
    slope = None
    if c_probe:
        lo = engelmaier_nf(0.01, c_probe, eps_f)
        hi = engelmaier_nf(0.02, c_probe, eps_f)
        if lo > 0 and hi > 0:
            slope = math.log(hi / lo) / math.log(2.0) - 1.0 / c_probe
    self_check = {
        "c_at_zero_temp_infinite_dwell": _sig(engelmaier_c(0.0, 1e12) + 0.442),
        "nf_at_unit_strain_ratio": _sig(nf_unit),
        "log_log_slope_minus_1_over_c": _sig(slope) if slope is not None else None,
    }
    if dmg_probe is not None and len(rows) == 1:
        self_check["miner_single_condition_times_damage"] = _sig(
            dmg_probe * (1.0 / dmg_probe) - 1.0)

    defaults_used = sorted({k for r in rows for k in r["_defaulted"]})
    for r in rows:
        del r["_defaulted"]

    assumptions = {
        "geometry": ALPHA_NOTE,
        "dnp": "the joint judged sits at DNP from the neutral point; the diagonal "
               "corner is the worst one on an area array",
        "strain": "the whole mismatch displacement is absorbed as shear across the "
                  "joint height; compliance_factor below 1 is a fit, not physics",
        "ductility": "fatigue_ductility = %g is Engelmaier's SnPb fit; SAC305 fits "
                     "in the literature spread ~0.2..0.35" % eps_f,
        "not_modelled": ["creep-fatigue interaction beyond c",
                         "underfill, voids, IMC growth",
                         "crack propagation after initiation",
                         "bending/warpage of the assembly",
                         "transient thermal gradients inside the joint"],
    }
    if defaults_used:
        assumptions["defaulted"] = (
            "%s not stated for at least one condition and filled with 25 C / 15 min; "
            "say them yourself before quoting a life" % ", ".join(defaults_used))

    return ok(
        {
            "geometry": {"dnp_mm": _sig(dnp), "dnp_source": dnp_source,
                         "joint_height_mm": _sig(h),
                         "compliance_factor": _sig(comp)},
            "delta_alpha_ppm": _sig(dalpha_ppm),
            "alpha_sources": {"component": ac_src, "board": ab_src},
            "shear_strain_range": _sig(comp * dnp * (abs(dalpha_ppm) * 1e-6)
                                       * float(conditions[0]["delta_t_k"]) / h),
            "conditions": rows,
            "miner": miner,
            "norris_landzberg": nl,
            "self_check": self_check,
            "formula": ("delta_gamma = F*DNP*dalpha*delta_T/h ; "
                        "Nf = 0.5*(delta_gamma/(2*eps_f))**(1/c) ; "
                        "c = -0.442 - 6e-4*Tbar + 1.74e-2*ln(1+360/t_D)"),
        },
        "cycles",
        "Engelmaier strain-life on a CTE-mismatch shear strain, Miner for multiple "
        "environments, Norris-Landzberg to move between test and use",
        "shear strain range = mismatch displacement over the joint standoff; "
        "Engelmaier's strain-life form of the Coffin-Manson law; Miner's linear "
        "damage; Norris-Landzberg N ~ f^n dT^-m exp(Ea/kT)",
        "arithmetic in closed form; every identity it uses is re-checked in "
        "self_check (c at the zero-temperature/infinite-dwell limit, Nf at unit "
        "normalised strain, the log-log slope against 1/c, Miner's own inverse, "
        "and AF(x,x) = 1). The physical accuracy is limited by the strain model, "
        "not by the arithmetic",
        assumptions,
        {"primary_condition": rows[0]["name"],
         "nf_probe": _sig(nf_probe)},
    )


def _nl_af(test, use, m, n, ea):
    """Norris-Landzberg acceleration factor N_use / N_test."""
    dt = (test["delta_t_k"] / use["delta_t_k"]) ** m
    fr = (use["frequency_cpd"] / test["frequency_cpd"]) ** n
    t_use = use["t_max_c"] + 273.15
    t_test = test["t_max_c"] + 273.15
    ex = math.exp(ea / K_B_EV * (1.0 / t_use - 1.0 / t_test))
    return dt * fr * ex


# ------------------------------------------------------- drop_board_strain

def drop_board_strain(a_mm, b_mm, thickness_mm, pulse, material=None,
                      e_gpa=None, nu=None, rho_kg_m3=None, mount="corners",
                      bc_x=None, bc_y=None, zeta=None, q_factor=None, mode=1,
                      poly_degree=None, critical_strain_ue=None,
                      dnp_mm=None, joint_height_mm=None):
    """Board surface strain under a drop/shock pulse, from three verified pieces.

    Step 1  vibration.plate_modes      -> f1 (the board's own first mode)
    Step 2  vibration.shock_sdof       -> peak deflection of that mode under the
                                          pulse, via the single-DOF equivalent
    Step 3  vibration.plate_stress     -> surface strain at that deflection

    This is a single-mode chain, and the assumption that makes it work is stated
    rather than hidden: the plate's first mode is treated as one SDOF oscillator
    of the same frequency and damping, so its peak modal amplitude IS the peak
    deflection at the antinode. Component mass is not in the plate (it would
    lower f1 and raise the response), so treat `freq_hz` as an upper bound.

    critical_strain_ue: the strain the parts survive, in microstrain, as
        qualified (JESD22-B111 / B113 style). This tool will NOT supply one --
        it belongs to the component and its qualification report. Without it you
        get the strain and `judged: false`.

    dnp_mm + joint_height_mm: optional, and then the joint shear strain follows
        as gamma = eps_board * DNP / h, i.e. the board stretches by eps*DNP over
        that distance and the joint absorbs it. Assumes a rigid component and no
        lead compliance; a gull-wing lead is exactly the case where this
        overstates.
    """
    st = _pos(critical_strain_ue)
    if critical_strain_ue is not None and st is None:
        return err("critical_strain_ue must be > 0", "ARG_INVALID")

    kw = {"material": material, "e_gpa": e_gpa, "nu": nu, "rho_kg_m3": rho_kg_m3,
          "mount": mount, "bc_x": bc_x, "bc_y": bc_y}
    if poly_degree is not None:
        kw["poly_degree"] = poly_degree
    r = _vib.plate_modes(a_mm, b_mm, thickness_mm,
                         n_modes=max(2, int(mode or 1)), **kw)
    if r.get("status") == "error":
        return r
    plate = r["value"]
    midx = int(mode or 1)
    if len(plate["modes"]) < midx:
        return err("mode %d does not exist on this plate" % midx, "ARG_RANGE")
    md = plate["modes"][midx - 1]
    if md.get("rigid_body"):
        return err("mode %d is a rigid-body mode: no curvature, no strain, and no "
                   "useful shock response" % midx, "ARG_RANGE",
                   hint="a free or corner-supported plate has up to 3 modes at 0 Hz")
    f1 = md["freq_hz"]

    sh = _vib.shock_sdof(f1, zeta=zeta, q_factor=q_factor, pulse=pulse)
    if sh.get("status") == "error":
        return sh
    shock = sh["value"]
    z_mm = shock["peak_relative_displacement_mm"]
    if not z_mm or z_mm <= 0:
        return err("the shock response gave no deflection; the pulse has no content "
                   "at this frequency", "ARG_RANGE")

    pr = _vib.plate_stress(a_mm, b_mm, thickness_mm, amplitude_mm=z_mm, mode=midx,
                           **kw)
    if pr.get("status") == "error":
        return pr
    press = pr["value"]
    eps_ue = press["surface_strain_ue"]

    joint = None
    dnp = _pos(dnp_mm)
    hj = _pos(joint_height_mm)
    if dnp is not None and hj is not None:
        joint = {
            "dnp_mm": _sig(dnp),
            "joint_height_mm": _sig(hj),
            "shear_strain": _sig((eps_ue * 1e-6) * dnp / hj),
            "note": ("gamma = eps_board * DNP / h. Rigid component, no lead "
                     "compliance: a gull-wing lead bends instead of shearing the "
                     "joint, so this OVERSTATES the strain there"),
        }
    elif dnp_mm is not None or joint_height_mm is not None:
        return err("dnp_mm and joint_height_mm go together", "ARG_REQUIRED")

    judged = False
    verdict = None
    margin = None
    if st is not None:
        judged = True
        verdict = "pass" if eps_ue <= st else "fail"
        margin = (st / eps_ue) if eps_ue > 0 else None

    warnings = list(press.get("warnings") or [])
    if not judged:
        warnings.append(
            "no critical_strain_ue was given, so this is a strain, not a verdict: "
            "the strain a joint survives belongs to the component and its "
            "qualification report (JESD22-B111 / B113 style), and this server owns "
            "no such number")
    if press.get("converged") is False:
        warnings.append("the plate stress is not converged (degree ladder spread "
                        "%.3g %%), so the strain scales with that error"
                        % (press.get("degree_sensitivity_pct") or 0.0))

    return ok(
        {
            "freq_hz": f1,
            "mode": midx,
            "pulse": shock["pulse"],
            "damping": {"zeta": shock["zeta"]},
            "static_displacement_mm": shock["static_displacement_mm"],
            "peak_deflection_mm": _sig(z_mm),
            "displacement_amplification": shock["displacement_amplification"],
            "peak_absolute_acceleration_g": shock["peak_absolute_acceleration_g"],
            "peak_time_ms": shock["peak_time_ms"],
            "von_mises_mpa": press["von_mises_mpa"],
            "von_mises_at_mm": press["von_mises_at_mm"],
            "curvature_per_m": press["curvature_per_m"],
            "surface_strain_ue": eps_ue,
            "joint": joint,
            "judged": judged,
            "critical_strain_ue": None if st is None else _sig(st),
            "verdict": verdict,
            "strain_margin": None if margin is None else _sig(margin),
            "degree_sensitivity_pct": press.get("degree_sensitivity_pct"),
            "converged": press.get("converged"),
            "warnings": warnings,
        },
        "ue",
        "single-mode plate response to a base-acceleration pulse, curvature to "
        "surface strain",
        "Kirchhoff thin plate (Rayleigh-Ritz modes) driven by a single-DOF Newmark "
        "shock response; surface strain from vibration.plate_stress, "
        "eps = sigma/E at z = h/2",
        "the chain is three already-verified solvers; the error bar that matters is "
        "the plate curvature's own degree-ladder spread, which is reported as "
        "degree_sensitivity_pct. Single mode only, component mass ignored, linear "
        "plate theory (invalid past ~half the thickness)",
        {"single_mode": "the first plate mode is treated as one SDOF oscillator; its "
                        "peak modal amplitude is taken as the peak deflection at the "
                        "antinode",
         "mass": "component mass is not in the plate, so f1 is high and the "
                 "predicted response is optimistic",
         "amplitude": "linear plate theory; past amplitude ~ h/2 the stress is "
                      "overstated",
         "pulse": "a drop is directional and the board does not respond in only one "
                  "mode; real drop qualification is a test, not this"},
        {"plate": {"freq_hz": f1, "material": plate["material"], "bc": plate["bc"],
                   "thickness_mm": plate["thickness_mm"]},
         "shock_integration": shock["integration"]},
    )


# ---------------------------------------------------------- shock spectrum

SPECTRUM_MAX_POINTS = 200


def shock_spectrum(pulse, f_n_hz=None, f_start_hz=None, f_stop_hz=None,
                   n_points=None, zeta=None, q_factor=None):
    """Shock response spectrum: the max-abs answer of a bank of SDOF oscillators.

    This is the standard way to state what a drop actually presents to a board:
    sweep the oscillator frequency and record the peak, so you can then read off
    the value at the board's own f1 (from vibration.plate_modes) and see how far
    the board sits from the shock's worst frequency.

    Convention: SRS is normally quoted at 5 % damping, which is the default here
    and is stated, not hidden. Give zeta or q_factor to move it.

    Returns, per frequency: the peak absolute acceleration (zpa), the pseudo
    velocity 2*pi*f*z and the peak relative displacement.
    """
    z_in = zeta
    if z_in is None and q_factor is None:
        z_in = 0.05
    if not isinstance(pulse, dict):
        return err("pulse must be an object", "ARG_REQUIRED",
                   hint='{"type": "half_sine", "amplitude_g": 1500, '
                        '"duration_ms": 0.5}')

    freqs = []
    if f_n_hz is not None:
        if not isinstance(f_n_hz, (list, tuple)) or not f_n_hz:
            return err("f_n_hz must be a non-empty list of frequencies",
                       "ARG_INVALID")
        for f in f_n_hz:
            v = _pos(f)
            if v is None:
                return err("every f_n_hz entry must be > 0", "ARG_INVALID")
            freqs.append(v)
    else:
        lo = _pos(f_start_hz)
        hi = _pos(f_stop_hz)
        if lo is None or hi is None:
            return err("give f_n_hz, or f_start_hz and f_stop_hz", "ARG_REQUIRED")
        if hi <= lo:
            return err("f_stop_hz must be above f_start_hz", "ARG_RANGE")
        n = int(n_points or 24)
        if n < 2 or n > SPECTRUM_MAX_POINTS:
            return err("n_points must be in 2..%d" % SPECTRUM_MAX_POINTS,
                       "ARG_INVALID")
        # log sweep: an SRS is read on a log axis, and a linear sweep either
        # wastes most of its points up high or misses the low-frequency plateau
        ratio = (hi / lo) ** (1.0 / (n - 1))
        freqs = [lo * ratio ** i for i in range(n)]

    rows = []
    peak_zpa = 0.0
    peak_at = None
    for f in freqs:
        r = _vib.shock_sdof(f, zeta=z_in, q_factor=q_factor, pulse=pulse)
        if r.get("status") == "error":
            return r
        v = r["value"]
        zpa = v["peak_absolute_acceleration_g"]
        disp = v["peak_relative_displacement_mm"]
        rows.append({"freq_hz": _sig(f, 5), "zpa_g": zpa,
                     "pv_mm_s": _sig(2.0 * math.pi * f * disp, 5),
                     "disp_mm": disp})
        if zpa > peak_zpa:
            peak_zpa = zpa
            peak_at = f

    amp = _num(pulse.get("amplitude_g"))
    zeta_used, e = _vib._sdof_zeta(q_factor, z_in)
    if e is not None:
        return e
    self_check = {
        "zpa_over_pulse_peak_max": _sig(peak_zpa / amp) if amp else None,
        "undamped_bound_respected": (None if amp is None else
                                     bool(peak_zpa <= 2.0 * amp * 1.02)),
        "lowest_freq_zpa_g": rows[0]["zpa_g"],
    }
    # zeta = 0 exactly is refused by the single-DOF solver (an undamped mode is a
    # resonance, not a damping ratio), so "near zero" is what this can check
    if str(pulse.get("type", "")).strip().lower() == "step" and not q_factor \
            and zeta is not None and 0.0 < float(zeta) <= 1e-4:
        self_check["step_near_zero_damping_zpa_over_pulse_peak"] = _sig(
            rows[0]["zpa_g"] / amp) if amp else None

    warnings = []
    if str(pulse.get("type", "")).strip().lower() == "step":
        warnings.append("a step never ends, so every point in this spectrum is the "
                        "quasi-static plus dynamic answer; a real drop is a pulse "
                        "-- use half_sine")
    if amp is None:
        warnings.append("pulse.amplitude_g is missing or not a number: the ratios "
                        "are meaningless")

    return ok(
        {
            "damping": {"zeta": _sig(zeta_used),
                        "defaulted": (zeta is None and q_factor is None),
                        "convention": ("5 % damping is the SRS convention; it is a "
                                       "convention, not a property of your board"
                                       if (zeta is None and q_factor is None)
                                       else "as given")},
            "pulse": {"type": str(pulse.get("type", "")),
                      "amplitude_g": amp,
                      "duration_ms": pulse.get("duration_ms")},
            "points": rows,
            "peak_zpa_g": _sig(peak_zpa),
            "peak_at_hz": _sig(peak_at),
            "self_check": self_check,
            "warnings": warnings,
            "reading": ("read the board's own f1 off this curve: at f1 much below "
                        "the pulse's 1/duration the answer is the quasi-static "
                        "g_peak, and at f1 well above it the board stops following "
                        "the pulse and the joint sees displacement, not force"),
        },
        "g",
        "shock response spectrum from single-DOF Newmark integration, one "
        "oscillator per frequency",
        "SRS = max |absolute acceleration| of a single-DOF oscillator over the "
        "response window, per frequency; same integrator as vibration.shock_sdof",
        "each point is the same Newmark solve the single-frequency tool uses "
        "(dt <= T_n/24, unconditionally stable); the spectrum as a whole is "
        "checked against the undamped 2x boundedness identity and, for a step at "
        "zero damping, against the analytic DLF of exactly 2",
        {"single_dof": "each frequency is an independent SDOF oscillator; this is "
                       "the definition of an SRS, not an approximation of it",
         "response_window": "3 periods or twice the pulse length, whichever is "
                            "longer - a longer tail would only add decay",
         "not_a_drop_test": "an SRS does not say a part fails; it says what the "
                            "pulse presents at each frequency"},
        {"frequencies_hz": len(freqs)},
    )
