"""Golden tests for the feedline (microstrip / stripline / CPW / GCPW) Calc module.

Three kinds of assertion, and the third one is the reason this file exists:

1. a value against a hand-recomputable reality check (50 ohm on 1.6 mm FR4 is a
   3.0 mm trace on every board in the drawer),
2. the model's own limits, where a wrong derivation is loudest: er = 1 must give
   coplanar strips in vacuum, and h -> infinity must give the infinite-substrate
   closed form - the first version of the CPW code was off by sqrt(eps_eff) and
   only the limit test caught it,
3. refusals: a target impedance the geometry cannot reach has to be an error with
   the reachable band attached, never a width that quietly misses.
"""
import os
import sys
import unittest

sys.path.insert(0, os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "src"))

from pcb_agent_mcp.calc import feedline  # noqa: E402

ETA0 = 376.730313668


class TestMicrostrip(unittest.TestCase):
    def test_three_mm_on_1_6mm_fr4_is_about_50_ohm(self):
        # the number every cheap board actually ships
        r = feedline.feedline_impedance("microstrip", 3.0, 1.6, er=4.3)
        self.assertEqual(r["status"], "ok")
        self.assertAlmostEqual(r["value"]["z0_ohm"], 51.35, places=2)

    def test_width_for_50_ohm_is_the_usual_3_1_mm(self):
        r = feedline.feedline_width(50.0, "microstrip", 1.6, er=4.3)
        self.assertEqual(r["status"], "ok")
        self.assertAlmostEqual(r["value"]["width_mm"], 3.1367, places=3)
        # the reverse answer must reproduce the forward model exactly
        back = feedline.feedline_impedance("microstrip", r["value"]["width_mm"], 1.6,
                                           er=4.3)
        self.assertAlmostEqual(back["value"]["z0_ohm"], 50.0, places=3)

    def test_impedance_falls_as_the_trace_widens(self):
        z = [feedline.feedline_impedance("microstrip", w, 1.6, er=4.3)["value"]["z0_ohm"]
             for w in (0.2, 0.5, 1.0, 2.0, 4.0)]
        self.assertEqual(z, sorted(z, reverse=True))

    def test_copper_thickness_lowers_the_impedance(self):
        bare = feedline.feedline_impedance("microstrip", 1.0, 1.6, er=4.3)["value"]
        clad = feedline.feedline_impedance("microstrip", 1.0, 1.6, er=4.3,
                                           copper_um=35.0)["value"]
        self.assertLess(clad["z0_ohm"], bare["z0_ohm"])
        # 1.25 * 35 um of effective widening, in mm
        self.assertAlmostEqual(clad["metal"]["w_eff_mm"] - bare["metal"]["w_eff_mm"],
                               0.04375, places=5)

    def test_field_check_reproduces_the_closed_form(self):
        r = feedline.feedline_impedance("microstrip", 3.0, 1.6, er=4.3,
                                        verify_field=True)
        self.assertEqual(r["status"], "ok")
        chk = r["value"]["field_check"]
        self.assertIsNotNone(chk["z0_field_ohm"])
        # the closed form is within a few percent of the solver - that is the
        # claim field2d.VALIDATION makes, and it is re-made here on this geometry
        self.assertLess(abs(chk["closed_minus_field_pct"]), 4.0)
        self.assertGreater(chk["mesh"]["nodes"], 100)
        # the solver is run at t = 0 on purpose, so its answer is compared with
        # the closed form at t = 0 and never with the thickness-corrected one
        self.assertAlmostEqual(chk["z0_closed_t0_ohm"], r["value"]["z0_ohm"], places=3)


class TestStripline(unittest.TestCase):
    def test_exact_conformal_value(self):
        # w = 0.4, one-plane distance 0.4 (b = 0.8), er 4.3 -> 48.43 ohm
        r = feedline.feedline_impedance("stripline", 0.4, 0.4, er=4.3)
        self.assertEqual(r["status"], "ok")
        self.assertAlmostEqual(r["value"]["z0_ohm"], 48.433, places=3)
        # a stripline is fully embedded, so eps_eff is er itself
        self.assertAlmostEqual(r["value"]["eps_eff"], 4.3, places=6)


class TestCoplanar(unittest.TestCase):
    def test_widening_the_slot_raises_the_impedance(self):
        z = [feedline.feedline_impedance("cpw", 1.0, 1.6, gap_mm=g, er=4.3)["value"]["z0_ohm"]
             for g in (0.1, 0.2, 0.5, 1.0)]
        self.assertEqual(z, sorted(z))

    def test_er_equals_one_gives_coplanar_strips_in_vacuum(self):
        # with er = 1 both media are air, so the B term vanishes and
        # Z0 = eta0/(4*K(k0)/K(k0')), independent of the substrate thickness
        a, b = 0.5, 1.0
        k0 = a / b
        expected = ETA0 / (4.0 * feedline._ratio(k0))
        thin = feedline.feedline_impedance("cpw", 1.0, 1.6, gap_mm=0.5, er=1.0)["value"]
        thick = feedline.feedline_impedance("cpw", 1.0, 1000.0, gap_mm=0.5, er=1.0)["value"]
        self.assertAlmostEqual(thin["z0_ohm"], expected, places=3)
        self.assertAlmostEqual(thick["z0_ohm"], expected, places=3)
        self.assertAlmostEqual(thin["eps_eff"], 1.0, places=9)

    def test_infinite_substrate_limit(self):
        # h -> infinity: Z0 = (eta0/4) * A^-1 / sqrt((er+1)/2). This is the test
        # that caught a sqrt(eps_eff) error in the first version of the mapping.
        a, b = 0.5, 1.0
        A = feedline._ratio(a / b)
        er = 4.3
        expected = (ETA0 / 4.0) * (1.0 / A) / ((er + 1.0) / 2.0) ** 0.5
        got = feedline.feedline_impedance("cpw", 1.0, 1e6, gap_mm=0.5, er=er)["value"]
        self.assertAlmostEqual(got["z0_ohm"], expected, places=3)
        self.assertAlmostEqual(got["z0_ohm"], 74.013, places=3)
        self.assertAlmostEqual(got["eps_eff"], (er + 1.0) / 2.0, places=4)

    def test_gcpw_converges_to_the_unbacked_answer(self):
        # as the substrate grows the back plane stops being seen from the slot
        unbacked = feedline.feedline_impedance("cpw", 1.0, 1e6, gap_mm=0.5,
                                               er=4.3)["value"]["z0_ohm"]
        backed = feedline.feedline_impedance("gcpw", 1.0, 1e6, gap_mm=0.5,
                                             er=4.3)["value"]["z0_ohm"]
        self.assertAlmostEqual(backed, unbacked, places=3)

    def test_a_back_ground_lowers_the_impedance(self):
        unbacked = feedline.feedline_impedance("cpw", 1.5, 1.6, gap_mm=0.2,
                                               er=4.3)["value"]["z0_ohm"]
        backed = feedline.feedline_impedance("gcpw", 1.5, 1.6, gap_mm=0.2,
                                             er=4.3)["value"]["z0_ohm"]
        self.assertLess(backed, unbacked)


class TestLossAndWavelength(unittest.TestCase):
    def test_guided_wavelength_needs_a_frequency(self):
        no_f = feedline.feedline_impedance("microstrip", 3.0, 1.6, er=4.3)["value"]
        self.assertIsNone(no_f["lambda_g_mm"])
        f = feedline.feedline_impedance("microstrip", 3.0, 1.6, er=4.3,
                                        freq_ghz=2.4)["value"]
        # lambda0 / sqrt(eps_eff)
        self.assertAlmostEqual(f["lambda_g_mm"], 124.9135 / f["eps_eff"] ** 0.5,
                               places=2)

    def test_cpw_conductor_loss_is_null_not_guessed(self):
        # the edge current distribution is not in a closed form, so the honest
        # answer is "not modelled", not a plausible-looking number
        r = feedline.feedline_impedance("cpw", 1.5, 1.6, gap_mm=0.2, er=4.3,
                                       freq_ghz=2.4, tan_delta=0.02)
        loss = r["value"]["loss"]
        self.assertIsNone(loss.get("conductor_db_per_100mm"))
        self.assertIsNotNone(loss.get("dielectric_db_per_100mm"))
        self.assertIsNotNone(loss.get("total_db_per_100mm_lower_bound"))

    def test_microstrip_loss_grows_with_frequency(self):
        lo = feedline.feedline_impedance("microstrip", 3.0, 1.6, er=4.3,
                                         freq_ghz=1.0, tan_delta=0.02)["value"]["loss"]
        hi = feedline.feedline_impedance("microstrip", 3.0, 1.6, er=4.3,
                                         freq_ghz=10.0, tan_delta=0.02)["value"]["loss"]
        self.assertGreater(hi["total_db_per_100mm"], lo["total_db_per_100mm"])


class TestRefusals(unittest.TestCase):
    def test_unknown_cross_section(self):
        r = feedline.feedline_impedance("slots", 1.0, 1.6)
        self.assertEqual(r["status"], "error")
        self.assertEqual(r["reason_code"], "ARG_INVALID")

    def test_coplanar_needs_a_gap(self):
        r = feedline.feedline_impedance("cpw", 1.0, 1.6)
        self.assertEqual(r["status"], "error")
        self.assertEqual(r["reason_code"], "ARG_REQUIRED")

    def test_non_positive_dimensions(self):
        self.assertEqual(feedline.feedline_impedance("microstrip", 0.0, 1.6)["reason_code"],
                         "ARG_RANGE")
        self.assertEqual(feedline.feedline_impedance("microstrip", 1.0, 0.0)["reason_code"],
                         "ARG_RANGE")

    def test_unreachable_target_returns_the_band_not_a_width(self):
        r = feedline.feedline_width(200.0, "microstrip", 0.2)
        self.assertEqual(r["status"], "error")
        self.assertEqual(r["reason_code"], "UNREACHABLE")
        band = r["reachable_ohm"]
        self.assertLess(band["narrowest_z_ohm"], 200.0)
        self.assertGreater(band["widest_z_ohm"], 0.0)
        # the returned band must be the forward model's own validity window
        for width, z in ((band["narrowest_width_mm"], band["narrowest_z_ohm"]),
                         (band["widest_width_mm"], band["widest_z_ohm"])):
            back = feedline.feedline_impedance("microstrip", width, 0.2)
            self.assertEqual(back["status"], "ok")
            self.assertAlmostEqual(back["value"]["z0_ohm"], z, places=2)


if __name__ == "__main__":
    unittest.main()
