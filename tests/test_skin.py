"""Golden tests for ac_sheet_resistance - the exact 1-D skin-effect solution.

The risk with a closed form is that it LOOKS right: every sinh/cosh expression
has the correct limits at both ends for some wrong combination of signs, factors
of two and real/imaginary parts.  So the tests are:

  * the two limits, each tight enough that a mistyped factor cannot pass
    (R -> 1 + 2s^4/15 below, R = X = s above);
  * the anchor value at s = 1, measured here once and pinned to 1e-6;
  * an INDEPENDENT numerical solution - a finite-difference solve of the same
    diffusion equation on 2001 nodes, which agrees to 0.05 % and converges as
    O(dz^2) - so the closed form is checked against arithmetic, not against
    itself;
  * the face identity (two faces at t == one face at t/2), which is a property of
    the model and therefore a regression guard, not evidence;
  * the two numbers that are easy to confuse: 0.8589 (current within one skin
    depth) and 0.632 = 1 - e^-1 (density at one skin depth) - the test asserts
    the first and asserts it is NOT the second;
  * every refusal, because a thickness that is silently assumed is the classic
    way a skin answer becomes wrong (35 um vs 17 um is a factor of four in the
    frequency where it starts to matter).

Every number below was measured before it was asserted (see docs/69).
"""
import cmath
import math
import os
import sys
import unittest

sys.path.insert(0, os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "src"))

from pcb_agent_mcp import tools as T  # noqa: E402
from pcb_agent_mcp.calc import skin as S  # noqa: E402

RHO = 1.724e-5          # ohm*mm, copper at 20 C
MU0 = 4.0e-7 * math.pi


def _fd_z_ratio(s, n=2001):
    """Z/R_dc for the one-face slab by finite differences on E(z) - independent."""
    h = s / (n - 1.0)
    g2 = 2.0j * h * h                     # gamma^2 h^2, gamma^2 = 2j/delta^2
    a = [1.0] * n
    b = [-2.0 - g2] * n
    c = [1.0] * n
    d = [0.0] * n
    b[0] = -1.0 - g2                      # dE/dz = 0 at the far face (ghost node)
    c[n - 2] = 0.0
    d[n - 2] = -1.0
    b[n - 1] = 1.0
    d[n - 1] = 1.0
    cp = [0.0] * n
    dp = [0.0] * n
    cp[0] = c[0] / b[0]
    dp[0] = d[0] / b[0]
    for i in range(1, n):
        m = b[i] - a[i] * cp[i - 1]
        cp[i] = c[i] / m
        dp[i] = (d[i] - a[i] * dp[i - 1]) / m
    e = [0.0] * n
    e[n - 1] = dp[n - 1]
    for i in range(n - 2, -1, -1):
        e[i] = dp[i] - cp[i] * e[i + 1]
    z = 1.0 / (sum(e) / n)
    return z.real, z.imag


class TestTheTwoLimits(unittest.TestCase):
    def test_below_one_skin_depth_it_is_the_series(self):
        # R - 1 = 4s^4/45 and X = 2s^2/3 come from ONE series,
        # u*coth(u) = 1 + u^2/3 - u^4/45 + ..., at u = s*(1+j).  The coefficient
        # was measured at 60 digits (0.0888888888 = 4/45) BEFORE it was written
        # here: a first version of this file claimed 2/15 and the check caught it.
        # (relative tolerances: the next term is O(s^2) away, so the absolute
        # gap grows with s even for a correct coefficient)
        for s, tol in ((0.01, 1e-6), (0.05, 1e-6), (0.1, 1e-4)):
            r, x = S._ratio_from_s(s)
            self.assertLess(abs((r - 1.0) / (4.0 * s ** 4 / 45.0) - 1.0), tol)
            self.assertLess(abs(x / (2.0 * s ** 2 / 3.0) - 1.0), tol)

    def test_the_reactance_is_the_dc_internal_inductance(self):
        # independent anchor: at low frequency X/R_dc -> 2s^2/3, and with
        # s^2 = omega*mu*t^2/(2*rho), R_dc = rho/t, that IS omega*mu*t/3 - the
        # textbook DC internal inductance of a slab.  A wrong factor of two in s
        # (the one/two-face switch) or a swapped real/imaginary part moves it.
        f, t_m, rho_m = 1e3, 35e-6, 1.724e-8
        st = S.slab_surface_impedance(f, t_m * 1e3, rho_m * 1e3)
        r_dc = rho_m / t_m
        x_expected = 2.0 * math.pi * f * MU0 * t_m / 3.0
        self.assertAlmostEqual(st["x_over_rdc"] * r_dc, x_expected, delta=1e-9)

    def test_above_a_few_skin_depths_r_and_x_both_go_to_s(self):
        for s in (5.0, 10.0, 100.0):
            r, x = S._ratio_from_s(s)
            self.assertLess(abs(r / s - 1.0), 1.5e-4)
            self.assertLess(abs(x / s - 1.0), 1.5e-4)
        r, x = S._ratio_from_s(1e6)      # the asymptote branch itself
        self.assertEqual((r, x), (1e6, 1e6))

    def test_the_surface_impedance_is_at_45_degrees_when_thick(self):
        r, x = S._ratio_from_s(50.0)
        self.assertLess(abs(r - x) / r, 1e-9)

    def test_the_anchor_at_one_skin_depth(self):
        # measured here, not quoted: R = 1.0856357048, X = 0.6503925810
        r, x = S._ratio_from_s(1.0)
        self.assertAlmostEqual(r, 1.0856357047503276, delta=1e-9)
        self.assertAlmostEqual(x, 0.6503925810415222, delta=1e-9)
        self.assertGreater(r, 1.0)       # a skin term can only add resistance
        self.assertLess(x, r)            # and at s = 1 it is still mostly resistive


class TestAgainstFiniteDifferences(unittest.TestCase):
    """The closed form versus a discretisation of the same equation."""

    def test_the_curve_matches_a_fd_solve(self):
        for s in (0.2, 0.5, 1.0, 2.0):
            fr, fx = _fd_z_ratio(s)
            cr, cx = S._ratio_from_s(s)
            self.assertLess(abs(fr / cr - 1.0), 1e-3, "s=%g R" % s)
            self.assertLess(abs(fx / cx - 1.0), 1e-2, "s=%g X" % s)

    def test_the_fd_error_halves_when_the_grid_halves(self):
        # O(dz^2) = O(1/n): 0.2015 % -> 0.1003 % measured; the point of this
        # test is that the closed form sits at the limit the FD is converging to,
        # so it cannot itself be a discretisation artefact.
        e1 = abs(_fd_z_ratio(1.0, 501)[0] / S._ratio_from_s(1.0)[0] - 1.0)
        e2 = abs(_fd_z_ratio(1.0, 1001)[0] / S._ratio_from_s(1.0)[0] - 1.0)
        self.assertLess(e2, e1 * 0.6)
        self.assertGreater(e2, e1 * 0.4)
        self.assertLess(e2, 2e-3)


class TestTheFaceRule(unittest.TestCase):
    def test_two_faces_at_t_is_one_face_at_half_t(self):
        for t_um in (35.0, 400.0):
            two = S.ac_sheet_resistance(5e6, thickness_um=t_um,
                                        faces="two")["value"]["r_ac_over_r_dc"]
            one = S.ac_sheet_resistance(5e6, thickness_um=t_um / 2.0,
                                        faces="one")["value"]["r_ac_over_r_dc"]
            self.assertAlmostEqual(two, one, delta=1e-12)

    def test_the_two_face_condition_is_less_than_the_one_face_one(self):
        one = S.ac_sheet_resistance(1e7, thickness_um=35)["value"]["r_ac_over_r_dc"]
        two = S.ac_sheet_resistance(1e7, thickness_um=35,
                                    faces="two")["value"]["r_ac_over_r_dc"]
        self.assertLess(two, one)
        self.assertGreater(two, 1.0)


class TestOneOunceCopper(unittest.TestCase):
    """The numbers a designer actually asks for."""

    def setUp(self):
        self.v = S.ac_sheet_resistance(1e6, thickness_um=35)["value"]

    def test_the_skin_depth_is_66_um_at_1_mhz(self):
        self.assertAlmostEqual(self.v["skin_depth_um"], 66.0820, delta=2e-3)
        # and it scales as 1/sqrt(f)
        v2 = S.ac_sheet_resistance(4e6, thickness_um=35)["value"]
        self.assertAlmostEqual(v2["skin_depth_um"] / self.v["skin_depth_um"], 0.5,
                               delta=1e-12)

    def test_one_ounce_copper_is_still_dc_at_1_mhz_to_under_1_percent(self):
        self.assertAlmostEqual(self.v["r_ac_over_r_dc"], 1.0069737, delta=1e-6)
        self.assertLess(self.v["r_ac_over_r_dc"], 1.01)
        self.assertIn("dc", self.v["regime"])

    def test_the_milestones_are_1p2_and_3p9_and_15p5_mhz(self):
        self.assertAlmostEqual(self.v["f_at_r_1pct_over_dc_hz"], 1.198257156e6,
                               delta=1.0)
        self.assertAlmostEqual(self.v["f_at_r_10pct_over_dc_hz"], 3.864536139e6,
                               delta=1.0)
        self.assertAlmostEqual(self.v["f_at_r_2x_dc_hz"], 15535543.889, delta=1.0)

    def test_at_10_mhz_it_is_54_percent_worse(self):
        v = S.ac_sheet_resistance(1e7, thickness_um=35)["value"]
        self.assertAlmostEqual(v["r_ac_over_r_dc"], 1.5408001, delta=1e-6)
        self.assertAlmostEqual(v["effective_thickness_um"], 35.0 / 1.5408001,
                               delta=1e-6)
        # 1/sqrt(f) beyond a few skin depths: 10 MHz -> 100 MHz is ~3.16x
        v2 = S.ac_sheet_resistance(1e8, thickness_um=35)["value"]
        v3 = S.ac_sheet_resistance(1e9, thickness_um=35)["value"]
        self.assertAlmostEqual(v3["r_ac_over_r_dc"] / v2["r_ac_over_r_dc"],
                               math.sqrt(10.0), delta=0.01)

    def test_the_sheet_resistance_matches_rho_over_effective_thickness(self):
        r_dc = RHO / 0.035
        self.assertAlmostEqual(self.v["dc_sheet_resistance_ohm_per_sq"], r_dc,
                               delta=1e-12)
        self.assertAlmostEqual(self.v["ac_sheet_resistance_ohm_per_sq"],
                               r_dc * self.v["r_ac_over_r_dc"], delta=1e-15)

    def test_a_thicker_conductor_starts_skinning_at_a_LOWER_frequency(self):
        # The counter-intuitive one, measured: f_1pct goes as 1/t^2, so 2 oz (70 um)
        # reaches 1 % at a QUARTER of the frequency of 1 oz, even though at any
        # fixed frequency the thicker copper is the closer to DC.  The two
        # statements are about different comparisons and only one of them is
        # "thicker is better".
        two_oz = S.ac_sheet_resistance(1e6, thickness_um=70)["value"]
        self.assertAlmostEqual(two_oz["f_at_r_1pct_over_dc_hz"],
                               self.v["f_at_r_1pct_over_dc_hz"] / 4.0, delta=2.0)
        # ... and at ONE fixed frequency the thicker copper is the FARTHER from
        # DC (R/R_dc grows like t/delta), because R_ac saturates at rho/(w*delta)
        # while R_dc keeps falling.  Thickness stops paying above the skin regime.
        self.assertGreater(two_oz["r_ac_over_r_dc"], self.v["r_ac_over_r_dc"])


class TestTheProfile(unittest.TestCase):
    def test_it_peaks_at_the_face(self):
        v = S.ac_sheet_resistance(1e7, thickness_um=35, profile_samples=9)["value"]
        j = [row["j_rel"] for row in v["profile"]]
        self.assertAlmostEqual(j[0], 1.0, delta=1e-12)
        self.assertEqual(j[0], max(j))
        for a, b in zip(j, j[1:]):
            self.assertGreater(a, b - 1e-12)   # monotonically inward
        self.assertEqual(len(v["profile"]), 9)
        self.assertAlmostEqual(v["profile"][-1]["depth_um"], 35.0, delta=1e-9)

    def test_at_dc_the_profile_is_flat(self):
        v = S.ac_sheet_resistance(1e3, thickness_um=35)["value"]
        j = [row["j_rel"] for row in v["profile"]]
        self.assertLess(max(j) / min(j) - 1.0, 1e-6)

    def test_the_phase_lags_going_inward(self):
        v = S.ac_sheet_resistance(1e7, thickness_um=35, profile_samples=5)["value"]
        phases = [row["phase_deg"] for row in v["profile"]]
        self.assertAlmostEqual(phases[0], 0.0, delta=1e-12)
        self.assertLess(phases[-1], phases[0])

    def test_the_current_within_one_skin_depth_is_086_not_063(self):
        # 0.8589 is |1 - exp(-(1+j))|, the CURRENT fraction.  0.632 = 1 - 1/e is
        # the DENSITY ratio at one skin depth.  They are different questions and
        # the tool answers the first one.
        frac = S.current_fraction_within(1e8, 0.4, RHO, 0.00661)
        self.assertAlmostEqual(frac, 0.8589, delta=2e-3)
        self.assertGreater(frac - (1.0 - math.exp(-1.0)), 0.2)

    def test_at_dc_the_fraction_up_to_depth_d_is_d_over_t(self):
        for frac_t in (0.1, 0.25, 0.5):
            got = S.current_fraction_within(1e3, 0.035, RHO, 0.035 * frac_t)
            self.assertAlmostEqual(got, frac_t, delta=1e-6)


class TestRoughness(unittest.TestCase):
    def test_rq_raises_the_resistance_and_is_off_by_default(self):
        smooth = S.ac_sheet_resistance(1e10, thickness_um=35)["value"]
        self.assertNotIn("roughness_k", smooth)
        rough = S.ac_sheet_resistance(1e10, thickness_um=35,
                                      roughness_rq_um=1.0)["value"]
        self.assertGreater(rough["roughness_k"], 1.0)
        self.assertGreater(rough["ac_sheet_resistance_rough_ohm_per_sq"],
                           smooth["ac_sheet_resistance_ohm_per_sq"])

    def test_one_skin_depth_of_roughness_costs_60_percent(self):
        # the Hammerstad-Jensen anchor already pinned in the roughness tests:
        # at Rq = delta the factor is 1.60514, so the skin app must carry it
        delta_mm = S.skin_depth_mm(1e9, RHO)
        v = S.ac_sheet_resistance(1e9, thickness_um=35,
                                  roughness_rq_um=delta_mm * 1e3)["value"]
        self.assertAlmostEqual(v["roughness_k"], 1.60514, delta=5e-5)


class TestTemperature(unittest.TestCase):
    def test_hot_copper_has_a_higher_sheet_resistance_and_a_lower_ratio(self):
        cold = S.ac_sheet_resistance(1e7, thickness_um=35, temp_c=20)["value"]
        hot = S.ac_sheet_resistance(1e7, thickness_um=35, temp_c=100)["value"]
        self.assertGreater(hot["dc_sheet_resistance_ohm_per_sq"],
                           cold["dc_sheet_resistance_ohm_per_sq"])
        self.assertLess(hot["r_ac_over_r_dc"], cold["r_ac_over_r_dc"])


class TestAbsoluteResistance(unittest.TestCase):
    def test_a_geometry_and_a_known_dc_resistance_agree(self):
        v = S.ac_sheet_resistance(1e7, thickness_um=35, length_mm=20,
                                  width_mm=2)["value"]
        self.assertAlmostEqual(v["dc_resistance_ohm"], RHO * 20.0 / (2.0 * 0.035),
                               delta=1e-12)
        self.assertAlmostEqual(v["ac_resistance_ohm"],
                               v["dc_resistance_ohm"] * v["r_ac_over_r_dc"],
                               delta=1e-15)

    def test_a_known_dc_resistance_scales_instead(self):
        v = S.ac_sheet_resistance(1e7, thickness_um=35,
                                  dc_resistance_ohm=0.1)["value"]
        self.assertAlmostEqual(v["ac_resistance_ohm"],
                               0.1 * v["r_ac_over_r_dc"], delta=1e-15)


class TestRefusals(unittest.TestCase):
    def _err(self, **kw):
        r = T.call_tool("ac_sheet_resistance", kw)
        self.assertEqual(r.get("status"), "error", r)
        return r

    def test_no_frequency(self):
        self.assertEqual(self._err(thickness_um=35)["reason_code"], "ARG_REQUIRED")

    def test_zero_frequency(self):
        self.assertEqual(self._err(frequency_hz=0, thickness_um=35)["reason_code"],
                         "BAD_PARAMETER")

    def test_no_thickness_or_both(self):
        self.assertEqual(self._err(frequency_hz=1e6)["reason_code"], "ARG_REQUIRED")
        self.assertEqual(self._err(frequency_hz=1e6, thickness_um=35,
                                   thickness_mm=0.035)["reason_code"],
                         "ARG_REQUIRED")

    def test_zero_thickness(self):
        self.assertEqual(self._err(frequency_hz=1e6, thickness_um=0)["reason_code"],
                         "BAD_PARAMETER")

    def test_unknown_face_rule(self):
        r = self._err(frequency_hz=1e6, thickness_um=35, faces="three")
        self.assertEqual(r["reason_code"], "BAD_PARAMETER")
        self.assertIn("factor of two", r["message"])

    def test_mu_r_below_one(self):
        self.assertEqual(self._err(frequency_hz=1e6, thickness_um=35,
                                   mu_r=0.5)["reason_code"], "BAD_PARAMETER")

    def test_bad_rho(self):
        self.assertEqual(self._err(frequency_hz=1e6, thickness_um=35,
                                   rho_ohm_mm=0)["reason_code"], "BAD_PARAMETER")

    def test_profile_samples_too_small(self):
        self.assertEqual(self._err(frequency_hz=1e6, thickness_um=35,
                                   profile_samples=1)["reason_code"],
                         "BAD_PARAMETER")

    def test_length_without_width(self):
        self.assertEqual(self._err(frequency_hz=1e6, thickness_um=35,
                                   length_mm=20)["reason_code"], "ARG_REQUIRED")

    def test_geometry_and_dc_resistance_together(self):
        self.assertEqual(self._err(frequency_hz=1e6, thickness_um=35, length_mm=20,
                                   width_mm=2, dc_resistance_ohm=0.1)["reason_code"],
                         "ARG_INVALID")

    def test_negative_roughness(self):
        self.assertEqual(self._err(frequency_hz=1e6, thickness_um=35,
                                   roughness_rq_um=-1)["reason_code"],
                         "BAD_PARAMETER")


class TestTheToolSurface(unittest.TestCase):
    def test_it_is_registered_with_a_schema(self):
        d = T.TOOLS["ac_sheet_resistance"]
        schema = d.get("inputSchema") or d
        self.assertEqual(schema["required"], ["frequency_hz"])
        self.assertIn("thickness_um", schema["properties"])
        self.assertIn("faces", schema["properties"])

    def test_the_envelope_says_what_it_is_not(self):
        r = T.call_tool("ac_sheet_resistance", {"frequency_hz": 1e7,
                                                "thickness_um": 35})
        self.assertEqual(r["status"], "ok")
        self.assertIn("1-D", r["model"])
        self.assertTrue(any("lateral" in w for w in r["warnings"]))
        self.assertIn("not_modelled", r["limits"])

    def test_a_thin_conductor_gets_no_roughness_warning(self):
        r = T.call_tool("ac_sheet_resistance", {"frequency_hz": 1e3,
                                                "thickness_um": 35})
        self.assertEqual(r["warnings"], [])


STRIP = dict(polygons=[[[0.0, 0.0], [20.0, 0.0], [20.0, 2.0], [0.0, 2.0]]],
             source={"x0_mm": -0.01, "y0_mm": -0.01, "x1_mm": 0.0, "y1_mm": 2.01},
             sink={"x0_mm": 20.0, "y0_mm": -0.01, "x1_mm": 20.01, "y1_mm": 2.01},
             h_mm=0.1)


class TestTheFieldToolAtFrequency(unittest.TestCase):
    """current_density_field(frequency_hz=...) is a uniform scaling - and says so."""

    def test_dc_is_unchanged_when_the_frequency_is_omitted(self):
        a = T.call_tool("current_density_field", dict(STRIP))
        b = T.call_tool("current_density_field", dict(STRIP, frequency_hz=None,
                                                      skin_faces="one"))
        self.assertEqual(a["status"], "ok")
        self.assertEqual(a["value"], b["value"])
        self.assertNotIn("frequency_hz", a["value"])
        self.assertNotIn("skin_depth_um", a["value"])

    def test_the_ac_resistance_is_the_dc_one_times_the_ratio(self):
        dc = T.call_tool("current_density_field", dict(STRIP))["value"]
        ac = T.call_tool("current_density_field", dict(STRIP,
                                                       frequency_hz=1e7))["value"]
        self.assertAlmostEqual(ac["resistance_ohm"] / dc["resistance_ohm"],
                               ac["r_ac_over_r_dc"], delta=1e-12)
        # the LATERAL field does not move: same peak, same crowding
        # AC J is the density in the effective layer = surface value = r x the DC
        # gross average; the gross average itself does not move.
        self.assertAlmostEqual(ac["peak_j_a_per_mm2"],
                               dc["peak_j_a_per_mm2"] * ac["r_ac_over_r_dc"],
                               delta=1e-9)
        self.assertAlmostEqual(ac["peak_j_gross_a_per_mm2"],
                               dc["peak_j_a_per_mm2"], delta=1e-12)
        self.assertAlmostEqual(ac["crowding_factor"], dc["crowding_factor"],
                               delta=1e-12)
        # and the surface density is the average times the ratio
        self.assertAlmostEqual(ac["peak_j_surface_a_per_mm2"],
                               ac["peak_j_a_per_mm2"], delta=1e-12)

    def test_the_ratio_agrees_with_the_skin_tool(self):
        ac = T.call_tool("current_density_field",
                         dict(STRIP, frequency_hz=1e7))["value"]
        skin = T.call_tool("ac_sheet_resistance", {"frequency_hz": 1e7,
                                                   "thickness_mm": 0.035})["value"]
        self.assertAlmostEqual(ac["r_ac_over_r_dc"], skin["r_ac_over_r_dc"],
                               delta=1e-12)
        self.assertAlmostEqual(ac["skin_depth_um"], skin["skin_depth_um"],
                               delta=1e-12)

    def test_two_faces_gives_the_smaller_resistance(self):
        one = T.call_tool("current_density_field",
                          dict(STRIP, frequency_hz=1e7))["value"]["resistance_ohm"]
        two = T.call_tool("current_density_field", dict(STRIP, frequency_hz=1e7,
                                                        skin_faces="two"))["value"]
        self.assertLess(two["resistance_ohm"], one)
        self.assertGreater(two["r_ac_over_r_dc"], 1.0)

    def test_bad_frequency_or_face_is_refused(self):
        for kw in ({"frequency_hz": 0}, {"frequency_hz": -1},
                   {"frequency_hz": 1e7, "skin_faces": "three"}):
            r = T.call_tool("current_density_field", dict(STRIP, **kw))
            self.assertEqual(r.get("status"), "error", kw)
            self.assertEqual(r["reason_code"], "BAD_PARAMETER")


if __name__ == "__main__":
    unittest.main()
