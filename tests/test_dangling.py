"""Dangling copper: what a tail is, and which ones may be cut.

The mechanism is FreeRouting's `DanglingCopper` (walk the stub behind a free end
to the next fork / pin / fixed item, keep user-fixed copper, never let the count
of open connections rise). The three readings we add are stated in the module
docstring and held to here:

* **attached is a claim about real geometry** - a track end inside another
  piece's width, a via's size, a pad's reach, or a point inside a same-net
  `filled_polygon`. A gap of 0.002 mm is a gap, and the tolerance is a parameter
  whose effect this file measures rather than assumes;
* **cuttable is a proof, not a hope** - a chain hangs off exactly one attachment
  point and is no pad's only copper, so removing it cannot open a connection;
* **every guard has a control that fails** - a clean board reports nothing, a
  via that still carries copper on both layers kills the tail, and a `(locked)`
  piece is reported but not cut.
"""
import os
import shutil
import sys
import tempfile
import unittest

sys.path.insert(0, os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "src"))

from pcb_agent_mcp import edit as E
from pcb_agent_mcp.edit import ops as O
from pcb_agent_mcp.edit import plan as P
from pcb_agent_mcp import tools

# One board carrying every finding at once: a T-junction tail, a stub that is a
# pad's only copper, a (locked) tail, and a piece attached at neither end.
TAIL_BOARD = """(kicad_pcb (version 20221018) (generator pcbnew)
  (general (thickness 1.6))
  (layers (0 "F.Cu" signal) (31 "B.Cu" signal) (44 "Edge.Cuts" user))
  (net 0 "") (net 1 "GND") (net 2 "VCC") (net 3 "SIG")
  (net_class "Default" "" (clearance 0.2) (trace_width 0.25) (via_dia 0.6) (via_drill 0.3))
  (gr_line (start 0 0) (end 40 0) (layer "Edge.Cuts") (width 0.1) (tstamp 00000000-0000-0000-0000-000000000001))
  (segment (start 5 5) (end 20 5) (width 0.25) (layer "F.Cu") (net 1) (tstamp 00000000-0000-0000-0000-000000000301))
  (segment (start 12 5) (end 12 10) (width 0.25) (layer "F.Cu") (net 1) (tstamp 00000000-0000-0000-0000-000000000302))
  (segment (start 35 5) (end 35 10) (width 0.25) (layer "F.Cu") (net 1) (tstamp 00000000-0000-0000-0000-000000000303))
  (segment (start 25 20) (end 30 20) (width 0.25) (layer "F.Cu") (net 3) (tstamp 00000000-0000-0000-0000-000000000304))
  (segment (start 5 15) (end 20 15) (width 0.25) (layer "F.Cu") (net 2) (tstamp 00000000-0000-0000-0000-000000000305))
  (segment (start 12 15) (end 12 25) (width 0.25) (layer "F.Cu") (net 2) (tstamp 00000000-0000-0000-0000-000000000306) (locked))
  (footprint "SYN:Pad" (version 20221018) (generator pcbnew)
    (layer "F.Cu") (at 0 0) (attr smd)
    (fp_text reference "J1" (at 0 -3) (layer "F.SilkS")
      (effects (font (size 0.7 0.7) (thickness 0.12))))
    (pad "1" smd rect (at 5 5) (size 1 1)
      (layers "F.Cu" "F.Paste" "F.Mask") (net 1 "GND")
      (tstamp 00000000-0000-0000-0000-000000000401))
    (pad "2" smd rect (at 20 5) (size 1 1)
      (layers "F.Cu" "F.Paste" "F.Mask") (net 1 "GND")
      (tstamp 00000000-0000-0000-0000-000000000402))
    (pad "3" smd rect (at 35 5) (size 1 1)
      (layers "F.Cu" "F.Paste" "F.Mask") (net 1 "GND")
      (tstamp 00000000-0000-0000-0000-000000000403)))
  (footprint "SYN:Pad" (version 20221018) (generator pcbnew)
    (layer "F.Cu") (at 0 0) (attr smd)
    (fp_text reference "J2" (at 0 -14) (layer "F.SilkS")
      (effects (font (size 0.7 0.7) (thickness 0.12))))
    (pad "1" smd rect (at 5 15) (size 1 1)
      (layers "F.Cu" "F.Paste" "F.Mask") (net 2 "VCC")
      (tstamp 00000000-0000-0000-0000-000000000501))
    (pad "2" smd rect (at 20 15) (size 1 1)
      (layers "F.Cu" "F.Paste" "F.Mask") (net 2 "VCC")
      (tstamp 00000000-0000-0000-0000-000000000502)))
)
"""

# A stub whose far end is a via carrying copper on one layer only (V3), next to
# one whose via is still alive on B.Cu (V4) - the negative control.
VIA_BOARD = """(kicad_pcb (version 20221018) (generator pcbnew)
  (general (thickness 1.6))
  (layers (0 "F.Cu" signal) (31 "B.Cu" signal) (44 "Edge.Cuts" user))
  (net 0 "") (net 4 "V3") (net 5 "V4")
  (net_class "Default" "" (clearance 0.2) (trace_width 0.25) (via_dia 0.6) (via_drill 0.3))
  (gr_line (start 0 0) (end 40 0) (layer "Edge.Cuts") (width 0.1) (tstamp 00000000-0000-0000-0000-000000000021))
  (segment (start 5 20) (end 20 20) (width 0.25) (layer "F.Cu") (net 4) (tstamp 00000000-0000-0000-0000-000000000701))
  (segment (start 12 20) (end 12 26) (width 0.25) (layer "F.Cu") (net 4) (tstamp 00000000-0000-0000-0000-000000000702))
  (via (at 12 26) (size 0.6) (drill 0.3) (layers "F.Cu" "B.Cu") (net 4) (tstamp 00000000-0000-0000-0000-000000000703))
  (segment (start 5 28) (end 20 28) (width 0.25) (layer "F.Cu") (net 5) (tstamp 00000000-0000-0000-0000-000000000711))
  (segment (start 12 28) (end 12 32) (width 0.25) (layer "F.Cu") (net 5) (tstamp 00000000-0000-0000-0000-000000000712))
  (via (at 12 32) (size 0.6) (drill 0.3) (layers "F.Cu" "B.Cu") (net 5) (tstamp 00000000-0000-0000-0000-000000000713))
  (segment (start 12 32) (end 20 32) (width 0.25) (layer "B.Cu") (net 5) (tstamp 00000000-0000-0000-0000-000000000714))
  (footprint "SYN:Pad" (version 20221018) (generator pcbnew)
    (layer "F.Cu") (at 0 0) (attr smd)
    (fp_text reference "J3" (at 0 -19) (layer "F.SilkS")
      (effects (font (size 0.7 0.7) (thickness 0.12))))
    (pad "1" thru_hole circle (at 5 20) (size 1 1) (drill 0.4)
      (layers "*.Cu" "*.Mask") (net 4 "V3")
      (tstamp 00000000-0000-0000-0000-000000000721))
    (pad "2" thru_hole circle (at 20 20) (size 1 1) (drill 0.4)
      (layers "*.Cu" "*.Mask") (net 4 "V3")
      (tstamp 00000000-0000-0000-0000-000000000722)))
  (footprint "SYN:Pad" (version 20221018) (generator pcbnew)
    (layer "F.Cu") (at 0 0) (attr smd)
    (fp_text reference "J4" (at 0 -27) (layer "F.SilkS")
      (effects (font (size 0.7 0.7) (thickness 0.12))))
    (pad "1" thru_hole circle (at 5 28) (size 1 1) (drill 0.4)
      (layers "*.Cu" "*.Mask") (net 5 "V4")
      (tstamp 00000000-0000-0000-0000-000000000731))
    (pad "2" thru_hole circle (at 20 28) (size 1 1) (drill 0.4)
      (layers "*.Cu" "*.Mask") (net 5 "V4")
      (tstamp 00000000-0000-0000-0000-000000000732))
    (pad "3" thru_hole circle (at 20 32) (size 1 1) (drill 0.4)
      (layers "*.Cu" "*.Mask") (net 5 "V4")
      (tstamp 00000000-0000-0000-0000-000000000733)))
)
"""

# Two stubs from pads: one ends inside a real filled pour, the other in mid-air.
POUR_BOARD = """(kicad_pcb (version 20221018) (generator pcbnew)
  (general (thickness 1.6))
  (layers (0 "F.Cu" signal) (31 "B.Cu" signal) (44 "Edge.Cuts" user))
  (net 0 "") (net 1 "GND")
  (net_class "Default" "" (clearance 0.2) (trace_width 0.25) (via_dia 0.6) (via_drill 0.3))
  (gr_line (start 0 0) (end 40 0) (layer "Edge.Cuts") (width 0.1) (tstamp 00000000-0000-0000-0000-000000000031))
  (segment (start 20 10) (end 30 10) (width 0.25) (layer "F.Cu") (net 1) (tstamp 00000000-0000-0000-0000-000000000801))
  (segment (start 20 20) (end 30 20) (width 0.25) (layer "F.Cu") (net 1) (tstamp 00000000-0000-0000-0000-000000000802))
  (zone (net 1) (net_name "GND") (layer "F.Cu") (tstamp 00000000-0000-0000-0000-000000000810)
    (hatch edge 0.5)
    (connect_pads (clearance 0.2))
    (min_thickness 0.25)
    (fill yes (thermal_gap 0.5) (thermal_bridge_width 0.5))
    (polygon (pts (xy 24 5) (xy 36 5) (xy 36 15) (xy 24 15)))
    (filled_polygon (layer "F.Cu")
      (pts (xy 24 5) (xy 36 5) (xy 36 15) (xy 24 15))))
  (footprint "SYN:Pad" (version 20221018) (generator pcbnew)
    (layer "F.Cu") (at 0 0) (attr smd)
    (fp_text reference "J5" (at 0 -9) (layer "F.SilkS")
      (effects (font (size 0.7 0.7) (thickness 0.12))))
    (pad "1" smd rect (at 20 10) (size 1 1)
      (layers "F.Cu" "F.Paste" "F.Mask") (net 1 "GND")
      (tstamp 00000000-0000-0000-0000-000000000811))
    (pad "2" smd rect (at 20 20) (size 1 1)
      (layers "F.Cu" "F.Paste" "F.Mask") (net 1 "GND")
      (tstamp 00000000-0000-0000-0000-000000000812)))
)
"""

# One track, pad to pad. Nothing here is dangling, so an answer of zero is the
# only honest one.
CLEAN_BOARD = """(kicad_pcb (version 20221018) (generator pcbnew)
  (general (thickness 1.6))
  (layers (0 "F.Cu" signal) (31 "B.Cu" signal) (44 "Edge.Cuts" user))
  (net 0 "") (net 1 "GND")
  (net_class "Default" "" (clearance 0.2) (trace_width 0.25) (via_dia 0.6) (via_drill 0.3))
  (gr_line (start 0 0) (end 40 0) (layer "Edge.Cuts") (width 0.1) (tstamp 00000000-0000-0000-0000-000000000011))
  (segment (start 5 5) (end 20 5) (width 0.25) (layer "F.Cu") (net 1) (tstamp 00000000-0000-0000-0000-000000000601))
  (footprint "SYN:Pad" (version 20221018) (generator pcbnew)
    (layer "F.Cu") (at 0 0) (attr smd)
    (fp_text reference "J1" (at 0 -3) (layer "F.SilkS")
      (effects (font (size 0.7 0.7) (thickness 0.12))))
    (pad "1" smd rect (at 5 5) (size 1 1)
      (layers "F.Cu" "F.Paste" "F.Mask") (net 1 "GND")
      (tstamp 00000000-0000-0000-0000-000000000611))
    (pad "2" smd rect (at 20 5) (size 1 1)
      (layers "F.Cu" "F.Paste" "F.Mask") (net 1 "GND")
      (tstamp 00000000-0000-0000-0000-000000000612)))
)
"""

# Two pieces of copper 0.4 mm apart: not a connection at 0.002 mm of tolerance,
# a connection at 0.5 mm of it. The reading is the caller's to move, so the test
# moves it and watches the answer change.
GAP_BOARD = """(kicad_pcb (version 20221018) (generator pcbnew)
  (general (thickness 1.6))
  (layers (0 "F.Cu" signal) (31 "B.Cu" signal) (44 "Edge.Cuts" user))
  (net 0 "") (net 1 "GND")
  (net_class "Default" "" (clearance 0.2) (trace_width 0.25) (via_dia 0.6) (via_drill 0.3))
  (gr_line (start 0 0) (end 40 0) (layer "Edge.Cuts") (width 0.1) (tstamp 00000000-0000-0000-0000-000000000041))
  (segment (start 5 5) (end 10 5) (width 0.25) (layer "F.Cu") (net 1) (tstamp 00000000-0000-0000-0000-000000000901))
  (segment (start 10.4 5) (end 15 5) (width 0.25) (layer "F.Cu") (net 1) (tstamp 00000000-0000-0000-0000-000000000902))
)
"""

T = "00000000-0000-0000-0000-000000000"

class DanglingBase(unittest.TestCase):
    TEXT = TAIL_BOARD

    def setUp(self):
        self.tmp = tempfile.mkdtemp(prefix="pcbdang-")
        self._home = os.environ.get("PCB_AGENT_HOME")
        os.environ["PCB_AGENT_HOME"] = os.path.join(self.tmp, "home")
        self.board = os.path.join(self.tmp, "board.kicad_pcb")
        self.write(self.TEXT)

    def tearDown(self):
        if self._home is None:
            os.environ.pop("PCB_AGENT_HOME", None)
        else:
            os.environ["PCB_AGENT_HOME"] = self._home
        P.PLANS.clear()
        shutil.rmtree(self.tmp, ignore_errors=True)

    def write(self, text):
        with open(self.board, "w", encoding="utf-8", newline="\n") as fh:
            fh.write(text)

    def text(self):
        with open(self.board, encoding="utf-8") as fh:
            return fh.read()

    def board_model(self):
        board, e = tools._load_board({"path": self.board})
        self.assertIsNone(e, e)
        return board

    def survey(self, **kw):
        from pcb_agent_mcp import dangling
        return dangling.survey(self.board_model(), **kw)

    def ops(self, **kw):
        from pcb_agent_mcp import dangling
        return dangling.removal_ops(self.survey(), **kw)

    def call(self, args):
        return tools.TOOLS["board_dangling_copper"]["handler"](args)

    def tails(self, report=None):
        report = report or self.survey()
        out = []
        for net in report["nets"]:
            out.extend(net["tails"])
        return out

    def by_uuid(self, suffix, report=None):
        for t in self.tails(report):
            if any(u.endswith(suffix) for u in t["uuids"]):
                return t
        return None


class WhatCountsAsATail(DanglingBase):
    """A free end, the chain behind it, and where the chain stops."""

    def test_the_t_junction_tail_is_found_at_its_own_length(self):
        t = self.by_uuid("302")
        self.assertIsNotNone(t)
        self.assertEqual((t["tracks"], t["vias"], t["pieces"]), (1, 0, 1))
        self.assertAlmostEqual(t["length_mm"], 5.0, places=6)
        # it stops at the trunk, which is a fork (the trunk runs on both ways)
        self.assertEqual(t["ends_at"], "fork")
        self.assertTrue(t["cuttable"])

    def test_the_trunk_itself_is_not_a_tail(self):
        # both of its ends sit on pads, so it hangs off nothing
        for t in self.tails():
            self.assertNotIn(T + "301", t["uuids"])

    def test_a_stub_carrying_a_pads_only_copper_is_refused(self):
        t = self.by_uuid("303")
        self.assertIsNotNone(t)
        self.assertEqual(t["ends_at"], "pad")
        self.assertFalse(t["cuttable"])
        self.assertEqual(t["why_not"], "would_open_a_pad")
        # and the refusal is an answer, not a silent drop: the piece is listed
        self.assertNotIn(T + "303", [u for o in self.ops() for u in [o["uuid"]]])

    def test_locked_copper_is_reported_and_not_cut(self):
        t = self.by_uuid("306")
        self.assertIsNotNone(t)
        self.assertFalse(t["cuttable"])
        self.assertEqual(t["why_not"], "locked")
        self.assertEqual(t["locked"], [T + "306"])

    def test_floating_copper_is_a_separate_finding(self):
        report = self.survey()
        self.assertEqual(report["floating"], 1)
        net = [n for n in report["nets"] if n["net"] == "SIG"][0]
        self.assertEqual(net["floating"][0]["ends_at"], "open")
        self.assertEqual(net["floating"][0]["track_uuids"], [T + "304"])
        # reported always, cut only on request
        self.assertEqual([o["uuid"] for o in self.ops()], [T + "302"])
        self.assertEqual([o["uuid"] for o in self.ops(include_floating=True)],
                         [T + "302", T + "304"])

    def test_the_counts_add_up(self):
        report = self.survey()
        self.assertEqual((report["tails"], report["floating"], report["cuttable"]),
                         (3, 1, 1))
        self.assertAlmostEqual(report["cuttable_length_mm"], 5.0, places=6)
        self.assertEqual(report["nets_with_dangling"], 3)

    def test_an_arc_is_copper_too(self):
        # an arc is a track node in KiCad; a tail that runs into one must stop at
        # it rather than walk through the gap the arc's endpoints leave
        text = TAIL_BOARD.replace(
            '(segment (start 25 20) (end 30 20) (width 0.25) (layer "F.Cu") (net 3) (tstamp 00000000-0000-0000-0000-000000000304))',
            '(arc (start 25 20) (mid 27 22) (end 30 20) (width 0.25) (layer "F.Cu") (net 3) (tstamp 00000000-0000-0000-0000-000000000304))')
        self.write(text)
        net = [n for n in self.survey()["nets"] if n["net"] == "SIG"][0]
        self.assertEqual(net["floating"][0]["track_uuids"], [T + "304"])
        self.assertGreater(net["floating"][0]["length_mm"], 5.0)   # the arc's length


class ViasAndPours(DanglingBase):
    """The two places a tail can end that are not a track end."""

    TEXT = VIA_BOARD

    def test_a_dead_end_via_goes_with_its_tail(self):
        t = self.by_uuid("702")
        self.assertIsNotNone(t)
        self.assertEqual((t["tracks"], t["vias"]), (1, 1))
        self.assertEqual(t["via_uuids"], [T + "703"])
        self.assertTrue(t["cuttable"])
        # the cut names both nodes: leaving the via behind would leave a hole on
        # one layer that nobody drew on purpose
        self.assertEqual([(o["op"], o["uuid"]) for o in self.ops()],
                         [("delete_track", T + "702"), ("delete_via", T + "703")])

    def test_a_via_that_still_carries_copper_kills_the_tail(self):
        # V4's via has a B.Cu track on it, so the stub is not dangling at all:
        # this is the control that the dead-end reading is not "any via"
        self.assertIsNone(self.by_uuid("712"))
        report = self.survey()
        self.assertEqual([n["net"] for n in report["nets"]], ["V3"])
        self.assertEqual(report["tails"], 1)

    def test_a_pour_is_an_attachment(self):
        self.write(POUR_BOARD)
        report = self.survey()
        self.assertEqual(report["nets_with_pour"], 1)
        net = report["nets"][0]
        self.assertTrue(net["has_pour"])
        # 801 ends inside the filled polygon, 802 ends in mid-air
        self.assertIsNone(self.by_uuid("801", report))
        t = self.by_uuid("802", report)
        self.assertIsNotNone(t)
        self.assertEqual(t["ends_at"], "pad")
        self.assertFalse(t["cuttable"])

    def test_a_keepout_is_not_copper(self):
        text = POUR_BOARD.replace(
            '(zone (net 1) (net_name "GND") (layer "F.Cu")',
            '(zone (net 1) (net_name "GND") (layer "F.Cu") (keepout (tracks not_allowed))')
        self.write(text)
        report = self.survey()
        self.assertEqual(report["nets_with_pour"], 0)
        # with the pour gone from the model, 801 is a pad stub like 802
        self.assertIsNotNone(self.by_uuid("801", report))


class TheGapIsReal(DanglingBase):
    """`tolerance_mm` is a reading, so moving it has to move the answer."""

    TEXT = GAP_BOARD

    def test_at_the_default_tolerance_the_two_pieces_do_not_touch(self):
        report = self.survey()
        self.assertEqual(report["floating"], 2)
        self.assertEqual(report["tolerance_mm"], 0.002)

    def test_a_coarse_tolerance_joins_them_into_one_chain(self):
        report = self.survey(tol_mm=0.5)
        self.assertEqual(report["floating"], 1)
        net = report["nets"][0]
        self.assertEqual(net["floating"][0]["tracks"], 2)


class ACleanBoardReportsNothing(DanglingBase):
    TEXT = CLEAN_BOARD

    def test_nothing_is_dangling(self):
        report = self.survey()
        self.assertEqual((report["tails"], report["floating"], report["cuttable"]),
                         (0, 0, 0))
        self.assertEqual(report["nets_with_dangling"], 0)
        self.assertEqual(self.ops(), [])

    def test_and_the_tool_says_so_without_writing(self):
        out = self.call({"path": self.board, "apply": True})
        self.assertEqual(out["status"], "ok")
        self.assertTrue(out["value"]["writes_nothing"])
        self.assertEqual(out["message"], "no dangling copper")
        self.assertEqual(self.text(), CLEAN_BOARD)


class WritingTheCut(DanglingBase):
    """`board_dangling_copper` with apply=true: the file, not just the report."""

    def test_the_report_writes_nothing(self):
        out = self.call({"path": self.board})
        self.assertEqual(out["status"], "ok")
        self.assertTrue(out["value"]["writes_nothing"])
        self.assertEqual(out["value"]["ops_count"], 1)
        self.assertEqual(self.text(), TAIL_BOARD)

    def test_the_cut_lands_and_leaves_everything_else_alone(self):
        out = self.call({"path": self.board, "apply": True})
        self.assertEqual(out["status"], "ok", out)
        self.assertFalse(out["value"]["writes_nothing"])
        self.assertEqual(out["value"]["ops_written"], 1)
        after = self.text()
        self.assertNotIn(T + "302", after)            # the tail is gone
        for keep in ("301", "303", "304", "305", "306"):   # trunk, pad stub,
            self.assertIn(T + keep, after)                 # floating, locked
        # and the second survey agrees with the file it is looking at
        self.assertEqual(out["value"]["after"]["tails"], 2)
        self.assertEqual(out["value"]["after"]["cuttable"], 0)

    def test_floating_copper_is_only_cut_when_asked(self):
        out = self.call({"path": self.board, "apply": True,
                         "include_floating": True})
        self.assertEqual(out["status"], "ok", out)
        after = self.text()
        self.assertNotIn(T + "302", after)
        self.assertNotIn(T + "304", after)
        self.assertIn(T + "306", after)               # locked still there
        self.assertEqual(out["value"]["ops_written"], 2)
        self.assertEqual(out["value"]["after"]["floating"], 0)

    def test_the_uuids_in_the_ops_are_in_the_file(self):
        text = self.text()
        ops = self.ops(include_floating=True)
        self.assertTrue(ops)
        for op in ops:
            self.assertIn(op["uuid"], text)


class TheCutRefusesLockedCopperAtTheOpLayer(DanglingBase):
    """The safety net under the guard: `delete_via` is a write path of its own."""

    TEXT = VIA_BOARD

    def via_board(self):
        text = VIA_BOARD.replace(
            '(via (at 12 26) (size 0.6) (drill 0.3) (layers "F.Cu" "B.Cu") (net 4) (tstamp 00000000-0000-0000-0000-000000000703))',
            '(via (at 12 26) (size 0.6) (drill 0.3) (layers "F.Cu" "B.Cu") (net 4) (tstamp 00000000-0000-0000-0000-000000000703) (locked))')
        self.write(text)

    def test_a_locked_via_is_refused_by_name(self):
        self.via_board()
        from pcb_agent_mcp.fidelity import kicad_doc as K
        ed = O.Editor(K.KiCadDocument.from_text(self.text(), self.board))
        with self.assertRaises(O.OpError) as ctx:
            O.OPS["delete_via"]["run"](ed, {"uuid": T + "703"}, "s")
        self.assertEqual(ctx.exception.code, "ARG_CONFLICT")
        self.assertIn(T + "703", str(ctx.exception))
        self.assertIn("allow_fixed=true", str(ctx.exception))
        self.assertEqual(len(ed.items("via")), 2)

    def test_allow_fixed_goes_ahead(self):
        self.via_board()
        from pcb_agent_mcp.fidelity import kicad_doc as K
        ed = O.Editor(K.KiCadDocument.from_text(self.text(), self.board))
        report = O.OPS["delete_via"]["run"](
            ed, {"uuid": T + "703", "allow_fixed": True}, "s")
        self.assertEqual(report["nodes_removed"], 1)
        self.assertEqual(len(ed.items("via")), 1)

    def test_the_locked_via_makes_the_whole_tail_uncuttable(self):
        self.via_board()
        t = self.by_uuid("702")
        self.assertFalse(t["cuttable"])
        self.assertEqual(t["why_not"], "locked")
        self.assertEqual(self.ops(), [])


if __name__ == "__main__":
    unittest.main()
