"""Tests for the two distributed cards: the lossy line (O, an LTRA model) and the
uniform distributed RC line (U, a URC model).

Both are turned into a lumped ladder of ordinary R/L/C at parse time, so the claim
is not "this is a transmission line": it is "this is an N-section ladder of the
parameters the model gives, and every number says which ladder". Each mechanism
carries a control that FAILS when the mechanism is absent:

* DC, no shunt conductance -> the total series resistance, EXACT and independent
  of the section count (the sections are a chain of resistors). 1 ohm in a 3 V
  50/50 divider reads 1.514851/1.485149 V at 1/4/20 sections. A ladder built with
  the wrong per-section value would scale with the count.
* DC, shunt conductance    -> converges to the DISTRIBUTED answer
  1/(cosh(gL)+Z0c/RL*sinh(gL)) as the sections increase (first order, measured).
  A wrong shunt value converges to the wrong asymptote.
* AC, no loss              -> agrees with the exact lossless line (T) to 1e-4 in
  magnitude and 0.02 deg in phase: two totally different implementations of the
  same physics.
* AC, series resistance    -> |V(far)| = 0.4950 at 1 MHz, i.e. the 1 % attenuation
  alpha = r/(2*Z0) over 1 m. A ladder with no r would read 0.5.
* AC, U (RC line)          -> the input divider matches the analytic
  Z_in = Z0c*coth(gL) solution of the distributed RC line to 0.15 % at 1 MHz.
* .tran                    -> the wavefront arrives at TD (5 ns) and sharpens as
  the sections increase; the settled value is the DC divider.
* .op report               -> one record per card with segments/segments_from and
  the totals; the ladder's internal nodes are NOT reported as circuit nodes.

Everything the card cannot honour is refused by name: a missing/undefined/wrong
model, a model with no LEN, a negative per-length value, Z0=/TD= (that is the T
card), FMAX/ISPERL/RSPERL on a URC, lump= on the card (fatal to ngspice), two
different return nodes, a shorted port, and an all-zero model.
"""
import cmath
import math
import os
import sys
import unittest

sys.path.insert(0, os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "src"))

from pcb_agent_mcp.calc import spice  # noqa: E402

def _ok(net, analysis="op", **kw):
    r = spice.simulate(net, analysis=analysis, **kw)
    assert r["status"] == "ok", r
    return r["value"]

def _env(net, analysis="op", **kw):
    r = spice.simulate(net, analysis=analysis, **kw)
    assert r["status"] == "ok", r
    return r

def _nodes(net, analysis="op"):
    v = _ok(net, analysis)
    # .ac answers per frequency point; the tests below read the first point
    if v.get("analysis") == "ac":
        return v["points"][0]["nodes_v"]
    return v["nodes_v"]

def _err(net, analysis="op"):
    r = spice.simulate(net, analysis=analysis)
    assert r["status"] == "error", r
    return r

def _oline(lump=None, r="1", l="250n", g="0", c="100p", length="1", load="R2 3 0 50",
           source="V1 1 0 3", analysis=".op"):
    """A 3 V source, 50 ohm series, the line, and a 50 ohm load: a 50 ohm system."""
    params = "r=%s l=%s g=%s c=%s len=%s" % (r, l, g, c, length)
    if lump is not None:
        params += " lump=%d" % lump
    return ("* o\n%s\nR1 1 2 50\nO1 2 0 3 0 m\n%s\n"
            ".model m LTRA(%s)\n%s\n.end\n" % (source, load, params, analysis))

def _urc(lump=None, r="1000", c="1n", length="1", load="", analysis=".op",
         source="V1 1 0 3"):
    params = "r=%s c=%s len=%s" % (r, c, length)
    if lump is not None:
        params += " lump=%d" % lump
    tail = ("\n" + load) if load else ""
    return ("* u\n%s\nR1 1 2 50\nU1 2 3 0 m\n%s\n"
            ".model m URC(%s)\n%s\n.end\n" % (source, tail, params, analysis))

class DCIsTheSeriesResistance(unittest.TestCase):
    """With no shunt conductance the ladder IS a chain of resistors."""

    def test_one_ohm_in_a_50_ohm_divider_reads_the_exact_divider(self):
        # 3 V, 50 ohm source, 1 ohm of line, 50 ohm load: I = 3/101
        want_near = 3.0 * 51.0 / 101.0
        want_far = 3.0 * 50.0 / 101.0
        for lump in (1, 4, 20):
            n = _nodes(_oline(lump=lump))
            self.assertAlmostEqual(n["2"], want_near, places=6, msg="lump=%d" % lump)
            self.assertAlmostEqual(n["3"], want_far, places=6, msg="lump=%d" % lump)

    def test_the_section_count_does_not_change_the_dc_answer(self):
        # the control for "the per-section value was divided correctly": a ladder
        # with r*len per SECTION instead of r*len/N would scale with the count
        a = _nodes(_oline(lump=1))
        b = _nodes(_oline(lump=20))
        self.assertAlmostEqual(a["3"], b["3"], places=6)
        self.assertAlmostEqual(a["3"], 1.485148514, places=6)

    def test_the_card_reports_its_ports_and_its_section_count(self):
        v = _ok(_oline())
        recs = [e for e in v["elements"] if e["name"] == "O1"]
        self.assertEqual(len(recs), 1)
        rec = recs[0]
        self.assertEqual(rec["type"], "O")
        self.assertEqual(rec["segments"], 20)
        self.assertIn("default 20", rec["segments_from"])
        self.assertAlmostEqual(rec["totals"]["series_r_ohm"], 1.0, places=12)
        self.assertAlmostEqual(rec["port1"]["current_a"], 3.0 / 101.0, places=9)
        self.assertAlmostEqual(rec["port1"]["voltage_v"], 1.514851485, places=6)
        self.assertAlmostEqual(rec["port2"]["voltage_v"], 1.485148515, places=6)

    def test_a_defaulted_section_count_is_disclosed(self):
        # a defaulted model is said out loud, not buried in the answer
        r = _env(_oline())
        self.assertEqual(r["assumptions"]["defaulted_model_params"].get("O1.lump"), 20)
        r = _env(_oline(lump=7))
        self.assertEqual(r["assumptions"]["defaulted_model_params"], {})

    def test_the_ladder_nodes_are_not_reported_as_circuit_nodes(self):
        # 20 sections would otherwise add 39 nodes named o1#n3 to every answer
        n = _nodes(_oline(lump=20))
        self.assertEqual(sorted(n), ["1", "2", "3"])

    def test_a_uniform_rc_line_at_dc_is_its_total_resistance(self):
        # r=2000 ohm/m over 0.5 m = 1 kohm in series, capacitors open at DC, and
        # the 50 ohm source resistor is part of the divider: the line is the only
        # thing between node 2 and node 3, so both ends read the same current
        n = _nodes(_urc(r="2000", length="0.5", load="R2 3 0 1000"))
        total = 50.0 + 1000.0 + 1000.0
        self.assertAlmostEqual(n["2"], 3.0 * 2000.0 / total, places=6)
        self.assertAlmostEqual(n["3"], 3.0 * 1000.0 / total, places=6)
        self.assertAlmostEqual(n["2"] - n["3"], 3.0 * 1000.0 / total, places=6)

    def test_an_open_far_end_has_no_dc_path_through_the_line(self):
        # a line whose model has only a shunt capacitance is an open circuit at
        # DC: the far node is held at 0 only by its load
        n = _nodes("* o\nV1 1 0 3\nR1 1 2 50\nO1 2 0 3 0 m\nR2 3 0 50\n"
                   ".model m LTRA(c=100p len=1)\n.op\n.end\n")
        self.assertAlmostEqual(n["2"], 3.0, places=6)
        self.assertAlmostEqual(n["3"], 0.0, places=6)

class TheLadderConverges(unittest.TestCase):
    """A lumped ladder is a model: its error must shrink as the sections grow."""

    def test_the_shunt_conductance_converges_to_the_distributed_answer(self):
        # r=1 ohm/m, g=1 S/m, len=1 m, 50 ohm load: the distributed DC transfer is
        # V(L)/V(0) = 1/(cosh(gL) + Z0c/RL*sinh(gL)) with gL=1 and Z0c=1 ohm
        gamma, z0c, rl = 1.0, 1.0, 50.0
        want = 1.0 / (math.cosh(gamma) + z0c / rl * math.sinh(gamma))
        errs = {}
        for lump in (2, 5, 20, 60):
            n = _nodes(_oline(lump=lump, g="1"))
            errs[lump] = abs(n["3"] / n["2"] - want)
        # monotone, and closing: 1/N convergence, measured 9.3e-2 -> 2.4e-3
        self.assertGreater(errs[2], errs[5])
        self.assertGreater(errs[5], errs[20])
        self.assertGreater(errs[20], errs[60])
        self.assertLess(errs[60], 5e-3, errs)

    def test_a_uniform_rc_line_converges_to_the_analytic_input_divider(self):
        # r=1000 ohm/m, c=1 nF/m, 1 m, far end open, 50 ohm source: the distributed
        # answer is Z_in = Z0c*coth(gL) with Z0c = sqrt(r/(jw c)), g = sqrt(jw r c)
        rp, cp = 1000.0, 1e-9
        f = 1e6
        w = 2.0 * math.pi * f
        z0c = cmath.sqrt(rp / (1j * w * cp))
        zin = z0c / cmath.tanh(cmath.sqrt(1j * w * rp * cp) * 1.0)
        want = abs(zin / (50.0 + zin))
        errs = {}
        for lump in (2, 5, 20, 60):
            net = _urc(lump=lump, analysis=".ac lin 2 %g %g" % (f, 2 * f),
                       source="V1 1 0 AC 1")
            errs[lump] = abs(_nodes(net, analysis="ac")["2"]["mag"] - want)
        # 1/N convergence: measured 4.4e-2 -> 2.0e-2 -> 5.7e-3 -> 1.1e-3
        self.assertGreater(errs[2], errs[5], errs)
        self.assertGreater(errs[5], errs[20], errs)
        self.assertGreater(errs[20], errs[60], errs)
        self.assertLess(errs[60], 5e-3, errs)

class ACAgainstTheExactLine(unittest.TestCase):
    """With r=0 and g=0 the same physics is solved exactly by the T card."""

    def test_a_ladder_with_no_loss_matches_the_lossless_line(self):
        sweep = ".ac lin 3 100k 1meg"
        line = ("* t\nV1 1 0 AC 1\nR1 1 2 50\nT1 2 0 3 0 Z0=50 TD=5n\nR2 3 0 50\n"
                "%s\n.end\n" % sweep)
        lad = ("* o\nV1 1 0 AC 1\nR1 1 2 50\nO1 2 0 3 0 m\nR2 3 0 50\n"
               ".model m LTRA(r=0 l=250n g=0 c=100p len=1 lump=100)\n%s\n.end\n" % sweep)
        a = _ok(line, analysis="ac")["points"]
        b = _ok(lad, analysis="ac")["points"]
        for pa, pb in zip(a, b):
            for node in ("2", "3"):
                self.assertAlmostEqual(pa["nodes_v"][node]["mag"],
                                       pb["nodes_v"][node]["mag"], places=4,
                                       msg="%s at %g" % (node, pa["freq_hz"]))
                self.assertLess(abs(pa["nodes_v"][node]["phase_deg"]
                                    - pb["nodes_v"][node]["phase_deg"]), 0.02)

    def test_series_resistance_attenuates_by_alpha_over_the_length(self):
        # alpha = r/(2*Z0) = 1/(2*50) = 0.01 neper/m, so 1 % over 1 m, and the
        # near end reads the DC divider 50.5/101 for the same reason
        n = _nodes(_oline(lump=100, analysis=".ac lin 3 100k 1meg",
                          source="V1 1 0 AC 1"), analysis="ac")
        self.assertAlmostEqual(n["3"]["mag"], 0.5 * math.exp(-0.01), places=3)
        self.assertLess(n["3"]["mag"], 0.5)

class Transient(unittest.TestCase):
    """The ladder's wavefront: it arrives at TD and sharpens with the sections."""

    def _half(self, lump):
        net = _oline(lump=lump, source="V1 1 0 PULSE(0 3 0 0.05n)",
                     analysis=".tran 0.1n 20n")
        pts = _ok(net, analysis="tran")["points"]
        target = 0.5 * 3.0 * 50.0 / 101.0
        for p in pts:
            if p["nodes_v"]["3"] >= target:
                return p["t"], pts[-1]["nodes_v"]["3"]
        raise AssertionError("the far end never crossed half")

    def test_the_wavefront_arrives_at_the_delay_and_sharpens(self):
        t20, final20 = self._half(20)
        t5, _ = self._half(5)
        self.assertGreater(t5, t20)          # more sections, less smearing
        self.assertLess(abs(t20 - 5e-9), 0.3e-9)
        self.assertAlmostEqual(final20, 1.4851485, places=4)

    def test_before_the_delay_the_far_end_has_not_moved(self):
        net = _oline(lump=20, source="V1 1 0 PULSE(0 3 0 0.05n)",
                     analysis=".tran 0.1n 20n")
        pts = _ok(net, analysis="tran")["points"]
        early = [p["nodes_v"]["3"] for p in pts if p["t"] <= 2.5e-9]
        self.assertTrue(early)
        self.assertLess(max(abs(v) for v in early), 1e-3, early[-1])

    def test_an_rc_line_charges_slowly_and_settles_to_the_source(self):
        # r=100 ohm/m, c=1 nF/m: the far end lags the near end and both settle at
        # 1 V, because a capacitor blocks DC and the line has no DC drop
        net = ("* u\nV1 1 0 PULSE(0 1 0 1p)\nR1 1 2 50\nU1 2 3 0 m\n"
               ".model m URC(r=100 c=1n len=1)\n.tran 10n 2000n\n.end\n")
        pts = _ok(net, analysis="tran")["points"]
        near = [p for p in pts if abs(p["t"] - 200e-9) < 6e-9][0]["nodes_v"]
        self.assertLess(near["3"], near["2"])          # the far end lags
        self.assertGreater(near["2"], 0.5)
        self.assertAlmostEqual(pts[-1]["nodes_v"]["3"], 1.0, places=3)

class Refusals(unittest.TestCase):
    """A card that cannot be modelled is refused by name, never approximated."""

    def test_the_model_must_be_named(self):
        r = _err("* o\nV1 1 0 3\nR1 1 2 50\nO1 2 0 3 0\nR2 3 0 50\n.op\n.end\n")
        self.assertEqual(r["reason_code"], "NETLIST_INVALID")
        self.assertIn("needs four nodes and a model", r["message"])

    def test_the_model_must_be_defined(self):
        r = _err("* o\nV1 1 0 3\nO1 2 0 3 0 nosuch\n.op\n.end\n")
        self.assertIn("not defined", r["message"])

    def test_the_model_must_be_of_the_right_type(self):
        r = _err("* o\nV1 1 0 3\nR1 1 2 50\nO1 2 0 3 0 m\nR2 3 0 50\n"
                 ".model m D(IS=1e-14)\n.op\n.end\n")
        self.assertIn("needs an LTRA model", r["message"])
        r = _err("* u\nV1 1 0 3\nU1 2 3 0 m\nR2 3 0 50\n"
                 ".model m LTRA(r=1 l=250n g=0 c=100p len=1)\n.op\n.end\n")
        self.assertIn("needs a URC model", r["message"])

    def test_z0_and_td_are_refused_with_a_pointer_at_the_t_card(self):
        r = _err("* o\nV1 1 0 3\nR1 1 2 50\nO1 2 0 3 0 m\nR2 3 0 50\n"
                 ".model m LTRA(Z0=50 TD=1n)\n.op\n.end\n")
        self.assertIn("refused by name", r["message"])
        self.assertIn("T card", r["message"])

    def test_a_model_without_a_length_is_refused(self):
        # defaulting the length to 1 would rescale every total silently
        r = _err("* o\nV1 1 0 3\nR1 1 2 50\nO1 2 0 3 0 m\nR2 3 0 50\n"
                 ".model m LTRA(r=1 l=250n g=0 c=100p)\n.op\n.end\n")
        self.assertIn("no LEN=", r["message"])

    def test_a_zero_length_is_refused(self):
        r = _err("* o\nV1 1 0 3\nR1 1 2 50\nO1 2 0 3 0 m\nR2 3 0 50\n"
                 ".model m LTRA(r=1 l=250n g=0 c=100p len=0)\n.op\n.end\n")
        self.assertIn("length must be positive", r["message"])

    def test_a_model_with_no_parameters_is_refused(self):
        r = _err("* o\nV1 1 0 3\nR1 1 2 50\nO1 2 0 3 0 m\nR2 3 0 50\n"
                 ".model m LTRA(len=1)\n.op\n.end\n")
        self.assertIn("chain of nothing", r["message"])

    def test_an_unknown_model_parameter_is_refused_by_name(self):
        r = _err("* o\nV1 1 0 3\nR1 1 2 50\nO1 2 0 3 0 m\nR2 3 0 50\n"
                 ".model m LTRA(r=1 l=250n g=0 c=100p len=1 FOO=2)\n.op\n.end\n")
        self.assertIn("FOO=", r["message"])
        self.assertIn("not a parameter", r["message"])

    def test_a_negative_per_length_value_is_refused(self):
        r = _err("* o\nV1 1 0 3\nR1 1 2 50\nO1 2 0 3 0 m\nR2 3 0 50\n"
                 ".model m LTRA(r=-1 l=250n g=0 c=100p len=1)\n.op\n.end\n")
        self.assertIn("cannot be negative", r["message"])

    def test_lump_on_the_card_is_refused_with_the_reason(self):
        # measured: an unknown token on an O card is fatal to ngspice, while an
        # unknown .model parameter is only a warning - so lump goes in the model
        r = _err("* o\nV1 1 0 3\nR1 1 2 50\nO1 2 0 3 0 m lump=5\nR2 3 0 50\n"
                 ".model m LTRA(r=1 l=250n g=0 c=100p len=1)\n.op\n.end\n")
        self.assertIn("lump", r["message"])
        self.assertIn("FATAL to ngspice", r["message"])

    def test_a_zero_or_fractional_section_count_is_refused(self):
        for bad in ("0", "2.5"):
            r = _err("* o\nV1 1 0 3\nR1 1 2 50\nO1 2 0 3 0 m\nR2 3 0 50\n"
                     ".model m LTRA(r=1 l=250n g=0 c=100p len=1 lump=%s)\n.op\n.end\n"
                     % bad)
            self.assertIn("whole number of at least 1", r["message"], bad)

    def test_two_different_return_nodes_are_refused(self):
        # the ladder treats the return as ONE ideal node; with two, the return
        # would be a second conductor with its own delay
        r = _err("* o\nV1 1 0 3\nR1 1 2 50\nO1 2 5 3 6 m\nR2 3 0 50\nR3 5 0 1m\n"
                 ".model m LTRA(r=1 l=250n g=0 c=100p len=1)\n.op\n.end\n")
        self.assertIn("two different return nodes", r["message"])

    def test_a_shorted_port_is_refused(self):
        r = _err("* o\nV1 1 0 3\nR1 1 2 50\nO1 2 2 3 0 m\nR2 3 0 50\n"
                 ".model m LTRA(r=1 l=250n g=0 c=100p len=1)\n.op\n.end\n")
        self.assertIn("port 1 shorted", r["message"])

    def test_a_line_that_is_its_own_load_is_refused(self):
        r = _err("* o\nV1 1 0 3\nR1 1 2 50\nO1 2 0 2 0 m\nR2 2 0 50\n"
                 ".model m LTRA(r=1 l=250n g=0 c=100p len=1)\n.op\n.end\n")
        self.assertIn("connects a port to itself", r["message"])

    def test_a_urc_needs_both_a_resistance_and_a_capacitance(self):
        r = _err("* u\nV1 1 0 3\nU1 2 3 0 m\nR2 3 0 50\n"
                 ".model m URC(c=1n len=1)\n.op\n.end\n")
        self.assertIn("no R=", r["message"])
        r = _err("* u\nV1 1 0 3\nU1 2 3 0 m\nR2 3 0 50\n"
                 ".model m URC(r=1000 len=1)\n.op\n.end\n")
        self.assertIn("no C=", r["message"])

    def test_a_urc_refuses_the_nonlinear_parameters_by_name(self):
        r = _err("* u\nV1 1 0 3\nU1 2 3 0 m\nR2 3 0 50\n"
                 ".model m URC(r=1000 c=1n len=1 FMAX=1g)\n.op\n.end\n")
        self.assertIn("FMAX=", r["message"])
        self.assertIn("refused by name", r["message"])

    def test_the_same_parameter_under_two_names_is_refused(self):
        r = _err("* u\nV1 1 0 3\nU1 2 3 0 m\nR2 3 0 50\n"
                 ".model m URC(r=1000 rperl=1000 c=1n len=1)\n.op\n.end\n")
        self.assertIn("under two names", r["message"])

    def test_a_ladder_that_cannot_fit_is_refused_with_the_lump_hint(self):
        # 200 sections is 603 unknowns: refused, and the hint names the knob
        r = _err(_oline(lump=200))
        self.assertEqual(r["reason_code"], "TOO_LARGE")
        self.assertIn("lump=", r["hint"])

class RealEngineAgrees(unittest.TestCase):
    """The ladder against ngspice's own distributed LTRA, when installed."""

    def setUp(self):
        self.argv = spice.find_ngspice()

    def _cross(self, net, analysis="op", tol=None):
        if not self.argv:
            self.skipTest("no ngspice on this machine")
        r = spice.simulate(net, analysis=analysis, engine="ngspice",
                           cross_check=True)
        assert r["status"] == "ok", r
        return r["cross_check"]

    def test_a_lossy_line_in_a_dc_point(self):
        cc = self._cross(_oline())
        self.assertGreaterEqual(cc["compared_nodes"], 3)
        self.assertLess(cc["max_abs_diff_v"], 1e-6, cc)

    def test_a_lossy_line_in_ac(self):
        # 1 % of attenuation per metre: ngspice solves the distributed line
        # exactly, this module solves a 100-section ladder, and they agree to
        # 1.6e-7 V (measured) at 100 kHz
        cc = self._cross(_oline(lump=100, analysis=".ac lin 3 100k 1meg",
                                source="V1 1 0 AC 1"), analysis="ac")
        self.assertGreaterEqual(cc["compared_nodes"], 3)
        self.assertLess(cc["max_abs_diff_v"], 1e-5, cc)

if __name__ == "__main__":
    unittest.main()
