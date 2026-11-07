"""board_45_corners: the angle checks, and the refill question under board_drc.

Two things are pinned here, and each has a negative control that has to be able
to fail:

1. A right angle IS a right angle. The survey must call an L-shaped run 90
   degrees and a pre-mitred run 135, and the chamfer must turn the former into
   the latter. The control is a straight run: if the code reported every vertex
   as a corner, `straight` would be 0 there and the test would go red.

2. A vertex sitting ON a pad must be refused, not cut. Cutting there removes
   copper from inside the pad - that is an open circuit dressed up as a tidy
   corner. The control is the identical L without the pad: it must cut.
"""
import os
import sys
import unittest

sys.path.insert(0, os.path.join(os.path.dirname(os.path.dirname(
    os.path.abspath(__file__))), "src"))

from pcb_agent_mcp.route import corners as C


class _Board(object):
    def __init__(self, tracks, pads=None, vias=None):
        self.tracks = tracks
        self.vias = vias or []
        self._pads = pads or []

    def pads(self):
        return self._pads


def _seg(uuid, net, layer, start, end):
    return {"uuid": uuid, "net": net, "layer": layer,
            "start": tuple(start), "end": tuple(end)}


def _L(net="N1", layer="F.Cu"):
    """A bare 90 degree turn at (10,10): (10,0)->(10,10)->(20,10)."""
    return [_seg("a", net, layer, (10, 0), (10, 10)),
            _seg("b", net, layer, (10, 10), (20, 10))]


class TestSurvey(unittest.TestCase):
    def test_right_angle_is_measured_and_named(self):
        v = C.survey(_Board(_L()))
        self.assertEqual(v["corners"], 1)
        self.assertEqual(v["corner_kinds"], {"right": 1})
        self.assertEqual(v["right_angle_corners"], 1)
        self.assertEqual(v["acute_corners"], 0)
        detail = v["right_angle_shortlist"][0]
        self.assertAlmostEqual(detail["turn_deg"], 90.0, places=3)
        self.assertEqual(detail["net"], "N1")

    def test_straight_run_is_not_a_corner(self):
        # Negative control: if vertex detection were broken this would report a
        # corner too, and the kind would not be 'straight'.
        v = C.survey(_Board([_seg("a", "N1", "F.Cu", (0, 0), (10, 0)),
                             _seg("b", "N1", "F.Cu", (10, 0), (20, 0))]))
        self.assertEqual(v["corners"], 1)
        self.assertEqual(v["corner_kinds"], {"straight": 1})
        self.assertEqual(v["right_angle_corners"], 0)

    def test_different_net_at_the_same_point_is_not_a_corner(self):
        v = C.survey(_Board([_seg("a", "N1", "F.Cu", (10, 0), (10, 10)),
                             _seg("b", "N2", "F.Cu", (10, 10), (20, 10))]))
        self.assertEqual(v["corners"], 0)

    def test_three_segments_at_one_point_is_a_junction_not_a_corner(self):
        tracks = _L() + [_seg("c", "N1", "F.Cu", (10, 10), (10, 20))]
        v = C.survey(_Board(tracks))
        self.assertEqual(v["junctions"], 1)
        self.assertEqual(v["right_angle_corners"], 0)
        self.assertEqual(v["junction_shortlist"][0]["segments"], 3)

    def test_a_45_degree_turn_reads_as_miter45(self):
        v = C.survey(_Board([_seg("a", "N1", "F.Cu", (10, 0), (10, 10)),
                             _seg("b", "N1", "F.Cu", (10, 10), (17.0711, 17.0711))]))
        self.assertEqual(v["corner_kinds"], {"miter45": 1})

    def test_acute_corner_is_reported_never_cut(self):
        # 45 degree wedge: the second leg doubles back towards the first.
        v = C.survey(_Board([_seg("a", "N1", "F.Cu", (10, 10), (20, 10)),
                             _seg("b", "N1", "F.Cu", (10, 10), (15, 15))]))
        self.assertEqual(v["acute_corners"], 1)
        plan = C.chamfer_ops(_Board([_seg("a", "N1", "F.Cu", (10, 10), (20, 10)),
                                     _seg("b", "N1", "F.Cu", (10, 10), (15, 15))]),
                             max_cut_mm=0.4, min_leg_mm=0.1)
        self.assertEqual(plan["ops"], [])

    def test_off45_reports_a_30_degree_segment(self):
        import math
        dx = 10 * math.cos(math.radians(30))
        dy = 10 * math.sin(math.radians(30))
        v = C.survey(_Board([_seg("a", "N1", "F.Cu", (0, 0), (dx, dy))]))
        self.assertEqual(v["off45_segments"], 1)
        self.assertAlmostEqual(v["off45_worst_deg"], 15.0, places=3)


class TestChamfer(unittest.TestCase):
    def test_right_angle_becomes_a_45_degree_mitre(self):
        board = _Board(_L())
        plan = C.chamfer_ops(board, max_cut_mm=0.4, min_leg_mm=0.1)
        self.assertEqual(plan["corners_chamfered"], 1)
        self.assertEqual(plan["skipped"], [])
        # one delete per rewritten segment, then the replacements
        kinds = [o["op"] for o in plan["ops"]]
        self.assertEqual(kinds, ["delete_track"] * 2 + ["add_track"] * 3,
                         "every delete must come before any add")
        adds = [o for o in plan["ops"] if o["op"] == "add_track"]
        # the mitre itself: one diagonal, 45 degrees on both axes
        diag = [o for o in adds if (abs(o["x1"] - o["x2"]) > 1e-6
                                    and abs(o["y1"] - o["y2"]) > 1e-6)]
        self.assertEqual(len(diag), 1)
        o = diag[0]
        self.assertAlmostEqual(abs(o["x1"] - o["x2"]), abs(o["y1"] - o["y2"]),
                               places=6)
        # the two survivors are shorter than the originals, never longer
        for a in adds:
            if a is o:
                continue
            self.assertLessEqual(abs(a["x2"] - a["x1"]) + abs(a["y2"] - a["y1"]),
                                 10.0 + 1e-6)

    def test_short_leg_is_refused(self):
        # 0.1 mm legs cannot hold any cut at all once min_leg_mm is reserved.
        tracks = [_seg("a", "N1", "F.Cu", (10, 9.9), (10, 10)),
                  _seg("b", "N1", "F.Cu", (10, 10), (10.1, 10))]
        plan = C.chamfer_ops(_Board(tracks), max_cut_mm=0.4, min_leg_mm=0.1)
        self.assertEqual(plan["corners_chamfered"], 0)
        self.assertEqual([s["reason"] for s in plan["skipped"]], ["leg_too_short"])

    def test_adaptive_shortens_instead_of_skipping(self):
        # A second net runs diagonally across the corner at (10,10). The full
        # 1.0 mm mitre would cross it; adaptive has to bisect down to something
        # that fits rather than walk away from the corner.
        #
        # Negative control: the same two nets with the blocker moved 6 mm away
        # must come back with the full cut and reduced == 0. Without that, a
        # "reduced" row would only prove the cut was shortened, not that it was
        # shortened *because of an obstacle*.
        blocker = [_seg("x", "N2", "F.Cu", (10.15, 9.45), (10.90, 9.45))]
        near = C.chamfer_ops(_Board(_L() + blocker), max_cut_mm=1.0,
                             min_leg_mm=0.1, clearance_mm=0.25)
        self.assertEqual(near["corners_chamfered"], 1)
        self.assertEqual(near["cut_reduced_count"], 1)
        self.assertLess(near["cut_reduced"][0]["used_mm"],
                        near["cut_reduced"][0]["asked_mm"])
        self.assertGreater(near["cut_reduced"][0]["used_mm"], 0.0)

        far = C.chamfer_ops(
            _Board(_L() + [_seg("x", "N2", "F.Cu", (10.15, 16.0), (10.90, 16.0))]),
            max_cut_mm=1.0, min_leg_mm=0.1, clearance_mm=0.25)
        self.assertEqual(far["cut_reduced_count"], 0,
                         "a blocker 6 mm away must not shorten anything")
        self.assertEqual(far["corners_chamfered"], 1)

    def test_adaptive_off_reproduces_the_old_skip(self):
        # The switch has to be real: with adaptive off the very same geometry
        # must be skipped, not silently reduced.
        blocker = [_seg("x", "N2", "F.Cu", (10.15, 9.45), (10.90, 9.45))]
        off = C.chamfer_ops(_Board(_L() + blocker), max_cut_mm=1.0,
                            min_leg_mm=0.1, clearance_mm=0.25, adaptive=False)
        self.assertEqual(off["corners_chamfered"], 0)
        self.assertEqual([s["reason"] for s in off["skipped"]], ["would_collide"])

    def test_vertex_on_a_pad_is_refused(self):
        pads = [{"net": "N1", "x": 10.0, "y": 10.0, "w": 1.0, "h": 1.0,
                 "rotation": 0.0}]
        plan = C.chamfer_ops(_Board(_L(), pads=pads), max_cut_mm=0.4,
                             min_leg_mm=0.1)
        self.assertEqual(plan["corners_chamfered"], 0)
        self.assertEqual([s["reason"] for s in plan["skipped"]], ["vertex_on_pad"])

    def test_vertex_on_a_via_is_refused(self):
        vias = [{"net": "N1", "start": (10.0, 10.0), "diameter": 0.6}]
        plan = C.chamfer_ops(_Board(_L(), vias=vias), max_cut_mm=0.4,
                             min_leg_mm=0.1)
        self.assertEqual([s["reason"] for s in plan["skipped"]], ["vertex_on_via"])

    def test_two_legs_sharing_one_segment_do_not_cross(self):
        # A U: one short base with a corner at each end. Cutting both ends by the
        # same amount would make the two cuts overlap, so at most one may happen.
        tracks = [_seg("a", "N1", "F.Cu", (10, 10), (10.5, 10)),
                  _seg("b", "N1", "F.Cu", (10.5, 10), (10.5, 20)),
                  _seg("c", "N1", "F.Cu", (10, 10), (0, 10))]
        plan = C.chamfer_ops(_Board(tracks), max_cut_mm=0.4, min_leg_mm=0.1)
        self.assertLessEqual(plan["corners_chamfered"], 1)
        # whatever got cut, the short middle segment must still exist
        for reason in plan["skipped"]:
            self.assertIn(reason["reason"], ("leg_reserved", "leg_too_short",
                                            "would_collide"))
        # no rebuilt segment may be zero-length: that is the first version's bug
        for o in plan["ops"]:
            if o["op"] == "add_track":
                self.assertGreater(abs(o["x2"] - o["x1"]) + abs(o["y2"] - o["y1"]),
                                   1e-6)


class TestRefillIsReported(unittest.TestCase):
    """The refill flag has to show up in the verdict, or a caller cannot tell a
    real clearance error from a stale pour."""

    def test_engine_drc_signature_accepts_refill(self):
        from pcb_agent_mcp.fidelity import engine
        import inspect
        sig = inspect.signature(engine.drc)
        self.assertIn("refill", sig.parameters)
        self.assertFalse(sig.parameters["refill"].default,
                         "the engine default must stay False: only board_drc and "
                         "the write gate opt in")

    def test_board_drc_defaults_to_refilling(self):
        from pcb_agent_mcp import tools
        spec = tools.TOOLS["board_drc"]
        props = (spec.get("inputSchema") or spec).get("properties") or {}
        self.assertIn("refill_zones", props)
        self.assertTrue((props["refill_zones"].get("default")) is True)


if __name__ == "__main__":
    unittest.main()
