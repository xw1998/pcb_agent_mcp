"""Magnetics beyond the CCM design point: DCM steady state, core loss, DCR.

Every expected number is hand arithmetic on the fixture values, written out in the
comment next to the assertion, plus three structural invariants that a formula
error cannot satisfy by accident:

  * at the CCM boundary the DCM duty must EQUAL the CCM duty (needs both entry
    points to agree about the same L and load),
  * the inductor's average current computed from the LOAD and computed from the
    WAVEFORM must agree (they share no input),
  * the same Steinmetz fit written in three unit conventions must give one W/m^3.

The refusals matter as much: a load above the CCM boundary must be told it is in
CCM instead of being handed a DCM duty, and a Steinmetz triple without a unit
convention must be rejected rather than assumed.
"""
import math
import os
import sys
import unittest

sys.path.insert(0, os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "src"))

from pcb_agent_mcp.calc import magnetics as M   # noqa: E402
from pcb_agent_mcp import tools                 # noqa: E402

FS = 500e3

class TestBuckBoostInductorAverage(unittest.TestCase):
    """0.0.18 correction: the winding carries Iin during D*T and Iout during (1-D)*T."""

    def test_average_is_iout_over_one_minus_d(self):
        # 5 -> 5 V, 1 A, r 0.3: D = 5/(5+5) = 0.5, I_L,avg = 1/(1-0.5) = 2 A,
        # dI = 0.3*2 = 0.6 A, L = 5*0.5/(0.6*500e3) = 8.333 uH
        v = M.inductor_ccm("buck_boost", 5.0, 5.0, FS, 0.3, iout_a=1.0)["value"]
        self.assertAlmostEqual(v["duty"], 0.5, places=6)
        self.assertAlmostEqual(v["inductor_avg_a"], 2.0, places=6)
        self.assertAlmostEqual(v["ripple_current_a"], 0.6, places=6)
        self.assertAlmostEqual(v["inductor_uh"], 8.333, places=3)

    def test_the_boundary_load_follows_the_winding_current(self):
        # valley = 0 at Iout = (1-D)*dI/2 = 0.5*0.3 = 0.15 A
        v = M.inductor_ccm("buck_boost", 5.0, 5.0, FS, 0.3, iout_a=1.0)["value"]
        self.assertAlmostEqual(v["ccm_min_output_a"], 0.15, places=6)

class TestDcm(unittest.TestCase):
    def _l(self, top, vin, vout):
        # the L that inductor_ccm designs for 1 A at r = 0.3
        return M.inductor_ccm(top, vin, vout, FS, 0.3, iout_a=1.0)["value"]

    def test_boost_duty_by_hand(self):
        # M = 2.4, R = 12/0.1 = 120, K = 2*8.102e-6*500e3/120 = 0.0675167
        # D = sqrt(K*M*(M-1)) = sqrt(0.0675167*2.4*1.4) = 0.47629
        l = self._l("boost", 5.0, 12.0)["inductor_h"]
        r = M.duty_dcm("boost", 5.0, 12.0, FS, l, iout_a=0.1)
        self.assertEqual(r["status"], "ok")
        v = r["value"]
        k = 2.0 * l * FS / 120.0
        self.assertAlmostEqual(v["K"], k, places=6)
        self.assertAlmostEqual(v["duty"], math.sqrt(k * 2.4 * 1.4), places=5)
        self.assertAlmostEqual(v["duty_diode"], v["duty"] / 1.4, places=5)

    def test_buck_duty_by_hand(self):
        # M = 5/12 = 0.41667, R = 50, K = 2*19.444e-6*500e3/50 = 0.38889
        # D = M*sqrt(K/(1-M)) = 0.41667*sqrt(0.66667) = 0.34021
        v = M.duty_dcm("buck", 12.0, 5.0, FS, 19.444e-6, iout_a=0.1)["value"]
        k = 2.0 * 19.444e-6 * FS / 50.0
        self.assertAlmostEqual(v["duty"], 0.4166667 * math.sqrt(k / (1 - 0.4166667)),
                               places=5)
        self.assertAlmostEqual(v["duty"], 0.34021, places=4)

    def test_buck_boost_duty_by_hand(self):
        # M = 1, R = 50, K = 2*8.333e-6*500e3/50 = 0.16667 -> D = sqrt(K) = 0.40825
        v = M.duty_dcm("buck_boost", 5.0, 5.0, FS, 8.333e-6, iout_a=0.1)["value"]
        self.assertAlmostEqual(v["duty"], math.sqrt(2.0 * 8.333e-6 * FS / 50.0),
                               places=5)

    def test_at_the_boundary_it_agrees_with_ccm(self):
        # approaching the boundary from below, the DCM duty must approach the CCM
        # duty - the two entry points have to be talking about the same converter
        for top, vin, vout in (("boost", 5.0, 12.0), ("buck", 12.0, 5.0),
                               ("buck_boost", 5.0, 5.0)):
            ccm = M.inductor_ccm(top, vin, vout, FS, 0.3, iout_a=1.0)["value"]
            i_b = ccm["ccm_min_output_a"]
            r = M.duty_dcm(top, vin, vout, FS, ccm["inductor_h"], iout_a=i_b * 0.999)
            self.assertEqual(r["status"], "ok", (top, r))
            self.assertAlmostEqual(r["value"]["duty"], ccm["duty"], delta=2e-3,
                                   msg="%s: DCM %.5f vs CCM %.5f"
                                       % (top, r["value"]["duty"], ccm["duty"]))
            self.assertAlmostEqual(r["value"]["load_at_ccm_boundary_a"], i_b,
                                   delta=1e-3)
            self.assertAlmostEqual(r["value"]["duty_total"], 1.0, delta=2e-3)

    def test_the_two_ways_of_getting_the_inductor_average_agree(self):
        # by load: Iout/(1-D), Iout, M*Iout  ;  by waveform: Ipk*(D+D2)/2
        for top, vin, vout in (("boost", 5.0, 12.0), ("buck", 12.0, 5.0),
                               ("buck_boost", 5.0, 5.0)):
            l = self._l(top, vin, vout)["inductor_h"]
            v = M.duty_dcm(top, vin, vout, FS, l, iout_a=0.1)["value"]
            self.assertAlmostEqual(v["inductor_avg_implied_by_load_a"],
                                   v["inductor_avg_implied_by_waveform_a"],
                                   delta=1e-3, msg=top)

    def test_three_intervals_fill_the_period(self):
        l = self._l("boost", 5.0, 12.0)["inductor_h"]
        for iout in (0.02, 0.05, 0.1, 0.14):
            v = M.duty_dcm("boost", 5.0, 12.0, FS, l, iout_a=iout)["value"]
            self.assertLess(v["duty_total"], 1.0)
            self.assertGreater(v["duty_idle"], 0.0)
            self.assertAlmostEqual(v["duty"] + v["duty_diode"] + v["duty_idle"], 1.0,
                                   places=5)

    def test_rms_is_the_definition(self):
        l = self._l("buck", 12.0, 5.0)["inductor_h"]
        v = M.duty_dcm("buck", 12.0, 5.0, FS, l, iout_a=0.1)["value"]
        self.assertAlmostEqual(v["inductor_rms_a"],
                               v["inductor_peak_a"] * math.sqrt(
                                   (v["duty"] + v["duty_diode"]) / 3.0), delta=1e-3)

    def test_ccm_load_is_refused_and_told_which_load_would_work(self):
        l = self._l("boost", 5.0, 12.0)["inductor_h"]
        r = M.duty_dcm("boost", 5.0, 12.0, FS, l, iout_a=1.0)
        self.assertEqual(r["status"], "error")
        self.assertEqual(r["reason_code"], "MODE_IS_CCM")
        self.assertAlmostEqual(r["load_at_ccm_boundary_a"], 0.15, places=6)
        self.assertGreater(r["K"], r["K_crit"])
        self.assertIn("0.15", r["hint"])

    def test_smaller_l_pushes_the_boundary_up(self):
        # smaller inductance => compliance range shrinks => the converter falls out of
        # CCM at a HIGHER load (boundary load scales as 1/L). At 0.2 A the big
        # inductor is still CCM while the half one is already DCM.
        full = M.duty_dcm("boost", 5.0, 12.0, FS, 8.102e-6, iout_a=0.2)
        half = M.duty_dcm("boost", 5.0, 12.0, FS, 4.051e-6, iout_a=0.2)
        self.assertEqual(full["reason_code"], "MODE_IS_CCM")
        self.assertEqual(half["status"], "ok")
        self.assertAlmostEqual(full["load_at_ccm_boundary_a"], 0.15, places=4)
        self.assertAlmostEqual(half["value"]["load_at_ccm_boundary_a"], 0.3,
                               places=4)

    def test_load_is_required_and_exclusive(self):
        self.assertEqual(M.duty_dcm("boost", 5.0, 12.0, FS, 8.1e-6)["reason_code"],
                         "ARG_MISSING")
        self.assertEqual(M.duty_dcm("boost", 5.0, 12.0, FS, 8.1e-6, iout_a=0.1,
                                    pout_w=1.2)["reason_code"], "ARG_INVALID")
        self.assertEqual(M.duty_dcm("flyback", 5.0, 12.0, FS, 8.1e-6,
                                    iout_a=0.1)["reason_code"], "ARG_INVALID")
        self.assertEqual(M.duty_dcm("boost", 5.0, 4.0, FS, 8.1e-6,
                                    iout_a=0.1)["reason_code"], "ARG_RANGE")
        self.assertEqual(M.duty_dcm("boost", 5.0, 12.0, FS, 0, iout_a=0.1)
                         ["reason_code"], "ARG_RANGE")

class TestCoreLoss(unittest.TestCase):
    # a made-up but self-consistent triple: none of these numbers is a real material,
    # which is the point - the module must not own a material table
    K_SI, A, B = 1e-3, 1.3, 2.5

    def test_si_arithmetic_by_hand(self):
        # 1e-3 * (1e5)^1.3 * (0.1)^2.5 = 1e-3 * 10^6.5 * 10^-2.5 = 10 W/m^3
        v = M.core_loss_steinmetz(1e5, 0.1, self.K_SI, self.A, self.B)["value"]
        self.assertAlmostEqual(v["P_v_w_m3"] / 10.0, 1.0, delta=1e-6)
        self.assertAlmostEqual(v["P_v_mw_cm3"] / 0.01, 1.0, delta=1e-6)
        self.assertEqual(v["units_used"], "si")

    def test_the_three_conventions_are_one_fit(self):
        # the same physical fit written three ways. kHz/mT carries 1e3 * 10^(3(b-a)),
        # kHz/Gauss carries 1e3 * 10^(4b-3a) relative to SI - both spelled out here.
        k_mt = self.K_SI / (1e3 * 10 ** (3 * (self.B - self.A)))
        k_g = self.K_SI / (1e3 * 10 ** (4 * self.B - 3 * self.A))
        a = M.core_loss_steinmetz(1e5, 0.1, self.K_SI, self.A, self.B,
                                  units="si")["value"]["P_v_w_m3"]
        b = M.core_loss_steinmetz(1e5, 0.1, k_mt, self.A, self.B,
                                  units="khz_mt")["value"]["P_v_w_m3"]
        c = M.core_loss_steinmetz(1e5, 0.1, k_g, self.A, self.B,
                                  units="khz_gauss")["value"]["P_v_w_m3"]
        self.assertAlmostEqual(b / a, 1.0, delta=1e-9)
        self.assertAlmostEqual(c / a, 1.0, delta=1e-9)

    def test_core_volume_turns_density_into_watts(self):
        # 10 W/m^3 over 1000 mm^3 = 10 * 1e-6 m^3 = 1e-5 W
        v = M.core_loss_steinmetz(1e5, 0.1, self.K_SI, self.A, self.B, ve_mm3=1000.0)
        self.assertAlmostEqual(v["value"]["core_loss_w"], 1e-5, delta=1e-12)

    def test_the_units_are_printed_both_ways(self):
        v = M.core_loss_steinmetz(1e5, 0.1, self.K_SI, self.A, self.B)["value"]
        self.assertAlmostEqual(v["P_v_w_m3"] / v["P_v_mw_cm3"], 1e3, delta=1e-6)
        self.assertIn("W/m^3", v["k_means"])

    def test_refusals(self):
        # a unit convention is not optional: the same k means things 1e12 apart
        self.assertEqual(M.core_loss_steinmetz(1e5, 0.1, 1e-3, 1.3, 2.5,
                                               units="furlongs")["reason_code"],
                         "ARG_INVALID")
        # no AC flux, no Steinmetz loss
        self.assertEqual(M.core_loss_steinmetz(1e5, 0.0, 1e-3, 1.3, 2.5)
                         ["reason_code"], "ARG_RANGE")
        self.assertEqual(M.core_loss_steinmetz(0, 0.1, 1e-3, 1.3, 2.5)
                         ["reason_code"], "ARG_RANGE")
        self.assertEqual(M.core_loss_steinmetz(1e5, 0.1, 0.0, 1.3, 2.5)
                         ["reason_code"], "ARG_RANGE")
        self.assertEqual(M.core_loss_steinmetz(1e5, 0.1, 1e-3, -1.3, 2.5)
                         ["reason_code"], "ARG_RANGE")
        self.assertEqual(M.core_loss_steinmetz("x", 0.1, 1e-3, 1.3, 2.5)
                         ["reason_code"], "ARG_INVALID")

    def test_it_says_what_it_is_not(self):
        v = M.core_loss_steinmetz(1e5, 0.1, self.K_SI, self.A, self.B)["value"]
        blob = " ".join(v["not_modelled"])
        self.assertIn("DC bias", blob)
        self.assertIn("sine", blob)
        self.assertIn("WINDING", blob)

class TestDcr(unittest.TestCase):
    def test_winding_loss_is_i_rms_squared_times_dcr(self):
        v = M.inductor_ccm("boost", 5.0, 12.0, FS, 0.3, iout_a=1.0,
                           dcr_ohm=0.05)["value"]
        w = v["winding"]
        self.assertAlmostEqual(w["winding_loss_w"],
                               v["inductor_rms_a"] ** 2 * 0.05, delta=2e-3)
        # 0.29 W against 12 W of output
        self.assertAlmostEqual(w["winding_loss_over_pout_pct"], 2.42, delta=0.05)
        self.assertAlmostEqual(w["efficiency_ceiling_if_dcr_were_the_only_loss"],
                               12.0 / 12.2902, delta=2e-3)

    def test_no_dcr_no_winding_block(self):
        v = M.inductor_ccm("boost", 5.0, 12.0, FS, 0.3, iout_a=1.0)["value"]
        self.assertIsNone(v["winding"])

    def test_double_counting_with_efficiency_is_named(self):
        v = M.inductor_ccm("boost", 5.0, 12.0, FS, 0.3, iout_a=1.0, efficiency=0.9,
                           dcr_ohm=0.05)["value"]
        self.assertIn("double_counting", v["winding"])

    def test_dcr_is_dc_only_and_says_so(self):
        v = M.inductor_ccm("buck", 12.0, 5.0, FS, 0.3, iout_a=1.0, dcr_ohm=0.01)
        self.assertIn("R_AC", v["value"]["winding"]["dc_only"])
        self.assertEqual(M.inductor_ccm("buck", 12.0, 5.0, FS, 0.3, iout_a=1.0,
                                        dcr_ohm=-1)["reason_code"], "ARG_RANGE")

class TestTools(unittest.TestCase):
    def test_registered_and_listed(self):
        c = tools.call_tool("calc_capabilities", {})
        for name in ("magnetics_inductor", "magnetics_dcm", "magnetics_core_loss"):
            self.assertIn(name, tools.TOOLS)
            self.assertIn(name, c["available"])

    def test_dcm_tool_refuses_a_ccm_load(self):
        r = tools.call_tool("magnetics_dcm", {"topology": "boost", "vin_v": 5,
                                             "vout_v": 12, "f_sw_hz": FS,
                                             "l_h": 8.102e-6, "iout_a": 1.0})
        self.assertEqual(r["reason_code"], "MODE_IS_CCM")

    def test_core_loss_tool_flags_a_missing_unit_convention(self):
        r = tools.call_tool("magnetics_core_loss", {"f_hz": 1e5, "b_peak_t": 0.1,
                                                   "k": 1e-3, "alpha": 1.3,
                                                   "beta": 2.5, "units": "nope"})
        self.assertEqual(r["reason_code"], "ARG_INVALID")
        r = tools.call_tool("magnetics_core_loss", {"f_hz": 1e5, "b_peak_t": 0.1,
                                                   "k": 1e-3, "alpha": 1.3,
                                                   "beta": 2.5})
        self.assertEqual(r["status"], "ok")   # si is the documented default

    def test_inductor_tool_accepts_dcr(self):
        r = tools.call_tool("magnetics_inductor",
                            {"topology": "boost", "vin_v": 5, "vout_v": 12,
                             "f_sw_hz": FS, "iout_a": 1.0, "dcr_ohm": 0.05})
        self.assertAlmostEqual(r["value"]["winding"]["winding_loss_w"], 0.2902,
                               delta=1e-3)

    def test_the_old_no_dcm_claim_is_gone(self):
        blob = " ".join(tools.call_tool("calc_capabilities", {})["not_implemented_yet"])
        self.assertNotIn("no DCM steady state", blob)
        self.assertIn("no DC-bias Steinmetz", blob)

if __name__ == "__main__":
    unittest.main()
