"""Does pouring a rail actually widen it where it is narrow?

`route/rail_plan.py` ends every unbalanced rail with `pour_needed` and a
`needs_outline: true` action, because it will not invent a shape. This module
answers the next question on a temp copy with KiCad's own `ZONE_FILLER`: pour
the board outline over the rail's own layer, then measure the copper that came
out - how much of the rail it covers, and how wide the copper is ACROSS the rail
at its narrowest point.

Everything here is the geometry and the verdict, not the engine: the only test
that touches `measure()` stubs the filler, because a unit test that needs KiCad
installed is a test that stops running. The real-board numbers live in
`docs/207`.

Two formulations are pinned down as *reverse controls*, because both of them are
plausible and both of them were wrong here first:

* `2*distance(sample, boundary)` reads a 206.72 mm2 plane as 0.0000 mm whenever
  the rail sits on the fill's edge - which is the normal case;
* dropping the `w/2` in `max(0, reach - w/2)` adds copper that is not there
  (copper the track already occupies) and inflates every reading by the track
  width.
"""
import os
import sys
import tempfile
import unittest
from types import SimpleNamespace
from unittest import mock

sys.path.insert(0, os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "src"))

from pcb_agent_mcp.route import rail_pour as RP  # noqa: E402

SQUARE = [(0.0, 0.0), (10.0, 0.0), (10.0, 10.0), (0.0, 10.0)]


def track(net, layer, a, b, width=0.1):
    return {"uuid": "t", "net": net, "layer": layer, "width": width,
            "start": list(a), "end": list(b), "locked": False}


def board(tracks=(), zones=(), outline=None, stackup=None, rules=None,
          vias=(), footprints=(), path=r"D:\tmp\does_not_exist.kicad_pcb"):
    cu = stackup or ["F.Cu", "In1.Cu", "In2.Cu", "In3.Cu", "In4.Cu", "B.Cu"]
    return SimpleNamespace(
        tracks=list(tracks), vias=list(vias), zones=list(zones),
        footprints=list(footprints),
        stackup=[{"name": n} for n in cu],
        outline=outline if outline is not None else [SQUARE],
        rules=rules or {},
        source={"path": path})


class TheRayIsAFirstCrossing(unittest.TestCase):
    def test_four_directions_from_the_centre_all_reach_the_square(self):
        # The `u` denominator sign was wrong here first: it returned None for a
        # plain square in every direction, which read as "pour adds nothing".
        for d in ((1, 0), (-1, 0), (0, 1), (0, -1)):
            self.assertAlmostEqual(RP._ray_t((5.0, 5.0), d, SQUARE), 5.0, places=9)

    def test_a_sample_on_the_edge_reaches_zero_not_none(self):
        self.assertEqual(RP._ray_t((0.0, 5.0), (1, 0), SQUARE), 0.0)
        self.assertEqual(RP._ray_t((0.0, 5.0), (-1, 0), SQUARE), 0.0)

    def test_only_the_first_crossing_counts(self):
        # A U-shaped fill: leaving by the near arm is where the copper ends.
        u_shape = [(0, 0), (10, 0), (10, 10), (6, 10), (6, 4), (4, 4), (4, 10),
                   (0, 10)]
        self.assertAlmostEqual(RP._ray_t((5.0, 2.0), (0, 1), u_shape), 2.0,
                               places=9)
        self.assertAlmostEqual(RP._ray_t((2.0, 5.0), (1, 0), u_shape), 2.0,
                               places=9)

    def test_a_ray_that_never_leaves_says_none(self):
        self.assertIsNone(RP._ray_t((5.0, 5.0), (1, 0), [(0, 0), (10, 0)]))

    def test_diagonal_is_the_euclidean_distance(self):
        self.assertAlmostEqual(RP._ray_t((5.0, 5.0), (0.7071067811865476,
                                                      0.7071067811865476),
                                         SQUARE), 7.0710678118654755, places=9)


class TheCorridorIsAcrossTheRail(unittest.TestCase):
    def test_a_pour_that_extends_two_mm_each_side_reads_track_plus_four(self):
        pour = [[(-5, -2), (15, -2), (15, 2), (-5, 2)]]
        b = board([track("/P", "In4.Cu", (0, 0), (10, 0))])
        c = RP.corridor(b, "/P", "In4.Cu", sample_mm=0.5, polys=pour)
        self.assertAlmostEqual(c["min_width_mm"], 4.0, places=6)
        self.assertAlmostEqual(c["width_p50_mm"], 4.0, places=6)
        self.assertEqual(c["bare_side_samples"], 0)

    def test_a_pour_that_only_touches_the_track_edges_adds_nothing(self):
        # 0.05 mm each side is exactly half the 0.1 mm track: copper the track
        # already occupies is not copper the pour adds.
        pour = [[(0, -0.05), (10, -0.05), (10, 0.05), (0, 0.05)]]
        b = board([track("/P", "In4.Cu", (0, 0), (10, 0))])
        c = RP.corridor(b, "/P", "In4.Cu", sample_mm=0.5, polys=pour)
        self.assertAlmostEqual(c["min_width_mm"], 0.1, places=6)
        self.assertEqual(c["bare_side_samples"], c["covered"])
        self.assertEqual(c["one_side_samples"], 0)

    def test_a_pour_narrower_than_the_track_never_subtracts(self):
        # Reverse control for the `max(0, ...)`: without it this would read
        # 0.06 mm - a width no copper on the board has.
        pour = [[(0, -0.02), (10, -0.02), (10, 0.02), (0, 0.02)]]
        b = board([track("/P", "In4.Cu", (0, 0), (10, 0))])
        c = RP.corridor(b, "/P", "In4.Cu", sample_mm=0.5, polys=pour)
        self.assertAlmostEqual(c["min_width_mm"], 0.1, places=6)

    def test_a_rail_on_the_fill_edge_is_not_zero_width(self):
        # The common real case: a neighbour routed at minimum clearance puts the
        # fill's edge exactly on this track's edge (0.05 mm from its centre), so
        # the pour adds nothing on that side while extending 1.9 mm on the
        # other. `2*distance(point, boundary)` sees only the 0.05 and reports
        # 0.1 mm - the RK3506 `+3V3` misread, which read 0.0000 mm on a
        # 206.72 mm2 plane. This one must read the copper that is there.
        pour = [[(-5, -2), (15, -2), (15, 0.05), (-5, 0.05)]]
        b = board([track("/P", "In4.Cu", (0, 0), (10, 0))])
        c = RP.corridor(b, "/P", "In4.Cu", sample_mm=0.5, polys=pour)
        self.assertAlmostEqual(c["min_width_mm"], 2.05, places=6)
        self.assertAlmostEqual(c["reach_left_mm"], 0.05, places=6)
        self.assertAlmostEqual(c["reach_right_mm"], 2.0, places=6)
        self.assertEqual(c["one_side_samples"], c["covered"])
        self.assertEqual(c["bare_side_samples"], 0)

    def test_the_minimum_is_the_bottleneck_and_at_names_it(self):
        # 4.0 mm wide for x < 8, then the pour is pinched to 0.05 mm below the
        # track: the bottleneck is at the pinch, and the median stays wide.
        pour = [[(-5, -2), (8, -2), (8, -0.05), (15, -0.05), (15, 2), (-5, 2)]]
        b = board([track("/P", "In4.Cu", (0, 0), (10, 0))])
        c = RP.corridor(b, "/P", "In4.Cu", sample_mm=0.5, polys=pour)
        self.assertAlmostEqual(c["min_width_mm"], 2.05, places=6)
        self.assertGreaterEqual(c["at"][0], 8.0)
        self.assertGreater(c["width_p50_mm"], 3.9)
        self.assertAlmostEqual(c["track_width_there_mm"], 0.1, places=6)

    def test_a_track_outside_the_pour_is_uncovered_length_not_zero_width(self):
        pour = [[(0, -2), (5, -2), (5, 2), (0, 2)]]
        b = board([track("/P", "In4.Cu", (0, 0), (10, 0))])
        c = RP.corridor(b, "/P", "In4.Cu", sample_mm=0.5, polys=pour)
        self.assertAlmostEqual(c["rail_length_mm"], 10.0, places=6)
        self.assertAlmostEqual(c["uncovered_length_mm"], 5.0, places=6)
        self.assertAlmostEqual(c["uncovered_share"], 0.5, places=6)

    def test_no_pour_and_no_track_both_report_rather_than_guess(self):
        b = board([track("/P", "In4.Cu", (0, 0), (10, 0))])
        c = RP.corridor(b, "/P", "In4.Cu", polys=[])
        self.assertFalse(c["filled"])
        self.assertEqual(c["pieces"], 0)
        self.assertIsNone(c["min_width_mm"])
        c2 = RP.corridor(b, "/P", "In3.Cu", polys=[[(0, 0), (1, 0), (1, 1)]])
        self.assertEqual(c2["samples"], 0)
        self.assertIsNone(c2["min_width_mm"])

    def test_a_45_degree_track_uses_its_own_perpendicular(self):
        # A 2 mm wide band along a diagonal track: the perpendicular is the
        # band's normal, so the width is 2.0 mm - not the 2.83 mm an axis-aligned
        # chord would give, and not the 2.0/sqrt(2) a mis-projected reading would.
        r = 2 ** 0.5
        u = (1 / r, 1 / r)
        n = (-1 / r, 1 / r)
        along = 10.0 / r
        half = 1.0
        def pt(t, s):
            return (u[0] * t + n[0] * s, u[1] * t + n[1] * s)
        pour = [[pt(-2, -half), pt(along + 2, -half), pt(along + 2, half),
                 pt(-2, half)]]
        b = board([track("/P", "In4.Cu", (0, 0), (u[0] * along, u[1] * along))])
        c = RP.corridor(b, "/P", "In4.Cu", sample_mm=0.5, polys=pour)
        self.assertAlmostEqual(c["min_width_mm"], 2.0, places=6)


class JudgeSaysWhatTheMeasurementProves(unittest.TestCase):
    def row(self, **cor):
        base = {"filled": True, "corridor": {"samples": 10, "covered": 10,
                                             "min_width_mm": 0.4,
                                             "uncovered_share": 0.0,
                                             "bare_side_samples": 0}}
        base["corridor"].update(cor)
        return base

    def test_an_unfilled_zone_is_unknown_not_narrow(self):
        v, why, _ = RP.judge({"filled": False}, 0.3)
        self.assertEqual(v, "pour_not_filled")
        self.assertIn("unknown", why)

    def test_a_rail_with_no_track_on_this_layer_is_no_copper(self):
        v, _, _ = RP.judge(self.row(samples=0, covered=0), 0.3)
        self.assertEqual(v, "no_copper")

    def test_no_sample_inside_the_pour_is_rail_not_covered(self):
        v, _, flags = RP.judge(self.row(min_width_mm=None, uncovered_share=1.0),
                               0.3)
        self.assertEqual(v, "rail_not_covered")
        self.assertIn("rail_partly_outside_pour", flags)

    def test_no_demand_is_not_a_verdict(self):
        v, why, _ = RP.judge(self.row(), None)
        self.assertEqual(v, "width_unproven")
        self.assertIn("no current", why)

    def test_a_bound_at_or_above_the_demand_proves_it(self):
        v, _, _ = RP.judge(self.row(min_width_mm=0.4), 0.4)
        self.assertEqual(v, "width_proven")

    def test_a_bound_below_the_demand_cannot_certify(self):
        v, why, _ = RP.judge(self.row(min_width_mm=0.1), 0.3)
        self.assertEqual(v, "width_unproven")
        self.assertIn("NOT proof", why)

    def test_a_pour_that_adds_nothing_is_flagged_even_when_wide_enough(self):
        v, _, flags = RP.judge(self.row(min_width_mm=0.4, bare_side_samples=3), 0.3)
        self.assertEqual(v, "width_proven")
        self.assertIn("pour_adds_no_copper_on_part_of_the_rail", flags)


class TheLayerToPourOn(unittest.TestCase):
    def test_the_inner_layer_with_the_most_rail_wins(self):
        b = board([track("/P", "In2.Cu", (0, 0), (5, 0)),
                   track("/P", "In4.Cu", (0, 5), (8, 5)),
                   track("/P", "F.Cu", (0, 9), (12, 9))])
        layer, why = RP.pick_layer(b, "/P")
        self.assertEqual(layer, "In4.Cu")
        self.assertFalse(why["outer_layer"])
        self.assertEqual(why["length_mm"], 8.0)

    def test_an_outer_layer_is_only_used_when_there_is_no_inner_copper(self):
        b = board([track("/P", "F.Cu", (0, 9), (12, 9))])
        layer, why = RP.pick_layer(b, "/P")
        self.assertEqual(layer, "F.Cu")
        self.assertTrue(why["outer_layer"])

    def test_a_named_layer_is_checked_against_the_board(self):
        b = board([track("/P", "In4.Cu", (0, 5), (8, 5))])
        self.assertEqual(RP.pick_layer(b, "/P", "In2.Cu")[0], "In2.Cu")
        layer, why = RP.pick_layer(b, "/P", "In9.Cu")
        self.assertIsNone(layer)
        self.assertEqual(why["reason"], "layer_not_on_board")

    def test_a_net_with_no_copper_has_nothing_to_pour(self):
        self.assertEqual(RP.pick_layer(board(), "/NOPE")[1]["reason"], "no_copper")

    def test_rail_layers_reports_length_segments_widths_vias_and_pads(self):
        b = board([track("/P", "In4.Cu", (0, 0), (3, 4)),
                   track("/P", "In4.Cu", (0, 0), (0, 6), width=0.2)],
                  vias=[{"net": "/P", "layers": ["In4.Cu", "B.Cu"]}],
                  footprints=[{"pads": [{"net": "/P", "layers": ["*.Cu"]}]}])
        per = RP.rail_layers(b, "/P")
        self.assertEqual(per["In4.Cu"]["segments"], 2)
        self.assertAlmostEqual(per["In4.Cu"]["length_mm"], 11.0, places=6)
        self.assertEqual(per["In4.Cu"]["widths"], [0.1, 0.2])
        self.assertEqual(per["In4.Cu"]["vias"], 1)
        self.assertEqual(per["In4.Cu"]["pads"], 1)  # *.Cu counts once per layer
        self.assertEqual(per["B.Cu"]["pads"], 1)
        self.assertEqual(per["B.Cu"]["vias"], 1)


class ZonePolygonsAndBoardParameters(unittest.TestCase):
    def test_only_this_net_on_this_layer(self):
        zones = [
            {"net": "/P", "layers": ["In4.Cu"],
             "filled_polygons": [[(0, 0), (1, 0), (1, 1)]]},
            {"net": "/P", "layers": ["In2.Cu"],
             "filled_polygons": [[(0, 0), (2, 0), (2, 2)]]},
            {"net": "/Q", "layers": ["In4.Cu"],
             "filled_polygons": [[(0, 0), (3, 0), (3, 3)]]},
            {"net": "/P", "layers": ["In4.Cu"], "filled_polygons": [[(0, 0)]]},
        ]
        polys = RP.zone_polygons(board(zones=zones), "/P", "In4.Cu")
        self.assertEqual(len(polys), 1)
        self.assertEqual(polys[0][1], (1.0, 0.0))

    def test_clearance_and_thickness_come_from_the_board(self):
        b = board(rules={"clearance": {"min_clearance": 0.1}},
                  zones=[{"min_thickness": 0.15}, {"min_thickness": 0.1}])
        p = RP.board_zone_params(b)
        self.assertEqual(p["clearance_mm"], 0.1)
        self.assertEqual(p["clearance_source"], "board.rules.min_clearance")
        self.assertEqual(p["min_thickness_mm"], 0.1)

    def test_a_board_without_the_numbers_says_so(self):
        p = RP.board_zone_params(board())
        self.assertIsNone(p["clearance_mm"])
        self.assertIsNone(p["min_thickness_mm"])


class MeasureReportsTheEngineAndNeverWrites(unittest.TestCase):
    def _temp_board(self, **kw):
        fd, path = tempfile.mkstemp(suffix=".kicad_pcb")
        os.close(fd)
        with open(path, "w", encoding="utf-8") as f:
            f.write("(kicad_pcb)")
        self.addCleanup(lambda: os.path.exists(path) and os.remove(path))
        return board(path=path, **kw)

    def test_a_refused_pour_is_not_a_narrow_pour(self):
        b = self._temp_board(tracks=[track("/P", "In4.Cu", (0, 0), (10, 10))])
        with mock.patch("pcb_agent_mcp.fidelity.fill.add_zone",
                        return_value={"status": "error", "reason": "no engine"}):
            row = RP.measure(b, "/P")
        self.assertEqual(row["verdict"], "engine_refused")
        self.assertIn("no engine", row["why"])
        self.assertEqual(row["pieces"], 0)

    def test_the_source_file_is_untouched(self):
        b = self._temp_board(tracks=[track("/P", "In4.Cu", (0, 0), (10, 10))])
        with open(b.source["path"], "rb") as f:
            before = f.read()
        with mock.patch("pcb_agent_mcp.fidelity.fill.add_zone",
                        return_value={"status": "error", "reason": "x"}):
            RP.measure(b, "/P")
        with open(b.source["path"], "rb") as f:
            self.assertEqual(f.read(), before)

    def test_a_net_with_no_copper_never_reaches_the_engine(self):
        b = self._temp_board()
        with mock.patch("pcb_agent_mcp.fidelity.fill.add_zone") as add:
            row = RP.measure(b, "/NOPE")
        self.assertEqual(row["verdict"], "no_copper")
        add.assert_not_called()

    def test_no_outline_means_no_pour_to_even_attempt(self):
        b = board(tracks=[track("/P", "In4.Cu", (0, 0), (10, 10))], outline=[])
        with mock.patch("pcb_agent_mcp.fidelity.fill.add_zone") as add:
            row = RP.measure(b, "/P")
        self.assertEqual(row["verdict"], "no_copper")
        self.assertIn("outline", row["why"])
        add.assert_not_called()

    def test_the_pour_parameters_are_the_board_s_own(self):
        b = self._temp_board(tracks=[track("/P", "In4.Cu", (0, 0), (10, 10))],
                             rules={"clearance": {"min_clearance": 0.1}},
                             zones=[{"min_thickness": 0.1}])
        with mock.patch("pcb_agent_mcp.fidelity.fill.add_zone",
                        return_value={"status": "error", "reason": "x"}) as add:
            RP.measure(b, "/P")
        kwargs = add.call_args.kwargs
        self.assertAlmostEqual(kwargs["clearance"], 0.1)
        self.assertAlmostEqual(kwargs["min_thickness"], 0.1)
        self.assertEqual(kwargs["connect"], "solid")
        self.assertTrue(kwargs["fill_it"])


if __name__ == "__main__":
    unittest.main()
