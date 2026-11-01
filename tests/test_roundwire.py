"""Golden tests for ac_round_conductor - skin effect in a WIRE, a TUBE and a VIA.

The point of these tests is not that the numbers are reproducible (they are, from
the same code).  It is that every number below was checked against something
OUTSIDE this module:

  * eight values pinned from mpmath at 80-900 decimal digits solving the very
    same Riccati equation with an independent integrator (mp.odefun, tol 1e-40).
    They agree with the walk here to 1e-13, and they are hard-coded, so a change
    in the walk has to fight the reference, not itself;
  * the DC series (R = 1 + x^4/48, X = x^2/4) and the DC internal inductance
    mu/(8 pi) = 50 nH/m of a ROUND wire - a constant that does not depend on the
    diameter, so a geometry mistake cannot hide in it;
  * the high-frequency asymptote R = x/2 + 1/4 + 3/(32x) at x = 1e6;
  * a finite-difference solve of the same ODE (a different discretisation,
    second order, no Bessel functions anywhere).

Three checks exist specifically because they can FAIL and would otherwise be
believed:

  1. The tube closed form cancels above x ~ 15 and returns the SOLID answer.
     `test_collapse_is_real` asserts that collapse (a fact about the formula) and
     `test_collapse_would_be_a_factor_of_five` asserts how wrong it is, so
     nobody can quietly re-promote it to the answer path.
  2. The Bessel radial profile returns a FLAT 1.0 at x = 72 - a perfectly
     plausible-looking picture that is completely wrong.  The test asserts the
     Bessel version DOES fail there and that the answer path does not, so a
     "cleanup" that swaps them back is caught.
  3. The thin-wall agreement with ac_sheet_resistance is compared AFTER dividing
     out the mean-radius geometric factor.  Without that factor a 25 um plating
     "disagrees" by 11.6 %, which is physics, not an error - and a test that
     called that a failure would be trained away rather than fixed.
"""
import math
import os
import sys
import unittest

sys.path.insert(0, os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "src"))

from pcb_agent_mcp import tools as T  # noqa: E402
from pcb_agent_mcp.calc import roundwire as R  # noqa: E402

MU0 = 4.0e-7 * math.pi
RHO = 1.724e-5          # ohm*mm, copper at 20 C


def _f_for_x(x, a2_mm):
    """The frequency at which a conductor of outer radius a2_mm sits at x."""
    return RHO * 1e-3 * x * x / (math.pi * MU0 * (a2_mm * 1e-3) ** 2)


def _call(args):
    return T.call_tool("ac_round_conductor", args)


class TestAnchorsAgainstMpmath(unittest.TestCase):
    """Eight pinned values, each from mpmath at 80-900 dps (see docs/70)."""

    def _check(self, x_out, r_ratio, wr, wi, tol):
        z, conv, _ = R.riccati_ratio(x_out, x_out * r_ratio)
        self.assertLess(abs(z.real - wr), tol * abs(wr),
                        "real: %.15g vs %.15g (conv %g)" % (z.real, wr, conv))
        self.assertLess(abs(z.imag - wi), tol * abs(wi),
                        "imag: %.15g vs %.15g (conv %g)" % (z.imag, wi, conv))

    def test_x71_r0845(self):
        """The hardest pinned point: needs ~70 dps before the closed form gives 15
        correct digits, and 55 dps gives a WRONG answer silently."""
        self._check(71.0, 0.845, 10.2239838017698159, 10.1517295191057189, 1e-9)

    def test_x40_r09(self):
        self._check(40.0, 0.9, 3.85009592325133328, 3.796654459826328, 1e-8)

    def test_x1000_r09(self):
        self._check(1000.0, 0.9, 95.047517812488, 94.999982169676, 1e-9)

    def test_x05_r05(self):
        self._check(0.5, 0.5, 1.00023849615162397, 0.0300628347297098434, 1e-8)

    def test_x5_r05(self):
        self._check(5.0, 0.5, 2.0579064265060352, 1.88579245401796797, 1e-8)

    def test_x10_r09(self):
        self._check(10.0, 0.9, 1.08116958598840627, 0.617238910090482977, 1e-8)

    def test_x200_r05(self):
        self._check(200.0, 0.5, 75.1878515566824676, 74.9996466739199531, 1e-8)

    def test_x300_r095(self):
        self._check(300.0, 0.95, 14.649405468524, 14.624969429468, 1e-8)

    def test_solid_closed_form_equals_the_walk(self):
        """The solid answer takes the closed form; the walk is run as a check and
        must reproduce it at x = 40 and at x = 400."""
        for x in (40.0, 400.0):
            zc = R._solid_ratio(x)
            zw, _, _ = R.riccati_ratio(x, 0.0)
            self.assertLess(abs(zw - zc) / abs(zc), 1e-8, "x=%g" % x)


class TestSolidClosedForm(unittest.TestCase):
    def setUp(self):
        self.r = _call({"frequency_hz": 1.0e6, "diameter_mm": 1.0})

    def test_dc_series_r(self):
        # R = 1 + x^4/48 (a ROUND wire). A flat sheet would be 1 + 4x^4/45 and
        # fails this by 4 % at x = 0.05, so the two cannot be confused.
        self.assertLess(self.r["checks"]["dc_series_r_rel_error"], 1e-12)

    def test_dc_series_x(self):
        # X = x^2/4 exactly (the internal inductance below)
        self.assertLess(self.r["checks"]["dc_series_x_rel_error"], 1e-9)

    def test_dc_internal_inductance_is_50_nh_per_m(self):
        # mu/(8 pi) = 50.0 nH/m for ANY diameter. The check the first version
        # missed by five orders of magnitude when it divided by the caller's f.
        c = self.r["checks"]
        self.assertAlmostEqual(c["dc_internal_inductance_nh_per_m"], 50.0, delta=1e-8)
        self.assertLess(c["dc_internal_inductance_rel_error"], 1e-9)

    def test_asymptote_r_and_45_degrees(self):
        c = self.r["checks"]
        self.assertLess(c["asymptote_r_rel_error"], 1e-6)
        # X -> x/2: |X/(x/2) - 1| is O(1/x^2) at x = 40
        self.assertLess(c["asymptote_45deg_rel_error"], 2e-4)

    def test_bessel_branches_overlap(self):
        c = self.r["checks"]
        self.assertLess(c["branch_overlap_j_rel"], 1e-10)
        self.assertLess(c["branch_overlap_y_rel"], 1e-10)

    def test_x_1e6_still_stable(self):
        z = R._solid_ratio(1.0e6)
        pred = 1.0e6 / 2.0 + 0.25 + 3.0 / (32.0 * 1.0e6)
        self.assertLess(abs(z.real / pred - 1.0), 1e-9)
        self.assertAlmostEqual(z.imag / 5.0e5, 1.0, delta=1e-9)

    def test_the_1mm_wire_at_1mhz(self):
        v = self.r["value"]
        self.assertAlmostEqual(v["skin_depth_um"], 66.082849628, delta=1e-6)
        self.assertAlmostEqual(v["x_outer"], 7.5662596697, delta=1e-8)
        self.assertAlmostEqual(v["ac_over_dc"], 4.0453449671, delta=1e-8)
        self.assertAlmostEqual(v["x_over_dc"], 3.7689655123, delta=1e-8)
        self.assertAlmostEqual(v["dc_resistance_ohm_per_m"], 0.021950650, delta=1e-8)
        self.assertAlmostEqual(v["ac_resistance_ohm_per_m"], 0.088797950, delta=1e-8)


class TestClosedFormCollapse(unittest.TestCase):
    """The tube closed form is a trap above x ~ 15. These tests pin the trap."""

    def test_collapse_is_real(self):
        """At x = 40, a1/a2 = 0.9 the tube closed form returns the SOLID answer
        (20.2523) digit for digit - it has lost every bit of the tube."""
        zt = R._tube_ratio(40.0, 36.0)
        zs = R._solid_ratio(40.0)
        self.assertLess(abs(zt - zs) / abs(zs), 1e-9)
        self.assertAlmostEqual(zt.real, 20.2523, delta=1e-4)

    def test_collapse_would_be_a_factor_of_five(self):
        truth, _, _ = R.riccati_ratio(40.0, 36.0)
        zt = R._tube_ratio(40.0, 36.0)
        self.assertGreater(abs(zt / truth - 1.0), 4.0)   # measured 5.26x

    def test_the_tool_refuses_to_publish_it_above_10(self):
        r = _call({"frequency_hz": 1.0e9, "drill_mm": 0.15, "plating_um": 25})
        c = r["checks"]
        self.assertIn("closed_form_skipped", c)
        self.assertNotIn("closed_form_vs_riccati_rel", c)
        self.assertIn("20.2523", c["closed_form_skipped"])

    def test_above_10_the_answer_is_the_walk_not_the_solid_one(self):
        """A 200 um barrel with a 31 um wall at x = 72 is 19.0x R_dc; the collapsed
        closed form would say ~36.0x, i.e. the solid wire."""
        f = _f_for_x(72.0, 0.1)
        r = _call({"frequency_hz": f, "diameter_mm": 0.2, "wall_um": 31.0})
        v = r["value"]
        self.assertAlmostEqual(v["x_outer"], 72.0, delta=1e-6)
        self.assertAlmostEqual(v["ac_over_dc"], 18.9920570730, delta=1e-7)
        self.assertLess(v["ac_over_dc"], 25.0)   # the solid answer is 36.25

    def test_below_10_the_two_paths_agree(self):
        f = _f_for_x(5.0, 0.5)
        r = _call({"frequency_hz": f, "diameter_mm": 1.0, "wall_um": 100.0})
        c = r["checks"]
        self.assertIn("closed_form_vs_riccati_rel", c)
        self.assertLess(c["closed_form_vs_riccati_rel"], 1e-6)
        self.assertNotIn("closed_form_skipped", c)


class TestBesselProfileIsTheNegativeControl(unittest.TestCase):
    def test_bessel_profile_is_flat_at_x72_and_the_answer_is_not(self):
        f = _f_for_x(72.0, 0.1)
        r = _call({"frequency_hz": f, "diameter_mm": 0.2, "wall_um": 31.0})
        xo, xi = r["value"]["x_outer"], r["value"]["x_inner"]
        good = [row["j_rel"] for row in R.radial_profile(xo, xi, 9)]
        bad = [row["j_rel"] for row in R.radial_profile_bessel(xo, xi, 9)]
        # the answer path has a real structure: |J| spans six decades
        self.assertLess(min(good), 1e-8)
        self.assertAlmostEqual(max(good), 1.0, delta=1e-12)
        # the closed form reports "uniform current" - plausible, and wrong
        self.assertGreater(min(bad), 0.999)

    def test_profile_is_monotone_and_peaks_at_the_surface(self):
        r = _call({"frequency_hz": 1.0e9, "drill_mm": 0.15, "plating_um": 25})
        c = r["checks"]
        self.assertIs(c["profile_monotone_outward"], True)
        self.assertIs(c["profile_outer_is_max"], True)

    def test_phase_lag_accumulates(self):
        """The lag is thousands of degrees across a barrel many skin depths wide;
        phase_deg is that number wrapped for a plot, never the only one."""
        r = _call({"frequency_hz": 1.0e9, "drill_mm": 0.15, "plating_um": 25})
        p = r["value"]["profile"]
        self.assertLess(p[0]["phase_lag_deg"], -600.0)
        for row in p:
            self.assertGreaterEqual(row["phase_deg"], -180.0)
            self.assertLessEqual(row["phase_deg"], 180.0)

    def test_closed_profile_is_used_only_below_5(self):
        r = _call({"frequency_hz": 1.0e4, "diameter_mm": 1.0})
        self.assertLess(r["value"]["x_outer"], 1.0)
        self.assertIn("profile_vs_closed_rel", r["checks"])
        self.assertLess(r["checks"]["profile_vs_closed_rel"], 1e-5)
        r2 = _call({"frequency_hz": 1.0e9, "drill_mm": 0.15, "plating_um": 25})
        self.assertIn("profile_vs_closed_skipped", r2["checks"])
        self.assertNotIn("profile_vs_closed_rel", r2["checks"])


class TestVia(unittest.TestCase):
    def test_the_flagship_via_at_1ghz(self):
        r = _call({"frequency_hz": 1.0e9, "drill_mm": 0.15, "plating_um": 25})
        v = r["value"]
        self.assertAlmostEqual(v["skin_depth_um"], 2.089723191, delta=1e-7)
        self.assertAlmostEqual(v["outer_diameter_mm"], 0.2, delta=1e-12)
        self.assertAlmostEqual(v["bore_mm"], 0.15, delta=1e-12)
        self.assertAlmostEqual(v["wall_um"], 25.0, delta=1e-9)
        self.assertAlmostEqual(v["x_outer"], 47.8532278490, delta=1e-7)
        self.assertAlmostEqual(v["ac_over_dc"], 10.5781254499, delta=1e-7)
        self.assertAlmostEqual(v["ac_resistance_ohm_per_m"], 13.268384387, delta=1e-6)
        self.assertAlmostEqual(v["dc_resistance_ohm_per_m"], 1.2543228429, delta=1e-9)
        self.assertIn("skin-dominated", v["regime"])
        self.assertIs(v["hollow"], True)
        # the wall is 12 skin depths thick, so the thin-wall warning must NOT fire
        self.assertFalse(any("the current fills the whole wall" in w
                             for w in r.get("warnings") or []))

    def test_a_wall_thinner_than_a_skin_depth_is_wall_limited(self):
        """0.5 um of plating: R/R_dc is 1.0003 - the current fills the wall, so the
        answer is 'how much copper did you put there', NOT a skin depth."""
        r = _call({"frequency_hz": 1.0e9, "drill_mm": 0.1, "plating_um": 0.5})
        v = r["value"]
        self.assertAlmostEqual(v["ac_over_dc"], 1.0002898, delta=1e-6)
        self.assertIn("wall-limited", v["regime"])
        self.assertTrue(any("the current fills the whole wall" in w
                            for w in r.get("warnings") or []))

    def test_a_via_at_dc_agrees_with_via_resistance_same_drawing(self):
        """The AC tool at 1 Hz has to land on the DC annulus answer - but only
        when the two tools are reading the SAME drawing.  This tool's bore is the
        finished hole with the copper outside it, which is via_resistance's
        plating_outside=true; the default there is the other convention."""
        ac = _call({"frequency_hz": 1.0, "drill_mm": 0.3, "plating_um": 25,
                    "length_mm": 1.6})["value"]
        dc = T.call_tool("via_resistance", {"drill_mm": 0.3, "plating_um": 25,
                                            "length_mm": 1.6,
                                            "plating_outside": True})["value"]
        self.assertLess(abs(ac["dc_resistance_ohm"] / dc["resistance_ohm"] - 1.0),
                        1e-6)      # via_resistance rounds to 9 digits

    def test_the_two_hole_conventions_differ_by_the_annulus_not_by_a_bug(self):
        """Default via_resistance puts the copper INSIDE drill_mm; the AC tool puts
        it outside. The ratio of the two DC answers must be exactly the ratio of
        the two annuli - if either formula were wrong the ratio would not land."""
        d, t = 0.3, 0.025
        a_in = math.pi * ((d / 2.0) ** 2 - (d / 2.0 - t) ** 2)     # copper inside
        a_out = math.pi * ((d / 2.0 + t) ** 2 - (d / 2.0) ** 2)    # copper outside
        r_default = T.call_tool("via_resistance",
                                {"drill_mm": d, "plating_um": t * 1e3,
                                 "length_mm": 1.6})["value"]["resistance_ohm"]
        r_out = T.call_tool("via_resistance",
                            {"drill_mm": d, "plating_um": t * 1e3,
                             "length_mm": 1.6,
                             "plating_outside": True})["value"]["resistance_ohm"]
        self.assertLess(abs(r_default / r_out - a_out / a_in), 1e-5)
        self.assertGreater(abs(r_default / r_out - 1.0), 0.15)   # 18 %, not noise

    def test_thin_wall_agrees_with_the_flat_sheet_after_the_geometric_factor(self):
        """The 11.6 % 'discrepancy' with a 25 um plating is the mean-radius factor,
        not solver error: X agrees to 1e-4 once it is divided out, and R to 1.1 %
        because a slab and a curved wall do not share the O(1/x) surface term."""
        r = _call({"frequency_hz": 1.0e9, "drill_mm": 0.15, "plating_um": 25})
        c = r["checks"]["thin_wall_vs_flat_sheet"]
        self.assertAlmostEqual(c["geom_factor"], 0.875, delta=1e-12)
        self.assertLess(c["x_rel_error"], 1e-3)
        self.assertLess(c["r_rel_error"], 0.03)


class TestIndependentSolvers(unittest.TestCase):
    def test_finite_difference_agrees_and_converges(self):
        for args in ({"frequency_hz": 1.0e6, "diameter_mm": 1.0},
                     {"frequency_hz": 1.0e9, "drill_mm": 0.15, "plating_um": 25}):
            c = _call(args)["checks"]
            self.assertIs(c["fd_converged"], True, args)
            self.assertLess(c["fd_vs_answer_rel_error"], 5e-3, args)
            self.assertLess(c["fd_self_convergence_rel"], 2e-3, args)

    def test_finite_difference_is_skipped_and_says_so_above_x_200(self):
        f = _f_for_x(250.0, 0.5)
        c = _call({"frequency_hz": f, "diameter_mm": 1.0})["checks"]
        self.assertEqual(c["fd_verify"], "skipped")
        self.assertIn("x_out = 250", c["fd_skipped"])
        self.assertNotIn("fd_vs_answer_rel_error", c)

    def test_fd_can_be_switched_off_without_changing_the_answer(self):
        a = _call({"frequency_hz": 1.0e9, "drill_mm": 0.15, "plating_um": 25})
        b = _call({"frequency_hz": 1.0e9, "drill_mm": 0.15, "plating_um": 25,
                   "fd_verify": False})
        self.assertEqual(a["value"]["ac_over_dc"], b["value"]["ac_over_dc"])
        self.assertNotIn("fd_vs_answer_rel_error", b["checks"])

    def test_bore_to_zero_is_the_solid_wire(self):
        """A bore of 1e-4 of the radius must be the solid wire once the area factor
        is divided out - comparing the RATIOS would show a fake 1e-6 offset."""
        r = _call({"frequency_hz": 1.0e9, "diameter_mm": 0.2, "wall_um": 99.99})
        self.assertLess(r["checks"]["bore_to_zero_rel"], 1e-4)


class TestRefusals(unittest.TestCase):
    """A guessed geometry is the way a skin answer becomes wrong silently."""

    def _err(self, args):
        r = _call(args)
        self.assertEqual(r["status"], "error", args)
        return r

    def test_no_geometry(self):
        self._err({"frequency_hz": 1.0e6})

    def test_no_frequency(self):
        self._err({"diameter_mm": 1.0})

    def test_zero_frequency(self):
        r = self._err({"frequency_hz": 0.0, "diameter_mm": 1.0})
        self.assertIn("via_resistance", r["message"])

    def test_diameter_and_drill_together(self):
        self._err({"frequency_hz": 1.0e6, "diameter_mm": 1.0, "drill_mm": 0.3})

    def test_drill_without_plating(self):
        self._err({"frequency_hz": 1.0e6, "drill_mm": 0.3})

    def test_wall_not_a_tube(self):
        self._err({"frequency_hz": 1.0e6, "diameter_mm": 1.0, "wall_um": 600.0})

    def test_zero_wall(self):
        self._err({"frequency_hz": 1.0e6, "diameter_mm": 1.0, "wall_um": 0.0})

    def test_mu_below_one(self):
        self._err({"frequency_hz": 1.0e6, "diameter_mm": 1.0, "mu_r": 0.5})

    def test_rho_zero(self):
        self._err({"frequency_hz": 1.0e6, "diameter_mm": 1.0, "rho_ohm_mm": 0.0})

    def test_profile_needs_two_points(self):
        self._err({"frequency_hz": 1.0e6, "diameter_mm": 1.0, "profile_samples": 1})


class TestMilestonesAndScaling(unittest.TestCase):
    def setUp(self):
        self.r = _call({"frequency_hz": 1.0e6, "diameter_mm": 1.0})

    def test_the_three_milestone_frequencies(self):
        v = self.r["value"]
        self.assertAlmostEqual(v["f_at_r_1pct_over_dc_hz"], 12150.686612, delta=1e-5)
        self.assertAlmostEqual(v["f_at_r_10pct_over_dc_hz"], 39883.650907, delta=1e-5)
        self.assertAlmostEqual(v["f_at_r_2x_dc_hz"], 208002.130738, delta=1e-5)
        self.assertLess(v["f_at_r_1pct_over_dc_hz"], v["f_at_r_10pct_over_dc_hz"])
        self.assertLess(v["f_at_r_10pct_over_dc_hz"], v["f_at_r_2x_dc_hz"])

    def test_the_milestones_round_trip(self):
        """Bisecting on the wall in skin depths is only valid if the frequency it
        returns really does give that R/R_dc."""
        v = self.r["value"]
        for key, target in (("f_at_r_1pct_over_dc_hz", 1.01),
                            ("f_at_r_10pct_over_dc_hz", 1.10),
                            ("f_at_r_2x_dc_hz", 2.00)):
            got = _call({"frequency_hz": v[key], "diameter_mm": 1.0})
            self.assertAlmostEqual(got["value"]["ac_over_dc"], target, delta=1e-8)

    def test_length_scales_the_answer_linearly(self):
        v = _call({"frequency_hz": 1.0e6, "diameter_mm": 1.0,
                   "length_mm": 25.0})["value"]
        self.assertAlmostEqual(v["dc_resistance_ohm"], 0.021950650 * 0.025, delta=1e-10)
        self.assertAlmostEqual(v["ac_resistance_ohm"], 0.088797950 * 0.025, delta=1e-10)
        self.assertAlmostEqual(v["ac_inductance_nh"],
                               v["internal_inductance_nh_per_m"] * 0.025, delta=1e-10)

    def test_roughness_only_touches_the_loss(self):
        a = _call({"frequency_hz": 1.0e6, "diameter_mm": 1.0})["value"]
        b = _call({"frequency_hz": 1.0e6, "diameter_mm": 1.0,
                   "roughness_rq_um": 1.0})["value"]
        self.assertNotIn("roughness_k", a)
        self.assertGreater(b["roughness_k"], 1.0)
        self.assertGreater(b["ac_resistance_rough_ohm_per_m"],
                           b["ac_resistance_ohm_per_m"])
        self.assertEqual(a["ac_resistance_ohm_per_m"], b["ac_resistance_ohm_per_m"])
        self.assertEqual(a["ac_over_dc"], b["ac_over_dc"])

    def test_temperature_raises_the_resistance(self):
        a = _call({"frequency_hz": 1.0e6, "diameter_mm": 1.0})["value"]
        b = _call({"frequency_hz": 1.0e6, "diameter_mm": 1.0,
                   "temp_c": 100.0})["value"]
        self.assertGreater(b["dc_resistance_ohm_per_m"], a["dc_resistance_ohm_per_m"])
        # ...and R/R_dc goes DOWN, because a hotter metal has a longer skin depth
        self.assertLess(b["ac_over_dc"], a["ac_over_dc"])


class TestEnvelope(unittest.TestCase):
    def setUp(self):
        self.r = _call({"frequency_hz": 1.0e9, "drill_mm": 0.15, "plating_um": 25})

    def test_it_is_honest_about_the_answer_path(self):
        self.assertIn("Riccati", self.r["model"])
        self.assertIn("not used", self.r["model"].replace("NOT used", "not used"))
        self.assertIn("Riccati", self.r["assumptions"]["answer_path"])
        self.assertIn("NOT the same as a symmetry plane",
                      self.r["assumptions"]["bore_condition"])

    def test_it_names_what_it_cannot_see(self):
        a = self.r["assumptions"]
        self.assertIn("phi", a["current"])          # axisymmetric: no phi variation
        w = " ".join(self.r.get("warnings") or [])
        self.assertIn("proximity", w)
        self.assertIn("pad", w)

    def test_it_says_the_roughness_arrow_points_one_way(self):
        r = _call({"frequency_hz": 1.0e9, "drill_mm": 0.15, "plating_um": 25})
        w = " ".join(r.get("warnings") or [])
        self.assertIn("LOWER bound", w)

    def test_units_are_named(self):
        self.assertEqual(self.r["unit"], "mixed (see keys: Hz, mm, um, ohm, nH, "
                                         "deg, dimensionless)")

    def test_no_key_is_a_skipped_check_that_also_ran(self):
        """The envelope must not carry both 'it ran' and 'it was skipped'."""
        c = self.r["checks"]
        self.assertNotIn("closed_form_vs_riccati_rel", c)
        self.assertNotIn("profile_vs_closed_rel", c)
        # x_out = 47.9 < 200, so the finite-difference solve DID run here
        self.assertIn("fd_vs_answer_rel_error", c)
        self.assertNotIn("fd_skipped", c)


class TestRegistration(unittest.TestCase):
    def test_tool_is_registered(self):
        self.assertIn("ac_round_conductor", T.TOOLS)

    def test_it_is_advertised(self):
        cap = T.call_tool("calc_capabilities", {})
        self.assertIn("ac_round_conductor", cap["available"])

    def test_the_schema_requires_a_frequency(self):
        d = T.TOOLS["ac_round_conductor"]
        self.assertEqual((d.get("inputSchema") or d)["required"], ["frequency_hz"])

    def test_the_neighbours_point_at_it(self):
        self.assertIn("ac_round_conductor", str(T.TOOLS["via_resistance"]))
        self.assertIn("ac_round_conductor", str(T.TOOLS["ac_sheet_resistance"]))

    def test_the_new_tool_names_the_hole_convention(self):
        a = _call({"frequency_hz": 1.0e9, "drill_mm": 0.15,
                   "plating_um": 25})["assumptions"]
        self.assertIn("FINISHED hole", a["glossary"])
        self.assertIn("plating_outside", a["glossary"])


if __name__ == "__main__":
    unittest.main()
