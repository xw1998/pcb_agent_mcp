"""Tests for the record-stream layer (`fidelity/lceda.py`).

This layer is the one that decides whether "unchanged lines keep their bytes" is
true, so the tests pin the exact mechanics that were measured on 20 real
projects (docs/13):

  * one compact JSON array per line, three different terminators inside one
    archive, a literal `[]` line and `\\t\\t`-indented lines;
  * a document boundary is a `["DOCTYPE", kind, version]` line;
  * editing one line leaves every other byte alone, and changing a field count
    is refused instead of producing a record the vendor never writes.
"""
import os
import sys
import unittest

_HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, os.path.join(_HERE, "..", "src"))
sys.path.insert(0, _HERE)

from pcb_agent_mcp.fidelity import lceda                      # noqa: E402
from pcb_agent_mcp.fidelity.lceda import (                    # noqa: E402
    LcedaError, Line, Member, dumps, dumps_spaced, from_mm, split_lines, to_mm)

import _lceda_fixtures as fx                                  # noqa: E402


class TestLines(unittest.TestCase):
    def test_three_terminators_are_kept_apart(self):
        text = '["A",1]\r\n["B",2]\n["C",3]'
        self.assertEqual(split_lines(text),
                         [('["A",1]', "\r\n"), ('["B",2]', "\n"), ('["C",3]', "")])

    def test_compact_and_spaced_forms(self):
        rec = ["LINE", 1, 2.5, "x"]
        self.assertEqual(dumps(rec), '["LINE",1,2.5,"x"]')
        self.assertEqual(dumps_spaced(rec), '["LINE", 1, 2.5, "x"]')

    def test_non_ascii_survives(self):
        self.assertEqual(dumps(["ATTR", "变压器"]), '["ATTR","变压器"]')

    def test_mil_conversion_is_one_constant(self):
        self.assertAlmostEqual(to_mm(400), 10.16, places=9)
        self.assertAlmostEqual(from_mm(10.16), 400.0, places=6)
        self.assertIsNone(to_mm(None))


class TestMember(unittest.TestCase):
    def setUp(self):
        self.text = fx.footprint_member_text()      # CRLF, `[]`, `\t\t`, no EOL
        self.member = Member("FP/x.efoo", self.text)

    def test_roundtrip_is_byte_identical(self):
        self.assertTrue(self.member.is_verbatim())
        report = self.member.roundtrip()
        self.assertTrue(report["verbatim"])
        self.assertEqual(report["n_other"], 0, report)

    def test_quirks_are_carried_not_normalised(self):
        lines = self.member.lines
        self.assertTrue(any(ln.is_empty_array for ln in lines))
        indented = [ln for ln in lines if ln.body.startswith("\t\t")]
        self.assertEqual(len(indented), 1)
        self.assertTrue(indented[0].body.lstrip().startswith('["ATTR"'))
        self.assertEqual(lines[-1].term, "")
        self.assertTrue(all(ln.term == "\r\n" for ln in lines[:-1]))

    def test_documents_split_on_doctype(self):
        two = Member("PCB/x.epcb", fx.pcb_member_text())
        self.assertEqual([d.kind for d in two.documents], ["FOOTPRINT", "PCB"])
        self.assertEqual(two.documents[1].version, "1.9")
        self.assertEqual(two.documents[1].display_unit(), "mm")

    def test_find_and_arity(self):
        doc = self.member.documents[0]
        self.assertEqual(doc.find("PAD", "ie1").record[5], "1")
        self.assertEqual(len(doc.find_all("PAD", "ie2")), 1)
        self.assertIsNone(doc.find("PAD", "nope"))
        self.assertEqual(doc.unique_arity("PAD"), 22)
        self.assertEqual(doc.unique_arity("ATTR"), 22)

    def test_editing_one_line_keeps_the_rest_verbatim(self):
        doc = self.member.documents[0]
        line = doc.find("PAD", "ie1")
        before = doc.find("PAD", "ie2").to_text()
        rec = list(line.record)
        rec[6] = -80
        self.member.set_record(line, rec)
        self.assertEqual(doc.find("PAD", "ie1").record[6], -80)
        self.assertEqual(doc.find("PAD", "ie2").to_text(), before)
        # every other byte is still what arrived
        out = self.member.to_text()
        self.assertEqual(out.count('["PAD","ie2"'), 1)
        self.assertIn("\r\n\t\t", out)          # the indented line kept tabs
        self.assertFalse(out.endswith("\n"))      # last line had no terminator
        self.assertNotIn("\n", out.replace("\r\n", ""))

    def test_changing_a_field_count_is_refused(self):
        doc = self.member.documents[0]
        line = doc.find("PAD", "ie1")
        with self.assertRaises(LcedaError):
            self.member.set_record(line, list(line.record)[:-1])
        with self.assertRaises(LcedaError):
            self.member.set_record(line, list(line.record) + [0])

    def test_mixed_arity_is_refused_not_guessed(self):
        text = "\r\n".join([
            '["DOCTYPE","FOOTPRINT","1.9"]',
            '["PAD","a",0,"",1,"1",0,0,0,null,["RECT",1,1,0],0]',
            '["PAD","b",0,"",1,"2",0,0,0,null,["RECT",1,1,0]]',
        ])
        doc = Member("FP/y.efoo", text).documents[0]
        self.assertEqual(doc.arity("PAD"), {12: 1, 11: 1})
        with self.assertRaises(LcedaError):
            doc.unique_arity("PAD")

    def test_a_line_that_is_not_json_raises(self):
        with self.assertRaises(LcedaError):
            Member("x.epcb", '["DOCTYPE","PCB","1.9"]\nnot json\n')

    def test_a_member_without_doctype_raises(self):
        with self.assertRaises(LcedaError):
            Member("x.epcb", '["NET","GND",1]\n')

    def test_unknown_tags_are_carried(self):
        text = "\n".join([
            '["DOCTYPE","PCB","1.9"]',
            '["ZZZ_FUTURE",1,{"a":2}]',
        ])
        member = Member("PCB/z.epcb", text)
        doc = member.documents[0]
        self.assertEqual(doc.first("ZZZ_FUTURE"), ["ZZZ_FUTURE", 1, {"a": 2}])
        self.assertEqual(member.stats()["unknown_tags"], ["ZZZ_FUTURE"])
        self.assertEqual(doc.find("ZZZ_FUTURE", 1).record[2], {"a": 2})

    def test_tag_layout_reports_field_counts_and_types(self):
        layout = self.member.tag_layout()["FOOTPRINT/PAD"]
        self.assertEqual(layout["count"], 3)
        self.assertEqual(layout["arity"], {22: 3})
        self.assertIn("int", layout["positions"]["6"])   # -50 */
        self.assertIn("NoneType", layout["positions"]["9"])
        self.assertIn("list", layout["positions"]["10"])

    def test_stats_counts_terminators(self):
        stats = self.member.stats()
        self.assertEqual(stats["documents"][0]["kind"], "FOOTPRINT")
        self.assertEqual(stats["terminators"].get(""), 1)


class TestMemberNames(unittest.TestCase):
    def test_only_stream_extensions(self):
        names = ["project.json", "PCB/", "PCB/a.epcb", "FOOTPRINT/b.efoo",
                 "SHEET/c/1.esch", "SYMBOL/d.esym", "notes.txt"]
        self.assertEqual(lceda.member_names_with_stream(names),
                         ["PCB/a.epcb", "FOOTPRINT/b.efoo", "SHEET/c/1.esch",
                          "SYMBOL/d.esym"])


class TestLineObject(unittest.TestCase):
    def test_tag_and_text(self):
        line = Line('["NET","GND"]', "\r\n", ["NET", "GND"], True)
        self.assertEqual(line.tag, "NET")
        self.assertFalse(line.is_blank)
        self.assertTrue(line.to_text().endswith("\r\n"))
        blank = Line("   ", "\n", None, False)
        self.assertTrue(blank.is_blank)
        self.assertIsNone(blank.tag)


if __name__ == "__main__":
    unittest.main()
