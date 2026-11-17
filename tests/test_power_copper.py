"""Tests for oracle.power_copper - "what carries this rail, plane or width".

Every case here exists to separate two readings that the old `power_net_thin`
flag could not tell apart:

  * a rail with NO plane whose only copper is 0.1 mm wide  -> a defect
  * a rail WITH a filled plane whose traces are 0.1 mm escapes -> not a defect

The second board is the control for the first: same net name, same trace width,
same length; the only difference is a filled zone under it. If the flag survived
that change it would be judging width and calling it "no plane".
"""
import os
import sys
import unittest

sys.path.insert(0, os.path.join(os.path.dirname(os.path.dirname(
    os.path.abspath(__file__))), "src"))

from pcb_agent_mcp import oracle  # noqa: E402
from pcb_agent_mcp.adapters import kicad_pcb  # noqa: E402

STACKUP = """(setup (stackup
    (layer "F.Cu" (type "copper") (thickness 0.035))
    (layer "dielectric 1" (type "core") (thickness 0.2) (epsilon_r 4.3))
    (layer "In1.Cu" (type "copper") (thickness 0.035))
    (layer "dielectric 2" (type "core") (thickness 0.2) (epsilon_r 4.3))
    (layer "B.Cu" (type "copper") (thickness 0.035))))"""

# A 10 x 10 mm filled square on In1.Cu, plus an outline-only zone on In1.Cu for a
# second net: the outline one must contribute 0 mm2 and be counted as unfilled.
ZONE_FILLED = """
  (zone (net 1) (net_name "+1V8_ANA") (layer "In1.Cu") (name "rail") (hatch edge 0.5)
    (priority 1) (filled_polygon (layer "In1.Cu") (pts (xy 5 5) (xy 15 5)
      (xy 15 15) (xy 5 15))))"""
ZONE_UNFILLED = """
  (zone (net 2) (net_name "+0V9_CPU") (layer "In1.Cu") (name "cpu") (hatch edge 0.5)
    (priority 0) (fill no))"""


def board(filled=True, unfilled=True):
    return """(kicad_pcb (version 20221018) %s
      (net 0 "") (net 1 "+1V8_ANA") (net 2 "+0V9_CPU")
      (gr_rect (start 0 0) (end 40 40) (layer "Edge.Cuts") (width 0.1)
        (fill none))
      (segment (start 5 10) (end 25 10) (width 0.1) (layer "F.Cu") (net 1))
      (segment (start 5 12) (end 25 12) (width 0.1) (layer "F.Cu") (net 2))
      %s%s
    )""" % (STACKUP, ZONE_FILLED if filled else "",
           ZONE_UNFILLED if unfilled else "")


def report(text, **kw):
    return oracle.power_copper(kicad_pcb.parse_board_text(text), **kw)


def row(r, net):
    """The net name as the FILE spells it: these fixtures carry no sheet path,
    so `+1V8_ANA` arrives without the leading `/` a real board has."""
    return [x for x in r["value"]["nets"]
            if x["net"].lstrip("/") == net.lstrip("/")][0]


class TestTheTwoCarriers(unittest.TestCase):
    def test_a_rail_on_signal_width_is_flagged_when_nothing_is_poured(self):
        r = report(board(filled=False, unfilled=False), current_a=1.0)
        self.assertEqual(r["status"], "ok", r)
        row1 = row(r, "/+1V8_ANA")
        self.assertEqual(row1["backing"], "traces_only")
        self.assertEqual(row1["plane_area_mm2"], 0.0)
        self.assertEqual(row1["widest_mm"], 0.1)
        self.assertIn("rail_on_signal_width", row1["flags"])
        # 20 mm of 0.1 mm copper = 2 mm2, and the demand needs ~0.79 mm.
        self.assertAlmostEqual(row1["trace_area_mm2"], 2.0, places=2)
        self.assertGreater(row1["required_width_mm"]["width_mm"], 0.1)
        self.assertIn("narrower_than_asked", row1["flags"])

    def test_the_same_rail_under_a_plane_is_not_flagged(self):
        """The control: same name, same 0.1 mm, same length - one zone added."""
        r = report(board(), current_a=1.0)
        row1 = row(r, "/+1V8_ANA")
        self.assertNotEqual(row1["backing"], "traces_only")
        self.assertAlmostEqual(row1["plane_area_mm2"], 100.0, places=2)
        self.assertNotIn("rail_on_signal_width", row1["flags"])
        self.assertNotIn("narrower_than_asked", row1["flags"])
        # A plane carries the current, so the width question is not asked of the
        # escape stub at all - it comes back empty rather than wrong.
        self.assertIsNone(row1["required_width_mm"])
        # 40 mm2 of plane + 2 mm2 of trace -> the plane is the conductor.
        self.assertGreater(row1["plane_share"], 0.9)

    def test_an_outline_that_never_filled_is_not_a_plane(self):
        """`(fill no)` with no filled_polygon: 0 mm2, and the reader says so.

        Counting the outline would let a drawn-and-never-filled plane "back" a
        rail that has no copper under it - the exact lie power_loop refuses to
        tell when it looks for a way home.
        """
        r = report(board(filled=False, unfilled=True))
        row2 = row(r, "/+0V9_CPU")
        self.assertEqual(row2["plane_area_mm2"], 0.0)
        self.assertEqual(row2["zone_outlines"], 1)
        self.assertEqual(row2["zone_outlines_unfilled"], 1)
        self.assertIn("plane_outline_unfilled", row2["flags"])
        self.assertEqual(row2["backing"], "traces_only")


class TestPlaneMap(unittest.TestCase):
    def test_the_split_is_reported_as_owners_and_priorities(self):
        r = report(board())
        in1 = r["value"]["plane_map"]["In1.Cu"]
        self.assertEqual([(x["net"], x["priority"], x["filled"]) for x in in1],
                         [("+1V8_ANA", 1, True), ("+0V9_CPU", 0, False)])


class TestTheNamesItMatches(unittest.TestCase):
    """A core rail names its voltage, not a word from the old list.

    Before this, `/+1V8_ANA` and `/+0V9_*` were invisible to every report built on
    `_is_powerish` - the nets carrying the most current on this board.
    """

    def test_voltage_named_rails_are_power(self):
        for name in ("/+1V8_ANA", "/+0V9_CPU", "/0V675_REF", "/+3V3", "/-12V"):
            self.assertTrue(oracle._is_powerish(name), name)

    def test_a_signal_hanging_off_a_rail_is_not_a_rail(self):
        for name in ("/+3V3_PG", "/USB0_VBUSDET", "/3V3_EN", "/1V8_SENSE"):
            self.assertFalse(oracle._is_powerish(name), name)

    def test_an_ordinary_signals_sheet_path_is_not_a_rail(self):
        for name in ("/RMII0_RXD0", "/DDR_A3", "/SPI1_CLK", "/SW_U5"):
            self.assertFalse(oracle._is_powerish(name), name)


if __name__ == "__main__":
    unittest.main()
