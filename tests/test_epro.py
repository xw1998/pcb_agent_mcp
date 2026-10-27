"""Tests for the LCEDA Pro board adapter, against the measured file format.

The old version of this file asserted an "append-only log with tickets" that the
vendor does not write; that format was a guess and every claim it pinned was
replaced. What is pinned now is what `docs/13` records from real projects:

  1. the board is a record stream inside a ZIP member (`PCB/<uuid>.epcb`),
  2. units are **mil** and the axis is **not** flipped in the file,
  3. a component's footprint pads come from the `FOOTPRINT` document, and the
     readable names come from `project.json` (`footprints[].title`,
     `devices[].title`) - the board document only carries uuids,
  4. a board edge that really does not close is reported as open chains, never
     fitted into a contour that is not in the file.

Plus the user's hard rule: reading an archive leaves its bytes untouched.
"""
import hashlib
import io
import json
import os
import shutil
import sys
import tempfile
import unittest
import zipfile

_HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, os.path.join(_HERE, "..", "src"))
sys.path.insert(0, _HERE)

from pcb_agent_mcp.adapters import detect_format, epro, open_board  # noqa: E402

import _lceda_fixtures as fx                                       # noqa: E402


class TestEproAdapter(unittest.TestCase):
    """One `.epro` in the measured format, parsed once."""

    @classmethod
    def setUpClass(cls):
        cls.tmp = tempfile.mkdtemp(prefix="epro_a_")
        cls.path = fx.write_epro(directory=cls.tmp)
        with open(cls.path, "rb") as fh:
            cls.sha_before = hashlib.sha256(fh.read()).hexdigest()
        cls.board = open_board(cls.path)

    @classmethod
    def tearDownClass(cls):
        shutil.rmtree(cls.tmp, ignore_errors=True)

    # ---------------------------------------------------------- container
    def test_detected_by_content(self):
        self.assertEqual(detect_format(self.path), "easyeda_epro")
        self.assertEqual(self.board.source["container"], "epro_zip")

    def test_archive_is_not_modified_by_parsing(self):
        with open(self.path, "rb") as fh:
            self.assertEqual(hashlib.sha256(fh.read()).hexdigest(),
                             self.sha_before)

    # ---------------------------------------------------------- units
    def test_unit_is_mil_and_verified(self):
        self.assertEqual(self.board.source["coord_unit"], "mil")
        self.assertAlmostEqual(self.board.source["mm_per_coord"], 0.0254)
        self.assertTrue(self.board.source["unit_verified"])

    def test_bottom_side_is_not_a_global_y_flip(self):
        # the file keeps y as it is; the frame note says so instead of flipping
        self.assertIn("y not inverted", self.board.source["frame"])

    # ---------------------------------------------------------- names
    def test_component_names_come_from_the_project_index(self):
        fp = self.board.footprints[0]
        self.assertEqual(fp["ref"], "R1")
        self.assertEqual(fp["value"], "RES-10k")        # devices[].title
        self.assertEqual(fp["device_uuid"], fx.DEV_UUID)
        self.assertEqual(fp["footprint_name"], "R0603")  # footprints[].title
        self.assertEqual(fp["lib_id"], fx.FP_UUID)
        self.assertEqual(fp["layer"], "TOP")

    def test_library_counts_are_reported(self):
        lib = self.board.source["library"]
        self.assertEqual(lib["footprint_documents"], 2)   # one in the .efoo, one in the .epcb
        self.assertEqual(lib["footprints_with_pads"], 1)
        self.assertEqual(self.board.source["footprint_resolution"],
                         {"resolved": 1, "missing": 0})

    # ---------------------------------------------------------- pads
    def test_pads_carry_net_geometry_and_hole(self):
        pads = {p["number"]: p for p in self.board.footprints[0]["pads"]}
        # pad "3" exists in the footprint but has no PAD_NET row on the board -
        # the measured case that a `set_pad_net` append has to handle.
        self.assertEqual(sorted(pads), ["1", "2", "3"])
        self.assertEqual(pads["1"]["net"], "GND")
        self.assertEqual(pads["2"]["net"], "VCC")
        self.assertIsNone(pads["3"]["net"])
        # component at (400,400) mil, pads at -50/+50 mil in the footprint frame
        self.assertAlmostEqual(pads["1"]["x"], fx.mil(350), places=6)
        self.assertAlmostEqual(pads["2"]["x"], fx.mil(450), places=6)
        self.assertAlmostEqual(pads["1"]["y"], fx.mil(400), places=6)
        self.assertAlmostEqual(pads["1"]["w"], fx.mil(100), places=6)
        self.assertAlmostEqual(pads["1"]["h"], fx.mil(60), places=6)
        self.assertEqual(pads["1"]["shape"], "rect")
        self.assertEqual(pads["2"]["shape"], "oval")
        self.assertTrue(pads["1"]["smd"])
        self.assertEqual(pads["2"]["hole_kind"], "round")
        self.assertAlmostEqual(pads["2"]["hole_mm"], fx.mil(30), places=6)

    def test_pad_shape_approximation_is_declared(self):
        self.assertIn("ELLIPSE", self.board.source["pad_shape_note"])

    # ---------------------------------------------------------- copper
    def test_track_and_arc_widths_are_mil(self):
        line = [t for t in self.board.tracks if t.get("_kind") != "arc"][0]
        arc = [t for t in self.board.tracks if t.get("_kind") == "arc"][0]
        self.assertAlmostEqual(line["width"], fx.mil(19.685), places=6)   # 0.5 mm
        self.assertAlmostEqual(arc["width"], fx.mil(19.685), places=6)
        self.assertEqual(line["net"], "GND")
        self.assertEqual(line["layer"], "TOP")
        self.assertAlmostEqual(line["end"][0], fx.mil(100), places=6)

    def test_via_drill_and_diameter(self):
        via = self.board.vias[0]
        self.assertAlmostEqual(via["drill"], fx.mil(12.0078), places=6)   # 0.305 mm
        self.assertAlmostEqual(via["diameter"], fx.mil(24.0158), places=6)
        self.assertEqual(via["net"], "VCC")

    def test_pour_becomes_zone_with_its_fill(self):
        zone = [z for z in self.board.zones if z["origin_type"] == "POUR"][0]
        self.assertEqual(zone["net"], "GND")
        self.assertEqual(zone["layer"], "TOP")
        self.assertEqual(len(zone["outline"]), 5)      # chained, not flattened
        self.assertEqual(len(zone["filled_polygons"]), 1)

    def test_zone_slot_that_is_not_a_distance_is_reported_raw(self):
        """POUR[5] holds only 0.2 or 1 in 12,400 measured pours: a flag."""
        for zone in self.board.zones:
            self.assertIsNone(zone["clearance"])
            self.assertIsNotNone(zone["clearance_raw"])
        self.assertIn("a flag, not a distance",
                      self.board.source["zone_clearance_note"])

    def test_fill_and_region_become_zones(self):
        fill = [z for z in self.board.zones if z["origin_type"] == "FILL"][0]
        self.assertEqual(fill["net"], "VCC")
        self.assertEqual(len(fill["outline"]), 4)
        region = [z for z in self.board.zones if z["origin_type"] == "REGION"][0]
        self.assertTrue(region["is_keepout"])
        self.assertEqual(region["keepout"]["flags"], [2, 7])
        self.assertEqual(region["layer"], "OUTLINE")

    # ---------------------------------------------------------- edge
    def test_closed_outline_and_its_cutout(self):
        self.assertEqual(self.board.counts()["outline_contours"], 1)
        self.assertEqual(self.board.counts()["cutouts"], 1)
        bbox = self.board.as_dict()["outline_bbox"]
        self.assertAlmostEqual(bbox["width_mm"], 25.4, places=6)
        self.assertAlmostEqual(bbox["height_mm"], 20.32, places=6)
        self.assertTrue(self.board.as_dict()["outline_closed"])
        self.assertTrue(self.board.present["outline"]["present"])

    # ---------------------------------------------------------- stackup / rules / text
    def test_stackup_sums_to_the_board_thickness(self):
        self.assertEqual(len(self.board.stackup), 3)
        self.assertEqual(self.board.stackup[1]["material"], "FR4")
        self.assertEqual(self.board.stackup[0]["thickness_unit"], "mil")
        self.assertAlmostEqual(self.board.thickness_mm, 1.6, places=4)

    def test_rules_are_converted_from_mil(self):
        rule = self.board.rules["lceda"]["safeClearance"]
        self.assertEqual(rule["unit_label"], "mm")        # what the user typed
        self.assertAlmostEqual(rule["value_mm"], fx.mil(6), places=6)

    def test_texts_are_carried(self):
        text = self.board.source["texts"][0]
        self.assertEqual(text["text"], "REV A")
        self.assertEqual(text["layer"], "TOP_SILK")
        self.assertEqual(text["rotation"], -270)

    def test_unmapped_facts_are_declared_not_defaulted(self):
        self.assertFalse(self.board.present["pad_to_mask"]["present"])
        self.assertFalse(self.board.present["component_3d"]["present"])
        self.assertIn("not verified", self.board.present["pad_to_mask"]["note"])


class TestEproOutlineThatDoesNotClose(unittest.TestCase):
    """A real project's hand-drawn edge is a few mil short: never fit it."""

    @classmethod
    def setUpClass(cls):
        cls.tmp = tempfile.mkdtemp(prefix="epro_o_")
        cls.path = fx.write_epro(directory=cls.tmp, name="open.epro",
                                outline="open")
        cls.board = open_board(cls.path)

    @classmethod
    def tearDownClass(cls):
        shutil.rmtree(cls.tmp, ignore_errors=True)

    def test_no_contour_is_invented(self):
        self.assertEqual(self.board.outline, [])
        self.assertEqual(len(self.board.outline_open), 1)

    def test_the_gap_is_reported_in_mil(self):
        note = self.board.source["outline_note"]
        self.assertIn("do not close", note)
        self.assertIn("5.0000 mil", note)
        self.assertIn("open chains", note)

    def test_the_edge_is_still_marked_present(self):
        # present means "the file has this fact", not "we resolved it"
        self.assertTrue(self.board.present["outline"]["present"])
        bbox = self.board.as_dict()["outline_bbox"]
        self.assertAlmostEqual(bbox["width_mm"], 25.4, places=6)
        self.assertFalse(self.board.as_dict()["outline_closed"])


class TestEproRectOutline(unittest.TestCase):
    """The quick-start project stores its edge as one `["R", ...]` record."""

    @classmethod
    def setUpClass(cls):
        cls.tmp = tempfile.mkdtemp(prefix="epro_r_")
        cls.path = fx.write_epro(directory=cls.tmp, name="rect.epro",
                                 outline="rect")
        cls.board = open_board(cls.path)

    @classmethod
    def tearDownClass(cls):
        shutil.rmtree(cls.tmp, ignore_errors=True)

    def test_rectangle_becomes_a_closed_contour(self):
        self.assertEqual(len(self.board.outline), 1)
        self.assertEqual(self.board.outline_open, [])
        bbox = self.board.as_dict()["outline_bbox"]
        self.assertAlmostEqual(bbox["width_mm"], 80.0, places=6)
        self.assertAlmostEqual(bbox["height_mm"], 60.0, places=6)


class TestEproInspect(unittest.TestCase):
    def test_inspect_reports_the_container_and_the_evidence(self):
        tmp = tempfile.mkdtemp(prefix="epro_i_")
        self.addCleanup(shutil.rmtree, tmp, True)
        path = fx.write_epro(directory=tmp)
        info = epro.inspect(path)
        self.assertEqual(info["status"], "ok")
        self.assertEqual(info["container"], "epro_zip")
        self.assertEqual(info["coord_unit"], "mil")
        self.assertTrue(info["unit_verified"])
        self.assertTrue(info["verify"]["ok"])
        self.assertIn("project.json", [m["name"] for m in info["members"]])
        kinds = [d["kind"] for d in info["documents"]]
        self.assertIn("PCB", kinds)
        pcb = [d for d in info["documents"] if d["kind"] == "PCB"][0]
        self.assertIn("POLY", pcb["tags"])
        self.assertEqual(pcb["tags"]["POLY"]["arity"], [8])
        self.assertEqual(pcb["tags"]["POLY"]["count"], 2)


class TestEproRefusals(unittest.TestCase):
    def test_not_a_zip_refused(self):
        fd, path = tempfile.mkstemp(suffix=".epro")
        os.close(fd)
        try:
            with io.open(path, "w", encoding="utf-8") as fh:
                fh.write("not a zip and not sqlite")
            with self.assertRaises(epro.EproError):
                epro.parse_board(path)
        finally:
            os.unlink(path)

    def test_zip_without_pcb_member_refused(self):
        fd, path = tempfile.mkstemp(suffix=".epro")
        os.close(fd)
        try:
            with zipfile.ZipFile(path, "w") as zf:
                zf.writestr("project.json", "{}")
            with self.assertRaises(epro.EproError):
                epro.parse_board(path)
        finally:
            os.unlink(path)

    def test_member_that_does_not_parse_is_refused(self):
        """A stream that is not JSON must raise, not be skipped."""
        fd, path = tempfile.mkstemp(suffix=".epro")
        os.close(fd)
        try:
            with zipfile.ZipFile(path, "w") as zf:
                zf.writestr("project.json", json.dumps({}))
                zf.writestr("PCB/x.epcb",
                            '["DOCTYPE","PCB","1.9"]\nnot json at all\n')
            with self.assertRaises(Exception):
                epro.parse_board(path)
        finally:
            os.unlink(path)


if __name__ == "__main__":
    unittest.main()
