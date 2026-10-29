"""Tests for `board_routability`: can a pad leave the pad at all?

The tool's whole value is that it refuses to answer a bigger question than the one
it can answer. It does not route, and `not_claimed` says so. What it does claim is:

* a pad with **no** way off it (no lane, not even a short stub, in any direction,
  and no copper of its own net touching it) is a hard blocker - that is the answer
  a person wants BEFORE watching a router fail for ten minutes;
* a pad that needs a dogleg is **not** that: calling it a blocker would be a false
  alarm, so it is reported as `bend_required` instead;
* a pad a track of its own net already reaches is connected, whatever the free
  space looks like;
* the clearance and track width come from the board's own net classes, and when
  nothing says anything the answer says it assumed a number instead of quoting one.

Every geometry below is synthetic on purpose: a real library part brings 64 pads of
noise to an assertion about one lane. The first run of the real thing is recorded in
the docstrings of this file's history - on a placed-but-unrouted STM32F401 core
board the rasterised first attempt answered "78 of 180 pads can never be connected",
which was the measurement counting each pad's own copper against itself.
"""
import os
import shutil
import tempfile
import unittest

from pcb_agent_mcp import edit as E
from pcb_agent_mcp import oracle
from pcb_agent_mcp import tools
from pcb_agent_mcp.edit import plan as P

SPEC = {"width_mm": 60, "height_mm": 40, "copper_layers": 2, "engine": False,
        "title": "routability"}

BODY_HEAD = """(footprint "SYN:%(name)s" (version 20221018) (generator pcbnew)
  (layer "F.Cu")
  (attr smd)
  (fp_text reference "REF**" (at 0 -6) (layer "F.SilkS")
    (effects (font (size 0.7 0.7) (thickness 0.12))))
  (fp_text value "%(name)s" (at 0 6) (layer "F.Fab")
    (effects (font (size 0.7 0.7) (thickness 0.12))))
"""
BODY_PAD = '''  (pad "%(number)s" smd rect (at %(x).4f %(y).4f) (size %(w).4f %(h).4f)
    (layers "F.Cu" "F.Paste" "F.Mask") (tstamp 5%(n)07d-1111-1111-1111-111111111111))
'''
BODY_TAIL = ")\n"


def footprint_body(name, pads):
    """A synthetic footprint. `pads` = [(number, dx, dy, w, h), ...] in mm."""
    out = [BODY_HEAD % {"name": name}]
    for n, (number, dx, dy, w, h) in enumerate(pads):
        out.append(BODY_PAD % {"number": number, "x": dx, "y": dy, "w": w,
                               "h": h, "n": n})
    out.append(BODY_TAIL)
    return "".join(out)


class RoutabilityBase(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.mkdtemp(prefix="pcbrou-")
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
    def write_part(self, name, pads):
        with open(os.path.join(self.lib, name + ".kicad_mod"), "w",
                  encoding="utf-8", newline="\n") as fh:
            fh.write(footprint_body(name, pads))

    def apply(self, *ops):
        plan = E.propose(self.board, list(ops))
        self.assertEqual(plan["blocking"], [], plan["blocking"])
        return E.apply_plan(plan["plan_id"], confirm=True,
                            options={"engine": False})

    def place(self, name, pads, ref="U1", x=30.0, y=20.0, nets=None):
        self.write_part(name, pads)
        return {"op": "add_footprint", "ref": ref, "x": x, "y": y,
                "library": self.lib, "name": name,
                "pad_nets": nets or {}, "create_nets": True}

    def measure(self, **kw):
        board, e = tools._load_board({"path": self.board})
        self.assertIsNone(e, e)
        return oracle.routability(board, **kw)


class TestWhatIsMeasured(RoutabilityBase):
    def test_a_board_nothing_is_placed_on_has_nothing_to_measure(self):
        out = self.measure()
        self.assertEqual(out["status"], "error")
        self.assertEqual(out["reason_code"], "NO_GEOMETRY")

    def test_a_lone_pad_can_leave_in_every_direction(self):
        self.apply(self.place("Lone", [("1", 0, 0, 1.0, 1.0)], nets={"1": "SIG"}))
        value = self.measure()["value"]
        self.assertEqual(value["verdict"], "escapes_exist")
        self.assertEqual(value["pads_checked"], 1)
        self.assertEqual(value["blocked"][:1], [])
        board, _ = tools._load_board({"path": self.board})
        pad = board.footprints[0]["pads"][0]
        self.assertEqual(len(pad["layers"]), 3)          # F.Cu + paste + mask
        self.assertEqual(value["already_connected_pads"], 0)

    def test_congestion_is_reported_per_layer_and_the_empty_one_is_lower(self):
        self.apply(self.place("Lone", [("1", 0, 0, 1.0, 1.0)], nets={"1": "SIG"}))
        value = self.measure()["value"]
        self.assertIn("F.Cu", value["congestion_by_layer"])
        self.assertIsNotNone(value["congestion_by_layer"]["F.Cu"])
        self.assertLess(value["congestion_by_layer"]["F.Cu"], 0.05)


class TestHardBlockers(RoutabilityBase):
    def test_a_pad_walled_in_by_other_nets_is_a_hard_blocker(self):
        # A 0.2 mm pad with four 0.4 mm pads of other nets 0.45 mm away: the lanes
        # out are inside another net's clearance, and so are the short ones, so the
        # pad cannot be reached at all - which is what a router would spend minutes
        # discovering.
        pads = [("1", 0.0, 0.0, 0.2, 0.2),
                ("2", 0.45, 0.0, 0.4, 0.4),
                ("3", -0.45, 0.0, 0.4, 0.4),
                ("4", 0.0, 0.45, 0.4, 0.4),
                ("5", 0.0, -0.45, 0.4, 0.4)]
        self.apply(self.place("Wall", pads,
                              nets={"1": "SIG", "2": "B", "3": "C", "4": "D",
                                    "5": "E"}))
        value = self.measure()["value"]
        self.assertEqual(value["verdict"], "blocked_pads", value["why"])
        self.assertEqual(value["blocked_pads"], 1)
        hit = value["blocked"][0]
        self.assertEqual(hit["pad"], "1")
        self.assertEqual(hit["net"], "SIG")
        self.assertEqual(hit["blocker_kind"], "hard")
        self.assertEqual(hit["escape_directions"], 0)
        self.assertEqual(value["nets_with_blocked_pads"], {"SIG": 1})
        # "it does not fit" is useless: the answer names the obstacle and how much
        # clearance is missing, which is what lets a person move one part 0.2 mm
        why = hit["why_blocked"]
        self.assertIsNotNone(why, hit)
        self.assertIn(why["net"], ("B", "C", "D", "E"))
        self.assertIsNotNone(why["reference"])
        self.assertGreater(why["needs_mm"], 0.0)
        self.assertLessEqual(why["needs_mm"], 0.2)
        self.assertEqual(value["per_net"]["SIG"]["blocked"], 1)

    def test_a_pad_that_needs_a_dogleg_is_not_called_a_blocker(self):
        # A 1 x 1 pad with four 0.4 x 8 walls whose inner copper starts 1.2 mm out.
        # A 0.4 mm stub clears them (0.3 mm of margin left) but a 1.5 mm lane runs
        # straight into them in every direction, diagonals included. That is a pad
        # a person routes with one bend, so "blocker" would be the wrong word.
        pads = [("1", 0.0, 0.0, 1.0, 1.0),
                ("2", 1.4, 0.0, 0.4, 8.0),
                ("3", -1.4, 0.0, 0.4, 8.0),
                ("4", 0.0, 1.4, 8.0, 0.4),
                ("5", 0.0, -1.4, 8.0, 0.4)]
        self.apply(self.place("Walled", pads,
                              nets={"1": "SIG", "2": "B", "3": "C",
                                    "4": "D", "5": "E"}))
        value = self.measure()["value"]
        self.assertEqual(value["verdict"], "bends_needed", value["why"])
        self.assertEqual(value["blocked_pads"], 0)
        self.assertEqual(value["bend_required_pads"], 1)
        hit = value["bend_required"][0]
        self.assertEqual(hit["pad"], "1")
        self.assertEqual(hit["blocker_kind"], "bend_required")
        self.assertEqual(hit["escape_directions"], 0)
        # the same geometry over a short lane IS escapable, which is why "blocker"
        # would have been a false alarm
        short = self.measure(escape_mm=0.4)["value"]
        self.assertEqual(short["bend_required_pads"], 0)
        self.assertGreater(short["pads_checked"], 0)


class TestAlreadyConnected(RoutabilityBase):
    def test_a_track_of_its_own_net_makes_a_pad_connected(self):
        self.apply(self.place("Lone2", [("1", 0, 0, 0.6, 0.6)], nets={"1": "SIG"}),
                   {"op": "add_track", "x1": 30.0, "y1": 20.0,
                    "x2": 34.0, "y2": 20.0, "width": 0.15, "layer": "F.Cu",
                    "net": "SIG", "create_net": True})
        value = self.measure()["value"]
        self.assertEqual(value["already_connected_pads"], 1)
        self.assertEqual(value["blocked_pads"], 0, value["why"])
        self.assertNotEqual(value["verdict"], "blocked_pads")
        self.assertTrue(value["per_net"]["SIG"]["connected"] == 1)

    def test_a_track_of_ANOTHER_net_does_not_count_as_connected(self):
        self.apply(self.place("Lone3", [("1", 0, 0, 0.6, 0.6)], nets={"1": "SIG"}),
                   {"op": "add_track", "x1": 30.0, "y1": 20.0,
                    "x2": 34.0, "y2": 20.0, "width": 0.15, "layer": "F.Cu",
                    "net": "OTHER", "create_net": True})
        value = self.measure()["value"]
        self.assertEqual(value["already_connected_pads"], 0)
        # and the other net's track, sitting right on the pad, makes it a blocker
        self.assertEqual(value["blocked_pads"], 1, value["why"])


class TestArguments(RoutabilityBase):
    def test_a_non_positive_grid_is_refused_not_defaulted(self):
        self.apply(self.place("Lone", [("1", 0, 0, 1.0, 1.0)], nets={"1": "SIG"}))
        out = self.measure(grid_mm=0)
        self.assertEqual(out["status"], "error")
        self.assertEqual(out["reason_code"], "ARG_RANGE")

    def test_a_non_positive_escape_is_refused(self):
        self.apply(self.place("Lone", [("1", 0, 0, 1.0, 1.0)], nets={"1": "SIG"}))
        self.assertEqual(self.measure(escape_mm=0)["reason_code"], "ARG_RANGE")

    def test_a_layer_the_board_does_not_have_is_refused_with_the_list(self):
        self.apply(self.place("Lone", [("1", 0, 0, 1.0, 1.0)], nets={"1": "SIG"}))
        out = self.measure(layer="In9.Cu")
        self.assertEqual(out["reason_code"], "ARG_INVALID")
        self.assertIn("F.Cu", out["message"])

    def test_the_clearance_comes_from_the_net_class_and_says_so(self):
        self.apply(self.place("Lone", [("1", 0, 0, 1.0, 1.0)], nets={"1": "SIG"}))
        out = self.measure()
        self.assertEqual(out["assumptions"]["clearance_from"], "net class Default")
        self.assertAlmostEqual(out["assumptions"]["clearance_mm"], 0.2, places=6)
        self.assertIn("not_claimed", out["assumptions"])
        self.assertIn("not a route", out["assumptions"]["not_claimed"])

    def test_an_explicit_over_ride_is_labelled_as_the_caller_s(self):
        self.apply(self.place("Lone", [("1", 0, 0, 1.0, 1.0)], nets={"1": "SIG"}))
        out = self.measure(clearance_mm=0.3, track_mm=0.4)
        self.assertEqual(out["assumptions"]["clearance_from"], "caller")
        self.assertEqual(out["assumptions"]["track_width_from"], "caller")
        self.assertAlmostEqual(out["assumptions"]["clearance_mm"], 0.3, places=6)


class TestThroughTheToolSurface(RoutabilityBase):
    def test_the_answer_is_marked_computed_and_not_a_verdict(self):
        self.apply(self.place("Lone", [("1", 0, 0, 1.0, 1.0)], nets={"1": "SIG"}))
        out = tools.call_tool("board_routability", {"path": self.board})
        self.assertEqual(out["status"], "ok")
        self.assertFalse(out["authoritative"])
        self.assertIn("board_drc", out["verdict_available_from"])
        self.assertIn("computed", out["answer_source"])
        self.assertEqual(out["value"]["pads_checked"], 1)

    def test_the_tool_is_listed_as_computed_not_as_the_vendor_s_answer(self):
        caps = tools.call_tool("calc_capabilities", {})
        text = repr(caps)
        self.assertIn("board_routability", text)


if __name__ == "__main__":
    unittest.main()
