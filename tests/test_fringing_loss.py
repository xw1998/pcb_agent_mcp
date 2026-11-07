"""Air-gap fringing loss: five INDEPENDENT checks of the field, four of the slab.

Nothing here repeats the closed forms it is checking. The field of two charged
strips is checked against (a) a Biot-Savart quadrature of the same strips, (b)
div H = 0 and curl H = 0 by finite differences, (c) the exact edge value q/2 as
the leg gets wide, (d) the 1/z^2 tail of a dipole layer, and (e) the axial
integral against its own quadrature and against the exact no-fringing limit.
The slab eddy factor is checked against a complex 1-D solution built from
E = A sinh(kx), and against the textbook thin-sheet limit.

Why bother: the two closed forms were derived here, not copied from a paper
(the Roshen 2007/2008 papers are behind a paywall and their formulas are NOT
reproduced anywhere in this repository - see docs/75). A derivation without an
independent check is a guess with good formatting.
"""
import cmath
import math
import os
import sys
import unittest

sys.path.insert(0, os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "src"))

from pcb_agent_mcp.calc import magnetics as M   # noqa: E402
from pcb_agent_mcp import tools                 # noqa: E402

MU0 = 4e-7 * math.pi
TAU = 2 * math.pi


def hz(x, z, a, g, q):
    return M._fringe_hz(x, z, a, g, q)


def hx(x, z, a, g, q):
    return M._fringe_hx(x, z, a, g, q)


def hz_biot_savart(x, z, a, g, q, n=2001):
    """Independent: sum the field of two line charges along +-(x in [-a,a])."""
    def one(z0):
        tot = 0.0
        for i in range(n):
            xp = -a + 2 * a * i / (n - 1)
            w = 0.5 if i in (0, n - 1) else 1.0
            tot += w * (z - z0) / ((x - xp) ** 2 + (z - z0) ** 2)
        return tot * (2 * a / (n - 1))
    return q * (one(g / 2.0) - one(-g / 2.0)) / (2 * math.pi)


def hz_quad_axis(a, g, q=1.0, n=20001):
    h = g / (n - 1)
    s = 0.0
    for i in range(n):
        z = -g / 2 + i * h
        w = 0.5 if i in (0, n - 1) else 1.0
        s += w * hz(0.0, z, a, g, q)
    return s * h


def slab_reference(rho, f, t, n=4001):
    """p_vol / H_peak^2 from E = A*sinh(kx), integrated over the slab."""
    om = TAU * f
    delta = math.sqrt(2 * rho / (om * MU0))
    k = (1 + 1j) / delta
    amp = -1j * om * MU0 / (k * cmath.cosh(k * t / 2.0))
    h = t / (n - 1)
    tot = 0.0
    for i in range(n):
        x = -t / 2 + i * h
        w = 0.5 if i in (0, n - 1) else 1.0
        tot += w * 0.5 * (1.0 / rho) * abs(amp * cmath.sinh(k * x)) ** 2
    return tot * h / t


def slab_exact_rms(rho, f, t):
    """The library's exact factor, per H_rms^2 (peak convention is /2)."""
    delta = math.sqrt(2 * rho / (TAU * f * MU0))
    D = t / delta
    gfactor, _, _ = M._slab_eddy_factor(rho, f, t)
    thin = (TAU * f) ** 2 * MU0 ** 2 * t ** 2 / (12.0 * rho)
    return thin * gfactor, D


BASE = dict(freq_hz=1e5, gap_mm=1.0, leg_width_mm=10.0, leg_depth_mm=10.0,
            layers=4, thickness_mm=0.05, winding_height_mm=20.0,
            standoff_mm=0.2, current_rms_a=25.0)


class TestFieldClosedForms(unittest.TestCase):
    """The field is the thing everything else is built on: pin it four ways."""

    def test_matches_biot_savart_quadrature(self):
        a, g = 5e-3, 1e-3
        for (x, z) in ((6e-3, 0.2e-3), (6e-3, 3e-3), (4e-3, 0.0), (5.2e-3, 8e-3)):
            c = hz(x, z, a, g, 1.0)
            n = hz_biot_savart(x, z, a, g, 1.0)
            self.assertLess(abs(c - n) / max(abs(c), 1e-12), 3e-5,
                            "closed form drifted from the quadrature at %s" % ((x, z),))

    def test_divergence_and_curl_vanish(self):
        a, g, q = 5e-3, 1e-3, 1.0
        d = 1e-6
        worst_div = worst_curl = 0.0
        for (x, z) in ((4e-3, 3e-3), (6e-3, 0.3e-3), (8e-3, 2e-3), (5e-3, 30e-3)):
            div = ((hx(x + d, z, a, g, q) - hx(x - d, z, a, g, q)) / (2 * d) +
                   (hz(x, z + d, a, g, q) - hz(x, z - d, a, g, q)) / (2 * d))
            curl = ((hz(x + d, z, a, g, q) - hz(x - d, z, a, g, q)) / (2 * d) -
                    (hx(x, z + d, a, g, q) - hx(x, z - d, a, g, q)) / (2 * d))
            scale = max(abs(hz(x, z, a, g, q)), abs(hx(x, z, a, g, q))) / g
            worst_div = max(worst_div, abs(div) / scale)
            worst_curl = max(worst_curl, abs(curl) / scale)
        self.assertLess(worst_div, 1e-5)
        self.assertLess(worst_curl, 1e-5)

    def test_edge_value_tends_to_half_the_pole_density(self):
        # a pole face is a sheet of charge: just outside it the field is q/2, and
        # the approach to q/2 with a/g is what says the strip integral is right
        prev = 0.0
        for ratio in (100.0, 1000.0, 10000.0):
            g = 1e-3
            a = ratio * g
            v = abs(hz(a, 0.0, a, g, 1.0))
            self.assertLess(abs(v - 0.5) / 0.5, 2.0 / ratio)
            self.assertGreater(v, prev)
            prev = v

    def test_far_field_falls_like_one_over_z_squared(self):
        a, g = 5e-3, 1e-3
        vals = [(z, abs(hz(0.0, z, a, g, 1.0))) for z in (40e-3, 80e-3, 160e-3, 320e-3)]
        for (z1, v1), (z2, v2) in zip(vals, vals[1:]):
            slope = math.log(v2 / v1) / math.log(z2 / z1)
            self.assertLess(abs(slope + 2.0), 0.06,
                            "a dipole layer must fall as 1/z^2, got %.4f" % slope)

    def test_symmetry(self):
        a, g, q = 5e-3, 1e-3, 1234.0
        for (x, z) in ((6e-3, 0.3e-3), (3e-3, 2e-3), (7e-3, 9e-3)):
            self.assertAlmostEqual(hz(x, z, a, g, q), hz(x, -z, a, g, q), places=9)
            self.assertAlmostEqual(hx(x, z, a, g, q), -hx(x, -z, a, g, q), places=9)
        # Hx vanishes on the mid plane: that is why H_z alone is the driver of
        # the closed ring current and H_x is the one this model does not carry
        self.assertEqual(hx(6e-3, 0.0, a, g, q), 0.0)

    def test_radial_field_is_finite_on_the_pole_edge(self):
        a, g = 5e-3, 1e-3
        v = hx(a, g / 2.0, a, g, 1.0)       # exactly the log-singular corner
        self.assertTrue(math.isfinite(v))
        self.assertGreater(abs(v), 1.0)


class TestGapIntegral(unittest.TestCase):

    def test_closed_form_matches_quadrature(self):
        for (a, g) in ((5e-3, 0.2e-3), (5e-3, 1e-3), (2e-3, 1e-3), (10e-3, 1e-3)):
            c = M._fringe_gap_mmf(a, g)
            n = abs(hz_quad_axis(a, g, 1.0))
            self.assertLess(abs(c - n) / c, 5e-5)

    def test_no_fringing_limit(self):
        # g/a -> 0: an infinitely wide pole face leaks nothing and the axial
        # integral is exactly q*g
        g = 1e-3
        for ratio in (1e3, 1e4, 1e5):
            frac = M._fringe_gap_mmf(ratio * g, g) / g
            # the shortfall is (g/a)/pi to first order, not O((g/a)^2): the
            # tapered edge of an infinite strip contributes at that order
            self.assertAlmostEqual((1.0 - frac) * math.pi / (1.0 / ratio), 1.0,
                                   places=2)
            self.assertGreater(frac, 0.99)

    def test_a_narrow_gap_concentrates_the_pole_density(self):
        # the shortfall below q*g is MMF the pole face has to make up
        g = 1e-3
        fracs = [M._fringe_gap_mmf(a, g) / g for a in (2e-3, 3e-3, 5e-3, 10e-3)]
        for lo, hi in zip(fracs, fracs[1:]):
            self.assertLess(lo, hi)


class TestSlabFactor(unittest.TestCase):

    def test_exact_against_complex_integral(self):
        rho = 1.724e-8
        for (f, t) in ((1e5, 0.005e-3), (1e5, 0.05e-3), (1e5, 0.2e-3),
                       (1e5, 1.0e-3), (1e6, 0.05e-3)):
            e, _ = slab_exact_rms(rho, f, t)
            ref = 2.0 * slab_reference(rho, f, t)      # peak -> rms
            self.assertLess(abs(e - ref) / ref, 5e-6,
                            "slab factor vs complex integral at f=%g t=%g" % (f, t))

    def test_thin_limit_is_the_textbook_form(self):
        rho = 1.724e-8
        for t in (1e-4, 1e-5, 1e-6):
            e, D = slab_exact_rms(rho, 1e5, t)
            thin = (TAU * 1e5) ** 2 * MU0 ** 2 * t ** 2 / (12.0 * rho)
            self.assertLess(abs(e / thin - 1.0), 3e-3)
            self.assertLess(D, 0.5)

    def test_series_branch_agrees_with_the_closed_form(self):
        rho = 1.724e-8
        # just either side of the branch switch
        lo, _, _ = M._slab_eddy_factor(rho, 1e5, 1e-8 * 0.99)
        hi, _, _ = M._slab_eddy_factor(rho, 1e5, 1e-8 * 1.01)
        self.assertLess(abs(lo - hi), 1e-9)

    def test_thick_foil_stops_growing_like_t_squared(self):
        # the thin-sheet form over-states a 1 mm foil at 100 kHz by ~36x, which
        # is exactly the reason this tool carries the exact factor
        rho = 1.724e-8
        e, D = slab_exact_rms(rho, 1e5, 1e-3)
        thin = (TAU * 1e5) ** 2 * MU0 ** 2 * (1e-3) ** 2 / (12.0 * rho)
        self.assertGreater(D, 4.0)
        self.assertLess(thin / e, 20.0)
        self.assertGreater(thin / e, 15.0)


class TestTool(unittest.TestCase):

    def test_anchor_numbers(self):
        v = M.fringing_loss(**BASE)["value"]
        self.assertEqual(v["ampere_turns"], 100.0)
        self.assertAlmostEqual(v["gap_mmf_fraction"], 0.936756, places=6)
        self.assertAlmostEqual(v["pole_concentration"], 1.067514, places=5)
        self.assertAlmostEqual(v["skin_depth_um"], 208.9723, places=3)
        self.assertAlmostEqual(v["slab_factor"], 0.999867, places=6)
        self.assertAlmostEqual(v["total_loss_w"], 0.0653932, places=6)
        self.assertAlmostEqual(v["fringe_resistance_ohm"], 1.04629e-4, places=8)
        self.assertAlmostEqual(v["loss_over_dc"], 0.035784, places=6)
        self.assertEqual(len(v["layers"]), 4)
        self.assertAlmostEqual(v["layers"][0]["field_weight_a2_m"], 1.2183e6,
                               delta=2e3)
        # the loss falls off with radius: the field is a near field
        losses = [r["loss_w"] for r in v["layers"]]
        self.assertEqual(losses, sorted(losses, reverse=True))

    def test_dc_resistance_is_the_geometry(self):
        v = M.fringing_loss(**BASE)["value"]
        rho = 1.724e-8
        expect = sum(rho * (r["perimeter_mm"] * 1e-3) / (20e-3 * 0.05e-3)
                     for r in v["layers"])
        self.assertAlmostEqual(v["dc_resistance_ohm"] / expect, 1.0, places=5)

    def test_ampere_turns_and_current_agree(self):
        a = M.fringing_loss(**BASE)["value"]
        b = dict(BASE)
        b.pop("current_rms_a")
        b["ampere_turns"] = 100.0
        v = M.fringing_loss(**b)["value"]
        self.assertAlmostEqual(v["total_loss_w"], a["total_loss_w"], places=9)
        self.assertAlmostEqual(v["fringe_resistance_ohm"], a["fringe_resistance_ohm"],
                               places=9)

    def test_thin_foil_loss_goes_like_f_squared(self):
        base = dict(BASE, thickness_mm=0.01, layers=1)
        lo = M.fringing_loss(**dict(base, freq_hz=1e4))["value"]["total_loss_w"]
        hi = M.fringing_loss(**dict(base, freq_hz=2e4))["value"]["total_loss_w"]
        self.assertAlmostEqual(hi / lo, 4.0, places=2)

    def test_more_standoff_is_cheaper(self):
        vals = [M.fringing_loss(**dict(BASE, standoff_mm=s))["value"]["total_loss_w"]
                for s in (0.0, 0.2, 0.5, 1.0, 2.0)]
        for lo, hi in zip(vals, vals[1:]):
            self.assertLess(hi, lo)
        self.assertLess(vals[-1] / vals[0], 0.25)

    def test_bigger_gap_at_the_same_mmf_is_cheaper(self):
        vals = [M.fringing_loss(**dict(BASE, gap_mm=g))["value"]["total_loss_w"]
                for g in (0.2, 0.5, 1.0, 2.0)]
        for lo, hi in zip(vals, vals[1:]):
            self.assertLess(hi, lo)

    def test_insulation_pushes_the_stack_out_and_costs_less(self):
        tight = M.fringing_loss(**BASE)["value"]["total_loss_w"]
        loose = M.fringing_loss(**dict(BASE, insulation_mm=0.1))["value"]["total_loss_w"]
        self.assertLess(loose, tight)

    def test_height_barely_matters(self):
        # the integrand is 1/z^4 past the near field, so this is a physical
        # statement, not an artifact: the loss lives within a few gap widths
        vals = [M.fringing_loss(**dict(BASE, winding_height_mm=h))["value"]["total_loss_w"]
                for h in (6.0, 20.0, 40.0)]
        self.assertLess(max(vals) / min(vals) - 1.0, 0.03)

    def test_temperature_and_resistivity(self):
        t20 = M.fringing_loss(**dict(BASE, temperature_c=20.0))["value"]["total_loss_w"]
        a = M.fringing_loss(**BASE)["value"]["total_loss_w"]
        self.assertAlmostEqual(t20, a, places=9)
        hot = M.fringing_loss(**dict(BASE, temperature_c=100.0))["value"]
        self.assertLess(hot["total_loss_w"], a)
        self.assertAlmostEqual(hot["total_loss_w"] / a, 0.7608, places=3)
        r = M.fringing_loss(**dict(BASE, resistivity_ohm_m=1.724e-8))["value"]
        self.assertAlmostEqual(r["total_loss_w"], a, places=9)

    def test_probes_reproduce_a_layer(self):
        v = M.fringing_loss(**dict(BASE, probe_radii_mm=[0.225, 1.0]))["value"]
        self.assertAlmostEqual(v["probes"][0]["field_weight_a2_m"],
                               v["layers"][0]["field_weight_a2_m"], delta=2e3)
        self.assertLess(v["probes"][1]["field_weight_a2_m"],
                        v["probes"][0]["field_weight_a2_m"])
        self.assertAlmostEqual(v["probes"][0]["peak_field_a_m"],
                               v["layers"][0]["peak_field_a_m"], places=1)

    def test_honesty_fields_are_present_and_measure_the_radial_part(self):
        v = M.fringing_loss(**BASE)["value"]
        blob = " ".join(v["not_modelled"])
        for key in ("radial", "round wire", "superposition", "permeability",
                    "non-sinusoidal", "3-D"):
            self.assertIn(key.lower(), blob.lower())
        self.assertAlmostEqual(v["radial_over_axial_at_0p2g"], 0.3213, places=3)
        self.assertIn("0.32", blob)

    def test_kelvin_style_assumptions_name_the_geometry(self):
        v = M.fringing_loss(**BASE)
        self.assertIn("one shell = one turn", v["assumptions"]["geometry"])
        self.assertIn("perfect magnetic conductors", v["assumptions"]["field"])

    def test_reserved_warning_when_the_gap_is_not_a_gap(self):
        v = M.fringing_loss(**dict(BASE, gap_mm=12.0))["value"]
        self.assertTrue(v["warnings"])
        self.assertIn("half the leg width", v["warnings"][0])
        ok = M.fringing_loss(**BASE)["value"]
        self.assertEqual(ok["warnings"], [])


class TestRefusals(unittest.TestCase):

    def code(self, **kw):
        r = M.fringing_loss(**dict(BASE, **kw))
        self.assertEqual(r["status"], "error", "expected a refusal: %r" % (kw,))
        return r["reason_code"]

    def test_bad_numbers(self):
        self.assertEqual(self.code(freq_hz=0), "ARG_RANGE")
        self.assertEqual(self.code(gap_mm=0), "ARG_RANGE")
        self.assertEqual(self.code(gap_mm=-1), "ARG_RANGE")
        self.assertEqual(self.code(leg_width_mm=0), "ARG_RANGE")
        self.assertEqual(self.code(leg_depth_mm=0), "ARG_RANGE")
        self.assertEqual(self.code(thickness_mm=0), "ARG_RANGE")
        self.assertEqual(self.code(winding_height_mm=0), "ARG_RANGE")
        self.assertEqual(self.code(standoff_mm=-0.1), "ARG_RANGE")
        self.assertEqual(self.code(insulation_mm=-0.1), "ARG_RANGE")
        self.assertEqual(self.code(layers=0), "ARG_RANGE")
        self.assertEqual(self.code(layers=201), "ARG_RANGE")
        self.assertEqual(self.code(current_rms_a=0), "ARG_RANGE")
        self.assertEqual(self.code(resistivity_ohm_m=0), "ARG_RANGE")
        self.assertEqual(self.code(temperature_c=-300), "ARG_RANGE")

    def test_mmf_must_be_given_exactly_once(self):
        kw = dict(BASE)
        kw.pop("current_rms_a")
        r = M.fringing_loss(**kw)
        self.assertEqual(r["reason_code"], "ARG_MISSING")
        self.assertIn("exactly one", r["message"])
        r2 = M.fringing_loss(**dict(BASE, ampere_turns=100.0))
        self.assertEqual(r2["reason_code"], "ARG_MISSING")

    def test_resistivity_and_temperature_are_exclusive(self):
        r = M.fringing_loss(**dict(BASE, resistivity_ohm_m=1.7e-8,
                                   temperature_c=80.0))
        self.assertEqual(r["reason_code"], "ARG_INVALID")
        self.assertIn("not both", r["message"])

    def test_probe_list_is_checked(self):
        self.assertEqual(self.code(probe_radii_mm=[-1.0]), "ARG_RANGE")
        r = M.fringing_loss(**dict(BASE, probe_radii_mm="0.5"))
        self.assertEqual(r["reason_code"], "ARG_INVALID")

    def test_non_numeric(self):
        self.assertEqual(self.code(gap_mm="wide"), "ARG_INVALID")
        self.assertEqual(self.code(layers="four"), "ARG_INVALID")


class TestRegistration(unittest.TestCase):

    def test_tool_is_registered(self):
        self.assertIn("magnetics_fringing_loss", tools.TOOLS)
        d = tools.TOOLS["magnetics_fringing_loss"]
        self.assertIn("fringing", d["description"].lower())
        req = d["inputSchema"]["required"]
        for key in ("freq_hz", "gap_mm", "leg_width_mm", "leg_depth_mm", "layers",
                    "thickness_mm", "winding_height_mm"):
            self.assertIn(key, req)

    def test_the_handler_path_works_like_the_function(self):
        h = tools.TOOLS["magnetics_fringing_loss"]["handler"]
        r = h(dict(BASE))
        self.assertEqual(r["status"], "ok")
        self.assertAlmostEqual(r["value"]["total_loss_w"], 0.0653932, places=6)
        # the handler supplies the two defaults the schema does not
        r2 = h({"freq_hz": 1e5, "gap_mm": 1.0, "leg_width_mm": 10.0,
                "leg_depth_mm": 10.0, "layers": 4, "thickness_mm": 0.05,
                "winding_height_mm": 20.0, "ampere_turns": 100.0})
        self.assertEqual(r2["status"], "ok")

    def test_tool_count(self):
        # 175 = 174 + magnetics_material_core_loss in 0.0.74
        # 179 = 178 + magnetics_core_loss_audit in 0.0.81 (scoring a triple against
        # the vendor's printed points is a different question from evaluating a fit)
        # 180 = 179 + thermal_spreading in 0.0.85 (a rectangular heat source's
        # spreading resistance is its own question; thermal_stack stays strictly 1-D)
        # 223 = 220 + the three routing-discipline tools in 0.0.122
        self.assertEqual(len(tools.TOOLS), 225)

    def test_the_capability_list_knows_it(self):
        h = tools.TOOLS["calc_capabilities"]["handler"]
        caps = h({})
        self.assertEqual(caps["status"], "ok")
        self.assertIn("magnetics_fringing_loss", caps["available"])
        # ... and the honesty list must not claim the gap loss is unmodelled
        # anywhere in this tool's own description
        d = tools.TOOLS["magnetics_fringing_loss"]["description"].lower()
        self.assertNotIn("gap/fringing field (usually the biggest", d)


if __name__ == "__main__":
    unittest.main()
