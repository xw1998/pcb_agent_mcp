"""Conductor loss from the 2-D field solution.

The point of this file is that the new number is not merely plausible: it is
checked against two things it shares no approximation with.

  * the parallel-plate limit, where INT g^2 ds has an exact value (2/w on
    microstrip: signal face plus a return face as wide as the trace; 1/w on
    stripline: the return current splits between two planes, so each plane
    carries half the current over the same width w);

  * the incremental-inductance rule on homogeneous stripline,
    alpha_c = Rs/(2*eta*Z0) * SUM dZ0/dn, evaluated by finite differences of
    `solve_single` and normalised by the signal current. The two derivations
    start from different physics (one from the charge distribution, one from
    how Z0 moves when the copper recedes) and agree to a few percent.

Everything else here is bookkeeping: the two estimates must both be present,
the roughness factor must be a separate multiplier, and the coplanar case must
still refuse rather than invent a number.
"""
import math
import unittest

from pcb_agent_mcp.calc import feedline, field2d

RS_1GHZ = math.sqrt(math.pi * 1e9 * 1.25663706212e-6 / 5.8e7)
ETA0 = 376.730313668


def _sf(kind, w, h, t=0.0, er=4.3, fine_div=10.0):
    return field2d.conductor_shape_factor(kind, w, h, t, er=er, fine_div=fine_div,
                                          refine=0.0)


class TestParallelPlateLimit(unittest.TestCase):
    def test_microstrip_approaches_two_over_w(self):
        # wide lines: the return current is confined under the trace, so the
        # signal face contributes 1/w and the return face 1/w
        wide = _sf("microstrip", 2.0, 0.2)
        wider = _sf("microstrip", 4.0, 0.2)
        self.assertLess(wide["shape_factor_per_mm"] * 2.0, 2.0)
        self.assertLess(wider["shape_factor_per_mm"] * 4.0, 2.0)
        # monotone towards 2 as w/h grows - never past it
        self.assertLess(wide["shape_factor_per_mm"] * 2.0,
                        wider["shape_factor_per_mm"] * 4.0)
        self.assertGreater(wide["shape_factor_per_mm"] * 2.0, 1.5)

    def test_stripline_approaches_one_over_w(self):
        # the return current splits between the two planes, so each carries half
        # and the sum is 1/w, NOT the 2/w a single-plane sheet model implies
        r = _sf("stripline", 4.0, 0.2)
        got = r["shape_factor_per_mm"] * 4.0
        self.assertLess(got, 1.0)
        self.assertGreater(got, 0.93)


class TestIncrementalInductanceCrossCheck(unittest.TestCase):
    def test_homogeneous_stripline_agrees(self):
        w, h, er, d = 1.0, 0.2, 4.3, 1e-4

        def z0(width=w, half=h):
            return field2d.solve_single("stripline", width, half, 0.0, er,
                                        fine_div=12.0)[0]

        z = z0()
        dzdw = (z0(width=w + d) - z0(width=w - d)) / (2 * d)
        dzdb = (z0(half=h + d) - z0(half=h - d)) / (2 * d)
        # recession: both side walls narrow the strip by 2*dn, both planes push
        # the reference distance out by dn each (b grows by 2*dn)
        alpha_inc = RS_1GHZ / (2 * (ETA0 / math.sqrt(er)) * z) * (-2 * dzdw + 2 * dzdb)
        alpha_sf = RS_1GHZ / (2 * z) * _sf("stripline", w, h)["shape_factor_per_mm"]
        ratio = alpha_sf / alpha_inc
        self.assertGreater(ratio, 0.85)
        self.assertLess(ratio, 1.25)


class TestShapeFactorAccounting(unittest.TestCase):
    def test_split_sums_to_the_total(self):
        r = _sf("microstrip", 0.35, 0.2, 0.035)
        total = r["shape_factor_per_mm"]
        parts = r["live_share"] + r["ground_share"]
        self.assertAlmostEqual(parts, total, places=9)
        self.assertAlmostEqual(r["side_share"] + r["face_share"], total, places=9)

    def test_return_charge_balances_the_signal_charge(self):
        r = _sf("microstrip", 0.5, 0.2)
        self.assertAlmostEqual(r["ground_to_live_charge_ratio"], -1.0, places=2)

    def test_finite_copper_is_steadier_than_zero_thickness(self):
        # the zero-thickness edge has a r^-1/2 charge singularity, so its shape
        # factor keeps moving with the mesh; real foil has a wall to spread over
        def two_meshes(t_mm):
            return field2d.conductor_shape_factor("microstrip", 0.35, 0.2, t_mm,
                                                  er=4.3, refine=1.6)
        zero = two_meshes(0.0)
        foil = two_meshes(0.035)
        self.assertIsNotNone(zero["mesh_drift_pct"])
        self.assertLess(abs(foil["mesh_drift_pct"]), abs(zero["mesh_drift_pct"]))
        self.assertTrue(zero["zero_thickness"])
        self.assertFalse(foil["zero_thickness"])

    def test_node_ceiling_refuses_before_solving(self):
        with self.assertRaises(ValueError):
            field2d.conductor_shape_factor("microstrip", 2.0, 0.2, 0.0, 4.3,
                                           max_nodes=1000)


class TestFeedlineLossAnswer(unittest.TestCase):
    def setUp(self):
        self.r = feedline.feedline_impedance("microstrip", 0.35, 0.2, er=4.3,
                                             freq_ghz=1.0, tan_delta=0.02)["value"]["loss"]

    def test_both_estimates_are_present(self):
        self.assertEqual(self.r["conductor_model"], "field_surface_charge")
        self.assertIsNotNone(self.r["conductor_db_per_100mm"])
        self.assertIsNotNone(self.r["conductor_sheet_bound_db_per_100mm"])
        self.assertNotEqual(self.r["conductor_db_per_100mm"],
                            self.r["conductor_sheet_bound_db_per_100mm"])

    def test_total_is_the_sum_and_the_ratio_matches(self):
        self.assertAlmostEqual(
            self.r["total_db_per_100mm"],
            self.r["conductor_db_per_100mm"] + self.r["dielectric_db_per_100mm"],
            places=3)
        self.assertAlmostEqual(
            self.r["field_over_sheet_ratio"],
            self.r["conductor_db_per_100mm"] / self.r["conductor_sheet_bound_db_per_100mm"],
            places=2)

    def test_zero_thickness_answer_carries_the_caveat(self):
        self.assertIn("shape_factor_caveat", self.r)
        self.assertIsNotNone(self.r["shape_factor_mesh_drift_pct"])

    def test_roughness_is_a_separate_multiplier(self):
        smooth = feedline.feedline_impedance(
            "microstrip", 0.35, 0.2, er=4.3, freq_ghz=1.0,
            tan_delta=0.02)["value"]["loss"]
        rough = feedline.feedline_impedance(
            "microstrip", 0.35, 0.2, er=4.3, freq_ghz=1.0, tan_delta=0.02,
            roughness_um=1.5)["value"]["loss"]
        self.assertNotIn("roughness", smooth)
        self.assertIn("roughness", rough)
        self.assertGreater(rough["roughness"]["factor"], 1.0)
        self.assertLess(rough["roughness"]["factor"], 2.0)
        # the geometry did not change, only Rs
        self.assertAlmostEqual(rough["shape_factor_per_mm"],
                               smooth["shape_factor_per_mm"], places=6)
        self.assertGreater(rough["conductor_db_per_100mm"],
                           smooth["conductor_db_per_100mm"])
        self.assertAlmostEqual(
            rough["effective_surface_resistance_ohm_sq"],
            smooth["surface_resistance_ohm_sq"] * rough["roughness"]["factor"],
            places=5)

    def test_roughness_grows_with_the_roughness(self):
        def db(r):
            return feedline.feedline_impedance(
                "microstrip", 0.35, 0.2, er=4.3, freq_ghz=1.0, tan_delta=0.02,
                roughness_um=r)["value"]["loss"]["conductor_db_per_100mm"]
        self.assertLess(db(0.5), db(1.5))
        self.assertLess(db(1.5), db(3.0))

    def test_negative_roughness_is_refused(self):
        r = feedline.feedline_impedance("microstrip", 0.35, 0.2, er=4.3,
                                        freq_ghz=1.0, tan_delta=0.02,
                                        roughness_um=-1.0)
        self.assertEqual(r["status"], "error")
        self.assertEqual(r["reason_code"], "ARG_RANGE")


class TestFallbacks(unittest.TestCase):
    def test_a_section_over_the_ceiling_falls_back_to_the_sheet_bound(self):
        # 5 um foil on a 50 um dielectric: the mesh the edge needs does not fit
        r = feedline.feedline_impedance("microstrip", 0.95, 0.05, copper_um=5.0,
                                        er=4.3, freq_ghz=1.0, tan_delta=0.02)
        self.assertEqual(r["status"], "ok")
        loss = r["value"]["loss"]
        self.assertEqual(loss["conductor_model"], "sheet_bound")
        self.assertIn("field model unavailable", loss["conductor_note"])
        self.assertAlmostEqual(loss["conductor_db_per_100mm"],
                               loss["conductor_sheet_bound_db_per_100mm"], places=4)

    def test_coplanar_still_refuses_conductor_loss(self):
        r = feedline.feedline_impedance("cpw", 0.3, 0.2, gap_mm=0.2, er=4.3,
                                        freq_ghz=1.0, tan_delta=0.02)
        loss = r["value"]["loss"]
        self.assertEqual(loss["conductor_model"], "not_computed")
        self.assertIsNone(loss["conductor_db_per_100mm"])
        self.assertIn("same layer", loss["conductor_note"])


if __name__ == "__main__":
    unittest.main()
