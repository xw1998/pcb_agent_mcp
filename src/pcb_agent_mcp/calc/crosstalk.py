"""Coupled-line crosstalk: 2-D field solution -> modal parameters -> time domain.

Why this module exists: every other answer this server gives about a high-speed
pair is a SINGLE line. `trace_impedance` knows nothing about the trace next to
it, and `diff_impedance_field` solves the odd and even modes only to take their
mean and call it "one line". So the most asked PCB question of all - "how much
of my 1 V edge lands on the neighbour" - had no answer here, and the usual
replies are either a rule of thumb ("3W") or a closed form that hides its
assumptions. This module answers it in three steps, each of which the caller can
re-derive:

1. The cross-section is SOLVED, not fitted: `field2d` gives the odd-mode and
   even-mode capacitance of a symmetric pair, both with the real dielectric and
   with vacuum. Four numbers, no closed form in the path.

2. Those four numbers become the two-mode transmission line. With
   Cg = line-to-ground, Cm = mutual, C11 = Cg + Cm:

       odd mode   C = C11 + Cm,  L = L11 - Lm,  Z_odd  = sqrt(L_o/C_o)
       even mode  C = C11 - Cm,  L = L11 + Lm,  Z_even = sqrt(L_e/C_e)

   and the inductances come from the VACUUM capacitances, L = mu0/c_air, which
   is the quasi-static statement that a non-magnetic board does not change the
   magnetic field. That is also where the microstrip's unequal modal velocities
   come from: er_eff differs between the modes, so v_even != v_odd.

3. The coupled telegrapher's equations are then solved in TIME, exactly, for a
   trapezoidal source. With identical terminations on both lines the modal
   circuits decouple, so each mode is a plain 1-D line (impedance Z_m, delay
   T_m) driven through R_s and loaded with R_l, and the answer is the bounce
   series of that line - no weak-coupling approximation, no saturation assumed,
   no long-line assumption. The physical voltages are recovered with
   V1 = (V_even + V_odd)/sqrt2, V2 = (V_even - V_odd)/sqrt2.

What the two answers mean, measured (see tests/test_crosstalk.py):
  * near end (NEXT) rises during 0..2T to a plateau and then decays; the
    plateau is Kb * V_incident with Kb = (1/4)(Lm/L11 + Cm/C11) only while the
    coupling is weak - here the same simulation reproduces that formula to
    <0.5 % at k=1.6 % and misses it by 2x at k=26 %, which is why the tool
    reports the simulated peak and the formula side by side instead of quoting
    the formula as the answer;
  * far end (FEXT) is a pulse of width ~tr, and it is EXACTLY zero when the two
    modal velocities are equal - i.e. for any homogeneous dielectric. The
    simulation reproduces that as a cancellation (stripline: <0.02 % of the
    source), which is the check that a wrong modal split cannot pass.

What this is not: a mode solver. Quasi-TEM (the same assumption every impedance
number in this server carries), no dispersion, no radiation, no copper loss, no
solder mask, no third line, no via or connector discontinuity, and the two lines
must be terminated with the same impedance at each end (that is what makes the
modal circuits decouple - a victim driven by a low-impedance buffer and an
aggressor by 50 ohm is NOT covered, and the answer says so).
"""
import math

from .result import ok, err
from . import field2d as _f2d

C0 = 299792458.0
EPS0 = 8.8541878128e-12
MU0 = 4e-7 * math.pi
S2 = math.sqrt(2.0)

# refuse rather than grind: the time loop is O(steps) in python
MAX_STEPS = 400000


def _sig(x, digits=6):
    if x == 0.0 or x is None:
        return x
    return float("%.*g" % (digits, x))


def modal_from_caps(c_odd_d, c_even_d, c_odd_a, c_even_a):
    """Four capacitance shape factors -> the two-mode line parameters.

    The naming is the field solver's: "odd" is the electric wall at the symmetry
    plane (the two traces at +V/-V), "even" the magnetic wall (both at +V), and
    each number is the capacitance PER LINE in that excitation. `_d` is with the
    real dielectric, `_a` with vacuum.

    The identities used, in one place so they can be checked:
      C11 = Cg + Cm      with Cg = c_even*eps0 (the mutual capacitor carries no
                         charge when both lines are at the same potential)
      Cm  = (c_odd - c_even)*eps0/2                                (ditto)
      L11 +- Lm = mu0 / c_{even,odd}_a   (a non-magnetic medium does not change
                         the inductance, so the VACUUM capacitance is the one to
                         invert)
    """
    cg_d = c_even_d * EPS0
    cm_d = 0.5 * (c_odd_d - c_even_d) * EPS0
    cg_a = c_even_a * EPS0
    cm_a = 0.5 * (c_odd_a - c_even_a) * EPS0
    c11 = cg_d + cm_d
    c11_a = cg_a + cm_a
    l_odd = MU0 / c_odd_a
    l_even = MU0 / c_even_a
    l11 = 0.5 * (l_odd + l_even)
    lm = 0.5 * (l_even - l_odd)
    c_odd = c11 + cm_d
    c_even = c11 - cm_d
    z_odd = math.sqrt(l_odd / c_odd)
    z_even = math.sqrt(l_even / c_even)
    v_odd = 1.0 / math.sqrt(l_odd * c_odd)
    v_even = 1.0 / math.sqrt(l_even * c_even)
    return {
        "cg_f_per_m": cg_d, "cm_f_per_m": cm_d,
        "cg_air_f_per_m": cg_a, "cm_air_f_per_m": cm_a,
        "c11_f_per_m": c11, "c11_air_f_per_m": c11_a,
        "l11_h_per_m": l11, "lm_h_per_m": lm,
        "c_odd_f_per_m": c_odd, "c_even_f_per_m": c_even,
        "l_odd_h_per_m": l_odd, "l_even_h_per_m": l_even,
        "z_odd_ohm": z_odd, "z_even_ohm": z_even,
        "v_odd_m_s": v_odd, "v_even_m_s": v_even,
        # er_eff is (c0/v)^2, which is identically c_dielectric/c_air for each
        # mode - so it is reported straight from the two capacitances rather
        # than through a square of a square root. (Writing c0/v here instead
        # gives sqrt(er_eff), which a homogeneous stripline exposes immediately:
        # it would report 2.0736 for an er = 4.3 board.)
        "er_eff_odd": c_odd_d / c_odd_a, "er_eff_even": c_even_d / c_even_a,
        "z_isolated_ohm": math.sqrt(l11 / c11),
        "k_c": cm_d / c11, "k_l": lm / l11,
        "kb_first_order": 0.25 * (lm / l11 + cm_d / c11),
    }


def _run_mode(zm, tm, rs, rl, src_modal, dt, n):
    """One decoupled mode: a 1-D line through R_s into R_l, by bounce series.

    A[n] is the wave leaving the near end at t = n*dt, B[n] the wave leaving the
    far end. Each is driven by the other one's arrival one delay earlier, with
    linear interpolation across the delay because T/dt is not an integer.
    """
    a = [0.0] * n
    b = [0.0] * n
    vn = [0.0] * n
    vf = [0.0] * n
    gl = (rl - zm) / (rl + zm)
    d = tm / dt
    for k in range(n):
        x = k - d
        if x >= 0.0:
            i0 = int(x)
            f = x - i0
            i1 = i0 + 1 if i0 + 1 < n else i0
            b_ret = b[i0] * (1.0 - f) + b[i1] * f
            a_del = a[i0] * (1.0 - f) + a[i1] * f
        else:
            b_ret = 0.0
            a_del = 0.0
        a[k] = (zm * src_modal[k] + (rs - zm) * b_ret) / (rs + zm)
        b[k] = gl * a_del
        vn[k] = a[k] + b_ret
        vf[k] = a_del + b[k]
    return vn, vf


def _waveforms(m, length_mm, rise_time_s, amplitude_v, rs, rl=None,
               samples_per_delay=60.0, swell=8.0):
    """Both modes driven by the same trapezoid; returns the 4 physical ends.

    Returns (dict, None) or (None, (msg, code)).
    """
    rl = rs if rl is None else rl
    length_m = length_mm * 1e-3
    te = length_m / m["v_even_m_s"]
    to = length_m / m["v_odd_m_s"]
    dt = min(te, to) / float(samples_per_delay)
    if rise_time_s / 20.0 < dt:
        dt = rise_time_s / 20.0
    # How long to run is not a taste: each round trip multiplies what is left by
    # the reflection coefficient, so a termination that is far from the modal
    # impedance needs many more trips to settle. Measured, this matters: with a
    # 10 ohm termination the old fixed 8 round trips stopped at 0.483 V where the
    # resistive divider says 0.500 V (-3.4 %) and the DC gate below then failed
    # the whole call. Derive the number of trips from the (worse) of the two
    # modal reflection coefficients instead.
    g_even = abs((rl - m["z_even_ohm"]) / (rl + m["z_even_ohm"]))
    g_odd = abs((rl - m["z_odd_ohm"]) / (rl + m["z_odd_ohm"]))
    gmax = min(max(g_even, g_odd), 1.0 - 1e-15)
    if gmax > 0.0:
        swell = max(swell, 2.0 * math.ceil(math.log(1e-10) / math.log(gmax)) + 4.0)
    tstop = swell * max(te, to) + 6.0 * rise_time_s
    n = int(tstop / dt) + 3
    if n > MAX_STEPS:
        return None, ("the time step is %s s for a %s mm line: that is %d steps, over "
                      "the %d this tool is willing to run. Shorten the line, lengthen "
                      "the rise time, or lower samples_per_delay"
                      % (_sig(dt), length_mm, n, MAX_STEPS), "OUT_OF_VALIDITY")
    src = [amplitude_v * min(1.0, (k * dt) / rise_time_s) for k in range(n)]
    src_modal = [v / S2 for v in src]
    ven, vef = _run_mode(m["z_even_ohm"], te, rs, rl, src_modal, dt, n)
    von, vof = _run_mode(m["z_odd_ohm"], to, rs, rl, src_modal, dt, n)
    t = [k * dt for k in range(n)]
    v1n = [(x + y) / S2 for x, y in zip(ven, von)]
    v2n = [(x - y) / S2 for x, y in zip(ven, von)]
    v1f = [(x + y) / S2 for x, y in zip(vef, vof)]
    v2f = [(x - y) / S2 for x, y in zip(vef, vof)]
    return {"dt": dt, "n": n, "t": t, "te": te, "to": to,
            "aggressor_near": v1n, "victim_near": v2n,
            "aggressor_far": v1f, "victim_far": v2f}, None


def _peak(t, v):
    i = max(range(len(v)), key=lambda k: abs(v[k]))
    return v[i], t[i]


def _decimate(t, v, points=101):
    n = len(v)
    if n <= points:
        return [[_sig(t[k] * 1e9), _sig(v[k])] for k in range(n)]
    step = (n - 1) / float(points - 1)
    out = []
    for k in range(points):
        i = int(round(k * step))
        i = min(i, n - 1)
        out.append([_sig(t[i] * 1e9), _sig(v[i])])
    return out


def coupled_lines(kind, width_mm, spacing_mm, dielectric_mm, length_mm,
                  rise_time_ns, copper_um=35.0, er=4.3, amplitude_v=1.0,
                  termination_ohm=None, mesh_div=12, samples_per_delay=60,
                  waveform_points=101):
    """Near- and far-end crosstalk between two parallel traces, from the field.

    `kind` is microstrip or stripline, `dielectric_mm` is the trace-to-plane
    height (stripline: HALF the plane-to-plane separation, same convention as
    every other pair tool here). `termination_ohm` is the resistor at EACH of
    the four ports; leave it out and the isolated-line impedance is used, which
    is what a driver/receiver pair usually looks like. Both lines must have the
    same termination at a given end - that is what decouples the two modes, and
    a mixed pair is refused rather than answered with a number from the wrong
    circuit.
    """
    kind = str(kind or "").strip().lower()
    if kind not in _f2d.KINDS:
        return err("kind must be microstrip or stripline", "ARG_INVALID")
    try:
        w = float(width_mm)
        s = float(spacing_mm)
        h = float(dielectric_mm)
        length = float(length_mm)
        tr_ns = float(rise_time_ns)
        t = float(copper_um) / 1000.0
        er = float(er)
        amp = float(amplitude_v)
    except (TypeError, ValueError):
        return err("numeric arguments required", "ARG_INVALID")
    if w <= 0 or s <= 0 or h <= 0:
        return err("width_mm, spacing_mm and dielectric_mm must be > 0", "ARG_RANGE")
    if t < 0:
        return err("copper_um must be >= 0", "ARG_RANGE")
    if er < 1.0:
        return err("er must be >= 1", "ARG_RANGE")
    if length <= 0:
        return err("length_mm must be > 0", "ARG_RANGE",
                   hint="a coupled run with no length has no crosstalk")
    if tr_ns <= 0:
        return err("rise_time_ns must be > 0: a step of zero rise time has infinite "
                   "bandwidth, and the far-end crosstalk of this model is proportional "
                   "to 1/rise_time, so it would be infinite", "ARG_RANGE")
    if amp <= 0:
        return err("amplitude_v must be > 0 (it is the source's open-circuit step)",
                   "ARG_RANGE")
    if termination_ohm is not None:
        try:
            termination_ohm = float(termination_ohm)
        except (TypeError, ValueError):
            return err("termination_ohm must be a number", "ARG_INVALID")
        if termination_ohm <= 0:
            return err("termination_ohm must be > 0", "ARG_RANGE")
    if s / h < 0.05:
        return err("spacing below 0.05*h: the symmetry plane is closer to the trace "
                   "than one mesh cell can resolve", "OUT_OF_VALIDITY")
    try:
        mesh_div = int(mesh_div)
        samples = int(samples_per_delay)
        npts = int(waveform_points)
    except (TypeError, ValueError):
        return err("mesh_div, samples_per_delay and waveform_points must be integers",
                   "ARG_INVALID")
    if mesh_div < 4 or mesh_div > 24:
        return err("mesh_div must be in 4..24 (cells across the smallest feature)",
                   "ARG_RANGE")
    if samples < 10 or samples > 400:
        return err("samples_per_delay must be in 10..400", "ARG_RANGE")
    if npts < 11 or npts > 501:
        return err("waveform_points must be in 11..501", "ARG_RANGE")

    # Refuse on the step budget BEFORE spending a minute on four field solves.
    # The velocity used here is the plane-wave one, c0/sqrt(er), which is an upper
    # bound on both modal velocities, so this can only be optimistic - the exact
    # gate runs again inside _waveforms with the solved velocities.
    v_ref = C0 / math.sqrt(er)
    tr_s = tr_ns * 1e-9
    t_ref = (length * 1e-3) / v_ref
    dt_ref = min(t_ref / float(samples_per_delay), tr_s / 20.0)
    n_ref = int((8.0 * t_ref + 6.0 * tr_s) / dt_ref) + 3
    if dt_ref <= 0 or n_ref > MAX_STEPS:
        return err("this run would need %d time steps (rise time %s ns, %s mm): over "
                   "the %d this tool will run. Lengthen the rise time, shorten the run, "
                   "or lower samples_per_delay"
                   % (n_ref, tr_ns, length, MAX_STEPS), "OUT_OF_VALIDITY")

    fine = max(min(w, h, t if t > 0 else h) / float(mesh_div), 1e-6)
    caps = _f2d._cap_modes(kind, w, s, h, t, er, fine)
    m = modal_from_caps(caps["c_odd_d"], caps["c_even_d"],
                        caps["c_odd_a"], caps["c_even_a"])
    z_iso = m["z_isolated_ohm"]
    rs = z_iso if termination_ohm is None else termination_ohm
    rl = rs

    wf, bad = _waveforms(m, length, tr_ns * 1e-9, amp, rs, rl,
                         samples_per_delay=samples)
    if bad is not None:
        return err(bad[0], bad[1])
    t_arr = wf["t"]

    # ---- the three checks this answer carries, each of which can fail ----
    # (a) with no coupling at all both modes are the same line: the victim must
    #     be silent and the aggressor must reach amp/2 at a matched far end.
    flat = dict(m)
    flat["cm_f_per_m"] = 0.0
    flat["lm_h_per_m"] = 0.0
    flat["c_odd_f_per_m"] = flat["c11_f_per_m"]
    flat["c_even_f_per_m"] = flat["c11_f_per_m"]
    flat["l_odd_h_per_m"] = flat["l11_h_per_m"]
    flat["l_even_h_per_m"] = flat["l11_h_per_m"]
    flat["z_odd_ohm"] = flat["z_even_ohm"] = z_iso
    flat["v_odd_m_s"] = flat["v_even_m_s"] = 1.0 / math.sqrt(m["l11_h_per_m"] * m["c11_f_per_m"])
    wf0, bad0 = _waveforms(flat, length, tr_ns * 1e-9, amp, rs, rl,
                           samples_per_delay=samples)
    if bad0 is not None:
        return err(bad0[0], bad0[1])
    leak0 = max(abs(v) for v in wf0["victim_near"]) / amp
    far0 = abs(wf0["aggressor_far"][-1])
    if leak0 > 1e-9:
        return err("the uncoupled control leaks %s of the source onto the neighbour: "
                   "the modal split is wrong and every number below would be wrong by "
                   "the same amount" % _sig(leak0), "INTERNAL_CHECK_FAILED")
    # (b) DC: with the coupling in place the far end still settles at the
    #     resistive divider, because at DC neither Cm nor Lm carries anything.
    dc = abs(wf["aggressor_far"][-1]) - amp * rl / (rs + rl)
    if abs(dc) > 1e-6 * amp:
        return err("the coupled solution does not settle to the resistive divider at "
                   "DC (off by %s V): the bounce series is truncated or the reflection "
                   "coefficient is wrong" % _sig(dc), "INTERNAL_CHECK_FAILED")

    # Two different lengths behave differently, and the crossover is not a taste:
    # the far end is (V_even - V_odd)/sqrt2, so as long as the two modes arrive
    # within one rise time of each other their difference grows with the length
    # of the coupled run (measured: constant per-mm at 10/25/50/100/200 mm). Once
    # the arrival times differ by more than the rise time, the difference is the
    # full step of one mode, i.e. it stops growing - and it cannot exceed
    # amp/4 (measured: 0.248002 at both 400 and 800 mm, = 99.2 % of amp/4).
    _dv_inv = abs(1.0 / m["v_odd_m_s"] - 1.0 / m["v_even_m_s"])
    coherent_mm = (tr_s / _dv_inv * 1000.0) if _dv_inv > 0 else None
    fext_saturated = (coherent_mm is not None and length > coherent_mm)

    next_v, next_t = _peak(t_arr, wf["victim_near"])
    fext_v, fext_t = _peak(t_arr, wf["victim_far"])
    aggr_v, aggr_t = _peak(t_arr, wf["aggressor_far"])
    v_inc = amp * rl / (rs + rl)
    kb = m["kb_first_order"]
    est = kb * v_inc

    modal = {
        "z_odd_ohm": _sig(m["z_odd_ohm"]), "z_even_ohm": _sig(m["z_even_ohm"]),
        "z_isolated_ohm": _sig(z_iso),
        "v_odd_m_s": _sig(m["v_odd_m_s"]), "v_even_m_s": _sig(m["v_even_m_s"]),
        "er_eff_odd": _sig(m["er_eff_odd"]), "er_eff_even": _sig(m["er_eff_even"]),
        "delay_odd_ns": _sig(wf["to"] * 1e9), "delay_even_ns": _sig(wf["te"] * 1e9),
    }
    coupling = {
        "c11_f_per_m": _sig(m["c11_f_per_m"]),
        "cg_f_per_m": _sig(m["cg_f_per_m"]),
        "cm_f_per_m": _sig(m["cm_f_per_m"]),
        "l11_h_per_m": _sig(m["l11_h_per_m"]),
        "lm_h_per_m": _sig(m["lm_h_per_m"]),
        "k_c_pct": _sig(100.0 * m["k_c"]), "k_l_pct": _sig(100.0 * m["k_l"]),
        "kb_first_order": _sig(kb),
        "kb_would_give_v": _sig(est),
        "sim_minus_kb_pct": _sig(100.0 * (abs(next_v) - est) / est) if est else None,
    }
    out = {
        "geometry": {"kind": kind, "width_mm": w, "spacing_mm": s, "s_h": _sig(s / h),
                     "dielectric_mm": h, "copper_um": _sig(t * 1000.0), "er": er,
                     "length_mm": length, "rise_time_ns": tr_ns},
        "termination_ohm": _sig(rs),
        "incident_v": _sig(v_inc),
        "modal": modal,
        "coupling": coupling,
        "next": {"peak_v": _sig(next_v), "peak_pct_of_amplitude": _sig(100.0 * abs(next_v) / amp),
                 "peak_ns": _sig(next_t * 1e9),
                 "waveform_ns_v": _decimate(t_arr, wf["victim_near"], npts)},
        "fext": {"peak_v": _sig(fext_v), "peak_pct_of_amplitude": _sig(100.0 * abs(fext_v) / amp),
                 "peak_ns": _sig(fext_t * 1e9),
                 "waveform_ns_v": _decimate(t_arr, wf["victim_far"], npts),
                 "scales_with_length": not fext_saturated,
                 "coherent_length_mm": _sig(coherent_mm) if coherent_mm else None,
                 "saturation_limit_v": _sig(0.25 * amp)},
        "aggressor": {"far_end_peak_v": _sig(aggr_v),
                      "far_end_final_v": _sig(wf["aggressor_far"][-1]),
                      "far_end_waveform_ns_v": _decimate(t_arr, wf["aggressor_far"], npts)},
        "checks": {
            "uncoupled_control_leak_fraction": _sig(leak0),
            "uncoupled_far_end_final_v": _sig(far0),
            "dc_settling_error_v": _sig(dc),
            "velocity_mismatch_pct": _sig(100.0 * abs(m["v_even_m_s"] - m["v_odd_m_s"])
                                          / max(m["v_even_m_s"], m["v_odd_m_s"])),
        },
        "mesh": {"cells_across_smallest_feature": mesh_div,
                 "divergence_cell_mm": _sig(fine),
                 "nodes": len(caps["sec"].xs) * len(caps["sec"].ys),
                 "samples": wf["n"], "dt_s": _sig(wf["dt"])},
        "not_computed": [
            "a third trace, a ground pour between the two, or a guard trace with its "
            "own vias - this is a two-line problem and coupling between three "
            "conductors is not additive",
            "dispersion, copper loss, skin effect and the dielectric's frequency "
            "dependence: the cross-section is solved once at DC, which is the "
            "quasi-TEM assumption every impedance answer here carries",
            "solder mask, the etch trapezoid, and the finite length of a real "
            "termination (the ports are at the ends of the coupled run)",
            "via, connector and package discontinuities, and any connector pinout "
            "crosstalk (that is a 3-D problem)",
            "radiation and the ground bounce a real return path produces; the "
            "reference plane is perfect here",
            "a victim whose termination differs from the aggressor's: the modal "
            "circuits only decouple when the two lines see the same impedance at "
            "each end, so that case is refused, not approximated",
        ],
    }
    return ok(value=out, unit="V", model="field2d_plus_modal_coupled_line_time_domain",
              basis=("2-D quasi-TEM field solution of div(eps*grad V) = 0 for the odd and "
                     "even modes, dielectric and vacuum; C11 = Cg + Cm with "
                     "Cg = C_even, Cm = (C_odd - C_even)/2, L from L = mu0/c_air; the "
                     "coupled telegrapher's equations then solved in time for a "
                     "trapezoidal source through the two decoupled modal lines, "
                     "V1,2 = (V_even +- V_odd)/sqrt2"),
              precision=("measured, not promised: the uncoupled control must leave the "
                         "victim at 0 V and the coupled far end must settle at the "
                         "resistive divider - both are gates inside this function and "
                         "both fail loudly instead of returning. The two velocities "
                         "here differ by %.1f %%, and that difference is the ONLY "
                         "source of far-end crosstalk in this model: a homogeneous "
                         "dielectric has v_even = v_odd and FEXT cancels to zero by "
                         "arithmetic, not by assumption (measured, see the stripline "
                         "case in docs/50). NEXT is the simulated peak; the "
                         "first-order Kb = (1/4)(Lm/L11 + Cm/C11) is quoted beside it "
                         "and departs from it as the coupling grows (measured: within "
                         "0.5 %% at k = 2 %%, 2x off at k = 26 %%), so Kb is not an "
                         "approximation of this number. The cross-section is the field "
                         "solver's, so this carries that solver's measured error (<1 %% "
                         "on the stripline and microstrip anchors in field2d.VALIDATION) "
                         "and no more"
                         % (100.0 * abs(m["v_even_m_s"] - m["v_odd_m_s"])
                            / max(m["v_even_m_s"], m["v_odd_m_s"]))),
              assumptions={"kind": kind, "er": er, "copper_um": _sig(t * 1000.0),
                           "equal terminations": "both lines, both ends",
                           "waveform": "trapezoid, rise_time_ns, flat top",
                           "quasi_tem": True},
              extra=out)
