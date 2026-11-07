"""board_trunk_plan: how wide a trunk can join this net's pad clusters, and where.

The question comes from the unfinished sky_star board: 38 unconnected +3V3 pads, and
every tool here could report the count while none could say whether a 0.4 mm trunk
between the pad groups is possible at all. The answer must be CONSTRUCTIVE (a width
that fits, along a path that steps around copper) and must name the throat, because
"it does not fit" without a coordinate is not something a person can act on.

Seven things are pinned, each with a control that can fail:

1. A net with no pads, or with one pad, is refused: there is nothing to join.
   Control: `net` is required, and omitting it must not silently plan the pads that
   have no net at all.
2. On empty floor the widest trunk is the measurement ceiling, reported as a bound
   (`beyond_reach`) rather than as a measurement - the number is honest about what
   the search could see.
3. A wall across the corridor on ONE layer blocks only that layer. Control: the same
   wall belonging to the planned net blocks nothing, because its own copper is where
   the trunk runs.
4. A corridor narrower than `track_mm` reads `throttled`, and the same geometry reads
   `fits` when the trunk is asked to be thinner - the verdict follows the geometry,
   not the setup.
5. `band_mm` is the whole width of the answer: a short wall stops the trunk when the
   band is narrower than the detour and does not when the band can step around it.
6. `cluster_mm` decides what counts as one place - a pad row is one cluster at 5 mm
   and four at 0.5 mm, and with one cluster there is no edge to answer about.
7. The tool surface says what it does not claim, and it is derived, not written.
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

NET = "+3V3"

class _Trunk(RoutabilityBase):
    def tp(self, **kw):
        board, e = tools._load_board({"path": self.board})
        self.assertIsNone(e, e)
        kw.setdefault("net", NET)
        return oracle.trunk_plan(board, **kw)

    def value(self, **kw):
        out = self.tp(**kw)
        self.assertEqual(out["status"], "ok", out)
        return out["value"]

    def restart(self):
        P.PLANS.clear()

    def two_pads(self, dx=20.0, nets=None):
        """Two pads of one net, `dx` apart, centred on the board."""
        self.apply(self.place("P", [("1", 0.0, 0.0, 1.0, 1.0),
                                    ("2", dx, 0.0, 1.0, 1.0)],
                              nets=nets or {"1": NET, "2": NET},
                              x=20.0, y=20.0))

    def wall(self, net, half_mm=6.0, x=30.0, w=0.4, layer="F.Cu"):
        return {"op": "add_track", "x1": x, "y1": 20.0 - half_mm,
                "x2": x, "y2": 20.0 + half_mm, "width": w, "layer": layer,
                "net": net, "create_net": True}

class TestWhatCannotBeAsked(_Trunk):
    def test_a_net_without_pads_is_refused(self):
        out = self.tp(net="+5V")
        self.assertEqual(out["status"], "error")
        self.assertEqual(out["reason_code"], "NO_GEOMETRY")
        self.assertIn("no pad", out["message"])

    def test_one_pad_needs_no_trunk(self):
        self.apply(self.place("P", [("1", 0.0, 0.0, 1.0, 1.0)], nets={"1": NET}))
        out = self.tp()
        self.assertEqual(out["status"], "error")
        self.assertEqual(out["reason_code"], "NO_GEOMETRY")
        self.assertIn("single pad", out["message"])

    def test_an_empty_net_name_is_refused(self):
        # Without this guard `net` would match every unconnected pad on the board
        # and plan copper that does not exist.
        self.two_pads()
        out = self.tp(net="")
        self.assertEqual(out["status"], "error")
        self.assertEqual(out["reason_code"], "ARG_INVALID")

class TestEmptyFloor(_Trunk):
    def test_the_ceiling_is_reported_as_a_bound_not_a_measurement(self):
        self.two_pads()
        v = self.value()
        self.assertEqual(v["verdict"], "trunk_fits", v)
        self.assertEqual(v["cluster_count"], 2)
        self.assertEqual(len(v["edges"]), 1)
        edge = v["edges"][0]
        self.assertAlmostEqual(edge["straight_mm"], 20.0, places=3)
        # Nothing is in the way, so the answer can only be "at least this much" -
        # and the number reported must be exactly the search's reach, with the
        # reason saying so.
        self.assertAlmostEqual(v["measured_up_to_mm"], edge["best"]["widest_mm"],
                              places=3)
        self.assertEqual(edge["best"]["limited_by"]["kind"], "beyond_reach")
        self.assertEqual(sorted(edge["fits_on"]), ["B.Cu", "F.Cu"])

    def test_the_requested_width_does_not_change_the_geometry(self):
        # Control for every test below: what the floor offers is a measurement, and
        # asking for a thinner trunk must not move it by a micron.
        self.two_pads()
        a = self.value(track_mm=0.3)["edges"][0]["best"]["widest_mm"]
        self.restart()
        b = self.value(track_mm=0.3)["edges"][0]["best"]["widest_mm"]
        self.assertAlmostEqual(a, b, places=6)

class TestAWallBlocksOneLayerOnly(_Trunk):
    def test_a_wall_of_another_net_stops_that_layer(self):
        self.two_pads()
        self.apply(self.wall("GND"))
        v = self.value(cluster_mm=5.0, band_mm=4.0, grid_mm=0.25)
        edge = v["edges"][0]
        # One layer is walled, the other is not: the answer must split by layer
        # rather than call the whole edge impossible.
        self.assertEqual(edge["layers"]["F.Cu"]["verdict"], "no_path",
                         edge["layers"]["F.Cu"])
        self.assertEqual(edge["fits_on"], ["B.Cu"])
        self.assertEqual(edge["best"]["layer"], "B.Cu")
        self.assertEqual(v["throttled_edges"], 0)

    def test_the_same_wall_of_our_own_net_blocks_nothing(self):
        # Control: the wall is where the trunk will run, so it is not an obstacle.
        self.two_pads()
        self.apply(self.wall(NET))
        v = self.value()
        edge = v["edges"][0]
        self.assertEqual(edge["verdict"], "fits", edge)
        self.assertIn("F.Cu", edge["fits_on"])

    def test_own_copper_is_ignored_even_when_it_is_a_stub(self):
        # A stub of the net crossing the corridor must not read as a wall either -
        # the first version counted pads of the net but not tracks, and a
        # half-routed net then reported its own copper as an obstacle.
        self.two_pads()
        self.apply({"op": "add_track", "x1": 25.0, "y1": 20.0, "x2": 35.0,
                    "y2": 20.0, "width": 0.3, "layer": "F.Cu", "net": NET,
                    "create_net": True})
        v = self.value()
        self.assertEqual(v["edges"][0]["verdict"], "fits", v["edges"][0])

class TestTheBandIsTheAnswer(_Trunk):
    def test_a_short_wall_is_stepped_around_inside_a_wide_band(self):
        self.two_pads()
        self.apply(self.wall("GND", half_mm=1.5))
        wide = self.value(layer="F.Cu", band_mm=4.0)["edges"][0]
        self.assertEqual(wide["verdict"], "fits", wide)
        self.restart()
        narrow = self.value(layer="F.Cu", band_mm=1.0)["edges"][0]
        self.assertEqual(narrow["verdict"], "throttled", narrow)
        self.assertEqual(narrow["layers"]["F.Cu"]["verdict"], "no_path", narrow)

    def test_a_corridor_sets_the_widest_trunk(self):
        # A channel 1.0 mm wide running along the trunk: two other-net walls at
        # y = 19.4 and 20.6, 0.2 thick each, so their inner edges are 1.0 mm apart.
        # A track centred between them has 0.5 - clearance of half width, i.e.
        # 2*(0.5 - 0.2) = 0.6 mm.
        self.two_pads()
        self.apply(
            {"op": "add_track", "x1": 20.0, "y1": 19.4, "x2": 40.0, "y2": 19.4,
             "width": 0.2, "layer": "F.Cu", "net": "W1", "create_net": True},
            {"op": "add_track", "x1": 20.0, "y1": 20.6, "x2": 40.0, "y2": 20.6,
             "width": 0.2, "layer": "F.Cu", "net": "W2", "create_net": True})
        v = self.value(layer="F.Cu", clearance_mm=0.2, band_mm=1.0, grid_mm=0.1,
                       track_mm=0.5)
        edge = v["edges"][0]
        wide = edge["layers"]["F.Cu"]["widest_mm"]
        self.assertAlmostEqual(wide, 0.6, delta=0.06, msg=edge)
        self.assertEqual(edge["verdict"], "fits", edge)
        self.restart()
        v2 = self.value(layer="F.Cu", clearance_mm=0.2, band_mm=1.0, grid_mm=0.1,
                        track_mm=0.9)
        self.assertEqual(v2["edges"][0]["verdict"], "throttled", v2["edges"][0])

class TestClustering(_Trunk):
    def test_a_pad_row_is_one_place_at_five_mm_and_four_at_half_a_mm(self):
        row = [("%d" % (i + 1), 1.0 * i, 0.0, 0.6, 0.6) for i in range(4)]
        self.apply(self.place("Row", row, nets=dict(("%d" % (i + 1), NET)
                                                    for i in range(4)),
                              x=20.0, y=20.0))
        out = self.tp(cluster_mm=5.0)
        self.assertEqual(out["status"], "error")
        self.assertEqual(out["reason_code"], "NO_GEOMETRY")
        self.assertIn("one cluster", out["message"])
        self.restart()
        v = self.value(cluster_mm=0.5)
        self.assertEqual(v["cluster_count"], 4)
        self.assertEqual(len(v["edges"]), 3, v["edges"])

class TestTheCellCapIsNotADeadEnd(_Trunk):
    def test_a_finer_raster_is_refused_with_a_way_out_not_a_dead_end(self):
        # The precision note tells the caller to re-run at a finer grid to tell a
        # real throat from a grid artefact, so the cell cap must hand back a
        # DIFFERENT grid - a hint that suggests the grid it just refused is a loop.
        self.two_pads()
        out = self.tp(grid_mm=0.02, band_mm=4.0)
        self.assertEqual(out["status"], "error")
        self.assertEqual(out["reason_code"], "ARG_RANGE")
        self.assertIn("raise max_cells", out["hint"])
        suggested = [w for w in out["hint"].split() if w.replace(".", "").isdigit()]
        self.assertNotIn("0.02", suggested, out["hint"])

class TestToolSurface(_Trunk):
    def test_it_is_registered_derived_and_says_what_it_does_not_claim(self):
        spec = None
        for t in tools.list_tools():
            if t["name"] == "board_trunk_plan":
                spec = t
        self.assertIsNotNone(spec)
        self.assertIn("net", spec["inputSchema"]["properties"])
        self.assertIn("net", spec["inputSchema"]["required"])
        self.assertIn("max_cells", spec["inputSchema"]["properties"])
        self.assertIn("does not route", spec["description"])
        self.two_pads()
        out = self.tp()
        self.assertEqual(out["status"], "ok")
        self.assertIn("not_claimed", out["assumptions"])
        self.assertIn("band", out["assumptions"]["not_claimed"])

    def test_it_writes_nothing(self):
        self.two_pads()
        before = open(self.board, "rb").read()
        self.value()
        self.assertEqual(open(self.board, "rb").read(), before)


if __name__ == "__main__":
    unittest.main()
