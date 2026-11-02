# -*- coding: utf-8 -*-
"""0.0.82: mask-defined pads - an opening SMALLER than the copper under it.

Kept apart from test_dfm.py and test_dfm_silk_mask.py: this quantity needs a board
built to make one number appear, and it needs the case where the number is ZERO to
be a measurement rather than an absence ("no mask-defined pad" is the good answer,
not a missing one).

Fixture arithmetic (all millimetres, spelled out where it is used):

  * a pad's opening is the pad grown by its `solder_mask_margin`, so a NEGATIVE
    margin makes the opening smaller than the copper - the mask overlaps the pad
    by |margin|, and that overlap is what a fab has to hold lithographically.
  * two pads at x = 0 and x = 2, both 1 x 1, leave a 1.000 mm copper gap; the
    mask web between their openings is 1.000 - m1 - m2 (0.0.56's identity), so a
    negative margin makes that web BIGGER by |m|. Both directions are asserted
    here, because a sign error in one place would move both numbers the same way.

Every limit below is a made-up number for a made-up fab: the module under test
never invents a limit, so the test must not pretend one is real.
"""

import os
import sys
import unittest

sys.path.insert(0, os.path.join(os.path.dirname(os.path.abspath(__file__)), "..", "src"))

from pcb_agent_mcp import oracle                             # noqa: E402
from pcb_agent_mcp.adapters import kicad_pcb as K            # noqa: E402
from pcb_agent_mcp.calc import dfm                           # noqa: E402

_EDGE = ('(gr_rect (start 0 0) (end 50 30) (stroke (width 0.05) (type solid)) '
         '(layer "Edge.Cuts"))')

_SOURCE = "unit-test fixture - invented limits for an invented fab"


def _doc(body, setup=""):
    return ("(kicad_pcb (version 20221018) (generator pcbnew)\n"
            "  (general (thickness 1.6))\n"
            "  (net 0 \"\")\n  (net 1 \"A\")\n"
            + ("  (setup %s)\n" % setup if setup else "")
            + "  " + _EDGE + "\n  " + body + "\n)\n")


def _board(body, setup=""):
    return K.parse_board_text(_doc(body, setup))


def _cap(rules):
    return {"fab": "fixture fab", "source": _SOURCE, "verified": True,
            "rules": dict(rules)}


def _chk(value, rule):
    rows = [c for c in value["checks"] if c["rule"] == rule]
    assert len(rows) == 1, [c["rule"] for c in value["checks"]]
    return rows[0]


def _pad(number, x, margin=None, size=1.0, layers='"F.Cu" "F.Mask"', extra=""):
    m = "" if margin is None else " (solder_mask_margin %s)" % margin
    return ('    (pad "%s" smd rect (at %s 0) (size %s %s) (layers %s)%s%s)\n'
            % (number, x, size, size, layers, m, extra))


def _fp(pads, x=10, ref="J1", extra=""):
    return ('  (footprint "T:N" (layer "F.Cu") (at %s 10)\n'
            '    (property "Reference" "%s")%s\n%s  )'
            % (x, ref, extra, pads))


class TestTheCatalogue(unittest.TestCase):

    def test_the_rule_count_moved_to_seventeen(self):
        self.assertEqual(len(dfm.RULES), 17)

    def test_the_two_new_keys_name_their_facts_and_units(self):
        self.assertEqual(dfm.RULES["max_mask_defined_pads"]["fact"],
                         "mask_defined_pads")
        self.assertEqual(dfm.RULES["max_mask_defined_pads"]["direction"], "max")
        self.assertEqual(dfm.RULES["max_mask_defined_pads"]["unit"], "count")
        self.assertEqual(dfm.RULES["min_mask_defined_overlap_mm"]["fact"],
                         "min_mask_defined_overlap")
        self.assertEqual(dfm.RULES["min_mask_defined_overlap_mm"]["direction"],
                         "min")
        self.assertEqual(dfm.RULES["min_mask_defined_overlap_mm"]["unit"], "mm")

    def test_the_residual_list_still_says_what_is_missing_and_does_not_overclaim(self):
        self.assertEqual(len(dfm.NOT_CHECKED_BY_DESIGN), 8)
        joined = " ".join(dfm.NOT_CHECKED_BY_DESIGN).lower()
        # The claim that moved: a mask-defined pad is measured now.
        self.assertNotIn("mask-defined pads (an opening smaller", joined)
        # The claim that did NOT move: the film itself is still not read.
        self.assertIn("mask film itself is not read", joined)
        self.assertIn("state no line width", joined)
        self.assertIn("after plating", joined)

    def test_coverage_denominator_moved_with_the_rule_count(self):
        env = dfm.validate({"fab": "f", "source": "s", "verified": True,
                            "rules": {"min_track_width_mm": 0.2}})
        self.assertEqual(env["value"]["rules_known"], 17)
        self.assertEqual(env["value"]["coverage_pct"], 5.9)


class TestTheMeasurement(unittest.TestCase):

    def fact(self, body, setup="", rules=None):
        facts = oracle._dfm_facts(_board(body, setup),
                                  {"max_mask_defined_pads": 1.0}
                                  if rules is None else rules)
        return facts

    def test_one_negative_margin_is_one_pad_and_the_overlap_is_the_absolute_value(self):
        f = self.fact(_fp(_pad("1", 0, -0.05)))
        self.assertEqual(f["mask_defined_pads"]["measured"], 1.0)
        self.assertEqual(f["mask_defined_pads"]["widest_overlap_mm"], 0.05)
        self.assertEqual(f["mask_defined_pads"]["pads"],
                         [{"ref": "J1.1", "overlap_mm": 0.05,
                           "margin_source": "pad"}])
        self.assertEqual(f["min_mask_defined_overlap"]["measured"], 0.05)
        self.assertIn("J1.1", f["min_mask_defined_overlap"]["where"])

    def test_the_overlap_fact_is_the_SMALLEST_one_not_the_first_or_the_widest(self):
        body = _fp(_pad("1", 0, -0.05) + _pad("2", 2, -0.03))
        f = self.fact(body)
        self.assertEqual(f["mask_defined_pads"]["measured"], 2.0)
        self.assertEqual(f["mask_defined_pads"]["widest_overlap_mm"], 0.05)
        # sorted ascending, so the first entry is the tightest overlap
        self.assertEqual([p["overlap_mm"] for p in f["mask_defined_pads"]["pads"]],
                         [0.03, 0.05])
        self.assertEqual(f["min_mask_defined_overlap"]["measured"], 0.03)
        self.assertIn("J1.2", f["min_mask_defined_overlap"]["where"])

    def test_a_positive_margin_is_zero_pads_not_an_absence(self):
        f = self.fact(_fp(_pad("1", 0, 0.05)))
        self.assertEqual(f["mask_defined_pads"]["measured"], 0.0)
        self.assertEqual(f["mask_defined_pads"]["pads"], [])
        self.assertIn("at least as large as the copper",
                      f["mask_defined_pads"]["where"])
        self.assertIn("not_applicable", f["min_mask_defined_overlap"])

    def test_an_absent_margin_is_zero_and_says_so(self):
        f = self.fact(_fp(_pad("1", 0)))
        self.assertEqual(f["mask_defined_pads"]["measured"], 0.0)
        self.assertEqual(f["min_mask_defined_overlap"]["not_applicable"],
                         "no pad has its solder-mask opening smaller than its "
                         "copper, so there is no overlap to measure")

    def test_a_footprint_wide_margin_applies_to_every_pad_and_is_labelled(self):
        body = _fp(_pad("1", 0) + _pad("2", 2),
                   extra=" (solder_mask_margin -0.02)")
        f = self.fact(body)
        self.assertEqual(f["mask_defined_pads"]["measured"], 2.0)
        self.assertEqual(set(p["margin_source"] for p in f["mask_defined_pads"]["pads"]),
                         {"footprint"})
        self.assertEqual(f["min_mask_defined_overlap"]["measured"], 0.02)

    def test_a_setup_wide_negative_clearance_reaches_pads_that_state_nothing(self):
        # KiCad's own precedence: pad > footprint > (setup pad_to_mask_clearance).
        f = self.fact(_fp(_pad("1", 0)), setup="(pad_to_mask_clearance -0.04)")
        self.assertEqual(f["mask_defined_pads"]["measured"], 1.0)
        self.assertEqual(f["mask_defined_pads"]["pads"][0]["margin_source"], "setup")

    def test_the_pad_own_margin_wins_over_the_footprint_one(self):
        body = _fp(_pad("1", 0, 0.05), extra=" (solder_mask_margin -0.02)")
        f = self.fact(body)
        self.assertEqual(f["mask_defined_pads"]["measured"], 0.0)

    def test_a_pad_that_is_not_masked_is_not_counted(self):
        # Same negative margin, but the pad opens no mask: nothing is exposed
        # through the mask, so there is no mask-defined opening to speak of.
        f = self.fact(_fp(_pad("1", 0, -0.05, layers='"F.Cu"')))
        self.assertEqual(f["mask_defined_pads"]["measured"], 0.0)

    def test_a_pad_masked_on_both_sides_is_counted_once(self):
        f = self.fact(_fp(_pad("1", 0, -0.05, layers='"*.Cu" "*.Mask"')))
        self.assertEqual(f["mask_defined_pads"]["measured"], 1.0)

    def test_a_long_list_is_truncated_and_says_so(self):
        body = _fp("".join(_pad(str(i), 2.0 * i, -0.05) for i in range(45)),
                   x=0)
        f = self.fact(body)
        self.assertEqual(f["mask_defined_pads"]["measured"], 45.0)
        self.assertEqual(len(f["mask_defined_pads"]["pads"]), 40)
        self.assertTrue(f["mask_defined_pads"]["list_truncated"])

    def test_the_mask_web_moves_the_other_way_when_the_margin_goes_negative(self):
        # One identity, two signs: web = gap - m1 - m2. Pads 1 x 1 at x = 0 and
        # x = 2 leave a 1.000 mm gap; -0.05 / -0.03 widen the web to 1.080, and
        # +0.05 / +0.03 narrow it to 0.920. A sign error would flip both.
        neg = _fp(_pad("1", 0, -0.05) + _pad("2", 2, -0.03))
        pos = _fp(_pad("1", 0, 0.05) + _pad("2", 2, 0.03))
        b_neg = _board(neg)
        b_pos = _board(pos)
        w_neg = oracle._mask_web(oracle._mask_apertures(b_neg))
        w_pos = oracle._mask_web(oracle._mask_apertures(b_pos))
        self.assertAlmostEqual(w_neg["measured"], 1.08, places=6)
        self.assertAlmostEqual(w_pos["measured"], 0.92, places=6)


class TestTheVerdict(unittest.TestCase):

    def run_check(self, body, rules):
        return oracle.dfm_check(_board(body), _cap(rules))["value"]

    def test_a_fab_that_does_not_do_them_says_zero_and_two_pads_fail(self):
        body = _fp(_pad("1", 0, -0.05) + _pad("2", 2, -0.03))
        v = self.run_check(body, {"max_mask_defined_pads": 0})
        c = _chk(v, "max_mask_defined_pads")
        self.assertEqual(c["measured"], 2.0)
        self.assertEqual(c["verdict"], "fail")
        self.assertEqual(c["margin"], -2.0)
        self.assertEqual(v["overall"], "fail")

    def test_the_same_document_on_a_board_without_them_passes(self):
        v = self.run_check(_fp(_pad("1", 0, 0.05) + _pad("2", 2, 0.03)),
                           {"max_mask_defined_pads": 0})
        c = _chk(v, "max_mask_defined_pads")
        self.assertEqual(c["measured"], 0.0)
        self.assertEqual(c["verdict"], "pass")
        self.assertEqual(v["overall"], "pass")

    def test_the_overlap_limit_is_a_MINIMUM_so_less_than_it_fails(self):
        body = _fp(_pad("1", 0, -0.049) + _pad("2", 2, -0.05))
        v = self.run_check(body, {"min_mask_defined_overlap_mm": 0.05})
        c = _chk(v, "min_mask_defined_overlap_mm")
        self.assertEqual(c["measured"], 0.049)
        self.assertEqual(c["verdict"], "fail")
        self.assertEqual(c["margin"], -0.001)

    def test_the_overlap_limit_passes_at_the_limit(self):
        body = _fp(_pad("1", 0, -0.05) + _pad("2", 2, -0.06))
        v = self.run_check(body, {"min_mask_defined_overlap_mm": 0.05})
        self.assertEqual(_chk(v, "min_mask_defined_overlap_mm")["verdict"], "pass")

    def test_an_overlap_rule_alone_on_a_board_with_none_is_not_applicable(self):
        v = self.run_check(_fp(_pad("1", 0)), {"min_mask_defined_overlap_mm": 0.05})
        self.assertEqual(_chk(v, "min_mask_defined_overlap_mm")["verdict"],
                         "not_applicable")
        self.assertEqual(v["overall"], "not_checked")

    def test_stating_one_of_the_two_still_measures_the_other(self):
        # One pass produces both numbers, so the fact is there even when only one
        # rule asked for it - but the RULE that was not stated must not be
        # reported (it is not in the document).
        body = _fp(_pad("1", 0, -0.05))
        v = self.run_check(body, {"max_mask_defined_pads": 1})
        self.assertIn("min_mask_defined_overlap", v["facts"])
        self.assertEqual(v["facts"]["min_mask_defined_overlap"]["measured"], 0.05)
        self.assertNotIn("min_mask_defined_overlap_mm",
                         [c["rule"] for c in v["checks"]])


class TestTheGating(unittest.TestCase):

    def test_a_document_that_states_neither_rule_does_not_pretend_to_have_measured(self):
        b = _board(_fp(_pad("1", 0, -0.05)))
        facts = oracle._dfm_facts(b, {"min_track_width_mm": 0.2})
        self.assertIn("were not examined", facts["mask_defined_pads"]["why"])
        self.assertIn("were not examined", facts["min_mask_defined_overlap"]["why"])

    def test_the_reason_reaches_the_answer_when_the_document_asks_nothing(self):
        b = _board(_fp(_pad("1", 0, -0.05)))
        v = oracle.dfm_check(b, _cap({"min_track_width_mm": 0.05}))["value"]
        self.assertIn("were not examined", v["facts"]["mask_defined_pads"]["why"])
        self.assertIn("were not examined",
                      v["facts"]["min_mask_defined_overlap"]["why"])
        # And neither rule appears as checked, passed or failed.
        self.assertEqual([c for c in v["checks"]
                          if c["rule"].endswith("mask_defined_pads")], [])


class TestTheDocumentSide(unittest.TestCase):

    def test_a_maximum_of_zero_is_accepted_because_it_means_none_allowed(self):
        env = dfm.validate({"fab": "f", "source": "s",
                            "rules": {"max_mask_defined_pads": 0}})
        self.assertEqual(env["status"], "ok")
        self.assertEqual(env["value"]["rules_stated"], 1)

    def test_a_minimum_of_zero_is_still_refused(self):
        env = dfm.validate({"fab": "f", "source": "s",
                            "rules": {"min_mask_defined_overlap_mm": 0}})
        self.assertEqual(env["status"], "error")
        self.assertEqual(env["reason_code"], "CAPABILITY_VALUE_INVALID")
        self.assertIn("asks for nothing", env["invalid_rules"][0]["why"])

    def test_a_negative_limit_is_still_refused_even_on_a_maximum(self):
        env = dfm.validate({"fab": "f", "source": "s",
                            "rules": {"max_board_width_mm": -1}})
        self.assertEqual(env["status"], "error")
        self.assertEqual(env["reason_code"], "CAPABILITY_VALUE_INVALID")
        self.assertIn("points downward", env["invalid_rules"][0]["why"])

    def test_a_minimum_rule_at_zero_width_is_unaffected(self):
        env = dfm.validate({"fab": "f", "source": "s",
                            "rules": {"min_track_width_mm": 0}})
        self.assertEqual(env["status"], "error")


if __name__ == "__main__":
    unittest.main()
