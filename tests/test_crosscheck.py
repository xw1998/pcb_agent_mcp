"""The derived answers, checked against the vendor where a vendor answer exists.

`board_clearance` is this server's own geometry; KiCad's DRC is a ruling. This
module is the only place where the two are put side by side, so its tests care
about three things:

1. it refuses when there is no ruling to compare against (no kicad-cli, wrong
   container, no file) instead of comparing the measurement to itself;
2. on a board built to have exactly one violation, it finds that violation on
   both sides and says `recall == 1.0` - and it does NOT claim agreement on a
   board where neither side found anything (`no_signal`);
3. the four derived answers with no vendor counterpart are reported as
   `not_comparable` instead of being silently blessed.
"""
import os
import shutil
import tempfile
import unittest

from pcb_agent_mcp import edit as E
from pcb_agent_mcp.fidelity import crosscheck as CC
from pcb_agent_mcp.fidelity import engine as ENGINE

HAS_CLI = bool(ENGINE.find_kicad_cli())


class _Tmp(unittest.TestCase):

    def setUp(self):
        self.tmp = tempfile.mkdtemp(prefix="pcbagent-cc-")

    def tearDown(self):
        shutil.rmtree(self.tmp, ignore_errors=True)


class TestRefusals(_Tmp):

    def test_a_missing_file_is_refused(self):
        out = CC.clearance_vs_drc(os.path.join(self.tmp, "nope.kicad_pcb"))
        self.assertEqual(out["reason_code"], "FILE_NOT_FOUND")
        self.assertFalse(os.path.exists(os.path.join(self.tmp, "nope.kicad_pcb")))

    def test_a_foreign_container_is_refused(self):
        import zipfile
        path = os.path.join(self.tmp, "proj.epro")
        with zipfile.ZipFile(path, "w") as zf:
            zf.writestr("project.json", "{}")
        out = CC.clearance_vs_drc(path)
        self.assertEqual(out["reason_code"], "FORMAT_NOT_SUPPORTED")


class TestNoCounterpart(unittest.TestCase):

    def test_the_four_unverifiable_metrics_say_so(self):
        out = CC.derive_all("ignored.kicad_pcb",
                            metrics=["free_space", "pour_connectivity",
                                     "cross_split", "loop_area"])
        self.assertEqual(out["summary"]["not_comparable_metrics"],
                         ["cross_split", "free_space", "loop_area",
                          "pour_connectivity"])
        for name in ("free_space", "pour_connectivity", "cross_split", "loop_area"):
            self.assertEqual(out["metrics"][name]["status"], "not_comparable")
            self.assertTrue(out["metrics"][name]["reason"])


@unittest.skipUnless(HAS_CLI, "no kicad-cli: there is no vendor ruling to compare")
class TestOnABuiltBoard(_Tmp):
    """A board built by this server, with one clearance violation put in on purpose."""

    def build(self, gap_mm):
        path = os.path.join(self.tmp, "cc.kicad_pcb")
        E.create_board(path, {"width_mm": 30, "height_mm": 20, "engine": False})
        plan = E.propose(path, [
            {"op": "add_net", "name": "N1"},
            {"op": "add_net", "name": "N2"},
            {"op": "add_track", "x1": 5, "y1": 10, "x2": 15, "y2": 10,
             "width": 0.25, "layer": "F.Cu", "net": "N1"},
            {"op": "add_track", "x1": 5, "y1": 10 + gap_mm, "x2": 15,
             "y2": 10 + gap_mm, "width": 0.25, "layer": "F.Cu", "net": "N2"},
        ], {"engine": False})
        E.apply_plan(plan["plan_id"], confirm=True, options={"engine": False})
        return path

    def test_one_violation_is_found_by_both_sides(self):
        # centre lines 0.4 apart, 0.25 wide -> 0.15 mm of copper gap, under the
        # 0.2 mm net-class rule this server wrote into the board
        path = self.build(0.4)
        out = CC.clearance_vs_drc(path, threshold_mm=0.5)
        self.assertEqual(out["status"], "ok")
        self.assertGreaterEqual(out["drc"]["clearance_checks"], 1)
        self.assertGreaterEqual(out["ours"]["pairs_below_threshold"], 1)
        self.assertEqual(out["agreement"]["recall"], 1.0)
        self.assertEqual(out["agreement"]["drc_only_total"], 0)
        self.assertEqual(out["verdict"], "drc_covered")
        # the pair we flagged is the pair DRC flagged: both are the two tracks
        self.assertEqual(out["ours"]["pairs_below_threshold"],
                         out["agreement"]["matched"])
        self.assertEqual(out["ours_only"] if "ours_only" in out else
                         out["agreement"]["ours_only_total"], 0)

    def test_our_report_is_not_a_claim_of_agreement_when_nothing_is_found(self):
        path = self.build(4.0)                    # far apart: nobody complains
        out = CC.clearance_vs_drc(path, threshold_mm=0.5)
        self.assertEqual(out["drc"]["clearance_checks"], 0)
        self.assertEqual(out["ours"]["pairs_below_threshold"], 0)
        self.assertIsNone(out["agreement"]["recall"])
        self.assertEqual(out["verdict"], "no_signal")
        self.assertIn("proves nothing", out["verdict_explained"])

    def test_the_threshold_comes_from_the_boards_own_rules(self):
        path = self.build(0.4)
        out = CC.clearance_vs_drc(path)           # no explicit threshold
        self.assertEqual(out["thresholds"]["ours_mm"], 0.2)
        self.assertIn("net class", out["thresholds"]["source"])

    def test_a_drc_miss_is_reported_as_missed_not_ignored(self):
        """Call the matcher with a tolerance that cannot match anything."""
        path = self.build(0.4)
        out = CC.clearance_vs_drc(path, threshold_mm=0.5, tol_mm=-1.0)
        self.assertEqual(out["verdict"], "missed_violations")
        self.assertEqual(out["agreement"]["recall"], 0.0)
        self.assertEqual(out["agreement"]["drc_only_total"], 1)
        self.assertIn("wrong in the dangerous direction",
                      out["agreement"]["drc_only"][0]["note"])


if __name__ == "__main__":
    unittest.main()
