"""A DRC verdict is only meaningful against the rules it was read against.

The gate compares two DRC runs. One half lives in the server's checkpoint home,
where there is no `.kicad_pro`, and KiCad silently falls back to its own default
rule minimums and - more damagingly - to its own *severities*: on the STM32F412
core board the same 199 KB of bytes gave 515 errors with no project beside it and
0 errors with the project (drill_out_of_range 158, track_width 199,
via_diameter 158 and 14 silk_overlap all flip). A diff across that boundary
invents violations the edit did not create.

So `drc(path, project=...)` must stage the board AND the project together, and
must say which rule set the number came from. These tests fake kicad-cli: what is
under test is the staging, not KiCad.
"""
import json
import os
import shutil
import tempfile
import unittest
from unittest import mock

from pcb_agent_mcp.fidelity import engine

BOARD = '(kicad_pcb (version 20241229) (generator "pcbnew") (layers))\n'
PRO = '{"meta": {"filename": "b.kicad_pro"}, "board": {"design_settings": {}}}\n'


class Staging(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.mkdtemp(prefix="pcb-agent-drcproj-")
        self.seen = []

    def tearDown(self):
        shutil.rmtree(self.tmp, ignore_errors=True)

    def board(self, name="b.kicad_pcb", pro=None):
        path = os.path.join(self.tmp, name)
        with open(path, "w", encoding="utf-8") as fh:
            fh.write(BOARD)
        if pro is not None:
            with open(os.path.splitext(path)[0] + ".kicad_pro", "w",
                      encoding="utf-8") as fh:
                fh.write(pro)
        return path

    def fake_run(self, args, cwd, timeout=180):
        """Record the call; write the report kicad-cli would have written."""
        report = args[args.index("-o") + 1]
        staged = args[-1]
        pro = os.path.splitext(staged)[0] + ".kicad_pro"
        self.seen.append({"args": list(args), "cwd": cwd,
                          "staged": os.path.basename(staged),
                          "dir": os.path.dirname(staged),
                          "files": sorted(os.listdir(os.path.dirname(staged))),
                          # read now: drc() removes its scratch dir before
                          # returning, so nothing outside can look afterwards
                          "staged_text": open(staged, encoding="utf-8").read(),
                          "pro_text": (open(pro, encoding="utf-8").read()
                                       if os.path.isfile(pro) else None)})
        with open(report, "w", encoding="utf-8") as fh:
            json.dump({"violations": [], "unconnected_items": [],
                       "kicad_version": "fake"}, fh)
        return {"ok": True, "returncode": 0, "stdout": "", "stderr": ""}

    def drc(self, path, **kw):
        with mock.patch.object(engine, "find_kicad_cli", return_value="kicad-cli"), \
                mock.patch.object(engine, "_run", side_effect=self.fake_run), \
                mock.patch.object(engine, "version", return_value="fake"):
            return engine.drc(path, **kw)

    # ---------------------------------------------------------------- cases
    def test_the_project_is_staged_beside_the_board_under_the_boards_name(self):
        board = self.board()
        out = self.drc(board, project=self.pro_path(board))
        self.assertEqual(out["status"], "ok")
        call = self.seen[-1]
        self.assertTrue(call["dir"].startswith(tempfile.gettempdir()))
        self.assertNotEqual(call["dir"], os.path.dirname(board))
        self.assertEqual(call["staged"], "b.kicad_pcb")
        self.assertIn("b.kicad_pro", call["files"])
        # the staged project takes the board's name, whatever the source was
        # called - that is what makes KiCad pair them
        self.assertEqual(out["project_file_used"], "rules.kicad_pro")
        self.assertEqual(out["rule_source"], "project")
        self.assertIsNone(out["rule_source_note"])

    def test_a_project_elsewhere_given_explicitly_still_lands_next_to_the_board(self):
        board = self.board("board.kicad_pcb")
        other = os.path.join(self.tmp, "elsewhere.kicad_pro")
        with open(other, "w", encoding="utf-8") as fh:
            fh.write(PRO)
        out = self.drc(board, project=other)
        call = self.seen[-1]
        self.assertEqual(out["project_file_used"], "elsewhere.kicad_pro")
        # the copy carries the board's basename, so the pair still matches
        self.assertEqual(sorted(call["files"]),
                         ["board.kicad_pcb", "board.kicad_pro"])
        self.assertEqual(call["pro_text"], PRO)
        self.assertEqual(call["staged_text"], BOARD)

    def test_the_project_beside_the_board_is_found_without_being_asked(self):
        board = self.board(pro=PRO)
        out = self.drc(board)
        self.assertEqual(out["rule_source"], "project")
        self.assertEqual(out["project_file_used"], "b.kicad_pro")
        self.assertIn("b.kicad_pro", self.seen[-1]["files"])

    def test_no_project_is_said_out_loud_instead_of_looking_like_the_project(self):
        board = self.board()
        out = self.drc(board)
        self.assertEqual(out["status"], "ok")
        self.assertEqual(out["rule_source"], "kicad_defaults")
        self.assertIsNone(out["project_file_used"])
        self.assertIn("kicad_pro", out["rule_source_note"])
        self.assertEqual(self.seen[-1]["files"], ["b.kicad_pcb"])

    def test_the_input_is_never_written_beside_itself(self):
        board = self.board(pro=PRO)
        before = open(board, "rb").read()
        self.drc(board)
        self.assertEqual(open(board, "rb").read(), before)
        self.assertEqual(sorted(os.listdir(self.tmp)), ["b.kicad_pcb",
                                                        "b.kicad_pro"])

    # ------------------------------------------------------------- helper
    def pro_path(self, board):
        path = os.path.join(self.tmp, "rules.kicad_pro")
        with open(path, "w", encoding="utf-8") as fh:
            fh.write(PRO)
        return path


class TheDiffUsesOneRuleSet(unittest.TestCase):
    """Both halves of the gate have to be read against the same project."""

    def test_apply_passes_the_project_found_beside_the_target(self):
        from pcb_agent_mcp.edit import plan as PLAN
        tmp = tempfile.mkdtemp(prefix="pcb-agent-proj-")
        try:
            board = os.path.join(tmp, "x.kicad_pcb")
            open(board, "w", encoding="utf-8").write(BOARD)
            open(os.path.join(tmp, "x.kicad_pro"), "w",
                 encoding="utf-8").write(PRO)
            self.assertEqual(PLAN._project_beside(board),
                             os.path.join(tmp, "x.kicad_pro"))
            # a checkpoint copy lives in the server's home, alone: the caller
            # has to hand it the project it must be judged against
            self.assertIsNone(PLAN._project_beside(
                os.path.join(tmp, "checkpoints", "x.kicad_pcb")))
            self.assertIsNone(PLAN._project_beside(""))
            self.assertIsNone(PLAN._project_beside(None))
        finally:
            shutil.rmtree(tmp, ignore_errors=True)


if __name__ == "__main__":
    unittest.main()
