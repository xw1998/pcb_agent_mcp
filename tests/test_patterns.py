"""Patterns: the reusable half of what this server learns.

Every refusal in this file has a failure side, and that is the only reason the
refusal exists: a bare number would be reused as if it had been measured, a
layout with two anchors would be planned twice with two different origins, and a
promotion with no reading would make a candidate indistinguishable from a
verified artefact.
"""
import os
import sys
import shutil
import tempfile
import unittest

sys.path.insert(0, os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "src"))

from pcb_agent_mcp import patterns as P  # noqa: E402
from pcb_agent_mcp import tools  # noqa: E402

HERE = os.path.dirname(os.path.abspath(__file__))
SAMPLE = os.path.join(HERE, "data", "sample.kicad_pcb")

RECIPE = {
    "id": "pat.recipe.usb-c-sink-cc",
    "title": "USB-C sink advertises its current with Rd on both CC pins",
    "problem": "a USB-C receptacle wired as a device must tell the host how much "
               "current it may draw",
    "applies_to": {"parts": ["USB1"]},
    "procedure": ["place one 5.1k resistor from CC1 to GND",
                  "place the same value from CC2 to GND",
                  "do not share one resistor between CC1 and CC2"],
    "parameters": {"rd_ohm": {"value": 5100, "unit": "ohm",
                              "source": "USB Type-C spec: Rd for a sink is 5.1k"}},
}

LAYOUT = {
    "id": "pat.layout.mcu-cluster",
    "title": "MCU with its HSE and load caps, one anchor",
    "class": "mcu_cluster",
    "elements": [
        {"role": "MCU", "ref": "U1", "dx_mm": 0.0, "dy_mm": 0.0, "anchor": True},
        {"role": "HSE crystal", "dx_mm": -8.5, "dy_mm": 0.0},
        {"role": "load cap", "dx_mm": -8.5, "dy_mm": 3.0, "side": "bottom"},
    ],
    "constraints": {"min_gap_mm": 2.0},
}


class PatternCase(unittest.TestCase):

    def setUp(self):
        self.home = tempfile.mkdtemp(prefix="pat_home_")
        self._old_home = os.environ.get("PCB_AGENT_HOME")
        self._old_switch = os.environ.get("PCB_AGENT_PATTERNS")
        os.environ["PCB_AGENT_HOME"] = self.home
        os.environ.pop("PCB_AGENT_PATTERNS", None)

    def tearDown(self):
        for key, value in (("PCB_AGENT_HOME", self._old_home),
                           ("PCB_AGENT_PATTERNS", self._old_switch)):
            if value is None:
                os.environ.pop(key, None)
            else:
                os.environ[key] = value
        shutil.rmtree(self.home, ignore_errors=True)

    def _verify(self, ident, reading="drc 0 violations", where="board_drc"):
        return tools.call_tool("pattern_verify",
                               {"id": ident, "confirm": True,
                                "reason": "checked on a real board",
                                "evidence": [{"from": where, "reading": reading}]})


class TestWhatCannotBeWrittenDown(PatternCase):

    def test_a_number_without_a_source_is_refused(self):
        payload = dict(RECIPE)
        payload["parameters"] = {"rd_ohm": {"value": 5100, "unit": "ohm"}}
        out = tools.call_tool("pattern_add", {"kind": "recipe", "entry": payload})
        self.assertEqual(out["status"], "error")
        self.assertIn("no source", out["message"])

    def test_a_bare_number_is_refused_too(self):
        payload = dict(RECIPE)
        payload["parameters"] = {"rd_ohm": 5100}
        out = tools.call_tool("pattern_add", {"kind": "recipe", "entry": payload})
        self.assertEqual(out["status"], "error")
        self.assertIn("not {value, unit, source}", out["message"])

    def test_a_layout_with_two_anchors_is_refused(self):
        payload = dict(LAYOUT)
        payload["elements"] = [dict(e) for e in LAYOUT["elements"]]
        payload["elements"][1]["anchor"] = True
        out = tools.call_tool("pattern_add", {"kind": "layout", "entry": payload})
        self.assertEqual(out["status"], "error")
        self.assertIn("exactly one element with anchor", out["message"])

    def test_a_layout_with_no_anchor_is_refused(self):
        payload = dict(LAYOUT)
        payload["elements"] = [dict(e) for e in LAYOUT["elements"]]
        payload["elements"][0]["anchor"] = False
        out = tools.call_tool("pattern_add", {"kind": "layout", "entry": payload})
        self.assertEqual(out["status"], "error")
        self.assertIn("exactly one element with anchor", out["message"])

    def test_a_missing_offset_is_not_read_as_zero(self):
        payload = dict(LAYOUT)
        payload["elements"] = [dict(e) for e in LAYOUT["elements"]]
        del payload["elements"][1]["dx_mm"]
        out = tools.call_tool("pattern_add", {"kind": "layout", "entry": payload})
        self.assertEqual(out["status"], "error")
        self.assertIn("missing offset is not zero", out["message"])

    def test_the_same_id_is_not_overwritten(self):
        tools.call_tool("pattern_add", {"kind": "recipe", "entry": RECIPE})
        out = tools.call_tool("pattern_add", {"kind": "recipe", "entry": RECIPE})
        self.assertEqual(out["status"], "error")
        self.assertIn("already exists", out["message"])


class TestPromotionIsTheOnlyWayUp(PatternCase):

    def test_nothing_is_visible_until_it_is_verified(self):
        tools.call_tool("pattern_add", {"kind": "recipe", "entry": RECIPE})
        verified = tools.call_tool("pattern_recall", {})
        self.assertEqual(verified["value"]["returned"], 0)
        candidates = tools.call_tool("pattern_recall", {"status": "candidate"})
        self.assertEqual(candidates["value"]["returned"], 1)
        self.assertEqual(candidates["value"]["entries"][0]["status"], "candidate")

    def test_verify_needs_confirm(self):
        tools.call_tool("pattern_add", {"kind": "recipe", "entry": RECIPE})
        out = tools.call_tool("pattern_verify",
                              {"id": RECIPE["id"], "confirm": False,
                               "reason": "looks right to me"})
        self.assertEqual(out["status"], "error")
        self.assertIn("confirm=true", out["message"])

    def test_verify_needs_a_reading(self):
        tools.call_tool("pattern_add", {"kind": "recipe", "entry": RECIPE})
        out = tools.call_tool("pattern_verify",
                              {"id": RECIPE["id"], "confirm": True,
                               "reason": "checked on a real board"})
        self.assertEqual(out["status"], "error")
        self.assertIn("at least one evidence item", out["message"])

    def test_evidence_without_a_source_is_refused(self):
        tools.call_tool("pattern_add", {"kind": "recipe", "entry": RECIPE})
        out = tools.call_tool("pattern_verify",
                              {"id": RECIPE["id"], "confirm": True,
                               "reason": "checked on a real board",
                               "evidence": [{"reading": "it worked"}]})
        self.assertEqual(out["status"], "error")
        self.assertIn("needs both", out["message"])

    def test_a_verified_entry_says_who_and_why(self):
        tools.call_tool("pattern_add", {"kind": "recipe", "entry": RECIPE})
        out = self._verify(RECIPE["id"])
        self.assertEqual(out["status"], "ok", out)
        entry = out["value"]["entry"]
        self.assertEqual(entry["status"], "verified")
        self.assertEqual(entry["verified_by"]["reason"], "checked on a real board")
        again = self._verify(RECIPE["id"])
        self.assertEqual(again["status"], "error")
        self.assertIn("already verified", again["message"])


class TestPlanningIsArithmeticAndNothingElse(PatternCase):

    def _verified_layout(self):
        tools.call_tool("pattern_add", {"kind": "layout", "entry": LAYOUT})
        self._verify(LAYOUT["id"])

    def test_a_candidate_layout_cannot_be_planned_from(self):
        tools.call_tool("pattern_add", {"kind": "layout", "entry": LAYOUT})
        out = tools.call_tool("pattern_layout_plan",
                              {"id": LAYOUT["id"], "x_mm": 100, "y_mm": -110})
        self.assertEqual(out["status"], "error")
        self.assertIn("candidate", out["message"])

    def test_rotation_is_the_same_turn_in_both_axes(self):
        self._verified_layout()
        out = tools.call_tool("pattern_layout_plan",
                              {"id": LAYOUT["id"], "x_mm": 100, "y_mm": -110,
                               "rotation_deg": 90})
        self.assertEqual(out["status"], "ok", out)
        rows = {r["role"]: r for r in out["value"]["elements"]}
        self.assertAlmostEqual(rows["MCU"]["x_mm"], 100.0, places=4)
        self.assertAlmostEqual(rows["MCU"]["y_mm"], -110.0, places=4)
        # (-8.5, 0) turned by +90 deg becomes (0, -8.5) under this definition
        self.assertAlmostEqual(rows["HSE crystal"]["x_mm"], 100.0, places=4)
        self.assertAlmostEqual(rows["HSE crystal"]["y_mm"], -118.5, places=4)
        self.assertTrue(out["value"]["writes_nothing"])

    def test_mirror_flips_x_and_swaps_sides(self):
        self._verified_layout()
        out = tools.call_tool("pattern_layout_plan",
                              {"id": LAYOUT["id"], "x_mm": 100, "y_mm": -110,
                               "mirror": True})
        rows = {r["role"]: r for r in out["value"]["elements"]}
        self.assertAlmostEqual(rows["HSE crystal"]["x_mm"], 108.5, places=4)
        self.assertEqual(rows["load cap"]["side"], "top")

    def test_a_plan_against_a_real_board_reports_what_would_collide(self):
        self._verified_layout()
        payload = dict(LAYOUT)
        payload["id"] = "pat.layout.collides"
        payload["elements"] = [dict(e) for e in LAYOUT["elements"]]
        # R1 really sits at (110, -110) on the sample board, inside its outline.
        payload["elements"].append({"role": "resistor", "ref": "R1",
                                    "dx_mm": 10.0, "dy_mm": 0.0})
        tools.call_tool("pattern_add", {"kind": "layout", "entry": payload})
        self._verify(payload["id"])
        out = tools.call_tool("pattern_layout_plan",
                              {"id": payload["id"], "x_mm": 100, "y_mm": -110,
                               "path": SAMPLE})
        self.assertEqual(out["status"], "ok", out)
        whys = " | ".join(c["why"] for c in out["value"]["conflicts"])
        self.assertIn("already on the board", whys)
        self.assertIn("under the", whys)

    def test_a_plan_outside_the_outline_is_reported(self):
        self._verified_layout()
        out = tools.call_tool("pattern_layout_plan",
                              {"id": LAYOUT["id"], "x_mm": 0, "y_mm": 0,
                               "path": SAMPLE})
        whys = " | ".join(c["why"] for c in out["value"]["conflicts"])
        self.assertIn("outside the board outline", whys)


class TestATemplateComesFromARealBoard(PatternCase):
    """The loop for layouts: read a cluster off a board, then plan it somewhere else.

    The sample board carries R1 at (110, -110) and C1 at (110, -120) in the canonical
    view, so the offset is a fact that can be checked, not a number typed by hand.
    """

    def test_offsets_are_read_from_the_board_and_land_as_a_candidate(self):
        out = tools.call_tool("pattern_layout_from_board",
                              {"path": SAMPLE, "anchor_ref": "R1"})
        self.assertEqual(out["status"], "ok", out)
        entry = out["value"]["entry"]
        self.assertEqual(entry["status"], "candidate")
        rows = {e["ref"]: e for e in entry["elements"]}
        self.assertTrue(rows["R1"]["anchor"])
        self.assertAlmostEqual(rows["C1"]["dx_mm"], 0.0, places=4)
        self.assertAlmostEqual(rows["C1"]["dy_mm"], -10.0, places=4)
        self.assertEqual(out["value"]["coordinate_system"], "canonical (mm, Y up)")

    def test_an_anchor_that_is_not_on_the_board_is_refused(self):
        out = tools.call_tool("pattern_layout_from_board",
                              {"path": SAMPLE, "anchor_ref": "U99"})
        self.assertEqual(out["status"], "error")
        self.assertIn("no footprint with reference", out["message"])

    def test_a_cluster_of_one_is_not_a_layout(self):
        out = tools.call_tool("pattern_layout_from_board",
                              {"path": SAMPLE, "anchor_ref": "R1", "refs": ["R1"]})
        self.assertEqual(out["status"], "error")
        self.assertIn("at least one more part", out["message"])

    def test_the_payload_goes_through_the_same_doors(self):
        made = tools.call_tool("pattern_layout_from_board",
                               {"path": SAMPLE, "anchor_ref": "R1"})["value"]["entry"]
        added = tools.call_tool("pattern_add", {"kind": "layout", "entry": made})
        self.assertEqual(added["status"], "ok", added)
        self.assertEqual(added["value"]["entry"]["status"], "candidate")
        self._verify(made["id"], reading="sample board, 2 footprints",
                     where=SAMPLE)
        plan = tools.call_tool("pattern_layout_plan",
                               {"id": made["id"], "x_mm": 100, "y_mm": -100})
        rows = {e["ref"]: e for e in plan["value"]["elements"]}
        self.assertAlmostEqual(rows["C1"]["x_mm"], 100.0, places=4)
        self.assertAlmostEqual(rows["C1"]["y_mm"], -110.0, places=4)
        self.assertIn("Y up", plan["value"]["coordinate_system"])


class TestIsItActuallyUsed(PatternCase):

    def test_uses_are_counted_and_unused_verified_entries_are_named(self):
        tools.call_tool("pattern_add", {"kind": "recipe", "entry": RECIPE})
        self._verify(RECIPE["id"])
        review = tools.call_tool("pattern_status", {})
        self.assertEqual(review["value"]["by_status"]["verified"], 1)
        self.assertEqual(review["value"]["verified_never_used"], [RECIPE["id"]])
        self.assertIn("never been applied", review["value"]["message"])
        tools.call_tool("pattern_use", {"id": RECIPE["id"], "outcome": "worked",
                                        "note": "two CC pins, drc clean"})
        review = tools.call_tool("pattern_status", {})
        self.assertEqual(review["value"]["uses"]["worked"], 1)
        self.assertEqual(review["value"]["verified_never_used"], [])

    def test_an_unknown_outcome_is_refused(self):
        tools.call_tool("pattern_add", {"kind": "recipe", "entry": RECIPE})
        out = tools.call_tool("pattern_use", {"id": RECIPE["id"], "outcome": "meh"})
        self.assertEqual(out["status"], "error")
        self.assertIn("outcome must be one of", out["message"])

    def test_the_switch_turns_the_whole_library_off(self):
        os.environ["PCB_AGENT_PATTERNS"] = "0"
        out = tools.call_tool("pattern_recall", {})
        self.assertEqual(out["status"], "error")
        self.assertEqual(out["reason_code"], "DATA_UNAVAILABLE")


if __name__ == "__main__":
    unittest.main()
