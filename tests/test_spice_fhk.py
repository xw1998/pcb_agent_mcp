"""Golden tests for the three coupled-source cards: F, H and K.

Every expected number below was computed BY HAND from the textbook relation, not
by running the solver and copying its output:

* F (CCCS)  `Fname n+ n- vname gain` - a current source of  gain * i(vname)
  flowing n+ -> n-. SPICE's branch current is positive from n+ to n- INSIDE the
  source, so a 1 V source behind 1 k has i = -1 mA.
* H (CCVS)  `Hname n+ n- vname gain` - a voltage source whose value is
  gain * i(vname); it needs an unknown of its own, F does not.
* K (mutual) `Kname L1 L2 k` - M = k*sqrt(L1*L2) coupling two EXISTING
  inductors. An open secondary at AC has v2/v1 = M/L1 = k*sqrt(L2/L1).

The three-quarters of these tests that are about refusals exist because the
alternative - dropping a card the solver does not model - gives the caller a
number with no warning attached.
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


def _ac(net):
    r = spice.simulate(net, analysis="ac")
    assert r["status"] == "ok", r
    return r["value"]["points"][0]


def _tran(net):
    r = spice.simulate(net, analysis="tran")
    assert r["status"] == "ok", r
    return r["value"]["points"]


def _el(v, name):
    for e in v.get("elements") or v.get("elements_final") or []:
        if e["name"].lower() == name.lower():
            return e
    raise AssertionError("no element %r in %r" % (name, [e["name"] for e in v["elements"]]))


def _err(net, analysis="op"):
    r = spice.simulate(net, analysis=analysis)
    assert r["status"] == "error", r
    assert r["reason_code"] == "NETLIST_INVALID", r
    return r


# an open-secondary transformer: R1 is only there because a voltage source in
# parallel with an inductor is a singular DC loop, and the answer must not
# depend on R1 (1 ohm is far below the 100 uH reactance at 1 kHz: 0.63 ohm)
_XFMR = ("* k\nV1 1 0 AC 1\nR1 1 3 1\nL1 3 0 100u\nL2 2 0 400u\n%s"
         "R2 2 0 1meg\n.ac lin 2 1k 2k\n.end\n")


class TestCCCS(unittest.TestCase):
    """F: a current source proportional to another element's current."""

    def test_gain_multiplies_the_controlling_current(self):
        # V1 1 0 1 behind 1 k draws i = -1 mA (branch current is measured
        # n+ -> n- inside the source). F1 from node 2 to node 0 therefore
        # pushes -2 mA into node 2, i.e. 2 mA flows down R2 = 1 k -> 2 V.
        v = _op("* f\nV1 1 0 1\nR1 1 0 1k\nF1 2 0 V1 2\nR2 2 0 1k\n.end\n")
        self.assertAlmostEqual(v["nodes_v"]["2"], 2.0, places=6)

    def test_a_negative_gain_flips_the_direction(self):
        v = _op("* f\nV1 1 0 1\nR1 1 0 1k\nF1 2 0 V1 -2\nR2 2 0 1k\n.end\n")
        self.assertAlmostEqual(v["nodes_v"]["2"], -2.0, places=6)

    def test_the_report_shows_both_the_reading_and_the_output(self):
        # the caller has to be able to see WHICH current was read, otherwise a
        # wrong control element is invisible in the answer
        v = _op("* f\nV1 1 0 1\nR1 1 0 1k\nF1 2 0 V1 2\nR2 2 0 1k\n.end\n")
        f = _el(v, "F1")
        self.assertAlmostEqual(f["control_current_a"], -0.001, places=9)
        self.assertAlmostEqual(f["current_a"], -0.002, places=9)
        self.assertAlmostEqual(f["power_w"], 2.0 * -0.002, places=9)

    def test_an_inductor_can_be_the_control(self):
        # L1 carries +1 mA (its branch direction is 2 -> 0, the same way the
        # current really flows), so with the same gain the output is -2 V: the
        # sign difference from the V1 case is the branch convention, not a bug
        v = _op("* f\nV1 1 0 1\nR1 1 2 1k\nL1 2 0 1m\nF1 3 0 L1 2\nR3 3 0 1k\n.end\n")
        self.assertAlmostEqual(v["nodes_v"]["3"], -2.0, places=6)
        self.assertAlmostEqual(_el(v, "F1")["control_current_a"], 0.001, places=9)

    def test_a_cccs_owns_no_unknown_of_its_own(self):
        # 2 nodes + the V1 branch row = 3; an F card that allocated a row would
        # make this 4 and would also have to invent a value for it
        self.assertEqual(_op("* f\nV1 1 0 1\nR1 1 0 1k\nF1 2 0 V1 2\n"
                             "R2 2 0 1k\n.end\n")["unknowns"], 3)

    def test_a_ccvs_can_be_the_control(self):
        # H1 2 0 V1 -1000 reads i = -1 mA and gives v2 = -1000 * -1m = +1 V;
        # F1 then reads H1's branch current (-1 mA) and delivers +2 V
        v = _op("* f\nV1 1 0 1\nR1 1 0 1k\nH1 2 0 V1 -1000\nR2 2 0 1k\n"
                "F1 3 0 H1 2\nR3 3 0 1k\n.end\n")
        self.assertAlmostEqual(v["nodes_v"]["2"], 1.0, places=6)
        self.assertAlmostEqual(v["nodes_v"]["3"], 2.0, places=6)


class TestCCVS(unittest.TestCase):
    """H: a voltage source proportional to another element's current."""

    def test_gain_multiplies_the_controlling_current(self):
        # v2 = gain * i = 2 * (-1 mA) = -2 mV
        v = _op("* h\nV1 1 0 1\nR1 1 0 1k\nH1 2 0 V1 2\nR2 2 0 1k\n.end\n")
        self.assertAlmostEqual(v["nodes_v"]["2"], -0.002, places=9)

    def test_a_large_gain_is_a_transresistance(self):
        # 1 mA through 1000 V/A is exactly 1 V - the classic op-amp-free way of
        # turning a shunt drop into a rail
        v = _op("* h\nV1 1 0 1\nR1 1 0 1k\nH1 2 0 V1 1000\nR2 2 0 1k\n.end\n")
        self.assertAlmostEqual(v["nodes_v"]["2"], -1.0, places=6)

    def test_a_ccvs_owns_a_row(self):
        # 2 nodes + V1 + H1: H has to be solved for like the voltage source it
        # is, so unlike F it MUST add an unknown
        self.assertEqual(_op("* h\nV1 1 0 1\nR1 1 0 1k\nH1 2 0 V1 2\n"
                             "R2 2 0 1k\n.end\n")["unknowns"], 4)

    def test_the_report_carries_the_branch_current(self):
        v = _op("* h\nV1 1 0 1\nR1 1 0 1k\nH1 2 0 V1 1000\nR2 2 0 1k\n.end\n")
        h = _el(v, "H1")
        # -1 V across 1 k sinks 1 mA, and that current flows 0 -> 2 through the
        # source, so the branch unknown (n+ -> n-) is +1 mA
        self.assertAlmostEqual(h["current_a"], 0.001, places=9)
        self.assertAlmostEqual(h["voltage_v"], -1.0, places=6)


class TestControlledSourceRefusals(unittest.TestCase):
    """A card that would silently read 0 A must be refused instead."""

    def test_a_resistor_cannot_be_the_control(self):
        # R has no branch current unknown; reading one would mean inventing it
        r = _err("* f\nV1 1 0 1\nR1 1 0 1k\nF1 2 0 R1 2\nR2 2 0 1k\n.op\n.end\n")
        self.assertIn("no branch current", r["message"])
        self.assertIn("R1", r["message"])

    def test_an_undefined_control_is_named(self):
        r = _err("* f\nV1 1 0 1\nR1 1 0 1k\nF1 2 0 V9 2\nR2 2 0 1k\n.op\n.end\n")
        self.assertIn("'V9'", r["message"])
        self.assertIn("no card in this deck defines", r["message"])

    def test_the_control_position_must_hold_a_name(self):
        # "F1 2 0 4 2" is the positional reading that turns 4 into a node and
        # then reads 0 A from nowhere - the refusal says why
        r = _err("* f\nV1 1 0 1\nR1 1 0 1k\nF1 2 0 4 2\nR2 2 0 1k\n.op\n.end\n")
        self.assertIn("is not an element name", r["message"])

    def test_a_short_cccs_is_refused(self):
        r = _err("* f\nV1 1 0 1\nR1 1 0 1k\nF1 2 0\nR2 2 0 1k\n.op\n.end\n")
        self.assertIn("needs two nodes", r["message"])
        self.assertIn("Fname n+ n- vname gain", r["message"])

    def test_a_long_ccvs_is_refused(self):
        r = _err("* h\nV1 1 0 1\nR1 1 0 1k\nH1 2 0 V1 2 3\nR2 2 0 1k\n.op\n.end\n")
        self.assertIn("trailing", r["message"])

    def test_a_card_cannot_control_itself(self):
        # F1's own current does not exist to be read - the same reason R is out
        r = _err("* f\nV1 1 0 1\nR1 1 0 1k\nF1 2 0 F1 2\nR2 2 0 1k\n.op\n.end\n")
        self.assertIn("no branch current", r["message"])


class TestMutualInductanceAC(unittest.TestCase):
    """K at AC: the open-secondary ratio is M/L1 = k*sqrt(L2/L1)."""

    def test_an_ideal_coupling_doubles_the_voltage(self):
        # L2/L1 = 400u/100u = 4, so sqrt = 2 and k = 1 gives exactly 2x
        p = _ac(_XFMR % "K1 L1 L2 1\n")
        self.assertAlmostEqual(p["nodes_v"]["2"]["mag"] / p["nodes_v"]["3"]["mag"],
                               2.0, places=6)

    def test_the_ratio_is_proportional_to_k(self):
        # k = 0.9 -> 1.8, k = 0.5 -> 1.0, k = 0 -> nothing at all. A solver that
        # stamped k as if it were M would get 0.9/0.5/0 of the WRONG base.
        for k, want in ((0.9, 1.8), (0.5, 1.0)):
            p = _ac(_XFMR % ("K1 L1 L2 %g\n" % k))
            self.assertAlmostEqual(p["nodes_v"]["2"]["mag"] / p["nodes_v"]["3"]["mag"],
                                   want, places=6)

    def test_zero_coupling_is_no_coupling(self):
        p = _ac(_XFMR % "K1 L1 L2 0\n")
        self.assertLess(p["nodes_v"]["2"]["mag"], 1e-9)
        # and the same deck without a K card must agree with it bit for bit
        q = _ac(_XFMR % "")
        self.assertAlmostEqual(p["nodes_v"]["3"]["re"], q["nodes_v"]["3"]["re"], places=12)

    def test_a_negative_k_inverts_the_phase(self):
        # the winding is wound the other way: same magnitude, 180 degrees apart
        p = _ac(_XFMR % "K1 L1 L2 -1\n")
        a = p["nodes_v"]["2"]
        b = p["nodes_v"]["3"]
        self.assertAlmostEqual(a["mag"] / b["mag"], 2.0, places=6)
        self.assertAlmostEqual(abs(a["phase_deg"] - b["phase_deg"]), 180.0, places=4)

    def test_a_positive_k_stays_in_phase(self):
        # a phase difference of exactly 0 is the physical claim; the solver's
        # own resolution leaves ~1e-4 deg of noise between the two readings
        p = _ac(_XFMR % "K1 L1 L2 0.5\n")
        self.assertAlmostEqual(p["nodes_v"]["2"]["phase_deg"],
                               p["nodes_v"]["3"]["phase_deg"], delta=0.01)

    def test_an_ideal_transformer_ratio_holds_under_load(self):
        # k = 1 makes v2/v1 = sqrt(L2/L1) whatever the load is; that is the
        # property that distinguishes a real transformer model from two
        # independent inductors that happen to be near each other
        p = _ac("* k\nV1 1 0 AC 1\nR1 1 3 50\nL1 3 0 100u\nL2 2 0 400u\n"
                "K1 L1 L2 1\nR2 2 0 50\n.ac lin 2 1k 2k\n.end\n")
        self.assertAlmostEqual(p["nodes_v"]["2"]["mag"] / p["nodes_v"]["3"]["mag"],
                               2.0, places=6)

    def test_a_loose_coupling_sags_under_load(self):
        # k = 0.5 cannot hold the ratio once current is drawn: the leakage is
        # real, so the loaded ratio must be below the open-circuit 1.0
        p = _ac("* k\nV1 1 0 AC 1\nR1 1 3 50\nL1 3 0 100u\nL2 2 0 400u\n"
                "K1 L1 L2 0.5\nR2 2 0 50\n.ac lin 2 1k 2k\n.end\n")
        self.assertLess(p["nodes_v"]["2"]["mag"] / p["nodes_v"]["3"]["mag"], 1.0)

    def test_a_k_card_adds_no_unknown(self):
        # the same two inductors with and without K: K is a statement about
        # them, not a device, so the matrix size must not move
        with_k = _ac(_XFMR % "K1 L1 L2 0.5\n")
        without = _ac(_XFMR % "")
        n_with = len(with_k["nodes_v"])
        n_without = len(without["nodes_v"])
        self.assertEqual(n_with, n_without)
        op = _op("* k\nV1 1 0 1\nR1 1 3 1k\nL1 3 0 1m\nL2 2 0 1m\nK1 L1 L2 1\n"
                 "R2 2 0 1k\n.end\n")
        # nodes 1,2,3 + V1 + L1 + L2 = 6; a K row would make 7
        self.assertEqual(op["unknowns"], 6)


class TestMutualInductanceTransient(unittest.TestCase):
    """K at transient: the cross term M/h lands on both inductor rows."""

    _EQ = ("* t\nV1 1 0 sin(0 1 1k)\nR1 1 3 1\nL1 3 0 1m\nL2 2 0 1m\n"
           "K1 L1 L2 1\nR2 2 0 1meg\n.tran 10u 1m\n.end\n")

    def test_two_equal_windings_see_the_same_voltage(self):
        # k = 1 and L1 = L2 means the windings are the same winding: v must be
        # identical at EVERY step, not just in magnitude - a sign or factor
        # error in the cross term shows up here and nowhere else
        pts = _tran(self._EQ)
        worst = max(abs(p["nodes_v"]["2"] - p["nodes_v"]["3"]) for p in pts)
        self.assertEqual(worst, 0.0)
        self.assertGreater(max(abs(p["nodes_v"]["3"]) for p in pts), 0.5)

    def test_an_open_secondary_keeps_the_turns_ratio(self):
        # v2/v1 = k*sqrt(L2/L1) = 2 for k = 1 and 1.8 for k = 0.9, checked on
        # every sample where the drive is not near zero
        for k, want in ((1.0, 2.0), (0.9, 1.8), (0.5, 1.0)):
            net = ("* t\nV1 1 0 sin(0 1 1k)\nR1 1 3 1\nL1 3 0 100u\nL2 2 0 400u\n"
                   "K1 L1 L2 %g\nR2 2 0 1meg\n.tran 10u 2m\n.end\n" % k)
            rs = [p["nodes_v"]["2"] / p["nodes_v"]["3"] for p in _tran(net)
                  if abs(p["nodes_v"]["3"]) > 0.5]
            self.assertGreater(len(rs), 10)
            self.assertAlmostEqual(min(rs), want, places=5)
            self.assertAlmostEqual(max(rs), want, places=5)

    def test_a_k_card_cannot_change_a_dc_solve(self):
        # at DC d/dt is 0, both inductors are wires, and M has nothing to act
        # on. The deck below has a non-zero DC point (0.5 V) so "no change" is
        # a real statement rather than a comparison of zeros.
        base = ("* d\nV1 1 0 1\nR1 1 2 1k\nL1 2 3 1m\nL2 3 4 4m\nR2 4 0 1k\n%s.end\n")
        a = _op(base % "")
        b = _op(base % "K1 L1 L2 0.9\n")
        self.assertAlmostEqual(a["nodes_v"]["2"], 0.5, places=6)
        for node in a["nodes_v"]:
            self.assertAlmostEqual(a["nodes_v"][node], b["nodes_v"][node], places=12)

    def test_the_k_record_states_what_it_asserts(self):
        # M = k*sqrt(L1*L2) = 0.9*sqrt(1m*4m) = 0.9*2m = 1.8 mH
        v = _op("* d\nV1 1 0 1\nR1 1 2 1k\nL1 2 3 1m\nL2 3 4 4m\nK1 L1 L2 0.9\n"
                "R2 4 0 1k\n.end\n")
        rec = _el(v, "K1")
        self.assertEqual(rec["inductors"], ["L1", "L2"])
        self.assertAlmostEqual(rec["coupling"], 0.9, places=12)
        self.assertAlmostEqual(rec["mutual_h"], 0.0018, places=15)
        # a K card has no terminals, so it must not pretend to have a voltage
        self.assertNotIn("voltage_v", rec)


class TestMutualInductanceRefusals(unittest.TestCase):
    """|k| > 1 is not a clamped parameter, it is an impossible statement."""

    def test_k_above_one_is_refused(self):
        r = _err("* k\nV1 1 0 1\nL1 1 0 1m\nL2 2 0 1m\nK1 L1 L2 1.5\n"
                 "R2 2 0 1k\n.op\n.end\n")
        self.assertIn("outside [-1, 1]", r["message"])
        self.assertIn("1.5", r["message"])

    def test_the_boundary_itself_is_allowed(self):
        for k in ("1", "-1"):
            r = spice.simulate("* k\nV1 1 0 1\nR1 1 3 1k\nL1 3 0 1m\nL2 2 0 1m\n"
                               "K1 L1 L2 %s\nR2 2 0 1k\n.op\n.end\n" % k, analysis="op")
            self.assertEqual(r["status"], "ok", (k, r))
            self.assertAlmostEqual(abs(_el(r["value"], "K1")["coupling"]), 1.0, places=12)

    def test_self_coupling_is_refused(self):
        r = _err("* k\nV1 1 0 1\nL1 1 0 1m\nK1 L1 L1 0.9\n.op\n.end\n")
        self.assertIn("to itself", r["message"])

    def test_a_resistor_cannot_be_coupled(self):
        r = _err("* k\nV1 1 0 1\nL1 1 0 1m\nR1 2 0 1k\nK1 L1 R1 0.9\n.op\n.end\n")
        self.assertIn("is a R card", r["message"])
        self.assertIn("between two inductors", r["message"])

    def test_a_missing_inductor_is_named(self):
        r = _err("* k\nV1 1 0 1\nL1 1 0 1m\nK1 L1 L9 0.9\n.op\n.end\n")
        self.assertIn("'L9'", r["message"])
        self.assertIn("is not an element of this deck", r["message"])

    def test_a_k_card_needs_exactly_three_fields(self):
        for body in ("K1 L1 L2\n", "K1 L1 L2 0.5 x\n"):
            r = _err("* k\nV1 1 0 1\nL1 1 0 1m\nL2 2 0 1m\n%sR2 2 0 1k\n.op\n.end\n" % body)
            self.assertIn("exactly", r["message"])

    def test_a_negative_inductance_cannot_be_coupled(self):
        # M = k*sqrt(L1*L2) needs both radicands positive; a square root of a
        # product of a positive and a negative is not a mutual inductance
        r = _err("* k\nV1 1 0 1\nL1 1 0 -1m\nL2 2 0 1m\nK1 L1 L2 0.9\n"
                 "R2 2 0 1k\n.op\n.end\n")
        self.assertIn("both positive", r["message"])


class TestInsideASubcircuit(unittest.TestCase):
    """Names inside a body are instance-local; that is a prefix, not a wish."""

    def test_a_cccs_can_read_an_internal_source(self):
        # there is no top-level Vi in this deck: if the control name were not
        # scoped to the instance the card would have nothing to read
        v = _op("* s\nV1 1 0 1\nX1 5 0 blk\nR5 5 0 1k\n"
                ".subckt blk o g\nVi n1 g 1\nRi n1 g 1k\nF1 o g Vi 2\n.ends\n.end\n")
        self.assertAlmostEqual(v["nodes_v"]["5"], 2.0, places=6)
        self.assertAlmostEqual(_el(v, "x1.f1")["control_current_a"], -0.001, places=9)

    def test_a_k_can_couple_internal_inductors(self):
        v = _ac("* s\nV1 1 0 AC 1\nR1 1 2 1\nX1 2 3 xf\nR2 3 0 1meg\n"
                ".subckt xf p s\nL1 p 0 100u\nL2 s 0 400u\nK1 L1 L2 1\n.ends\n"
                ".ac lin 2 1k 2k\n.end\n")
        self.assertAlmostEqual(v["nodes_v"]["3"]["mag"] / v["nodes_v"]["2"]["mag"],
                               2.0, places=6)

    def test_a_top_level_namesake_does_not_capture_the_k(self):
        # a decoy L1 of a different value sits at top level; if the K card were
        # resolved globally it would couple 1 mH to 400 uH and the ratio would
        # be 0.632x, not 2x
        v = _ac("* s\nV1 1 0 AC 1\nR1 1 2 1\nX1 2 3 xf\nR2 3 0 1meg\n"
                "L1 9 0 1m\n"
                ".subckt xf p s\nL1 p 0 100u\nL2 s 0 400u\nK1 L1 L2 1\n.ends\n"
                ".ac lin 2 1k 2k\n.end\n")
        self.assertAlmostEqual(v["nodes_v"]["3"]["mag"] / v["nodes_v"]["2"]["mag"],
                               2.0, places=6)

    def test_two_instances_do_not_couple_to_each_other(self):
        # X1 is driven, X2 is not: X2's secondary must stay at zero. Cross-talk
        # between instances is the failure mode a shared name would produce.
        v = _ac("* s\nV1 1 0 AC 1\nR1 1 2 1\nX1 2 3 xf\nR2 3 0 1meg\n"
                "X2 4 5 xf\nR4 4 0 1k\nR5 5 0 1meg\n"
                ".subckt xf p s\nL1 p 0 100u\nL2 s 0 400u\nK1 L1 L2 1\n.ends\n"
                ".ac lin 2 1k 2k\n.end\n")
        self.assertGreater(v["nodes_v"]["3"]["mag"], 0.5)
        self.assertLess(v["nodes_v"]["5"]["mag"], 1e-9)


class RealEngineAgrees(unittest.TestCase):
    """The builtin answer against ngspice, when one is installed.

    These are the negative controls of this file: the hand-derived numbers above
    could in principle be self-consistently wrong in the same way as the code.
    ngspice-47 has an independent implementation of all three cards, and on
    these linear decks the agreement is exact (0.0), not merely close.
    """

    def setUp(self):
        self.argv = spice.find_ngspice()

    def _cross(self, net, analysis="op"):
        if not self.argv:
            self.skipTest("no ngspice on this machine")
        r = spice.simulate(net, analysis=analysis, engine="ngspice", cross_check=True)
        assert r["status"] == "ok", r
        return r["cross_check"]

    def test_a_cccs_in_a_dc_point(self):
        cc = self._cross("* f\nV1 1 0 1\nR1 1 0 1k\nF1 2 0 V1 2\nR2 2 0 1k\n"
                         ".op\n.end\n")
        self.assertGreaterEqual(cc["compared_nodes"], 2)
        self.assertLess(cc["max_abs_diff_v"], 1e-9, cc)

    def test_a_ccvs_in_a_dc_point(self):
        cc = self._cross("* h\nV1 1 0 1\nR1 1 0 1k\nH1 2 0 V1 2\nR2 2 0 1k\n"
                         ".op\n.end\n")
        self.assertLess(cc["max_abs_diff_v"], 1e-9, cc)

    def test_a_ccvs_with_a_large_gain(self):
        # a 1 V reading is reported to 9 decimal places, so the floor of this
        # comparison is 1e-9 rather than 0; the 2 mV cases above reach 0.0
        cc = self._cross("* h\nV1 1 0 1\nR1 1 0 1k\nH1 2 0 V1 1000\nR2 2 0 1k\n"
                         ".op\n.end\n")
        self.assertLessEqual(cc["max_abs_diff_v"], 1e-9, cc)

    def test_both_in_ac(self):
        for body in ("F1 2 0 V1 2", "H1 2 0 V1 2"):
            cc = self._cross("* d\nV1 1 0 DC 1 AC 1\nR1 1 0 1k\n%s\nR2 2 0 1k\n"
                             ".op\n.ac lin 2 100 1k\n.end\n" % body, analysis="ac")
            self.assertLess(cc["max_abs_diff_v"], 1e-9, (body, cc))

    def test_mutual_inductance_in_ac(self):
        for k in ("1", "0.5", "-0.5"):
            cc = self._cross("* k\nV1 1 0 AC 1\nR1 1 3 1\nL1 3 0 100u\n"
                             "L2 2 0 400u\nK1 L1 L2 %s\nR2 2 0 1meg\n"
                             ".op\n.ac lin 2 1k 2k\n.end\n" % k, analysis="ac")
            self.assertEqual(cc["max_abs_diff_v"], 0.0, (k, cc))

    def test_mutual_inductance_with_a_loaded_secondary(self):
        cc = self._cross("* k\nV1 1 0 AC 1\nR1 1 3 50\nL1 3 0 100u\nL2 2 0 400u\n"
                         "K1 L1 L2 1\nR2 2 0 50\n.op\n.ac lin 2 1k 2k\n.end\n",
                         analysis="ac")
        self.assertEqual(cc["max_abs_diff_v"], 0.0, cc)


if __name__ == "__main__":
    unittest.main()
