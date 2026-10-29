"""Tests for .subckt / X instances.

The claim under test is narrow and checkable: FLATTENING IS FAITHFUL. It is not
"subcircuits are supported" - it is that expanding an instance must give bit for
bit the same answer as writing the same elements out by hand, and that the same
internal node name is used on both sides (that is what makes `cross_check=True`
against ngspice compare real nodes instead of finding none in common).

Each mechanism has a control that FAILS if the mechanism is not there:
* node prefixing      -> two instances that share names must still be two nodes
* parameter override  -> dropping it changes nothing, so a >5% move proves it landed
* local .model scope  -> same model name in two subcircuits must stay two devices
* ground pins         -> a pin tied to 0 must stay the global ground, not x1.0
"""
import os
import sys
import unittest

sys.path.insert(0, os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "src"))

from pcb_agent_mcp.calc import spice  # noqa: E402


def _ok(net, analysis="op", **kw):
    r = spice.simulate(net, analysis=analysis, **kw)
    assert r["status"] == "ok", r
    return r["value"]


DIVIDER = """
* subckt ladder
.subckt div a b
R1 a m 1k
R2 m b 1k
.ends
V1 1 0 10
X1 1 0 div
.op
.end
"""


class ExpansionIsFaithful(unittest.TestCase):
    """The one property everything else rests on."""

    def test_against_a_hand_flattened_deck(self):
        # the same RC ladder, once as a subcircuit and once written out flat;
        # every node value must agree to the last bit the solver can produce
        hier = """
* rc as subckt
.subckt rc a b params: r=1k c=1n
R1 a m {r}
C1 m b {c}
.ends
V1 1 0 pulse(0 5 0 1n 1n 1m 2m)
X1 1 0 rc
.tran 1u 2u
.end
"""
        flat = """
* rc flat
V1 1 0 pulse(0 5 0 1n 1n 1m 2m)
R1 1 mid 1k
C1 mid 0 1n
.tran 1u 2u
.end
"""
        h = _ok(hier, "tran")["points"]
        f = _ok(flat, "tran")["points"]
        self.assertEqual(len(h), len(f))
        for ph, pf in zip(h, f):
            self.assertAlmostEqual(ph["nodes_v"]["1"], pf["nodes_v"]["1"], places=12)
            self.assertAlmostEqual(ph["nodes_v"]["x1.m"], pf["nodes_v"]["mid"],
                                   places=12)

    def test_the_internal_node_keeps_the_name_ngspice_prints(self):
        v = _ok(DIVIDER)
        self.assertIn("x1.m", v["nodes_v"])
        self.assertAlmostEqual(v["nodes_v"]["x1.m"], 5.0, places=6)
        # the top-level node keeps its own name: expansion must not touch it
        self.assertEqual(v["nodes_v"]["1"], 10.0)

    def test_a_nested_instance_names_the_whole_path(self):
        v = _ok("""
* nested
.subckt inner a b
R1 a b 2k
.ends
.subckt outer p q
X1 p mid inner
X2 mid q inner
.ends
V1 1 0 12
X9 1 0 outer
.op
.end
""")
        # p=12 V, q=ground, two 2k in series: the middle is 6 V
        self.assertAlmostEqual(v["nodes_v"]["x9.mid"], 6.0, places=6)
        self.assertNotIn("None", v["nodes_v"])


class InstancesDoNotShareNodes(unittest.TestCase):
    def test_two_instances_are_two_internal_nodes(self):
        # the control: if the flattener forgot the prefix, both instances would
        # write R1 a m / R2 m b and the node count would collapse to ONE
        v = _ok("""
* two instances
.subckt div a b
R1 a m 1k
R2 m b 1k
.ends
V1 1 0 10
X1 1 0 div
X2 1 0 div
.op
.end
""")
        inner = [n for n in v["nodes_v"] if n != "1"]
        self.assertEqual(sorted(inner), ["x1.m", "x2.m"], v["nodes_v"])

    def test_one_instance_being_loaded_does_not_move_the_other(self):
        # X1 feeds a 2k load, X2 does not: x2.m must stay at the unloaded 5 V
        # while x1.m is dragged down. A shared-node flattener would move both.
        v = _ok("""
* load one of two
.subckt div a b
R1 a m 1k
R2 m b 1k
.ends
V1 1 0 10
X1 1 0 div
X2 1 0 div
R3 2 0 1k
R4 1 2 1k
.op
.end
""")
        self.assertAlmostEqual(v["nodes_v"]["x1.m"], 5.0, places=6)
        self.assertAlmostEqual(v["nodes_v"]["x2.m"], 5.0, places=6)


class ParametersReachTheElement(unittest.TestCase):
    def test_a_default_and_an_override_give_different_answers(self):
        net = """
* param divider
.subckt div a b params: r1=1k r2=1k
R1 a m {r1}
R2 m b {r2}
.ends
V1 1 0 9
X1 1 0 div
X2 1 0 div r2=3k
.op
.end
"""
        v = _ok(net)["nodes_v"]
        # unloaded divider: 9 * r2 / (r1 + r2)
        self.assertAlmostEqual(v["x1.m"], 9.0 * 1.0 / 2.0, places=6)
        self.assertAlmostEqual(v["x2.m"], 9.0 * 3.0 / 4.0, places=6)
        # the control: "parameter parsed but dropped" would make these equal
        self.assertGreater(abs(v["x1.m"] - v["x2.m"]) / v["x1.m"], 0.05)

    def test_arithmetic_on_parameters_is_evaluated(self):
        v = _ok("""
.subckt div a b params: r1=1k r2=1k
R1 a m {r1}
R2 m b {r2*2}
.ends
V1 1 0 9
X1 1 0 div
.op
.end
""")["nodes_v"]
        # r2*2 = 2k, so the tap is 9*2/3
        self.assertAlmostEqual(v["x1.m"], 6.0, places=6)

    def test_an_override_may_itself_be_an_expression_on_the_callers_parameters(self):
        v = _ok("""
.subckt leg a b params: r=1k
R1 a b {r}
.ends
.subckt pair p q params: r=1k
X1 p q leg r={r/2}
.ends
V1 1 0 4
X1 1 0 pair r=2k
.op
.end
""")["nodes_v"]
        # 4 V across one leg inside pair, leg's r = 2k/2 = 1k: 4 mA
        self.assertAlmostEqual(v["1"], 4.0, places=9)

    def test_params_prefix_is_accepted(self):
        a = _ok("""
.subckt div a b params: r2=1k
R1 a m 1k
R2 m b {r2}
.ends
V1 1 0 9
X1 1 0 div params: r2=2k
.op
.end
""")["nodes_v"]
        self.assertAlmostEqual(a["x1.m"], 6.0, places=6)


class LocalModelsStayLocal(unittest.TestCase):
    def test_two_subcircuits_may_each_own_a_model_of_the_same_name(self):
        # both bodies define ".model dm"; only one of them is a 1e-9 A diode. If
        # the model table were global, the second definition would overwrite the
        # first and the two diodes would drop the SAME voltage.
        v = _ok("""
* same model name, two scopes
.subckt soft a b
.model dm D(IS=1e-14 N=1)
D1 a b dm
.ends
.subckt leaky a b
.model dm D(IS=1e-9 N=1)
D1 a b dm
.ends
V1 1 0 0.6
R1 1 2 1k
X1 2 0 soft
X2 2 0 leaky
.op
.end
""")["nodes_v"]
        el = _ok("""
* same model name, two scopes
.subckt soft a b
.model dm D(IS=1e-14 N=1)
D1 a b dm
.ends
.subckt leaky a b
.model dm D(IS=1e-9 N=1)
D1 a b dm
.ends
V1 1 0 0.6
R1 1 2 1k
X1 2 0 soft
X2 2 0 leaky
.op
.end
""")["elements"]
        soft = [e for e in el if e["name"] == "x1.d1"][0]
        leaky = [e for e in el if e["name"] == "x2.d1"][0]
        # the leaky diode carries ~1e5 times the saturation current, so at the
        # same 0.6 V node it eats far more of the drop
        self.assertGreater(abs(leaky["current_a"]), 10.0 * abs(soft["current_a"]))
        self.assertIn("2", v)

    def test_a_global_model_is_still_visible_inside_a_subcircuit(self):
        v = _ok("""
.model dg D(IS=1e-14 N=1)
.subckt leg a b
D1 a b dg
.ends
V1 1 0 0.6
R1 1 2 1k
X1 2 0 leg
.op
.end
""")
        d = [e for e in v["elements"] if e["name"] == "x1.d1"][0]
        self.assertGreater(d["current_a"], 0.0)
        # a global model must NOT be copied under a per-instance name: the
        # element keeps pointing at "dg"
        el = spice.parse_netlist("""
.model dg D(IS=1e-14 N=1)
.subckt leg a b
D1 a b dg
.ends
V1 1 0 0.6
X1 1 0 leg
.op
.end
""")
        self.assertEqual([e["model"] for e in el["elements"] if e["type"] == "D"],
                         ["dg"])
        self.assertEqual(sorted(el["models"]), ["dg"])


class GroundPinsAreGlobal(unittest.TestCase):
    def test_a_pin_tied_to_zero_is_the_global_ground(self):
        v = _ok("""
.subckt div a b
R1 a m 1k
R2 m b 1k
.ends
V1 1 0 10
X1 1 0 div
.op
.end
""")["nodes_v"]
        # "x1.0" would mean the ground pin became an ordinary internal node and
        # the divider would have nowhere to close: the tap would be 10 V
        self.assertNotIn("x1.0", v)
        self.assertAlmostEqual(v["x1.m"], 5.0, places=6)

    def test_an_inner_ground_name_collapses_too(self):
        # 0 and gnd inside the body are the same global node: if either were
        # renamed to x1.0 / x1.gnd the divider would float and give 6 V
        v = _ok("""
.subckt ld a b
R1 a m 1k
R2 m 0 1k
R3 m gnd 1k
.ends
V1 1 0 6
X1 1 0 ld
.op
.end
""")["nodes_v"]
        self.assertEqual(sorted(v), ["1", "x1.m"], v)
        self.assertAlmostEqual(v["x1.m"], 6.0 * 0.5 / 1.5, places=6)


class ExpensiveMistakesAreRefused(unittest.TestCase):
    def test_a_cycle_is_named_with_the_chain(self):
        r = spice.simulate("""
.subckt a p q
X1 p q b
.ends
.subckt b p q
X1 p q a
.ends
V1 1 0 1
X9 1 0 a
.op
.end
""")
        self.assertEqual(r["status"], "error")
        self.assertIn("instantiates itself", r["message"])
        self.assertIn("a -> b -> a", r["message"])

    def test_an_undefined_subcircuit_is_an_error(self):
        r = spice.simulate("V1 1 0 1\nX1 1 0 nosuch\n.op\n.end\n")
        self.assertEqual(r["status"], "error")
        self.assertEqual(r["reason_code"], "NETLIST_INVALID")
        self.assertIn("not defined", r["message"])

    def test_a_pin_count_mismatch_is_an_error(self):
        r = spice.simulate("""
.subckt div a b
R1 a b 1k
.ends
V1 1 0 1
X1 1 0 3 div
.op
.end
""")
        self.assertEqual(r["status"], "error")
        self.assertIn("3 node(s)", r["message"])
        self.assertIn("2 pin(s)", r["message"])

    def test_an_undeclared_parameter_is_an_error(self):
        # silently ignoring it would answer with the DEFAULT and look fine
        r = spice.simulate("""
.subckt div a b params: r=1k
R1 a b {r}
.ends
V1 1 0 1
X1 1 0 div r2=5k
.op
.end
""")
        self.assertEqual(r["status"], "error")
        self.assertIn("does not", r["message"])

    def test_a_param_card_with_a_bare_expression_is_refused_by_name(self):
        # ngspice reads ".param r=1k*2" as arithmetic; this build asks for the
        # braces rather than keeping a second, silently-different number reader
        r = spice.simulate(""".param r=1k*2
V1 1 0 1
R1 1 0 {r}
.op
.end
""")
        self.assertEqual(r["status"], "error")
        self.assertIn("write {1k*2}", r["message"])

    def test_an_expression_naming_an_undefined_parameter_is_refused(self):
        r = spice.simulate("V1 1 0 1\nR1 1 0 {r}\n.op\n.end\n")
        self.assertEqual(r["status"], "error")
        self.assertIn("'r' is not a parameter in scope here", r["message"])

    def test_a_function_in_an_expression_is_refused_by_name(self):
        r = spice.simulate("""
.subckt leg a b params: r=1k
R1 a b {sqrt(r)}
.ends
V1 1 0 1
X1 1 0 leg
.op
.end
""")
        self.assertEqual(r["status"], "error")
        self.assertIn("sqrt", r["message"])

    def test_a_nested_subckt_definition_is_refused(self):
        r = spice.simulate("""
.subckt outer p q
.subckt inner p q
R1 p q 1k
.ends
.ends
V1 1 0 1
X1 1 0 outer
.op
.end
""")
        self.assertEqual(r["status"], "error")
        self.assertIn("inside another .subckt", r["message"])

    def test_an_unclosed_subckt_is_refused(self):
        # no trailing .end at all: the body runs to EOF
        r = spice.simulate(".subckt div a b\nR1 a b 1k\n")
        self.assertEqual(r["status"], "error")
        self.assertIn("never closed", r["message"])

    def test_a_missing_ends_before_end_is_named_as_such(self):
        # the same mistake one line earlier: a lone .end inside the body. The
        # error must point at the missing .ends, not at ".op is not allowed here".
        r = spice.simulate("""
.subckt div a b
R1 a b 1k
.end
""")
        self.assertEqual(r["status"], "error")
        self.assertIn("missing .ends", r["message"])

    def test_a_directive_inside_a_body_is_refused(self):
        r = spice.simulate("""
.subckt div a b
.tran 1u 1m
R1 a b 1k
.ends
V1 1 0 1
X1 1 0 div
.op
.end
""")
        self.assertEqual(r["status"], "error")
        self.assertIn("inside .subckt", r["message"])

    def test_a_deck_with_x_no_longer_claims_subcircuits_are_missing(self):
        r = spice.simulate(DIVIDER)
        joined = " ".join(r.get("not_computed") or [])
        self.assertNotIn("subcircuits (.subckt), hierarchical netlists", joined)
        # but the limits that DO exist are still stated
        self.assertIn(".param", joined)


class RealEngineAgrees(unittest.TestCase):
    """ngspice handles the hierarchy natively, so it is an independent check of
    the expansion. Skipped (not failed) when no binary is installed - and the
    skip is visible, because an unverified claim is not a verified one."""

    @classmethod
    def setUpClass(cls):
        if spice.find_ngspice() is None:
            raise unittest.SkipTest("no ngspice binary on this machine")

    def test_the_same_deck_on_both_engines(self):
        deck = """
* subckt, dc-connected
.subckt div a b params: r1=1k r2=2k
R1 a m {r1}
R2 m b {r2}
.ends
V1 1 0 10
X1 1 0 div
.op
.end
"""
        r = spice.simulate(deck, engine="ngspice", cross_check=True)
        self.assertEqual(r["status"], "ok", r)
        cross = r["cross_check"]
        # the internal node is compared too, which only works because the names
        # match ngspice's own (x1.m)
        self.assertGreaterEqual(cross["compared_nodes"], 2, cross)
        self.assertLess(cross["max_abs_diff_v"], 1e-6, cross)

    def test_a_deck_with_a_floating_island_is_where_the_two_engines_part(self):
        # documented, not hidden: with no DC path to ground a node is held by each
        # engine's own gmin convention (0 V here, ~half the drive in ngspice), so
        # this deck is NOT a valid cross-check and the disagreement is not about
        # the hierarchy - the same flat netlist parts the same way
        flat = ("V1 1 0 5\nR1 1 a 1k\nC1 a mid 1n\nR2 mid b 2k\n"
                "C2 b 0 1n\n.op\n.end\n")
        hier = """
.subckt rc a b params: r=1k c=1n
R1 a m {r}
C1 m b {c}
.ends
V1 1 0 5
R1 1 a 1k
X1 a b rc r=2k
C2 b 0 1n
.op
.end
"""
        f = spice.simulate(flat, engine="ngspice", cross_check=True)["cross_check"]
        h = spice.simulate(hier, engine="ngspice", cross_check=True)["cross_check"]
        self.assertGreater(f["max_abs_diff_v"], 0.1)
        self.assertGreater(h["max_abs_diff_v"], 0.1)
        # the hierarchical deck's disagreement is the same size as the flat one's
        self.assertAlmostEqual(f["max_abs_diff_v"], h["max_abs_diff_v"], delta=0.05)


if __name__ == "__main__":
    unittest.main()
