"""Tests for the router: `board_route`, its rules, its rasters, its refusals.

What is under test is not "does it look nice" but the four claims the tool makes
in its own description:

* **the rules come from the board or the call, never from the router's
  imagination** - a board with no clearance anywhere is refused, not guessed at;
* **a raster cell is never evidence** - every emitted segment is re-checked
  against exact polygon geometry, and these tests measure that distance
  themselves instead of trusting the router's own verdict;
* **nothing is written unless it was asked for, and a partial board is refused
  unless it was asked for** - a half-routed board that looks finished is the
  worst answer this tool could give;
* **the pessimistic raster is pessimistic about the right thing** - a via needs
  more room than a track, and the maps say so.

Every board here is synthetic and a few millimetres wide, because the point is a
lane and a clearance, and a real library part brings 64 pads of noise to both.
The real-board numbers live in docs/27-自动布线器.md, not here.
"""
import math
import os
import shutil
import tempfile
import unittest
from types import SimpleNamespace

from pcb_agent_mcp import edit as E
from pcb_agent_mcp import geometry as GEO
from pcb_agent_mcp import tools
from pcb_agent_mcp.edit import plan as P
from pcb_agent_mcp.route import grid as GRID
from pcb_agent_mcp.route import rules as R
from pcb_agent_mcp.route import router

SPEC = {"width_mm": 60, "height_mm": 40, "copper_layers": 2, "engine": False,
        "title": "route"}

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

def read_bytes(path):
    with open(path, "rb") as fh:
        return fh.read()

def footprint_body(name, pads):
    """A synthetic footprint. `pads` = [(number, dx, dy, w, h), ...] in mm."""
    out = [BODY_HEAD % {"name": name}]
    for n, (number, dx, dy, w, h) in enumerate(pads):
        out.append(BODY_PAD % {"number": number, "x": dx, "y": dy, "w": w,
                               "h": h, "n": n})
    out.append(BODY_TAIL)
    return "".join(out)

class RouteBase(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.mkdtemp(prefix="pcbroute-")
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

    def load(self):
        board, e = tools._load_board({"path": self.board})
        self.assertIsNone(e, e)
        return board

    def route(self, **kw):
        board, e = tools._load_board({"path": self.board})
        self.assertIsNone(e, e)
        return router.plan(board, **kw)

    def call(self, **args):
        args.setdefault("path", self.board)
        return tools.call_tool("board_route", args)

class TestRulesAreResolvedNotInvented(RouteBase):
    """The router may not draw copper to a rule it made up."""

    def test_clearance_and_width_come_from_the_net_class_and_say_so(self):
        bundle = R.resolve(self.load())
        self.assertAlmostEqual(bundle["clearance_mm"], 0.2, places=6)
        self.assertAlmostEqual(bundle["track_mm"], 0.25, places=6)
        self.assertEqual(bundle["sources"]["clearance"], "net class Default")
        self.assertEqual(bundle["sources"]["track_width"], "net class Default")
        # Edge clearance really is absent from a bare board - but "absent" is not
        # "unchecked": the referee applies KiCad's 0.5 mm board-setup default
        # whether or not the file says so, so the router takes that value in the
        # pessimistic direction and declares it. This assertion used to demand
        # `is None`, which is exactly what let six tracks land 0.275 mm from
        # Edge.Cuts on the F401 core board.
        self.assertAlmostEqual(bundle["edge_clearance_mm"],
                               R.DEFAULT_EDGE_CLEARANCE_MM, places=6)
        self.assertEqual(bundle["sources"]["edge_clearance"],
                         "assumed: KiCad board-setup default")
        self.assertEqual([a["fact"] for a in bundle["assumed"]], ["edge_clearance"])

    def test_the_caller_can_set_the_edge_rule_and_then_it_is_not_assumed(self):
        bundle = R.resolve(self.load(), edge_clearance_mm=0.3)
        self.assertAlmostEqual(bundle["edge_clearance_mm"], 0.3, places=6)
        self.assertEqual(bundle["sources"]["edge_clearance"], "caller")
        self.assertEqual([a["fact"] for a in bundle["assumed"]], [])

    def test_a_board_with_no_rule_anywhere_is_refused(self):
        blank = SimpleNamespace(rules={}, nets={})
        with self.assertRaises(R.RulesError) as ctx:
            R.resolve(blank)
        self.assertEqual(ctx.exception.reason_code, "RULES_ABSENT")
        self.assertIn("will not guess", ctx.exception.hint)

    def test_the_caller_can_override_and_the_source_says_which_won(self):
        bundle = R.resolve(self.load(), clearance_mm=0.15, track_mm=0.15)
        self.assertAlmostEqual(bundle["clearance_mm"], 0.15, places=6)
        self.assertEqual(bundle["sources"]["clearance"], "caller")
        self.assertEqual(bundle["sources"]["track_width"], "caller")

class TestRastersArePessimistic(RouteBase):
    """A cell the map calls free must be free; a via needs more room than a track."""

    def test_a_via_needs_more_room_than_a_track(self):
        board = self.load()
        bundle = R.resolve(board)
        # One 1.0 mm square pad of net A, copper edge at x = 30.5.
        pad = {"kind": "pad", "net": "A", "layers": ["F.Cu"], "ref": "U1.1",
               "poly": [(30.0, 20.5), (30.5, 20.5), (30.5, 19.5), (30.0, 19.5)]}
        board.footprints.append({"ref": "U1", "pads": [], "graphics": []})
        grid = GRID.Grid(board, ["F.Cu"], 0.05, bundle)
        grid.add_copper(pad)
        near = grid.node(30.9, 20.0)          # 0.4 mm from the copper edge
        self.assertTrue(grid.free(near[0], near[1], "F.Cu", "B"))
        self.assertFalse(grid.free_via(near[0], near[1], "F.Cu", "B"))
        own = grid.node(30.9, 20.0)
        self.assertTrue(grid.free(own[0], own[1], "F.Cu", "A"))
        outside = grid.node(-5.0, 20.0)       # off the board entirely
        self.assertFalse(grid.free(outside[0], outside[1], "F.Cu", "A"))

class TestOneNetRoute(RouteBase):
    def setUp(self):
        RouteBase.setUp(self)
        self.apply(self.place("Pad", [("1", 0, 0, 1.0, 1.0)], ref="J1", x=10.0, y=8.0,
                             nets={"1": "SIG"}),
                    self.place("Pad", [("1", 0, 0, 1.0, 1.0)], ref="J2", x=40.0, y=8.0,
                             nets={"1": "SIG"}))

    def test_a_two_pad_net_gets_one_legal_track(self):
        res = self.route(nets=["SIG"])
        self.assertEqual(res["failed"], [])
        sig = res["nets"][0]
        self.assertEqual(sig["status"], "routed")
        self.assertEqual(sig["pads"], 2)
        self.assertEqual(sig["vias"], 0)
        self.assertGreaterEqual(sig["segments"], 1)
        self.assertGreater(sig["length_mm"], 25.0)
        self.assertLess(sig["length_mm"], 35.0)
        self.assertEqual(res["rejected"], [])

    def test_the_tool_says_who_will_write_and_does_not_write(self):
        before = read_bytes(self.board)
        out = self.call(nets=["SIG"])
        self.assertEqual(out["status"], "ok")
        self.assertEqual(out["value"]["nets_routed"], 1)
        self.assertGreaterEqual(out["value"]["segments"], 1)
        self.assertGreater(out["value"]["track_length_mm"], 25.0)
        self.assertTrue(out["assumptions"]["writes_nothing"])
        self.assertIn("apply=true", out["next_step"])
        self.assertEqual(read_bytes(self.board), before)

class TestTheExactCheckIsTheJudge(RouteBase):
    """A track the raster proposed is still measured, independently, here."""

    def setUp(self):
        RouteBase.setUp(self)
        self.apply(self.place("Pad", [("1", 0, 0, 1.0, 1.0)], ref="J1", x=10.0, y=8.0,
                              nets={"1": "SIG"}),
                   self.place("Pad", [("1", 0, 0, 1.0, 1.0)], ref="J2", x=40.0, y=8.0,
                              nets={"1": "SIG"}),
                   # a 6 mm wide foreign pad sitting in the straight line
                   self.place("Wall", [("1", 0, 0, 4.0, 6.0)], ref="U2", x=25.0, y=8.0,
                              nets={"1": "OTHER"}))

    def test_the_route_goes_around_and_the_gap_is_measured_here(self):
        res = self.route(nets=["SIG"], clearance_mm=0.2)
        self.assertEqual(res["failed"], [], res["failed"])
        self.assertTrue(all(o["net"] == "SIG" for o in res["ops"]))
        wall = [(25.0 - 2.0, 8.0 - 3.0), (25.0 + 2.0, 8.0 - 3.0),
                (25.0 + 2.0, 8.0 + 3.0), (25.0 - 2.0, 8.0 + 3.0)]
        for op in res["ops"]:
            if op["op"] != "add_track":
                continue
            a = (op["x1"], op["y1"])
            b = (op["x2"], op["y2"])
            half = op["width"] / 2.0
            gap = GEO.dist_point_polygon(a, wall)
            for k in range(1, 11):
                t = k / 10.0
                p = (a[0] + t * (b[0] - a[0]), a[1] + t * (b[1] - a[1]))
                gap = min(gap, GEO.dist_point_polygon(p, wall))
            self.assertGreaterEqual(gap - half, 0.2 - 1e-6,
                                    "segment %s-%s is %.4f mm from the foreign pad"
                                    % (a, b, gap - half))

class TestPartialBoardsAreRefused(RouteBase):
    """`allow_partial` exists for a reason; the default must not be it."""

    def setUp(self):
        RouteBase.setUp(self)
        self.apply(
            self.place("Pad", [("1", 0, 0, 1.0, 1.0)], ref="J1", x=10.0, y=8.0,
                       nets={"1": "SIG"}),
            self.place("Pad", [("1", 0, 0, 1.0, 1.0)], ref="J2", x=40.0, y=8.0,
                       nets={"1": "SIG"}),
            self.place("Pad", [("1", 0, 0, 1.0, 1.0)], ref="J3", x=10.0, y=20.0,
                       nets={"1": "WALLED"}),
            self.place("Pad", [("1", 0, 0, 1.0, 1.0)], ref="J4", x=40.0, y=20.0,
                       nets={"1": "WALLED"}),
            # a keep-out box around J4's pad: its terminal has nowhere to start
            {"op": "add_keepout", "x0": 38.0, "y0": 18.0, "x1": 42.0, "y1": 22.0,
             "layers": ["F.Cu", "B.Cu"], "rules": ["tracks", "vias"]})

    def test_one_net_that_cannot_leave_blocks_the_write(self):
        before = read_bytes(self.board)
        out = self.call(apply=True)
        self.assertEqual(out["status"], "error")
        self.assertEqual(out["reason_code"], "ROUTE_INCOMPLETE")
        self.assertEqual(out["value"]["nets_routed"], 1)
        self.assertEqual(out["value"]["nets_failed"], 1)
        self.assertEqual(read_bytes(self.board), before)

    def test_allow_partial_writes_only_what_routed(self):
        out = self.call(apply=True, allow_partial=True)
        self.assertEqual(out["status"], "ok", out.get("message"))
        self.assertEqual(out["applied"]["status"], "ok", out["applied"])
        board = self.load()
        tracks = [it for it in board.tracks if it.get("net") == "SIG"]
        self.assertGreaterEqual(len(tracks), 1)
        self.assertEqual([it for it in board.tracks if it.get("net") == "WALLED"], [])

    def test_incremental_does_not_lay_a_second_copy(self):
        self.call(apply=True, allow_partial=True)
        again = self.call(nets=["SIG"], incremental=True)
        self.assertEqual(again["status"], "ok", again.get("message"))
        self.assertEqual(again["value"]["nets_routed"], 1)
        self.assertEqual(again["value"]["segments"], 0)

    def test_a_net_that_does_not_exist_is_refused_with_a_reason(self):
        out = self.call(nets=["NOPE"])
        self.assertEqual(out["status"], "error")
        self.assertEqual(out["reason_code"], "NO_GEOMETRY")

class TestKeepoutsAreReal(RouteBase):
    """A keepout is not a clearance: copper may not enter its interior at all.

    Two separate claims, because the router got both wrong at once: the search
    treated a keepout as a band around its rim (so it walked through the middle
    of one and called it free), and the exact check that is the judge did not
    look at keepouts at all (so a segment could cross one and be reported clean).
    """

    BOX = [(23.0, 5.0), (27.0, 5.0), (27.0, 11.0), (23.0, 11.0)]

    def setUp(self):
        RouteBase.setUp(self)
        self.apply(self.place("Pad", [("1", 0, 0, 1.0, 1.0)], ref="J1", x=10.0, y=8.0,
                              nets={"1": "SIG"}),
                   self.place("Pad", [("1", 0, 0, 1.0, 1.0)], ref="J2", x=40.0, y=8.0,
                              nets={"1": "SIG"}),
                   {"op": "add_keepout", "x0": 23.0, "y0": 5.0, "x1": 27.0,
                    "y1": 11.0, "layers": ["F.Cu", "B.Cu"],
                    "rules": ["tracks", "vias"]})

    def test_the_route_goes_around_and_never_enters_the_box(self):
        res = self.route(nets=["SIG"])
        self.assertEqual(res["failed"], [], res["failed"])
        box = self.BOX
        for op in res["ops"]:
            if op["op"] == "add_via":
                self.assertFalse(GEO.point_in_polygon((op["x"], op["y"]), box),
                                 "via at %s,%s is inside the keepout"
                                 % (op["x"], op["y"]))
                continue
            half = op["width"] / 2.0
            a = (op["x1"], op["y1"])
            b = (op["x2"], op["y2"])
            steps = max(8, int(math.hypot(b[0] - a[0], b[1] - a[1]) / 0.05))
            for k in range(steps + 1):
                t = k / float(steps)
                p = (a[0] + t * (b[0] - a[0]), a[1] + t * (b[1] - a[1]))
                self.assertFalse(GEO.point_in_polygon(p, box),
                                 "track %s-%s runs through the keepout at %s"
                                 % (a, b, p))
                self.assertGreaterEqual(GEO.dist_point_polygon(p, box), half - 1e-6,
                                        "track %s-%s is %.4f mm from the keepout "
                                        "rim, half-width is %.4f"
                                        % (a, b, GEO.dist_point_polygon(p, box), half))

class TestAPadInsideAKeepoutIsRefused(RouteBase):
    """A pad the router cannot legally leave is said out loud, not routed anyway."""

    def setUp(self):
        RouteBase.setUp(self)
        self.apply(self.place("Pad", [("1", 0, 0, 1.0, 1.0)], ref="J1", x=10.0, y=20.0,
                              nets={"1": "IN"}),
                   self.place("Pad", [("1", 0, 0, 1.0, 1.0)], ref="J2", x=40.0, y=20.0,
                              nets={"1": "IN"}),
                   {"op": "add_keepout", "x0": 38.0, "y0": 18.0, "x1": 42.0,
                    "y1": 22.0, "layers": ["F.Cu", "B.Cu"],
                    "rules": ["tracks", "vias"]})

    def test_the_failure_names_the_keepout(self):
        res = self.route(nets=["IN"])
        self.assertEqual(len(res["failed"]), 1, res["failed"])
        why = res["failed"][0]["reason"]
        self.assertEqual(why["code"], "no_terminal")
        detail = why["detail"][0]
        self.assertEqual(detail["pad"], "J2.1")
        self.assertEqual(detail["why"]["reason"], "keepout")
        self.assertEqual(res["ops"], [])

class TestCopperKeepsOffTheBoardEdge(RouteBase):
    """Copper-to-edge is a rule about the BOARD, not about the neighbour's net.

    The router enforced it for vias only. Measured on the F401 core board: six
    LED1_A/LED2_A tracks came out 0.275 mm from Edge.Cuts and kicad-cli DRC
    called every one of them `copper_edge_clearance`. The wall below leaves a
    1 mm gap at the board edge as the ONLY way across, so the two arms are the
    claim and its own anti-false-green control:

    * with the rule in force the gap is not a gap (the copper would be 0.375 mm
      from the edge where 0.5 mm is required) and the net is reported failed;
    * loosen the rule and the SAME geometry routes - which proves the refusal
      above is the edge rule and not "this board cannot be routed at all".
    """

    # A 20 x 10 mm board, so that the only way across is a 0.8 mm slot between
    # the wall and Edge.Cuts. Big enough to be a real search, small enough that
    # the arm which HAS to detour 5 mm sideways is still inside the budget.
    def setUp(self):
        RouteBase.setUp(self)
        E.create_board(self.board, dict(SPEC, width_mm=20, height_mm=10,
                                        overwrite=True))
        self.apply(self.place("Pad", [("1", 0, 0, 1.0, 1.0)], ref="J1", x=4.0, y=5.0,
                              nets={"1": "SIG"}),
                   self.place("Pad", [("1", 0, 0, 1.0, 1.0)], ref="J2", x=16.0, y=5.0,
                              nets={"1": "SIG"}),
                   {"op": "add_keepout", "x0": 9.0, "y0": 0.5, "x1": 11.0,
                    "y1": 9.2, "layers": ["F.Cu", "B.Cu"],
                    "rules": ["tracks", "vias"]})

    def _edge_gap(self, ops, ring):
        """How far the copper of every emitted segment stays from Edge.Cuts."""
        worst = None
        for op in ops:
            if op["op"] == "add_via":
                d = GEO.dist_point_polygon((op["x"], op["y"]), ring) - 0.3
            else:
                a = (op["x1"], op["y1"])
                b = (op["x2"], op["y2"])
                n = len(ring)
                d = min(GEO.dist_segment_segment(a, b, ring[k], ring[(k + 1) % n])
                        for k in range(n)) - op["width"] / 2.0
            if worst is None or d < worst:
                worst = d
        return worst

    def test_the_gap_at_the_edge_is_not_a_gap(self):
        board = self.load()
        bundle = R.resolve(board)
        self.assertAlmostEqual(bundle["edge_clearance_mm"],
                               R.DEFAULT_EDGE_CLEARANCE_MM, places=6)
        self.assertEqual(bundle["sources"]["edge_clearance"],
                         "assumed: KiCad board-setup default")
        self.assertTrue([a for a in bundle["assumed"]
                         if a["fact"] == "edge_clearance"])
        res = self.route(nets=["SIG"])
        self.assertTrue(res["failed"],
                        "a 1 mm gap at the board edge is not a legal passage for "
                        "a 0.5 mm copper-to-edge rule, but the router used it")

    def test_the_rule_is_what_stopped_it(self):
        res = self.route(nets=["SIG"], edge_clearance_mm=0.1)
        self.assertEqual(res["failed"], [], res["failed"])
        ring = [tuple(p) for p in self.load().outline[0]]
        got = self._edge_gap(res["ops"], ring)
        self.assertGreaterEqual(got, 0.1 - 1e-6,
                                "copper came out %.4f mm from Edge.Cuts while the "
                                "rule asked for 0.1 mm" % got)

if __name__ == "__main__":
    unittest.main()
