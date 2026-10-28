"""The 2-D field solver has to keep the accuracy it claims.

Every number in `field2d.VALIDATION` is re-derived here from the same code path the
tools run, and compared with a reference the solver shares no approximation with:

  * zero-thickness homogeneous stripline -> the exact conformal-mapping answer
    Z0 = eta0/(4*sqrt(er)) * K(k)/K(k'),  k = sech(pi*w/(2*b))
  * isolated microstrip -> Hammerstad-Jensen (carried below, self-contained)

The point is that the table cannot rot: if a future change to the mesh, the
stencil or the endpoint weighting moves the answer, this test says so instead of
letting a stale "within 2.8%" claim ride along in the tool description.

Deliberately NOT covered: the coupled case at tight gaps. There is no independent
exact answer in this repo for it, so the server reports `reference_note` there
rather than pretending.
"""

import math
import unittest

from pcb_agent_mcp.calc import field2d


def hammerstad_z0(w_over_h, er):
    """Hammerstad-Jensen microstrip Z0 (zero thickness), the standard reference."""
    wh = w_over_h
    if wh <= 1.0:
        ee = ((er + 1) / 2.0
              + (er - 1) / 2.0 * (1.0 / math.sqrt(1.0 + 12.0 / wh)
                                  + 0.04 * (1.0 - wh) ** 2))
        return 60.0 / math.sqrt(ee) * math.log(8.0 / wh + wh / 4.0)
    ee = (er + 1) / 2.0 + (er - 1) / 2.0 / math.sqrt(1.0 + 12.0 / wh)
    return 120.0 * math.pi / (math.sqrt(ee)
                              * (wh + 1.393 + 0.667 * math.log(wh + 1.444)))


class TheStriplineMatchesItsExactAnswer(unittest.TestCase):
    H = 0.2          # half the plane-to-plane separation, so b = 0.4
    ER = 4.3

    def _error_pct(self, w_over_b):
        w = w_over_b * 2.0 * self.H
        z, _section = field2d.solve_single("stripline", w, self.H, 0.0, self.ER)
        exact = field2d.stripline_zero_thickness_z0(w, 2.0 * self.H, self.ER)
        return 100.0 * (z - exact) / exact

    def test_the_table_is_what_the_code_produces(self):
        table = field2d.VALIDATION["stripline_vs_conformal"]["error_pct_by_w_over_b"]
        for key in ("1.0", "2.0"):
            with self.subTest(w_over_b=key):
                got = self._error_pct(float(key))
                self.assertAlmostEqual(
                    got, table[key], delta=0.05,
                    msg="w/b=%s: table says %.2f%%, code gives %.2f%%"
                        % (key, table[key], got))

    def test_every_checked_geometry_is_inside_the_claimed_band(self):
        worst = max(abs(v) for v in
                    field2d.VALIDATION["stripline_vs_conformal"]
                    ["error_pct_by_w_over_b"].values())
        self.assertLess(worst, 3.0)
        # the claimed worst case overall must still be the real worst case
        self.assertGreaterEqual(field2d.VALIDATION["worst_seen_pct"], worst)


class TheMicrostripMatchesHammerstad(unittest.TestCase):
    H = 0.2

    def test_the_table_is_what_the_code_produces(self):
        table = field2d.VALIDATION["microstrip_vs_hammerstad"]["error_pct"]
        for w_over_h, er, key in ((2.0, 4.3, "w/h=2 er=4.3"),
                                  (1.0, 1.0, "w/h=1 er=1")):
            with self.subTest(key=key):
                z, _section = field2d.solve_single(
                    "microstrip", w_over_h * self.H, self.H, 0.0, er)
                got = 100.0 * (z - hammerstad_z0(w_over_h, er)) / hammerstad_z0(w_over_h, er)
                self.assertAlmostEqual(
                    got, table[key], delta=0.05,
                    msg="%s: table says %.2f%%, code gives %.2f%%"
                        % (key, table[key], got))


class TheCoupledPathIsCheckedWhereAReferenceExists(unittest.TestCase):
    """At s/h = 8 the two traces barely see each other, so 2x the isolated exact
    answer is a legitimate reference for Zdiff. That is the only place the coupled
    code path is checked, and the server says so out loud."""

    def test_far_gap_coupled_result_agrees_with_twice_the_exact(self):
        h, er = 0.2, 4.3
        w = 0.4                      # w/b = 1
        z, _drift, _runs = field2d.solve_pair(
            "stripline", w, 8.0 * h, h, 0.0, er, levels=(1.0,))
        exact = 2.0 * field2d.stripline_zero_thickness_z0(w, 2.0 * h, er)
        got = 100.0 * (z - exact) / exact
        table = field2d.VALIDATION["stripline_pair_far_gap"]["error_pct_by_w_over_b"]
        self.assertAlmostEqual(got, table["1.0"], delta=0.3)
        self.assertLess(abs(got), 3.0)


class TheCopperThicknessDoesNotDriveTheMesh(unittest.TestCase):
    """A 35 um foil once forced a 0.003 mm cell and the run never finished. The step
    now comes from the in-plane geometry; this pins down that giving the copper only
    a few cells is enough - halving the cell moves the answer by a fraction of a
    percent, so the coarse mesh is not buying speed with accuracy."""

    def test_halving_the_cell_moves_the_answer_by_less_than_half_a_percent(self):
        args = ("microstrip", 0.4, 0.4, 0.2, 0.035, 4.3)
        z12, _d, _r = field2d.solve_pair(*args, levels=(1.0,), fine_div=12.0)
        z24, _d, _r = field2d.solve_pair(*args, levels=(1.0,), fine_div=24.0)
        delta = 100.0 * (z24 - z12) / z12
        self.assertLess(abs(delta), 0.5, "fine_div 12 -> 24 moved Z by %.3f%%" % delta)

    def test_the_fine_step_ignores_a_thin_foil(self):
        # with t = 35 um the step must still be the in-plane one, not t/12
        self.assertAlmostEqual(field2d._fine_step(0.4, 0.4, 0.2, 0.035, 12.0),
                               0.035 / 4.0, places=9)


class ItRefusesInsteadOfGuessing(unittest.TestCase):
    def test_a_gap_the_mesh_cannot_resolve_is_refused(self):
        r = field2d.field_impedance(0.4, 0.005, 0.2, kind="microstrip")
        self.assertEqual(r["status"], "error")
        self.assertEqual(r["reason_code"], "OUT_OF_VALIDITY")

    def test_an_unknown_geometry_is_refused(self):
        r = field2d.field_impedance(0.4, 0.2, 0.2, kind="coplanar")
        self.assertEqual(r["status"], "error")
        self.assertEqual(r["reason_code"], "ARG_INVALID")

    def test_nonsense_dimensions_are_refused(self):
        self.assertEqual(field2d.field_impedance(-1, 0.2, 0.2)["reason_code"], "ARG_RANGE")


class TheToolAnswersHonestly(unittest.TestCase):
    def test_it_reports_the_gap_to_the_closed_form_it_replaces(self):
        r = field2d.field_impedance(0.4, 0.4, 0.2, copper_um=35.0, kind="microstrip",
                                    levels=1)
        self.assertEqual(r["status"], "ok")
        extra = r
        self.assertIsNotNone(extra["closed_form_ohm"])
        # the closed form is the one the rest of the server used; the whole reason
        # to ship a solver is that this difference is now visible
        self.assertIsNotNone(extra["closed_form_minus_solver_pct"])
        # a tight stripline gap must carry the "no independent reference" note
        r2 = field2d.field_impedance(0.4, 0.25, 0.2, copper_um=35.0, kind="stripline",
                                     levels=1)
        self.assertEqual(r2["status"], "ok")
        self.assertIn("reference_note", r2)


if __name__ == "__main__":
    unittest.main()
