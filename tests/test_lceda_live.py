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


class ThePipeReportNamesTheStateNotAGuess(unittest.TestCase):
    """healthy / bridge_dead / not_running - the three states the user sees as
    'works', '未找到 Bridge 服务，3 秒后重试', and 'no window at all'."""

    def _with(self, names, ping):
        original_enum = L.available_endpoints
        original_cls = L.PipeTransport
        L.available_endpoints = lambda prefix=L.APP_NAME: list(names)

        class T(original_cls):
            live = True

            def call(self, op, payload, timeout_s):
                return ping(op, payload, timeout_s)

        L.PipeTransport = T
        try:
            return L.pipe_report()
        finally:
            L.PipeTransport = original_cls
            L.available_endpoints = original_enum

    def test_no_pipe_is_not_running(self):
        report = self._with([], lambda *a: self.fail("nothing to ping"))
        self.assertEqual(report["state"], "not_running")
        self.assertEqual(report["pipes"], [])

    def test_a_pipe_that_answers_is_healthy_and_keeps_the_version(self):
        report = self._with(
            ["JLCEDAProabcd1234"],
            lambda op, payload, t: {"id": 1, "ok": True,
                                    "value": {"bridgeVersion": "4.1.60"}, "logs": []},
        )
        self.assertEqual(report["state"], "healthy")
        self.assertEqual(report["answering"], "JLCEDAProabcd1234")
        self.assertEqual(report["bridge_version"], "4.1.60")

    def test_a_pipe_that_never_answers_is_bridge_dead(self):
        def never(op, payload, t):
            raise L.LiveError("APP_TIMEOUT", "no reply to 'ping'")

        report = self._with(["JLCEDAProabcd1234"], never)
        self.assertEqual(report["state"], "bridge_dead")
        self.assertIsNone(report["answering"])
        self.assertEqual(report["failures"][0]["code"], "APP_TIMEOUT")

class TheClientPassesThroughWhatTheAppSaid(unittest.TestCase):
    def test_ping_keeps_the_identity_and_marks_the_transport_as_not_live(self):
        client = L.Client(FakeTransport([_ok({"endpoint": "FAKE0", "bridgeVersion": "4.1.60"})]))
        identity = client.ping()
        self.assertEqual(identity["bridgeVersion"], "4.1.60")
        self.assertFalse(client.verified)

    def test_open_sends_an_absolute_path_and_the_headless_flag(self):
        transport = FakeTransport([_ok({"bridgeVersion": "4.1.60"}),      # ping before
                                   _ok({"sessionId": "s1", "headless": True}),
                                   _ok({"bridgeVersion": "4.1.60"})])     # ping after
        client = L.Client(transport)
        here = os.path.abspath(__file__)
        value = client.open_project(here, headless=True, verify_content=False)
        self.assertEqual(value["sessionId"], "s1")
        ops = [c[0] for c in transport.calls]
        self.assertEqual(ops, ["ping", "open", "ping"])
        open_call = transport.calls[1]
        self.assertEqual(open_call[1]["path"], here)
        self.assertTrue(open_call[1]["headless"])

    def test_open_defaults_to_not_headless(self):
        transport = FakeTransport([_ok({}), _ok({"sessionId": "s1"}), _ok({})])
        client = L.Client(transport)
        client.open_project(os.path.abspath(__file__), verify_content=False)
        self.assertFalse(transport.calls[1][1]["headless"])

    def test_a_legacy_container_is_refused_before_anything_is_sent(self):
        import tempfile
        with tempfile.NamedTemporaryFile(suffix=".eprj", delete=False) as handle:
            handle.write(b"SQLite format 3\x00" + b"\x00" * 512)
            legacy = handle.name
        try:
            fmt = L.project_container(legacy)
            self.assertEqual(fmt["container"], "sqlite")
            self.assertTrue(fmt["needs_upgrade"])
            transport = FakeTransport([])
            client = L.Client(transport)
            with self.assertRaises(L.LiveError) as caught:
                client.open_project(legacy)
            self.assertEqual(caught.exception.code, "PROJECT_NEEDS_UPGRADE")
            self.assertEqual(caught.exception.extra["container"]["container"], "sqlite")
            self.assertEqual(transport.calls, [])
            # And saying so on purpose is still allowed (the dialog is then the
            # caller's problem, which is what allow_upgrade means).
            transport = FakeTransport([_ok({"bridgeVersion": "4.1.60"}),
                                       _ok({"sessionId": "s1"}),
                                       _ok({"bridgeVersion": "4.1.60"})])
            client = L.Client(transport)
            client.open_project(legacy, allow_upgrade=True, verify_content=False)
            self.assertEqual([c[0] for c in transport.calls], ["ping", "open", "ping"])
        finally:
            os.unlink(legacy)

    def test_a_zip_container_is_not_refused(self):
        import tempfile
        with tempfile.NamedTemporaryFile(suffix=".epro2", delete=False) as handle:
            handle.write(b"PK\x03\x04" + b"\x00" * 32)
            modern = handle.name
        try:
            fmt = L.project_container(modern)
            self.assertFalse(fmt["needs_upgrade"])
            self.assertEqual(fmt["container"], "zip")
        finally:
            os.unlink(modern)

    def test_a_project_that_did_not_load_is_not_called_a_success(self):
        # Measured on 4.1.60: 'open' answers with a session id and the pipe keeps
        # pinging while the window sits on a modal dialog and the app's own project
        # object stays {}. A ping-only canary calls that green.
        transport = FakeTransport([_ok({"bridgeVersion": "4.1.60"}),
                                   _ok({"sessionId": "s1"}),
                                   _ok({"bridgeVersion": "4.1.60"}),
                                   _ok([{"sessionId": "s1", "status": "alive"}]),
                                   _ok({"pcbCount": 0, "schCount": 0})])
        client = L.Client(transport)
        with self.assertRaises(L.LiveError) as caught:
            client.open_project(os.path.abspath(__file__))
        exc = caught.exception
        self.assertEqual(exc.code, "OPEN_NOT_CONFIRMED")
        self.assertFalse(exc.extra["content_probe"]["loaded"])
        self.assertFalse(exc.extra["content_probe"]["session_loaded"])
        self.assertEqual(exc.extra["content_probe"]["pcb_count"], 0)
        self.assertIn("document", exc.extra["next"])
        self.assertEqual([c[0] for c in transport.calls],
                         ["ping", "open", "ping", "session.list", "invoke"])
        self.assertEqual(transport.calls[4][1]["session"], "s1")

    def test_the_document_lists_are_what_confirm_the_open(self):
        # getCurrentProjectInfo() never settles on this build (measured 45 s), so the
        # lists of PCB and schematic documents are the reading that can answer.
        transport = FakeTransport([_ok({"bridgeVersion": "4.1.60"}),
                                   _ok({"sessionId": "s1"}),
                                   _ok({"bridgeVersion": "4.1.60"}),
                                   _ok([{"sessionId": "s1", "status": "alive"}]),
                                   _ok({"pcbCount": 2, "schCount": 1,
                                        "projectUuid": "p1",
                                        "pcbs": [{"name": "PCB1"}, {"name": "PCB2"}],
                                        "projectCount": 0})])
        client = L.Client(transport)
        value = client.open_project(os.path.abspath(__file__))
        self.assertEqual(value["sessionId"], "s1")
        state = client.last_open["content"]
        self.assertTrue(state["loaded"])
        self.assertTrue(state["session_loaded"])
        self.assertEqual(state["pcb_count"], 2)
        self.assertEqual(state["sch_count"], 1)
        self.assertEqual(state["project_uuid"], "p1")
        self.assertEqual(len(state["pcbs"]), 2)
        self.assertEqual(client.open_session, "s1")

    def test_the_probe_uses_the_last_live_session_when_none_is_named(self):
        transport = FakeTransport([_ok([{"sessionId": "dead", "status": "dead"},
                                        {"sessionId": "s2", "status": "alive"}]),
                                   _ok({"pcbCount": 0, "schCount": 0})])
        client = L.Client(transport)
        state = client.project_state()
        self.assertFalse(state["loaded"])
        self.assertEqual(state["session"], "s2")
        self.assertEqual(transport.calls[1][1]["session"], "s2")

    def test_a_probe_that_cannot_run_is_not_loaded(self):
        # A getter that never settles takes the whole probe down; the cheap fallback
        # still answers, so the two facts that are knowable are not lost with it.
        transport = FakeTransport([_ok([{"sessionId": "s1", "status": "alive"}]),
                                   L.LiveError("APP_TIMEOUT", "no reply"),
                                   _ok({"projectCount": 4,
                                        "workspace": {"uuid": "Personal"}})])
        client = L.Client(transport)
        state = client.project_state("s1")
        self.assertFalse(state["loaded"])
        self.assertEqual(state["project_count"], 4)
        self.assertEqual(state["workspace"], {"uuid": "Personal"})
        self.assertIn("APP_TIMEOUT: no reply", state["why"])
        self.assertIn("not a project list on this build", state["why"])

    def test_a_fallback_that_also_fails_still_answers(self):
        transport = FakeTransport([_ok([{"sessionId": "s1", "status": "alive"}]),
                                   L.LiveError("APP_TIMEOUT", "no reply"),
                                   L.LiveError("APP_TIMEOUT", "no reply")])
        client = L.Client(transport)
        state = client.project_state("s1")
        self.assertFalse(state["loaded"])
        self.assertIsNone(state["project_count"])
        self.assertIn("no session could be read", state["why"])
        self.assertIn("not a project list on this build", state["why"])

    def test_an_editor_that_stops_answering_after_open_is_reported_not_returned(self):
        transport = FakeTransport([_ok({"bridgeVersion": "4.1.60"}),
                                   _ok({"sessionId": "s1", "renderId": 3}),
                                   L.LiveError("APP_TIMEOUT", "no reply to 'ping'")])
        client = L.Client(transport)
        with self.assertRaises(L.LiveError) as caught:
            client.open_project(os.path.abspath(__file__), headless=True)
        exc = caught.exception
        self.assertEqual(exc.code, "APP_DIED_DURING_OPEN")
        self.assertEqual(exc.extra["open_reply"]["sessionId"], "s1")
        self.assertTrue(exc.extra["headless"])
        self.assertGreater(exc.extra["size_bytes"], 0)
        self.assertEqual(exc.extra["bridge_after"]["code"], "APP_TIMEOUT")
        self.assertEqual(exc.extra["bridge_before"]["bridge_version"], "4.1.60")

    def test_a_half_dead_editor_is_not_asked_to_open_anything(self):
        transport = FakeTransport([L.LiveError("APP_TIMEOUT", "no reply to 'ping'")])
        client = L.Client(transport)
        with self.assertRaises(L.LiveError) as caught:
            client.open_project(os.path.abspath(__file__))
        self.assertEqual(caught.exception.code, "APP_TIMEOUT")
        self.assertEqual([c[0] for c in transport.calls], ["ping"])

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
        self.assertEqual(len(names), 11, names)
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

class RecoverChangesNothingUnlessItIsTold(unittest.TestCase):
    """The tool exists for one state: a pipe that is there and never answers. It
    must be impossible to lose unsaved work by merely LOOKING at that state."""

    def setUp(self):
        from pcb_agent_mcp import tools as T
        self.T = T
        self.orig = (T._live.pipe_report, T._desktop.detect,
                     T._desktop.launch, T._desktop.close)
        self.calls = []
        T._desktop.detect = lambda app: {"found": True, "exe": r"C:\\fake\\lceda-pro.exe"}
        T._desktop.launch = lambda *a, **k: self.calls.append(("launch",)) or {"status": "ok"}
        T._desktop.close = lambda *a, **k: self.calls.append(("close",)) or {"status": "ok"}

    def tearDown(self):
        (self.T._live.pipe_report, self.T._desktop.detect,
         self.T._desktop.launch, self.T._desktop.close) = self.orig

    def _report(self, state):
        return {"state": state, "pipes": [] if state == "not_running" else ["JLCEDAProd"],
                "answering": None, "bridge_version": None, "failures": [],
                "pipe_root": L.PIPE_ROOT}

    def test_a_healthy_bridge_is_reported_and_nothing_is_started(self):
        self.T._live.pipe_report = lambda **k: dict(self._report("healthy"),
                                                    answering="JLCEDAProd",
                                                    bridge_version="4.1.60")
        out = self.T.call_tool("lceda_pro_recover", {})
        self.assertEqual(out.get("status"), "ok")
        self.assertEqual(out["value"]["state"], "healthy")
        self.assertEqual(self.calls, [])

    def test_without_confirm_a_dead_bridge_is_only_reported(self):
        self.T._live.pipe_report = lambda **k: self._report("bridge_dead")
        out = self.T.call_tool("lceda_pro_recover", {})
        self.assertEqual(out.get("reason_code"), "RECOVER_NOT_CONFIRMED")
        self.assertEqual(self.calls, [])

    def test_confirm_on_a_dead_bridge_closes_first_then_launches(self):
        states = [self._report("bridge_dead"), self._report("healthy")]
        states[1]["answering"] = "JLCEDAProd"
        seq = list(states)
        self.T._live.pipe_report = lambda **k: seq.pop(0) if seq else self._report("healthy")
        out = self.T.call_tool("lceda_pro_recover", {"confirm": True, "wait_s": 5})
        self.assertEqual(out.get("status"), "ok")
        self.assertEqual([c[0] for c in self.calls], ["close", "launch"])
        self.assertEqual(out["value"]["state"], "restarted")

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
class TheConnectionCheckSeparatesThreeFacts(unittest.TestCase):
    """Pipe answers / a session is alive / a project object comes back.

    Only the third one means a call would land on a project. The measured failure
    this class exists for: `open` returned a session id and ping kept answering
    while the window sat on a modal dialog and every `eda.dmt_*` call returned {}.
    """

    def setUp(self):
        from pcb_agent_mcp import tools as T
        self.T = T
        self.orig = (T._live.pipe_report, T._live_client, T._lceda_instances,
                     T._desktop.launch)
        T._lceda_instances = lambda: [{"pid": 4242, "has_window": True,
                                       "title": "嘉立创EDA(专业版) - V4.1.60",
                                       "project": None}]

    def tearDown(self):
        (self.T._live.pipe_report, self.T._live_client, self.T._lceda_instances,
         self.T._desktop.launch) = self.orig

    def _report(self, state, **kw):
        rep = {"state": state, "pipes": ["JLCEDAProd"] if state != "not_running" else [],
               "answering": None, "bridge_version": None, "failures": [],
               "pipe_root": L.PIPE_ROOT}
        rep.update(kw)
        return rep

    def test_a_dead_pipe_is_not_connected(self):
        self.T._live.pipe_report = lambda **k: self._report("bridge_dead")
        out = self.T.call_tool("lceda_pro_verify", {})
        self.assertEqual(out.get("status"), "error")
        self.assertEqual(out["reason_code"], "NOT_CONNECTED")
        self.assertFalse(out["connected"])
        self.assertEqual(len(out["instances"]), 1)

    def test_a_healthy_pipe_with_no_project_is_the_failing_case(self):
        self.T._live.pipe_report = lambda **k: self._report(
            "healthy", answering="JLCEDAProd", bridge_version="4.1.60.198f38ab")

        class Client(object):
            endpoint = "JLCEDAProd"

            def project_state(self, session=None, timeout_s=15.0):
                return {"loaded": False, "session": "s1", "project_keys": [],
                        "project": None, "pcb_count": 0, "project_count": 0,
                        "probe_error": None,
                        "why": "the app returned an empty project object"}

            def close(self):
                pass

        self.T._live_client = lambda args: Client()
        out = self.T.call_tool("lceda_pro_verify", {})
        self.assertEqual(out["reason_code"], "NO_PROJECT_LOADED")
        self.assertFalse(out["connected"])
        self.assertEqual(out["state"], "healthy")
        self.assertEqual(out["project"]["why"],
                         "the app returned an empty project object")

    def test_a_project_object_is_what_makes_it_connected(self):
        self.T._live.pipe_report = lambda **k: self._report(
            "healthy", answering="JLCEDAProd", bridge_version="4.1.60.198f38ab")

        class Client(object):
            endpoint = "JLCEDAProd"

            def project_state(self, session=None, timeout_s=15.0):
                return {"loaded": True, "session": "s1", "project_keys": ["id"],
                        "project": {"id": "p1"}, "pcb_count": 1, "project_count": 3,
                        "probe_error": None,
                        "why": "the app returned a project object"}

            def close(self):
                pass

        self.T._live_client = lambda args: Client()
        out = self.T.call_tool("lceda_pro_verify", {})
        self.assertEqual(out.get("status"), "ok")
        self.assertTrue(out["value"]["connected"])
        self.assertEqual(out["value"]["project"]["project_count"], 3)

    def test_a_second_instance_is_refused_instead_of_started(self):
        # A window is up but no pipe was ever created for it: the one state where
        # recover would otherwise start a second copy.
        self.T._live.pipe_report = lambda **k: self._report("not_running")
        started = []
        self.T._desktop.launch = lambda *a, **k: started.append(1)
        out = self.T.call_tool("lceda_pro_recover", {"confirm": True, "wait_s": 5})
        self.assertEqual(out["reason_code"], "ALREADY_RUNNING")
        self.assertEqual(started, [])
        self.assertEqual(out["instances"][0]["pid"], 4242)

class OneEditorMeansOneWindow(unittest.TestCase):
    """Measured 2026-11-07: ONE editor is 6-7 `lceda-pro.exe` processes (main,
    crashpad, gpu, NetworkService, 3 renderers) and, after an `open`, TWO titled
    frames. So "how many editors are open" is answered by counting titled frames -
    not processes (says 7) and not raw HWNDs (says more)."""

    def setUp(self):
        from pcb_agent_mcp import tools as T
        self.T = T
        self.orig = (T._desktop._instances, T._desktop.editor_windows,
                     T._desktop.close_window, T._desktop._user32)
        self.windows = [
            {"hwnd": 101, "pid": 1848, "title": "嘉立创EDA(专业版) - V4.1.60",
             "area": 1298900, "instance_pid": 1848},
            {"hwnd": 202, "pid": 1848, "title": "嘉立创EDA(专业版) - V4.1.60",
             "area": 1298900, "instance_pid": 1848},
        ]
        self.closed = []

        class _U(object):
            def GetForegroundWindow(self):
                return 202

        T._desktop._instances = lambda app: [
            {"pid": 1848, "has_window": True, "window_count": 2, "process_count": 6,
             "title": "嘉立创EDA(专业版) - V4.1.60", "titles": [], "hwnd": 101,
             "project": "", "processes": [1848, 1, 2, 3, 4, 5], "created": 0.0}]
        T._desktop.editor_windows = lambda app: [dict(w) for w in self.windows
                                                 if w["hwnd"] not in self.closed]
        T._desktop.close_window = lambda hwnd, timeout_s=6.0: (
            self.closed.append(int(hwnd)) or {"status": "ok", "hwnd": int(hwnd),
                                              "closed": True, "still_open": False})
        T._desktop._user32 = lambda: _U()

    def tearDown(self):
        (self.T._desktop._instances, self.T._desktop.editor_windows,
         self.T._desktop.close_window, self.T._desktop._user32) = self.orig

    def test_one_process_with_two_frames_is_not_one_window(self):
        out = self.T._lceda_pro_close_extra({})
        self.assertEqual(out["reason_code"], "CLOSE_NOT_CONFIRMED")
        self.assertEqual(out["instances"][0]["windows"], 2)
        self.assertEqual(out["instances"][0]["processes"], 6)
        self.assertEqual(self.closed, [])

    def test_confirm_closes_the_extra_and_leaves_one(self):
        out = self.T._lceda_pro_close_extra({"confirm": True})
        self.assertEqual(out.get("status"), "ok")
        self.assertEqual(len(self.closed), 1)
        self.assertEqual(len(out["value"]["windows_after"]), 1)
        self.assertNotEqual(out["value"]["kept"]["hwnd"], self.closed[0])

    def test_naming_the_window_to_keep_is_honoured(self):
        out = self.T._lceda_pro_close_extra({"confirm": True, "keep_hwnd": 202})
        self.assertEqual(out["value"]["kept"]["hwnd"], 202)
        self.assertEqual(self.closed, [101])

    def test_one_window_is_left_alone(self):
        self.closed.append(202)
        out = self.T._lceda_pro_close_extra({"confirm": True})
        self.assertEqual(out["value"]["closed"], [])
        self.assertEqual(len(self.closed), 1)

    def test_a_window_that_refuses_to_close_is_reported(self):
        self.T._desktop.close_window = lambda hwnd, timeout_s=6.0: {
            "status": "ok", "hwnd": int(hwnd), "closed": False, "still_open": True}
        out = self.T._lceda_pro_close_extra({"confirm": True})
        self.assertEqual(out["reason_code"], "CLOSE_INCOMPLETE")
        self.assertEqual(len(out["still_open"]), 1)

class TheLauncherRefusesToStartASecondCopy(unittest.TestCase):
    """The measured incident: an editor was already up, launch() was called anyway,
    and a second window appeared that shared nothing with the first."""

    def setUp(self):
        from pcb_agent_mcp import desktop as D
        self.D = D
        self.orig = (D._instances, D.detect, D.subprocess.Popen)
        D._instances = lambda app: [{"pid": 4242, "has_window": True,
                                     "title": "嘉立创EDA(专业版) - V4.1.60",
                                     "project": "", "created": 0.0}]

    def tearDown(self):
        (self.D._instances, self.D.detect, self.D.subprocess.Popen) = self.orig

    def test_a_running_window_means_no_second_process(self):
        spawned = []
        self.D.subprocess.Popen = lambda *a, **k: spawned.append(a) or None
        out = self.D.launch(os.path.abspath(__file__), [], app="lceda-pro")
        self.assertEqual(out["reason_code"], "ALREADY_RUNNING")
        self.assertEqual(spawned, [])
        self.assertEqual(out["instances"][0]["pid"], 4242)

    def test_saying_so_on_purpose_still_works(self):
        class P(object):
            pid = 9999
        self.D.subprocess.Popen = lambda *a, **k: P()
        out = self.D.launch(os.path.abspath(__file__), [],
                            app="lceda-pro", allow_second=True)
        self.assertEqual(out.get("status"), "ok")
        self.assertEqual(out["pid"], 9999)

    def test_an_unknown_app_is_not_gated(self):
        class P(object):
            pid = 9998
        self.D.subprocess.Popen = lambda *a, **k: P()
        out = self.D.launch(os.path.abspath(__file__), [])
        self.assertEqual(out["pid"], 9998)

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


class TestTheProjectFileItselfSaysWhatIsInIt(unittest.TestCase):
    """A timed-out document list is not an empty project.

    Measured 2026-11-07: `getAllPcbsInfo()` timed out against a project whose own
    database names two PCBs. The file is readable while the editor is not, so it is
    used as corroboration - never as a substitute for the in-app answer.
    """

    def _write(self, rows):
        import json as _json
        import sqlite3
        import tempfile
        handle = tempfile.NamedTemporaryFile(suffix=".eprj2", delete=False)
        handle.close()
        con = sqlite3.connect(handle.name)
        con.execute("create table project_structures (id integer primary key, "
                    "ticket text, project_uuid text, branch_uuid text, structure text)")
        for index, structure in enumerate(rows):
            con.execute("insert into project_structures values (?,?,?,?,?)",
                        (index + 1, "t", "p", "b", _json.dumps(structure)))
        con.commit()
        con.close()
        return handle.name

    def test_the_newest_row_decides_and_pcbs_are_counted(self):
        old = {"pcbs": {}, "schematics": {}}
        new = {"pcbs": {"a": {"uuid": "a", "title": "PCB1"},
                        "b": {"uuid": "b", "title": "PCB2"}},
               "schematics": {"s": {"uuid": "s", "name": "Schematic1"}},
               "sheets": {"h": {"uuid": "h", "title": "P1"}},
               "boards": {"d": {"uuid": "d", "title": "Board1"}},
               "panels": {}}
        path = self._write([old, new])
        try:
            reading = L.project_structure(path)
        finally:
            os.unlink(path)
        self.assertTrue(reading["readable"])
        self.assertEqual(reading["row_id"], 2)
        self.assertEqual(reading["counts"]["pcbs"], 2)
        self.assertEqual(reading["counts"]["schematics"], 1)
        self.assertEqual(reading["counts"]["panels"], 0)
        self.assertEqual([p["name"] for p in reading["pcbs"]], ["PCB1", "PCB2"])

    def test_a_file_that_is_not_a_project_database_says_so(self):
        import tempfile
        with tempfile.NamedTemporaryFile(suffix=".epro2", delete=False) as handle:
            handle.write(b"PK\x03\x04" + b"\x00" * 32)
            path = handle.name
        try:
            reading = L.project_structure(path)
        finally:
            os.unlink(path)
        self.assertFalse(reading["readable"])
        self.assertIn("database", reading["why"].lower())

class TestTheListingIsNotThePage(unittest.TestCase):
    """`limit` is how many rows to hand back, not how many exist.

    Measured 2026-11-07: the walk stopped at the limit, so asking for 3 rows also
    said the machine holds 3 projects - and the disagreement text ("the app lists
    0 while N exist") changed with the page size.
    """

    def test_total_does_not_depend_on_the_page_size(self):
        import json
        import shutil
        import tempfile
        base = tempfile.mkdtemp(prefix="lceda_projects_")
        user = os.path.join(base, "LCEDA-Pro")
        projects = os.path.join(base, "projects")
        os.makedirs(user)
        os.makedirs(projects)
        try:
            for index in range(5):
                with open(os.path.join(projects, "p%d.epro2" % index), "wb") as fh:
                    fh.write(b"PK\x03\x04" + b"\x00" * 16)
            with open(os.path.join(user, "config.json"), "w",
                      encoding="utf-8") as fh:
                json.dump({"type": "HALF_OFFLINE",
                           "APP_PROJECT_DIR": [projects]}, fh)
            one = L.projects_on_disk(user_data=user, deep=True, limit=1)
            full = L.projects_on_disk(user_data=user, deep=True, limit=None)
            self.assertEqual(one["total"], 5)
            self.assertEqual(len(one["projects"]), 1)
            self.assertTrue(one["truncated"])
            self.assertEqual(full["total"], 5)
            self.assertEqual(len(full["projects"]), 5)
            self.assertFalse(full["truncated"])
        finally:
            shutil.rmtree(base, ignore_errors=True)

if __name__ == "__main__":
    unittest.main()
