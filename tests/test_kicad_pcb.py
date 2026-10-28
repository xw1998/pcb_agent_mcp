"""Tests for the KiCad `.kicad_pcb` adapter.

The boards used here are synthetic (no real project was available on this
machine), so every assertion is about *what the adapter claims*, in particular
about the two silent cross-format traps:

  * the axis convention (KiCad grows Y downwards, the canonical model upwards),
  * a missing fact must be declared absent, never filled with a default.

There is also one hard test that matters for the user's "do not touch my
project" rule: parsing must not modify a single byte of the file.
"""
import hashlib
import io
import json
import os
import shutil
import sys
import tempfile
import unittest

sys.path.insert(0, os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "src"))

from pcb_agent_mcp.adapters import detect_format, open_board  # noqa: E402
from pcb_agent_mcp.adapters import kicad_pcb  # noqa: E402

HERE = os.path.dirname(os.path.abspath(__file__))
SAMPLE = os.path.join(HERE, "data", "sample.kicad_pcb")


def _sha256(path):
    h = hashlib.sha256()
    with open(path, "rb") as f:
        for chunk in iter(lambda: f.read(65536), b""):
            h.update(chunk)
    return h.hexdigest()


class TestFormatDetection(unittest.TestCase):
    def test_sniffs_kicad_by_content_not_extension(self):
        self.assertEqual(detect_format(SAMPLE), "kicad_pcb")

    def test_unknown_file_refused(self):
        with tempfile.NamedTemporaryFile("w", suffix=".txt", delete=False) as f:
            f.write("hello")
            junk = f.name
        try:
            self.assertEqual(detect_format(junk), "unknown")
            with self.assertRaises(ValueError):
                open_board(junk)
        finally:
            os.unlink(junk)


class TestParseSampleBoard(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.sha_before = _sha256(SAMPLE)
        cls.board = open_board(SAMPLE)

    def test_file_is_not_modified_by_parsing(self):
        self.assertEqual(_sha256(SAMPLE), self.sha_before)

    def test_counts(self):
        c = self.board.counts()
        self.assertEqual(c["footprints"], 2)
        self.assertEqual(c["pads"], 4)
        self.assertEqual(c["tracks"], 2)
        self.assertEqual(c["vias"], 1)
        self.assertEqual(c["zones"], 2)   # one GND pour + one keepout
        self.assertEqual(c["outline_contours"], 1)

    def test_y_axis_is_flipped(self):
        # KiCad says (at 110 110); canonical must say y = -110
        r1 = [f for f in self.board.footprints if f["ref"] == "R1"][0]
        self.assertAlmostEqual(r1["x"], 110.0, places=6)
        self.assertAlmostEqual(r1["y"], -110.0, places=6)

    def test_outline_is_a_closed_contour(self):
        contour = self.board.outline[0]
        self.assertEqual(len(contour), 4)
        xs = sorted(p[0] for p in contour)
        self.assertAlmostEqual(xs[0], 100.0, places=6)
        self.assertAlmostEqual(xs[-1], 160.0, places=6)

    def test_net_codes_resolved_to_names(self):
        # a track stores net 2; the model must expose "VCC", not "2"
        self.assertEqual(self.board.tracks[0]["net"], "VCC")
        self.assertEqual(self.board.vias[0]["net"], "VCC")
        gnd = [z for z in self.board.zones if z["net"] == "GND"][0]
        self.assertEqual(gnd["net"], "GND")
        self.assertEqual(len([z for z in self.board.zones if z.get("keepout")]), 1)

    def test_properties_read_as_reference_and_value(self):
        c1 = [f for f in self.board.footprints if f["ref"] == "C1"][0]
        self.assertEqual(c1["value"], "100n")
        self.assertTrue(c1["dnp"])

    def test_pad_geometry_and_net(self):
        r1 = [f for f in self.board.footprints if f["ref"] == "R1"][0]
        nets = sorted(p["net"] for p in r1["pads"])
        self.assertEqual(nets, ["GND", "VCC"])
        pad = r1["pads"][0]
        self.assertAlmostEqual(pad["w"], 1.2, places=6)
        self.assertAlmostEqual(pad["h"], 1.4, places=6)

    def test_stackup_is_read(self):
        layers = {l["name"]: l for l in self.board.stackup}
        self.assertAlmostEqual(layers["dielectric 1"]["thickness_mm"], 1.53, places=6)
        self.assertAlmostEqual(layers["dielectric 1"]["er"], 4.3, places=6)
        self.assertAlmostEqual(layers["F.Cu"]["thickness_mm"], 0.035, places=6)
        self.assertAlmostEqual(self.board.thickness_mm, 1.6, places=6)

    def test_zone_fill_polygon_read(self):
        zone = [z for z in self.board.zones if z["filled_polygons"]][0]
        self.assertEqual(len(zone["filled_polygons"]), 1)
        self.assertEqual(len(zone["filled_polygons"][0]), 4)

    def test_rules_from_the_board_file_are_labelled_as_such(self):
        """No .kicad_pro here, but the board file carries its own (net_class ...).

        Those blocks ARE rules, so the fact is present -- and must say where it came
        from, otherwise the caller cannot tell a project rule from a board-local one.
        """
        rules = self.board.rules
        self.assertIn("net_class", (rules.get("net_class_source") or "").lower(),
                      "the sample board's own net_class block is the rule source")
        self.assertIn("Default", rules.get("net_classes") or {})
        facts = [a["fact"] for a in self.board.absent]
        self.assertNotIn("design_rules", facts,
                         "a rule source exists, so it must not be declared missing")

    def test_rules_are_declared_absent_when_neither_source_exists(self):
        text = """(kicad_pcb (version 20221018)
  (net 0 "") (net 1 "A")
  (footprint "T:X" (layer "F.Cu") (at 10 10 0)
    (property "Reference" "U1" (at 0 0 0) (layer "F.SilkS"))
    (pad "1" smd rect (at 0 0) (size 1 1) (layers "F.Cu") (net 1 "A"))
  )
)"""
        board = kicad_pcb.parse_board_text(text, path="<memory>")
        facts = [a["fact"] for a in board.absent]
        self.assertIn("design_rules", facts,
                      "no .kicad_pro and no (net_class ...): nothing may be guessed")


class TestFootprintRotation(unittest.TestCase):
    """A rotated footprint's pads must be transformed into board coordinates."""

    def test_pad_rotated_with_footprint(self):
        text = """(kicad_pcb (version 20221018)
  (net 0 "") (net 1 "A")
  (footprint "T:X" (layer "F.Cu") (at 10 10 90)
    (property "Reference" "U1" (at 0 0 0) (layer "F.SilkS"))
    (pad "1" smd rect (at 2 0) (size 1 1) (layers "F.Cu") (net 1 "A"))
  )
)"""
        board = kicad_pcb.parse_board_text(text, path="<memory>")
        pad = board.footprints[0]["pads"][0]
        # local (+2,0), rotated 90 deg, then Y flipped into canonical space. pcbnew
        # 10.0.7 puts this pad at (10, 8) in the file frame, i.e. canonical (10, -8);
        # the old expectation of -12 was the rotation-sign defect, see
        # tests/test_kicad_pad_placement.py.
        self.assertAlmostEqual(pad["x"], 10.0, places=6)
        self.assertAlmostEqual(pad["y"], -8.0, places=6)


class TestDrillOffset(unittest.TestCase):
    """`(drill N (offset x y))` moves the COPPER, not just the hole.

    pcbnew says so: for complex_hierarchy Q201.1 (offset 0 0.4) the pad's bounding
    box is centred 0.4 mm below GetPosition() and HitTest covers that shifted
    rectangle. Ignoring the offset left 27 pads of that board up to 0.4 mm off --
    enough to change a 0.25 mm clearance by a quarter of its value.
    """

    TEXT = """(kicad_pcb (version 20221018)
  (net 0 "") (net 1 "A")
  (footprint "T:TO92" (layer "F.Cu") (at 100 100)
    (property "Reference" "Q1" (at 0 0 0) (layer "F.SilkS"))
    (pad "1" thru_hole rect (at 0 0) (size 1.1 1.8)
      (drill 0.75 (offset 0 0.4)) (layers "*.Cu" "*.Mask") (net 1 "A"))
    (pad "2" thru_hole rect (at 2 0) (size 1.1 1.8)
      (drill 0.75) (layers "*.Cu" "*.Mask") (net 2 "B"))
  )
)"""

    def _board(self):
        return kicad_pcb.parse_board_text(self.TEXT, "offset.kicad_pcb")

    def test_offset_is_read_and_stored_with_y_canonicalised(self):
        pads = self._board().pads()
        self.assertEqual(pads[0]["offset"], [0.0, -0.4])
        self.assertNotIn("offset", pads[1])

    def test_offset_shifts_the_outline_but_not_the_anchor(self):
        from pcb_agent_mcp.adapters.canonical import pad_points
        pads = self._board().pads()
        # the anchor is what pcbnew reports as GetPosition(), unchanged
        self.assertAlmostEqual(pads[0]["y"], -100.0, places=6)
        ys = [p[1] for p in pad_points(pads[0])]
        self.assertAlmostEqual((min(ys) + max(ys)) / 2.0, -100.4, places=6)
        ys2 = [p[1] for p in pad_points(pads[1])]
        self.assertAlmostEqual((min(ys2) + max(ys2)) / 2.0, -100.0, places=6)

    def test_offset_turns_with_the_pad(self):
        from pcb_agent_mcp.adapters.canonical import pad_points
        # pcbnew on `(pad 1 thru_hole rect (at 0 0 90) (size 1.0 2.0)
        # (drill 0.5 (offset 0 0.4)))` of a footprint at (50, 50) reports the pad
        # bbox centre at file (50.4, 50.0): the offset turns with the pad, so the
        # same board written at (100, 100) has its copper at canonical (100.4, -100).
        txt = self.TEXT.replace('(at 0 0) (size 1.1 1.8)',
                                '(at 0 0 90) (size 1.1 1.8)')
        pad = kicad_pcb.parse_board_text(txt, "offset.kicad_pcb").pads()[0]
        xs, ys = zip(*pad_points(pad))
        self.assertAlmostEqual((min(xs) + max(xs)) / 2.0, 100.4, places=6)
        self.assertAlmostEqual((min(ys) + max(ys)) / 2.0, -100.0, places=6)


class TestPadShapeWidths(unittest.TestCase):
    """The pad outline has to be the shape the file asks for, at full size."""

    TEXT = """(kicad_pcb (version 20221018)
  (net 0 "") (net 1 "A")
  (footprint "T:X" (layer "F.Cu") (at 50 50)
    (property "Reference" "P1" (at 0 0 0) (layer "F.SilkS"))
    (pad "1" thru_hole oval (at 0 0 270) (size 1.6 2.8) (drill 1)
      (layers "*.Cu" "*.Mask") (net 1 "A"))
    (pad "2" thru_hole roundrect (at 5 0) (size 2 1) (drill 0.5)
      (roundrect_rratio 0.1) (layers "*.Cu" "*.Mask") (net 1 "A"))
  )
)"""

    def test_an_oval_keeps_its_full_size(self):
        from pcb_agent_mcp.adapters.canonical import pad_points
        pad = kicad_pcb.parse_board_text(self.TEXT, "shape.kicad_pcb").pads()[0]
        xs, ys = zip(*pad_points(pad))
        # 1.6 x 2.8 at 270 deg -> 2.8 wide, 1.6 tall. The old stadium construction
        # sliced the wrong arcs and produced a 1.2 mm wide sliver here.
        self.assertAlmostEqual(max(xs) - min(xs), 2.8, places=6)
        self.assertAlmostEqual(max(ys) - min(ys), 1.6, places=6)

    def test_a_roundrect_keeps_its_size_and_corner_radius(self):
        from pcb_agent_mcp.adapters.canonical import pad_points
        pad = kicad_pcb.parse_board_text(self.TEXT, "shape.kicad_pcb").pads()[1]
        self.assertAlmostEqual(pad["rratio"], 0.1, places=6)
        xs, ys = zip(*pad_points(pad))
        self.assertAlmostEqual(max(xs) - min(xs), 2.0, places=6)
        self.assertAlmostEqual(max(ys) - min(ys), 1.0, places=6)


class TestProjectRules(unittest.TestCase):
    def test_kicad_pro_rules_are_read(self):
        tmp = tempfile.mkdtemp()
        try:
            pcb = os.path.join(tmp, "demo.kicad_pcb")
            shutil.copy(SAMPLE, pcb)
            with io.open(os.path.join(tmp, "demo.kicad_pro"), "w", encoding="utf-8") as f:
                json.dump({"board": {"design_settings": {"rules": {
                    "min_clearance": 0.15, "min_track_width": 0.2}}}}, f)
            board = open_board(pcb, project=os.path.join(tmp, "demo.kicad_pro"))
            self.assertTrue(board.rules)
            self.assertTrue(board.present.get("design_rules", {}).get("present"))
            self.assertNotIn("design_rules", [a["fact"] for a in board.absent])
        finally:
            shutil.rmtree(tmp, ignore_errors=True)


if __name__ == "__main__":
    unittest.main()
