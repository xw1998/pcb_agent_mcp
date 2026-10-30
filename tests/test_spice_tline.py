"""Tests for the lossless transmission line (T card): DC is a wire, AC is a
phase shift, .tran is a delayed copy of the far end.

The claim is narrow: one ideal, lossless, constant-Z0 line, solved so that every
number it reports is the number the ideal model has. Each mechanism carries a
control that FAILS when the mechanism is absent:

* DC                      -> a 3 V source behind 50 ohm into a matched 50 ohm
                             load reads 1.5 V at BOTH ports and +/-30 mA through
                             the card. Drop the card and the far node reads 0 V,
                             which is the control for "the line is what connects
                             them".
* .tran matched step      -> the source side sits at 1.5 V from the first step
                             while the far end is 0 V until TD and 1.5 V after
                             it. A line implemented as a plain wire would put
                             1.5 V on the far end at t = 0.
* .tran open end          -> the far end doubles to 3.0 V at TD and the source
                             side doubles at 2*TD (the reflection returning). A
                             line with no reflection model would read 1.5 V.
* .ac                     -> |V(far)| = 0.5 at every frequency and the phase is
                             exactly -omega*TD. A line modelled as a wire would
                             read phase 0.
* F=/NL=                  -> the same delay as TD=, point for point.
* PULSE with no width     -> SPICE's default width is tstop; with the old
                             zero-width default the source collapsed after the
                             first step and the far end never reached 1.5 V.

Everything the card cannot honour is refused by name rather than approximated:
a missing Z0, a missing or zero TD, TD shorter than the timestep (the far end
would have to read a time that does not exist yet), IC=/REL=/ABS=, and the
half-wave AC point where an ideal lossless line's admittance is infinite.
"""
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
    return _ok(net, analysis)["nodes_v"]

def _err(net, analysis="op"):
    r = spice.simulate(net, analysis=analysis)
    assert r["status"] == "error", r
    return r

def _line(name="T1", nodes="2 0 3 0", params="Z0=50 TD=1n", load="R2 3 0 50"):
    """A 3 V source behind 50 ohm, one line, and a load on the far port."""
    return ("* t\nV1 1 0 dc 3\nR1 1 2 50\n%s %s %s\n%s\n.op\n.end\n"
            % (name, nodes, params, load))

def _step(params="Z0=50 TD=1n", load="R2 3 0 50", tran=".tran 0.1n 5n"):
    """The same deck, driven by a 3 V step, so the delay is an observable."""
    return ("* t\nV1 1 0 PULSE(0 3 0 1p)\nR1 1 2 50\nT1 2 0 3 0 %s\n%s\n%s\n.end\n"
            % (params, load, tran))

def _at(points, tq, node):
    """The node voltage at the last point at or before tq.

    The solver's printed times are rounded, so this compares with a tolerance
    (~1e-15 s, i.e. a millionth of a step here) instead of by equality.
    """
    best = None
    for p in points:
        if p["t"] <= tq + 1e-15:
            best = p
        else:
            break
    assert best is not None, (tq, points[:3])
    return best["nodes_v"][node]

def _tline(value):
    for el in value["elements" if "elements" in value else "elements_final"]:
        if el["name"] == "T1":
            return el
    raise AssertionError("no T1 in the report: %r" % (value,))

class DCIsAWire(unittest.TestCase):
    def test_a_matched_divider_puts_half_the_source_on_both_ports(self):
        n = _nodes(_line())
        self.assertAlmostEqual(n["1"], 3.0, places=9)
        self.assertAlmostEqual(n["2"], 1.5, places=9)
        self.assertAlmostEqual(n["3"], 1.5, places=9)

    def test_without_the_card_the_far_node_reads_zero(self):
        # THE control: node 3 is only connected through the line, so removing it
        # leaves a floating node at 0 V. A "1.5 V at every node" answer would
        # pass the test above without the line doing anything.
        n = _nodes("* t\nV1 1 0 dc 3\nR1 1 2 50\nR2 3 0 50\n.op\n.end\n")
        self.assertAlmostEqual(n["3"], 0.0, places=9)

    def test_the_line_carries_the_current_and_dissipates_nothing(self):
        el = _tline(_ok(_line()))
        self.assertAlmostEqual(el["port1"]["voltage_v"], 1.5, places=9)
        self.assertAlmostEqual(el["port2"]["voltage_v"], 1.5, places=9)
        self.assertAlmostEqual(el["port1"]["current_a"], 0.03, places=9)
        self.assertAlmostEqual(el["port2"]["current_a"], -0.03, places=9)
        self.assertEqual(el["z0_ohm"], 50.0)
        self.assertAlmostEqual(el["delay_s"], 1e-9, places=18)
        # an ideal line stores and releases energy but dissipates none: the two
        # port powers cancel term by term, so this is not a hidden loss model
        self.assertAlmostEqual(el["power_w"], 0.0, places=12)

    def test_swapping_the_ports_swaps_nothing_at_dc(self):
        # the four nodes are read positionally, so a deck that wrote port 2 first
        # must still give a shorted line at DC
        n = _nodes(_line(nodes="3 0 2 0"))
        self.assertAlmostEqual(n["2"], 1.5, places=9)
        self.assertAlmostEqual(n["3"], 1.5, places=9)

    def test_two_lines_in_series(self):
        n = _nodes("* t\nV1 1 0 dc 3\nR1 1 2 50\nT1 2 0 3 0 Z0=50 TD=1n\n"
                   "T2 3 0 4 0 Z0=50 TD=2n\nR2 4 0 50\n.op\n.end\n")
        for node in ("2", "3", "4"):
            self.assertAlmostEqual(n[node], 1.5, places=9)

    def test_a_line_inside_a_subcircuit(self):
        # the body's node names are remapped per instance, exactly like every
        # other card; a body line that kept its own names would leave node 3
        # disconnected and read 0 V
        n = _nodes("""* t
.subckt line50 a b c d
T1 a b c d Z0=50 TD=1n
.ends
V1 1 0 dc 3
R1 1 2 50
X1 2 0 3 0 line50
R2 3 0 50
.op
.end
""")
        self.assertAlmostEqual(n["2"], 1.5, places=9)
        self.assertAlmostEqual(n["3"], 1.5, places=9)

    def test_a_short_at_the_far_end_pulls_the_full_source_current(self):
        # at DC the line is a wire, so the source sees the load itself: a short
        # draws 3 V / 50 ohm. An implementation that kept the far end floating
        # would draw nothing, which is the other half of "the line is a wire".
        n = _nodes(_line(load="R2 3 0 1m"))
        el = _tline(_ok(_line(load="R2 3 0 1m")))
        # 1 mOhm, not 0: the far end sits at 3 V * 1m/50 = 60 uV, which is the
        # point - the line does not invent a drop of its own at DC
        self.assertLess(abs(n["3"]), 1e-3)
        self.assertAlmostEqual(el["port1"]["current_a"], 0.06, places=4)

class TransientStep(unittest.TestCase):
    def test_a_matched_step_holds_the_source_side_at_half(self):
        pts = _ok(_step(), analysis="tran")["points"]
        for tq in (0.5e-9, 1.0e-9, 2.5e-9, 5.0e-9):
            self.assertAlmostEqual(_at(pts, tq, "2"), 1.5, places=6)

    def test_the_far_end_is_zero_until_the_delay_then_steps(self):
        pts = _ok(_step(), analysis="tran")["points"]
        self.assertAlmostEqual(_at(pts, 0.0, "3"), 0.0, places=9)
        self.assertAlmostEqual(_at(pts, 0.9e-9, "3"), 0.0, places=9)
        self.assertAlmostEqual(_at(pts, 1.5e-9, "3"), 1.5, places=6)
        self.assertAlmostEqual(_at(pts, 5.0e-9, "3"), 1.5, places=6)

    def test_an_open_end_doubles_the_step_and_the_reflection_comes_back(self):
        # far end open: the incident wave reflects with coefficient +1, so the
        # far end sees 2*1.5 = 3.0 V after TD and the source side sees it after
        # another TD. A line that ignores reflection would read 1.5 V forever.
        pts = _ok(_step(load="R2 3 0 1e12"), analysis="tran")["points"]
        self.assertAlmostEqual(_at(pts, 1.5e-9, "3"), 3.0, places=6)
        self.assertAlmostEqual(_at(pts, 1.5e-9, "2"), 1.5, places=6)
        self.assertAlmostEqual(_at(pts, 0.9e-9, "2"), 1.5, places=6)
        self.assertAlmostEqual(_at(pts, 2.5e-9, "2"), 3.0, places=6)

    def test_a_short_at_the_far_end_reflects_with_minus_one(self):
        # 0 ohm load: the far end is pinned at 0 V by the short itself, and the
        # reflected wave cancels the source side at 2*TD
        pts = _ok(_step(load="R2 3 0 1m"), analysis="tran")["points"]
        self.assertLess(abs(_at(pts, 1.5e-9, "3")), 1e-3)
        self.assertLess(abs(_at(pts, 2.5e-9, "2")), 1e-3)

    def test_f_and_nl_are_the_same_delay_as_td(self):
        a = _ok(_step(params="Z0=50 TD=1n"), analysis="tran")["points"]
        b = _ok(_step(params="Z0=50 F=1G NL=1"), analysis="tran")["points"]
        self.assertEqual(len(a), len(b))
        for pa, pb in zip(a, b):
            self.assertEqual(pa["t"], pb["t"])
            for node in ("2", "3"):
                self.assertAlmostEqual(pa["nodes_v"][node], pb["nodes_v"][node], places=9)

    def test_nl_scales_the_delay(self):
        # TD = 1/(F*NL) = 1/(1G*2) = 0.5 ns, so the far end must step at 0.5 ns
        pts = _ok(_step(params="Z0=50 F=1G NL=2"), analysis="tran")["points"]
        self.assertAlmostEqual(_at(pts, 0.4e-9, "3"), 0.0, places=9)
        self.assertAlmostEqual(_at(pts, 1.0e-9, "3"), 1.5, places=6)

    def test_a_pulse_with_no_width_lasts_until_tstop(self):
        # regression for a real defect: the old PULSE width default was 0, so
        # PULSE(0 3 0 1p) collapsed back to 0 after one instant and the far end
        # never left 0 V. SPICE's default width is tstop.
        net = "* t\nV1 1 0 PULSE(0 3 0 1p)\nR1 1 2 50\nR2 2 0 50\n.tran 0.1n 2n\n.end\n"
        pts = _ok(net, analysis="tran")["points"]
        self.assertAlmostEqual(_at(pts, 2.0e-9, "2"), 1.5, places=6)

    def test_the_final_report_agrees_with_the_last_point(self):
        # elements_final is computed by a different code path (the companion
        # relation, not the stamped row), so this is a real agreement check
        v = _ok(_step(), analysis="tran")
        el = _tline(v)
        last = v["points"][-1]["nodes_v"]
        self.assertAlmostEqual(el["port1"]["voltage_v"], last["2"], places=6)
        self.assertAlmostEqual(el["port2"]["voltage_v"], last["3"], places=6)
        # the currents sum to zero on an ideal line at every instant
        self.assertAlmostEqual(el["port1"]["current_a"] + el["port2"]["current_a"],
                               0.0, places=9)

class ACPhase(unittest.TestCase):
    def _ac(self, freqs=("50meg", "100meg", "150meg", "200meg")):
        return _ok("* t\nV1 1 0 dc 1 ac 1\nR1 1 2 50\nT1 2 0 3 0 Z0=50 TD=1n\n"
                   "R2 3 0 50\n.ac lin %d %s %s\n.end\n"
                   % (len(freqs), freqs[0], freqs[-1]), analysis="ac")["points"]

    def test_a_matched_line_passes_half_the_source_at_every_frequency(self):
        for p in self._ac():
            self.assertAlmostEqual(p["nodes_v"]["2"]["mag"], 0.5, places=6)
            self.assertAlmostEqual(p["nodes_v"]["3"]["mag"], 0.5, places=6)

    def test_the_phase_is_minus_omega_td(self):
        # the only frequency dependence of an ideal matched line is the delay:
        # phase = -2*pi*f*TD, i.e. -18 deg per 50 MHz at TD = 1 ns
        for p, want in zip(self._ac(), (-18.0, -36.0, -54.0, -72.0)):
            self.assertAlmostEqual(p["nodes_v"]["3"]["phase_deg"], want, places=4)

    def test_the_near_end_has_no_phase_shift_of_its_own(self):
        # the delayed copy is at the far end; the near end only sees the line's
        # input impedance, so its phase must stay 0
        for p in self._ac():
            self.assertAlmostEqual(p["nodes_v"]["2"]["phase_deg"], 0.0, places=6)

    def test_an_open_ended_line_is_a_quarter_wave_transformer(self):
        # open far end: Zin = -j*Z0/tan(theta), so the near end reads
        # cos(theta) of the source (0.309 at 200 MHz = cos 72 deg) and 0 at the
        # quarter wave, 250 MHz, where the half-wave stub is a short. The far
        # end, by contrast, is always twice the incident wave = 1.0, because a
        # perfect open end reflects with +1 at every frequency. This is the
        # frequency-domain face of the reflection the transient tests measure.
        pts = _ok("* t\nV1 1 0 dc 1 ac 1\nR1 1 2 50\nT1 2 0 3 0 Z0=50 TD=1n\n"
                  "R2 3 0 1e12\n.ac lin 3 200meg 300meg\n.end\n", analysis="ac")["points"]
        self.assertAlmostEqual(pts[0]["nodes_v"]["2"]["mag"],
                               math.cos(0.4 * math.pi), places=5)
        # lin 3 200meg 300meg is 200 / 250 / 300 MHz, so the quarter wave is
        # the middle point
        self.assertAlmostEqual(pts[1]["nodes_v"]["2"]["mag"], 0.0, places=3)
        for p in pts:
            self.assertAlmostEqual(p["nodes_v"]["3"]["mag"], 1.0, places=5)

class Refusals(unittest.TestCase):
    def test_a_missing_z0_is_refused(self):
        r = _err(_line(params="TD=1n"))
        self.assertEqual(r["reason_code"], "NETLIST_INVALID")
        self.assertIn("needs Z0=", r["message"])

    def test_a_missing_td_is_refused(self):
        # ngspice silently defaults a missing TD to 0, which turns the line into
        # a wire; a deck that lost this number must not be answered
        r = _err(_line(params="Z0=50"))
        self.assertEqual(r["reason_code"], "NETLIST_INVALID")
        self.assertIn("has no delay", r["message"])

    def test_a_zero_delay_is_refused(self):
        r = _err(_line(params="Z0=50 TD=0"))
        self.assertEqual(r["reason_code"], "NETLIST_INVALID")
        self.assertIn("zero-delay line is a wire", r["message"])

    def test_a_negative_z0_is_refused(self):
        r = _err(_line(params="Z0=-50 TD=1n"))
        self.assertEqual(r["reason_code"], "NETLIST_INVALID")
        self.assertIn("must be positive", r["message"])

    def test_both_td_and_f_are_refused(self):
        r = _err(_line(params="Z0=50 TD=1n F=1G"))
        self.assertEqual(r["reason_code"], "NETLIST_INVALID")
        self.assertIn("gives both TD= and F=", r["message"])

    def test_nl_without_f_is_refused(self):
        r = _err(_line(params="Z0=50 NL=2"))
        self.assertEqual(r["reason_code"], "NETLIST_INVALID")
        self.assertIn("gives NL= without F=", r["message"])

    def test_ic_rel_and_abs_are_refused_by_name(self):
        for key in ("IC=0", "REL=1", "ABS=1"):
            r = _err(_line(params="Z0=50 TD=1n " + key))
            self.assertEqual(r["reason_code"], "NETLIST_INVALID")
            self.assertIn("IC= (the line's initial", r["message"])

    def test_three_nodes_are_refused_with_the_node_count(self):
        # without this check the "Z0=50" token got read as a node name and the
        # message pointed at the impedance instead of at the missing node
        r = _err(_line(nodes="2 0 3", params="Z0=50 TD=1n").replace("  Z0", " Z0"))
        self.assertEqual(r["reason_code"], "NETLIST_INVALID")
        self.assertIn("needs four node names before any parameter", r["message"])

    def test_a_port_wired_to_itself_is_refused(self):
        r = _err(_line(nodes="2 0 2 0"))
        self.assertEqual(r["reason_code"], "NETLIST_INVALID")
        self.assertIn("connects a port to itself", r["message"])

    def test_a_line_with_both_ports_on_ground_is_refused(self):
        r = _err(_line(nodes="0 0 0 0"))
        self.assertEqual(r["reason_code"], "NETLIST_INVALID")
        self.assertIn("both ports on ground", r["message"])

    def test_a_delay_shorter_than_the_timestep_is_refused(self):
        # the far end would have to read a time between steps that does not
        # exist; rounding it to one step would move every reflection
        r = _err(_step(tran=".tran 2n 50n"), analysis="tran")
        self.assertEqual(r["reason_code"], "ARG_RANGE")
        self.assertIn("shorter than the timestep", r["message"])

    def test_the_half_wave_point_is_refused_not_answered_with_a_huge_number(self):
        r = _err("* t\nV1 1 0 dc 1 ac 1\nR1 1 2 50\nT1 2 0 3 0 Z0=50 TD=1n\n"
                 "R2 3 0 50\n.ac lin 2 100meg 500meg\n.end\n", analysis="ac")
        self.assertEqual(r["reason_code"], "SINGULAR")
        self.assertIn("multiple of pi", r["message"])

    def test_a_lossy_line_without_a_model_is_refused_by_name(self):
        # 0.0.31 does implement O cards (docs/37), so the refusal here can no
        # longer be "O is not implemented": what is still refused is an O card
        # with no model to take its parameters from, and the message has to name
        # the model rather than the card.
        r = _err("* t\nV1 1 0 dc 1\nO1 2 0 3 0 ltramod\nR2 3 0 50\n.op\n.end\n")
        self.assertEqual(r["reason_code"], "NETLIST_INVALID")
        self.assertIn("ltramod", r["message"])
        self.assertNotIn("not implemented here", r["message"])

class Warnings(unittest.TestCase):
    def test_near_the_half_wave_point_the_answer_comes_with_a_warning(self):
        # 500 MHz is the singular point; 500.05 MHz is soluble but ill
        # conditioned. The answer is still given - with the reason attached
        r = _env("* t\nV1 1 0 dc 1 ac 1\nR1 1 2 50\nT1 2 0 3 0 Z0=50 TD=1n\n"
                 "R2 3 0 50\n.ac lin 2 100meg 500.05meg\n.end\n", analysis="ac")
        hits = [w for w in r["warnings"] if "multiple of pi" in w]
        self.assertEqual(len(hits), 1, r["warnings"])
        self.assertIn("T1", hits[0])

    def test_the_warning_is_not_repeated_for_every_frequency(self):
        # a dense sweep near 500 MHz would otherwise produce hundreds of copies
        r = _env("* t\nV1 1 0 dc 1 ac 1\nR1 1 2 50\nT1 2 0 3 0 Z0=50 TD=1n\n"
                 "R2 3 0 50\n.ac lin 5 499.9meg 500.2meg\n.end\n", analysis="ac")
        hits = [w for w in r["warnings"] if "multiple of pi" in w]
        self.assertEqual(len(hits), 1, r["warnings"])

class RealEngineAgrees(unittest.TestCase):
    """The builtin answer against ngspice, when one is installed."""

    def setUp(self):
        self.argv = spice.find_ngspice()

    def _cross(self, net, analysis="op"):
        if not self.argv:
            self.skipTest("no ngspice on this machine")
        r = spice.simulate(net, analysis=analysis, engine="ngspice",
                           cross_check=True)
        assert r["status"] == "ok", r
        return r["cross_check"]

    def test_a_line_in_a_dc_point(self):
        cc = self._cross(_line())
        self.assertGreaterEqual(cc["compared_nodes"], 2)
        self.assertLess(cc["max_abs_diff_v"], 1e-6, cc)

    def test_a_line_in_a_transient(self):
        cc = self._cross(_step(), analysis="tran")
        self.assertGreaterEqual(cc["compared_nodes"], 2)
        self.assertLess(cc["max_abs_diff_v"], 1e-6, cc)

    def test_a_line_in_ac(self):
        cc = self._cross("* t\nV1 1 0 dc 1 ac 1\nR1 1 2 50\nT1 2 0 3 0 Z0=50 TD=1n\n"
                         "R2 3 0 50\n.ac lin 4 50meg 200meg\n.end\n", analysis="ac")
        self.assertGreaterEqual(cc["compared_nodes"], 2)
        self.assertLess(cc["max_abs_diff_v"], 1e-6, cc)

if __name__ == "__main__":
    unittest.main()
