"""Tests for the DRC severity policy and the apply gate that uses it.

The reason this file exists: on the STM32F401 core board every one of the 69
KiCad violations was a *warning* (silkscreen and text height), and the gate
treated all 69 as blocking, so the board could not be edited at all. The fix is
not "ignore more" - it is "let KiCad's severity decide, and fail closed when the
severity cannot be read".
"""
import unittest

from pcb_agent_mcp.edit import drc_policy as P
from pcb_agent_mcp.edit import plan as PLAN


def v(type_, severity, description="x", pos=None):
    item = {"type": type_, "severity": severity, "description": description}
    if pos is not None:
        item["pos"] = pos
    return item


def drc(*violations):
    """The shape `engine.drc` returns, with only the fields the diff reads."""
    return {"status": "ok", "violations": list(violations)}


class TestTheRuleItself(unittest.TestCase):
    def test_only_error_blocks(self):
        self.assertTrue(P.is_blocking(v("clearance", "error")))
        self.assertFalse(P.is_blocking(v("text_height", "warning")))
        self.assertFalse(P.is_blocking(v("silk_overlap", "exclusion")))

    def test_an_unreadable_severity_blocks(self):
        """'I could not tell' must not be read as 'KiCad says it is cosmetic'."""
        self.assertTrue(P.is_blocking(v("clearance", None)))
        self.assertTrue(P.is_blocking({"type": "clearance"}))
        self.assertTrue(P.is_blocking({}))

    def test_strict_blocks_everything(self):
        self.assertTrue(P.is_blocking(v("text_height", "warning"), strict=True))

    def test_classify_splits_and_counts(self):
        items = [v("text_height", "warning"), v("silk_over_copper", "warning"),
                 v("clearance", "error"), v("shorting_items", "error")]
        out = P.classify(items)
        self.assertEqual(out["total"], 4)
        self.assertEqual(out["by_severity"], {"warning": 2, "error": 2})
        self.assertEqual(out["blocking_total"], 2)
        self.assertEqual(out["advisory_total"], 2)
        self.assertEqual([b["type"] for b in out["blocking"]],
                         ["clearance", "shorting_items"])

    def test_the_types_measured_on_the_core_board_carry_a_fix_hint(self):
        for name in ("silk_edge_clearance", "text_height", "silk_overlap",
                     "silk_over_copper"):
            item = v(name, "warning")
            self.assertTrue(P.hint_for(item), name)
            self.assertEqual(P.MEASURED[name], "warning")

    def test_an_unmeasured_type_still_obeys_its_severity(self):
        out = P.classify([v("some_future_rule", "warning")])
        self.assertEqual(out["advisory_total"], 1)
        self.assertNotIn("fix", out["advisory"][0])

    def test_the_policy_is_stated_not_implied(self):
        out = P.classify([])
        self.assertIn("severity", out["policy"])
        self.assertFalse(out["strict"])


class TestTheApplyGateUsesIt(unittest.TestCase):
    def test_a_new_warning_does_not_break_the_edit(self):
        before = drc(v("text_height", "warning", pos={"x": 1, "y": 1}))
        after = drc(v("text_height", "warning", pos={"x": 1, "y": 1}),
                    v("silk_overlap", "warning", pos={"x": 2, "y": 2}))
        diff = PLAN._drc_diff(before, after)
        self.assertTrue(diff["comparable"])
        self.assertEqual(diff["new_total"], 1)
        self.assertEqual(diff["new_blocking_total"], 0)
        self.assertEqual(diff["new_advisory_total"], 1)
        self.assertEqual(diff["new_advisory"][0]["type"], "silk_overlap")
        self.assertTrue(diff["new_advisory"][0]["fix"])

    def test_a_new_error_still_blocks(self):
        before = drc()
        after = drc(v("clearance", "error", pos={"x": 3, "y": 4}))
        diff = PLAN._drc_diff(before, after)
        self.assertEqual(diff["new_blocking_total"], 1)
        self.assertEqual(diff["new_blocking"][0]["type"], "clearance")
        self.assertEqual(diff["new_by_severity"], {"error": 1})

    def test_fixed_violations_are_reported_as_resolved(self):
        before = drc(v("clearance", "error", pos={"x": 3, "y": 4}))
        after = drc()
        diff = PLAN._drc_diff(before, after)
        self.assertEqual(diff["new_total"], 0)
        self.assertEqual(diff["resolved_total"], 1)

    def test_strict_mode_makes_the_warning_block(self):
        before = drc()
        after = drc(v("text_height", "warning", pos={"x": 0, "y": 0}))
        diff = PLAN._drc_diff(before, after, strict=True)
        self.assertEqual(diff["new_blocking_total"], 1)

    def test_a_drc_that_did_not_run_is_not_a_pass(self):
        diff = PLAN._drc_diff({"status": "error", "reason_code": "KICAD_CLI_NOT_FOUND"},
                              drc())
        self.assertFalse(diff["comparable"])
        self.assertIn("KICAD_CLI_NOT_FOUND", diff["reason"])


if __name__ == "__main__":
    unittest.main()
