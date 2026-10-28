"""Board-level 2-D electro-thermal: the plate solve, the coupling, the refusals.

The anchors here are arithmetic, not this module's own output:

  * A load spread UNIFORMLY over a plate has an exact answer. With q the total
    dissipation and hc_tot = 2*h*A the steady balance is

        hc_tot*(T - Ta) = q_ref*(1 + alpha*(T - 20))
        T = (hc_tot*Ta + q_ref*(1 - 20*alpha)) / (hc_tot - q_ref*alpha)

    and the runaway threshold is q_ref*alpha = hc_tot, i.e. exactly the 1-D rule
    dP/dT*Rth < 1 with Rth = 1/hc_tot.
  * A uniform load on an adiabatic plate conducts nothing (the field is flat), so
    the transient is a single RC: T(t) = Ta + dT_inf*(1 - exp(-t/tau)) with
    tau = C_tot/hc_tot. Backward Euler is first order and with dt comparable to
    tau its first step is visibly damped - that is measured here, not hidden.
  * A trace's cold resistance is rho20*L/(w*t*layers) and its total I^2R is
    exact even though the distribution is quantised by the grid.

Every refusal below is a case where the honest answer is "no number", so each
one asserts the reason_code rather than a value.
"""
import io
import json
import math
import unittest

from pcb_agent_mcp.calc import electrothermal as et

RHO20 = 1.724e-8
ALPHA = 0.00393
TA = 25.0


def _hc_total(w_mm, h_mm, h):
    """Total two-face convection conductance of the plate, W/K (exact)."""
    return 2.0 * h * (w_mm * 1e-3) * (h_mm * 1e-3)


def _uniform_closed_form(w_mm, h_mm, h, p_w, ta=TA, alpha=ALPHA):
    hc = _hc_total(w_mm, h_mm, h)
    return (hc * ta + p_w * (1.0 - 20.0 * alpha)) / (hc - p_w * alpha)


class TestUniformPlateHasAClosedForm(unittest.TestCase):
    def test_steady_matches_the_closed_form(self):
        r = et.board_electrothermal(50, 50, 1.6, 0.3, 10, [], uniform_power_w=2.0,
                                   cell_mm=2.0)
        self.assertEqual(r["status"], "ok", r)
        want = _uniform_closed_form(50, 50, 10, 2.0)
        self.assertAlmostEqual(r["t_max_c"], want, places=4)
        # and the plate really is isothermal: a uniform source on an adiabatic
        # plate has nowhere to conduct to
        self.assertLess(r["t_max_c"] - r["t_min_c"], 1e-3)

    def test_a_second_operating_point_also_matches(self):
        # 5 W on the same plate: the alpha*T term makes this nonlinear, so a
        # second point is not redundant with the first
        r = et.board_electrothermal(50, 50, 1.6, 0.3, 10, [], uniform_power_w=5.0,
                                   cell_mm=2.0)
        # 6 significant digits is what the envelope reports, so that is the scale
        self.assertAlmostEqual(r["t_max_c"], _uniform_closed_form(50, 50, 10, 5.0),
                               places=3)

    def test_the_reported_power_is_the_hot_power_not_the_20c_power(self):
        r = et.board_electrothermal(50, 50, 1.6, 0.3, 10, [], uniform_power_w=5.0,
                                   cell_mm=2.0)
        t = r["t_max_c"]
        self.assertAlmostEqual(r["total_power_hot_w"],
                               5.0 * (1.0 + ALPHA * (t - 20.0)), places=4)
        self.assertGreater(r["total_power_hot_w"], 5.0)   # copper got worse
        self.assertAlmostEqual(r["total_power_at_20c_w"], 5.0, places=9)

    def test_energy_balances(self):
        r = et.board_electrothermal(50, 50, 1.6, 0.3, 10, [], uniform_power_w=2.0,
                                   cell_mm=2.0)
        eb = r["energy_balance"]
        self.assertAlmostEqual(eb["power_in_w"], eb["power_out_convection_w"],
                               places=4)
        self.assertLess(abs(eb["residual_pct"]), 1e-3)

    def test_effective_rth_is_hot_spot_over_power(self):
        r = et.board_electrothermal(50, 50, 1.6, 0.3, 10, [], uniform_power_w=2.0,
                                   cell_mm=2.0)
        self.assertAlmostEqual(r["effective_rth_k_per_w"],
                               (r["t_max_c"] - TA) / r["total_power_hot_w"],
                               places=3)
        # for a uniform source that Rth is just 1/hc_tot
        self.assertAlmostEqual(r["effective_rth_k_per_w"],
                               1.0 / _hc_total(50, 50, 10), places=2)


class TestRunawayIsRefusedBeforeTheSolve(unittest.TestCase):
    """The 1-D rule dP/dT*Rth < 1 becomes q*alpha < 2hA on a uniform plate."""

    def test_threshold_is_exactly_two_ha_over_alpha(self):
        hc = _hc_total(50, 50, 10)
        limit = hc / ALPHA                      # 12.7226... W
        below = limit * 0.999
        above = limit * 1.001
        r = et.board_electrothermal(50, 50, 1.6, 0.3, 10, [],
                                   uniform_power_w=below, cell_mm=2.0)
        self.assertEqual(r["status"], "ok", r)
        r = et.board_electrothermal(50, 50, 1.6, 0.3, 10, [],
                                   uniform_power_w=above, cell_mm=2.0)
        self.assertEqual(r["status"], "error")
        self.assertEqual(r["reason_code"], "THERMAL_RUNAWAY")

    def test_the_refusal_names_the_offending_diagonal(self):
        r = et.board_electrothermal(50, 50, 1.6, 0.3, 10, [], uniform_power_w=13.0,
                                   cell_mm=2.0)
        self.assertEqual(r["reason_code"], "THERMAL_RUNAWAY")
        # 13 W over 625 cells: 2hA - q*alpha, and it must be <= 0
        self.assertLessEqual(r["worst_diagonal"], 0.0)

    def test_alpha_zero_never_runs_away(self):
        r = et.board_electrothermal(50, 50, 1.6, 0.3, 10, [], uniform_power_w=100.0,
                                   cell_mm=2.0, alpha_per_k=0.0)
        self.assertEqual(r["status"], "ok", r)
        self.assertAlmostEqual(r["t_max_c"], TA + 100.0 / _hc_total(50, 50, 10),
                               places=4)

    def test_a_ridiculous_alpha_is_an_argument_error_not_a_runaway(self):
        r = et.board_electrothermal(50, 50, 1.6, 0.3, 10, [], uniform_power_w=1.0,
                                   cell_mm=5.0, alpha_per_k=0.1)
        self.assertEqual(r["status"], "error")
        self.assertEqual(r["reason_code"], "ARG_RANGE")


class TestTransientIsBackwardEuler(unittest.TestCase):
    def _run(self, points, t_end=5000.0):
        return et.board_electrothermal(
            50, 50, 1.6, 0.3, 10, [], uniform_power_w=2.0, cell_mm=2.0,
            alpha_per_k=0.0,
            transient={"t_end_s": t_end, "points": points, "cp_j_kg_k": 1150,
                       "rho_kg_m3": 1850})

    def test_points_count_the_samples_including_t_zero(self):
        r = self._run(26)
        h = r["transient"]["history"]
        self.assertEqual(len(h), 26)
        self.assertEqual(h[0]["t_s"], 0.0)
        self.assertAlmostEqual(h[-1]["t_s"], 5000.0, places=6)
        self.assertAlmostEqual(r["transient"]["dt_s"], 200.0, places=6)

    def test_late_solution_is_the_steady_answer(self):
        r = self._run(26)
        steady = TA + 2.0 / _hc_total(50, 50, 10)      # alpha = 0 -> linear
        self.assertAlmostEqual(r["transient"]["history"][-1]["t_max_c"], steady,
                               places=2)

    def test_first_step_is_damped_by_exactly_the_backward_euler_factor(self):
        # dT_1 = dT_inf * (dt/tau) / (1 + dt/tau), with tau = C_tot/hc_tot
        r = self._run(26)
        dt = r["transient"]["dt_s"]
        c_tot = r["transient"]["heat_capacity_j_per_k"]
        hc = _hc_total(50, 50, 10)
        tau = c_tot / hc
        d_inf = 2.0 / hc
        want = TA + d_inf * (dt / tau) / (1.0 + dt / tau)
        self.assertAlmostEqual(r["transient"]["history"][1]["t_max_c"], want,
                               places=3)

    def test_the_curve_never_overshoots_the_steady_state(self):
        r = self._run(26)
        steady = TA + 2.0 / _hc_total(50, 50, 10)
        for sample in r["transient"]["history"]:
            self.assertLessEqual(sample["t_max_c"], steady + 1e-3)

    def test_missing_heat_capacity_is_refused(self):
        r = et.board_electrothermal(
            50, 50, 1.6, 0.3, 10, [], uniform_power_w=2.0, cell_mm=5.0,
            transient={"t_end_s": 100.0, "points": 5})
        self.assertEqual(r["status"], "error")
        self.assertIn(r["reason_code"], ("ARG_REQUIRED", "ARG_RANGE"))


class TestTraceConductors(unittest.TestCase):
    def _trace(self, current=3.0, cell_mm=1.0, width=1.0):
        return et.board_electrothermal(
            50, 50, 1.6, 0.3, 10,
            [{"x1_mm": 5, "y1_mm": 25, "x2_mm": 45, "y2_mm": 25,
              "width_mm": width, "thickness_um": 35, "current_a": current}],
            cell_mm=cell_mm)

    def test_cold_resistance_is_the_bar_formula(self):
        r = self._trace()
        self.assertEqual(r["status"], "ok", r)
        c = r["conductors"][0]
        want = RHO20 * 40e-3 / (1.0e-3 * 35e-6)
        self.assertAlmostEqual(c["r20_ohm"], want, places=7)   # 0.0197028571 ohm
        self.assertAlmostEqual(c["power_at_20c_w"], 9.0 * want, places=6)

    def test_hot_resistance_follows_the_hot_temperature(self):
        r = self._trace()
        c = r["conductors"][0]
        self.assertAlmostEqual(c["r_hot_ohm"],
                               c["r20_ohm"] * (1.0 + ALPHA * (c["t_max_c"] - 20.0)),
                               places=6)
        self.assertGreater(c["r_hot_ohm"], c["r20_ohm"])

    def test_heat_lands_somewhere_and_the_plate_is_not_isothermal(self):
        r = self._trace()
        self.assertGreater(r["t_max_c"] - r["t_avg_c"], 0.5)

    def test_a_coarse_grid_understates_the_hot_spot(self):
        # a 1 mm trace on 2 mm cells cannot be resolved: the same watts land in
        # fewer, bigger cells, so the peak must be *lower*. Reporting a single
        # number without cell_mm would hide this.
        fine = self._trace(cell_mm=1.0)["t_max_c"]
        coarse = self._trace(cell_mm=2.0)["t_max_c"]
        self.assertLess(coarse, fine)

    def test_layers_are_parallel_copper(self):
        one = et.board_electrothermal(
            50, 50, 1.6, 0.3, 10,
            [{"x1_mm": 5, "y1_mm": 25, "x2_mm": 45, "y2_mm": 25,
              "width_mm": 1.0, "thickness_um": 35, "current_a": 3}], cell_mm=1.0)
        two = et.board_electrothermal(
            50, 50, 1.6, 0.3, 10,
            [{"x1_mm": 5, "y1_mm": 25, "x2_mm": 45, "y2_mm": 25,
              "width_mm": 1.0, "thickness_um": 35, "current_a": 3,
              "layers": 2}], cell_mm=1.0)
        self.assertAlmostEqual(two["conductors"][0]["r20_ohm"],
                               one["conductors"][0]["r20_ohm"] / 2.0, places=7)

    def test_zero_current_carries_no_heat_and_is_refused(self):
        r = self._trace(current=0.0)
        self.assertEqual(r["status"], "error")
        self.assertEqual(r["reason_code"], "ARG_RANGE")

    def test_a_trace_thinner_than_the_grid_is_refused_not_snapped(self):
        r = self._trace(width=0.05, cell_mm=5.0)
        self.assertEqual(r["status"], "error")
        self.assertEqual(r["reason_code"], "ARG_RANGE")
        self.assertIn("thinner than the grid", r["message"])

    def test_a_trace_outside_the_plate_is_refused(self):
        r = et.board_electrothermal(
            50, 50, 1.6, 0.3, 10,
            [{"x1_mm": 5, "y1_mm": 25, "x2_mm": 60, "y2_mm": 25,
              "width_mm": 1.0, "thickness_um": 35, "current_a": 3}], cell_mm=1.0)
        self.assertEqual(r["status"], "error")
        self.assertEqual(r["reason_code"], "ARG_RANGE")

    def test_zero_length_is_refused(self):
        r = et.board_electrothermal(
            50, 50, 1.6, 0.3, 10,
            [{"x1_mm": 5, "y1_mm": 25, "x2_mm": 5, "y2_mm": 25,
              "width_mm": 1.0, "thickness_um": 35, "current_a": 3}], cell_mm=1.0)
        self.assertEqual(r["reason_code"], "ARG_RANGE")


class TestEnvelopeHonesty(unittest.TestCase):
    def test_h_is_never_defaulted(self):
        r = et.board_electrothermal(50, 50, 1.6, 0.3, None, [],
                                   uniform_power_w=2.0, cell_mm=5.0)
        self.assertEqual(r["reason_code"], "ARG_REQUIRED")

    def test_no_source_at_all_is_refused(self):
        r = et.board_electrothermal(50, 50, 1.6, 0.3, 10, [], cell_mm=5.0)
        self.assertEqual(r["reason_code"], "ARG_REQUIRED")

    def test_grid_explosion_is_refused_with_the_cell_count(self):
        r = et.board_electrothermal(50, 50, 1.6, 0.3, 10, [],
                                   uniform_power_w=2.0, cell_mm=0.1,
                                   max_cells=20000)
        self.assertEqual(r["reason_code"], "TOO_LARGE")
        self.assertIn("250000", r["message"])

    def test_the_laminate_is_named_as_the_spreader(self):
        # the single most misleading thing this module could do is let a caller
        # believe the ground plane is doing the spreading
        r = et.board_electrothermal(50, 50, 1.6, 0.3, 10, [], uniform_power_w=2.0,
                                   cell_mm=5.0)
        nc = " ".join(r["not_computed"])
        self.assertIn("NOT through the real copper pours", nc)
        self.assertIn("thermal vias", nc)
        self.assertIn("radiation", nc)
        self.assertFalse(r["authoritative"])
        self.assertEqual(r["cell_mm"], [5.0, 5.0])

    def test_the_map_is_drawn_and_its_hottest_cell_is_the_reported_one(self):
        r = et.board_electrothermal(
            50, 50, 1.6, 0.3, 10,
            [{"x1_mm": 25, "y1_mm": 5, "x2_mm": 25, "y2_mm": 45,
              "width_mm": 1.0, "thickness_um": 35, "current_a": 3}], cell_mm=1.0)
        self.assertEqual(r["status"], "ok", r)
        self.assertEqual(len(r["map"]), 16)
        joined = "".join(r["map"])
        self.assertIn("@", joined)
        self.assertIn(r["map_legend"], ("row 0 is y=0; '@' is the hottest cell",))


class TestToolSurface(unittest.TestCase):
    def test_the_tool_is_registered_and_its_description_says_2d(self):
        from pcb_agent_mcp import tools
        self.assertIn("board_electrothermal", tools.TOOLS)
        self.assertIn("2-D", tools.TOOLS["board_electrothermal"]["description"])

    def test_the_tool_round_trips_through_the_handler(self):
        from pcb_agent_mcp import tools
        r = tools.TOOLS["board_electrothermal"]["handler"](
            {"width_mm": 50, "height_mm": 50, "thickness_mm": 1.6, "k_w_mk": 0.3,
             "h_w_m2k": 10, "uniform_power_w": 2.0, "cell_mm": 2.0})
        self.assertEqual(r["status"], "ok", r)
        self.assertAlmostEqual(r["t_max_c"],
                               _uniform_closed_form(50, 50, 10, 2.0), places=3)

    def test_capabilities_stop_claiming_the_loop_is_one_lump(self):
        from pcb_agent_mcp import tools
        prose = json.dumps(tools.TOOLS["calc_capabilities"]["handler"]({}),
                           ensure_ascii=False)
        self.assertIn("board_electrothermal", prose)
        self.assertNotIn("back into a temperature field - that last one is still "
                         "the user\'s to iterate", prose)


if __name__ == "__main__":
    unittest.main()
