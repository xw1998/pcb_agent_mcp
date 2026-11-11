"""Where the heat is: the board's copper carrying currents you gave per pad.

`board_thermal` is the first tool here that answers "how hot does THIS board
get", and it can be wrong in ways that still look like numbers. Three are
pinned here:

* The I^2R must be the board's own loss. `board_ir_drop` publishes
  `total_copper_loss_w` for the same source/sink/current, computed inside its
  own solve; `dissipation` reaches the same total by a different route (pad
  currents -> one DC solve -> per-edge power). They agree, or one of them is
  describing a different board.
* A net whose currents do not add up to zero is refused. With no component
  models in a .kicad_pcb, "2 A in, nothing out" is half a circuit - and a plate
  solved from half a circuit is a thermal picture that was invented.
* The plate has to be the plate: outline bounding box, and the pours of the
  file in it (without them the same heat spreads through FR4 alone and the
  answer is several times hotter - which is exactly why they are passed).
"""
import os
import sys
import tempfile
import unittest

sys.path.insert(0, os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "src"))

from pcb_agent_mcp import oracle as O  # noqa: E402
from pcb_agent_mcp.adapters import kicad_pcb as K  # noqa: E402
from pcb_agent_mcp.sim import power as SP  # noqa: E402

PLATING_UM = 25.0
# 20 mm of 0.5 mm 35 um copper: R = 1.724e-8*20e-3/(0.5e-3*35e-6)
STRIP_R = 0.019702857142857143

STACKUP = """  (setup (stackup
      (layer "F.Cu" (type "copper") (thickness 0.035))
      (layer "dielectric 1" (type "core") (thickness 1.51) (epsilon_r 4.5))
      (layer "B.Cu" (type "copper") (thickness 0.035))))"""

POUR = """  (zone (net 2) (net_name "GND") (layer "B.Cu") (tstamp 1) (hatch edge 0.5)
    (connect_pads (clearance 0.2))
    (min_thickness 0.25)
    (fill yes (thermal_gap 0.5) (thermal_bridge_width 0.5))
    (polygon (pts (xy 2 2) (xy 48 2) (xy 48 28) (xy 2 28)))
    (filled_polygon (layer "B.Cu") (pts (xy 2 2) (xy 48 2) (xy 48 28)
                                        (xy 2 28))))"""

# U3 is on VCC with no track anywhere near it: the pad a careless current would
# be put into, and the case where a number would be invented if it were not refused
PADS = """  (footprint "T:A" (layer "F.Cu") (at 10 10)
    (fp_text reference "U1" (at 0 -2) (layer "F.SilkS"))
    (pad "1" smd rect (at 0 0) (size 1 1) (layers "F.Cu") (net 1 "VCC")))
  (footprint "T:B" (layer "F.Cu") (at 30 10)
    (fp_text reference "U2" (at 0 -2) (layer "F.SilkS"))
    (pad "1" smd rect (at 0 0) (size 1 1) (layers "F.Cu") (net 1 "VCC")))
  (footprint "T:C" (layer "F.Cu") (at 10 26)
    (fp_text reference "U3" (at 0 -2) (layer "F.SilkS"))
    (pad "1" smd rect (at 0 0) (size 1 1) (layers "F.Cu") (net 1 "VCC")))
  (footprint "T:G" (layer "F.Cu") (at 40 20)
    (fp_text reference "J1" (at 0 -2) (layer "F.SilkS"))
    (pad "1" smd rect (at 0 0) (size 1 1) (layers "F.Cu") (net 2 "GND")))"""

STRIP = '  (segment (start 10 10) (end 30 10) (width 0.5) (layer "F.Cu") (net 1))'
# A second, separate piece of the SAME net: exactly what a finished board looks
# like when a pour or a link is missing. Measured on ecc83, where net
# Net-(P3-P1) splits and the solve died "singular at 11" - an island is an
# unknown with no equation, not a board defect.
SPLIT_PAD = """  (footprint "T:D" (layer "F.Cu") (at 45 10)
    (fp_text reference "U4" (at 0 -2) (layer "F.SilkS"))
    (pad "1" smd rect (at 0 0) (size 1 1) (layers "F.Cu") (net 1 "VCC")))"""
SPLIT_STRIP = ('  (segment (start 45 10) (end 46 10) (width 0.5) '
               '(layer "F.Cu") (net 1))')
GND_STUB = '  (segment (start 40 20) (end 40 22) (width 0.5) (layer "F.Cu") (net 2))'


def _board(pour=True, outline=True, split=False):
    text = ['(kicad_pcb (version 20221018) (generator pcbnew)',
            '  (general (thickness 1.6))',
            '  (layers (0 "F.Cu" signal) (31 "B.Cu" signal) (44 "Edge.Cuts" user))',
            STACKUP,
            '  (net 0 "") (net 1 "VCC") (net 2 "GND")']
    if outline:
        text.append('  (gr_rect (start 0 0) (end 50 30) (layer "Edge.Cuts") '
                    '(width 0.1))')
    text.extend([PADS, STRIP, GND_STUB])
    if split:
        text.extend([SPLIT_PAD, SPLIT_STRIP])
    if pour:
        text.append(POUR)
    text.append(')')
    path = os.path.join(
        tempfile.gettempdir(),
        "board_thermal_%d_%d.kicad_pcb" % (os.getpid(), id(text)))
    with open(path, "w", newline="") as fh:
        fh.write("\n".join(text) + "\n")
    return K.parse_board(path)


ONE_A = [{"at": "U2.1", "a": 1.0}, {"at": "U1.1", "a": -1.0}]


class TestTheHeatIsTheBoardsOwnLoss(unittest.TestCase):
    def test_the_total_power_is_the_ir_drop_loss(self):
        """Two routes to one number: the IR-drop solve and the pad-current solve."""
        b = _board(pour=False)
        ir = O.board_ir_drop(b, "VCC", "U1.1", ["U2.1"], current_a=1.0,
                             plating_um=PLATING_UM)
        self.assertEqual(ir["status"], "ok", ir)
        out = SP.dissipation(b, ONE_A, plating_um=PLATING_UM)
        self.assertEqual(out["status"], "ok", out)
        # board_ir_drop rounds its published loss to nine significant digits, so
        # the agreement is asserted at that width, not below it
        self.assertAlmostEqual(out["value"]["total_power_w"],
                               ir["value"]["total_copper_loss_w"], places=9)

    def test_each_segment_carries_the_current_that_makes_that_power(self):
        b = _board(pour=False)
        out = SP.dissipation(b, ONE_A, plating_um=PLATING_UM)
        self.assertEqual(out["status"], "ok", out)
        segs = [s for s in out["value"]["segments"] if s["net"] == "VCC"]
        self.assertEqual(len(segs), 1, segs)
        s = segs[0]
        self.assertAlmostEqual(s["resistance_ohm"], STRIP_R, places=12)
        self.assertAlmostEqual(s["current_a"], 1.0, places=9)
        self.assertAlmostEqual(s["power_w"], STRIP_R, places=12)

    def test_the_power_follows_the_square_of_the_current(self):
        b = _board(pour=False)
        one = SP.dissipation(b, ONE_A, plating_um=PLATING_UM)["value"]
        two = SP.dissipation(b, [{"at": "U2.1", "a": 2.0},
                                 {"at": "U1.1", "a": -2.0}],
                             plating_um=PLATING_UM)["value"]
        self.assertAlmostEqual(two["total_power_w"] / one["total_power_w"], 4.0,
                               places=9)


class TestWhatIsRefused(unittest.TestCase):
    def test_currents_that_do_not_add_up_are_refused(self):
        """A net cannot gain charge; a .kicad_pcb has nothing to carry it away."""
        b = _board(pour=False)
        out = SP.dissipation(b, [{"at": "U2.1", "a": 1.0}], plating_um=PLATING_UM)
        self.assertEqual(out["status"], "error", out)
        self.assertEqual(out["reason_code"], "ARG_INVALID")
        self.assertIn("add up to", out["message"])
        self.assertIn("VCC", out["message"])

    def test_a_current_on_a_pad_with_no_copper_is_refused(self):
        b = _board(pour=False)
        out = SP.dissipation(b, [{"at": "U2.1", "a": 1.0},
                                 {"at": "U1.1", "a": -1.0},
                                 {"at": "U3.1", "a": 0.5},
                                 {"at": "U1.1", "a": -0.5}],
                             plating_um=PLATING_UM)
        self.assertEqual(out["status"], "error", out)
        self.assertEqual(out["reason_code"], "NOT_CONNECTED")
        self.assertIn("U3.1", out["message"])

    def test_a_pad_that_does_not_exist_says_so(self):
        b = _board(pour=False)
        out = SP.dissipation(b, [{"at": "U9.1", "a": 1.0}], plating_um=PLATING_UM)
        self.assertEqual(out["status"], "error", out)
        self.assertEqual(out["reason_code"], "NOT_FOUND")

    def test_a_net_whose_copper_is_one_stub_still_answers_its_own_loss(self):
        b = _board(pour=False)
        out = SP.dissipation(b, [{"at": "J1.1", "a": 1.0},
                                 {"at": "J1.1", "a": -1.0}],
                             plating_um=PLATING_UM)
        # both currents land on one pad, so nothing flows: the loss is 0 and it is
        # 0 because the current cancels, not because a solver failed
        self.assertEqual(out["status"], "ok", out)
        self.assertLess(out["value"]["total_power_w"], 1e-12)


class TestAnIslandOnTheNet(unittest.TestCase):
    def test_an_island_carries_nothing_and_does_not_stop_the_answer(self):
        b = _board(pour=False, split=True)
        out = SP.dissipation(b, ONE_A, plating_um=PLATING_UM)
        self.assertEqual(out["status"], "ok", out)
        v = out["value"]
        # the loss is still the driven strip alone: 0.1 A cannot appear in copper
        # that nothing leads into
        self.assertAlmostEqual(v["total_power_w"], STRIP_R, places=12)
        self.assertEqual(v["segment_count"], 1, v["segments"])
        self.assertEqual([i["net"] for i in v["islands"]], ["VCC"])

    def test_currents_landing_on_two_pieces_are_refused(self):
        b = _board(pour=False, split=True)
        out = SP.dissipation(b, [{"at": "U2.1", "a": 1.0},
                                 {"at": "U4.1", "a": -1.0}],
                             plating_um=PLATING_UM)
        self.assertEqual(out["status"], "error", out)
        self.assertEqual(out["reason_code"], "NOT_CONNECTED")
        self.assertIn("separate pieces", out["message"])

class TestThePlateIsTheBoard(unittest.TestCase):
    def test_the_plate_covers_the_outline_and_rises(self):
        b = _board(pour=True)
        out = SP.board_thermal(b, {"currents": ONE_A, "h_w_m2k": 10.0,
                                   "cell_mm": 0.4, "plating_um": PLATING_UM})
        self.assertEqual(out["status"], "ok", out)
        v = out["value"]
        self.assertAlmostEqual(v["plate"]["width_mm"], 50.0, places=9)
        self.assertAlmostEqual(v["plate"]["height_mm"], 30.0, places=9)
        self.assertAlmostEqual(v["plate"]["thickness_mm"], 1.6, places=9)
        self.assertEqual(v["conductor_count"], 1)
        self.assertGreater(v["t_max_c"], 25.0)
        # The plate is solved at the ambient the answer is quoted at (25 C), and
        # copper resists more when it is warmer, so the loss here is NOT the
        # 20 C STRIP_R: measured 0.020090018285714283 against 0.019702857142857143,
        # a ratio of 1.01966 = 1 + 0.00393*5 exactly. Reporting the 20 C number
        # would mean the heat was computed at one temperature and placed at
        # another.
        self.assertAlmostEqual(v["dissipation"]["total_power_w"],
                               STRIP_R * (1.0 + 0.00393 * 5.0), places=12)
        ir = O.board_ir_drop(b, "VCC", "U2.1", ["U1.1"], current_a=1.0,
                             copper_um=None, plating_um=PLATING_UM,
                             temp_c=25.0, quantize_mm=0.01)
        self.assertEqual(ir["status"], "ok", ir)
        self.assertAlmostEqual(v["dissipation"]["total_power_w"],
                               ir["value"]["total_copper_loss_w"], places=9)

    def test_the_files_pour_is_what_keeps_it_cool(self):
        """Disable the pour and the same 19.7 mW spreads through FR4 alone."""
        with_pour = SP.board_thermal(_board(pour=True),
                                     {"currents": ONE_A, "h_w_m2k": 10.0,
                                      "cell_mm": 0.4, "plating_um": PLATING_UM})
        without = SP.board_thermal(_board(pour=True),
                                   {"currents": ONE_A, "h_w_m2k": 10.0,
                                    "cell_mm": 0.4, "plating_um": PLATING_UM,
                                    "copper_from_board": False})
        self.assertEqual(with_pour["status"], "ok", with_pour)
        self.assertEqual(without["status"], "ok", without)
        self.assertEqual(with_pour["value"]["plate"]["pour_rectangles"], 1)
        self.assertEqual(without["value"]["plate"]["pour_rectangles"], 0)
        self.assertLess(with_pour["value"]["t_max_c"],
                        without["value"]["t_max_c"])

    def test_the_second_current_gets_four_times_hotter(self):
        b = _board(pour=False)
        one = SP.board_thermal(b, {"currents": ONE_A, "h_w_m2k": 10.0,
                                   "cell_mm": 0.4, "plating_um": PLATING_UM})
        two = SP.board_thermal(b, {"currents": [{"at": "U2.1", "a": 2.0},
                                                {"at": "U1.1", "a": -2.0}],
                                   "h_w_m2k": 10.0, "cell_mm": 0.4,
                                   "plating_um": PLATING_UM})
        self.assertEqual(one["status"], "ok", one)
        self.assertEqual(two["status"], "ok", two)
        d1 = one["value"]["delta_t_max_k"]
        d2 = two["value"]["delta_t_max_k"]
        self.assertGreater(d1, 0.0)
        # I^2 makes it FOUR, and rho(T) makes it slightly MORE than four: the
        # hot one is the more resistive one, so a clean 4.000 would mean alpha
        # had been dropped from the model
        self.assertGreater(d2 / d1, 4.0)
        self.assertLess(d2 / d1, 4.5)

    def test_a_board_with_no_outline_has_no_plate(self):
        b = _board(outline=False)
        out = SP.board_thermal(b, {"currents": ONE_A, "h_w_m2k": 10.0})
        self.assertEqual(out["status"], "error", out)
        self.assertEqual(out["reason_code"], "NOT_APPLICABLE")

    def test_convection_is_required(self):
        b = _board()
        out = SP.board_thermal(b, {"currents": ONE_A})
        self.assertEqual(out["status"], "error", out)
        self.assertEqual(out["reason_code"], "ARG_MISSING")
        self.assertIn("h_w_m2k", out["message"])


if __name__ == "__main__":
    unittest.main()
