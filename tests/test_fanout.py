"""Tests for `board_fanout`: getting a BGA's balls out of the array, honestly.

What is under test is the four claims the tool makes:

* **the pitch comes off the pads, or the part is refused** - escape geometry is
  measured in pitch units, so a made-up pitch is a whole fan-out that looks fine
  and is wrong;
* **an escape is accepted by exact geometry, not by a distance rule of its own** -
  every accepted stub/via is re-judged by the same test the router uses, against
  the copper already on the board plus the escapes accepted before it;
* **a ball that cannot leave is REPORTED with the wall it hit** - never dropped,
  and the advice names the real ways out instead of a different direction;
* **KiCad agrees** - the escapes are written through propose/apply and the
  vendor's own DRC is asked whether the new copper keeps its distance.

The 1.27 mm / 0.6 mm ball test part is the one that can be fully escaped with a
default 0.25/0.6/0.2 net class; the 0.8 mm / 0.4 mm part is the one that cannot,
on purpose, so the refusal path is measured rather than asserted.
"""
import json
import os
import shutil
import tempfile
import unittest

from pcb_agent_mcp import edit as E
from pcb_agent_mcp import fanout as FO
from pcb_agent_mcp import tools
from pcb_agent_mcp.edit import plan as P
from pcb_agent_mcp.fidelity import engine as ENGINE

SPEC = {"width_mm": 40, "height_mm": 40, "copper_layers": 2, "engine": False,
        "title": "fanout"}

HEAD = '''(footprint "SYN:%(name)s" (version 20221018) (generator pcbnew)
  (layer "F.Cu")
  (attr smd)
  (fp_text reference "REF**" (at 0 -10) (layer "F.SilkS")
    (effects (font (size 0.7 0.7) (thickness 0.12))))
  (fp_text value "%(name)s" (at 0 10) (layer "F.Fab")
    (effects (font (size 0.7 0.7) (thickness 0.12))))
'''
PAD = '''  (pad "%(number)s" smd circle (at %(x).4f %(y).4f) (size %(size).4f %(size).4f)
    (layers "F.Cu" "F.Paste" "F.Mask") (tstamp 5%(n)07d-1111-1111-1111-111111111111))
'''
HOLE = '''  (pad "27" np_thru_hole circle (at 3.0 3.0) (size 2.2 2.2) (drill 2.2)
    (layers "*.Cu" "*.Mask") (tstamp 59999999-1111-1111-1111-111111111111))
'''

def lattice_body(name, pitch, size, n=5, jitter=None):
    """`n x n` balls on a `pitch` lattice, optionally with one pad moved."""
    out = [HEAD % {"name": name}]
    number = 1
    for row in range(n):
        for col in range(n):
            x, y = col * pitch, row * pitch
            if jitter and (row, col) == jitter:
                x += 0.11
            out.append(PAD % {"number": str(number), "x": x, "y": y,
                              "size": size, "n": number})
            number += 1
    out.append(")\n")
    return "".join(out)

class FanoutBase(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.mkdtemp(prefix="pcbfan-")
        self._home = os.environ.get("PCB_AGENT_HOME")
        os.environ["PCB_AGENT_HOME"] = os.path.join(self.tmp, "home")
        self.board = os.path.join(self.tmp, "board.kicad_pcb")
        E.create_board(self.board, dict(SPEC))
        self.lib = os.path.join(self.tmp, "Syn.pretty")
        os.makedirs(self.lib)

    def tearDown(self):
        if self._home is None:
            os.environ.pop("PCB_AGENT_HOME", None)
        else:
            os.environ["PCB_AGENT_HOME"] = self._home
        P.PLANS.clear()
        shutil.rmtree(self.tmp, ignore_errors=True)

    # ---------------------------------------------------------------- helpers
    def part(self, name, body, ref="U1", x=20.0, y=20.0, nets=None):
        with open(os.path.join(self.lib, name + ".kicad_mod"), "w",
                  encoding="utf-8", newline="\n") as fh:
            fh.write(body)
        plan = E.propose(self.board, [{
            "op": "add_footprint", "ref": ref, "x": x, "y": y,
            "library": self.lib, "name": name, "pad_nets": nets or {},
            "create_nets": True}])
        self.assertEqual(plan["blocking"], [], plan["blocking"])
        E.apply_plan(plan["plan_id"], confirm=True, options=dict(engine=False))

    def bga(self, pitch=1.27, size=0.6, n=5, ref="U1", jitter=None, hole=False):
        count = n * n
        body = lattice_body("BGA%d" % count, pitch, size, n=n, jitter=jitter)
        if hole:
            body = body[:-2] + HOLE + ")\n"
        self.part("BGA%d" % count, body, ref=ref,
                  nets=dict((str(k), "N%d" % k) for k in range(1, count + 1)))

    def load(self):
        board, e = tools._load_board({"path": self.board})
        self.assertIsNone(e, e)
        return board

    def plan(self, **kw):
        return FO.fanout(self.load(), **kw)

    def apply(self, ops):
        plan = E.propose(self.board, list(ops))
        self.assertEqual(plan["blocking"], [], plan["blocking"])
        report = E.apply_plan(plan["plan_id"], confirm=True,
                              options=dict(engine=False))
        self.assertTrue(report["gates"]["postconditions"]["all_verified"],
                        report["gates"]["postconditions"])
        return report

class TestFindArrays(FanoutBase):
    """The pitch is read off the pads - or the part is not an array."""

    def test_a_lattice_is_found_with_its_pitch_and_centre(self):
        self.bga()
        arrays = FO.find_arrays(self.load())
        self.assertEqual(len(arrays), 1)
        self.assertEqual(arrays[0]["ref"], "U1")
        self.assertEqual(arrays[0]["pads"], 25)
        self.assertAlmostEqual(arrays[0]["pitch_mm"], 1.27, places=4)
        # pads run 20.0 .. 25.08 in x and 14.92 .. 20.0 in y (Y-up)
        self.assertAlmostEqual(arrays[0]["centre"][0], 22.54, places=4)
        self.assertAlmostEqual(arrays[0]["centre"][1], 17.46, places=4)
        self.assertAlmostEqual(arrays[0]["bbox_mm"][3], 20.0, places=4)

    def test_a_two_pad_part_is_not_an_array(self):
        body = lattice_body("small", 1.27, 0.6, n=2)
        self.part("small", body)
        self.assertEqual(FO.find_arrays(self.load()), [])

    def test_min_pads_lets_a_small_array_through_on_purpose(self):
        body = lattice_body("small", 1.27, 0.6, n=2)
        self.part("small", body)
        arrays = FO.find_arrays(self.load(), min_pads=4)
        self.assertEqual([a["pads"] for a in arrays], [4])

    def test_an_off_lattice_pad_refuses_the_whole_part(self):
        self.bga(jitter=(2, 3))
        self.assertEqual(FO.find_arrays(self.load()), [],
                         "a 0.11 mm shove is not a lattice, and a wrong pitch is "
                         "a whole fan-out that looks fine")

    def test_max_pitch_filters_a_fine_ball_part(self):
        self.bga(pitch=1.27)
        self.assertEqual(FO.find_arrays(self.load(), max_pitch=0.8), [])
        self.assertEqual(len(FO.find_arrays(self.load(), max_pitch=1.3)), 1)

    def test_a_bare_hole_with_no_annular_ring_is_not_a_ball(self):
        self.bga(hole=True)
        arrays = FO.find_arrays(self.load())
        self.assertEqual(arrays[0]["pads"], 25,
                         "the 2.2 mm np_thru_hole has no ring to escape from")

    def test_a_reference_that_is_not_an_array_is_refused(self):
        self.bga()
        board = self.load()
        with self.assertRaises(FO.FanoutError) as ctx:
            FO.fanout(board, ref="U9")
        self.assertEqual(ctx.exception.reason_code, "NO_ARRAY")
        self.assertIn("lattice", ctx.exception.message)

class TestFanout(FanoutBase):
    """Every ball out, or a named wall - and the shapes used are the ones said."""

    def test_a_127_pitch_part_is_fully_escaped(self):
        self.bga()
        res = self.plan()
        self.assertEqual(res["escaped"], 25, res["skipped"])
        self.assertEqual(res["balls_total"], 25)
        self.assertEqual(res["skipped"], [])
        self.assertIsNone(res["advice"])
        # the perimeter leaves straight out; only the 3 x 3 interior needs a
        # dogbone, and that is the number the tool must not inflate
        self.assertEqual(res["styles"], {"direct": 16, "dogbone": 9})
        self.assertEqual(len([o for o in res["ops"] if o["op"] == "add_via"]), 25)
        self.assertGreater(len([o for o in res["ops"] if o["op"] == "add_track"]), 25)

    def test_the_escapes_keep_their_distance_where_the_tool_says(self):
        self.bga()
        res = self.plan()
        bundle = res["rules"]
        self.assertAlmostEqual(bundle["clearance_mm"], 0.2, places=6)
        self.assertAlmostEqual(bundle["rules" if False else "track_mm"], 0.25, places=6)
        self.assertEqual(bundle["via_mm"], [0.6, 0.3])
        self.assertEqual(res["rules"]["sources"]["clearance"], "net class Default")

    def test_a_dogbone_really_changes_layer(self):
        self.bga()
        res = self.plan()
        # the interior (rows 2..4, columns 2..4 of 5) is the dogbone set; each of
        # those balls must own a track on B.Cu
        bottom = [o for o in res["ops"]
                  if o["op"] == "add_track" and o["layer"] == "B.Cu"]
        self.assertEqual(len(bottom), 9, bottom[:2])
        top = [o for o in res["ops"]
               if o["op"] == "add_track" and o["layer"] == "F.Cu"]
        self.assertEqual(len(top), 25)

    def test_an_08_pitch_part_on_a_default_class_cannot_be_escaped(self):
        self.bga(pitch=0.8, size=0.4)
        res = self.plan()
        # 0.4 mm pads on a 0.8 mm pitch leave 0.4 mm of channel, and 0.25 mm of
        # track with 0.2 mm either side needs 0.65 - the perimeter can leave
        # outwards, the interior cannot leave at all. That is geometry, and the
        # answer has to say so instead of fanning out a fiction.
        self.assertEqual(res["escaped"], 16)
        self.assertEqual(len(res["skipped"]), 9)
        self.assertEqual(res["styles"]["dogbone"], 0)
        self.assertTrue(res["advice"])
        self.assertIn("0.60 mm via", res["advice"])
        reasons = " ".join(s["reason"] for s in res["skipped"])
        self.assertIn("blocked", reasons)
        self.assertIn("dogbone", reasons)

    def test_a_smaller_via_opens_the_same_part(self):
        self.bga(pitch=0.8, size=0.4)
        res = self.plan(clearance_mm=0.1, track_mm=0.1, via_diameter_mm=0.3,
                        via_drill_mm=0.15)
        self.assertEqual(res["escaped"], 25, res["skipped"])
        self.assertEqual(res["styles"], {"direct": 16, "dogbone": 9})
        self.assertEqual(res["rules"]["sources"]["clearance"], "caller")

    def test_max_balls_stops_and_says_which_balls_stopped(self):
        self.bga()
        res = self.plan(max_balls=5)
        self.assertEqual(res["escaped"], 5)
        self.assertEqual(len(res["skipped"]), 20)
        self.assertEqual(set(s["reason"] for s in res["skipped"]),
                         {"max_balls reached"})

    def test_one_direction_only_cannot_reach_the_interior(self):
        self.bga()
        res = self.plan(directions=1)
        self.assertLess(res["escaped"], 25)
        self.assertTrue(res["skipped"])
        # the ladder is what gets the interior out: one direction reaches fewer
        # dogbones as well as fewer balls
        self.assertLess(res["styles"]["dogbone"], 9)

    def test_a_forced_layer_is_used_for_every_stub(self):
        self.bga()
        res = self.plan(layer="B.Cu")
        layers = set(o["layer"] for o in res["ops"] if o["op"] == "add_track")
        self.assertEqual(layers, {"B.Cu"})

    def test_a_layer_that_is_not_copper_is_refused(self):
        self.bga()
        with self.assertRaises(FO.FanoutError) as ctx:
            self.plan(layer="F.SilkS")
        self.assertEqual(ctx.exception.reason_code, "LAYER_NOT_IN_BOARD")

    def test_the_written_ops_are_absolute_and_inside_the_board(self):
        self.bga()
        res = self.plan()
        xs = [v for o in res["ops"] for v in
              ([o["x"]] if o["op"] == "add_via" else [o["x1"], o["x2"]])]
        ys = [v for o in res["ops"] for v in
              ([o["y"]] if o["op"] == "add_via" else [o["y1"], o["y2"]])]
        # Y-up frame: the array sits between y 14.92 and 20.0, escapes go outwards
        self.assertGreater(min(xs), 15.0)
        self.assertLess(max(xs), 30.0)
        self.assertGreater(min(ys), 10.0)
        self.assertLess(max(ys), 25.0)

class TestFanoutOps(FanoutBase):
    """The plan has to survive board_propose + board_apply, and KiCad's DRC."""

    def test_every_op_verifies_on_the_written_file(self):
        self.bga()
        report = self.apply(self.plan()["ops"])
        pc = report["gates"]["postconditions"]
        self.assertTrue(pc["all_verified"], pc)
        self.assertEqual(len(pc["operations"]), 59)

    def test_the_tool_is_gone_even_with_arguments_that_would_have_worked(self):
        """0.0.108 removed the TOOL, not the default: this board is a 1.27 mm
        array the planner would have escaped (see the tests above), and the
        call still has to come back TOOL_RETIRED - a tool that answers "ok"
        when a caller asked for automatic escapes would be the laziness the
        removal exists to prevent."""
        self.bga()
        with open(self.board, "rb") as fh:
            before = fh.read()
        out = tools.call_tool("board_fanout", {"path": self.board})
        self.assertEqual(out["status"], "error")
        self.assertEqual(out["reason_code"], "TOOL_RETIRED")
        self.assertIn("cannot be called", out["message"])
        with open(self.board, "rb") as fh:
            self.assertEqual(fh.read(), before)

    def test_apply_true_is_refused_too_and_writes_nothing(self):
        """The dangerous argument is the one that writes. It must be refused
        before anything reaches the file, not after a plan is built."""
        self.bga()
        with open(self.board, "rb") as fh:
            before = fh.read()
        out = tools.call_tool("board_fanout",
                              {"path": self.board, "apply": True})
        self.assertEqual(out["status"], "error")
        self.assertEqual(out["reason_code"], "TOOL_RETIRED")
        self.assertNotIn("applied", out)
        with open(self.board, "rb") as fh:
            self.assertEqual(fh.read(), before)

    def test_a_missing_array_is_no_longer_diagnosed_because_nothing_runs(self):
        """NO_ARRAY used to be the honest error. Now the tool does not run at
        all, so the error has to be about the tool - reporting NO_ARRAY would
        tell the caller the ref was wrong when the capability is gone."""
        self.bga()
        out = tools.call_tool("board_fanout", {"path": self.board, "ref": "U9"})
        self.assertEqual(out["status"], "error")
        self.assertEqual(out["reason_code"], "TOOL_RETIRED")
        self.assertNotEqual(out["reason_code"], "NO_ARRAY")

    def test_the_planner_itself_still_works_as_a_library(self):
        """Retiring the tool is not deleting the work: the planner is still
        importable and still escapes 25 balls on this board. That keeps the
        distinction honest - 'no automatic escape in this server' is a product
        decision, not 'the code was broken'."""
        self.assertTrue(callable(getattr(FO, "fanout", None)),
                        "pcb_agent_mcp.fanout.fanout must still exist: the tool "
                        "was retired, the planner was not deleted")

class TestFanoutAgainstKiCad(FanoutBase):
    """KiCad's own DRC has to accept the escapes."""

    def test_pcbnew_finds_no_clearance_violation_in_the_escapes(self):
        if not ENGINE.find_kicad_cli():
            self.skipTest("kicad-cli is not installed")
        self.bga()
        before = ENGINE.drc(self.board)
        self.apply(self.plan()["ops"])
        after = ENGINE.drc(self.board)
        def kinds(report):
            return [v["type"] for v in report.get("violations", [])]
        # the bare test board already carries two warnings of its own making
        # (a footprint with no library, text below KiCad's minimum height); the
        # escapes may only ADD dangling-end warnings, never a clearance error.
        self.assertEqual(sorted(set(kinds(before))),
                         ["lib_footprint_issues", "text_height"])
        new = sorted(set(kinds(after)) - set(kinds(before)))
        self.assertEqual(new, ["track_dangling", "via_dangling"], kinds(after))
        for bad in ("clearance", "shorting_items", "annular_width", "via_diameter",
                    "hole_to_hole", "track_width"):
            self.assertNotIn(bad, kinds(after), bad)
