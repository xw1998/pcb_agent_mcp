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
        self.assertEqual(c["zones"], 1)
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
        self.assertEqual(self.board.zones[0]["net"], "GND")

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
        zone = self.board.zones[0]
        self.assertEqual(len(zone["filled_polygons"]), 1)
        self.assertEqual(len(zone["filled_polygons"][0]), 4)

    def test_missing_project_file_is_declared_not_guessed(self):
        facts = [a["fact"] for a in self.board.absent]
        self.assertIn("design_rules", facts)


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
        # local (+2,0), rotated 90 deg CCW in KiCad's frame, then Y flipped:
        #   board x = 10 + 0 = 10, board y = -(10 + 2) = -12
        self.assertAlmostEqual(pad["x"], 10.0, places=6)
        self.assertAlmostEqual(pad["y"], -12.0, places=6)


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
