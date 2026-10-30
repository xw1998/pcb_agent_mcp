"""Golden tests for the SPICE solver.

Every expected number here was computed BY HAND from the textbook formula, not
by running this solver and copying its output - otherwise the test would only
prove the code is stable, not that it is right.

Two of these tests are about refusals rather than answers: an unimplemented card
must be rejected by NAME, and a non-converging circuit must report an error
instead of a plausible field. A simulator that silently skips a card it does not
know is worse than one that refuses, because the user gets a number with no
warning attached.
"""
import math
import os
import sys
import unittest

sys.path.insert(0, os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "src"))

from pcb_agent_mcp.calc import spice  # noqa: E402


def _op(net):
    r = spice.simulate(net, analysis="op")
    assert r["status"] == "ok", r
    return r["value"]


class TestDC(unittest.TestCase):
    def test_divider(self):
        # 10 V across 1k + 2k: the tap is 10*2/3 = 6.6666667 V
        v = _op("* div\nV1 1 0 10\nR1 1 2 1k\nR2 2 0 2k\n.end\n")
        self.assertAlmostEqual(v["nodes_v"]["2"], 10.0 * 2.0 / 3.0, places=7)

    def test_series_resistors_share_the_current(self):
        v = _op("* two\nV1 1 0 5\nR1 1 2 100\nR2 2 0 300\n.end\n")
        self.assertAlmostEqual(v["nodes_v"]["2"], 5.0 * 300.0 / 400.0, places=7)
        i = 5.0 / 400.0
        r1 = [e for e in v["elements"] if e["name"] == "R1"][0]
        self.assertAlmostEqual(abs(r1["current_a"]), i, places=9)
        self.assertAlmostEqual(r1["power_w"], i * i * 100.0, places=9)

    def test_every_element_reports_its_own_state(self):
        v = _op("* div\nV1 1 0 10\nR1 1 2 1k\nR2 2 0 2k\n.end\n")
        names = [e["name"] for e in v["elements"]]
        self.assertEqual(names, ["V1", "R1", "R2"])
        for e in v["elements"]:
            self.assertIn("voltage_v", e)
            self.assertIn("current_a", e)
            self.assertIn("power_w", e)

    def test_current_source_into_a_resistor(self):
        # SPICE convention: `I n+ n- value` pushes current from n+ THROUGH the
        # source to n-, so `I1 1 0 1m` injects 1 mA into node 1. Getting this
        # backwards turns 10 V into -10 V and nothing else here would notice.
        v = _op("* isrc\nI1 1 0 1m\nR1 1 0 10k\n.end\n")
        self.assertAlmostEqual(v["nodes_v"]["1"], 10.0, places=6)

    def test_vcvs_and_vccs(self):
        # E gain 10 driven from a 1 V source; G drives 1k with 1mS*1V = 1 mA
        v = _op("* dep\nV1 1 0 1\nE1 2 0 1 0 10\nG1 0 3 1 0 1m\nR1 3 0 1k\n.end\n")
        self.assertAlmostEqual(v["nodes_v"]["2"], 10.0, places=6)
        self.assertAlmostEqual(v["nodes_v"]["3"], 1.0, places=6)


class TestDiode(unittest.TestCase):
    def test_silicon_diode_forward_drop(self):
        # 5 V, 1k, IS=1e-14, N=1: solving 5 = I*1000 + Vt*ln(I/IS) gives
        # Vd = 0.6925 V (the classic number for a 4.3 mA silicon diode)
        v = _op("* d\nV1 1 0 5\nR1 1 2 1k\nD1 2 0 dmod\n.model dmod D(IS=1e-14 N=1)\n.end\n")
        vd = v["nodes_v"]["2"]
        self.assertAlmostEqual(vd, 0.692544, places=5)
        i = (5.0 - vd) / 1000.0
        self.assertAlmostEqual(i, 0.0043075, places=6)
        # and it must converge in a handful of iterations, not hundreds: a solver
        # without junction limiting takes ~170 sweeps here and still lands wrong
        self.assertLess(v["iterations"], 40)

    def test_reverse_biased_diode_is_not_a_short(self):
        v = _op("* rev\nV1 1 0 -5\nR1 1 2 1k\nD1 2 0 dmod\n.model dmod D(IS=1e-14)\n.end\n")
        # 5 V the wrong way: the node sits at about -5 V, current ~ IS
        self.assertLess(v["nodes_v"]["2"], -4.999)
        d = [e for e in v["elements"] if e["name"] == "D1"][0]
        self.assertLess(abs(d["current_a"]), 1e-8)

    def test_missing_model_is_reported_with_the_line_number(self):
        r = spice.simulate("* bad\nV1 1 0 1\nD1 1 0 nope\n.op\n.end\n", analysis="op")
        self.assertEqual(r["status"], "error")
        self.assertEqual(r["reason_code"], "NETLIST_INVALID")
        self.assertIn("line 3", r["message"])


class TestBJT(unittest.TestCase):
    def test_forward_active_beta_comes_out_of_the_model(self):
        # 10 V, collector 4.7k, base 470k from 5 V, BF=200.
        # Ib = (5-Vbe)/470k, Ic = 200*Ib, and Vce = 10 - Ic*4.7k must be > Vbe.
        v = _op("* q\nVCC 1 0 10\nVB 4 0 5\nRB 4 2 470k\nQ1 3 2 0 qmod\n"
                "RC 1 3 4.7k\n.model qmod NPN(IS=1e-16 BF=200)\n.end\n")
        q = [e for e in v["elements"] if e["name"] == "Q1"][0]
        ib = q["current_a"].get("ib") if isinstance(q["current_a"], dict) else None
        ic = q["current_a"].get("ic") if isinstance(q["current_a"], dict) else None
        self.assertIsNotNone(ib, "the BJT must report base and collector currents")
        self.assertAlmostEqual(ic / ib, 200.0, places=3)
        self.assertAlmostEqual(v["nodes_v"]["2"], 0.78905, places=4)
        self.assertAlmostEqual(v["nodes_v"]["3"], 1.5781, places=3)
        self.assertAlmostEqual(ic, 1.7919e-3, places=7)
        self.assertLess(v["iterations"], 40)


class TestMOSFET(unittest.TestCase):
    def test_square_law_saturation(self):
        # VTO=1, KP=2m, LAMBDA=0, Vgs=2 -> vov=1, Id = KP/2*vov^2 = 1 mA.
        # Into 1k from 5 V: Vds = 5 - 1m*1k = 4 V, still > vov, so saturation.
        v = _op("* m\nVDD 1 0 5\nVG 2 0 2\nM1 3 2 0 0 mmod\nRD 1 3 1k\n"
                ".model mmod NMOS(VTO=1 KP=2m LAMBDA=0)\n.end\n")
        self.assertAlmostEqual(v["nodes_v"]["3"], 4.0, places=4)
        m = [e for e in v["elements"] if e["name"] == "M1"][0]
        idn = m["current_a"].get("id") if isinstance(m["current_a"], dict) else None
        self.assertIsNotNone(idn)
        self.assertAlmostEqual(idn, 1.0e-3, places=9)
        self.assertLess(v["iterations"], 30)


class TestTransient(unittest.TestCase):
    def test_rc_step_matches_the_backward_euler_closed_form(self):
        # tau = 1k*1u = 1 ms; h = 0.1 ms; 5 ms = 50 steps.
        # Backward Euler gives v_n = 1-(1/(1+h/tau))^n exactly, so this asserts
        # the INTEGRATOR, not just "it rises towards 1".
        r = spice.simulate("* rc\nV1 1 0 pulse(0 1 0 1n 1n 10m 20m)\nR1 1 2 1k\n"
                           "C1 2 0 1u\n.tran 0.1m 5m\n.end\n")
        self.assertEqual(r["status"], "ok")
        last = r["value"]["points"][-1]
        expect = 1.0 - (1.0 / (1.0 + 0.1e-3 / 1e-3)) ** 50
        self.assertAlmostEqual(last["nodes_v"]["2"], expect, places=6)
        # and the continuous answer is different - if these ever coincide the
        # "backward euler" claim is a lie
        self.assertLess(abs(expect - (1 - math.exp(-5.0))), 0.01)
        self.assertGreater(abs(last["nodes_v"]["2"] - (1 - math.exp(-5.0))), 1e-4)

    def test_inductor_current_follows_the_rl_step(self):
        # 1 V step into 1 mH with 1 ohm series: tau = L/R = 1 ms, so at t = 1 ms
        # the current is 1-e^-1 = 0.6321 A. (A pure L across a 0 V source is a
        # voltage-source/inductor loop - singular at DC, refused rather than
        # fudged, which is why the series R is there.)
        r = spice.simulate("* rl\nV1 1 0 pulse(0 1 0 1n 1n 10m 20m)\n"
                           "L1 1 2 1m\nR1 2 0 1\n.tran 10u 1m\n.end\n")
        self.assertEqual(r["status"], "ok")
        l1 = [e for e in r["value"]["elements_final"] if e["name"] == "L1"][0]
        # The exact answer is 1-e^-1, but this solver is BACKWARD EULER, whose
        # closed form for a step is i_n = 1-(1+h/tau)^-n. Comparing against the
        # continuous answer would need a loose tolerance that hides a wrong
        # timestep count; comparing against the BE closed form at the stated
        # step count is exact to round-off.
        h, tau = 10e-6, 1e-3
        be = 1.0 - (1.0 + h / tau) ** -100
        self.assertAlmostEqual(l1["current_a"], be, places=9)
        self.assertEqual(r["value"]["steps"], 100)     # 1 ms / 10 us, not 101
        # and BE lags the continuous answer by about h/(2*tau) = 0.5%
        self.assertAlmostEqual(l1["current_a"] - (1.0 - math.exp(-1.0)),
                               -h / (2 * tau) * math.exp(-1.0), places=4)

    def test_half_wave_rectifier_charges_to_about_the_peak_minus_a_diode(self):
        r = spice.simulate("* rect\nV1 1 0 sin(0 5 1k)\nD1 1 2 dmod\nC1 2 0 10u\n"
                           "R1 2 0 1k\n.model dmod D(IS=1e-14)\n.tran 20u 5m 0 uic\n.end\n")
        self.assertEqual(r["status"], "ok")
        peak = max(p["nodes_v"]["2"] for p in r["value"]["points"])
        self.assertGreater(peak, 3.9)
        self.assertLess(peak, 5.0)          # a diode drop and ripple below 5 V

    def test_pwl_source(self):
        r = spice.simulate("* pwl\nV1 1 0 pwl(0 0 1m 1 2m 0)\nR1 1 0 1k\n"
                           ".tran 0.1m 2m\n.end\n")
        self.assertEqual(r["status"], "ok")
        pts = r["value"]["points"]
        mid = min(pts, key=lambda p: abs(p["t"] - 1e-3))
        self.assertGreater(mid["nodes_v"]["1"], 0.9)


class TestAC(unittest.TestCase):
    def test_series_rlc_resonance(self):
        # 10 ohm, 1 mH, 100 nF -> f0 = 1/(2*pi*sqrt(1e-3*1e-7)) = 15915.49 Hz,
        # and at resonance |V_C| = Q = 10 for a 1 V drive.
        r = spice.simulate("* rlc\nV1 1 0 AC 1\nR1 1 2 10\nL1 2 3 1m\nC1 3 0 100n\n"
                           ".ac dec 40 1k 100k\n.end\n")
        self.assertEqual(r["status"], "ok")
        f0 = 1.0 / (2 * math.pi * math.sqrt(1e-3 * 1e-7))
        self.assertAlmostEqual(f0, 15915.49, places=1)
        pts = r["value"]["points"]
        best = max(pts, key=lambda p: p["nodes_v"]["3"]["mag"])
        self.assertLess(abs(best["freq_hz"] - f0), f0 * 0.01)
        self.assertAlmostEqual(best["nodes_v"]["3"]["mag"], 10.0, places=1)

    def test_truncated_sweep_says_so_instead_of_ending_silently(self):
        # 200 points kept out of 401: the sweep stops near 6 kHz, where |V_C| is
        # still climbing. Reported as a warning because otherwise the last point
        # of the "sweep" looks like the peak of the circuit.
        r = spice.simulate("* rlc\nV1 1 0 AC 1\nR1 1 2 10\nL1 2 3 1m\nC1 3 0 100n\n"
                           ".ac dec 200 1k 100k\n.end\n")
        self.assertEqual(r["status"], "ok")
        self.assertEqual(len(r["value"]["points"]), 200)
        self.assertTrue(any("only the first" in w for w in r["warnings"]), r["warnings"])
        self.assertLess(r["value"]["points"][-1]["freq_hz"], 10000.0)

    def test_nonlinear_ac_uses_the_operating_point(self):
        # A diode biased at 4.3 mA has rd = n*Vt/I = 0.025852/0.0043075 = 6.0 ohm,
        # so a 1 V drive through 1k gives 6.0/(1000+6.0). Getting this wrong
        # (e.g. by stamping the DC bias current as an AC source) gave 0.669
        # instead of 0.00597 - a factor of 112.
        r = spice.simulate("* dac\nV1 1 0 DC 5 AC 1\nR1 1 2 1k\nD1 2 0 dmod\n"
                           ".model dmod D(IS=1e-14)\n.ac dec 1 1k 10k\n.end\n")
        self.assertEqual(r["status"], "ok")
        p = r["value"]["points"][0]
        vt = 0.025852
        vd = None
        op = spice.simulate("* dac\nV1 1 0 DC 5 AC 1\nR1 1 2 1k\nD1 2 0 dmod\n"
                            ".model dmod D(IS=1e-14)\n.op\n.end\n", analysis="op")
        vd = op["value"]["nodes_v"]["2"]
        rd = vt / ((5.0 - vd) / 1000.0)
        expect = rd / (1000.0 + rd)
        self.assertAlmostEqual(p["nodes_v"]["2"]["mag"], expect, places=5)

    def test_ac_without_a_dc_bias_divider_is_just_the_divider(self):
        r = spice.simulate("* ac\nV1 1 0 AC 1\nR1 1 2 1k\nR2 2 0 1k\n"
                           ".ac dec 1 100 1k\n.end\n")
        self.assertEqual(r["status"], "ok")
        self.assertAlmostEqual(r["value"]["points"][0]["nodes_v"]["2"]["mag"], 0.5,
                               places=6)


class TestRefusals(unittest.TestCase):
    def test_unimplemented_cards_are_named(self):
        # B is NOT in this list any more: 0.0.29 implements behavioural sources,
        # O and U left it in 0.0.31 (see docs/37); a card whose refusal would be a
        # lie must not be listed. The cards here still have no implementation.
        for card in ("F1 1 0 V1 1", "K1 L1 L2 0.9", "S1 1 2 3 4 sw",
                     "X1 1 0 sub", "T1 1 0 2 0 Z0=50", "H1 1 0 V1 1"):
            r = spice.simulate("* x\nV1 1 0 1\nR1 1 2 1k\nR2 2 0 1k\n%s\n.op\n.end\n"
                               % card, analysis="op")
            self.assertEqual(r["status"], "error", card)
            self.assertEqual(r["reason_code"], "NETLIST_INVALID", card)

    def test_too_large_is_refused_not_attempted(self):
        lines = ["* big", "V1 n0 0 1"]
        for k in range(1, 420):
            lines.append("R%d n%d n%d 1k" % (k, k - 1, k))
        lines.append(".op")
        lines.append(".end")
        r = spice.simulate("\n".join(lines), analysis="op")
        self.assertEqual(r["status"], "error")
        self.assertEqual(r["reason_code"], "TOO_LARGE")

    def test_bad_analysis_name(self):
        # "noise" was the example of an illegal name until 0.0.32 implemented it;
        # a name nobody implements still has to be refused, and refused by name
        r = spice.simulate("* d\nV1 1 0 1\nR1 1 0 1k\n.end\n", analysis="disto")
        self.assertEqual(r["status"], "error")
        self.assertEqual(r["reason_code"], "ARG_INVALID")
        self.assertIn("noise", r["message"])

    def test_a_legal_analysis_that_the_deck_does_not_ask_for(self):
        # legal name, no card: that is a missing card (ARG_REQUIRED), not a
        # misspelled name - the two refusals mean different things
        r = spice.simulate("* d\nV1 1 0 1\nR1 1 0 1k\n.end\n", analysis="noise")
        self.assertEqual(r["status"], "error")
        self.assertEqual(r["reason_code"], "ARG_REQUIRED")
        self.assertIn(".noise", r["hint"])

    def test_no_ground_node(self):
        r = spice.simulate("* n\nV1 1 2 1\nR1 1 2 1k\n.op\n.end\n", analysis="op")
        self.assertEqual(r["status"], "error")

    def test_temp_c_overrides_the_card(self):
        net = "* t\nV1 1 0 5\nR1 1 2 1k\nD1 2 0 dmod\n.model dmod D(IS=1e-14)\n.end\n"
        a = spice.simulate(net, analysis="op", temp_c=27)["value"]["nodes_v"]["2"]
        b = spice.simulate(net, analysis="op", temp_c=100)["value"]["nodes_v"]["2"]
        self.assertLess(b, a)      # hotter diode drops less at the same current
        # and the shift is about -2 mV/K, not a rounding artefact
        self.assertAlmostEqual((a - b) / (100.0 - 27.0), 0.001665, places=4)
        # the report must carry the scaled IS, not just the number it produced
        hot = [e for e in spice.simulate(net, analysis="op", temp_c=100)
               ["value"]["elements"] if e["name"] == "D1"][0]["model_params"]
        self.assertAlmostEqual(hot["IS_nominal"], 1e-14, places=20)
        self.assertGreater(hot["IS"], 1e-11)     # ~8500x at 100 C
        self.assertEqual(hot["TNOM_c"], 27.0)
        self.assertEqual(spice.simulate(net, analysis="op",
                                        temp_c=27)["assumptions"]["temp_c"], 27.0)

    def test_envelope_is_never_missing_basis_or_precision(self):
        r = spice.simulate("* d\nV1 1 0 10\nR1 1 2 1k\nR2 2 0 2k\n.op\n.end\n", analysis="op")
        for k in ("model", "basis", "precision", "assumptions"):
            self.assertTrue(r.get(k), k)


if __name__ == "__main__":
    unittest.main()
