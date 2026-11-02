"""Tests for the 0.0.83 additions: modal participation and random response
summed over several modes.

Same discipline as the other vibration tests: no expected number is a run of the
code under test. Each one is one of

  * an ANALYTIC number that this code has no way to produce except by getting the
    integral right - for an SSSS square plate Gamma_(1,1) = 16/pi^2, every mode
    with an even index along one axis has exactly zero net deflection, and the
    capture fraction of the first mode is (16/pi^2)^2/4;
  * a closed form from single-DOF random vibration - Miles'
    g = sqrt(pi/2 * f * Q * W), which the modal summation must reproduce in the
    one-mode limit;
  * an independent numerical integration (much finer grid, its own loop) of the
    very integrand the tool claims to integrate, so a wrong factor, a dropped
    cross term or an unconverged peak shows up as a disagreement rather than as
    the code agreeing with itself;
  * hand arithmetic from what the tool reports (shares summing to 100 %).

What this file is guarding against, in order of how quietly it would go wrong:

  a) a "sum of squares" that is not summed - the second mode silently truncated,
     or a mode with Gamma = 0 still contributing - TestZeroParticipationAddsNothing
  b) the participation factor replaced by a fitted weight (a constant per mode):
     the even modes would stop being zero - TestEvenModesHaveNoNetDeflection
  c) the coherent cross term dropped from the displacement while still being
     reported (a number that describes something nobody computed) -
     TestTheCoherentShareIsRealAndMatchesAnIndependentIntegral
  d) the relative-acceleration integrand |w^2 D|^2 used for g: it tends to a
     constant at high frequency, so any bandwidth integral of it is dominated by
     a term that is NOT resonant - TestAsymptoticAccelerationIntegrandIsRejected
  e) the "participation" knob changing the answer when it is not asked for -
     TestDefaultOffChangesNothing
"""
import math
import os
import sys
import unittest

sys.path.insert(0, os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "src"))

from pcb_agent_mcp.calc import vibration as V  # noqa: E402

G0 = 9.80665          # m/s^2 per g, the constant the tool documents
TP = 2.0 * math.pi
GAMMA_11 = 16.0 / math.pi ** 2          # 1.6211...

A_MM = 100.0
H_MM = 1.6


def _plate(n_modes=4, participation=True, **kw):
    args = dict(a_mm=A_MM, b_mm=A_MM, thickness_mm=H_MM, material="fr4",
                mount="edges", n_modes=n_modes, participation=participation)
    args.update(kw)
    r = V.plate_modes(**args)
    assert r["status"] == "ok", r
    return r["value"]


def _resp(modes, psd=None, **kw):
    r = V.miles_response(None, psd=psd if psd is not None else {"g2_per_hz": 0.1},
                         modes=modes, **kw)
    assert r["status"] == "ok", r
    return r["value"]


def _mode(fr, participation=1.0, shape=1.0):
    return {"freq_hz": fr, "participation": participation, "shape_at_point": shape}


# --------------------------------------------------------------------------
# an independent evaluation of the integrals the tool reports, on a much finer
# grid and with its own loop. Nothing here is imported from the module.
# --------------------------------------------------------------------------

def _transfer(f, fr, zeta, c):
    w = TP * f
    wi = TP * fr
    return c / complex(wi * wi - w * w, 2.0 * zeta * w * wi)


def _independent_integrals(modes, w_of_f, f_lo, f_hi, n_coarse=4000, n_fine=8000):
    """Return (int |sum D|^2 df, int |1 - w^2 sum D|^2 df, [int |D_k|^2 df])."""
    grid = [f_lo + (f_hi - f_lo) * i / float(n_coarse) for i in range(n_coarse + 1)]
    for fr, zeta, _c in modes:
        d = max(12.0 * zeta, 0.02)
        a0 = max(f_lo, fr * (1.0 - d))
        a1 = min(f_hi, fr * (1.0 + d))
        if a1 > a0:
            grid.extend([a0 + (a1 - a0) * i / float(n_fine)
                         for i in range(n_fine + 1)])
    grid.sort()
    tot = [0.0, 0.0]
    per = [0.0] * len(modes)
    prev = None
    for f in grid:
        w = TP * f
        s = 0j
        ds = []
        for fr, zeta, c in modes:
            d = _transfer(f, fr, zeta, c)
            s += d
            ds.append(d)
        wf = w_of_f(f)
        cur = (wf * abs(s) ** 2, wf * abs(1.0 - w * w * s) ** 2,
               [wf * abs(d) ** 2 for d in ds])
        if prev is not None:
            h = f - prev[0]
            if h > 0.0:
                tot[0] += 0.5 * (prev[1][0] + cur[0]) * h
                tot[1] += 0.5 * (prev[1][1] + cur[1]) * h
                for k in range(len(modes)):
                    per[k] += 0.5 * (prev[1][2][k] + cur[2][k]) * h
        prev = (f, cur)
    return tot[0], tot[1], per


class TestParticipationIsAnIntegralNotAWeight(unittest.TestCase):
    """Gamma = int w dA / int w^2 dA, with w the shape normalised to peak 1."""

    def test_the_first_ssss_mode_is_16_over_pi_squared(self):
        p = _plate()["participation"]
        got = p["modes"][0]["participation"]
        # 16/pi^2 comes from int sin sin / int sin^2 sin^2 on a square: nothing
        # in the Ritz assembly knows that number.
        self.assertAlmostEqual(got, GAMMA_11, delta=GAMMA_11 * 1e-3)

    def test_even_modes_have_no_net_deflection(self):
        # (1,2), (2,1) and (2,2) each have an even index, so their integral of
        # the shape over the plate is exactly zero and base acceleration cannot
        # excite them at all. A fitted weight could never come out this clean.
        p = _plate(n_modes=4)["participation"]
        for m in p["modes"][1:]:
            self.assertLess(abs(m["participation"]), 1e-9, m)

    def test_modal_mass_of_the_first_mode_is_a_quarter_of_the_plate(self):
        p = _plate()["participation"]
        # mpa * a * b * int phi^2 with phi = sin(pi x/a) sin(pi y/b) gives a
        # quarter of the plate mass for the (1,1) mode.
        self.assertAlmostEqual(p["modes"][0]["modal_mass_g"],
                               p["plate_mass_g"] / 4.0,
                               delta=p["plate_mass_g"] * 2e-3)
        # and the four modes together hold ALMOST the whole plate: 2.8 % of it
        # is missing because a degree-7 monomial basis represents sin(pi x/a) to
        # a few parts in a thousand, not exactly. The gap is the basis, not the
        # mass - and note the four modal masses alone are NOT a bound on the
        # plate mass once the shapes are peak-normalised (see n_modes = 9).
        self.assertAlmostEqual(p["modal_mass_sum_g"], p["plate_mass_g"],
                               delta=p["plate_mass_g"] * 0.05)
        self.assertLess(p["modal_mass_sum_g"], p["plate_mass_g"])

    def test_mass_capture_of_the_first_mode_is_gamma_squared_over_four(self):
        p = _plate()["participation"]
        # effective mass / total mass = Gamma^2 * (1/4) for (1,1) alone
        self.assertAlmostEqual(p["mass_capture_fraction"],
                               GAMMA_11 ** 2 / 4.0,
                               delta=1e-3)

    def test_capture_never_exceeds_one_and_rises_with_the_basis(self):
        # poly_degree held fixed: the capture fraction is a property of the mode
        # SET, so comparing two different bases would compare two different
        # shapes and prove nothing.
        last = 0.0
        for n in (1, 2, 4, 9):
            f = _plate(n_modes=n, poly_degree=7)["participation"][
                "mass_capture_fraction"]
            self.assertLessEqual(f, 1.0 + 1e-9, n)
            self.assertGreaterEqual(f, last - 1e-12, n)
            last = f
        # (1,1) alone is 65.7 %; adding (1,2)/(2,1) adds nothing because their
        # Gamma is zero; the ninth mode does add, and it stays under 1
        self.assertGreater(last, 0.65)

    def test_effective_mass_is_gamma_squared_times_modal_mass(self):
        for m in _plate(n_modes=4)["participation"]["modes"]:
            self.assertAlmostEqual(m["effective_mass_g"],
                                   m["modal_mass_g"] * m["participation"] ** 2,
                                   delta=max(1e-6, abs(m["modal_mass_g"]) * 1e-4))

    def test_the_two_places_that_report_effective_mass_agree_in_grams(self):
        # modes[i] (per mode) and participation.modes[i] (the summary) are built
        # in two different spots; a kg/g slip in one of them is only visible by
        # putting them side by side.
        v = _plate(n_modes=4)
        for a, b in zip(v["modes"], v["participation"]["modes"]):
            self.assertEqual(a["effective_mass_g"], b["effective_mass_g"])
            self.assertEqual(a["modal_mass_g"], b["modal_mass_g"])
        # and the effective masses sum to the capture fraction times the plate
        p = v["participation"]
        self.assertAlmostEqual(p["effective_mass_sum_g"] / p["plate_mass_g"],
                               p["mass_capture_fraction"], delta=1e-3)


class TestDefaultOffChangesNothing(unittest.TestCase):

    def test_participation_block_is_absent_unless_asked(self):
        v = _plate(participation=False)
        self.assertTrue(v.get("participation") is None)

    def test_the_rest_of_the_plate_answer_is_identical(self):
        on = _plate(participation=True)
        off = _plate(participation=False)
        # the eigenvalue problem is untouched: same basis, same frequencies, same
        # frequency order - only the extra per-mode keys are added
        self.assertEqual(on["basis"], off["basis"])
        self.assertEqual([m["freq_hz"] for m in on["modes"]],
                         [m["freq_hz"] for m in off["modes"]])
        self.assertEqual([m["lambda"] for m in on["modes"]],
                         [m["lambda"] for m in off["modes"]])
        self.assertNotIn("participation", off["modes"][0])
        self.assertIn("participation", on["modes"][0])

    def test_one_modal_question_without_modes_is_the_old_answer(self):
        a = V.miles_response(300.0, psd={"g2_per_hz": 0.1}, q_factor=10.0)["value"]
        # Miles in closed form, from the fixture and nothing else
        expect = math.sqrt(0.5 * math.pi * 300.0 * 10.0 * 0.1)
        self.assertAlmostEqual(a["g_rms"], expect, delta=expect * 1e-3)
        self.assertNotIn("per_mode", a)


class TestOneModeReducesToMiles(unittest.TestCase):

    def test_g_rms_matches_miles(self):
        v = _resp([_mode(300.0)], q_factor=10.0)
        expect = math.sqrt(0.5 * math.pi * 300.0 * 10.0 * 0.1)   # 21.708 g
        self.assertAlmostEqual(v["g_rms"], expect, delta=expect * 1e-2)
        self.assertAlmostEqual(v["ratio_to_miles"], 1.0, delta=1e-3)

    def test_displacement_matches_miles(self):
        v = _resp([_mode(300.0)], q_factor=10.0)
        # Miles' displacement is g / w^2; both routes must land on the same mm.
        z_mm = G0 * math.sqrt(0.5 * math.pi * 300.0 * 10.0 * 0.1) / (TP * 300.0) ** 2 * 1000.0
        self.assertAlmostEqual(v["displacement_rms_mm"], z_mm, delta=z_mm * 1e-3)
        self.assertAlmostEqual(v["displacement_ratio_to_miles"], 1.0, delta=1e-3)

    def test_g_rows_as_sqrt_of_frequency(self):
        a = _resp([_mode(100.0)], q_factor=25.0)["g_rms"]
        b = _resp([_mode(400.0)], q_factor=25.0)["g_rms"]
        self.assertAlmostEqual(b / a, 2.0, delta=0.02)

    def test_peak_is_the_peak_factor_times_the_rms(self):
        v = _resp([_mode(200.0)], q_factor=20.0, gamma=3.0)
        self.assertAlmostEqual(v["g_peak"], 3.0 * v["g_rms"], delta=1e-3 * v["g_peak"])
        self.assertGreaterEqual(v["g_rms_absolute_including_quasistatic"],
                                v["g_rms"] * 0.999)

    def test_the_displacement_integral_converges_on_an_independent_grid(self):
        # Fine grid, own loop: if the tool's 600 + 1200 grid were too coarse the
        # two numbers would part company here.
        zeta = 1.0 / 100.0          # q_factor = 50 below
        modes = [(200.0, zeta, 1.0)]
        i_z = _independent_integrals(modes, lambda f: 0.1, 0.0, 600.0)[0]
        expect_mm = G0 * math.sqrt(i_z) * 1000.0
        got = _resp([_mode(200.0)], q_factor=50.0)["displacement_rms_mm"]
        self.assertAlmostEqual(got, expect_mm, delta=expect_mm * 5e-3)


class TestSeveralModes(unittest.TestCase):

    def test_two_far_modes_add_as_squares(self):
        # 100 and 1000 Hz are 10x apart: no overlap, so the variance adds.
        a = _resp([_mode(100.0)], q_factor=25.0)["g_rms"]
        b = _resp([_mode(1000.0)], q_factor=25.0)["g_rms"]
        both = _resp([_mode(100.0), _mode(1000.0)], q_factor=25.0)
        self.assertAlmostEqual(both["g_rms"], math.hypot(a, b),
                               delta=0.02 * math.hypot(a, b))
        self.assertLess(abs(both["coherent_displacement_variance_share_pct"]), 1.0)
        self.assertEqual(both["modes_included"], 2)
        self.assertEqual(both["strongest_mode"], 2)

    def test_the_four_modal_answers_collapse_to_one_on_a_real_plate(self):
        # On an SSSS square only (1,1) has a non-zero participation, so a run
        # that carries all four modes must give exactly the single-mode answer.
        # This is what catches "sum the dead modes too".
        rows = _plate(n_modes=4)["participation"]["modes"]
        modes = [{"freq_hz": m["freq_hz"], "participation": m["participation"],
                  "shape_at_point": 1.0} for m in rows]
        four = _resp(modes, q_factor=25.0)
        one = _resp([_mode(rows[0]["freq_hz"],
                           participation=rows[0]["participation"])], q_factor=25.0)
        self.assertEqual(four["modes_included"], 4)
        self.assertAlmostEqual(four["g_rms"], one["g_rms"], delta=one["g_rms"] * 1e-3)
        self.assertLess(abs(four["coherent_displacement_variance_share_pct"]), 1e-6)

    def test_a_neighbouring_mode_is_not_the_single_mode_answer(self):
        # 100 and 105 Hz with Q = 25 (zeta = 0.02): the peaks overlap and the
        # single-mode answer is simply wrong, by 40 %.
        one = _resp([_mode(100.0)], q_factor=25.0)
        two = _resp([_mode(100.0), _mode(105.0)], q_factor=25.0)
        self.assertGreater(two["g_rms"], 1.3 * one["g_rms"])
        self.assertGreater(two["coherent_displacement_variance_share_pct"], 20.0)

    def test_the_coherent_share_is_real_and_matches_an_independent_integral(self):
        zeta = 0.02
        modes = [(100.0, zeta, 1.0), (105.0, zeta, 1.0)]
        i_z, _i_g, per = _independent_integrals(modes, lambda f: 0.1, 0.0, 315.0)
        diag = sum(per)
        expect_share = 100.0 * (i_z - diag) / i_z
        got = _resp([_mode(100.0), _mode(105.0)], zeta=zeta)
        # the reported displacement itself is the coherent sum
        self.assertAlmostEqual(got["displacement_rms_mm"],
                               G0 * math.sqrt(i_z) * 1000.0,
                               delta=G0 * math.sqrt(i_z) * 1000.0 * 5e-3)
        self.assertAlmostEqual(got["coherent_displacement_variance_share_pct"],
                               expect_share, delta=0.5)

    def test_zero_participation_adds_nothing(self):
        one = _resp([_mode(300.0)], q_factor=10.0)["g_rms"]
        two = _resp([_mode(300.0), _mode(400.0, participation=0.0)], q_factor=10.0)
        self.assertAlmostEqual(two["g_rms"], one, delta=one * 1e-3)
        self.assertAlmostEqual(two["per_mode"][1]["g_rms_own"], 0.0, delta=1e-9)
        # and the reported share for the dead mode is exactly zero
        self.assertEqual(two["per_mode"][1]["share_of_resonant_variance_pct"], 0.0)

    def test_shares_sum_to_one_hundred(self):
        v = _resp([_mode(100.0), _mode(105.0), _mode(200.0, participation=0.5)],
                  q_factor=25.0)
        s = sum(m["share_of_resonant_variance_pct"] for m in v["per_mode"])
        self.assertAlmostEqual(s, 100.0, delta=0.05)

    def test_shape_enters_as_its_square(self):
        a = _resp([_mode(300.0, shape=1.0)], q_factor=10.0)["g_rms"]
        b = _resp([_mode(300.0, shape=0.5)], q_factor=10.0)["g_rms"]
        self.assertAlmostEqual(b / a, 0.5, delta=0.01)
        # participation and shape multiply, so swapping them is the same mode
        c = _resp([_mode(300.0, participation=0.5, shape=1.0)], q_factor=10.0)["g_rms"]
        self.assertAlmostEqual(c, b, delta=b * 1e-3)

    def test_a_finer_grid_is_built_for_every_peak(self):
        one = _resp([_mode(300.0)], q_factor=10.0)
        two = _resp([_mode(300.0), _mode(310.0)], q_factor=10.0)
        self.assertGreater(two["grid_points"], one["grid_points"])

    def test_the_asymptotic_acceleration_integrand_is_not_what_is_reported(self):
        # int W |w^2 D|^2 df tends to c^2 * (band width) at high frequency, so on
        # a two-mode case 10x apart it would report far more than the resonant
        # sum. Naming the number that would come out of it makes the choice
        # falsifiable instead of a matter of taste.
        zeta = 0.02
        modes = [(100.0, zeta, 1.0), (1000.0, zeta, 1.0)]
        i_z, i_g_abs, per = _independent_integrals(
            modes, lambda f: 0.1, 0.0, 3000.0, n_coarse=12000, n_fine=2000)
        resonant = math.sqrt(sum(((TP * fr) ** 2 * math.sqrt(v)) ** 2
                                 for (fr, _z, _c), v in zip(modes, per)))
        got = _resp([_mode(100.0), _mode(1000.0)], zeta=zeta)
        self.assertAlmostEqual(got["g_rms"], resonant, delta=0.02 * resonant)
        # the number the rejected integrand would have given:
        wrong = math.sqrt(i_g_abs)   # in g: G0 * sqrt(.) m/s^2 over G0
        self.assertGreater(wrong, 1.15 * got["g_rms"])


class TestDampingPerMode(unittest.TestCase):

    def test_a_per_mode_zeta_overrides_the_global_one(self):
        a = _resp([_mode(300.0)], q_factor=10.0)["g_rms"]
        b = _resp([{"freq_hz": 300.0, "participation": 1.0,
                    "shape_at_point": 1.0, "q_factor": 40.0}])["g_rms"]
        self.assertAlmostEqual(b / a, 2.0, delta=0.02)   # sqrt(40/10)
        v = _resp([{"freq_hz": 300.0, "participation": 1.0, "shape_at_point": 1.0,
                    "zeta": 0.05}], q_factor=10.0)
        self.assertAlmostEqual(v["per_mode"][0]["q_factor"], 10.0, delta=0.01)

    def test_the_first_modes_damping_is_the_reported_one(self):
        v = _resp([{"freq_hz": 300.0, "participation": 1.0, "shape_at_point": 1.0,
                    "zeta": 0.02}, _mode(320.0)], q_factor=5.0)
        self.assertAlmostEqual(v["zeta"], 0.02, delta=1e-9)

    def test_no_damping_anywhere_is_refused(self):
        r = V.miles_response(None, psd={"g2_per_hz": 0.1}, modes=[_mode(300.0)])
        self.assertEqual(r["reason_code"], "ARG_REQUIRED")
        r = V.miles_response(None, psd={"g2_per_hz": 0.1}, q_factor=0.5,
                             modes=[_mode(300.0)])
        self.assertEqual(r["reason_code"], "ARG_INVALID")


class TestRefusals(unittest.TestCase):
    """Every way to ask an impossible question is its own refusal."""

    def test_modes_must_be_a_non_empty_list(self):
        for bad in ([], {}, "300", 3):
            r = V.miles_response(None, psd={"g2_per_hz": 0.1}, q_factor=10.0,
                                 modes=bad)
            self.assertEqual(r["reason_code"], "ARG_REQUIRED", bad)
        # modes=None means "not asked for", i.e. the single-mode path, so that
        # is refused for the missing f_n_hz instead of being called empty
        r = V.miles_response(None, psd={"g2_per_hz": 0.1}, q_factor=10.0,
                            modes=None)
        self.assertEqual(r["reason_code"], "ARG_INVALID")

    def test_two_hundred_and_one_modes_is_too_many(self):
        r = V.miles_response(None, psd={"g2_per_hz": 0.1}, q_factor=10.0,
                             modes=[_mode(100.0 + i) for i in range(201)])
        self.assertEqual(r["reason_code"], "TOO_LARGE")
        r = V.miles_response(None, psd={"g2_per_hz": 0.1}, q_factor=10.0,
                             modes=[_mode(100.0 + i) for i in range(200)])
        self.assertEqual(r["status"], "ok")

    def test_each_bad_mode_is_named_by_index(self):
        cases = [
            ({"participation": 1.0, "shape_at_point": 1.0}, "freq_hz"),
            ({"freq_hz": 0.0, "participation": 1.0, "shape_at_point": 1.0}, "freq_hz"),
            ({"freq_hz": -3.0, "participation": 1.0, "shape_at_point": 1.0}, "freq_hz"),
            ({"freq_hz": 300.0, "shape_at_point": 1.0}, "participation"),
            ({"freq_hz": 300.0, "participation": "x", "shape_at_point": 1.0},
             "participation"),
            ({"freq_hz": 300.0, "participation": 1.0}, "shape_at_point"),
        ]
        for m, field in cases:
            r = V.miles_response(None, psd={"g2_per_hz": 0.1}, q_factor=10.0,
                                 modes=[_mode(100.0), m])
            self.assertEqual(r["reason_code"], "ARG_INVALID", m)
            self.assertIn("modes[1].%s" % field, r["message"])

    def test_shape_is_accepted_as_an_alias(self):
        a = _resp([{"freq_hz": 300.0, "participation": 1.0, "shape": 0.5}],
                  q_factor=10.0)
        b = _resp([_mode(300.0, shape=0.5)], q_factor=10.0)
        self.assertEqual(a["g_rms"], b["g_rms"])

    def test_psd_must_carry_a_level(self):
        r = V.miles_response(None, psd={}, q_factor=10.0, modes=[_mode(300.0)])
        self.assertEqual(r["reason_code"], "ARG_REQUIRED")
        r = V.miles_response(None, psd=None, q_factor=10.0, modes=[_mode(300.0)])
        self.assertEqual(r["reason_code"], "ARG_REQUIRED")
        r = V.miles_response(None, psd={"g2_per_hz": 0.0}, q_factor=10.0,
                             modes=[_mode(300.0)])
        self.assertEqual(r["reason_code"], "ARG_INVALID")

    def test_points_need_two_breakpoints(self):
        r = V.miles_response(None, psd={"points": [[100.0, 0.1]]}, q_factor=10.0,
                             modes=[_mode(300.0)])
        self.assertEqual(r["reason_code"], "ARG_INVALID")

    def test_a_peak_factor_of_zero_is_refused(self):
        for g in (0.0, -2.0, "three"):
            r = V.miles_response(None, psd={"g2_per_hz": 0.1}, q_factor=10.0,
                                 gamma=g, modes=[_mode(300.0)])
            self.assertEqual(r["reason_code"], "ARG_INVALID", g)


class TestPsdCurve(unittest.TestCase):

    def test_a_flat_curve_given_as_points_agrees_with_g2_per_hz(self):
        a = _resp([_mode(200.0)], q_factor=25.0)
        b = _resp([_mode(200.0)], q_factor=25.0,
                  psd={"points": [[1.0, 0.1], [5000.0, 0.1]]})
        self.assertAlmostEqual(b["g_rms"], a["g_rms"], delta=a["g_rms"] * 0.02)
        self.assertEqual(b["warnings"], [])

    def test_a_curve_that_stops_below_the_peak_is_flagged_not_silently_clamped(self):
        r = V.miles_response(None, psd={"points": [[10.0, 0.1], [400.0, 0.1]]},
                             q_factor=25.0, modes=[_mode(200.0)])
        self.assertEqual(r["status"], "ok")
        w = " ".join(r["value"]["warnings"])
        self.assertIn("3*the highest mode", w)
        self.assertEqual(r["value"]["integration_band_hz"][1], 600.0)

    def test_a_coarse_curve_is_integrated_not_read_at_the_peak(self):
        # 0 above 400 Hz versus flat: the answer has to follow the curve, so a
        # peak-height reading would report the same number for both.
        a = V.miles_response(None, psd={"points": [[10.0, 0.1], [400.0, 0.0]]},
                             q_factor=25.0, modes=[_mode(200.0)])
        b = V.miles_response(None, psd={"points": [[10.0, 0.1], [400.0, 0.1]]},
                             q_factor=25.0, modes=[_mode(200.0)])
        self.assertEqual(a["status"], "ok")
        self.assertGreater(b["value"]["displacement_rms_mm"],
                           a["value"]["displacement_rms_mm"] * 1.0)
        self.assertLess(a["value"]["g_rms_absolute_including_quasistatic"],
                        b["value"]["g_rms_absolute_including_quasistatic"])


class TestToolSurface(unittest.TestCase):

    def test_modes_is_an_argument_and_psd_is_the_only_required_one(self):
        from pcb_agent_mcp import tools
        spec = tools.TOOLS["vibration_random_response"]
        self.assertEqual(spec["inputSchema"]["required"], ["psd"])
        self.assertIn("modes", spec["inputSchema"]["properties"])
        self.assertIn("f_n_hz", spec["inputSchema"]["properties"])

    def test_the_handler_forwards_modes(self):
        from pcb_agent_mcp import tools
        h = tools.TOOLS["vibration_random_response"]["handler"]
        r = h({"psd": {"g2_per_hz": 0.1}, "q_factor": 10.0,
               "modes": [_mode(300.0)]})
        self.assertEqual(r["status"], "ok")
        self.assertEqual(r["value"]["modes_included"], 1)
        self.assertAlmostEqual(r["value"]["g_rms"], 21.708, delta=0.217)

    def test_neither_f_n_hz_nor_modes_is_refused(self):
        from pcb_agent_mcp import tools
        h = tools.TOOLS["vibration_random_response"]["handler"]
        r = h({"psd": {"g2_per_hz": 0.1}, "q_factor": 10.0})
        self.assertEqual(r["status"], "error")
        self.assertEqual(r["reason_code"], "ARG_INVALID")

    def test_the_plate_tool_exposes_participation(self):
        from pcb_agent_mcp import tools
        spec = tools.TOOLS["vibration_plate_modes"]
        self.assertIn("participation", spec["inputSchema"]["properties"])
        h = spec["handler"]
        r = h({"a_mm": A_MM, "b_mm": A_MM, "thickness_mm": H_MM,
               "material": "fr4", "mount": "ssss", "n_modes": 2,
               "participation": True})
        self.assertEqual(r["status"], "ok")
        self.assertAlmostEqual(r["value"]["participation"]["modes"][0]["participation"],
                               GAMMA_11, delta=GAMMA_11 * 1e-3)
        # and leaving it out stays out
        r = h({"a_mm": A_MM, "b_mm": A_MM, "thickness_mm": H_MM,
               "material": "fr4", "mount": "ssss", "n_modes": 2})
        self.assertTrue(r["value"].get("participation") is None)


if __name__ == "__main__":
    unittest.main()
