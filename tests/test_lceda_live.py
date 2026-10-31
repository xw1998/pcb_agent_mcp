"""The command-space bridge: what it does with each answer the app can give.

The live application is exercised by ONE test at the bottom, and it is skipped
unless a JLCEDAPro* pipe is actually open. Everything else runs against fake
transports, because the point of most of these cases is the FAILURE path - a
missing project, an execution error, a pipe that never answers - and a real
editor cannot be made to fail on demand without touching the user's projects.
"""

import io
import json
import os
import unittest

from pcb_agent_mcp import lceda_live as L


class FakeTransport(L.Transport):
    """Records what was asked, replays queued replies. live=False on purpose."""

    live = False

    def __init__(self, replies):
        self.replies = list(replies)
        self.calls = []
        self.endpoint = "FAKE0"

    def call(self, op, payload, timeout_s):
        self.calls.append((op, dict(payload or {}), timeout_s))
        if not self.replies:
            raise AssertionError("unexpected call: %s" % op)
        item = self.replies.pop(0)
        if isinstance(item, L.LiveError):
            raise item
        # The wire-level check lives in the real transport; a fake that skipped
        # it would let an error reply look like a success.
        return L.PipeTransport._check(item)


def _ok(value, **extra):
    reply = {"id": 1, "ok": True, "value": value, "logs": [], "durationMs": 3}
    reply.update(extra)
    return reply


class TheEndpointNameIsDerivedNotGuessed(unittest.TestCase):
    def test_the_name_is_the_stripped_app_name_plus_eight_hex(self):
        name = L.endpoint_name(r"C:\Users\somebody\Documents\LCEDA-Pro")
        self.assertTrue(name.startswith("JLCEDAPro"))
        self.assertEqual(len(name), len("JLCEDAPro") + 8)
        self.assertEqual(name[10:], name[10:].lower())

    def test_the_same_path_always_gives_the_same_name(self):
        a = L.endpoint_name(r"C:\x")
        b = L.endpoint_name(r"C:\x")
        c = L.endpoint_name(r"C:\y")
        self.assertEqual(a, b)
        self.assertNotEqual(a, c)

    def test_enumeration_never_raises_and_returns_a_list(self):
        names = L.available_endpoints()
        self.assertIsInstance(names, list)
        for name in names:
            self.assertTrue(name.startswith(L.APP_NAME))


class NoEditorMeansNoAnswer(unittest.TestCase):
    def test_connect_without_any_pipe_says_app_not_running(self):
        original = L.available_endpoints
        L.available_endpoints = lambda prefix=L.APP_NAME: []
        try:
            with self.assertRaises(L.LiveError) as caught:
                L.connect()
        finally:
            L.available_endpoints = original
        self.assertEqual(caught.exception.code, "APP_NOT_RUNNING")
        self.assertEqual(caught.exception.extra["pipes_checked"], 0)

    def test_a_pipe_that_does_not_ping_is_not_accepted(self):
        original = L.available_endpoints
        L.available_endpoints = lambda prefix=L.APP_NAME: ["JLCEDAProdeadbeef"]
        original_cls = L.PipeTransport

        class DeadTransport(original_cls):
            live = True

            def __init__(self, endpoint, open_path=None):
                original_cls.__init__(self, endpoint, open_path)
                self.endpoint = endpoint

            def call(self, op, payload, timeout_s):
                raise L.LiveError("APP_TIMEOUT", "no reply")

        L.PipeTransport = DeadTransport
        try:
            with self.assertRaises(L.LiveError) as caught:
                L.connect()
        finally:
            L.PipeTransport = original_cls
            L.available_endpoints = original
        self.assertEqual(caught.exception.code, "APP_NOT_RUNNING")
        self.assertEqual(len(caught.exception.extra["failures"]), 1)


class TheClientPassesThroughWhatTheAppSaid(unittest.TestCase):
    def test_ping_keeps_the_identity_and_marks_the_transport_as_not_live(self):
        client = L.Client(FakeTransport([_ok({"endpoint": "FAKE0", "bridgeVersion": "4.1.60"})]))
        identity = client.ping()
        self.assertEqual(identity["bridgeVersion"], "4.1.60")
        self.assertFalse(client.verified)

    def test_open_sends_an_absolute_path_and_the_headless_flag(self):
        transport = FakeTransport([_ok({"sessionId": "s1", "headless": True})])
        client = L.Client(transport)
        here = os.path.abspath(__file__)
        client.open_project(here, headless=True)
        op, payload, _ = transport.calls[0]
        self.assertEqual(op, "open")
        self.assertEqual(payload["path"], here)
        self.assertTrue(payload["headless"])

    def test_a_missing_path_is_refused_before_anything_is_sent(self):
        transport = FakeTransport([])
        client = L.Client(transport)
        with self.assertRaises(L.LiveError) as caught:
            client.open_project("")
        self.assertEqual(caught.exception.code, "ARG_REQUIRED")
        with self.assertRaises(L.LiveError) as caught:
            client.open_project(os.path.join(os.path.dirname(__file__), "does-not-exist.eprj"))
        self.assertEqual(caught.exception.code, "ARG_INVALID")
        self.assertEqual(transport.calls, [])

    def test_invoke_requires_a_session_and_code(self):
        client = L.Client(FakeTransport([]))
        for kwargs in ({"session": "", "code": "return 1"}, {"session": "s1", "code": "  "}):
            with self.assertRaises(L.LiveError) as caught:
                client.invoke(**kwargs)
            self.assertEqual(caught.exception.code, "ARG_REQUIRED")

    def test_invoke_reports_value_logs_and_duration(self):
        transport = FakeTransport(
            [{"id": 1, "ok": True, "value": [1, 2], "logs": ["hi"], "durationMs": 7}]
        )
        client = L.Client(transport)
        out = client.invoke("s1", "return [1,2];")
        self.assertEqual(out["value"], [1, 2])
        self.assertEqual(out["logs"], ["hi"])
        self.assertEqual(out["duration_ms"], 7)
        self.assertEqual(transport.calls[0][1]["fn"], "run")

    def test_the_api_query_only_sends_the_levels_that_were_asked_for(self):
        transport = FakeTransport([_ok({"title": "x"}), _ok({"title": "y"})])
        client = L.Client(transport)
        client.api("eda")
        client.api("eda", class_name="PCB_Document")
        self.assertNotIn("className", transport.calls[0][1])
        self.assertEqual(transport.calls[1][1]["className"], "PCB_Document")
        self.assertNotIn("methodName", transport.calls[1][1])

    def test_the_invoke_timeout_is_capped_at_the_published_budget(self):
        transport = FakeTransport([{"id": 1, "ok": True, "value": None, "logs": []}])
        client = L.Client(transport)
        client.invoke("s1", "return 1;", timeout_ms=10 ** 7)
        self.assertLessEqual(transport.calls[0][2], L.MAX_TIMEOUT_MS / 1000.0 + 5.0)


class ErrorMappingKeepsTheVendorCode(unittest.TestCase):
    def _error(self, code, message="nope"):
        return {"id": 1, "ok": False, "error": {"code": code, "message": message}, "logs": []}

    def test_a_known_code_survives_as_the_reason_code(self):
        transport = FakeTransport([self._error("EXECUTION_ERROR", "boom")])
        client = L.Client(transport)
        with self.assertRaises(L.LiveError) as caught:
            client.invoke("s1", "throw new Error('boom')")
        self.assertEqual(caught.exception.code, "EXECUTION_ERROR")
        self.assertEqual(caught.exception.message, "boom")
        self.assertEqual(caught.exception.extra["app_code"], "EXECUTION_ERROR")

    def test_an_unknown_code_is_not_invented_as_ours(self):
        transport = FakeTransport([self._error("SOMETHING_NEW")])
        client = L.Client(transport)
        with self.assertRaises(L.LiveError) as caught:
            client.sessions()
        self.assertEqual(caught.exception.code, "APP_ERROR")
        self.assertEqual(caught.exception.extra["app_code"], "SOMETHING_NEW")

    def test_an_error_without_a_code_is_an_app_error_not_a_crash(self):
        transport = FakeTransport([{"id": 1, "ok": False, "error": {}}])
        client = L.Client(transport)
        with self.assertRaises(L.LiveError) as caught:
            client.sessions()
        self.assertEqual(caught.exception.code, "APP_ERROR")

    def test_the_published_vendor_codes_are_all_passed_through(self):
        for code in sorted(L.PASS_THROUGH_CODES):
            with self.assertRaises(L.LiveError) as caught:
                L.PipeTransport._check(self._error(code))
            self.assertEqual(caught.exception.code, code)


class TheFrameLayer(unittest.TestCase):
    class _FakePipe(object):
        def __init__(self, replies):
            self.replies = list(replies)
            self.written = []
            self.closed = False

        def write(self, data):
            self.written.append(data)

        def read(self, n):
            if self.replies:
                return self.replies.pop(0)
            return b""

        def close(self):
            self.closed = True

    def _transport(self, replies):
        transport = L.PipeTransport("FAKE1")
        transport._fh = self._FakePipe(replies)
        return transport

    def test_a_frame_is_one_json_line_with_id_op_and_budget(self):
        transport = self._transport([b'{"id":1,"ok":true,"value":{"a":1}}\n'])
        transport.call("ping", {"x": None, "y": 2}, 5.0)
        line = transport._fh.written[0]
        self.assertTrue(line.endswith(b"\n"))
        frame = json.loads(line.decode("utf-8"))
        self.assertEqual(frame["op"], "ping")
        self.assertEqual(frame["id"], 1)
        self.assertEqual(frame["timeoutMs"], 5000)
        self.assertEqual(frame["y"], 2)
        self.assertNotIn("x", frame)

    def test_a_reply_split_across_chunks_is_reassembled(self):
        transport = self._transport([b'{"id":1,"ok":tr', b'ue,"value":42}\n'])
        reply = transport.call("ping", {}, 5.0)
        self.assertEqual(reply["value"], 42)

    def test_a_reply_without_a_newline_times_out_instead_of_hanging(self):
        transport = self._transport([b'{"id":1,"ok":true'])
        pipe = transport._fh
        with self.assertRaises(L.LiveError) as caught:
            transport.call("ping", {}, 0.05)
        self.assertEqual(caught.exception.code, "APP_TIMEOUT")
        self.assertTrue(pipe.closed)

    def test_a_non_json_reply_is_a_protocol_error(self):
        transport = self._transport([b"not json\n"])
        with self.assertRaises(L.LiveError) as caught:
            transport.call("ping", {}, 1.0)
        self.assertEqual(caught.exception.code, "PROTOCOL_ERROR")

    def test_an_oversized_request_is_refused_before_it_is_written(self):
        transport = self._transport([])
        big = "x" * (L.MAX_REQUEST_BYTES + 10)
        with self.assertRaises(L.LiveError) as caught:
            transport.call("invoke", {"code": big}, 5.0)
        self.assertEqual(caught.exception.code, "FRAME_TOO_LARGE")
        self.assertEqual(transport._fh.written, [])

    def test_an_oversized_reply_is_refused_rather_than_buffered(self):
        transport = self._transport([b"x" * (L.MAX_REPLY_BYTES + 10)])
        with self.assertRaises(L.LiveError) as caught:
            transport.call("ping", {}, 0.5)
        self.assertEqual(caught.exception.code, "RESULT_TOO_LARGE")


class TheToolsSayWhatTheyCanDo(unittest.TestCase):
    """The bridge tools exist, are listed where the model looks first, and the one
    that can write says so in its own schema.

    Seven, not six: `lceda_pro_steps` (the stepped write, so a person can watch
    and interrupt) joined them after the first real board. It is covered by the
    same failure-path tests as the rest, and it is NOT verified against a running
    editor - see docs/26 §26.1.4.
    """

    def test_every_bridge_tool_is_registered_and_listed(self):
        from pcb_agent_mcp import tools as T
        names = [n for n in T.TOOLS if n.startswith("lceda_pro_")]
        self.assertEqual(len(names), 7, names)
        available = T.TOOLS["calc_capabilities"]["handler"]({})["available"]
        for name in names:
            self.assertIn(name, available)

    def test_run_requires_a_session_and_code_and_warns_about_writes(self):
        from pcb_agent_mcp import tools as T
        spec = T.TOOLS["lceda_pro_run"]
        self.assertEqual(sorted(spec["inputSchema"]["required"]), ["code", "session"])
        self.assertIn("can touch a live design", spec["description"])
        # An empty session is refused before anything is sent, so this holds
        # whether or not an editor is running.
        out = T.call_tool("lceda_pro_run", {"session": "", "code": "return 1;"})
        self.assertEqual(out.get("reason_code"), "ARG_REQUIRED")

class FakeEditor(object):
    """Just enough of `lceda_live.Client` for the write path: invoke() + close()."""

    def __init__(self, values):
        self.values = list(values)
        self.calls = []
        self.closed = False

    def invoke(self, session, code, fn="run", args=None, timeout_ms=None):
        self.calls.append({"session": session, "code": code,
                           "timeout_ms": timeout_ms})
        if not self.values:
            raise AssertionError("unexpected invoke: %s" % (code,))
        return {"value": self.values.pop(0), "logs": [], "duration_ms": 2}

    def close(self):
        self.closed = True

class TheWriteIsConfirmedByAProbeNotByGoodIntentions(unittest.TestCase):
    """`lceda_write.run` is the discipline: probe, write, the same probe again.

    Every write claim here is about the snippet's OBSERVED EFFECT. No write has
    been run against the real application in this build, and the report says so on
    every answer rather than only where it is inconvenient.
    """

    def test_a_probe_that_changed_is_the_only_kind_of_success(self):
        from pcb_agent_mcp import lceda_write as W
        client = FakeEditor([0.0, "moved", 1.0])
        res = W.run(client, "s1", "U1.y += 1;", probe="U1.y", label="U1 down 1mm")
        self.assertEqual(res["status"], "ok")
        rep = res["report"]
        self.assertTrue(rep["changed"])
        self.assertEqual((rep["before"], rep["after"]), (0.0, 1.0))
        self.assertEqual(rep["label"], "U1 down 1mm")
        self.assertFalse(rep["verified_against_app"])
        # probe, write, probe - in that order, so the "after" reading cannot be
        # taken before the snippet ran.
        self.assertEqual([c["code"] for c in client.calls],
                         ["U1.y", "U1.y += 1;", "U1.y"])

    def test_a_probe_that_did_not_change_is_a_refusal(self):
        from pcb_agent_mcp import lceda_write as W
        client = FakeEditor([1.0, "no-op", 1.0])
        res = W.run(client, "s1", "U1.y += 1;", probe="U1.y")
        self.assertEqual(res["status"], "error")
        self.assertEqual(res["reason_code"], "WRITE_HAD_NO_EFFECT")
        self.assertEqual(res["report"]["before"], res["report"]["after"])
        self.assertEqual(res["report"]["ran"]["value"], "no-op")

    def test_an_integer_that_comes_back_as_a_float_is_not_a_change(self):
        from pcb_agent_mcp import lceda_write as W
        client = FakeEditor([1, "no-op", 1.0])
        res = W.run(client, "s1", "x()", probe="count")
        self.assertEqual(res["reason_code"], "WRITE_HAD_NO_EFFECT")

    def test_change_without_a_probe_is_refused_before_anything_is_sent(self):
        from pcb_agent_mcp import lceda_write as W
        client = FakeEditor([])
        with self.assertRaises(W.WriteError) as ctx:
            W.run(client, "s1", "U1.y += 1;")
        self.assertEqual(ctx.exception.reason_code, "ARG_REQUIRED")
        self.assertEqual(client.calls, [])

    def test_save_is_confirmed_only_by_no_error_and_says_so(self):
        from pcb_agent_mcp import lceda_write as W
        client = FakeEditor(["saved"])
        res = W.run(client, "s1", "await api.save();", expect="no_error",
                    label="save")
        self.assertEqual(res["status"], "ok")
        rep = res["report"]
        self.assertIsNone(rep["changed"])
        self.assertIn("nothing was read back", rep["verified_by"])
        self.assertIn("that the file on disk changed", rep["not_verified"])

    def test_a_snippet_that_raises_is_the_apps_error_not_a_pass(self):
        from pcb_agent_mcp import lceda_write as W

        class Boom(FakeEditor):
            def invoke(self, session, code, **kw):
                if code == "boom":
                    raise L.LiveError("EXEC_ERROR", "the snippet threw")
                return FakeEditor.invoke(self, session, code, **kw)

        client = Boom([0.0, 1.0])
        with self.assertRaises(L.LiveError):
            W.run(client, "s1", "boom", probe="U1.y")

class TheWriteToolCarriesThatDiscipline(unittest.TestCase):
    """The tool layer must not soften it: no probe, no claim."""

    def setUp(self):
        from pcb_agent_mcp import tools as T
        self.T = T
        self._orig = T._live_client

    def tearDown(self):
        self.T._live_client = self._orig

    def _stub(self, values):
        client = FakeEditor(values)
        self.T._live_client = lambda args: client
        return client

    def test_a_read_back_change_is_reported_as_observed_not_as_verified(self):
        client = self._stub([0.0, "moved", 1.0])
        out = self.T.call_tool("lceda_pcb_write", {
            "session": "s", "code": "move()", "probe": "U1.y", "op": "move",
            "label": "U1 down 1mm"})
        self.assertEqual(out["status"], "ok", out)
        self.assertTrue(out["value"]["changed"])
        self.assertFalse(out["assumptions"]["verified_against_app"])
        self.assertTrue(client.closed, "the pipe must be closed again")

    def test_a_no_op_write_is_never_reported_as_success(self):
        self._stub([1.0, "no-op", 1.0])
        out = self.T.call_tool("lceda_pcb_write", {
            "session": "s", "code": "move()", "probe": "U1.y", "op": "move"})
        self.assertEqual(out["status"], "error")
        self.assertEqual(out["reason_code"], "WRITE_HAD_NO_EFFECT")
        self.assertEqual(out["report"]["before"], out["report"]["after"])

    def test_a_write_without_a_probe_is_refused_and_nothing_is_sent(self):
        client = self._stub([])
        out = self.T.call_tool("lceda_pcb_write", {
            "session": "s", "code": "move()", "op": "move"})
        self.assertEqual(out["reason_code"], "ARG_REQUIRED")
        self.assertEqual(client.calls, [])

    def test_an_unknown_op_names_the_ops_it_has(self):
        self._stub([])
        out = self.T.call_tool("lceda_pcb_write", {
            "session": "s", "code": "x()", "op": "teleport"})
        self.assertEqual(out["reason_code"], "ARG_INVALID")
        self.assertIn("move", out["hint"])

    def test_the_tool_says_which_builds_its_write_claim_covers(self):
        spec = self.T.TOOLS["lceda_pcb_write"]
        self.assertEqual(sorted(spec["inputSchema"]["required"]), ["code", "session"])
        # The description must name the gate, not just the flag: a reader has to be
        # able to tell that true means "measured on this bridge", not "supported".
        self.assertIn("VERIFIED_WRITES", spec["description"])
        self.assertIn("verified_against_app", spec["description"])
        self.assertIn("4.1.60.198f38ab", spec["description"])


class TheGateOpensOnlyWhereAMeasurementExists(unittest.TestCase):
    """`verified_against_app` is a table lookup, and every way of missing it is a
    case here: an unlisted build, an op nobody ran there, a probe that did not
    move. Without one of these the flag would be decoration."""

    MEASURED = "4.1.60.198f38ab"

    def _run(self, values, **kw):
        from pcb_agent_mcp import lceda_write as W
        return W.run(FakeEditor(values), "s1", "x()", probe="n", **kw)

    def test_a_measured_build_with_a_moved_probe_opens_it(self):
        res = self._run([0.0, "done", 1.0], op="create",
                        bridge_version=self.MEASURED)
        rep = res["report"]
        self.assertTrue(rep["verified_against_app"])
        self.assertIn("LCEDA Pro V4.1.60", rep["verified_by"])
        self.assertEqual(rep["measured"]["measured_on"], "2026-11-01")
        # The measurement narrative travels with the answer.
        self.assertTrue(any("restart" in line for line in rep["measured"]["run"]))
        # And the things nobody measured are still named.
        self.assertTrue(any("screen" in n for n in rep["not_verified"]))

    def test_a_build_that_was_never_touched_stays_false(self):
        res = self._run([0.0, "done", 1.0], op="create", bridge_version="4.1.99.0")
        self.assertFalse(res["report"]["verified_against_app"])

    def test_no_version_at_all_stays_false(self):
        res = self._run([0.0, "done", 1.0], op="create")
        self.assertFalse(res["report"]["verified_against_app"])
        self.assertEqual(res["report"]["not_verified"],
                         ["that the file on disk changed",
                          "that what the probe read is what a human sees"])

    def test_an_op_that_was_never_run_there_stays_false(self):
        # `write` is the caller-defined op: whatever it does, the table cannot
        # vouch for it, so a moved probe must not be enough.
        res = self._run([0.0, "done", 1.0], op="write",
                        bridge_version=self.MEASURED)
        self.assertTrue(res["report"]["changed"])
        self.assertFalse(res["report"]["verified_against_app"])

    def test_a_probe_that_did_not_move_never_opens_it(self):
        res = self._run([1.0, "no-op", 1.0], op="create",
                        bridge_version=self.MEASURED)
        self.assertEqual(res["reason_code"], "WRITE_HAD_NO_EFFECT")
        self.assertFalse(res["report"]["verified_against_app"])

    def test_save_opens_it_and_says_the_call_itself_was_not_read_back(self):
        from pcb_agent_mcp import lceda_write as W
        res = W.run(FakeEditor(["saved"]), "s1", "x()", expect="no_error",
                    op="save", bridge_version=self.MEASURED)
        rep = res["report"]
        self.assertTrue(rep["verified_against_app"])
        self.assertIn("THIS call's save was not itself read back", rep["verified_by"])

    def test_the_tool_reads_the_bridge_from_the_client_it_talks_to(self):
        class Speaking(FakeEditor):
            identity = {"bridgeVersion": TheGateOpensOnlyWhereAMeasurementExists.MEASURED}
        client = Speaking([0.0, "done", 1.0])
        from pcb_agent_mcp import tools as T
        orig = T._live_client
        T._live_client = lambda args: client
        try:
            out = T.call_tool("lceda_pcb_write", {
                "session": "s", "code": "create()", "probe": "n", "op": "create"})
        finally:
            T._live_client = orig
        self.assertEqual(out["status"], "ok", out)
        self.assertTrue(out["assumptions"]["verified_against_app"])
        self.assertEqual(out["assumptions"]["bridge_version"], self.MEASURED)
        self.assertIn("measured on LCEDA Pro V4.1.60", out["precision"])

    def test_the_tool_without_a_version_says_not_verified(self):
        from pcb_agent_mcp import tools as T
        client = FakeEditor([0.0, "done", 1.0])   # no identity, no ping
        orig = T._live_client
        T._live_client = lambda args: client
        try:
            out = T.call_tool("lceda_pcb_write", {
                "session": "s", "code": "create()", "probe": "n", "op": "create"})
        finally:
            T._live_client = orig
        self.assertFalse(out["assumptions"]["verified_against_app"])
        self.assertIsNone(out["assumptions"]["bridge_version"])
        self.assertIn("NOT VERIFIED AGAINST THIS APPLICATION BUILD", out["precision"])

LIVE_ENDPOINTS = L.available_endpoints()


@unittest.skipUnless(
    LIVE_ENDPOINTS,
    "no JLCEDAPro* command-space pipe is open (start LCEDA Pro to run this)",
)
class TheRunningEditorAnswersForReal(unittest.TestCase):
    """The only case that proves the bytes are right for the real application."""

    def _connect_or_skip(self):
        """`LIVE_ENDPOINTS` is an import-time snapshot: a pipe can be gone by the
        time the test runs (the application was closed mid-suite), and that is a
        missing instrument, not a regression. Skip, never fail."""
        try:
            return L.connect()
        except L.LiveError as exc:
            self.skipTest("the editor's pipe went away: %s" % (exc,))

    def test_ping_reports_a_bridge_version_and_the_endpoint_is_the_one_we_found(self):
        client = self._connect_or_skip()
        try:
            self.assertTrue(client.verified)
            self.assertIn(client.identity.get("endpoint"), LIVE_ENDPOINTS)
            self.assertRegex(str(client.identity.get("bridgeVersion")), r"^\d+\.\d+\.\d+")
        finally:
            client.close()

    def test_the_eda_namespace_lists_the_pcb_document_class(self):
        client = self._connect_or_skip()
        try:
            surface = client.api("eda")
        finally:
            client.close()
        self.assertIsInstance(surface, dict)
        names = [entry["name"] for entry in surface.get("types") or []]
        self.assertIn("PCB_Document", names)
        self.assertIn("DMT_Pcb", names)
        # The document name is not the runtime property name; the app ships the
        # mapping with every namespace query, so we read it instead of guessing.
        mapping = (surface.get("callConventions") or {}).get("classToProperty") or {}
        self.assertEqual(mapping.get("PCB_Document"), "pcb_Document")
        self.assertEqual(mapping.get("DMT_Pcb"), "dmt_Pcb")


if __name__ == "__main__":
    unittest.main()
