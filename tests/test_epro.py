"""Tests for the EasyEDA Pro `.epro` adapter, built on synthetic archives.

No real `.epro` was available on this machine, and the payload key names are not
published. So the tests pin the four things that can be pinned without a sample:

  1. the append-only log resolves by highest ticket (a stale line never wins),
  2. a delete marker removes the object,
  3. the 0.01-inch unit and the Y-down axis are converted at the boundary,
  4. an unmapped field is *declared*, never defaulted to zero.

Plus the user-facing hard rule: reading an archive leaves its bytes untouched.
"""
import hashlib
import io
import json
import os
import sys
import tempfile
import unittest
import zipfile

sys.path.insert(0, os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "src"))

from pcb_agent_mcp.adapters import detect_format, epro, open_board  # noqa: E402


def _line(otype, oid, ticket, payload):
    return json.dumps({"type": otype, "id": oid, "ticket": ticket}) + "||" + json.dumps(payload)


def _build(path):
    """A tiny board: 2 nets, 1 component with 2 pads, 1 line (overridden),
    1 line that gets deleted, 1 via, 1 pour."""
    lines = [
        _line("NET", "n1", 1, {"name": "GND"}),
        _line("NET", "n2", 1, {"name": "VCC"}),
        _line("NET", "n3", 1, {"name": "OLD"}),
        _line("NET", "n3", 5, {"name": ""}),                       # deleted net
        _line("COMPONENT", "c1", 1, {"x": 400, "y": 400, "rotation": 0,
                                     "designator": "R1", "name": "10k", "layer": 1}),
        _line("PAD", "p1", 1, {"x": 380, "y": 400, "width": 60, "height": 60,
                               "number": "1", "net": "n1", "parentId": "c1",
                               "layer": 1, "shape": "rect"}),
        _line("PAD", "p2", 1, {"x": 420, "y": 400, "width": 60, "height": 60,
                               "number": "2", "net": "n2", "parentId": "c1",
                               "layer": 1, "shape": "rect"}),
        # the same line id twice: ticket 7 must win over ticket 1
        _line("LINE", "l1", 1, {"x": 400, "y": 420, "x2": 400, "y2": 500,
                                "width": 10, "net": "n2", "layer": 1}),
        _line("LINE", "l1", 7, {"x": 400, "y": 420, "x2": 400, "y2": 600,
                                "width": 10, "net": "n2", "layer": 1}),
        # and one that is created then deleted
        _line("LINE", "l2", 1, {"x": 300, "y": 300, "x2": 310, "y2": 310,
                                "width": 10, "net": "n1", "layer": 1}),
        _line("LINE", "l2", 9, {"x": "", "y": "", "x2": "", "y2": "",
                                "width": "", "net": "", "layer": ""}),
        _line("VIA", "v1", 1, {"x": 400, "y": 600, "holeRadius": 15,
                               "padRadius": 30, "net": "n2", "layer": 1}),
        _line("POUR", "pour1", 1, {"net": "n1", "layer": 2,
                                   "points": [[300, 300], [500, 300],
                                              [500, 500], [300, 500]]}),
    ]
    with zipfile.ZipFile(path, "w", zipfile.ZIP_DEFLATED) as zf:
        zf.writestr("project.json", json.dumps({"name": "demo"}))
        zf.writestr("demo.epcb", "\n".join(lines) + "\n")
        zf.writestr("demo.esch", "")


class TestEproAdapter(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.tmp = tempfile.mkdtemp()
        cls.path = os.path.join(cls.tmp, "demo.epro")
        _build(cls.path)
        with open(cls.path, "rb") as f:
            cls.sha_before = hashlib.sha256(f.read()).hexdigest()
        cls.board = open_board(cls.path)

    @classmethod
    def tearDownClass(cls):
        import shutil
        shutil.rmtree(cls.tmp, ignore_errors=True)

    def test_archive_is_not_modified_by_parsing(self):
        with open(self.path, "rb") as f:
            self.assertEqual(hashlib.sha256(f.read()).hexdigest(), self.sha_before)

    def test_detected_by_content(self):
        self.assertEqual(detect_format(self.path), "easyeda_epro")

    def test_highest_ticket_wins(self):
        self.assertEqual(len(self.board.tracks), 1)
        track = self.board.tracks[0]
        self.assertAlmostEqual(track["end"][1], -600 * 0.254, places=6)

    def test_delete_marker_removes_object(self):
        # l2 was deleted; only l1 survives
        self.assertEqual(len(self.board.tracks), 1)

    def test_deleted_net_is_gone(self):
        self.assertEqual(sorted(self.board.nets.values()), ["GND", "VCC"])

    def test_unit_conversion(self):
        # 400 * 0.254 = 101.6 mm
        r1 = [f for f in self.board.footprints if f["ref"] == "R1"][0]
        self.assertAlmostEqual(r1["x"], 101.6, places=6)

    def test_y_axis_flipped(self):
        r1 = [f for f in self.board.footprints if f["ref"] == "R1"][0]
        self.assertAlmostEqual(r1["y"], -101.6, places=6)

    def test_pad_attached_to_parent_and_net_named(self):
        r1 = [f for f in self.board.footprints if f["ref"] == "R1"][0]
        self.assertEqual(len(r1["pads"]), 2)
        self.assertEqual(sorted(p["net"] for p in r1["pads"]), ["GND", "VCC"])
        self.assertAlmostEqual(r1["pads"][0]["w"], 60 * 0.254, places=6)

    def test_via_geometry(self):
        via = self.board.vias[0]
        self.assertAlmostEqual(via["diameter"], 30 * 0.254, places=6)
        self.assertAlmostEqual(via["drill"], 15 * 0.254, places=6)
        self.assertEqual(via["net"], "VCC")

    def test_pour_becomes_zone(self):
        self.assertEqual(len(self.board.zones), 1)
        self.assertEqual(self.board.zones[0]["net"], "GND")
        self.assertEqual(len(self.board.zones[0]["outline"]), 4)

    def test_unit_is_marked_unverified(self):
        # this adapter has never been checked against a real project
        self.assertFalse(self.board.source["unit_verified"])
        self.assertEqual(self.board.source["unit_assumed"], "0.254 mm per unit (0.01 inch)")

    def test_log_stats_expose_dropped_lines(self):
        stats = self.board.source["log_stats"]
        self.assertEqual(stats["unparsable"], 0)
        self.assertGreaterEqual(stats["superseded"], 1)
        self.assertGreaterEqual(stats["deleted"], 2)

    def test_outline_absent_is_declared(self):
        # the synthetic archive has no board-outline layer, so the adapter must
        # say so rather than invent a bbox
        self.assertFalse(self.board.present["outline"]["present"])
        self.assertIn("outline", [a["fact"] for a in self.board.absent])

    def test_inspect_lists_raw_keys(self):
        info = epro.inspect(self.path)
        self.assertEqual(info["status"], "ok")
        doc = info["documents"][0]
        self.assertIn("PAD", doc["types"])
        pad_keys = doc["types"]["PAD"]["samples"][0]["keys"]
        self.assertIn("width", pad_keys)


class TestEproRefusals(unittest.TestCase):
    def test_not_a_zip_refused(self):
        fd, path = tempfile.mkstemp(suffix=".epro")
        os.close(fd)
        try:
            with io.open(path, "w", encoding="utf-8") as f:
                f.write("not a zip")
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


if __name__ == "__main__":
    unittest.main()
