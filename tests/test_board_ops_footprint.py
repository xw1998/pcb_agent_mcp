"""Tests for the board-level `add_footprint` operation.

Why this file exists
--------------------
`add_footprint` is the only operation that copies geometry from *outside* the
board file, so it is the only one that can be wrong in ways the rest of the
write path cannot see: a pad angle that does not follow the footprint, a
back-side body that KiCad still draws on the front, a field stuck at board
coordinates, a net quietly invented from a typo. Every rule checked below was
measured on real KiCad output first (see the docstrings inside `ops.py`:
`_flip_body`, `_rotate_pad`), never reasoned out here.

The footprint bodies are synthetic so the tests stay offline and exact: a real
library part adds 64 pads of noise to an assertion about one angle. One
`skipUnless` case at the end places a real KiCad library footprint when KiCad
is installed, because "our parser accepts our own fixture" is not the same
claim as "it accepts what KiCad ships".

Engine (kicad-cli) checks are off here: the four gates that protect the file
are exercised in full, the engine gate is covered by `test_edit.py`.
"""
import os
import shutil
import tempfile
import unittest

from pcb_agent_mcp import edit as E
from pcb_agent_mcp.edit import ops as O
from pcb_agent_mcp.edit import plan as P
from pcb_agent_mcp.fidelity import engine as ENGINE
from pcb_agent_mcp.fidelity import kicad_doc as K
from pcb_agent_mcp.fidelity import sexpr as S

SPEC = {"width_mm": 60, "height_mm": 40, "copper_layers": 2, "engine": False}

_TEMPLATE = {}


def template_bytes():
    """One empty board, built once with the engine gate off (it is elsewhere)."""
    if "bytes" not in _TEMPLATE:
        tmp = tempfile.mkdtemp(prefix="pcbfptemplate-")
        try:
            path = os.path.join(tmp, "template.kicad_pcb")
            E.create_board(path, dict(SPEC))
            with open(path, "rb") as fh:
                _TEMPLATE["bytes"] = fh.read()
        finally:
            shutil.rmtree(tmp, ignore_errors=True)
    return _TEMPLATE["bytes"]


# A synthetic part with the four things that used to go wrong:
#   * a pad written with its own angle (90), so "does rotation accumulate?"
#   * an unnamed pad (a thermal tab), so "does a blank pad number get a net?"
#   * a Reference/Value the footprint author placed, so the field template is
#     the library's and not a guess
#   * a token this v7 board format does not define (`embedded_fonts`, which
#     KiCad 9/10 writes), so "is a loss reported?" has an answer
BODY = """(footprint "SYN:TestPart" (version 20221018) (generator pcbnew)
  (layer "F.Cu")
  (tedit 5F6A2A2A)
  (descr "synthetic part for the add_footprint tests")
  (tags "test")
  (attr smd)
  (fp_text reference "REF**" (at 0 -2.6) (layer "F.SilkS")
    (effects (font (size 0.7 0.7) (thickness 0.12))))
  (fp_text value "TestPart" (at 0 2.6) (layer "F.Fab")
    (effects (font (size 0.7 0.7) (thickness 0.12))))
  (fp_line (start -1.4 -0.9) (end 1.4 -0.9) (layer "F.SilkS") (width 0.12)
    (tstamp 11111111-1111-1111-1111-111111111111))
  (fp_line (start -1.4 0.9) (end 1.4 0.9) (layer "F.SilkS") (width 0.12)
    (tstamp 22222222-2222-2222-2222-222222222222))
  (pad "1" smd roundrect (at -1.0 0.6 90) (size 0.9 0.6)
    (layers "F.Cu" "F.Paste" "F.Mask") (roundrect_rratio 0.25)
    (tstamp 33333333-3333-3333-3333-333333333333))
  (pad "2" smd roundrect (at 1.0 0.6) (size 0.9 0.6)
    (layers "F.Cu" "F.Paste" "F.Mask") (roundrect_rratio 0.25)
    (tstamp 44444444-4444-4444-4444-444444444444))
  (pad "" smd rect (at 0 -0.6) (size 1.2 0.8)
    (layers "F.Cu" "F.Mask") (tstamp 55555555-5555-5555-5555-555555555555))
  (embedded_fonts no)
)
"""


class FootprintTestBase(unittest.TestCase):
    """Sandbox + helpers. No tests of its own: both classes below inherit it."""

    def setUp(self):
        self.tmp = tempfile.mkdtemp(prefix="pcbfp-")
        self._home = os.environ.get("PCB_AGENT_HOME")
        os.environ["PCB_AGENT_HOME"] = os.path.join(self.tmp, "home")
        self.board = os.path.join(self.tmp, "board.kicad_pcb")
        with open(self.board, "wb") as fh:
            fh.write(template_bytes())
        self.lib = os.path.join(self.tmp, "Syn.pretty")
        os.makedirs(self.lib)
        with open(os.path.join(self.lib, "TestPart.kicad_mod"), "w",
                  encoding="utf-8", newline="\n") as fh:
            fh.write(BODY)

    def tearDown(self):
        if self._home is None:
            os.environ.pop("PCB_AGENT_HOME", None)
        else:
            os.environ["PCB_AGENT_HOME"] = self._home
        P.PLANS.clear()
        shutil.rmtree(self.tmp, ignore_errors=True)

    # ------------------------------------------------------------ helpers
    def op(self, **kw):
        base = {"op": "add_footprint", "ref": "U9", "x": 20.0, "y": 15.0,
                "library": self.lib, "name": "TestPart"}
        base.update(kw)
        return base

    def apply(self, *ops, **kw):
        """propose + apply with the engine gate off; returns the apply report."""
        plan = E.propose(self.board, list(ops), kw.get("options"))
        self.assertEqual(plan["blocking"], [], plan["blocking"])
        report = E.apply_plan(plan["plan_id"], confirm=True,
                              options=dict(kw.get("apply_options") or {},
                                           engine=False))
        return plan, report

    def reread(self):
        with open(self.board, "rb") as fh:
            return K.KiCadDocument.from_text(fh.read().decode("utf-8"), self.board)

    def footprint(self, ref="U9"):
        ed = O.Editor(self.reread())
        return ed.footprint(ref)

class FootprintOpTest(FootprintTestBase):
    # ------------------------------------------------------------ placement
    def test_a_part_is_placed_and_the_gates_verify_it(self):
        plan, report = self.apply(self.op())
        gates = report["gates"]
        self.assertTrue(gates["bytes_written"]["ok"])
        self.assertTrue(gates["token_identity"]["ok"])
        self.assertTrue(gates["postconditions"]["all_verified"],
                        gates["postconditions"])
        detail = plan["operations"][0]["detail"]
        self.assertEqual(detail["pads"], 3)
        self.assertEqual(detail["at"], [20.0, 15.0, 0.0])
        fp = self.footprint()
        at = S.child(fp, "at")
        self.assertAlmostEqual(S.arg_num(at, 1), 20.0, places=6)
        self.assertAlmostEqual(S.arg_num(at, 2), -15.0, places=6)   # file frame
        self.assertEqual(S.arg_text(S.child(fp, "property"), 2) or
                         S.arg_text(S.child(fp, "fp_text"), 2), "U9")
        self.assertEqual(len(S.children(fp, "pad")), 3)

    def test_the_library_body_is_copied_not_invented(self):
        detail = self.apply(self.op())[0]["operations"][0]["detail"]
        self.assertEqual(detail["source"],
                         os.path.join(self.lib, "TestPart.kicad_mod"))
        self.assertEqual(detail["lib_id"], "Syn:TestPart")
        fp = self.footprint()
        # the silk the library drew is in the board, unchanged
        lines = [(S.arg_num(S.child(l, "start"), 1), S.arg_num(S.child(l, "end"), 1))
                 for l in S.children(fp, "fp_line")]
        self.assertEqual(lines, [(-1.4, 1.4), (-1.4, 1.4)])

    def test_a_missing_footprint_definition_is_an_error_not_an_empty_part(self):
        with self.assertRaises(O.OpError) as ctx:
            self.apply(self.op(name="DoesNotExist"))
        self.assertEqual(ctx.exception.code, "FILE_NOT_FOUND")

    def test_a_directory_without_a_name_is_refused(self):
        with self.assertRaises(O.OpError) as ctx:
            self.apply(self.op(name=None))
        self.assertEqual(ctx.exception.code, "ARG_MISSING")

    def test_a_reference_already_on_the_board_is_refused(self):
        self.apply(self.op(ref="U1"))
        with self.assertRaises(O.OpError) as ctx:
            self.apply(self.op(ref="U1", x=30.0))
        self.assertEqual(ctx.exception.code, "REFERENCE_EXISTS")

    # ------------------------------------------------------------ nets
    def test_a_typo_in_the_net_map_is_refused(self):
        with self.assertRaises(O.OpError) as ctx:
            self.apply(self.op(pad_nets={"7": "VCC"}))
        self.assertEqual(ctx.exception.code, "PAD_NOT_FOUND")
        self.assertIn("7", str(ctx.exception))

    def test_a_missing_net_is_refused_unless_the_caller_means_it(self):
        with self.assertRaises(O.OpError) as ctx:
            self.apply(self.op(pad_nets={"1": "VCC"}))
        self.assertEqual(ctx.exception.code, "NET_NOT_FOUND")

        plan, _ = self.apply(self.op(pad_nets={"1": "VCC", "2": "GND"},
                                     create_nets=True))
        assigned = plan["operations"][0]["detail"]["nets_assigned"]
        self.assertEqual([assigned[k][1] for k in sorted(assigned)], ["VCC", "GND"])

        nets = {}
        for pad in S.children(self.footprint(), "pad"):
            number = S.arg_text(pad, 1) or ""
            node = S.child(pad, "net")
            nets[number] = S.arg_text(node, 2) if node is not None else None
        self.assertEqual(nets, {"1": "VCC", "2": "GND", "": None})

    def test_an_unnamed_pad_stays_net_less(self):
        # the map cannot name pad "" - so the tab is reported as unconnected by
        # DRC instead of being wired to whatever was nearest
        self.apply(self.op(pad_nets={"1": "VCC"}, create_nets=True))
        pads = S.children(self.footprint(), "pad")
        tab = [p for p in pads if (S.arg_text(p, 1) or "") == ""][0]
        self.assertIsNone(S.child(tab, "net"))

    # ------------------------------------------------------------ rotation
    def test_a_rotated_footprint_carries_its_pad_angles(self):
        # pad 1 is stored at 90 in the library; a footprint at 90 must leave it
        # at 180, and pad 2 (0) must become 90. Leaving the library angles alone
        # makes 0.4 x 1.35 pads overlap by 0.7 mm on the USB connector.
        self.apply(self.op(rot=90))
        fp = self.footprint()
        self.assertAlmostEqual(S.arg_num(S.child(fp, "at"), 3), 90.0, places=6)
        angles = {}
        for pad in S.children(fp, "pad"):
            angles[S.arg_text(pad, 1) or ""] = S.arg_num(S.child(pad, "at"), 3, 0.0)
        self.assertAlmostEqual(angles["1"], 180.0, places=6)
        self.assertAlmostEqual(angles["2"], 90.0, places=6)

    def test_field_angles_follow_the_rotation(self):
        # a stored field angle is absolute: a 90 deg part with a 0 deg field
        # prints a horizontal reference across a vertical body
        self.apply(self.op(rot=270))
        for kind in ("property", "fp_text"):
            for field in S.children(self.footprint(), kind):
                at = S.child(field, "at")
                self.assertAlmostEqual(S.arg_num(at, 3, 0.0), 270.0, places=6)

    # ------------------------------------------------------------ back side
    def test_a_back_side_part_is_stored_mirrored(self):
        plan, _ = self.apply(self.op(layer="B.Cu", rot=0))
        detail = plan["operations"][0]["detail"]
        self.assertAlmostEqual(detail["rotation_in_file"], 180.0, places=6)

        fp = self.footprint()
        self.assertEqual(S.arg_text(S.child(fp, "layer"), 1), "B.Cu")
        # library pads: "1" at (-1.0, 0.6, 90), "2" at (1.0, 0.6, 0),
        # the tab at (0, -0.6, 0) - so dy is what it was, negated
        want_y = {"1": -0.6, "2": -0.6, "": 0.6}
        want_angle = {"1": 180.0 - (90.0 + 0.0), "2": 180.0 - (0.0 + 0.0),
                      "": 180.0 - (0.0 + 0.0)}
        for pad in S.children(fp, "pad"):
            number = S.arg_text(pad, 1) or ""
            at = S.child(pad, "at")
            self.assertAlmostEqual(S.arg_num(at, 2), want_y[number], places=6)
            self.assertAlmostEqual(S.arg_num(at, 3, 0.0), want_angle[number],
                                   places=6)
            layers = S.child(pad, "layers")
            names = [it.text for it in layers.items[1:] if isinstance(it, S.Atom)]
            self.assertIn("B.Cu", names)
            self.assertNotIn("F.Cu", names)
        # text renders mirrored on the back, as KiCad writes it
        for kind in ("property", "fp_text"):
            for field in S.children(fp, kind):
                justify = S.child(S.child(field, "effects") or S.Seq(), "justify")
                self.assertIsNotNone(justify, "%s has no justify" % kind)
                self.assertTrue(any(isinstance(it, S.Atom) and it.text == "mirror"
                                    for it in justify.items[1:]))

    def test_a_back_side_part_rotated_90_gets_the_90_degree_mirror(self):
        # The claim being pinned: KiCad stores `180 - angle`, so a caller asking
        # for 90 on the back gets 90 in the file, not 270.
        plan, _ = self.apply(self.op(layer="B.Cu", rot=90))
        self.assertAlmostEqual(plan["operations"][0]["detail"]["rotation_in_file"],
                               90.0, places=6)
        self.assertAlmostEqual(S.arg_num(S.child(self.footprint(), "at"), 3),
                               90.0, places=6)

    def test_the_verifier_accepts_the_mirrored_angle(self):
        # postconditions must not fight the mirror rule (they compare 180-rot too)
        _, report = self.apply(self.op(layer="B.Cu", rot=45))
        post = report["gates"]["postconditions"]["operations"][0]
        self.assertTrue(post["verified"], post["detail"])

    # ------------------------------------------------------------ honesty
    def test_a_token_this_board_format_lacks_is_reported(self):
        plan, _ = self.apply(self.op())
        dropped = plan["operations"][0]["detail"]["dropped_for_this_board_format"]
        self.assertEqual(dropped, ["embedded_fonts"])
        with open(self.board, "r", encoding="utf-8") as fh:
            self.assertNotIn("embedded_fonts", fh.read())

    def test_the_field_size_is_the_callers_choice(self):
        self.apply(self.op(field_size=0.8))
        fp = self.footprint()
        ref = [f for kind in ("property", "fp_text")
               for f in S.children(fp, kind)
               if S.arg_text(f, 1) in ("Reference", "reference")][0]
        size = S.child(S.child(ref, "effects"), "font")
        size = S.child(size, "size")
        self.assertAlmostEqual(S.arg_num(size, 1), 0.8, places=6)

    def test_a_field_size_of_zero_is_refused(self):
        with self.assertRaises(O.OpError) as ctx:
            self.apply(self.op(field_size=0))
        self.assertEqual(ctx.exception.code, "ARG_INVALID")

    def test_a_layer_that_is_not_a_side_is_refused(self):
        with self.assertRaises(O.OpError) as ctx:
            self.apply(self.op(layer="In1.Cu"))
        self.assertEqual(ctx.exception.code, "LAYER_NOT_ALLOWED")

    def test_the_write_path_verifier_says_the_same_thing_as_the_reader(self):
        # run_all + verify_all on one in-memory copy: no file, no KiCad. This is
        # the pair the MCP tools call, so they must agree on their own.
        doc = K.KiCadDocument.from_text(
            template_bytes().decode("utf-8"), self.board)
        ops = [self.op()]
        O.run_all(doc, ops)
        verified = O.verify_all(
            K.KiCadDocument.from_text(doc.to_text(), self.board), ops)
        self.assertTrue(verified["all_verified"], verified)


def _kicad_footprint_lib():
    exe = ENGINE.find_kicad_cli()
    if not exe:
        return None
    root = os.path.dirname(os.path.dirname(exe))
    path = os.path.join(root, "share", "kicad", "footprints")
    return path if os.path.isdir(path) else None


@unittest.skipUnless(_kicad_footprint_lib(),
                     "KiCad's own footprint libraries are not installed here")
class RealLibraryTest(FootprintTestBase):
    """The same op against what KiCad actually ships.

    The synthetic fixture proves the rules; this proves the parser survives a
    64-pad LQFP at 45 degrees, which is where a copied body stops being trivial.
    """

    def test_a_real_lqfp_64_places_and_verifies(self):
        lib = os.path.join(_kicad_footprint_lib(), "Package_QFP.pretty")
        pad_nets = dict(("" if i is None else str(i), "GND") for i in range(1, 65))
        ops = [self.op(ref="U1", x=25.0, y=20.0, rot=45,
                       library=lib, name="LQFP-64_10x10mm_P0.5mm",
                       pad_nets=pad_nets, create_nets=True)]
        plan, report = self.apply(*ops)
        detail = plan["operations"][0]["detail"]
        self.assertEqual(detail["pads"], 64)
        self.assertTrue(report["gates"]["postconditions"]["all_verified"],
                        report["gates"]["postconditions"])
        self.assertEqual(len(S.children(self.footprint("U1"), "pad")), 64)


if __name__ == "__main__":
    unittest.main()
