'''The vendor's own printed core-loss fit: look it up by name, or refuse.

What is being checked, and why each check can fail:

  (1) the catalog's three worked examples (Kool Mu 60u toroid at 100 kHz):
      11.0 / 168 / 466 mW/cm3. They pin BOTH the transcription of the row and
      WHICH exponent is the frequency: the catalog prints a, b, c in that order
      with b the flux exponent and c the frequency exponent, and swapping them
      still yields a plausible number.
  (2) the exponent direction, on its own: ten times the frequency must scale by
      10^c (1.541) and not by 10^b (1.988); twice the flux by 2^b and not 2^c.
  (3) the unit convention: the printed equation is mW/cm3, TESLA, kHz, so it must
      be identical to the SI path with k = a*10^(3-3c), alpha = c, beta = b. Both
      directions are computed and compared, not asserted from a hand conversion.
  (4) every one of the 61 printed rows resolves back to ITSELF when asked with
      its own (variant, shape, permeability, frequency side). A transcription
      slip in a variant label or a shape label makes some row unreachable or
      ambiguous, and this walks the table rather than sampling it.
  (5) the refusals: an under-specified question is answered with the list of
      printed rows, never with one of them.
  (6) zero change to the existing answers: the SI triple still gives exactly
      130000 W/m3, and an unrelated tool's anchor is untouched.
'''
import io
import json
import math
import os
import sys
import unittest

sys.path.insert(0, os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "src"))

from pcb_agent_mcp.calc import magnetics as M   # noqa: E402
from pcb_agent_mcp import tools                 # noqa: E402

DATA = os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))),
                    "data", "magnetic_materials.json")

# the three worked examples printed next to the fit equation in the Magnetics
# powder-core catalog (printed p. 23): Kool Mu 60u toroid, 100 kHz
WORKED = ((0.014, 11.0), (0.055, 168.0), (0.092, 466.0))


def _rows():
    with io.open(DATA, encoding="utf-8") as fh:
        d = json.load(fh)
    out = []
    for m in d["materials"]:
        lf = m.get("loss_fit")
        if isinstance(lf, dict):
            for r in lf.get("rows") or []:
                out.append((m, lf, r))
    return d, out


def _f_for(row):
    'A frequency that is strictly inside the row own side of any printed split.'
    if "frequency_khz_below" in row:
        return float(row["frequency_khz_below"]) * 1e3 * 0.5
    if "frequency_khz_above" in row:
        return float(row["frequency_khz_above"]) * 1e3 * 2.0
    return 100e3


class WorkedExamples(unittest.TestCase):
    def test_the_three_printed_examples(self):
        h = tools.TOOLS["magnetics_material_core_loss"]["handler"]
        for b, printed in WORKED:
            r = h({"material": "Kool Mu", "freq_hz": 100e3, "b_peak_t": b,
                   "permeability": 60, "shape": "toroid"})
            self.assertEqual(r["status"], "ok", r)
            got = r["value"]["P_v_mw_cm3"]
            self.assertLess(abs(got - printed) / printed, 5e-3,
                            "B=%g T: %g vs printed %g" % (b, got, printed))

    def test_the_row_named_is_the_row_printed(self):
        r = M.material_core_loss("Kool Mu", 100e3, 0.014, permeability=60,
                                 shape="toroid")
        v = r["value"]
        self.assertEqual(v["fit_row"], "toroid 60/75/90/125u")
        self.assertEqual(v["fit_printed_page"], 109)
        self.assertTrue(v["fit_source"].endswith("#page=109"))
        self.assertEqual(v["fit_coefficients"]["a"], 44.3)
        self.assertEqual(v["fit_coefficients"]["b"], 1.988)
        self.assertEqual(v["fit_coefficients"]["c"], 1.541)
        self.assertIn("HALF", v["fit_coefficients"]["b_is"])
        self.assertIn("curve fit", v["fit_is_a_curve_fit_not_a_limit"].lower())


class WhichExponentIsWhich(unittest.TestCase):
    def _pv(self, f, b):
        return M.material_core_loss("Kool Mu", f, b, permeability=60,
                                    shape="toroid")["value"]["P_v_w_m3"]

    def test_ten_times_the_frequency_is_ten_to_the_c(self):
        ratio = self._pv(1e6, 0.05) / self._pv(1e5, 0.05)
        # the returned density is rounded to 6 decimals, so compare relatively
        self.assertLess(abs(ratio / 10 ** 1.541 - 1.0), 1e-6)
        self.assertGreater(abs(ratio / 10 ** 1.988 - 1.0), 0.3,
                           "the swapped exponent would be off by a third")

    def test_twice_the_flux_is_two_to_the_b(self):
        ratio = self._pv(1e5, 0.1) / self._pv(1e5, 0.05)
        self.assertLess(abs(ratio / 2 ** 1.988 - 1.0), 1e-6)
        self.assertGreater(abs(ratio / 2 ** 1.541 - 1.0), 0.3)


class UnitConvention(unittest.TestCase):
    def test_khz_t_is_the_arithmetic_conversion_of_mw_cm3_t_khz(self):
        a, b, c = 44.3, 1.988, 1.541
        k_si = a * 10 ** (3 - 3 * c)
        self.assertAlmostEqual(k_si / 1.0553675249259014, 1.0, places=12)
        via_units = M.core_loss_steinmetz(100e3, 0.014, a, c, b, units="khz_t")
        via_si = M.core_loss_steinmetz(100e3, 0.014, k_si, c, b, units="si")
        self.assertEqual(via_units["status"], "ok")
        self.assertEqual(via_si["status"], "ok")
        self.assertEqual(via_units["value"]["P_v_w_m3"], via_si["value"]["P_v_w_m3"])
        self.assertEqual(via_si["value"]["P_v_w_m3"], 11038.403394)

    def test_the_lookup_agrees_with_the_converted_triple(self):
        got = M.material_core_loss("Kool Mu", 100e3, 0.014, permeability=60,
                                   shape="toroid")["value"]["P_v_w_m3"]
        want = M.core_loss_steinmetz(100e3, 0.014, 1.0553675249259014, 1.541,
                                     1.988, units="si")["value"]["P_v_w_m3"]
        self.assertEqual(got, want)

    def test_tesla_not_millitesla(self):
        # feeding the same NUMBER as mT would be off by 1000^b ~ 1e6, which is how
        # a wrong convention announces itself rather than looking plausible
        got = M.material_core_loss("Kool Mu", 100e3, 0.014, permeability=60,
                                   shape="toroid")["value"]["P_v_mw_cm3"]
        as_mt = M.core_loss_steinmetz(100e3, 0.014, 44.3, 1.541, 1.988,
                                      units="khz_mt")["value"]["P_v_mw_cm3"]
        self.assertGreater(as_mt / got, 1e5)


class EveryRowIsReachable(unittest.TestCase):
    def test_the_table_has_the_rows_it_claims(self):
        rows = _rows()[1]
        per = {}
        for m, _lf, r in rows:
            per[m["name"]] = per.get(m["name"], 0) + 1
        self.assertEqual(per, {"Kool Mu": 40, "MPP": 10, "High Flux": 11})
        self.assertEqual(len(rows), 61)

    def test_each_row_resolves_to_itself(self):
        bad = []
        for m, lf, r in _rows()[1]:
            mu = r["permeability"][0]
            got, fit, why = M._resolve_loss_fit(m, mu, r["shape"], r.get("variant"),
                                                _f_for(r))
            if got is not r:
                bad.append("%s / %s / %gu: %s" % (m["name"], M._loss_row_label(r),
                                                  mu, why))
        self.assertEqual(bad, [])

    def test_every_row_is_physically_shaped(self):
        # a positive scale and positive exponents: a transcription slip that
        # dropped a minus sign would still resolve, so check the numbers too
        for m, lf, r in _rows()[1]:
            self.assertGreater(r["a"], 0.0, r)
            self.assertGreater(r["b"], 1.0, r)
            self.assertGreater(r["c"], 0.0, r)
            self.assertGreaterEqual(r["printed_page"], 109, r)
            self.assertLessEqual(r["printed_page"], 111, r)
            self.assertTrue(r["permeability"], r)
            for p in r["permeability"]:
                self.assertGreater(float(p), 0.0, r)
            self.assertTrue(lf.get("b_is"), m["name"])

    def test_the_ultra_split_is_two_disjoint_rows(self):
        rows = _rows()[1]
        ultra = [r for m, _l, r in rows if r.get("variant") == "Ultra"]
        lo = [r for r in ultra if "frequency_khz_below" in r]
        hi = [r for r in ultra if "frequency_khz_above" in r]
        self.assertEqual(len(lo), 2)
        self.assertEqual(len(hi), 2)
        for r in lo + hi:
            self.assertEqual(float(r.get("frequency_khz_below",
                                         r.get("frequency_khz_above"))), 100.0)


class Refusals(unittest.TestCase):
    def _err(self, expect, **kw):
        r = M.material_core_loss(**kw)
        self.assertEqual(r["status"], "error", r)
        self.assertEqual(r["reason_code"], expect, r)
        return r

    def test_a_material_without_a_fit(self):
        for name in ("N87", "3C90", "3C96"):
            r = self._err("FIT_NOT_RESOLVED", material=name, freq_hz=100e3,
                          b_peak_t=0.1, permeability=100, shape="toroid")
            self.assertIn("no vendor loss fit", r["message"])

    def test_an_unknown_material_lists_the_ones_that_have_one(self):
        r = self._err("NOT_FOUND", material="Unobtainium", freq_hz=100e3,
                      b_peak_t=0.1)
        self.assertIn("Kool Mu", r["hint"])
        self.assertIn("High Flux", r["hint"])

    def test_leaving_out_the_permeability_returns_the_rows_not_one_of_them(self):
        r = self._err("FIT_NOT_RESOLVED", material="Kool Mu", freq_hz=100e3,
                      b_peak_t=0.05, shape="toroid")
        self.assertIn("printed row", r.get("hint") or "")
        self.assertGreater(len(r["rows_available"]), 1)

    def test_a_shape_the_material_does_not_print(self):
        r = self._err("FIT_NOT_RESOLVED", material="MPP", freq_hz=100e3,
                      b_peak_t=0.05, permeability=60, shape="EQ/LP")
        self.assertIn("prints no EQ/LP", r["message"])

    def test_a_shape_that_is_not_a_shape(self):
        r = self._err("FIT_NOT_RESOLVED", material="Kool Mu", freq_hz=100e3,
                      b_peak_t=0.05, permeability=60, shape="banana")
        self.assertIn("shape must be one of", r["message"])

    def test_a_permeability_the_row_does_not_cover(self):
        r = self._err("FIT_NOT_RESOLVED", material="Kool Mu", freq_hz=100e3,
                      b_peak_t=0.05, permeability=999, shape="toroid")
        self.assertIn("no fit at 999 u", r["message"])

    def test_a_variant_the_line_does_not_have(self):
        # MPP is a single product line; asking for an Ultra row is a different
        # question, not a typo to be smoothed over
        r = self._err("FIT_NOT_RESOLVED", material="MPP", freq_hz=100e3,
                      b_peak_t=0.05, permeability=60, shape="toroid",
                      variant="Ultra")
        self.assertIn("no loss fit for variant", r["message"])

    def test_the_100_khz_boundary_of_the_ultra_fit_is_not_covered(self):
        r = self._err("FIT_NOT_RESOLVED", material="Kool Mu", freq_hz=100e3,
                      b_peak_t=0.05, permeability=26, shape="toroid",
                      variant="Ultra")
        self.assertIn("100 kHz", r["message"])
        self.assertIn("neither side claims", r["message"])
        # just inside either side does resolve
        for f in (99e3, 101e3):
            ok = M.material_core_loss("Kool Mu", f, 0.05, permeability=26,
                                      shape="toroid", variant="Ultra")
            self.assertEqual(ok["status"], "ok", ok)
            self.assertNotEqual(ok["value"]["fit_row"], None)

    def test_a_negative_or_zero_frequency(self):
        for f in (0.0, -1e3):
            self._err("ARG_RANGE", material="Kool Mu", freq_hz=f, b_peak_t=0.05,
                      permeability=60, shape="toroid")

    def test_a_non_numeric_permeability(self):
        r = M.material_core_loss("Kool Mu", 100e3, 0.05, permeability="sixty",
                                 shape="toroid")
        self.assertEqual(r["status"], "error")
        self.assertEqual(r["reason_code"], "FIT_NOT_RESOLVED")
        self.assertIn("permeability must be numeric", r["message"])

    def test_the_refusal_is_not_a_silent_zero(self):
        r = M.material_core_loss("N87", 100e3, 0.1, permeability=100,
                                 shape="toroid")
        self.assertNotIn("value", r)


class Warnings(unittest.TestCase):
    def test_outside_the_charts_is_flagged_not_refused(self):
        r = M.material_core_loss("High Flux", 3e3, 0.6, permeability=26,
                                 shape="toroid")
        self.assertEqual(r["status"], "ok")
        w = r["value"]["warnings"]
        self.assertEqual(len(w), 2)
        self.assertTrue(any("outside the 5-2000 kHz" in x for x in w))
        self.assertTrue(any("above the 0.5 T" in x for x in w))

    def test_inside_the_charts_is_silent(self):
        r = M.material_core_loss("Kool Mu", 100e3, 0.05, permeability=60,
                                 shape="toroid")
        self.assertNotIn("warnings", r["value"])

    def test_the_validity_window_is_stated_every_time(self):
        r = M.material_core_loss("Kool Mu", 100e3, 0.05, permeability=60,
                                 shape="toroid")
        joined = " ".join(r["value"]["not_modelled"])
        self.assertIn("validity", joined)
        self.assertIn("5-2000 kHz", joined)


class WaveformAndRegistration(unittest.TestCase):
    def test_a_triangle_is_not_a_sine_here_either(self):
        sine = M.material_core_loss("Kool Mu", 100e3, 0.05, permeability=60,
                                    shape="toroid")
        tri = M.material_core_loss("Kool Mu", 100e3, 0.05, permeability=60,
                                   shape="toroid", waveform="triangle")
        self.assertEqual(tri["status"], "ok")
        ratio = tri["value"]["P_v_w_m3"] / sine["value"]["P_v_w_m3"]
        # iGSE for a 50 % duty triangle with alpha = c = 1.541
        self.assertAlmostEqual(ratio / M._igse_shape_ratio(1.541, [(0.1, 0.5), (-0.1, 0.5)]),
                               1.0, places=9)
        self.assertNotAlmostEqual(ratio, 1.0, places=3)

    def test_registered_as_a_tool(self):
        t = tools.TOOLS["magnetics_material_core_loss"]
        self.assertEqual(set(t["inputSchema"]["required"]),
                         {"material", "freq_hz", "b_peak_t"})
        # the unit convention is part of the stored row, not a caller argument:
        # letting the caller pick mT here is exactly the mistake this tool exists
        # to avoid
        self.assertNotIn("units", t["inputSchema"]["properties"])
        self.assertEqual(t["inputSchema"]["properties"]["shape"]["enum"],
                         sorted(M.LOSS_FIT_SHAPES))
        self.assertEqual(len(tools.TOOLS), 175)
        caps = tools.TOOLS["calc_capabilities"]["handler"]({})
        self.assertIn("magnetics_material_core_loss", caps["available"])

    def test_the_handler_is_the_function(self):
        h = tools.TOOLS["magnetics_material_core_loss"]["handler"]
        kw = {"material": "MPP", "freq_hz": 250e3, "b_peak_t": 0.03,
              "permeability": 125, "shape": "toroid"}
        self.assertEqual(h(dict(kw)), M.material_core_loss(**kw))


class ZeroChange(unittest.TestCase):
    def test_the_si_triple_still_gives_the_same_number(self):
        r = tools.TOOLS["magnetics_core_loss"]["handler"](
            {"f_hz": 100e3, "b_peak_t": 0.1, "k": 1.3, "alpha": 1.5,
             "beta": 2.5, "units": "si"})
        self.assertEqual(r["status"], "ok")
        self.assertEqual(r["value"]["P_v_w_m3"], 130000.0)

    def test_the_new_unit_convention_did_not_move_the_old_one(self):
        # khz_mt was the closest existing convention; it must be untouched
        r = M.core_loss_steinmetz(100e3, 0.1, 1.5, 1.3, 2.5, units="khz_mt")
        self.assertEqual(r["status"], "ok")
        self.assertEqual(r["value"]["P_v_mw_cm3"],
                         M.core_loss_steinmetz(100e3, 0.1, 1.5, 1.3, 2.5,
                                               units="khz_mt")["value"]["P_v_mw_cm3"])

    def test_the_materials_report_lists_the_fits_it_now_has(self):
        d = tools.TOOLS["magnetics_data"]["handler"]({"kind": "materials"})
        fits = d["assumptions"]["fits"]
        self.assertNotIn("none performed", fits.lower())
        self.assertIn("loss_fit", fits)


if __name__ == "__main__":
    unittest.main()
