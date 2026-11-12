"""The board's own plane pair: which two layers, how far apart, how good a plate.

`plane_resonance` has always answered "where are the modes" from three typed
numbers. The three numbers are all in the file - two copper layers, the stackup
between them, the outline - so what is tested here is the READING, not the mode
formula (that is test_cavity.py's job):

* the cavity is the outline's BOUNDING BOX, and the answer says so with the
  outline's own area beside it;
* er is the capacitance-equivalent average of every dielectric sublayer between
  the two plates (a layer count is not a number);
* a board that cannot name two poured layers, and a stackup that states no er,
  are REFUSALS - 4.3 would silently set every mode.

Measured on real demo boards: stickhub (B.Cu/F.Cu, 1.51 mm, er 4.5, 16.5 x 40
mm box) lowest mode (0,1) at 1.76654 GHz, f/f_mode 1.35858 at 2.4 GHz;
royalblue54L_feather (In6.Cu/In5.Cu, 0.3 mm) lowest mode (1,0) at 1.20955 GHz;
ecc83 has one poured layer and is refused.
"""
import math
import os
import sys
import tempfile
import unittest

sys.path.insert(0, os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "src"))

from pcb_agent_mcp.adapters import kicad_pcb as K  # noqa: E402
from pcb_agent_mcp.sim import planes as P  # noqa: E402

C_LIGHT = 299792458.0

STACKUP_2 = """  (setup (stackup
      (layer "F.Cu" (type "copper") (thickness 0.035))
      (layer "dielectric 1" (type "core") (thickness 1.51) (epsilon_r 4.5))
      (layer "B.Cu" (type "copper") (thickness 0.035))))"""

STACKUP_NO_ER = """  (setup (stackup
      (layer "F.Cu" (type "copper") (thickness 0.035))
      (layer "dielectric 1" (type "core") (thickness 1.51))
      (layer "B.Cu" (type "copper") (thickness 0.035))))"""

STACKUP_4 = """  (setup (stackup
      (layer "F.Cu" (type "copper") (thickness 0.035))
      (layer "dielectric 1" (type "prepreg") (thickness 0.1) (epsilon_r 4.2))
      (layer "In1.Cu" (type "copper") (thickness 0.035))
      (layer "dielectric 2" (type "core") (thickness 1.2) (epsilon_r 4.6))
      (layer "In2.Cu" (type "copper") (thickness 0.035))
      (layer "dielectric 3" (type "prepreg") (thickness 0.1) (epsilon_r 4.2))
      (layer "B.Cu" (type "copper") (thickness 0.035))))"""

def _pour(layer, net, net_name, pts, keepout=False):
    body = ('  (zone (net %d) (net_name "%s") (layer "%s") (tstamp 1) (hatch edge 0.5)\n'
            '    (connect_pads (clearance 0.2))\n'
            '    (min_thickness 0.25)\n'
            % (net, net_name, layer))
    if keepout:
        body += '    (keepout (tracks not_allowed))\n'
    body += '    (polygon (pts %s))\n' % " ".join("(xy %s %s)" % (x, y) for x, y in pts)
    body += '    (filled_polygon (layer "%s") (pts %s)))' % (
        layer, " ".join("(xy %s %s)" % (x, y) for x, y in pts))
    return body

RING = [(0, 0), (20, 0), (20, 10), (0, 10)]

def _board(stackup=STACKUP_2, pours=None, four=False, outline=True):
    layers = ('(0 "F.Cu" signal) (4 "In1.Cu" signal) (6 "In2.Cu" signal) '
              '(31 "B.Cu" signal) (44 "Edge.Cuts" user)') if four else \
             '(0 "F.Cu" signal) (31 "B.Cu" signal) (44 "Edge.Cuts" user)'
    text = ['(kicad_pcb (version 20221018) (generator pcbnew)',
            '  (general (thickness 1.6))',
            '  (layers %s)' % layers,
            stackup,
            '  (net 0 "") (net 1 "VCC") (net 2 "GND")']
    if outline:
        text.append('  (gr_rect (start 0 0) (end 20 10) (layer "Edge.Cuts") '
                    '(width 0.1))')
    text.extend(pours or [])
    text.append(')')
    path = os.path.join(tempfile.gettempdir(),
                        "board_planes_%d_%d.kicad_pcb" % (os.getpid(), len(text)))
    with open(path, "w", newline="") as fh:
        fh.write("\n".join(text) + "\n")
    return K.parse_board(path)

BOTH = [_pour("F.Cu", 2, "GND", RING), _pour("B.Cu", 2, "GND", RING)]

class TestTheCavityIsTheOutlineBox(unittest.TestCase):
    def test_the_box_is_the_outline_and_the_areas_are_both_reported(self):
        b = _board(pours=BOTH)
        box = P.outline_box(b.outline)
        self.assertAlmostEqual(box[0], 0.0, places=9)
        # the adapter turns KiCad's downward y into board y, so the box's origin
        # is (0, -10) here; width and height are what the cavity uses
        self.assertAlmostEqual(box[1] + box[3], 0.0, places=6)
        self.assertAlmostEqual(box[2], 20.0, places=6)
        self.assertAlmostEqual(box[3], 10.0, places=6)
        self.assertAlmostEqual(P.outline_area(b.outline), 200.0, places=6)

    def test_nesting_does_not_change_an_area(self):
        # the same ring arrives three ways depending on the adapter: a bare
        # ring, a list of rings, rings with a hole one deeper
        ring = [(0, 0), (4, 0), (4, 3), (0, 3)]
        self.assertAlmostEqual(P.outline_area(ring), 12.0, places=9)
        self.assertAlmostEqual(P.outline_area([ring]), 12.0, places=9)
        self.assertAlmostEqual(P.outline_area([[ring]]), 12.0, places=9)
        # a lone point is not a ring, and a hole is not a negative area here
        self.assertAlmostEqual(P.outline_area([[0, 0], [1, 1]]), 0.0, places=9)

class TestTheDielectricIsBetweenThePlates(unittest.TestCase):
    def test_one_sublayer_is_its_thickness_and_its_er(self):
        d = P.layer_dielectric(_board(), "F.Cu", "B.Cu")
        self.assertAlmostEqual(d["thickness_mm"], 1.51, places=9)
        self.assertAlmostEqual(d["er"], 4.5, places=9)
        self.assertEqual(d["layers_between"], 1)
        self.assertEqual(d["missing_er_in"], [])

    def test_several_sublayers_are_a_series_average_not_a_plain_mean(self):
        d = P.layer_dielectric(_board(STACKUP_4, four=True), "F.Cu", "In2.Cu")
        self.assertAlmostEqual(d["thickness_mm"], 1.3, places=9)
        series = 0.1 / 4.2 + 1.2 / 4.6          # the capacitance-equivalent er
        self.assertAlmostEqual(d["er"], 1.3 / series, places=9)
        # the plain mean (4.4) is NOT the answer: it understates the gap's
        # capacitance, and every mode would move with it
        self.assertNotAlmostEqual(d["er"], 0.5 * (4.2 + 4.6), places=2)
        self.assertEqual(d["layers_between"], 3)   # one copper foil in between

    def test_a_missing_er_is_named_rather_than_replaced(self):
        d = P.layer_dielectric(_board(STACKUP_NO_ER), "F.Cu", "B.Cu")
        self.assertAlmostEqual(d["thickness_mm"], 1.51, places=9)
        self.assertIsNone(d["er"])
        self.assertEqual(d["missing_er_in"], ["dielectric 1"])

class TestACoverageIsMeasuredAgainstTheBox(unittest.TestCase):
    def test_a_full_pour_is_one(self):
        cov = P.plane_coverage(_board(pours=BOTH), "F.Cu", (0, 0, 20.0, 10.0))
        self.assertAlmostEqual(cov["coverage_of_box"], 1.0, places=9)
        self.assertEqual(cov["biggest_net"], "GND")
        self.assertAlmostEqual(cov["copper_area_mm2"], 200.0, places=6)

    def test_a_half_pour_and_a_keepout_are_not_copper(self):
        half = _pour("F.Cu", 2, "GND", [(0, 0), (10, 0), (10, 10), (0, 10)])
        barrier = _pour("F.Cu", 0, "", [(0, 0), (20, 0), (20, 5), (0, 5)],
                        keepout=True)
        cov = P.plane_coverage(_board(pours=[half, barrier]), "F.Cu",
                               (0, 0, 20.0, 10.0))
        self.assertAlmostEqual(cov["coverage_of_box"], 0.5, places=9)
        self.assertEqual(cov["biggest_net"], "GND")

class TestWhatIsRefused(unittest.TestCase):
    def test_a_board_with_one_poured_layer_has_no_pair_and_says_so(self):
        out = P.board_plane_resonance(
            _board(pours=[_pour("B.Cu", 2, "GND", RING)]), {})
        self.assertEqual(out["status"], "error")
        self.assertEqual(out["reason_code"], "NOT_APPLICABLE")
        found = (out.get("extra") or {}).get("pours_found") or []
        self.assertEqual([f["layer"] for f in found], ["B.Cu"])

    def test_a_stackup_without_er_is_a_refusal_not_a_4_3(self):
        out = P.board_plane_resonance(_board(STACKUP_NO_ER, pours=BOTH), {})
        self.assertEqual(out["status"], "error")
        self.assertEqual(out["reason_code"], "ARG_MISSING")
        self.assertIn("dielectric constant", out["message"])
        self.assertEqual((out.get("extra") or {}).get("missing_er_in"),
                         ["dielectric 1"])

    def test_a_layer_pair_must_be_named_in_full_and_must_be_copper(self):
        b = _board(pours=BOTH)
        half = P.board_plane_resonance(b, {"layer_a": "F.Cu"})
        self.assertEqual(half["reason_code"], "ARG_MISSING")
        same = P.board_plane_resonance(b, {"layer_a": "F.Cu", "layer_b": "F.Cu"})
        self.assertEqual(same["reason_code"], "ARG_INVALID")
        name = P.board_plane_resonance(b, {"layer_a": "F.Cu", "layer_b": "In3.Cu"})
        self.assertEqual(name["reason_code"], "NOT_FOUND")

    def test_no_outline_means_no_cavity(self):
        out = P.board_plane_resonance(_board(pours=BOTH, outline=False), {})
        self.assertEqual(out["reason_code"], "NOT_APPLICABLE")

class TestOneRealAnswer(unittest.TestCase):
    def test_the_lowest_mode_is_the_box_and_the_stackup(self):
        out = P.board_plane_resonance(_board(pours=BOTH),
                                      {"f_operate_hz": 2.4e9})
        self.assertEqual(out["status"], "ok", out)
        v = out["value"]
        b = v["board"]
        self.assertEqual(b["layer_a"], "F.Cu")
        self.assertEqual(b["layer_b"], "B.Cu")
        self.assertTrue(b["pair_from_file"])
        self.assertTrue(b["cavity_is_the_box"])
        self.assertAlmostEqual(b["dielectric_mm"], 1.51, places=9)
        self.assertAlmostEqual(b["er"], 4.5, places=9)
        self.assertAlmostEqual(b["outline_area_mm2"], 200.0, places=6)
        low = v["lowest_mode"]
        self.assertEqual((low["m"], low["n"]), (1, 0))
        # f_10 over 20 mm of er 4.5 is the physics the file supplied
        want = C_LIGHT / (2.0 * math.sqrt(4.5)) / 0.020
        self.assertAlmostEqual(low["f_hz"] / want, 1.0, places=4)
        self.assertAlmostEqual(v["operating_point"]["f_operate_over_f_mode"],
                               2.4e9 / low["f_hz"], places=3)
        # the box and the copper are the same ring here, so nothing is warned
        self.assertEqual([n for n in (out.get("notes") or [])
                          if "BOUNDING BOX" in n].__len__(), 1)

if __name__ == "__main__":
    unittest.main()
