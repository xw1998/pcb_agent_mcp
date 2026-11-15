"""`redundant_loop`: a net that closes a ring against itself, and what is NOT one.

Why this rule exists: on the SOM board `/FB_U7` had two paths between the same
two pins - a 10.48 mm In3 arm going the long way round a U and a 4.9 mm B.Cu
diagonal - and neither `duplicate_copper` (different endpoints) nor
`detour_ratio` (the ring was inside the cap) saw it. Sixteen of the seventeen
millimetres were the second copy.

Four cases, and every one of the last three is a negative control that has to be
able to fail:

1. A square ring closes: 1 ring.
2. An L run (a tree) does not: 0 rings. If the counter were `edges - nodes`
   without the component term it would report -1 here, so this pins the formula.
3. Two different nets meeting at the same point on one layer do not make a ring:
   the graph is per net. If the net key were dropped this would read 1.
4. The same net on two layers meeting at the same two points is NOT a ring
   without a via (they are two unconnected pieces of copper), and IS a ring once
   the vias are there. If layers were fused unconditionally, the no-via half
   would read 1.

Case 4 is also the one that caught the real defect in this function: identity
merging has to shrink the node count, not just relabel a component, or each
joined via silently cancels a ring (`/FB_U7` read -1 ring while the copper was
plainly a ring).
"""
import os
import sys
import unittest

sys.path.insert(0, os.path.join(os.path.dirname(os.path.dirname(
    os.path.abspath(__file__))), "src"))

from pcb_agent_mcp import shape_gate

D = "0.10"

class _Board(object):
    def __init__(self, tracks, vias=None, pads=None):
        self.tracks = tracks
        self.vias = vias or []
        self._pads = pads or []

    def pads(self):
        return self._pads

def _seg(net, layer, start, end):
    return {"net": net, "layer": layer,
            "start": (float(start[0]), float(start[1])),
            "end": (float(end[0]), float(end[1])), "width": float(D)}

def _ring(net="N1", layer="F.Cu"):
    return [_seg(net, layer, (0, 0), (10, 0)),
            _seg(net, layer, (10, 0), (10, 10)),
            _seg(net, layer, (10, 10), (0, 10)),
            _seg(net, layer, (0, 10), (0, 0))]

def _rows(board):
    return {r["net"]: r for r in shape_gate._redundant_loop(board)}

class TestRedundantLoop(unittest.TestCase):
    def test_square_ring_closes(self):
        rows = _rows(_Board(_ring()))
        self.assertEqual(len(rows), 1)
        row = rows["N1"]
        self.assertEqual(row["cycles"], 1)
        self.assertEqual(row["components"], 1)
        self.assertAlmostEqual(row["removable_length_mm"], 10.0, places=6)
        # the long way round is what to drop, so the listed piece is a whole side
        self.assertAlmostEqual(row["removable"][0]["length_mm"], 10.0, places=6)

    def test_tree_does_not_close(self):
        # Negative control for the count itself: an L is a tree, so
        # edges - nodes + components is 0 and must not go negative.
        rows = _rows(_Board([_seg("N1", "F.Cu", (0, 0), (10, 0)),
                             _seg("N1", "F.Cu", (10, 0), (10, 10))]))
        self.assertEqual(rows, {})

    def test_two_nets_at_one_point_are_not_a_ring(self):
        # Negative control for the net key. Two nets sharing a coordinate are
        # two pieces of copper, not a loop.
        rows = _rows(_Board([_seg("N1", "F.Cu", (0, 0), (10, 0)),
                             _seg("N1", "F.Cu", (10, 0), (10, 10)),
                             _seg("N2", "F.Cu", (10, 0), (0, 10)),
                             _seg("N2", "F.Cu", (0, 10), (0, 0))]))
        self.assertEqual(rows, {})

    def test_layers_join_only_where_a_via_joins_them(self):
        # Two parallel paths between (0,0) and (10,0), one per layer. Without
        # vias this is two unrelated pieces of copper and there is no ring; with
        # a via at each end it is one ring. Both halves matter: the second one is
        # the positive case, the first is the control that fails if layers are
        # fused unconditionally.
        tracks = [_seg("N1", "F.Cu", (0, 0), (10, 0)),
                  _seg("N1", "B.Cu", (0, 0), (10, 0))]
        self.assertEqual(_rows(_Board(tracks)), {})
        vias = [{"net": "N1", "x": 0.0, "y": 0.0},
                {"net": "N1", "x": 10.0, "y": 0.0}]
        rows = _rows(_Board(tracks, vias=vias))
        self.assertEqual(len(rows), 1)
        self.assertEqual(rows["N1"]["cycles"], 1)
        self.assertEqual(rows["N1"]["components"], 1)

    def test_vertex_on_a_segment_interior_splits_it(self):
        # The SOM board's ring met the tie in the MIDDLE of a segment, not at
        # an endpoint. If only endpoints counted, this would read 0.
        tracks = [_seg("N1", "F.Cu", (0, 0), (20, 0)),
                  _seg("N1", "F.Cu", (10, 0), (10, -10)),
                  _seg("N1", "F.Cu", (10, -10), (0, -10)),
                  _seg("N1", "F.Cu", (0, -10), (0, 0))]
        rows = _rows(_Board(tracks))
        self.assertEqual(len(rows), 1)
        self.assertEqual(rows["N1"]["cycles"], 1)

class TestRuleTable(unittest.TestCase):
    def test_rank_and_blocking(self):
        order = [r["id"] for r in shape_gate.RULES]
        self.assertEqual(order[3], "redundant_loop")
        self.assertEqual(order[4], "dead_end")
        self.assertEqual(order[5], "square_corner")
        self.assertTrue(shape_gate.RULES[3]["blocking"])
        self.assertTrue(shape_gate.RULES[4]["blocking"])

if __name__ == "__main__":
    unittest.main()
