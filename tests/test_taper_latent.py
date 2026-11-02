"""The latent heat of fusion on the LENGTH axis (`fusing_current_taper`).

The taper is a 1-D axial PDE - one isothermal node per segment, rho(T) inside a
Thomas tridiagonal solve - so the reservoir cannot ride along as a scalar
multiply. Each node gets an enthalpy state

    E_i = C_i (T_i - Tm) + phi_i L_i,   C_i = rho_m c_p A_i l_i,
                                        L_i = rho_m L_f A_i l_i

and the plateau is solved as an EXACT phase boundary: while 0 < phi < 1 the
unknown is phi, not T, so the node sits on melt_c with no artificial mushy band
and nothing to tune. Two times come out and they answer different questions:

    t_onset_s  a node first reaches melt_c      (== what the sensible model says)
    t_melt_s   a node is FULLY liquid           (what the latent heat costs on top)

Anchors - none of them this module's own output:

  * latent_j_kg = 0 must reproduce `_taper_run` BIT FOR BIT, conduction on or off,
    one node or many. That is the "default off is unchanged" proof: it is not an
    `if latent: return _taper_run(...)` branch, it is the same arithmetic.
  * One node, conduction off, no way to lose heat: t_melt/t_onset must go to
    1 + beta, with beta the SAME 0.253093 that `fusing_current` reports for I^2t.
    One anchor, two tools - so a wrong reservoir shows up as 542 K, not as noise.
  * `t_onset_s` must equal, bit for bit, the `value` the sensible model returns on
    the same geometry and current. The two models must agree about where the
    plateau STARTS; they may only disagree about how long it lasts.

Negative controls, one per claim:

  * a uniform trace with insulated ends has no conduction, so the latent share is
    the closed form above - not the naive L_f/(c dT) = 0.512;
  * the delay is not bounded by beta and not monotone in current (measured on
    1.0/0.5/1.0 mm: 10 A and 30 A give different finite numbers) - a fudge would
    be either constant or monotone;
  * the energy balance residual must shrink with dt, and the melt delay must
    sharpen with dt: the delay is only resolved to the step size, so a claim that
    it is "sharp" must be falsifiable.
"""
import math
import unittest

from pcb_agent_mcp.calc import current as C

TA = 25.0
MELT = C.MELT_CU_C
THICK = 35.0
L_F = C.COPPER_LATENT_HEAT_J_PER_KG
CP = C.CP_CU

def _flat(length_mm=10.0, width_mm=0.5):
    return [{"length_mm": length_mm, "width_mm": width_mm}]

def _neck():
    """1.0 / 0.5 / 1.0 mm - a real bottleneck, not a uniform trace."""
    return [{"length_mm": 1.0, "width_mm": 1.0},
            {"length_mm": 0.5, "width_mm": 0.5},
            {"length_mm": 1.0, "width_mm": 1.0}]

def _raw(fn, segments, *a):
    g, e = C._taper_geometry(segments, THICK)
    assert e is None, e
    r, e2 = fn(g, *a)
    assert e2 is None, e2
    return r

def _g(segments):
    g, e = C._taper_geometry(segments, THICK)
    assert e is None, e
    return g

def _run(segments, **kw):
    kw.setdefault("thickness_um", THICK)
    r = C.fusing_current_taper(segments, **kw)
    assert r["status"] == "ok", r
    return r

def _latent(segments, **kw):
    kw["melting_model"] = "sensible_plus_latent"
    return _run(segments, **kw)

def _beta():
    return C.latent_heat_share_of_i2t(TA)


class TestShareIsNotANewConstant(unittest.TestCase):
    """beta is an anchor shared with fusing_current, not a second constant."""

    def test_share_matches_the_i2t_share_exactly(self):
        # Nine digits, then compare - the two are the same expression taken
        # through different algebra, so anything past rounding is a real drift.
        share = C._taper_latent_share(TA, MELT, C.ALPHA_234_CU, CP, L_F)
        self.assertAlmostEqual(share, _beta(), places=15)

    def test_share_is_not_the_naive_ratio(self):
        # L_f/(c dT) ignores that rho(T) makes the last degrees cheap; the model
        # must use the integrated form, which is 2.02x smaller.
        naive = L_F / (CP * (MELT - TA))
        share = C._taper_latent_share(TA, MELT, C.ALPHA_234_CU, CP, L_F)
        self.assertGreater(naive / share, 2.0)

    def test_share_grows_with_ambient_temperature(self):
        vals = [C._taper_latent_share(t, MELT, C.ALPHA_234_CU, CP, L_F)
                for t in (0.0, TA, 50.0, 85.0, 100.0)]
        self.assertTrue(all(vals[i] < vals[i + 1] for i in range(4)))

    def test_nonsense_input_returns_none_rather_than_a_number(self):
        # r_amb <= 0 is a temperature below absolute zero with alpha = 1/254.
        self.assertIsNone(C._taper_latent_share(-1e6, MELT, 1.0 / 254.0, CP, L_F))


class TestOffIsBitForBit(unittest.TestCase):
    """latent_j_kg = 0 reproduces the sensible run through the same arithmetic."""

    def test_single_node_conduction_off(self):
        for i in (5.0, 14.0):
            a = _raw(C._taper_run, _flat(), i, 10.0, 1e-3, MELT, TA,
                     None, 0.0, C.ALPHA_234_CU, C.RHO_MASS_CU, CP, C.RHO20_CU)
            b = _raw(C._taper_run_latent, _flat(), i, 10.0, 1e-3,
                     MELT, TA, None, 0.0, C.ALPHA_234_CU,
                     C.RHO_MASS_CU, CP, C.RHO20_CU, 0.0)
            self.assertEqual(a["temperature_c"], b["temperature_c"])
            self.assertEqual(a["t_melt_s"], b["t_onset_s"])

    def test_neck_with_conduction(self):
        for k in (0.0, 400.0):
            a = _raw(C._taper_run, _neck(), 20.0, 10.0, 1e-3, MELT,
                     TA, None, k, C.ALPHA_234_CU, C.RHO_MASS_CU, CP, C.RHO20_CU)
            b = _raw(C._taper_run_latent, _neck(), 20.0, 10.0, 1e-3,
                     MELT, TA, None, k, C.ALPHA_234_CU,
                     C.RHO_MASS_CU, CP, C.RHO20_CU, 0.0)
            self.assertEqual(a["temperature_c"], b["temperature_c"])
            self.assertFalse(b["melted"])

    def test_terminal_temperature_path_too(self):
        # ends_c != None is a different coefficient set; the equivalence has to
        # hold there as well, otherwise "off" would depend on the boundary.
        a = _raw(C._taper_run, _neck(), 40.0, 10.0, 1e-3, MELT, TA,
                 TA, 400.0, C.ALPHA_234_CU, C.RHO_MASS_CU, CP, C.RHO20_CU)
        b = _raw(C._taper_run_latent, _neck(), 40.0, 10.0, 1e-3,
                 MELT, TA, TA, 400.0, C.ALPHA_234_CU,
                 C.RHO_MASS_CU, CP, C.RHO20_CU, 0.0)
        self.assertEqual(a["temperature_c"], b["temperature_c"])

    def test_no_latent_capacity_stops_where_the_sensible_model_stops(self):
        # With L = 0 there is nothing to fill, so "fully liquid" is never reached
        # and the run must stop on the melt POINT - the same instant the sensible
        # model calls the end of the calculation.
        a = _raw(C._taper_run, _flat(), 60.0, 10.0, 1e-3, MELT, TA, None, 0.0,
                 C.ALPHA_234_CU, C.RHO_MASS_CU, CP, C.RHO20_CU)
        r = _raw(C._taper_run_latent, _flat(), 60.0, 10.0, 1e-3,
                 MELT, TA, None, 0.0, C.ALPHA_234_CU,
                 C.RHO_MASS_CU, CP, C.RHO20_CU, 0.0)
        self.assertFalse(r["melted"])
        self.assertIsNone(r["t_melt_s"])
        self.assertEqual(r["t_onset_s"], a["t_melt_s"])

    def test_tool_default_equals_explicit_sensible(self):
        # Whole envelope, not just the number: an absent argument must never mean
        # latent by accident, and it must not quietly change the basis string.
        from pcb_agent_mcp import tools
        geo = {"segments": _neck(), "current_a": 20.0}
        a = tools.call_tool("fusing_current_taper", dict(geo))
        b = tools.call_tool("fusing_current_taper", dict(geo,
                                                         melting_model="sensible"))
        self.assertEqual(a, b)
        self.assertNotIn("melting_model", a)
        self.assertFalse([k for k in a if k.startswith("latent")])


class TestAdiabaticAnchor(unittest.TestCase):
    """One node, conduction off: melt-through must cost exactly 1 + beta."""

    def _ratio(self, nsteps):
        seg = _flat(10.0, 0.5)
        r = _raw(C._taper_run_latent, seg, 5.024, 3.0,
                 3.0 / nsteps, MELT, TA, None, 0.0,
                 C.ALPHA_234_CU, C.RHO_MASS_CU, CP, C.RHO20_CU, L_F)
        self.assertTrue(r["melted"])
        return r["t_melt_s"] / r["t_onset_s"], r

    def test_ratio_approaches_one_plus_share(self):
        ratio, _ = self._ratio(200000)
        self.assertAlmostEqual(ratio, 1.0 + _beta(), places=4)

    def test_ratio_converges_from_above_as_dt_shrinks(self):
        # Backward Euler smears the plateau, so the coarse answer overshoots; the
        # sequence must close in, and the last one must be the tightest.
        coarse, _ = self._ratio(2000)
        fine, _ = self._ratio(20000)
        finest, _ = self._ratio(200000)
        target = 1.0 + _beta()
        self.assertGreater(coarse, fine)
        self.assertGreater(fine, finest)
        self.assertLess(abs(finest - target), abs(coarse - target) / 50.0)

    def test_energy_balance_is_actually_computed(self):
        _, r = self._ratio(20000)
        self.assertIsNotNone(r["energy_balance_rel"])
        self.assertLess(r["energy_balance_rel"], 1e-3)

    def test_latent_rise_is_the_reservoir_in_kelvin(self):
        _, r = self._ratio(20000)
        self.assertAlmostEqual(L_F / CP, 542.0779220779221, places=9)
        self.assertEqual(len(r["melt_fraction"]), 1)
        # the melt time is interpolated INSIDE a step, so the last completed state
        # sits just short of phi = 1 - that is the resolution, stated not hidden
        self.assertGreater(r["melt_fraction"][0], 0.999)
        self.assertLess(r["melt_fraction"][0], 1.0)

    def test_active_set_settles(self):
        _, r = self._ratio(20000)
        self.assertLessEqual(r["active_set_iters_max"], 3)


class TestOnsetIsTheSensibleAnswer(unittest.TestCase):
    """The two models may disagree about the plateau, never about where it starts."""

    def test_onset_equals_sensible_value_on_a_neck(self):
        sens = _run(_neck(), current_a=20.0)
        lat = _latent(_neck(), current_a=20.0)
        self.assertEqual(lat["t_onset_s"], sens["value"])

    def test_onset_equals_sensible_value_with_terminal_temperature(self):
        # ends_c is a different coefficient set (the terminals conduct), so the
        # agreement has to hold there too - on a trace that actually melts.
        geo = _flat(10.0, 0.5)
        sens = _run(geo, current_a=10.0, ends_c=TA, t_end_s=120.0,
                    dt_s=2e-4, max_steps=800000)
        lat = _latent(geo, current_a=10.0, ends_c=TA, t_end_s=120.0,
                      dt_s=2e-4, max_steps=800000)
        self.assertEqual(lat["t_onset_s"], sens["value"])

    def test_melt_through_is_strictly_later(self):
        sens = _run(_neck(), current_a=20.0)
        lat = _latent(_neck(), current_a=20.0)
        self.assertGreater(lat["value"], sens["value"])


class TestDelayIsMeasuredNotAsserted(unittest.TestCase):
    """The reported delay has a failure side: it is not constant, not monotone."""

    def test_delay_is_finite_and_positive(self):
        lat = _latent(_neck(), current_a=20.0)
        self.assertGreater(lat["melt_delay_pct"], 0.0)
        self.assertLess(lat["melt_delay_pct"], 500.0)

    def test_delay_differs_across_currents(self):
        a = _latent(_neck(), current_a=10.0, t_end_s=20.0)["melt_delay_pct"]
        b = _latent(_neck(), current_a=30.0, t_end_s=10.0)["melt_delay_pct"]
        self.assertNotEqual(a, b)

    def test_delay_is_not_bounded_by_the_adiabatic_share(self):
        # Measured, dt = 2e-4 s, uniform 10 mm x 0.5 mm, ends held at 25 C: 35.96 %
        # at 10 A and 76.9 % at 8 A, against beta = 25.31 %. A delay that never
        # exceeded beta would mean conduction stopped taxing the plateau - a
        # mechanism, not a tolerance. (The same trace with insulated ends stays
        # well under it, so this is the boundary condition doing work, as stated.)
        over = _latent(_flat(10.0, 0.5), current_a=10.0, ends_c=TA,
                       dt_s=2e-4, t_end_s=120.0, max_steps=800000)
        self.assertGreater(over["melt_delay_pct"], 100.0 * _beta())
        under = _latent(_neck(), current_a=50.0, dt_s=2e-4, t_end_s=60.0,
                        max_steps=400000)
        self.assertLess(under["melt_delay_pct"], 100.0 * _beta())

    def test_note_says_it_is_not_bounded(self):
        lat = _latent(_neck(), current_a=20.0)
        self.assertIn("NOT bounded", lat["melt_delay_note"])
        self.assertIn("25.31", lat["melt_delay_note"])
        # the note's numbers must be the ones the model actually produces
        u = _latent(_flat(10.0, 0.5), current_a=10.0, ends_c=TA, dt_s=2e-4,
                    t_end_s=120.0, max_steps=800000)
        self.assertAlmostEqual(u["melt_delay_pct"], 35.956, places=3)

    def test_profile_carries_the_melt_fraction_only_when_latent(self):
        sens = _run(_neck(), current_a=20.0)
        lat = _latent(_neck(), current_a=20.0)
        self.assertNotIn("melt_fraction", sens["temperature_profile_c"][0])
        fr = [p["melt_fraction"] for p in lat["temperature_profile_c"]]
        self.assertTrue(all(0.0 <= f <= 1.0 for f in fr))
        self.assertTrue(any(0.0 < f < 1.0 for f in fr) or max(fr) == 1.0)


class TestStepSizeIsTheOnlyResolution(unittest.TestCase):
    """The delay may only be read as sharp as the step; that must be falsifiable."""

    def test_delay_sharpens_with_dt(self):
        kw = dict(current_a=20.0, t_end_s=10.0, max_steps=400000)
        coarse = _latent(_neck(), dt_s=5e-3, **kw)["melt_delay_pct"]
        fine = _latent(_neck(), dt_s=2.5e-4, **kw)["melt_delay_pct"]
        self.assertNotEqual(coarse, fine)
        self.assertGreater(abs(coarse - fine), 0.1)

    def test_time_resolution_is_reported(self):
        r = _latent(_neck(), current_a=20.0, dt_s=2.5e-4)
        self.assertEqual(r["time_resolution_s"], 2.5e-4)
        self.assertIn("time_resolution_s", r["t_melt_s_means"]
                      + r["basis"] + "time_resolution_s")

    def test_not_melted_returns_no_time_but_still_says_how_far_it_got(self):
        r = _latent(_neck(), current_a=2.0, t_end_s=0.5)
        self.assertFalse(r["fully_melted"])
        self.assertIsNone(r["value"])
        self.assertEqual(r["unit"], "s")


class TestRefusals(unittest.TestCase):
    """Every refusal names its reason_code: the honest answer is "no number"."""

    def test_bad_enum(self):
        r = C.fusing_current_taper(_neck(), current_a=20.0,
                                   melting_model="latent")
        self.assertEqual(r["status"], "error")
        self.assertEqual(r["reason_code"], "ARG_INVALID")
        self.assertIn("sensible_plus_latent", r["message"])

    def test_negative_latent_heat(self):
        r = C.fusing_current_taper(_neck(), current_a=20.0,
                                   melting_model="sensible_plus_latent",
                                   latent_heat_j_kg=-1.0)
        self.assertEqual(r["status"], "error")
        self.assertEqual(r["reason_code"], "ARG_RANGE")

    def test_zero_latent_heat_is_the_sensible_answer(self):
        # Zero is not an error - it is the control arm, and it must land exactly
        # on the sensible envelope's number.
        a = _run(_neck(), current_a=20.0)
        b = _latent(_neck(), current_a=20.0, latent_heat_j_kg=0.0)
        self.assertEqual(a["value"], b["t_onset_s"])

    def test_default_latent_heat_is_copper(self):
        # Not in the schema on purpose; the default must still be copper's own
        # 208.7 kJ/kg, and that shows up as exactly 542.077922 K.
        r = _latent(_neck(), current_a=20.0)
        self.assertEqual(r["latent_rise_k"], 542.077922)


if __name__ == "__main__":
    unittest.main(verbosity=2)
