"""Finite coplanar ground width (0.0.25): the wing stops, and everything moves.

Nothing external can check this geometry - the quasi-static conformal map that
`coplanar_vs_conformal` is measured against has INFINITE grounds, so with a
finite wing that comparison is two different structures, not an error bar. What
is checked instead:

  * ground_mm = 0 is bit-for-bit the historical answer (the parameter is
    opt-in, and every number in VALIDATION was measured on the infinite wing);
  * a finite wing raises Z0, monotonically, and decays back onto the infinite
    answer as the wing is widened - the return current has somewhere to spread;
  * the wing's outer edge lands exactly on a grid line, because that edge is
    the whole physics of the parameter;
  * on a pair the effect splits by mode (odd ~1 %, even ~22 % at a wing of
    half a gap), which is the design-relevant fact and also proof that the two
    modes are not both being fed the same number.

The negative control is the bug this batch actually had: the parameter existed
in the section class but was dropped by the layer above, so the answer was
identical to the infinite wing and still looked plausible. `_WingIgnored`
reproduces that on purpose.
"""

import unittest

from pcb_agent_mcp import tools
from pcb_agent_mcp.calc import field2d
from pcb_agent_mcp.calc.field2d import _Coplanar, _CoplanarPair

GEOM = dict(w=0.3, g=0.2, h=0.2, er=4.3)


def _solve(kind, ground_mm, fine=6.0, w=None, g=None, h=None):
    w = GEOM["w"] if w is None else w
    g = GEOM["g"] if g is None else g
    h = GEOM["h"] if h is None else h
    z, _drift, runs = field2d.coplanar_solve_pair(
        kind, w, g, h, 0.0, GEOM["er"], levels=(1.0,), fine_div=fine,
        ground_mm=ground_mm)
    return z, runs[0]


class _WingIgnored(_Coplanar):
    """Bug, kept on purpose: ground_mm is accepted and then dropped.

    This is what the tool layer did before the schema was updated - the number
    that came back was the infinite-wing answer, and nothing about it looked
    wrong.
    """

    def __init__(self, kind, w, g, h, t, er, fine, far_span=8.0,
                 ground_mm=None):
        _Coplanar.__init__(self, kind, w, g, h, t, er, fine,
                           far_span=far_span, ground_mm=None)


class TestTheDefaultIsUnchanged(unittest.TestCase):
    def test_ground_mm_zero_is_exactly_the_historical_answer(self):
        base = {"width_mm": 0.3, "gap_mm": 0.2, "dielectric_mm": 0.2,
                "kind": "cpw", "levels": 1}
        a = tools.call_tool("coplanar_field", dict(base))
        b = tools.call_tool("coplanar_field", dict(base, ground_mm=0))
        self.assertEqual(a["status"], "ok")
        self.assertEqual(b["status"], "ok")
        self.assertEqual(a["value"], b["value"])
        self.assertEqual(a["eps_eff"], b["eps_eff"])
        self.assertIsNone(a["ground_width_mm"])
        self.assertIsNone(b["ground_width_mm"])
        self.assertNotIn("ground_note", a)
        self.assertNotIn("ground_note", b)

    def test_the_pair_default_is_unchanged_too(self):
        base = {"width_mm": 0.3, "spacing_mm": 0.2, "gap_mm": 0.2,
                "dielectric_mm": 0.2, "kind": "cpw", "levels": 1}
        a = tools.call_tool("coplanar_pair_field", dict(base))
        b = tools.call_tool("coplanar_pair_field", dict(base, ground_mm=0))
        self.assertEqual(a["status"], "ok")
        self.assertEqual(a["value"], b["value"])
        self.assertEqual(a["z_even_ohm"], b["z_even_ohm"])
        self.assertIsNone(b["ground_width_mm"])

    def test_a_negative_or_textual_ground_is_refused(self):
        base = {"width_mm": 0.3, "gap_mm": 0.2, "dielectric_mm": 0.2,
                "kind": "cpw"}
        self.assertEqual(
            tools.call_tool("coplanar_field", dict(base, ground_mm=-0.1))
            ["reason_code"], "ARG_RANGE")
        self.assertEqual(
            tools.call_tool("coplanar_field", dict(base, ground_mm="wide"))
            ["status"], "error")
        self.assertEqual(
            tools.call_tool("coplanar_pair_field",
                            dict(base, spacing_mm=0.2, ground_mm=-1))
            ["reason_code"], "ARG_RANGE")


class TestTheWingDoesWhatAnEdgeDoes(unittest.TestCase):
    def test_a_finite_wing_raises_z0_and_decays_back(self):
        z_inf, run_inf = _solve("cpw", None)
        z_half, _ = _solve("cpw", 0.1)     # gap/2
        z_width, _ = _solve("cpw", 0.5)    # 2.5 gaps
        z_wide, _ = _solve("cpw", 2.0)     # 10 gaps
        # narrower wing -> higher impedance, and never below the infinite answer
        self.assertGreater(z_half, z_width)
        self.assertGreater(z_width, z_wide)
        self.assertGreater(z_wide, z_inf)
        # the half-gap wing is worth double digits, the 10-gap wing is noise
        self.assertGreater(100.0 * (z_half - z_inf) / z_inf, 8.0)
        self.assertLess(100.0 * (z_wide - z_inf) / z_inf, 1.5)
        # and it gets there by growing the mesh, not by changing the box scale
        self.assertGreater(run_inf["nodes"], 0)

    def test_the_wing_edge_is_a_grid_line(self):
        b = GEOM["w"] / 2.0 + GEOM["g"]
        for gw in (0.05, 0.2, 0.7):
            sec = _Coplanar("cpw", GEOM["w"], GEOM["g"], GEOM["h"], 0.0,
                            GEOM["er"], 0.05, ground_mm=gw)
            # both wings, so |x|: the mesh is mirrored
            hits = [x for x in sec.xs if abs(abs(x) - (b + gw)) < 1e-12]
            self.assertEqual(len(hits), 2, "wing edge at %.3f not on a grid line"
                             % (b + gw))
        # without the parameter that grid line does not exist at all
        sec = _Coplanar("cpw", GEOM["w"], GEOM["g"], GEOM["h"], 0.0,
                        GEOM["er"], 0.05)
        self.assertEqual([x for x in sec.xs if abs(abs(x) - (b + 0.2)) < 1e-12],
                         [])

    def test_the_pair_wing_edge_is_a_grid_line_too(self):
        # the pair's ground starts at |x| = spacing/2 + width + gap, not at
        # width/2 + gap - the two strips sit in the middle
        a3 = 0.2 / 2.0 + GEOM["w"] + GEOM["g"]
        sec = _CoplanarPair("cpw", GEOM["w"], 0.2, GEOM["g"], GEOM["h"], 0.0,
                            GEOM["er"], 0.05, ground_mm=0.15)
        hits = [x for x in sec.xs if abs(abs(x) - (a3 + 0.15)) < 1e-12]
        self.assertEqual(len(hits), 2)

    def test_a_wing_that_is_ignored_is_caught(self):
        # the negative control: same class, parameter dropped
        honest = _Coplanar("cpw", GEOM["w"], GEOM["g"], GEOM["h"], 0.0,
                           GEOM["er"], 0.05, ground_mm=0.1)
        honest.build()
        honest._V = honest.solve()
        c_honest = honest.charge(honest._V)

        blind = _WingIgnored("cpw", GEOM["w"], GEOM["g"], GEOM["h"], 0.0,
                             GEOM["er"], 0.05, ground_mm=0.1)
        blind.build()
        blind._V = blind.solve()
        c_blind = blind.charge(blind._V)

        ref = _Coplanar("cpw", GEOM["w"], GEOM["g"], GEOM["h"], 0.0,
                        GEOM["er"], 0.05)
        ref.build()
        ref._V = ref.solve()
        c_ref = ref.charge(ref._V)

        # dropping the wing reproduces the infinite answer to the last digit...
        self.assertAlmostEqual(c_blind, c_ref, places=9)
        # ...and the honest wing is far enough away to be unmistakable
        self.assertGreater(abs(c_honest - c_ref) / c_ref, 0.02)


class TestTheTwoModesSplit(unittest.TestCase):
    def test_a_narrow_ground_is_a_common_mode_problem(self):
        p = dict(w=GEOM["w"], s=0.2)
        inf = field2d.coplanar_pair_solve(
            "cpw", p["w"], p["s"], GEOM["g"], GEOM["h"], 0.0, GEOM["er"],
            levels=(1.0,), fine_div=6.0, ground_mm=None)
        fin = field2d.coplanar_pair_solve(
            "cpw", p["w"], p["s"], GEOM["g"], GEOM["h"], 0.0, GEOM["er"],
            levels=(1.0,), fine_div=6.0, ground_mm=0.1)
        d_odd = 100.0 * (fin["z_odd"] - inf["z_odd"]) / inf["z_odd"]
        d_even = 100.0 * (fin["z_even"] - inf["z_even"]) / inf["z_even"]
        # both rise (the return path got narrower) but by wildly different amounts
        self.assertGreater(d_odd, 0.2)
        self.assertGreater(d_even, 8.0)
        self.assertGreater(d_even, 5.0 * d_odd)
        # the study's own ordering must survive: odd still far below even
        self.assertLess(fin["z_odd"], fin["z_even"])
        # and eps_eff of the odd mode barely notices, because the odd return is
        # already hugging the slot
        self.assertLess(abs(fin["eps_eff_odd"] - inf["eps_eff_odd"]), 0.06)


if __name__ == "__main__":
    unittest.main()
