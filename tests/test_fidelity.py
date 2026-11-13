"""Tests for the fidelity layer: conversion, not inference.

The bar these tests hold the code to:

    a fact may only be reported if it is IN the carried document;
    and the carried document must be provably complete.

So the tests cover, in this order: the tokenizer keeps spelling/quoting, the
round trip is a bijection on tokens, the coverage report counts what has no rule
yet instead of dropping it, views read the stored values (fill polygons, exact
pad sizes, net classes), and - the real proof - KiCad itself plots the original
and the re-emitted board to the same Gerbers. The last test is the anti-false-
green check: drop one node on purpose and the engine comparison must fail.
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

from pcb_agent_mcp import fidelity                          # noqa: E402
from pcb_agent_mcp.fidelity import engine, epro_doc, sexpr as S  # noqa: E402
from pcb_agent_mcp.fidelity.epro_doc import EproDocument      # noqa: E402
from pcb_agent_mcp.fidelity.kicad_doc import KiCadDocument    # noqa: E402
from pcb_agent_mcp.fidelity.views import INTERPRETED_HEADS, BoardView  # noqa: E402

import _lceda_fixtures as _fx                              # noqa: E402

SAMPLE = os.path.join(os.path.dirname(os.path.abspath(__file__)), "data",
                      "sample.kicad_pcb")


def _sha(path):
    import hashlib
    with open(path, "rb") as fh:
        return hashlib.sha256(fh.read()).hexdigest()


# --------------------------------------------------------------- sexpr
class TestSexprIsLossless(unittest.TestCase):

    def test_atom_spelling_is_kept(self):
        node = S.parse_single("(x 1.0000 -0 1e-3)")
        texts = [a.text for a in node.items[1:]]
        self.assertEqual(texts, ["1.0000", "-0", "1e-3"])
        self.assertIn("1.0000", S.dumps(node))

    def test_quoted_and_bare_are_different(self):
        a = S.parse_single('(pad "1" 1)')
        self.assertTrue(a.items[1].quoted)
        self.assertFalse(a.items[2].quoted)
        self.assertEqual(S.dumps(a), '(pad "1" 1)')

    def test_escapes_round_trip(self):
        src = r'(text "a\"b\\c\nd")'
        node = S.parse_single(src)
        self.assertEqual(node.items[1].text, 'a"b\\c\nd')
        self.assertEqual(S.dumps(node), src)

    def test_unknown_escape_keeps_its_backslash(self):
        node = S.parse_single(r'(t "a\qb")')
        self.assertEqual(node.items[1].text, "a\\qb")

    def test_order_and_duplicates_are_kept(self):
        node = S.parse_single("(k (net 1) (net 1))")
        self.assertEqual(len(S.children(node, "net")), 2)

    def test_unbalanced_is_an_error(self):
        with self.assertRaises(S.SexprError):
            S.parse("(a (b)")
        with self.assertRaises(S.SexprError):
            S.parse("(a))")


# --------------------------------------------------------------- document
class TestKiCadDocument(unittest.TestCase):

    def setUp(self):
        self.sha = _sha(SAMPLE)
        self.doc = KiCadDocument.from_path(SAMPLE)

    def test_file_is_not_modified(self):
        self.doc.to_bytes()
        self.assertEqual(_sha(SAMPLE), self.sha)

    def test_roundtrip_is_a_token_bijection(self):
        r = self.doc.verify()
        self.assertTrue(r["token_identity"], r)

    def test_document_facts(self):
        st = self.doc.stats()
        self.assertEqual(st["version"], "20221018")
        self.assertEqual(st["generator"], "pcbnew")
        self.assertGreater(st["seqs"], 100)

    def test_coverage_is_complete_on_a_real_board(self):
        cov = self.doc.coverage(INTERPRETED_HEADS)
        self.assertEqual(cov["share_with_a_reading_rule"], 1.0, cov)
        self.assertEqual(cov["carried_but_not_yet_interpreted"], {})

    def test_unknown_node_is_carried_and_reported(self):
        text = self.doc.text.replace(
            "  (gr_text",
            '  (zzz_custom_block (foo "bar") (baz 1.50))\n  (gr_text', 1)
        doc = KiCadDocument.from_text(text)
        self.assertTrue(doc.verify()["token_identity"])
        cov = doc.coverage(INTERPRETED_HEADS)
        unk = cov["carried_but_not_yet_interpreted"]
        self.assertIn("zzz_custom_block", unk)
        self.assertEqual(unk["zzz_custom_block"], 1)
        raw = BoardView(doc).uninterpreted()
        self.assertEqual(len(raw), 1)
        self.assertEqual(raw[0]["head"], "zzz_custom_block")
        self.assertIn('(foo "bar")', raw[0]["raw"])


# --------------------------------------------------------------- views
class TestViewsReadStoredValues(unittest.TestCase):

    def setUp(self):
        self.view = BoardView(KiCadDocument.from_path(SAMPLE))

    def test_every_record_carries_its_raw_node(self):
        for kind in ("nets", "footprints", "pads", "tracks", "vias", "zones",
                     "outline", "graphics", "stackup", "net_classes"):
            for item in getattr(self.view, kind)():
                self.assertIn("raw", item, kind)
                self.assertIn("src", item, kind)
                self.assertTrue(item["raw"].startswith("("), kind)

    def test_y_is_negative_in_the_canonical_frame(self):
        r1 = [f for f in self.view.footprints() if f["reference"] == "R1"][0]
        self.assertEqual((r1["x_mm"], r1["y_mm"]), (110.0, -110.0))

    def test_pad_position_is_absolute_and_exact(self):
        pads = [p for p in self.view.pads() if p["footprint"] == "R1"]
        self.assertEqual(len(pads), 2)
        left = [p for p in pads if p["number"] == "1"][0]
        self.assertAlmostEqual(left["x_mm"], 109.05, places=6)
        self.assertEqual(left["w_mm"], 1.2)
        self.assertEqual(left["h_mm"], 1.4)
        self.assertEqual(left["net"], "GND")
        self.assertEqual(left["roundrect_rratio"], "0.25")

    def test_zone_fill_is_the_stored_polygon_not_a_recompute(self):
        gnd = [z for z in self.view.zones() if z["net"] == "GND"][0]
        self.assertEqual(gnd["filled_polygon_count"], 1)
        self.assertIn("filled_polygon", gnd["filled_polygons"][0])
        self.assertEqual(len(gnd["outline_points"]), 4)
        self.assertFalse(gnd["keepout"])

    def test_keepout_rules_are_read(self):
        ko = [z for z in self.view.zones() if z["keepout"]][0]
        self.assertEqual(ko["keepout_rules"]["tracks"], "not_allowed")

    def test_net_class_numbers_are_read_with_the_right_key(self):
        nc = self.view.net_classes()[0]
        self.assertEqual(nc["name"], "Default")
        self.assertEqual(nc["clearance"], 0.2)
        self.assertEqual(nc["trace_width"], 0.25)      # KiCad 7 spelling
        self.assertEqual(nc["trace_width_key"], "trace_width")

    def test_a_rotated_footprint_puts_its_pads_where_kicad_does(self):
        """The pinned numbers are pcbnew 10.0.7's, not ours: see
        tests/test_kicad_pad_placement.py, which measures the same cases against
        the installed KiCad. This view reports Y up, so y_mm == -pcbnew_y.

        Rotation 0 is the case that hides a wrong rotation sign, so every case
        here is rotated: a negated angle used to mirror the pads about the
        footprint origin (a 90 deg header pad 25.4 mm out) while every other
        answer stayed self-consistent.
        """
        text = """(kicad_pcb (version 20221018)
  (net 0 "") (net 1 "A")
  (footprint "T:Q" (layer "F.Cu") (at 10 10 90)
    (property "Reference" "U1" (at 0 0 0) (layer "F.SilkS"))
    (pad "1" smd rect (at 2 0) (size 1 1) (layers "F.Cu") (net 1 "A"))
  )
  (footprint "T:F" (layer "F.Cu") (at 10 10 45)
    (property "Reference" "U2" (at 0 0 0) (layer "F.SilkS"))
    (pad "1" smd rect (at 2 0) (size 1 2) (layers "F.Cu") (net 1 "A"))
    (pad "2" smd rect (at 0 2) (size 1 2) (layers "F.Cu") (net 1 "A"))
  )
  (footprint "T:HDR" (layer "F.Cu") (at 2.54 -3.81 90)
    (property "Reference" "J9" (at 0 0 0) (layer "F.SilkS"))
    (pad "6" thru_hole circle (at 0 12.7 90) (size 1.7 1.7) (drill 1)
      (layers "*.Cu" "*.Mask") (net 1 "A"))
  )
)"""
        pads = BoardView(KiCadDocument.from_text(text)).pads()
        got = dict(((p["footprint"], p["number"]), (p["x_mm"], p["y_mm"]))
                   for p in pads)
        want = {
            ("U1", "1"): (10.0, -8.0),
            ("U2", "1"): (11.414214, -8.585786),
            ("U2", "2"): (11.414214, -11.414214),
            ("J9", "6"): (15.24, 3.81),
        }
        for key, xy in want.items():
            self.assertAlmostEqual(got[key][0], xy[0], places=5, msg=key)
            self.assertAlmostEqual(got[key][1], xy[1], places=5, msg=key)
        # The local offsets stay as written (Y up), so the answer is reproducible
        # from `raw` alone.
        self.assertEqual(got[("J9", "6")][0] - 2.54, 12.7)

    def test_stackup_rows_come_from_the_file(self):
        rows = {r["name"]: r for r in self.view.stackup()}
        self.assertEqual(rows["dielectric 1"]["thickness"], 1.53)
        self.assertEqual(rows["dielectric 1"]["epsilon_r"], 4.3)
        self.assertEqual(rows["dielectric 1"]["material"], "FR4")

    def test_texts_are_not_lost(self):
        texts = [g for g in self.view.graphics() if g["kind"] == "gr_text"]
        self.assertEqual([t["text"] for t in texts], ["REV A"])

    def test_setup_reports_the_facts_it_carried(self):
        setup = self.view.setup()
        self.assertEqual(setup["board_thickness_mm"], 1.6)
        self.assertEqual(setup["fields"]["pad_to_mask_clearance"], "0")


# --------------------------------------------------------------- epro
# The sample is `tests/_lceda_fixtures.py`, built from the format that was
# measured on disk (docs/13): a ZIP of record-stream members, mil units,
# `project.json` holding the only human-readable names.
def _epro_path(tag="closed"):
    return _fx.write_epro(name="t_%s.epro" % tag, outline=tag)


class TestEproDocument(unittest.TestCase):
    """`.epro` is a revision container: every member carried, byte for byte."""

    def setUp(self):
        self.dir = tempfile.mkdtemp(prefix="epro_t_")
        self.addCleanup(shutil.rmtree, self.dir, True)
        self.path = _fx.write_epro(directory=self.dir)
        self.doc = EproDocument.from_path(self.path)

    def test_every_member_is_kept(self):
        names = self.doc.names()
        self.assertIn("project.json", names)
        self.assertIn("PCB/%s.epcb" % _fx.PCB_UUID, names)
        self.assertIn("FOOTPRINT/%s.efoo" % _fx.FP_UUID, names)
        self.assertEqual(self.doc.entry_bytes("PCB/"), b"")
        self.assertEqual(json.loads(self.doc.entry_bytes("project.json"))["pcbs"],
                         {_fx.PCB_UUID: "PCB1_1"})

    def test_stream_members_are_the_ones_with_a_stream_extension(self):
        self.assertEqual(self.doc.stream_names(),
                         ["PCB/%s.epcb" % _fx.PCB_UUID,
                          "FOOTPRINT/%s.efoo" % _fx.FP_UUID,
                          "SHEET/%s/1.esch" % _fx.SCH_UUID])

    def test_roundtrip_is_byte_identical(self):
        report = self.doc.verify()
        self.assertTrue(report["ok"], report)
        for member in report["members"]:
            self.assertTrue(member["verbatim"], member)
            self.assertEqual(member["n_other"], 0, member)
        with open(self.path, "rb") as fh:
            original = fh.read()
        self.assertEqual(hashlib.sha256(self.doc.to_bytes()).hexdigest(),
                         hashlib.sha256(original).hexdigest())

    def test_one_member_holds_two_documents(self):
        kinds = [(name, d.kind) for name, d in self.doc.documents()]
        self.assertEqual(kinds, [
            ("PCB/%s.epcb" % _fx.PCB_UUID, "FOOTPRINT"),
            ("PCB/%s.epcb" % _fx.PCB_UUID, "PCB"),
            ("FOOTPRINT/%s.efoo" % _fx.FP_UUID, "FOOTPRINT"),
            ("SHEET/%s/1.esch" % _fx.SCH_UUID, "SCH"),
        ])

    def test_project_index_joins_boards_to_pcbs(self):
        project = self.doc.project()
        self.assertEqual(sorted(project), ["boards", "config", "devices",
                                           "footprints", "panels", "pcbs",
                                           "schematics", "symbols"])
        board = self.doc.boards()[0]
        self.assertEqual(board["name"], "Board1_1")
        self.assertEqual(board["pcb_uuid"], _fx.PCB_UUID)
        self.assertEqual(board["schematic_uuid"], _fx.SCH_UUID)
        self.assertEqual(board["member"], "PCB/%s.epcb" % _fx.PCB_UUID)
        self.assertTrue(board["in_project_index"])

    def test_adding_a_revision_keeps_the_old_members_untouched(self):
        """What the editor does on save: a new member plus a project.json edit."""
        new_name = "PCB/%s.epcb" % ("f" * 32)
        text = _fx.pcb_member_text()
        project = json.loads(self.doc.entry_bytes("project.json"))
        project["pcbs"]["f" * 32] = "PCB1_1"
        blob = self.doc.to_bytes({new_name: text,
                                  "project.json": json.dumps(project)})
        out = os.path.join(self.dir, "new.epro")
        with open(out, "wb") as fh:
            fh.write(blob)
        after = EproDocument.from_path(out)
        for name in self.doc.names():
            if name == "project.json":
                continue
            self.assertEqual(after.entry_bytes(name), self.doc.entry_bytes(name),
                             name)
        self.assertEqual(after.entry_bytes(new_name).decode("utf-8"), text)
        self.assertIn("f" * 32, after.project()["pcbs"])

    def test_sniff_reads_content_not_extension(self):
        self.assertTrue(epro_doc.sniff(self.path))
        plain = os.path.join(self.dir, "not_a_zip.epro")
        with open(plain, "w", encoding="utf-8") as fh:
            fh.write("this is not a zip\n")
        self.assertFalse(epro_doc.sniff(plain))
        self.assertFalse(epro_doc.looks_like_epro(plain))
        with self.assertRaises(epro_doc.EproError):
            EproDocument.from_path(plain)


# --------------------------------------------------------------- engine
class TestEngineIsTheReferee(unittest.TestCase):

    def setUp(self):
        if not engine.find_kicad_cli():
            self.skipTest("kicad-cli not installed")

    def test_roundtrip_survives_the_vendor_engine(self):
        doc = KiCadDocument.from_path(SAMPLE)
        r = engine.verify(doc)
        self.assertEqual(r["status"], "ok")
        self.assertTrue(r["identical"], r)
        self.assertTrue(r["checks"]["gerbers"]["identical"])
        self.assertTrue(r["checks"]["stats"]["identical"])
        self.assertTrue(r["checks"]["position"]["identical"])

    def test_engine_check_has_teeth(self):
        """Anti-false-green: drop the pour and the comparison must fail."""
        doc = KiCadDocument.from_path(SAMPLE)
        board = doc.board()
        board.items = [i for i in board.items
                       if not (isinstance(i, S.Seq) and S.head(i) == "zone"
                               and S.child(i, "net") is not None)]
        r = engine.verify(doc)
        self.assertFalse(r["identical"])
        self.assertFalse(r["checks"]["stats"]["identical"])
        self.assertTrue(r["checks"]["gerbers"]["layers_different"])

    def test_drc_is_authoritative_and_read_only(self):
        before = _sha(SAMPLE)
        r = engine.drc(SAMPLE)
        self.assertEqual(r["status"], "ok")
        self.assertTrue(r["authoritative"])
        self.assertIn("violations_total", r)
        self.assertEqual(_sha(SAMPLE), before)

    def test_stats_reports_the_copper_the_pour_really_covers(self):
        r = engine.stats(SAMPLE)
        self.assertEqual(r["status"], "ok")
        self.assertEqual(r["board"]["width"], "60.0000 mm")
        self.assertNotEqual(r["board"]["back_copper_area"], "0.000 mm²")

    def test_missing_file_is_refused_not_created(self):
        ghost = os.path.join(os.path.dirname(SAMPLE), "does_not_exist.kicad_pcb")
        r = engine.drc(ghost)
        self.assertEqual(r["status"], "error")
        self.assertFalse(os.path.exists(ghost))


if __name__ == "__main__":
    unittest.main()
