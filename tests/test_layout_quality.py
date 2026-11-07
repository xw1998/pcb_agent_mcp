"""Layout quality and topology: the two layers a "compliant but ugly" board is
missing.

Every case here has a failure side, which is the only reason the reading exists:
a metric that cannot come out badly is not a metric. The real board used in
docs/117 is the standing control - clearance 0, isolated copper 0, and zero
stitching vias between the two ground pours.

Thresholds are assumptions of the judgement, so the cases read them from the
module rather than repeating them: changing one has to change the test's
expectation too, not silently contradict it.
"""
import os
import shutil
import sys
import tempfile
import unittest

sys.path.insert(0, os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "src"))

from pcb_agent_mcp import layout_quality as Q  # noqa: E402
from pcb_agent_mcp import patterns as P  # noqa: E402
from pcb_agent_mcp import tools  # noqa: E402
from pcb_agent_mcp.adapters.canonical import Board  # noqa: E402

HERE = os.path.dirname(os.path.abspath(__file__))
SAMPLE = os.path.join(HERE, "data", "sample.kicad_pcb")


def _pad(net, x=0.0, y=0.0):
    return {"number": "1", "net": net, "x": x, "y": y, "w": 1.0, "h": 1.0,
            "shape": "roundrect", "rotation": 0.0, "layers": ["F.Cu"],
            "hole_mm": None, "smd": True, "type": "smd"}


def _fp(ref, x, y, rotation=0.0, nets=("SIG",), pads=2, layer="F.Cu"):
    pad_rows = [_pad(nets[i % len(nets)], x=x, y=y) for i in range(pads)]
    return {"ref": ref, "value": ref, "x": x, "y": y, "rotation": rotation,
            "layer": layer, "pads": pad_rows,
            "bbox": {"x_min": x - 1, "y_min": y - 1, "x_max": x + 1, "y_max": y + 1}}


def _board(footprints, outline=None):
    board = Board("test", "memory")
    board.footprints = footprints
    if outline is not None:
        board.outline = [outline]
    return board


class TestTheReadingsCanComeOutBadly(unittest.TestCase):
    """Each reading is checked on a board built to break it, and on one that holds it."""

    def test_spacing_needs_two_parts_and_says_so(self):
        got = Q.metrics(_board([_fp("R1", 0, 0)]))["metrics"]
        self.assertIsNone(got["spacing_median_mm"]["value"])
        self.assertIn("fewer than two", got["spacing_median_mm"]["why"])

    def test_the_same_board_can_score_well_and_badly_depending_on_the_reading(self):
        rhythm = _board([_fp("R1", 0, 0), _fp("C1", 5, 0), _fp("C2", 10, 0)])
        jammed = _board([_fp("R1", 0, 0), _fp("C1", 0.2, 0), _fp("C2", 0.4, 0)])
        good = Q.metrics(rhythm)["metrics"]
        bad = Q.metrics(jammed)["metrics"]
        self.assertLess(bad["spacing_median_mm"]["value"],
                        good["spacing_median_mm"]["value"])
        self.assertGreater(bad["spacing_median_mm"]["crowded_share"], 0.0)
        self.assertEqual(good["spacing_median_mm"]["crowded_share"], 0.0)

    def test_orientation_counts_only_what_is_on_an_axis(self):
        straight = _board([_fp("R1", 0, 0, 0.0), _fp("R2", 5, 0, 90.0)])
        askew = _board([_fp("R1", 0, 0, 37.0), _fp("R2", 5, 0, 12.5)])
        self.assertEqual(Q.metrics(straight)["metrics"]
                         ["orientation_on_axis_share"]["value"], 1.0)
        self.assertEqual(Q.metrics(askew)["metrics"]
                         ["orientation_on_axis_share"]["value"], 0.0)

    def test_alignment_is_measured_with_the_declared_tolerance(self):
        lined = _board([_fp("R1", 0, 0), _fp("R2", 0, 8), _fp("R3", 0, 16)])
        scattered = _board([_fp("R1", 0, 0), _fp("R2", 4, 7), _fp("R3", 9, 2)])
        self.assertEqual(Q.metrics(lined)["metrics"]["alignment_share"]["value"], 1.0)
        self.assertEqual(Q.metrics(scattered)["metrics"]["alignment_share"]["value"], 0.0)

    def test_an_edge_margin_is_not_answered_without_an_outline(self):
        got = Q.metrics(_board([_fp("R1", 0, 0), _fp("R2", 5, 0)]))["metrics"]
        self.assertIsNone(got["edge_margin_median_mm"]["value"])
        self.assertIn("no closed outline", got["edge_margin_median_mm"]["why"])

    def test_a_part_hanging_over_the_edge_is_negative_not_zero(self):
        outline = [(-5, -5), (5, -5), (5, 5), (-5, 5)]
        board = _board([_fp("R1", 0, 0), _fp("R2", 6, 0)], outline=outline)
        got = Q.metrics(board)["metrics"]["edge_margin_median_mm"]
        self.assertLess(got["min"], 0)
        self.assertGreater(got["negative_share"], 0)

    def test_density_sees_the_corner_and_the_empty_half(self):
        corner = [_fp("R%d" % i, float(i) * 0.5, 0.0) for i in range(6)]
        board = _board(corner, outline=[(0, 0), (100, 0), (100, 100), (0, 100)])
        got = Q.metrics(board)["metrics"]
        self.assertGreater(got["density_cv"]["value"], 0.5)
        self.assertGreater(got["density_empty_share"]["value"], 0.7)

    def test_small_parts_are_listed_as_unclassified_not_guessed(self):
        got = Q.groups(_board([_fp("U1", 0, 0, nets=("A",), pads=20),
                               _fp("R9", 40, 40, nets=("ZZZ",))]))
        self.assertIn("R9", got["unclassified"])
        self.assertEqual([g["name"] for g in got["groups"] if g["name"] == "mcu"], ["mcu"])


class TestTheLayerIsTheFootprintsOwn(unittest.TestCase):
    """A copper graphic inside a footprint must not rename the footprint's layer.

    Measured 2026-11-07 on the F412 core board: 30 of 32 parts came back as "F.Fab"
    and 2 as "B.Fab", because the graphic loop reused the variable `layer`. Every
    side-dependent answer inherited that: board_components(layer="B.Cu") was empty
    and `side` in an extracted layout was a coin toss.
    """

    SRC = """(kicad_pcb (version 20221018) (generator t)
      (layers (0 "F.Cu" signal) (31 "B.Cu" signal))
      (net 0 "")
      (footprint "Test:LED" (layer "B.Cu") (at 10 10 0)
        (fp_poly (pts (xy 0 0) (xy 1 0) (xy 1 1))
          (stroke (width 0.1) (type solid)) (fill yes) (layer "F.Cu"))
        (pad "1" smd roundrect (at 0 0) (size 1 1) (layers "F.Cu") (net 0 ""))))"""

    def test_a_copper_graphic_does_not_overwrite_the_layer(self):
        from pcb_agent_mcp.adapters import kicad_pcb as K
        board = K.parse_board_text(self.SRC)
        self.assertEqual(board.footprints[0]["layer"], "B.Cu")
        self.assertEqual(len(board.footprints[0]["graphics"]), 1)

    def test_a_board_on_disk_reports_copper_sides_not_fab_layers(self):
        out = tools.call_tool("board_components", {"path": SAMPLE})
        rows = out["value"]
        for row in rows:
            self.assertNotIn("Fab", str(row.get("layer")),
                             "%s came back on %r" % (row.get("ref"), row.get("layer")))


class TestPercentilesNeedACorpus(unittest.TestCase):

    def test_no_reference_means_no_percentile_and_it_says_why(self):
        got = Q.metrics(_board([_fp("R1", 0, 0), _fp("R2", 5, 0)]))
        self.assertIsNone(got["reference"])
        self.assertIn("no reference set", got["why_no_percentile"])
        self.assertNotIn("percentile",
                         got["metrics"]["spacing_median_mm"])

    def test_a_directional_reading_gets_a_percentile_a_neutral_one_does_not(self):
        target = _board([_fp("R1", 0, 0, 40.0), _fp("R2", 5, 0, 40.0)])
        good = _board([_fp("R1", 0, 0, 0.0), _fp("R2", 5, 0, 0.0)])
        mid = _board([_fp("R1", 0, 0, 0.0), _fp("R2", 5, 0, 90.0)])
        got = Q.metrics(target, reference=[good, mid])["metrics"]
        self.assertIn("percentile", got["orientation_on_axis_share"])
        self.assertEqual(got["orientation_on_axis_share"]["percentile"], 0.0)
        self.assertNotIn("percentile", got["spacing_median_mm"])
        self.assertIn("position_pct", got["spacing_median_mm"])

    def test_fewer_than_five_references_is_not_usable_and_says_so(self):
        target = _board([_fp("R1", 0, 0, 0.0), _fp("R2", 5, 0, 0.0)])
        other = _board([_fp("R1", 0, 0, 45.0), _fp("R2", 5, 0, 30.0)])
        got = Q.metrics(target, reference=[other, other])
        self.assertFalse(got["reference"]["usable_for_judgement"])
        self.assertIn("not usable", got["metrics"]["orientation_on_axis_share"]
                      ["percentile_note"])

    def test_a_backup_folder_full_of_copies_is_not_a_corpus(self):
        target = _board([_fp("R1", 0, 0), _fp("R2", 5, 0)])
        copies = [_board([_fp("R1", 0, 0), _fp("R2", 5, 0)]) for _ in range(6)]
        got = Q.metrics(target, reference=copies)
        self.assertFalse(got["reference"]["usable_for_judgement"])
        self.assertEqual(got["reference"]["same_reference_set_as_this_board"], 6)


class TestTopologyIsTheStepBeforeCoordinates(unittest.TestCase):

    def test_the_controller_is_found_by_fan_out_not_by_its_name(self):
        board = _board([_fp("U1", 0, 0, pads=24, nets=("PA0", "PA1")),
                        _fp("Y1", -8, 0, nets=("XTAL_IN", "XTAL_OUT")),
                        _fp("C1", -6, 2, nets=("GND",))])
        got = Q.topology(board)
        names = {g["name"]: g for g in got["groups"]}
        self.assertIn("U1", names["mcu"]["refs"])
        self.assertIn("Y1", names["clock"]["refs"])

    def test_a_flash_part_is_memory_even_when_it_touches_a_rail(self):
        board = _board([_fp("U1", 0, 0, pads=24, nets=("PA0",)),
                        _fp("U3", 10, 0, nets=("+3V3", "W25_CS", "W25_CLK",
                                               "W25_IO0", "W25_IO1"))])
        got = Q.topology(board)
        names = {g["name"]: g for g in got["groups"]}
        self.assertIn("U3", names["memory"]["refs"])

    def test_the_flow_starts_at_power_and_reaches_what_is_wired(self):
        board = _board([_fp("U1", 0, 0, pads=24, nets=("PA0", "VDD")),
                        _fp("C1", 3, 0, nets=("VDD", "GND")),
                        _fp("U3", 10, 0, nets=("VDD", "W25_CS"))])
        got = Q.topology(board)
        self.assertEqual(got["flow"]["source"], "power")
        self.assertIn("memory", got["flow"]["order"])
        self.assertEqual(got["flow"]["unreached"], [])

    def test_a_group_wired_to_nothing_is_reported_unreached(self):
        board = _board([_fp("U1", 0, 0, pads=24, nets=("VDD", "PA0")),
                        _fp("J9", 40, 40, nets=("NOBODY",))])
        got = Q.topology(board)
        self.assertIn("J9", got["unclassified"])


class TestTheToolsAnswer(unittest.TestCase):

    def test_the_metrics_tool_reports_absolute_readings_without_a_reference(self):
        out = tools.call_tool("board_layout_metrics", {"path": SAMPLE})
        self.assertEqual(out["status"], "ok", out)
        self.assertIn("no reference set", out["message"])
        metrics = out["value"]["metrics"]
        self.assertAlmostEqual(metrics["spacing_median_mm"]["value"], 10.0, places=3)
        self.assertEqual(metrics["alignment_share"]["value"], 1.0)
        self.assertIs(out.get("authoritative"), False)

    def test_the_metrics_tool_takes_a_named_reference_set(self):
        out = tools.call_tool("board_layout_metrics",
                              {"path": SAMPLE, "reference": [SAMPLE]})
        self.assertEqual(out["status"], "ok", out)
        # A single reference identical to the target is arithmetic, not a judgement.
        self.assertFalse(out["value"]["reference"]["usable_for_judgement"])
        self.assertIn("NOT usable", out["message"])

    def test_a_reference_that_cannot_be_parsed_is_reported_not_dropped(self):
        out = tools.call_tool("board_layout_metrics",
                              {"path": SAMPLE,
                               "reference": [os.path.join(HERE, "data", "nope.kicad_pcb")]})
        self.assertEqual(out["status"], "ok", out)
        self.assertTrue(out["value"]["reference_skipped"])

    def test_the_topology_tool_answers_groups_links_and_flow(self):
        out = tools.call_tool("board_topology", {"path": SAMPLE})
        self.assertEqual(out["status"], "ok", out)
        value = out["value"]
        for key in ("groups", "links", "flow", "how"):
            self.assertIn(key, value)
        self.assertIs(out.get("authoritative"), False)


class TestAPitfallIsAFailureSampleWithACause(unittest.TestCase):

    def setUp(self):
        self.home = tempfile.mkdtemp(prefix="pat_pit_")
        self._old = os.environ.get("PCB_AGENT_HOME")
        os.environ["PCB_AGENT_HOME"] = self.home

    def tearDown(self):
        if self._old is None:
            os.environ.pop("PCB_AGENT_HOME", None)
        else:
            os.environ["PCB_AGENT_HOME"] = self._old
        shutil.rmtree(self.home, ignore_errors=True)

    def _entry(self, **over):
        base = {"id": "pat.pitfall.density-collapse",
                "title": "everything jammed into one corner",
                "symptom": "density_cv far above the reference set",
                "cause": "parts placed one by one, no partition first",
                "fix": "run board_topology, allocate a region per group, then place",
                "metric": "density_cv", "below_percentile": 10}
        base.update(over)
        return base

    def test_a_symptom_too_short_to_recognise_is_refused(self):
        out = tools.call_tool("pattern_add",
                              {"kind": "pitfall",
                               "entry": self._entry(symptom="ugly")})
        self.assertEqual(out["status"], "error")
        self.assertIn("at least 8 characters", out["message"])

    def test_a_metric_nobody_can_measure_again_is_refused(self):
        out = tools.call_tool("pattern_add",
                              {"kind": "pitfall",
                               "entry": self._entry(metric="looks_bad")})
        self.assertEqual(out["status"], "error")
        self.assertIn("not one board_layout_metrics reports", out["message"])

    def test_a_complete_pitfall_lands_as_a_candidate(self):
        out = tools.call_tool("pattern_add", {"kind": "pitfall",
                                              "entry": self._entry()})
        self.assertEqual(out["status"], "ok", out)
        self.assertEqual(out["value"]["entry"]["status"], "candidate")
        recalled = tools.call_tool("pattern_recall",
                                   {"kind": "pitfall", "status": "candidate"})
        self.assertEqual(recalled["value"]["returned"], 1)
        row = P.get("pat.pitfall.density-collapse")
        self.assertEqual(row["metric"], "density_cv")
        self.assertEqual(row["below_percentile"], 10)


if __name__ == "__main__":
    unittest.main()
