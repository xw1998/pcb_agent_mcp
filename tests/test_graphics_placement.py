"""Tests for the placement note on a layer plot: where the board sits, and whether
the sheet can hold it.

The claim being tested is narrow on purpose:

* a plot is placed by the design's **file coordinates**, so a board drawn at
  negative y prints above the sheet and the page comes back **blank while the
  export reports success** (docs/99 records the whole hunt) - so the warning has
  to travel beside the files and must not turn a real export into an error;
* the advice in that warning must **actually clear it** - the last test moves the
  same board into positive coordinates with `translate_board` and requires the
  warning to disappear, which is the only thing that makes the advice more than
  text;
* "could not measure" is never reported as "fits": an unreadable file, an unknown
  paper name and a board with no positions all come back as no judgement rather
  than a pass.
"""
import io
import os
import shutil
import sys
import tempfile
import unittest

_HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, os.path.join(_HERE, "..", "src"))

from pcb_agent_mcp import edit as E                       # noqa: E402
from pcb_agent_mcp import fab                             # noqa: E402
from pcb_agent_mcp.fidelity import engine                  # noqa: E402

BOARD = """(kicad_pcb (version 20221018) (generator pcbnew)
  (general (thickness 1.6))
  (paper "A4")
  (layers
    (0 "F.Cu" signal)
    (31 "B.Cu" signal)
    (37 "F.SilkS" user)
    (44 "Edge.Cuts" user)
  )
  (net 0 "")
  (net 1 "GND")
  (gr_line (start %(x0)s %(y0)s) (end %(x1)s %(y0)s) (layer "Edge.Cuts") (width 0.1) (tstamp 00000000-0000-0000-0000-000000000001))
  (gr_line (start %(x1)s %(y0)s) (end %(x1)s %(y1)s) (layer "Edge.Cuts") (width 0.1) (tstamp 00000000-0000-0000-0000-000000000002))
  (gr_line (start %(x1)s %(y1)s) (end %(x0)s %(y1)s) (layer "Edge.Cuts") (width 0.1) (tstamp 00000000-0000-0000-0000-000000000003))
  (gr_line (start %(x0)s %(y1)s) (end %(x0)s %(y0)s) (layer "Edge.Cuts") (width 0.1) (tstamp 00000000-0000-0000-0000-000000000004))
  (footprint "Test:R_0805" (layer "F.Cu") (at %(fx)s %(fy)s) (tstamp 00000000-0000-0000-0000-000000000010)
    (attr smd)
    (fp_text reference "R1" (at 0 0) (layer "F.SilkS") (effects (font (size 1 1) (thickness 0.15))))
    (fp_text value "10k" (at 0 2) (layer "F.Fab") (effects (font (size 1 1) (thickness 0.15))))
    (pad "1" smd roundrect (at 0 0) (size 1 1) (layers "F.Cu" "F.Mask" "F.Paste") (net 1 "GND"))
  )
  (segment (start %(x0)s %(y0)s) (end %(fx)s %(fy)s) (width 0.25) (layer "F.Cu") (net 1) (tstamp 00000000-0000-0000-0000-000000000020))
  (via (at %(fx)s %(fy)s) (size 0.6) (drill 0.3) (layers "F.Cu" "B.Cu") (net 1) (tstamp 00000000-0000-0000-0000-000000000021))
)
"""


def board_text(x0=10.0, y0=10.0, x1=60.0, y1=40.0, fx=30.0, fy=25.0, paper='(paper "A4")'):
    text = BOARD % {"x0": x0, "y0": y0, "x1": x1, "y1": y1, "fx": fx, "fy": fy}
    if paper is not None:
        return text.replace('(paper "A4")', paper)
    return text.replace('(paper "A4")\n', "")


class Extent(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.mkdtemp(prefix="pcbplace-")
        self._home = os.environ.get("PCB_AGENT_HOME")
        os.environ["PCB_AGENT_HOME"] = os.path.join(self.tmp, "home")

    def tearDown(self):
        if self._home is None:
            os.environ.pop("PCB_AGENT_HOME", None)
        else:
            os.environ["PCB_AGENT_HOME"] = self._home
        E.plan.PLANS.clear()
        shutil.rmtree(self.tmp, ignore_errors=True)

    def write(self, text, name="b.kicad_pcb"):
        path = os.path.join(self.tmp, name)
        with io.open(path, "w", encoding="utf-8", newline="\n") as fh:
            fh.write(text)
        return path

    # ------------------------------------------------------------------ facts
    def test_a_board_in_positive_coordinates_fits_and_says_nothing(self):
        got = fab.board_extent(self.write(board_text()))
        self.assertEqual(got["bbox_file_mm"],
                         {"x_min": 10.0, "x_max": 60.0, "y_min": 10.0, "y_max": 40.0})
        self.assertEqual(got["warnings"], [])
        self.assertTrue(got["fits_paper"])
        self.assertNotIn("negative_axes", got)
        # every position is counted, and the via's (at fx fy) is one of them: 4 lines
        # x 2 + footprint 1 + segment 2 + via 1 = 12 - a via is not decoration, and
        # leaving it out of the box is how a board "fits" on paper and clips anyway
        self.assertEqual(got["positions"], 12)

    def test_a_board_drawn_at_negative_y_is_named_and_points_at_the_move(self):
        got = fab.board_extent(self.write(board_text(y0=-40.0, y1=-10.0, fy=-25.0)))
        self.assertEqual(got["negative_axes"], ["y"])
        self.assertFalse(got["fits_paper"])
        joined = " ".join(got["warnings"])
        self.assertIn("negative y", joined)
        self.assertIn("translate_board", joined)
        # the warning hands back the numbers, and the sign is the crux: the file is
        # y-down and the edit API is y-up, so a positive dy would bury the board
        # further. dx is 0 here because x is already positive.
        self.assertEqual(got["advice"]["dx"], 0.0)
        self.assertEqual(got["advice"]["dy"], -40.0)
        # the y values reported are the file's own, not flipped: the box is still
        # -40..-10 and must not be silently turned into 10..40
        self.assertEqual(got["bbox_file_mm"]["y_min"], -40.0)
        self.assertEqual(got["bbox_file_mm"]["y_max"], -10.0)

    def test_a_board_wider_than_the_sheet_is_called_clipped_not_scaled(self):
        got = fab.board_extent(self.write(board_text(x1=400.0, fx=300.0)))
        self.assertEqual(got["warnings"].__len__(), 1)
        self.assertIn("clipped, not scaled", got["warnings"][0])
        self.assertFalse(got["fits_paper"])
        # a size problem is separable from a coordinate-sign problem
        self.assertNotIn("negative_axes", got)

    def test_a_custom_paper_size_is_honoured(self):
        narrow = fab.board_extent(self.write(
            board_text(x1=200.0, fx=150.0, paper='(paper "User" 100 80)'),
            "n.kicad_pcb"))
        self.assertEqual(narrow["paper"], {"name": "User", "w_mm": 100.0, "h_mm": 80.0})
        self.assertEqual(narrow["warnings"].__len__(), 1)

    def test_an_unknown_paper_name_is_not_judged(self):
        got = fab.board_extent(self.write(board_text(paper='(paper "LedgerX")')))
        self.assertNotIn("paper", got)
        self.assertNotIn("fits_paper", got)
        self.assertEqual(got["warnings"], [])

    def test_a_file_that_cannot_be_read_is_not_reported_as_fitting(self):
        bad = self.write("not a board at all", "bad.kicad_pcb")
        self.assertIsNone(fab.board_extent(bad))
        self.assertIsNone(fab.board_extent(os.path.join(self.tmp, "absent.kicad_pcb")))


@unittest.skipUnless(engine.find_kicad_cli(), "kicad-cli is not installed")
class Wiring(unittest.TestCase):
    """The note has to reach `graphics`, and the advice has to clear it."""

    def setUp(self):
        self.tmp = tempfile.mkdtemp(prefix="pcbplace-e2e-")
        self._home = os.environ.get("PCB_AGENT_HOME")
        os.environ["PCB_AGENT_HOME"] = os.path.join(self.tmp, "home")
        # the design lives in its own directory: a plot is refused inside the source
        # tree, and that refusal is a separate rule with its own test
        self.proj = os.path.join(self.tmp, "proj")
        os.makedirs(self.proj)
        self.board = os.path.join(self.proj, "b.kicad_pcb")
        with io.open(self.board, "w", encoding="utf-8", newline="\n") as fh:
            fh.write(board_text(y0=-40.0, y1=-10.0, fy=-25.0))

    def tearDown(self):
        if self._home is None:
            os.environ.pop("PCB_AGENT_HOME", None)
        else:
            os.environ["PCB_AGENT_HOME"] = self._home
        E.plan.PLANS.clear()
        shutil.rmtree(self.tmp, ignore_errors=True)

    def test_a_blank_page_still_reports_ok_and_carries_the_warning(self):
        out = os.path.join(self.tmp, "out")
        got = fab.graphics(self.board, fmt="pdf", out_dir=out)
        # the trap docs/99 records: the export succeeds, the page can be blank
        self.assertEqual(got["status"], "ok")
        self.assertTrue(got["files"])
        self.assertEqual(got["placement"]["negative_axes"], ["y"])
        self.assertTrue(got["warnings"])
        self.assertIn("translate_board", " ".join(got["warnings"]))

    def test_the_advice_actually_clears_the_warning(self):
        """Execute the note literally, numbers and all, and require it to go quiet.

        This is the whole point of carrying the note: it is only advice if following
        it works. The dx/dy are taken from the warning - not written here - so a
        wrong sign cannot pass by being copied into the test.
        """
        first = fab.board_extent(self.board)
        advice = first["advice"]
        self.assertLess(advice["dy"], 0.0)
        plan = E.propose(self.board, [{"op": advice["op"], "dx": advice["dx"],
                                       "dy": advice["dy"]}])
        self.assertEqual(plan["blocking"], [], plan["blocking"])
        E.apply_plan(plan["plan_id"], confirm=True, options=dict(engine=False))
        out = os.path.join(self.tmp, "out2")
        got = fab.graphics(self.board, fmt="pdf", out_dir=out)
        self.assertEqual(got["status"], "ok")
        self.assertEqual(got["warnings"] if "warnings" in got else [], [])
        self.assertTrue(got["placement"]["fits_paper"])
        self.assertNotIn("negative_axes", got["placement"])
        self.assertEqual(got["placement"]["bbox_file_mm"]["y_min"], 0.0)


if __name__ == "__main__":
    unittest.main()
