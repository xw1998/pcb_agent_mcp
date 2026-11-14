"""Generator-side constraints: what the rules hand to layout and routing.

Every test here has a failing twin, because the two defects this module was
written around both looked fine on the page:

* a 24 MHz crystal classified as a magnetic part (the henry test matched the
  "mh" inside "mhz"), and
* a keep-out row whose first two numbers were a box corner while the router
  read a box centre -- off by one half-extent, which also kept the block on the
  wrong side of the board once the router negated it.
"""
import json
import os
import shutil
import tempfile
import types
import unittest

from pcb_agent_mcp.ruleset import design_constraints as DC
from pcb_agent_mcp.ruleset.measure import _is_crystal, _is_magnetic


def part(ref, lib_id, value, pads):
    """A footprint dict in the shape the canonical adapter emits."""
    return {"ref": ref, "lib_id": lib_id, "value": value,
            "pads": [{"number": str(i), "x": x, "y": y, "w": w, "h": h,
                      "net": None} for i, (x, y, w, h) in enumerate(pads)]}


def board(footprints=(), tracks=()):
    return types.SimpleNamespace(footprints=list(footprints),
                                 tracks=list(tracks), vias=[])


class TestPartClassification(unittest.TestCase):

    def test_megahertz_is_not_a_henry(self):
        # The real value on this board's 24 MHz crystal; without the lookahead
        # the regex reads "24 mh" and the crystal becomes an inductor.
        fp = part("Y1", "RK3506:Package_Crystal_SMD_3225-4P", "24 MHz",
                  [(0, 0, 1.0, 1.0)])
        self.assertFalse(_is_magnetic(fp))
        self.assertTrue(_is_crystal(fp))

    def test_a_real_henry_value_is_still_magnetic(self):
        fp = part("L7", "Synthetic:L_4020", "4.3uH", [(0, 0, 1.0, 1.0)])
        self.assertTrue(_is_magnetic(fp))

    def test_a_clock_rate_on_an_inductor_stays_magnetic(self):
        # An inductor whose value mentions a frequency must not be un-classified
        # by the fix: the reference prefix still decides.
        fp = part("L3", "Synthetic:L_Cenker", "600R@100MHz", [(0, 0, 1.0, 1.0)])
        self.assertTrue(_is_magnetic(fp))


class TestKeepoutGeometry(unittest.TestCase):

    def setUp(self):
        # Body pads at 29.2..33.6 x 6.75..10.45 -- the shape of a 4020 inductor.
        self.fp = part("L3", "Synthetic:L_Cenker_CKCS4020", "8.2uH",
                       [(29.7, 7.25, 1.0, 1.0), (33.1, 9.95, 1.0, 1.0)])
        self.b = board([self.fp])

    def test_rows_are_centre_plus_half_extent(self):
        rows, skipped = DC.keepout_rects(self.b)
        self.assertEqual(skipped, [])
        self.assertEqual(len(rows), 1)
        r = rows[0]
        self.assertAlmostEqual(r["cx"], 31.4, places=6)
        self.assertAlmostEqual(r["cy"], 8.6, places=6)
        self.assertAlmostEqual(r["hx"], 2.3, places=6)
        self.assertAlmostEqual(r["hy"], 1.95, places=6)
        self.assertEqual(r["rule"], "layout.magnetic_keepout")

    def test_the_corner_it_used_to_write_is_named_not_tolerated(self):
        rows, _ = DC.keepout_rects(self.b)
        r = rows[0]
        corner_row = [round(r["cx"] - r["hx"], 6), round(r["cy"] - r["hy"], 6),
                      r["hx"], r["hy"]]
        self.assertEqual(DC.classify_row(corner_row, r), "corner_min")
        centre_row = [r["cx"], r["cy"], r["hx"], r["hy"]]
        self.assertEqual(DC.classify_row(centre_row, r), "centre")

    def test_a_crystal_does_not_borrow_the_magnetic_rule(self):
        xtal = part("Y1", "Synthetic:Crystal_3225", "24 MHz",
                    [(0, 0, 1.0, 0.8)])
        rows, _ = DC.keepout_rects(board([xtal]))
        self.assertEqual(rows[0]["kind"], "crystal")
        self.assertEqual(rows[0]["rule"], "layout.crystal_keepout_adjacent")

    def test_a_part_without_pads_is_skipped_and_named(self):
        broken = part("L9", "Synthetic:L_Cenker", "1uH", [])
        rows, skipped = DC.keepout_rects(board([broken]))
        self.assertEqual(rows, [])
        self.assertEqual(skipped[0]["ref"], "L9")

    def test_the_router_payload_matches_the_rows(self):
        tmp = tempfile.mkdtemp(prefix="dc-")
        try:
            path = os.path.join(tmp, "ko.json")
            info = DC.write_maze_keepout(self.b, path)
            with open(path, encoding="utf-8") as fh:
                written = json.load(fh)
            self.assertEqual(written, info["payload"])
            self.assertEqual(written, DC.maze_keepout(self.b))
            self.assertAlmostEqual(written[0][0], 31.4, places=6)
        finally:
            shutil.rmtree(tmp, ignore_errors=True)


class TestNetBinding(unittest.TestCase):

    def test_copper_on_an_unknown_net_is_counted_not_hidden(self):
        fp = part("R1", "Synthetic:R_0402", "10k", [(0, 0, 0.5, 0.5)])
        fp["pads"][0]["net"] = "/SIG"
        tracks = [{"net": "/SIG", "start": [0, 0], "end": [1, 0]},
                  {"net": "/V9", "start": [0, 1], "end": [1, 1]},
                  {"net": None, "start": [0, 2], "end": [1, 2]}]
        nb = DC.net_binding(board([fp], tracks))
        self.assertEqual(nb["pads_with_net"], 1)
        self.assertEqual(nb["tracks_with_net"], 2)
        self.assertEqual(nb["tracks_on_unknown_net"], 1)
        self.assertEqual(nb["unknown_examples"], ["/V9"])

    def test_all_pads_bound_is_reported_as_such(self):
        fp = part("R1", "Synthetic:R_0402", "10k", [(0, 0, 0.5, 0.5)])
        fp["pads"][0]["net"] = "/SIG"
        nb = DC.net_binding(board([fp], [{"net": "/SIG"}]))
        self.assertEqual(nb["pads"], nb["pads_with_net"])
        self.assertEqual(nb["tracks_on_unknown_net"], 0)


if __name__ == "__main__":
    unittest.main()
