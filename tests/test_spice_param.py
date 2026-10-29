"""Tests for .param: top-level cards, bodies, ordering and the ways it refuses.

The claim is narrow: a parameter is a NUMBER that reaches the element, and every
name that cannot be resolved is named in the error instead of being guessed. Each
mechanism carries a control that FAILS when the mechanism is absent:

* a parameter that lands -> changing it must change the current (a dropped
  parameter leaves the answer sitting at the other value)
* forward reference      -> resolving in written order would raise, so the value
  is the proof that rounds ran
* local .param shadows a global of the same name -> 9k vs 1k is a 9x move
* strict numbers        -> _value() alone reads "1k*2" as 1000; this must refuse
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


def _current(net):
    """Current through the LAST element, which every deck below ends with."""
    return _ok(net)["elements"][-1]["current_a"]


DIVIDER = """
* param divider
.param r1=1k r2=3k
V1 1 0 9
R1 1 2 {r1}
R2 2 0 {r2}
.op
.end
"""


class ParametersReachTheElement(unittest.TestCase):
    def test_a_divider_built_from_parameters(self):
        v = _ok(DIVIDER)
        self.assertAlmostEqual(v["nodes_v"]["2"], 9.0 * 3 / 4, places=6)

    def test_changing_the_parameter_changes_the_answer(self):
        # THE control: if .param were parsed and then dropped, both decks would
        # read the same number and this assertion would fail
        other = DIVIDER.replace("r2=3k", "r2=1k")
        self.assertAlmostEqual(_ok(other)["nodes_v"]["2"], 9.0 / 2, places=6)

    def test_a_parameter_may_drive_a_source(self):
        v = _ok("""
.param vdd=3.3
V1 1 0 {vdd}
R1 1 0 1k
.op
.end
""")
        self.assertAlmostEqual(v["nodes_v"]["1"], 3.3, places=9)

    def test_engineering_suffixes_survive(self):
        self.assertAlmostEqual(_current(".param r=1meg\nV1 1 0 1\nR1 1 0 {r}\n"
                                        ".op\n.end\n"), 1e-6, places=12)
        self.assertAlmostEqual(_current(".param r=2.2u\nV1 1 0 1\nR1 1 0 {r}\n"
                                        ".op\n.end\n"), 1 / 2.2e-6, places=3)

    def test_a_parameter_may_come_after_the_element_that_uses_it(self):
        a = _ok("""
V1 1 0 9
R1 1 2 {r1}
R2 2 0 {r2}
.param r1=1k r2=3k
.op
.end
""")
        self.assertAlmostEqual(a["nodes_v"]["2"], 6.75, places=6)

    def test_the_last_definition_of_a_name_wins(self):
        self.assertAlmostEqual(_current(".param r=1k\n.param r=2k\nV1 1 0 1\n"
                                        "R1 1 0 {r}\n.op\n.end\n"), 5e-4, places=9)


class ParametersResolveInAnyOrder(unittest.TestCase):
    def test_arithmetic_on_an_earlier_parameter(self):
        self.assertAlmostEqual(_current(".param a=1k b={a*3}\nV1 1 0 1\n"
                                        "R1 1 0 {b}\n.op\n.end\n"), 1 / 3000.0,
                               places=9)

    def test_a_forward_reference_is_resolved_not_refused(self):
        # the numbers are written backwards on purpose: sequential resolution
        # would fail here, so 1/3000 IS the proof that rounds ran
        self.assertAlmostEqual(_current(".param b={a*3} a=1k\nV1 1 0 1\n"
                                        "R1 1 0 {b}\n.op\n.end\n"), 1 / 3000.0,
                               places=9)

    def test_a_circular_definition_names_the_cycle(self):
        r = spice.simulate(".param a={b+1} b={a+1}\nV1 1 0 1\nR1 1 0 {a}\n"
                           ".op\n.end\n")
        self.assertEqual(r["status"], "error")
        self.assertIn("circular", r["message"])
        # both names are named, and the message says what is still waiting
        self.assertIn("'b'", r["message"])
        self.assertIn("a, b", r["message"])

    def test_an_undefined_name_is_a_typo_not_a_cycle(self):
        r = spice.simulate(".param r={q*2}\nV1 1 0 1\nR1 1 0 {r}\n.op\n.end\n")
        self.assertEqual(r["status"], "error")
        self.assertIn("'q' is not a parameter in scope here", r["message"])
        self.assertNotIn("circular", r["message"])


class StrictNumbers(unittest.TestCase):
    def test_a_bare_expression_is_refused_with_the_braces_spelled_out(self):
        r = spice.simulate(".param r=1k*2\nV1 1 0 1\nR1 1 0 {r}\n.op\n.end\n")
        self.assertEqual(r["status"], "error")
        self.assertIn("write {1k*2}", r["message"])

    def test_an_element_value_has_the_same_rule(self):
        # _value("1k*2") silently stops at the multiplication sign and returns
        # 1000; a netlist that says "1k*2" there is arithmetic, so it is refused
        r = spice.simulate("V1 1 0 1\nR1 1 0 1k*2\n.op\n.end\n")
        self.assertEqual(r["status"], "error")
        self.assertIn("write {1k*2}", r["message"])

    def test_units_after_the_number_are_still_fine(self):
        self.assertAlmostEqual(_current(".param r=1kohm\nV1 1 0 1\nR1 1 0 {r}\n"
                                        ".op\n.end\n"), 1e-3, places=9)

    def test_a_parameter_cannot_be_used_as_a_number_in_a_plain_value_slot(self):
        r = spice.simulate(".param r=1k\nV1 1 0 1\nR1 1 0 r\n.op\n.end\n")
        self.assertEqual(r["status"], "error")
        self.assertIn("neither a number", r["message"])


class BodiesAndGlobals(unittest.TestCase):
    LEG = """
.param vdd=5
.subckt leg a b params: r=1k
.param rr={r*2}
R1 a b {rr}
.ends
V1 1 0 9
X1 1 0 leg r=2k
.op
.end
"""

    def test_a_body_param_uses_the_instance_parameters(self):
        # rr = 2*r = 4k -> 9/4k
        self.assertAlmostEqual(_current(self.LEG), 2.25e-3, places=9)

    def test_a_body_may_read_a_top_level_parameter(self):
        self.assertAlmostEqual(
            _current(".param vdd=5\n.subckt leg a b\nR1 a b {vdd}\n.ends\n"
                     "V1 1 0 9\nX1 1 0 leg\n.op\n.end\n"), 9 / 5.0, places=9)

    def test_a_body_param_shadows_a_global_of_the_same_name(self):
        # 1k locally against 9k globally: a dropped local would be 9x larger
        self.assertAlmostEqual(
            _current(".param r=9k\n.subckt leg a b\n.param r=1k\n"
                     "R1 a b {r}\n.ends\nV1 1 0 9\nX1 1 0 leg\n.op\n.end\n"),
            9e-3, places=9)

    def test_a_body_param_is_not_an_instance_parameter(self):
        r = spice.simulate("""
.subckt leg a b
.param rr=2k
R1 a b {rr}
.ends
V1 1 0 9
X1 1 0 leg rr=3k
.op
.end
""")
        self.assertEqual(r["status"], "error")
        self.assertIn("does not declare", r["message"])


class SourcesTakeParameters(unittest.TestCase):
    """A source is where a parameter is most often wanted (vdd, amplitude, edges),
    and each of its three number slots is parsed by different code."""

    def test_dc_value(self):
        v = _ok(".param vdd=2.5\nV1 1 0 dc={vdd}\nR1 1 0 1k\n.op\n.end\n")
        self.assertAlmostEqual(v["nodes_v"]["1"], 2.5, places=9)

    def test_ac_amplitude(self):
        r = spice.simulate(".param mag=0.5\nV1 1 0 ac {mag} 0\nR1 1 0 1k\n"
                           ".ac dec 1 1k 2k\n.end\n", analysis="ac")
        self.assertEqual(r["status"], "ok", r)
        self.assertAlmostEqual(r["value"]["points"][0]["nodes_v"]["1"]["mag"], 0.5,
                               places=9)

    def test_pulse_argument(self):
        deck = (".param vhi=3.3\nV1 1 0 pulse(0 {vhi} 0 1n 1n 1m 2m)\n"
                "R1 1 0 1k\n.tran 0.1m 2m\n.end\n")
        # resolved before the solver ever looks at it: every argument is a float
        spec = spice.parse_netlist(deck)
        args = spec["elements"][0]["wave"]["args"]
        self.assertTrue(all(isinstance(a, float) for a in args), args)
        self.assertAlmostEqual(args[1], 3.3, places=9)
        self.assertEqual(spice.simulate(deck)["status"], "ok")


class RealEngineAgrees(unittest.TestCase):
    """ngspice has .param too, so it is an independent check of the resolution
    order and of the numbers. Skipped (visibly) without a binary."""

    @classmethod
    def setUpClass(cls):
        if spice.find_ngspice() is None:
            raise unittest.SkipTest("no ngspice binary on this machine")

    def test_a_parameterised_deck_on_both_engines(self):
        deck = """
* parameter deck, dc-connected
.param r1=1k r2=3k
V1 1 0 9
R1 1 2 {r1}
R2 2 0 {r2}
.op
.end
"""
        r = spice.simulate(deck, engine="ngspice", cross_check=True)
        self.assertEqual(r["status"], "ok", r)
        cross = r["cross_check"]
        self.assertGreaterEqual(cross["compared_nodes"], 2, cross)
        self.assertLess(cross["max_abs_diff_v"], 1e-6, cross)


if __name__ == "__main__":
    unittest.main()
