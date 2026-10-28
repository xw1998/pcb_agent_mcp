"""Golden tests for the matching-network Calc module.

Every network here is solved from the load admittance, not from a Q approximation,
and every solution is re-evaluated: the resulting impedance and the residual VSWR
come back recomputed from the component values. That is asserted here, because
"the formula produced numbers" and "the numbers actually match the load" are two
different claims and only the second one is worth anything.
"""
import os
import sys
import unittest

sys.path.insert(0, os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "src"))

from pcb_agent_mcp.calc import matching  # noqa: E402


class TestReflection(unittest.TestCase):
    def test_a_2_to_1_load(self):
        v = matching.reflection(100.0)["value"]
        # gamma = (100-50)/(100+50) = 1/3, VSWR = 3, RL = 9.54 dB, mismatch 0.51 dB
        self.assertAlmostEqual(v["gamma_mag"], 1.0 / 3.0, places=6)
        self.assertAlmostEqual(v["vswr"], 2.0, places=6)
        self.assertAlmostEqual(v["return_loss_db"], 9.542, places=3)
        self.assertAlmostEqual(v["mismatch_loss_db"], 0.5116, places=3)

    def test_a_matched_load_is_perfect(self):
        v = matching.reflection(50.0)["value"]
        self.assertAlmostEqual(v["vswr"], 1.0, places=9)
        self.assertAlmostEqual(v["mismatch_loss_db"], 0.0, places=9)
        self.assertIsNone(v["return_loss_db"])  # -infinity, not a number

    def test_the_four_views_agree(self):
        v = matching.reflection("100+25j", freq_ghz=2.4)["value"]
        g = v["gamma_mag"]
        # each reported field is rounded for display, so the agreement is checked
        # at display precision - the identities are exact, the printout is not
        self.assertAlmostEqual(v["vswr"], (1 + g) / (1 - g), places=3)
        import math
        self.assertAlmostEqual(v["return_loss_db"], -20 * math.log10(g), places=3)
        self.assertAlmostEqual(v["reflected_power_pct"], 100.0 * g * g, delta=0.001)

    def test_reactance_is_named_as_a_component(self):
        v = matching.reflection("50+50j", freq_ghz=2.4)["value"]
        # X = +50 ohm at 2.4 GHz -> L = X/(2*pi*f) = 3.3157 nH
        self.assertAlmostEqual(v["equivalent_reactance"]["series_inductance_nh"],
                               3.3157, places=3)
        self.assertIsNone(v["equivalent_reactance"]["series_capacitance_pf"])
        v2 = matching.reflection("50-50j", freq_ghz=2.4)["value"]
        self.assertAlmostEqual(v2["equivalent_reactance"]["series_capacitance_pf"],
                               1.3263, places=3)

    def test_string_and_object_forms_are_the_same_load(self):
        a = matching.reflection("100+25j")["value"]["vswr"]
        b = matching.reflection({"real": 100, "imag": 25})["value"]["vswr"]
        c = matching.reflection("100-25j")["value"]["vswr"]
        self.assertAlmostEqual(a, b, places=9)
        self.assertAlmostEqual(a, c, places=9)  # |gamma| is symmetric in X

    def test_a_pure_reactance_is_refused(self):
        # R = 0 cannot be matched by any passive network: VSWR is infinite and
        # every component of a match would have to be lossy
        r = matching.reflection("-j30")
        self.assertEqual(r["status"], "error")
        self.assertEqual(r["reason_code"], "ARG_RANGE")
        self.assertIn("non-positive resistance", r["message"])

    def test_a_bad_string_is_refused(self):
        r = matching.reflection("fifty ohm")
        self.assertEqual(r["status"], "error")


class TestLMatch(unittest.TestCase):
    def test_100_ohm_at_2_4_ghz(self):
        v = matching.l_match(100.0, 2.4)["value"]
        sol = v["solutions"][0]
        self.assertEqual(sol["topology"], "shunt_across_load_then_series")
        self.assertAlmostEqual(sol["shunt"]["c_pf"], 0.6631, places=3)
        self.assertAlmostEqual(sol["series"]["l_nh"], 3.3157, places=3)
        self.assertAlmostEqual(sol["loaded_q"], 1.0, places=6)

    def test_every_solution_actually_matches_the_load(self):
        # this is the assertion that matters: the reported components must
        # reproduce 50 ohm, checked by recomputing rather than by trusting
        for z in ("100+0j", "25+0j", "150-60j", "10+40j"):
            v = matching.l_match(z, 2.4)["value"]
            self.assertTrue(v["solutions"], z)
            for sol in v["solutions"]:
                self.assertAlmostEqual(sol["resulting_z_ohm"]["real"], 50.0, delta=0.01)
                self.assertAlmostEqual(sol["resulting_z_ohm"]["imag"], 0.0, delta=0.01)
                self.assertLess(sol["residual_vswr"], 1e-3)

    def test_both_topologies_have_the_expected_q(self):
        # 100 ohm from 50: Q = sqrt(R/r - 1) = 1 for the shunt solution, and the
        # dual series solution has the same Q
        v = matching.l_match(100.0, 2.4)["value"]
        self.assertEqual(len(v["solutions"]), 2)
        for sol in v["solutions"]:
            self.assertAlmostEqual(sol["loaded_q"], 1.0, places=6)

    def test_a_matched_load_needs_no_network(self):
        # a 50 ohm load is already matched: the network degenerates to nothing
        # rather than to a small inductor nobody asked for
        v = matching.l_match(50.0, 2.4)["value"]
        for sol in v["solutions"]:
            self.assertEqual(sol["shunt"]["kind"], "none")
            self.assertEqual(sol["series"]["kind"], "none")
            self.assertAlmostEqual(sol["loaded_q"], 0.0, places=6)

    def test_no_frequency_no_answer(self):
        r = matching.l_match(100.0, None)
        self.assertEqual(r["status"], "error")


class TestPiT(unittest.TestCase):
    def test_pi_implements_the_requested_q(self):
        v = matching.pi_match(50.0, 200.0, 2.4, q=3.0)["value"]
        self.assertAlmostEqual(v["q"], 3.0, places=3)
        # Q = (Q1 + Q2)/2 with Q1 = sqrt(Rs/Rv - 1), Q2 = sqrt(Rl/Rv - 1)
        rv = v["virtual_resistance_ohm"]
        import math
        q1 = math.sqrt(50.0 / rv - 1.0)
        q2 = math.sqrt(200.0 / rv - 1.0)
        self.assertAlmostEqual((q1 + q2) / 2.0, 3.0, places=3)

    def test_pi_elements_are_low_pass_shape(self):
        v = matching.pi_match(50.0, 200.0, 2.4, q=3.0)["value"]
        kinds = [v["elements"][k]["kind"] for k in ("shunt_source_side", "series",
                                                    "shunt_load_side")]
        self.assertEqual(kinds, ["capacitor_shunt", "inductor_series", "capacitor_shunt"])

    def test_t_is_the_dual(self):
        v = matching.t_match(50.0, 200.0, 2.4, q=3.0)["value"]
        kinds = [v["elements"][k]["kind"] for k in ("series_source_side", "shunt",
                                                    "series_load_side")]
        self.assertEqual(kinds, ["inductor_series", "capacitor_shunt", "inductor_series"])

    def test_higher_q_means_a_lower_virtual_resistance(self):
        lo = matching.pi_match(50.0, 200.0, 2.4, q=2.0)["value"]["virtual_resistance_ohm"]
        hi = matching.pi_match(50.0, 200.0, 2.4, q=5.0)["value"]["virtual_resistance_ohm"]
        self.assertLess(hi, lo)

    def test_a_q_below_the_l_match_minimum_is_refused(self):
        # an L match already needs Q = 1 here; asking for less is not a network
        r = matching.pi_match(50.0, 200.0, 2.4, q=0.5)
        self.assertEqual(r["status"], "error")
        self.assertEqual(r["reason_code"], "ARG_RANGE")

    def test_bad_resistance(self):
        self.assertEqual(matching.pi_match(0.0, 200.0, 2.4)["reason_code"], "ARG_RANGE")


class TestStub(unittest.TestCase):
    def test_textbook_single_stub_for_100_ohm(self):
        v = matching.single_stub(100.0, 2.4)["value"]
        self.assertAlmostEqual(v["lambda_mm"], 124.9135, places=3)
        first = v["solutions"][0]
        # d = 0.348 lambda, open stub 0.098 lambda - the canonical answer
        self.assertAlmostEqual(first["distance_from_load_mm"], 43.4645, places=3)
        self.assertAlmostEqual(first["stub_length_mm"], 12.2361, places=3)

    def test_the_shorter_stub_comes_first(self):
        v = matching.single_stub("150-40j", 2.4)["value"]
        lens = [s["stub_length_mm"] for s in v["solutions"]]
        self.assertEqual(lens, sorted(lens))

    def test_a_short_stub_is_half_a_wavelength_away_from_the_open_one(self):
        op = matching.single_stub(100.0, 2.4, termination="open")["value"]
        sh = matching.single_stub(100.0, 2.4, termination="short")["value"]
        lam = op["lambda_mm"]
        # a short stub of length l behaves like an open stub of l + lambda/4, so at
        # the same stub position the two lengths must differ by a quarter wave
        # modulo a half wave (which is where a stub's impedance repeats)
        by_d = {round(s["distance_from_load_mm"], 4): s["stub_length_mm"]
                for s in sh["solutions"]}
        for o in op["solutions"]:
            d = round(o["distance_from_load_mm"], 4)
            self.assertIn(d, by_d)
            delta = (by_d[d] - o["stub_length_mm"]) % (lam / 2.0)
            self.assertAlmostEqual(delta, lam / 4.0, delta=0.01)

    def test_series_stub_is_declared_missing(self):
        r = matching.single_stub(100.0, 2.4, stub="series")
        self.assertEqual(r["status"], "error")
        self.assertEqual(r["reason_code"], "NOT_IMPLEMENTED")


class TestQuarterWave(unittest.TestCase):
    def test_100_ohm_needs_70_71_ohm_and_a_quarter_wavelength(self):
        v = matching.quarter_wave(100.0, 2.4)["value"]
        self.assertAlmostEqual(v["section_z0_ohm"], 70.7107, places=3)
        self.assertAlmostEqual(v["section_length_mm"], 124.9135 / 4.0, places=3)
        self.assertAlmostEqual(v["resistance_ratio"], 2.0, places=6)

    def test_dielectric_shortens_the_section(self):
        air = matching.quarter_wave(100.0, 2.4, er_eff=1.0)["value"]
        fr4 = matching.quarter_wave(100.0, 2.4, er_eff=4.0)["value"]
        self.assertAlmostEqual(fr4["section_length_mm"], air["section_length_mm"] / 2.0,
                               places=3)
        # the impedance does not depend on the dielectric, only the length does
        self.assertAlmostEqual(fr4["section_z0_ohm"], air["section_z0_ohm"], places=6)

    def test_a_reactive_load_is_refused(self):
        r = matching.quarter_wave("100+80j", 2.4)
        self.assertEqual(r["status"], "error")
        self.assertEqual(r["reason_code"], "OUT_OF_VALIDITY")

    def test_a_large_ratio_carries_a_bandwidth_warning(self):
        v = matching.quarter_wave(1000.0, 2.4)["value"]
        self.assertAlmostEqual(v["resistance_ratio"], 20.0, places=6)
        self.assertIn("narrow", v["bandwidth_warning"].lower())
        # a 2:1 ratio is inside the practical window and carries no warning
        self.assertNotIn("bandwidth_warning", matching.quarter_wave(100.0, 2.4)["value"])


if __name__ == "__main__":
    unittest.main()
