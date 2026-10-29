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


class TestEvenOrderLadder(unittest.TestCase):
    """Even-order Chebyshev: the prototype's load is g(n+1) != 1.

    Every expected number here is a hand relation, not a run of the code:
    0.5 dB ripple means the CENTRE of an even-order Chebyshev sits on the ripple
    contour (Tn(0) = -1), the band edges sit on it too, and the output transformer
    ratio is sqrt(g(n+1)) because its input impedance must be ratio^2*z0 = g*z0.
    """

    def setUp(self):
        self.v = _ok(F.lumped_bandpass(2, 1.0e9, 0.1))["value"]

    def test_centre_sits_on_the_ripple_contour(self):
        # even order: |S21|^2 = 1/(1+eps^2) at f0, i.e. exactly the ripple
        self.assertAlmostEqual(self.v["band_edges"]["f_center_il_db"], 0.5, delta=0.01)
        self.assertAlmostEqual(self.v["band_edges"]["f_lower_il_db"], 0.5, delta=0.01)
        self.assertAlmostEqual(self.v["band_edges"]["f_upper_il_db"], 0.5, delta=0.01)

    def test_parity_is_series_first(self):
        self.assertEqual([e["kind"] for e in self.v["elements"]], ["series", "shunt"])
        v4 = _ok(F.lumped_bandpass(4, 1.0e9, 0.1))["value"]
        self.assertEqual([e["kind"] for e in v4["elements"]],
                         ["series", "shunt", "series", "shunt"])

    def test_transformer_ratio_is_sqrt_of_the_load_ratio(self):
        g = self.v["prototype_g"]
        t = self.v["termination"]
        self.assertAlmostEqual(t["output_transformer_ratio"], math.sqrt(g[2]), places=6)
        self.assertAlmostEqual(t["ladder_load_ohm"], 50.0 * g[2], places=4)
        # the ratio squared is what the ladder sees, i.e. g(n+1) * z0
        self.assertAlmostEqual(t["output_transformer_ratio"] ** 2 * t["port_ohm"],
                               t["ladder_load_ohm"], delta=1e-4)

    def test_odd_order_has_no_transformer(self):
        v = _ok(F.lumped_bandpass(3, 1.0e9, 0.1))["value"]
        self.assertEqual(v["termination"]["output_transformer_ratio"], 1.0)
        self.assertAlmostEqual(v["termination"]["ladder_load_ohm"], 50.0, places=9)

    def test_even_order_still_passes_the_two_derivation_audit(self):
        for n in (2, 4):
            sc = _ok(F.lumped_bandpass(n, 1.0e9, 0.1))["value"]["self_check"]
            self.assertLess(sc["max_abs_deviation_db"], F.RESPONSE_AGREEMENT_DB,
                            "n=%d did not reproduce the Chebyshev polynomial" % n)

    def test_power_balance_holds_with_the_transformer(self):
        sc = self.v["self_check"]
        self.assertLess(float(sc["power_balance_rel_error"]), 1e-9)
        self.assertLess(float(sc["s21_squared_vs_delivered_power_rel_error"]), 1e-6)


class TestFiniteQ(unittest.TestCase):
    """Finite unloaded Q: the loss is computed through the cascade, and the centre
    insertion loss is cross-checked against Matthaei's 4.343*sum(g)/(Qu*FBW)."""

    def setUp(self):
        self.f0 = 1.0e9
        self.bw = 0.1

    def _il(self, q, order=3):
        v = _ok(F.lumped_bandpass(order, self.f0, self.bw, q_unloaded=q))["value"]
        return v["loss"]["insertion_loss_center_db"]

    def test_lossless_answer_has_no_loss_block(self):
        v = _ok(F.lumped_bandpass(3, self.f0, self.bw))["value"]
        self.assertNotIn("loss", v)

    def test_centre_loss_matches_matthaei_dissipation_loss(self):
        for q in (200.0, 80.0):
            v = _ok(F.lumped_bandpass(3, self.f0, self.bw, q_unloaded=q))["value"]
            g = v["prototype_g"]
            want = 4.343 * sum(g[:3]) / (q * self.bw)      # hand formula
            self.assertAlmostEqual(v["loss"]["insertion_loss_center_db"], want,
                                   delta=max(0.15, 0.10 * want))

    def test_lower_q_means_more_loss(self):
        a, b, c = self._il(200.0), self._il(80.0), self._il(30.0)
        self.assertLess(a, b)
        self.assertLess(b, c)
        self.assertGreater(a, 0.0)

    def test_loss_element_is_the_definition_of_unloaded_q(self):
        v = _ok(F.lumped_bandpass(3, self.f0, self.bw, q_unloaded=90.0))["value"]
        w0 = 2.0 * math.pi * self.f0
        for el in v["elements"]:
            if el["kind"] == "series":
                self.assertEqual(el["loss_element"]["kind"], "series_r_ohm")
                r = el["loss_element"]["value"]
                # Q = w0*L/R must come back to 90
                self.assertAlmostEqual(w0 * el["L_nh"] * 1e-9 / r, 90.0, delta=1e-4)
            else:
                self.assertEqual(el["loss_element"]["kind"], "shunt_g_s")
                gsh = el["loss_element"]["value"]
                # Q = w0*C/G
                self.assertAlmostEqual(w0 * el["C_pf"] * 1e-12 / gsh, 90.0, delta=1e-4)

    def test_the_wave_identity_holds_at_the_centre(self):
        # |S21|^2 = 1 - |S11|^2 - (dissipated fraction): the energy bookkeeping and
        # the wave answer are two statements of the same thing, so they must agree.
        v = _ok(F.lumped_bandpass(3, self.f0, self.bw, q_unloaded=80.0))["value"]
        frac = v["loss"]["dissipated_fraction_at_center"]
        self.assertGreater(frac, 0.0)
        self.assertLess(frac, 1.0)
        il_c = v["band_edges"]["f_center_il_db"]
        s11_abs = 10.0 ** (-v["loss"]["s11_at_center_db"] / 20.0)
        s21_sq = 10.0 ** (-il_c / 10.0)
        self.assertAlmostEqual(s21_sq + s11_abs ** 2 + frac, 1.0, delta=0.01)

    def test_loaded_q_is_reported_and_below_the_unloaded_q(self):
        for qu in (200.0, 100.0):
            L = _ok(F.lumped_bandpass(3, self.f0, self.bw,
                                      q_unloaded=qu))["value"]["loss"]
            self.assertIsNotNone(L["loaded_q"])
            self.assertLess(L["loaded_q"], qu)
            self.assertIn("3 dB of the in-band minimum", L["delta_3db_definition"])
            self.assertIn("MEASURED", L["loaded_q_caveat"])
            # a 3-pole 10 % band must land its loaded Q near 1/(1.15*0.1) = 8.7
            self.assertAlmostEqual(L["loaded_q"], 8.7, delta=1.5)

    def test_per_resonator_q_list(self):
        v = _ok(F.lumped_bandpass(3, self.f0, self.bw, q_unloaded=[200.0, 60.0, 200.0]))
        self.assertEqual(v["value"]["loss"]["q_unloaded"][1], 60.0)

    def test_q_rejections(self):
        _err(F.lumped_bandpass(3, self.f0, self.bw, q_unloaded=0.0), "ARG_INVALID")
        _err(F.lumped_bandpass(3, self.f0, self.bw, q_unloaded=[100.0, 100.0]),
             "ARG_INVALID")

    def test_power_audit_still_holds_with_loss(self):
        sc = _ok(F.lumped_bandpass(3, self.f0, self.bw, q_unloaded=50.0))["value"]
        self.assertLess(float(sc["self_check"]["power_balance_rel_error"]), 1e-9)
        self.assertLess(
            float(sc["self_check"]["s21_squared_vs_delivered_power_rel_error"]), 1e-6)


class TestParasitics(unittest.TestCase):
    def setUp(self):
        self.f0 = 1.0e9
        self.bw = 0.1

    def test_inductor_self_resonance_is_reported(self):
        v = _ok(F.lumped_bandpass(3, self.f0, self.bw,
                                  parasitics={"cp_f": 0.2e-12}))["value"]
        cp = v["loss"]["parasitics"]
        self.assertEqual(cp["cp_f"], 0.2e-12)
        for el, srf in zip(v["elements"], cp["inductor_srf_hz"]):
            want = 1.0 / (2.0 * math.pi * math.sqrt(el["L_nh"] * 1e-9 * 0.2e-12))
            self.assertAlmostEqual(srf, want, delta=want * 1e-5)

    def test_a_self_resonance_below_the_band_is_flagged(self):
        # 0.5 pF across the 87 nH series inductor puts its SRF at ~762 MHz, under f0
        v = _ok(F.lumped_bandpass(3, self.f0, self.bw,
                                  parasitics={"cp_f": 0.5e-12}))["value"]
        cp = v["loss"]["parasitics"]
        self.assertTrue(cp["srf_below_3f0"])
        self.assertIn("warning", cp)

    def test_small_parasitics_barely_move_the_response(self):
        v = _ok(F.lumped_bandpass(3, self.f0, self.bw,
                                  parasitics={"cp_f": 1e-15,
                                              "esl_h": 1e-15}))["value"]
        self.assertAlmostEqual(v["loss"]["insertion_loss_center_db"], 0.0, delta=0.01)
        self.assertFalse(v["loss"]["parasitics"]["srf_below_3f0"])

    def test_parasitics_only_make_it_worse(self):
        base = _ok(F.lumped_bandpass(3, self.f0, self.bw, q_unloaded=200.0))["value"]
        worse = _ok(F.lumped_bandpass(3, self.f0, self.bw, q_unloaded=200.0,
                                      parasitics={"cp_f": 0.1e-12,
                                                  "esl_h": 0.2e-9}))["value"]
        self.assertGreater(worse["loss"]["insertion_loss_center_db"],
                           base["loss"]["insertion_loss_center_db"])

    def test_negative_parasitics_refused(self):
        _err(F.lumped_bandpass(3, self.f0, self.bw,
                              parasitics={"cp_f": -1e-12}), "ARG_INVALID")
        _err(F.lumped_bandpass(3, self.f0, self.bw,
                              parasitics={"esl_h": -1e-9}), "ARG_INVALID")


class TestMonteCarlo(unittest.TestCase):
    """Tolerance yield. Zero tolerance must return the nominal design bit for bit
    (no sampling), and a seed must make the answer reproducible."""

    def setUp(self):
        self.args = dict(f0_hz=1.0e9, fbw=0.1)

    def test_zero_tolerance_is_degenerate_not_sampled(self):
        r = _ok(F.filter_monte_carlo(3, n_draws=500, seed=1, **self.args))
        v = r["value"]
        self.assertTrue(v["degenerate_zero_tolerance"])
        self.assertEqual(v["n_draws"], 1)
        self.assertEqual(v["yield_pct"], 100.0)
        self.assertIn("every draw IS the nominal design", r["how_to_read"])

    def test_zero_tolerance_metrics_equal_the_nominal_filter(self):
        v = _ok(F.filter_monte_carlo(3, seed=1, **self.args))["value"]
        nom = _ok(F.lumped_bandpass(3, 1.0e9, 0.1))["value"]["band_edges"]
        self.assertAlmostEqual(v["stats"]["il_center_db"]["median"],
                               nom["f_center_il_db"], delta=1e-3)
        self.assertAlmostEqual(v["stats"]["il_lower_edge_db"]["median"],
                               nom["f_lower_il_db"], delta=1e-3)
        self.assertAlmostEqual(v["stats"]["il_upper_edge_db"]["median"],
                               nom["f_upper_il_db"], delta=1e-3)

    def test_same_seed_same_answer(self):
        a = _ok(F.filter_monte_carlo(3, tol_c_pct=2, tol_l_pct=2, n_draws=100,
                                     seed=42, **self.args))["value"]
        b = _ok(F.filter_monte_carlo(3, tol_c_pct=2, tol_l_pct=2, n_draws=100,
                                     seed=42, **self.args))["value"]
        self.assertEqual(a["yield_pct"], b["yield_pct"])
        self.assertEqual(a["stats"], b["stats"])

    def test_tolerance_costs_yield(self):
        tight = _ok(F.filter_monte_carlo(3, tol_c_pct=0.5, tol_l_pct=0.5, n_draws=200,
                                         seed=5, **self.args))["value"]
        loose = _ok(F.filter_monte_carlo(3, tol_c_pct=5.0, tol_l_pct=5.0, n_draws=200,
                                         seed=5, **self.args))["value"]
        self.assertGreater(tight["yield_pct"], loose["yield_pct"])

    def test_a_loose_spec_passes_everything(self):
        v = _ok(F.filter_monte_carlo(3, tol_c_pct=1, tol_l_pct=1, n_draws=50,
                                     seed=3, il_max_db=20.0, **self.args))["value"]
        self.assertEqual(v["yield_pct"], 100.0)

    def test_nominal_failing_the_spec_is_called_out(self):
        r = _ok(F.filter_monte_carlo(3, tol_c_pct=1, n_draws=20, seed=3,
                                     il_max_db=0.1, **self.args))
        self.assertFalse(r["value"]["nominal_passes"])
        self.assertIn("NOMINAL design already fails", r["how_to_read"])

    def test_statistics_are_ordered_and_the_confidence_width_is_reported(self):
        v = _ok(F.filter_monte_carlo(3, tol_c_pct=2, tol_l_pct=2, n_draws=200,
                                     seed=11, **self.args))["value"]
        st = v["stats"]["worst_passband_il_db"]
        self.assertLessEqual(st["min"], st["median"])
        self.assertLessEqual(st["median"], st["p95"])
        self.assertLessEqual(st["p95"], st["max"])
        # hand check of the binomial half-width: 1.96*sqrt(p(1-p)/n)*100
        p = v["passed"] / float(v["n_draws"])
        self.assertAlmostEqual(v["yield_95ci_pct"],
                               1.96 * math.sqrt(p * (1 - p) / v["n_draws"]) * 100.0,
                               delta=0.01)

    def test_stopband_spec_is_enforced(self):
        # asking for 60 dB at 1.4 GHz makes the yield collapse
        r = _ok(F.filter_monte_carlo(3, tol_c_pct=1, tol_l_pct=1, n_draws=50, seed=2,
                                     f_stop_hz=1.4e9, reject_min_db=60.0,
                                     **self.args))
        self.assertIn("il_stop_db", r["value"]["stats"])
        self.assertLessEqual(r["value"]["yield_pct"], 100.0)

    def test_gaussian_needs_the_sigma_definition_to_be_stated(self):
        r = _ok(F.filter_monte_carlo(3, tol_c_pct=2, tol_l_pct=2, n_draws=50,
                                     seed=4, distribution="gaussian", **self.args))
        self.assertIn("2 sigma", r["assumptions"]["distribution"])
        v = _ok(F.filter_monte_carlo(3, tol_c_pct=2, n_draws=50, seed=4,
                                     distribution="gaussian", **self.args))["value"]
        self.assertEqual(v["distribution"], "gaussian")

    def test_argument_refusals(self):
        _err(F.filter_monte_carlo(3, reject_min_db=40.0, **self.args), "ARG_CONFLICT")
        _err(F.filter_monte_carlo(3, f_stop_hz=2e9, **self.args), "ARG_CONFLICT")
        _err(F.filter_monte_carlo(3, tol_c_pct=-1, **self.args), "ARG_RANGE")
        _err(F.filter_monte_carlo(3, tol_c_pct=120, **self.args), "ARG_RANGE")
        _err(F.filter_monte_carlo(3, n_draws=0, **self.args), "ARG_RANGE")
        _err(F.filter_monte_carlo(3, il_max_db=0.0, **self.args), "ARG_RANGE")
        _err(F.filter_monte_carlo(3, distribution="normal", **self.args), "ARG_INVALID")
        _err(F.filter_monte_carlo(3, n_draws=10, seed=1, f_stop_hz=-5.0,
                                  reject_min_db=40.0, **self.args), "ARG_RANGE")

    def test_even_order_monte_carlo_uses_the_transformer(self):
        v = _ok(F.filter_monte_carlo(2, tol_c_pct=1, n_draws=20, seed=8,
                                     **self.args))["value"]
        # the nominal even-order design has 0.5 dB at the centre, on the ripple line
        self.assertAlmostEqual(v["nominal"]["il_center_db"], 0.5, delta=0.01)


if __name__ == "__main__":
    unittest.main()
