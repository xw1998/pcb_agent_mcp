# -*- coding: utf-8 -*-
"""The corridor an equal-length bus needed in layout, as a graded rule.

`docs/197` section 4 states the rule in prose: a bus that has to be length-matched
needs its corridor reserved **in layout**, because a meander can only exist where
the air was left, and the BGA escape fence can never hold one. Prose is not a
rule until a number answers it, and the number may not be this module's own
arithmetic: every way of deriving a tooth's ceiling by hand produces a fake
"cannot be done" (see `route.tune_board.headroom`'s docstring). So the fact comes
from the tuner's own probe, and these tests pin the four states it can be in.

Fixture arithmetic (millimetres, exact, 50 x 30 outline at the origin):

  * `/DDR_DQS0P` runs (10,10)-(30,10) = 20.0 mm and `/DDR_DQS0N` runs
    (10,14)-(22,14) = 12.0 mm, so the shorter one owes 8.0 mm and its own copper
    can absorb 4.763 mm of it: `headroom` reports 3.237 mm short with the board to
    itself. That is the failure case, and the geometry - not a tolerance - is what
    makes it one.
  * the control is the same two nets at equal length (both 20.0 mm): nothing is
    owed, so nothing can be blocked, and the count is 0. Same measurement, same
    code, opposite answer - which is what separates "measured a failure" from
    "measured something".

The two name shapes are the board's own: `ddr_groups` reads the `/DDR_` tables
the tuner tunes by, and a `_P`/`_N` pair off that bus is added with the skew the
catalogue allows.
"""

import os
import sys
import unittest

_HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, os.path.join(os.path.dirname(_HERE), "src"))

from pcb_agent_mcp.adapters import kicad_pcb as K                   # noqa: E402
from pcb_agent_mcp.calc import rules as R                          # noqa: E402
from pcb_agent_mcp.ruleset import audit                            # noqa: E402
from pcb_agent_mcp.ruleset import measure as M                     # noqa: E402
from pcb_agent_mcp.ruleset import catalog as _catalog              # noqa: E402

RID = "layout.length_tuning_corridor"
FACT = "length_groups_blocked_by_geometry"

_HEAD = '''(kicad_pcb (version 20221018) (generator pcbnew)
  (general (thickness 1.6))
  (layers
    (0 "F.Cu" signal)
    (31 "B.Cu" signal)
    (44 "Edge.Cuts" user)
  )
  (net 0 "")
  (net 1 "GND")
  (net 2 "/DDR_DQS0P")
  (net 3 "/DDR_DQS0N")
  (net 4 "SIG_A")
  (net 5 "SIG_B")
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

def _seg(x0, y0, x1, y1, net, width=0.1):
    return ('  (segment (start %g %g) (end %g %g) (width %g) (layer "F.Cu") '
            '(net %d))\n' % (x0, y0, x1, y1, width, net))

def _unequal():
    """A DDR pair whose short member owes 8.0 mm and has 4.76 mm of room."""
    return _board(_seg(10, 10, 30, 10, 2) + _seg(10, 14, 22, 14, 3))

def _equal():
    """The same pair at equal length: nothing owed, nothing to be blocked by."""
    return _board(_seg(10, 10, 30, 10, 2) + _seg(10, 14, 30, 14, 3))

def _no_group():
    return _board(_seg(10, 10, 30, 10, 4) + _seg(10, 14, 30, 14, 5))

_PAIR_HEAD = _HEAD.replace('  (net 5 "SIG_B")\n',
                           '  (net 5 "SIG_B")\n  (net 6 "/USB_DP")\n  (net 7 "/USB_DN")\n')

def _off_bus_pair():
    """A by-name differential pair that is NOT on the /DDR_ tables.

    The delivered board has one of these (`/USB0_DP`/`/USB0_DN`) and it is short by
    0.254 mm by geometry (docs/204 section 4) - nothing asked it before, because
    `ddr_groups()` only names what the tuner would tune. The catalogue rule has to
    cover it too: a pair owes 0.127 mm of skew whether or not it is on the bus.
    """
    return K.parse_board_text(
        _PAIR_HEAD + _seg(10, 10, 30, 10, 6) + _seg(10, 14, 22, 14, 7) + "\n)\n",
        path="<memory>")

def _check(board, **kw):
    res = audit(board, **kw)
    return dict((c["id"], c) for c in res["levels"]["P3"]["checks"]).get(RID), res

class TestTheRuleItself(unittest.TestCase):

    def test_it_is_in_the_catalogue_at_p3_with_a_source(self):
        rules = _catalog()
        row = [r for r in (rules["rules"] if isinstance(rules, dict) else rules)
               if r["id"] == RID]
        self.assertEqual(len(row), 1)
        self.assertEqual(row[0]["level"], "P3")
        self.assertEqual(row[0]["fact"], FACT)
        self.assertEqual(row[0]["kind"], "count_max")
        self.assertEqual(row[0]["limit"], 0)
        self.assertTrue(row[0]["source"].strip())

    def test_the_pair_tolerance_is_the_one_the_skew_rule_states(self):
        # Two numbers under one name: if the catalogue's diff-pair skew changed
        # and this module kept its own copy, a group would be measured to one
        # tolerance and graded by another.
        skew = [r for r in R.LAYOUT_RULES if r["id"] == "layout.diffpair_skew"][0]
        self.assertAlmostEqual(M._DIFFPAIR_TOL_MM, float(skew["limit"]), places=9)

class TestWhatCountsAsAGroup(unittest.TestCase):

    def test_a_pair_recognised_by_name_off_the_bus_is_measured_too(self):
        groups = M._equal_length_groups(_off_bus_pair())
        names = [g.get("name") for g in groups]
        self.assertIn("pair /USB_DN//USB_DP", names)
        g = [x for x in groups if x.get("name") == "pair /USB_DN//USB_DP"][0]
        self.assertAlmostEqual(g["tol_mm"], M._DIFFPAIR_TOL_MM, places=9)
        # and the group reaches grading: same code path, count 1 -> fail
        check, _res = _check(_off_bus_pair(), facts={FACT: 1})
        self.assertEqual(check["state"], "fail")
        self.assertEqual(check["measured"], 1)

    def test_a_ddr_pair_is_not_counted_twice(self):
        # A pair already in `ddr_groups` must not be added again by the by-name
        # source: two groups over one net reports one shortfall as two failures.
        groups = M._equal_length_groups(_unequal())
        over = [g for g in groups if "/DDR_DQS0P" in (g.get("nets") or [])]
        self.assertEqual(len(over), 1)

class TestTheFourStates(unittest.TestCase):

    def test_no_equal_length_group_is_an_absent_object_not_a_missing_number(self):
        row = M._length_corridor(_no_group())
        self.assertIn("not_applicable", row[FACT])
        check, res = _check(_no_group())
        self.assertEqual(check["state"], "not_applicable")
        self.assertIn("no equal-length group", check["reason"])
        # The control that makes this a real statement: a board WITH a group has
        # no `not_applicable` anywhere, so the branch is not answering everything.
        row2 = M._length_corridor(_unequal())
        self.assertNotIn("not_applicable", row2[FACT])

    def test_off_by_default_and_the_refusal_names_its_cost_and_its_tool(self):
        row = M._length_corridor(_unequal())
        reason = row[FACT]["unmeasurable"]
        self.assertIn("board_length_tune", reason)
        # The cost is the ONE measured on RK3506 (docs/204 section 5): 8.1 s for
        # a 2-net pair, 394 s for the 47-net DDR bus. Asserting the number keeps
        # the refusal from drifting back to an estimate.
        self.assertIn("8.1 s", reason)
        self.assertIn("394 s", reason)
        self.assertIn(FACT, reason)
        check, _res = _check(_unequal())
        self.assertEqual(check["state"], "undetermined")
        self.assertIn("board_length_tune", check["reason"])

    def test_a_supplied_count_is_graded_both_ways(self):
        # 0 passes, 2 fails - the same code path, so the limit is doing the work.
        check, _res = _check(_unequal(), facts={FACT: 0})
        self.assertEqual(check["state"], "pass")
        self.assertEqual(check["measured"], 0)
        check, _res = _check(_unequal(), facts={FACT: 2})
        self.assertEqual(check["state"], "fail")
        self.assertEqual(check["measured"], 2)

    def test_the_probe_answers_and_its_answer_flips_with_the_geometry(self):
        short = M._length_corridor(_unequal(), headroom=True)[FACT]
        self.assertEqual(short["value"], 1.0)
        self.assertIn("/DDR_DQS0N", short["where"])
        self.assertGreater(short["examples"][0]["shortfall_mm"], 3.0)
        # Equal length, same measurement, nothing blocked.
        same = M._length_corridor(_equal(), headroom=True)[FACT]
        self.assertEqual(same["value"], 0.0)
        self.assertEqual(same["examples"], [])
        # And through the audit, where it lands on the failing net above the pass.
        check, res = _check(_unequal(), headroom=True)
        self.assertEqual(check["state"], "fail")
        self.assertEqual(res["levels"]["P3"]["status"], "flagged")
        self.assertIsNone(res["priority"]["blocking_level"])

class TestItIsAPriorityNotADecree(unittest.TestCase):

    def test_a_failing_corridor_never_blocks_a_build(self):
        _check_row, res = _check(_unequal(), facts={FACT: 1})
        self.assertIsNone(res["priority"]["blocking_level"])
        self.assertTrue(res["verdict"].startswith("buildable_flag_at_P3"),
                        res["verdict"])

    def test_the_reason_names_layout_not_another_tuning_run(self):
        rule = [r for r in R.LAYOUT_RULES if r["id"] == RID][0]
        self.assertIn("layout", rule["why"].lower())
        self.assertIn("per segment", rule["how_to_measure"].lower())

if __name__ == "__main__":
    unittest.main()
