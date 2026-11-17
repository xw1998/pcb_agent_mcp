"""Tests for route.rail_plan - the four questions behind one power rail.

Each case is a tiny real `.kicad_pcb` text, and each one exists to pin down a
verdict that used to be a guess:

  * a rail whose traces are too narrow but could legally be grown  -> widen_traces
  * the same rail boxed in by a foreign track                      -> pour_needed
  * a filled plane whose `min_thickness` is below the demand       -> UNMEASURED,
    not "too thin": the file does not say the copper is thin, it says nothing
  * the same plane with `min_thickness` above the demand           -> covered
  * a plane that reaches no pad and no via of its own net          -> flagged
  * a pour polygon with a foreign track endpoint inside it         -> stale

The inverse cases are the point of the file: every verdict here has a control
built from the same fixture with one number changed, so a bug that always
returned "pour_needed" would fail half of them.
"""
import os
import sys
import unittest

sys.path.insert(0, os.path.join(os.path.dirname(os.path.dirname(
    os.path.abspath(__file__))), "src"))

from pcb_agent_mcp.adapters import kicad_pcb  # noqa: E402
from pcb_agent_mcp.route import rail_plan  # noqa: E402

# 1 A on 1 oz copper, 10 C rise, IPC-2221, measured (not guessed):
#   external 0.3021 mm, internal 0.7859 mm
REQ_EXT_1A = 0.3021
REQ_INT_1A = 0.7859

STACKUP = """(setup (stackup
    (layer "F.Cu" (type "copper") (thickness 0.035))
    (layer "dielectric 1" (type "core") (thickness 0.2) (epsilon_r 4.3))
    (layer "In1.Cu" (type "copper") (thickness 0.035))
    (layer "dielectric 2" (type "core") (thickness 0.2) (epsilon_r 4.3))
    (layer "B.Cu" (type "copper") (thickness 0.035))))"""

HEAD = """(kicad_pcb (version 20221018) (generator pcbnew)
  (general (thickness 1.6))
  (net 0 "") (net 1 "+3V3") (net 2 "/GND")
  %s
  (gr_rect (start 0 0) (end 60 40) (layer "Edge.Cuts") (width 0.1)
    (fill none))
""" % STACKUP

PAD_3V3 = """  (footprint "T:P" (layer "F.Cu") (at 20 20)
    (fp_text reference "U1" (at 0 -2) (layer "F.SilkS"))
    (pad "1" thru_hole circle (at 0 0) (size 1 1) (drill 0.5)
      (layers "*.Cu" "*.Mask") (net 1 "+3V3")))
"""

def board(body):
    return kicad_pcb.parse_board_text(HEAD + PAD_3V3 + body + "\n)\n")

def plan(body, currents=None, **kw):
    # clearance passed explicitly so the fixture does not depend on whether the
    # text parse found a `.kicad_pro`; one test below checks that default.
    kw.setdefault("clearance_mm", 0.1)
    kw.setdefault("edge_clearance_mm", 0.3)
    return rail_plan.plan(board(body), currents if currents is not None else
                          {"+3V3": 1.0}, **kw)

def row(out, net="+3V3"):
    return [r for r in out["rows"] if r["net"].lstrip("/") == net.lstrip("/")][0]

class TestDemandIsRequired(unittest.TestCase):
    def test_no_currents_and_no_nets_raises(self):
        with self.assertRaises(rail_plan.PlanError):
            rail_plan.plan(board(""), {})

    def test_a_named_net_without_a_current_is_inspected_not_judged(self):
        body = '  (segment (start 10 20) (end 30 20) (width 0.5) (layer "F.Cu") (net 1))\n'
        out = rail_plan.plan(board(body), {}, nets=["+3V3"])
        r = row(out)
        self.assertEqual(r["verdict"], "no_demand")
        self.assertEqual(r["demand_a"], None)
        self.assertEqual(r["required_width_mm"]["external"], None)
        self.assertEqual(r["trace"]["widest_mm"], 0.5)
        self.assertEqual(r["trace"]["below_demand"], 0)

    def test_bad_current_raises(self):
        for bad in ({"+3V3": 0}, {"+3V3": -1}, {"+3V3": "lots"}):
            with self.assertRaises(rail_plan.PlanError):
                rail_plan.plan(board(""), bad)

    def test_unknown_net_raises(self):
        with self.assertRaises(rail_plan.PlanError):
            rail_plan.plan(board(""), {"+9V9": 1.0})

    def test_sheet_path_spelling_is_accepted(self):
        body = '  (segment (start 10 20) (end 30 20) (width 0.5) (layer "F.Cu") (net 1))\n'
        out = rail_plan.plan(board(body), {"/+3V3": 1.0})
        r = row(out)
        self.assertEqual(r["net"], "+3V3")
        self.assertEqual(r["net_asked"], "/+3V3")
        self.assertEqual(r["verdict"], "traces_ok")

class TestTraces(unittest.TestCase):
    def test_wide_trace_is_ok(self):
        body = '  (segment (start 10 20) (end 30 20) (width 0.5) (layer "F.Cu") (net 1))\n'
        r = row(plan(body))
        self.assertEqual(r["verdict"], "traces_ok")

    def test_narrow_trace_with_room_can_be_widened(self):
        # A foreign track 0.9 mm away keeps the room measurement inside the
        # window, so `widest_legal_mm` is a number instead of "at least window".
        body = ('  (segment (start 10 20) (end 30 20) (width 0.1) (layer "F.Cu") (net 1))\n'
                '  (segment (start 10 20.9) (end 30 20.9) (width 0.1) '
                '(layer "F.Cu") (net 2))\n')
        r = row(plan(body))
        self.assertEqual(r["verdict"], "widen_traces")
        self.assertIsNotNone(r["trace"]["widest_legal_mm"])
        self.assertGreaterEqual(r["trace"]["widest_legal_mm"], REQ_EXT_1A)
        self.assertTrue([a for a in r["actions"]
                         if a["op"] == "set_track_width"
                         and a["layer_class"] == "external"])

    def test_the_same_trace_boxed_in_needs_a_pour(self):
        """Control for the case above: one foreign track moves the verdict."""
        body = ('  (segment (start 10 20) (end 30 20) (width 0.1) (layer "F.Cu") (net 1))\n'
                '  (segment (start 10 20.2) (end 30 20.2) (width 0.1) '
                '(layer "F.Cu") (net 2))\n')
        r = row(plan(body))
        self.assertEqual(r["verdict"], "pour_needed")
        zones = [a for a in r["actions"] if a["op"] == "add_zone"]
        self.assertTrue(zones, r["actions"])
        for a in zones:
            self.assertTrue(a["needs_outline"])
            self.assertIn(a["layer"], ("In1.Cu", "B.Cu", "F.Cu"))

    def test_lowering_the_current_flips_widen_to_ok(self):
        """反向失败对照: same copper, less current - the verdict must reverse."""
        body = '  (segment (start 10 20) (end 30 20) (width 0.1) (layer "F.Cu") (net 1))\n'
        self.assertEqual(row(plan(body, {"+3V3": 1.0}))["verdict"], "widen_traces")
        self.assertEqual(row(plan(body, {"+3V3": 0.05}))["verdict"], "traces_ok")

    def test_internal_layer_uses_the_internal_demand(self):
        body = '  (segment (start 10 20) (end 30 20) (width 0.5) (layer "In1.Cu") (net 1))\n'
        r = row(plan(body))
        # 0.5 mm is fine on an outer layer for 1 A and short on an inner one.
        self.assertEqual(r["required_width_mm"]["internal"], round(REQ_INT_1A, 4))
        self.assertEqual(r["verdict"], "widen_traces")

class TestCoverage(unittest.TestCase):
    def test_a_declared_net_with_nothing_on_it_is_no_copper(self):
        """/GND is in the net table but has no pad, track, via or plane."""
        r = row(plan("", {"/GND": 1.0}), "/GND")
        self.assertEqual(r["verdict"], "no_copper")
        self.assertEqual(r["carrier"], "none")

    def test_control_one_pad_makes_it_uncovered_instead(self):
        r = row(plan("", {"\u002b3V3": 1.0}))
        self.assertEqual(r["verdict"], "pads_uncovered")

    def test_pad_without_any_copper_is_named(self):
        r = row(plan('  (segment (start 40 20) (end 50 20) (width 0.5) '
                     '(layer "F.Cu") (net 2))\n'))
        self.assertEqual(r["verdict"], "pads_uncovered")
        self.assertIn("U1.1", r["pads"]["orphan"])

    def test_adding_its_own_trace_removes_the_orphan(self):
        """Control: same pad, same foreign copper, one same-net trace added."""
        body = ('  (segment (start 40 20) (end 50 20) (width 0.5) (layer "F.Cu") (net 2))\n'
                '  (segment (start 20 20) (end 30 20) (width 0.5) (layer "F.Cu") (net 1))\n')
        r = row(plan(body))
        self.assertEqual(r["pads"]["orphan"], [])
        self.assertEqual(r["pads"]["on_copper"], 1)

class TestPlanes(unittest.TestCase):
    def _plane(self, net=1, name="+3V3", min_thickness="0.1", pts=None, layer="In1.Cu"):
        pts = pts or "(xy 10 10) (xy 30 10) (xy 30 30) (xy 10 30)"
        return ('  (zone (net %d) (net_name "%s") (layer "%s") (name "rail")\n'
                '    (hatch edge 0.5) (priority 0) (min_thickness %s)\n'
                '    (connect_pads (clearance 0.2))\n'
                '    (filled_polygon (layer "%s") (pts %s)))\n'
                % (net, name, layer, min_thickness, layer, pts))

    def test_plane_covers_but_cannot_prove_capacity(self):
        r = row(plan(self._plane(min_thickness="0.1")))
        self.assertEqual(r["verdict"], "plane_covered_capacity_unmeasured")
        self.assertIn("plane_capacity_unmeasured", r["flags"])
        per = r["plane"]["per_layer"]["In1.Cu"]
        # the pad is INSIDE the polygon, so coverage is real ...
        self.assertEqual(per["pads_touching"], 1)
        self.assertEqual(per["pads_inside"], 1)
        # ... and the file still proves nothing about the corridor width.
        self.assertFalse(per["proves_enough"])
        self.assertEqual(per["min_thickness_mm"], 0.1)
        self.assertEqual(per["not_measured"], ["current_corridor_width"])
        self.assertTrue([a for a in r["actions"] if a["op"] == "prove_plane_capacity"])

    def test_a_thick_plane_is_a_proof(self):
        """Control: same polygon, min_thickness above the demand -> covered."""
        r = row(plan(self._plane(min_thickness="2.5")))
        self.assertEqual(r["verdict"], "covered")
        self.assertTrue(r["plane"]["per_layer"]["In1.Cu"]["proves_enough"])

    def test_min_thickness_is_read_as_the_value_written(self):
        """The bug this pins down: `min(0.0, x)` reported 0.0 for every plane."""
        r = row(plan(self._plane(min_thickness="0.35")))
        self.assertEqual(r["plane"]["per_layer"]["In1.Cu"]["min_thickness_mm"], 0.35)

    def test_plane_that_reaches_nothing_is_flagged(self):
        """A fill whose polygon misses every pad AND every via of its own net."""
        body = ('  (zone (net 1) (net_name "+3V3") (layer "In1.Cu") (name "island")\n'
                '    (hatch edge 0.5) (priority 0) (min_thickness 0.1)\n'
                '    (filled_polygon (layer "In1.Cu") (pts (xy 40 5) (xy 50 5)\n'
                '      (xy 50 15) (xy 40 15)))\n)'
                '  (segment (start 20 20) (end 30 20) (width 0.5) (layer "F.Cu") (net 1))\n')
        r = row(plan(body))
        self.assertIn("plane_reaches_nothing", r["flags"])
        self.assertTrue(r["plane"]["per_layer"]["In1.Cu"]["reaches_nothing"])

    def test_plane_connected_only_by_a_via_is_not_isolated(self):
        """Control for the flag: no pad inside, but a via of the net is inside."""
        body = (self._plane().replace('(pts (xy 10 10) (xy 30 10) (xy 30 30) (xy 10 30))',
                                      '(pts (xy 10 10) (xy 50 10) (xy 50 30) (xy 10 30))')
                .replace('(zone (net 1) (net_name "+3V3")',
                         '(zone (net 1) (net_name "+3V3")')
                + '  (via (at 45 20) (size 0.6) (drill 0.3) (layers "F.Cu" "B.Cu") (net 1))\n')
        r = row(plan(body))
        self.assertNotIn("plane_reaches_nothing", r["flags"])
        self.assertGreaterEqual(r["plane"]["per_layer"]["In1.Cu"]["vias_inside"], 1)

    def test_foreign_track_inside_the_fill_is_a_stale_pour(self):
        body = (self._plane() +
                '  (segment (start 5 20) (end 20 20) (width 0.2) (layer "In1.Cu") (net 2))\n')
        r = row(plan(body))
        self.assertEqual(r["verdict"], "plane_stale")
        self.assertIn("foreign_track_inside_plane", r["flags"])
        self.assertTrue([a for a in r["actions"] if a["op"] == "fill_zones"])

    def test_priority_ties_are_reported(self):
        body = (self._plane() +
                self._plane(net=2, name="/GND", min_thickness="0.1",
                            pts="(xy 5 5) (xy 9 5) (xy 9 9) (xy 5 9)"))
        out = plan(body)
        in1 = [l for l in out["layers"] if l["layer"] == "In1.Cu"][0]
        self.assertTrue(in1["priority_ties"])
        self.assertEqual(in1["priority_ties"][0]["priority"], 0)
        self.assertEqual(len(in1["priority_ties"][0]["nets"]), 2)

class TestDefaultsAndShape(unittest.TestCase):
    def test_clearance_default_is_reported_as_such(self):
        out = rail_plan.plan(board(
            '  (segment (start 10 20) (end 30 20) (width 0.5) (layer "F.Cu") (net 1))\n'),
            {"+3V3": 1.0})
        self.assertEqual(out["assumptions"]["clearance_source"], "default")
        self.assertAlmostEqual(out["assumptions"]["clearance_mm"], 0.2)

    def test_rows_are_sorted_and_summary_counts_match(self):
        body = ('  (segment (start 10 20) (end 30 20) (width 0.1) (layer "F.Cu") (net 1))\n'
                '  (segment (start 10 30) (end 30 30) (width 0.1) (layer "F.Cu") (net 2))\n')
        out = plan(body, {"+3V3": 1.0, "/GND": 0.2})
        self.assertEqual([r["net"] for r in out["rows"]], ["+3V3", "/GND"])
        self.assertEqual(sum(out["summary"].values()), len(out["rows"]))
        self.assertEqual(out["summary"].get("pour_needed", 0)
                         + out["summary"].get("widen_traces", 0)
                         + out["summary"].get("traces_ok", 0)
                         + out["summary"].get("no_copper", 0), 2)

    def test_pour_candidates_prefer_inner_layers(self):
        body = ('  (segment (start 10 20) (end 30 20) (width 0.1) (layer "F.Cu") (net 1))\n'
                '  (segment (start 10 20.2) (end 30 20.2) (width 0.1) '
                '(layer "F.Cu") (net 2))\n')
        r = row(plan(body))
        zones = [a for a in r["actions"] if a["op"] == "add_zone"]
        self.assertEqual(zones[0]["layer"], "In1.Cu")

    def test_pour_action_names_the_tool_that_makes_the_outline(self):
        body = ('  (segment (start 10 20) (end 30 20) (width 0.1) (layer "F.Cu") (net 1))\n'
                '  (segment (start 10 20.2) (end 30 20.2) (width 0.1) '
                '(layer "F.Cu") (net 2))\n')
        r = row(plan(body))
        zones = [a for a in r["actions"] if a["op"] == "add_zone"]
        self.assertTrue(zones)
        for a in zones:
            self.assertTrue(a["needs_outline"])
            self.assertEqual(a["next_tool"], "board_add_zone")

    def test_every_verdict_is_one_of_the_declared_ones(self):
        body = '  (segment (start 10 20) (end 30 20) (width 0.1) (layer "F.Cu") (net 1))\n'
        out = plan(body)
        for r in out["rows"]:
            self.assertIn(r["verdict"], rail_plan.VERDICTS)
        self.assertNotIn("plane_unproven", rail_plan.VERDICTS)

if __name__ == "__main__":
    unittest.main()
