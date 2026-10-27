"""Tests for the desktop control layer.

These tests are deliberately conservative: they never close an EDA window the
user may have open, and they never open a project. What they do pin down is the
part that most often lies in this kind of tool:

  * a refused request says *why* and does not create files,
  * "no project in the title" is empty, not a guess,
  * a running process is identified by its image path, not by a hardcoded path,
  * start/stop are observable through the OS (verified with a throwaway process).
"""
import os
import sys
import tempfile
import time
import unittest

sys.path.insert(0, os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "src"))

from pcb_agent_mcp import desktop  # noqa: E402

WINDOWS = desktop.IS_WINDOWS


@unittest.skipUnless(WINDOWS, "desktop layer is Windows-only")
class TestDetect(unittest.TestCase):
    def test_app_table_shape(self):
        for name, spec in desktop.APPS.items():
            for key in ("display", "exe_names", "project_exts", "open_argv"):
                self.assertIn(key, spec, name)

    def test_detect_returns_evidence_even_when_missing(self):
        r = desktop.detect("lceda-pro")
        self.assertIn("found", r)
        self.assertIn("tried", r)
        self.assertIsInstance(r["tried"], list)
        if r["found"]:
            self.assertTrue(os.path.isfile(r["exe"]))
            self.assertIn(r["source"],
                          ("explicit path", "running process", "drive scan", "registry"))

    def test_detect_all_covers_every_app(self):
        r = desktop.detect_all()
        self.assertEqual(sorted(r), sorted(desktop.APPS))

    def test_detect_unknown_app_raises(self):
        with self.assertRaises(desktop.DesktopError):
            desktop.detect("definitely-not-an-eda")

    def test_explicit_path_wins(self):
        exe = sys.executable
        r = desktop.detect("kicad-cli", explicit=exe)
        # explicit path is returned even if the exe name does not match, and the
        # source says so - the caller can decide whether to trust it
        self.assertEqual(r["source"], "explicit path")
        self.assertEqual(r["exe"], exe)


@unittest.skipUnless(WINDOWS, "desktop layer is Windows-only")
class TestProcessFacts(unittest.TestCase):
    def test_own_pid_is_visible_and_alive(self):
        rows = desktop.list_processes()
        pids = [r[0] if isinstance(r, (tuple, list)) else r for r in rows]
        self.assertIn(os.getpid(), pids)
        self.assertTrue(desktop.process_alive(os.getpid()))

    def test_process_path_of_self(self):
        path = desktop.process_path(os.getpid())
        self.assertTrue(path and path.lower().endswith(".exe"), path)

    def test_bogus_pid_is_not_alive(self):
        self.assertFalse(desktop.process_alive(0xFFFFFF))

    def test_tcp_listener_table_readable(self):
        listeners = desktop.tcp_listeners()
        self.assertIsInstance(listeners, list)
        for row in listeners:
            self.assertEqual(len(row), 2)
        # port 0 is never a listener
        self.assertFalse(desktop.port_listening(0))


@unittest.skipUnless(WINDOWS, "desktop layer is Windows-only")
class TestStatusAndClose(unittest.TestCase):
    def test_status_on_installed_app_is_well_formed(self):
        r = desktop.status("lceda-pro", probe_ports=False)
        self.assertEqual(r["status"], "ok")
        for key in ("running", "instances", "projects_open", "trust"):
            self.assertIn(key, r)
        self.assertEqual(r["running"], bool(r["instances"]))

    def test_close_on_not_running_is_a_no_op(self):
        # pretend the app is not running: pick an app that is not in the table's
        # real processes by asking for a status first and skipping if it is open
        st = desktop.status("kicad", probe_ports=False)
        if st["running"]:
            self.skipTest("KiCad is currently open; refusing to close a user window")
        r = desktop.close("kicad")
        self.assertEqual(r["status"], "ok")
        self.assertFalse(r["was_running"])

    def test_close_unknown_app_raises(self):
        with self.assertRaises(desktop.DesktopError):
            desktop.close("nope-not-an-app")


@unittest.skipUnless(WINDOWS, "desktop layer is Windows-only")
class TestLaunchAndStop(unittest.TestCase):
    def test_launch_detached_and_it_exits(self):
        # a throwaway interpreter, not an EDA: safe to start and stop
        r = desktop.launch(sys.executable, ["-c", "import time; time.sleep(30)"])
        self.assertEqual(r["status"], "ok")
        pid = r["pid"]
        self.assertTrue(desktop.process_alive(pid))
        try:
            self.assertIn("breakaway", r)
            # the recorded flags must match whether breakaway survived
            self.assertIn("0x", r["creationflags"])
        finally:
            if desktop.process_alive(pid):
                import ctypes
                PROCESS_TERMINATE = 0x0001
                handle = ctypes.windll.kernel32.OpenProcess(PROCESS_TERMINATE, False, pid)
                if handle:
                    ctypes.windll.kernel32.TerminateProcess(handle, 1)
                    ctypes.windll.kernel32.CloseHandle(handle)
            deadline = time.time() + 5
            while time.time() < deadline and desktop.process_alive(pid):
                time.sleep(0.1)
            self.assertFalse(desktop.process_alive(pid))


class TestTitleParsing(unittest.TestCase):
    def test_lceda_title(self):
        self.assertEqual(
            desktop.parse_project_from_title("lceda-pro", "主控板 - 嘉立创EDA(专业版)"),
            "主控板")

    def test_lceda_no_project_title_is_empty(self):
        self.assertEqual(desktop.parse_project_from_title("lceda-pro", "嘉立创EDA(专业版)"), "")

    def test_kicad_extension_and_bare_forms(self):
        self.assertEqual(
            desktop.parse_project_from_title("kicad", "demo.kicad_pro - KiCad 10.0"),
            "demo.kicad_pro")
        self.assertEqual(desktop.parse_project_from_title("kicad", "demo - KiCad"), "demo")

    def test_kicad_application_name_only_is_not_a_project(self):
        for title in ("KiCad", "KiCad E.D.A. 10.0"):
            self.assertEqual(desktop.parse_project_from_title("kicad", title), "")

    def test_empty_title(self):
        self.assertEqual(desktop.parse_project_from_title("pcbnew", ""), "")


@unittest.skipUnless(WINDOWS, "desktop layer is Windows-only")
class TestOpenProjectRefusals(unittest.TestCase):
    def test_missing_project_refused_and_not_created(self):
        tmp = tempfile.mkdtemp()
        target = os.path.join(tmp, "does-not-exist.epro")
        r = desktop.open_project("lceda-pro", target)
        self.assertEqual(r["status"], "error")
        self.assertEqual(r["reason_code"], "PROJECT_NOT_FOUND")
        self.assertIn("never creates", r["message"])
        self.assertFalse(os.path.exists(target))

    def test_wrong_extension_refused(self):
        fd, path = tempfile.mkstemp(suffix=".txt")
        os.close(fd)
        try:
            r = desktop.open_project("lceda-pro", path)
            self.assertEqual(r["status"], "error")
            self.assertEqual(r["reason_code"], "EXT_MISMATCH")
        finally:
            os.unlink(path)

    def test_pcb_file_goes_to_pcbnew_not_kicad_manager(self):
        fd, path = tempfile.mkstemp(suffix=".kicad_pcb")
        os.close(fd)
        try:
            self.assertEqual(desktop.open_project("kicad", path)["reason_code"],
                             "EXT_MISMATCH")
        finally:
            os.unlink(path)


if __name__ == "__main__":
    unittest.main()
