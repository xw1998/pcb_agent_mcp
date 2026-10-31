"""Any TE_mnp / TM_mnp cavity mode, the mode chart, and roughness on Qc.

The old entry point answered exactly one mode (TE101) from one bracket formula.
Generalising it is where a physics tool usually goes quietly wrong, because a
mode index pair that is off by one still produces a plausible number. So every
claim here is pinned to a check that CAN fail:

  * the wall loss is computed twice from different algebra - the closed form
    (products of int sin^2 / int cos^2, with the tangential set named per wall)
    against an integration that writes |H_tan|^2 as |H|^2 - (H.n)^2 and never
    names a tangential component at all. A wrong wall bookkeeping moves the
    second one and not the first;
  * every mode must balance stored energy, u_e/u_m = 1. The returned ratio is
    u_e/u_m and NOT u_e/(u_e+u_m): the latter is 0.5 for every mode that
    balances, so comparing IT to 1.0 is a gate that can never pass (that was a
    real defect during this rewrite - it refused every mode but TE101);
  * the general closed form must reproduce the TE101 bracket formula, which is
    the number 0.0.51 shipped and which is anchored to the textbook ~7.8k for
    WR-90 at 10 GHz;
  * a filled cavity (er, mu_r) must balance too. 0.0.51 integrated the fields
    with vacuum eps/mu while the closed form used the fill, so er != 1 made the
    two disagree and the tool withheld the answer. That is a refusal, not a
    wrong number, but it is still a defect and the arithmetic is pinned here.

Two negative controls make the gates themselves testable: dropping one of the
two z walls changes Qc by far more than the 2 % tolerance, and the reason
u_e/(u_e+u_m) cannot be the returned ratio is shown arithmetically.
"""
import math
import unittest

from pcb_agent_mcp import tools as T
from pcb_agent_mcp.calc import filters as F

C0 = 299792458.0
MU0 = 4.0e-7 * math.pi
A, B = 22.86e-3, 10.16e-3                 # WR-90
F10 = 10e9
F011 = 16.573220e9


def _d_of(fam, m, n, p, a, b, f0, er=1.0, mur=1.0):
    """Length that puts (m, n, p) on f0 in a cavity of cross-section a x b."""
    c = C0 / math.sqrt(er * mur)
    kx, ky = m * math.pi / a, n * math.pi / b
    k0 = 2.0 * math.pi * f0 / c
    kz2 = k0 * k0 - (kx * kx + ky * ky)
    if kz2 <= 0.0:
        raise ValueError("below cutoff")
    return p * math.pi / math.sqrt(kz2)


def _h_at(fam, m, n, p, a, b, d, x, y, z):
    """H at a point, amplitude-normalised: the common factor is dropped because
    it cancels between the stored energy and the wall loss. For TM that common
    factor is (w*eps/kc^2); for TE the components are already at unit |Hz|."""
    kx, ky, kz = m * math.pi / a, n * math.pi / b, p * math.pi / d
    kc2 = kx * kx + ky * ky
    s = (math.sin(kx * x), math.sin(ky * y), math.sin(kz * z))
    c = (math.cos(kx * x), math.cos(ky * y), math.cos(kz * z))
    if fam == "TE":
        hx = -(kz * kx / kc2) * s[0] * c[1] * c[2]
        hy = -(kz * ky / kc2) * c[0] * s[1] * c[2]
        hz = c[0] * c[1] * s[2]
    else:
        hx = (ky / kc2) * s[0] * c[1] * c[2]
        hy = -(kx / kc2) * c[0] * s[1] * c[2]
        hz = 0.0
    return hx, hy, hz


def _energies(fam, m, n, p, a, b, d, n_grid=32):
    """(int|H|^2 dV, int|E|^2 dV) with E scaled so that eps*int|E|^2 is
    comparable to mu*int|H|^2 for a vacuum fill (the k0^2/kc^4 factor)."""
    kx, ky, kz = m * math.pi / a, n * math.pi / b, p * math.pi / d
    kc2 = kx * kx + ky * ky
    k0 = math.sqrt(kc2 + kz * kz)
    dx, dy, dz = a / n_grid, b / n_grid, d / n_grid
    sh = se = 0.0
    for i in range(n_grid):
        x = (i + 0.5) * dx
        for j in range(n_grid):
            y = (j + 0.5) * dy
            for k in range(n_grid):
                z = (k + 0.5) * dz
                hx, hy, hz = _h_at(fam, m, n, p, a, b, d, x, y, z)
                sh += hx * hx + hy * hy + hz * hz
                if fam == "TE":
                    # E_t = (k0/kc^2) * z X grad_t Hz, i.e. |E|^2 = (k0^2/kc^4)|grad Hz|^2
                    gx = -kx * math.sin(kx * x) * math.cos(ky * y) * math.sin(kz * z)
                    gy = -ky * math.cos(kx * x) * math.sin(ky * y) * math.sin(kz * z)
                    se += (k0 * k0 / kc2 ** 2) * (gx * gx + gy * gy)
                else:
                    # E_z = sin sin cos and E_t = (kz/kc^2) grad_t Ez
                    ez = math.sin(kx * x) * math.sin(ky * y) * math.cos(kz * z)
                    gx = kx * math.cos(kx * x) * math.sin(ky * y) * math.cos(kz * z)
                    gy = ky * math.sin(kx * x) * math.cos(ky * y) * math.cos(kz * z)
                    se += ez * ez + (kz * kz / kc2 ** 2) * (gx * gx + gy * gy)
    cell = dx * dy * dz
    return sh * cell, se * cell


def _q_by_normal(fam, m, n, p, a, b, d, f0, rs, n_grid=32,
                 z_faces=2, x_swap=False):
    """Qc with the wall loss written as |H|^2 - (H.n)^2 on each wall.

    Independent of _cavity_q_closed: no tangential component is ever named, and
    the quadrature is a midpoint sum over the actual grid. z_faces and
    x_tan_full exist only so the negative controls can break it on purpose.
    """
    vol, _ = _energies(fam, m, n, p, a, b, d, n_grid)
    dx, dy, dz = a / n_grid, b / n_grid, d / n_grid

    def _surf(axis, pos, nu, nv, du, dv, swap=False):
        total = 0.0
        for i in range(nu):
            u = (i + 0.5) * du
            for j in range(nv):
                v = (j + 0.5) * dv
                if axis == "z":
                    x, y, z = u, v, pos
                elif axis == "x":
                    x, y, z = pos, u, v
                else:
                    x, y, z = u, pos, v
                hx, hy, hz = _h_at(fam, m, n, p, a, b, d, x, y, z)
                full = hx * hx + hy * hy + hz * hz
                normal = {"x": hx * hx, "y": hy * hy, "z": hz * hz}[axis]
                # swap = the classic index error: subtract the TANGENTIAL pair on
                # the x wall and count hx^2 + hz^2 as loss there instead
                total += (hx * hx + hz * hz) if swap else full - normal
        return total * du * dv

    wall = _surf("z", 0.0, n_grid, n_grid, dx, dy)
    if z_faces == 2:
        wall += _surf("z", d, n_grid, n_grid, dx, dy)
    wall += (_surf("x", 0.0, n_grid, n_grid, dy, dz, x_swap)
             + _surf("x", a, n_grid, n_grid, dy, dz, x_swap))
    wall += (_surf("y", 0.0, n_grid, n_grid, dx, dz)
             + _surf("y", b, n_grid, n_grid, dx, dz))
    return 2.0 * math.pi * f0 * MU0 * vol / (rs * wall)


def _call(**kw):
    return T.call_tool("filter_waveguide_cavity", kw)


def _val(**kw):
    r = _call(**kw)
    assert r["status"] == "ok", r
    return r["value"]


MODES = [("TE", 1, 0, 1), ("TE", 0, 1, 1), ("TE", 1, 1, 1), ("TE", 2, 0, 1),
         ("TE", 1, 1, 2), ("TE", 0, 1, 2), ("TE", 3, 1, 1),
         ("TM", 1, 1, 1), ("TM", 1, 1, 2), ("TM", 2, 1, 1), ("TM", 2, 2, 1)]


class TestTE101IsUnchanged(unittest.TestCase):
    """0.0.51's answer, to the digit: the mode string and the wording moved, the
    physics did not."""

    def test_the_golden_numbers(self):
        v = _val(f0_hz=F10, a_mm=22.86, b_mm=10.16)
        self.assertEqual(v["mode"], "TE_1_0_1")
        self.assertAlmostEqual(v["length_mm"], 19.8536, places=4)
        self.assertAlmostEqual(v["q_conductor"], 7821.5, places=1)
        self.assertAlmostEqual(v["q_conductor_numeric"], 7821.5, places=1)
        self.assertEqual(v["q_agreement_pct"], 0.0)
        self.assertAlmostEqual(v["r_over_q_ohm"], 160.9572, places=4)
        self.assertEqual(v["unloaded_q"], v["q_conductor"])

    def test_the_new_blocks_appear_only_when_asked_for(self):
        r = _call(f0_hz=F10, a_mm=22.86, b_mm=10.16)
        v = r["value"]
        self.assertNotIn("mode_chart", v)
        self.assertNotIn("roughness", v)
        self.assertNotIn("next_mode", v)
        nc = r["not_computed"]
        self.assertTrue(any("pass roughness_um" in q for q in nc))
        self.assertTrue(any("pass modes_upto_hz" in q for q in nc))
        asked = _call(f0_hz=F10, a_mm=22.86, b_mm=10.16, roughness_um=2.0,
                      modes_upto_hz=20e9)["not_computed"]
        self.assertFalse(any("pass roughness_um" in q for q in asked))
        self.assertFalse(any("pass modes_upto_hz" in q for q in asked))

    def test_a_default_b_comes_from_a(self):
        v = _val(f0_hz=F10, a_mm=22.86)
        self.assertTrue(v["b_assumed"])
        self.assertAlmostEqual(v["b_mm"], 11.43, places=4)


class TestTheGeneralClosedForm(unittest.TestCase):
    """Every mode, two derivations, and the seeds of the TE101 answer."""

    def test_the_general_form_reproduces_the_bracket(self):
        v = _val(f0_hz=F10, a_mm=22.86, b_mm=10.16, modes_upto_hz=20e9)
        row = [q for q in v["mode_chart"] if q["mode"] == "TE_1_0_1"][0]
        self.assertAlmostEqual(row["q_conductor"], v["q_conductor"], delta=0.11)

    def test_an_independent_wall_loss_agrees(self):
        for fam, m, n, p in MODES:
            kx, ky = m * math.pi / A, n * math.pi / B
            kc2 = kx * kx + ky * ky
            k0 = 2.0 * math.pi * 12e9 / C0
            if k0 * k0 <= kc2:
                continue
            d = _d_of(fam, m, n, p, A, B, 12e9)
            closed = F._cavity_q_closed(fam, m, n, p, A, B, d, 12e9, 0.026, 1.0)
            other = _q_by_normal(fam, m, n, p, A, B, d, 12e9, 0.026)
            # places=8, not 9: the test computes MU0 as 4.0e-7*pi and the
            # module as 4*pi*1e-7, which differ by 5.4e-10 relative
            self.assertAlmostEqual(other / closed, 1.0, places=8,
                                   msg="%s%d%d%d" % (fam, m, n, p))

    def test_every_mode_balances_energy(self):
        for fam, m, n, p in MODES:
            kx, ky = m * math.pi / A, n * math.pi / B
            kc2 = kx * kx + ky * ky
            k0 = 2.0 * math.pi * 12e9 / C0
            if k0 * k0 <= kc2:
                continue
            d = _d_of(fam, m, n, p, A, B, 12e9)
            h2, e2 = _energies(fam, m, n, p, A, B, d)
            self.assertAlmostEqual(e2 / h2, 1.0, places=12,
                                   msg="%s%d%d%d" % (fam, m, n, p))
            q, ratio = F._cavity_q_numeric(fam, m, n, p, A, B, d, 12e9, 0.026)
            self.assertAlmostEqual(ratio, 1.0, places=12)
            self.assertGreater(q, 0.0)

    def test_the_returned_ratio_cannot_be_ue_over_total(self):
        """Why the gate compares u_e/u_m to 1 and not u_e/(u_e+u_m) to 1: the
        second is 0.5 for every balanced mode, so 'must be 1' never passes.
        0.0.51's rewrite shipped that comparison and refused every non-TE101
        mode with 'U_e/U = 0.500000000'."""
        d = _d_of("TE", 0, 1, 1, A, B, F011)
        q, ratio = F._cavity_q_numeric("TE", 0, 1, 1, A, B, d, F011, 0.026)
        self.assertAlmostEqual(ratio, 1.0, places=12)        # u_e/u_m
        self.assertAlmostEqual(ratio / (1.0 + ratio), 0.5, places=12)

    def test_the_gate_can_fail(self):
        """Negative control on the wall bookkeeping: keep only one of the two z
        walls and the loss drops by far more than the 2 % the tool tolerates."""
        d = _d_of("TE", 1, 0, 1, A, B, F10)
        good = _q_by_normal("TE", 1, 0, 1, A, B, d, F10, 0.026)
        broke = _q_by_normal("TE", 1, 0, 1, A, B, d, F10, 0.026, z_faces=1)
        self.assertGreater(abs(broke / good - 1.0), 0.10)

    def test_the_other_gate_can_fail(self):
        """Same for the named tangential set: counting hx^2 (the NORMAL component,
        which is identically zero on a PEC wall) and dropping hy^2 is the index
        error that looks like a rounding. It has to move Qc well past 2 %.

        Note why the |H|^2 - (H.n)^2 form cannot be broken the same way: on a PEC
        wall H.n IS zero, so that expression is exact by construction. What it
        buys is that it never names a tangential pair, so a wrong pair in the
        closed form shows up as a disagreement instead of agreeing with itself."""
        d = _d_of("TE", 1, 1, 1, A, B, 20e9)
        good = _q_by_normal("TE", 1, 1, 1, A, B, d, 20e9, 0.026)
        broke = _q_by_normal("TE", 1, 1, 1, A, B, d, 20e9, 0.026, x_swap=True)
        self.assertGreater(abs(broke / good - 1.0), 0.02)

    def test_te011_beats_te101_at_the_same_frequency(self):
        """The reason everyone builds TE011 cavities: at the same frequency the
        wall currents are spread further apart. 1.28x here; the check is that
        the ordering survives, not the exact ratio."""
        v101 = _val(f0_hz=F011, a_mm=22.86, b_mm=10.16)
        v011 = _val(f0_hz=F011, a_mm=22.86, b_mm=10.16, mode="TE011")
        self.assertAlmostEqual(v011["length_mm"], 19.8536, places=4)
        self.assertAlmostEqual(v011["q_conductor"], 8828.7, places=1)
        self.assertGreater(v011["q_conductor"], 1.15 * v101["q_conductor"])

    def test_a_filled_cavity_balances_and_agrees(self):
        """0.0.51's defect: with er != 1 the field integral used vacuum eps and
        the closed form the fill, so the two disagreed and the answer was
        withheld. Both energies now use the fill."""
        v = _val(f0_hz=12e9, a_mm=30.0, b_mm=15.0, er=2.2, mu_r=1.2)
        self.assertAlmostEqual(v["length_mm"], 7.9535, places=4)
        self.assertEqual(v["q_agreement_pct"], 0.0)
        for mode in ("TE_1_0_1", "TE_0_1_1", "TM_1_1_1"):
            d = _d_of(*_parse(mode), 30e-3, 15e-3, 12e9, 2.2, 1.2)
            q, ratio = F._cavity_q_numeric(*_parse(mode), 30e-3, 15e-3, d,
                                           12e9, 0.026, 1.2, 2.2)
            self.assertAlmostEqual(ratio, 1.0, places=12, msg=mode)


def _parse(name):
    fam, _, tail = name.partition("_")
    m, n, p = (int(x) for x in tail.split("_"))
    return fam, m, n, p


class TestTheModeChart(unittest.TestCase):
    """"My filter is fine" is a claim about the neighbours."""

    def test_the_neighbours_of_wr90_at_10_ghz(self):
        v = _val(f0_hz=F10, a_mm=22.86, b_mm=10.16, modes_upto_hz=20e9)
        chart = {q["mode"]: q for q in v["mode_chart"]}
        self.assertEqual(v["mode_chart_count"], 9)
        self.assertEqual(v["next_mode"]["mode"], "TE_2_0_1")
        self.assertAlmostEqual(v["next_mode"]["f_over_f0"], 1.5132, places=4)
        self.assertAlmostEqual(chart["TM_1_1_0"]["f_hz"], 16.1451e9, delta=1e6)
        self.assertAlmostEqual(chart["TE_0_1_1"]["f_hz"], F011, delta=2e6)
        self.assertEqual(sum(1 for q in v["mode_chart"] if q["is_operating"]), 1)
        self.assertEqual(chart["TE_1_0_1"]["is_operating"], True)

    def test_a_square_cavity_degenerates(self):
        v = _val(f0_hz=10e9, a_mm=20.0, b_mm=20.0, modes_upto_hz=15e9)
        chart = {q["mode"]: q for q in v["mode_chart"]}
        self.assertEqual(chart["TE_1_0_1"]["degeneracy"], 2)
        self.assertEqual(chart["TE_0_1_1"]["degeneracy"], 2)
        self.assertAlmostEqual(chart["TE_1_0_1"]["f_hz"],
                               chart["TE_0_1_1"]["f_hz"], delta=1.0)
        self.assertEqual(chart["TM_1_1_0"]["degeneracy"], 1)

    def test_the_chart_and_the_single_mode_answer_agree(self):
        """One mode, two entry points: the chart's Qc for TE_2_0_1 must be the
        number the tool gives when TE_2_0_1 is the mode you asked for."""
        v = _val(f0_hz=F10, a_mm=22.86, b_mm=10.16, modes_upto_hz=20e9)
        row = [q for q in v["mode_chart"] if q["mode"] == "TE_2_0_1"][0]
        direct = _val(f0_hz=row["f_hz"], a_mm=22.86, b_mm=10.16, mode="TE_2_0_1")
        self.assertAlmostEqual(direct["q_conductor"], row["q_conductor"], delta=0.11)

    def test_a_chart_that_would_be_endless_is_refused(self):
        r = _call(f0_hz=F10, a_mm=22.86, b_mm=10.16, modes_upto_hz=200e9)
        self.assertEqual(r["reason_code"], "ARG_RANGE")
        self.assertIn("200", r["message"])
        self.assertIn("11454", r["message"])


class TestRoughness(unittest.TestCase):
    """One multiplier on this side too, and the same trap: Qc /= K, not / K^2."""

    def test_qc_is_divided_by_k_exactly_once(self):
        smooth = _val(f0_hz=F10, a_mm=22.86, b_mm=10.16)
        rough = _val(f0_hz=F10, a_mm=22.86, b_mm=10.16, roughness_um=2.0)
        k = rough["roughness"]["loss_factor"]
        self.assertAlmostEqual(k, 1.950452, places=6)
        self.assertAlmostEqual(rough["q_conductor"] * k, smooth["q_conductor"],
                               delta=0.2)
        self.assertAlmostEqual(rough["roughness"]["q_conductor_smooth"],
                               smooth["q_conductor"], delta=0.06)
        self.assertLess(k, 2.0)

    def test_more_roughness_costs_more_but_never_2x(self):
        prev = None
        for rq in (0.0, 0.5, 2.0, 5.0, 20.0):
            v = _val(f0_hz=F10, a_mm=22.86, b_mm=10.16, roughness_um=rq)
            k = v["roughness"]["loss_factor"]
            self.assertLess(k, 2.0)
            if prev is not None:
                self.assertGreater(k, prev - 1e-12)
            prev = k
        self.assertAlmostEqual(_val(f0_hz=F10, a_mm=22.86, b_mm=10.16,
                                    roughness_um=0.0)["roughness"]["loss_factor"],
                               1.0, places=12)

    def test_the_chart_is_roughened_too(self):
        v = _val(f0_hz=F10, a_mm=22.86, b_mm=10.16, modes_upto_hz=20e9,
                 roughness_um=2.0)
        row = [q for q in v["mode_chart"] if q["mode"] == "TE_1_0_1"][0]
        self.assertAlmostEqual(row["q_conductor"], v["q_conductor"], delta=0.11)


class TestRefusals(unittest.TestCase):
    """Each one is a mode someone would type, and every message names the fix."""

    def _code(self, **kw):
        r = _call(**kw)
        self.assertEqual(r["status"], "error", r)
        return r["reason_code"], r["message"]

    def test_tm_mn0_has_no_length(self):
        code, msg = self._code(f0_hz=16.1451e9, a_mm=22.86, b_mm=10.16, mode="TM110")
        self.assertEqual(code, "OUT_OF_VALIDITY")
        self.assertIn("ANY length", msg)
        self.assertIn("1.61451e+10", msg)

    def test_te_mn0_is_not_a_mode(self):
        code, msg = self._code(f0_hz=F10, a_mm=22.86, b_mm=10.16, mode="TE100")
        self.assertEqual(code, "ARG_INVALID")
        self.assertIn("p >= 1", msg)

    def test_te000(self):
        code, _ = self._code(f0_hz=F10, a_mm=22.86, b_mm=10.16, mode="TE000")
        self.assertEqual(code, "ARG_INVALID")

    def test_tm_with_a_zero_index(self):
        for mode in ("TM011", "TM001", "TM_1_0_1"):
            code, msg = self._code(f0_hz=F10, a_mm=22.86, b_mm=10.16, mode=mode)
            self.assertEqual(code, "ARG_INVALID")
            self.assertIn("m >= 1 and n >= 1", msg)

    def test_below_cutoff(self):
        code, msg = self._code(f0_hz=5e9, a_mm=22.86, b_mm=10.16, mode="TE101")
        self.assertEqual(code, "OUT_OF_VALIDITY")
        self.assertIn("6.55714e+09", msg)
        self.assertIn("imaginary", msg)

    def test_unreadable_spellings(self):
        for mode in ("TE2010", "XYZ101", "TE_1_1", "TE", "", "TE_1_x_1"):
            r = _call(f0_hz=F10, a_mm=22.86, b_mm=10.16, mode=mode)
            if mode == "":
                self.assertEqual(r["status"], "ok")   # empty means "default"
            else:
                self.assertEqual(r["reason_code"], "ARG_INVALID", mode)

    def test_negative_roughness(self):
        code, msg = self._code(f0_hz=F10, a_mm=22.86, b_mm=10.16, roughness_um=-1)
        self.assertEqual(code, "ARG_RANGE")
        self.assertIn("Rz is NOT converted", msg)

    def test_er_below_one(self):
        code, _ = self._code(f0_hz=F10, a_mm=22.86, b_mm=10.16, er=0.5)
        self.assertEqual(code, "ARG_RANGE")

    def test_underscored_integers_beyond_nine(self):
        """TE_10_0_1 is reachable only through the underscored spelling, and it
        is a real mode - so the tool must answer it rather than read '1001'."""
        r = _call(f0_hz=80e9, a_mm=22.86, b_mm=10.16, mode="TE_10_0_1")
        self.assertEqual(r["status"], "ok", r)
        self.assertEqual(r["value"]["mode"], "TE_10_0_1")
        self.assertGreater(r["value"]["q_conductor"], 0.0)


class TestTheSelfDescription(unittest.TestCase):
    """The envelope's `model` string is the tool's own claim about itself. It
    said "R/Q" for modes that carry no R/Q and "mode chart" for calls that did
    not ask for one - both are the kind of quiet lie that makes a reader trust
    a null field. Two suffixes, two independent conditions."""

    def test_r_over_q_is_only_claimed_when_it_is_reported(self):
        r101 = _call(f0_hz=F10, a_mm=22.86, b_mm=10.16)
        self.assertIsNotNone(r101["value"]["r_over_q_ohm"])
        self.assertIn("R/Q", r101["model"])
        self.assertNotIn("TE101 only", r101["model"])

        r011 = _call(f0_hz=F011, a_mm=22.86, b_mm=10.16, mode="TE011")
        self.assertIsNone(r011["value"]["r_over_q_ohm"])
        self.assertIn("R/Q is TE101 only", r011["model"])
        self.assertNotIn("wall-loss Qc, R/Q", r011["model"])

    def test_the_mode_chart_is_only_claimed_when_it_is_returned(self):
        plain = _call(f0_hz=F10, a_mm=22.86, b_mm=10.16)
        self.assertNotIn("mode_chart", plain["value"])
        self.assertNotIn("mode chart", plain["model"])

        charted = _call(f0_hz=F10, a_mm=22.86, b_mm=10.16, modes_upto_hz=20e9)
        self.assertEqual(charted["value"]["mode_chart_count"], 9)
        self.assertIn("mode chart", charted["model"])

if __name__ == "__main__":
    unittest.main()
