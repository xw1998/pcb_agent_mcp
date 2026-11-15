"""`dead_end`: copper that joins nothing, and what must NOT be called that.

Why this rule exists: on the SOM board 24 vias were drilled, plated and paid for
and touched copper on F.Cu only -- KiCad's own DRC called them `via_dangling`,
but this gate had no opinion about them, so a board could leave the house with
24 dead holes and still read `shape_pass`.

Two shapes, one defect: a track end that touches nothing (a tail drawn off a
real node and left hanging) and a via with copper on one side only.

Eight cases. The first five are the trap this rule fell into on its first draft,
which reported 296 dead ends on a board whose DRC was clean:

1. Two 0.1 mm tracks whose ends sit 0.0139 mm apart ARE joined. Judging "touching"
   by coordinate equality rather than half-width overlap calls them two dead
   ends. This is the control that pins the physical criterion.
2. A track end inside another track's body (a T) is joined, not dangling.
3. A track end on a same-net pad is joined; on nothing it is the defect.
4. A via held on F.Cu and B.Cu is fine; held on F.Cu alone is dead -- and the
   via must not count as its own copper, or it reads as held on all six layers
   and the rule can never fire (the second bug in the first draft).
5. Two layers meeting at one point with NO via are NOT joined: the dead end is
   per layer, exactly like `redundant_loop`.
6. A rail via held only by its zone fill is not dead. A stitched rail via often
   has no track of its own, and calling that a defect would fire on every pour.
7. An SMD pad only holds its own side of the board.
8. The rule is blocking by default and sits at priority 5, ahead of the soft
   cosmetic rules.
"""
import os
import sys
import unittest

sys.path.insert(0, os.path.join(os.path.dirname(os.path.dirname(
    os.path.abspath(__file__))), "src"))

from pcb_agent_mcp import shape_gate

D = "0.10"


class _Board(object):
    def __init__(self, tracks, vias=None, pads=None, zones=None):
        self.tracks = tracks
        self.vias = vias or []
        self._pads = pads or []
        self.zones = zones or []

    def pads(self):
        return self._pads


def _seg(net, layer, start, end, width=D):
    return {"net": net, "layer": layer,
            "start": (float(start[0]), float(start[1])),
            "end": (float(end[0]), float(end[1])), "width": float(width)}


def _pad(net, x, y, layers=("*",), hole=None, w=0.6, h=0.6):
    return {"net": net, "x": float(x), "y": float(y), "layers": list(layers),
            "hole_mm": hole, "w": w, "h": h, "shape": "circle",
            "rotation": 0.0, "offset": (0.0, 0.0)}


def _via(net, x, y, diameter=0.2, layers=None):
    return {"net": net, "x": float(x), "y": float(y),
            "diameter": float(diameter), "drill": 0.1,
            "layers": layers or ["F.Cu", "In1.Cu", "In2.Cu", "In3.Cu", "In4.Cu", "B.Cu"]}


def _count(board):
    items, ends, vias = shape_gate._dead_end(board)
    return len(items), ends, vias


class TestDeadEnd(unittest.TestCase):
    def test_two_ends_that_overlap_in_copper_are_joined(self):
        # 0.0139 mm gap between two 0.1 mm tracks: the copper overlaps by
        # 0.086 mm, so the junction is ONE conductor. The two outer ends are
        # still free, so exactly two ends are reported -- and the junction is
        # not one of them. A coordinate test reports four.
        tracks = [_seg("N1", "F.Cu", (0, 0), (10, 0)),
                  _seg("N1", "F.Cu", (10.0139, 0), (20, 0))]
        items, ends, vias = shape_gate._dead_end(_Board(tracks))
        self.assertEqual((ends, vias), (2, 0))
        self.assertEqual(sorted((i["x"], i["y"]) for i in items),
                         [(0.0, 0.0), (20.0, 0.0)])

    def test_a_hanging_end_is_counted(self):
        tracks = [_seg("N1", "F.Cu", (0, 0), (10, 0))]
        n, ends, vias = _count(_Board(tracks))
        self.assertEqual((ends, vias), (2, 0))

    def test_an_end_on_a_pad_is_joined(self):
        tracks = [_seg("N1", "F.Cu", (0, 0), (10, 0))]
        pads = [_pad("N1", 0, 0), _pad("N1", 10, 0)]
        n, ends, vias = _count(_Board(tracks, pads=pads))
        self.assertEqual((ends, vias), (0, 0))

    def test_an_end_in_another_tracks_body_is_joined(self):
        # A T: the branch lands in the middle of the trunk. Endpoint-only
        # matching reads this as a dead end.
        tracks = [_seg("N1", "F.Cu", (0, 0), (20, 0)),
                  _seg("N1", "F.Cu", (10, 0), (10, 10))]
        n, ends, vias = _count(_Board(tracks))
        # the trunk's two ends are free, the branch's far end is free: 3
        self.assertEqual(ends, 3)

    def test_a_via_held_on_one_layer_is_dead(self):
        tracks = [_seg("N1", "F.Cu", (0, 0), (10, 0))]
        vias = [_via("N1", 10, 0)]
        n, ends, vias_n = _count(_Board(tracks, vias=vias))
        self.assertEqual(vias_n, 1)
        # and the via holds the track end, so no track end is counted there
        self.assertEqual(ends, 1)

    def test_a_via_held_on_both_sides_is_fine(self):
        tracks = [_seg("N1", "F.Cu", (0, 0), (10, 0)),
                  _seg("N1", "B.Cu", (10, 0), (20, 0))]
        vias = [_via("N1", 10, 0)]
        n, ends, vias_n = _count(_Board(tracks, vias=vias))
        self.assertEqual((ends, vias_n), (2, 0))  # (0,0) and (20,0) hang

    def test_a_via_must_not_count_as_its_own_copper(self):
        # The first draft did, so every via read as held on all six layers.
        vias = [_via("N1", 0, 0), _via("N1", 5, 0)]
        n, ends, vias_n = _count(_Board([], vias=vias))
        self.assertEqual(vias_n, 2)

    def test_two_layers_meeting_without_a_via_are_not_joined(self):
        # Per-layer, like redundant_loop: the same coordinate on F.Cu and B.Cu
        # is two pieces of copper unless a via is there.
        tracks = [_seg("N1", "F.Cu", (0, 0), (10, 0)),
                  _seg("N1", "B.Cu", (10, 0), (20, 0))]
        n, ends, vias = _count(_Board(tracks))
        self.assertEqual(ends, 4)

    def test_a_via_held_only_by_its_zone_is_not_dead(self):
        zone = {"net": "GND", "layers": ["F.Cu", "B.Cu"],
                "filled_polygons": [[(0.0, 0.0), (8.0, 0.0), (8.0, 8.0), (0.0, 8.0)]]}
        vias = [_via("GND", 4, 4)]
        n, ends, vias_n = _count(_Board([], vias=vias, zones=[zone]))
        self.assertEqual(vias_n, 0)

    def test_an_smd_pad_holds_only_its_own_side(self):
        tracks = [_seg("N1", "F.Cu", (0, 0), (10, 0)),
                  _seg("N1", "B.Cu", (10, 0), (20, 0))]
        pads = [_pad("N1", 0, 0, layers=("F.Cu",)),
                _pad("N1", 10, 0, layers=("F.Cu",))]
        n, ends, vias = _count(_Board(tracks, pads=pads))
        # F.Cu side is held by its pad; the B.Cu run holds nothing at either end
        self.assertEqual(ends, 2)

    def test_rule_rank_and_blocking(self):
        order = [r["id"] for r in shape_gate.RULES]
        self.assertIn("dead_end", order)
        idx = order.index("dead_end")
        self.assertEqual(order[idx - 1], "redundant_loop")
        self.assertEqual(order[idx + 1], "square_corner")
        self.assertTrue(shape_gate.RULES[idx]["blocking"])


if __name__ == "__main__":
    unittest.main()
