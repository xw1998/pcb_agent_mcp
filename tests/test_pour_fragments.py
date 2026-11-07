"""board_pour_fragments: a pour is one conductor only if it is one piece.

Three things are pinned, each with a control that has to be able to fail:

1. N separate filled_polygon rings are N fragments, and the areas are the real
   shoelace areas. Control: the same rings merged into one ring must report 1,
   so a "3" cannot come from counting vertices.
2. The reported gap is the closest vertex pair, not the centroid distance.
   Control: moving one ring further away must raise the gap and nothing else.
3. A zone with no filled_polygon is `pour_not_filled`, never "0 fragments":
   "the engine has not poured this" and "the pour is whole" are different facts
   and the tool must not merge them.
"""
import os
import sys
import unittest

sys.path.insert(0, os.path.join(os.path.dirname(os.path.dirname(
    os.path.abspath(__file__))), "src"))

from pcb_agent_mcp import oracle as O


def _rect(x0, y0, x1, y1):
    return [(x0, y0), (x1, y0), (x1, y1), (x0, y1)]


class _Board(object):
    def __init__(self, zones):
        self.zones = zones
        self.stackup = []
        self.footprints = []


class TestPourFragments(unittest.TestCase):
    def test_three_rings_are_three_fragments(self):
        z = {"net": "+3V3", "layers": ["F.Cu"],
             "filled_polygons": [_rect(0, 0, 2, 1), _rect(5, 0, 8, 1),
                                 _rect(12, 0, 13, 0.5)]}
        v = O.pour_fragments(_Board([z]))["value"]
        self.assertEqual(v["fragment_total"], 3)
        row = v["zones"][0]
        self.assertEqual(row["fragments"], 3)
        areas = [f["area_mm2"] for f in row["detail"]]
        # detail is sorted largest-first, so 3.0 leads
        self.assertAlmostEqual(areas[0], 3.0, places=6)
        self.assertAlmostEqual(areas[1], 2.0, places=6)
        self.assertAlmostEqual(areas[2], 0.5, places=6)
        # largest first, and the share is that piece over the total
        self.assertAlmostEqual(row["largest_share"], 3.0 / 5.5, places=4)
        # gap is the closest vertex pair: (2,0)..(5,0) = 3 mm
        self.assertAlmostEqual(row["nearest_pairs"][0]["gap_mm"], 3.0, places=6)

    def test_one_ring_is_one_fragment(self):
        # Control for the test above: if fragments were counted from vertices or
        # bounding boxes, a single 8-vertex ring could never come back as 1.
        z = {"net": "GND", "layers": ["B.Cu"], "filled_polygons": [_rect(0, 0, 4, 4)]}
        v = O.pour_fragments(_Board([z]))["value"]
        self.assertEqual(v["fragment_total"], 1)
        self.assertEqual(v["zones"][0]["fragments"], 1)
        self.assertEqual(v["zones"][0]["nearest_pairs"], [])

    def test_gap_follows_the_geometry_not_the_centroid(self):
        near = {"net": "N", "layers": ["F.Cu"],
                "filled_polygons": [_rect(0, 0, 1, 1), _rect(2, 0, 3, 1)]}
        far = {"net": "N", "layers": ["F.Cu"],
               "filled_polygons": [_rect(0, 0, 1, 1), _rect(9, 0, 10, 1)]}
        gn = O.pour_fragments(_Board([near]))["value"]["zones"][0]["nearest_pairs"][0]
        gf = O.pour_fragments(_Board([far]))["value"]["zones"][0]["nearest_pairs"][0]
        self.assertAlmostEqual(gn["gap_mm"], 1.0, places=6)
        self.assertAlmostEqual(gf["gap_mm"], 8.0, places=6)

    def test_unfilled_zone_is_not_zero_fragments(self):
        z = {"net": "+5V", "layers": ["F.Cu"], "filled_polygons": []}
        row = O.pour_fragments(_Board([z]))["value"]["zones"][0]
        self.assertEqual(row["status"], "pour_not_filled")
        self.assertIsNone(row["fragments"],
                          "an unpoured zone has an UNKNOWN fragment count - reporting "
                          "0 would read as 'the pour is whole'")

    def test_net_filter_selects_zones(self):
        b = _Board([{"net": "GND", "layers": ["B.Cu"],
                     "filled_polygons": [_rect(0, 0, 2, 2)]},
                    {"net": "+3V3", "layers": ["F.Cu"],
                     "filled_polygons": [_rect(0, 0, 1, 1), _rect(4, 0, 5, 1)]}])
        v = O.pour_fragments(b, net="+3V3")["value"]
        self.assertEqual(len(v["zones"]), 1)
        self.assertEqual(v["zones"][0]["net"], "+3V3")
        self.assertEqual(v["fragment_total"], 2)


if __name__ == "__main__":
    unittest.main()
