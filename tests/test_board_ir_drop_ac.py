"""Golden tests for board_ir_drop at a FREQUENCY (0.0.70).

The claim being tested is narrow and checkable: at a frequency each conductor
keeps its geometry and gets the effective thickness of the exact 1-D solve, so
a track's drop is its DC drop times a ratio that `skin.ac_sheet_resistance`
publishes for the same foil, and a via's barrel is scaled by
`roundwire.ac_round_conductor` for the same tube. Nothing about the network
changes - which is why every DC assertion in `test_board_ir_drop.py` must still
hold to the last digit, and that is asserted here too.

Every mechanism below has a control that can fail:

  * a 35 um and a 70 um track must get DIFFERENT ratios, and using one ratio for
    both must give a measurably different total (a "one number for the whole
    board" implementation fails this),
  * a via must get the TUBE ratio, which is smaller than the slab ratio for the
    same wall thickness (a "barrel = flat foil" implementation fails this),
  * passing only faces/mu_r/roughness without a frequency is refused, because
    answering at DC after accepting them would be a wrong number by omission.
"""
import io
import itertools
import json
import os
import sys
import tempfile
import unittest

sys.path.insert(0, os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "src"))

from pcb_agent_mcp import oracle  # noqa: E402
from pcb_agent_mcp.adapters import kicad_pcb  # noqa: E402
from pcb_agent_mcp.calc import roundwire as RW  # noqa: E402
from pcb_agent_mcp.calc import skin as SK  # noqa: E402

_seq = itertools.count()


def _write(text):
    path = os.path.join(tempfile.gettempdir(),
                        "ir_drop_ac_%d_%d.kicad_pcb" % (os.getpid(), next(_seq)))
    with io.open(path, "w", encoding="utf-8", newline="") as fh:
        fh.write(text)
    return kicad_pcb.parse_board(path)


def _board(f_cu=0.035, b_cu=0.035, sink="B.Cu", zone=False):
    """20 mm of 0.5 mm copper from U1.1 to U2.1.

    sink="B.Cu" routes it F.Cu -> via -> B.Cu (so the barrel is in the answer);
    sink="F.Cu" keeps U2 on the top layer, where a single track reaches it.
    """
    body = []
    if sink == "F.Cu":
        body.append('  (segment (start 10 10) (end 30 10) (width 0.5) '
                    '(layer "F.Cu") (net 1))')
        u2 = ('  (footprint "T:B" (layer "F.Cu") (at 30 10)\n'
              '    (fp_text reference "U2" (at 0 -2) (layer "F.SilkS"))\n'
              '    (pad "1" smd rect (at 0 0) (size 1 1) (layers "F.Cu") '
              '(net 1 "VCC")))\n')
    else:
        body.append('  (segment (start 10 10) (end 20 10) (width 0.5) '
                    '(layer "F.Cu") (net 1))')
        body.append('  (via (at 20 10) (size 0.6) (drill 0.3) '
                    '(layers "F.Cu" "B.Cu") (net 1))')
        body.append('  (segment (start 20 10) (end 30 10) (width 0.5) '
                    '(layer "B.Cu") (net 1))')
        u2 = ('  (footprint "T:B" (layer "B.Cu") (at 30 10)\n'
              '    (fp_text reference "U2" (at 0 -2) (layer "B.SilkS"))\n'
              '    (pad "1" smd rect (at 0 0) (size 1 1) (layers "B.Cu") '
              '(net 1 "VCC")))\n')
    if zone:
        body.append("""  (zone (net 1) (net_name "VCC") (layer "F.Cu") (tstamp 0)
    (hatch edge 0.508) (priority 0)
    (connect_pads (clearance 0.2))
    (min_thickness 0.25)
    (fill yes (thermal_gap 0.5) (thermal_bridge_width 0.5))
    (polygon (pts (xy 5 5) (xy 35 5) (xy 35 15) (xy 5 15)))
    (filled_polygon (layer "F.Cu")
      (pts (xy 5 5) (xy 35 5) (xy 35 15) (xy 5 15))))""")
    head = (
        '(kicad_pcb (version 20221018) (generator pcbnew)\n'
        '  (general (thickness 1.6))\n'
        '  (layers (0 "F.Cu" signal) (31 "B.Cu" signal) '
        '(44 "Edge.Cuts" user))\n'
        '  (setup (stackup\n'
        '      (layer "F.Cu" (type "copper") (thickness {f_cu:g}))\n'
        '      (layer "dielectric 1" (type "core") (thickness 1.53) '
        '(epsilon_r 4.3))\n'
        '      (layer "B.Cu" (type "copper") (thickness {b_cu:g}))))\n'
        '  (net 0 "") (net 1 "VCC")\n'
        '  (gr_rect (start 0 0) (end 50 30) (layer "Edge.Cuts") (width 0.1))\n'
        '  (footprint "T:A" (layer "F.Cu") (at 10 10)\n'
        '    (fp_text reference "U1" (at 0 -2) (layer "F.SilkS"))\n'
        '    (pad "1" smd rect (at 0 0) (size 1 1) (layers "F.Cu") '
        '(net 1 "VCC")))\n').format(f_cu=f_cu, b_cu=b_cu)
    return _write(head + u2 + "\n".join(body) + "\n)\n")


def _drop(brd, **kw):
    return oracle.board_ir_drop(brd, "VCC", "U1.1", ["U2.1"], current_a=1.0, **kw)


def _slug(r):
    """The whole envelope minus the mother of all noise: no floats lost."""
    return json.dumps(r, sort_keys=True, default=repr)


class TestTheDefaultIsTheDcAnswer(unittest.TestCase):
    def test_the_new_knobs_default_to_off(self):
        import inspect
        sig = inspect.signature(oracle.board_ir_drop)
        self.assertIsNone(sig.parameters["frequency_hz"].default)
        self.assertEqual(sig.parameters["faces"].default, "one")
        self.assertEqual(sig.parameters["mu_r"].default, 1.0)
        self.assertIsNone(sig.parameters["roughness_rq_um"].default)

    def test_passing_the_defaults_explicitly_changes_nothing(self):
        # The point of "off by default" is not the default value, it is that the
        # answer is the one it always was - so the explicit defaults are compared
        # against the omitted ones, bit for bit, on a board WITH a via and a
        # pour (the two paths that get touched).
        for brd in (_board(), _board(zone=True)):
            for kw_extra in ({}, {"model_zones": True, "zone_grid_mm": 0.5,
                                 "max_zone_nodes": 20000}):
                a = _drop(brd, plating_um=25.0, **kw_extra)
                b = _drop(brd, plating_um=25.0, frequency_hz=None, faces="one",
                          mu_r=1.0, roughness_rq_um=None, **kw_extra)
                self.assertEqual(_slug(a), _slug(b))
                self.assertNotIn("frequency_hz", a["value"])
                self.assertNotIn("ac_summary", a["value"])
                self.assertNotIn("frequency_hz", a["assumptions"])
                self.assertIn("skin effect", a["not_computed"])

    def test_the_dc_numbers_the_069_tests_pin_are_still_exact(self):
        r = _drop(_board(), plating_um=25.0)
        self.assertAlmostEqual(r["value"]["max_drop_mv"], 20.979986, places=5)
        via = [e for e in r["value"]["edges"] if e["kind"] == "via"][0]
        self.assertAlmostEqual(via["drop_mv"], 1.277129, places=6)


class TestOneTrackAtOneFrequency(unittest.TestCase):
    def test_the_ratio_is_the_number_skin_publishes(self):
        # The whole design of this tool is "no second physics". If the ratio here
        # ever drifts from what `ac_sheet_resistance` says for the same foil, one
        # of the two is wrong and a caller has no way to tell which.
        r = _drop(_board(sink="F.Cu"), frequency_hz=1e6)
        sk = SK.ac_sheet_resistance(1e6, thickness_um=35.0)["value"]
        self.assertAlmostEqual(r["value"]["ac_summary"]["track"]["ratio_max"],
                               sk["r_ac_over_r_dc"], places=9)
        self.assertAlmostEqual(r["value"]["ac_skin_depth_um"],
                               sk["skin_depth_um"], places=9)

    def test_twenty_millimetres_of_one_ounce_copper_at_one_megahertz(self):
        # DC: 1.724e-8*20e-3/(0.5e-3*35e-6) = 19.702857 mOhm
        # 1 MHz: x 1.0069737 (skin depth 66.08 um against 35 um of copper)
        dc = _drop(_board(sink="F.Cu"))
        ac = _drop(_board(sink="F.Cu"), frequency_hz=1e6)
        self.assertAlmostEqual(dc["value"]["max_drop_mv"], 19.702857, places=6)
        self.assertAlmostEqual(ac["value"]["max_drop_mv"], 19.840259, places=6)
        self.assertAlmostEqual(ac["value"]["total_copper_loss_w"], 0.019840259,
                               places=9)
        self.assertEqual(ac["value"]["frequency_hz"], 1e6)
        self.assertEqual(ac["value"]["ac_faces"], "one")

    def test_the_edge_keeps_both_thicknesses(self):
        ac = _drop(_board(sink="F.Cu"), frequency_hz=1e6)
        self.assertNotIn("copper_eff_um", ac["value"]["edges"][0])
        # the edge list is the public view; the effective thickness is a model
        # detail, so what a caller sees is that the resistance moved
        dc = _drop(_board(sink="F.Cu"))
        self.assertGreater(ac["value"]["edges"][0]["resistance_ohm"],
                           dc["value"]["edges"][0]["resistance_ohm"])

    def test_the_answer_says_what_it_left_out(self):
        ac = _drop(_board(), plating_um=25.0, frequency_hz=1e6)
        nc = ac["not_computed"]
        self.assertNotIn("skin effect", nc)
        self.assertTrue(any("proximity" in s for s in nc), nc)
        self.assertTrue(any("lateral current crowding" in s for s in nc), nc)
        self.assertIn("1e+06 Hz", ac["precision"])
        self.assertIn("ONE-FREQUENCY", ac["precision"])
        self.assertIn("1e+06 Hz", ac["basis"])
        self.assertEqual(ac["assumptions"]["frequency_hz"], 1e6)
        self.assertEqual(ac["assumptions"]["ac_faces"], "one")
        self.assertTrue(any("DC average over the PHYSICAL cross-section" in w
                            for w in ac["warnings"]), ac["warnings"])


class TestEachConductorGetsItsOwnRatio(unittest.TestCase):
    def test_thirty_five_and_seventy_microns_do_not_share_a_ratio(self):
        # Same 10 mm on each layer, but 1 oz on top and 2 oz underneath. The thin
        # one is further into the skin effect, so its ratio is the smaller one -
        # and one ratio for the whole board is a different answer.
        r = _drop(_board(f_cu=0.035, b_cu=0.070), plating_um=25.0,
                  frequency_hz=1e6)
        s = r["value"]["ac_summary"]["track"]
        self.assertEqual(s["segments"], 2)
        self.assertAlmostEqual(s["ratio_min"],
                               SK.ac_sheet_resistance(1e6, thickness_um=35.0)
                               ["value"]["r_ac_over_r_dc"], places=9)
        self.assertAlmostEqual(s["ratio_max"],
                               SK.ac_sheet_resistance(1e6, thickness_um=70.0)
                               ["value"]["r_ac_over_r_dc"], places=9)
        self.assertGreater(s["ratio_max"] - s["ratio_min"], 0.09)

        # The control: ONE ratio applied to both tracks (the "multiply the board
        # resistance by one number" implementation) is a different total, and the
        # difference is far above float noise.
        one_ratio = (SK.ac_sheet_resistance(1e6, thickness_um=35.0)
                     ["value"]["r_ac_over_r_dc"])
        dc = _drop(_board(f_cu=0.035, b_cu=0.070), plating_um=25.0)
        wrong = dc["value"]["max_drop_mv"] * one_ratio
        self.assertGreater(abs(r["value"]["max_drop_mv"] - wrong), 1e-3)

    def test_the_ratio_only_grows_with_frequency(self):
        last = None
        for f in (1e3, 1e4, 1e5, 1e6, 1e7, 1e8, 1e9):
            r = _drop(_board(), plating_um=25.0, frequency_hz=f)
            v = r["value"]
            if last is not None:
                self.assertGreaterEqual(v["max_drop_mv"], last["max_drop_mv"])
                self.assertGreaterEqual(v["ac_summary"]["track"]["ratio_max"],
                                        last["ac_summary"]["track"]["ratio_max"])
            last = v
        # and at 1 GHz a DC answer is no longer a bound, it is a different
        # number: 35 um of copper is 16.75x DC, and the 17.5 um foil below is
        # 8.37x at the same 1 GHz (see TestFacesAndRoughness)
        self.assertAlmostEqual(last["ac_summary"]["track"]["ratio_max"],
                               16.748629747, places=6)


class TestViasUseTheTube(unittest.TestCase):
    def test_the_barrel_is_scaled_by_the_bessel_solution(self):
        r = _drop(_board(), plating_um=25.0, frequency_hz=1e6)
        tube = RW.ac_round_conductor(1e6, drill_mm=0.3, plating_um=25.0)["value"]
        self.assertAlmostEqual(r["value"]["ac_summary"]["via"]["ratio_max"],
                               tube["ac_over_dc"], places=9)

    def test_a_barrel_is_not_a_flat_foil(self):
        # The control for the previous test. Honest size of the effect: solving
        # the same 25 um of copper as a SLAB instead of the tube it is gives
        # 1.00168 -> 1.00182 at 1 MHz (0.01 % : nothing) but 11.96331 against
        # 11.17541 at 1 GHz, i.e. the slab answer is 7.05 % HIGH. That is above
        # any rounding, so "treat the barrel as foil" is a different answer - and
        # it is also a reminder that the barrel's geometry costs far more at DC
        # (0.0.67 measured 5.26x for calling a 0.3 mm hole a square) than it does
        # inside the AC ratio.
        tube = RW.ac_round_conductor(1e9, drill_mm=0.3, plating_um=25.0)
        slab = SK.ac_sheet_resistance(1e9, thickness_um=25.0)
        self.assertGreater(slab["value"]["r_ac_over_r_dc"] /
                           tube["value"]["ac_over_dc"], 1.05)
        r = _drop(_board(), plating_um=25.0, frequency_hz=1e9)
        self.assertAlmostEqual(r["value"]["ac_summary"]["via"]["ratio_max"],
                               tube["value"]["ac_over_dc"], places=9)


class TestFacesAndRoughness(unittest.TestCase):
    def test_two_faces_halves_the_penetration(self):
        # "two faces" means the return current is symmetric, so the argument of
        # the slab solution is halved (t/(2*delta) instead of t/delta). Two
        # regimes, and they scale DIFFERENTLY - which is why both are checked:
        #   deep skin (1 GHz, s = 16.7): R ~ s, so the ratio halves exactly
        #   far from the limit (1 MHz, s = 0.53): R-1 ~ 4s^4/45, so the excess
        #   is divided by 16, not by 2 or 4
        r1 = _drop(_board(sink="F.Cu"), frequency_hz=1e9)["value"] \
            ["ac_summary"]["track"]["ratio_max"]
        r2 = _drop(_board(sink="F.Cu"), frequency_hz=1e9,
                   faces="two")["value"]["ac_summary"]["track"]["ratio_max"]
        self.assertAlmostEqual(r1, 16.748629747, places=6)
        self.assertAlmostEqual(r2, 8.374313654, places=6)
        self.assertAlmostEqual(r2 / r1, 0.5, places=3)

        lo1 = _drop(_board(sink="F.Cu"), frequency_hz=1e6)["value"] \
            ["ac_summary"]["track"]["ratio_max"]
        lo2 = _drop(_board(sink="F.Cu"), frequency_hz=1e6,
                    faces="two")["value"]["ac_summary"]["track"]["ratio_max"]
        self.assertAlmostEqual(lo1, 1.006973727, places=6)
        self.assertAlmostEqual(lo2, 1.000437082, places=6)
        self.assertAlmostEqual((lo2 - 1.0) / (lo1 - 1.0), 1.0 / 16.0, places=2)

    def test_roughness_can_only_add_resistance(self):
        smooth = _drop(_board(sink="F.Cu"), frequency_hz=1e9,
                       copper_um=17.5)
        rough = _drop(_board(sink="F.Cu"), frequency_hz=1e9,
                      copper_um=17.5, roughness_rq_um=1.0)
        self.assertAlmostEqual(smooth["value"]["ac_summary"]["track"]["ratio_max"],
                               8.374313654, places=6)
        self.assertAlmostEqual(rough["value"]["ac_summary"]["track"]["ratio_max"],
                               10.028273309, places=6)
        self.assertGreater(rough["value"]["max_drop_mv"],
                           smooth["value"]["max_drop_mv"])


class TestZonesAtAFrequency(unittest.TestCase):
    def test_a_pour_gets_the_same_thickness_treatment(self):
        kw = dict(model_zones=True, zone_grid_mm=0.5, max_zone_nodes=20000)
        dc = _drop(_board(zone=True), plating_um=25.0, **kw)
        ac = _drop(_board(zone=True), plating_um=25.0, frequency_hz=1e6, **kw)
        self.assertEqual(ac["status"], "ok")
        sheet = ac["zone_summary"]["sheets"][0]
        self.assertEqual(sheet["thickness_um"], 35.0)          # physical weight
        self.assertAlmostEqual(sheet["copper_eff_um"], 34.75760990931462,
                               places=9)
        self.assertAlmostEqual(sheet["r_ac_over_rdc"], 1.006973727, places=9)
        self.assertAlmostEqual(sheet["sheet_ohm_per_sq_dc"],
                               0.000492571, places=9)
        # both sheet resistances are rounded to 9 decimals of an ohm, so the
        # quotient carries about 1e-6 of absolute slop
        ratio = sheet["sheet_ohm_per_sq"] / sheet["sheet_ohm_per_sq_dc"]
        self.assertAlmostEqual(ratio, sheet["r_ac_over_rdc"], places=6)
        self.assertGreater(ac["value"]["max_drop_mv"], dc["value"]["max_drop_mv"])
        self.assertGreater(ac["value"]["ac_summary"]["zone"]["segments"], 1000)
        # the DC table keeps exactly the keys it always had
        self.assertNotIn("copper_eff_um", dc["zone_summary"]["sheets"][0])
        self.assertNotIn("r_ac_over_rdc", dc["zone_summary"]["sheets"][0])

    def test_the_dc_table_of_a_frequency_answer_still_has_the_physical_weight(self):
        ac = _drop(_board(zone=True), plating_um=25.0, frequency_hz=1e6,
                   model_zones=True, zone_grid_mm=0.5, max_zone_nodes=20000)
        sheet = ac["zone_summary"]["sheets"][0]
        self.assertGreater(sheet["thickness_um"], sheet["copper_eff_um"])


class TestRefusals(unittest.TestCase):
    def _refuse(self, code, **kw):
        r = _drop(_board(), **kw)
        self.assertEqual(r["status"], "error", r)
        self.assertEqual(r["reason_code"], code, r.get("message"))
        return r

    def test_zero_frequency_is_not_a_skin_depth(self):
        r = self._refuse("ARG_RANGE", frequency_hz=0)
        self.assertIn("frequency_hz must be > 0", r["message"])

    def test_negative_frequency(self):
        self._refuse("ARG_RANGE", frequency_hz=-1e6)

    def test_a_third_face_count(self):
        r = self._refuse("ARG_INVALID", frequency_hz=1e6, faces="three")
        self.assertIn("factor of two", r["message"])

    def test_mu_r_below_one(self):
        self._refuse("ARG_RANGE", frequency_hz=1e6, mu_r=0.5)

    def test_negative_roughness(self):
        self._refuse("ARG_RANGE", frequency_hz=1e6, roughness_rq_um=-1.0)

    def test_faces_without_a_frequency_is_refused_not_ignored(self):
        r = self._refuse("ARG_INVALID", faces="two")
        self.assertIn("frequency_hz", r["message"])

    def test_mu_r_without_a_frequency_is_refused(self):
        self._refuse("ARG_INVALID", mu_r=2.0)

    def test_roughness_without_a_frequency_is_refused(self):
        self._refuse("ARG_INVALID", roughness_rq_um=1.0)


class TestSchema(unittest.TestCase):
    def test_the_tool_advertises_the_knobs(self):
        from pcb_agent_mcp import tools as T
        d = T.TOOLS["board_ir_drop"]
        sch = d.get("inputSchema") or d
        props = sch["properties"]
        for key in ("frequency_hz", "faces", "mu_r", "roughness_rq_um"):
            self.assertIn(key, props)
        self.assertEqual(props["faces"]["enum"], ["one", "two"])
        self.assertEqual(props["mu_r"]["default"], 1.0)
        self.assertNotIn("frequency_hz", sch.get("required") or [])
        self.assertIn("frequency_hz", d["description"])


if __name__ == "__main__":
    unittest.main()
