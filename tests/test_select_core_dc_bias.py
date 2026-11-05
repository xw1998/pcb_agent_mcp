"""0.0.78: the DC-bias loop in select_core.

What is being pinned here, in order:

  * the no-bias answer did not move (same parts, same turns, no new keys);
  * the biased design really honours both constraints at the bias it was solved
    for (L at the bias >= the L asked for, peak flux <= the limit), and the turns
    are >= the unbiased ones - the bias can only make the winding longer;
  * L(N) is not monotone and the solver does not pretend it is: with a row whose
    c > 2 the inductance peaks and then falls, and a part that cannot reach L is
    refused with the maximum it does reach (dc_bias_unreachable);
  * the fit is really read: perturbing one printed row moves the answer, and a
    table that is not the shipped one is refused rather than used;
  * every refusal arm is a refusal (bias without powder cores, a bias above the
    peak current, a zero bias, a missing table);
  * the printed-band flag exists and fires, so an extrapolated mu is never
    presented as a reading;
  * the tool schema carries the arguments and the handler passes them through.
"""
import glob
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
DATA = os.path.join(ROOT, "data", "dc_bias_fit.json")

L_REQ = 1e-4      # 100 uH
IPK = 10.0        # A
MU = 60.0


def _load(path):
    with io.open(path, encoding="utf-8") as fh:
        return json.load(fh)


def _scratch_path(name):
    if not os.path.isdir(SCRATCH):
        os.makedirs(SCRATCH)
    return os.path.join(SCRATCH, name)


def _biased(**kw):
    args = dict(inductance_h=L_REQ, i_peak_a=IPK, include_powder_cores=True,
                permeability=MU)
    args.update(kw)
    return mag.select_core(**args)


class TestTheUnbiasedAnswerDidNotMove(unittest.TestCase):
    """Default off: no bias asked for, byte for byte the 0.0.77 answer."""

    def test_pool_and_first_parts_are_pinned(self):
        whole = mag.select_core(L_REQ, IPK, include_powder_cores=True)
        self.assertEqual(whole["status"], "ok")
        self.assertEqual(whole["value"]["table_rows"], 1939)
        self.assertEqual(whole["value"]["feasible_count"], 1932)
        self.assertEqual([c["part"] for c in whole["value"]["candidates"][:5]],
                         ["0077141A7", "0077445A7", "0077444A7", "0077140A7",
                          "C058141A2"])
        one = _biased()["value"]
        self.assertEqual(one["table_rows"], 288)
        self.assertEqual(one["feasible_count"], 288)
        self.assertEqual([c["part"] for c in one["candidates"][:5]],
                         ["0077141A7", "C058141A2", "0055141A2", "0077151A7",
                          "C058151A2"])

    def test_no_bias_keys_appear(self):
        v = _biased()["value"]
        self.assertNotIn("dc_bias", v)
        for row in v["candidates"]:
            self.assertNotIn("dc_bias", row)
            self.assertNotIn("turns_basis", row)
            self.assertNotIn("al_at_bias_nh", row)

    def test_a_zero_bias_is_refused_not_ignored(self):
        out = _biased(dc_bias_current_a=0.0)
        self.assertEqual(out["status"], "error")
        self.assertEqual(out["reason_code"], "ARG_RANGE")


class TestTheBiasedDesignHonoursItsOwnConstraints(unittest.TestCase):
    """The answer has to be a design, not just a bigger number."""

    def setUp(self):
        self.v = _biased(dc_bias_current_a=8.0)["value"]
        self.row = self.v["candidates"][0]

    def test_every_listed_part_reaches_l_at_the_bias(self):
        for row in self.v["candidates"]:
            d = row["dc_bias"]
            self.assertGreaterEqual(d["inductance_h"], L_REQ * (1 - 1e-9),
                                    row["part"])
            self.assertLessEqual(d["b_peak_t"], d["b_limit_t"], row["part"])

    def test_more_turns_than_the_unbiased_answer(self):
        d = self.row["dc_bias"]
        self.assertGreater(d["turns_without_bias"], 0)
        self.assertGreaterEqual(d["turns"], d["turns_without_bias"])
        self.assertEqual(self.row["turns"], d["turns"])

    def test_the_unbiased_inductance_of_those_turns_would_be_more(self):
        # that is the whole point: the printed AL over-promises at current
        d = self.row["dc_bias"]
        self.assertGreater(d["inductance_without_bias_h"], L_REQ)

    def test_h_is_the_catalogs_own_relation(self):
        row = self.row
        d = row["dc_bias"]
        h_from_relation = (0.4 * math.pi * d["turns"] * 8.0
                           / (float(row["le_mm"]) / 10.0))
        self.assertAlmostEqual(d["h_oe"], h_from_relation, places=4)

    def test_the_percent_comes_from_that_rows_own_coefficients(self):
        d = self.row["dc_bias"]
        fit = d["fit"]
        self.assertEqual(fit["a"], 0.01)
        expect = mag.dc_bias_percent(fit["a"], fit["b"], fit["c"], d["h_oe"])
        self.assertAlmostEqual(d["percent_initial_permeability"], expect, places=6)
        self.assertIsNotNone(fit["printed_page"])

    def test_the_flux_is_judged_on_the_biased_inductance(self):
        d = self.row["dc_bias"]
        expect = (d["inductance_h"] * IPK
                  / (d["turns"] * float(self.row["ae_mm2"]) * 1e-6))
        self.assertAlmostEqual(d["b_peak_t"], expect, places=6)

    def test_the_al_margin_is_judged_against_the_biased_al(self):
        # 0.0.77's al_ratio compared against the printed AL; at a bias that would
        # report a shortfall that does not exist, so the reference moved
        self.assertAlmostEqual(self.row["al_ratio"], 1.0, delta=0.02)
        self.assertIsNone(self.row["gap_required"])

    def test_the_telling_sentence_names_both_counts(self):
        text = self.row["turns_basis"]
        self.assertIn("AT the DC bias", text)
        self.assertIn(str(self.row["dc_bias"]["turns_without_bias"]), text)


class TestTheLoopIsNotMonotone(unittest.TestCase):
    """c > 2 makes more turns buy less inductance - the solver must know."""

    def _row_c(self, c):
        rows = []
        for g in _load(DATA)["groups"]:
            for r in g["rows"]:
                if r["c"] > c:
                    rows.append((g["material"], r))
        return rows

    def test_the_table_really_has_rows_past_two(self):
        rows = self._row_c(2.0)
        total = sum(len(g["rows"]) for g in _load(DATA)["groups"])
        self.assertEqual(total, 125)
        self.assertEqual(len(rows), 86)

    def test_l_of_n_falls_past_the_peak(self):
        # an Edge / XFlux toroid row with c > 2 - the case where more turns stop
        # buying inductance. The peak moves with the current, so this is checked
        # on the row that is actually shipped
        data = _load(DATA)
        group = [g for g in data["groups"]
                 if g["material"] == "XFlux"
                 and g["shape_family"] == "Toroids"][0]
        row = [r for r in group["rows"] if r["permeability"] == 60.0][0]
        self.assertGreater(row["c"], 2.0)
        al_h = 35e-9
        k = 0.4 * math.pi * 8.0 / (41.2 / 10.0)

        def l_of(n):
            return al_h * n * n * mag.dc_bias_percent(
                row["a"], row["b"], row["c"], k * n) / 100.0

        peak = int(round((2 * row["a"]
                          / ((row["c"] - 2) * row["b"] * k ** row["c"]))
                         ** (1.0 / row["c"])))
        self.assertGreater(l_of(peak), l_of(peak * 2))
        self.assertGreater(l_of(peak), l_of(1))

    def test_a_part_that_cannot_reach_l_is_refused_with_its_maximum(self):
        # a 5.3 mm toroid cannot hold 100 uH at 8 A; the answer says so
        out = _biased(dc_bias_current_a=8.0, shape=None)
        gone = [r for r in out["value"]["rejected"]
                if r.get("reason") == "dc_bias_unreachable"]
        self.assertTrue(gone)
        sample = gone[0]
        self.assertEqual(sample["dc_bias"]["status"], "unreachable")
        self.assertIn("why", sample["dc_bias"])
        self.assertGreater(sample["dc_bias"]["maximum_inductance_h"], 0.0)
        self.assertIn(sample["part"], out["value"]["dc_bias"]["unreachable"])
        self.assertIsNone(sample["turns"])

    def test_the_cap_is_a_cap(self):
        # with a 6-turn window nothing large enough exists: the refusal has to
        # come from the cap, not from a silent fallback
        out = _biased(dc_bias_current_a=8.0, dc_bias_turns_max=6)
        v = out["value"]
        self.assertEqual(v["feasible_count"], 0)
        unreachable = [r for r in v["rejected"]
                       if r.get("reason") == "dc_bias_unreachable"]
        self.assertTrue(unreachable)
        self.assertEqual(unreachable[0]["dc_bias"]["turns_max_scanned"], 6)


class TestTheFitIsReadNotAssumed(unittest.TestCase):
    """A different table has to give a different answer."""

    def _patched_table(self, name, mutate):
        data = _load(DATA)
        mutate(data)
        p = _scratch_path(name)
        with io.open(p, "w", encoding="utf-8", newline="\n") as fh:
            json.dump(data, fh, ensure_ascii=False, indent=1)
        return p

    @staticmethod
    def _make_softer(data):
        # halve b: a flatter curve, so fewer turns are needed at the same current
        for g in data["groups"]:
            if g["material"] == "XFlux" and g["shape_family"] == "Toroids":
                for r in g["rows"]:
                    r["b"] = r["b"] / 2.0

    def test_a_softer_curve_needs_fewer_turns(self):
        base = _biased(dc_bias_current_a=8.0)
        p = self._patched_table("select_core_dc_soft.json", self._make_softer)
        soft = _biased(dc_bias_current_a=8.0, dc_bias_data_path=p)
        self.assertEqual(base["status"], "ok")
        self.assertEqual(soft["status"], "ok")
        base_by_part = {c["part"]: c for c in base["value"]["candidates"]}
        deltas = [a["dc_bias"]["turns"]
                  - base_by_part[a["part"]]["dc_bias"]["turns"]
                  for a in soft["value"]["candidates"]
                  if a["material"] == "XFlux"
                  and a.get("material_variant") is None
                  and a["part"] in base_by_part]
        self.assertTrue(deltas)
        # a smaller b means the same current eats less of mu, so the same L is
        # reached with FEWER turns - never more
        self.assertLess(min(deltas), 0)
        self.assertLessEqual(max(deltas), 0)
        # and a flatter curve also lets more parts reach L at all - the pool is
        # re-solved, not the 0.0.77 answer scaled
        self.assertNotEqual(base["value"]["feasible_count"],
                            soft["value"]["feasible_count"])

    def test_a_shifted_a_is_refused_by_the_loader(self):
        def shift(data):
            data["groups"][0]["rows"][0]["a"] = 0.02
        p = self._patched_table("select_core_dc_bada.json", shift)
        out = _biased(dc_bias_current_a=8.0, dc_bias_data_path=p)
        self.assertEqual(out["status"], "error")
        self.assertEqual(out["reason_code"], "DATA_UNAVAILABLE")

    def test_a_missing_table_is_refused(self):
        out = _biased(dc_bias_current_a=8.0,
                      dc_bias_data_path=_scratch_path("nope_dc_bias.json"))
        self.assertEqual(out["status"], "error")
        self.assertEqual(out["reason_code"], "DATA_UNAVAILABLE")


class TestTheRefusals(unittest.TestCase):
    """Each of these would otherwise be answered with the wrong question."""

    def test_a_bias_without_powder_cores_is_refused(self):
        out = mag.select_core(L_REQ, IPK, dc_bias_current_a=8.0)
        self.assertEqual(out["status"], "error")
        self.assertEqual(out["reason_code"], "ARG_INVALID")
        self.assertIn("include_powder_cores", json.dumps(out, ensure_ascii=False))

    def test_a_bias_above_the_peak_current_is_refused(self):
        out = mag.select_core(L_REQ, 5.0, include_powder_cores=True,
                              dc_bias_current_a=8.0)
        self.assertEqual(out["status"], "error")
        self.assertEqual(out["reason_code"], "ARG_RANGE")

    def test_a_negative_bias_is_refused(self):
        out = _biased(dc_bias_current_a=-1.0)
        self.assertEqual(out["status"], "error")
        self.assertEqual(out["reason_code"], "ARG_RANGE")

    def test_a_text_bias_is_refused(self):
        out = _biased(dc_bias_current_a="eight")
        self.assertEqual(out["status"], "error")
        self.assertEqual(out["reason_code"], "ARG_INVALID")


class TestASolidCoreSaysSoInsteadOfGuessing(unittest.TestCase):
    """Ferrites have no printed fit: the row says that, it does not pretend."""

    def test_a_ferrite_candidate_carries_a_named_gap(self):
        out = mag.select_core(220e-6, 1.5, material="N87",
                              dc_bias_current_a=1.0,
                              include_powder_cores=True,
                              current_density_a_mm2=5.0, i_rms_a=1.0)
        v = out["value"]
        rows = v["candidates"] + [r for r in v["rejected"] if "material" in r]
        ferrites = [r for r in rows if r.get("material") == "N87"]
        self.assertTrue(ferrites)
        for r in ferrites:
            self.assertIsNone(r["dc_bias"])
            self.assertIn("powder", r["dc_bias_because"])
            self.assertIn("dc_bias", r["missing"])
        self.assertIn("0 of the", v["dc_bias"]["listed_with_a_fit"])

    def test_the_untouched_note_still_mentions_the_solid_core_gap(self):
        v = mag.select_core(220e-6, 1.5, material="N87",
                            dc_bias_current_a=1.0,
                            include_powder_cores=True)["value"]
        joined = " ".join(v["not_modelled"])
        self.assertIn("a solid core here has none", joined)
        self.assertNotIn("a powder core's DC-bias roll-off", joined)


class TestThePrintedBand(unittest.TestCase):
    """10-800 Oe is where the curve is drawn; past it mu is an extrapolation."""

    def test_an_out_of_band_design_is_flagged(self):
        # 20 A on the biggest 60u toroid lands well past 800 Oe only for small
        # parts, so ask for something small: shape narrows to the 5.3 mm bore
        out = _biased(dc_bias_current_a=6.0, limit=None)
        v = out["value"]
        flagged = v["dc_bias"]["outside_the_printed_band"]
        self.assertTrue(flagged)
        row = [c for c in v["candidates"] if c["part"] == flagged[0]][0]
        self.assertFalse(row["dc_bias"]["h_within_printed_band"])
        self.assertIn("EXTRAPOLATION", row["dc_bias"]["h_band_note"])
        self.assertIn("10-800 Oe", v["dc_bias"]["band_note"])
        self.assertEqual(mag.DC_BIAS_PLOTTED_OE, (10.0, 800.0))

    def test_an_in_band_design_is_not_flagged(self):
        v = _biased(dc_bias_current_a=8.0)["value"]
        first = v["candidates"][0]
        self.assertTrue(first["dc_bias"]["h_within_printed_band"])
        self.assertNotIn(first["part"], v["dc_bias"]["outside_the_printed_band"])


class TestTheToolSurface(unittest.TestCase):

    def test_the_schema_carries_the_arguments(self):
        props = tools.TOOLS["magnetics_select_core"]["inputSchema"]["properties"]
        for key in ("dc_bias_current_a", "dc_bias_data_path", "dc_bias_turns_max"):
            self.assertIn(key, props)
        self.assertEqual(props["dc_bias_turns_max"]["default"], 1000)

    def test_the_handler_passes_them_through(self):
        out = tools.TOOLS["magnetics_select_core"]["handler"]({
            "inductance_h": L_REQ, "i_peak_a": IPK,
            "include_powder_cores": True, "permeability": MU,
            "dc_bias_current_a": 8.0, "limit": 1})
        self.assertEqual(out["status"], "ok")
        row = out["value"]["candidates"][0]
        self.assertIn("dc_bias", row)
        self.assertGreaterEqual(row["turns"], row["dc_bias"]["turns_without_bias"])

    def test_the_description_states_the_loop_and_the_non_monotonicity(self):
        text = tools.TOOLS["magnetics_select_core"]["description"]
        self.assertIn("dc_bias_current_a", text)
        self.assertIn("86 of the 125", text)
        self.assertIn("dc_bias_unreachable", text)

    def test_the_capability_note_no_longer_calls_it_open(self):
        text = tools._capabilities({})["not_implemented_yet"][0]
        self.assertIn("closed in 0.0.78", text)
        self.assertNotIn("a fixed point 0.0.77 does not", text)

    def test_the_tool_count_did_not_move(self):
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
