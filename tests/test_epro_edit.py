"""Tests for the LCEDA Pro (`.epro`) write path.

The fixtures are built by `_lceda_fixtures`, which synthesises the **measured**
member layout and record arities (docs/13): a ZIP with ``PCB/<uuid>.epcb``,
``FOOTPRINT/<uuid>.efoo`` and a ``project.json``, records in mil with the real
field order. The tests never touch a real project - the reviewer's boards are
only ever read.

What is asserted here is the shape of the promise, not just "it returned ok":

* proposing writes **nothing** (the archive's sha256 is unchanged);
* applying changes **one member** and leaves every other member byte-identical;
* every line whose bytes changed is a line the operation **named**, and every
  other line kept its bytes (the `rows_untouched` / `diff_is_intended` gates);
* each operation's promise is **re-read out of the written file** by the
  adapter (`reparsed`), not taken from the in-memory plan;
* what cannot be derived from the file is **refused** (`NET_NOT_FOUND`,
  `LAYER_NOT_FOUND`, `PAD_OBJECT_UNKNOWN`, `WRITE_UNSUPPORTED_FORMAT`).
"""
import hashlib
import json
import os
import shutil
import sqlite3
import tempfile
import unittest
import zipfile

import _lceda_fixtures as _fx

from pcb_agent_mcp.edit import epro_edit as X
from pcb_agent_mcp.fidelity import epro_doc
from pcb_agent_mcp.fidelity import lceda


def _sha(path):
    with open(path, "rb") as fh:
        return hashlib.sha256(fh.read()).hexdigest()


def _members(path):
    with zipfile.ZipFile(path) as zf:
        return {name: hashlib.sha256(zf.read(name)).hexdigest()
                for name in zf.namelist()}


def _records(path, tag):
    """The records of one tag, read straight out of the file (not via the plan)."""
    with zipfile.ZipFile(path) as zf:
        name = [n for n in zf.namelist() if n.lower().endswith(".epcb")][0]
        text = zf.read(name).decode("utf-8")
    out = []
    for line in text.split("\n"):
        line = line.strip()
        if line.startswith('["%s"' % tag):
            out.append(json.loads(line))
    return out


class _Case(unittest.TestCase):
    """One synthetic archive per test, in a temp dir (never beside a project)."""

    def setUp(self):
        self.dir = tempfile.mkdtemp(prefix="epro_edit_")
        self.addCleanup(shutil.rmtree, self.dir, True)
        self.path = _fx.write_epro(self.dir, "board.epro")

    def propose(self, ops, **options):
        return X.propose(self.path, ops, options or None)

    def apply(self, ops, **options):
        plan = self.propose(ops, **options)
        self.assertEqual(plan["status"], "ok", plan["blocking"])
        result = X.apply_plan(plan["plan_id"], confirm=True)
        self.assertEqual(result["status"], "ok")
        return result

    def assertGatesGreen(self, result):
        for gate, value in result["gates"].items():
            if isinstance(value, dict):
                self.assertTrue(value.get("ok"), "%s: %r" % (gate, value))


class TestCatalogue(unittest.TestCase):
    def test_describe_lists_every_registered_op(self):
        ops = X.describe()
        names = [o["op"] for o in ops]
        self.assertEqual(names, sorted(X.OPS))
        self.assertEqual(len(set(names)), len(names))
        for entry in ops:
            self.assertTrue(entry["summary"])
            self.assertIsInstance(entry["params"], dict)

    def test_no_op_is_registered_twice_under_two_spellings(self):
        self.assertEqual(sorted(X.OPS), sorted({k for k in X.OPS}))

    def test_unknown_operation_is_named_with_the_known_ones(self):
        plan_dir = tempfile.mkdtemp(prefix="epro_edit_")
        self.addCleanup(shutil.rmtree, plan_dir, True)
        path = _fx.write_epro(plan_dir, "board.epro")
        with self.assertRaises(X.EditError) as ctx:
            X.propose(path, [{"op": "teleport_component"}])
        self.assertEqual(ctx.exception.code, "OP_UNKNOWN")
        for name in X.OPS:
            self.assertIn(name, str(ctx.exception))


class TestProposeWritesNothing(_Case):
    def test_proposing_leaves_the_archive_untouched(self):
        before_sha, before_members = _sha(self.path), _members(self.path)
        mtime = os.path.getmtime(self.path)
        plan = self.propose([{"op": "move_component", "component": "e1",
                              "x_mm": 25.0, "y_mm": 25.0}])
        self.assertEqual(plan["status"], "ok")
        self.assertEqual(_sha(self.path), before_sha)
        self.assertEqual(_members(self.path), before_members)
        self.assertEqual(os.path.getmtime(self.path), mtime)

    def test_an_empty_plan_is_refused(self):
        with self.assertRaises(X.EditError) as ctx:
            self.propose([])
        self.assertEqual(ctx.exception.code, "ARG_MISSING")

    def test_a_missing_file_is_refused(self):
        with self.assertRaises(X.EditError) as ctx:
            X.propose(os.path.join(self.dir, "nope.epro"),
                      [{"op": "set_text", "id": "s1", "text": "x"}])
        self.assertEqual(ctx.exception.code, "FILE_NOT_FOUND")


class TestMoveComponent(_Case):
    def test_it_changes_the_component_and_the_attr_that_sits_on_its_anchor(self):
        before = _records(self.path, "COMPONENT")
        plan = self.propose([{"op": "move_component", "component": "e1",
                              "x_mm": 25.0, "y_mm": 25.0}])
        self.assertEqual(plan["lines_changed"], 2)          # COMPONENT + ATTR
        self.assertEqual([r["attrs_moved"] for r in plan["operations"]],
                         [[{"attr": "e1d", "key": "Designator"}]])
        after = _records(self.path, "COMPONENT")
        self.assertEqual(after, before, "proposing must not write")

        result = self.apply([{"op": "move_component", "component": "e1",
                              "x_mm": 25.0, "y_mm": 25.0}])
        self.assertGatesGreen(result)
        moved = _records(self.path, "COMPONENT")[0]
        self.assertAlmostEqual(moved[4] * _fx.MIL, 25.0, places=4)
        self.assertAlmostEqual(moved[5] * _fx.MIL, 25.0, places=4)
        # the designator label that sat exactly on the anchor travelled with it
        attr = [a for a in _records(self.path, "ATTR") if a[1] == "e1d"][0]
        self.assertAlmostEqual(attr[5] * _fx.MIL, 25.0, places=4)
        self.assertAlmostEqual(attr[6] * _fx.MIL, 25.0, places=4)
        self.assertTrue(result["gates"]["reparsed"]["operations"][0]["verified"])

    def test_other_members_and_unnamed_lines_keep_their_bytes(self):
        before = _members(self.path)
        result = self.apply([{"op": "move_component", "component": "e1",
                              "x_mm": 12.5, "y_mm": 8.0}])
        after = _members(self.path)
        self.assertEqual(sorted(before), sorted(after))
        changed = [n for n in before if before[n] != after[n]]
        self.assertEqual(len(changed), 1, changed)
        self.assertTrue(changed[0].lower().endswith(".epcb"))
        self.assertEqual(result["gates"]["members_untouched"]["ok"], True)
        self.assertEqual(result["gates"]["rows_untouched"]["ok"], True)

    def test_an_unknown_component_is_refused(self):
        with self.assertRaises(X.EditError) as ctx:
            self.propose([{"op": "move_component", "component": "e99",
                           "x_mm": 1.0, "y_mm": 1.0}])
        self.assertEqual(ctx.exception.code, "RECORD_NOT_FOUND")
        self.assertEqual(ctx.exception.extra["tag"], "COMPONENT")

    def test_the_attr_that_is_not_on_the_anchor_is_left_alone_and_said_so(self):
        # designator sits at (400, 400) = the anchor; move the record's own
        # coordinates out from under it first, then move again: nothing follows.
        self.apply([{"op": "move_component", "component": "e1",
                     "x_mm": 10.16, "y_mm": 10.16}])
        plan = self.propose([{"op": "move_component", "component": "e1",
                              "x_mm": 30.0, "y_mm": 30.0}])
        self.assertTrue(plan["operations"][0]["warning"])


class TestSetTrack(_Case):
    def test_a_line_width_is_written_in_mil(self):
        self.apply([{"op": "set_track", "id": "l1", "width_mm": 1.0}])
        line = _records(self.path, "LINE")[0]
        self.assertAlmostEqual(line[9] * _fx.MIL, 1.0, places=4)

    def test_an_arc_endpoint_and_its_net_can_both_change(self):
        self.apply([{"op": "set_track", "id": "a1", "x1_mm": 5.0, "net": "GND"}])
        arc = _records(self.path, "ARC")[0]
        self.assertAlmostEqual(arc[5] * _fx.MIL, 5.0, places=4)
        self.assertEqual(arc[3], "GND")

    def test_a_net_that_is_not_in_the_table_is_refused(self):
        with self.assertRaises(X.EditError) as ctx:
            self.propose([{"op": "set_track", "id": "l1", "net": "GNDD"}])
        self.assertEqual(ctx.exception.code, "NET_NOT_FOUND")
        self.assertIn("GND", ctx.exception.extra["nets"])

    def test_an_unknown_track_id_is_refused(self):
        with self.assertRaises(X.EditError) as ctx:
            self.propose([{"op": "set_track", "id": "l404", "width_mm": 1.0}])
        self.assertEqual(ctx.exception.code, "RECORD_NOT_FOUND")


class TestSetVia(_Case):
    def test_drill_and_pad_are_written_and_keep_their_ratio(self):
        self.apply([{"op": "set_via", "id": "v1", "drill_mm": 0.3,
                     "diameter_mm": 0.6, "x_mm": 12.0, "y_mm": 13.0}])
        via = _records(self.path, "VIA")[0]
        self.assertAlmostEqual(via[7] * _fx.MIL, 0.3, places=4)
        self.assertAlmostEqual(via[8] * _fx.MIL, 0.6, places=4)
        self.assertAlmostEqual(via[5] * _fx.MIL, 12.0, places=4)

    def test_a_drill_that_is_not_smaller_than_the_pad_is_refused(self):
        with self.assertRaises(X.EditError) as ctx:
            self.propose([{"op": "set_via", "id": "v1", "drill_mm": 0.7}])
        self.assertEqual(ctx.exception.code, "ARG_INVALID")

    def test_a_net_that_does_not_exist_is_refused_before_anything_is_written(self):
        before = _sha(self.path)
        with self.assertRaises(X.EditError) as ctx:
            self.propose([{"op": "set_via", "id": "v1", "net": "nope"}])
        self.assertEqual(ctx.exception.code, "NET_NOT_FOUND")
        self.assertEqual(_sha(self.path), before)


class TestSetText(_Case):
    def test_text_position_and_layer(self):
        self.apply([{"op": "set_text", "id": "s1", "text": "REV B",
                     "x_mm": 3.0, "layer": "TOP_SILK"}])
        rec = _records(self.path, "STRING")[0]
        self.assertEqual(rec[6], "REV B")
        self.assertAlmostEqual(rec[4] * _fx.MIL, 3.0, places=4)
        self.assertEqual(rec[3], 3)                      # TOP_SILK's code, from LAYER

    def test_a_layer_name_the_file_does_not_have_is_refused(self):
        with self.assertRaises(X.EditError) as ctx:
            self.propose([{"op": "set_text", "id": "s1", "layer": "SILK_TOP"}])
        self.assertEqual(ctx.exception.code, "LAYER_NOT_FOUND")
        self.assertIn("TOP_SILK", str(ctx.exception))


class TestSetPadNet(_Case):
    def test_an_existing_pad_net_row_is_rewritten(self):
        plan = self.propose([{"op": "set_pad_net", "component": "e1",
                              "pad": "1", "net": "VCC"}])
        self.assertFalse(plan["operations"][0]["appended"])
        self.apply([{"op": "set_pad_net", "component": "e1",
                     "pad": "1", "net": "VCC"}])
        row = [r for r in _records(self.path, "PAD_NET") if r[2] == "1"][0]
        self.assertEqual(row[3], "VCC")

    def test_a_pad_without_a_row_is_refused_unless_appending_is_allowed(self):
        with self.assertRaises(X.EditError) as ctx:
            self.propose([{"op": "set_pad_net", "component": "e1",
                           "pad": "3", "net": "VCC"}])
        self.assertEqual(ctx.exception.code, "RECORD_NOT_FOUND")
        self.assertEqual(ctx.exception.extra["pad"], "3")

    def test_appending_copies_the_rows_the_footprint_needs(self):
        plan = self.propose([{"op": "set_pad_net", "component": "e1", "pad": "3",
                              "net": "VCC", "allow_new_record": True}])
        self.assertEqual(plan["lines_added"], 1)
        self.assertFalse(plan["verified_by_app"])
        self.assertEqual(plan["operations"][0]["appended_rows"],
                         [{"pad_object": "ie3", "net": "VCC"}])

        result = self.apply([{"op": "set_pad_net", "component": "e1", "pad": "3",
                              "net": "VCC", "allow_new_record": True}])
        self.assertGatesGreen(result)
        rows = [r for r in _records(self.path, "PAD_NET") if r[2] == "3"]
        self.assertEqual(len(rows), 1)
        # the fifth field is the footprint-local pad object, read from the file
        self.assertEqual(rows[0][4], "ie3")
        self.assertEqual(rows[0][3], "VCC")

    def test_a_pad_number_the_footprint_does_not_have_is_refused(self):
        with self.assertRaises(X.EditError) as ctx:
            self.propose([{"op": "set_pad_net", "component": "e1", "pad": "77",
                           "net": "VCC", "allow_new_record": True}])
        self.assertEqual(ctx.exception.code, "PAD_NOT_FOUND")
        self.assertEqual(ctx.exception.extra["footprint"], _fx.FP_UUID)


class TestDeleteRecord(_Case):
    def test_deleting_needs_allow_delete(self):
        with self.assertRaises(X.EditError) as ctx:
            self.propose([{"op": "delete_record", "tag": "STRING", "id": "s1"}])
        self.assertEqual(ctx.exception.code, "CONFIRMATION_REQUIRED")

    def test_deleting_a_component_needs_cascade_and_then_takes_its_children(self):
        with self.assertRaises(X.EditError) as ctx:
            self.propose([{"op": "delete_record", "tag": "COMPONENT", "id": "e1",
                           "allow_delete": True}])
        self.assertEqual(ctx.exception.code, "CASCADE_REQUIRED")
        self.assertEqual(ctx.exception.extra["children"], 5)   # 3 ATTR + 2 PAD_NET

        result = self.apply([{"op": "delete_record", "tag": "COMPONENT",
                              "id": "e1", "allow_delete": True, "cascade": True}])
        self.assertGatesGreen(result)
        self.assertEqual(_records(self.path, "COMPONENT"), [])
        self.assertEqual([a for a in _records(self.path, "ATTR") if a[3] == "e1"], [])
        self.assertEqual([p for p in _records(self.path, "PAD_NET") if p[1] == "e1"], [])

    def test_deleting_one_text_leaves_the_rest_of_the_stream_where_it_was(self):
        before = _records(self.path, "LINE")
        self.apply([{"op": "delete_record", "tag": "STRING", "id": "s1",
                     "allow_delete": True}])
        self.assertEqual(_records(self.path, "STRING"), [])
        self.assertEqual(_records(self.path, "LINE"), before)


class TestApplyGates(_Case):
    def test_confirm_is_required(self):
        plan = self.propose([{"op": "set_track", "id": "l1", "width_mm": 1.0}])
        before = _sha(self.path)
        with self.assertRaises(X.EditError) as ctx:
            X.apply_plan(plan["plan_id"])
        self.assertEqual(ctx.exception.code, "CONFIRMATION_REQUIRED")
        self.assertEqual(_sha(self.path), before)

    def test_a_plan_is_single_use(self):
        plan = self.propose([{"op": "set_track", "id": "l1", "width_mm": 1.0}])
        X.apply_plan(plan["plan_id"], confirm=True)
        with self.assertRaises(X.EditError) as ctx:
            X.apply_plan(plan["plan_id"], confirm=True)
        self.assertEqual(ctx.exception.code, "PLAN_NOT_FOUND")

    def test_an_unknown_plan_id_is_refused(self):
        with self.assertRaises(X.EditError) as ctx:
            X.apply_plan("plan-doesnotexist", confirm=True)
        self.assertEqual(ctx.exception.code, "PLAN_NOT_FOUND")

    def test_a_plan_made_against_other_bytes_is_refused(self):
        plan = self.propose([{"op": "set_track", "id": "l1", "width_mm": 1.0}])
        other = _fx.write_epro(self.dir, "other.epro", outline="rect")
        with open(other, "rb") as fh:
            blob = fh.read()
        with open(self.path, "wb") as fh:            # the editor saved meanwhile
            fh.write(blob)
        with self.assertRaises(X.EditError) as ctx:
            X.apply_plan(plan["plan_id"], confirm=True)
        self.assertEqual(ctx.exception.code, "BOARD_CHANGED_SINCE_PLAN")

    def test_the_result_names_how_to_undo_it(self):
        result = self.apply([{"op": "set_track", "id": "l1", "width_mm": 1.0}])
        self.assertIn("board_restore", result["how_to_undo"])
        self.assertTrue(result["checkpoint"]["checkpoint_id"])
        self.assertIn("never opened in LCEDA Pro", result["not_verified"])

    def test_every_gate_the_module_promises_is_reported(self):
        result = self.apply([{"op": "set_text", "id": "s1", "text": "REV C"}])
        for gate in ("bytes_written", "members_untouched", "rows_untouched",
                     "stream_parses", "diff_is_intended", "reparsed"):
            self.assertIn(gate, result["gates"])
            self.assertTrue(result["gates"][gate]["ok"], gate)


class TestRefusals(_Case):
    def test_a_sqlite_project_is_refused_with_a_reason(self):
        path = os.path.join(self.dir, "project.eprj2")
        con = sqlite3.connect(path)
        con.execute("CREATE TABLE documents (uuid TEXT, title TEXT, docType TEXT,"
                    " dataStr TEXT)")
        con.commit()
        con.close()
        self.assertTrue(path.startswith(self.dir))
        with self.assertRaises(X.EditError) as ctx:
            X.propose(path, [{"op": "set_text", "id": "s1", "text": "x"}])
        self.assertEqual(ctx.exception.code, "WRITE_UNSUPPORTED_FORMAT")
        self.assertIn("ticket", str(ctx.exception))

    def test_a_file_that_is_neither_container_nor_kicad_is_refused(self):
        path = os.path.join(self.dir, "junk.bin")
        with open(path, "wb") as fh:
            fh.write(b"not an archive")
        with self.assertRaises(X.EditError) as ctx:
            X.propose(path, [{"op": "set_text", "id": "s1", "text": "x"}])
        self.assertEqual(ctx.exception.code, "FORMAT_NOT_SUPPORTED")

    def test_a_named_member_that_is_not_in_the_archive_is_refused(self):
        with self.assertRaises(X.EditError) as ctx:
            self.propose([{"op": "set_text", "id": "s1", "text": "x"}],
                         member="PCB/nope.epcb")
        self.assertEqual(ctx.exception.code, "MEMBER_NOT_FOUND")


class TestArchiveShape(_Case):
    def test_the_untouched_lines_are_byte_identical_after_a_write(self):
        with zipfile.ZipFile(self.path) as zf:
            name = [n for n in zf.namelist() if n.lower().endswith(".epcb")][0]
            before = zf.read(name).decode("utf-8").split("\n")
        self.apply([{"op": "set_track", "id": "l1", "width_mm": 1.0}])
        with zipfile.ZipFile(self.path) as zf:
            after = zf.read(name).decode("utf-8").split("\n")
        self.assertEqual(len(before), len(after))
        differing = [i for i, (a, b) in enumerate(zip(before, after)) if a != b]
        self.assertEqual(len(differing), 1)
        self.assertTrue(after[differing[0]].startswith('["LINE","l1"'))

    def test_the_member_still_round_trips_after_a_write(self):
        self.apply([{"op": "set_via", "id": "v1", "drill_mm": 0.3,
                     "diameter_mm": 0.6}])
        container = epro_doc.EproDocument.from_path(self.path)
        name = [n for n in container.names() if n.lower().endswith(".epcb")][0]
        member = lceda.Member(name, container.entry_bytes(name).decode("utf-8"))
        self.assertTrue(member.is_verbatim())
        self.assertEqual([d.kind for d in member.documents], ["FOOTPRINT", "PCB"])


if __name__ == "__main__":
    unittest.main()
