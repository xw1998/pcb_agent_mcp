"""What the refilled pour gave up, and the check that stops it being a guess.

The DRC gate structurally cannot answer this: a refilled pour meets clearance by
carving itself back around whatever was added, so "no new clearance violation" is
true by construction whenever a zone is in play. The number that IS available is
the filled area, measured on the refilled copies of both halves.

The cases below are the ones that matter for honesty rather than for the happy
path: the same board twice must read a delta of exactly zero (a metric that
drifts against itself is not a metric), a pour that ate itself must read negative,
and a pour that measures LARGER than its own outline must be refused instead of
reported - that is the signature of the hole-winding assumption being wrong, and
a wrong area is worse than no area.
"""
import os
import sys
import tempfile
import unittest

sys.path.insert(0, os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "src"))

from pcb_agent_mcp.edit import plan as P  # noqa: E402

_TEMPLATE = """(kicad_pcb (version 20240108) (generator pcbnew)
\t(general (thickness 1.6))
\t(layers (0 "F.Cu" signal) (31 "B.Cu" signal))
\t(zone
\t\t(net 0)
\t\t(net_name "GND")
\t\t(layer "F.Cu")
\t\t(uuid "z-1")
\t\t(hatch edge 0.5)
\t\t(connect_pads yes (clearance 0.2))
\t\t(min_thickness 0.2)
\t\t(fill yes)
\t\t(polygon (pts (xy 0 0) (xy 10 0) (xy 10 10) (xy 0 10)))
%s\t)
)
"""

def _ring(x0, y0, x1, y1):
    return ("\t\t(filled_polygon (layer \"F.Cu\") (pts"
            " (xy %s %s) (xy %s %s) (xy %s %s) (xy %s %s)))\n"
            % (x0, y0, x1, y0, x1, y1, x0, y1))

_TWO_ZONE_SECOND = """\t(zone
\t\t(net 0)
\t\t(net_name "+3V3")
\t\t(layer "B.Cu")
\t\t(uuid "z-2")
\t\t(hatch edge 0.5)
\t\t(connect_pads yes (clearance 0.2))
\t\t(min_thickness 0.2)
\t\t(fill yes)
\t\t(polygon (pts (xy 0 0) (xy 10 0) (xy 10 10) (xy 0 10)))
\t\t(filled_polygon (layer "B.Cu") (pts (xy 2 2) (xy 8 2) (xy 8 8) (xy 2 8)))
\t)"""

def _board(fills):
    return _TEMPLATE % "".join(_ring(*f) for f in fills)

class TestPourDelta(unittest.TestCase):
    def setUp(self):
        self.dir = tempfile.mkdtemp(prefix="pour_test_")

    def _write(self, name, text):
        path = os.path.join(self.dir, name)
        with open(path, "w", encoding="utf-8") as fh:
            fh.write(text)
        return path

    def test_the_same_pour_twice_is_a_delta_of_exactly_zero(self):
        """Measured white noise would make every other number below meaningless."""
        a = self._write("a.kicad_pcb", _board([(1, 1, 9, 9)]))
        got = P._pour_delta(a, a)
        self.assertTrue(got["comparable"])
        self.assertEqual(got["delta_mm2"], 0.0)
        self.assertFalse(got["pour_changed"])

    def test_a_pour_that_ate_itself_reads_negative(self):
        """10x10 outline, fill 8x8 -> 6x6: 64 mm2 down to 36 mm2."""
        before = self._write("b.kicad_pcb", _board([(1, 1, 9, 9)]))
        after = self._write("c.kicad_pcb", _board([(2, 2, 8, 8)]))
        got = P._pour_delta(before, after)
        self.assertTrue(got["comparable"])
        self.assertAlmostEqual(got["before_mm2"], 64.0, places=4)
        self.assertAlmostEqual(got["after_mm2"], 36.0, places=4)
        self.assertAlmostEqual(got["delta_mm2"], -28.0, places=4)
        self.assertAlmostEqual(got["pour_gave_up_mm2"], 28.0, places=4)
        self.assertEqual(got["pour_gained_mm2"], 0.0)
        self.assertTrue(got["pour_changed"])

    def test_two_islands_are_summed_not_averaged(self):
        before = self._write("d.kicad_pcb", _board([(1, 1, 3, 3), (5, 5, 9, 9)]))
        got = P._pour_delta(before, before)
        self.assertAlmostEqual(got["after_mm2"], 4.0 + 16.0, places=4)
        self.assertEqual(got["zones"][0]["rings"], 2)
        self.assertEqual(got["zones"][0]["vertices"], 8)

    def test_a_pour_bigger_than_its_own_outline_is_refused(self):
        """The signature of the hole-winding assumption failing: no area, not a wrong one."""
        inside = self._write("e.kicad_pcb", _board([(1, 1, 9, 9)]))
        outside = self._write("f.kicad_pcb", _board([(-1, -1, 11, 11)]))   # 144 > 100
        got = P._pour_delta(inside, outside)
        self.assertFalse(got["comparable"])
        self.assertIn("outline", got["reason"])
        self.assertEqual(got["zones_unusable"], [0])
        self.assertNotIn("delta_mm2", got)

    def test_a_zone_that_vanished_is_not_a_pair(self):
        """Two pours and one pour have no per-zone pairing, so no number either."""
        one = self._write("g.kicad_pcb", _board([(1, 1, 9, 9)]))
        two = self._write("h.kicad_pcb",
                          _board([(1, 1, 4, 4)]).replace(
                              "\n\t(zone\n\t\t(net 0)\n\t\t(net_name \"GND\")",
                              _TWO_ZONE_SECOND + "\n\t(zone\n\t\t(net 0)\n\t\t(net_name \"GND\")",
                              1))
        got = P._pour_delta(one, two)
        self.assertFalse(got["comparable"])
        self.assertIn("same zones", got["reason"])

    def test_a_keepout_zone_is_not_a_pour_at_all(self):
        text = _board([(1, 1, 9, 9)])
        path = self._write("i.kicad_pcb", text)
        from pcb_agent_mcp.adapters import open_board
        board = open_board(path)
        board.zones[0]["is_keepout"] = True
        self.assertEqual(P._pour_rows(board), [])

if __name__ == "__main__":
    unittest.main()
