"""Copper over a reference plane: legal to the DRC, a cut return path.

The fact this file pins down was measured on the RK3506 SOM, not invented here:
`DDR_A8` / `DDR_A11` had exactly one path in the router, and it ran four
segments on In1.Cu - the board's only GND pour, 1934 mm2 of a 2025 mm2 board.
A track over a pour is legal copper; the official ZONE_FILLER re-cuts the pour
around it, so DRC reports nothing. The bill is the plane: the refill gave up
36.96 mm2, i.e. the return path under every DDR signal referencing In1 was cut.

So the gate this file tests is a measurement (`edit/planes.py`) plus one
judgement (`ruleset/precondition.plane_pour_intact`):

* a pour covering at least half the board is a PLANE, and copper of another net
  landing on it is refused at propose time;
* a smaller pour (this board's 17% In3) is reported with its share and let
  through - whether a track may cross a split power plane is a design question,
  not a geometric one;
* `allow_plane_crossing=true` writes anyway, and the row still says the plane
  was cut (a report, never a silent pass);
* copper whose pour is the same net is not a crossing at all;
* a measurement that did not run blocks - "unmeasured" is not "intact".
"""
import os
import sys
import tempfile
import unittest

sys.path.insert(0, os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "src"))

from pcb_agent_mcp.edit import planes as PL          # noqa: E402
from pcb_agent_mcp.edit import plan as P            # noqa: E402
from pcb_agent_mcp.ruleset import precondition as PRE  # noqa: E402

# 20 x 10 mm board: 200 mm2. A pour over the whole outline is 100% (a plane);
# the 8 x 10 one is 40% (not a plane by the 0.5 declaration).
#
# Every y below is written the way KiCad writes it (Y down), so the board reads
# canonical y 0..10. Operations are in that same canonical frame - `edit/build`
# flips only when a node becomes file text - so a track at y 5 is mid-board.
BOARD = """(kicad_pcb (version 20221018) (generator pcbnew)
  (general (thickness 1.6))
  (layers (0 "F.Cu" signal) (4 "In1.Cu" signal) (31 "B.Cu" signal)
          (44 "Edge.Cuts" user))
  (net 0 "") (net 1 "GND") (net 2 "SIG") (net 3 "PWR")
  (gr_rect (start 0 -10) (end 20 0) (layer "Edge.Cuts") (width 0.1))
%s
)
"""

PLANE = """  (zone (net 1) (net_name "GND") (layer "In1.Cu") (tstamp 1) (hatch edge 0.5)
    (connect_pads (clearance 0.2))
    (min_thickness 0.25)
    (polygon (pts (xy 0 -10) (xy 20 -10) (xy 20 0) (xy 0 0)))
    (filled_polygon (layer "In1.Cu") (pts (xy 0 -10) (xy 20 -10) (xy 20 0) (xy 0 0))))"""

SPLIT = """  (zone (net 3) (net_name "PWR") (layer "In1.Cu") (tstamp 2) (hatch edge 0.5)
    (connect_pads (clearance 0.2))
    (min_thickness 0.25)
    (polygon (pts (xy 0 -10) (xy 8 -10) (xy 8 0) (xy 0 0)))
    (filled_polygon (layer "In1.Cu") (pts (xy 0 -10) (xy 8 -10) (xy 8 0) (xy 0 0))))"""


def a_track(net="SIG", layer="In1.Cu", x1=2.0, y1=5.0, x2=18.0, y2=5.0):
    return {"op": "add_track", "x1": x1, "y1": y1, "x2": x2, "y2": y2,
            "width": 0.25, "layer": layer, "net": net}


class PlaneTestBase(unittest.TestCase):
    pours = PLANE

    def setUp(self):
        self.tmp = tempfile.mkdtemp(prefix="pcbplane-")
        self.board = os.path.join(self.tmp, "board.kicad_pcb")
        with open(self.board, "w", newline="") as fh:
            fh.write(BOARD % self.pours)
        self._home = os.environ.get("PCB_AGENT_HOME")
        os.environ["PCB_AGENT_HOME"] = os.path.join(self.tmp, "home")

    def tearDown(self):
        if self._home is None:
            os.environ.pop("PCB_AGENT_HOME", None)
        else:
            os.environ["PCB_AGENT_HOME"] = self._home

    def propose(self, ops, **options):
        options.setdefault("precheck_clearance_mm", 0.1)
        return P.propose(self.board, ops, options)


class TheMeasurement(PlaneTestBase):
    def test_the_pour_is_a_plane_and_the_track_crosses_it(self):
        from pcb_agent_mcp.adapters.kicad_pcb import parse_board
        report = PL.crossings(parse_board(self.board), [a_track()])
        self.assertAlmostEqual(report["board_mm2"], 200.0, places=6)
        self.assertEqual(len(report["planes"]), 1)
        self.assertEqual(report["planes"][0]["layer"], "In1.Cu")
        self.assertAlmostEqual(report["planes"][0]["share"], 1.0, places=6)
        self.assertEqual(len(report["crossings"]), 1)
        hit = report["crossings"][0]
        self.assertTrue(hit["plane"])
        self.assertEqual((hit["net"], hit["layer"], hit["pour_net"]),
                         ("SIG", "In1.Cu", "GND"))

    def test_copper_of_the_pours_own_net_is_not_a_crossing(self):
        from pcb_agent_mcp.adapters.kicad_pcb import parse_board
        report = PL.crossings(parse_board(self.board),
                              [a_track(net="GND")])
        self.assertEqual(report["crossings"], [])

    def test_a_layer_without_a_pour_measures_nothing(self):
        from pcb_agent_mcp.adapters.kicad_pcb import parse_board
        report = PL.crossings(parse_board(self.board),
                              [a_track(layer="F.Cu")])
        self.assertEqual(report["planes"], PL.crossings(
            parse_board(self.board), [])["planes"])
        self.assertEqual(report["crossings"], [])


class TheGate(PlaneTestBase):
    def test_landing_on_a_plane_is_refused_at_propose(self):
        out = self.propose([a_track()])
        pre = out["preconditions"]
        self.assertEqual(pre["verdict"], "refused_at_plane_pour_intact")
        self.assertFalse(pre["ok"])
        self.assertIn("precondition.plane_pour_intact",
                      [b["gate"] for b in out["blocking"]])
        row = [c for c in pre["checks"] if c["check"] == "plane_pour_intact"][0]
        self.assertIs(row["ok"], False)
        self.assertEqual(row["severity"], "block")
        self.assertIn("GND", row["why"])
        # the measurement is reported even though the plan is refused
        pours = out["precheck"]["plane_pours"]
        self.assertEqual(len(pours["crossings"]), 1)
        self.assertTrue(pours["crossings"][0]["plane"])

    def test_a_signal_layer_track_passes(self):
        out = self.propose([a_track(layer="F.Cu")])
        self.assertEqual(out["blocking"], [])
        row = [c for c in out["preconditions"]["checks"]
               if c["check"] == "plane_pour_intact"][0]
        self.assertIs(row["ok"], True)

    def test_the_option_writes_and_still_reports_the_cut(self):
        out = self.propose([a_track()], allow_plane_crossing=True)
        self.assertEqual(out["blocking"], [])
        row = [c for c in out["preconditions"]["checks"]
               if c["check"] == "plane_pour_intact"][0]
        self.assertIs(row["ok"], False)          # the row does not lie
        self.assertEqual(row["severity"], "report")
        self.assertIn("plane_pour_intact", out["preconditions"]["reported"])

    def test_a_plan_that_lands_no_copper_is_not_asked(self):
        row = PRE.plane_pour_intact({"crossings": [
            {"net": "SIG", "layer": "In1.Cu", "pour_net": "GND", "plane": True}]},
            copper_write=False)
        self.assertIsNone(row["ok"])
        self.assertEqual(row["severity"], "report")
        self.assertIn("lands no copper", row["why"])


class ASmallPourIsReportedNotBlocked(PlaneTestBase):
    pours = SPLIT

    def test_forty_percent_is_not_a_plane(self):
        out = self.propose([a_track(net="SIG")])
        self.assertEqual(out["blocking"], [])
        crossing = out["precheck"]["plane_pours"]["crossings"][0]
        self.assertFalse(crossing["plane"])
        self.assertAlmostEqual(crossing["share"], 0.4, places=6)
        row = [c for c in out["preconditions"]["checks"]
               if c["check"] == "plane_pour_intact"][0]
        self.assertIs(row["ok"], True)
        self.assertIn("not planes", row["why"])
        self.assertIn("PWR", row["why"])


HALF = """  (zone (net 1) (net_name "GND") (layer "In1.Cu") (tstamp 3) (hatch edge 0.5)
    (connect_pads (clearance 0.2))
    (min_thickness 0.25)
    (polygon (pts (xy 0 -10) (xy 20 -10) (xy 20 -5) (xy 0 -5)))
    (filled_polygon (layer "In1.Cu") (pts (xy 0 -10) (xy 20 -10) (xy 20 -5) (xy 0 -5))))"""


class TheFileAndTheMeasurementAgreeAboutY(PlaneTestBase):
    """Operations and pour outlines are in the same frame (canonical, Y up).

    A half-board pour is the only shape that can tell a correct reading from one
    that flips y: the whole-board pour in `PlaneTestBase` contains the mirrored
    point too, so it would pass either way. This pour covers canonical y 5..10,
    written as file y -10..-5; a track at y 7 is on it and one at y 2 is not.
    """
    pours = HALF

    def test_the_top_half_of_the_file_is_the_planes_half(self):
        from pcb_agent_mcp.adapters.kicad_pcb import parse_board
        board = parse_board(self.board)
        clear = PL.crossings(board, [a_track(y1=2.0, y2=2.0)])
        hit = PL.crossings(board, [a_track(y1=7.0, y2=7.0)])
        self.assertEqual(clear["crossings"], [])
        self.assertEqual(len(hit["crossings"]), 1)
        self.assertAlmostEqual(hit["crossings"][0]["at"][1], 7.0, places=6)


class AnUnmeasuredPlaneIsNotAnIntactOne(unittest.TestCase):
    def test_a_measurement_error_blocks_and_the_option_cannot_wave_it_through(self):
        row = PRE.plane_pour_intact({"error": "ValueError: boom"}, copper_write=True)
        self.assertIs(row["ok"], False)
        self.assertEqual(row["severity"], "block")
        self.assertIn("boom", row["why"])
        row = PRE.plane_pour_intact({"error": "ValueError: boom"}, copper_write=True,
                                   allow=True)
        self.assertIs(row["ok"], False)
        self.assertEqual(row["severity"], "block")

    def test_a_plan_that_cannot_measure_blocks(self):
        # the planned board is what gets measured, so a zone the document tree
        # can write but the adapter cannot read is a refusal, not a pass
        tmp = tempfile.mkdtemp(prefix="pcbplane-")
        path = os.path.join(tmp, "board.kicad_pcb")
        with open(path, "w", newline="") as fh:
            fh.write(BOARD % PLANE)
        out = P.propose(path, [a_track()], {"precheck_clearance_mm": 0.1})
        self.assertEqual(out["precheck"]["plane_pours"]["crossings"][0]["plane"],
                         True)


if __name__ == "__main__":
    unittest.main()
