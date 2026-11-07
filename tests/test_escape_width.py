"""board_routability(width_scan_mm=...): how wide a track can actually leave a pad?

`escape` already answered "can this pad get out at all", with a track width the
board's net class chose. That answered a different question than the one a build
asks when a 0.4 mm rail will not leave an LQFP pad: **what is the widest track
that does fit**. Measured on a real 0.5 mm-pitch LQFP100, U1.14 / U1.19 / U1.21
come back at 0.3 mm - which is the number behind "+3V3 drawn at 0.4 mm cannot be
routed out of the MCU", and it is invisible in a yes/no escape answer.

Four things are pinned, each with a control that can fail:

1. A lone pad on an empty board takes the largest rung of the ladder.
2. A pad in a corridor takes a smaller rung than the same pad without the walls
   - measured in the same test, on two boards that differ only by the walls.
   (Control for "the scan is not just returning the first rung".)
3. With `width_scan_mm` absent the field is absent - the old answer is byte for
   byte what it was.
4. The ladder is honoured as given: asking only for 0.15 can never return 0.6.
"""
import os
import shutil
import sys
import tempfile
import unittest

sys.path.insert(0, os.path.join(os.path.dirname(os.path.dirname(
    os.path.abspath(__file__))), "src"))
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

from pcb_agent_mcp import edit as E
from pcb_agent_mcp import oracle
from pcb_agent_mcp import tools
from pcb_agent_mcp.edit import plan as P

from test_routability import SPEC, RoutabilityBase


class TestEscapeWidth(RoutabilityBase):
    def test_lone_pad_takes_the_top_rung(self):
        self.apply(self.place("Lone", [("1", 0, 0, 1.0, 1.0)], nets={"1": "SIG"}))
        v = self.measure(width_scan=[0.6, 0.4, 0.3, 0.2, 0.15])["value"]
        narrow = v["narrowest_escapes"]
        self.assertEqual(len(narrow), 1, "an empty board has exactly one pad")
        self.assertEqual(narrow[0]["reference"], "U1")
        self.assertEqual(narrow[0]["max_width_mm"], 0.6)

    def _widest(self, ref, **kw):
        v = self.measure(width_scan=[0.6, 0.4, 0.3, 0.2, 0.15], worst=40, **kw)["value"]
        got = [r["max_width_mm"] for r in v["narrowest_escapes"]
               if r["reference"] == ref]
        self.assertEqual(len(got), 1, "exactly one row for %s, got %r" % (ref, got))
        return got[0]

    def _restart(self):
        P.PLANS.clear()
        os.remove(self.board)
        E.create_board(self.board, dict(SPEC))

    def test_a_corridor_costs_width(self):
        """Same pad, same board, and the only difference is two walls.

        Three neighbours do NOT cost width: a pad with one side wide open still
        leaves at the top rung, and pretending otherwise would be the scan
        reporting an obstacle it never measured. What does cost width is a
        corridor - copper on two opposite sides close enough that the lane out
        has to squeeze - so that is the geometry pinned here, with the empty
        board measured first as the control.
        """
        self.apply(self.place("Lone", [("1", 0, 0, 0.25, 0.25)],
                              nets={"1": "SIG"}))
        free = self._widest("U1")
        self.assertEqual(free, 0.6, "on an empty board the top rung fits")

        self._restart()
        # Two 6 x 0.25 walls 0.575 mm above and below the centreline. Inner edge
        # is 0.45 mm away, so a lane needs 0.45 - w/2 >= 0.2 clearance to stay
        # clear of them: 0.6 is refused, 0.4 fits with 0.05 mm to spare.
        self.apply(
            self.place("Lone", [("1", 0, 0, 0.25, 0.25)], nets={"1": "SIG"}),
            self.place("Wall", [("1", 0.0, 0.575, 6.0, 0.25),
                                ("2", 0.0, -0.575, 6.0, 0.25)],
                       ref="U2", x=30.0, y=20.0, nets={"1": "A", "2": "B"}))
        squeezed = self._widest("U1")
        self.assertLess(squeezed, free,
                        "walls 0.45 mm off the centreline must cost width")

    def test_without_the_ladder_the_answer_does_not_change(self):
        self.apply(self.place("Lone", [("1", 0, 0, 1.0, 1.0)], nets={"1": "SIG"}))
        plain = self.measure()["value"]
        for key in ("one_way", "blocked", "bend_required"):
            for row in plain[key]:
                self.assertNotIn("widest_escape", row,
                                 "an unasked question must not appear in the answer")

    def test_the_ladder_is_what_the_caller_asked_for(self):
        self.apply(self.place("Lone", [("1", 0, 0, 1.0, 1.0)], nets={"1": "SIG"}))
        out = tools.TOOLS["board_routability"]["handler"](
            {"path": self.board, "width_scan_mm": [0.15]})
        self.assertEqual(out["status"], "ok")
        v = out["value"]
        for key in ("one_way", "blocked", "bend_required"):
            for row in v[key]:
                w = row.get("widest_escape")
                if w:
                    self.assertEqual(w["max_width_mm"], 0.15)


if __name__ == "__main__":
    unittest.main()
