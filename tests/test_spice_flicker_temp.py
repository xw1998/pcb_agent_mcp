"""Tests for the three 0.0.54 honesty repairs in the builtin SPICE subset.

Every one of these fixes a deck that USED to run and answer, quietly, a
question the caller did not ask:

* `KF=` on a diode was ignored, so the noise report was 1/f-free while the card
  said otherwise. Now it is modelled, and the expected numbers below are the
  bundled ngspice-47's own readings on the same deck - not a run of this code.
* `KF=` on a Q/M/J is still not modelled (no reference model exists: measured,
  ngspice-47 moves its answer by 0 % for KF 1e-15..1e-6). It is REFUSED at the
  .noise analysis by name instead of being reported without it.
* `.temp 25 100` was read as "take 25, drop 100". It is a sweep now, and each
  case has to be the SAME number as that deck run with one temperature.
* `mc(1)` / `agauss(...)` used to parse as the nominal value; they are refused.

Each class names the observation that would fail if its claim were false, and
the negative control is in the same class wherever one exists (a fix that also
"works" for KF=0, or for a single .temp, would pass the positive test alone).
"""
import io
import math
import os
import sys
import unittest

sys.path.insert(0, os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "src"))

from pcb_agent_mcp.calc import spice  # noqa: E402
from pcb_agent_mcp import tools as T  # noqa: E402

KB = 1.380649e-23
Q = 1.602176634e-19

# ---- the same deck that was handed to ngspice-47 (vendor/ngspice), where the
# two densities in ONOISE below were read off `print onoise_spectrum`.
NG_ONOISE_1HZ = 2.534181e-08
NG_ONOISE_10HZ = 8.302515e-09

FLICKER = ("* flicker probe diode\n"
           "V1 2 0 dc 0.6 ac 1\n"
           "R1 2 1 1k\n"
           "D1 1 0 DM\n"
           ".model DM D(KF=1e-16 AF=1)\n"
           ".noise v(1) V1 dec 1 1 10\n.end\n")


def _val(net, **kw):
    r = spice.simulate(net, **kw)
    assert r["status"] == "ok", r
    return r["value"]


def _err(net, **kw):
    r = spice.simulate(net, **kw)
    assert r["status"] == "error", r
    return r


def _warn(r):
    return " | ".join(r.get("warnings") or [])


def _psd_at(point, element):
    return point["contributions_v2_per_hz"][element]


class TheDiodeFlickerReproducesTheEngine(unittest.TestCase):
    """The 1/f form is not invented: it is fitted to ngspice-47's own readings."""

    def test_the_two_engine_readings_come_back_within_one_percent(self):
        v = _val(FLICKER)
        pts = {round(p["freq_hz"]): p for p in v["points"]}
        self.assertEqual(sorted(pts), [1, 10])
        for f, want in ((1, NG_ONOISE_1HZ), (10, NG_ONOISE_10HZ)):
            got = pts[f]["onoise_v_per_sqrt_hz"]
            self.assertLess(abs(got / want - 1.0), 0.01,
                            "f=%g Hz: this build reads %.6g, ngspice-47 reads %.6g"
                            % (f, got, want))

    def test_without_kf_the_answer_falls_by_an_order_of_magnitude(self):
        """The negative control: with KF=0 the 1/f term must be GONE.

        A build that reported the same density with and without KF= would pass
        the test above only by accident; this one cannot pass unless the term is
        really being added.
        """
        without = _val(FLICKER.replace("KF=1e-16 AF=1", "KF=0"))
        withkf = _val(FLICKER)
        a = without["points"][0]["onoise_v_per_sqrt_hz"]
        b = withkf["points"][0]["onoise_v_per_sqrt_hz"]
        self.assertGreater(b / a, 3.0,
                           "KF=1e-16 has to dominate 1/f at 1 Hz (got ratio %.3g)"
                           % (b / a))
        # ... and the KF=0 answer is the pure shot+thermal one, which does NOT
        # depend on frequency: if the flicker leaked in, this would fall too.
        self.assertAlmostEqual(without["points"][0]["onoise_v_per_sqrt_hz"],
                               without["points"][1]["onoise_v_per_sqrt_hz"],
                               places=12)

    def test_the_extra_term_is_exactly_kf_times_id_over_f(self):
        """Independent arithmetic: the flicker psd is the card's KF * Id / f.

        Id is read back from the SHOT density (2q*Id), so this test does not
        trust the junction current the flicker code used - if that code had
        picked up the wrong current, the ratio would not be KF.
        """
        v = _val(FLICKER)
        pt = v["points"][0]
        shot = [s for s in v["sources"]
                if s["element"] == "D1" and s["mechanism"] == "shot"][0]
        i_d = shot["psd_a2_per_hz"] / (2.0 * Q)
        flick = [s for s in v["sources"]
                 if s["element"] == "D1" and s["mechanism"] == "flicker"][0]
        got = flick["psd_a2_per_hz_at"]["1"]
        want = 1e-16 * i_d / 1.0
        self.assertLess(abs(got / want - 1.0), 1e-9,
                        "flicker psd %.6g vs KF*Id/f %.6g" % (got, want))


class TheShapeIsFrequencyDependent(unittest.TestCase):
    """S(f)*f is constant for AF=1 and S(f)*f^2 for AF=2 - and not the other way.

    The density is read from the SOURCE list (the flicker term alone), not from
    the element's total: D1's contribution also carries shot noise, which is
    frequency independent and would blur the shape.
    """

    DECK = ("* d\nV1 2 0 dc 0.6 ac 1\nR1 2 1 1k\nD1 1 0 DM\n"
            ".model DM D(KF=1e-15 AF=%s)\n.noise v(1) V1 dec 1 1 10\n.end\n")

    def _ends(self, af):
        v = _val(self.DECK % af)
        fir = [s for s in v["sources"] if s["mechanism"] == "flicker"][0]
        at = fir["psd_a2_per_hz_at"]
        self.assertEqual(sorted(at, key=float), ["1", "10"])
        return at["1"], at["10"]

    def test_af1_scales_as_one_over_f(self):
        a1, a10 = self._ends("1")
        self.assertLess(abs((a1 * 1.0) / (a10 * 10.0) - 1.0), 1e-9,
                        "AF=1: S(f)*f must not drift (got %.6g)"
                        % ((a1 * 1.0) / (a10 * 10.0)))

    def test_the_f_minus_one_shape_does_not_depend_on_af(self):
        """AF is the CURRENT exponent; the frequency exponent is always -1.

        So S(f)*f is constant for AF=2 as well - and the two cards still have to
        differ in magnitude, by exactly |Id|^(AF2-AF1), which is what the other
        class measures. A build that scaled the frequency by AF would pass this
        and fail there; one that ignored AF would pass this and fail the
        current-ratio test.
        """
        a1, a10 = self._ends("2")
        self.assertLess(abs((a1 * 1.0) / (a10 * 10.0) - 1.0), 1e-9,
                        "AF=2: S(f)*f must still be constant")


class TheFlickerFollowsTheCurrent(unittest.TestCase):
    """KF*|Id|^AF: the flicker density tracks the junction current."""

    DECK = ("* d\nV1 2 0 dc %g ac 1\nR1 2 1 1k\nD1 1 0 DM\n"
            ".model DM D(KF=1e-15 AF=1)\n.noise v(1) V1 lin 2 1 2\n.end\n")

    def _flicker_and_shot(self, vfwd):
        v = _val(self.DECK % vfwd)
        fir = [s for s in v["sources"] if s["mechanism"] == "flicker"][0]
        shot = [s for s in v["sources"] if s["mechanism"] == "shot"][0]
        return fir["psd_a2_per_hz_at"]["1"], shot["psd_a2_per_hz"]

    def test_the_ratio_of_flicker_equals_the_ratio_of_current(self):
        # Id is read from the shot density (2q*Id), so the ratio below does not
        # borrow the current the flicker code itself used
        fa, sa = self._flicker_and_shot(0.60)
        fb, sb = self._flicker_and_shot(0.64)
        self.assertGreater(sb / sa, 1.2, "the two biases must differ in current")
        self.assertLess(abs((fb / fa) / (sb / sa) - 1.0), 1e-9,
                        "AF=1 must scale with |Id| (flicker %.6g vs shot %.6g)"
                        % (fb / fa, sb / sa))

    def test_af2_goes_as_the_square_of_the_current(self):
        """Negative control for the exponent: AF=2 must NOT track Id linearly."""
        net = self.DECK.replace("AF=1", "AF=2")
        vals = []
        for vfwd in (0.60, 0.64):
            v = _val(net % vfwd)
            fir = [s for s in v["sources"] if s["mechanism"] == "flicker"][0]
            shot = [s for s in v["sources"] if s["mechanism"] == "shot"][0]
            vals.append((fir["psd_a2_per_hz_at"]["1"], shot["psd_a2_per_hz"]))
        (fa, sa), (fb, sb) = vals
        self.assertLess(abs((fb / fa) / (sb / sa) ** 2 - 1.0), 1e-9)
        self.assertGreater(abs((fb / fa) / (sb / sa) - 1.0), 0.1)


class TheUnmodelledFlickerIsRefused(unittest.TestCase):
    """A Q/M/J that states KF= gets an error naming it, not a quiet number."""

    BJT = ("* bjt\nV1 1 0 dc 0.7 ac 1\nR1 1 2 10k\nV2 4 0 5\nR2 4 3 1k\n"
           "Q1 3 2 0 qm\n.model qm NPN(IS=1e-16 BF=100%s)\n"
           ".noise V(3) V1 lin 3 100 300\n.end\n")
    MOS = ("* mos\nV1 1 0 5 ac 1\nV2 2 0 1.5\nR1 1 3 1k\nM1 3 2 0 0 nmos W=10u L=1u\n"
           ".model nmos NMOS(KP=1e-4 VTO=0.5%s)\n"
           ".noise V(3) V1 lin 3 100 300\n.end\n")
    JFET = ("* jf\nV1 1 0 5 ac 1\nVG 2 0 0\nRD 1 3 1k\nJ1 3 2 0 jm\n"
            ".model jm NJF(VTO=-2 BETA=1m%s)\n"
            ".noise V(3) V1 lin 3 100 300\n.end\n")

    def test_each_device_is_named_in_the_refusal(self):
        for net, dev in ((self.BJT, "Q1"), (self.MOS, "M1"), (self.JFET, "J1")):
            r = _err(net % " KF=1e-16 AF=1")
            self.assertEqual(r["reason_code"], "NOT_MODELED")
            self.assertIn(dev, r["message"])
            self.assertIn("0.0 %", r["hint"],
                          "the hint has to carry the measured engine fact")
            self.assertEqual([d["element"] for d in
                              r["devices_with_unmodelled_flicker"]], [dev])

    def test_without_kf_the_same_deck_answers(self):
        """Negative control: the refusal is about KF=, not about the device."""
        for net in (self.BJT, self.MOS, self.JFET):
            v = _val(net % "")
            self.assertEqual(v["analysis"], "noise")
            self.assertTrue(v["points"])

    def test_a_kf_deck_still_answers_op(self):
        """KF= changes nothing outside .noise, exactly as in SPICE."""
        r = spice.simulate("* d\nV1 1 0 0.7\nR1 1 2 10k\nV2 4 0 5\nR2 4 3 1k\n"
                           "Q1 3 2 0 qm\n.model qm NPN(IS=1e-16 BF=100 KF=1e-16)\n"
                           ".op\n.end\n")
        self.assertEqual(r["status"], "ok")


class TheTemperatureCardIsASweep(unittest.TestCase):
    """.temp 25 100 solves twice, and each case IS the one-temperature answer."""

    DECK = ("* t\nV1 1 0 5\nR1 1 2 1k\nD1 2 0 DM\n"
            ".model DM D(IS=1e-14)\n.temp %s\n.op\n.end\n")

    def test_two_temperatures_give_two_independent_cases(self):
        v = _val(self.DECK % "25 100")
        self.assertEqual(v["analysis"], "temperature_sweep")
        self.assertEqual(v["temperatures_c"], [25.0, 100.0])
        self.assertEqual([c["temp_c"] for c in v["cases"]], [25.0, 100.0])
        self.assertEqual([c["result"]["assumptions"]["temp_c"]
                          for c in v["cases"]], [25.0, 100.0])
        # the diode's forward drop falls with temperature: if one case were
        # recomputed from the other this monotonicity would not survive
        v25 = v["cases"][0]["result"]["value"]["nodes_v"]["2"]
        v100 = v["cases"][1]["result"]["value"]["nodes_v"]["2"]
        self.assertGreater(v25, v100)
        self.assertIsNot(v["cases"][0]["result"], v["cases"][1]["result"])

    def test_each_case_equals_the_deck_run_at_that_one_temperature(self):
        v = _val(self.DECK % "25 100")
        for i, t in enumerate((25.0, 100.0)):
            solo = _val(self.DECK % ("%g" % t))
            self.assertEqual(solo["analysis"], "op",
                             "one temperature is not a sweep")
            self.assertEqual(v["cases"][i]["result"]["value"]["nodes_v"],
                             solo["nodes_v"])

    def test_a_caller_temperature_overrides_the_card_and_says_so(self):
        r = spice.simulate(self.DECK % "25 100", temp_c=50)
        self.assertEqual(r["status"], "ok")
        self.assertEqual(r["value"]["analysis"], "op")
        self.assertAlmostEqual(r["assumptions"]["temp_c"], 50.0)
        self.assertIn("overrode", _warn(r))

    def test_an_empty_temperature_card_is_refused(self):
        r = _err(self.DECK % "")
        self.assertEqual(r["reason_code"], "NETLIST_INVALID")
        self.assertIn("at least one temperature", r["message"])


class MonteCarloValuesAreRefused(unittest.TestCase):
    """`R1 1 0 1k mc(1)` used to come back as a confident 1000 ohm."""

    DECK = "V1 1 0 5\nR1 1 0 %s\n.op\n.end\n"

    def test_each_statistical_value_is_refused_by_name(self):
        for val in ("mc(1)", "mc(10,1)", "unif(0.9k,1.1k)",
                    "agauss(1k,100,1)", "gauss(1k,100)", "aunif(0.9k,1.1k)"):
            r = _err(self.DECK % val)
            self.assertEqual(r["reason_code"], "NETLIST_INVALID", val)
            self.assertIn("Monte-Carlo", r["message"], val)

    def test_the_negative_control_is_a_plain_number(self):
        v = _val(self.DECK % "1k")
        self.assertAlmostEqual(v["nodes_v"]["1"], 5.0, places=9)


class TheCardsThatWereFalselyClaimedToBeRejected(unittest.TestCase):
    """The capability text used to say F H J K S W were rejected. They are not."""

    def test_f_h_j_k_s_w_all_solve(self):
        decks = {
            "F": "* f\nV1 1 0 1\nR1 1 0 1k\nF1 2 0 V1 2\nR2 2 0 1k\n.op\n.end\n",
            "H": "* h\nV1 1 0 1\nR1 1 0 1k\nH1 2 0 V1 2\nR2 2 0 1k\n.op\n.end\n",
            "J": "* j\nVd 1 0 3\nVg 2 0 0\nRd 1 3 1k\nJ1 3 2 0 jm\n"
                 ".model jm NJF(VTO=-1 BETA=1m)\n.op\n.end\n",
            "K": "* k\nV1 1 0 AC 1\nR1 1 3 1\nL1 3 0 100u\nL2 2 0 400u\n"
                 "K1 L1 L2 0.5\nR2 2 0 1meg\n.ac lin 2 1k 2k\n.end\n",
            "S": "* s\nV1 1 0 2\nR1 1 2 100\nR2 2 0 100\nS1 2 0 1 0 sw\n"
                 ".model sw SW(VT=1 RON=10 ROFF=1meg)\n.op\n.end\n",
            "W": "* w\nV1 1 0 2\nR1 1 2 100\nR2 2 0 100\nW1 2 0 V1 sw\n"
                 ".model sw CSW(IT=1m RON=1 ROFF=1meg)\n.op\n.end\n",
        }
        for letter, deck in sorted(decks.items()):
            r = spice.simulate(deck)
            self.assertEqual(r["status"], "ok", "%s card: %s" % (letter, r))

    def test_the_capability_text_no_longer_claims_they_are_rejected(self):
        c = T.call_tool("calc_capabilities", {})
        text = "\n".join(c["not_implemented_yet"])
        self.assertNotIn("rejects F H J K S W", text)
        self.assertIn("F H J K S W are SOLVED", text)
        self.assertIn("A multi-valued .temp card is a SWEEP", text)
        self.assertIn("KF*|Id|^AF/f", text)


class NoTrailingTokenIsDropped(unittest.TestCase):
    """A token the card cannot use is named, never dropped.

    `R1 1 0 1k mc(1)` is why this exists: the value parsed, the Monte-Carlo call
    was dropped, and the deck answered at 1 k - a confident answer to a
    question the caller did not ask. The same hole in `R1 1 0 1k 2k` looks like
    a typo until you notice the second number never reached anything.
    """

    def test_a_token_after_an_r_value_is_refused(self):
        for extra in ("mc(1)", "2k", "tc1=0.1"):
            r = _err("V1 1 0 5\nR1 1 0 1k %s\n.op\n.end\n" % extra)
            self.assertEqual(r["reason_code"], "NETLIST_INVALID", extra)
            self.assertIn("trailing", r["message"], extra)
            self.assertIn(extra, r["message"], extra)

    def test_ic_is_still_read_on_a_c_or_an_l(self):
        """Negative control: the one documented trailing field must survive."""
        # each part in series with a 1 k, so the DC point exists: an L straight
        # across an ideal source is a source loop, which is a different answer
        # (the C is open at DC so node 2 sits at the rail; the L is a short so it
        # sits at ground - either way the ic= field must not be the thing that
        # makes the deck fail)
        v = _val("V1 1 0 5\nR1 1 2 1k\nC1 2 0 1u ic=1\n.op\n.end\n")
        # gmin sits across every junction-free node pair, so the rail is
        # 4.999999995 V rather than 5; 1e-6 is well inside that
        self.assertAlmostEqual(v["nodes_v"]["2"], 5.0, places=6)
        v = _val("V1 1 0 5\nR1 1 2 1k\nL1 2 0 1m ic=0.1\n.op\n.end\n")
        self.assertAlmostEqual(v["nodes_v"]["2"], 0.0, places=9)
        r = _err("V1 1 0 5\nR1 1 0 1k\nC1 1 0 1u tc=1\n.op\n.end\n")
        self.assertIn("trailing", r["message"])

    def test_a_sixth_field_on_an_e_card_is_refused(self):
        r = _err("V1 1 0 5\nR1 1 0 1k\nE1 2 0 1 0 2 extra\nR2 2 0 1k\n.op\n.end\n")
        self.assertIn("trailing", r["message"])
        v = _val("V1 1 0 5\nR1 1 0 1k\nE1 2 0 1 0 2\nR2 2 0 1k\n.op\n.end\n")
        self.assertAlmostEqual(v["nodes_v"]["2"], 10.0, places=6)


class AnArgumentNoHandlerReadsIsReported(unittest.TestCase):
    """A misspelt key used to produce a confident answer computed at the default."""

    DECK = "V1 1 0 5\nR1 1 0 1k\n.op\n.end\n"

    def test_a_misspelt_key_is_named_and_the_answer_stands(self):
        r = T.call_tool("sim_spice", {"netlist": self.DECK, "temperature_c": 50})
        self.assertEqual(r["status"], "ok")
        self.assertTrue(any("temperature_c" in w and "IGNORED" in w
                            for w in (r.get("warnings") or [])), r.get("warnings"))

    def test_a_correct_call_gains_no_warning(self):
        """Negative control: the warning must not appear for a well-formed call."""
        r = T.call_tool("sim_spice", {"netlist": self.DECK})
        self.assertEqual(r["status"], "ok")
        self.assertFalse(any("IGNORED" in w for w in (r.get("warnings") or [])))
        self.assertNotIn("unread_arguments", r)


if __name__ == "__main__":
    unittest.main()
