"""Tests for the two 0.0.53 additions to plate_modes: copper skins and a point mass.

Same discipline as test_vibration.py: no expected number comes from a run of the
code under test. Each one is either

  * a numeric integral the test does ITSELF (Simpson over z, layer by layer, for
    the transformed section) - not the closed form the module uses, or
  * an exact structural identity that a wrong sign or a swapped factor cannot
    satisfy, or
  * hand arithmetic from the fixture geometry.

The three things most likely to be wrong here, and the test that would catch
each:
  a) the stiffness term silently dropped, so the mass alone runs f1 down -
     TestTheMassAloneIsTheWrongAnswer (the negative control: it must predict LOW)
  b) the point mass smeared over the whole board instead of applied at a point -
     TestAPointIsAPoint (a mass on a node must leave f1 EXACTLY alone)
  c) the whitening factor wrong for the basis actually used -
     TestTheRankOneWhitening (compared against the Cholesky route, independent)
"""
import math
import os
import sys
import unittest

sys.path.insert(0, os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "src"))

from pcb_agent_mcp.calc import vibration as V  # noqa: E402

# ---- fixture: the same 100 x 80 x 1.6 mm FR-4 board as the 0.0.53 hand
# arithmetic, simply supported on all four edges (mount='edges' in the module,
# spelled as explicit edges here so the number is not tied to a preset name).
A_MM = 100.0
B_MM = 80.0
H_MM = 1.6
SS = dict(mount="custom", bc_x=["ss", "ss"], bc_y=["ss", "ss"])

# What the bare plate answered before this change. Hard-coded from the analytic
# SSSS form, not from a run:
#   f11 = pi/2 * (1/a^2 + 1/b^2) * sqrt(D / rho h)
#   D = 24e9 * 1.6e-3^3 / (12 (1 - 0.15^2)) = 8.38056 N m
#   rho h = 1850 * 1.6e-3 = 2.96 kg/m^2
PLAIN_F1 = 677.29
PLAIN_D = 8.38056
PLAIN_MPA = 2.96


def _plate(**kw):
    args = dict(a_mm=A_MM, b_mm=B_MM, thickness_mm=H_MM, material="fr4")
    args.update(SS)
    args.update(kw)
    r = V.plate_modes(**args)
    assert r["status"] == "ok", r
    return r["value"]


def _f1(**kw):
    return _plate(**kw)["modes"][0]["freq_hz"]


def _simpson(f, lo, hi, m=1000):
    """Composite Simpson with m intervals (m must be even; an odd count is not
    composite Simpson at all and is 1e-3 off on a quadratic - it cost a run to
    notice, which is why the parity is written down here)."""
    hh = (hi - lo) / m
    s = f(lo) + f(hi)
    for i in range(1, m):
        s += (4.0 if i % 2 else 2.0) * f(lo + i * hh)
    return s * hh / 3.0


def _section_by_integration(h_m, t, e_gpa, nu, rho):
    """D11 / D12 / D66 / mass-per-area by walking z, layer by layer.

    Deliberately NOT the closed form the module uses: this integrates
    E(z)/(1-nu(z)^2) * z^2 dz, nu(z) E(z)/(1-nu(z)^2) * z^2 dz,
    E(z)/(2(1+nu(z))) * z^2 dz and rho(z) dz numerically, with copper where
    |z| > h/2 and laminate inside. Simpson is exact for the cubics involved, so
    agreement is a statement about the model, not about quadrature.
    """
    hz = h_m / 2.0
    zmax = hz + t
    e = e_gpa * 1e9
    e_cu = V.E_CU

    def layers():
        return ((-zmax, -hz, e_cu, V.NU_CU, V.RHO_CU),
                (-hz, hz, e, nu, rho),
                (hz, zmax, e_cu, V.NU_CU, V.RHO_CU))

    d11 = d12 = d66 = mpa = 0.0
    for lo, hi, ee, nn, rr in layers():
        d11 += _simpson(lambda z: ee / (1.0 - nn * nn) * z * z, lo, hi)
        d12 += _simpson(lambda z: nn * ee / (1.0 - nn * nn) * z * z, lo, hi)
        d66 += _simpson(lambda z: ee / (2.0 * (1.0 + nn)) * z * z, lo, hi)
        mpa += _simpson(lambda z: rr, lo, hi)
    return d11, d12, d66, mpa


class TestTheTransformedSection(unittest.TestCase):
    """The copper skins enter through D11/D12/D66 and the mass per area, and
    each number is checked against a numeric integral done here."""

    def _check(self, cu_um):
        v = _plate(copper_um=cu_um)
        c = v["composite"]
        self.assertIsNotNone(c)
        d11, d12, d66, mpa = _section_by_integration(
            H_MM * 1e-3, cu_um * 1e-6, 24.0, 0.15, 1850.0)
        # 6 SIGNIFICANT DIGITS is what the envelope reports, so the tolerance
        # has to sit above half a unit in the 6th digit (5e-6 relative here),
        # not at machine epsilon. 1e-5 still catches every factor/sign error.
        self.assertAlmostEqual(c["d11_n_m"], d11, delta=abs(d11) * 1e-5)
        self.assertAlmostEqual(c["d12_n_m"], d12, delta=abs(d12) * 1e-5 + 1e-9)
        self.assertAlmostEqual(c["d66_n_m"], d66, delta=abs(d66) * 1e-5)
        self.assertAlmostEqual(v["mass_per_area_kg_m2"], mpa, delta=mpa * 1e-5)
        # the reported flexural rigidity IS d11 - the whole stiffness of the
        # solve now rides on it, not on the laminate's own D
        self.assertAlmostEqual(v["flexural_rigidity_n_m"], c["d11_n_m"],
                               delta=c["d11_n_m"] * 1e-9)

    def test_one_ounce_per_face(self):
        self._check(35.0)

    def test_two_ounces_per_face(self):
        self._check(70.0)

    def test_half_an_ounce_per_face(self):
        self._check(17.5)

    def test_a_different_material_is_not_hard_coded_to_fr4(self):
        """A 30 GPa / nu 0.2 / 1900 kg/m^3 2 mm board, 1 oz skins."""
        args = dict(a_mm=A_MM, b_mm=B_MM, thickness_mm=2.0, e_gpa=30.0, nu=0.2,
                    rho_kg_m3=1900.0, copper_um=35.0)
        args.update(SS)
        v = V.plate_modes(**args)["value"]
        d11, d12, d66, mpa = _section_by_integration(2e-3, 35e-6, 30.0, 0.2, 1900.0)
        c = v["composite"]
        self.assertAlmostEqual(c["d11_n_m"], d11, delta=d11 * 1e-5)
        self.assertAlmostEqual(c["d12_n_m"], d12, delta=abs(d12) * 1e-5 + 1e-9)
        self.assertAlmostEqual(c["d66_n_m"], d66, delta=d66 * 1e-5)
        self.assertAlmostEqual(v["mass_per_area_kg_m2"], mpa, delta=mpa * 1e-5)

    def test_the_laminate_d11_is_reported_next_to_the_composite(self):
        c = _plate(copper_um=35.0)["composite"]
        self.assertAlmostEqual(c["laminate_d11_n_m"], PLAIN_D, delta=PLAIN_D * 1e-5)
        self.assertAlmostEqual(c["copper_um_per_face"], 35.0)

    def test_the_mass_share_of_the_copper(self):
        # 2 * 8960 * 35e-6 / 3.5872 = 0.17487
        c = _plate(copper_um=35.0)["composite"]
        self.assertAlmostEqual(c["mass_share_copper"], 0.1748, delta=1e-4)
        c2 = _plate(copper_um=70.0)["composite"]
        self.assertAlmostEqual(c2["mass_share_copper"], 0.2976, delta=1e-4)

    def test_the_effective_poisson_ratio_stays_between_the_two_materials(self):
        """d12/d11 must equal 1 - 2 d66/d11 layer by layer.

        That identity holds for EVERY isotropic layer separately (algebra), so
        a weighted sum of the layers keeps it, and it is what makes the two
        skin terms collapse onto one effective nu. A sign error in d12 or a
        factor-2 error in d66 breaks it - which is the point."""
        for cu in (35.0, 70.0):
            c = _plate(copper_um=cu)["composite"]
            nu_eff = c["d12_n_m"] / c["d11_n_m"]
            self.assertAlmostEqual(nu_eff, 1.0 - 2.0 * c["d66_n_m"] / c["d11_n_m"],
                                   delta=2e-5)
            self.assertGreater(nu_eff, 0.15)
            self.assertLess(nu_eff, V.NU_CU)

    def test_a_zero_skin_leaves_the_laminate_alone(self):
        v = _plate(copper_um=0.0)
        self.assertIsNone(v["composite"])
        self.assertAlmostEqual(v["flexural_rigidity_n_m"], PLAIN_D,
                               delta=PLAIN_D * 1e-5)
        self.assertAlmostEqual(v["modes"][0]["freq_hz"], PLAIN_F1,
                               delta=PLAIN_F1 * 1e-5)


class TestTheShapeDoesNotChange(unittest.TestCase):
    """Because d12/d11 == 1 - 2 d66/d11, the two skin terms are the same bracket
    scaled by d11, so the mode SHAPE is untouched and f1 ~ sqrt(D11 / mass).
    Exact, not approximate - except for the 6-digit rounding of the report."""

    def test_f1_scales_as_sqrt_of_d11_over_mass_per_area(self):
        plain = _plate()
        for cu in (17.5, 35.0, 70.0):
            v = _plate(copper_um=cu)
            pred = plain["modes"][0]["freq_hz"] * math.sqrt(
                (v["flexural_rigidity_n_m"] / v["mass_per_area_kg_m2"])
                / (plain["flexural_rigidity_n_m"] / plain["mass_per_area_kg_m2"]))
            self.assertAlmostEqual(v["modes"][0]["freq_hz"], pred,
                                   delta=pred * 1e-6)

    def test_the_antinode_and_the_quiet_area_do_not_move(self):
        plain = _plate()
        v = _plate(copper_um=70.0)
        self.assertEqual(v["modes"][0]["antinode_mm"],
                         plain["modes"][0]["antinode_mm"])
        self.assertEqual(v["modes"][0]["quiet_area_fraction"],
                         plain["modes"][0]["quiet_area_fraction"])

    def test_the_measured_numbers(self):
        self.assertAlmostEqual(_f1(), PLAIN_F1, delta=PLAIN_F1 * 1e-5)
        self.assertAlmostEqual(_f1(copper_um=35.0), 800.849, delta=0.05)
        self.assertAlmostEqual(_f1(copper_um=70.0), 888.369, delta=0.05)


class TestTheMassAloneIsTheWrongAnswer(unittest.TestCase):
    """Negative control. Copper adds stiffness AND mass; the mass alone pulls f1
    DOWN, so a code that only grew the mass per area would answer below the bare
    plate. The real answer is above - and the gap is the whole point."""

    def test_pure_mass_would_predict_low_but_the_answer_is_high(self):
        plain = _plate()
        v = _plate(copper_um=35.0)
        f_mass_only = plain["modes"][0]["freq_hz"] / math.sqrt(
            v["mass_per_area_kg_m2"] / plain["mass_per_area_kg_m2"])
        self.assertLess(f_mass_only, plain["modes"][0]["freq_hz"])   # it IS low
        self.assertGreater(v["modes"][0]["freq_hz"], f_mass_only * 1.2)
        # 2 oz: the stiffness wins by even more
        v2 = _plate(copper_um=70.0)
        f_mass_only2 = plain["modes"][0]["freq_hz"] / math.sqrt(
            v2["mass_per_area_kg_m2"] / plain["mass_per_area_kg_m2"])
        self.assertGreater(v2["modes"][0]["freq_hz"], f_mass_only2 * 1.3)


class TestAPointIsAPoint(unittest.TestCase):
    """A mass at a node of a mode must leave that mode EXACTLY alone. A smeared
    mass (a global scaling of rho) would move it. This is what separates a
    rank-one update from 'add the mass and re-solve'."""

    def test_a_mass_on_a_node_is_a_no_op(self):
        v = _plate(added_mass_g=20.0, added_mass_at_mm=[0.0, B_MM / 2.0])
        self.assertEqual(v["added_mass"]["whitening_c"], 0.0)
        self.assertEqual(v["added_mass"]["mode_basis_at_point"], 0.0)
        self.assertAlmostEqual(v["modes"][0]["freq_hz"], PLAIN_F1,
                               delta=PLAIN_F1 * 1e-6)
        # every mode, not just the first, because the basis vanishes there
        plain = _plate(n_modes=4)
        for got, ref in zip(v["modes"], plain["modes"]):
            self.assertAlmostEqual(got["freq_hz"], ref["freq_hz"],
                                   delta=ref["freq_hz"] * 1e-6)

    def test_the_mass_hurts_most_at_the_antinode(self):
        """f1 has to drop MOST at the centre of the (1,1) mode, less at a
        quarter point, and not at all on a nodal line.

        Note what is NOT asserted: that mode_basis_at_point (= |s|^2, the norm
        of the WHOLE whitened basis at that point) peaks at the centre. It does
        not - it is not the mode-1 amplitude, and it reads 13.78 at the centre
        against 14.88 at (25,20). Measuring 316 Hz there against 436 Hz settles
        which of the two the mass actually cares about."""
        f_centre = _f1(added_mass_g=20.0)
        f_quarter = _f1(added_mass_g=20.0, added_mass_at_mm=[25.0, 20.0])
        f_near_edge = _f1(added_mass_g=20.0, added_mass_at_mm=[95.0, B_MM / 2.0])
        f_node = _f1(added_mass_g=20.0, added_mass_at_mm=[0.0, B_MM / 2.0])
        self.assertLess(f_centre, f_quarter)
        self.assertLess(f_quarter, f_near_edge)
        self.assertLess(f_near_edge, f_node)
        self.assertAlmostEqual(f_node, PLAIN_F1, delta=PLAIN_F1 * 1e-6)

    def test_f1_falls_and_falls_faster_as_the_mass_grows(self):
        prev = PLAIN_F1
        for m in (1.0, 5.0, 20.0, 50.0):
            f = _f1(added_mass_g=m)
            self.assertLess(f, prev)
            prev = f

    def test_the_measured_point_mass_number(self):
        self.assertAlmostEqual(_f1(added_mass_g=20.0), 316.044, delta=0.05)


class TestTheSingleModeAnchor(unittest.TestCase):
    """For the (1,1) SSSS mode the shape is sin(pi x/a) sin(pi y/b), so at the
    centre (c^T s)^2 = 4 and the single-mode Rayleigh quotient gives

        f = f0 / sqrt(1 + 4 m / M_plate)

    This is an OUTSIDE reference (plate theory), not the code's own answer: the
    code solves the full rank-one problem, so it must land close to this but is
    not required to hit it. 4 % is the stated tolerance and the measured miss is
    ~2.4 %, which is the multi-mode content the anchor ignores."""

    def test_the_centre_mass_lands_within_four_percent(self):
        v = _plate(added_mass_g=20.0)
        m_plate = v["added_mass"]["plate_mass_g"]
        pred = PLAIN_F1 / math.sqrt(1.0 + 4.0 * 20.0 / m_plate)
        got = v["modes"][0]["freq_hz"]
        self.assertAlmostEqual(got, pred, delta=pred * 0.04)

    def test_the_naive_beta_anchor_would_be_wrong(self):
        """beta|s|^2 uses the whitened norm as if the mode were s itself, which
        over-counts by (s.s)/(c.s)^2 and answers ~190 Hz against a measured 316.
        Recording the failure mode so nobody 'fixes' the code back to it."""
        v = _plate(added_mass_g=20.0)
        m_plate = v["added_mass"]["plate_mass_g"]
        beta_s2 = v["added_mass"]["mass_ratio"] * v["added_mass"][
            "mode_basis_at_point"]
        naive = PLAIN_F1 / math.sqrt(1.0 + beta_s2)
        self.assertLess(naive, 200.0)
        self.assertGreater(v["modes"][0]["freq_hz"], naive * 1.5)


class TestTheRankOneWhitening(unittest.TestCase):
    """_rank1_whiten is checked against the Cholesky route on a random SPD
    problem, and against the definition W^T M W = I. Independent of the plate."""

    def _problem(self, n=6, seed=12345):
        # a small deterministic LCG, so the test needs no random module state
        state = [seed]

        def rnd():
            state[0] = (1103515245 * state[0] + 12345) % (1 << 31)
            return state[0] / float(1 << 31) - 0.5

        stiff = [[0.0] * n for _ in range(n)]
        for i in range(n):
            stiff[i][i] = 2.0 + abs(rnd())
            for j in range(i):
                stiff[i][j] = stiff[j][i] = rnd()
        s = [rnd() for _ in range(n)]
        return stiff, s

    def test_it_matches_the_cholesky_route(self):
        """Same eigenvalues as the Cholesky route, not the same matrix.

        Two traps, both hit while writing this, both written down so nobody
        walks back into them:

          * the whitening factor from M = L L^T is L^-T, NOT L^-1. L^-1 does
            not whiten: X M X^T is I only for X = L^-1, and X^T M X is not.
            The loop below asserts X M X^T = I so this cannot pass by luck.
          * L^-T and the closed form M^-1/2 both whiten but are related by an
            ORTHOGONAL factor, so the whitened matrices differ elementwise and
            only their eigenvalues agree - which is all the solver reads.
            Comparing elements is the wrong test at any tolerance."""
        for beta in (0.05, 0.7, 3.0):
            stiff, s = self._problem()
            n = len(stiff)
            out, apply = V._rank1_whiten(stiff, s, beta)
            self.assertIsNotNone(apply)
            mm = [[(1.0 if i == j else 0.0) + beta * s[i] * s[j]
                   for j in range(n)] for i in range(n)]
            L = V._cholesky(mm)
            self.assertIsNotNone(L)
            X = V._lower_inverse(L)                    # L^-1, lower triangular
            for i in range(n):                         # X M X^T == I
                for j in range(n):
                    got = sum(X[i][k] * mm[k][l] * X[j][l]
                              for k in range(n) for l in range(n))
                    self.assertAlmostEqual(got, 1.0 if i == j else 0.0, delta=1e-12)
            ref = [[sum(X[i][k] * stiff[k][l] * X[j][l]
                        for k in range(n) for l in range(n))
                    for j in range(n)] for i in range(n)]
            lam_cf, _v = V._jacobi(out)
            lam_ch, _v = V._jacobi(ref)
            for a, bb in zip(lam_cf, lam_ch):
                self.assertAlmostEqual(a, bb, delta=max(1e-9, abs(bb) * 1e-9))

    def test_the_whitening_is_symmetric(self):
        """Recover A from apply and check A = Aᵀ and Aᵀ M A = I.

        Symmetry is not decoration: the solver is a Jacobi sweep, which assumes
        a symmetric matrix. The symmetric square root has it, L^-1 does not."""
        stiff, s = self._problem(n=5, seed=999)
        out, apply = V._rank1_whiten(stiff, s, 1.3)
        n = len(stiff)
        self.assertIsNotNone(apply)
        A = [[0.0] * n for _ in range(n)]
        for j in range(n):
            e = [0.0] * n
            e[j] = 1.0
            col = apply(e)
            for i in range(n):
                A[i][j] = col[i]
        for i in range(n):
            for j in range(n):
                self.assertAlmostEqual(A[i][j], A[j][i], delta=1e-12)
        for i in range(n):
            for j in range(n):
                got = 0.0
                for k in range(n):
                    for l in range(n):
                        got += A[k][i] * ((1.0 if k == l else 0.0)
                                          + 1.3 * s[k] * s[l]) * A[l][j]
                self.assertAlmostEqual(got, 1.0 if i == j else 0.0, delta=1e-9)
                # and out == Aᵀ K A, the matrix the solver actually diagonalises
                ref = sum(A[k][i] * stiff[k][l] * A[l][j]
                          for k in range(n) for l in range(n))
                self.assertAlmostEqual(out[i][j], ref, delta=1e-9)

    def test_a_mass_on_a_node_returns_the_input_untouched(self):
        stiff, _s = self._problem(n=4)
        out, apply = V._rank1_whiten(stiff, [0.0, 0.0, 0.0, 0.0], 5.0)
        self.assertIsNone(apply)
        self.assertEqual(out, stiff)

    def test_a_zero_mass_returns_the_input_untouched(self):
        stiff, s = self._problem(n=4)
        out, apply = V._rank1_whiten(stiff, s, 0.0)
        self.assertIsNone(apply)
        self.assertEqual(out, stiff)


class TestTheOutputStaysHonest(unittest.TestCase):
    """The envelope has to describe what it actually gave. Both blocks are
    absent (None) unless the argument that produces them was given."""

    def test_both_new_blocks_are_none_by_default(self):
        v = _plate()
        self.assertIsNone(v["composite"])
        self.assertIsNone(v["added_mass"])

    def test_the_copper_warning_disappears_only_when_copper_is_given(self):
        blob = " ".join(_plate()["warnings"])
        self.assertIn("copper stiffening is not modelled", blob)
        blob = " ".join(_plate(copper_um=35.0)["warnings"])
        self.assertNotIn("copper stiffening is not modelled", blob)
        self.assertIn("SYMMETRIC skin pair", blob)
        self.assertIn("35", blob)

    def test_the_mass_warning_disappears_only_when_a_mass_is_given(self):
        blob = " ".join(_plate()["warnings"])
        self.assertIn("component mass is not modelled", blob)
        blob = " ".join(_plate(added_mass_g=20.0)["warnings"])
        self.assertNotIn("component mass is not modelled", blob)

    def test_the_mounting_warning_goes_away_only_when_both_are_given(self):
        for kw in ({}, {"copper_um": 35.0}, {"added_mass_g": 20.0}):
            blob = " ".join(_plate(**kw)["warnings"])
            self.assertIn("mounting compliance and shear deformation", blob)
        blob = " ".join(_plate(copper_um=35.0, added_mass_g=20.0)["warnings"])
        self.assertNotIn("mounting compliance and shear deformation", blob)

    def test_the_mass_report_is_internally_consistent(self):
        v = _plate(added_mass_g=20.0)
        m = v["added_mass"]
        self.assertAlmostEqual(m["plate_mass_g"],
                               v["mass_per_area_kg_m2"] * A_MM * 1e-3 * B_MM * 1e-3 * 1e3,
                               delta=m["plate_mass_g"] * 1e-5)
        self.assertAlmostEqual(m["mass_ratio"], 20.0 / m["plate_mass_g"],
                               delta=m["mass_ratio"] * 1e-5)
        self.assertAlmostEqual(m["whitening_c"],
                               math.sqrt(1.0 + m["mass_ratio"]
                                         * m["mode_basis_at_point"]) - 1.0,
                               delta=max(1e-6, m["whitening_c"] * 1e-4))
        self.assertEqual(m["at_mm"], [A_MM / 2.0, B_MM / 2.0])

    def test_the_default_point_is_the_plate_centre(self):
        v = _plate(added_mass_g=20.0, added_mass_at_mm=[50.0, 40.0])
        w = _plate(added_mass_g=20.0)
        self.assertEqual(v["modes"][0]["freq_hz"], w["modes"][0]["freq_hz"])

    def test_the_plate_mass_grows_with_copper(self):
        self.assertAlmostEqual(_plate()["mass_per_area_kg_m2"], PLAIN_MPA,
                               delta=PLAIN_MPA * 1e-5)
        # 2.96 + 2*8960*35e-6 = 3.5872
        self.assertAlmostEqual(_plate(copper_um=35.0)["mass_per_area_kg_m2"],
                               3.5872, delta=3.5872 * 1e-5)


class TestTheStressSurface(unittest.TestCase):
    """With copper the OUTERMOST material is copper, so the bending stress at a
    given curvature has to use copper's E and nu at copper's z."""

    def test_the_surface_is_labelled_and_sits_at_the_copper_face(self):
        s = _plate(copper_um=35.0, stress_amplitude_mm=0.01)["modes"][0]["stress"]
        self.assertEqual(s["surface_material"], "copper")
        self.assertAlmostEqual(s["surface_z_mm"], 0.835, delta=1e-6)

    def test_without_copper_the_surface_is_the_laminate_at_h_over_2(self):
        s = _plate(stress_amplitude_mm=0.01)["modes"][0]["stress"]
        self.assertEqual(s["surface_material"], "laminate")
        self.assertAlmostEqual(s["surface_z_mm"], 0.8, delta=1e-6)

    def test_the_stress_scales_by_the_surface_modulus_times_z(self):
        """sigma = -Q(z) z (k + nu k') at the peak, and BOTH the modulus and the
        nu in that bracket belong to the layer at the surface. The curvature is
        the same with and without copper at equal amplitude, so the ratio of the
        two reported peaks must be

            (E_CU z_cu /(1-nu_cu^2)) / (E_lam z_lam /(1-nu_lam^2))
            times (k_y + nu_cu k_x) / (k_y + nu_lam k_x)

        - built here from the material tables and the curvatures the tool
        reports, never from the tool's stress.

        Registered correction (0.0.57): 0.0.56 used copper's E beside the
        LAMINATE's nu, so this ratio collapsed to the bare material factor
        5.2874. The honest value on this plate is 5.8741; a revert fails here.
        """
        lam = _plate(stress_amplitude_mm=0.01)["modes"][0]["stress"]
        cop = _plate(copper_um=35.0, stress_amplitude_mm=0.01)["modes"][0]["stress"]
        kx, ky = cop["curvature_per_m"]
        kx0, ky0 = lam["curvature_per_m"]
        # copper at equal amplitude does not move the curvature of this mode
        self.assertAlmostEqual(kx / kx0, 1.0, places=9)
        self.assertAlmostEqual(ky / ky0, 1.0, places=9)
        e_lam, nu_lam, z_lam = 24e9, 0.15, 0.8e-3
        factor = ((V.E_CU * 0.835e-3 / (1.0 - V.NU_CU ** 2))
                  / (e_lam * z_lam / (1.0 - nu_lam ** 2)))
        mix = (ky + V.NU_CU * kx) / (ky + nu_lam * kx)
        self.assertAlmostEqual(cop["sigma_max_mpa"] / lam["sigma_max_mpa"],
                               factor * mix, delta=factor * mix * 2e-3)
        # and the nu in the bracket is not a no-op on this plate: the old
        # mixed-nu recipe is >5 % away, so the assertion above has teeth
        self.assertGreater(abs(mix - 1.0), 0.05)
        self.assertNotAlmostEqual(cop["sigma_max_mpa"] / lam["sigma_max_mpa"],
                                  factor, delta=factor * 0.05)
        # von Mises is NOT scaled by that ratio: it mixes sigma with tau, and
        # tau / sigma depends on nu as well, so a different nu moves the mix.
        self.assertNotAlmostEqual(cop["von_mises_mpa"] / lam["von_mises_mpa"],
                                  factor, delta=factor * 0.05)

    def test_the_strain_uses_the_surface_modulus(self):
        cop = _plate(copper_um=35.0, stress_amplitude_mm=0.01)["modes"][0]["stress"]
        self.assertAlmostEqual(cop["surface_strain_ue"],
                               1e6 * (cop["sigma_max_mpa"] * 1e6) / V.E_CU,
                               delta=cop["surface_strain_ue"] * 1e-4)


class TestRefusals(unittest.TestCase):
    def _code(self, **kw):
        args = dict(a_mm=A_MM, b_mm=B_MM, thickness_mm=H_MM, material="fr4")
        args.update(SS)
        args.update(kw)
        return V.plate_modes(**args)

    def test_a_zero_mass_is_refused_not_ignored(self):
        r = self._code(added_mass_g=0.0)
        self.assertEqual(r["status"], "error")
        self.assertEqual(r["reason_code"], "ARG_RANGE")
        self.assertIn("must be a number > 0", r["message"])
        self.assertIn("no component mass", r["hint"])

    def test_a_negative_mass_is_refused(self):
        self.assertEqual(self._code(added_mass_g=-1.0)["reason_code"], "ARG_RANGE")

    def test_a_mass_off_the_plate_is_refused(self):
        for p in ([120.0, 40.0], [50.0, -1.0]):
            r = self._code(added_mass_g=1.0, added_mass_at_mm=p)
            self.assertEqual(r["status"], "error")
            self.assertEqual(r["reason_code"], "ARG_RANGE")
            self.assertIn("outside the plate", r["message"])

    def test_a_malformed_point_is_refused(self):
        r = self._code(added_mass_g=1.0, added_mass_at_mm="here")
        self.assertEqual(r["reason_code"], "ARG_INVALID")

    def test_negative_copper_is_refused(self):
        self.assertEqual(self._code(copper_um=-1.0)["reason_code"], "ARG_RANGE")

    def test_non_numeric_copper_is_refused(self):
        self.assertEqual(self._code(copper_um="lots")["reason_code"], "ARG_RANGE")

    def test_copper_thicker_than_a_quarter_of_the_laminate_is_out_of_validity(self):
        # h/4 = 0.4 mm per face is the line (h/2 = 0.8 mm is the half-thickness)
        r = self._code(copper_um=420.0)
        self.assertEqual(r["status"], "error")
        self.assertEqual(r["reason_code"], "OUT_OF_VALIDITY")
        self.assertIn("thin-plate limit", r["hint"])
        # and just under it is still answered
        self.assertEqual(self._code(copper_um=399.0)["status"], "ok")

    def test_a_zero_mass_does_not_hide_a_normal_answer(self):
        r = self._code(added_mass_g=0.0)
        self.assertNotIn("status", r.get("value", {}))
        self.assertEqual(r["status"], "error")


class TestThroughTheToolSurface(unittest.TestCase):
    def test_the_schema_carries_the_three_new_arguments(self):
        from pcb_agent_mcp import tools
        spec = tools.TOOLS["vibration_plate_modes"]
        props = spec["inputSchema"]["properties"]
        for name in ("copper_um", "added_mass_g", "added_mass_at_mm"):
            self.assertIn(name, props)

    def test_the_tool_passes_them_through(self):
        from pcb_agent_mcp import tools
        base = {"a_mm": A_MM, "b_mm": B_MM, "thickness_mm": H_MM,
                "material": "fr4", "mount": "custom",
                "bc_x": ["ss", "ss"], "bc_y": ["ss", "ss"]}
        r = tools.call_tool("vibration_plate_modes",
                            dict(base, copper_um=35.0, added_mass_g=20.0))
        self.assertEqual(r["status"], "ok")
        self.assertIsNotNone(r["value"]["composite"])
        self.assertIsNotNone(r["value"]["added_mass"])

    def test_the_bare_call_through_the_tool_is_unchanged(self):
        from pcb_agent_mcp import tools
        r = tools.call_tool("vibration_plate_modes",
                            {"a_mm": A_MM, "b_mm": B_MM,
                             "thickness_mm": H_MM, "material": "fr4",
                             "mount": "custom", "bc_x": ["ss", "ss"],
                             "bc_y": ["ss", "ss"]})
        self.assertEqual(r["status"], "ok")
        self.assertIsNone(r["value"]["composite"])
        self.assertIsNone(r["value"]["added_mass"])
        self.assertAlmostEqual(r["value"]["modes"][0]["freq_hz"], PLAIN_F1,
                               delta=PLAIN_F1 * 1e-5)

    def test_the_tool_count_is_unchanged(self):
        # a deliberate ratchet: adding a tool must bump this number, so the surface
        # cannot grow by accident. 162 -> 164 in 0.0.55 (magnetics_select_core and
        # magnetics_data, both backed by data/magnetic_materials.json);
        # 164 -> 165 in 0.0.59 (fusing_current_taper - a new tool rather than a new
        # parameter, because one width_mm cannot express a tapered geometry).
        from pcb_agent_mcp import tools
        # 168 = 162 + the three 0.0.56..0.0.59 additions + the three 0.0.60
        # plane additions (roughness_huray, plane_radiation_q, plane_open_end);
        # 169 = 168 + current_density_field in 0.0.63 (a 2-D field solve is a new
        # tool, not a flag on the 1-D ones: it needs a geometry, not a width)
        # 170 = 169 + pdn_step_response in 0.0.65 (a time-domain answer, not a
        # flag on the frequency-domain one: it needs an edge rate and a regulator)
        # 171 = 170 + ac_sheet_resistance in 0.0.66 (the 1-D skin-effect answer
        # for a sheet; current_density_field now CONSUMES it via frequency_hz,
        # but the closed-form R(f)/delta/t_eff/loss question is its own tool)
        # 172 = 171 + ac_round_conductor in 0.0.67 (a sheet and a round wire are
        # NOT the same geometry: a bore is a Neumann surface with no net current
        # behind it, and a plated via needs the annulus, so this is a new tool
        # with its own solver, not a flag on ac_sheet_resistance)
        # 173 = 172 + ac_self_heating in 0.0.68 (R at a frequency is an INPUT to
        # the thermal loop, not a separate calculation: the answer is a TEMPERATURE
        # and the loop has to be solved for, so it is its own tool)
        # 174 = 173 + magnetics_fringing_loss in 0.0.72 (the field that leaks out
        # of an air gap is a different field from the 1-D stack field: Dowell
        # cannot see it at all, and its driver is the MMF in the gap, not the
        # winding's own ampere-turns)
        # 175 = 174 + magnetics_material_core_loss in 0.0.74 (a vendor's printed
        # per-permeability fit is a different object from a material-level
        # Steinmetz triple, so it is its own tool rather than a new argument)
        # 179 = 178 + magnetics_core_loss_audit in 0.0.81 (scoring a triple against
        # the vendor's printed points is a different question from evaluating a fit)
        # 180 = 179 + thermal_spreading in 0.0.85 (a rectangular heat source's
        # spreading resistance is its own question; thermal_stack stays strictly 1-D)
        # 203 = 200 + the three 0.0.121 additions (board_net_report, board_pour_audit,
        # board_render): being able to sort nets by detour, to see a pour's
        # islands, and to LOOK at the copper are three separate questions,
        # each of which the previous 200 tools could not answer.
        # 223 = 220 + the three routing-discipline tools in 0.0.122
        self.assertEqual(len(tools.TOOLS), 227)

    def test_capabilities_states_the_boundaries_0_0_57_moved(self):
        """The honesty list must move WITH the code: 0.0.53 claimed symmetric-only
        skins and one point mass; 0.0.57 models different faces and distributed
        patches, so those two items have to name the NEW boundary (flat-state
        linearisation for an unsymmetric stack, stiffness still absent) instead of
        claiming the capability is missing."""
        from pcb_agent_mcp import tools
        c = tools.call_tool("calc_capabilities", {})
        blob = " ".join(c["not_implemented_yet"])
        self.assertNotIn("no component mass or copper stiffening", blob)
        # the old blanket denials are gone because they are now false
        self.assertNotIn("SYMMETRIC copper skins are modelled", blob)
        self.assertNotIn("distributed component mass, component stiffness", blob)
        # ...and what replaced them names the real remaining limit
        self.assertIn("DIFFERENT thicknesses on the two faces ARE", blob)
        self.assertIn("FLAT-STATE", blob)
        self.assertIn("REDUCED stiffness", blob)
        self.assertIn("Component STIFFNESS is not modelled", blob)


if __name__ == "__main__":
    unittest.main()
