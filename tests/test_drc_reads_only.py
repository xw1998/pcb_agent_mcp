"""A DRC is a read. It may not leave the board it read any different.

The bug this file exists for: `fidelity.engine.drc(..., refill=True,
save_to=...)` passes kicad-cli `--save-board`, which makes kicad-cli WRITE the
board it was handed. The scratch copy was only made when a `.kicad_pro` was
around, so on a board without one the "board it was handed" was the caller's own
file - and kicad-cli re-saved it with KiCad's writer. KiCad 10 treats
`(net_class "Default" ... (clearance 0.2) (trace_width 0.25) ...)` as legacy and
drops it on save, so one apply (which runs the DRC gate with save_to) silently
removed the board's clearance: the file still parsed, still plotted, and the next
`board_route` refused it with RULES_ABSENT. Nothing in the answer said so.

The assertions below are about the bytes on disk, because that is the only thing
that was actually wrong.
"""
import os
import shutil
import tempfile
import unittest

from pcb_agent_mcp import edit as E
from pcb_agent_mcp.fidelity import engine as ENGINE

HAS_CLI = ENGINE.find_kicad_cli() is not None

SPEC = {"width_mm": 60, "height_mm": 40, "copper_layers": 2, "engine": False,
        "title": "drc-reads-only"}

def read_bytes(path):
    with open(path, "rb") as fh:
        return fh.read()

@unittest.skipUnless(HAS_CLI, "kicad-cli is not installed here")
class ARefilledDrcLeavesTheBoardAlone(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.mkdtemp(prefix="pcbdrcro-")
        self.board = os.path.join(self.tmp, "board.kicad_pcb")
        E.create_board(self.board, dict(SPEC))
        # the board must actually state a clearance, or "it was preserved" says
        # nothing
        self.assertIn(b"net_class", read_bytes(self.board))

    def tearDown(self):
        shutil.rmtree(self.tmp, ignore_errors=True)

    def test_the_bytes_are_identical_after_a_verdict_with_save_to(self):
        before = read_bytes(self.board)
        out = ENGINE.drc(self.board, refill=True,
                         save_to=os.path.join(self.tmp, "refilled.kicad_pcb"))
        self.assertEqual(out["status"], "ok", out.get("message"))
        self.assertTrue(out["refilled_board_saved_to"])
        self.assertEqual(read_bytes(self.board), before)

    def test_the_saved_copy_is_the_one_the_filler_touched_not_the_original(self):
        """The point of `save_to` is the refilled polygons - so the copy has to
        exist and has to be the refilled one, not an untouched duplicate."""
        out = ENGINE.drc(self.board, refill=True,
                         save_to=os.path.join(self.tmp, "refilled.kicad_pcb"))
        saved = out["refilled_board_saved_to"]
        self.assertTrue(os.path.isfile(saved))
        # KiCad re-writes what it is given; our own writer would have kept the
        # file byte-identical, so a size/format difference here proves the copy
        # came back from the engine
        self.assertNotEqual(read_bytes(saved), read_bytes(self.board))

    def test_the_clearance_the_board_states_survives_the_read(self):
        ENGINE.drc(self.board, refill=True,
                   save_to=os.path.join(self.tmp, "refilled.kicad_pcb"))
        body = read_bytes(self.board).decode("utf-8")
        self.assertIn('(net_class "Default"', body)
        self.assertIn("(clearance 0.2)", body)

    def test_an_apply_with_the_engine_on_keeps_the_board_routable(self):
        """The end-to-end shape of the bug: edit once, then read the rules.

        `board_route` was only the messenger here; the question "does the board
        still say what clearance it wants" is what a file-stripping write
        destroys."""
        from pcb_agent_mcp.route import rules as R
        from pcb_agent_mcp import tools
        plan = E.propose(self.board, [
            {"op": "add_track", "net": "SIG", "x1": 5.0, "y1": 5.0,
             "x2": 25.0, "y2": 5.0, "width_mm": 0.25, "layer": "F.Cu",
             "create_net": True}])
        self.assertEqual(plan["blocking"], [], plan["blocking"])
        E.apply_plan(plan["plan_id"], confirm=True, options={"engine": True})
        board, e = tools._load_board({"path": self.board})
        self.assertIsNone(e, e)
        bundle = R.resolve(board)
        self.assertAlmostEqual(bundle["clearance_mm"], 0.2, places=6)
        self.assertEqual(bundle["sources"]["clearance"], "net class Default")

if __name__ == "__main__":
    unittest.main()
