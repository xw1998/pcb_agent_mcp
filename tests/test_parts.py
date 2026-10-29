"""Part catalogue tests: the offline .elib reader and the EasyEDA -> KiCad converter.

Everything here runs against a synthetic catalogue built in a temp directory, so
the suite does not need LCEDA Pro installed and does not touch a real user
library. The one thing a fixture cannot prove is that the real vendor documents
parse the same way, so there is a separate opt-in test at the bottom that runs
only when a real ``lceda-std.elib`` is present.
"""
import io
import json
import os
import sqlite3
import sys
import tempfile
import unittest

sys.path.insert(0, os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "src"))

from pcb_agent_mcp import parts  # noqa: E402
from pcb_agent_mcp import tools  # noqa: E402

# --------------------------------------------------------------- fixtures
SYMBOL_DOC = "\n".join([
    '["DOCTYPE","SYMBOL","1.0"]',
    '["HEAD",{"symbolType":2,"originX":0,"originY":0,"version":"0.13.0"}]',
    '["PART","TEST.1",{"BBOX":[-70,-70,70,70]}]',
    '["ATTR","a1","","Symbol","TEST",false,false,null,null,0,"st2",0]',
    '["ATTR","a2","","Designator","U?",false,false,null,null,0,"st2",0]',
    '["RECT","e1",-70,-70,70,70,0,0,0,"st1",0]',
    '["PIN","p1",1,1,-90,50,20,0,null,0,0,1]',
    '["ATTR","n1","p1","NAME","VCC",false,true,-67,50,0,"st3",0]',
    '["ATTR","n2","p1","NUMBER","1",false,true,-76,54,0,"st4",0]',
    '["ATTR","n3","p1","Pin Type","PWR",false,false,null,null,0,"st2",0]',
    '["PIN","p2",1,1,90,-50,20,180,null,0,0,1]',
    '["ATTR","n4","p2","NAME","GND",false,true,67,-50,0,"st3",0]',
    '["ATTR","n5","p2","NUMBER","2",false,true,76,-54,0,"st4",0]',
    '["ATTR","n6","p2","Pin Type","GND",false,false,null,null,0,"st2",0]',
])

# Two SMD pads 100 mil apart on the top layer plus one silkscreen box: enough to
# exercise size, rotation, graphics and the layer map without a vendor file.
FOOTPRINT_DOC = "\n".join([
    '["DOCTYPE","FOOTPRINT","1.3"]',
    '["LAYER",1,"TOP","Top Layer",3,"#ff0000",1,"#7f0000",1]',
    '["LAYER",3,"TOP_SILK","Top Silkscreen Layer",3,"#ffcc00",1,"#7f6600",1]',
    '["LAYER",12,"MULTI","Multi-Layer",3,"#c0c0c0",1,"#606060",1]',
    '["LAYER",49,"COMPONENT_MARKING","Component Marking Layer",3,"#fff",1,"#000",1]',
    '["CANVAS",0,0,"mil",5,5]',
    '["ACTIVE_LAYER",1]',
    '["PAD","e1",0,"",1,"1",-50,0,0,null,["RECT",40,30,0],[],0,0,0,1,0,2,2,0,0,0]',
    '["PAD","e2",0,"",1,"2",50,0,0,null,["OVAL",40,20],[],0,0,90,1,0,2,2,0,0,0]',
    '["PAD","e3",0,"",12,"3",0,-100,0,["ROUND",20,20],["ELLIPSE",40,40],[],0,0,0,1,0,2,2,0,0,0]',
    '["POLY","g1",0,"",3,10,[-70,-70,"L",70,-70],0]',
    '["POLY","g2",0,"",3,10,[70,-70,"L",70,70],0]',
    '["FILL","g3",0,"",3,0.2,0,[-70,-70,"L",-60,-70,-60,-60,-70,-60,-70,-70],0]',
    '["FILL","g4",0,"",49,0.2,0,[[ "CIRCLE",0,0,5]],0]',
    '["ATTR","t1",0,"",3,null,null,"Footprint","TEST-FP",0,0,"default",45,6,0,0,3,0,0,0,0,0]',
])


def build_library(path):
    con = sqlite3.connect(path)
    con.executescript("""
        create table devices (uuid varchar, description varchar, title varchar,
            display_title varchar, images text, source varchar, version varchar,
            ticket integer, footprint_type integer, symbol_type integer,
            created_at datetime, updated_at datetime, createTime datetime,
            updateTime datetime, modifier_uuid varchar, creator_uuid varchar,
            owner_uuid varchar, project_uuid varchar, child_tag varchar,
            parent_tag varchar, custom_tags varchar, history_uuid varchar);
        create table components (uuid varchar, title varchar, display_title varchar,
            description varchar, source varchar, version varchar,
            created_at datetime, updated_at datetime, ticket integer, docType integer,
            dataStr text, createTime datetime, updateTime datetime,
            modifier_uuid varchar, creator_uuid varchar, owner_uuid varchar,
            project_uuid varchar, child_tag varchar, parent_tag varchar,
            custom_tags varchar, history_uuid varchar);
        create table attributes (key text, value text, device_uuid varchar);
    """)
    con.execute("insert into components (uuid,title,display_title,docType,dataStr) "
                "values (?,?,?,?,?)",
                ("sym1", "test", "TEST", 2, SYMBOL_DOC))
    con.execute("insert into components (uuid,title,display_title,docType,dataStr) "
                "values (?,?,?,?,?)",
                ("fp1", "test-fp", "TEST-FP", 4, FOOTPRINT_DOC))
    con.execute("insert into devices (uuid,title,display_title,description,"
                "footprint_type,symbol_type) values (?,?,?,?,?,?)",
                ("dev1", "testpart", "TESTPART", "a test device", 4, 2))
    for key, value in (("Supplier Part", "C999999"),
                       ("Manufacturer Part", "TESTPART-1"),
                       ("Manufacturer", "ACME"),
                       ("Supplier Footprint", "SOT-23"),
                       ("Datasheet", "https://example.invalid/ds.pdf"),
                       ("Symbol", "sym1"),
                       ("Footprint", "fp1"),
                       ("Program Memory Size", "64KB")):
        con.execute("insert into attributes (key,value,device_uuid) values (?,?,?)",
                    (key, value, "dev1"))
    con.commit()
    con.close()
    return path


class _WithLibrary(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.tmp = tempfile.mkdtemp(prefix="pcbparts")
        cls.lib = build_library(os.path.join(cls.tmp, "fake.elib"))


# ------------------------------------------------------- document readers
class TestFootprintDocument(_WithLibrary):
    def test_unit_is_read_from_the_document(self):
        doc = parts.parse_footprint_doc(FOOTPRINT_DOC)
        self.assertEqual(doc["unit"], "mil")

    def test_pads_convert_mil_to_mm(self):
        pads = parts.footprint_pads_mm(FOOTPRINT_DOC)
        self.assertEqual(len(pads), 3)
        p1 = [p for p in pads if p["number"] == "1"][0]
        self.assertAlmostEqual(p1["x"], -1.27, places=6)
        self.assertAlmostEqual(p1["w"], 1.016, places=6)
        self.assertEqual(p1["layer"], "F.Cu")

    def test_through_hole_pad_keeps_its_drill(self):
        pads = parts.footprint_pads_mm(FOOTPRINT_DOC)
        p3 = [p for p in pads if p["number"] == "3"][0]
        self.assertAlmostEqual(p3["drill"], 0.508, places=6)
        self.assertEqual(p3["layer"], "*.Cu")

    def test_pad_rotation_comes_from_field_14_not_field_8(self):
        # field 8 is 0 for both pads, field 14 is 90 for pad 2 only; a converter
        # that read field 8 would place a vertical oval where the vendor has a
        # horizontal one.
        pads = parts.footprint_pads_mm(FOOTPRINT_DOC)
        p2 = [p for p in pads if p["number"] == "2"][0]
        self.assertEqual(p2["rot"], 90)


class TestSymbolDocument(_WithLibrary):
    def test_pins_carry_name_number_and_electrical_type(self):
        doc = parts.parse_symbol_doc(SYMBOL_DOC)
        self.assertEqual(len(doc["pins"]), 2)
        first = doc["pins"][0]
        self.assertEqual(first["name"], "VCC")
        self.assertEqual(first["number"], "1")
        self.assertEqual(first["electrical"], "PWR")

    def test_pin_rotation_is_degrees(self):
        doc = parts.parse_symbol_doc(SYMBOL_DOC)
        self.assertEqual([p["rot"] for p in doc["pins"]], [0, 180])


# ---------------------------------------------------------- KiCad output
class TestFootprintToKiCad(_WithLibrary):
    def test_every_pad_reaches_the_file(self):
        text, report = parts.footprint_to_kicad(FOOTPRINT_DOC, name="TEST-FP")
        self.assertEqual(report["pads"], 3)
        self.assertEqual(text.count("(pad "), 3)
        self.assertTrue(text.startswith('(footprint "TEST-FP"'))
        self.assertEqual(text.count("("), text.count(")"))

    def test_through_hole_pad_is_written_as_thru_hole_with_drill(self):
        text, _ = parts.footprint_to_kicad(FOOTPRINT_DOC, name="TEST-FP")
        self.assertIn("(pad \"3\" thru_hole", text)
        self.assertIn("(drill 0.508)", text)

    def test_silkscreen_poly_becomes_lines_and_marking_layer_is_dropped(self):
        text, report = parts.footprint_to_kicad(FOOTPRINT_DOC, name="TEST-FP")
        self.assertEqual(text.count("fp_line"), 2)
        self.assertIn("graphic-on-layer-49", report["dropped"])
        self.assertIn("fp_poly", text)

    def test_pad_angle_is_written(self):
        text, _ = parts.footprint_to_kicad(FOOTPRINT_DOC, name="TEST-FP")
        self.assertIn('(at 1.27 0 90)', text)

    def test_a_millimetre_document_is_scaled_not_assumed_to_be_mil(self):
        # 131 of the 4 044 footprints in a real std library declare "mm"; reading
        # their coordinates as mil would shrink the part 39.37x.
        mm = FOOTPRINT_DOC.replace('"mil"', '"mm"')
        pads = parts.footprint_pads_mm(mm)
        p1 = [p for p in pads if p["number"] == "1"][0]
        self.assertAlmostEqual(p1["x"], -50.0, places=6)
        self.assertAlmostEqual(p1["w"], 40.0, places=6)
        text, report = parts.footprint_to_kicad(mm, name="X")
        self.assertEqual(report["unit"], "mm")
        self.assertIn("(at -50 0", text)

    def test_an_unknown_unit_is_refused_not_guessed(self):
        bad = FOOTPRINT_DOC.replace('"mil"', '"furlong"')
        with self.assertRaises(parts.PartError) as ctx:
            parts.footprint_to_kicad(bad, name="X")
        self.assertEqual(ctx.exception.reason_code, "UNIT_UNVERIFIED")
        with self.assertRaises(parts.PartError):
            parts.footprint_pads_mm(bad)


class TestSymbolToKiCad(_WithLibrary):
    def test_pins_and_body_are_written(self):
        text, report = parts.symbol_to_kicad(SYMBOL_DOC, name="TEST")
        self.assertEqual(report["pins"], 2)
        self.assertEqual(text.count("(pin "), 2)
        self.assertIn("(name \"VCC\"", text)
        self.assertIn("(number \"1\"", text)
        self.assertEqual(text.count("("), text.count(")"))

    def test_power_pin_type_is_mapped(self):
        text, _ = parts.symbol_to_kicad(SYMBOL_DOC, name="TEST")
        self.assertIn("(pin power_in line", text)
        self.assertIn("(pin power_in line", text)

    def test_a_document_without_pins_is_refused(self):
        with self.assertRaises(parts.PartError) as ctx:
            parts.symbol_to_kicad('["DOCTYPE","SYMBOL","1.0"]', name="X")
        self.assertEqual(ctx.exception.reason_code, "SYMBOL_EMPTY")


# -------------------------------------------------------------- catalogue
class TestOfflineCatalogue(_WithLibrary):
    def test_search_by_title(self):
        out = parts.search_offline("testpart", path=self.lib)
        self.assertEqual(out["total"], 1)
        part = out["parts"][0]
        self.assertEqual(part["lcsc"], "C999999")
        self.assertEqual(part["symbol_uuid"], "sym1")

    def test_search_by_attribute_value(self):
        out = parts.search_offline("C999999", path=self.lib)
        self.assertEqual(out["total"], 1)
        self.assertEqual(out["parts"][0]["uuid"], "dev1")

    def test_search_is_case_insensitive(self):
        self.assertEqual(parts.search_offline("TeStPaRt", path=self.lib)["total"], 1)

    def test_miss_is_zero_not_an_error(self):
        self.assertEqual(parts.search_offline("nope-nope", path=self.lib)["total"], 0)

    def test_empty_query_is_refused(self):
        with self.assertRaises(parts.PartError) as ctx:
            parts.search_offline("   ", path=self.lib)
        self.assertEqual(ctx.exception.reason_code, "ARG_INVALID")

    def test_get_returns_every_attribute(self):
        part = parts.get_offline("dev1", path=self.lib)
        self.assertEqual(part["attributes"]["Manufacturer"], "ACME")
        self.assertEqual(part["package"], "SOT-23")

    def test_missing_uuid_is_named(self):
        with self.assertRaises(parts.PartError) as ctx:
            parts.get_offline("does-not-exist", path=self.lib)
        self.assertEqual(ctx.exception.reason_code, "PART_NOT_FOUND")

    def test_library_status_counts_typed_documents(self):
        info = parts.library_status(self.lib)
        self.assertEqual(info["counts"]["devices"], 1)
        self.assertEqual(info["counts"]["symbols"], 1)
        self.assertEqual(info["counts"]["footprints"], 1)

    def test_missing_library_error_lists_where_it_looked(self):
        with self.assertRaises(parts.PartError) as ctx:
            parts.find_library(os.path.join(self.tmp, "nope.elib"))
        self.assertEqual(ctx.exception.reason_code, "LIBRARY_NOT_FOUND")
        self.assertIn("looked in", str(ctx.exception))

    def test_env_variable_is_honoured(self):
        old = os.environ.get(parts.STD_LIB_ENV)
        os.environ[parts.STD_LIB_ENV] = self.lib
        try:
            self.assertEqual(parts.find_library()["path"], self.lib)
        finally:
            if old is None:
                os.environ.pop(parts.STD_LIB_ENV, None)
            else:
                os.environ[parts.STD_LIB_ENV] = old


# ------------------------------------------------------------ tool layer
class TestPartTools(_WithLibrary):
    def test_search_tool_answers_with_the_envelope(self):
        out = tools.call_tool("part_search", {"query": "testpart",
                                              "path": self.lib,
                                              "source": "offline"})
        self.assertEqual(out["status"], "ok")
        self.assertEqual(out["value"], 1)
        self.assertEqual(out["source"], "offline")
        self.assertTrue(out["basis"])

    def test_search_tool_names_the_source_it_used(self):
        out = tools.call_tool("part_search", {"query": "testpart",
                                              "path": self.lib,
                                              "source": "offline"})
        self.assertIn("lceda-std.elib", out["basis"])

    def test_export_tool_writes_two_files_and_reads_them_back(self):
        out_dir = os.path.join(self.tmp, "out")
        out = tools.call_tool("part_export", {"uuid": "dev1", "out_dir": out_dir,
                                              "path": self.lib, "name": "TESTPART"})
        self.assertEqual(out["status"], "ok", out)
        self.assertEqual(out["value"], 2)
        sym = os.path.join(out_dir, "TESTPART.kicad_sym")
        mod = os.path.join(out_dir, "TESTPART.kicad_mod")
        self.assertTrue(os.path.isfile(sym))
        self.assertTrue(os.path.isfile(mod))
        with io.open(mod, "r", encoding="utf-8") as fh:
            self.assertIn("(pad \"1\"", fh.read())
        self.assertEqual(sorted(out["report"].keys()), ["footprint", "symbol"])

    def test_export_refuses_to_clobber(self):
        out_dir = os.path.join(self.tmp, "out2")
        first = tools.call_tool("part_export", {"uuid": "dev1", "out_dir": out_dir,
                                                "path": self.lib, "name": "T2"})
        self.assertEqual(first["status"], "ok")
        second = tools.call_tool("part_export", {"uuid": "dev1", "out_dir": out_dir,
                                                 "path": self.lib, "name": "T2"})
        self.assertEqual(second["status"], "error")
        self.assertEqual(second["reason_code"], "TARGET_EXISTS")

    def test_export_names_a_device_without_geometry(self):
        con = sqlite3.connect(self.lib)
        con.execute("insert into devices (uuid,title,display_title) values (?,?,?)",
                    ("dev2", "no geo", "NO-GEO"))
        con.commit()
        con.close()
        out = tools.call_tool("part_export", {"uuid": "dev2",
                                              "out_dir": os.path.join(self.tmp, "o3"),
                                              "path": self.lib})
        self.assertEqual(out["status"], "error")
        self.assertEqual(out["reason_code"], "SYMBOL_MISSING")

    def test_pads_tool_returns_mm_geometry(self):
        out = tools.call_tool("part_footprint_pads", {"footprint_uuid": "fp1",
                                                      "path": self.lib})
        self.assertEqual(out["status"], "ok")
        self.assertEqual(out["value"], 3)
        self.assertAlmostEqual(out["pads"][0]["x"], -1.27, places=6)

    def test_unknown_footprint_is_refused(self):
        out = tools.call_tool("part_footprint_pads", {"footprint_uuid": "nope",
                                                      "path": self.lib})
        self.assertEqual(out["status"], "error")
        self.assertEqual(out["reason_code"], "DOCUMENT_MISSING")


REAL_LIB = os.environ.get(parts.STD_LIB_ENV)


@unittest.skipUnless(REAL_LIB and os.path.isfile(REAL_LIB),
                     "set %s to a real lceda-std.elib to run this" % parts.STD_LIB_ENV)
class TestRealVendorDocuments(unittest.TestCase):
    """Opt-in: the synthetic fixture proves the reader, this proves the vendor."""

    def test_a_real_device_converts_with_pads_pins_and_a_report(self):
        out = parts.search_offline("STM32F401", limit=1, path=REAL_LIB)
        self.assertTrue(out["parts"])
        part = out["parts"][0]
        datas = parts.data_strings([part["symbol_uuid"], part["footprint_uuid"]],
                                   path=REAL_LIB)
        text, report = parts.footprint_to_kicad(
            datas[part["footprint_uuid"]]["data"], name="REAL")
        self.assertGreater(report["pads"], 8)
        self.assertEqual(text.count("("), text.count(")"))
        sym, srep = parts.symbol_to_kicad(datas[part["symbol_uuid"]]["data"],
                                          name="REAL")
        self.assertGreater(srep["pins"], 8)
        self.assertEqual(sym.count("("), sym.count(")"))


if __name__ == "__main__":
    unittest.main()
