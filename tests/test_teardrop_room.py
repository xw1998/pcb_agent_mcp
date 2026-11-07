"""board_teardrop_room: has this pad/track corner got room for a teardrop?

The question comes from the reference board: it has 762 teardrops, and every board
drawn here has none, which is a real part of "why does mine look worse". KiCad can
always draw a teardrop - the layout question is whether the copper it adds has room,
and in a 0.3 mm fan-out it often has not. So this measures an envelope and refuses to
invent a required length.

Six things are pinned, each with a control that can fail:

1. On empty floor the answer is a measurement of the board edge, and the number is
   the distance that is actually there (`room = edge distance - edge clearance`).
2. Another net's copper inside the envelope makes room negative and NAMES the part
   that set it. Control: the same pad renamed to the junction's own net fits again -
   one rename, opposite answers - which is also the bug the first version had: it
   filtered the single nearest object after the fact, and the nearest thing was the
   track the teardrop grows out of, so every corner read as blocked by one clearance.
3. A longer envelope reaches copper a shorter one does not, so `length_mm` moves the
   verdict and `max_length_mm` reports the longest length that still clears.
4. `layers` decides which junctions exist at all; asking for the layer the track is
   not on leaves nothing to measure.
5. Pours are not obstacles unless asked (`pours_block`), for the same reason as the
   other board tools: the filler cuts back around what is added.
6. The tool surface says "draws nothing", admits the sampling step, and writes
   nothing to the file.
"""
import os
import sys
import unittest

sys.path.insert(0, os.path.join(os.path.dirname(os.path.dirname(
    os.path.abspath(__file__))), "src"))
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

from pcb_agent_mcp import oracle
from pcb_agent_mcp import tools
from pcb_agent_mcp.edit import plan as P

from test_routability import RoutabilityBase

NET = "SIG"

class _Tear(RoutabilityBase):
    def td(self, **kw):
        board, e = tools._load_board({"path": self.board})
        self.assertIsNone(e, e)
        return oracle.teardrop_room(board, **kw)

    def value(self, **kw):
        out = self.td(**kw)
        self.assertEqual(out["status"], "ok", out)
        return out["value"]

    def restart(self):
        P.PLANS.clear()

    def one_junction(self, pad_w=1.0, track_w=0.3, x=20.0, y=20.0):
        """A 1x1 pad at (20,20) with a 0.3 track leaving it along +x."""
        self.apply(
            self.place("A", [("1", 0.0, 0.0, pad_w, pad_w)], x=x, y=y,
                       nets={"1": NET}),
            {"op": "add_track", "x1": x, "y1": y, "x2": x + 5.0, "y2": y,
             "width": track_w, "layer": "F.Cu", "net": NET, "create_net": True})

    def neighbour(self, net, dx, dy, size=0.4):
        self.apply(self.place("B", [("1", 0.0, 0.0, size, size)], ref="U2",
                              x=20.0 + dx, y=20.0 + dy, nets={"1": net}))

class TestTheEmptyCorner(_Tear):
    def test_a_corner_with_nothing_near_it_is_measured_against_the_edge(self):
        self.one_junction()
        v = self.value()
        self.assertEqual(v["verdict"], "teardrops_fit", v)
        self.assertEqual(v["junction_count"], 1)
        hit = v["worst"][0]
        self.assertEqual(hit["ref"], "U1.1")
        self.assertEqual(hit["layer"], "F.Cu")
        self.assertAlmostEqual(hit["pad_width_mm"], 1.0, places=3)
        self.assertAlmostEqual(hit["track_mm"], 0.3, places=3)
        # The envelope starts at the pad's far side along the track.
        self.assertAlmostEqual(hit["at"][0], 20.5, places=3)
        self.assertAlmostEqual(hit["at"][1], 20.0, places=3)
        # Board is 60x40, so the nearest edge is 19.5 mm away and the edge
        # clearance is the assumed 0.2 (this board has no net classes).
        self.assertEqual(hit["limited_by"]["kind"], "board_edge")
        self.assertAlmostEqual(hit["room_mm"], 19.3, delta=0.05)
        self.assertAlmostEqual(v["max_length_mm"], 2.0, places=3)

    def test_the_requested_length_does_not_change_the_room_on_empty_floor(self):
        # Control for the tests below: room is a property of the floor, not of the
        # length asked about, when nothing is in the way.
        self.one_junction()
        a = self.value(length_mm=0.4)["worst"][0]["room_mm"]
        self.restart()
        b = self.value(length_mm=1.5)["worst"][0]["room_mm"]
        self.assertAlmostEqual(a, b, places=3)

class TestAnotherNetBlocks(_Tear):
    def test_copper_inside_the_envelope_turns_room_negative_and_is_named(self):
        self.one_junction()
        # 0.4 mm pad at (20.6, 20.8): the envelope's top corner is at (20.5, 20.5),
        # so the pad is 0.1 mm away and the 0.2 clearance is short by 0.1.
        self.neighbour("B", 0.6, 0.8)
        v = self.value()
        self.assertEqual(v["verdict"], "teardrops_blocked", v)
        hit = v["worst"][0]
        self.assertLess(hit["room_mm"], 0.0)
        self.assertAlmostEqual(hit["room_mm"], -0.1, delta=0.02)
        self.assertEqual(hit["limited_by"]["kind"], "pad")
        self.assertEqual(hit["limited_by"]["ref"], "U2.1")
        self.assertEqual(hit["limited_by"]["net"], "B")
        self.assertAlmostEqual(hit["limited_by"]["distance_mm"], 0.1, delta=0.02)
        self.assertEqual(v["blocked"], 1)
        self.assertEqual(v["fits"], 0)

    def test_the_same_pad_of_our_own_net_fits_again(self):
        # Control: one rename, opposite answer. A teardrop merges with its own net's
        # copper, so its own copper is not an obstacle - and after this the counter
        # goes the other way, which is what proves the filter is by net and not by
        # "the nearest thing".
        self.one_junction()
        self.neighbour(NET, 0.6, 0.8)
        v = self.value()
        self.assertEqual(v["verdict"], "teardrops_fit", v)
        self.assertGreater(v["worst"][0]["room_mm"], 10.0)

    def test_a_second_track_of_our_own_net_is_not_an_obstacle_either(self):
        self.one_junction()
        self.apply({"op": "add_track", "x1": 20.2, "y1": 20.9, "x2": 23.0,
                    "y2": 20.9, "width": 0.3, "layer": "F.Cu", "net": NET,
                    "create_net": True})
        v = self.value()
        self.assertEqual(v["verdict"], "teardrops_fit", v)

class TestWhereTheEnvelopeStarts(_Tear):
    """Three shapes that each made the first version answer the wrong question."""

    def test_a_track_from_the_centre_of_a_big_pad_starts_at_the_pads_edge(self):
        # A track that ends 0.0002 mm from the pad's centroid has a direction of its
        # own numerical noise; the envelope then grew out of a pad corner. The
        # envelope must start where the centreline leaves the pad - here the pad's
        # far edge at 20.8 - and be as wide as the pad is there.
        self.one_junction(pad_w=1.6, track_w=0.3)
        hit = self.value()["worst"][0]
        self.assertAlmostEqual(hit["at"][0], 20.8, delta=0.02)
        self.assertAlmostEqual(hit["at"][1], 20.0, delta=0.02)
        self.assertAlmostEqual(hit["pad_width_mm"], 1.6, delta=0.05)

    def test_a_stub_inside_the_pad_is_not_a_corner(self):
        # A track that runs from inside a 1.6 mm pad to the pad's own far edge gives
        # a direction pointing back into the pad; the envelope then went backwards
        # through the pad and out over its neighbours. It is a stub, not a corner.
        self.one_junction(pad_w=1.6, track_w=0.3)
        self.apply({"op": "add_track", "x1": 20.0, "y1": 20.0, "x2": 20.2,
                    "y2": 20.2, "width": 0.15, "layer": "F.Cu", "net": NET,
                    "create_net": True})
        v = self.value()
        self.assertEqual(v["junction_count"], 1, v["worst"])

    def test_an_oblique_exit_is_not_as_wide_as_the_pads_diagonal(self):
        # 0.3 x 1.6 pad, track leaving at 45 degrees from the centre: the copper at
        # the pad's edge there is ~0.3 wide, not the 1.6 the pad's diagonal would
        # read - and that overstatement called an entire connector row blocked.
        self.apply(
            self.place("O", [("1", 0.0, 0.0, 0.3, 1.6)], x=20.0, y=20.0,
                       nets={"1": NET}),
            {"op": "add_track", "x1": 20.0, "y1": 20.0, "x2": 22.0, "y2": 22.0,
             "width": 0.15, "layer": "F.Cu", "net": NET, "create_net": True})
        hit = self.value()["worst"][0]
        self.assertLess(hit["pad_width_mm"], 0.6, hit)
        self.assertGreaterEqual(hit["pad_width_mm"], 0.15)

class TestLengthIsTheOtherHalf(_Tear):
    def test_a_longer_envelope_reaches_copper_a_short_one_does_not(self):
        # An other-net pad 1.9 mm out along the track: outside a 0.5 mm envelope,
        # inside a 2.0 mm one. Same board, one argument, opposite verdicts.
        self.one_junction()
        self.neighbour("B", 1.9, 0.0)
        short = self.value(length_mm=0.5)["worst"][0]
        self.assertTrue(short["fits"], short)
        self.assertAlmostEqual(short["max_length_mm"], 0.9, delta=0.11)
        self.restart()
        long = self.value(length_mm=2.0)["worst"][0]
        self.assertFalse(long["fits"], long)
        self.assertEqual(long["limited_by"]["ref"], "U2.1")

    def test_max_length_is_where_the_verdict_flips(self):
        self.one_junction()
        self.neighbour("B", 1.9, 0.0)
        v = self.value(length_mm=0.5)
        longest = v["worst"][0]["max_length_mm"]
        self.assertTrue(v["worst"][0]["fits"])
        self.restart()
        self.assertFalse(self.value(length_mm=round(longest + 0.1, 3))
                         ["worst"][0]["fits"])

class TestWhatCannotBeAsked(_Tear):
    def test_only_a_stub_inside_one_pad_has_no_corner_either(self):
        self.apply(
            self.place("A", [("1", 0.0, 0.0, 1.6, 1.6)], x=20.0, y=20.0,
                       nets={"1": NET}),
            {"op": "add_track", "x1": 20.0, "y1": 20.0, "x2": 20.2, "y2": 20.0,
             "width": 0.15, "layer": "F.Cu", "net": NET, "create_net": True})
        out = self.td()
        self.assertEqual(out["status"], "error")
        self.assertEqual(out["reason_code"], "NO_GEOMETRY")

    def test_a_board_with_no_track_on_a_pad_has_no_corner_to_measure(self):
        self.apply(self.place("A", [("1", 0.0, 0.0, 1.0, 1.0)], nets={"1": NET}))
        out = self.td()
        self.assertEqual(out["status"], "error")
        self.assertEqual(out["reason_code"], "NO_GEOMETRY")
        self.assertIn("no track ends on a pad", out["message"])

    def test_asking_for_the_other_layer_leaves_nothing_to_measure(self):
        # The junction is on F.Cu; the filter is honoured rather than widened.
        self.one_junction()
        out = self.td(layers=["B.Cu"])
        self.assertEqual(out["status"], "error")
        self.assertEqual(out["reason_code"], "NO_GEOMETRY")

    def test_a_negative_length_is_refused(self):
        self.one_junction()
        out = self.td(length_mm=0.0)
        self.assertEqual(out["status"], "error")
        self.assertEqual(out["reason_code"], "ARG_RANGE")

    def test_an_unknown_layer_is_refused(self):
        self.one_junction()
        out = self.td(layers=["In9.Cu"])
        self.assertEqual(out["status"], "error")
        self.assertEqual(out["reason_code"], "ARG_INVALID")

    def test_a_region_that_excludes_the_corner_skips_it_instead_of_inventing(self):
        self.one_junction()
        out = self.td(region=[40.0, 30.0, 50.0, 35.0])
        self.assertEqual(out["status"], "error")
        self.assertEqual(out["reason_code"], "NO_GEOMETRY")

class TestPoursAreNotObstaclesUnlessAsked(_Tear):
    def _pour(self, pours_block):
        self.apply({"op": "add_net", "name": "GND"},
                   {"op": "add_zone", "net": "GND", "layer": "F.Cu",
                    "x0": 10, "y0": 10, "x1": 40, "y1": 30})
        board, e = tools._load_board({"path": self.board})
        self.assertIsNone(e, e)
        board.zones[0]["filled_polygons"] = [
            [(10.0, 10.0), (40.0, 10.0), (40.0, 30.0), (10.0, 30.0)],
            [(20.6, 19.5), (22.0, 19.5), (22.0, 20.5), (20.6, 20.5)]]
        return oracle.teardrop_room(board, pours_block=pours_block)

    def test_a_plane_around_the_corner_does_not_block_by_default(self):
        self.one_junction()
        out = self._pour(False)
        self.assertEqual(out["status"], "ok", out)
        self.assertEqual(out["value"]["verdict"], "teardrops_fit", out["value"])
        self.assertFalse(out["assumptions"]["pours_counted"])

    def test_asking_for_strict_pours_blocks_it(self):
        # Control: the same geometry, one flag, the other answer. The filled
        # polygons are declared to cover the envelope but not the junction itself,
        # so this measures the flag and not the pour's outline.
        self.one_junction()
        out = self._pour(True)
        self.assertEqual(out["status"], "ok", out)
        v = out["value"]
        self.assertTrue(out["assumptions"]["pours_counted"])
        self.assertEqual(v["verdict"], "teardrops_blocked", v)

class TestToolSurface(_Tear):
    def test_it_is_registered_derived_and_says_what_it_does_not_claim(self):
        spec = None
        for t in tools.list_tools():
            if t["name"] == "board_teardrop_room":
                spec = t
        self.assertIsNotNone(spec)
        props = spec["inputSchema"]["properties"]
        for key in ("length_mm", "clearance_mm", "edge_clearance_mm", "layers",
                    "region", "limit", "grid_mm", "max_length_mm", "pours_block"):
            self.assertIn(key, props)
        self.assertEqual(spec["inputSchema"]["required"], ["path"])
        self.assertIn("draws nothing", spec["description"])
        self.assertIn("Pours are not obstacles", spec["description"])

    def test_the_answer_says_it_is_an_envelope_and_not_a_curve(self):
        self.one_junction()
        out = self.td()
        self.assertEqual(out["status"], "ok", out)
        self.assertIn("not_claimed", out["assumptions"])
        self.assertIn("not which", out["assumptions"]["not_claimed"])
        self.assertIn("sampling", out["precision"])

    def test_it_writes_nothing(self):
        self.one_junction()
        before = open(self.board, "rb").read()
        self.value()
        self.assertEqual(open(self.board, "rb").read(), before)

if __name__ == "__main__":
    unittest.main()
