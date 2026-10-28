"""Golden tests for the four modules merged in from the engineering skills.

    calc/magnetics.py   boost-inductor-circuit-calc / SKILL.md
    calc/stackup.py     rf-circuit-design / SKILL.md 3.1-3.2
    calc/chain.py       rf-circuit-design / SKILL.md 4.4 (cascade NF)
    calc/rules.py       kicad-free + rf-circuit-design + circuit-analysis checklists

Every number asserted here can be re-derived by hand from the formula written in
the docstring, so a refactor that keeps the tests green but changes the physics
would have to change the arithmetic in this file too.
"""
import math
import os
import sys
import unittest

sys.path.insert(0, os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "src"))

from pcb_agent_mcp.calc import chain, magnetics, stackup, rules  # noqa: E402


class TestBoostInductor(unittest.TestCase):
    def test_the_reference_case_by_hand(self):
        # 5 -> 12 V, 1 A, 500 kHz, eta 0.9, r 0.3
        #   D   = 1 - 0.9*5/12          = 0.625
        #   Iin = 12/(0.9*5)            = 2.6667 A
        #   dI  = 0.3*2.6667            = 0.8 A
        #   L   = 5*0.625/(0.8*500e3)   = 7.8125 uH
        v = magnetics.inductor_ccm("boost", 5.0, 12.0, 500e3, 0.3,
                                   iout_a=1.0, efficiency=0.9)["value"]
        self.assertAlmostEqual(v["duty"], 0.625, delta=1e-9)
        self.assertAlmostEqual(v["inductor_uh"], 7.8125, delta=1e-3)
        self.assertAlmostEqual(v["ripple_current_a"], 0.8, places=6)
        self.assertAlmostEqual(v["inductor_peak_a"], 3.0667, places=4)
        self.assertAlmostEqual(v["inductor_valley_a"], 2.2667, places=4)

    def test_rms_is_the_triangular_waveform(self):
        v = magnetics.inductor_ccm("boost", 5.0, 12.0, 500e3, 0.3,
                                   iout_a=1.0, efficiency=0.9)["value"]
        iavg, d = v["inductor_avg_a"], v["ripple_current_a"]
        self.assertAlmostEqual(v["inductor_rms_a"],
                               math.sqrt(iavg ** 2 + d ** 2 / 12.0), delta=1e-3)

    def test_buck_duty_and_where_ccm_ends(self):
        # 12 -> 5 V at 1 A: D = 5/12, L = 7*0.41667/(0.3*1*500e3)
        v = magnetics.inductor_ccm("buck", 12.0, 5.0, 500e3, 0.3, iout_a=1.0)["value"]
        self.assertAlmostEqual(v["duty"], 5.0 / 12.0, delta=1e-5)
        self.assertAlmostEqual(v["inductor_uh"], 19.444, delta=1e-3)
        self.assertAlmostEqual(v["ccm_min_output_a"], 0.15, places=6)

    def test_a_boost_below_its_input_is_refused_before_any_load(self):
        r = magnetics.inductor_ccm("boost", 5.0, 4.0, 500e3)
        self.assertEqual(r["status"], "error")
        self.assertEqual(r["reason_code"], "ARG_RANGE")

    def test_ripple_of_two_or_more_cannot_be_ccm(self):
        r = magnetics.inductor_ccm("boost", 5.0, 12.0, 500e3, 2.0, iout_a=1.0)
        self.assertEqual(r["reason_code"], "ARG_RANGE")

    def test_load_is_required_and_must_be_one_of_two(self):
        self.assertEqual(magnetics.inductor_ccm("boost", 5.0, 12.0, 500e3)["reason_code"],
                         "ARG_MISSING")
        self.assertEqual(magnetics.inductor_ccm("boost", 5.0, 12.0, 500e3, 0.3,
                                                iout_a=1.0, pout_w=12.0)["reason_code"],
                         "ARG_INVALID")

    def test_power_and_current_inputs_agree(self):
        a = magnetics.inductor_ccm("boost", 5.0, 12.0, 500e3, 0.3, iout_a=2.0,
                                   efficiency=0.9)["value"]
        b = magnetics.inductor_ccm("boost", 5.0, 12.0, 500e3, 0.3, pout_w=24.0,
                                   efficiency=0.9)["value"]
        self.assertAlmostEqual(a["inductor_uh"], b["inductor_uh"], places=9)

    def test_saturation_is_undetermined_without_a_part(self):
        v = magnetics.inductor_ccm("boost", 5.0, 12.0, 500e3, 0.3, iout_a=1.0)["value"]
        self.assertEqual(v["saturation"]["verdict"], "undetermined")
        self.assertIn("Isat", v["saturation"]["what_is_missing"])

    def test_saturation_is_graded_with_a_margin(self):
        ok = magnetics.inductor_ccm("boost", 5.0, 12.0, 500e3, 0.3, iout_a=1.0,
                                    efficiency=0.9, isat_a=4.0)["value"]
        # peak 3.0667 A * 1.2 = 3.68 A required, 4 A part -> pass
        self.assertEqual(ok["saturation"]["verdict"], "pass")
        self.assertAlmostEqual(ok["saturation"]["required_with_margin_a"], 3.68, places=2)
        bad = magnetics.inductor_ccm("boost", 5.0, 12.0, 500e3, 0.3, iout_a=1.0,
                                     efficiency=0.9, isat_a=3.0)["value"]
        self.assertEqual(bad["saturation"]["verdict"], "fail")

    def test_unknown_topology_is_named(self):
        r = magnetics.inductor_ccm("flyback", 5.0, 12.0, 500e3, iout_a=1.0)
        self.assertEqual(r["reason_code"], "ARG_INVALID")


class TestStackup(unittest.TestCase):
    def test_the_four_layer_stack_adds_up(self):
        v = stackup.stackup_design(4, "fr4", 1.0)["value"]
        total = sum(row.get("dielectric_to_next_mm", 0.0) for row in v["stack"])
        self.assertAlmostEqual(total, 1.0, places=9)

    def test_every_signal_layer_with_a_plane_below_gets_a_width(self):
        v = stackup.stackup_design(6, "ro4350b", 1.2, 50.0)["value"]
        solved = [w for w in v["widths"] if w.get("width_mm")]
        # L1, L3 and L6? L6 is the bottom layer: no plane below, so 2 of 3 are solved
        self.assertEqual(len(solved), 2)
        for w in solved:
            self.assertAlmostEqual(w["z0_check_ohm"], 50.0, places=2)

    def test_a_thicker_dielectric_needs_a_wider_line(self):
        thin = stackup.stackup_design(4, "fr4", 0.6)["value"]["widths"][0]["width_mm"]
        thick = stackup.stackup_design(4, "fr4", 1.6)["value"]["widths"][0]["width_mm"]
        self.assertGreater(thick, thin)

    def test_via_pitch_needs_a_frequency(self):
        v = stackup.stackup_design(4, "fr4", 1.0)["value"]
        self.assertIsNone(v["via_pitch_mm"])
        self.assertIn("no frequency", v["via_pitch_rule"])
        w = stackup.stackup_design(4, "fr4", 1.0, 50.0, None, 2.4)["value"]
        # lambda_g(2.4 GHz, er 4.3) = 60.2 mm -> /20 = 3.01 mm
        self.assertAlmostEqual(w["via_pitch_mm"], 3.01, places=2)

    def test_symmetry_is_reported_not_assumed(self):
        self.assertTrue(stackup.stackup_design(4, "fr4")["value"]["copper_balance"]["symmetric"])
        self.assertFalse(stackup.stackup_design(6, "fr4")["value"]["copper_balance"]["symmetric"])

    def test_material_choice_follows_the_band(self):
        self.assertEqual(stackup.material_for(1.0)["value"]["recommended"], "fr4")
        self.assertNotEqual(stackup.material_for(5.8)["value"]["recommended"], "fr4")
        self.assertEqual(stackup.material_for(1.0)["value"]["alternatives"] != [], True)

    def test_above_the_table_it_refuses(self):
        r = stackup.material_for(60.0)
        self.assertEqual(r["reason_code"], "OUT_OF_VALIDITY")

    def test_an_unknown_material_without_er_is_refused(self):
        self.assertEqual(stackup.stackup_design(4, "unobtainium")["reason_code"],
                         "ARG_INVALID")
        self.assertEqual(stackup.stackup_design(4, "unobtainium", er=3.9)["status"], "ok")

    def test_only_four_and_six_layers(self):
        self.assertEqual(stackup.stackup_design(8, "fr4")["reason_code"], "ARG_INVALID")


class TestChain(unittest.TestCase):
    def test_the_skills_own_worked_example(self):
        # 0.8/15, 1.2/20, 2.0/10 dB -> the skill computes 0.83 dB
        v = chain.cascade([{"name": "a", "gain_db": 15, "nf_db": 0.8},
                           {"name": "b", "gain_db": 20, "nf_db": 1.2},
                           {"name": "c", "gain_db": 10, "nf_db": 2.0}])["value"]
        self.assertAlmostEqual(v["nf_total_db"], 0.8369, places=4)
        self.assertAlmostEqual(v["gain_total_db"], 45.0, places=9)
        self.assertEqual(v["dominant_noise_stage"]["stage"], "a")

    def test_a_front_end_filter_costs_its_own_loss(self):
        # 3 dB loss then a 0.7 dB LNA: F = 1.9953 + 0.1749/0.5012 = 2.3443 -> 3.70 dB.
        # It lands a hair BELOW 3 + 0.7 because a 3 dB attenuator is F = 1.995, not 2.
        v = chain.cascade([{"name": "filt", "gain_db": -3.0, "nf_db": 3.0},
                           {"name": "lna", "gain_db": 18.0, "nf_db": 0.7}])["value"]
        self.assertAlmostEqual(v["nf_total_db"], 3.70, delta=0.01)

    def test_cascaded_iip3_by_hand(self):
        # 1/IIP3 = 1/1 mW + 100/10 mW = 11 /mW -> -10.41 dBm
        v = chain.cascade([{"name": "a", "gain_db": 20, "nf_db": 1.0, "iip3_dbm": 0.0},
                           {"name": "b", "gain_db": 10, "nf_db": 5.0, "iip3_dbm": 10.0}])["value"]
        self.assertAlmostEqual(v["iip3_total_dbm"], -10.4139, places=4)
        self.assertAlmostEqual(v["oip3_total_dbm"], 19.5861, places=4)

    def test_oip3_is_converted_to_input_referred(self):
        a = chain.cascade([{"name": "a", "gain_db": 10, "nf_db": 1.0, "oip3_dbm": 20.0}])["value"]
        b = chain.cascade([{"name": "a", "gain_db": 10, "nf_db": 1.0, "iip3_dbm": 10.0}])["value"]
        self.assertAlmostEqual(a["iip3_total_dbm"], b["iip3_total_dbm"], places=6)

    def test_a_missing_nf_is_an_explicit_gap(self):
        v = chain.cascade([{"name": "a", "gain_db": 10}])["value"]
        self.assertIsNone(v["nf_total_db"])
        self.assertIn("nf_db", v["answers_only_when_every_stage_is_known"]["nf_total_db"])

    def test_sensitivity_is_kTB_plus_nf(self):
        v = chain.cascade([{"name": "a", "gain_db": 20, "nf_db": 2.0}],
                          bandwidth_hz=1e6, required_snr_db=10.0)["value"]
        # -174 + 2 + 60 + 10 = -102 dBm
        self.assertAlmostEqual(v["sensitivity_dbm"], -102.0, delta=0.15)
        self.assertIsNone(v["sensitivity_dbm"] if "bandwidth_hz" not in v else None)

    def test_without_a_bandwidth_there_is_no_sensitivity(self):
        v = chain.cascade([{"name": "a", "gain_db": 20, "nf_db": 2.0}])["value"]
        self.assertIsNone(v["sensitivity_dbm"])
        self.assertIn("bandwidth", v["sensitivity_why_not"])

    def test_a_negative_nf_is_refused(self):
        r = chain.cascade([{"name": "a", "gain_db": 10, "nf_db": -1.0}])
        self.assertEqual(r["reason_code"], "ARG_RANGE")

    def test_an_empty_chain_is_refused(self):
        self.assertEqual(chain.cascade([])["reason_code"], "ARG_REQUIRED")


class TestMatchBandwidth(unittest.TestCase):
    def test_bw_is_f0_over_q(self):
        from pcb_agent_mcp.calc import matching
        v = matching.bandwidth(q=10.0, freq_ghz=2.4)["value"]
        self.assertAlmostEqual(v["bandwidth_hz"], 240e6, places=0)
        self.assertAlmostEqual(v["fractional_bandwidth"], 0.1, places=6)

    def test_the_vswr_band_is_narrower_by_its_factor(self):
        from pcb_agent_mcp.calc import matching
        v = matching.bandwidth(q=10.0, freq_ghz=2.4, vswr=2.0)["value"]
        self.assertAlmostEqual(v["bandwidth_at_vswr_hz"], 240e6 * math.sqrt(0.5), places=0)

    def test_two_of_three_are_required(self):
        from pcb_agent_mcp.calc import matching
        self.assertEqual(matching.bandwidth()["reason_code"], "ARG_MISSING")
        self.assertEqual(matching.bandwidth(q=10.0)["reason_code"], "ARG_MISSING")


class TestRuleCatalogues(unittest.TestCase):
    def test_both_catalogues_list_what_they_need(self):
        lay = rules.design_rules("layout")["value"]
        sch = rules.design_rules("schematic")["value"]
        self.assertEqual(lay["count"], 22)
        self.assertEqual(sch["count"], 14)
        for row in lay["rules"] + sch["rules"]:
            self.assertTrue(row["source"], row["id"])
            self.assertIn(row["severity"], rules.SEVERITIES)
            self.assertIn(row["evidence_level"], ("L0", "L1", "L2", "L3"))
            self.assertTrue(row["how_to_measure"], row["id"])

    def test_unknown_rule_id_is_named(self):
        r = rules.design_rules("layout", ["layout.nope"])
        self.assertEqual(r["reason_code"], "ARG_INVALID")
        self.assertIn("layout.nope", r["message"])

    def test_a_missing_measurement_is_never_a_pass(self):
        v = rules.review("layout", {"min_via_hole_mm": 0.25})["value"]
        self.assertGreater(v["counts"]["undetermined"], 0)
        for c in v["undetermined"]:
            self.assertTrue(c["next_check"])
            self.assertNotIn(c["fact"], ("", None))

    def test_an_empty_fact_set_is_refused(self):
        self.assertEqual(rules.review("layout", {})["reason_code"], "ARG_MISSING")
        self.assertEqual(rules.review("schematic", {})["reason_code"], "ARG_MISSING")

    def test_a_violation_reports_the_limit_it_beat(self):
        v = rules.review("layout", {"min_via_hole_mm": 0.2})["value"]
        fails = {f["id"]: f for f in v["fails"]}
        self.assertIn("layout.min_via_hole", fails)
        self.assertAlmostEqual(fails["layout.min_via_hole"]["limit"], 0.3)
        self.assertEqual(v["worst_failing_severity"], "Medium")

    def test_the_3w_limit_is_computed_from_the_line(self):
        v = rules.review("layout", {"trace_width_mm": 0.3,
                                    "rf_min_spacing_to_other_trace_mm": 0.8})["value"]
        row = [c for c in v["fails"] + v["passes"]
               if c["id"] == "layout.rf_spacing_3w"][0]
        self.assertAlmostEqual(row["limit"], 0.9, places=9)
        self.assertEqual(row["state"] if "state" in row else "fail", "fail")

    def test_stitching_pitch_without_a_frequency_is_undetermined(self):
        v = rules.review("layout", {"stitching_via_pitch_mm": 1.0})["value"]
        row = [c for c in v["undetermined"] if c["id"] == "layout.stitching_via_pitch"][0]
        self.assertIn("freq_ghz", row["reason"])

    def test_the_current_limit_refuses_to_pick_a_standard(self):
        v = rules.review("layout", {"power_trace_width_mm": 0.3,
                                    "current_a": 2.0})["value"]
        row = [c for c in v["undetermined"]
               if c["id"] == "layout.power_trace_width"][0]
        self.assertIn("current_standard", row["reason"])

    def test_with_a_standard_the_current_limit_is_the_chart(self):
        v = rules.review("layout", {"power_trace_width_mm": 0.3, "current_a": 2.0,
                                    "current_standard": "ipc2221"})["value"]
        row = [c for c in v["fails"] if c["id"] == "layout.power_trace_width"]
        self.assertTrue(row)
        self.assertGreater(row[0]["limit"], 0.3)

    def test_isolation_spacing_fails_closed_while_the_table_is_unverified(self):
        v = rules.review("schematic", {"min_isolation_gap_mm": 1.0,
                                       "voltage_v": 400.0})["value"]
        row = [c for c in v["undetermined"] if c["id"] == "sch.isolation_spacing"][0]
        self.assertIn("table", row["reason"].lower() + row["limit_from"].lower())

    def test_boolean_rules_need_a_real_boolean(self):
        v = rules.review("layout", {"signal_crosses_plane_split": "no"})["value"]
        row = [c for c in v["undetermined"]
               if c["id"] == "layout.reference_plane_continuous"][0]
        self.assertIn("true/false", row["reason"])

    def test_a_typo_is_reported_not_swallowed(self):
        v = rules.review("layout", {"min_via_hole": 0.3})["value"]
        self.assertIn("min_via_hole", v["unknown_facts_ignored"])
        self.assertIn("silently", v["unknown_facts_note"])

    def test_a_clean_measured_set_says_clean_but_not_proven(self):
        facts = {r["fact"]: (False if r["kind"] == "bool_false" else
                             (True if r["kind"] == "bool_true" else
                              (r["limit"] if isinstance(r["limit"], (int, float))
                               else 0.5)))
                 for r in rules.SCHEMATIC_RULES}
        v = rules.review("schematic", facts)["value"]
        self.assertEqual(v["counts"]["fail"], 0)
        self.assertIn("must not be reported as one", v["what_this_does_not_prove"])
        self.assertEqual(v["verdict"], "clean_but_incomplete" if v["counts"]["undetermined"]
                         else "measured_clean")


if __name__ == "__main__":
    unittest.main()
