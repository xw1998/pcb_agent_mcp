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
import json
import math
import os
import shutil
import tempfile
import unittest
from types import SimpleNamespace

from pcb_agent_mcp import edit as E
from pcb_agent_mcp import geometry as GEO
from pcb_agent_mcp import tools
from pcb_agent_mcp.calc import impedance as Z
from pcb_agent_mcp.edit import ops as OPS
from pcb_agent_mcp.edit import plan as P
from pcb_agent_mcp.route import grid as GRID
from pcb_agent_mcp.route import intent as INTENT
from pcb_agent_mcp.route import rules as R
from pcb_agent_mcp.route import router
from pcb_agent_mcp.route import topology as TOPO
from pcb_agent_mcp.route import tune as TUNE
from pcb_agent_mcp.route import tune_board as TB

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
        """Ask the router, not the retired tool.

        0.0.107 removed the TOOL `board_route` (autorouting is not part of this
        server any more) but not the router: `tools._board_route` is still the
        function that reads the board, resolves the rules, plans, and - with
        `apply` - writes through board_propose/board_apply. Every claim below is
        about THAT behaviour, so it is asked of the function directly. The
        retirement itself is pinned once, in
        `TestTheToolIsRetiredNotTheRouter`, instead of being re-asserted in
        eighteen cases that used to describe a reachable tool.
        """
        args.setdefault("path", self.board)
        return tools._board_route(args)

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

class TestTheLatticeCanBeAnchoredOnThePads(RouteBase):
    """A pitch the pads do not share is a 1 um rejection waiting to happen."""

    def test_the_origin_sets_the_phase_and_not_the_extent(self):
        board = self.load()
        bundle = R.resolve(board)
        plain = GRID.Grid(board, ["F.Cu"], 0.05, bundle)
        moved = GRID.Grid(board, ["F.Cu"], 0.05, bundle, origin=[18.0749, 24.1001])
        # Same extent: the origin moves the lattice, it does not crop the board.
        self.assertAlmostEqual(plain.x1, moved.x1, places=6)
        self.assertAlmostEqual(plain.y1, moved.y1, places=6)
        ox = (moved.x0 - 18.0749) % 0.05
        oy = (moved.y0 - 24.1001) % 0.05
        self.assertLess(min(ox, 0.05 - ox), 1e-6)
        self.assertLess(min(oy, 0.05 - oy), 1e-6)
        # And a pad centre is on the lattice to the last digit.
        pt = moved.xy(*moved.node(18.0749, 24.1001))
        self.assertAlmostEqual(pt[0], 18.0749, places=6)
        self.assertAlmostEqual(pt[1], 24.1001, places=6)

    def test_an_unaligned_lattice_misses_the_pad_centre(self):
        board = self.load()
        bundle = R.resolve(board)
        plain = GRID.Grid(board, ["F.Cu"], 0.05, bundle)
        pt = plain.xy(*plain.node(18.0749, 24.1001))
        self.assertLess(abs(pt[0] - 18.0749), 0.05)
        self.assertGreater(abs(pt[0] - 18.0749), 1e-4)

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

class TestAnEdgePadIsNotAWall(RouteBase):
    """A pad centred ON Edge.Cuts must still be routable.

    The rule this class defends, and its two edges:

    * a castellation pad (an edge connector, cut in half by the outline) is a
      process option, not a clearance violation - `ruleset.measure._castellated`
      already says so, and the precedent it names is a shipped Adafruit Feather.
      Its net therefore has to leave the pad, which means the pad's OWN copper
      cannot be inside the edge band;
    * the exemption is the pad's copper and nothing else. Copper that merely
      runs near the edge is still refused (`TestCopperKeepsOffTheBoardEdge`),
      and the reverse control below widens the rule past the pad's inward reach
      and demands the same board fail - so a passing test cannot be explained by
      the band having been switched off.

    Measured before the fix: every one of the five unrouted J1 nets on RK3506
    stopped with `search_budget`, and the first stub it wanted to emit was
    rejected `too_close_to_edge, short_by 0.35` with `from` = (45.0, 9.0) - the
    Edge.Cuts line itself, i.e. the pad's centre.
    """

    # 1.0 mm pad on a 60 x 40 board: half of it reaches 0.5 mm inward, so a
    # 0.3 mm rule (0.3 + 0.125 of half-track = 0.425) is satisfiable the moment
    # the track gets out of the pad, and a 0.6 mm rule is not.
    PAD_MM = 1.0

    def setUp(self):
        RouteBase.setUp(self)
        self.apply(
            self.place("EdgePad", [("1", 0, 0, self.PAD_MM, self.PAD_MM)],
                       ref="J1", x=0.0, y=20.0, nets={"1": "SIG"}),
            self.place("Pad", [("1", 0, 0, 1.0, 1.0)], ref="J2", x=30.0,
                       y=20.0, nets={"1": "SIG"}))

    def test_the_net_of_an_edge_pad_leaves_the_pad(self):
        res = self.route(nets=["SIG"], edge_clearance_mm=0.3)
        self.assertEqual(res["failed"], [], res["failed"])
        self.assertTrue(res["ops"], "no copper emitted")
        starts = [(op["x1"], op["y1"]) for op in res["ops"]
                  if op["op"] == "add_track"]
        self.assertTrue(any(abs(x) < 0.05 for x, _y in starts),
                        "no track starts on the edge pad: %s" % (starts,))

    def test_the_copper_outside_the_pad_still_keeps_the_edge_rule(self):
        res = self.route(nets=["SIG"], edge_clearance_mm=0.3)
        self.assertEqual(res["failed"], [], res["failed"])
        half_pad = self.PAD_MM / 2.0
        worst = None
        for op in res["ops"]:
            if op["op"] != "add_track":
                continue
            a = (op["x1"], op["y1"])
            b = (op["x2"], op["y2"])
            if max(a[0], b[0]) <= half_pad:
                continue        # this whole segment is the pad's copper
            # the pad's half of the stub is the pad's copper: measure the rest
            lo = 0.0
            if min(a[0], b[0]) < half_pad and a[0] != b[0]:
                lo = (half_pad - a[0]) / (b[0] - a[0])
                lo = min(1.0, max(0.0, lo))
            p = (a[0] + (b[0] - a[0]) * lo, a[1] + (b[1] - a[1]) * lo)
            d = min(GEO.dist_segment_segment(p, b, ring_a, ring_b)
                    for ring_a, ring_b in self._left_edge()) - op["width"] / 2.0
            if worst is None or d < worst:
                worst = d
        self.assertIsNotNone(worst)
        self.assertGreaterEqual(worst, 0.3 - 1e-6,
                                "copper came out %.4f mm from Edge.Cuts while "
                                "the rule asked for 0.3 mm" % worst)

    def test_the_exemption_ends_where_the_pad_does(self):
        # 0.6 mm is wider than the pad reaches inward, so the copper outside the
        # pad cannot keep the rule and the net MUST fail. Without this arm the
        # passing test above would also pass on a router that simply turned the
        # edge band off.
        res = self.route(nets=["SIG"], edge_clearance_mm=0.6)
        self.assertTrue(res["failed"], res["ops"])

    def _left_edge(self):
        ring = [tuple(p) for p in self.load().outline[0]]
        segs = []
        for k in range(len(ring)):
            a, b = ring[k], ring[(k + 1) % len(ring)]
            if abs(a[0]) < 1e-9 and abs(b[0]) < 1e-9:
                segs.append((a, b))
        self.assertTrue(segs, "no left edge in the outline")
        return segs

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

class TestAnImpedanceTargetBecomesAWidth(RouteBase):
    """An impedance target is a design intent, and the search only knows widths.

    The rule this class defends: "50 ohm" is honoured by reverse-solving a width
    and using it as the net's track width, and when the stackup cannot pin the
    dielectric down the answer is an error - an impedance target that quietly
    lands on the net-class width would be a number nobody can build to.
    """

    def test_the_target_is_solved_and_the_width_is_what_the_net_gets(self):
        bundle = R.resolve(self.load(),
                           impedance_targets=[{"net": "SIG", "ohm": 50,
                                               "dielectric_mm": 0.2}])
        target = bundle["impedance_targets"][0]
        self.assertAlmostEqual(target["dielectric_mm"], 0.2, places=9)
        self.assertAlmostEqual(target["er"], 4.3, places=9)
        self.assertAlmostEqual(R.for_net(bundle, "SIG")["track_mm"],
                               target["width_mm"], places=9)
        self.assertNotAlmostEqual(R.for_net(bundle, "SIG")["track_mm"],
                                  bundle["track_mm"], places=3)
        # the width really is 50 ohm going forward, not just a bisection artifact
        back = Z.trace_impedance(target["width_mm"], 0.2)
        self.assertEqual(back["status"], "ok", back)
        self.assertAlmostEqual(back["value"], 50.0, delta=0.5)
        self.assertIn("impedance", bundle["sources"])
        self.assertIn("50 ohm", bundle["sources"]["impedance"][0])
        self.assertIn("impedance", R.for_net(bundle, "SIG")["source"])
        # er / copper were defaulted, and the answer says it defaulted them
        self.assertEqual(len(target["derived"]), 2, target["derived"])

    def test_the_stackup_is_used_when_it_pins_the_dielectric_down(self):
        board = self.load()
        bundle = R.resolve(board, impedance_targets=[{"net": "SIG", "ohm": 50}])
        target = bundle["impedance_targets"][0]
        self.assertAlmostEqual(target["dielectric_mm"], 1.51, places=9)
        self.assertAlmostEqual(target["er"], 4.3, places=9)
        self.assertAlmostEqual(target["copper_um"], 35.0, places=9)
        self.assertTrue(any("stackup layer" in d for d in target["derived"]),
                        target["derived"])

    def test_a_stackup_that_cannot_pin_it_down_is_an_error_not_a_guess(self):
        board = self.load()
        board.stackup = []
        for kwargs in ({"impedance_targets": [{"net": "SIG", "ohm": 50}]},
                       {"impedance_targets": [{"net": "SIG", "ohm": 50}],
                        "track_mm": 0.25}):
            with self.assertRaises(R.RulesError) as ctx:
                R.resolve(board, **kwargs)
            self.assertEqual(ctx.exception.reason_code,
                             "IMPEDANCE_NEEDS_STACKUP")
            self.assertIn("dielectric_mm", ctx.exception.hint)
        # two dielectric thicknesses are just as ambiguous as none
        board.stackup = [
            {"name": "core", "type": "core", "thickness_mm": 0.2, "er": 4.3,
             "tand": None, "copper_um": None},
            {"name": "prepreg", "type": "prepreg", "thickness_mm": 0.1,
             "er": 4.1, "tand": None, "copper_um": None}]
        with self.assertRaises(R.RulesError) as ctx:
            R.resolve(board, impedance_targets=[{"net": "SIG", "ohm": 50}])
        self.assertEqual(ctx.exception.reason_code, "IMPEDANCE_NEEDS_STACKUP")

    def test_an_unreachable_target_is_refused_with_the_reachable_range(self):
        with self.assertRaises(R.RulesError) as ctx:
            R.resolve(self.load(), impedance_targets=[{"net": "SIG", "ohm": 190,
                                                       "dielectric_mm": 1.51}])
        self.assertEqual(ctx.exception.reason_code, "IMPEDANCE_UNREACHABLE")
        self.assertIn("ohm", ctx.exception.hint)

    def test_a_pair_target_gives_both_nets_the_same_width(self):
        bundle = R.resolve(self.load(),
                           impedance_targets=[{"p": "D+", "n": "D-", "ohm": 90,
                                               "dielectric_mm": 0.2}])
        w = bundle["impedance_targets"][0]["width_mm"]
        self.assertAlmostEqual(R.for_net(bundle, "D+")["track_mm"], w, places=9)
        self.assertAlmostEqual(R.for_net(bundle, "D-")["track_mm"], w, places=9)
        self.assertAlmostEqual(bundle["track_mm"], 0.25, places=6)

    def test_two_widths_for_one_net_is_a_contradiction(self):
        bundle = R.resolve(self.load(),
                           impedance_targets=[{"p": "D+", "n": "D-", "ohm": 90,
                                               "dielectric_mm": 0.2}])
        w = round(bundle["impedance_targets"][0]["width_mm"], 4)
        # the same number in the diff-pairs entry is consistent, not a conflict
        R.resolve(self.load(),
                  impedance_targets=[{"p": "D+", "n": "D-", "ohm": 90,
                                      "dielectric_mm": 0.2}],
                  diff_pairs=[{"p": "D+", "n": "D-", "gap_mm": 0.2,
                               "width_mm": w}])
        with self.assertRaises(R.RulesError) as ctx:
            R.resolve(self.load(),
                      impedance_targets=[{"p": "D+", "n": "D-", "ohm": 90,
                                          "dielectric_mm": 0.2}],
                      diff_pairs=[{"p": "D+", "n": "D-", "gap_mm": 0.2,
                                   "width_mm": 0.5}])
        self.assertEqual(ctx.exception.reason_code, "RULES_INVALID")
        self.assertIn("contradiction", ctx.exception.message)

    def test_the_tool_carries_the_target_and_keeps_the_width(self):
        self.apply(self.place("Pad", [("1", 0, 0, 1.0, 1.0)], ref="J1", x=10.0,
                              y=8.0, nets={"1": "SIG"}),
                   self.place("Pad", [("1", 0, 0, 1.0, 1.0)], ref="J2", x=40.0,
                              y=8.0, nets={"1": "SIG"}))
        out = self.call(nets=["SIG"],
                        impedance_targets=[{"net": "SIG", "ohm": 50,
                                            "dielectric_mm": 0.2}])
        self.assertEqual(out["status"], "ok", out)
        target = out["impedance_targets"][0]
        widths = {op["width"] for op in out["ops_preview"]
                  if op["op"] == "add_track"}
        self.assertTrue(widths)
        for w in widths:
            self.assertAlmostEqual(w, target["width_mm"], places=4)
        self.assertIn("impedance",
                      out["assumptions"]["where_the_numbers_came_from"])

class TestCreepageCanBeLookedUpNotJustGiven(RouteBase):
    """The router can do the standard lookup itself - and refuses where the table does.

    The number still never comes from the router's imagination: it comes from
    `calc/iec60664.py`, whose table was recovered from a published transcription
    and is documented as exactly that. What this class pins down is that the
    lookup is reachable from the call, that the answer says which ladder it used,
    and that an input past the end of a ladder is an error rather than a
    straight-line extrapolation.
    """

    def test_the_working_voltage_produces_the_table_value(self):
        bundle = R.resolve(self.load(), working_voltage_v=250)
        self.assertAlmostEqual(bundle["creepage_mm"], 2.5, places=6)
        self.assertIn("IEC 60664-1 Table F.5", bundle["sources"]["creepage"])
        self.assertAlmostEqual(R.creepage(bundle, "A", "B"), 2.5, places=6)
        lookup = bundle["creepage_lookup"]
        self.assertEqual(lookup["status"], "ok")
        self.assertEqual(lookup["ladder"], "PD2|MG_IIIa|pcb0")
        self.assertEqual(lookup["unit"], "mm")

    def test_the_lookup_parameters_change_the_ladder(self):
        one = R.resolve(self.load(), working_voltage_v=250, pollution_degree=1,
                        material_group="I")
        two = R.resolve(self.load(), working_voltage_v=250, pollution_degree=3,
                        material_group="I")
        self.assertEqual(one["creepage_lookup"]["ladder"], "PD1|MG_I|pcb0")
        self.assertEqual(two["creepage_lookup"]["ladder"], "PD3|MG_I|pcb0")
        self.assertGreater(two["creepage_mm"], one["creepage_mm"])
        rein = R.resolve(self.load(), working_voltage_v=250, insulation="reinforced")
        self.assertAlmostEqual(rein["creepage_mm"], 5.0, places=6)

    def test_the_stricter_of_the_two_numbers_wins_and_says_which(self):
        wide = R.resolve(self.load(), working_voltage_v=250, creepage_mm=6.0)
        self.assertAlmostEqual(wide["creepage_mm"], 6.0, places=6)
        self.assertIn("stricter", wide["sources"]["creepage"])
        self.assertIn("IEC 60664-1", wide["sources"].get("creepage_table", ""))
        tight = R.resolve(self.load(), working_voltage_v=250, creepage_mm=0.1)
        self.assertAlmostEqual(tight["creepage_mm"], 2.5, places=6)
        self.assertIn("IEC table value", tight["sources"]["creepage"])

    def test_an_input_the_table_does_not_cover_is_refused(self):
        with self.assertRaises(R.RulesError) as ctx:
            R.resolve(self.load(), working_voltage_v=1e9)
        self.assertEqual(ctx.exception.reason_code, "OUT_OF_VALIDITY")
        self.assertIn("ladder", ctx.exception.message + ctx.exception.hint)
        with self.assertRaises(R.RulesError) as ctx:
            R.resolve(self.load(), working_voltage_v=250, pollution_degree=4)
        self.assertEqual(ctx.exception.reason_code, "STANDARD_SCOPE")
        with self.assertRaises(R.RulesError) as ctx:
            R.resolve(self.load(), working_voltage_v=250, pollution_degree=2.5)
        self.assertEqual(ctx.exception.reason_code, "ARG_INVALID")
        with self.assertRaises(R.RulesError) as ctx:
            R.resolve(self.load(), working_voltage_v=250, material_group="IV")
        self.assertEqual(ctx.exception.reason_code, "ARG_INVALID")

    def test_the_tool_looks_it_up_and_reports_the_ladder(self):
        self.apply(self.place("Pad", [("1", 0, 0, 1.0, 1.0)], ref="J1", x=10.0,
                              y=8.0, nets={"1": "SIG"}),
                   self.place("Pad", [("1", 0, 0, 1.0, 1.0)], ref="J2", x=40.0,
                              y=8.0, nets={"1": "SIG"}))
        out = self.call(nets=["SIG"], working_voltage_v=250)
        self.assertEqual(out["status"], "ok", out)
        self.assertAlmostEqual(out["assumptions"]["creepage_mm"], 2.5, places=6)
        lookup = out["assumptions"]["creepage_lookup"]
        self.assertEqual(lookup["ladder"], "PD2|MG_IIIa|pcb0")
        self.assertIn("IEC 60664-1", out["assumptions"]
                      ["where_the_numbers_came_from"]["creepage"])
        bad = self.call(nets=["SIG"], working_voltage_v=1e9)
        self.assertEqual(bad["status"], "error")
        self.assertEqual(bad["reason_code"], "OUT_OF_VALIDITY")

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

class TestTheMeanderShapeIsTheOneAskedFor(RouteBase):
    """`style` is per group, and the shape nobody asked for is the old one.

    `route.tune_board` has drawn a rounded wave since the user rejected the
    triangle; the ROUTER's own post-pass could only draw the 45-degree tooth,
    which is exactly what came out of a routed DDR lane and got refused on sight.
    So `style="arc"` reaches the router too. Four claims, each falsifiable alone:
    the arcs are really written (a real 3-point arc, ~90 degrees, with a radius),
    the length this module reports counts them, saying nothing changes nothing,
    and a shape this code cannot draw is refused by name instead of quietly
    becoming a tooth.
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

    def _arcs(self, ops, net=None):
        return [o for o in ops if o["op"] == "add_track_arc"
                and (net is None or o["net"] == net)]

    def _copper(self, ops, net):
        """Straight + arc length of one net, measured from the ops themselves."""
        total = 0.0
        for o in ops:
            if o["net"] != net:
                continue
            if o["op"] == "add_track":
                total += math.hypot(o["x2"] - o["x1"], o["y2"] - o["y1"])
            elif o["op"] == "add_track_arc":
                total += OPS.arc_length_mm((o["x1"], o["y1"]),
                                          (o["mx"], o["my"]),
                                          (o["x2"], o["y2"]))
        return total

    def test_arc_style_writes_arcs_and_still_matches(self):
        res = self.route(nets=["L1", "L2"], layers=["F.Cu"],
                         length_groups=[{"name": "clk", "nets": ["L1", "L2"],
                                         "max_skew_mm": 0.5, "style": "arc"}])
        grp = res["length_groups"][0]
        self.assertEqual(grp["style"], "arc", grp)
        self.assertEqual(grp["status"], "matched", grp)
        arcs = self._arcs(res["ops"])
        self.assertTrue(arcs, "style=arc wrote no arc at all")
        # A real arc, not a chamfer wearing the op name: a radius exists and the
        # sweep is a quarter turn - which is what the rounded wave is made of.
        for a in arcs[:8]:
            geom = TB._arc_geom((a["x1"], a["y1"]), (a["mx"], a["my"]),
                                     (a["x2"], a["y2"]))
            self.assertIsNotNone(geom, a)
            _c, r, _a0, sweep = geom
            self.assertGreater(r, 0.0, a)
            self.assertAlmostEqual(abs(sweep), 90.0, delta=0.5)
        # A rounded wave introduces no new ANGLE: every straight piece is on the
        # segment's own axis or across it, so the lane still reads as 0/45/90/135
        # copper - the rule that got the chamfer rejected in the first place was
        # about right angles, not about curves.
        short = min(grp["before"], key=lambda n: grp["before"][n])
        for o in res["ops"]:
            if o["op"] != "add_track" or o["net"] != short:
                continue
            ang = math.degrees(math.atan2(o["y2"] - o["y1"],
                                          o["x2"] - o["x1"])) % 45.0
            self.assertTrue(min(ang, 45.0 - ang) < 1e-6, o)
        # The reported length is the copper: measured here on the ops, both nets
        # land inside the budget, and the short one was really lengthened.
        got = dict((n, self._copper(res["ops"], n)) for n in ("L1", "L2"))
        self.assertLessEqual(abs(got["L1"] - got["L2"]), 0.5 + 1e-6, got)
        short = min(grp["before"], key=lambda n: grp["before"][n])
        self.assertGreater(got[short], grp["before"][short] + 0.5, got)
        self.assertAlmostEqual(got[short], grp["after"][short], delta=2e-3)

    def test_saying_nothing_still_writes_only_straight_copper(self):
        res = self.route(nets=["L1", "L2"], layers=["F.Cu"],
                         length_groups=[{"name": "clk", "nets": ["L1", "L2"],
                                         "max_skew_mm": 0.5}])
        grp = res["length_groups"][0]
        self.assertEqual(grp["style"], "chamfer", grp)
        self.assertEqual(grp["status"], "matched", grp)
        self.assertEqual(self._arcs(res["ops"]), [],
                         "the default shape grew an arc nobody asked for")
        short = min(grp["before"], key=lambda n: grp["before"][n])
        self.assertIn(short, grp["detail"], grp["detail"])
        self.assertNotIn("arcs", grp["detail"][short])

    def test_a_shape_this_router_cannot_draw_is_refused(self):
        out = self.call(nets="L1,L2", layers="F.Cu",
                        length_groups=[{"nets": ["L1", "L2"],
                                        "max_skew_mm": 0.5,
                                        "style": "triangle"}])
        self.assertEqual(out["status"], "error", out)
        self.assertEqual(out["reason_code"], "ARG_INVALID", out)
        self.assertIn("triangle", out["message"])

    def test_the_two_styles_share_their_arithmetic(self):
        a, b, extra, amp = (0.0, 0.0), (10.0, 0.0), 2.0, 1.0
        pts = TUNE._serpentine(a, b, extra, amp, 1)
        pieces, meta = TUNE._wave(a, b, extra, amp, 1, "chamfer")
        self.assertEqual(meta["arcs"], 0)
        self.assertEqual(len(pieces), len(pts) - 1)
        for k, piece in enumerate(pieces):
            self.assertEqual(piece[0], "line")
            self.assertEqual(piece[1], pts[k])
            self.assertEqual(piece[2], pts[k + 1])
        cham = sum(math.hypot(p[2][0] - p[1][0], p[2][1] - p[1][1])
                   for p in pieces) - 10.0
        self.assertAlmostEqual(cham, extra, delta=1e-9)
        # The arc wave buys the SAME length, and the pieces say how it is made.
        apieces, ameta = TUNE._wave(a, b, extra, amp, 1, "arc", 0.1)
        arcs = [p for p in apieces if p[0] == "arc"]
        self.assertTrue(arcs)
        self.assertEqual(ameta["arcs"], len(arcs))
        self.assertGreater(ameta["radius_mm"], 0.0)
        total = 0.0
        for p in apieces:
            if p[0] == "line":
                total += math.hypot(p[2][0] - p[1][0], p[2][1] - p[1][1])
            else:
                total += OPS.arc_length_mm(p[1], p[2], p[3])
        self.assertAlmostEqual(total - 10.0, extra, delta=1e-5)
        with self.assertRaises(ValueError):
            TUNE._wave(a, b, extra, amp, 1, "circle")

class TestDiffPairsEnterTheSearch(RouteBase):
    """A declared pair is a distance rule and a width, not a drawing.

    As far as the SEARCH is concerned, a pair is a minimum distance: the two nets
    are routed so they cannot come closer than the declared gap and both carry
    the declared width. Whether they then run side by side is decided after the
    search, by `TestAPairCanBeCoupled`; here the offset walk is blocked or out of
    fanout range, so the pair keeps its independent routing and the report says
    so with the reason rather than the old blanket "this router does not couple".
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
        # 12 mm apart: joining the offset run to the pads would be a diagonal
        # fanout nobody asked for, so coupling is refused and says why.
        self.assertEqual(rep["why"]["reason"], "fanout_too_long")
        self.assertIn("do not run side by side", rep["why"]["note"])
        self.assertIn("fanout", rep["why"]["note"])
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

LAYER_MOD = ('(footprint "SYN:%(name)s" (version 20221018) (generator pcbnew)\n'
             '  (layer "F.Cu") (attr smd)\n'
             '  (fp_text reference "%(ref)s" (at 0 -6) (layer "F.SilkS")\n'
             '    (effects (font (size 0.7 0.7) (thickness 0.12))))\n'
             '  (pad "1" smd rect (at 0 0) (size %(w).4f %(w).4f)\n'
             '    (layers "%(l)s" "%(p)s" "%(m)s")\n'
             '    (tstamp 5%(n)07d-1111-1111-1111-111111111111))\n'
             ')\n')


class TestALayerChangeGetsAGroundVia(RouteBase):
    """A signal that changes layer needs a way back across, and gets one.

    The signal pad is on F.Cu and the other is on B.Cu, so the only legal route
    dives through a via. A ground via beside it is what makes the return current
    able to follow; without one the return current detours or does not go.
    """

    def _pad_on(self, ref, x, y, net, layer, w=0.6, n=0):
        if layer == "F.Cu":
            layers = ("F.Cu", "F.Paste", "F.Mask")
        else:
            layers = ("B.Cu", "B.Paste", "B.Mask")
        name = "P" + ref
        with open(os.path.join(self.lib, name + ".kicad_mod"), "w",
                  encoding="utf-8", newline="\n") as fh:
            fh.write(LAYER_MOD % {"name": name, "ref": ref, "w": w, "n": n,
                                  "l": layers[0], "p": layers[1], "m": layers[2]})
        return {"op": "add_footprint", "ref": ref, "x": x, "y": y,
                "library": self.lib, "name": name,
                "pad_nets": {"1": net}, "create_nets": True}

    def _layer_change(self, ground_layers=("F.Cu", "B.Cu")):
        ops = [self._pad_on("J1", 10.0, 10.0, "SIG", "F.Cu", n=1),
               self._pad_on("J2", 30.0, 10.0, "SIG", "B.Cu", n=2)]
        for k, layer in enumerate(ground_layers):
            # The ground corridor runs past both pads, so the signal via always
            # has ground copper on both sides of the layer change within reach.
            ops.append({"op": "add_track", "x1": 8.0, "y1": 11.6, "x2": 34.0,
                        "y2": 11.6, "width": 0.25, "layer": layer,
                        "net": "GND", "create_net": True})
        self.apply(*ops)

    def test_the_signal_via_gets_a_ground_via_on_the_copper(self):
        self._layer_change()
        res = self.route(nets=["SIG"], layers=["F.Cu", "B.Cu"])
        self.assertEqual(res["failed"], [], res["failed"])
        rp = res["return_path"]
        self.assertEqual(rp["status"], "stitched")
        self.assertEqual(rp["ground_net"], "GND")
        # One signal via, one ground via - not one per layer, not one per pad.
        self.assertEqual(rp["attempted"], 1)
        self.assertEqual(len(rp["added"]), 1)
        got = rp["added"][0]
        self.assertEqual(sorted(got["layers_with_ground"]), ["B.Cu", "F.Cu"])
        # It landed ON the ground track, not near it: a via 1 mm short of the
        # pour is a floating island, and DRC reports it as unconnected.
        self.assertAlmostEqual(got["at"][1], 11.6, places=6)
        self.assertLessEqual(got["distance_mm"], 2.0)
        # ...and it is really written, as ground copper with a role a reader can
        # tell apart from routed vias.
        vias = [op for op in res["ops"] if op["op"] == "add_via"]
        self.assertEqual(len(vias), 2)
        gnd = [op for op in vias if op["net"] == "GND"]
        self.assertEqual(len(gnd), 1)
        self.assertEqual(gnd[0]["role"], "ground_stitch")
        self.assertEqual(gnd[0]["for_net"], "SIG")

    def test_ground_on_one_layer_only_is_refused_not_faked(self):
        self._layer_change(ground_layers=("F.Cu",))
        res = self.route(nets=["SIG"], layers=["F.Cu", "B.Cu"])
        rp = res["return_path"]
        self.assertEqual(rp["status"], "refused")
        self.assertEqual(rp["added"], [])
        self.assertEqual(rp["skipped"][0]["reason"], "ground_not_on_two_layers")
        self.assertEqual(rp["skipped"][0]["layers_with_ground"], ["F.Cu"])
        # Nothing was written for the ground net at all.
        self.assertEqual([op for op in res["ops"]
                          if op["op"] == "add_via" and op["net"] == "GND"], [])

    def test_a_board_with_no_ground_net_is_refused_by_name(self):
        self._layer_change(ground_layers=())
        res = self.route(nets=["SIG"], layers=["F.Cu", "B.Cu"])
        rp = res["return_path"]
        self.assertEqual(rp["status"], "refused")
        self.assertEqual(rp["why"]["reason"], "no_ground_net")
        self.assertIsNone(rp["ground_net"])

    def test_the_pass_can_be_turned_off(self):
        self._layer_change()
        res = self.route(nets=["SIG"], layers=["F.Cu", "B.Cu"],
                         return_path=False)
        self.assertIsNone(res["return_path"])
        self.assertEqual([op for op in res["ops"]
                          if op["op"] == "add_via" and op["net"] == "GND"], [])

    def test_two_ground_nets_is_a_refusal_not_a_coin_flip(self):
        self._layer_change(ground_layers=("F.Cu", "B.Cu"))
        self.apply(self.place("Pad", [("1", 0, 0, 0.5, 0.5)], ref="J3", x=50.0,
                              y=30.0, nets={"1": "VSS"}))
        res = self.route(nets=["SIG"], layers=["F.Cu", "B.Cu"])
        rp = res["return_path"]
        self.assertEqual(rp["status"], "refused")
        self.assertEqual(rp["why"]["reason"], "ambiguous_ground_net")
        self.assertEqual(rp["why"]["candidates"], ["GND", "VSS"])

    def test_the_ground_can_be_named_when_the_board_calls_it_something_else(self):
        # AGND is a different return network, so it is not picked automatically -
        # but the caller may say "this board's ground is AGND" and get it stitched.
        self._layer_change(ground_layers=())
        for layer in ("F.Cu", "B.Cu"):
            self.apply({"op": "add_track", "x1": 8.0, "y1": 11.6, "x2": 34.0,
                        "y2": 11.6, "width": 0.25, "layer": layer,
                        "net": "AGND", "create_net": True})
        res = self.route(nets=["SIG"], layers=["F.Cu", "B.Cu"],
                         return_path_net="AGND")
        rp = res["return_path"]
        self.assertEqual(rp["status"], "stitched", rp.get("why"))
        self.assertEqual(rp["ground_net"], "AGND")


class TestAPairCanBeCoupled(RouteBase):
    """The second net really becomes the first net's path, offset by the gap.

    The pads are only slightly wider than the trace on purpose: a coupled run
    comes out beside the other member's PAD at the ends, so a pad twice the trace
    width would make the last millimetre of every coupled pair a clearance
    violation. That is a property of coupling to the pad, not a bug here.
    """

    def _four_pads(self, pad_mm=0.25, y_n=10.0):
        self.apply(self.place("Pad", [("1", 0, 0, pad_mm, pad_mm)], ref="J1",
                              x=10.0, y=8.0, nets={"1": "D+"}),
                   self.place("Pad", [("1", 0, 0, pad_mm, pad_mm)], ref="J2",
                              x=40.0, y=8.0, nets={"1": "D+"}),
                   self.place("Pad", [("1", 0, 0, pad_mm, pad_mm)], ref="J3",
                              x=10.0, y=y_n, nets={"1": "D-"}),
                   self.place("Pad", [("1", 0, 0, pad_mm, pad_mm)], ref="J4",
                              x=40.0, y=y_n, nets={"1": "D-"}))

    def test_the_second_net_becomes_the_first_net_offset_by_the_gap(self):
        self._four_pads()
        res = self.route(nets=["D+", "D-"], layers=["F.Cu"],
                         diff_pairs=[{"p": "D+", "n": "D-", "gap_mm": 0.3,
                                      "width_mm": 0.25}])
        self.assertEqual(res["failed"], [], res["failed"])
        rep = res["diff_pairs"][0]
        self.assertTrue(rep["coupled"], rep.get("why"))
        self.assertEqual(rep["status"], "pair_coupled")
        # The declared gap is what came out, not "at least the gap".
        self.assertAlmostEqual(rep["gap_achieved_mm"], 0.3, delta=0.02)
        # ...and it holds along the whole run, not just at the closest point.
        self.assertGreater(rep["coupled_fraction"], 0.8)
        # `coupled: true` must be backed by copper that really looks like a pair:
        # the second net has exactly one long run beside the first, parallel to it.
        runs = [op for op in res["ops"]
                if op["op"] == "add_track" and op["net"] == "D-"]
        self.assertEqual(len(runs), 3)     # fanout, coupled run, fanout
        long_run = max(runs, key=lambda o: math.hypot(o["x2"] - o["x1"],
                                                      o["y2"] - o["y1"]))
        self.assertAlmostEqual(long_run["y1"], 8.0 + 0.3 + 0.25, delta=0.01)
        self.assertAlmostEqual(long_run["y2"], 8.0 + 0.3 + 0.25, delta=0.01)
        # The fanouts are reported: a pair that coupled for 30 mm and needed a
        # 20 mm detour to get there is a different board.
        self.assertEqual(rep["coupling"]["segments"], 3)
        self.assertAlmostEqual(rep["coupling"]["fanout_mm"][0], 1.45, delta=0.01)

    def test_a_pair_whose_offset_is_blocked_keeps_its_own_copper(self):
        self._four_pads()
        # A third net's pad sits exactly where the offset run would go.
        self.apply(self.place("Pad", [("1", 0, 0, 0.25, 0.25)], ref="J5",
                              x=25.0, y=8.55, nets={"1": "GUARD"}))
        res = self.route(nets=["D+", "D-"], layers=["F.Cu"],
                         diff_pairs=[{"p": "D+", "n": "D-", "gap_mm": 0.3,
                                      "width_mm": 0.25}])
        self.assertEqual(res["failed"], [], res["failed"])
        rep = res["diff_pairs"][0]
        self.assertFalse(rep["coupled"])
        self.assertEqual(rep["why"]["reason"], "offset_blocked")
        # Both sides are named: "+1" is blocked by the guard, "-1" by the other
        # member's own pad (its fanout would cross it). Naming only the last one
        # would send the caller to move the wrong part.
        sides = dict((s["side"], s) for s in rep["why"]["detail"]["sides"])
        self.assertEqual(sides[1]["detail"]["reference"], "J5.1")
        self.assertEqual(sides[-1]["detail"]["reference"], "J1.1")
        # The second net's own copper is still there, byte for byte: a refused
        # coupling must not delete a routed net.
        self.assertEqual(rep["length_n_mm"], 30.0)
        runs = [op for op in res["ops"]
                if op["op"] == "add_track" and op["net"] == "D-"]
        self.assertEqual(len(runs), 1)
        self.assertAlmostEqual(runs[0]["y1"], 10.0, places=6)
        self.assertAlmostEqual(runs[0]["y2"], 10.0, places=6)

    def test_coupling_can_be_turned_off_per_pair(self):
        self._four_pads()
        res = self.route(nets=["D+", "D-"], layers=["F.Cu"],
                         diff_pairs=[{"p": "D+", "n": "D-", "gap_mm": 0.3,
                                      "width_mm": 0.25, "couple": False}])
        rep = res["diff_pairs"][0]
        self.assertFalse(rep["coupled"])
        self.assertEqual(rep["coupling"]["reason"], "not_asked")
        # Independent routing, as before: no gap claimed.
        self.assertGreater(rep["gap_achieved_mm"], 0.3 + 0.02)
        self.assertIn("couple=false", rep["why"]["note"])

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


class TestOneIntentObjectThreeConsumers(RouteBase):
    """One intent object, three consumers - and every refusal named.

    The failure this prevents is not untidiness: a board measured with the
    net-class clearance and then routed with `clearance_mm=0.2` was measured about
    a different board, and both answers were "right".
    """

    def _pair(self, net="A", x2=40.0):
        self.apply(self.place("Pad", [("1", 0, 0, 0.6, 0.6)], ref="J1", x=10.0,
                              y=8.0, nets={"1": net}),
                   self.place("Pad", [("1", 0, 0, 0.6, 0.6)], ref="J2", x=x2,
                              y=8.0, nets={"1": net}))

    def test_an_unknown_key_is_refused_rather_than_ignored(self):
        out = tools.call_tool("board_intent", {
            "path": self.board,
            "intent": {"version": 1, "constraints": {"clearance": 0.2}}})
        self.assertEqual(out["status"], "error")
        self.assertEqual(out["reason_code"], "INTENT_INVALID")
        # The typo is NAMED, and the vocabulary it should have used is spelled out:
        # "clearance" silently becoming "no constraint requested" is the failure
        # this refusal exists to prevent.
        self.assertIn("clearance", out["message"])
        self.assertIn("clearance_mm", out["hint"])

    def test_a_version_this_build_does_not_know_is_refused(self):
        out = tools.call_tool("board_intent", {"path": self.board,
                                               "intent": {"version": 99}})
        self.assertEqual(out["reason_code"], "INTENT_VERSION")

    def test_a_missing_file_is_named_not_guessed_at(self):
        out = tools.call_tool("board_intent", {
            "path": self.board, "intent": os.path.join(self.tmp, "nope.json")})
        self.assertEqual(out["reason_code"], "INTENT_FILE_MISSING")
        self.assertIn("nope.json", out["message"])

    def test_only_what_the_intent_declares_comes_out(self):
        spec = INTENT.load({"version": 1,
                            "constraints": {"clearance_mm": 0.2, "track_mm": 0.25},
                            "nets": {"route": "A, B"}})
        flat = INTENT.flatten(spec)
        self.assertEqual(sorted(flat), ["clearance_mm", "nets", "track_mm"])
        self.assertEqual(flat["nets"], ["A", "B"])
        # "not declared" must not become "zero": a via diameter nobody asked about
        # is not 0 mm.
        self.assertNotIn("via_diameter_mm", flat)

    def test_the_caller_wins_and_the_disagreement_is_named(self):
        spec = INTENT.load({"version": 1, "constraints": {"clearance_mm": 0.2}})
        kw, report = INTENT.resolve({"clearance_mm": 0.3}, spec)
        self.assertEqual(kw["clearance_mm"], 0.3)
        self.assertEqual(report["conflicts"],
                         [{"key": "clearance_mm", "intent": 0.2,
                           "caller": 0.3, "used": "caller"}])
        self.assertEqual(report["from_caller"], ["clearance_mm"])

    def test_a_keepout_over_a_pad_is_refused_before_ten_minutes_of_routing(self):
        self._pair()
        out = self.call(nets=["A"], layers=["F.Cu"], intent={
            "version": 1,
            "keepouts": [{"x0": 9.0, "y0": 7.0, "x1": 11.0, "y1": 9.0}]})
        self.assertEqual(out["status"], "error")
        self.assertEqual(out["reason_code"], "INTENT_INVALID")
        self.assertIn("J1.1", json.dumps(out["intent_problems"]))

    def test_a_footprint_outside_its_region_is_reported_not_refused(self):
        self._pair()
        out = tools.call_tool("board_intent", {"path": self.board, "intent": {
            "version": 1,
            "placement": {"regions": [{"ref": "J1",
                                       "region": [30.0, 15.0, 45.0, 25.0],
                                       "order": 2}]}}})
        self.assertEqual(out["status"], "ok", out)
        self.assertEqual(out["value"]["blocking"], 0)
        self.assertEqual(out["value"]["problems"], 1)
        self.assertIn("outside its declared region", out["findings"][0]["problem"])

    def test_a_region_off_the_board_is_refused(self):
        self._pair()
        out = tools.call_tool("board_intent", {"path": self.board, "intent": {
            "version": 1,
            "placement": {"regions": [{"ref": "J1",
                                       "region": [0.0, 0.0, 70.0, 40.0]}]}}})
        self.assertEqual(out["status"], "error")
        self.assertEqual(out["reason_code"], "INTENT_INVALID")
        self.assertIn("past the board edge", json.dumps(out["blocking_findings"]))

    def test_the_intent_that_measures_is_the_intent_that_routes(self):
        self._pair()
        out = tools.call_tool("board_routability", {
            "path": self.board,
            "intent": {"version": 1, "constraints": {"clearance_mm": 0.45}}})
        self.assertEqual(out["status"], "ok", out)
        report = out["assumptions"]["intent"]
        self.assertEqual(report["declared"], ["clearance_mm"])
        self.assertIn("board", report)
        # The measurement really used the file's number, not the net class.
        self.assertIn(0.45, [float(v) for v in _numbers(out) if v is not None])


def _numbers(obj, out=None):
    """Every number anywhere in a reply, for "was this value used" assertions."""
    if out is None:
        out = []
    if isinstance(obj, dict):
        for val in obj.values():
            _numbers(val, out)
    elif isinstance(obj, (list, tuple)):
        for val in obj:
            _numbers(val, out)
    elif isinstance(obj, (int, float)) and not isinstance(obj, bool):
        out.append(obj)
    return out


class TestAMeanderChoosesItsHost(RouteBase):
    """Which segment gets the serpentine is a decision, not a loop order.

    Length alone is the wrong key twice: a shorter segment in open copper beats the
    longest one squeezed between other nets, and a segment that ends at a via is
    the worst host of all - a serpentine there is a stub.
    """

    def _em(self, ops, crowded=False):
        class _Em(object):
            pass
        em = _Em()
        em.ops = ops
        em.grid = SimpleNamespace(
            _map={"F.Cu": [1 if crowded else 0]},
            _sets=[["GUARD"]],
            cells_near=lambda x, y, r: [0])
        return em

    def test_a_host_that_ends_at_a_via_is_discounted_not_preferred(self):
        open_seg = {"op": "add_track", "net": "L1", "layer": "F.Cu", "width": 0.25,
                    "x1": 0.0, "y1": 0.0, "x2": 20.0, "y2": 0.0}
        via_seg = {"op": "add_track", "net": "L1", "layer": "F.Cu", "width": 0.25,
                   "x1": 0.0, "y1": 5.0, "x2": 20.0, "y2": 5.0}
        via = {"op": "add_via", "net": "L1", "x": 0.0, "y": 5.0,
               "size": 0.6, "drill": 0.3}
        ranked = TUNE._candidate_order(self._em([open_seg, via_seg, via]), "L1",
                                       [via_seg, open_seg])
        self.assertIs(ranked[0][1], open_seg)
        self.assertTrue(ranked[1][2]["ends_at_a_via"])
        self.assertAlmostEqual(ranked[1][0], ranked[0][0] * 0.25, places=6)

    def test_other_nets_copper_near_the_midpoint_discounts_the_host(self):
        seg = {"op": "add_track", "net": "L1", "layer": "F.Cu", "width": 0.25,
               "x1": 0.0, "y1": 0.0, "x2": 20.0, "y2": 0.0}
        clean = TUNE._candidate_order(self._em([seg]), "L1", [seg])[0]
        busy = TUNE._candidate_order(self._em([seg], crowded=True), "L1", [seg])[0]
        self.assertAlmostEqual(busy[0], clean[0] * 0.25, places=6)
        self.assertEqual(busy[2]["other_copper_within_mm"], 2.0)
        self.assertIsNone(clean[2]["other_copper_within_mm"])

    def test_the_report_carries_the_ladder_and_the_hosts_it_passed_over(self):
        self.apply(self.place("Pad", [("1", 0, 0, 0.5, 0.5)], ref="J1", x=10.0,
                              y=8.0, nets={"1": "L1"}),
                   self.place("Pad", [("1", 0, 0, 0.5, 0.5)], ref="J2", x=40.0,
                              y=8.0, nets={"1": "L1"}),
                   self.place("Pad", [("1", 0, 0, 0.5, 0.5)], ref="J3", x=10.0,
                              y=20.0, nets={"1": "L2"}),
                   self.place("Pad", [("1", 0, 0, 0.5, 0.5)], ref="J4", x=44.0,
                              y=20.0, nets={"1": "L2"}))
        res = self.route(nets=["L1", "L2"], layers=["F.Cu"],
                         length_groups=[{"name": "clk", "nets": ["L1", "L2"],
                                         "max_skew_mm": 0.5}])
        grp = res["length_groups"][0]
        self.assertEqual(grp["status"], "matched", grp)
        got = grp["detail"]["L1"]
        ladder = got["amplitude_ladder_mm"]
        # The widest amplitude is tried first and the ladder only ever narrows: a
        # narrow meander is a fallback, never the shape that was asked for.
        self.assertGreaterEqual(len(ladder), 2, ladder)
        self.assertEqual(ladder, sorted(ladder, reverse=True))
        self.assertGreater(ladder[-1], 0.0)
        for part in got["meanders"]:
            self.assertIn(part["amplitude_mm"], ladder)
            self.assertEqual(part["narrowed"],
                             part["amplitude_mm"] != ladder[0])
        # Hosts are ranked, and the ranking is carried: `room_mm` is what the
        # segment could buy, best first.
        hosts = got["hosts_considered"]
        self.assertTrue(hosts)
        self.assertEqual([h["room_mm"] for h in hosts],
                         sorted((h["room_mm"] for h in hosts), reverse=True))


class TestNewCopperMakesThePourStale(RouteBase):
    """A filled pour in the file describes the board BEFORE this routing.

    A track crossing a pour is not a short - the pour is re-cut around it - but the
    polygons in the file are now a snapshot of another board, and a board whose
    fill is stale must not read as finished.
    """

    def _board_with_pour(self, net="A"):
        self.apply(self.place("Pad", [("1", 0, 0, 0.6, 0.6)], ref="J1", x=10.0,
                              y=8.0, nets={"1": net}),
                   self.place("Pad", [("1", 0, 0, 0.6, 0.6)], ref="J2", x=40.0,
                              y=8.0, nets={"1": net}))
        self.apply({"op": "add_zone", "layer": "F.Cu", "net": net,
                    "x0": 0.0, "y0": 0.0, "x1": 60.0, "y1": 40.0})

    def test_new_copper_in_a_pour_asks_for_a_refill(self):
        self._board_with_pour()
        res = self.route(nets=["A"], layers=["F.Cu"])
        self.assertEqual(res["failed"], [], res["failed"])
        pour = res["pour"]
        self.assertTrue(pour["needs_refill"], pour)
        self.assertEqual(pour["zones"][0]["zone_net"], "A")
        self.assertGreaterEqual(pour["zones"][0]["segments_inside"], 1)
        self.assertTrue(pour["zones"][0]["same_net_as_the_new_copper"])
        self.assertIn("refill", pour["why"])

    def test_copper_outside_the_pour_asks_for_nothing(self):
        self._board_with_pour()
        board = self.load()
        outside = {"op": "add_track", "net": "A", "layer": "F.Cu", "width": 0.25,
                   "x1": 100.0, "y1": 100.0, "x2": 110.0, "y2": 100.0}
        rep = router._pour_report(board, [outside], ["F.Cu", "B.Cu"])
        self.assertFalse(rep["needs_refill"])
        self.assertEqual(rep["zones"], [])

    def test_the_tool_carries_it(self):
        self._board_with_pour()
        out = self.call(nets="A", layers="F.Cu")
        self.assertEqual(out["status"], "ok", out)
        self.assertTrue(out["pour"]["needs_refill"])
        self.assertTrue(out["assumptions"]["pour"]["needs_refill"])

class TestARefillCanBeAskedFor(RouteBase):
    """Reporting a stale pour is not the same as fixing it - the fix exists too.

    The pour that goes stale is the one the router itself made stale, so the only
    honest thing to offer is KiCad's own filler: `refill_pours=true` runs it after
    the copper is written and transplants its polygons. The fill is faked here
    (the real engine is exercised by tests/test_zone_fill.py against pcbnew);
    what is under test is the wiring - that it runs exactly when asked, that the
    answer says whether it ran, and that "nothing went stale" is not a refill.
    """

    def _pillars(self, net="A"):
        self.apply(self.place("Pad", [("1", 0, 0, 0.6, 0.6)], ref="J1", x=10.0,
                              y=8.0, nets={"1": net}),
                   self.place("Pad", [("1", 0, 0, 0.6, 0.6)], ref="J2", x=40.0,
                              y=8.0, nets={"1": net}))

    def _pillars_in_a_pour(self, net="A"):
        self._pillars(net)
        self.apply({"op": "add_zone", "layer": "F.Cu", "net": net,
                    "x0": 0.0, "y0": 0.0, "x1": 60.0, "y1": 40.0})

    def _fake_fill(self, calls, status="ok"):
        def fill(path, in_place=False, timeout=300):
            calls.append({"path": path, "in_place": in_place})
            if status != "ok":
                return {"status": "error", "reason_code": status,
                        "message": "engine said no"}
            return {"status": "ok",
                    "value": {"zones": 1, "poured_zones": 1,
                              "fill_area_mm2": 2400.0, "written_to_file": True,
                              "checkpoint_id": "cp-1", "per_zone": [],
                              "poured_nothing": []},
                    "unit": "zones / mm2", "model": "vendor_zone_filler"}
        return fill

    def test_a_refill_runs_after_the_copper_and_says_so(self):
        self._pillars_in_a_pour()
        calls = []
        real = tools._fill.fill
        tools._fill.fill = self._fake_fill(calls)
        try:
            out = self.call(nets="A", layers="F.Cu", apply=True, refill_pours=True)
        finally:
            tools._fill.fill = real
        self.assertEqual(out["status"], "ok", out)
        self.assertEqual(len(calls), 1, calls)
        self.assertTrue(calls[0]["in_place"])
        self.assertEqual(out["refill"]["status"], "refilled")
        self.assertTrue(out["value"]["refilled"])
        self.assertEqual(out["value"]["fill_area_mm2"], 2400.0)
        self.assertTrue(out["assumptions"]["pour"]["refilled"])
        self.assertIn("board_drc", out["next_step"])

    def test_without_the_flag_no_filler_is_run(self):
        self._pillars_in_a_pour()
        calls = []
        real = tools._fill.fill
        tools._fill.fill = self._fake_fill(calls)
        try:
            out = self.call(nets="A", layers="F.Cu", apply=True)
        finally:
            tools._fill.fill = real
        self.assertEqual(out["status"], "ok", out)
        self.assertNotIn("refill", out)
        self.assertEqual(calls, [])

    def test_nothing_stale_is_not_a_refill(self):
        self._pillars()                     # no zone at all: nothing can go stale
        calls = []
        real = tools._fill.fill
        tools._fill.fill = self._fake_fill(calls)
        try:
            out = self.call(nets="A", layers="F.Cu", apply=True, refill_pours=True)
        finally:
            tools._fill.fill = real
        self.assertEqual(out["refill"]["status"], "nothing_stale")
        self.assertNotIn("refilled", out["value"])
        self.assertEqual(calls, [])

    def test_a_refusal_is_reported_and_the_copper_stays(self):
        self._pillars_in_a_pour()
        calls = []
        real = tools._fill.fill
        tools._fill.fill = self._fake_fill(calls, status="ENGINE_FAILED")
        try:
            out = self.call(nets="A", layers="F.Cu", apply=True, refill_pours=True)
        finally:
            tools._fill.fill = real
        self.assertEqual(out["status"], "ok", out)   # the copper itself was written
        self.assertEqual(out["refill"]["status"], "refused")
        self.assertEqual(out["refill"]["error"]["reason_code"], "ENGINE_FAILED")
        self.assertNotIn("refilled", out["value"])
        self.assertIn("still the pre-routing ones", out["next_step"])

class TestABranchPointIsNotAPad(RouteBase):
    """A spanning tree may only bend at pads; copper may bend wherever it likes.

    Three pads at (0,0), (2,2) and (4,0) cost 4+4 = 8 mm of Manhattan copper as a
    spanning tree, and 2+2+2 = 6 mm if the tree branches at (2,0) - a point that
    is not a pad. `topology="steiner"` lets the router use that point.

    What is NOT claimed: this is the iterated 1-Steiner heuristic on the Hanan
    grid, an upper bound on the exact rectilinear Steiner tree, so a saving that
    exists can still be missed. And the lengths here are pad-centre to pad-centre
    before the search has avoided anything; the copper that is really laid down
    is reported per net.
    """

    def test_the_classic_star_gets_shorter(self):
        pts = [(0.0, 0.0), (2.0, 2.0), (4.0, 0.0)]
        mst = TOPO.tree(pts, "mst")
        st = TOPO.tree(pts, "steiner")
        # mst mode keeps the number the router has always reported: a Manhattan
        # spanning tree whose edges are then measured in Euclidean mm (on this
        # three-point tie it joins (0,0)-(2,2) and (0,0)-(4,0))
        self.assertAlmostEqual(mst["length_mm"], 2 * math.sqrt(2) + 4.0, places=3)
        # steiner mode reports the metric the search actually pays - Manhattan
        self.assertEqual(st["mst_length_mm"], 8.0)
        self.assertEqual(st["length_mm"], 6.0)
        self.assertEqual(st["saved_mm"], 2.0)
        self.assertEqual([list(p) for p in st["steiner"]], [[2.0, 0.0]])
        self.assertEqual(len(st["edges"]), 3)          # 3 pads + 1 branch - 1

    def test_two_pads_have_nothing_to_branch(self):
        st = TOPO.tree([(0.0, 0.0), (5.0, 0.0)], "steiner")
        self.assertEqual(st["steiner"], [])
        self.assertEqual(st["length_mm"], 5.0)
        self.assertIn("nothing to branch", st["note"])

    def test_the_heuristic_never_makes_the_tree_longer(self):
        pts = [(0.0, 0.0), (10.0, 0.0), (0.0, 10.0), (10.0, 10.0),
               (5.0, 3.0)]
        st = TOPO.tree(pts, "steiner")
        self.assertLessEqual(st["length_mm"], st["mst_length_mm"] + 1e-9)
        self.assertGreaterEqual(st["saved_mm"], 0.0)

    def test_an_unknown_topology_is_refused(self):
        board = self.load()
        with self.assertRaises(router.RouteError) as caught:
            router.plan(board, topology="rmst")
        self.assertEqual(caught.exception.reason_code, "ARG_INVALID")
        out = self.call(nets="A", layers="F.Cu", topology="rmst")
        self.assertEqual(out["status"], "error")
        self.assertEqual(out["reason_code"], "ARG_INVALID")

    def _star(self, net="A"):
        self.apply(self.place("Pad", [("1", 0, 0, 0.6, 0.6)], ref="J1", x=10.0,
                              y=10.0, nets={"1": net}),
                   self.place("Pad", [("1", 0, 0, 0.6, 0.6)], ref="J2", x=20.0,
                              y=20.0, nets={"1": net}),
                   self.place("Pad", [("1", 0, 0, 0.6, 0.6)], ref="J3", x=30.0,
                              y=10.0, nets={"1": net}))

    def test_the_router_uses_a_branch_point_when_asked(self):
        self._star()
        res = self.route(nets=["A"], layers=["F.Cu"], topology="steiner")
        self.assertEqual(res["failed"], [], res["failed"])
        self.assertEqual(res["stats"]["topology"], "steiner")
        net = res["nets"][0]
        self.assertEqual(net["topology"], "steiner")
        self.assertGreaterEqual(len(net["steiner_points"]), 1)
        self.assertLessEqual(net["tree_mm"], net["tree_mm_mst"] + 1e-9)
        self.assertGreater(net["segments"], 0)

    def test_mst_is_still_the_default(self):
        self._star()
        res = self.route(nets=["A"], layers=["F.Cu"])
        net = res["nets"][0]
        self.assertEqual(net["topology"], "mst")
        self.assertNotIn("steiner_points", net)
        self.assertEqual(net["edges"], 2)


if __name__ == "__main__":
    unittest.main()

class _StubGrid(object):
    """Just enough grid for the emitter: it says which layers block a via."""
    def __init__(self, layers, blocked=()):
        self.layers = list(layers)
        self.pitch = 0.1
        self.via_dia = 0.6
        self.blocked = set(blocked)

    def via_blockers(self, x, y, net, span=None):
        for layer in (span or self.layers):
            if layer in self.blocked:
                return [{"why": "stub: %s blocks a via" % layer}]
        return []

STUB_BUNDLE = {"default": {"via_diameter_mm": 0.6, "via_drill_mm": 0.3,
                           "track_mm": 0.25, "clearance_mm": 0.2}}

class TestBlindAndBuriedVias(unittest.TestCase):
    """A via's span is what makes it blind, buried or through - and the file has
    to say which one it is, or the model and KiCad disagree about the copper."""

    STACK = ["F.Cu", "In1.Cu", "In2.Cu", "B.Cu"]

    def emitter(self, grid, blind=False):
        return router._Emitter(grid, STUB_BUNDLE, layers=grid.layers,
                              blind_vias=blind)

    def op(self, em):
        return em._buf[-1][1]

    def test_a_through_via_carries_no_layers_key(self):
        em = self.emitter(_StubGrid(self.STACK))
        self.assertTrue(em.via("A", 1.0, 1.0))
        self.assertNotIn("layers", self.op(em))

    def test_a_partial_span_is_written_when_blind_vias_are_allowed(self):
        em = self.emitter(_StubGrid(self.STACK), blind=True)
        self.assertTrue(em.via("A", 1.0, 1.0, ["F.Cu", "In1.Cu"]))
        self.assertEqual(self.op(em)["layers"], ["F.Cu", "In1.Cu"])

    def test_the_span_is_kept_in_stackup_order_whatever_the_caller_passed(self):
        em = self.emitter(_StubGrid(self.STACK), blind=True)
        self.assertTrue(em.via("A", 1.0, 1.0, ["In2.Cu", "F.Cu"]))
        self.assertEqual(self.op(em)["layers"], ["F.Cu", "In2.Cu"])

    def test_without_blind_vias_a_partial_span_becomes_a_through_via(self):
        # B.Cu blocks a via here: a through via must be refused, so the caller
        # cannot be told "your blind via fitted" when the file says through.
        em = self.emitter(_StubGrid(self.STACK, blocked=["B.Cu"]), blind=False)
        self.assertFalse(em.via("A", 1.0, 1.0, ["F.Cu", "In1.Cu"]))
        self.assertEqual(em.bad[-1]["span"], ["F.Cu", "B.Cu"])

    def test_with_blind_vias_the_same_spot_is_legal(self):
        em = self.emitter(_StubGrid(self.STACK, blocked=["B.Cu"]), blind=True)
        self.assertTrue(em.via("A", 1.0, 1.0, ["F.Cu", "In1.Cu"]))
        self.assertEqual(em.bad, [])

    def test_a_full_span_never_gains_a_layers_key(self):
        em = self.emitter(_StubGrid(self.STACK), blind=True)
        self.assertTrue(em.via("A", 1.0, 1.0, list(self.STACK)))
        self.assertNotIn("layers", self.op(em))

class TestTheToolIsRetiredNotTheRouter(RouteBase):
    """0.0.107 removed the TOOL, and this is the one place that says so.

    Eighteen cases above ask the router - `tools._board_route` - because that is
    where the behaviour they describe still lives. This case pins the other half:
    the NAME is not reachable from a caller, and calling it is not a typo. Two
    different answers ("unknown tool" vs "that tool is gone on purpose") are what
    keep the removal from reading like a bug in this server.
    """

    def test_calling_the_name_is_tool_retired_and_not_tool_not_found(self):
        out = tools.call_tool("board_route", {"path": self.board})
        self.assertEqual(out["status"], "error")
        self.assertEqual(out["reason_code"], "TOOL_RETIRED")
        self.assertNotEqual(out["reason_code"], "TOOL_NOT_FOUND")
        # the refusal has to name what was removed, or a caller reads "gone" as
        # "this server cannot do that today" and tries again tomorrow
        self.assertIn("autorout", out["message"])

    def test_the_name_is_not_advertised_under_any_profile(self):
        for profile in ("core", "all", "default"):
            names = tools.list_tools(profile)
            self.assertNotIn("board_route", names if isinstance(names, dict)
                             else json.loads(json.dumps(names)))

    def test_the_board_is_not_touched_by_the_refusal(self):
        before = read_bytes(self.board)
        tools.call_tool("board_route", {"path": self.board, "apply": True})
        self.assertEqual(read_bytes(self.board), before)
