"""Fusing a trace whose WIDTH CHANGES along it (`fusing_current_taper`).

Two anchors, and neither of them is this module's own output:

  * A UNIFORM segment with insulated ends has identical neighbours, so every axial
    conduction term cancels in pairs and the model collapses to the lumped
    adiabatic ODE. That has a closed form with copper's own linear rho(T):

        t = rho_m c_p A^2 / (I^2 rho20 alpha) * ln((1+a(Tm-20))/(1+a(Ta-20)))

    so the time-domain solver is checked against arithmetic, and the answer must
    NOT depend on how many segments the caller split the same trace into.
  * The same closed form at t = 1 s is what Onderdonk (1928) fits, so
    `fusing_current` and the taper's uniform case must agree. They agree to
    0.33 % in current, not to machine precision: Onderdonk's 33 and today's
    rho_m*cp/rho20 are not exactly the same constant. That residue is MEASURED
    here and pinned, so if someone later "improves" the constants the test says
    which number moved.

Negative controls, one per mechanism (each would FAIL if the mechanism were wrong
in the obvious way):

  * a uniform trace must give EXACTLY zero conduction delay - if the delay number
    were a constant or a fudge, this would show it;
  * a neck must melt before the uniform trace of the same mean width - the whole
    point of the length axis, and the thing the old single-width tool cannot say;
  * narrower must mean earlier, monotonically, over four widths;
  * switching conduction off must reproduce the adiabatic control exactly, which
    is what proves the control arm is really conduction-free;
  * a current that does not melt must return NO time (not the end of the window).

Every refusal asserts its reason_code, because in each case the honest answer is
"no number".
"""
import math
import unittest

from pcb_agent_mcp.calc import current as C

TA = 25.0
MELT = C.MELT_CU_C
THICK = 35.0
RHO_M = C.RHO_MASS_CU
CP = C.CP_CU
RHO20 = C.RHO20_CU
ALPHA = C.ALPHA_234_CU


def _area_m2(width_mm, thick_um=THICK):
    return (width_mm * 1e-3) * (thick_um * 1e-6)


def _adiabatic_time(i_a, width_mm, thick_um=THICK, ta=TA, melt=MELT):
    """The closed form the uniform case must reproduce (copper's linear rho law)."""
    a = _area_m2(width_mm, thick_um)
    num = 1.0 + ALPHA * (melt - 20.0)
    den = 1.0 + ALPHA * (ta - 20.0)
    return RHO_M * CP * a * a / (i_a * i_a * RHO20 * ALPHA) * math.log(num / den)


def _flat(length_mm=100.0, width_mm=2.0, n=1):
    return [{"length_mm": length_mm / n, "width_mm": width_mm} for _ in range(n)]


def _neck(width_mm=0.5, arm_mm=20.0, neck_mm=2.0, arm_width_mm=2.0):
    return [{"length_mm": arm_mm, "width_mm": arm_width_mm},
            {"length_mm": neck_mm, "width_mm": width_mm},
            {"length_mm": arm_mm, "width_mm": arm_width_mm}]


def _run(segments, **kw):
    kw.setdefault("thickness_um", THICK)
    r = C.fusing_current_taper(segments, **kw)
    assert r["status"] == "ok", r
    return r


I_OND_1S = C.fusing_current(2.0, t_s=1.0, thickness_um=THICK,
                            t_ambient_c=TA)["value"]


class TestTheUniformCaseIsTheAdiabaticClosedForm(unittest.TestCase):
    """No conduction, no profile: arithmetic decides, not the solver."""

    def test_the_model_matches_the_closed_form_to_a_fraction_of_a_percent(self):
        r = _run(_flat(), current_a=I_OND_1S, t_end_s=1.5)
        ana = _adiabatic_time(I_OND_1S, 2.0)
        self.assertLess(abs(r["t_melt_s"] - ana) / ana, 0.002,
                        "t=%r closed form=%r" % (r["t_melt_s"], ana))

    def test_segmentation_of_a_uniform_trace_changes_nothing(self):
        # identical neighbours -> the conduction terms cancel in pairs, exactly
        times = []
        for n in (1, 2, 5, 10, 40):
            r = _run(_flat(n=n), current_a=I_OND_1S, t_end_s=1.5)
            times.append(r["t_melt_s"])
        self.assertLess(max(times) - min(times), 1e-9, times)

    def test_the_profile_of_a_uniform_trace_is_flat(self):
        r = _run(_flat(n=10), current_a=15.0, t_end_s=1.0)
        temps = [p["t_c"] for p in r["temperature_profile_c"]]
        self.assertEqual(r["melted"], False)
        self.assertLess(max(temps) - min(temps), 1e-9, temps)

    def test_agrees_with_onderdonk_and_the_residue_is_measured(self):
        # 0.33 % in current: Onderdonk's 33 vs rho_m*cp/rho20 today. Pinned, not
        # fitted away - a change in either constant moves this number.
        r = _run(_flat(), current_a=I_OND_1S, t_end_s=1.5)
        dev = abs(r["t_melt_s"] - 1.0) / 1.0
        self.assertLess(dev, 0.01, "melt time %r s at the 1 s Onderdonk current"
                        % r["t_melt_s"])
        ratio = math.sqrt(1.0 / r["t_melt_s"])   # t ~ 1/I^2
        self.assertLess(abs(ratio - 1.0), 0.005,
                        "current residue %.4f %%" % (100.0 * (1.0 - ratio)))

    def test_backward_euler_converges_from_below_as_dt_shrinks(self):
        ana = _adiabatic_time(I_OND_1S, 2.0)
        errs = []
        for n in (200, 500, 2000, 8000):
            r = _run(_flat(), current_a=I_OND_1S, t_end_s=1.5, dt_s=1.5 / n)
            errs.append(abs(r["t_melt_s"] - ana) / ana)
        # first order: four times the steps must cut the error by roughly four
        self.assertGreater(errs[0], errs[-1] * 4.0, errs)
        self.assertLess(errs[-1], 2e-4, errs)


class TestTheNeckIsWhereItMelts(unittest.TestCase):
    def test_it_melts_in_the_narrow_segment(self):
        r = _run(_neck(0.5), current_a=30.0, t_end_s=1.0)
        self.assertTrue(r["melted"])
        self.assertEqual(r["melt_segment"], 1)
        self.assertAlmostEqual(r["melt_at_mm"], 21.0, places=6)

    def test_narrower_means_earlier(self):
        times = []
        for w in (2.0, 1.0, 0.5, 0.25):
            r = _run(_neck(w), current_a=30.0, t_end_s=1.0)
            times.append(r["t_melt_s"])
        self.assertEqual(times, sorted(times, reverse=True), times)
        self.assertGreater(times[0] / times[-1], 10.0, times)

    def test_a_neck_melts_before_the_uniform_trace_of_the_mean_width(self):
        taper = _run(_neck(0.5), current_a=30.0, t_end_s=1.0)
        mean_w = (20.0 * 2.0 + 2.0 * 0.5 + 20.0 * 2.0) / 42.0
        uniform = _run(_flat(42.0, mean_w), current_a=30.0, t_end_s=1.0)
        self.assertTrue(taper["melted"] and uniform["melted"])
        self.assertLess(taper["t_melt_s"], uniform["t_melt_s"] / 4.0,
                        "taper %r vs uniform %r" % (taper["t_melt_s"],
                                                    uniform["t_melt_s"]))


class TestConductionIsMeasuredNotAsserted(unittest.TestCase):
    def test_a_uniform_trace_has_exactly_zero_conduction_delay(self):
        r = _run(_flat(), current_a=I_OND_1S, t_end_s=1.5)
        self.assertEqual(r["adiabatic_control"]["conduction_delay_pct"], 0.0)

    def test_a_neck_has_a_positive_conduction_delay(self):
        r = _run(_neck(0.5), current_a=30.0, t_end_s=1.0)
        delay = r["adiabatic_control"]["conduction_delay_pct"]
        self.assertIsNotNone(delay)
        self.assertGreater(delay, 5.0)
        self.assertLess(r["adiabatic_control"]["t_melt_s"], r["t_melt_s"])

    def test_switching_conduction_off_reproduces_the_control_arm(self):
        r = _run(_neck(0.5), current_a=30.0, t_end_s=1.0)
        off = _run(_neck(0.5), current_a=30.0, t_end_s=1.0, k_thermal_w_mk=0.0)
        self.assertAlmostEqual(off["t_melt_s"],
                               r["adiabatic_control"]["t_melt_s"], places=6)

    def test_fixed_ends_keep_the_middle_hottest_and_delay_the_melt(self):
        ins = _run(_flat(n=10), current_a=25.0, t_end_s=1.0)
        fix = _run(_flat(n=10), current_a=25.0, t_end_s=1.0, ends_c=TA)
        self.assertIn(fix["peak_segment"], (4, 5))    # middle of ten segments
        self.assertLess(fix["peak_c"], ins["peak_c"])
        self.assertGreater(fix["t_melt_s"], ins["t_melt_s"])


class TestTheReverseDirectionIsTheSameCurve(unittest.TestCase):
    def test_current_from_time_and_back_again(self):
        for target in (0.05, 0.2):
            back = _run(_neck(0.5), t_melt_s=target)
            self.assertEqual(back["unit"], "A")
            fwd = _run(_neck(0.5), current_a=back["value"], t_end_s=target * 3.0)
            self.assertLess(abs(fwd["t_melt_s"] - target) / target, 2e-3,
                            (target, back["value"], fwd["t_melt_s"]))
            self.assertEqual(fwd["melt_segment"], 1)

    def test_the_reverse_current_sits_above_the_adiabatic_lower_bound(self):
        target = 0.2
        back = _run(_neck(0.5), t_melt_s=target)
        floor = C._taper_adiabatic_current(_area_m2(0.5), target, TA, MELT, ALPHA,
                                           RHO_M, CP, RHO20)
        self.assertGreater(back["value"], floor)
        self.assertLess(back["value"], floor * 4.0)

    def test_reverse_with_fixed_ends(self):
        back = _run(_flat(), t_melt_s=2.0, ends_c=TA)
        self.assertIsNotNone(back["steady_threshold_a"])
        self.assertGreater(back["value"], back["steady_threshold_a"])


class TestTheSteadyThreshold(unittest.TestCase):
    def test_no_ends_means_no_steady_threshold(self):
        r = _run(_flat(), current_a=I_OND_1S, t_end_s=1.5)
        self.assertIsNone(r["steady_threshold_a"])
        self.assertIn("none exists", r["steady_threshold_note"])

    def test_the_threshold_is_where_the_steady_peak_reaches_the_melt_point(self):
        r = _run(_flat(), current_a=I_OND_1S, t_end_s=1.5, ends_c=TA)
        thr = r["steady_threshold_a"]
        g, e = C._taper_geometry(_flat(), THICK)
        self.assertIsNone(e)
        peak = C._taper_steady(g, thr, TA, C.K_THERMAL_CU, ALPHA, RHO_M, CP, RHO20)
        self.assertIsNotNone(peak)
        self.assertLess(abs(peak - MELT), 1.0, peak)

    def test_the_threshold_is_far_below_the_pulse_fusing_current(self):
        # a 0.1 m trace clamped at 25 C can only dump heat through its own ends
        r = _run(_flat(), current_a=I_OND_1S, t_end_s=1.5, ends_c=TA)
        self.assertLess(r["steady_threshold_a"], I_OND_1S / 4.0)
        self.assertGreater(r["steady_threshold_a"], 0.1)


class TestNoMeltIsNotAFakeNumber(unittest.TestCase):
    def test_a_low_current_reports_no_time_at_all(self):
        r = _run(_flat(), current_a=2.0, t_end_s=1.0)
        self.assertFalse(r["melted"])
        self.assertIsNone(r["value"])
        self.assertEqual(r["unit"], "s")
        self.assertIsNone(r["t_melt_s"])
        self.assertIsNone(r["melt_at_mm"])
        self.assertGreater(r["margin_to_melt_k"], 100.0)

    def test_the_peak_is_the_end_of_the_window_not_the_melt_point(self):
        r = _run(_flat(), current_a=2.0, t_end_s=1.0)
        self.assertLess(r["peak_c"], 500.0)
        self.assertAlmostEqual(r["peak_at_s"], 1.0, places=6)


class TestRefusals(unittest.TestCase):
    def _code(self, **kw):
        kw.setdefault("thickness_um", THICK)
        r = C.fusing_current_taper(kw.pop("segments", _flat()), **kw)
        self.assertEqual(r["status"], "error", r)
        return r["reason_code"]

    def test_empty_segments(self):
        self.assertEqual(self._code(segments=[], current_a=1.0), "ARG_INVALID")

    def test_segments_must_be_a_list(self):
        self.assertEqual(self._code(segments={"length_mm": 1, "width_mm": 1},
                                    current_a=1.0), "ARG_INVALID")

    def test_segment_missing_a_width(self):
        self.assertEqual(self._code(segments=[{"length_mm": 5.0}], current_a=1.0),
                         "ARG_INVALID")

    def test_segment_length_must_be_positive(self):
        self.assertEqual(self._code(segments=[{"length_mm": 0.0, "width_mm": 2.0}],
                                    current_a=1.0), "ARG_RANGE")

    def test_segment_width_must_be_positive(self):
        self.assertEqual(self._code(segments=[{"length_mm": 5.0, "width_mm": -1.0}],
                                    current_a=1.0), "ARG_RANGE")

    def test_thickness_must_be_positive(self):
        self.assertEqual(self._code(thickness_um=0.0, current_a=1.0), "ARG_RANGE")

    def test_alpha_must_be_positive(self):
        self.assertEqual(self._code(alpha_per_k=0.0, current_a=1.0), "ARG_RANGE")

    def test_negative_k_is_refused(self):
        self.assertEqual(self._code(k_thermal_w_mk=-1.0, current_a=1.0), "ARG_RANGE")

    def test_both_questions_at_once(self):
        self.assertEqual(self._code(current_a=1.0, t_melt_s=1.0), "ARG_REQUIRED")

    def test_neither_question(self):
        self.assertEqual(self._code(), "ARG_REQUIRED")

    def test_t_end_must_be_positive(self):
        self.assertEqual(self._code(current_a=1.0, t_end_s=0.0), "ARG_RANGE")

    def test_dt_must_be_positive(self):
        self.assertEqual(self._code(current_a=1.0, t_end_s=1.0, dt_s=0.0), "ARG_RANGE")

    def test_step_budget_is_enforced(self):
        self.assertEqual(self._code(current_a=1.0, t_end_s=1.0, dt_s=1e-6,
                                    max_steps=1000), "ARG_RANGE")

    def test_melt_must_be_above_ambient(self):
        self.assertEqual(self._code(current_a=1.0, t_ambient_c=1200.0), "ARG_RANGE")

    def test_a_terminal_at_the_melt_point_is_refused(self):
        self.assertEqual(self._code(current_a=1.0, ends_c=MELT), "ARG_RANGE")

    def test_non_numeric_ends(self):
        self.assertEqual(self._code(current_a=1.0, ends_c="hot"), "ARG_INVALID")

    def test_ambient_below_the_reference(self):
        self.assertEqual(self._code(current_a=1.0, t_ambient_c=-300.0), "ARG_RANGE")


class TestEnvelope(unittest.TestCase):
    def test_the_envelope_carries_its_own_limit(self):
        r = _run(_neck(0.5), current_a=30.0, t_end_s=1.0)
        for k in ("basis", "precision", "model", "assumptions"):
            self.assertTrue(r.get(k), k)
        self.assertIn("spatial", r["assumptions"])
        self.assertIn("isothermal", r["assumptions"]["spatial"])
        self.assertIn("1-D along the trace", r["assumptions"]["spatial"])
        # the one-sided errors point in OPPOSITE directions, and the envelope says so
        self.assertIn("OPPOSITE", r["precision"])
        self.assertEqual(r["segment_count"], 3)
        self.assertEqual(r["ends_are"], "insulated (no heat leaves through the ends)")
        self.assertIn("axial conduction only", r["assumptions"]["cooling"])

    def test_ends_are_named_when_they_are_given(self):
        r = _run(_flat(), current_a=I_OND_1S, t_end_s=1.5, ends_c=TA)
        self.assertEqual(r["ends_are"], "fixed at ends_c")
        self.assertIn("pinned at ends_c", r["assumptions"]["cooling"])

    def test_the_answer_matches_the_question(self):
        t = _run(_flat(), current_a=I_OND_1S, t_end_s=1.5)
        self.assertEqual(t["unit"], "s")
        self.assertAlmostEqual(t["value"], t["t_melt_s"], places=6)
        i = _run(_flat(), t_melt_s=1.0)
        self.assertEqual(i["unit"], "A")
        self.assertAlmostEqual(i["value"], i["current_a"], places=6)

    def test_copper_oz_and_thickness_are_the_same_knob(self):
        a = _run(_flat(), thickness_um=35.0, current_a=10.0, t_end_s=0.5)
        b = _run(_flat(), thickness_um=None, copper_oz=35.0 / C.OZ_TO_UM,
                 current_a=10.0, t_end_s=0.5)
        self.assertAlmostEqual(a["peak_c"], b["peak_c"], places=3)
