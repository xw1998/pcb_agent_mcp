# -*- coding: utf-8 -*-
"""Reverse controls for `aesthetics` - ordering two boards that are both legal.

The module's claim is that a handful of readings about the SHAPE of the copper can
say which of two compliant boards is the tidier one. That claim is worth nothing
unless the readings can be made to move for the right reason and to stay put for
the wrong one, so every test below has both halves:

  * a staircase must read worse than a straight run, and a 20 mm run stored as two
    10 mm pieces must read the same bends but a different `collinear` count;
  * reversing every segment, or moving the whole board 50 mm, must change NOTHING
    - a reading that depends on which end of a track was written first is not
    measuring the copper;
  * `compare` must return `incomparable` with the disagreeing readings named
    rather than inventing a winner, must call out the density confound, and must
    refuse to order fewer than two boards instead of ranking one.

Numbers in the assertions are worked out by hand from the geometry in the same
test, never copied from a run.
"""
import os
import sys
import tempfile
import unittest

sys.path.insert(0, os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "src"))

from pcb_agent_mcp import aesthetics  # noqa: E402
from pcb_agent_mcp.adapters import kicad_pcb  # noqa: E402

STACKUP = """(setup (stackup (layer "F.Cu" (type "copper") (thickness 0.035))
    (layer "dielectric 1" (type "core") (thickness 0.2) (epsilon_r 4.3))
    (layer "B.Cu" (type "copper") (thickness 0.035))))"""


def board_text(tracks="", pads="", zones="", size=120):
    return """(kicad_pcb (version 20221018) %s
      (net 0 "") (net 1 "SIG") (net 2 "GND") (net 3 "V3")
      (gr_rect (start 0 0) (end %d %d) (layer "Edge.Cuts") (width 0.1) (fill none))
%s%s%s
    )""" % (STACKUP, size, size, tracks, pads, zones)


def pad_footprint(ref, x, y, net=1, name="SIG"):
    return ('  (footprint "T:P" (layer "F.Cu") (at %g %g)\n'
            '    (fp_text reference "%s" (at 0 0) (layer "F.SilkS"))\n'
            '    (pad "1" smd rect (at 0 0) (size 1 1) (layers "F.Cu") '
            '(net %d "%s")))\n' % (x, y, ref, net, name))


def seg(x0, y0, x1, y1, net=1, layer="F.Cu", width=0.25):
    return ('  (segment (start %g %g) (end %g %g) (width %g) (layer "%s") '
            '(net %d))\n' % (x0, y0, x1, y1, width, layer, net))


def via(x, y, net=1):
    return ('  (via (at %g %g) (size 0.6) (drill 0.3) (layers "F.Cu" "B.Cu") '
            '(net %d))\n' % (x, y, net))


class Base(unittest.TestCase):
    def setUp(self):
        self.dir = tempfile.mkdtemp(prefix="aesthet_")

    def board(self, tracks="", pads="", zones="", size=120):
        path = os.path.join(self.dir, "b%d.kicad_pcb" % len(os.listdir(self.dir)))
        with open(path, "w", encoding="utf-8") as fh:
            fh.write(board_text(tracks, pads, zones, size))
        return kicad_pcb.parse_board(path)

    def value(self, board, key):
        return (aesthetics.readings(board).get(key) or {}).get("value")


# --------------------------------------------------------------------------- #
# the readings move for the right reason
# --------------------------------------------------------------------------- #
class TestReadings(Base):
    def test_a_straight_run_has_no_bends_and_long_pieces(self):
        # 40 mm of SIG as one piece: no joint at all, so no bend and no stitch
        b = self.board(seg(10, 10, 50, 10))
        got = aesthetics.readings(b)
        self.assertEqual(got["bends_per_100mm"]["value"], 0.0)
        self.assertEqual(got["collinear_per_100mm"]["value"], 0.0)
        self.assertAlmostEqual(got["chunk_median_mm"]["value"], 40.0, places=3)
        self.assertEqual(got["long_seg_share"]["value"], 1.0)
        self.assertEqual(got["joints"]["dangling_ends"], 2)

    def test_a_staircase_reads_worse_than_a_straight_run(self):
        # same 40 mm of copper, same net: one piece vs four 45-degree doglegs
        straight = seg(10, 10, 50, 10)
        zig = (seg(10, 10, 10, 20) + seg(10, 20, 20, 30) + seg(20, 30, 20, 40)
               + seg(20, 40, 30, 50))
        b1, b2 = self.board(straight), self.board(zig)
        self.assertGreater(self.value(b2, "bends_per_100mm"),
                           self.value(b1, "bends_per_100mm"))
        self.assertLess(self.value(b2, "chunk_median_mm"),
                        self.value(b1, "chunk_median_mm"))
        self.assertGreater(self.value(b2, "long_seg_share"),
                           0.0)  # 10 mm pieces are still "long"
        # and the direction each reading declares must point that way
        rows = aesthetics.readings(b2)
        self.assertEqual(rows["bends_per_100mm"]["better_when"], "lower")
        self.assertEqual(rows["chunk_median_mm"]["better_when"], "higher")

    def test_the_turn_mix_separates_a_chamfer_from_a_square_turn(self):
        # 45-degree chamfer (H then 45) vs square turn (H then V)
        chamfer = seg(10, 10, 30, 10) + seg(30, 10, 40, 20)
        square = seg(10, 40, 30, 40) + seg(30, 40, 30, 60)
        m1 = aesthetics.readings(self.board(chamfer))["turn_mix"]
        m2 = aesthetics.readings(self.board(square))["turn_mix"]
        self.assertEqual(m1["change_45"], 1)
        self.assertEqual(m1["change_90"], 0)
        self.assertEqual(m2["change_90"], 1)
        self.assertEqual(m2["change_45"], 0)

    def test_one_run_stored_as_two_pieces_is_a_stitch_not_a_bend(self):
        one = seg(10, 10, 30, 10)
        two = seg(10, 10, 20, 10) + seg(20, 10, 30, 10)
        b1, b2 = self.board(one), self.board(two)
        r1, r2 = aesthetics.readings(b1), aesthetics.readings(b2)
        self.assertEqual(r1["collinear_per_100mm"]["value"], 0.0)
        self.assertEqual(r2["collinear_per_100mm"]["count"], 1)
        # the split adds no direction change: this is waste, not ugliness
        self.assertEqual(r2["bends_per_100mm"]["value"], 0.0)
        self.assertLess(r2["chunk_median_mm"]["value"],
                        r1["chunk_median_mm"]["value"])

    def test_a_tee_is_not_counted_as_a_bend(self):
        # a 90-degree branch off a trunk is correct routing; counting it as a
        # square corner is how a good board reads as a bad one
        trunk = seg(10, 10, 50, 10)
        branch = seg(30, 10, 30, 40)
        got = aesthetics.readings(self.board(trunk + branch))
        self.assertEqual(got["joints"]["tees_or_stars"], 1)
        self.assertEqual(got["bends_per_100mm"]["value"], 0.0)

    def test_two_nets_side_by_side_are_a_bundle_until_they_are_not(self):
        near = seg(10, 10, 50, 10, net=1) + seg(10, 10.3, 50, 10.3, net=2)
        far = seg(10, 10, 50, 10, net=1) + seg(10, 12.0, 50, 12.0, net=2)
        self.assertGreater(self.value(self.board(near), "bundle_share"), 0.9)
        self.assertEqual(self.value(self.board(far), "bundle_share"), 0.0)

    def test_a_meander_of_one_net_is_not_its_own_bundle(self):
        # same net twice: a serpentine's own teeth must not count as a bundle
        teeth = seg(10, 10, 30, 10, net=1) + seg(30, 10.4, 10, 10.4, net=1)
        self.assertEqual(self.value(self.board(teeth), "bundle_share"), 0.0)

    def test_copper_free_board_says_so_instead_of_reading_zero(self):
        got = aesthetics.readings(self.board(""))
        self.assertIn("why_nothing_measured", got)
        self.assertNotIn("bends_per_100mm", got)

    def test_pad_count_normalises_the_bend_reading(self):
        zig = (seg(10, 10, 10, 20) + seg(10, 20, 20, 30) + seg(20, 30, 20, 40))
        many = "".join(pad_footprint("P%d" % i, 5 + i * 2.0, 100) for i in range(10))
        few = pad_footprint("P0", 5, 100)
        r_many = aesthetics.readings(self.board(zig, many))
        r_few = aesthetics.readings(self.board(zig, few))
        self.assertGreater(r_few["bends_per_pad"]["value"],
                           r_many["bends_per_pad"]["value"])
        self.assertAlmostEqual(r_many["bends_per_pad"]["value"],
                               r_few["bends_per_pad"]["value"] / 10.0, places=6)


# --------------------------------------------------------------------------- #
# invariances: the reading must not depend on how the file was written
# --------------------------------------------------------------------------- #
class TestInvariances(Base):
    TRACKS = (seg(10, 10, 30, 10) + seg(30, 10, 40, 20) + seg(40, 20, 40, 40)
              + seg(10, 60, 30, 60, net=2) + seg(30, 60, 50, 60, net=2))

    def _compare(self, other, size=120):
        base = aesthetics.readings(self.board(self.TRACKS))
        return base, aesthetics.readings(self.board(other, size=size))

    def test_reversing_every_segment_changes_nothing(self):
        flipped = "".join(
            seg(x1, y1, x0, y0, net=net)
            for x0, y0, x1, y1, net in
            [(10, 10, 30, 10, 1), (30, 10, 40, 20, 1), (40, 20, 40, 40, 1),
             (10, 60, 30, 60, 2), (30, 60, 50, 60, 2)])
        a, b = self._compare(flipped)
        for key in ("bends_per_100mm", "chunk_median_mm", "collinear_per_100mm",
                    "long_seg_share", "bundle_share"):
            self.assertEqual(a[key]["value"], b[key]["value"], key)

    def test_moving_the_board_changes_nothing(self):
        shifted = "".join(
            seg(x0 + 50, y0 + 30, x1 + 50, y1 + 30, net=net)
            for x0, y0, x1, y1, net in
            [(10, 10, 30, 10, 1), (30, 10, 40, 20, 1), (40, 20, 40, 40, 1),
             (10, 60, 30, 60, 2), (30, 60, 50, 60, 2)])
        a, b = self._compare(shifted, size=200)
        for key in ("bends_per_100mm", "chunk_median_mm", "long_seg_share"):
            self.assertEqual(a[key]["value"], b[key]["value"], key)

    def test_layers_are_kept_apart(self):
        # a piece on B.Cu must not form a joint with a piece on F.Cu
        split = seg(10, 10, 30, 10, layer="F.Cu") + seg(30, 10, 50, 10, layer="B.Cu")
        got = aesthetics.readings(self.board(split))
        self.assertEqual(got["bends_per_100mm"]["value"], 0.0)
        self.assertEqual(got["joints"]["matched_pairs"], 0)
        self.assertEqual(got["joints"]["dangling_ends"], 4)

    def test_a_layer_filter_reads_only_that_layer(self):
        split = seg(10, 10, 30, 10, layer="F.Cu") + seg(30, 10, 50, 10, layer="B.Cu")
        b = self.board(split)
        self.assertEqual(aesthetics.readings(b, layer="B.Cu")["segments"], 1)
        self.assertEqual(aesthetics.readings(b, layer="F.Cu")["segments"], 1)
        self.assertEqual(aesthetics.readings(b)["segments"], 2)


# --------------------------------------------------------------------------- #
# ordering
# --------------------------------------------------------------------------- #
class TestCompare(Base):
    CLEAN = (seg(10, 10, 50, 10) + seg(50, 10, 50, 50) + seg(50, 50, 10, 50))
    #: one CONNECTED path of 16 short pieces, 3 mm each: same kind of copper, a
    #: staircase instead of long runs. Written as a chain on purpose - the earlier
    #: version stacked every piece's end on the next piece's end and produced
    #: three-way joints, which the readings correctly called tees, not bends.
    UGLY = "".join(
        seg(10 + 3 * (i % 2), 10 + 3 * i,
            10 + 3 * ((i + 1) % 2), 10 + 3 * (i + 1))
        for i in range(16))

    def test_a_clean_board_dominates_a_staircase(self):
        clean, ugly = self.board(self.CLEAN), self.board(self.UGLY)
        out = aesthetics.compare([clean, ugly], names=["clean", "ugly"])
        self.assertEqual(out["status"], "ok")
        value = out["value"]
        self.assertEqual(value["order_by_borda"][0], "clean")
        pair = value["pairs"][0]
        self.assertEqual(pair["verdict"], "a_dominates")
        self.assertIn("bends_per_100mm", pair["a_better_on"])

    def test_identical_boards_tie_and_say_why_nothing_was_ranked(self):
        a, b = self.board(self.CLEAN), self.board(self.CLEAN)
        out = aesthetics.compare([a, b], names=["a", "b"])
        value = out["value"]
        self.assertEqual(value["pairs"][0]["verdict"], "tie")
        self.assertTrue(value["not_ranked"])       # every reading read the same
        for r in value["boards"]:
            self.assertAlmostEqual(r["borda"], 0.5, places=6)

    def test_a_split_run_is_told_apart_from_a_bend(self):
        # B differs from A only by how one run is STORED: no new bend, one stitch
        a = self.board(self.CLEAN)
        b = self.board(seg(10, 10, 30, 10) + seg(30, 10, 50, 10)
                       + seg(50, 10, 50, 50) + seg(50, 50, 10, 50))
        out = aesthetics.compare([a, b], names=["whole", "halves"])["value"]
        pair = out["pairs"][0]
        self.assertIn("collinear_per_100mm", pair["a_better_on"])
        self.assertNotIn("bends_per_100mm", pair["a_better_on"] + pair["b_better_on"])

    def test_two_readings_that_disagree_about_density_are_called_confounded(self):
        # A: 1 mm of copper, 1 bend, 100 pads -> awful per 100 mm, excellent per pad
        # B: 100 mm of copper, 10 bends, 1 pad -> the reverse
        a = self.board(seg(10, 10, 10.5, 10) + seg(10.5, 10, 10.5, 10.5),
                       "".join(pad_footprint("A%d" % i, 40 + i * 0.6, 40)
                               for i in range(100)))
        # ten separate 10 mm L-shapes: 100 mm of copper, exactly 10 bends
        lshapes = "".join(
            seg(5 + i * 11, 20, 5 + i * 11 + 5, 20) + seg(5 + i * 11 + 5, 20,
                                                          5 + i * 11 + 5, 25)
            for i in range(10))
        b = self.board(lshapes, pad_footprint("B0", 60, 60))
        out = aesthetics.compare([a, b], names=["stub", "long"])["value"]
        pair = out["pairs"][0]
        self.assertIsNotNone(pair["confounded"])
        self.assertIn("density", pair["confounded"])

    def test_ordering_needs_two_boards(self):
        out = aesthetics.compare([self.board(self.CLEAN)], names=["only"])
        self.assertEqual(out["status"], "error")
        self.assertEqual(out["reason_code"], "NOT_ENOUGH_BOARDS")

    def test_two_boards_without_copper_is_an_error_not_a_ranking(self):
        out = aesthetics.compare([self.board(""), self.board("")])
        self.assertEqual(out["status"], "error")
        self.assertEqual(out["reason_code"], "NO_GEOMETRY")

    def test_unreadable_board_is_named_not_silently_dropped(self):
        out = aesthetics.compare([self.board(self.CLEAN), self.board("")],
                                 names=["clean", "empty"])
        self.assertEqual(out["status"], "ok")
        empty = [b for b in out["value"]["boards"] if b["name"] == "empty"]
        self.assertEqual(empty, [])            # it carries no reading, so it is out
        self.assertIn("clean", [b["name"] for b in out["value"]["boards"]])
        # ...but it is named, with the reason, instead of vanishing
        skipped = out["value"]["skipped"]
        self.assertEqual([s0["name"] for s0 in skipped], ["empty"])
        self.assertIn("no routed copper", skipped[0]["why"])


# --------------------------------------------------------------------------- #
# the calibration table is data, not a threshold
# --------------------------------------------------------------------------- #
class TestCalibration(Base):
    def test_report_places_the_board_but_refuses_nothing(self):
        out = aesthetics.report(self.board(TestCompare.CLEAN))
        self.assertEqual(out["status"], "ok")
        where = out["value"]["place_quality"]
        self.assertIn("not_a_threshold", where)
        self.assertIn("bends_per_100mm", where["readings"])
        # the report never turns a corpus number into a verdict
        self.assertNotIn("verdict", out["value"])

    def test_every_separating_reading_puts_the_hand_board_on_the_good_side(self):
        # a direction typo is invisible in the table and lethal in `compare`:
        # ring the sign of every reading that claims to separate against the two
        # boards it was calibrated on
        table = aesthetics.CALIBRATION
        for key, sign in aesthetics.DIRECTED.items():
            if not table[key].get("separates"):
                continue
            gap = (table[key]["hand"] - table[key]["machine"]) * sign
            self.assertGreater(gap, 0.0, key)

    def test_a_reading_that_does_not_separate_says_so_and_is_still_ranked(self):
        table = aesthetics.CALIBRATION
        self.assertFalse(table["collinear_per_100mm"]["separates"])
        self.assertIn("does NOT separate", table["collinear_per_100mm"]["note"])
        separated = sorted(k for k in aesthetics.DIRECTED
                           if table[k].get("separates"))
        self.assertEqual(separated,
                         sorted(set(aesthetics.DIRECTED) - {"collinear_per_100mm"}))
        # ranked anyway: waste is waste, and hiding the reading would make the
        # other five look like the whole story
        self.assertIn("collinear_per_100mm", aesthetics.DIRECTED)
        out = aesthetics.report(self.board(TestCompare.CLEAN))["value"]
        row = out["place_quality"]["readings"]["collinear_per_100mm"]
        self.assertFalse(row["separates_hand_from_machine"])

    def test_the_calibration_records_where_its_numbers_came_from(self):
        table = aesthetics.CALIBRATION
        for key in aesthetics.DIRECTED:
            self.assertIn(key, table, key)
            self.assertGreater(table[key]["corpus_n"], 0, key)
            lo, hi = table[key]["corpus_range"]
            self.assertLessEqual(lo, hi, key)

    def test_every_ranked_reading_declares_a_direction(self):
        for key in aesthetics.DIRECTED:
            self.assertIn(aesthetics.DIRECTED[key], (1, -1), key)
        rows = aesthetics.readings(self.board(TestCompare.CLEAN))
        for key in ("via_per_100mm", "offgrid_share"):
            self.assertIn("better_when", rows[key])
        self.assertEqual(rows["via_per_100mm"]["better_when"], "no_direction")


if __name__ == "__main__":
    unittest.main()
