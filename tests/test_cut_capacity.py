"""board_cut_capacity: how many tracks get across this line, and how many must.

Every other tool here asks about one object. A floorplan fails one level up: a net
with pads on both sides of a line has to get across it, and the crossings it needs
cannot be fewer than the lanes the line offers. On the sky_star board that was the
whole reason it could not be finished - turning the headers into through-holes cut
B.Cu into a few gaps while 12 nets still had pads on both sides.

Six things are pinned, each with a control that can fail:

1. An empty board is ONE lane, as wide as the board, and the count follows
   n*track + (n-1)*clearance <= width. Control: a line outside the board must be
   refused rather than answered with zero lanes.
2. Drawn copper splits that lane and the missing piece is exactly track + clearance
   on both sides. Control: a track that does not reach the line changes nothing.
3. A net with pads on BOTH sides must cross; the same part with both pads on one
   side must not. That pair is the whole demand computation.
4. A net that already crosses is not demanded again - otherwise every reading on a
   half-routed board would double-count work already paid for.
5. A pour is reported in `sensitivity` but NOT counted against the primary answer,
   because the filler re-cuts it around whatever crosses. Control: asking for
   `obstacles=["...zones"]` must change the number.
6. More nets than lanes comes back as `capacity_exceeded` with a negative slack,
   and widening the one gap by 1.7 mm must flip it back - the verdict has to move
   with the geometry, not with the setup.
"""
import math
import os
import sys
import unittest

sys.path.insert(0, os.path.join(os.path.dirname(os.path.dirname(
    os.path.abspath(__file__))), "src"))
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

from pcb_agent_mcp import edit as E
from pcb_agent_mcp import oracle
from pcb_agent_mcp import tools
from pcb_agent_mcp.edit import plan as P

from test_routability import SPEC, RoutabilityBase

W, H = 60.0, 40.0            # RoutabilityBase's SPEC board, in mm
CLR, TW = 0.2, 0.25


def _fits(width, tw=TW, clr=CLR):
    if width < tw - 1e-9:
        return 0
    return int(math.floor((width + clr) / (tw + clr) + 1e-9))


class _Cut(RoutabilityBase):
    def cc(self, **kw):
        board, e = tools._load_board({"path": self.board})
        self.assertIsNone(e, e)
        return oracle.cut_capacity(board, **kw)

    def restart(self):
        P.PLANS.clear()
        os.remove(self.board)
        E.create_board(self.board, dict(SPEC))


class TestLanes(_Cut):
    def test_an_empty_board_is_one_lane(self):
        out = self.cc(angle_deg=90.0, track_mm=TW, clearance_mm=CLR)
        self.assertEqual(out["status"], "ok", out)
        v = out["value"]
        self.assertEqual(v["layers"]["F.Cu"]["gap_count"], 1,
                         "no copper, so nothing splits the line")
        gap = v["layers"]["F.Cu"]["gaps"][0]
        self.assertAlmostEqual(gap["width_mm"], H, places=3)
        self.assertEqual(gap["tracks_fit"], _fits(H))
        self.assertEqual(v["layers"]["F.Cu"]["capacity"], _fits(H))

    def test_a_line_that_misses_the_board_is_refused_not_answered_zero(self):
        # Control for the test above: "0 lanes" from a line drawn in empty space
        # would read as "this board has no capacity", which is a different claim.
        out = self.cc(x=500.0, y=500.0)
        self.assertEqual(out["status"], "error")
        self.assertEqual(out["reason_code"], "OUTSIDE_BOARD")

    def test_a_track_across_the_line_costs_lanes(self):
        self.apply(
            self.place("P", [("1", 0.0, -6.0, 1.0, 1.0), ("2", 0.0, 6.0, 1.0, 1.0)],
                       nets={"1": "SIG", "2": "SIG"}),
            {"op": "add_track", "x1": 10.0, "y1": 0.0,
             "x2": 10.0, "y2": 40.0, "width": 0.4, "layer": "F.Cu",
             "net": "SIG"})
        v = self.cc(angle_deg=0.0, track_mm=TW, clearance_mm=CLR)["value"]
        row = v["layers"]["F.Cu"]
        self.assertEqual(row["gap_count"], 2, "a track through the line splits it")
        self.assertLess(row["capacity"], _fits(W))
        # the missing piece is the track width plus the clearance on both sides
        self.assertAlmostEqual(row["blocked_mm"], 0.4 + 2 * CLR, places=3)
        self.assertEqual(v["already_crossing"], ["SIG"])
        self.assertEqual(v["must_cross"], 0,
                         "a net that already crosses is not demanded again")

    def test_a_track_that_does_not_reach_the_line_changes_nothing(self):
        # Control for the test above: if the split came from a track merely
        # existing, a track that stops 10 mm short could not restore one lane.
        self.apply({"op": "add_track", "x1": 40.0, "y1": 0.0,
                    "x2": 40.0, "y2": 10.0, "width": 0.4, "layer": "F.Cu",
                    "net": "SIG", "create_net": True})
        row = self.cc(angle_deg=0.0)["value"]["layers"]["F.Cu"]
        self.assertEqual(row["gap_count"], 1)
        self.assertEqual(row["blockers"], [])
        self.assertEqual(row["capacity"], _fits(W))


class TestDemand(_Cut):
    def test_pads_on_both_sides_must_cross(self):
        self.apply(self.place("P", [("1", 0.0, -6.0, 1.0, 1.0),
                                    ("2", 0.0, 6.0, 1.0, 1.0)],
                              nets={"1": "SIG", "2": "SIG"}))
        v = self.cc(x=30.0, y=20.0, angle_deg=0.0)["value"]
        self.assertEqual(v["must_cross"], 1, v)
        self.assertIn("SIG", v["must_cross_nets"])

    def test_pads_on_one_side_are_not_demanded(self):
        # Control: the identical part, moved so both pads sit on the same side of
        # the line, must drop out - otherwise "must cross" is just counting nets.
        self.apply(self.place("P", [("1", 0.0, -14.0, 1.0, 1.0),
                                    ("2", 0.0, -8.0, 1.0, 1.0)],
                              nets={"1": "SIG", "2": "SIG"}))
        v = self.cc(x=30.0, y=20.0, angle_deg=0.0)["value"]
        self.assertEqual(v["must_cross"], 0, v)
        self.assertNotIn("SIG", v["must_cross_nets"])

    def _straddle_and_wall(self, gap_from, gap_to):
        """Five nets cut in two by a wall that leaves one hole `gap_from..gap_to`.

        The wall is the pad row of a header: it is what turns "plenty of board" into
        "two lanes" on a real layout, and it is drawn on both layers because a
        through-hole row cuts both.
        """
        ops = []
        for i in range(5):
            ops.append(self.place(
                "N%d" % i, [("1", 0.0, -1.0, 2.0, 2.0), ("2", 0.0, 1.0, 2.0, 2.0)],
                ref="R%d" % i, x=12.0 + i * 8.0, y=20.0,
                nets={"1": "N%d" % i, "2": "N%d" % i}))
        for lay in ("F.Cu", "B.Cu"):
            ops.append({"op": "add_track", "x1": 0.0, "y1": 20.0,
                        "x2": gap_from, "y2": 20.0, "width": 0.4, "layer": lay,
                        "net": "WALL" + lay, "create_net": True})
            ops.append({"op": "add_track", "x1": gap_to, "y1": 20.0,
                        "x2": 60.0, "y2": 20.0, "width": 0.4, "layer": lay,
                        "net": "WALL" + lay})
        self.apply(*ops)

    def test_more_nets_than_lanes_is_capacity_exceeded(self):
        self._straddle_and_wall(29.35, 30.65)
        v = self.cc(angle_deg=0.0, track_mm=TW, clearance_mm=CLR)["value"]
        self.assertEqual(v["must_cross"], 5, v)
        # one hole 0.501 mm wide -> one 0.25 track per layer -> 2 lanes for 5 nets
        self.assertEqual(v["capacity"], 2, v["layers"])
        self.assertEqual(v["verdict"], "capacity_exceeded", v)
        self.assertEqual(v["slack"], -3)

        # Control: the SAME board with the one hole 1.7 mm wider. The verdict has
        # to flip, which it only can if it is reading the geometry and not the
        # setup.
        self.restart()
        self._straddle_and_wall(29.0, 31.0)
        v = self.cc(angle_deg=0.0, track_mm=TW, clearance_mm=CLR)["value"]
        self.assertEqual(v["must_cross"], 5, v)
        self.assertEqual(v["capacity"], 6, v["layers"])
        self.assertEqual(v["verdict"], "within_capacity", v)


class TestPourIsNotAnObstacle(_Cut):
    def test_a_pour_is_reported_but_not_counted(self):
        self.apply({"op": "add_net", "name": "GND"},
                   {"op": "add_zone", "net": "GND", "layer": "F.Cu",
                    "x0": 1, "y0": 1, "x1": 59, "y1": 39})
        block, e = tools._load_board({"path": self.board})
        self.assertIsNone(e, e)
        # Stand in for the filler: without a filled_polygon there is nothing to
        # measure, and what is under test is the accounting, not the pouring.
        block.zones[0]["filled_polygons"] = [
            [(-1.0, -1.0), (61.0, -1.0), (61.0, 41.0), (-1.0, 41.0)]]
        loose = oracle.cut_capacity(block, angle_deg=0.0)["value"]
        strict = oracle.cut_capacity(
            block, angle_deg=0.0,
            obstacles=["pads", "vias", "tracks", "zones"])["value"]
        self.assertGreater(loose["capacity"], strict["capacity"],
                           "the pour must only block when it was asked to")
        self.assertEqual(strict["layers"]["F.Cu"]["capacity"], 0,
                         "a pour over the whole line leaves no lane on that layer")
        self.assertGreater(strict["layers"]["B.Cu"]["capacity"], 0,
                           "and the un-poured layer is untouched")
        self.assertGreater(loose["sensitivity"]["pads_vias_tracks"],
                           loose["sensitivity"]["with_pours_too"],
                           "sensitivity must show what the pour would cost")
        self.assertEqual(strict["sensitivity"]["with_pours_too"],
                         strict["capacity"])


class TestPourShapeIsExact(_Cut):
    """A pour is 21 451 vertices on the real ground plane, so it is measured by
    solving the crossings rather than by sampling - and the number has to be the
    shape, not an artefact of the solver."""

    def _blocked(self, ring):
        self.apply({"op": "add_net", "name": "GND"},
                   {"op": "add_zone", "net": "GND", "layer": "F.Cu",
                    "x0": 10, "y0": 0, "x1": 20, "y1": 40})
        block, e = tools._load_board({"path": self.board})
        self.assertIsNone(e, e)
        block.zones[0]["filled_polygons"] = [ring]
        return oracle.cut_capacity(
            block, angle_deg=0.0, track_mm=TW, clearance_mm=CLR,
            obstacles=["pads", "vias", "tracks", "zones"])["value"]["layers"]["F.Cu"]

    def test_a_ten_mm_pour_blocks_ten_plus_two_clearances(self):
        row = self._blocked([(10.0, 0.0), (20.0, 0.0), (20.0, 40.0), (10.0, 40.0)])
        self.assertAlmostEqual(row["blocked_mm"], 10.0 + 2 * CLR, places=3)
        self.assertEqual([g["width_mm"] for g in row["gaps"]], [39.8, 9.8])

    def test_a_pour_edge_exactly_on_the_line_still_blocks(self):
        # A line running exactly along a polygon edge has no right answer for "did
        # this edge cross", so the solver nudges 0.004 mm. What must not happen is
        # the pour disappearing: the answer has to stay the same shape.
        row = self._blocked([(10.0, 20.0), (20.0, 20.0), (20.0, 40.0), (10.0, 40.0)])
        self.assertAlmostEqual(row["blocked_mm"], 10.0 + 2 * CLR, places=3)


class TestToolSurface(_Cut):
    def test_the_handler_passes_the_arguments_through(self):
        out = tools.TOOLS["board_cut_capacity"]["handler"](
            {"path": self.board, "angle_deg": 0.0, "track_mm": 0.4,
             "clearance_mm": 0.15})
        self.assertEqual(out["status"], "ok", out)
        self.assertAlmostEqual(out["assumptions"]["clearance_mm"], 0.15, places=6)
        self.assertAlmostEqual(out["assumptions"]["track_mm"], 0.4, places=6)
        v = out["value"]
        self.assertEqual(v["obstacles"], ["pad", "track", "via"])
        self.assertAlmostEqual(v["line"]["usable_mm"], W, places=3)
        self.assertIn("not_claimed", out["assumptions"])

    def test_an_unknown_obstacle_kind_is_refused(self):
        out = tools.TOOLS["board_cut_capacity"]["handler"](
            {"path": self.board, "obstacles": ["smoke"]})
        self.assertEqual(out["status"], "error")
        self.assertEqual(out["reason_code"], "ARG_INVALID")


if __name__ == "__main__":
    unittest.main()
