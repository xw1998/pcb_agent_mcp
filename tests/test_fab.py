"""Tests for the manufacturing layer: delegation plus re-reading, never guessing.

The four claims the module makes, and what each test holds it to:

* **the artefacts are KiCad's, not mine** - every byte in the package comes from
  `kicad-cli`, and with no `kicad-cli` the answer is `KICAD_CLI_NOT_FOUND`
  rather than a silent fallback writer;
* **a file that exists is not a file that works** - each artefact is re-read and
  must satisfy the property that makes it usable (M02* terminator, drill tool
  table, position columns, ISO-10303-21 header).  The checkers are tested
  directly on hand-written good/bad files, so a broken checker fails here
  instead of quietly passing a broken export;
* **the user's project is never written to** - an `out_dir` inside the source
  directory is refused, and the default output is a temp directory that is not
  inside the source tree;
* **an empty NPTH file is a fact about the board, not a failed export** - a board
  with no non-plated holes still gets a valid M48 header with no tools, and that
  must not be reported as a problem.

The end-to-end tests run the real `kicad-cli` on `tests/data/sample.kicad_pcb`
(a 60x40 mm two-layer board) and are skipped when the binary is absent.  The
real-board numbers (9 Gerber layers, 748 KB zip) live in docs/27.
"""
import hashlib
import os
import sys
import tempfile
import unittest
import zipfile

_HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, os.path.join(_HERE, "..", "src"))
sys.path.insert(0, _HERE)

from pcb_agent_mcp import fab                                  # noqa: E402
from pcb_agent_mcp.fab import FabError                          # noqa: E402
from pcb_agent_mcp.fidelity import engine                       # noqa: E402

SAMPLE = os.path.join(_HERE, "data", "sample.kicad_pcb")


def _sha256(path):
    h = hashlib.sha256()
    with open(path, "rb") as fh:
        for chunk in iter(lambda: fh.read(1 << 20), b""):
            h.update(chunk)
    return h.hexdigest()


def _write(directory, name, text):
    path = os.path.join(directory, name)
    with open(path, "w", encoding="utf-8", newline="") as fh:
        fh.write(text)
    return path


GOOD_GERBER = """%TF.FileFunction,Copper,L1,Top*%
%FSLAX46Y46*%
%MOMM*%
G01*
D10*
X1000000Y1000000D02*
X1000000Y1000000D03*
X2000000Y1000000D03*
M02*
"""

TRUNCATED_GERBER = """%FSLAX46Y46*%
%MOMM*%
G01*
D10*
X1000000Y1000000D02*
X1000000Y1000000D03*
"""

GOOD_DRILL = """M48
;FILE_FORMAT=4:4
;TYPE=PLATED
FMAT,2
METRIC,TZ
T1C0.300
T2C0.600
%
G90
G05
T1
X19.05Y19.05
T2
X20.32Y20.32
X21.59Y20.32
T0
M30
"""

EMPTY_NPTH_DRILL = """M48
;FILE_FORMAT=4:4
;TYPE=NON_PLATED
FMAT,2
METRIC
%
G90
G05
T0
M30
"""

GOOD_POS = """Ref,Val,Package,PosX,PosY,Rot,Side
R1,10k,R_0805,10.0,10.0,90.0,top
C1,100n,C_0603,10.0,20.0,0.0,top
"""

BAD_POS = """Ref,Val,Package,PosX,PosY
R1,10k,R_0805,10.0,10.0
"""

NETLIST = """(export (version "E")
  (design (source "t.kicad_sch") (tool "Eeschema"))
  (components
    (comp (ref "R1") (value "10k") (footprint "Test:R_0805"))
    (comp (ref "C1") (value "100n") (footprint "Test:C_0603")))
  (nets
    (net (code "1") (name "GND")
      (node (ref "R1") (pin "1"))
      (node (ref "C1") (pin "2")))
    (net (code "2") (name "VCC")
      (node (ref "R1") (pin "2")))))
"""


class TestOutDirPolicy(unittest.TestCase):
    """The 'never write beside the user's design' rule, tested on a real path."""

    def setUp(self):
        self.src_dir = os.path.dirname(SAMPLE)

    def test_refuses_out_dir_inside_the_source_tree(self):
        inside = os.path.join(self.src_dir, "fab_out")
        with self.assertRaises(FabError) as caught:
            fab._resolve_out(SAMPLE, inside, False)
        self.assertEqual(caught.exception.reason_code, "OUT_DIR_BESIDE_SOURCE")
        self.assertFalse(os.path.exists(inside),
                         "a refused output directory must not be created")

    def test_refuses_the_source_directory_itself(self):
        with self.assertRaises(FabError) as caught:
            fab._resolve_out(SAMPLE, self.src_dir, False)
        self.assertEqual(caught.exception.reason_code, "OUT_DIR_BESIDE_SOURCE")

    def test_allow_beside_source_lets_it_through(self):
        inside = os.path.join(self.src_dir, "fab_out_allowed")
        try:
            got = fab._resolve_out(SAMPLE, inside, True)
            self.assertEqual(os.path.abspath(got), os.path.abspath(inside))
            self.assertTrue(os.path.isdir(got))
        finally:
            try:
                os.rmdir(inside)
            except OSError:
                pass

    def test_default_output_is_a_temp_dir_outside_the_source_tree(self):
        got = fab._resolve_out(SAMPLE, None, False)
        self.assertTrue(os.path.isdir(got))
        self.assertFalse(fab._is_inside(got, self.src_dir))

    def test_sibling_directory_with_a_shared_prefix_is_not_inside(self):
        # "data2" must not be treated as being inside "data"
        self.assertFalse(fab._is_inside(os.path.join(self.src_dir, "..", "data2"),
                                        self.src_dir))
        self.assertTrue(fab._is_inside(os.path.join(self.src_dir, "x", "y"),
                                       self.src_dir))


class TestArtefactChecks(unittest.TestCase):
    """A checker that passes a broken file is worse than no checker."""

    def setUp(self):
        self.tmp = tempfile.mkdtemp(prefix="pcb_fab_test_")

    def tearDown(self):
        for name in os.listdir(self.tmp):
            try:
                os.remove(os.path.join(self.tmp, name))
            except OSError:
                pass
        try:
            os.rmdir(self.tmp)
        except OSError:
            pass

    def test_good_gerber_passes_and_counts_flashes(self):
        path = _write(self.tmp, "g.gtl", GOOD_GERBER)
        got = fab._check_gerber(path)
        self.assertEqual(got["problems"], [])
        self.assertTrue(got["terminated"])
        self.assertTrue(got["x2_attributes"])
        self.assertEqual(got["flashes"], 2)

    def test_truncated_gerber_is_a_problem(self):
        path = _write(self.tmp, "t.gtl", TRUNCATED_GERBER)
        got = fab._check_gerber(path)
        self.assertTrue(any("M02*" in p for p in got["problems"]))
        self.assertFalse(got["terminated"])

    def test_gerber_without_a_header_is_a_problem(self):
        path = _write(self.tmp, "h.gtl", "G01*\nX0Y0D03*\nM02*\n")
        self.assertTrue(any("%FS" in p for p in fab._check_gerber(path)["problems"]))

    def test_good_drill_reports_tools_and_hits(self):
        path = _write(self.tmp, "p.drl", GOOD_DRILL)
        got = fab._check_drill(path)
        self.assertEqual(got["problems"], [])
        self.assertEqual(got["tools_mm"], [0.3, 0.6])
        self.assertEqual(got["hits"], 3)
        self.assertFalse(got["empty_slot"])

    def test_empty_npth_slot_is_not_a_problem(self):
        path = _write(self.tmp, "b-NPTH.drl", EMPTY_NPTH_DRILL)
        got = fab._check_drill(path)
        self.assertEqual(got["problems"], [])
        self.assertTrue(got["empty_slot"])
        self.assertTrue(got["non_plated"])

    def test_drill_with_hits_but_no_tool_table_is_a_problem(self):
        path = _write(self.tmp, "h.drl", "M48\nMETRIC\n%\nT1\nX1Y1\nM30\n")
        got = fab._check_drill(path)
        self.assertTrue(any("tool table" in p for p in got["problems"]))

    def test_drill_without_a_header_is_a_problem(self):
        path = _write(self.tmp, "n.drl", "X1Y1\nX2Y2\n")
        got = fab._check_drill(path)
        self.assertTrue(any("M48" in p for p in got["problems"]))

    def test_good_pos_reports_rows_and_columns(self):
        path = _write(self.tmp, "positions.csv", GOOD_POS)
        got = fab._check_pos(path)
        self.assertEqual(got["problems"], [])
        self.assertEqual(got["rows"], 2)
        self.assertEqual(got["columns"][0], "Ref")

    def test_pos_missing_side_and_rot_columns_is_a_problem(self):
        path = _write(self.tmp, "bad.csv", BAD_POS)
        problems = fab._check_pos(path)["problems"]
        self.assertTrue(any("rot" in p for p in problems))
        self.assertTrue(any("side" in p for p in problems))

    def test_pos_with_no_component_rows_is_a_problem(self):
        path = _write(self.tmp, "empty.csv", "Ref,PosX,PosY,Rot,Side\n")
        self.assertTrue(any("no component rows" in p
                            for p in fab._check_pos(path)["problems"]))

    def test_step_header_is_checked(self):
        good = os.path.join(self.tmp, "a.step")
        with open(good, "w", encoding="utf-8") as fh:
            fh.write("ISO-10303-21;\nHEADER;\nENDSEC;\n")
        self.assertEqual(fab._check_step(good)["problems"], [])
        bad = _write(self.tmp, "b.step", "solid board\nendsolid\n")
        self.assertTrue(fab._check_step(bad)["problems"])


class TestBomAndNetlist(unittest.TestCase):
    """BOM header must survive as written; netlists are walked, not counted."""

    def setUp(self):
        self.tmp = tempfile.mkdtemp(prefix="pcb_fab_test_")

    def tearDown(self):
        for name in os.listdir(self.tmp):
            try:
                os.remove(os.path.join(self.tmp, name))
            except OSError:
                pass
        try:
            os.rmdir(self.tmp)
        except OSError:
            pass

    def test_bom_rows_keep_the_header_as_written(self):
        path = _write(self.tmp, "bom.csv",
                      '"Reference","Value","Footprint","QUANTITY","DNP"\n'
                      '"R1,R2","10k","R_0805","2",""\n'
                      '"C1","100n","C_0603","1","DNP"\n'
                      '\n')
        rows = fab.bom_rows(path)
        self.assertEqual(len(rows), 2, "blank trailing line must not become a row")
        self.assertEqual(list(rows[0].keys()),
                         ["Reference", "Value", "Footprint", "QUANTITY", "DNP"])
        self.assertEqual(rows[0]["Reference"], "R1,R2")
        self.assertEqual(int(rows[0]["QUANTITY"]), 2)

    def test_netlist_is_walked_not_grepped(self):
        path = _write(self.tmp, "n.net", NETLIST)
        got = fab.netlist_summary(path, "kicadsexpr")
        self.assertTrue(got.get("parsed"))
        self.assertEqual(got["nets"], 2)
        self.assertEqual(got["nodes"], 3)
        self.assertEqual(got["components"], 2)
        self.assertEqual(got["net_names"], ["GND", "VCC"])
        self.assertEqual(got["refs"], ["R1", "C1"])

    def test_netlist_written_one_node_per_line_still_counts(self):
        # KiCad reformats freely; a substring count would be wrong here.
        one_per_line = NETLIST.replace("(net (code \"1\") (name \"GND\")",
                                       "(net\n      (code \"1\")\n      (name \"GND\")")
        path = _write(self.tmp, "n2.net", one_per_line)
        got = fab.netlist_summary(path, "kicadsexpr")
        self.assertEqual((got["nets"], got["nodes"], got["components"]), (2, 3, 2))

    def test_unparsable_netlist_says_so_instead_of_inventing_counts(self):
        path = _write(self.tmp, "junk.net", "(export (version \"E\")\n")
        got = fab.netlist_summary(path, "kicadsexpr")
        self.assertFalse(got.get("parsed", True))
        self.assertNotIn("nets", got)

    def test_non_tree_format_falls_back_to_a_labelled_line_count(self):
        path = _write(self.tmp, "n.xml", "<netlist>\n<net/>\n</netlist>\n")
        got = fab.netlist_summary(path, "kicadxml")
        self.assertIn("rough string count", got["parsed"])
        self.assertEqual(got["nets"], 1)


class TestRefusals(unittest.TestCase):
    """Bad input must be a reason code, never a half-written file."""

    def test_missing_project_is_refused(self):
        with self.assertRaises(FabError) as caught:
            fab.package(os.path.join(_HERE, "data", "nope.kicad_pcb"))
        self.assertEqual(caught.exception.reason_code, "PROJECT_NOT_FOUND")

    def test_unknown_format_lists_the_known_ones(self):
        with self.assertRaises(FabError) as caught:
            fab.package(SAMPLE, formats=("gerbers", "hologram"))
        self.assertEqual(caught.exception.reason_code, "ARG_INVALID")
        self.assertIn("gerbers", caught.exception.hint or "")

    def test_no_formats_is_refused(self):
        with self.assertRaises(FabError) as caught:
            fab.package(SAMPLE, formats=())
        self.assertEqual(caught.exception.reason_code, "ARG_INVALID")

    def test_unknown_graphic_format_is_refused(self):
        with self.assertRaises(FabError) as caught:
            fab.graphics(SAMPLE, fmt="tiff")
        self.assertEqual(caught.exception.reason_code, "ARG_INVALID")

    def test_unknown_3d_format_is_refused(self):
        with self.assertRaises(FabError) as caught:
            fab.threed(SAMPLE, fmt="obj")
        self.assertEqual(caught.exception.reason_code, "ARG_INVALID")

    def test_unknown_schematic_format_is_refused(self):
        with self.assertRaises(FabError) as caught:
            fab.schematic_export(SAMPLE, fmt="doc")
        self.assertEqual(caught.exception.reason_code, "ARG_INVALID")

    def test_known_formats_are_documented(self):
        for key in ("gerbers", "drill", "pos", "step", "ipc2581", "odb",
                    "ipcd356", "gencad"):
            self.assertIn(key, fab.FORMATS)
            self.assertTrue(fab.FORMATS[key].strip())


class TestWithoutKiCad(unittest.TestCase):
    """No engine means an error code, not a homemade Gerber."""

    def setUp(self):
        self.saved = engine._CACHE.get("exe", None)
        self.had = "exe" in engine._CACHE
        engine._CACHE["exe"] = None

    def tearDown(self):
        if self.had:
            engine._CACHE["exe"] = self.saved
        else:
            engine._CACHE.pop("exe", None)

    def test_package_refuses_and_writes_nothing(self):
        out = tempfile.mkdtemp(prefix="pcb_fab_nokicad_")
        try:
            with self.assertRaises(FabError) as caught:
                fab.package(SAMPLE, out_dir=out, formats=("gerbers",))
            self.assertEqual(caught.exception.reason_code, "KICAD_CLI_NOT_FOUND")
            self.assertEqual(os.listdir(out), [],
                             "a refused export must not leave files behind")
        finally:
            try:
                os.rmdir(out)
            except OSError:
                pass

    def test_graphics_and_3d_refuse_the_same_way(self):
        for call in (lambda: fab.graphics(SAMPLE, fmt="svg"),
                     lambda: fab.threed(SAMPLE, fmt="step"),
                     lambda: fab.schematic_export(SAMPLE, fmt="pdf")):
            with self.assertRaises(FabError) as caught:
                call()
            self.assertEqual(caught.exception.reason_code, "KICAD_CLI_NOT_FOUND")


@unittest.skipUnless(engine.find_kicad_cli(), "kicad-cli is not installed")
class TestEnginePackage(unittest.TestCase):
    """End to end on a real board: the package is KiCad's, and it re-reads OK."""

    @classmethod
    def setUpClass(cls):
        cls.tmp = tempfile.mkdtemp(prefix="pcb_fab_e2e_")
        cls.out = os.path.join(cls.tmp, "out")
        cls.src_sha = _sha256(SAMPLE)
        cls.got = fab.package(SAMPLE, out_dir=cls.out,
                              formats=("gerbers", "drill", "pos"))

    @classmethod
    def tearDownClass(cls):
        import shutil
        shutil.rmtree(cls.tmp, ignore_errors=True)
        import time
        time.sleep(0.05)
        for base, dirs, files in os.walk(cls.tmp, topdown=False):
            for name in files:
                try:
                    os.remove(os.path.join(base, name))
                except OSError:
                    pass
            for name in dirs:
                try:
                    os.rmdir(os.path.join(base, name))
                except OSError:
                    pass
        try:
            os.rmdir(cls.tmp)
        except OSError:
            pass

    def test_every_format_succeeded(self):
        self.assertEqual(self.got["status"], "ok",
                         "problems: %s" % self.got["problems"])
        self.assertEqual(self.got["problems"], [])
        for fmt in ("gerbers", "drill", "pos"):
            self.assertEqual(self.got["formats"][fmt]["status"], "ok")

    def test_counts_come_from_the_artefacts(self):
        counts = self.got["counts"]
        # this board's stored plot settings ask for 2 copper + F.SilkS + Edge.Cuts
        self.assertGreaterEqual(counts["gerber_layers"], 4)
        self.assertGreaterEqual(counts["drill_files"], 1)
        self.assertEqual(counts["pos_rows"], 2)

    def test_gerber_and_drill_actually_read_back(self):
        by_name = {f["name"]: f for f in self.got["files"]}
        gerbers = [f for n, f in by_name.items()
                   if n.endswith(".gbr") and "F_Cu" in n]
        self.assertTrue(gerbers, "no top-copper Gerber in %s" % sorted(by_name))
        self.assertGreater(gerbers[0]["check"]["flashes"], 0)
        drills = [f for n, f in by_name.items() if n.endswith(".drl")]
        self.assertTrue(drills)
        self.assertTrue(any(f["check"]["hits"] > 0 for f in drills),
                        "a via must leave a plated drill hit")

    def test_manifest_matches_the_files_on_disk(self):
        import json
        with open(self.got["manifest"], "r", encoding="utf-8") as fh:
            man = json.load(fh)
        self.assertEqual(man["schema"], "pcb_agent_mcp/fab/1")
        self.assertEqual(man["source"]["sha256"], self.src_sha)
        self.assertEqual(man["source"]["sha256"], self.got["source"]["sha256"])
        self.assertEqual(man["problems"], [])
        for entry in man["files"]:
            full = os.path.join(self.out, entry["name"])
            self.assertTrue(os.path.isfile(full), entry["name"])
            self.assertEqual(_sha256(full), entry["sha256"], entry["name"])

    def test_source_file_is_untouched(self):
        self.assertEqual(_sha256(SAMPLE), self.src_sha,
                         "the design file was modified by an export")

    def test_zip_contains_the_artefacts_and_the_manifest(self):
        self.assertTrue(self.got["zip"])
        with zipfile.ZipFile(self.got["zip"]) as zf:
            names = zf.namelist()
        self.assertIn("manifest.json", names)
        self.assertTrue(any(n.endswith(".gbr") for n in names))
        self.assertTrue(any(n.endswith(".drl") for n in names))

    def test_nothing_was_written_outside_the_output_directory(self):
        self.assertFalse(fab._is_inside(self.out, os.path.dirname(SAMPLE)))

    def test_out_dir_beside_source_is_refused_end_to_end(self):
        beside = os.path.join(os.path.dirname(SAMPLE), "fab_beside")
        with self.assertRaises(FabError) as caught:
            fab.package(SAMPLE, out_dir=beside, formats=("gerbers",))
        self.assertEqual(caught.exception.reason_code, "OUT_DIR_BESIDE_SOURCE")
        self.assertFalse(os.path.exists(beside))

    def test_graphics_plot_needs_and_gets_a_layer_list(self):
        out = os.path.join(self.tmp, "plot")
        got = fab.graphics(SAMPLE, fmt="svg", out_dir=out)
        self.assertEqual(got["status"], "ok")
        self.assertIn("F.Cu", got["layers"])
        self.assertIn("Edge.Cuts", got["layers"])
        self.assertTrue(got["files"])
        self.assertTrue(got["files"][0]["name"].endswith(".svg"))

    def test_board_layers_are_read_from_the_board(self):
        names = fab.board_layers(SAMPLE)
        self.assertIn("F.Cu", names)
        self.assertIn("Edge.Cuts", names)


if __name__ == "__main__":
    unittest.main()
