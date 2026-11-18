# -*- coding: utf-8 -*-
"""Two segments leaving one point in the SAME direction is not a 0 degree corner.

The via-in-pad relocation on `rk3506_som` left 6 vertices where a new stitch lay
exactly on top of existing same-net copper. `angle_audit` called them
`acute_corner` with `corner_deg: 0.0` - an interior angle of zero degrees, which
cannot exist in copper: the two rays are the same ray. The number was right (one
piece of copper drawn twice) and the name sent the fixer at an angle that does
not exist.

Every test here comes with the reverse case, because a new bucket that swallows
real acid traps would be worse than the wrong name it replaced.
"""
import os
import sys
import unittest

sys.path.insert(0, os.path.join(
    os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "src"))
sys.path.insert(0, os.path.join(
    os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "tools"))

from pcb_agent_mcp import oracle, shape_gate  # noqa: E402
from pcb_agent_mcp.adapters import kicad_pcb  # noqa: E402

STACKUP = """(setup (stackup (layer "F.Cu" (type "copper") (thickness 0.035))
    (layer "dielectric 1" (type "core") (thickness 0.2) (epsilon_r 4.3))
    (layer "B.Cu" (type "copper") (thickness 0.035))))"""


def board(tracks, pads=""):
    """40x40 board, one net per pad so tracks can be labelled against copper."""
    if not pads:
        pads = """
  (footprint "T:P" (layer "F.Cu") (at 20 15)
    (fp_text reference "A1" (at 0 0) (layer "F.SilkS"))
    (pad "1" smd rect (at 0 0) (size 1 1) (layers "F.Cu") (net 1 "SIG")))
  (footprint "T:P" (layer "F.Cu") (at 25 15)
    (fp_text reference "A2" (at 0 0) (layer "F.SilkS"))
    (pad "1" smd rect (at 0 0) (size 1 1) (layers "F.Cu") (net 1 "SIG")))
"""
    return kicad_pcb.parse_board_text("""(kicad_pcb (version 20221018) %s
      (net 0 "") (net 1 "SIG") (net 2 "GND")
      (gr_rect (start 0 0) (end 40 40) (layer "Edge.Cuts") (width 0.1) (fill none))
%s%s
    )""" % (STACKUP, pads, tracks))


# The whole trace 20->25 and a second trace 20->23 on the same net and layer:
# the second is a prefix of the first, so 0.3 mm of copper is drawn twice.
OVERLAP = """
  (segment (start 20 15) (end 25 15) (width 0.25) (layer "F.Cu") (net 1))
  (segment (start 20 15) (end 23 15) (width 0.25) (layer "F.Cu") (net 1))
"""
# A real acid trap: two rays leaving one point 45 degrees apart.
ACUTE = """
  (segment (start 20 15) (end 25 15) (width 0.25) (layer "F.Cu") (net 1))
  (segment (start 20 15) (end 23 18) (width 0.25) (layer "F.Cu") (net 1))
"""
# Same heading, different nets: the two pieces do not belong to one another.
FOREIGN = """
  (segment (start 20 15) (end 25 15) (width 0.25) (layer "F.Cu") (net 1))
  (segment (start 20 15) (end 23 15) (width 0.25) (layer "F.Cu") (net 2))
"""
# Folded back on itself: 180 degrees is a straight line through the vertex.
FOLD = """
  (segment (start 20 15) (end 25 15) (width 0.25) (layer "F.Cu") (net 1))
  (segment (start 20 15) (end 15 15) (width 0.25) (layer "F.Cu") (net 1))
"""
# Both segments end on the same point: that is the `coincident` bucket already.
TWIN = """
  (segment (start 20 15) (end 25 15) (width 0.25) (layer "F.Cu") (net 1))
  (segment (start 20 15) (end 25 15) (width 0.10) (layer "F.Cu") (net 1))
"""


class TestOverlapBucket(unittest.TestCase):
    def test_two_traces_leaving_one_point_the_same_way_are_overlap(self):
        out = oracle.angle_audit(board(OVERLAP))
        c = out["value"]["corners"]["F.Cu"]
        self.assertEqual(c["overlap"], 1, c)
        self.assertEqual(c["corner_acute"], 0, c)
        self.assertEqual(c["overlap_list"][0]["net"], "SIG")
        self.assertAlmostEqual(c["overlap_list"][0]["corner_deg"], 0.0, places=3)
        self.assertIn("overlapping_copper@F.Cu", out["value"]["problems"])
        self.assertNotIn("acute_corner@F.Cu", out["value"]["problems"])

    def test_a_real_45_degree_acid_trap_is_still_acute_and_not_overlap(self):
        """Reverse control: the new bucket must not swallow a genuine corner."""
        out = oracle.angle_audit(board(ACUTE))
        c = out["value"]["corners"]["F.Cu"]
        self.assertEqual(c["corner_acute"], 1, c)
        self.assertEqual(c["overlap"], 0, c)
        self.assertAlmostEqual(c["acute_list"][0]["corner_deg"], 45.0, places=1)
        self.assertIn("acute_corner@F.Cu", out["value"]["problems"])

    def test_the_same_heading_on_two_nets_is_not_overlap(self):
        out = oracle.angle_audit(board(FOREIGN))
        c = out["value"]["corners"]["F.Cu"]
        self.assertEqual(c["overlap"], 0, c)
        self.assertEqual(c["corner_acute"], 0, c)

    def test_a_180_degree_fold_is_collinear_not_overlap(self):
        out = oracle.angle_audit(board(FOLD))
        c = out["value"]["corners"]["F.Cu"]
        self.assertEqual(c["collinear"], 1, c)
        self.assertEqual(c["overlap"], 0, c)

    def test_the_same_two_endpoints_stay_in_the_coincident_bucket(self):
        out = oracle.angle_audit(board(TWIN))
        c = out["value"]["corners"]["F.Cu"]
        self.assertEqual(c["coincident"], 2, c)
        self.assertEqual(c["overlap"], 0, c)


class TestShapeGateCountsOverlap(unittest.TestCase):
    def test_overlap_blocks_duplicate_copper(self):
        g = shape_gate.gate(board(OVERLAP))
        self.assertEqual(g["status"], "ok", g)
        v = g["value"]
        rule = [r for r in v["rules"] if r["id"] == "duplicate_copper"][0]
        self.assertGreaterEqual(rule["measured"], 1, rule)
        self.assertTrue(rule["blocking"], rule)
        self.assertTrue(any(i.get("kind") == "overlap" for i in rule["items"]),
                        rule["items"])
        self.assertEqual(v["verdict"], "shape_fail")
        # the overlap must NOT also be reported as an acid trap
        acute = [r for r in v["rules"] if r["id"] == "acute_corner"][0]
        self.assertEqual(acute["measured"], 0, acute)

    def test_a_clean_board_is_untouched_by_the_new_bucket(self):
        g = shape_gate.gate(board("""
  (segment (start 20 15) (end 25 15) (width 0.25) (layer "F.Cu") (net 1))
"""))
        v = g["value"]
        rule = [r for r in v["rules"] if r["id"] == "duplicate_copper"][0]
        self.assertEqual(rule["measured"], 0, rule)
        self.assertFalse(rule["blocking"], rule)


class _FakeModel(object):
    """Just enough Model for the coverage question."""

    def __init__(self, tracks):
        self.tracks = tracks
        self.pads = []
        self.zones = []

    def near(self, x, y):
        idx = []
        for i, t in enumerate(self.tracks):
            if min(t["x1"], t["x2"]) - 2 <= x <= max(t["x1"], t["x2"]) + 2 and \
                    min(t["y1"], t["y2"]) - 2 <= y <= max(t["y1"], t["y2"]) + 2:
                idx.append(i)
        return {"pad": [], "track": idx, "via": [], "edge": []}


def _track(x1, y1, x2, y2, w=0.25, net="SIG", layer="F.Cu"):
    return {"net": net, "layer": layer, "x1": x1, "y1": y1,
            "x2": x2, "y2": y2, "w": w}


class TestCoverageHelper(unittest.TestCase):
    """The relocation planner asks this before it draws a stitch."""

    def setUp(self):
        import off_pad_dogbone
        self.opd = off_pad_dogbone

    def test_a_stitch_inside_an_existing_trace_is_covered(self):
        m = _FakeModel([_track(20.0, 15.0, 25.0, 15.0, w=0.5)])
        self.assertTrue(self.opd._covered_by_own(
            m, "SIG", "F.Cu", 21.0, 15.0, 23.0, 15.0, 0.25))

    def test_a_stitch_that_leaves_the_existing_trace_is_not_covered(self):
        """Reverse control: the same query one half-width to the side."""
        m = _FakeModel([_track(20.0, 15.0, 25.0, 15.0, w=0.5)])
        self.assertFalse(self.opd._covered_by_own(
            m, "SIG", "F.Cu", 21.0, 15.4, 23.0, 15.4, 0.25))

    def test_a_partially_covered_stitch_is_not_covered(self):
        m = _FakeModel([_track(20.0, 15.0, 22.0, 15.0, w=0.5)])
        self.assertFalse(self.opd._covered_by_own(
            m, "SIG", "F.Cu", 20.0, 15.0, 24.0, 15.0, 0.25))

    def test_copper_of_another_net_does_not_cover(self):
        m = _FakeModel([_track(20.0, 15.0, 25.0, 15.0, w=0.5, net="GND")])
        self.assertFalse(self.opd._covered_by_own(
            m, "SIG", "F.Cu", 21.0, 15.0, 23.0, 15.0, 0.25))

    def test_off_grid_tells_45_from_the_1_4_degree_case(self):
        self.assertFalse(self.opd._off_grid(0.0, 0.0, 10.0, 10.0))
        self.assertFalse(self.opd._off_grid(0.0, 0.0, 10.0, 0.0))
        # /FB_U7 produced a 1.4 degree segment during a relocation
        self.assertTrue(self.opd._off_grid(12.84, 3.6, 12.112, 3.582))
        # /FSPI_D1 produced a 96.3 degree stub, 6.3 degrees off the vertical
        self.assertTrue(self.opd._off_grid(38.027, 36.665, 38.048, 36.475))


if __name__ == "__main__":
    unittest.main()
