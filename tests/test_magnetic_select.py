"""Tests for calc/magnetics.select_core and the vendor table behind it (0.0.55).

The discipline is the same as the rest of the suite: no expected number is taken
from a run of the code under test.

  * the geometric relations (L = AL*N^2, B = L*Ipk/(N*Ae), the series-reluctance
    gap) are recomputed here from the table's own vendor numbers;
  * every refusal is paired with a control that DOES answer, so a test cannot
    pass by the feature being dead;
  * the loss behaviour is pinned by the one thing that would silently ruin it -
    the reference point's CONDITION. U20/16/7 is measured at 25 kHz, not 100 kHz;
    an implementation that forgets the condition returns 1.9 W at both.

If the table grows a Steinmetz triple, `test_no_steinmetz_triple_is_invented`
fails on purpose: the extrapolation path must then be re-decided, not silently
inherited.
"""
import io
import json
import math
import os
import sys
import unittest

sys.path.insert(0, os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "src"))

from pcb_agent_mcp.calc import magnetics as M  # noqa: E402

MU0 = 4e-7 * math.pi


def _table():
    with io.open(M.DATA_PATH, encoding="utf-8") as fh:
        return json.load(fh)


def _core(part):
    for c in _table()["cores"]:
        if c["part"] == part:
            return c
    raise AssertionError("core %s missing from the table" % part)


def _mat(name):
    for m in _table()["materials"]:
        if m["name"] == name:
            return m
    raise AssertionError("material %s missing from the table" % name)


class TheTableIsEvidenceNotMemory(unittest.TestCase):
    def test_every_row_names_its_source(self):
        t = _table()
        self.assertTrue(t["verified"])
        for row in t["materials"] + t["cores"]:
            self.assertTrue(row.get("source"), row.get("name") or row.get("part"))
            for url in row["source"]:
                self.assertTrue(str(url).startswith("http"), url)

    def test_a_measured_loss_point_carries_its_condition(self):
        # a loss number without (B, f, T) is a number about nothing
        for c in _table()["cores"]:
            ref = c.get("loss_ref")
            if ref:
                for k in ("p_w", "b_mt", "f_khz", "temp_c"):
                    self.assertIsNotNone(ref.get(k), (c["part"], k))

    def test_not_every_core_is_measured_at_100_khz(self):
        # the negative control for the condition being DATA: if this ever stops
        # being true, the "condition matters" test below loses its witness
        freqs = {c["loss_ref"]["f_khz"] for c in _table()["cores"] if c.get("loss_ref")}
        self.assertIn(25, freqs)
        self.assertIn(100, freqs)

    def test_no_steinmetz_triple_is_invented(self):
        # no vendor fit was reachable in a quotable form on 2026-10-31. If one is
        # ever transcribed, this test is the tripwire to re-decide the scaling path.
        for m in _table()["materials"]:
            self.assertIsNone(m.get("steinmetz"), m["name"])
            self.assertTrue(m.get("steinmetz_note"), m["name"])

    def test_a_missing_number_is_null_and_says_why(self):
        # QU 100/40/20: the part page had the mass but not the effective params
        c = _core("B67410A0105X087")
        self.assertIsNone(c["ae_mm2"])
        self.assertTrue(c["incomplete_note"])
        # RM10/I-3C90: the ungapped 3C90 AL row was not captured
        self.assertIsNone(_core("RM10/I-3C90")["al_nh"])
        self.assertTrue(_core("RM10/I-3C90")["al_note"])


class TheTurnsAreTwoConstraints(unittest.TestCase):
    def test_turns_satisfy_both_relations(self):
        L, ipk, blim = 100e-6, 3.0, 0.30
        r = M.select_core(L, ipk, b_max_t=blim)
        self.assertEqual(r["status"], "ok")
        self.assertGreater(r["value"]["feasible_count"], 0)
        for c in r["value"]["candidates"]:
            ae = (c.get("flux_area_mm2") or c["ae_mm2"]) * 1e-6
            n = c["turns"]
            # B = L*Ipk/(N*A) <= limit, recomputed from the table (A is Amin where
            # the vendor publishes one: that is where the flux actually peaks)
            self.assertLessEqual(L * ipk / (n * ae), blim * (1 + 1e-12), c["part"])
            # and the peak the tool reports is that same number
            self.assertAlmostEqual(c["b_peak_t"], L * ipk / (n * ae), places=6)
            # L = AL*N^2 wants at least this many turns
            if c["al_nh"]:
                self.assertGreaterEqual(n, math.floor(math.sqrt(L / (c["al_nh"] * 1e-9))))

    def test_where_flux_decides_the_answer_is_one_turn_short_of_failing(self):
        # the negative control: the answer is minimal, not merely sufficient. Only
        # where the flux constraint is the binding one - a part whose AL already
        # needs more turns is bound by L = AL*N^2 instead.
        L, ipk, blim = 100e-6, 3.0, 0.30
        r = M.select_core(L, ipk, b_max_t=blim)
        checked = 0
        for c in r["value"]["candidates"]:
            ae = (c.get("flux_area_mm2") or c["ae_mm2"]) * 1e-6
            if c["turns_from_flux"] <= (c["turns_from_al"] or 0):
                continue
            checked += 1
            self.assertGreater(L * ipk / ((c["turns"] - 1) * ae), blim, c["part"])
        self.assertGreater(checked, 0)

    def test_a_smaller_flux_limit_needs_more_turns(self):
        L, ipk = 100e-6, 3.0
        a = M.select_core(L, ipk, b_max_t=0.30, material="N87")
        b = M.select_core(L, ipk, b_max_t=0.15, material="N87")
        ca = {c["part"]: c["turns"] for c in a["value"]["candidates"]}
        cb = {c["part"]: c["turns"] for c in b["value"]["candidates"]}
        common = set(ca) & set(cb)
        self.assertTrue(common)
        for p in common:
            self.assertGreaterEqual(cb[p], ca[p], p)

    def test_a_thousandfold_inductance_is_a_thousandfold_flux_turns(self):
        # the negative control for a unit slip (mH typed as H, or uH as H): with
        # the flux binding, N = L*Ipk/(B*Ae) is linear in L
        small = M.select_core(100e-6, 3.0, b_max_t=0.3, material="N87",
                              shape="PQ 20/16")
        big = M.select_core(100e-3, 3.0, b_max_t=0.3, material="N87",
                            shape="PQ 20/16")
        n_small = small["value"]["candidates"][0]["turns"]
        n_big = big["value"]["candidates"][0]["turns"]
        self.assertGreater(n_big / n_small, 950)
        self.assertLess(n_big / n_small, 1050)
        # the reported peak stays pinned to the limit either way
        self.assertAlmostEqual(big["value"]["candidates"][0]["b_peak_t"], 0.3,
                               places=4)


class SaturationIsJudgedAgainstAPublishedBsat(unittest.TestCase):
    def test_a_caller_limit_above_the_material_bsat_is_rejected_by_name(self):
        # 0.6 T on N87, whose published Bsat at 100 C is 0.39 T: the numbers can be
        # made to satisfy the caller's limit, the material cannot
        over = M.select_core(100e-6, 3.0, b_max_t=0.6, material="N87",
                             temperature_c=100, shape="PQ 20/16")
        ok = M.select_core(100e-6, 3.0, b_max_t=0.3, material="N87",
                           temperature_c=100, shape="PQ 20/16")
        self.assertEqual(over["status"], "ok")
        self.assertEqual(over["value"]["feasible_count"], 0)
        judged = [x for x in over["value"]["rejected"] if "verdict" in x]
        self.assertTrue(judged)
        self.assertTrue(all(x["reason"] == "saturates" for x in judged))
        self.assertGreater(judged[0]["b_peak_t"], judged[0]["bsat_t"])
        self.assertGreater(ok["value"]["feasible_count"], 0)

    def test_a_core_whose_material_lacks_bsat_there_is_unjudged_not_guessed(self):
        # 3C90 publishes Bsat at 25 C only; at 100 C the honest answer is "cannot
        # judge this part", not an interpolation
        cold = M.select_core(100e-6, 3.0, material="3C90", temperature_c=25)
        hot = M.select_core(100e-6, 3.0, material="3C90", temperature_c=100)
        self.assertEqual(cold["status"], "ok")
        self.assertGreater(cold["value"]["feasible_count"], 0)
        u = hot["unjudged"]
        self.assertTrue(u)
        self.assertTrue(all(x["reason"] == "saturation_unknown" for x in u))
        self.assertIn("no published Bsat at 100 C", u[0]["because"])
        self.assertEqual(hot["value"]["feasible_count"], 0)

    def test_the_derate_moves_the_flux_limit_and_the_turns(self):
        # bsat_derate is a design margin: halving it must not reduce the turns
        L, ipk = 20e-6, 3.0
        tight = M.select_core(L, ipk, material="N87", temperature_c=100,
                              bsat_derate=0.4)
        loose = M.select_core(L, ipk, material="N87", temperature_c=100,
                              bsat_derate=0.8)
        ct = {c["part"]: c["turns"] for c in tight["value"]["candidates"]}
        cl = {c["part"]: c["turns"] for c in loose["value"]["candidates"]}
        self.assertTrue(set(ct) & set(cl))
        for p in set(ct) & set(cl):
            self.assertGreaterEqual(ct[p], cl[p], p)
        # the limit really is the derate times the published 390 mT
        self.assertAlmostEqual(
            loose["value"]["candidates"][0]["b_limit_t"], 0.8 * 0.390, places=6)

    def test_a_caller_limit_needs_no_material_at_all(self):
        # 60 C is a temperature nothing in the table publishes Bsat at; an explicit
        # b_max_t makes the question answerable anyway
        r = M.select_core(100e-6, 3.0, b_max_t=0.25, temperature_c=60)
        self.assertEqual(r["status"], "ok")
        self.assertGreater(r["value"]["feasible_count"], 0)
        for c in r["value"]["candidates"]:
            self.assertEqual(c["saturation"], "ok")
            self.assertIn("caller's b_max_t", c["saturation_note"])


class TheGapFollowsFromTheReluctanceDifference(unittest.TestCase):
    def test_gap_is_the_series_reluctance_difference(self):
        L, ipk, blim = 100e-6, 3.0, 0.30
        r = M.select_core(L, ipk, b_max_t=blim, shape="ELP 18/4/10")
        c = r["value"]["candidates"][0]
        core = _core(c["part"])
        n, ae, le = c["turns"], core["ae_mm2"] * 1e-6, core["le_mm"] * 1e-3
        mu = _mat(core["material"])["mu_i"]
        expect = MU0 * ae * n ** 2 / L - le / mu
        self.assertTrue(c["gap_required"])
        self.assertAlmostEqual(c["gap_mm"], expect * 1e3, places=6)

    def test_an_already_gapped_part_may_need_no_further_gap(self):
        # 100 uH is exactly AL*20^2 for the A250 variant (250 nH), while the
        # ungapped 3250 nH part needs a gap to get that far down
        r = M.select_core(100e-6, 1.0, material="3C90", temperature_c=25,
                          shape="PQ 20/16")
        parts = {c["part"]: c for c in r["value"]["candidates"]}
        self.assertIn("PQ20/16-3C90-A250", parts)
        self.assertFalse(parts["PQ20/16-3C90-A250"]["gap_required"])
        self.assertTrue(parts["PQ20/16-3C90"]["gap_required"])
        self.assertAlmostEqual(parts["PQ20/16-3C90-A250"]["al_required_nh"], 250.0,
                               places=2)

    def test_min_area_is_used_when_it_is_smaller(self):
        r = M.select_core(100e-6, 3.0, b_max_t=0.3, material="3C90",
                          temperature_c=25, shape="PQ")
        rows = [c for c in r["value"]["candidates"] if c["part"].startswith("PQ20/16")]
        self.assertTrue(rows)
        for c in rows:
            self.assertEqual(c.get("flux_area_mm2"), 59.1)
            self.assertLess(c["flux_area_mm2"], c["ae_mm2"])
            # and the peak really was judged on Amin
            self.assertAlmostEqual(c["b_peak_t"],
                                   100e-6 * 3.0 / (c["turns"] * 59.1e-6), places=6)


class CoreLossKeepsTheReferenceCondition(unittest.TestCase):
    def test_the_measured_point_is_reproduced_exactly(self):
        # U20/16/7-3C90: 1.900 W/set at 200 mT / 25 kHz / 100 C
        r = M.select_core(200e-6, 2.0, material="3C90", temperature_c=25,
                          freq_hz=25e3, b_ac_t=0.2, shape="U 20/16/7")
        c = r["value"]["candidates"][0]
        self.assertEqual(c["core_loss_w"], 1.9)
        self.assertIn("vendor's own measured point", c["core_loss_basis"])

    def test_100_khz_on_a_25_khz_part_is_not_the_same_number(self):
        # the negative control for the condition: same B, 4x the frequency. If the
        # condition were ignored this would still return 1.9 W.
        r = M.select_core(200e-6, 2.0, material="3C90", temperature_c=25,
                          freq_hz=100e3, b_ac_t=0.2, shape="U 20/16/7")
        c = r["value"]["candidates"][0]
        self.assertIsNone(c["core_loss_w"])
        self.assertIn("loss_exponents", c["core_loss_basis"])

    def test_the_exponents_extrapolate_and_follow_the_law(self):
        # scaling with (x, y) must land exactly on the law it claims to use
        r = M.select_core(200e-6, 2.0, material="3C90", temperature_c=25,
                          freq_hz=50e3, b_ac_t=0.1, loss_exponents=(1.6, 2.6),
                          shape="U 20/16/7")
        c = r["value"]["candidates"][0]
        expect = 1.9 * (50e3 / 25e3) ** 1.6 * (0.1 / 0.2) ** 2.6
        self.assertAlmostEqual(c["core_loss_w"], expect, places=6)

    def test_a_negative_exponent_is_refused(self):
        r = M.select_core(100e-6, 1.0, b_max_t=0.3, loss_exponents=(1.6, -2.6))
        self.assertEqual(r["status"], "error")
        self.assertEqual(r["reason_code"], "ARG_RANGE")

    def test_a_part_without_a_measured_point_says_unknown_not_zero(self):
        r = M.select_core(100e-6, 1.0, b_max_t=0.3, freq_hz=100e3, b_ac_t=0.1,
                          material="3C90", temperature_c=25, shape="PQ")
        c = r["value"]["candidates"][0]
        self.assertIsNone(c["core_loss_w"])
        self.assertIn("not zero", c["core_loss_basis"])
        self.assertIn("core_loss_w", c["missing"])

    def test_no_frequency_means_no_loss_number(self):
        r = M.select_core(100e-6, 1.0, b_max_t=0.3, material="N87")
        c = r["value"]["candidates"][0]
        self.assertIsNone(c["core_loss_w"])
        self.assertIn("freq_hz", c["core_loss_basis"])


class TheRankingIsSize(unittest.TestCase):
    def test_feasible_cores_come_back_smallest_volume_first(self):
        r = M.select_core(100e-6, 2.0, b_max_t=0.3)
        vols = [c["ve_mm3"] for c in r["value"]["candidates"]]
        self.assertEqual(vols, sorted(vols))

    def test_limit_truncates_the_list_not_the_count(self):
        a = M.select_core(100e-6, 2.0, b_max_t=0.3)
        b = M.select_core(100e-6, 2.0, b_max_t=0.3, limit=2)
        self.assertEqual(len(b["value"]["candidates"]), 2)
        self.assertEqual(a["value"]["feasible_count"], b["value"]["feasible_count"])
        self.assertEqual([c["part"] for c in b["value"]["candidates"]],
                         [c["part"] for c in a["value"]["candidates"][:2]])

    def test_a_part_without_geometry_is_rejected_by_name_not_skipped_silently(self):
        r = M.select_core(100e-6, 1.0, b_max_t=0.3)
        bad = [x for x in r["value"]["rejected"] if x["part"] == "B67410A0105X087"]
        self.assertEqual(len(bad), 1)
        self.assertEqual(bad[0]["reason"], "no_geometry")
        self.assertTrue(bad[0]["because"])


class TheWindowIsCheckedOnlyWhereItIsKnown(unittest.TestCase):
    def test_only_the_part_with_a_former_has_a_window_verdict(self):
        r = M.select_core(100e-6, 2.0, b_max_t=0.3, i_rms_a=1.0,
                          current_density_a_mm2=5.0, fill_factor=0.4)
        verdicts = {c["part"]: (c["winding"] or {}).get("window_fits")
                    for c in r["value"]["candidates"]}
        self.assertEqual(verdicts.get("B66325G0000X187"), True)
        others = [v for p, v in verdicts.items() if p != "B66325G0000X187"]
        self.assertTrue(others)
        self.assertTrue(all(v is None for v in others))

    def test_requiring_a_window_drops_the_unchecked_parts(self):
        kwargs = dict(b_max_t=0.3, i_rms_a=1.0, current_density_a_mm2=5.0,
                      fill_factor=0.4)
        loose = M.select_core(100e-6, 2.0, **kwargs)
        strict = M.select_core(100e-6, 2.0, require_window_fit=True, **kwargs)
        self.assertGreater(loose["value"]["feasible_count"],
                           strict["value"]["feasible_count"])
        self.assertEqual([c["part"] for c in strict["value"]["candidates"]],
                         ["B66325G0000X187"])

    def test_too_much_copper_for_the_window_is_an_overflow(self):
        # 6 A at 5 A/mm^2 through 178 mm^2 with a 0.4 fill: 2 turns of 1.2 mm^2 is
        # 6 mm^2, fine; 100 A is not, and the same part must then be rejected
        small = M.select_core(100e-6, 2.0, b_max_t=0.3, i_rms_a=6.0,
                             current_density_a_mm2=5.0, fill_factor=0.4,
                             shape="E 42/21/15")
        big = M.select_core(100e-6, 2.0, b_max_t=0.3, i_rms_a=100.0,
                            current_density_a_mm2=5.0, fill_factor=0.4,
                            shape="E 42/21/15")
        self.assertEqual(small["value"]["feasible_count"], 1)
        self.assertEqual(big["value"]["feasible_count"], 0)
        reasons = [x["reason"] for x in big["value"]["rejected"]]
        self.assertIn("window_overflow", reasons)

    def test_copper_loss_uses_the_former_mean_turn_length(self):
        r = M.select_core(100e-6, 2.0, b_max_t=0.3, i_rms_a=2.0,
                          current_density_a_mm2=5.0, fill_factor=0.4,
                          shape="E 42/21/15")
        c = r["value"]["candidates"][0]
        w = c["winding"]
        a_wire = 2.0 / 5.0
        rho = M.COPPER_RESISTIVITY_OHM_M * (1 + M.COPPER_TEMPCO_PER_K * (100 - 20))
        r_dc = rho * c["turns"] * 93.0e-3 / (a_wire * 1e-6)
        self.assertAlmostEqual(w["p_copper_w"], 2.0 ** 2 * r_dc, places=6)
        self.assertAlmostEqual(w["window_area_needed_mm2"],
                               a_wire * c["turns"] / 0.4, places=6)

    def test_no_temperature_rise_is_offered_at_all(self):
        r = M.select_core(100e-6, 2.0, b_max_t=0.3)
        blob = json.dumps(r, ensure_ascii=False)
        self.assertIn("temperature rise", blob)
        self.assertNotIn("delta_t_k", blob)


class TheRefusalsAreNamedAndTheControlsAnswer(unittest.TestCase):
    def test_unknown_material_is_not_found_with_the_list(self):
        r = M.select_core(100e-6, 1.0, material="unicorn")
        self.assertEqual(r["reason_code"], "NOT_FOUND")
        self.assertIn("materials_in_table", r)

    def test_unknown_shape_is_not_found_with_the_list(self):
        r = M.select_core(100e-6, 1.0, shape="ZZ 99")
        self.assertEqual(r["reason_code"], "NOT_FOUND")
        self.assertIn("shapes_in_table", r)

    def test_bad_arguments_are_range_errors(self):
        self.assertEqual(M.select_core(0, 1.0)["reason_code"], "ARG_RANGE")
        self.assertEqual(M.select_core(1e-6, 0)["reason_code"], "ARG_RANGE")
        self.assertEqual(M.select_core(1e-6, 1.0, bsat_derate=1.5)["reason_code"],
                         "ARG_RANGE")
        self.assertEqual(M.select_core(1e-6, 1.0, limit=0)["reason_code"],
                         "ARG_RANGE")

    def test_a_missing_table_is_a_named_error(self):
        r = M.select_core(100e-6, 1.0, data_path=os.path.join("nope", "nope.json"))
        self.assertEqual(r["reason_code"], "DATA_UNAVAILABLE")

    def test_an_unverified_table_is_refused(self):
        import tempfile
        p = os.path.join(tempfile.mkdtemp(), "t.json")
        with io.open(p, "w", encoding="utf-8") as fh:
            fh.write('{"verified": false, "cores": [{"part": "x"}]}')
        r = M.select_core(100e-6, 1.0, data_path=p)
        self.assertEqual(r["reason_code"], "DATA_UNAVAILABLE")
        self.assertIn("not marked verified", r["message"])

    def test_the_browse_tool_reads_the_same_numbers(self):
        r = M.magnetic_data("cores", material="N87")
        self.assertEqual(r["status"], "ok")
        self.assertEqual(r["value"]["count"],
                         len([c for c in _table()["cores"]
                              if c["material"] == "N87"]))
        one = M.magnetic_data("cores", name="B66325G0000X187")
        self.assertEqual(one["value"]["count"], 1)
        self.assertEqual(one["value"]["rows"][0]["ae_mm2"], 178.0)
        self.assertEqual(M.magnetic_data("cores", name="nope")["reason_code"],
                         "NOT_FOUND")
        self.assertEqual(M.magnetic_data(kind="spam")["reason_code"], "ARG_INVALID")


if __name__ == "__main__":
    unittest.main()
