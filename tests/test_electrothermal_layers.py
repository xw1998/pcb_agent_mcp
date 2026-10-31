"""Through-thickness stacks: the layered electro-thermal solve (`layer_stack`).

The anchors are arithmetic, not this module's own output:

  * Two levels, a source spread UNIFORMLY over the top level, one convection
    face per surface level, and a laminate coupling G between them have an exact
    answer. With q the total dissipation, hA = h*area (ONE face, not two) and
    G = k_z*area/thickness:

        u_top = q*(hA + G) / (hA*(hA + 2G))
        u_bot = q*G / (hA*(hA + 2G))

    A uniform source on an adiabatic plate conducts nothing in-plane, so the
    in-plane grid drops out and the numbers are exact to the solver tolerance.
    `alpha_per_k = 0` makes the source temperature independent, so no nonlinear
    term is left to hide behind.
  * `k_z -> infinity` means "the levels are shorted", and then the stack must
    reproduce the SINGLE-PLATE answer of the same board. That is the regression
    anchor for the whole model: if the summed slab conductances or the surface
    areas were counted wrong, this limit would land somewhere else.
  * The capacity of the stack is rho*cp*area*thickness exactly, because the
    midpoint slabs add up to the board.

Negative controls, one per mechanism (a test that would FAIL if the mechanism
were wrong in the obvious way):

  * giving each surface level TWO faces (the single-plate hc) makes the plate
    read half as hot as the closed form - `test_one_face_per_surface_level`
    measures that distance instead of trusting the code;
  * ignoring k_z (or defaulting it to k for the wrong reason) breaks the
    monotone approach to the plate limit;
  * an internal level that convects would cool the middle of the stack - the
    3-level test pins that it does not.

Every refusal asserts the reason_code, because in each case the honest answer is
"no number".
"""
import math
import unittest

from pcb_agent_mcp.calc import electrothermal as et

K_LAM = 0.3
K_CU = 400.0
H = 10.0
W, HH = 100.0, 80.0
TH = 1.6
CELL = 2.0
AREA = (W * 1e-3) * (HH * 1e-3)
TA = 25.0
RHO = 8960.0
CP = 385.0


def _uniform_top_source(current_a=50.0):
    """One conductor that covers the whole board, sitting on the top face.

    A segment along x with width = the board height is a full rectangle; the
    total I^2R is exact however the cells are rasterised, which is what the
    closed form needs.
    """
    return {"x1_mm": 0.0, "y1_mm": HH / 2.0, "x2_mm": W, "y2_mm": HH / 2.0,
            "width_mm": HH, "thickness_um": 35, "current_a": current_a,
            "z_mm": TH}


def _two_level(kz=K_LAM, cu_um=0.0):
    return {"copper_layers": [{"z_mm": 0.0, "thickness_um": cu_um},
                              {"z_mm": TH, "thickness_um": cu_um}],
            "k_z_w_mk": kz}


def _axis_trace(z=TH, current=3.0):
    return {"x1_mm": 0.0, "y1_mm": HH / 2.0, "x2_mm": 90.0, "y2_mm": HH / 2.0,
            "width_mm": 2.0, "thickness_um": 35, "current_a": current,
            "z_mm": z}


def _base(conductors, **kw):
    d = dict(width_mm=W, height_mm=HH, thickness_mm=TH, k_w_mk=K_LAM,
             h_w_m2k=H, conductors=conductors, cell_mm=CELL)
    d.update(kw)
    return d


def _closed_form(q, hA, G):
    u_top = q * (hA + G) / (hA * (hA + 2.0 * G))
    u_bot = q * G / (hA * (hA + 2.0 * G))
    return TA + u_top, TA + u_bot


class TestTheTwoLevelClosedForm(unittest.TestCase):
    def test_uniform_source_matches_the_hand_arithmetic(self):
        r = et.board_electrothermal(layer_stack=_two_level(),
                                    alpha_per_k=0.0,
                                    **_base([_uniform_top_source()]))
        self.assertEqual(r["status"], "ok", r)
        self.assertEqual(r["conductors"][0]["cells"],
                         r["grid"][0] * r["grid"][1])
        hA = H * AREA                 # ONE face per surface level
        G = K_LAM * AREA / (TH * 1e-3)
        q = r["total_power_at_20c_w"]
        want_top, want_bot = _closed_form(q, hA, G)
        lv = r["layer_stack"]["levels"]
        self.assertAlmostEqual(lv[1]["t_max_c"], want_top, places=4)
        self.assertAlmostEqual(lv[0]["t_max_c"], want_bot, places=4)
        # and each level really is isothermal: a uniform source has nowhere to
        # conduct to in-plane
        self.assertLess(lv[1]["t_max_c"] - lv[1]["t_min_c"], 1e-3)

    def test_one_face_per_surface_level(self):
        # Negative control for `hc_face = h*ac`: if a surface level were given
        # the single-plate TWO-face conductance, both closed forms would come out
        # at half the rise. Measure that distance instead of trusting the code.
        r = et.board_electrothermal(layer_stack=_two_level(), alpha_per_k=0.0,
                                    **_base([_uniform_top_source()]))
        hA = H * AREA
        G = K_LAM * AREA / (TH * 1e-3)
        q = r["total_power_at_20c_w"]
        one, _ = _closed_form(q, hA, G)
        two, _ = _closed_form(q, 2.0 * hA, G)
        got = r["layer_stack"]["levels"][1]["t_max_c"]
        self.assertAlmostEqual(got, one, places=4)
        self.assertGreater(abs(got - two), 1.0)

    def test_the_split_between_the_levels_follows_the_coupling(self):
        # A weakly coupled stack keeps the heat near the source, a strongly
        # coupled one shares it. If the vertical path were ignored (or every
        # level were forced to one temperature) these two would be equal.
        def rises(kz):
            r = et.board_electrothermal(layer_stack=_two_level(kz=kz),
                                        alpha_per_k=0.0,
                                        **_base([_uniform_top_source()]))
            lv = r["layer_stack"]["levels"]
            return lv[1]["t_max_c"] - TA, lv[0]["t_max_c"] - TA
        def closed(kz):
            G = kz * AREA / (TH * 1e-3)
            hA = H * AREA
            # q is the same in every run: alpha_per_k=0 keeps R at the 20 C value
            q = et.board_electrothermal(layer_stack=_two_level(kz=kz),
                                        alpha_per_k=0.0,
                                        **_base([_uniform_top_source()])
                                        )["total_power_at_20c_w"]
            return _closed_form(q, hA, G)

        for kz in (0.05, 50.0):
            top, bot = rises(kz)
            want_top, want_bot = closed(kz)
            self.assertAlmostEqual(TA + top, want_top, places=3)
            self.assertAlmostEqual(TA + bot, want_bot, places=3)

        top_weak, bot_weak = rises(0.05)
        top_strong, bot_strong = rises(50.0)
        # better coupling cools the top (the heat has somewhere to go) and
        # warms the bottom (it now arrives there) - both directions matter, a
        # model that only had one level would show neither
        self.assertGreater(top_weak, top_strong)
        self.assertGreater(bot_strong, bot_weak)
        # and the weaker coupling keeps a bigger top/bottom split
        self.assertGreater(top_weak / bot_weak, top_strong / bot_strong)
        # the split is bounded by the closed-form 1 + hA/G ratio
        self.assertLess(top_weak / bot_weak, 1.0 + H * AREA / (0.05 * AREA / (TH * 1e-3)) + 1e-3)


class TestTheShortedLimitIsTheSinglePlate(unittest.TestCase):
    def test_kz_to_infinity_reproduces_the_plate(self):
        plate = et.board_electrothermal(**_base([_axis_trace()]))
        stack = et.board_electrothermal(layer_stack=_two_level(kz=1e6),
                                        tol_k=1e-6, **_base([_axis_trace()]))
        self.assertEqual(plate["status"], "ok", plate)
        self.assertEqual(stack["status"], "ok", stack)
        self.assertAlmostEqual(stack["t_max_c"], plate["t_max_c"], places=2)
        self.assertAlmostEqual(stack["t_avg_c"], plate["t_avg_c"], places=2)

    def test_better_coupling_cools_from_above_and_is_monotone(self):
        plate = et.board_electrothermal(**_base([_axis_trace()]))["t_max_c"]
        got = []
        for kz in (K_LAM, 5.0, 1e6):
            r = et.board_electrothermal(layer_stack=_two_level(kz=kz),
                                        tol_k=1e-6, **_base([_axis_trace()]))
            self.assertEqual(r["status"], "ok", r)
            got.append(r["t_max_c"])
        # a finite coupling can only add resistance, so every stack sits at or
        # above the shorted limit, and raising k_z walks down to it
        for v in got:
            self.assertGreaterEqual(v, plate - 0.01)
        self.assertGreater(got[0], got[1])
        self.assertGreater(got[1], got[2])
        self.assertAlmostEqual(got[2], plate, places=2)

    def test_a_stiff_stack_still_answers_and_says_why_the_gate_moved(self):
        # k_z = 1e12 is the "levels shorted" limit; at that stiffness the
        # solver's own tolerance, not the model, dominates the percentage, so the
        # energy gate must report the allowance it used instead of firing.
        r = et.board_electrothermal(layer_stack=_two_level(kz=1e12),
                                    tol_k=1e-6, **_base([_axis_trace()]))
        self.assertEqual(r["status"], "ok", r)
        eb = r["energy_balance"]
        self.assertIn("residual_allowance_w", eb)
        self.assertIn("noise floor", eb["residual_allowance"])
        plate = et.board_electrothermal(**_base([_axis_trace()]))["t_max_c"]
        self.assertAlmostEqual(r["t_max_c"], plate, places=1)


class TestNoStackNoNewKeys(unittest.TestCase):
    def test_the_plate_output_is_unchanged(self):
        r = et.board_electrothermal(**_base([_axis_trace()]))
        self.assertEqual(r["status"], "ok", r)
        self.assertNotIn("layer_stack", r)
        self.assertNotIn("residual_allowance_w", r["energy_balance"])

    def test_a_stack_reports_its_levels_and_gaps(self):
        r = et.board_electrothermal(layer_stack=_two_level(cu_um=35.0),
                                    **_base([_axis_trace()]))
        self.assertEqual(r["status"], "ok", r)
        st = r["layer_stack"]
        self.assertEqual(len(st["levels"]), 2)
        self.assertEqual(len(st["gaps"]), 1)
        self.assertEqual(st["levels"][0]["role"], "bottom face")
        self.assertEqual(st["levels"][1]["role"], "top face")
        self.assertTrue(st["levels"][0]["surface"])
        self.assertAlmostEqual(st["gaps"][0]["conductance_w_per_k"],
                               K_LAM * AREA / (TH * 1e-3), places=9)
        self.assertAlmostEqual(st["gaps"][0]["areal_rth_m2k_per_w"],
                               (TH * 1e-3) / K_LAM,
                               delta=(TH * 1e-3) / K_LAM * 1e-5)
        self.assertEqual(st["hot_level"], 1)
        self.assertIn("level 1", r["map_legend"])
        self.assertAlmostEqual(st["levels"][1]["copper_um_area_avg"], 35.0,
                               places=6)

    def test_zones_are_an_exact_area_fraction(self):
        half = {"copper_layers": [
            {"z_mm": 0.0, "thickness_um": 0.0},
            {"z_mm": TH, "thickness_um": 35.0,
             "zones": [{"x1_mm": 0.0, "y1_mm": 0.0,
                        "x2_mm": W / 2.0, "y2_mm": HH}]}],
            "k_z_w_mk": K_LAM}
        r = et.board_electrothermal(layer_stack=half, **_base([_axis_trace()]))
        self.assertEqual(r["status"], "ok", r)
        self.assertAlmostEqual(r["layer_stack"]["levels"][1]
                               ["copper_um_area_avg"], 35.0 * 0.5, places=6)
        self.assertAlmostEqual(r["layer_stack"]["levels"][0]
                               ["copper_um_area_avg"], 0.0, places=9)

    def test_copper_planes_spread_the_hot_spot(self):
        bare = et.board_electrothermal(layer_stack=_two_level(), **_base(
            [_axis_trace()]))["t_max_c"]
        planes = et.board_electrothermal(layer_stack=_two_level(cu_um=35.0),
                                         **_base([_axis_trace()]))["t_max_c"]
        # copper is a spreader, so the peak has to come DOWN, and the answer
        # must still be above the plate model that has no copper at all
        self.assertLess(planes, bare)
        plate = et.board_electrothermal(**_base([_axis_trace()]))["t_max_c"]
        self.assertLess(plate, bare)


class TestThreeLevelsAndTheInternalFace(unittest.TestCase):
    def _mid_source(self):
        c = _axis_trace(z=TH / 2.0)
        return c

    def test_a_source_inside_the_stack_is_the_hottest_and_conducts_both_ways(self):
        st = {"copper_layers": [{"z_mm": 0.0, "thickness_um": 0.0},
                                {"z_mm": TH / 2.0, "thickness_um": 35.0},
                                {"z_mm": TH, "thickness_um": 0.0}],
              "k_z_w_mk": K_LAM}
        r = et.board_electrothermal(layer_stack=st, **_base([self._mid_source()]))
        self.assertEqual(r["status"], "ok", r)
        lv = r["layer_stack"]["levels"]
        self.assertEqual(len(lv), 3)
        self.assertFalse(lv[1]["surface"])
        self.assertEqual(r["layer_stack"]["hot_level"], 1)
        self.assertGreater(lv[1]["t_max_c"], lv[0]["t_max_c"])
        self.assertGreater(lv[1]["t_max_c"], lv[2]["t_max_c"])
        # both faces carry heat out, and the internal level does not: the energy
        # balance only has convection from the two surfaces. The layered solver
        # reports its own noise floor, so judge against that allowance rather
        # than a fixed percentage (the residual is solver noise here, not power)
        eb = r["energy_balance"]
        residual_w = abs(eb["residual_pct"]) / 100.0 * eb["power_in_w"]
        self.assertLessEqual(residual_w, eb["residual_allowance_w"])
        self.assertGreater(eb["residual_allowance_w"], 0.0)
        self.assertGreater(eb["residual_allowance_w"],
                           0.005 * eb["power_in_w"])

    def test_an_internal_level_does_not_convect(self):
        # Negative control: if the internal level had h, the middle of the stack
        # would lose heat to the air and the peak would drop far below this. The
        # two surface levels are the only place heat leaves, so the mid level
        # must sit above BOTH surfaces - the closed form for a symmetric
        # three-level stack pins the ordering, not the exact value.
        sym = {"copper_layers": [{"z_mm": 0.0, "thickness_um": 0.0},
                                 {"z_mm": TH / 2.0, "thickness_um": 0.0},
                                 {"z_mm": TH, "thickness_um": 0.0}],
               "k_z_w_mk": 0.005}
        r = et.board_electrothermal(layer_stack=sym,
                                    alpha_per_k=0.0,
                                    **_base([self._mid_source()]))
        self.assertEqual(r["status"], "ok", r)
        lv = r["layer_stack"]["levels"]
        # with equal gaps the two surfaces are symmetric and the mid level is
        # hotter than both by the series half of the rise
        self.assertAlmostEqual(lv[0]["t_max_c"], lv[2]["t_max_c"], places=4)
        self.assertGreater(lv[1]["t_max_c"] - lv[0]["t_max_c"], 1.0)


class TestLayeredTransient(unittest.TestCase):
    def test_the_capacity_is_the_whole_board(self):
        tr = {"t_end_s": 20.0, "points": 4, "cp_j_kg_k": CP, "rho_kg_m3": RHO,
              "initial_c": TA}
        r = et.board_electrothermal(layer_stack=_two_level(cu_um=35.0),
                                    transient=tr, **_base([_axis_trace()]))
        self.assertEqual(r["status"], "ok", r)
        want = RHO * CP * AREA * (TH * 1e-3)
        # _sig rounds the reported number to 6 significant digits, so compare
        # the ratio to 1e-4 rather than 1e-9
        self.assertAlmostEqual(r["transient"]["heat_capacity_j_per_k"] / want,
                               1.0, places=4)
        hist = [p["t_max_c"] for p in r["transient"]["history"]]
        self.assertEqual(len(hist), 4)
        for a, b in zip(hist, hist[1:]):
            self.assertGreater(b, a)
        self.assertAlmostEqual(r["t_max_c"], hist[-1], places=6)


class TestThermalViasAcrossTheStack(unittest.TestCase):
    def _vias(self, **kw):
        d = {"x1_mm": 20.0, "y1_mm": 20.0, "x2_mm": 80.0, "y2_mm": 60.0,
             "pitch_mm": 5.0, "d_mm": 0.3, "plating_um": 25, "span_mm": TH}
        d.update(kw)
        return [d]

    def test_vias_short_the_thickness_and_are_reported_per_gap(self):
        no = et.board_electrothermal(layer_stack=_two_level(), **_base(
            [_axis_trace()]))
        yes = et.board_electrothermal(layer_stack=_two_level(),
                                      thermal_vias=self._vias(),
                                      **_base([_axis_trace()]))
        self.assertEqual(yes["status"], "ok", yes)
        self.assertLess(yes["layer_stack"]["rise_across_board_k"],
                        no["layer_stack"]["rise_across_board_k"])
        rep = yes["thermal_vias"]
        self.assertTrue(rep["modelled"])
        self.assertEqual(rep["vias_counted"],
                         rep["arrays"][0]["vias_counted"])
        gap = yes["layer_stack"]["gaps"][0]
        self.assertGreater(gap["via_conductance_w_per_k"], 0.0)
        self.assertAlmostEqual(gap["conductance_with_vias_w_per_k"],
                               gap["conductance_w_per_k"] +
                               gap["via_conductance_w_per_k"],
                               delta=abs(gap["conductance_with_vias_w_per_k"]) * 1e-5)
        # the barrel is a series of gaps: one gap here, so the array's
        # single-gap conductance IS the per-gap number
        # the report-level number is the WHOLE BOARD conductance over that gap,
        # a single array entry is per via - so compare to the board number the
        # stack itself reported
        self.assertAlmostEqual(rep["per_gap_conductance_w_per_k"][0],
                               gap["via_conductance_w_per_k"],
                               delta=abs(gap["via_conductance_w_per_k"]) * 1e-5)
        self.assertAlmostEqual(
            rep["per_gap_conductance_w_per_k"][0] /
            rep["arrays"][0]["per_gap_conductance_w_per_k"][0],
            float(rep["vias_counted"]),
            delta=float(rep["vias_counted"]) * 1e-5)

    def test_the_via_conductance_matches_hand_arithmetic(self):
        a_mm2 = math.pi * ((0.15 ** 2) - (0.125 ** 2))
        want_gap = K_CU * (a_mm2 * 1e-6) / (TH * 1e-3)
        r = et.board_electrothermal(layer_stack=_two_level(),
                                    thermal_vias=self._vias(),
                                    **_base([_axis_trace()]))
        arr = r["thermal_vias"]["arrays"][0]
        self.assertAlmostEqual(arr["per_gap_conductance_w_per_k"][0],
                               want_gap, delta=want_gap * 1e-5)
        self.assertAlmostEqual(r["thermal_vias"]
                               ["per_gap_conductance_w_per_k"][0],
                               want_gap * arr["vias_counted"],
                               delta=want_gap * arr["vias_counted"] * 1e-5)


class TestRefusals(unittest.TestCase):
    def _call(self, **kw):
        args = _base([_axis_trace()])
        args.update(kw)
        return et.board_electrothermal(**args)

    def test_pours_and_a_stack_would_double_count(self):
        r = self._call(layer_stack=_two_level(),
                       copper_pours=[{"x1_mm": 0, "y1_mm": 0, "x2_mm": W,
                                      "y2_mm": HH, "thickness_um": 35}])
        self.assertEqual(r["reason_code"], "ARG_INVALID")
        self.assertIn("zones", r["hint"])

    def test_a_conductor_must_say_which_level(self):
        c = _axis_trace()
        del c["z_mm"]
        r = self._call(conductors=[c], layer_stack=_two_level())
        self.assertEqual(r["reason_code"], "ARG_REQUIRED")
        self.assertIn("z_mm", r["message"])

    def test_a_conductor_off_the_levels_is_refused(self):
        c = _axis_trace(z=0.8)          # no level at 0.8 mm
        r = self._call(conductors=[c], layer_stack=_two_level())
        self.assertEqual(r["reason_code"], "ARG_RANGE")
        self.assertIn("not one of the levels", r["message"])

    def test_parallel_layers_are_refused(self):
        c = _axis_trace()
        c["layers"] = 2
        r = self._call(conductors=[c], layer_stack=_two_level())
        self.assertEqual(r["reason_code"], "ARG_RANGE")
        self.assertIn("stack already IS the layers", r["message"])

    def test_a_via_sink_would_count_the_air_twice(self):
        r = self._call(layer_stack=_two_level(),
                       thermal_vias=[{"x1_mm": 20, "y1_mm": 20, "x2_mm": 80,
                                      "y2_mm": 60, "pitch_mm": 5,
                                      "d_mm": 0.3, "plating_um": 25,
                                      "span_mm": TH, "sink_c": 0.0}])
        self.assertEqual(r["reason_code"], "ARG_INVALID")
        self.assertIn("sink_c", r["message"])

    def test_a_barrel_profile_has_no_gap_to_land_in(self):
        r = self._call(layer_stack=_two_level(),
                       thermal_vias=[{"x1_mm": 20, "y1_mm": 20, "x2_mm": 80,
                                      "y2_mm": 60, "pitch_mm": 5,
                                      "d_mm": 0.3, "plating_um": 25,
                                      "span_mm": TH,
                                      "barrel_profile": [
                                          {"length_mm": 0.8,
                                           "plating_um": 25},
                                          {"length_mm": 0.8,
                                           "plating_um": 15}]}])
        self.assertEqual(r["reason_code"], "ARG_INVALID")
        self.assertIn("barrel_profile", r["message"])

    def test_a_partial_span_is_refused(self):
        r = self._call(layer_stack=_two_level(),
                       thermal_vias=[{"x1_mm": 20, "y1_mm": 20, "x2_mm": 80,
                                      "y2_mm": 60, "pitch_mm": 5,
                                      "d_mm": 0.3, "plating_um": 25,
                                      "span_mm": 0.8}])
        self.assertEqual(r["reason_code"], "ARG_RANGE")
        self.assertIn("span the board", r["message"])

    def test_the_grid_limit_counts_every_level(self):
        r = self._call(layer_stack=_two_level(), cell_mm=1.0,
                       max_cells=20000)      # 100x80 x 2 levels = 16000 ok?
        self.assertEqual(r["status"], "ok", r)
        r = self._call(layer_stack=_two_level(), cell_mm=1.0, max_cells=10000)
        self.assertEqual(r["reason_code"], "TOO_LARGE")
        self.assertIn("levels", r["message"])

    def test_an_empty_or_misspelled_stack_is_refused(self):
        r = self._call(layer_stack={"copper_layers": []})
        self.assertEqual(r["reason_code"], "ARG_INVALID")
        r = self._call(layer_stack=[{"z_mm": 0.0, "thickness_um": 35}])
        self.assertEqual(r["reason_code"], "ARG_INVALID")

    def test_a_level_outside_the_board_is_refused(self):
        r = self._call(layer_stack={"copper_layers": [
            {"z_mm": 2.5, "thickness_um": 35}]})
        self.assertEqual(r["reason_code"], "ARG_RANGE")
        self.assertIn("outside the board", r["message"])

    def test_the_laminate_coupling_must_be_positive(self):
        r = self._call(layer_stack=_two_level(kz=0.0))
        self.assertEqual(r["reason_code"], "ARG_RANGE")
        self.assertIn("k_z_w_mk", r["message"])

    def test_a_negative_copper_thickness_is_refused(self):
        r = self._call(layer_stack={"copper_layers": [
            {"z_mm": 0.0, "thickness_um": -1.0}]})
        self.assertEqual(r["reason_code"], "ARG_RANGE")

    def test_a_zero_area_zone_is_refused(self):
        r = self._call(layer_stack={"copper_layers": [
            {"z_mm": 0.0, "thickness_um": 35,
             "zones": [{"x1_mm": 10, "y1_mm": 10, "x2_mm": 10, "y2_mm": 40}]}]})
        self.assertEqual(r["reason_code"], "ARG_RANGE")
        self.assertIn("zero area", r["message"])

    def test_a_zone_outside_the_plate_is_refused(self):
        r = self._call(layer_stack={"copper_layers": [
            {"z_mm": 0.0, "thickness_um": 35,
             "zones": [{"x1_mm": 0, "y1_mm": 0, "x2_mm": W + 5,
                        "y2_mm": HH}]}]})
        self.assertEqual(r["reason_code"], "ARG_RANGE")
        self.assertIn("outside the plate", r["message"])


if __name__ == "__main__":
    unittest.main()
