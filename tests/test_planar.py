"""The two things a plane pair does that `cavity.py` refuses to model, plus the
Huray surface (`roughness_huray`, `plane_radiation_q`, `plane_open_end`).

Every anchor here is arithmetic that lives OUTSIDE the module:

  * Huray: the transition function at the knee is exactly 1/sqrt(5), the two
    limits are RF and 1, and K is linear in (RF-1) - so sr gets multiplied by
    that one number. If someone replaces F with a fitted curve, `x=1` moves and
    the reported knee frequency stops being the place where delta = r/2.
  * Radiation Q: the two shapes have closed forms for a square plane -
    er*a/(6h) for (1,0) and er*a/(8h) for (1,1) - so the code is checked against
    a hand integral of cos^2 over the same geometry, not against itself. The
    identities that must hold exactly (Q proportional to 1/h, to er, invariant
    under uniform scaling, and Q_total = 1/sum(1/Q_edge)) each kill a different
    way of getting the bookkeeping wrong.
  * Open end: the extension is tied to the CITED capacitance law by requiring
    eps0*(a/h + 2*delta_l/h) == eps0*(l/h + (1/pi)(ln(2*pi*l/h)+1)) with
    l/h = a/h. That is what proves the factor of 2 in "one edge gets half of the
    two-edge correction". The second check is analytic too: for a 1-D mode the
    exact and first-order shifts must differ by exactly -eps/(1+eps), and the
    ratio between "two edges open" and "one edge open" is 2*(1+eps)/(1+2*eps).

Negative controls, one per mechanism:
  * sr = 0 must give K == 1.0 exactly (a smooth surface cannot gain loss);
  * at 1 Hz, and again at 1e12 Hz, K must stay strictly inside (1, RF): the tool
    may never report the asymptotic value as if it had been reached;
  * 1e20 Hz must be REFUSED, not clamped (the snowball model is quasi-static);
  * radiation Q must go UP as h shrinks, i.e. the sign of the effect is right;
  * opening only the y edges of a (1,0) mode must shift the frequency by
    EXACTLY zero, because the mode does not see that dimension - a blanket
    "stretch everything" implementation would fail here;
  * a fully walled rectangle must refuse rather than report an infinite Q.

Comparison convention: every number in the envelope is rounded to six
significant digits on purpose (the rest of calc does the same), so numeric
checks here are RELATIVE, at 1e-5 or looser - never `places=` on a rounded
value, which is how the first draft of this file managed to fail twenty times
on the last digit.
"""
import math
import unittest

from pcb_agent_mcp.calc import cavity as CV
from pcb_agent_mcp.calc import planar as P
from pcb_agent_mcp.calc import roughness as R
from pcb_agent_mcp.calc.result import ok as _ok

MU0 = 4e-7 * math.pi
C0 = 299792458.0
ETA0 = MU0 * C0
EPS0 = 1.0 / (MU0 * C0 * C0)
SIGMA = 5.8e7


def _delta(f_hz, sigma=SIGMA):
    return math.sqrt(2.0 / (2.0 * math.pi * f_hz * MU0 * sigma))


def _v(env):
    assert env["status"] == "ok", env
    return env["value"]


def _close(tc, got, want, rel=1e-5, msg=""):
    """Relative comparison, because the envelope rounds to six digits."""
    if want == 0.0:
        tc.assertEqual(got, 0.0, msg)
        return
    tc.assertLess(abs(got - want) / abs(want), rel,
                  "%s: got %r want %r (rel %.2e)" % (msg, got, want,
                                                     (got - want) / want))


class TestHurayMath(unittest.TestCase):
    """The three functions in roughness.py, against arithmetic."""

    def test_surface_ratio_is_the_hemisphere_area(self):
        r, n = 0.5e-6, 4.0e10
        self.assertAlmostEqual(R.huray_surface_ratio(r, n),
                               4.0 * math.pi * r * r * n, places=20)

    def test_surface_ratio_round_trip_one(self):
        r = 0.3e-6
        n = 1.0 / (4.0 * math.pi * r * r)          # by construction sr = 1
        self.assertAlmostEqual(R.huray_surface_ratio(r, n), 1.0, places=12)

    def test_surface_ratio_flat_surface_is_zero(self):
        self.assertEqual(R.huray_surface_ratio(0.5e-6, 0.0), 0.0)

    def test_surface_ratio_rejects_nonsense(self):
        self.assertIsNone(R.huray_surface_ratio(0.0, 1e10))
        self.assertIsNone(R.huray_surface_ratio(-1e-6, 1e10))
        self.assertIsNone(R.huray_surface_ratio(0.5e-6, -1.0))
        self.assertIsNone(R.huray_surface_ratio(None, 1e10))

    def test_transition_knee_is_exactly_one_over_sqrt5(self):
        """delta = r/2 => x = 1 => F = 1/sqrt(5). Not a table value: algebra."""
        r = 0.5e-6
        self.assertAlmostEqual(R.huray_transition(r, r / 2.0),
                               1.0 / math.sqrt(5.0), places=15)

    def test_transition_limits(self):
        # x -> 0 (delta << r): F -> 1
        self.assertAlmostEqual(R.huray_transition(1e-3, 1e-12), 1.0, places=6)
        # x -> inf: F -> 0 like 1/(x*sqrt2)
        r, d = 1e-9, 1e-3
        x = 2.0 * d / r
        self.assertAlmostEqual(R.huray_transition(r, d) * x * math.sqrt(2.0),
                               1.0, places=6)

    def test_transition_rejects_bad_arguments(self):
        self.assertIsNone(R.huray_transition(0.0, 1e-6))
        self.assertIsNone(R.huray_transition(0.5e-6, 0.0))

    def test_loss_factor_is_linear_in_rf_minus_one(self):
        delta, r = _delta(1e9), 0.5e-6
        f = R.huray_transition(r, delta)
        k1 = R.huray_loss_factor(delta, r, 0.0)     # RF = 1
        k2 = R.huray_loss_factor(delta, r, 1.0)     # RF = 2.5
        k4 = R.huray_loss_factor(delta, r, 2.0)     # RF = 4.0
        self.assertAlmostEqual(k1, 1.0, places=15)
        self.assertAlmostEqual(k2, 1.0 + 1.5 * 1.0 * f, places=15)
        self.assertAlmostEqual(k4 - k1, 2.0 * (k2 - k1), places=15)

    def test_loss_factor_flat_surface(self):
        """Negative control: zero nodules must not add any loss."""
        self.assertEqual(R.huray_loss_factor(_delta(1e9), 0.5e-6, 0.0), 1.0)


class TestHurayTool(unittest.TestCase):
    """The envelope: numbers, limits, refusals."""

    def test_one_ghz_low_roughness_anchor(self):
        v = _v(P.huray_roughness(1e9, nodule_radius_um=0.5, surface_ratio=1.0))
        _close(self, v["skin_depth_um"], 2.08981)
        self.assertEqual(v["surface_ratio_dc"], 2.5)
        self.assertEqual(v["roughness_factor_RF"], 2.5)
        _close(self, v["transition_F"], 0.079689)
        _close(self, v["loss_factor_K"], 1.11953)
        _close(self, v["excess_loss_pct"], 11.9534)

    def test_ten_ghz_high_roughness_anchor(self):
        v = _v(P.huray_roughness(1e10, nodule_radius_um=0.5, surface_ratio=6.0))
        self.assertEqual(v["surface_ratio_dc"], 10.0)
        _close(self, v["loss_factor_K"], 2.99940)
        self.assertGreater(v["loss_factor_K"], 2.0)

    def test_k_rises_with_frequency_monotonically(self):
        ks = [_v(P.huray_roughness(f, nodule_radius_um=0.5, surface_ratio=3.0))
              ["loss_factor_K"] for f in (1e8, 1e9, 1e10, 1e11, 1e12)]
        for lo, hi in zip(ks, ks[1:]):
            self.assertGreater(hi, lo)

    def test_k_never_reaches_the_asymptote(self):
        """Negative control: K must stay strictly inside (1, RF) at every
        frequency the model accepts - the asymptote is a limit, not an answer."""
        rf = 1.0 + 1.5 * 3.0
        for f in (1e3, 1e9, 1e11, 1e12):
            v = _v(P.huray_roughness(f, nodule_radius_um=0.5, surface_ratio=3.0))
            self.assertGreater(v["loss_factor_K"], 1.0, f)
            self.assertLess(v["loss_factor_K"], rf, f)

    def test_low_frequency_limit_is_one(self):
        """Negative control: at 1 Hz nothing resolves a nodule, so K-1 is
        vanishingly small (the exact value is 1.2e-5 above one)."""
        v = _v(P.huray_roughness(1.0, nodule_radius_um=0.5, surface_ratio=6.0))
        self.assertLess(v["loss_factor_K"] - 1.0, 1e-3)
        self.assertLess(v["transition_x"], 1e7)

    def test_optical_frequency_is_refused_not_clamped(self):
        """The snowball model is quasi-static: at 1e20 Hz the nodule is bigger
        than a wavelength. Refusing is the honest answer."""
        env = P.huray_roughness(1e20, nodule_radius_um=0.5, surface_ratio=3.0)
        self.assertEqual(env["status"], "error")
        self.assertEqual(env["reason_code"], "MODEL_SCOPE")

    def test_knee_frequency_is_where_x_is_one(self):
        v = _v(P.huray_roughness(1e9, nodule_radius_um=0.5, surface_ratio=1.0))
        fk = v["knee_frequency_hz"]
        _close(self, _delta(fk) / 0.5e-6, 0.5, msg="knee sits at delta = r/2")
        vk = _v(P.huray_roughness(fk, nodule_radius_um=0.5, surface_ratio=1.0))
        _close(self, vk["transition_x"], 1.0)
        _close(self, vk["transition_F"], 1.0 / math.sqrt(5.0))
        _close(self, vk["loss_factor_K"], 1.0 + 1.5 / math.sqrt(5.0))

    def test_surface_resistance_is_multiplied_not_added(self):
        v = _v(P.huray_roughness(1e9, nodule_radius_um=0.5, surface_ratio=3.0))
        _close(self, v["surface_resistance_ohm_sq"],
               v["surface_resistance_ohm_sq_smooth"] * v["loss_factor_K"],
               rel=1e-4)

    def test_density_and_ratio_describe_the_same_carpet(self):
        r = 0.4
        sr = 1.5
        n_per_mm2 = sr / (4.0 * math.pi * (r * 1e-6) ** 2) * 1e-6
        a = _v(P.huray_roughness(1e9, nodule_radius_um=r, surface_ratio=sr))
        b = _v(P.huray_roughness(1e9, nodule_radius_um=r,
                                 nodule_density_per_mm2=n_per_mm2))
        _close(self, a["surface_ratio"], b["surface_ratio"])
        _close(self, a["loss_factor_K"], b["loss_factor_K"])

    def test_hammerstad_comparison_is_side_by_side_only(self):
        v = _v(P.huray_roughness(1e9, nodule_radius_um=0.5, surface_ratio=1.0,
                                 rq_um=0.5))
        h = v["hammerstad_comparison"]
        _close(self, h["hammerstad_loss_factor"],
               R.roughness_factor(0.5e-6, _delta(1e9)))
        self.assertEqual(h["huray_loss_factor"], v["loss_factor_K"])
        self.assertIn("no Rq <-> (r, sr) conversion", h["model"])
        # and without rq_um the key is simply absent
        v2 = _v(P.huray_roughness(1e9, nodule_radius_um=0.5, surface_ratio=1.0))
        self.assertNotIn("hammerstad_comparison", v2)

    def test_flat_surface_gives_exactly_one(self):
        """Negative control: sr = 0 must move nothing at all."""
        v = _v(P.huray_roughness(1e9, nodule_radius_um=0.5, surface_ratio=0.0))
        self.assertEqual(v["loss_factor_K"], 1.0)
        self.assertEqual(v["excess_loss_pct"], 0.0)
        self.assertEqual(v["surface_resistance_ohm_sq"],
                         v["surface_resistance_ohm_sq_smooth"])

    def test_envelope_carries_the_source(self):
        env = P.huray_roughness(1e9, nodule_radius_um=0.5, surface_ratio=1.0)
        self.assertIn("EPEPS 2017", env["source"])
        self.assertEqual(env["unit"], "dimensionless Rs multiplier")
        self.assertTrue(env["basis"])

    def test_refusals(self):
        cases = [
            (dict(f_hz=0.0, nodule_radius_um=0.5, surface_ratio=1.0), "ARG_RANGE"),
            (dict(f_hz=-1e9, nodule_radius_um=0.5, surface_ratio=1.0), "ARG_RANGE"),
            (dict(f_hz=1e9, nodule_radius_um=0.0, surface_ratio=1.0), "ARG_RANGE"),
            (dict(f_hz=1e9, nodule_radius_um=0.5, surface_ratio=-1.0), "ARG_RANGE"),
            (dict(f_hz=1e9, nodule_radius_um=0.5, surface_ratio=40.0), "ARG_RANGE"),
            (dict(f_hz=1e9, nodule_radius_um=0.5), "ARG_MISSING"),
            (dict(f_hz=1e9, nodule_radius_um=0.5, surface_ratio=1.0,
                  nodule_density_per_mm2=1e5), "ARG_INVALID"),
            (dict(f_hz=1e9, surface_ratio=1.0), "ARG_INVALID"),
            (dict(f_hz=1e9, nodule_radius_um=1e6, surface_ratio=1.0),
             "MODEL_SCOPE"),
            (dict(f_hz=1e9, nodule_radius_um=0.5, surface_ratio=1.0,
                  conductivity_s_m=0.0), "ARG_INVALID"),
            (dict(f_hz=1e9, nodule_radius_um=0.5, surface_ratio=1.0,
                  mur=0.0), "ARG_INVALID"),
            (dict(f_hz=1e9, nodule_radius_um=0.5, surface_ratio=1.0,
                  rq_um=-1.0), "ARG_RANGE"),
        ]
        for kwargs, code in cases:
            env = P.huray_roughness(**kwargs)
            self.assertEqual(env["status"], "error", kwargs)
            self.assertEqual(env["reason_code"], code, kwargs)


class TestPlaneRadiationQ(unittest.TestCase):
    """Squares have closed forms; everything else is built out of them."""

    def test_mode_10_square_is_er_a_over_6h(self):
        a, h, er = 50.0, 0.2, 4.3
        v = _v(P.plane_radiation_q(a, a, h, er))
        _close(self, v["q_radiation"], er * a / (6.0 * h))

    def test_mode_11_square_is_er_a_over_8h(self):
        a, h, er = 50.0, 0.2, 4.3
        v = _v(P.plane_radiation_q(a, a, h, er, m=1, n=1))
        _close(self, v["q_radiation"], er * a / (8.0 * h))

    def test_inverse_in_h(self):
        """Negative control on the sign: a thinner gap rings longer."""
        q1 = _v(P.plane_radiation_q(50, 50, 0.2, 4.3))["q_radiation"]
        q2 = _v(P.plane_radiation_q(50, 50, 0.1, 4.3))["q_radiation"]
        _close(self, q2 / q1, 2.0)
        self.assertGreater(q2, q1)

    def test_proportional_to_er(self):
        q1 = _v(P.plane_radiation_q(50, 50, 0.2, 4.3))["q_radiation"]
        q2 = _v(P.plane_radiation_q(50, 50, 0.2, 8.6))["q_radiation"]
        _close(self, q2 / q1, 2.0)

    def test_invariant_under_uniform_scaling(self):
        """Q_rad has no frequency in it, so scaling geometry by 2 changes nothing
        - and the frequency, which does have one, halves."""
        q1 = _v(P.plane_radiation_q(50, 50, 0.2, 4.3))
        v2 = _v(P.plane_radiation_q(100, 100, 0.4, 4.3))
        _close(self, v2["q_radiation"], q1["q_radiation"])
        _close(self, v2["f_hz"], q1["f_hz"] / 2.0)

    def test_per_edge_reciprocal_identity(self):
        v = _v(P.plane_radiation_q(50, 30, 0.2, 4.3, m=1, n=1))
        inv = sum(1.0 / e["q_rad_alone"] for e in v["per_edge"].values())
        _close(self, 1.0 / inv, v["q_radiation"], rel=1e-4)

    def test_edge_integrals_match_the_mode_shape(self):
        a, b = 50.0, 30.0
        v = _v(P.plane_radiation_q(a, b, 0.2, 4.3, m=1, n=1))
        _close(self, v["area_integral_m2"], a / 1000 * b / 1000 / 4.0)
        _close(self, v["edge_integral_m"], 2 * (b + a) / 1000 / 2.0)
        v0 = _v(P.plane_radiation_q(a, b, 0.2, 4.3, m=1, n=0))
        _close(self, v0["edge_integral_m"],
               2 * b / 1000 + 2 * (a / 1000) / 2.0)

    def test_closed_edges_shrink_the_radiated_power(self):
        v = _v(P.plane_radiation_q(50, 50, 0.2, 4.3, open_edges=["x0"]))
        self.assertEqual(v["open_edges"], ["x0"])
        _close(self, v["edge_integral_m"], 0.05)
        q_all = _v(P.plane_radiation_q(50, 50, 0.2, 4.3))["q_radiation"]
        _close(self, v["q_radiation"], q_all * 3.0)

    def test_radiated_power_scales_with_omega(self):
        p1 = _v(P.plane_radiation_q(50, 50, 0.2, 4.3))["radiated_power_w_at_1v_gap"]
        p2 = _v(P.plane_radiation_q(50, 50, 0.2, 4.3, m=2))["radiated_power_w_at_1v_gap"]
        _close(self, p2 / p1, 2.0)

    def test_frequency_matches_cavity_plane_resonance(self):
        """Two modules, one formula: they must not drift apart."""
        modes = CV.plane_resonance(50, 50, 4.3, 2, 2, 1e9)["value"]
        for entry in modes:
            v = _v(P.plane_radiation_q(50, 50, 0.2, 4.3,
                                       m=entry["m"], n=entry["n"]))
            _close(self, v["f_hz"], entry["f_hz"], rel=1e-4, msg=str(entry))

    def test_q_conductor_matches_cavity_plane_q(self):
        v = _v(P.plane_radiation_q(50, 50, 0.2, 4.3))
        q = CV.plane_q(v["f_hz"], 0.2)["value"]
        _close(self, v["q_conductor"], q["q_conductor"])
        _close(self, v["q_conductor"], 0.2e-3 / _delta(v["f_hz"]))

    def test_q_total_adds_the_three_in_reciprocal_form(self):
        v = _v(P.plane_radiation_q(50, 50, 1.6, 4.3, tan_delta=0.02))
        want = 1.0 / (1.0 / v["q_conductor"] + 1.0 / 50.0 + 1.0 / v["q_radiation"])
        _close(self, v["q_total"], want, rel=1e-4)
        # on 1.6 mm the edge radiates harder than the copper or the FR4 damps
        self.assertEqual(v["which_dominates"], "radiation")
        self.assertLess(v["q_radiation"], min(v["q_conductor"], 50.0))

    def test_k0_h_warning_band(self):
        v = _v(P.plane_radiation_q(50, 50, 10.0, 4.3))
        self.assertGreater(v["k0_h"], 0.1)
        self.assertLess(v["k0_h"], 1.0)
        self.assertIn("warning", v)

    def test_refusals(self):
        cases = [
            ((50, 50, 0.2, 4.3), dict(m=0, n=0), "ARG_RANGE"),
            ((50, 50, 0.2, 4.3), dict(open_edges=[]), "ARG_RANGE"),
            ((50, 50, 0.2, 4.3), dict(open_edges="none"), "ARG_RANGE"),
            ((50, 50, 0.2, 4.3), dict(open_edges=["x9"]), "ARG_INVALID"),
            ((50, 50, 0.2, 4.3), dict(open_edges=["x0", "x0"]), "ARG_INVALID"),
            ((50, 50, 100.0, 4.3), {}, "MODEL_SCOPE"),
            ((0, 50, 0.2, 4.3), {}, "ARG_RANGE"),
            ((50, 50, 0.2, 0.5), {}, "ARG_RANGE"),
            ((50, 50, 0.2, 4.3), dict(m=0.5), "ARG_INVALID"),
            ((50, 50, 0.2, 4.3), dict(tan_delta=0.0), "ARG_RANGE"),
            ((50, 50, 0.2, 4.3), dict(tan_delta=2.0), "ARG_RANGE"),
            ((50, 50, 0.2, 4.3), dict(conductivity_s_m=0.0), "ARG_INVALID"),
        ]
        for args, kwargs, code in cases:
            env = P.plane_radiation_q(*args, **kwargs)
            self.assertEqual(env["status"], "error", (args, kwargs))
            self.assertEqual(env["reason_code"], code, (args, kwargs))

    def test_all_edges_string_is_four_edges(self):
        v = _v(P.plane_radiation_q(50, 50, 0.2, 4.3, open_edges="all"))
        self.assertEqual(sorted(v["open_edges"]), ["x0", "x1", "y0", "y1"])

    def test_comma_string_is_a_list(self):
        """'x0,x1' is the same request as ['x0','x1'] - a caller typing edges
        into a text argument must not silently get four open edges."""
        a = _v(P.plane_radiation_q(50, 50, 0.2, 4.3, open_edges="x0,x1"))
        b = _v(P.plane_radiation_q(50, 50, 0.2, 4.3, open_edges=["x0", "x1"]))
        self.assertEqual(a["open_edges"], b["open_edges"])
        self.assertEqual(a["q_radiation"], b["q_radiation"])

    def test_assumptions_name_what_is_missing(self):
        env = P.plane_radiation_q(50, 50, 0.2, 4.3)
        self.assertIn("outside", env["assumptions"])
        self.assertIn("does_not_prove", env)      # merged from extra
        self.assertIn("soldered", env["assumptions"]["components"])


class TestPlaneOpenEnd(unittest.TestCase):
    """delta_l, and the detuning that follows from it."""

    def test_extension_anchor(self):
        a, h = 50.0, 0.2
        v = _v(P.plane_open_end(a, a, h, 4.3))
        want = h / (2 * math.pi) * (math.log(2 * math.pi * a / h) + 1.0)
        _close(self, v["edge_extension_mm"]["x"], want)
        _close(self, v["edge_extension_mm"]["x"], 0.266086)
        _close(self, v["edge_extension_over_h"]["x"], 1.33043)

    def test_extension_is_the_halved_yariv_correction(self):
        """Tie delta_l to the CITED capacitance law, not to our own algebra.

        Yariv (2021) eq. 5.20: C/eps0 = l/h + (1/pi)*(ln(2*pi*l/h)+1) per unit
        width for a stripe capacitor - ONE correction shared by its TWO ends.
        Expressing the same capacitance as an extra parallel-plate length must
        give exactly 2*delta_l, which is where the factor of 2 comes from.
        """
        a, b, h = 50.0, 30.0, 0.2
        v = _v(P.plane_open_end(a, b, h, 4.3))
        dl = v["edge_extension_mm"]["x"] / 1000.0
        lam = (a / 1000.0) / (h / 1000.0)          # l/h = a/h
        c_yariv = EPS0 * (lam + (1.0 / math.pi) * (math.log(2 * math.pi * lam) + 1))
        c_ours = EPS0 * (a / 1000.0 + 2 * dl) / (h / 1000.0)
        _close(self, c_ours, c_yariv, rel=1e-5)

    def test_detuning_is_negative_and_a_few_percent(self):
        v = _v(P.plane_open_end(50, 50, 0.2, 4.3))
        self.assertLess(v["delta_f_over_f"], 0.0)
        _close(self, v["delta_f_over_f_pct"], -1.05313)
        _close(self, v["f_detuned_hz"], 1.44573e9 * (1 - 0.0105313), rel=1e-5)

    def test_thick_dielectric_detunes_much_more(self):
        thin = _v(P.plane_open_end(50, 50, 0.2, 4.3))["delta_f_over_f_pct"]
        thick = _v(P.plane_open_end(50, 50, 1.6, 4.3))["delta_f_over_f_pct"]
        _close(self, thick, -6.01208)
        self.assertLess(thick, 4.0 * thin)      # not linear in h: there is a log

    def test_the_mode_does_not_see_the_other_direction(self):
        """Negative control: a (1,0) mode must not move when only y edges open.

        A blanket 'stretch both dimensions' implementation passes everything
        else in this class and fails here.
        """
        v = _v(P.plane_open_end(50, 30, 0.2, 4.3, m=1, n=0,
                                open_edges=["y0", "y1"]))
        self.assertEqual(v["delta_f_over_f"], 0.0)
        self.assertEqual(v["delta_a_mm"], 0.0)
        self.assertGreater(v["delta_b_mm"], 0.0)
        self.assertIsNone(v["first_order_check"]["relative_gap"])

    def test_one_open_edge_is_half_of_two(self):
        """The extension halves, and the SHIFT follows the exact nonlinear law:
        eps vs 2*eps gives 2*(1+eps)/(1+2*eps), not 2."""
        one = _v(P.plane_open_end(50, 50, 0.2, 4.3, open_edges=["x0"]))
        two = _v(P.plane_open_end(50, 50, 0.2, 4.3, open_edges=["x0", "x1"]))
        _close(self, two["delta_a_mm"], 2 * one["delta_a_mm"], rel=1e-6)
        eps = one["delta_a_mm"] / 50.0
        predicted = 2.0 * (1.0 + eps) / (1.0 + 2.0 * eps)
        _close(self, two["delta_f_over_f"] / one["delta_f_over_f"], predicted)
        _close(self, one["delta_f_over_f_pct"], -0.529355)

    def test_first_order_gap_is_the_predicted_second_order_term(self):
        """Two paths, and the difference between them is a PREDICTION.

        For a 1-D mode f ~ 1/a, so f_eff/f0 = 1/(1+eps) while the first-order
        form is -eps; hence the relative gap must be exactly -eps/(1+eps).
        """
        v = _v(P.plane_open_end(50, 50, 0.2, 4.3))
        eps = v["delta_a_mm"] / 50.0
        want = -eps / (1.0 + eps)
        _close(self, v["first_order_check"]["relative_gap"], want)
        # and the gap never leaves the bound, which is the part that is
        # geometry-independent
        self.assertLessEqual(abs(v["first_order_check"]["relative_gap"]),
                             abs(v["first_order_check"]["gap_bound"]))

    def test_gap_stays_inside_the_bound_for_a_2d_mode(self):
        v = _v(P.plane_open_end(50, 30, 0.2, 4.3, m=1, n=1))
        chk = v["first_order_check"]
        self.assertLessEqual(abs(chk["relative_gap"]), abs(chk["gap_bound"]))
        self.assertLess(chk["relative_gap"], 0.0)

    def test_scale_invariance_of_delta_over_h(self):
        """The correction is geometric: only a/h can matter."""
        v1 = _v(P.plane_open_end(50, 50, 0.2, 4.3))
        v2 = _v(P.plane_open_end(500, 500, 2.0, 4.3))
        _close(self, v1["edge_extension_over_h"]["x"],
               v2["edge_extension_over_h"]["x"], rel=1e-6)

    def test_relative_shift_falls_as_the_plane_grows(self):
        near = _v(P.plane_open_end(30, 30, 0.5, 4.3))["delta_f_over_f_pct"]
        far = _v(P.plane_open_end(300, 300, 0.5, 4.3))["delta_f_over_f_pct"]
        self.assertLess(abs(far), abs(near))

    def test_frequency_matches_cavity_plane_resonance(self):
        v = _v(P.plane_open_end(50, 30, 0.2, 4.3, m=1, n=0))
        modes = CV.plane_resonance(50, 30, 4.3, 1, 1, 1e9)["value"]
        want = [e for e in modes if e["m"] == 1 and e["n"] == 0][0]["f_hz"]
        _close(self, v["f_resonance_hz"], want, rel=1e-4)

    def test_refusals(self):
        cases = [
            ((50, 50, 5.5, 4.3), {}, "MODEL_SCOPE"),          # a/h < 10
            ((50, 4.0, 0.5, 4.3), {}, "MODEL_SCOPE"),          # b/h < 10
            ((50, 50, 0.2, 4.3), dict(m=0, n=0), "ARG_RANGE"),
            ((50, 50, 0.2, 4.3), dict(open_edges=[]), "ARG_RANGE"),
            ((50, 50, 0.2, 4.3), dict(open_edges=["z0"]), "ARG_INVALID"),
            ((0, 50, 0.2, 4.3), {}, "ARG_RANGE"),
        ]
        for args, kwargs, code in cases:
            env = P.plane_open_end(*args, **kwargs)
            self.assertEqual(env["status"], "error", (args, kwargs))
            self.assertEqual(env["reason_code"], code, (args, kwargs))

    def test_geometry_gate_boundary(self):
        """a/h = 10 is inside the asymptote, 9.9 is not: the gate is exact."""
        self.assertEqual(P.plane_open_end(9.9, 50, 1.0, 4.3)["status"], "error")
        self.assertEqual(P.plane_open_end(10.0, 50, 1.0, 4.3)["status"], "ok")


class TestEnvelopeShape(unittest.TestCase):
    """Every return is the uniform envelope, with the mandatory fields."""

    def test_both_functions_return_the_full_envelope(self):
        for env in (P.plane_radiation_q(50, 50, 0.2, 4.3),
                    P.plane_open_end(50, 50, 0.2, 4.3),
                    P.huray_roughness(1e9, nodule_radius_um=0.5,
                                      surface_ratio=1.0)):
            self.assertEqual(env["status"], "ok")
            for key in ("value", "unit", "model", "basis", "precision",
                        "assumptions"):
                self.assertIn(key, env)
            self.assertTrue(env["precision"])
            self.assertIn("does_not_prove", env)

    def test_ok_envelope_is_what_the_helper_builds(self):
        """A guard on the envelope helper itself: value is a dict, extra merges."""
        env = _ok({"a": 1}, "u", "m", "b", "p", {"x": 1}, {"extra_key": 2})
        self.assertEqual(env["extra_key"], 2)
        self.assertEqual(env["value"], {"a": 1})


if __name__ == "__main__":
    unittest.main()
