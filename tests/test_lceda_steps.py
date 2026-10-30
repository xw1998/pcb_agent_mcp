"""Recording a run and replaying it: the comparison is the feature, not the running.

Nothing here contacts LCEDA Pro. The client is a fake that answers with whatever a
test tells it to, which is the only way to ask the question that matters - "what
does a replay say when a value comes back the same, different, or not at all".

What the tests hold the module to:

  * a step without code is refused, and nothing is substituted for it;
  * a step recorded with a value becomes comparable; a step recorded without one
    reports `uncomparable`, never "matched" (null must not equal a missing value);
  * a differing value is a `differs` row with both values, not an exception and not
    a silent pass;
  * a failing step stops the replay there and names what was not run;
  * the stop file interrupts between steps;
  * a saved script reloads and its sha256 is recomputed and checked, and an edited
    script says so instead of pretending to be the recorded one;
  * nothing is written outside the server's own directory unless a path is given.
"""

import io
import json
import os
import tempfile
import unittest

from pcb_agent_mcp import lceda_steps as S
from pcb_agent_mcp import tools

STEPS = [
    {"label": "select U1", "code": "return eda.dmt_Pcb.select('U1');",
     "value": {"selected": 1}},
    {"label": "move U1", "code": "return await eda.pcb_Document.moveComponent('U1', 5, 0);",
     "value": {"x": 15, "y": 20}},
    {"label": "save", "code": "return await eda.pcb_Document.save();", "value": True},
]

class FakeClient(object):
    """Answers each invoke with the next scripted reply; records what it was asked."""

    def __init__(self, values=None, error_at=None):
        self.values = list(values) if values is not None else []
        self.error_at = error_at
        self.seen = []

    def invoke(self, session, snippet, fn="run", args=None, timeout_ms=None):
        self.seen.append((session, snippet, fn, args))
        index = len(self.seen) - 1
        if self.error_at is not None and index == self.error_at:
            raise _boom()
        value = self.values[index] if index < len(self.values) else None
        return {"value": value, "logs": [], "duration_ms": 3}

class _boom(Exception):
    code = "EXECUTION_ERROR"

    def __init__(self):
        Exception.__init__(self, "the app threw")
        self.message = "the app threw: TypeError"

class Recording(unittest.TestCase):
    def test_a_run_becomes_a_script_that_keeps_the_values_it_returned(self):
        script = S.make_script(STEPS, note="first light", session="sess-1")
        self.assertEqual(script["format"], S.FORMAT)
        self.assertEqual(len(script["steps"]), 3)
        self.assertEqual(script["recorded_values"], 3)
        self.assertEqual(script["steps_without_a_recorded_value"], 0)
        self.assertEqual(script["steps"][1]["expected"], {"x": 15, "y": 20})
        self.assertEqual(script["note"], "first light")
        self.assertEqual(len(script["sha256"]), 64)

    def test_a_step_without_a_value_is_kept_but_marked_uncomparable(self):
        script = S.make_script([{"label": "a", "code": "return 1;"}])
        self.assertEqual(script["recorded_values"], 0)
        self.assertEqual(script["steps_without_a_recorded_value"], 1)
        self.assertNotIn("expected", script["steps"][0])

    def test_a_step_with_nothing_to_run_is_refused_rather_than_filled_in(self):
        with self.assertRaises(S.StepError) as ctx:
            S.make_script([{"label": "a", "code": "   "}])
        self.assertEqual(ctx.exception.reason_code, "ARG_INVALID")
        self.assertIn("nothing to run", ctx.exception.hint)

    def test_an_empty_run_is_not_a_script(self):
        with self.assertRaises(S.StepError) as ctx:
            S.make_script([])
        self.assertEqual(ctx.exception.reason_code, "ARG_INVALID")

    def test_the_script_never_claims_idempotence(self):
        script = S.make_script(STEPS)
        self.assertEqual(script["idempotent"], "unknown")
        self.assertIn("creates two things",
                      script["why_idempotence_is_unknown"])

    def test_the_same_steps_produce_the_same_sha256(self):
        a = S.make_script(STEPS)["sha256"]
        b = S.make_script(STEPS)["sha256"]
        self.assertEqual(a, b)
        c = S.make_script(STEPS[:2])["sha256"]
        self.assertNotEqual(a, c)

class Storing(unittest.TestCase):
    def setUp(self):
        self.dir = tempfile.mkdtemp(prefix="pcb-steps-")
        self.path = os.path.join(self.dir, "run.json")

    def tearDown(self):
        for name in os.listdir(self.dir):
            os.remove(os.path.join(self.dir, name))   # moves to the recycle bin
        os.rmdir(self.dir)

    def test_a_script_round_trips_and_its_digest_is_recomputed(self):
        script = S.make_script(STEPS)
        got = S.save(script, path=self.path)
        self.assertEqual(got["sha256"], script["sha256"])
        back = S.load(self.path)
        self.assertTrue(back["sha256_matches"])
        self.assertEqual(back["sha256_recomputed"], script["sha256"])
        self.assertEqual(len(back["steps"]), 3)

    def test_an_edited_script_says_it_is_not_the_one_that_was_recorded(self):
        script = S.make_script(STEPS)
        S.save(script, path=self.path)
        with io.open(self.path, "r", encoding="utf-8") as fh:
            blob = json.load(fh)
        blob["steps"][1]["code"] = "return 1;"
        with io.open(self.path, "w", encoding="utf-8", newline="\n") as fh:
            fh.write(json.dumps(blob, indent=1))
        back = S.load(self.path)
        self.assertFalse(back["sha256_matches"])
        self.assertIn("edited", back["why_it_can_differ"])

    def test_writing_over_an_existing_script_needs_permission(self):
        script = S.make_script(STEPS)
        S.save(script, path=self.path)
        with self.assertRaises(S.StepError) as ctx:
            S.save(script, path=self.path)
        self.assertEqual(ctx.exception.reason_code, "FILE_EXISTS")
        S.save(script, path=self.path, overwrite=True)   # allowed when asked

    def test_a_file_that_is_not_a_script_is_refused_by_its_format(self):
        with io.open(self.path, "w", encoding="utf-8", newline="\n") as fh:
            fh.write('{"hello": 1}\n')
        with self.assertRaises(S.StepError) as ctx:
            S.load(self.path)
        self.assertEqual(ctx.exception.reason_code, "FORMAT_UNKNOWN")

    def test_the_default_location_is_the_servers_own_directory_not_a_project(self):
        home = S.home()
        self.assertIn(os.path.join(".pcb_agent_mcp", "step-scripts"), home)
        self.assertNotIn("LCEDA-Pro", home)

class Replaying(unittest.TestCase):
    def test_a_replay_that_returns_the_recorded_values_is_reported_as_reproduced(self):
        script = S.make_script(STEPS)
        client = FakeClient([{"selected": 1}, {"x": 15, "y": 20}, True])
        rep = S.replay(client, "sess-1", script)
        self.assertEqual(rep["status"], "reproduced")
        self.assertEqual(rep["counts"]["match"], 3)
        self.assertEqual(rep["counts"]["differs"], 0)
        self.assertEqual([r["label"] for r in rep["rows"]],
                         ["select U1", "move U1", "save"])

    def test_an_integer_that_comes_back_as_a_float_is_the_same_value(self):
        script = S.make_script([{"code": "return 1;", "value": 1}])
        rep = S.replay(FakeClient([1.0]), "sess-1", script)
        self.assertEqual(rep["counts"]["match"], 1)

    def test_a_different_value_is_a_measurement_with_both_values_not_an_error(self):
        script = S.make_script(STEPS)
        client = FakeClient([{"selected": 1}, {"x": 900, "y": 20}, True])
        rep = S.replay(client, "sess-1", script)
        self.assertEqual(rep["status"], "differs")
        self.assertEqual(rep["counts"]["differs"], 1)
        row = [r for r in rep["rows"] if r["outcome"] == S.DIFFERS][0]
        self.assertEqual(row["expected"], {"x": 15, "y": 20})
        self.assertEqual(row["value"], {"x": 900, "y": 20})
        self.assertIn("measurement, not a failure", row["why"])
        # the replay still ran to the end: a difference is not a reason to stop
        self.assertEqual(rep["steps_run"], 3)

    def test_a_step_without_a_recorded_value_is_not_counted_as_a_pass(self):
        script = S.make_script([{"code": "return 1;"}])
        rep = S.replay(FakeClient([None]), "sess-1", script)
        self.assertEqual(rep["counts"]["no_recorded_value"], 1)
        self.assertEqual(rep["counts"]["match"], 0)
        self.assertEqual(rep["status"], "inconclusive")
        self.assertIsNone(rep["rows"][0]["matches_recording"])
        self.assertIn("not a pass", rep["rows"][0]["why"])

    def test_a_failing_step_stops_the_replay_and_names_what_was_not_run(self):
        script = S.make_script(STEPS)
        rep = S.replay(FakeClient([{"selected": 1}], error_at=1), "sess-1", script)
        self.assertEqual(rep["failed_at_step"], 1)
        self.assertEqual(rep["steps_run"], 1)
        self.assertEqual(rep["rows"][1]["outcome"], S.FAILED)
        self.assertEqual(rep["rows"][1]["error_code"], "EXECUTION_ERROR")
        self.assertEqual(rep["not_run"], ["save"])
        self.assertEqual(rep["status"], "differs")

    def test_the_stop_file_interrupts_between_steps(self):
        script = S.make_script(STEPS)
        asked = []

        def stop_now():
            asked.append(1)
            return len(asked) > 1          # false before step 0, true before step 1

        rep = S.replay(FakeClient([{"selected": 1}] * 3), "sess-1", script,
                       should_stop=stop_now)
        self.assertEqual(rep["stopped_before_step"], 1)
        self.assertEqual(rep["steps_run"], 1)
        self.assertEqual(rep["not_run"], ["move U1", "save"])

    def test_compare_off_runs_everything_and_claims_nothing(self):
        script = S.make_script(STEPS)
        rep = S.replay(FakeClient([{"selected": 1}, {"x": 15, "y": 20}, True]),
                       "sess-1", script, compare=False)
        self.assertEqual(rep["counts"]["no_recorded_value"], 3)
        self.assertEqual(rep["status"], "inconclusive")
        self.assertTrue(all("compare was switched off" in r["why"]
                            for r in rep["rows"]))

    def test_a_replay_without_a_session_is_refused_before_it_runs_anything(self):
        client = FakeClient([])
        with self.assertRaises(S.StepError) as ctx:
            S.replay(client, "", S.make_script(STEPS))
        self.assertEqual(ctx.exception.reason_code, "ARG_REQUIRED")
        self.assertEqual(client.seen, [])

class Wiring(unittest.TestCase):
    def test_the_recorder_runs_nothing_and_writes_nothing_by_default(self):
        r = tools.call_tool("lceda_step_record", {"steps": STEPS})
        self.assertEqual(r["status"], "ok", r)
        self.assertIsNone(r["value"]["saved"])
        self.assertEqual(r["value"]["steps"], 3)
        self.assertEqual(r["assumptions"]["idempotent"], "unknown")
        self.assertIn("never", r["assumptions"]["never_beside_a_project"])

    def test_the_recorder_refuses_a_step_with_no_code(self):
        r = tools.call_tool("lceda_step_record",
                            {"steps": [{"label": "empty", "code": ""}]})
        self.assertEqual(r["status"], "error")
        self.assertEqual(r["reason_code"], "ARG_INVALID")

    def test_a_replay_without_a_session_or_script_is_refused_with_a_reason(self):
        r = tools.call_tool("lceda_step_replay", {"session": "sess-1"})
        self.assertEqual(r["status"], "error")
        self.assertEqual(r["reason_code"], "ARG_MISSING")
        self.assertIn("recorded", r["hint"])
        r2 = tools.call_tool("lceda_step_replay", {"path": "no-such-script.json"})
        self.assertEqual(r2["reason_code"], "ARG_MISSING")

    def test_the_tools_are_registered_and_the_replay_verdict_is_not_named_status(self):
        caps = tools.call_tool("calc_capabilities", {})
        self.assertIn("lceda_step_record", caps["available"])
        self.assertIn("lceda_step_replay", caps["available"])
        for name in ("lceda_step_record", "lceda_step_replay"):
            self.assertIn(name, tools.TOOLS)
        blob = tools.TOOLS["lceda_step_replay"]["description"]
        self.assertIn("uncomparable", blob)
        self.assertIn("unknown", blob)

if __name__ == "__main__":
    unittest.main()
