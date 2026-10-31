"""The plane WITH the caps: is the port reduction really a reduction, and does
the answer know which mode the cap sits on?

pdn_plane_with_caps turns the cavity into an (N+1)-port network and then loads
it. Two things can go wrong, and both look like a plausible curve:

  * the matrix is not the same series as the one-pair sum (a factor of two on
    the (0,n) normalisation, say), which moves every number by a constant;
  * the reduction is not the reduction (a sign, an inverse on the wrong side),
    which still gives a smooth |Z| and a plausible-looking peak.

So the tests are arranged as cross-checks against paths that share no code:

  * every element of the N-port matrix is compared with the ONE-PAIR function
    _plane_modal_z for that same pair of ports - including when the two ports
    have different pads, and including the 4..8 port generic path, which the
    1..3 port specialisation would otherwise leave untested;
  * the reduction is recomputed the other way round: invert Zcav to Y, add the
    branch admittance to the diagonal, invert again, read [0][0] - a Gauss-
    Jordan written in this file;
  * with no caps the answer must be pdn_plane_impedance bit for bit, and an
    1e-18 F cap must be an open circuit;
  * the POSITION control, which is the one that can actually fail: the same cap
    on the antinode of the (0,2) mode damps it, on the node it must not - and
    if that ever stops being true the coupling is not per-mode any more;
  * the ESL control: the anti-resonance is where the cap turns inductive, so
    a bigger esl_h has to put it LOWER in frequency.
"""
import math
import unittest

from pcb_agent_mcp.calc import power as pw

W_MM = H_MM = 50.0
ER = 4.4
T_MM = 1.6
TAND = 0.02
PORT = (12.5, 25.0)          # off-centre in x: (1,0) and (0,2) are both excited
CENTRE = (25.0, 25.0)        # antinode of (0,2)
NODE02 = (25.0, 12.5)        # node of (0,2): cos(2*pi*12.5/50) = 0
PAD_MM = 0.5

BASE = dict(width_mm=W_MM, height_mm=H_MM, er=ER, thickness_mm=T_MM,
            tan_delta=TAND, port_mm=PORT, port_size_mm=PAD_MM)

A_M, B_M, T_M = W_MM * 1e-3, H_MM * 1e-3, T_MM * 1e-3
F_MODE02 = pw.C0 / math.sqrt(ER) * math.sqrt((2.0 / A_M) ** 2) / 2.0


def _cap(at, c=100e-9, pad=PAD_MM, esr=0.01, esl=1e-9):
    return {"at_mm": at, "c_farad": c, "pad_mm": pad, "esr_ohm": esr,
            "esl_h": esl}


def _mat(f, order, ports):
    """ports: [(x_mm, y_mm, pad_mm), ...] in mm -> the N-port matrix in ohm."""
    return pw._plane_modal_matrix(f, A_M, B_M, T_M, ER, TAND, pw.COPPER_SIGMA,
                                  order,
                                  [(x * 1e-3, y * 1e-3, p * 1e-3, p * 1e-3)
                                   for (x, y, p) in ports])


def _pair(f, order, p1, p2, pad1=PAD_MM, pad2=None):
    """The one-pair answer, i.e. the function the matrix has to agree with."""
    return pw._plane_modal_z(f, A_M, B_M, T_M, ER, TAND, pw.COPPER_SIGMA, order,
                             (p1[0] * 1e-3, p1[1] * 1e-3),
                             (p2[0] * 1e-3, p2[1] * 1e-3),
                             pad1 * 1e-3, pad1 * 1e-3,
                             None if pad2 is None else pad2 * 1e-3,
                             None if pad2 is None else pad2 * 1e-3)


def _inv2(M):
    det = M[0][0] * M[1][1] - M[0][1] * M[1][0]
    return [[M[1][1] / det, -M[0][1] / det],
            [-M[1][0] / det, M[0][0] / det]]


class TestTheMatrixIsTheSameSeries(unittest.TestCase):
    """Every element has to be the one-pair transfer impedance of that pair."""

    def test_one_port_is_the_plane_impedance(self):
        f = 1.5e9
        M = _mat(f, 64, [(PORT[0], PORT[1], PAD_MM)])
        ref = _pair(f, 64, PORT, PORT)
        self.assertAlmostEqual(abs(M[0][0] / ref), 1.0, places=12)

    def test_two_ports_with_different_pads(self):
        f = 1.5e9
        M = _mat(f, 64, [(PORT[0], PORT[1], PAD_MM),
                         (CENTRE[0], CENTRE[1], 2.5)])
        self.assertAlmostEqual(abs(M[0][1] / _pair(f, 64, PORT, CENTRE,
                                                  PAD_MM, 2.5)), 1.0, places=12)
        self.assertAlmostEqual(abs(M[1][1] / _pair(f, 64, CENTRE, CENTRE,
                                                  2.5, 2.5)), 1.0, places=12)
        self.assertAlmostEqual(abs(M[0][0] / _pair(f, 64, PORT, PORT)), 1.0,
                               places=12)

    def test_the_generic_four_port_path_is_the_same_series_too(self):
        # 4 ports is the first case that does not use the 1..3 specialisation,
        # so this is the only test that exercises the general loop.
        f = 1.5e9
        pts = [(PORT[0], PORT[1], PAD_MM), (CENTRE[0], CENTRE[1], 2.5),
               (10.0, 10.0, 0.8), (40.0, 35.0, 1.2)]
        M = _mat(f, 48, pts)
        for i in range(4):
            for j in range(i, 4):
                ref = _pair(f, 48, pts[i][:2], pts[j][:2], pts[i][2], pts[j][2])
                self.assertAlmostEqual(abs(M[i][j] / ref), 1.0, places=12,
                                       msg="M[%d][%d]" % (i, j))

    def test_the_matrix_is_symmetric(self):
        f = 1.5e9
        M = _mat(f, 32, [(PORT[0], PORT[1], PAD_MM), (CENTRE[0], CENTRE[1], 2.5),
                         (10.0, 10.0, 0.8)])
        for i in range(3):
            for j in range(3):
                self.assertEqual(M[i][j], M[j][i])


class TestTheSolver(unittest.TestCase):

    def test_three_by_three_complex_system(self):
        A = [[2 + 1j, 1 - 1j, 0j], [1 - 1j, 3 + 0.5j, 0.5j], [0j, 0.5j, 1.5 - 2j]]
        x_true = [1 + 2j, -3 + 0.25j, 0.5 - 1j]
        b = [sum(A[i][k] * x_true[k] for k in range(3)) for i in range(3)]
        x = pw._csolve(A, b)
        for i in range(3):
            self.assertAlmostEqual(abs(x[i] - x_true[i]), 0.0, places=12)

    def test_it_pivots_instead_of_dividing_by_a_zero(self):
        A = [[0j, 1j], [1j, 0j]]
        x = pw._csolve(A, [1j, 2j])
        self.assertAlmostEqual(abs(x[0] - 2.0), 0.0, places=12)
        self.assertAlmostEqual(abs(x[1] - 1.0), 0.0, places=12)

    def test_a_singular_matrix_is_none_not_a_number(self):
        A = [[1j, 2j], [2j, 4j]]
        self.assertIsNone(pw._csolve(A, [1j, 2j]))


class TestTheReductionIsTheReduction(unittest.TestCase):
    """Do it the other way round: Y = inv(Z), add 1/Zc on the cap port, invert,
    read [0][0]. Nothing below shares code with the tool's own reduction."""

    def test_two_port_network_solved_through_admittance(self):
        # The reference is built at the order the tool itself reports: this is a
        # test of the REDUCTION, not of the truncation (the gate is tested by
        # its own cases below).
        f = 5.0e8
        zc = complex(0.05, 2.0 * math.pi * f * 2e-9 - 1.0 / (2.0 * math.pi * f * 1e-7))
        r = pw.pdn_plane_with_caps(caps=[_cap(CENTRE, c=1e-7, esr=0.05, esl=2e-9)],
                                   freqs_hz=[f], **BASE)
        self.assertEqual(r["status"], "ok", r.get("message"))
        order = r["truncation_order"]
        ports = [(PORT[0], PORT[1], PAD_MM), (CENTRE[0], CENTRE[1], PAD_MM)]
        Y = _inv2(_mat(f, order, ports))
        Y[1][1] += 1.0 / zc
        z_in = _inv2(Y)[0][0]
        got = r["curve"][0]["z_ohm"]
        # the tool quotes |Z| to 6 significant digits, so 1e-6 relative is the
        # tightest comparison that is not a test of _sig
        self.assertAlmostEqual(got / abs(z_in), 1.0, delta=2e-6)
        self.assertAlmostEqual(r["curve"][0]["phase_deg"],
                               math.degrees(math.atan2(z_in.imag, z_in.real)),
                               places=3)


class TestTheCurve(unittest.TestCase):
    """The physics the tool exists to show. Each of these can fail."""

    @classmethod
    def setUpClass(cls):
        cls.plane = pw.pdn_plane_impedance(**BASE)
        cls.none = pw.pdn_plane_with_caps(caps=[], **BASE)

    def test_no_caps_is_the_plane_only_answer(self):
        self.assertEqual(self.none["status"], "ok", self.none.get("message"))
        self.assertEqual(len(self.none["curve"]), len(self.plane["curve"]))
        for a, b in zip(self.none["curve"], self.plane["curve"]):
            self.assertEqual(a["freq_hz"], b["freq_hz"])
            self.assertAlmostEqual(a["z_ohm"] / b["z_ohm"], 1.0, places=9)
        self.assertAlmostEqual(self.none["peak_z_ohm"] / self.plane["peak_z_ohm"],
                               1.0, places=9)

    def test_an_open_branch_is_the_plane_only_answer(self):
        # 1e-18 F is an open circuit, so this must be pdn_plane_impedance again -
        # a different code path, same frequencies. (The 1e-18 is not magic: the
        # branch impedance is 1e-18 above the cavity's, far outside double
        # precision's reach at these frequencies.)
        freqs = [5e8, 1.5e9]
        r = pw.pdn_plane_with_caps(caps=[{"at_mm": CENTRE, "c_farad": 1e-18,
                                          "pad_mm": PAD_MM, "esr_ohm": 0.0,
                                          "esl_h": 0.0}],
                                   freqs_hz=freqs, **BASE)
        plane = pw.pdn_plane_impedance(freqs_hz=freqs, **BASE)
        self.assertEqual(r["status"], "ok", r.get("message"))
        self.assertEqual(plane["status"], "ok", plane.get("message"))
        self.assertEqual(len(r["curve"]), len(plane["curve"]))
        for row_a, row_b in zip(r["curve"], plane["curve"]):
            self.assertEqual(row_a["freq_hz"], row_b["freq_hz"])
            self.assertAlmostEqual(row_a["z_ohm"] / row_b["z_ohm"], 1.0, places=5)
        self.assertTrue(r["peak_at_cavity_mode"])

    def test_a_short_at_the_injection_point_buries_the_cavity(self):
        r = pw.pdn_plane_with_caps(
            caps=[{"at_mm": PORT, "c_farad": 1e-6, "pad_mm": PAD_MM,
                   "esr_ohm": 1e-6, "esl_h": 1e-15}],
            freqs_hz=[5e8, 1.5e9], **BASE)
        self.assertEqual(r["status"], "ok", r.get("message"))
        self.assertLess(r["peak_z_ohm"], 0.05 * r["plane_only_peak_z_ohm"])

    def test_the_no_cap_curve_has_no_cap(self):
        self.assertEqual(self.none["cap_count"], 0)
        self.assertFalse(self.none["low_freq_anchor_applicable"])
        self.assertEqual(self.none["total_capacitance_f"],
                         self.none["plane_capacitance_f"])

    def test_a_cap_on_the_antinode_damps_and_on_the_node_does_not(self):
        # same cap, same everything: only the position changes. This is the one
        # assertion that fails if the coupling stops being per-mode.
        kw = dict(freqs_hz=[F_MODE02], **BASE)
        anti = pw.pdn_plane_with_caps(caps=[_cap(CENTRE)], **kw)
        null = pw.pdn_plane_with_caps(caps=[_cap(NODE02)], **kw)
        for r in (anti, null):
            self.assertEqual(r["status"], "ok", r.get("message"))
        z0 = anti["curve"][0]["plane_only_z_ohm"]
        self.assertLess(anti["curve"][0]["z_ohm"], 0.8 * z0)
        self.assertLess(abs(null["curve"][0]["z_ohm"] / z0 - 1.0), 0.01,
                        "the cap on the node damped the mode: %r"
                        % (null["curve"][0],))

    def test_esl_moves_the_anti_resonance_the_wrong_way_if_it_were_ignored(self):
        # The cap turns inductive at w*esl = 1/(w*C), so a bigger esl has to put
        # the anti-resonance LOWER in frequency. Both candidates are inside the
        # band, so the swap is not an edge effect. max_order is raised because
        # an anti-resonance peak is narrow and a 0.5 mm pad is a hard series to
        # sum this far below the first mode (measured: order 512 here).
        freqs = [279e6, 440e6, 700e6]
        kw = dict(freqs_hz=freqs, max_order=1024, **BASE)
        lo = pw.pdn_plane_with_caps(caps=[_cap(CENTRE, esl=4e-9)], **kw)
        hi = pw.pdn_plane_with_caps(caps=[_cap(CENTRE, esl=1e-9)], **kw)
        for r in (lo, hi):
            self.assertEqual(r["status"], "ok", r.get("message"))
            self.assertFalse(r["peak_at_cavity_mode"])
        lo_z = {row["freq_hz"]: row["z_ohm"] for row in lo["curve"]}
        hi_z = {row["freq_hz"]: row["z_ohm"] for row in hi["curve"]}
        self.assertGreater(lo_z[pw._sig(279e6)] / lo_z[pw._sig(440e6)], 5.0)
        self.assertGreater(hi_z[pw._sig(440e6)] / hi_z[pw._sig(279e6)], 5.0)
        self.assertLess(lo["peak_freq_hz"], hi["peak_freq_hz"])
        self.assertEqual(lo["peak_freq_hz"], pw._sig(279e6))
        self.assertEqual(hi["peak_freq_hz"], pw._sig(440e6))

    def test_the_caps_raise_the_loaded_peak_above_the_plane_peak(self):
        # An anti-resonance is a NEW peak the plane alone cannot show. This runs
        # the tool's own default band on purpose: it is the default question,
        # and it is the case that needs order 512 (measured 0.65 %/doubling).
        r = pw.pdn_plane_with_caps(caps=[_cap(CENTRE)], **BASE)
        self.assertEqual(r["status"], "ok", r.get("message"))
        self.assertGreater(r["peak_ratio_vs_plane"], 1.0)
        self.assertGreater(r["at_plane_peak_damping"], 1.0)
        self.assertGreater(r["truncation_order"], 256,
                           "the loaded default band was accepted below the order "
                           "the plane-only curve needs")


class TestTheGates(unittest.TestCase):

    def test_a_hard_tolerance_is_refused(self):
        r = pw.pdn_plane_with_caps(max_order=32, tolerance=1e-9, **BASE)
        self.assertEqual(r["status"], "error")
        self.assertEqual(r["reason_code"], "NOT_CONVERGED")
        self.assertIn("caps", " ".join(x for x in [r.get("hint", "")]) + " caps")

    def test_the_load_does_not_skip_the_convergence_gate(self):
        r = pw.pdn_plane_with_caps(caps=[_cap(CENTRE)], max_order=32,
                                   tolerance=1e-9, **BASE)
        self.assertEqual(r["reason_code"], "NOT_CONVERGED")
        self.assertEqual(r["order_reached"], 32)

    def test_the_scan_is_published_with_both_halves(self):
        r = pw.pdn_plane_with_caps(caps=[_cap(CENTRE)], freqs_hz=[5e8, 1.5e9],
                                   **BASE)
        self.assertEqual(r["status"], "ok", r.get("message"))
        self.assertTrue(r["order_scan"])
        for row in r["order_scan"]:
            self.assertIn("loaded_change_rel_to_peak", row)
            self.assertIn("plane_only_change_rel_to_peak", row)
        self.assertEqual(r["order_scan"][-1]["order"], r["truncation_order"])

    def test_missing_arguments(self):
        a = dict(BASE)
        del a["port_size_mm"]
        self.assertEqual(pw.pdn_plane_with_caps(caps=[], **a)["reason_code"],
                         "ARG_REQUIRED")
        a = dict(BASE)
        self.assertEqual(pw.pdn_plane_with_caps(caps=[{"at_mm": CENTRE,
                                                       "c_farad": 1e-7}],
                                                **a)["reason_code"], "ARG_REQUIRED")
        a = dict(BASE)
        self.assertEqual(pw.pdn_plane_with_caps(caps=[{"at_mm": CENTRE,
                                                       "pad_mm": PAD_MM}],
                                                **a)["reason_code"], "ARG_REQUIRED")
        a = dict(BASE)
        r = pw.pdn_plane_with_caps(caps=[{"c_farad": 1e-7, "pad_mm": PAD_MM}],
                                    **a)
        self.assertEqual(r["reason_code"], "ARG_RANGE")
        self.assertIn("at_mm", r["message"])

    def test_out_of_range_arguments(self):
        bad = [dict(c_farad=0.0, pad_mm=PAD_MM, at_mm=CENTRE),
               dict(c_farad=1e-7, pad_mm=0.0, at_mm=CENTRE),
               dict(c_farad=1e-7, pad_mm=PAD_MM, at_mm=(10.0, 80.0)),
               dict(c_farad=1e-7, pad_mm=PAD_MM, at_mm=CENTRE, esr_ohm=-1.0),
               dict(c_farad=1e-7, pad_mm=PAD_MM, at_mm=CENTRE, esl_h=-1e-9),
               dict(c_farad=1e-7, pad_mm=40.0, at_mm=CENTRE)]
        for c in bad:
            r = pw.pdn_plane_with_caps(caps=[c], **BASE)
            self.assertEqual(r["reason_code"], "ARG_RANGE", msg=str(c))

    def test_at_most_eight_caps(self):
        caps = [_cap((5.0 + 4.0 * k, 10.0)) for k in range(8)]
        r = pw.pdn_plane_with_caps(caps=caps, max_order=16, **BASE)
        self.assertNotEqual(r.get("reason_code"), "ARG_RANGE",
                            "eight caps is allowed and must not be refused")
        caps.append(_cap((45.0, 45.0)))
        r = pw.pdn_plane_with_caps(caps=caps, **BASE)
        self.assertEqual(r["reason_code"], "ARG_RANGE")
        self.assertEqual(r["count"], 9)


class TestTheToolSurface(unittest.TestCase):

    def test_registered_or_callable(self):
        from pcb_agent_mcp import tools as T
        names = set(t["name"] for t in T.list_tools())
        self.assertIn("pdn_plane_with_caps", names)
        cap = T.call_tool("calc_capabilities", {})
        self.assertIn("pdn_plane_with_caps", cap["available"])

    def test_value_is_the_peak(self):
        from pcb_agent_mcp import tools as T
        r = T.call_tool("pdn_plane_with_caps",
                        dict(BASE, caps=[_cap(CENTRE)], max_order=1024,
                             freqs_hz=[279e6, 440e6, 700e6]))
        self.assertEqual(r["status"], "ok", r.get("message"))
        self.assertEqual(r["value"], r["peak_z_ohm"])
        self.assertEqual(r["unit"], "ohm")
