"""House style: the rules DRC never sees.

Every case has a failure side. The real evidence behind the module is in
docs/122 - the F412 core board runs all 556 segments at one width while a
production carrier uses 13 - and the control here is a board built to break each
reading: one width, one via family, and a net whose role cannot be read.
"""
import os
import sys
import unittest

sys.path.insert(0, os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "src"))

from pcb_agent_mcp import house_style as H  # noqa: E402
from pcb_agent_mcp.adapters.canonical import Board  # noqa: E402

def _pad(net, x, y):
    return {"number": "1", "net": net, "x": x, "y": y, "w": 1.0, "h": 1.0,
            "shape": "roundrect", "rotation": 0.0, "layers": ["F.Cu"],
            "hole_mm": None, "smd": True, "type": "smd"}

def _board(tracks, vias=(), pads=(), outline=None):
    board = Board("test", "memory")
    board.tracks = list(tracks)
    board.vias = list(vias)
    board.footprints = []
    for i, (net, x, y) in enumerate(pads):
        board.footprints.append({"ref": "U%d" % i, "value": "U", "x": x, "y": y,
                                 "rotation": 0.0, "layer": "F.Cu",
                                 "pads": [_pad(net, x, y)]})
    board.outline = [outline] if outline else []
    return board

def _track(net, start, end, width, layer="F.Cu"):
    return {"net": net, "layer": layer, "width": width, "start": start, "end": end,
            "uuid": "%s-%s-%s" % (net, start, end)}

class TestRoleIsReadOffTheName(unittest.TestCase):
    def test_power_pour_and_signal_are_told_apart(self):
        self.assertEqual(H.role_of("+3V3"), "power")
        self.assertEqual(H.role_of("VBUS"), "power")
        self.assertEqual(H.role_of("VDDIO_1V8"), "power")
        self.assertEqual(H.role_of("GND"), "pour")
        self.assertEqual(H.role_of("AGND"), "pour")

    def test_a_ground_net_is_not_a_width_rule(self):
        """A pour has no width, so a width fix for it would be a rule about nothing."""
        board = _board([_track("GND", (0, 0), (5, 0), 0.15)])
        got = H.diffs(board, {"width_by_role": {"signal": 0.3}})
        self.assertEqual(got["nets_to_change"], 0)
        self.assertEqual(got["skipped"]["pour"], ["GND"])

    def test_an_unknown_name_is_signal_not_power(self):
        self.assertEqual(H.role_of("PA7"), "signal")
        self.assertEqual(H.role_of("USB_DP"), "differential")
        self.assertEqual(H.role_of(""), "unassigned")

class TestTheWidthLadder(unittest.TestCase):
    def test_one_width_is_one_rung_and_a_full_share(self):
        board = _board([_track("PA1", (0, 0), (5, 0), 0.15),
                        _track("+3V3", (0, 1), (5, 1), 0.15)])
        got = H.style(board)
        self.assertEqual(got["width"]["distinct"], 1)
        self.assertEqual(got["width"]["dominant_share"], 1.0)

    def test_two_rungs_are_two_and_the_share_adds_to_one(self):
        board = _board([_track("PA1", (0, 0), (5, 0), 0.15),
                        _track("PA2", (0, 1), (5, 1), 0.15),
                        _track("+3V3", (0, 2), (5, 2), 0.4)])
        got = H.style(board)
        self.assertEqual(got["width"]["distinct"], 2)
        self.assertAlmostEqual(sum(r["share"] for r in got["width"]["ladder"]), 1.0)
        self.assertEqual(got["width"]["dominant_mm"], 0.15)

    def test_float_noise_is_not_a_rung(self):
        """0.15 and 0.15000000000000002 are one decision, not two."""
        board = _board([_track("PA1", (0, 0), (5, 0), 0.15),
                        _track("PA2", (0, 1), (5, 1), 0.15000000000000002)])
        self.assertEqual(H.style(board)["width"]["distinct"], 1)

    def test_the_ladder_reaches_the_roles(self):
        board = _board([_track("PA1", (0, 0), (5, 0), 0.15),
                        _track("+3V3", (0, 1), (5, 1), 0.4)])
        got = H.style(board)["by_role"]
        self.assertEqual(got["signal"]["median_width_mm"], 0.15)
        self.assertEqual(got["power"]["median_width_mm"], 0.4)

class TestViaFamilies(unittest.TestCase):
    def test_one_geometry_is_one_family(self):
        board = _board([], vias=[{"net": "A", "drill": 0.3, "diameter": 0.6},
                                 {"net": "B", "drill": 0.3, "diameter": 0.6}])
        got = H.style(board)["vias"]
        self.assertEqual(got["families"], 1)
        self.assertEqual(got["dominant_share"], 1.0)

    def test_a_second_geometry_is_a_second_family_and_the_share_splits(self):
        board = _board([], vias=[{"net": "A", "drill": 0.3, "diameter": 0.6},
                                 {"net": "B", "drill": 0.3, "diameter": 0.6},
                                 {"net": "C", "drill": 0.2, "diameter": 0.45}])
        got = H.style(board)["vias"]
        self.assertEqual(got["families"], 2)
        self.assertAlmostEqual(got["dominant_share"], 2 / 3.0, places=4)
        self.assertEqual(got["ladder"][0]["drill_mm"], 0.3)

class TestCorners(unittest.TestCase):
    def test_a_turn_is_measured_where_two_segments_meet(self):
        board = _board([_track("PA1", (0, 0), (5, 0), 0.2),
                        _track("PA1", (5, 0), (10, 5), 0.2)])
        got = H.style(board)["corners"]
        self.assertEqual(got["turns"], 1)
        self.assertEqual(got["share_turning_45"], 1.0)

    def test_two_collinear_segments_are_not_a_turn(self):
        board = _board([_track("PA1", (0, 0), (5, 0), 0.2),
                        _track("PA1", (5, 0), (10, 0), 0.2)])
        got = H.style(board)["corners"]
        self.assertEqual(got["turns"], 0)
        self.assertIsNone(got["share_turning_45"])

class TestRulesAreRatiosNotMillimetres(unittest.TestCase):
    def _reference(self, power, signal):
        return {"segments": 10,
                "width": {"distinct": 3, "dominant_mm": signal, "dominant_share": 0.5},
                "by_role": {"power": {"median_width_mm": power},
                            "signal": {"median_width_mm": signal}},
                "vias": {"families": 1},
                "corners": {"share_turning_45": 0.95}}

    def test_absolute_copies_the_reference_millimetres(self):
        got = H.rules([self._reference(0.19, 0.10)])
        self.assertEqual(got["width_by_role"]["power"], 0.19)

    def test_scaled_keeps_this_board_signal_width_and_applies_the_ratio(self):
        got = H.rules([self._reference(0.19, 0.10)], baseline_width_mm=0.15)
        self.assertEqual(got["width_by_role"]["signal"], 0.15)
        # 0.15 * (0.19 / 0.10) = 0.285, snapped to the 0.05 ladder -> 0.30
        self.assertEqual(got["width_by_role"]["power"], 0.30)
        self.assertIn("scaled_from", got)

    def test_one_reference_says_so(self):
        self.assertEqual(H.rules([self._reference(0.19, 0.10)])["confidence"], "one board")
        self.assertEqual(H.rules([self._reference(0.19, 0.10)] * 3)["confidence"],
                         "consensus of 3")

    def test_no_reference_is_an_answer_not_an_empty_object(self):
        got = H.rules([])
        self.assertEqual(got["boards"], 0)
        self.assertIn("nothing to copy", got["why"])

class TestTheFixList(unittest.TestCase):
    def test_a_net_at_the_target_width_is_not_in_the_fix_list(self):
        board = _board([_track("PA1", (0, 0), (5, 0), 0.15)])
        got = H.diffs(board, {"width_by_role": {"signal": 0.15}})
        self.assertEqual(got["nets_to_change"], 0)
        self.assertEqual(got["nets_already_right"], 1)

    def test_a_power_net_at_signal_width_is_and_carries_the_op(self):
        board = _board([_track("+3V3", (0, 0), (5, 0), 0.15),
                        _track("PA1", (0, 1), (5, 1), 0.15)])
        got = H.diffs(board, {"width_by_role": {"signal": 0.15, "power": 0.4}})
        self.assertEqual(got["nets_to_change"], 1)
        row = got["fix"][0]
        self.assertEqual(row["net"], "+3V3")
        self.assertEqual(row["op"], {"op": "set_track_width", "width": 0.4, "net": "+3V3"})

    def test_the_list_is_ordered_by_how_much_copper_it_touches(self):
        board = _board([_track("+3V3", (0, 0), (2, 0), 0.15),
                        _track("VBUS", (0, 1), (8, 1), 0.15)])
        got = H.diffs(board, {"width_by_role": {"signal": 0.15, "power": 0.4}})
        self.assertEqual([r["net"] for r in got["fix"]], ["VBUS", "+3V3"])

    def test_a_net_with_two_widths_is_listed_even_if_one_of_them_matches(self):
        board = _board([_track("+3V3", (0, 0), (5, 0), 0.15),
                        _track("+3V3", (5, 0), (9, 0), 0.4)])
        got = H.diffs(board, {"width_by_role": {"signal": 0.15, "power": 0.4}})
        self.assertEqual(got["nets_to_change"], 1)
        self.assertEqual(got["fix"][0]["current_widths_mm"], [0.15, 0.4])

    def test_a_role_the_rule_set_says_nothing_about_is_skipped_and_named(self):
        board = _board([_track("USB_DP", (0, 0), (5, 0), 0.15)])
        got = H.diffs(board, {"width_by_role": {"signal": 0.15}})
        self.assertEqual(got["nets_to_change"], 0)
        self.assertEqual(got["skipped"]["no_rule_for_role"], ["differential"])

class TestClearanceRoom(unittest.TestCase):
    """A width rule is not applicable until something says whether it fits."""

    def _pair(self, spacing, widths=(0.15, 0.15), clearance=0.15, planned=None):
        board = Board("test", "memory")
        board.tracks = []
        for i, (net, width) in enumerate(zip(("A", "B"), widths)):
            board.tracks.append(_track(net, (0, i * spacing), (20, i * spacing), width))
        board.vias = []
        board.footprints = []
        board.outline = []
        board.rules = {"clearance": {"min_clearance": clearance}}
        return H.clearance_room(board, planned_widths=planned)

    def test_a_neighbour_that_stays_put_allows_twice_the_slack(self):
        # d = 0.5, clearance 0.15, neighbour 0.15 -> 2*(0.5-0.15)-0.15 = 0.55
        got = self._pair(0.5)["max_width_mm"]
        self.assertAlmostEqual(got["A"], 0.55, places=4)

    def test_both_nets_growing_uses_the_symmetric_bound(self):
        # d - clearance = 0.35 when the neighbour grows too
        got = self._pair(0.5, planned={"A": 0.5, "B": 0.5})["max_width_mm"]
        self.assertAlmostEqual(got["A"], 0.35, places=4)

    def test_the_clearance_comes_from_the_board_rules(self):
        got = self._pair(0.5, clearance=0.2)["max_width_mm"]
        self.assertAlmostEqual(got["A"], 2 * (0.5 - 0.2) - 0.15, places=4)
        self.assertEqual(self._pair(0.5, clearance=0.2)["clearance_mm"], 0.2)

    def test_a_net_never_reports_less_than_its_current_width(self):
        got = self._pair(0.2)["max_width_mm"]
        self.assertGreaterEqual(got["A"], 0.15)

    def test_a_pad_is_an_obstacle_even_with_no_track_near_it(self):
        board = Board("test", "memory")
        board.tracks = [_track("A", (0, 0), (10, 0), 0.15)]
        board.vias = []
        board.outline = []
        board.rules = {"clearance": {"min_clearance": 0.15}}
        board.footprints = [{"ref": "U1", "value": "U", "x": 5, "y": 0.4,
                             "rotation": 0.0, "layer": "F.Cu",
                             "pads": [{"number": "1", "net": "B", "x": 5, "y": 0.4,
                                       "w": 0.3, "h": 0.3, "layers": ["F.Cu"]}]}]
        # the pad box edge is 0.25 from the track centre -> 2*(0.25-0.15) = 0.2
        self.assertAlmostEqual(H.clearance_room(board)["max_width_mm"]["A"], 0.2, places=4)

class TestARotatedPadIsNotAnAxisAlignedBox(unittest.TestCase):
    """J1 on the F412 board is a 270 degree socket: read unrotated, its 0.4 x 1.35
    pads reach 0.46 mm further than they do and VBUS reads 0.15 instead of 0.30."""

    def _board(self, deg):
        board = Board("test", "memory")
        board.tracks = [_track("A", (0, 0), (10, 0), 0.15)]
        board.vias = []
        board.outline = []
        board.rules = {"clearance": {"min_clearance": 0.15}}
        board.footprints = [{"ref": "J1", "value": "USB", "x": 5, "y": 0.9,
                             "rotation": deg, "layer": "F.Cu",
                             "pads": [{"number": "1", "net": "B", "x": 5, "y": 0.9,
                                       "w": 0.4, "h": 1.35, "rotation": deg,
                                       "layers": ["F.Cu"]}]}]
        return board

    def test_at_270_degrees_the_pad_points_along_x_and_the_track_fits(self):
        # box half-height is 0.2, track centre 0.9 away -> 2*(0.7-0.15) = 1.1
        got = H.clearance_room(self._board(270.0))["max_width_mm"]["A"]
        self.assertAlmostEqual(got, 1.1, places=3)

    def test_at_0_degrees_the_same_pad_reaches_further_and_allows_less(self):
        # box half-height is 0.675, track centre 0.9 away -> 2*(0.225-0.15) = 0.15
        got = H.clearance_room(self._board(0.0))["max_width_mm"]["A"]
        self.assertAlmostEqual(got, 0.15, places=3)

    def test_the_answer_names_the_neighbour_that_binds(self):
        got = H.clearance_room(self._board(0.0))
        self.assertEqual(got["bound_by"]["A"]["kind"], "pad")
        self.assertEqual(got["bound_by"]["A"]["neighbour"], "J1.1")

class TestThePairIsOneDecision(unittest.TestCase):
    def test_a_differential_pair_takes_the_tighter_side(self):
        board = Board("test", "memory")
        board.tracks = [_track("USB_DP", (0, 0), (10, 0), 0.15),
                        _track("USB_DM", (0, 0.8), (10, 0.8), 0.15),
                        _track("OTHER", (0, 1.1), (10, 1.1), 0.15)]
        board.vias = []
        board.footprints = []
        board.outline = []
        board.rules = {"clearance": {"min_clearance": 0.15}}
        got = H.diffs(board, {"width_by_role": {"signal": 0.15, "differential": 0.40}})
        self.assertTrue(got["pair_capped"])
        self.assertEqual(got["fix"], [])

class TestTheFixListIsApplicable(unittest.TestCase):
    def test_a_target_that_cannot_fit_is_capped_and_says_so(self):
        board = Board("test", "memory")
        board.tracks = [_track("+3V3", (0, 0), (10, 0), 0.15),
                        _track("PA1", (0, 0.3), (10, 0.3), 0.15)]
        board.vias = []
        board.footprints = []
        board.outline = []
        board.rules = {"clearance": {"min_clearance": 0.15}}
        got = H.diffs(board, {"width_by_role": {"signal": 0.15, "power": 2.0}})
        self.assertEqual(got["fix"], [])
        row = [b for b in got["blocked"] if b["net"] == "+3V3"][0]
        self.assertEqual(row["asked_mm"], 2.0)
        self.assertLess(row["fits_mm"], 2.0)
        self.assertEqual(got["capped"][0]["asked_mm"], 2.0)

    def test_cap_false_reports_the_rule_verbatim(self):
        board = Board("test", "memory")
        board.tracks = [_track("+3V3", (0, 0), (10, 0), 0.15)]
        board.vias = []
        board.footprints = []
        board.outline = []
        board.rules = {"clearance": {"min_clearance": 0.15}}
        got = H.diffs(board, {"width_by_role": {"signal": 0.15, "power": 2.0}}, cap=False)
        self.assertEqual(got["fix"][0]["target_width_mm"], 2.0)
        self.assertIsNone(got["clearance_room"])

if __name__ == "__main__":
    unittest.main()
