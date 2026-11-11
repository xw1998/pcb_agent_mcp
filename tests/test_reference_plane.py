"""The return-path measurement: a gap a track enters and leaves, and the two
things that look like one but are not.

On sky_star the coarse raster flagged twenty-seven tracks and the exact re-read
killed two of them - both were tracks running within 0.1 mm of a pour boundary,
which the 0.2 mm raster cannot resolve. That false positive is a test here
(``test_a_track_hugging_the_pour_edge_is_not_a_split``), because a rule that
invents a slot on a good board is worse than a rule that says nothing.

A track that runs off the edge of the pour is counted separately: the return
current has somewhere to go, it just leaves the plane, which is a different
thing from crossing a slot in it.
"""
import os
import sys
import unittest

sys.path.insert(0, os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "src"))

from pcb_agent_mcp.adapters.canonical import Board  # noqa: E402
from pcb_agent_mcp.ruleset import measure as M  # noqa: E402


def _track(net, start, end, layer="F.Cu"):
    return {"net": net, "layer": layer, "width": 0.25,
            "start": list(start), "end": list(end),
            "uuid": "%s-%s-%s" % (net, start, end)}


def _pour(layer, rings, net="GND"):
    return {"net": net, "is_keepout": False, "layers": [layer],
            "filled_polygons": [[(float(x), float(y)) for x, y in r] for r in rings]}


def _board(tracks=(), zones=()):
    b = Board("test", "memory")
    b.footprints = []
    b.tracks = list(tracks)
    b.vias = []
    b.zones = list(zones)
    b.outline = []
    b.rules = {}
    return b


def _rect(x0, y0, x1, y1):
    return [(x0, y0), (x1, y0), (x1, y1), (x0, y1)]


class TestTheSplit(unittest.TestCase):
    def test_a_solid_plane_under_the_track_is_not_a_crossing(self):
        b = _board([_track("SIG", (1, 0), (19, 0))],
                   [_pour("B.Cu", [_rect(0, -5, 20, 5)])])
        m = M.measure(b)
        row = m["signal_crosses_plane_split"]
        self.assertIs(row["value"], False)
        self.assertEqual(row["examples"], [])

    def test_a_hole_the_track_enters_and_leaves_is_reported_with_its_width(self):
        """A 4 mm hole in the plane; the track goes in at x=9 and out at x=13."""
        b = _board([_track("SIG", (1, 0), (19, 0))],
                   [_pour("B.Cu", [_rect(0, -5, 20, 5), _rect(9, -2, 13, 2)])])
        m = M.measure(b)
        row = m["signal_crosses_plane_split"]
        self.assertIs(row["value"], True)
        self.assertEqual(len(row["examples"]), 1)
        self.assertAlmostEqual(row["examples"][0]["uncovered_mm"], 4.0, places=1)
        self.assertEqual(row["examples"][0]["net"], "SIG")
        self.assertIn("widest", row["where"])

    def test_a_gap_narrower_than_the_raster_still_counts(self):
        """0.4 mm is two cells of the coarse pass; it must survive the re-read."""
        b = _board([_track("SIG", (1, 0), (19, 0))],
                   [_pour("B.Cu", [_rect(0, -5, 20, 5), _rect(9, -2, 9.4, 2)])])
        m = M.measure(b)
        self.assertIs(m["signal_crosses_plane_split"]["value"], True)

    def test_a_track_hugging_the_pour_edge_is_not_a_split(self):
        """The sky_star false positive: 0.05 mm inside the edge, coarse says no."""
        b = _board([_track("SIG", (1, 0.05), (19, 0.05))],
                   [_pour("B.Cu", [_rect(0, 0, 20, 5)])])
        m = M.measure(b)
        row = m["signal_crosses_plane_split"]
        self.assertIs(row["value"], False)
        self.assertEqual(row["examples"], [])

    def test_a_track_that_runs_off_the_pour_is_counted_separately(self):
        b = _board([_track("SIG", (5, 0), (15, 0))],
                   [_pour("B.Cu", [_rect(0, -5, 10, 5)])])
        m = M.measure(b)
        row = m["signal_crosses_plane_split"]
        self.assertIs(row["value"], False)
        self.assertIn("1 track(s) run over the edge", row["note"])


class TestWhenItRefuses(unittest.TestCase):
    def test_without_a_layer_order_it_will_not_guess_the_reference(self):
        b = _board([_track("SIG", (1, 0), (19, 0)),
                    _track("SIG", (1, 2), (19, 2), layer="In1.Cu")],
                   [_pour("In1.Cu", [_rect(0, -5, 20, 5)])])
        m = M.measure(b)
        row = m["signal_crosses_plane_split"]
        self.assertIn("unmeasurable", row)
        self.assertIn("cannot be ordered", row["unmeasurable"])

    def test_a_board_with_no_pour_has_no_plane_to_cross(self):
        b = _board([_track("SIG", (1, 0), (19, 0)),
                    _track("GND", (1, 2), (19, 2), layer="B.Cu")])
        m = M.measure(b)
        row = m["signal_crosses_plane_split"]
        self.assertIn("unmeasurable", row)
        self.assertIn("no filled pour", row["unmeasurable"])


if __name__ == "__main__":
    unittest.main()
