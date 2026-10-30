"""Tests for behavioural sources (B cards): the expression language and its edges.

The claim is narrow: a B source is an ordinary source whose value is an
expression over node voltages, parameters and time, and every expression shape
that is NOT implemented is refused by name instead of being approximated. Each
mechanism carries a control that FAILS when the mechanism is absent:

* a constant V=5          -> change it to V=7 and the node must move (a dropped
                             card would leave the node floating at 0 V)
* V(2)*2                  -> the answer is the ARGUMENT times two, so a source
                             that ignored its node reference would sit at 0 V
* V(2)*V(2)               -> nonlinear: a linear-only implementation that
                             re-used the first iterate would give 3 V, not 9 V
* if(V(2)>3, 1m, 10m)     -> a comparison that never fired would give 10 mA,
                             which is a 10x different current through the load
* time in .tran           -> V=time*1000 is 1 V at 1 ms only if t is real time
* derivative              -> sqrt(V) at V=0 has no centred derivative; without
                             the one-sided fallback the run dies with a maths
                             error instead of converging
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

def _nodes(net, analysis="op"):
    return _ok(net, analysis)["nodes_v"]

def _err(net, analysis="op"):
    r = spice.simulate(net, analysis=analysis)
    assert r["status"] == "error", r
    return r

def _b(name, expr, load="R1 1 0 1k"):
    """A one-node-probe deck: B drives node 1, and R1 loads it."""
    return "* b\nV1 2 0 dc 3\nV3 3 0 dc 0.5\nB1 1 0 %s\n%s\n.op\n.end\n" % (expr, load)

class ConstantExpressions(unittest.TestCase):
    def test_a_constant_voltage_source(self):
        v = _nodes("* b\nB1 1 0 V=5\nR1 1 0 1k\n.op\n.end\n")
        self.assertAlmostEqual(v["1"], 5.0, places=9)

    def test_changing_the_constant_changes_the_answer(self):
        # THE control: a card that was parsed and then dropped leaves node 1
        # floating, and a floating node does not read 7 V
        v = _nodes("* b\nB1 1 0 V=7\nR1 1 0 1k\n.op\n.end\n")
        self.assertAlmostEqual(v["1"], 7.0, places=9)

    def test_a_constant_current_source_into_a_resistor(self):
        # SPICE's convention: +I flows from n+ to n- INSIDE the source, so node 1
        # is pulled DOWN to -1 V here. That is the convention, not a sign bug.
        v = _nodes("* b\nB1 1 0 I=1m\nR1 1 0 1k\n.op\n.end\n")
        self.assertAlmostEqual(v["1"], -1.0, places=6)

    def test_engineering_suffixes_are_numbers_in_an_expression(self):
        # unlike .param (where a bare "1k*2" is refused), an expression has no
        # second reading, so the suffixes are simply numbers
        v = _nodes("* b\nB1 1 0 V=1k*2\n.op\n.end\n")
        self.assertAlmostEqual(v["1"], 2000.0, places=6)

class NodeReferences(unittest.TestCase):
    def test_a_node_voltage_reaches_the_expression(self):
        self.assertAlmostEqual(_nodes(_b("B1", "V=V(2)*2"))["1"], 6.0, places=9)

    def test_a_differential_reference(self):
        # V(2,3) is 3 - 0.5 = 2.5; times 4 is 10. Reading only the first node
        # would give 12, and reading only the second would give 2.
        self.assertAlmostEqual(_nodes(_b("B1", "V=V(2,3)*4"))["1"], 10.0, places=9)

    def test_a_ground_reference_is_the_same_as_one_node(self):
        self.assertAlmostEqual(_nodes(_b("B1", "V=V(2,0)*2"))["1"], 6.0, places=9)

    def test_a_nonlinear_expression_is_solved_not_frozen(self):
        # 3^2 is 9; an implementation that evaluated the expression once at the
        # first iterate (0 V) would leave this at 0 V
        self.assertAlmostEqual(_nodes(_b("B1", "V=V(2)*V(2)"))["1"], 9.0, places=6)

    def test_a_sqrt_source_converges_through_the_domain_edge(self):
        # sqrt(V) at V=0: the CENTRED difference steps to -1e-6 and dies with a
        # maths error, so this test only passes with the one-sided fallback
        self.assertAlmostEqual(_nodes(_b("B1", "V=sqrt(V(2))"))["1"],
                               3.0 ** 0.5, places=6)

    def test_a_current_source_that_depends_on_a_node(self):
        v = _nodes("* b\nV1 2 0 dc 2\nB1 1 0 I=V(2)*0.001\nR1 1 0 1k\n.op\n.end\n")
        self.assertAlmostEqual(v["1"], -2.0, places=6)

class Arithmetic(unittest.TestCase):
    def test_the_five_operators_and_power(self):
        cases = [("1+2", 3.0), ("10-4", 6.0), ("3*4", 12.0), ("9/2", 4.5),
                 ("2**10", 1024.0), ("2^10", 1024.0), ("-(-2)", 2.0),
                 ("2*3+1", 7.0), ("2*(3+1)", 8.0)]
        for expr, want in cases:
            self.assertAlmostEqual(_nodes(_b("B1", "V=" + expr))["1"], want,
                                   places=6, msg=expr)

    def test_comparisons_are_one_or_zero(self):
        self.assertAlmostEqual(_nodes(_b("B1", "V=V(2)>1"))["1"], 1.0, places=6)
        self.assertAlmostEqual(_nodes(_b("B1", "V=V(2)<1"))["1"], 0.0, places=6)

    def test_boolean_operators_and_the_if_function(self):
        # 3 > 1 and 3 < 5 -> 1, then if() picks the first branch: 5
        self.assertAlmostEqual(
            _nodes(_b("B1", "V=if(V(2)>1 && V(2)<5, 5, 6)"))["1"], 5.0, places=6)
        self.assertAlmostEqual(
            _nodes(_b("B1", "V=if(V(2)>9 || V(2)==3, 5, 6)"))["1"], 5.0, places=6)
        self.assertAlmostEqual(
            _nodes(_b("B1", "V=if(!(V(2)>9), 5, 6)"))["1"], 5.0, places=6)

    def test_the_if_argument_that_is_not_taken_does_not_change_the_answer(self):
        # control for the line above: swapping the branches must move the answer
        self.assertAlmostEqual(
            _nodes(_b("B1", "V=if(V(2)>9, 5, 6)"))["1"], 6.0, places=6)

    def test_functions(self):
        cases = [("abs(-3)", 3.0), ("min(4,2)", 2.0), ("max(4,2)", 4.0),
                 ("exp(0)", 1.0), ("ln(exp(1))", 1.0), ("log(exp(2))", 2.0),
                 ("log10(1000)", 3.0), ("floor(2.7)", 2.0), ("ceil(2.1)", 3.0),
                 ("pow(2,8)", 256.0), ("uramp(-1)", 0.0), ("uramp(2)", 2.0),
                 ("u(-1)", 0.0), ("u(2)", 1.0), ("sgn(-3)", -1.0),
                 ("atan2(1,1)*4", 3.14159265)]
        for expr, want in cases:
            self.assertAlmostEqual(_nodes(_b("B1", "V=" + expr))["1"], want,
                                   places=5, msg=expr)

    def test_the_ternary_operator_and_if_agree(self):
        a = _nodes(_b("B1", "V=(V(2)>2)?4:1"))["1"]
        b = _nodes(_b("B1", "V=if(V(2)>2, 4, 1)"))["1"]
        self.assertAlmostEqual(a, 4.0, places=6)
        self.assertAlmostEqual(b, a, places=9)

    def test_a_ternary_with_no_colon_is_refused(self):
        r = _err("* b\nB1 1 0 V=(V(2)>2)?4\nR1 1 0 1k\n.op\n.end\n")
        self.assertEqual(r["reason_code"], "NETLIST_INVALID")
        self.assertIn("no matching ':'", r["message"])

    def test_a_node_named_like_a_python_keyword(self):
        # "in" is a Python keyword and a perfectly ordinary net name; the node
        # call is replaced by a placeholder before parsing, so this is a name and
        # not a syntax error
        v = _nodes("* b\nV1 in 0 dc 2\nB1 1 0 V=3*V(in)\nR1 1 0 1k\n.op\n.end\n")
        self.assertAlmostEqual(v["1"], 6.0, places=6)

    def test_pi_is_a_constant(self):
        self.assertAlmostEqual(_nodes(_b("B1", "V=sin(pi/2)"))["1"], 1.0, places=6)

class TimeAndTran(unittest.TestCase):
    def test_time_reaches_a_transient(self):
        v = _ok("* b\nB1 1 0 V=time*1000\nR1 1 0 1k\n.tran 10u 1m\n.end\n",
                analysis="tran")
        self.assertAlmostEqual(v["points"][0]["nodes_v"]["1"], 0.0, places=9)
        self.assertAlmostEqual(v["points"][-1]["nodes_v"]["1"], 1.0, places=6)

    def test_time_is_zero_in_an_operating_point(self):
        # .op has no time axis; t is 0 there, and saying so is better than
        # refusing the deck
        v = _nodes("* b\nB1 1 0 V=time*1000+2\nR1 1 0 1k\n.op\n.end\n")
        self.assertAlmostEqual(v["1"], 2.0, places=6)

class Parameters(unittest.TestCase):
    def test_a_global_parameter_in_an_expression(self):
        v = _nodes("* b\n.param k=3\nV1 2 0 dc 2\nB1 1 0 V=k*V(2)\n"
                   "R1 1 0 1k\n.op\n.end\n")
        self.assertAlmostEqual(v["1"], 6.0, places=6)

    def test_braces_around_a_parameter_are_just_grouping(self):
        v = _nodes("* b\n.param k=3\nV1 2 0 dc 2\nB1 1 0 V={k}*V(2)\n"
                   "R1 1 0 1k\n.op\n.end\n")
        self.assertAlmostEqual(v["1"], 6.0, places=6)

    def test_an_unknown_name_is_refused_with_the_line(self):
        r = _err("* b\nB1 1 0 V=q*2\nR1 1 0 1k\n.op\n.end\n")
        self.assertEqual(r["reason_code"], "NETLIST_INVALID")
        self.assertIn("'q' is not a parameter in scope here", r["message"])

    def test_a_subcircuit_parameter_reaches_the_expression(self):
        net = """* b
.subckt amp in out params: gain=2
B1 out 0 V=gain*V(in)
.ends
V1 2 0 dc 3
X1 2 1 amp gain=4
R1 1 0 1k
.op
.end
"""
        self.assertAlmostEqual(_nodes(net)["1"], 12.0, places=6)

    def test_a_body_expression_reads_the_instances_own_nodes(self):
        # the same subcircuit twice, driven differently: if the body's V(in) were
        # not remapped per instance, both outputs would read the same number
        net = """* b
.subckt dbl in out
B1 out 0 V=2*V(in)
.ends
V1 2 0 dc 3
V2 4 0 dc 5
X1 2 1 dbl
X2 4 3 dbl
.op
.end
"""
        n = _nodes(net)
        self.assertAlmostEqual(n["1"], 6.0, places=6)
        self.assertAlmostEqual(n["3"], 10.0, places=6)

class AC(unittest.TestCase):
    def test_ac_gain_of_a_linearised_b_source(self):
        # V(1) = 2*V(2) is a plain gain, so the AC answer is 2 at every frequency
        v = _ok("* b\nV1 2 0 dc 3 ac 1\nB1 1 0 V=V(2)*2\nR1 1 0 1k\n"
                ".ac dec 1 1k 10k\n.end\n", analysis="ac")
        for p in v["points"]:
            self.assertAlmostEqual(p["nodes_v"]["1"]["mag"], 2.0, places=6)

    def test_ac_gain_of_a_nonlinear_b_source_is_the_bias_slope(self):
        # I = 0.001*V(2)^2 at V(2)=2 is a transconductance of 4 mA/V, so node 1
        # (1k to ground, and the current is pulled down) is -4 V per volt. That
        # is the small-signal definition, and 0.004 would be the "no derivative"
        # answer - a 1000x difference.
        v = _ok("* b\nV1 2 0 dc 2 ac 1\nB1 1 0 I=0.001*V(2)*V(2)\nR1 1 0 1k\n"
                ".ac dec 1 1k 10k\n.end\n", analysis="ac")
        self.assertAlmostEqual(v["points"][0]["nodes_v"]["1"]["mag"], 4.0, places=5)

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

    def test_a_nonlinear_b_source_in_a_dc_point(self):
        cc = self._cross("* b\nV1 2 0 dc 2\nB1 1 0 V=V(2)*V(2)\nR1 1 0 1k\n"
                         ".op\n.end\n")
        self.assertGreaterEqual(cc["compared_nodes"], 2)
        self.assertLess(cc["max_abs_diff_v"], 1e-6, cc)

    def test_a_conditional_in_a_dc_point(self):
        # the "? :" spelling, because ngspice-47 has no if() function (it reports
        # "no such function 'if'"), and a cross-check compares decks, not
        # dialects. The builtin takes both spellings - see Arithmetic.
        cc = self._cross("* b\nV1 2 0 dc 4\nB1 1 0 V=(V(2)>3)?2:1\n"
                         "R1 1 0 1k\n.op\n.end\n")
        self.assertLess(cc["max_abs_diff_v"], 1e-6, cc)

    def test_a_linearised_b_source_in_ac(self):
        cc = self._cross("* b\nV1 2 0 dc 2 ac 1\nB1 1 0 I=exp(V(2))*0.001\n"
                         "R1 1 0 1k\n.op\n.ac dec 1 1k 100k\n.end\n", analysis="ac")
        self.assertLess(cc["max_abs_diff_v"], 1e-6, cc)

class Refusals(unittest.TestCase):
    def test_laplace_is_refused_by_name(self):
        r = _err("* b\nB1 1 0 V=laplace(1/(1+s))\nR1 1 0 1k\n.op\n.end\n")
        self.assertEqual(r["reason_code"], "NETLIST_INVALID")
        self.assertIn("laplace() is not implemented here", r["message"])

    def test_the_current_of_another_element_is_refused_by_name(self):
        r = _err("* b\nV1 2 0 1\nB1 1 0 V=I(V1)\nR1 1 0 1k\n.op\n.end\n")
        self.assertEqual(r["reason_code"], "NETLIST_INVALID")
        self.assertIn("I(...) is not implemented here", r["message"])

    def test_an_unknown_function_lists_what_exists(self):
        r = _err("* b\nB1 1 0 V=tanh2(V(2))\nR1 1 0 1k\n.op\n.end\n")
        self.assertEqual(r["reason_code"], "NETLIST_INVALID")
        self.assertIn("implemented:", r["message"])

    def test_a_node_nobody_connects_is_refused(self):
        # V(9) would read 0 V in a permissive implementation, which turns a typo
        # into a silent constant
        r = _err("* b\nB1 1 0 V=V(9)*2\nR1 1 0 1k\n.op\n.end\n")
        self.assertEqual(r["reason_code"], "NETLIST_INVALID")
        self.assertIn("reads node '9'", r["message"])

    def test_a_missing_v_or_i_is_refused(self):
        r = _err("* b\nB1 1 0 5\nR1 1 0 1k\n.op\n.end\n")
        self.assertEqual(r["reason_code"], "NETLIST_INVALID")
        self.assertIn("has no V=<expr> or I=<expr>", r["message"])

    def test_a_syntax_error_names_the_expression(self):
        r = _err("* b\nB1 1 0 V=(1+2\nR1 1 0 1k\n.op\n.end\n")
        self.assertEqual(r["reason_code"], "NETLIST_INVALID")
        self.assertIn("cannot read the expression", r["message"])

    def test_giving_both_v_and_i_is_refused(self):
        # "2 I=3" would be reported as a syntax error in the expression, which
        # points at the wrong thing: the card is ambiguous, not malformed
        r = _err("* b\nB1 1 0 V=2 I=3\nR1 1 0 1k\n.op\n.end\n")
        self.assertEqual(r["reason_code"], "NETLIST_INVALID")
        self.assertIn("gives both V= and I=", r["message"])

    def test_an_expression_with_no_value_is_not_reported_as_non_convergence(self):
        # 1/0 at the bias: the netlist is fine and the message must not send the
        # caller looking for a floating node
        r = _err("* b\nB1 1 0 V=1/0\nR1 1 0 1k\n.op\n.end\n")
        self.assertEqual(r["reason_code"], "EXPRESSION_INVALID")
        self.assertIn("division by zero", r["message"])

if __name__ == "__main__":
    unittest.main()
