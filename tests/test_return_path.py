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
