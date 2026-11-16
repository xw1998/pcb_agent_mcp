"""`move_cluster` / `swap_pad_nets` / `reserve_region` / `release_region`.

Why these four exist, measured on RK3506 (`docs/173` §5): five of the twelve
defects on that board had no operation that could express their answer. Four of
the five are "make room here" - the only fix is to move a group of parts and the
copper that belongs to them in one rigid step - and the fifth (DDR: 35 of 39
nets have nowhere to put a tuning tooth) needs a band held for a net group
before anything is routed into it. `add_keepout` can only forbid and `add_zone`
can only add copper, so neither could say "this band is being held for these
nets", and every generative op took a 1-D object, so "make room here" was not
expressible at all.

What each test is defending:

* a cluster move that leaves copper behind is a net that OPENS SILENTLY - the
  file still parses, KiCad still opens it, and the only thing that notices is
  `board_drc`'s unconnected count. So the refusal has to be the default and it
  has to name the nets, and the "allow" path has to report the number.
* the pad-absolute convention is measured, not assumed: pad `(at)` is relative to
  the footprint in the FILE frame and the footprint angle turns it
  counter-clockwise ON SCREEN. The wrong sign moves a rotated part's pads to the
  far side of the part, so this file pins the exact number.
* a reservation has to be findable again by NAME. KiCad writes `(name ...)` on
  its own auto-placement rule areas (read off `_corpus/multichannel.kicad_pcb`),
  so the field is a fact of the format and round-trips through the engine.

Engine (kicad-cli) checks are off here, as in `test_board_ops_footprint.py`:
this file is about the operations, and the four file gates are covered by
`test_edit.py`.
"""
import hashlib
import os
import shutil
import tempfile
import unittest

from pcb_agent_mcp import edit as E
from pcb_agent_mcp.edit import ops as O
from pcb_agent_mcp.edit import plan as P
from pcb_agent_mcp.fidelity import kicad_doc as K
from pcb_agent_mcp.fidelity import sexpr as S

SPEC = {"width_mm": 60, "height_mm": 40, "copper_layers": 2, "engine": False}

_TEMPLATE = {}


def template_bytes():
    if "bytes" not in _TEMPLATE:
        tmp = tempfile.mkdtemp(prefix="pcbclustertemplate-")
        try:
            path = os.path.join(tmp, "template.kicad_pcb")
            E.create_board(path, dict(SPEC))
            with open(path, "rb") as fh:
                _TEMPLATE["bytes"] = fh.read()
        finally:
            shutil.rmtree(tmp, ignore_errors=True)
    return _TEMPLATE["bytes"]


BODY = """(footprint "SYN:TwoPad" (version 20221018) (generator pcbnew)
  (layer "F.Cu")
  (tedit 5F6A2A2A)
  (attr smd)
  (fp_text reference "REF**" (at 0 -2.6) (layer "F.SilkS")
    (effects (font (size 0.7 0.7) (thickness 0.12))))
  (fp_text value "TwoPad" (at 0 2.6) (layer "F.Fab")
    (effects (font (size 0.7 0.7) (thickness 0.12))))
  (pad "1" smd rect (at -1.0 0.6) (size 0.9 0.6)
    (layers "F.Cu" "F.Paste" "F.Mask") (tstamp 11111111-1111-1111-1111-111111111111))
  (pad "2" smd rect (at 1.0 0.6) (size 0.9 0.6)
    (layers "F.Cu" "F.Paste" "F.Mask") (tstamp 22222222-2222-2222-2222-222222222222))
  (embedded_fonts no)
)
"""


class ClusterBase(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.mkdtemp(prefix="pcbcluster-")
        self._home = os.environ.get("PCB_AGENT_HOME")
        os.environ["PCB_AGENT_HOME"] = os.path.join(self.tmp, "home")
        self.board = os.path.join(self.tmp, "board.kicad_pcb")
        with open(self.board, "wb") as fh:
            fh.write(template_bytes())
        self.lib = os.path.join(self.tmp, "Syn.pretty")
        os.makedirs(self.lib)
        with open(os.path.join(self.lib, "TwoPad.kicad_mod"), "w",
                  encoding="utf-8", newline="\n") as fh:
            fh.write(BODY)

    def tearDown(self):
        if self._home is None:
            os.environ.pop("PCB_AGENT_HOME", None)
        else:
            os.environ["PCB_AGENT_HOME"] = self._home
        P.PLANS.clear()
        shutil.rmtree(self.tmp, ignore_errors=True)

    # ---------------------------------------------------------------- helpers
    def place(self, ref, x, y, rot=None):
        op = {"op": "add_footprint", "ref": ref, "x": x, "y": y,
              "library": self.lib, "name": "TwoPad"}
        if rot is not None:
            op["rot"] = rot
        return op

    def apply(self, *ops):
        plan = E.propose(self.board, list(ops))
        self.assertEqual(plan["blocking"], [], plan["blocking"])
        return plan, E.apply_plan(plan["plan_id"], confirm=True,
                                  options={"engine": False})

    def digest(self):
        with open(self.board, "rb") as fh:
            return hashlib.sha256(fh.read()).hexdigest()

    def editor(self):
        with open(self.board, "rb") as fh:
            doc = K.KiCadDocument.from_text(fh.read().decode("utf-8"), self.board)
        return O.Editor(doc)

    def at(self, ref):
        ed = self.editor()
        node = ed.footprint(ref)
        at = S.child(node, "at")
        return [S.arg_num(at, 1, 0.0), -S.arg_num(at, 2, 0.0),
                S.arg_num(at, 3, 0.0) or 0.0]

    def track_ends(self):
        ed = self.editor()
        out = []
        for node in ed.items("segment"):
            out.append((S.arg_num(S.child(node, "start"), 1),
                        -S.arg_num(S.child(node, "start"), 2),
                        S.arg_num(S.child(node, "end"), 1),
                        -S.arg_num(S.child(node, "end"), 2)))
        return out

    def two_parts(self):
        """U9 at (20,15), U10 at (26,15).

        The footprint body puts both pads at file-frame y = +0.6, and the file
        frame's Y axis points DOWN, so on screen (and in every assertion here)
        the pads sit at y - 0.6 = 14.4. Measured, not assumed: the same board
        read back through pcbnew reports pad 1 of U9 at x 19.0, y -14.4.
        """
        plan, report = self.apply(self.place("U9", 20.0, 15.0),
                                  self.place("U10", 26.0, 15.0))
        self.assertTrue(report["gates"]["postconditions"]["all_verified"])
        return plan


class MoveClusterTest(ClusterBase):
    def test_a_cluster_moves_its_parts_and_the_copper_between_them(self):
        self.two_parts()
        # pad 1 of each part is at (x-1, 14.4); a track joins the two pad centres
        plan, report = self.apply(
            {"op": "add_track", "x1": 19.0, "y1": 14.4, "x2": 25.0, "y2": 14.4,
             "width": 0.2, "layer": "F.Cu", "net": "SIG", "create_net": True})
        self.assertTrue(report["gates"]["postconditions"]["all_verified"])

        plan, report = self.apply(
            {"op": "move_cluster", "members": ["U9", "U10"], "dx": 2.0})
        detail = plan["operations"][0]["detail"]
        self.assertEqual(detail["members"], ["U9", "U10"])
        self.assertEqual(detail["copper_moved"], 1)
        self.assertEqual(detail["copper_orphaned"], 0)
        self.assertTrue(report["gates"]["postconditions"]["all_verified"])
        self.assertAlmostEqual(self.at("U9")[0], 22.0, places=6)
        self.assertAlmostEqual(self.at("U10")[0], 28.0, places=6)
        # the track rode along, so both ends are still on their pad centres
        self.assertEqual(self.track_ends(), [(21.0, 14.4, 27.0, 14.4)])

    def test_copper_that_would_be_left_behind_is_refused_by_default(self):
        self.two_parts()
        # starts on U9 pad 1 and runs off to the board edge: half in, half out
        self.apply({"op": "add_track", "x1": 19.0, "y1": 14.4, "x2": 5.0,
                    "y2": 14.4, "width": 0.2, "layer": "F.Cu", "net": "SIG",
                    "create_net": True})
        before = self.digest()
        # An op-level refusal is raised at plan time, not filed as a gate: the
        # plan is all-or-nothing, so there is no half-plan to report.
        with self.assertRaises(O.OpError) as ctx:
            E.propose(self.board, [{"op": "move_cluster",
                                    "members": ["U9", "U10"], "dx": 2.0}])
        self.assertEqual(ctx.exception.code, "ORPHAN_COPPER")
        self.assertIn("SIG", str(ctx.exception))
        self.assertEqual(self.digest(), before,
                         "a refused plan must not have written anything")
        self.assertAlmostEqual(self.at("U9")[0], 20.0, places=6)

    def test_the_orphan_cost_can_be_accepted_and_is_then_reported(self):
        self.two_parts()
        self.apply({"op": "add_track", "x1": 19.0, "y1": 14.4, "x2": 5.0,
                    "y2": 14.4, "width": 0.2, "layer": "F.Cu", "net": "SIG",
                    "create_net": True})
        plan, report = self.apply({"op": "move_cluster",
                                   "members": ["U9", "U10"], "dx": 2.0,
                                   "allow_orphan": True})
        detail = plan["operations"][0]["detail"]
        self.assertEqual(detail["copper_orphaned"], 1)
        self.assertEqual(detail["copper_orphaned_nets"], ["SIG"])
        self.assertAlmostEqual(self.at("U9")[0], 22.0, places=6)
        # and the stranded track really did stay put
        self.assertEqual(self.track_ends(), [(19.0, 14.4, 5.0, 14.4)])

    def test_a_cluster_rotation_turns_the_ring_about_its_own_centre(self):
        self.two_parts()
        plan, _ = self.apply({"op": "move_cluster", "members": ["U9", "U10"],
                              "rot": 90.0})
        detail = plan["operations"][0]["detail"]
        self.assertEqual(detail["centre"], [23.0, 15.0])
        # (20,15) about (23,15) by +90 is (-3,0) -> (0,-3): x stays, y drops
        self.assertAlmostEqual(self.at("U9")[0], 23.0, places=6)
        self.assertAlmostEqual(self.at("U9")[1], 12.0, places=6)
        self.assertAlmostEqual(self.at("U10")[0], 23.0, places=6)
        self.assertAlmostEqual(self.at("U10")[1], 18.0, places=6)
        self.assertAlmostEqual(self.at("U9")[2], 90.0, places=6)

    def test_a_pad_offset_follows_the_footprint_angle(self):
        """The measured convention: +90 turns a pad counter-clockwise on screen.

        U9 at (20,15) with rot 90: pad 1's own `(at -1.0 0.6)` is in the FILE
        frame (Y down), so its view-frame offset is (-1, -0.6); +90 turns that
        to (+0.6, -1), landing the pad at (20 + 0.6, 15 - 1) = (20.6, 14.0).
        The opposite sign puts pad 1 at (19.4, 16.0) - 2.0 mm away, on the far
        side of the part, and a cluster box built from that would miss the
        copper it is supposed to carry. pcbnew on the written board agrees.
        """
        self.apply(self.place("U9", 20.0, 15.0, rot=90.0))
        ed = self.editor()
        pads = O._fp_pads_abs(ed.footprint("U9"))
        by_num = {p[3]: (p[0], p[1]) for p in pads}
        self.assertAlmostEqual(by_num["1"][0], 20.6, places=6)
        self.assertAlmostEqual(by_num["1"][1], 14.0, places=6)
        self.assertAlmostEqual(by_num["2"][0], 20.6, places=6)
        self.assertAlmostEqual(by_num["2"][1], 16.0, places=6)

    def test_an_unknown_member_is_named_not_ignored(self):
        self.two_parts()
        with self.assertRaises(O.OpError) as ctx:
            E.propose(self.board, [{"op": "move_cluster",
                                    "members": ["U9", "U77"], "dx": 1.0}])
        self.assertEqual(ctx.exception.code, "REFERENCE_NOT_FOUND")
        self.assertIn("U77", str(ctx.exception))

    def test_a_no_op_move_is_refused(self):
        self.two_parts()
        with self.assertRaises(O.OpError) as ctx:
            E.propose(self.board, [{"op": "move_cluster",
                                    "members": ["U9"], "dx": 0, "dy": 0}])
        self.assertEqual(ctx.exception.code, "ARG_INVALID")
        self.assertIn("would not move anything", str(ctx.exception))


class SwapPadNetsTest(ClusterBase):
    def test_a_ring_of_two_swaps_the_nets_in_one_step(self):
        self.two_parts()
        self.apply({"op": "set_pad_net", "ref": "U9", "pad": "1", "net": "N_A",
                    "create_nets": True},
                   {"op": "set_pad_net", "ref": "U9", "pad": "2", "net": "N_B",
                    "create_nets": True})
        plan, report = self.apply({"op": "swap_pad_nets",
                                   "pads": [["U9", "1"], ["U9", "2"]]})
        detail = plan["operations"][0]["detail"]
        self.assertEqual(detail["before"], ["N_A", "N_B"])
        self.assertEqual(detail["after"], ["N_B", "N_A"])
        self.assertTrue(report["gates"]["postconditions"]["all_verified"])
        ed = self.editor()
        nets = {}
        for pad in S.children(ed.footprint("U9"), "pad"):
            nets[S.arg_text(pad, 1)] = O._item_net(pad)
        self.assertEqual(nets, {"1": "N_B", "2": "N_A"})

    def test_a_ring_of_three_rotates_rather_than_swapping_pairs(self):
        self.two_parts()
        ops = []
        for pad, net in (("1", "N_A"), ("2", "N_B")):
            ops.append({"op": "set_pad_net", "ref": "U9", "pad": pad, "net": net,
                        "create_nets": True})
        ops.append({"op": "set_pad_net", "ref": "U10", "pad": "1", "net": "N_C",
                    "create_nets": True})
        self.apply(*ops)
        plan, _ = self.apply({"op": "swap_pad_nets",
                              "pads": [["U9", "1"], ["U9", "2"], ["U10", "1"]]})
        detail = plan["operations"][0]["detail"]
        self.assertEqual(detail["before"], ["N_A", "N_B", "N_C"])
        self.assertEqual(detail["after"], ["N_B", "N_C", "N_A"])
        ed = self.editor()
        self.assertEqual(O._item_net(
            [p for p in S.children(ed.footprint("U10"), "pad")
             if S.arg_text(p, 1) == "1"][0]), "N_A")

    def test_a_ring_that_names_a_missing_pad_is_refused(self):
        self.two_parts()
        with self.assertRaises(O.OpError) as ctx:
            E.propose(self.board, [{"op": "swap_pad_nets",
                                    "pads": [["U9", "1"], ["U9", "9"]]}])
        self.assertEqual(ctx.exception.code, "PAD_NOT_FOUND")
        self.assertIn("pad", str(ctx.exception))


class ReserveRegionTest(ClusterBase):
    RECT = {"x0": 10.0, "y0": 10.0, "x1": 30.0, "y1": 20.0}

    def reserve(self, group, **kw):
        op = {"op": "reserve_region", "group": group, "layer": "F.Cu"}
        op.update(self.RECT)
        op.update(kw)
        return op

    def test_a_reservation_is_written_as_a_named_rule_area(self):
        plan, report = self.apply(self.reserve("DDR_D0"))
        detail = plan["operations"][0]["detail"]
        self.assertEqual(detail["label"], "reserve:DDR_D0")
        self.assertEqual(detail["layers"], ["F.Cu"])
        self.assertTrue(report["gates"]["postconditions"]["all_verified"])
        ed = self.editor()
        names = [O._zone_name(z) for z in ed.items("zone")]
        self.assertEqual(names, ["reserve:DDR_D0"])
        zone = ed.items("zone")[0]
        self.assertIsNotNone(S.child(zone, "keepout"))

    def test_a_label_that_is_not_a_net_list_calls_nothing_foreign(self):
        """'make room for the buck cluster' is a label, not a net list.

        Guessing which tokens are nets is how a checker starts inventing
        refusals, so the area is written and nothing is called foreign.
        """
        self.apply({"op": "add_track", "x1": 12.0, "y1": 12.0, "x2": 28.0,
                    "y2": 18.0, "width": 0.2, "layer": "F.Cu", "net": "SIG",
                    "create_net": True})
        plan, _ = self.apply(self.reserve("buck cluster"))
        detail = plan["operations"][0]["detail"]
        self.assertFalse(detail["nets_are_known"])
        self.assertEqual(detail["foreign_nets"], [])
        self.assertEqual(detail["occupied_tracks"], 1)

    def test_foreign_copper_in_the_band_is_refused_until_it_is_accepted(self):
        self.apply({"op": "add_track", "x1": 12.0, "y1": 12.0, "x2": 28.0,
                    "y2": 18.0, "width": 0.2, "layer": "F.Cu", "net": "SIG",
                    "create_net": True},
                   {"op": "add_track", "x1": 12.0, "y1": 19.0, "x2": 28.0,
                    "y2": 19.0, "width": 0.2, "layer": "F.Cu", "net": "CLK",
                    "create_net": True})
        before = self.digest()
        with self.assertRaises(O.OpError) as ctx:
            E.propose(self.board, [self.reserve("CLK")])
        self.assertEqual(ctx.exception.code, "RESERVE_OCCUPIED")
        self.assertIn("SIG", str(ctx.exception))
        self.assertEqual(self.digest(), before,
                         "a refused plan must not have written anything")

        plan, _ = self.apply(self.reserve("CLK", allow_existing=True))
        detail = plan["operations"][0]["detail"]
        self.assertEqual(detail["occupied_tracks"], 2)
        self.assertEqual(detail["foreign_nets"], ["SIG"])

    def test_the_same_label_cannot_be_reserved_twice(self):
        self.apply(self.reserve("DDR_D0"))
        with self.assertRaises(O.OpError) as ctx:
            E.propose(self.board, [self.reserve("DDR_D0")])
        self.assertEqual(ctx.exception.code, "RESERVE_EXISTS")

    def test_release_removes_every_layer_and_then_refuses_to_repeat(self):
        plan, _ = self.apply(self.reserve("DDR_D0", layers=["F.Cu", "B.Cu"]))
        self.assertEqual(plan["operations"][0]["detail"]["layers"],
                         ["F.Cu", "B.Cu"])
        self.assertEqual(len(self.editor().items("zone")), 2)
        plan, report = self.apply({"op": "release_region", "group": "DDR_D0"})
        self.assertEqual(plan["operations"][0]["detail"]["zones_removed"], 2)
        self.assertTrue(report["gates"]["postconditions"]["all_verified"])
        self.assertEqual(self.editor().items("zone"), [])
        with self.assertRaises(O.OpError) as ctx:
            E.propose(self.board, [{"op": "release_region", "group": "DDR_D0"}])
        self.assertEqual(ctx.exception.code, "RESERVE_NOT_FOUND")


if __name__ == "__main__":
    unittest.main()
