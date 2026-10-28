"""Tests for the filter / cavity synthesis layer (calc/filters.py).

Same discipline as the rest of this suite: the expected numbers are NOT taken
from a run of the code under test. Every one is either

  * a textbook prototype table value (Chebyshev 0.5 dB g1..g4 and the
    Butterworth 1-2-1 alternation), or
  * a closed form recomputed here from the fixture geometry (TE101 length,
    surface resistance, R/Q), or
  * an internal consistency relation the code is REQUIRED to uphold and refuse
    the answer when it cannot (ABCD S21 vs the prototype closed form; the
    closed-form Qc vs a numerical wall-loss integral).

Coupling solves are the slow part (3-15 s each on this machine), so the coupling
tests are limited to the cases that pin a documented fact: one golden inversion,
the range refusals, and the k-resolution warning.
"""
import math
import os
import sys
import unittest

sys.path.insert(0, os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "src"))

from pcb_agent_mcp.calc import filters as F  # noqa: E402

C0 = 299792458.0


def _ok(r):
    assert isinstance(r, dict), r
    if r.get("status") != "ok":
        raise AssertionError("expected ok, got %r" % (r,))
    return r


def _err(r, code):
    assert r.get("status") == "error", r
    assert r.get("reason_code") == code, (r.get("reason_code"), r)
    return r


class TestPrototype(unittest.TestCase):
    def test_third_order_chebyshev_half_db(self):
        g = _ok(F.prototype_gvalues(3, "chebyshev", 0.5))["value"]["g"]
        self.assertEqual(len(g), 4)
        # 0.5 dB Chebyshev, n=3: 1.5963, 1.0967, 1.5963, 1.0
        self.assertAlmostEqual(g[0], 1.596331, places=5)
        self.assertAlmostEqual(g[1], 1.096681, places=5)
        self.assertAlmostEqual(g[2], 1.596331, places=5)
        self.assertAlmostEqual(g[3], 1.0, places=9)

    def test_fifth_order_chebyshev_half_db(self):
        g = _ok(F.prototype_gvalues(5, "chebyshev", 0.5))["value"]["g"]
        self.assertAlmostEqual(g[0], 1.705821, places=5)
        self.assertAlmostEqual(g[1], 1.229610, places=5)
        self.assertAlmostEqual(g[2], 2.540881, places=5)
        self.assertAlmostEqual(g[3], 1.229610, places=5)
        self.assertAlmostEqual(g[4], 1.705821, places=5)
        self.assertAlmostEqual(g[5], 1.0, places=9)

    def test_butterworth_is_the_121_alternation(self):
        g = _ok(F.prototype_gvalues(3, "butterworth"))["value"]["g"]
        self.assertEqual(len(g), 4)
        self.assertAlmostEqual(g[0], 1.0, places=9)
        self.assertAlmostEqual(g[1], 2.0, places=9)
        self.assertAlmostEqual(g[2], 1.0, places=9)
        self.assertAlmostEqual(g[3], 1.0, places=9)

    def test_butterworth_rejects_a_ripple(self):
        _err(F.prototype_gvalues(3, "butterworth", 0.5), "ARG_CONFLICT")

    def test_order_out_of_range(self):
        _err(F.prototype_gvalues(0), "ARG_RANGE")
        _err(F.prototype_gvalues(21), "ARG_RANGE")

    def test_unknown_response(self):
        _err(F.prototype_gvalues(3, "bessel"), "ARG_INVALID")


class TestLumpedBandpass(unittest.TestCase):
    """Order 3, f0 = 2.4 GHz, 10 % bandwidth, 50 ohm."""

    def setUp(self):
        self.v = _ok(F.lumped_bandpass(3, 2.4e9, 0.1))["value"]

    def test_center_has_zero_insertion_loss(self):
        # lossless ladder: |S21| = 1 exactly at f0
        self.assertAlmostEqual(self.v["band_edges"]["f_center_il_db"], 0.0, places=6)

    def test_band_edge_is_minus_the_ripple(self):
        e = self.v["band_edges"]
        # geometric band edges of a BP transform: f0*(sqrt(1+(fbw/2)^2) +- fbw/2)
        half = 0.1 / 2.0
        lo = 2.4e9 * (math.sqrt(1.0 + half * half) - half)
        hi = 2.4e9 * (math.sqrt(1.0 + half * half) + half)
        self.assertAlmostEqual(e["f_lower_hz"], lo, delta=1.0)
        self.assertAlmostEqual(e["f_upper_hz"], hi, delta=1.0)
        # and BOTH edges sit on the -0.5 dB Chebyshev contour
        self.assertAlmostEqual(e["f_lower_il_db"], 0.5, delta=0.01)
        self.assertAlmostEqual(e["f_upper_il_db"], 0.5, delta=0.01)

    def test_two_derivations_agree(self):
        # the whole point of the tool: ABCD cascade vs 1/(1+eps^2 Tn^2)
        sc = self.v["self_check"]
        self.assertEqual(sc["tolerance_db"], F.RESPONSE_AGREEMENT_DB)
        self.assertLess(sc["max_abs_deviation_db"], F.RESPONSE_AGREEMENT_DB)

    def test_every_resonator_self_resonates_at_f0(self):
        # the structural signature of the LP->BP transform: each branch is an
        # LC that resonates at f0, so 1/(2 pi sqrt(LC)) must equal f0 for all.
        for el in self.v["elements"]:
            self.assertGreater(el["C_pf"], 0.0)
            self.assertGreater(el["L_nh"], 0.0)
            f = 1.0 / (2.0 * math.pi
                       * math.sqrt(el["L_nh"] * 1e-9 * el["C_pf"] * 1e-12))
            # C_pf / L_nh are rounded to 6 decimals, so 1e-6 relative
            self.assertAlmostEqual(f, 2.4e9, delta=2.4e9 * 1e-5)
            self.assertAlmostEqual(el["f_self_res_hz"], 2.4e9, delta=1.0)

    def test_element_kinds_alternate_from_a_shunt(self):
        kinds = [el["kind"] for el in self.v["elements"]]
        self.assertEqual(kinds, ["shunt", "series", "shunt"])

    def test_fbw_ge_one_refused(self):
        _err(F.lumped_bandpass(3, 2.4e9, 1.0), "ARG_RANGE")

    def test_zero_frequency_refused(self):
        _err(F.lumped_bandpass(3, 0.0, 0.1), "ARG_RANGE")


class TestCoupledMicrostrip(unittest.TestCase):
    def test_golden_inversion_is_self_consistent(self):
        # The answer is only meaningful if the impedances the solver RETURNS for
        # the geometry it returned are the impedances asked for.
        v = _ok(F.coupled_microstrip(70.0, 40.0, 0.2, er=4.3))["value"]
        self.assertLess(v["worst_error_pct"], 0.5)
        self.assertLess(abs(v["z0e_ohm"] - 70.0), 0.35)
        self.assertLess(abs(v["z0o_ohm"] - 40.0), 0.20)
        self.assertGreater(v["w_mm"], 0.0)
        self.assertGreater(v["s_mm"], 0.0)
        self.assertAlmostEqual(v["k_coefficient"], 30.0 / 110.0, delta=0.02)

    def test_odd_must_be_less_than_even(self):
        _err(F.coupled_microstrip(40.0, 70.0, 0.2), "ARG_RANGE")
        _err(F.coupled_microstrip(50.0, 50.0, 0.2), "ARG_RANGE")

    def test_weak_coupling_refused(self):
        # k = 0.4/100.4 ~ 0.004, below the 0.02 floor
        _err(F.coupled_microstrip(50.2, 49.8, 0.2), "OUT_OF_VALIDITY")

    def test_bad_geometry_refused(self):
        _err(F.coupled_microstrip(70.0, 40.0, 0.0), "ARG_RANGE")
        _err(F.coupled_microstrip(70.0, 40.0, 0.2, er=0.5), "ARG_RANGE")

    def test_k_resolution_warning_fires_for_weak_coupling(self):
        # Two nearly equal impedances: the k resolution note MUST be emitted,
        # because a 0.5 % impedance error is a large fraction of k here.
        r = _ok(F.coupled_microstrip(52.0, 49.0, 0.2, fine_div=4.0))
        self.assertIsNotNone(r.get("k_resolution_note"))

    def test_every_iteration_is_reported(self):
        v = _ok(F.coupled_microstrip(70.0, 40.0, 0.2, fine_div=4.0))["value"]
        self.assertGreaterEqual(len(v["iterations"]), 1)
        for step in v["iterations"]:
            self.assertIn("err_pct", step)
            self.assertIn("w_mm", step)


class TestCoupledResonator(unittest.TestCase):
    """Order 3, 2.4 GHz, 5 % bandwidth on 0.254 mm / er 4.3."""

    def setUp(self):
        self.v = _ok(F.coupled_resonator_bpf(3, 2.4e9, 0.05,
                                           h_mm=0.254, er=4.3))["value"]
        self.g = [1.596331, 1.096681, 1.596331, 1.0]
        self.fbw = 0.05

    def test_qe_follows_the_g_values(self):
        self.assertAlmostEqual(self.v["q_external"]["in"], self.g[0] / self.fbw,
                               places=2)
        self.assertAlmostEqual(self.v["q_external"]["out"],
                               self.g[2] * self.g[3] / self.fbw, places=2)

    def test_k_follows_the_g_values(self):
        for sec in self.v["couplings"]:
            i, j = sec["between"]
            target = self.fbw / math.sqrt(self.g[i - 1] * self.g[j - 1])
            self.assertAlmostEqual(sec["k"], target, places=5)

    def test_each_coupling_carries_its_own_verdict(self):
        for sec in self.v["couplings"]:
            self.assertIn("meets_target", sec)
            self.assertIn("k_error_pct", sec)
            self.assertAlmostEqual(sec["achieved_k"],
                                   (sec["z0e_ohm"] - sec["z0o_ohm"])
                                   / (sec["z0e_ohm"] + sec["z0o_ohm"]), places=5)

    def test_resonator_is_about_half_a_guided_wavelength(self):
        res = self.v["resonator"]
        lam_g = res["lambda_g_mm"]
        length = res["length_mm"]
        # two open-end extensions are removed, so it is SHORT of lambda_g/2
        self.assertLess(length, lam_g / 2.0)
        self.assertGreater(length, 0.95 * lam_g / 2.0)
        # BOTH ends are corrected, so the shortening is 2 * extension
        self.assertAlmostEqual(lam_g / 2.0 - length,
                               2.0 * res["open_end_extension_mm"],
                               delta=1e-3)


class TestWaveguideCavity(unittest.TestCase):
    """WR-90 (a = 22.86 mm, b = 10.16 mm) at 10 GHz, copper walls."""

    def setUp(self):
        self.v = _ok(F.waveguide_cavity(10e9, 22.86, 10.16))["value"]

    def test_length_is_half_the_guide_wavelength(self):
        # TE101: k^2 = (pi/a)^2 + (pi/d)^2 with k = 2 pi f / c
        k = 2.0 * math.pi * 10e9 / C0
        d = math.pi / math.sqrt(k * k - (math.pi / 22.86e-3) ** 2) * 1000.0
        self.assertAlmostEqual(self.v["length_mm"], d, delta=0.01)
        self.assertAlmostEqual(self.v["length_mm"], 19.8536, delta=0.01)

    def test_surface_resistance_matches_the_closed_form(self):
        # Rs = sqrt(pi * f * mu0 / sigma), mu0 = 4e-7*pi
        rs = math.sqrt(math.pi * 10e9 * 4e-7 * math.pi / 5.8e7)
        self.assertAlmostEqual(self.v["surface_resistance_ohm"], rs, delta=rs * 1e-3)

    def test_qc_is_derived_two_ways_and_agrees(self):
        self.assertLess(abs(self.v["q_agreement_pct"]), 2.0)
        self.assertAlmostEqual(self.v["q_conductor_numeric"], self.v["q_conductor"],
                               delta=self.v["q_conductor"] * 0.02)

    def test_qc_lands_where_hand_arithmetic_puts_it(self):
        # A standard X-band copper cavity is quoted near 7800-8000; a factor of
        # anything (2, pi, sigma vs Rs) would leave this band immediately.
        self.assertAlmostEqual(self.v["q_conductor"], 7821.5, delta=60.0)
        self.assertAlmostEqual(self.v["unloaded_q"], self.v["q_conductor"],
                               delta=1e-6)

    def test_r_over_q(self):
        # R/Q = 4b/(omega*eps0*a*d) for TE101
        omega = 2.0 * math.pi * 10e9
        eps0 = 8.8541878128e-12
        rq = 4.0 * 10.16e-3 / (omega * eps0 * 22.86e-3 * 19.8536e-3)
        self.assertAlmostEqual(self.v["r_over_q_ohm"], rq, delta=rq * 0.01)

    def test_below_cutoff_refused(self):
        # 5 GHz is below the 6.56 GHz TE10 cutoff of WR-90: no TE101 exists
        _err(F.waveguide_cavity(5e9, 22.86, 10.16), "OUT_OF_VALIDITY")

    def test_only_te101_is_implemented(self):
        _err(F.waveguide_cavity(10e9, 22.86, 10.16, mode="TE011"), "NOT_IMPLEMENTED")

    def test_default_b_is_a_over_two_and_says_so(self):
        v = _ok(F.waveguide_cavity(10e9, 22.86))["value"]
        self.assertAlmostEqual(v["b_mm"], 11.43, places=6)
        self.assertTrue(v["b_assumed"])


if __name__ == "__main__":
    unittest.main()
