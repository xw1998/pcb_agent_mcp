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


def _board(tracks=(), zones=(), stackup=()):
    b = Board("test", "memory")
    b.stackup = [{"name": n} for n in stackup]
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

    def test_a_narrow_gap_is_counted_but_not_graded(self):
        """0.4 mm is a via clearance, not a split: it lands in `bands`, ungraded.

        A hole this size sits around every via and every pad of another net on a
        real plane. The re-read must still find it (it is two cells of the coarse
        pass, so the coarse pass can miss it) - it just may not be called a break.
        """
        b = _board([_track("SIG", (1, 0), (19, 0))],
                   [_pour("B.Cu", [_rect(0, -5, 20, 5), _rect(9, -2, 9.4, 2)])])
        row = M.measure(b)["signal_crosses_plane_split"]
        self.assertIs(row["value"], False)
        self.assertEqual(sum(row["bands"].values()), 1)
        self.assertIn("under 1.0 mm", row["bands"])
        self.assertIn("not graded", row["where"])
        self.assertIn("not graded", row["note"])

    def test_a_gap_wider_than_one_millimetre_is_graded(self):
        """1.2 mm is a plane cut, and the verdict says so."""
        b = _board([_track("SIG", (1, 0), (19, 0))],
                   [_pour("B.Cu", [_rect(0, -5, 20, 5), _rect(9, -2, 10.2, 2)])])
        row = M.measure(b)["signal_crosses_plane_split"]
        self.assertIs(row["value"], True)
        self.assertAlmostEqual(row["examples"][0]["uncovered_mm"], 1.2, places=1)
        self.assertIn("1.0-2.0 mm", row["bands"])

    def test_a_power_plane_is_not_a_reference_plane(self):
        """The video case: GND is the biggest flood, +5V is the adjacent copper.

        A track on F.Cu whose other side is the +5V plane returns on GND, which
        is two layers away. Testing it against +5V reported 297 crossings on a
        finished board; the rule must say it cannot test, not that it broke.
        """
        b = _board([_track("SIG", (1, 0), (19, 0), layer="F.Cu")],
                   [_pour("B.Cu", [_rect(0, -5, 40, 5)], net="GND"),
                    _pour("B.Cu", [_rect(20, -5, 40, 5)], net="GND"),
                    _pour("In1.Cu", [_rect(0, -5, 20, 5), _rect(9, -2, 15, 2)],
                          net="+5V")],
                   stackup=("F.Cu", "In1.Cu", "B.Cu"))
        row = M.measure(b)["signal_crosses_plane_split"]
        self.assertEqual(row["reference_net"], "GND")
        self.assertIs(row["value"], False)
        self.assertIn("In1.Cu carries +5V, which is not the return net",
                      row["note"])

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


class TestTheCorpusRefutedTheVerdict(unittest.TestCase):
    """The rule was a Critical P1 bool. Every finished board failed it.

    Eleven of the twelve measurable KiCad demo boards cross at least one gap
    wider than 1 mm in their return copper, including boards that shipped. A gap
    only breaks a FAST signal, and no board file says which nets are fast, so
    the crossing is measured and the verdict is left to the caller: the check
    reports `undetermined` with the measurement as evidence instead of failing
    the board.
    """

    def test_a_crossing_is_shown_and_not_graded(self):
        import importlib
        audit_mod = importlib.import_module("pcb_agent_mcp.ruleset.audit")
        b = _board([_track("SIG", (1, 0), (19, 0))],
                   [_pour("B.Cu", [_rect(0, -5, 20, 5), _rect(9, -2, 15, 2)])],
                   stackup=("F.Cu", "B.Cu"))
        b.net_resolve = None
        rep = audit_mod.audit(b, levels=["P1"])
        hit = [c for c in rep["levels"]["P1"]["checks"]
               if c["id"] == "layout.reference_plane_continuous"]
        self.assertEqual(len(hit), 1)
        self.assertEqual(hit[0]["state"], "undetermined")
        self.assertIs(hit[0]["measured"], True)
        self.assertIn("not graded", hit[0]["reason"])
        self.assertIn("gap wider than 1.0 mm", hit[0]["evidence"])

if __name__ == "__main__":
    unittest.main()
