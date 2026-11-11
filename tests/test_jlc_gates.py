"""The JLCPCB gates absorbed from jlceda-pcb-ai-workflow: each one must be able to fail.

Six checks, each with the negative control that decides whether the positive
answer meant anything:

1. the library refuses a rule whose quote is missing (a number with no source is
   how a copied blog value becomes a "limit");
2. the three-tier verdict separates 0.05 (BLOCK) from 0.11 (WARN) from 0.30
   (PASS) on the same rule, and NONE_PRESENT comes back N/A instead of PASS;
3. an exported package is graded, and a package with the top copper file removed
   is BLOCKed - a guard that passes everything passes nothing;
4. a .txt export note inside the package is NOT parsed as Excellon (that was a
   real false BLOCK during the port);
5. the dialogue gate goes PENDING -> CONFIRMED -> STALE when a bound file is
   edited, and refuses a confirmation without the user's own words;
6. seal refuses while the gates are not all confirmed.

The fixtures are the reference project's synthetic ones (MIT); they are synthetic
on purpose - nothing here is a passing board.
"""
import copy
import json
import os
import shutil
import sys
import tempfile
import unittest
import zipfile

sys.path.insert(0, os.path.join(os.path.dirname(os.path.dirname(
    os.path.abspath(__file__))), "src"))

from pcb_agent_mcp.jlc import api, gerber, gates, spec

DATA = os.path.join(os.path.dirname(os.path.abspath(__file__)), "data", "jlc")
ZIP = os.path.join(DATA, "synthetic_2layer_fs45.zip")
BOM = os.path.join(DATA, "synthetic_native_bom_utf16.csv")
CPL = os.path.join(DATA, "synthetic_native_cpl_utf16.csv")
PROCESS = json.load(open(os.path.join(DATA, "process_2layer_1oz.json"), encoding="utf-8"))


class LibraryTest(unittest.TestCase):
    def test_shipped_library_has_no_problem(self):
        report = api.validate_library()
        self.assertTrue(report["ok"], report["problems"][:5])

    def test_rule_without_quote_is_rejected(self):
        data = copy.deepcopy(spec.load("jlc-pcb-capabilities.json"))
        for rule in data["rules"]:
            if rule.get("limit") and "quote" in rule["limit"]:
                rule["limit"]["quote"] = "  "
                break
        problems = spec.validate(data, "pcb")
        self.assertTrue(any("quote" in p for p in problems), problems)


class TierTest(unittest.TestCase):
    def _verdict(self, value, key="min_track_width"):
        return api.spec_check({"layers": 2, "copper_oz": 1},
                              {key: value})["results"]

    def test_block_warn_pass_are_distinct(self):
        for value, expect in ((0.05, "BLOCK"), (0.11, "WARN"), (0.30, "PASS")):
            rows = [r for r in self._verdict(value) if r["rule_id"] == "PCB.TRACE.WIDTH_MIN"]
            self.assertEqual(rows[0]["verdict"], expect, (value, rows[0]))

    def test_none_present_is_not_a_pass(self):
        rows = [r for r in self._verdict("NONE_PRESENT")
                if r["rule_id"] == "PCB.TRACE.WIDTH_MIN"]
        self.assertEqual(rows[0]["verdict"], "N/A")

    def test_missing_measurement_is_evidence_not_pass(self):
        report = api.spec_check({"layers": 2, "copper_oz": 1}, {"min_track_width": 0.3})
        self.assertEqual(report["exit"], 2)
        self.assertGreater(report["summary"]["counts"].get("NEEDS_EVIDENCE", 0), 0)


class GerberTest(unittest.TestCase):
    def test_package_is_measured(self):
        report = api.gerber_guard(ZIP, PROCESS)
        self.assertIn(report["measurements"]["board_w"], (60.0, 60))
        self.assertAlmostEqual(report["measurements"]["board_h"], 40.0, places=6)
        self.assertNotIn("BLOCK", {i["verdict"] for i in report["structure"]},
                         [i for i in report["structure"] if i["verdict"] == "BLOCK"])

    def test_export_note_is_not_parsed_as_excellon(self):
        files = gerber.read_package(ZIP)
        roles = {name: gerber.classify(name, gerber._decode(raw))
                 for name, raw in files.items()}
        self.assertFalse([n for n, r in roles.items() if r.startswith("drill")
                          and n.lower().endswith(".txt")], roles)

    def test_missing_copper_file_blocks(self):
        files = gerber.read_package(ZIP)
        drop = [n for n, raw in files.items()
                if gerber.classify(n, gerber._decode(raw)) == "copper_top"]
        self.assertTrue(drop, sorted(files))
        tmp = tempfile.mkdtemp(prefix="jlc_pkg_")
        try:
            for name, raw in files.items():
                if name in drop:
                    continue
                with open(os.path.join(tmp, name), "wb") as fh:
                    fh.write(raw)
            report = api.gerber_guard(tmp, PROCESS)
            ids = {i["id"] for i in report["structure"] if i["verdict"] == "BLOCK"}
            self.assertIn("GB.FILE.REQUIRED", ids)
        finally:
            shutil.rmtree(tmp, ignore_errors=True)


class BomCplTest(unittest.TestCase):
    def test_native_encoding_is_a_warning_and_sets_match(self):
        report = api.bom_cpl_check(bom=BOM, cpl=CPL, gerber_path=ZIP)
        ids = {i["id"]: i["verdict"] for i in report["items"]}
        self.assertEqual(ids.get("FILE.ENC.NATIVE"), "WARN")
        self.assertEqual(ids.get("X.BOM_CPL.SET"), "PASS")

    def test_designator_mismatch_is_reported(self):
        tmp = tempfile.mkdtemp(prefix="jlc_bom_")
        try:
            dst = os.path.join(tmp, "cpl.csv")
            text = open(CPL, "r", encoding="utf-16").read()
            lines = text.splitlines()
            header = lines[0].split("\t")
            row = lines[1].split("\t")
            row[0] = "ZZ999"
            lines[1] = "\t".join(row)
            open(dst, "w", encoding="utf-8").write("\n".join(lines) + "\n")
            report = api.bom_cpl_check(bom=BOM, cpl=dst)
            ids = {i["id"]: i["verdict"] for i in report["items"]}
            self.assertEqual(ids.get("X.BOM_CPL.SET"), "BLOCK", ids)
        finally:
            shutil.rmtree(tmp, ignore_errors=True)


class GateTest(unittest.TestCase):
    def setUp(self):
        self.project = tempfile.mkdtemp(prefix="jlc_gate_")
        api.gate("init", self.project, "demo", apply=True)
        self.file = os.path.join(self.project, "schematic.json")
        open(self.file, "w").write('{"v": 1}')

    def tearDown(self):
        shutil.rmtree(self.project, ignore_errors=True)

    def test_lifecycle_pending_confirmed_stale(self):
        self.assertEqual([g["state"] for g in api.gate("status", self.project)["gates"]],
                         ["PENDING"] * 5)
        api.gate("confirm", self.project, gate="G1", choice=1,
                 user_reply="原理图我看过了，可以", bind=["schematic.json"])
        states = [g["state"] for g in api.gate("status", self.project)["gates"]]
        self.assertEqual(states[0], "CONFIRMED")
        open(self.file, "w").write('{"v": 2}')
        states = [g["state"] for g in api.gate("status", self.project)["gates"]]
        self.assertEqual(states[0], "STALE", states)

    def test_reply_and_binding_are_mandatory(self):
        with self.assertRaises(ValueError):
            api.gate("confirm", self.project, gate="G1", choice=1, user_reply="  ",
                     bind=["schematic.json"])
        with self.assertRaises(ValueError):
            api.gate("confirm", self.project, gate="G1", choice=1, user_reply="行")

    def test_out_of_order_gate_is_refused(self):
        with self.assertRaises(ValueError):
            api.gate("confirm", self.project, gate="G2", choice=1,
                     user_reply="行", bind=["schematic.json"])

    def test_seal_refuses_while_gates_pending(self):
        with open(os.path.join(self.project, "process.json"), "w") as fh:
            json.dump({"layers": 2, "copper_oz": 1}, fh)
        with self.assertRaises(ValueError) as ctx:
            api.release(self.project, "v1", ZIP, apply=True)
        self.assertIn("gates not all confirmed", str(ctx.exception))


class FabIntegrationTest(unittest.TestCase):
    def test_fab_package_exposes_the_guard(self):
        from pcb_agent_mcp import tools as T
        props = T.TOOLS["board_fab_package"]["inputSchema"]["properties"]
        self.assertIn("jlc_guard", props)
        self.assertIn("jlc_process", props)
        self.assertFalse(props["jlc_guard"].get("default", False))


if __name__ == "__main__":
    unittest.main()
