"""Plate stress: checked against the exact mode, not against itself.

For a rectangular plate simply supported on all four edges the mode (1,1) IS

    w(x, y) = A sin(pi x / a) sin(pi y / b)

so every curvature below has a textbook value:

    w_xx = -(pi/a)^2 w,  w_yy = -(pi/b)^2 w,  w_xy = (pi/a)(pi/b) A cos cos
    sigma_x = -E z/(1-nu^2) (w_xx + nu w_yy)     z = h/2
    sigma_y = -E z/(1-nu^2) (w_yy + nu w_xx)
    tau_xy  = -E z/(1+nu) w_xy

Two facts that the code got wrong at first and that these tests pin down:

  * The largest von Mises stress of that exact mode is at a CORNER, not at the
    centre: w_xx and w_yy vanish there, but w_xy peaks and tau_xy with it (the
    classic corner torque of a plate). vm(corner) = sqrt(3)*|tau|.
  * The largest PRINCIPAL stress is at the centre. One point cannot answer both
    questions, so the tool reports two points; feeding a vm-point tensor into
    "sigma_max" understates the peak principal stress by ~36 % on this plate.

Everything else here is a refusal: no amplitude, a rigid-body mode, a negative
amplitude, and a corner-mounted plate whose ladder collapses (which must say
"unknown", not "small").
"""
import math
import unittest

from pcb_agent_mcp.calc import vibration as vib

A_MM, B_MM, H_MM = 100.0, 80.0, 1.6
AMP_MM = 0.2
# the fr4 preset, spelled out, so the closed form does not drift with the table
E = 24.0e9
NU = 0.15
RHO = 1850.0
TAU = 2.0 * math.pi


def _D():
    return E * (H_MM * 1e-3) ** 3 / (12.0 * (1.0 - NU * NU))


def _curv_centre():
    """(w_xx, w_yy) at the centre of mode (1,1), 1/m."""
    return (-(math.pi / (A_MM * 1e-3)) ** 2 * (AMP_MM * 1e-3),
            -(math.pi / (B_MM * 1e-3)) ** 2 * (AMP_MM * 1e-3))


def _sigma_from_curv(kx, ky, kxy=0.0):
    z = H_MM * 1e-3 / 2.0
    ez = E * z / (1.0 - NU * NU)
    sx = -ez * (kx + NU * ky)
    sy = -ez * (ky + NU * kx)
    t = -E * z / (1.0 + NU) * kxy
    mid = 0.5 * (sx + sy)
    rr = math.sqrt(0.25 * (sx - sy) ** 2 + t * t)
    hi, lo = mid + rr, mid - rr
    vm = math.sqrt(hi * hi - hi * lo + lo * lo)
    return sx, sy, t, hi, lo, vm


class TestStressMatchesTheExactMode(unittest.TestCase):

    def test_frequency_matches_the_navier_eigenvalue(self):
        # f11 = (pi/2) sqrt(D/(rho h)) ((1/a)^2 + (1/b)^2)
        want = (math.pi / 2.0) * math.sqrt(
            _D() / (RHO * H_MM * 1e-3)) * (
            (1.0 / (A_MM * 1e-3)) ** 2 + (1.0 / (B_MM * 1e-3)) ** 2)
        r = vib.plate_modes(A_MM, B_MM, H_MM, material="fr4", mount="ssss",
                            n_modes=1, poly_degree=9)
        self.assertEqual(r["status"], "ok")
        got = r["value"]["modes"][0]["freq_hz"]
        self.assertAlmostEqual(got / want, 1.0, delta=0.01)

    def test_curvature_at_the_centre_matches_the_sine(self):
        r = vib.plate_modes(A_MM, B_MM, H_MM, material="fr4", mount="ssss",
                            n_modes=1, poly_degree=9, stress_amplitude_mm=AMP_MM)
        st = r["value"]["modes"][0]["stress"]
        self.assertEqual(st["at_mm"], [50.0, 40.0])
        want_kx, want_ky = _curv_centre()
        got_kx, got_ky = st["curvature_per_m"]
        self.assertAlmostEqual(got_kx / want_kx, 1.0, delta=0.005)
        self.assertAlmostEqual(got_ky / want_ky, 1.0, delta=0.005)

    def test_sigma_at_the_centre_matches_the_sine(self):
        r = vib.plate_modes(A_MM, B_MM, H_MM, material="fr4", mount="ssss",
                            n_modes=1, poly_degree=9, stress_amplitude_mm=AMP_MM)
        st = r["value"]["modes"][0]["stress"]
        kx, ky = _curv_centre()
        sx, sy, _t, hi, _lo, _vm = _sigma_from_curv(kx, ky)
        self.assertAlmostEqual(st["sigma_x_mpa"] / (sx / 1e6), 1.0, delta=0.005)
        self.assertAlmostEqual(st["sigma_y_mpa"] / (sy / 1e6), 1.0, delta=0.005)
        self.assertAlmostEqual(st["sigma_max_mpa"] / (hi / 1e6), 1.0, delta=0.005)
        # tau is zero on the symmetry axes
        self.assertLess(abs(st["tau_xy_mpa"]), 1e-6)

    def test_von_mises_peaks_at_a_corner_through_the_twisting_curvature(self):
        # exact corner: sigma_x = sigma_y = 0, tau = E z/(1+nu) (pi/a)(pi/b) A
        kxy = (math.pi / (A_MM * 1e-3)) * (math.pi / (B_MM * 1e-3)) * (AMP_MM * 1e-3)
        _sx, _sy, t, _hi, _lo, vm = _sigma_from_curv(0.0, 0.0, kxy)
        self.assertAlmostEqual(vm / abs(t), math.sqrt(3.0), delta=1e-9)
        r = vib.plate_modes(A_MM, B_MM, H_MM, material="fr4", mount="ssss",
                            n_modes=1, poly_degree=9, stress_amplitude_mm=AMP_MM)
        st = r["value"]["modes"][0]["stress"]
        self.assertAlmostEqual(st["tau_xy_max_mpa"] / (abs(t) / 1e6), 1.0, delta=0.02)
        self.assertAlmostEqual(st["von_mises_mpa"] / (vm / 1e6), 1.0, delta=0.02)
        # and it is a corner, not the middle
        x, y = st["von_mises_at_mm"]
        self.assertIn(x, (0.0, 100.0))
        self.assertIn(y, (0.0, 80.0))


class TestTheTwoPeaksAreDifferentPoints(unittest.TestCase):

    def test_sigma_max_is_not_taken_at_the_von_mises_point(self):
        v = vib.plate_stress(A_MM, B_MM, H_MM, AMP_MM, material="fr4",
                             mount="ssss")["value"]
        self.assertNotEqual(v["sigma_max_at_mm"], v["von_mises_at_mm"])
        self.assertEqual(v["sigma_max_at_mm"], [50.0, 40.0])
        # the old single-point behaviour reported 4.1195 MPa here; the real peak
        # principal stress is ~6.5 MPa. The two must not be confusable.
        self.assertGreater(v["sigma_max_mpa"], 1.5 * v["tau_xy_max_mpa"])
        self.assertAlmostEqual(v["sigma_max_mpa"], v["sigma_1_mpa"], places=6)

    def test_the_note_says_why_the_points_differ(self):
        v = vib.plate_stress(A_MM, B_MM, H_MM, AMP_MM, material="fr4",
                             mount="ssss")["value"]
        self.assertIn("different points", v["peak_locations_note"])
        self.assertIn("tau_xy_max_at_mm", v)


class TestLinearInAmplitude(unittest.TestCase):

    def test_doubling_the_amplitude_doubles_every_stress(self):
        one = vib.plate_stress(A_MM, B_MM, H_MM, AMP_MM, material="fr4",
                               mount="ssss")["value"]
        two = vib.plate_stress(A_MM, B_MM, H_MM, 2 * AMP_MM, material="fr4",
                               mount="ssss")["value"]
        for k in ("von_mises_mpa", "sigma_max_mpa", "tau_xy_max_mpa"):
            self.assertAlmostEqual(two[k] / one[k], 2.0, delta=1e-3)

    def test_amplitude_over_half_the_thickness_is_flagged(self):
        v = vib.plate_stress(A_MM, B_MM, H_MM, 0.5 * H_MM + 0.01, material="fr4",
                             mount="ssss")["value"]
        self.assertTrue(any("OVERSTATES" in w for w in v["warnings"]))
        quiet = vib.plate_stress(A_MM, B_MM, H_MM, AMP_MM, material="fr4",
                                 mount="ssss")["value"]
        self.assertFalse(any("OVERSTATES" in w for w in quiet["warnings"]))


class TestTheLadderIsTheErrorBar(unittest.TestCase):

    def test_ssss_converges_and_says_so(self):
        v = vib.plate_stress(A_MM, B_MM, H_MM, AMP_MM, material="fr4",
                             mount="ssss")["value"]
        self.assertEqual(len(v["degree_ladder"]), 3)
        self.assertEqual([r["poly_degree"] for r in v["degree_ladder"]], [5, 7, 9])
        self.assertTrue(v["converged"])
        self.assertLess(v["degree_sensitivity_pct"], 1.0)

    def test_curvature_converges_more_slowly_than_frequency(self):
        # this is the whole reason the ladder exists: on the same rungs the
        # frequency barely moves while the stress moves several times more
        v = vib.plate_stress(A_MM, B_MM, H_MM, AMP_MM, material="fr4",
                             mount="ssss")["value"]
        lo, hi = v["degree_ladder"][0], v["degree_ladder"][-1]
        df = abs(hi["freq_hz"] - lo["freq_hz"]) / hi["freq_hz"] * 100.0
        ds = abs(hi["sigma_max_mpa"] - lo["sigma_max_mpa"]) / hi["sigma_max_mpa"] * 100.0
        self.assertLess(df, 0.1)
        self.assertGreater(ds, 4.0 * df)

    def test_poly_degree_caps_the_ladder_instead_of_replacing_it(self):
        # a cap must not silently cost the caller their error bar
        v = vib.plate_stress(A_MM, B_MM, H_MM, AMP_MM, material="fr4",
                             mount="ssss", poly_degree=7)["value"]
        self.assertEqual([r["poly_degree"] for r in v["degree_ladder"]], [5, 7])
        self.assertIsNotNone(v["degree_sensitivity_pct"])

    def test_corner_mount_reports_unknown_not_small(self):
        v = vib.plate_stress(A_MM, B_MM, H_MM, AMP_MM, material="fr4",
                             mount="corners")["value"]
        self.assertEqual(len(v["degree_ladder"]), 1)
        self.assertIsNone(v["converged"])
        self.assertIsNone(v["degree_sensitivity_pct"])
        self.assertTrue(any("NO measured error bar" in w for w in v["warnings"]))


class TestRefusals(unittest.TestCase):

    def test_no_amplitude_is_no_stress(self):
        r = vib.plate_stress(A_MM, B_MM, H_MM, None, material="fr4", mount="ssss")
        self.assertEqual(r["status"], "error")
        self.assertEqual(r["reason_code"], "ARG_REQUIRED")
        # and plate_modes without an amplitude must not invent a stress stanza
        pm = vib.plate_modes(A_MM, B_MM, H_MM, material="fr4", mount="ssss",
                             n_modes=1)
        self.assertNotIn("stress", pm["value"]["modes"][0])

    def test_negative_amplitude_is_refused(self):
        r = vib.plate_stress(A_MM, B_MM, H_MM, -1.0, material="fr4", mount="ssss")
        self.assertEqual(r["status"], "error")
        self.assertEqual(r["reason_code"], "ARG_REQUIRED")

    def test_rigid_body_mode_has_no_curvature(self):
        r = vib.plate_stress(A_MM, B_MM, H_MM, AMP_MM, material="fr4", mount="free")
        self.assertEqual(r["status"], "error")
        self.assertEqual(r["reason_code"], "ARG_RANGE")
        self.assertIn("rigid-body", r["message"])

    def test_bad_poly_degree_is_refused(self):
        r = vib.plate_stress(A_MM, B_MM, H_MM, AMP_MM, material="fr4",
                             mount="ssss", poly_degree=2)
        self.assertEqual(r["reason_code"], "ARG_INVALID")


class TestEnvelopeHonesty(unittest.TestCase):

    def test_the_not_computed_list_names_the_real_failure_modes(self):
        v = vib.plate_stress(A_MM, B_MM, H_MM, AMP_MM, material="fr4",
                             mount="ssss")["value"]
        blob = " ".join(v["stress_not_computed"]).lower()
        self.assertGreaterEqual(len(v["stress_not_computed"]), 6)
        for word in ("solder-joint", "interlaminar", "stress concentrations",
                     "geometric nonlinearity", "residual stress", "amplitude"):
            self.assertIn(word, blob)

    def test_envelope_says_what_was_modelled(self):
        r = vib.plate_stress(A_MM, B_MM, H_MM, AMP_MM, material="fr4",
                             mount="ssss")
        blob = " ".join(
            list(r["assumptions"].values()) + list(r["assumptions"].keys())
            + [r["model"], r["precision"], str(r["basis"])]).lower()
        self.assertIn("caller", blob)          # the amplitude is not invented
        self.assertIn("degree ladder", blob)   # the error bar is measured
        self.assertIn("mpa", r["unit"].lower())


class TestToolSurface(unittest.TestCase):

    def test_registered_with_the_ladder_knobs(self):
        from pcb_agent_mcp import tools
        self.assertIn("vibration_plate_stress", tools.TOOLS)
        spec = tools.TOOLS["vibration_plate_stress"]
        self.assertIn("amplitude_mm", spec["inputSchema"]["required"])
        self.assertIn("Rayleigh-Ritz", spec["description"])

    def test_handler_returns_the_two_points(self):
        from pcb_agent_mcp import tools
        h = tools.TOOLS["vibration_plate_stress"]["handler"]
        r = h({"a_mm": A_MM, "b_mm": B_MM, "thickness_mm": H_MM,
               "amplitude_mm": AMP_MM, "material": "fr4", "mount": "ssss"})
        self.assertEqual(r["status"], "ok")
        self.assertEqual(r["value"]["sigma_max_at_mm"], [50.0, 40.0])
        self.assertNotEqual(r["value"]["sigma_max_at_mm"],
                            r["value"]["von_mises_at_mm"])

    def test_plate_modes_tool_forwards_the_amplitude(self):
        from pcb_agent_mcp import tools
        h = tools.TOOLS["vibration_plate_modes"]["handler"]
        r = h({"a_mm": A_MM, "b_mm": B_MM, "thickness_mm": H_MM,
               "material": "fr4", "mount": "ssss", "n_modes": 1,
               "stress_amplitude_mm": AMP_MM})
        self.assertEqual(r["status"], "ok")
        self.assertIn("stress", r["value"]["modes"][0])


if __name__ == "__main__":
    unittest.main()
