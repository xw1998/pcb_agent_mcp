"""Tests for the vibration layer: frequency, response, fatigue, shock.

The point of this file is that NONE of the expected numbers come from a run of
the code being tested. Every one is either

  * a closed form from plate theory   (SSSS and CCCC eigenvalues, the beam
    limit, Miles' equation, the step-pulse DLF), or
  * a published coefficient the code is checked AGAINST (Leissa's lambda^2 for a
    free square: 13.489 at nu=0.3), or
  * hand arithmetic from the fixture geometry.

That is the only way a Ritz assembly can be caught getting a sign, a transpose
or a factor of 2 wrong - the three mistakes already found while building it.
"""
import math
import os
import sys
import unittest

sys.path.insert(0, os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "src"))

from pcb_agent_mcp.calc import vibration as V  # noqa: E402


# A 100 x 100 x 1.0 mm FR-4 plate, so every expected value below can be
# recomputed from these four lines.
A_MM = 100.0
H_MM = 1.0
E = 24e9
NU = 0.15
RHO = 1850.0


def _k():
    """sqrt(D / rho h) in m^2/s."""
    D = E * (H_MM * 1e-3) ** 3 / (12.0 * (1.0 - NU ** 2))
    return math.sqrt(D / (RHO * H_MM * 1e-3))


def _plate(mount, n=1, **kw):
    args = dict(a_mm=A_MM, b_mm=A_MM, thickness_mm=H_MM, e_gpa=E / 1e9,
                nu=NU, rho_kg_m3=RHO, mount=mount, n_modes=n)
    args.update(kw)
    r = V.plate_modes(**args)
    assert r["status"] == "ok", r
    return r["value"]


class PlateEigenvalues(unittest.TestCase):
    """f_mn = (pi/2) * (m^2/a^2 + n^2/b^2) * sqrt(D/rho h) for SSSS."""

    def test_simply_supported_square_matches_the_closed_form(self):
        f11 = math.pi / 2.0 * 2.0 / (A_MM * 1e-3) ** 2 * _k()
        got = _plate("edges")["modes"][0]["freq_hz"]
        self.assertAlmostEqual(got, f11, delta=f11 * 1e-5)

    def test_simply_supported_2_to_1_plate_matches_the_closed_form(self):
        a, b = 200.0, 100.0
        f11 = math.pi / 2.0 * (1.0 / (a * 1e-3) ** 2 + 1.0 / (b * 1e-3) ** 2) * _k()
        r = V.plate_modes(a, b, H_MM, e_gpa=E / 1e9, nu=NU, rho_kg_m3=RHO,
                          mount="edges", n_modes=1)
        self.assertAlmostEqual(r["value"]["modes"][0]["freq_hz"], f11,
                               delta=f11 * 1e-5)

    def test_clamped_square_matches_leissa_35_99(self):
        f11 = 35.99 / (2.0 * math.pi) * _k() / (A_MM * 1e-3) ** 2
        got = _plate("clamped")["modes"][0]["freq_hz"]
        self.assertAlmostEqual(got, f11, delta=f11 * 1e-4)

    def test_clamped_is_stiffer_than_simply_supported(self):
        self.assertGreater(_plate("clamped")["modes"][0]["freq_hz"],
                           _plate("edges")["modes"][0]["freq_hz"] * 1.5)

    def test_free_square_matches_leissa_13_489_at_nu_0_3(self):
        # Leissa, "Vibration of Plates" (NASA SP-160): free square, nu = 0.3,
        # lambda^2 = 13.489 for the first elastic mode. The sign of this check
        # matters: getting the mixed second-derivative index transposed makes
        # the free-free case the only one that goes wrong, and it goes wrong by
        # producing NEGATIVE eigenvalues.
        nu = 0.3
        D = E * (H_MM * 1e-3) ** 3 / (12.0 * (1.0 - nu ** 2))
        k = math.sqrt(D / (RHO * H_MM * 1e-3))
        r = V.plate_modes(A_MM, A_MM, H_MM, e_gpa=E / 1e9, nu=nu,
                          rho_kg_m3=RHO, mount="free", n_modes=4)
        v = r["value"]
        self.assertEqual(v["rigid_body_modes"], [1, 2, 3])
        for m in v["modes"][:3]:
            self.assertEqual(m["freq_hz"], 0.0)
            self.assertTrue(m["rigid_body"])
            self.assertNotIn("antinode_mm", m)
        f_first = v["modes"][3]["freq_hz"]
        lam2 = 2.0 * math.pi * f_first * (A_MM * 1e-3) ** 2 / k
        self.assertAlmostEqual(lam2, 13.489, delta=13.489 * 0.005)

    def test_no_negative_eigenvalue_in_any_mount(self):
        for mount in ("edges", "clamped", "free", "cantilever", "corners"):
            for m in _plate(mount, n=3)["modes"]:
                self.assertGreaterEqual(m["lambda"], -1e-6, mount)

    def test_thicker_plate_is_stiffer_and_a_longer_one_softer(self):
        base = _plate("edges")["modes"][0]["freq_hz"]
        thick = V.plate_modes(A_MM, A_MM, 2.0 * H_MM, e_gpa=E / 1e9, nu=NU,
                              rho_kg_m3=RHO, mount="edges", n_modes=1)
        # f ~ h for a plate whose density does not change: 2x thickness = 2x f
        self.assertAlmostEqual(thick["value"]["modes"][0]["freq_hz"], 2.0 * base,
                              delta=base * 1e-4)
        long_ = V.plate_modes(2.0 * A_MM, 2.0 * A_MM, H_MM, e_gpa=E / 1e9, nu=NU,
                              rho_kg_m3=RHO, mount="edges", n_modes=1)
        self.assertAlmostEqual(long_["value"]["modes"][0]["freq_hz"], base / 4.0,
                              delta=base * 1e-4)

    def test_stiffer_material_goes_as_sqrt_e(self):
        base = _plate("edges")["modes"][0]["freq_hz"]
        stiff = V.plate_modes(A_MM, A_MM, H_MM, e_gpa=4.0 * E / 1e9, nu=NU,
                              rho_kg_m3=RHO, mount="edges", n_modes=1)
        self.assertAlmostEqual(stiff["value"]["modes"][0]["freq_hz"], 2.0 * base,
                               delta=base * 1e-4)


class PlateModeShapes(unittest.TestCase):
    """The eigenvalues could be right with the eigenvectors mixed up."""

    def test_first_mode_of_a_simply_supported_plate_peaks_at_the_centre(self):
        v = _plate("edges", n=1)
        m = v["modes"][0]
        self.assertAlmostEqual(m["antinode_mm"][0], 50.0, delta=3.0)
        self.assertAlmostEqual(m["antinode_mm"][1], 50.0, delta=3.0)

    def test_probe_values_follow_sin_sin(self):
        # w = sin(pi x/a) sin(pi y/b) for the (1,1) SSSS mode
        probes = [{"name": "centre", "x_mm": 50.0, "y_mm": 50.0},
                  {"name": "y5", "x_mm": 50.0, "y_mm": 5.0},
                  {"name": "x5y5", "x_mm": 5.0, "y_mm": 5.0}]
        v = V.plate_modes(A_MM, A_MM, H_MM, e_gpa=E / 1e9, nu=NU, rho_kg_m3=RHO,
                          mount="edges", n_modes=1, probe_points=probes)["value"]
        d = {p["name"]: p["normalised_deflection"]
             for p in v["modes"][0]["deflection_at_probes"]}
        self.assertAlmostEqual(d["centre"], 1.0, delta=1e-3)
        self.assertAlmostEqual(d["y5"], math.sin(math.pi * 0.05), delta=5e-3)
        self.assertAlmostEqual(d["x5y5"], math.sin(math.pi * 0.05) ** 2, delta=5e-3)

    def test_the_square_has_a_degenerate_second_pair(self):
        # (1,2) and (2,1) share a frequency; the eigensolver may return any
        # linear combination of them, so no probe value on those two modes is
        # meaningful - only the frequency is. Checking the degeneracy itself
        # catches a basis that breaks the symmetry of the plate.
        v = _plate("edges", n=3)
        self.assertAlmostEqual(v["modes"][1]["freq_hz"], v["modes"][2]["freq_hz"],
                               delta=v["modes"][1]["freq_hz"] * 1e-4)
        f21 = math.pi / 2.0 * (1.0 + 4.0) / (A_MM * 1e-3) ** 2 * _k()
        self.assertAlmostEqual(v["modes"][1]["freq_hz"], f21, delta=f21 * 1e-4)

    def test_cantilever_antinode_is_on_the_free_edge(self):
        m = _plate("cantilever")["modes"][0]
        self.assertAlmostEqual(m["antinode_mm"][0], 100.0, delta=6.0)


class PlateRefusals(unittest.TestCase):
    def test_unknown_material_is_refused_with_the_known_list(self):
        r = V.plate_modes(100, 100, 1.0, material="unobtainium")
        self.assertEqual(r["status"], "error")
        self.assertEqual(r["reason_code"], "MATERIAL_UNKNOWN")
        self.assertIn("fr4", r["hint"])

    def test_no_material_at_all_is_refused_not_defaulted(self):
        r = V.plate_modes(100, 100, 1.0)
        self.assertEqual(r["status"], "error")
        self.assertEqual(r["reason_code"], "ARG_REQUIRED")

    def test_negative_thickness_is_refused(self):
        r = V.plate_modes(100, 100, -1.0, material="fr4")
        self.assertEqual(r["status"], "error")
        self.assertEqual(r["reason_code"], "ARG_INVALID")

    def test_unknown_mount_is_refused_with_the_known_list(self):
        r = V.plate_modes(100, 100, 1.0, material="fr4", mount="screwed")
        self.assertEqual(r["status"], "error")
        self.assertEqual(r["reason_code"], "MOUNT_UNKNOWN")

    def test_custom_without_boundaries_is_refused(self):
        r = V.plate_modes(100, 100, 1.0, material="fr4", mount="custom")
        self.assertEqual(r["reason_code"], "ARG_REQUIRED")

    def test_poisson_ratio_outside_a_solid_is_refused(self):
        r = V.plate_modes(100, 100, 1.0, e_gpa=24, nu=0.7, rho_kg_m3=1850)
        self.assertEqual(r["reason_code"], "ARG_INVALID")

    def test_material_preset_says_it_is_not_a_datasheet(self):
        r = V.plate_modes(100, 100, 1.0, material="fr4")
        self.assertIn("not a supplier datasheet", r["assumptions"]["note"])


class Miles(unittest.TestCase):
    def test_flat_psd_matches_the_miles_closed_form(self):
        r = V.miles_response(300.0, psd={"g2_per_hz": 0.1}, q_factor=10.0)
        expected = math.sqrt(math.pi / 2.0 * 300.0 * 10.0 * 0.1)
        self.assertAlmostEqual(r["value"]["g_rms"], expected, delta=expected * 1e-5)

    def test_numerical_integral_agrees_with_the_closed_form(self):
        a = V.miles_response(300.0, psd={"g2_per_hz": 0.1}, q_factor=10.0)["value"]
        b = V.miles_response(300.0, psd={"points": [[10, 0.1], [3000, 0.1]]},
                             q_factor=10.0)["value"]
        self.assertLess(abs(a["g_rms"] - b["g_rms"]) / a["g_rms"], 0.01)

    def test_peak_is_gamma_times_rms(self):
        v = V.miles_response(200.0, psd={"g2_per_hz": 0.05}, q_factor=20.0,
                             gamma=2.0)["value"]
        self.assertAlmostEqual(v["g_peak"], 2.0 * v["g_rms"],
                               delta=v["g_peak"] * 1e-5)

    def test_displacement_follows_from_the_acceleration(self):
        v = V.miles_response(300.0, psd={"g2_per_hz": 0.1}, q_factor=10.0)["value"]
        expected = (v["g_rms"] * 9.80665 / (2 * math.pi * 300.0) ** 2) * 1000.0
        self.assertAlmostEqual(v["displacement_rms_mm"], expected,
                               delta=abs(expected) * 1e-5)

    def test_doubling_the_psd_goes_as_sqrt_w(self):
        a = V.miles_response(100.0, psd={"g2_per_hz": 0.01}, q_factor=10.0)["value"]
        b = V.miles_response(100.0, psd={"g2_per_hz": 0.04}, q_factor=10.0)["value"]
        self.assertAlmostEqual(b["g_rms"] / a["g_rms"], 2.0, delta=1e-5)

    def test_zeta_and_q_are_the_same_input(self):
        a = V.miles_response(100.0, psd={"g2_per_hz": 0.1}, zeta=0.05)["value"]
        b = V.miles_response(100.0, psd={"g2_per_hz": 0.1}, q_factor=10.0)["value"]
        self.assertAlmostEqual(a["g_rms"], b["g_rms"], delta=1e-9)

    def test_no_damping_is_refused_rather_than_dividing_by_zero(self):
        r = V.miles_response(100.0, psd={"g2_per_hz": 0.1})
        self.assertEqual(r["reason_code"], "ARG_REQUIRED")

    def test_empty_psd_is_refused(self):
        self.assertEqual(V.miles_response(100.0, psd={})["reason_code"],
                         "ARG_REQUIRED")


class Steinberg(unittest.TestCase):
    """N_i = N0 (z_allow / delta_i)^b with the three textbook bands."""

    def test_allowable_cycles_are_hand_computable(self):
        v = V.steinberg_fatigue(300.0, 0.5, 1.5)["value"]
        s_rms = 0.5 / 3.0
        for band, mult in zip(v["bands"], (3.0, 2.0, 1.0)):
            expected = 2.0e7 * (1.5 / (mult * s_rms)) ** 6.4
            self.assertAlmostEqual(band["allowable_cycles"], expected,
                                   delta=expected * 1e-5)
            self.assertAlmostEqual(band["amplitude_mm"], mult * s_rms,
                                   delta=mult * s_rms * 1e-5)

    def test_band_fractions_are_the_textbook_ones(self):
        v = V.steinberg_fatigue(300.0, 0.5, 1.5)["value"]
        self.assertAlmostEqual(sum(b["cycle_fraction"] for b in v["bands"]), 1.0,
                               delta=1e-3)

    def test_life_is_the_miner_sum(self):
        v = V.steinberg_fatigue(300.0, 0.5, 1.5)["value"]
        rate = 300.0 * sum(b["cycle_fraction"] / b["allowable_cycles"]
                           for b in v["bands"])
        self.assertAlmostEqual(v["life_hours"], 1.0 / rate / 3600.0,
                               delta=v["life_hours"] * 1e-6)

    def test_more_allowable_deflection_means_longer_life(self):
        a = V.steinberg_fatigue(300.0, 0.5, 1.0)["value"]["life_hours"]
        b = V.steinberg_fatigue(300.0, 0.5, 2.0)["value"]["life_hours"]
        # (2/1)^6.4 = 84.4x: every band moves by the same factor
        self.assertAlmostEqual(b / a, 2.0 ** 6.4, delta=1.0)

    def test_missing_z_allow_is_refused_not_invented(self):
        r = V.steinberg_fatigue(300.0, 0.5)
        self.assertEqual(r["reason_code"], "ARG_REQUIRED")
        self.assertIn("qualification", r["hint"])

    def test_target_hours_gives_a_margin_of_life_over_target(self):
        v = V.steinberg_fatigue(300.0, 0.5, 1.5, target_hours=1000.0)["value"]
        self.assertAlmostEqual(v["margin"], v["life_hours"] / 1000.0,
                               delta=v["margin"] * 1e-6)
        self.assertEqual(v["pass"], v["life_hours"] >= 1000.0)

    def test_weak_exponent_is_refused(self):
        r = V.steinberg_fatigue(300.0, 0.5, 1.5, exponent=1.0)
        self.assertEqual(r["reason_code"], "ARG_INVALID")


class Shock(unittest.TestCase):
    """The step-pulse DLF is 2 for zero damping and 1+exp(-zeta pi/..) otherwise."""

    def test_undamped_step_peak_is_exactly_two(self):
        v = V.shock_sdof(100.0, zeta=1e-4,
                         pulse={"type": "step", "amplitude_g": 10.0})["value"]
        self.assertAlmostEqual(v["displacement_amplification"], 2.0, delta=0.01)

    def test_damped_step_peak_matches_the_analytic_dlf(self):
        for zeta in (0.05, 0.1, 0.2):
            v = V.shock_sdof(100.0, zeta=zeta,
                             pulse={"type": "step", "amplitude_g": 10.0})["value"]
            expected = 1.0 + math.exp(-zeta * math.pi
                                      / math.sqrt(1.0 - zeta * zeta))
            self.assertAlmostEqual(v["displacement_amplification"], expected,
                                   delta=expected * 0.005, msg=str(zeta))

    def test_static_displacement_is_a0_over_omega_squared(self):
        v = V.shock_sdof(100.0, zeta=0.1,
                         pulse={"type": "step", "amplitude_g": 10.0})["value"]
        expected = 10.0 * 9.80665 / (2 * math.pi * 100.0) ** 2 * 1000.0
        self.assertAlmostEqual(v["static_displacement_mm"], expected,
                               delta=expected * 1e-5)

    def test_a_rect_pulse_shorter_than_ten_percent_of_the_period_barely_moves_it(self):
        v = V.shock_sdof(100.0, zeta=0.05,
                         pulse={"type": "rect", "amplitude_g": 10.0,
                                "duration_ms": 0.1})["value"]
        self.assertLess(v["displacement_amplification"], 0.1)

    def test_a_long_pulse_converges_to_the_step_answer(self):
        short = V.shock_sdof(100.0, zeta=0.05,
                             pulse={"type": "rect", "amplitude_g": 10.0,
                                    "duration_ms": 20.0})["value"]
        step = V.shock_sdof(100.0, zeta=0.05,
                            pulse={"type": "step", "amplitude_g": 10.0})["value"]
        self.assertAlmostEqual(short["displacement_amplification"],
                               step["displacement_amplification"], delta=0.02)

    def test_half_sine_peak_acceleration_exceeds_the_input(self):
        v = V.shock_sdof(500.0, zeta=0.05,
                         pulse={"type": "half_sine", "amplitude_g": 30.0,
                                "duration_ms": 2.0})["value"]
        self.assertGreater(v["peak_absolute_acceleration_g"], 30.0)

    def test_unknown_pulse_type_is_refused(self):
        r = V.shock_sdof(100.0, zeta=0.1, pulse={"type": "sawtooth",
                                                "amplitude_g": 10.0})
        self.assertEqual(r["reason_code"], "ARG_INVALID")

    def test_missing_duration_for_a_finite_pulse_is_refused(self):
        r = V.shock_sdof(100.0, zeta=0.1,
                         pulse={"type": "rect", "amplitude_g": 10.0})
        self.assertEqual(r["reason_code"], "ARG_REQUIRED")


class Transmissibility(unittest.TestCase):
    def test_at_resonance_it_equals_q(self):
        v = V.transmissibility(100.0, q_factor=5.0, freq_hz=[100.0])["value"]
        # |T| at r=1 is sqrt(1+(2z)^2)/(2z) = Q sqrt(1+1/Q^2)
        self.assertAlmostEqual(v["points"][0]["transmissibility"],
                               5.0 * math.sqrt(1.0 + 1.0 / 25.0), delta=1e-4)

    def test_isolation_only_starts_above_root_two(self):
        v = V.transmissibility(100.0, q_factor=5.0,
                               freq_hz=[135.0, 145.0])["value"]
        self.assertGreater(v["points"][0]["transmissibility"], 1.0)
        self.assertLess(v["points"][1]["transmissibility"], 1.0)
        self.assertAlmostEqual(v["isolation_onset_hz"], 100.0 * math.sqrt(2.0),
                               delta=0.01)

    def test_high_frequency_rolls_off_as_coefficient_over_r(self):
        # far above resonance the numerator term (2 zeta r)^2 dominates, so the
        # ACCELERATION transmissibility falls as 2 zeta / r, i.e. 1/r, not 1/r^2
        v = V.transmissibility(10.0, zeta=0.05,
                               freq_hz=[1000.0, 2000.0])["value"]
        t1, t2 = (p["transmissibility"] for p in v["points"])
        self.assertAlmostEqual(t2 / t1, 0.5, delta=0.01)

    def test_range_sweep_returns_every_point(self):
        v = V.transmissibility(100.0, q_factor=5.0,
                               freq_range=[10.0, 200.0, 20])["value"]
        self.assertEqual(len(v["points"]), 20)
        self.assertAlmostEqual(v["points"][0]["freq_hz"], 10.0, delta=1e-9)


KICAD_BOARD = """(kicad_pcb (version 20221018) (generator pcbnew)
  (general (thickness 1.6))
  (layers (0 "F.Cu" signal) (31 "B.Cu" signal))
  (setup (pad_to_mask_clearance 0))
  (net 0 "")
  (net 1 "VCC")
  (gr_rect (start 0 0) (end 100 80) (stroke (width 0.1) (type default))
    (fill none) (layer "Edge.Cuts"))
  (footprint "R_0603" (layer "F.Cu") (at 50 40 0) (attr smd)
    (property "Reference" "R1")
    (pad "1" smd rect (at -0.8 0) (size 0.8 0.9) (layers "F.Cu") (net 1 "VCC"))
    (pad "2" smd rect (at 0.8 0) (size 0.8 0.9) (layers "F.Cu") (net 1 "VCC")))
  (footprint "R_0603" (layer "F.Cu") (at 10 40 0) (attr smd)
    (property "Reference" "R2")
    (pad "1" smd rect (at -0.8 0) (size 0.8 0.9) (layers "F.Cu") (net 1 "VCC")))
)
"""


class BoardVibration(unittest.TestCase):
    def test_board_uses_its_own_outline_and_thickness(self):
        from pcb_agent_mcp.adapters import open_board
        from pcb_agent_mcp import oracle
        import tempfile
        path = os.path.join(tempfile.mkdtemp(), "v.kicad_pcb")
        with open(path, "w", encoding="utf-8") as fh:
            fh.write(KICAD_BOARD)
        board = open_board(path)
        r = oracle.board_vibration(board, mount="edges", thickness_mm=1.6)
        self.assertEqual(r["status"], "ok", r)
        v = r["value"]
        self.assertAlmostEqual(v["plate_mm"][0], 100.0, delta=1e-6)
        self.assertAlmostEqual(v["plate_mm"][1], 80.0, delta=1e-6)
        self.assertAlmostEqual(v["outline_fill_fraction_of_bbox"], 1.0, delta=1e-3)
        # R1 sits at the centre of a (1,1) SSSS mode and R2 near the edge
        hot = {h["ref"]: h for h in v["hotspots"]}
        self.assertGreater(abs(hot["R1"]["normalised_deflection"]),
                           abs(hot["R2"]["normalised_deflection"]))
        self.assertTrue(hot["R1"]["near_antinode"])

    def test_a_board_without_an_outline_is_refused(self):
        from pcb_agent_mcp.adapters.canonical import Board
        from pcb_agent_mcp import oracle
        r = oracle.board_vibration(Board("kicad", "x"))
        self.assertEqual(r["reason_code"], "OUTLINE_MISSING")

    def test_unknown_thickness_is_refused_not_defaulted(self):
        from pcb_agent_mcp.adapters.canonical import Board
        from pcb_agent_mcp import oracle
        b = Board("kicad", "x")
        b.outline = [[(0.0, 0.0), (10.0, 0.0), (10.0, 10.0), (0.0, 10.0)]]
        r = oracle.board_vibration(b)
        self.assertEqual(r["reason_code"], "ARG_REQUIRED")


class ToolSurface(unittest.TestCase):
    def test_the_six_vibration_tools_are_registered(self):
        from pcb_agent_mcp import tools
        for name in ("vibration_plate_modes", "vibration_random_response",
                     "vibration_fatigue", "vibration_shock",
                     "vibration_isolator", "board_vibration"):
            self.assertIn(name, tools.TOOLS)

    def test_capabilities_lists_the_vibration_layer_and_its_limits(self):
        from pcb_agent_mcp import tools
        c = tools.call_tool("calc_capabilities", {})
        self.assertIn("vibration", c["layers"])
        self.assertIn("board_vibration", c["available"])
        blob = " ".join(c["not_implemented_yet"])
        for word in ("creep", "drop", "nonlinear"):
            self.assertIn(word, blob)

    def test_board_vibration_is_marked_derived(self):
        from pcb_agent_mcp import tools
        self.assertIn("board_vibration",
                      tools.call_tool("calc_capabilities", {})
                      ["derived_vs_authoritative"]
                      ["derived (this server computed it)"])


if __name__ == "__main__":
    unittest.main()
