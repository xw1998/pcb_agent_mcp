"""The finished thickness the file STATES must equal the stackup it BUILDS.

This is not cosmetics. KiCad's own `stats` sums the stackup and ignores
`(general (thickness ...))`, so a 4-layer board declared as 1.6 mm whose
dielectrics only added up to 1.18 mm shipped as 1.18 mm - and every gate in the
project was green, because nothing compared the two numbers. `create_board`
divided the dielectric budget over `inner` layers instead of over the dielectric
layers, which is exactly the kind of bug that a plausible-looking file hides.

So there are two halves here:
  * the generator writes a stackup that sums to the declared thickness, for every
    layer count and thickness the tool accepts;
  * the read side (`oracle.summary`) reports the mismatch when it exists, so a
    file written by anything else - or by an older version of us - is caught
    without running KiCad at all.

The engine half (`kicad-cli` agreeing) is opt-in via PCB_AGENT_ENGINE_TESTS:
a kicad-cli stats+drc round trip costs minutes on this machine.
"""
import os
import tempfile
import shutil
import unittest

from pcb_agent_mcp import edit as E
from pcb_agent_mcp import oracle as O
from pcb_agent_mcp.adapters import kicad_pcb as A
from pcb_agent_mcp.edit import plan as P
from pcb_agent_mcp.fidelity import engine as ENGINE
from pcb_agent_mcp.fidelity import kicad_doc as K

LAYERS = (2, 4, 6)
THICKNESSES = (0.8, 1.2, 1.6)


def _stackup_sum(board):
    total = 0.0
    seen = False
    for layer in board.stackup or []:
        v = layer.get("thickness_mm")
        if isinstance(v, (int, float)) and v > 0:
            total += float(v)
            seen = True
    return round(total, 4) if seen else None


class StackupBase(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.mkdtemp(prefix="pcbstack-")
        self._home = os.environ.get("PCB_AGENT_HOME")
        os.environ["PCB_AGENT_HOME"] = os.path.join(self.tmp, "home")

    def tearDown(self):
        if self._home is None:
            os.environ.pop("PCB_AGENT_HOME", None)
        else:
            os.environ["PCB_AGENT_HOME"] = self._home
        P.PLANS.clear()
        shutil.rmtree(self.tmp, ignore_errors=True)

    def make(self, layers, thickness):
        path = os.path.join(self.tmp, "b%d_%s.kicad_pcb" % (layers, thickness))
        report = E.create_board(path, {"width_mm": 40, "height_mm": 30,
                                       "copper_layers": layers,
                                       "thickness_mm": thickness,
                                       "engine": False})
        return path, report


class TestGeneratedStackupSumsUp(StackupBase):
    """Every accepted geometry: stated thickness == built thickness."""

    def test_matrix_stated_equals_stackup_sum(self):
        for layers in LAYERS:
            for thickness in THICKNESSES:
                with self.subTest(layers=layers, thickness=thickness):
                    path, _report = self.make(layers, thickness)
                    board = A.parse_board(path)
                    self.assertAlmostEqual(board.thickness_mm, thickness, places=3)
                    total = _stackup_sum(board)
                    self.assertIsNotNone(total)
                    self.assertLessEqual(abs(total - thickness), 0.005,
                                         "stackup %r sums to %r, file states %r"
                                         % (layers, total, thickness))

    def test_dielectrics_are_not_squeezed_into_the_inner_layers(self):
        """The bug: 4 layers spent the whole budget on `inner`, so the two outer
        dielectrics were 0.05 mm each and the board came out 1.18 mm."""
        path, _report = self.make(4, 1.6)
        board = A.parse_board(path)
        dielectrics = [l for l in board.stackup
                       if str(l.get("type") or "").startswith("core")
                       or str(l.get("type") or "").startswith("prepreg")]
        self.assertEqual(len(dielectrics), 3)   # 4 copper layers -> 3 dielectrics
        self.assertGreater(min(l["thickness_mm"] for l in dielectrics), 0.1)

    def test_layer_counts_stay_in_step_with_copper(self):
        for layers, copper in ((2, 2), (4, 4), (6, 6)):
            with self.subTest(layers=layers):
                path, _report = self.make(layers, 1.6)
                board = A.parse_board(path)
                cu = [l for l in board.stackup if l.get("type") == "copper"]
                self.assertEqual(len(cu), copper)
                for layer in cu:
                    self.assertAlmostEqual(layer["thickness_mm"], 0.035, places=4)


class TestSummaryReportsTheMismatch(StackupBase):
    """A file that states one thing and builds another must say so, read-only."""

    def test_agreeing_file_is_reported_as_agreeing(self):
        path, _report = self.make(4, 1.6)
        out = O.summary(A.parse_board(path))
        self.assertEqual(out["status"], "ok")
        self.assertAlmostEqual(out["thickness_mm"], 1.6, places=3)
        self.assertAlmostEqual(out["thickness_from_stackup_mm"], 1.6,
                               places=3)
        self.assertTrue(out["thickness_agrees"])

    def test_disagreeing_file_is_reported_as_disagreeing(self):
        path, _report = self.make(4, 1.6)
        with open(path, "r", encoding="utf-8", newline="") as fh:
            text = fh.read()
        # Same stackup, a different claim: exactly the shape of the old bug,
        # produced from the outside so the generator cannot help us pass.
        self.assertIn("(thickness 1.6)", text)
        broken = text.replace("(thickness 1.6)", "(thickness 1.18)", 1)
        board = A.parse_board_text(broken, path=path)
        out = O.summary(board)
        self.assertFalse(out["thickness_agrees"])
        self.assertAlmostEqual(out["thickness_mm"], 1.18, places=3)
        self.assertAlmostEqual(out["thickness_from_stackup_mm"], 1.6,
                               places=3)

    def test_no_stackup_is_none_not_a_guess(self):
        path, _report = self.make(2, 1.6)
        with open(path, "r", encoding="utf-8", newline="") as fh:
            text = fh.read()
        start = text.index("(stackup")
        depth = 0
        end = start
        for i, ch in enumerate(text[start:], start):
            if ch == "(":
                depth += 1
            elif ch == ")":
                depth -= 1
                if depth == 0:
                    end = i + 1
                    break
        board = A.parse_board_text(text[:start] + text[end:], path=path)
        out = O.summary(board)
        self.assertIsNone(out["thickness_from_stackup_mm"])
        self.assertIsNone(out["thickness_agrees"])


@unittest.skipUnless(os.environ.get("PCB_AGENT_ENGINE_TESTS"),
                     "set PCB_AGENT_ENGINE_TESTS=1 (kicad-cli round trip is slow)")
class TestKiCadAgreesWithTheStackup(StackupBase):
    def test_engine_measures_the_declared_thickness(self):
        path, report = self.make(4, 1.6)
        stats = ENGINE.stats(path)
        self.assertEqual(stats.get("status"), "ok")
        measured = P._engine_mm((stats.get("board") or {}).get("board_thickness"))
        self.assertIsNotNone(measured)
        self.assertLessEqual(abs(measured - 1.6), 0.005)

    def test_create_board_gate_reports_agreement(self):
        path, report = self.make(4, 1.6)
        r = E.create_board(path, {"width_mm": 40, "height_mm": 30,
                                  "copper_layers": 4, "thickness_mm": 1.6,
                                  "overwrite": True})
        gates = r["gates"]["engine_stats"]
        self.assertTrue(gates["thickness_agrees"], gates)
        self.assertAlmostEqual(gates["thickness_stated_mm"], 1.6, places=3)
        self.assertAlmostEqual(gates["thickness_engine_mm"], 1.6, places=3)


if __name__ == "__main__":
    unittest.main()
