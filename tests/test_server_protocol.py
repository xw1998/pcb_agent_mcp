"""One request must not be able to kill the session, and one reply must be JSON.

Both were measured failures, not hypothetical ones:

  * a JSON-RPC batch arrives as an array. `handle()` assumed a dict, so a batch
    raised `AttributeError: 'list' object has no attribute 'get'` *outside* any
    handler, and the exception left `serve()` - the process was gone, and every
    later request went unanswered. To a user that is "the chat died mid-sentence".
  * `json.dumps` writes bare `NaN` / `Infinity` for those floats. They are not
    JSON; a strict client rejects the whole message and the turn dies the same
    way. A physics answer that divides by zero is exactly where such a value
    appears, so the transport must neutralise it, not the calculator.

`serve()` is the unit under test; every assertion reads the bytes a client sees.
"""
import io
import json
import os
import unittest
from unittest import mock

from pcb_agent_mcp import server
from pcb_agent_mcp import tools


def serve(text):
    out = io.BytesIO()
    server.serve(stdin=io.BytesIO(text.encode("utf-8")), stdout=out)
    return [json.loads(line) for line in out.getvalue().decode("utf-8").splitlines()
            if line.strip()]


def ping(i=1):
    return '{"jsonrpc":"2.0","id":%d,"method":"ping"}' % i


class ABatchIsAnsweredAsABatch(unittest.TestCase):
    def test_a_batch_gets_one_array_holding_every_answer(self):
        lines = serve("[%s,%s]\n" % (ping(1), ping(2)))
        self.assertEqual(len(lines), 1, "a batch is one reply, not one per element")
        self.assertIsInstance(lines[0], list)
        self.assertEqual([r["id"] for r in lines[0]], [1, 2])

    def test_an_element_that_is_not_an_object_is_rejected_alone(self):
        # 7 is not a request. It gets -32600 with a null id (that is what the
        # spec says for an unidentifiable element) and its neighbours still run.
        lines = serve('[%s,7,%s]\n' % (ping(1), ping(2)))
        batch = lines[0]
        self.assertEqual(len(batch), 3)
        bad = [r for r in batch if r.get("id") is None]
        self.assertEqual(len(bad), 1)
        self.assertEqual(bad[0]["error"]["code"], -32600)
        self.assertEqual([r["id"] for r in batch if r.get("id")], [1, 2])

    def test_the_session_keeps_talking_after_a_batch(self):
        # This is the regression: the old code raised out of the read loop here,
        # so the second line was never read and the process was already dead.
        lines = serve("[%s]\n%s\n" % (ping(1), ping(2)))
        self.assertEqual(len(lines), 2)
        self.assertEqual(lines[1]["id"], 2)

    def test_a_notification_inside_a_batch_produces_no_element(self):
        notif = '{"jsonrpc":"2.0","method":"notifications/initialized"}'
        lines = serve("[%s,%s]\n" % (notif, ping(3)))
        self.assertEqual(len(lines[0]), 1)
        self.assertEqual(lines[0][0]["id"], 3)

    def test_an_empty_batch_is_an_error_not_a_silent_no_op(self):
        lines = serve("[]\n")
        self.assertEqual(len(lines), 1)
        self.assertEqual(lines[0]["error"]["code"], -32600)

    def test_a_batch_of_pure_notifications_stays_silent(self):
        notif = '{"jsonrpc":"2.0","method":"notifications/initialized"}'
        self.assertEqual(serve("[%s]\n" % notif), [])


class TheReplyIsAlwaysJSON(unittest.TestCase):
    def test_nan_and_infinity_become_null(self):
        dumped = server._dumps({"v": float("nan"), "w": float("inf"),
                                "u": float("-inf"), "ok": 1.5})
        parsed = json.loads(dumped)
        self.assertIsNone(parsed["v"])
        self.assertIsNone(parsed["w"])
        self.assertIsNone(parsed["u"])
        self.assertEqual(parsed["ok"], 1.5)
        for token in ("NaN", "Infinity"):
            self.assertNotIn(token, dumped)

    def test_a_tool_that_returns_nan_does_not_break_the_wire(self):
        with mock.patch.object(server._tools, "call_tool",
                               return_value={"status": "ok", "value": float("nan")}):
            lines = serve('{"jsonrpc":"2.0","id":5,"method":"tools/call",'
                          '"params":{"name":"unit_convert","arguments":{}}}\n')
        text = lines[0]["result"]["content"][0]["text"]
        self.assertNotIn("NaN", text)
        self.assertIn("null", text)
        json.loads(text)  # the answer is still valid JSON for the model to read

    def test_values_json_cannot_carry_are_stringified_not_dropped(self):
        class Thing(object):
            def __str__(self):
                return "thing"
        parsed = json.loads(server._dumps({"x": Thing(), "t": (1, 2)}))
        self.assertEqual(parsed["x"], "thing")
        self.assertEqual(parsed["t"], [1, 2])


class TheProtocolSurface(unittest.TestCase):
    def test_an_unknown_method_and_bad_json_have_their_own_codes(self):
        lines = serve('{"jsonrpc":"2.0","id":1,"method":"no/such"}\n')
        self.assertEqual(lines[0]["error"]["code"], -32601)
        lines = serve("{not json\n")
        self.assertEqual(lines[0]["error"]["code"], -32700)

    def test_params_that_is_not_an_object_is_ignored_not_fatal(self):
        lines = serve('{"jsonrpc":"2.0","id":1,"method":"ping","params":[]}\n')
        self.assertEqual(lines[0]["result"], {})

    def test_tools_list_follows_the_profile(self):
        with mock.patch.dict(os.environ):
            os.environ.pop("PCB_AGENT_TOOLSET", None)
            core = serve('{"jsonrpc":"2.0","id":1,"method":"tools/list"}\n')[0]
            self.assertEqual(len(core["result"]["tools"]),
                             len(tools.CORE_TOOLS) + len(tools.META_TOOLS))
            os.environ["PCB_AGENT_TOOLSET"] = "all"
            # 0.0.109: the `all` profile is 300 159 B, so the server pages it with
            # the MCP cursor instead of sending one oversized reply. Counting only
            # the first page would now fail for the wrong reason, so walk the
            # cursor and count what a client would actually receive.
            seen, cursor = 0, None
            while True:
                params = ('"params":{"cursor":"%s"}' % cursor) if cursor else ""
                page = serve('{"jsonrpc":"2.0","id":1,"method":"tools/list"%s}\n'
                             % (("," + params) if params else ""))[0]["result"]
                seen += len(page["tools"])
                cursor = page.get("nextCursor")
                if not cursor:
                    break
            self.assertEqual(seen, len(tools.TOOLS))

    def test_the_optional_resource_and_prompt_queries_have_answers(self):
        for method, key in (("resources/templates/list", "resourceTemplates"),
                            ("resources/list", "resources"),
                            ("prompts/list", "prompts")):
            lines = serve('{"jsonrpc":"2.0","id":1,"method":"%s"}\n' % method)
            self.assertEqual(lines[0]["result"][key], [])

    def test_initialize_advertises_the_discovery_route(self):
        lines = serve('{"jsonrpc":"2.0","id":1,"method":"initialize",'
                      '"params":{"protocolVersion":"2025-06-18"}}\n')
        self.assertIn("tool_search", lines[0]["result"]["instructions"])
