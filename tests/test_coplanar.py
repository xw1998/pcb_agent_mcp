"""Coplanar cross-section (0.0.23): the checks it can and cannot pass.

Four checks, and they are deliberately not all the same kind:

  * against the conformal map - an INDEPENDENT physics reference (the closed form
    shares no discretisation with the solver), a few percent;
  * scale invariance - EXACT, and it catches a whole class of unit bugs;
  * left/right symmetry of the two ground wings - EXACT, and it is the reason the
    coplanar section uses centred control volumes instead of the pair section's
    forward differences;
  * zero-thickness mesh drift larger than the finite-thickness one - a MECHANISM
    check: the slot edge really is a logarithmic singularity.

There is no incremental-inductance check here and there cannot be one: the
coplanar shape factor diverges logarithmically at zero thickness, so there is no
converged value for the closed-form derivative to be compared against. See
docs/29-共面波导场解.md.
"""

import unittest

from pcb_agent_mcp.calc import feedline
from pcb_agent_mcp.calc import field2d


def _closed(kind, w, g, h, er):
    return feedline._cpw(w, g, h, 0.0, er, kind == "gcpw")[0]


class TestAgainstTheConformalMap(unittest.TestCase):
    def test_cpw_reads_within_five_percent_and_low(self):
        z, drift, runs = field2d.coplanar_solve_pair("cpw", 0.3, 0.2, 0.2, er=4.3,
                                                     levels=(1.0,))
        closed = _closed("cpw", 0.3, 0.2, 0.2, 4.3)
        gap = 100.0 * (closed - z) / z
        self.assertLess(abs(gap), 5.0)
        # the measured direction is not an accident: the solver reads low of the
        # map on every geometry in the table
        self.assertLess(gap, 0.0)
        self.assertGreater(runs[0]["eps_eff"], 1.0)
        self.assertLess(runs[0]["eps_eff"], 4.3)
        self.assertGreater(runs[0]["nodes"], 1000)

    def test_gcpw_reads_within_five_percent_and_low(self):
        z, drift, runs = field2d.coplanar_solve_pair("gcpw", 0.3, 0.2, 0.2, er=4.3,
                                                     levels=(1.0,))
        closed = _closed("gcpw", 0.3, 0.2, 0.2, 4.3)
        gap = 100.0 * (closed - z) / z
        self.assertLess(abs(gap), 5.0)
        self.assertLess(gap, 0.0)
        # the backside plane pulls eps_eff up: more of the field is in the board
        unbacked, _, _ = field2d.coplanar_solve_pair("cpw", 0.3, 0.2, 0.2, er=4.3,
                                                     levels=(1.0,))
        self.assertLess(z, unbacked)
        self.assertGreater(runs[0]["eps_eff"], 2.6)

    def test_validation_table_matches_the_code_path(self):
        # the table published in the answers must be reproducible, not folklore
        entry = field2d.VALIDATION["coplanar_vs_conformal"]
        z, _, _ = field2d.coplanar_solve_pair(
            "gcpw", 0.2, 0.15, 0.2, er=4.3, levels=(1.0, 0.65))
        gap = 100.0 * (_closed("gcpw", 0.2, 0.15, 0.2, 4.3) - z) / z
        self.assertAlmostEqual(gap, entry["error_pct_by_case"]
                               ["gcpw w=0.2 g=0.15 h=0.2 er=4.3"], delta=0.35)


class TestExactInvariants(unittest.TestCase):
    def test_scale_invariance_is_exact(self):
        # doubling every length must halve the shape factor, exactly: the number
        # is 1/length, and the mesh is derived from the geometry so it scales too
        a = field2d.coplanar_shape_factor("cpw", 0.3, 0.2, 0.2, er=4.3,
                                          refine=None)
        b = field2d.coplanar_shape_factor("cpw", 0.6, 0.4, 0.4, er=4.3,
                                          refine=None)
        self.assertAlmostEqual(2.0 * b["shape_factor_per_mm"]
                               / a["shape_factor_per_mm"], 1.0, places=6)

    def test_the_two_ground_wings_split_the_return_current_exactly(self):
        for kind in ("cpw", "gcpw"):
            sf = field2d.coplanar_shape_factor(kind, 0.3, 0.2, 0.2, er=4.3,
                                               refine=None)
            self.assertAlmostEqual(sf["ground_share_left"], sf["ground_share_right"],
                                   places=12)
            # nothing is lost in the partition
            self.assertAlmostEqual(
                sf["ground_share_left"] + sf["ground_share_right"]
                + sf["ground_share_back"], sf["ground_share"], places=12)

    def test_an_unbacked_line_has_no_backside_return(self):
        sf = field2d.coplanar_shape_factor("cpw", 0.3, 0.2, 0.2, er=4.3, refine=None)
        self.assertEqual(sf["ground_share_back"], 0.0)
        self.assertGreater(sf["ground_share_left"], 0.0)

    def test_a_backed_line_returns_almost_all_of_it_on_the_backside(self):
        sf = field2d.coplanar_shape_factor("gcpw", 0.3, 0.2, 0.2, er=4.3, refine=None)
        self.assertGreater(sf["ground_share_back"], 0.8 * sf["ground_share"])


class TestMechanism(unittest.TestCase):
    def test_zero_thickness_drift_is_reported_and_large(self):
        # a zero-thickness slot edge is r^-1/2: the integral has no converged
        # value, and the drift is the honest uncertainty rather than a defect
        sf = field2d.coplanar_shape_factor("cpw", 0.3, 0.2, 0.2, er=4.3)
        self.assertTrue(sf["zero_thickness"])
        self.assertIsNotNone(sf["mesh_drift_pct"])
        self.assertGreater(sf["mesh_drift_pct"], 2.0)
        self.assertIsNotNone(sf["coarser"])
        self.assertGreater(sf["coarser"]["fine_mm"], sf["fine_mm"])

    def test_the_answer_moves_the_way_a_log_divergence_says_it_should(self):
        # finer mesh -> larger integral, monotonically: the corner charge keeps
        # growing as the cell shrinks
        s = [field2d.coplanar_shape_factor("cpw", 0.3, 0.2, 0.2, er=4.3,
                                           refine=None, fine_div=d)
             ["shape_factor_per_mm"] for d in (6.0, 8.0, 10.0)]
        self.assertLess(s[0], s[1])
        self.assertLess(s[1], s[2])


class TestRefusals(unittest.TestCase):
    def test_unknown_kind(self):
        with self.assertRaises(ValueError):
            field2d.coplanar_solve_pair("microstrip", 0.3, 0.2, 0.2)
        with self.assertRaises(ValueError):
            field2d.coplanar_shape_factor("stripline", 0.3, 0.2, 0.2)

    def test_bad_geometry(self):
        with self.assertRaises(ValueError):
            field2d.coplanar_solve_pair("cpw", 0.0, 0.2, 0.2)
        with self.assertRaises(ValueError):
            field2d.coplanar_solve_pair("cpw", 0.3, 0.0, 0.2)
        with self.assertRaises(ValueError):
            field2d.coplanar_solve_pair("cpw", 0.3, 0.2, 0.2, er=0.9)

    def test_node_ceiling_refuses_before_sweeping(self):
        with self.assertRaises(ValueError) as ctx:
            field2d.coplanar_solve_pair("cpw", 0.3, 0.2, 0.2, max_nodes=200)
        self.assertIn("over the 200-node limit", str(ctx.exception))
        with self.assertRaises(ValueError):
            field2d.coplanar_shape_factor("cpw", 0.3, 0.2, 0.2, max_nodes=200)


class TestTheTool(unittest.TestCase):
    def test_envelope_carries_the_comparison(self):
        r = field2d.coplanar_field(0.3, 0.2, 0.2, kind="cpw", levels=1)
        self.assertEqual(r["status"], "ok")
        self.assertGreater(r["value"], 0.0)
        self.assertEqual(r["unit"], "ohm")
        self.assertLess(r["closed_form_minus_solver_pct"], 0.0)
        self.assertGreater(r["eps_eff"], 1.0)
        self.assertIn("error_pct_by_case", r["validated_against"])
        self.assertIn("cpw w=0.3 g=0.2 h=0.2 er=4.3",
                      r["validated_against"]["error_pct_by_case"])

    def test_unknown_kind_is_an_error_not_a_default(self):
        r = field2d.coplanar_field(0.3, 0.2, 0.2, kind="stripline")
        self.assertEqual(r["status"], "error")
        self.assertEqual(r["reason_code"], "ARG_INVALID")

    def test_a_mesh_it_refuses_comes_back_as_an_out_of_validity_error(self):
        r = field2d.coplanar_field(0.3, 0.2, 0.2, max_nodes=200)
        self.assertEqual(r["status"], "error")
        self.assertEqual(r["reason_code"], "OUT_OF_VALIDITY")


if __name__ == "__main__":
    unittest.main()
