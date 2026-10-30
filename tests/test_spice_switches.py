"""Golden tests for the two switch cards: S (voltage-controlled) and W (current).

A switch is not a nonlinear device in the Newton sense: it is a conductance with
TWO values, and which one is in circuit is a state. That makes every expected
number below a divider, computed by hand from the two resistor values - and makes
the interesting tests the ones about WHICH state the solver settled in:

* S `Sname n+ n- nc+ nc- model` - closed while v(nc+) - v(nc-) is past the
  threshold `VT`; the card has four terminals because the control is a voltage.
* W `Wname n+ n- vname model` - closed while the current through the NAMED
  element is past `IT`; reading `vname` as a node would switch on 0 A forever.

The hysteresis tests are the negative controls: the SAME ramp that closes a
switch with VH=0 must leave one with VH=0.5 open, because the ramp stops at
1.2 V while closing needs VT+VH = 1.5 V - the band is the FULL VH on each side of
VT (measured against ngspice, not read off a manual). A hysteresis that is never
compared against a no-hysteresis run is not a test, it is decoration.
"""
import cmath
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


def _els(value):
    return {e["name"]: e for e in value["elements"]}


def _tran(net):
    r = spice.simulate(net, analysis="tran")
    assert r["status"] == "ok", r
    return r["value"]


def _sw_dc(control_v, extra=""):
    return ("* s dc\nV1 1 0 5\nVc 3 0 %s\nS1 1 2 3 0 smod\nR1 2 0 1k\n"
            ".model smod SW(VT=1 VH=0 RON=10 ROFF=1meg)\n.op\n%s"
            % (control_v, extra))


class TestVoltageSwitchDC(unittest.TestCase):
    def test_closed_switch_is_a_ten_ohm_resistor(self):
        # 5 V through RON=10 into 1k: 5 * 1000/1010
        # node voltages are REPORTED rounded to 9 decimals, so 8 places is the
        # tightest comparison the report can support - 9 would fail on 5e-10
        v = _op(_sw_dc(5))
        self.assertAlmostEqual(v["nodes_v"]["2"], 4.950495049504950, places=8)
        s1 = _els(v)["S1"]
        self.assertEqual(s1["state"], "on")
        self.assertAlmostEqual(s1["resistance_ohm"], 10.0, places=9)
        # gmin again: the reported current includes ~5e-12 A of leakage
        self.assertAlmostEqual(s1["current_a"], 4.950495049504950e-3, delta=1e-9)
        self.assertAlmostEqual(s1["control_voltage_v"], 5.0, places=9)

    def test_open_switch_is_a_megohm(self):
        # S1 sits in SERIES with the 1k load, so open leaves the load fed only
        # through ROFF: 5 * 1e3/(1e3+1e6) - a few millivolts, not 4.995 V
        v = _op(_sw_dc(0))
        self.assertAlmostEqual(v["nodes_v"]["2"], 0.004995004995004995, places=8)
        s1 = _els(v)["S1"]
        self.assertEqual(s1["state"], "off")
        self.assertAlmostEqual(s1["resistance_ohm"], 1e6, places=3)

    def test_the_switch_does_not_change_the_control_voltage(self):
        # the control pair (3, 0) is an INPUT: the switch draws no current from it
        v = _op(_sw_dc(5))
        self.assertAlmostEqual(v["nodes_v"]["3"], 5.0, places=12)
        self.assertAlmostEqual(_els(v)["Vc"]["current_a"], 5e-12, places=15)

    def test_the_threshold_is_inclusive_at_exactly_vt(self):
        # Which way the boundary falls is a CONVENTION - at exactly VT both states
        # are self-consistent - so it is settled by measurement against ngspice:
        # with VH=0 the engine closes when control == VT (>=) and opens one
        # microvolt below (16/16 cases in probe_thr2.py). Pinned here because a
        # reader could reasonably assume the opposite.
        at = _els(_op(_sw_dc(1.0)))["S1"]
        below = _els(_op(_sw_dc(0.999999)))["S1"]
        self.assertEqual(at["state"], "on")
        self.assertEqual(below["state"], "off")

    def test_ON_is_the_state_the_deck_STARTS_in_not_a_latch(self):
        # Measured against ngspice-47, because the intuitive reading ("ON means
        # on") is wrong: with a 0 V control the card still settles OPEN (0 V is
        # below VT-VH), and the mirror deck `OFF` with a 5 V control settles
        # CLOSED. What ON/OFF changes is where the search STARTS, which is
        # invisible unless the control sits inside the hysteresis band.
        on_net = ("* s init\nV1 1 0 5\nVc 3 0 0\nS1 1 2 3 0 smod ON\nR1 2 0 1k\n"
                  ".model smod SW(VT=1 VH=0 RON=10 ROFF=1meg)\n.op\n")
        s1 = _els(_op(on_net))["S1"]
        self.assertEqual(s1["state"], "off")
        self.assertEqual(s1["initial_state"], "on")
        off_net = ("* s init\nV1 1 0 5\nVc 3 0 5\nS1 1 2 3 0 smod OFF\nR1 2 0 1k\n"
                   ".model smod SW(VT=1 VH=0 RON=10 ROFF=1meg)\n.op\n")
        s2 = _els(_op(off_net))["S1"]
        self.assertEqual(s2["state"], "on")
        self.assertEqual(s2["initial_state"], "off")

    def test_inside_a_band_the_seed_is_the_whole_answer(self):
        # 1.2 V sits inside VT-VH .. VT+VH, so NEITHER state contradicts the
        # control and the seed decides: `ON` settles closed, no seed settles
        # open. A DC solve read straight off the iterate cannot hold this - the
        # first iteration would see 0 V and drop the seed.
        def deck(init):
            return ("* seed\nV1 1 0 5\nVc 3 0 1.2\nS1 1 2 3 0 smod%s\nR1 2 0 1k\n"
                    ".model smod SW(VT=1 VH=0.5 RON=10 ROFF=1meg)\n.op\n" % init)
        self.assertEqual(_els(_op(deck("")))["S1"]["state"], "off")
        self.assertEqual(_els(_op(deck(" ON")))["S1"]["state"], "on")
        self.assertEqual(_els(_op(deck(" OFF")))["S1"]["state"], "off")


class TestCurrentSwitchDC(unittest.TestCase):
    def _net(self, r_gate):
        # i(Vs) is the CURRENT through the 0 V source, which is the gate branch:
        # 5 V / Rg. The sign follows ngspice's convention (positive from n+ to n-
        # INSIDE the source), measured against the engine on this very deck.
        return ("* w dc\nV1 1 0 5\nVs 1 3 0\nRg 3 0 %s\nW1 1 2 Vs swl\n"
                "R1 2 0 100\n.model swl CSW(IT=1m IH=0 RON=1 ROFF=1meg)\n.op\n"
                % r_gate)

    def test_five_milliamps_closes_it(self):
        v = _op(self._net("1k"))
        w1 = _els(v)["W1"]
        self.assertEqual(w1["state"], "on")
        # gmin leaks ~5e-12 A through the 0 V sense source, so 12 places would
        # fail on a value that is right to 12 significant digits
        self.assertAlmostEqual(w1["control_current_a"], 5e-3, delta=1e-9)
        self.assertEqual(w1["controlled_element"], "Vs")
        self.assertAlmostEqual(v["nodes_v"]["2"], 5.0 * 100.0 / 101.0, places=8)

    def test_half_a_milliamp_leaves_it_open(self):
        v = _op(self._net("10k"))
        w1 = _els(v)["W1"]
        self.assertEqual(w1["state"], "off")
        self.assertAlmostEqual(w1["control_current_a"], 5e-4, delta=1e-9)
        self.assertAlmostEqual(v["nodes_v"]["2"], 5.0 * 100.0 / 1000100.0, places=8)

    def test_the_sign_of_the_control_current_matters(self):
        # reversing the sense source reverses the current, so the same 5 mA reads
        # as -5 mA and the switch stays open: a W card is not an absolute-value
        net = self._net("1k").replace("Vs 1 3 0", "Vs 3 1 0")
        w1 = _els(_op(net))["W1"]
        self.assertEqual(w1["state"], "off")
        self.assertAlmostEqual(w1["control_current_a"], -5e-3, delta=1e-9)


class TestSwitchesInAC(unittest.TestCase):
    def test_ac_uses_the_state_the_bias_point_settled_in(self):
        # RON=1k, R=1k, C=1uF at f = 1/(2*pi*1e-3): Zc = 500*(1-j), so
        # v2 = Zc/(1k+Zc) = 0.4472136 at -26.5651 degrees.
        f = 1.0 / (2.0 * math.pi * 1e-3)
        net = ("* s ac\nV1 1 0 DC 5 AC 1\nVc 3 0 5\nS1 1 2 3 0 smod\n"
               "R1 2 0 1k\nC1 2 0 1u\n"
               ".model smod SW(VT=1 VH=0 RON=1k ROFF=1meg)\n"
               ".ac lin 2 %.9f %.9f\n" % (f, 2.0 * f))
        r = spice.simulate(net, analysis="ac")
        self.assertEqual(r["status"], "ok", r)
        v2 = r["value"]["points"][0]["nodes_v"]["2"]
        z = cmath.rect(v2["mag"], math.radians(v2["phase_deg"]))
        self.assertAlmostEqual(abs(z), 0.4472136194, places=6)
        self.assertAlmostEqual(math.degrees(cmath.phase(z)), -26.5650511771,
                               places=4)

    def test_the_open_state_is_visible_in_ac_too(self):
        f = 1.0 / (2.0 * math.pi * 1e-3)
        net = ("* s ac open\nV1 1 0 DC 5 AC 1\nVc 3 0 0\nS1 1 2 3 0 smod\n"
               "R1 2 0 1k\nC1 2 0 1u\n"
               ".model smod SW(VT=1 VH=0 RON=1k ROFF=1meg)\n"
               ".ac lin 2 %.9f %.9f\n" % (f, 2.0 * f))
        r = spice.simulate(net, analysis="ac")
        self.assertEqual(r["status"], "ok", r)
        # The switch is in SERIES with the load, so "open" means the source reaches
        # the RC pair through ROFF=1e6. With Zp = 1k || 1uF at w=1000 (= 500-j500)
        # the answer is Zp/(1e6+Zp): 0.7 mV at -45 degrees, not 1 V at 0. An open
        # switch that still passed the full source would be a short.
        zp = 1.0 / (1.0 / 1000.0 + 1j * 1000.0 * 1e-6)
        want = zp / (1e6 + zp)
        v2 = r["value"]["points"][0]["nodes_v"]["2"]
        self.assertAlmostEqual(v2["mag"], abs(want), places=9)
        self.assertAlmostEqual(v2["phase_deg"],
                               math.degrees(cmath.phase(want)), places=6)


class TestHysteresis(unittest.TestCase):
    # The control ramps 0 -> 1.2 V over 2 ms starting at 1 ms, so it crosses
    # VT=1.0 but never reaches VT+VH/2 = 1.25 for VH=0.5. One switching, one not.
    _RAMP = ("* ramp\nV1 1 0 5\nVc 3 0 PULSE(0 1.2 1m 2m 0 50m 60m)\n"
             "S1 1 2 3 0 smod\nR1 2 0 1k\n"
             ".model smod SW(VT=1 %s RON=10 ROFF=1meg)\n.tran 50u 5m\n")

    def test_without_hysteresis_the_ramp_closes_the_switch(self):
        v = _tran(self._RAMP % "VH=0")
        self.assertAlmostEqual(v["points"][-1]["nodes_v"]["2"],
                               4.950495049504950, places=6)
        self.assertGreaterEqual(v["switch_flips"], 1)

    def test_with_hysteresis_the_same_ramp_does_not(self):
        v = _tran(self._RAMP % "VH=0.5")
        self.assertAlmostEqual(v["points"][-1]["nodes_v"]["2"],
                               0.004995004995004995, places=8)
        self.assertEqual(v["switch_flips"], 0)

    def test_once_closed_a_dip_inside_the_band_keeps_it_closed(self):
        # 0 -> 5 V at 0.5 ms, back to 0.9 V at 2 ms. VT=1, VH=0.5: the open->closed
        # edge needs > 1.25 (5 V does it), the closed->open edge needs < 0.75
        # (0.9 V does not), so the switch must still be closed at the end.
        net = ("* latch\nV1 1 0 5\nVc 3 0 PWL(0 0 0.5m 0 0.6m 5 2m 5 2.1m 0.9 "
               "5m 0.9)\nS1 1 2 3 0 smod\nR1 2 0 1k\n"
               ".model smod SW(VT=1 VH=0.5 RON=10 ROFF=1meg)\n.tran 20u 5m\n")
        v = _tran(net)
        for p in v["points"]:
            if p["t"] >= 3e-3:
                self.assertAlmostEqual(p["nodes_v"]["2"], 4.950495049504950,
                                       places=6)
                break

    def test_hysteresis_can_shut_an_edge_that_a_pure_threshold_would_chatter_on(self):
        # a triangle that crosses VT twice inside one hysteresis band: the narrow
        # peak is below VT+VH/2, so a switch that starts open never closes at all,
        # while VH=0 closes it on the first crossing
        net = ("* triangle\nV1 1 0 5\nVc 3 0 PWL(0 0 1m 1.3 2m 0.7 5m 0.7)\n"
               "S1 1 2 3 0 smod\nR1 2 0 1k\n"
               ".model smod SW(VT=1 %s RON=10 ROFF=1meg)\n.tran 10u 5m\n")
        loose = _tran(net % "VH=0")
        tight = _tran(net % "VH=0.8")
        # Both END open - the drive falls back to 0.7 V and the VH=0 arm lets go at
        # once - so the loose arm is read by its PEAK, not by its last point: it
        # did close while the drive was above 1 V, the hysteretic one never did.
        self.assertAlmostEqual(max(p["nodes_v"]["2"] for p in loose["points"]),
                               4.950495049504950, places=6)
        self.assertAlmostEqual(max(p["nodes_v"]["2"] for p in tight["points"]),
                               0.004995004995004995, places=8)


class TestSwitchRefusals(unittest.TestCase):
    def _bad(self, net):
        r = spice.simulate(net, analysis="op")
        self.assertEqual(r["status"], "error", r)
        self.assertEqual(r["reason_code"], "NETLIST_INVALID", r)
        return r["message"]

    def test_a_missing_model(self):
        msg = self._bad("* x\nV1 1 0 5\nS1 1 2 3 0 nosuch\nR1 2 0 1k\n.op\n")
        self.assertIn("not defined", msg)

    def test_a_switch_and_a_diode_model_are_not_interchangeable(self):
        msg = self._bad("* x\nV1 1 0 5\nS1 1 2 3 0 dm\nR1 2 0 1k\n"
                        ".model dm D(IS=1e-14)\n.op\n")
        self.assertIn("needs a SW model", msg)

    def test_a_W_card_needs_a_CSW_model(self):
        msg = self._bad("* x\nV1 1 0 5\nVs 1 3 0\nRg 3 0 1k\nW1 1 2 Vs smod\n"
                        "R1 2 0 1k\n.model smod SW(VT=1 RON=1 ROFF=1meg)\n.op\n")
        self.assertIn("needs a CSW model", msg)

    def test_zero_on_resistance_is_refused_not_silently_shortened(self):
        msg = self._bad(_sw_dc(5).replace("RON=10", "RON=0"))
        self.assertIn("ideal short", msg)

    def test_a_negative_off_resistance(self):
        msg = self._bad(_sw_dc(5).replace("ROFF=1meg", "ROFF=-1"))
        self.assertIn("positive (large) resistance", msg)

    def test_an_off_resistance_below_the_on_resistance(self):
        msg = self._bad(_sw_dc(5).replace("RON=10 ROFF=1meg", "RON=1k ROFF=10"))
        self.assertIn("conducts BETTER when open", msg)

    def test_a_negative_hysteresis(self):
        msg = self._bad(_sw_dc(5).replace("VH=0", "VH=-0.1"))
        self.assertIn("cannot be negative", msg)

    def test_an_unknown_model_parameter_is_named(self):
        msg = self._bad(_sw_dc(5).replace("VH=0", "VH=0 VON=3"))
        self.assertIn("not a parameter of a SW switch", msg)
        self.assertIn("VT, VH, RON, ROFF", msg)

    def test_a_truncated_card_shows_the_shape(self):
        msg = self._bad("* x\nV1 1 0 5\nS1 1 2 3 0\nR1 2 0 1k\n.op\n")
        self.assertIn("Sname n+ n- nc+ nc- model", msg)

    def test_a_W_card_whose_control_is_a_number(self):
        msg = self._bad("* x\nV1 1 0 5\nW1 1 2 3 swl\nR1 2 0 1k\n"
                        ".model swl CSW(IT=1m RON=1 ROFF=1meg)\n.op\n")
        self.assertIn("not an element name", msg)

    def test_trailing_junk_is_not_a_field(self):
        msg = self._bad(_sw_dc(5).replace("RON=10 ROFF=1meg)\n",
                                          "RON=10 ROFF=1meg)\n").replace(
            "S1 1 2 3 0 smod", "S1 1 2 3 0 smod maybe"))
        self.assertIn("is not a field of a S card", msg)

    def test_a_W_card_controlled_by_a_resistor_has_no_branch_current(self):
        msg = self._bad("* x\nV1 1 0 5\nR1 1 0 1k\nW1 1 2 R1 swl\nR2 2 0 1k\n"
                        ".model swl CSW(IT=1m RON=1 ROFF=1meg)\n.op\n")
        self.assertIn("no branch current", msg)

    def test_a_switch_that_removes_its_own_control_is_refused(self):
        # S1 shorts node 3, which is the voltage that closes S1: on it reads
        # 0.0005 V (open), off it reads 0.4545 V (closed). No state is consistent.
        net = ("* bistable\nV1 1 0 5\nR1 1 3 10k\nR2 3 0 1k\nS1 3 0 3 0 smod\n"
               ".model smod SW(VT=0.4 VH=0 RON=1 ROFF=1meg)\n.op\n")
        r = spice.simulate(net, analysis="op")
        self.assertEqual(r["status"], "error")
        self.assertEqual(r["reason_code"], "NOT_CONVERGED")
        self.assertIn("bistable", r["hint"])


class TestInsideASubcircuit(unittest.TestCase):
    def test_a_voltage_switch_keeps_its_control_pair(self):
        net = ("* subckt\nV1 1 0 5\nVc 3 0 5\nX1 1 2 3 swcell\n"
               ".subckt swcell a b c\nS1 a b c 0 smod\nR1 b 0 1k\n.ends\n"
               ".model smod SW(VT=1 VH=0 RON=10 ROFF=1meg)\n.op\n")
        v = _op(net)
        self.assertAlmostEqual(v["nodes_v"]["2"], 4.950495049504950, places=8)
        # the body's control node c is the pin, so the top-level 5 V is the control
        self.assertIn("x1.s1", _els(v))

    def test_a_W_card_inside_a_body_is_switched_by_the_body_source(self):
        # A W card names an ELEMENT, not a node, so the controlling branch has to
        # exist INSIDE the body too: Vg is the body's own 0 V sense source. That
        # also tests the scoping on both ends - the card's third field AND the
        # branch it points at.
        net = ("* subckt w\nV1 1 0 5\nX1 1 2 wcell\n"
               ".subckt wcell a b\nVg a c 0\nRg c 0 1k\nW1 a b Vg swl\n"
               "R1 b 0 100\n.ends\n"
               ".model swl CSW(IT=1m IH=0 RON=1 ROFF=1meg)\n.op\n")
        v = _op(net)
        w = _els(v)["x1.w1"]
        self.assertEqual(w["state"], "on")
        self.assertEqual(w["controlled_element"], "x1.vg")
        self.assertAlmostEqual(w["control_current_a"], 5e-3, delta=1e-9)
        self.assertAlmostEqual(v["nodes_v"]["2"], 5.0 * 100.0 / 101.0, places=8)


class TestTransientPlumbing(unittest.TestCase):
    def test_switch_flips_are_reported_only_when_there_is_a_switch(self):
        sw = _tran(TestHysteresis._RAMP % "VH=0")
        self.assertIn("switch_flips", sw)
        plain = _tran("* rc\nV1 1 0 1\nR1 1 2 1k\nC1 2 0 1u\n.tran 10u 1m\n")
        self.assertNotIn("switch_flips", plain)

    def test_the_output_follows_the_drive_through_the_switch(self):
        # the drive closes at 1 ms for 3 ms: the load is 4.95 V while closed and
        # back to the leakage level afterwards
        net = ("* follow\nV1 1 0 5\nVc 3 0 PULSE(0 5 1m 1u 1u 3m 10m)\n"
               "S1 1 2 3 0 smod\nR1 2 0 1k\nC1 2 0 1u\n"
               ".model smod SW(VT=1 VH=0.2 RON=10 ROFF=1meg)\n.tran 20u 5m\n")
        v = _tran(net)
        for p in v["points"]:
            if abs(p["t"] - 2.5e-3) < 1e-12:
                self.assertAlmostEqual(p["nodes_v"]["2"], 4.950495049504950,
                                       places=5)
            if abs(p["t"] - 4.8e-3) < 1e-12:
                # the drive went low at 4 ms, so C1 discharges through R1 with a
                # ~1 ms time constant: 4.95*e^-0.8 = 2.22 V. Read the ORDER of
                # magnitude, not a number set by the solver's step grid.
                self.assertTrue(2.0 < p["nodes_v"]["2"] < 2.5,
                                p["nodes_v"]["2"])
        self.assertEqual(v["switch_flips"], 2)


@unittest.skipUnless(spice.find_ngspice(), "ngspice binary not found")
class RealEngineAgrees(unittest.TestCase):
    """The engine is the oracle for the state, not only for the divider."""

    def _both(self, net, analysis=None):
        mine = spice.simulate(net, analysis=analysis)
        theirs = spice.simulate(net, analysis=analysis, engine="ngspice")
        self.assertEqual(mine["status"], "ok", mine)
        self.assertEqual(theirs["status"], "ok", theirs)
        return mine["value"], theirs["value"]

    def test_dc_closed_and_open(self):
        for control in (5, 0, 1.0, 1.000001):
            mine, theirs = self._both(_sw_dc(control))
            for node, val in theirs["nodes_v"].items():
                self.assertAlmostEqual(mine["nodes_v"][node], val, places=8,
                                       msg="control %s node %s" % (control, node))

    def test_the_on_off_seed_matches_in_all_three_decks(self):
        # the seed is a real DC input, so it has to be the ENGINE's answer, not
        # this module's reading: 0 V + ON -> open, 5 V + OFF -> closed, and
        # 1.2 V inside a VT=1/VH=0.5 band -> whatever the card started in.
        decks = []
        for ctl, init, vh in ((0, " ON", 0), (5, " OFF", 0),
                              (1.2, " ON", 0.5), (1.2, "", 0.5)):
            decks.append("* seed\nV1 1 0 5\nVc 3 0 %s\nS1 1 2 3 0 smod%s\n"
                         "R1 2 0 1k\n.model smod SW(VT=1 VH=%s RON=10 ROFF=1meg)\n"
                         ".op\n" % (ctl, init, vh))
        for net in decks:
            mine, theirs = self._both(net)
            for node, val in theirs["nodes_v"].items():
                self.assertAlmostEqual(mine["nodes_v"][node], val, places=8,
                                       msg=net)

    def test_the_current_switch_matches_too(self):
        for rg in ("1k", "10k"):
            net = TestCurrentSwitchDC()._net(rg)
            mine, theirs = self._both(net)
            for node, val in theirs["nodes_v"].items():
                self.assertAlmostEqual(mine["nodes_v"][node], val, places=8)

    def test_ac_with_the_state_frozen_matches(self):
        f = 1.0 / (2.0 * math.pi * 1e-3)
        net = ("* s ac\nV1 1 0 DC 5 AC 1\nVc 3 0 5\nS1 1 2 3 0 smod\n"
               "R1 2 0 1k\nC1 2 0 1u\n"
               ".model smod SW(VT=1 VH=0 RON=1k ROFF=1meg)\n"
               ".ac lin 2 %.9f %.9f\n" % (f, 2.0 * f))
        mine = spice.simulate(net, analysis="ac")["value"]["points"][0]["nodes_v"]
        theirs = spice.simulate(net, analysis="ac")["value"]["points"][0]["nodes_v"]
        m = cmath.rect(mine["2"]["mag"], math.radians(mine["2"]["phase_deg"]))
        t = cmath.rect(theirs["2"]["mag"], math.radians(theirs["2"]["phase_deg"]))
        self.assertLess(abs(m - t), 1e-6)

    def test_transient_away_from_the_edge_agrees_to_a_third_of_a_percent(self):
        # The edge itself is quantised to a solver step (ngspice takes its own,
        # finer, steps across the discontinuity), so the honest comparison is the
        # tail: there the two engines differ only by backward Euler vs trapezoidal.
        net = ("* sw tran\nV1 1 0 5\nVc 3 0 PULSE(0 5 1m 1u 1u 3m 10m)\n"
               "S1 1 2 3 0 smod\nR1 2 0 1k\nC1 2 0 1u\n"
               ".model smod SW(VT=1 VH=0.2 RON=10 ROFF=1meg)\n.tran 20u 5m\n")
        mine = spice.simulate(net)["value"]["points"]
        theirs = [(p["t"], p["nodes_v"]["2"])
                  for p in spice.simulate(net, engine="ngspice")["value"]["points"]]

        def interp(t):
            if t <= theirs[0][0]:
                return theirs[0][1]
            for (t0, v0), (t1, v1) in zip(theirs, theirs[1:]):
                if t <= t1:
                    return v1 if t1 == t0 else v0 + (v1 - v0) * (t - t0) / (t1 - t0)
            return theirs[-1][1]

        worst = 0.0
        for p in mine:
            if 0.9e-3 <= p["t"] <= 1.2e-3:
                continue          # the edge: one step of timing quantisation
            worst = max(worst, abs(p["nodes_v"]["2"] - interp(p["t"])))
        self.assertLess(worst / 4.950495049504950, 0.005,
                        "worst %.3g V away from the edge" % worst)


if __name__ == "__main__":
    unittest.main()
