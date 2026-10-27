"""Tests for the Oracle (measurement) layer.

Each expected number below is geometric and checked by hand, because a measuring
layer that is only tested for "does not crash" is worse than none: it will happily
report a clearance of 0 on a board it never understood.

The board fixtures are minimal `.kicad_pcb` texts so that the Oracle is exercised
through a real adapter, not through hand-built dicts - a dict-based test would
pass even if an adapter silently produced the wrong net names.
"""
import os
import sys
import unittest

sys.path.insert(0, os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "src"))

from pcb_agent_mcp import oracle  # noqa: E402
from pcb_agent_mcp.adapters import kicad_pcb  # noqa: E402

HERE = os.path.dirname(os.path.abspath(__file__))
SAMPLE = os.path.join(HERE, "data", "sample.kicad_pcb")

STACKUP = """(setup (stackup (layer "F.Cu" (type "copper") (thickness 0.035))
    (layer "dielectric 1" (type "core") (thickness 0.2) (epsilon_r 4.3))
    (layer "B.Cu" (type "copper") (thickness 0.035))))"""


def two_parallel_tracks(centerline_gap_mm, width_mm=0.2):
    """Two tracks on different nets, running parallel along x."""
    y2 = 10.0 + centerline_gap_mm
    return """(kicad_pcb (version 20221018) %s
      (net 0 "") (net 1 "A") (net 2 "B")
      (gr_rect (start 0 0) (end 40 40) (layer "Edge.Cuts") (width 0.1) (fill none))
      (segment (start 5 10) (end 35 10) (width %r) (layer "F.Cu") (net 1))
      (segment (start 5 %r) (end 35 %r) (width %r) (layer "F.Cu") (net 2))
    )""" % (STACKUP, width_mm, y2, y2, width_mm)


class TestSummary(unittest.TestCase):
    def test_sample_board_summary(self):
        board = kicad_pcb.parse_board(SAMPLE)
        r = oracle.summary(board)
        self.assertEqual(r["status"], "ok")
        self.assertEqual(r["value"]["footprints"], 2)
        self.assertEqual(r["value"]["tracks"], 2)

    def test_summary_carries_presence_and_absence(self):
        board = kicad_pcb.parse_board(SAMPLE)
        r = oracle.summary(board)
        self.assertIn("design_rules", [a["fact"] for a in r["absent"]])
        self.assertTrue(r["present"]["stackup"]["present"])


class TestClearance(unittest.TestCase):
    def test_track_width_is_subtracted_from_centreline_distance(self):
        # centre distance 0.5 mm, both 0.2 mm wide -> edge gap 0.5 - 0.2 = 0.3 mm
        board = kicad_pcb.parse_board_text(two_parallel_tracks(0.5))
        r = oracle.clearance(board, "A", "B", threshold_mm=0.2)
        self.assertEqual(r["status"], "ok")
        self.assertAlmostEqual(r["value"]["min_gap_mm"], 0.3, places=4)
        self.assertEqual(r["value"]["violations"], 0)

    def test_violation_is_reported_with_both_references(self):
        # centre distance 0.3 mm -> edge gap 0.1 mm, below the 0.2 mm threshold
        board = kicad_pcb.parse_board_text(two_parallel_tracks(0.3))
        r = oracle.clearance(board, "A", "B", threshold_mm=0.2)
        self.assertEqual(r["value"]["violations"], 1)
        worst = r["value"]["worst"][0]
        self.assertAlmostEqual(worst["gap_mm"], 0.1, places=4)
        self.assertEqual({worst["net_a"], worst["net_b"]}, {"A", "B"})
        self.assertIn(worst["item_a"], ("track@F.Cu",))

    def test_same_net_pair_refused(self):
        board = kicad_pcb.parse_board(SAMPLE)
        r = oracle.clearance(board, "GND", "GND")
        self.assertEqual(r["status"], "error")
        self.assertEqual(r["reason_code"], "ARG_INVALID")

    def test_near_pair_found_across_spatial_buckets(self):
        # regression: two pads 0.7 mm apart used to land in non-adjacent buckets
        # and were never compared at all (min_gap came back null)
        board = kicad_pcb.parse_board(SAMPLE)
        r = oracle.clearance(board, "GND", "VCC", threshold_mm=0.2)
        self.assertAlmostEqual(r["value"]["min_gap_mm"], 0.7, places=3)

    def test_no_geometry_refused(self):
        board = kicad_pcb.parse_board(SAMPLE)
        r = oracle.clearance(board, "NOSUCH", "GND")
        self.assertEqual(r["status"], "error")
        self.assertEqual(r["reason_code"], "NO_GEOMETRY")


class TestFreeSpace(unittest.TestCase):
    def test_free_rects_are_inside_the_outline(self):
        board = kicad_pcb.parse_board(SAMPLE)
        r = oracle.free_space(board, "F.Cu", grid_mm=1.0, clearance_mm=0.2)
        self.assertEqual(r["status"], "ok")
        x0 = r["value"]["grid"]["x0"]
        step = r["value"]["grid"]["step_mm"]
        for rect in r["value"]["free_rects"]:
            self.assertGreaterEqual(rect["x_mm"], x0 - 1e-6)
            self.assertEqual(round((rect["x_mm"] - x0) / step),
                             round((rect["x_mm"] - x0) / step))
        self.assertGreater(r["value"]["free_ratio"], 0.0)
        self.assertLess(r["value"]["free_ratio"], 1.0)

    def test_grid_too_fine_refused(self):
        board = kicad_pcb.parse_board(SAMPLE)
        r = oracle.free_space(board, "F.Cu", grid_mm=0.001)
        self.assertEqual(r["status"], "error")
        self.assertEqual(r["reason_code"], "GRID_TOO_FINE")


class TestPourConnectivity(unittest.TestCase):
    def test_isolated_pad_is_reported(self):
        text = """(kicad_pcb (version 20221018) %s
          (net 0 "") (net 1 "GND")
          (gr_rect (start 0 0) (end 40 40) (layer "Edge.Cuts") (width 0.1) (fill none))
          (footprint "T:P" (layer "F.Cu") (at 10 10)
            (property "Reference" "R1" (at 0 0 0) (layer "F.SilkS"))
            (pad "1" smd rect (at 0 0) (size 1 1) (layers "F.Cu") (net 1 "GND"))
          )
          (footprint "T:P" (layer "F.Cu") (at 100 100)
            (property "Reference" "R2" (at 0 0 0) (layer "F.SilkS"))
            (pad "1" smd rect (at 0 0) (size 1 1) (layers "F.Cu") (net 1 "GND"))
          )
          (zone (net 1) (net_name "GND") (layer "B.Cu") (layername "B.Cu")
            (hatch edge 0.5) (connect_pads (clearance 0.2)) (min_thickness 0.25)
            (fill yes (mode solid))
            (polygon (pts (xy 0 0) (xy 40 0) (xy 40 40) (xy 0 40)))
            (filled_polygon (layer "B.Cu") (pts (xy 0 0) (xy 40 0) (xy 40 40) (xy 0 40)))
          )
        )""" % STACKUP
        board = kicad_pcb.parse_board_text(text)
        r = oracle.pour_connectivity(board)
        self.assertEqual(r["status"], "ok")
        self.assertEqual(r["value"]["connected_pads"], 1)
        self.assertEqual(r["value"]["isolated_pads"], 1)
        self.assertEqual(r["value"]["isolated"][0], "R2.1")


class TestRefusalsOnMissingFacts(unittest.TestCase):
    def test_cross_split_without_plane_refuses(self):
        text = """(kicad_pcb (version 20221018)
          (net 0 "") (net 1 "A")
          (gr_rect (start 0 0) (end 40 40) (layer "Edge.Cuts") (width 0.1) (fill none))
          (segment (start 5 10) (end 35 10) (width 0.2) (layer "F.Cu") (net 1))
        )"""
        board = kicad_pcb.parse_board_text(text)
        r = oracle.cross_split(board, layer="F.Cu")
        self.assertEqual(r["status"], "error")
        self.assertEqual(r["reason_code"], "FACT_ABSENT_IN_FORMAT")

    def test_loop_area_without_stackup_refuses(self):
        text = """(kicad_pcb (version 20221018)
          (net 0 "") (net 1 "A")
          (gr_rect (start 0 0) (end 40 40) (layer "Edge.Cuts") (width 0.1) (fill none))
          (segment (start 5 10) (end 35 10) (width 0.2) (layer "F.Cu") (net 1))
        )"""
        board = kicad_pcb.parse_board_text(text)
        r = oracle.loop_area(board, "A")
        self.assertEqual(r["status"], "error")
        self.assertEqual(r["reason_code"], "FACT_ABSENT_IN_FORMAT")

    def test_loop_area_with_stackup_answers(self):
        board = kicad_pcb.parse_board_text(two_parallel_tracks(0.5))
        r = oracle.loop_area(board, "A")
        self.assertEqual(r["status"], "ok")
        # 30 mm of routed length over a 0.2 mm dielectric -> 6.0 mm^2
        self.assertAlmostEqual(r["value"]["reference_height_mm"], 0.2, places=6)
        self.assertAlmostEqual(r["value"]["total_length_mm"], 30.0, places=6)
        self.assertAlmostEqual(r["value"]["loop_area_mm2"], 6.0, places=3)


class TestComponentsNets(unittest.TestCase):
    def test_components_listed_with_bbox(self):
        board = kicad_pcb.parse_board(SAMPLE)
        r = oracle.components(board)
        refs = [c["ref"] for c in r["value"]]
        self.assertEqual(refs, ["R1", "C1"])
        self.assertGreater(r["value"][0]["bbox"]["x_max"], r["value"][0]["bbox"]["x_min"])

    def test_net_track_length(self):
        board = kicad_pcb.parse_board(SAMPLE)
        r = oracle.nets(board)
        self.assertAlmostEqual(r["value"]["VCC"]["track_length_mm"], 19.0, places=3)


if __name__ == "__main__":
    unittest.main()
