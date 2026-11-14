"""Length tuning on finished copper: what it claims, and the failing twin of each.

The tool's value is not "it made a squiggle". It is four claims, each of which can
be false while the output still looks right:

* the length it reports is the length of the TRACKS it wrote (vias are counted and
  NOT added, arcs are reported and NOT tuned);
* every new segment went through the same exact polygon test the router uses -
  and the rule-derived rectangles travelled into that test, not just into the
  audit;
* a group it cannot match says so, with the number it reached and which segment
  refused - "blocked" and "too short" are different answers;
* only 0/45/90/135 copper is meandered, because a tooth fanned off a 30-degree
  segment lands at 75/105 and this package does not draw those.
"""
import math
import os
import shutil
import tempfile
import unittest

from pcb_agent_mcp import edit as E
from pcb_agent_mcp import tools
from pcb_agent_mcp.route import rules as R
from pcb_agent_mcp.route import tune as T
from pcb_agent_mcp.route import tune_board as TB

SPEC = {"width_mm": 40, "height_mm": 24, "copper_layers": 2, "engine": False,
        "title": "tune"}


def _angle(a, b):
    return math.degrees(math.atan2(b[1] - a[1], b[0] - a[0])) % 180.0


class _Board(unittest.TestCase):

    def setUp(self):
        self.tmp = tempfile.mkdtemp(prefix="pcbtune-")
        self._home = os.environ.get("PCB_AGENT_HOME")
        os.environ["PCB_AGENT_HOME"] = os.path.join(self.tmp, "home")
        self.path = os.path.join(self.tmp, "board.kicad_pcb")
        E.create_board(self.path, dict(SPEC))
        self.board, err = tools._load_board({"path": self.path})
        self.assertIsNone(err, err)

    def tearDown(self):
        if self._home is None:
            os.environ.pop("PCB_AGENT_HOME", None)
        else:
            os.environ["PCB_AGENT_HOME"] = self._home
        shutil.rmtree(self.tmp, ignore_errors=True)

    def write(self, *ops):
        plan = E.propose(self.path, list(ops))
        self.assertEqual(plan["blocking"], [], plan["blocking"])
        return E.apply_plan(plan["plan_id"], confirm=True,
                            options={"engine": False})

    def track(self, net, a, b, width=0.25, layer="F.Cu"):
        return {"op": "add_track", "x1": a[0], "y1": a[1], "x2": b[0],
                "y2": b[1], "width": width, "layer": layer, "net": net,
                "create_net": True}

    def reload(self):
        board, err = tools._load_board({"path": self.path})
        self.assertIsNone(err, err)
        return board


class TestWhatTheMeasurementIncludes(_Board):

    def test_vias_are_counted_but_never_added_to_length(self):
        self.write(self.track("A", (5.0, 5.0), (15.0, 5.0),
                              layer="F.Cu"),
                   {"op": "add_via", "x": 15.0, "y": 5.0, "net": "A",
                    "diameter": 0.6, "drill": 0.3})
        m = TB.measure(self.reload())["A"]
        self.assertAlmostEqual(m["length_mm"], 10.0, places=6)
        self.assertEqual(m["vias_not_counted"], 1)

    def test_arcs_are_reported_not_counted(self):
        # A board with no arc says so with a zero; the field exists per net
        # because a length that skipped an arc has to be readable as such.
        self.write(self.track("A", (5.0, 5.0), (15.0, 5.0)))
        m = TB.measure(self.reload())["A"]
        self.assertEqual(m["arcs_not_counted"], 0)
        self.assertEqual(m["segments"], 1)


class TestTheSerpentineLands(_Board):

    def setUp(self):
        _Board.setUp(self)
        self.write(self.track("LONG", (5.0, 5.0), (15.0, 5.0)),
                   self.track("SHORT", (5.0, 8.0), (13.0, 8.0)))
        self.board = self.reload()
        self.group = {"name": "one", "nets": ["LONG", "SHORT"], "tol_mm": 0.05}

    def test_the_skew_is_measured_before_and_after_not_assumed(self):
        before = TB.measure(self.board)
        self.assertGreater(abs(before["LONG"]["length_mm"]
                               - before["SHORT"]["length_mm"]), 1.5)
        res = TB.plan(self.board, [self.group])
        self.assertEqual(res["status"], "ok", res["groups"])
        g = res["groups"][0]
        self.assertGreater(g["before_skew_mm"], 1.5)
        self.assertLessEqual(g["after_skew_mm"], 0.05)

    def test_every_written_segment_is_on_the_45_grid(self):
        res = TB.plan(self.board, [self.group])
        added = [o for o in res["ops"] if o["op"] == "add_track"]
        self.assertGreater(len(added), 2)
        for op in added:
            ang = _angle((op["x1"], op["y1"]), (op["x2"], op["y2"]))
            self.assertTrue(any(min(abs(ang - g), 180 - abs(ang - g)) <= 0.06
                                for g in (0, 45, 90, 135)),
                            "segment at %.3f degrees" % ang)

    def test_the_net_stays_connected_and_the_file_agrees_after_the_write(self):
        res = TB.plan(self.board, [self.group])
        plan = E.propose(self.path, res["ops"])
        self.assertEqual(plan["blocking"], [], plan["blocking"])
        E.apply_plan(plan["plan_id"], confirm=True, options={"engine": False})
        after = TB.measure(self.reload())
        self.assertAlmostEqual(after["SHORT"]["length_mm"],
                               after["LONG"]["length_mm"], delta=0.05)
        # Connectivity: the replaced chain starts and ends where the old segment
        # did, so the two original end points still carry copper.
        segs = [s for s in TB.segments(self.reload()) if s["net"] == "SHORT"]
        pts = set(tuple(round(v, 4) for v in s["start"]) for s in segs)
        pts |= set(tuple(round(v, 4) for v in s["end"]) for s in segs)
        self.assertIn((5.0, 8.0), pts)
        self.assertIn((13.0, 8.0), pts)

    def test_the_default_amplitude_keeps_the_legs_three_widths_apart(self):
        # 3w between the legs of one tooth is what the amplitude buys; measuring
        # it here keeps "amplitude" from becoming a free knob.
        self.assertAlmostEqual(T.AMP_FACTOR, 3.0 * math.sqrt(2.0), places=9)
        self.assertGreaterEqual(T.AMP_FLOOR_MM, 0.5)


class TestTheRefusalsAreNamed(_Board):

    def test_a_keepout_across_the_corridor_is_refused_by_the_rule_not_ignored(self):
        self.write(self.track("LONG", (5.0, 5.0), (15.0, 5.0)),
                   self.track("SHORT", (5.0, 8.0), (13.5, 8.0)),
                   {"op": "add_keepout", "x0": 4.0, "y0": 6.0, "x1": 14.0,
                    "y1": 11.0, "layers": ["F.Cu"], "rules": ["tracks"]})
        board = self.reload()
        res = TB.plan(board, [{"name": "g", "nets": ["LONG", "SHORT"]}],
                      keepout_rects=[{"x0": 4.0, "y0": 6.0, "x1": 14.0,
                                      "y1": 11.0, "rule": "layout.test",
                                      "owner": "T1"}])
        self.assertEqual(res["status"], "length_not_matched")
        refused = [r for g in res["groups"] for e in g["per_net"]
                   for r in e["refusals"]]
        self.assertTrue(refused, res["groups"])
        self.assertIn(refused[0]["reason"], ("keepout", "crosses_keepout",
                                             "inside_keepout", "clearance"))

    def test_a_segment_off_the_45_grid_is_not_meandered(self):
        ang = math.tan(math.radians(30.0))
        # LONG2 at y=20, SHORT2 climbing AWAY from it: the only thing that can
        # refuse here is the angle, not a crossing.
        self.write(self.track("LONG2", (5.0, 20.0), (15.0, 20.0)),
                   self.track("SHORT2", (5.0, 15.0),
                              (10.0, 15.0 + 5.0 * ang)))
        res = TB.plan(self.reload(), [{"name": "g2",
                                       "nets": ["LONG2", "SHORT2"]}])
        self.assertEqual(res["status"], "length_not_matched")
        entry = res["groups"][0]["per_net"][1]
        self.assertEqual(entry["meanders"], 0)
        reasons = [r["reason"] for r in entry["refusals"]]
        self.assertIn("not_on_45_grid", reasons)

    def test_a_target_that_cannot_be_reached_reports_the_length_it_reached(self):
        self.write(self.track("LONG3", (5.0, 17.0), (15.0, 17.0)),
                   self.track("SHORT3", (5.0, 19.5), (13.0, 19.5)))
        res = TB.plan(self.reload(), [{"name": "g3",
                                       "nets": ["LONG3", "SHORT3"],
                                       "target_mm": 40.0,
                                       "max_replacements": 4}])
        self.assertEqual(res["status"], "length_not_matched")
        entry = res["groups"][0]["per_net"][1]
        self.assertLess(entry["after_mm"], 40.0)
        self.assertGreater(entry["after_mm"], entry["before_mm"])
        # Which of the two honest answers it is depends on the segment: a 13 mm
        # run runs out of room for another tooth (does_not_fit) before the
        # replacement budget does. Both are named; neither is a silent stop.
        self.assertIn("does_not_fit",
                      [r["reason"] for r in entry["refusals"]])


class TestNamesAndGroups(_Board):

    def test_a_sheet_path_net_answers_to_its_bare_name(self):
        names = {"/DDR_DQ0", "/DDR_DQ1", "/GND"}
        self.assertEqual(TB._match_name(names, "DQ0"), "/DDR_DQ0")
        self.assertEqual(TB._match_name(names, "/DDR_DQ1"), "/DDR_DQ1")
        self.assertIsNone(TB._match_name(names, "DQ2"))

    def test_the_ddr_tables_produce_groups_only_for_copper_that_exists(self):
        self.write(self.track("/DDR_DQS0P", (5.0, 5.0), (15.0, 5.0)),
                   self.track("/DDR_DQS0N", (5.0, 7.0), (13.0, 7.0)))
        groups = TB.ddr_groups(self.reload())
        self.assertEqual([g["name"] for g in groups], ["pair DQS0P/DQS0N"])
        self.assertEqual(groups[0]["nets"], ["/DDR_DQS0P", "/DDR_DQS0N"])


class TestTheToolAnswers(_Board):

    def test_the_tool_plans_and_writes_nothing_without_apply(self):
        self.write(self.track("LONG", (5.0, 5.0), (15.0, 5.0)),
                   self.track("SHORT", (5.0, 8.0), (13.0, 8.0)))
        before = os.stat(self.path).st_mtime
        out = tools.TOOLS["board_tune_length"]["handler"](
            {"path": self.path, "groups": [{"nets": ["LONG", "SHORT"]}]})
        self.assertEqual(out["status"], "ok", out.get("message"))
        self.assertEqual(out["value"]["groups"][0]["status"], "matched")
        self.assertEqual(os.stat(self.path).st_mtime, before)

    def test_an_board_with_no_track_is_a_refusal_not_a_zero_skew(self):
        with self.assertRaises(TB.TuneError) as ctx:
            TB.plan(self.board, [{"nets": ["A", "B"]}])
        self.assertEqual(ctx.exception.reason_code, "TUNE_NO_COPPER")


class TestTheApplyFloor(_Board):
    """A meander legal by the net rule can still be refused by the write gate.

    `edit.apply_plan` will not grow the count of different-net pairs closer than
    0.2 mm, and a board's bundles are often already at 0.1 mm. The tool therefore
    COUNTS that cost instead of pretending it does not exist: the number travels
    in `clearance_floor`, and the write needs the caller to say so.
    """

    def _pair_in_a_channel(self):
        # The apply floor (0.2 mm) and this board's own net rule (0.1 mm) have to
        # be DIFFERENT numbers, or the test cannot tell "counted" from "refused":
        # the created template ships a 0.2 mm class, so it is narrowed here.
        raw = open(self.path, "rb").read()
        self.assertEqual(raw.count(b"(clearance 0.2)"), 1)
        open(self.path, "wb").write(raw.replace(b"(clearance 0.2)",
                                                b"(clearance 0.1)"))
        self.board = self.reload()
        # SHORT is 1.5 mm behind, and two neighbours sit 0.6 mm either side of it:
        # far enough that the net rule allows a narrow tooth, close enough that
        # the 0.2 mm floor forbids the wide ones.
        self.write(self.track("LONG", (5.0, 5.0), (15.0, 5.0)),
                   self.track("SHORT", (5.0, 8.0), (13.5, 8.0)),
                   self.track("N1", (5.0, 7.4), (13.0, 7.4)),
                   self.track("N2", (5.0, 8.6), (13.0, 8.6)))
        return [{"name": "pair", "nets": ["LONG", "SHORT"], "tol_mm": 0.05}]

    def test_the_floor_number_is_the_one_the_gate_uses(self):
        from pcb_agent_mcp.ruleset import precondition
        self.assertEqual(TB.FLOOR_MM, precondition.CLEARANCE_FLOOR_MM)

    def test_a_rule_legal_meander_reports_its_floor_cost(self):
        groups = self._pair_in_a_channel()
        res = TB.plan(self.reload(), groups)
        self.assertEqual(res["groups"][0]["status"], "matched")
        self.assertGreaterEqual(res["clearance_floor"]["new_pairs"], 1)
        self.assertEqual(res["clearance_floor"]["floor_mm"], TB.FLOOR_MM)
        self.assertLess(res["clearance_floor"]["worst"][0]["gap_mm"],
                        TB.FLOOR_MM)
        self.assertGreaterEqual(res["clearance_floor"]["worst"][0]["gap_mm"], 0.1)

    def test_judging_with_the_floor_refuses_the_same_meander(self):
        groups = self._pair_in_a_channel()
        res = TB.plan(self.reload(), groups, floor_mm=TB.FLOOR_MM)
        self.assertEqual(res["groups"][0]["status"], "length_not_matched")
        reasons = [r["reason"] for e in res["groups"][0]["per_net"]
                   for r in e["refusals"]]
        self.assertIn("clearance", reasons)
        self.assertEqual(res["clearance_floor"]["new_pairs"], 0)

    def test_the_tool_will_not_write_when_the_floor_cost_is_unacknowledged(self):
        groups = self._pair_in_a_channel()
        before = os.stat(self.path).st_mtime
        out = tools.TOOLS["board_tune_length"]["handler"](
            {"path": self.path, "groups": groups, "apply": True})
        self.assertEqual(out["status"], "ok", out.get("message"))
        self.assertIn("allow_new_violations", out["next_step"])
        self.assertEqual(os.stat(self.path).st_mtime, before)

FOUR = {"width_mm": 40, "height_mm": 24, "copper_layers": 4, "engine": False,
        "title": "tune4"}
LAYERS4 = ["F.Cu", "In1.Cu", "In2.Cu", "B.Cu"]


class TestThroughViaCopperIsOnEveryInnerLayer(unittest.TestCase):
    """A `(layers "F.Cu" "B.Cu")` via is a THROUGH via: its ring is on every
    inner layer, not only on the two it names.

    The failing twin is one real pair on the RK3506 board: a meander on In2.Cu
    was accepted 0.0069 mm from a GND through via, because the blocker set
    filtered vias by (from, to) only - so the via did not exist on In2.Cu at
    all, and `seg_blockers` said the corridor was free. `kicad-cli pcb drc`
    reported it on the written board. The negative twin below is a BLIND via
    F.Cu->In1.Cu at the same point: it must stay invisible on In2.Cu, or the
    fix has simply moved the error to the other direction.
    """

    def setUp(self):
        self.tmp = tempfile.mkdtemp(prefix="pcbtune4-")
        self._home = os.environ.get("PCB_AGENT_HOME")
        os.environ["PCB_AGENT_HOME"] = os.path.join(self.tmp, "home")

    def tearDown(self):
        if self._home is None:
            os.environ.pop("PCB_AGENT_HOME", None)
        else:
            os.environ["PCB_AGENT_HOME"] = self._home
        shutil.rmtree(self.tmp, ignore_errors=True)

    def board_with_via(self, layers):
        tag = "-".join(l.replace(".Cu", "") for l in layers)
        path = os.path.join(self.tmp, "via-%s.kicad_pcb" % tag)
        E.create_board(path, dict(FOUR))
        ops = [{"op": "add_via", "x": 10.0, "y": 10.0, "net": "GND",
                "size": 0.6, "drill": 0.3, "create_net": True,
                "layers": list(layers)}]
        plan = E.propose(path, ops)
        self.assertEqual(plan["blocking"], [], plan["blocking"])
        res = E.apply_plan(plan["plan_id"], confirm=True, options={"engine": False})
        self.assertEqual(res["status"], "ok", res)
        board, err = tools._load_board({"path": path})
        self.assertIsNone(err, err)
        return path, board

    def test_a_through_via_blocks_the_inner_layer(self):
        from pcb_agent_mcp.route.grid import Grid
        _, board = self.board_with_via(["F.Cu", "B.Cu"])
        grid = Grid(board, LAYERS4, 0.05, R.resolve(board))
        hits = grid.seg_blockers((8.0, 10.5), (12.0, 10.5), "In2.Cu", "X", 0.25)
        self.assertEqual(len(hits), 1, hits)
        self.assertEqual(hits[0]["kind"], "via")
        self.assertAlmostEqual(hits[0]["actual_mm"], 0.5 - 0.125 - 0.3,
                               places=3)

    def test_a_blind_via_does_not_block_a_layer_it_never_reaches(self):
        from pcb_agent_mcp.route.grid import Grid
        _, board = self.board_with_via(["F.Cu", "In1.Cu"])
        grid = Grid(board, LAYERS4, 0.05, R.resolve(board))
        hits = grid.seg_blockers((8.0, 10.0), (12.0, 10.0), "In2.Cu", "X", 0.25)
        self.assertEqual(hits, [], hits)

    def test_the_clearance_gate_reads_the_through_via_as_every_layer(self):
        from pcb_agent_mcp.edit import checks
        from pcb_agent_mcp.fidelity import kicad_doc as KD
        through, _ = self.board_with_via(["F.Cu", "B.Cu"])
        blind, _ = self.board_with_via(["F.Cu", "In1.Cu"])
        doc = KD.KiCadDocument.from_path(through)
        vias = [i for i in checks._copper_items(doc) if i["kind"] == "via"]
        self.assertEqual([i["layer"] for i in vias], ["*"])
        doc = KD.KiCadDocument.from_path(blind)
        vias = [i for i in checks._copper_items(doc) if i["kind"] == "via"]
        self.assertEqual(sorted(i["layer"] for i in vias), ["F.Cu", "In1.Cu"])


if __name__ == "__main__":
    unittest.main()
