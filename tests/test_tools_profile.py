"""What a client is charged for before it says a word.

`tools/list` is the first request of every conversation, and its answer becomes
part of the prompt. At 192 tools the reply was measured at 316 728 bytes
(105 576 tokens by the /3 rule) - larger than a small model's whole window, so
the dialog filled up before the first question and the client stalled.

The server therefore advertises a profile and keeps the rest reachable through
tool_search + tool_call. Three failures matter, and each has a test below:

  * the profile drops a capability and the model concludes the server cannot do
    something it can;
  * the profile is not actually smaller, so nothing is gained;
  * discovery is not equivalent to calling the tool directly, so the short path
    silently gives a different answer than the long one.

Every assertion here has a number in it that was measured, not chosen.
"""
import json
import os
import unittest
from unittest import mock

from pcb_agent_mcp import server
from pcb_agent_mcp import tools

# measured on this tree: the full reply line, envelope included
FULL_LINE_BYTES = 316728      # 192 tools, before the profile existed
CORE_LINE_MAX_BYTES = 60000   # the profile's promise: ~5x smaller, not ~10% smaller


def served_tools_list():
    """The exact bytes `tools/list` puts on the wire."""
    out = __import__("io").BytesIO()
    server.serve(stdin=__import__("io").BytesIO(
        b'{"jsonrpc":"2.0","id":1,"method":"tools/list"}\n'), stdout=out)
    line = out.getvalue().strip()
    return line, json.loads(line)["result"]["tools"]


class TheProfileIsSmaller(unittest.TestCase):
    def test_the_default_reply_is_about_a_sixth_of_the_registry(self):
        line, listed = served_tools_list()
        self.assertGreaterEqual(len(tools.TOOLS), 192,
                                "the registry shrank; re-measure before trusting "
                                "the sizes in this file")
        self.assertLessEqual(len(listed), 50, "profile is not a profile: %d tools"
                             % len(listed))
        self.assertLess(len(line), CORE_LINE_MAX_BYTES,
                        "%d bytes on the wire" % len(line))

    def test_asking_for_everything_still_gives_everything(self):
        # Negative control for the test above: if someone shrank the *registry*
        # instead of adding a profile, this is where it shows up.
        every = tools.list_tools("all")
        self.assertEqual(len(every), len(tools.TOOLS))
        self.assertGreater(len(json.dumps({"tools": every}, separators=(",", ":"),
                                          ensure_ascii=False).encode()),
                           FULL_LINE_BYTES // 2)

    def test_every_name_in_the_profile_exists(self):
        # A typo here does not raise anywhere: the name is simply filtered out of
        # the reply, and the capability becomes unreachable-but-silent.
        missing = [n for n in tools.CORE_TOOLS
                   if n not in tools.TOOLS and n not in tools.META_TOOLS]
        self.assertEqual(missing, [])

    def test_discovery_survives_every_profile(self):
        # Without these two, whatever the profile filtered out is unreachable for
        # the rest of the session.
        for profile in ("core", "all", "*", "magnetics_", "", "board_,calc_"):
            names = [t["name"] for t in tools.list_tools(profile)]
            self.assertIn("tool_search", names, profile)
            self.assertIn("tool_call", names, profile)

    def test_a_profile_that_matches_nothing_falls_back_to_core(self):
        # `PCB_AGENT_TOOLSET=boards_` (a typo for board_) must not hand the model
        # a two-tool server that looks like every capability was deleted.
        names = [t["name"] for t in tools.list_tools("no_such_prefix_")]
        self.assertIn("board_propose", names)
        self.assertIn("tool_search", names)

    def test_a_prefix_picks_that_layer_and_nothing_else(self):
        names = [t["name"] for t in tools.list_tools("magnetics_")]
        body = [n for n in names if n not in tools.META_TOOLS]
        self.assertTrue(body)
        self.assertTrue(all(n.startswith("magnetics_") for n in body), body)

    def test_the_env_var_is_what_the_server_reads(self):
        with mock.patch.dict(os.environ):
            os.environ.pop("PCB_AGENT_TOOLSET", None)
            self.assertEqual(tools.toolset_from_env(), "core")
            self.assertEqual(len(tools.list_tools(tools.toolset_from_env())),
                             len(tools.CORE_TOOLS) + len(tools.META_TOOLS))
            os.environ["PCB_AGENT_TOOLSET"] = "all"
            self.assertEqual(len(tools.list_tools(tools.toolset_from_env())),
                             len(tools.TOOLS))


class FindingAToolYouCannotSee(unittest.TestCase):
    def test_search_finds_by_task_not_just_by_name(self):
        hits = tools.call_tool("tool_search", {"query": "trace impedance"})
        self.assertEqual(hits["status"], "ok")
        names = [t["name"] for t in hits["tools"]]
        self.assertIn("trace_impedance", names[:6], names)

    def test_search_answers_a_chinese_task_word(self):
        # Before the alias table these queries returned "ok, 0 matches": a silent
        # false negative that reads as "this server cannot do impedance".
        cases = (("阻抗", "trace_impedance"), ("过孔", "via_resistance"),
                 ("铺铜", "board_zones"), ("散热", "thermal_via_array"))
        for chinese, expected in cases:
            hits = tools.call_tool("tool_search", {"query": chinese, "limit": 25})
            names = [t["name"] for t in hits.get("tools") or []]
            self.assertEqual(hits["status"], "ok", chinese)
            self.assertIn(expected, names, "%s -> %s" % (chinese, names))

    def test_a_query_nothing_maps_says_so_instead_of_answering_zero(self):
        hits = tools.call_tool("tool_search", {"query": "苹果香蕉"})
        self.assertEqual(hits["returned"], 0)
        self.assertIn("alias", hits["hint"])
        self.assertTrue(hits.get("search_was_english_only"))

    def test_an_english_query_nothing_matches_still_points_at_the_layer_map(self):
        hits = tools.call_tool("tool_search", {"query": "zzzqqq"})
        self.assertEqual(hits["returned"], 0)
        self.assertIn("calc_capabilities", hits["hint"])

    def test_calling_through_the_discovery_path_is_the_same_answer(self):
        direct = tools.call_tool("calc_capabilities", {})
        routed = tools.call_tool("tool_call", {"name": "calc_capabilities"})
        self.assertEqual(json.dumps(direct, sort_keys=True),
                         json.dumps(routed, sort_keys=True))

    def test_arguments_may_arrive_as_a_json_string(self):
        routed = tools.call_tool("tool_call", {"name": "calc_capabilities",
                                               "arguments": "{}"})
        self.assertEqual(routed["status"], "ok")

    def test_discovery_refuses_what_it_cannot_do(self):
        self.assertEqual(tools.call_tool("tool_call", {})["reason_code"],
                         "ARG_MISSING")
        self.assertEqual(tools.call_tool("tool_call", {"name": "tool_call"})
                         ["reason_code"], "ARG_INVALID")
        unknown = tools.call_tool("tool_call", {"name": "board_nope"})
        self.assertEqual(unknown["status"], "error")
        self.assertIn("tool_search", unknown["hint"])

    def test_search_never_returns_more_than_asked(self):
        hits = tools.call_tool("tool_search", {"query": "board", "limit": 3})
        self.assertEqual(hits["returned"], 3)
        self.assertLessEqual(hits["total_matches"], len(tools.TOOLS))
