"""Golden tests for the differential-pair Calc module.

Two things are being pinned here:

1. the forward formula matches a value computed by hand from the stated basis,
2. the reverse solver refuses to answer outside the model's validity window
   instead of returning a width that quietly produces the wrong impedance.
"""
import os
import sys
import unittest

sys.path.insert(0, os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "src"))

from pcb_agent_mcp.calc import diffpair  # noqa: E402


class TestDiffImpedanceForward(unittest.TestCase):
    def test_microstrip_matches_hand_calc(self):
        # h=0.2, w=0.2, t=0.035, er=4.3
        #   single: 5.98*0.2/(0.8*0.2+0.035) = 1.196/0.195 = 6.1333, ln = 1.8139
        #           Z0 = 87/sqrt(5.71)*1.8139 = 36.406*1.8139 = 66.04
        #   coupling s/h = 1.0: 1 - 0.48*exp(-0.96) = 1 - 0.48*0.38289 = 0.81621
        #   Zdiff = 2*66.04*0.81621 = 107.78
        r = diffpair.diff_impedance(0.2, 0.2, 0.2)
        self.assertEqual(r["status"], "ok")
        self.assertAlmostEqual(r["value"], 107.8, places=1)
        self.assertAlmostEqual(r["z_single_ended_ohm"], 66.04, places=2)
        self.assertAlmostEqual(r["coupling_factor"], 0.81621, places=4)

    def test_stripline_uses_its_own_coupling_constant(self):
        r = diffpair.diff_impedance(0.2, 0.2, 0.2, kind="stripline")
        self.assertEqual(r["status"], "ok")
        # b = 2h = 0.4: single 60/sqrt(4.3)*ln(1.6/(0.67*pi*0.195))
        #   = 28.933*ln(3.8986) = 28.933*1.3605 = 39.36
        #   coupling: 1 - 0.347*exp(-2.9*0.2/0.4) = 1 - 0.347*0.23457 = 0.91860
        #   Zdiff = 2*39.36*0.91860 = 72.31
        self.assertAlmostEqual(r["value"], 72.3, places=1)

    def test_coupling_is_monotone_in_spacing(self):
        tight = diffpair.diff_impedance(0.2, 0.1, 0.2)["value"]
        loose = diffpair.diff_impedance(0.2, 0.5, 0.2)["value"]
        self.assertLess(tight, loose)

    def test_spacing_window_reported_not_silently_extrapolated(self):
        r = diffpair.diff_impedance(0.2, 2.0, 0.2)      # s/h = 10, far outside 0.1..3
        self.assertEqual(r["status"], "ok")
        self.assertIn("validity_note", r)

    def test_missing_argument_refused(self):
        self.assertEqual(diffpair.diff_impedance(None, 0.2, 0.2)["status"], "error")


class TestDiffWidthReverse(unittest.TestCase):
    def test_ethernet_100_ohm_reachable(self):
        r = diffpair.width_for_diff_impedance(100.0, 0.2, 0.2)
        self.assertEqual(r["status"], "ok")
        # round-trip: the solved width must reproduce the target
        self.assertAlmostEqual(r["zdiff_recomputed"], 100.0, places=1)
        self.assertAlmostEqual(r["value"], 0.2342, places=3)

    def test_usb_90_ohm_reachable(self):
        r = diffpair.width_for_diff_impedance(90.0, 0.15, 0.18)
        self.assertEqual(r["status"], "ok")
        self.assertAlmostEqual(r["zdiff_recomputed"], 90.0, places=1)

    def test_target_below_model_range_refused(self):
        r = diffpair.width_for_diff_impedance(5.0, 0.2, 0.2)
        self.assertEqual(r["status"], "error")
        self.assertEqual(r["reason_code"], "OUT_OF_VALIDITY")

    def test_unreachable_target_reports_reachable_band(self):
        # s == h means heavy coupling; 400 ohm cannot be reached with w/h <= 3
        r = diffpair.width_for_diff_impedance(400.0, 0.2, 0.2)
        self.assertEqual(r["status"], "error")
        self.assertEqual(r["reason_code"], "UNREACHABLE")
        self.assertIn("reachable", r["hint"])
        self.assertLess(r["reachable_min_ohm"], r["reachable_max_ohm"])

    def test_solved_width_reproduces_target_for_several_pairs(self):
        for target, s, h in ((100.0, 0.2, 0.2), (90.0, 0.15, 0.18),
                             (120.0, 0.3, 0.25), (85.0, 0.12, 0.2)):
            r = diffpair.width_for_diff_impedance(target, s, h)
            self.assertEqual(r["status"], "ok", (target, s, h, r))
            self.assertAlmostEqual(r["zdiff_recomputed"], target, places=0)


if __name__ == "__main__":
    unittest.main()
