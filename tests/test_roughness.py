"""Surface roughness: one multiplier, and the three things that can be wrong.

The Hammerstad-Jensen form is small enough to look obviously right and wrong in
ways nobody notices:

  * the exponent (Rq/delta squared, not linear) - the anchors below move by
    30 % if the square goes missing, so they are pinned to five digits;
  * which quantity it multiplies. Rs *= K means Qc = h/(delta_s*K) = h/delta_s
    / K, NOT / K^2. The cross-check here is a second code path that never sees
    K: replace the conductivity with sigma_eff = sigma/K^2 and let the smooth
    model do the work, because Rs = 1/(sigma*delta_s) and delta_s ~ 1/sqrt
    (sigma). qc goes as sqrt(sigma), so that path has to land on qc/K - an
    implementation that divided by K^2 lands on 214.9 instead of 405.6;
  * whether it stays on the copper side. roughness may move q_conductor and
    the surface resistance; q_dielectric is bit-identical, and so is the whole
    answer when the argument is left out. That last one is the negative
    control: a coefficient that leaked into the dielectric path shows up there
    as a change with nothing to explain it.

Also pinned: K never reaches 2 (the form cannot make the path more than twice
the flat one), the same foil costs more at higher frequency (because delta_s
shrinks), and an ideal conductor with a roughness is refused instead of
silently ignored.
"""
import math
import unittest

from pcb_agent_mcp.calc import cavity as cv
from pcb_agent_mcp.calc import power as pw
from pcb_agent_mcp.calc.roughness import (MU0, effective_conductivity,
                                          roughness_factor, skin_depth_m)

F0 = 1e9
SIGMA = pw.COPPER_SIGMA
DELTA0 = skin_depth_m(F0, SIGMA)          # one skin depth at 1 GHz in copper

W_MM = H_MM = 50.0
ER = 4.4
T_MM = 1.6
TAND = 0.02
PORT = (12.5, 25.0)
PAD_MM = 0.5
BASE = dict(width_mm=W_MM, height_mm=H_MM, er=ER, thickness_mm=T_MM,
            tan_delta=TAND, port_mm=PORT, port_size_mm=PAD_MM)


class TestTheFactor(unittest.TestCase):

    def test_the_three_anchors(self):
        """K(delta) = 1.60514, K(2 delta) = 1.88750, K(10 delta) = 1.99545."""
        self.assertAlmostEqual(roughness_factor(DELTA0, DELTA0), 1.6051369134225069,
                               places=12)
        self.assertAlmostEqual(roughness_factor(2 * DELTA0, DELTA0),
                               1.8875036482733578, places=12)
        self.assertAlmostEqual(roughness_factor(10 * DELTA0, DELTA0), 1.99545,
                               places=5)

    def test_it_never_reaches_two(self):
        """arctan is bounded by pi/2, so K < 2 for any roughness: a sanity rail."""
        for mult in (1e2, 1e3, 1e6):
            k = roughness_factor(mult * DELTA0, DELTA0)
            self.assertLess(k, 2.0)           # 2 is the ceiling, never reached
            self.assertGreater(k, 1.99)
        # past u ~ 1e7 the double rounds to exactly 2 and cannot go above it,
        # which is the physical statement (the path is at most twice as long)
        for mult in (1e9, 1e15):
            self.assertLessEqual(roughness_factor(mult * DELTA0, DELTA0), 2.0)

    def test_smooth_is_exactly_one(self):
        """None, 0, negative, and a missing skin depth all mean 'smooth'."""
        self.assertEqual(roughness_factor(None, DELTA0), 1.0)
        self.assertEqual(roughness_factor(0.0, DELTA0), 1.0)
        self.assertEqual(roughness_factor(-1e-6, DELTA0), 1.0)
        self.assertEqual(roughness_factor(DELTA0, 0.0), 1.0)
        self.assertEqual(roughness_factor(DELTA0, None), 1.0)
        self.assertEqual(roughness_factor("x", DELTA0), 1.0)

    def test_the_square_is_there(self):
        """u and u^2 must not give the same factor - the anchor is the check."""
        u = 0.5
        lin = 1.0 + (2.0 / math.pi) * math.atan(1.4 * u)
        sq = roughness_factor(u * DELTA0, DELTA0)
        self.assertGreater(abs(sq - lin), 0.05)

    def test_skin_depth_is_the_classical_one(self):
        w = 2.0 * math.pi * F0
        self.assertAlmostEqual(skin_depth_m(F0, SIGMA),
                               math.sqrt(2.0 / (w * MU0 * SIGMA)), places=18)

    def test_effective_conductivity_reproduces_the_loss(self):
        """Rs_eff = 1/(sigma_eff*delta_eff) == Rs*K, because delta_eff = delta*K."""
        for rq_mult in (0.3, 1.0, 2.0, 5.0):
            k = roughness_factor(rq_mult * DELTA0, DELTA0)
            se = effective_conductivity(SIGMA, rq_mult * DELTA0, DELTA0)
            self.assertAlmostEqual(se / (SIGMA / (k * k)), 1.0, places=12)
            d_eff = skin_depth_m(F0, se)
            rs_smooth = 1.0 / (SIGMA * DELTA0)
            self.assertAlmostEqual(1.0 / (se * d_eff) / (rs_smooth * k), 1.0,
                                   places=12)


class TestPlaneQ(unittest.TestCase):

    def test_the_default_answer_does_not_move(self):
        """No roughness_um => the same dict, key for key (this is the contract)."""
        a = cv.plane_q(F0, T_MM, tan_delta=TAND)
        self.assertEqual(a["status"], "ok")
        v = a["value"]
        self.assertNotIn("roughness", v)
        self.assertNotIn("roughness", a)
        # the copper number is still h/delta_s, from the smooth formula
        self.assertAlmostEqual(v["q_conductor"] / (T_MM * 1e-3 / DELTA0), 1.0,
                               delta=1e-6)
        self.assertAlmostEqual(v["q_dielectric"] / (1.0 / TAND), 1.0, delta=1e-6)

    def test_zero_roughness_is_smooth_with_a_report(self):
        """rq = 0 is allowed: the numbers are the smooth ones, the block says 1."""
        a = cv.plane_q(F0, T_MM, tan_delta=TAND)["value"]
        b = cv.plane_q(F0, T_MM, tan_delta=TAND, roughness_um=0.0)["value"]
        self.assertEqual(b["roughness"]["loss_factor"], 1.0)
        for k in ("q_conductor", "q_dielectric", "q_total", "skin_depth_um",
                  "surface_resistance_ohm_sq"):
            self.assertEqual(a[k], b[k], k)

    def test_qc_drops_by_k_and_the_dielectric_does_not(self):
        rq_um = 2.0
        smooth = cv.plane_q(F0, T_MM, tan_delta=TAND)["value"]
        rough = cv.plane_q(F0, T_MM, tan_delta=TAND, roughness_um=rq_um)["value"]
        k = rough["roughness"]["loss_factor"]
        self.assertAlmostEqual(k / roughness_factor(rq_um * 1e-6, DELTA0), 1.0,
                               delta=1e-5)   # k comes back through _sig, 6 digits
        self.assertAlmostEqual(rough["q_conductor"] / (smooth["q_conductor"] / k),
                               1.0, delta=1e-5)
        self.assertEqual(rough["q_dielectric"], smooth["q_dielectric"])
        self.assertEqual(rough["tan_delta"], smooth["tan_delta"])
        self.assertAlmostEqual(
            rough["surface_resistance_ohm_sq"]
            / (smooth["surface_resistance_ohm_sq"] * k), 1.0, delta=1e-5)

    def test_the_reciprocals_still_add(self):
        v = cv.plane_q(F0, T_MM, tan_delta=TAND, roughness_um=2.0)["value"]
        self.assertAlmostEqual(
            v["q_total"], 1.0 / (1.0 / v["q_conductor"] + 1.0 / v["q_dielectric"]),
            delta=1e-6 * v["q_total"])
        self.assertAlmostEqual(v["which_dominates"], "dielectric")  # Qd = 50 wins
        self.assertAlmostEqual(
            v["half_power_bandwidth_hz"] / (F0 / v["q_total"]), 1.0, places=5)

    def test_sigma_eff_is_the_same_answer_through_another_path(self):
        """The cross-check that catches Rs*=K applied as Rs*=K^2."""
        for rq_mult, f in ((1.0, 1e9), (3.0, 4e9), (0.4, 2.5e9)):
            d = skin_depth_m(f, SIGMA)
            k = roughness_factor(rq_mult * d, d)
            with_k = cv.plane_q(f, T_MM, tan_delta=TAND,
                                roughness_um=rq_mult * d * 1e6)["value"]
            via_sigma = cv.plane_q(
                f, T_MM, tan_delta=TAND,
                conductivity_s_m=SIGMA / (k * k))["value"]
            self.assertAlmostEqual(via_sigma["q_conductor"]
                                   / (with_k["q_conductor"]), 1.0, delta=2e-6)

    def test_more_roughness_is_more_loss_and_it_is_monotone(self):
        qc = [cv.plane_q(F0, T_MM, tan_delta=TAND, roughness_um=r)["value"]
              ["q_conductor"] for r in (0.0, 0.3, 0.5, 1.0, 2.0, 5.0)]
        for a, b in zip(qc, qc[1:]):
            self.assertGreater(a, b)
        # and it saturates: 5 um is already ~2.4 delta, so the last step is small
        self.assertGreater(qc[-1] / qc[-2], 0.8)

    def test_the_same_foil_costs_more_at_higher_frequency(self):
        """delta_s ~ 1/sqrt(f), so K grows with f for a FIXED Rq."""
        ks = [cv.plane_q(f, T_MM, roughness_um=2.0)["value"]["roughness"]
              ["loss_factor"] for f in (1e9, 2e9, 10e9)]
        self.assertLess(ks[0], ks[1])
        self.assertLess(ks[1], ks[2])

    def test_roughness_is_refused_without_copper(self):
        """cavity_plane_q always has copper; the negative and the junk are refused."""
        for bad in (-1.0, "rough"):
            r = cv.plane_q(F0, T_MM, tan_delta=TAND, roughness_um=bad)
            self.assertEqual(r["status"], "error")
            self.assertEqual(r["reason_code"], "ARG_RANGE")

    def test_it_can_change_the_total_q_when_copper_dominates(self):
        """The honest reading: on 1.6 mm FR4 the dielectric hides it, on a thin
        low-loss dielectric the same foil moves Q by 40 %."""
        fr4 = [cv.plane_q(2e9, 1.6, tan_delta=0.02, roughness_um=r)["value"]
               ["q_total"] for r in (None, 2.0)]
        thin = [cv.plane_q(2e9, 0.1, tan_delta=0.002, roughness_um=r)["value"]
                for r in (None, 2.0)]
        self.assertLess(fr4[1] / fr4[0], 1.0)
        self.assertGreater(fr4[1] / fr4[0], 0.95)          # ~1 % on FR4
        self.assertLess(thin[1]["q_total"] / thin[0]["q_total"], 0.7)
        self.assertEqual(thin[1]["q_dielectric"], thin[0]["q_dielectric"])


class TestPlaneCurve(unittest.TestCase):

    def test_the_curve_carries_it_and_the_peak_comes_down(self):
        smooth = pw.pdn_plane_impedance(max_order=256, **BASE)
        rough = pw.pdn_plane_impedance(max_order=256, roughness_um=2.0, **BASE)
        self.assertEqual(smooth["status"], "ok")
        self.assertEqual(rough["status"], "ok")
        self.assertNotIn("roughness", smooth)
        blk = rough["roughness"]
        self.assertEqual(blk["rq_um"], 2.0)
        self.assertGreater(blk["loss_factor_at_first_mode"], 1.5)
        self.assertAlmostEqual(blk["q_copper_smooth_at_first_mode"]
                               / blk["q_copper_with_roughness_at_first_mode"],
                               blk["loss_factor_at_first_mode"], delta=1e-4)
        self.assertGreater(blk["skin_depth_at_first_mode_um"], 1.0)
        # same modes, same order, a lower peak - and a SHALLOWER dip, which is
        # the direction that is easy to get backwards: a null is a near-short
        # across the pair, and damping fills it in, so |Z| at the dip goes UP
        self.assertEqual(rough["truncation_order"], smooth["truncation_order"])
        self.assertEqual(rough["first_mode_q_dielectric"],
                         smooth["first_mode_q_dielectric"])
        self.assertLess(rough["first_mode_q"], smooth["first_mode_q"])
        self.assertLessEqual(rough["peak_z_ohm"], smooth["peak_z_ohm"])
        self.assertGreater(rough["dip_z_ohm"], smooth["dip_z_ohm"])

    def test_ideal_copper_with_roughness_is_refused(self):
        args = dict(BASE)
        args["conductivity_s_m"] = None
        r = pw.pdn_plane_impedance(max_order=16, roughness_um=2.0, **args)
        self.assertEqual(r["status"], "error")
        self.assertEqual(r["reason_code"], "ARG_RANGE")

    def test_with_caps_takes_the_same_argument(self):
        caps = [{"at_mm": [25.0, 25.0], "c_farad": 100e-9, "pad_mm": 0.5,
                 "esr_ohm": 0.01, "esl_h": 1e-9}]
        args = dict(BASE)
        args["caps"] = caps
        args["freqs_hz"] = [1.0e9, 1.429e9]
        smooth = pw.pdn_plane_with_caps(max_order=256, **args)
        rough = pw.pdn_plane_with_caps(max_order=256, roughness_um=2.0, **args)
        self.assertEqual(smooth["status"], "ok")
        self.assertEqual(rough["status"], "ok")
        self.assertNotIn("roughness", smooth)
        self.assertIn("roughness", rough)
        self.assertLess(rough["first_mode_q_copper"], smooth["first_mode_q_copper"])
        self.assertEqual(rough["first_mode_q_dielectric"],
                         smooth["first_mode_q_dielectric"])


class TestTheFeedlineUsesTheSameFormula(unittest.TestCase):
    """One formula in one place: the trace-loss multiplier and the plane-Q one
    must agree, or two tools quote different copper for the same foil."""

    def test_the_two_paths_agree(self):
        from pcb_agent_mcp.calc import feedline as fl
        for f_ghz, rq in ((1.0, 1.0), (5.0, 0.5), (10.0, 2.0)):
            a, _ = fl._roughness_factor(rq, f_ghz)
            b = roughness_factor(rq * 1e-6, skin_depth_m(f_ghz * 1e9, pw.COPPER_SIGMA))
            self.assertAlmostEqual(a / b, 1.0, places=15)


if __name__ == "__main__":
    unittest.main()
