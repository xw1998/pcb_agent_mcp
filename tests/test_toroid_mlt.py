"""0.0.79: the toroid mean turn length and the copper it buys.

What is being pinned here, in order:

  * the table is a transcription and the band is measured from it on every call:
    thirteen printed (OD, ID, H, MLT) rows, the ratio mlt/flat recomputed here,
    and the summary the answer reports equal to that recomputation - a wrong row
    has to move the answer, not hide behind a cached median;
  * the two geometry relations in the answer were checked against those same
    thirteen pages: le within 0.6 %, Ae 4-10 % ABOVE the printed section (the
    printed one has rounded corners), so the answer says which one it is;
  * the MLT is an INTERVAL and says so, and a vendor's own printed value falls
    inside it - which is the only licence for quoting an estimate at all;
  * the copper is independent arithmetic: rho(T)*N*MLT/A, N1 from the bore,
    layers from N1, a LOW-bias note once there is more than one layer, the
    current temperature relationship, and a window that says out loud when the
    winding does not fit rather than returning a plausible number;
  * every impossible question is its own refusal (and the missing-table arm);
  * select_core(toroid_mlt='estimate') is the 0.0.78 answer plus a winding:
    'off' and omitting the argument are byte-identical, and with 'off' the old
    sentence naming the missing length is still there;
  * the capability tables and the tool count moved once, on purpose.
"""
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
TABLE = os.path.join(ROOT, "data", "toroid_mlt_reference.json")

OD = 26.92
ID = 14.48
HT = 11.10
FLAT = 2.0 * HT + (OD - ID)          # 34.64
RATIO_MIN = 1.247656
RATIO_MED = 1.365936
RATIO_MAX = 1.460472

def _rows():
    with io.open(TABLE, encoding="utf-8") as fh:
        return json.load(fh)

def _call(**kw):
    args = {"od_mm": OD, "id_mm": ID, "ht_mm": HT}
    args.update(kw)
    return tools.TOOLS["magnetics_toroid_mlt"]["handler"](args)

def _plain(**kw):
    return mag.magnetics_toroid_mlt(**kw)

class TestTheBandIsMeasuredFromTheRows(unittest.TestCase):

    def test_the_table_is_thirteen_transcribed_rows_each_with_its_url(self):
        doc = _rows()
        self.assertEqual(doc["schema"], "toroid-mlt-reference")
        self.assertEqual(len(doc["rows"]), 13)
        for row in doc["rows"]:
            for key in ("model", "od_mm", "id_mm", "ht_mm", "mlt_mm", "url"):
                self.assertIn(key, row, row.get("model"))
                self.assertTrue(row[key] not in (None, ""), (row.get("model"), key))
            self.assertTrue(row["url"].startswith("https://www.amidon.de/"), row["url"])
            self.assertGreater(row["mlt_mm"], 2.0 * row["ht_mm"] + row["od_mm"]
                               - row["id_mm"])

    def test_the_band_equals_the_ratios_recomputed_here(self):
        rows = _rows()["rows"]
        ratios = sorted(r["mlt_mm"] / (2.0 * r["ht_mm"] + (r["od_mm"] - r["id_mm"]))
                        for r in rows)
        n = len(ratios)
        median = (ratios[n // 2] if n % 2 else 0.5 * (ratios[n // 2 - 1]
                                                      + ratios[n // 2]))
        band = mag._toroid_mlt_band(_rows())
        self.assertEqual(band["n"], n)
        self.assertAlmostEqual(band["ratio_min"], ratios[0], places=6)
        self.assertAlmostEqual(band["ratio_max"], ratios[-1], places=6)
        self.assertAlmostEqual(band["ratio_median"], median, places=6)
        self.assertAlmostEqual(band["ratio_min"], RATIO_MIN, places=6)
        self.assertAlmostEqual(band["ratio_median"], RATIO_MED, places=6)
        self.assertAlmostEqual(band["ratio_max"], RATIO_MAX, places=6)

    def test_the_extremes_are_the_cores_that_supply_them(self):
        rows = {r["model"]: r for r in _rows()["rows"]}
        def ratio(model):
            r = rows[model]
            return r["mlt_mm"] / (2.0 * r["ht_mm"] + (r["od_mm"] - r["id_mm"]))
        self.assertAlmostEqual(ratio("T184-6"), RATIO_MIN, places=6)
        self.assertAlmostEqual(ratio("T520-26"), RATIO_MAX, places=6)
        self.assertAlmostEqual(ratio("T68-6"), RATIO_MED, places=6)

    def test_the_band_is_recomputed_on_every_call_not_stored(self):
        doc = _rows()
        one = {"rows": [{"model": "fabricated", "od_mm": 10.0, "id_mm": 5.0,
                         "ht_mm": 2.0, "mlt_mm": 27.0}]}
        band = mag._toroid_mlt_band(one)
        self.assertEqual(band["n"], 1)
        self.assertAlmostEqual(band["ratio_median"], 3.0, places=12)
        self.assertEqual(band["models"], ["fabricated"])
        # a summary in the file would make the fabricated row a no-op; the file
        # carries none, so a caller cannot pass one in either
        self.assertNotIn("band", doc)
        self.assertNotIn("ratio_median", doc)

    def test_a_table_that_is_not_the_shipped_one_is_refused(self):
        if not os.path.isdir(SCRATCH):
            os.makedirs(SCRATCH)
        bad = os.path.join(SCRATCH, "toroid_mlt_bad.json")
        with io.open(bad, "w", encoding="utf-8") as fh:
            json.dump({"schema": "something-else", "rows": []}, fh)
        out = _plain(od_mm=OD, id_mm=ID, ht_mm=HT, mlt_data_path=bad)
        self.assertEqual(out["status"], "error")
        self.assertEqual(out["reason_code"], "DATA_UNAVAILABLE")

class TestTheGeometryRelationsWereChecked(unittest.TestCase):
    """The answer quotes two relations; both are testable against the same rows."""

    def test_le_is_the_log_mean_of_the_diameters_within_six_tenths_of_a_percent(self):
        worst = 0.0
        for row in _rows()["rows"]:
            le = math.pi * (row["od_mm"] + row["id_mm"]) / 2.0
            worst = max(worst, abs(le / (row["printed_le_cm"] * 10.0) - 1.0))
        self.assertLess(worst, 0.0065)
        self.assertAlmostEqual(worst, 0.006051, places=5)

    def test_the_rectangular_ae_is_four_to_ten_percent_above_the_printed_one(self):
        worst_low, worst_high = 1.0, 0.0
        for row in _rows()["rows"]:
            ae = row["ht_mm"] * (row["od_mm"] - row["id_mm"]) / 2.0
            rel = ae / (row["printed_ae_cm2"] * 100.0) - 1.0
            worst_low = min(worst_low, rel)
            worst_high = max(worst_high, rel)
        self.assertGreater(worst_low, 0.04)
        self.assertLess(worst_high, 0.10)

    def test_the_answer_says_which_ae_it_quotes(self):
        geo = _plain(od_mm=OD, id_mm=ID, ht_mm=HT)["value"]["geometry"]
        self.assertIn("4-10 % lower", geo["ae_basis"])
        self.assertIn("rounded corners", geo["ae_basis"])
        self.assertIn("0.6 %", geo["le_basis"])
        self.assertAlmostEqual(geo["le_mm"], 65.030968, places=5)
        self.assertAlmostEqual(geo["ae_mm2"], 69.042, places=5)
        self.assertAlmostEqual(geo["ve_mm3"], 4489.868088, places=5)
        self.assertAlmostEqual(geo["window_area_mm2"], 164.674747, places=5)

class TestTheMltIsAnInterval(unittest.TestCase):

    def test_the_answer_is_the_flat_perimeter_times_the_median_and_a_band(self):
        mlt = _plain(od_mm=OD, id_mm=ID, ht_mm=HT)["value"]["mlt"]
        self.assertAlmostEqual(mlt["flat_rect_mm"], FLAT, places=12)
        self.assertAlmostEqual(mlt["estimate_mm"], FLAT * RATIO_MED, places=5)
        self.assertAlmostEqual(mlt["estimate_mm"], 47.31602, places=5)
        self.assertEqual(mlt["range_mm"], [43.218817, 50.59075])
        self.assertAlmostEqual(mlt["range_mm"][0], FLAT * RATIO_MIN, places=4)
        self.assertAlmostEqual(mlt["range_mm"][1], FLAT * RATIO_MAX, places=4)
        self.assertIs(mlt["is_estimate"], True)
        self.assertLess(mlt["range_mm"][0], mlt["estimate_mm"])
        self.assertLess(mlt["estimate_mm"], mlt["range_mm"][1])
        self.assertEqual(mlt["ratio"]["n"], 13)

    def test_the_band_is_wide_enough_to_be_honest_and_not_a_formula(self):
        # a formula would collapse the interval to a point; the width here is
        # 16 % of the median, which is the whole reason it is not quoted as one
        width = (50.59075 - 43.218817) / 47.31602
        self.assertGreater(width, 0.10)
        self.assertLess(width, 0.25)

    def test_the_vendors_own_printed_value_falls_inside_the_band(self):
        # T106-1 prints 43.9 mm for exactly these three dimensions
        printed = 43.9
        mlt = _plain(od_mm=OD, id_mm=ID, ht_mm=HT)["value"]["mlt"]
        self.assertLessEqual(mlt["range_mm"][0], printed)
        self.assertLessEqual(printed, mlt["range_mm"][1])
        row = [r for r in _rows()["rows"] if r["model"] == "T106-1"][0]
        self.assertEqual((row["od_mm"], row["id_mm"], row["ht_mm"]),
                         (OD, ID, HT))

    def test_the_model_line_and_the_assumptions_say_estimate_not_reading(self):
        out = _plain(od_mm=OD, id_mm=ID, ht_mm=HT)
        self.assertIn("measured printed-MLT ratio band", out["model"])
        self.assertIn("interval", out["precision"])
        self.assertIn("estimate", out["assumptions"]["mlt"])
        self.assertIn("never a vendor number", out["assumptions"]["mlt"])
        self.assertGreaterEqual(len(out["value"]["not_modelled"]), 5)

class TestTheCopper(unittest.TestCase):

    def test_r_dc_is_rho_n_mlt_over_area_computed_here(self):
        wiring = _call(turns=54, wire_diameter_mm=1.0, i_rms_a=5.0,
                       fill_factor=0.4)["value"]["winding"]
        rho = 1.724e-8 * (1.0 + 0.00393 * (100.0 - 20.0))
        area = math.pi * 0.5 * 0.5 * 1e-6
        self.assertAlmostEqual(wiring["wire_area_mm2"], math.pi * 0.25, places=6)
        self.assertAlmostEqual(wiring["r_dc_ohm"],
                               rho * 54 * FLAT * RATIO_MED * 1e-3 / area, places=6)
        self.assertAlmostEqual(wiring["r_dc_ohm"], 0.073719, places=6)
        self.assertAlmostEqual(wiring["p_copper_w"], 25.0 * wiring["r_dc_ohm"],
                               places=4)
        self.assertAlmostEqual(wiring["p_copper_w"], 1.842964, places=5)

    def test_the_range_comes_from_the_mlt_band_and_only_from_it(self):
        wiring = _call(turns=54, wire_diameter_mm=1.0)["value"]["winding"]
        rho = 1.724e-8 * (1.0 + 0.00393 * 80.0)
        area = math.pi * 0.25 * 1e-6
        lo, hi = wiring["r_dc_range_ohm"]
        self.assertAlmostEqual(lo, rho * 54 * 43.218817 * 1e-3 / area, places=6)
        self.assertAlmostEqual(hi, rho * 54 * 50.59075 * 1e-3 / area, places=6)
        self.assertLess(lo, wiring["r_dc_ohm"])
        self.assertLess(wiring["r_dc_ohm"], hi)

    def test_n1_and_the_layer_count_come_from_the_bore(self):
        wiring = _call(turns=54, wire_diameter_mm=1.0)["value"]["winding"]
        n1 = int(math.floor(math.pi * (ID - 1.0) / 1.0))
        self.assertEqual(n1, 42)
        self.assertEqual(wiring["single_layer_max_turns"], n1)
        self.assertEqual(wiring["layers"], 2)
        self.assertIn("N1 = pi*(ID - d)/d", wiring["single_layer_basis"])

    def test_more_than_one_layer_is_flagged_as_a_low_bias(self):
        two = _call(turns=54, wire_diameter_mm=1.0)["value"]["winding"]
        one = _call(turns=42, wire_diameter_mm=1.0)["value"]["winding"]
        self.assertEqual(one["layers"], 1)
        self.assertNotIn("multilayer_note", one)
        self.assertIn("biased LOW", two["multilayer_note"])
        self.assertIn("single-winding", two["multilayer_note"])

    def test_temperature_moves_the_copper_by_the_copper_constant(self):
        hot = _call(turns=54, wire_diameter_mm=1.0)["value"]["winding"]["r_dc_ohm"]
        cold = _call(turns=54, wire_diameter_mm=1.0,
                     temperature_c=20.0)["value"]["winding"]["r_dc_ohm"]
        self.assertAlmostEqual(cold, 0.056085, places=6)
        self.assertAlmostEqual(hot / cold, 1.0 + 0.00393 * 80.0, places=4)

    def test_the_window_says_out_loud_when_the_winding_does_not_fit(self):
        fits = _call(turns=54, wire_diameter_mm=1.0,
                     fill_factor=0.4)["value"]["winding"]
        self.assertIs(fits["window_fits"], True)
        self.assertAlmostEqual(fits["window_area_needed_mm2"], 106.028752, places=5)
        self.assertNotIn("window_note", fits)
        tight = _call(turns=54, wire_diameter_mm=1.0,
                      fill_factor=0.05)["value"]["winding"]
        self.assertIs(tight["window_fits"], False)
        self.assertIn("cannot be put on this core", tight["window_note"])
        self.assertIn("not what can be wound", tight["window_note"])

    def test_a_wire_without_turns_still_gets_the_length(self):
        out = _call(wire_diameter_mm=1.0)["value"]
        self.assertEqual(out["winding"]["single_layer_max_turns"], 42)
        self.assertIsNone(out["winding"]["r_dc_ohm"])
        self.assertIsNone(out["winding"]["turns"])

class TestEveryImpossibleQuestionIsRefused(unittest.TestCase):

    def test_the_geometry_arms(self):
        for kw, code in (
                (dict(od_mm=ID, id_mm=OD), "ARG_RANGE"),
                (dict(od_mm=-1.0), "ARG_RANGE"),
                (dict(ht_mm=0.0), "ARG_RANGE")):
            args = {"od_mm": OD, "id_mm": ID, "ht_mm": HT}
            args.update(kw)
            out = mag.magnetics_toroid_mlt(**args)
            self.assertEqual(out["status"], "error", kw)
            self.assertEqual(out["reason_code"], code, kw)

    def test_the_wire_arms(self):
        self.assertEqual(_call(wire_diameter_mm=ID)["reason_code"], "ARG_RANGE")
        self.assertEqual(_call(wire_diameter_mm=0.0)["reason_code"], "ARG_RANGE")
        self.assertEqual(_call(wire_diameter_mm="thick")["reason_code"],
                         "ARG_INVALID")
        self.assertEqual(_call(turns=10)["reason_code"], "ARG_INVALID")
        self.assertEqual(_call(turns=0, wire_diameter_mm=1.0)["reason_code"],
                         "ARG_RANGE")
        self.assertEqual(_call(turns=10.5, wire_diameter_mm=1.0)["reason_code"],
                         "ARG_RANGE")
        self.assertEqual(_call(turns=10, wire_diameter_mm=1.0,
                               fill_factor=2.0)["reason_code"], "ARG_RANGE")
        self.assertEqual(_call(wire_diameter_mm=1.0,
                               i_rms_a=1.0)["reason_code"], "ARG_INVALID")
        self.assertEqual(_call(turns=10, wire_diameter_mm=1.0,
                               i_rms_a=-1.0)["reason_code"], "ARG_RANGE")

    def test_a_missing_table_is_data_unavailable_and_not_a_default_band(self):
        out = mag.magnetics_toroid_mlt(OD, ID, HT,
                                       mlt_data_path="data/no_such_table.json")
        self.assertEqual(out["status"], "error")
        self.assertEqual(out["reason_code"], "DATA_UNAVAILABLE")
        self.assertIn("toroid_mlt_reference.json", out["hint"])

class TestSelectCoreKeepsItsOldAnswer(unittest.TestCase):
    """0.0.78's answer must not move when the flag is off."""

    ARGS = {"inductance_h": 1e-4, "i_peak_a": 5.0, "i_rms_a": 1.0,
            "current_density_a_mm2": 4.0, "include_powder_cores": True,
            "material": "MPP", "permeability": 60, "limit": 2}

    def test_off_and_omitted_are_byte_identical(self):
        off = mag.select_core(toroid_mlt="off", **self.ARGS)
        omitted = mag.select_core(**self.ARGS)
        self.assertEqual(json.dumps(off, sort_keys=True),
                         json.dumps(omitted, sort_keys=True))
        self.assertEqual(off["status"], "ok")
        self.assertEqual(len(off["value"]["candidates"]), 2)

    def test_off_still_says_the_length_is_missing(self):
        row = mag.select_core(toroid_mlt="off", **self.ARGS)["value"]["candidates"][0]
        self.assertIsNone(row["winding"]["p_copper_w"])
        self.assertIn("no mean turn length", row["winding"]["missing_wire"])
        self.assertNotIn("mlt_mm", row["winding"])

    def test_estimate_adds_a_winding_and_touches_nothing_else(self):
        off = mag.select_core(toroid_mlt="off", **self.ARGS)["value"]["candidates"][0]
        on = mag.select_core(toroid_mlt="estimate",
                             **self.ARGS)["value"]["candidates"][0]
        self.assertEqual(on["part"], off["part"])
        self.assertEqual(on["turns"], off["turns"])
        self.assertIs(on["winding"]["mlt_is_estimate"], True)
        self.assertAlmostEqual(on["winding"]["mlt_mm"], 6.583811, places=5)
        self.assertEqual(on["winding"]["mlt_range_mm"], [6.013704, 7.039475])
        self.assertLess(on["winding"]["mlt_range_mm"][0], on["winding"]["mlt_mm"])
        self.assertLess(on["winding"]["mlt_mm"], on["winding"]["mlt_range_mm"][1])
        self.assertIsNotNone(on["winding"]["r_dc_ohm"])
        self.assertIsNotNone(on["winding"]["p_copper_w"])
        self.assertIn("ESTIMATE", on["winding"]["p_copper_basis"])

    def test_a_mode_that_is_not_a_mode_is_refused(self):
        out = mag.select_core(toroid_mlt="maybe", **self.ARGS)
        self.assertEqual(out["status"], "error")
        self.assertEqual(out["reason_code"], "ARG_INVALID")

    def test_the_top_level_block_only_appears_when_asked(self):
        off = mag.select_core(toroid_mlt="off", **self.ARGS)["value"]
        on = mag.select_core(toroid_mlt="estimate", **self.ARGS)["value"]
        self.assertNotIn("toroid_mlt", off)
        block = on["toroid_mlt"]
        self.assertEqual(block["mode"], "estimate")
        self.assertEqual(block["ratio_band"]["n"], 13)
        self.assertEqual(block["rows_with_an_estimate"], len(on["candidates"]))
        self.assertEqual(block["rows_without_one"], [])

    def test_the_schema_carries_the_flag_and_the_handler_passes_it(self):
        prop = tools.TOOLS["magnetics_select_core"]["inputSchema"]["properties"]
        self.assertIn("toroid_mlt", prop)
        self.assertEqual(prop["toroid_mlt"]["enum"], ["off", "estimate"])
        self.assertEqual(prop["toroid_mlt"]["default"], "off")
        args = dict(self.ARGS)
        args.update(od_mm=OD, id_mm=ID, ht_mm=HT, toroid_mlt="estimate")
        out = tools.TOOLS["magnetics_select_core"]["handler"](args)
        self.assertEqual(out["status"], "ok")
        self.assertIn("toroid_mlt", out["value"])

class TestThePowderRowsCarryTheEstimate(unittest.TestCase):

    @classmethod
    def setUpClass(cls):
        with io.open(os.path.join(ROOT, "data", "powder_cores.json"),
                     encoding="utf-8") as fh:
            cls.rows = mag._powder_core_rows(json.load(fh))

    def test_every_row_gets_a_flat_perimeter_a_length_and_a_band(self):
        self.assertGreater(len(self.rows), 1900)
        for row in self.rows:
            self.assertAlmostEqual(
                row["mlt_flat_rect_mm"],
                2.0 * row["ht_mm"] + (row["od_mm"] - row["id_mm"]), places=5)
            self.assertAlmostEqual(row["mlt_estimate_mm"],
                                   row["mlt_flat_rect_mm"] * RATIO_MED, places=4)
            self.assertEqual(len(row["mlt_range_mm"]), 2)
            self.assertLess(row["mlt_range_mm"][0], row["mlt_range_mm"][1])

    def test_the_printed_column_is_still_empty_and_the_note_still_says_so(self):
        for row in self.rows[:50]:
            self.assertIsNone(row["mlt_mm"])
            self.assertIn("no mean turn length", row["mlt_note"])
            self.assertIn("not a printed value", row["mlt_note"])

class TestTheToolSurfaceMovedOnce(unittest.TestCase):

    def test_the_tool_exists_and_names_its_limits(self):
        self.assertIn("magnetics_toroid_mlt", tools.TOOLS)
        text = tools.TOOLS["magnetics_toroid_mlt"]["description"]
        self.assertIn("MEASURED band", text)
        self.assertIn("never a lookup", text)
        self.assertIn("N1 = pi*(ID-d)/d", text)
        self.assertIn("LOWER bound", text)
        self.assertIn("skin and proximity", text)

    def test_the_capabilities_list_it_and_stop_calling_mlt_missing(self):
        caps = tools._capabilities({})
        self.assertIn("magnetics_toroid_mlt", caps["available"])
        # 174 = 173 + thermal_spreading in 0.0.85
        self.assertEqual(len(caps["available"]), 186)
        joined = json.dumps(caps)
        self.assertIn("toroid_mlt", joined)
        self.assertNotIn("(so no toroid copper estimate)", joined)

    def test_the_tool_count_is_one_higher_than_0_0_78(self):
        # 179 = 178 + magnetics_core_loss_audit in 0.0.81 (scoring a triple against
        # the vendor's printed points is a different question from evaluating a fit)
        # 180 = 179 + thermal_spreading in 0.0.85 (a rectangular heat source's
        # spreading resistance is its own question; thermal_stack stays strictly 1-D)
        # 203 = 200 + the three 0.0.121 additions (board_net_report, board_pour_audit,
        # board_render): being able to sort nets by detour, to see a pour's
        # islands, and to LOOK at the copper are three separate questions,
        # each of which the previous 200 tools could not answer.
        self.assertEqual(len(tools.TOOLS), 203)

if __name__ == "__main__":
    unittest.main()
