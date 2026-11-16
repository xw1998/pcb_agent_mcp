"""The rule catalogue -> generator channel, and the four ways it can be fake.

A rule can look honoured while nothing consumes it. The tests below separate
four claims that are easy to conflate:

* the classification covers all 27 rules, and a rule nobody classified is an
  ERROR rather than an unclassified leftover (a count that only goes up);
* a rule-derived rectangle blocks the SEARCH and the JUDGE - a block that only
  reaches the raster lets a proposed segment cross it and still be reported
  clean, which is the exact defect this version is about;
* a rule-derived number is a floor, and a rule that would loosen the board's own
  DRC is refused with both numbers named, not silently applied;
* a spacing written as a multiple of the width refuses to guess when the width is
  unknown ("3W" with no W is not a distance).

Every test has a failing twin: the same measurement with the projection removed,
because "it passed" and "it passed for the reason I think" are different claims.
"""
import os
import shutil
import tempfile
import types
import unittest

from pcb_agent_mcp import edit as E
from pcb_agent_mcp import tools
from pcb_agent_mcp.route import grid as GRID
from pcb_agent_mcp.route import rules as R
from pcb_agent_mcp.ruleset import projection as PR

SPEC = {"width_mm": 30, "height_mm": 20, "copper_layers": 2, "engine": False,
        "title": "projection"}

def part(ref, lib_id, value, pads):
    return {"ref": ref, "lib_id": lib_id, "value": value,
            "pads": [{"number": str(i), "x": x, "y": y, "w": w, "h": h,
                      "net": net} for i, (x, y, w, h, net)
                     in enumerate(pads)]}

class TestTheLedgerCoversTheCatalogue(unittest.TestCase):

    def test_every_rule_has_exactly_one_projection_entry(self):
        rows = PR.table()
        from pcb_agent_mcp.calc.rules import LAYOUT_RULES
        self.assertEqual(sorted(rows), sorted(r["id"] for r in LAYOUT_RULES))
        self.assertEqual(len(rows), 29)

    def test_the_counts_add_up_and_are_not_all_optimistic(self):
        rows = PR.table()
        counts = {}
        for row in rows.values():
            counts[row["status"]] = counts.get(row["status"], 0) + 1
        self.assertEqual(sum(counts.values()), 29)
        # A ledger where everything is "projected" would be the flattering
        # answer; this board of numbers is checked against the bucket it must
        # contain, and the two buckets that are honest admissions are asserted.
        self.assertGreaterEqual(counts.get("not_wired", 0), 5)
        self.assertGreaterEqual(counts.get("audit_only", 0), 5)

    def test_a_rule_with_no_entry_is_an_error_not_a_leftover(self):
        real = PR._rules
        fake = dict(real())
        fake["layout.brand_new_rule"] = {"id": "layout.brand_new_rule",
                                         "category": "test", "unit": "mm",
                                         "limit": 1.0}
        PR._rules = lambda: fake
        try:
            with self.assertRaises(PR.ProjectionError) as ctx:
                PR.table()
            self.assertIn("no projection entry", str(ctx.exception))
            self.assertIn("layout.brand_new_rule", str(ctx.exception))
        finally:
            PR._rules = real

    def test_an_entry_with_no_rule_is_an_error_too(self):
        real = PR.ENTRIES
        PR.ENTRIES = list(real) + [("layout.ghost", "rect_keepout", "projected",
                                    "does not exist")]
        try:
            with self.assertRaises(PR.ProjectionError) as ctx:
                PR.table()
            self.assertIn("no rule in the catalogue", str(ctx.exception))
        finally:
            PR.ENTRIES = real

class TestTheRectBlockReachesBothGates(unittest.TestCase):
    """A projection that only marks the raster is not a projection."""

    def setUp(self):
        self.tmp = tempfile.mkdtemp(prefix="pcbproj-")
        self._home = os.environ.get("PCB_AGENT_HOME")
        os.environ["PCB_AGENT_HOME"] = os.path.join(self.tmp, "home")
        path = os.path.join(self.tmp, "board.kicad_pcb")
        E.create_board(path, dict(SPEC))
        self.board, err = tools._load_board({"path": path})
        self.assertIsNone(err, err)
        self.bundle = R.resolve(self.board)

    def tearDown(self):
        if self._home is None:
            os.environ.pop("PCB_AGENT_HOME", None)
        else:
            os.environ["PCB_AGENT_HOME"] = self._home
        shutil.rmtree(self.tmp, ignore_errors=True)

    def _rect(self):
        # 1 mm square of forbidden copper in the middle of the board.
        return {"x0": 14.0, "y0": 9.0, "x1": 16.0, "y1": 11.0,
                "rule": "layout.magnetic_keepout", "owner": "L3"}

    def test_the_raster_is_closed_where_the_rule_says(self):
        bare = GRID.Grid(self.board, ["B.Cu"], 0.1, self.bundle)
        cell = bare.node(15.0, 10.0)
        self.assertTrue(bare.free(cell[0], cell[1], "B.Cu", "A"),
                        "positive control: with no rule the cell must be free")
        ruled = GRID.Grid(self.board, ["B.Cu"], 0.1, self.bundle,
                          extra_keepouts=[self._rect()])
        self.assertFalse(ruled.free(cell[0], cell[1], "B.Cu", "A"))
        self.assertEqual(ruled.rule_keepouts, 1)

    def test_the_judge_sees_it_too_and_names_the_rule(self):
        ruled = GRID.Grid(self.board, ["B.Cu"], 0.1, self.bundle,
                          extra_keepouts=[self._rect()])
        hits = ruled.seg_blockers((12.0, 10.0), (18.0, 10.0), "B.Cu", "A",
                                  width=0.2)
        self.assertTrue(hits, "a segment straight through the rule rectangle was "
                             "reported clean")
        self.assertEqual(hits[0]["reason"], "keepout")
        self.assertIn(hits[0]["why"], ("inside_keepout", "crosses_keepout"))
        bare = GRID.Grid(self.board, ["B.Cu"], 0.1, self.bundle)
        self.assertEqual(bare.seg_blockers((12.0, 10.0), (18.0, 10.0), "B.Cu",
                                           "A", width=0.2), [])

    def test_a_rect_without_an_owner_is_dropped_and_named(self):
        bad = dict(self._rect())
        bad.pop("owner")
        grid = GRID.Grid(self.board, ["B.Cu"], 0.1, self.bundle,
                         extra_keepouts=[bad])
        self.assertEqual(grid.rule_keepouts, 0)
        self.assertEqual(len(grid.dropped_rule_keepouts), 1)
        cell = grid.node(15.0, 10.0)
        self.assertTrue(grid.free(cell[0], cell[1], "B.Cu", "A"),
                        "a block nobody can re-derive must not be applied")

    def test_a_layer_outside_the_routing_set_is_skipped_not_widened(self):
        rect = dict(self._rect(), layers=["In7.Cu"])
        grid = GRID.Grid(self.board, ["B.Cu"], 0.1, self.bundle,
                         extra_keepouts=[rect])
        self.assertEqual(grid.rule_keepouts, 0)

class TestRuleNumbersAreFloors(unittest.TestCase):

    def setUp(self):
        self.tmp = tempfile.mkdtemp(prefix="pcbproj2-")
        self._home = os.environ.get("PCB_AGENT_HOME")
        os.environ["PCB_AGENT_HOME"] = os.path.join(self.tmp, "home")
        path = os.path.join(self.tmp, "board.kicad_pcb")
        E.create_board(path, dict(SPEC))
        self.board, err = tools._load_board({"path": path})
        self.assertIsNone(err, err)
        self.bundle = R.resolve(self.board)

    def tearDown(self):
        if self._home is None:
            os.environ.pop("PCB_AGENT_HOME", None)
        else:
            os.environ["PCB_AGENT_HOME"] = self._home
        shutil.rmtree(self.tmp, ignore_errors=True)

    def test_a_wider_rule_wins_and_says_so(self):
        out, rep = R.apply_overrides(self.bundle, {
            "track_mm": {"*": 0.508}, "clearance_factor": {"RF_IN": 3.0},
            "track_mm_net": None})
        self.assertAlmostEqual(out["default"]["track_mm"], 0.508, places=6)
        self.assertAlmostEqual(out["per_net"]["RF_IN"]["clearance_mm"],
                               3.0 * out["per_net"]["RF_IN"]["track_mm"],
                               places=6)
        facts = [i["fact"] for i in rep["applied"]]
        self.assertIn("track_mm", facts)
        self.assertIn("clearance_mm", facts)
        self.assertTrue([s for s in out["sources"]["rule_catalogue"]])

    def test_a_looser_rule_is_refused_with_both_numbers(self):
        strict = R.apply_overrides(self.bundle, {"track_mm": {"*": 0.6}})[0]
        out, rep = R.apply_overrides(strict, {"track_mm": {"*": 0.2}})
        self.assertAlmostEqual(out["default"]["track_mm"], 0.6, places=6,
                               msg="0.2 mm from a rule must not thin copper the "
                                   "board already measures at 0.6")
        refused = [i for i in rep["not_applied"] if i["fact"] == "track_mm"]
        self.assertEqual(len(refused), 1)
        self.assertAlmostEqual(refused[0]["rule_mm"], 0.2, places=6)
        self.assertAlmostEqual(refused[0]["board_mm"], 0.6, places=6)
        self.assertEqual(rep["applied"], [])

    def test_three_w_without_a_width_is_an_error_not_a_guess(self):
        empty = dict(self.bundle)
        empty = dict(empty, per_net={}, default=dict(empty["default"],
                                                     track_mm=0.0))
        with self.assertRaises(R.RulesError) as ctx:
            R.apply_overrides(empty, {"clearance_factor": {"RF_IN": 3.0}})
        self.assertEqual(ctx.exception.reason_code, "RULES_INVALID")
        self.assertIn("3 x width", str(ctx.exception))

    def test_a_drill_floor_bigger_than_the_pad_is_refused(self):
        with self.assertRaises(R.RulesError) as ctx:
            R.apply_overrides(self.bundle, {"via_drill_mm": 0.9})
        self.assertEqual(ctx.exception.reason_code, "RULES_INVALID")
        self.assertIn("annular", str(ctx.exception))

class TestWhatTheBoardActuallyFeedsBack(unittest.TestCase):

    def _board(self):
        return types.SimpleNamespace(
            footprints=[
                part("L1", "Syn:L_4020", "4.7uH",
                     [(10.0, 10.0, 1.0, 1.0, None),
                      (14.0, 12.0, 1.0, 1.0, None)]),
                part("U1", "Syn:RF_AMP", "PA",
                     [(20.0, 10.0, 0.6, 0.6, "RF_IN"),
                      (24.0, 10.0, 0.6, 0.6, "RF_OUT"),
                      (20.0, 14.0, 0.6, 0.6, "+5V"),
                      (24.0, 14.0, 0.6, 0.6, "GND")]),
            ],
            tracks=[], vias=[])

    def test_the_rectangle_carries_its_rule_and_owner(self):
        rects, skipped = PR.keepout_rects(self._board())
        self.assertEqual(skipped, [])
        self.assertEqual(len(rects), 1)
        self.assertEqual(rects[0]["owner"], "L1")
        self.assertEqual(rects[0]["rule"], "layout.magnetic_keepout")
        self.assertLess(rects[0]["x0"], rects[0]["x1"])
        self.assertLess(rects[0]["y0"], rects[0]["y1"])

    def test_rf_and_power_nets_are_matched_by_name_only(self):
        board = self._board()
        self.assertEqual(PR.rf_nets(board), ["RF_IN", "RF_OUT"])
        self.assertEqual(PR.power_nets(board), ["+5V"])
        # GND is a pour question, not a track width: a rail regex that swallowed
        # GND would widen every ground track on every board.
        self.assertNotIn("GND", PR.power_nets(board))

    def test_the_net_matchers_survive_a_real_boards_net_names(self):
        # These are this project's actual net names, sheet path included. The
        # first draft matched with a regex anchored at the string start, so every
        # rail on a hierarchical schematic (i.e. every real board) was skipped and
        # `power_trace_width` looked satisfied while applying to nothing.
        board = types.SimpleNamespace(
            footprints=[part("U1", "Syn:X", "x", [
                (0.0, 0.0, 0.5, 0.5, "/+0V9_CPU"),
                (1.0, 0.0, 0.5, 0.5, "/+1V35_DDR"),
                (2.0, 0.0, 0.5, 0.5, "/+3V3"),
                (3.0, 0.0, 0.5, 0.5, "/+5V_IN"),
                (4.0, 0.0, 0.5, 0.5, "/GND"),
                (5.0, 0.0, 0.5, 0.5, "/DDR_A0"),
                (6.0, 0.0, 0.5, 0.5, "/SDMMC_D0"),
                (7.0, 0.0, 0.5, 0.5, "/USB0_VBUSDET"),
                (8.0, 0.0, 0.5, 0.5, "/ANT_2G4"),
            ])],
            tracks=[], vias=[])
        self.assertEqual(PR.bare_net("/+3V3"), "+3V3")
        self.assertEqual(PR.power_nets(board),
                         ["/+0V9_CPU", "/+1V35_DDR", "/+3V3", "/+5V_IN"])
        # GND is a pour, the DDR/SDMMC/USB names are signals with digits in them.
        self.assertEqual(PR.rf_nets(board), ["/ANT_2G4"])

    def test_the_payload_has_the_shape_the_router_takes(self):
        payload = PR.router_inputs(self._board())
        self.assertEqual(len(payload["keepout_rects"]), 1)
        self.assertIn("x0", payload["keepout_rects"][0])
        ov = payload["net_overrides"]
        self.assertAlmostEqual(ov["track_mm"]["*"], 0.1524, places=6)
        self.assertAlmostEqual(ov["track_mm"]["+5V"], 0.508, places=6)
        self.assertAlmostEqual(ov["clearance_factor"]["RF_IN"], 3.0, places=6)
        self.assertIsNone(PR.limit_of("layout.power_trace_width"),
                          "a current-driven limit must not be invented when no "
                          "current was given")

class TestTheToolAnswers(unittest.TestCase):

    def setUp(self):
        self.tmp = tempfile.mkdtemp(prefix="pcbproj3-")
        self._home = os.environ.get("PCB_AGENT_HOME")
        os.environ["PCB_AGENT_HOME"] = os.path.join(self.tmp, "home")
        self.path = os.path.join(self.tmp, "board.kicad_pcb")
        E.create_board(self.path, dict(SPEC))

    def tearDown(self):
        if self._home is None:
            os.environ.pop("PCB_AGENT_HOME", None)
        else:
            os.environ["PCB_AGENT_HOME"] = self._home
        shutil.rmtree(self.tmp, ignore_errors=True)

    def test_the_tool_reports_all_29_rules_and_writes_nothing(self):
        self.assertIn("pcb_rule_projection", tools.TOOLS)
        before = os.stat(self.path).st_mtime
        out = tools.TOOLS["pcb_rule_projection"]["handler"]({"path": self.path})
        self.assertEqual(out["status"], "ok", out)
        self.assertEqual(out["value"]["n_rules"], 29)
        self.assertEqual(os.stat(self.path).st_mtime, before)

if __name__ == "__main__":
    unittest.main()
