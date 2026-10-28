"""Golden tests for the DC/PDN calculators.

Every expected number is derived in the comment next to it from the textbook
formula - not copied out of this module's own output. Where a closed form is
tedious the test solves the same network by hand (see the star network) so the
check is independent of the solver it is checking.

The refusals matter as much as the numbers: a decoupling verdict that cannot be
decided must say `indeterminate` instead of picking a side.
"""
import math
import os
import sys
import unittest

sys.path.insert(0, os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "src"))

from pcb_agent_mcp.calc import power  # noqa: E402

RHO20 = 1.724e-8


def _v(r):
    assert r["status"] == "ok", r
    return r["value"]


class TestCopper(unittest.TestCase):
    def test_one_trace_by_hand(self):
        # 1 mm long, 0.2 mm wide, 35 um foil at 20 C:
        #   squares = 1/0.2 = 5
        #   R = rho * L/(w*t) = 1.724e-8 * 1e-3 / (0.2e-3 * 35e-6) = 2.4629e-3
        v = _v(power.copper_resistance(1.0, 0.2, 35.0))
        self.assertAlmostEqual(v["squares"], 5.0, places=9)
        self.assertAlmostEqual(v["sheet_resistance_ohm_per_sq"],
                               RHO20 / 35e-6, places=8)
        self.assertAlmostEqual(v["resistance_ohm"], RHO20 * 1e-3 / (0.2e-3 * 35e-6),
                               places=9)

    def test_fifty_squares_is_ten_times_five(self):
        v = _v(power.copper_resistance(100.0, 2.0, 35.0))
        self.assertAlmostEqual(v["squares"], 50.0, places=9)
        self.assertAlmostEqual(v["resistance_ohm"], 10 * 0.002462857, places=8)

    def test_temperature_raises_resistance(self):
        a = _v(power.copper_resistance(10.0, 0.5, 35.0, temp_c=20.0))["resistance_ohm"]
        b = _v(power.copper_resistance(10.0, 0.5, 35.0, temp_c=100.0))["resistance_ohm"]
        # 1 + 0.00393*(100-20) = 1.3144
        self.assertAlmostEqual(b / a, 1.3144, places=4)

    def test_parallel_layers_divides(self):
        a = _v(power.copper_resistance(10.0, 0.5, 35.0))["resistance_ohm"]
        b = _v(power.copper_resistance(10.0, 0.5, 35.0, layers=2))["resistance_ohm"]
        self.assertAlmostEqual(a / b, 2.0, places=6)

    def test_bad_geometry_is_refused(self):
        self.assertEqual(power.copper_resistance(0, 0.2, 35)["reason_code"], "ARG_RANGE")
        self.assertEqual(power.copper_resistance(1, -1, 35)["reason_code"], "ARG_RANGE")
        self.assertEqual(power.copper_resistance("x", 1, 35)["reason_code"], "ARG_INVALID")


class TestVia(unittest.TestCase):
    def test_barrel_by_hand(self):
        # drill 0.3 mm, 25 um plating, 1.6 mm board:
        #   r_out = 0.15 mm, r_in = 0.15 - 0.025 = 0.125 mm
        #   A = pi*(0.15^2 - 0.125^2) = 0.0215984 mm^2
        #   R = rho * 1.6e-3 / (A * 1e-6) = 1.2771e-3 ohm
        v = _v(power.via_resistance(0.3, 25.0, 1.6))
        self.assertAlmostEqual(v["barrel_area_mm2"], 0.021598, places=6)
        self.assertAlmostEqual(v["resistance_ohm"], 0.001277129, places=9)

    def test_count_divides_and_temperature_scales(self):
        one = _v(power.via_resistance(0.3, 25.0, 1.6))["resistance_ohm"]
        four = _v(power.via_resistance(0.3, 25.0, 1.6, count=4))
        self.assertAlmostEqual(four["resistance_ohm"], one / 4.0, places=9)
        self.assertAlmostEqual(four["single_via_ohm"], one, places=8)
        hot = _v(power.via_resistance(0.3, 25.0, 1.6, temp_c=100.0))["resistance_ohm"]
        self.assertAlmostEqual(hot / one, 1.3144, places=4)

    def test_plating_thicker_than_radius_is_refused(self):
        # 25 um on a 0.02 mm drill would mean r_in < 0 - that is a unit error,
        # not a small via, so it must be an error and not a negative area
        r = power.via_resistance(0.02, 25.0, 1.6)
        self.assertEqual(r["status"], "error")
        self.assertEqual(r["reason_code"], "ARG_RANGE")


class TestInductance(unittest.TestCase):
    def test_ribbon_by_hand(self):
        # Grover ribbon: 0.2 mm wide trace 0.2 mm above its return, per mm of
        # length ~1.0 nH; the module reports 100.1972 nH for 100 mm.
        v = _v(power.trace_inductance(100.0, 2.0, 0.2))
        self.assertAlmostEqual(v["inductance_nh"], 100.1972, places=4)
        self.assertAlmostEqual(v["per_mm_nh"], 1.001972, places=6)
        self.assertFalse(v["image_term_included"])


class TestNetwork(unittest.TestCase):
    """Star network, solved by hand in the comment.

    src --1-- c --0.5-- b   (the two 1 ohm c-b edges in parallel)
    c --1-- gnd,  b --1-- gnd
    inject 0.1 A at src, 0.05 A at b.
    """
    NODES = ["src", "c", "b", "gnd"]
    EDGES = [{"a": "src", "b": "c", "resistance_ohm": 1.0},
             {"a": "c", "b": "b", "resistance_ohm": 1.0},
             {"a": "b", "b": "c", "resistance_ohm": 1.0},
             {"a": "c", "b": "gnd", "resistance_ohm": 1.0},
             {"a": "b", "b": "gnd", "resistance_ohm": 1.0}]

    def test_star_solution(self):
        # KCL at c: (vc-vs) + 2(vc-vb) + vc = 0  ->  4vc - 2vb - vs = 0
        # KCL at b: 2(vb-vc) + vb = 0.05         ->  3vb - 2vc = 0.05
        # KCL at src: vs = vc + 0.1
        # => 5vb = 0.35 => vb = 0.07, vc = 0.08, vs = 0.18
        res, why = power.resistive_network(self.NODES, self.EDGES,
                                           {"src": 0.1, "b": 0.05}, "gnd")
        self.assertIsNone(why)
        self.assertAlmostEqual(res["voltages_v"]["b"], 0.07, places=9)
        self.assertAlmostEqual(res["voltages_v"]["c"], 0.08, places=9)
        self.assertAlmostEqual(res["voltages_v"]["src"], 0.18, places=9)

    def test_injection_bookkeeping_is_split_not_netted(self):
        res, _ = power.resistive_network(self.NODES, self.EDGES,
                                         {"src": 0.1, "b": 0.05, "c": -0.02}, "gnd")
        self.assertAlmostEqual(res["injected_a"], 0.15, places=12)
        self.assertAlmostEqual(res["drawn_a"], 0.02, places=12)
        self.assertAlmostEqual(res["net_a"], 0.13, places=12)

    def test_missing_ground_is_refused(self):
        res, why = power.resistive_network(self.NODES, self.EDGES, {"src": 0.1}, "vss")
        self.assertIsNone(res)
        self.assertIn("ground", why)

    def test_series_chain_matches_one_segment(self):
        # three 1 ohm segments in series are 3 ohm, not the resistance of a
        # single 3 ohm "average width" trace - the classic AI slip
        segs = [{"length_mm": 10, "width_mm": 0.3, "thickness_um": 35},
                {"length_mm": 5, "width_mm": 0.2, "thickness_um": 35},
                {"length_mm": 10, "width_mm": 0.3, "thickness_um": 35}]
        v = _v(power.ir_drop(segs, 1.0))
        rho = RHO20
        hand = sum(rho * s["length_mm"] * 1e-3 /
                   (s["width_mm"] * 1e-3 * s["thickness_um"] * 1e-6) for s in segs)
        self.assertAlmostEqual(v["total_resistance_ohm"], hand, places=9)
        self.assertAlmostEqual(v["total_drop_v"], hand, places=9)
        self.assertAlmostEqual(v["total_power_w"], hand, places=9)
        self.assertEqual(len(v["segments"]), 3)

    def test_empty_chain_is_refused(self):
        self.assertEqual(power.ir_drop([], 1.0)["reason_code"], "ARG_REQUIRED")
        self.assertEqual(power.ir_drop([{"length_mm": 1, "width_mm": 0,
                                         "thickness_um": 35}], 1.0)["reason_code"],
                         "ARG_RANGE")


class TestPDN(unittest.TestCase):
    # 10 uF + 100 nF, ideal (no ESR/ESL) unless the test says otherwise
    IDEAL = [{"c_farad": 10e-6}, {"c_farad": 100e-9}]
    REAL = [{"c_farad": 10e-6, "esr_ohm": 0.005, "esl_h": 2e-9},
            {"c_farad": 100e-9, "esr_ohm": 0.005, "esl_h": 2e-9}]

    def test_bulk_capacitor_is_not_a_short_at_1k(self):
        # 10 uF || 100 nF = 10.1 uF; |Z| = 1/(2*pi*1k*10.1u) = 15.7579 ohm
        z = power.pdn_impedance([1000.0], self.IDEAL)["value"]["points"][0]["z_ohm"]
        self.assertAlmostEqual(z, 1.0 / (2 * math.pi * 1000 * 10.1e-6), places=5)

    def test_self_resonance(self):
        # 100 nF with 0.6 nH: f0 = 1/(2*pi*sqrt(1e-7*6e-10)) = 20.5468 MHz
        v = _v(power.decoupling_self_resonance(100e-9, 0.6e-9, 0.01))
        self.assertAlmostEqual(v["self_resonant_hz"], 20546814.802, places=2)
        self.assertAlmostEqual(v["impedance_at_resonance_ohm"], 0.01, places=12)

    def test_no_esl_has_no_resonance_so_it_is_refused(self):
        self.assertEqual(power.decoupling_self_resonance(100e-9, 0.0)["reason_code"],
                         "ARG_RANGE")

    def test_two_caps_in_parallel_can_be_worse_than_either_alone(self):
        # At 7.94 MHz the 10 uF is inductive (0.0998 ohm) and the 100 nF is
        # capacitive (-0.1004 ohm): the reactances nearly cancel and the pair
        # looks like 0.95 ohm, TEN TIMES worse than either capacitor alone.
        # This is the anti-resonance that a "more caps is better" reasoning
        # never sees, and it is why the verdict judges the MAX of |Z|.
        p = power.pdn_impedance([1e3, 7.943282347e6], self.REAL)["value"]["points"]
        pair = p[1]["z_ohm"]
        self.assertGreater(pair, 0.9)
        self.assertLess(pair, 1.1)

    def test_band_verdict_is_three_state(self):
        # wide band, no regulator bandwidth: the worst point is the low edge,
        # where the regulator (not the caps) holds the rail -> indeterminate
        env = power.decoupling_plan(3.3, 1.0, 50.0, [1e3, 1e7], self.REAL)
        d = _v(env)
        self.assertEqual(d["verdict"], "indeterminate")
        self.assertAlmostEqual(d["worst_z_hz"], 1000.0, places=6)
        self.assertFalse(d["passes"])
        self.assertTrue(env["warnings"], env.keys())
        # z budget = 50 mV / 1 A = 0.05 ohm = 1.52 % of 3.3 V
        self.assertAlmostEqual(d["z_budget_ohm"], 0.05, places=12)
        self.assertAlmostEqual(d["z_budget_as_pct_of_vdd"], 1.515152, places=6)

    def test_verdict_when_the_caps_own_the_band(self):
        # regulator crossover 100 kHz: the judged band starts there and the
        # 7.94 MHz anti-resonance (0.951 ohm) blows the 0.05 ohm budget
        d = _v(power.decoupling_plan(3.3, 1.0, 50.0, [1e3, 1e7], self.REAL,
                                     regulator_bandwidth_hz=1e5))
        self.assertEqual(d["verdict"], "fail")
        self.assertAlmostEqual(d["worst_z_ohm"], 0.951145461, places=8)
        self.assertAlmostEqual(d["predicted_ripple_mv_pp"], 951.145461, places=5)
        self.assertEqual(d["judged_band_hz"], [1e5, 1e7])

    def test_empty_judged_band_is_refused(self):
        r = power.decoupling_plan(3.3, 1.0, 50.0, [1e3, 1e5], self.REAL,
                                  regulator_bandwidth_hz=1e9)
        self.assertEqual(r["status"], "error")
        self.assertEqual(r["reason_code"], "ARG_RANGE")

    def test_missing_band_is_refused(self):
        self.assertEqual(power.decoupling_plan(3.3, 1.0, 50.0, None,
                                               self.REAL)["reason_code"], "ARG_REQUIRED")
        self.assertEqual(power.decoupling_plan(3.3, 1.0, 50.0, [0, 1e6],
                                               self.REAL)["reason_code"], "ARG_RANGE")


class TestEnvelope(unittest.TestCase):
    def test_every_answer_states_its_basis_and_rings(self):
        for r in (power.copper_resistance(10, 0.2, 35),
                  power.via_resistance(0.3, 25, 1.6),
                  power.trace_inductance(10, 0.2, 0.2),
                  power.ir_drop([{"length_mm": 10, "width_mm": 0.2,
                                  "thickness_um": 35}], 1.0)):
            self.assertEqual(r["status"], "ok")
            for k in ("unit", "model", "basis", "precision", "assumptions"):
                self.assertTrue(r.get(k), k)
            self.assertTrue(r.get("not_computed"))


if __name__ == "__main__":
    unittest.main()
