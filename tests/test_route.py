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

class TestCreepageIsNeverInvented(RouteBase):
    """Creepage is a standard lookup, not a board-file property.

    The rule this class defends: the router either applies the number the caller
    measured off the standard, or applies none and says so. It never translates
    "0.2 mm of electrical clearance" into "0.2 mm of creepage", because those are
    two different rules that happen to share a ruler.
    """

    def test_no_creepage_requested_means_no_creepage_applied(self):
        bundle = R.resolve(self.load())
        self.assertIsNone(bundle["creepage_mm"])
        self.assertEqual(bundle["creepage_pairs"], [])
        self.assertIn("never invented", bundle["sources"]["creepage"])
        self.assertEqual([a["fact"] for a in bundle["assumed"]], ["edge_clearance"])
        self.assertAlmostEqual(R.creepage(bundle, "A", "B"), 0.0, places=9)
        self.assertAlmostEqual(R.pair_spacing(bundle, "A", "B"), 0.2, places=6)

    def test_the_stricter_of_clearance_and_creepage_wins(self):
        bundle = R.resolve(self.load(), clearance_mm=0.2, creepage_mm=1.0,
                           creepage_pairs=[{"a": "A", "b": "B", "mm": 2.0}])
        self.assertAlmostEqual(bundle["creepage_mm"], 1.0, places=6)
        self.assertEqual(bundle["sources"]["creepage"], "caller")
        self.assertAlmostEqual(R.pair_spacing(bundle, "A", "B"), 2.0, places=6)
        self.assertAlmostEqual(R.pair_spacing(bundle, "B", "A"), 2.0, places=6)
        self.assertAlmostEqual(R.pair_spacing(bundle, "A", "C"), 1.0, places=6)
        # a pair rule never narrows a board-wide one, and vice versa
        self.assertAlmostEqual(R.creepage(bundle, "C", "D"), 1.0, places=6)

    def test_a_creepage_that_is_not_a_distance_is_refused(self):
        for val in (0, -0.5, "wide"):
            with self.assertRaises(R.RulesError) as ctx:
                R.resolve(self.load(), creepage_mm=val)
            self.assertEqual(ctx.exception.reason_code, "RULES_INVALID")
        with self.assertRaises(R.RulesError) as ctx:
            R.resolve(self.load(), creepage_pairs=[{"a": "A", "b": "A",
                                                    "mm": 1.0}])
        self.assertEqual(ctx.exception.reason_code, "RULES_INVALID")

    def test_the_tool_refuses_a_pair_string_it_cannot_parse(self):
        for bad in ("A:B", "A:B:narrow", "A:A:1.0"):
            out = self.call(nets=["SIG"], creepage_pairs=bad)
            self.assertEqual(out["status"], "error", bad)
            self.assertEqual(out["reason_code"], "RULES_INVALID", bad)

class TestCreepageReachesBothEnds(RouteBase):
    """A creepage rule is only real if it stops the search AND the judge.

    Only the raster and the search plans inside the band the exact check then
    rejects ("the raster is not the judge"); only the exact check and the answer
    depends on a 0.05 mm cell being drawn where nobody measured.
    """

    def _board_with_a_pad(self):
        board = self.load()
        board.footprints.append({"ref": "U1", "pads": [], "graphics": []})
        return board

    def _pad(self):
        # 1.0 mm square of net A; its copper edge is at x = 30.5, y = 20.0
        return {"kind": "pad", "net": "A", "layers": ["F.Cu"], "ref": "U1.1",
                "poly": [(30.0, 20.5), (30.5, 20.5), (30.5, 19.5), (30.0, 19.5)]}

    def test_the_band_widens_with_creepage(self):
        board = self._board_with_a_pad()
        tight = GRID.Grid(board, ["F.Cu"], 0.05, R.resolve(board))
        tight.add_copper(self._pad())
        near = tight.node(31.1, 20.0)          # 0.6 mm from the pad's copper
        self.assertTrue(tight.free(near[0], near[1], "F.Cu", "B"))

        wide = R.resolve(board, creepage_mm=1.0)
        grid = GRID.Grid(board, ["F.Cu"], 0.05, wide)
        grid.add_copper(self._pad())
        self.assertFalse(grid.free(near[0], near[1], "F.Cu", "B"),
                         "1.0 mm of creepage was asked for and a cell 0.6 mm from "
                         "the copper was still free to the search")
        far = grid.node(31.8, 20.0)            # 1.3 mm out: outside every margin
        self.assertTrue(grid.free(far[0], far[1], "F.Cu", "B"))
        self.assertTrue([n for n in grid.notes if "creepage" in n], grid.notes)

    def test_the_exact_check_measures_creepage_and_names_the_number(self):
        board = self._board_with_a_pad()
        grid = GRID.Grid(board, ["F.Cu"], 0.1,
                         R.resolve(board, creepage_mm=1.0))
        grid.add_copper(self._pad())
        # A 0.25 mm track whose copper comes 0.475 mm from the pad: legal at
        # 0.2 mm of clearance, illegal at 1.0 mm of creepage.
        hits = grid.seg_blockers((30.5, 21.1), (33.0, 21.1), "F.Cu", "B")
        self.assertTrue(hits, "a 0.475 mm gap passed a 1.0 mm creepage rule")
        self.assertAlmostEqual(hits[0]["needed_mm"], 1.0, places=4)
        self.assertAlmostEqual(hits[0]["actual_mm"], 0.475, places=3)

    def test_the_router_keeps_the_distance_it_was_given(self):
        self.apply(self.place("Pad", [("1", 0, 0, 1.0, 1.0)], ref="J1", x=10.0,
                              y=8.0, nets={"1": "SIG"}),
                   self.place("Pad", [("1", 0, 0, 1.0, 1.0)], ref="J2", x=40.0,
                              y=8.0, nets={"1": "SIG"}),
                   self.place("Wall", [("1", 0, 0, 4.0, 6.0)], ref="U2", x=25.0,
                              y=8.0, nets={"1": "OTHER"}))
        wall = [(23.0, 5.0), (27.0, 5.0), (27.0, 11.0), (23.0, 11.0)]
        n = len(wall)

        def gap(res):
            """Copper edge of the F.Cu tracks to the wall's AREA (0 if it crosses).

            `dist_point_polygon` measures to the boundary only, so a track straight
            through a pad reads as 1.7 mm from it - the measurement has to say
            "inside" explicitly or it congratulates a short.
            """
            worst = None
            for op in res["ops"]:
                if op["op"] != "add_track" or op["layer"] != "F.Cu":
                    continue
                a = (op["x1"], op["y1"])
                b = (op["x2"], op["y2"])
                d = min(GEO.dist_segment_segment(a, b, wall[k], wall[(k + 1) % n])
                        for k in range(n))
                if GEO.point_in_polygon(a, wall) or GEO.point_in_polygon(b, wall):
                    d = 0.0
                d -= op["width"] / 2.0
                if worst is None or d < worst:
                    worst = d
            return worst

        # One copper layer, or the router legally drops to B.Cu underneath an SMD
        # pad and a 2-D measurement calls that a short.
        tight = self.route(nets=["SIG"], clearance_mm=0.2, layers=["F.Cu"])
        self.assertEqual(tight["failed"], [], tight["failed"])
        self.assertLess(gap(tight), 1.0,
                        "the control arm already keeps 1 mm, so it cannot show "
                        "what creepage changed")

        wide = self.route(nets=["SIG"], clearance_mm=0.2, creepage_mm=1.0,
                          layers=["F.Cu"])
        self.assertEqual(wide["failed"], [], wide["failed"])
        got = gap(wide)
        self.assertGreaterEqual(got, 1.0 - 1e-6,
                                "copper came within %.4f mm while creepage asked "
                                "for 1.0 mm" % got)
        self.assertEqual(wide["rules"]["creepage_mm"], 1.0)

class TestLengthMatchingChangesTheCopper(RouteBase):
    """Equal length is a property of the FINISHED path, so it is a post-pass.

    The claim under test is the shape of the answer, not the shape of the meander:
    a group that can be matched comes back `matched` with the skew that was
    measured on the copper that now exists, and a group that cannot comes back
    `length_not_matched` carrying the length it did reach - never `matched`
    because the router tried hard.
    """

    def setUp(self):
        RouteBase.setUp(self)
        self.apply(self.place("Pad", [("1", 0, 0, 0.5, 0.5)], ref="J1", x=10.0,
                              y=8.0, nets={"1": "L1"}),
                   self.place("Pad", [("1", 0, 0, 0.5, 0.5)], ref="J2", x=40.0,
                              y=8.0, nets={"1": "L1"}),
                   self.place("Pad", [("1", 0, 0, 0.5, 0.5)], ref="J3", x=10.0,
                              y=20.0, nets={"1": "L2"}),
                   self.place("Pad", [("1", 0, 0, 0.5, 0.5)], ref="J4", x=44.0,
                              y=20.0, nets={"1": "L2"}))

    def _lengths(self, ops):
        out = {}
        for op in ops:
            if op["op"] != "add_track":
                continue
            out[op["net"]] = out.get(op["net"], 0.0) + math.hypot(
                op["x2"] - op["x1"], op["y2"] - op["y1"])
        return out

    def test_a_group_that_fits_comes_back_matched(self):
        res = self.route(nets=["L1", "L2"], layers=["F.Cu"],
                         length_groups=[{"name": "clk", "nets": ["L1", "L2"],
                                         "max_skew_mm": 0.5}])
        self.assertEqual(res["failed"], [], res["failed"])
        grp = res["length_groups"][0]
        self.assertEqual(grp["status"], "matched", grp)
        self.assertLessEqual(grp["skew_after_mm"], 0.5 + 1e-6)
        # measured here, on the ops that will actually be written
        got = self._lengths(res["ops"])
        self.assertAlmostEqual(got["L1"], 34.0, delta=0.5)
        self.assertAlmostEqual(got["L2"], 34.0, delta=0.5)
        self.assertGreater(got["L1"], 31.0,
                           "the shorter net was not lengthened at all")

    def test_a_group_that_does_not_fit_says_so_and_gives_the_real_number(self):
        self.apply(self.place("Pad", [("1", 0, 0, 0.5, 0.5)], ref="J5", x=10.0,
                              y=30.0, nets={"1": "L3"}),
                   self.place("Pad", [("1", 0, 0, 0.5, 0.5)], ref="J6", x=13.0,
                              y=30.0, nets={"1": "L3"}))
        res = self.route(nets=["L2", "L3"], layers=["F.Cu"],
                         length_groups=[{"name": "stub", "nets": ["L2", "L3"],
                                         "max_skew_mm": 0.5}])
        grp = res["length_groups"][0]
        self.assertEqual(grp["status"], "length_not_matched", grp)
        self.assertGreater(grp["skew_after_mm"], 0.5)
        # It lengthened what it could (a 3 mm stub holds about 1 mm of meander)
        # and still reports the shortfall: "not matched" is not "not attempted".
        self.assertGreater(grp["after"]["L3"], 3.0)
        self.assertLess(grp["after"]["L3"], 5.0)

    def test_the_tool_carries_the_group_through(self):
        out = self.call(nets="L1,L2", layers="F.Cu",
                        length_groups=[{"nets": ["L1", "L2"], "max_skew_mm": 0.5}])
        self.assertEqual(out["status"], "ok")
        self.assertEqual(out["value"]["nets_routed"], 2)
        grp = out["length_groups"][0]
        self.assertEqual(grp["status"], "matched")

class TestDiffPairsEnterTheSearch(RouteBase):
    """A declared pair is a distance rule and a width, not a drawing.

    The router does not couple the two nets; it routes them so they cannot come
    closer than the declared gap, gives both the declared width, and then reports
    the gap and the skew that came out. The report says `coupled: false`, because
    "we kept them 0.15 mm apart" and "they run side by side at 0.15 mm" are
    different claims and only the first one is true here.
    """

    def test_the_declared_gap_is_the_distance_the_judge_uses(self):
        board = self.load()
        pair = {"p": "D+", "n": "D-", "gap_mm": 0.5, "width_mm": 0.2}
        bundle = R.resolve(board, diff_pairs=[pair])
        self.assertAlmostEqual(R.diff_gap(bundle, "D+", "D-"), 0.5)
        self.assertAlmostEqual(R.diff_gap(bundle, "D-", "D+"), 0.5)
        self.assertAlmostEqual(R.pair_spacing(bundle, "D+", "D-"), 0.5)
        # both members carry the declared width, the rest of the board does not
        self.assertAlmostEqual(R.for_net(bundle, "D+")["track_mm"], 0.2)
        self.assertAlmostEqual(R.for_net(bundle, "D-")["track_mm"], 0.2)
        self.assertAlmostEqual(R.for_net(bundle, "OTHER")["track_mm"], 0.25)

        board.footprints.append({"ref": "U1", "pads": [], "graphics": []})
        grid = GRID.Grid(board, ["F.Cu"], 0.05, R.resolve(board))
        grid.add_track("D+", "F.Cu", (30.0, 20.0), (30.0, 24.0), 0.2)
        near = (30.5, 22.0)                 # 0.5 mm centre-to-centre, 0.3 gap
        self.assertEqual(grid.seg_blockers((30.5, 21.0), (30.5, 23.0), "F.Cu",
                                           "D-", 0.2), [],
                         "0.3 mm of gap is legal at the net-class clearance of 0.2")
        wide = GRID.Grid(board, ["F.Cu"], 0.05, bundle)
        wide.add_track("D+", "F.Cu", (30.0, 20.0), (30.0, 24.0), 0.2)
        hits = wide.seg_blockers((30.5, 21.0), (30.5, 23.0), "F.Cu", "D-", 0.2)
        self.assertTrue(hits, "a 0.3 mm gap passed a declared 0.5 mm pair gap")
        self.assertAlmostEqual(hits[0]["needed_mm"], 0.5, places=4)

    def test_a_pair_that_did_not_run_side_by_side_is_reported_as_such(self):
        self.apply(self.place("Pad", [("1", 0, 0, 0.5, 0.5)], ref="J1", x=10.0,
                              y=8.0, nets={"1": "D+"}),
                   self.place("Pad", [("1", 0, 0, 0.5, 0.5)], ref="J2", x=40.0,
                              y=8.0, nets={"1": "D+"}),
                   self.place("Pad", [("1", 0, 0, 0.5, 0.5)], ref="J3", x=10.0,
                              y=20.0, nets={"1": "D-"}),
                   self.place("Pad", [("1", 0, 0, 0.5, 0.5)], ref="J4", x=40.0,
                              y=20.0, nets={"1": "D-"}))
        res = self.route(nets=["D+", "D-"], layers=["F.Cu"],
                         diff_pairs=[{"p": "D+", "n": "D-", "gap_mm": 0.5,
                                      "width_mm": 0.2}])
        self.assertEqual(res["failed"], [], res["failed"])
        rep = res["diff_pairs"][0]
        self.assertFalse(rep["coupled"])
        self.assertAlmostEqual(rep["gap_declared_mm"], 0.5)
        self.assertGreater(rep["gap_achieved_mm"], 0.5 + 0.02)
        self.assertEqual(rep["status"], "pair_not_coupled")
        self.assertIn("does not couple", rep["why"]["note"])
        # both nets really carry the declared width
        self.assertTrue(all(op["width"] == 0.2 for op in res["ops"]
                            if op["op"] == "add_track"))

    def test_a_skew_budget_on_a_pair_becomes_a_length_group(self):
        self.apply(self.place("Pad", [("1", 0, 0, 0.5, 0.5)], ref="J1", x=10.0,
                              y=8.0, nets={"1": "D+"}),
                   self.place("Pad", [("1", 0, 0, 0.5, 0.5)], ref="J2", x=40.0,
                              y=8.0, nets={"1": "D+"}),
                   self.place("Pad", [("1", 0, 0, 0.5, 0.5)], ref="J3", x=10.0,
                              y=12.0, nets={"1": "D-"}),
                   self.place("Pad", [("1", 0, 0, 0.5, 0.5)], ref="J4", x=44.0,
                              y=12.0, nets={"1": "D-"}))
        res = self.route(nets=["D+", "D-"], layers=["F.Cu"],
                         diff_pairs=[{"p": "D+", "n": "D-", "gap_mm": 0.3,
                                      "width_mm": 0.2, "max_skew_mm": 0.5}])
        self.assertEqual(res["failed"], [], res["failed"])
        self.assertEqual(len(res["length_groups"]), 1)
        grp = res["length_groups"][0]
        self.assertEqual(sorted(grp["nets"]), ["D+", "D-"])
        self.assertEqual(grp["status"], "matched", grp)
        self.assertLessEqual(grp["skew_after_mm"], 0.5 + 1e-6)
        self.assertLessEqual(res["diff_pairs"][0]["skew_mm"], 0.5 + 1e-6)

    def test_bad_structures_are_refused_not_dropped(self):
        out = self.call(nets="D+", diff_pairs=[{"p": "D+", "n": "D-"}])
        self.assertEqual(out["status"], "error")
        self.assertEqual(out["reason_code"], "RULES_INVALID")
        out = self.call(nets="D+", diff_pairs=[{"p": "D+", "n": "D+"}])
        self.assertEqual(out["status"], "error")
        self.assertEqual(out["reason_code"], "RULES_INVALID")
        out = self.call(nets="D+", length_groups="not json")
        self.assertEqual(out["status"], "error")
        self.assertEqual(out["reason_code"], "ARG_INVALID")

if __name__ == "__main__":
    unittest.main()
