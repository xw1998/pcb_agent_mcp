"""0.0.81: the vendors' PRINTED loss points, and the audit for a triple you bring.

What is being pinned here, in order:

  * the anchor table is a transcription, not a fit: every one of the 17 points
    carries the page it was printed on, the exact text that was printed, and a
    qualifier from a closed set of four. The counts and the qualifier split are
    recomputed from the file here rather than quoted from the answer.
  * a bound and a typical value are different kinds of number, and the two
    fields that report them are kept apart: `satisfies_the_printed_bound` is only
    ever true/false where the page printed <= or Max., and only ever None where
    it printed a typical value. 3C90's page prints both kinds in one column,
    which is why no triple can be solved out of it - and `solved_here` is false
    with the reason travelling with the answer.
  * every point is recomputed from the triple here, in all four unit
    conventions, so a units mistake cannot hide behind the verdict.
  * the negative controls are tables that have been tampered with: a wrong
    schema, a negative Pv, a misspelled qualifier, a source id that is not in
    `sources`, a truncated material list, and a Pv column set to zero. Each is
    refused by the loader rather than evaluated - a reader that guessed would
    answer from a shifted column.
  * the refusals are their own: a grade with a specification page but no PV row
    (3B7, N27) answers DATA_UNAVAILABLE with points_printed = 0 and says why, an
    untranscribed material NOT_FOUND with the list, an unknown unit convention
    ARG_INVALID, a temperature with no printed point OUT_OF_VALIDITY with the
    temperatures that exist, a non-positive exponent ARG_RANGE, and a missing
    table DATA_UNAVAILABLE.
  * one thing DID change in an existing answer, and only one: the hint on
    magnetics_material_core_loss's FIT_NOT_RESOLVED refusal now names this tool.
    The reason code, the message and every number are untouched, and that is
    asserted here so a future edit cannot quietly widen it.
"""
import copy
import io
import json
import os
import unittest

from pcb_agent_mcp.calc import magnetics as mag
from pcb_agent_mcp import tools

HERE = os.path.dirname(os.path.abspath(__file__))
ROOT = os.path.dirname(HERE)
SCRATCH = os.path.join(ROOT, "_scratch")
TABLE = os.path.join(ROOT, "data", "material_loss_anchors.json")

QUALIFIERS = ("at_most", "approx", "plain", "max")
BOUNDS = ("at_most", "max")

def _table():
    with io.open(TABLE, encoding="utf-8") as fh:
        return json.load(fh)

def _call(**kw):
    return tools.TOOLS["magnetics_core_loss_audit"]["handler"](kw)

def _all_points(data):
    out = []
    for name, entry in (data.get("materials") or {}).items():
        for p in entry.get("points") or []:
            out.append((name, entry, p))
    return out

def _pv(k, a, b, units, f_hz, b_t):
    return mag._st_pv_kw_m3(k, a, b, units, f_hz, b_t)

def _write_bad(name, mutate):
    data = copy.deepcopy(_table())
    mutate(data)
    path = os.path.join(SCRATCH, "anchor_bad_%s.json" % name)
    with io.open(path, "w", encoding="utf-8", newline="") as fh:
        fh.write(json.dumps(data, ensure_ascii=False, indent=2))
    return path

class TestTheTableIsATranscription(unittest.TestCase):

    def test_the_counts_are_the_transcribed_ones(self):
        data = _table()
        pts = _all_points(data)
        self.assertEqual(len(pts), 17)
        self.assertEqual(len(data["materials"]), 9)
        self.assertEqual(sorted(data["materials_without_points"]), ["3B7", "N27"])
        for nm in data["materials_without_points"]:
            self.assertEqual(data["materials"][nm]["points"], [])
        by_count = {k: len(v["points"]) for k, v in data["materials"].items()}
        self.assertEqual(by_count, {"3C90": 3, "3C92": 2, "3C93": 3, "3C94": 2,
                                    "3C96": 3, "3C98": 3, "N87": 1,
                                    "3B7": 0, "N27": 0})

    def test_every_point_carries_its_page_its_text_and_a_known_qualifier(self):
        data = _table()
        ids = {s["id"] for s in data["sources"]}
        for name, entry, p in _all_points(data):
            self.assertIn(entry["source"], ids, name)
            self.assertIn(p["qualifier"], QUALIFIERS, name)
            self.assertTrue(str(p["printed_as"]).strip(), name)
            for k in ("temp_c", "f_khz", "b_mt", "pv_kw_m3"):
                self.assertGreater(float(p[k]), 0.0, (name, k))
            self.assertIsInstance(p["temp_c"], (int, float), name)
        # the one material whose point lives on a product page rather than a
        # printed page number says so instead of inventing a page
        self.assertIsNone(data["materials"]["N87"]["pdf_page"])
        self.assertIsNone(data["materials"]["N87"]["printed_page"])

    def test_the_qualifier_split_is_the_printed_one(self):
        data = _table()
        split = {}
        for _n, _e, p in _all_points(data):
            split[p["qualifier"]] = split.get(p["qualifier"], 0) + 1
        self.assertEqual(split, {"at_most": 2, "approx": 11, "plain": 3, "max": 1})
        # 3C90's single column mixes a bound with a typical value: that, and not
        # a missing fit, is the reason it cannot be solved for a triple
        q = [p["qualifier"] for p in data["materials"]["3C90"]["points"]]
        self.assertEqual(q, ["at_most", "at_most", "approx"])

    def test_the_sources_carry_a_url_a_date_and_the_anomalies_as_printed(self):
        data = _table()
        self.assertEqual(len(data["sources"]), 3)
        for s in data["sources"]:
            self.assertTrue(s["url"].startswith("https://"), s["id"])
            self.assertTrue(s["date_printed"], s["id"])
        fx = [s for s in data["sources"] if s["id"] == "ferroxcube-handbook-2013"][0]
        self.assertTrue(fx["printed_page_equals_pdf_page"])
        self.assertIn("2009", fx["anomaly"])
        self.assertIn("2013 Jul 31", fx["date_printed"])
        br = [s for s in data["sources"]
              if s["id"] == "ferroxcube-3c96-3c98-2022"][0]
        self.assertFalse(br["printed_page_equals_pdf_page"])
        tdk = [s for s in data["sources"] if s["id"] == "tdk-product-page"][0]
        self.assertIn("not a density", tdk["anomaly"])

class TestEveryPointIsRecomputed(unittest.TestCase):
    """No number in the answer is taken from the answer."""

    def test_all_17_points_recompute_from_the_triple(self):
        data = _table()
        k, a, b = 5.0e-7, 1.6, 2.5
        out = _call(material="3C90", k=k, alpha=a, beta=b)
        self.assertEqual(out["status"], "ok")
        seen = 0
        for name, _entry, p in _all_points(data):
            got = _call(material=name, k=k, alpha=a, beta=b)
            self.assertEqual(got["status"], "ok", name)
            rows = got["value"]["anchors"]
            self.assertEqual(len(rows), len(data["materials"][name]["points"]), name)
            for row in rows:
                if (row["temp_c"], row["f_khz"], row["b_mt"]) != (
                        float(p["temp_c"]), float(p["f_khz"]), float(p["b_mt"])):
                    continue
                seen += 1
                want = _pv(k, a, b, "khz_mt", p["f_khz"] * 1e3, p["b_mt"] * 1e-3)
                self.assertAlmostEqual(row["computed_pv_kw_m3"], want, places=4)
                self.assertAlmostEqual(
                    row["deviation_pct"], (want - p["pv_kw_m3"]) / p["pv_kw_m3"] * 100.0,
                    places=4)
        self.assertEqual(seen, 17, "every printed point must appear exactly once")

    def test_the_four_unit_conventions_are_the_same_number_in_different_clothes(self):
        # 1 kW/m^3 == 1 mW/cm^3 and 1 mT == 1e-3 T, 1 kHz == 1e3 Hz: a triple
        # re-expressed in another convention must give the same Pv, so a units
        # mistake shows up as a magnitude, not as a plausible wrong answer
        p_khzm = _pv(5.0e-7, 1.6, 2.5, "khz_mt", 100e3, 0.2)
        # k' = k * (1e3)^a * (1e-3)^b when moving to SI (Hz, T, W/m^3 = 1e-3 kW/m^3)
        k_si = 5.0e-7 * 1000.0 / ((1e3 ** 1.6) * (1e-3 ** 2.5))
        p_si = _pv(k_si, 1.6, 2.5, "si", 100e3, 0.2)
        self.assertAlmostEqual(p_khzm, p_si, places=6)
        # Gauss is 10 mT, so k' = k * 10^b
        k_g = 5.0e-7 / (10.0 ** 2.5)
        self.assertAlmostEqual(p_khzm, _pv(k_g, 1.6, 2.5, "khz_gauss", 100e3, 0.2),
                               places=6)
        # tesla instead of milli-tesla: k' = k * 1000^b
        k_t = 5.0e-7 * (1e3 ** 2.5)
        self.assertAlmostEqual(p_khzm, _pv(k_t, 1.6, 2.5, "khz_t", 100e3, 0.2),
                               places=6)

    def test_a_triple_can_be_consistent_while_a_bound_is_not_close(self):
        out = _call(material="3C90", k=5.0e-7, alpha=1.6, beta=2.5)
        v = out["value"]
        first = v["anchors"][0]
        self.assertFalse(first["within_10_percent"])
        self.assertTrue(first["satisfies_the_printed_bound"])
        self.assertIn("a bound cannot exclude a smaller value", first["note"])
        self.assertEqual(v["points_in_band"], 2)
        self.assertEqual(v["bound_violations"], 0)
        self.assertEqual(v["verdict"], "consistent_with_every_printed_point")
        self.assertFalse(v["solved_here"])
        self.assertIn("arithmetic", v["why_not_solved"])

    def test_the_same_triple_can_fail_another_grade(self):
        # the negative control for the verdict: it is not "consistent" because
        # the tool is lenient, it is consistent where the numbers agree
        out = _call(material="N87", k=5.0e-7, alpha=1.6, beta=2.5)
        v = out["value"]
        self.assertEqual(v["verdict"], "violates_a_printed_upper_bound")
        self.assertEqual(v["bound_violations"], 1)
        self.assertFalse(v["anchors"][0]["satisfies_the_printed_bound"])
        self.assertIn("exceeds the printed upper bound", v["anchors"][0]["note"])

    def test_a_typical_value_is_missed_in_both_directions(self):
        # 3C93 at 140 C: one triple too high, one too low; neither is a bound,
        # so both are misses and neither is a "violation"
        hi = _call(material="3C93", k=5.0e-7, alpha=1.6, beta=2.5)
        lo = _call(material="3C93", k=1.0e-7, alpha=1.6, beta=2.5)
        for out in (hi, lo):
            v = out["value"]
            self.assertEqual(v["verdict"], "does_not_match_every_printed_point")
            self.assertEqual(v["bound_violations"], 0)
            self.assertGreater(v["typical_value_misses"], 0)
            for row in v["anchors"]:
                self.assertIsNone(row["satisfies_the_printed_bound"])

    def test_the_temperature_filter_scores_only_the_printed_temperature(self):
        out = _call(material="3C93", k=5.0e-7, alpha=1.6, beta=2.5,
                    temperature_c=140)
        self.assertEqual(out["value"]["points_evaluated"], 3)
        self.assertEqual(out["value"]["temperatures_printed"], [140.0])
        out = _call(material="3C90", k=5.0e-7, alpha=1.6, beta=2.5,
                    temperature_c=100.0)
        self.assertEqual(out["value"]["points_evaluated"], 3)

class TestTheLoaderRefusesATamperedTable(unittest.TestCase):
    """The negative control: five ways the file can be wrong and still parse."""

    def _refused(self, path):
        out = _call(material="3C90", k=5.0e-7, alpha=1.6, beta=2.5,
                    anchor_data_path=path)
        self.assertEqual(out["status"], "error")
        self.assertEqual(out["reason_code"], "DATA_UNAVAILABLE")
        return out

    def test_wrong_schema(self):
        p = _write_bad("schema", lambda d: d.__setitem__("schema", "v2"))
        self.assertIn("schema", self._refused(p)["message"])

    def test_negative_pv(self):
        def m(d):
            d["materials"]["3C90"]["points"][1]["pv_kw_m3"] = -80.0
        self.assertIn("non-positive", self._refused(_write_bad("negpv", m))["message"])

    def test_misspelled_qualifier(self):
        def m(d):
            d["materials"]["3C90"]["points"][0]["qualifier"] = "at-most"
        msg = self._refused(_write_bad("qual", m))["message"]
        self.assertIn("qualifier", msg)
        self.assertIn("never inferred", msg)

    def test_source_id_that_is_not_in_sources(self):
        def m(d):
            d["materials"]["3C96"]["source"] = "some-other-pdf"
        msg = self._refused(_write_bad("src", m))["message"]
        self.assertIn("not in sources", msg)

    def test_a_truncated_table(self):
        def m(d):
            for nm in ("3C94", "3C98", "N87", "3C92", "3C93"):
                d["materials"][nm]["points"] = []
        msg = self._refused(_write_bad("trunc", m))["message"]
        self.assertIn("only 6 points", msg)

    def test_a_material_claimed_to_have_no_point_but_carrying_them(self):
        def m(d):
            d["materials_without_points"] = ["3B7", "N27", "3C90"]
        msg = self._refused(_write_bad("claim", m))["message"]
        self.assertIn("yet carries", msg)

    def test_the_printed_as_text_is_mandatory(self):
        def m(d):
            del d["materials"]["3C92"]["points"][0]["printed_as"]
        msg = self._refused(_write_bad("noas", m))["message"]
        self.assertIn("printed_as", msg)

class TestTheRefusals(unittest.TestCase):

    def test_a_grade_with_no_pv_row_says_so_and_does_not_fit_one(self):
        for nm in ("3B7", "N27"):
            out = _call(material=nm, k=5e-7, alpha=1.6, beta=2.5)
            self.assertEqual(out["status"], "error", nm)
            self.assertEqual(out["reason_code"], "DATA_UNAVAILABLE", nm)
            self.assertEqual(out["material"], nm)
            self.assertEqual(out["points_printed"], 0)
            self.assertTrue(out["why"], nm)
            self.assertEqual(sorted(out["materials_without_points"]), ["3B7", "N27"])

    def test_an_untranscribed_material_lists_what_is_there(self):
        out = _call(material="3F36", k=5e-7, alpha=1.6, beta=2.5)
        self.assertEqual(out["reason_code"], "NOT_FOUND")
        self.assertEqual(len(out["materials_printed"]), 9)
        self.assertIn("3C90", out["materials_printed"])
        self.assertNotIn("3F36", out["materials_printed"])

    def test_an_unknown_unit_convention_is_refused(self):
        out = _call(material="3C90", k=5e-7, alpha=1.6, beta=2.5, units="cgs")
        self.assertEqual(out["reason_code"], "ARG_INVALID")
        self.assertIn("khz_mt", out["hint"])

    def test_a_temperature_with_no_printed_point_lists_the_ones_that_exist(self):
        out = _call(material="3C90", k=5e-7, alpha=1.6, beta=2.5, temperature_c=140)
        self.assertEqual(out["reason_code"], "OUT_OF_VALIDITY")
        self.assertEqual(out["temperatures_printed"], [100.0])

    def test_non_positive_coefficients_and_exponents(self):
        for args in ({"k": 0.0}, {"k": -1e-7}, {"alpha": 0.0}, {"beta": -2.0}):
            kw = dict({"material": "3C90", "k": 5e-7, "alpha": 1.6, "beta": 2.5},
                      **args)
            self.assertEqual(_call(**kw)["reason_code"], "ARG_RANGE", args)
        for args in ({"k": "x"}, {"alpha": None}, {"beta": "1.6x"}):
            kw = dict({"material": "3C90", "k": 5e-7, "alpha": 1.6, "beta": 2.5},
                      **args)
            self.assertEqual(_call(**kw)["reason_code"], "ARG_INVALID", args)

    def test_a_missing_table_is_not_answered_from_memory(self):
        out = _call(material="3C90", k=5e-7, alpha=1.6, beta=2.5,
                    anchor_data_path=os.path.join(SCRATCH, "no_such_anchors.json"))
        self.assertEqual(out["reason_code"], "DATA_UNAVAILABLE")
        self.assertIn("missing", out["message"])

class TestTheAnswerCarriesItsProvenance(unittest.TestCase):

    def test_the_page_the_url_and_the_qualifier_meanings_travel_with_it(self):
        out = _call(material="3C98", k=5e-7, alpha=1.6, beta=2.5)
        self.assertEqual(out["status"], "ok")
        self.assertEqual(out["value"]["printed_page"], "5")
        self.assertEqual(out["value"]["pdf_page"], 6)
        self.assertEqual(len(out["sources"]), 1)
        self.assertTrue(out["sources"][0]["url"].startswith("https://"))
        self.assertIn("bound", out["qualifier_meanings"]["at_most"])
        self.assertIn("material_loss_anchors.json", out["basis"])
        self.assertIn("never derived", json.dumps(out["assumptions"]))

    def test_the_brochure_grade_is_not_clamed_to_share_the_handbook_page(self):
        out = _call(material="3C98", k=5e-7, alpha=1.6, beta=2.5)
        self.assertFalse(out["printed_page_equals_pdf_page"])
        out = _call(material="3C90", k=5e-7, alpha=1.6, beta=2.5)
        self.assertTrue(out["printed_page_equals_pdf_page"])
        self.assertEqual(out["value"]["printed_page"], 84)

class TestTheOnlyExistingAnswerThatMoved(unittest.TestCase):
    """0.0.80's refusal keeps its code, its message and its numbers."""

    def test_the_hint_now_names_the_audit_and_nothing_else_changed(self):
        before = {"reason_code": "FIT_NOT_RESOLVED"}
        out = tools.TOOLS["magnetics_material_core_loss"]["handler"](
            {"material": "3C90", "freq_hz": 100000, "b_peak_t": 0.2})
        self.assertEqual(out["status"], "error")
        self.assertEqual(out["reason_code"], before["reason_code"])
        self.assertIn("carries no vendor loss fit in this table", out["message"])
        self.assertIn("3C90", out["message"])
        self.assertIn("magnetics_core_loss_audit", out["hint"])
        self.assertIn("3 loss points", out["hint"])

    def test_the_powder_path_is_untouched(self):
        # the same call that worked in 0.0.80 still returns the same numbers
        out = tools.TOOLS["magnetics_material_core_loss"]["handler"](
            {"material": "Kool Mu", "permeability": 60, "shape": "Toroids",
             "freq_hz": 100000, "b_peak_t": 0.014})
        self.assertEqual(out["status"], "ok")
        self.assertAlmostEqual(out["value"]["P_v_mw_cm3"], 11.04, places=2)

class TestTheSurfaceMovedOnce(unittest.TestCase):

    def test_the_tool_is_registered_and_counted(self):
        self.assertIn("magnetics_core_loss_audit", tools.TOOLS)
        # 179 = 178 + magnetics_core_loss_audit in 0.0.81 (scoring a triple
        # against printed points is a different question from evaluating a fit)
        self.assertEqual(len(tools.TOOLS), 179)
        cap = tools.TOOLS["calc_capabilities"]["handler"]({})
        self.assertIn("magnetics_core_loss_audit", cap["available"])
        self.assertEqual(len(cap["available"]), 173)

    def test_the_capability_list_says_what_the_points_do_not_give(self):
        cap = tools.TOOLS["calc_capabilities"]["handler"]({})
        blob = json.dumps(cap["not_implemented_yet"], ensure_ascii=False)
        self.assertIn("The printed LOSS POINTS arrived in 0.0.81", blob)
        self.assertIn("What is still NOT offered is a vendor triple", blob)
        self.assertIn("FIT_NOT_RESOLVED for every ferrite", blob)

if __name__ == "__main__":
    unittest.main()
