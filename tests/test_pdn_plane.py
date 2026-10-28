"""Plane-pair modes inside the PDN: peaks, coupling nulls, and one real anchor.

The anchor that makes these numbers auditable: the (0,0) term of the PMC Green's
function IS the parallel-plate capacitor, so the whole modal sum, evaluated far
below the first resonance, must come back to 1/(j*w*C_plane) with
C_plane = eps0*er*a*b/t. If the normalisation of psi_mn is off by anything, that
sum is off by the same factor - which is exactly how this module caught its own
missing square (the first version was 20x low).

What is deliberately NOT tested as a curve: the modal sum near and above the
first resonance. It is truncation-dependent, so the module reports peaks only
and says so; the test below asserts that the report SAYS so, instead of
inventing a tolerance for a number nobody can defend.
"""
import math
import unittest

from pcb_agent_mcp.calc import cavity as cav
from pcb_agent_mcp.calc import power as pw

W_MM = H_MM = 50.0
ER = 4.4
T_MM = 1.6
TAND = 0.02
C_PLANE = pw.EPS0 * ER * (W_MM * 1e-3) * (H_MM * 1e-3) / (T_MM * 1e-3)


def _peak_closed_form(m, n, x_mm, y_mm, tan_delta=TAND):
    """|Z_peak| for one mode, written out again from the definition."""
    a, b, t = W_MM * 1e-3, H_MM * 1e-3, T_MM * 1e-3
    kmn2 = (math.pi * m / a) ** 2 + (math.pi * n / b) ** 2
    f = pw.C0 / (2.0 * math.sqrt(ER)) * math.sqrt((m / a) ** 2 + (n / b) ** 2)
    cm = 1.0 if m == 0 else math.sqrt(2.0)
    cn = 1.0 if n == 0 else math.sqrt(2.0)
    psi2 = ((cm * cn) ** 2 / (a * b)
            * math.cos(math.pi * m * x_mm / W_MM) ** 2
            * math.cos(math.pi * n * y_mm / H_MM) ** 2)
    return abs(2.0 * math.pi * f * pw.MU0 * t * psi2 * (1.0 / tan_delta) / kmn2), f


def _modal_sum(f_hz, x_mm, y_mm, order=10):
    """The full sum, same constants, for the capacitance anchor."""
    a, b, t = W_MM * 1e-3, H_MM * 1e-3, T_MM * 1e-3
    k = 2.0 * math.pi * f_hz * math.sqrt(pw.EPS0 * ER * pw.MU0)
    k2 = k * k * (1.0 - 1j * TAND)
    tot = 0j
    for m in range(order + 1):
        for n in range(order + 1):
            cm = 1.0 if m == 0 else math.sqrt(2.0)
            cn = 1.0 if n == 0 else math.sqrt(2.0)
            kmn2 = (math.pi * m / a) ** 2 + (math.pi * n / b) ** 2
            psi2 = ((cm * cn) ** 2 / (a * b)
                    * math.cos(math.pi * m * x_mm / W_MM) ** 2
                    * math.cos(math.pi * n * y_mm / H_MM) ** 2)
            tot += psi2 / (kmn2 - k2)
    return abs(2.0 * math.pi * f_hz * pw.MU0 * t * tot)


def _call(**kw):
    args = dict(width_mm=W_MM, height_mm=H_MM, er=ER, thickness_mm=T_MM,
                tan_delta=TAND)
    args.update(kw)
    return pw.pdn_plane_modes(args["width_mm"], args["height_mm"], args["er"],
                              args["thickness_mm"], args["tan_delta"],
                              probe_mm=args.get("probe_mm"),
                              port_mm=args.get("port_mm"),
                              band_hz=args.get("band_hz"),
                              max_order=args.get("max_order", 6))


class TestTheCapacitanceAnchor(unittest.TestCase):

    def test_the_modal_sum_reproduces_the_plane_capacitor(self):
        # 1 MHz is 3 orders below the first mode (1.43 GHz)
        f = 1e6
        z_cap = 1.0 / (2.0 * math.pi * f * C_PLANE)
        for x_mm, y_mm in ((25.0, 25.0), (12.5, 25.0), (5.0, 5.0)):
            got = _modal_sum(f, x_mm, y_mm)
            self.assertAlmostEqual(got / z_cap, 1.0, delta=0.01,
                                   msg="port at (%s,%s)" % (x_mm, y_mm))

    def test_reported_capacitance_is_eps0_er_a_b_over_t(self):
        v = _call()["value"]
        self.assertAlmostEqual(v["plane_capacitance_f"] / C_PLANE, 1.0, delta=1e-6)

    def test_the_uniform_mode_alone_is_that_capacitor(self):
        # (0,0) is the parallel-plate field, so its term must be 1/(j w C) on its
        # own. The loss enters as 1/sqrt(1+1/Q^2) = 0.9998 at Q = 50.
        f = 1e6
        z_cap = 1.0 / (2.0 * math.pi * f * C_PLANE)
        k = 2.0 * math.pi * f * math.sqrt(pw.EPS0 * ER * pw.MU0)
        k2 = k * k * (1.0 - 1j * TAND)
        a, b, t = W_MM * 1e-3, H_MM * 1e-3, T_MM * 1e-3
        psi2 = 1.0 / (a * b)
        one = abs(2.0 * math.pi * f * pw.MU0 * t * psi2 / (0.0 - k2))
        self.assertAlmostEqual(one / z_cap, 1.0, delta=1e-3)


class TestModeFrequencies(unittest.TestCase):

    def test_matches_the_cavity_module_mode_for_mode(self):
        mine = _call(max_order=3)["value"]["modes"]
        theirs = cav.plane_resonance(W_MM, H_MM, ER, m_max=3, n_max=3,
                                     top=64)["value"]
        got = {(m["m"], m["n"]): m["f_hz"] for m in mine}
        for row in theirs:
            key = (row["m"], row["n"])
            if key in got:
                self.assertAlmostEqual(got[key] / row["f_hz"], 1.0, delta=1e-9)

    def test_first_mode_is_c_over_two_root_er_times_one_over_a(self):
        v = _call()["value"]
        want = pw.C0 / (2.0 * math.sqrt(ER)) / (W_MM * 1e-3)
        # 6 significant digits out of the envelope
        self.assertAlmostEqual(v["modes"][0]["f_hz"] / want, 1.0, delta=1e-5)


class TestPeakMagnitude(unittest.TestCase):

    def test_peak_matches_the_closed_form_for_a_coupled_mode(self):
        v = _call(port_mm=[12.5, 25.0])["value"]
        row = [m for m in v["modes"] if (m["m"], m["n"]) == (1, 0)][0]
        want, f = _peak_closed_form(1, 0, 12.5, 25.0)
        self.assertTrue(row["coupled"])
        self.assertAlmostEqual(row["f_hz"] / f, 1.0, delta=1e-5)
        self.assertAlmostEqual(row["z_peak_ohm"] / want, 1.0, delta=1e-5)

    def test_peak_scales_with_q_which_is_one_over_tan_delta(self):
        lo = _call(port_mm=[12.5, 25.0], tan_delta=0.02)["value"]["worst_z_peak_ohm"]
        hi = _call(port_mm=[12.5, 25.0], tan_delta=0.04)["value"]["worst_z_peak_ohm"]
        self.assertAlmostEqual(lo / hi, 2.0, delta=1e-4)
        v = _call(port_mm=[12.5, 25.0])["value"]
        self.assertAlmostEqual(v["q_from_loss_tangent"], 50.0, delta=1e-9)

    def test_peak_scales_with_thickness(self):
        thin = _call(port_mm=[12.5, 25.0], thickness_mm=0.8)["value"]["worst_z_peak_ohm"]
        thick = _call(port_mm=[12.5, 25.0], thickness_mm=1.6)["value"]["worst_z_peak_ohm"]
        self.assertAlmostEqual(thin / thick, 0.5, delta=1e-4)


class TestCouplingNulls(unittest.TestCase):

    def test_the_centre_cannot_excite_the_odd_modes(self):
        v = _call(port_mm=[25.0, 25.0])["value"]
        odd = {(m["m"], m["n"]): m for m in v["modes"]
               if (m["m"] + m["n"]) % 2 == 1}
        self.assertTrue(odd)
        for key, row in odd.items():
            self.assertFalse(row["coupled"], str(key))
            self.assertEqual(row["z_peak_ohm"], 0.0)
            self.assertIn("null", row["why_not"])

    def test_quarter_point_excites_what_the_centre_cannot(self):
        v = _call(port_mm=[12.5, 25.0])["value"]
        row = [m for m in v["modes"] if (m["m"], m["n"]) == (1, 0)][0]
        self.assertTrue(row["coupled"])
        self.assertGreater(row["z_peak_ohm"], 1.0)

    def test_two_different_points_use_the_transfer_impedance(self):
        # the peak is psi(r1)*psi(r2), not psi(r1)^2, so the ratio to the same-
        # point case is the product of the two cosines over cos^2 of one of them:
        # cos(pi/4)*cos(pi/10) / cos(pi/4)^2. NOT "the closer point wins" - see
        # the note at the end of this test.
        v = _call(port_mm=[12.5, 25.0], probe_mm=[5.0, 25.0])["value"]
        row = [m for m in v["modes"] if (m["m"], m["n"]) == (1, 0)][0]
        a, b, t = W_MM * 1e-3, H_MM * 1e-3, T_MM * 1e-3
        f = pw.C0 / (2.0 * math.sqrt(ER)) * math.sqrt((1.0 / a) ** 2)
        kmn2 = (math.pi / a) ** 2
        psi = (2.0 / (a * b)
               * math.cos(math.pi * 12.5 / W_MM) * math.cos(math.pi * 5.0 / W_MM))
        want = abs(2.0 * math.pi * f * pw.MU0 * t * psi * 50.0 / kmn2)
        self.assertAlmostEqual(row["z_peak_ohm"] / want, 1.0, delta=1e-4)
        same = _call(port_mm=[12.5, 25.0])["value"]
        same_row = [m for m in same["modes"] if (m["m"], m["n"]) == (1, 0)][0]
        # peak ratio must follow the cosine PRODUCT at the two points, i.e.
        # cos(pi*12.5/50)*cos(pi*5/50) / cos(pi*12.5/50)^2 = cos(pi/10)/cos(pi/4),
        # which is 1.3450 - i.e. the off-port point is STRONGER here, not weaker.
        # "the point nearest the antinode wins" is the wrong mental model: only
        # cos(m pi x/a) matters, and a/10 sits closer to the antinode at x=0 than
        # a/4 does, while a/4 is quoted against itself, so the ratio is 0.951/0.707.
        ratio = (math.cos(math.pi * 12.5 / W_MM) * math.cos(math.pi * 5.0 / W_MM)
                 / math.cos(math.pi * 12.5 / W_MM) ** 2)
        self.assertAlmostEqual(ratio, 1.34499702, delta=1e-6)
        self.assertAlmostEqual(row["z_peak_ohm"] / same_row["z_peak_ohm"],
                               ratio, delta=1e-4)

    def test_the_worst_mode_respects_the_band(self):
        wide = _call(port_mm=[12.5, 25.0], band_hz=[1e6, 3e9])["value"]
        narrow = _call(port_mm=[12.5, 25.0], band_hz=[2.5e9, 3e9])["value"]
        self.assertTrue(1e6 <= wide["worst_z_peak_hz"] <= 3e9)
        self.assertTrue(2.5e9 <= narrow["worst_z_peak_hz"] <= 3e9)
        # restricting the band cannot invent a bigger peak than the whole sweep
        self.assertLessEqual(narrow["worst_z_peak_ohm"],
                             wide["worst_z_peak_ohm"] + 1e-9)
        for row in wide["modes"]:
            self.assertIn("in_band", row)

    def test_a_band_with_no_mode_reports_no_worst_in_band(self):
        below = _call(port_mm=[12.5, 25.0], band_hz=[1e6, 1e8])["value"]
        self.assertIsNone(below["worst_z_peak_ohm"])
        self.assertIsNotNone(below["worst_z_peak_any_band_ohm"])


class TestRefusals(unittest.TestCase):

    def test_no_loss_tangent_is_no_number(self):
        r = pw.pdn_plane_modes(W_MM, H_MM, ER, T_MM, None)
        self.assertEqual(r["status"], "error")
        self.assertEqual(r["reason_code"], "ARG_REQUIRED")
        self.assertIn("infinite", r["message"])

    def test_zero_and_absurd_loss_tangents_are_refused(self):
        for bad in (0.0, -0.1, 1.0, 5.0):
            r = pw.pdn_plane_modes(W_MM, H_MM, ER, T_MM, bad)
            self.assertEqual(r["reason_code"], "ARG_RANGE", str(bad))

    def test_port_outside_the_plane_is_refused(self):
        r = _call(port_mm=[10.0, 60.0])
        self.assertEqual(r["reason_code"], "ARG_RANGE")
        self.assertIn("port_mm", r["message"])

    def test_bad_max_order_is_refused(self):
        r = _call(max_order=0)
        self.assertEqual(r["reason_code"], "ARG_RANGE")

    def test_missing_geometry_is_refused(self):
        r = pw.pdn_plane_modes(None, H_MM, ER, T_MM, TAND)
        self.assertEqual(r["reason_code"], "ARG_REQUIRED")


class TestPdnIntegration(unittest.TestCase):

    def _caps(self):
        return [{"c_farad": 10e-6, "esr_ohm": 0.005, "esl_h": 1e-9},
                {"c_farad": 100e-9, "esr_ohm": 0.01, "esl_h": 5e-10}]

    def test_the_plane_diagnostic_does_not_touch_the_lumped_curve(self):
        f = [1e6, 1e7, 1e8, 5e8, 1e9, 2e9, 3e9]
        bare = pw.pdn_impedance(f, self._caps())["value"]
        withp = pw.pdn_impedance(f, self._caps(), plane={
            "width_mm": W_MM, "height_mm": H_MM, "er": ER,
            "thickness_mm": T_MM, "tan_delta": TAND,
            "port_mm": [12.5, 25.0]})["value"]
        self.assertEqual(bare["points"], withp["points"])
        self.assertEqual(bare["min_z_ohm"], withp["min_z_ohm"])
        self.assertIsNone(bare["plane_modes"])
        self.assertIsNotNone(withp["plane_modes"])

    def test_verdict_compares_a_mode_peak_with_the_lumped_minimum(self):
        v = pw.pdn_impedance([1e8, 1e9, 2e9], self._caps(), plane={
            "width_mm": W_MM, "height_mm": H_MM, "er": ER,
            "thickness_mm": T_MM, "tan_delta": TAND,
            "port_mm": [12.5, 25.0]})["value"]
        pm = v["plane_modes"]
        self.assertTrue(pm["modes_in_band"])
        self.assertIn("NOT the PDN", pm["verdict"])
        self.assertIn("does not converge", pm["why_not_a_term"])

    def test_a_band_below_the_first_mode_says_the_curve_stands(self):
        v = pw.pdn_impedance([1e6, 1e7, 1e8], self._caps(), plane={
            "width_mm": W_MM, "height_mm": H_MM, "er": ER,
            "thickness_mm": T_MM, "tan_delta": TAND})["value"]
        self.assertFalse(v["plane_modes"]["modes_in_band"])
        self.assertIn("stands", v["plane_modes"]["verdict"])


class TestEnvelopeHonesty(unittest.TestCase):

    def test_the_not_computed_list_names_the_convergence_problem(self):
        r = _call()
        blob = " ".join(r["not_computed"]).lower()
        self.assertIn("converges", blob)
        self.assertIn("truncation", blob)
        self.assertIn("via barrel", blob)
        for word in ("split planes", "copper loss", "open-edge"):
            self.assertIn(word, blob)

    def test_the_envelope_refuses_to_call_two_rows_a_curve(self):
        r = _call()
        self.assertIn("not a curve", r["precision"])

    def test_the_envelope_says_what_the_loss_is(self):
        r = _call()
        self.assertEqual(r["assumptions"]["loss"],
                         "dielectric only - no copper, no radiation")


class TestToolSurface(unittest.TestCase):

    def test_registered_and_requires_the_loss_tangent(self):
        from pcb_agent_mcp import tools
        self.assertIn("pdn_plane_modes", tools.TOOLS)
        req = tools.TOOLS["pdn_plane_modes"]["inputSchema"]["required"]
        self.assertIn("tan_delta", req)

    def test_pdn_impedance_takes_a_plane_object(self):
        from pcb_agent_mcp import tools
        h = tools.TOOLS["pdn_impedance"]["handler"]
        r = h({"freqs_hz": [1e9, 2e9],
               "caps": [{"c_farad": 1e-6, "esr_ohm": 0.01, "esl_h": 1e-9}],
               "plane": {"width_mm": W_MM, "height_mm": H_MM, "er": ER,
                         "thickness_mm": T_MM, "tan_delta": TAND,
                         "port_mm": [12.5, 25.0]}})
        self.assertEqual(r["status"], "ok")
        self.assertTrue(r["value"]["plane_modes"]["modes_in_band"])

    def test_the_capability_text_no_longer_claims_no_planes(self):
        from pcb_agent_mcp import tools
        r = tools.TOOLS["calc_capabilities"]["handler"]({})
        blob = str(r).lower()
        self.assertIn("pdn_plane_modes", blob)


if __name__ == "__main__":
    unittest.main()
