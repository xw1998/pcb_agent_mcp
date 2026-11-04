"""Stitching vias: the holes that sew two ground pours into one plane.

What these tests are actually protecting
----------------------------------------
Not "the function runs". Three claims, each with a control that has to FAIL:

1. **A hole only goes where the same net's FILLED copper already is, on every
   layer it spans.** Control: a board whose pour exists on one layer only must
   refuse with `no_same_net_pour` - not place a stub; and a point outside the fill
   must be refused `no_pour_here` even though the zone's outline covers it.
2. **A zone with no `filled_polygon` is not coverage.** Control: a zone declared
   with `(fill yes)` and an empty fill must produce `has_fill == False`, so the
   audit cannot call an unpoured board "stitched".
3. **Existing copper is respected without the vendor engine.** Control: a via
   dropped 0.1 mm from a different-net track must be refused `too_close` with the
   net named, and two drills 0.1 mm apart must be refused `hole_too_close` **even
   when both nets are the same** (hole-to-hole is a fabrication rule, not a net
   rule).

A note on the frame: this module works in the canonical frame (mm, y up), which is
what every other geometry module here reads. The tests build boards in that frame
directly - a file's y-down spelling is the adapter's business, not this one's.
"""
import unittest

from pcb_agent_mcp.adapters.canonical import Board
from pcb_agent_mcp.route import stitch


def _square(x0, y0, x1, y1):
    return [(x0, y0), (x1, y0), (x1, y1), (x0, y1)]


def _board(both_layers=True, fill=True, extra_zones=()):
    """20 x 20 board, GND pour on F.Cu (and B.Cu unless told otherwise)."""
    b = Board("test", "<memory>")
    b.outline = [_square(0.0, 0.0, 20.0, 20.0)]
    poly = _square(1.0, 1.0, 19.0, 19.0)
    zones = [{"net": "GND", "layers": ["F.Cu"], "outline": poly,
              "filled_polygons": [poly] if fill else [], "priority": 1},
             {"net": "GND", "layers": ["B.Cu"], "outline": poly,
              "filled_polygons": [poly] if (fill and both_layers) else [],
              "priority": 0}]
    zones.extend(extra_zones)
    b.zones = zones
    b.nets = {1: "GND"}
    return b


class TestCoverageIsThePrecondition(unittest.TestCase):
    def test_pour_on_one_layer_refuses_rather_than_placing_a_stub(self):
        b = _board(both_layers=False)
        cov = stitch.coverage(b)
        self.assertTrue(cov["F.Cu"]["has_fill"])
        self.assertFalse(cov["B.Cu"]["has_fill"])
        p = stitch.plan(b)
        self.assertEqual(p["ops"], [])
        self.assertEqual(p["refused"], "no_same_net_pour")
        self.assertEqual(p["layers_without_fill"], ["B.Cu"])

    def test_declared_zone_without_a_fill_is_not_coverage(self):
        b = _board(fill=False)
        cov = stitch.coverage(b)
        for layer in ("F.Cu", "B.Cu"):
            self.assertFalse(cov[layer]["has_fill"],
                             "a zone with (fill yes) and no filled_polygon is not "
                             "copper; calling it coverage is how an unpoured board "
                             "gets reported as stitched")
            self.assertEqual(cov[layer]["filled_polygons"], 0)
        self.assertEqual(stitch.plan(b)["refused"], "no_same_net_pour")

    def test_audit_says_not_stitchable_and_says_which_layer(self):
        b = _board(both_layers=False)
        a = stitch.audit(b)
        self.assertEqual(a["verdict"], "not_stitchable")
        self.assertEqual(a["why"]["layers_without_fill"], ["B.Cu"])


class TestWhereAHoleMayGo(unittest.TestCase):
    def test_clean_board_gets_a_centred_grid_of_holes(self):
        b = _board()
        p = stitch.plan(b, pitch_mm=3.0, max_ops=500, beside_vias=False)
        self.assertGreater(p["placed"], 20)
        # The grid is laid over the fill's own bounding box, so the outermost row
        # of points sits exactly ON the fill boundary. Depending on which way ray
        # casting answers for a vertex that lands `no_pour_here` or
        # `pour_edge_too_close` - both are refusals, and neither is nudged: nudge
        # exists to escape a hair-line miss, not to go looking for copper.
        boundary = (p["skip_counts"].get("no_pour_here", 0) +
                    p["skip_counts"].get("pour_edge_too_close", 0))
        self.assertEqual(boundary, 49 - p["placed"])
        self.assertEqual(len(p["ops"]), p["placed"])
        for op in p["ops"]:
            self.assertEqual(op["op"], "add_via")
            self.assertEqual(op["net"], "GND")
            self.assertEqual((op["size"], op["drill"]), (0.6, 0.3))
            self.assertTrue(1.0 < op["x"] < 19.0 and 1.0 < op["y"] < 19.0)

    def test_points_outside_the_fill_are_refused_even_though_the_outline_covers_them(self):
        # The fill leaves a 1 mm ring; the zone OUTLINE still reaches the edge. A
        # tool that trusted the outline would put holes in the ring.
        b = _board()
        p = stitch.plan(b, pitch_mm=1.0, max_ops=40, beside_vias=False)
        self.assertGreater(p["skip_counts"].get("no_pour_here", 0), 0)
        for op in p["ops"]:
            self.assertTrue(1.0 < op["x"] < 19.0 and 1.0 < op["y"] < 19.0,
                            "a hole landed on the unpoured ring: %r" % (op,))

    def test_a_different_net_track_is_respected_and_named(self):
        b = _board()
        # A horizontal SIG track at y = 10, right through the middle of the grid.
        b.tracks = [{"net": "SIG", "layer": "F.Cu", "width": 0.25,
                     "start": (1.0, 10.0), "end": (19.0, 10.0)}]
        b.nets[2] = "SIG"
        p = stitch.plan(b, pitch_mm=3.0, max_ops=500, beside_vias=False,
                        nudge_mm=0.0)
        too_close = [s for s in p["skipped"] if s["reason"] == "too_close"]
        self.assertTrue(too_close, "the track was not seen at all")
        self.assertTrue(any(s.get("with_net") == "SIG" for s in too_close))
        for s in too_close:
            self.assertLess(s["gap_mm"], 0.25)
        # and no placed hole sits inside the clearance band around that track
        for op in p["ops"]:
            self.assertGreater(abs(op["y"] - 10.0) - 0.125 - 0.3, 0.25 - 1e-6)

    def test_two_drills_too_close_is_refused_even_on_the_same_net(self):
        b = _board()
        b.vias = [{"net": "GND", "x": 10.0, "y": 10.0,
                   "diameter": 0.6, "drill": 0.3}]
        obs = stitch._obstacles(b)
        why = stitch._why_not(10.1, 10.0, "GND", 0.3, 0.3, obs, 0.25,
                              ("F.Cu", "B.Cu"))
        self.assertIsNotNone(why, "two 0.3 mm drills 0.1 mm apart is a hole hit")
        self.assertEqual(why["reason"], "hole_too_close")

    def test_existing_stitch_vias_are_not_doubled_up(self):
        b = _board()
        b.vias = [{"net": "GND", "x": 10.0, "y": 10.0,
                   "diameter": 0.6, "drill": 0.3}]
        p = stitch.plan(b, pitch_mm=3.0, max_ops=500, beside_vias=False,
                        nudge_mm=0.0)
        for op in p["ops"]:
            gap = ((op["x"] - 10.0) ** 2 + (op["y"] - 10.0) ** 2) ** 0.5 - 0.6
            self.assertGreaterEqual(gap, 0.25 - 1e-6)

    def test_a_hole_may_not_hang_over_the_pours_own_boundary(self):
        # A fill only 0.8 mm wide: the centre line is 0.4 mm from copper that ends,
        # and a 0.6 mm via needs 0.3 + 0.25. Placing here would put the annulus in
        # the clearance gap the pour itself left - a defect wearing a stitch's
        # clothes, and one no DRC would call a copper short.
        b = _board()
        strip = _square(9.6, 0.0, 10.4, 20.0)
        for z in b.zones:
            z["outline"] = strip
            z["filled_polygons"] = [strip]
        p = stitch.plan(b, pitch_mm=3.0, max_ops=10, beside_vias=False,
                        nudge_mm=0.0)
        self.assertEqual(p["placed"], 0)
        self.assertTrue(p["skip_counts"].get("pour_edge_too_close"))

    def test_a_near_miss_is_nudged_not_dropped(self):
        b = _board()
        b.tracks = [{"net": "SIG", "layer": "F.Cu", "width": 0.25,
                     "start": (1.0, 9.7), "end": (19.0, 9.7)}]
        b.nets[2] = "SIG"
        strict = stitch.plan(b, pitch_mm=3.0, max_ops=500, beside_vias=False,
                             nudge_mm=0.0)
        loose = stitch.plan(b, pitch_mm=3.0, max_ops=500, beside_vias=False,
                            nudge_mm=0.4)
        self.assertGreaterEqual(loose["placed"], strict["placed"])
        self.assertGreater(loose["nudged"], 0)


class TestReturnPathVias(unittest.TestCase):
    def test_a_layer_changing_signal_via_gets_a_ground_via_next_to_it(self):
        b = _board()
        b.vias = [{"net": "SIG", "x": 10.0, "y": 10.0,
                   "diameter": 0.6, "drill": 0.3}]
        b.nets[2] = "SIG"
        p = stitch.plan(b, pitch_mm=3.0, max_ops=500, beside_vias=True,
                        near_via_mm=3.0)
        near = [op for op in p["ops"]
                if 0.0 < ((op["x"] - 10.0) ** 2 + (op["y"] - 10.0) ** 2) ** 0.5
                <= 3.0]
        self.assertTrue(near, "no ground via was planned within a via pitch of "
                              "the signal via that changes layer")

    def test_audit_counts_signal_vias_with_no_return(self):
        b = _board()
        b.vias = [{"net": "SIG", "x": 10.0, "y": 10.0,
                   "diameter": 0.6, "drill": 0.3}]
        a = stitch.audit(b)
        self.assertEqual(a["signal_vias"], 1)
        self.assertEqual(a["signal_vias_without_nearby_return"], 1)
        self.assertEqual(a["existing_stitch_vias"], 0)
        self.assertEqual(a["verdict"], "no_stitching_at_all")


class TestTheRefusalsAreReasonsNotCounts(unittest.TestCase):
    def test_every_skip_carries_a_reason_code_and_can_be_read(self):
        b = _board()
        b.tracks = [{"net": "SIG", "layer": "F.Cu", "width": 0.25,
                     "start": (1.0, 10.0), "end": (19.0, 10.0)}]
        b.nets[2] = "SIG"
        p = stitch.plan(b, pitch_mm=2.0, max_ops=500, beside_vias=False,
                        nudge_mm=0.0)
        known = {"no_pour_here", "pour_edge_too_close", "too_close",
                 "hole_too_close", "too_near_board_edge", "existing_via",
                 "on_same_net_pad", "outside_board",
                 "no_room_around_signal_via"}
        self.assertTrue(p["skipped"])
        for s in p["skipped"]:
            self.assertIn(s["reason"], known)
            self.assertIn("x", s)
            self.assertIn("y", s)
        self.assertEqual(sum(p["skip_counts"].values()), p["skipped_total"])

    def test_a_drill_wider_than_the_pad_is_an_error_not_a_via(self):
        with self.assertRaises(ValueError):
            stitch.plan(_board(), dia_mm=0.3, drill_mm=0.3)


if __name__ == "__main__":
    unittest.main()
