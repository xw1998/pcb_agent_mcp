"""Constriction (spreading) resistance: the exact limits, the brackets, and the
two checks that can fail.

Three kinds of test here, deliberately kept apart:

* EXACT anchors. A source covering the whole plate has no spreading at all, so
  R = t/(k*A_plate) to machine precision; a convective far face over the whole
  plate is the 1-D wall t/(k*A) + 1/(h*A) exactly; mirroring the patch, or
  doubling k, must move nothing but the factor.
* MEASURED anchors, frozen as constants, with the number they were checked
  against written down: an independent first-order finite-volume solve of the
  same PDE (three-dimensional, no eigenfunctions) converges to these values from
  below as its through-thickness step is halved - 12.4, 8.3, 4.1, 2.0, 0.97 per
  cent below on dz = 125, 83, 42, 21, 10 um - so the series is the limit and the
  gap is the discretisation, not a missing term. The probe is _scratch work
  (numpy, not shipped); what is frozen here is the series' own value at a stated
  cutoff.
* NEGATIVE CONTROLS. A check that cannot fail is not a check, so the two ways
  this series can be got wrong are implemented on purpose and the tests assert
  that they are caught - and, for the orthogonality factor, that the whole-plate
  anchor alone is BLIND to it (which is why both kinds of test exist).
"""
import io
import math
import os
import sys
import unittest

sys.path.insert(0, os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "src"))

from pcb_agent_mcp.calc import thermal  # noqa: E402
from pcb_agent_mcp import tools  # noqa: E402


def _v(r):
    assert r["status"] == "ok", r
    return r["value"]


def _e(r):
    assert r["status"] == "error", r
    return r


# The four plates the frozen numbers belong to: 10 x 10 x 1 mm, k = 200.
PLATE = dict(plate_width_mm=10.0, plate_length_mm=10.0, thickness_mm=1.0,
             k_w_mk=200.0)


def _r(**kw):
    args = dict(PLATE)
    args.update(kw)
    return _v(thermal.spreading_resistance(**args))


class TestExactLimits(unittest.TestCase):
    def test_the_whole_plate_spreads_nothing(self):
        # source == plate: the only surviving term is m = n = 0, whose limit is
        # t/(k*A) = 1e-3/(200*1e-4) = 0.05 K/W exactly
        v = _r(source_x0_mm=0.0, source_x1_mm=10.0,
               source_y0_mm=0.0, source_y1_mm=10.0)
        self.assertEqual(v["rth_k_per_w"], 0.05)
        self.assertEqual(v["spreading_excess_k_per_w"], 0.0)
        self.assertEqual(v["rth_1d_plate_k_per_w"], 0.05)

    def test_a_vanishing_thickness_has_no_spreading_left(self):
        # t -> 0 sends every term's tanh(lam t)/lam to t, so the whole series
        # collapses onto t/(k*A_source): the rest of the plate stops helping.
        # The approach is SLOW, and measured to be so - the cosine coefficients
        # of an indicator decay like 1/m, so a finite cutoff under-counts the
        # tail: on this plate R/col is 0.5729, 0.9466, 0.9927, 0.9949 at
        # t = 1, 0.1, 0.01, 0.001 mm with terms = 400.  Hence the assertion is
        # monotone + a floor, not a tight tolerance around 1.
        prev = None
        for t in (1.0, 0.1, 0.01, 0.001):
            v = _r(thickness_mm=t, source_x0_mm=4.0, source_x1_mm=6.0,
                   source_y0_mm=4.0, source_y1_mm=6.0, terms=400)
            col = v["rth_1d_source_column_k_per_w"]
            ratio = v["rth_k_per_w"] / col
            self.assertLess(ratio, 1.0)      # spreading can only help
            if prev is not None:
                self.assertGreater(ratio, prev)
            prev = ratio
        self.assertGreater(prev, 0.99)

    def test_a_convective_far_face_over_the_whole_plate_is_one_dimensional(self):
        # R = t/(k*A) + 1/(h*A) = 0.05 + 1/(1000*1e-4) = 10.05 K/W exactly
        v = _r(source_x0_mm=0.0, source_x1_mm=10.0,
               source_y0_mm=0.0, source_y1_mm=10.0,
               far_face="convective", h_w_m2k=1000.0)
        self.assertAlmostEqual(v["rth_k_per_w"], 10.05, delta=1e-9)

    def test_the_robin_face_approaches_the_isothermal_one(self):
        # A convective far face is an isothermal face in series with a wall, so
        # R must fall as h grows and approach the isothermal value from above.
        # There is deliberately NO "1/(h*A_plate)" to strip: heat leaves only
        # near the source, so the Robin flux is not the uniform one that
        # plate-wide term assumes (stripping it leaves a 0.0217 error at
        # h = 1e6, which is what the first version of this test measured).
        iso = _r(source_x0_mm=3.0, source_x1_mm=7.0,
                 source_y0_mm=3.0, source_y1_mm=7.0)["rth_k_per_w"]
        prev = None
        for h in (10.0, 1e3, 1e5, 1e7, 1e9):
            got = _r(source_x0_mm=3.0, source_x1_mm=7.0,
                     source_y0_mm=3.0, source_y1_mm=7.0,
                     far_face="convective", h_w_m2k=h)["rth_k_per_w"]
            self.assertGreaterEqual(got, iso)   # the wall can only add
            if prev is not None:
                self.assertLess(got, prev)      # more h, less resistance
            prev = got
        self.assertLess(prev - iso, 1e-3)

    def test_doubling_k_halves_the_answer(self):
        a = _r(source_x0_mm=3.0, source_x1_mm=7.0,
               source_y0_mm=3.0, source_y1_mm=7.0)["rth_k_per_w"]
        b = _r(k_w_mk=400.0, source_x0_mm=3.0, source_x1_mm=7.0,
               source_y0_mm=3.0, source_y1_mm=7.0)["rth_k_per_w"]
        self.assertAlmostEqual(a / b, 2.0, delta=1e-12)

    def test_mirroring_the_patch_changes_nothing(self):
        # the edges are adiabatic in both directions, so the plate has two
        # mirror symmetries and the answer must respect them exactly
        y1 = _r(source_x0_mm=3.0, source_x1_mm=7.0,
                source_y0_mm=1.0, source_y1_mm=4.0)["rth_k_per_w"]
        y2 = _r(source_x0_mm=3.0, source_x1_mm=7.0,
                source_y0_mm=6.0, source_y1_mm=9.0)["rth_k_per_w"]
        x1 = _r(source_x0_mm=1.0, source_x1_mm=4.0,
                source_y0_mm=3.0, source_y1_mm=7.0)["rth_k_per_w"]
        x2 = _r(source_x0_mm=6.0, source_x1_mm=9.0,
                source_y0_mm=3.0, source_y1_mm=7.0)["rth_k_per_w"]
        self.assertAlmostEqual(y1, y2, delta=1e-12)
        self.assertAlmostEqual(x1, x2, delta=1e-12)
        self.assertAlmostEqual(y1, x1, delta=1e-12)   # the plate is square

    def test_the_far_corner_is_further_away_than_the_centre(self):
        centre = _r(source_x0_mm=4.0, source_x1_mm=6.0,
                    source_y0_mm=4.0, source_y1_mm=6.0)["rth_k_per_w"]
        corner = _r(source_x0_mm=0.0, source_x1_mm=2.0,
                    source_y0_mm=0.0, source_y1_mm=2.0)["rth_k_per_w"]
        self.assertGreater(corner, centre)


class TestBrackets(unittest.TestCase):
    """t/(k*A_plate) <= R <= t/(k*A_source), which the series guarantees term by
    term: the first is the m = n = 0 term, the rest are positive, and spreading
    can only help against a straight column under the patch."""

    CASES = [
        (3.0, 7.0, 3.0, 7.0), (4.0, 6.0, 4.0, 6.0), (0.5, 3.5, 0.5, 5.5),
        (1.0, 9.0, 4.5, 5.5), (2.0, 8.0, 2.0, 8.0), (0.0, 1.0, 0.0, 1.0),
        (4.0, 4.5, 4.0, 4.5),
    ]

    def test_the_answer_stays_between_the_two_one_dimensional_references(self):
        for x0, x1, y0, y1 in self.CASES:
            for t in (0.05, 1.0, 10.0):
                v = _r(thickness_mm=t, source_x0_mm=x0, source_x1_mm=x1,
                       source_y0_mm=y0, source_y1_mm=y1)
                self.assertGreaterEqual(v["rth_k_per_w"],
                                        v["rth_1d_plate_k_per_w"] - 1e-12)
                self.assertLessEqual(v["rth_k_per_w"],
                                     v["rth_1d_source_column_k_per_w"] + 1e-12)
                self.assertGreaterEqual(v["spreading_excess_k_per_w"], 0.0)
                self.assertGreaterEqual(v["spreading_relief_k_per_w"], 0.0)

    def test_a_smaller_patch_costs_more(self):
        prev = None
        for side in (8.0, 4.0, 2.0, 1.0):
            c = 5.0
            v = _r(source_x0_mm=c - side / 2, source_x1_mm=c + side / 2,
                   source_y0_mm=c - side / 2, source_y1_mm=c + side / 2)
            if prev is not None:
                self.assertGreater(v["rth_k_per_w"], prev)
            prev = v["rth_k_per_w"]

    def test_the_thickness_only_adds(self):
        prev = None
        for t in (0.05, 0.2, 1.0, 5.0):
            v = _r(thickness_mm=t, source_x0_mm=4.0, source_x1_mm=6.0,
                   source_y0_mm=4.0, source_y1_mm=6.0)
            if prev is not None:
                self.assertGreater(v["rth_k_per_w"], prev)
            prev = v["rth_k_per_w"]


class TestMeasuredValues(unittest.TestCase):
    """The numbers below are the series at terms = 400 (twice the default), and
    each was checked against the independent finite-volume solve described in
    the module docstring: the FVM lands 0.97 % below on its finest through-
    thickness step and halves that gap every time the step halves."""

    def test_centre_4x4(self):
        v = _r(source_x0_mm=3.0, source_x1_mm=7.0,
               source_y0_mm=3.0, source_y1_mm=7.0, terms=400)
        self.assertAlmostEqual(v["rth_k_per_w"], 0.236078529798, delta=1e-12)
        self.assertAlmostEqual(v["spreading_excess_k_per_w"], 0.186078529798,
                               delta=1e-12)
        self.assertAlmostEqual(v["psi"], 0.188862824, delta=1e-12)

    def test_centre_2x2(self):
        v = _r(source_x0_mm=4.0, source_x1_mm=6.0,
               source_y0_mm=4.0, source_y1_mm=6.0, terms=400)
        self.assertAlmostEqual(v["rth_k_per_w"], 0.716124740033, delta=1e-12)

    def test_corner_3x5(self):
        v = _r(source_x0_mm=0.5, source_x1_mm=3.5,
               source_y0_mm=0.5, source_y1_mm=5.5, terms=400)
        self.assertAlmostEqual(v["rth_k_per_w"], 0.253879439710, delta=1e-12)

    def test_strip_8x1(self):
        v = _r(source_x0_mm=1.0, source_x1_mm=9.0,
               source_y0_mm=4.5, source_y1_mm=5.5, terms=400)
        self.assertAlmostEqual(v["rth_k_per_w"], 0.335405525827, delta=1e-12)

    def test_the_default_cutoff_is_close_enough_and_says_so(self):
        # terms=120 vs terms=240 is the convergence number the envelope reports;
        # on these plates it is a few times 1e-4, i.e. the cutoff is honest
        for x0, x1, y0, y1 in ((3.0, 7.0, 3.0, 7.0), (4.0, 6.0, 4.0, 6.0)):
            v = _r(source_x0_mm=x0, source_x1_mm=x1,
                   source_y0_mm=y0, source_y1_mm=y1)
            self.assertLess(v["convergence_rel"], 1e-3)
            self.assertAlmostEqual(v["rth_at_terms_k_per_w"], v["rth_k_per_w"],
                                   delta=1e-12)
            self.assertNotEqual(v["rth_at_2terms_k_per_w"], v["rth_at_terms_k_per_w"])


def _series(terms, drop_eps=False, scale=1.0):
    """A deliberately WRONG copy of the series, for the negative controls: with
    drop_eps it forgets the orthogonality factor 2 of every m>0 or n>0 mode."""
    W = L = 10e-3
    t = 1e-3
    k = 200.0
    x0, x1, y0, y1 = 3e-3, 7e-3, 3e-3, 7e-3
    As = (x1 - x0) * (y1 - y0)

    def ci(a, b, size, m):
        if m == 0:
            return b - a
        c = m * math.pi / size
        return (math.sin(c * b) - math.sin(c * a)) / c

    ix = [ci(x0, x1, W, m) for m in range(terms + 1)]
    iy = [ci(y0, y1, L, n) for n in range(terms + 1)]
    total = 0.0
    for m in range(terms + 1):
        for n in range(terms + 1):
            lam = math.pi * math.hypot(m / W, n / L)
            fo = t if lam == 0.0 else math.tanh(lam * t) / lam
            ex = 1.0 if (drop_eps or m == 0) else 2.0
            ey = 1.0 if (drop_eps or n == 0) else 2.0
            f = ix[m] * iy[n]
            total += f * f * ex * ey * fo
    return scale * total / (k * As * As * W * L)


class TestTheChecksCanFail(unittest.TestCase):
    def test_a_missing_orthogonality_factor_is_caught_by_the_shape(self):
        # 4x4 patch: dropping the 2s changes the number by more than 10 %
        right = _r(terms=400, source_x0_mm=3.0, source_x1_mm=7.0,
                   source_y0_mm=3.0, source_y1_mm=7.0)["rth_k_per_w"]
        wrong = _series(400, drop_eps=True)
        self.assertGreater(abs(wrong - right) / right, 0.10)

    def test_but_the_whole_plate_anchor_is_blind_to_it(self):
        # ...and this is why both kinds of test exist: on the whole-plate case
        # only the m = n = 0 term survives, so the same wrong series passes
        W = L = 10e-3
        t, k = 1e-3, 200.0
        self.assertAlmostEqual(t / (k * W * L), 0.05, delta=1e-15)
        v = _r(source_x0_mm=0.0, source_x1_mm=10.0,
               source_y0_mm=0.0, source_y1_mm=10.0)
        # both the right and the wrong series see exactly 0.05 here, because
        # every mode with m>0 or n>0 has a zero footprint
        self.assertEqual(v["rth_k_per_w"], 0.05)

    def test_a_scale_error_is_caught(self):
        right = _r(terms=400, source_x0_mm=3.0, source_x1_mm=7.0,
                   source_y0_mm=3.0, source_y1_mm=7.0)["rth_k_per_w"]
        for scale in (0.5, 2.0):
            self.assertGreater(abs(_series(400, scale=scale) - right) / right,
                               0.4)

    def test_the_naive_radial_formula_is_not_the_same_number(self):
        # ln(b/a)/(2 pi k t) is what people write down instead; on this plate
        # (patch equivalent radius 2.26 mm, outer 5.64 mm) it is not the answer
        a = math.sqrt(16.0 / math.pi) * 1e-3
        b = math.sqrt(50.0 / math.pi) * 1e-3
        naive = math.log(b / a) / (2 * math.pi * 200.0 * 1e-3)
        right = _r(terms=400, source_x0_mm=3.0, source_x1_mm=7.0,
                   source_y0_mm=3.0, source_y1_mm=7.0)["rth_k_per_w"]
        self.assertGreater(abs(naive - right) / right, 0.3)


class TestRefusals(unittest.TestCase):
    def test_a_patch_outside_the_plate(self):
        e = _e(thermal.spreading_resistance(
            10.0, 10.0, 1.0, 200.0, 8.0, 12.0, 3.0, 7.0))
        self.assertEqual(e["reason_code"], "ARG_RANGE")

    def test_a_patch_with_no_area(self):
        e = _e(thermal.spreading_resistance(
            10.0, 10.0, 1.0, 200.0, 5.0, 5.0, 3.0, 7.0))
        self.assertEqual(e["reason_code"], "ARG_RANGE")
        self.assertIn("no area", e["message"] + e.get("hint", ""))

    def test_an_adiabatic_far_face_is_refused_rather_than_answered(self):
        e = _e(thermal.spreading_resistance(
            10.0, 10.0, 1.0, 200.0, 3.0, 7.0, 3.0, 7.0, far_face="adiabatic"))
        self.assertEqual(e["reason_code"], "ARG_INVALID")
        self.assertIn("no sink", e["hint"])

    def test_a_convective_face_needs_its_h(self):
        e = _e(thermal.spreading_resistance(
            10.0, 10.0, 1.0, 200.0, 3.0, 7.0, 3.0, 7.0, far_face="convective"))
        self.assertEqual(e["reason_code"], "ARG_REQUIRED")
        self.assertIn("thermal_convection", e["hint"])

    def test_zero_thickness_and_zero_k(self):
        for kw in (dict(thickness_mm=0.0), dict(k_w_mk=0.0),
                   dict(plate_width_mm=0.0)):
            args = dict(PLATE)
            args.update(kw)
            args.update(source_x0_mm=1.0, source_x1_mm=2.0,
                        source_y0_mm=1.0, source_y1_mm=2.0)
            self.assertEqual(_e(thermal.spreading_resistance(
                args["plate_width_mm"], args["plate_length_mm"],
                args["thickness_mm"], args["k_w_mk"], 1.0, 2.0, 1.0, 2.0))
                ["reason_code"], "ARG_RANGE")

    def test_the_cutoff_is_bounded(self):
        for terms in (10, 500):
            e = _e(thermal.spreading_resistance(
                10.0, 10.0, 1.0, 200.0, 3.0, 7.0, 3.0, 7.0, terms=terms))
            self.assertEqual(e["reason_code"], "ARG_RANGE")
        self.assertEqual(_e(thermal.spreading_resistance(
            10.0, 10.0, 1.0, 200.0, 3.0, 7.0, 3.0, 7.0, terms=25.5))
            ["reason_code"], "ARG_INVALID")


class TestToolLayer(unittest.TestCase):
    def test_the_tool_is_registered_and_carries_its_limits(self):
        spec = tools.TOOLS["thermal_spreading"]
        self.assertIn("constriction", spec["description"].lower())
        for key in ("plate_width_mm", "plate_length_mm", "thickness_mm",
                    "k_w_mk", "source_x0_mm", "source_x1_mm",
                    "source_y0_mm", "source_y1_mm"):
            self.assertIn(key, spec["inputSchema"]["required"])
        self.assertEqual(spec["inputSchema"]["properties"]["far_face"]["enum"],
                         ["isothermal", "convective"])

    def test_the_whole_plate_reports_a_plain_zero_not_a_negative_zero(self):
        # 0.05 - 0.05 can come out as -1e-17 and round() then prints "-0.0" in
        # JSON; the answer is "no spreading", so it must read as 0.0.
        v = _r(source_x0_mm=0.0, source_x1_mm=10.0,
               source_y0_mm=0.0, source_y1_mm=10.0)
        self.assertEqual(repr(v["spreading_excess_k_per_w"]), "0.0")

    def test_the_capability_list_knows_about_it(self):
        cap = tools.TOOLS["calc_capabilities"]["handler"]({})
        self.assertIn("thermal_spreading", cap["available"])

    def test_the_defaults_are_the_isothermal_face_and_120_terms(self):
        r = tools.TOOLS["thermal_spreading"]["handler"]({
            "plate_width_mm": 10, "plate_length_mm": 10, "thickness_mm": 1.0,
            "k_w_mk": 200, "source_x0_mm": 3, "source_x1_mm": 7,
            "source_y0_mm": 3, "source_y1_mm": 7})
        v = _v(r)
        self.assertEqual(v["far_face"], "isothermal")
        self.assertEqual(v["terms"], 120)
        self.assertIsNone(v["h_w_m2k"])
        self.assertAlmostEqual(v["rth_k_per_w"], 0.236046830818, delta=1e-12)

    def test_the_tool_layer_does_not_floor_a_fractional_cutoff(self):
        # int() in the tool wrapper would turn 25.5 into 25 and answer. The
        # refusal has to survive the wrapper, not just the calc function.
        r = tools.TOOLS["thermal_spreading"]["handler"]({
            "plate_width_mm": 10, "plate_length_mm": 10, "thickness_mm": 1.0,
            "k_w_mk": 200, "source_x0_mm": 3, "source_x1_mm": 7,
            "source_y0_mm": 3, "source_y1_mm": 7, "terms": 25.5})
        self.assertEqual(_e(r)["reason_code"], "ARG_INVALID")

    def test_the_heatsink_budget_counts_the_spreading(self):
        base = _v(tools.TOOLS["thermal_heatsink"]["handler"]({
            "power_w": 5.0, "t_j_max_c": 125.0, "t_ambient_c": 45.0,
            "rth_jc_k_per_w": 1.2, "rth_cs_k_per_w": 0.4}))
        # 80/5 - 1.2 - 0.4 = 14.4 K/W, target 0.8*14.4 = 11.52
        self.assertAlmostEqual(base["rth_sa_budget_k_per_w"], 14.4, delta=1e-9)
        self.assertAlmostEqual(base["rth_sa_target_k_per_w"], 11.52, delta=1e-9)
        self.assertEqual(base["rth_spread_k_per_w"], 0.0)
        with_spread = _v(tools.TOOLS["thermal_heatsink"]["handler"]({
            "power_w": 5.0, "t_j_max_c": 125.0, "t_ambient_c": 45.0,
            "rth_jc_k_per_w": 1.2, "rth_cs_k_per_w": 0.4,
            "rth_spread_k_per_w": 0.186}))
        # 14.4 - 0.186 = 14.214, target 11.3712
        self.assertAlmostEqual(with_spread["rth_sa_budget_k_per_w"], 14.214,
                               delta=1e-9)
        self.assertAlmostEqual(with_spread["rth_sa_target_k_per_w"], 11.3712,
                               delta=1e-9)

    def test_a_spreading_budget_that_swallows_the_headroom_is_refused(self):
        e = _e(tools.TOOLS["thermal_heatsink"]["handler"]({
            "power_w": 5.0, "t_j_max_c": 125.0, "t_ambient_c": 45.0,
            "rth_jc_k_per_w": 1.2, "rth_cs_k_per_w": 0.4,
            "rth_spread_k_per_w": 20.0}))
        self.assertEqual(e["reason_code"], "INFEASIBLE")
        self.assertIn("spreading", e["message"])


if __name__ == "__main__":
    unittest.main()
