"""Tests for the LCEDA Pro (`.epro`) write path.

These run against a SYNTHETIC archive on purpose: no real `.epro` was on disk
when the writer was written, and pretending otherwise would be the exact kind of
false green this project exists to avoid. What can be tested without a real file
is tested hard:

* the log is only ever **appended** to (the old bytes are a prefix of the new);
* the **ticket** rises above every ticket already in the member (a smaller one is
  ignored by the application in silence - no error, no change);
* **every other ZIP member** stays byte-identical (a rebuilt `.ecop` would change
  the copper-pour fill while still looking valid);
* a **key name the file never showed** is refused, because EasyEDA Pro ignores an
  unknown key silently;
* `unit_verified` is reported as false, never as a pass.
"""
import io
import json
import os
import shutil
import tempfile
import unittest
import zipfile

from pcb_agent_mcp.edit import epro_edit as X
from pcb_agent_mcp.edit import plan as P
from pcb_agent_mcp.fidelity.epro_doc import EproDocument

def epro_bytes():
    """Same shape as the fidelity sample: NET, COMPONENT, PAD, a deleted VIA."""
    lines_pcb = [
        '{"type":"NET","id":"n1","ticket":1}||{"netName":"GND"}',
        '{"type":"NET","id":"n1","ticket":2}||{"netName":"GND","color":"#ff0000"}',
        '{"type":"COMPONENT","id":"c1","ticket":1}||{"designator":"R1","x":100,"y":200}',
        '{"type":"COMPONENT","id":"c1","ticket":2}||{"designator":"R1","x":120,"y":200}',
        '{"type":"PAD","id":"p1","ticket":1}||{"parentId":"c1","number":"1","x":110,"y":200}',
        '{"type":"VIA","id":"v1","ticket":2}||{}',
        'not a json line at all',
    ]
    buf = io.BytesIO()
    with zipfile.ZipFile(buf, "w", zipfile.ZIP_DEFLATED) as zf:
        zf.writestr("project.json", json.dumps({"name": "demo"}))
        zf.writestr("demo.epcb", "\n".join(lines_pcb) + "\n")
        zf.writestr("demo.eblob", b"\x00\x01\x02binary")
    return buf.getvalue()

class EproTestBase(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.mkdtemp(prefix="pcbepro-")
        self._home = os.environ.get("PCB_AGENT_HOME")
        os.environ["PCB_AGENT_HOME"] = os.path.join(self.tmp, "home")
        self.path = os.path.join(self.tmp, "demo.epro")
        self.original = epro_bytes()
        with open(self.path, "wb") as fh:
            fh.write(self.original)

    def tearDown(self):
        if self._home is None:
            os.environ.pop("PCB_AGENT_HOME", None)
        else:
            os.environ["PCB_AGENT_HOME"] = self._home
        P.PLANS.clear()
        shutil.rmtree(self.tmp, ignore_errors=True)

    def read(self):
        with open(self.path, "rb") as fh:
            return fh.read()

    def doc(self):
        return EproDocument.from_bytes(self.read(), self.path)

class TestEproPropose(EproTestBase):
    def test_propose_writes_nothing(self):
        prop = X.propose(self.path, [
            {"op": "set_fields", "target": {"type": "COMPONENT", "id": "c1"},
             "fields": {"x": 140}}])
        self.assertEqual(self.read(), self.original)
        self.assertEqual(prop["format"], "epro")
        self.assertEqual(prop["member"], "demo.epcb")
        self.assertEqual(prop["blocking"], [])

    def test_ticket_rises_above_every_ticket_in_the_member(self):
        prop = X.propose(self.path, [
            {"op": "set_fields", "target": {"type": "COMPONENT", "id": "c1"},
             "fields": {"x": 140}},
            {"op": "add_object", "type": "NET", "id": "n2",
             "fields": {"netName": "VCC"}}])
        # the member's highest ticket is 2, so the two new lines take 3 and 4
        self.assertEqual(prop["ticket"]["from"], 2)
        self.assertEqual([r["ticket"] for r in prop["operations"]], [3, 4])

    def test_unknown_key_is_refused_with_the_keys_that_do_exist(self):
        with self.assertRaises(X.EditError) as ctx:
            X.propose(self.path, [
                {"op": "set_fields", "target": {"type": "COMPONENT", "id": "c1"},
                 "fields": {"xPos": 140}}])
        self.assertEqual(ctx.exception.code, "KEY_NOT_IN_FILE")
        self.assertIn("designator", ctx.exception.extra["known"])

    def test_unknown_type_is_refused(self):
        with self.assertRaises(X.EditError) as ctx:
            X.propose(self.path, [
                {"op": "add_object", "type": "NO_SUCH_TYPE", "fields": {"a": 1}}])
        self.assertEqual(ctx.exception.code, "UNKNOWN_OBJECT_TYPE")

    def test_allow_new_keys_turns_the_refusal_into_a_warning(self):
        prop = X.propose(self.path, [
            {"op": "set_fields", "target": {"type": "COMPONENT", "id": "c1"},
             "fields": {"xPos": 140}, "allow_new_keys": True}])
        self.assertTrue(prop["warnings"])
        self.assertEqual(prop["blocking"], [])

    def test_missing_object_and_deleted_object_are_refused(self):
        with self.assertRaises(X.EditError) as ctx:
            X.propose(self.path, [
                {"op": "set_fields", "target": {"type": "COMPONENT", "id": "nope"},
                 "fields": {"x": 1}}])
        self.assertEqual(ctx.exception.code, "OBJECT_NOT_FOUND")
        with self.assertRaises(X.EditError) as ctx:
            X.propose(self.path, [
                {"op": "set_fields", "target": {"type": "VIA", "id": "v1"},
                 "fields": {"x": 1}}])
        self.assertEqual(ctx.exception.code, "OBJECT_DELETED")

    def test_add_existing_object_is_refused(self):
        with self.assertRaises(X.EditError) as ctx:
            X.propose(self.path, [
                {"op": "add_object", "type": "COMPONENT", "id": "c1",
                 "fields": {"designator": "R2"}}])
        self.assertEqual(ctx.exception.code, "OBJECT_EXISTS")

    def test_empty_plan_and_wrong_format_are_refused(self):
        with self.assertRaises(X.EditError) as ctx:
            X.propose(self.path, [])
        self.assertEqual(ctx.exception.code, "ARG_MISSING")
        plain = os.path.join(self.tmp, "b.kicad_pcb")
        with open(plain, "w", encoding="utf-8") as fh:
            fh.write("(kicad_pcb (version 20221018))")
        with self.assertRaises(X.EditError) as ctx:
            X.propose(plain, [{"op": "add_object", "type": "NET",
                               "fields": {"netName": "GND"}}])
        self.assertEqual(ctx.exception.code, "FORMAT_NOT_SUPPORTED")

class TestEproApply(EproTestBase):
    def test_apply_appends_and_never_rewrites(self):
        prop = X.propose(self.path, [
            {"op": "set_fields", "target": {"type": "COMPONENT", "id": "c1"},
             "fields": {"x": 140}},
            {"op": "add_object", "type": "PAD", "id": "p2",
             "fields": {"parentId": "c1", "number": "2", "x": 130, "y": 200}},
            {"op": "delete_object", "type": "NET", "id": "n1"}])
        applied = X.apply_plan(prop["plan_id"], confirm=True)
        self.assertEqual(applied["status"], "ok")
        self.assertEqual(applied["gates_passed"],
                         ["append_only", "bytes_written", "log_idempotent",
                          "members_untouched", "postconditions"])
        self.assertFalse(applied["unit_verified"])

        doc = self.doc()
        # the old bytes are a prefix: nothing was rewritten, only appended
        before = EproDocument.from_bytes(self.original)
        self.assertTrue(doc.entry_bytes("demo.epcb").startswith(
            before.entry_bytes("demo.epcb")))
        state = doc.resolved("demo.epcb")
        self.assertEqual(state[("COMPONENT", "c1")]["payload"]["x"], 140)
        self.assertEqual(state[("COMPONENT", "c1")]["payload"]["designator"], "R1")
        self.assertEqual(state[("COMPONENT", "c1")]["ticket"], 3)
        self.assertEqual(state[("PAD", "p2")]["payload"],
                         {"parentId": "c1", "number": "2", "x": 130, "y": 200})
        self.assertTrue(state[("NET", "n1")]["deleted"])

    def test_other_members_stay_byte_identical(self):
        prop = X.propose(self.path, [
            {"op": "add_object", "type": "NET", "id": "n2",
             "fields": {"netName": "VCC"}}])
        X.apply_plan(prop["plan_id"], confirm=True)
        before = EproDocument.from_bytes(self.original)
        after = self.doc()
        for name in ("project.json", "demo.eblob"):
            self.assertEqual(before.entry_bytes(name), after.entry_bytes(name), name)
        self.assertEqual(after.names(), before.names())

    def test_confirm_is_required_and_the_plan_is_single_use(self):
        prop = X.propose(self.path, [
            {"op": "add_object", "type": "NET", "id": "n2",
             "fields": {"netName": "VCC"}}])
        with self.assertRaises(X.EditError) as ctx:
            X.apply_plan(prop["plan_id"])
        self.assertEqual(ctx.exception.code, "CONFIRMATION_REQUIRED")
        self.assertEqual(self.read(), self.original)
        X.apply_plan(prop["plan_id"], confirm=True)
        with self.assertRaises(X.EditError) as ctx:
            X.apply_plan(prop["plan_id"], confirm=True)
        self.assertEqual(ctx.exception.code, "PLAN_NOT_FOUND")

    def test_archive_changed_since_plan_is_refused(self):
        prop = X.propose(self.path, [
            {"op": "add_object", "type": "NET", "id": "n2",
             "fields": {"netName": "VCC"}}])
        with open(self.path, "ab") as fh:
            fh.write(b"\n")
        with self.assertRaises(X.EditError) as ctx:
            X.apply_plan(prop["plan_id"], confirm=True)
        self.assertEqual(ctx.exception.code, "BOARD_CHANGED_SINCE_PLAN")

    def test_new_file_mode_leaves_the_source_alone(self):
        out = os.path.join(self.tmp, "copy.epro")
        prop = X.propose(self.path, [
            {"op": "add_object", "type": "NET", "id": "n2",
             "fields": {"netName": "VCC"}}])
        X.apply_plan(prop["plan_id"], confirm=True,
                     options={"mode": "new_file", "out_path": out})
        self.assertEqual(self.read(), self.original)
        self.assertTrue(os.path.isfile(out))
        self.assertEqual(EproDocument.from_path(out).resolved("demo.epcb")[
            ("NET", "n2")]["payload"], {"netName": "VCC"})

    def test_rollback_restores_the_exact_bytes(self):
        prop = X.propose(self.path, [
            {"op": "add_object", "type": "NET", "id": "n2",
             "fields": {"netName": "VCC"}}])
        applied = X.apply_plan(prop["plan_id"], confirm=True)
        P.restore_checkpoint(applied["checkpoint"]["checkpoint_id"], confirm=True)
        self.assertEqual(self.read(), self.original)

class TestFormatDetection(unittest.TestCase):
    def test_detected_by_content_not_by_name(self):
        from pcb_agent_mcp import edit as E
        tmp = tempfile.mkdtemp(prefix="pcbfmt-")
        try:
            epro_with_pcb_name = os.path.join(tmp, "looks_like.kicad_pcb")
            with open(epro_with_pcb_name, "wb") as fh:
                fh.write(epro_bytes())
            self.assertEqual(E.detect_format(epro_with_pcb_name), "epro")
            kicad_with_epro_name = os.path.join(tmp, "looks_like.epro")
            with open(kicad_with_epro_name, "w", encoding="utf-8") as fh:
                fh.write("(kicad_pcb (version 20221018))\n")
            self.assertEqual(E.detect_format(kicad_with_epro_name), "kicad")
        finally:
            shutil.rmtree(tmp, ignore_errors=True)

if __name__ == "__main__":
    unittest.main()
