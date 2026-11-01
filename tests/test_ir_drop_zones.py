"""Golden tests for board_ir_drop(model_zones=True) - the copper pour as copper.

Why this exists: on a plane-heavy board the two halves of a power net are often
joined by NOTHING but the pour, so the track-only answer is not "a bit high", it
is `singular`. Modelling the pour as a 2-D sheet of rho/t is the difference
between a number and no number - and the numbers below are anchored on a case
whose answer is known by hand, so "we got a number" cannot pass for "we got the
right number".

Every claim has a control that can fail:

  * the pour-only net must still be refused with the switch OFF (otherwise the
    default path silently changed and every historical answer is suspect),
  * a pour that does not reach the sink must say NOT_CONNECTED instead of a
    small drop,
  * a pour on another net must not be counted,
  * the CG solver must reproduce the dense solver bit-for-bit on networks that
    have no pour at all (that is the "we swapped the linear algebra" regression).
"""
import itertools
import math
import os
import sys
import tempfile
import unittest

sys.path.insert(0, os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "src"))

from pcb_agent_mcp import oracle  # noqa: E402
from pcb_agent_mcp.adapters import kicad_pcb  # noqa: E402
from pcb_agent_mcp.calc import power as PW  # noqa: E402
from pcb_agent_mcp.calc import zonefield as ZF  # noqa: E402

RHO20 = 1.724e-8
_seq = itertools.count()


def sheet_ohm_per_sq(thick_um=35.0):
    """rho(20C)/t, in ohm per square. 1.724e-8 ohm*m over 35 um."""
    return RHO20 / (thick_um * 1e-6)

HEAD = """(kicad_pcb (version 20221018) (generator pcbnew)
  (general (thickness 1.6))
  (layers (0 "F.Cu" signal) (31 "B.Cu" signal) (44 "Edge.Cuts" user))
  (setup (stackup
      (layer "F.Cu" (type "copper") (thickness 0.035))
      (layer "dielectric 1" (type "core") (thickness 1.53) (epsilon_r 4.3))
      (layer "B.Cu" (type "copper") (thickness 0.035))))
  (net 0 "") (net 1 "VCC") (net 2 "VCC2")
  (gr_rect (start 0 0) (end 50 30) (layer "Edge.Cuts") (width 0.1))
"""

# Two 1 x 10 mm pads, 20 mm apart, on the same layer.  A 1 x 10 mm pad spans the
# full height of the pour, so the current is a straight 20 mm wide sheet and the
# answer is countable by hand: 20 mm / 10 mm = 2 squares.
PADS = """  (footprint "T:A" (layer "F.Cu") (at 10 10)
    (fp_text reference "U1" (at 0 -6) (layer "F.SilkS"))
    (pad "1" smd rect (at 0 0) (size 1 10) (layers "F.Cu") (net 1 "VCC")))
  (footprint "T:B" (layer "F.Cu") (at 30 10)
    (fp_text reference "U2" (at 0 -6) (layer "F.SilkS"))
    (pad "1" smd rect (at 0 0) (size 1 10) (layers "F.Cu") (net 1 "VCC")))
"""


def zone(x0, y0, x1, y1, net=1, name="VCC", layer="F.Cu"):
    return ("""  (zone (net %d) (net_name "%s") (layer "%s") (tstamp 0)
    (hatch edge 0.508) (priority 0)
    (connect_pads (clearance 0.2))
    (min_thickness 0.25)
    (fill yes (thermal_gap 0.5) (thermal_bridge_width 0.5))
    (polygon (pts (xy %.6g %.6g) (xy %.6g %.6g) (xy %.6g %.6g) (xy %.6g %.6g)))
    (filled_polygon (layer "%s")
      (pts (xy %.6g %.6g) (xy %.6g %.6g) (xy %.6g %.6g) (xy %.6g %.6g))))
""" % (net, name, layer, x0, y0, x1, y0, x1, y1, x0, y1,
           layer, x0, y0, x1, y0, x1, y1, x0, y1))


# The cross-layer fixture: sink pad on B.Cu, so the only way across is the via.
PADS_XLAYER = """  (footprint "T:A" (layer "F.Cu") (at 10 10)
    (fp_text reference "U1" (at 0 -2) (layer "F.SilkS"))
    (pad "1" smd rect (at 0 0) (size 1 1) (layers "F.Cu") (net 1 "VCC")))
  (footprint "T:B" (layer "B.Cu") (at 30 10)
    (fp_text reference "U2" (at 0 -2) (layer "B.SilkS"))
    (pad "1" smd rect (at 0 0) (size 1 1) (layers "B.Cu") (net 1 "VCC")))
"""


def write_board(body, pads=PADS):
    path = os.path.join(tempfile.gettempdir(),
                        "ir_drop_zone_%d_%d.kicad_pcb" % (os.getpid(), next(_seq)))
    with open(path, "w", newline="") as fh:
        fh.write(HEAD + pads + body + "\n)\n")
    return path


def make(body, pads=PADS):
    return kicad_pcb.parse_board(write_board(body, pads))


FULL = zone(5, 5, 35, 15)          # reaches both pads
LEFT_ONLY = zone(5, 5, 15, 15)     # reaches U1 only
NO_TRACKS = ""


class TestTheSwitchIsOffByDefault(unittest.TestCase):
    """The default path must be byte-identical to before the pour existed."""

    def test_a_pour_only_net_is_still_refused_with_the_switch_off(self):
        r = oracle.board_ir_drop(make(FULL), "VCC", "U1.1", ["U2.1"], current_a=1.0)
        self.assertEqual(r["status"], "error")
        self.assertEqual(r["reason_code"], "NOT_APPLICABLE")
        self.assertIn("no tracks", r["message"])

    def test_the_default_does_not_build_a_zone_grid(self):
        r = oracle.board_ir_drop(make(FULL), "VCC", "U1.1", ["U2.1"], current_a=1.0,
                                 model_zones=False)
        self.assertEqual(r["reason_code"], "NOT_APPLICABLE")
        self.assertNotIn("zone_summary", r)  # errors carry no payload at all

    def test_the_switch_is_not_the_default_in_the_signature(self):
        import inspect
        sig = inspect.signature(oracle.board_ir_drop)
        self.assertIs(sig.parameters["model_zones"].default, False)
        self.assertIsNone(sig.parameters["zone_grid_mm"].default)


class TestSheetResistanceByHand(unittest.TestCase):
    def test_two_squares_is_the_upper_bound_and_the_pad_is_why(self):
        # 20 mm of current path across a 10 mm wide sheet = 2 squares
        #   = 2 * 1.724e-8/35e-6 = 0.98514 mOhm -> 0.98514 mV at 1 A.
        # That is what "length times sheet resistance" gives, and it is an UPPER
        # bound: the pads are 1 mm wide, so the current enters and leaves over
        # 1 mm of the 10 mm edge and spreads - the effective length is shorter.
        two_sq_mv = 2 * sheet_ohm_per_sq() * 1e3
        r = oracle.board_ir_drop(make(FULL), "VCC", "U1.1", ["U2.1"],
                                 current_a=1.0, model_zones=True,
                                 zone_grid_mm=0.5, max_zone_nodes=20000)
        self.assertEqual(r["status"], "ok", r.get("message"))
        v = r["value"]
        self.assertLess(v["max_drop_mv"], two_sq_mv)
        self.assertAlmostEqual(v["max_drop_mv"], 0.935986, places=5)
        # and it is not a wild number: within 5% of the hand estimate
        self.assertGreater(v["max_drop_mv"], 0.95 * two_sq_mv)

    def test_the_one_nine_squares_reading(self):
        # contact columns at x = 9.5..10.5 and 29.5..30.5 mm are at 0.5 mm pitch
        # on a 0.5 mm grid, so the path between the outer contact columns is
        # 19 mm of a 10 mm wide sheet = 1.9 squares = 0.9359 mV.
        one_nine = 1.9 * sheet_ohm_per_sq() * 1e3
        r = oracle.board_ir_drop(make(FULL), "VCC", "U1.1", ["U2.1"],
                                 current_a=1.0, model_zones=True,
                                 zone_grid_mm=0.5, max_zone_nodes=20000)
        self.assertEqual(r["status"], "ok", r.get("message"))
        self.assertAlmostEqual(r["value"]["max_drop_mv"], one_nine, delta=0.01 * one_nine)

    def test_the_envelope_says_the_pour_is_in_it(self):
        r = oracle.board_ir_drop(make(FULL), "VCC", "U1.1", ["U2.1"],
                                 current_a=1.0, model_zones=True,
                                 zone_grid_mm=0.5, max_zone_nodes=20000)
        self.assertEqual(r["value"]["net"], "VCC")
        self.assertEqual(r["model"], "track_via_pad_zone_resistor_graph")
        self.assertIn("2-D sheet", r["basis"])
        self.assertIn("conjugate gradients", r["basis"])
        zs = r["zone_summary"]
        self.assertEqual([s["layer"] for s in zs["sheets"]], ["F.Cu"])
        self.assertEqual(zs["sheets"][0]["cells"], 1200)
        self.assertAlmostEqual(zs["sheets"][0]["sheet_ohm_per_sq"],
                               sheet_ohm_per_sq(), delta=1e-9)
        self.assertIsNotNone(r["solver"])
        self.assertEqual(r["solver"]["method"], "ic0_preconditioned_cg")
        self.assertTrue(r["solver"]["converged"])
        self.assertFalse(any("UPPER bound" in w for w in r["warnings"]), r["warnings"])
        self.assertNotIn("copper pour / plane in parallel", r["not_computed"])

    def test_a_finer_pitch_does_not_move_the_sheet_resistance(self):
        # the pitch changes how finely the current spreads near a contact, not
        # the sheet resistance - so 0.5 mm and 1.0 mm must agree to a percent
        coarse = oracle.board_ir_drop(make(FULL), "VCC", "U1.1", ["U2.1"],
                                      current_a=1.0, model_zones=True,
                                      zone_grid_mm=1.0, max_zone_nodes=20000)
        fine = oracle.board_ir_drop(make(FULL), "VCC", "U1.1", ["U2.1"],
                                    current_a=1.0, model_zones=True,
                                    zone_grid_mm=0.5, max_zone_nodes=20000)
        self.assertEqual(coarse["status"], "ok", coarse.get("message"))
        self.assertEqual(fine["status"], "ok", fine.get("message"))
        a = coarse["value"]["max_drop_mv"]
        b = fine["value"]["max_drop_mv"]
        self.assertLess(abs(a - b) / b, 0.02, (a, b))


class TestNoPourMeansNoChange(unittest.TestCase):
    """With no zones on the net, the CG path must land on the dense answer."""

    def _both(self, body, pads=PADS, **kw):
        b = make(body, pads)
        off = oracle.board_ir_drop(b, "VCC", "U1.1", ["U2.1"], current_a=1.0, **kw)
        on = oracle.board_ir_drop(b, "VCC", "U1.1", ["U2.1"], current_a=1.0,
                                  model_zones=True, **kw)
        return off, on

    def test_a_single_track_gives_the_same_answer_either_way(self):
        body = '  (segment (start 10 10) (end 30 10) (width 0.5) (layer "F.Cu") (net 1))'
        off, on = self._both(body)
        self.assertEqual(off["status"], "ok")
        self.assertEqual(on["status"], "ok", on.get("message"))
        self.assertAlmostEqual(off["value"]["max_drop_mv"], 19.702857, places=6)
        self.assertAlmostEqual(on["value"]["max_drop_mv"],
                               off["value"]["max_drop_mv"], places=6)
        self.assertEqual(on["value"]["edge_count"], off["value"]["edge_count"])
        self.assertEqual(on["value"]["node_count"], off["value"]["node_count"])
        # no pour was modelled, so the envelope must not claim one
        self.assertIsNone(on["zone_summary"])
        self.assertEqual(on["model"], "track_via_pad_resistor_graph")
        self.assertFalse(any("pour" in w for w in on["warnings"]), on["warnings"])

    def test_a_via_on_a_two_layer_board_is_not_split(self):
        # Only a via that crosses MORE than its two endpoint layers needs a node
        # per layer; splitting a plain through via would change a historical
        # number for no reason.
        body = ('  (segment (start 10 10) (end 20 10) (width 0.5) (layer "F.Cu") (net 1))\n'
                '  (via (at 20 10) (size 0.6) (drill 0.3) (layers "F.Cu" "B.Cu") (net 1))\n'
                '  (segment (start 20 10) (end 30 10) (width 0.5) (layer "B.Cu") (net 1))')
        off, on = self._both(body, PADS_XLAYER, plating_um=25.0)
        self.assertEqual(off["status"], "ok")
        self.assertEqual(on["status"], "ok", on.get("message"))
        self.assertAlmostEqual(on["value"]["max_drop_mv"], 20.979986, places=5)
        self.assertAlmostEqual(on["value"]["max_drop_mv"],
                               off["value"]["max_drop_mv"], places=6)


class TestRefusals(unittest.TestCase):
    def test_a_pour_that_does_not_reach_the_sink_is_not_a_small_drop(self):
        r = oracle.board_ir_drop(make(LEFT_ONLY), "VCC", "U1.1", ["U2.1"],
                                 current_a=1.0, model_zones=True,
                                 zone_grid_mm=0.5, max_zone_nodes=20000)
        self.assertEqual(r["status"], "error")
        self.assertEqual(r["reason_code"], "NOT_CONNECTED")
        self.assertIn("U2.1", r["message"])

    def test_a_pour_on_another_net_is_not_our_copper(self):
        r = oracle.board_ir_drop(make(zone(5, 5, 35, 15, net=2, name="VCC2")),
                                 "VCC", "U1.1", ["U2.1"], current_a=1.0,
                                 model_zones=True, zone_grid_mm=0.5)
        self.assertEqual(r["status"], "error")
        self.assertEqual(r["reason_code"], "NOT_APPLICABLE")

    def test_a_keepout_is_not_copper(self):
        # A keepout on the same net is the absence of copper, not a pour.
        z = zone(5, 5, 35, 15).replace(
            "    (min_thickness 0.25)\n",
            "    (min_thickness 0.25)\n"
            "    (keepout (tracks not_allowed) (vias not_allowed) "
            "(pads not_allowed) (copperpour not_allowed))\n", 1)
        r = oracle.board_ir_drop(make(z), "VCC", "U1.1", ["U2.1"], current_a=1.0,
                                 model_zones=True, zone_grid_mm=0.5)
        self.assertEqual(r["status"], "error")
        self.assertEqual(r["reason_code"], "NOT_APPLICABLE")

    def test_the_cell_budget_is_enforced_not_silently_coarsened(self):
        r = oracle.board_ir_drop(make(FULL), "VCC", "U1.1", ["U2.1"],
                                 current_a=1.0, model_zones=True,
                                 zone_grid_mm=0.5, max_zone_nodes=100)
        self.assertEqual(r["status"], "error")
        self.assertEqual(r["reason_code"], "TOO_LARGE")
        self.assertIn("pour grid", r["message"])

    def test_a_zero_pitch_is_refused_rather_than_clamped(self):
        r = oracle.board_ir_drop(make(FULL), "VCC", "U1.1", ["U2.1"],
                                 current_a=1.0, model_zones=True, zone_grid_mm=0)
        self.assertEqual(r["status"], "error")
        self.assertEqual(r["reason_code"], "ARG_RANGE")
        self.assertIn("zone_grid_mm", r["message"])

    def test_a_non_numeric_pitch_is_refused(self):
        r = oracle.board_ir_drop(make(FULL), "VCC", "U1.1", ["U2.1"],
                                 current_a=1.0, model_zones=True,
                                 zone_grid_mm="fine")
        self.assertEqual(r["status"], "error")
        self.assertEqual(r["reason_code"], "ARG_INVALID")

    def test_a_negative_link_is_refused(self):
        r = oracle.board_ir_drop(make(FULL), "VCC", "U1.1", ["U2.1"],
                                 current_a=1.0, model_zones=True,
                                 zone_link_mm=-0.1)
        self.assertEqual(r["status"], "error")
        self.assertEqual(r["reason_code"], "ARG_RANGE")


class TestSolverEquivalence(unittest.TestCase):
    """The CG path must agree with the dense path it replaces."""

    def test_dense_and_cg_agree_on_a_sheet(self):
        # a 7x7 sheet: 49 nodes, every cell one square, one corner source, one
        # corner sink. Dense nodal analysis vs CG: the answer must be the same.
        n = 7
        nodes = [(i, j) for i in range(n) for j in range(n)]
        edges = []
        for (i, j) in nodes:
            if i + 1 < n:
                edges.append(((i, j), (i + 1, j), 1.0))
            if j + 1 < n:
                edges.append(((i, j), (i, j + 1), 1.0))
        names = ["n%d_%d" % (i, j) for (i, j) in nodes]
        ids = {p: names[k] for k, p in enumerate(nodes)}
        ee = [{"a": ids[a], "b": ids[b], "resistance_ohm": r} for (a, b, r) in edges]
        inj = {ids[(0, 0)]: 1.0, ids[(n - 1, n - 1)]: -1.0}
        dense, why = PW.resistive_network(names, ee, inj, ids[(0, 0)])
        self.assertIsNotNone(dense, why)
        cg, why = PW.resistive_network_cg(names, ee, inj, ids[(0, 0)], tol=1e-12)
        self.assertIsNotNone(cg, why)
        self.assertTrue(cg["converged"])
        dv = max(abs(dense["voltages_v"][k] - cg["voltages_v"][k]) for k in names)
        self.assertLess(dv, 1e-9, dv)
        # sanity: the sheet's own number is in the right ballpark
        self.assertGreater(dense["voltages_v"][ids[(0, 0)]]
                           - dense["voltages_v"][ids[(n - 1, n - 1)]], 1.0)

    def test_dense_and_cg_agree_on_a_chain(self):
        names = ["a", "b", "c", "d"]
        ee = [{"a": "a", "b": "b", "resistance_ohm": 1.0},
              {"a": "b", "b": "c", "resistance_ohm": 1.5},
              {"a": "c", "b": "d", "resistance_ohm": 0.5}]
        inj = {"a": 1.0, "d": -1.0}
        dense, _ = PW.resistive_network(names, ee, inj, "a")
        cg, _ = PW.resistive_network_cg(names, ee, inj, "a", tol=1e-14)
        self.assertEqual(cg["precond"], "ic0")
        self.assertLessEqual(cg["iterations"], 4)
        for k in names:
            self.assertAlmostEqual(dense["voltages_v"][k], cg["voltages_v"][k],
                                   places=9)

    def test_parallel_edges_are_folded_not_double_counted(self):
        # two 1 ohm resistors between the same pair is 0.5 ohm, not 0.25; if the
        # adjacency double-counted them IC(0) would be built on a pattern that
        # does not match the matrix and the solve would crawl or stall.
        names = ["a", "b", "c"]
        ee = [{"a": "a", "b": "b", "resistance_ohm": 1.0},
              {"a": "a", "b": "b", "resistance_ohm": 1.0},
              {"a": "b", "b": "c", "resistance_ohm": 1.0}]
        inj = {"a": 1.0, "c": -1.0}
        cg, _ = PW.resistive_network_cg(names, ee, inj, "a", tol=1e-14)
        self.assertAlmostEqual(cg["voltages_v"]["a"] - cg["voltages_v"]["c"],
                               1.5, places=9)


class TestConstantsAgree(unittest.TestCase):
    def test_the_link_ohm_is_one_number(self):
        self.assertEqual(ZF.LINK_OHM, oracle._ZF_LINK_OHM)

    def test_the_grid_window_is_one_window(self):
        self.assertEqual(ZF.plan_layer.__defaults__[2], oracle.ZF_MIN_GRID_MM)
        self.assertEqual(ZF.plan_layer.__defaults__[3], oracle.ZF_MAX_GRID_MM)

    def test_the_sheet_model_name_is_stated(self):
        self.assertEqual(ZF.ZONE_SHEET_MODEL, "zone_sheet_grid")


class TestTheToolSurface(unittest.TestCase):
    def test_the_knobs_are_exposed_with_the_switch_off(self):
        from pcb_agent_mcp import tools
        d = tools.TOOLS["board_ir_drop"]
        sch = d.get("inputSchema") or d
        props = sch["properties"]
        for k in ("model_zones", "zone_grid_mm", "max_zone_nodes", "zone_link_mm"):
            self.assertIn(k, props)
        self.assertIs(props["model_zones"].get("default"), False)
        self.assertNotIn("model_zones", sch.get("required") or [])

    def test_the_tool_reaches_the_pour_path(self):
        from pcb_agent_mcp import tools
        r = tools.call_tool("board_ir_drop", {
            "path": write_board(FULL), "net": "VCC", "source": "U1.1",
            "sinks": ["U2.1"], "current_a": 1.0, "model_zones": True,
            "zone_grid_mm": 0.5, "max_zone_nodes": 20000})
        self.assertEqual(r["status"], "ok", r.get("message"))
        self.assertAlmostEqual(r["value"]["max_drop_mv"], 0.935986, places=5)
        self.assertEqual(r["model"], "track_via_pad_zone_resistor_graph")
        self.assertTrue(r["solver"]["converged"])

    def test_the_tool_leaves_the_default_alone(self):
        from pcb_agent_mcp import tools
        r = tools.call_tool("board_ir_drop", {
            "path": write_board(FULL), "net": "VCC", "source": "U1.1",
            "sinks": ["U2.1"], "current_a": 1.0})
        self.assertEqual(r["status"], "error")
        self.assertEqual(r["reason_code"], "NOT_APPLICABLE")


if __name__ == "__main__":
    unittest.main()
