"""Golden tests for current_density_field - the 2-D field, not the 1-D formula.

The whole point of this tool is the number the 1-D sheet estimate cannot give:
J at a geometry change.  So the tests are built around two analytic anchors that
a wrong solver cannot hit by accident, and around the two things that make a
field answer dishonest if they are not said out loud:

  * a uniform strip must reproduce rho/t * L/W and a flat field (crowding 1.0,
    rings equal), otherwise the mesh, the units or the scaling is wrong;
  * a 4:1 width step must reproduce the exact 1-D series resistance and put 4x
    the density in the narrow part;
  * a 90 degree bend has a 270 degree metal corner, J ~ r^(-1/3), so the peak
    MUST grow by ~2^(1/3) = 1.2599 per halving of h.  That growth is the
    measurement: if it were 1.0 the corner would be being smoothed away, and the
    tool would be quietly answering a different geometry than the caller drew;
  * a field that has not converged must come back as an error, not as a number.

Every number below was measured before it was asserted (see docs/66).
"""
import math
import os
import sys
import unittest

sys.path.insert(0, os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "src"))

from pcb_agent_mcp.calc import currentfield as CF  # noqa: E402

RHO = 1.724e-5          # ohm*mm, copper at 20 C
T = 0.035               # mm, 1 oz

# a 20 x 2 mm strip, contacts on the two full end faces
STRIP = [[[0.0, 0.0], [20.0, 0.0], [20.0, 2.0], [0.0, 2.0]]]
STRIP_SRC = {"x0_mm": -0.01, "y0_mm": -0.01, "x1_mm": 0.0, "y1_mm": 2.01}
STRIP_SNK = {"x0_mm": 20.0, "y0_mm": -0.01, "x1_mm": 20.01, "y1_mm": 2.01}

# 4:1 width step at x = 10
STEP_NARROW = [[[0.0, 0.75], [10.0, 0.75], [10.0, 1.25], [0.0, 1.25]]]
STEP_WIDE = [[[10.0, 0.0], [20.0, 0.0], [20.0, 2.0], [10.0, 2.0]]]
STEP_SRC = {"x0_mm": -0.01, "y0_mm": 0.74, "x1_mm": 0.0, "y1_mm": 1.26}

# 1 mm wide L: horizontal arm along y = 10, vertical arm along x = 10.
# The inner corner is (9.5, 9.5) and is a 270 degree interior angle of metal.
BEND = [[[0.0, 10.5], [10.5, 10.5], [10.5, 0.0], [9.5, 0.0], [9.5, 9.5], [0.0, 9.5]]]
BEND_SRC = {"x0_mm": -0.01, "y0_mm": 9.49, "x1_mm": 0.0, "y1_mm": 10.51}
BEND_SNK = {"x0_mm": 9.49, "y0_mm": -0.01, "x1_mm": 10.51, "y1_mm": 0.0}
# the same two arms, joined through a 45 degree stub instead of a sharp corner
BEND_MITER = [{"points": [[0.0, 10.0], [8.0, 10.0], [10.0, 8.0], [10.0, 0.0]],
               "width_mm": 1.0}]


# The solver is pure Python and a 90 degree bend at h = 0.05 mm needs ~5 s, so the
# same solve is never run twice inside this file.
_CACHE = {}


def _run(key, **kw):
    if key not in _CACHE:
        kw.setdefault("thickness_mm", T)
        kw.setdefault("current_a", 1.0)
        kw.setdefault("levels", 3)
        _CACHE[key] = CF.current_density_field(**kw)
    return _CACHE[key]


def _strip(current=1.0, **kw):
    kw.setdefault("h_mm", 0.1)
    kw["current_a"] = current
    return _run(("strip",) + tuple(sorted(kw.items())),
                polygons=STRIP, source=STRIP_SRC, sink=STRIP_SNK, **kw)


def _bend(**kw):
    kw.setdefault("h_mm", 0.05)
    return _run(("bend",) + tuple(sorted(kw.items())),
                polygons=BEND, source=BEND_SRC, sink=BEND_SNK, **kw)


def _miter(**kw):
    kw.setdefault("h_mm", 0.05)
    return _run(("miter",) + tuple(sorted(kw.items())),
                paths=BEND_MITER, source=BEND_SRC, sink=BEND_SNK, **kw)


class TestAnalyticAnchors(unittest.TestCase):
    def test_uniform_strip_matches_the_sheet_resistance(self):
        r = _strip()
        self.assertEqual(r["status"], "ok", r)
        v = r["value"]
        analytic = RHO / T * 20.0 / 2.0        # 4.9257e-3 ohm
        self.assertAlmostEqual(v["resistance_ohm"] / analytic, 1.0, delta=0.02)

    def test_uniform_strip_has_a_flat_field(self):
        """peak/median ~ 1 and every ring equal: a field solver that leans on the
        grid or on the contacts shows up here first."""
        v = _strip()["value"]
        self.assertAlmostEqual(v["peak_j_a_per_mm2"] / v["median_j_a_per_mm2"], 1.0,
                               delta=0.01)
        self.assertAlmostEqual(v["uniform_j_at_narrowest_a_per_mm2"],
                               1.0 / (2.0 * T), delta=1e-3)
        self.assertAlmostEqual(v["crowding_factor"], 1.0, delta=0.01)
        rings = v["rings"]
        self.assertAlmostEqual(rings["max_j_2h_to_4h"] / rings["max_j_0.5h_to_1h"],
                               1.0, delta=0.01)
        self.assertTrue(v["peak_is_grid_convergent"])

    def test_width_step_gives_exactly_four_times_the_density(self):
        """A 4:1 step is the cleanest 1-D check there is: the narrow part must
        carry J = I/(0.5*t) and the wide part I/(2*t), ratio 4.000."""
        r = _run("step", polygons=STEP_NARROW + STEP_WIDE, source=STEP_SRC,
                 sink=STRIP_SNK, h_mm=0.05, thickness_mm=T, current_a=1.0)
        self.assertEqual(r["status"], "ok", r)
        v = r["value"]
        j_narrow = 1.0 / (0.5 * T)
        j_wide = 1.0 / (2.0 * T)
        self.assertAlmostEqual(v["uniform_j_at_narrowest_a_per_mm2"], j_narrow,
                               delta=j_narrow * 1e-3)
        # the grid is dominated by the wide half, so the median sits on j_wide
        self.assertAlmostEqual(v["median_j_a_per_mm2"] / j_wide, 1.0, delta=0.05)
        # R against the 1-D series value.  Measured 0.012449 vs 0.012314, i.e.
        # +1.1%: the step corner is a 270 degree metal corner and the staircase
        # rasterisation of it costs a little, which is the honest reading rather
        # than a one-sided claim that the mesh must come in under.
        r_1d = RHO / T * (10.0 / 0.5 + 10.0 / 2.0)
        self.assertAlmostEqual(v["resistance_ohm"] / r_1d, 1.0, delta=0.03)

    def test_resistance_scales_with_rho_and_thickness(self):
        base = _strip()["value"]["resistance_ohm"]
        twice_thick = _strip(thickness_mm=2 * T)["value"]["resistance_ohm"]
        twice_rho = _strip(rho_ohm_mm=2 * RHO)["value"]["resistance_ohm"]
        self.assertAlmostEqual(twice_thick / base, 0.5, delta=0.02)
        self.assertAlmostEqual(twice_rho / base, 2.0, delta=0.02)

    def test_current_scales_the_field_linearly_and_not_the_resistance(self):
        one = _strip(current=1.0)["value"]
        two = _strip(current=2.0)["value"]
        self.assertAlmostEqual(two["peak_j_a_per_mm2"] / one["peak_j_a_per_mm2"], 2.0,
                               delta=1e-6)
        self.assertAlmostEqual(two["resistance_ohm"], one["resistance_ohm"],
                               delta=one["resistance_ohm"] * 1e-9)
        self.assertAlmostEqual(two["power_w"] / one["power_w"], 4.0, delta=1e-6)


class TestTheCornerIsSingularAndSaysSo(unittest.TestCase):
    def test_a_bend_peak_sits_on_the_inner_corner(self):
        r = _bend()
        self.assertEqual(r["status"], "ok", r)
        px, py = r["value"]["peak_at_mm"]
        self.assertLess(abs(px - 9.5), 0.3, px)
        self.assertLess(abs(py - 9.5), 0.3, py)

    def test_the_peak_grows_like_two_to_the_one_third(self):
        """THE decisive check. A 270 degree metal corner gives J ~ r^(-1/3), so
        halving h multiplies the peak by 2^(1/3). Measured 1.266 (h 0.05) and
        1.262 (h 0.025). If a change ever made this 1.0 the corner would be being
        rounded away somewhere and the tool would answer a different geometry."""
        r = _bend()
        v = r["value"]
        expect = 2.0 ** (1.0 / 3.0)                     # 1.2599
        self.assertAlmostEqual(v["peak_growth"], expect, delta=0.05)
        self.assertGreater(v["peak_growth"], 1.15)
        self.assertFalse(v["peak_is_grid_convergent"])
        self.assertEqual(v["peak_growth_if_270deg_corner"], expect)
        self.assertTrue(any("NOT GRID CONVERGENT" in w for w in r["warnings"]))
        # the whole level table has to show the growth, not just the last pair
        peaks = [lv["peak_j_a_per_mm2"] for lv in r["levels"]]
        self.assertEqual(peaks, sorted(peaks))

    def test_the_rings_are_the_reading_that_holds_still(self):
        """J ~ r^(-1/3) means the annulus max must fall by (2)^(1/3) between the
        1h..2h and 2h..4h rings (measured 0.796, theory 0.794) - while on a
        uniform strip the same ratio is 1.000."""
        v = _bend()["value"]
        decay = v["rings"]["decay_2h_4h_over_0.5h_1h"]
        self.assertAlmostEqual(decay, 2.0 ** (-1.0 / 3.0), delta=0.05)

    def test_a_rounded_miter_lowers_the_peak_at_the_same_resolution(self):
        """The layout advice, measured: joining the same two arms through a 45
        degree stub takes the peak from 74.2 to 51.8 A/mm^2 at h = 0.05 mm and
        softens the growth from 1.266 to 1.149, for the same copper width."""
        sharp = _bend()["value"]
        miter = _miter()["value"]
        self.assertLess(miter["peak_j_a_per_mm2"], 0.85 * sharp["peak_j_a_per_mm2"])
        self.assertLess(miter["peak_growth"], sharp["peak_growth"])


class TestFailClosed(unittest.TestCase):
    def test_a_deliberately_short_solve_is_an_error_not_a_number(self):
        r = CF.current_density_field(polygons=STRIP, source=STRIP_SRC, sink=STRIP_SNK,
                                     h_mm=0.1, levels=1, max_sweeps=3,
                                     thickness_mm=T, current_a=1.0)
        self.assertEqual(r["status"], "error")
        self.assertEqual(r["reason_code"], "NOT_CONVERGED")
        self.assertNotIn("value", r)
        self.assertIn("did not converge", r["message"])
        # the failed level is named, so the caller knows where it stopped
        self.assertEqual(r["failed_level"]["sweeps"], 3)

    def test_a_contact_that_misses_the_copper_is_refused(self):
        r = CF.current_density_field(polygons=STRIP,
                                     source={"x_mm": 5.0, "y_mm": 8.0,
                                             "radius_mm": 0.3},
                                     sink=STRIP_SNK, h_mm=0.1, levels=2,
                                     thickness_mm=T, current_a=1.0)
        self.assertEqual(r["reason_code"], "CONTACT_NOT_ON_COPPER")
        self.assertNotIn("value", r)

    def test_disconnected_copper_is_diagnosed_not_reported_as_singular(self):
        """Two islands, source on one, sink on the other. The old answer to this on
        a real board was 'singular at node 763'; here it has to say what is wrong
        AND how many components it found."""
        two = [[[0.0, 0.0], [5.0, 0.0], [5.0, 2.0], [0.0, 2.0]],
               [[6.0, 0.0], [10.0, 0.0], [10.0, 2.0], [6.0, 2.0]]]
        r = CF.current_density_field(
            polygons=two, source=STRIP_SRC,
            sink={"x0_mm": 10.0, "y0_mm": -0.01, "x1_mm": 10.01, "y1_mm": 2.01},
            h_mm=0.2, levels=1, thickness_mm=T, current_a=1.0)
        self.assertEqual(r["status"], "error")
        self.assertEqual(r["reason_code"], "NOT_CONNECTED")
        self.assertIn("Components found: 2", r["hint"])

    def test_source_and_sink_cannot_be_the_same_copper(self):
        r = CF.current_density_field(polygons=STRIP, source=STRIP_SRC, sink=STRIP_SRC,
                                     h_mm=0.2, levels=1, thickness_mm=T,
                                     current_a=1.0)
        self.assertEqual(r["status"], "error")
        self.assertEqual(r["reason_code"], "CONTACT_NOT_ON_COPPER")
        self.assertIn("overlap", r["message"])

    def test_bad_input_is_refused_with_a_reason(self):
        cases = [
            (dict(polygons=None, paths=None), "BAD_GEOMETRY"),
            (dict(polygons=[[[0, 0], [1, 1]]]), "BAD_GEOMETRY"),
            (dict(paths=[{"points": [[0, 0], [1, 0]]}]), "BAD_GEOMETRY"),
            (dict(paths=[{"points": [[0, 0], [1, 0]], "width_mm": 0}]), "BAD_GEOMETRY"),
        ]
        for kw, code in cases:
            r = CF.current_density_field(source=STRIP_SRC, sink=STRIP_SNK, h_mm=0.2,
                                         thickness_mm=T, current_a=1.0, **kw)
            self.assertEqual(r["status"], "error", (kw, r))
            self.assertEqual(r["reason_code"], code, (kw, r))
        r = CF.current_density_field(polygons=STRIP, source=STRIP_SRC, sink=STRIP_SNK,
                                     h_mm=0.2, thickness_mm=T, current_a=-1.0)
        self.assertEqual(r["reason_code"], "BAD_PARAMETER")
        r = CF.current_density_field(polygons=STRIP, source=STRIP_SRC, sink=STRIP_SNK,
                                     h_mm=0.2, thickness_mm=0.0, current_a=1.0)
        self.assertEqual(r["reason_code"], "BAD_PARAMETER")
        r = CF.current_density_field(polygons=STRIP, source=None, sink=STRIP_SNK,
                                     h_mm=0.2, thickness_mm=T, current_a=1.0)
        self.assertEqual(r["reason_code"], "BAD_PARAMETER")


class TestTheEnvelopeTellsTheTruth(unittest.TestCase):
    def test_envelope_carries_model_basis_precision_and_assumptions(self):
        r = _strip()
        self.assertEqual(r["status"], "ok")
        for key in ("model", "basis", "precision", "assumptions", "unit"):
            self.assertTrue(r.get(key), key)
        self.assertIn("2-D", r["model"])
        self.assertIn("sheet", r["basis"])
        self.assertIn("PEAK current density is not", r["precision"])
        self.assertEqual(r["assumptions"]["thickness_mm"], T)
        self.assertEqual(r["assumptions"]["grid_h_mm"], 0.1)

    def test_the_level_table_is_a_real_refinement(self):
        r = _strip(levels=3)
        hs = [lv["h_mm"] for lv in r["levels"]]
        self.assertEqual(hs, [0.4, 0.2, 0.1])
        self.assertEqual([lv["converged"] for lv in r["levels"]], [True] * 3)
        for lv in r["levels"]:
            self.assertGreater(lv["unknowns"], 0)
            self.assertLess(lv["residual_v"], 1e-6)
        # unknowns have to grow with the refinement, or the levels are not levels
        self.assertLess(r["levels"][0]["unknowns"], r["levels"][-1]["unknowns"])

    def test_the_checks_are_reported_even_when_they_pass(self):
        r = _strip()
        c = r["checks"]
        self.assertLess(c["current_conservation_rel_error"], 1e-4)
        self.assertLess(c["power_balance_rel_error"], 1e-4)
        self.assertAlmostEqual(c["power_edge_sum_w"], c["power_i2r_w"],
                               delta=c["power_i2r_w"] * 1e-4)
        self.assertEqual(r["warnings"], [])

    def test_a_round_capped_trace_is_copper_too(self):
        """paths= is the form a layout tool gives you; the round caps make it
        slightly LONGER than the same nominal length as a rectangle, so R comes
        out a little under the rectangle (the disc contacts shorten it), and the
        contact edge itself shows up as a soft hot spot that is reported."""
        r = CF.current_density_field(paths=[{"points": [[1.0, 1.0], [19.0, 1.0]],
                                             "width_mm": 2.0}],
                                     source={"x_mm": 1.0, "y_mm": 1.0,
                                             "radius_mm": 0.6},
                                     sink={"x_mm": 19.0, "y_mm": 1.0,
                                           "radius_mm": 0.6},
                                     h_mm=0.1, levels=3, thickness_mm=T,
                                     current_a=1.0)
        self.assertEqual(r["status"], "ok", r)
        v = r["value"]
        # the reference section is the 2 mm width, not the tapered cap tip
        self.assertAlmostEqual(v["narrowest_section_mm2"], 2.0 * T, delta=1e-9)
        self.assertLess(v["resistance_ohm"], RHO / T * 20.0 / 2.0)
        self.assertGreater(v["resistance_ohm"], 0.7 * RHO / T * 20.0 / 2.0)
        # funneling into a 0.6 mm disc contact on a 2 mm trace is a real hot spot
        self.assertGreater(v["crowding_factor"], 1.2)
        self.assertTrue(r["warnings"], "a contact-edge hot spot must be flagged")

    def test_auto_grid_stays_inside_the_unknown_budget(self):
        r = CF.current_density_field(polygons=STRIP, source=STRIP_SRC, sink=STRIP_SNK,
                                     max_unknowns=4000, levels=2, thickness_mm=T,
                                     current_a=1.0)
        self.assertEqual(r["status"], "ok", r)
        for lv in r["levels"]:
            self.assertLessEqual(lv["unknowns"], 4000 * 1.2)


if __name__ == "__main__":
    unittest.main()
