"""`hole_in_pad`: a drill that removes pad copper, and what must NOT count.

Why this file exists: the rule's first implementation tested whether the via's
**centre** fell inside the pad's `w` x `h` rectangle. On the SOM board that read
8 holes; the board has 62. Both numbers are wrong in the same direction, and the
reason is geometric: a 1.40 x 0.56 oval pad is a stadium, so a hole sitting at
the end of a track inside the stadium has a centre outside the rectangle while
its drill is still cutting pad copper -- a defect that reads as clean. Counting
only centres that are inside the rectangle is the "plausible but not the same
question" trap: the question is *does the drill remove pad copper*, and the
answer is a distance from the hole's centre to the pad's real outline.

Five cases, built to fail differently:

1. A hole whose centre is outside the rectangle but whose drill does not reach
   the stadium's copper is NOT a via in a pad. The old test called it one.
2. A hole whose centre is outside the rectangle and whose drill crosses the
   outline IS a via in a pad. The old test called it clean -- the 54 misses.
3. A hole fully inside a round pad IS a via in a pad.
4. A hole just clear of a round pad's edge is not.
5. Net identity decides: a foreign-net hole inside the pad is a clearance
   question for the DRC, not this rule.

A sixth case pins the other half: a THT pad's own hole is not a via in a pad
unless a *via* is drilled inside it, so a pad that carries `hole_mm` and has no
via on it must not be reported.
"""
import os
import sys
import unittest

sys.path.insert(0, os.path.join(os.path.dirname(os.path.dirname(
    os.path.abspath(__file__))), "src"))

from pcb_agent_mcp import place_gate

EDGE = [(0.0, 0.0), (40.0, 0.0), (40.0, 40.0), (0.0, 40.0), (0.0, 0.0)]


class _Board(object):
    def __init__(self, pads, vias, tracks=None):
        self.outline = [EDGE]
        self.outline_open = []
        self.footprints = [{"ref": "U1", "value": "x", "lib_id": "x:y",
                            "x": 20.0, "y": 20.0,
                            "bbox": {"x_min": 19.0, "y_min": 19.0, "x_max": 21.0, "y_max": 21.0},
                            "pads": pads}]
        self.vias = vias
        self.tracks = tracks or []
        self.graphics = []
        self.zones = []

    def pads(self):
        return self.footprints[0]["pads"]


def _pad(shape, w, h, x, y, net="N1", rot=0.0, number="1", hole=None):
    return {"ref": "U1", "number": str(number), "net": net,
            "x": float(x), "y": float(y), "w": float(w), "h": float(h),
            "shape": shape, "rotation": rot, "offset": (0.0, 0.0),
            "layers": ["F.Cu", "F.Mask", "F.Paste"], "hole_mm": hole,
            "smd": True}


def _via(x, y, net="N1", drill=0.2, dia=0.4):
    return {"net": net, "x": float(x), "y": float(y), "drill": float(drill),
            "diameter": float(dia),
            "from": "F.Cu", "to": "B.Cu",
            "layers": ["F.Cu", "In1.Cu", "In2.Cu", "In3.Cu", "In4.Cu", "B.Cu"]}


def _hits(board):
    res = place_gate.gate(board)
    out = res["value"] if isinstance(res, dict) else res.value
    return [r for r in out["rules"] if r["id"] == "hole_in_pad"][0]


class TestHoleInPad(unittest.TestCase):
    def test_centre_outside_rect_and_drill_clear_is_not_a_via_in_pad(self):
        # stadium 1.40 x 0.56: half-height 0.28. Hole at y=0.55 is outside the
        # rectangle? No -- the rectangle test allows |y| <= 0.70, so the old
        # rule fired. The drill (r=0.10) stops 0.27 mm short of the copper.
        b = _Board([_pad("oval", 1.40, 0.56, 20.0, 20.0)], [_via(20.0, 20.55)])
        self.assertEqual(_hits(b)["measured"], 0)

    def test_centre_outside_rect_but_drill_cuts_the_outline_is_a_via_in_pad(self):
        # y=0.36: the centre is 0.08 mm past the stadium's edge and the drill
        # radius is 0.10, so the wall ends 0.02 mm inside the copper. Little
        # overlap, still a pierced pad.
        b = _Board([_pad("oval", 1.40, 0.56, 20.0, 20.0)], [_via(20.0, 20.36)])
        rule = _hits(b)
        self.assertEqual(rule["measured"], 1)
        self.assertAlmostEqual(rule["items"][0]["hole_gap_mm"], 0.08, places=2)

    def test_a_hole_inside_a_round_pad_is_a_via_in_pad(self):
        # circle d=1.0, hole at 0.55 from centre: 0.05 mm outside the outline,
        # drill radius 0.10 -> 0.05 mm of pad copper is drilled away.
        b = _Board([_pad("circle", 1.0, 1.0, 20.0, 20.0)], [_via(20.55, 20.0)])
        rule = _hits(b)
        self.assertEqual(rule["measured"], 1)
        self.assertAlmostEqual(rule["items"][0]["hole_gap_mm"], 0.05, places=2)

    def test_a_hole_just_clear_of_a_round_pad_is_not(self):
        b = _Board([_pad("circle", 1.0, 1.0, 20.0, 20.0)], [_via(20.66, 20.0)])
        self.assertEqual(_hits(b)["measured"], 0)

    def test_a_foreign_net_hole_is_the_drcs_job(self):
        b = _Board([_pad("rect", 1.0, 1.0, 20.0, 20.0, net="N1")],
                   [_via(20.0, 20.0, net="N2")])
        self.assertEqual(_hits(b)["measured"], 0)

    def test_a_tht_pad_without_a_via_is_not_reported(self):
        # the pad's own hole is not a via; only a via drilled inside it counts
        b = _Board([_pad("circle", 1.6, 1.6, 20.0, 20.0, hole=0.8)], [])
        self.assertEqual(_hits(b)["measured"], 0)

    def test_the_rule_is_blocking_and_first(self):
        self.assertEqual(place_gate.RULES[0]["id"], "hole_in_pad")
        self.assertTrue(place_gate.RULES[0]["blocking"])


if __name__ == "__main__":
    unittest.main()
