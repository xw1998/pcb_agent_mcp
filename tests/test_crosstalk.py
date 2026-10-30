"""Golden tests for two-line crosstalk (NEXT / FEXT).

The module under test has three layers, and each has a different way of being
wrong, so each gets its own kind of test:

  1. `modal_from_caps` - pure algebra from four capacitance numbers. Tested with
     SYNTHETIC capacitances so no field solve is needed: every entry of the
     returned dict is checked against the identity that defines it (and in
     particular er_eff = C_d/C_a, not c0/v, which is sqrt of it - a homogeneous
     stripline exposes the difference immediately: er = 4.3, not 2.0736).

  2. `_waveforms` - the time-domain bounce series on the two decoupled modes.
     Tested with synthetic modal parameters once more (seconds, not minutes),
     because the properties that matter are structural and must hold for ANY
     per-unit-length numbers:
        * no coupling at all  -> the victim is silent, bit for bit;
        * equal mode velocities -> far-end crosstalk cancels to machine zero;
        * weak coupling      -> NEXT = Kb * V_incident on the nose;
        * NEXT does not care about length or rise time, FEXT does (L, and 1/tr).

  3. `coupled_lines` - the whole pipeline with a real 2-D field solve (~6 s for
     stripline, ~32 s for microstrip at mesh_div = 8, so the solve happens ONCE
     in setUpClass and every test reads it). This is where the two ports that
     make or break the model are pinned: stripline must come back with er_eff =
     er on both modes and FEXT = 0, microstrip with er_eff_odd < er_eff_even
     and a nonzero NEGATIVE FEXT.

The most valuable check here is the last one: an INDEPENDENT ground truth. The
lumped ladder in `_ladder` is a plain FDTD leapfrog on the same L and C - it
shares no line of code and no assumption with the modal bounce series (no modes,
no weak coupling, no closed form). If someone "fixes" the factor of 2 that
separates Kb * V_incident from Kb * V_source, the ladder will not move and this
file will fail. That factor is real: measured, NEXT == 0.25*(k_l+k_c)*V_incident
where V_incident = amp*RL/(RS+RL) is the wave that actually arrives.
"""
import math
import os
import sys
import time
import unittest

sys.path.insert(0, os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "src"))

from pcb_agent_mcp.calc import crosstalk as X  # noqa: E402

# A "board" whose per-unit-length numbers we invent, so that the algebra and the
# time loop can be tested without a field solve. c_even/c_odd are the capacitance
# shape factors the solver returns; the _a suffix is vacuum.
def _caps(c_even, c_odd, er=1.0):
    return X.modal_from_caps(c_odd * er, c_even * er, c_odd, c_even)

# Small coupling, homogeneous dielectric: k_c == k_l to 6 digits by construction.
WEAK = _caps(5.0, 5.02, er=4.3)
# The four numbers a real microstrip pair returns (w = s = h = 0.2 mm, er 4.3,
# 35 um, taken from the mesh_div = 12 solve below): the odd mode is the FASTER
# one here, which is what makes far-end crosstalk negative. Synthetic in the
# sense that no solve is run, but the numbers are a solved cross-section.
METRIC_ODD_D, METRIC_EVEN_D = 10.505664, 8.333623
METRIC_ODD_A, METRIC_EVEN_A = 3.873203, 2.472430
METRIC = X.modal_from_caps(METRIC_ODD_D, METRIC_EVEN_D, METRIC_ODD_A, METRIC_EVEN_A)
# Exactly homogeneous and coupled: the two modal velocities must be identical.
HOMOG = _caps(5.0, 5.5, er=4.3)
# No coupling at all.
FLAT = _caps(5.0, 5.0, er=4.3)


def _wf(m, length_mm=50.0, tr_ns=0.2, amp=1.0, termination=None):
    rs = m["z_isolated_ohm"] if termination is None else termination
    out, bad = X._waveforms(m, length_mm, tr_ns * 1e-9, amp, rs, rs)
    assert bad is None, bad
    return out, rs


def _peaks(wf):
    return (X._peak(wf["t"], wf["victim_near"])[0],
            X._peak(wf["t"], wf["victim_far"])[0])


# --------------------------------------------------------------------------
# 1. the algebra
# --------------------------------------------------------------------------
class TestModalIdentities(unittest.TestCase):
    def test_capacitance_split(self):
        m = METRIC
        # C11 = Cg + Cm and Cg = c_even*eps0 (the mutual capacitor is uncharged
        # when both lines sit at the same potential).
        self.assertAlmostEqual(m["c11_f_per_m"],
                               m["cg_f_per_m"] + m["cm_f_per_m"], places=26)
        self.assertAlmostEqual(m["cg_f_per_m"], METRIC_EVEN_D * X.EPS0, places=26)
        self.assertAlmostEqual(m["cm_f_per_m"],
                               0.5 * (METRIC_ODD_D - METRIC_EVEN_D) * X.EPS0, places=26)

    def test_mode_capacitance_is_just_the_solved_one(self):
        # odd mode: C = C11 + Cm = c_odd*eps0 exactly; even: C11 - Cm = c_even*eps0.
        for c_even, c_odd in ((METRIC_EVEN_D, METRIC_ODD_D), (5.0, 5.02), (1.0, 1.0)):
            m = X.modal_from_caps(c_odd, c_even, c_odd, c_even)
            self.assertAlmostEqual(m["c_odd_f_per_m"], c_odd * X.EPS0, places=27)
            self.assertAlmostEqual(m["c_even_f_per_m"], c_even * X.EPS0, places=27)

    def test_inductance_comes_from_the_vacuum_capacitance(self):
        m = METRIC
        self.assertAlmostEqual(m["l_odd_h_per_m"], X.MU0 / METRIC_ODD_A, places=24)
        self.assertAlmostEqual(m["l_even_h_per_m"], X.MU0 / METRIC_EVEN_A, places=24)
        self.assertAlmostEqual(m["l11_h_per_m"],
                               0.5 * (m["l_odd_h_per_m"] + m["l_even_h_per_m"]), places=26)
        self.assertAlmostEqual(m["lm_h_per_m"],
                               0.5 * (m["l_even_h_per_m"] - m["l_odd_h_per_m"]), places=26)

    def test_er_eff_is_the_capacitance_ratio_not_c0_over_v(self):
        m = METRIC
        self.assertAlmostEqual(m["er_eff_odd"], METRIC_ODD_D / METRIC_ODD_A, places=14)
        self.assertAlmostEqual(m["er_eff_even"], METRIC_EVEN_D / METRIC_EVEN_A, places=14)
        # ... and it is consistent with the reported velocity, as (c0/v)^2.
        # (places=8 and not more: c0^2*mu0*eps0 is 1 to the precision of the
        # published constants, i.e. 1e-9, and that is the whole error budget here)
        self.assertAlmostEqual(m["er_eff_odd"],
                               (X.C0 / m["v_odd_m_s"]) ** 2, places=8)
        self.assertAlmostEqual(m["er_eff_even"],
                               (X.C0 / m["v_even_m_s"]) ** 2, places=8)

    def test_velocity_and_impedance_follow_from_l_and_c(self):
        m = METRIC
        self.assertAlmostEqual(m["v_odd_m_s"],
                               1.0 / math.sqrt(m["l_odd_h_per_m"] * m["c_odd_f_per_m"]),
                               places=2)
        self.assertAlmostEqual(m["z_odd_ohm"],
                               math.sqrt(m["l_odd_h_per_m"] / m["c_odd_f_per_m"]), places=10)
        self.assertAlmostEqual(m["z_even_ohm"],
                               math.sqrt(m["l_even_h_per_m"] / m["c_even_f_per_m"]), places=10)
        self.assertAlmostEqual(m["z_isolated_ohm"],
                               math.sqrt(m["l11_h_per_m"] / m["c11_f_per_m"]), places=10)

    def test_coupling_coefficients_and_kb(self):
        m = METRIC
        self.assertAlmostEqual(m["k_c"], m["cm_f_per_m"] / m["c11_f_per_m"], places=15)
        self.assertAlmostEqual(m["k_l"], m["lm_h_per_m"] / m["l11_h_per_m"], places=15)
        self.assertAlmostEqual(m["kb_first_order"],
                               0.25 * (m["k_l"] + m["k_c"]), places=15)
        # symmetric pair with a homogeneous dielectric: k_c == k_l by construction
        self.assertAlmostEqual(WEAK["k_c"], WEAK["k_l"], places=10)

    def test_homogeneous_dielectric_has_no_velocity_mismatch(self):
        for m in (HOMOG, WEAK):
            self.assertAlmostEqual(m["er_eff_odd"], 4.3, places=12)
            self.assertAlmostEqual(m["er_eff_even"], 4.3, places=12)
            self.assertLess(abs(m["v_odd_m_s"] - m["v_even_m_s"]) / m["v_even_m_s"], 1e-14)

    def test_isolated_line_sits_between_the_modes(self):
        # odd mode pulls the pair's capacitance up and inductance down => lowest Z
        for m in (METRIC, HOMOG, WEAK):
            self.assertLess(m["z_odd_ohm"], m["z_isolated_ohm"])
            self.assertLess(m["z_isolated_ohm"], m["z_even_ohm"])


# --------------------------------------------------------------------------
# 2. the time domain, on synthetic modal parameters
# --------------------------------------------------------------------------
class TestUncoupledControl(unittest.TestCase):
    def test_victim_is_exactly_silent(self):
        wf, _ = _wf(FLAT)
        self.assertEqual(max(abs(v) for v in wf["victim_near"]), 0.0)
        self.assertEqual(max(abs(v) for v in wf["victim_far"]), 0.0)

    def test_aggressor_reaches_the_divider(self):
        wf, _ = _wf(FLAT)
        self.assertAlmostEqual(wf["aggressor_far"][-1], 0.5, places=12)

    def test_the_control_is_not_vacuous(self):
        # the same call with coupling switched on does land on the victim
        wf, _ = _wf(HOMOG)
        self.assertGreater(abs(_peaks(wf)[0]), 1e-3)


class TestFarEndCancelsInAHomogeneousDielectric(unittest.TestCase):
    def test_fext_is_machine_zero(self):
        # two modal velocities that agree to 1e-16 make the far-end contributions
        # cancel exactly - this is arithmetic, not an assumption, and a wrong
        # modal split cannot pass it.
        wf, _ = _wf(HOMOG)
        nxt, fxt = _peaks(wf)
        self.assertGreater(abs(nxt), 1e-2)             # near end is loud ...
        self.assertLess(abs(fxt), 1e-12)               # ... far end is not there

    def test_it_cancels_at_every_rise_time(self):
        for tr in (0.1, 0.2, 0.5):
            wf, _ = _wf(HOMOG, tr_ns=tr)
            self.assertLess(abs(_peaks(wf)[1]), 1e-12, tr)


class TestWeakCouplingLaw(unittest.TestCase):
    """NEXT = Kb * V_incident, with V_incident the wave that ARRIVES, not the source.

    ``amp`` is the source's open-circuit step. Into a matched pair the wave that
    travels is amp/2, and NEXT is a fraction Kb of THAT. The textbook 1/4 in Kb
    is a quarter of the incident wave; reading it as a quarter of the source is a
    factor-of-2 error that the lumped ladder in TestAgainstAnIndependentLadder
    would catch (that ladder never sees this file's constant).
    """

    def test_next_equals_kb_times_incident(self):
        wf, rs = _wf(WEAK)
        nxt = _peaks(wf)[0]
        v_inc = 1.0 * rs / (rs + rs)
        self.assertAlmostEqual(v_inc, 0.5, places=12)
        ratio = nxt / (WEAK["kb_first_order"] * v_inc)
        self.assertAlmostEqual(ratio, 1.0, delta=2e-3)

    def test_next_does_not_depend_on_length_or_rise_time(self):
        vals = []
        for length, tr in ((50.0, 0.2), (200.0, 0.2), (400.0, 0.5), (50.0, 0.1)):
            wf, rs = _wf(WEAK, length_mm=length, tr_ns=tr)
            vals.append(_peaks(wf)[0] / (WEAK["kb_first_order"] * 0.5))
        for v in vals:
            self.assertAlmostEqual(v, vals[0], places=6)

    def test_next_is_linear_in_amplitude(self):
        a, _ = _wf(WEAK, amp=0.5)
        b, _ = _wf(WEAK, amp=2.0)
        self.assertAlmostEqual(_peaks(b)[0], 4.0 * _peaks(a)[0], places=15)

    def test_kb_departs_from_the_simulation_fast(self):
        # strong coupling: Kb is a first-order formula and this is where it loses.
        # METRIC is k_c 0.0476 / k_l 0.0476 after the er_eff fix -> ~0.06 % high;
        # the point of the test is that the reported sim-vs-Kb gap is computed
        # from the simulation, not assumed to be zero.
        wf, _ = _wf(METRIC)
        nxt = _peaks(wf)[0]
        self.assertAlmostEqual(nxt / (METRIC["kb_first_order"] * 0.5), 1.01007, delta=2e-4)


class TestFarEndScalesWithLengthAndRiseTime(unittest.TestCase):
    """FEXT has TWO regimes, and the crossover is the coherence length.

    The far end is (V_even - V_odd)/sqrt2. While the two modes arrive within one
    rise time of each other, that difference grows in proportion to how much
    coupled line the wave has crossed, so it is proportional to the length; once
    they arrive further apart than the rise time, the difference is one whole
    step of a mode and stops growing.

        L_coh = tr / |1/v_odd - 1/v_even|

    measured for METRIC: 317 mm at tr = 0.2 ns, 159 mm at tr = 0.1 ns, and the
    plateau is amp/4 (0.248002 V at both 400 mm and 800 mm, i.e. 99.2 % of 0.25).
    """

    def test_fext_is_proportional_to_length_inside_the_coherent_regime(self):
        base = None
        for length in (10.0, 25.0, 50.0, 100.0, 200.0):
            wf, _ = _wf(METRIC, length_mm=length)
            per_mm = _peaks(wf)[1] / length
            if base is None:
                base = per_mm
                self.assertLess(base, -1e-9)        # nonzero AND negative
            # 1e-4, not machine precision: at 200 mm the run is already 0.63 of
            # the coherence length and the per-mm value has drifted 3.8e-5.
            self.assertAlmostEqual(per_mm / base, 1.0, delta=1e-4, msg=str(length))

    def test_fext_stops_growing_once_the_modes_separate(self):
        a, _ = _wf(METRIC, length_mm=400.0)
        b, _ = _wf(METRIC, length_mm=800.0)
        fa, fb = _peaks(a)[1], _peaks(b)[1]
        # the same value at 400 and 800 mm: it is not linear any more
        self.assertAlmostEqual(fb / fa, 1.0, delta=1e-6)

    def test_the_plateau_is_a_quarter_of_the_source(self):
        # the physical bound: the two modes can differ by at most one full step,
        # and V2 = (V_even - V_odd)/sqrt2 with each mode carrying amp/2*sqrt2 ...
        # the number falls out as amp/4, and the simulation lands on 99.2 % of it.
        wf, _ = _wf(METRIC, length_mm=800.0)
        f = abs(_peaks(wf)[1])
        self.assertLess(f, 0.25)
        self.assertGreater(f, 0.98 * 0.25)

    def test_the_tool_says_which_regime_the_length_is_in(self):
        # 50 mm against a 317 mm coherence length: proportional to length
        m = _solve("microstrip")
        self.assertIs(m["fext"]["scales_with_length"], True)
        self.assertAlmostEqual(m["fext"]["coherent_length_mm"] / 317.3, 1.0, delta=0.01)
        self.assertAlmostEqual(m["fext"]["saturation_limit_v"], 0.25, places=12)

    def test_fext_is_proportional_to_one_over_rise_time(self):
        # inside the coherent regime (50 mm << 317 mm at 0.2 ns), so the only
        # thing changing is the slope of the edge.
        a, _ = _wf(METRIC, tr_ns=0.2)
        b, _ = _wf(METRIC, tr_ns=0.1)
        c, _ = _wf(METRIC, tr_ns=0.5)
        fa, fb, fc = _peaks(a)[1], _peaks(b)[1], _peaks(c)[1]
        self.assertAlmostEqual(fb / fa, 2.0, delta=5e-6)
        self.assertAlmostEqual(fc / fa, 0.4, delta=5e-6)

    def test_fext_sign_follows_the_faster_mode(self):
        # in METRIC the odd mode is faster (er_eff_odd < er_eff_even), so the
        # inductive coupling wins and the far end goes negative.
        self.assertLess(METRIC["er_eff_odd"], METRIC["er_eff_even"])
        self.assertLess(_peaks(_wf(METRIC)[0])[1], 0.0)


class TestTermination(unittest.TestCase):
    def test_any_equal_termination_still_settles_at_the_divider(self):
        # at DC neither Cm nor Lm carries anything, so the far end is the
        # resistive divider whatever the resistor is. This also pins the RUN
        # LENGTH: a termination far from the modal impedance reflects hard, and
        # with a fixed 8 round trips a 10 ohm termination used to stop at
        # 0.483 V (-3.4 %), which then failed the DC gate inside the tool and
        # turned a legitimate question into INTERNAL_CHECK_FAILED.
        for r in (10.0, 25.0, 100.0):
            wf, rs = _wf(METRIC, termination=r)
            self.assertAlmostEqual(rs, r)
            self.assertAlmostEqual(wf["aggressor_far"][-1], 0.5, places=12)

    def test_a_hard_mismatch_runs_long_enough_and_says_so_if_it_cannot(self):
        # 0.1 ohm reflects at 0.997 per trip: that needs > 7000 trips, i.e. more
        # time steps than MAX_STEPS, so it must be REFUSED rather than truncated.
        wf, _ = _wf(METRIC, termination=1.0)
        self.assertAlmostEqual(wf["aggressor_far"][-1], 0.5, places=9)
        out, bad = X._waveforms(METRIC, 50.0, 0.2e-9, 1.0, 0.1, 0.1)
        self.assertIsNone(out)
        self.assertIn("time step", bad[0])
        self.assertEqual(bad[1], "OUT_OF_VALIDITY")


# --------------------------------------------------------------------------
# 3. the whole pipeline, one field solve each
# --------------------------------------------------------------------------
_SOLVED = {}


def _solve(kind):
    """One field solve per kind, cached at module level.

    Module level rather than a class attribute so that the ladder test below can
    read the same solve whatever order unittest picks.
    """
    if kind not in _SOLVED:
        args = (dict(width_mm=0.2, spacing_mm=0.4, dielectric_mm=0.3,
                     length_mm=50.0, rise_time_ns=0.15) if kind == "stripline"
                else dict(width_mm=0.2, spacing_mm=0.2, dielectric_mm=0.2,
                          length_mm=50.0, rise_time_ns=0.2))
        r = X.coupled_lines(kind=kind, mesh_div=8, waveform_points=11, **args)
        assert r["status"] == "ok", r
        _SOLVED[kind] = r["value"]
    return _SOLVED[kind]


class TestSolvedCrossSections(unittest.TestCase):
    """One field solve per kind, shared by every test in the class.

    mesh_div = 8 rather than 12: measured 2.6x faster on the microstrip and it
    moves Z by 0.06 % and NEXT by 0.4 %, so the golden numbers below are the
    mesh_div = 8 ones, not the default.
    """

    @classmethod
    def setUpClass(cls):
        cls.s = _solve("stripline")
        cls.m = _solve("microstrip")

    # ---- stripline: homogeneous dielectric, so FEXT must vanish ----
    def test_stripline_er_eff_equals_er_on_both_modes(self):
        # 4.3, not 2.0736: this is the identity through a real field solve
        self.assertAlmostEqual(self.s["modal"]["er_eff_odd"], 4.3, places=3)
        self.assertAlmostEqual(self.s["modal"]["er_eff_even"], 4.3, places=3)

    def test_stripline_velocities_are_equal(self):
        self.assertLess(self.s["checks"]["velocity_mismatch_pct"], 1e-3)
        self.assertAlmostEqual(self.s["modal"]["delay_odd_ns"],
                               self.s["modal"]["delay_even_ns"], places=6)

    def test_stripline_fext_is_zero_to_the_field_solvers_accuracy(self):
        self.assertGreater(self.s["next"]["peak_v"], 1e-3)      # non-vacuous
        self.assertLess(abs(self.s["fext"]["peak_v"]), 1e-4)

    def test_stripline_next_golden(self):
        self.assertAlmostEqual(self.s["next"]["peak_v"], 0.0138382, places=6)
        self.assertAlmostEqual(self.s["modal"]["z_odd_ohm"], 55.546, places=3)
        self.assertAlmostEqual(self.s["modal"]["z_even_ohm"], 62.0503, places=3)
        self.assertAlmostEqual(self.s["modal"]["z_isolated_ohm"], 58.7081, places=3)
        self.assertAlmostEqual(self.s["coupling"]["k_c_pct"], 5.53106, places=4)
        self.assertAlmostEqual(self.s["coupling"]["sim_minus_kb_pct"], 0.0765988, places=4)

    # ---- microstrip: unequal er_eff, so FEXT is real and negative ----
    def test_microstrip_odd_mode_is_stiffer_than_even(self):
        self.assertLess(self.m["modal"]["er_eff_odd"], self.m["modal"]["er_eff_even"])
        self.assertGreater(self.m["modal"]["v_odd_m_s"], self.m["modal"]["v_even_m_s"])

    def test_microstrip_golden(self):
        self.assertAlmostEqual(self.m["modal"]["er_eff_odd"], 2.70836, places=4)
        self.assertAlmostEqual(self.m["modal"]["er_eff_even"], 3.36814, places=4)
        self.assertAlmostEqual(self.m["modal"]["z_odd_ohm"], 59.0209, places=3)
        self.assertAlmostEqual(self.m["modal"]["z_even_ohm"], 82.8358, places=3)
        self.assertAlmostEqual(self.m["modal"]["z_isolated_ohm"], 70.5638, places=3)
        self.assertAlmostEqual(self.m["modal"]["delay_odd_ns"], 0.274475, places=6)
        self.assertAlmostEqual(self.m["modal"]["delay_even_ns"], 0.306086, places=6)
        self.assertAlmostEqual(self.m["checks"]["velocity_mismatch_pct"], 10.3276, places=3)
        self.assertAlmostEqual(self.m["next"]["peak_pct_of_amplitude"], 4.22691, places=4)
        self.assertAlmostEqual(self.m["fext"]["peak_pct_of_amplitude"], 3.92, places=6)

    def test_microstrip_fext_is_negative_and_material(self):
        self.assertLess(self.m["fext"]["peak_v"], 0.0)
        self.assertGreater(abs(self.m["fext"]["peak_pct_of_amplitude"]), 3.0)

    def test_microstrip_kb_is_one_percent_optimistic(self):
        # and it is NOT an approximation of the answer: the tool reports both.
        self.assertAlmostEqual(self.m["coupling"]["sim_minus_kb_pct"], 1.00365, places=3)

    # ---- the gates that ship inside coupled_lines ----
    def test_both_answers_carry_zero_checks(self):
        for v in (self.s, self.m):
            self.assertEqual(v["checks"]["uncoupled_control_leak_fraction"], 0.0)
            self.assertAlmostEqual(v["checks"]["uncoupled_far_end_final_v"], 0.5, places=10)
            self.assertLess(abs(v["checks"]["dc_settling_error_v"]), 1e-12)

    def test_reported_incident_is_the_matched_half(self):
        for v in (self.s, self.m):
            self.assertAlmostEqual(v["incident_v"], 0.5, places=12)
            self.assertAlmostEqual(v["termination_ohm"], v["modal"]["z_isolated_ohm"], places=5)

    def test_not_computed_list_is_still_honest(self):
        for v in (self.s, self.m):
            self.assertGreaterEqual(len(v["not_computed"]), 6)
            joined = " ".join(v["not_computed"])
            for word in ("third trace", "dispersion", "solder mask", "radiation",
                         "victim whose termination differs"):
                self.assertIn(word, joined)


# --------------------------------------------------------------------------
# 4. independent ground truth
# --------------------------------------------------------------------------
def _ladder(l11, lm, cg, cm, length_mm, tr_s, amp, rs, k_sections=200, cfl=0.4):
    """Two coupled lines as a lumped ladder (FDTD leapfrog), no modes at all.

    Shares nothing with the module under test except the per-unit-length numbers:
    the 2x2 L and C matrices are inverted directly, and the line is a chain of
    k_sections pi-sections. Returns (dt, victim_near_series).
    """
    length_m = length_mm * 1e-3
    dz = length_m / k_sections
    ls, lms = l11 * dz, lm * dz
    cgs, cms = cg * dz, cm * dz
    det_l = ls * ls - lms * lms
    linv = (ls / det_l, -lms / det_l)
    c11s = cgs + cms
    det_c = c11s * c11s - cms * cms
    cinv = (c11s / det_c, cms / det_c)

    # the two modal velocities of this same lumped line, only used to pick dt.
    # Per UNIT length, not per section: 1/sqrt(L_section*C_section) = v/dz.
    c11 = cg + cm
    v_odd = 1.0 / math.sqrt((l11 - abs(lm)) * (c11 + abs(cm)))
    v_even = 1.0 / math.sqrt((l11 + abs(lm)) * (c11 - abs(cm)))
    vmax = max(v_odd, v_even)
    dt = cfl * dz / vmax
    n = int((8.0 * length_m / vmax + 6.0 * tr_s) / dt) + 2
    v1 = [0.0] * (k_sections + 1)
    v2 = [0.0] * (k_sections + 1)
    i1 = [0.0] * k_sections
    i2 = [0.0] * k_sections
    out2 = []
    for step in range(n):
        vs = amp * min(1.0, step * dt / tr_s)
        dv1 = [0.0] * k_sections
        dv2 = [0.0] * k_sections
        for k in range(k_sections):
            a = v1[k] - v1[k + 1]
            b = v2[k] - v2[k + 1]
            dv1[k] = linv[0] * a + linv[1] * b
            dv2[k] = linv[1] * a + linv[0] * b
        for k in range(k_sections):
            i1[k] += dt * dv1[k]
            i2[k] += dt * dv2[k]
        cur1 = [0.0] * (k_sections + 1)
        cur2 = [0.0] * (k_sections + 1)
        cur1[0] = (vs - v1[0]) / rs - i1[0]
        cur2[0] = (0.0 - v2[0]) / rs - i2[0]
        for k in range(1, k_sections):
            cur1[k] = i1[k - 1] - i1[k]
            cur2[k] = i2[k - 1] - i2[k]
        cur1[k_sections] = i1[k_sections - 1] - v1[k_sections] / rs
        cur2[k_sections] = i2[k_sections - 1] - v2[k_sections] / rs
        for k in range(k_sections + 1):
            v1[k] += dt * (cinv[0] * cur1[k] + cinv[1] * cur2[k])
            v2[k] += dt * (cinv[1] * cur1[k] + cinv[0] * cur2[k])
        out2.append(v2[0])
    return dt, out2


class TestAgainstAnIndependentLadder(unittest.TestCase):
    """The modal answer against a lumped ladder built from the same L and C.

    Runs on the microstrip solved once above (per-unit-length numbers are read
    back out of its report), so it costs no extra field solve.
    """

    def test_ladder_agrees_with_the_modal_answer(self):
        m = _solve("microstrip")
        c = m["coupling"]
        rs = m["termination_ohm"]
        wf, bad = X._waveforms(
            {"z_odd_ohm": m["modal"]["z_odd_ohm"],
             "z_even_ohm": m["modal"]["z_even_ohm"],
             "v_odd_m_s": m["modal"]["v_odd_m_s"],
             "v_even_m_s": m["modal"]["v_even_m_s"]},
            50.0, 0.2e-9, 1.0, rs, rs)
        self.assertIsNone(bad)
        modal_next = X._peak(wf["t"], wf["victim_near"])[0]
        _dt, series = _ladder(c["l11_h_per_m"], c["lm_h_per_m"],
                               c["cg_f_per_m"], c["cm_f_per_m"],
                              50.0, 0.2e-9, 1.0, rs)
        ladder_next = max(series, key=abs)
        # measured: 0.042492 (k=200) vs 0.042428 modal = 0.15 %
        self.assertAlmostEqual(ladder_next / modal_next, 1.0, delta=0.005)
        self.assertGreater(abs(modal_next), 1e-2)


# --------------------------------------------------------------------------
# 5. refusals (all of these return before the field solve, so they are fast)
# --------------------------------------------------------------------------
class TestRefusals(unittest.TestCase):
    BASE = dict(kind="microstrip", width_mm=0.2, spacing_mm=0.2,
                dielectric_mm=0.2, length_mm=50.0, rise_time_ns=0.2)

    def _code(self, **kw):
        args = dict(self.BASE)
        args.update(kw)
        r = X.coupled_lines(**args)
        self.assertEqual(r["status"], "error", r)
        return r["reason_code"], r.get("message", "")

    def test_bad_kind(self):
        self.assertEqual(self._code(kind="cpw")[0], "ARG_INVALID")

    def test_zero_dimensions(self):
        for k in ("width_mm", "spacing_mm", "dielectric_mm"):
            self.assertEqual(self._code(**{k: 0})[0], "ARG_RANGE", k)

    def test_zero_rise_time_names_the_infinity(self):
        code, msg = self._code(rise_time_ns=0)
        self.assertEqual(code, "ARG_RANGE")
        self.assertIn("1/rise_time", msg)

    def test_zero_length_and_amplitude(self):
        self.assertEqual(self._code(length_mm=0)[0], "ARG_RANGE")
        self.assertEqual(self._code(amplitude_v=0)[0], "ARG_RANGE")

    def test_er_below_one(self):
        self.assertEqual(self._code(er=0.9)[0], "ARG_RANGE")

    def test_negative_copper(self):
        self.assertEqual(self._code(copper_um=-1)[0], "ARG_RANGE")

    def test_termination_must_be_a_positive_number(self):
        self.assertEqual(self._code(termination_ohm=0)[0], "ARG_RANGE")
        self.assertEqual(self._code(termination_ohm="x")[0], "ARG_INVALID")

    def test_spacing_below_the_mesh(self):
        code, msg = self._code(spacing_mm=0.001)
        self.assertEqual(code, "OUT_OF_VALIDITY")
        self.assertIn("0.05*h", msg)

    def test_integer_arguments(self):
        self.assertEqual(self._code(mesh_div=99)[0], "ARG_RANGE")
        self.assertEqual(self._code(mesh_div=3)[0], "ARG_RANGE")
        self.assertEqual(self._code(samples_per_delay=5)[0], "ARG_RANGE")
        self.assertEqual(self._code(samples_per_delay=1000)[0], "ARG_RANGE")
        self.assertEqual(self._code(waveform_points=5)[0], "ARG_RANGE")
        self.assertEqual(self._code(waveform_points=1000)[0], "ARG_RANGE")
        self.assertEqual(self._code(mesh_div="x")[0], "ARG_INVALID")

    def test_step_budget_is_refused_before_solving(self):
        # a 10 m line with a 10 ps edge would need ~1.1e8 steps: the gate has to
        # fire BEFORE the four field solves, and it has to fire in well under a
        # second - that is the whole point of doing the precheck with the
        # plane-wave velocity.
        t0 = time.time()
        code, msg = self._code(length_mm=10000.0, rise_time_ns=0.01)
        self.assertEqual(code, "OUT_OF_VALIDITY")
        self.assertIn("time steps", msg)
        self.assertLess(time.time() - t0, 1.0)


if __name__ == "__main__":
    unittest.main()
