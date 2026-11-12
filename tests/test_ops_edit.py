"""Tests for the operations that edit parts and copper already on the board.

The nine operations added here are the ones a real layout needs halfway through:
remove a part, flip a part to the back, delete a net or a pour, set a pour's
priority, put a net in a class, resize a pad, change a field, draw an arc on a
mechanical layer.

What every test holds the code to:

* **the gate re-reads the written file** - not "run() did not raise". Each test
  goes through `propose` + `apply_plan` and asserts the postcondition gate, so a
  write that the parser cannot read back fails here;
* **a refusal names what would have gone wrong** - deleting a net still used by
  copper, resizing a pad that is not there, a selector-less "delete every zone";
* **flipping a part is KiCad's own geometry** - `set_footprint_layer` is checked
  against `pcbnew` flipping the same footprint (when KiCad is installed), and
  against itself in a round trip, because a mirrored pad that is 3 mm off looks
  perfectly plausible in the file.
"""
import io
import os
import shutil
import subprocess
import sys
import tempfile
import unittest

sys.path.insert(0, os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "src"))

from pcb_agent_mcp import edit as E
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
    (35 "F.Fab" user)
    (36 "B.SilkS" user)
    (37 "F.SilkS" user)
    (44 "Edge.Cuts" user)
    (48 "Dwgs.User" user)
  )
  (net 0 "")
  (net 1 "GND")
  (net 2 "VCC")
  (net_class "Default" ""
    (clearance 0.2) (trace_width 0.25) (via_dia 0.6) (via_drill 0.3))
  (net_class "Power" ""
    (clearance 0.4) (trace_width 0.5) (via_dia 0.8) (via_drill 0.4))
  (gr_line (start 0 0) (end 40 0) (layer "Edge.Cuts") (width 0.1) (tstamp 00000000-0000-0000-0000-000000000001))
  (gr_line (start 40 0) (end 40 30) (layer "Edge.Cuts") (width 0.1) (tstamp 00000000-0000-0000-0000-000000000002))
  (gr_line (start 40 30) (end 0 30) (layer "Edge.Cuts") (width 0.1) (tstamp 00000000-0000-0000-0000-000000000003))
  (gr_line (start 0 30) (end 0 0) (layer "Edge.Cuts") (width 0.1) (tstamp 00000000-0000-0000-0000-000000000004))
  (footprint "Test:R_0805" (layer "F.Cu") (at 10 10) (tstamp 00000000-0000-0000-0000-000000000010)
    (attr smd)
    (fp_text reference "R1" (at 0 -2) (layer "F.SilkS") (tstamp 00000000-0000-0000-0000-000000000011))
    (fp_text value "10k" (at 0 2) (layer "F.Fab") (tstamp 00000000-0000-0000-0000-000000000012))
    (pad "1" smd roundrect (at -0.95 0) (size 1.2 1.4) (layers "F.Cu" "F.Paste" "F.Mask") (roundrect_rratio 0.25) (net 1 "GND") (tstamp 00000000-0000-0000-0000-000000000013))
    (pad "2" smd roundrect (at 0.95 0) (size 1.2 1.4) (layers "F.Cu" "F.Paste" "F.Mask") (roundrect_rratio 0.25) (net 2 "VCC") (tstamp 00000000-0000-0000-0000-000000000014))
  )
  (footprint "Test:QFN" (layer "F.Cu") (at 20 15 30) (tstamp 00000000-0000-0000-0000-000000000020)
    (attr smd)
    (fp_text reference "U1" (at 0 -3 30) (layer "F.SilkS") (tstamp 00000000-0000-0000-0000-000000000021))
    (fp_text value "QFN" (at 0 3 30) (layer "F.Fab") (tstamp 00000000-0000-0000-0000-000000000022))
    (fp_line (start -1.5 1) (end 1.5 1) (layer "F.SilkS") (width 0.12) (tstamp 00000000-0000-0000-0000-000000000023))
    (pad "1" smd rect (at -1 1.5 0) (size 1 0.6) (layers "F.Cu" "F.Paste" "F.Mask") (net 1 "GND") (tstamp 00000000-0000-0000-0000-000000000024))
    (pad "2" smd rect (at 1 -1.5 0) (size 1 0.6) (layers "F.Cu" "F.Paste" "F.Mask") (net 2 "VCC") (tstamp 00000000-0000-0000-0000-000000000025))
  )
  (segment (start 11 10) (end 14 10) (width 0.25) (layer "F.Cu") (net 2) (tstamp 00000000-0000-0000-0000-000000000030))
  (via (at 14 10) (size 0.6) (drill 0.3) (layers "F.Cu" "B.Cu") (net 2) (tstamp 00000000-0000-0000-0000-000000000031))
  (zone (net 1) (net_name "GND") (layer "F.Cu") (tstamp 00000000-0000-0000-0000-000000000040) (hatch edge 0.5)
    (connect_pads (clearance 0.2))
    (min_thickness 0.25)
    (fill yes)
    (polygon (pts (xy 2 2) (xy 18 2) (xy 18 18) (xy 2 18)))
  )
  (zone (net 2) (net_name "VCC") (layer "B.Cu") (tstamp 00000000-0000-0000-0000-000000000041) (hatch edge 0.5)
    (min_thickness 0.25)
    (polygon (pts (xy 20 2) (xy 38 2) (xy 38 28) (xy 20 28)))
  )
)
"""


class EditBase(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.mkdtemp(prefix="pcbops-")
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

    # ---------------------------------------------------------------- helpers
    def apply(self, *ops):
        plan = E.propose(self.board, list(ops))
        self.assertEqual(plan["blocking"], [], plan["blocking"])
        report = E.apply_plan(plan["plan_id"], confirm=True,
                             options=dict(engine=False))
        self.assertTrue(report["gates"]["postconditions"]["all_verified"],
                        report["gates"]["postconditions"])
        return plan, report

    def reread(self):
        with open(self.board, "rb") as fh:
            return K.KiCadDocument.from_text(fh.read().decode("utf-8"), self.board)

    def editor(self):
        return O.Editor(self.reread())

    def footprint(self, ref="R1"):
        return self.editor().footprint(ref)

    def net_names(self):
        return sorted(n for n in self.editor().net_by_name if n)


class DeleteFootprint(EditBase):
    def test_the_part_and_its_pads_are_gone(self):
        plan, _ = self.apply({"op": "delete_footprint", "ref": "R1"})
        detail = plan["operations"][0]["detail"]
        self.assertEqual(detail["pads_removed"], 2)
        self.assertEqual(detail["nets_released"], ["GND", "VCC"])
        self.assertEqual(self.editor().footprints()[0].__class__, S.Seq)
        with self.assertRaises(O.OpError) as ctx:
            self.footprint("R1")
        self.assertEqual(ctx.exception.code, "REFERENCE_NOT_FOUND")
        # the other part is untouched
        self.assertEqual(self.editor().footprint_ref(self.footprint("U1")), "U1")

    def test_a_missing_reference_is_refused(self):
        with self.assertRaises(O.OpError) as ctx:
            self.apply({"op": "delete_footprint", "ref": "NOPE"})
        self.assertEqual(ctx.exception.code, "REFERENCE_NOT_FOUND")

    def test_net_declarations_survive(self):
        before = self.net_names()
        self.apply({"op": "delete_footprint", "ref": "R1"})
        self.assertEqual(self.net_names(), before)


class FlipFootprint(EditBase):
    OP = {"op": "set_footprint_layer", "ref": "U1", "layer": "B.Cu"}

    def pads(self):
        fp = self.footprint("U1")
        out = {}
        for pad in S.children(fp, "pad"):
            at = S.child(pad, "at")
            out[S.arg_text(pad, 1)] = {
                "dx": S.arg_num(at, 1, 0.0), "dy": S.arg_num(at, 2, 0.0),
                "angle": S.arg_num(at, 3) or 0.0,
                "layers": [it.text for it in S.child(pad, "layers").items[1:]
                           if isinstance(it, S.Atom)]}
        return out

    def fp_state(self):
        fp = self.footprint("U1")
        at = S.child(fp, "at")
        return {"layer": S.arg_text(S.child(fp, "layer"), 1),
                "angle": S.arg_num(at, 3) or 0.0}

    def test_the_part_moves_to_the_back_and_the_file_angle_mirrors(self):
        before = self.fp_state()
        self.assertEqual(before["layer"], "F.Cu")
        self.apply(self.OP)
        after = self.fp_state()
        self.assertEqual(after["layer"], "B.Cu")
        self.assertAlmostEqual(after["angle"], 180.0 - before["angle"], places=6)

    def test_pads_mirror_and_keep_their_side_when_they_are_through_hole(self):
        before = self.pads()
        self.apply(self.OP)
        after = self.pads()
        for number, pad in before.items():
            got = after[number]
            self.assertAlmostEqual(got["dx"], pad["dx"], places=9)
            self.assertAlmostEqual(got["dy"], -pad["dy"], places=9)
            # the file stores a pad's angle **absolute**, so it flips on its own;
            # subtracting the footprint angle as well put pads 30 deg out here
            self.assertAlmostEqual(got["angle"], (180.0 - pad["angle"]) % 360.0,
                                   places=6)
            self.assertEqual(sorted(got["layers"]), ["B.Cu", "B.Mask", "B.Paste"])

    def test_silk_and_fields_follow_the_part(self):
        fp_before = self.footprint("U1")
        line_before = S.child(fp_before, "fp_line")
        ref_before = S.child(fp_before, "fp_text")
        self.apply(self.OP)
        fp = self.footprint("U1")
        line = S.child(fp, "fp_line")
        self.assertEqual(S.arg_text(S.child(line, "layer"), 1), "B.SilkS")
        self.assertAlmostEqual(S.arg_num(S.child(line, "start"), 2),
                               -S.arg_num(S.child(line_before, "start"), 2),
                               places=9)
        ref = S.child(fp, "fp_text")
        self.assertEqual(S.arg_text(S.child(ref, "layer"), 1), "B.SilkS")
        justify = S.child(S.child(ref, "effects"), "justify")
        self.assertTrue(any(it.text == "mirror" for it in justify.items[1:]),
                        "back-side text must be justified mirrored")

    def test_flipping_twice_returns_the_original_geometry(self):
        before = (self.fp_state(), self.pads())
        self.apply(self.OP)
        self.apply({"op": "set_footprint_layer", "ref": "U1", "layer": "F.Cu"})
        after = (self.fp_state(), self.pads())
        self.assertEqual(after[0]["layer"], before[0]["layer"])
        self.assertAlmostEqual(after[0]["angle"], before[0]["angle"], places=6)
        for number, pad in before[1].items():
            self.assertAlmostEqual(after[1][number]["dx"], pad["dx"], places=9)
            self.assertAlmostEqual(after[1][number]["dy"], pad["dy"], places=9)
            self.assertAlmostEqual(after[1][number]["angle"], pad["angle"],
                                   places=6)
            self.assertEqual(sorted(after[1][number]["layers"]),
                             sorted(pad["layers"]))
        justify = S.child(S.child(S.child(self.footprint("U1"), "fp_text"),
                                  "effects"), "justify")
        self.assertFalse(justify is not None and
                         any(it.text == "mirror" for it in justify.items[1:]),
                         "mirror must be removed when the part comes back")

    def test_asking_for_the_side_it_is_already_on_changes_nothing(self):
        plan, _ = self.apply({"op": "set_footprint_layer", "ref": "U1",
                              "layer": "F.Cu"})
        self.assertFalse(plan["operations"][0]["detail"]["changed"])
        self.assertEqual(self.fp_state()["layer"], "F.Cu")

    def test_a_non_side_layer_is_refused(self):
        with self.assertRaises(O.OpError) as ctx:
            self.apply({"op": "set_footprint_layer", "ref": "U1",
                        "layer": "F.SilkS"})
        self.assertEqual(ctx.exception.code, "LAYER_NOT_ALLOWED")


_DUMP = r"""
import json, sys, pcbnew
b = pcbnew.LoadBoard(sys.argv[1])
flip = sys.argv[2] == "flip"
out = []
for fp in b.GetFootprints():
    if fp.GetReference() != "U1":
        continue
    if flip:
        fp.Flip(fp.GetPosition(), False)
    for p in fp.Pads():
        q = p.GetPosition()
        out.append({"num": p.GetNumber(), "x": q.x / 1e6, "y": q.y / 1e6,
                    "angle": p.GetOrientation().AsDegrees()})
    out.append({"ref": fp.GetReference(), "angle": fp.GetOrientation().AsDegrees(),
                "layer": fp.GetLayerName()})
json.dump(out, sys.stdout)
"""


@unittest.skipUnless(ENGINE.find_kicad_cli(), "KiCad (pcbnew) is not installed here")
class FlipAgainstPcbnew(EditBase):
    """Our flip must equal the one `pcbnew` performs, pad by pad."""

    def _kicad_python(self):
        exe = ENGINE.find_kicad_cli()
        cand = os.path.join(os.path.dirname(exe), "python.exe")
        return cand if os.path.isfile(cand) else None

    def _dump(self, path, flip):
        env = {k: v for k, v in os.environ.items()
               if k not in ("PYTHONPATH", "PYTHONHOME")}
        proc = subprocess.run([self._kicad_python(), "-E", "-s", "-c", _DUMP,
                               path, "flip" if flip else "keep"],
                              capture_output=True, text=True, timeout=180, env=env)
        self.assertEqual(proc.returncode, 0, proc.stderr[-800:])
        import json
        return json.loads(proc.stdout)

    def test_pad_positions_and_angles_match_pcbnew(self):
        py = self._kicad_python()
        if not py:
            self.skipTest("KiCad's own python is not next to kicad-cli")
        truth = self._dump(self.board, True)
        self.apply({"op": "set_footprint_layer", "ref": "U1", "layer": "B.Cu"})
        mine = self._dump(self.board, False)
        t_pads = {e["num"]: e for e in truth if "num" in e}
        m_pads = {e["num"]: e for e in mine if "num" in e}
        self.assertEqual(sorted(t_pads), sorted(m_pads))
        for number, want in t_pads.items():
            got = m_pads[number]
            self.assertAlmostEqual(got["x"], want["x"], places=3,
                                   msg="pad %s x" % number)
            self.assertAlmostEqual(got["y"], want["y"], places=3,
                                   msg="pad %s y" % number)
            slop = abs((got["angle"] - want["angle"]) % 360.0)
            self.assertLess(min(slop, 360.0 - slop), 0.02,
                            "pad %s angle %.3f vs pcbnew %.3f"
                            % (number, got["angle"], want["angle"]))
        t_fp = [e for e in truth if "ref" in e][0]
        m_fp = [e for e in mine if "ref" in e][0]
        self.assertEqual(m_fp["layer"], t_fp["layer"])
        self.assertAlmostEqual(m_fp["angle"] % 360.0, t_fp["angle"] % 360.0,
                               places=3)


class DeleteNet(EditBase):
    def test_a_net_still_used_by_copper_is_refused(self):
        with self.assertRaises(O.OpError) as ctx:
            self.apply({"op": "delete_net", "net": "GND"})
        self.assertEqual(ctx.exception.code, "NET_IN_USE")
        self.assertIn("pads", str(ctx.exception))

    def test_force_unassigns_every_user_then_removes_the_declaration(self):
        plan, _ = self.apply({"op": "delete_net", "net": "GND", "force": True})
        detail = plan["operations"][0]["detail"]
        self.assertEqual(detail["uses_before"]["pads"], 2)
        self.assertEqual(detail["uses_before"]["zones"], 1)
        ed = self.editor()
        self.assertNotIn("GND", ed.net_by_name)
        for fp in ed.footprints():
            for pad in S.children(fp, "pad"):
                net = S.child(pad, "net")
                self.assertNotEqual(S.arg_text(net, 1), detail["net_code"])
        self.assertFalse([z for z in ed.items("zone")
                          if S.arg_text(S.child(z, "net_name"), 1) == "GND"])

    def test_an_unused_net_goes_without_force(self):
        self.apply({"op": "add_net", "name": "SPARE"})
        self.apply({"op": "delete_net", "net": "SPARE"})
        self.assertNotIn("SPARE", self.net_names())

    def test_the_unnamed_net_is_not_editable(self):
        with self.assertRaises(O.OpError) as ctx:
            self.apply({"op": "delete_net", "net": "", "force": True})
        self.assertIn(ctx.exception.code, ("NET_NOT_FOUND", "ARG_MISSING",
                                           "NET_NOT_DELETABLE"))

    def test_a_missing_net_is_refused(self):
        with self.assertRaises(O.OpError) as ctx:
            self.apply({"op": "delete_net", "net": "NOPE"})
        self.assertEqual(ctx.exception.code, "NET_NOT_FOUND")


class Zones(EditBase):
    def zones(self):
        return self.editor().items("zone")

    def test_delete_needs_a_selector(self):
        with self.assertRaises(O.OpError) as ctx:
            self.apply({"op": "delete_zone"})
        self.assertEqual(ctx.exception.code, "ARG_MISSING")

    def test_delete_by_net_and_by_layer(self):
        plan, _ = self.apply({"op": "delete_zone", "net": "GND"})
        self.assertEqual(plan["operations"][0]["detail"]["zones_removed"], 1)
        self.assertEqual([S.arg_text(S.child(z, "net_name"), 1)
                          for z in self.zones()], ["VCC"])
        self.apply({"op": "delete_zone", "layer": "B.Cu"})
        self.assertEqual(self.zones(), [])

    def test_a_selector_that_matches_nothing_is_refused(self):
        with self.assertRaises(O.OpError) as ctx:
            self.apply({"op": "delete_zone", "layer": "F.SilkS"})
        self.assertEqual(ctx.exception.code, "TARGET_NOT_FOUND")

    def test_priority_is_written_where_kicad_reads_it(self):
        plan, _ = self.apply({"op": "set_zone_priority", "net": "GND",
                              "priority": 3})
        self.assertEqual(plan["operations"][0]["detail"]["zones_changed"], 1)
        gnd = [z for z in self.zones()
               if S.arg_text(S.child(z, "net_name"), 1) == "GND"][0]
        self.assertEqual(int(S.arg_num(S.child(gnd, "priority"), 1)), 3)
        vcc = [z for z in self.zones()
               if S.arg_text(S.child(z, "net_name"), 1) == "VCC"][0]
        self.assertIsNone(S.child(vcc, "priority"))

    def test_priority_of_zero_is_allowed_and_replaces(self):
        self.apply({"op": "set_zone_priority", "net": "GND", "priority": 2})
        self.apply({"op": "set_zone_priority", "net": "GND", "priority": 0})
        gnd = [z for z in self.zones()
               if S.arg_text(S.child(z, "net_name"), 1) == "GND"][0]
        self.assertEqual(int(S.arg_num(S.child(gnd, "priority"), 1)), 0)


class ZoneParams(EditBase):
    """`set_zone_params` changes what the pour MEANS, not just a number.

    `filled_areas_thickness no` is the field KiCad 10 reads to decide whether a
    stored filled_polygon is the copper's edge or a centreline to be stroked by
    min_thickness/2.  Without it the same polygon is judged 0.125 mm thicker on
    every side than the file shows, which is what made 386 clearance errors on
    the F412RG board - so the test asserts the written token, and (when KiCad is
    installed) asks pcbnew whether it agrees.
    """

    def zone(self, net):
        for z in self.editor().items("zone"):
            if S.arg_text(S.child(z, "net_name"), 1) == net:
                return z
        raise AssertionError("no zone on %r" % net)

    def _add_filled_polygon(self, net):
        doc = K.KiCadDocument.from_text(
            open(self.board, "rb").read().decode("utf-8"), self.board)
        for z in S.children(doc.board(), "zone"):
            if S.arg_text(S.child(z, "net_name"), 1) != net:
                continue
            pts = S.Seq([S.Atom("pts")] + [
                S.Seq([S.Atom("xy"), S.Atom(str(x)), S.Atom(str(y))])
                for x, y in ((3, 3), (10, 3), (10, 10), (3, 10))])
            S.append_child(z, S.Seq([S.Atom("filled_polygon"),
                                     S.Seq([S.Atom("layer"), S.Atom("F.Cu", True)]),
                                     pts]))
        with open(self.board, "wb") as fh:
            fh.write(doc.to_bytes())

    def test_filled_areas_thickness_is_written_where_kicad_reads_it(self):
        plan, _ = self.apply({"op": "set_zone_params", "net": "GND",
                              "filled_areas_thickness": False})
        detail = plan["operations"][0]["detail"]
        self.assertEqual(detail["zones_changed"], 1)
        self.assertTrue(detail["refill_required"])
        gnd = self.zone("GND")
        self.assertEqual(S.arg_text(S.child(gnd, "filled_areas_thickness"), 1), "no")
        # the other zone is not touched by a net selector
        self.assertIsNone(S.child(self.zone("VCC"), "filled_areas_thickness"))

    def test_yes_is_the_other_spelling_and_is_accepted(self):
        self.apply({"op": "set_zone_params", "net": "GND",
                    "filled_areas_thickness": "yes"})
        self.assertEqual(S.arg_text(S.child(self.zone("GND"),
                                            "filled_areas_thickness"), 1), "yes")

    def test_old_fill_is_dropped_because_it_described_old_parameters(self):
        self._add_filled_polygon("GND")
        self.assertEqual(len(S.children(self.zone("GND"), "filled_polygon")), 1)
        plan, _ = self.apply({"op": "set_zone_params", "net": "GND",
                              "min_thickness": 0.1})
        detail = plan["operations"][0]["detail"]
        self.assertEqual(detail["dropped_filled_polygons"], 1)
        self.assertEqual(S.children(self.zone("GND"), "filled_polygon"), [])
        self.assertEqual(S.arg_num(S.child(self.zone("GND"), "min_thickness"), 1),
                         0.1)

    def test_clearance_goes_into_connect_pads(self):
        self.apply({"op": "set_zone_params", "net": "GND", "clearance": 0.375})
        conn = S.child(self.zone("GND"), "connect_pads")
        self.assertAlmostEqual(S.arg_num(S.child(conn, "clearance"), 1), 0.375)

    def test_a_call_with_no_parameter_is_refused(self):
        with self.assertRaises(O.OpError) as ctx:
            self.apply({"op": "set_zone_params", "net": "GND"})
        self.assertEqual(ctx.exception.code, "ARG_MISSING")

    def test_a_selector_that_matches_nothing_is_refused(self):
        with self.assertRaises(O.OpError) as ctx:
            self.apply({"op": "set_zone_params", "layer": "F.SilkS",
                        "clearance": 0.3})
        self.assertEqual(ctx.exception.code, "TARGET_NOT_FOUND")

    def test_a_keep_out_spells_out_every_rule_flag(self):
        """A rule flag left out of the file is read by KiCad as not_allowed.

        Measured on a 96-footprint clone: rule areas written with only
        `(copperpour not_allowed)` raised 199 `items_not_allowed` errors on
        ordinary tracks; spelling `(tracks allowed)` and friends out took them
        to 0. So the gold content is not the one word asked for - it is the
        five flags, four of them `allowed`.
        """
        self.apply({"op": "add_keepout", "layer": "F.Cu", "rules": ["copperpour"],
                    "points": [[1, 1], [2, 1], [2, 2], [1, 2]]})
        keepout = [z for z in self.editor().items("zone")
                   if S.child(z, "keepout") is not None][0]
        ko = S.child(keepout, "keepout")
        got = {S.head(n): S.arg_text(n, 1) for n in ko.items[1:]
               if isinstance(n, S.Seq)}
        self.assertEqual(got, {"tracks": "allowed", "vias": "allowed",
                               "pads": "allowed", "copperpour": "not_allowed",
                               "footprints": "allowed"})

    def test_a_keep_out_has_no_fill_parameters(self):
        self.apply({"op": "add_keepout", "layer": "F.Cu",
                    "points": [[1, 1], [2, 1], [2, 2], [1, 2]]})
        keepout = [z for z in self.editor().items("zone")
                   if S.child(z, "keepout") is not None][0]
        uid = self.editor().sp.id_of(keepout)
        with self.assertRaises(O.OpError) as ctx:
            self.apply({"op": "set_zone_params", "uuid": uid, "clearance": 0.3})
        self.assertEqual(ctx.exception.code, "ARG_INVALID")


class NetClasses(EditBase):
    def classes(self):
        out = {}
        for node in self.editor().items("net_class"):
            out[S.arg_text(node, 1)] = [S.arg_text(n, 1)
                                        for n in S.children(node, "add_net")]
        return out

    def test_a_net_is_added_to_the_class(self):
        self.apply({"op": "assign_netclass", "net": "GND", "netclass": "Power"})
        self.assertIn("GND", self.classes()["Power"])

    def test_assigning_twice_does_not_duplicate(self):
        self.apply({"op": "assign_netclass", "net": "GND", "netclass": "Power"})
        plan, _ = self.apply({"op": "assign_netclass", "net": "GND",
                              "netclass": "Power"})
        self.assertFalse(plan["operations"][0]["detail"]["changed"])
        self.assertEqual(self.classes()["Power"].count("GND"), 1)

    def test_moving_a_net_between_classes_is_reported(self):
        self.apply({"op": "assign_netclass", "net": "GND", "netclass": "Default"})
        plan, _ = self.apply({"op": "assign_netclass", "net": "GND",
                              "netclass": "Power"})
        detail = plan["operations"][0]["detail"]
        self.assertEqual([m["netclass"] for m in detail["moved_from"]], ["Default"])
        classes = self.classes()
        self.assertNotIn("GND", classes["Default"])
        self.assertIn("GND", classes["Power"])

    def test_a_missing_class_is_refused_with_the_ones_that_exist(self):
        with self.assertRaises(O.OpError) as ctx:
            self.apply({"op": "assign_netclass", "net": "GND", "netclass": "Nope"})
        self.assertEqual(ctx.exception.code, "NETCLASS_NOT_FOUND")
        self.assertIn("Default", str(ctx.exception))


class PadsAndFields(EditBase):
    def test_a_pad_is_resized_and_the_shape_is_kept(self):
        plan, _ = self.apply({"op": "set_pad_size", "ref": "R1", "pad": "1",
                              "width": 1.5, "height": 1.8})
        self.assertEqual(plan["operations"][0]["detail"]["before"], [1.2, 1.4])
        pad = S.children(self.footprint("R1"), "pad")[0]
        size = S.child(pad, "size")
        self.assertAlmostEqual(S.arg_num(size, 1), 1.5, places=9)
        self.assertAlmostEqual(S.arg_num(size, 2), 1.8, places=9)
        self.assertEqual(S.head(pad), "pad")
        self.assertIn("roundrect", [it.text for it in pad.items
                                    if isinstance(it, S.Atom)])

    def test_height_defaults_to_the_width(self):
        self.apply({"op": "set_pad_size", "ref": "R1", "pad": "2", "width": 1.1})
        size = S.child(S.children(self.footprint("R1"), "pad")[1], "size")
        self.assertAlmostEqual(S.arg_num(size, 1), 1.1, places=9)
        self.assertAlmostEqual(S.arg_num(size, 2), 1.1, places=9)

    def test_a_missing_pad_lists_the_ones_that_exist(self):
        with self.assertRaises(O.OpError) as ctx:
            self.apply({"op": "set_pad_size", "ref": "R1", "pad": "9", "width": 1})
        self.assertEqual(ctx.exception.code, "PAD_NOT_FOUND")
        self.assertIn("1", str(ctx.exception))

    def test_a_field_value_is_replaced_in_place(self):
        before = S.child(self.footprint("R1"), "fp_text")
        self.apply({"op": "set_footprint_field", "ref": "R1", "field": "Value",
                    "value": "22k"})
        fields = [f for f in S.children(self.footprint("R1"), "fp_text")
                  if S.arg_text(f, 1) == "value"]
        self.assertEqual(len(fields), 1, "a second Value field must not appear")
        self.assertEqual(S.arg_text(fields[0], 2), "22k")
        self.assertAlmostEqual(S.arg_num(S.child(fields[0], "at"), 1),
                               S.arg_num(S.child(before, "at"), 1), places=9)

    def test_a_field_can_be_hidden_and_shown_again(self):
        self.apply({"op": "set_footprint_field", "ref": "R1", "field": "Value",
                    "value": "22k", "visible": False})
        field = [f for f in S.children(self.footprint("R1"), "fp_text")
                 if S.arg_text(f, 1) == "value"][0]
        self.assertIsNotNone(S.child(field, "hide"))
        self.apply({"op": "set_footprint_field", "ref": "R1", "field": "Value",
                    "value": "22k", "visible": True})
        field = [f for f in S.children(self.footprint("R1"), "fp_text")
                 if S.arg_text(f, 1) == "value"][0]
        self.assertIsNone(S.child(field, "hide"))

    def test_an_empty_string_clears_a_field(self):
        self.apply({"op": "set_footprint_field", "ref": "R1", "field": "Value",
                    "value": ""})
        field = [f for f in S.children(self.footprint("R1"), "fp_text")
                 if S.arg_text(f, 1) == "value"][0]
        self.assertEqual(S.arg_text(field, 2), "")

    def test_a_missing_value_argument_is_refused(self):
        with self.assertRaises(O.OpError) as ctx:
            self.apply({"op": "set_footprint_field", "ref": "R1",
                        "field": "Value"})
        self.assertEqual(ctx.exception.code, "ARG_MISSING")

class FieldGeometry(EditBase):
    """The optional layer / position / size arguments added in 0.0.98.

    The rule these tests defend: naming none of them must leave the field
    exactly as it was, and naming one must not quietly move the others.
    """

    def _field(self, name="reference", ref="R1"):
        return [f for f in S.children(self.footprint(ref), "fp_text")
                if S.arg_text(f, 1) == name][0]

    def test_naming_no_geometry_leaves_the_field_where_it_was(self):
        at0 = S.child(self._field(), "at")
        x0, y0 = S.arg_num(at0, 1), S.arg_num(at0, 2)
        layer0 = S.arg_text(S.child(self._field(), "layer"), 1)
        self.assertIsNone(S.child(self._field(), "effects"),
                          "the fixture field has no effects node - if that "
                          "changes, this test stops proving anything")
        self.apply({"op": "set_footprint_field", "ref": "R1",
                    "field": "Reference", "value": "R99"})
        field = self._field(ref="R99")
        self.assertEqual(S.arg_text(field, 2), "R99")
        at = S.child(field, "at")
        self.assertAlmostEqual(S.arg_num(at, 1), x0, places=9)
        self.assertAlmostEqual(S.arg_num(at, 2), y0, places=9)
        self.assertEqual(S.arg_text(S.child(field, "layer"), 1), layer0)
        self.assertIsNone(S.child(field, "effects"),
                          "a size nobody asked for must not be invented")

    def test_a_field_can_move_layer_position_and_size_at_once(self):
        plan, _ = self.apply({"op": "set_footprint_field", "ref": "R1",
                              "field": "Reference", "value": "R1",
                              "layer": "F.Fab", "x": 1.5, "y": -3.25,
                              "text_height": 0.8, "text_width": 0.9})
        field = self._field()
        self.assertEqual(S.arg_text(S.child(field, "layer"), 1), "F.Fab")
        at = S.child(field, "at")
        self.assertAlmostEqual(S.arg_num(at, 1), 1.5, places=6)
        # Y is Y-UP on the way in and Y-DOWN in the file: -3.25 in, +3.25 out
        self.assertAlmostEqual(S.arg_num(at, 2), 3.25, places=6)
        node = S.child(S.child(S.child(field, "effects"), "font"), "size")
        self.assertAlmostEqual(S.arg_num(node, 1), 0.9, places=6)
        self.assertAlmostEqual(S.arg_num(node, 2), 0.8, places=6)
        detail = plan["operations"][0]["detail"]["geometry"]
        self.assertEqual(detail["layer"], "F.Fab")
        self.assertAlmostEqual(detail["x"], 1.5, places=6)
        self.assertAlmostEqual(detail["y"], -3.25, places=6)

    def test_naming_one_axis_keeps_the_other_and_the_angle(self):
        at0 = S.child(self._field(), "at")
        y0 = S.arg_num(at0, 2)
        self.apply({"op": "set_footprint_field", "ref": "R1",
                    "field": "Reference", "value": "R1", "x": 2.5})
        at = S.child(self._field(), "at")
        self.assertAlmostEqual(S.arg_num(at, 1), 2.5, places=6)
        self.assertAlmostEqual(S.arg_num(at, 2), y0, places=6)

    def test_a_layer_the_board_does_not_declare_is_refused(self):
        with self.assertRaises(O.OpError) as ctx:
            self.apply({"op": "set_footprint_field", "ref": "R1",
                        "field": "Reference", "value": "R1",
                        "layer": "F.Nope"})
        self.assertEqual(ctx.exception.code, "LAYER_NOT_IN_BOARD")
        self.assertEqual(S.arg_text(S.child(self._field(), "layer"), 1),
                         "F.SilkS", "a refused op must not have written")

    def test_a_position_written_as_text_is_refused(self):
        with self.assertRaises(O.OpError) as ctx:
            self.apply({"op": "set_footprint_field", "ref": "R1",
                        "field": "Reference", "value": "R1", "x": "1.5"})
        self.assertEqual(ctx.exception.code, "ARG_INVALID")

    def test_a_non_positive_text_size_is_refused(self):
        for size in (0, -0.8):
            with self.assertRaises(O.OpError) as ctx:
                self.apply({"op": "set_footprint_field", "ref": "R1",
                            "field": "Reference", "value": "R1",
                            "text_height": size})
            self.assertEqual(ctx.exception.code, "ARG_INVALID")

    def test_the_verifier_notices_a_field_that_did_not_move(self):
        """The negative control: a passing verify must mean something.

        After a write that moved the field to x=2.5, asking the verifier to
        confirm x=4.0 has to come back False - otherwise `all_verified` is a
        constant and the gate is decoration.
        """
        self.apply({"op": "set_footprint_field", "ref": "R1",
                    "field": "Reference", "value": "R1", "x": 2.5})
        spec = O.OPS["set_footprint_field"]
        out = spec["verify"](self.editor(),
                             {"ref": "R1", "field": "Reference",
                              "value": "R1", "x": 4.0})
        self.assertFalse(out["ok"], out)
        self.assertIn("reads", out.get("reason", ""))
        ok = spec["verify"](self.editor(),
                            {"ref": "R1", "field": "Reference",
                             "value": "R1", "x": 2.5})
        self.assertTrue(ok["ok"], ok)



class MechArc(EditBase):
    def test_the_arc_lands_on_the_requested_layer(self):
        self.apply({"op": "add_mech_arc", "sx": 5.0, "sy": 5.0, "mx": 6.0,
                    "my": 4.0, "ex": 7.0, "ey": 5.0, "layer": "Dwgs.User"})
        arcs = [n for n in self.editor().items("gr_arc")
                if S.arg_text(S.child(n, "layer"), 1) == "Dwgs.User"]
        self.assertEqual(len(arcs), 1)
        self.assertAlmostEqual(S.arg_num(S.child(arcs[0], "start"), 1), 5.0,
                               places=6)
        self.assertAlmostEqual(-S.arg_num(S.child(arcs[0], "start"), 2), 5.0,
                               places=6)

    def test_an_unknown_layer_is_refused(self):
        with self.assertRaises(O.OpError) as ctx:
            self.apply({"op": "add_mech_arc", "sx": 5.0, "sy": 5.0, "mx": 6.0,
                        "my": 4.0, "ex": 7.0, "ey": 5.0, "layer": "Nowhere"})
        self.assertEqual(ctx.exception.code, "LAYER_NOT_IN_BOARD")


NAME_ONLY = """(kicad_pcb (version 20260206) (generator "pcbnew") (generator_version "10.0")
  (general (thickness 1.6))
  (layers (0 "F.Cu" signal) (31 "B.Cu" signal) (37 "F.SilkS" user) (44 "Edge.Cuts" user))
  (footprint "Test:R_0805" (layer "F.Cu") (at 10 10)
    (property "Reference" "R1" (at 0 -2 0) (layer "F.SilkS"))
    (property "Value" "10k" (at 0 2 0) (layer "F.Fab"))
    (pad "1" smd roundrect (at -0.95 0) (size 1.2 1.4)
      (layers "F.Cu" "F.Paste" "F.Mask") (roundrect_rratio 0.25) (net "GND"))
    (pad "2" smd roundrect (at 0.95 0) (size 1.2 1.4)
      (layers "F.Cu" "F.Paste" "F.Mask") (roundrect_rratio 0.25) (net "VCC"))
  )
  (segment (start 11 10) (end 14 10) (width 0.25) (layer "F.Cu") (net "VCC"))
)
"""


class NameOnlyNets(unittest.TestCase):
    """KiCad 10 boards name nets on the objects and keep no `(net code name)` table.

    A tool that only understands the older table would report every net as
    missing on those boards, so the map is derived from the references.
    """

    def setUp(self):
        self.tmp = tempfile.mkdtemp(prefix="pcbops-")
        self._home = os.environ.get("PCB_AGENT_HOME")
        os.environ["PCB_AGENT_HOME"] = os.path.join(self.tmp, "home")
        self.board = os.path.join(self.tmp, "board.kicad_pcb")
        with open(self.board, "w", encoding="utf-8", newline="\n") as fh:
            fh.write(NAME_ONLY)

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

    def test_the_nets_are_found_without_a_table(self):
        ed = self.editor()
        self.assertTrue(ed.name_only)
        self.assertEqual(sorted(n for n in ed.net_by_name if n), ["GND", "VCC"])

    def test_deleting_a_net_finds_its_users(self):
        ed = self.editor()
        use = O._net_uses(ed, "VCC")
        self.assertEqual(len(use["pads"]), 1)
        self.assertEqual(use["tracks"], 1)

    def test_creating_a_net_declaration_is_refused_with_a_reason(self):
        ed = self.editor()
        with self.assertRaises(O.OpError) as ctx:
            ed.net_code("NEW", allow_create=True)
        self.assertEqual(ctx.exception.code, "NET_CANNOT_CREATE")


if __name__ == "__main__":
    unittest.main()

class ViaInPad(EditBase):
    """A via in a pad lands on the pad centre, in the pad's net, or it refuses."""

    def vias(self):
        out = []
        for node in self.editor().items("via"):
            at = S.child(node, "at")
            net = S.child(node, "net")
            out.append({"x": S.arg_num(at, 1, 0.0),
                        "y": S.arg_num(at, 2, 0.0),
                        "net": S.arg_text(net, 1),
                        "layers": [it.text for it in
                                   S.child(node, "layers").items[1:]
                                   if isinstance(it, S.Atom)]})
        return out

    def pad_centre(self, ref, number):
        """Where the pad is, computed straight off the raw nodes."""
        import math
        fp = self.footprint(ref)
        at = S.child(fp, "at")
        fx, fy = S.arg_num(at, 1, 0.0), S.arg_num(at, 2, 0.0)
        fa = math.radians(S.arg_num(at, 3) or 0.0)
        for pad in S.children(fp, "pad"):
            if S.arg_text(pad, 1) != number:
                continue
            pat = S.child(pad, "at")
            px, py = S.arg_num(pat, 1, 0.0), S.arg_num(pat, 2, 0.0)
            # the board file grows down: turn the pad offset in the Y-up frame
            cx, cy = fx, -fy
            rx, ry = px, -py
            return (cx + rx * math.cos(fa) - ry * math.sin(fa),
                    -(cy + rx * math.sin(fa) + ry * math.cos(fa)))
        raise AssertionError("pad %s.%s not found" % (ref, number))

    def test_the_via_sits_on_the_pad_centre_of_a_rotated_part(self):
        before = len(self.vias())
        plan, _ = self.apply({"op": "add_via_in_pad", "ref": "U1", "pad": "1"})
        detail = plan["operations"][0]["detail"]
        self.assertEqual(detail["net"], "GND")
        self.assertEqual(detail["pad_type"], "smd")
        after = self.vias()
        self.assertEqual(len(after), before + 1)
        px, py = self.pad_centre("U1", "1")
        new = [v for v in after if abs(v["y"] - py) < 1e-3 and
               abs(v["x"] - px) < 1e-3]
        self.assertEqual(len(new), 1, "no via at the pad centre: %r" % (after,))
        self.assertEqual(new[0]["net"], "1")

    def test_the_via_takes_the_pad_net_without_being_told(self):
        plan, _ = self.apply({"op": "add_via_in_pad", "ref": "R1", "pad": "2"})
        detail = plan["operations"][0]["detail"]
        self.assertEqual(detail["net"], "VCC")
        self.assertEqual(detail["pad_size"], [1.2, 1.4])

    def test_a_net_the_pad_does_not_have_is_refused_as_a_short_not_written(self):
        """This case used to assert that naming another net worked. It does not,
        and that is the correct answer: R1 pad 2 carries VCC, so a GND via at its
        centre is 0 mm from VCC copper - a short, not a routing choice. The
        clearance gate stops it at propose time, before a byte is written; naming
        a net is still the way out for a pad that has none (see the netless-pad
        case below)."""
        before = open(self.board, "rb").read()
        plan = E.propose(self.board, [{"op": "add_via_in_pad", "ref": "R1",
                                       "pad": "2", "net": "GND"}])
        gates = [b.get("gate") for b in plan["blocking"] if isinstance(b, dict)]
        self.assertEqual(gates, ["precondition.clearance_no_worse"], plan["blocking"])
        with open(self.board, "rb") as fh:
            self.assertEqual(fh.read(), before)

    def test_a_pad_with_no_net_is_refused_unless_a_net_is_named(self):
        self.apply({"op": "set_pad_net", "ref": "R1", "pad": "1", "net": ""})
        with self.assertRaises(O.OpError) as ctx:
            self.apply({"op": "add_via_in_pad", "ref": "R1", "pad": "1"})
        self.assertEqual(ctx.exception.code, "PAD_HAS_NO_NET")
        self.apply({"op": "add_via_in_pad", "ref": "R1", "pad": "1", "net": "GND"})

    def test_a_missing_pad_names_the_ones_that_are_there(self):
        with self.assertRaises(O.OpError) as ctx:
            self.apply({"op": "add_via_in_pad", "ref": "U1", "pad": "99"})
        self.assertEqual(ctx.exception.code, "PAD_NOT_FOUND")
        self.assertIn("1, 2", str(ctx.exception))

    def test_two_drills_in_one_pad_are_refused(self):
        self.apply({"op": "add_via_in_pad", "ref": "U1", "pad": "1"})
        with self.assertRaises(O.OpError) as ctx:
            self.apply({"op": "add_via_in_pad", "ref": "U1", "pad": "1"})
        self.assertEqual(ctx.exception.code, "VIA_EXISTS")

    def test_a_drill_wider_than_the_via_is_refused(self):
        with self.assertRaises(O.OpError) as ctx:
            self.apply({"op": "add_via_in_pad", "ref": "U1", "pad": "1",
                        "size": 0.3, "drill": 0.3})
        self.assertEqual(ctx.exception.code, "ARG_INVALID")

    def test_the_span_is_written_so_the_file_agrees_with_the_model(self):
        plan, _ = self.apply({"op": "add_via_in_pad", "ref": "U1", "pad": "2",
                              "layers": ["B.Cu", "F.Cu"]})
        detail = plan["operations"][0]["detail"]
        self.assertEqual(detail["layers"], ["F.Cu", "B.Cu"],
                         "an inverted pair must be put back into stack order")
        got = [v for v in self.vias() if v["net"] == "2" and
               abs(v["x"] - self.pad_centre("U1", "2")[0]) < 1e-3]
        self.assertEqual(len(got), 1)
        self.assertEqual(got[0]["layers"], ["F.Cu", "B.Cu"])

    def test_a_layer_that_is_not_copper_is_refused(self):
        with self.assertRaises(O.OpError) as ctx:
            self.apply({"op": "add_via_in_pad", "ref": "U1", "pad": "1",
                        "layers": ["F.Cu", "F.SilkS"]})
        self.assertIn(ctx.exception.code, ("LAYER_NOT_IN_BOARD",
                                           "LAYER_NOT_IN_COPPER"))

_VIP_DUMP = r"""
import json, sys, pcbnew
b = pcbnew.LoadBoard(sys.argv[1])
out = []
for fp in b.GetFootprints():
    if fp.GetReference() != "U1":
        continue
    for p in fp.Pads():
        q = p.GetPosition()
        out.append(["pad", p.GetNumber(), q.x / 1e6, q.y / 1e6])
for t in b.GetTracks():
    if t.Type() == pcbnew.PCB_VIA_T:
        q = t.GetPosition()
        out.append(["via", t.GetNetname(), q.x / 1e6, q.y / 1e6])
json.dump(out, sys.stdout)
"""


@unittest.skipUnless(ENGINE.find_kicad_cli(), "KiCad (pcbnew) is not installed here")
class ViaInPadAgainstPcbnew(EditBase):
    """The written via must be where pcbnew says the pad is, not where we think."""

    def _kicad_python(self):
        exe = ENGINE.find_kicad_cli()
        cand = os.path.join(os.path.dirname(exe), "python.exe")
        return cand if os.path.isfile(cand) else None

    def _dump(self, path):
        env = {k: v for k, v in os.environ.items()
               if k not in ("PYTHONPATH", "PYTHONHOME")}
        proc = subprocess.run([self._kicad_python(), "-E", "-s", "-c",
                               _VIP_DUMP, path],
                              capture_output=True, text=True, timeout=180,
                              env=env)
        self.assertEqual(proc.returncode, 0, proc.stderr[-800:])
        import json
        return json.loads(proc.stdout)

    def test_the_via_lands_exactly_on_pcbnews_pad_position(self):
        py = self._kicad_python()
        if not py:
            self.skipTest("KiCad's own python is not next to kicad-cli")
        rows = self._dump(self.board)
        pads = {(r[1]): (r[2], r[3]) for r in rows if r[0] == "pad"}
        self.assertIn("1", pads)
        self.apply({"op": "add_via_in_pad", "ref": "U1", "pad": "1"})
        rows = self._dump(self.board)
        pads = {(r[1]): (r[2], r[3]) for r in rows if r[0] == "pad"}
        vias = [r for r in rows if r[0] == "via"]
        want = pads["1"]
        near = [v for v in vias
                if abs(v[2] - want[0]) < 1e-4 and abs(v[3] - want[1]) < 1e-4]
        self.assertEqual(len(near), 1,
                         "pcbnew sees no via at U1 pad 1 (%r vs %r)"
                         % (vias, want))
        self.assertEqual(near[0][1], "GND")



class Teardrop(EditBase):
    """A teardrop is real copper: filled, netted, anchored on the pad."""

    def polys(self):
        out = []
        for node in self.editor().items("gr_poly"):
            layer = S.arg_text(S.child(node, "layer"), 1)
            net = S.child(node, "net")
            fill = S.child(node, "fill")
            pts = []
            pn = S.child(node, "pts")
            for item in (pn.items[1:] if pn is not None else []):
                if isinstance(item, S.Seq):
                    pts.append((S.arg_num(item, 1, 0.0),
                                -(S.arg_num(item, 2, 0.0) or 0.0)))
            out.append({"layer": layer,
                        "net": (S.arg_text(net, 2) or S.arg_text(net, 1)
                                if net is not None else None),
                        "fill": S.arg_text(fill, 1) if fill is not None else None,
                        "points": pts})
        return out

    def test_the_polygon_is_filled_copper_of_the_pads_net(self):
        self.apply({"op": "add_teardrop", "ref": "R1", "pad": "2"})
        got = self.polys()
        self.assertEqual(len(got), 1)
        self.assertEqual(got[0]["layer"], "F.Cu")
        self.assertEqual(got[0]["net"], "2")
        self.assertEqual(got[0]["fill"], "yes")
        self.assertGreater(len(got[0]["points"]), 20,
                           "a curved fillet is sampled into an arc, not two chords")

    def test_the_straight_variant_is_four_vertices(self):
        self.apply({"op": "add_teardrop", "ref": "R1", "pad": "2",
                    "curve": False})
        self.assertEqual(len(self.polys()[0]["points"]), 4)

    def test_both_variants_reach_the_same_shoulders(self):
        import math
        self.apply({"op": "add_teardrop", "ref": "R1", "pad": "2"},
                   {"op": "add_teardrop", "ref": "R1", "pad": "2",
                    "curve": False})
        curved, chord = self.polys()
        cx, cy = 10.95, -10.0
        far_c = max(math.hypot(x - cx, y - cy) for x, y in curved["points"])
        far_s = max(math.hypot(x - cx, y - cy) for x, y in chord["points"])
        # both end at the shoulder, sqrt(L^2 + (w/2)^2); the curve is not a
        # different teardrop, only a different edge between the same two points
        self.assertAlmostEqual(far_c, far_s, places=6)
        self.assertGreater(len(curved["points"]), len(chord["points"]))

    def test_the_polygon_starts_on_the_pad_circle_and_reaches_the_shoulder(self):
        import math
        plan, _ = self.apply({"op": "add_teardrop", "ref": "R1", "pad": "2",
                              "length": 2.0})
        detail = plan["operations"][0]["detail"]["teardrops"][0]
        self.assertAlmostEqual(detail["length"], 2.0, places=6)
        pts = self.polys()[0]["points"]
        # pad 2 of R1 sits at (10.95, -10) in Y-up space, radius min(1.2,1.4)/2
        cx, cy, r = 10.95, -10.0, 0.6
        dists = [math.hypot(x - cx, y - cy) for x, y in pts]
        self.assertLess(min(abs(d - r) for d in dists), 1e-4,
                        "no vertex sits on the pad's own circle")
        self.assertGreater(max(dists), 1.95,
                           "the teardrop has to reach the shoulder, 2 mm out")
        # anchored on the circle on both sides of the track, i.e. two fillets
        self.assertLess(min(abs(d - r) for d in dists), 1e-4)

    def test_a_pad_with_no_track_is_refused_and_says_so(self):
        # R1 pad 1 is on GND and has no track; the fixture's track is on pad 2
        with self.assertRaises(O.OpError) as ctx:
            self.apply({"op": "add_teardrop", "ref": "R1", "pad": "1"})
        self.assertEqual(ctx.exception.code, "NO_TRACK")
        self.assertIn("route the pad first", str(ctx.exception))

    def test_a_direction_can_be_stated_when_nothing_is_routed_yet(self):
        self.apply({"op": "add_teardrop", "ref": "U1", "pad": "1",
                    "dx": 0, "dy": -1})
        detail = self.polys()[0]
        self.assertEqual(detail["net"], "1")
        ys = [p[1] for p in detail["points"]]
        # the tear runs down (Y-up -y), so the furthest vertex is below the pad
        self.assertLess(min(ys), -16.9)

    def test_a_pad_without_a_net_has_nowhere_to_belong(self):
        self.apply({"op": "set_pad_net", "ref": "R1", "pad": "2", "net": ""})
        with self.assertRaises(O.OpError) as ctx:
            self.apply({"op": "add_teardrop", "ref": "R1", "pad": "2"})
        self.assertEqual(ctx.exception.code, "PAD_HAS_NO_NET")

    def test_a_length_inside_half_the_track_width_is_refused(self):
        with self.assertRaises(O.OpError) as ctx:
            self.apply({"op": "add_teardrop", "ref": "R1", "pad": "2",
                        "length": 0.1, "track_width": 0.25})
        self.assertEqual(ctx.exception.code, "ARG_INVALID")

    def test_all_tears_every_track_leaving_the_pad(self):
        # a second track leaving the same pad straight down (file +y = Y-up -y)
        # op coordinates are Y-up: the board's y=10 line is y=-10 here
        self.apply({"op": "add_track", "x1": 10.95, "y1": -10.2, "x2": 10.95,
                    "y2": -16.0, "width": 0.25, "layer": "F.Cu", "net": "VCC"})
        plan, _ = self.apply({"op": "add_teardrop", "ref": "R1", "pad": "2",
                              "all": True})
        self.assertEqual(plan["operations"][0]["detail"]["count"], 2)
        dirs = {tuple(t["direction"]) for t in
                plan["operations"][0]["detail"]["teardrops"]}
        self.assertEqual(len(dirs), 2)


_TD_DUMP = r"""
import json, sys, pcbnew
b = pcbnew.LoadBoard(sys.argv[1])
out = []
for d in b.GetDrawings():
    if not d.GetLayerName().endswith(".Cu"):
        continue
    bb = d.GetBoundingBox()
    out.append({"layer": d.GetLayerName(), "net": d.GetNetname(),
                "fill": bool(d.IsAnyFill()),
                "x0": bb.GetX() / 1e6, "y0": bb.GetY() / 1e6,
                "x1": bb.GetRight() / 1e6, "y1": bb.GetBottom() / 1e6})
json.dump(out, sys.stdout)
"""


@unittest.skipUnless(ENGINE.find_kicad_cli(), "KiCad (pcbnew) is not installed here")
class TeardropAgainstPcbnew(EditBase):
    """pcbnew has to see the teardrop as copper of that net, where we put it."""

    def _kicad_python(self):
        exe = ENGINE.find_kicad_cli()
        cand = os.path.join(os.path.dirname(exe), "python.exe")
        return cand if os.path.isfile(cand) else None

    def _dump(self, path):
        env = {k: v for k, v in os.environ.items()
               if k not in ("PYTHONPATH", "PYTHONHOME")}
        proc = subprocess.run([self._kicad_python(), "-E", "-s", "-c", _TD_DUMP,
                               path], capture_output=True, text=True,
                              timeout=180, env=env)
        self.assertEqual(proc.returncode, 0, proc.stderr[-800:])
        import json
        return json.loads(proc.stdout)

    def test_pcbnew_reads_it_as_filled_copper_with_the_pad_net(self):
        py = self._kicad_python()
        if not py:
            self.skipTest("KiCad's own python is not next to kicad-cli")
        self.assertEqual(self._dump(self.board), [])
        self.apply({"op": "add_teardrop", "ref": "R1", "pad": "2",
                    "length": 2.0})
        rows = self._dump(self.board)
        self.assertEqual(len(rows), 1, rows)
        self.assertEqual(rows[0]["layer"], "F.Cu")
        self.assertEqual(rows[0]["net"], "VCC")
        self.assertTrue(rows[0]["fill"], "KiCad must see a FILLED polygon")
        # the pad centre is (10.95, -10) in Y-up; the shoulder is 2 mm along +u
        self.assertLess(rows[0]["x0"], 11.6, "starts at the pad edge, not past it")
        self.assertGreater(rows[0]["x1"], 12.6)

    def test_drc_does_not_call_the_teardrop_a_clearance_problem(self):
        exe = ENGINE.find_kicad_cli()
        before = ENGINE.drc(self.board)
        self.apply({"op": "add_teardrop", "ref": "R1", "pad": "2",
                    "length": 2.0})
        after = ENGINE.drc(self.board)
        def kinds(rep):
            out = {}
            for v in (rep.get("violations") or []):
                out[v.get("type")] = out.get(v.get("type"), 0) + 1
            return out
        b, a = kinds(before), kinds(after)
        for bad in ("clearance", "shorting_items", "copper_edge_clearance"):
            self.assertEqual(a.get(bad, 0), b.get(bad, 0),
                             "teardrop changed %s: %r -> %r" % (bad, b, a))


class BulkDeleteAndReplace(EditBase):
    """Deleting copper in bulk, and re-creating what was deleted in one plan.

    Both exist because of the same session: ripping up a region emitted one
    `delete_track` per segment (528 operations for one board), and replacing a
    pour by `delete_zone` + `add_zone` on the same net/layer in one apply was
    refused with `postcondition check failed` because the delete's postcondition
    asked "does anything still match net+layer" on the FINAL tree - where the
    zone the plan had just added answered yes.
    """

    def zones_for(self, net):
        return [z for z in self.editor().items("zone")
                if S.arg_text(S.child(z, "net_name"), 1) == net]

    def test_a_pour_can_be_deleted_and_re_created_in_one_plan(self):
        plan = E.propose(self.board, [
            {"op": "delete_zone", "net": "GND", "layer": "F.Cu"},
            {"op": "add_zone", "net": "GND", "layer": "F.Cu",
             "points": [[3, 3], [17, 3], [17, 17], [3, 17]]},
        ])
        self.assertEqual(plan["blocking"], [], plan["blocking"])
        # the conflict is REPORTED (it is about the same object) and allowed
        kinds = [c["kind"] for c in plan["plan_conflicts"]]
        self.assertEqual(kinds, ["delete_then_add_same_zone"])
        self.assertTrue(plan["plan_conflicts"][0]["allowed"])
        report = E.apply_plan(plan["plan_id"], confirm=True,
                              options=dict(engine=False))
        self.assertEqual(report["status"], "ok")
        self.assertTrue(report["gates"]["postconditions"]["all_verified"])
        # exactly one GND pour on F.Cu, and it is the NEW one
        gnd = self.zones_for("GND")
        self.assertEqual(len(gnd), 1)
        # the file stores y DOWN, so the outline given as (3, 3) mm Y up is
        # written `(xy 3 -3)` - the round trip through the caller's frame is what
        # is being checked here, not the spelling
        pts = [(S.arg_text(n, 1), S.arg_text(n, 2))
               for _p, n in S.walk(gnd[0]) if S.head(n) == "xy"]
        self.assertIn(("3", "-3"), pts)

    def test_the_delete_postcondition_is_checked_by_uuid(self):
        detail = O.run_all(
            K.KiCadDocument.from_path(self.board),
            [{"op": "delete_zone", "net": "GND", "layer": "F.Cu"}])[0]["detail"]
        self.assertEqual(len(detail["deleted_uuids"]), 1)
        res = O._verify_delete_zone(
            O.Editor(K.KiCadDocument.from_path(self.board)),
            {"op": "delete_zone", "net": "GND", "layer": "F.Cu"},
            {"detail": detail})
        self.assertTrue(res["ok"])
        self.assertEqual(res["checked_by"].split(" of ")[0], "uuid")

    def test_delete_copper_needs_a_selector(self):
        with self.assertRaises(O.OpError) as ctx:
            self.apply({"op": "delete_copper", "kinds": "all"})
        self.assertEqual(ctx.exception.code, "ARG_MISSING")

    def test_delete_copper_by_net_takes_the_tracks_and_the_via(self):
        plan, _ = self.apply({"op": "delete_copper", "net": "VCC"})
        detail = plan["operations"][0]["detail"]
        self.assertEqual(detail["by_kind"], {"tracks": 1, "vias": 1, "zones": 1})
        ed = self.editor()
        self.assertEqual([n for n in ed.items("segment")
                          if S.arg_text(S.child(n, "net"), 1) == "2"], [])
        self.assertEqual([n for n in ed.items("via")
                          if S.arg_text(S.child(n, "net"), 1) == "2"], [])

    def test_a_region_selector_deletes_only_what_is_inside_it(self):
        # the via is `(at 14 10)` in the FILE, which is y = -10 mm in the frame
        # this server reports (mm, Y up) - so the region is stated negatively.
        plan, _ = self.apply({"op": "delete_copper",
                              "region": [13, -11, 15, -9], "kinds": ["vias"]})
        self.assertEqual(plan["operations"][0]["detail"]["by_kind"], {"vias": 1})
        self.assertEqual(self.editor().items("via"), [])

    def test_a_zone_that_merely_crosses_the_region_is_not_deleted(self):
        with self.assertRaises(O.OpError) as ctx:
            self.apply({"op": "delete_copper", "region": [0, 0, 5, 5],
                        "kinds": ["zones"]})
        self.assertEqual(ctx.exception.code, "TARGET_NOT_FOUND")
        self.assertIn("WHOLE outline", str(ctx.exception.hint))
        self.assertEqual(len(self.zones_for("GND")), 1)

    def test_delete_copper_postcondition_survives_a_later_add(self):
        plan = E.propose(self.board, [
            {"op": "delete_copper", "net": "VCC"},
            {"op": "add_track", "net": "VCC", "layer": "F.Cu",
             "x1": 11, "y1": 10, "x2": 12.5, "y2": 10},
        ])
        self.assertEqual(plan["blocking"], [], plan["blocking"])
        report = E.apply_plan(plan["plan_id"], confirm=True,
                              options=dict(engine=False))
        verified = report["gates"]["postconditions"]["operations"]
        self.assertTrue(verified[0]["verified"], verified[0])
        self.assertEqual(len(self.editor().items("segment")), 1)


class DryRun(EditBase):
    def test_a_dry_run_proves_the_gates_and_leaves_no_byte(self):
        with open(self.board, "rb") as fh:
            before = fh.read()
        plan = E.propose(self.board, [{"op": "add_track", "net": "GND",
                                       "layer": "F.Cu",
                                       "x1": 5, "y1": 5, "x2": 8, "y2": 5}])
        out = E.apply_plan(plan["plan_id"], confirm=True,
                           options=dict(engine=False, dry_run=True))
        self.assertTrue(out["dry_run"])
        self.assertTrue(out["would_write"])
        self.assertTrue(out["file_restored_byte_for_byte"])
        self.assertTrue(out["plan_still_open"])
        with open(self.board, "rb") as fh:
            self.assertEqual(fh.read(), before)
        # the plan is still usable: a real write of the same plan must work
        done = E.apply_plan(plan["plan_id"], confirm=True,
                            options=dict(engine=False))
        self.assertEqual(done["status"], "ok")
        self.assertEqual(len(self.editor().items("segment")), 2)

class TranslateBoard(EditBase):
    """Moving the whole board exists because a plot lands where the numbers are.

    The board below sits at positive coordinates; the real board this was written
    for sits at negative Y and exported as a blank page. What the tests hold the
    operation to: every position moves by the same delta, the geometry INSIDE a
    footprint does not (it is already in the footprint's own frame), one unknown
    item kind stops the whole move, and the postcondition notices a point that
    stayed behind.
    """

    def node_at(self, head, index=0):
        nodes = self.editor().items(head)
        return nodes[index]

    def point(self, node, child):
        n = S.child(node, child)
        return (S.arg_num(n, 1, 0.0), S.arg_num(n, 2, 0.0))

    def test_everything_outside_a_footprint_moves_by_the_same_delta(self):
        plan, _ = self.apply({"op": "translate_board", "dx": 100.0, "dy": 25.0})
        ed = self.editor()
        # a file Y of -25 is 25 mm up: the API is Y up, the file is Y down
        edge = ed.items("gr_line")[0]
        self.assertEqual(self.point(edge, "start"), (100.0, -25.0))
        self.assertEqual(self.point(edge, "end"), (140.0, -25.0))
        self.assertEqual(self.point(ed.footprint("R1"), "at")[:2], (110.0, -15.0))
        segment = ed.items("segment")[0]
        self.assertEqual(self.point(segment, "start"), (111.0, -15.0))
        self.assertEqual(self.point(ed.items("via")[0], "at"), (114.0, -15.0))
        zone = ed.items("zone")[0]
        first = S.children(S.child(S.child(zone, "polygon"), "pts"), "xy")[0]
        self.assertEqual((S.arg_num(first, 1, 0.0), S.arg_num(first, 2, 0.0)),
                         (102.0, -23.0))
        detail = plan["operations"][0]["detail"]
        self.assertEqual(detail["items_total"], 10)
        self.assertEqual(detail["positions"], 21)
        self.assertNotEqual(detail["digest_before"], detail["digest_after"])

    def test_the_geometry_inside_a_footprint_does_not_move_relative_to_it(self):
        self.apply({"op": "translate_board", "dx": 100.0, "dy": 25.0})
        fp = self.editor().footprint("R1")
        pads = S.children(fp, "pad")
        self.assertEqual(self.point(pads[0], "at"), (-0.95, 0.0))
        self.assertEqual(self.point(pads[1], "at"), (0.95, 0.0))
        self.assertEqual(self.point(S.children(fp, "fp_text")[0], "at"), (0.0, -2.0))

    def test_the_rotation_of_a_footprint_survives(self):
        self.apply({"op": "translate_board", "dx": -5.0, "dy": -7.0})
        at = S.child(self.editor().footprint("U1"), "at")
        # U1 was at file (20, 15); -5 mm and 7 mm up put it at (15, 22) in the
        # file's Y-down frame, which is (15, -22) in the frame this server reports
        self.assertEqual((S.arg_num(at, 1, 0.0), S.arg_num(at, 2, 0.0)),
                         (15.0, 22.0))
        self.assertAlmostEqual(S.arg_num(at, 3, 0.0), 30.0, places=9)

    def test_a_move_of_nothing_is_refused(self):
        with self.assertRaises(O.OpError) as ctx:
            self.apply({"op": "translate_board", "dx": 0, "dy": 0})
        self.assertEqual(ctx.exception.code, "ARG_INVALID")

    def test_an_item_kind_it_does_not_know_stops_the_whole_move(self):
        # KiCad 8 writes tables; nothing here knows how a table is anchored, so a
        # board carrying one must be refused rather than moved around it.
        with open(self.board, "w", encoding="utf-8", newline="\n") as fh:
            fh.write(BOARD.replace("(segment ", "(table (column_count 2) (at 5 5))"
                                   "\n  (segment ", 1))
        with open(self.board, "rb") as fh:
            before = fh.read()
        with self.assertRaises(O.OpError) as ctx:
            self.apply({"op": "translate_board", "dx": 10, "dy": 10})
        self.assertEqual(ctx.exception.code, "UNSUPPORTED_ITEM")
        self.assertIn("table", str(ctx.exception))
        with open(self.board, "rb") as fh:
            self.assertEqual(fh.read(), before)

    def test_the_verifier_notices_a_position_that_stayed_behind(self):
        # reverse control: the write succeeds, then one corner is put back. The
        # postcondition has to fail - otherwise it is only checking that run() ran.
        plan = E.propose(self.board, [{"op": "translate_board", "dx": 100.0,
                                       "dy": 25.0}])
        records = P.PLANS[plan["plan_id"]]["records"]      # gone after a write
        E.apply_plan(plan["plan_id"], confirm=True, options=dict(engine=False))
        with open(self.board, encoding="utf-8", newline="") as fh:
            text = fh.read()
        self.assertIn("(start 100 -25)", text)
        with open(self.board, "w", encoding="utf-8", newline="") as fh:
            fh.write(text.replace("(start 100 -25)", "(start 0 0)", 1))
        with open(self.board, encoding="utf-8", newline="") as fh:
            doc = K.KiCadDocument.from_text(fh.read(), self.board)
        out = O.verify_all(doc, plan["operations"], records=records)
        self.assertFalse(out["all_verified"], out)
        self.assertIn("not the ones the run computed",
                      " ".join(out["operations"][0]["detail"].get("problems") or []))


class SetBoardOutlinePolygon(EditBase):
    """`set_board_outline` only writes a rectangle.

    A real enclosure forces a shape a rectangle cannot express (rounded
    corners, a notch). Before this operation the only way was to hand-edit
    Edge.Cuts outside every gate, and the failure mode of a half-done edit is
    not cosmetic: two outlines on one board are two boards to every rule that
    reads Edge.Cuts.
    """

    def apply(self, *ops):
        """`mech_unchanged` refuses a plan that touches Edge.Cuts unless it is
        told the shape is the subject of the edit. In this class it always is."""
        plan = E.propose(self.board, list(ops), {"allow_edge_changes": True})
        self.assertEqual(plan["blocking"], [], plan["blocking"])
        report = E.apply_plan(plan["plan_id"], confirm=True,
                              options=dict(engine=False, allow_edge_changes=True))
        self.assertTrue(report["gates"]["postconditions"]["all_verified"],
                        report["gates"]["postconditions"])
        return plan, report

    def test_replaces_the_edge_with_one_closed_polygon(self):
        shape = [[2, 2], [18, 2], [18, 17], [2, 17], [10, 9]]
        self.apply({"op": "set_board_outline_polygon", "points": shape})
        edge = self.editor().edge_items()
        # ONE item, not five: an unclosed polygon plus the old rectangle would
        # read as two boards
        self.assertEqual([S.head(n) for n in edge], ["gr_poly"])
        got = [(round(S.arg_num(p, 1), 4), round(-S.arg_num(p, 2), 4))
               for _path, p in S.walk(edge[0]) if S.head(p) == "xy"]
        for x, y in shape:
            self.assertIn((round(x, 4), round(y, 4)), got)
        # the file stores y DOWN, so (10, 9) mm Y up is written `(xy 10 -9)`
        self.assertIn((10.0, 9.0), got)
        self.assertEqual(len(got), 6, got)  # 5 vertices + the closing one

    def test_a_two_point_outline_is_refused(self):
        with self.assertRaises(O.OpError) as ctx:
            self.apply({"op": "set_board_outline_polygon",
                        "points": [[0, 0], [10, 10]]})
        self.assertEqual(ctx.exception.code, "ARG_INVALID")
        # refused means the old edge is still there - a delete that ran before
        # the check would leave the board with no edge at all
        self.assertEqual(len(self.editor().edge_items()), 4)
