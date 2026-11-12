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
from pcb_agent_mcp.calc import cavity as CAV  # noqa: E402
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

STACKUP_TD = """  (setup (stackup
      (layer "F.Cu" (type "copper") (thickness 0.035))
      (layer "dielectric 1" (type "core") (thickness 1.51) (epsilon_r 4.5)
             (loss_tangent 0.02))
      (layer "B.Cu" (type "copper") (thickness 0.035))))"""

STACKUP_TD_MIX = """  (setup (stackup
      (layer "F.Cu" (type "copper") (thickness 0.035))
      (layer "dielectric 1" (type "prepreg") (thickness 0.1) (epsilon_r 4.2)
             (loss_tangent 0.01))
      (layer "In1.Cu" (type "copper") (thickness 0.035))
      (layer "dielectric 2" (type "core") (thickness 1.2) (epsilon_r 4.6)
             (loss_tangent 0.022))
      (layer "dielectric 3" (type "prepreg") (thickness 0.1) (epsilon_r 4.2)
             (loss_tangent 0.01))
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

    def test_a_layer_that_is_two_permille_copper_is_not_a_plate(self):
        # measured on a real demo board (complex_hierarchy): the second poured
        # layer carries one small pour, so scoring alone would name a 0.2 %
        # copper layer as the other side of a cavity
        tiny = _pour("F.Cu", 2, "GND", [(0, 0), (0.4, 0), (0.4, 10), (0, 10)])
        b = _board(pours=[tiny, _pour("B.Cu", 2, "GND", RING)])
        out = P.board_plane_resonance(b, {})
        self.assertEqual(out["reason_code"], "NOT_APPLICABLE")
        self.assertIn("not a plate", out["message"])
        # the escape hatch: naming the pair is the caller's assumption, and it wins
        named = P.board_plane_resonance(b, {"layer_a": "B.Cu", "layer_b": "F.Cu"})
        self.assertEqual(named["status"], "ok", named)
        self.assertFalse(named["value"]["board"]["pair_from_file"])

    def test_a_file_with_no_stackup_says_that_rather_than_no_pours(self):
        out = P.board_plane_resonance(_board("", pours=BOTH), {})
        self.assertEqual(out["reason_code"], "NOT_APPLICABLE")
        self.assertIn("states no stackup", out["message"])
        self.assertEqual((out.get("extra") or {}).get("copper_layers"), [])

    def test_no_outline_means_no_cavity(self):
        out = P.board_plane_resonance(_board(pours=BOTH, outline=False), {})
        self.assertEqual(out["reason_code"], "NOT_APPLICABLE")

class TestTheModesAreDamped(unittest.TestCase):
    """A mode location is not a ringing level: Q is a second question.

    Three mechanisms, three numbers, and 1/Q = 1/Qc + 1/Qd + 1/Qrad because they
    all see the same stored energy. What is NOT in it is what is soldered across
    the planes, which is usually the dominant loss of a real PDN resonance.
    """

    def test_each_mode_carries_its_three_losses_and_their_reciprocal_sum(self):
        out = P.board_plane_resonance(_board(STACKUP_TD, pours=BOTH), {})
        self.assertEqual(out["status"], "ok", out)
        rows = out["value"]["modes"]
        self.assertTrue(rows)
        for r in rows:
            d = r["damping"]
            self.assertGreater(d["q_conductor"], 0.0)
            self.assertGreater(d["q_radiation"], 0.0)
            self.assertAlmostEqual(d["q_dielectric"], 50.0, places=9)  # 1/0.02
            want = 1.0 / (1.0 / d["q_conductor"] + 1.0 / d["q_dielectric"]
                          + 1.0 / d["q_radiation"])
            self.assertAlmostEqual(d["q_total"], want, places=9)
            self.assertEqual(d["losses_in_q_total"],
                             ["q_conductor", "q_dielectric", "q_radiation"])
            self.assertAlmostEqual(d["h_mm"], 1.51, places=9)
        b = out["value"]["board"]
        self.assertAlmostEqual(b["tan_delta"], 0.02, places=9)
        self.assertEqual(b["tan_delta_missing_in"], [])
        # the q_conductor in the answer IS cavity.plane_q's, not a second formula
        want = CAV.plane_q(rows[0]["f_hz"], 1.51, tan_delta=0.02)["value"]
        self.assertAlmostEqual(rows[0]["damping"]["q_conductor"],
                               want["q_conductor"], places=9)
        # Qc = h/delta_s moves with FREQUENCY only (delta ~ 1/sqrt(f)), never with
        # the mode numbers: 1358.15 at 3.533 GHz against 2872.14 at 15.80 GHz is
        # 2.1146, and sqrt(15.80/3.533) is 2.1147
        self.assertAlmostEqual(
            rows[-1]["damping"]["q_conductor"] / rows[0]["damping"]["q_conductor"],
            math.sqrt(rows[-1]["f_hz"] / rows[0]["f_hz"]), places=3)
        self.assertGreater(len({round(r["damping"]["q_radiation"], 6)
                                for r in rows}), 1)

    def test_the_total_is_lower_than_every_part_and_radiation_can_win(self):
        # Measured on this box (20 x 10 mm, 1.51 mm of er 4.5): the (1,0) mode is
        # 3.533 GHz with Qc 1358.15, Qd 50 and Qrad 7.45033, so 1/Q adds up to
        # 6.45 and RADIATION is what damps it - a small board on a thick gap
        # radiates, which is why the split is printed instead of one number.
        # Written as "the total is below every part" so it stays true whichever
        # mechanism wins, and the parts are asserted separately.
        out = P.board_plane_resonance(_board(STACKUP_TD, pours=BOTH), {})
        d = out["value"]["modes"][0]["damping"]
        self.assertAlmostEqual(d["q_radiation"], 7.45033, places=4)
        self.assertAlmostEqual(d["q_dielectric"], 50.0, places=9)
        self.assertGreater(d["q_conductor"], 1300.0)
        self.assertAlmostEqual(d["q_total"], 6.453339142, places=5)
        for k in ("q_conductor", "q_dielectric", "q_radiation"):
            self.assertLess(d["q_total"], d[k])

    def test_sublayers_are_weighted_by_capacitance(self):
        # t/er weights: 0.1/4.2 at 0.01, 1.2/4.6 at 0.022, 0.1/4.2 at 0.01
        w = [0.1 / 4.2, 1.2 / 4.6, 0.1 / 4.2]
        want = (w[0] * 0.01 + w[1] * 0.022 + w[2] * 0.01) / sum(w)
        out = P.board_plane_resonance(_board(STACKUP_TD_MIX, four=True, pours=[
            _pour("F.Cu", 2, "GND", RING), _pour("B.Cu", 2, "GND", RING)]),
            {"layer_a": "F.Cu", "layer_b": "B.Cu"})
        self.assertEqual(out["status"], "ok", out)
        self.assertAlmostEqual(out["value"]["board"]["tan_delta"], want, places=9)
        # measured 0.020147651006711: NOT the thickness-weighted 0.0202857 and
        # not the plain 0.014 - the energy that sets the loss is in the
        # capacitance, so t/er is the weight
        self.assertAlmostEqual(want, 0.020147651006711, places=9)
        thick_weighted = (0.1 * 0.01 + 1.2 * 0.022 + 0.1 * 0.01) / 1.4
        self.assertNotAlmostEqual(want, thick_weighted, places=4)
        self.assertNotAlmostEqual(want, (0.01 + 0.022 + 0.01) / 3.0, places=3)

    def test_a_stackup_with_no_loss_tangent_says_the_q_is_optimistic(self):
        out = P.board_plane_resonance(_board(pours=BOTH), {})   # STACKUP_2: no td
        d = out["value"]["modes"][0]["damping"]
        self.assertIsNone(d["q_dielectric"])
        self.assertEqual(d["losses_in_q_total"], ["q_conductor", "q_radiation"])
        self.assertEqual(out["value"]["board"]["tan_delta_missing_in"],
                         ["dielectric 1"])
        self.assertTrue(any("counted as lossless" in n
                            for n in (out.get("notes") or [])))

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
