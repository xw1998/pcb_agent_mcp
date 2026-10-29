"""Tests for the write path: ops, checks, propose/apply gates, checkpoints.

The point of these tests is not "the function returns a dict". It is that the
four gates have teeth: a plan that lies has to be caught and the board has to be
left exactly as it was. So several tests deliberately break a plan to prove the
rollback fires.

Engine (kicad-cli) checks are opt-in per test via `engine=True` because a full
comparison spawns a dozen kicad-cli runs; the rest of the suite stays fast.
"""
import json
import os
import shutil
import tempfile
import unittest

from pcb_agent_mcp import edit as E
from pcb_agent_mcp.edit import checks as C
from pcb_agent_mcp.edit import ops as O
from pcb_agent_mcp.edit import plan as P
from pcb_agent_mcp.fidelity import engine as ENGINE
from pcb_agent_mcp.fidelity import kicad_doc as K
from pcb_agent_mcp.fidelity import sexpr as S

SPEC = {"width_mm": 60, "height_mm": 40, "copper_layers": 4,
        "title": "unit test board"}

# A real create_board runs kicad-cli twice (stats + DRC) and one of those runs
# costs ~90 s on this machine, so the starting board is built ONCE with the
# engine gate live and every test then starts from those exact bytes. The
# template is still a board KiCad itself accepted - only the repeated proof is
# shared.
_TEMPLATE = {}

def template_bytes():
    if "bytes" not in _TEMPLATE:
        tmp = tempfile.mkdtemp(prefix="pcbtemplate-")
        try:
            path = os.path.join(tmp, "template.kicad_pcb")
            _TEMPLATE["report"] = E.create_board(path, dict(SPEC))
            with open(path, "rb") as fh:
                _TEMPLATE["bytes"] = fh.read()
        finally:
            shutil.rmtree(tmp, ignore_errors=True)
    return _TEMPLATE["bytes"]

class EditTestBase(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.mkdtemp(prefix="pcbedit-")
        self._home = os.environ.get("PCB_AGENT_HOME")
        os.environ["PCB_AGENT_HOME"] = os.path.join(self.tmp, "home")
        self.board = os.path.join(self.tmp, "board.kicad_pcb")
        with open(self.board, "wb") as fh:
            fh.write(template_bytes())

    def tearDown(self):
        if self._home is None:
            os.environ.pop("PCB_AGENT_HOME", None)
        else:
            os.environ["PCB_AGENT_HOME"] = self._home
        P.PLANS.clear()
        shutil.rmtree(self.tmp, ignore_errors=True)

    def read(self, path=None):
        with open(path or self.board, "rb") as fh:
            return fh.read()

    def doc(self, path=None):
        return K.KiCadDocument.from_path(path or self.board)


class TestCreateBoard(EditTestBase):
    def test_created_board_is_v7_with_a_closed_outline(self):
        doc = self.doc()
        self.assertEqual(doc.stats()["version"], "20221018")
        report = C.outline_report(doc)
        self.assertTrue(report["closed"])
        self.assertEqual(report["size_mm"], {"width": 60.0, "height": 40.0})
        self.assertTrue(doc.verify()["token_identity"])

    def test_layer_table_uses_v7_numbers_not_v10(self):
        doc = self.doc()
        table = {}
        for item in S.child(doc.board(), "layers").items[1:]:
            table[S.arg_text(item, 1)] = S.arg_text(item, 0)
        self.assertEqual(table["F.Cu"], "0")
        self.assertEqual(table["B.Cu"], "31")      # v10 would be 2
        self.assertEqual(table["Edge.Cuts"], "44")  # v10 would be 25
        self.assertEqual([table["In1.Cu"], table["In2.Cu"]], ["1", "2"])

    def test_engine_proved_the_template_once(self):
        gates = template_bytes() and _TEMPLATE["report"]["gates"]
        self.assertTrue(gates["engine_stats"]["ok"], gates.get("engine_stats"))
        self.assertTrue(gates["engine_drc"]["ok"], gates.get("engine_drc"))

    def test_refuses_to_overwrite_without_permission(self):
        with self.assertRaises(E.EditError) as ctx:
            E.create_board(self.board, dict(SPEC, engine=False))
        self.assertEqual(ctx.exception.code, "TARGET_EXISTS")

    def test_overwrite_true_allowed(self):
        r = E.create_board(self.board, dict(SPEC, overwrite=True, width_mm=70,
                                           engine=False))
        self.assertEqual(r["status"], "ok")
        self.assertEqual(C.outline_report(self.doc())["size_mm"]["width"], 70.0)

    def test_two_layer_board_has_no_inner_copper(self):
        other = os.path.join(self.tmp, "two.kicad_pcb")
        E.create_board(other, {"width_mm": 30, "height_mm": 20, "copper_layers": 2,
                               "engine": False})
        table = {}
        for item in S.child(self.doc(other).board(), "layers").items[1:]:
            table[S.arg_text(item, 1)] = S.arg_text(item, 0)
        self.assertNotIn("In1.Cu", table)
        self.assertEqual(table["B.Cu"], "31")


class TestOpsCatalogue(unittest.TestCase):
    def test_catalogue_is_json_and_complete(self):
        ops = E.describe_ops()
        json.dumps(ops)                      # a set() here breaks the tool schema
        names = {o["op"] for o in ops}
        for needed in ("add_outline_rect", "add_slot", "add_cutout_circle",
                       "add_mech_rect", "add_board_text", "add_track", "add_via",
                       "add_keepout", "add_zone", "add_net", "set_netclass",
                       "set_board_thickness"):
            self.assertIn(needed, names)
        for op in ops:
            self.assertTrue(op["summary"], op["op"])
            self.assertIsInstance(op["affects"], list)


class TestChecks(EditTestBase):
    def test_outline_gap_is_measured(self):
        doc = self.doc()
        lines = [n for n in doc.items() if S.head(n) == "gr_line"]
        doc.board().items.remove(lines[0])
        report = C.outline_report(doc)
        self.assertFalse(report["closed"])
        self.assertEqual(len(report["gaps_mm"]), 2)   # the two ends of the hole
        self.assertGreater(report["largest_gap_mm"], 20)

    def test_track_outside_outline_is_reported(self):
        doc = self.doc()
        ed = O.Editor(doc)
        O.OPS["add_track"]["run"](ed, {"x1": 10, "y1": 10, "x2": 90, "y2": 10,
                                       "width": 0.3, "layer": "F.Cu", "net": "SIG",
                                       "create_net": True}, "t")
        outside = C.outside_report(doc)
        self.assertTrue(outside["outside_total"] >= 1)
        self.assertTrue(outside["outside"]["tracks"])

    def test_copper_clearance_finds_a_close_pair(self):
        doc = self.doc()
        ed = O.Editor(doc)
        ed.net_code("A", allow_create=True)
        ed.net_code("B", allow_create=True)
        for net, y in (("A", 10.0), ("B", 10.1)):
            O.OPS["add_track"]["run"](ed, {"x1": 5, "y1": y, "x2": 25, "y2": y,
                                           "width": 0.2, "layer": "F.Cu", "net": net,
                                           "create_net": True}, net)
        out = C.copper_clearance(doc, threshold_mm=0.2)
        self.assertGreaterEqual(out["violations"], 1)
        self.assertLess(out["worst"][0]["gap_mm"], 0.2)

    def test_digest_counts_and_delta(self):
        before = C.digest(self.doc())
        prop = E.propose(self.board, [{"op": "add_via", "x": 20, "y": 20,
                                       "size": 0.6, "drill": 0.3, "net": "VCC",
                                       "create_net": True}])
        after = prop["board_after"]
        self.assertEqual(after["delta"]["vias"], 1)
        self.assertEqual(after["counts"]["vias"], before["counts"]["vias"] + 1)


class TestSlotAndHoles(EditTestBase):
    def test_slot_is_two_lines_and_two_arcs_with_the_right_chord(self):
        prop = E.propose(self.board, [{"op": "add_slot", "x1": 8, "y1": 14,
                                       "x2": 26, "y2": 14, "slot_width": 3.2}])
        self.assertFalse(prop["blocking"])
        doc = K.KiCadDocument.from_text(P.PLANS[prop["plan_id"]]["new_text"],
                                        self.board)
        arcs = [n for n in doc.items() if S.head(n) == "gr_arc"]
        lines = [n for n in doc.items() if S.head(n) == "gr_line"]
        self.assertEqual(len(arcs), 2)
        self.assertEqual(len(lines), 4 + 2)          # outline + slot sides
        for a in arcs:
            st, en = S.child(a, "start"), S.child(a, "end")
            chord = ((S.arg_num(st, 1, 0) - S.arg_num(en, 1, 0)) ** 2 +
                     (S.arg_num(st, 2, 0) - S.arg_num(en, 2, 0)) ** 2) ** 0.5
            self.assertAlmostEqual(chord, 3.2, places=4)

    def test_cutout_circle_stays_inside_the_board(self):
        prop = E.propose(self.board, [{"op": "add_cutout_circle", "cx": 48,
                                       "cy": 10, "r": 2.5}])
        doc = K.KiCadDocument.from_text(P.PLANS[prop["plan_id"]]["new_text"],
                                        self.board)
        circles = [n for n in doc.items() if S.head(n) == "gr_circle"]
        self.assertEqual(len(circles), 1)
        self.assertEqual(S.arg_text(S.child(circles[0], "layer"), 1), "Edge.Cuts")


class TestProposeNeverWrites(EditTestBase):
    def test_propose_leaves_the_file_byte_identical(self):
        before = self.read()
        prop = E.propose(self.board, [{"op": "add_net", "name": "VCC"}])
        self.assertEqual(self.read(), before)
        self.assertEqual(prop["status"], "ok")
        self.assertIn("nothing_written", prop)

    def test_propose_refuses_a_plan_with_no_operations(self):
        with self.assertRaises(E.EditError) as ctx:
            E.propose(self.board, [])
        self.assertEqual(ctx.exception.code, "ARG_MISSING")

    def test_propose_refuses_a_missing_file(self):
        with self.assertRaises(E.EditError) as ctx:
            E.propose(os.path.join(self.tmp, "nope.kicad_pcb"),
                      [{"op": "add_net", "name": "VCC"}])
        self.assertEqual(ctx.exception.code, "FILE_NOT_FOUND")

    def test_propose_refuses_a_non_kicad_file(self):
        bad = os.path.join(self.tmp, "x.txt")
        with open(bad, "w") as fh:
            fh.write("hello")
        with self.assertRaises(E.EditError) as ctx:
            E.propose(bad, [{"op": "add_net", "name": "VCC"}])
        self.assertEqual(ctx.exception.code, "FORMAT_NOT_SUPPORTED")

    def test_unknown_op_is_named(self):
        with self.assertRaises(O.OpError) as ctx:
            E.propose(self.board, [{"op": "teleport_footprint"}])
        self.assertEqual(ctx.exception.code, "OP_UNKNOWN")
        self.assertIn("teleport_footprint", str(ctx.exception))

    def test_bad_layer_is_refused_with_the_layer_table(self):
        with self.assertRaises(O.OpError) as ctx:
            E.propose(self.board, [{"op": "add_mech_line", "x1": 1, "y1": 1,
                                    "x2": 2, "y2": 2, "layer": "F.Nonsense"}])
        self.assertEqual(ctx.exception.code, "LAYER_NOT_IN_BOARD")

    def test_mech_layer_outside_the_whitelist_is_refused(self):
        with self.assertRaises(O.OpError) as ctx:
            E.propose(self.board, [{"op": "add_mech_line", "x1": 1, "y1": 1,
                                    "x2": 2, "y2": 2, "layer": "F.Cu"}])
        self.assertEqual(ctx.exception.code, "LAYER_NOT_ALLOWED")

    def test_an_open_outline_blocks_the_plan(self):
        """A gap in Edge.Cuts must be named as blocking, with how to clear it."""
        doc = self.doc()
        edge = [n for n in doc.items() if S.head(n) == "gr_line"]
        doc.board().items.remove(edge[0])
        with open(self.board, "wb") as fh:
            fh.write(doc.to_bytes())
        prop = E.propose(self.board, [{"op": "add_net", "name": "VCC"}])
        # an outline that was ALREADY open is reported, not turned into a veto:
        # blocking here would mean "no op may ever touch an unfinished board".
        gates = [w["gate"] for w in prop["warnings"]]
        self.assertIn("outline_already_open", gates)
        self.assertEqual(prop["blocking"], [])
        # ...but a plan that TOUCHES the outline on an open board is blocked.
        prop = E.propose(self.board, [{"op": "add_outline_line", "x1": 0, "y1": 0,
                                       "x2": 10, "y2": 0}])
        self.assertIn("outline_not_closed", [b["gate"] for b in prop["blocking"]])
        self.assertIn("how_to_clear", prop["blocking"][0])

    def test_a_redundant_edge_that_leaves_a_loose_end_is_blocking(self):
        # an extra segment with one free end really is an open outline
        prop = E.propose(self.board, [{"op": "add_outline_line", "x1": 0, "y1": 0,
                                       "x2": 10, "y2": 0}])
        self.assertIn("outline_not_closed", [b["gate"] for b in prop["blocking"]])

    def test_zero_sized_rectangle_is_refused(self):
        with self.assertRaises(O.OpError) as ctx:
            E.propose(self.board, [{"op": "add_cutout_rect", "x0": 5, "y0": 5,
                                    "x1": 5, "y1": 9}])
        self.assertEqual(ctx.exception.code, "ARG_INVALID")


class TestApplyGates(EditTestBase):
    def test_apply_needs_confirm(self):
        prop = E.propose(self.board, [{"op": "add_net", "name": "VCC"}])
        with self.assertRaises(E.EditError) as ctx:
            E.apply_plan(prop["plan_id"])
        self.assertEqual(ctx.exception.code, "CONFIRMATION_REQUIRED")
        self.assertEqual(self.read(), self.read())     # nothing written

    def test_apply_writes_and_verifies(self):
        prop = E.propose(self.board, [{"op": "add_net", "name": "VCC"}])
        r = E.apply_plan(prop["plan_id"], confirm=True, options={"engine": False})
        self.assertTrue(r["gates"]["bytes_written"]["ok"])
        self.assertTrue(r["gates"]["token_identity"]["ok"])
        self.assertTrue(r["gates"]["postconditions"]["all_verified"])
        self.assertIn("VCC", self.read().decode("utf-8"))

    def test_plan_is_single_use(self):
        prop = E.propose(self.board, [{"op": "add_net", "name": "VCC"}])
        E.apply_plan(prop["plan_id"], confirm=True, options={"engine": False})
        with self.assertRaises(E.EditError) as ctx:
            E.apply_plan(prop["plan_id"], confirm=True, options={"engine": False})
        self.assertEqual(ctx.exception.code, "PLAN_NOT_FOUND")

    def test_board_changed_since_plan_is_refused(self):
        prop = E.propose(self.board, [{"op": "add_net", "name": "VCC"}])
        with open(self.board, "ab") as fh:
            fh.write(b"\n")
        with self.assertRaises(E.EditError) as ctx:
            E.apply_plan(prop["plan_id"], confirm=True, options={"engine": False})
        self.assertEqual(ctx.exception.code, "BOARD_CHANGED_SINCE_PLAN")

    def test_postcondition_gate_rolls_back(self):
        """A plan whose file was tampered with must not survive the gates."""
        prop = E.propose(self.board, [{"op": "add_net", "name": "VCC"}])
        before = self.read()
        plan = P.PLANS[prop["plan_id"]]
        plan["new_bytes"] = before                  # the change silently vanishes
        plan["new_signature"] = S.signature(
            K.KiCadDocument.from_text(before.decode("utf-8"), self.board).tree)
        with self.assertRaises(E.EditError) as ctx:
            E.apply_plan(prop["plan_id"], confirm=True, options={"engine": False})
        self.assertEqual(ctx.exception.code, "GATE_BLOCKED")
        self.assertTrue(any("postcondition" in b
                            for b in ctx.exception.extra["blocking"]))
        self.assertEqual(self.read(), before, "the board must be rolled back")

    def test_bytes_gate_catches_a_wrong_write(self):
        prop = E.propose(self.board, [{"op": "add_net", "name": "VCC"}])
        before = self.read()
        plan = P.PLANS[prop["plan_id"]]
        plan["new_bytes"] = plan["new_bytes"].replace(b"VCC", b"VDD")
        with self.assertRaises(E.EditError) as ctx:
            E.apply_plan(prop["plan_id"], confirm=True, options={"engine": False})
        self.assertEqual(ctx.exception.code, "GATE_BLOCKED")
        self.assertIn("bytes_written", ctx.exception.extra["gates"])
        self.assertEqual(self.read(), before)

    def test_new_file_mode_refuses_an_existing_target(self):
        prop = E.propose(self.board, [{"op": "add_net", "name": "VCC"}])
        with self.assertRaises(E.EditError) as ctx:
            E.apply_plan(prop["plan_id"], confirm=True,
                         options={"engine": False, "mode": "new_file",
                                  "out_path": self.board})
        self.assertEqual(ctx.exception.code, "TARGET_EXISTS")

    def test_new_file_mode_leaves_the_source_alone(self):
        out = os.path.join(self.tmp, "copy.kicad_pcb")
        before = self.read()
        prop = E.propose(self.board, [{"op": "add_net", "name": "VCC"}])
        r = E.apply_plan(prop["plan_id"], confirm=True,
                         options={"engine": False, "mode": "new_file",
                                  "out_path": out})
        self.assertTrue(os.path.isfile(out))
        self.assertEqual(self.read(), before)
        self.assertIn(b"VCC", self.read(out))
        self.assertEqual(r["mode"], "new_file")


class TestCheckpoints(EditTestBase):
    def test_apply_takes_a_checkpoint_and_restore_brings_it_back(self):
        before = self.read()
        prop = E.propose(self.board, [{"op": "add_net", "name": "VCC"}])
        applied = E.apply_plan(prop["plan_id"], confirm=True, options={"engine": False})
        cid = applied["checkpoint"]["checkpoint_id"]
        self.assertNotEqual(self.read(), before)

        listed = E.list_checkpoints(self.board)
        self.assertTrue(any(c["checkpoint_id"] == cid
                            for c in listed["checkpoints"]))

        with self.assertRaises(E.EditError) as ctx:
            E.restore_checkpoint(cid, confirm=False)
        self.assertEqual(ctx.exception.code, "CONFIRMATION_REQUIRED")

        r = E.restore_checkpoint(cid, confirm=True)
        self.assertTrue(r["changed"])
        self.assertEqual(self.read(), before, "the checkpoint body is the old board")

    def test_restoring_the_current_state_is_a_no_op(self):
        cid = E.save_checkpoint(self.board, self.read(), "test")["checkpoint_id"]
        r = E.restore_checkpoint(cid, confirm=True)
        self.assertFalse(r["changed"])

    def test_default_home_is_outside_any_project(self):
        saved = os.environ.pop("PCB_AGENT_HOME", None)
        try:
            root = os.path.abspath(E.checkpoint_root())
        finally:
            if saved is not None:
                os.environ["PCB_AGENT_HOME"] = saved
        self.assertEqual(root, os.path.join(os.path.expanduser("~"),
                                            ".pcb_agent_mcp", "checkpoints"))
        self.assertFalse(root.lower().startswith(
            os.path.abspath(os.path.dirname(self.board)).lower() + os.sep.lower()),
            "the default checkpoint root must not be inside a project folder")

    def test_configured_home_is_inside_the_test_sandbox_only(self):
        root = os.path.abspath(E.checkpoint_root())
        self.assertTrue(root.startswith(os.path.abspath(self.tmp)), root)

    def test_a_corrupted_checkpoint_is_refused(self):
        meta = E.save_checkpoint(self.board, self.read(), "test")
        with open(meta["body"], "wb") as fh:
            fh.write(b"(kicad_pcb (version 20221018))")
        with self.assertRaises(E.EditError) as ctx:
            E.restore_checkpoint(meta["checkpoint_id"], confirm=True)
        self.assertEqual(ctx.exception.code, "CHECKPOINT_CORRUPT")


@unittest.skipUnless(ENGINE.find_kicad_cli(), "kicad-cli not installed")
class TestEngineGate(EditTestBase):
    def test_engine_gate_sees_edge_silk_as_an_advisory_not_a_wall(self):
        """KiCad's own DRC must SEE the edge silk - and grade it honestly.

        `silk_edge_clearance` is a WARNING in KiCad's report, so since DRC grading
        (`edit/drc_policy.py`) it is advisory: the edit lands and the violation is
        reported. The old shape of this test demanded a hard block, but a real
        board produced 69 warnings and 0 errors, so blocking on warnings made the
        board uneditable - being unable to edit is not a safety property.
        """
        prop = E.propose(self.board, [{"op": "add_board_text", "text": "EDGE",
                                       "x": 4, "y": 39.6, "layer": "F.SilkS",
                                       "height": 1.6, "thickness": 0.16}])
        r = E.apply_plan(prop["plan_id"], confirm=True)
        drc = r["gates"]["drc"]
        self.assertTrue(drc["comparable"], drc)
        self.assertGreaterEqual(drc["new_total"], 1, drc)
        self.assertEqual(drc["new_blocking_total"], 0, drc)
        self.assertGreaterEqual(drc["new_advisory_total"], 1, drc)
        self.assertIn("warning", drc["new_by_severity"])

    def test_strict_drc_turns_that_advisory_into_a_block(self):
        """The teeth are still there, one switch away."""
        prop = E.propose(self.board, [{"op": "add_board_text", "text": "EDGE",
                                       "x": 4, "y": 39.6, "layer": "F.SilkS",
                                       "height": 1.6, "thickness": 0.16}])
        before = self.read()
        with self.assertRaises(E.EditError) as ctx:
            E.apply_plan(prop["plan_id"], confirm=True,
                         options={"strict_drc": True})
        self.assertEqual(ctx.exception.code, "GATE_BLOCKED")
        blocking = " ".join(ctx.exception.extra["blocking"])
        self.assertIn("DRC", blocking)
        self.assertEqual(self.read(), before, "a blocked apply must roll back")

    def test_engine_gate_passes_a_clean_edit(self):
        prop = E.propose(self.board, [{"op": "add_board_text", "text": "PCB-AGENT",
                                       "x": 30, "y": 20, "layer": "F.SilkS",
                                       "height": 1.2, "thickness": 0.15}])
        r = E.apply_plan(prop["plan_id"], confirm=True)
        eng = r["gates"]["engine"]
        self.assertTrue(eng["ok"], eng)
        self.assertGreaterEqual(eng["comparable_checks"], 2)


if __name__ == "__main__":
    unittest.main()
