"""Tests for `route.blockers` and the `board_blockers` tool.

What is under test is the claim the tool makes in its own description:

* **the mutability tag is a property of the object's kind, never a decision** -
  a pad is movable, another net's copper is reroutable, a pour / keepout / edge /
  graphic is fixed, and an object kind this module does not know is fixed too
  (refusing to call something movable is the safe direction);
* **"boxed in" and "the search failed upstream" are different answers** - the
  eight directions are probed with the same exact segment test the router uses,
  and a pad with an open side must not be reported as boxed in;
* **the numbers are the router's** - every distance comes from
  `Grid.seg_blockers`, so a wall that DRC would accept is not called a wall.

The boards here are synthetic and a few millimetres wide: the point is one pad
and one clearance, and a real library part brings 64 pads of noise to both.
"""
import os
import shutil
import tempfile
import unittest

from pcb_agent_mcp import edit as E
from pcb_agent_mcp import tools
from pcb_agent_mcp.edit import plan as P
from pcb_agent_mcp.route import blockers as BLOCK
from pcb_agent_mcp.route import grid as GRID
from pcb_agent_mcp.route import rules as R

SPEC = {"width_mm": 40, "height_mm": 30, "copper_layers": 2, "engine": False,
        "title": "blockers"}

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

class BlockerBase(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.mkdtemp(prefix="pcbblock-")
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

    def write_part(self, name, pads):
        with open(os.path.join(self.lib, name + ".kicad_mod"), "w",
                  encoding="utf-8", newline="\n") as fh:
            fh.write(footprint_body(name, pads))

    def apply(self, *ops):
        plan = E.propose(self.board, list(ops))
        self.assertEqual(plan["blocking"], [], plan["blocking"])
        return E.apply_plan(plan["plan_id"], confirm=True,
                            options={"engine": False})

    def place(self, name, pads, ref="U1", x=20.0, y=15.0, nets=None):
        self.write_part(name, pads)
        return {"op": "add_footprint", "ref": ref, "x": x, "y": y,
                "library": self.lib, "name": name,
                "pad_nets": nets or {}, "create_nets": True}

    def load(self):
        board, e = tools._load_board({"path": self.board})
        self.assertIsNone(e, e)
        return board

    def grid(self, layers=("F.Cu",), pitch=0.05):
        return GRID.Grid(self.load(), list(layers), pitch, R.resolve(self.load()))

    def call(self, **args):
        args.setdefault("path", self.board)
        return tools.call_tool("board_blockers", args)

# --------------------------------------------------------------------------
class TestMutabilityIsAPropertyOfTheKind(unittest.TestCase):
    """A tag, not a judgement: this module never decides what may be moved."""

    def test_every_kind_has_the_documented_tag(self):
        want = {"pad": "movable", "track": "reroutable", "via": "reroutable",
                "zone": "fixed", "pour": "fixed", "keepout": "fixed",
                "edge": "fixed", "graphic": "fixed"}
        for kind, tag in want.items():
            self.assertEqual(BLOCK.classify({"kind": kind})[0], tag, kind)
        self.assertEqual(dict(BLOCK.MUTABILITY), want)

    def test_an_unknown_kind_is_fixed_and_says_so(self):
        tag, why = BLOCK.classify({"kind": "banana"})
        self.assertEqual(tag, "fixed")
        self.assertIn("does not know how to move", why)

    def test_the_reason_names_the_thing_that_would_have_to_change(self):
        self.assertIn("the part can be moved", BLOCK.classify({"kind": "pad"})[1])
        self.assertIn("reroute", BLOCK.classify({"kind": "track"})[1])
        self.assertIn("design decision", BLOCK.classify({"kind": "pour"})[1])
        self.assertIn("rule", BLOCK.classify({"kind": "keepout"})[1])
        self.assertIn("outline", BLOCK.classify({"kind": "edge"})[1])

class TestSectors(unittest.TestCase):
    def test_eight_named_sectors_starting_east_going_counter_clockwise(self):
        self.assertEqual(BLOCK.SECTORS,
                         ("E", "NE", "N", "NW", "W", "SW", "S", "SE"))

    def test_a_direction_lands_in_the_sector_it_is_named_after(self):
        cases = {(1, 0): "E", (1, 1): "NE", (0, 1): "N", (-1, 1): "NW",
                 (-1, 0): "W", (-1, -1): "SW", (0, -1): "S", (1, -1): "SE"}
        for (dx, dy), name in cases.items():
            idx = BLOCK._sector_of(dx, dy)
            self.assertEqual(BLOCK.SECTORS[idx], name, (dx, dy))

    def test_a_zero_direction_is_not_an_error(self):
        self.assertEqual(BLOCK.SECTORS[BLOCK._sector_of(0.0, 0.0)], "E")

# --------------------------------------------------------------------------
class TestEmptySpaceIsNotABlocker(BlockerBase):
    """Nothing in the way must read as 'nothing in the way', not as 'boxed in'."""

    def setUp(self):
        BlockerBase.setUp(self)
        self.apply(self.place("Pad", [("1", 0, 0, 0.6, 0.6)], ref="J1",
                             x=10.0, y=10.0, nets={"1": "SIG"}))

    def test_nothing_within_reach_lists_nothing_and_says_the_route_was_lost_elsewhere(self):
        res = BLOCK.blockers(self.grid(), "SIG", (10.0, 10.0), "F.Cu")
        self.assertEqual(res["counts"]["total"], 0)
        self.assertEqual(len(res["open_sectors"]), 8)
        self.assertIn("nothing here is what stopped the route", res["diagnosis"])
        self.assertTrue(res["via_in_pad"]["fits"])

class TestABoxedInPad(BlockerBase):
    """Four neighbours of another net at 0.6 mm: the pad has no way out."""

    def setUp(self):
        BlockerBase.setUp(self)
        self.apply(self.place(
            "Cross",
            [("1", 0.0, 0.0, 0.6, 0.6),      # the pad that needs to leave
             ("2", 0.6, 0.0, 0.6, 0.6),
             ("3", -0.6, 0.0, 0.6, 0.6),
             ("4", 0.0, 0.6, 0.6, 0.6),
             ("5", 0.0, -0.6, 0.6, 0.6)],
            ref="U1", x=20.0, y=15.0,
            nets={"1": "SIG", "2": "GND", "3": "GND", "4": "GND", "5": "GND"}))

    def res(self, **kw):
        return BLOCK.blockers(self.grid(), "SIG", (20.0, 15.0), "F.Cu", **kw)

    def test_every_probed_side_is_blocked(self):
        res = self.res()
        self.assertEqual(res["open_sectors"], [])
        self.assertEqual(len(res["corridors"]), 8)
        self.assertFalse(any(c["open"] for c in res["corridors"]))
        self.assertGreater(res["counts"]["total"], 0)

    def test_the_diagnosis_says_boxed_in_and_names_what_could_move(self):
        res = self.res()
        self.assertIn("boxed in on every probed side", res["diagnosis"])
        # All four neighbours are pads: "movable", not "reroutable".
        self.assertEqual(res["counts"]["movable"], res["counts"]["total"])
        self.assertEqual(res["counts"]["reroutable"], 0)
        self.assertIn("could move", res["diagnosis"])

    def test_each_blocker_carries_how_short_it_is_and_where(self):
        res = self.res()
        top = res["blockers"][0]
        self.assertGreater(top["short_by_mm"], 0.0)
        self.assertAlmostEqual(top["needed_mm"], 0.2, places=6)
        self.assertTrue(top["sectors"])
        self.assertEqual(top["mutability"], "movable")

    def test_the_rule_is_stated_in_the_answer_not_left_to_the_reader(self):
        res = self.res()
        self.assertIn("not a decision made here", res["mutability_rule"])

    def test_the_probe_radius_is_one_pad_and_a_bit_by_default(self):
        res = self.res()
        # clearance 0.2 + track/2 0.125 + 0.4 = 0.725
        self.assertAlmostEqual(res["probe_radius_mm"], 0.725, places=4)

class TestOneOpenSideIsADifferentDiagnosis(BlockerBase):
    """A wall on one side only: the failure is upstream, and the answer says so.

    The probe reaches 2 mm here, because a small object inside one pad-and-a-bit
    blocks every ray by construction - the ray STARTS at the point, so anything
    nearer than clearance + track/2 walls the whole fan off regardless of side.
    One open side is only observable when the wall is further than that band and
    narrow enough that the 45-degree rays miss it.
    """

    def setUp(self):
        BlockerBase.setUp(self)
        self.apply(self.place(
            "Wall",
            [("1", 0.0, 0.0, 0.6, 0.6),
             ("2", -1.0, 0.0, 0.4, 0.4)],
            ref="U1", x=20.0, y=15.0, nets={"1": "SIG", "2": "GND"}))

    def test_the_open_sides_are_counted_and_named(self):
        res = BLOCK.blockers(self.grid(), "SIG", (20.0, 15.0), "F.Cu",
                             radius_mm=2.0)
        self.assertTrue(res["open_sectors"])
        self.assertNotIn("W", res["open_sectors"])
        self.assertIn("NOT boxed in", res["diagnosis"])
        self.assertIn("upstream", res["diagnosis"])

    def test_the_tool_turns_that_into_a_next_step_instead_of_a_shrug(self):
        out = self.call(ref="U1", pad="1", radius_mm=2.0)
        self.assertEqual(out["status"], "ok", out)
        self.assertIn("NOT boxed in", out["message"])
        # 0.0.107 removed the router, so the hint can no longer be "the router
        # will go further along the net". What it says instead has to name a tool
        # that exists (board_dangling / board_routability) and the one move that
        # is possible in this exact spot (a via in the pad), or it is a next step
        # the caller cannot take.
        self.assertIn("walk the net yourself", out["hint"])
        self.assertIn("via-in-pad", out["hint"])

class TestViaInPadIsReportedNotAssumed(BlockerBase):
    """A via needs 0.6 mm of copper; a 0.25 mm track needs a quarter of that."""

    def setUp(self):
        BlockerBase.setUp(self)
        # Neighbour copper edge at 0.45 mm from the centre pad's centre: the track
        # (half 0.125) passes at 0.325 > 0.2, the via (radius 0.3) fails at 0.15.
        self.apply(self.place(
            "Near",
            [("1", 0.0, 0.0, 0.6, 0.6),
             ("2", 0.75, 0.0, 0.6, 0.6)],
            ref="U1", x=20.0, y=15.0, nets={"1": "SIG", "2": "GND"}))

    def test_the_track_fits_but_the_via_does_not(self):
        res = BLOCK.blockers(self.grid(), "SIG", (20.0, 15.0), "F.Cu")
        self.assertTrue(res["open_sectors"])
        self.assertFalse(res["via_in_pad"]["fits"])
        self.assertTrue(res["via_in_pad"]["blockers"])
        self.assertEqual(res["via_in_pad"]["blockers"][0]["kind"], "pad")

class TestTheProbeReachIsTheCallers(BlockerBase):
    def setUp(self):
        BlockerBase.setUp(self)
        self.apply(self.place(
            "Far",
            [("1", 0.0, 0.0, 0.6, 0.6),
             ("2", 3.0, 0.0, 0.6, 0.6)],
            ref="U1", x=20.0, y=15.0, nets={"1": "SIG", "2": "GND"}))

    def test_a_short_probe_reports_nothing_and_a_long_one_finds_the_wall(self):
        grid = self.grid()
        near = BLOCK.blockers(grid, "SIG", (20.0, 15.0), "F.Cu", radius_mm=0.5)
        far = BLOCK.blockers(grid, "SIG", (20.0, 15.0), "F.Cu", radius_mm=3.5)
        self.assertEqual(near["counts"]["total"], 0)
        self.assertGreater(far["counts"]["total"], 0)
        self.assertAlmostEqual(far["probe_radius_mm"], 3.5, places=6)

    def test_the_assumptions_are_the_ones_the_probe_actually_leaned_on(self):
        res = BLOCK.blockers(self.grid(), "SIG", (20.0, 15.0), "F.Cu")
        self.assertIsInstance(res["assumptions"], list)
        # A bare board carries no min_copper_edge_clearance, and the router takes
        # KiCad's board-setup default in the pessimistic direction - the same
        # number this probe's edge test uses, so it must be declared here too.
        self.assertTrue(any("edge_clearance" in a for a in res["assumptions"]),
                        res["assumptions"])

# --------------------------------------------------------------------------
class TestTheTool(BlockerBase):
    def setUp(self):
        BlockerBase.setUp(self)
        self.apply(self.place("Pad", [("1", 0.0, 0.0, 0.6, 0.6),
                                      ("2", 2.0, 0.0, 0.6, 0.6)],
                             ref="J1", x=10.0, y=10.0,
                             nets={"1": "SIG", "2": "SIG"}))

    def test_a_pad_is_addressed_by_ref_and_number(self):
        out = self.call(ref="J1", pad="1")
        self.assertEqual(out["status"], "ok", out)
        self.assertEqual(out["value"]["net"], "SIG")
        self.assertEqual(out["value"]["layer"], "F.Cu")
        # The tool answers in the same frame board_pads reports, y down.
        self.assertEqual(out["value"]["at"], [10.0, 10.0])

    def test_a_missing_pad_is_refused_with_the_way_to_find_it(self):
        out = self.call(ref="J1", pad="99")
        self.assertEqual(out["status"], "error")
        self.assertEqual(out["reason_code"], "PAD_NOT_FOUND")
        self.assertIn("board_pads", out["hint"])

    def test_a_ref_without_a_pad_number_is_refused(self):
        out = self.call(ref="J1")
        self.assertEqual(out["reason_code"], "ARG_MISSING")

    def test_a_point_needs_a_net_and_a_layer(self):
        out = self.call(x=10.0, y=10.0)
        self.assertEqual(out["reason_code"], "NET_UNKNOWN")
        out = self.call(x=10.0, y=10.0, net="SIG")
        self.assertEqual(out["reason_code"], "ARG_MISSING")
        out = self.call(x=10.0, y=10.0, net="SIG", layer="F.Cu")
        self.assertEqual(out["status"], "ok", out)

    def test_a_layer_that_is_not_copper_is_refused(self):
        out = self.call(x=10.0, y=10.0, net="SIG", layer="F.SilkS")
        self.assertEqual(out["status"], "error")
        self.assertEqual(out["reason_code"], "NO_SUCH_LAYER")

    def test_the_answer_is_marked_as_computed_and_carries_its_precision(self):
        out = self.call(ref="J1", pad="1")
        self.assertIn("precision", out)
        self.assertIn("diagnosis, not a proof", out["precision"])
        self.assertIn("assumptions", out)

if __name__ == "__main__":
    unittest.main()
