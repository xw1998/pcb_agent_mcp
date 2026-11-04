"""The latent heat of fusion, added to Onderdonk - and the four ways it could be wrong.

Onderdonk's relation stops when the copper REACHES 1083 C. Copper then needs another
208.7 kJ/kg before it changes phase, and because the last stretch to the melt point is
travelled at rho(1083)/rho(25) = 5.08x the cold power, that latent stage is cheap in
TIME and expensive in ENERGY. The fraction it adds to I^2t is

    beta = L_f / (c (234 + Tm) ln(ratio))     ratio = (Tm - Ta)/(234 + Ta) + 1

and the current moves by its square root, because I^2t is what the energy balance
fixes. The tests are grouped the way the question is:

* EXACT algebra. I^2t scales by exactly (1 + beta) and the current by exactly its
  square root; beta is linear in L_f and inverse in c; beta -> 0 as L_f -> 0. These
  must hold to floating point, and they hold for ANY geometry, which is the point of
  the derivation: area, copper weight and rho20 all cancel.
* MEASURED anchors. beta at five ambients, frozen as literals with an INDEPENDENT
  numeric integration of the same ODE beside them (RK4 on dT/dt = k(234+T), then the
  latent stage at rho(Tm)); they agree to 5e-12, so the closed form is not merely
  self-consistent.
* NEGATIVE CONTROLS, three of them, because each is a real way to get this wrong:
  (1) L_f/(c dT) = 0.512 is the naive thermal-mass answer and is 2.02x too big - the
  test asserts the model does NOT return it and that the ratio is exactly
  ratio/ln(ratio); (2) using rho(25) instead of rho(1083) for the latent stage is the
  same mistake in energy terms; (3) the "33" consistency check only holds at 25 C, so
  running it at another ambient must FAIL - otherwise the check has no discriminating
  power and would pass on a wrong constant.
* THE DEFAULT IS UNTOUCHED. Every fusing number this module printed before 0.0.86 must
  come back bit-identical with melting_model left alone, because a published fusing
  table is reproduced from that path.
"""
import io
import math
import os
import sys
import unittest

sys.path.insert(0, os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "src"))

from pcb_agent_mcp.calc import current  # noqa: E402
from pcb_agent_mcp import tools  # noqa: E402

CP = current.COPPER_CP_J_PER_KGK
LF = current.COPPER_LATENT_HEAT_J_PER_KG
RHOM = current.COPPER_DENSITY_KG_M3
RHO20 = current.COPPER_RHO20_OHM_M
TM = current.ONDERDONK_MELT_C
REF = current.ONDERDONK_REF_C

# beta frozen at five ambients (measured 2026-11-02, probe _scratch/probe_latent086d.py)
BETA = {
    0.0: 0.238223631682,
    25.0: 0.253092718970,
    50.0: 0.268294437784,
    85.0: 0.290284624242,
    100.0: 0.300006804551,
}

# the fusing numbers this module printed before the parameter existed
SENSIBLE_FROZEN = {
    # (width_mm, oz, t_s): (sensible_a, latent_a)
    (0.5, 1.0, 1.0): (5.024, 5.624),
    (1.0, 1.0, 1.0): (10.047, 11.247),
    (0.2, 1.0, 0.01): (20.095, 22.495),
    (2.0, 2.0, 10.0): (12.709, 14.227),
}


def _v(r):
    assert r["status"] == "ok", r
    return r["value"]


class TestBetaAlgebra(unittest.TestCase):
    def test_frozen_at_five_ambients(self):
        for ta, want in BETA.items():
            self.assertAlmostEqual(current.latent_heat_share_of_i2t(ta), want, places=11,
                                   msg="ambient %s" % ta)

    def test_beta_is_not_the_naive_thermal_mass_answer(self):
        # L_f/(c*dT) = 542/1083 = 0.5006 ... and 0.512 if dT is taken as 1058
        naive = LF / (CP * (TM - 25.0))
        beta = current.latent_heat_share_of_i2t(25.0)
        self.assertGreater(naive / beta, 2.0)
        self.assertLess(naive / beta, 2.05)
        # and the exact ratio is (234+Tm)*ln(ratio)/(Tm-Ta): the naive form drops the
        # 234+Tm scale of the log and the rho(Tm)/rho(Ta) speed-up at the same time
        ratio = (TM - 25.0) / (REF + 25.0) + 1.0
        self.assertAlmostEqual(naive / beta,
                               (REF + TM) * math.log(ratio) / (TM - 25.0), places=12)

    def test_linear_in_Lf_and_inverse_in_cp(self):
        ta = 40.0
        base = current.latent_heat_share_of_i2t(ta)
        saved_l, saved_c = current.COPPER_LATENT_HEAT_J_PER_KG, current.COPPER_CP_J_PER_KGK
        try:
            current.COPPER_LATENT_HEAT_J_PER_KG = saved_l * 2.0
            self.assertAlmostEqual(current.latent_heat_share_of_i2t(ta) / base, 2.0,
                                   places=13)
            current.COPPER_LATENT_HEAT_J_PER_KG = saved_l
            current.COPPER_CP_J_PER_KGK = saved_c * 4.0
            self.assertAlmostEqual(current.latent_heat_share_of_i2t(ta) / base, 0.25,
                                   places=13)
            current.COPPER_LATENT_HEAT_J_PER_KG = 0.0
            current.COPPER_CP_J_PER_KGK = saved_c
            self.assertEqual(current.latent_heat_share_of_i2t(ta), 0.0)
        finally:
            current.COPPER_LATENT_HEAT_J_PER_KG = saved_l
            current.COPPER_CP_J_PER_KGK = saved_c

    def test_rises_monotonically_with_ambient(self):
        vals = [current.latent_heat_share_of_i2t(ta) for ta in (0, 25, 50, 85, 100)]
        for lo, hi in zip(vals, vals[1:]):
            self.assertLess(lo, hi)

    def test_hot_ambient_makes_the_latent_part_bigger(self):
        # counter-intuitive but right: less sensible heat is left to absorb, so the
        # latent stage is a LARGER fraction - 0.238 at 0 C, 0.300 at 100 C
        self.assertAlmostEqual(current.latent_heat_share_of_i2t(0.0) /
                               current.latent_heat_share_of_i2t(25.0), 0.9412, delta=1e-4)
        self.assertAlmostEqual(current.latent_heat_share_of_i2t(100.0) /
                               current.latent_heat_share_of_i2t(25.0), 1.1854, delta=1e-4)


class TestI2tScaling(unittest.TestCase):
    def test_i2t_ratio_is_exactly_one_plus_beta(self):
        for ta in sorted(BETA):
            a = current.fusing_current(0.5, t_ambient_c=ta)
            b = current.fusing_current(0.5, t_ambient_c=ta,
                                       melting_model="sensible_plus_latent")
            ratio = (b["fusing_current_with_latent_a"] /
                     b["fusing_current_sensible_only_a"]) ** 2
            # the reported currents are rounded to 1e-6 A, so the ratio carries ~1e-7
            self.assertAlmostEqual(ratio, 1.0 + current.latent_heat_share_of_i2t(ta),
                                   places=6)

    def test_current_ratio_is_the_square_root(self):
        b = current.fusing_current(0.5)
        ratio = b["fusing_current_with_latent_a"] / b["fusing_current_sensible_only_a"]
        self.assertAlmostEqual(ratio,
                               math.sqrt(1.0 + current.latent_heat_share_of_i2t(25.0)),
                               places=6)
        self.assertAlmostEqual(ratio, 1.119416, places=6)

    def test_geometry_does_not_enter_beta(self):
        for w, oz, t in SENSIBLE_FROZEN:
            r = current.fusing_current(w, t_s=t, copper_oz=oz)
            self.assertAlmostEqual(r["latent_share_of_i2t"],
                                   current.latent_heat_share_of_i2t(25.0), places=6,
                                   msg="%s/%s/%s" % (w, oz, t))


class TestDefaultIsUntouched(unittest.TestCase):
    def test_frozen_pairs(self):
        for (w, oz, t), (sens, lat) in SENSIBLE_FROZEN.items():
            r = current.fusing_current(w, t_s=t, copper_oz=oz)
            self.assertEqual(r["value"], sens, msg="%s/%s/%s" % (w, oz, t))
            r2 = current.fusing_current(w, t_s=t, copper_oz=oz,
                                        melting_model="sensible_plus_latent")
            self.assertEqual(r2["value"], lat, msg="%s/%s/%s" % (w, oz, t))

    def test_default_model_name_and_extra_keys(self):
        r = current.fusing_current(0.5)
        self.assertEqual(r["melting_model"], "sensible")
        self.assertEqual(r["model"], "onderdonk_adiabatic_fusing")
        self.assertEqual(r["fusing_current_sensible_only_a"], 5.023718)
        self.assertEqual(r["fusing_current_with_latent_a"], 5.623632)
        self.assertEqual(r["latent_share_of_i2t"], 0.253093)
        r2 = current.fusing_current(0.5, melting_model="sensible_plus_latent")
        self.assertEqual(r2["model"], "onderdonk_adiabatic_fusing_plus_latent_heat")
        self.assertEqual(r2["value"], 5.624)

    def test_none_means_sensible(self):
        self.assertEqual(current.fusing_current(0.5, melting_model=None)["value"],
                         current.fusing_current(0.5)["value"])

    def test_bad_model_is_refused_with_the_number_in_the_hint(self):
        for bad in ("latent", "melt_through", "", "sensible+latent"):
            r = current.fusing_current(0.5, melting_model=bad)
            self.assertEqual(r["status"], "error")
            self.assertEqual(r["reason_code"], "ARG_INVALID")
            self.assertIn("11.9 %", r["hint"])
            self.assertIn("25.3 %", r["hint"])
        self.assertFalse(any(current.fusing_current(0.5, melting_model=b)["status"] == "ok"
                             for b in ("latent", "melt_through", "", "sensible+latent")))

    def test_texts_quote_the_measured_split(self):
        r = current.fusing_current(0.5)
        self.assertIn("+25.31 %", r["latent_heat_means"])
        self.assertIn("+11.94 %", r["latent_heat_means"])
        self.assertIn("208.7 kJ/kg", r["latent_heat_means"])
        self.assertIn("407 kJ/kg", r["latent_heat_means"])
        self.assertIn("latent heat", r["precision"])
        self.assertIn("sensible_plus_latent", r["assumptions"]["phase_change"])

    def test_assumption_branches_differ(self):
        a = current.fusing_current(0.5)["assumptions"]["phase_change"]
        b = current.fusing_current(0.5,
                                   melting_model="sensible_plus_latent")["assumptions"]["phase_change"]
        self.assertNotEqual(a, b)
        self.assertIn("not modelled", a)
        self.assertIn("melts THROUGH", b)


class TestSteadyRating(unittest.TestCase):
    def test_melt_time_scales_only_when_asked(self):
        a = current.fusing_current(0.5, t_s=1.0, standard="ipc2152")
        b = current.fusing_current(0.5, t_s=1.0, standard="ipc2152",
                                   melting_model="sensible_plus_latent")
        ta = a["steady_rating"]["adiabatic_melt_time_at_capacity_s"]
        tb = b["steady_rating"]["adiabatic_melt_time_at_capacity_s"]
        # 12.803135 s is what this module printed before 0.0.86 - it must not move
        self.assertEqual(ta, 12.803135)
        self.assertAlmostEqual(tb / ta,
                               1.0 + current.latent_heat_share_of_i2t(25.0), places=6)
        self.assertEqual(tb, 16.043515)

    def test_fuse_over_capacity_moves_by_the_current(self):
        a = current.fusing_current(0.5, t_s=1.0, standard="ipc2152")
        b = current.fusing_current(0.5, t_s=1.0, standard="ipc2152",
                                   melting_model="sensible_plus_latent")
        self.assertEqual(a["steady_rating"]["fuse_over_capacity"], 3.578)
        self.assertEqual(b["steady_rating"]["fuse_over_capacity"], 4.005)
        self.assertEqual(a["steady_rating"]["capacity_a"],
                         b["steady_rating"]["capacity_a"])

    def test_time_shortfall_is_twenty_percent(self):
        # the other two melt tools still stop at the melt point; say by how much
        for ta, pct in ((0.0, 19.2), (25.0, 20.2), (100.0, 23.1)):
            short = 100.0 * (1.0 - 1.0 / (1.0 + current.latent_heat_share_of_i2t(ta)))
            self.assertAlmostEqual(short, pct, delta=0.05)


class TestIndependentIntegration(unittest.TestCase):
    """RK4 on the ODE the closed form came from - no eigenfunctions, no algebra reuse."""

    @staticmethod
    def _beta_numeric(ta, n=60000):
        # dT/dt = k(234+T); the latent stage runs at rho(Tm), so dL/dt = k(234+Tm)
        # with L in kelvin-equivalents (c*rho_m*dL/dt = I^2*rho(Tm)).
        k = 1.0 / (REF + ta)          # arbitrary scale: cancels in the ratio
        dt = math.log((REF + TM) / (REF + ta)) / k / n
        T, t = ta, 0.0
        for _ in range(n * 3):
            k1 = k * (REF + T)
            k2 = k * (REF + T + 0.5 * dt * k1)
            k3 = k * (REF + T + 0.5 * dt * k2)
            k4 = k * (REF + T + dt * k3)
            step = dt / 6.0 * (k1 + 2 * k2 + 2 * k3 + k4)
            if T + step >= TM:
                t += dt * (TM - T) / step
                break
            T += step
            t += dt
        return ((LF / CP) / (k * (REF + TM))) / t

    def test_closed_form_matches_the_integral(self):
        for ta in (0.0, 25.0, 50.0, 100.0):
            num = self._beta_numeric(ta)
            closed = current.latent_heat_share_of_i2t(ta)
            self.assertLess(abs(num - closed) / closed, 1e-8, msg="ambient %s" % ta)

    def test_the_initial_guess_can_be_wrong(self):
        # negative control for the integrator itself: using rho(Ta) for the latent
        # stage (the naive answer) is 5.08x off, so the check above is not vacuous
        ta = 25.0
        wrong = ((LF / CP) / (1.0 / (REF + ta) * (REF + ta))) / (
            math.log((REF + TM) / (REF + ta)) / (1.0 / (REF + ta)))
        self.assertGreater(wrong / current.latent_heat_share_of_i2t(ta), 5.0)


class TestThirtyThreeConsistency(unittest.TestCase):
    """Onderdonk's 33 and copper's handbook constants have to agree, or one of them is
    not the copper they claim to be. The identity is defined at 25 C only."""

    LHS = CP * RHOM * (REF + 25.0) / RHO20
    RHS = 1.0 / (current.M2_PER_CMIL ** 2 * 33.0 * math.log(10.0))

    def test_agrees_within_two_percent(self):
        rel = (self.LHS - self.RHS) / self.RHS
        self.assertLess(abs(rel), 0.02)
        self.assertAlmostEqual(rel, 0.0134, places=4)

    def test_backsolving_rho20_closes_it(self):
        # 1.7431e-8 ohm*m is the rho20 that makes 385 / 208700 / 1083 exactly
        # consistent with Onderdonk's 33 - which is why 385 is used and not 400
        rho = CP * RHOM * (REF + 25.0) * current.M2_PER_CMIL ** 2 * 33.0 * math.log(10.0)
        self.assertAlmostEqual(rho, 1.7431e-8, places=12)
        self.assertEqual(RHO20, 1.72e-8)

    def test_the_check_discriminates(self):
        # negative control: at any other ambient the identity does NOT hold, so
        # "it passes" carries information. If this ever passes, the check is dead.
        for ta in (0.0, 50.0, 100.0):
            rel = (CP * RHOM * (REF + ta) / RHO20 - self.RHS) / self.RHS
            self.assertGreater(abs(rel), 0.05, msg="ambient %s" % ta)


class TestToolLayer(unittest.TestCase):
    def test_schema_carries_the_enum(self):
        prop = tools.TOOLS["fusing_current"]["inputSchema"]["properties"]["melting_model"]
        self.assertEqual(prop["enum"], ["sensible", "sensible_plus_latent"])

    def test_handler_passes_it_through(self):
        r = tools.call_tool("fusing_current", {"width_mm": 0.5})
        self.assertEqual(r["status"], "ok")
        self.assertEqual(r["value"], 5.024)
        self.assertEqual(r["melting_model"], "sensible")
        self.assertFalse(r.get("unread_arguments"))
        r2 = tools.call_tool("fusing_current", {"width_mm": 0.5,
                                                "melting_model": "sensible_plus_latent"})
        self.assertEqual(r2["status"], "ok")
        self.assertEqual(r2["value"], 5.624)
        self.assertEqual(r2["melting_model"], "sensible_plus_latent")
        self.assertFalse(r2.get("unread_arguments"))

    def test_bad_enum_reaches_the_caller(self):
        r = tools.call_tool("fusing_current", {"width_mm": 0.5, "melting_model": "latent"})
        self.assertEqual(r["status"], "error")
        self.assertEqual(r["reason_code"], "ARG_INVALID")

    def test_the_taper_has_it_too_since_088(self):
        # 0.0.86 gave fusing_current the switch, 0.0.87 the pulse train (closed form,
        # one lumped temperature), 0.0.88 the taper (per-node enthalpy state, exact
        # phase boundary). All three now carry it - and the taper's own numbers live
        # in test_taper_latent.
        props = tools.TOOLS["fusing_current_taper"]["inputSchema"]["properties"]
        self.assertIn("melting_model", props)
        self.assertEqual(props["melting_model"]["enum"],
                         ["sensible", "sensible_plus_latent"])
        self.assertIn("melting_model",
                      tools.TOOLS["repetitive_pulse_rating"]["inputSchema"]["properties"])

    def test_tool_count_did_not_change(self):
        self.assertEqual(len(tools.TOOLS), 192)

    def test_gap_is_registered(self):
        # 0.0.88 closed the "melting, by name" gap on all three tools, so that entry
        # was replaced - not deleted - by the one that is still open: what the metal
        # does after it is liquid.
        cap = tools.call_tool("calc_capabilities", {})
        text = "\n".join(cap["not_implemented_yet"])
        self.assertNotIn("Melting, by name", text)
        self.assertIn("Melting, past the melt-through", text)


if __name__ == "__main__":
    unittest.main(verbosity=2)
