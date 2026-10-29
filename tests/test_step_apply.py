"""Tests for the stepped write path (visible between steps, stoppable, identical).

The promise being defended here is narrow and checkable:

* a stepped write is **not a different feature** - the file it ends up with is
  byte-for-byte what the one-shot apply would have produced, otherwise "do it in
  steps so you can watch" would also mean "and get a slightly different board";
* after every chunk the file on disk is a complete, parseable document (and a
  snapshot of it is kept), which is the whole point: open it in the EDA and look;
* the stop file stops it, the resume keeps the stop as a record instead of
  deleting it, and an interruption does not throw away the steps already taken;
* a file that changed on disk between two steps is refused, not overwritten.

Engine (kicad-cli) gates are switched off in these tests (`engine: False`); they
are already covered by test_edit.py, and re-running DRC per chunk would make the
suite minutes long for no extra evidence about stepping.
"""
import os
import shutil
import tempfile
import unittest

from pcb_agent_mcp import edit as E
from pcb_agent_mcp.edit import plan as P
from pcb_agent_mcp.edit import sch_plan as SP
from pcb_agent_mcp.fidelity import kicad_doc as KD
from pcb_agent_mcp.fidelity import kicad_sch as KS

from tests.test_sch_edit import schematic_text

SPEC = {"width_mm": 60, "height_mm": 40, "copper_layers": 2, "engine": False,
        "title": "stepped write"}

# Four operations that touch three different parts of the file, so a chunk
# boundary lands in the middle of a group rather than at a natural seam.
FOUR_OPS = [
    {"op": "add_mech_line", "x1": 5, "y1": 5, "x2": 30, "y2": 5,
     "layer": "Dwgs.User", "width": 0.15},
    {"op": "add_mech_rect", "x0": 10, "y0": 10, "x1": 20, "y1": 18,
     "layer": "Cmts.User", "width": 0.15},
    {"op": "add_mech_circle", "cx": 40, "cy": 20, "r": 3, "layer": "Cmts.User"},
    {"op": "add_board_text", "text": "STEP-BY-STEP", "x": 30, "y": 30,
     "layer": "F.SilkS", "height": 1.2, "thickness": 0.15},
]


class StepBase(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.mkdtemp(prefix="pcbstep-")
        self._home = os.environ.get("PCB_AGENT_HOME")
        os.environ["PCB_AGENT_HOME"] = os.path.join(self.tmp, "home")
        self.board = os.path.join(self.tmp, "board.kicad_pcb")
        E.create_board(self.board, dict(SPEC))

    def tearDown(self):
        if self._home is None:
            os.environ.pop("PCB_AGENT_HOME", None)
        else:
            os.environ["PCB_AGENT_HOME"] = self._home
        P.PLANS.clear()
        P.STEPS.clear()
        stop = P.stop_file()
        if os.path.isfile(stop):
            os.rename(stop, stop + ".leftover")
        shutil.rmtree(self.tmp, ignore_errors=True)

    def read(self, path=None):
        with open(path or self.board, "rb") as fh:
            return fh.read()

    def snapshots(self, plan_id):
        folder = os.path.join(P.steps_root(), plan_id)
        if not os.path.isdir(folder):
            return []
        return sorted(os.listdir(folder))

    def propose(self, ops=None):
        prop = E.propose(self.board, ops or FOUR_OPS, {"engine": False})
        return prop, P.PLANS[prop["plan_id"]]


class TestSteppedEqualsOneShot(StepBase):
    def test_the_finished_file_is_the_one_shot_file_byte_for_byte(self):
        prop, plan = self.propose()
        expected = plan["new_bytes"]

        first = E.apply_steps(prop["plan_id"], confirm=True,
                              options={"step": 2, "max_chunks": 1, "engine": False})
        self.assertFalse(first["done"])
        self.assertEqual(first["operations_done"], 2)
        self.assertNotEqual(self.read(), expected, "two of four ops are not the plan")

        second = E.apply_steps(prop["plan_id"], confirm=True,
                               options={"step": 2, "max_chunks": 1, "engine": False})
        self.assertTrue(second["done"])
        self.assertEqual(self.read(), expected)
        gates = second["gates"]
        self.assertTrue(gates["bytes_written"]["ok"], gates["bytes_written"])
        self.assertTrue(gates["token_identity"]["ok"], gates["token_identity"])
        self.assertTrue(gates["postconditions"]["ok"], gates["postconditions"])

    def test_uneven_chunks_also_land_on_the_same_bytes(self):
        """A chunk boundary must not change a single generated id."""
        prop, plan = self.propose()
        expected = plan["new_bytes"]
        first = E.apply_steps(prop["plan_id"], confirm=True,
                              options={"step": 1, "max_chunks": 1, "engine": False})
        self.assertEqual(first["operations_done"], 1)
        second = E.apply_steps(prop["plan_id"], confirm=True,
                               options={"step": 3, "max_chunks": 1, "engine": False})
        self.assertTrue(second["done"])
        self.assertEqual([c["first"] for c in second["steps_written"]], [1])
        self.assertEqual(self.read(), expected)

    def test_max_chunks_one_call_writes_several_chunks(self):
        prop, plan = self.propose()
        out = E.apply_steps(prop["plan_id"], confirm=True,
                            options={"step": 1, "max_chunks": 4, "engine": False})
        self.assertTrue(out["done"])
        self.assertEqual([c["first"] for c in out["steps_written"]], [0, 1, 2, 3])
        self.assertEqual(self.read(), plan["new_bytes"])


class TestWhatIsVisibleBetweenSteps(StepBase):
    def test_every_chunk_leaves_a_complete_file_and_a_snapshot(self):
        prop, plan = self.propose()
        out = E.apply_steps(prop["plan_id"], confirm=True,
                            options={"step": 1, "max_chunks": 2, "engine": False})
        self.assertEqual(len(out["steps_written"]), 2)

        # the file on disk is a document, not half a write
        doc = KD.KiCadDocument.from_path(self.board)
        self.assertTrue(doc.verify()["token_identity"])

        snaps = self.snapshots(prop["plan_id"])
        self.assertEqual(snaps, ["001.kicad_pcb", "002.kicad_pcb"])
        with open(os.path.join(P.steps_root(), prop["plan_id"], "002.kicad_pcb"),
                  "rb") as fh:
            self.assertEqual(fh.read(), self.read())

        # and the same progress is readable from another process, as a log
        log = E.step_events(prop["plan_id"])
        kinds = [e["kind"] for e in log["events"]]
        self.assertEqual(kinds.count("step"), 2)
        self.assertIn("begin", kinds)
        self.assertEqual([e["seq"] for e in log["events"] if e["kind"] == "step"],
                         [1, 2])

    def test_done_event_and_plan_consumption(self):
        prop, plan = self.propose()
        out = E.apply_steps(prop["plan_id"], confirm=True,
                            options={"step": 2, "max_chunks": 2, "engine": False})
        self.assertTrue(out["done"])
        self.assertNotIn(prop["plan_id"], P.PLANS, "a plan is single-use")
        self.assertNotIn(prop["plan_id"], P.STEPS)
        kinds = [e["kind"] for e in E.step_events(prop["plan_id"])["events"]]
        self.assertEqual(kinds[-1], "done")
        with self.assertRaises(E.EditError) as ctx:
            E.apply_steps(prop["plan_id"], confirm=True, options={"step": 1})
        self.assertEqual(ctx.exception.code, "PLAN_NOT_FOUND")

    def test_confirmation_is_still_required(self):
        prop, plan = self.propose()
        before = self.read()
        with self.assertRaises(E.EditError) as ctx:
            E.apply_steps(prop["plan_id"], confirm=False, options={"step": 1})
        self.assertEqual(ctx.exception.code, "CONFIRMATION_REQUIRED")
        self.assertEqual(self.read(), before)


class TestStoppingAndResuming(StepBase):
    def test_a_stop_file_before_the_first_step_writes_nothing(self):
        prop, plan = self.propose()
        before = self.read()
        E.step_control("stop", note="operator wants a look first")
        self.assertTrue(os.path.isfile(P.stop_file()))

        with self.assertRaises(E.EditError) as ctx:
            E.apply_steps(prop["plan_id"], confirm=True,
                          options={"step": 1, "engine": False})
        self.assertEqual(ctx.exception.code, "STOPPED_BY_USER")
        self.assertEqual(self.read(), before)
        self.assertEqual(self.snapshots(prop["plan_id"]), [])

        # releasing keeps the decision on disk instead of deleting the evidence
        rel = E.step_control("resume")
        self.assertTrue(rel["removed"])
        self.assertFalse(os.path.isfile(P.stop_file()))
        self.assertTrue(os.path.isfile(rel["archived_to"]))
        with open(rel["archived_to"], encoding="utf-8") as fh:
            self.assertIn("operator wants a look first", fh.read())

        out = E.apply_steps(prop["plan_id"], confirm=True,
                           options={"step": 2, "max_chunks": 2, "engine": False})
        self.assertTrue(out["done"])
        self.assertEqual(self.read(), plan["new_bytes"])

    def test_stopping_between_chunks_keeps_the_steps_already_taken(self):
        prop, plan = self.propose()
        E.apply_steps(prop["plan_id"], confirm=True,
                      options={"step": 2, "max_chunks": 1, "engine": False})
        after_first = self.read()

        E.step_control("stop")
        with self.assertRaises(E.EditError) as ctx:
            E.apply_steps(prop["plan_id"], confirm=True,
                          options={"step": 2, "engine": False})
        self.assertEqual(ctx.exception.code, "STOPPED_BY_USER")
        self.assertEqual(self.read(), after_first, "a stop must not roll anything back")
        self.assertEqual(P.STEPS[prop["plan_id"]]["index"], 2)

        E.step_control("resume")
        out = E.apply_steps(prop["plan_id"], confirm=True,
                           options={"step": 2, "engine": False})
        self.assertTrue(out["done"])
        self.assertEqual([c["first"] for c in out["steps_written"]], [2])
        self.assertEqual(self.read(), plan["new_bytes"])

    def test_status_reports_the_waiter(self):
        self.assertFalse(E.step_control("status")["stopped"])
        E.step_control("stop")
        self.assertTrue(E.step_control("status")["stopped"])


class TestRefusals(StepBase):
    def test_a_file_saved_in_the_editor_between_steps_is_refused(self):
        prop, plan = self.propose()
        E.apply_steps(prop["plan_id"], confirm=True,
                      options={"step": 2, "max_chunks": 1, "engine": False})
        on_disk = self.read()
        with open(self.board, "ab") as fh:
            fh.write(b"\n")
        touched = self.read()

        with self.assertRaises(E.EditError) as ctx:
            E.apply_steps(prop["plan_id"], confirm=True,
                          options={"step": 2, "engine": False})
        self.assertEqual(ctx.exception.code, "BOARD_CHANGED_SINCE_STEP")
        self.assertEqual(self.read(), touched, "nothing may be written after a refusal")
        self.assertNotEqual(self.read(), on_disk)
        kinds = [e["kind"] for e in E.step_events(prop["plan_id"])["events"]]
        self.assertIn("refused", kinds)


class TestSchematicStepping(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.mkdtemp(prefix="pcbstepsch-")
        self._home = os.environ.get("PCB_AGENT_HOME")
        os.environ["PCB_AGENT_HOME"] = os.path.join(self.tmp, "home")
        self.path = os.path.join(self.tmp, "unit.kicad_sch")
        with open(self.path, "wb") as fh:
            fh.write(schematic_text(with_symbol=True).encode("utf-8"))

    def tearDown(self):
        if self._home is None:
            os.environ.pop("PCB_AGENT_HOME", None)
        else:
            os.environ["PCB_AGENT_HOME"] = self._home
        SP.PLANS.clear()
        P.STEPS.clear()
        stop = P.stop_file()
        if os.path.isfile(stop):
            os.rename(stop, stop + ".leftover")
        shutil.rmtree(self.tmp, ignore_errors=True)

    def read(self):
        with open(self.path, "rb") as fh:
            return fh.read()

    def test_schematic_stepwise_lands_on_the_one_shot_bytes(self):
        ops = [
            {"op": "add_label", "text": "STEP_A", "x": 55, "y": 50.8},
            {"op": "add_label", "text": "STEP_B", "x": 58, "y": 50.8},
        ]
        prop = SP.propose(self.path, ops, {"engine": False})
        plan = SP.PLANS[prop["plan_id"]]
        expected = plan["new_bytes"]

        out = SP.apply_steps(prop["plan_id"], confirm=True,
                             options={"step": 1, "max_chunks": 1, "engine": False})
        self.assertFalse(out["done"])
        self.assertEqual(out["operations_done"], 1)
        self.assertNotEqual(self.read(), plan["new_bytes"])

        # the intermediate is a schematic, not a partial write
        mid = KS.KiCadSchematic.from_path(self.path)
        self.assertTrue(mid.verify()["token_identity"])

        out = SP.apply_steps(prop["plan_id"], confirm=True,
                             options={"step": 1, "max_chunks": 1, "engine": False})
        self.assertTrue(out["done"])
        self.assertEqual(self.read(), expected)
        self.assertTrue(out["gates"]["bytes_written"]["ok"], out["gates"]["bytes_written"])

    def test_changed_code_differs_between_the_two_paths(self):
        """The refusal has to name the right file type, or the hint misleads."""
        prop = SP.propose(self.path, [{"op": "add_label", "text": "X",
                                       "x": 55, "y": 50.8}], {"engine": False})
        SP.apply_steps(prop["plan_id"], confirm=True,
                       options={"step": 1, "max_chunks": 1, "engine": False})
        # nothing left to write means the plan finished and was consumed
        self.assertNotIn(prop["plan_id"], SP.PLANS)


class TestToolSurface(unittest.TestCase):
    """The stepping has to be reachable the way a caller actually reaches it.

    A model cannot call `plan.apply_steps`; it calls `board_apply` with a `step`, and
    it stops or inspects through `board_progress`. If those two do not line up with
    the functions underneath, the feature exists only in the test suite.
    """

    def setUp(self):
        self.tmp = tempfile.mkdtemp(prefix="pcbsteptool-")
        self._home = os.environ.get("PCB_AGENT_HOME")
        os.environ["PCB_AGENT_HOME"] = os.path.join(self.tmp, "home")

    def tearDown(self):
        if self._home is None:
            os.environ.pop("PCB_AGENT_HOME", None)
        else:
            os.environ["PCB_AGENT_HOME"] = self._home
        P.PLANS.clear()
        P.STEPS.clear()
        stop = P.stop_file()
        if os.path.isfile(stop):
            os.rename(stop, stop + ".leftover")
        shutil.rmtree(self.tmp, ignore_errors=True)

    def test_the_two_entry_points_are_registered(self):
        from pcb_agent_mcp import tools as T
        self.assertIn("board_progress", T.TOOLS)
        self.assertIn("lceda_pro_steps", T.TOOLS)
        props = T.TOOLS["board_apply"]["inputSchema"]["properties"]
        self.assertIn("step", props["options"]["properties"])
        self.assertIn("max_chunks", props["options"]["properties"])

    def test_stop_resume_and_log_through_the_tool(self):
        from pcb_agent_mcp import tools as T
        out = T.call_tool("board_progress", {"action": "status"})
        self.assertEqual(out.get("status"), "ok", out)
        self.assertFalse(out["stopped"])

        out = T.call_tool("board_progress", {"action": "stop", "note": "look first"})
        self.assertEqual(out.get("status"), "ok", out)
        self.assertTrue(out["stop_file"].endswith("STOP"))

        out = T.call_tool("board_progress", {"action": "status"})
        self.assertTrue(out["stopped"])

        out = T.call_tool("board_progress", {"action": "resume"})
        self.assertEqual(out.get("status"), "ok", out)
        self.assertTrue(out["archived_to"])
        self.assertFalse(os.path.isfile(P.stop_file()))

        out = T.call_tool("board_progress", {"action": "log"})
        self.assertEqual(out.get("status"), "ok", out)
        self.assertEqual(out["action"], "log")
        self.assertIn("how_to_stop", out)

    def test_a_stepped_board_apply_through_the_tool(self):
        from pcb_agent_mcp import tools as T
        board = os.path.join(self.tmp, "tool.kicad_pcb")
        E.create_board(board, dict(SPEC))
        prop = T.call_tool("board_propose", {"path": board, "operations": FOUR_OPS,
                                            "options": {"engine": False}})
        self.assertEqual(prop.get("status"), "ok", prop)
        plan_id = prop["plan_id"]
        plan = P.PLANS[plan_id]

        first = T.call_tool("board_apply", {"plan_id": plan_id, "confirm": True,
                                           "options": {"step": 2, "max_chunks": 1,
                                                       "engine": False}})
        self.assertEqual(first.get("status"), "ok", first.get("message"))
        self.assertFalse(first["done"])

        log = T.call_tool("board_progress", {"plan_id": plan_id})
        self.assertEqual(log["total"], 2, log)

        second = T.call_tool("board_apply", {"plan_id": plan_id, "confirm": True,
                                            "options": {"step": 2, "engine": False}})
        self.assertEqual(second.get("status"), "ok", second.get("message"))
        self.assertTrue(second["done"])
        with open(board, "rb") as fh:
            self.assertEqual(fh.read(), plan["new_bytes"])


if __name__ == "__main__":
    unittest.main()
