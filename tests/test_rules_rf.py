# -*- coding: utf-8 -*-
"""The RF family, measured on the nets the file's own names call RF.

Six rules used to answer "this rule needs a fact the file does not state", which
is true of the ruleset and says nothing about the board. They are now measured
from the copper, on the nets whose NAMES say RF - and every number here has a
board built to make it appear plus a control that makes the same measurement
come out the other way, because "measured something" and "measured the right
thing" are different claims.

Fixture arithmetic (millimetres, exact, all on a 50 x 30 outline at the origin):

  * right angle: two segments meeting at (20,10) with directions (-1,0) and
    (0,1) turn the line by 90 deg. A 45 deg miter turns it by 45, and a
    collinear vertex turns it by 0 - that last one is the control that catches
    "the angle between the two vectors" being reported instead of its
    supplement, which counts every split track as a right angle.
  * bend radius: an arc whose three stored points are (18,20), (18.5858,18.5858)
    and (20,18) is a quarter circle of R = 2.0; the circumradius of those three
    points is 2.00005 at the given rounding.
  * spacing: two parallel 0.3 mm lines 0.5 mm centre-to-centre leave
    0.5 - 0.15 - 0.15 = 0.20 mm edge-to-edge.
  * edge: a 0.3 mm line centred 1.5 mm from the outline is 1.5 - 0.15 = 1.35 mm
    from it.
  * antenna: a 1 x 1 pad at y = 15 and a 0.3 mm line at y = 21 leave
    6.0 - 0.5 - 0.15 = 5.35 mm, and at y = 27 leave 11.35 mm.
"""

import os
import sys
import unittest

_HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, os.path.join(os.path.dirname(_HERE), "src"))

from pcb_agent_mcp.adapters import kicad_pcb as K                   # noqa: E402
from pcb_agent_mcp.ruleset import audit                            # noqa: E402
from pcb_agent_mcp.ruleset import measure as M                     # noqa: E402
from pcb_agent_mcp.calc import rules as R                          # noqa: E402

_HEAD = '''(kicad_pcb (version 20221018) (generator pcbnew)
  (general (thickness 1.6))
  (layers
    (0 "F.Cu" signal)
    (31 "B.Cu" signal)
    (44 "Edge.Cuts" user)
  )
  (net 0 "")
  (net 1 "GND")
  (net 2 "RF_IN")
  (net 3 "ANT_2G4")
  (net 4 "SIG")
  (net 5 "unconnected-(U5-ANT-Pad1)")
  (net 6 "BT_PWR")
  (net 7 "SIG_TX")
  (net_class "Default" ""
    (clearance 0.2) (trace_width 0.25) (via_dia 0.6) (via_drill 0.3)
  )
  (gr_line (start 0 0) (end 50 0) (layer "Edge.Cuts") (width 0.1))
  (gr_line (start 50 0) (end 50 30) (layer "Edge.Cuts") (width 0.1))
  (gr_line (start 50 30) (end 0 30) (layer "Edge.Cuts") (width 0.1))
  (gr_line (start 0 30) (end 0 0) (layer "Edge.Cuts") (width 0.1))
'''


def _board(body):
    return K.parse_board_text(_HEAD + body + "\n)\n", path="<memory>")


def _seg(x0, y0, x1, y1, net, width=0.3, layer="F.Cu"):
    return ('  (segment (start %g %g) (end %g %g) (width %g) (layer "%s") '
            '(net %d))\n' % (x0, y0, x1, y1, width, layer, net))


def _facts(board):
    m = M.measure(board)
    return m, M._rf_facts(board)


class TestWhichNetsAreRf(unittest.TestCase):

    def test_whole_tokens_only_and_the_auto_label_is_not_a_net(self):
        board = _board(_seg(10, 10, 20, 10, 2)      # RF_IN       -> RF
                       + _seg(10, 12, 20, 12, 3)    # ANT_2G4     -> RF + antenna
                       + _seg(10, 14, 20, 14, 5)    # unconnected-(...) -> not a net
                       + _seg(10, 16, 20, 16, 6)    # BT_PWR      -> not RF (BT)
                       + _seg(10, 18, 20, 18, 7)    # SIG_TX      -> not RF (TX)
                       + _seg(10, 20, 20, 20, 1))   # GND
        pop = M._rf_population(board)
        self.assertEqual(pop["nets"], ["ANT_2G4", "RF_IN"])
        self.assertEqual(pop["antenna"], ["ANT_2G4"])

    def test_band_spellings_are_tokens(self):
        head = _HEAD.replace('(net 4 "SIG")',
                             '(net 4 "SIG")\n  (net 8 "2G4_FEED")\n'
                             '  (net 9 "433M")')
        board = K.parse_board_text(head + _seg(10, 10, 20, 10, 8)
                                   + _seg(10, 12, 20, 12, 9) + "\n)\n",
                                   path="<memory>")
        self.assertEqual(M._rf_population(board)["nets"], ["2G4_FEED", "433M"])

    def test_a_board_with_no_rf_name_says_so_and_measures_nothing(self):
        m, f = _facts(_board(_seg(10, 10, 20, 10, 1)))
        self.assertEqual(sorted(f), sorted([
            "rf_line_z0_error_pct", "has_right_angle_bend_on_rf",
            "rf_min_bend_radius_mm", "rf_min_width_mm",
            "rf_min_spacing_to_other_trace_mm", "rf_min_distance_to_edge_mm",
            "antenna_min_distance_to_other_rf_mm"]))
        for key, row in f.items():
            self.assertIn("unmeasurable", row, key)
        # The sentence has to be actionable: name the fix, not just "no data".
        self.assertIn("RF_IN", f["rf_min_distance_to_edge_mm"]["unmeasurable"])
        self.assertIn("facts", f["rf_min_distance_to_edge_mm"]["unmeasurable"])
        # And nothing of the family reaches the catalogue, so no rule can pass.
        facts = M.catalog_facts(m)
        self.assertFalse([k for k in facts if "rf" in k or "antenna" in k])


class TestRightAngleBend(unittest.TestCase):

    def test_a_right_angle_is_found_with_its_corner(self):
        board = _board(_seg(10, 10, 20, 10, 2) + _seg(20, 10, 20, 20, 2))
        f = M._rf_facts(board)["has_right_angle_bend_on_rf"]
        self.assertIs(f["value"], True)
        ex = f["examples"][0]
        self.assertEqual(ex["net"], "RF_IN")
        self.assertEqual(ex["layer"], "F.Cu")
        self.assertAlmostEqual(ex["x_mm"], 20.0, places=3)
        self.assertAlmostEqual(abs(ex["y_mm"]), 10.0, places=3)
        self.assertAlmostEqual(ex["turn_deg"], 90.0, places=2)

    def test_a_45_degree_miter_is_not_a_right_angle(self):
        board = _board(_seg(10, 10, 20, 10, 2)
                       + _seg(20, 10, 21, 11, 2)
                       + _seg(21, 11, 21, 21, 2))
        f = M._rf_facts(board)["has_right_angle_bend_on_rf"]
        self.assertIs(f["value"], False)
        self.assertIn("45.0 deg", f["where"])
        self.assertNotIn("examples", f)

    def test_a_collinear_vertex_is_not_a_bend_at_all(self):
        # The control for the supplement: two segments of ONE straight line turn
        # the direction by 0 deg. Reporting the angle between the two outward
        # vectors instead gave 180 deg and failed every split track.
        board = _board(_seg(10, 10, 15, 10, 2) + _seg(15, 10, 20, 10, 2))
        f = M._rf_facts(board)["has_right_angle_bend_on_rf"]
        self.assertIs(f["value"], False)
        self.assertIn("sharpest turning 0.0 deg", f["where"])

    def test_an_rf_net_with_no_track_has_no_corner_to_grade(self):
        pad = ('  (footprint "T:P" (layer "F.Cu") (at 25 15)\n'
               '    (fp_text reference "J1" (at 0 -2) (layer "F.SilkS"))\n'
               '    (pad "1" smd rect (at 0 0) (size 1 1) '
               '(layers "F.Cu" "F.Paste" "F.Mask") (net 2 "RF_IN"))\n  )\n')
        f = M._rf_facts(_board(pad))["has_right_angle_bend_on_rf"]
        self.assertIn("unmeasurable", f)
        self.assertIn("no track", f["unmeasurable"])


class TestBendRadius(unittest.TestCase):

    def test_an_arc_gives_its_own_circumradius(self):
        arc = ('  (arc (start 18 20) (mid 18.5858 18.5858) (end 20 18) '
               '(width 0.3) (layer "F.Cu") (net 2))\n')
        board = _board(arc)
        f = M._rf_facts(board)["rf_min_bend_radius_mm"]
        self.assertAlmostEqual(f["value"], 2.0, delta=0.01)
        self.assertEqual(f["arc_tracks"], 1)
        self.assertIn("0.3 mm line", f["where"])

    def test_a_miter_has_no_radius_and_the_answer_says_where_to_look(self):
        board = _board(_seg(10, 10, 20, 10, 2) + _seg(20, 10, 20, 20, 2))
        f = M._rf_facts(board)["rf_min_bend_radius_mm"]
        self.assertIn("unmeasurable", f)
        self.assertIn("MITERS", f["unmeasurable"])
        self.assertIn("layout.rf_right_angle_bend", f["unmeasurable"])


class TestSpacingToOtherCopper(unittest.TestCase):

    def test_parallel_lines_report_the_gap_in_line_widths(self):
        board = _board(_seg(10, 10, 30, 10, 2)
                       + _seg(10, 10.5, 30, 10.5, 1))
        f = M._rf_facts(board)["rf_min_spacing_to_other_trace_mm"]
        self.assertAlmostEqual(f["value"], 0.2, delta=0.02)
        self.assertAlmostEqual(f["in_line_widths"], 0.2 / 0.3, delta=0.05)
        self.assertAlmostEqual(f["rf_min_width_mm"], 0.3, places=6)

    def test_copper_outside_the_window_is_a_bound_not_a_measurement(self):
        board = _board(_seg(10, 10, 30, 10, 2) + _seg(10, 25, 30, 25, 1))
        f = M._rf_facts(board)["rf_min_spacing_to_other_trace_mm"]
        self.assertNotIn("value", f)
        self.assertEqual(f["lower_bound"], 5.0)

    def test_a_neighbour_on_another_layer_is_not_a_neighbour(self):
        board = _board(_seg(10, 10, 30, 10, 2)
                       + _seg(10, 10.5, 30, 10.5, 1, layer="B.Cu"))
        f = M._rf_facts(board)["rf_min_spacing_to_other_trace_mm"]
        self.assertNotIn("value", f)
        self.assertEqual(f["lower_bound"], 5.0)


class TestDistanceToEdge(unittest.TestCase):

    def test_the_edge_distance_is_edge_to_edge(self):
        board = _board(_seg(5, 1.5, 25, 1.5, 2))
        f = M._rf_facts(board)["rf_min_distance_to_edge_mm"]
        self.assertAlmostEqual(f["value"], 1.35, delta=0.02)


class TestAntennaKeepout(unittest.TestCase):

    PAD = ('  (footprint "T:ANT" (layer "F.Cu") (at 25 15)\n'
           '    (fp_text reference "AE1" (at 0 -2) (layer "F.SilkS"))\n'
           '    (pad "1" smd rect (at 0 0) (size 1 1) '
           '(layers "F.Cu" "F.Paste" "F.Mask") (net 3 "ANT_2G4"))\n  )\n')

    def test_copper_6mm_away_measures_5_35(self):
        board = _board(self.PAD + _seg(10, 21, 40, 21, 1))
        f = M._rf_facts(board)["antenna_min_distance_to_other_rf_mm"]
        self.assertAlmostEqual(f["value"], 5.35, delta=0.05)

    def test_copper_further_away_measures_more(self):
        board = _board(self.PAD + _seg(10, 27, 40, 27, 1))
        f = M._rf_facts(board)["antenna_min_distance_to_other_rf_mm"]
        self.assertAlmostEqual(f["value"], 11.35, delta=0.05)

    def test_no_antenna_name_leaves_the_keepout_unmeasurable(self):
        board = _board(_seg(10, 10, 30, 10, 2))
        f = M._rf_facts(board)["antenna_min_distance_to_other_rf_mm"]
        self.assertIn("unmeasurable", f)
        self.assertIn("antenna", f["unmeasurable"])


class TestTheCatalogueGradesThem(unittest.TestCase):
    """A measurement is only worth having if a rule consumes it."""

    def _review(self, board):
        facts = M.catalog_facts(M.measure(board))
        return facts, R.review("layout", facts)["value"]

    def test_a_right_angle_fails_the_l0_rule_and_the_flag_stays_a_bool(self):
        facts, res = self._review(
            _board(_seg(10, 10, 20, 10, 2) + _seg(20, 10, 20, 20, 2)))
        self.assertIs(facts["has_right_angle_bend_on_rf"], True)
        self.assertEqual([c["id"] for c in res["fails"]],
                         ["layout.rf_right_angle_bend"])

    def test_a_miter_passes_it(self):
        facts, res = self._review(
            _board(_seg(10, 10, 20, 10, 2) + _seg(20, 10, 21, 11, 2)
                   + _seg(21, 11, 21, 21, 2)))
        self.assertIs(facts["has_right_angle_bend_on_rf"], False)
        self.assertIn("layout.rf_right_angle_bend",
                      [p["id"] for p in res["passes"]])
        self.assertNotIn("layout.rf_right_angle_bend",
                         [c["id"] for c in res["fails"]])

    def test_the_3w_limit_uses_the_rf_line_s_own_width(self):
        # 0.2 mm gap against 3 x 0.3 mm: fails by the RF width, and the limit
        # must be 0.9 rather than absent.
        facts, res = self._review(
            _board(_seg(10, 10, 30, 10, 2) + _seg(10, 10.5, 30, 10.5, 1)))
        self.assertAlmostEqual(facts["rf_line_width_mm"], 0.3, places=6)
        row = [c for c in res["fails"] if c["id"] == "layout.rf_spacing_3w"]
        self.assertEqual(len(row), 1)
        self.assertAlmostEqual(row[0]["limit"], 0.9, places=6)
        self.assertAlmostEqual(row[0]["measured"], 0.2, delta=0.02)

    def test_an_rf_line_hugging_the_edge_fails_the_edge_rule(self):
        facts, res = self._review(_board(_seg(5, 0.5, 25, 0.5, 3)))
        self.assertAlmostEqual(facts["rf_min_distance_to_edge_mm"], 0.35,
                               delta=0.02)
        row = [c for c in res["fails"] if c["id"] == "layout.rf_to_board_edge"]
        self.assertEqual(len(row), 1)
        self.assertEqual(row[0]["limit"], 3.0)

    def test_a_bound_only_ever_passes_and_never_becomes_a_failed_measurement(self):
        # Nothing else on the board within 5 mm of the RF line: the honest
        # answer is "at least 5 mm", the edge rule wants 3, so it can only pass.
        facts, res = self._review(_board(_seg(10, 10, 30, 10, 2)))
        self.assertEqual(facts["rf_min_distance_to_edge_mm"], 5.0)
        self.assertIn("layout.rf_to_board_edge",
                      [p["id"] for p in res["passes"]])

    def test_nothing_invents_an_impedance(self):
        # Z0 stays unmeasured: the target impedance is a design decision.
        facts, res = self._review(_board(_seg(10, 10, 30, 10, 2)))
        self.assertNotIn("rf_line_z0_error_pct", facts)
        self.assertIn("layout.rf_z0_tolerance",
                      [c["id"] for c in res["undetermined"]])
        self.assertEqual(res["unknown_facts_ignored"], [])


class TestAuditPlacesThemAtP3(unittest.TestCase):

    def test_the_six_rf_rules_are_p3_and_a_right_angle_is_reported_there(self):
        board = _board(_seg(10, 10, 20, 10, 2) + _seg(20, 10, 20, 20, 2))
        res = audit(board)
        p3 = dict((c["id"], c["state"]) for c in res["levels"]["P3"]["checks"])
        self.assertEqual(p3.get("layout.rf_right_angle_bend"), "fail")
        self.assertEqual(p3.get("layout.rf_to_board_edge"), "pass")
        # A P3 finding is a quality note, never "blocked".
        self.assertTrue(res["verdict"].startswith("buildable_flag_at_P3"),
                        res["verdict"])
        measured = res["measured"]
        self.assertIs(measured["has_right_angle_bend_on_rf"]["value"], True)


if __name__ == "__main__":
    unittest.main()
