"""Tests for the DFM layer: a capability document YOU supply against a board.

Two separate things are being proven here.

1. The catalogue refuses to become a table. An unknown rule key and a missing
   `source` are errors, not defaults - because a typo'd rule reports as "not
   stated" and an unattributed limit is indistinguishable from an invented one.
2. The board-side measurements are the board's. Fixtures are small real
   `.kicad_pcb` files with hand-computed geometry, so every expected number
   below is arithmetic from the fixture text, not output copied from a run.

The fixture (50 x 30 mm outline, 1 mm slot):

  track 1  (10,10)->(30,10) 0.2 mm wide            narrowest track = 0.2
  track 2  (1,10)->(1,20)   0.25 mm wide           copper to left edge
                                                   = 1 - 0.25/2 = 0.875
  via A    (20,10) 0.6 / 0.3                       ring = (0.6-0.3)/2 = 0.15
  via B    (21,10) 0.6 / 0.3                       web to A = 1.0 - 0.15 - 0.15
                                                   = 0.7
  pad U1.1 (10,10) SMD 1 x 1 net VCC               ---------------------------------
  pad U2.1 (40,20) THT 1 x 1 drill 0.4 net VCC     ring = (1.0-0.4)/2 = 0.3
  pad U3.1 (25,15) SMD 1 x 1 net SIG               gap to track 1
                                                   = 14.5 - 10.1 = 4.4
  slot     (35,5)-(36,15) on Edge.Cuts             throat = 1.0
  silk     line (2,18)-(8,18) 0.12 wide            narrowest silk stroke = 0.12
  silk     every reference text is 1.0 mm high     smallest silk height = 1.0
  mask     U4.1/U4.2 1x1 on 1.4 mm pitch, both
           VCC, (setup (pad_to_mask_clearance 0.05))
                                                   web = 0.4 - 0.05 - 0.05 = 0.3
  hole     nothing of another net within 5 mm of
           a finished hole wall                    proven bound >= 5.0
"""
import itertools
import os
import sys
import tempfile
import unittest

sys.path.insert(0, os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "src"))

from pcb_agent_mcp import oracle, tools  # noqa: E402
from pcb_agent_mcp.adapters import kicad_pcb  # noqa: E402
from pcb_agent_mcp.calc import dfm  # noqa: E402

_seq = itertools.count()

BOARD = """(kicad_pcb (version 20221018) (generator pcbnew)
  (general (thickness 1.6))
  (layers (0 "F.Cu" signal) (31 "B.Cu" signal) (44 "Edge.Cuts" user))
  (setup (pad_to_mask_clearance 0.05))
  (net 0 "") (net 1 "VCC") (net 2 "SIG")
  (gr_rect (start 0 0) (end 50 30) (layer "Edge.Cuts") (width 0.1))
  (gr_rect (start 35 5) (end 36 15) (layer "Edge.Cuts") (width 0.1))
  (gr_line (start 2 28) (end 8 28) (stroke (width 0.12) (type solid))
    (layer "F.SilkS"))
  (footprint "T:A" (layer "F.Cu") (at 10 10)
    (fp_text reference "U1" (at 0 -2) (layer "F.SilkS")
      (effects (font (size 1 1) (thickness 0.15))))
    (pad "1" smd rect (at 0 0) (size 1 1) (layers "F.Cu") (net 1 "VCC")))
  (footprint "T:B" (layer "F.Cu") (at 40 20)
    (fp_text reference "U2" (at 0 -2) (layer "F.SilkS")
      (effects (font (size 1 1) (thickness 0.15))))
    (pad "1" thru_hole circle (at 0 0) (size 1 1) (drill 0.4)
      (layers "*.Cu" "*.Mask") (net 1 "VCC")))
  (footprint "T:C" (layer "F.Cu") (at 25 15)
    (fp_text reference "U3" (at 0 -2) (layer "F.SilkS")
      (effects (font (size 1 1) (thickness 0.15))))
    (pad "1" smd rect (at 0 0) (size 1 1) (layers "F.Cu") (net 2 "SIG")))
  (footprint "T:D" (layer "F.Cu") (at 45 5)
    (fp_text reference "U4" (at 0 -2) (layer "F.SilkS")
      (effects (font (size 1 1) (thickness 0.15))))
    (pad "1" smd rect (at -0.7 0) (size 1 1) (layers "F.Cu" "F.Mask")
      (net 1 "VCC"))
    (pad "2" smd rect (at 0.7 0) (size 1 1) (layers "F.Cu" "F.Mask")
      (net 1 "VCC")))
  (segment (start 10 10) (end 30 10) (width 0.2) (layer "F.Cu") (net 1))
  (segment (start 1 10) (end 1 20) (width 0.25) (layer "F.Cu") (net 1))
  (via (at 20 10) (size 0.6) (drill 0.3) (layers "F.Cu" "B.Cu") (net 1))
  (via (at 21 10) (size 0.6) (drill 0.3) (layers "F.Cu" "B.Cu") (net 1))
)
"""


def board():
    path = os.path.join(tempfile.gettempdir(),
                        "dfm_%d_%d.kicad_pcb" % (os.getpid(), next(_seq)))
    with open(path, "w", newline="") as fh:
        fh.write(BOARD)
    return kicad_pcb.parse_board(path)


def cap(rules, source="vendor capability page, checked 2026-10-28", verified=True,
        plating_um=25):
    out = {"fab": "test fab", "source": source, "verified": verified,
           "rules": dict(rules)}
    if plating_um is not None:
        out["plating_um"] = plating_um
    return out


ALL_RULES = {
    "min_track_width_mm": 0.2,
    "min_clearance_mm": 4.0,
    "min_via_drill_mm": 0.3,
    "min_via_annular_ring_mm": 0.15,
    "min_pth_annular_ring_mm": 0.3,
    "min_hole_to_hole_mm": 0.7,
    "min_copper_to_edge_mm": 0.875,
    "min_slot_width_mm": 1.0,
    "max_board_width_mm": 50.0,
    "max_board_height_mm": 30.0,
    "max_copper_layers": 2,
    "min_silk_line_width_mm": 0.10,
    "min_silk_text_height_mm": 0.8,
    "min_mask_web_mm": 0.25,
    "min_copper_to_hole_mm": 0.5,
}


class TestCapabilityDocument(unittest.TestCase):
    """The catalogue must reject, not fill in."""

    def test_source_is_mandatory(self):
        r = dfm.validate({"rules": {"min_track_width_mm": 0.2}})
        self.assertEqual(r["status"], "error")
        self.assertEqual(r["reason_code"], "SOURCE_REQUIRED")

    def test_blank_source_is_not_a_source(self):
        r = dfm.validate(cap({"min_track_width_mm": 0.2}, source="   "))
        self.assertEqual(r["reason_code"], "SOURCE_REQUIRED")

    def test_unknown_key_rejects_the_whole_document(self):
        # A typo must not read as "not stated", which would look like a pass.
        r = dfm.validate(cap({"min_track_wdith_mm": 0.2}))
        self.assertEqual(r["reason_code"], "CAPABILITY_KEY_UNKNOWN")
        self.assertEqual(r["unknown_rules"], ["min_track_wdith_mm"])
        self.assertIn("min_track_width_mm", [x["rule"] for x in r["known_rules"]])

    def test_empty_rules_is_an_error(self):
        r = dfm.validate(cap({}))
        self.assertEqual(r["reason_code"], "CAPABILITY_EMPTY")

    def test_non_positive_and_non_numeric_values_are_refused(self):
        r = dfm.validate(cap({"min_track_width_mm": 0,
                              "min_clearance_mm": "0.2"}))
        self.assertEqual(r["reason_code"], "CAPABILITY_VALUE_INVALID")
        bad = {b["rule"] for b in r["invalid_rules"]}
        self.assertEqual(bad, {"min_track_width_mm", "min_clearance_mm"})

    def test_partial_document_lists_what_is_unanswered(self):
        r = dfm.validate(cap({"min_track_width_mm": 0.2}))
        self.assertEqual(r["status"], "ok")
        v = r["value"]
        self.assertEqual(v["rules_stated"], 1)
        self.assertEqual(v["coverage_pct"],
                         round(100.0 / len(dfm.RULES), 1))
        stated = [x["rule"] for x in v["stated"]]
        self.assertEqual(stated, ["min_track_width_mm"])
        not_stated = {x["rule"] for x in v["not_stated"]}
        self.assertEqual(not_stated, set(dfm.RULES) - {"min_track_width_mm"})

    def test_unverified_document_says_so(self):
        r = dfm.validate(cap({"min_track_width_mm": 0.2}, verified=False))
        self.assertFalse(r["value"]["verified"])
        self.assertTrue(any("verified" in w for w in r["value"]["warnings"]))


class TestComparison(unittest.TestCase):
    """Boundary behaviour: equal passes, one micron short fails."""

    def _run(self, rules, facts):
        return dfm.evaluate(cap(rules), facts)

    def test_min_rule_on_the_limit_passes(self):
        r = self._run({"min_track_width_mm": 0.2},
                      {"narrowest_track": {"measured": 0.2, "where": "x"}})
        c = r["value"]["checks"][0]
        self.assertEqual(c["verdict"], "pass")
        self.assertAlmostEqual(c["margin"], 0.0, places=9)

    def test_min_rule_one_micron_short_fails(self):
        r = self._run({"min_track_width_mm": 0.2},
                      {"narrowest_track": {"measured": 0.199}})
        self.assertEqual(r["value"]["checks"][0]["verdict"], "fail")
        self.assertAlmostEqual(r["value"]["checks"][0]["margin"], -0.001, places=9)
        self.assertEqual(r["value"]["overall"], "fail")

    def test_max_rule_both_directions(self):
        r = self._run({"max_board_width_mm": 50.0},
                      {"board_width": {"measured": 50.0}})
        self.assertEqual(r["value"]["checks"][0]["verdict"], "pass")
        r = self._run({"max_board_width_mm": 50.0},
                      {"board_width": {"measured": 50.001}})
        self.assertEqual(r["value"]["checks"][0]["verdict"], "fail")

    def test_a_bound_is_not_a_measurement_but_can_still_decide(self):
        # Nothing was found inside the window, so the true value is >= bound.
        r = self._run({"min_copper_to_edge_mm": 0.3},
                      {"min_copper_to_edge": {"lower_bound": 5.0}})
        c = r["value"]["checks"][0]
        self.assertEqual(c["verdict"], "pass")
        self.assertEqual(c["lower_bound"], 5.0)
        self.assertNotIn("measured", c)

    def test_no_measurement_is_not_checked_never_pass(self):
        r = self._run({"min_via_drill_mm": 0.3},
                      {"min_via_drill": {"why": "adapter read no drills"}})
        self.assertEqual(r["value"]["checks"][0]["verdict"], "not_checked")
        self.assertEqual(r["value"]["overall"], "not_checked")
        self.assertTrue(any("could not be measured" in w for w in r["value"]["warnings"]))

    def test_not_applicable_is_distinct_from_not_checked(self):
        r = self._run({"min_slot_width_mm": 1.0},
                      {"min_slot_width": {"not_applicable": "no cut-out"}})
        v = r["value"]
        self.assertEqual(v["checks"][0]["verdict"], "not_applicable")
        self.assertEqual(v["not_applicable"], ["min_slot_width_mm"])
        self.assertEqual(v["not_checked"], [])

    def test_unstated_rules_are_listed_not_passed(self):
        r = self._run({"min_track_width_mm": 0.2},
                      {"narrowest_track": {"measured": 0.3}})
        v = r["value"]
        self.assertEqual(v["overall"], "pass")
        self.assertEqual(len(v["missing_known_rules"]), len(dfm.RULES) - 1)
        self.assertTrue(any("not stated" in w for w in v["warnings"]))


class TestBoardFacts(unittest.TestCase):
    """Every expected number is arithmetic on the fixture text."""

    def facts(self, **kw):
        rules, e = dfm.read_capability(cap(ALL_RULES))
        self.assertIsNone(e)
        return oracle._dfm_facts(board(), rules, **kw)

    def test_narrowest_track(self):
        self.assertAlmostEqual(self.facts()["narrowest_track"]["measured"], 0.2,
                               places=9)

    def test_via_drill_and_rings(self):
        f = self.facts()
        self.assertAlmostEqual(f["min_via_drill"]["measured"], 0.3, places=9)
        self.assertAlmostEqual(f["min_via_ring"]["measured"], 0.15, places=9)
        # THT pad: (min(1.0, 1.0) - 0.4) / 2
        self.assertAlmostEqual(f["min_pad_ring"]["measured"], 0.3, places=9)

    def test_hole_to_hole_web(self):
        # 1.0 mm between centres - 0.15 - 0.15
        self.assertAlmostEqual(self.facts()["min_hole_web"]["measured"], 0.7,
                               places=9)

    def test_copper_to_edge_subtracts_the_half_width(self):
        # centreline 1.0 mm from x=0, 0.25 mm wide -> 0.875 of copper-to-edge
        self.assertAlmostEqual(self.facts()["min_copper_to_edge"]["measured"], 0.875,
                               places=9)

    def test_clearance_gap(self):
        # pad U3.1 bottom edge y=14.5, track 1 top edge y=10.1
        self.assertAlmostEqual(self.facts()["min_copper_gap"]["measured"], 4.4,
                               places=6)

    def test_slot_throat(self):
        self.assertAlmostEqual(self.facts()["min_slot_width"]["measured"], 1.0,
                               places=9)

    def test_board_size_and_layer_count(self):
        f = self.facts()
        self.assertAlmostEqual(f["board_width"]["measured"], 50.0, places=6)
        self.assertAlmostEqual(f["board_height"]["measured"], 30.0, places=6)
        self.assertEqual(f["copper_layers"]["measured"], 2)

    def test_clearance_can_be_skipped_and_says_so(self):
        from pcb_agent_mcp.calc import dfm as _dfm
        rules, _ = _dfm.read_capability(cap(ALL_RULES))
        f = oracle._dfm_facts(board(), rules, check_clearance=False)
        self.assertIn("check_clearance=False", f["min_copper_gap"]["why"])


class TestDfmCheck(unittest.TestCase):
    """End to end: document + board -> verdict per rule."""

    def test_everything_within_capability_passes(self):
        r = oracle.dfm_check(board(), cap(ALL_RULES))
        self.assertEqual(r["status"], "ok")
        v = r["value"]
        self.assertEqual(v["overall"], "pass", v["checks"])
        self.assertEqual(v["failed"], 0)
        self.assertEqual(v["passed"], len(ALL_RULES))
        self.assertEqual(v["missing_known_rules"], [])
        self.assertNotIn("warnings", v)
        self.assertIn("facts", v)

    def test_one_rule_failed_names_it_and_the_margin(self):
        rules = dict(ALL_RULES, min_track_width_mm=0.25)
        r = oracle.dfm_check(board(), cap(rules))
        v = r["value"]
        self.assertEqual(v["overall"], "fail")
        self.assertEqual(v["failed_rules"], ["min_track_width_mm"])
        c = [c for c in v["checks"] if c["rule"] == "min_track_width_mm"][0]
        self.assertEqual(c["measured"], 0.2)
        self.assertAlmostEqual(c["margin"], -0.05, places=9)

    def test_unmeasurable_rule_is_reported_as_such(self):
        rules = {"min_copper_to_edge_mm": 0.3}
        r = oracle.dfm_check(board(), cap(rules), check_clearance=False)
        v = r["value"]
        self.assertEqual(v["overall"], "pass")
        self.assertEqual(v["missing_known_rules"][0]["rule"],
                         sorted(set(dfm.RULES) - set(rules))[0])

    def test_unknown_key_reaches_the_caller_as_an_error(self):
        r = oracle.dfm_check(board(), cap({"min_track_wdith_mm": 0.2}))
        self.assertEqual(r["status"], "error")
        self.assertEqual(r["reason_code"], "CAPABILITY_KEY_UNKNOWN")

    def test_derived_and_not_authoritative(self):
        # The tool wrapper, not the function, is what marks the answer computed.
        r = tools.call_tool("dfm_check", {"path": self._path(),
                                          "capability": cap(ALL_RULES)})
        self.assertEqual(r["status"], "ok")
        self.assertFalse(r["authoritative"])
        self.assertIn("board_drc", r["verdict_available_from"])

    def _path(self):
        path = os.path.join(tempfile.gettempdir(),
                            "dfm_tool_%d_%d.kicad_pcb" % (os.getpid(), next(_seq)))
        with open(path, "w", newline="") as fh:
            fh.write(BOARD)
        return path


class TestRegistration(unittest.TestCase):
    def test_both_tools_are_registered_and_self_described(self):
        names = [t["name"] for t in tools.list_tools()]
        self.assertIn("dfm_capability", names)
        self.assertIn("dfm_check", names)
        cap_out = tools.call_tool("calc_capabilities", {})
        self.assertIn("dfm_check", cap_out["available"])
        self.assertIn("dfm_capability", cap_out["available"])
        self.assertIn("dfm", cap_out["layers"])
        # The old stub line must be gone: "DFM checks (fabricator capability rules)"
        self.assertFalse(any(x == "DFM checks (fabricator capability rules)"
                             for x in cap_out["not_implemented_yet"]))


if __name__ == "__main__":
    unittest.main()
