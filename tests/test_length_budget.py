# -*- coding: utf-8 -*-
"""Tests for the three readings that sit between "the copper is wrong" and
"here is what you can do about it".

board_length_budget, board_group_consistency and board_reservations are only
worth having if their verdicts can actually FLIP, so every case below pairs a
positive reading with a reverse control on the same geometry: the same board that
answers `route_too_short` answers `fits` when the target is lowered, and the same
group that reads `consistent` reads `inconsistent` when one net takes a detour.
A test that only asserted `status == ok` would pass on a function that returns a
constant.
"""
import os
import sys
import tempfile
import unittest

sys.path.insert(0, os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "src"))

from pcb_agent_mcp import oracle  # noqa: E402
from pcb_agent_mcp.adapters import kicad_pcb  # noqa: E402

STACKUP = """(setup (stackup (layer "F.Cu" (type "copper") (thickness 0.035))
    (layer "dielectric 1" (type "core") (thickness 0.2) (epsilon_r 4.3))
    (layer "B.Cu" (type "copper") (thickness 0.035))))"""


def board_text(tracks="", zones=""):
    """44 x 44 bare board with three named nets and no pads.

    Nothing here needs a footprint: length_budget and group_consistency measure
    routed copper, and reservations measure rule areas against tracks.
    """
    return """(kicad_pcb (version 20221018) %s
      (net 0 "") (net 1 "SIG") (net 2 "GND") (net 3 "V3")
      (gr_rect (start 0 0) (end 44 44) (layer "Edge.Cuts") (width 0.1) (fill none))
%s%s
    )""" % (STACKUP, tracks, zones)


def seg(x0, y0, x1, y1, net, layer="F.Cu", width=0.25):
    return ('  (segment (start %g %g) (end %g %g) (width %g) (layer "%s") (net %d))\n'
            % (x0, y0, x1, y1, width, layer, net))


# 30 mm of SIG: 10 + 10 + 10, the minimum tree over a 10 mm square.
SIG30 = seg(10, 10, 20, 10, 1) + seg(20, 10, 20, 20, 1) + seg(20, 20, 10, 20, 1)
# The same tree with the middle leg going out to y = 30 and back: 50 mm.
SIG50 = (seg(10, 10, 20, 10, 1) + seg(20, 10, 20, 30, 1)
         + seg(20, 30, 20, 20, 1) + seg(20, 20, 10, 20, 1))
GND20 = seg(10, 10, 10, 30, 2)
GND30 = seg(12, 10, 12, 40, 2)
SIG30V = seg(10, 10, 10, 40, 1)   # one straight 30 mm run, parallel to GND30
GND25 = seg(10, 10, 10, 35, 2)


class Base(unittest.TestCase):
    def setUp(self):
        self.dir = tempfile.mkdtemp(prefix="lenbudget_")

    def path(self, tracks="", zones="", name="b.kicad_pcb"):
        p = os.path.join(self.dir, name)
        with open(p, "w", encoding="utf-8", newline="\n") as fh:
            fh.write(board_text(tracks, zones))
        return p

    def board(self, tracks="", zones=""):
        return kicad_pcb.parse_board(self.path(tracks, zones))

    @staticmethod
    def value(result):
        assert result["status"] == "ok", result
        return result["value"]

    def row(self, result, net):
        for r in self.value(result)["nets"]:
            if r["net"] == net:
                return r
        raise AssertionError("net %r missing from %r" % (net, result))


class TestLengthBudget(Base):
    def test_one_net_is_told_it_has_nothing_to_match(self):
        v = self.value(oracle.length_budget(self.board(SIG30), ["SIG"]))
        self.assertEqual(v["verdict"], "nothing_to_match")
        self.assertEqual(v["nets_in_group"], 1)
        self.assertIn("name the group", v["note"])

    def test_the_same_single_net_answers_when_a_target_is_given(self):
        # reverse control for the case above: the question is meaningless only
        # because the target is taken from the net itself
        # 30 mm of copper in three 10 mm runs: ceiling 11.8 mm of extra, so a
        # 45 mm ask is out of reach while a 40 mm ask is not
        b = self.board(SIG30)
        self.assertEqual(self.value(oracle.length_budget(b, ["SIG"], target_mm=40.0))
                         ["verdict"], "budget_met")
        v = self.value(oracle.length_budget(b, ["SIG"], target_mm=45.0))
        self.assertNotEqual(v["verdict"], "nothing_to_match")
        self.assertEqual(v["verdict"], "budget_exceeded")
        self.assertEqual(self.row(oracle.length_budget(b, ["SIG"], target_mm=45.0),
                                  "SIG")["verdict"], "route_too_short")

    def test_a_short_route_is_told_to_move_rather_than_tune(self):
        b = self.board(SIG30 + GND20)
        v = self.value(oracle.length_budget(b, ["SIG", "GND"]))
        gnd = self.row(oracle.length_budget(b, ["SIG", "GND"]), "GND")
        self.assertEqual(v["verdict"], "budget_exceeded")
        self.assertEqual(gnd["verdict"], "route_too_short")
        self.assertEqual(gnd["short_of_target_by_mm"], 9.8)
        self.assertEqual(gnd["ceiling_mm"], 8.077)   # 0.414 * (20 - 0.5)
        self.assertLess(gnd["ceiling_mm"], gnd["short_of_target_by_mm"])
        self.assertEqual(v["unreachable"], ["GND"])

    def test_the_same_short_route_fits_once_the_target_comes_down(self):
        # reverse control: nothing about the copper changed, only the ask
        b = self.board(SIG30 + GND20)
        v = self.value(oracle.length_budget(b, ["SIG", "GND"], target_mm=25.0))
        self.assertEqual(self.row(oracle.length_budget(b, ["SIG", "GND"],
                                                       target_mm=25.0), "GND")["verdict"],
                         "fits")
        self.assertEqual(v["verdict"], "budget_met")
        self.assertEqual(v["unreachable"], [])

    def test_a_narrow_band_costs_capacity_the_tooth_formula_alone_would_allow(self):
        b = self.board(SIG30 + GND20)
        wide = self.row(oracle.length_budget(b, ["SIG", "GND"], target_mm=27.0), "GND")
        narrow_b = oracle.length_budget(b, ["SIG", "GND"], target_mm=27.0, band_mm=0.02)
        narrow = self.row(narrow_b, "GND")
        self.assertEqual(wide["verdict"], "fits")
        self.assertEqual(narrow["verdict"], "needs_wider_band")
        self.assertTrue(narrow["teeth_capped"])
        self.assertFalse(wide["teeth_capped"])
        self.assertLess(narrow["capacity_at_band_mm"], wide["capacity_at_band_mm"])
        # the ceiling is the same: only the band changed, not the straight run
        self.assertEqual(narrow["ceiling_mm"], wide["ceiling_mm"])
        self.assertEqual(self.value(narrow_b)["needs_band"], ["GND"])

    def test_a_net_with_no_copper_is_named_not_silently_skipped(self):
        r = oracle.length_budget(self.board(SIG30), ["SIG", "V3"])
        self.assertEqual(r["status"], "error")
        self.assertEqual(r["reason_code"], "NO_GEOMETRY")
        self.assertIn("V3", r["message"])

    def test_a_band_of_zero_is_refused(self):
        r = oracle.length_budget(self.board(SIG30 + GND20), ["SIG", "GND"], band_mm=0)
        self.assertEqual(r["status"], "error")
        self.assertEqual(r["reason_code"], "ARG_RANGE")

    def test_naming_no_nets_is_refused(self):
        r = oracle.length_budget(self.board(SIG30), [])
        self.assertEqual(r["status"], "error")
        self.assertEqual(r["reason_code"], "ARG_MISSING")


class TestGroupConsistency(Base):
    def test_a_pair_of_equal_parallel_runs_reads_as_one_bundle(self):
        # two straight 30 mm runs side by side: same length, parallel, one pair
        v = self.value(oracle.group_consistency(self.board(SIG30V + GND30), ["SIG", "GND"]))
        self.assertEqual(v["length_spread_mm"], 0.0)
        self.assertEqual(v["verdict"], "consistent", v["failing"])
        self.assertIsNone(v["spacing_cv"])   # one pair: no variation to measure
        # a skip is not a failure
        self.assertNotIn("spacing_even", v["failing"])

    def test_one_detour_makes_the_group_inconsistent_and_names_the_check(self):
        # reverse control: the same 30 mm GND run, but SIG now takes a detour
        v = self.value(oracle.group_consistency(self.board(GND30 + SIG50), ["SIG", "GND"]))
        self.assertEqual(v["length_spread_mm"], 20.0)
        self.assertEqual(v["verdict"], "inconsistent")
        self.assertIn("length_spread", v["failing"])
        self.assertEqual(v["checks"][0]["measured"], 20.0)

    def test_uneven_gaps_are_caught_once_there_are_three_nets(self):
        # three 20 mm vertical runs, gaps 2 mm and 8 mm
        tracks = (seg(10, 10, 10, 30, 1) + seg(12, 10, 12, 30, 2)
                  + seg(20, 10, 20, 30, 3))
        v = self.value(oracle.group_consistency(self.board(tracks), ["SIG", "GND", "V3"]))
        self.assertEqual(v["length_spread_mm"], 0.0)
        self.assertIsNotNone(v["spacing_cv"])
        self.assertIn("spacing_even", v["failing"])
        sv = [c for c in v["checks"] if c["check"] == "spacing_even"][0]
        self.assertFalse(sv["ok"])
        self.assertGreater(sv["measured"], 0.5)
        # reverse control: move the third run to an even 2 mm gap
        even = (seg(10, 10, 10, 30, 1) + seg(12, 10, 12, 30, 2)
                + seg(14, 10, 14, 30, 3))
        v2 = self.value(oracle.group_consistency(self.board(even), ["SIG", "GND", "V3"]))
        # cv runs over ALL pairs, so an evenly spaced bundle still scores ~0.35
        # (2, 2 and the outer 4); what matters is that it lands under the limit
        self.assertLess(v2["spacing_cv"], 0.5)
        self.assertLess(v2["spacing_cv"], v["spacing_cv"])
        self.assertEqual(v2["verdict"], "consistent", v2["failing"])

    def test_one_net_is_not_a_group(self):
        r = oracle.group_consistency(self.board(SIG30), ["SIG"])
        self.assertEqual(r["status"], "error")
        self.assertEqual(r["reason_code"], "ARG_RANGE")

    def test_a_missing_net_is_named(self):
        r = oracle.group_consistency(self.board(SIG30), ["SIG", "V3"])
        self.assertEqual(r["status"], "error")
        self.assertEqual(r["reason_code"], "NO_GEOMETRY")
        self.assertIn("V3", r["message"])


RESERVE_FREE = """
  (zone (net 0) (net_name "") (name "reserve:GND") (layers "F.Cu") (hatch edge 0.5)
    (connect_pads (clearance 0.2)) (min_thickness 0.25) (fill no)
    (keepout (tracks not_allowed) (vias not_allowed) (pads allowed) (copperpour allowed))
    (polygon (pts (xy 28 28) (xy 42 28) (xy 42 42) (xy 28 42))))
"""
RESERVE_NAMED = RESERVE_FREE.replace('"reserve:GND"', '"buck cluster"')


class TestReservations(Base):
    def test_a_board_with_no_named_zone_holds_nothing(self):
        v = self.value(oracle.reservations(self.board(SIG30)))
        self.assertEqual(v["verdict"], "no_reservations")
        self.assertEqual(v["total"], 0)

    def test_a_named_band_that_is_still_empty_reads_free(self):
        v = self.value(oracle.reservations(self.board(SIG30 + GND20, RESERVE_FREE)))
        self.assertEqual(v["total"], 1)
        row = v["reservations"][0]
        self.assertEqual(row["group"], "GND")
        self.assertEqual(row["kind"], "reservation")
        self.assertTrue(row["nets_are_known"])
        self.assertTrue(row["free"])
        self.assertEqual(row["occupied_tracks"], 0)
        self.assertEqual(v["verdict"], "reservations_all_free")
        self.assertEqual(row["layers"], ["F.Cu"])
        self.assertEqual(row["rules"].get("tracks"), "not_allowed")

    def test_copper_of_another_net_inside_the_band_fills_it(self):
        # reverse control: same rule area, one SIG track ending inside it
        b = self.board(SIG30 + GND20 + seg(20, 20, 40, 40, 1), RESERVE_FREE)
        v = self.value(oracle.reservations(b))
        row = v["reservations"][0]
        self.assertGreater(row["occupied_tracks"], 0)
        self.assertEqual(row["foreign_nets"], ["SIG"])
        self.assertFalse(row["free"])
        self.assertEqual(v["verdict"], "reservations_occupied")
        self.assertEqual(v["occupied"], ["reserve:GND"])

    def test_copper_of_the_reserved_net_does_not_count_as_occupying(self):
        # GND's own track runs inside the band: the label names it, so it is not
        # foreign. Only a net outside the list fills it.
        b = self.board(seg(10, 10, 40, 40, 2), RESERVE_FREE)
        row = self.value(oracle.reservations(b))["reservations"][0]
        self.assertGreater(row["occupied_tracks"], 0)
        self.assertEqual(row["foreign_nets"], [])
        self.assertTrue(row["free"])

    def test_a_label_that_is_not_a_net_list_never_calls_copper_foreign(self):
        b = self.board(SIG30 + GND20 + seg(20, 20, 40, 40, 1), RESERVE_NAMED)
        row = self.value(oracle.reservations(b))["reservations"][0]
        self.assertIsNone(row["group"])
        self.assertEqual(row["label"], "buck cluster")
        self.assertEqual(row["kind"], "rule_area")
        self.assertFalse(row["nets_are_known"])
        self.assertEqual(row["foreign_nets"], [])
        self.assertFalse(row["free"])   # free is judged only for a net list
        self.assertGreater(row["occupied_tracks"], 0)


if __name__ == "__main__":
    unittest.main()
