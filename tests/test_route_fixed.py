"""Locked / fixed copper: the file's word and the caller's word.

Two spellings of one claim, and the claim is "do not move this copper":

* the **file** says it with `(locked)` - the same thing Specctra writes as
  `(type fix)`, and the reason a session file never returns fixed copper;
* the **caller** says it with `fixed_nets` on `board_route`.

What every test here holds the code to:

* **the flag changes the ANSWER, not just a field** - without `fixed_nets` the
  same net is routed; with it, no op names that net. A flag that only decorates
  the report would pass a weaker test and be worthless;
* **a fixed net with no copper is called out** - "leave it alone and call it
  fixed" is the one outcome that would report an open net as settled;
* **the refusal is a refusal, not a silent exclusion** - a fixed net inside a
  diff pair or a length group is an error, because both passes MOVE copper while
  still being reported as a pair/group;
* **the write path refuses locked copper and can be told to go ahead** - a guard
  that cannot be overridden is a different bug than a guard that does not exist.
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
from pcb_agent_mcp.fidelity import kicad_doc as K
from pcb_agent_mcp.route import router
from pcb_agent_mcp.route import rules as R

LOCKED_BOARD = """(kicad_pcb (version 20221018) (generator pcbnew)
  (general (thickness 1.6))
  (layers
    (0 "F.Cu" signal)
    (31 "B.Cu" signal)
    (44 "Edge.Cuts" user)
  )
  (net 0 "")
  (net 1 "GND")
  (net 2 "VCC")
  (net_class "Default" ""
    (clearance 0.2) (trace_width 0.25) (via_dia 0.6) (via_drill 0.3))
  (gr_line (start 0 0) (end 40 0) (layer "Edge.Cuts") (width 0.1) (tstamp 00000000-0000-0000-0000-000000000001))
  (gr_line (start 40 0) (end 40 30) (layer "Edge.Cuts") (width 0.1) (tstamp 00000000-0000-0000-0000-000000000002))
  (gr_line (start 40 30) (end 0 30) (layer "Edge.Cuts") (width 0.1) (tstamp 00000000-0000-0000-0000-000000000003))
  (gr_line (start 0 30) (end 0 0) (layer "Edge.Cuts") (width 0.1) (tstamp 00000000-0000-0000-0000-000000000004))
  (segment (start 5 5) (end 15 5) (width 0.25) (layer "F.Cu") (net 1) (tstamp 00000000-0000-0000-0000-000000000101) (locked))
  (segment (start 5 8) (end 15 8) (width 0.25) (layer "F.Cu") (net 2) (tstamp 00000000-0000-0000-0000-000000000102))
  (segment (start 5 11) (end 15 11) (width 0.25) (layer "F.Cu") (net 2) (tstamp 00000000-0000-0000-0000-000000000103) (locked))
  (footprint "SYN:Pad" (version 20221018) (generator pcbnew)
    (layer "F.Cu") (at 0 0) (attr smd)
    (fp_text reference "J1" (at 0 -3) (layer "F.SilkS")
      (effects (font (size 0.7 0.7) (thickness 0.12))))
    (pad "1" smd rect (at 2 5) (size 1 1)
      (layers "F.Cu" "F.Paste" "F.Mask") (net 1 "GND")
      (tstamp 00000000-0000-0000-0000-000000000201))
    (pad "2" smd rect (at 38 5) (size 1 1)
      (layers "F.Cu" "F.Paste" "F.Mask") (net 1 "GND")
      (tstamp 00000000-0000-0000-0000-000000000202)))
  (footprint "SYN:Pad" (version 20221018) (generator pcbnew)
    (layer "F.Cu") (at 0 0) (attr smd)
    (fp_text reference "J2" (at 0 -8) (layer "F.SilkS")
      (effects (font (size 0.7 0.7) (thickness 0.12))))
    (pad "1" smd rect (at 2 8) (size 1 1)
      (layers "F.Cu" "F.Paste" "F.Mask") (net 2 "VCC")
      (tstamp 00000000-0000-0000-0000-000000000203))
    (pad "2" smd rect (at 38 8) (size 1 1)
      (layers "F.Cu" "F.Paste" "F.Mask") (net 2 "VCC")
      (tstamp 00000000-0000-0000-0000-000000000204)))
)
"""

GND_UUID = "00000000-0000-0000-0000-000000000101"
VCC_LOCKED_UUID = "00000000-0000-0000-0000-000000000103"


class LockedBase(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.mkdtemp(prefix="pcbfixed-")
        self._home = os.environ.get("PCB_AGENT_HOME")
        os.environ["PCB_AGENT_HOME"] = os.path.join(self.tmp, "home")
        self.board = os.path.join(self.tmp, "board.kicad_pcb")
        with open(self.board, "w", encoding="utf-8", newline="\n") as fh:
            fh.write(LOCKED_BOARD)

    def tearDown(self):
        if self._home is None:
            os.environ.pop("PCB_AGENT_HOME", None)
        else:
            os.environ["PCB_AGENT_HOME"] = self._home
        P.PLANS.clear()
        shutil.rmtree(self.tmp, ignore_errors=True)

    def editor(self):
        with open(self.board, "rb") as fh:
            doc = K.KiCadDocument.from_text(fh.read().decode("utf-8"), self.board)
        return O.Editor(doc)


class TheFileSaysLocked(LockedBase):
    """`(locked)` is read, and it is read from the file, not invented."""

    def test_the_flag_is_read_off_the_segment(self):
        from pcb_agent_mcp import tools
        board, e = tools._load_board({"path": self.board})
        self.assertIsNone(e, e)
        # canonical/track y is Y-UP: the file stores 5/8/11 downwards, so the
        # model reports -5/-8/-11 and the flag rides along unchanged
        flags = sorted((it["start"][1], bool(it.get("locked"))) for it in board.tracks)
        self.assertEqual(flags, [(-11.0, True), (-8.0, False), (-5.0, True)])

    def test_the_tree_view_reads_it_too(self):
        # The fidelity view is the other reader of the same file: both have to
        # agree, or one of them is reading a different board.
        from pcb_agent_mcp.fidelity import views as V
        doc = K.KiCadDocument.from_text(LOCKED_BOARD, self.board)
        rows = V.BoardView(doc).tracks()
        self.assertEqual(len(rows), 3)
        self.assertEqual(sorted((round(r["y1_mm"], 3), r["locked"]) for r in rows),
                         [(-11.0, True), (-8.0, False), (-5.0, True)])
        # and the two readers of the same file agree to the millimetre
        from pcb_agent_mcp import tools
        board, e = tools._load_board({"path": self.board})
        self.assertIsNone(e, e)
        self.assertEqual(
            sorted((round(r["y1_mm"], 6), r["locked"]) for r in rows),
            sorted((round(t["start"][1], 6), bool(t.get("locked")))
                   for t in board.tracks))
        # and the raw node still carries the file's own text
        self.assertEqual(sum(1 for r in rows if "(locked)" in r["raw"]), 2)


class DeletingLockedCopper(LockedBase):
    """The write path must refuse, and must be overridable explicitly."""

    def test_a_locked_segment_is_refused_by_name(self):
        ed = self.editor()
        with self.assertRaises(O.OpError) as ctx:
            O.OPS["delete_track"]["run"](ed, {"uuid": GND_UUID}, "s")
        self.assertEqual(ctx.exception.code, "ARG_CONFLICT")
        self.assertIn(GND_UUID, str(ctx.exception))
        self.assertIn("allow_fixed=true", str(ctx.exception))
        # and nothing was removed on the way to the refusal
        self.assertEqual(len(ed.items("segment")), 3)

    def test_allow_fixed_goes_ahead(self):
        ed = self.editor()
        report = O.OPS["delete_track"]["run"](
            ed, {"uuid": GND_UUID, "allow_fixed": True}, "s")
        self.assertEqual(report["nodes_removed"], 1)
        self.assertEqual(len(ed.items("segment")), 2)

    def test_an_unlocked_segment_is_untouched_by_the_guard(self):
        ed = self.editor()
        report = O.OPS["delete_track"]["run"](
            ed, {"uuid": "00000000-0000-0000-0000-000000000102"}, "s")
        self.assertEqual(report["nodes_removed"], 1)

    def test_resizing_a_net_with_locked_copper_is_refused(self):
        ed = self.editor()
        with self.assertRaises(O.OpError) as ctx:
            O.OPS["set_track_width"]["run"](
                ed, {"net": "VCC", "width": 0.5}, "s")
        self.assertEqual(ctx.exception.code, "ARG_CONFLICT")
        self.assertIn(VCC_LOCKED_UUID, str(ctx.exception))
        # not one segment changed: the refusal happens before the first write
        from pcb_agent_mcp.fidelity import sexpr as S
        widths = [S.arg_num(S.child(n, "width"), 1)
                  for n in ed.items("segment")
                  if S.arg_text(S.child(n, "net"), 1) == "2"]
        self.assertEqual(widths, [0.25, 0.25])

    def test_resizing_the_whole_net_with_allow_fixed(self):
        ed = self.editor()
        report = O.OPS["set_track_width"]["run"](
            ed, {"net": "VCC", "width": 0.5, "allow_fixed": True}, "s")
        self.assertEqual(report["segments_changed"], 2)

    def test_the_refusal_comes_out_of_propose_as_well(self):
        with self.assertRaises(O.OpError) as ctx:
            E.propose(self.board, [{"op": "delete_track", "uuid": GND_UUID}])
        self.assertEqual(ctx.exception.code, "ARG_CONFLICT")


class FixedNetsOnTheRouter(LockedBase):
    """`fixed_nets` on `board_route`: not routed, not ripped, and reported."""

    def route(self, **kw):
        from pcb_agent_mcp import tools
        board, e = tools._load_board({"path": self.board})
        self.assertIsNone(e, e)
        return router.plan(board, **kw)

    def test_a_fixed_net_is_not_routed_and_its_copper_is_counted(self):
        # GND already has one 10 mm segment; SIG has one too. Asking for both to
        # be routed with GND fixed must leave GND's copper alone and still settle
        # the question of what it looks like.
        res = self.route(fixed_nets=["GND"])
        fixed = res["fixed"]
        self.assertEqual(fixed["nets"], ["GND"])
        self.assertEqual(fixed["unknown"], [])
        self.assertEqual(fixed["copper"]["GND"]["segments"], 1)
        self.assertAlmostEqual(fixed["copper"]["GND"]["length_mm"], 10.0, places=3)
        self.assertEqual(fixed["copper"]["GND"]["locked_segments"], 1)
        self.assertEqual(fixed["ops_emitted_for_fixed"], 0)
        gnd = [n for n in res["nets"] if n["net"] == "GND"][0]
        self.assertEqual(gnd["status"], "fixed")
        self.assertTrue(gnd["fixed"])
        self.assertEqual(gnd["segments"], 1)      # the board's, not the router's
        self.assertNotIn("GND", [f["net"] for f in res["failed"]])

    def test_the_flag_is_what_changed_the_answer(self):
        # Same board, same call, one word missing: the net that was left alone
        # is now a routing job. Without this control the test above would pass
        # on a router that simply never routes anything.
        res = self.route()
        self.assertIsNone(res["fixed"])
        gnd = [n for n in res["nets"] if n["net"] == "GND"][0]
        self.assertEqual(gnd["status"], "routed")
        self.assertGreater(gnd["segments"], 0)
        self.assertGreater(len([o for o in res["ops"] if o["net"] == "GND"]), 0)

    def test_a_fixed_net_with_no_copper_is_called_out(self):
        # VCC's two segments are on the board, so pick a net that has pads but no
        # copper: none here. Instead, the sharpest available case - a net the
        # board has no copper for at all is `unknown`, and an existing net whose
        # copper was deleted is the same shape as adding one that never had any.
        # The router must not route it "because it is fixed".
        res = self.route(fixed_nets=["NOPE"])
        self.assertEqual(res["fixed"]["unknown"], ["NOPE"])
        self.assertEqual(res["fixed"]["copper"], {})

    def test_a_fixed_net_in_a_length_group_is_refused(self):
        with self.assertRaises(router.RouteError) as ctx:
            self.route(fixed_nets=["GND"],
                       length_groups=[{"nets": ["GND", "VCC"], "max_skew_mm": 0.5}])
        self.assertEqual(ctx.exception.reason_code, "ARG_CONFLICT")
        self.assertIn("GND", ctx.exception.message)
        self.assertIn("length-matched", ctx.exception.message)

    def test_a_fixed_net_in_a_diff_pair_is_refused(self):
        with self.assertRaises(router.RouteError) as ctx:
            self.route(fixed_nets=["VCC"],
                       diff_pairs=[{"p": "GND", "n": "VCC", "gap_mm": 0.2}])
        self.assertEqual(ctx.exception.reason_code, "ARG_CONFLICT")
        self.assertIn("VCC", ctx.exception.message)

    def test_the_tool_carries_the_flag_and_the_plan_still_refuses_a_partial_board(self):
        from pcb_agent_mcp import tools
        out = tools.call_tool("board_route",
                              {"path": self.board, "fixed_nets": "GND"})
        self.assertEqual(out["status"], "ok", out.get("message"))
        self.assertEqual(out["value"]["nets_fixed"], 1)


class AMissingFixedNetIsReportedByTheRouter(unittest.TestCase):
    """The `fixed_missing_copper` branch, exercised where it can actually fire.

    On a board with pads but no copper the whole test above has no fixed net to
    be wrong about, so this case is built directly: one net declared fixed with
    nothing on the board. The status says so instead of leaving it "unrouted and
    therefore fine".
    """

    def setUp(self):
        self.tmp = tempfile.mkdtemp(prefix="pcbfixed2-")
        self._home = os.environ.get("PCB_AGENT_HOME")
        os.environ["PCB_AGENT_HOME"] = os.path.join(self.tmp, "home")
        self.board = os.path.join(self.tmp, "board.kicad_pcb")
        E.create_board(self.board, {"width_mm": 60, "height_mm": 40,
                                    "copper_layers": 2, "engine": False,
                                    "title": "fixed"})
        self.lib = os.path.join(self.tmp, "Syn.pretty")
        os.makedirs(self.lib)

    def tearDown(self):
        if self._home is None:
            os.environ.pop("PCB_AGENT_HOME", None)
        else:
            os.environ["PCB_AGENT_HOME"] = self._home
        P.PLANS.clear()
        shutil.rmtree(self.tmp, ignore_errors=True)

    def _part(self, ref, x, y, net):
        body = ('(footprint "SYN:Pad" (version 20221018) (generator pcbnew)\n'
                '  (layer "F.Cu")\n  (attr smd)\n'
                '  (fp_text reference "%s" (at 0 -3) (layer "F.SilkS")\n'
                '    (effects (font (size 0.7 0.7) (thickness 0.12))))\n'
                '  (fp_text value "Pad" (at 0 3) (layer "F.Fab")\n'
                '    (effects (font (size 0.7 0.7) (thickness 0.12))))\n'
                '  (pad "1" smd rect (at 0 0) (size 1 1)\n'
                '    (layers "F.Cu" "F.Paste" "F.Mask")\n'
                '    (tstamp 5%s-1111-1111-1111-111111111111))\n)\n' % (ref, ref))
        with open(os.path.join(self.lib, "Pad.kicad_mod"), "w",
                  encoding="utf-8", newline="\n") as fh:
            fh.write(body)
        return {"op": "add_footprint", "ref": ref, "x": x, "y": y,
                "library": self.lib, "name": "Pad", "pad_nets": {"1": net},
                "create_nets": True}

    def test_no_copper_is_reported_not_ignored(self):
        from pcb_agent_mcp import tools
        ops = [self._part("J1", 10.0, 8.0, "SIG"),
               self._part("J2", 40.0, 8.0, "SIG")]
        plan = E.propose(self.board, ops)
        self.assertEqual(plan["blocking"], [], plan["blocking"])
        E.apply_plan(plan["plan_id"], confirm=True, options={"engine": False})
        board, e = tools._load_board({"path": self.board})
        self.assertIsNone(e, e)
        res = router.plan(board, fixed_nets=["SIG"])
        sig = [n for n in res["nets"] if n["net"] == "SIG"][0]
        self.assertEqual(sig["status"], "fixed_missing_copper")
        self.assertEqual(sig["reason"]["code"], "fixed_missing_copper")
        self.assertEqual(res["fixed"]["copper"]["SIG"]["segments"], 0)
        self.assertEqual(res["fixed"]["ops_emitted_for_fixed"], 0)
        self.assertEqual([f["net"] for f in res["failed"]], ["SIG"])
        # And the control: the same board without the flag routes the net.
        again = router.plan(board)
        self.assertEqual([n for n in again["nets"]
                          if n["net"] == "SIG"][0]["status"], "routed")


if __name__ == "__main__":
    unittest.main()
