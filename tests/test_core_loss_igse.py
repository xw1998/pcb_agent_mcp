"""iGSE: the same Steinmetz triple, evaluated on a flux that is not a sine.

The whole point of the improved form is that it must REDUCE to the plain Steinmetz
equation on a sine, because that reduction is what fixes its normalization k_i.
So the anchor test here is not "does the number look plausible" - it is:

  * k_i is derived from that reduction, and the reduction is then measured against
    a direct numerical integral of B(t) (independent of any closed form), so a
    wrong k_i cannot pass;
  * the triangular closed form is measured against the same numerical integral of
    a triangular B(t) (again: the integral knows nothing about the formula);
  * a piecewise triangle must land on the triangle, and a piecewise STAIRCASE
    sine must NOT land on the sine - the second one is the control that shows the
    first one is not just "any two-segment list gives the triangle".

Negative controls: an unknown waveform, a duty handed to a sine, segments handed
to a triangle, a missing segment list, dt_fractions that do not sum to 1, a
trajectory that does not close, a zero swing and a b_peak_t that contradicts the
segments all have to be refused - never silently ignored, because "ignored" here
means answering a different question than the one asked.
"""
import math
import os
import sys
import unittest

sys.path.insert(0, os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "src"))

from pcb_agent_mcp.calc import magnetics as M   # noqa: E402
from pcb_agent_mcp import tools                 # noqa: E402

K, A, B = 100.0, 1.4, 2.7          # a made-up but self-consistent SI triple


def _numeric_pv(b_samples, dt, k, alpha, beta):
    """(1/T) Int k_i |dB/dt|^alpha dB_pp^(beta-alpha) dt, straight off the samples.

    Independent of the module's segment arithmetic: only _igse_ki (the quantity
    under test) is shared, and it is exactly what the reduction pins down.
    """
    n = len(b_samples)
    period = dt * n
    bmax, bmin = max(b_samples), min(b_samples)
    dbpp = bmax - bmin
    ki = M._igse_ki(k, alpha, beta)
    total = 0.0
    for i in range(n):
        j = (i + 1) % n
        dbdt = (b_samples[j] - b_samples[i]) / dt
        total += ki * abs(dbdt) ** alpha * dbpp ** (beta - alpha) * dt
    return total / period


class TestIAlpha(unittest.TestCase):
    def test_matches_direct_quadrature(self):
        for alpha in (0.7, 1.0, 1.4, 1.6, 2.7, 3.0):
            n = 400001
            acc = 0.0
            for i in range(n):
                acc += abs(math.cos(2.0 * math.pi * (i + 0.5) / n)) ** alpha
            numeric = acc * 2.0 * math.pi / n
            self.assertAlmostEqual(M._i_alpha(alpha) / numeric, 1.0, delta=1e-8)

    def test_alpha_one_is_the_plain_integral_of_abs_cos(self):
        # Int_0^2pi |cos| = 4 exactly
        self.assertAlmostEqual(M._i_alpha(1.0), 4.0, delta=1e-12)


class TestSineReduction(unittest.TestCase):
    """The identity that fixes k_i - and the only thing that makes iGSE 'improved'."""

    def test_numeric_integral_on_a_sine_equals_steinmetz(self):
        f, bp = 100e3, 0.1
        n = 200000
        period = 1.0 / f
        samples = [bp * math.sin(2.0 * math.pi * f * (i * period / n))
                   for i in range(n)]
        measured = _numeric_pv(samples, period / n, K, A, B)
        steinmetz = K * f ** A * bp ** B
        self.assertAlmostEqual(measured / steinmetz, 1.0, delta=1e-9)

    def test_the_module_prints_the_same_thing_on_a_sine(self):
        # stating sine explicitly is the default path, and it must be the closed form
        a = M.core_loss_steinmetz(1e5, 0.1, K, A, B)["value"]
        b = M.core_loss_steinmetz(1e5, 0.1, K, A, B, waveform="sine")["value"]
        self.assertEqual(a, b)
        self.assertNotIn("igse", a)
        self.assertNotIn("waveform", a)


class TestTriangle(unittest.TestCase):
    def _numeric_triangle(self, duty, bp, f=100e3, n=200000):
        period = 1.0 / f
        out = []
        for i in range(n):
            t = period * i / n
            if t < duty * period:
                out.append(-bp + 2.0 * bp * (t / (duty * period)))
            else:
                out.append(bp - 2.0 * bp * ((t - duty * period)
                                            / ((1.0 - duty) * period)))
        return out, period / n

    def test_against_the_numerical_integral(self):
        for duty in (0.5, 0.3, 0.1, 0.9):
            measured = M.core_loss_steinmetz(1e5, 0.1, K, A, B,
                                             waveform="triangle",
                                             duty=duty)["value"]["P_v_w_m3"]
            samples, dt = self._numeric_triangle(duty, 0.1)
            numeric = _numeric_pv(samples, dt, K, A, B)
            # 1e-5, not 1e-12: the module prints 6 significant digits above 1e6
            # (see _fmt), so the printed answer carries ~1e-6 of rounding by design
            self.assertAlmostEqual(measured / numeric, 1.0, delta=1e-5)

    def test_the_symmetric_closed_form_by_hand(self):
        # duty 0.5: |dB/dt| = 2*dB_pp*f everywhere, dB_pp = 2*B_peak
        #   P = k_i * (2*dB_pp*f)^alpha * dB_pp^(beta-alpha) = k_i * 2^alpha * dB_pp^beta * f^alpha
        ki = M._igse_ki(K, A, B)
        expect = ki * 2.0 ** A * 0.2 ** B * 1e5 ** A
        got = M.core_loss_steinmetz(1e5, 0.1, K, A, B,
                                    waveform="triangle")["value"]["P_v_w_m3"]
        self.assertAlmostEqual(got / expect, 1.0, delta=1e-6)

    def test_the_ratio_only_depends_on_alpha_and_the_shape(self):
        # dB_pp and f cancel out of the ratio, so moving both must not move it
        a = M.core_loss_steinmetz(1e5, 0.1, K, A, B,
                                  waveform="triangle")["value"]["ratio_to_sine"]
        b = M.core_loss_steinmetz(2e5, 0.05, K, A, B,
                                  waveform="triangle")["value"]["ratio_to_sine"]
        self.assertAlmostEqual(a, b, delta=1e-9)

    def test_a_sine_is_not_a_triangle(self):
        # the direction, not just the difference: for alpha > 1 the plain Steinmetz
        # answer is an OVER-estimate (the sine spends more of its period at the steep
        # part of the swing), for alpha < 1 an under-estimate, and alpha = 1 is the
        # exact crossover because both have the same mean |dB/dt|.
        for alpha, expect_flag in ((1.4, "overestimate"), (2.7, "overestimate"),
                                   (0.7, "underestimate")):
            v = M.core_loss_steinmetz(1e5, 0.1, K, alpha, B, waveform="triangle")["value"]
            self.assertEqual(v["sine_equiv_is"], expect_flag)
        self.assertAlmostEqual(M._igse_shape_ratio(
            1.0, [(1.0, 0.5), (-1.0, 0.5)]), 1.0, delta=1e-12)

    def test_the_shortcut_answer_is_printed_next_to_the_real_one(self):
        v = M.core_loss_steinmetz(1e5, 0.1, K, A, B, waveform="triangle")["value"]
        self.assertAlmostEqual(v["P_v_sine_equiv_w_m3"]
                               / (K * 1e5 ** A * 0.1 ** B), 1.0, delta=1e-5)
        self.assertAlmostEqual(v["ratio_to_sine"],
                               v["P_v_w_m3"] / v["P_v_sine_equiv_w_m3"],
                               delta=1e-5)
        self.assertAlmostEqual(v["flux_swing_pp_t"], 0.2, delta=1e-12)
        self.assertEqual(len(v["segments_used"]), 2)
        self.assertAlmostEqual(v["segments_used"][0]["dB_dt_t_s"], 4e4, delta=1e-6)


class TestPiecewise(unittest.TestCase):
    def test_a_piecewise_triangle_lands_on_the_triangle(self):
        tri = M.core_loss_steinmetz(1e5, 0.1, K, A, B, waveform="triangle")["value"]
        for duty in (0.5, 0.3):
            seg = [[0.2, duty], [-0.2, 1.0 - duty]]
            p = M.core_loss_steinmetz(1e5, None, K, A, B, waveform="piecewise",
                                      segments=seg)["value"]
            t = M.core_loss_steinmetz(1e5, 0.1, K, A, B, waveform="triangle",
                                      duty=duty)["value"]
            self.assertAlmostEqual(p["P_v_w_m3"] / t["P_v_w_m3"], 1.0, delta=1e-12)
            self.assertAlmostEqual(p["b_peak_t"], 0.1, delta=1e-12)

    def test_a_staircase_sine_is_not_the_sine(self):
        """The control for the test above: piecewise does not force any shape."""
        n = 8
        seg = []
        for i in range(n):
            b0 = 0.1 * math.sin(2.0 * math.pi * i / n)
            b1 = 0.1 * math.sin(2.0 * math.pi * (i + 1) / n)
            seg.append([b1 - b0, 1.0 / n])
        p = M.core_loss_steinmetz(1e5, None, K, A, B, waveform="piecewise",
                                  segments=seg)["value"]
        sine = K * 1e5 ** A * 0.1 ** B
        # 8 straight chords are not a sine, and the answer must say so
        self.assertGreater(abs(p["P_v_w_m3"] / sine - 1.0), 0.01)
        # ... and it must be close to it, which is the other half of the control
        self.assertLess(abs(p["P_v_w_m3"] / sine - 1.0), 0.10)

    def test_a_square_wave_costs_more_than_a_triangle(self):
        # finite edge time so the slope is defined; the flat tops contribute nothing
        edge = 0.02
        seg = [[0.2, edge], [0.0, 0.5 - edge], [-0.2, edge], [0.0, 0.5 - edge]]
        sq = M.core_loss_steinmetz(1e5, None, K, A, B, waveform="piecewise",
                                   segments=seg)["value"]
        tri = M.core_loss_steinmetz(1e5, 0.1, K, A, B, waveform="triangle")["value"]
        self.assertGreater(sq["P_v_w_m3"], tri["P_v_w_m3"])

    def test_b_peak_is_a_check_not_a_decoration(self):
        seg = [[0.2, 0.5], [-0.2, 0.5]]
        ok = M.core_loss_steinmetz(1e5, 0.1, K, A, B, waveform="piecewise",
                                   segments=seg)
        self.assertEqual(ok["status"], "ok")
        bad = M.core_loss_steinmetz(1e5, 0.15, K, A, B, waveform="piecewise",
                                    segments=seg)
        self.assertEqual(bad["reason_code"], "ARG_INVALID")


class TestUnitConventionsStillAgree(unittest.TestCase):
    def test_the_three_conventions_are_one_fit_for_a_triangle_too(self):
        k_mt = K / (1e3 * 10 ** (3 * (B - A)))
        k_g = K / (1e3 * 10 ** (4 * B - 3 * A))
        vals = [M.core_loss_steinmetz(1e5, 0.1, k, A, B, units=u,
                                      waveform="triangle")["value"]["P_v_w_m3"]
                for k, u in ((K, "si"), (k_mt, "khz_mt"), (k_g, "khz_gauss"))]
        for v in vals[1:]:
            self.assertAlmostEqual(v / vals[0], 1.0, delta=1e-9)


class TestRefusals(unittest.TestCase):
    """Every one of these would otherwise be a different question answered."""

    def test_unknown_waveform(self):
        r = M.core_loss_steinmetz(1e5, 0.1, K, A, B, waveform="sawtooth")
        self.assertEqual(r["reason_code"], "ARG_INVALID")
        self.assertIn("sine, triangle, piecewise", r["message"])

    def test_duty_is_not_ignored_on_a_sine(self):
        r = M.core_loss_steinmetz(1e5, 0.1, K, A, B, duty=0.5)
        self.assertEqual(r["reason_code"], "ARG_INVALID")
        self.assertIn("triangle", r["message"])

    def test_segments_are_not_ignored_on_a_triangle(self):
        r = M.core_loss_steinmetz(1e5, 0.1, K, A, B, waveform="triangle",
                                  segments=[[0.2, 0.5], [-0.2, 0.5]])
        self.assertEqual(r["reason_code"], "ARG_INVALID")

    def test_piecewise_without_segments(self):
        self.assertEqual(M.core_loss_steinmetz(1e5, None, K, A, B,
                                               waveform="piecewise")["reason_code"],
                         "ARG_INVALID")
        self.assertEqual(M.core_loss_steinmetz(1e5, None, K, A, B,
                                               waveform="piecewise",
                                               segments=[[0.2, 1.0]])["reason_code"],
                         "ARG_INVALID")

    def test_duty_out_of_range(self):
        for d in (0.0, 1.0, -0.2, 1.5):
            self.assertEqual(M.core_loss_steinmetz(1e5, 0.1, K, A, B,
                                                   waveform="triangle",
                                                   duty=d)["reason_code"],
                             "ARG_RANGE")

    def test_time_must_sum_to_one(self):
        r = M.core_loss_steinmetz(1e5, None, K, A, B, waveform="piecewise",
                                  segments=[[0.1, 0.4], [-0.1, 0.4]])
        self.assertEqual(r["reason_code"], "ARG_RANGE")
        self.assertIn("sum to 1", r["message"])

    def test_the_trajectory_must_close(self):
        r = M.core_loss_steinmetz(1e5, None, K, A, B, waveform="piecewise",
                                  segments=[[0.1, 0.5], [-0.05, 0.5]])
        self.assertEqual(r["reason_code"], "ARG_RANGE")
        self.assertIn("does not close", r["message"])

    def test_zero_swing_and_zero_length_rows(self):
        self.assertEqual(M.core_loss_steinmetz(1e5, None, K, A, B,
                                               waveform="piecewise",
                                               segments=[[0.0, 0.5], [0.0, 0.5]])
                         ["reason_code"], "ARG_RANGE")
        self.assertEqual(M.core_loss_steinmetz(1e5, None, K, A, B,
                                               waveform="piecewise",
                                               segments=[[0.1, 1.0], [-0.1, 0.0]])
                         ["reason_code"], "ARG_RANGE")

    def test_malformed_rows(self):
        for seg in ([[0.1, 0.5], [-0.1]], "not a list", [1.0, 2.0], None):
            self.assertEqual(M.core_loss_steinmetz(1e5, None, K, A, B,
                                                   waveform="piecewise",
                                                   segments=seg)["reason_code"],
                             "ARG_INVALID")

    def test_sine_still_needs_an_amplitude(self):
        r = M.core_loss_steinmetz(1e5, None, K, A, B)
        self.assertEqual(r["status"], "error")


class TestSelectCoreWaveform(unittest.TestCase):
    REQ = dict(inductance_h=100e-6, i_peak_a=3.0, freq_hz=1e5, b_ac_t=0.1,
               loss_exponents=(1.3, 2.5), material="N87")

    def test_sine_is_the_default_and_says_nothing_extra(self):
        a = M.select_core(**self.REQ)["value"]
        b = M.select_core(flux_waveform="sine", **self.REQ)["value"]
        self.assertEqual(a, b)
        self.assertNotIn("flux_waveform", a)

    def test_triangle_scales_the_loss_and_nothing_else(self):
        sine = M.select_core(**self.REQ)["value"]
        tri = M.select_core(flux_waveform="triangle", **self.REQ)["value"]
        self.assertIn("flux_waveform", tri)
        ratio = M._igse_shape_ratio(1.3, [(1.0, 0.5), (-1.0, 0.5)])
        by_part = {c["part"]: c for c in sine["candidates"]}
        seen = 0
        for c in tri["candidates"]:
            s = by_part[c["part"]]
            # the turns, the flux and the gap are shape-independent
            self.assertEqual(c["turns"], s["turns"])
            self.assertEqual(c["b_peak_t"], s["b_peak_t"])
            self.assertEqual(c["gap_required"], s["gap_required"])
            if s["core_loss_w"] is not None:
                seen += 1
                self.assertAlmostEqual(c["core_loss_w"] / s["core_loss_w"], ratio,
                                       delta=1e-5)
                self.assertIn("iGSE shape ratio", c["core_loss_basis"])
        self.assertGreater(seen, 0)

    def test_a_duty_moves_the_loss_the_right_way(self):
        a = M.select_core(flux_waveform="triangle", duty=0.5, **self.REQ)["value"]
        b = M.select_core(flux_waveform="triangle", duty=0.2, **self.REQ)["value"]
        # a narrower pulse means a steeper slope on one side - more loss, not less
        self.assertGreater(b["candidates"][0]["core_loss_w"],
                           a["candidates"][0]["core_loss_w"])

    def test_refuses_a_shape_it_cannot_pay_for(self):
        r = M.select_core(inductance_h=100e-6, i_peak_a=3.0, freq_hz=1e5,
                          b_ac_t=0.1, material="N87", flux_waveform="triangle")
        self.assertEqual(r["status"], "ok")     # the core is still judged
        c = r["value"]["candidates"][0]
        self.assertIsNone(c["core_loss_w"])
        self.assertIn("loss_exponents", c["core_loss_basis"])

    def test_argument_errors(self):
        self.assertEqual(M.select_core(inductance_h=100e-6, i_peak_a=3.0,
                                       flux_waveform="saw")["reason_code"],
                         "ARG_INVALID")
        self.assertEqual(M.select_core(inductance_h=100e-6, i_peak_a=3.0,
                                       flux_waveform="piecewise")["reason_code"],
                         "ARG_INVALID")
        self.assertEqual(M.select_core(inductance_h=100e-6, i_peak_a=3.0,
                                       duty=0.5)["reason_code"], "ARG_INVALID")
        self.assertEqual(M.select_core(inductance_h=100e-6, i_peak_a=3.0,
                                       flux_waveform="triangle",
                                       duty=0.0)["reason_code"], "ARG_RANGE")


class TestTools(unittest.TestCase):
    def test_the_schema_carries_the_new_arguments(self):
        for name, keys in (("magnetics_core_loss",
                            ("waveform", "duty", "segments")),
                          ("magnetics_select_core",
                           ("flux_waveform", "duty"))):
            d = tools.TOOLS[name]
            schema = d.get("inputSchema") or d
            for k in keys:
                self.assertIn(k, schema["properties"], (name, k))
        self.assertEqual(
            tools.TOOLS["magnetics_core_loss"].get("inputSchema", tools.TOOLS["magnetics_core_loss"])
            ["properties"]["waveform"].get("default"), "sine")

    def test_the_tool_answers_a_triangle(self):
        r = tools.call_tool("magnetics_core_loss",
                            {"f_hz": 1e5, "b_peak_t": 0.1, "k": K, "alpha": A,
                             "beta": B, "waveform": "triangle"})
        self.assertEqual(r["status"], "ok")
        self.assertEqual(r["value"]["waveform"], "triangle")
        self.assertAlmostEqual(r["value"]["ratio_to_sine"],
                               M._igse_shape_ratio(A, [(1.0, 0.5), (-1.0, 0.5)]),
                               delta=1e-5)

    def test_the_tool_answers_piecewise(self):
        r = tools.call_tool("magnetics_core_loss",
                            {"f_hz": 1e5, "k": K, "alpha": A, "beta": B,
                             "waveform": "piecewise",
                             "segments": [[0.2, 0.5], [-0.2, 0.5]]})
        self.assertEqual(r["status"], "ok")
        self.assertAlmostEqual(r["value"]["flux_swing_pp_t"], 0.2, delta=1e-12)

    def test_the_tool_refuses_a_broken_trajectory(self):
        r = tools.call_tool("magnetics_core_loss",
                            {"f_hz": 1e5, "b_peak_t": 0.1, "k": K, "alpha": A,
                             "beta": B, "waveform": "piecewise",
                             "segments": [[0.2, 0.5], [-0.1, 0.5]]})
        self.assertEqual(r["reason_code"], "ARG_RANGE")

    def test_the_tool_count_did_not_move(self):
        # 0.0.71 adds arguments to two existing tools, not tools: the ratchet is a
        # count, and silently growing the surface is how a tool list rots.
        # 0.0.72 does add one (magnetics_fringing_loss): 173 -> 174
        # 0.0.74 adds one more (magnetics_material_core_loss): 174 -> 175
        self.assertEqual(len(tools.TOOLS), 176)


if __name__ == "__main__":
    unittest.main()
