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
import io
import json
import os
import sys
import unittest
import zipfile

sys.path.insert(0, os.path.join(os.path.dirname(os.path.abspath(__file__)), "..", "src"))

from pcb_agent_mcp import fidelity                          # noqa: E402
from pcb_agent_mcp.fidelity import engine, sexpr as S        # noqa: E402
from pcb_agent_mcp.fidelity.epro_doc import EproDocument      # noqa: E402
from pcb_agent_mcp.fidelity.kicad_doc import KiCadDocument    # noqa: E402
from pcb_agent_mcp.fidelity.views import INTERPRETED_HEADS, BoardView  # noqa: E402

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
def _epro_bytes():
    """A tiny but realistic .epro: append-only log with a delete and a re-ticket."""
    lines_pcb = [
        '{"type":"NET","id":"n1","ticket":1}||{"netName":"GND"}',
        '{"type":"NET","id":"n1","ticket":2}||{"netName":"GND","color":"#ff0000"}',
        '{"type":"COMPONENT","id":"c1","ticket":1}||{"designator":"R1","x":100,"y":200}',
        '{"type":"COMPONENT","id":"c1","ticket":2}||{"designator":"R1","x":120,"y":200}',
        '{"type":"PAD","id":"p1","ticket":1}||{"parentId":"c1","number":"1","x":110,"y":200}',
        '{"type":"VIA","id":"v1","ticket":1}||{"x":300,"y":400}',
        '{"type":"VIA","id":"v1","ticket":2}||{}',
        '{"type":"ZZZ_FUTURE","id":"z1","ticket":1}||{"whatever":1}',
        'not a json line at all',
    ]
    buf = io.BytesIO()
    with zipfile.ZipFile(buf, "w", zipfile.ZIP_DEFLATED) as zf:
        zf.writestr("project.json", json.dumps({"name": "demo"}))
        zf.writestr("demo.epcb", "\n".join(lines_pcb) + "\n")
        zf.writestr("demo.eblob", b"\x00\x01\x02binary")
    return buf.getvalue()


class TestEproDocument(unittest.TestCase):

    def setUp(self):
        self.doc = EproDocument.from_bytes(_epro_bytes(), path="<memory>")

    def test_every_member_is_kept(self):
        self.assertEqual(sorted(self.doc.names()),
                         ["demo.eblob", "demo.epcb", "project.json"])
        self.assertEqual(self.doc.entry_bytes("demo.eblob"), b"\x00\x01\x02binary")

    def test_roundtrip_is_byte_identical(self):
        r = self.doc.verify()
        self.assertTrue(r["byte_identity"], r)
        self.assertTrue(all(m["byte_identity"] and m["state_idempotent"]
                            for m in r["members"].values()))

    def test_highest_ticket_wins(self):
        live = {r["id"]: r for r in self.doc.live("demo.epcb")}
        self.assertEqual(live["n1"]["payload"]["color"], "#ff0000")
        self.assertEqual(live["c1"]["payload"]["x"], 120)

    def test_empty_payload_means_deleted_and_is_recorded(self):
        live = {r["id"]: r for r in self.doc.live("demo.epcb")}
        self.assertNotIn("v1", live)
        gone = {r["id"]: r for r in self.doc.deleted("demo.epcb")}
        self.assertIn("v1", gone)
        self.assertTrue(gone["v1"]["deleted"])

    def test_unknown_type_is_carried_not_dropped(self):
        seen = self.doc.known_types_seen()
        self.assertEqual(seen["not_in_known_types"], {"ZZZ_FUTURE": 1})

    def test_unparsable_line_is_reported(self):
        bad = self.doc.unparsed("demo.epcb")
        self.assertEqual(len(bad), 1)
        self.assertIn("not a json line", bad[0][3])

    def test_key_inventory_turns_guessing_into_reading(self):
        inv = self.doc.key_inventory("demo.epcb")
        self.assertEqual(sorted(inv["COMPONENT"]["keys"].keys()), ["designator", "x", "y"])
        self.assertEqual(inv["COMPONENT"]["keys"]["x"]["sample"], 100)
        self.assertEqual(inv["COMPONENT"]["objects"], 2)

    def test_stats_accounts_for_every_line(self):
        st = self.doc.stats()
        self.assertEqual(st["lines_total"], 10)   # 9 log lines + project.json
        self.assertEqual(st["lines_unparsed"], 1)


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
