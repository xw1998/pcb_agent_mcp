"""Copper as the heat spreader in the board 2-D electro-thermal solve.

Anchors are arithmetic, not this module's own output:

  * A pour that covers the whole board uniformly IS the same problem as running
    with k_w_mk = k_lam + (t_cu/t_board)*layers*k_cu. That equivalent k is hand
    arithmetic (35 um / 1.6 mm * 400 = 8.75 W/(m*K)), and the two runs have to
    agree.
  * Per-cell coverage is an EXACT area overlap, so a pour edge half way through
    a cell is 0.5: k_eff max is k_lam + 0.5*8.75 exactly, and the board average
    is a hand sum over "covered cell / bare cell".
  * A pour of 0 um of copper is a no-op, bit for bit.

The negative control for "copper spreads heat" is a pour of the same area placed
where there is no heat: it must move the peak by a measured, tiny amount. Without
that pair, "the peak dropped" could just be "we added a number somewhere".

Every refusal below asserts the reason_code, because in each case the honest
answer is "no number".
"""
import math
import unittest

from pcb_agent_mcp.calc import electrothermal as et

K_LAM = 0.3
T_BOARD_MM = 1.6
H = 10.0
W, HH = 100.0, 80.0
CELL = 2.0            # -> 50 x 40 cells, each exactly 2 x 2 mm
NX, NY = 50, 40
K_CU = 400.0

def _dk(t_cu_um=35.0, layers=1, k_cu=K_CU, t_board_mm=T_BOARD_MM):
    """The k that a uniform copper layer adds to the sheet, hand arithmetic."""
    return (t_cu_um * 1e-6) * layers / (t_board_mm * 1e-3) * k_cu

class _Base(unittest.TestCase):
    def _common(self):
        return dict(width_mm=W, height_mm=HH, thickness_mm=T_BOARD_MM,
                    k_w_mk=K_LAM, h_w_m2k=H, t_ambient_c=25.0, cell_mm=CELL,
                    conductors=[{"x1_mm": 10, "y1_mm": 40, "x2_mm": 90,
                                 "y2_mm": 40, "width_mm": 3.0,
                                 "thickness_um": 35.0, "current_a": 4.0}])

    def _run(self, pours=None, **kw):
        args = self._common()
        args.update(kw)
        if pours is not None:
            args["copper_pours"] = pours
        return et.board_electrothermal(**args)

    def _full(self, t_um=35.0, layers=1, **kw):
        return self._run(pours=[{"x1_mm": 0, "y1_mm": 0, "x2_mm": W,
                                 "y2_mm": HH, "thickness_um": t_um,
                                 "layers": layers}], **kw)

class TestWithoutPoursNothingChanges(_Base):
    def test_the_medium_is_the_laminate_and_the_answer_says_so(self):
        r = self._run()
        self.assertEqual(r["status"], "ok", r)
        cs = r["copper_spreading"]
        self.assertFalse(cs["modelled"])
        self.assertIn("laminate", cs["why"])
        # the old honest sentence must still be reachable in precision
        self.assertIn("laminate k you passed", r["precision"])
        # and the not-computed list still carries the laminate-only limitation
        self.assertTrue(any("laminate" in s for s in r["not_computed"]))

    def test_a_zero_micron_pour_changes_nothing_at_all(self):
        base = self._run()
        zero = self._full(t_um=0.0)
        self.assertEqual(zero["status"], "ok", zero)
        for key in ("t_max_c", "t_min_c", "t_avg_c", "delta_t_max_k"):
            self.assertEqual(zero[key], base[key], key)

    def test_a_zero_micron_pour_is_still_reported_as_a_map(self):
        zero = self._full(t_um=0.0)
        cs = zero["copper_spreading"]
        self.assertTrue(cs["modelled"])
        for key in ("min", "max", "area_avg"):
            self.assertAlmostEqual(cs["k_eff_w_mk"][key], K_LAM, places=12)

    def test_the_laminate_only_sentence_leaves_the_not_computed_list(self):
        base = self._run()
        withcu = self._full()
        self.assertTrue(any("laminate k given by the caller" in s
                            for s in base["not_computed"]))
        self.assertFalse(any("laminate k given by the caller" in s
                             for s in withcu["not_computed"]))
        self.assertTrue(any("smeared" in s for s in withcu["not_computed"]))

class TestUniformPourEqualsTheEquivalentScalarK(_Base):
    def test_the_equivalent_k_is_the_hand_number(self):
        r = self._full()
        cs = r["copper_spreading"]["k_eff_w_mk"]
        want = K_LAM + _dk()
        self.assertAlmostEqual(want, 9.05, places=12)      # 0.3 + 8.75
        self.assertAlmostEqual(cs["min"], want, places=9)
        self.assertAlmostEqual(cs["max"], want, places=9)
        self.assertAlmostEqual(cs["area_avg"], want, places=9)

    def test_the_field_equals_the_run_with_that_k_passed_directly(self):
        withpours = self._full()
        direct = self._run(k_w_mk=K_LAM + _dk())
        self.assertEqual(direct["status"], "ok", direct)
        self.assertAlmostEqual(withpours["t_max_c"], direct["t_max_c"], places=6)
        self.assertAlmostEqual(withpours["t_avg_c"], direct["t_avg_c"], places=6)

    def test_half_a_cell_of_copper_is_exactly_half(self):
        # cells are exactly 2 mm wide, so x in [0, 1] is half of cell 0
        r = self._run(pours=[{"x1_mm": 0, "y1_mm": 0, "x2_mm": 1.0,
                              "y2_mm": HH, "thickness_um": 35.0}])
        cs = r["copper_spreading"]["k_eff_w_mk"]
        covered = K_LAM + 0.5 * _dk()
        self.assertAlmostEqual(cs["max"], covered, places=9)
        self.assertAlmostEqual(cs["min"], K_LAM, places=12)
        want_avg = (K_LAM * (NX * NY - NY) + covered * NY) / float(NX * NY)
        self.assertAlmostEqual(cs["area_avg"], want_avg, places=9)

    def test_two_pours_of_the_same_copper_add_their_thickness(self):
        one = self._full()["copper_spreading"]["k_eff_w_mk"]["area_avg"]
        # two separate pours over the same area == one pour of twice the copper
        r2 = self._run(pours=[
            {"x1_mm": 0, "y1_mm": 0, "x2_mm": W, "y2_mm": HH, "thickness_um": 35.0},
            {"x1_mm": 0, "y1_mm": 0, "x2_mm": W, "y2_mm": HH, "thickness_um": 35.0}])
        self.assertAlmostEqual(one, K_LAM + _dk(), places=9)
        self.assertAlmostEqual(r2["copper_spreading"]["k_eff_w_mk"]["area_avg"],
                               K_LAM + 2.0 * _dk(), places=9)
        self.assertNotAlmostEqual(r2["copper_spreading"]["k_eff_w_mk"]["area_avg"],
                                  one, places=6)

    def test_layers_scale_the_copper_like_thickness_does(self):
        two_layers = self._full(layers=2)["copper_spreading"]["k_eff_w_mk"]
        twice_thick = self._full(t_um=70.0)["copper_spreading"]["k_eff_w_mk"]
        self.assertAlmostEqual(two_layers["area_avg"], twice_thick["area_avg"],
                               places=9)

    def test_the_copper_k_is_a_stated_knob_not_a_constant(self):
        hotter = self._full(k_copper_w_mk=800.0)["copper_spreading"]
        self.assertAlmostEqual(hotter["k_copper_w_mk"], 800.0, places=9)
        self.assertAlmostEqual(hotter["k_eff_w_mk"]["area_avg"],
                               K_LAM + 2.0 * _dk(), places=9)

class TestWhereTheCopperIs(_Base):
    def _strip(self, y1, y2):
        return self._run(pours=[{"x1_mm": 0, "y1_mm": y1, "x2_mm": W,
                                 "y2_mm": y2, "thickness_um": 35.0}])

    def test_a_strip_under_the_trace_cools_the_peak_a_lot(self):
        bare = self._run()["t_max_c"]
        under = self._strip(35.0, 45.0)["t_max_c"]   # the trace is at y = 40
        self.assertGreater(bare - under, 5.0,
                           "35 um under a 4 A trace must do something visible")

    def test_the_same_strip_far_from_the_trace_barely_moves_the_peak(self):
        # negative control: identical area, identical copper, no heat to spread
        bare = self._run()["t_max_c"]
        away = self._strip(0.0, 10.0)["t_max_c"]
        self.assertLess(bare - away, 0.1)
        self.assertGreaterEqual(bare - away, 0.0)

    def test_the_two_strips_are_the_same_board_area(self):
        a = self._strip(35.0, 45.0)["copper_spreading"]
        b = self._strip(0.0, 10.0)["copper_spreading"]
        self.assertAlmostEqual(a["pours"][0]["area_fraction_of_board"],
                               b["pours"][0]["area_fraction_of_board"], places=9)
        # same copper, different place -> different answer
        self.assertNotAlmostEqual(
            self._strip(35.0, 45.0)["t_max_c"],
            self._strip(0.0, 10.0)["t_max_c"], places=3)

    def test_more_copper_is_colder(self):
        one = self._full(layers=1)["t_max_c"]
        two = self._full(layers=2)["t_max_c"]
        self.assertLess(two, one)

    def test_energy_still_balances_with_copper_present(self):
        for r in (self._full(), self._strip(35.0, 45.0)):
            res = abs(r["energy_balance"]["residual_pct"])
            self.assertLess(res, 0.5, r["energy_balance"])

    def test_the_mixing_rule_is_stated_where_it_can_be_read(self):
        cs = self._full()["copper_spreading"]
        self.assertIn("harmonic", cs["faces"])
        self.assertIn("in-plane", cs["assumption"])
        self.assertIn("vertical", cs["assumption"])
        self.assertGreater(cs["k_eff_over_laminate_max"], 1.0)

class TestRefusals(_Base):
    def test_a_pour_outside_the_plate_is_refused(self):
        r = self._run(pours=[{"x1_mm": 0, "y1_mm": 0, "x2_mm": W + 10,
                              "y2_mm": HH, "thickness_um": 35.0}])
        self.assertEqual(r["status"], "error")
        self.assertEqual(r["reason_code"], "ARG_RANGE")

    def test_negative_copper_thickness_is_refused(self):
        r = self._run(pours=[{"x1_mm": 0, "y1_mm": 0, "x2_mm": 10,
                              "y2_mm": 10, "thickness_um": -1.0}])
        self.assertEqual(r["reason_code"], "ARG_RANGE")

    def test_a_pour_without_geometry_is_refused_with_the_key(self):
        r = self._run(pours=[{"x1_mm": 0, "y1_mm": 0, "x2_mm": 10}])
        self.assertEqual(r["reason_code"], "ARG_INVALID")
        self.assertIn("thickness_um", r["message"])

    def test_a_zero_area_pour_is_refused(self):
        r = self._run(pours=[{"x1_mm": 5, "y1_mm": 5, "x2_mm": 5,
                              "y2_mm": 10, "thickness_um": 35.0}])
        self.assertEqual(r["reason_code"], "ARG_RANGE")
        self.assertIn("zero area", r["message"])

    def test_zero_copper_conductivity_is_refused(self):
        r = self._full(k_copper_w_mk=0.0)
        self.assertEqual(r["reason_code"], "ARG_RANGE")

    def test_pours_that_are_not_a_list_are_refused(self):
        r = self._run(pours="a plane")
        self.assertEqual(r["reason_code"], "ARG_INVALID")

    def test_a_pour_that_is_not_an_object_is_refused(self):
        r = self._run(pours=[[0, 0, 10, 10]])
        self.assertEqual(r["reason_code"], "ARG_INVALID")

    def test_zero_layers_is_refused(self):
        r = self._run(pours=[{"x1_mm": 0, "y1_mm": 0, "x2_mm": 10,
                              "y2_mm": 10, "thickness_um": 35.0, "layers": 0}])
        self.assertEqual(r["reason_code"], "ARG_RANGE")

class TestToolSurface(_Base):
    def test_the_schema_carries_the_two_new_keys(self):
        from pcb_agent_mcp import tools
        props = tools.TOOLS["board_electrothermal"]["inputSchema"]["properties"]
        self.assertIn("copper_pours", props)
        self.assertIn("k_copper_w_mk", props)

    def test_the_handler_passes_pours_through(self):
        from pcb_agent_mcp import tools
        args = self._common()
        bare = tools.TOOLS["board_electrothermal"]["handler"](dict(args))
        args["copper_pours"] = [{"x1_mm": 0, "y1_mm": 0, "x2_mm": W,
                                 "y2_mm": HH, "thickness_um": 35.0}]
        fed = tools.TOOLS["board_electrothermal"]["handler"](args)
        self.assertEqual(fed["status"], "ok", fed)
        self.assertTrue(fed["copper_spreading"]["modelled"])
        self.assertLess(fed["t_max_c"], bare["t_max_c"])

if __name__ == "__main__":
    unittest.main()
