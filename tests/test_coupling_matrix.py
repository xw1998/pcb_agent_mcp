"""The coupling-matrix filter engine, checked against things it does not compute with.

Three independent references are used here, and none of them is the module
checking itself:

  * an ABCD cascade of the same inverters, written out in this file, which is a
    completely different route to the S-parameters than the nodal solve;
  * filters._prototype_il_db, i.e. the prototype polynomial 1/(1+eps^2 Tn^2);
  * Matthaei's midband dissipation loss 4.343*sum(g)/(Qu*FBW).

The three self-audits the module runs internally (prototype agreement, unitarity,
and the cofactor formula for a trisection null) are also exercised, but the point
of the tests is to reach the same numbers from the outside: if the internal check
and the module were both wrong the same way, these would still catch it.

Two things the tests pin down that are easy to lose:

  * the in-band ripple must come off its own dense grid.  Reading it off the
    display sweep undersamples it (41 display points put about two samples inside
    the band), so the tests assert that points=41 and points=401 return the SAME
    ripple - if the ripple followed the display sweep, they would not.
  * unloaded Q enters the normalised network as Qu*FBW.  Raw Qu makes the
    dissipation look 1/FBW too small, so the test compares the insertion loss at
    two different bandwidths: halving FBW must roughly double the loss in dB.
"""
import math
import os
import sys
import unittest

sys.path.insert(0, os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "src"))

from pcb_agent_mcp.calc import filtermatrix as FM   # noqa: E402
from pcb_agent_mcp.calc import filters as FLT       # noqa: E402
from pcb_agent_mcp import tools                     # noqa: E402

Z0 = 1.0


def abcd_inverter(j):
    """ABCD of an ideal admittance inverter of value j: [[0, j/jj],[j*jj, 0]]."""
    return [[0.0, 1j / j], [1j * j, 0.0]]


def mul(a, b):
    return [[a[0][0] * b[0][0] + a[0][1] * b[1][0], a[0][0] * b[0][1] + a[0][1] * b[1][1]],
            [a[1][0] * b[0][0] + a[1][1] * b[1][0], a[1][0] * b[0][1] + a[1][1] * b[1][1]]]


def abcd_shunt(y):
    return [[1.0, 0.0], [y, 1.0]]


def abcd_series(z):
    return [[1.0, z], [0.0, 1.0]]


def cascade_s21_inline(g, n, omega, qu=None):
    """S21 of the inline filter from an ABCD cascade - no nodal solve involved.

    Source -> [J, shunt resonator, J] ... -> load, with J = 1/sqrt(qe) at the ends
    and J = m(i,i+1) inside.  A resonator at normalised frequency omega is the
    shunt susceptance j*omega (plus 1/Qu of conductance when loss is asked for).
    """
    qe1 = g[0]
    qen = g[n - 1] * g[n]
    js = [math.sqrt(1.0 / qe1)]
    for i in range(n - 1):
        js.append(1.0 / math.sqrt(g[i] * g[i + 1]))
    js.append(math.sqrt(1.0 / qen))
    a = [[1.0, 0.0], [0.0, 1.0]]
    for i in range(n + 1):
        a = mul(a, abcd_inverter(js[i]))
        if i < n:
            y = 1j * omega
            if qu is not None:
                y = y + 1.0 / qu
            a = mul(a, abcd_shunt(y))
    # S21 from ABCD, both ports Z0 = 1
    den = a[0][0] + a[0][1] / Z0 + a[1][0] * Z0 + a[1][1]
    return 2.0 / den


def il_db(x):
    return -20.0 * math.log10(abs(x))


class TestAgainstAbcdCascade(unittest.TestCase):

    def test_inline_s21_matches_the_abcd_cascade(self):
        """Two independent routes to the same S21: nodal solve vs ABCD cascade."""
        worst = 0.0
        for n in (1, 2, 3, 4, 5):
            for resp, ripple in (("chebyshev", 0.5), ("butterworth", None)):
                r = FM.coupled_matrix_filter(n, 1e9, 0.08, ripple_db=ripple,
                                             response=resp, points=41)
                self.assertEqual(r["status"], "ok", r.get("message"))
                g = FLT._prototype(n, resp, 0.0 if ripple is None else ripple)
                # build the matrix here, unrounded: the answer rounds m to 6 decimals
                # for display and that would mask a 1e-9 comparison
                mat, qe1, qen = FM._inline_matrix(g, n)
                for om in (-4.0, -2.0, -0.7, 0.0, 0.7, 2.0, 4.0):
                    want = il_db(cascade_s21_inline(g, n, om))
                    got = il_db(FM._nodal_s(n, mat, qe1, qen, None, om)[1])
                    worst = max(worst, abs(got - want))
        self.assertLess(worst, 1e-9, "nodal solve and ABCD cascade disagree by %.3g dB" % worst)

    def test_lossy_inline_s21_matches_the_abcd_cascade(self):
        """Same, with the shunt 1/Qu conductance on every resonator."""
        n, bw, qu_phys = 3, 0.05, 300.0
        r = FM.coupled_matrix_filter(n, 2.4e9, bw, ripple_db=0.5, q_unloaded=qu_phys)
        self.assertEqual(r["status"], "ok", r.get("message"))
        g = FLT._prototype(n, "chebyshev", 0.5)
        qu_norm = qu_phys * bw
        mat, qe1, qen = FM._inline_matrix(g, n)
        worst = 0.0
        for om in (-2.0, -0.4, 0.0, 0.4, 2.0):
            want = il_db(cascade_s21_inline(g, n, om, qu=qu_norm))
            got = il_db(FM._nodal_s(n, mat, qe1, qen, [qu_norm] * n, om)[1])
            worst = max(worst, abs(got - want))
        self.assertLess(worst, 1e-9)


class TestAgainstThePrototypePolynomial(unittest.TestCase):

    def test_butterworth_is_exact(self):
        """Butterworth g values are closed form, so the network must match the
        polynomial - to the 1e-4 dB the answer prints its sweep at, and to 1e-6 dB
        in the module's own self check (which compares unrounded numbers)."""
        worst = 0.0
        for n in (1, 2, 3, 4, 5, 6):
            r = FM.coupled_matrix_filter(n, 1e9, 0.1, response="butterworth", points=61)
            self.assertEqual(r["status"], "ok", r.get("message"))
            self.assertLess(r["value"]["self_check"]["closed_form_max_deviation_db"], 1e-6)
            for row in r["value"]["response"]:
                om = FM._omega(row["f_hz"], 1e9, 0.1)
                want = 10.0 * math.log10(1.0 + om ** (2 * n))
                worst = max(worst, abs(row["s21_db"] - want))
        self.assertLess(worst, 2e-4, "reported sweep is rounded to 1e-4 dB")

    def test_chebyshev_matches_the_polynomial_to_a_tenth_of_a_millidb(self):
        """Chebyshev: agrees with filters._epsilon's polynomial to << the 0.02 dB
        filters.py allows itself between a ladder and the prototype.  The residual
        is filters.py's log(1/tanh(R/17.37)) recursion, ~5e-5 in epsilon."""
        worst = 0.0
        for n in (1, 2, 3, 4, 5, 6):
            for ripple in (0.1, 0.5, 1.0):
                r = FM.coupled_matrix_filter(n, 1e9, 0.08, ripple_db=ripple, points=61)
                self.assertEqual(r["status"], "ok", r.get("message"))
                for row in r["value"]["response"]:
                    om = FM._omega(row["f_hz"], 1e9, 0.08)
                    want = FLT._prototype_il_db(n, "chebyshev", ripple, om)
                    worst = max(worst, abs(row["s21_db"] - want))
        self.assertLess(worst, 1e-3, "worst %.3g dB" % worst)

    def test_inband_ripple_equals_the_requested_ripple_for_an_inline_filter(self):
        for n in (1, 2, 3, 4, 5):
            for ripple in (0.1, 0.5):
                r = FM.coupled_matrix_filter(n, 2.4e9, 0.06, ripple_db=ripple)
                self.assertEqual(r["status"], "ok", r.get("message"))
                got = r["value"]["inband"]["ripple_actual_db"]
                self.assertAlmostEqual(got, ripple, delta=1e-3,
                                       msg="n=%d ripple=%s gave %s" % (n, ripple, got))

    def test_unitarity_over_the_whole_sweep(self):
        for n in (1, 2, 3, 4, 5, 6):
            r = FM.coupled_matrix_filter(n, 1e9, 0.1, ripple_db=0.5, points=61)
            self.assertEqual(r["status"], "ok", r.get("message"))
            worst = 0.0
            for row in r["value"]["response"]:
                s11 = 10.0 ** (-row["s11_db"] / 20.0)
                s21 = 10.0 ** (-row["s21_db"] / 20.0)
                worst = max(worst, abs(s11 * s11 + s21 * s21 - 1.0))
            self.assertLess(worst, 1e-3, "rounding in the reported dB costs this much")


class TestTransmissionZeros(unittest.TestCase):

    def test_zero_lands_on_the_cofactor_formula(self):
        """Omega_z = m12^2/m13, then found numerically in the sweep."""
        for fz, sign in ((2.86e9, 1), (2.34e9, -1)):
            r = FM.coupled_matrix_filter(3, 2.6e9, 0.05, ripple_db=0.1,
                                         zero_hz=fz, span=6.0, points=201)
            self.assertEqual(r["status"], "ok", r.get("message"))
            z = r["value"]["transmission_zeros"]["zeros"][0]
            m = r["value"]["matrix"]["m"]
            # the reported analytic Omega and the 6-decimal matrix in the answer
            # agree, and the numerically found null sits on it
            self.assertAlmostEqual(z["analytic_omega"], m[0][1] * m[1][2] / m[0][2],
                                   delta=1e-5 * max(1.0, abs(z["analytic_omega"])))
            self.assertAlmostEqual(z["measured_omega"], z["analytic_omega"],
                                   delta=1e-4 * max(1.0, abs(z["analytic_omega"])))
            self.assertLess(abs(z["omega_error_pct"]), 1e-3)
            self.assertEqual(sign, 1 if z["analytic_omega"] > 0 else -1)
            self.assertGreater(z["depth_db"], 100.0)

    def test_zero_sign_puts_the_zero_on_the_right_side_of_the_band(self):
        above = FM.coupled_matrix_filter(3, 2.6e9, 0.05, ripple_db=0.1,
                                         zero_hz=2.86e9, span=6.0)
        below = FM.coupled_matrix_filter(3, 2.6e9, 0.05, ripple_db=0.1,
                                         zero_hz=2.34e9, span=6.0)
        za = above["value"]["transmission_zeros"]["zeros"][0]
        zb = below["value"]["transmission_zeros"]["zeros"][0]
        self.assertGreater(za["measured_hz"], 2.6e9)
        self.assertLess(zb["measured_hz"], 2.6e9)
        # the sign of m13 is what moves the zero; |Omega_z| is NOT symmetric in f
        self.assertGreater(za["analytic_omega"], 0.0)
        self.assertLess(zb["analytic_omega"], 0.0)

    def test_inline_has_no_finite_zero_and_says_why(self):
        r = FM.coupled_matrix_filter(4, 1e9, 0.1, ripple_db=0.5)
        self.assertEqual(r["status"], "ok")
        tz = r["value"]["transmission_zeros"]
        self.assertEqual(tz["zeros"], [])
        self.assertIn("no path that cancels", tz["why_none"])

    def test_a_null_inside_the_passband_is_flagged_as_a_notch(self):
        """m12^2/m13 lands at |Omega| < 1: a notch cut out of the band, not a
        stopband zero, and the answer must not present it as a filter feature."""
        r = FM.coupled_matrix_filter(3, 2.6e9, 0.05, ripple_db=0.1,
                                     couplings=[[1, 3, 0.98]], span=3.0)
        self.assertEqual(r["status"], "ok", r.get("message"))
        z = r["value"]["transmission_zeros"]["zeros"][0]
        self.assertTrue(z["inside_passband"])
        self.assertIn("INSIDE the passband", z["note"])

    def test_a_null_outside_the_span_is_reported_as_not_found(self):
        r = FM.coupled_matrix_filter(3, 2.6e9, 0.05, ripple_db=0.1,
                                     couplings=[[1, 3, 0.28]], span=3.0)
        self.assertEqual(r["status"], "ok", r.get("message"))
        tz = r["value"]["transmission_zeros"]
        self.assertEqual(tz["zeros"], [])
        self.assertIn("raise span", tz["why_none"])


class TestInbandSampling(unittest.TestCase):

    def test_inband_numbers_do_not_follow_the_display_sweep(self):
        """points=41 puts about two samples inside the band; points=401 puts forty.
        If the ripple were read off the display sweep these would differ."""
        a = FM.coupled_matrix_filter(3, 2.6e9, 0.05, ripple_db=0.1, points=41)
        b = FM.coupled_matrix_filter(3, 2.6e9, 0.05, ripple_db=0.1, points=401)
        self.assertEqual(a["status"], "ok")
        self.assertEqual(b["status"], "ok")
        self.assertEqual(a["value"]["inband"]["sampled_points_in_band"], FM.IN_BAND_POINTS)
        self.assertAlmostEqual(a["value"]["inband"]["ripple_actual_db"],
                               b["value"]["inband"]["ripple_actual_db"], delta=1e-9)

    def test_a_perturbed_matrix_reports_the_ripple_it_actually_delivers(self):
        """The trisection is a PERTURBATION of the inline matrix: the requested
        0.1 dB comes back as something else, and that something is stated."""
        r = FM.coupled_matrix_filter(3, 2.6e9, 0.05, ripple_db=0.1,
                                     zero_hz=2.86e9, span=6.0)
        self.assertEqual(r["status"], "ok")
        rp = r["value"]["inband"]["ripple_actual_db"]
        self.assertGreater(rp, 0.1)
        self.assertIn("PERTURBED", r["value"]["inband"]["ripple_note"])
        self.assertEqual(r["value"]["inband"]["ripple_requested_db"], 0.1)

    def test_the_band_edges_are_the_Omega_equals_one_frequencies(self):
        f0, bw = 2.4e9, 0.08
        r = FM.coupled_matrix_filter(3, f0, bw, ripple_db=0.5)
        lo, hi = r["value"]["inband"]["band_hz"]
        self.assertAlmostEqual(FM._omega(lo, f0, bw), -1.0, delta=1e-9)
        self.assertAlmostEqual(FM._omega(hi, f0, bw), 1.0, delta=1e-9)


class TestFiniteUnloadedQ(unittest.TestCase):

    def test_midband_loss_matches_matthaei(self):
        for n, qu in ((3, 200.0), (5, 500.0), (3, 400.0)):
            r = FM.coupled_matrix_filter(n, 2.4e9, 0.1, ripple_db=0.5, q_unloaded=qu)
            self.assertEqual(r["status"], "ok", r.get("message"))
            loss = r["value"]["loss"]
            self.assertLess(abs(loss["matthaei_deviation_pct"]), 5.0,
                            "n=%d Qu=%s: %s %%" % (n, qu, loss["matthaei_deviation_pct"]))

    def test_loss_rises_as_qu_falls(self):
        prev = -1.0
        for qu in (2000.0, 500.0, 200.0, 80.0):
            r = FM.coupled_matrix_filter(3, 2.4e9, 0.1, ripple_db=0.5, q_unloaded=qu)
            il = r["value"]["loss"]["insertion_loss_center_db"]
            self.assertGreater(il, prev, "Qu=%s did not raise the loss" % qu)
            prev = il

    def test_unloaded_q_is_normalised_by_fbw(self):
        """Halving FBW must roughly double the dB of dissipation loss: Qu enters the
        normalised network as Qu*FBW, the same scaling the external Q gets.  A raw
        Qu would make the two bandwidths give the SAME loss."""
        a = FM.coupled_matrix_filter(3, 2.4e9, 0.1, ripple_db=0.5, q_unloaded=300.0)
        b = FM.coupled_matrix_filter(3, 2.4e9, 0.05, ripple_db=0.5, q_unloaded=300.0)
        ia = a["value"]["loss"]["insertion_loss_center_db"]
        ib = b["value"]["loss"]["insertion_loss_center_db"]
        self.assertAlmostEqual(ib / ia, 2.0, delta=0.2,
                               msg="FBW halved but loss went %.4g -> %.4g dB" % (ia, ib))

    def test_the_null_fills_in_with_loss(self):
        deep = FM.coupled_matrix_filter(3, 2.6e9, 0.05, ripple_db=0.1,
                                        zero_hz=2.86e9, span=6.0)
        shallow = FM.coupled_matrix_filter(3, 2.6e9, 0.05, ripple_db=0.1,
                                           zero_hz=2.86e9, span=6.0, q_unloaded=150.0)
        d0 = deep["value"]["transmission_zeros"]["zeros"][0]["depth_db"]
        d1 = shallow["value"]["transmission_zeros"]["zeros"][0]["depth_db"]
        self.assertGreater(d0, d1 + 20.0)
        self.assertIn("unloaded Q", shallow["value"]["transmission_zeros"]["zeros"][0]
                      ["depth_limited_by"])

    def test_lossy_answers_do_not_claim_unitarity(self):
        r = FM.coupled_matrix_filter(3, 2.4e9, 0.1, ripple_db=0.5, q_unloaded=200.0)
        sc = r["value"]["self_check"]
        self.assertIsNone(sc["unitarity_max_error"])
        self.assertIn("absorb power", sc["unitarity_note"])

    def test_lossy_answers_skip_the_closed_form_check(self):
        """The prototype polynomial has no loss, so a lossy network must not be
        scored against it - and the answer says the check was not run."""
        r = FM.coupled_matrix_filter(3, 2.4e9, 0.1, ripple_db=0.5, q_unloaded=200.0)
        sc = r["value"]["self_check"]
        self.assertFalse(sc["closed_form_compared"])
        self.assertIsNone(sc["closed_form_max_deviation_db"])


class TestCustomMatrixAndProbe(unittest.TestCase):

    def test_custom_couplings_change_the_topology_and_the_matrix(self):
        r = FM.coupled_matrix_filter(4, 2.4e9, 0.06, ripple_db=0.5,
                                     couplings=[[1, 4, 0.12]], span=4.0)
        self.assertEqual(r["status"], "ok", r.get("message"))
        v = r["value"]
        self.assertEqual(v["topology"], "custom")
        self.assertAlmostEqual(abs(v["matrix"]["m"][0][3]), 0.12, delta=1e-6)

    def test_an_explicitly_supplied_q_external_is_used_as_given(self):
        """q_external is an ABSOLUTE Qe: the network stores Qe*FBW."""
        r = FM.coupled_matrix_filter(3, 2.4e9, 0.06, ripple_db=0.5,
                                     q_external=[20.0, 20.0])
        self.assertEqual(r["status"], "ok", r.get("message"))
        self.assertAlmostEqual(r["value"]["matrix"]["q_external_in_absolute"], 20.0,
                               delta=1e-3)
        self.assertAlmostEqual(r["value"]["matrix"]["q_external_in"], 20.0 * 0.06,
                               delta=1e-6)
        # an external Q the prototype did not ask for is not scored against the
        # prototype polynomial - and the answer says the check was skipped
        self.assertFalse(r["value"]["self_check"]["closed_form_compared"])

    def test_probe_returns_the_two_s_parameters_at_the_frequency_asked(self):
        r = FM.coupled_matrix_filter(3, 2.4e9, 0.06, ripple_db=0.5, probe_hz=2.4e9)
        self.assertEqual(r["status"], "ok")
        p = r["value"]["probe"]
        self.assertEqual(p["f_hz"], 2.4e9)
        self.assertAlmostEqual(p["omega"], 0.0, delta=1e-9)
        self.assertAlmostEqual(p["s21_db"], 0.0, delta=1e-3)

    def test_probe_outside_the_swept_band_says_the_mapping_is_the_limit(self):
        r = FM.coupled_matrix_filter(3, 2.4e9, 0.06, ripple_db=0.5,
                                     probe_hz=5.0e9, span=3.0)
        self.assertEqual(r["status"], "ok")
        self.assertIn("narrow-band mapping", r["value"]["probe"]["note"])

    def test_the_absolute_external_q_is_reported_next_to_the_normalised_one(self):
        bw = 0.04
        r = FM.coupled_matrix_filter(3, 2.4e9, bw, ripple_db=0.5)
        m = r["value"]["matrix"]
        self.assertAlmostEqual(m["q_external_in_absolute"],
                               m["q_external_in"] / bw, delta=1e-3)


class TestRefusals(unittest.TestCase):
    """Each of these is a user error the tool must name, not absorb."""

    def _code(self, **kw):
        args = {"order": 3, "f0_hz": 2.4e9, "fbw": 0.05}
        args.update(kw)
        r = FM.coupled_matrix_filter(args.pop("order"), args.pop("f0_hz"),
                                     args.pop("fbw"), **args)
        self.assertEqual(r["status"], "error", "expected a refusal, got %s" % (r.get("value"),))
        return r

    def test_order_out_of_range(self):
        self.assertEqual(self._code(order=0)["reason_code"], "ARG_RANGE")
        self.assertEqual(self._code(order=9)["reason_code"], "ARG_RANGE")

    def test_bandwidth_out_of_range(self):
        self.assertEqual(self._code(fbw=0.0)["reason_code"], "ARG_RANGE")
        self.assertEqual(self._code(fbw=1.5)["reason_code"], "ARG_RANGE")

    def test_missing_or_non_numeric_arguments(self):
        self.assertEqual(self._code(f0_hz="x")["reason_code"], "ARG_INVALID")

    def test_zero_hz_and_couplings_together_is_a_conflict(self):
        r = self._code(zero_hz=2.6e9, couplings=[[1, 3, 0.2]])
        self.assertEqual(r["reason_code"], "ARG_CONFLICT")

    def test_a_zero_asked_for_on_a_non_third_order_filter(self):
        self.assertEqual(self._code(order=4, zero_hz=2.6e9)["reason_code"], "ARG_CONFLICT")

    def test_a_zero_inside_the_band_that_is_asked_for_explicitly(self):
        r = self._code(zero_hz=2.45e9)
        self.assertEqual(r["reason_code"], "ARG_RANGE")
        self.assertIn("Omega", r["message"])

    def test_couplings_naming_the_same_or_a_nonexistent_resonator(self):
        self.assertEqual(self._code(couplings=[[1, 1, 0.2]])["reason_code"], "ARG_RANGE")
        self.assertEqual(self._code(couplings=[[1, 4, 0.2]])["reason_code"], "ARG_RANGE")

    def test_a_coupling_that_is_not_a_coupling(self):
        self.assertEqual(self._code(couplings=[[1, 3, 3.0]])["reason_code"], "ARG_RANGE")
        self.assertEqual(self._code(couplings=[[1, 3]])["reason_code"], "ARG_INVALID")

    def test_bad_q_values(self):
        self.assertEqual(self._code(q_unloaded=0.0)["reason_code"], "ARG_RANGE")
        self.assertEqual(self._code(q_external=[1.0])["reason_code"], "ARG_INVALID")
        self.assertEqual(self._code(q_unloaded=[100.0, 100.0])["reason_code"], "ARG_INVALID")

    def test_bad_sweep_arguments(self):
        self.assertEqual(self._code(points=10)["reason_code"], "ARG_RANGE")
        self.assertEqual(self._code(span=1.0)["reason_code"], "ARG_RANGE")
        self.assertEqual(self._code(probe_hz=-1.0)["reason_code"], "ARG_RANGE")

    def test_butterworth_with_a_ripple_is_a_conflict(self):
        r = self._code(response="butterworth", ripple_db=0.5)
        self.assertEqual(r["reason_code"], "ARG_CONFLICT")


class TestHonesty(unittest.TestCase):

    def test_not_computed_names_the_missing_synthesis_and_the_missing_layout(self):
        r = FM.coupled_matrix_filter(3, 2.6e9, 0.05, ripple_db=0.1, zero_hz=2.86e9)
        text = " ".join(r["not_computed"]).lower()
        self.assertIn("cameron", text)
        self.assertIn("layout", text)

    def test_the_answer_does_not_call_the_perturbed_ripple_the_requested_one(self):
        r = FM.coupled_matrix_filter(3, 2.6e9, 0.05, ripple_db=0.1, zero_hz=2.86e9)
        self.assertIn("perturb", r["precision"].lower())

    def test_the_tool_is_registered_and_round_trips(self):
        self.assertIn("filter_coupling_matrix", tools.TOOLS)
        r = tools.call_tool("filter_coupling_matrix",
                            {"order": 3, "f0_hz": 2.6e9, "fbw": 0.05,
                             "ripple_db": 0.1, "zero_hz": 2.86e9})
        self.assertEqual(r["status"], "ok", r.get("message"))
        self.assertEqual(r["value"]["topology"], "trisection")


if __name__ == "__main__":
    unittest.main()
