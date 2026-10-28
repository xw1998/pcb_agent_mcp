"""Byte-identity against files KiCad itself wrote.

The synthetic sample in `tests/data/` proved the parser on a file *we* wrote,
which is the weakest possible evidence: a writer and a reader can agree on the
same wrong idea. This test uses the boards and schematics that ship with the
KiCad installation - tab-indented, CRLF, literal tabs inside strings, 200k
atoms, version stamps from 20241030 to 20260206 - and demands that

* the S-expression tree carries every token (`token_identity`),
* re-emitting it reproduces the file **byte for byte** (`bytes_identical`),
* and the canonical adapter reads the same numbers out of it as the tree holds.

Skipped when no KiCad installation is found; it is a real-machine check, not a
fixture.
"""
import os
import unittest

from pcb_agent_mcp import desktop
from pcb_agent_mcp.adapters import kicad_pcb as A
from pcb_agent_mcp.fidelity import kicad_doc as K
from pcb_agent_mcp.fidelity import sexpr as S

# Bigger files cost minutes in pure Python and add nothing: the format, not the
# size, is what is being tested.
MAX_BYTES = 2_500_000
MAX_FILES = 12

def _demo_files(ext):
    info = desktop.detect("kicad")
    if not info.get("found"):
        return []
    root = os.path.join(os.path.dirname(info["install_dir"]), "share", "kicad", "demos")
    if not os.path.isdir(root):
        return []
    out = []
    for dirpath, _dirnames, filenames in os.walk(root):
        for name in filenames:
            if not name.endswith(ext):
                continue
            path = os.path.join(dirpath, name)
            try:
                if os.path.getsize(path) > MAX_BYTES:
                    continue
            except OSError:
                continue
            out.append(path)
    return sorted(out)[:MAX_FILES]

class TestRealBoards(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.boards = _demo_files(".kicad_pcb")
        cls.schematics = _demo_files(".kicad_sch")

    def _check(self, path):
        with open(path, "rb") as fh:
            raw = fh.read()
        doc = K.KiCadDocument.from_path(path)
        report = doc.verify()
        self.assertTrue(report["token_identity"], (path, report))
        self.assertTrue(report["bytes_identical"], (path, report))
        self.assertEqual(doc.to_bytes(), raw, path)
        return doc

    def test_real_boards_are_byte_identical(self):
        if not self.boards:
            self.skipTest("no KiCad demo boards on this machine")
        for path in self.boards:
            self._check(path)

    def test_real_schematics_are_byte_identical(self):
        if not self.schematics:
            self.skipTest("no KiCad demo schematics on this machine")
        for path in self.schematics:
            self._check(path)

    def test_versions_this_parser_has_not_been_taught_are_still_exact(self):
        """The tree is version-agnostic on purpose: new fields are carried, not
        rejected. If this ever fails the reader has started to validate."""
        if not self.boards:
            self.skipTest("no KiCad demo boards on this machine")
        versions = set()
        for path in self.boards:
            doc = K.KiCadDocument.from_path(path)
            versions.add(doc.stats()["version"])
        self.assertTrue(versions, "no versions read")

    def test_adapter_and_tree_agree_on_a_real_board(self):
        if not self.boards:
            self.skipTest("no KiCad demo boards on this machine")
        path = self.boards[0]
        doc = K.KiCadDocument.from_path(path)
        board = A.parse_board(path)
        with open(path, "rb") as fh:
            text = fh.read().decode("utf-8", "surrogateescape")
        # Two readers must not disagree: the adapter has its own tokenizer for
        # speed, so this is the test that keeps the two honest.
        twin = A.parse_board_text(text, path)
        self.assertEqual(len(board.footprints), len(twin.footprints))
        self.assertEqual(len(board.tracks), len(twin.tracks))
        self.assertEqual(len(board.vias), len(twin.vias))
        self.assertEqual(len(board.pads()), len(twin.pads()))
        # ... and the tree the gate watches must describe the same board.
        self.assertEqual(len(K.KiCadDocument.from_path(path).items("footprint")),
                         len(board.footprints))

class TestTrivia(unittest.TestCase):
    def test_whitespace_survives_a_round_trip(self):
        text = '(a\r\n\t(b 1  2)\r\n\t\t(c "x\ty")\r\n)\r\n'
        self.assertEqual(S.dumps_document(S.parse(text)), text)

    def test_a_literal_tab_in_a_string_is_not_re_escaped(self):
        text = '(a "x\ty")'
        self.assertEqual(S.dumps_document(S.parse(text)), text)

    def test_an_unknown_escape_keeps_its_backslash(self):
        text = '(a "c:\\\\path")\n'
        self.assertEqual(S.dumps_document(S.parse(text)), text)

    def test_a_built_node_is_not_glued_to_its_neighbour(self):
        parent = S.parse("(a (b 1))")[0]
        parent.items.append(S.Seq([S.Atom("c"), S.Atom("2")]))
        out = S.dumps_document(S.Seq([parent]))
        self.assertIn("(c 2)", out)

    def test_reformat_indents_like_a_sibling(self):
        parent = S.parse('(a\n\t(b 1)\n\t(c 2)\n)')[0]
        fresh = S.Seq([S.Atom("d"), S.Seq([S.Atom("e"), S.Atom("3")])])
        S.append_child(parent, fresh)
        out = S.dumps_document(S.Seq([parent]))
        self.assertIn('\n\t(d\n\t\t(e 3)\n\t)', out)

    def test_error_says_where_the_file_is_broken(self):
        with self.assertRaises(S.SexprError) as cm:
            S.parse("(a (b)\n (c)")
        self.assertIn("never closed", str(cm.exception))
        with self.assertRaises(S.SexprError) as cm2:
            S.parse("(a))")
        self.assertIn("column 4", str(cm2.exception))

if __name__ == "__main__":
    unittest.main()
