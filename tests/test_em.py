"""Tests for `calc.em` and the `fullwave_solver` tool.

The seam has exactly four claims to defend, and each one is a test:

* **nothing here is a solver** - the module exposes detection and handoff text,
  never a field, a pattern or an S-parameter;
* **presence is not evidence** - `verified_here` is false for every solver, and
  the version reported is the one the binary printed, not one parsed out of a
  table in this repository;
* **a variable pointing at nothing is its own answer** - a typo in
  `PCB_AGENT_OPENEMS` must not read as "not installed";
* **asking for a name that is not there is an ERROR** - never a silent fallback
  to the closed-form starting geometry.

No test here needs a real solver. One test points the override variable at
`sys.executable` so the probe path itself (spawn, read, parse, no hang) is
exercised; the rest point it at nothing.
"""
import os
import sys
import unittest

from pcb_agent_mcp import tools
from pcb_agent_mcp.calc import em

ENV_VARS = [em.DETECTORS[k]["env"] for k in em.DETECTORS]

class EmBase(unittest.TestCase):
    def setUp(self):
        self.saved = dict((k, os.environ.pop(k, None)) for k in ENV_VARS)

    def tearDown(self):
        for key, value in self.saved.items():
            if value is None:
                os.environ.pop(key, None)
            else:
                os.environ[key] = value

    def call(self, **args):
        return tools.call_tool("fullwave_solver", args)

class TestThereIsNoSolverHere(EmBase):
    def test_the_module_exposes_detection_and_nothing_that_computes_a_field(self):
        public = [n for n in dir(em) if not n.startswith("_")]
        for forbidden in ("solve", "pattern", "s_parameter", "sweep", "fdtd",
                          "mom", "fem"):
            self.assertNotIn(forbidden, public)
        self.assertIn("radiation patterns", em.WHAT_IT_WILL_NOT_DO)
        self.assertIn("Maxwell", em.WHAT_IT_WILL_NOT_DO)

    def test_the_handoff_says_what_has_to_come_from_you(self):
        for step in em._STEPS:
            self.assertTrue(step.strip())
        self.assertIn("sweep", em._STEPS[3].lower())
        self.assertEqual(len(em._STEPS), 4)

    def test_every_detector_names_a_handoff_and_what_it_needs(self):
        for name, spec in em.DETECTORS.items():
            self.assertTrue(spec["handoff"], name)
            self.assertTrue(spec["needs_from_you"], name)
            self.assertTrue(spec["env"].startswith("PCB_AGENT_"), name)

class TestNothingInstalledIsAnAnswer(EmBase):
    def test_an_empty_machine_reports_every_solver_missing_without_raising(self):
        got = em.detect()
        self.assertEqual(got["found"], [])
        self.assertEqual(got["missing"], list(em.DEFAULT_NAMES))
        for row in got["solvers"]:
            self.assertFalse(row["found"])
            self.assertIsNone(row["path"])
            self.assertFalse(row["verified_here"])

    def test_verification_text_reads_as_none_rather_than_as_a_claim(self):
        self.assertIn("never run", em.VERSION_VERIFICATION)
        self.assertIn("verified_here: false", em.VERSION_VERIFICATION)

    def test_an_unknown_name_is_refused(self):
        with self.assertRaises(ValueError):
            em.detect(names=["cst"])

class TestAProbeIsNotAProof(EmBase):
    def test_a_real_binary_is_found_and_its_own_version_is_reported(self):
        # sys.executable stands in for a solver: the point is that the probe
        # spawns something, reads what IT says, and attaches no trust to it.
        os.environ["PCB_AGENT_OPENEMS"] = sys.executable
        row = em.detect(names=["openems"])["solvers"][0]
        self.assertTrue(row["found"])
        self.assertEqual(row["path"], sys.executable)
        self.assertEqual(row["how_found"], "env PCB_AGENT_OPENEMS")
        self.assertTrue(row["version"])
        self.assertIn("Python", row["version"])
        self.assertFalse(row["verified_here"])

    def test_probe_false_reads_the_path_and_skips_running_it(self):
        os.environ["PCB_AGENT_OPENEMS"] = sys.executable
        row = em.detect(names=["openems"], probe=False)["solvers"][0]
        self.assertTrue(row["found"])
        self.assertIsNone(row["version"])
        self.assertIsNone(row["exit_code"])

    def test_a_variable_pointing_at_nothing_is_its_own_answer(self):
        ghost = os.path.join(os.environ.get("TEMP") or os.getcwd(), "no-such-openems")
        os.environ["PCB_AGENT_OPENEMS"] = ghost
        row = em.detect(names=["openems"], probe=False)["solvers"][0]
        self.assertFalse(row["found"])
        self.assertIn("not a file", row["env_problem"])
        self.assertIn("no-such-openems", row["env_problem"])

class TestTheTool(EmBase):
    def test_the_default_probe_answers_the_matrix(self):
        out = self.call()
        self.assertEqual(out["status"], "ok", out)
        self.assertEqual(out["value"]["found"], [])
        self.assertEqual(out["value"]["missing"], list(em.DEFAULT_NAMES))
        self.assertIn("Maxwell", out["value"]["this_server_will_not_do"])
        self.assertEqual(len(out["value"]["how_to_hand_over"]), 4)
        self.assertIn("no full-wave solver is installed", out["message"])
        self.assertIn("verified_here", out["precision"])

    def test_a_named_solver_that_is_not_installed_is_an_error_not_a_substitute(self):
        out = self.call(solver="openems")
        self.assertEqual(out["status"], "error")
        self.assertEqual(out["reason_code"], "SOLVER_NOT_FOUND")
        self.assertIn("PCB_AGENT_OPENEMS", out["hint"])
        self.assertIn("will not", out["hint"])
        self.assertIn("solvers", out["value"])          # the matrix is still there

    def test_a_named_solver_that_is_there_comes_back_ok(self):
        os.environ["PCB_AGENT_MEEP"] = sys.executable
        out = self.call(solver="meep")
        self.assertEqual(out["status"], "ok", out)
        self.assertEqual(out["value"]["found"], ["meep"])
        self.assertIn("Python", out["message"])
        self.assertFalse(out["value"]["solvers"][0]["verified_here"])

    def test_an_unknown_solver_name_is_refused(self):
        out = self.call(solver="cst")
        self.assertEqual(out["status"], "error")
        self.assertEqual(out["reason_code"], "ARG_INVALID")
        self.assertIn("cannot be probed", out["hint"])

    def test_a_timeout_of_zero_is_refused(self):
        out = self.call(timeout_s=0)
        self.assertEqual(out["reason_code"], "ARG_RANGE")

    def test_the_capability_matrix_mentions_the_seam(self):
        out = tools.call_tool("calc_capabilities", {})
        layers = out.get("layers") or {}
        self.assertIn("fullwave", layers)
        self.assertIn("seam", layers["fullwave"])
        self.assertIn("fullwave_solver", out["available"])
        joined = " ".join(out["not_implemented_yet"])
        self.assertIn("SEAM, not a solver", joined)

if __name__ == "__main__":
    unittest.main()
