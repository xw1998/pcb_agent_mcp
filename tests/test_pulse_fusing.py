"""Tests for the repetitive-pulse (duty-cycle derating) layer.

Same discipline as the rest of the suite: no expected number comes from a run of
the code under test. Each one is

  * a physical constant written out again here (rho20, rho_m, c_p, 1083 C, 234),
  * hand arithmetic on the fixture numbers, or
  * a definition that must hold identically (I_eq = I*sqrt(D) is an energy
    balance; the runaway threshold must reproduce itself when fed back).

The strongest case in this file is `TestShotCounts.test_rk4_integrates_the_same_
train`: an RK4 integrator written here, integrating the raw ODE
C' dT/dt = I^2 rho(T) - hP (T - Ta) segment by segment, has to land on the same
steady peak and the same shot count. It shares no algebra with the closed-form
geometric series, so it fails if the series has a sign error, an off-by-one, or
a wrong sign of exp().

The refusals carry as much weight as the numbers: h is not defaulted (it IS the
answer for a pulse train, a factor of 2 in h moves the sustained current by
sqrt(2) and the steady peak far more), and two spellings of one repetition rate
that disagree are refused rather than silently preferring one.
"""
import math
import os
import sys
import unittest

sys.path.insert(0, os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "src"))

from pcb_agent_mcp import tools as T                              # noqa: E402
from pcb_agent_mcp.calc import current as C                        # noqa: E402

# Physical constants, written out here so the test does not inherit them.
RHO20 = 1.72e-8          # ohm*m at 20 C
RHO_MASS = 8960.0        # kg/m^3
CP = 385.0               # J/(kg*K)
TM = 1083.0              # copper melting point, C
TREF = 234.0             # the same 234 Onderdonk uses
OZ_UM = 34.8             # 1 oz finished thickness, um


def _area_perim(width_mm, thick_um):
    w = width_mm / 1000.0
    t = thick_um / 1e6
    return w * t, 2.0 * (w + t)


def _rk4_train(width_mm, thick_um, current_a, h, t_amb_c, t_on, t_off,
               steps=400, max_cycles=5000):
    """Integrate the raw ODE, segment by segment, with RK4.

    Deliberately naive: rho(T) is evaluated as the physical law rho20*(234+T)/254,
    the two segments have different right-hand sides (no I^2 during the off time),
    and nothing here knows about geometric series.
    """
    area, perim = _area_perim(width_mm, thick_um)
    c_line = RHO_MASS * CP * area

    def heat(t_c):
        rho = RHO20 * (TREF + t_c) / (TREF + 20.0)
        return current_a ** 2 * rho / (area * c_line)

    def f_on(t_c):
        return heat(t_c) - h * perim * (t_c - t_amb_c) / c_line

    def f_off(t_c):
        return -h * perim * (t_c - t_amb_c) / c_line

    def advance(t_c, dt, n, f):
        for _ in range(n):
            k1 = f(t_c)
            k2 = f(t_c + 0.5 * dt * k1)
            k3 = f(t_c + 0.5 * dt * k2)
            k4 = f(t_c + dt * k3)
            t_c += dt * (k1 + 2.0 * k2 + 2.0 * k3 + k4) / 6.0
        return t_c

    t_c = t_amb_c
    peaks = []
    shots = None
    for cycle in range(1, max_cycles + 1):
        t_c = advance(t_c, t_on / steps, steps, f_on)
        peaks.append(t_c)
        if shots is None and t_c >= TM:
            shots = cycle
            break
        t_c = advance(t_c, t_off / steps, steps, f_off)
    return peaks, shots, t_c


def _adiabatic_melt_time(area_m2, current_a, t_amb_c):
    """t = area^2 rho_m c_p ln((234+Tm)/(234+Ta)) / (I^2 rho20)  -- hand derived.

    Energy: I^2 rho(T) dt / area = rho_m c_p area dT, with rho linear in T, so the
    integrals separate into ln of the temperature ratio.
    """
    ratio = (TREF + TM) / (TREF + t_amb_c)
    # rho(T) = rho20 (234 + T)/254 carries the 254 = 234 + 20 in its denominator,
    # and it moves to the numerator of the time integral
    return ((area_m2 ** 2 * RHO_MASS * CP * (TREF + 20.0) * math.log(ratio)) /
            (current_a ** 2 * RHO20))


BASE = dict(width_mm=1.0, t_on_s=1e-3, duty_cycle=0.1, h_w_m2k=10.0)


class TestAdiabaticLimit(unittest.TestCase):
    """h = 0 deletes the cooling, and the model must then BE Onderdonk."""

    def test_no_cooling_melt_time_is_the_hand_derived_energy_integral(self):
        r = C.repetitive_pulse_rating(width_mm=1.0, t_on_s=1e-3, duty_cycle=0.1,
                                      h_w_m2k=0.0, current_a=5.0)
        self.assertEqual(r["status"], "ok")
        area, _perim = _area_perim(1.0, OZ_UM)
        want = _adiabatic_melt_time(area, 5.0, 25.0)
        # the adiabatic answer is a TOTAL ON TIME; the train's wall clock also
        # counts the dead time between shots, so compare shots * t_on
        self.assertAlmostEqual(r["pulses_to_melt"] * r["t_on_s"] / want, 1.0, delta=5e-4)

    def test_no_cooling_says_so_and_refuses_a_sustained_current(self):
        r = C.repetitive_pulse_rating(width_mm=1.0, t_on_s=1e-3, duty_cycle=0.1,
                                      h_w_m2k=0.0, current_a=5.0)
        self.assertEqual(r["value"], 0.0)
        self.assertIn("no_cooling", r)
        self.assertIn("never leaves", r["no_cooling"])
        # negative control: with cooling the same width has a real rating
        r2 = C.repetitive_pulse_rating(**BASE)
        self.assertGreater(r2["value"], 10.0)

    def test_no_cooling_shot_energy_is_independent_of_the_pulse_length(self):
        # every joule stays in, so only the total on-time matters:
        # shots * t_on must be the same for 1 ms and 10 ms pulses
        short = C.repetitive_pulse_rating(width_mm=1.0, t_on_s=1e-3, duty_cycle=0.1,
                                          h_w_m2k=0.0, current_a=5.0)
        long_ = C.repetitive_pulse_rating(width_mm=1.0, t_on_s=1e-2, duty_cycle=0.1,
                                          h_w_m2k=0.0, current_a=5.0)
        self.assertAlmostEqual(short["pulses_to_melt"] * 1e-3 /
                               (long_["pulses_to_melt"] * 1e-2), 1.0, delta=5e-3)

    def test_no_cooling_agrees_with_the_single_pulse_fusing_function(self):
        # fusing_current is the same physics with Onderdonk's 33: two independent
        # routes to one number, so this is a cross-check of the constants.
        r = C.repetitive_pulse_rating(width_mm=1.0, t_on_s=1e-3, duty_cycle=0.1,
                                      h_w_m2k=0.0, current_a=5.0)
        f = C.fusing_current(width_mm=1.0, t_s=5.0)          # raise to the same total on-time
        # fusing_current answers a current for a time; ask the inverse instead:
        # the current that melts in 4.014 s must be the one that took 4014 shots
        total_on = r["pulses_to_melt"] * 1e-3
        f2 = C.fusing_current(width_mm=1.0, t_s=total_on)
        self.assertAlmostEqual(f2["value"] / 5.0, 1.0, delta=0.02)
        self.assertEqual(f["status"], "ok")


class TestRunawayThreshold(unittest.TestCase):
    """The closed-form boundary I^2 = area h P / (D beta rho(Ta)), and its twin
    1/(1 + k tau) for the duty ceiling at a given current."""

    def test_threshold_reproduces_itself_from_the_returned_numbers(self):
        r = C.repetitive_pulse_rating(**BASE)
        area, perim = _area_perim(r["width_mm"], r["thickness_um"])
        rho_ta = RHO20 * (TREF + r["t_ambient_c"]) / (TREF + 20.0)
        beta = 1.0 / (TREF + r["t_ambient_c"])
        want = math.sqrt(area * r["h_w_m2k"] * perim / (r["duty_cycle"] * beta * rho_ta))
        self.assertAlmostEqual(r["max_repetitive_current_a"] / want, 1.0, delta=1e-6)

    def test_threshold_scales_with_the_square_root_of_h(self):
        a = C.repetitive_pulse_rating(**BASE)
        b = C.repetitive_pulse_rating(**dict(BASE, h_w_m2k=40.0))
        self.assertAlmostEqual(b["max_repetitive_current_a"] /
                               a["max_repetitive_current_a"], 2.0, delta=1e-6)

    def test_threshold_scales_with_one_over_sqrt_duty(self):
        quarter = C.repetitive_pulse_rating(**dict(BASE, duty_cycle=0.25))
        full = C.repetitive_pulse_rating(**dict(BASE, duty_cycle=1.0))
        # four times the duty needs half the current
        self.assertAlmostEqual(quarter["max_repetitive_current_a"] /
                               full["max_repetitive_current_a"], 2.0, delta=1e-6)

    def test_just_below_the_threshold_settles_and_just_above_melts(self):
        crit = C.repetitive_pulse_rating(**BASE)["max_repetitive_current_a"]
        below = C.repetitive_pulse_rating(**dict(BASE, current_a=0.999 * crit))
        above = C.repetitive_pulse_rating(**dict(BASE, current_a=1.001 * crit))
        # runaway is a statement about EXISTENCE of a fixed point, not about being
        # hot: just below the boundary the fixed point exists (and is far above
        # melting - which is what the shot count is for), just above it does not.
        self.assertFalse(below["runs_away"])
        self.assertIsNotNone(below["steady_peak_c"])
        self.assertTrue(above["runs_away"])
        self.assertNotIn("steady_peak_c", above)
        # and above the boundary the shot count is always finite
        self.assertIsNotNone(above["pulses_to_melt"])
        self.assertFalse(above["survives_indefinitely"])

    def test_duty_ceiling_and_the_current_threshold_are_the_same_boundary(self):
        # at max_duty_cycle_at_current the period ratio should be exactly critical:
        # k*t_on == t_off/tau, i.e. q == 1
        r = C.repetitive_pulse_rating(**dict(BASE, current_a=5.0))
        d_star = r["max_duty_cycle_at_current"]
        just_under = C.repetitive_pulse_rating(width_mm=1.0, t_on_s=1e-3,
                                               duty_cycle=d_star * 0.998,
                                               h_w_m2k=10.0, current_a=5.0)
        over = C.repetitive_pulse_rating(width_mm=1.0, t_on_s=1e-3,
                                         duty_cycle=d_star * 1.002,
                                         h_w_m2k=10.0, current_a=5.0)
        self.assertGreater(d_star, 0.0)
        self.assertLess(d_star, 1.0)
        # the same boundary as the current threshold: a fixed point below it, none
        # above it. At the boundary itself q = 1, so the series does not converge.
        self.assertFalse(just_under["runs_away"])
        self.assertIsNotNone(just_under["steady_peak_c"])
        self.assertTrue(over["runs_away"])
        self.assertNotIn("steady_peak_c", over)

    def test_threshold_is_refused_when_nothing_cools(self):
        r = C.repetitive_pulse_rating(width_mm=1.0, t_on_s=1e-3, duty_cycle=0.1,
                                      h_w_m2k=0.0)
        self.assertEqual(r["value"], 0.0)
        self.assertIn("no_cooling", r)


class TestSteadyState(unittest.TestCase):
    def test_dc_equivalent_current_is_exactly_i_times_root_duty(self):
        # Energy balance: the train's mean heating I^2 rho(Ta) D equals a DC
        # current's I_eq^2 rho(Ta) over the same mean rise, so I_eq = I sqrt(D).
        # the threshold scales as 1/sqrt(D), so the current has to come down with
        # the duty to stay in the settling regime
        for duty, cur in ((0.1, 5.0), (0.25, 3.0), (0.4, 2.0)):
            r = C.repetitive_pulse_rating(**dict(BASE, duty_cycle=duty, current_a=cur))
            self.assertEqual(r["status"], "ok")
            self.assertAlmostEqual(r["dc_equivalent_current_a"] /
                                   (cur * math.sqrt(duty)), 1.0, delta=2e-4)

    def test_long_thermal_time_constant_means_no_ripple(self):
        # tau >> period: the copper does not have time to cool between shots
        r = C.repetitive_pulse_rating(**BASE, current_a=5.0)
        self.assertGreater(r["time_constant_over_period"], 100.0)
        self.assertLess(r["steady_ripple_c"] / r["steady_mean_rise_c"], 0.01)

    def test_short_thermal_time_constant_means_large_ripple(self):
        # a very high h on a thin trace: tau << period, so the peak rides the pulse
        r = C.repetitive_pulse_rating(width_mm=0.2, t_on_s=1e-3, duty_cycle=0.5,
                                      h_w_m2k=200000.0, current_a=2.0)
        self.assertLess(r["time_constant_over_period"], 0.5)
        self.assertGreater(r["steady_ripple_c"] / r["steady_mean_rise_c"], 0.9)

    def test_halving_h_roughly_doubles_the_rise_in_the_low_rise_limit(self):
        # when beta*dT is small the ODE is linear, so dT ~ 1/h
        hot = C.repetitive_pulse_rating(**dict(BASE, h_w_m2k=2000.0, current_a=1.0))
        cold = C.repetitive_pulse_rating(**dict(BASE, h_w_m2k=1000.0, current_a=1.0))
        self.assertAlmostEqual(cold["steady_mean_rise_c"] /
                               hot["steady_mean_rise_c"], 2.0, delta=0.03)

    def test_duty_one_matches_the_continuous_solution(self):
        # duty = 1 is no longer a train: it is one long pulse that must settle at
        # the linear fixed point, and the peak must equal the mean (no ripple)
        r = C.repetitive_pulse_rating(width_mm=1.0, t_on_s=1e-3, duty_cycle=1.0,
                                      h_w_m2k=2000.0, current_a=2.0)
        self.assertAlmostEqual(r["steady_ripple_c"], 0.0, delta=1e-9)
        self.assertAlmostEqual(r["steady_peak_c"], r["steady_mean_rise_c"] + 25.0,
                               delta=1e-6)

    def test_h_sensitivity_names_the_number_that_was_guessed(self):
        r = C.repetitive_pulse_rating(**BASE, current_a=5.0)
        self.assertIn("h_sensitivity", r)
        self.assertIn("%", r["h_sensitivity"])
        # the two probes really are h/2 and 2h
        self.assertGreater(r["steady_peak_c_at_half_h"], r["steady_peak_c"])
        self.assertLess(r["steady_peak_c_at_double_h"], r["steady_peak_c"])


class TestShotCounts(unittest.TestCase):
    def test_rk4_integrates_the_same_train(self):
        # An independent integrator of the raw ODE (this file), segment by segment.
        # Nothing is shared with the geometric series under test.
        peaks, shots, _end = _rk4_train(1.0, OZ_UM, 12.0, 10.0, 25.0, 1e-3, 9e-3,
                                        steps=400, max_cycles=3000)
        r = C.repetitive_pulse_rating(**BASE, current_a=12.0)
        self.assertEqual(shots, r["pulses_to_melt"])
        # this current runs away, so there is no steady peak to compare - the
        # melting temperature is the only thing both routes agree on
        self.assertTrue(r["runs_away"])
        self.assertAlmostEqual(peaks[-1], TM, delta=2.0)   # RK4 step granularity

    def test_rk4_reproduces_the_steady_peak_of_a_surviving_train(self):
        peaks, shots, _end = _rk4_train(1.0, OZ_UM, 5.0, 10.0, 25.0, 1e-3, 9e-3,
                                        steps=200, max_cycles=20000)
        self.assertIsNone(shots)
        r = C.repetitive_pulse_rating(**BASE, current_a=5.0)
        self.assertAlmostEqual(peaks[-1] / r["steady_peak_c"], 1.0, delta=2e-3)

    def test_melt_after_is_the_shot_count_times_the_period(self):
        r = C.repetitive_pulse_rating(**BASE, current_a=12.0)
        period = 1e-3 / 0.1
        self.assertAlmostEqual(r["melt_after_s"] / (r["pulses_to_melt"] * period),
                               1.0, delta=1e-9)

    def test_a_single_shot_case_is_reported_as_one_shot(self):
        r = C.repetitive_pulse_rating(width_mm=0.2, t_on_s=1e-3, duty_cycle=0.1,
                                      h_w_m2k=10.0, current_a=400.0)
        self.assertEqual(r["pulses_to_melt"], 1)
        self.assertAlmostEqual(r["melt_after_s"], 1e-3 / 0.1, delta=1e-9)

    def test_surviving_train_reports_none_not_a_big_integer(self):
        r = C.repetitive_pulse_rating(**BASE, current_a=5.0)
        self.assertTrue(r["survives_indefinitely"])
        self.assertIsNone(r["pulses_to_melt"])
        self.assertNotIn("melt_after_s", r)


class TestRefusals(unittest.TestCase):
    def _rc(self, **kw):
        return C.repetitive_pulse_rating(**kw).get("reason_code")

    def test_h_is_required(self):
        r = C.repetitive_pulse_rating(width_mm=1.0, t_on_s=1e-3, duty_cycle=0.1)
        self.assertEqual(r["reason_code"], "ARG_INVALID")
        self.assertIn("h_w_m2k is required", r["message"])
        self.assertIn("ranges from", r["message"] + r.get("hint", ""))

    def test_negative_h_is_refused(self):
        self.assertEqual(self._rc(**dict(BASE, h_w_m2k=-1.0)), "ARG_RANGE")

    def test_a_repetition_rate_is_required(self):
        r = C.repetitive_pulse_rating(width_mm=1.0, t_on_s=1e-3, h_w_m2k=10.0)
        self.assertEqual(r["reason_code"], "ARG_INVALID")
        self.assertIn("give duty_cycle or period_s", r["message"])

    def test_duty_out_of_range_is_refused(self):
        for bad in (0.0, -0.1, 1.5):
            self.assertEqual(self._rc(**dict(BASE, duty_cycle=bad)), "ARG_RANGE", bad)

    def test_period_shorter_than_the_pulse_is_refused(self):
        r = C.repetitive_pulse_rating(width_mm=1.0, t_on_s=1e-3, period_s=5e-4,
                                      h_w_m2k=10.0)
        self.assertEqual(r["reason_code"], "ARG_RANGE")
        self.assertIn("never ends", r["message"])

    def test_two_spellings_that_disagree_are_refused_with_both_numbers(self):
        r = C.repetitive_pulse_rating(width_mm=1.0, t_on_s=1e-3, duty_cycle=0.5,
                                      period_s=1e-3, h_w_m2k=10.0)
        self.assertEqual(r["reason_code"], "ARG_RANGE")
        self.assertIn("disagree", r["message"])
        self.assertAlmostEqual(r["duty_cycle"], 0.5)
        self.assertAlmostEqual(r["t_on_s_over_period_s"], 1.0)

    def test_two_spellings_that_agree_are_accepted(self):
        r = C.repetitive_pulse_rating(width_mm=1.0, t_on_s=1e-3, duty_cycle=0.1,
                                      period_s=1e-2, h_w_m2k=10.0)
        self.assertEqual(r["status"], "ok")
        self.assertAlmostEqual(r["duty_cycle"], 0.1)

    def test_zero_or_negative_geometry_is_refused(self):
        self.assertEqual(self._rc(**dict(BASE, width_mm=0.0)), "ARG_RANGE")
        self.assertEqual(self._rc(**dict(BASE, thickness_um=0.0)), "ARG_RANGE")
        self.assertEqual(self._rc(**dict(BASE, copper_oz=0.0)), "ARG_RANGE")
        self.assertEqual(self._rc(**dict(BASE, t_on_s=0.0)), "ARG_RANGE")

    def test_impossible_ambient_is_refused(self):
        self.assertEqual(self._rc(**dict(BASE, t_ambient_c=-300.0)), "ARG_RANGE")
        self.assertEqual(self._rc(**dict(BASE, t_ambient_c=1083.0)), "ARG_RANGE")

    def test_bad_layer_and_non_numeric_arguments_are_refused(self):
        self.assertEqual(self._rc(**dict(BASE, layer="sideways")), "ARG_INVALID")
        self.assertEqual(self._rc(**dict(BASE, current_a="x")), "ARG_INVALID")
        self.assertEqual(self._rc(**dict(BASE, duty_cycle="x")), "ARG_INVALID")

    def test_standard_argument_goes_through_the_standard_guard(self):
        r = C.repetitive_pulse_rating(**dict(BASE, standard=""))
        self.assertEqual(r["reason_code"], "STANDARD_REQUIRED")

    def test_an_unknown_standard_is_refused_not_defaulted(self):
        r = C.repetitive_pulse_rating(**dict(BASE, standard="IPC-9999"))
        self.assertIn(r.get("reason_code"),
                      ("STANDARD_UNKNOWN", "STANDARD_REQUIRED", "ARG_INVALID"))


class TestToolSurface(unittest.TestCase):
    def test_is_registered_with_the_three_required_inputs(self):
        self.assertIn("repetitive_pulse_rating", T.TOOLS)
        req = sorted(T.TOOLS["repetitive_pulse_rating"]["inputSchema"]["required"])
        self.assertEqual(req, ["h_w_m2k", "t_on_s", "width_mm"])

    def test_is_listed_in_the_capability_map(self):
        caps = T.TOOLS["calc_capabilities"]["handler"]({})
        self.assertIn("repetitive_pulse_rating", caps["available"])
        self.assertNotIn("repetitive pulses with duty-cycle derating",
                         " ".join(caps["available"]))

    def test_call_tool_matches_the_direct_call(self):
        args = dict(BASE, current_a=5.0)
        via_tool = T.call_tool("repetitive_pulse_rating", args)
        direct = C.repetitive_pulse_rating(**args)
        self.assertEqual(via_tool["value"], direct["value"])
        self.assertEqual(via_tool["pulses_to_melt"], direct["pulses_to_melt"])

    def test_the_error_envelope_uses_reason_code_and_message(self):
        out = T.call_tool("repetitive_pulse_rating",
                          {"width_mm": 1.0, "t_on_s": 1e-3, "duty_cycle": 0.1})
        self.assertEqual(out.get("reason_code"), "ARG_INVALID")
        self.assertIn("h_w_m2k", out.get("message", ""))
        self.assertNotIn("value", out)


if __name__ == "__main__":
    unittest.main()
