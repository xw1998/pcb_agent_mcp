"""The two ladders: per-item room, and the clipping that turns it into a plan.

Both have a failure side, so every case below is a board built to be limited by
exactly one neighbour: a 0.60 mm fan-out pair whose copper gap is already the
clearance, a lone via with nothing near it, a track between two pads. The
clipping cases are the three the sky_star run actually hit - a ceiling that
lands ON the asked value, a drill that only fits once the size has been clipped,
and a via whose ceiling is below its current size (left alone, never shrunk).
"""
import os
import sys
import unittest

sys.path.insert(0, os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "src"))

from pcb_agent_mcp import house_style as H  # noqa: E402
from pcb_agent_mcp import tools as T  # noqa: E402
from pcb_agent_mcp.adapters.canonical import Board  # noqa: E402

RULES = {"clearance": {"min_clearance": 0.15},
         "track_width": {"min_track_width": 0.15},
         "via": {"min_via_diameter": 0.45, "min_via_annular_width": 0.1,
                 "min_hole_to_hole": 0.25, "min_through_hole_diameter": 0.2}}


def _via(net, x, y, size=0.45, drill=0.25, uuid=None):
    return {"net": net, "x": x, "y": y, "diameter": size, "drill": drill,
            "layers": ["F.Cu", "B.Cu"], "uuid": uuid or "%s-%s" % (net, x)}


def _pad(net, x, y, w=1.0, h=1.0):
    return {"number": "1", "net": net, "x": x, "y": y, "w": w, "h": h,
            "shape": "roundrect", "rotation": 0.0, "layers": ["F.Cu"],
            "hole_mm": None, "smd": True, "type": "smd"}


def _board(tracks=(), vias=(), pads=()):
    board = Board("test", "memory")
    board.tracks = list(tracks)
    board.vias = list(vias)
    board.footprints = []
    for i, (net, x, y) in enumerate(pads):
        board.footprints.append({"ref": "U%d" % i, "value": "U", "x": x, "y": y,
                                 "rotation": 0.0, "layer": "F.Cu",
                                 "pads": [_pad(net, x, y)]})
    board.outline = []
    board.rules = dict(RULES)
    board.net_classes = {}
    board.netclass_assignments = {}
    return board


def _track(net, start, end, width, layer="F.Cu"):
    return {"net": net, "layer": layer, "width": width, "start": start, "end": end,
            "uuid": "%s-%s-%s" % (net, start, end)}


def _value(res):
    return res["value"] if isinstance(res, dict) and "value" in res else res


class TestViaRoom(unittest.TestCase):
    def test_a_sixty_millimetre_fan_out_pins_the_via_at_its_current_size(self):
        """0.60 apart, 0.45 wide, 0.15 clearance: 0.45 is already the answer."""
        board = _board(vias=[_via("PA1", 0, 0), _via("PA2", 0.6, 0)])
        a = H.via_room(board)["by_uuid"]["PA1-0"]
        self.assertAlmostEqual(a["max_size_mm"], 0.45, places=6)
        self.assertEqual(a["bound_by"]["kind"], "via")

    def test_the_same_pair_further_apart_reports_more_room(self):
        """If 0.80 gave 0.45 too, the bound would be a constant, not geometry."""
        board = _board(vias=[_via("PA1", 0, 0), _via("PA2", 0.8, 0)])
        a = H.via_room(board)["by_uuid"]["PA1-0"]
        # 2 * (0.8 centre distance - 0.225 far radius - 0.15 clearance)
        self.assertAlmostEqual(a["max_size_mm"], 0.85, places=6)

    def test_vias_on_one_net_are_still_two_holes(self):
        """Copper gap does not apply within a net; hole-to-hole does."""
        board = _board(vias=[_via("GND", 0, 0), _via("GND", 0.6, 0)])
        a = H.via_room(board)["by_uuid"]["GND-0"]
        self.assertIsNone(a["max_size_mm"])
        # the size is free, so the ring is measured against the current 0.45
        self.assertAlmostEqual(a["drill_ceiling_from_annular_mm"], 0.25, places=6)

    def test_a_via_with_nothing_near_it_reports_no_ceiling(self):
        a = H.via_room(_board(vias=[_via("PA1", 0, 0)]))["by_uuid"]["PA1-0"]
        self.assertIsNone(a["max_size_mm"])
        self.assertIsNone(a["max_drill_mm"])

    def test_a_foreign_track_caps_the_size(self):
        board = _board(vias=[_via("PA1", 0, 0)],
                       tracks=[_track("PA2", (0.5, -2), (0.5, 2), 0.15)])
        a = H.via_room(board)["by_uuid"]["PA1-0"]
        # centre to the track's edge 0.5 - 0.075, minus clearance 0.15, doubled
        self.assertAlmostEqual(a["max_size_mm"], 2 * (0.425 - 0.15), places=3)
        self.assertEqual(a["bound_by"]["kind"], "track")

    def test_an_explicit_clearance_is_never_relaxed_to_the_project_rule(self):
        """The gate reads 0.2; measuring at 0.15 would plan a blocked write."""
        # 0.90 apart keeps both ceilings above the 0.45 already on the board,
        # so the two readings are the clearance rule and nothing else.
        board = _board(vias=[_via("PA1", 0, 0), _via("PA2", 0.9, 0)])
        self.assertAlmostEqual(
            H.via_room(board)["by_uuid"]["PA1-0"]["max_size_mm"], 1.05, places=6)
        self.assertAlmostEqual(
            H.via_room(board, clearance_mm=0.2)["by_uuid"]["PA1-0"]["max_size_mm"],
            0.95, places=6)


class TestSegmentRoom(unittest.TestCase):
    def test_a_track_squeezed_between_two_pads_cannot_grow(self):
        board = _board(tracks=[_track("PA1", (0, 0), (0, 5), 0.15)],
                       pads=[("X", -0.5, 0), ("Y", 0.5, 0)])
        row = H.segment_room(board)["by_uuid"]["PA1-(0, 0)-(0, 5)"]
        self.assertLessEqual(row["max_width_mm"], 0.15 + 1e-9)
        self.assertEqual(row["bound_by"]["kind"], "pad")

    def test_a_lone_track_is_not_limited(self):
        row = H.segment_room(_board(tracks=[_track("PA1", (0, 0), (0, 5), 0.15)])
                             )["by_uuid"]["PA1-(0, 0)-(0, 5)"]
        self.assertIsNone(row["max_width_mm"])
        self.assertIsNone(row["bound_by"])


class TestTheClipping(unittest.TestCase):
    def _ladder(self, board, size, drill, clearance=None, net="*"):
        return _value(T._via_ladder(board, {
            "targets": [{"net": net, "size_mm": size, "drill_mm": drill}],
            "clearance_mm": clearance}))

    def test_a_ceiling_that_lands_on_the_ask_is_dropped_one_step(self):
        """Measured on sky_star: 0.5000 leaves the pair at 0.1999 mm."""
        board = _board(vias=[_via("PA1", 0, 0), _via("PA2", 0.625, 0)])
        row = self._ladder(board, 0.50, 0.30, net="PA1")["rows"][0]
        self.assertAlmostEqual(row["planned_size_mm"], 0.49, places=6)
        self.assertAlmostEqual(row["planned_drill_mm"], 0.28, places=6)
        self.assertTrue(row["capped_size"])
        self.assertTrue(row["capped_drill"])

    def test_a_ceiling_below_the_current_size_leaves_the_via_alone(self):
        """A via never shrinks: 0.45 is on the board, so 0.44 would be a loss."""
        board = _board(vias=[_via("PA1", 0, 0), _via("PA2", 0.6, 0)])
        got = self._ladder(board, 0.50, 0.30)
        self.assertEqual(got["operations"], [])
        self.assertEqual(got["vias_planned"], 0)
        self.assertEqual(got["vias_left_alone"], 2)

    def test_a_via_with_room_takes_the_ask_and_is_grouped(self):
        board = _board(vias=[_via("PA1", 0, 0), _via("PA2", 2.0, 0)])
        got = self._ladder(board, 0.50, 0.30, net="PA1")
        row = got["rows"][0]
        self.assertAlmostEqual(row["planned_size_mm"], 0.50, places=6)
        self.assertAlmostEqual(row["planned_drill_mm"], 0.30, places=6)
        self.assertFalse(row["capped_size"])
        self.assertEqual(got["operations"][0]["op"], "set_via_size")
        self.assertEqual(got["operations"][0]["uuids"], ["PA1-0"])

    def test_drill_at_or_above_the_size_is_refused_by_name(self):
        res = T._via_ladder(_board(vias=[_via("PA1", 0, 0)]),
                            {"targets": [{"net": "*", "size_mm": 0.5,
                                          "drill_mm": 0.5}]})
        self.assertEqual(res.get("reason_code"), "ARG_INVALID")
        self.assertIn("annular", res.get("message", ""))

    def test_a_net_with_no_vias_is_refused_rather_than_silently_skipped(self):
        res = T._via_ladder(_board(vias=[_via("PA1", 0, 0)]),
                            {"targets": [{"net": "PB2", "size_mm": 0.5,
                                          "drill_mm": 0.3}]})
        self.assertEqual(res.get("reason_code"), "NO_GEOMETRY")


if __name__ == "__main__":
    unittest.main()
