"""Tests for the 0.0.57 additions: an unsymmetric section and distributed mass.

Same discipline as the other vibration tests: no expected number is a run of the
code under test. Each one is either

  * a closed form (the SSSS eigenvalue on a composite D and mpa, the 1/sqrt(1+beta)
    of a uniform added mass), computed from the fixture with nothing but
    arithmetic, or
  * an exact structural identity a wrong sign cannot satisfy (a symmetric stack's
    B is the zero matrix, the two faces of a symmetric board carry equal von
    Mises), or
  * hand arithmetic from the numbers the tool reports (the surface stress rebuilt
    from the reported curvatures with copper's own E and nu).

The four ways this could go quietly wrong, and the test that catches each:

  a) the membrane-bending term dropped, so one-sided copper reads as if it were
     symmetric - TestAsymmetricSection / TestOneSidedCopperIsSofterThanSymmetric
  b) a patch's mass divided by nothing, so a shrinking patch stops weighing
     anything at all - TestAPatchShrinksToThePointMass (its limit is the point
     mass, which is an independent code path)
  c) the surface stress keeping the laminate's nu beside copper's E (what 0.0.56
     did) - TestTheSurfaceUsesItsOwnNu, negative control included
  d) a symmetric board's two faces reading differently - TestBothFacesAgree
"""
import math
import os
import sys
import unittest

sys.path.insert(0, os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "src"))

from pcb_agent_mcp.calc import vibration as V          # noqa: E402
from pcb_agent_mcp.calc import vibration_poly as VP   # noqa: E402

# ---- fixture: 100 x 80 x 1.6 mm FR-4, the board the docs quote --------------
A_MM, B_MM, H_MM = 100.0, 80.0, 1.6
E_LAM, NU_LAM, RHO_LAM = 24e9, 0.15, 1850.0
E_CU, NU_CU, RHO_CU = 110e9, 0.34, 8960.0
H_M = H_MM * 1e-3


def _inv3(m):
    """Plain 3x3 inverse (cofactors) - the tests may not borrow the module's."""
    det = (m[0][0] * (m[1][1] * m[2][2] - m[1][2] * m[2][1])
           - m[0][1] * (m[1][0] * m[2][2] - m[1][2] * m[2][0])
           + m[0][2] * (m[1][0] * m[2][1] - m[1][1] * m[2][0]))
    c = [[0.0] * 3 for _ in range(3)]
    for i in range(3):
        for j in range(3):
            a = [[m[r][cc] for cc in range(3) if cc != j]
                 for r in range(3) if r != i]
            c[j][i] = ((-1.0) ** (i + j)) * (a[0][0] * a[1][1] - a[0][1] * a[1][0])
    return [[c[i][j] / det for j in range(3)] for i in range(3)]
MPA_LAM = RHO_LAM * H_M                    # 2.96 kg/m^2
ONE_OZ = 35.0                              # um


def _plate(**kw):
    r = V.plate_modes(A_MM, B_MM, H_MM, material="fr4", **kw)
    assert r["status"] == "ok", r
    return r["value"]


def _sq(edges):
    """SSSS eigenvalue of a rectangle: omega = pi^2 sqrt(D/mua) (1/a^2 + 1/b^2)."""
    D, mpa = edges
    return (math.pi ** 2 * math.sqrt(D / mpa)
            * (1.0 / (A_MM * 1e-3) ** 2 + 1.0 / (B_MM * 1e-3) ** 2) / (2.0 * math.pi))


class TestSymmetricSection(unittest.TestCase):
    """A symmetric stack must keep B exactly zero, and D must be the sum."""

    def test_b_is_exactly_zero(self):
        s = V._laminate_section(H_M, E_LAM, NU_LAM, 35e-6, 35e-6)
        self.assertIsNotNone(s)
        # not "small": the two skins' first moments cancel term by term
        self.assertEqual(s["b11"], 0.0)
        self.assertFalse(s["asym"])
        self.assertEqual(s["z0"], 0.0)

    def test_d_is_the_layer_by_layer_sum(self):
        t = 35e-6
        hz = H_M / 2.0
        iz_cu = ((hz + t) ** 3 - hz ** 3) / 3.0        # int z^2 dz, one skin
        want = (E_LAM * H_M ** 3 / (12.0 * (1.0 - NU_LAM ** 2))
                + 2.0 * E_CU / (1.0 - NU_CU ** 2) * iz_cu)
        s = V._laminate_section(H_M, E_LAM, NU_LAM, t, t)
        self.assertAlmostEqual(s["d11"] / want, 1.0, places=12)

    def test_the_closed_form_path_agrees_with_the_section(self):
        """`copper_um` alone still uses the 0.0.53 closed form; the section must
        reproduce it, or the two routes have quietly diverged."""
        v = _plate(copper_um=ONE_OZ, mount="ssss")
        s = V._laminate_section(H_M, E_LAM, NU_LAM, ONE_OZ * 1e-6, ONE_OZ * 1e-6)
        # the closed form integrates the same skin, so the two agree to the ulp
        # of the two algebraically-equal expressions, not exactly
        self.assertLess(abs(v["flexural_rigidity_n_m"] / s["d11_sym"] - 1.0), 1e-5)
        self.assertLess(abs(v["mass_per_area_kg_m2"]
                            / (MPA_LAM + 2.0 * RHO_CU * 35e-6) - 1.0), 1e-9)


class TestSymmetricCopperAgainstTheClosedForm(unittest.TestCase):
    """With copper on both faces the plate is still isotropic, so the exact SSSS
    eigenvalue of the composite section is known - no Ritz tolerance to hide in."""

    def test_ssss_eigenvalue(self):
        for cu in (None, ONE_OZ, 2 * ONE_OZ):
            v = _plate(copper_um=cu, mount="ssss")
            got = v["modes"][0]["freq_hz"]
            want = _sq((v["flexural_rigidity_n_m"],
                        v["mass_per_area_kg_m2"]))
            self.assertLess(abs(got - want) / want, 1e-4,
                            "cu=%s got %.4f want %.4f" % (cu, got, want))

    def test_copper_raises_f1(self):
        bare = _plate(mount="ssss")["modes"][0]["freq_hz"]
        cu = _plate(copper_um=ONE_OZ, mount="ssss")["modes"][0]["freq_hz"]
        self.assertGreater(cu, bare)


class TestAsymmetricSection(unittest.TestCase):
    """One-sided copper: B != 0, the plate softens, the neutral plane moves."""

    def setUp(self):
        self.one = V._laminate_section(H_M, E_LAM, NU_LAM, 35e-6, 0.0)

    def test_b_is_not_zero(self):
        self.assertTrue(self.one["asym"])
        self.assertGreater(self.one["b11"], 0.0)

    def test_reduction_is_the_schur_complement(self):
        # Structural identities, not the module's own inverse: for a unit
        # curvature k the mid-plane strain eps0 must carry NO net membrane force
        # (A eps0 + B k = 0), and the moment resultant B eps0 + D k must equal
        # D* k.  D* is rebuilt here with plain matrix arithmetic
        # (D* = D - B A^-1 B) from the blocks the section returns, so a section
        # whose report and whose reduced stiffness disagree is caught.
        a, b, d, e0 = (self.one["a"], self.one["b"], self.one["d"],
                       self.one["eps0"])
        ainv = _inv3(a)
        bainv = [[sum(b[r][t] * ainv[t][c] for t in range(3))
                  for c in range(3)] for r in range(3)]
        dstar = [[d[r][c] - sum(bainv[r][t] * b[t][c] for t in range(3))
                  for c in range(3)] for r in range(3)]
        for kcol in range(3):
            k = [1.0 if i == kcol else 0.0 for i in range(3)]
            for row in range(3):
                n_force = (sum(a[row][c] * e0[c][kcol] for c in range(3))
                           + sum(b[row][c] * k[c] for c in range(3)))
                self.assertAlmostEqual(n_force / a[row][row], 0.0, places=9)
                mom = (sum(b[row][c] * e0[c][kcol] for c in range(3))
                       + sum(d[row][c] * k[c] for c in range(3)))
                # off-diagonal entries of D* are zero for this isotropic
                # section, so compare as a vector scaled by the in-plane D*
                self.assertAlmostEqual(
                    (mom - dstar[row][kcol]) / dstar[0][0], 0.0, places=9)
        # isotropic section: the in-plane diagonal is one number, the twist is
        # a different one, and both are below the unreduced D
        self.assertAlmostEqual(dstar[0][0] / dstar[1][1], 1.0, places=12)
        self.assertAlmostEqual(dstar[0][0] / self.one["d11"], 1.0, places=12)
        self.assertLess(self.one["d11"], self.one["d11_sym"])
        self.assertLess(dstar[2][2], d[2][2])

    def test_neutral_plane_moves_toward_the_copper(self):
        self.assertGreater(self.one["z0"], 0.0)
        mirror = V._laminate_section(H_M, E_LAM, NU_LAM, 0.0, 35e-6)
        self.assertLess(mirror["z0"], 0.0)
        self.assertAlmostEqual(mirror["z0"] / -self.one["z0"], 1.0, places=12)
        self.assertAlmostEqual(mirror["d11"] / self.one["d11"], 1.0, places=12)

    def test_the_reduction_is_reported(self):
        v = _plate(copper_um=ONE_OZ, copper_um_bottom=0)
        a = v["composite"]["asymmetric"]
        self.assertIsNotNone(a)
        self.assertLess(a["d11_reduced_n_m"], a["d11_before_reduction_n_m"])
        self.assertGreater(a["stiffness_lost_pct"], 0.0)


class TestOneSidedCopperIsSofterThanSymmetric(unittest.TestCase):
    def test_ordering(self):
        bare = _plate(mount="ssss")["modes"][0]["freq_hz"]
        one = _plate(mount="ssss", copper_um=ONE_OZ,
                     copper_um_bottom=0)["modes"][0]["freq_hz"]
        both = _plate(mount="ssss", copper_um=ONE_OZ)["modes"][0]["freq_hz"]
        # one skin is half the mass and much less than half the stiffness
        self.assertGreater(one, bare)
        self.assertLess(one, both)


class TestDistributedMass(unittest.TestCase):
    def test_whole_plate_is_a_scalar_mass(self):
        """A patch covering the whole plate adds M = I + beta I, so f must scale
        by exactly 1/sqrt(1+beta) - and beta is read off the tool's own report."""
        bare = _plate(mount="ssss")["modes"][0]["freq_hz"]
        v = _plate(mount="ssss", added_mass_regions=[
            {"mass_g": 20.0, "center_mm": [A_MM / 2.0, B_MM / 2.0],
             "size_mm": [A_MM, B_MM]}])
        beta = v["added_mass_distributed"]["added_over_plate"]
        self.assertLess(abs(beta / (20e-3 / (MPA_LAM * 0.1 * 0.08)) - 1.0), 1e-5)
        want = bare / math.sqrt(1.0 + beta)
        self.assertLess(abs(v["modes"][0]["freq_hz"] - want) / want, 1e-4)

    def test_omitting_size_means_the_whole_plate(self):
        a = _plate(mount="ssss", added_mass_regions=[{"mass_g": 20.0}])
        b = _plate(mount="ssss", added_mass_regions=[
            {"mass_g": 20.0, "size_mm": [A_MM, B_MM]}])
        self.assertEqual(a["modes"][0]["freq_hz"], b["modes"][0]["freq_hz"])

    def test_a_patch_shrinks_to_the_point_mass(self):
        """The negative control for a patch's mass: if the density were not
        normalised by the patch area, a shrinking patch would weigh nothing and
        this would converge to the BARE frequency instead - a 20 g part reading
        as no part at all."""
        bare = _plate(mount="ssss")["modes"][0]["freq_hz"]
        point = _plate(mount="ssss", added_mass_g=20.0,
                       added_mass_at_mm=[50.0, 40.0])["modes"][0]["freq_hz"]
        tiny = _plate(mount="ssss", added_mass_regions=[
            {"mass_g": 20.0, "center_mm": [50.0, 40.0],
             "size_mm": [0.5, 0.5]}])["modes"][0]["freq_hz"]
        self.assertLess(abs(tiny - point) / point, 1e-3)
        # and it must be nowhere near the bare plate, which is what a missing
        # 1/area would have produced
        self.assertGreater(abs(tiny - bare), 20.0 * abs(tiny - point))

    def test_patch_and_point_together(self):
        v = _plate(mount="ssss", added_mass_g=10.0, added_mass_at_mm=[50.0, 40.0],
                   added_mass_regions=[{"mass_g": 10.0, "size_mm": [A_MM, B_MM]}])
        d = v["added_mass_distributed"]
        self.assertAlmostEqual(d["added_mass_total_g"], 20.0, places=9)

    def test_bad_patches_are_refused(self):
        bad = [
            ([{"mass_g": 1.0, "center_mm": [5.0, 5.0], "size_mm": [20.0, 20.0]}],
             "ARG_RANGE"),                       # hangs off the left edge
            ([{"mass_g": 0.0}], "ARG_RANGE"),    # no mass
            ([{"mass_g": -1.0}], "ARG_RANGE"),
            ([{"mass_g": 1.0, "size_mm": [0.0, 5.0]}], "ARG_RANGE"),
            ([], "ARG_INVALID"),                 # empty list is not "no mass"
        ]
        for regs, code in bad:
            r = V.plate_modes(A_MM, B_MM, H_MM, material="fr4", mount="ssss",
                              added_mass_regions=regs)
            self.assertEqual(r["status"], "error", regs)
            self.assertEqual(r["reason_code"], code, regs)


class TestTheSurfaceUsesItsOwnNu(unittest.TestCase):
    """0.0.56 computed the copper surface stress as E_CU z/(1-NU_CU^2) times
    (w_xx + NU_LAM w_yy) - copper's E beside the laminate's nu. 0.0.57 uses the
    layer's own pair. This test rebuilds the number from the curvatures the tool
    reports, and then asserts the OLD recipe gives a materially different answer,
    so a quiet revert is caught."""

    def _stress(self, **kw):
        r = V.plate_stress(A_MM, B_MM, H_MM, 0.2, mode=3, material="fr4", **kw)
        self.assertEqual(r["status"], "ok", r)
        return r["value"]

    def test_new_matches_the_reported_curvatures(self):
        v = self._stress(copper_um=ONE_OZ)
        z = v["surface_z_mm"] * 1e-3
        kx, ky = v["curvature_per_m"]
        kxy = v["tau_xy_mpa"]                    # ~0 at the sigma_max point
        f = E_CU / (1.0 - NU_CU ** 2)
        want_x = -f * z * (kx + NU_CU * ky)
        want_y = -f * z * (ky + NU_CU * kx)
        # sigma/curvature are reported rounded to 6 significant figures, so the
        # rebuild cannot close tighter than a few ppm; 1e-4 still catches a
        # recipe swap (the old one is off by 21 % here).
        self.assertLess(abs(v["sigma_x_mpa"] - want_x / 1e6)
                        / abs(want_x / 1e6), 1e-4)
        self.assertLess(abs(v["sigma_y_mpa"] - want_y / 1e6)
                        / abs(want_y / 1e6), 1e-4)
        self.assertLess(abs(kxy), 1e-9)           # 6.7e-13 in practice

    def test_the_old_recipe_would_have_differed(self):
        v = self._stress(copper_um=ONE_OZ)
        z = v["surface_z_mm"] * 1e-3
        kx, ky = v["curvature_per_m"]
        f = E_CU / (1.0 - NU_CU ** 2)
        old_x = -f * z * (kx + NU_LAM * ky) / 1e6
        # The gap is exactly (kx + nu_cu ky) / (kx + nu_lam ky).  On this mode
        # kx is not zero at the peak, so it is 1.213 and not the full
        # nu_cu/nu_lam = 2.267; assert the identity first, then that it is big.
        want_ratio = (kx + NU_CU * ky) / (kx + NU_LAM * ky)
        self.assertLess(abs(v["sigma_x_mpa"] / old_x / want_ratio - 1.0), 1e-4)
        self.assertGreater(abs(v["sigma_x_mpa"] / old_x), 1.15,
                           "the mixed-nu stress has crept back in")

    def test_no_copper_keeps_the_laminate_surface(self):
        v = self._stress()
        self.assertEqual(v["surfaces_checked"], 1)
        self.assertEqual(v["surface_face"], "laminate")
        self.assertAlmostEqual(v["surface_z_mm"], H_MM / 2.0, places=9)
        z = v["surface_z_mm"] * 1e-3
        kx, ky = v["curvature_per_m"]
        f = E_LAM / (1.0 - NU_LAM ** 2)
        want = -f * z * (kx + NU_LAM * ky) / 1e6
        self.assertLess(abs(v["sigma_x_mpa"] - want) / abs(want), 1e-4)


class TestBothFacesAgree(unittest.TestCase):
    def test_symmetric_copper_gives_two_equal_faces(self):
        v = V.plate_stress(A_MM, B_MM, H_MM, 0.2, mode=3, material="fr4",
                           copper_um=ONE_OZ)
        self.assertEqual(v["status"], "ok")
        st = v["value"]
        self.assertEqual(st["surfaces_checked"], 2)
        d = st["von_mises_by_surface_mpa"]
        self.assertAlmostEqual(d["top"] / d["bottom"], 1.0, places=12)

    def test_one_sided_copper_gives_two_different_faces(self):
        v = V.plate_stress(A_MM, B_MM, H_MM, 0.2, mode=3, material="fr4",
                           copper_um=ONE_OZ, copper_um_bottom=0)
        self.assertEqual(v["status"], "ok")
        st = v["value"]
        d = st["von_mises_by_surface_mpa"]
        self.assertEqual(st["surface_face"], "top")      # the worse one
        self.assertGreater(d["top"], d["bottom"])


class TestPolygonCopper(unittest.TestCase):
    SQ = [[0.0, 0.0], [40.0, 0.0], [40.0, 30.0], [0.0, 30.0]]

    def test_square_agrees_with_the_rectangle_path(self):
        for cu in (None, ONE_OZ, 2 * ONE_OZ):
            r = V.plate_modes(40.0, 30.0, H_MM, material="fr4", mount="clamped",
                              copper_um=cu)["value"]
            p = VP.plate_modes_polygon(self.SQ, H_MM, material="fr4",
                                       mount="clamped", copper_um=cu)["value"]
            want = r["modes"][0]["freq_hz"]
            got = p["modes"][0]["freq_hz"]
            self.assertLess(abs(got - want) / want, 3e-3,
                            "cu=%s rect %.5f poly %.5f" % (cu, want, got))
            self.assertAlmostEqual(p["flexural_rigidity_n_m"]
                                   / r["flexural_rigidity_n_m"], 1.0, places=9)

    def test_copper_um_alone_means_both_faces(self):
        a = VP.plate_modes_polygon(self.SQ, H_MM, material="fr4",
                                   copper_um=ONE_OZ)["value"]
        b = VP.plate_modes_polygon(self.SQ, H_MM, material="fr4",
                                   copper_um=ONE_OZ,
                                   copper_um_bottom=ONE_OZ)["value"]
        self.assertEqual(a["modes"][0]["freq_hz"], b["modes"][0]["freq_hz"])
        self.assertTrue(a["composite"]["symmetric"])

    def test_one_sided_polygon_reports_the_reduction(self):
        p = VP.plate_modes_polygon(self.SQ, H_MM, material="fr4",
                                   copper_um=ONE_OZ,
                                   copper_um_bottom=0)["value"]
        a = p["composite"]["asymmetric"]
        self.assertIsNotNone(a)
        self.assertGreater(a["b11_n"], 0.0)
        self.assertLess(a["d11_reduced_n_m"], a["d11_before_reduction_n_m"])

    def test_copper_free_value_did_not_move(self):
        """Locked numbers, not theory: these are what the copper-free path read in
        0.0.56, kept here so the copper work cannot drift the old answers."""
        p = VP.plate_modes_polygon(self.SQ, H_MM, material="fr4")["value"]
        self.assertEqual(p["modes"][0]["freq_hz"], 4588.03)
        c = VP.plate_modes_polygon(self.SQ, H_MM, material="fr4",
                                   mount="clamped")["value"]
        self.assertEqual(c["modes"][0]["freq_hz"], 8546.05)


class TestCopperIsRefusedWhenItIsNotACoating(unittest.TestCase):
    def test_negative(self):
        r = V.plate_modes(A_MM, B_MM, H_MM, material="fr4", copper_um=-1)
        self.assertEqual(r["reason_code"], "ARG_RANGE")
        r = VP.plate_modes_polygon(TestPolygonCopper.SQ, H_MM, material="fr4",
                                   copper_um=-1)
        self.assertEqual(r["reason_code"], "ARG_RANGE")

    def test_a_copper_plate_is_not_a_pcb(self):
        r = V.plate_modes(A_MM, B_MM, H_MM, material="fr4", copper_um=800)
        self.assertEqual(r["reason_code"], "OUT_OF_VALIDITY")
        r = VP.plate_modes_polygon(TestPolygonCopper.SQ, H_MM, material="fr4",
                                   copper_um=800)
        self.assertEqual(r["reason_code"], "OUT_OF_VALIDITY")


if __name__ == "__main__":
    unittest.main()
