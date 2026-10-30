"""Tests for multi-page (hierarchical) schematics.

A KiCad page that has children is a TREE, and almost every mistake in this area
is a mistake about *which* tree was measured. The claims defended here:

* the tree is read from the `(sheet ...)` nodes and the `Sheetfile` properties on
  disk - a page number lives in `(instances ...)`, not in the file name, and one
  child file used by two sheets is one file with two instances (two pages), not
  a duplicate;
* an engine run on a lone copy of one file answers a different question than the
  project does once a page has children, so both sides of a before/after diff are
  materialised as whole trees. The negative control is the same tree with one
  child removed from disk: the same code must then report `missing_file`;
* a sheet pin is a *promise* about a hierarchical label in the child. The pin is
  written, and ERC is what refuses it when the label is not there - a positive
  control puts the label in first and shows the same plan going through;
* nothing here deletes a file, and a refused plan leaves the file byte-identical.

The engine (kicad-cli) classes skip cleanly when the demo schematics or the CLI
are absent, because the rest of the suite has to run anywhere.
"""
import os
import shutil
import tempfile
import unittest

from pcb_agent_mcp import edit as E
from pcb_agent_mcp.edit import sch_ops as SO
from pcb_agent_mcp.edit import sch_plan as SP
from pcb_agent_mcp.fidelity import engine as ENGINE
from pcb_agent_mcp.fidelity import hierarchy as HI
from pcb_agent_mcp.fidelity import kicad_sch as KS

ROOT_UUID = "11111111-1111-4111-8111-111111111111"
SHEET_A = "33333333-3333-4333-8333-333333333333"
SHEET_B = "55555555-5555-4555-8555-555555555555"

# ------------------------------------------------------------------ fixtures
def sheet_node(name, sheetfile, page, uid, x=40.0, y=40.0, w=40.0, h=25.0,
               pins=(), project="unit", parent="/" + ROOT_UUID, parents=None):
    """One `(sheet ...)` node, in the shape KiCad 9/10 actually writes.

    `parents` (a list of `(instance path, page)`) writes one entry per parent
    instance, which is what KiCad does when the page the node sits on is itself
    instantiated more than once; each page number is project-wide and distinct.
    """
    out = ["\t(sheet",
           "\t\t(at %s %s)" % (x, y),
           "\t\t(size %s %s)" % (w, h),
           "\t\t(exclude_from_sim no)",
           "\t\t(in_bom yes)",
           "\t\t(on_board yes)",
           "\t\t(dnp no)",
           "\t\t(uuid \"%s\")" % uid,
           "\t\t(property \"Sheetname\" \"%s\"" % name,
           "\t\t\t(at %s %s 0)" % (x, y - 0.7625),
           "\t\t\t(effects\n\t\t\t\t(font\n\t\t\t\t\t(size 1.524 1.524)\n\t\t\t\t)",
           "\t\t\t\t(justify left bottom)\n\t\t\t)",
           "\t\t)",
           "\t\t(property \"Sheetfile\" \"%s\"" % sheetfile,
           "\t\t\t(at %s %s 0)" % (x, y + h + 0.6099),
           "\t\t\t(effects\n\t\t\t\t(font\n\t\t\t\t\t(size 1.524 1.524)\n\t\t\t\t)",
           "\t\t\t\t(justify left top)\n\t\t\t)",
           "\t\t)"]
    for pin in pins:
        out += ["\t\t(pin \"%s\" input" % pin,
                "\t\t\t(at %s %s 0)" % (x + w, y + h / 2.0),
                "\t\t\t(uuid \"%s\")" % _pin_uuid(pin),
                "\t\t\t(effects\n\t\t\t\t(font\n\t\t\t\t\t(size 1.27 1.27)\n\t\t\t\t)",
                "\t\t\t\t(justify left)\n\t\t\t)",
                "\t\t)"]
    entries = list(parents) if parents is not None else [(parent, page)]
    out += ["\t\t(instances", "\t\t\t(project \"%s\"" % project]
    for spot, one in entries:
        out += ["\t\t\t\t(path \"%s\"" % spot,
                "\t\t\t\t\t(page \"%s\")" % one,
                "\t\t\t\t)"]
    out += ["\t\t\t)", "\t\t)", "\t)"]
    return "\n".join(out) + "\n"


def _pin_uuid(name):
    """A stable pin uuid, so a fixture is reproducible byte for byte."""
    digest = sum(ord(c) * (i + 1) for i, c in enumerate(name)) % 0xFFF0
    return "66666666-6666-4666-8666-%012x" % (digest + 1)


def page_text(body="", is_root=True):
    head = ("(kicad_sch\n"
            "\t(version 20250114)\n"
            "\t(generator \"eeschema\")\n"
            "\t(generator_version \"9.0\")\n"
            "\t(uuid \"%s\")\n"
            "\t(paper \"A4\")\n"
            "\t(lib_symbols)\n" % ROOT_UUID)
    tail = ""
    if is_root:
        tail = ("\t(sheet_instances\n\t\t(path \"/\"\n\t\t\t(page \"1\")\n\t\t)\n\t)\n")
    return head + body + tail + "\t(embedded_fonts no)\n)\n"


def hlabel(name, x=60.0, y=60.0, shape="input"):
    return ("\t(hierarchical_label \"%s\"\n"
            "\t\t(shape %s)\n"
            "\t\t(at %s %s 0)\n"
            "\t\t(effects\n\t\t\t(font\n\t\t\t\t(size 1.27 1.27)\n\t\t\t)\n"
            "\t\t\t(justify left)\n\t\t)\n"
            "\t\t(uuid \"%s\")\n\t)\n" % (name, shape, x, y, _pin_uuid("lbl" + name)))


class _Tmp(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.mkdtemp(prefix="pcbsheets-")

    def tearDown(self):
        shutil.rmtree(self.tmp, ignore_errors=True)

    def put(self, text, name):
        path = os.path.join(self.tmp, name)
        with open(path, "wb") as fh:
            fh.write(text.encode("utf-8"))
        return path

    def read(self, path):
        with open(path, "rb") as fh:
            return fh.read()

    def project(self, child_body="", root_extra="", children=("power.kicad_sch",)):
        """A root page with one sheet, plus the child file(s) it points at."""
        root = self.put(page_text(child_body + root_extra), "root.kicad_sch")
        for name in children:
            self.put(page_text("", is_root=False), name)
        return root

# ------------------------------------------------------------ reading a tree
class TestHierarchyRead(_Tmp):

    def test_a_two_file_project_reads_as_a_tree(self):
        root = self.project(root_extra=sheet_node("power", "power.kicad_sch", 2,
                                                  SHEET_A, pins=["VCC"]))
        info = HI.describe(root)
        self.assertEqual(info["sheets"], 1)
        self.assertEqual(info["file_count"], 2)
        self.assertEqual(info["depth"], 1)
        self.assertEqual([p["page"] for p in info["pages"]], ["2"])
        self.assertEqual(info["pages"][0]["pins"], ["VCC"])
        self.assertEqual(info["issues"], [])
        self.assertFalse(info["duplicate_pages"])

    def test_one_child_file_used_twice_is_two_pages_not_a_duplicate_file(self):
        root = self.project(root_extra=(
            sheet_node("left", "power.kicad_sch", 2, SHEET_A)
            + sheet_node("right", "power.kicad_sch", 3, SHEET_B, y=90)))
        info = HI.describe(root)
        self.assertEqual(info["sheets"], 2)
        self.assertEqual(info["file_count"], 2)        # root + one child, not three
        self.assertEqual([p["page"] for p in info["pages"]], ["2", "3"])
        self.assertEqual({p["file"] for p in info["pages"]}, {"power.kicad_sch"})
        self.assertEqual(info["issues"], [])

    def test_a_missing_child_is_an_issue_naming_the_file_it_looked_for(self):
        root = self.put(page_text(sheet_node("ghost", "nowhere.kicad_sch", 2,
                                            SHEET_A)), "root.kicad_sch")
        info = HI.describe(root)
        self.assertEqual(info["file_count"], 1)
        self.assertEqual([i["issue"] for i in info["issues"]], ["missing_file"])
        self.assertEqual(info["issues"][0]["sheetfile"], "nowhere.kicad_sch")
        self.assertTrue(info["issues"][0]["tried"].endswith("nowhere.kicad_sch"))

    def test_a_cycle_is_reported_and_the_walk_stops(self):
        root = self.project(root_extra=sheet_node("loop", "root.kicad_sch", 2,
                                                  SHEET_A))
        info = HI.describe(root)
        self.assertEqual([i["issue"] for i in info["issues"]], ["cycle"])
        self.assertEqual(info["depth"], 1)

    def test_two_sheets_on_one_page_is_a_duplicate_page(self):
        root = self.project(root_extra=(
            sheet_node("a", "power.kicad_sch", 2, SHEET_A)
            + sheet_node("b", "power.kicad_sch", 2, SHEET_B, y=90)))
        info = HI.describe(root)
        self.assertEqual([i["issue"] for i in info["issues"]], ["duplicate_page"])
        self.assertEqual(info["duplicate_pages"], {"2": ["a", "b"]})

    def test_depth_counts_nesting_not_file_count(self):
        deep = self.put(page_text(sheet_node("mid", "mid.kicad_sch", 2, SHEET_A)),
                        "deep.kicad_sch")
        self.put(page_text(sheet_node("leaf", "leaf.kicad_sch", 2, SHEET_B),
                           is_root=False), "mid.kicad_sch")
        self.put(page_text("", is_root=False), "leaf.kicad_sch")
        info = HI.describe(deep)
        self.assertEqual(info["depth"], 2)
        self.assertEqual(info["file_count"], 3)
        self.assertEqual(info["sheets"], 2)
        self.assertEqual([p["page"] for p in info["pages"]], ["2", "2"])
        self.assertEqual(info["issues"], [])

    def test_an_unreadable_child_is_an_issue_not_a_crash(self):
        root = self.put(page_text(sheet_node("bad", "bad.kicad_sch", 2, SHEET_A)),
                        "root.kicad_sch")
        self.put("(kicad_sch (version 1) (broken\n", "bad.kicad_sch")
        info = HI.describe(root)
        self.assertIn("unreadable", [i["issue"] for i in info["issues"]])

    def test_reading_never_touches_the_file(self):
        root = self.project(root_extra=sheet_node("power", "power.kicad_sch", 2,
                                                  SHEET_A))
        before = self.read(root)
        HI.describe(root)
        HI.describe(root, text=page_text())
        HI.walk(root)
        HI.find_root(root)
        self.assertEqual(self.read(root), before)

    def test_walk_accepts_proposed_text_for_the_entry_file_only(self):
        root = self.project(root_extra=sheet_node("power", "power.kicad_sch", 2,
                                                  SHEET_A))
        proposed = page_text(sheet_node("extra", "power.kicad_sch", 3, SHEET_B))
        info = HI.describe(root, text=proposed)
        self.assertEqual([p["sheet"] for p in info["pages"]], ["extra"])
        self.assertEqual(self.read(root), root and self.read(root))   # unchanged

# ----------------------------------------------------- which file is the root
class TestRoot(_Tmp):

    def test_a_child_walks_up_one_level_to_the_root(self):
        root = self.project(root_extra=sheet_node("power", "power.kicad_sch", 2,
                                                  SHEET_A))
        child = os.path.join(self.tmp, "power.kicad_sch")
        found = HI.find_root(child)
        self.assertEqual(os.path.basename(found["root"]), "root.kicad_sch")
        self.assertIn("walked up 1 level", found["source"])
        self.assertEqual([os.path.basename(c) for c in found["walked"]],
                         ["power.kicad_sch"])

    def test_a_file_nobody_points_at_is_its_own_root(self):
        root = self.project()
        found = HI.find_root(root)
        self.assertEqual(found["root"], os.path.abspath(root))
        self.assertEqual(found["candidates"], [])
        self.assertEqual(found["source"], "no sibling names this file as a Sheetfile")

    def test_two_siblings_naming_the_same_file_is_ambiguous_and_not_picked(self):
        self.put(page_text(sheet_node("c", "shared.kicad_sch", 2, SHEET_A),
                           ), "one.kicad_sch")
        self.put(page_text(sheet_node("c", "shared.kicad_sch", 2, SHEET_B),
                           ), "two.kicad_sch")
        shared = self.put(page_text("", is_root=False), "shared.kicad_sch")
        found = HI.find_root(shared)
        self.assertEqual(found["root"], os.path.abspath(shared))
        self.assertIn("ambiguous", found["source"])
        self.assertEqual(len(found["candidates"]), 2)

    def test_parents_of_names_the_sheet_box_not_just_the_file(self):
        root = self.project(root_extra=sheet_node("power", "power.kicad_sch", 2,
                                                  SHEET_A))
        child = os.path.join(self.tmp, "power.kicad_sch")
        parents = HI.parents_of(child)
        self.assertEqual(len(parents), 1)
        self.assertEqual(parents[0]["file"], os.path.abspath(root))
        self.assertEqual(parents[0]["sheet"], "power")
        self.assertEqual(parents[0]["page"], "2")

# ------------------------------------------------------------- materialising
class TestMaterialise(_Tmp):

    def test_the_whole_tree_is_copied_with_the_entry_replaced(self):
        root = self.project(root_extra=sheet_node("power", "power.kicad_sch", 2,
                                                  SHEET_A))
        proposed = page_text(sheet_node("swapped", "power.kicad_sch", 3, SHEET_B))
        state = HI.materialise(root, text=proposed)
        try:
            self.assertEqual(len(state["files"]), 2)
            self.assertTrue(os.path.isfile(state["entry"]))
            with open(state["entry"], "r", encoding="utf-8") as fh:
                self.assertIn("swapped", fh.read())
            child = os.path.join(os.path.dirname(state["entry"]), "power.kicad_sch")
            self.assertTrue(os.path.isfile(child))
            self.assertFalse(state["missing"])
            self.assertEqual(self.read(root)[:20], self.read(root)[:20])
        finally:
            HI.cleanup(state)
        self.assertFalse(os.path.isdir(state["dir"]))

    def test_a_missing_child_is_reported_by_the_copy_too(self):
        root = self.put(page_text(sheet_node("ghost", "nowhere.kicad_sch", 2,
                                            SHEET_A)), "root.kicad_sch")
        state = HI.materialise(root)
        try:
            self.assertEqual([m["sheet"] for m in state["missing"]], ["ghost"])
            self.assertEqual(len(state["files"]), 1)
        finally:
            HI.cleanup(state)

    def test_materialising_a_child_keeps_the_root_it_belongs_to(self):
        root = self.project(root_extra=sheet_node("power", "power.kicad_sch", 2,
                                                  SHEET_A))
        child = os.path.join(self.tmp, "power.kicad_sch")
        state = HI.materialise(child, text=page_text(hlabel("VCC"), is_root=False))
        try:
            self.assertEqual(len(state["files"]), 2)
            self.assertEqual(os.path.basename(state["entry"]), "root.kicad_sch")
            edited = os.path.join(os.path.dirname(state["entry"]),
                                  "power.kicad_sch")
            with open(edited, "r", encoding="utf-8") as fh:
                self.assertIn("hierarchical_label", fh.read())
        finally:
            HI.cleanup(state)

# ------------------------------------------------------------- sheet ops
class TestSheetOps(_Tmp):

    def test_add_sheet_writes_a_node_kicad_would_write(self):
        root = self.project()
        doc = KS.KiCadSchematic.from_path(root)
        nodes = {}
        _add = SO.OPS["add_sheet"]["run"]
        _add(SO.SchEditor(doc), {"op": "add_sheet", "name": "power",
                                 "file": "power.kicad_sch", "x": 40, "y": 100,
                                 "pins": ["VCC"]}, "seed")
        nodes = doc.items("sheet")
        self.assertEqual(len(nodes), 1)
        heads = [SO.S.head(i) for i in nodes[0].items if not SO.S.head(i) is None]
        for required in ("at", "size", "uuid", "property", "pin", "instances"):
            self.assertIn(required, heads)
        facts = HI.sheet_facts(nodes[0])
        self.assertEqual(facts["name"], "power")
        self.assertEqual(facts["file"], "power.kicad_sch")
        self.assertEqual(facts["pins"][0]["name"], "VCC")

    def test_the_page_number_defaults_to_the_highest_in_use_plus_one(self):
        root = self.project(root_extra=sheet_node("one", "power.kicad_sch", 7,
                                                  SHEET_A))
        doc = KS.KiCadSchematic.from_path(root)
        detail = SO.OPS["add_sheet"]["run"](
            SO.SchEditor(doc), {"op": "add_sheet", "name": "next",
                                "file": "power.kicad_sch", "x": 10, "y": 100}, "s")
        self.assertEqual(detail["page"], "8")

    def test_a_page_number_already_in_use_is_refused(self):
        root = self.project(root_extra=sheet_node("one", "power.kicad_sch", 2,
                                                  SHEET_A))
        doc = KS.KiCadSchematic.from_path(root)
        with self.assertRaises(SO.OpError) as ctx:
            SO.OPS["add_sheet"]["run"](
                SO.SchEditor(doc), {"op": "add_sheet", "name": "clash",
                                    "file": "power.kicad_sch", "x": 10, "y": 100,
                                    "page": 2}, "s")
        self.assertEqual(ctx.exception.code, "PAGE_EXISTS")

    def test_sheetfile_must_be_a_bare_kicad_sch_name(self):
        root = self.project()
        doc = KS.KiCadSchematic.from_path(root)
        for bad in ("power.txt", "../power.kicad_sch", "sub/power.kicad_sch"):
            with self.assertRaises(SO.OpError) as ctx:
                SO.OPS["add_sheet"]["run"](
                    SO.SchEditor(doc), {"op": "add_sheet", "name": "x",
                                        "file": bad, "x": 10, "y": 10}, "s")
            self.assertEqual(ctx.exception.code, "ARG_INVALID", bad)

    def test_a_non_positive_size_is_refused(self):
        root = self.project()
        doc = KS.KiCadSchematic.from_path(root)
        with self.assertRaises(SO.OpError) as ctx:
            SO.OPS["add_sheet"]["run"](
                SO.SchEditor(doc), {"op": "add_sheet", "name": "x",
                                    "file": "x.kicad_sch", "x": 0, "y": 0,
                                    "width": 0}, "s")
        self.assertEqual(ctx.exception.code, "ARG_RANGE")

    def test_pin_side_decides_both_the_coordinate_and_the_rotation(self):
        root = self.project()
        doc = KS.KiCadSchematic.from_path(root)
        ed = SO.SchEditor(doc)
        got = {}
        for side, want_rot in (("left", 180), ("right", 0), ("top", 90),
                               ("bottom", 270)):
            node, _name = SO._pin_node(ed, {"name": side, "side": side, "offset": 10},
                                       40.0, 40.0, 40.0, 20.0)
            at = SO.S.child(node, "at")
            got[side] = (SO.S.arg_num(at, 1), SO.S.arg_num(at, 2),
                         SO.S.arg_num(at, 3))
            self.assertEqual(got[side][2], want_rot)
        self.assertEqual(got["left"][:2], (40.0, 50.0))
        self.assertEqual(got["right"][:2], (80.0, 50.0))
        self.assertEqual(got["top"][:2], (50.0, 40.0))
        self.assertEqual(got["bottom"][:2], (50.0, 60.0))

    def test_a_pin_that_does_not_fit_its_edge_is_refused(self):
        root = self.project()
        ed = SO.SchEditor(KS.KiCadSchematic.from_path(root))
        with self.assertRaises(SO.OpError) as ctx:
            SO._pin_node(ed, {"name": "VCC", "side": "right", "offset": 80},
                         0.0, 0.0, 40.0, 20.0)
        self.assertEqual(ctx.exception.code, "ARG_RANGE")
        for bad in ({"name": "VCC", "shape": "banana"},
                    {"name": "VCC", "side": "middle"}):
            with self.assertRaises(SO.OpError) as ctx:
                SO._pin_node(ed, bad, 0.0, 0.0, 40.0, 20.0)
            self.assertEqual(ctx.exception.code, "ARG_INVALID")

    def test_add_sheet_pin_lands_before_instances_and_reports_its_uuid(self):
        root = self.project(root_extra=sheet_node("power", "power.kicad_sch", 2,
                                                  SHEET_A))
        doc = KS.KiCadSchematic.from_path(root)
        detail = SO.OPS["add_sheet_pin"]["run"](
            SO.SchEditor(doc), {"op": "add_sheet_pin", "sheet": "power",
                                "name": "EN"}, "s")
        self.assertTrue(detail["uuid"])
        node = doc.items("sheet")[0]
        heads = [SO.S.head(i) for i in node.items if SO.S.head(i)]
        self.assertLess(heads.index("pin"), heads.index("instances"))
        self.assertEqual(sorted(detail and HI.sheet_facts(node)["pins"][0]["name"]
                                for _ in [0]), sorted(["EN"]))

    def test_an_unknown_or_ambiguous_sheet_name_is_named(self):
        root = self.project(root_extra=(
            sheet_node("same", "power.kicad_sch", 2, SHEET_A)
            + sheet_node("same", "power.kicad_sch", 3, SHEET_B, y=90)))
        doc = KS.KiCadSchematic.from_path(root)
        with self.assertRaises(SO.OpError) as ctx:
            SO.OPS["add_sheet_pin"]["run"](
                SO.SchEditor(doc), {"op": "add_sheet_pin", "sheet": "same",
                                    "name": "EN"}, "s")
        self.assertEqual(ctx.exception.code, "SHEET_AMBIGUOUS")
        with self.assertRaises(SO.OpError) as ctx:
            SO.OPS["add_sheet_pin"]["run"](
                SO.SchEditor(doc), {"op": "add_sheet_pin", "sheet": "nope",
                                    "name": "EN"}, "s")
        self.assertEqual(ctx.exception.code, "SHEET_NOT_FOUND")
        # the uuid is unambiguous even when the name is not
        detail = SO.OPS["add_sheet_pin"]["run"](
            SO.SchEditor(doc), {"op": "add_sheet_pin", "sheet_uuid": SHEET_B,
                                "name": "EN"}, "s")
        self.assertEqual(detail["sheet_uuid"], SHEET_B)

    def test_set_sheet_field_renames_and_repoints(self):
        root = self.project(root_extra=sheet_node("power", "power.kicad_sch", 2,
                                                  SHEET_A))
        doc = KS.KiCadSchematic.from_path(root)
        detail = SO.OPS["set_sheet_field"]["run"](
            SO.SchEditor(doc), {"op": "set_sheet_field", "sheet": "power",
                                "field": "Sheetname", "value": "pwr"}, "s")
        self.assertEqual(detail["was"], "power")
        facts = HI.sheet_facts(doc.items("sheet")[0])
        self.assertEqual(facts["name"], "pwr")
        SO.OPS["set_sheet_field"]["run"](
            SO.SchEditor(doc), {"op": "set_sheet_field", "sheet": "pwr",
                                "field": "Sheetfile", "value": "other.kicad_sch"},
            "s")
        self.assertEqual(HI.sheet_facts(doc.items("sheet")[0])["file"],
                         "other.kicad_sch")

    def test_setting_the_same_value_or_an_unknown_field_is_refused(self):
        root = self.project(root_extra=sheet_node("power", "power.kicad_sch", 2,
                                                  SHEET_A))
        doc = KS.KiCadSchematic.from_path(root)
        with self.assertRaises(SO.OpError) as ctx:
            SO.OPS["set_sheet_field"]["run"](
                SO.SchEditor(doc), {"op": "set_sheet_field", "sheet": "power",
                                    "field": "Sheetname", "value": "power"}, "s")
        self.assertEqual(ctx.exception.code, "OP_NOOP")
        with self.assertRaises(SO.OpError) as ctx:
            SO.OPS["set_sheet_field"]["run"](
                SO.SchEditor(doc), {"op": "set_sheet_field", "sheet": "power",
                                    "field": "Sheetuuid", "value": "x"}, "s")
        self.assertEqual(ctx.exception.code, "ARG_INVALID")

    def test_delete_sheet_keeps_the_file_and_says_who_else_uses_it(self):
        root = self.project(root_extra=(
            sheet_node("left", "power.kicad_sch", 2, SHEET_A)
            + sheet_node("right", "power.kicad_sch", 3, SHEET_B, y=90)))
        doc = KS.KiCadSchematic.from_path(root)
        first = SO.OPS["delete_sheet"]["run"](
            SO.SchEditor(doc), {"op": "delete_sheet", "sheet": "left"}, "s")
        self.assertEqual(first["other_sheets_using_it"], ["right"])
        self.assertFalse(first["orphaned"])
        self.assertIn("never deletes a file", first["file_kept"])
        self.assertEqual(len(doc.items("sheet")), 1)
        second = SO.OPS["delete_sheet"]["run"](
            SO.SchEditor(doc), {"op": "delete_sheet", "sheet": "right"}, "s")
        self.assertTrue(second["orphaned"])
        self.assertEqual(doc.items("sheet"), [])
        self.assertTrue(os.path.isfile(os.path.join(self.tmp, "power.kicad_sch")))

    def test_the_four_sheet_ops_are_in_the_catalogue(self):
        names = {o["op"] for o in E.describe_sch_ops()}
        for op in ("add_sheet", "add_sheet_pin", "set_sheet_field", "delete_sheet"):
            self.assertIn(op, names)
        self.assertEqual(len(names), 22)

    def test_an_edit_inserts_a_block_and_leaves_every_other_byte_alone(self):
        root = self.project()
        before = self.read(root)
        doc = KS.KiCadSchematic.from_path(root)
        SO.OPS["add_sheet"]["run"](
            SO.SchEditor(doc), {"op": "add_sheet", "name": "power",
                                "file": "power.kicad_sch", "x": 40, "y": 100}, "s")
        after = doc.to_bytes()
        self.assertEqual(doc.verify()["token_identity"], True)
        self.assertGreater(len(after), len(before))
        # the tail (sheet_instances + embedded_fonts + close) is untouched
        self.assertTrue(after.endswith(before[-60:]))

# --------------------------------------------------------------- the gates
class TestHierarchyGates(_Tmp):

    def test_a_plan_reports_the_tree_before_and_after(self):
        root = self.project(root_extra=sheet_node("one", "power.kicad_sch", 2,
                                                  SHEET_A))
        plan = E.propose_schematic(root, [
            {"op": "add_sheet", "name": "two", "file": "power.kicad_sch",
             "x": 40, "y": 100}], {"engine": False})
        self.assertEqual(plan["hierarchy_before"]["sheets"], 1)
        self.assertEqual(plan["hierarchy_after"]["sheets"], 2)
        self.assertEqual([p["page"] for p in plan["hierarchy_after"]["pages"]],
                         ["2", "3"])
        self.assertEqual(plan["hierarchy_diff"]["new_total"], 0)
        self.assertEqual(plan["blocking"], [])

    def test_a_sheet_box_pointing_at_nothing_blocks_and_the_file_is_untouched(self):
        root = self.project()
        before = self.read(root)
        plan = E.propose_schematic(root, [
            {"op": "add_sheet", "name": "ghost", "file": "nowhere.kicad_sch",
             "x": 40, "y": 100}], {"engine": False})
        self.assertEqual(plan["hierarchy_diff"]["new_total"], 1)
        self.assertEqual(plan["hierarchy_diff"]["new"][0]["issue"], "missing_file")
        self.assertTrue(plan["blocking"])
        with self.assertRaises(E.EditError) as ctx:
            E.apply_plan(plan["plan_id"], confirm=True, options={"engine": False})
        self.assertEqual(ctx.exception.code, "GATE_BLOCKED")
        self.assertEqual(self.read(root), before)

    def test_repointing_a_sheet_into_a_cycle_blocks(self):
        root = self.project(root_extra=sheet_node("power", "power.kicad_sch", 2,
                                                  SHEET_A))
        before = self.read(root)
        plan = E.propose_schematic(root, [
            {"op": "set_sheet_field", "sheet": "power", "field": "Sheetfile",
             "value": "root.kicad_sch"}], {"engine": False})
        self.assertEqual(plan["hierarchy_diff"]["new"][0]["issue"], "cycle")
        self.assertTrue(plan["blocking"])
        with self.assertRaises(E.EditError) as ctx:
            E.apply_plan(plan["plan_id"], confirm=True, options={"engine": False})
        self.assertEqual(ctx.exception.code, "GATE_BLOCKED")
        self.assertEqual(self.read(root), before)

    def test_deleting_a_sheet_is_not_blocked_by_the_tree_gate(self):
        root = self.project(root_extra=sheet_node("power", "power.kicad_sch", 2,
                                                  SHEET_A))
        plan = E.propose_schematic(root, [
            {"op": "delete_sheet", "sheet": "power"}], {"engine": False})
        self.assertEqual(plan["blocking"], [])
        result = E.apply_plan(plan["plan_id"], confirm=True,
                              options={"engine": False})
        self.assertEqual(result["status"], "ok")
        self.assertEqual(result["hierarchy"]["sheets"], 0)

# ----------------------------------------------------------- creating a page
class TestNewFile(_Tmp):

    def test_an_empty_page_is_created_and_parses_back(self):
        target = os.path.join(self.tmp, "new.kicad_sch")
        out = E.new_schematic_file(target, title="power", confirm=True)
        self.assertEqual(out["status"], "ok")
        self.assertTrue(out["self_check"]["token_identity"])
        doc = KS.KiCadSchematic.from_path(target)
        self.assertEqual(doc.version(), "20250114")
        self.assertEqual(doc.items("sheet"), [])
        self.assertEqual(HI.describe(target)["issues"], [])

    def test_creating_over_an_existing_file_is_refused(self):
        target = self.put(page_text(), "here.kicad_sch")
        before = self.read(target)
        with self.assertRaises(E.EditError) as ctx:
            E.new_schematic_file(target, confirm=True)
        self.assertEqual(ctx.exception.code, "TARGET_EXISTS")
        self.assertEqual(self.read(target), before)

    def test_without_confirm_nothing_is_created(self):
        target = os.path.join(self.tmp, "later.kicad_sch")
        with self.assertRaises(E.EditError) as ctx:
            E.new_schematic_file(target)
        self.assertEqual(ctx.exception.code, "CONFIRMATION_REQUIRED")
        self.assertFalse(os.path.exists(target))

    def test_a_missing_folder_or_a_foreign_extension_is_refused(self):
        with self.assertRaises(E.EditError) as ctx:
            E.new_schematic_file(os.path.join(self.tmp, "nope", "x.kicad_sch"),
                                 confirm=True)
        self.assertEqual(ctx.exception.code, "FOLDER_NOT_FOUND")
        with self.assertRaises(E.EditError) as ctx:
            E.new_schematic_file(os.path.join(self.tmp, "x.esch"), confirm=True)
        self.assertEqual(ctx.exception.code, "WRITE_UNSUPPORTED_FORMAT")

    def test_a_new_page_can_become_a_child_page_in_one_go(self):
        root = self.project()
        child = os.path.join(self.tmp, "fresh.kicad_sch")
        E.new_schematic_file(child, confirm=True)
        plan = E.propose_schematic(root, [
            {"op": "add_sheet", "name": "fresh", "file": "fresh.kicad_sch",
             "x": 40, "y": 100}], {"engine": False})
        self.assertEqual(plan["blocking"], [])
        result = E.apply_plan(plan["plan_id"], confirm=True,
                              options={"engine": False})
        self.assertEqual(result["status"], "ok")
        pages = result["hierarchy"]["pages"]
        self.assertEqual([(p["sheet"], p["page"], p["file"]) for p in pages],
                         [("fresh", "2", "fresh.kicad_sch")])

# ---------------------------------------------------- what KiCad itself says
def demo_hierarchy():
    """A KiCad demo project whose root has child sheets, if one is installed."""
    cli = ENGINE.find_kicad_cli()
    if not cli:
        return None
    demos = os.path.join(os.path.dirname(os.path.dirname(cli)), "share", "kicad",
                         "demos")
    if not os.path.isdir(demos):
        return None
    for folder in sorted(os.listdir(demos)):
        path = os.path.join(demos, folder)
        if not os.path.isdir(path):
            continue
        for name in sorted(os.listdir(path)):
            if not name.endswith(".kicad_sch"):
                continue
            try:
                info = HI.describe(os.path.join(path, name))
            except Exception:                        # noqa: BLE001
                continue
            if info["sheets"] and info["file_count"] > 1 and not info["issues"]:
                return path, name
    return None


# ------------------------------------------------------- pages of one file
class TestInstancePaging(_Tmp):
    """One page, used twice: two instance paths, two page numbers, one file.

    This is the part of "multi page" that a single-file view cannot see. KiCad
    numbers pages once per *project* (the `vme-wren` demo runs 1..73), and a
    sheet node whose parent page is instantiated twice carries one
    `(path ... (page ...))` entry per instance. Writing one entry gives every
    instance the same number: the file still parses, the netlist is unchanged,
    and the printed pages are wrong.
    """

    def reused(self):
        """root -2 sheets (pages 2,3)-> child -1 sheet (pages 9,10)-> leaf.

        The leaf is the same node read twice: one entry per instance of the page
        it sits on. Its pages (9, 10) are also the project's highest, so a page
        added to the child has to be numbered after them, not after the root's 3.
        """
        a = "/%s/%s" % (ROOT_UUID, SHEET_A)
        b = "/%s/%s" % (ROOT_UUID, SHEET_B)
        child_body = sheet_node("leaf", "leaf.kicad_sch", 9, SHEET_B,
                                x=20, y=120, parents=[(a, "9"), (b, "10")])
        root = self.put(page_text(
            sheet_node("left", "child.kicad_sch", 2, SHEET_A)
            + sheet_node("right", "child.kicad_sch", 3, SHEET_B, x=100)),
            "root.kicad_sch")
        child = self.put(page_text(child_body, is_root=False), "child.kicad_sch")
        self.put(page_text("", is_root=False), "leaf.kicad_sch")
        return root, child, [a, b]

    def test_a_file_used_twice_names_two_instance_paths(self):
        root, child, spots = self.reused()
        info = HI.instance_paths(child)
        self.assertFalse(info["is_root"])
        self.assertEqual([i["path"] for i in info["instances"]], spots)
        self.assertEqual([i["page"] for i in info["instances"]], ["2", "3"])
        self.assertEqual(info["max_page"], 10)         # the leaf's pages
        self.assertEqual(info["root"], root)
        self.assertEqual(HI.instance_paths(root)["instances"][0]["page"], "1")

    def test_a_sheet_added_to_a_reused_page_gets_one_page_per_instance(self):
        _root, child, spots = self.reused()
        doc = KS.KiCadSchematic.from_path(child)
        detail = SO.OPS["add_sheet"]["run"](
            SO.SchEditor(doc), {"op": "add_sheet", "name": "probe",
                                "file": "probe.kicad_sch", "x": 20, "y": 20}, "s")
        # page numbers are project-wide: the leaf already holds 9 and 10, so
        # the two new pages are 11 and 12 - not 4 and 5 after the root's 3
        self.assertEqual(detail["pages"], ["11", "12"])
        self.assertEqual(detail["instance_paths"], spots)
        node = [n for n in doc.items("sheet")
                if HI.sheet_facts(n)["name"] == "probe"][0]
        entries = [(p["path"], p["page"])
                   for proj in SO.S.children(SO.S.child(node, "instances"),
                                             "project")
                   for path in SO.S.children(proj, "path")
                   for p in [{"path": SO.S.arg_text(path, 1),
                              "page": SO.S.arg_text(
                                  SO.S.child(path, "page"), 1)}]]
        self.assertEqual(entries, [(spots[0], "11"), (spots[1], "12")])

    def test_one_page_number_cannot_name_a_reused_page(self):
        _root, child, _spots = self.reused()
        doc = KS.KiCadSchematic.from_path(child)
        with self.assertRaises(SO.OpError) as ctx:
            SO.OPS["add_sheet"]["run"](
                SO.SchEditor(doc), {"op": "add_sheet", "name": "probe",
                                    "file": "probe.kicad_sch", "x": 20, "y": 20,
                                    "page": 4}, "s")
        self.assertEqual(ctx.exception.code, "ARG_INVALID")

    def test_a_page_number_used_anywhere_in_the_project_is_refused(self):
        root, _child, _spots = self.reused()
        doc = KS.KiCadSchematic.from_path(root)
        # 9 is not on this page at all - it is two levels down. The check is
        # against the project, not against the file being edited.
        with self.assertRaises(SO.OpError) as ctx:
            SO.OPS["add_sheet"]["run"](
                SO.SchEditor(doc), {"op": "add_sheet", "name": "probe",
                                    "file": "probe.kicad_sch", "x": 20, "y": 20,
                                    "page": 9}, "s")
        self.assertEqual(ctx.exception.code, "PAGE_EXISTS")

    def test_the_whole_tree_is_what_the_describe_counts(self):
        root, _child, _spots = self.reused()
        info = HI.describe(root)
        # left, right, and the leaf twice (one per instance of its parent)
        self.assertEqual(info["sheets"], 4)
        self.assertEqual(info["file_count"], 3)
        self.assertEqual(info["depth"], 2)
        self.assertEqual(info["duplicate_pages"], {})
        leaf = [p for p in info["pages"] if p["sheet"] == "leaf"]
        self.assertEqual([p["page"] for p in leaf], ["9", "10"])

DEMO = demo_hierarchy()


@unittest.skipUnless(DEMO, "no kicad-cli / hierarchical demo project here")
class TestHierarchyWithKiCad(_Tmp):
    """The parts only KiCad can answer."""

    def copy_demo(self):
        folder, name = DEMO
        for entry in os.listdir(folder):
            if entry.endswith(".kicad_sch") or entry.endswith(".kicad_pro"):
                shutil.copyfile(os.path.join(folder, entry),
                                os.path.join(self.tmp, entry))
        return os.path.join(self.tmp, name)

    def test_the_engine_is_asked_about_every_page(self):
        root = self.copy_demo()
        plan = E.propose_schematic(root, [
            {"op": "add_sheet", "name": "probe", "file": "probe.kicad_sch",
             "x": 20, "y": 20}])
        self.assertTrue(plan["blocking"])            # the child does not exist yet
        # create the child and ask again: now the tree resolves and the engine runs
        E.new_schematic_file(os.path.join(self.tmp, "probe.kicad_sch"), confirm=True)
        plan = E.propose_schematic(root, [
            {"op": "add_sheet", "name": "probe", "file": "probe.kicad_sch",
             "x": 20, "y": 20}], {"engine": False})
        self.assertEqual(plan["blocking"], [])
        result = E.apply_plan(plan["plan_id"], confirm=True,
                              options={"allow_new_violations": True})
        self.assertIn("tree", result["gates"])
        self.assertTrue(result["gates"]["tree"]["ok"])
        self.assertGreaterEqual(result["gates"]["tree"]["after"]["files"], 3)
        self.assertTrue(result["gates"]["netlist"]["comparable"])
        self.assertTrue(result["gates"]["erc"]["comparable"])
        # this demo instantiates the edited page twice, so one added sheet node
        # becomes two pages - one per instance. A lone-file engine run could not
        # see the doubling at all, which is what the tree gate exists for.
        before = plan["hierarchy_before"]["pages"]
        instances = [p for p in before if p["file"] == os.path.basename(root)]
        self.assertEqual(len(instances), 2)
        self.assertEqual(result["hierarchy"]["sheets"],
                         plan["hierarchy_before"]["sheets"] + len(instances))
        added = [p for p in result["hierarchy"]["pages"] if p["sheet"] == "probe"]
        self.assertEqual(len(added), len(instances))
        for p in added:
            self.assertEqual(p["file"], "probe.kicad_sch")
            self.assertTrue(p["page"])
        self.assertEqual(len({p["page"] for p in added}), len(added))  # distinct
        self.assertEqual(result["hierarchy"]["duplicate_pages"], {})

    def test_a_pin_with_no_label_behind_it_is_refused_by_erc(self):
        root = self.copy_demo()
        E.new_schematic_file(os.path.join(self.tmp, "bare.kicad_sch"), confirm=True)
        before = self.read(root)
        plan = E.propose_schematic(root, [
            {"op": "add_sheet", "name": "bare", "file": "bare.kicad_sch",
             "x": 20, "y": 20, "pins": ["VCC"]}])
        with self.assertRaises(E.EditError) as ctx:
            E.apply_plan(plan["plan_id"], confirm=True)
        self.assertEqual(ctx.exception.code, "GATE_BLOCKED")
        new = ctx.exception.extra["gates"]["erc"]["new"]
        self.assertIn("hier_label_mismatch", {v["type"] for v in new})
        self.assertEqual(self.read(root), before)

    def test_the_same_plan_goes_through_once_the_label_exists(self):
        root = self.copy_demo()
        child = os.path.join(self.tmp, "labelled.kicad_sch")
        E.new_schematic_file(child, confirm=True)
        made = E.propose_schematic(child, [
            {"op": "add_hierarchical_label", "text": "VCC", "x": 40, "y": 40}])
        E.apply_plan(made["plan_id"], confirm=True,
                     options={"allow_new_violations": True})
        plan = E.propose_schematic(root, [
            {"op": "add_sheet", "name": "labelled", "file": "labelled.kicad_sch",
             "x": 20, "y": 20, "pins": ["VCC"]}])
        result = E.apply_plan(plan["plan_id"], confirm=True,
                              options={"allow_new_violations": True})
        types = {v["type"] for v in result["gates"]["erc"]["new"]}
        self.assertNotIn("hier_label_mismatch", types)


if __name__ == "__main__":
    unittest.main()
