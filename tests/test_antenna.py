"""Golden tests for the antenna Calc module.

The point of these tests is not "the formula runs" - it is that the module refuses
to answer outside the validity window of each formula. A helix outside its
axial-mode window is a different antenna, not a slightly wrong one, and a loop above
0.1 lambda circumference is not a small loop at all. Those refusals are asserted
here as hard requirements.
"""
import os
import sys
import unittest

sys.path.insert(0, os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "src"))

from pcb_agent_mcp.calc import antenna  # noqa: E402


class TestPatch(unittest.TestCase):
    def test_fr4_2450_mhz_lands_on_the_usual_37_by_29_mm(self):
        r = antenna.patch_rectangular(2.45, 4.4, 1.6, tan_delta=0.02)
        self.assertEqual(r["status"], "ok")
        v = r["value"]
        # every 2.45 GHz FR4 patch in the drawer is about 37 x 29 mm
        self.assertAlmostEqual(v["width_mm"], 37.234, places=2)
        self.assertAlmostEqual(v["length_mm"], 28.808, places=2)
        self.assertAlmostEqual(v["fringing_dL_mm"], 0.7386, places=3)
        # eps_eff of the patch is the microstrip value at width W, between 1 and er
        self.assertGreater(v["eps_eff"], (4.4 + 1) / 2)
        self.assertLess(v["eps_eff"], 4.4)
        # the length must be shorter than half a guided wavelength by exactly 2*dL
        self.assertAlmostEqual(v["lambda_g_mm"] / 2.0 - 2.0 * v["fringing_dL_mm"],
                               v["length_mm"], places=3)

    def test_higher_permittivity_shrinks_the_patch(self):
        small = antenna.patch_rectangular(2.45, 10.2, 1.27)["value"]
        big = antenna.patch_rectangular(2.45, 2.2, 1.6)["value"]
        self.assertLess(small["width_mm"], big["width_mm"])
        self.assertLess(small["length_mm"], big["length_mm"])

    def test_edge_resistance_and_inset_are_consistent(self):
        v = antenna.patch_rectangular(2.45, 4.4, 1.6)["value"]
        self.assertGreater(v["rin_at_radiating_edge_ohm"], 50.0)
        # moving the feed in lowers the resistance, so the inset has to exist and
        # has to be shorter than the patch
        self.assertGreater(v["inset_depth_mm"], 0.0)
        self.assertLess(v["inset_depth_mm"], v["length_mm"] / 2.0)

    def test_ground_margin_is_six_substrate_heights(self):
        v = antenna.patch_rectangular(2.45, 4.4, 1.6)["value"]
        self.assertAlmostEqual(v["ground_margin_mm"], 9.6, places=3)

    def test_bandwidth_is_narrower_at_vswr_1_5_than_2(self):
        bw = antenna.patch_rectangular(2.45, 4.4, 1.6)["value"]["bandwidth"]
        self.assertLess(bw["vswr_lt_1.5"]["fractional_pct"],
                        bw["vswr_lt_2.0"]["fractional_pct"])
        self.assertGreater(bw["vswr_lt_2.0"]["fractional_pct"], 1.0)

    def test_thicker_substrate_widens_the_band(self):
        thin = antenna.patch_rectangular(2.45, 4.4, 0.8)["value"]["bandwidth"]
        thick = antenna.patch_rectangular(2.45, 4.4, 3.2)["value"]["bandwidth"]
        self.assertGreater(thick["vswr_lt_2.0"]["fractional_pct"],
                           thin["vswr_lt_2.0"]["fractional_pct"])


class TestPrintedWire(unittest.TestCase):
    def test_length_is_a_range_and_the_air_end_is_suggested(self):
        v = antenna.printed_wire(2.45, 4.4, 1.6)["value"]
        lo = v["total_length_mm"]["min_half_dielectric"]
        hi = v["total_length_mm"]["max_all_air"]
        self.assertLess(lo, hi)
        # quarter wave in free space
        self.assertAlmostEqual(hi, 122.364 / 4.0, places=2)
        # quarter wave with eps = (er+1)/2
        self.assertAlmostEqual(lo, 122.364 / 4.0 / (((4.4 + 1) / 2) ** 0.5), places=2)
        self.assertEqual(v["suggested_start_mm"], hi)

    def test_meander_needs_a_bounding_box(self):
        r = antenna.printed_wire(2.45, 4.4, 1.6, shape="meander")
        self.assertEqual(r["status"], "error")
        self.assertEqual(r["reason_code"], "ARG_REQUIRED")

    def test_meander_reports_whether_it_fits(self):
        v = antenna.printed_wire(2.45, 4.4, 1.6, shape="meander",
                                 bbox_mm=[20.0, 10.0])["value"]["meander"]
        self.assertIn("fits", v)
        self.assertIsInstance(v["fits"], bool)
        # a 3 x 2 mm box cannot hold a 30 mm quarter wave even meandered
        tiny = antenna.printed_wire(2.45, 4.4, 1.6, shape="meander",
                                    bbox_mm=[3.0, 2.0])["value"]["meander"]
        self.assertFalse(tiny["fits"])
        self.assertFalse(tiny["fits_without_meandering"])
        # a 20 x 10 box cannot hold it as a straight wire, but can meandered
        self.assertFalse(v["fits_without_meandering"])

    def test_ifa_tap_is_nearer_the_short_end_than_the_model_says(self):
        v = antenna.printed_wire(2.45, 4.4, 1.6, shape="ifa")["value"]
        # the tap estimate exists, is inside the element, and carries the warning
        # that a real printed part matches at a shorter tap
        self.assertGreater(v["tap_for_target_mm"], 0.0)
        self.assertLess(v["tap_for_target_mm"], v["suggested_start_mm"])
        self.assertIn("SHORTER", v["tap_note"])
        table = v["tap_table"]
        self.assertEqual(table, sorted(table, key=lambda e: e["offset_mm"]))
        self.assertEqual(table, sorted(table, key=lambda e: e["rin_ohm"]))

    def test_bandwidth_ceiling_comes_back_with_the_length(self):
        v = antenna.printed_wire(2.45, 4.4, 1.6)["value"]
        ceiling = v["bandwidth_ceiling"]
        self.assertGreater(ceiling["q_min_chu"], 0.0)
        self.assertGreater(ceiling["max_bandwidth_vswr_lt_2_pct"], 0.0)


class TestDipoleMonopole(unittest.TestCase):
    def test_dipole_is_0_475_lambda_and_73_ohm(self):
        v = antenna.dipole_monopole(2.45)["value"]
        self.assertAlmostEqual(v["lambda0_mm"], 122.364, places=2)
        self.assertAlmostEqual(v["resonant_length_mm"], 58.123, places=2)
        self.assertAlmostEqual(v["radiation_resistance_ohm"], 73.1, places=2)
        self.assertAlmostEqual(v["directivity_dbi"], 2.15, places=2)

    def test_monopole_is_half_the_dipole(self):
        d = antenna.dipole_monopole(2.45)["value"]
        m = antenna.dipole_monopole(2.45, kind="monopole")["value"]
        self.assertAlmostEqual(m["resonant_length_mm"], d["resonant_length_mm"] / 2.0,
                               delta=0.001)
        self.assertAlmostEqual(m["radiation_resistance_ohm"],
                               d["radiation_resistance_ohm"] / 2.0, delta=0.1)

    def test_a_given_length_is_answered_with_its_own_resistance(self):
        v = antenna.dipole_monopole(2.45, length_mm=52.0)["value"]
        self.assertIn("requested_length", v)
        g = v["requested_length"]
        self.assertAlmostEqual(g["length_mm"], 52.0, places=6)
        # 52 mm is short of the 58.1 mm resonance, and the model says so in the
        # only way it can: as a fraction of lambda0, not as a reactance it cannot
        # compute from the sinusoidal distribution alone
        self.assertAlmostEqual(g["fraction_of_lambda0"], 0.425, places=3)
        self.assertLess(g["fraction_of_lambda0"], 0.475)
        self.assertIn("reactance", g["note"])

    def test_bad_kind(self):
        r = antenna.dipole_monopole(2.45, kind="yagi")
        self.assertEqual(r["reason_code"], "ARG_INVALID")


class TestHelix(unittest.TestCase):
    def test_a_textbook_axial_helix(self):
        v = antenna.helix_axial(2.45, 6, 40.0, turn_spacing_mm=30.0)["value"]
        self.assertAlmostEqual(v["C_over_lambda"], 1.027, places=3)
        self.assertAlmostEqual(v["pitch_deg"], 13.43, places=2)
        self.assertAlmostEqual(v["axial_length_mm"], 180.0, places=2)
        self.assertGreater(v["gain_dbic_estimate"], 10.0)
        self.assertLess(v["hpbw_deg_estimate"], 90.0)

    def test_gain_grows_with_turns(self):
        g = [antenna.helix_axial(2.45, n, 40.0, turn_spacing_mm=30.0)["value"]["gain_dbic_estimate"]
             for n in (4, 8, 16)]
        self.assertEqual(g, sorted(g))

    def test_normal_mode_geometry_is_refused(self):
        # diameter 20 mm at 2.45 GHz is C/lambda = 0.51 and the pitch is far too
        # small: that is a normal-mode helix, i.e. a poor short dipole
        r = antenna.helix_axial(2.45, 6, 20.0, turn_spacing_mm=5.0)
        self.assertEqual(r["status"], "error")
        self.assertEqual(r["reason_code"], "OUT_OF_VALIDITY")

    def test_too_few_turns_are_refused(self):
        r = antenna.helix_axial(2.45, 2, 40.0, turn_spacing_mm=30.0)
        self.assertEqual(r["reason_code"], "OUT_OF_VALIDITY")

    def test_pitch_is_accepted_instead_of_spacing(self):
        a = antenna.helix_axial(2.45, 6, 40.0, turn_spacing_mm=30.0)["value"]
        b = antenna.helix_axial(2.45, 6, 40.0, pitch_deg=a["pitch_deg"])["value"]
        self.assertAlmostEqual(a["turn_spacing_mm"], b["turn_spacing_mm"], delta=0.05)


class TestSmallLoop(unittest.TestCase):
    def test_125_khz_rfid_loop_is_very_small_indeed(self):
        v = antenna.small_loop(0.01356, diameter_mm=40.0)["value"]
        self.assertAlmostEqual(v["circumference_mm"], 125.664, places=2)
        self.assertLess(v["circumference_over_lambda"], 0.01)
        # R_rad is a fraction of a micro-ohm here - keeping the digits is the
        # whole point, rounding it to 0.0 would read as "does not radiate"
        self.assertLess(v["r_rad_ohm"], 1e-6)
        self.assertGreater(v["r_rad_ohm"], 0.0)

    def test_loop_above_the_limit_is_refused_with_the_number_it_would_have_given(self):
        r = antenna.small_loop(2.45, diameter_mm=40.0)
        self.assertEqual(r["status"], "error")
        self.assertEqual(r["reason_code"], "OUT_OF_VALIDITY")
        self.assertGreater(r["circumference_over_lambda"], 1.0)
        self.assertIn("r_rad_if_formula_were_used_ohm", r)

    def test_area_and_diameter_agree(self):
        import math
        a = antenna.small_loop(0.01356, diameter_mm=40.0)["value"]
        b = antenna.small_loop(0.01356, area_mm2=math.pi * 400.0)["value"]
        self.assertAlmostEqual(a["area_mm2"], b["area_mm2"], places=2)
        self.assertAlmostEqual(a["r_rad_ohm"], b["r_rad_ohm"], places=12)

    def test_missing_geometry(self):
        r = antenna.small_loop(0.01356)
        self.assertEqual(r["reason_code"], "ARG_REQUIRED")


class TestChuLimit(unittest.TestCase):
    def test_a_ten_mm_cube_at_2_4_ghz_cannot_be_wideband(self):
        v = antenna.q_limit(2.4, 10.0)["value"]
        self.assertAlmostEqual(v["sphere_radius_mm"], 5.0, places=3)
        self.assertAlmostEqual(v["ka"], 0.2515, places=3)
        # Q >= 1/(ka)^3 + 1/(ka)
        ka = v["ka"]
        self.assertAlmostEqual(v["q_min"], 1.0 / ka ** 3 + 1.0 / ka, places=2)
        self.assertLess(v["max_fractional_bandwidth_pct"], 2.0)

    def test_a_bigger_box_allows_more_bandwidth(self):
        small = antenna.q_limit(2.4, 10.0)["value"]
        big = antenna.q_limit(2.4, 60.0)["value"]
        self.assertLess(big["q_min"], small["q_min"])
        self.assertGreater(big["max_fractional_bandwidth_pct"],
                           small["max_fractional_bandwidth_pct"])

    def test_vswr_2_is_wider_than_vswr_1_5(self):
        a = antenna.q_limit(2.4, 20.0, vswr=1.5)["value"]
        b = antenna.q_limit(2.4, 20.0, vswr=2.0)["value"]
        self.assertGreater(b["max_fractional_bandwidth_pct"],
                           a["max_fractional_bandwidth_pct"])


class TestChipAntenna(unittest.TestCase):
    def test_it_gives_the_checklist_and_refuses_the_gain(self):
        r = antenna.chip_antenna(2.4, keepout_x_mm=6.0, keepout_y_mm=4.0)
        self.assertEqual(r["status"], "ok")
        v = r["value"]
        # lambda0 at 2.4 GHz is 124.914 mm, so the reactive near field ends at
        # lambda/(2*pi) = 19.88 mm
        self.assertAlmostEqual(v["near_field_boundary_mm"],
                               124.9135 / (2 * 3.141592653589793), places=2)
        self.assertTrue(v["checklist"])
        # this is the honest part: what is NOT computed is listed on every answer
        self.assertIn("gain", " ".join(v["not_computed"]).lower())
        self.assertNotIn("gain_dbi", v)  # no invented number

    def test_a_known_gain_is_only_echoed_not_derived(self):
        v = antenna.chip_antenna(2.4, max_gain_dbi=0.5)["value"]
        self.assertEqual(v["datasheet_gain_dbi"], 0.5)

    def test_keepout_smaller_than_the_near_field_is_flagged(self):
        v = antenna.chip_antenna(2.4, keepout_x_mm=1.0, keepout_y_mm=1.0)["value"]
        self.assertTrue(v.get("warnings"))
        self.assertGreaterEqual(len(v["warnings"]), 2)


if __name__ == "__main__":
    unittest.main()
