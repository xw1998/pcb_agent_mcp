"""Crosstalk between two nets, found on the board and solved once.

Two halves, deliberately separated because they fail differently:

* the SEARCH (`coupled_runs`, `reference_plane`) is pure geometry and is tested
  hard - parallel within an angle, edge-to-edge gap, longitudinal overlap - so a
  run that does not exist cannot be invented; a tool that reports crosstalk
  between traces that merely look near each other is worse than no tool;
* the SOLVE is the old 2-D field engine and is exercised once, with a coarse
  mesh, because a 0.15 mm pair costs minutes (measured: 285.8 s at mesh_div 8).
  What is asserted there is that it runs on the numbers the file gave, not that
  the physics is right - that is `test_crosstalk.py`'s job.
"""
import os
import sys
import tempfile
import unittest

sys.path.insert(0, os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "src"))

from pcb_agent_mcp.adapters import kicad_pcb as K  # noqa: E402
from pcb_agent_mcp.sim import couple as C  # noqa: E402

STACKUP_2 = """  (setup (stackup
      (layer "F.Cu" (type "copper") (thickness 0.035))
      (layer "dielectric 1" (type "core") (thickness 1.51) (epsilon_r 4.5)
             (loss_tangent 0.02))
      (layer "B.Cu" (type "copper") (thickness 0.035))))"""

STACKUP_4 = """  (setup (stackup
      (layer "F.Cu" (type "copper") (thickness 0.035))
      (layer "dielectric 1" (type "prepreg") (thickness 0.1) (epsilon_r 4.2))
      (layer "In1.Cu" (type "copper") (thickness 0.035))
      (layer "dielectric 2" (type "core") (thickness 1.2) (epsilon_r 4.6))
      (layer "In2.Cu" (type "copper") (thickness 0.035))
      (layer "dielectric 3" (type "prepreg") (thickness 0.1) (epsilon_r 4.2))
      (layer "B.Cu" (type "copper") (thickness 0.035))))"""


def _seg(x1, y1, x2, y2, w, net, layer="F.Cu"):
    return ('  (segment (start %s %s) (end %s %s) (width %s) (layer "%s") '
            '(net %s))' % (x1, y1, x2, y2, w, layer, net))


def _board(segs, four_layer=False):
    stack = STACKUP_4 if four_layer else STACKUP_2
    layers = ('(0 "F.Cu" signal) (4 "In1.Cu" signal) (6 "In2.Cu" signal) '
              '(31 "B.Cu" signal) (44 "Edge.Cuts" user)') if four_layer else \
             '(0 "F.Cu" signal) (31 "B.Cu" signal) (44 "Edge.Cuts" user)'
    text = ['(kicad_pcb (version 20221018) (generator pcbnew)',
            '  (general (thickness 1.6))',
            '  (layers %s)' % layers,
            stack,
            '  (net 0 "") (net 1 "A") (net 2 "B") (net 3 "GND")',
            '  (gr_rect (start 0 0) (end 20 10) (layer "Edge.Cuts") (width 0.1))']
    text.extend(segs)
    text.append(')')
    path = os.path.join(tempfile.gettempdir(),
                        "board_couple_%d_%d.kicad_pcb" % (os.getpid(), len(segs)))
    with open(path, "w", newline="") as fh:
        fh.write("\n".join(text) + "\n")
    return K.parse_board(path)


PAIR = [_seg(2, 4, 8, 4, 0.3, 1),
        _seg(2, 4.6, 8, 4.6, 0.3, 2),
        _seg(2, 8, 8, 8, 1.0, 3, "B.Cu")]


class TestTheSearchFindsRunsThatExist(unittest.TestCase):
    def test_a_parallel_run_is_one_run_with_its_own_gap(self):
        runs = C.coupled_runs(C._tracks(_board(PAIR), "A", "F.Cu"),
                              C._tracks(_board(PAIR), "B", "F.Cu"))
        self.assertEqual(len(runs), 1, runs)
        self.assertAlmostEqual(runs[0]["gap_mm"], 0.3, places=9)   # 0.6 - 0.3
        self.assertAlmostEqual(runs[0]["overlap_mm"], 6.0, places=9)
        self.assertAlmostEqual(runs[0]["angle_deg"], 0.0, places=6)

    def test_a_crossing_trace_is_not_a_run(self):
        b = _board([_seg(2, 4, 8, 4, 0.3, 1), _seg(5, 1, 5, 7, 0.3, 2)])
        self.assertEqual(C.coupled_runs(C._tracks(b, "A", "F.Cu"),
                                        C._tracks(b, "B", "F.Cu")), [])

    def test_a_gap_beyond_the_limit_is_not_a_run(self):
        b = _board([_seg(2, 4, 8, 4, 0.3, 1), _seg(2, 5.0, 8, 5.0, 0.3, 2)])
        self.assertEqual(C.coupled_runs(C._tracks(b, "A", "F.Cu"),
                                        C._tracks(b, "B", "F.Cu"),
                                        max_gap_mm=0.5), [])

    def test_a_short_overlap_is_not_a_run(self):
        b = _board([_seg(2, 4, 8, 4, 0.3, 1), _seg(7.5, 4.6, 9, 4.6, 0.3, 2)])
        self.assertEqual(C.coupled_runs(C._tracks(b, "A", "F.Cu"),
                                        C._tracks(b, "B", "F.Cu"),
                                        min_overlap_mm=2.0), [])

    def test_touching_copper_is_a_zero_gap_not_a_negative_one(self):
        b = _board([_seg(2, 4, 8, 4, 0.3, 1), _seg(2, 4.3, 8, 4.3, 0.3, 2)])
        runs = C.coupled_runs(C._tracks(b, "A", "F.Cu"),
                              C._tracks(b, "B", "F.Cu"))
        self.assertEqual(len(runs), 1)
        self.assertEqual(runs[0]["gap_mm"], 0.0)


class TestTheReferencePlaneComesFromTheStackup(unittest.TestCase):
    def test_an_outer_layer_is_a_microstrip(self):
        ref = C.reference_plane(_board(PAIR), "F.Cu")
        self.assertTrue(ref["outer"])
        self.assertEqual(ref["reference_below"], "B.Cu")
        self.assertIsNone(ref["reference_above"])
        self.assertAlmostEqual(ref["dielectric_mm"], 1.51, places=9)
        self.assertAlmostEqual(ref["er"], 4.5, places=9)

    def test_an_inner_layer_is_a_stripline_and_takes_the_nearer_side(self):
        ref = C.reference_plane(_board(PAIR, four_layer=True), "In1.Cu")
        self.assertFalse(ref["outer"])
        self.assertEqual(ref["nearest"], "above")     # 0.1 mm to F.Cu, 1.2 to In2
        self.assertAlmostEqual(ref["dielectric_mm"], 0.1, places=9)
        self.assertAlmostEqual(ref["er"], 4.2, places=9)
        self.assertAlmostEqual(ref["both_sides"]["below"]["dielectric_mm"], 1.2,
                               places=9)


class TestWhatIsRefused(unittest.TestCase):
    def test_a_rise_time_is_required(self):
        out = C.board_crosstalk(_board(PAIR), {"net_a": "A", "net_b": "B"})
        self.assertEqual(out["status"], "error", out)
        self.assertEqual(out["reason_code"], "ARG_MISSING")
        self.assertIn("rise_time_ns", out["message"])

    def test_a_net_does_not_couple_to_itself(self):
        out = C.board_crosstalk(_board(PAIR), {"net_a": "A", "net_b": "A",
                                               "rise_time_ns": 0.5})
        self.assertEqual(out["reason_code"], "ARG_INVALID")

    def test_nets_that_never_share_a_layer_are_refused(self):
        b = _board([_seg(2, 4, 8, 4, 0.3, 1, "F.Cu"),
                    _seg(2, 4.6, 8, 4.6, 0.3, 2, "B.Cu")])
        out = C.board_crosstalk(b, {"net_a": "A", "net_b": "B",
                                    "rise_time_ns": 0.5})
        self.assertEqual(out["reason_code"], "NOT_APPLICABLE")
        self.assertIn("share a copper layer", out["message"])

    def test_no_qualifying_run_reports_the_closest_one_instead(self):
        b = _board([_seg(2, 4, 8, 4, 0.3, 1), _seg(2, 6, 8, 6, 0.3, 2)])
        out = C.board_crosstalk(b, {"net_a": "A", "net_b": "B",
                                    "rise_time_ns": 0.5, "max_gap_mm": 0.5})
        self.assertEqual(out["reason_code"], "NOT_APPLICABLE")
        near = out["extra"]["closest_parallel"]
        self.assertEqual(len(near), 1)
        self.assertAlmostEqual(near[0]["gap_mm"], 1.7, places=9)

    def test_two_different_widths_are_refused_because_the_solver_has_one(self):
        b = _board([_seg(2, 4, 8, 4, 0.3, 1), _seg(2, 4.5, 8, 4.5, 0.6, 2)])
        out = C.board_crosstalk(b, {"net_a": "A", "net_b": "B",
                                    "rise_time_ns": 0.5})
        self.assertEqual(out["reason_code"], "ARG_INVALID")
        self.assertIn("ONE width", out["message"])


class TestOneRealSolve(unittest.TestCase):
    """Slow: the field engine costs 30 s to 5 min, so this is ONE coarse run."""

    def test_the_pair_on_the_board_is_the_pair_that_is_solved(self):
        out = C.board_crosstalk(_board(PAIR),
                                {"net_a": "A", "net_b": "B",
                                 "rise_time_ns": 0.5, "mesh_div": 6,
                                 "samples_per_delay": 20, "waveform_points": 21})
        self.assertEqual(out["status"], "ok", out)
        v = out["value"]
        info = v["board"]
        self.assertEqual(info["layer"], "F.Cu")
        self.assertEqual(info["kind"], "microstrip")
        self.assertAlmostEqual(info["width_mm"], 0.3, places=9)
        self.assertAlmostEqual(info["spacing_mm"], 0.3, places=9)
        self.assertAlmostEqual(info["length_mm"], 6.0, places=9)
        self.assertAlmostEqual(info["dielectric_mm"], 1.51, places=9)
        self.assertEqual(info["candidate_runs"], 1)
        self.assertIn("next", v["crosstalk"])
        self.assertIn("fext", v["crosstalk"])


if __name__ == "__main__":
    unittest.main()
