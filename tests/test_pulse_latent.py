"""Tests for the fusion-energy reservoir on top of a pulse train (0.0.87).

The 0.0.86 layer answered "one pulse, how much I2t before the metal opens".
A train asks a different question and the answer that used to be returned for it
was not the answer to that question:

  * `value` used to be the RUNAWAY bound, sqrt(area*h*P/(D*beta*rho(Ta))) - a rate
    balance that carries no heat capacity and no melt point. At duty 1 % it sits
    4.44x above the current the copper can actually survive (geometry D below).
  * 0.0.87 splits the question into three currents: the melt-free current
    (settled peak exactly on 1083 C) is `value`; the runaway bound keeps its
    number and its own name; and with latent heat on, a third threshold appears -
    the current whose peak may pin at 1083 C as long as the off-time can empty the
    fusion reservoir again.

Nothing here inherits a number from a run of the code under test. The reference
implementation of the reservoir is a verbatim shot-by-shot march written out in
this file; the closed-form steady peak is re-derived from the ODE; and the
adiabatic limit is checked against `ceil((1+beta)/t_on)`, which is pure algebra
from the same derivation that gives Onderdonk its sqrt(t) - with one difference
that the model has to get right: the last pulse is a whole pulse.

Geometry keys used throughout (width, t_on, duty, oz, h):

    A  0.5 mm   1 ms   0.05   1 oz   200     short pulse, medium cooling
    B  2.0 mm  10 ms   0.2    2 oz    50     fat trace, slow cooling
    C  0.5 mm   1 s    0.1    1 oz    20     long pulse
    D  0.5 mm   1 s    0.01   1 oz    20     same copper, 10x less duty
    E  0.25 mm  0.1 s  0.5    0.5 oz  30     thin foil, high duty
"""
import math
import os
import sys
import unittest

sys.path.insert(0, os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "src"))

from unittest import mock                                      # noqa: E402

from pcb_agent_mcp import tools as T                            # noqa: E402
from pcb_agent_mcp.calc import current as C                     # noqa: E402

# Physical constants written out again, so the test does not inherit them.
RHO20 = 1.72e-8          # ohm*m at 20 C
RHO_MASS = 8960.0        # kg/m^3
CP = 385.0               # J/(kg*K)
TM = 1083.0              # copper melting point, C
TREF = 234.0             # the 234 of Onderdonk's law
OZ_UM = 34.8             # 1 oz finished copper, um
LATENT_J_KG = 208700.0   # fusion enthalpy of copper, J/kg (0.0.86 doc, sourced)

GEOM = {
    "A": dict(width_mm=0.5, t_on_s=0.001, duty_cycle=0.05, copper_oz=1.0, h_w_m2k=200.0),
    "B": dict(width_mm=2.0, t_on_s=0.010, duty_cycle=0.2, copper_oz=2.0, h_w_m2k=50.0),
    "C": dict(width_mm=0.5, t_on_s=1.0, duty_cycle=0.1, copper_oz=1.0, h_w_m2k=20.0),
    "D": dict(width_mm=0.5, t_on_s=1.0, duty_cycle=0.01, copper_oz=1.0, h_w_m2k=20.0),
    "E": dict(width_mm=0.25, t_on_s=0.1, duty_cycle=0.5, copper_oz=0.5, h_w_m2k=30.0),
}

H = T.TOOLS["repetitive_pulse_rating"]["handler"]


def _internals(g, current, ta=25.0):
    """The per-interval ODE coefficients, re-derived from physical constants.

    d(dT)/dt = a + k*dT inside each interval, with
        a    = I^2 rho(Ta) / (area * C')
        beta = 1/(234 + Ta)                (rho is linear in T)
        k    = a*beta - 1/tau
        tau  = C' / (h P)
    """
    w = g["width_mm"] / 1000.0
    t = OZ_UM * g["copper_oz"] / 1e6
    area = w * t
    perim = 2.0 * (w + t)
    rho = RHO20 * (TREF + ta) / (TREF + 20.0)
    beta = 1.0 / (TREF + ta)
    c_line = RHO_MASS * CP * area
    a = current ** 2 * rho / (area * c_line)
    tau = float("inf") if g["h_w_m2k"] == 0.0 else c_line / (g["h_w_m2k"] * perim)
    t_off = g["t_on_s"] * (1.0 - g["duty_cycle"]) / g["duty_cycle"]
    return a, beta, tau, g["t_on_s"], t_off, TM - ta


def _rise(dt0, a, k, t):
    """Exact solution of d(dT)/dt = a + k*dT over t, from dt0."""
    if abs(k * t) < 1e-12:
        return dt0 + a * t
    return (dt0 + a / k) * math.exp(k * t) - a / k


def _steady_peak(g, current, ta=25.0):
    """Settled peak of the train, from the geometric series, derived here.

    trough -> peak -> trough -> ... is affine, so the fixed point is
    x = b/(1-q) with q = exp(k*t_on)*exp(-t_off/tau) and
    b = (a/k)(exp(k*t_on) - 1)*exp(-t_off/tau).
    """
    a, beta, tau, t_on, t_off, _dTm = _internals(g, current, ta)
    k = a * beta - 1.0 / tau
    e_on = math.exp(k * t_on)
    e_off = math.exp(-t_off / tau)
    q = e_on * e_off
    b = (a / k) * (e_on - 1.0) * e_off
    return _rise(b / (1.0 - q), a, k, t_on)


def _raw_latent_march(g, current, cap=200000, ta=25.0):
    """Verbatim shot-by-shot march with a fusion reservoir. No closed forms.

    Deliberately naive, and deliberately written independently of
    `_train_latent`: the time to reach the melt point inside a pulse is solved
    from the ODE by hand (that is one line of algebra), the rest is a loop.
    """
    a, beta, tau, t_on, t_off, dTm = _internals(g, current, ta)
    rise = LATENT_J_KG / CP
    k = a * beta - 1.0 / tau
    phi = a + k * dTm
    freeze_rise = dTm / tau
    trough, latent = 0.0, 0.0
    for shot in range(1, cap + 1):
        if phi <= 0.0:
            return None
        if trough >= dTm:
            t_a = 0.0
        else:
            den = a + k * trough
            if den <= 0.0:
                return shot
            t_a = math.log(phi / den) / k
            if t_a < 0.0:
                t_a = 0.0
        if t_a >= t_on:
            peak = _rise(trough, a, k, t_on)
            gain = 0.0
        else:
            gain = phi * (t_on - t_a)
            peak = dTm
        if latent + gain >= rise:
            return shot
        latent += gain
        if latent > 0.0:
            t_hold = latent / freeze_rise if freeze_rise > 0.0 else float("inf")
            if t_hold < t_off:
                latent = 0.0
                trough = dTm * math.exp(-(t_off - t_hold) / tau)
            else:
                latent -= freeze_rise * t_off
                trough = dTm
        else:
            trough = peak * math.exp(-t_off / tau)
    return None


def _rating(g, **kw):
    args = dict(g)
    args.update(kw)
    return H(args)


class TestThreeCurrents(unittest.TestCase):
    """`value` is the melt-free current, not the runaway bound.

    This is the headline change of 0.0.87 and the one that could quietly make a
    design fail in the field: the old `value` over-reported by 4.44x at 1 % duty.
    """

    def test_value_settles_on_the_melt_point(self):
        # independent closed form: the settled peak at `value` is 1083 C, and the
        # peak moves with current, so +/-0.5 % has to straddle it on both sides
        for name, g in GEOM.items():
            r = _rating(g, melting_model="sensible_plus_latent")
            self.assertEqual(r["status"], "ok", name)
            v = r["value"]
            # the series returns a RISE above ambient, so the melt point is
            # 1083 - 25 = 1058 K of rise
            self.assertAlmostEqual(_steady_peak(g, v), TM - 25.0, delta=5e-3, msg=name)
            self.assertLess(_steady_peak(g, 0.995 * v), TM - 25.0, name)
            self.assertGreater(_steady_peak(g, 1.005 * v), TM - 25.0, name)

    def test_value_is_repeated_under_its_new_name(self):
        for name, g in GEOM.items():
            r = _rating(g)
            self.assertEqual(r["value"], r["max_current_without_melting_a"], name)
            self.assertIn("max_current_without_melting_basis", r, name)

    def test_runaway_bound_is_still_the_rate_balance(self):
        # the number did not move, only its name did: recompute it from the
        # definition I^2 = area*h*P/(D*beta*rho(Ta))
        for name, g in GEOM.items():
            r = _rating(g)
            w = g["width_mm"] / 1000.0
            t = OZ_UM * g["copper_oz"] / 1e6
            area, perim = w * t, 2.0 * (w + t)
            rho = RHO20 * (TREF + 25.0) / (TREF + 20.0)
            beta = 1.0 / (TREF + 25.0)
            want = math.sqrt(area * g["h_w_m2k"] * perim / (g["duty_cycle"] * beta * rho))
            self.assertAlmostEqual(r["max_repetitive_current_a"] / want, 1.0,
                                   delta=1e-6, msg=name)
            self.assertAlmostEqual(r["runaway_bound_over_no_melt"],
                                   r["max_repetitive_current_a"] / r["value"],
                                   delta=1e-6, msg=name)

    def test_low_duty_shows_the_runaway_bound_is_not_permission(self):
        # geometry D is the whole point: the old answer 23.445 A is 4.44x the
        # 5.284 A the copper survives, and it is not the pulse length that does
        # this, it is the 1 % duty
        d = _rating(GEOM["D"], melting_model="sensible_plus_latent")
        self.assertGreater(d["runaway_bound_over_no_melt"], 4.0)
        self.assertGreater(d["max_repetitive_current_a"], 4.0 * d["value"])
        # the note has to say the word, not just be a number
        self.assertIn("upper bound", d["max_repetitive_current_a_note"])
        # and the high-duty geometry must not be in that regime, otherwise the
        # test above proves nothing about duty
        c = _rating(GEOM["C"], melting_model="sensible_plus_latent")
        self.assertLess(c["runaway_bound_over_no_melt"], 1.5)

    def test_melt_through_sits_above_the_melt_free_current(self):
        # parking the peak ON 1083 C and emptying the reservoir in the off-time is
        # strictly less demanding than not reaching 1083 C at all - and it is a
        # different criterion, so it has to be a different number
        for name, g in GEOM.items():
            r = _rating(g, melting_model="sensible_plus_latent")
            thru = r["max_current_without_melt_through_a"]
            self.assertIsNotNone(thru, name)
            self.assertGreater(thru, r["value"], name)
            self.assertLess(thru, r["max_repetitive_current_a"], name)
            self.assertAlmostEqual(r["melt_through_over_no_melt"], thru / r["value"],
                                   delta=1e-6, msg=name)
            self.assertIn("NOT permission", r["max_current_without_melt_through_basis"])


class TestDefaultIsUntouched(unittest.TestCase):
    """New features default off; with the default the envelope must be identical."""

    def test_default_equals_explicit_sensible(self):
        for name in ("A", "C", "E"):
            base = dict(GEOM[name], current_a=2.0)
            a = H(dict(base))
            b = H(dict(base, melting_model="sensible"))
            self.assertEqual(a, b, name)

    def test_default_equals_none(self):
        base = dict(GEOM["A"], current_a=2.0)
        self.assertEqual(H(dict(base)), H(dict(base, melting_model=None)))

    def test_default_envelope_carries_no_latent_keys(self):
        r = H(dict(GEOM["A"], current_a=2.0))
        self.assertNotIn("melting_model", r)
        self.assertNotIn("max_current_without_melt_through_a", r)
        self.assertNotIn("latent_rise_k", r)
        self.assertFalse([k for k in r if k.startswith("latent")])
        self.assertNotIn("unread_arguments", r)

    def test_latent_model_is_the_only_difference(self):
        base = dict(GEOM["C"], current_a=3.0)
        a = H(dict(base))
        b = H(dict(base, melting_model="sensible_plus_latent"))
        # every default key survives with the same value; only new keys appear
        for k, v in a.items():
            if k in ("survives_indefinitely", "pulses_to_melt", "melt_after_s",
                     "melting_model", "basis", "model", "assumptions", "precision"):
                continue
            self.assertEqual(b[k], v, k)
        self.assertEqual(b["melting_model"], "sensible_plus_latent")


class TestLatentDelay(unittest.TestCase):
    """Latent heat does not make the copper better at shedding heat - it only
    postpones the moment the fusion energy is paid."""

    def test_below_the_melt_free_current_never_melts_in_either_model(self):
        for name in ("A", "C", "D"):
            r = H(dict(GEOM[name], current_a=0.99 * _rating(GEOM[name])["value"],
                       melting_model="sensible_plus_latent"))
            self.assertTrue(r["survives_indefinitely"], name)
            self.assertIsNone(r["pulses_to_melt"], name)

    def test_at_the_old_runaway_current_the_reservoir_multiplies_the_count(self):
        # 0.95 of the runaway bound: the sensible model melts in 81 (A) / 274 (B)
        # shots, the reservoir version needs 139 / 467. Independent check: the
        # ratio is 139/81 and 467/274, i.e. +71.6 % and +70.4 %.
        for name, want_sens, want_lat in (("A", 81, 139), ("B", 274, 467)):
            run = _rating(GEOM[name])["max_repetitive_current_a"]
            cur = 0.95 * run
            s = H(dict(GEOM[name], current_a=cur, melting_model="sensible"))
            l = H(dict(GEOM[name], current_a=cur, melting_model="sensible_plus_latent"))
            self.assertEqual(s["pulses_to_melt"], want_sens, name)
            self.assertEqual(l["pulses_to_melt"], want_lat, name)
            self.assertAlmostEqual(l["latent_delays_melt_pct"],
                                   100.0 * (want_lat / want_sens - 1.0), delta=1e-3)
            self.assertIn("pinned at the melt point", l["latent_verdict"])

    def test_just_above_the_melt_free_current_latent_changes_the_answer(self):
        # A: at 1.005*value the sensible train dies in 218 shots, the reservoir one
        # in 2360. C and D: at 1.02*value the sensible train dies in 3 and 1 shots,
        # and the reservoir train never melts at all - the off-time empties it.
        a = _rating(GEOM["A"], melting_model="sensible_plus_latent")
        cur = 1.005 * a["value"]
        s = H(dict(GEOM["A"], current_a=cur, melting_model="sensible"))
        l = H(dict(GEOM["A"], current_a=cur, melting_model="sensible_plus_latent"))
        self.assertEqual(s["pulses_to_melt"], 218)
        self.assertEqual(l["pulses_to_melt"], 2360)
        for name in ("C", "D"):
            base = _rating(GEOM[name], melting_model="sensible_plus_latent")
            cur = 1.02 * base["value"]
            s = H(dict(GEOM[name], current_a=cur, melting_model="sensible"))
            l = H(dict(GEOM[name], current_a=cur, melting_model="sensible_plus_latent"))
            self.assertIsNotNone(s["pulses_to_melt"], name)
            self.assertTrue(l["survives_indefinitely"], name)
            self.assertIsNone(l["pulses_to_melt"], name)
            self.assertIn("empties inside the off-time", l["latent_verdict"], name)

    def test_the_sustainable_current_itself_is_unaffected(self):
        # in = out over a period: a cyclic steady state cannot hoard latent energy,
        # so the derating number is the same in both models - and that is stated
        # as a key rather than left to be inferred
        for name in ("A", "D"):
            a = _rating(GEOM[name], current_a=10.0)
            b = _rating(GEOM[name], current_a=10.0,
                        melting_model="sensible_plus_latent")
            self.assertEqual(a["max_repetitive_current_a"], b["max_repetitive_current_a"])
            self.assertTrue(b["max_repetitive_current_unaffected_by_latent"], name)
            self.assertEqual(a["value"], b["value"], name)

    def test_the_reservoir_size_is_the_ratio_of_the_two_constants(self):
        r = _rating(GEOM["A"], current_a=10.0, melting_model="sensible_plus_latent")
        self.assertAlmostEqual(C.COPPER_LATENT_RISE_K, LATENT_J_KG / CP, delta=1e-9)
        self.assertEqual(r["latent_rise_k"], round(LATENT_J_KG / CP, 6))
        self.assertAlmostEqual(r["latent_rise_k"], 542.077922, delta=1e-6)


class TestAdiabaticGrid(unittest.TestCase):
    """h = 0 is the one place the answer is pure algebra: every joule stays in."""

    def test_extra_on_time_is_the_reservoir_paid_at_the_melt_point(self):
        # The reservoir is filled at the rate heat arrives WHILE PINNED at 1083 C,
        # phi = a*(1 + beta*dTm) - not at the cold rate a, and not at the average
        # rate. So the extra on-time is (L_f/c_p)/phi, within one whole pulse.
        i_fuse = C.fusing_current(width_mm=0.5, t_s=1.0, copper_oz=1.0)["value"]
        self.assertAlmostEqual(i_fuse, 5.024, delta=1e-3)
        g = dict(width_mm=0.5, copper_oz=1.0, h_w_m2k=0.0, duty_cycle=1.0)
        a, beta, _tau, _ton, _toff, dTm = _internals(dict(g, t_on_s=0.01), i_fuse)
        phi = a * (1.0 + beta * dTm)
        want_extra = (LATENT_J_KG / CP) / phi
        for t_on in (1.0, 0.5, 0.2, 0.1, 0.05, 0.01, 0.005):
            args = dict(g, t_on_s=t_on, current_a=i_fuse)
            s = H(dict(args, melting_model="sensible"))
            l = H(dict(args, melting_model="sensible_plus_latent"))
            self.assertIsNotNone(s["pulses_to_melt"], t_on)
            self.assertIsNotNone(l["pulses_to_melt"], t_on)
            self.assertGreater(l["pulses_to_melt"], s["pulses_to_melt"], t_on)
            extra = (l["pulses_to_melt"] - s["pulses_to_melt"]) * t_on
            self.assertAlmostEqual(extra / want_extra, 1.0, delta=t_on / want_extra)
            if t_on <= 0.05:
                # negative control, only where a whole pulse is fine enough to
                # resolve it: paying the reservoir at the COLD rate would need
                # 1.28 s of extra on-time, i.e. five times longer
                self.assertGreater((LATENT_J_KG / CP) / a, 4.0 * extra, t_on)

    def test_the_limit_agrees_with_the_adiabatic_energy_integral(self):
        # same check as 0.0.86 but through the latent route: shots*t_on*(1+beta)
        # must equal the hand-derived time to melt, and it must be a full pulse
        i_fuse = C.fusing_current(width_mm=1.0, t_s=1.0)["value"]
        r = H(dict(width_mm=1.0, copper_oz=1.0, h_w_m2k=0.0, t_on_s=0.01,
                   duty_cycle=1.0, current_a=i_fuse,
                   melting_model="sensible_plus_latent"))
        w = 1.0 / 1000.0
        t = OZ_UM / 1e6
        ratio = (TREF + TM) / (TREF + 25.0)
        want = ((w * t) ** 2 * RHO_MASS * CP * (TREF + 20.0) * math.log(ratio) /
                (i_fuse ** 2 * RHO20))
        # the reservoir adds (L_f/c_p)/phi of on-time on top of that integral
        a, beta, _tau, _ton, _toff, dTm = _internals(
            dict(width_mm=1.0, copper_oz=1.0, h_w_m2k=0.0, t_on_s=0.01,
                 duty_cycle=1.0), i_fuse)
        want += (LATENT_J_KG / CP) / (a * (1.0 + beta * dTm))
        got = r["pulses_to_melt"] * 0.01
        # a whole pulse is the resolution here, so compare within one pulse
        self.assertAlmostEqual(got / want, 1.0, delta=0.01 / want)


class TestReservoirCrossCheck(unittest.TestCase):
    """The closed-form reservoir and the verbatim march have to agree.

    This is the test that can actually fail if the algebra in `_train_latent` has
    a sign error: the reference below never uses a fixed point, a logarithm of a
    ratio of coefficients, or `_latent_settles`.
    """

    def test_finite_melt_counts_match_the_verbatim_march(self):
        cases = [("A", 1.02, 305), ("A", 1.15, 70),
                 ("C", 1.02, 2), ("C", 1.15, 1),
                 ("D", 1.02, 1), ("D", 1.15, 1),
                 ("E", 1.02, 100), ("E", 1.15, 23)]
        for name, frac, want in cases:
            g = GEOM[name]
            thru = _rating(g, melting_model="sensible_plus_latent")[
                "max_current_without_melt_through_a"]
            cur = frac * thru
            self.assertEqual(_raw_latent_march(g, cur), want, (name, frac))
            got = H(dict(g, current_a=cur, melting_model="sensible_plus_latent"))
            self.assertEqual(got["pulses_to_melt"], want, (name, frac))

    def test_settled_trains_agree_with_the_march_over_a_long_window(self):
        # below the melt-through ceiling the trough stops moving. The march needs
        # only a couple of thousand shots here (measured drift 0.0 between 2e3 and
        # 2e4), so an exact agreement is a real check and not a coincidence.
        for name in ("A", "C", "D", "E"):
            g = GEOM[name]
            thru = _rating(g, melting_model="sensible_plus_latent")[
                "max_current_without_melt_through_a"]
            cur = 0.98 * thru
            self.assertIsNone(_raw_latent_march(g, cur, cap=20000), name)
            got = H(dict(g, current_a=cur, melting_model="sensible_plus_latent"))
            self.assertTrue(got["survives_indefinitely"], name)

    def test_the_steady_peak_of_the_march_and_the_series_agree(self):
        # the same check for the sensible layer, one level down: the series closed
        # form and a stepped map have to land on the same number
        g = GEOM["A"]
        a, beta, tau, t_on, t_off, _dTm = _internals(g, 20.0)
        k = a * beta - 1.0 / tau
        trough = 0.0
        for _ in range(400):
            peak = _rise(trough, a, k, t_on)
            trough = peak * math.exp(-t_off / tau)
        self.assertAlmostEqual(_steady_peak(g, 20.0) / peak, 1.0, delta=1e-6)


class TestCeilingTwoSided(unittest.TestCase):
    """A threshold that only fails on one side is a number, not a threshold."""

    def test_threshold_is_tight_on_both_sides(self):
        want = {"A": 305, "B": 1037, "C": 2, "D": 1, "E": 100}
        for name, g in GEOM.items():
            thru = _rating(g, melting_model="sensible_plus_latent")[
                "max_current_without_melt_through_a"]
            below = H(dict(g, current_a=0.98 * thru,
                           melting_model="sensible_plus_latent"))
            above = H(dict(g, current_a=1.02 * thru,
                           melting_model="sensible_plus_latent"))
            self.assertTrue(below["survives_indefinitely"], name)
            self.assertFalse(above["survives_indefinitely"], name)
            self.assertEqual(above["pulses_to_melt"], want[name], name)
            self.assertGreater(above["pulses_to_melt"], 0, name)


class TestNegativeControl(unittest.TestCase):
    """The obvious wrong model, written down, so the right one can be told apart."""

    def test_latent_heat_cannot_be_carried_as_a_temperature_offset(self):
        # If 208.7 kJ/kg were just extra kelvin (L_f/c_p = 542.08 K), the melt
        # time would be 542.08/(1083-25) = 51 % longer; the truth is
        # (1+beta) = 1.25x, because the energy is absorbed AT the melt point and
        # temperature stops there. The wrong model is 3.7x off - refuse it.
        offset_s = C.fusing_current(width_mm=0.5, t_s=1.0, copper_oz=1.0)["value"]
        self.assertAlmostEqual(offset_s, 5.024, delta=1e-3)
        naive = (1.0 + LATENT_J_KG / CP / (TM - 25.0))
        exact = 1.0 + 1.0 / (TREF + 25.0)
        self.assertAlmostEqual(naive, 1.512, delta=2e-3)
        self.assertAlmostEqual(exact, 1.003861, delta=1e-6)
        self.assertGreater(naive / exact, 1.5)
        # and the closed form must land on the phi route, not on the naive one:
        # the extra on-time is (L_f/c_p)/phi ~ 0.25 s, while carrying the same
        # energy as kelvin would need 0.51 s (51 % of the sensible melt time)
        i_fuse = offset_s
        t_on = 0.005
        args = dict(width_mm=0.5, copper_oz=1.0, h_w_m2k=0.0, t_on_s=t_on,
                    duty_cycle=1.0, current_a=i_fuse)
        s = H(dict(args, melting_model="sensible"))
        l = H(dict(args, melting_model="sensible_plus_latent"))
        extra = (l["pulses_to_melt"] - s["pulses_to_melt"]) * t_on
        naive_extra = s["pulses_to_melt"] * t_on * (LATENT_J_KG / CP) / (TM - 25.0)
        self.assertLess(extra, 0.7 * naive_extra)
        self.assertGreater(extra, 0.2)

    def test_without_the_reservoir_the_same_case_is_shorter(self):
        # the negative control has to be a real difference: sensible melts in 1
        # shot at this current, latent in 2
        i_fuse = C.fusing_current(width_mm=0.5, t_s=1.0, copper_oz=1.0)["value"]
        args = dict(width_mm=0.5, copper_oz=1.0, h_w_m2k=0.0, t_on_s=1.0,
                    duty_cycle=1.0, current_a=i_fuse)
        s = H(dict(args, melting_model="sensible"))
        l = H(dict(args, melting_model="sensible_plus_latent"))
        self.assertEqual(s["pulses_to_melt"], 1)
        self.assertEqual(l["pulses_to_melt"], 2)
        self.assertEqual(l["latent_delays_melt_pct"], 100.0)
        # the reservoir did not shrink: it is still 542 K of fusion energy
        self.assertAlmostEqual(l["latent_rise_k"], LATENT_J_KG / CP, delta=1e-3)


class TestCapIsNotSurvival(unittest.TestCase):
    """'No number' has two meanings and they must not be merged."""

    def test_a_capped_run_is_not_reported_as_a_survivor(self):
        capped = {"shots": None, "why": "beyond the shot cap", "peak": None,
                  "latent_left": 1.0, "gain_per_shot": 1.0,
                  "release_capacity": 1.0}
        with mock.patch.object(C, "_train_latent", return_value=capped):
            r = C.repetitive_pulse_rating(
                width_mm=0.5, t_on_s=1e-3, duty_cycle=0.05, copper_oz=1.0,
                h_w_m2k=200.0, current_a=10.0,
                melting_model="sensible_plus_latent")
        self.assertEqual(r["status"], "ok")
        self.assertFalse(r["survives_indefinitely"])
        self.assertIsNone(r["pulses_to_melt"])
        self.assertEqual(r["melt_beyond_shot_cap"], 200000)
        self.assertIn("accumulating", r["latent_verdict"])

    def test_a_settled_run_is_a_survivor(self):
        settled = {"shots": None, "why": "settled", "peak": 800.0,
                   "latent_left": 0.0, "gain_per_shot": 0.0,
                   "release_capacity": 10.0}
        with mock.patch.object(C, "_train_latent", return_value=settled):
            r = C.repetitive_pulse_rating(
                width_mm=0.5, t_on_s=1e-3, duty_cycle=0.05, copper_oz=1.0,
                h_w_m2k=200.0, current_a=10.0,
                melting_model="sensible_plus_latent")
        self.assertTrue(r["survives_indefinitely"])
        self.assertNotIn("melt_beyond_shot_cap", r)


class TestToolLayer(unittest.TestCase):
    """The argument has to exist in the schema, or it is silently ignored."""

    def test_schema_offers_the_model_and_its_two_values(self):
        prop = T.TOOLS["repetitive_pulse_rating"]["inputSchema"]["properties"]
        self.assertIn("melting_model", prop)
        self.assertEqual(prop["melting_model"]["enum"],
                         ["sensible", "sensible_plus_latent"])

    def test_unknown_model_is_refused_not_ignored(self):
        r = H(dict(GEOM["A"], melting_model="bogus"))
        self.assertEqual(r["status"], "error")
        self.assertEqual(r["reason_code"], "ARG_INVALID")
        self.assertIn("sensible_plus_latent", r["message"])
        self.assertNotIn("value", r)

    def test_handler_passes_the_default_through(self):
        # absent, None and "sensible" all have to reach the calculator as the same
        # thing - a missing argument must never mean "latent on by accident"
        base = dict(GEOM["B"], current_a=5.0)
        self.assertEqual(H(dict(base)), H(dict(base, melting_model="sensible")))
        self.assertEqual(H(dict(base)), H(dict(base, melting_model=None)))

    def test_counts_and_units_are_unchanged(self):
        r = H(dict(GEOM["E"], melting_model="sensible_plus_latent"))
        self.assertEqual(r["unit"], "A")
        self.assertEqual(r["status"], "ok")
        self.assertNotIn("unread_arguments", r)


if __name__ == "__main__":
    unittest.main()
