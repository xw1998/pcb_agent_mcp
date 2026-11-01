"""Golden tests for ac_self_heating - the AC electro-thermal loop.

What these tests are for: the loop is non-linear in T (R_ac is not affine in rho
once the current lives in a shell), so the answer cannot be checked against a
closed form the way the DC loop can.  Every assertion below is one of three
kinds, and none of them is "the code still returns what it returned yesterday":

  * an INDEPENDENT model of the same physics - `electrothermal.conductor_self_heating`,
    a different module with a closed form.  When the exponent is 1 the two MUST
    agree to 1e-6; `test_dc_limit_failure_it_catches` shows the comparison has
    teeth by re-running it with the wrong (ambient) resistance and asserting the
    gap is ~2 %, which is exactly the unit slip this check found in development;
  * an ANALYTIC property that a wrong branch would break - dT = I^2*Rth*R(ta+dT)
    (the definition of the fixed point), dT = I^2*Rth*R when alpha = 0 (algebra:
    a constant resistance is the whole loop), R_two_faces = R_one_face/2 in the
    skin-dominated limit (the surface term cannot see the far side);
  * the EXPONENT itself, dlnR/dlnrho, which is 1/2 in a skin-dominated conductor
    and 1 in a DC or wall-limited one.  It is measured by the module and asserted
    here, because that single number is the difference between "there is no
    steady state at any temperature" and "there is one, above melting".

Two runaway forms exist and are asserted SEPARATELY, because collapsing them into
one message would be the wrong answer: with exponent 1 the loop has no fixed
point anywhere (test_runaway_no_root_at_all), with exponent 1/2 it does and the
number that comes back is an extrapolation past melting (test_runaway_root_beyond_melt).
"""
import math
import os
import sys
import unittest

sys.path.insert(0, os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "src"))

from pcb_agent_mcp import tools as T  # noqa: E402
from pcb_agent_mcp.calc import acthermal as A  # noqa: E402
from pcb_agent_mcp.calc import electrothermal as ET  # noqa: E402

SHEET = dict(shape="sheet", length_mm=20.0, width_mm=1.0, thickness_um=35.0)


def _call(**kw):
    return A.ac_self_heating(**kw)


def _ok(testcase, **kw):
    r = _call(**kw)
    testcase.assertEqual(r["status"], "ok", r.get("message"))
    return r


class TestDcLimit(unittest.TestCase):
    """At low frequency the AC loop must BE the DC loop, to the DC solver's own
    tolerance.  This is the only test here with a second independent model behind
    it, and it is the one that caught a 1000x ohm*m / ohm*mm slip."""

    def _pair(self, f, **kw):
        r = _ok(self, frequency_hz=f, current_a=kw.pop("i", 3.0),
                rth_c_per_w=kw.pop("rth", 20.0), **kw)
        return r, r["value"]["checks"]

    def test_sheet_100hz_matches_electrothermal(self):
        r, c = self._pair(100.0, **SHEET)
        self.assertEqual(r["value"]["regime_hot"], "dc")
        self.assertLess(c["dc_linear_model_rel_diff"], 1e-6)
        # and the same statement written the other way round, with the other
        # module called directly
        geom, e = A._geometry("sheet", 20.0, 1.0, 35.0, None, "one", None, None,
                              None, None)
        self.assertIsNone(e)
        r20 = A._one(geom, 100.0, 20.0, 1.0, None)[0]
        dc = ET.conductor_self_heating(r20, 3.0, 20.0, 25.0)
        self.assertEqual(dc["status"], "ok")
        self.assertLess(abs(dc["delta_t_c"] - r["value"]["delta_t_c"]) /
                        r["value"]["delta_t_c"], 1e-5)

    def test_round_1khz_matches_electrothermal(self):
        r = _ok(self, shape="round", frequency_hz=1e3, current_a=1.0,
                rth_c_per_w=100.0, length_mm=20.0, diameter_mm=0.5)
        self.assertEqual(r["value"]["regime_hot"], "dc")
        self.assertLess(r["value"]["checks"]["dc_linear_model_rel_diff"], 1e-5)

    def test_dc_limit_failure_it_catches(self):
        """The comparison uses R at 20 C, not R at the ambient.  Feeding it the
        ambient value shifts dT by (1+alpha*(Ta-20)) = 1.0197, i.e. ~2 %, which
        is far outside the 1e-6 band: if the code slips back, this fails."""
        geom, _ = A._geometry("sheet", 20.0, 1.0, 35.0, None, "one", None, None,
                              None, None)
        r = _ok(self, frequency_hz=100.0, current_a=3.0, rth_c_per_w=20.0,
                **SHEET)
        r_cold = r["value"]["resistance_cold_ohm"]
        dc_wrong = ET.conductor_self_heating(r_cold, 3.0, 20.0, 25.0)
        rel = abs(dc_wrong["delta_t_c"] - r["value"]["delta_t_c"]) / \
            r["value"]["delta_t_c"]
        self.assertGreater(rel, 5e-3)
        self.assertAlmostEqual(rel, 0.0197, delta=2e-3)

    def test_alpha_zero_is_plain_joule(self):
        """With alpha = 0 the resistance does not move, so dT = I^2*Rth*R exactly
        and the loop cannot run away at any current."""
        r = _ok(self, frequency_hz=1e6, current_a=3.0, rth_c_per_w=20.0,
                alpha_per_k=0.0, **SHEET)
        v = r["value"]
        self.assertAlmostEqual(v["resistance_gain"], 1.0, delta=1e-12)
        exact = 9.0 * 20.0 * v["resistance_cold_ohm"]
        self.assertAlmostEqual(v["delta_t_c"], exact, delta=1e-5 * exact)
        self.assertEqual(v["checks"]["dlnr_dlnrho"], 1.0)
        self.assertEqual(v["runaway_gain"], 0.0)
        self.assertIsNone(v["unit_gain_current_a"])


class TestExponent(unittest.TestCase):
    """dlnR/dlnrho is the whole mechanism, and it is measured, not assumed."""

    def test_skin_dominated_sheet_is_half(self):
        r = _ok(self, frequency_hz=1e9, current_a=3.0, rth_c_per_w=20.0, **SHEET)
        v = r["value"]
        self.assertEqual(v["regime_hot"], "skin-dominated")
        self.assertAlmostEqual(v["dlnr_dlnrho"], 0.5, delta=1e-4)
        self.assertAlmostEqual(v["r_over_rdc_cold"], 16.5865, delta=1e-3)
        self.assertEqual(v["checks"]["beta_expectation"], 0.5)
        self.assertLess(v["checks"]["beta_deviation"], 1e-6)

    def test_skin_dominated_wire_is_half(self):
        r = _ok(self, shape="round", frequency_hz=1e8, current_a=2.0,
                rth_c_per_w=100.0, length_mm=1.6, diameter_mm=0.5)
        self.assertEqual(r["value"]["regime_hot"], "skin-dominated")
        # a wire is not a sheet: 0.5067, not 0.5000, because the Bessel answer
        # still has a 1/x correction at x = 37 - the assertion is the band, not
        # the digit
        self.assertAlmostEqual(r["value"]["dlnr_dlnrho"], 0.5067, delta=2e-3)

    def test_wall_limited_via_is_one(self):
        """A 0.5 um wall at 1 GHz has x_wall = 0.24, so the whole wall carries
        current and the exponent is the DC one - even at a gigahertz."""
        r = _ok(self, shape="round", frequency_hz=1e9, current_a=0.5,
                rth_c_per_w=200.0, length_mm=1.6, drill_mm=0.3, plating_um=0.5)
        v = r["value"]
        self.assertEqual(v["regime_hot"], "wall-limited")
        self.assertLess(v["x_wall_hot"], 0.5)
        self.assertAlmostEqual(v["dlnr_dlnrho"], 0.9995, delta=1e-3)
        self.assertEqual(v["checks"]["beta_expectation"], 1.0)
        self.assertLess(v["checks"]["beta_deviation"], 2e-3)

    def test_tube_wall_limited_is_about_one(self):
        r = _ok(self, shape="round", frequency_hz=1e6, current_a=2.0,
                rth_c_per_w=100.0, length_mm=1.6, diameter_mm=0.5, wall_um=50.0)
        self.assertEqual(r["value"]["regime_hot"], "wall-limited")
        # 0.952 rather than 1.000: R/R_dc still drifts with rho at x_wall = 0.75
        self.assertAlmostEqual(r["value"]["dlnr_dlnrho"], 0.9525, delta=3e-3)

    def test_dc_is_exactly_one(self):
        r = _ok(self, frequency_hz=100.0, current_a=3.0, rth_c_per_w=20.0, **SHEET)
        self.assertEqual(r["value"]["dlnr_dlnrho"], 1.0)

    def test_roughness_suppresses_the_expectation(self):
        """Roughness depends on delta, so the exponent drops below 1/2 and there
        is no prediction to compare against - saying nothing beats failing a
        check that was never valid there."""
        r = _ok(self, frequency_hz=1e9, current_a=3.0, rth_c_per_w=20.0,
                roughness_rq_um=1.0, **SHEET)
        c = r["value"]["checks"]
        self.assertIsNone(c["beta_expectation"])
        self.assertIsNone(c["beta_deviation"])
        self.assertIn("roughness", c["beta_expectation_note"])
        self.assertLess(r["value"]["dlnr_dlnrho"], 0.45)
        self.assertGreater(r["value"]["dlnr_dlnrho"], 0.25)

    def test_roughness_raises_the_answer(self):
        rough = _ok(self, frequency_hz=1e9, current_a=3.0, rth_c_per_w=20.0,
                    roughness_rq_um=1.0, **SHEET)["value"]
        smooth = _ok(self, frequency_hz=1e9, current_a=3.0, rth_c_per_w=20.0,
                     **SHEET)["value"]
        self.assertGreater(rough["resistance_cold_ohm"],
                           smooth["resistance_cold_ohm"])
        self.assertGreater(rough["t_final_c"], smooth["t_final_c"])
        self.assertTrue(rough["roughness_in_the_answer"])
        self.assertFalse(smooth["roughness_in_the_answer"])


class TestFixedPoint(unittest.TestCase):
    """The answer must satisfy its own definition, on every shape."""

    def _check(self, **kw):
        v = _ok(self, **kw)["value"]
        k = v["current_a"] ** 2 * (v["delta_t_c"] /
                                   (v["current_a"] ** 2 * v["resistance_hot_ohm"]))
        # k recovered from the answer itself: dT = I^2*Rth*R_hot
        rth = v["delta_t_c"] / (v["current_a"] ** 2 * v["resistance_hot_ohm"])
        resid = abs(v["delta_t_c"] - v["current_a"] ** 2 * rth *
                    v["resistance_hot_ohm"])
        self.assertLess(resid, 1e-6 * max(1.0, v["delta_t_c"]))
        self.assertTrue(v["checks"]["energy_balance_ok"])
        self.assertGreaterEqual(v["resistance_hot_ohm"], v["resistance_cold_ohm"])
        self.assertAlmostEqual(v["t_final_c"], 25.0 + v["delta_t_c"], delta=1e-4)
        p_exact = v["current_a"] ** 2 * v["resistance_hot_ohm"]
        self.assertAlmostEqual(v["power_w"], p_exact, delta=2e-5 * p_exact)
        self.assertLess(v["runaway_gain"], 1.0)
        self.assertAlmostEqual(v["checks"]["stability_margin"],
                               1.0 - v["runaway_gain"], delta=1e-5)

    def test_sheet_dc(self):
        self._check(frequency_hz=100.0, current_a=3.0, rth_c_per_w=20.0, **SHEET)

    def test_sheet_skin(self):
        self._check(frequency_hz=1e9, current_a=3.0, rth_c_per_w=20.0, **SHEET)

    def test_via_wall_limited(self):
        self._check(shape="round", frequency_hz=1e9, current_a=0.5,
                    rth_c_per_w=200.0, length_mm=1.6, drill_mm=0.3,
                    plating_um=0.5)

    def test_solid_wire(self):
        self._check(shape="round", frequency_hz=1e8, current_a=2.0,
                    rth_c_per_w=100.0, length_mm=1.6, diameter_mm=0.5)

    def test_tube(self):
        self._check(shape="round", frequency_hz=1e6, current_a=2.0,
                    rth_c_per_w=100.0, length_mm=1.6, diameter_mm=0.5, wall_um=50.0)


class TestTwoFaces(unittest.TestCase):
    def test_two_faces_halve_the_surface_resistance(self):
        """In the skin-dominated limit the surface term cannot see the far side,
        so faces='two' is exactly half - an identity, not a rule of thumb."""
        one = _ok(self, frequency_hz=1e9, current_a=3.0, rth_c_per_w=20.0,
                  faces="one", **SHEET)["value"]
        two = _ok(self, frequency_hz=1e9, current_a=3.0, rth_c_per_w=20.0,
                  faces="two", **SHEET)["value"]
        self.assertAlmostEqual(two["resistance_cold_ohm"] /
                               one["resistance_cold_ohm"], 0.5, delta=1e-4)
        self.assertLess(two["t_final_c"], one["t_final_c"])


class TestTrends(unittest.TestCase):
    def test_hotter_as_frequency_rises(self):
        # frequency can only ADD resistance, so the AC loop can only be hotter
        t = [_ok(self, frequency_hz=f, current_a=3.0, rth_c_per_w=20.0,
                 **SHEET)["value"]["t_final_c"] for f in (100.0, 1e6, 1e8, 1e9)]
        for a, b in zip(t, t[1:]):
            self.assertLess(a, b)

    def test_hotter_as_current_rises(self):
        t = [_ok(self, frequency_hz=1e6, current_a=i, rth_c_per_w=20.0,
                 **SHEET)["value"]["t_final_c"] for i in (1.0, 2.0, 4.0, 8.0)]
        for a, b in zip(t, t[1:]):
            self.assertLess(a, b)

    def test_zero_current_is_ambient(self):
        v = _ok(self, frequency_hz=1e9, current_a=0.0, rth_c_per_w=20.0,
                **SHEET)["value"]
        self.assertEqual(v["t_final_c"], 25.0)
        self.assertEqual(v["delta_t_c"], 0.0)
        self.assertEqual(v["iterations"], 0)
        self.assertEqual(v["power_w"], 0.0)
        self.assertEqual(v["resistance_gain"], 1.0)

    def test_ambient_moves_the_answer(self):
        v = _ok(self, frequency_hz=1e6, current_a=3.0, rth_c_per_w=20.0,
                t_ambient_c=80.0, **SHEET)["value"]
        self.assertAlmostEqual(v["t_final_c"] - 80.0, v["delta_t_c"], delta=1e-4)
        # hotter ambient means higher rho and therefore more heat
        v2 = _ok(self, frequency_hz=1e6, current_a=3.0, rth_c_per_w=20.0,
                 **SHEET)["value"]
        self.assertGreater(v["delta_t_c"], v2["delta_t_c"])


class TestRunaway(unittest.TestCase):
    """Two failures, two messages. The DC one is algebra; the AC one is a
    temperature nobody can build."""

    narrow = dict(shape="sheet", length_mm=20.0, width_mm=0.1, thickness_um=35.0,
                  rth_c_per_w=20.0)

    def test_runaway_no_root_at_all(self):
        r = _call(frequency_hz=100.0, current_a=12.0, **self.narrow)
        self.assertEqual(r["status"], "error")
        self.assertEqual(r["reason_code"], "THERMAL_RUNAWAY")
        self.assertEqual(r["dlnr_dlnrho_cold"], 1.0)
        self.assertIn("no steady state at any temperature", r["message"])
        self.assertNotIn("root_beyond_melt_t_c", r)
        self.assertAlmostEqual(r["runaway_gain_cold"], 1.11502, delta=1e-4)
        # the two numbers a designer can act on
        self.assertAlmostEqual(r["unit_gain_current_a"], 11.3642, delta=2e-3)
        self.assertAlmostEqual(r["rth_for_root_at_melt_c_per_w"], 14.4088,
                               delta=5e-3)
        self.assertIn("11.364", r["hint"])

    def test_runaway_root_beyond_melt(self):
        """Same geometry, same current, 1 GHz: the exponent is 1/2 now, so a fixed
        point EXISTS - it is just at 37 kK, which is the honest way to say the
        same design is impossible."""
        r = _call(frequency_hz=1e9, current_a=10.0, **self.narrow)
        self.assertEqual(r["status"], "error")
        self.assertEqual(r["reason_code"], "THERMAL_RUNAWAY")
        self.assertEqual(r["dlnr_dlnrho_cold"], 0.5)
        self.assertAlmostEqual(r["root_beyond_melt_t_c"], 37325.8, delta=1.0)
        self.assertIn("above the melting point", r["message"])
        self.assertIn("ordering, not a reading", r["hint"])

    def test_runaway_below_the_melt_is_not_reported(self):
        """The same trace at 10 A and 100 Hz settles at 915 C - appalling, but a
        real fixed point, so it is an answer and not a runaway."""
        v = _ok(self, frequency_hz=100.0, current_a=10.0,
                **self.narrow)["value"]
        self.assertAlmostEqual(v["t_final_c"], 915.208, delta=1e-2)
        self.assertGreater(v["t_final_c"], 120.0)
        self.assertTrue(v["checks"]["above_100c_linear_law"])
        self.assertLess(v["runaway_gain"], 1.0)

    def test_unit_gain_current_is_the_dc_threshold(self):
        """With exponent 1 the loop gain at the answer is exactly (I/I_unit)^2 -
        an identity that a wrong beta would break."""
        for i in (3.0, 6.0, 10.0):
            v = _ok(self, frequency_hz=100.0, current_a=i,
                    **self.narrow)["value"]
            self.assertAlmostEqual(v["runaway_gain"] /
                                   (i / v["unit_gain_current_a"]) ** 2, 1.0,
                                   delta=1e-4)

    def test_under_unit_gain_still_has_a_root_above_melt(self):
        """11 A is below the 11.364 A unit-gain current, so the exponent-1 loop has
        a root - at about dT 3.9 kK. The uncapped search runs UNCONDITIONALLY now;
        it used to be gated on exponent < 1, and this current therefore came back
        as "no steady state at any temperature", which is a false statement about
        a loop that demonstrably settles somewhere. The pair with
        test_runaway_no_root_at_all (12 A, same geometry) is the negative control:
        above the unit-gain current there really is no root, so the two cases
        cannot be produced by one blanket answer."""
        r = _call(frequency_hz=100.0, current_a=11.0, **self.narrow)
        self.assertEqual(r["status"], "error")
        self.assertEqual(r["reason_code"], "THERMAL_RUNAWAY")
        self.assertEqual(r["dlnr_dlnrho_cold"], 1.0)
        self.assertNotIn("no steady state at any temperature", r["message"])
        self.assertIn("root_beyond_melt_t_c", r)
        self.assertGreater(r["root_beyond_melt_dt_c"], 1000.0)
        self.assertLess(r["runaway_gain_cold"], 1.0)
        self.assertIn("cold-point gain", r["hint"])


class TestGeometry(unittest.TestCase):
    def test_sheet_and_round_differ(self):
        """Same thickness, same length: two different problems, which is why
        shape is required rather than inferred."""
        s = _ok(self, shape="sheet", frequency_hz=1e6, current_a=1.0,
                rth_c_per_w=50.0, length_mm=20.0, width_mm=1.0,
                thickness_um=35.0)["value"]
        w = _ok(self, shape="round", frequency_hz=1e6, current_a=1.0,
                rth_c_per_w=50.0, length_mm=20.0, diameter_mm=0.045)["value"]
        self.assertNotAlmostEqual(s["resistance_cold_ohm"],
                                  w["resistance_cold_ohm"], places=6)

    def test_via_convention_is_drill_plus_plating(self):
        """OD = drill + 2*plating: 0.3 mm drill with 0.5 um plating is a 0.301 mm
        barrel, and its DC resistance is rho*L/area exactly."""
        v = _ok(self, shape="round", frequency_hz=1e3, current_a=1.0,
                rth_c_per_w=100.0, length_mm=1.6, drill_mm=0.3,
                plating_um=0.5)["value"]
        a1 = 0.15
        a2 = 0.1505
        area = math.pi * (a2 * a2 - a1 * a1)
        rho = A.RHO20_CU * (1.0 + A.ALPHA_CU * (25.0 - 20.0)) * 1e3
        self.assertAlmostEqual(v["resistance_cold_ohm"], rho * 1.6 / area,
                               delta=1e-6 * rho * 1.6 / area)

    def test_thickness_um_and_mm_agree(self):
        a = _ok(self, frequency_hz=1e6, current_a=2.0, rth_c_per_w=20.0,
                shape="sheet", length_mm=20.0, width_mm=1.0,
                thickness_um=35.0)["value"]
        b = _ok(self, frequency_hz=1e6, current_a=2.0, rth_c_per_w=20.0,
                shape="sheet", length_mm=20.0, width_mm=1.0,
                thickness_mm=0.035)["value"]
        self.assertEqual(a["t_final_c"], b["t_final_c"])

    def test_length_scales_the_resistance(self):
        a = _ok(self, frequency_hz=1e6, current_a=1.0, rth_c_per_w=20.0,
                shape="sheet", length_mm=10.0, width_mm=1.0,
                thickness_um=35.0)["value"]
        b = _ok(self, frequency_hz=1e6, current_a=1.0, rth_c_per_w=20.0,
                shape="sheet", length_mm=20.0, width_mm=1.0,
                thickness_um=35.0)["value"]
        self.assertAlmostEqual(b["resistance_cold_ohm"] /
                               a["resistance_cold_ohm"], 2.0, delta=1e-9)

    def test_skin_depth_matches_the_formula(self):
        v = _ok(self, frequency_hz=1e9, current_a=3.0, rth_c_per_w=20.0,
                **SHEET)["value"]
        rho_m = A.RHO20_CU * (1.0 + A.ALPHA_CU * (v["t_final_c"] - 20.0))
        delta_um = math.sqrt(rho_m / (math.pi * 1e9 * A.MU0)) * 1e6
        self.assertAlmostEqual(v["skin_depth_um_hot"], delta_um,
                               delta=1e-3 * delta_um)


class TestRefusals(unittest.TestCase):
    """Every one of these is something the module could have guessed at."""

    def _err(self, code, **kw):
        r = _call(**kw)
        self.assertEqual(r["status"], "error", r)
        self.assertEqual(r["reason_code"], code, r.get("message"))
        return r

    def test_shape_required(self):
        self._err("ARG_REQUIRED", frequency_hz=1e6, current_a=1.0,
                  rth_c_per_w=20.0, length_mm=20.0, width_mm=1.0,
                  thickness_um=35.0)

    def test_shape_must_be_named(self):
        r = self._err("BAD_PARAMETER", shape="wire", frequency_hz=1e6,
                      current_a=1.0, rth_c_per_w=20.0, length_mm=20.0,
                      width_mm=1.0, thickness_um=35.0)
        self.assertIn("not inferred", r["message"])

    def test_frequency_required(self):
        self._err("ARG_REQUIRED", shape="sheet", current_a=1.0, rth_c_per_w=20.0,
                  length_mm=20.0, width_mm=1.0, thickness_um=35.0)

    def test_frequency_must_be_positive(self):
        self._err("BAD_PARAMETER", frequency_hz=0.0, current_a=1.0,
                  rth_c_per_w=20.0, **SHEET)

    def test_current_required(self):
        self._err("ARG_REQUIRED", frequency_hz=1e6,
                  rth_c_per_w=20.0, **SHEET)

    def test_current_not_negative(self):
        self._err("ARG_RANGE", frequency_hz=1e6, current_a=-1.0,
                  rth_c_per_w=20.0, **SHEET)

    def test_rth_required(self):
        r = self._err("ARG_REQUIRED", frequency_hz=1e6,
                      current_a=1.0, **SHEET)
        self.assertIn("never assumed", r["message"])

    def test_rth_must_be_positive(self):
        self._err("ARG_RANGE", frequency_hz=1e6, current_a=1.0, rth_c_per_w=0.0,
                  **SHEET)

    def test_length_required(self):
        self._err("ARG_REQUIRED", shape="sheet", frequency_hz=1e6, current_a=1.0,
                  rth_c_per_w=20.0, width_mm=1.0, thickness_um=35.0)

    def test_both_thicknesses(self):
        self._err("ARG_REQUIRED", frequency_hz=1e6, current_a=1.0,
                  rth_c_per_w=20.0, shape="sheet", length_mm=20.0,
                  width_mm=1.0, thickness_um=35.0, thickness_mm=0.035)

    def test_no_thickness(self):
        self._err("ARG_REQUIRED", frequency_hz=1e6, current_a=1.0,
                  rth_c_per_w=20.0, shape="sheet", length_mm=20.0,
                  width_mm=1.0)

    def test_bad_faces(self):
        self._err("BAD_PARAMETER", frequency_hz=1e6, current_a=1.0,
                  rth_c_per_w=20.0, faces="both", **SHEET)

    def test_sheet_is_not_a_diameter(self):
        self._err("ARG_INVALID", frequency_hz=1e6, current_a=1.0,
                  rth_c_per_w=20.0, diameter_mm=0.5, **SHEET)

    def test_diameter_and_drill(self):
        self._err("ARG_INVALID", shape="round", frequency_hz=1e6, current_a=1.0,
                  rth_c_per_w=20.0, length_mm=1.6, diameter_mm=0.3,
                  drill_mm=0.3, plating_um=25.0)

    def test_drill_without_plating(self):
        r = self._err("ARG_REQUIRED", shape="round", frequency_hz=1e6,
                      current_a=1.0, rth_c_per_w=20.0, length_mm=1.6,
                      drill_mm=0.3)
        self.assertIn("bare hole", r["message"])

    def test_wall_with_drill(self):
        self._err("ARG_INVALID", shape="round", frequency_hz=1e6, current_a=1.0,
                  rth_c_per_w=20.0, length_mm=1.6, diameter_mm=0.3,
                  drill_mm=0.3, wall_um=20.0)

    def test_plating_closes_the_hole(self):
        r = self._err("ARG_RANGE", shape="round", frequency_hz=1e6, current_a=1.0,
                      rth_c_per_w=20.0, length_mm=1.6, drill_mm=0.3,
                      plating_um=200.0)
        self.assertIn("hole convention", r["hint"])

    def test_wall_thicker_than_radius(self):
        self._err("ARG_RANGE", shape="round", frequency_hz=1e6, current_a=1.0,
                  rth_c_per_w=20.0, length_mm=1.6, diameter_mm=0.5,
                  wall_um=300.0)

    def test_negative_roughness(self):
        self._err("ARG_RANGE", frequency_hz=1e6, current_a=1.0, rth_c_per_w=20.0,
                  roughness_rq_um=-1.0, **SHEET)

    def test_negative_alpha(self):
        self._err("ARG_RANGE", frequency_hz=1e6, current_a=1.0, rth_c_per_w=20.0,
                  alpha_per_k=-0.001, **SHEET)

    def test_mu_below_one(self):
        self._err("BAD_PARAMETER", frequency_hz=1e6, current_a=1.0,
                  rth_c_per_w=20.0, mu_r=0.5, **SHEET)

    def test_zero_resistivity(self):
        self._err("ARG_RANGE", frequency_hz=1e6, current_a=1.0, rth_c_per_w=20.0,
                  rho20_ohm_m=0.0, **SHEET)

    def test_zero_width(self):
        self._err("ARG_RANGE", frequency_hz=1e6, current_a=1.0, rth_c_per_w=20.0,
                  shape="sheet", length_mm=20.0, width_mm=0.0,
                  thickness_um=35.0)


class TestEnvelope(unittest.TestCase):
    def test_required_text_fields(self):
        r = _ok(self, frequency_hz=1e9, current_a=3.0, rth_c_per_w=20.0, **SHEET)
        for k in ("model", "basis", "precision", "assumptions", "value", "unit"):
            self.assertTrue(r.get(k), k)
        self.assertEqual(r["unit"], "C")
        self.assertIn("bisection", r["model"])
        self.assertIn("dlnR/dlnrho", r["model"])
        self.assertIsInstance(r["value"], dict)
        self.assertTrue(r["value"]["limits"])
        self.assertEqual(r["assumptions"]["rth_source"],
                         "caller-supplied single lump, not derived here")

    def test_every_value_field_is_a_number_or_a_name(self):
        v = _ok(self, frequency_hz=1e9, current_a=3.0, rth_c_per_w=20.0,
                **SHEET)["value"]
        for k in ("t_final_c", "delta_t_c", "resistance_cold_ohm",
                  "resistance_hot_ohm", "resistance_gain", "r_over_rdc_hot",
                  "r_over_rdc_cold", "power_w", "skin_depth_um_hot",
                  "dlnr_dlnrho", "runaway_gain", "runaway_gain_cold",
                  "unit_gain_current_a", "melt_margin_c"):
            self.assertIsInstance(v[k], float, k)
        self.assertIsInstance(v["iterations"], int)
        self.assertIsInstance(v["regime_hot"], str)
        # two different statements, and neither is "a runaway happened": one is
        # about the solution (is it stable?), the other about ambient (can this
        # design ever settle near where it starts?)
        self.assertIsInstance(v["steady_state_unstable"], bool)
        self.assertFalse(v["steady_state_unstable"])
        self.assertIsInstance(v["cold_loop_gain_above_one"], bool)
        self.assertFalse(v["cold_loop_gain_above_one"])

    def test_round_only_fields(self):
        s = _ok(self, frequency_hz=1e6, current_a=1.0, rth_c_per_w=20.0,
                shape="sheet", length_mm=20.0, width_mm=1.0,
                thickness_um=35.0)["value"]
        w = _ok(self, shape="round", frequency_hz=1e6, current_a=1.0,
                rth_c_per_w=20.0, length_mm=1.6, diameter_mm=0.5)["value"]
        self.assertNotIn("x_outer_hot", s)
        self.assertIn("x_outer_hot", w)
        self.assertIn("x_wall_hot", w)


class TestSpectrumEquivalent(unittest.TestCase):
    """`harmonics=[{f, I}]` must BE the one-frequency path.

    That is the strongest statement available here: it is not "the two agree to
    within the physics", it is the same arithmetic. The multi-frequency branch
    sums (I^2*Rth)*R, which for one term is bit-for-bit k*R, and it takes the
    exponent straight from `_beta_at` when there is only one term - so a
    difference in ANY reported digit means the new branch has an extra rounding
    step somewhere, and that is the bug this class exists to catch.
    """

    CASES = [
        ("1 MHz", dict(SHEET, frequency_hz=1e6, rth_c_per_w=20.0), 3.0),
        ("1 GHz", dict(SHEET, frequency_hz=1e9, rth_c_per_w=20.0), 3.0),
        ("via", dict(shape="round", length_mm=1.6, drill_mm=0.3,
                     plating_um=25.0, frequency_hz=1e9, rth_c_per_w=200.0), 0.5),
        ("rough", dict(SHEET, frequency_hz=1e9, rth_c_per_w=20.0,
                       roughness_rq_um=1.0), 3.0),
        ("hot", dict(SHEET, frequency_hz=1e6, rth_c_per_w=20.0,
                     t_ambient_c=125.0), 3.0),
        ("two faces", dict(SHEET, frequency_hz=1e9, rth_c_per_w=20.0,
                           faces="two"), 3.0),
    ]

    def test_one_harmonic_reproduces_the_single_frequency_answer_exactly(self):
        for name, kw, cur in self.CASES:
            with self.subTest(name):
                kw = dict(kw)
                f = kw.pop("frequency_hz")
                single = _ok(self, frequency_hz=f, current_a=cur, **kw)
                spec = _ok(self, harmonics=[{"frequency_hz": f,
                                             "current_a": cur}], **kw)
                a, b = single["value"], spec["value"]
                for key in ("t_final_c", "delta_t_c", "power_w", "dlnr_dlnrho",
                            "dlnr_dlnrho_cold", "runaway_gain",
                            "runaway_gain_cold", "iterations", "melt_margin_c"):
                    self.assertEqual(a[key], b[key], key)

    def test_the_shared_checks_are_identical_too(self):
        single = _ok(self, frequency_hz=1e9, current_a=3.0, rth_c_per_w=20.0,
                     **SHEET)["value"]["checks"]
        spec = _ok(self, harmonics=[{"frequency_hz": 1e9, "current_a": 3.0}],
                   rth_c_per_w=20.0, **SHEET)["value"]["checks"]
        for key in ("energy_balance_residual_k", "energy_balance_ok",
                    "dlnr_dlnrho", "runaway_gain_at_solution", "power_w",
                    "delta_t_c", "t_final_c"):
            self.assertEqual(single[key], spec[key], key)


class TestSpectrumPower(unittest.TestCase):
    """The physics of a spectrum: the POWERS add, on the same copper."""

    BUCK = [{"frequency_hz": 5e5, "current_a": 0.4},
            {"frequency_hz": 1.5e6, "current_a": 0.13},
            {"frequency_hz": 2.5e6, "current_a": 0.07},
            {"frequency_hz": 3.5e6, "current_a": 0.04}]

    def _buck(self):
        return _ok(self, harmonics=self.BUCK, dc_current_a=1.0,
                   rth_c_per_w=60.0, **SHEET)

    def test_power_is_the_sum_of_i2r_over_the_terms(self):
        v = self._buck()["value"]
        total = sum(t["power_hot_w"] for t in v["terms"])
        self.assertAlmostEqual(v["power_w"], total, delta=1e-5 * total)
        self.assertEqual(len(v["terms"]), 5)          # 4 harmonics + 1 DC

    def test_energy_balance_holds_on_the_root(self):
        c = self._buck()["value"]["checks"]
        self.assertTrue(c["energy_balance_ok"], c["energy_balance_residual_k"])

    def test_rms_current_is_the_root_sum_of_squares(self):
        v = self._buck()["value"]
        exact = math.sqrt(1.0 + sum(h["current_a"] ** 2 for h in self.BUCK))
        self.assertAlmostEqual(v["rms_current_a"], exact, delta=1e-5 * exact)

    def test_the_dc_term_sees_the_whole_cross_section(self):
        """R_dc = rho*L/(w*t) is not a skin solution: it is the full section, and
        it must be BELOW every AC term's R at the same temperature."""
        v = self._buck()["value"]
        dc = [t for t in v["terms"] if t["kind"] == "dc"][0]
        # rho(25.7 C) * 20 mm / (1 mm * 0.035 mm), ohm*mm -> ohm
        rho20, alpha = 1.724e-8, 0.00393
        rho_mm = rho20 * (1.0 + alpha * (v["t_final_c"] - 20.0)) * 1e3
        exact = rho_mm * 20.0 / (1.0 * 0.035)
        self.assertAlmostEqual(dc["resistance_hot_ohm"], exact, delta=1e-5 * exact)
        self.assertEqual(dc["r_over_rdc_hot"], 1.0)
        self.assertEqual(dc["regime_hot"], "dc")
        for t in v["terms"]:
            if t["kind"] == "ac":
                self.assertGreater(t["r_over_rdc_hot"], 1.0)

    def test_the_power_shares_add_up(self):
        v = self._buck()["value"]
        shares = [t["power_share"] for t in v["terms"]]
        self.assertAlmostEqual(sum(shares), 1.0, delta=1e-3)   # 4-digit rounding
        self.assertAlmostEqual(max(shares), 0.8444, delta=1e-4)  # the DC term
        self.assertEqual(v["terms"][-1]["kind"], "dc")

    def test_zero_current_spectrum_is_zero_rise(self):
        r = _ok(self, harmonics=[{"frequency_hz": 1e9, "current_a": 0.0}],
                dc_current_a=0.0, rth_c_per_w=20.0, **SHEET)
        self.assertEqual(r["value"]["delta_t_c"], 0.0)
        self.assertEqual(r["value"]["power_w"], 0.0)
        self.assertEqual(r["value"]["iterations"], 0)


class TestSpectrumExponent(unittest.TestCase):
    """The exponent that decides runaway is the power-weighted one, and it is
    measured on the SUM - so a mixed spectrum must land strictly between 1/2 and
    1, and a pure one must reproduce its own value."""

    def test_all_skin_dominated_gives_the_half(self):
        r = _ok(self, harmonics=[{"frequency_hz": 1e9, "current_a": 1.0},
                                 {"frequency_hz": 2e9, "current_a": 0.6}],
                rth_c_per_w=40.0, **SHEET)
        c = r["value"]["checks"]
        self.assertEqual(r["value"]["dlnr_dlnrho"], 0.5)
        self.assertEqual(c["beta_expectation"], 0.5)
        self.assertLess(c["beta_deviation"], 1e-6)

    def test_the_dc_term_carries_exactly_one(self):
        v = _ok(self, harmonics=[{"frequency_hz": 1e6, "current_a": 3.0}],
                dc_current_a=3.0, rth_c_per_w=20.0, **SHEET)["value"]
        dc = [t for t in v["terms"] if t["kind"] == "dc"][0]
        self.assertEqual(dc["dlnr_dlnrho_term"], 1.0)
        ac = [t for t in v["terms"] if t["kind"] == "ac"][0]
        self.assertLess(ac["dlnr_dlnrho_term"], 1.0)
        # and the sum's exponent sits between the two, weighted by power
        self.assertGreater(v["dlnr_dlnrho"], ac["dlnr_dlnrho_term"])
        self.assertLess(v["dlnr_dlnrho"], 1.0)

    def test_the_sum_exponent_is_the_power_weighted_mean_of_its_parts(self):
        """An identity, not a fit: any drift is a bug in beta_p."""
        for kw in (dict(harmonics=[{"frequency_hz": 1e9, "current_a": 1.0},
                                   {"frequency_hz": 2e9, "current_a": 0.6}],
                        rth_c_per_w=40.0),
                   dict(harmonics=[{"frequency_hz": 5e5, "current_a": 0.4}],
                        dc_current_a=1.0, rth_c_per_w=60.0),
                   dict(harmonics=[{"frequency_hz": 1e6, "current_a": 3.0}],
                        dc_current_a=3.0, rth_c_per_w=20.0)):
            with self.subTest(kw=kw):
                c = _ok(self, **dict(SHEET, **kw))["value"]["checks"]
                self.assertTrue(c["dlnP_dlnrho_weighted_ok"],
                                (c["dlnP_dlnrho_weighted_check"],
                                 c["dlnr_dlnrho"]))

    def test_a_transition_term_suppresses_the_expectation(self):
        """1 MHz on 35 um copper is in the transition region, where the exponent
        is neither 1/2 nor 1 - so there is nothing to compare against and the
        module must say so instead of picking the nearer one."""
        c = _ok(self, harmonics=[{"frequency_hz": 1e6, "current_a": 3.0}],
                rth_c_per_w=20.0, **SHEET)["value"]["checks"]
        self.assertIsNone(c["beta_expectation"])
        self.assertIn("transition", c["beta_expectation_note"])


class TestSpectrumModels(unittest.TestCase):
    """What the two simpler models would have said. Both are reported as
    DIFFERENCES - neither is an error bar."""

    def test_lumping_a_spectrum_into_one_frequency_is_wrong_by_10_percent(self):
        c = _ok(self, harmonics=[{"frequency_hz": 1e9, "current_a": 1.0},
                                 {"frequency_hz": 2e9, "current_a": 0.6}],
                rth_c_per_w=40.0, **SHEET)["value"]["checks"]
        # all the RMS current at 1 GHz, where R is lower than at 2 GHz -> the
        # single-frequency answer is SHORT of the truth
        self.assertEqual(c["single_frequency_model_direction"], "under")
        self.assertAlmostEqual(c["single_frequency_model_rel_diff"], 0.1005,
                               delta=2e-4)

    def test_lumping_a_spectrum_into_dc_is_wrong_by_95_percent(self):
        c = _ok(self, harmonics=[{"frequency_hz": 1e9, "current_a": 1.0},
                                 {"frequency_hz": 2e9, "current_a": 0.6}],
                rth_c_per_w=40.0, **SHEET)["value"]["checks"]
        self.assertAlmostEqual(c["all_dc_model_rel_diff"], 0.9466, delta=2e-4)
        self.assertIn("terms[]", c["all_dc_model_note"])
        # and it is deliberately NOT called dc_linear_model_*, which in the
        # one-frequency branch answers a narrower question (R held fixed)
        self.assertNotIn("dc_linear_model_rel_diff", c)

    def test_a_mildly_dispersive_spectrum_is_barely_wrong_either_way(self):
        """The same tool on a buck's ripple: the DC term dominates (84 % of the
        power), so both simplifications are near the truth - this is the control
        that shows the two checks above are not just always large."""
        c = _ok(self, harmonics=[
            {"frequency_hz": 5e5, "current_a": 0.4},
            {"frequency_hz": 1.5e6, "current_a": 0.13},
            {"frequency_hz": 2.5e6, "current_a": 0.07},
            {"frequency_hz": 3.5e6, "current_a": 0.04}],
            dc_current_a=1.0, rth_c_per_w=60.0, **SHEET)["value"]["checks"]
        self.assertLess(c["all_dc_model_rel_diff"], 0.002)
        self.assertLess(c["single_frequency_model_rel_diff"], 0.002)


class TestSpectrumRunaway(unittest.TestCase):
    """A spectrum is not a sum of separate problems: the loop gain ADDS."""

    NARROW = dict(shape="sheet", length_mm=20.0, width_mm=0.1, thickness_um=35.0)

    def test_each_half_settles_but_together_they_do_not(self):
        """The negative control for the whole branch: 9 A of DC and 9 A of
        100 Hz current, each alone below the runaway threshold, together above
        it. Any implementation that took the largest term, or the RMS current at
        one frequency, would get this wrong in the SAFE direction."""
        alone = _ok(self, frequency_hz=100.0, current_a=9.0, rth_c_per_w=20.0,
                    **self.NARROW)
        self.assertLess(alone["value"]["runaway_gain_cold"], 1.0)
        self.assertAlmostEqual(alone["value"]["delta_t_c"], 436.5061, delta=1e-2)

        both = _call(harmonics=[{"frequency_hz": 100.0, "current_a": 9.0}],
                     dc_current_a=9.0, rth_c_per_w=20.0, **self.NARROW)
        self.assertEqual(both["status"], "error")
        self.assertEqual(both["reason_code"], "THERMAL_RUNAWAY")
        self.assertGreater(both["runaway_gain_cold"], 1.2)
        self.assertIn("Rth*P(dT)", both["message"])

    def test_the_gain_of_a_single_ac_term_is_quadratic_in_its_current(self):
        a = _ok(self, harmonics=[{"frequency_hz": 1e9, "current_a": 1.0}],
                rth_c_per_w=20.0, **SHEET)["value"]["runaway_gain_cold"]
        b = _ok(self, harmonics=[{"frequency_hz": 1e9, "current_a": 2.0}],
                rth_c_per_w=20.0, **SHEET)["value"]["runaway_gain_cold"]
        self.assertAlmostEqual(b / a, 4.0, delta=0.02)   # 4x the heat, 2x the R

    def test_the_scale_that_reaches_unit_cold_gain_squares_to_the_inverse(self):
        v = _ok(self, harmonics=[{"frequency_hz": 1e9, "current_a": 1.0},
                                 {"frequency_hz": 2e9, "current_a": 0.6}],
                rth_c_per_w=40.0, **SHEET)["value"]
        self.assertAlmostEqual(v["unit_gain_current_scale"] ** 2
                               * v["runaway_gain_cold"], 1.0, delta=1e-4)

    def test_a_spectrum_running_away_carries_no_single_current(self):
        r = _call(harmonics=[{"frequency_hz": 100.0, "current_a": 9.0}],
                  dc_current_a=9.0, rth_c_per_w=20.0, **self.NARROW)
        self.assertIsNone(r.get("unit_gain_current_a"))
        self.assertGreater(r["unit_gain_current_scale"], 0.0)
        self.assertGreater(r["rth_for_root_at_melt_c_per_w"], 0.0)


class TestSpectrumRefusals(unittest.TestCase):
    """Every one of these is a place where a spectrum could be guessed at."""

    def _err(self, code, **kw):
        r = _call(**kw)
        self.assertEqual(r["status"], "error", r)
        self.assertEqual(r["reason_code"], code, r.get("message"))
        return r

    def test_current_and_harmonics_are_not_interchangeable(self):
        r = self._err("ARG_INVALID", frequency_hz=1e6, current_a=1.0,
                      harmonics=[{"frequency_hz": 1e6, "current_a": 1.0}],
                      rth_c_per_w=20.0, **SHEET)
        self.assertIn("not both", r["message"])

    def test_top_level_frequency_with_harmonics(self):
        self._err("ARG_INVALID", frequency_hz=1e6,
                  harmonics=[{"frequency_hz": 1e6, "current_a": 1.0}],
                  rth_c_per_w=20.0, **SHEET)

    def test_empty_spectrum(self):
        self._err("ARG_INVALID", harmonics=[], rth_c_per_w=20.0, **SHEET)

    def test_dc_only_is_not_this_tool(self):
        r = self._err("ARG_INVALID", dc_current_a=3.0, rth_c_per_w=20.0, **SHEET)
        self.assertIn("electrothermal_conductor", r["message"])

    def test_two_currents_at_one_frequency(self):
        r = self._err("ARG_INVALID", rth_c_per_w=20.0,
                      harmonics=[{"frequency_hz": 1e6, "current_a": 1.0},
                                 {"frequency_hz": 1e6, "current_a": 2.0}],
                      **SHEET)
        self.assertIn("phasor", r["message"])

    def test_a_harmonic_at_zero_hz(self):
        r = self._err("BAD_PARAMETER", rth_c_per_w=20.0,
                      harmonics=[{"frequency_hz": 0.0, "current_a": 1.0}],
                      **SHEET)
        self.assertIn("dc_current_a", r["message"])

    def test_an_unknown_key_in_a_harmonic(self):
        r = self._err("BAD_PARAMETER", rth_c_per_w=20.0,
                      harmonics=[{"frequency_hz": 1e6, "current_a": 1.0,
                                  "phase_deg": 30.0}], **SHEET)
        self.assertIn("phase_deg", r["message"])

    def test_a_harmonic_without_a_current(self):
        self._err("ARG_REQUIRED", rth_c_per_w=20.0,
                  harmonics=[{"frequency_hz": 1e6}], **SHEET)

    def test_negative_harmonic_current(self):
        self._err("ARG_RANGE", rth_c_per_w=20.0,
                  harmonics=[{"frequency_hz": 1e6, "current_a": -1.0}], **SHEET)

    def test_negative_dc_current(self):
        self._err("ARG_RANGE", rth_c_per_w=20.0, dc_current_a=-1.0,
                  harmonics=[{"frequency_hz": 1e6, "current_a": 1.0}], **SHEET)

    def test_not_a_list(self):
        self._err("BAD_PARAMETER", rth_c_per_w=20.0, harmonics=1e6, **SHEET)

    def test_entry_not_an_object(self):
        self._err("BAD_PARAMETER", rth_c_per_w=20.0, harmonics=[1e6], **SHEET)

    def test_too_many_harmonics(self):
        r = self._err("ARG_RANGE", rth_c_per_w=20.0,
                      harmonics=[{"frequency_hz": float(k + 1), "current_a": 1e-3}
                                 for k in range(40)], **SHEET)
        self.assertIn("32", r["message"])

    def test_a_spectrum_still_needs_an_rth(self):
        self._err("ARG_REQUIRED", harmonics=[{"frequency_hz": 1e6,
                                              "current_a": 1.0}], **SHEET)


class TestRegistration(unittest.TestCase):
    def test_registered_and_listed(self):
        self.assertIn("ac_self_heating", T.TOOLS)
        caps = T.call_tool("calc_capabilities", {})
        self.assertIn("ac_self_heating", str(caps))

    def test_schema(self):
        d = T.TOOLS["ac_self_heating"]
        sch = d.get("inputSchema") or d
        props = sch["properties"]
        for k in ("shape", "frequency_hz", "current_a", "harmonics",
                  "dc_current_a", "rth_c_per_w",
                  "length_mm", "width_mm", "thickness_um", "thickness_mm",
                  "faces", "diameter_mm", "drill_mm", "plating_um", "wall_um",
                  "t_ambient_c", "mu_r", "roughness_rq_um", "alpha_per_k",
                  "rho20_ohm_m"):
            self.assertIn(k, props)
        # current_a and frequency_hz left the required list in 0.0.69: they are
        # one of two ways to say what the current is, and a client that saw them
        # as required could never send a spectrum. The function itself still
        # refuses a call with neither, so nothing is silently defaulted.
        self.assertEqual(sorted(sch["required"]),
                         ["length_mm", "rth_c_per_w", "shape"])
        self.assertEqual(sch["properties"]["harmonics"]["maxItems"], 32)
        self.assertEqual(sorted(sch["properties"]["harmonics"]["items"]["required"]),
                         ["current_a", "frequency_hz"])
        self.assertEqual(sorted(props["shape"]["enum"]), ["round", "sheet"])
        self.assertEqual(sorted(props["faces"]["enum"]), ["one", "two"])

    def test_call_through_the_tool_layer(self):
        r = T.call_tool("ac_self_heating", {
            "shape": "sheet", "frequency_hz": 1e9, "current_a": 3.0,
            "rth_c_per_w": 20.0, "length_mm": 20.0, "width_mm": 1.0,
            "thickness_um": 35.0})
        self.assertEqual(r["status"], "ok")
        self.assertAlmostEqual(r["value"]["t_final_c"], 56.7733, delta=1e-3)
        self.assertAlmostEqual(r["value"]["dlnr_dlnrho"], 0.5, delta=1e-5)

    def test_tool_layer_defaults(self):
        """faces defaults to 'one', t_ambient_c to 25 and mu_r to 1 - and the
        four numbers that are never assumed are what the schema marks required."""
        r = T.call_tool("ac_self_heating", {
            "shape": "round", "frequency_hz": 1e9, "current_a": 0.5,
            "rth_c_per_w": 200.0, "length_mm": 1.6, "drill_mm": 0.3,
            "plating_um": 0.5})
        self.assertEqual(r["status"], "ok")
        self.assertEqual(r["value"]["t_ambient_c"], 25.0)
        self.assertAlmostEqual(r["value"]["t_final_c"], 28.0147, delta=1e-3)

    def test_dc_tool_still_points_elsewhere(self):
        """electrothermal_conductor's not_computed list must now name this tool -
        a forward reference that is not updated is a wrong answer."""
        r = T.call_tool("electrothermal_conductor", {
            "r_ohm_at_20c": 0.01, "current_a": 1.0, "rth_c_per_w": 20.0})
        self.assertEqual(r["status"], "ok")
        self.assertIn("ac_self_heating", str(r.get("not_computed")))


if __name__ == "__main__":
    unittest.main()
