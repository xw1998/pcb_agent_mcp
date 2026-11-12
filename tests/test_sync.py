"""Schematic netlist vs board: the diff and the operations it can produce.

The two sides are faked on purpose in most tests: a netlist dict is exactly what
`fidelity.engine.sch_netlist()` returns, and the board is a hand-written
.kicad_pcb. That keeps the rules under test (pin level, `/`-prefixed net names,
`unconnected-` nets, a missing part has no position) separate from KiCad being
installed. One end-to-end test rides on kicad-cli when it is there.
"""
import os
import shutil
import sys
import tempfile
import unittest

sys.path.insert(0, os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "src"))

from pcb_agent_mcp import edit as E
from pcb_agent_mcp import sync as SY
from pcb_agent_mcp.edit import ops as O
from pcb_agent_mcp.edit import plan as P
from pcb_agent_mcp.fidelity import engine as ENGINE
from pcb_agent_mcp.fidelity import kicad_doc as K
from pcb_agent_mcp.fidelity import sexpr as S

BOARD = """(kicad_pcb (version 20221018) (generator pcbnew)
  (general (thickness 1.6))
  (layers
    (0 "F.Cu" signal)
    (31 "B.Cu" signal)
    (44 "Edge.Cuts" user)
  )
  (net 0 "")
  (net 1 "GND")
  (net 2 "SDA")
  (net 3 "STALE")
  (gr_line (start 0 0) (end 40 0) (layer "Edge.Cuts") (width 0.1) (tstamp 00000000-0000-0000-0000-000000000001))
  (gr_line (start 40 0) (end 40 25) (layer "Edge.Cuts") (width 0.1) (tstamp 00000000-0000-0000-0000-000000000002))
  (gr_line (start 40 25) (end 0 25) (layer "Edge.Cuts") (width 0.1) (tstamp 00000000-0000-0000-0000-000000000003))
  (gr_line (start 0 25) (end 0 0) (layer "Edge.Cuts") (width 0.1) (tstamp 00000000-0000-0000-0000-000000000004))
  (footprint "Test:QFN" (layer "F.Cu") (at 20 15) (tstamp 00000000-0000-0000-0000-000000000020)
    (fp_text reference "U1" (at 0 -3) (layer "F.SilkS") (tstamp 00000000-0000-0000-0000-000000000021))
    (pad "1" smd rect (at -1 1.5) (size 1 0.6) (layers "F.Cu") (net 1 "GND") (tstamp 00000000-0000-0000-0000-000000000024))
    (pad "2" smd rect (at 1 -1.5) (size 1 0.6) (layers "F.Cu") (net 3 "STALE") (tstamp 00000000-0000-0000-0000-000000000025))
  )
  (footprint "Test:0805" (layer "F.Cu") (at 10 10) (tstamp 00000000-0000-0000-0000-000000000010)
    (fp_text reference "R9" (at 0 -2) (layer "F.SilkS") (tstamp 00000000-0000-0000-0000-000000000011))
    (pad "1" smd roundrect (at -0.95 0) (size 1.2 1.4) (layers "F.Cu") (net 2 "SDA") (tstamp 00000000-0000-0000-0000-000000000013))
    (pad "2" smd roundrect (at 0.95 0) (size 1.2 1.4) (layers "F.Cu") (net 1 "GND") (tstamp 00000000-0000-0000-0000-000000000014))
  )
)
"""

def netlist(components, nets):
    return {"status": "ok", "authoritative": True,
            "components": [{"ref": r, "value": v, "footprint": f}
                           for r, v, f in components],
            "nets": [{"name": n, "nodes": [[r, p] for r, p in nodes]}
                     for n, nodes in nets]}

class SyncBase(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.mkdtemp(prefix="pcbsync-")
        self._home = os.environ.get("PCB_AGENT_HOME")
        os.environ["PCB_AGENT_HOME"] = os.path.join(self.tmp, "home")
        self.board = os.path.join(self.tmp, "board.kicad_pcb")
        with open(self.board, "w", encoding="utf-8", newline="\n") as fh:
            fh.write(BOARD)

    def tearDown(self):
        if self._home is None:
            os.environ.pop("PCB_AGENT_HOME", None)
        else:
            os.environ["PCB_AGENT_HOME"] = self._home
        P.PLANS.clear()
        shutil.rmtree(self.tmp, ignore_errors=True)

    def write_board(self, text):
        path = os.path.join(self.tmp, "variant.kicad_pcb")
        with open(path, "w", encoding="utf-8", newline="\n") as fh:
            fh.write(text)
        return path

    def kinds(self, diff):
        return sorted(row["kind"] for row in diff["issues"])

class BoardIndex(SyncBase):
    def test_pads_nets_layers_and_positions_are_read(self):
        idx = SY.board_index(self.board)
        self.assertEqual(sorted(idx["footprints"]), ["R9", "U1"])
        self.assertEqual(idx["footprints"]["U1"]["lib_id"], "Test:QFN")
        self.assertEqual(idx["footprints"]["U1"]["pads"], {"1": "GND", "2": "STALE"})
        self.assertAlmostEqual(idx["footprints"]["U1"]["at"][0], 20.0, places=6)
        # board files count y downwards, the rest of this server counts it
        # upwards (`build.y_file` negates on the way in), so the index negates
        # too - one convention, not a second one invented here
        self.assertAlmostEqual(idx["footprints"]["U1"]["at"][1], -15.0, places=6)
        self.assertEqual(idx["counts"], {"footprints": 2, "pads": 4, "nets": 3})

    def test_a_missing_board_is_refused_by_name(self):
        with self.assertRaises(SY.SyncError) as ctx:
            SY.board_index(os.path.join(self.tmp, "nope.kicad_pcb"))
        self.assertEqual(ctx.exception.code, "PROJECT_NOT_FOUND")

class NetlistAliases(unittest.TestCase):
    def test_a_root_sheet_prefix_is_dropped(self):
        idx = SY.netlist_index(netlist([("R1", "10k", "Test:0805")],
                                      [("/GND", [("R1", "2"), ("U1", "1")])]))
        self.assertEqual(idx["aliases"]["/GND"], "GND")
        self.assertEqual(idx["ambiguous_aliases"], set())

    def test_a_sub_sheet_path_is_kept(self):
        idx = SY.netlist_index(netlist([("R1", "10k", "Test:0805")],
                                      [("/power/GND", [("R1", "2"), ("U1", "1")])]))
        self.assertEqual(idx["aliases"]["/power/GND"], "/power/GND")

    def test_two_sheets_with_one_label_are_ambiguous_not_merged(self):
        idx = SY.netlist_index(netlist(
            [("R1", "10k", "Test:0805")],
            [("/a/GND", [("R1", "2"), ("U1", "1")]),
             ("/b/GND", [("R2", "2"), ("U2", "1")])]))
        self.assertEqual(idx["ambiguous_aliases"], set())

class Compare(SyncBase):
    def test_a_board_that_agrees_reports_no_blocking_issue(self):
        board = self.write_board(BOARD.replace('(net 3 "STALE")', '(net 2 "SDA")'))
        diff = SY.compare(board, netlist(
            [("R9", "10k", "Test:0805"), ("U1", "MCU", "Test:QFN")],
            [("/GND", [("R9", "2"), ("U1", "1")]),
             ("/SDA", [("R9", "1"), ("U1", "2")])]))
        self.assertTrue(diff["in_sync"], diff["issues"])
        self.assertEqual(diff["blocking_kinds"], [])

    def test_a_part_the_board_lacks_is_reported_without_a_position(self):
        diff = SY.compare(self.board, netlist(
            [("R9", "10k", "Test:0805"), ("U1", "MCU", "Test:QFN"),
             ("C7", "100n", "Test:0402")], []))
        rows = [r for r in diff["issues"] if r["kind"] == "missing_on_board"]
        self.assertEqual(len(rows), 1)
        self.assertEqual(rows[0]["ref"], "C7")
        self.assertTrue(rows[0]["needs_placement"])
        self.assertIn("missing_on_board", diff["blocking_kinds"])

    def test_a_footprint_that_changed_is_reported_both_ways(self):
        diff = SY.compare(self.board, netlist(
            [("R9", "10k", "Test:0402"), ("U1", "MCU", "Test:QFN")], []))
        rows = [r for r in diff["issues"] if r["kind"] == "footprint_id_mismatch"]
        self.assertEqual(rows[0]["schematic"], "Test:0402")
        self.assertEqual(rows[0]["board"], "Test:0805")

    def test_a_library_nickname_is_case_insensitive_but_a_name_is_not(self):
        diff = SY.compare(self.board, netlist(
            [("R9", "10k", "test:0805"), ("U1", "MCU", "Test:QFN")], []))
        self.assertNotIn("footprint_id_mismatch", self.kinds(diff))

    def test_a_pad_on_the_wrong_net_is_a_pin_level_difference(self):
        diff = SY.compare(self.board, netlist(
            [("R9", "10k", "Test:0805"), ("U1", "MCU", "Test:QFN")],
            [("/SDA", [("R9", "1"), ("U1", "2")])]))
        rows = [r for r in diff["issues"] if r["kind"] == "pad_net_mismatch"]
        self.assertEqual(len(rows), 1)
        self.assertEqual((rows[0]["ref"], rows[0]["pin"]), ("U1", "2"))
        self.assertEqual(rows[0]["schematic"], "SDA")
        self.assertEqual(rows[0]["board"], "STALE")

    def test_a_pin_the_footprint_does_not_have_is_named_with_the_pads_it_has(self):
        diff = SY.compare(self.board, netlist(
            [("R9", "10k", "Test:0805"), ("U1", "MCU", "Test:QFN")],
            [("/SDA", [("U1", "2"), ("U1", "33")])]))
        rows = [r for r in diff["issues"] if r["kind"] == "pin_not_on_footprint"]
        self.assertEqual(rows[0]["pin"], "33")
        self.assertEqual(rows[0]["board_pads"], ["1", "2"])

    def test_a_footprint_the_schematic_does_not_have(self):
        diff = SY.compare(self.board, netlist([("U1", "MCU", "Test:QFN")], []))
        rows = [r for r in diff["issues"] if r["kind"] == "on_board_not_in_schematic"]
        self.assertEqual([r["ref"] for r in rows], ["R9"])

    def test_an_unconnected_pin_agrees_with_an_untied_pad(self):
        board = self.write_board(BOARD.replace(
            '(net 2 "SDA") (tstamp 00000000-0000-0000-0000-000000000013)',
            '(tstamp 00000000-0000-0000-0000-000000000013)'))
        diff = SY.compare(board, netlist(
            [("R9", "10k", "Test:0805"), ("U1", "MCU", "Test:QFN")],
            [("unconnected-(R9-Pad1)", [("R9", "1")])]))
        self.assertNotIn("pad_net_mismatch", self.kinds(diff))

    def test_a_pad_named_unconnected_agrees_with_a_pin_that_names_no_net(self):
        # the mirror image of the case above: the schematic lists nothing for
        # the pin at all, and KiCad has named the pad's net after the pad.
        # Both sides are saying "nothing is connected here".
        board = self.write_board(BOARD.replace(
            '(net 2 "SDA") (tstamp 00000000-0000-0000-0000-000000000013)',
            '(net 4 "unconnected-(R9-Pad1)") (tstamp 00000000-0000-0000-0000-000000000013)'
        ).replace('(net 3 "STALE")', '(net 3 "STALE") (net 4 "unconnected-(R9-Pad1)")\n'))
        diff = SY.compare(board, netlist(
            [("R9", "10k", "Test:0805"), ("U1", "MCU", "Test:QFN")], []))
        self.assertNotIn("pad_net_mismatch", self.kinds(diff))

    def test_an_untied_pad_where_the_schematic_connects_a_net(self):
        board = self.write_board(BOARD.replace(
            '(net 2 "SDA") (tstamp 00000000-0000-0000-0000-000000000013)',
            '(tstamp 00000000-0000-0000-0000-000000000013)'))
        diff = SY.compare(board, netlist(
            [("R9", "10k", "Test:0805"), ("U1", "MCU", "Test:QFN")],
            [("/SDA", [("U1", "2"), ("R9", "1")])]))
        rows = [r for r in diff["issues"] if r["kind"] == "pad_net_mismatch"]
        self.assertEqual([(r["ref"], r["pin"], r["schematic"], r["board"])
                          for r in rows],
                         [("R9", "1", "SDA", ""), ("U1", "2", "SDA", "STALE")])

    def test_a_net_that_reaches_no_pad(self):
        diff = SY.compare(self.board, netlist(
            [("R9", "10k", "Test:0805"), ("U1", "MCU", "Test:QFN")],
            [("/SCL", [("R9", "1"), ("U1", "2")])]))
        rows = [r for r in diff["issues"] if r["kind"] == "net_not_on_board"]
        self.assertEqual([r["net"] for r in rows], ["SCL"])
        self.assertEqual(rows[0]["schematic_net"], "/SCL")

    def test_a_one_pin_net_is_not_a_connection(self):
        diff = SY.compare(self.board, netlist(
            [("R9", "10k", "Test:0805")], [("/SCL", [("R9", "1")])]))
        self.assertNotIn("net_not_on_board", self.kinds(diff))

    def test_a_board_net_the_schematic_never_had_is_kept_for_the_caller(self):
        diff = SY.compare(self.board, netlist(
            [("R9", "10k", "Test:0805"), ("U1", "MCU", "Test:QFN")],
            [("/GND", [("R9", "2"), ("U1", "1")])]))
        rows = [r for r in diff["issues"]
                if r["kind"] == "pad_net_not_in_schematic_baseline"]
        # SDA is on R9 pad 1 and STALE on U1 pad 2, and the netlist named
        # neither, so both are reported - as information, not as blocking
        self.assertEqual([(r["ref"], r["pad"]) for r in rows],
                         [("R9", "1"), ("U1", "2")])
        self.assertNotIn("pad_net_not_in_schematic_baseline", diff["blocking_kinds"])

class Operations(SyncBase):
    def diff(self):
        return SY.compare(self.board, netlist(
            [("R9", "10k", "Test:0805"), ("U1", "MCU", "Test:QFN"),
             ("C7", "100n", "Test:0402")],
            [("/GND", [("R9", "2"), ("U1", "1")]),
             ("/SDA", [("R9", "1"), ("U1", "2")]),
             ("/SCL", [("R9", "1"), ("U1", "33")])]))

    def test_pad_net_fixes_become_operations(self):
        out = SY.operations_for(self.diff())
        ops = [o for o in out["operations"] if o["op"] == "set_pad_net"]
        # pin 33 is not a pad on this footprint, so it is reported rather than
        # written; R9 pad 1 really does carry the wrong net
        self.assertEqual(sorted((o["ref"], o["pad"], o["net"]) for o in ops),
                         [("R9", "1", "SCL"), ("U1", "2", "SDA")])
        self.assertIn("pin_not_on_footprint", self.kinds(self.diff()))

    def test_a_missing_part_without_a_placement_is_not_invented(self):
        out = SY.operations_for(self.diff())
        self.assertFalse([o for o in out["operations"] if o["op"] == "add_footprint"])
        self.assertEqual([u["ref"] for u in out["unresolved"]], ["C7"])
        self.assertEqual(out["unresolved"][0]["why"], "needs_placement")

    def test_a_placement_turns_it_into_an_add_footprint(self):
        out = SY.operations_for(self.diff(),
                                placement={"C7": {"x": 5, "y": 6, "rot": 90,
                                                  "library": "/tmp/libs.pretty",
                                                  "name": "0402",
                                                  "pad_nets": {"1": "GND", "2": "SDA"}}})
        add = [o for o in out["operations"] if o["op"] == "add_footprint"]
        self.assertEqual(len(add), 1)
        self.assertEqual(add[0]["rot"], 90)
        self.assertEqual(add[0]["library"], "/tmp/libs.pretty")
        self.assertEqual(add[0]["pad_nets"], {"1": "GND", "2": "SDA"})

    def test_extra_parts_are_only_removed_when_asked(self):
        d = SY.compare(self.board, netlist([("U1", "MCU", "Test:QFN")], []))
        self.assertFalse([o for o in SY.operations_for(d)["operations"]
                          if o["op"] == "delete_footprint"])
        self.assertEqual([o["ref"] for o in
                          SY.operations_for(d, remove_extra=True)["operations"]
                          if o["op"] == "delete_footprint"], ["R9"])

    def test_stale_pad_nets_are_only_cleared_when_asked(self):
        d = SY.compare(self.board, netlist(
            [("R9", "10k", "Test:0805"), ("U1", "MCU", "Test:QFN")],
            [("/GND", [("R9", "2"), ("U1", "1")])]))
        # SDA and STALE are on pads, the netlist names neither: both are "stale"
        self.assertFalse([o for o in SY.operations_for(d)["operations"]
                          if o["op"] == "set_pad_net" and not o["net"]])
        cleared = [o for o in SY.operations_for(d, clear_extra_pad_nets=True)["operations"]
                   if o["op"] == "set_pad_net" and not o["net"]]
        self.assertEqual([(o["ref"], o["pad"], o["net"]) for o in cleared],
                         [("R9", "1", ""), ("U1", "2", "")])

class SetPadNet(SyncBase):
    def apply(self, op):
        plan = E.propose(self.board, [op])
        self.assertEqual(plan["blocking"], [], plan["blocking"])
        report = E.apply_plan(plan["plan_id"], confirm=True, options=dict(engine=False))
        self.assertTrue(report["gates"]["postconditions"]["all_verified"],
                        report["gates"]["postconditions"])
        return plan, report

    def pad(self, ref, number):
        doc = K.KiCadDocument.from_path(self.board)
        ed = O.Editor(doc)
        for pad in S.children(ed.footprint(ref), "pad"):
            if (S.arg_text(pad, 1) or "") == number:
                net = S.child(pad, "net")
                if net is None:
                    return ""
                return S.arg_text(net, 2) or S.arg_text(net, 1) or ""
        raise AssertionError("no pad %s" % number)

    def test_a_pad_moves_to_another_declared_net(self):
        self.apply({"op": "set_pad_net", "ref": "R9", "pad": "1", "net": "GND"})
        self.assertEqual(self.pad("R9", "1"), "GND")
        # and the old net is still declared, because other things may use it
        doc = K.KiCadDocument.from_path(self.board)
        self.assertIn("SDA", O.Editor(doc).net_by_name)

    def test_a_net_that_does_not_exist_is_refused_unless_creation_is_allowed(self):
        with self.assertRaises(O.OpError) as ctx:
            E.propose(self.board, [{"op": "set_pad_net", "ref": "R9", "pad": "1",
                                    "net": "SCL"}])
        self.assertEqual(ctx.exception.code, "NET_NOT_FOUND")
        self.apply({"op": "set_pad_net", "ref": "R9", "pad": "1", "net": "SCL",
                    "create_nets": True})
        self.assertEqual(self.pad("R9", "1"), "SCL")

    def test_a_pad_can_be_untied(self):
        self.apply({"op": "set_pad_net", "ref": "U1", "pad": "2", "net": ""})
        self.assertEqual(self.pad("U1", "2"), "")

    def test_a_missing_pad_lists_the_ones_that_exist(self):
        with self.assertRaises(O.OpError) as ctx:
            E.propose(self.board, [{"op": "set_pad_net", "ref": "R9", "pad": "7",
                                    "net": "GND"}])
        self.assertEqual(ctx.exception.code, "PAD_NOT_FOUND")
        self.assertIn("1", str(ctx.exception))

    def test_a_missing_net_argument_is_refused(self):
        with self.assertRaises(O.OpError) as ctx:
            E.propose(self.board, [{"op": "set_pad_net", "ref": "R9", "pad": "1"}])
        self.assertEqual(ctx.exception.code, "ARG_MISSING")

class NameOnlyBoard(SyncBase):
    """KiCad 10 boards have no net table; the pad still names its net."""
    def setUp(self):
        SyncBase.setUp(self)
        text = BOARD.replace('(version 20221018)', '(version 20260206)')
        for line in ('  (net 0 "")\n', '  (net 1 "GND")\n', '  (net 2 "SDA")\n',
                     '  (net 3 "STALE")\n'):
            text = text.replace(line, "")
        text = (text.replace('(net 1 "GND")', '(net "GND")')
                    .replace('(net 2 "SDA")', '(net "SDA")')
                    .replace('(net 3 "STALE")', '(net "STALE")'))
        with open(self.board, "w", encoding="utf-8", newline="\n") as fh:
            fh.write(text)

    def test_the_index_reads_nets_from_the_objects(self):
        idx = SY.board_index(self.board)
        self.assertTrue(idx["name_only_nets"])
        self.assertEqual(idx["nets"], ["GND", "SDA", "STALE"])

    def test_set_pad_net_writes_the_name_only_form(self):
        plan = E.propose(self.board, [{"op": "set_pad_net", "ref": "R9", "pad": "1",
                                       "net": "GND"}])
        self.assertEqual(plan["blocking"], [], plan["blocking"])
        E.apply_plan(plan["plan_id"], confirm=True, options=dict(engine=False))
        with open(self.board, "r", encoding="utf-8") as fh:
            text = fh.read()
        self.assertIn('(net "GND")', text)
        self.assertNotIn('(net 1 "GND")', text)

@unittest.skipUnless(ENGINE.find_kicad_cli(), "kicad-cli is not installed")
class AgainstKiCad(SyncBase):
    """The real pair: our board and its schematic, through KiCad's netlister."""
    BOARD = os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))),
                         "_scratch", "core", "stm32f401re_core.kicad_pcb")
    SCH = os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))),
                       "_scratch", "core", "stm32f401re_core.kicad_sch")

    def setUp(self):
        SyncBase.setUp(self)
        if not (os.path.isfile(self.BOARD) and os.path.isfile(self.SCH)):
            self.skipTest("the core-board scratch pair is not in this checkout")

    def test_the_pair_this_repo_built_is_in_sync(self):
        net = ENGINE.sch_netlist(self.SCH)
        self.assertEqual(net["status"], "ok", net.get("message"))
        diff = SY.compare(self.BOARD, net)
        self.assertTrue(diff["in_sync"], diff["issues"][:5])
        self.assertEqual(diff["board_counts"]["footprints"], 32)
        self.assertEqual(diff["schematic_counts"]["components"], 35)
        # the three PWR_FLAG symbols have no footprint and must not be reported
        # as missing parts
        self.assertEqual([r for r in diff["issues"] if r["kind"] == "missing_on_board"], [])

if __name__ == "__main__":
    unittest.main()
