"""Time-domain PDN droop: every number here has a closed form or an identity.

The point of these tests is that the transient answer is NOT graded by "it looks
like a droop". It is graded by

  * a one-pole network, whose closed form is written down below,
  * an LC ring, whose period is 2*pi*sqrt(LC) and whose ESR is the only damping,
  * a pure capacitor fed by a current, where backward Euler is exact (the ramp),
  * the DC tail, which any drifting integrator or wrong companion sign misses,
  * a charge identity (sum of -C*du against the integral of what went past the
    regulator) that only closes if EVERY branch made it into the node equation.

Each of those fails for a different bug, which is the reason all of them exist.
"""
import math
import unittest

from pcb_agent_mcp.calc import power as P


def _tool(args):
    from pcb_agent_mcp import tools as T
    return T.call_tool("pdn_step_response", args)


def _interp(ts, vs, t):
    """Linear interpolation of the reported (decimated) waveform."""
    if t <= ts[0]:
        return vs[0]
    for i in range(1, len(ts)):
        if ts[i] >= t:
            span = ts[i] - ts[i - 1]
            if span <= 0:
                return vs[i]
            f = (t - ts[i - 1]) / span
            return vs[i - 1] + f * (vs[i] - vs[i - 1])
    return vs[-1]


def _crossings(ts, vs, level):
    """Times where vs crosses `level`, linearly interpolated."""
    out = []
    for i in range(1, len(vs)):
        a, b = vs[i - 1] - level, vs[i] - level
        if a == 0:
            out.append(ts[i - 1])
        elif a * b < 0:
            span = ts[i] - ts[i - 1]
            out.append(ts[i - 1] + span * abs(a) / (abs(a) + abs(b)))
    return out


class TestTheOnePoleClosedForm(unittest.TestCase):
    """R_series in parallel with (ESR + C): first order, and the two ends of the
    curve are set by two different resistors."""

    R_series = 1.0
    ESR = 0.01
    C = 1e-6
    I = 1.0

    def _solve(self, probes_ns):
        return _tool({"caps": [{"c_farad": self.C, "esr_ohm": self.ESR}],
                      "step_a": self.I, "r_series_ohm": self.R_series,
                      "samples": 4000, "probe_at_ns": probes_ns})

    def _r_par(self):
        return self.R_series * self.ESR / (self.R_series + self.ESR)

    def _tau(self):
        # NOT C*(R||ESR).  The parallel combination only sets where the curve
        # STARTS (the cap is a short through its own ESR at t=0+, so the first
        # reading is -I*(R||ESR)); the speed it gets there is set by the loop
        # that actually charges the cap, i.e. the series sum C*(R_series+ESR).
        return self.C * (self.R_series + self.ESR)

    def _exact(self, t):
        rs, r_par = self.R_series, self._r_par()
        return -self.I * (rs - (rs - r_par) * math.exp(-t / self._tau()))

    def test_the_two_ends_are_the_two_resistors(self):
        tau = self._tau()
        r = self._solve([0.1 * tau * 1e9, tau * 1e9, 5.0 * tau * 1e9])
        self.assertEqual(r["status"], "ok", r.get("message"))
        v = r["value"]
        # before the step the rail sits at the preload DC point (no load here)
        self.assertAlmostEqual(v["v_initial_v"], 0.0, delta=1e-12)
        # at t=inf the cap is open: -I*R_series, and that is also the DC answer
        self.assertAlmostEqual(v["v_final_v"], -self.I * self.R_series, delta=1e-7)
        self.assertAlmostEqual(v["dc_expected_v"], -self.I * self.R_series, delta=1e-12)
        self.assertLess(v["dc_check_rel"], 1e-4)
        # at t=0+ the cap would be a short through its ESR (-I*(R||ESR) = -9.90 mV);
        # a tenth of a time constant is already most of the way there.  Both
        # ends are checked separately below, so this one only has to be within
        # the backward-Euler bias, measured at 0.05 % on this anchor.
        got = v["probes"][0]["v_v"]
        exact = self._exact(0.1 * tau)
        self.assertAlmostEqual(got, exact, delta=abs(exact) * 0.01)
        self.assertGreater(abs(got), abs(-self.I * self._r_par()))
        self.assertLess(abs(got), abs(v["v_final_v"]))
        # a monotone droop: the extreme is the tail, and there is no undershoot
        self.assertEqual(v["t_peak_s"], v["t_stop_s"])
        self.assertEqual(v["undershoot_v"], 0.0)

    def test_the_middle_of_the_curve_is_exp_minus_one(self):
        tau = self._tau()
        r = self._solve([tau * 1e9])
        self.assertEqual(r["status"], "ok", r.get("message"))
        v = r["value"]
        exact = self._exact(tau)
        got = v["probes"][0]["v_v"]
        # backward Euler is first order: at h = tau/40 the exponential comes out
        # 1.2 % high, so 3 % is the assertion, and h_refine_peak_delta_pct is the
        # number that says the grid is not the story
        self.assertAlmostEqual(got, exact, delta=abs(exact) * 0.03)
        self.assertLess(v["h_refine_peak_delta_pct"], 2.0)
        # At one time constant the gap has closed by exp(-1): 63.6 % of the way
        # from -I*(R||ESR) to -I*R_series.  A wrong tau shows up here as a
        # fraction outside [0.62, 0.65] even when the shape still looks right.
        self.assertGreater(abs(got) / self.R_series, 0.62)
        self.assertLess(abs(got) / self.R_series, 0.65)

    def test_a_100uF_cap_is_the_textbook_droop(self):
        r = _tool({"caps": [{"c_farad": 100e-6, "esr_ohm": 0.01}],
                   "step_a": 2.0, "r_series_ohm": 0.02})
        self.assertEqual(r["status"], "ok", r.get("message"))
        v = r["value"]
        self.assertAlmostEqual(v["peak_v"], -0.04, delta=1e-6)
        self.assertLess(v["charge_balance_rel"], 1e-9)
        self.assertIs(v["tail_settled"], True)
        self.assertGreater(v["settle_time_s"], 0.0)
        self.assertLess(v["settle_time_s"], v["t_stop_s"])


class TestThePureCapacitorIsARamp(unittest.TestCase):
    """With the regulator far away (1 Mohm) the cap sees a constant current, and
    backward Euler is EXACT on a ramp - so the only error left is the charge the
    regulator itself carried, which is ~1e-6 of the total."""

    def test_the_ramp_is_v_equals_esr_times_i_plus_q_over_c(self):
        r = _tool({"caps": [{"c_farad": 1e-6, "esr_ohm": 0.05}],
                   "step_a": 1.0, "r_series_ohm": 1e6, "rise_ns": 50.0,
                   "t_stop_ns": 1000.0})
        self.assertEqual(r["status"], "ok", r.get("message"))
        v = r["value"]
        # Q(t) = I*(t - rise/2) once the edge is over
        q = 1.0 * (1e-6 - 25e-9)
        exact = -(0.05 + q / 1e-6)
        self.assertAlmostEqual(v["v_final_v"], exact, delta=abs(exact) * 2e-3)
        self.assertIs(v["tail_settled"], False)     # 1 us is nowhere near 1e6*1e-6 s
        self.assertLess(v["h_refine_peak_delta_pct"], 0.5)


class TestTheLCRing(unittest.TestCase):
    """10 nH against 1 uF is 1.59 MHz. The period comes from the integrator, not
    from a stored formula, and the ESR is the only damping - so a wrong companion
    sign changes the frequency or kills the ring."""

    L = 10e-9
    C = 1e-6
    R = 0.05

    def test_the_period_is_two_pi_root_lc(self):
        r = _tool({"caps": [{"c_farad": self.C}], "step_a": 1.0,
                   "r_series_ohm": self.R, "vr_series_h": self.L,
                   "rise_ns": 50.0, "t_stop_ns": 3000.0})
        self.assertEqual(r["status"], "ok", r.get("message"))
        v = r["value"]
        level = v["v_final_v"]
        cross = _crossings(v["t_s"], v["v_rail_v"], level)
        self.assertGreaterEqual(len(cross), 4, "no ring to measure: %s" % (cross,))
        span = cross[3] - cross[0]          # 3 half periods
        got = 2.0 * span / 3.0
        exact = 2.0 * math.pi * math.sqrt(self.L * self.C)
        self.assertAlmostEqual(got, exact, delta=exact * 0.05)

    def test_it_rings_so_the_undershoot_is_real(self):
        r = _tool({"caps": [{"c_farad": self.C}], "step_a": 1.0,
                   "r_series_ohm": self.R, "vr_series_h": self.L,
                   "rise_ns": 50.0, "t_stop_ns": 3000.0})
        v = r["value"]
        self.assertGreater(v["undershoot_v"], 0.0)
        self.assertLess(v["v_final_v"], 0.0)
        # with ESR = 0 the only damping is the 0.05 ohm regulator, so the first
        # swing goes PAST the DC answer and comes back - which is the whole point
        # of a transient answer
        self.assertGreater(abs(v["undershoot_v"]), abs(v["v_final_v"]))


class TestTheLoadEdge(unittest.TestCase):
    def test_an_ideal_step_into_inductance_is_refused(self):
        r = _tool({"caps": [{"c_farad": 1e-6, "esl_h": 1e-9}], "r_series_ohm": 0.01})
        self.assertEqual(r["status"], "error")
        self.assertEqual(r["reason_code"], "ARG_REQUIRED")
        self.assertIn("rise_ns", r["message"])
        # and the same network WITH an edge answers
        ok = _tool({"caps": [{"c_farad": 1e-6, "esl_h": 1e-9}], "r_series_ohm": 0.01,
                    "rise_ns": 100.0})
        self.assertEqual(ok["status"], "ok", ok.get("message"))

    def test_a_slower_edge_droops_less(self):
        base = {"caps": [{"c_farad": 10e-6, "esr_ohm": 0.002, "esl_h": 500e-12}],
                "step_a": 5.0, "r_series_ohm": 0.001, "vr_series_h": 2e-9,
                "t_stop_ns": 20000.0}
        fast = _tool(dict(base, rise_ns=100.0))["value"]
        slow = _tool(dict(base, rise_ns=2000.0))["value"]
        self.assertLess(abs(slow["peak_v"]), abs(fast["peak_v"]))
        self.assertLess(abs(slow["vrm_di_dt_a_per_us"]),
                        abs(fast["vrm_di_dt_a_per_us"]))


class TestWhereTheCapsSit(unittest.TestCase):
    """The same capacitor, moved behind 2 mohm and 1 nH of board.

    The DC tail is IDENTICAL in both cases - that is the honest answer, because at
    DC the cap is open and the load current crosses the plane either way.  What
    moves is the peak, i.e. the part of the answer the frequency-domain tool only
    sees as an impedance bump.
    """

    STEP = {"step_a": 10.0, "r_series_ohm": 0.001, "plane_r_ohm": 0.002,
            "plane_l_h": 1e-9, "rise_ns": 400.0}

    def _at(self, where):
        return _tool({"caps": [{"c_farad": 100e-6, "esr_ohm": 0.002, "at": where}],
                      **self.STEP})["value"]

    def test_bulk_caps_behind_the_plane_are_worse(self):
        die = self._at("die")
        bulk = self._at("bulk")
        # the plane is in the DC path in BOTH cases: 1 mohm regulator + 2 mohm board
        self.assertAlmostEqual(die["v_final_v"], -10.0 * 0.003, delta=1e-6)
        self.assertAlmostEqual(bulk["v_final_v"], -10.0 * 0.003, delta=1e-6)
        # ... so the DC tail cannot tell them apart, and placement is NOT a factor
        # in it.  A single-node model that dropped the plane would have said
        # -10 mV here, which is the bug this assertion exists to catch.
        self.assertGreater(abs(bulk["peak_v"]), abs(die["peak_v"]) * 1.4)
        self.assertLess(abs(die["peak_v"]), 10.0 * 0.003 * 1.3)
        self.assertLess(bulk["h_refine_peak_delta_pct"], 2.0)
        self.assertLess(bulk["charge_balance_rel"], 1e-9)

    def test_the_plane_is_not_dropped_when_every_cap_is_at_the_die(self):
        bare = _tool({"caps": [{"c_farad": 100e-6, "esr_ohm": 0.002}],
                      "step_a": 10.0, "r_series_ohm": 0.001, "rise_ns": 400.0})["value"]
        withplane = self._at("die")
        self.assertAlmostEqual(bare["v_final_v"], -10.0 * 0.001, delta=1e-6)
        self.assertAlmostEqual(withplane["v_final_v"], -10.0 * 0.003, delta=1e-6)
        self.assertEqual(bare["nodes"], withplane["nodes"])

    def test_the_two_nodes_are_reported(self):
        self.assertEqual(self._at("die")["nodes"], 1)
        self.assertEqual(self._at("bulk")["nodes"], 2)


class TestCountIsParallelism(unittest.TestCase):
    def test_count_equals_listing_them(self):
        a = _tool({"caps": [{"c_farad": 10e-6, "esr_ohm": 0.003, "count": 3}],
                   "step_a": 3.0, "r_series_ohm": 0.002})
        b = _tool({"caps": [{"c_farad": 10e-6, "esr_ohm": 0.003}] * 3,
                   "step_a": 3.0, "r_series_ohm": 0.002})
        self.assertEqual(a["status"], "ok", a.get("message"))
        self.assertEqual(b["status"], "ok", b.get("message"))
        self.assertAlmostEqual(a["value"]["peak_v"], b["value"]["peak_v"], delta=1e-15)
        self.assertAlmostEqual(a["value"]["v_final_v"], b["value"]["v_final_v"],
                               delta=1e-15)


class TestARisingLoad(unittest.TestCase):
    def test_a_negative_step_goes_the_other_way(self):
        r = _tool({"caps": [{"c_farad": 100e-6, "esr_ohm": 0.01}],
                   "preload_a": 2.0, "step_a": -2.0, "r_series_ohm": 0.02})
        self.assertEqual(r["status"], "ok", r.get("message"))
        v = r["value"]
        self.assertAlmostEqual(v["v_initial_v"], -0.04, delta=1e-9)
        self.assertAlmostEqual(v["v_final_v"], 0.0, delta=1e-7)
        # the rail RISES back to zero, so the extreme is the end of the window
        self.assertGreater(v["peak_v"], v["v_initial_v"])
        # and it still has to say when it got there: a 0 V DC answer must not make
        # the settle test degenerate
        self.assertGreater(v["settle_time_s"], 0.0)
        self.assertLess(v["settle_time_s"], v["t_stop_s"])


class TestRefusals(unittest.TestCase):
    def test_no_caps(self):
        r = _tool({"caps": [], "r_series_ohm": 0.01})
        self.assertEqual(r["reason_code"], "ARG_REQUIRED")

    def test_a_zero_step(self):
        r = _tool({"caps": [{"c_farad": 1e-6}], "r_series_ohm": 0.01, "step_a": 0.0})
        self.assertEqual(r["reason_code"], "ARG_INVALID")

    def test_a_bolted_source_is_not_a_measurement(self):
        r = _tool({"caps": [{"c_farad": 1e-6}]})
        self.assertEqual(r["reason_code"], "ARG_INVALID")
        self.assertIn("ideal source", r["message"])

    def test_bulk_without_a_path(self):
        r = _tool({"caps": [{"c_farad": 1e-6, "at": "bulk"}], "r_series_ohm": 0.01})
        self.assertEqual(r["reason_code"], "ARG_INVALID")

    def test_an_unknown_placement(self):
        r = _tool({"caps": [{"c_farad": 1e-6, "at": "middle"}], "r_series_ohm": 0.01})
        self.assertEqual(r["reason_code"], "ARG_INVALID")
        self.assertIn("middle", r["message"])

    def test_scalar_ranges_are_refused_not_clamped(self):
        for key, val in (("samples", 10), ("settle_band_pct", 0.0),
                         ("rise_ns", -1.0), ("t_stop_ns", -1.0),
                         ("r_series_ohm", -1.0)):
            args = {"caps": [{"c_farad": 1e-6}], "r_series_ohm": 0.01}
            args[key] = val
            r = _tool(args)
            self.assertEqual(r["status"], "error", key)
            self.assertIn(r["reason_code"], ("ARG_RANGE", "ARG_INVALID"), key)

    def test_a_zero_capacitance(self):
        r = _tool({"caps": [{"c_farad": 0.0}], "r_series_ohm": 0.01})
        self.assertEqual(r["reason_code"], "ARG_INVALID")


class TestTheToolSurface(unittest.TestCase):
    def test_it_is_registered_with_its_own_args(self):
        from pcb_agent_mcp import tools as T
        self.assertIn("pdn_step_response", T.TOOLS)
        d = T.TOOLS["pdn_step_response"]
        props = (d.get("inputSchema") or d)["properties"]
        for key in ("caps", "step_a", "r_series_ohm", "vr_series_h", "plane_r_ohm",
                    "plane_l_h", "rise_ns", "t_stop_ns", "samples",
                    "settle_band_pct", "refine"):
            self.assertIn(key, props)
        self.assertEqual((d.get("inputSchema") or d)["required"], ["caps"])

    def test_the_envelope_says_what_it_is(self):
        r = _tool({"caps": [{"c_farad": 100e-6, "esr_ohm": 0.01}],
                   "step_a": 2.0, "r_series_ohm": 0.02})
        self.assertEqual(r["unit"], "V")
        self.assertEqual(r["model"], P.STEP_MODEL)
        self.assertIn("backward Euler", r["basis"])
        self.assertIn("not_computed", r)
        self.assertTrue(any("regulator LOOP" in x for x in r["not_computed"]))
        self.assertEqual(r["assumptions"]["step_a"], 2.0)

    def test_the_curve_is_returned_and_is_the_right_length(self):
        r = _tool({"caps": [{"c_farad": 100e-6, "esr_ohm": 0.01}],
                   "step_a": 2.0, "r_series_ohm": 0.02})
        v = r["value"]
        self.assertEqual(len(v["t_s"]), len(v["v_rail_v"]))
        self.assertGreater(len(v["t_s"]), 60)
        self.assertLessEqual(len(v["t_s"]), 200)
        self.assertEqual(v["t_s"][0], 0.0)
        self.assertAlmostEqual(v["t_s"][-1], v["t_stop_s"], delta=v["t_stop_s"] * 1e-6)


if __name__ == "__main__":
    unittest.main()
