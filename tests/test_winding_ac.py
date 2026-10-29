"""Winding AC resistance (Dowell) against an INDEPENDENT 1-D solution.

The closed form below is Dowell's series and it is easy to transcribe wrong - in
particular the Delta factor belongs on BOTH terms, and leaving it off the
proximity term is only visible at high frequency. So the check here does not
repeat the series: it solves the same 1-D problem a second way.

With all layers in series, every layer sees the same MMF drop, so H goes from
(i/m)*H0 to ((i+1)/m)*H0 across layer i, and inside a layer

    h(x) = Hl*cosh(gx) + B*sinh(gx),  B = (Hr - Hl*cosh(gt))/sinh(gt)
    J(x) = g*(Hl*sinh(gx) + B*cosh(gx)),   g = (1+j)/delta

which is the exact solution of d2H/dx2 = j*omega*mu*sigma*H. Loss is
(1/(2 sigma)) * INT |J|^2 dx by quadrature. Nothing in it uses sinh(2D)+sin(2D).
"""
import cmath
import math
import os
import sys
import unittest

sys.path.insert(0, os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "src"))

from pcb_agent_mcp.calc import magnetics as M   # noqa: E402
from pcb_agent_mcp import tools                 # noqa: E402

MU0 = 4e-7 * math.pi
DELTA_100K = math.sqrt(1.724e-8 / (math.pi * 1e5 * MU0))     # 208.97 um


def ode_fr(layers, delta, nodes=1500):
    """F_R from the per-layer exact ODE solution + quadrature (no Dowell series)."""
    t = 1.0
    g = (1 + 1j) / (t / delta)
    p = 0.0
    for i in range(layers):
        hl = i / float(layers)
        hr = (i + 1) / float(layers)
        b = (hr - hl * cmath.cosh(g * t)) / cmath.sinh(g * t)
        s = 0.0
        for k in range(nodes):
            x = t * (k + 0.5) / nodes
            j = g * (hl * cmath.sinh(g * x) + b * cmath.cosh(g * x))
            s += abs(j) ** 2 * (t / nodes)
        p += 0.5 * s
    p_dc = 0.5 * layers * t * (1.0 / (layers * t)) ** 2
    return p / p_dc


class TestAgainstIndependentSolution(unittest.TestCase):

    def test_matches_the_per_layer_ode_for_m1_to_m8(self):
        worst = 0.0
        for m in (1, 2, 3, 4, 8):
            for d in (0.2, 0.5, 1.0, 2.0, 4.0, 8.0):
                closed = M._dowell_fr(m, d)[0]
                num = ode_fr(m, d)
                worst = max(worst, abs(num - closed) / closed)
        # 1500 quadrature nodes per layer: the residual is quadrature error only
        self.assertLess(worst, 1e-4, "worst relative error %.2e" % worst)

    def test_dc_limit_is_unity(self):
        # Delta -> 0: sinh2D+sin2D ~ 4D, cosh2D-cos2D ~ 4D^2, so term1 -> 1
        for m in (1, 4, 16):
            fr = M._dowell_fr(m, 0.01)[0]
            self.assertAlmostEqual(fr, 1.0, delta=1e-3)

    def test_high_frequency_limit_is_the_surface_impedance_sum(self):
        # Delta >> 1: F_R -> Delta*(2m^2+1)/3.  That limit is derived separately
        # from (Rs/2)*SUM_k(|H_k-1|^2+|H_k|^2) with H_k = k, i.e. two faces per
        # layer and the proximity term growing as m^2.  It is what catches the
        # missing Delta on the second term.
        for m in (1, 2, 4, 8):
            d = 40.0
            self.assertAlmostEqual(M._dowell_fr(m, d)[0] / (d * (2 * m * m + 1) / 3.0),
                                   1.0, delta=2e-3)

    def test_terms_add_up_and_stay_above_unity(self):
        for m in (1, 2, 7):
            for d in (0.05, 0.5, 3.0):
                fr, own, prox = M._dowell_fr(m, d)
                self.assertAlmostEqual(fr, own + prox, places=12)
                self.assertGreaterEqual(fr, 1.0 - 1e-12)

    def test_monotone_in_delta_and_in_layers(self):
        m = 3
        prev = 0.0
        for d in (0.1, 0.5, 1.0, 2.0, 6.0):
            fr = M._dowell_fr(m, d)[0]
            self.assertGreater(fr, prev)
            prev = fr
        d = 1.0
        prev = 0.0
        for m in range(1, 9):
            fr = M._dowell_fr(m, d)[0]
            self.assertGreater(fr, prev)
            prev = fr

    def test_m1_has_no_proximity_term(self):
        fr, own, prox = M._dowell_fr(1, 1.5)
        self.assertEqual(prox, 0.0)
        self.assertEqual(fr, own)


class TestAnswerShape(unittest.TestCase):

    def test_skin_depth_and_ratio_at_100k(self):
        # delta = sqrt(rho/(pi*f*mu0)) = 208.97 um; h = 0.1 mm => Delta 0.4785
        v = M.winding_ac_resistance(1e5, 1, "foil", thickness_mm=0.1)["value"]
        self.assertAlmostEqual(v["skin_depth_um"], 208.97, delta=0.02)
        self.assertAlmostEqual(v["delta_eq"], 0.47853, places=4)
        self.assertAlmostEqual(v["fr"], 1.00465, places=4)
        self.assertEqual(v["fr_proximity"], 0.0)

    def test_more_layers_cost_more(self):
        one = M.winding_ac_resistance(1e5, 1, "foil", thickness_mm=0.1)["value"]
        three = M.winding_ac_resistance(1e5, 3, "foil", thickness_mm=0.1)["value"]
        self.assertAlmostEqual(three["fr_own_field"], one["fr"], places=5)
        self.assertGreater(three["fr_proximity"], 0.0)
        self.assertGreater(three["fr"], one["fr"])

    def test_ohms_from_measured_dc_resistance(self):
        v = M.winding_ac_resistance(1e5, 2, "foil", thickness_mm=0.1,
                                    dc_resistance_ohm=0.05)["value"]
        self.assertAlmostEqual(v["ac_resistance_ohm"], 0.05 * v["fr"], delta=2e-6)

    def test_ohms_from_geometry(self):
        # rho * L / A = 1.724e-8 * (4*0.040) / (0.1e-3 * 1e-3) = 0.027584
        # rho * L / A = 1.724e-8 * (2*0.020) / (0.1e-3 * 1e-3) = 0.006896
        v = M.winding_ac_resistance(1e5, 2, "foil", thickness_mm=0.1,
                                    turns=2, mean_length_mm=20.0)["value"]
        self.assertAlmostEqual(v["dc_resistance_ohm"], 0.006896, places=8)

    def test_temperature_lowers_the_ratio(self):
        cold = M.winding_ac_resistance(1e5, 2, "foil", thickness_mm=0.1)["value"]
        hot = M.winding_ac_resistance(1e5, 2, "foil", thickness_mm=0.1,
                                      temperature_c=120.0)["value"]
        # hotter copper: longer skin depth (Delta smaller) but more resistance;
        # the RATIO falls, the resistance rises
        self.assertLess(hot["fr"], cold["fr"])
        self.assertGreater(hot["resistivity_ohm_m"], cold["resistivity_ohm_m"])
        # rho(120C) = 2.4015e-8, delta = 246.78 um, Delta = 0.40522
        self.assertAlmostEqual(hot["skin_depth_um"], 246.64, delta=0.02)
        self.assertAlmostEqual(hot["fr"], 1.0114, places=3)

    def test_round_wire_reports_the_bracket_not_a_pick(self):
        v = M.winding_ac_resistance(1e5, 2, "round", wire_diameter_mm=0.4,
                                    pitch_mm=0.5)["value"]
        # h_eq = (pi/4)*0.16/0.5 = 0.2513 mm, eta = 0.5027
        self.assertAlmostEqual(v["equivalent_foil_mm"], 0.251327, places=5)
        self.assertAlmostEqual(v["copper_fraction_of_layer"], 0.50265, places=4)
        self.assertEqual(v["reported"], "fr_area_equivalent")
        self.assertLess(v["fr_porosity_corrected"], v["fr_area_equivalent"])
        self.assertEqual(v["round_wire_bracket"],
                         [v["fr_porosity_corrected"], v["fr_area_equivalent"]])
        self.assertIn("between", v["round_wire_note"])
        # asking for the other convention moves `fr` and the split with it
        w = M.winding_ac_resistance(1e5, 2, "round", wire_diameter_mm=0.4,
                                    pitch_mm=0.5, porosity_corrected=True)["value"]
        self.assertEqual(w["reported"], "fr_porosity_corrected")
        self.assertAlmostEqual(w["fr"], w["fr_porosity_corrected"], places=6)
        self.assertAlmostEqual(w["fr"], w["fr_own_field"] + w["fr_proximity"],
                               places=6)

    def test_not_modelled_names_the_gap_field(self):
        v = M.winding_ac_resistance(1e5, 2, "foil", thickness_mm=0.1)["value"]
        blob = " ".join(v["not_modelled"])
        self.assertIn("fringing", blob)
        self.assertIn("litz", blob)
        self.assertIn("interleaved", blob)


class TestRefusals(unittest.TestCase):

    def test_zero_frequency_is_refused(self):
        r = M.winding_ac_resistance(0, 1, "foil", thickness_mm=0.1)
        self.assertEqual(r["reason_code"], "ARG_RANGE")

    def test_layer_bounds(self):
        self.assertEqual(M.winding_ac_resistance(1e5, 0, "foil", thickness_mm=0.1)
                         ["reason_code"], "ARG_RANGE")
        self.assertEqual(M.winding_ac_resistance(1e5, 80, "foil", thickness_mm=0.1)
                         ["reason_code"], "ARG_RANGE")

    def test_both_geometries_is_a_mistake(self):
        r = M.winding_ac_resistance(1e5, 1, "foil", thickness_mm=0.1,
                                    wire_diameter_mm=0.4)
        self.assertEqual(r["reason_code"], "ARG_INVALID")

    def test_round_needs_a_pitch(self):
        r = M.winding_ac_resistance(1e5, 1, "round", wire_diameter_mm=0.4)
        self.assertEqual(r["reason_code"], "ARG_MISSING")

    def test_wire_cannot_be_wider_than_the_pitch(self):
        r = M.winding_ac_resistance(1e5, 1, "round", wire_diameter_mm=0.5,
                                    pitch_mm=0.4)
        self.assertEqual(r["reason_code"], "ARG_RANGE")

    def test_foil_turns_must_equal_layers(self):
        r = M.winding_ac_resistance(1e5, 2, "foil", thickness_mm=0.1, turns=3)
        self.assertEqual(r["reason_code"], "ARG_INVALID")

    def test_temperature_and_resistivity_are_mutually_exclusive(self):
        r = M.winding_ac_resistance(1e5, 1, "foil", thickness_mm=0.1,
                                    temperature_c=90.0, resistivity_ohm_m=2e-8)
        self.assertEqual(r["reason_code"], "ARG_INVALID")

    def test_unknown_shape(self):
        r = M.winding_ac_resistance(1e5, 1, "litz", thickness_mm=0.1)
        self.assertEqual(r["reason_code"], "ARG_INVALID")


class TestToolWiring(unittest.TestCase):

    def test_tool_is_registered_and_answers(self):
        self.assertIn("magnetics_winding_ac", tools.TOOLS)
        r = tools.call_tool("magnetics_winding_ac",
                            {"freq_hz": 1e5, "layers": 2, "thickness_mm": 0.1,
                             "dc_resistance_ohm": 0.05})
        self.assertEqual(r["status"], "ok")
        self.assertAlmostEqual(r["value"]["ac_resistance_ohm"], 0.0511, delta=1e-4)

    def test_default_shape_is_foil(self):
        r = tools.call_tool("magnetics_winding_ac",
                            {"freq_hz": 1e5, "layers": 1, "thickness_mm": 0.1})
        self.assertEqual(r["value"]["shape"], "foil")


if __name__ == "__main__":
    unittest.main()
