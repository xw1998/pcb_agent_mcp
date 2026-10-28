"""IEC 60664-1 creepage / clearance, and the bootstrapping that produced it.

The point of these tests is not "does 250 V return 2.5 mm" alone - a wrong table
can hit one number. They are built in three layers:

* gold values read off the compiled reference implementation, chosen on both
  sides of a step boundary, so an off-by-one in the ladder lookup shows up;
* rules that a mis-transcribed table cannot satisfy (PD4 is not a quantity,
  clearance refuses instead of keeping the surviving branch, reinforced is 2x);
* a mutation layer - damage the table on purpose and require the validator to
  reject it. Without that layer a validator can pass by checking nothing.

The numbers themselves were recovered by compiling KiCad 10.0's
pcb_calculator/calculator_panels/iec60664.cpp and probing the raw ladder
functions (207k probe points, zero mismatches); see _scratch/iec_ref/.
"""
import io
import json
import os
import shutil
import tempfile
import unittest

from pcb_agent_mcp.calc import iec60664 as ie
from pcb_agent_mcp.calc import safety_table
from pcb_agent_mcp.calc import creepage as cr


def _value(result):
    assert result.get("status") == "ok", result
    return float(result["value"])


class TestCreepageLadder(unittest.TestCase):
    def test_gold_steps_pd2_iiia(self):
        # Table F.5, PD2 / material group IIIa, no PCB-material column.
        # Expected values are what gen7.exe (the compiled KiCad implementation,
        # exact same inputs) printed, not what this port computes.
        self.assertEqual(_value(ie.creepage_mm(100)), 1.4)
        self.assertEqual(_value(ie.creepage_mm(125)), 1.5)
        self.assertEqual(_value(ie.creepage_mm(160)), 1.6)
        self.assertEqual(_value(ie.creepage_mm(250)), 2.5)
        self.assertEqual(_value(ie.creepage_mm(320)), 3.2)

    def test_step_is_upper_bounded_not_lower(self):
        # 160.0 exactly is still the 1.6 step; just above it the computed
        # v/100 segment takes over (compiled code: 160.001 -> 1.60001)
        self.assertEqual(_value(ie.creepage_mm(160.0)), 1.6)
        self.assertEqual(_value(ie.creepage_mm(160.001)), 1.60001)

    def test_pcb_material_column_is_used_when_it_applies(self):
        over_board = _value(ie.creepage_mm(100, pollution_degree=1,
                                           material_group="I", pcb_material=True))
        in_air = _value(ie.creepage_mm(100, pollution_degree=1,
                                       material_group="I", pcb_material=False))
        self.assertEqual(over_board, 0.1)     # compiled reference: 0.1 mm
        self.assertEqual(in_air, 0.25)        # compiled reference: 0.25 mm

    def test_pcb_material_flag_is_ignored_above_1000_v(self):
        r = ie.creepage_mm(1200, pollution_degree=1, material_group="I",
                           pcb_material=True)
        self.assertIn("pcb_material_note", r)
        self.assertIn("1000 V", r["pcb_material_note"])
        # and the number must equal the non-PCB column
        self.assertEqual(_value(r), _value(ie.creepage_mm(
            1200, pollution_degree=1, material_group="I", pcb_material=False)))

    def test_pcb_material_flag_is_ignored_at_pd3(self):
        r = ie.creepage_mm(250, pollution_degree=3, material_group="I",
                           pcb_material=True)
        self.assertIn("PD3", r["pcb_material_note"])
        # and the number equals the non-PCB column (compiled reference: 3.2 mm)
        self.assertEqual(_value(r), 3.2)
        self.assertEqual(_value(r), _value(ie.creepage_mm(
            250, pollution_degree=3, material_group="I", pcb_material=False)))

    def test_reinforced_doubles_the_table_value(self):
        basic = _value(ie.creepage_mm(250, insulation="basic"))
        reinf = _value(ie.creepage_mm(250, insulation="reinforced"))
        self.assertEqual(basic, 2.5)
        self.assertAlmostEqual(reinf, 5.0, places=9)

    def test_pd4_creepage_is_out_of_scope_not_1_6_mm(self):
        r = ie.creepage_mm(100, pollution_degree=4)
        self.assertEqual(r["status"], "error")
        self.assertEqual(r["reason_code"], "STANDARD_SCOPE")
        self.assertIn("1.6 mm", r["hint"])   # names the artefact it refuses to copy

    def test_past_the_end_of_the_ladder_is_refused(self):
        r = ie.creepage_mm(200000)
        self.assertEqual(r["status"], "error")
        self.assertEqual(r["reason_code"], "OUT_OF_VALIDITY")

    def test_linear_segment_is_really_linear(self):
        # PD2 x IIIa above 160 V rms is v/100 in the source (line 1057):
        # compiled code gives 175 -> 1.75, 1000 -> 10, 2000 -> 20
        for v in (170.0, 333.0, 1000.0, 2000.0):
            self.assertAlmostEqual(_value(ie.creepage_mm(v)), v / 100.0, places=9)
        # ... but the step at/below 160 is a table value, not v/100
        self.assertEqual(_value(ie.creepage_mm(160)), 1.6)
        self.assertAlmostEqual(_value(ie.creepage_mm(160.001)), 1.60001, places=6)
        # and the 50 kV step is back to a table value (500 mm), not v/100 (500)
        self.assertEqual(_value(ie.creepage_mm(50000)), 500.0)

    def test_creepage_says_it_is_not_the_whole_rule(self):
        r = ie.creepage_mm(250)
        self.assertIn("standards_note", r)
        self.assertIn("clearance", r["standards_note"])


class TestClearanceLadder(unittest.TestCase):
    def test_gold_peak_table_f8(self):
        # Table F.8 (peak). PD1 is used so the transient branch bottoms out at
        # 0.01 mm and the peak branch is what speaks (compiled gold: 0.5 kV ->
        # 0.04, 1 kV -> 0.26, 2 kV -> 1.27, 50 kV -> 100).
        self.assertEqual(_value(ie.clearance_mm(500, transient_v=0.0,
                                               pollution_degree=1)), 0.04)
        self.assertEqual(_value(ie.clearance_mm(1000, transient_v=0.0,
                                               pollution_degree=1)), 0.26)
        self.assertEqual(_value(ie.clearance_mm(2000, transient_v=0.0,
                                               pollution_degree=1)), 1.27)
        self.assertEqual(_value(ie.clearance_mm(50000, transient_v=0.0,
                                               pollution_degree=1)), 100.0)

    def test_low_end_of_peak_table_is_not_skipped(self):
        # The first recovery pass lost 0.04..0.25 kV because it reused a coarse
        # bisection's breakpoints.  Read the ladder directly here: through the
        # public API the placeholder transient branch (0.01 mm) would mask it.
        doc, _ = ie._load()
        ladder = doc["clearance_peak"]["PD1"]
        expect = [(0.04, 0.001), (0.05, 0.002), (0.06, 0.002), (0.1, 0.003),
                  (0.12, 0.004), (0.15, 0.005), (0.2, 0.006), (0.25, 0.008),
                  (0.33, 0.01)]
        for v, mm in expect:
            hit = ie._lookup(ladder, v)
            self.assertIsNotNone(hit, "peak %r kV fell off the ladder" % v)
            self.assertEqual(hit[0], mm, "peak %r kV" % v)

    def test_gold_transient_table_f2(self):
        # Table F.2 (transient withstand), compiled gold for PD2:
        # 1.5 kV -> 0.5, 2.5 kV -> 1.5, 40 kV -> 60, 50 kV -> 75
        peak = 1000.0   # 1 kV peak stays below every transient figure here
        self.assertEqual(_value(ie.clearance_mm(peak, transient_v=1500)), 0.5)
        self.assertEqual(_value(ie.clearance_mm(peak, transient_v=2500)), 1.5)
        self.assertEqual(_value(ie.clearance_mm(peak, transient_v=40000)), 60.0)
        self.assertEqual(_value(ie.clearance_mm(peak, transient_v=50000)), 75.0)

    def test_transient_wins_over_peak_when_it_is_worse(self):
        only_peak = _value(ie.clearance_mm(1000, transient_v=0.0))
        with_transient = _value(ie.clearance_mm(1000, transient_v=2500))
        self.assertEqual(only_peak, 0.26)
        self.assertEqual(with_transient, 1.5)

    def test_missing_transient_is_reported_not_hidden(self):
        r = ie.clearance_mm(1000)
        self.assertIn("transient_not_supplied", r)
        self.assertIsNone(r.get("clearance_from_transient_mm"))

    def test_one_branch_off_the_table_refuses_the_whole_answer(self):
        # 200 kV transient is past Table F.2's end. The peak branch survives, so a
        # lazy port would answer the peak figure; the reference implementation
        # sets BOTH branches to -1 and the port must refuse instead.
        r = ie.clearance_mm(1000, transient_v=200000)
        self.assertEqual(r["status"], "error")
        self.assertEqual(r["reason_code"], "OUT_OF_VALIDITY")

    def test_reinforced_prefers_the_series_then_1_6(self):
        # 2.5 kV is in the preferred series -> replaced by 4.0 kV
        r = ie.clearance_mm(1.0, transient_v=2500, insulation="reinforced")
        self.assertEqual(r.get("transient_reinforced_rule"), "preferred_series")
        self.assertAlmostEqual(r.get("transient_kv_used"), 4.0, places=12)
        # 2.4 kV is not -> multiplied by 1.6 = 3.84 kV
        r = ie.clearance_mm(1.0, transient_v=2400, insulation="reinforced")
        self.assertEqual(r.get("transient_reinforced_rule"), "times_1.6")
        self.assertAlmostEqual(r.get("transient_kv_used"), 3.84, places=12)

    def test_altitude_correction_applies_to_clearance(self):
        sea = _value(ie.clearance_mm(1000, transient_v=2500, altitude_m=0))
        high = _value(ie.clearance_mm(1000, transient_v=2500, altitude_m=5000))
        self.assertEqual(sea, 1.5)
        self.assertAlmostEqual(high, 1.5 * 1.48, places=6)   # Table A.2, 5000 m

    def test_absurd_altitude_is_refused(self):
        r = ie.clearance_mm(1000, transient_v=2500, altitude_m=30000)
        self.assertEqual(r["status"], "error")
        self.assertEqual(r["reason_code"], "OUT_OF_VALIDITY")


class TestSpacingAndGroove(unittest.TestCase):
    def test_spacing_takes_the_larger(self):
        r = ie.spacing(400, transient_v=2500)
        self.assertEqual(r["status"], "ok")
        self.assertGreaterEqual(r["creepage_mm"], r["clearance_mm"])
        self.assertEqual(r["value"], max(r["creepage_mm"], r["clearance_mm"]))
        self.assertEqual(r["governing"], "creepage")

    def test_groove_width_below_3mm_is_a_third(self):
        self.assertAlmostEqual(_value(ie.min_groove_width_mm(1.5)), 0.5, places=9)
        self.assertAlmostEqual(_value(ie.min_groove_width_mm(2.4)), 0.8, places=9)

    def test_groove_width_above_3mm_is_fixed(self):
        self.assertEqual(_value(ie.min_groove_width_mm(4.0, pollution_degree=1)), 0.25)
        self.assertEqual(_value(ie.min_groove_width_mm(4.0, pollution_degree=2)), 1.0)
        self.assertEqual(_value(ie.min_groove_width_mm(4.0, pollution_degree=3)), 1.5)

    def test_groove_width_at_pd4_is_out_of_scope(self):
        r = ie.min_groove_width_mm(4.0, pollution_degree=4)
        self.assertEqual(r["reason_code"], "STANDARD_SCOPE")

    def test_impulse_withstand_gold(self):
        self.assertEqual(_value(ie.impulse_withstand_v(250, "II")), 2500)
        self.assertEqual(_value(ie.impulse_withstand_v(230, "III")), 4000)


class TestStandardDispatch(unittest.TestCase):
    def test_iec60664_spellings_are_recognised(self):
        for spelling in ("IEC 60664-1", "iec60664", "IEC60664-1:2020",
                         "iec 60664-1:2020-05"):
            r = cr.creepage_required(250, safety_standard=spelling)
            self.assertEqual(r["status"], "ok", spelling)
            self.assertEqual(float(r["value"]), 2.5, spelling)

    def test_other_families_still_fail_closed(self):
        for std in ("IEC 62368-1", "IEC 61010-1", "UL 60950", "IPC-2221"):
            r = cr.creepage_required(250, safety_standard=std)
            self.assertEqual(r["status"], "error", std)
            self.assertEqual(r["reason_code"], "SAFETY_TABLE_MISSING", std)

    def test_missing_standard_is_refused(self):
        r = cr.clearance_required(250)
        self.assertEqual(r["reason_code"], "SAFETY_TABLE_MISSING")

    def test_spacing_required_goes_through_the_public_entry_point(self):
        r = cr.spacing_required(400, transient_v=2500,
                                safety_standard="IEC 60664-1")
        self.assertEqual(r["status"], "ok")
        self.assertGreaterEqual(r["creepage_mm"], r["clearance_mm"])


class TestTableValidator(unittest.TestCase):
    def setUp(self):
        with io.open(ie.DATA_PATH, encoding="utf-8") as fh:
            self.doc = json.load(fh)

    def test_the_shipped_table_passes(self):
        report = safety_table.validate_iec60664(self.doc)
        self.assertTrue(report["usable"], report["failed"])
        self.assertEqual(report["ladders"], 32)
        self.assertEqual(report["steps"], 808)

    def test_a_duplicated_threshold_is_caught(self):
        doc = json.loads(json.dumps(self.doc))
        entry = doc["creepage"]["PD2|MG_IIIa|pcb0"]
        entry["steps"][3][0] = entry["steps"][2][0]   # two steps, one threshold
        report = safety_table.validate_iec60664(doc)
        self.assertFalse(report["usable"])
        self.assertIn("ladders_sane_and_monotone", report["failed"])

    def test_a_falling_ladder_is_caught(self):
        doc = json.loads(json.dumps(self.doc))
        doc["creepage"]["PD2|MG_IIIa|pcb0"]["steps"][4][1] = 0.001
        report = safety_table.validate_iec60664(doc)
        self.assertFalse(report["usable"])

    def test_max_v_must_match_the_last_threshold(self):
        doc = json.loads(json.dumps(self.doc))
        doc["creepage"]["PD2|MG_IIIa|pcb0"]["max_v"] = 99999.0
        report = safety_table.validate_iec60664(doc)
        self.assertFalse(report["usable"])

    def test_a_linear_segment_through_nowhere_is_caught(self):
        doc = json.loads(json.dumps(self.doc))
        doc["creepage"]["PD2|MG_IIIa|pcb0"]["steps"][-2][1] = {
            "slope": 0.01, "intercept": 5.0}
        report = safety_table.validate_iec60664(doc)
        self.assertFalse(report["usable"])

    def test_missing_pollution_degree_is_caught(self):
        doc = json.loads(json.dumps(self.doc))
        del doc["clearance_transient"]["PD3"]
        report = safety_table.validate_iec60664(doc)
        self.assertFalse(report["usable"])

    def test_table_f8_must_not_depend_on_pollution_degree(self):
        doc = json.loads(json.dumps(self.doc))
        doc["clearance_peak"]["PD2"]["steps"][0][1] = 0.5
        report = safety_table.validate_iec60664(doc)
        self.assertFalse(report["usable"])

    def test_unverified_flag_is_caught(self):
        doc = json.loads(json.dumps(self.doc))
        doc["verified"] = False
        report = safety_table.validate_iec60664(doc)
        self.assertFalse(report["usable"])

    def test_check_which_routes_iec60664(self):
        report = safety_table.check_which("iec60664")
        self.assertTrue(report["usable"], report["failed"])
        self.assertEqual(report["edition"], "IEC 60664-1:2020-05")

    def test_a_corrupt_file_is_refused_at_load_time(self):
        tmp = tempfile.mkdtemp()
        try:
            bad = os.path.join(tmp, "iec60664_tables.json")
            doc = json.loads(json.dumps(self.doc))
            doc["schema"] = 2
            with io.open(bad, "w", encoding="utf-8") as fh:
                json.dump(doc, fh)
            ie.reset_cache()
            saved = ie.DATA_PATH
            try:
                ie.DATA_PATH = bad
                r = ie.creepage_mm(250)
                self.assertEqual(r["status"], "error")
                self.assertEqual(r["reason_code"], "SAFETY_TABLE_MISSING")
            finally:
                ie.DATA_PATH = saved
                ie.reset_cache()
        finally:
            shutil.rmtree(tmp, ignore_errors=True)


if __name__ == "__main__":
    unittest.main()
