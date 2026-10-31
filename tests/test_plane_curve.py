"""The plane impedance CURVE: does the modal sum have a limit, and is the limit right?

The whole point of this tool is a refusal turned into an answer, so the tests are
about the refusal first:

  * with a POINT port the same sum must still be climbing at order 256 - that is
    the negative control, and it is what makes the finite-pad claim mean
    something. Without it "it converges now" would be a story;
  * the convergence gate must bite: a tolerance nobody can meet has to come back
    NOT_CONVERGED rather than a number;
  * the limit must be the right limit: below the first mode the sum IS the
    parallel-plate capacitance (0.5 %), and at a mode peak it IS the closed-form
    single-mode peak of pdn_plane_modes, which is a different code path.

Two more independent routes are used: a textbook double sum written out again in
this file (no folded tables, no precomputed arrays), and the physical statement
that at a parallel resonance the input impedance is RESISTIVE - so the phase at
the first mode has to be near 0 deg, not near +-90 deg.
"""
import math
import unittest

from pcb_agent_mcp.calc import power as pw

W_MM = H_MM = 50.0
ER = 4.4
T_MM = 1.6
TAND = 0.02
PORT = (12.5, 25.0)          # off-centre in x: (1,0) is excited, (2,0) is not
CENTRE = (25.0, 25.0)
PAD_MM = 0.5
C_PLANE = pw.EPS0 * ER * (W_MM * 1e-3) * (H_MM * 1e-3) / (T_MM * 1e-3)

BASE = dict(width_mm=W_MM, height_mm=H_MM, er=ER, thickness_mm=T_MM,
            tan_delta=TAND, port_mm=PORT, port_size_mm=PAD_MM)


def _f_mn(m, n, a_mm=W_MM, b_mm=H_MM, er=ER):
    return (pw.C0 / math.sqrt(er)
            * math.sqrt((m / (a_mm * 1e-3)) ** 2 + (n / (b_mm * 1e-3)) ** 2) / 2.0)


def _textbook_sum(f_hz, order, p1=PORT, p2=PORT, pad_mm=PAD_MM, tan_delta=TAND,
                  conductivity=pw.COPPER_SIGMA):
    """The same series, written out from the textbook formula, nothing folded.

    Deliberately naive: c_m c_n kept separate, the pad average written as
    sin(u)/u inline, the Q looked up per mode at the evaluation frequency.
    """
    a = W_MM * 1e-3
    b = H_MM * 1e-3
    t = T_MM * 1e-3
    w = 2.0 * math.pi * f_hz
    k2 = w * w * pw.MU0 * pw.EPS0 * ER
    q = 1.0 / (1.0 / (1.0 / tan_delta) + (0.0 if conductivity is None else
                                          1.0 / (t / math.sqrt(
                                              1.0 / (math.pi * f_hz * pw.MU0
                                                     * conductivity)))))
    kap2 = k2 * (1.0 - 1j / q)
    s = pad_mm * 1e-3
    total = 0j
    for m in range(order + 1):
        cm = 1.0 if m == 0 else math.sqrt(2.0)
        ux = math.pi * m * s / (2.0 * a)
        sxm = 1.0 if m == 0 else math.sin(ux) / ux
        for n in range(order + 1):
            cn = 1.0 if n == 0 else math.sqrt(2.0)
            uy = math.pi * n * s / (2.0 * b)
            syn = 1.0 if n == 0 else math.sin(uy) / uy
            kmn2 = (math.pi * m / a) ** 2 + (math.pi * n / b) ** 2
            psi = (cm * cn) ** 2 / (a * b) * sxm * sxm * syn * syn
            psi *= (math.cos(math.pi * m * p1[0] / W_MM)
                    * math.cos(math.pi * m * p2[0] / W_MM)
                    * math.cos(math.pi * n * p1[1] / H_MM)
                    * math.cos(math.pi * n * p2[1] / H_MM))
            total += psi / (kmn2 - kap2)
    return 1j * w * pw.MU0 * t * total


def _z(f, order, p1=PORT, p2=PORT, pad_mm=PAD_MM, conductivity=pw.COPPER_SIGMA):
    return pw._plane_modal_z(f, W_MM * 1e-3, H_MM * 1e-3, T_MM * 1e-3, ER, TAND,
                             conductivity, order,
                             (p1[0] * 1e-3, p1[1] * 1e-3),
                             (p2[0] * 1e-3, p2[1] * 1e-3),
                             pad_mm * 1e-3, pad_mm * 1e-3)


class TestItIsStillTheSameSeries(unittest.TestCase):
    """A second implementation of the same formula: different bookkeeping, same
    number to 1e-9. Catches a folded table or an index slip, not a physics slip."""

    def test_textbook_sum_matches(self):
        for f in (2.4e8, 1.0e9, 1.4288e9):
            for order in (6, 16):
                here = _z(f, order)
                there = _textbook_sum(f, order)
                self.assertAlmostEqual(abs(here) / abs(there), 1.0, places=9,
                                       msg="f=%g order=%d" % (f, order))
                self.assertAlmostEqual((here - there).real, 0.0, places=9)
                self.assertAlmostEqual((here - there).imag, 0.0, places=9)

    def test_reciprocity_is_exact(self):
        a = _z(1.0e9, 24, p1=PORT, p2=CENTRE)
        b = _z(1.0e9, 24, p1=CENTRE, p2=PORT)
        self.assertEqual(a, b)


class TestTheLimitIsTheRightLimit(unittest.TestCase):
    """Below the first mode the plane pair IS a capacitor, and its capacitance is
    known without any series at all. This is the anchor that fixes the
    normalisation of every c_m - the (0,0) term alone is that capacitor."""

    def test_low_frequency_is_the_plane_capacitance(self):
        f = _f_mn(1, 0) / 1000.0
        z = abs(_z(f, 64))
        z_cap = 1.0 / (2.0 * math.pi * f * C_PLANE)
        self.assertLess(abs(z / z_cap - 1.0), 0.005,
                        "%.6g ohm against %.6g ohm" % (z, z_cap))

    def test_low_frequency_does_not_care_about_the_order(self):
        f = _f_mn(1, 0) / 100.0
        z8 = _z(f, 8)
        z128 = _z(f, 128)
        # not zero: 8 orders cannot see the higher terms, and they do move the
        # sum by 3e-4 here. What matters is that it stops moving - 8 vs 128 is
        # 300x smaller than the 0.15 % anchor error we accept at f/1000.
        self.assertLess(abs(z128 - z8) / abs(z128), 1e-3)

    def test_first_mode_peak_is_the_closed_form_peak(self):
        """At the first mode the sum is dominated by one term, and that term is
        pdn_plane_modes' closed form. Two code paths, one number."""
        mods = pw.pdn_plane_modes(W_MM, H_MM, ER, T_MM, TAND, port_mm=PORT,
                                  max_order=2)
        row = [r for r in mods["value"]["modes"] if (r["m"], r["n"]) == (1, 0)][0]
        # the same Q as that function uses (dielectric only, Q = 1/tan_delta)
        z = abs(_z(row["f_hz"], 64, conductivity=None))
        self.assertLess(abs(z / row["z_peak_ohm"] - 1.0), 0.01,
                        "sum %.6g vs closed form %.6g" % (z, row["z_peak_ohm"]))

    def test_copper_lowers_the_peak_by_the_q_ratio(self):
        """The only difference between the two Q conventions is 1/Qc, so the peak
        must scale with the total Q - not with anything else."""
        f = _f_mn(1, 0)
        z_ideal = abs(_z(f, 64, conductivity=None))          # Q = 1/tan_delta
        z_both = abs(_z(f, 64))                             # Q = 1/(1/Qc + 1/Qd)
        q_total = pw._plane_q_of(f, T_MM * 1e-3, TAND, pw.COPPER_SIGMA)[0]
        q_diel = 1.0 / TAND
        self.assertLess(abs(z_both / z_ideal - q_total / q_diel), 0.01)

    def test_at_a_parallel_resonance_the_impedance_is_resistive(self):
        """Physics, not implementation: a parallel resonance has a real impedance
        at the resonant frequency, so the phase there must be near 0 deg. A sign
        slip in the 1/(k_mn^2 - k^2(1 - j/Q)) shows up here as +-90 deg."""
        z = _z(_f_mn(1, 0), 64)
        self.assertLess(abs(math.degrees(math.atan2(z.imag, z.real))), 12.0)
        self.assertGreater(z.real, 0.0)


class TestThePadIsWhatMakesItConverge(unittest.TestCase):
    """The negative control. A plane pair driven at a POINT has a logarithmically
    infinite reactance, so the sum must still be climbing however far you take
    it. If this ever flattens, the finite-pad claim has lost its teeth."""

    def test_point_port_still_climbing_at_order_256(self):
        vals = [abs(_z(2.0e9, order, pad_mm=0.0, conductivity=None))
                for order in (8, 32, 128, 256)]
        self.assertEqual(vals, sorted(vals), "not monotone: %r" % (vals,))
        self.assertGreater(vals[-1] / vals[0], 2.5,
                           "point port settled: %r" % (vals,))
        # and the growth is not a plateau in disguise
        self.assertGreater(vals[-1] - vals[-2], 0.5 * (vals[-2] - vals[-3]))

    def test_a_finite_pad_settles_where_the_point_port_does_not(self):
        f = 2.0e9
        pad = [abs(_z(f, o)) for o in (64, 128, 256)]
        self.assertLess(abs(pad[-1] - pad[-2]) / pad[-1], 0.05,
                        "finite pad did not settle: %r" % (pad,))
        point = [abs(_z(f, o, pad_mm=0.0)) for o in (64, 128, 256)]
        self.assertGreater(abs(point[-1] - point[-2]) / point[-1], 0.05,
                           "point port DID settle: %r - the control is dead"
                           % (point,))


class TestTheGateBites(unittest.TestCase):

    def test_tolerance_nobody_can_meet_is_refused(self):
        r = pw.pdn_plane_impedance(max_order=32, tolerance=1e-6, **BASE)
        self.assertEqual(r["status"], "error")
        self.assertEqual(r["reason_code"], "NOT_CONVERGED")
        self.assertEqual(r["order_reached"], 32)
        self.assertGreater(r["max_change_rel_to_peak"], 1e-6)
        self.assertTrue(r["order_scan"])
        self.assertIn("port_size_mm", r.get("hint", ""))

    def test_the_scan_is_reported_so_the_number_can_be_audited(self):
        r = pw.pdn_plane_impedance(freqs_hz=[5e8, 1.4288e9, 2.0e9], **BASE)
        self.assertEqual(r["status"], "ok")
        scan = r["order_scan"]
        self.assertGreaterEqual(len(scan), 2)
        for row in scan:
            self.assertIn("max_change_rel_to_peak", row)
            self.assertIn("max_pointwise_relative_change", row)
        self.assertLessEqual(r["truncation_max_change_rel_to_peak"],
                             r["truncation_tolerance"])
        self.assertEqual(scan[-1]["order"], r["truncation_order"])

    def test_the_anchor_is_reported(self):
        r = pw.pdn_plane_impedance(freqs_hz=[5e8, 1.4288e9], **BASE)
        self.assertLess(r["capacitance_anchor_error_pct"], 1.0)
        self.assertAlmostEqual(r["plane_capacitance_f"] / C_PLANE, 1.0, places=5)


class TestTheCurve(unittest.TestCase):

    def setUp(self):
        self.r = pw.pdn_plane_impedance(freqs_hz=[3e8, 1.0e9, 1.4288e9, 2.0e9],
                                        **BASE)

    def test_ok(self):
        self.assertEqual(self.r["status"], "ok", self.r.get("message"))
        self.assertGreater(self.r["curve_points"], 4)

    def test_every_mode_in_the_band_is_a_row(self):
        """The peaks are narrow, so the sampling must not be left to the caller's
        grid: the mode frequencies are inserted, and the tool says how many."""
        rows = self.r["curve"]
        self.assertGreaterEqual(self.r["mode_frequencies_inserted"], 1)
        freqs = [r["freq_hz"] for r in rows]
        for f in freqs:
            self.assertGreater(f, 0.0)
        # the first mode must be one of them, and it must be the peak here
        # reported fields carry six significant digits, so compare at that scale
        self.assertAlmostEqual(self.r["first_resonance_hz"] / _f_mn(1, 0), 1.0,
                               places=5)
        # the peak of a LOSSY mode is a touch below f_mn - by f_mn/(2Q^2), i.e.
        # 2e-4 at Q=47 - so the sampled maximum may land one grid point below the
        # inserted mode. That is physics, not a missed row: the row at f_mn must
        # still be there and must still be within 1 % of the peak.
        self.assertLess(abs(self.r["peak_freq_hz"] / self.r["first_resonance_hz"]
                            - 1.0), 1e-3)
        at_mode = [row for row in rows
                   if abs(row["freq_hz"] / self.r["first_resonance_hz"] - 1.0) < 1e-5]
        self.assertEqual(len(at_mode), 1)
        self.assertLess(abs(at_mode[0]["z_ohm"] / self.r["peak_z_ohm"] - 1.0), 0.01)

    def test_peak_agrees_with_the_single_mode_peak(self):
        mods = pw.pdn_plane_modes(W_MM, H_MM, ER, T_MM, TAND, port_mm=PORT,
                                  max_order=2)
        row = [r for r in mods["value"]["modes"] if (r["m"], r["n"]) == (1, 0)][0]
        q_total = pw._plane_q_of(_f_mn(1, 0), T_MM * 1e-3, TAND, pw.COPPER_SIGMA)[0]
        want = row["z_peak_ohm"] * q_total * TAND
        self.assertLess(abs(self.r["peak_z_ohm"] / want - 1.0), 0.02,
                        "curve peak %.6g vs closed form %.6g"
                        % (self.r["peak_z_ohm"], want))

    def test_the_dip_is_between_modes_and_below_the_peak(self):
        """The number a cap bank has to beat. It only means something above the
        first mode - below it |Z| is just falling."""
        self.assertIsNotNone(self.r["dip_z_ohm"])
        self.assertGreater(self.r["dip_freq_hz"], self.r["first_resonance_hz"])
        self.assertLess(self.r["dip_z_ohm"], self.r["peak_z_ohm"])

    def test_value_is_the_peak(self):
        self.assertEqual(self.r["value"], self.r["peak_z_ohm"])
        self.assertEqual(self.r["unit"], "ohm")

    def test_the_caller_grid_is_kept(self):
        freqs = [r["freq_hz"] for r in self.r["curve"]]
        for f in (3e8, 1.0e9, 2.0e9):
            self.assertTrue(any(abs(x - f) < 1.0 for x in freqs), f)

    def test_band_default_is_flagged(self):
        r = pw.pdn_plane_impedance(**BASE)
        self.assertTrue(r["band_defaulted"])
        self.assertLess(r["band_hz"][0], r["first_resonance_hz"])
        self.assertGreater(r["band_hz"][1], r["first_resonance_hz"])


class TestRefusals(unittest.TestCase):

    def test_missing_pad_is_refused_and_says_why(self):
        a = dict(BASE)
        del a["port_size_mm"]
        r = pw.pdn_plane_impedance(**a)
        self.assertEqual(r["status"], "error")
        self.assertEqual(r["reason_code"], "ARG_REQUIRED")
        self.assertIn("converge", r["hint"])

    def test_missing_tan_delta_is_refused(self):
        a = dict(BASE)
        del a["tan_delta"]
        r = pw.pdn_plane_impedance(**a)
        self.assertEqual(r["reason_code"], "ARG_REQUIRED")
        self.assertIn("tan_delta", r["message"])

    def test_pad_wider_than_half_the_plane(self):
        a = dict(BASE)
        a["port_size_mm"] = 40.0
        r = pw.pdn_plane_impedance(**a)
        self.assertEqual(r["reason_code"], "ARG_RANGE")
        self.assertIn("port_size_mm", r["message"])

    def test_zero_pad(self):
        a = dict(BASE)
        a["port_size_mm"] = 0.0
        r = pw.pdn_plane_impedance(**a)
        self.assertEqual(r["reason_code"], "ARG_RANGE")

    def test_port_outside_the_plane(self):
        a = dict(BASE)
        a["port_mm"] = (10.0, 80.0)
        r = pw.pdn_plane_impedance(**a)
        self.assertEqual(r["reason_code"], "ARG_RANGE")

    def test_band_must_be_ordered(self):
        a = dict(BASE)
        a.update(f_start_hz=2e9, f_stop_hz=1e9)
        r = pw.pdn_plane_impedance(**a)
        self.assertEqual(r["reason_code"], "ARG_RANGE")

    def test_order_cap_and_tolerance_range(self):
        a = dict(BASE)
        a["tolerance"] = 0.9
        self.assertEqual(pw.pdn_plane_impedance(**a)["reason_code"], "ARG_RANGE")
        a = dict(BASE)
        a["max_order"] = 4
        self.assertEqual(pw.pdn_plane_impedance(**a)["reason_code"], "ARG_RANGE")

    def test_bad_dielectric(self):
        a = dict(BASE)
        a["er"] = 0.5
        self.assertEqual(pw.pdn_plane_impedance(**a)["reason_code"], "ARG_RANGE")

    def test_not_computed_is_carried(self):
        r = pw.pdn_plane_impedance(freqs_hz=[5e8, 1.4288e9], **BASE)
        self.assertIn("not_computed", r)
        joined = " ".join(r["not_computed"])
        self.assertIn("capacitor", joined)


class TestTheToolSurface(unittest.TestCase):

    def test_registered(self):
        from pcb_agent_mcp import tools as T
        names = set(t["name"] for t in T.list_tools())
        self.assertIn("pdn_plane_impedance", names)

    def test_callable_with_named_arguments(self):
        from pcb_agent_mcp import tools as T
        r = T.call_tool("pdn_plane_impedance", dict(BASE, freqs_hz=[5e8, 1.4288e9]))
        self.assertEqual(r["status"], "ok", r.get("message"))
        self.assertIn("curve", r)

    def test_capabilities_no_longer_claims_the_curve_is_missing(self):
        from pcb_agent_mcp import tools as T
        cap = T.call_tool("calc_capabilities", {})
        joined = " ".join(str(x) for x in cap["not_implemented_yet"])
        self.assertNotIn("the modal SUM is not computed", joined)
        self.assertIn("pdn_plane_impedance", cap["available"])


if __name__ == "__main__":
    unittest.main()
