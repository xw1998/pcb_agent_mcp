"""Golden tests for the thermal calculators.

Same rule as the other golden files: the expected number is derived in the
comment from the formula, and the refusals are part of the contract. Two of the
refusals here exist because the alternative is a plausible wrong number:
a two-dimensional plate refuses to run without a convection coefficient (it
would otherwise have no way to lose heat and would report the adiabatic
temperature), and it reports the energy balance so a field that "looks right"
can be checked against conservation.
"""
import math
import os
import sys
import unittest

sys.path.insert(0, os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "src"))

from pcb_agent_mcp.calc import thermal  # noqa: E402


def _v(r):
    assert r["status"] == "ok", r
    return r["value"]


class TestStack(unittest.TestCase):
    def test_fr4_by_hand(self):
        # Rth = t/(k*A) = 1.6e-3 / (0.3 * 100e-6) = 53.3333 K/W
        v = _v(thermal.thermal_stack(
            [{"thickness_mm": 1.6, "k_w_mk": 0.3, "name": "FR4"}], 100.0))
        self.assertAlmostEqual(v["rth_k_per_w"], 53.333333333, places=8)
        self.assertAlmostEqual(v["g_thermal_w_per_k"], 1 / 53.333333333, places=8)

    def test_power_turns_rth_into_a_rise(self):
        v = _v(thermal.thermal_stack(
            [{"thickness_mm": 1.6, "k_w_mk": 0.3}], 100.0, power_w=0.5))
        self.assertAlmostEqual(v["delta_t_k"], 26.666667, places=6)

    def test_layers_are_in_series(self):
        # two identical FR4 layers are twice one: 106.6667 K/W
        v = _v(thermal.thermal_stack(
            [{"thickness_mm": 1.6, "k_w_mk": 0.3},
             {"thickness_mm": 1.6, "k_w_mk": 0.3}], 100.0))
        self.assertAlmostEqual(v["rth_k_per_w"], 106.666666667, places=8)
        self.assertEqual([l["share_pct"] for l in v["layers"]], [50.0, 50.0])

    def test_copper_layer_dominates_a_stack(self):
        # 0.1 mm of copper over 1.6 mm of FR4: the copper is 0.1e-3/(385*100e-6)
        # = 0.0026 K/W against 53.3, so the stack is still 53.33 K/W and the
        # copper's share rounds to ~0 %
        v = _v(thermal.thermal_stack(
            [{"thickness_mm": 1.6, "k_w_mk": 0.3},
             {"thickness_mm": 0.1, "k_w_mk": 385.0}], 100.0))
        self.assertAlmostEqual(v["rth_k_per_w"], 53.33, places=1)
        self.assertLess(v["layers"][1]["share_pct"], 0.1)

    def test_bad_input_is_refused(self):
        self.assertEqual(thermal.thermal_stack([], 100.0)["reason_code"], "ARG_REQUIRED")
        self.assertEqual(thermal.thermal_stack(
            [{"thickness_mm": 1.6, "k_w_mk": 0.3}], 0.0)["reason_code"], "ARG_RANGE")
        self.assertEqual(thermal.thermal_stack(
            [{"thickness_mm": 1.6, "k_w_mk": 0.3}], 100.0,
            power_w=float("nan"))["reason_code"], "ARG_INVALID")


class TestViaArray(unittest.TestCase):
    def test_single_barrel_by_hand(self):
        # 0.3 mm drill, 25 um plating, 1.6 mm board:
        #   A = pi*(0.15^2 - 0.125^2) = 0.0215984 mm^2 = 2.15984e-8 m^2
        #   Rth = 1.6e-3 / (385 * 2.15984e-8) = 192.414 K/W
        v = _v(thermal.via_array_thermal(0.3, 25.0, 1.6))
        self.assertAlmostEqual(v["rth_one_via_k_per_w"], 192.414004399, places=6)
        self.assertAlmostEqual(v["barrel_area_mm2"], 0.021598449, places=9)
        # copper fills 30.5556 % of the drilled hole's cross-section
        self.assertAlmostEqual(v["fill_ratio_area_pct"], 30.5556, places=4)

    def test_count_divides(self):
        v = _v(thermal.via_array_thermal(0.3, 25.0, 1.6, count=9))
        self.assertAlmostEqual(v["rth_array_k_per_w"], 192.414004399 / 9.0, places=6)

    def test_pitch_adds_the_board_around_each_hole(self):
        # with a 1 mm pitch each cell is 1x1 mm of which 0.0215984 mm^2 is
        # copper: Rth_cell = 1/( (0.0215984/385) + ((1-0.0215984)/0.3) ) per mm
        # -> 185.714 K/W per cell, 20.635 for 9
        v = _v(thermal.via_array_thermal(0.3, 25.0, 1.6, count=9, pitch_mm=1.0))
        self.assertAlmostEqual(v["rth_per_cell_with_board_k_per_w"], 185.713888229,
                               places=6)
        self.assertAlmostEqual(v["rth_array_with_board_k_per_w"], 20.63487647,
                               places=6)
        self.assertAlmostEqual(v["rth_pure_fr4_same_area_k_per_w"], 592.592592593,
                               places=6)

    def test_plating_thicker_than_the_radius_is_a_unit_error(self):
        r = thermal.via_array_thermal(0.02, 25.0, 1.6)
        self.assertEqual(r["status"], "error")
        self.assertEqual(r["reason_code"], "ARG_RANGE")


class TestPlate(unittest.TestCase):
    def test_uniform_high_k_plate_matches_the_analytic_surface(self):
        # k = 1000, t = 1.6 mm on 100x100 mm, h = 10 on both faces, 1 W:
        # the plate is isothermal, so ta + P/(2*h*A) = 25 + 1/(2*10*0.01) = 30 C
        v = _v(thermal.plate_temperature(
            100.0, 100.0, 1.6, 1000.0, 10.0,
            [{"x_mm": 50, "y_mm": 50, "power_w": 1.0, "size_mm": 5.0}]))
        self.assertAlmostEqual(v["t_avg_c"], 30.0, delta=0.01)
        self.assertLess(abs(v["energy_balance"]["residual_pct"]), 1.0)

    def test_fr4_plate_has_a_real_hot_spot_and_conserves_energy(self):
        v = _v(thermal.plate_temperature(
            30.0, 30.0, 1.6, 0.3, 10.0,
            [{"x_mm": 15, "y_mm": 15, "power_w": 2.0, "size_mm": 3.0}],
            cell_mm=0.5))
        # 2 W through 1.6 mm of FR4 over a 3 mm pad: the K/W are huge, so the
        # hot spot is ~1.2 kK above ambient while the edges stay near it
        self.assertGreater(v["t_max_c"], 1000.0)
        self.assertEqual(v["sources"][0]["cells_spread"], 32)
        self.assertLess(abs(v["energy_balance"]["residual_pct"]), 0.5)
        self.assertGreater(v["spread_k"], 500.0)

    def test_fixed_edge_boundary_conserves_energy_too(self):
        # a small h plus a 25 C edge ring: the rim becomes the heat path, and the
        # bookkeeping must show the power leaving through it (this path had a
        # sign error that made the balance read 199 %)
        v = _v(thermal.plate_temperature(
            30.0, 30.0, 1.6, 0.3, 1.0,
            [{"x_mm": 15, "y_mm": 15, "power_w": 1.0, "size_mm": 3.0}],
            edge_temp_c=25.0, cell_mm=1.0))
        eb = v["energy_balance"]
        self.assertGreater(eb["power_out_fixed_edges_w"], 0.0)
        self.assertLess(abs(eb["residual_pct"]), 0.5)
        self.assertAlmostEqual(v["t_min_c"], 25.0, places=6)

    def test_no_convection_coefficient_is_refused(self):
        # h = 0 would be an adiabatic plate: the answer would be "however hot
        # the grid says", which is not a temperature
        r = thermal.plate_temperature(
            30.0, 30.0, 1.6, 0.3, 0.0,
            [{"x_mm": 15, "y_mm": 15, "power_w": 1.0}], cell_mm=1.0)
        self.assertEqual(r["status"], "error")
        self.assertEqual(r["reason_code"], "ARG_REQUIRED")

    def test_point_source_warns_that_the_hot_spot_is_a_grid_artefact(self):
        r = thermal.plate_temperature(
            30.0, 30.0, 1.6, 0.3, 10.0,
            [{"x_mm": 15, "y_mm": 15, "power_w": 1.0}], cell_mm=1.0)
        self.assertEqual(r["status"], "ok")
        self.assertTrue(any("cell" in w for w in r["warnings"]), r.get("warnings"))


class TestTransient(unittest.TestCase):
    def test_single_time_constant(self):
        # Rth = 10 K/W, Cth = 1 J/K -> tau = 10 s; P = 2 W.
        # At t = tau: dT = 20*(1-e^-1) = 12.6424 K
        v = _v(thermal.thermal_transient(10.0, 1.0, 2.0, 10.0))
        self.assertAlmostEqual(v["steady_delta_t_k"], 20.0, places=9)
        self.assertAlmostEqual(v["delta_t_at_end_k"], 12.642411177, places=6)
        self.assertEqual(v["time_constants_s"], [10.0])

    def test_foster_ladder_is_the_sum(self):
        # three stages: steady = P*sum(R) = 2*26 = 52 K
        v = _v(thermal.thermal_transient([1, 5, 20], [0.1, 0.6, 3], 2.0, 30.0))
        self.assertAlmostEqual(v["steady_delta_t_k"], 52.0, places=9)
        self.assertEqual(v["time_constants_s"], [0.1, 3.0, 60.0])
        # at 30 s the two fast stages are done (6 K+10 K) and the slow one has
        # reached 1-e^-0.5 of its 40 K
        hand = 2 * 1 * (1 - math.exp(-300)) + 2 * 5 * (1 - math.exp(-10)) + \
            2 * 20 * (1 - math.exp(-0.5))
        self.assertAlmostEqual(v["delta_t_at_end_k"], hand, places=6)

    def test_mismatched_lengths_are_refused(self):
        self.assertEqual(thermal.thermal_transient([1, 2], [1], 1.0, 1.0)
                         ["reason_code"], "ARG_INVALID")


class TestHeatsink(unittest.TestCase):
    def test_budget_by_hand(self):
        # (125-40)/2 = 42.5 K/W total; minus 1.5 junction-case and 0.5
        # case-sink = 40.5 K/W for the sink; target = 0.8*40.5 = 32.4
        v = _v(thermal.heatsink_select(2.0, 125.0, 40.0, 1.5, 0.5))
        self.assertAlmostEqual(v["required_rth_total_k_per_w"], 42.5, places=9)
        self.assertAlmostEqual(v["rth_sa_budget_k_per_w"], 40.5, places=9)
        self.assertAlmostEqual(v["rth_sa_target_k_per_w"], 32.4, places=9)

    def test_impossible_ask_says_infeasible(self):
        r = thermal.heatsink_select(50.0, 125.0, 40.0, 1.5, 0.5)
        self.assertEqual(r["status"], "error")
        self.assertEqual(r["reason_code"], "INFEASIBLE")
        self.assertIn("2.000", r["message"])

    def test_margin_above_one_is_a_probability_error(self):
        self.assertEqual(thermal.heatsink_select(2.0, 125.0, 40.0, 1.5, 0.5,
                                                 safety_margin=1.4)["reason_code"],
                         "ARG_RANGE")

    def test_ambient_above_the_limit_is_refused(self):
        self.assertEqual(thermal.heatsink_select(2.0, 30.0, 40.0, 1.5, 0.5)
                         ["reason_code"], "ARG_RANGE")


class TestConvection(unittest.TestCase):
    def test_forced_by_hand(self):
        # h = 10.45 - 2 + 10*sqrt(2) = 22.5921
        self.assertAlmostEqual(_v(thermal.convection_coefficient(2.0))["h_w_m2k"],
                               22.592136, places=6)

    def test_natural_needs_a_length(self):
        v = _v(thermal.convection_coefficient(0.0, 0.1, mode="natural"))
        self.assertAlmostEqual(v["h_w_m2k"], 6.350433, places=6)
        self.assertEqual(thermal.convection_coefficient(0.0, mode="natural")
                         ["reason_code"], "ARG_REQUIRED")

    def test_still_air_is_not_forced_convection(self):
        self.assertEqual(thermal.convection_coefficient(0.0)["reason_code"], "ARG_RANGE")
        self.assertEqual(thermal.convection_coefficient(-1.0)["reason_code"],
                         "ARG_RANGE")

    def test_correlation_says_it_is_a_correlation(self):
        r = thermal.convection_coefficient(2.0)
        self.assertIn("25", r["precision"])


if __name__ == "__main__":
    unittest.main()
