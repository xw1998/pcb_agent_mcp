"""Return paths on a real stackup: which layer changes need a hole, and which
ones no hole can save.

What these tests protect
------------------------
Three claims, and each has a control that has to fail, because the first version
of this module was green on all of them and useless on every board that came off
disk:

1. **A KiCad net name is `/GND`, not `GND`.** The ground lookup read the whole
   string, so `ground_net()` answered `no_ground_net` on any parsed board and the
   emitter pass could never fire. Control: `AGND` must stay a DIFFERENT return
   network (not auto-picked), and `/PWR/GND` must be recognised.

2. **"One ground via beside every layer change" is a two-layer rule.** On a
   stackup, a signal that goes F.Cu -> In2.Cu with an In1 GND plane between them
   has the same reference on both sides: the return current never leaves that
   plane, and a hole beside it is copper with no job. Control: a layer change
   whose two sides reference DIFFERENT nets must be reported as a defect that no
   via can fix, and a via that joins one layer only must be reported as a stub.

3. **A hole only lands on copper that is really there, on two layers.** Control:
   ground on one layer only is refused with the layers named, not faked.

The frame is the canonical one (mm, y up) - the file's y-down spelling is the
adapter's business.
"""
import os
import sys
import unittest

sys.path.insert(0, os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "src"))

from pcb_agent_mcp.adapters.canonical import Board  # noqa: E402
from pcb_agent_mcp.route import return_path as RP  # noqa: E402
from pcb_agent_mcp.route import router  # noqa: E402


def _stack(*names):
    return [{"name": n, "type": "signal", "thickness_mm": 0.035} for n in names]


def _pour(net, layer, x0=0.0, y0=0.0, x1=30.0, y1=30.0):
    ring = [(x0, y0), (x1, y0), (x1, y1), (x0, y1)]
    return {"net": net, "layers": [layer], "layer": layer, "is_keepout": False,
            "outline": list(ring), "filled_polygons": [list(ring)],
            "fill_mode": "solid"}


def _track(net, layer, a, b, width=0.2):
    return {"net": net, "layer": layer, "width": width,
            "start": [a[0], a[1]], "end": [b[0], b[1]]}


def _via(net, x, y, span, layers=None, dia=0.6, drill=0.3):
    return {"net": net, "x": x, "y": y, "diameter": dia, "drill": drill,
            "from": span[0], "to": span[-1],
            "layers": list(layers if layers is not None else span),
            "kind": "through"}


def _board(copper, zones=(), tracks=(), vias=()):
    b = Board("canonical", "memory")
    b.stackup = _stack(*copper)
    b.zones = list(zones)
    b.tracks = list(tracks)
    b.vias = list(vias)
    return b


class TestTheGroundNetIsFoundUnderItsRealName(unittest.TestCase):
    """A parsed board spells ground `/GND`; the pass has to read that as ground.

    Reading the whole string instead of the last path segment made the emitter
    answer `no_ground_net` on every board this tool has ever been pointed at,
    while every test - all written with a plain `GND` - stayed green.
    """

    def test_hierarchical_names_that_are_ground(self):
        for name in ("GND", "gnd", "/GND", "/PWR/GND", "GNDD", "/PWR/GNDPWR",
                     " VSS ", "VSS"):
            self.assertTrue(RP._looks_ground(name), name)

    def test_names_that_are_not_ground(self):
        # AGND is a different return network: auto-picking it would put stitching
        # holes on the analogue plane and no DRC would ever say so.
        for name in ("AGND", "/AGND", "GNDA", "+3V3", "/DDR_DQ0", "VCCA", "", None,
                     42, "/GND/SPLIT"):
            self.assertFalse(RP._looks_ground(name), name)

    def test_a_parsed_style_board_finds_its_ground_and_audits(self):
        b = _board(["F.Cu", "In1.Cu", "B.Cu"],
                   zones=[_pour("/GND", "In1.Cu")],
                   tracks=[_track("/GND", "F.Cu", (0.0, 10.0), (30.0, 10.0))],
                   vias=[_via("/DDR_DQ0", 10.0, 10.0, ("F.Cu", "B.Cu"))])
        rep = RP.audit(b)
        self.assertEqual(rep["verdict"], "unserved", rep)
        self.assertEqual(rep["net"], "/GND")
        self.assertEqual(rep["signal_vias"], 1)


class TestALayerChangeIsJudgedByItsReferencePlane(unittest.TestCase):
    """The hole is owed by the reference plane, not by the act of changing layers."""

    def _two_sided(self, bottom_pour_net=None):
        """F.Cu -> In2.Cu, with In1 carrying GND under both."""
        tracks = [_track("/DDR_DQ0", "F.Cu", (5.0, 10.0), (10.0, 10.0)),
                  _track("/DDR_DQ0", "In2.Cu", (10.0, 10.0), (15.0, 10.0))]
        zones = [_pour("/GND", "In1.Cu")]
        if bottom_pour_net:
            zones.append(_pour(bottom_pour_net, "B.Cu"))
        return _board(["F.Cu", "In1.Cu", "In2.Cu", "B.Cu"], zones=zones,
                      tracks=tracks,
                      vias=[_via("/DDR_DQ0", 10.0, 10.0, ("F.Cu", "In2.Cu"))])

    def test_same_reference_on_both_sides_needs_no_hole(self):
        rep = RP.audit_reference(self._two_sided(), depth=1)
        self.assertEqual(rep["counts"].get("shared_reference"), 1, rep)
        self.assertEqual(rep["cases"], [])
        self.assertEqual(rep["verdict"], "return_paths_intact")

    def test_the_reason_a_shared_reference_is_fine_is_in_the_report(self):
        rep = RP.audit_reference(self._two_sided(), depth=1)
        self.assertIn("never leaves it", rep["meaning"]["shared_reference"])

    def test_a_change_of_plane_net_is_a_defect_no_via_can_fix(self):
        # In2 -> B.Cu, referencing In1 (GND) and In3 (+1V35_DDR) respectively.
        b = _board(
            ["F.Cu", "In1.Cu", "In2.Cu", "In3.Cu", "B.Cu"],
            zones=[_pour("/GND", "In1.Cu"), _pour("/+1V35_DDR", "In3.Cu")],
            tracks=[_track("/DDR_DQ1", "In2.Cu", (5.0, 10.0), (10.0, 10.0)),
                    _track("/DDR_DQ1", "B.Cu", (10.0, 10.0), (15.0, 10.0))],
            vias=[_via("/DDR_DQ1", 10.0, 10.0, ("In2.Cu", "B.Cu"))])
        rep = RP.audit_reference(b, depth=1)
        self.assertEqual(rep["counts"].get("reference_changes_net"), 1, rep)
        self.assertEqual(rep["verdict"], "defects")
        case = rep["defect_cases"][0]
        self.assertEqual(case["detail"]["reference"]["net"], "/GND")
        self.assertEqual(case["detail"]["reference_after"]["net"], "/+1V35_DDR")
        self.assertIn("no via", rep["meaning"]["reference_changes_net"])

    def test_a_reference_on_the_same_net_but_another_layer_asks_for_a_hole(self):
        b = _board(
            ["F.Cu", "In1.Cu", "In2.Cu", "In3.Cu", "B.Cu"],
            zones=[_pour("/GND", "In1.Cu"), _pour("/GND", "In3.Cu")],
            tracks=[_track("/DDR_DQ2", "In2.Cu", (5.0, 10.0), (10.0, 10.0)),
                    _track("/DDR_DQ2", "B.Cu", (10.0, 10.0), (15.0, 10.0))],
            vias=[_via("/DDR_DQ2", 10.0, 10.0, ("In2.Cu", "B.Cu"))])
        rep = RP.audit_reference(b, depth=1)
        self.assertEqual(rep["counts"].get("stitch_needed"), 1, rep)

    def test_a_hole_with_ground_beside_it_is_already_stitched(self):
        b = _board(
            ["F.Cu", "In1.Cu", "In2.Cu", "In3.Cu", "B.Cu"],
            zones=[_pour("/GND", "In1.Cu"), _pour("/GND", "In3.Cu")],
            tracks=[_track("/DDR_DQ2", "In2.Cu", (5.0, 10.0), (10.0, 10.0)),
                    _track("/DDR_DQ2", "B.Cu", (10.0, 10.0), (15.0, 10.0))],
            vias=[_via("/DDR_DQ2", 10.0, 10.0, ("In2.Cu", "B.Cu")),
                  _via("/GND", 10.5, 10.0, ("F.Cu", "B.Cu"))])
        rep = RP.audit_reference(b, depth=1, reach_mm=2.0)
        self.assertEqual(rep["counts"].get("stitched"), 1, rep)

    def test_a_via_joining_one_layer_is_a_stub(self):
        # The tracks end at the via but on ONE layer only: a hole that changes no
        # current path. DRC calls this via_dangling; it is not a return path.
        b = _board(["F.Cu", "In1.Cu", "B.Cu"],
                   zones=[_pour("/GND", "In1.Cu")],
                   tracks=[_track("/SIG", "F.Cu", (5.0, 10.0), (10.0, 10.0)),
                           _track("/SIG", "F.Cu", (10.0, 10.0), (15.0, 10.0))],
                   vias=[_via("/SIG", 10.0, 10.0, ("F.Cu", "B.Cu"))])
        rep = RP.audit_reference(b, depth=1)
        self.assertEqual(rep["counts"].get("dangling_escape"), 1, rep)
        self.assertEqual(rep["cases"][0]["detail"]["layers_joined"], ["F.Cu"])

    def test_no_pour_within_depth_is_reported_not_guessed(self):
        b = _board(["F.Cu", "In1.Cu", "In2.Cu", "B.Cu"],
                   zones=[_pour("/GND", "In1.Cu")],
                   tracks=[_track("/SIG", "In2.Cu", (5.0, 10.0), (10.0, 10.0)),
                           _track("/SIG", "B.Cu", (10.0, 10.0), (15.0, 10.0))],
                   vias=[_via("/SIG", 10.0, 10.0, ("In2.Cu", "B.Cu"))])
        rep = RP.audit_reference(b, depth=1)
        self.assertEqual(rep["counts"].get("no_reference"), 1, rep)
        self.assertEqual(rep["cases"][0]["detail"]["layers_without_pour"], ["B.Cu"])


class TestThePlannerPlacesHolesOnlyWhereCopperIs(unittest.TestCase):
    """Same question, whole board, and nothing written unless it is legal."""

    def _one_via(self, ground_track_on=None, extra_zones=()):
        tracks = [_track("/DDR_DQ0", "F.Cu", (5.0, 10.0), (10.0, 10.0))]
        if ground_track_on:
            # A ground track crossing the via's neighbourhood: the second layer
            # of ground copper a hole needs to be anything but a stub.
            tracks.append(_track("/GND", ground_track_on, (5.0, 11.0), (15.0, 11.0)))
        return _board(["F.Cu", "In1.Cu", "B.Cu"],
                      zones=[_pour("/GND", "In1.Cu")] + list(extra_zones),
                      tracks=tracks,
                      vias=[_via("/DDR_DQ0", 10.0, 10.0, ("F.Cu", "B.Cu"))])

    def test_a_via_with_ground_on_two_layers_gets_one_hole(self):
        rep = RP.plan(self._one_via(ground_track_on="F.Cu"), clearance_mm=0.15)
        self.assertEqual(rep["status"], "stitched", rep)
        self.assertEqual(len(rep["ops"]), 1)
        op = rep["ops"][0]
        self.assertEqual(op["net"], "/GND")
        self.assertEqual(op["role"], "ground_stitch")
        self.assertEqual(op["for_net"], "/DDR_DQ0")
        self.assertLessEqual(rep["added"][0]["distance_mm"], 2.0)

    def test_ground_on_one_layer_only_is_refused_with_the_layers_named(self):
        rep = RP.plan(self._one_via(), clearance_mm=0.15)
        self.assertEqual(rep["status"], "refused", rep)
        self.assertEqual(rep["ops"], [])
        self.assertEqual(rep["skipped"][0]["reason"], "ground_not_on_two_layers")
        self.assertEqual(rep["skipped"][0]["layers_with_ground"], ["In1.Cu"])

    def test_a_via_already_beside_a_ground_via_is_not_doubled(self):
        b = self._one_via()
        b.vias.append(_via("/GND", 10.4, 10.0, ("F.Cu", "B.Cu")))
        rep = RP.plan(b, clearance_mm=0.15)
        self.assertEqual(rep["ops"], [])
        self.assertEqual(rep["skipped"][0]["reason"], "already_stitched")

    def test_two_ground_nets_is_a_refusal_not_a_coin_flip(self):
        # AGND is deliberately NOT a ground here, so the second return network
        # has to be a real one: VSS is.
        b = self._one_via(extra_zones=[_pour("/VSS", "B.Cu")])
        rep = RP.plan(b)
        self.assertEqual(rep["status"], "refused")
        self.assertEqual(rep["why"]["reason"], "ambiguous_ground_net")

    def test_a_named_ground_is_used_even_when_it_is_not_auto_picked(self):
        b = self._one_via(ground_track_on="F.Cu", extra_zones=[_pour("/VSS", "B.Cu")])
        rep = RP.plan(b, net="/GND", clearance_mm=0.15)
        self.assertEqual(rep["status"], "stitched", rep)

    def test_the_hole_size_comes_from_the_board_not_from_a_constant(self):
        b = self._one_via(ground_track_on="F.Cu")
        b.vias[0]["diameter"], b.vias[0]["drill"] = 0.8, 0.4
        rep = RP.plan(b, clearance_mm=0.15)
        self.assertEqual(rep["dia_mm"], 0.8)
        self.assertEqual(rep["drill_mm"], 0.4)

    def test_no_legal_spot_says_what_refused_it(self):
        # A 1 mm clearance on a board whose ground track sits 1 mm away: every
        # probe is refused `too_close`, and the reason has to be readable.
        b = self._one_via(ground_track_on="F.Cu")
        b.tracks.append(_track("/OTHER", "F.Cu", (5.0, 10.55), (15.0, 10.55)))
        rep = RP.plan(b, clearance_mm=1.0, max_ops=4)
        self.assertEqual(rep["ops"], [])
        self.assertEqual(rep["skipped"][0]["reason"], "no_legal_spot")
        self.assertTrue(rep["skipped"][0]["probes"] > 0)
        self.assertTrue(rep["skipped"][0]["refused_by"])


class TestTheStackupReportNamesTheLayersWithNoReturnPath(unittest.TestCase):
    """Per-layer: which signal layer has no pour beside it at all."""

    def test_a_layer_with_no_pour_beside_it_is_named(self):
        b = _board(["F.Cu", "In1.Cu", "In2.Cu", "B.Cu"],
                   zones=[_pour("/GND", "In1.Cu")],
                   tracks=[_track("/SIG", "B.Cu", (1.0, 1.0), (20.0, 1.0))])
        rep = router._stackup_reference(b, ["F.Cu", "In1.Cu", "In2.Cu", "B.Cu"],
                                        [])
        self.assertEqual(rep["layers_without_reference"], ["B.Cu"], rep)
        self.assertEqual(rep["layers_without_reference_and_copper"], ["B.Cu"])
        self.assertEqual(rep["pour_layers"], ["In1.Cu"])
        self.assertEqual(rep["In2.Cu"]["reference_layers"], ["In1.Cu"])

    def test_a_split_plane_is_reported_as_split_not_as_one_net(self):
        # In3 carries three nets. Reading one name off it would claim the whole
        # layer is +3V3, and every signal over the +1V35_DDR pour would look like
        # it references the wrong plane.
        b = _board(["F.Cu", "In1.Cu", "In2.Cu", "In3.Cu", "B.Cu"],
                   zones=[_pour("/GND", "In1.Cu"),
                          _pour("/+1V35_DDR", "In3.Cu", 0.0, 0.0, 10.0, 30.0),
                          _pour("/+3V3", "In3.Cu", 10.0, 0.0, 30.0, 30.0)])
        copper = ["F.Cu", "In1.Cu", "In2.Cu", "In3.Cu", "B.Cu"]
        rep = router._stackup_reference(b, copper, [])
        nb = [n for n in rep["In2.Cu"]["neighbours"] if n["layer"] == "In3.Cu"][0]
        self.assertTrue(nb["pour_split"], nb)
        self.assertEqual(sorted(nb["pour_nets"]), ["/+1V35_DDR", "/+3V3"])
        # biggest pour first: +3V3 covers twice the area of +1V35_DDR here
        self.assertEqual(rep["In2.Cu"]["reference_nets"]["In3.Cu"],
                         ["/+3V3", "/+1V35_DDR"])

    def test_an_empty_board_reports_nothing_missing_by_accident(self):
        # With no pours at all the report must not claim a return path exists.
        b = _board(["F.Cu", "B.Cu"])
        rep = router._stackup_reference(b, ["F.Cu", "B.Cu"], [])
        self.assertEqual(rep["layers_without_reference"], ["B.Cu", "F.Cu"])
        self.assertEqual(rep["pour_layers"], [])


if __name__ == "__main__":
    unittest.main()


class TestTheToolAnswersBothReadings(unittest.TestCase):
    """End to end through the tool surface, on a board read off disk.

    The point of the tool is that these two questions were UNASKABLE before:
    `board_stitch_vias` refuses a board whose ground is a pour on an inner layer
    (it wants same-net fill on the layers the hole spans), so on a six-layer board
    it answered `not_stitchable` and never looked at the holes, and the emitter
    pass only ever saw the copper of its own run.
    """

    def _tool(self, **extra):
        from pcb_agent_mcp import tools
        path = os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))),
                            "examples", "rk3506_som", "rk3506_som.kicad_pcb")
        args = {"path": path}
        args.update(extra)
        return tools.call_tool("board_return_path", args)

    def test_the_board_gets_a_net_and_both_readings_with_counts_that_add_up(self):
        out = self._tool()
        self.assertEqual(out["status"], "ok", out)
        v = out["value"]
        self.assertTrue(v["writes_nothing"])
        prox, ref = v["proximity"], v["reference"]
        self.assertEqual(ref["net"], prox["net"])
        self.assertEqual(prox["signal_vias"], ref["signal_vias"])
        self.assertEqual(sum(ref["counts"].values()), ref["signal_vias"])
        # Both readings must survive to the caller: the reference one is what a
        # two-layer checker never asks, so dropping it would return the old answer.
        self.assertIn("unserved_shortlist", prox)
        self.assertIn("defect_cases", ref)

    def test_a_defect_no_via_can_fix_is_named_in_the_next_step(self):
        out = self._tool()
        counts = out["value"]["reference"]["counts"]
        if counts.get("reference_changes_net") or counts.get("no_reference"):
            self.assertIn("no via fixes those", out["next_step"])
        else:                          # nothing to name: the message must not lie
            self.assertNotIn("next_step", out)

    def test_place_reports_its_refusals_and_writes_nothing_without_apply(self):
        out = self._tool(action="place", clearance_mm=0.25, max_ops=40)
        self.assertEqual(out["status"], "ok", out)
        v = out["value"]
        self.assertTrue(v["writes_nothing"])
        self.assertIn(v["status"], ("stitched", "refused", "nothing_to_stitch"))
        for skip in v["skipped"][:5]:
            self.assertTrue(skip.get("reason"), skip)


class TestANetIsNotAllowedToChangeLayerForFree(unittest.TestCase):
    """The policy a bus is usually given in words: "this bus does not change layer".

    Written as a count or as a verdict, it has to be judged per net, because the
    decision is made per net ("DDR never leaves the top two layers") and the
    defect is per net ("DQ4 references two different planes"). A whole-board
    total cannot express either: it stays the same when one net improves and
    another gets worse.
    """

    def _shared(self):
        """F.Cu -> In2.Cu with In1/GND under both: a legal change."""
        return _board(["F.Cu", "In1.Cu", "In2.Cu"],
                      zones=[_pour("/GND", "In1.Cu")],
                      tracks=[_track("/DDR_DQ0", "F.Cu", (5.0, 10.0), (10.0, 10.0)),
                              _track("/DDR_DQ0", "In2.Cu", (10.0, 10.0), (15.0, 10.0))],
                      vias=[_via("/DDR_DQ0", 10.0, 10.0, ("F.Cu", "In2.Cu"))])

    def _two_planes(self):
        """In2 -> B.Cu referencing /GND above and /+1V35_DDR below."""
        return _board(
            ["F.Cu", "In1.Cu", "In2.Cu", "In3.Cu", "B.Cu"],
            zones=[_pour("/GND", "In1.Cu"), _pour("/+1V35_DDR", "In3.Cu")],
            tracks=[_track("/DDR_DQ1", "In2.Cu", (5.0, 10.0), (10.0, 10.0)),
                    _track("/DDR_DQ1", "B.Cu", (10.0, 10.0), (15.0, 10.0)),
                    _track("/USB0_DP", "F.Cu", (5.0, 20.0), (10.0, 20.0)),
                    _track("/USB0_DP", "In2.Cu", (10.0, 20.0), (15.0, 20.0))],
            vias=[_via("/DDR_DQ1", 10.0, 10.0, ("In2.Cu", "B.Cu")),
                  _via("/USB0_DP", 10.0, 20.0, ("F.Cu", "In2.Cu"))])

    def test_the_pattern_selects_its_nets_and_says_which_ones_matched_nothing(self):
        res = RP.layer_changes(self._two_planes(), nets=["DDR"])
        self.assertEqual(list(res["nets"]), ["/DDR_DQ1"], res["nets"])
        self.assertNotIn("/USB0_DP", res["nets"])
        self.assertEqual(res.get("unmatched_patterns"), [])
        res2 = RP.layer_changes(self._two_planes(), nets=["DDR", "NOPE"])
        self.assertEqual(res2["unmatched_patterns"], ["NOPE"])

    def test_a_poured_rail_is_out_of_the_population_and_named(self):
        # The board carries +1V35_DDR by pouring it. Its vias are plane
        # stitching, and `DDR` is a substring of its name, so without this the
        # rail's holes would be reported as a DDR bus defect.
        b = _board(
            ["F.Cu", "In1.Cu", "In2.Cu", "In3.Cu", "B.Cu"],
            zones=[_pour("/GND", "In1.Cu"), _pour("/+1V35_DDR", "In3.Cu")],
            tracks=[_track("/DDR_DQ1", "In2.Cu", (5.0, 10.0), (10.0, 10.0)),
                    _track("/DDR_DQ1", "B.Cu", (10.0, 10.0), (15.0, 10.0)),
                    _track("/+1V35_DDR", "In3.Cu", (5.0, 30.0), (10.0, 30.0)),
                    _track("/+1V35_DDR", "B.Cu", (10.0, 30.0), (15.0, 30.0))],
            vias=[_via("/DDR_DQ1", 10.0, 10.0, ("In2.Cu", "B.Cu")),
                  _via("/+1V35_DDR", 10.0, 30.0, ("In3.Cu", "B.Cu"))])
        res = RP.layer_changes(b, nets=["DDR"])
        self.assertEqual(list(res["nets"]), ["/DDR_DQ1"], res["nets"])
        self.assertEqual(res["excluded_as_poured"], ["/+1V35_DDR"])
        self.assertFalse(res["include_poured"])
        # asking for it explicitly gets the whole-board reading back
        wide = RP.layer_changes(b, nets=["DDR"], include_poured=True)
        self.assertIn("/+1V35_DDR", wide["nets"])
        self.assertEqual(wide["excluded_as_poured"], [])

    def test_a_pattern_matches_a_hierarchical_name_by_its_parts(self):
        self.assertTrue(RP._net_matches("/DDR_DQ0", "DDR"))
        self.assertTrue(RP._net_matches("/DDR_DQ0", "DQ0"))
        self.assertTrue(RP._net_matches("/PWR/DDR_VREF", "DDR"))
        self.assertFalse(RP._net_matches("/USB0_DP", "DDR"))
        self.assertFalse(RP._net_matches("/DDR_DQ0", " "))
        self.assertFalse(RP._net_matches(None, "DDR"))

    def test_a_change_between_two_plane_nets_is_a_violation(self):
        res = RP.layer_changes(self._two_planes(), nets=["DDR"])
        self.assertEqual(res["verdict"], "violations", res)
        self.assertEqual(res["violations_total"], 1)
        v = res["violations"][0]
        self.assertEqual(v["verdict"], "reference_changes_net")
        self.assertFalse(v["fixable_by_via"])
        self.assertEqual(res["nets"]["/DDR_DQ1"]["verdict"], "violations")

    def test_a_legal_change_is_clean_and_owes_no_hole(self):
        res = RP.layer_changes(self._shared(), nets=["DDR"])
        self.assertEqual(res["verdict"], "clean", res)
        self.assertEqual(res["violations_total"], 0)
        self.assertEqual(res["owed_vias"], 0)
        self.assertEqual(res["nets"]["/DDR_DQ0"]["layers"], ["F.Cu", "In2.Cu"])

    def test_max_changes_turns_a_clean_bus_into_a_violation_by_count(self):
        # Every change here is legal; the rule being tested is "this net changes
        # layer at most N times", which no defect count can express.
        res = RP.layer_changes(self._shared(), nets=["DDR"], max_changes=0)
        self.assertEqual(res["verdict"], "violations")
        self.assertEqual(res["violations"][0]["verdict"], "too_many_layer_changes")
        self.assertEqual(res["violations"][0]["detail"]["vias"], 1)
        self.assertEqual(res["counts"].get("shared_reference"), 1)

    def test_a_stitchable_change_is_reported_as_owed_not_as_a_violation(self):
        b = _board(
            ["F.Cu", "In1.Cu", "In2.Cu", "In3.Cu", "B.Cu"],
            zones=[_pour("/GND", "In1.Cu"), _pour("/GND", "In3.Cu")],
            tracks=[_track("/DDR_DQ2", "In2.Cu", (5.0, 10.0), (10.0, 10.0)),
                    _track("/DDR_DQ2", "B.Cu", (10.0, 10.0), (15.0, 10.0))],
            vias=[_via("/DDR_DQ2", 10.0, 10.0, ("In2.Cu", "B.Cu"))])
        res = RP.layer_changes(b, nets=["DDR"], depth=1)
        self.assertEqual(res["verdict"], "clean", res)
        self.assertEqual(res["owed_vias"], 1)
        self.assertEqual(res["counts"].get("stitch_needed"), 1)

    def test_a_net_with_no_via_is_no_layer_change_not_a_pass(self):
        res = RP.layer_changes(self._shared(), nets=["USB0"])
        self.assertEqual(res["verdict"], "no_layer_change")
        self.assertEqual(res["nets"], {})

class TestTheGateJudgesTheEditNotTheBoard(unittest.TestCase):
    """A board that already carries a defect must stay editable.

    Refusing every future edit until an old defect is repaired is how a rule gets
    switched off, so the comparison is "violations that exist after and not
    before", never a total.
    """

    def _board(self, broken):
        tracks = [_track("/DDR_DQ0", "F.Cu", (5.0, 10.0), (10.0, 10.0)),
                  _track("/DDR_DQ0", "In2.Cu", (10.0, 10.0), (15.0, 10.0))]
        vias = [_via("/DDR_DQ0", 10.0, 10.0, ("F.Cu", "In2.Cu"))]
        if broken:
            tracks += [_track("/DDR_DQ1", "In2.Cu", (5.0, 20.0), (10.0, 20.0)),
                       _track("/DDR_DQ1", "B.Cu", (10.0, 20.0), (15.0, 20.0))]
            vias.append(_via("/DDR_DQ1", 10.0, 20.0, ("In2.Cu", "B.Cu")))
        return _board(
            ["F.Cu", "In1.Cu", "In2.Cu", "In3.Cu", "B.Cu"],
            zones=[_pour("/GND", "In1.Cu"), _pour("/+1V35_DDR", "In3.Cu")],
            tracks=tracks, vias=vias)

    def test_a_new_violation_blocks(self):
        res = RP.policy_gate(self._board(False), self._board(True), nets=["DDR"])
        self.assertFalse(res["ok"], res)
        self.assertEqual(len(res["new"]), 1)
        self.assertEqual(res["new"][0]["verdict"], "reference_changes_net")
        self.assertIn("/DDR_DQ1", res["message"])

    def test_an_old_violation_alone_does_not_block_even_while_it_remains(self):
        res = RP.policy_gate(self._board(True), self._board(True), nets=["DDR"])
        self.assertTrue(res["ok"], res)
        self.assertEqual(res["new"], [])
        # and it is still reported, so a pass does not imply a clean board
        self.assertEqual(res["after"]["violations"], 1)

    def test_fixing_a_violation_is_not_blocked_by_the_policy(self):
        res = RP.policy_gate(self._board(True), self._board(False), nets=["DDR"])
        self.assertTrue(res["ok"], res)
        self.assertEqual(res["before"]["violations"], 1)
        self.assertEqual(res["after"]["violations"], 0)

class TestThePolicyOnTheRealBoardAndOnTheWritePath(unittest.TestCase):
    """The two things a person actually touches: the tool and `board_apply`."""

    def _path(self):
        return os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))),
                            "examples", "rk3506_som", "rk3506_som.kicad_pcb")

    def test_the_layer_changes_action_reports_per_net(self):
        from pcb_agent_mcp import tools
        out = tools.call_tool("board_return_path",
                              {"path": self._path(), "action": "layer_changes",
                               "nets": ["DDR"], "shortlist": 5})
        self.assertEqual(out["status"], "ok", out)
        v = out["value"]
        self.assertTrue(v["writes_nothing"])
        self.assertTrue(v["nets"])
        for name in v["nets"]:
            self.assertTrue(name.startswith("/DDR"), name)
            rec = v["nets"][name]
            self.assertEqual(sum(rec["counts"].values()), rec["vias"])
        # `DDR` as a substring also selects the +1V35_DDR rail; the rail is
        # poured, so it is named as excluded instead of judged as a bus defect
        self.assertIn("/+1V35_DDR", v["excluded_as_poured"])
        self.assertNotIn("/+1V35_DDR", v["nets"])
        self.assertTrue(v["excluded_as_poured"])
        self.assertTrue(out.get("note"), out)
        # every violation is either a real defect or the count cap, and none of
        # them claims a hole would fix it
        for viol in v["violations"]:
            self.assertIn(viol["verdict"], ("reference_changes_net",
                                            "no_reference", "dangling_escape",
                                            "too_many_layer_changes"))
            self.assertFalse(viol["fixable_by_via"])

    def test_a_via_that_changes_layer_is_refused_and_the_file_is_untouched(self):
        import shutil
        import tempfile
        from pcb_agent_mcp import edit as E
        tmp = tempfile.mkdtemp(prefix="pcbpolicy-")
        home = os.environ.get("PCB_AGENT_HOME")
        os.environ["PCB_AGENT_HOME"] = os.path.join(tmp, "home")
        try:
            path = os.path.join(tmp, "board.kicad_pcb")
            shutil.copy(self._path(), path)
            with open(path, "rb") as fh:
                before = fh.read()
            opts = {"engine": False, "preconditions": False,
                    "allow_new_violations": True,
                    "precheck_layer_policy": {"nets": ["DDR"]}}
            ops = [{"op": "add_via", "x": 12.0, "y": 30.0, "size": 0.6,
                    "drill": 0.3, "net": "/DDR_DQ0"}]
            prop = E.propose(path, ops, opts)
            # propose already knows: the cheap place to change one's mind
            self.assertEqual(prop["precheck"]["layer_policy"]["ok"], False)
            self.assertTrue([b for b in prop["blocking"]
                             if b.get("gate") == "layer_policy"], prop["blocking"])
            with self.assertRaises(E.EditError) as ctx:
                E.apply_plan(prop["plan_id"], confirm=True, options=opts)
            self.assertEqual(ctx.exception.reason_code, "GATE_BLOCKED")
            self.assertIn("layer change the policy forbids", str(ctx.exception))
            with open(path, "rb") as fh:
                self.assertEqual(fh.read(), before, "the refuse must roll back")
            # the same plan with the override writes, and the gate still REPORTS
            # the violation it accepted - an override is not a clean bill
            opts2 = dict(opts, allow_layer_changes=True)
            prop2 = E.propose(path, ops, opts2)
            out = E.apply_plan(prop2["plan_id"], confirm=True, options=opts2)
            self.assertEqual(out["status"], "ok", out)
            self.assertEqual(out["gates"]["layer_policy"]["ok"], False)
        finally:
            if home is None:
                os.environ.pop("PCB_AGENT_HOME", None)
            else:
                os.environ["PCB_AGENT_HOME"] = home
            shutil.rmtree(tmp, ignore_errors=True)
