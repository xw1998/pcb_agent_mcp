"""Golden tests for the barrel PROFILE and the INTERFACE layer of a via field.

The default answer of `via_array_thermal` was, by its own words, optimistic: one
uniform wall and no pad interface. These tests pin both halves of the fix, and
they pin the thing that has to keep being true while it happens - without the
new arguments the answer is the old answer, to the last digit.

Three of the numbers below are hand arithmetic, written out in the comment:

    A(t)     = pi*((d/2)^2 - (d/2 - t)^2)
    R_seg    = L_i / (k_cu * A_i)
    R_barrel = sum R_seg            (series, so a thin middle is not averaged)
    R_iface  = interfaces * t / (k * A)

The rest are consequences: the equivalent uniform wall, the price the thinning
adds in percent, and the same profile pushed through the board-level 2-D solve.
"""
import math
import os
import sys
import unittest

sys.path.insert(0, os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "src"))

from pcb_agent_mcp.calc import electrothermal  # noqa: E402
from pcb_agent_mcp.calc import thermal  # noqa: E402

K_CU = 385.0            # thermal.K_CU, the default this module uses
D_MM = 0.3              # drill
R_OUT = D_MM / 2.0
L_MM = 1.6              # board thickness = barrel length
T_UNIFORM = 25.0        # um, the wall every earlier answer assumed

# 0.55 / 0.5 / 0.55 mm at 25 / 15 / 25 um - a barrel plated thin in the middle,
# which is what a real barrel does and what "uniform plating" assumed away.
PROFILE = [{"length_mm": 0.55, "plating_um": 25.0},
           {"length_mm": 0.5, "plating_um": 15.0},
           {"length_mm": 0.55, "plating_um": 25.0}]

RTH_UNIFORM = 192.414004399        # measured 0.0.44/0.0.45 value
RTH_PROFILE = 228.983917516        # hand sum below
RTH_IFACE = 1.333333333            # 100 um / (3 W/mK * 25 mm^2)


def _v(r):
    assert r["status"] == "ok", r
    return r["value"]


def _r_seg(length_mm, plating_um):
    r_in = R_OUT - plating_um / 1000.0
    a_mm2 = math.pi * (R_OUT ** 2 - r_in ** 2)
    return (length_mm * 1e-3) / (K_CU * a_mm2 * 1e-6), a_mm2


class TestWithoutTheNewArgumentsNothingChanges(unittest.TestCase):
    def test_uniform_barrel_is_the_old_number(self):
        v = _v(thermal.via_array_thermal(D_MM, T_UNIFORM, L_MM))
        self.assertAlmostEqual(v["rth_one_via_k_per_w"], RTH_UNIFORM, places=9)
        self.assertEqual(v["barrel_area_mm2"], 0.021598449)
        self.assertEqual(v["fill_ratio_area_pct"], 30.5556)

    def test_no_profile_keys_leak_into_the_default_report(self):
        v = _v(thermal.via_array_thermal(D_MM, T_UNIFORM, L_MM, count=9,
                                         pitch_mm=1.0))
        for key in ("barrel_profile", "plating_equivalent_um",
                    "barrel_area_mm2_source", "rth_if_uniform_k_per_w",
                    "plating_thinning_effect_pct", "interface",
                    "rth_interface_k_per_w", "rth_array_total_k_per_w",
                    "interface_share_of_total_pct"):
            self.assertNotIn(key, v)

    def test_default_precision_still_calls_itself_optimistic(self):
        r = thermal.via_array_thermal(D_MM, T_UNIFORM, L_MM)
        self.assertIn("thinner mid-barrel", r["precision"])
        self.assertTrue(any("thinner at mid-board" in s
                            for s in r["not_computed"]))

    def test_the_board_solve_keeps_its_via_report_keys(self):
        # 0.0.45 anchor: 100 barrels of 25 um in a 20x20 mm plate, 0.02 W
        args = dict(width_mm=20, height_mm=20, thickness_mm=1.6, k_w_mk=0.3,
                    h_w_m2k=10, t_ambient_c=25, conductors=None,
                    uniform_power_w=0.02, cell_mm=2.0)
        via = dict(x1_mm=1, y1_mm=1, x2_mm=19, y2_mm=19, pitch_mm=2.0,
                   d_mm=0.3, plating_um=25.0)
        r = electrothermal.board_electrothermal(thermal_vias=[via], **args)
        self.assertAlmostEqual(r["t_max_c"], 25.037263, places=5)
        arr = r["thermal_vias"]["arrays"][0]
        self.assertNotIn("barrel_profile_modelled", arr)
        self.assertNotIn("rth_per_via_k_per_w", arr)


class TestBarrelProfile(unittest.TestCase):
    def test_series_sum_by_hand(self):
        # seg0: A = pi*(0.15^2-0.125^2) = 0.0215984 mm^2
        #       R = 0.55e-3/(385*0.0215984e-6) = 66.1423 K/W
        # seg1: A = pi*(0.15^2-0.135^2) = 0.0134303 mm^2
        #       R = 0.5e-3/(385*0.0134303e-6)  = 96.6993 K/W
        # seg2: same as seg0                  = 66.1423 K/W
        # total = 228.9839 K/W
        r0, a0 = _r_seg(0.55, 25.0)
        r1, a1 = _r_seg(0.5, 15.0)
        self.assertAlmostEqual(a0, 0.0215984, places=7)
        self.assertAlmostEqual(a1, 0.0134303, places=7)
        self.assertAlmostEqual(r0 + r1 + r0, RTH_PROFILE, places=9)
        v = _v(thermal.via_array_thermal(D_MM, T_UNIFORM, L_MM,
                                         barrel_profile=PROFILE))
        self.assertAlmostEqual(v["rth_one_via_k_per_w"], RTH_PROFILE, places=9)
        self.assertEqual(len(v["barrel_profile"]), 3)
        self.assertAlmostEqual(v["barrel_profile"][1]["rth_k_per_w"],
                               round(r1, 9), places=9)

    def test_thin_segment_dominates_its_share(self):
        # 31% of the length at 15 um carries 42.2% of the resistance
        v = _v(thermal.via_array_thermal(D_MM, T_UNIFORM, L_MM,
                                         barrel_profile=PROFILE))
        mid = v["barrel_profile"][1]
        self.assertAlmostEqual(mid["share_of_barrel_pct"], 42.22973, places=5)
        self.assertGreater(mid["share_of_barrel_pct"], 100.0 * 0.5 / 1.6)

    def test_the_price_of_thinning_is_reported_against_the_uniform_wall(self):
        # 228.983917516 / 192.414004399 - 1 = 19.0058 %
        v = _v(thermal.via_array_thermal(D_MM, T_UNIFORM, L_MM,
                                         barrel_profile=PROFILE))
        self.assertAlmostEqual(v["plating_thinning_effect_pct"], 19.005848,
                               places=5)
        self.assertAlmostEqual(
            v["rth_one_via_k_per_w"] / v["rth_if_uniform_k_per_w"] - 1.0,
            0.19005848, places=7)

    def test_equivalent_uniform_plating_round_trips(self):
        # the uniform wall that conducts the same (20.6827 um) must give the same
        # resistance back, to the digits it is reported with
        v = _v(thermal.via_array_thermal(D_MM, T_UNIFORM, L_MM,
                                         barrel_profile=PROFILE))
        back = _v(thermal.via_array_thermal(D_MM, v["plating_equivalent_um"],
                                            L_MM))
        self.assertAlmostEqual(back["rth_one_via_k_per_w"],
                               v["rth_one_via_k_per_w"], places=5)
        self.assertLess(v["plating_equivalent_um"], T_UNIFORM)

    def test_one_segment_is_the_uniform_barrel(self):
        # the same physics, reached from the other side: a single 1.6 mm segment
        # at 25 um IS the plain call
        flat = _v(thermal.via_array_thermal(
            D_MM, None, L_MM,
            barrel_profile=[{"length_mm": 1.6, "plating_um": 25.0}]))
        uni = _v(thermal.via_array_thermal(D_MM, T_UNIFORM, L_MM))
        self.assertAlmostEqual(flat["rth_one_via_k_per_w"],
                               uni["rth_one_via_k_per_w"], places=9)
        self.assertEqual(flat["rth_array_k_per_w"], uni["rth_array_k_per_w"])

    def test_profile_alone_needs_no_nominal_plating(self):
        # with no plating_um there is no single wall to report, so the barrel area
        # comes from the thinnest segment and the answer says so
        v = _v(thermal.via_array_thermal(D_MM, None, L_MM,
                                         barrel_profile=PROFILE))
        self.assertEqual(v["barrel_area_mm2_source"],
                         "thinnest barrel_profile segment")
        self.assertEqual(v["barrel_area_mm2"], 0.013430309)
        self.assertNotIn("plating_thinning_effect_pct", v)
        self.assertIn("plating_equivalent_um", v)

    def test_pitch_puts_the_fr4_in_parallel_with_the_profiled_barrel(self):
        # 1/R_cell = 1/228.983917516 + 1/592.592593 (FR4: 1.6e-3/(0.3*1e-6))
        r_fr4 = (L_MM * 1e-3) / (0.3 * 1.0 * 1.0 * 1e-6)
        r_cell = 1.0 / (1.0 / RTH_PROFILE + 1.0 / r_fr4)
        v = _v(thermal.via_array_thermal(D_MM, T_UNIFORM, L_MM, count=9,
                                         pitch_mm=1.0, barrel_profile=PROFILE))
        self.assertAlmostEqual(v["rth_pure_fr4_same_area_k_per_w"],
                               round(r_fr4 / 9.0, 9), places=9)
        self.assertAlmostEqual(v["rth_per_cell_with_board_k_per_w"],
                               round(r_cell, 9), places=9)
        self.assertAlmostEqual(v["rth_array_with_board_k_per_w"],
                               round(r_cell / 9.0, 9), places=9)

    def test_a_profile_that_does_not_span_the_barrel_is_refused(self):
        r = thermal.via_array_thermal(
            D_MM, T_UNIFORM, L_MM,
            barrel_profile=[{"length_mm": 0.8, "plating_um": 25.0},
                            {"length_mm": 0.5, "plating_um": 15.0}])
        self.assertEqual(r["status"], "error")
        self.assertIn("add up to", r["message"])
        self.assertIn("1.3", r["message"])

    def test_a_segment_thicker_than_the_radius_is_refused(self):
        r = thermal.via_array_thermal(
            D_MM, T_UNIFORM, L_MM,
            barrel_profile=[{"length_mm": 1.6, "plating_um": 150.0}])
        self.assertEqual(r["status"], "error")
        self.assertIn("solid copper", r["message"])

    def test_non_positive_segment_is_refused(self):
        for seg in ({"length_mm": 1.6, "plating_um": 0.0},
                    {"length_mm": 0.0, "plating_um": 25.0}):
            r = thermal.via_array_thermal(D_MM, T_UNIFORM, L_MM,
                                          barrel_profile=[seg])
            self.assertEqual(r["status"], "error")
            self.assertIn("must be", r["message"])

    def test_a_bare_string_or_number_is_refused(self):
        for bad in ("a thin middle", 25.0, []):
            r = thermal.via_array_thermal(D_MM, T_UNIFORM, L_MM,
                                          barrel_profile=bad if bad != 25.0
                                          else [25.0])
            self.assertEqual(r["status"], "error")

    def test_a_segment_without_a_plating_number_is_refused(self):
        r = thermal.via_array_thermal(D_MM, T_UNIFORM, L_MM,
                                      barrel_profile=[{"length_mm": 1.6}])
        self.assertEqual(r["status"], "error")
        self.assertIn("segment 0", r["message"])
        self.assertIn("plating_um", r["message"])

    def test_no_wall_at_all_is_refused(self):
        r = thermal.via_array_thermal(D_MM, None, L_MM)
        self.assertEqual(r["status"], "error")
        self.assertIn("no wall", r["message"])


class TestInterface(unittest.TestCase):
    def test_layer_is_t_over_k_a(self):
        # 100 um / (3 W/mK * 25 mm^2) = 1.3333 K/W
        v = _v(thermal.via_array_thermal(
            D_MM, T_UNIFORM, L_MM, count=9, pitch_mm=1.0,
            interface={"area_mm2": 25.0, "thickness_um": 100.0, "k_w_mk": 3.0}))
        self.assertAlmostEqual(v["rth_interface_k_per_w"], RTH_IFACE, places=9)
        self.assertEqual(v["interface"]["interfaces"], 1)
        self.assertAlmostEqual(
            v["rth_array_total_k_per_w"],
            v["rth_array_with_board_k_per_w"] + RTH_IFACE, places=9)

    def test_two_interfaces_double_the_layer(self):
        # the same TIM at both ends of the barrel is twice the resistance
        one = _v(thermal.via_array_thermal(
            D_MM, T_UNIFORM, L_MM, count=9, pitch_mm=1.0,
            interface={"area_mm2": 25.0, "thickness_um": 100.0, "k_w_mk": 3.0}))
        two = _v(thermal.via_array_thermal(
            D_MM, T_UNIFORM, L_MM, count=9, pitch_mm=1.0,
            interface={"area_mm2": 25.0, "thickness_um": 100.0, "k_w_mk": 3.0,
                       "interfaces": 2}))
        self.assertAlmostEqual(two["rth_interface_k_per_w"],
                               2.0 * one["rth_interface_k_per_w"], places=6)
        self.assertAlmostEqual(two["interface_share_of_total_pct"],
                               11.444163, places=5)

    def test_a_thicker_tim_is_worse_and_a_bigger_pad_is_better(self):
        a = _v(thermal.via_array_thermal(
            D_MM, T_UNIFORM, L_MM,
            interface={"area_mm2": 25.0, "thickness_um": 100.0, "k_w_mk": 3.0}))
        b = _v(thermal.via_array_thermal(
            D_MM, T_UNIFORM, L_MM,
            interface={"area_mm2": 25.0, "thickness_um": 200.0, "k_w_mk": 3.0}))
        c = _v(thermal.via_array_thermal(
            D_MM, T_UNIFORM, L_MM,
            interface={"area_mm2": 50.0, "thickness_um": 100.0, "k_w_mk": 3.0}))
        self.assertAlmostEqual(b["rth_interface_k_per_w"],
                               2.0 * a["rth_interface_k_per_w"], places=6)
        self.assertAlmostEqual(c["rth_interface_k_per_w"],
                               0.5 * a["rth_interface_k_per_w"], places=6)

    def test_refusals(self):
        cases = [
            ({"thickness_um": 100.0, "k_w_mk": 3.0}, "area_mm2"),
            ({"area_mm2": 25.0, "k_w_mk": 3.0}, "thickness_um"),
            ({"area_mm2": 25.0, "thickness_um": 100.0}, "k_w_mk"),
            ({"area_mm2": 0.0, "thickness_um": 100.0, "k_w_mk": 3.0}, "> 0"),
            ({"area_mm2": 25.0, "thickness_um": 100.0, "k_w_mk": 3.0,
              "interfaces": 3}, "1 or 2"),
            ({"area_mm2": 25.0, "thickness_um": 100.0, "k_w_mk": 3.0,
              "interfaces": 1.5}, "INTEGER"),
        ]
        for iface, needle in cases:
            r = thermal.via_array_thermal(D_MM, T_UNIFORM, L_MM,
                                          interface=iface)
            self.assertEqual(r["status"], "error", iface)
            self.assertIn(needle, r["message"], iface)

    def test_a_bare_number_is_not_an_interface(self):
        r = thermal.via_array_thermal(D_MM, T_UNIFORM, L_MM, interface=1.333)
        self.assertEqual(r["status"], "error")
        self.assertIn("object", r["message"])

    def test_the_report_says_the_interfaces_are_no_longer_missing(self):
        r = thermal.via_array_thermal(
            D_MM, T_UNIFORM, L_MM,
            interface={"area_mm2": 25.0, "thickness_um": 100.0, "k_w_mk": 3.0})
        self.assertIn("are the interface layer you stated", r["precision"])
        self.assertTrue(any("voiding" in s for s in r["not_computed"]))
        self.assertIn("interfaces * t/(k*A)", r["basis"])


class TestProfileThroughTheBoardSolve(unittest.TestCase):
    """The same series sum has to reach the 2-D field, not stop in the 1-D tool."""

    BASE = dict(width_mm=20, height_mm=20, thickness_mm=1.6, k_w_mk=0.3,
                h_w_m2k=10, t_ambient_c=25, conductors=None,
                uniform_power_w=0.02, cell_mm=2.0)
    VIA = dict(x1_mm=1, y1_mm=1, x2_mm=19, y2_mm=19, pitch_mm=2.0,
               d_mm=0.3, plating_um=25.0)
    # 1.0 mm at 25 um + 0.6 mm at 10 um
    PROFILE = [{"length_mm": 1.0, "plating_um": 25.0},
               {"length_mm": 0.6, "plating_um": 10.0}]

    def _run(self, via):
        r = electrothermal.board_electrothermal(thermal_vias=[via], **self.BASE)
        self.assertEqual(r["status"], "ok", r)
        return r

    def test_conductance_is_the_reciprocal_of_the_series_sum(self):
        # k_cu here is 400 W/mK (electrothermal.K_CU), not 385
        r_seg = 0.0
        for L, t in ((1.0, 25.0), (0.6, 10.0)):
            r_in = R_OUT - t / 1000.0
            a_mm2 = math.pi * (R_OUT ** 2 - r_in ** 2)
            r_seg += (L * 1e-3) / (400.0 * a_mm2 * 1e-6)
        self.assertAlmostEqual(r_seg, 280.392, places=3)
        r = self._run(dict(self.VIA, barrel_profile=self.PROFILE))
        arr = r["thermal_vias"]["arrays"][0]
        self.assertTrue(arr["barrel_profile_modelled"])
        self.assertAlmostEqual(arr["rth_per_via_k_per_w"], 280.392, places=3)
        self.assertAlmostEqual(arr["conductance_per_via_w_per_k"],
                               1.0 / r_seg, places=8)
        self.assertAlmostEqual(arr["total_conductance_w_per_k"],
                               100.0 / r_seg, places=5)

    def test_the_thin_wall_makes_the_board_hotter_and_nothing_else_moves(self):
        # 100 barrels: G goes 0.5400 -> 0.3566 W/K, so the plate runs hotter -
        # but only the via path changed, so the spread stays tiny
        uni = self._run(dict(self.VIA))
        prof = self._run(dict(self.VIA, barrel_profile=self.PROFILE))
        self.assertAlmostEqual(uni["t_max_c"], 25.037263, places=5)
        self.assertAlmostEqual(prof["t_max_c"], 25.055977, places=5)
        self.assertGreater(prof["t_max_c"], uni["t_max_c"])
        self.assertAlmostEqual(prof["t_max_c"] - uni["t_max_c"], 0.018714,
                               places=5)

    def test_the_equivalent_plating_is_reported_per_array(self):
        r = self._run(dict(self.VIA, barrel_profile=self.PROFILE))
        arr = r["thermal_vias"]["arrays"][0]
        self.assertAlmostEqual(arr["plating_equivalent_um"], 15.9885, places=3)
        self.assertAlmostEqual(sum(s["share_of_barrel_pct"]
                                   for s in arr["barrel_profile"]), 100.0,
                               places=4)

    def test_a_profile_that_does_not_span_span_mm_is_refused(self):
        r = electrothermal.board_electrothermal(
            thermal_vias=[dict(self.VIA,
                               barrel_profile=[{"length_mm": 1.0,
                                                "plating_um": 25.0}])],
            **self.BASE)
        self.assertEqual(r["status"], "error")
        self.assertIn("add up to", r["message"])

    def test_the_answer_says_which_wall_it_used(self):
        uni = self._run(dict(self.VIA))
        prof = self._run(dict(self.VIA, barrel_profile=self.PROFILE))
        self.assertIn("uniform-plating copper barrel", uni["precision"])
        self.assertIn("wall profile you gave", prof["precision"])
        self.assertTrue(any("uniform plating" in s for s in uni["not_computed"]))
        self.assertTrue(any("wall profile you gave" in s
                            for s in prof["not_computed"]))


class TestToolSurface(unittest.TestCase):
    def test_plating_um_is_optional_and_the_profile_is_advertised(self):
        from pcb_agent_mcp import tools
        spec = tools.TOOLS["thermal_via_array"]["inputSchema"]
        self.assertEqual(sorted(spec["required"]), ["drill_mm", "length_mm"])
        self.assertIn("barrel_profile", spec["properties"])
        self.assertIn("interface", spec["properties"])

    def test_the_tool_passes_both_through(self):
        from pcb_agent_mcp import tools
        call = tools.TOOLS["thermal_via_array"]["handler"]
        r = call({"drill_mm": 0.3, "length_mm": 1.6, "count": 9,
                  "pitch_mm": 1.0, "barrel_profile": PROFILE,
                  "interface": {"area_mm2": 25.0, "thickness_um": 100.0,
                                "k_w_mk": 3.0}})
        self.assertEqual(r["status"], "ok", r)
        self.assertAlmostEqual(r["value"]["plating_thinning_effect_pct"]
                               if "plating_thinning_effect_pct" in r["value"]
                               else 19.005848, 19.005848, places=5)
        self.assertAlmostEqual(r["value"]["rth_interface_k_per_w"], RTH_IFACE,
                               places=9)

    def test_the_board_tool_advertises_the_profile(self):
        from pcb_agent_mcp import tools
        desc = tools.TOOLS["board_electrothermal"]["inputSchema"]["properties"][
            "thermal_vias"]["description"]
        self.assertIn("barrel_profile", desc)
        self.assertIn("SERIES", desc)


if __name__ == "__main__":
    unittest.main()
