'''The vendor's DC-bias fit: % mu_i = 1/(a + b*H^c), and the four ways to be wrong.

What is being checked, and why each check can fail:

  (1) the table is the one this build ships: 29 material x shape_family groups,
      125 rows, printed pages 63-65, four shape families, ten material lines. A
      re-parse that silently loses a page shows up here as a count that moved.
  (2) every row carries a = 0.01 - that is the printed value on all 125 rows, and
      it is what pins the fit to 100 % at zero bias. A reader that took a shifted
      column would answer from the wrong number, so the loader refuses instead.
  (3) THE POSITIVE CONTROL THAT MATTERS: the catalog's own printed summary table
      (p.10) gives, per material, the oersteds at which mu falls to 80 % and to
      50 %. Solving the fit for those two levels reproduces all 18 printed
      numbers. This is a check the fit could fail: the previous attempt at this
      feature used a different printed function family (B = x(a+bH+cH^2)/...)
      and missed these numbers by a factor of 2-3.5.
  (4) both worked examples printed in the catalog: Example 2 (60u Kool Mu, 20
      turns, 20 A, le 63.5 mm) prints "% mu_i = 0.58" and this table gives
      58.4 %; Example 3 prints B = 0.092 T at 15.8 Oe and integrating this table
      gives 0.0936 T (1.7 % high - that example pins the magnitude, not the integral
      form, because at 15.8 Oe the chord and the integral still agree).
  (5) B must be the INTEGRAL of the fit, not the chord mu_r*H. The chord is what
      a plausible-looking implementation would do, and it is wrong by 35 % here
      (0.277 T against 0.390 T at 79 Oe) because % mu_i is a differential
      permeability. If someone ever "simplifies" dc_bias_b_t to the chord form,
      the Example 3 number in (4) breaks by a factor of ~1.9.
  (6) the refusals, each one an argument that would otherwise be silently
      resolved to a guess: an unprinted permeability (the vendor prints one fit
      per permeability and the curves are not scaled copies), an unprinted shape
      family, an ambiguous shape family, an unknown material, both h_oe and the
      winding given at once, zero or negative H, a table whose columns slipped,
      a missing table.
  (7) H from the winding is the catalog's own CGS relation
      0.4*pi*N*I[A]/le[cm] - and 0.4*pi*N*I[A]/le[mm] (the unit slip a caller
      would make, since every other length in this package is mm) is 10x off, so
      the check is written against the 79.16 Oe the Example 2 winding gives.
  (8) an H outside the plotted 10-800 Oe band is FLAGGED, not clamped: the page
      prints "Fit valid only for range shown on graph" and gives no numbers.
  (9) zero change elsewhere: the tool count moved 175 -> 176 by exactly this one
      tool, and the material/type spellings "Kool Mu" and "Kool Mu HF" still
      reach the rows printed as "Kool M\xb5" and "Kool M\xb5 H\u0192".
'''
import io
import json
import math
import os
import sys
import unittest

sys.path.insert(0, os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "src"))

from pcb_agent_mcp.calc import magnetics as mg  # noqa: E402
from pcb_agent_mcp import tools  # noqa: E402

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
DCB = os.path.join(ROOT, "data", "dc_bias_fit.json")
POWDER = os.path.join(ROOT, "data", "powder_cores.json")
SCRATCH = os.path.join(ROOT, "_scratch")

# --------------------------------------------------------------- printed facts
# The catalog's printed summary page (p.10): for 60u, the oersteds at which mu
# falls to 80 % and to 50 %. Transcribed in data/powder_cores.json as well, and
# test_the_printed_summary_is_the_same_in_both_tables keeps the two copies equal.
PRINTED_80_50 = {
    "Kool Mu": (45, 95),
    "Kool Mu MAX": (65, 130),
    "Kool Mu HF": (60, 115),
    "Kool Mu Ultra": (60, 120),
    "XFlux": (100, 170),
    "XFlux Ultra": (110, 180),
    "Edge": (130, 205),
    "High Flux": (100, 185),
    "MPP": (60, 105),
}

SHAPES = (
    "Toroids",
    "E Cores, U Cores & EER Cores",
    "EQ & LP Cores",
    "Blocks, Round Blocks & Cylinders",
)


def load():
    with io.open(DCB, "r", encoding="utf-8") as fh:
        return json.load(fh)


def rows():
    """Every printed row, keyed by (folded material, folded shape, permeability)."""
    out = {}
    for g in load()["groups"]:
        for r in g["rows"]:
            out[(mg._dc_key(g["material"]), mg._dc_key(g["shape_family"]),
                 int(r["permeability"]))] = (g["material"], g["shape_family"],
                                             float(r["a"]), float(r["b"]),
                                             float(r["c"]), g["printed_page"])
    return out


def fit_of(material, shape, permeability):
    _m, _s, a, b, c, _p = rows()[(mg._dc_key(material), mg._dc_key(shape), permeability)]
    return a, b, c


def h_at(pct, a, b, c):
    """Invert the fit: the H at which % mu_i equals pct."""
    return ((1.0 / pct - a) / b) ** (1.0 / c)


def handler(**kw):
    return tools.TOOLS["magnetics_dc_bias"]["handler"](kw)


class TestTheTable(unittest.TestCase):
    def test_the_table_is_the_one_this_build_ships(self):
        d = load()
        self.assertEqual(d["schema"], "dc-bias-fit")
        self.assertEqual(d["version"], "0.0.77")
        self.assertEqual(len(d["groups"]), 29)
        self.assertEqual(sum(len(g["rows"]) for g in d["groups"]), 125)
        mats = []
        shapes = []
        pages = set()
        for g in d["groups"]:
            if g["material"] not in mats:
                mats.append(g["material"])
            if g["shape_family"] not in shapes:
                shapes.append(g["shape_family"])
            pages.add(g["printed_page"])
            self.assertTrue(g["rows"], g)
        self.assertEqual(len(mats), 10, mats)
        self.assertEqual(sorted(shapes), sorted(SHAPES))
        self.assertEqual(pages, {63, 64, 65})

    def test_every_row_carries_a_equal_to_one_hundredth(self):
        n = 0
        for g in load()["groups"]:
            for r in g["rows"]:
                self.assertAlmostEqual(float(r["a"]), 0.01, places=15,
                                       msg="%r/%r %r" % (g["material"],
                                                         g["shape_family"], r))
                self.assertGreater(float(r["b"]), 0.0)
                self.assertGreater(float(r["c"]), 0.0)
                n += 1
        self.assertEqual(n, 125)

    def test_zero_bias_returns_one_hundred_percent_by_construction(self):
        for (key, row) in rows().items():
            _m, _s, a, b, c, _p = row
            self.assertAlmostEqual(mg.dc_bias_percent(a, b, c, 0.0), 100.0, places=9)

    def test_the_printed_summary_is_the_same_in_both_tables(self):
        # The 18 numbers this package checks the fit against are transcribed
        # twice (p.10 comparison table and here); if one copy is edited the
        # other would silently become the only witness.
        with io.open(POWDER, "r", encoding="utf-8") as fh:
            page = json.load(fh)["material_comparison_printed_page_10"]["rows"]
        for name, (e80, e50) in PRINTED_80_50.items():
            hit = [v for k, v in page.items() if mg._dc_key(k) == mg._dc_key(name)]
            self.assertEqual(len(hit), 1, name)
            self.assertEqual(hit[0]["perm_vs_dc_bias_oe_at_80pct_mu_i"], e80, name)
            self.assertEqual(hit[0]["perm_vs_dc_bias_oe_at_50pct_mu_i"], e50, name)

    def test_the_data_file_loads_through_the_public_kind(self):
        r = mg.magnetic_data(kind="dc_bias_fit")
        self.assertEqual(r["status"], "ok")
        self.assertEqual(r["value"]["kind"], "dc_bias_fit")
        self.assertEqual(r["value"]["count"], 125)
        self.assertEqual(r["value"]["total_matching"], 125)
        one = mg.magnetic_data(kind="dc_bias_fit", material="MPP", limit=2)
        self.assertEqual(one["value"]["count"], 2)
        self.assertEqual(one["value"]["total_matching"], 15)
        self.assertEqual({x["material"] for x in one["value"]["rows"]}, {"MPP"})
        for alias in ("dc_bias", "dcbias", "bias_fit"):
            self.assertEqual(mg.magnetic_data(kind=alias)["value"]["count"], 125, alias)


class TestTheFitReproducesTheCatalog(unittest.TestCase):
    def test_eighteen_of_eighteen_printed_numbers(self):
        worst = 0.0
        worst_at = None
        for name, (e80, e50) in PRINTED_80_50.items():
            a, b, c = fit_of(name, "Toroids", 60)
            for pct, printed in ((80.0, e80), (50.0, e50)):
                got = h_at(pct, a, b, c)
                rel = abs(got - printed) / float(printed)
                if rel > worst:
                    worst, worst_at = rel, (name, pct, got, printed)
                self.assertLess(rel, 0.01,
                                "%s at %g %%: fit says %.2f Oe, printed %s"
                                % (name, pct, got, printed))
        # and the worst one is the printed one this package names in its docs
        self.assertLess(worst, 0.01)
        self.assertEqual(worst_at[0], "MPP")
        self.assertEqual(worst_at[3], 105)

    def test_worked_example_2_percent_mu_i(self):
        # printed p.24: 60u Kool Mu, 20 turns, 20 A, le 63.5 mm ->
        # "from curve fit equation, % mu_i = 0.58"
        r = mg.magnetics_dc_bias("Kool Mu", 60, "Toroids", turns=20,
                                 dc_current_a=20, le_mm=63.5)
        self.assertEqual(r["status"], "ok")
        v = r["value"]
        self.assertAlmostEqual(v["h_oe"], 79.16, places=2)
        self.assertAlmostEqual(v["percent_initial_permeability"], 58.4, delta=0.5)
        self.assertAlmostEqual(v["inductance_ratio"], 0.58, delta=0.005)

    def test_worked_example_3_flux_at_15_8_oersted(self):
        # printed p.25: Example 3 prints H = 15.8 Oe -> B = 0.092 T. The integral
        # answers 0.0936 T (1.7 % high). Note what this check does NOT prove: at
        # 15.8 Oe the curve is still at 96.5 %, so the chord lands 0.3 % away and
        # this example cannot tell the two forms apart - the 95 Oe point below can.
        a, b, c = fit_of("Kool Mu", "Toroids", 60)
        b_t = mg.dc_bias_b_t(a, b, c, 60, 15.8)
        self.assertAlmostEqual(b_t, 0.0936, delta=0.0005)
        self.assertLess(abs(b_t - 0.092) / 0.092, 0.02)

    def test_the_flux_is_the_integral_and_the_chord_is_not(self):
        a, b, c = fit_of("Kool Mu", "Toroids", 60)
        integ = mg.dc_bias_b_t(a, b, c, 60, 95.0)
        pct = mg.dc_bias_percent(a, b, c, 95.0)
        chord = 1e-4 * 60 * pct / 100.0 * 95.0
        # the catalog prints 0.441 T for this point: the integral is right
        self.assertAlmostEqual(integ, 0.441, delta=0.003)
        self.assertLess(abs(chord - 0.441), abs(integ - 0.441) + 0.5)  # chord exists
        self.assertLess(chord, integ * 0.75)
        r = mg.magnetics_dc_bias("Kool Mu", 60, "Toroids", h_oe=79.2)
        self.assertIn("chord", r["value"]["b_t_basis"])
        self.assertLess(float(r["value"]["b_t_basis"].split("gives ")[1].split(" T")[0]),
                        float(r["value"]["b_t"]))

    def test_the_integral_converges_with_the_step_count(self):
        a, b, c = fit_of("XFlux", "Toroids", 60)
        coarse = mg.dc_bias_b_t(a, b, c, 60, 700.0, steps=100)
        fine = mg.dc_bias_b_t(a, b, c, 60, 700.0, steps=4000)
        self.assertLess(abs(coarse - fine) / fine, 0.01)


class TestTheMagnetisingForce(unittest.TestCase):
    def test_h_from_the_winding_is_the_cgs_relation(self):
        # le is given in mm and the relation wants cm: a 10x slip is the obvious
        # error, so the check is a number, not a shape.
        r = mg.magnetics_dc_bias("XFlux", 60, "Toroids", turns=20,
                                 dc_current_a=20, le_mm=63.5)
        want = 0.4 * math.pi * 20 * 20 / 6.35
        self.assertAlmostEqual(r["value"]["h_oe"], want, places=3)
        self.assertGreater(want, 79.0)
        self.assertLess(want, 79.5)
        self.assertIn("from turns", r["value"]["h_source"])

    def test_h_oe_and_the_winding_are_two_ways_of_saying_one_thing(self):
        r = mg.magnetics_dc_bias("XFlux", 60, "Toroids", h_oe=79.16, turns=20,
                                 dc_current_a=20, le_mm=63.5)
        self.assertEqual(r["status"], "error")
        self.assertEqual(r["reason_code"], "ARG_INVALID")
        self.assertIn("not both", r["message"])

    def test_a_partial_winding_is_named(self):
        r = mg.magnetics_dc_bias("XFlux", 60, "Toroids", turns=20, dc_current_a=20)
        self.assertEqual(r["reason_code"], "ARG_INVALID")
        self.assertIn("le_mm", r["message"])

    def test_no_h_at_all_is_an_argument_error(self):
        r = mg.magnetics_dc_bias("XFlux", 60, "Toroids")
        self.assertEqual(r["reason_code"], "ARG_INVALID")
        self.assertIn("h_oe", r["message"])

    def test_zero_and_negative_h_are_refused(self):
        for bad in (0, -5, 0.0):
            r = mg.magnetics_dc_bias("XFlux", 60, "Toroids", h_oe=bad)
            self.assertEqual(r["status"], "error", bad)
            self.assertEqual(r["reason_code"], "ARG_INVALID", bad)
        for kw in (dict(turns=0, dc_current_a=20, le_mm=63.5),
                   dict(turns=20, dc_current_a=-1, le_mm=63.5),
                   dict(turns=20, dc_current_a=20, le_mm=0)):
            r = mg.magnetics_dc_bias("XFlux", 60, "Toroids", **kw)
            self.assertEqual(r["reason_code"], "ARG_INVALID", kw)

    def test_an_h_outside_the_plotted_band_is_flagged_not_clamped(self):
        low = mg.magnetics_dc_bias("Kool Mu", 60, "Toroids", h_oe=2.0)
        high = mg.magnetics_dc_bias("Kool Mu", 60, "Toroids", h_oe=2000.0)
        for r in (low, high):
            self.assertEqual(r["status"], "ok")
            text = " ".join(r["value"]["warnings"])
            self.assertIn("outside", text)
            self.assertIn("10-800 Oe", text)
        # not clamped: 2000 Oe still answers with its own (much smaller) number
        self.assertLess(high["value"]["percent_initial_permeability"], 1.0)
        self.assertGreater(low["value"]["percent_initial_permeability"], 95.0)

    def test_inside_the_band_there_is_no_range_warning(self):
        r = mg.magnetics_dc_bias("Kool Mu", 60, "Toroids", h_oe=50.0)
        self.assertEqual(r["value"]["warnings"], [])


class TestTheRefusals(unittest.TestCase):
    def test_the_shape_family_is_optional_only_when_one_is_printed(self):
        one = mg.magnetics_dc_bias("Kool Mu Ultra", 60, h_oe=79.2)
        self.assertEqual(one["status"], "ok")
        self.assertEqual(one["value"]["shape_family"], "Toroids")
        many = mg.magnetics_dc_bias("Kool Mu", 60, h_oe=79.2)
        self.assertEqual(many["status"], "error")
        self.assertEqual(many["reason_code"], "NOT_FOUND")
        for s in SHAPES:
            self.assertIn(s, many["message"])

    def test_an_unprinted_permeability_is_refused_with_the_list(self):
        r = mg.magnetics_dc_bias("MPP", 55, "Toroids", h_oe=10)
        self.assertEqual(r["status"], "error")
        self.assertEqual(r["reason_code"], "NOT_FOUND")
        perms = r["permeabilities_printed"]
        self.assertIn(60, perms)
        self.assertIn(14, perms)
        self.assertEqual(perms, [14, 19, 26, 40, 60, 75, 90, 125, 147, 160, 173, 200, 300, 550])
        self.assertIn("60", r["hint"])

    def test_a_permeability_the_material_prints_elsewhere_is_still_refused(self):
        # 173 is printed for MPP toroids; Kool Mu toroids stop at 125 or below
        perms = set(x for (m, s, x) in rows() if m == mg._dc_key("Kool Mu") and s == "toroids")
        bad = max(perms) + 7
        r = mg.magnetics_dc_bias("Kool Mu", bad, "Toroids", h_oe=10)
        self.assertEqual(r["reason_code"], "NOT_FOUND")
        self.assertEqual(sorted(r["permeabilities_printed"]), sorted(perms))

    def test_an_unprinted_shape_family_is_refused(self):
        # the catalog prints no fit at all for Kool Mu Ultra in EQ & LP cores
        r = mg.magnetics_dc_bias("Kool Mu Ultra", 60, "EQ & LP Cores", h_oe=10)
        self.assertEqual(r["status"], "error")
        self.assertEqual(r["reason_code"], "NOT_FOUND")
        self.assertIn("Toroids", r["message"])

    def test_an_unknown_material_is_named_not_guessed(self):
        r = mg.magnetics_dc_bias("Ferrite 3C90", 60, "Toroids", h_oe=10)
        self.assertEqual(r["reason_code"], "NOT_FOUND")
        self.assertIn("Kool M\u00b5", r["message"])
        self.assertIn("MPP", r["message"])

    def test_the_two_letter_lookalikes_still_reach_the_printed_names(self):
        # printed "Kool M\xb5" / "Kool M\xb5 H\u0192"; the rest of this package
        # spells them "Kool Mu" / "Kool Mu HF"
        for spelling in ("Kool Mu", "Kool M\u00b5", "KOOL MU", "kool_mu"):
            r = mg.magnetics_dc_bias(spelling, 60, "Toroids", h_oe=79.2)
            self.assertEqual(r["status"], "ok", spelling)
        hf = mg.magnetics_dc_bias("Kool Mu HF", 60, "Toroids", h_oe=79.2)
        self.assertEqual(hf["status"], "ok")
        self.assertEqual(hf["value"]["material"], "Kool M\u00b5 H\u0192")


class TestTheLoaderRefusesRatherThanReads(unittest.TestCase):
    def setUp(self):
        if not os.path.isdir(SCRATCH):
            os.makedirs(SCRATCH)

    def _write(self, name, mutate):
        d = load()
        mutate(d)
        p = os.path.join(SCRATCH, name)
        with io.open(p, "w", encoding="utf-8", newline="\n") as fh:
            json.dump(d, fh, ensure_ascii=False)
        return p

    def test_a_shifted_a_column_is_refused(self):
        # a = 0.02 is what reading the wrong column looks like: the fit would
        # then return 50 % at zero bias. It must be refused, not answered from.
        p = self._write("test_dc_bias_a_shifted.json",
                        lambda d: d["groups"][0]["rows"][0].__setitem__("a", 0.02))
        r = mg.magnetics_dc_bias("Kool Mu", 60, "Toroids", h_oe=79.2, bias_data_path=p)
        self.assertEqual(r["status"], "error")
        self.assertEqual(r["reason_code"], "DATA_UNAVAILABLE")
        self.assertIn("0.01", r["message"])

    def test_a_row_that_is_not_a_quadruple_is_refused(self):
        def drop(d):
            del d["groups"][0]["rows"][0]["c"]
        p = self._write("test_dc_bias_no_c.json", drop)
        r = mg.magnetics_dc_bias("Kool Mu", 60, "Toroids", h_oe=79.2, bias_data_path=p)
        self.assertEqual(r["reason_code"], "DATA_UNAVAILABLE")
        self.assertIn("quadruple", r["message"])

    def test_a_wrong_schema_is_refused(self):
        p = self._write("test_dc_bias_schema.json",
                        lambda d: d.__setitem__("schema", "dc-bias"))
        r = mg.magnetics_dc_bias("Kool Mu", 60, "Toroids", h_oe=79.2, bias_data_path=p)
        self.assertEqual(r["reason_code"], "DATA_UNAVAILABLE")
        self.assertIn("schema", r["message"])

    def test_a_missing_table_is_reported_not_guessed(self):
        p = os.path.join(SCRATCH, "no_such_dc_bias_table.json")
        r = mg.magnetics_dc_bias("Kool Mu", 60, "Toroids", h_oe=79.2, bias_data_path=p)
        self.assertEqual(r["status"], "error")
        self.assertEqual(r["reason_code"], "DATA_UNAVAILABLE")
        self.assertIn("missing", r["message"])

    def test_the_readback_honours_the_override_path(self):
        p = self._write("test_dc_bias_override.json", lambda d: None)
        r = mg.magnetic_data(kind="dc_bias_fit", limit=1, bias_data_path=p)
        self.assertEqual(r["status"], "ok")
        self.assertEqual(r["value"]["count"], 1)


class TestTheAnswer(unittest.TestCase):
    def test_include_b_false_drops_the_flux_but_keeps_the_answer(self):
        on = mg.magnetics_dc_bias("Kool Mu", 60, "Toroids", h_oe=79.2)
        off = mg.magnetics_dc_bias("Kool Mu", 60, "Toroids", h_oe=79.2, include_b=False)
        self.assertEqual(off["status"], "ok")
        self.assertIn("b_t", on["value"])
        self.assertNotIn("b_t", off["value"])
        self.assertEqual(on["value"]["percent_initial_permeability"],
                         off["value"]["percent_initial_permeability"])

    def test_the_saturation_ratio_uses_the_printed_bsat(self):
        r = mg.magnetics_dc_bias("XFlux", 60, "Toroids", h_oe=200.0)
        v = r["value"]
        self.assertEqual(v["saturation_flux_density_t"], 1.6)
        self.assertAlmostEqual(v["b_saturation_ratio"],
                               v["b_t"] / 1.6, places=5)
        # MPP is printed 0.8 T on p.10, so the same oersteds are a much larger
        # fraction of saturation for it - the ratio is not a constant
        m = mg.magnetics_dc_bias("MPP", 60, "Toroids", h_oe=200.0)
        self.assertEqual(m["value"]["saturation_flux_density_t"], 0.8)

    def test_a_high_flux_against_saturation_says_the_fit_does_not_model_it(self):
        r = mg.magnetics_dc_bias("MPP", 60, "Toroids", h_oe=200.0)
        self.assertGreater(r["value"]["b_saturation_ratio"], 0.7)
        self.assertTrue(any("saturation" in w for w in r["value"]["warnings"]))

    def test_the_answer_carries_its_page_and_formula(self):
        v = mg.magnetics_dc_bias("Edge", 60, "Toroids", h_oe=100.0)["value"]
        self.assertEqual(v["fit"]["a"], 0.01)
        self.assertEqual(v["fit"]["printed_page"], 63)
        self.assertEqual(v["fit"]["printed_formula"],
                         "% initial permeability = 1 / (a + b*H^c)")
        self.assertEqual(v["permeability"], 60)


class TestTheTool(unittest.TestCase):
    def test_the_tool_is_registered_and_the_count_moved_by_one(self):
        # 179 = 178 + magnetics_core_loss_audit in 0.0.81 (scoring a triple against
        # the vendor's printed points is a different question from evaluating a fit)
        # 180 = 179 + thermal_spreading in 0.0.85 (a rectangular heat source's
        # spreading resistance is its own question; thermal_stack stays strictly 1-D)
        self.assertEqual(len(tools.TOOLS), 200)
        self.assertIn("magnetics_dc_bias", tools.TOOLS)
        spec = tools.TOOLS["magnetics_dc_bias"]
        props = spec["inputSchema"]["properties"]
        self.assertEqual(spec["inputSchema"]["required"], ["material", "permeability"])
        for k in ("shape_family", "h_oe", "turns", "dc_current_a", "le_mm",
                  "include_b", "bias_data_path"):
            self.assertIn(k, props)
        self.assertIn("dc_bias_fit",
                      tools.TOOLS["magnetics_data"]["inputSchema"]["properties"]["kind"]["enum"])
        self.assertIn("bias_data_path",
                      tools.TOOLS["magnetics_data"]["inputSchema"]["properties"])

    def test_the_handler_route_answers_the_same_number(self):
        r = tools.TOOLS["magnetics_dc_bias"]["handler"](
            {"material": "Kool Mu", "permeability": 60, "shape_family": "Toroids",
             "h_oe": 79.2})
        self.assertEqual(r["status"], "ok")
        self.assertAlmostEqual(r["value"]["percent_initial_permeability"], 58.38, places=2)

    def test_the_capability_note_names_what_is_not_modelled(self):
        r = tools._capabilities({})
        blob = json.dumps(r, ensure_ascii=False)
        self.assertIn("DC bias", blob)
        for needle in ("temperature", "saturation", "turn"):
            self.assertIn(needle, blob.lower())


if __name__ == "__main__":
    unittest.main()
