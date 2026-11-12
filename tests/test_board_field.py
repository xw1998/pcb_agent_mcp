"""J on the board's own copper, and the 1-D answer beside it.

The field solver is old and already tested (`tests/test_currentfield.py`); what
is new is the region and the contacts coming out of a .kicad_pcb. So these tests
are about the wiring, and about the two numbers not silently becoming one:

* a straight strip of known geometry must come out at rho*L/(w*t) - if the
  region were built wrong (a gap under a pad, a track dropped, a width ignored)
  this is the assertion that notices;
* halving the width must double it, exactly;
* the pad funnel must show up in the peak J that the 1-D answer cannot see, and
  both must be printed in the same answer;
* and every way to ask about copper that is not there is a refusal, not a zero.
"""
import os
import sys
import tempfile
import unittest

sys.path.insert(0, os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "src"))

from pcb_agent_mcp.adapters import kicad_pcb as K  # noqa: E402
from pcb_agent_mcp.sim import field as F  # noqa: E402

# 30 mm of 1 mm 35 um copper: R = 1.724e-5 ohm*mm * 30 / (1.0*0.035)
RHO_MM = 1.724e-5
STRIP_R = RHO_MM * 30.0 / (1.0 * 0.035)

STACKUP = """  (setup (stackup
      (layer "F.Cu" (type "copper") (thickness 0.035))
      (layer "dielectric 1" (type "core") (thickness 1.51) (epsilon_r 4.5))
      (layer "B.Cu" (type "copper") (thickness 0.035))))"""

PADS = """  (footprint "T:A" (layer "F.Cu") (at 5 10)
    (fp_text reference "U1" (at 0 -2) (layer "F.SilkS"))
    (pad "1" smd rect (at 0 0) (size 2 2) (layers "F.Cu") (net 1 "VCC")))
  (footprint "T:B" (layer "F.Cu") (at 35 10)
    (fp_text reference "U2" (at 0 -2) (layer "F.SilkS"))
    (pad "1" smd rect (at 0 0) (size 2 2) (layers "F.Cu") (net 1 "VCC")))
  (footprint "T:C" (layer "F.Cu") (at 20 3)
    (fp_text reference "U3" (at 0 -2) (layer "F.SilkS"))
    (pad "1" smd rect (at 0 0) (size 2 2) (layers "F.Cu") (net 2 "GND")))"""


def _board(width_mm=1.0, extra=""):
    text = ['(kicad_pcb (version 20221018) (generator pcbnew)',
            '  (general (thickness 1.6))',
            '  (layers (0 "F.Cu" signal) (31 "B.Cu" signal) (44 "Edge.Cuts" user))',
            STACKUP,
            '  (net 0 "") (net 1 "VCC") (net 2 "GND")',
            '  (gr_rect (start 0 0) (end 40 20) (layer "Edge.Cuts") (width 0.1))',
            PADS,
            '  (segment (start 5 10) (end 35 10) (width %s) (layer "F.Cu") '
            '(net 1))' % width_mm,
            extra,
            ')']
    path = os.path.join(tempfile.gettempdir(),
                        "board_field_%d_%d.kicad_pcb" % (os.getpid(), id(text)))
    with open(path, "w", newline="") as fh:
        fh.write("\n".join(text) + "\n")
    return K.parse_board(path)


POUR = """  (zone (net 1) (net_name "VCC") (layer "F.Cu") (hatch edge 0.5)
    (connect_pads yes (clearance 0.2)) (min_thickness 0.2)
    (fill yes (thermal_gap 0.5) (thermal_bridge_width 0.5))
    (polygon (pts (xy 2 8) (xy 38 8) (xy 38 12) (xy 2 12)))
    (filled_polygon (layer "F.Cu") (pts (xy 2 8) (xy 38 8) (xy 38 12) (xy 2 12))))"""

def _pour_board():
    """VCC between U1.1 and U2.1 with NO track at all: the pour is the copper."""
    text = ['(kicad_pcb (version 20221018) (generator pcbnew)',
            '  (general (thickness 1.6))',
            '  (layers (0 "F.Cu" signal) (31 "B.Cu" signal) (44 "Edge.Cuts" user))',
            STACKUP,
            '  (net 0 "") (net 1 "VCC") (net 2 "GND")',
            '  (gr_rect (start 0 0) (end 40 20) (layer "Edge.Cuts") (width 0.1))',
            PADS,
            POUR,
            ')']
    path = os.path.join(tempfile.gettempdir(),
                        "board_field_pour_%d.kicad_pcb" % os.getpid())
    with open(path, "w", newline="") as fh:
        fh.write("\n".join(text) + "\n")
    return K.parse_board(path)

ONE_A = {"net": "VCC", "source_pad": "U2.1", "sink_pad": "U1.1"}


def _field(board, **kw):
    args = dict(ONE_A)
    args.update(kw)
    return F.board_current_field(board, args)


class TestTheRegionIsTheBoard(unittest.TestCase):
    def test_a_straight_strip_comes_out_at_rho_l_over_w_t(self):
        out = _field(_board())
        self.assertEqual(out["status"], "ok", out)
        v = out["value"]
        self.assertEqual(v["board"]["layer"], "F.Cu")
        self.assertEqual(v["board"]["paths"], 1)
        self.assertEqual(v["board"]["pad_polygons"], 2)
        self.assertAlmostEqual(v["board"]["copper_um"], 35.0, places=9)
        # The contact is a disc of the pad's size, so the gauge length is the
        # strip minus one radius at each end: 30 - 2*0.8 = 28.4 mm. Measured
        # 0.013989 ohm against 0.013989 for rho*28.4/(w*t) - the strip alone
        # would be 0.014777, and reporting THAT would mean the contact was not
        # the pad.
        r = v["resistance_ohm"]
        rad = v["board"]["contacts"]["U1.1"]["radius_mm"]
        self.assertAlmostEqual(rad, 0.8, places=9)
        self.assertLess(abs(r - RHO_MM * (30.0 - 2 * rad) / 0.035) / r, 0.02,
                        "field %r" % (r,))
        self.assertTrue(v["grid_convergence"]["is_grid_convergent"],
                        v["grid_convergence"])

    def test_halving_the_width_doubles_the_resistance(self):
        wide = _field(_board(1.0))["value"]["resistance_ohm"]
        narrow = _field(_board(0.5))["value"]["resistance_ohm"]
        # 0.027892 against 0.013989: 1.99384, not 2.000, because the pads are the
        # same width in both boards and shunt the narrower strip a little less
        self.assertAlmostEqual(narrow / wide, 2.0, delta=0.02)

    def test_the_pad_funnel_shows_up_in_the_peak(self):
        v = _field(_board())["value"]
        cmp = v["against_the_1d_answer"]
        self.assertIsNotNone(cmp)
        # 1 A across 1 mm x 35 um is 28.6 A/mm^2; the funnel into a 2 mm pad is
        # narrower than that, and the 1-D number cannot see it either way
        self.assertGreater(cmp["peak_j_1d_a_per_mm2"], 0.0)
        self.assertGreater(v["peak_j_a_per_mm2"], 0.0)
        self.assertNotEqual(cmp["peak_j_field_a_per_mm2"],
                            cmp["peak_j_1d_a_per_mm2"])
        self.assertIsNotNone(cmp["relative_difference"])

    def test_the_resistance_is_confirmed_but_the_peak_is_not(self):
        # These are two different questions and the answer must not let one
        # vouch for the other. Measured on this board: R 0.013992 against
        # 0.013986 (0.043 % apart, confirmed) while the peak went 29.7096 ->
        # 30.8407 A/mm^2 when the grid was halved - x1.0381, far inside the
        # x1.2599 a 270 degree corner would give, but well over the x1.02 a
        # resolved peak has to be under. The peak sits on the contact edge,
        # which is an equipotential island with a hard edge and is singular by
        # construction, so it is published as a shape.
        out = _field(_board())
        c = out["value"]["grid_convergence"]
        self.assertTrue(c["is_grid_convergent"], c)
        self.assertIsNotNone(c["h_fine_mm"], c)
        self.assertIsNotNone(c["peak_j_fine_a_per_mm2"], c)
        self.assertIsInstance(c["peak_is_grid_convergent"], bool)
        self.assertFalse(c["peak_is_grid_convergent"], c)
        self.assertGreater(c["peak_growth"], 1.0)
        self.assertLess(c["peak_growth"], c["peak_growth_if_270deg_corner"])
        self.assertAlmostEqual(c["peak_growth_if_270deg_corner"],
                               2.0 ** (1.0 / 3.0), places=12)
        self.assertAlmostEqual(c["peak_convergence_threshold"], 1.02, places=12)
        self.assertAlmostEqual(
            c["peak_j_fine_a_per_mm2"] / c["peak_j_a_per_mm2"],
            c["peak_growth"], places=12)
        peak_note = [n for n in out["notes"] if "PEAK" in n]
        self.assertEqual(len(peak_note), 1, peak_note)
        self.assertIn("NOT confirmed", peak_note[0])
        self.assertIn("not this peak", peak_note[0])
        self.assertIn("x%.4f" % c["peak_growth"], peak_note[0])

    def test_the_same_question_twice_is_the_same_number(self):
        a = _field(_board())["value"]["resistance_ohm"]
        b = _field(_board())["value"]["resistance_ohm"]
        self.assertEqual(a, b)


class TestThePourIsCopper(unittest.TestCase):
    """A plane-heavy net has no track between its pads: the pour IS the path.

    This used to be refused ("a net made only of a pour has no 2-D region here"),
    which is the wrong answer from a field tool - the pour is copper on the net,
    it is usually the only thing joining the two halves, and refusing it means
    the answer is missing exactly on the boards where the question matters.
    """

    def test_a_pour_carries_the_current_when_there_is_no_track(self):
        out = _field(_pour_board())
        self.assertEqual(out["status"], "ok", out)
        v = out["value"]
        self.assertEqual(v["board"]["paths"], 0)
        self.assertEqual(v["board"]["pour_polygons"], 1)
        self.assertEqual(v["board"]["layer"], "F.Cu")
        # 36 x 4 mm of 35 um copper between contacts 30 mm apart: the sheet is
        # ~0.4926 ohm/sq, so a 30/4 = 7.5 square run is ~0.0037 ohm and the fill
        # either side of the contacts makes it lower still
        r = v["resistance_ohm"]
        self.assertGreater(r, 0.0)
        self.assertLess(r, 0.005)
        self.assertIsNotNone(v["board"]["pour_area_mm2"])
        self.assertAlmostEqual(v["board"]["pour_area_mm2"], 144.0, places=6)
        self.assertTrue(any("pour" in n for n in out["notes"]), out["notes"])
        self.assertFalse(any("are not in it" in n and "pours" in n
                             for n in out["notes"]), out["notes"])

    def test_asking_for_no_pour_refuses_a_pour_only_net(self):
        out = _field(_pour_board(), model_pours=False)
        self.assertEqual(out["status"], "error", out)
        self.assertIn(out["reason_code"],
                      ("NOT_APPLICABLE", "NO_GEOMETRY", "NOT_CONNECTED"), out)

class TestWhatIsRefused(unittest.TestCase):
    def test_a_pad_on_another_net_is_refused(self):
        out = _field(_board(), sink_pad="U3.1")
        self.assertEqual(out["status"], "error", out)
        self.assertEqual(out["reason_code"], "ARG_INVALID")
        self.assertIn("not on net", out["message"])

    def test_one_pad_is_not_a_field(self):
        out = _field(_board(), sink_pad="U2.1")
        self.assertEqual(out["status"], "error", out)
        self.assertEqual(out["reason_code"], "ARG_INVALID")

    def test_a_net_with_no_track_is_refused(self):
        out = _field(_board(), net="GND", source_pad="U3.1", sink_pad="U3.1")
        self.assertEqual(out["status"], "error", out)
        self.assertEqual(out["reason_code"], "ARG_INVALID")

    def test_a_layer_with_no_track_on_it_is_refused(self):
        out = _field(_board(), layer="B.Cu")
        self.assertEqual(out["status"], "error", out)
        self.assertEqual(out["reason_code"], "NOT_APPLICABLE")
        self.assertIn("B.Cu", out["message"])

    def test_missing_contacts_are_named(self):
        out = F.board_current_field(_board(), {"net": "VCC"})
        self.assertEqual(out["status"], "error", out)
        self.assertEqual(out["reason_code"], "ARG_MISSING")
        self.assertIn("source_pad", out["message"])


if __name__ == "__main__":
    unittest.main()
