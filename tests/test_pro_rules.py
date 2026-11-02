"""Net classes live in the `.kicad_pro`, so writing them means proving a JSON rewrite.

The five gates in `edit/pro_rules.py` each exist because of something that was
measured on a real project:

* KiCad writes projects with `indent=2`, **sorted keys** and **escaped non-ASCII**
  (`"démo"` is stored as `"d\\u00e9mo"`). Re-serialising a real 16,523-byte project
  with those settings is byte-identical; with `ensure_ascii=False` it differs by
  exactly one line. So "it round-trips" is a real check, and a file that does NOT
  round-trip is refused instead of being reformatted.
* `set_netclass` on the BOARD (edit/ops.py) edits `(net_class ...)` blocks that
  KiCad 7+ ignores. That is why this write path exists at all.
* A net may belong to several classes (`"GND": ["Fanout", "HV"]`), and a class with
  one owner is stored as a bare string. Writing a list where KiCad expects a string
  is the kind of change that opens fine and resolves wrong.
* `Default` is every unassigned net's fallback. Removing it is refused, not tried.
"""
import json
import os
import shutil
import tempfile
import unittest

from pcb_agent_mcp import edit as E
from pcb_agent_mcp import tools as TOOLS
from pcb_agent_mcp.adapters import kicad_pcb as A
from pcb_agent_mcp.edit import pro_rules as PR

# Written the way KiCad 10.0.7 writes a project: sorted keys at every level,
# 2-space indent, non-ASCII escaped, trailing newline. The point of a literal is
# that the style gate is checked against KiCad's shape, not against `dump()`.
PRO = (
    '{\n'
    '  "board": {\n'
    '    "design_settings": {\n'
    '      "rules": {\n'
    '        "min_clearance": 0.15\n'
    '      }\n'
    '    }\n'
    '  },\n'
    '  "meta": {\n'
    '    "filename": "d\\u00e9mo.kicad_pro",\n'
    '    "version": 3\n'
    '  },\n'
    '  "net_settings": {\n'
    '    "classes": [\n'
    '      {\n'
    '        "clearance": 0.15,\n'
    '        "name": "Default",\n'
    '        "track_width": 0.25,\n'
    '        "via_diameter": 0.6,\n'
    '        "via_drill": 0.3\n'
    '      },\n'
    '      {\n'
    '        "clearance": 0.2,\n'
    '        "name": "Fanout",\n'
    '        "track_width": 0.15,\n'
    '        "via_diameter": 0.45,\n'
    '        "via_drill": 0.2\n'
    '      }\n'
    '    ],\n'
    '    "meta": {\n'
    '      "version": 5\n'
    '    },\n'
    '    "netclass_assignments": {\n'
    '      "GND": "Fanout"\n'
    '    },\n'
    '    "netclass_patterns": []\n'
    '  },\n'
    '  "sheets": []\n'
    '}\n'
)


class ProBase(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.mkdtemp(prefix="pcbpro-")
        self._home = os.environ.get("PCB_AGENT_HOME")
        os.environ["PCB_AGENT_HOME"] = os.path.join(self.tmp, "home")
        self.pro = os.path.join(self.tmp, "demo.kicad_pro")
        with open(self.pro, "wb") as fh:
            fh.write(PRO.encode("utf-8"))
        self.board = os.path.join(self.tmp, "demo.kicad_pcb")

    def tearDown(self):
        if self._home is None:
            os.environ.pop("PCB_AGENT_HOME", None)
        else:
            os.environ["PCB_AGENT_HOME"] = self._home
        shutil.rmtree(self.tmp, ignore_errors=True)

    def raw(self):
        with open(self.pro, "rb") as fh:
            return fh.read()

    def data(self):
        return json.loads(self.raw().decode("utf-8"))


class TestTheStyleGateIsReal(ProBase):
    def test_the_fixture_is_in_kicads_own_style(self):
        """If this fails, the literal above drifted and every other test is moot."""
        self.assertEqual(PR.dump(self.data()), PRO.encode("utf-8"))

    def _write_unsorted(self):
        """The same document with the top-level keys in the wrong order.

        `sort_keys=False` on an already-sorted dict reproduces the same text, so
        the keys have to be genuinely permuted to look like a hand edit.
        """
        data = self.data()
        shuffled = {k: data[k] for k in reversed(list(data))}
        with open(self.pro, "wb") as fh:
            fh.write((json.dumps(shuffled, indent=2) + "\n").encode("utf-8"))

    def test_a_project_that_does_not_round_trip_is_refused_not_reformatted(self):
        self._write_unsorted()
        before = self.raw()
        with self.assertRaises(E.EditError) as ctx:
            E.write_project_rules(self.pro, [{"op": "set_netclass",
                                              "name": "HV", "clearance": 0.5}])
        self.assertEqual(ctx.exception.reason_code, "PRO_FORMAT_UNKNOWN")
        self.assertIn("reformat", str(ctx.exception))
        self.assertEqual(self.raw(), before, "a refused write must not touch the file")

    def test_the_read_tool_says_it_is_not_writable_and_why(self):
        self._write_unsorted()
        out = E.read_project_rules(self.pro)
        self.assertFalse(out["writable"])
        self.assertFalse(out["style_roundtrip"])
        self.assertIn("reformat", out["why_not_writable"])


class TestRead(ProBase):
    def test_classes_and_ownership_are_read_as_they_are(self):
        out = E.read_project_rules(self.pro)
        self.assertEqual(out["status"], "ok")
        names = [c["name"] for c in out["net_classes"]]
        self.assertEqual(names, ["Default", "Fanout"])
        fanout = out["net_classes"][1]
        self.assertEqual(fanout["clearance"], 0.2)
        self.assertEqual(fanout["track_width"], 0.15)
        self.assertEqual(fanout["nets"], 1)
        self.assertEqual(out["net_count"], 1)
        self.assertEqual(out["assignment_form"], "map")
        self.assertTrue(out["writable"])

    def test_the_board_path_finds_the_project_beside_it(self):
        out = E.read_project_rules(self.board)
        self.assertEqual(os.path.basename(out["project"]), "demo.kicad_pro")

    def test_no_project_is_a_refusal_with_the_reason(self):
        lonely = os.path.join(self.tmp, "lonely.kicad_pcb")
        with self.assertRaises(E.EditError) as ctx:
            E.read_project_rules(lonely)
        self.assertEqual(ctx.exception.reason_code, "FILE_NOT_FOUND")
        self.assertIn("net classes in the project file", str(ctx.exception))


class TestWrite(ProBase):
    def test_a_new_class_copies_default_and_says_what_it_inherited(self):
        before = json.loads(self.raw().decode("utf-8"))["net_settings"]["classes"]
        default = [c for c in before if c["name"] == "Default"][0]
        out = E.write_project_rules(self.pro, [
            {"op": "set_netclass", "name": "HV", "clearance": 0.5}])
        self.assertEqual(out["status"], "ok")
        op = out["operations"][0]
        self.assertTrue(op["created"])
        self.assertEqual(op["shape_copied_from"], "Default")
        self.assertIn("via_diameter", op["inherited"])
        self.assertNotIn("clearance", op["inherited"])
        # every key Default has is still on the new class: nothing was invented
        classes = {c["name"]: c for c in
                   self.data()["net_settings"]["classes"]}
        self.assertEqual(set(classes["HV"]), set(default))
        self.assertEqual(classes["HV"]["via_diameter"], default["via_diameter"])
        self.assertEqual(classes["HV"]["clearance"], 0.5)
        self.assertTrue(out["written"])
        self.assertIsNotNone(out["checkpoint"])

    def test_the_gates_are_all_reported_and_all_pass(self):
        out = E.write_project_rules(self.pro, [
            {"op": "set_netclass", "name": "HV", "clearance": 0.5, "track_width": 0.3},
            {"op": "assign_nets", "netclass": "HV", "nets": ["GND", "+3V3"]}])
        self.assertEqual(out["blocking"], [])
        for gate in ("format_roundtrip", "diff_is_intended", "bytes_written",
                     "json_roundtrip", "readback"):
            self.assertTrue(out["gates"][gate]["ok"], gate)
        self.assertEqual(out["gates"]["readback"]["reader"],
                         "adapters.kicad_pcb.load_project_rules")
        self.assertEqual(out["gates"]["bytes_written"]["sha256"], out["sha256_after"])
        # the diff gate's answer: only net_settings keys moved
        self.assertTrue(out["changed_keys"])
        for key in out["changed_keys"]:
            self.assertTrue(key.startswith("/net_settings/"), key)

    def test_a_net_in_two_classes_is_a_list_and_going_back_is_a_string(self):
        E.write_project_rules(self.pro, [
            {"op": "set_netclass", "name": "HV", "clearance": 0.5},
            {"op": "assign_nets", "netclass": "HV", "nets": ["GND"]}])
        own = self.data()["net_settings"]["netclass_assignments"]
        self.assertEqual(own["GND"], ["Fanout", "HV"])
        out = E.write_project_rules(self.pro, [
            {"op": "unassign_nets", "nets": ["GND"], "netclass": "HV"}])
        self.assertEqual(out["operations"][0]["changed"]["GND"]["after"], ["Fanout"])
        own = self.data()["net_settings"]["netclass_assignments"]
        self.assertEqual(own["GND"], "Fanout")

    def test_the_drc_reader_sees_the_new_clearance(self):
        E.write_project_rules(self.pro, [
            {"op": "set_netclass", "name": "Fanout", "clearance": 0.254}])
        rules = A.load_project_rules(self.pro)
        self.assertAlmostEqual(rules["net_classes"]["Fanout"]["clearance"], 0.254)

    def test_an_operation_that_changes_nothing_does_not_write(self):
        before = self.raw()
        out = E.write_project_rules(self.pro, [
            {"op": "set_netclass", "name": "Fanout", "clearance": 0.2}])
        self.assertFalse(out["written"])
        self.assertFalse(out["bytes_changed"])
        self.assertEqual(self.raw(), before)
        self.assertIn("already in the project", out["message"])

    def test_dry_run_runs_every_gate_on_a_copy_and_leaves_the_project_alone(self):
        before = self.raw()
        out = E.write_project_rules(self.pro, [
            {"op": "set_netclass", "name": "HV", "clearance": 0.5}], dry_run=True)
        self.assertTrue(out["dry_run"])
        self.assertFalse(out["written"])
        self.assertTrue(out["would_write"])
        self.assertTrue(out["file_unchanged"])
        self.assertIn("temporary copy",
                      out["gates"]["bytes_written"]["measured_on"])
        self.assertEqual(self.raw(), before)
        self.assertFalse(os.path.exists(self.pro + ".tmp"))
        leftovers = [n for n in os.listdir(self.tmp) if n.startswith(".pcbagent-pro-")]
        self.assertEqual(leftovers, [], "the temporary copy must be cleaned up")


class TestRefusals(ProBase):
    def test_default_cannot_be_removed(self):
        with self.assertRaises(E.EditError) as ctx:
            E.write_project_rules(self.pro, [{"op": "remove_netclass",
                                              "name": "Default"}])
        self.assertEqual(ctx.exception.reason_code, "CANNOT_REMOVE_DEFAULT")
        self.assertIn("fallback", str(ctx.exception))

    def test_a_class_still_in_use_is_refused_until_its_nets_move(self):
        with self.assertRaises(E.EditError) as ctx:
            E.write_project_rules(self.pro, [{"op": "remove_netclass",
                                              "name": "Fanout"}])
        self.assertEqual(ctx.exception.reason_code, "CLASS_STILL_IN_USE")
        self.assertIn("reassign_to", ctx.exception.hint or "")
        out = E.write_project_rules(self.pro, [{"op": "remove_netclass",
                                               "name": "Fanout",
                                               "reassign_to": "Default"}])
        self.assertEqual(out["operations"][0]["nets_moved"], ["GND"])
        self.assertEqual(self.data()["net_settings"]["netclass_assignments"]["GND"],
                         "Default")

    def test_a_pattern_naming_the_class_blocks_the_removal(self):
        data = self.data()
        data["net_settings"]["netclass_patterns"] = [
            {"netclass": "Fanout", "pattern": "W25_*"}]
        with open(self.pro, "wb") as fh:
            fh.write(PR.dump(data))
        with self.assertRaises(E.EditError) as ctx:
            E.write_project_rules(self.pro, [{"op": "remove_netclass",
                                              "name": "Fanout",
                                              "reassign_to": "Default"}])
        self.assertEqual(ctx.exception.reason_code, "PATTERN_REFERENCES_CLASS")

    def test_assigning_to_a_class_that_does_not_exist_is_refused(self):
        with self.assertRaises(E.EditError) as ctx:
            E.write_project_rules(self.pro, [{"op": "assign_nets",
                                              "netclass": "HV", "nets": ["GND"]}])
        self.assertEqual(ctx.exception.reason_code, "NO_SUCH_CLASS")
        self.assertIn("set_netclass", ctx.exception.hint or "")

    def test_a_misspelt_knob_is_named_not_ignored(self):
        with self.assertRaises(E.EditError) as ctx:
            E.write_project_rules(self.pro, [{"op": "set_netclass", "name": "Fanout",
                                              "clearance_mm": 0.3}])
        self.assertEqual(ctx.exception.reason_code, "UNKNOWN_KNOB")

    def test_a_knob_that_is_not_a_number_is_refused(self):
        with self.assertRaises(E.EditError) as ctx:
            E.write_project_rules(self.pro, [{"op": "set_netclass", "name": "Fanout",
                                              "clearance": "tight"}])
        self.assertEqual(ctx.exception.reason_code, "KNOB_NOT_A_NUMBER")

    def test_an_unknown_op_lists_the_ones_this_tool_writes(self):
        with self.assertRaises(E.EditError) as ctx:
            E.write_project_rules(self.pro, [{"op": "set_netclass_clearance"}])
        self.assertEqual(ctx.exception.reason_code, "UNKNOWN_OP")
        self.assertIn("assign_nets", str(ctx.exception))


class TestThroughCallTool(ProBase):
    def test_the_write_reply_is_top_level_and_the_error_keeps_its_hint(self):
        out = TOOLS.call_tool("board_rules_write",
                              {"path": self.pro,
                               "operations": [{"op": "set_netclass", "name": "HV",
                                               "clearance": 0.5}]})
        self.assertEqual(out["status"], "ok")
        self.assertEqual(out["payload_is"], "top_level")
        self.assertTrue(out["written"])

    def test_an_error_reply_carries_reason_code_and_hint(self):
        out = TOOLS.call_tool("board_rules_write",
                              {"path": self.pro,
                               "operations": [{"op": "remove_netclass",
                                               "name": "Fanout"}]})
        self.assertEqual(out["status"], "error")
        self.assertEqual(out["reason_code"], "CLASS_STILL_IN_USE")
        self.assertIn("reassign_to", out["hint"])

    def test_the_read_tool_is_advertised_and_answers(self):
        self.assertIn("board_rules", TOOLS.TOOLS)
        caps = TOOLS._capabilities({})
        self.assertIn("board_rules", caps["available"])
        self.assertIn("board_rules_write", caps["available"])
        out = TOOLS.call_tool("board_rules", {"path": self.pro})
        self.assertEqual(out["status"], "ok")
        self.assertEqual(len(out["net_classes"]), 2)


if __name__ == "__main__":
    unittest.main()
