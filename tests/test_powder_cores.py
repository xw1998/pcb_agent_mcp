'''The powder-core toroids: the printed grid, its geometry, and what is refused.

What is being checked, and why each check can fail:

  (1) the table is the one this build ships: schema, 43 printed sizes, 500
      permeability rows, 1923 purchasable part numbers over nine material
      columns. A re-parse that loses the pages the vendor leaves cells blank on
      shows up here as a row/part count that moved.
  (2) the geometry is self-consistent WITHOUT looking at the vendor's word for
      it: Ve must equal Ae*Le, Le must equal the log-mean path pi(OD-ID)/ln(OD/ID),
      and Ae must be a sane fraction of the rectangular section HT*(OD-ID)/2. A
      transcription slip in any of the three breaks one of the three, and the one
      page where the vendor's own three numbers disagree is named, not smoothed.
  (3) the column a part number sits in is checked against the material code
      INSIDE the part number, on all 1923 cells. This is the check that catches a
      column-shifted parse, which the geometry checks cannot see.
  (4) every permeability a column carries is one the catalog's own comparison
      page lists for that material - a structural check on the 500 rows.
  (5) AL/mu is flat inside one page-column (it is a geometry ratio), so the three
      cells that break it by >12% are listed; all three are the same printed row
      on one page, and the page image was checked before it was believed.
  (6) the four vendor printing anomalies are kept verbatim. Correcting them would
      be inventing a part number, and dropping the row would be losing one.
  (7) the mass is an ESTIMATE (Ve x printed density x printed factor) and it is
      refused, not interpolated, for the three permeabilities the vendor prints no
      factor for.
  (8) zero change: with include_powder_cores absent (the default), the pool is
      still the 16 solid cores and a known baseline answer is unchanged to the
      digit. This is the check that fails if the powder table ever leaks into the
      default path.
  (9) a powder part can be selected and its answers are the vendor's: the Bsat is
      the value printed for its column, the core loss is the vendor's own
      per-permeability fit times Ve, and there is NO gap length because a powder
      core has no discrete gap.
 (10) the refusals: permeability without include_powder_cores is an argument
      error rather than a silently ignored argument; a permeability no column
      prints returns nothing rather than the nearest one; an unknown material is
      named.
 (11) the two saturation values that arrived in 0.0.76 (MPP 0.8 T, High Flux 1.5 T)
      are the catalog's comparison page and say so, because a filled-in field with
      no provenance is worse than a null.
'''
import io
import json
import math
import os
import statistics
import sys
import unittest

sys.path.insert(0, os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "src"))

from pcb_agent_mcp.calc import magnetics as mg  # noqa: E402
from pcb_agent_mcp import tools  # noqa: E402

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
POWDER = os.path.join(ROOT, "data", "powder_cores.json")
MATERIALS = os.path.join(ROOT, "data", "magnetic_materials.json")


def load():
    with io.open(POWDER, encoding="utf-8") as fh:
        return json.load(fh)


def cells(data):
    """Every non-empty cell, as (size, row, column_index, part)."""
    cols = data["column_order"]
    for size in data["sizes"]:
        for row in size["rows"]:
            for i, part in enumerate(row["parts"]):
                if isinstance(part, str) and part.strip() not in ("", "-"):
                    yield size, row, i, cols[i], part.strip()


class TestThePrintedGrid(unittest.TestCase):
    def test_the_table_is_the_one_this_build_ships(self):
        data = load()
        self.assertEqual(data["schema"], mg.POWDER_SCHEMA)
        self.assertEqual(len(data["sizes"]), 43)
        self.assertEqual(sum(len(s["rows"]) for s in data["sizes"]), 500)
        self.assertEqual(sum(1 for _ in cells(data)), 1923)
        self.assertEqual(len(data["column_order"]), 9)
        # the pages the numbers come from, and that the printed page is what is
        # cited (the PDF page is one ahead throughout this catalog)
        pages = [s["printed_page"] for s in data["sizes"]]
        self.assertEqual(pages, sorted(pages))
        self.assertEqual(pages[0], 155)
        self.assertEqual(pages[-1], 197)
        for s in data["sizes"]:
            self.assertEqual(s["pdf_page"], s["printed_page"] + 1)

    def test_the_data_file_loads_through_the_public_kind(self):
        e = mg.magnetic_data(kind="powder_cores")
        self.assertEqual(e["status"], "ok")
        self.assertEqual(e["value"]["count"], 43)
        e = mg.magnetic_data(kind="powder_parts", limit=5)
        self.assertEqual(e["status"], "ok")
        self.assertEqual(e["value"]["count"], 5)
        self.assertEqual(e["value"]["total_matching"], 1923)


class TestGeometryWithoutTrustingTheVendor(unittest.TestCase):
    def test_ve_equals_ae_times_le_on_every_page_but_one(self):
        data = load()
        bad = []
        for s in data["sizes"]:
            g = s["geometry"]
            ratio = g["ve_mm3"] / (g["ae_mm2"] * g["le_mm"])
            self.assertAlmostEqual(ratio, g["ve_selfcheck_ratio"], places=3)
            if abs(ratio - 1.0) > 0.02:
                bad.append((s["printed_page"], round(ratio, 4)))
        # exactly one page disagrees with itself, and it is the one where the
        # printed Ve is inconsistent with the printed Ae and Le
        self.assertEqual(bad, [(193, 0.9348)])

    def test_the_one_bad_page_is_the_printed_ve_not_our_reading(self):
        data = load()
        s = [x for x in data["sizes"] if x["printed_page"] == 193][0]
        g = s["geometry"]
        # Le is its own check and passes to 0.1 %, so Le was read right
        self.assertLess(abs(g["le_mm"] - g["le_mm_log_mean_check"]) / g["le_mm"], 0.01)
        # and Ae is a sane fraction of the rectangular section, so Ae was read right
        rect = s["ht_mm"] * (s["od_mm"] - s["id_mm"]) / 2.0
        self.assertTrue(0.90 <= g["ae_mm2"] / rect <= 0.99)
        # therefore the disagreement is in the printed Ve, and it is kept as printed
        self.assertEqual(g["ve_mm3"], 81500.0)
        self.assertEqual(g["ae_mm2"], 492.0)
        self.assertEqual(g["le_mm"], 177.2)
        note = [a for a in data["printing_anomalies"] if a["printed_page"] == 193]
        self.assertEqual(len(note), 1)
        self.assertIn("mutually inconsistent", note[0]["why"])

    def test_le_equals_the_log_mean_path_on_every_page(self):
        data = load()
        worst = 0.0
        for s in data["sizes"]:
            od, idm = s["od_mm"], s["id_mm"]
            logmean = math.pi * (od - idm) / math.log(od / idm)
            err = abs(logmean - s["geometry"]["le_mm"]) / s["geometry"]["le_mm"]
            worst = max(worst, err)
        self.assertLess(worst, 0.01)

    def test_ae_is_a_sane_fraction_of_the_rectangular_section(self):
        data = load()
        ratios = [s["geometry"]["ae_mm2"] / (s["ht_mm"] * (s["od_mm"] - s["id_mm"]) / 2.0)
                  for s in data["sizes"]]
        self.assertGreater(min(ratios), 0.85)
        self.assertLess(max(ratios), 1.0)


class TestTheColumnIsTheMaterial(unittest.TestCase):
    CODES = {"55": "MPP", "58": "High Flux", "59": "Edge",
             "70": "Kool M\u00b5 Ultra", "74": "XFlux Ultra", "76": "Kool M\u00b5 H\u0192",
             "77": "Kool M\u00b5", "78": "XFlux", "79": "Kool M\u00b5 MAX"}

    def test_the_material_code_inside_each_part_number_agrees_with_its_column(self):
        data = load()
        mismatched = []
        for size, row, i, col, part in cells(data):
            code = part[2:4]
            if self.CODES.get(code) != col:
                mismatched.append((size["printed_page"], row["perm"], col, part))
        # two cells carry another column's part number; both are the vendor's own
        # printing error and both are registered (see the next test)
        self.assertEqual(len(mismatched), 2)
        self.assertEqual(mismatched[0][2:], ("MPP", "0058330A2"))
        self.assertEqual(mismatched[1][2:], ("Edge", "0058871A2"))

    def test_the_five_printing_anomalies_are_kept_verbatim(self):
        data = load()
        anomalies = data["printing_anomalies"]
        self.assertEqual(len(anomalies), 5)
        by_page = {}
        for a in anomalies:
            by_page.setdefault(a["printed_page"], []).append(a)
        # two part numbers printed in the wrong column, kept as printed
        self.assertEqual(by_page[176][0]["printed"], "0058330A2")
        self.assertEqual(by_page[176][0]["column"], "MPP")
        self.assertEqual(by_page[191][0]["printed"], "0058871A2")
        self.assertEqual(by_page[191][0]["column"], "Edge")
        # one cell where the letter O stands where every other cell has a zero
        self.assertEqual(by_page[162][0]["printed"], "CO58031A2")
        self.assertIn("letter O", by_page[162][0]["why"])
        # and it really is in the table that way, not normalised on the way in
        parts = [p for _, _, _, _, p in cells(data)]
        self.assertIn("CO58031A2", parts)
        self.assertNotIn("C058031A2", parts)
        # every anomaly says how it was confirmed, and geometry one is geometry
        for a in anomalies:
            self.assertTrue(a.get("verified_by"))
        self.assertEqual(by_page[193][0]["column"], "geometry")
        # one part number printed against two different sizes: both rows stay
        self.assertEqual(by_page[166][0]["printed"], "0058133A2")
        self.assertIn("p.168", by_page[166][0]["why"])
        self.assertIn("two sizes", by_page[166][0]["why"])

    def test_every_permeability_a_column_carries_is_one_the_catalog_prints(self):
        data = load()
        printed = data["material_comparison_printed_page_10"]["rows"]
        checked = 0
        for i, col in enumerate(data["column_order"]):
            have = {r["perm"] for s in data["sizes"] for r in s["rows"]
                    if isinstance(r["parts"][i], str) and r["parts"][i] != "-"}
            lo = printed[col]["available_permeabilities_printed"]["min"]
            hi = printed[col]["available_permeabilities_printed"]["max"]
            self.assertTrue(have, col)
            self.assertEqual([p for p in have if p < lo or p > hi], [], col)
            checked += 1
        self.assertEqual(checked, 9)

    def test_al_over_mu_is_flat_inside_one_page_column_but_for_three_cells(self):
        data = load()
        far = []
        for s in data["sizes"]:
            for i, col in enumerate(data["column_order"]):
                vals = [(r["perm"], r["al_nh"], r["al_nh"] / r["perm"])
                        for r in s["rows"]
                        if isinstance(r["parts"][i], str) and r["parts"][i] != "-"
                        and r["al_nh"] and r["perm"]]
                if len(vals) < 3:
                    continue
                med = statistics.median(v[2] for v in vals)
                for perm, al, ratio in vals:
                    if abs(ratio / med - 1.0) > 0.12:
                        far.append((s["printed_page"], col, perm, al))
        # all three are the same printed row on one page: AL 18 where the column's
        # own median implies 16. The page image was rendered and the value is 18.
        self.assertEqual(far, [(158, "Kool M\u00b5", 40, 18),
                               (158, "High Flux", 40, 18),
                               (158, "MPP", 40, 18)])


class TestTheExpandedParts(unittest.TestCase):
    def test_one_row_per_printed_cell_with_the_fields_select_core_needs(self):
        data = load()
        rows = mg._powder_core_rows(data)
        # one row per printed cell - 1923 of them - and 1920 distinct strings,
        # because three part numbers are printed against two places each. Those
        # three collisions are named below and every one of them is registered in
        # printing_anomalies; a row that went missing would move 1923, and a
        # collision that went unregistered would move the 1920.
        self.assertEqual(len(rows), 1923)
        counts = {}
        for r in rows:
            counts[r["part"]] = counts.get(r["part"], 0) + 1
        self.assertEqual(len(counts), 1920)
        self.assertEqual(sorted(p for p, n in counts.items() if n > 1),
                         ["0058133A2", "0058330A2", "0058871A2"])
        registered = {a["printed"] for a in data["printing_anomalies"]}
        for part in ("0058133A2", "0058330A2", "0058871A2"):
            self.assertIn(part, registered)
        need = ("part", "shape", "material", "permeability", "ae_mm2", "le_mm",
                "ve_mm3", "al_nh", "aw_mm2", "mass_g", "loss_fit_lookup",
                "al_tolerance_pct", "printed_page")
        for r in rows[:200]:
            for k in need:
                self.assertIn(k, r)
            self.assertEqual(r["shape"], "toroid")
            self.assertTrue(r["gap_is_distributed"])
            self.assertEqual(r["finish"], r["part"][-2:])
            self.assertEqual(r["loss_fit_lookup"]["variant"], r["material_variant"])
            self.assertEqual(r["loss_fit_lookup"]["permeability"], r["permeability"])

    def test_the_al_tolerance_comes_from_the_part_number_and_the_od(self):
        data = load()
        rows = mg._powder_core_rows(data, material="MPP", permeability=147)
        self.assertTrue(rows)
        for r in rows:
            prefix = r["part"][:2]
            self.assertIn(prefix, ("C0", "00"))
            if prefix == "C0":
                self.assertEqual(r["al_tolerance_pct"],
                                 2 if r["od_mm"] > 4.65 else 5)
            else:
                # 00 is the ungraded cell: one printed AL with the +-8 % band
                self.assertEqual(r["al_tolerance_pct"], 8)
        self.assertIn("C0", {r["part"][:2] for r in rows})
        # an 00 prefix is the single printed value with the +-8 % band
        rows = mg._powder_core_rows(data, material="Kool Mu", permeability=60)
        self.assertIn(8, {r["al_tolerance_pct"] for r in rows})

    def test_mass_is_an_estimate_and_is_refused_without_a_printed_factor(self):
        data = load()
        # 60u: the printed density IS the value used, so factor 1.0 and no error
        r = [x for x in mg._powder_core_rows(data, material="MPP", permeability=60)][0]
        dens = data["material_properties_printed_page_16"]["rows"]["MPP"]["density_60u_g_cm3"]
        self.assertAlmostEqual(r["mass_g"], r["ve_mm3"] * 1e-3 * dens, places=4)
        self.assertIn("estimate", r["mass_basis"])
        for mu in (200, 300, 550):
            r = mg._powder_core_rows(data, material="MPP", permeability=mu)[0]
            self.assertIsNone(r["mass_g"])
            self.assertIn("refused rather than interpolated", r["mass_basis"])
        # the catalog says the nominal weight is on the individual data sheet
        self.assertIn("estimate", data["material_properties_printed_page_16"]
                      ["weight_is_an_estimate"])


class TestSelection(unittest.TestCase):
    def test_the_powder_table_does_not_leak_into_the_default_path(self):
        a = mg.select_core(100e-6, 5.0, data_path=MATERIALS)
        b = mg.select_core(100e-6, 5.0, include_powder_cores=False, data_path=MATERIALS)
        self.assertEqual(json.dumps(a, sort_keys=True), json.dumps(b, sort_keys=True))
        self.assertEqual(a["value"]["table_rows"], 16)
        self.assertNotIn("core_pool", a["value"])
        for c in a["value"]["candidates"]:
            self.assertNotIn("permeability", c)

    def test_the_solid_core_baseline_did_not_move(self):
        # the 0.0.75 answer for this requirement, to the digit: a change here is a
        # change in the default path, which this release is not allowed to make
        e = mg.select_core(100e-6, 5.0, data_path=MATERIALS)
        self.assertEqual(e["value"]["feasible_count"], 9)
        c = e["value"]["candidates"][0]
        self.assertEqual(c["part"], "B66283G0000X187")
        self.assertEqual(c["turns"], 41)
        self.assertEqual(c["b_peak_t"], 0.310308)
        self.assertEqual(c["b_limit_t"], 0.312)
        self.assertEqual(c["gap_mm"], 0.81913)
        self.assertEqual(c["saturation"], "ok")

    def test_a_powder_part_can_be_selected_and_says_what_it_cannot_do(self):
        e = mg.select_core(100e-6, 5.0, include_powder_cores=True, material="MPP",
                           permeability=60, freq_hz=100e3, b_ac_t=0.05, limit=1,
                           data_path=MATERIALS)
        self.assertEqual(e["status"], "ok")
        v = e["value"]
        # the MPP column prices 41 cells at 60 u in this catalog
        self.assertEqual(v["core_pool"]["parts"], 41)
        self.assertEqual(v["core_pool"]["permeability_filter"], 60.0)
        self.assertEqual(v["core_pool"]["solid_cores"], 16)
        c = v["candidates"][0]
        self.assertEqual(c["part"], "0055141A2")
        self.assertEqual(c["permeability"], 60.0)
        self.assertEqual(c["turns"], 601)
        # the Bsat is the single value printed for the MPP column, and the answer
        # says the vendor printed no temperature against it
        self.assertEqual(c["b_limit_t"], 0.64)
        self.assertEqual(c["saturation"], "ok")
        self.assertIn("not printed by the vendor", c["b_limit_temperature"])
        # no gap to compute: a powder core's gap is distributed
        self.assertIsNone(c["gap_mm"])
        self.assertIn("distributed", c["gap_because"])
        # and the loss is the vendor's own fit, not a number this build invented
        self.assertIsNotNone(c["core_loss_w"])
        self.assertIn("vendor printed fit", c["core_loss_basis"])

    def test_the_powder_core_loss_is_the_vendor_fit_times_ve(self):
        e = mg.select_core(100e-6, 5.0, include_powder_cores=True, material="MPP",
                           permeability=60, freq_hz=100e3, b_ac_t=0.05, limit=1,
                           data_path=MATERIALS)
        c = e["value"]["candidates"][0]
        ref = mg.material_core_loss("MPP", 100e3, 0.05, permeability=60, shape="toroid")
        self.assertEqual(ref["status"], "ok")
        pv_cm3 = ref["value"]["P_v_mw_cm3"]
        expected = pv_cm3 * (c["ve_mm3"] * 1e-3) * 1e-3
        # the public answer rounds to 4 decimals and P_v is itself printed to 6, so
        # the comparison is at the precision the answer is published with
        self.assertAlmostEqual(c["core_loss_w"], expected, places=4)

    def test_the_triangle_flux_moves_the_powder_loss_by_the_igse_ratio(self):
        kw = dict(include_powder_cores=True, material="MPP", permeability=60,
                  freq_hz=100e3, b_ac_t=0.05, limit=1, data_path=MATERIALS)
        sine = mg.select_core(100e-6, 5.0, **kw)["value"]["candidates"][0]
        tri = mg.select_core(100e-6, 5.0, flux_waveform="triangle", duty=0.4,
                             **kw)["value"]["candidates"][0]
        self.assertLess(tri["core_loss_w"], sine["core_loss_w"])
        self.assertIn("iGSE shape ratio", tri["core_loss_basis"])

    def test_no_copper_is_estimated_for_a_toroid(self):
        e = mg.select_core(100e-6, 5.0, include_powder_cores=True, material="MPP",
                           permeability=60, i_rms_a=1.0, current_density_a_mm2=4.0,
                           limit=1, data_path=MATERIALS)
        w = e["value"]["candidates"][0]["winding"]
        self.assertIsNone(w["p_copper_w"])
        self.assertIn("no mean turn length", w["missing_wire"])
        self.assertIn("p_copper_w", e["value"]["candidates"][0]["missing"])


class TestTheRefusals(unittest.TestCase):
    def test_permeability_without_the_powder_pool_is_an_argument_error(self):
        e = mg.select_core(100e-6, 5.0, permeability=60, data_path=MATERIALS)
        self.assertEqual(e["status"], "error")
        self.assertEqual(e["reason_code"], "ARG_INVALID")
        self.assertIn("include_powder_cores", e["hint"])

    def test_a_permeability_no_column_prints_returns_nothing(self):
        e = mg.magnetic_data(kind="powder_parts", material="MPP", permeability=55)
        self.assertEqual(e["status"], "error")
        self.assertEqual(e["reason_code"], "NOT_FOUND")
        # and the narrow is named rather than read as "MPP is not in the table"
        e = mg.select_core(100e-6, 5.0, include_powder_cores=True, material="MPP",
                           permeability=55, data_path=MATERIALS)
        self.assertEqual(e["status"], "error")
        self.assertEqual(e["reason_code"], "NOT_FOUND")
        self.assertIn("55", e["message"])
        self.assertIn(60.0, e["permeabilities_printed"])
        self.assertEqual(e["material"], "MPP")

    def test_an_unknown_material_is_named_not_guessed(self):
        e = mg.select_core(100e-6, 5.0, include_powder_cores=True,
                           material="Kool Muu", data_path=MATERIALS)
        self.assertEqual(e["status"], "error")
        self.assertEqual(e["reason_code"], "NOT_FOUND")
        self.assertIn("Kool Muu", e["message"])

    def test_a_bad_table_schema_is_refused_rather_than_read(self):
        tmp = os.path.join(ROOT, "_scratch", "powder_schema_wrong.json")
        # written under _scratch and left there: this environment refuses a
        # permanent delete (os.unlink raises) and a test may not empty the bin
        with io.open(tmp, "w", encoding="utf-8") as fh:
            fh.write('{"schema": "something-else", "sizes": []}')
        data, why = mg._load_powder_cores(tmp)
        self.assertIsNone(data)
        self.assertIn("schema", why)
        e = mg.magnetic_data(kind="powder_cores", powder_data_path=tmp)
        self.assertEqual(e["status"], "error")
        self.assertEqual(e["reason_code"], "DATA_UNAVAILABLE")

    def test_an_unmappable_column_heading_aborts_the_load(self):
        data = load()
        data = json.loads(json.dumps(data))
        data["column_order"][0] = "Kool Mx"
        tmp = os.path.join(ROOT, "_scratch", "powder_bad_column.json")
        with io.open(tmp, "w", encoding="utf-8") as fh:
            json.dump(data, fh)
        got, why = mg._load_powder_cores(tmp)
        self.assertIsNone(got)
        self.assertIn("Kool Mx", why)


class TestTheFilledSaturationValues(unittest.TestCase):
    def test_mpp_and_high_flux_saturation_came_from_the_comparison_page(self):
        e = mg.magnetic_data(kind="materials", data_path=MATERIALS)
        self.assertEqual(e["status"], "ok")
        got = {r["name"]: r for r in e["value"]["rows"]}
        self.assertEqual(got["MPP"]["bsat_mt_25c"], 800)
        self.assertEqual(got["High Flux"]["bsat_mt_25c"], 1500)
        for name in ("MPP", "High Flux"):
            note = got[name]["bsat_note"]
            self.assertIn("CHANGED in 0.0.76", note)
            self.assertIn("p.10", note)
            self.assertIn("does NOT print a temperature" if name == "MPP"
                          else "No temperature is printed", note)
        # and the number agrees with the powder table's own copy of that page
        data = load()
        printed = data["material_comparison_printed_page_10"]["rows"]
        self.assertEqual(printed["MPP"]["sat_flux_density_t"] * 1000, 800)
        self.assertEqual(printed["High Flux"]["sat_flux_density_t"] * 1000, 1500)

    def test_the_tool_count_did_not_move(self):
        # 0.0.76 widens magnetics_data's kind and adds two arguments to
        # magnetics_select_core; it adds no tool, and the pool is opt-in.
        # 179 = 178 + magnetics_core_loss_audit in 0.0.81 (scoring a triple against
        # the vendor's printed points is a different question from evaluating a fit)
        # 180 = 179 + thermal_spreading in 0.0.85 (a rectangular heat source's
        # spreading resistance is its own question; thermal_stack stays strictly 1-D)
        # 223 = 220 + the three routing-discipline tools in 0.0.122
        self.assertEqual(len(tools.TOOLS), 229)
        props = tools.TOOLS["magnetics_select_core"]["inputSchema"]["properties"]
        self.assertIn("include_powder_cores", props)
        self.assertIn("permeability", props)
        self.assertIn("powder_cores",
                      tools.TOOLS["magnetics_data"]["inputSchema"]["properties"]["kind"]["enum"])


if __name__ == "__main__":
    unittest.main()
