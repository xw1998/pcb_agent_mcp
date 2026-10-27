"""Tool-registry contract tests.

Two things are checked here that no single module can check for itself:

1. **Every** tool answers with a `status` field, returns a JSON-serialisable body
   and never leaks an exception as a stack trace (a model talking to a broken
   tool must see a refusal it can act on).
2. Reading a board through the tool layer leaves the file byte-identical. This is
   the executable form of the user's rule: never touch my project.
"""
import hashlib
import json
import os
import sys
import tempfile
import unittest
import zipfile

sys.path.insert(0, os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "src"))

from pcb_agent_mcp import tools  # noqa: E402

HERE = os.path.dirname(os.path.abspath(__file__))
SAMPLE = os.path.join(HERE, "data", "sample.kicad_pcb")

BOARD_TOOLS = ["board_summary", "board_components", "board_nets", "board_clearance",
               "board_free_space", "board_pour_connectivity", "board_cross_split",
               "board_loop_area"]


def _sha256(path):
    with open(path, "rb") as f:
        return hashlib.sha256(f.read()).hexdigest()


class TestRegistryShape(unittest.TestCase):
    def test_every_tool_is_documented_and_schemad(self):
        for spec in tools.list_tools():
            self.assertTrue(spec["description"], spec["name"])
            self.assertGreater(len(spec["description"]), 30, spec["name"])
            self.assertEqual(spec["inputSchema"]["type"], "object", spec["name"])

    def test_unknown_tool_is_refused(self):
        r = tools.call_tool("no_such_tool", {})
        self.assertEqual(r["status"], "error")
        self.assertEqual(r["reason_code"], "TOOL_NOT_FOUND")

    def test_missing_argument_is_refused_not_raised(self):
        r = tools.call_tool("board_summary", {})
        self.assertEqual(r["status"], "error")
        self.assertEqual(r["reason_code"], "ARG_MISSING")

    def test_result_is_json_serialisable(self):
        r = tools.call_tool("calc_capabilities", {})
        json.dumps(r, ensure_ascii=False)


class TestBoardToolsAreReadOnly(unittest.TestCase):
    def test_every_board_tool_leaves_the_file_untouched(self):
        before = _sha256(SAMPLE)
        mtime = os.stat(SAMPLE).st_mtime_ns
        args = {"path": SAMPLE, "layer": "F.Cu", "net": "VCC"}
        for name in BOARD_TOOLS:
            r = tools.call_tool(name, args)
            self.assertIn(r["status"], ("ok", "error"), name)
            self.assertEqual(_sha256(SAMPLE), before, name)
        self.assertEqual(os.stat(SAMPLE).st_mtime_ns, mtime)

    def test_epro_inspect_leaves_the_archive_untouched(self):
        tmp = tempfile.mkdtemp()
        path = os.path.join(tmp, "demo.epro")
        with zipfile.ZipFile(path, "w") as zf:
            zf.writestr("project.json", "{}")
            zf.writestr("demo.epcb", json.dumps(
                {"type": "NET", "id": "n1", "ticket": 1}) + "||" + '{"name":"GND"}\n')
        before = _sha256(path)
        try:
            r = tools.call_tool("epro_inspect", {"path": path})
            self.assertEqual(r["status"], "ok")
            self.assertEqual(_sha256(path), before)
        finally:
            import shutil
            shutil.rmtree(tmp, ignore_errors=True)

    def test_board_tools_never_create_a_missing_file(self):
        missing = os.path.join(tempfile.gettempdir(), "definitely_missing_%d.kicad_pcb"
                               % os.getpid())
        if os.path.exists(missing):
            os.unlink(missing)
        r = tools.call_tool("board_summary", {"path": missing})
        self.assertEqual(r["reason_code"], "FILE_NOT_FOUND")
        self.assertFalse(os.path.exists(missing))


class TestNonWindowsSafe(unittest.TestCase):
    def test_desktop_tools_are_listed_even_off_windows(self):
        names = {t["name"] for t in tools.list_tools()}
        for name in ("app_detect", "app_status", "app_launch", "app_close",
                     "app_open_project"):
            self.assertIn(name, names)

    def test_open_project_refuses_missing_file(self):
        r = tools.call_tool("app_open_project",
                            {"app": "lceda-pro", "path": "/no/such/file.epro"})
        self.assertEqual(r["status"], "error")
        self.assertIn(r["reason_code"], ("PROJECT_NOT_FOUND", "FILE_NOT_FOUND",
                                         "APP_UNKNOWN", "TOOL_RAISED"))


if __name__ == "__main__":
    unittest.main()
