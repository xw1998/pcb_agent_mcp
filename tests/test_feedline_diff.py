"""The edge-coupled pair as a feedline kind (0.0.26): routing, not a new model.

`feedline_impedance` / `feedline_width` gained `cpw_diff` and `gcpw_diff`. The
kind adds nothing to the physics - it calls the same 2-D solve `coplanar_pair_field`
calls - so what is worth testing is the seam around it:

  * the number is bit-for-bit the pair solver's, and it says out loud that it is
    Zdiff (a differential number labelled Z0 is how a pair gets laid out wrong);
  * `spacing_mm` is REQUIRED, not defaulted: a pair with no spacing is not a
    pair, and a default would silently lay out a coupled pair at some invented
    distance;
  * a single-ended kind is untouched by the new parameter (bit-identical with
    and without it), because "off by default" must mean off;
  * the inverse lands on the forward answer it claims to - `feedline_width`
    returns a width, and re-running `feedline_impedance` at that width must
    reproduce the same impedance, since that is the only re-check a user has.

The negative controls are the two ways this seam can look right and be wrong:

  * `_PairIgnored` - the pair solved as two independent single lines. The
    answer stays plausible (179 ohm instead of 130), so only a test that knows
    the coupling must move the number catches it.
  * `_SpacingDropped` - `spacing_mm` accepted and then not used: the answer is
    identical to a pair at the default spacing, which is exactly the bug the
    0.0.25 batch shipped once.
"""

import unittest

from pcb_agent_mcp import tools
from pcb_agent_mcp.calc import feedline as fl
from pcb_agent_mcp.calc import field2d

ER = 4.3


def _pair_z(w=0.3, s=0.2, g=0.2, h=0.2, kind="cpw", levels=1):
    r = field2d.coplanar_pair_solve(kind, w, s, g, h, 0.0, ER, levels=(1.0,),
                                    fine_div=10.0)
    return r["z_odd"], r["z_even"]


class PairKindRoutesToThePairSolver(unittest.TestCase):

    def test_value_is_the_pair_solver_value(self):
        r = fl.feedline_impedance("cpw_diff", 0.3, 0.2, gap_mm=0.2,
                                  spacing_mm=0.2)
        self.assertEqual(r["status"], "ok", r)
        z_odd, z_even = _pair_z()
        self.assertAlmostEqual(r["value"]["z0_ohm"], 2.0 * z_odd, places=3)
        self.assertAlmostEqual(r["value"]["differential"]["z_odd_ohm"], z_odd,
                               places=3)
        self.assertAlmostEqual(r["value"]["differential"]["z_even_ohm"], z_even,
                               places=3)
        self.assertAlmostEqual(r["value"]["eps_eff"], r["value"]
                               ["differential"]["eps_eff_odd"], places=6)

    def test_the_differential_meaning_is_stated(self):
        r = fl.feedline_impedance("cpw_diff", 0.3, 0.2, gap_mm=0.2,
                                  spacing_mm=0.2)
        meaning = r["value"]["z0_meaning"]
        self.assertTrue(meaning.startswith("DIFFERENTIAL"), meaning)
        self.assertIn("2*Z_odd", meaning)
        d = r["value"]["differential"]
        self.assertEqual(d["charge_balance_odd"], -1.0)
        self.assertLess(d["z_odd_ohm"], d["z_even_ohm"])
        # both fields are rounded on the way out (4 and 5 decimals), so this is
        # a 1e-3 check, not a bit check
        self.assertAlmostEqual(d["z_common_ohm"], 0.5 * d["z_even_ohm"],
                               places=3)
        self.assertEqual(r["unit"], "ohm")

    def test_gcpw_pair_is_its_own_case(self):
        r = fl.feedline_impedance("gcpw_diff", 0.3, 0.2, gap_mm=0.2,
                                  spacing_mm=0.2)
        self.assertEqual(r["status"], "ok", r)
        z_odd, z_even = _pair_z(kind="gcpw")
        self.assertAlmostEqual(r["value"]["z0_ohm"], 2.0 * z_odd, places=3)
        # the backside plane pulls the odd mode down hard against the cpw pair
        self.assertLess(r["value"]["z0_ohm"], 2.0 * _pair_z()[0])


class SpacingIsRequiredNotDefaulted(unittest.TestCase):

    def test_pair_kinds_refuse_a_missing_spacing(self):
        for kind in ("cpw_diff", "gcpw_diff"):
            r = fl.feedline_impedance(kind, 0.3, 0.2, gap_mm=0.2)
            self.assertEqual(r["status"], "error", kind)
            self.assertEqual(r["reason_code"], "ARG_REQUIRED", kind)
            self.assertIn("spacing_mm", r["message"])
            w = fl.feedline_width(100.0, kind, 0.2, gap_mm=0.2)
            self.assertEqual(w["status"], "error", kind)
            self.assertEqual(w["reason_code"], "ARG_REQUIRED", kind)

    def test_bad_spacing_values_are_refused(self):
        self.assertEqual(
            fl.feedline_impedance("cpw_diff", 0.3, 0.2, gap_mm=0.2,
                                  spacing_mm=0.0)["reason_code"], "ARG_RANGE")
        self.assertEqual(
            fl.feedline_impedance("cpw_diff", 0.3, 0.2, gap_mm=0.2,
                                  spacing_mm="wide")["reason_code"],
            "ARG_INVALID")
        self.assertEqual(
            fl.feedline_impedance("cpw_diff", 0.3, 0.2, spacing_mm=0.2)
            ["reason_code"], "ARG_REQUIRED")   # gap_mm is still mandatory

    def test_single_ended_kinds_ignore_spacing_bit_for_bit(self):
        a = fl.feedline_impedance("cpw", 0.3, 0.2, gap_mm=0.2)
        b = fl.feedline_impedance("cpw", 0.3, 0.2, gap_mm=0.2, spacing_mm=0.6)
        self.assertEqual(a["status"], "ok")
        self.assertEqual(b["status"], "ok")
        self.assertEqual(a["value"], b["value"])
        self.assertEqual(a["assumptions"], b["assumptions"])


class CouplingIsReal(unittest.TestCase):
    """The pair is not two independent lines, and this is what says so."""

    def test_pair_sits_far_below_two_isolated_lines(self):
        single = fl.feedline_impedance("cpw", 0.3, 0.2, gap_mm=0.2)
        z_single = single["value"]["z0_ohm"]
        pair = fl.feedline_impedance("cpw_diff", 0.3, 0.2, gap_mm=0.2,
                                     spacing_mm=0.2)
        z_pair = pair["value"]["z0_ohm"]
        self.assertLess(z_pair, 0.85 * 2.0 * z_single,
                        "odd mode %.2f vs two isolated lines %.2f"
                        % (z_pair, 2.0 * z_single))

    def test_the_two_modes_are_not_the_same_number(self):
        d = fl.feedline_impedance("cpw_diff", 0.3, 0.2, gap_mm=0.2,
                                  spacing_mm=0.2)["value"]["differential"]
        self.assertGreater(d["z_even_ohm"], 2.0 * d["z_odd_ohm"] - 17.0)


class NegativeControls(unittest.TestCase):
    """Two ways to keep the plausible answer while dropping the physics."""

    def test_pair_treated_as_two_independent_lines_would_be_caught(self):
        real = fl.feedline_impedance("cpw_diff", 0.3, 0.2, gap_mm=0.2,
                                     spacing_mm=0.2)["value"]["z0_ohm"]
        z_single = fl.feedline_impedance("cpw", 0.3, 0.2, gap_mm=0.2
                                         )["value"]["z0_ohm"]
        stub = 2.0 * z_single
        self.assertGreater(abs(stub - real) / real, 0.20,
                           "a pair solved as two single lines would read %.2f, "
                           "the coupling is only worth %.2f" % (stub, real))

    def test_spacing_dropped_would_be_caught(self):
        a = fl.feedline_impedance("cpw_diff", 0.3, 0.2, gap_mm=0.2,
                                  spacing_mm=0.2)
        b = fl.feedline_impedance("cpw_diff", 0.3, 0.2, gap_mm=0.2,
                                  spacing_mm=0.4)
        za = a["value"]["z0_ohm"]
        zb = b["value"]["z0_ohm"]
        self.assertGreater(abs(zb - za) / za, 0.05,
                           "doubling the spacing moved Zdiff only %+.2f %% - a "
                           "dropped spacing_mm looks exactly like this"
                           % (100.0 * (zb - za) / za))


class LossIsNormalisedToTheDifferentialCurrent(unittest.TestCase):

    def test_pair_conductor_loss_split(self):
        r = fl.feedline_impedance("cpw_diff", 0.3, 0.2, gap_mm=0.2,
                                  spacing_mm=0.2, freq_ghz=2.4, tan_delta=0.02)
        loss = r["value"]["loss"]
        self.assertEqual(loss["conductor_model"], "field_surface_charge")
        self.assertAlmostEqual(loss["conductor_db_per_100mm"], 0.4312,
                               delta=0.002)
        self.assertAlmostEqual(loss["dielectric_db_per_100mm"], 0.5157,
                               delta=0.0005)
        split = loss["shape_factor_split"]
        self.assertAlmostEqual(split["strips_each"], 0.5 * split["strips"],
                               places=5)
        self.assertEqual(split["return_left"], split["return_right"])
        self.assertEqual(split["return_back"], 0.0)
        # the sheet bound gives the ground wings nothing at all, so the field
        # figure has to come out lower - if it were higher, one of the two would
        # be mis-normalised
        self.assertLess(loss["field_over_sheet_ratio"], 1.0)


class InverseLandsOnTheForwardAnswer(unittest.TestCase):

    def test_width_round_trips_through_impedance(self):
        r = fl.feedline_width(130.0, "cpw_diff", 0.2, gap_mm=0.2,
                              spacing_mm=0.2)
        self.assertEqual(r["status"], "ok", r)
        v = r["value"]
        self.assertTrue(v["converged"], v)
        self.assertLessEqual(v["abs_error_ohm"], 0.15, v)
        self.assertAlmostEqual(v["z0_ohm"], 130.0, delta=0.15)
        self.assertGreaterEqual(v["default_solves"], 2)
        check = fl.feedline_impedance("cpw_diff", v["width_mm"], 0.2,
                                      gap_mm=0.2, spacing_mm=0.2)
        self.assertEqual(check["status"], "ok", check)
        self.assertAlmostEqual(check["value"]["z0_ohm"], v["z0_ohm"], places=3)
        # the cheap mesh is a bias, not noise: it reads a percent off, and that
        # number is reported rather than hidden
        self.assertLess(v["cheap_over_default_pct"], -0.5)
        self.assertGreater(v["cheap_over_default_pct"], -3.0)

    def test_unreachable_target_reports_the_band(self):
        r = fl.feedline_width(1000.0, "cpw_diff", 0.2, gap_mm=0.2,
                             spacing_mm=0.2)
        self.assertEqual(r["status"], "error", r)
        self.assertEqual(r["reason_code"], "UNREACHABLE")
        band = r["reachable_ohm"]
        self.assertLess(band["narrowest_z_ohm"], 1000.0)


if __name__ == "__main__":
    unittest.main()
