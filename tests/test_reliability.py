"""Tests for calc/reliability.py.

Three habits this file keeps, because each one has already caught a real bug in
this repository:

  * a second solution that does NOT share code with the thing under test. Nf is
    recovered by bisection on the strain-life equation instead of by the same
    algebraic rearrangement the module uses; the Norris-Landzberg factor is
    rebuilt from two complete lives rather than from the ratio formula.
  * no assertion on a rounded output field when an identity is meant. The
    module rounds to 6 significant digits for display, which is 1e-6 relative -
    far too coarse for an identity check.
  * directional assertions, not just magnitudes. "c is negative" is worth
    nothing; "longer dwell makes Nf smaller" is the physics.
"""
import unittest

from pcb_agent_mcp.calc import reliability as R
from pcb_agent_mcp.calc import vibration as V


def _bisect_nf(delta_gamma, c, eps_f=0.325, lo=1e-6, hi=1e12, iters=200):
    """Invert delta_gamma/2 = eps_f * (2 Nf)^c by bisection.

    Completely different arithmetic from the module's rearrangement, so an
    exponent or a sign that got flipped shows up as a mismatch here.
    """
    def f(n):
        return eps_f * (2.0 * n) ** c - delta_gamma / 2.0
    # c < 0, so f is decreasing in N: f(lo) > 0 > f(hi)
    for _ in range(iters):
        mid = (lo * hi) ** 0.5 if lo > 0 else (lo + hi) / 2.0
        if f(mid) > 0:
            lo = mid
        else:
            hi = mid
    return (lo + hi) / 2.0


def _nl_from_scratch(cond, m, n, ea, k=8.617333262e-5):
    """N = A f^n dT^-m exp(Ea/kT) with A = 1, in arbitrary units."""
    import math
    return (cond["frequency_cpd"] ** n
            * cond["delta_t_k"] ** (-m)
            * math.exp(ea / k / (cond["t_max_c"] + 273.15)))


class TestEngelmaier(unittest.TestCase):
    def test_c_at_the_documented_limit(self):
        # Tbar = 0 and an infinite dwell is the -0.442 the fit extrapolates to
        self.assertAlmostEqual(R.engelmaier_c(0.0, 1e12), -0.442, places=9)

    def test_c_matches_the_closed_form_by_hand(self):
        # -0.442 - 6e-4*50 + 1.74e-2*ln(1+360/15)
        want = -0.442 - 0.03 + 0.0174 * 3.2188758248682006
        self.assertAlmostEqual(R.engelmaier_c(50.0, 15.0), want, places=12)

    def test_hotter_is_more_damaging(self):
        self.assertLess(R.engelmaier_c(125.0, 15.0), R.engelmaier_c(25.0, 15.0))
        nf_hot = R.engelmaier_nf(0.02, R.engelmaier_c(125.0, 15.0))
        nf_cold = R.engelmaier_nf(0.02, R.engelmaier_c(25.0, 15.0))
        self.assertLess(nf_hot, nf_cold)

    def test_longer_dwell_is_more_damaging(self):
        # the whole point of Engelmaier's c: creep needs time
        self.assertLess(R.engelmaier_c(50.0, 240.0), R.engelmaier_c(50.0, 1.0))
        self.assertLess(R.engelmaier_nf(0.02, R.engelmaier_c(50.0, 240.0)),
                        R.engelmaier_nf(0.02, R.engelmaier_c(50.0, 1.0)))

    def test_nf_agrees_with_a_bisection_second_solution(self):
        for dg in (0.002, 0.01, 0.05, 0.2):
            for c in (-0.30, -0.442, -0.60):
                direct = R.engelmaier_nf(dg, c)
                self.assertAlmostEqual(_bisect_nf(dg, c) / direct, 1.0, places=6,
                                       msg="dg=%r c=%r" % (dg, c))

    def test_nf_is_half_a_cycle_at_unit_normalised_strain(self):
        # dg = 2 eps_f  =>  0.5 * 1^(1/c) = 0.5, independent of c
        for c in (-0.3, -0.5, -0.9):
            self.assertAlmostEqual(R.engelmaier_nf(0.65, c), 0.5, places=12)

    def test_log_log_slope_is_one_over_c(self):
        import math
        c = -0.442
        a = R.engelmaier_nf(0.01, c)
        b = R.engelmaier_nf(0.02, c)
        self.assertAlmostEqual(math.log(b / a) / math.log(2.0), 1.0 / c,
                               places=10)


class TestShearStrain(unittest.TestCase):
    def _ok(self, **kw):
        args = {"joint_height_mm": 0.5, "dnp_mm": 10.0,
                "alpha_component_ppm": 6.0, "alpha_board_ppm": 16.0,
                "delta_t_k": 100.0}
        args.update(kw)
        return R.solder_joint_fatigue(**args)

    def test_strain_is_the_displacement_over_the_standoff(self):
        # 10 mm * 10 ppm/K * 100 K = 10 um, over a 0.5 mm standoff = 0.02
        r = self._ok()
        self.assertEqual(r["status"], "ok")
        self.assertAlmostEqual(r["value"]["shear_strain_range"], 0.02, places=9)

    def test_dnp_defaults_to_the_diagonal_corner(self):
        r = self._ok(dnp_mm=None, body_a_mm=30.0, body_b_mm=40.0)
        self.assertEqual(r["status"], "ok")
        self.assertAlmostEqual(r["value"]["geometry"]["dnp_mm"], 25.0, places=9)
        self.assertIn("diagonal", r["value"]["geometry"]["dnp_source"])

    def test_the_sign_of_the_mismatch_does_not_flip_the_strain(self):
        a = self._ok(alpha_component_ppm=6.0, alpha_board_ppm=16.0)
        b = self._ok(alpha_component_ppm=16.0, alpha_board_ppm=6.0)
        self.assertAlmostEqual(a["value"]["shear_strain_range"],
                               b["value"]["shear_strain_range"], places=12)

    def test_the_same_cte_is_refused_not_answered_with_zero(self):
        r = self._ok(alpha_component_ppm=16.0, alpha_board_ppm=16.0)
        self.assertEqual(r["status"], "error")
        self.assertEqual(r["reason_code"], "ARG_RANGE")

    def test_joint_height_is_required(self):
        r = R.solder_joint_fatigue(dnp_mm=10.0, delta_t_k=100.0,
                                   alpha_component_ppm=6.0,
                                   alpha_board_ppm=16.0)
        self.assertEqual(r["status"], "error")
        self.assertEqual(r["reason_code"], "ARG_REQUIRED")
        self.assertIn("standoff", r["hint"])

    def test_dnp_is_required(self):
        r = R.solder_joint_fatigue(joint_height_mm=0.5, delta_t_k=100.0,
                                   alpha_component_ppm=6.0,
                                   alpha_board_ppm=16.0)
        self.assertEqual(r["status"], "error")
        self.assertEqual(r["reason_code"], "ARG_REQUIRED")

    def test_unknown_alpha_preset_is_refused_with_the_known_ones(self):
        r = R.solder_joint_fatigue(joint_height_mm=0.5, dnp_mm=10.0,
                                   delta_t_k=100.0, alpha_component="unobtainium",
                                   alpha_board_ppm=16.0)
        self.assertEqual(r["status"], "error")
        self.assertIn("fr4", r["hint"])

    def test_compliance_factor_scales_the_strain_linearly(self):
        a = self._ok()
        b = self._ok(compliance_factor=0.5)
        self.assertAlmostEqual(a["value"]["shear_strain_range"]
                               / b["value"]["shear_strain_range"], 2.0, places=9)


class TestMiner(unittest.TestCase):
    def test_damage_adds_and_the_two_reports_agree(self):
        r = R.solder_joint_fatigue(
            joint_height_mm=0.5, dnp_mm=10.0, alpha_component_ppm=6.0,
            alpha_board_ppm=16.0,
            conditions=[{"name": "day", "delta_t_k": 100.0, "cycles": 2,
                         "blocks_per_year": 365},
                        {"name": "night", "delta_t_k": 40.0, "cycles": 2,
                         "blocks_per_year": 365}])
        self.assertEqual(r["status"], "ok")
        v = r["value"]
        rows = v["conditions"]
        self.assertAlmostEqual(
            v["miner"]["total_damage_per_block"],
            rows[0]["damage_per_block"] + rows[1]["damage_per_block"], places=9)
        # both fields are rounded to 6 significant digits, so check the identity
        self.assertAlmostEqual(
            v["miner"]["blocks_to_failure"]
            * v["miner"]["total_damage_per_block"], 1.0, places=5)
        # the milder cycle must last longer, i.e. cost less damage per cycle
        self.assertLess(rows[1]["damage_per_block"] / 2.0,
                        rows[0]["damage_per_block"] / 2.0)

    def test_no_cycle_count_means_no_verdict(self):
        r = R.solder_joint_fatigue(joint_height_mm=0.5, dnp_mm=10.0,
                                   alpha_component_ppm=6.0,
                                   alpha_board_ppm=16.0, delta_t_k=100.0)
        v = r["value"]
        self.assertIsNone(v["conditions"][0]["verdict"])
        self.assertIsNone(v["miner"]["total_damage_per_block"])
        self.assertIsNone(v["miner"]["blocks_to_failure"])

    def test_a_single_condition_states_its_own_inverse(self):
        r = R.solder_joint_fatigue(joint_height_mm=0.5, dnp_mm=10.0,
                                   alpha_component_ppm=6.0,
                                   alpha_board_ppm=16.0, delta_t_k=100.0,
                                   cycles=1)
        self.assertAlmostEqual(
            r["value"]["self_check"]["miner_single_condition_times_damage"],
            0.0, places=9)


class TestNorrisLandzberg(unittest.TestCase):
    TEST = {"delta_t_k": 165.0, "frequency_cpd": 2.0, "t_max_c": 125.0}
    USE = {"delta_t_k": 100.0, "frequency_cpd": 1.0, "t_max_c": 55.0}

    def _run(self, test=None, use=None):
        return R.solder_joint_fatigue(
            joint_height_mm=0.5, dnp_mm=10.0, alpha_component_ppm=6.0,
            alpha_board_ppm=16.0, delta_t_k=100.0,
            nl_test=test or self.TEST, nl_use=use or self.USE)

    def test_af_matches_two_complete_lives(self):
        v = self._run()["value"]
        m, n, ea = R.NL_M, R.NL_N, R.NL_EA_EV
        want = _nl_from_scratch(self.USE, m, n, ea) / _nl_from_scratch(
            self.TEST, m, n, ea)
        # the reported factor is rounded to 6 significant digits
        self.assertAlmostEqual(v["norris_landzberg"]["acceleration_factor"]
                               / want, 1.0, places=5)

    def test_af_of_identical_conditions_is_one(self):
        v = self._run(test=self.TEST, use=self.TEST)["value"]
        self.assertAlmostEqual(
            v["norris_landzberg"]["acceleration_factor"], 1.0, places=12)
        self.assertAlmostEqual(
            v["norris_landzberg"]["self_check_af_identity"], 0.0, places=12)

    def test_the_factor_composes(self):
        A = {"delta_t_k": 200.0, "frequency_cpd": 6.0, "t_max_c": 150.0}
        B = {"delta_t_k": 120.0, "frequency_cpd": 2.0, "t_max_c": 85.0}
        C = {"delta_t_k": 60.0, "frequency_cpd": 1.0, "t_max_c": 40.0}
        af_ab = R._nl_af(A, B, R.NL_M, R.NL_N, R.NL_EA_EV)
        af_bc = R._nl_af(B, C, R.NL_M, R.NL_N, R.NL_EA_EV)
        af_ac = R._nl_af(A, C, R.NL_M, R.NL_N, R.NL_EA_EV)
        self.assertAlmostEqual(af_ab * af_bc / af_ac, 1.0, places=9)

    def test_a_harsher_test_gives_a_factor_above_one(self):
        v = self._run()["value"]
        self.assertGreater(v["norris_landzberg"]["acceleration_factor"], 1.0)
        self.assertGreater(
            v["norris_landzberg"]["cycles_to_failure_equivalent_at_use"],
            v["norris_landzberg"]["cycles_to_failure_at_test"])

    def test_half_a_pair_is_refused(self):
        r = R.solder_joint_fatigue(joint_height_mm=0.5, dnp_mm=10.0,
                                   alpha_component_ppm=6.0,
                                   alpha_board_ppm=16.0, delta_t_k=100.0,
                                   nl_test=self.TEST)
        self.assertEqual(r["status"], "error")
        self.assertEqual(r["reason_code"], "ARG_REQUIRED")


class TestDrop(unittest.TestCase):
    A, B, H = 100.0, 80.0, 1.6
    PULSE = {"type": "half_sine", "amplitude_g": 1500.0, "duration_ms": 0.5}

    def _run(self, **kw):
        args = dict(a_mm=self.A, b_mm=self.B, thickness_mm=self.H,
                    pulse=self.PULSE, material="fr4", mount="corners",
                    q_factor=15.0)
        args.update(kw)
        return R.drop_board_strain(**args)

    def test_the_chain_reproduces_its_three_pieces(self):
        r = self._run()
        self.assertEqual(r["status"], "ok")
        v = r["value"]
        # 1. the frequency is the plate's own first mode
        pm = V.plate_modes(self.A, self.B, self.H, material="fr4",
                           mount="corners", n_modes=2)
        self.assertAlmostEqual(v["freq_hz"], pm["value"]["modes"][0]["freq_hz"],
                               places=6)
        # 2. the deflection is the SDOF answer to the same pulse
        sh = V.shock_sdof(v["freq_hz"], q_factor=15.0, pulse=self.PULSE)
        self.assertAlmostEqual(v["peak_deflection_mm"],
                               sh["value"]["peak_relative_displacement_mm"],
                               places=6)
        # 3. the strain is what plate_stress makes of that deflection
        ps = V.plate_stress(self.A, self.B, self.H,
                            amplitude_mm=v["peak_deflection_mm"], mode=1,
                            material="fr4", mount="corners")
        self.assertAlmostEqual(v["surface_strain_ue"],
                               ps["value"]["surface_strain_ue"], places=6)

    def test_no_critical_strain_means_no_verdict(self):
        v = self._run()["value"]
        self.assertFalse(v["judged"])
        self.assertIsNone(v["verdict"])
        self.assertTrue(any("no critical_strain_ue" in w for w in v["warnings"]))

    def test_a_critical_strain_turns_it_into_a_verdict(self):
        v = self._run(critical_strain_ue=1000.0)["value"]
        self.assertTrue(v["judged"])
        self.assertEqual(v["verdict"], "pass" if v["surface_strain_ue"] <= 1000.0
                         else "fail")
        self.assertAlmostEqual(v["strain_margin"] * v["surface_strain_ue"], 1000.0,
                               places=3)

    def test_the_joint_strain_is_eps_times_dnp_over_h(self):
        v = self._run(dnp_mm=5.0, joint_height_mm=0.5)["value"]
        want = v["surface_strain_ue"] * 1e-6 * 5.0 / 0.5
        self.assertAlmostEqual(v["joint"]["shear_strain"] / want, 1.0, places=9)

    def test_half_a_joint_geometry_is_refused(self):
        r = self._run(dnp_mm=5.0)
        self.assertEqual(r["status"], "error")
        self.assertEqual(r["reason_code"], "ARG_REQUIRED")

    def test_damping_is_required(self):
        r = R.drop_board_strain(self.A, self.B, self.H, self.PULSE,
                                material="fr4", mount="corners")
        self.assertEqual(r["status"], "error")
        self.assertEqual(r["reason_code"], "ARG_REQUIRED")

    def test_a_higher_q_means_more_strain(self):
        lo = self._run(q_factor=10.0)["value"]["surface_strain_ue"]
        hi = self._run(q_factor=50.0)["value"]["surface_strain_ue"]
        self.assertGreater(hi, lo)


class TestShockSpectrum(unittest.TestCase):
    STEP = {"type": "step", "amplitude_g": 30.0}
    HALF = {"type": "half_sine", "amplitude_g": 1500.0, "duration_ms": 0.5}

    def test_a_step_at_near_zero_damping_is_exactly_2x(self):
        r = R.shock_spectrum(self.STEP, f_n_hz=[5.0, 50.0, 500.0], zeta=1e-9)
        for p in r["value"]["points"]:
            self.assertAlmostEqual(p["zpa_g"] / 30.0, 2.0, places=4)

    def test_no_point_exceeds_the_undamped_bound(self):
        r = R.shock_spectrum(self.HALF, f_start_hz=20.0, f_stop_hz=5000.0,
                             n_points=16)
        v = r["value"]
        self.assertTrue(v["self_check"]["undamped_bound_respected"])
        for p in v["points"]:
            self.assertLessEqual(p["zpa_g"], 2.0 * 1500.0 * 1.02)

    def test_the_low_frequency_plateau_is_the_velocity_change(self):
        # a very soft oscillator sees the pulse as a pure velocity change, so the
        # pseudo velocity flattens at dV = a0 * 2 t_d / pi (half-sine)
        import math
        r = R.shock_spectrum(self.HALF, f_start_hz=5.0, f_stop_hz=20.0,
                             n_points=4)
        dv = 1500.0 * 9.80665 * 2.0 * 0.5e-3 / math.pi * 1000.0  # mm/s
        pv = [p["pv_mm_s"] for p in r["value"]["points"]]
        # flat to three digits across the plateau ...
        self.assertAlmostEqual(pv[0] / pv[-1], 1.0, places=2)
        # ... and below dV by the damping the oscillator actually has: a 5 %
        # impulse response peaks at exp(-zeta*pi/2) = 0.925 of the undamped one
        self.assertLess(pv[0], dv)
        self.assertAlmostEqual(pv[0] / dv, math.exp(-0.05 * math.pi / 2),
                               places=2)

    def test_the_sweep_is_logarithmic(self):
        r = R.shock_spectrum(self.HALF, f_start_hz=100.0, f_stop_hz=1000.0,
                             n_points=3)
        f = [p["freq_hz"] for p in r["value"]["points"]]
        self.assertAlmostEqual(f[1] / f[0], f[2] / f[1], places=3)

    def test_the_damping_default_is_declared(self):
        r = R.shock_spectrum(self.HALF, f_n_hz=[100.0])
        d = r["value"]["damping"]
        self.assertTrue(d["defaulted"])
        self.assertAlmostEqual(d["zeta"], 0.05, places=12)
        self.assertIn("convention", d)

    def test_a_bad_range_is_refused(self):
        r = R.shock_spectrum(self.HALF, f_start_hz=1000.0, f_stop_hz=100.0)
        self.assertEqual(r["status"], "error")
        self.assertEqual(r["reason_code"], "ARG_RANGE")


class TestHonesty(unittest.TestCase):
    def test_every_envelope_names_its_fits(self):
        r = R.solder_joint_fatigue(joint_height_mm=0.5, dnp_mm=10.0,
                                   alpha_component_ppm=6.0,
                                   alpha_board_ppm=16.0, delta_t_k=100.0)
        a = r["assumptions"]
        self.assertIn("ductility", a)
        self.assertIn("0.325", a["ductility"])
        self.assertIn("underfill", " ".join(a["not_modelled"]))
        self.assertTrue(r["basis"])
        self.assertTrue(r["precision"])

    def test_the_defaulted_dwell_and_temperature_are_called_out(self):
        r = R.solder_joint_fatigue(joint_height_mm=0.5, dnp_mm=10.0,
                                   alpha_component_ppm=6.0,
                                   alpha_board_ppm=16.0, delta_t_k=100.0)
        self.assertIn("defaulted", r["assumptions"])
        self.assertIn("dwell_min", r["assumptions"]["defaulted"])

    def test_the_drop_envelope_says_what_it_leaves_out(self):
        r = R.drop_board_strain(100.0, 80.0, 1.6,
                                {"type": "half_sine", "amplitude_g": 1500.0,
                                 "duration_ms": 0.5},
                                material="fr4", mount="corners", q_factor=15.0)
        a = r["assumptions"]
        self.assertIn("mass", a)
        self.assertIn("single_mode", a)
        self.assertIn("pulse", a)

    def test_a_rigid_body_mode_is_refused_by_name(self):
        r = R.drop_board_strain(100.0, 80.0, 1.6,
                                {"type": "half_sine", "amplitude_g": 1500.0,
                                 "duration_ms": 0.5},
                                material="fr4", mount="free", q_factor=15.0,
                                mode=1)
        self.assertEqual(r["status"], "error")
        self.assertEqual(r["reason_code"], "ARG_RANGE")
        self.assertIn("rigid-body", r["message"])

    def test_the_spread_of_a_preset_is_not_hidden(self):
        r = R.drop_board_strain(100.0, 80.0, 1.6,
                                {"type": "half_sine", "amplitude_g": 1500.0,
                                 "duration_ms": 0.5},
                                material="fr4", mount="corners", q_factor=15.0)
        joined = " ".join(r["value"]["warnings"])
        self.assertIn("18..30 GPa", joined)


if __name__ == "__main__":
    unittest.main()
