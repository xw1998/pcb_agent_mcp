"""Radiation and thermal vias in the board 2-D electro-thermal solve.

Both are the SAME algebraic object - a per-cell linear conductance to a
temperature that is not solved for - so both are anchors on arithmetic, not on
this module's own output:

  * Radiation is Stefan-Boltzmann with an EXACT rewrite: q = eps*sigma*(T^4 -
    Ta^4) == h_rad*(T - Ta) with h_rad = eps*sigma*(T + Ta)*(T^2 + Ta^2) in
    kelvin. h_rad is hand-computable, so the reported h_rad is checked against
    the identity, not against a previous run.
  * A via array's total conductance is a hand number: k_cu*pi*(r_out^2 - r_in^2)
    /span per barrel, so a field of them is EXACTLY the same problem as running
    with h raised by G_total/(2*A_cell*N_cells). The two runs have to agree, and
    that equality is what proves the vias were wired into the field instead of
    just being reported.
  * The isothermal limit has a closed form when the plate is thick enough that
    conduction beats everything else: P*(1+alpha*(T-20)) = 2*h*A*(T-Ta) +
    2*eps*sigma*A*(T^4 - Ta^4). k = 100 W/(m*K) must land on that root.

The negative controls, without which "it got colder" only means "a number got
smaller":

  * eps = 0 must be bit-for-bit the run that never mentioned radiation.
  * The same via field, same count, same copper, moved off the trace must move
    the peak by a measured tiny amount (~2e-4 K here).

Refusals assert reason_code, because in each case the honest answer is "no
number".
"""
import math
import unittest

from pcb_agent_mcp.calc import electrothermal as et

SIGMA = 5.670374419e-8
K_LAM = 0.3
T_BOARD_MM = 1.6
H = 10.0
W, HH = 100.0, 80.0
CELL = 2.0            # -> 50 x 40 cells, each exactly 2 x 2 mm
NX, NY = 50, 40
K_CU = 400.0
AC = (CELL * 1e-3) ** 2      # cell area in m^2
UW, UH = 20.0, 20.0          # the small uniform board -> 10 x 10 cells
UNX, UNY = 10, 10

# measured anchors (2026-10-30), with the trace at x = 10..90 mm, y = 40 mm
BARE_TMAX = 36.082953
EPS09_TMAX = 33.360543


def _h_rad_hand(t_c, ta_c, eps):
    t1 = t_c + 273.15
    t2 = ta_c + 273.15
    return eps * SIGMA * (t1 + t2) * (t1 * t1 + t2 * t2)


def _analytic_uniform_root(p_w, alpha, ta_c, eps, side_m, h):
    """Root of P*(1+alpha*(T-20)) = 2hA(T-Ta) + 2*eps*sigma*A*(T^4-Ta^4)."""
    area = side_m * side_m
    ta = ta_c + 273.15

    def f(t_k):
        return (p_w * (1.0 + alpha * (t_k - 273.15 - 20.0))
                - 2.0 * h * area * (t_k - ta)
                - 2.0 * eps * SIGMA * area * (t_k ** 4 - ta ** 4))

    lo, hi = ta, ta + 500.0
    for _ in range(300):
        mid = 0.5 * (lo + hi)
        if f(mid) > 0.0:
            lo = mid
        else:
            hi = mid
    return lo - 273.15


class _Base(unittest.TestCase):
    def _common(self):
        return dict(width_mm=W, height_mm=HH, thickness_mm=T_BOARD_MM,
                    k_w_mk=K_LAM, h_w_m2k=H, t_ambient_c=25.0, cell_mm=CELL,
                    conductors=[{"x1_mm": 10, "y1_mm": 40, "x2_mm": 90,
                                 "y2_mm": 40, "width_mm": 3.0,
                                 "thickness_um": 35.0, "current_a": 4.0}])

    def _run(self, **kw):
        args = self._common()
        args.update(kw)
        return et.board_electrothermal(**args)

    def _uniform(self, **kw):
        args = dict(width_mm=UW, height_mm=UH, thickness_mm=T_BOARD_MM,
                    k_w_mk=K_LAM, h_w_m2k=H, t_ambient_c=25.0, cell_mm=CELL,
                    uniform_power_w=0.02, conductors=None)
        args.update(kw)
        return et.board_electrothermal(**args)

    def _via(self, **kw):
        v = dict(x1_mm=1.0, y1_mm=1.0, x2_mm=19.0, y2_mm=19.0,
                 pitch_mm=2.0, d_mm=0.3, plating_um=25.0)
        v.update(kw)
        return [v]


class TestWithoutTheNewParametersNothingChanges(_Base):
    def test_radiation_is_off_and_the_answer_says_why(self):
        r = self._run()
        self.assertEqual(r["status"], "ok", r)
        rad = r["radiation"]
        self.assertFalse(rad["modelled"])
        self.assertIn("convection", rad["why"])

    def test_vias_are_off_and_the_answer_says_why(self):
        r = self._run()
        v = r["thermal_vias"]
        self.assertFalse(v["modelled"])
        self.assertIn("far cooler", v["why"])

    def test_zero_emissivity_is_bit_identical_to_no_emissivity(self):
        base = self._run()
        zero = self._run(emissivity=0.0)
        self.assertEqual(zero["status"], "ok", zero)
        for key in ("t_max_c", "t_min_c", "t_avg_c", "delta_t_max_k"):
            self.assertEqual(zero[key], base[key], key)
        # and it must not pretend radiation was modelled
        self.assertFalse(zero["radiation"]["modelled"])

    def test_no_vias_list_is_bit_identical_to_an_empty_run(self):
        base = self._run()
        empty = self._run(thermal_vias=None)
        for key in ("t_max_c", "t_avg_c"):
            self.assertEqual(empty[key], base[key], key)
        self.assertFalse(empty["thermal_vias"]["modelled"])

    def test_the_not_computed_list_still_names_both_when_they_are_off(self):
        nc = self._run()["not_computed"]
        self.assertTrue(any(s == "radiation (matters above ~60 C)" for s in nc))
        self.assertTrue(any(s == "thermal vias" for s in nc))

    def test_the_not_computed_list_stops_naming_them_when_they_are_on(self):
        nc = self._run(emissivity=0.9, thermal_vias=self._via())["not_computed"]
        self.assertFalse(any(s == "radiation (matters above ~60 C)" for s in nc))
        self.assertFalse(any(s == "thermal vias" for s in nc))
        self.assertTrue(any("grey body" in s for s in nc))
        self.assertTrue(any("one barrel" in s for s in nc))


class TestRadiation(_Base):
    def test_radiation_lowers_the_peak(self):
        self.assertAlmostEqual(self._run()["t_max_c"], BARE_TMAX, places=6)
        hot = self._run(emissivity=0.9)
        self.assertAlmostEqual(hot["t_max_c"], EPS09_TMAX, places=6)
        self.assertGreater(BARE_TMAX - hot["t_max_c"], 2.5,
                           "eps 0.9 on a 36 C board must move the peak visibly")

    def test_more_emissivity_is_monotonically_colder(self):
        peaks = [self._run(emissivity=e)["t_max_c"]
                 for e in (0.05, 0.2, 0.5, 0.9, 1.0)]
        for a, b in zip(peaks, peaks[1:]):
            self.assertLess(b, a, peaks)

    def test_h_rad_is_the_exact_identity_hand_computed(self):
        r = self._run(emissivity=0.9)
        hr = r["radiation"]["h_rad_w_m2k"]
        want = _h_rad_hand(r["t_max_c"], 25.0, 0.9)
        self.assertAlmostEqual(hr["at_t_max"], want, places=5)
        # at T = Ta the identity collapses to 4*eps*sigma*Ta^3, also hand-worked
        self.assertAlmostEqual(hr["at_t_ambient"],
                               4.0 * 0.9 * SIGMA * (298.15 ** 3), places=5)
        self.assertLess(hr["at_t_ambient"], hr["at_t_max"],
                        "h_rad rises with temperature, that is why it iterates")

    def test_the_linearisation_is_a_fixed_point_not_one_pass(self):
        r = self._run(emissivity=0.9)
        rad = r["radiation"]
        self.assertGreaterEqual(rad["outer_iterations"], 2)
        self.assertIn("lagged", rad["linearisation"])
        self.assertIn("fixed point", rad["linearisation"])

    def test_energy_balances_with_radiation(self):
        r = self._run(emissivity=0.9)
        eb = r["energy_balance"]
        self.assertGreater(eb["power_out_radiation_w"], 0.0)
        self.assertLess(abs(eb["residual_pct"]), 0.01, eb)
        # the two paths add up to everything that leaves
        self.assertAlmostEqual(
            eb["power_out_convection_w"] + eb["power_out_radiation_w"],
            eb["power_in_w"], delta=0.001 * eb["power_in_w"])

    def test_the_share_leaving_by_radiation_is_a_third_of_it(self):
        share = self._run(emissivity=0.9)["radiation"]["share_of_heat_out_pct"]
        self.assertGreater(share, 30.0)
        self.assertLess(share, 40.0)

    def test_the_isothermal_limit_matches_the_analytic_root(self):
        # k large enough that the plate is almost uniform; the root is hand-worked
        side = 50.0
        r = et.board_electrothermal(
            width_mm=side, height_mm=side, thickness_mm=T_BOARD_MM,
            k_w_mk=100.0, h_w_m2k=H, t_ambient_c=25.0, cell_mm=CELL,
            uniform_power_w=2.0, emissivity=0.9, tol_k=1e-6, conductors=None)
        self.assertEqual(r["status"], "ok", r)
        want = _analytic_uniform_root(2.0, 0.00393, 25.0, 0.9, side * 1e-3, H)
        self.assertAlmostEqual(want, 52.85045568, places=6)      # the anchor
        self.assertAlmostEqual(r["t_max_c"], want, delta=2e-3)
        # isothermal really means isothermal: that is what makes the root apply
        self.assertLess(r["spread_k"], 1e-3)

    def test_a_hotter_ambient_radiates_more(self):
        cold = self._run(emissivity=0.9)["radiation"]["heat_out_w"]
        warm = self._run(emissivity=0.9, t_ambient_c=45.0)["radiation"]["heat_out_w"]
        self.assertGreater(warm, cold)

    def test_emissivity_outside_zero_to_one_is_refused(self):
        for bad in (1.1, -0.1):
            r = self._run(emissivity=bad)
            self.assertEqual(r["status"], "error")
            self.assertEqual(r["reason_code"], "ARG_RANGE", bad)

    def test_a_non_numeric_emissivity_is_refused(self):
        r = self._run(emissivity="hot")
        self.assertEqual(r["reason_code"], "ARG_INVALID")


class TestThermalVias(_Base):
    def test_one_via_is_the_hand_annulus(self):
        r = self._uniform(thermal_vias=self._via())
        a = r["thermal_vias"]["arrays"][0]
        r_out = 0.15
        r_in = 0.15 - 0.025
        a_mm2 = math.pi * (r_out ** 2 - r_in ** 2)
        self.assertAlmostEqual(a["barrel_area_mm2"], a_mm2, places=6)
        g = K_CU * (a_mm2 * 1e-6) / (T_BOARD_MM * 1e-3)
        self.assertAlmostEqual(g, 0.005399612373357457, places=12)
        self.assertAlmostEqual(a["conductance_per_via_w_per_k"], g, places=6)
        # a 0.3 mm drill with 25 um plating is 30.6% copper by area, not 100%
        self.assertAlmostEqual(a["barrel_area_over_drill_area_pct"],
                               100.0 * a_mm2 / (math.pi * r_out ** 2), places=3)
        self.assertLess(a["barrel_area_over_drill_area_pct"], 35.0)

    def test_a_via_field_equals_the_equivalent_h(self):
        nob = self._uniform()
        via = self._uniform(thermal_vias=self._via())
        self.assertEqual(via["status"], "ok", via)
        self.assertEqual(via["thermal_vias"]["vias_counted"], 100)
        g_tot = via["thermal_vias"]["total_conductance_w_per_k"]
        h_eq = H + g_tot / (2.0 * AC * UNX * UNY)
        eq = self._uniform(h_w_m2k=h_eq)
        self.assertEqual(eq["status"], "ok", eq)
        self.assertAlmostEqual(eq["t_max_c"], via["t_max_c"], places=4)
        # and the vias are not nothing: 100 barrels to ambient really cools it
        self.assertGreater(nob["t_max_c"] - via["t_max_c"], 2.0)

    def test_the_equivalent_h_is_reported_per_cell(self):
        r = self._uniform(thermal_vias=self._via())
        g_tot = r["thermal_vias"]["total_conductance_w_per_k"]
        self.assertAlmostEqual(r["thermal_vias"]["equivalent_h_w_m2k"],
                               g_tot / (2.0 * AC * UNX * UNY), places=2)
        self.assertAlmostEqual(r["thermal_vias"]["equivalent_h_w_m2k"],
                               674.952, places=2)

    def test_the_same_field_under_the_trace_cools_and_far_away_does_not(self):
        bare = self._run()["t_max_c"]
        under = self._run(thermal_vias=self._via(
            x1_mm=0.0, y1_mm=34.0, x2_mm=100.0, y2_mm=46.0))["t_max_c"]
        away = self._run(thermal_vias=self._via(
            x1_mm=0.0, y1_mm=0.0, x2_mm=100.0, y2_mm=12.0))["t_max_c"]
        self.assertGreater(bare - under, 5.0,
                           "a via field under a 4 A trace must do something visible")
        self.assertLess(abs(bare - away), 0.01,
                        "the same copper in a cold corner must not help")
        self.assertGreaterEqual(bare - away, 0.0)

    def test_the_two_placements_are_the_same_field(self):
        a = self._run(thermal_vias=self._via(
            x1_mm=0.0, y1_mm=34.0, x2_mm=100.0, y2_mm=46.0))["thermal_vias"]
        b = self._run(thermal_vias=self._via(
            x1_mm=0.0, y1_mm=0.0, x2_mm=100.0, y2_mm=12.0))["thermal_vias"]
        self.assertEqual(a["vias_counted"], b["vias_counted"])
        self.assertAlmostEqual(a["total_conductance_w_per_k"],
                               b["total_conductance_w_per_k"], places=9)
        # identical hardware, identical count -> different answer only by place
        self.assertNotAlmostEqual(
            self._run(thermal_vias=self._via(
                x1_mm=0.0, y1_mm=34.0, x2_mm=100.0, y2_mm=46.0))["t_max_c"],
            self._run(thermal_vias=self._via(
                x1_mm=0.0, y1_mm=0.0, x2_mm=100.0, y2_mm=12.0))["t_max_c"],
            places=2)

    def test_a_via_lands_in_the_one_cell_holding_its_centre(self):
        one = self._run(thermal_vias=self._via(
            x1_mm=99.0, y1_mm=40.0, x2_mm=99.0, y2_mm=40.0,
            pitch_mm=2.0))["thermal_vias"]
        self.assertEqual(one["vias_counted"], 1)
        self.assertEqual(one["arrays"][0]["vias_per_axis"], [1, 1])
        self.assertEqual(one["arrays"][0]["vias_off_grid"], 0)

    def test_off_grid_centres_are_counted_not_silently_dropped(self):
        # the last row at y = 80 mm falls on the far edge, index ny -> outside
        r = self._run(thermal_vias=self._via(
            x1_mm=0.0, y1_mm=0.0, x2_mm=100.0, y2_mm=80.0))
        self.assertEqual(r["status"], "ok", r)
        a = r["thermal_vias"]["arrays"][0]
        self.assertEqual(a["vias_per_axis"], [51, 41])
        self.assertGreater(a["vias_off_grid"], 0)
        self.assertEqual(a["vias_counted"] + a["vias_off_grid"],
                         a["vias_asked"])

    def test_a_cold_sink_may_drag_the_field_below_the_ambient(self):
        r = self._run(thermal_vias=self._via(
            x1_mm=0.0, y1_mm=34.0, x2_mm=100.0, y2_mm=46.0, sink_c=0.0))
        self.assertEqual(r["status"], "ok", r)
        self.assertLess(r["t_min_c"], 1.0)
        self.assertEqual(r["thermal_vias"]["arrays"][0]["sink_c_source"], "caller")

    def test_the_sink_defaults_to_the_ambient_and_says_so(self):
        r = self._run(thermal_vias=self._via())
        self.assertEqual(r["thermal_vias"]["arrays"][0]["sink_c"], 25.0)
        self.assertEqual(r["thermal_vias"]["arrays"][0]["sink_c_source"],
                         "ambient")

    def test_the_span_defaults_to_the_board_thickness(self):
        a = self._run(thermal_vias=self._via())["thermal_vias"]["arrays"][0]
        self.assertAlmostEqual(a["span_mm"], T_BOARD_MM, places=9)
        half = self._run(thermal_vias=self._via(
            span_mm=0.8))["thermal_vias"]["arrays"][0]
        self.assertAlmostEqual(half["conductance_per_via_w_per_k"],
                               2.0 * a["conductance_per_via_w_per_k"], places=6)

    def test_energy_balances_with_vias(self):
        r = self._run(thermal_vias=self._via(
            x1_mm=0.0, y1_mm=34.0, x2_mm=100.0, y2_mm=46.0))
        eb = r["energy_balance"]
        self.assertGreater(eb["power_out_vias_w"], 0.0)
        self.assertLess(abs(eb["residual_pct"]), 0.5, eb)

    def test_energy_balances_with_both_at_once(self):
        r = self._run(emissivity=0.9, thermal_vias=self._via(
            x1_mm=0.0, y1_mm=34.0, x2_mm=100.0, y2_mm=46.0))
        eb = r["energy_balance"]
        self.assertGreater(eb["power_out_radiation_w"], 0.0)
        self.assertGreater(eb["power_out_vias_w"], 0.0)
        self.assertLess(abs(eb["residual_pct"]), 0.5, eb)
        self.assertIn("+ radiation", r["model"])
        self.assertIn("+ thermal vias", r["model"])

    def test_two_arrays_add_their_conductance(self):
        one = self._run(thermal_vias=self._via())["thermal_vias"]
        two = self._run(thermal_vias=self._via() + [dict(
            x1_mm=1.0, y1_mm=41.0, x2_mm=19.0, y2_mm=59.0,
            pitch_mm=2.0, d_mm=0.3, plating_um=25.0)])["thermal_vias"]
        self.assertEqual(len(two["arrays"]), 2)
        self.assertEqual(two["vias_counted"], 2 * one["vias_counted"])
        self.assertAlmostEqual(two["total_conductance_w_per_k"],
                               2.0 * one["total_conductance_w_per_k"],
                               places=4)

    def test_a_plating_thicker_than_the_radius_is_refused(self):
        r = self._run(thermal_vias=self._via(plating_um=150.0))
        self.assertEqual(r["reason_code"], "ARG_RANGE")
        self.assertIn("solid", r["message"])

    def test_non_positive_via_dimensions_are_refused(self):
        for key in ("pitch_mm", "d_mm", "plating_um", "span_mm"):
            r = self._run(thermal_vias=self._via(**{key: 0.0}))
            self.assertEqual(r["reason_code"], "ARG_RANGE", key)

    def test_an_array_outside_the_plate_is_refused(self):
        r = self._run(thermal_vias=self._via(x2_mm=W + 10.0))
        self.assertEqual(r["reason_code"], "ARG_RANGE")
        self.assertIn("outside", r["message"])

    def test_an_array_with_no_centre_on_the_board_is_refused(self):
        r = self._run(thermal_vias=self._via(
            x1_mm=W, y1_mm=40.0, x2_mm=W, y2_mm=40.0))
        self.assertEqual(r["reason_code"], "ARG_RANGE")
        self.assertIn("no via centre", r["message"])

    def test_a_via_array_missing_a_key_is_refused_with_the_key(self):
        r = self._run(thermal_vias=[{"x1_mm": 0, "y1_mm": 0, "x2_mm": 10,
                                     "y2_mm": 10, "pitch_mm": 2,
                                     "d_mm": 0.3}])
        self.assertEqual(r["reason_code"], "ARG_INVALID")
        self.assertIn("plating_um", r["message"])

    def test_thermal_vias_that_are_not_a_list_are_refused(self):
        r = self._run(thermal_vias="a field of vias")
        self.assertEqual(r["reason_code"], "ARG_INVALID")

    def test_a_via_array_that_is_not_an_object_is_refused(self):
        r = self._run(thermal_vias=[[1, 1, 19, 19]])
        self.assertEqual(r["reason_code"], "ARG_INVALID")

    def test_a_non_numeric_sink_is_refused(self):
        r = self._run(thermal_vias=self._via(sink_c="cold plate"))
        self.assertEqual(r["reason_code"], "ARG_INVALID")


class TestToolSurface(_Base):
    def test_the_schema_carries_the_two_new_keys(self):
        from pcb_agent_mcp import tools
        props = tools.TOOLS["board_electrothermal"]["inputSchema"]["properties"]
        self.assertIn("emissivity", props)
        self.assertIn("thermal_vias", props)

    def test_the_handler_passes_both_through(self):
        from pcb_agent_mcp import tools
        args = self._common()
        bare = tools.TOOLS["board_electrothermal"]["handler"](dict(args))
        fed = tools.TOOLS["board_electrothermal"]["handler"](
            dict(args, emissivity=0.9,
                 thermal_vias=self._via(x1_mm=0.0, y1_mm=34.0,
                                        x2_mm=100.0, y2_mm=46.0)))
        self.assertEqual(fed["status"], "ok", fed)
        self.assertTrue(fed["radiation"]["modelled"])
        self.assertTrue(fed["thermal_vias"]["modelled"])
        self.assertLess(fed["t_max_c"], bare["t_max_c"])


if __name__ == "__main__":
    unittest.main()
