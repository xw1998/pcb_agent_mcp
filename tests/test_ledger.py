"""Tests for `ledger` and the `board_run_ledger` tool.

The claims the module makes in its own docstring, one test each:

* **it never changes an answer** - recording swallows every error, and a ledger
  that cannot be written must not fail the tool that was asked to do real work;
* **it is a record, not a cache** - entries come back newest first and are never
  fed into a computation;
* **it holds no design data** - a digest, the arguments that change behaviour,
  the verdict;
* **default on, one switch off** - `PCB_AGENT_LEDGER=0` writes nothing at all.

Every test points `PCB_AGENT_HOME` at a temporary directory, so nothing here can
touch the real home.
"""
import json
import os
import shutil
import tempfile
import unittest

from pcb_agent_mcp import ledger
from pcb_agent_mcp import tools

class LedgerBase(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.mkdtemp(prefix="pcbledger-")
        self._home = os.environ.get("PCB_AGENT_HOME")
        self._off = os.environ.pop("PCB_AGENT_LEDGER", None)
        os.environ["PCB_AGENT_HOME"] = os.path.join(self.tmp, "home")
        self.board = os.path.join(self.tmp, "board.kicad_pcb")
        with open(self.board, "wb") as fh:
            fh.write(b"(kicad_pcb (version 20260206))\n")

    def tearDown(self):
        if self._home is None:
            os.environ.pop("PCB_AGENT_HOME", None)
        else:
            os.environ["PCB_AGENT_HOME"] = self._home
        os.environ.pop("PCB_AGENT_LEDGER", None)
        if self._off is not None:
            os.environ["PCB_AGENT_LEDGER"] = self._off
        shutil.rmtree(self.tmp, ignore_errors=True)

    def lines(self):
        with open(ledger.path(), "r", encoding="utf-8") as fh:
            return [json.loads(x) for x in fh if x.strip()]

class TestTheRecord(LedgerBase):
    def test_a_run_is_one_json_line_keyed_by_the_board_digest(self):
        digest = ledger.digest(self.board)
        self.assertTrue(ledger.record("route", {"board": digest, "verdict": "routed",
                                                "nets_routed": 3}))
        rows = self.lines()
        self.assertEqual(len(rows), 1)
        self.assertEqual(rows[0]["kind"], "route")
        self.assertEqual(rows[0]["payload"]["board"], digest)
        self.assertEqual(rows[0]["payload"]["verdict"], "routed")
        self.assertIn("at", rows[0])

    def test_the_digest_is_the_file_not_a_copy_of_it(self):
        first = ledger.digest(self.board)
        with open(self.board, "ab") as fh:
            fh.write(b";\n")
        second = ledger.digest(self.board)
        self.assertNotEqual(first, second)
        self.assertEqual(len(first), 64)

    def test_a_file_that_cannot_be_read_has_no_digest(self):
        self.assertIsNone(ledger.digest(os.path.join(self.tmp, "nope.kicad_pcb")))
        self.assertIsNone(ledger.digest(None))

    def test_an_unknown_kind_is_refused_before_anything_is_written(self):
        with self.assertRaises(ValueError):
            ledger.record("wish", {})
        self.assertFalse(os.path.isfile(ledger.path()))

    def test_entries_are_newest_first_and_limited(self):
        for n in range(3):
            ledger.record("route", {"verdict": "routed", "n": n})
        got = ledger.entries(limit=2)
        self.assertEqual([r["payload"]["n"] for r in got], [2, 1])
        self.assertEqual(len(ledger.entries(limit=0)), 3)

    def test_a_malformed_line_is_skipped_not_fatal(self):
        ledger.record("route", {"verdict": "routed"})
        with open(ledger.path(), "a", encoding="utf-8") as fh:
            fh.write("{not json\n\n")
        ledger.record("fanout", {"verdict": "escaped"})
        kinds = [r["kind"] for r in ledger.entries()]
        self.assertEqual(kinds, ["fanout", "route"])

    def test_filtering_by_kind_and_by_digest(self):
        digest = ledger.digest(self.board)
        ledger.record("route", {"board": digest, "verdict": "routed"})
        ledger.record("fanout", {"board": digest, "verdict": "escaped"})
        ledger.record("route", {"board": "0" * 64, "verdict": "incomplete"})
        self.assertEqual(len(ledger.entries(kind="route")), 2)
        self.assertEqual(len(ledger.entries(digest_value=digest)), 2)
        self.assertEqual(len(ledger.entries(kind="route", digest_value=digest)), 1)

    def test_the_summary_counts_the_verdicts_it_shows(self):
        ledger.record("route", {"verdict": "routed"})
        ledger.record("route", {"verdict": "incomplete"})
        got = ledger.summary()
        self.assertEqual(got["count"], 2)
        self.assertEqual(got["totals"], {"route:routed": 1, "route:incomplete": 1})
        self.assertEqual(got["file"], ledger.path())

    def test_an_empty_ledger_is_an_empty_answer_not_an_error(self):
        self.assertEqual(ledger.entries(), [])
        self.assertEqual(ledger.summary()["count"], 0)

class TestSecondGuessesAreNotAnAnswer(LedgerBase):
    def test_the_switch_off_writes_nothing(self):
        os.environ["PCB_AGENT_LEDGER"] = "0"
        self.assertFalse(ledger.enabled())
        self.assertFalse(ledger.record("route", {"verdict": "routed"}))
        self.assertFalse(os.path.isfile(ledger.path()))

    def test_false_off_and_other_spellings_are_the_same_switch(self):
        for value in ("0", "false", "off", ""):
            os.environ["PCB_AGENT_LEDGER"] = value
            self.assertFalse(ledger.enabled(), value)
        for value in ("1", "true", "on", "yes"):
            os.environ["PCB_AGENT_LEDGER"] = value
            self.assertTrue(ledger.enabled(), value)

    def test_a_home_that_cannot_be_created_is_not_an_exception(self):
        # A FILE where the ledger's directory would have to be: makedirs fails.
        blocker = os.path.join(self.tmp, "blocked")
        with open(blocker, "wb") as fh:
            fh.write(b"x")
        os.environ["PCB_AGENT_HOME"] = blocker
        self.assertFalse(ledger.record("route", {"verdict": "routed"}))
        self.assertEqual(ledger.entries(), [])

    def test_the_ledger_rotates_instead_of_growing_for_ever(self):
        ledger.MAX_BYTES, keep = 200, ledger.MAX_BYTES
        try:
            for n in range(40):
                ledger.record("route", {"verdict": "routed", "n": n,
                                        "padding": "x" * 40})
            self.assertTrue(os.path.isfile(ledger.path()))
            self.assertTrue(os.path.isfile(ledger.path() + ".1"))
            # The rotation happens when the size is already past the cap, so the
            # live file holds at most the run that tripped it and the ones after.
            self.assertLessEqual(len(self.lines()), 3)
            self.assertGreater(len(self.lines()), 0)
        finally:
            ledger.MAX_BYTES = keep

class TestTheTool(LedgerBase):
    def call(self, **args):
        return tools.call_tool("board_run_ledger", args)

    def test_an_empty_ledger_says_zero_and_where_it_lives(self):
        out = self.call()
        self.assertEqual(out["status"], "ok", out)
        self.assertEqual(out["value"]["count"], 0)
        self.assertEqual(out["value"]["file"], ledger.path())
        self.assertTrue(out["value"]["holds_no_design_data"])

    def test_a_board_path_filters_to_that_board_only(self):
        mine = ledger.digest(self.board)
        ledger.record("route", {"board": mine, "verdict": "routed"})
        ledger.record("route", {"board": "0" * 64, "verdict": "incomplete"})
        out = self.call(path=self.board)
        self.assertEqual(out["value"]["count"], 1)
        self.assertEqual(out["value"]["board_digest"], mine[:12])
        self.assertEqual(out["value"]["runs"][0]["verdict"], "routed")

    def test_an_unknown_kind_is_refused(self):
        out = self.call(kind="wish")
        self.assertEqual(out["status"], "error")
        self.assertEqual(out["reason_code"], "ARG_INVALID")

    def test_an_unreadable_board_path_is_refused_rather_than_unfiltered(self):
        out = self.call(path=os.path.join(self.tmp, "nope.kicad_pcb"))
        self.assertEqual(out["status"], "error")
        self.assertEqual(out["reason_code"], "FILE_UNREADABLE")
        self.assertIn("digest is unknown", out["message"])

class TestWhoRanIt(LedgerBase):
    """A run names its owner, and asking for an owner is not asking for everything."""

    def call(self, **args):
        return tools.call_tool("board_run_ledger", args)

    def test_a_request_thread_has_no_owner(self):
        from pcb_agent_mcp import jobs
        self.assertIsNone(jobs.current())

    def test_work_inside_a_job_records_that_job_as_the_owner(self):
        from pcb_agent_mcp import jobs
        box = {}

        def work():
            box["owner"] = jobs.current()
            ledger.record("route", {"board": ledger.digest(self.board),
                                    "verdict": "routed"}, owner=jobs.current())
            return True

        job = jobs.submit("default", work)
        jobs.wait(job["id"], timeout_s=10)
        self.assertEqual(box["owner"], job["id"])
        got = ledger.summary(owner=job["id"])
        self.assertEqual(got["count"], 1)
        self.assertEqual(got["runs"][0]["owner"], job["id"])
        # and once the thread is done the next caller is nobody's job again
        self.assertIsNone(jobs.current())

    def test_an_owner_filter_does_not_silently_return_everything(self):
        ledger.record("route", {"board": "0" * 64, "verdict": "routed"})
        ledger.record("route", {"board": "0" * 64, "verdict": "routed"},
                      owner="route-1-abc123")
        self.assertEqual(ledger.summary()["count"], 2)
        self.assertEqual(ledger.summary(owner="route-1-abc123")["count"], 1)
        # the negative control: an owner nobody used returns nothing rather than
        # falling back to "all runs"
        self.assertEqual(ledger.summary(owner="route-9-zzzzzz")["count"], 0)

    def test_the_tool_filters_by_owner_and_says_which(self):
        ledger.record("route", {"board": "0" * 64, "verdict": "routed"},
                      owner="route-2-def456")
        out = self.call(owner="route-2-def456")
        self.assertEqual(out["status"], "ok", out)
        self.assertEqual(out["value"]["count"], 1)
        self.assertEqual(out["value"]["owner"], "route-2-def456")
        self.assertIn("route-2-def456", out["value"]["owner_note"])
        other = self.call(owner="route-3-111111")
        self.assertEqual(other["value"]["count"], 0)

    def test_an_empty_owner_is_refused_instead_of_meaning_all(self):
        out = self.call(owner="")
        self.assertEqual(out["status"], "error")
        self.assertEqual(out["reason_code"], "ARG_INVALID")

if __name__ == "__main__":
    unittest.main()
