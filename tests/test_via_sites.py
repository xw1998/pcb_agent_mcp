"""board_via_sites: where may a via legally go, and how much room has it got.

The gap this closes was measured, not imagined: in the stitching round only four of
sixteen ground islands had room for a 0.45 hole, and two of those four put the hole
into PD13/PD14's track - which the script only found by rebuilding the board and
reading DRC.

Six things are pinned, each with a control that can fail:

1. On an empty board the only limit is the edge, and the room is
   edge distance - (edge clearance + radius). Control: a point closer than that is
   refused, and `killed_by` says which rule refused it.
2. Another net's pad 0.5 mm away costs the room. Control: the SAME pad, with the
   requested via on that pad's net, does not - own copper is where a via connects.
3. A pour of another net refuses a hole inside it (`pour_interior`). Control: the
   same pour on the requested net leaves the site legal.
4. Drill-to-drill is a fab rule and is never waived by `net`: two 0.3 drills need
   0.55 mm of centre distance, so 0.5 is refused and 0.6 is not.
5. The search window is honoured and clipped to the board; a window outside the
   board is refused rather than answered "no site".
6. Omitted arguments fall back to the board's own rules and SAY so.
"""
import math
import os
import sys
import unittest

sys.path.insert(0, os.path.join(os.path.dirname(os.path.dirname(
    os.path.abspath(__file__))), "src"))
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

from pcb_agent_mcp import oracle
from pcb_agent_mcp import tools

from test_routability import RoutabilityBase

W, H = 60.0, 40.0


class _Sites(RoutabilityBase):
    def sites(self, **kw):
        board, e = tools._load_board({"path": self.board})
        self.assertIsNone(e, e)
        return oracle.via_sites(board, **kw)

    def at(self, x, y, **kw):
        return self.sites(region=[x, y, x, y], **kw)


class TestWhatLimitsRoom(_Sites):
    def test_an_empty_board_is_limited_by_the_edge(self):
        out = self.at(30.0, 20.0)
        self.assertEqual(out["status"], "ok", out)
        v = out["value"]
        self.assertEqual(v["verdict"], "sites_exist")
        site = v["sites"][0]
        self.assertEqual(site["limited_by"], "board edge")
        a = out["assumptions"]
        expect = min(30.0, 60.0 - 30.0, 20.0, 40.0 - 20.0) - \
            (a["edge_clearance_mm"] + v["via"]["diameter_mm"] / 2.0)
        self.assertAlmostEqual(site["room_mm"], expect, places=3)

    def test_a_point_too_close_to_the_edge_is_refused(self):
        # Control for the test above: the same board, 0.2 mm from the edge, must be
        # killed by exactly the edge rule.
        out = self.at(W - 0.2, 20.0)
        v = out["value"]
        self.assertEqual(v["verdict"], "no_legal_site")
        self.assertEqual(v["killed_by"]["edge_clearance"], 1)
        self.assertEqual(v["killed_by"]["copper_clearance"], 0)

    def test_another_nets_pad_costs_the_room(self):
        self.apply(self.place("P", [("1", 0.0, 0.0, 1.0, 1.0)], nets={"1": "OTHER"}))
        # pad edge is 0.5 mm from the probe
        out = self.at(31.0, 20.0, net="SIG")
        a, v = out["assumptions"], out["value"]
        site = v["sites"][0]
        self.assertEqual(site["limited_by"], "pad on F.Cu")
        expect = 0.5 - (a["clearance_mm"] + v["via"]["diameter_mm"] / 2.0)
        self.assertAlmostEqual(site["room_mm"], expect, places=3)
        self.assertEqual(site["nearest"], {"kind": "pad", "net": "OTHER",
                                           "ref": "U1.1"})

    def test_own_copper_does_not_cost_anything(self):
        # Control: the same pad, same probe, asking for the via on THAT pad's net.
        # If the number came from "there is copper nearby" rather than from the net
        # rule, this case could not differ.
        self.apply(self.place("P", [("1", 0.0, 0.0, 1.0, 1.0)], nets={"1": "OTHER"}))
        site = self.at(31.0, 20.0, net="OTHER")["value"]["sites"][0]
        self.assertEqual(site["limited_by"], "board edge")
        self.assertGreater(site["room_mm"], 10.0)


class TestDrillRule(_Sites):
    def test_two_drills_need_centre_distance_of_drill_plus_wall(self):
        self.apply({"op": "add_net", "name": "SIG"},
                   {"op": "add_via", "x": 30.0, "y": 20.0, "drill": 0.3,
                    "diameter": 0.5, "net": "SIG", "layers": ["F.Cu", "B.Cu"]})
        wall = self.at(30.0, 20.0, net="SIG")["assumptions"]["hole_to_hole_mm"]
        # 0.5 apart: 0.5 - 0.15 - 0.15 = 0.20 < wall, so refused
        too_close = self.at(30.5, 20.0, net="SIG")["value"]
        self.assertEqual(too_close["verdict"], "no_legal_site", too_close["sites"])
        self.assertEqual(too_close["killed_by"]["hole_to_hole"], 1)
        # 0.6 apart: 0.30 >= wall, so legal - the same board, 0.1 mm further out,
        # which is what makes this a measurement of the rule and not of the board.
        legal = self.at(30.0 + 0.3 + wall + 0.01, 20.0, net="SIG")["value"]
        self.assertEqual(legal["verdict"], "sites_exist", legal["killed_by"])
        self.assertAlmostEqual(legal["sites"][0]["room_mm"], 0.01, places=2)


class TestPourBlocksOnlyOtherNets(_Sites):
    def _pour(self, net, pours_block=False):
        self.apply({"op": "add_net", "name": "GND"},
                   {"op": "add_zone", "net": "GND", "layer": "F.Cu",
                    "x0": 20, "y0": 10, "x1": 40, "y1": 30})
        board, e = tools._load_board({"path": self.board})
        self.assertIsNone(e, e)
        board.zones[0]["filled_polygons"] = [
            [(20.0, 10.0), (40.0, 10.0), (40.0, 30.0), (20.0, 30.0)]]
        return oracle.via_sites(board, region=[30, 20, 30, 20], net=net,
                                pours_block=pours_block)["value"]

    def test_another_nets_pour_is_reported_not_refused(self):
        # The filler cuts a plane back around whatever is added, so a hole inside
        # another net's pour is a bite out of the plane, not a short. The answer has
        # to SAY which plane would be bitten, or the caller cannot tell the two
        # apart.
        v = self._pour("+3V3")
        self.assertEqual(v["verdict"], "sites_exist", v["killed_by"])
        self.assertEqual(v["sites"][0]["inside_pours"], ["GND"])

    def test_asking_for_strict_pours_refuses_it(self):
        # Control for the test above: the same geometry, one flag, opposite answer.
        v = self._pour("+3V3", pours_block=True)
        self.assertEqual(v["verdict"], "no_legal_site")
        self.assertEqual(v["killed_by"]["pour_interior"], 1)

    def test_a_pour_of_the_same_net_is_where_the_via_belongs(self):
        # Control: a stitching via inside its own pour is the normal case, so the
        # same geometry on the same net must be legal and bite nothing.
        v = self._pour("GND")
        self.assertEqual(v["verdict"], "sites_exist", v["killed_by"])
        self.assertEqual(v["sites"][0]["inside_pours"], [])

    def test_just_outside_a_strict_pour_the_room_is_the_gap(self):
        # 0.5 mm outside the pour edge, with the plane held fixed, the pour is the
        # limit and the room is that gap minus the clearance and the radius.
        v = self._pour_at(19.5, 20.0, "+3V3", pours_block=True)
        self.assertEqual(v["sites"][0]["limited_by"], "pour edge on F.Cu")
        self.assertLess(v["sites"][0]["room_mm"], 0.5)

    def _pour_at(self, x, y, net, pours_block=False):
        self.apply({"op": "add_net", "name": "GND"},
                   {"op": "add_zone", "net": "GND", "layer": "F.Cu",
                    "x0": 20, "y0": 10, "x1": 40, "y1": 30})
        board, e = tools._load_board({"path": self.board})
        self.assertIsNone(e, e)
        board.zones[0]["filled_polygons"] = [
            [(20.0, 10.0), (40.0, 10.0), (40.0, 30.0), (20.0, 30.0)]]
        return oracle.via_sites(board, region=[x, y, x, y], net=net,
                                pours_block=pours_block)["value"]


class TestWindow(_Sites):
    def test_a_window_outside_the_board_is_refused(self):
        out = self.at(500.0, 500.0)
        self.assertEqual(out["status"], "error")
        self.assertEqual(out["reason_code"], "ARG_RANGE")

    def test_the_window_is_clipped_to_the_board(self):
        # Centred 2 mm from the corner with a 5 mm radius: the window must be pulled
        # back to the board, not hang off it.
        v = self.sites(near=[2.0, 2.0], radius_mm=5.0)["value"]
        w = v["window"]
        self.assertGreaterEqual(w["x0"], -1e-6)
        self.assertGreaterEqual(w["y0"], -1e-6)
        self.assertLessEqual(w["x1"], W + 1e-6)
        self.assertLessEqual(w["y1"], H + 1e-6)
        self.assertGreater(v["legal_sites"], 0)

    def test_a_scan_too_large_to_answer_is_refused_with_what_would_fit(self):
        # The whole board at 0.2 mm is 79 200 candidates and took minutes. Refusing
        # with a number is the honest answer; timing out is not.
        out = self.sites(near=[30.0, 20.0], radius_mm=50.0)
        self.assertEqual(out["status"], "error")
        self.assertEqual(out["reason_code"], "ARG_RANGE")
        self.assertIn("grid_mm", out["hint"])


class TestToolSurface(_Sites):
    def test_the_handler_takes_a_region_and_says_what_it_assumed(self):
        out = tools.TOOLS["board_via_sites"]["handler"](
            {"path": self.board, "region": ["30", "20", "30", "20"]})
        self.assertEqual(out["status"], "ok", out)
        self.assertIn("hole_to_hole_mm", out["assumptions"])
        self.assertEqual(out["value"]["via"]["drill_mm"], 0.3)

    def test_a_bad_region_is_refused(self):
        out = tools.TOOLS["board_via_sites"]["handler"](
            {"path": self.board, "region": ["left", "top"]})
        self.assertEqual(out["status"], "error")
        self.assertEqual(out["reason_code"], "ARG_INVALID")

    def test_a_named_layer_must_exist(self):
        out = self.tools_at(30.0, 20.0, layers=["F.SilkS"])
        self.assertEqual(out["status"], "error")
        self.assertEqual(out["reason_code"], "ARG_INVALID")

    def tools_at(self, x, y, **kw):
        board, e = tools._load_board({"path": self.board})
        self.assertIsNone(e, e)
        return oracle.via_sites(board, region=[x, y, x, y], **kw)


if __name__ == "__main__":
    unittest.main()
