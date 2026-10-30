"""Snippets built out of the app's own method table, and the refusals that guard it.

Nothing here contacts LCEDA Pro. The `doc.api` answers are the shapes the real
4.1.60 bridge returns (namespace -> {types, callConventions}; class ->
{title, remarks, methods:[{name, comment}], callPath}), trimmed by hand so a
test can read them. What the tests hold the module to:

  * the method NAME is taken from that table and nothing is invented - a table
    without a matching method is a refusal, not a similar-looking name;
  * two equally good matches are an ambiguity with the candidates attached;
  * the receiver comes from `callPath` (or an explicit `property`), never from
    lower-casing the class name;
  * the ARGUMENTS are the caller's and are passed verbatim;
  * the generated snippet goes through `lceda_write.run` unchanged, probe and
    all, and every answer still says verified_against_app=false.

The negative controls matter more than the happy path: a generator that quietly
returns "the closest method" is exactly the plausible wrong answer this project
refuses, so each way of being wrong has a test that fails if the guard is removed.
"""

import unittest

from pcb_agent_mcp import lceda_snippet as S
from pcb_agent_mcp import lceda_write as W
from pcb_agent_mcp import tools

PCBDOC = {
    "title": "PCB_Document",
    "remarks": "PCB 文档",
    "callPath": "eda.pcb_Document.",
    "methods": [
        {"name": "save", "comment": "保存文档"},
        {"name": "saveAs", "comment": "另存为"},
        {"name": "getCanvasOrigin", "comment": "获取画布原点"},
        {"name": "getAllComponentsPosition", "comment": "获取所有器件位置"},
        {"name": "setComponentPosition", "comment": "设置器件位置"},
        {"name": "moveComponent", "comment": "移动器件"},
        {"name": "addComponent", "comment": "添加器件"},
        {"name": "deleteComponent", "comment": "删除器件"},
        {"name": "refresh", "comment": "刷新"},
    ],
}

AMBIGUOUS = {
    "title": "PCB_Document",
    "callPath": "eda.pcb_Document.",
    "methods": [
        {"name": "moveComponent", "comment": "移动器件"},
        {"name": "moveComponentTo", "comment": "移动器件到指定位置"},
    ],
}

NO_CALLPATH = {
    "title": "PCB_Document",
    "methods": [{"name": "save", "comment": "保存文档"}],
}

NAMESPACE = {
    "types": [{"name": "PCB_Document", "comment": "PCB 文档"}],
    "callConventions": {"entry": "eda", "classToProperty":
                        {"PCB_Document": "pcb_Document"}},
}

class Resolving(unittest.TestCase):
    def test_a_save_is_one_call_on_the_receiver_the_app_named(self):
        r = S.build("save", PCBDOC)
        self.assertEqual(r["status"], "ok")
        self.assertEqual(r["code"], "return await eda.pcb_Document.save();")
        self.assertEqual(r["receiver_from"], "callPath")
        self.assertEqual(r["confidence"], "name")
        self.assertFalse(r["verified_against_app"])
        self.assertTrue(any("argument order" in n for n in r["not_verified"]))

    def test_a_move_uses_the_caller_arguments_verbatim(self):
        r = S.build("move", PCBDOC, argsv=["'U1'", "{dx: 5, dy: -3}"])
        self.assertEqual(r["status"], "ok")
        self.assertEqual(r["code"],
                         "return await eda.pcb_Document.moveComponent('U1', "
                         "{dx: 5, dy: -3});")
        self.assertTrue(r["args_from_caller"])

    def test_numbers_are_json_encoded_and_strings_are_not_quoted_twice(self):
        r = S.build("create", PCBDOC, argsv=[5, 2.5, True, None])
        self.assertEqual(r["code"],
                         "return await eda.pcb_Document.addComponent(5, 2.5, true, "
                         "null);")
        self.assertEqual(r["json_encoded_args"], [1, 2, 3, 4])

    def test_a_probe_can_be_suggested_from_the_same_table_and_admits_what_it_is(self):
        p = S.suggest_probe(PCBDOC)
        self.assertEqual(p["status"], "ok")
        self.assertEqual(p["code"],
                         "return await eda.pcb_Document.getAllComponentsPosition();")
        self.assertFalse(p["verified_against_app"])
        self.assertTrue(any("is the object you are about to change" in n
                            for n in p["not_verified"]))

    def test_an_explicit_property_wins_and_the_class_name_is_never_lower_cased(self):
        r = S.build("save", NO_CALLPATH, prop="pcb_Document")
        self.assertEqual(r["receiver"], "eda.pcb_Document.")
        self.assertEqual(r["receiver_from"], "caller")

class Refusals(unittest.TestCase):
    def test_a_table_without_the_operation_is_a_refusal_not_the_closest_name(self):
        short = {"title": "PCB_Document", "callPath": "eda.pcb_Document.",
                 "methods": [{"name": "refresh", "comment": "刷新"},
                             {"name": "getCanvasOrigin", "comment": "获取画布原点"}]}
        r = S.build("move", short, argsv=["'U1'"])
        self.assertEqual(r["status"], "error")
        self.assertEqual(r["reason_code"], "SNIPPET_NO_API")
        self.assertIn("lceda_pcb_write", r["hint"])

    def test_two_equally_good_methods_are_ambiguous_and_both_are_named(self):
        r = S.build("move", AMBIGUOUS, argsv=["'U1'"])
        self.assertEqual(r["status"], "ambiguous")
        self.assertEqual(r["reason_code"], "SNIPPET_AMBIGUOUS")
        self.assertEqual(sorted(r["candidates"]),
                         ["moveComponent", "moveComponentTo"])

    def test_pick_settles_the_ambiguity_and_an_unknown_pick_is_refused(self):
        r = S.build("move", AMBIGUOUS, argsv=["'U1'"], pick="moveComponentTo")
        self.assertEqual(r["status"], "ok")
        self.assertIn("moveComponentTo(", r["code"])
        bad = S.build("move", AMBIGUOUS, argsv=["'U1'"], pick="moveEverything")
        self.assertEqual(bad["reason_code"], "SNIPPET_NO_API")

    def test_a_class_name_is_not_a_receiver(self):
        with self.assertRaises(S.SnippetError) as ctx:
            S.build("save", NO_CALLPATH)
        self.assertEqual(ctx.exception.reason_code, "SNIPPET_NO_RECEIVER")
        self.assertIn("lower-case", ctx.exception.hint)

    def test_a_namespace_table_lists_classes_but_no_methods(self):
        with self.assertRaises(S.SnippetError) as ctx:
            S.build("save", NAMESPACE)
        self.assertEqual(ctx.exception.reason_code, "ARG_INVALID")
        self.assertIn("NAMESPACE", ctx.exception.message)

    def test_a_move_without_arguments_is_refused_instead_of_emitted_empty(self):
        r = S.build("move", PCBDOC)
        self.assertEqual(r["reason_code"], "SNIPPET_NO_ARGS")

    def test_the_vendor_comment_alone_is_weaker_evidence_and_says_so(self):
        table = {"title": "PCB_Document", "callPath": "eda.pcb_Document.",
                 "methods": [{"name": "applyEdit", "comment": "移动器件"}]}
        r = S.build("move", table, argsv=["'U1'"])
        self.assertEqual(r["status"], "ok")
        self.assertEqual(r["confidence"], "comment_only")

    def test_a_read_whose_comment_borrows_a_write_word_is_not_taken_for_a_write(self):
        table = {"title": "PCB_Document", "callPath": "eda.pcb_Document.",
                 "methods": [{"name": "getMovableItems", "comment": "移动区域"}]}
        r = S.build("move", table, argsv=["'U1'"])
        self.assertEqual(r["reason_code"], "SNIPPET_NO_API")

class Wiring(unittest.TestCase):
    """The generated snippet has to survive the write discipline unchanged."""

    def test_the_generated_code_and_probe_go_through_lceda_write_untouched(self):
        code = S.build("move", PCBDOC, argsv=["'U1'", "1", "2"])["code"]
        probe = S.suggest_probe(PCBDOC)["code"]
        seen = []

        class Fake(object):
            def invoke(self, session, snippet, fn="run", args=None, timeout_ms=None):
                seen.append(snippet)
                value = {"count": 1} if len(seen) == 1 else {"count": 2}
                return {"value": value, "logs": [], "duration_ms": 1}

        rep = W.run(Fake(), "sess-1", code, probe=probe)
        self.assertEqual(seen, [probe, code, probe])
        self.assertTrue(rep["report"]["changed"])
        self.assertEqual(rep["report"]["verified_against_app"], False)

    def test_a_generated_snippet_that_changes_nothing_is_still_a_refusal(self):
        code = S.build("save", PCBDOC)["code"]

        class Stuck(object):
            def invoke(self, session, snippet, fn="run", args=None, timeout_ms=None):
                return {"value": {"count": 7}, "logs": [], "duration_ms": 1}

        rep = W.run(Stuck(), "sess-1", code, probe="return 1;")
        self.assertEqual(rep["status"], "error")
        self.assertEqual(rep["reason_code"], "WRITE_HAD_NO_EFFECT")

    def test_the_tool_is_registered_and_documented_with_its_limits(self):
        caps = tools.call_tool("calc_capabilities", {})
        self.assertIn("lceda_write_snippet", caps["available"])
        blob = tools.TOOLS["lceda_write_snippet"]["description"]
        for word in ("SNIPPET_NO_API", "SNIPPET_AMBIGUOUS", "SNIPPET_NO_RECEIVER",
                     "verified_against_app=false"):
            self.assertIn(word, blob)
        blob2 = " ".join(caps["not_implemented_yet"])
        self.assertIn("callPath", blob2)

    def test_the_tool_builds_from_a_pasted_dump_without_touching_any_app(self):
        r = tools.call_tool("lceda_write_snippet",
                            {"op": "move", "api_dump": PCBDOC,
                             "argsv": ["'U1'", "5", "0"]})
        self.assertEqual(r["status"], "ok", r)
        self.assertEqual(r["value"]["code"],
                         "return await eda.pcb_Document.moveComponent('U1', 5, 0);")
        self.assertEqual(r["value"]["receiver_from"], "callPath")
        self.assertIn("getAllComponentsPosition", r["value"]["probe"])
        self.assertFalse(r["assumptions"]["verified_against_app"])

    def test_the_tool_reports_an_ambiguity_as_an_error_with_the_candidates(self):
        r = tools.call_tool("lceda_write_snippet",
                            {"op": "move", "api_dump": AMBIGUOUS,
                             "argsv": ["'U1'"]})
        self.assertEqual(r["status"], "error")
        self.assertEqual(r["reason_code"], "SNIPPET_AMBIGUOUS")
        self.assertEqual(sorted(r["report"]["candidates"]),
                         ["moveComponent", "moveComponentTo"])

    def test_the_tool_never_runs_anything(self):
        # No `session`, no pipe: a dump alone must be enough, and the answer must
        # not claim any observation of the application.
        r = tools.call_tool("lceda_write_snippet",
                            {"op": "save", "api_dump": PCBDOC})
        self.assertEqual(r["status"], "ok", r)
        self.assertIn("NOT VERIFIED AGAINST THE APPLICATION", r["precision"])
        self.assertEqual(r["value"]["args_from_caller"], True)

if __name__ == "__main__":
    unittest.main()
