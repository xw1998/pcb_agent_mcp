"""Routing shape as numbers, and the redraw list that comes out of it.

Each reading is checked against a board built to break it. Thresholds are read
from the module so changing one has to change the expectation too.
"""
import os
import sys
import unittest

sys.path.insert(0, os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "src"))

from pcb_agent_mcp import routing_profile as R  # noqa: E402
from pcb_agent_mcp.adapters.canonical import Board  # noqa: E402

def _board(tracks, outline=((-50, -50), (50, -50), (50, 50), (-50, 50)), vias=()):
    board = Board("test", "memory")
    board.tracks = list(tracks)
    board.vias = list(vias)
    board.footprints = []
    board.outline = [list(outline)] if outline else []
    return board

def _track(net, start, end, layer="F.Cu", width=0.2):
    return {"net": net, "layer": layer, "width": width, "start": start, "end": end,
            "uuid": "%s-%s-%s" % (net, start, end)}

class TestAngleBuckets(unittest.TestCase):
    def test_a_diagonal_run_is_counted_as_diagonal(self):
        got = R.profile(_board([_track("A", (0, 0), (10, 10))]))
        self.assertEqual(got["diagonal_share"], 1.0)
        self.assertEqual(got["off_grid_share"], 0.0)

    def test_a_manhattan_run_is_not(self):
        got = R.profile(_board([_track("A", (0, 0), (10, 0)),
                                _track("A", (10, 0), (10, 10))]))
        self.assertEqual(got["diagonal_share"], 0.0)

    def test_a_segment_off_the_45_grid_is_off_grid(self):
        got = R.profile(_board([_track("A", (0, 0), (10, 5))]))
        self.assertEqual(got["off_grid_share"], 1.0)

    def test_the_threshold_that_decided_it_is_in_the_answer(self):
        got = R.profile(_board([_track("A", (0, 0), (10, 5))]))
        self.assertEqual(got["thresholds"]["angle_tol_deg"], R.ANGLE_TOL_DEG)

class TestCrossings(unittest.TestCase):
    def test_a_segment_spanning_the_board_is_a_crossing(self):
        board = _board([_track("A", (-50, 0), (50, 0))])   # diagonal 141.4 mm
        got = R.profile(board)
        self.assertEqual(got["crossing_segments"], 1)
        self.assertAlmostEqual(got["crossing_threshold_mm"],
                               round(R.CROSSING_SHARE * 100 * 2 ** 0.5, 2), places=1)

    def test_a_neighbourhood_run_is_not(self):
        got = R.profile(_board([_track("A", (0, 0), (5, 0))]))
        self.assertEqual(got["crossing_segments"], 0)

class TestBends(unittest.TestCase):
    def test_one_corner_is_one_bend(self):
        got = R.profile(_board([_track("A", (0, 0), (5, 0)),
                                _track("A", (5, 0), (10, 5))]))
        self.assertEqual(got["bends_per_net_median"], 1)

    def test_straight_split_is_no_bend(self):
        got = R.profile(_board([_track("A", (0, 0), (5, 0)),
                                _track("A", (5, 0), (10, 0))]))
        self.assertEqual(got["bends_per_net_median"], 0)

class TestLayerPreference(unittest.TestCase):
    def test_a_pure_horizontal_layer_reports_horizontal(self):
        got = R.profile(_board([_track("A", (0, 0), (5, 0)),
                                _track("B", (0, 1), (5, 1))]))
        row = [r for r in got["layers"] if r["layer"] == "F.Cu"][0]
        self.assertEqual(row["preferred_direction"], "horizontal")
        self.assertEqual(row["preference_strength"], 1.0)

    def test_a_split_layer_has_weak_preference(self):
        got = R.profile(_board([_track("A", (0, 0), (5, 0)),
                                _track("B", (0, 1), (0, 6)),
                                _track("C", (1, 0), (6, 5)),
                                _track("D", (2, 0), (-3, 5))]))
        row = [r for r in got["layers"] if r["layer"] == "F.Cu"][0]
        self.assertLess(row["preference_strength"], 0.75)

class TestComparisonNeedsAReference(unittest.TestCase):
    def test_without_a_reference_it_says_the_question_was_not_answered(self):
        got = R.compare(R.profile(_board([_track("A", (0, 0), (5, 0))])), [])
        self.assertEqual(got["boards"], 0)
        self.assertIn("not answered", got["why"])

    def test_a_lower_is_better_reading_reports_further_when_higher(self):
        target = {"off_grid_share": 0.5}
        reference = {"off_grid_share": 0.01}
        got = R.compare(target, [reference])
        row = [r for r in got["rows"] if r["reading"] == "off_grid_share"][0]
        self.assertEqual(row["verdict"], "further from the reference")
        self.assertIn("off_grid_share", got["further"])

    def test_a_missing_reading_is_not_comparable_rather_than_zero(self):
        got = R.compare({"off_grid_share": None}, [{"off_grid_share": 0.01}])
        row = got["rows"][0]
        self.assertEqual(row["position"], "not comparable here")
        self.assertIsNone(row["delta"])

class TestViolationsAreAList(unittest.TestCase):
    def test_an_over_long_segment_is_listed_with_both_endpoints(self):
        board = _board([_track("A", (-50, 0), (50, 0))])
        got = R.violations(board)
        self.assertEqual(got["long_segment_count"], 1)
        row = got["long_segments"][0]
        self.assertEqual(row["net"], "A")
        self.assertEqual(len(row["start"]), 2)
        self.assertGreater(row["length_mm"], got["limits"]["max_segment_mm"])

    def test_a_short_run_is_not_listed(self):
        got = R.violations(_board([_track("A", (0, 0), (2, 0))]))
        self.assertEqual(got["long_segment_count"], 0)

    def test_off_grid_copper_is_counted(self):
        got = R.violations(_board([_track("A", (0, 0), (10, 3))]))
        self.assertEqual(got["off_grid_segments"], 1)

    def test_supplied_limits_win_and_are_echoed(self):
        board = _board([_track("A", (0, 0), (10, 0))])
        got = R.violations(board, limits={"max_segment_mm": 5.0})
        self.assertEqual(got["limits"]["max_segment_mm"], 5.0)
        self.assertEqual(got["long_segment_count"], 1)

    def test_a_bend_limit_lists_only_nets_over_it(self):
        board = _board([_track("A", (0, 0), (5, 0)),
                        _track("A", (5, 0), (10, 5)),
                        _track("B", (0, 10), (5, 10))])
        got = R.violations(board, limits={"max_bends_per_net": 0})
        self.assertEqual([r["net"] for r in got["nets_over_bend_limit"]], ["A"])

if __name__ == "__main__":
    unittest.main()
