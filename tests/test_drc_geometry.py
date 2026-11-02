"""The two facts a DRC violation was missing: how much it is short, and where.

`kicad-cli pcb drc --format json` publishes no structured geometry - the wanted
and the measured value exist only inside the localised `description` string.
The reference session got tired of that and wrote its own `measure_gap.py`;
that is a good outcome for one caller and a bad one for the product, so the
numbers are parsed here, every time, and the answer says how sure it is.

Two rules this file pins, both learned the hard way:

* a description with no millimetre pair gets `geometry: null` plus the reason -
  never a zero, never a guessed number;
* the `actual` marker decides which number is which. Without it the order is
  reported as "assumed", not silently presented as measured.

It also pins the reply envelope, because reading the wrong shape produced a
wrong answer out of a correct reply: `board_zones` said 0 where the file had 2,
which is the worst kind of wrong.
"""
import os
import shutil
import sys
import tempfile
import unittest

sys.path.insert(0, os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "src"))

from pcb_agent_mcp.fidelity import drc_geometry as G  # noqa: E402
from pcb_agent_mcp import tools as TOOLS  # noqa: E402
from pcb_agent_mcp import oracle  # noqa: E402
from pcb_agent_mcp import edit as E  # noqa: E402
from pcb_agent_mcp.edit import plan as P  # noqa: E402
from pcb_agent_mcp.adapters import kicad_pcb  # noqa: E402

# Verbatim from `kicad-cli 10.0.7 pcb drc --format json` on a real board.
ZONE_CLEARANCE = "间距违规 (填充区 间距 0.2500 mm; 实际 0.2005 mm)"
DRILL_TOO_CLOSE = "钻孔太靠近其他孔 (电路板配置约束 最小 0.2500 mm; 实际 0.0000 mm)"
SILK_HEIGHT = "文字高度超出范围 (最小高度 0.8000 mm; 实际 0.7000 mm)"
EN_CLEARANCE = "Clearance violation (Zone clearance 0.25mm; actual 0.2005mm)"

STACKUP = """(setup (stackup (layer "F.Cu" (type "copper") (thickness 0.035))
    (layer "dielectric 1" (type "core") (thickness 0.2) (epsilon_r 4.3))
    (layer "B.Cu" (type "copper") (thickness 0.035))))"""

class TestParsingTheDescription(unittest.TestCase):
    def test_the_measured_value_is_the_one_after_the_keyword(self):
        c, a, order, nums = G.parse(ZONE_CLEARANCE)
        self.assertAlmostEqual(c, 0.25, places=6)
        self.assertAlmostEqual(a, 0.2005, places=6)
        self.assertEqual(order, "from_keyword")
        self.assertEqual(nums, [0.25, 0.2005])

    def test_a_named_constraint_does_not_confuse_the_split(self):
        """'电路板配置约束 最小 0.2500 mm' - the words before the number are prose."""
        c, a, order, _ = G.parse(DRILL_TOO_CLOSE)
        self.assertAlmostEqual(c, 0.25, places=6)
        self.assertAlmostEqual(a, 0.0, places=6)
        self.assertEqual(order, "from_keyword")

    def test_a_third_number_would_not_be_mistaken_for_the_actual(self):
        c, a, order, nums = G.parse(SILK_HEIGHT)
        self.assertEqual((c, a, order), (0.8, 0.7, "from_keyword"))
        self.assertEqual(len(nums), 2)

    def test_the_english_template_parses_the_same_way(self):
        c, a, order, _ = G.parse(EN_CLEARANCE)
        self.assertAlmostEqual(c, 0.25, places=6)
        self.assertAlmostEqual(a, 0.2005, places=6)
        self.assertEqual(order, "from_keyword")

    def test_without_the_keyword_the_order_is_a_stated_assumption(self):
        c, a, order, _ = G.parse("Silkscreen overlap 0.15 mm 0.10 mm")
        self.assertEqual((c, a, order), (0.15, 0.1, "assumed"))

    def test_a_single_number_is_not_made_into_a_pair(self):
        """One value is one value: guessing a second would invent a number."""
        c, a, order, nums = G.parse("Track width 0.1500 mm")
        self.assertEqual((c, a, order), (None, None, None))
        self.assertEqual(nums, [0.15])

    def test_a_description_without_millimetres_yields_nothing(self):
        c, a, order, nums = G.parse("Courtyard overlap (footprints X1, X2)")
        self.assertEqual((c, a, order, nums), (None, None, None, []))
        self.assertEqual(G.parse(None)[:3], (None, None, None))

class TestAnnotatingViolations(unittest.TestCase):
    def test_a_violation_that_has_numbers_gets_a_geometry_object(self):
        v = {"type": "clearance", "severity": "error", "description": ZONE_CLEARANCE}
        G.annotate([v])
        g = v["geometry"]
        self.assertAlmostEqual(g["constraint_mm"], 0.25, places=6)
        self.assertAlmostEqual(g["actual_mm"], 0.2005, places=6)
        self.assertAlmostEqual(g["shortfall_mm"], 0.0495, places=6)
        self.assertEqual(g["order"], "from_keyword")
        self.assertNotIn("caveat", g, "a keyword-ordered pair needs no caveat")
        self.assertIn("description", g["source"])

    def test_a_violation_without_numbers_says_why_instead_of_zero(self):
        v = {"type": "courtyard_overlap", "severity": "error",
             "description": "Courtyard overlap (footprints X1, X2)"}
        G.annotate([v])
        self.assertIsNone(v["geometry"])
        self.assertIn("no pair of millimetre values", v["geometry_absent_because"])
        self.assertEqual(v["numbers_mm"], [])
        self.assertNotIn("constraint_mm", v)

    def test_an_assumed_order_is_labelled(self):
        v = {"description": "Silkscreen overlap 0.15 mm 0.10 mm"}
        G.annotate([v])
        self.assertEqual(v["geometry"]["order"], "assumed")
        self.assertIn("actual", v["geometry"]["caveat"])

    def test_the_annotation_is_additive(self):
        v = {"type": "clearance", "severity": "error", "description": ZONE_CLEARANCE,
             "items": [{"description": "Pad 1", "pos": {"x": 1, "y": 2}}]}
        before = dict(v)
        out = G.annotate([v])
        self.assertIs(out[0], v)
        for key, val in before.items():
            self.assertEqual(v[key], val, "%s must not be rewritten" % key)

    def test_the_headline_is_sorted_by_how_short_it_is(self):
        vs = [{"type": "a", "description": ZONE_CLEARANCE},
              {"type": "b", "description": DRILL_TOO_CLOSE},
              {"type": "c", "description": "Courtyard overlap (X1, X2)"}]
        G.annotate(vs)
        rows = G.summarise(vs)
        self.assertEqual([r["type"] for r in rows], ["b", "a"],
                         "0.25 of shortfall outranks 0.0495; a violation with no "
                         "geometry cannot be ranked and is left out")
        self.assertEqual(rows[0]["shortfall_mm"], 0.25)

class TestTheReplySaysWhereThePayloadIs(unittest.TestCase):
    def test_a_calc_style_reply_points_at_value(self):
        out = TOOLS._uniform_envelope({"status": "ok", "unit": "mm",
                                       "value": {"zones": 2}})
        self.assertEqual(out["payload_is"], "value")
        self.assertIn("`value`", out["where_to_read"])

    def test_a_write_style_reply_points_at_the_top_level(self):
        out = TOOLS._uniform_envelope({"status": "ok", "plan_id": "abc",
                                       "gates": {}, "operations": []})
        self.assertEqual(out["payload_is"], "top_level")
        self.assertIn("plan_id", out["where_to_read"])
        self.assertNotIn("value", out)

    def test_a_reply_that_already_said_it_is_not_overwritten(self):
        out = TOOLS._uniform_envelope({"status": "ok", "value": {"zones": 2},
                                       "payload_is": "value"})
        self.assertEqual(out["payload_is"], "value")

    def test_a_non_dict_is_passed_through(self):
        self.assertEqual(TOOLS._uniform_envelope(["x"]), ["x"])

class TestThroughCallTool(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.mkdtemp(prefix="pcbgeo-")
        self._home = os.environ.get("PCB_AGENT_HOME")
        os.environ["PCB_AGENT_HOME"] = os.path.join(self.tmp, "home")
        self.board = os.path.join(self.tmp, "g.kicad_pcb")
        E.create_board(self.board, {"width_mm": 40, "height_mm": 30,
                                    "copper_layers": 2, "engine": False})

    def tearDown(self):
        if self._home is None:
            os.environ.pop("PCB_AGENT_HOME", None)
        else:
            os.environ["PCB_AGENT_HOME"] = self._home
        P.PLANS.clear()
        shutil.rmtree(self.tmp, ignore_errors=True)

    def test_board_zones_says_the_answer_is_under_value(self):
        out = TOOLS.call_tool("board_zones", {"path": self.board})
        self.assertEqual(out["status"], "ok")
        self.assertEqual(out["payload_is"], "value")

    def test_board_propose_says_the_answer_is_at_the_top_level(self):
        out = TOOLS.call_tool("board_propose", {
            "path": self.board,
            "operations": [{"op": "add_net", "name": "GND"}]})
        self.assertEqual(out["status"], "ok", out)
        self.assertEqual(out["payload_is"], "top_level")
        self.assertIn("plan_id", out)

class TestPourConnectivityIsNotAVerdict(unittest.TestCase):
    def test_the_value_carries_the_authoritative_false_flag(self):
        text = """(kicad_pcb (version 20221018) %s
          (net 0 "") (net 1 "GND")
          (gr_rect (start 0 0) (end 40 40) (layer "Edge.Cuts") (width 0.1) (fill none))
          (footprint "T:P" (layer "F.Cu") (at 10 10)
            (property "Reference" "R1" (at 0 0 0) (layer "F.SilkS"))
            (pad "1" smd rect (at 0 0) (size 1 1) (layers "F.Cu") (net 1 "GND"))
          )
          (zone (net 1) (net_name "GND") (layer "B.Cu") (layername "B.Cu")
            (hatch edge 0.5) (connect_pads (clearance 0.2)) (min_thickness 0.25)
            (fill yes (mode solid))
            (polygon (pts (xy 0 0) (xy 40 0) (xy 40 40) (xy 0 40)))
            (filled_polygon (layer "B.Cu") (pts (xy 0 0) (xy 40 0) (xy 40 40) (xy 0 40)))
          )
        )""" % STACKUP
        board = kicad_pcb.parse_board_text(text)
        r = oracle.pour_connectivity(board)
        self.assertEqual(r["status"], "ok")
        self.assertIs(r["value"]["authoritative"], False)
        self.assertIn("board_drc", r["value"]["use_instead"])
        self.assertIn("NOT a connectivity verdict", r["model"])
        self.assertIs(r["assumptions"]["authoritative"], False)
        self.assertIn("board_drc", r["assumptions"]["verdict_available_from"])
        self.assertIn("spokes", r["assumptions"]["why_not_authoritative"])
        self.assertIn("does not mean the pad is unconnected",
                      r["value"]["this_is_not_a_connectivity_verdict"])

if __name__ == "__main__":
    unittest.main()
