# -*- coding: utf-8 -*-
"""0.0.56: silkscreen stroke/height, solder-mask web, copper to the FINISHED hole.

Kept apart from test_dfm.py on purpose. That file has one board and one set of
limits that every one of its assertions shares; these three quantities each need
a board built to make exactly one number appear, and each needs a NEGATIVE
CONTROL that makes the same number come out wrong. Mixing them into the shared
fixture would hide which assertion caught a regression.

Fixture arithmetic (all millimetres, and each one is spelled out where it is used):

  * silk: the width of a stroke is `(stroke (width x))` (pre-6 files write
    `(width x)` inline), and the character height is the SECOND number of
    `(size w h)` - not the first, which is the width of one character cell.
  * mask web: two openings on one side with copper gap g and expansions m1, m2
    leave g - m1 - m2 of mask between them. That identity is exact for a disc
    dilation, so no polygon is offset; a non-positive web means the openings
    MERGE.
  * copper to hole: dist(copper, hole centre) - drill/2 - plating.

Every limit below is a made-up number belonging to a made-up fab: the point of
the module under test is that it never invents a limit, so the test must not
pretend one is real.
"""

import os
import sys
import unittest

sys.path.insert(0, os.path.join(os.path.dirname(os.path.abspath(__file__)), "..", "src"))

from pcb_agent_mcp import oracle, tools                              # noqa: E402
from pcb_agent_mcp.adapters import kicad_pcb as K                   # noqa: E402
from pcb_agent_mcp.calc import dfm                                  # noqa: E402

_ABSENT = object()

# A 50 x 30 board. Only Edge.Cuts is mandatory; everything else is per test.
_EDGE = ('(gr_rect (start 0 0) (end 50 30) (stroke (width 0.05) (type solid)) '
         '(layer "Edge.Cuts"))')

_SOURCE = "unit-test fixture - invented limits for an invented fab"


def _doc(body, setup=""):
    return ("(kicad_pcb (version 20221018) (generator pcbnew)\n"
            "  (general (thickness 1.6))\n"
            "  (net 0 \"\")\n  (net 1 \"A\")\n  (net 2 \"B\")\n"
            + ("  (setup %s)\n" % setup if setup else "")
            + "  " + _EDGE + "\n  " + body + "\n)\n")


def _board(body, setup=""):
    return K.parse_board_text(_doc(body, setup))


def _check(body, rules, setup="", plating_um=_ABSENT):
    """(board, envelope) for a board built from `body` and a one-to-four rule doc."""
    cap = {"fab": "fixture fab", "source": _SOURCE, "verified": True,
           "rules": dict(rules)}
    if plating_um is not _ABSENT:
        cap["plating_um"] = plating_um
    board = _board(body, setup)
    return board, oracle.dfm_check(board, cap)


def _chk(value, rule):
    for c in value["checks"]:
        if c["rule"] == rule:
            return c
    raise AssertionError("rule %r missing from %s"
                         % (rule, [c["rule"] for c in value["checks"]]))


def _fact(value, fact):
    return value["facts"][fact]


# --------------------------------------------------------------- silk strokes
class TestSilkIsMeasured(unittest.TestCase):

    def test_board_level_line_sets_the_narrowest_stroke(self):
        body = ('(gr_line (start 5 5) (end 15 5) (stroke (width 0.12) (type solid)) '
                '(layer "F.SilkS"))')
        _b, env = _check(body, {"min_silk_line_width_mm": 0.10})
        c = _chk(env["value"], "min_silk_line_width_mm")
        self.assertEqual(c["measured"], 0.12)
        self.assertEqual(c["verdict"], "pass")
        self.assertIn("board-level gr_line on F.SilkS", c["where"])

    def test_character_height_is_the_second_size_number(self):
        # (size 0.6 1.8): 0.6 is the cell width, 1.8 is the glyph height. Reading
        # the first number would report 0.6 and fail a board that is fine.
        body = ('(gr_text "AB" (at 5 5) (layer "F.SilkS") '
                '(effects (font (size 0.6 1.8) (thickness 0.15))))')
        _b, env = _check(body, {"min_silk_text_height_mm": 1.5})
        c = _chk(env["value"], "min_silk_text_height_mm")
        self.assertEqual(c["measured"], 1.8)
        self.assertEqual(c["verdict"], "pass")

    def test_item_without_a_width_is_excluded_and_counted(self):
        # No (stroke ...) at all and no inline (width ...): the file does not say,
        # so this item must be excluded rather than defaulted - and the exclusion
        # must be visible, because the true minimum may be smaller.
        body = ('(gr_line (start 5 5) (end 15 5) (stroke (width 0.2) (type solid)) '
                '(layer "F.SilkS"))\n'
                '  (gr_line (start 5 7) (end 15 7) (layer "F.SilkS"))')
        _b, env = _check(body, {"min_silk_line_width_mm": 0.1})
        c = _chk(env["value"], "min_silk_line_width_mm")
        self.assertEqual(c["measured"], 0.2)
        self.assertIn("1 other silk item(s) state no line width", c["where"])
        self.assertIn("may be smaller", c["where"])

    def test_pre_six_inline_width_is_read(self):
        # KiCad 5 and earlier wrote (width x) directly on the graphic.
        body = ('(gr_line (start 5 5) (end 15 5) (layer "F.SilkS") (width 0.17))')
        _b, env = _check(body, {"min_silk_line_width_mm": 0.1})
        self.assertEqual(_chk(env["value"], "min_silk_line_width_mm")["measured"], 0.17)

    def test_no_silkscreen_at_all_is_not_applicable(self):
        _b, env = _check('', {"min_silk_line_width_mm": 0.1,
                             "min_silk_text_height_mm": 0.8})
        for rule in ("min_silk_line_width_mm", "min_silk_text_height_mm"):
            c = _chk(env["value"], rule)
            self.assertEqual(c["verdict"], "not_applicable")
            self.assertIn("no silkscreen item", c["note"])

    def test_graphics_without_text_make_the_height_not_applicable(self):
        body = ('(gr_line (start 5 5) (end 15 5) (stroke (width 0.15) (type solid)) '
                '(layer "F.SilkS"))')
        _b, env = _check(body, {"min_silk_text_height_mm": 0.8})
        c = _chk(env["value"], "min_silk_text_height_mm")
        self.assertEqual(c["verdict"], "not_applicable")
        self.assertIn("no silkscreen text", c["note"])

    def test_text_without_a_size_is_a_why_not_a_pass(self):
        body = '(gr_text "AB" (at 5 5) (layer "F.SilkS"))'
        _b, env = _check(body, {"min_silk_text_height_mm": 0.8})
        c = _chk(env["value"], "min_silk_text_height_mm")
        self.assertEqual(c["verdict"], "not_checked")
        self.assertIn("none states a character size", c["note"])

    def test_a_stroke_one_hundredth_under_the_limit_fails(self):
        narrow = ('(gr_line (start 5 5) (end 15 5) (stroke (width 0.11) (type solid)) '
                  '(layer "F.SilkS"))')
        exact = ('(gr_line (start 5 5) (end 15 5) (stroke (width 0.12) (type solid)) '
                 '(layer "F.SilkS"))')
        _b, env = _check(narrow, {"min_silk_line_width_mm": 0.12})
        self.assertEqual(_chk(env["value"], "min_silk_line_width_mm")["verdict"], "fail")
        _b, env = _check(exact, {"min_silk_line_width_mm": 0.12})
        self.assertEqual(_chk(env["value"], "min_silk_line_width_mm")["verdict"], "pass")

    def test_text_one_tenth_under_the_limit_fails(self):
        body = ('(gr_text "AB" (at 5 5) (layer "F.SilkS") '
                '(effects (font (size 1 1) (thickness 0.15))))')
        _b, env = _check(body, {"min_silk_text_height_mm": 1.1})
        c = _chk(env["value"], "min_silk_text_height_mm")
        self.assertEqual(c["measured"], 1.0)
        self.assertEqual(c["verdict"], "fail")

    def test_a_copper_graphic_never_enters_the_silk_facts(self):
        # Board-level graphics use the `gr_` prefix and so do board-level silk
        # items; only the LAYER decides. A `gr_line` on F.Cu at 0.05 mm must not
        # become the narrowest silk stroke.
        body = ('(gr_line (start 5 5) (end 15 5) (stroke (width 0.05) (type solid)) '
                '(layer "F.Cu"))')
        _b, env = _check(body, {"min_silk_line_width_mm": 0.1})
        c = _chk(env["value"], "min_silk_line_width_mm")
        self.assertEqual(c["verdict"], "not_applicable")
        self.assertIn("no silkscreen item", c["note"])

    def test_footprint_silk_is_included_and_names_its_owner(self):
        body = ('(footprint "T:S" (layer "F.Cu") (at 10 10)\n'
                '    (fp_text reference "U7" (at 0 -2) (layer "F.SilkS")\n'
                '      (effects (font (size 1 1) (thickness 0.15))))\n'
                '    (fp_line (start -1 -1) (end 1 -1) (stroke (width 0.09) (type solid))\n'
                '      (layer "F.SilkS"))\n'
                '    (pad "1" smd rect (at 0 0) (size 1 1) (layers "F.Cu") (net 1 "A")))')
        b, env = _check(body, {"min_silk_line_width_mm": 0.1})
        c = _chk(env["value"], "min_silk_line_width_mm")
        self.assertEqual(c["measured"], 0.09)
        self.assertIn("U7 fp_line on F.SilkS", c["where"])
        self.assertEqual(c["verdict"], "fail")
        self.assertTrue(b.present["silk"]["present"])


# ------------------------------------------------------------- solder-mask web
_TWO_PADS = ('  (footprint "T:P" (layer "F.Cu") (at 30 10)\n'
             '    (fp_text reference "U5" (at 0 -2) (layer "F.SilkS")\n'
             '      (effects (font (size 1 1) (thickness 0.15))))\n'
             '    (pad "1" smd rect (at -0.7 0) (size 1 1) (layers "F.Cu" "F.Mask")\n'
             '      (net 1 "A"))\n'
             '    (pad "2" smd rect (at 0.7 0) (size 1 1) (layers "F.Cu" "F.Mask")\n'
             '      (net 1 "A")))')


class TestMaskWeb(unittest.TestCase):

    def test_two_openings_sharing_the_setup_expansion(self):
        # 1.4 mm pitch, 1.0 mm pads -> copper gap 0.400; both grow by 0.050 -> 0.300.
        _b, env = _check(_TWO_PADS, {"min_mask_web_mm": 0.25},
                         setup="(pad_to_mask_clearance 0.05)")
        c = _chk(env["value"], "min_mask_web_mm")
        self.assertEqual(c["measured"], 0.3)
        self.assertEqual(c["verdict"], "pass")
        self.assertIn("U5.1 vs U5.2 on F.Mask", c["where"])

    def test_no_expansion_anywhere_leaves_the_copper_gap(self):
        _b, env = _check(_TWO_PADS, {"min_mask_web_mm": 0.25})
        c = _chk(env["value"], "min_mask_web_mm")
        self.assertEqual(c["measured"], 0.4)

    def test_the_absent_expansion_is_recorded_as_absent(self):
        b = _board(_TWO_PADS)
        pads = b.footprints[0]["pads"]
        self.assertEqual(pads[0]["mask_margin"], 0.0)
        self.assertEqual(pads[0]["mask_margin_source"], "absent_treated_as_zero")
        self.assertEqual(b.present["pad_to_mask"]["expansion"]["pads_from"],
                         {"absent_treated_as_zero": 2})

    def test_a_pad_level_margin_wins_over_the_setup_default(self):
        # 0.400 - 0.200 (pad 1) - 0.050 (setup) = 0.150
        body = ('(footprint "T:P" (layer "F.Cu") (at 30 10)\n'
                '    (fp_text reference "U6" (at 0 -2) (layer "F.SilkS")\n'
                '      (effects (font (size 1 1) (thickness 0.15))))\n'
                '    (pad "1" smd rect (at -0.7 0) (size 1 1) (layers "F.Cu" "F.Mask")\n'
                '      (solder_mask_margin 0.2) (net 1 "A"))\n'
                '    (pad "2" smd rect (at 0.7 0) (size 1 1) (layers "F.Cu" "F.Mask")\n'
                '      (net 1 "A")))')
        b, env = _check(body, {"min_mask_web_mm": 0.1},
                        setup="(pad_to_mask_clearance 0.05)")
        c = _chk(env["value"], "min_mask_web_mm")
        self.assertEqual(c["measured"], 0.15)
        self.assertEqual(round(c["measured"] - 0.1, 6), 0.05)
        srcs = b.present["pad_to_mask"]["expansion"]["pads_from"]
        self.assertEqual(srcs, {"pad": 1, "setup": 1})

    def test_a_footprint_level_margin_beats_the_setup_default(self):
        # 0.400 - 0.150 - 0.150 = 0.100 (no setup block at all)
        body = ('(footprint "T:P" (layer "F.Cu") (at 30 10)\n'
                '    (solder_mask_margin 0.15)\n'
                '    (fp_text reference "U8" (at 0 -2) (layer "F.SilkS")\n'
                '      (effects (font (size 1 1) (thickness 0.15))))\n'
                '    (pad "1" smd rect (at -0.7 0) (size 1 1) (layers "F.Cu" "F.Mask")\n'
                '      (net 1 "A"))\n'
                '    (pad "2" smd rect (at 0.7 0) (size 1 1) (layers "F.Cu" "F.Mask")\n'
                '      (net 1 "A")))')
        b, env = _check(body, {"min_mask_web_mm": 0.05})
        c = _chk(env["value"], "min_mask_web_mm")
        self.assertEqual(c["measured"], 0.1)
        self.assertEqual(b.present["pad_to_mask"]["expansion"]["pads_from"],
                         {"footprint": 2})

    def test_one_opening_on_a_side_is_not_applicable(self):
        body = ('(footprint "T:P" (layer "F.Cu") (at 30 10)\n'
                '    (pad "1" smd rect (at 0 0) (size 1 1) (layers "F.Cu" "F.Mask")\n'
                '      (net 1 "A")))')
        _b, env = _check(body, {"min_mask_web_mm": 0.25},
                         setup="(pad_to_mask_clearance 0.05)")
        c = _chk(env["value"], "min_mask_web_mm")
        self.assertEqual(c["verdict"], "not_applicable")
        self.assertIn("fewer than two", c["note"])

    def test_openings_that_merge_are_counted_not_reported_as_a_zero_web(self):
        # 1.0 mm pitch, 1.0 mm pads -> copper gap 0, so the two openings overlap
        # by 0.100: there is no mask between them. Saying "0 mm web" would read
        # as "a web too thin to make", which is a different (wrong) verdict.
        body = ('(footprint "T:P" (layer "F.Cu") (at 30 10)\n'
                '    (pad "1" smd rect (at -0.5 0) (size 1 1) (layers "F.Cu" "F.Mask")\n'
                '      (net 1 "A"))\n'
                '    (pad "2" smd rect (at 0.5 0) (size 1 1) (layers "F.Cu" "F.Mask")\n'
                '      (net 1 "A")))')
        _b, env = _check(body, {"min_mask_web_mm": 0.25},
                         setup="(pad_to_mask_clearance 0.05)")
        c = _chk(env["value"], "min_mask_web_mm")
        self.assertEqual(c["verdict"], "not_applicable")
        self.assertIn("merges", c["note"])

    def test_a_web_below_the_limit_fails(self):
        _b, env = _check(_TWO_PADS, {"min_mask_web_mm": 0.31},
                         setup="(pad_to_mask_clearance 0.05)")
        c = _chk(env["value"], "min_mask_web_mm")
        self.assertEqual(c["measured"], 0.3)
        self.assertEqual(c["verdict"], "fail")

    def test_the_two_sides_are_never_paired_across_the_board(self):
        # One opening on F.Mask and one on B.Mask at 1.4 mm apart in x. Pairing
        # them would report a 0.3 mm web; in reality they are separated by the
        # whole board thickness and there is no web to speak of.
        body = ('(footprint "T:P" (layer "F.Cu") (at 30 10)\n'
                '    (pad "1" smd rect (at -0.7 0) (size 1 1) (layers "F.Cu" "F.Mask")\n'
                '      (net 1 "A"))\n'
                '    (pad "2" smd rect (at 0.7 0) (size 1 1) (layers "B.Cu" "B.Mask")\n'
                '      (net 1 "A")))')
        _b, env = _check(body, {"min_mask_web_mm": 0.25},
                         setup="(pad_to_mask_clearance 0.05)")
        c = _chk(env["value"], "min_mask_web_mm")
        self.assertEqual(c["verdict"], "not_applicable")
        self.assertIn("fewer than two", c["note"])

    def test_a_mask_graphic_is_an_opening_with_no_expansion_added(self):
        # Pad copper 9.5..10.5; drawn opening 10.65..10.85 -> copper gap 0.150.
        # The graphic is the opening itself, so only the pad's 0.050 is subtracted
        # -> 0.100. (KiCad applies pad_to_mask_clearance to pads only.)
        body = ('(footprint "T:P" (layer "F.Cu") (at 20 10)\n'
                '    (fp_text reference "U9" (at 0 -2) (layer "F.SilkS")\n'
                '      (effects (font (size 1 1) (thickness 0.15))))\n'
                '    (pad "1" smd rect (at 0 0) (size 1 1) (layers "F.Cu" "F.Mask")\n'
                '      (net 1 "A")))\n'
                '  (gr_rect (start 20.65 9.5) (end 20.85 10.5) (layer "F.Mask"))')
        _b, env = _check(body, {"min_mask_web_mm": 0.05},
                         setup="(pad_to_mask_clearance 0.05)")
        c = _chk(env["value"], "min_mask_web_mm")
        self.assertEqual(c["measured"], 0.1)
        self.assertIn("board mask graphic", c["where"])
        self.assertIn("0.0500 - 0.0000", c["where"])


# ------------------------------------------- copper to the finished hole wall
# One THT pad (net A) at (20,10), drill 0.6, and one net-B track at x = 21.5:
#   point-to-segment distance 1.5, minus half the track (0.100) = 1.400,
#   minus the drill radius (0.300) = 1.100, minus the plating.
_HOLE_AND_TRACK = ('  (footprint "T:H" (layer "F.Cu") (at 20 10)\n'
                   '    (pad "1" thru_hole circle (at 0 0) (size 1.2 1.2) (drill 0.6)\n'
                   '      (layers "*.Cu" "*.Mask") (net 1 "A")))\n'
                   '  (segment (start 21.5 8) (end 21.5 12) (width 0.2) (layer "F.Cu")\n'
                   '    (net 2 "B"))')


class TestCopperToFinishedHole(unittest.TestCase):

    def test_no_plating_measures_to_the_drill_wall(self):
        _b, env = _check(_HOLE_AND_TRACK, {"min_copper_to_hole_mm": 1.05},
                         plating_um=0)
        c = _chk(env["value"], "min_copper_to_hole_mm")
        self.assertEqual(c["measured"], 1.1)
        self.assertEqual(c["verdict"], "pass")

    def test_one_hundred_microns_of_plating_removes_one_tenth(self):
        _b, env = _check(_HOLE_AND_TRACK, {"min_copper_to_hole_mm": 1.05},
                         plating_um=100)
        c = _chk(env["value"], "min_copper_to_hole_mm")
        self.assertEqual(c["measured"], 1.0)
        self.assertEqual(c["verdict"], "fail")       # same board, plating decides

    def test_a_document_without_plating_um_is_refused_not_measured(self):
        _b, env = _check(_HOLE_AND_TRACK, {"min_copper_to_hole_mm": 0.1})
        c = _chk(env["value"], "min_copper_to_hole_mm")
        self.assertEqual(c["verdict"], "not_checked")
        self.assertIn("plating_um", c["note"])
        self.assertIn("different question", c["note"])

    def test_a_negative_plating_is_rejected(self):
        _b, env = _check(_HOLE_AND_TRACK, {"min_copper_to_hole_mm": 0.1},
                         plating_um=-1)
        self.assertEqual(env["status"], "error")
        self.assertEqual(env["reason_code"], "CAPABILITY_VALUE_INVALID")

    def test_a_non_numeric_plating_is_rejected(self):
        _b, env = _check(_HOLE_AND_TRACK, {"min_copper_to_hole_mm": 0.1},
                         plating_um="25")
        self.assertEqual(env["status"], "error")
        self.assertEqual(env["reason_code"], "CAPABILITY_VALUE_INVALID")

    def test_an_isolated_hole_gives_a_proven_lower_bound(self):
        body = ('  (footprint "T:H" (layer "F.Cu") (at 20 10)\n'
                '    (pad "1" thru_hole circle (at 0 0) (size 1.2 1.2) (drill 0.6)\n'
                '      (layers "*.Cu" "*.Mask") (net 1 "A")))')
        _b, env = _check(body, {"min_copper_to_hole_mm": 0.5}, plating_um=25)
        c = _chk(env["value"], "min_copper_to_hole_mm")
        self.assertEqual(c["lower_bound"], 5.0)
        self.assertEqual(c["verdict"], "pass")

    def test_the_owning_pad_is_its_ring_not_a_clearance(self):
        # A 1.2 mm pad on a 0.6 mm hole has a 0.3 mm ring; measured as a clearance
        # that would read "0.3 mm of copper 0.0 mm from the wall" and fail every
        # through-hole board. The owning pad must be excluded.
        body = ('  (footprint "T:H" (layer "F.Cu") (at 20 10)\n'
                '    (pad "1" thru_hole circle (at 0 0) (size 1.2 1.2) (drill 0.6)\n'
                '      (layers "*.Cu" "*.Mask") (net 1 "A")))')
        _b, env = _check(body, {"min_copper_to_hole_mm": 0.9}, plating_um=25)
        c = _chk(env["value"], "min_copper_to_hole_mm")
        self.assertEqual(c["verdict"], "pass")
        self.assertIn("lower_bound", c)

    def test_a_same_net_trace_touching_its_via_is_a_connection(self):
        body = ('  (via (at 20 10) (size 0.6) (drill 0.3) (layers "F.Cu" "B.Cu")\n'
                '    (net 1 "A"))\n'
                '  (segment (start 20 10) (end 10 10) (width 0.2) (layer "F.Cu")\n'
                '    (net 1 "A"))')
        _b, env = _check(body, {"min_copper_to_hole_mm": 0.9}, plating_um=25)
        c = _chk(env["value"], "min_copper_to_hole_mm")
        self.assertEqual(c["verdict"], "pass")
        self.assertIn("lower_bound", c)

    def test_an_npth_hole_still_counts_another_nets_copper(self):
        # NPTH drill 0.5 at (14,10); net-B track at x = 14.75, width 0.2:
        #   0.75 - 0.100 (half track) = 0.650, - 0.250 (radius) = 0.400,
        #   - 0.025 (25 um plating) = 0.375
        body = ('  (footprint "T:N" (layer "F.Cu") (at 14 10)\n'
                '    (pad "1" np_thru_hole circle (at 0 0) (size 0.5 0.5) (drill 0.5)\n'
                '      (layers "*.Mask")))\n'
                '  (segment (start 14.75 8) (end 14.75 12) (width 0.2) (layer "F.Cu")\n'
                '    (net 2 "B"))')
        _b, env = _check(body, {"min_copper_to_hole_mm": 0.3}, plating_um=25)
        c = _chk(env["value"], "min_copper_to_hole_mm")
        self.assertEqual(c["measured"], 0.375)
        self.assertEqual(c["verdict"], "pass")


# ------------------------------------------------------------------- catalogue
class TestTheCatalogue(unittest.TestCase):

    def test_the_rule_count_is_fifteen_and_the_new_keys_name_their_facts(self):
        self.assertEqual(len(dfm.RULES), 15)
        for key, fact in (("min_silk_line_width_mm", "min_silk_line_width"),
                          ("min_silk_text_height_mm", "min_silk_text_height"),
                          ("min_mask_web_mm", "min_mask_web"),
                          ("min_copper_to_hole_mm", "min_copper_to_hole")):
            self.assertEqual(dfm.RULES[key]["fact"], fact)
            self.assertEqual(dfm.RULES[key]["direction"], "min")

    def test_the_residual_list_did_not_shrink_by_deleting_the_hard_parts(self):
        # 0.0.56 narrowed three residual entries instead of dropping them; the
        # count stays 8 so docs/README.md's "8 类" stays true, and none of the
        # three may still claim the quantity is not measured at all.
        self.assertEqual(len(dfm.NOT_CHECKED_BY_DESIGN), 8)
        joined = " ".join(dfm.NOT_CHECKED_BY_DESIGN).lower()
        self.assertIn("mask-defined pads", joined)
        self.assertIn("state no line width", joined)
        self.assertIn("after plating", joined)

    def test_read_plating_converts_microns_to_millimetres(self):
        self.assertEqual(dfm.read_plating({"plating_um": 25}), (0.025, None))
        self.assertEqual(dfm.read_plating({"plating_um": 0}), (0.0, None))
        self.assertEqual(dfm.read_plating({}), (None, None))
        self.assertEqual(dfm.read_plating({"plating_um": None}), (None, None))

    def test_coverage_denominator_moved_with_the_rule_count(self):
        env = dfm.validate({"fab": "f", "source": "s", "verified": True,
                            "rules": {"min_track_width_mm": 0.2}})
        self.assertEqual(env["value"]["rules_known"], 15)
        self.assertEqual(env["value"]["coverage_pct"], 6.7)


# -------------------------------------------------------------- registration
class TestRegistration(unittest.TestCase):

    def _desc(self, name):
        for t in tools.list_tools():
            if t["name"] == name:
                return t["description"]
        raise AssertionError("tool %r is not registered" % name)

    def test_dfm_check_says_what_it_measures_now(self):
        d = self._desc("dfm_check")
        self.assertIn("plating_um", d)
        self.assertIn("silkscreen", d)
        self.assertIn("FINISHED hole wall", d)

    def test_dfm_capability_documents_the_plating_key(self):
        self.assertIn("plating_um", self._desc("dfm_capability"))

    def test_calc_capabilities_credits_the_release(self):
        out = tools.call_tool("calc_capabilities", {})
        dfm_lines = [s for s in out["not_implemented_yet"]
                     if "DFM rules the fab states" in s]
        self.assertEqual(len(dfm_lines), 1)
        self.assertIn("0.0.56", dfm_lines[0])


if __name__ == "__main__":
    unittest.main()
