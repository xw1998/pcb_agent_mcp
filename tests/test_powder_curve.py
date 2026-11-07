"""0.0.80: the printed magnetisation B(H) curve and the mu-vs-T fit.

What is being pinned here, in order:

  * the table is a transcription of the vendor's printed coefficients and every
    row carries the page it came from - the counts (29 groups / 125 rows, 9 / 54,
    49 stability rows, 5 High DC Bias variants) are recomputed here rather than
    quoted from the answer;
  * the two closed forms are evaluated as printed, and the structural checks the
    loader makes are checks that can fail: a table whose columns have shifted
    (a negative numerator, a positive temperature `a`, a wrong schema) is refused
    rather than evaluated - that is the negative control for the whole section;
  * three independent agreements came out before anything was written down and
    all three are recomputed here from the file: the catalog's worked example
    (0.441 T printed, 0.44126 T here), the p.15 stability column against the
    temperature fit (0.045 pp worst, nine materials), and the DC-bias integral
    against the magnetisation curve (median 0.49 % at 100 Oe over 61 toroid rows);
  * where the two printed fits disagree - three rows, up to 54 % - both numbers
    are returned with fits_disagree and neither is picked, and the row whose fit
    turns over inside the printed band is flagged instead of clamped (and the
    falling branch is proved to actually fall, so the flag is not cosmetic);
  * what the fit cannot answer is refused with the list that would answer it:
    H <= 0, an unprinted / ambiguous permeability or shape family, an unprinted
    material, a temperature outside -50..200 C, and a missing table;
  * the capability tables and the tool count moved once, on purpose.
"""
import copy
import io
import json
import math
import os
import unittest

from pcb_agent_mcp.calc import magnetics as mag
from pcb_agent_mcp import tools

HERE = os.path.dirname(os.path.abspath(__file__))
ROOT = os.path.dirname(HERE)
SCRATCH = os.path.join(ROOT, "_scratch")
TABLE = os.path.join(ROOT, "data", "powder_curve_fits.json")

MU0_T_PER_OE = 4e-7 * math.pi * 79.5771


def _table():
    with io.open(TABLE, encoding="utf-8") as fh:
        return json.load(fh)


def _call(**kw):
    return tools.TOOLS["magnetics_powder_curve"]["handler"](kw)


def _rows(data):
    out = []
    for g in (data.get("magnetization") or {}).get("groups") or []:
        for r in g.get("rows") or []:
            out.append((g, r))
    return out


def _b(row, h):
    num = row["a"] + row["b"] * h + row["c"] * h * h
    den = 1.0 + row["d"] * h + row["e"] * h * h
    return (num / den) ** row["x"]


def _dmu(row, t):
    return (row["a"] + row["b"] * t + row["c"] * t * t + row["d"] * t ** 3
            + row["e"] * t ** 4) * 100.0


def _row_of(data, material, shape, perm):
    for g, r in _rows(data):
        if (g.get("material") == material and g.get("shape_family") == shape
                and r.get("permeability") == perm):
            return r
    raise AssertionError("no row %s/%s/%s" % (material, shape, perm))


class TestTheTableIsTheVendorS(unittest.TestCase):

    def test_the_counts_and_the_pages(self):
        d = _table()
        self.assertEqual(d.get("schema"), "powder-curve-fits")
        self.assertEqual(d.get("version"), "0.0.80")
        mag_groups = d["magnetization"]["groups"]
        self.assertEqual(len(mag_groups), 29)
        self.assertEqual(len(_rows(d)), 125)
        self.assertEqual(len(d["magnetization"]["variants_high_dc_bias"]), 5)
        self.assertEqual(len(d["temperature"]["groups"]), 9)
        self.assertEqual(sum(len(g["rows"]) for g in d["temperature"]["groups"]), 54)
        self.assertEqual(len(d["stability_printed_page_15"]["rows"]), 49)
        # every row knows the printed page it came from
        for g in mag_groups:
            self.assertTrue(g.get("printed_page"))
        for g in d["temperature"]["groups"]:
            self.assertTrue(g.get("printed_page"))
        # the page offset is stated once and is the measured one (-2, not -1)
        self.assertIn("pdf page - 2", d["source"]["printed_page_offset"])

    def test_every_magnetisation_row_rises_over_the_printed_band(self):
        # the same invariant the loader checks, recomputed here so a bad row in the
        # file is caught by the test suite and not only by a refusal at run time
        d = _table()
        for g, r in _rows(d):
            for h in (1.0, 10.0, 100.0, 1000.0):
                num = r["a"] + r["b"] * h + r["c"] * h * h
                den = 1.0 + r["d"] * h + r["e"] * h * h
                self.assertGreater(den, 0.0, (g.get("material"), r.get("permeability")))
                self.assertGreater(num, 0.0, (g.get("material"), r.get("permeability")))
            self.assertGreater(_b(r, 100.0), 0.0)

    def test_every_temperature_row_starts_by_falling_and_stays_small(self):
        d = _table()
        for g in d["temperature"]["groups"]:
            for r in g["rows"]:
                self.assertLess(r["a"], 0.0, (g.get("material"), r.get("permeability")))
                span = abs(_dmu(r, 100.0) - _dmu(r, -50.0))
                self.assertLess(span, 20.0, (g.get("material"), r.get("permeability")))

    def test_the_seven_turning_rows_are_the_ones_that_turn(self):
        d = _table()
        flagged = [(g.get("material"), r.get("permeability"),
                    r.get("turns_over_at_h_oe")) for g, r in _rows(d)
                   if r.get("turns_over_at_h_oe") is not None]
        self.assertEqual(len(flagged), 7)
        for _m, _p, h in flagged:
            r = _row_of(d, _m, "Toroids", _p)
            lo, hi = h * 0.9, h * 1.1
            self.assertLess(_b(r, lo), _b(r, h))          # still rising below it
            self.assertLess(_b(r, hi), _b(r, h))          # falling above it


class TestTheLoaderRefusesAShiftedColumn(unittest.TestCase):
    """The negative control for the whole section: these are the ways the table
    could be wrong, and each has to be caught by the loader rather than evaluated."""

    def _write(self, name, mutate):
        d = copy.deepcopy(_table())
        mutate(d)
        path = os.path.join(SCRATCH, name)
        with io.open(path, "w", encoding="utf-8", newline="") as fh:
            json.dump(d, fh)
        return path

    def test_a_wrong_schema_is_refused(self):
        p = self._write("curve_bad_schema.json",
                        lambda d: d.__setitem__("schema", "something-else"))
        out = mag.magnetics_powder_curve("Kool Mu", 60, shape_family="Toroids",
                                         h_oe=10.0, curve_data_path=p)
        self.assertEqual(out.get("reason_code"), "DATA_UNAVAILABLE")
        self.assertIn("schema", json.dumps(out, ensure_ascii=False))

    def test_a_negative_numerator_is_refused_not_evaluated(self):
        def bad(d):
            d["magnetization"]["groups"][0]["rows"][0]["a"] = -1e9
        p = self._write("curve_bad_rising.json", bad)
        out = mag.magnetics_powder_curve("Kool Mu", 60, shape_family="Toroids",
                                         h_oe=10.0, curve_data_path=p)
        self.assertEqual(out.get("reason_code"), "DATA_UNAVAILABLE")
        self.assertIn("shifted a column", json.dumps(out, ensure_ascii=False))

    def test_a_positive_temperature_a_is_refused(self):
        def bad(d):
            d["temperature"]["groups"][0]["rows"][0]["a"] = 1e-3
        p = self._write("curve_bad_temp.json", bad)
        out = mag.magnetics_powder_curve("Kool Mu", 60, temperature_c=25.0,
                                         curve_data_path=p)
        self.assertEqual(out.get("reason_code"), "DATA_UNAVAILABLE")
        self.assertIn("negative a", json.dumps(out, ensure_ascii=False))

    def test_a_non_numeric_coefficient_is_refused(self):
        def bad(d):
            d["magnetization"]["groups"][0]["rows"][0]["e"] = "n/a"
        p = self._write("curve_bad_type.json", bad)
        out = mag.magnetics_powder_curve("Kool Mu", 60, shape_family="Toroids",
                                         h_oe=10.0, curve_data_path=p)
        self.assertEqual(out.get("reason_code"), "DATA_UNAVAILABLE")

    def test_a_missing_table_is_its_own_refusal(self):
        out = mag.magnetics_powder_curve("Kool Mu", 60, shape_family="Toroids",
                                         h_oe=10.0,
                                         curve_data_path=os.path.join(
                                             SCRATCH, "no_such_curve.json"))
        self.assertEqual(out.get("reason_code"), "DATA_UNAVAILABLE")
        self.assertIn("restore", (out.get("hint") or ""))


class TestTheCatalogSWorkedExample(unittest.TestCase):

    def test_95_oersteds_in_60u_kool_mu_is_0_441_tesla(self):
        # p.25 Example 3 prints B = 0.441 T at 95 Oe; the transcription gives
        # 0.44126 T, i.e. inside the printed three digits
        d = _table()
        r = _row_of(d, "Kool Mµ", "Toroids", 60)
        self.assertAlmostEqual(_b(r, 95.0), 0.44126, places=5)
        self.assertLess(abs(_b(r, 95.0) - 0.441) / 0.441, 0.01)
        out = _call(material="Kool Mu", permeability=60, shape_family="Toroids",
                    h_oe=95.0)
        self.assertEqual(out.get("status"), "ok")
        v = out["value"]
        self.assertAlmostEqual(v["b_t"], 0.44126, places=5)
        # and the second printed point of the same example
        self.assertLess(abs(_b(r, 15.8) - 0.092) / 0.092, 0.01)

    def test_the_tool_refuses_to_evaluate_at_zero_because_b_zero_is_not_zero(self):
        out = _call(material="Kool Mu", permeability=60, shape_family="Toroids",
                    h_oe=0.0)
        self.assertEqual(out.get("reason_code"), "ARG_INVALID")
        self.assertAlmostEqual(out["b_at_zero_h_t"], 0.00529, places=5)
        self.assertIn("not a small-signal model", out["message"])

    def test_the_differential_permeability_is_the_derivative_of_the_same_fit(self):
        d = _table()
        r = _row_of(d, "Kool Mµ", "Toroids", 60)
        h, step = 95.0, 95.0 * 1e-4
        here = (_b(r, h + step) - _b(r, h - step)) / (2 * step) / MU0_T_PER_OE
        out = _call(material="Kool Mu", permeability=60, shape_family="Toroids",
                    h_oe=h)
        self.assertAlmostEqual(out["value"]["mu_r_differential"], round(here, 6),
                               places=5)
        # the printed nominal is 60; the differential at 95 Oe is ~29.8, and the
        # gap between them is exactly why the curve is not a small-signal model
        self.assertTrue(25.0 < here < 35.0)


class TestTheTemperatureFitAgainstA_PrintedNumber(unittest.TestCase):

    def test_nine_materials_match_the_printed_p15_dynamic_range(self):
        d = _table()
        stored = {r["material"]: r for r in
                  d["cross_check"]["temperature_range_against_the_printed_stability_column"]
                  ["rows"]}
        worst = 0.0
        for g in d["temperature"]["groups"]:
            name = g["material"]
            r = [x for x in g["rows"] if x["permeability"] == 60][0]
            span = abs(_dmu(r, 100.0) - _dmu(r, -50.0))
            gap = abs(span - stored[name]["printed_mu_vs_t_dynamic_range_pct"])
            self.assertLess(gap, 0.05, name)
            self.assertAlmostEqual(span, stored[name]["fit_dynamic_range_pct"],
                                   places=3)
            worst = max(worst, gap)
        self.assertAlmostEqual(worst, 0.045, places=3)

    def test_the_answer_carries_the_printed_row_it_was_checked_against(self):
        out = _call(material="Kool Mu", permeability=60, temperature_c=100.0)
        v = out["value"]
        self.assertAlmostEqual(v["mu_change_pct"], -1.4332, places=4)
        self.assertAlmostEqual(v["mu_r_at_t"], 60.0 * (1.0 - 0.014332), places=4)
        self.assertEqual(v["mu_r_at_25c"], 60)
        self.assertAlmostEqual(v["mu_ratio_vs_25c"], 0.985668, places=6)
        self.assertEqual(v["printed_stability"]["mu_vs_t_dynamic_range_pct"], 2.9)
        self.assertAlmostEqual(v["printed_stability_gap_pp"], 0.04525, places=5)
        self.assertTrue(v["inside_printed_dynamic_range"])

    def test_the_range_ends_are_measured_not_guessed(self):
        # -50 and 200 are accepted (the tabulated band's low end and the printed
        # rating); one degree outside either is refused
        for t in (-50.0, 200.0):
            self.assertEqual(_call(material="Kool Mu", permeability=60,
                                   temperature_c=t).get("status"), "ok")
        for t in (-51.0, 201.0):
            out = _call(material="Kool Mu", permeability=60, temperature_c=t)
            self.assertEqual(out.get("reason_code"), "OUT_OF_VALIDITY")
            self.assertIn("quartic", out["message"])

    def test_inside_the_range_but_outside_the_tabulated_band_is_flagged(self):
        out = _call(material="Kool Mu", permeability=60, temperature_c=150.0)
        self.assertEqual(out.get("status"), "ok")
        self.assertFalse(out["value"]["inside_printed_dynamic_range"])
        self.assertTrue(any("outside the -50..100 C band" in w
                            for w in out["value"]["warnings"]))

    def test_a_material_with_no_temperature_fit_is_refused_with_the_list(self):
        out = _call(material="Kool Mu MAX High Performance", permeability=60,
                    shape_family="Toroids", temperature_c=25.0)
        self.assertEqual(out.get("reason_code"), "NOT_FOUND")
        self.assertEqual(len(out.get("temperature_materials_printed") or []), 9)


class TestTheTwoPrintedFitsDisagreeAndBothAreReported(unittest.TestCase):

    def test_mpp_550u_is_54_percent_apart_and_says_so(self):
        out = _call(material="MPP", permeability=550, shape_family="Toroids",
                    h_oe=100.0)
        v = out["value"]
        self.assertAlmostEqual(v["b_t"], 0.234146, places=5)
        self.assertAlmostEqual(v["b_t_from_dc_bias_integral"], 0.5128, places=4)
        self.assertAlmostEqual(v["delta_pct_vs_dc_bias_integral"], -54.341, places=2)
        self.assertTrue(v["fits_disagree"])
        self.assertAlmostEqual(v["turns_over_at_h_oe"], 23.8, places=1)
        self.assertTrue(v["past_fit_turnover"])
        self.assertIsNone(v["mu_r_differential"])
        self.assertTrue(any("neither" not in w and "disagree" in w
                            for w in v["warnings"]))

    def test_the_falling_branch_really_falls_so_the_flag_is_not_cosmetic(self):
        d = _table()
        r = _row_of(d, "MPP", "Toroids", 550)
        self.assertLess(_b(r, 100.0), _b(r, 23.8))
        self.assertLess(_b(r, 200.0), _b(r, 100.0))
        # and below the turnover the answer is a normal rising row
        out = _call(material="MPP", permeability=550, shape_family="Toroids",
                    h_oe=10.0)
        self.assertFalse(out["value"]["past_fit_turnover"])
        self.assertIsNotNone(out["value"]["mu_r_differential"])

    def test_the_three_rows_over_ten_percent_are_the_three_stored_ones(self):
        d = _table()
        stored = d["cross_check"]["magnetization_against_the_dc_bias_integral"]
        wanted = sorted(("%s %s" % (r["material"], r["permeability"]))
                        for r in stored["rows_disagreeing_by_more_than_10pct_at_100_oe"])
        got = []
        for g, r in _rows(d):
            out = _call(material=g["material"], permeability=r["permeability"],
                        shape_family=g["shape_family"], h_oe=100.0)
            v = out.get("value") or {}
            if v.get("fits_disagree"):
                got.append("%s %s" % (g["material"], r["permeability"]))
        self.assertEqual(sorted(got), wanted)
        self.assertEqual(len(got), 3)

    def test_the_median_over_the_toroid_rows_is_under_two_percent(self):
        d = _table()
        deltas = []
        for g, r in _rows(d):
            if g.get("shape_family") != "Toroids":
                continue
            out = _call(material=g["material"], permeability=r["permeability"],
                        shape_family="Toroids", h_oe=100.0)
            deltas.append(abs(out["value"]["delta_pct_vs_dc_bias_integral"]))
        self.assertEqual(len(deltas), 61)
        deltas.sort()
        self.assertLess(deltas[len(deltas) // 2], 1.0)

    def test_a_part_where_they_agree_is_not_flagged(self):
        out = _call(material="Kool Mu", permeability=60, shape_family="Toroids",
                    h_oe=100.0)
        v = out["value"]
        self.assertFalse(v["fits_disagree"])
        self.assertLess(abs(v["delta_pct_vs_dc_bias_integral"]), 1.0)


class TestEveryImpossibleQuestionIsItsOwnRefusal(unittest.TestCase):

    def test_an_unprinted_material(self):
        out = _call(material="Sendust", permeability=60, h_oe=10.0)
        self.assertEqual(out.get("reason_code"), "NOT_FOUND")
        self.assertEqual(len(out.get("materials_printed") or []), 12)

    def test_an_ambiguous_shape_family_lists_the_four(self):
        out = _call(material="Kool Mu", permeability=60, h_oe=10.0)
        self.assertEqual(out.get("reason_code"), "ARG_INVALID")
        self.assertEqual(len(out.get("shape_families_printed") or []), 4)
        self.assertIn("invention", out["message"])

    def test_an_unprinted_permeability_lists_the_printed_ones(self):
        out = _call(material="Kool Mu", permeability=33, shape_family="Toroids",
                    h_oe=10.0)
        self.assertEqual(out.get("reason_code"), "NOT_FOUND")
        self.assertEqual(out["permeabilities_printed"],
                         [14, 19, 26, 40, 60, 75, 90, 125])

    def test_an_unprinted_permeability_on_the_temperature_side_too(self):
        out = _call(material="Kool Mu", permeability=125, temperature_c=25.0)
        # 125 IS printed for Kool Mu toroids on the temperature page
        self.assertEqual(out.get("status"), "ok")
        out = _call(material="High Flux", permeability=200, temperature_c=25.0)
        self.assertEqual(out.get("reason_code"), "NOT_FOUND")

    def test_both_routes_to_h_at_once(self):
        out = _call(material="Kool Mu", permeability=60, shape_family="Toroids",
                    h_oe=10.0, turns=40, dc_current_a=2.0, le_mm=50.0)
        self.assertEqual(out.get("reason_code"), "ARG_INVALID")

    def test_a_partial_winding(self):
        out = _call(material="Kool Mu", permeability=60, shape_family="Toroids",
                    turns=40, dc_current_a=2.0)
        self.assertEqual(out.get("reason_code"), "ARG_INVALID")
        self.assertIn("le_mm", out["message"])

    def test_neither_question_asked(self):
        out = _call(material="Kool Mu", permeability=60, shape_family="Toroids")
        self.assertEqual(out.get("reason_code"), "ARG_INVALID")
        self.assertIn("two different fits", out["message"])

    def test_a_variant_block_refuses_a_shape_family(self):
        out = _call(material="High DC Bias XFlux", permeability=26,
                    shape_family="Toroids", h_oe=50.0)
        self.assertEqual(out.get("reason_code"), "ARG_INVALID")
        self.assertIn("without a shape family", out["message"])

    def test_a_variant_block_is_not_cross_checked_against_the_standard_grade(self):
        out = _call(material="High DC Bias XFlux", permeability=26, h_oe=50.0)
        self.assertEqual(out.get("status"), "ok")
        v = out["value"]
        self.assertEqual(v["material"], "High DC Bias XFlux")
        self.assertIsNone(v["shape_family"])
        self.assertIsNone(v["b_t_from_dc_bias_integral"])
        self.assertIn("different curve", v["cross_check_note"])


class TestTheWindingsAndTheEdges(unittest.TestCase):

    def test_the_winding_route_gives_the_catalogs_own_h(self):
        out = _call(material="Kool Mu", permeability=60, shape_family="Toroids",
                    turns=40, dc_current_a=2.0, le_mm=50.0)
        self.assertEqual(out.get("status"), "ok")
        self.assertAlmostEqual(out["value"]["h_oe"],
                               0.4 * math.pi * 40 * 2.0 / (50.0 / 10.0), places=6)

    def test_outside_the_plotted_band_is_flagged_and_the_h_kept(self):
        out = _call(material="Kool Mu", permeability=60, shape_family="Toroids",
                    h_oe=1000.0)
        v = out["value"]
        self.assertEqual(v["h_oe"], 1000.0)
        self.assertFalse(v["inside_plotted_band"])
        self.assertTrue(any("outside the 1-300 Oe band" in w
                            for w in v["warnings"]))

    def test_the_evidence_block_is_opt_in_and_complete(self):
        plain = _call(material="MPP", permeability=550, shape_family="Toroids",
                      h_oe=100.0)
        self.assertNotIn("cross_check", plain)
        full = _call(material="MPP", permeability=550, shape_family="Toroids",
                     h_oe=100.0, include_evidence=True)
        self.assertIn("cross_check", full)
        self.assertEqual(len(full["printing_anomalies"]), 5)
        self.assertEqual(len(full["not_carried"]), 5)
        for a in full["printing_anomalies"]:
            self.assertTrue(a["kept_as_printed"])
        self.assertIn("method_rejected", full["cross_check"])
        self.assertAlmostEqual(
            full["cross_check"]["method_rejected"]["median_error_pct"], 36.9,
            places=1)

    def test_the_answer_names_the_pages_and_the_formula(self):
        out = _call(material="Kool Mu", permeability=60, shape_family="Toroids",
                    h_oe=95.0)
        self.assertIn("128-132", out["basis"])
        self.assertIn("B = [", out["model"])
        self.assertEqual(out["value"]["fit"]["printed_page"], 128)
        self.assertIn("powder_curve_fits.json", out["basis"])


class TestTheSurfaceMovedOnce(unittest.TestCase):

    def test_the_tool_is_registered_and_counted(self):
        self.assertIn("magnetics_powder_curve", tools.TOOLS)
        # 179 = 178 + magnetics_core_loss_audit in 0.0.81 (scoring a triple against
        # the vendor's printed points is a different question from evaluating a fit)
        # 180 = 179 + thermal_spreading in 0.0.85 (a rectangular heat source's
        # spreading resistance is its own question; thermal_stack stays strictly 1-D)
        # 223 = 220 + the three routing-discipline tools in 0.0.122
        self.assertEqual(len(tools.TOOLS), 227)
        cap = tools.TOOLS["calc_capabilities"]["handler"]({})
        self.assertIn("magnetics_powder_curve", cap["available"])
        # 174 = 173 + thermal_spreading in 0.0.85
        self.assertEqual(len(cap["available"]), 204)

    def test_the_capability_list_no_longer_claims_there_is_no_curve(self):
        cap = tools.TOOLS["calc_capabilities"]["handler"]({})
        blob = json.dumps(cap["not_implemented_yet"], ensure_ascii=False)
        self.assertNotIn("No saturation CURVE of a real", blob)
        self.assertIn("The CURVE arrived in 0.0.80", blob)
        # the residue it does still refuse is named
        self.assertIn("a Bsat per temperature still", blob)


if __name__ == "__main__":
    unittest.main()
