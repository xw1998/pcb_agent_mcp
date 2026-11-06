"""Tests for the schematic write path.

Three things are being defended here, in this order:

1. an unmodified schematic is re-emitted **byte for byte** (the same promise the
   board path keeps, tested here on a schematic-shaped file);
2. a plan's insertions are insertions - nothing else in the file moves, which is
   what makes a diff of the result reviewable;
3. the gates have teeth: a plan that cannot be honoured is refused and the file
   on disk is left exactly as it was.

Engine tests (ERC + netlist) are opt-in and skip cleanly when kicad-cli or its
demo schematics are absent, because the rest of the suite has to run anywhere.
"""
import os
import shutil
import tempfile
import unittest

from pcb_agent_mcp import edit as E
from pcb_agent_mcp.edit import sch_ops as SO
from pcb_agent_mcp.edit import sch_plan as SP
from pcb_agent_mcp.fidelity import engine as ENGINE
from pcb_agent_mcp.fidelity import kicad_sch as KS
from pcb_agent_mcp.fidelity import sexpr as S

# --------------------------------------------------------------- fixtures
UUID = "11111111-1111-4111-8111-111111111111"

HEAD = """(kicad_sch
	(version 20250114)
	(generator "eeschema")
	(generator_version "9.0")
	(uuid "%s")
	(paper "A4")
	(lib_symbols)
""" % UUID

TAIL = """	(sheet_instances
		(path "/"
			(page "1")
		)
	)
	(embedded_fonts no)
)
"""

WIRE_1 = """	(wire
		(pts
			(xy 50.8 50.8)
			(xy 63.5 50.8)
		)
		(stroke
			(width 0)
			(type default)
		)
		(uuid "22222222-2222-4222-8222-222222222222")
	)
"""

SYMBOL_R1 = """	(symbol
		(lib_id "Test:R")
		(at 76.2 76.2 0)
		(unit 1)
		(exclude_from_sim no)
		(in_bom yes)
		(on_board yes)
		(dnp no)
		(uuid "33333333-3333-4333-8333-333333333333")
		(property "Reference" "R1"
			(at 78.232 74.93 0)
			(effects
				(font
					(size 1.27 1.27)
				)
			)
		)
		(property "Value" "10k"
			(at 78.232 77.47 0)
			(effects
				(font
					(size 1.27 1.27)
				)
			)
		)
		(property "Footprint" "Resistor_SMD:R_0603_1608Metric"
			(at 78.232 80.01 0)
			(effects
				(font
					(size 1.27 1.27)
				)
				(hide yes)
			)
		)
		(pin "1"
			(uuid "44444444-4444-4444-8444-444444444444")
		)
		(pin "2"
			(uuid "55555555-5555-4555-8555-555555555555")
		)
		(instances
			(project "unit"
				(path "/%s"
					(reference "R1")
					(unit 1)
				)
			)
		)
	)
""" % UUID

LIB_R = """	(lib_symbols
		(symbol "Test:R"
			(pin_numbers
				(hide yes)
			)
			(pin_names
				(offset 0)
			)
			(exclude_from_sim no)
			(in_bom yes)
			(on_board yes)
			(property "Reference" "R"
				(at 2.032 0 90)
				(effects
					(font
						(size 1.27 1.27)
					)
				)
			)
			(property "Value" "R"
				(at 0 0 90)
				(effects
					(font
						(size 1.27 1.27)
					)
				)
			)
			(property "Footprint" ""
				(at -1.778 0 90)
				(effects
					(font
						(size 1.27 1.27)
					)
					(hide yes)
				)
			)
			(symbol "R_0_1"
				(rectangle
					(start -1.016 -2.54)
					(end 1.016 2.54)
					(stroke
						(width 0.254)
						(type default)
					)
					(fill
						(type none)
					)
				)
			)
			(symbol "R_1_1"
				(pin passive line
					(at 0 3.81 270)
					(length 1.27)
					(name "~"
						(effects
							(font
								(size 1.27 1.27)
							)
						)
					)
					(number "1"
						(effects
							(font
								(size 1.27 1.27)
							)
						)
					)
				)
				(pin passive line
					(at 0 -3.81 90)
					(length 1.27)
					(name "~"
						(effects
							(font
								(size 1.27 1.27)
							)
						)
					)
					(number "2"
						(effects
							(font
								(size 1.27 1.27)
							)
						)
					)
				)
			)
		)
	)
"""

# The definition strings below are the S-expression a caller may pass instead of
# a library file; `add_symbol` renames the entry to the requested lib_id.
DEF_R = """(symbol "R"
	(pin_numbers
		(hide yes)
	)
	(pin_names
		(offset 0)
	)
	(exclude_from_sim no)
	(in_bom yes)
	(on_board yes)
	(property "Reference" "R"
		(at 2.032 0 90)
		(effects
			(font
				(size 1.27 1.27)
			)
		)
	)
	(property "Value" "R"
		(at 0 0 90)
		(effects
			(font
				(size 1.27 1.27)
			)
		)
	)
	(property "Footprint" ""
		(at -1.778 0 90)
		(effects
			(font
				(size 1.27 1.27)
			)
			(hide yes)
		)
	)
	(symbol "R_0_1"
		(rectangle
			(start -1.016 -2.54)
			(end 1.016 2.54)
			(stroke
				(width 0.254)
				(type default)
			)
			(fill
				(type none)
			)
		)
	)
	(symbol "R_1_1"
		(pin passive line
			(at 0 3.81 270)
			(length 1.27)
			(name "~"
				(effects
					(font
						(size 1.27 1.27)
					)
				)
			)
			(number "1"
				(effects
					(font
						(size 1.27 1.27)
					)
				)
			)
		)
		(pin passive line
			(at 0 -3.81 90)
			(length 1.27)
			(name "~"
				(effects
					(font
						(size 1.27 1.27)
					)
				)
			)
			(number "2"
				(effects
					(font
						(size 1.27 1.27)
					)
				)
			)
		)
	)
)"""

def schematic_text(with_symbol=False):
    """HEAD already carries an EMPTY `(lib_symbols)`; the symbol case replaces it."""
    head = HEAD if not with_symbol else HEAD.replace("\t(lib_symbols)\n", "")
    body = head + WIRE_1
    if with_symbol:
        body = head + LIB_R + SYMBOL_R1 + WIRE_1
    return body + TAIL

class _Tmp(unittest.TestCase):
    """A temp directory per test, plus a helper to put a schematic in it."""

    def setUp(self):
        self.tmp = tempfile.mkdtemp(prefix="pcbsch-")

    def tearDown(self):
        shutil.rmtree(self.tmp, ignore_errors=True)

    def write(self, text, name="unit.kicad_sch"):
        path = os.path.join(self.tmp, name)
        with open(path, "wb") as fh:
            fh.write(text.encode("utf-8"))
        return path

    def read(self, path):
        with open(path, "rb") as fh:
            return fh.read()

# --------------------------------------------------------------- fidelity
class TestSchematicDocument(_Tmp):

    def test_unmodified_schematic_is_reeemitted_byte_for_byte(self):
        text = schematic_text(with_symbol=True)
        path = self.write(text)
        doc = KS.KiCadSchematic.from_path(path)
        self.assertEqual(doc.to_bytes(), self.read(path))
        proof = doc.verify()
        self.assertTrue(proof["token_identity"])
        self.assertTrue(proof["bytes_identical"])

    def test_project_and_root_path_come_from_the_file(self):
        path = self.write(schematic_text(with_symbol=True))
        doc = KS.KiCadSchematic.from_path(path)
        proj = doc.project()
        self.assertEqual(proj["project"], "unit")
        self.assertEqual(proj["root_path"], "/" + UUID)
        self.assertIn("instances", proj["source"])

    def test_project_falls_back_when_no_symbol_is_placed(self):
        path = self.write(schematic_text(with_symbol=False))
        doc = KS.KiCadSchematic.from_path(path)
        proj = doc.project()
        self.assertEqual(proj["project"], "unit")          # from the file name
        self.assertEqual(proj["root_path"], "/" + UUID)    # from root uuid
        self.assertIn("sheet_instances", proj["source"])

    def test_a_board_is_not_a_schematic(self):
        path = self.write("(kicad_pcb\n\t(version 20240108)\n)\n", "b.kicad_pcb")
        with self.assertRaises(ValueError):
            KS.KiCadSchematic.from_path(path)

    def test_symbol_entry_reads_fields_pins_and_placement(self):
        path = self.write(schematic_text(with_symbol=True))
        doc = KS.KiCadSchematic.from_path(path)
        (sym,) = doc.symbols()
        self.assertEqual(sym["reference"], "R1")
        self.assertEqual(sym["value"], "10k")
        self.assertEqual(sym["lib_id"], "Test:R")
        self.assertEqual((sym["x"], sym["y"]), (76.2, 76.2))
        self.assertEqual(sym["pins"], ["1", "2"])
        self.assertEqual(sym["footprint"], "Resistor_SMD:R_0603_1608Metric")

# --------------------------------------------------------------- operations
class TestSchOps(_Tmp):

    def run_ops(self, text, ops):
        doc = KS.KiCadSchematic.from_text(text, "<memory>")
        records = SO.run_all(doc, ops, seed_base="t")
        return doc, records

    def run_ops_from_file(self, text, ops):
        """Same, but through a real path - a plan needs the project name, and that
        is read from the file's instances / file name."""
        path = self.write(text)
        doc = KS.KiCadSchematic.from_path(path)
        records = SO.run_all(doc, ops, seed_base="t")
        return doc, records

    def test_add_wire_writes_one_node_per_segment(self):
        doc, recs = self.run_ops(schematic_text(), [
            {"op": "add_wire", "points": [[10, 10], [20, 10], [20, 20]]}])
        self.assertEqual(recs[0]["detail"]["segment_count"], 2)
        wires = doc.items("wire")
        self.assertEqual(len(wires), 3)                     # 1 fixture + 2 new
        pts = [[(S.arg_num(xy, 1), S.arg_num(xy, 2))
                for xy in S.children(S.child(w, "pts"), "xy")] for w in wires[1:]]
        self.assertEqual(pts, [[(10.0, 10.0), (20.0, 10.0)],
                               [(20.0, 10.0), (20.0, 20.0)]])

    def test_wire_frame_is_the_file_frame_not_y_up(self):
        doc, _ = self.run_ops(schematic_text(), [
            {"op": "add_wire", "points": [[10, -5], [10, -15]]}])
        wire = doc.items("wire")[-1]
        ys = [S.arg_num(xy, 2) for xy in S.children(S.child(wire, "pts"), "xy")]
        self.assertEqual(ys, [-5.0, -15.0])       # not negated

    def test_a_zero_length_wire_is_refused(self):
        with self.assertRaises(SO.OpError) as ctx:
            self.run_ops(schematic_text(), [
                {"op": "add_wire", "points": [[10, 10], [10, 10]]}])
        self.assertEqual(ctx.exception.code, "OP_INVALID")

    def test_add_label_family(self):
        doc, recs = self.run_ops(schematic_text(), [
            {"op": "add_label", "text": "N1", "x": 20, "y": 20},
            {"op": "add_global_label", "text": "VCC", "x": 30, "y": 20,
             "rotation": 180, "shape": "output"},
            {"op": "add_hierarchical_label", "text": "PORT", "x": 40, "y": 20,
             "shape": "bidirectional"}])
        self.assertEqual(len(doc.items("label")), 1)
        gl = doc.items("global_label")[0]
        self.assertEqual(S.arg_text(gl, 1), "VCC")
        self.assertEqual(S.arg_text(S.child(gl, "shape"), 1), "output")
        props = [S.arg_text(p, 1) for p in S.children(gl, "property")]
        self.assertIn("Intersheetrefs", props)
        self.assertIsNotNone(S.child(gl, "fields_autoplaced"))
        hl = doc.items("hierarchical_label")[0]
        self.assertEqual(S.arg_text(S.child(hl, "shape"), 1), "bidirectional")

    def test_bad_shape_is_refused(self):
        with self.assertRaises(SO.OpError):
            self.run_ops(schematic_text(), [
                {"op": "add_global_label", "text": "X", "x": 1, "y": 1,
                 "shape": "sideways"}])

    def test_junction_and_no_connect(self):
        doc, _ = self.run_ops(schematic_text(), [
            {"op": "add_junction", "x": 12.7, "y": 12.7},
            {"op": "add_no_connect", "x": 13.97, "y": 12.7}])
        self.assertEqual(len(doc.items("junction")), 1)
        self.assertEqual(len(doc.items("no_connect")), 1)
        self.assertEqual(S.arg_num(S.child(doc.items("junction")[0], "at"), 1), 12.7)

    def test_text_polyline_rectangle_and_bus(self):
        doc, _ = self.run_ops(schematic_text(), [
            {"op": "add_text", "text": "hello", "x": 5, "y": 5},
            {"op": "add_polyline", "points": [[1, 1], [2, 2], [3, 1]]},
            {"op": "add_rectangle", "start_x": 1, "start_y": 1,
             "end_x": 5, "end_y": 5},
            {"op": "add_bus", "points": [[1, 9], [1, 19]]},
            {"op": "add_bus_entry", "x": 1, "y": 19}])
        self.assertEqual(S.arg_text(doc.items("text")[0], 1), "hello")
        self.assertEqual(len(doc.items("polyline")), 1)
        self.assertEqual(len(doc.items("rectangle")), 1)
        self.assertEqual(len(doc.items("bus")), 1)
        self.assertEqual(len(doc.items("bus_entry")), 1)

    def test_add_symbol_embeds_the_definition_and_annotates(self):
        doc, recs = self.run_ops_from_file(schematic_text(), [
            {"op": "add_symbol", "lib_id": "Test:NEWR", "x": 100, "y": 100,
             "value": "4k7", "definition": DEF_R}])
        ref = recs[0]["detail"]["reference"]
        self.assertEqual(ref, "R1")
        self.assertIn("Test:NEWR", doc.lib_symbol_names())
        node, entry = SO.SchEditor(doc).find_symbol("R1")
        self.assertIsNotNone(node)
        self.assertEqual(entry["pins"], ["1", "2"])
        inst = S.child(node, "instances")
        proj = S.child(inst, "project")
        self.assertEqual(S.arg_text(proj, 1), "unit")
        path = S.child(proj, "path")
        self.assertEqual(S.arg_text(path, 1), "/" + UUID)
        self.assertEqual(S.arg_text(S.child(path, "reference"), 1), "R1")

    def test_add_symbol_reuses_an_embedded_definition_without_library(self):
        doc, recs = self.run_ops(schematic_text(with_symbol=True), [
            {"op": "add_symbol", "lib_id": "Test:R", "x": 100, "y": 100}])
        self.assertFalse(recs[0]["detail"]["definition"]["added_to_lib_symbols"])
        self.assertEqual(doc.lib_symbol_names(), ["Test:R"])   # not duplicated
        self.assertEqual(recs[0]["detail"]["reference"], "R2")

    def test_add_symbol_refuses_to_invent_a_body(self):
        with self.assertRaises(SO.OpError) as ctx:
            self.run_ops(schematic_text(), [
                {"op": "add_symbol", "lib_id": "Device:R", "x": 1, "y": 1}])
        self.assertEqual(ctx.exception.code, "MISSING_SYMBOL_DEFINITION")

    def test_add_symbol_refuses_a_duplicate_reference(self):
        with self.assertRaises(SO.OpError) as ctx:
            self.run_ops(schematic_text(with_symbol=True), [
                {"op": "add_symbol", "lib_id": "Test:R", "x": 100, "y": 100,
                 "reference": "R1"}])
        self.assertEqual(ctx.exception.code, "REFERENCE_EXISTS")

    def test_set_symbol_field_updates_and_creates(self):
        doc, _ = self.run_ops(schematic_text(with_symbol=True), [
            {"op": "set_symbol_field", "reference": "R1", "field": "Value",
             "value": "22k"},
            {"op": "set_symbol_field", "reference": "R1", "field": "MPN",
             "value": "RC0603"}])
        _node, entry = SO.SchEditor(doc).find_symbol("R1")
        self.assertEqual(entry["value"], "22k")
        names = [S.arg_text(p, 1) for p in S.children(_node, "property")]
        self.assertIn("MPN", names)

    def test_set_symbol_field_hides_the_footprint_field(self):
        # KiCad draws Footprint/Datasheet/Description hidden: they are data, not
        # silkscreen.  A visible Footprint field dumped its UUID across the page.
        doc, _ = self.run_ops(schematic_text(with_symbol=True), [
            {"op": "set_symbol_field", "reference": "R1", "field": "Footprint",
             "value": "Lib:0402"}])
        node, _entry = SO.SchEditor(doc).find_symbol("R1")
        field = [p for p in S.children(node, "property")
                 if S.arg_text(p, 1) == "Footprint"][0]
        self.assertIsNotNone(S.child(S.child(field, "effects"), "hide"))

    def test_set_symbol_field_requires_an_explicit_value(self):
        with self.assertRaises(SO.OpError) as ctx:
            self.run_ops(schematic_text(with_symbol=True), [
                {"op": "set_symbol_field", "reference": "R1", "field": "Value"}])
        self.assertEqual(ctx.exception.code, "ARG_MISSING")

    def test_move_symbol_moves_its_fields_too(self):
        doc, _ = self.run_ops(schematic_text(with_symbol=True), [
            {"op": "move_symbol", "reference": "R1", "x": 80, "y": 80,
             "rotation": 90}])
        node, entry = SO.SchEditor(doc).find_symbol("R1")
        self.assertEqual((entry["x"], entry["y"]), (80.0, 80.0))
        self.assertEqual(entry["rotation"], 90.0)
        ref_prop = [p for p in S.children(node, "property")
                    if S.arg_text(p, 1) == "Reference"][0]
        at = S.child(ref_prop, "at")
        self.assertAlmostEqual(S.arg_num(at, 1), 78.232 + (80 - 76.2), places=4)
        self.assertAlmostEqual(S.arg_num(at, 2), 74.93 + (80 - 76.2), places=4)

    def test_delete_symbol(self):
        doc, _ = self.run_ops(schematic_text(with_symbol=True), [
            {"op": "delete_symbol", "reference": "R1"}])
        self.assertEqual(doc.symbols(), [])
        self.assertEqual(doc.lib_symbol_names(), ["Test:R"])   # body is kept

    def test_delete_object_by_uuid(self):
        text = schematic_text()
        root = S.parse(text).items[0]
        wire = S.child(root, "wire")
        uid = S.arg_text(S.child(wire, "uuid"), 1)
        doc, _ = self.run_ops(text, [{"op": "delete_object", "uuid": uid}])
        self.assertEqual(doc.items("wire"), [])

    def test_delete_object_refuses_a_symbol(self):
        with self.assertRaises(SO.OpError) as ctx:
            self.run_ops(schematic_text(with_symbol=True), [
                {"op": "delete_object",
                 "uuid": "33333333-3333-4333-8333-333333333333"}])
        self.assertEqual(ctx.exception.code, "OP_REFUSED")

    def test_unknown_op_is_named(self):
        with self.assertRaises(SO.OpError) as ctx:
            self.run_ops(schematic_text(), [{"op": "add_sparkle"}])
        self.assertEqual(ctx.exception.code, "OP_UNKNOWN")

    def test_verify_all_reads_each_postcondition_back(self):
        text = schematic_text(with_symbol=True)
        ops = [{"op": "add_wire", "points": [[10, 10], [20, 10]]},
               {"op": "set_symbol_field", "reference": "R1", "field": "Value",
                "value": "1k"},
               {"op": "move_symbol", "reference": "R1", "x": 90, "y": 90}]
        doc = KS.KiCadSchematic.from_text(text, "<memory>")
        SO.run_all(doc, ops, seed_base="t")
        written = doc.to_bytes()
        reloaded = KS.KiCadSchematic.from_text(written.decode("utf-8"), "<memory>")
        report = SO.verify_all(reloaded, ops)
        self.assertTrue(report["all_verified"], report)

    def test_verify_all_catches_a_plan_that_did_not_happen(self):
        ops = [{"op": "add_wire", "points": [[10, 10], [20, 10]]}]
        doc = KS.KiCadSchematic.from_text(schematic_text(), "<memory>")
        # nothing was run: the file does not contain the wire
        report = SO.verify_all(doc, ops)
        self.assertFalse(report["all_verified"])
        self.assertFalse(report["operations"][0]["verified"])

# --------------------------------------------------------------- propose/apply
class TestSchPlan(_Tmp):

    def test_empty_plan_is_refused(self):
        path = self.write(schematic_text())
        with self.assertRaises(E.EditError) as ctx:
            E.propose_schematic(path, [], {"engine": False})
        self.assertEqual(ctx.exception.code, "ARG_MISSING")

    def test_a_board_file_is_refused_by_the_schematic_path(self):
        path = self.write("(kicad_pcb\n\t(version 20240108)\n)\n", "b.kicad_pcb")
        with self.assertRaises(E.EditError) as ctx:
            E.propose_schematic(path, [{"op": "add_no_connect", "x": 1, "y": 1}],
                                {"engine": False})
        self.assertEqual(ctx.exception.code, "FORMAT_NOT_SUPPORTED")

    def test_the_other_containers_are_named_not_parsed(self):
        """An .epro / .eprj1 / junk file gets told WHAT IT IS, not a parse error."""
        import zipfile
        zpath = os.path.join(self.tmp, "proj.epro")
        with zipfile.ZipFile(zpath, "w") as zf:       # a real zip container
            zf.writestr("project.json", "{}")
        with self.assertRaises(E.EditError) as ctx:
            E.propose_schematic(zpath, [{"op": "add_no_connect", "x": 1, "y": 1}],
                                {"engine": False})
        self.assertEqual(ctx.exception.code, "WRITE_UNSUPPORTED_FORMAT")
        self.assertIn(".esch", str(ctx.exception))

        spath = os.path.join(self.tmp, "proj.eprj")
        with open(spath, "wb") as fh:
            fh.write(b"SQLite format 3\x00" + b"\x00" * 64)
        with self.assertRaises(E.EditError) as ctx:
            E.schematic_summary(spath, engine=False)
        self.assertEqual(ctx.exception.code, "WRITE_UNSUPPORTED_FORMAT")

        junk = self.write("hello, I am a text file\n", "junk.txt")
        with self.assertRaises(E.EditError) as ctx:
            E.schematic_view(junk)
        self.assertEqual(ctx.exception.code, "NOT_A_SCHEMATIC")

    def test_a_missing_file_is_refused_not_created(self):
        ghost = os.path.join(self.tmp, "nope.kicad_sch")
        with self.assertRaises(E.EditError) as ctx:
            E.schematic_summary(ghost, engine=False)
        self.assertEqual(ctx.exception.code, "FILE_NOT_FOUND")
        self.assertFalse(os.path.exists(ghost))

    def test_proposal_writes_nothing(self):
        path = self.write(schematic_text())
        before = self.read(path)
        E.propose_schematic(path, [{"op": "add_wire",
                                    "points": [[10, 10], [20, 10]]}],
                            {"engine": False})
        self.assertEqual(self.read(path), before)

    def test_apply_inserts_without_disturbing_a_single_existing_line(self):
        text = schematic_text(with_symbol=True)
        path = self.write(text)
        plan = E.propose_schematic(path, [
            {"op": "add_wire", "points": [[10, 10], [20, 10]]},
            {"op": "add_label", "text": "N", "x": 10, "y": 10}], {"engine": False})
        result = E.apply_plan(plan["plan_id"], confirm=True, options={"engine": False})
        self.assertTrue(result["gates"]["bytes_written"]["ok"])
        self.assertTrue(result["gates"]["token_identity"]["ok"])
        self.assertTrue(result["gates"]["postconditions"]["ok"])
        new_lines = self.read(path).decode("utf-8").splitlines()
        old_lines = text.splitlines()
        it = iter(new_lines)
        # every original line is still there, in order: the file only grew
        for line in old_lines:
            for cand in it:
                if cand == line:
                    break
            else:
                self.fail("original line disappeared from the file: %r" % line)

    def test_apply_needs_confirm_and_the_plan_is_single_use(self):
        path = self.write(schematic_text())
        plan = E.propose_schematic(path, [{"op": "add_no_connect", "x": 10, "y": 10}],
                                   {"engine": False})
        with self.assertRaises(E.EditError) as ctx:
            E.apply_plan(plan["plan_id"], confirm=False, options={"engine": False})
        self.assertEqual(ctx.exception.code, "CONFIRMATION_REQUIRED")
        E.apply_plan(plan["plan_id"], confirm=True, options={"engine": False})
        with self.assertRaises(E.EditError) as ctx:
            E.apply_plan(plan["plan_id"], confirm=True, options={"engine": False})
        self.assertEqual(ctx.exception.code, "PLAN_NOT_FOUND")

    def test_apply_refuses_a_file_that_changed_after_the_plan(self):
        path = self.write(schematic_text())
        plan = E.propose_schematic(path, [{"op": "add_no_connect", "x": 10, "y": 10}],
                                   {"engine": False})
        with open(path, "ab") as fh:
            fh.write(b"\n")
        with self.assertRaises(E.EditError) as ctx:
            E.apply_plan(plan["plan_id"], confirm=True, options={"engine": False})
        self.assertEqual(ctx.exception.code, "SCHEMATIC_CHANGED_SINCE_PLAN")

    def test_a_bad_postcondition_rolls_the_file_back(self):
        """The gate that matters: lie in the plan, lose the write."""
        text = schematic_text()
        path = self.write(text)
        plan = E.propose_schematic(path, [{"op": "add_no_connect", "x": 10, "y": 10}],
                                   {"engine": False})
        # corrupt the operations list the gate verifies against
        plan_id = plan["plan_id"]
        SP.PLANS[plan_id]["operations"][0]["x"] = 99.0
        with self.assertRaises(E.EditError) as ctx:
            E.apply_plan(plan_id, confirm=True, options={"engine": False})
        self.assertEqual(ctx.exception.code, "GATE_BLOCKED")
        self.assertEqual(self.read(path), text.encode("utf-8"))

    def test_checkpoint_restores_the_identical_bytes(self):
        text = schematic_text()
        path = self.write(text)
        plan = E.propose_schematic(path, [{"op": "add_no_connect", "x": 10, "y": 10}],
                                   {"engine": False})
        result = E.apply_plan(plan["plan_id"], confirm=True, options={"engine": False})
        cid = result["checkpoint"]["checkpoint_id"]
        restored = E.restore_checkpoint(cid, confirm=True)
        self.assertTrue(restored["changed"])
        self.assertEqual(self.read(path), text.encode("utf-8"))

    def test_warnings_name_an_endpoint_that_touches_nothing(self):
        path = self.write(schematic_text())
        plan = E.propose_schematic(path, [
            {"op": "add_wire", "points": [[10, 10], [20, 10]]}], {"engine": False})
        gates = [w["gate"] for w in plan["warnings"]]
        self.assertIn("endpoint_not_attached", gates)

    def test_outside_page_is_reported(self):
        path = self.write(schematic_text())
        plan = E.propose_schematic(path, [
            {"op": "add_no_connect", "x": 900, "y": 900}], {"engine": False})
        self.assertIn("outside_page", [w["gate"] for w in plan["warnings"]])

    def test_view_reports_symbols_and_objects(self):
        path = self.write(schematic_text(with_symbol=True))
        syms = E.schematic_view(path, kind="symbols")
        self.assertEqual(syms["symbols"][0]["reference"], "R1")
        objs = E.schematic_view(path, kind="objects")
        self.assertEqual(objs["total"], 2)          # 1 symbol + 1 wire
        self.assertIn("frame", objs)
        digest = E.schematic_summary(path, engine=False)["digest"]
        self.assertEqual(digest["symbols"], 1)
        self.assertEqual(digest["wires"], 1)

# --------------------------------------------------------------- engine gates
def demo_schematic():
    """A real KiCad demo schematic, if this machine has one."""
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
            if name.endswith(".kicad_sch"):
                cand = os.path.join(path, name)
                try:
                    KS.KiCadSchematic.from_path(cand)
                except Exception:                    # noqa: BLE001
                    continue
                return cand
    return None

DEMO = demo_schematic()

@unittest.skipUnless(DEMO, "no kicad-cli / demo schematics on this machine")
class TestSchEngineGates(_Tmp):
    """The parts that only KiCad can answer."""

    def copy_demo(self):
        path = os.path.join(self.tmp, "demo.kicad_sch")
        shutil.copyfile(DEMO, path)
        return path

    def test_a_dangling_label_is_blocked_by_the_erc_gate(self):
        path = self.copy_demo()
        before = self.read(path)
        plan = E.propose_schematic(path, [
            {"op": "add_label", "text": "ZZ_DANGLING", "x": 300, "y": 200},
            {"op": "add_wire", "points": [[300, 200], [300, 190]]}])
        self.assertEqual(plan["erc_before"]["status"], "ok")
        with self.assertRaises(E.EditError) as ctx:
            E.apply_plan(plan["plan_id"], confirm=True)
        self.assertEqual(ctx.exception.code, "GATE_BLOCKED")
        gates = ctx.exception.extra["gates"]
        self.assertTrue(gates["erc"]["comparable"])
        self.assertGreater(gates["erc"]["new_total"], 0)
        self.assertEqual(self.read(path), before)      # rolled back

    def test_the_netlister_sees_a_placed_symbol_and_its_pins(self):
        path = self.copy_demo()
        plan = E.propose_schematic(path, [
            {"op": "add_symbol", "lib_id": "Test:R", "x": 250, "y": 250,
             "value": "1k", "definition": DEF_R}])
        ref = plan["operations"][0]["detail"]["reference"]
        result = E.apply_plan(plan["plan_id"], confirm=True,
                              options={"allow_new_violations": True})
        netlist = result["gates"]["netlist"]
        self.assertTrue(netlist["comparable"])
        self.assertIn(ref, netlist["components_added"])
        self.assertTrue(result["gates"]["postconditions"]["ok"])

    def test_a_field_edit_passes_every_gate_untouched_connectivity(self):
        path = self.copy_demo()
        doc = KS.KiCadSchematic.from_path(path)
        # a normal part, not a power symbol: renaming #PWR GND *does* rename the
        # net, and the gate is right to call that a connectivity change (there is
        # a separate test for exactly that).
        symbols = [s for s in doc.symbols()
                   if s["reference"] and s["value"]
                   and not s["reference"].startswith("#")]
        if not symbols:
            self.skipTest("the demo sheet has no ordinary symbol with a value")
        ref = symbols[0]["reference"]
        before_plan = E.propose_schematic(path, [
            {"op": "set_symbol_field", "reference": ref, "field": "Value",
             "value": "CHANGED"}])
        result = E.apply_plan(before_plan["plan_id"], confirm=True)
        netlist = result["gates"]["netlist"]
        self.assertTrue(netlist["comparable"])
        self.assertFalse(netlist["changed"])           # a value is not connectivity
        self.assertEqual(result["gates"]["erc"]["new_total"], 0)
        self.assertIn(ref, netlist["components_changed"])

    def test_renaming_a_power_symbol_is_blocked_as_a_netlist_change(self):
        """Changing #PWR's value renames the power net - the netlister proves it."""
        path = self.copy_demo()
        doc = KS.KiCadSchematic.from_path(path)
        power = [s for s in doc.symbols()
                 if (s["reference"] or "").startswith("#PWR")]
        if not power:
            self.skipTest("the demo sheet has no #PWR symbol")
        before = self.read(path)
        plan = E.propose_schematic(path, [
            {"op": "set_symbol_field", "reference": power[0]["reference"],
             "field": "Value", "value": "NOT_A_POWER_NET"}])
        with self.assertRaises(E.EditError) as ctx:
            E.apply_plan(plan["plan_id"], confirm=True)
        self.assertEqual(ctx.exception.code, "GATE_BLOCKED")
        gates = ctx.exception.extra["gates"]
        self.assertTrue(gates["netlist"]["changed"])
        self.assertEqual(self.read(path), before)

class TestPinGeometry(_Tmp):
    """A pin is a connection point but it is not a drawing object.

    Its position lives in the library definition and is only mapped onto the
    sheet by the placed symbol's `at`/`mirror`/rotation. Before `pin_points`
    existed, a wire drawn from a pin to a label looked like it touched nothing,
    and every such plan carried a bogus "electrically open" warning. The four
    frames below are the whole mapping: `sheet = (X + dx, Y - dy)`.
    """

    def editor(self, text=None, **swap):
        body = text if text is not None else schematic_text(with_symbol=True)
        for old, new in swap.items():
            body = body.replace(old, new)
        self.path = self.write(body)
        return SO.SchEditor(KS.KiCadSchematic.from_path(self.path))

    def pins(self, **swap):
        ed = self.editor(**swap)
        return dict((number, (round(x, 4), round(y, 4)))
                    for x, y, ref, number, lib_id in ed.pin_points()
                    if ref == "R1")

    def test_a_library_pin_lands_on_the_sheet(self):
        # library pins at (0, 3.81) and (0, -3.81); the symbol is at (76.2, 76.2)
        # and the sheet's y grows down while the library's grows up
        self.assertEqual(self.pins(), {"1": (76.2, 72.39), "2": (76.2, 80.01)})

    def test_a_rotated_symbol_moves_its_pins(self):
        self.assertEqual(self.pins(**{"(at 76.2 76.2 0)": "(at 76.2 76.2 90)"}),
                         {"1": (72.39, 76.2), "2": (80.01, 76.2)})

    def test_a_180_degree_symbol_moves_its_pins(self):
        self.assertEqual(self.pins(**{"(at 76.2 76.2 0)": "(at 76.2 76.2 180)"}),
                         {"1": (76.2, 80.01), "2": (76.2, 72.39)})

    def test_a_mirrored_symbol_moves_its_pins(self):
        # (mirror x) flips the library's y, which on the sheet is a y flip too
        self.assertEqual(
            self.pins(**{"(at 76.2 76.2 0)": "(at 76.2 76.2 0)\n\t\t(mirror x)"}),
            {"1": (76.2, 80.01), "2": (76.2, 72.39)})

    def test_objects_near_finds_a_pin_and_says_whose_it_is(self):
        ed = self.editor()
        hits = ed.objects_near(76.2, 72.39)
        pins = [h for h in hits if h["kind"] == "pin"]
        self.assertEqual(len(pins), 1)
        self.assertEqual((pins[0]["reference"], pins[0]["pin"]), ("R1", "1"))
        self.assertEqual(pins[0]["lib_id"], "Test:R")

    def test_a_point_with_nothing_on_it_has_no_objects(self):
        self.assertEqual(self.editor().objects_near(30.0, 90.0), [])

    def test_a_wire_ending_on_a_pin_is_not_called_dangling(self):
        # two ops in one plan: the second wire's far end only exists once the
        # first is drawn, which is exactly why the check runs on the finished
        # tree instead of on the document as it was before the plan.
        plan = E.propose_schematic(self.path_(), [
            {"op": "add_wire", "points": [[76.2, 72.39], [63.5, 72.39]]},
            {"op": "add_wire", "points": [[63.5, 72.39], [63.5, 50.8]]}],
            {"engine": False})
        gates = [w["gate"] for w in plan["warnings"]]
        self.assertNotIn("endpoint_not_attached", gates, plan["warnings"])
        self.assertEqual(plan["blocking"], [])

    def test_a_floating_wire_is_still_reported(self):
        # the negative control: without it the test above would pass even if the
        # check had been deleted
        plan = E.propose_schematic(self.path_(), [
            {"op": "add_wire", "points": [[30.0, 90.0], [40.0, 90.0]]}],
            {"engine": False})
        gate = [w for w in plan["warnings"] if w["gate"] == "endpoint_not_attached"]
        self.assertEqual(len(gate), 1, plan["warnings"])
        self.assertEqual(len(gate[0]["endpoints"]), 2)

    def path_(self):
        if not getattr(self, "path", None) or not os.path.exists(self.path):
            self.editor()
        return self.path


if __name__ == "__main__":
    unittest.main()
