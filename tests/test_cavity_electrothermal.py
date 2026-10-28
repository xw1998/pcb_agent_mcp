"""Tests for the cavity (plane resonance) and electro-thermal layers.

Same discipline as the vibration tests: no expected number comes from a run of
the code being tested. Each one is

  * a closed form with an obvious reading        (f10 = c/(2 sqrt(er) a), where a
    is exactly half a guided wavelength; lambda_g = 2a for the (1,0) mode),
  * hand arithmetic on the fixture numbers       (R20 = rho L/(w t), then
    dT = A/(1-G) with A and G written out), or
  * a definition that must hold identically      (the fixed point must satisfy
    P*Rth = dT, so feeding the answer back must reproduce itself).

The two most valuable cases here are the refusal ones: a runaway gain above 1
must NOT return the last iterate, and via_fence must NOT invent an acceptance
ratio (lambda_g/20 vs lambda_g/10 is a factor of 2 disagreement that the module
has no business resolving).
"""
import math
import os
import sys
import unittest

sys.path.insert(0, os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "src"))

from pcb_agent_mcp.calc import cavity as C      # noqa: E402
from pcb_agent_mcp.calc import electrothermal as E  # noqa: E402

C0 = 299792458.0


class PlaneResonance(unittest.TestCase):
    def test_first_mode_is_exactly_half_a_guided_wavelength(self):
        # a = 100 mm at er = 4.3 -> lambda_g = 2*100 mm = 200 mm, so f = c/(sqrt(er)*200mm)
        r = C.plane_resonance(100.0, 80.0, 4.3)
        self.assertEqual(r["status"], "ok")
        f10 = r["value"][0]
        self.assertEqual((f10["m"], f10["n"]), (1, 0))
        expect_hz = C0 / (2.0 * math.sqrt(4.3) * 0.1)      # 0.72286... GHz
        self.assertAlmostEqual(f10["f_hz"] / expect_hz, 1.0, delta=1e-6)
        self.assertAlmostEqual(f10["lambda_g_mm"], 200.0, delta=1e-6)

    def test_second_mode_is_the_first_scaled_by_the_aspect_ratio(self):
        # (0,1) sits at f10 * a/b because (1/a) vs (1/b) share the same prefactor
        r = C.plane_resonance(100.0, 80.0, 4.3)
        f10, f01 = r["value"][0], r["value"][1]
        self.assertAlmostEqual(f01["f_hz"] / f10["f_hz"], 100.0 / 80.0, delta=1e-9)

    def test_diagonal_mode_adds_in_quadrature(self):
        r = C.plane_resonance(100.0, 80.0, 4.3, m_max=1, n_max=1)
        f = {(m["m"], m["n"]): m["f_hz"] for m in r["value"]}
        quad = math.sqrt(f[(1, 0)] ** 2 + f[(0, 1)] ** 2)
        self.assertAlmostEqual(f[(1, 1)] / quad, 1.0, delta=1e-5)  # _sig keeps 6 digits

    def test_square_keeps_degenerate_pairs_separate(self):
        r = C.plane_resonance(100.0, 100.0, 4.0, m_max=1, n_max=1)
        rows = r["value"]
        self.assertEqual(len(rows), 3)          # (1,0), (0,1), (1,1)
        self.assertAlmostEqual(rows[0]["f_hz"], rows[1]["f_hz"], delta=1e-6)
        self.assertNotEqual((rows[0]["m"], rows[0]["n"]), (rows[1]["m"], rows[1]["n"]))

    def test_er_enters_as_inverse_sqrt(self):
        a = C.plane_resonance(100.0, 100.0, 4.0)["value"][0]["f_hz"]
        b = C.plane_resonance(100.0, 100.0, 16.0)["value"][0]["f_hz"]
        self.assertAlmostEqual(a / b, 2.0, delta=1e-5)

    def test_operating_point_names_the_nearest_mode_without_a_verdict(self):
        base = C.plane_resonance(100.0, 80.0, 4.3)["value"][0]["f_hz"]
        r = C.plane_resonance(100.0, 80.0, 4.3, f_operate_hz=base * 1.02)
        op = r["operating_point"]
        self.assertEqual((op["nearest_mode"]["m"], op["nearest_mode"]["n"]), (1, 0))
        self.assertAlmostEqual(op["f_operate_over_f_mode"], 1.02, delta=1e-6)
        self.assertNotIn("verdict", r)

    def test_no_correction_and_no_q_are_stated(self):
        r = C.plane_resonance(100.0, 80.0, 4.3)
        self.assertFalse(r["authoritative"])
        blob = " ".join(r["not_computed"])
        self.assertIn("Q", blob)
        self.assertIn("detuning", blob)

    def test_refusals(self):
        self.assertEqual(C.plane_resonance("x", 80.0, 4.3)["reason_code"], "ARG_INVALID")
        self.assertEqual(C.plane_resonance(100.0, 0.0, 4.3)["reason_code"], "ARG_RANGE")
        self.assertEqual(C.plane_resonance(100.0, 80.0, 0.5)["reason_code"], "ARG_RANGE")
        self.assertEqual(C.plane_resonance(100.0, 80.0, 4.3, n_max=0)["reason_code"],
                         "ARG_INVALID")


class ViaFence(unittest.TestCase):
    def setUp(self):
        self.f = C.plane_resonance(100.0, 80.0, 4.3)["value"][0]["f_hz"]  # 722.864 MHz
        self.lam_g = 200.0

    def test_pitch_ratio_is_plain_division(self):
        r = C.via_fence(self.f, 4.3, pitch_mm=2.0)
        v = r["value"]
        self.assertAlmostEqual(v["lambda_g_mm"], self.lam_g, delta=1e-6)
        self.assertAlmostEqual(v["pitch_over_lambda_g"], 2.0 / self.lam_g, delta=1e-9)
        self.assertAlmostEqual(v["phase_per_pitch_deg"], 360.0 * 2.0 / self.lam_g,
                               delta=1e-9)
        self.assertAlmostEqual(v["vias_per_lambda_g"], self.lam_g / 2.0, delta=1e-6)

    def test_without_a_criterion_it_refuses_to_judge(self):
        # lambda_g/20 = 10 mm and lambda_g/10 = 20 mm: a 8 mm pitch passes one and
        # fails the other, so the module must not pick.
        v = C.via_fence(self.f, 4.3, pitch_mm=8.0)["value"]
        self.assertEqual(v["verdict"], "indeterminate")
        self.assertIn("lambda_g/20", v["verdict_reason"])
        self.assertNotIn("margin_mm", v)

    def test_with_a_criterion_it_judges_both_ways(self):
        # 15 mm pitch: 0.05*200 = 10 mm -> fail, 0.10*200 = 20 mm -> pass. The very
        # same geometry must flip, which is exactly why the criterion cannot be
        # built in.
        tight = C.via_fence(self.f, 4.3, pitch_mm=15.0, max_pitch_ratio=0.05)["value"]
        loose = C.via_fence(self.f, 4.3, pitch_mm=15.0, max_pitch_ratio=0.10)["value"]
        self.assertEqual(tight["verdict"], "fail")
        self.assertEqual(loose["verdict"], "pass")
        self.assertAlmostEqual(tight["max_pitch_mm"], 10.0, delta=1e-6)
        self.assertAlmostEqual(loose["max_pitch_mm"], 20.0, delta=1e-6)

    def test_criterion_without_a_pitch_returns_the_limit(self):
        v = C.via_fence(self.f, 4.3, max_pitch_ratio=0.05)["value"]
        self.assertEqual(v["verdict"], "indeterminate")
        self.assertAlmostEqual(v["max_pitch_mm"], 10.0, delta=1e-6)
        self.assertNotIn("pitch_mm", v)

    def test_needs_something_to_work_with(self):
        self.assertEqual(C.via_fence(self.f, 4.3)["reason_code"], "ARG_REQUIRED")
        self.assertEqual(C.via_fence(0, 4.3, pitch_mm=2.0)["reason_code"], "ARG_REQUIRED")
        self.assertEqual(C.via_fence(self.f, 4.3, pitch_mm=-1)["reason_code"], "ARG_RANGE")

    def test_fence_width_reports_its_own_electrical_length(self):
        v = C.via_fence(self.f, 4.3, pitch_mm=2.0, fence_width_mm=10.0)["value"]
        self.assertAlmostEqual(v["fence_width_over_lambda_g"], 0.05, delta=1e-9)
        self.assertAlmostEqual(v["fence_electrical_length_deg"], 18.0, delta=1e-6)


class ElectroThermalHandArithmetic(unittest.TestCase):
    """R20 = 0.01 ohm, I = 1 A, Rth = 50 K/W, alpha = 0.00393, T_amb = 25 C."""

    def setUp(self):
        self.R20 = 0.01
        self.I = 1.0
        self.RTH = 50.0
        self.A = self.I ** 2 * self.R20 * self.RTH * (1 + 0.00393 * (25.0 - 20.0))
        self.G = self.I ** 2 * self.R20 * self.RTH * 0.00393
        self.DT = self.A / (1.0 - self.G)

    def test_matches_the_closed_form_fixed_point(self):
        r = E.conductor_self_heating(self.R20, self.I, self.RTH)
        self.assertEqual(r["status"], "ok")
        self.assertAlmostEqual(r["delta_t_c"], self.DT, delta=2e-6)
        self.assertAlmostEqual(r["t_final_c"], 25.0 + self.DT, delta=1e-4)
        self.assertAlmostEqual(r["runaway_gain"], self.G, delta=1e-8)
        self.assertGreater(r["iterations"], 0)

    def test_the_answer_satisfies_P_times_Rth_equals_dT(self):
        r = E.conductor_self_heating(self.R20, self.I, self.RTH)
        self.assertAlmostEqual(r["power_w"] * self.RTH, r["delta_t_c"], delta=1e-4)

    def test_r_hot_follows_the_linear_law(self):
        r = E.conductor_self_heating(self.R20, self.I, self.RTH)
        # R(T) references 20 C, so the gain is alpha*(T_hot - 20) = alpha*(5 + dT)
        self.assertAlmostEqual(r["r_hot_ohm"] / (self.R20 * (1 + 0.00393 * (25.0 + self.DT - 20.0))),
                               1.0, delta=1e-5)

    def test_zero_current_is_ambient(self):
        r = E.conductor_self_heating(self.R20, 0.0, self.RTH)
        self.assertAlmostEqual(r["delta_t_c"], 0.0, delta=1e-9)
        self.assertAlmostEqual(r["t_final_c"], 25.0, delta=1e-9)

    def test_resistance_gain_and_margin_are_reported(self):
        r = E.conductor_self_heating(self.R20, self.I, self.RTH)
        self.assertAlmostEqual(r["resistance_gain"],
                               1 + 0.00393 * (25.0 + self.DT - 20.0), delta=1e-5)
        self.assertAlmostEqual(r["stability_margin"], 1.0 - self.G, delta=1e-8)


class ElectroThermalRunaway(unittest.TestCase):
    def test_gain_above_one_is_refused_not_iterated(self):
        # G = 2^2 * 1 ohm * 300 K/W * 0.00393 = 4.716
        r = E.conductor_self_heating(1.0, 2.0, 300.0)
        self.assertEqual(r["status"], "error")
        self.assertEqual(r["reason_code"], "THERMAL_RUNAWAY")
        self.assertAlmostEqual(r["runaway_gain"], 4.716, delta=1e-3)
        self.assertNotIn("value", r)

    def test_exactly_unit_gain_is_refused(self):
        # G = 1 exactly: 1 A, 1 ohm, Rth = 1/(0.00393) K/W
        r = E.conductor_self_heating(1.0, 1.0, 1.0 / 0.00393)
        self.assertEqual(r["reason_code"], "THERMAL_RUNAWAY")

    def test_high_but_stable_gain_converges_and_says_so(self):
        # G = 0.98: converges to A/(1-G) = 50x(A); the margin must be reported tiny
        rth = 0.98 / (1.0 ** 2 * 1.0 * 0.00393)
        r = E.conductor_self_heating(1.0, 1.0, rth, max_iter=20000)
        self.assertEqual(r["status"], "ok")
        self.assertAlmostEqual(r["runaway_gain"], 0.98, delta=1e-9)
        self.assertLess(r["stability_margin"], 0.03)


class ElectroThermalTrace(unittest.TestCase):
    def test_r20_is_rho_L_over_w_t(self):
        r = E.trace_self_heating(20.0, 1.0, 35.0, 1.0, rth_c_per_w=100.0)
        expect = 1.724e-8 * 0.020 / (0.001 * 35e-6)
        self.assertAlmostEqual(r["r20_ohm"] / expect, 1.0, delta=1e-6)

    def test_matches_the_one_line_hand_calculation(self):
        # R20 = 9.85143 mohm, G = 0.0038720, A = 1.004478 -> dT = 1.008382
        r = E.trace_self_heating(20.0, 1.0, 35.0, 1.0, rth_c_per_w=100.0)
        r20 = 1.724e-8 * 0.020 / (0.001 * 35e-6)
        g = 1.0 * r20 * 100.0 * 0.00393
        a = 1.0 * r20 * 100.0 * (1 + 0.00393 * 5.0)
        self.assertAlmostEqual(r["delta_t_c"], a / (1.0 - g), delta=2e-5)

    def test_current_density_is_a_per_mm2(self):
        r = E.trace_self_heating(20.0, 1.0, 35.0, 1.0, rth_c_per_w=100.0)
        self.assertAlmostEqual(r["current_density_a_per_mm2"], 1.0 / 0.035, delta=1e-3)

    def test_rth_is_required(self):
        r = E.trace_self_heating(20.0, 1.0, 35.0, 1.0)
        self.assertEqual(r["reason_code"], "ARG_REQUIRED")

    def test_refusals(self):
        self.assertEqual(E.trace_self_heating(-1, 1, 35, 1, rth_c_per_w=100)["reason_code"],
                         "ARG_RANGE")
        self.assertEqual(E.trace_self_heating(20, 1, 0, 1, rth_c_per_w=100)["reason_code"],
                         "ARG_RANGE")
        self.assertEqual(E.trace_self_heating(20, 1, 35, -1, rth_c_per_w=100)["reason_code"],
                         "ARG_RANGE")
        self.assertEqual(E.trace_self_heating(20, 1, 35, 1, rth_c_per_w=0)["reason_code"],
                         "ARG_RANGE")
        self.assertEqual(E.trace_self_heating("x", 1, 35, 1, rth_c_per_w=100)["reason_code"],
                         "ARG_INVALID")


class ToolSurface(unittest.TestCase):
    def test_the_four_tools_are_registered(self):
        from pcb_agent_mcp import tools
        for name in ("cavity_resonance", "cavity_via_fence",
                     "electrothermal_trace", "electrothermal_conductor"):
            self.assertIn(name, tools.TOOLS)

    def test_capabilities_lists_both_layers(self):
        from pcb_agent_mcp import tools
        c = tools.call_tool("calc_capabilities", {})
        self.assertIn("cavity", c["layers"])
        self.assertIn("electrothermal", c["layers"])
        for name in ("cavity_resonance", "cavity_via_fence",
                     "electrothermal_trace", "electrothermal_conductor"):
            self.assertIn(name, c["available"])
        self.assertIn("THERMAL_RUNAWAY", c["layers"]["electrothermal"])

    def test_the_old_no_electrothermal_claim_is_gone(self):
        # 0.0.5 said "no electrothermal coupling"; that sentence had to be replaced,
        # not kept next to a working tool.
        from pcb_agent_mcp import tools
        c = tools.call_tool("calc_capabilities", {})
        blob = " ".join(c["not_implemented_yet"])
        # 0.0.10 replaced it again: the coupling now reaches a 2-D laminate field,
        # so the live wording is board_electrothermal + "2-D field".
        self.assertIn("board_electrothermal", blob)
        self.assertIn("2-D field", blob)
        self.assertNotIn("nothing here feeds a temperature rise back", blob)

    def test_tools_are_callable_through_the_dispatcher(self):
        from pcb_agent_mcp import tools
        r = tools.call_tool("cavity_resonance", {"a_mm": 100, "b_mm": 80, "er": 4.3})
        self.assertEqual(r["status"], "ok")
        r = tools.call_tool("electrothermal_conductor",
                            {"r_ohm_at_20c": 0.01, "current_a": 1,
                             "rth_c_per_w": 50})
        self.assertEqual(r["status"], "ok")
        r = tools.call_tool("cavity_via_fence", {"f_resonance_hz": 722.864e6, "er": 4.3})
        self.assertEqual(r["reason_code"], "ARG_REQUIRED")


if __name__ == "__main__":
    unittest.main()
