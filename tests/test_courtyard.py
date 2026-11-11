"""Does a part collide with its neighbour? Answered from the outline the part
DRAWS, not from a box around it.

The audit used to cap this rule at `undetermined` because the adapter kept only a
bbox. On a real manufactured board (KiCad's `pic_programmer`, 2-layer) that bbox
reported P3 against C7 with a 0.295 mm x 6.6 mm "overlap"; the drawn outlines
clear each other by 2.05 mm. A rule that invents a collision on a finished board
is worse than one that says nothing, so the test that matters is the negative
one: two courtyards with a gap must measure that gap.

Two approximations stay honest: a courtyard drawn as an open polyline cannot carry
a verdict (it is not a closed area), and a part that draws no courtyard at all is
not put into a pad-sized box to be judged.
"""
import os
import shutil
import sys
import tempfile
import unittest

sys.path.insert(0, os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "src"))

from pcb_agent_mcp.adapters import kicad_pcb as K  # noqa: E402
from pcb_agent_mcp.ruleset import measure as M  # noqa: E402

_HEAD = """(kicad_pcb (version 20240108) (generator pcbnew)
  (general (thickness 1.6))
  (paper "A4")
  (layers (0 "F.Cu" signal) (31 "B.Cu" signal) (37 "F.SilkS" user)
          (44 "F.CrtYd" user) (45 "B.CrtYd" user))
  (net 0 "")
"""

def _fp(ref, x, y, graphics, pad=True):
    body = ['  (footprint "lib" (layer "F.Cu") (at %s %s)' % (x, y),
            '    (property "Reference" "%s")' % ref]
    body.extend(graphics)
    if pad:
        body.append('    (pad "1" smd rect (at 0 0) (size 1 1) '
                    '(layers "F.Cu" "F.Mask"))')
    body.append("  )")
    return "\n".join(body)

def _rect(sx, sy, ex, ey, w=0.05):
    return ('    (fp_rect (start %s %s) (end %s %s) (stroke (width %s)) '
            '(layer "F.CrtYd"))' % (sx, sy, ex, ey, w))

def _lines(pts, w=0.05, close=True):
    out = []
    seq = list(pts) + [pts[0]] if close else list(pts)
    for (ax, ay), (bx, by) in zip(seq, seq[1:]):
        out.append('    (fp_line (start %s %s) (end %s %s) '
                   '(stroke (width %s)) (layer "F.CrtYd"))' % (ax, ay, bx, by, w))
    return "\n".join(out)

def _circle(cx, cy, r, w=0.05):
    return ('    (fp_circle (center %s %s) (end %s %s) (stroke (width %s)) '
            '(layer "F.CrtYd"))' % (cx, cy, cx + r, cy, w))

class _Board(unittest.TestCase):
    def setUp(self):
        self.dir = tempfile.mkdtemp(prefix="pcbagent-cy-")

    def tearDown(self):
        shutil.rmtree(self.dir, ignore_errors=True)

    def parse(self, *footprints):
        path = os.path.join(self.dir, "t.kicad_pcb")
        with open(path, "w", encoding="utf-8", newline="\n") as fh:
            fh.write(_HEAD + "\n".join(footprints) + "\n)\n")
        return K.parse_board(path)

    def overlap(self, *footprints):
        return M.measure(self.parse(*footprints))["courtyard_overlaps"]

class TestTheOutlineIsRead(unittest.TestCase):
    def test_a_circle_courtyard_keeps_its_radius(self):
        """Read as centre/rim, `_graphic_points` returns the bbox corners and the
        radius comes out sqrt(2) too big about a corner - a mounting hole then
        collides with everything within 2.8 radii. This pins the radius."""
        d = tempfile.mkdtemp(prefix="pcbagent-cy-")
        try:
            path = os.path.join(d, "t.kicad_pcb")
            with open(path, "w", encoding="utf-8", newline="\n") as fh:
                fh.write(_HEAD + _fp("H1", 10, 10, [_circle(0, 0, 1.0)]) + "\n)\n")
            b = K.parse_board(path)
            ring = b.footprints[0]["courtyard"]["contours"][0]
            xs = [p[0] for p in ring]
            ys = [p[1] for p in ring]
            # centreline diameter 2.0, not 2.05: the pen width is not the area
            self.assertAlmostEqual(max(xs) - min(xs), 2.0, places=2)
            self.assertAlmostEqual(max(ys) - min(ys), 2.0, places=2)
            self.assertAlmostEqual((max(xs) + min(xs)) / 2.0, 10.0, places=2)
        finally:
            shutil.rmtree(d, ignore_errors=True)

class TestTheVerdict(_Board):
    def test_two_courtyards_with_a_gap_are_clean_and_the_gap_is_named(self):
        b = self.overlap(_fp("U1", 10, 10, [_rect(-2, -1, 2, 1)]),
                         _fp("U2", 15, 10, [_rect(-2, -1, 2, 1)]))
        self.assertEqual(b["value"], 0.0)
        self.assertNotIn("supports", b)
        self.assertIn("clears by", b["where"])

    def test_two_courtyards_that_meet_flush_touch_and_do_not_collide(self):
        """Exactly flush is not a collision.

        royalblue54L_feather places 27 pairs with a courtyard edge shared exactly
        (R5's bottom edge IS R7's top edge) and stickhub 64 - a library that
        draws the courtyard as the body outline plus a flush placement. KiCad's
        courtyard rule with a 0.0 mm clearance lets those through, and a rule
        that did not would refuse two manufactured boards.
        """
        # U1 spans x 8..12 and U2 12..16: the right edge of one IS the left edge
        # of the other. (It used to be written at 13.5, i.e. a 0.5 mm overlap,
        # and passed only because the older test could not see area overlap.)
        b = self.overlap(_fp("U1", 10, 10, [_rect(-2, -1, 2, 1)]),
                         _fp("U2", 14.0, 10, [_rect(-2, -1, 2, 1)]))
        self.assertEqual(b["value"], 0.0)
        self.assertEqual(b["touching"], [{"a": "U1", "b": "U2"}])
        self.assertIn("touch exactly", b["note"])

    def test_two_courtyards_that_share_area_are_a_collision(self):
        b = self.overlap(_fp("U1", 10, 10, [_rect(-2, -1, 2, 1)]),
                         _fp("U2", 13.0, 10, [_rect(-2, -1, 2, 1)]))
        self.assertEqual(b["value"], 1.0)
        self.assertEqual(b["examples"][0]["a"], "U1")
        self.assertEqual(b["examples"][0]["b"], "U2")
        self.assertEqual(b["examples"][0]["gap_mm"], 0.0)
        self.assertEqual(b["examples"][0]["where"], "drawn courtyards overlap")

    def test_a_flush_pair_that_rounds_to_0_05_ppm_is_still_flush(self):
        """Royalblue54L_feather, U2 against C1.

        U2's right edge lands on 150.68 and C1's left edge on 150.67999999999998 -
        the same point in nanometres, but the raw cross product comes out 1.8e-13
        instead of 0 and a strict sign test calls that a transversal crossing.
        That pair (and roughly forty more on two boards) read as collisions until
        the orientation test was given a tolerance scaled by the squared edge
        length. The offset here is written so the float really does move.
        """
        b = self.overlap(_fp("U1", 10, 10, [_rect(-2, -1, 2, 1)]),
                         _fp("U2", 14.000000000000004, 10, [_rect(-2, -1, 2, 1)]))
        self.assertEqual(b["value"], 0.0)
        self.assertEqual(b["touching"], [{"a": "U1", "b": "U2"}])

    def test_a_real_0_05_mm_overlap_is_not_swallowed_by_that_tolerance(self):
        b = self.overlap(_fp("U1", 10, 10, [_rect(-2, -1, 2, 1)]),
                         _fp("U2", 13.95, 10, [_rect(-2, -1, 2, 1)]))
        self.assertEqual(b["value"], 1.0)

    def test_a_courtyard_drawn_as_four_lines_is_a_ring_and_can_collide(self):
        """Four `fp_line`s chained end to end - the way KiCad actually draws one."""
        ring = _lines([(-2, -1), (2, -1), (2, 1), (-2, 1)])
        b = self.overlap(_fp("U1", 10, 10, [_rect(-2, -1, 2, 1)]),
                         _fp("U2", 13.5, 10, [ring]))
        self.assertEqual(b["value"], 1.0)
        self.assertNotIn("supports", b)

    def test_a_circle_that_clears_a_neighbour_by_0_2_mm_is_not_a_collision(self):
        """The regression the radius bug produced: with r read as sqrt(2)*r about
        a corner the hole reaches x = 11.37 and hits the neighbour at 11.2."""
        b = self.overlap(_fp("H1", 10, 10, [_circle(0, 0, 1.0)]),
                         _fp("U2", 12.7, 10, [_rect(-0.5, -1, 0.5, 1)]))
        self.assertEqual(b["value"], 0.0)

class TestTheHonestRefusals(_Board):
    def test_a_courtyard_that_does_not_close_cannot_carry_a_verdict(self):
        open_ring = _lines([(-2, -1), (2, -1), (2, 1), (-2, 1)], close=False)
        b = self.overlap(_fp("U1", 10, 10, [_rect(-2, -1, 2, 1)]),
                         _fp("U2", 12.2, 10, [open_ring]))
        self.assertEqual(b["value"], 1.0)
        self.assertEqual(b["supports"], "undetermined")
        self.assertIn("closed courtyard", b["why_that_cap"])

    def test_a_part_with_no_courtyard_is_not_put_into_a_box(self):
        b = self.overlap(_fp("U1", 10, 10, [_rect(-2, -1, 2, 1)]),
                         _fp("U2", 10.5, 10.2, [], pad=True))
        # U2 draws nothing: the only thing left is a bbox, and it is a candidate.
        self.assertEqual(b["supports"], "undetermined")
        self.assertIn("bbox", b["where"])

    def test_two_parts_that_both_draw_nothing_are_not_a_pair_at_all(self):
        b = self.overlap(_fp("U1", 10, 10, [], pad=True),
                         _fp("U2", 10.5, 10.2, [], pad=True))
        self.assertEqual(b["value"], 1.0)
        self.assertEqual(b["supports"], "undetermined")
        self.assertEqual([e["a"] for e in b["examples"]], ["U1"])

if __name__ == "__main__":
    unittest.main()
