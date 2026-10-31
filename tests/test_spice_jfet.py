"""Golden tests for the JFET card: J (level-1, Shichman-Hodges).

Every expected number below was computed BY HAND from the equation the module
claims to stamp, and then that equation itself was measured against ngspice-47
point by point (48 combinations of model x Vgs x Vds, worst absolute difference
0.000e+00). The four claims a reader would doubt get their own test each, with
the wrong answer named in the assertion so a regression cannot pass quietly:

* the card has THREE nodes, not four: `Jname nd ng ns model`. A JFET's gate is a
  junction to the channel and level-1 SPICE has no separate bulk terminal, so
  reading the model name as a fourth node is the mistake, and it is refused.
* there is NO factor of 1/2. BETA=1m at Vgs-Vto=1 reads 1 mA, not 0.5 mA - the
  MOSFET's saturation form divided by two is what everyone expects here, and the
  linear form is `BETA*(2*Vov*Vds - Vds^2)`, not `BETA*(2*Vov*Vds - Vds^2)/2`.
* AREA on the CARD REPLACES the model's AREA= (model 2 with card 3 reads 3x,
  measured), it is not multiplied with it - and the card's AREA= is the only
  geometry knob: W= and L= are refused by ngspice itself on a J card.
* the gate junction is NOT stamped. That is exact below ~0.5 V of forward gate
  bias and wrong above it, so the element report carries
  `gate_junction_forward_v` and the deck is flagged `outside_model` past 0.5 V,
  instead of the model quietly pretending the gate is an open circuit forever.

The two forms meet exactly at Vds = Vov (no smoothing term is needed or wanted),
which is what makes the boundary test assert one number rather than a range.
"""
import cmath
import math
import os
import sys
import unittest

sys.path.insert(0, os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "src"))

from pcb_agent_mcp.calc import spice  # noqa: E402

def _op(net):
    r = spice.simulate(net, analysis="op")
    assert r["status"] == "ok", r
    return r["value"]

def _ac(net):
    r = spice.simulate(net, analysis="ac")
    assert r["status"] == "ok", r
    return r["value"]["points"][0]

def _tran(net):
    r = spice.simulate(net, analysis="tran")
    assert r["status"] == "ok", r
    return r["value"]

def _els(value):
    return {e["name"]: e for e in value["elements"]}

def _j(net):
    return _els(_op(net))["J1"]

def _bias(vd=5.0, vg=0.0, vto=-1.0, beta=1e-3, model_extra="", card_extra=""):
    """Drain and gate pinned by ideal sources: every number below is then a
    property of the device equation alone, with no bias network to solve."""
    return ("* jf\nVd 1 0 %g\nVg 2 0 %g\nJ1 1 2 0 jm%s\n"
            ".model jm NJF(VTO=%g BETA=%g%s)\n.op\n"
            % (vd, vg, card_extra, vto, beta, model_extra))

class TestSquareLaw(unittest.TestCase):
    """The equation itself: one overdrive, one transconductance, two regions."""

    def test_one_volt_overdrive_reads_beta_not_beta_over_two(self):
        # the MOSFET's saturation form is BETA/W-ish with a 1/2 in front; a JFET's
        # is not. 1 mA is the whole point: 0.5 mA here means the 1/2 crept in.
        j1 = _j(_bias())
        self.assertAlmostEqual(j1["current_a"]["id"], 1.0e-3, places=12)
        self.assertNotAlmostEqual(j1["current_a"]["id"], 0.5e-3, places=6)

    def test_the_transconductance_is_two_beta_times_the_overdrive(self):
        # dId/dVgs = 2*BETA*Vov in saturation: 2 * 1m * 1 = 2 mA/V
        j1 = _j(_bias())
        self.assertAlmostEqual(j1["gm_s"], 2.0e-3, places=6)

    def test_power_is_vds_times_id(self):
        # 5 V across a 1 mA device that draws no gate current: 5 mW, all of it
        j1 = _j(_bias())
        self.assertAlmostEqual(j1["power_w"], 5.0e-3, places=12)

    def test_below_the_knee_the_linear_form_is_2_vov_vds_minus_vds_squared(self):
        # Vov = 1, Vds = 0.3: 1m * (0.6 - 0.09) = 0.51 mA
        j1 = _j(_bias(vd=0.3))
        self.assertAlmostEqual(j1["current_a"]["id"], 0.51e-3, places=12)
        self.assertEqual(j1["region"], "linear")
        # and in the linear region gm = 2*BETA*Vds = 0.6 mA/V, i.e. it depends on
        # Vds, which is how a reader can tell the two regions apart from the report
        self.assertAlmostEqual(j1["gm_s"], 0.6e-3, places=6)

    def test_the_two_forms_meet_at_vds_equal_vov(self):
        # Vds = Vov = 1: linear reads 1m*(2-1) = 1 mA and saturation reads
        # 1m*1^2 = 1 mA. A smoothing term would move this number; nothing does.
        j1 = _j(_bias(vd=1.0))
        self.assertAlmostEqual(j1["current_a"]["id"], 1.0e-3, places=12)
        # the boundary belongs to saturation (Vds >= Vov), as in the reference
        self.assertEqual(j1["region"], "saturation")

    def test_above_the_knee_the_current_stops_depending_on_vds(self):
        # LAMBDA=0: 1.5 V and 5 V read the same 1 mA
        self.assertAlmostEqual(_j(_bias(vd=1.5))["current_a"]["id"], 1.0e-3,
                               places=12)
        self.assertAlmostEqual(_j(_bias(vd=5.0))["current_a"]["id"], 1.0e-3,
                               places=12)

    def test_a_gate_below_the_pinch_off_voltage_is_cut_off(self):
        # Vgs = -2 with VTO = -1: Vov = -1, no channel at all
        j1 = _j(_bias(vg=-2.0))
        self.assertEqual(j1["current_a"]["id"], 0.0)
        self.assertEqual(j1["gm_s"], 0.0)
        self.assertEqual(j1["region"], "cutoff")

    def test_lambda_puts_a_slope_on_the_saturation_region(self):
        # 1m * (1 + 0.02*5) = 1.1 mA, and the SAME lambda does not touch the
        # region boundary itself
        j1 = _j(_bias(model_extra=" LAMBDA=0.02"))
        self.assertAlmostEqual(j1["current_a"]["id"], 1.1e-3, places=12)

class TestBiasNetworks(unittest.TestCase):
    """Numbers a user actually quotes: an operating point and a bias resistor."""

    def test_a_drain_resistor_settles_where_hand_arithmetic_says(self):
        # Vg=-0.4, VTO=-1, BETA=2m -> Vov=0.6 -> Id = 2m*0.36 = 720 uA
        # Vd = 12 - 4.7k*720u = 8.616 V, and Vds=8.616 >= 0.6 so the assumption
        # (saturation) is self-consistent
        net = ("* jf bias\nVdd 10 0 12\nRd 10 1 4.7k\nVg 2 0 -0.4\nJ1 1 2 0 jm\n"
               ".model jm NJF(VTO=-1 BETA=2m)\n.op\n")
        v = _op(net)
        self.assertAlmostEqual(v["nodes_v"]["1"], 8.616, delta=1e-6)
        j1 = _els(v)["J1"]
        self.assertAlmostEqual(j1["current_a"]["id"], 0.72e-3, places=12)
        self.assertEqual(j1["region"], "saturation")
        self.assertAlmostEqual(j1["power_w"], 8.616 * 0.72e-3, delta=1e-9)

    def test_a_source_resistor_trades_overdrive_for_bias_stability(self):
        # Vg=0, VTO=-2, BETA=1m, Rs=1k. Id = 1m*(2 - 1000*Id)^2 has two roots
        # (x = 1 mA and x = 4 mA in mA units); only x=1 has Vov>0, so the device
        # reports Vs = 1 V, Vgs = -1 V, Id = 1 mA and Vd = 12 - 4.7 = 7.3 V
        net = ("* jf degen\nVdd 10 0 12\nRd 10 1 4.7k\nVg 2 0 0\n"
               "J1 1 2 3 jm\nRs 3 0 1k\n.model jm NJF(VTO=-2 BETA=1m)\n.op\n")
        v = _op(net)
        self.assertAlmostEqual(v["nodes_v"]["3"], 1.0, places=6)
        self.assertAlmostEqual(v["nodes_v"]["1"], 7.3, places=6)
        j1 = _els(v)["J1"]
        self.assertAlmostEqual(j1["current_a"]["id"], 1.0e-3, places=6)
        self.assertAlmostEqual(j1["vgs_v"], -1.0, places=6)

    def test_the_gate_draws_no_current_so_the_gate_source_is_not_loaded(self):
        # a 1 k resistor in series with the gate must not move the gate voltage
        net = ("* jf gate\nVdd 10 0 12\nRd 10 1 4.7k\nVg 2 0 -0.4\nRg 2 3 1k\n"
               "J1 1 3 0 jm\n.model jm NJF(VTO=-1 BETA=2m)\n.op\n")
        v = _op(net)
        self.assertAlmostEqual(v["nodes_v"]["3"], -0.4, places=9)
        self.assertAlmostEqual(v["nodes_v"]["1"], 8.616, delta=1e-6)

class TestArea(unittest.TestCase):
    """AREA is the only geometry knob, and the card's value REPLACES the model's."""

    def test_the_model_area_scales_the_current(self):
        j1 = _j(_bias(model_extra=" AREA=2"))
        self.assertAlmostEqual(j1["current_a"]["id"], 2.0e-3, places=12)
        self.assertAlmostEqual(j1["model_params"]["BETA_eff"], 2.0e-3, places=15)

    def test_the_card_area_scales_the_current(self):
        j1 = _j(_bias(card_extra=" AREA=3"))
        self.assertAlmostEqual(j1["current_a"]["id"], 3.0e-3, places=12)
        self.assertAlmostEqual(j1["model_params"]["AREA"], 3.0, places=15)

    def test_the_card_area_replaces_the_model_area_instead_of_multiplying(self):
        # measured against ngspice: model AREA=2 plus card AREA=3 reads 3x, not 6x.
        # This is the assertion that would fail if the two were multiplied.
        j1 = _j(_bias(model_extra=" AREA=2", card_extra=" AREA=3"))
        self.assertAlmostEqual(j1["current_a"]["id"], 3.0e-3, places=12)
        self.assertNotAlmostEqual(j1["current_a"]["id"], 6.0e-3, places=6)

class TestDefaults(unittest.TestCase):
    """A parameter nobody wrote down has to be said out loud, not assumed."""

    def test_an_empty_model_uses_the_engines_own_defaults(self):
        # VTO=-2, BETA=1e-4, LAMBDA=0, AREA=1: Vov = 0-(-2) = 2, Id = 4e-4 A
        net = ("* jf defaults\nVd 1 0 5\nVg 2 0 0\nJ1 1 2 0 jm\n"
               ".model jm NJF()\n.op\n")
        j1 = _j(net)
        self.assertAlmostEqual(j1["current_a"]["id"], 4.0e-4, places=12)
        self.assertEqual(j1["model_params"],
                         {"VTO": -2.0, "BETA": 1e-4, "LAMBDA": 0.0,
                          "AREA": 1.0, "BETA_eff": 1e-4})

    def test_the_defaults_are_named_in_the_envelope(self):
        net = ("* jf defaults\nVd 1 0 5\nVg 2 0 0\nJ1 1 2 0 jm\n"
               ".model jm NJF()\n.op\n")
        r = spice.simulate(net, analysis="op")
        got = r["assumptions"]["defaulted_model_params"]
        self.assertEqual(got, {"jm.vto": -2.0, "jm.beta": 1e-4,
                               "jm.lambda": 0.0, "jm.area": 1.0})

    def test_only_what_fell_back_is_named(self):
        net = ("* jf defaults\nVd 1 0 5\nVg 2 0 0\nJ1 1 2 0 jm\n"
               ".model jm NJF(VTO=-1 BETA=1m)\n.op\n")
        got = spice.simulate(net, analysis="op")["assumptions"][
            "defaulted_model_params"]
        self.assertEqual(sorted(got), ["jm.area", "jm.lambda"])

class TestPolarity(unittest.TestCase):
    """A PJF is the same equation with the terminals mirrored, and its report
    has to read in the DEVICE's sign convention, not the node convention."""

    def test_a_pjf_with_the_gate_above_the_source_conducts_downwards(self):
        # versus 0 on the source and 0.5 V on the gate: Vov = 0.5-... measured in
        # device terms Vs-Vg = -0.5 against VTO=-1 -> Vov = 0.5, Id = 0.25 mA
        net = ("* pjf\nVd 1 0 -5\nVg 2 0 0.5\nJ1 1 2 0 jm\n"
               ".model jm PJF(VTO=-1 BETA=1m)\n.op\n")
        j1 = _j(net)
        self.assertEqual(j1["polarity"], "PJF")
        self.assertAlmostEqual(j1["current_a"]["id"], -0.25e-3, places=12)
        self.assertAlmostEqual(j1["vds_v"], 5.0, places=9)
        self.assertAlmostEqual(j1["vgs_v"], -0.5, places=9)
        self.assertAlmostEqual(j1["vov_v"], 0.5, places=9)
        self.assertEqual(j1["region"], "saturation")
        # current flows in at the drain so the device dissipates: 5 V * 0.25 mA
        self.assertAlmostEqual(j1["power_w"], 1.25e-3, places=12)

    def test_the_mirror_is_exact(self):
        n = _j("* n\nVd 1 0 5\nVg 2 0 -0.5\nJ1 1 2 0 jm\n"
               ".model jm NJF(VTO=-1 BETA=1m)\n.op\n")
        p = _j("* p\nVd 1 0 -5\nVg 2 0 0.5\nJ1 1 2 0 jm\n"
               ".model jm PJF(VTO=-1 BETA=1m)\n.op\n")
        for key in ("vgs_v", "vov_v", "gm_s", "power_w"):
            self.assertAlmostEqual(n[key], p[key], places=9, msg=key)
        self.assertAlmostEqual(n["current_a"]["id"], -p["current_a"]["id"],
                               places=15)
        self.assertEqual((n["polarity"], p["polarity"]), ("NJF", "PJF"))

class TestGateJunction(unittest.TestCase):
    """The junction is not stamped, so the report has to say WHEN that matters."""

    def test_a_reverse_biased_gate_reads_no_forward_voltage(self):
        self.assertAlmostEqual(_j(_bias(vg=-1.0))["gate_junction_forward_v"],
                               -1.0, places=9)

    def test_half_a_volt_of_forward_bias_is_still_inside_the_model(self):
        # measured: the engine's own gate current is below 1 uA up to ~0.5 V, so
        # 0.3 V is called inside the model and nothing is appended to warnings
        net = _bias(vg=0.3)
        j1 = _j(net)
        self.assertAlmostEqual(j1["gate_junction_forward_v"], 0.3, places=9)
        self.assertNotIn("outside_model", j1)
        self.assertEqual(spice.simulate(net, analysis="op")["warnings"], [])

    def test_past_half_a_volt_the_answer_is_flagged_and_named(self):
        net = _bias(vg=1.0)
        j1 = _j(net)
        self.assertAlmostEqual(j1["gate_junction_forward_v"], 1.0, places=9)
        self.assertIn("outside_model", j1)
        self.assertIn("no gate-channel diode", j1["outside_model"])
        self.assertEqual(len(spice.simulate(net, analysis="op")["warnings"]), 1)

    def test_the_forward_direction_is_mirrored_for_a_p_channel_part(self):
        # measured in ngspice: a PJF's gate junction conducts when the gate sits
        # BELOW the channel (3.14 A at Vg=-4 with the source grounded), the exact
        # mirror of an NJF's (gate above). So the flag has to mirror too.
        p = _j("* p\nVd 1 0 -5\nVg 2 0 -1\nJ1 1 2 0 jm\n"
               ".model jm PJF(VTO=-1 BETA=1m)\n.op\n")
        self.assertAlmostEqual(p["gate_junction_forward_v"], 1.0, places=9)
        self.assertIn("outside_model", p)
        # the same gate voltage on the NJF side is REVERSE bias for an n-channel
        # part only when it is below the channel; +1 V above is forward there
        n = _j(_bias(vg=1.0))
        self.assertIn("outside_model", n)

class TestAC(unittest.TestCase):
    def test_a_common_source_stage_has_gain_minus_gm_times_rd(self):
        # Vg=0, VTO=-2, BETA=1m -> Vov=2, Id=4 mA, gm = 2*1m*2 = 4 mA/V
        # Rd=1k -> |gain| = 4, phase 180 (an inverting stage)
        net = ("* jf ac\nVdd 10 0 12\nRd 10 1 1k\nVg 2 0 DC 0 AC 1\n"
               "J1 1 2 0 jm\n.model jm NJF(VTO=-2 BETA=1m)\n.ac lin 2 1k 2k\n")
        p = _ac(net)
        self.assertAlmostEqual(p["nodes_v"]["1"]["mag"], 4.0, places=6)
        self.assertAlmostEqual(abs(p["nodes_v"]["1"]["phase_deg"]), 180.0,
                               places=6)
        z = cmath.rect(p["nodes_v"]["1"]["mag"],
                       math.radians(p["nodes_v"]["1"]["phase_deg"]))
        self.assertAlmostEqual(z.real, -4.0, places=6)

    def test_a_cut_off_device_has_no_gain(self):
        net = ("* jf ac off\nVdd 10 0 12\nRd 10 1 1k\nVg 2 0 DC -3 AC 1\n"
               "J1 1 2 0 jm\n.model jm NJF(VTO=-2 BETA=1m)\n.ac lin 2 1k 2k\n")
        p = _ac(net)
        self.assertAlmostEqual(p["nodes_v"]["1"]["mag"], 0.0, places=12)

class TestTransient(unittest.TestCase):
    def test_the_output_follows_the_gate_step(self):
        # C1 on the drain, Rd=1k, gate pulsed from -2 (cut off, node sits at 12 V
        # because that is the DC operating point) to 0 (Vov=2, Id clamps at 4 mA,
        # so the node settles at 12-4 = 8 V with tau = 1 ms).
        # At 4 ms: 8 + 4*exp(-3) = 8.199; back at 6 ms the gate returns to -2 and
        # the node climbs to 12 - 4*exp(-2) = 11.46 by 8 ms.
        net = ("* jf tran\nVdd 10 0 12\nRd 10 1 1k\nC1 1 0 1u\n"
               "Vg 2 0 PULSE(-2 0 1m 1u 1u 5m 20m)\nJ1 1 2 0 jm\n"
               ".model jm NJF(VTO=-2 BETA=1m)\n.tran 20u 8m\n")
        v = _tran(net)
        by_t = {round(p["t"], 9): p["nodes_v"]["1"] for p in v["points"]}
        self.assertAlmostEqual(by_t[0.0], 12.0, places=6)
        # backward Euler vs the reference's trapezoid: read 3 decimals
        self.assertAlmostEqual(by_t[4e-3], 8.199, delta=0.05)
        self.assertAlmostEqual(by_t[8e-3], 11.456, delta=0.05)

class TestNoise(unittest.TestCase):
    NET = ("* jf noise\nVdd 1 0 12 AC 1\nRd 1 2 1k\nVg 3 0 0\nJ1 2 3 0 jm\n"
           ".model jm NJF(VTO=-2 BETA=1m)\n.noise V(2) Vdd lin 2 100 200\n")

    def test_the_channel_is_4kt_two_thirds_gm(self):
        # Vov=2, BETA=1m -> gm = 4 mA/V; 4kT*(2/3)*4m = 4.42e-23 A^2/Hz
        v = spice.simulate(self.NET, analysis="noise")["value"]
        src = {s["element"]: s for s in v["sources"]}["J1"]
        self.assertEqual(src["mechanism"], "channel_thermal")
        kb, t = 1.380649e-23, 27.0 + 273.15
        want = 4.0 * kb * t * (2.0 / 3.0) * 4e-3
        self.assertLess(abs(src["psd_a2_per_hz"] / want - 1.0), 1e-9)

    def test_there_is_no_gate_shot_noise_because_there_is_no_gate_current(self):
        v = spice.simulate(self.NET, analysis="noise")["value"]
        mech = [s["mechanism"] for s in v["sources"] if s["element"] == "J1"]
        self.assertEqual(mech, ["channel_thermal"])
        self.assertNotIn("shot", " ".join(mech))

    def test_the_device_dominates_the_load_by_two_thirds_gm_rd(self):
        # the two contributions at the output differ by exactly (2/3)*gm*Rd, the
        # device's transconductance against the resistor's 4kT/R: both the model
        # and the transfer have to be right for this ratio to land
        v = spice.simulate(self.NET, analysis="noise")["value"]
        c = v["points"][0]["contributions_v2_per_hz"]
        self.assertLess(abs(c["J1"] / c["Rd"] / ((2.0 / 3.0) * 4e-3 * 1e3) - 1.0),
                        1e-6)

class TestInsideASubcircuit(unittest.TestCase):
    def test_a_j_card_keeps_its_three_nodes_inside_a_body(self):
        net = ("* jf subckt\nVdd 10 0 12\nRd 10 1 4.7k\nVg 2 0 -0.4\n"
               "X1 1 2 0 jcell\n.subckt jcell d g s\nJ1 d g s jm\n.ends\n"
               ".model jm NJF(VTO=-1 BETA=2m)\n.op\n")
        v = _op(net)
        self.assertAlmostEqual(v["nodes_v"]["1"], 8.616, delta=1e-6)
        j1 = _els(v)["x1.j1"]
        self.assertAlmostEqual(j1["current_a"]["id"], 0.72e-3, places=12)
        self.assertEqual(j1["polarity"], "NJF")

class TestRefusals(unittest.TestCase):
    """Each refusal names the parameter and says why ignoring it would lie."""

    def _bad(self, net):
        r = spice.simulate(net, analysis="op")
        self.assertEqual(r["status"], "error", r)
        self.assertEqual(r["reason_code"], "NETLIST_INVALID", r)
        return r["message"]

    def test_a_missing_model(self):
        msg = self._bad("* x\nVd 1 0 5\nVg 2 0 0\nJ1 1 2 0 nosuch\n.op\n")
        self.assertIn("not defined", msg)

    def test_a_j_card_needs_a_JFET_model_not_a_MOSFET_one(self):
        msg = self._bad(_bias(model_extra="").replace(
            ".model jm NJF(VTO=-1 BETA=0.001)",
            ".model jm NMOS(KP=1e-4 VTO=0.5)"))
        self.assertIn("needs a NJF/PJF model", msg)
        self.assertIn("NMOS", msg)

    def test_a_drain_resistance_is_refused_because_it_is_an_internal_node(self):
        msg = self._bad(_bias(model_extra=" RD=100"))
        self.assertIn("RD= is a drain series resistance", msg)
        self.assertIn("14%", msg)

    def test_a_source_resistance_is_refused_for_the_same_reason(self):
        msg = self._bad(_bias(model_extra=" RS=100"))
        self.assertIn("RS= is a source series resistance", msg)

    def test_the_doping_tail_parameter_is_a_different_equation(self):
        msg = self._bad(_bias(model_extra=" B=2"))
        self.assertIn("B= is the doping-tail parameter", msg)
        self.assertIn("1.5x", msg)

    def test_a_gate_junction_parameter_needs_a_junction_this_subset_does_not_have(self):
        for param in ("IS=1e-14", "PB=0.7", "MJ=0.5", "FC=0.5"):
            msg = self._bad(_bias(model_extra=" " + param))
            self.assertIn("gate junction", msg, param)

    def test_device_capacitances_are_refused_because_ac_has_none(self):
        for param in ("CGS=1p", "CGD=1p"):
            msg = self._bad(_bias(model_extra=" " + param))
            self.assertIn("capacitance", msg, param)

    def test_flicker_noise_is_refused_rather_than_silently_omitted(self):
        """Since 0.0.54 the refusal moved to where the lie would be told.

        KF= on a JFET used to fail at .op. It no longer does, because at .op it
        changes nothing - exactly as in SPICE - and refusing a circuit for a
        parameter that cannot affect the asked analysis is its own kind of wrong
        answer. The refusal now sits at the .noise analysis, which is the only
        place an ignored KF= would turn into a quiet number; there is no
        reference model to copy for a JFET (measured: ngspice-47 moved its own
        answer by 0.0 % for KF 1e-15..1e-6).
        """
        # .op is fine: nothing there can be affected
        v = _op(_bias(model_extra=" KF=1e-16"))
        self.assertAlmostEqual(_els(v)["J1"]["current_a"]["id"], 1e-3, places=12)
        # .noise is not: name the device instead of reporting thermal+shot only
        net = _bias(model_extra=" KF=1e-16").replace(
            ".op", ".noise V(1) Vd lin 3 100 300")
        r = spice.simulate(net)
        self.assertEqual(r["status"], "error", r)
        self.assertEqual(r["reason_code"], "NOT_MODELED")
        self.assertIn("flicker", r["message"])
        self.assertIn("J1", r["message"])
        self.assertIn("0.0 %", r["hint"])
        # and a bare AF= with no KF= has no flicker term to model: it answers
        r2 = spice.simulate(_bias(model_extra=" AF=2").replace(
            ".op", ".noise V(1) Vd lin 3 100 300"))
        self.assertEqual(r2["status"], "ok", r2)

    def test_temperature_parameters_are_refused_at_one_temperature(self):
        for param in ("TCV=1m", "BEX=1"):
            msg = self._bad(_bias(model_extra=" " + param))
            self.assertIn("one temperature", msg, param)

    def test_the_level_two_parameters_are_named(self):
        for param in ("BETA1=1m", "LAMBDA1=0.1"):
            msg = self._bad(_bias(model_extra=" " + param))
            self.assertIn("level-2", msg, param)

    def test_an_unknown_model_parameter_lists_what_is_implemented(self):
        msg = self._bad(_bias(model_extra=" FOO=1"))
        self.assertIn("not a parameter of a NJF JFET", msg)
        self.assertIn("VTO, BETA, LAMBDA, AREA", msg)

    def test_the_zero_valued_and_unity_parameters_are_let_through(self):
        # RD=0 / RS=0 / B=1 ARE the engine's defaults: refusing them would reject
        # every model card copied from a real part library
        for param in ("RD=0", "RS=0", "B=1"):
            r = spice.simulate(_bias(model_extra=" " + param), analysis="op")
            self.assertEqual(r["status"], "ok", param)

    def test_a_geometry_parameter_on_the_card_is_refused(self):
        # measured: ngspice itself answers "unknown parameter (w)" on a J card
        msg = self._bad(_bias(card_extra=" W=2u"))
        self.assertIn("W= and L= are refused by ngspice itself", msg)
        msg = self._bad(_bias(card_extra=" L=2u"))
        self.assertIn("W= and L= are refused by ngspice itself", msg)

    def test_a_zero_area_is_refused_on_both_the_card_and_the_model(self):
        self.assertIn("cannot be zero", self._bad(_bias(card_extra=" AREA=0")))
        self.assertIn("cannot be zero", self._bad(_bias(model_extra=" AREA=0")))

    def test_a_nonsense_transconductance_or_lambda_is_refused(self):
        self.assertIn("cannot be zero or negative", self._bad(_bias(beta=0.0)))
        self.assertIn("negative channel-length modulation",
                      self._bad(_bias(model_extra=" LAMBDA=-0.1")))

    def test_a_truncated_card_shows_the_shape(self):
        msg = self._bad("* x\nVd 1 0 5\nVg 2 0 0\nJ1 1 2 0\n"
                        ".model jm NJF(VTO=-1 BETA=1m)\n.op\n")
        self.assertIn("Jname nd ng ns model [AREA=<x>]", msg)

    def test_a_parameter_where_the_model_name_belongs_is_named(self):
        # the fourth field is the model: a reader who counts four nodes lands here
        msg = self._bad("* x\nVd 1 0 5\nVg 2 0 0\nJ1 1 2 0 AREA=2\n"
                        ".model jm NJF(VTO=-1 BETA=1m)\n.op\n")
        self.assertIn("three nodes (drain, gate, source)", msg)

    def test_trailing_junk_is_not_a_field(self):
        msg = self._bad(_bias(card_extra=" maybe"))
        self.assertIn("is not a field of a J card", msg)

    def test_the_off_flag_is_accepted_and_does_not_change_the_answer(self):
        # OFF is an initial guess, not a latch: the operating point is solved
        on = _j(_bias(card_extra=" OFF"))
        off = _j(_bias())
        self.assertAlmostEqual(on["current_a"]["id"], off["current_a"]["id"],
                               places=15)

@unittest.skipUnless(spice.find_ngspice(), "ngspice binary not found")
class RealEngineAgrees(unittest.TestCase):
    """The equation is the engine's, so the engine is the oracle for it."""

    def _both(self, net, analysis="op"):
        mine = spice.simulate(net, analysis=analysis)
        theirs = spice.simulate(net, analysis=analysis, engine="ngspice")
        self.assertEqual(mine["status"], "ok", mine)
        self.assertEqual(theirs["status"], "ok", theirs)
        return mine["value"], theirs["value"]

    def test_the_whole_id_vs_vds_family_matches(self):
        for vd in (0.1, 0.3, 1.0, 1.5, 5.0, 10.0):
            mine, theirs = self._both(_bias(vd=vd))
            for node, val in theirs["nodes_v"].items():
                self.assertAlmostEqual(mine["nodes_v"][node], val, places=8,
                                       msg="vd=%s node %s" % (vd, node))

    def test_the_bias_network_matches(self):
        net = ("* jf bias\nVdd 10 0 12\nRd 10 1 4.7k\nVg 2 0 -0.4\nJ1 1 2 0 jm\n"
               ".model jm NJF(VTO=-1 BETA=2m LAMBDA=0.02)\n.op\n")
        mine, theirs = self._both(net)
        for node, val in theirs["nodes_v"].items():
            self.assertAlmostEqual(mine["nodes_v"][node], val, places=8)

    def test_the_area_semantics_match(self):
        for extra in (" AREA=2", ""):
            net = _bias(model_extra=extra, card_extra=" AREA=3")
            mine, theirs = self._both(net)
            for node, val in theirs["nodes_v"].items():
                self.assertAlmostEqual(mine["nodes_v"][node], val, places=8,
                                       msg=extra)

    def test_the_default_parameters_are_the_engines(self):
        # an empty model card: these are the numbers ngspice fills in, which is
        # where the module's defaults came from in the first place
        net = ("* jf defaults\nVd 1 0 5\nVg 2 0 0\nJ1 1 2 0 jm\n"
               ".model jm NJF()\n.op\n")
        mine, theirs = self._both(net)
        for node, val in theirs["nodes_v"].items():
            self.assertAlmostEqual(mine["nodes_v"][node], val, places=8)

    def test_a_p_channel_part_matches(self):
        net = ("* pjf\nVd 1 0 -5\nVg 2 0 0.5\nJ1 1 2 0 jm\n"
               ".model jm PJF(VTO=-1 BETA=1m)\n.op\n")
        mine, theirs = self._both(net)
        for node, val in theirs["nodes_v"].items():
            self.assertAlmostEqual(mine["nodes_v"][node], val, places=8)

    def test_the_ac_gain_matches(self):
        net = ("* jf ac\nVdd 10 0 12\nRd 10 1 1k\nVg 2 0 DC 0 AC 1\n"
               "J1 1 2 0 jm\n.model jm NJF(VTO=-2 BETA=1m)\n.ac lin 2 1k 2k\n")
        mine = spice.simulate(net, analysis="ac")["value"]["points"][0]
        theirs = spice.simulate(net, analysis="ac", engine="ngspice")[
            "value"]["points"][0]
        m = cmath.rect(mine["nodes_v"]["1"]["mag"],
                       math.radians(mine["nodes_v"]["1"]["phase_deg"]))
        t = cmath.rect(theirs["nodes_v"]["1"]["mag"],
                       math.radians(theirs["nodes_v"]["1"]["phase_deg"]))
        self.assertLess(abs(m - t), 1e-6)

    def test_the_transient_tail_agrees_to_a_few_percent(self):
        # the gate step is quantised to a solver step, so the comparison is the
        # tail: there backward Euler vs the engine's trapezoid is the only gap
        net = ("* jf tran\nVdd 10 0 12\nRd 10 1 1k\nC1 1 0 1u\n"
               "Vg 2 0 PULSE(-2 0 1m 1u 1u 5m 20m)\nJ1 1 2 0 jm\n"
               ".model jm NJF(VTO=-2 BETA=1m)\n.tran 20u 8m\n")
        mine = spice.simulate(net)["value"]["points"]
        theirs = [(p["t"], p["nodes_v"]["1"])
                  for p in spice.simulate(net, engine="ngspice")["value"]["points"]]

        def interp(t):
            if t <= theirs[0][0]:
                return theirs[0][1]
            for (t0, v0), (t1, v1) in zip(theirs, theirs[1:]):
                if t <= t1:
                    return v1 if t1 == t0 else v0 + (v1 - v0) * (t - t0) / (t1 - t0)
            return theirs[-1][1]

        worst = 0.0
        for p in mine:
            if 0.9e-3 <= p["t"] <= 1.2e-3:
                continue          # the step itself: one step of quantisation
            worst = max(worst, abs(p["nodes_v"]["1"] - interp(p["t"])))
        self.assertLess(worst / 12.0, 0.01, "worst %.3g V" % worst)

if __name__ == "__main__":
    unittest.main()
