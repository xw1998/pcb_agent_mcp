"""Background jobs, and the one slow call that uses them today (DRC).

The interesting question is not "does a thread run" - it is what the answers say
while nothing is known yet. Three things must hold, and each has a test that fails
if it stops holding:

  * a running job has NO result. There is no "last known" verdict, no partial one,
    and no empty dict that could be read as "no violations";
  * a failure is kept, not raised at whoever polls second, and not turned into a
    success;
  * cancelling discards the result without pretending to have killed the process,
    and an unknown id is JOB_NOT_FOUND rather than an empty answer.

`kicad-cli` is replaced by a stand-in `_run`, because the point here is the job
plumbing and the answer's shape; the real engine has its own tests
(test_fidelity) that skip when KiCad is absent.
"""

import json
import os
import tempfile
import time
import unittest

from pcb_agent_mcp import jobs as J
from pcb_agent_mcp import tools
from pcb_agent_mcp.fidelity import engine

BOARD = "(kicad_pcb (version 20240108) (generator \"pcbnew\"))\n"

class JobsBase(unittest.TestCase):
    def setUp(self):
        J.reset()

    def tearDown(self):
        J.reset()

class Submitting(JobsBase):
    def test_a_job_answers_running_and_then_done_with_the_functions_value(self):
        import threading
        gate = threading.Event()
        job = J.submit("test", lambda: (gate.wait(5), {"ok": 1})[1])
        self.assertEqual(job["state"], "running")
        self.assertTrue(job["state_is_as_of_submission"])
        snap = J.snapshot(job["id"])
        self.assertEqual(snap["state"], "running")
        self.assertNotIn("result", snap)
        gate.set()
        got = J.wait(job["id"], timeout_s=5)
        self.assertEqual(got["state"], "done")
        self.assertEqual(got["result"], {"ok": 1})

    def test_a_running_job_offers_no_result_at_all(self):
        release = []
        job = J.submit("test", lambda: (time.sleep(0.3), 7)[1])
        snap = J.snapshot(job["id"])
        self.assertEqual(snap["state"], "running")
        self.assertIsNone(snap.get("result"))
        self.assertIn("never reports a partial", snap["why_there_is_no_result"])
        J.wait(job["id"], timeout_s=5)

    def test_a_failure_is_kept_with_its_type_and_message(self):
        def boom():
            raise RuntimeError("the engine fell over")
        job = J.submit("test", boom)
        got = J.wait(job["id"], timeout_s=5)
        self.assertEqual(got["state"], "failed")
        self.assertEqual(got["error"]["type"], "RuntimeError")
        self.assertIn("the engine fell over", got["error"]["message"])
        self.assertIsNone(got["result"])

    def test_a_cancel_discards_the_result_instead_of_returning_it(self):
        job = J.submit("test", lambda: (time.sleep(0.2), "done anyway")[1])
        J.cancel(job["id"])
        got = J.wait(job["id"], timeout_s=5)
        self.assertEqual(got["state"], "cancelled")
        self.assertIsNone(got["result"])

    def test_an_unknown_id_is_not_found_rather_than_empty(self):
        with self.assertRaises(J.JobError) as ctx:
            J.get("nope-1-abc")
        self.assertEqual(ctx.exception.reason_code, "JOB_NOT_FOUND")
        self.assertIn("in memory", ctx.exception.message)

    def test_wait_returns_running_when_its_own_budget_runs_out(self):
        job = J.submit("test", lambda: (time.sleep(0.6), 1)[1])
        got = J.wait(job["id"], timeout_s=0.05)
        self.assertEqual(got["state"], "running")
        self.assertTrue(got["still_running"])
        J.wait(job["id"], timeout_s=5)

    def test_a_cap_refuses_the_third_of_a_kind_instead_of_queueing_it(self):
        old = dict(J.MAX_RUNNING)
        J.MAX_RUNNING["slow"] = 1
        gate = []
        try:
            first = J.submit("slow", lambda: (gate.append(1), time.sleep(0.4))[1])
            with self.assertRaises(J.JobError) as ctx:
                J.submit("slow", lambda: 2)
            self.assertEqual(ctx.exception.reason_code, "JOB_BUSY")
            J.wait(first["id"], timeout_s=5)
        finally:
            J.MAX_RUNNING.clear()
            J.MAX_RUNNING.update(old)

    def test_the_listing_filters_and_never_shows_a_result_as_a_state(self):
        J.wait(J.submit("a", lambda: 1)["id"], timeout_s=5)
        J.wait(J.submit("b", lambda: 2)["id"], timeout_s=5)
        self.assertEqual(len(J.list_jobs(kind="a")), 1)
        self.assertEqual(len(J.list_jobs(state="done")), 2)
        self.assertEqual(J.list_jobs(kind="nothing"), [])

class DRCAsAJob(JobsBase):
    def setUp(self):
        JobsBase.setUp(self)
        self.dir = tempfile.mkdtemp(prefix="pcb-drc-")
        self.board = os.path.join(self.dir, "b.kicad_pcb")
        with open(self.board, "w", encoding="utf-8") as fh:
            fh.write(BOARD)
        self.saved = (engine.find_kicad_cli, engine._run)
        engine.find_kicad_cli = lambda: r"D:\fake\kicad-cli.exe"
        self.calls = []
        engine._run = self._fake_run

    def tearDown(self):
        engine.find_kicad_cli, engine._run = self.saved
        for name in os.listdir(self.dir):
            os.remove(os.path.join(self.dir, name))
        os.rmdir(self.dir)
        JobsBase.tearDown(self)

    def _fake_run(self, args, cwd, timeout=180):
        self.calls.append({"args": list(args), "timeout": timeout})
        out = args[args.index("-o") + 1]
        with open(out, "w", encoding="utf-8") as fh:
            json.dump({"kicad_version": "10.0.4",
                       "violations": [{"severity": "error", "type": "clearance"}],
                       "unconnected_items": []}, fh)
        return {"ok": True, "returncode": 0, "stdout": "", "stderr": ""}

    def test_the_default_drc_budget_is_the_measured_one_not_a_guess(self):
        self.assertEqual(engine.DRC_TIMEOUT_S, 900)
        engine.drc(self.board)
        self.assertEqual(self.calls[-1]["timeout"], 900)

    def test_background_answers_with_an_id_and_admits_there_is_no_verdict_yet(self):
        r = tools.call_tool("board_drc", {"path": self.board, "background": True})
        self.assertEqual(r["status"], "ok", r)
        self.assertIn("NO RESULT YET", r["precision"])
        jid = r["value"]["job"]
        self.assertEqual(r["value"]["state"], "running")
        self.assertNotIn("violations_total", r["value"])
        done = tools.call_tool("board_job", {"action": "wait", "id": jid,
                                             "timeout_s": 15})
        self.assertEqual(done["status"], "ok", done)
        job = done["value"]
        self.assertEqual(job["state"], "done")
        self.assertEqual(job["result"]["violations_total"], 1)
        self.assertTrue(job["result"]["authoritative"])
        self.assertEqual(job["result"]["engine"]["version"], "10.0.4")

    def test_a_foreground_call_still_answers_directly_and_is_the_same_verdict(self):
        r = tools.call_tool("board_drc", {"path": self.board})
        self.assertEqual(r["status"], "ok", r)
        self.assertEqual(r["violations_total"], 1)
        self.assertIn("blocking_if_this_were_an_edit", r)

    def test_an_engine_that_produces_no_report_fails_the_job_it_does_not_pass_it(self):
        def nothing(args, cwd, timeout=180):
            self.calls.append({"args": list(args), "timeout": timeout})
            return {"ok": False, "error": "TIMEOUT", "timeout_s": timeout}
        engine._run = nothing
        r = tools.call_tool("board_drc", {"path": self.board, "background": True,
                                          "timeout_s": 5})
        job = tools.call_tool("board_job", {"action": "wait",
                                            "id": r["value"]["job"],
                                            "timeout_s": 15})["value"]
        self.assertEqual(job["state"], "done")
        self.assertEqual(job["result"]["reason_code"], "ENGINE_FAILED")
        self.assertEqual(self.calls[-1]["timeout"], 5)

    def test_the_id_is_refused_when_it_is_unknown_and_the_action_when_it_is_not_real(self):
        r = tools.call_tool("board_job", {"action": "get", "id": "drc-9-zzz"})
        self.assertEqual(r["reason_code"], "JOB_NOT_FOUND")
        r2 = tools.call_tool("board_job", {"action": "fly"})
        self.assertEqual(r2["reason_code"], "ARG_INVALID")
        self.assertIn("list, get, wait, cancel", r2["hint"])

    def test_a_running_job_polled_too_early_says_running_and_nothing_more(self):
        slow = []

        def slow_run(args, cwd, timeout=180):
            slow.append(1)
            time.sleep(0.6)
            return self._fake_run(args, cwd, timeout)
        engine._run = slow_run
        r = tools.call_tool("board_drc", {"path": self.board, "background": True})
        early = tools.call_tool("board_job", {"action": "wait",
                                              "id": r["value"]["job"],
                                              "timeout_s": 0.05})
        self.assertIn("STILL RUNNING", early["precision"])
        self.assertTrue(early["value"]["still_running"])
        self.assertNotIn("result", early["value"])
        tools.call_tool("board_job", {"action": "wait", "id": r["value"]["job"],
                                      "timeout_s": 15})

    def test_a_named_project_is_what_the_verdict_is_read_against(self):
        """Same bytes, two verdicts - the project is the difference.

        Without a project KiCad falls back to its own rule minimums, so the answer
        has to say so (`rule_source`). With one, the answer must say `project`.
        The reverse control is the next test: a path that is not there is refused
        rather than quietly replaced by the defaults.
        """
        bare = tools.call_tool("board_drc", {"path": self.board})
        self.assertEqual(bare["status"], "ok", bare)
        self.assertEqual(bare["rule_source"], "kicad_defaults")
        pro = os.path.join(self.dir, "b.kicad_pro")
        with open(pro, "w", encoding="utf-8") as fh:
            fh.write("{}")
        told = tools.call_tool("board_drc", {"path": self.board, "project": pro})
        self.assertEqual(told["status"], "ok", told)
        self.assertEqual(told["rule_source"], "project")
        self.assertIsNone(told["rule_source_note"])

    def test_a_project_that_is_not_there_is_refused_not_silently_swapped(self):
        missing = os.path.join(self.dir, "nope.kicad_pro")
        r = tools.call_tool("board_drc", {"path": self.board, "project": missing})
        self.assertEqual(r["status"], "error", r)
        self.assertEqual(r["reason_code"], "FILE_NOT_FOUND")
        self.assertIn("silently", r["message"])
        self.assertEqual(self.calls, [])

    def test_the_job_tool_is_registered_and_documented(self):
        caps = tools.call_tool("calc_capabilities", {})
        self.assertIn("board_job", caps["available"])
        blob = tools.TOOLS["board_job"]["description"]
        for word in ("running", "JOB_NOT_FOUND", "in memory"):
            self.assertIn(word, blob)

if __name__ == "__main__":
    unittest.main()
