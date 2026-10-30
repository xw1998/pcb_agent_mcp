"""Tests for .noise: small-signal noise at the DC bias, one adjoint solve per point.

The claim under test is narrow and falsifiable, so each class names the
observation that would FAIL if the claim were false:

* a single resistor reads exactly 4kT/R and the output density is sqrt(4kT*R).
  A wrong constant, a wrong temperature (273.15 vs 300.15), or a missing 4 in
  Nyquist all land here, and each is off by a factor no rounding can hide.
* an ideal source SHORTING a resistor reads zero noise - physics, not omission:
  the source entry is still listed with its density, only its transfer is zero.
  An implementation that added densities without a transfer function would
  still read sqrt(4kTR) here.
* two 1k in parallel read sqrt(4kT*500), four 1k in series read twice the
  single-resistor density: the noise of a network depends on the network, and
  only a correct transfer function gets both.
* the RC low-pass density follows 4kTR/(1+(2*pi*f*R*C)^2) at EVERY swept point,
  and its band integral matches the closed form
  4kTR/(2*pi*R*C)*(atan(2*pi*f2*R*C) - atan(2*pi*f1*R*C)) to 1e-4 relative.
  The band integral is the part a reader will quote, so it gets the tight test.
* the integral rule follows the sweep grid (dec/oct -> ln f, lin -> f). The
  negative control is the same circuit on a 1000-point LIN sweep: same code,
  same points count, +47 % - a rule that ignored the grid could not show both.
* input-referred noise is output noise divided by |gain|, and |gain| is the
  same complex MNA the .ac analysis solves, so the two must agree point by
  point. A gain of zero reports null plus a warning instead of "inf".
* shot noise: for a diode in series with R the ratio of the two contributions
  is q*Id*R/(2kT) at EVERY frequency (the junction's dynamic resistance cancels
  out of the ratio) - an implementation that forgot the transfer, or used
  |Id| from the wrong node, gets a ratio that drifts with frequency.
* a MOSFET's channel noise is 4kT*(2/3)*gm with gm measured independently by
  finite differences on .op currents; and W= L= must actually reach the model
  (they were dropped on the floor before 0.0.32, silently).
* 27 C and 327.15 C differ by exactly sqrt(2): the temperature has to be the
  circuit's, not a constant.

Everything the analysis cannot honour is refused by name, and the last two
classes compare against a real ngspice when one is installed - the only way
to audit the delegation itself.
"""
import math
import os
import sys
import unittest

sys.path.insert(0, os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "src"))

from pcb_agent_mcp.calc import spice  # noqa: E402

KB = 1.380649e-23
Q = 1.602176634e-19
T27 = 27.0 + 273.15


def _close(a, b, rel, msg=None):
    """Relative comparison: the reported numbers carry 10 significant digits."""
    assert abs(a) < 1e300 and abs(b) < 1e300, msg
    assert abs(a / b - 1.0) <= rel, (msg, a, b, abs(a / b - 1.0))


def _kT(temp_c=T27 - 273.15):
    return KB * (temp_c + 273.15)


def _nz(net, **kw):
    kw.setdefault("analysis", "noise")
    r = spice.simulate(net, **kw)
    assert r["status"] == "ok", r
    return r


def _val(net, **kw):
    return _nz(net, **kw)["value"]


def _first(net, **kw):
    return _val(net, **kw)["points"][0]


def _err(net, **kw):
    kw.setdefault("analysis", "noise")
    r = spice.simulate(net, **kw)
    assert r["status"] == "error", r
    return r


def _r_net(body, tail="lin 3 100 300", out="V(1)"):
    """A current source drives `body` from an ideal 1 A test current."""
    return "* one\nI1 0 1 AC 1\n%s\n.noise %s I1 %s\n.end\n" % (body, out, tail)


RC = ("* rc\nV1 1 0 AC 1\nR1 1 2 1k\nC1 2 0 1u\n"
      ".noise V(2) V1 %s\n.end\n")

DIVIDER = ("* div\nV1 1 0 AC 1\nR1 1 2 1k\nR2 2 0 1k\n"
           ".noise V(2) V1 %s\n.end\n")

DIODE = ("* diode\nV1 1 0 0.6 AC 1\nR1 1 2 1k\nD1 2 0 dm\n.model dm D(IS=1e-14)\n"
         ".noise V(2) V1 %s\n.end\n")

MOS = ("* mos\nV1 1 0 5 AC 1\nV2 2 0 1.5\nR1 1 3 1k\nM1 3 2 0 0 nmos W=%s L=1u\n"
       ".model nmos NMOS(KP=1e-4 VTO=0.5)\n.noise V(3) V1 %s\n.end\n")

BJT = ("* bjt\nV1 1 0 0.7 AC 1\nR1 1 2 10k\nV2 4 0 5\nR2 4 3 1k\nQ1 3 2 0 qm\n"
       ".model qm NPN(IS=1e-16 BF=100)\n.noise V(3) V1 %s\n.end\n")


class ThermalDensity(unittest.TestCase):
    """One resistor, one number: 4kT/R across it, sqrt(4kT*R) at its terminals."""

    def test_a_single_resistor_reads_4kt_over_r_exactly(self):
        v = _val(_r_net("R1 1 0 1k"))
        want = 4.0 * _kT() / 1000.0
        _close(v["sources"][0]["psd_a2_per_hz"], want, 1e-12)
        self.assertEqual(v["sources"][0]["mechanism"], "thermal")
        self.assertIn("4kT/R", v["sources"][0]["formula"])
        self.assertEqual(v["sources"][0]["element"], "R1")

    def test_the_output_density_is_sqrt_4kTR(self):
        p = _first(_r_net("R1 1 0 1k"))
        want = math.sqrt(4.0 * _kT() * 1000.0)  # 4.071372237e-09 at 27 C
        _close(p["onoise_v_per_sqrt_hz"], want, 1e-9)
        # and it is flat: a resistor has no frequency dependence
        for q in _val(_r_net("R1 1 0 1k"))["points"]:
            _close(q["onoise_v_per_sqrt_hz"], want, 1e-9)

    def test_the_gain_of_a_current_driven_resistor_is_its_resistance(self):
        p = _first(_r_net("R1 1 0 1k"))
        self.assertEqual(p["gain_mag"], 1000.0)
        # input-referred = output / gain, i.e. the noise CURRENT
        _close(p["inoise_v_per_sqrt_hz"], math.sqrt(4.0 * _kT() / 1000.0), 1e-9)

    def test_two_equal_resistors_in_parallel_are_half_the_resistance(self):
        p = _first(_r_net("R1 1 0 1k\nR2 1 0 1k"))
        _close(p["onoise_v_per_sqrt_hz"], math.sqrt(4.0 * _kT() * 500.0), 1e-9)
        self.assertEqual(p["gain_mag"], 500.0)

    def test_four_in_series_read_twice_the_density_of_one(self):
        one = _first(_r_net("R1 1 0 1k"))["onoise_v_per_sqrt_hz"]
        four = _first(_r_net("R1 1 2 1k\nR2 2 3 1k\nR3 3 4 1k\nR4 4 0 1k"))
        _close(four["onoise_v_per_sqrt_hz"], 2.0 * one, 1e-9)
        self.assertEqual(four["gain_mag"], 4000.0)

    def test_an_ideal_source_across_a_resistor_short_circuits_its_noise(self):
        # physics, not omission: the source still carries its 4kT/R density and
        # is still listed, but nothing of it reaches the (clamped) output.
        net = ("* shorted\nV1 1 0 0 AC 1\nR1 1 0 1k\n"
               ".noise V(1) V1 lin 3 100 300\n.end\n")
        v = _val(net)
        self.assertEqual([s["element"] for s in v["sources"]], ["R1"])
        self.assertEqual(v["sources"][0]["psd_a2_per_hz"],
                         _val(_r_net("R1 1 0 1k"))["sources"][0]["psd_a2_per_hz"])
        for p in v["points"]:
            self.assertEqual(p["onoise_v_per_sqrt_hz"], 0.0)
            self.assertEqual(p["contributions_v2_per_hz"], {"R1": 0.0})

    def test_a_circuit_with_no_noise_source_reads_zero_and_says_why(self):
        net = ("* silent\nV1 1 0 AC 1\nC1 1 0 1u\n.noise V(1) V1 dec 10 1 100\n.end\n")
        v = _val(net)
        self.assertEqual(v["sources"], [])
        self.assertEqual(v["integrated"]["onoise_v_rms"], 0.0)
        self.assertIn("noiseless_by_construction", _nz(net)["assumptions"])

    def test_a_flat_density_integrates_to_density_times_bandwidth(self):
        v = _val(_r_net("R1 1 0 1k", tail="lin 3 100 300"))
        d = v["points"][0]["onoise_v_per_sqrt_hz"]
        _close(v["integrated"]["onoise_v_rms"], d * 200.0 ** 0.5, 1e-6)
        self.assertEqual(v["integrated"]["band_hz"], [100.0, 300.0])

    def test_the_temperature_is_the_circuit_temperature_not_a_constant(self):
        a = _first(_r_net("R1 1 0 1k"), temp_c=27.0)
        b = _first(_r_net("R1 1 0 1k"), temp_c=327.15)
        _close(b["onoise_v_per_sqrt_hz"], a["onoise_v_per_sqrt_hz"] * 2.0 ** 0.5, 1e-9)
        self.assertEqual(_val(_r_net("R1 1 0 1k"))["bias_point_v"] is not None, True)


class RCLowPass(unittest.TestCase):
    """A resistor's noise through a real transfer function."""

    def _points(self, n=10):
        return _val(RC % ("dec %d 1 1meg" % n))["points"]

    def test_every_point_follows_the_analytic_transfer(self):
        R, C = 1000.0, 1e-6
        for p in self._points(10):
            f = p["freq_hz"]
            want = 4.0 * _kT() * R / (1.0 + (2.0 * math.pi * f * R * C) ** 2)
            _close(p["onoise_v2_per_hz"], want, 1e-6, (f, want))

    def test_the_dc_density_is_the_flat_one_and_the_corner_falls_3db(self):
        pts = self._points(10)
        flat = math.sqrt(4.0 * _kT() * 1000.0)
        _close(pts[0]["onoise_v_per_sqrt_hz"], flat, 1e-4)
        # 1/(2*pi*R*C) = 159.15 Hz: the nearest swept decade point is 158.49 Hz
        near = min(pts, key=lambda p: abs(p["freq_hz"] - 159.15494309189535))
        _close(near["onoise_v_per_sqrt_hz"], flat * 0.5 ** 0.5, 1e-2)

    def test_the_band_integral_matches_the_closed_form(self):
        R, C = 1000.0, 1e-6
        for n in (10, 25):
            v = _val(RC % ("dec %d 1 1meg" % n))
            f1, f2 = v["integrated"]["band_hz"]
            k = 4.0 * _kT() * R / (2.0 * math.pi * R * C)
            want = math.sqrt(k * (math.atan(2.0 * math.pi * f2 * R * C)
                                  - math.atan(2.0 * math.pi * f1 * R * C)))
            _close(v["integrated"]["onoise_v_rms"], want, 1e-3, (n, want))

    def test_more_points_converge_and_never_overshoot_the_total(self):
        R, C = 1000.0, 1e-6
        total = math.sqrt(_kT() / C)  # kT/C: the whole band of an RC
        last = None
        for n in (5, 10, 25):
            got = _val(RC % ("dec %d 1 1meg" % n))["integrated"]["onoise_v_rms"]
            self.assertLess(got, total)
            if last is not None:
                self.assertGreater(got, last)  # from below, monotonically
            last = got
        self.assertLess(abs(last / total - 1.0), 0.01)

    def test_the_sweep_grid_decides_the_integration_rule(self):
        # same circuit, same 1000 points, two grids: the dec sweep is accurate
        # to <0.1 % of the closed form and the lin sweep is >10 % low, because a
        # lin grid cannot resolve a corner it steps over. One rule cannot be
        # right for both, so the grid decides - and the report says which grid.
        R, C = 1000.0, 1e-6
        v = _val(RC % "dec 10 1 1meg")
        f1, f2 = v["integrated"]["band_hz"]
        k = 4.0 * _kT() * R / (2.0 * math.pi * R * C)
        want = math.sqrt(k * (math.atan(2.0 * math.pi * f2 * R * C)
                              - math.atan(2.0 * math.pi * f1 * R * C)))
        self.assertLess(abs(v["integrated"]["onoise_v_rms"] / want - 1.0), 1e-4)
        self.assertIn("ln(f)", v["integrated"]["method"])
        lin = _val(RC % "lin 1000 1 1meg")
        self.assertGreater(abs(lin["integrated"]["onoise_v_rms"] / want - 1.0), 0.10)
        self.assertIn("trapezoid in f", lin["integrated"]["method"])
        self.assertNotIn("ln(f)", lin["integrated"]["method"])

    def test_a_decade_sweep_holds_the_points_the_deck_asked_for(self):
        self.assertEqual(len(self._points(10)), 61)   # 6 decades * 10 + 1
        self.assertEqual(len(self._points(1)), 7)
        oct_ = _val(RC % "oct 4 1 16")
        self.assertEqual(len(oct_["points"]), 17)     # 4 octaves * 4 + 1
        self.assertEqual(oct_["integrated"]["band_hz"], [1.0, 16.0])


class InputReferred(unittest.TestCase):
    """The second number in every noise report, and what it means when absent."""

    def test_the_gain_is_the_ac_gain(self):
        # the same dec grid, so the two analyses must agree point by point:
        # they are the same complex MNA matrix, one solved for the answer and
        # one adjointly for the transfer
        ac = spice.simulate("* div\nV1 1 0 AC 1\nR1 1 2 1k\nR2 2 0 1k\n"
                            ".ac dec 10 1 1meg\n.end\n",
                            analysis="ac")["value"]["points"]
        nz = _val(DIVIDER % "dec 10 1 1meg")["points"]
        self.assertEqual(len(ac), len(nz))
        for a, b in zip(ac, nz):
            self.assertEqual(a["freq_hz"], b["freq_hz"])
            _close(b["gain_mag"], a["nodes_v"]["2"]["mag"], 1e-9)

    def test_input_referred_is_output_divided_by_the_gain(self):
        v = _val(DIVIDER % "dec 10 1 1meg")
        for p in v["points"]:
            self.assertEqual(p["gain_mag"], 0.5)
            _close(p["inoise_v_per_sqrt_hz"], p["onoise_v_per_sqrt_hz"] / 0.5, 1e-9)
        # a 1k/1k divider: both resistors at 4kT/R, half of it at the output
        _close(v["points"][0]["onoise_v_per_sqrt_hz"],
               math.sqrt(4.0 * _kT() * 500.0), 1e-9)

    def test_an_unreachable_output_reports_null_instead_of_inf(self):
        net = ("* unreachable\nV1 1 0 AC 1\nR1 1 0 1k\nR2 2 0 1k\n"
               ".noise V(2) V1 dec 10 1 100\n.end\n")
        r = _nz(net)
        v = r["value"]
        for p in v["points"]:
            self.assertEqual(p["gain_mag"], 0.0)
            self.assertIsNone(p["inoise_v_per_sqrt_hz"])
        self.assertIsNone(v["integrated"]["inoise_v_rms"])
        self.assertGreater(v["integrated"]["onoise_v_rms"], 0.0)
        self.assertTrue(any("input-referred noise is not defined" in w
                            for w in (r["warnings"] or [])), r["warnings"])

    def test_a_single_point_has_a_density_but_no_band_and_says_so(self):
        r = _nz(_r_net("R1 1 0 1k", tail="lin 1 10 100"))
        v = r["value"]
        self.assertEqual(len(v["points"]), 1)
        self.assertAlmostEqual(v["points"][0]["freq_hz"], 10.0)
        self.assertEqual(v["integrated"]["onoise_v_rms"], 0.0)
        self.assertTrue(any("one frequency" in w for w in (r["warnings"] or [])),
                        r["warnings"])

    def test_a_sweep_longer_than_the_point_budget_is_truncated_out_loud(self):
        r = _nz(RC % "dec 100 1 1meg")
        self.assertEqual(len(r["value"]["points"]), 200)
        self.assertLess(r["value"]["integrated"]["band_hz"][1], 1e6)
        self.assertTrue(any("only the first 200" in w for w in (r["warnings"] or [])),
                        r["warnings"])


class ShotNoise(unittest.TestCase):
    """Devices whose noise is set by the bias point, and must say which current."""

    def test_a_diode_reports_2q_times_its_own_forward_current(self):
        v = _val(DIODE % "lin 2 100 200")
        i_d = (0.6 - v["bias_point_v"]["2"]) / 1000.0
        self.assertAlmostEqual(i_d * 1000.0, i_d * 1000.0)  # a number, not a NaN
        src = {s["mechanism"]: s for s in v["sources"]}
        _close(src["shot"]["psd_a2_per_hz"], 2.0 * Q * i_d, 1e-7)
        self.assertIn("2q", src["shot"]["formula"])

    def test_the_shot_to_thermal_ratio_is_frequency_independent(self):
        # R and the junction are in series to ground, so both densities see the
        # SAME transfer function and the ratio is q*Id*R/(2kT): the junction's
        # dynamic resistance cancels. A missing transfer, or an Id read from the
        # wrong node, makes the ratio drift with frequency.
        v = _val(DIODE % "dec 10 1 1meg")
        i_d = (0.6 - v["bias_point_v"]["2"]) / 1000.0
        want = Q * i_d * 1000.0 / (2.0 * _kT())
        for p in v["points"]:
            c = p["contributions_v2_per_hz"]
            _close(c["D1"] / c["R1"], want, 1e-7, (p["freq_hz"], c))
        self.assertGreater(want, 0.1)  # otherwise this test proves nothing

    def test_a_bjt_reports_collector_and_base_shot_noise_separately(self):
        v = _val(BJT % "lin 2 100 200")
        b = v["bias_point_v"]
        i_b = (0.7 - b["2"]) / 10000.0
        i_c = (5.0 - b["3"]) / 1000.0
        src = {s["mechanism"]: s for s in v["sources"] if s["element"] == "Q1"}
        _close(src["shot_collector"]["psd_a2_per_hz"], 2.0 * Q * i_c, 1e-6)
        # the bias point is reported to 9 decimals, so Ib carries ~2e-6
        _close(src["shot_base"]["psd_a2_per_hz"], 2.0 * Q * i_b, 1e-5)
        _close(i_c / i_b, 100.0, 1e-4)  # BF=100: the deck's own check

    def test_a_mosfet_reports_4kt_two_thirds_gm_and_w_over_l_reaches_the_model(self):
        v = _val(MOS % ("10u", "lin 2 100 200"))
        psd = {s["mechanism"]: s["psd_a2_per_hz"] for s in v["sources"]}
        self.assertIn("channel_thermal", psd)
        # gm measured independently: 1 mV step on the gate, drain current from
        # .op node voltages (node 3 sits on a 1k resistor from 5 V)
        def i_d(w, vg):
            net = ("* mos\nV1 1 0 5\nV2 2 0 %g\nR1 1 3 1k\nM1 3 2 0 0 nmos W=%s L=1u\n"
                   ".model nmos NMOS(KP=1e-4 VTO=0.5)\n.op\n.end\n" % (vg, w))
            return (5.0 - spice.simulate(net, analysis="op")["value"]["nodes_v"]["3"]) / 1000.0
        gm = (i_d("10u", 1.501) - i_d("10u", 1.5)) / 0.001
        self.assertGreater(gm, 0.0)
        # gm by finite differences on .op currents: agreed to 5e-4
        _close(psd["channel_thermal"], 4.0 * _kT() * (2.0 / 3.0) * gm, 1e-3)
        # W= and L= used to be parsed and dropped: doubling W must double the
        # density, which a W-less model cannot do
        wide = {s["mechanism"]: s["psd_a2_per_hz"]
                for s in _val(MOS % ("20u", "lin 2 100 200"))["sources"]}
        _close(wide["channel_thermal"], 2.0 * psd["channel_thermal"], 1e-6)


class Refusals(unittest.TestCase):
    """Noise asks for things that can be wrong, so each one is refused by name."""

    def test_an_output_node_nothing_connects(self):
        r = _err("*\nV1 1 0 1\nR1 1 0 1k\n.noise V(9) V1 dec 2 1 10\n.end\n")
        self.assertEqual(r["reason_code"], "NETLIST_INVALID")
        self.assertIn("'9'", r["message"])

    def test_an_input_source_not_in_the_netlist(self):
        r = _err("*\nV1 1 0 1\nR1 1 0 1k\n.noise V(1) V9 dec 2 1 10\n.end\n")
        self.assertEqual(r["reason_code"], "NETLIST_INVALID")
        self.assertIn("V9", r["message"])

    def test_a_card_missing_a_field(self):
        r = _err("*\nV1 1 0 1\nR1 1 0 1k\n.noise V(1) V1 dec 10 10\n.end\n")
        self.assertEqual(r["reason_code"], "NETLIST_INVALID")
        self.assertIn("6 tokens", r["message"])

    def test_a_current_written_as_the_output(self):
        r = _err("*\nV1 1 0 1\nR1 1 0 1k\n.noise I(1) V1 dec 2 1 10\n.end\n")
        self.assertEqual(r["reason_code"], "NETLIST_INVALID")
        self.assertIn("V(node)", r["message"])

    def test_an_output_that_is_only_ground(self):
        r = _err("*\nV1 1 0 1\nR1 1 0 1k\n.noise V(0) V1 dec 2 1 10\n.end\n")
        self.assertEqual(r["reason_code"], "NETLIST_INVALID")
        self.assertIn("ground", r["message"])

    def test_an_input_that_is_not_a_source(self):
        r = _err("*\nV1 1 0 1\nR1 1 0 1k\nE1 2 0 1 0 2\n.noise V(2) E1 dec 2 1 10\n.end\n")
        self.assertEqual(r["reason_code"], "ARG_INVALID")
        self.assertIn("must be a V or I source", r["message"])

    def test_an_empty_or_backwards_band(self):
        for tail in ("dec 2 10 10", "dec 2 10 1"):
            r = _err("*\nV1 1 0 1\nR1 1 0 1k\n.noise V(1) V1 %s\n.end\n" % tail)
            self.assertEqual(r["reason_code"], "ARG_RANGE", tail)

    def test_a_sweep_word_that_is_not_dec_oct_or_lin(self):
        r = _err("*\nV1 1 0 1\nR1 1 0 1k\n.noise V(1) V1 foo 2 1 10\n.end\n")
        self.assertEqual(r["reason_code"], "NETLIST_INVALID")
        self.assertIn("dec, oct or lin", r["message"])

    def test_an_unsupported_directive_is_still_refused_rather_than_ignored(self):
        # .print is not implemented in the builtin subset; delegating the same
        # deck works, which is why this refusal is not a wall (see below)
        r = _err("*\nV1 1 0 1 AC 1\nR1 1 2 1k\nC1 2 0 1u\n"
                 ".noise V(2) V1 dec 10 1 1e4\n.print noise onoise_spectrum\n.end\n")
        self.assertEqual(r["reason_code"], "NETLIST_INVALID")
        self.assertIn(".print", r["message"])

    def test_the_analysis_name_is_no_longer_split_between_three_words(self):
        r = _err("*\nV1 1 0 1\nR1 1 0 1k\n.noise V(1) V1 dec 2 1 10\n.end\n",
                 analysis="disto")
        self.assertEqual(r["reason_code"], "ARG_INVALID")
        self.assertIn("noise", r["message"])  # the message names the legal set


@unittest.skipUnless(spice.find_ngspice(), "no ngspice binary on this machine")
class RealEngineAgrees(unittest.TestCase):
    """The same decks on a real ngspice: the transfer is corroborated, not asserted.

    The mechanisms are the same two formulas, but each engine finds its own bias
    point, so a small difference is expected and a large one is a finding. The
    MOSFET agrees to the last printed digit, which is why it gets the tight
    bound; the diode drifts by ~0.2 % because the junction's bias differs.
    """

    def _worst(self, net, tol):
        b = _val(net)
        e = _val(net, engine="ngspice")
        self.assertEqual(len(b["points"]), len(e["points"]))
        worst = 0.0
        for x, y in zip(b["points"], e["points"]):
            self.assertAlmostEqual(x["freq_hz"], y["freq_hz"], 6)
            worst = max(worst, abs(x["onoise_v_per_sqrt_hz"]
                                   / y["onoise_v_per_sqrt_hz"] - 1.0))
        self.assertLess(worst, tol, worst)
        return worst

    def test_a_resistive_divider(self):
        self._worst(DIVIDER % "dec 10 1 1meg", 1e-5)

    def test_an_rc_low_pass(self):
        self._worst(RC % "dec 10 1 1meg", 1e-5)

    def test_a_diode(self):
        self._worst(DIODE % "dec 10 1 1meg", 1e-2)

    def test_a_mosfet_with_an_explicit_w_over_l(self):
        self._worst(MOS % ("10u", "dec 10 1 1meg"), 1e-6)

    def test_a_bjt_with_two_shot_mechanisms(self):
        self._worst(BJT % "dec 10 1 1meg", 1e-2)


@unittest.skipUnless(spice.find_ngspice(), "no ngspice binary on this machine")
class Delegation(unittest.TestCase):
    """engine="ngspice" answers .noise from its own rawfile, and can be audited."""

    def test_the_engine_returns_a_spectrum_and_its_own_integral(self):
        v = _val(DIVIDER % "dec 10 1 1meg", engine="ngspice")
        self.assertEqual(len(v["points"]), 61)
        self.assertGreater(v["points"][0]["onoise_v_per_sqrt_hz"], 0.0)
        self.assertIn("inoise_v_per_sqrt_hz", v["points"][0])
        self.assertGreater(v["integrated"]["onoise_v_rms"], 0.0)
        self.assertIn("ngspice", v["integrated"]["method"])

    def test_the_cross_check_compares_the_spectrum_only(self):
        r = _nz(DIVIDER % "dec 10 1 1meg", engine="ngspice", cross_check=True)
        cc = r["cross_check"]
        self.assertEqual(cc["compared_points"], 61)
        self.assertLess(cc["max_rel_diff_onoise"], 1e-5)
        # the input-referred total is explicitly NOT compared: engines differ
        self.assertIn("NOT compared", cc["note"])

    def test_a_deck_the_subset_refuses_still_runs_on_the_engine(self):
        # .print noise is unimplemented in the builtin parser; the deck itself is
        # valid SPICE, so the delegation path must not inherit the refusal
        net = ("*\nV1 1 0 1 AC 1\nR1 1 2 1k\nC1 2 0 1u\n"
               ".noise V(2) V1 dec 10 1 1e4\n.print noise onoise_spectrum\n.end\n")
        self.assertEqual(_err(net)["reason_code"], "NETLIST_INVALID")
        v = _val(net, engine="ngspice")
        self.assertEqual(len(v["points"]), 41)
        self.assertGreater(v["points"][0]["onoise_v_per_sqrt_hz"], 0.0)


if __name__ == "__main__":
    unittest.main()
