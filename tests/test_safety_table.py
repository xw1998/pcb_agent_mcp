"""The safety tables are only as good as what can be checked about them.

Three things are pinned here:
  1. the shipped files are still refusing (no silent "verified" crept in),
  2. the validator rejects a hand-written table that looks right but breaks a rule -
     a single source, a distance that falls as voltage rises, a worse material group
     asking for less,
  3. the reason the tables are empty is recorded as data (the sources disagree), not
     as a comment somebody can delete.
"""

import json
import os
import tempfile
import unittest

from pcb_agent_mcp.calc import creepage, safety_table


def _row(v, d, pd="PD2", mg="IIIa", ins="basic", sources=("a", "b"), table="F.4"):
    return {"voltage_v": v, "pollution_degree": pd, "material_group": mg,
            "insulation": ins, "distance_mm": d, "table": table,
            "sources": list(sources)}


def _doc(rows, verified=True, edition="IEC 60664-1:2007", schema=2):
    return {"schema": schema, "verified": verified, "edition": edition,
            "tables": {"creepage|IEC 60664-1": rows}}


class TheShippedTablesStillRefuse(unittest.TestCase):
    def test_the_creepage_table_is_not_usable_and_says_why(self):
        rep = safety_table.check_which("creepage")
        self.assertEqual(rep["status"], "ok")
        self.assertFalse(rep["usable"])
        self.assertIn("declared_verified", rep["failed"])

    def test_the_tool_that_needs_it_still_fails_closed(self):
        r = creepage.creepage_required(250, pollution_degree=2, material_group="IIIa")
        self.assertEqual(r["status"], "error")
        self.assertEqual(r["reason_code"], "SAFETY_TABLE_MISSING")

    def test_the_disagreement_that_blocks_filling_is_recorded(self):
        with open(safety_table.DATA_PATH, encoding="utf-8") as fh:
            doc = json.load(fh)
        disputes = doc["candidates"]["disputes"]
        self.assertTrue(disputes)
        first = disputes[0]
        self.assertEqual(first["key"]["voltage_v"], 250)
        self.assertEqual(sorted(first["values_mm"]), [1.5, 2.5])

    def test_the_disagreement_detector_finds_it(self):
        rows = [{"pollution_degree": "PD2", "material_group": "IIIa", "voltage_v": 250,
                 "distance_mm": 1.5, "source": "x"},
                {"pollution_degree": "PD2", "material_group": "IIIa", "voltage_v": 250,
                 "distance_mm": 2.5, "source": "y"}]
        found = safety_table.sources_disagree(rows)
        self.assertEqual(len(found), 1)
        self.assertEqual(found[0]["values_mm"], [1.5, 2.5])

    def test_the_ipc2152_table_is_empty_and_not_usable(self):
        rep = safety_table.check_which("ipc2152")
        self.assertEqual(rep["status"], "ok")
        self.assertFalse(rep["usable"])
        self.assertEqual(rep["rows"], 0)


class ATableThatPassesAllTheRulesIsAccepted(unittest.TestCase):
    def test_a_monotone_two_source_table_is_usable(self):
        rows = [_row(50, 0.6), _row(100, 0.9), _row(250, 2.5), _row(500, 3.0),
                _row(250, 2.0, mg="II"), _row(250, 1.0, mg="I"),
                _row(250, 3.2, pd="PD3"), _row(250, 0.4, pd="PD1")]
        rep = safety_table.validate(_doc(rows))
        self.assertTrue(rep["usable"], rep["checks"])
        self.assertEqual(rep["rows"], len(rows))

    def test_one_source_is_not_verification(self):
        rows = [_row(250, 2.5, sources=("a",))]
        rep = safety_table.validate(_doc(rows))
        self.assertFalse(rep["usable"])
        self.assertIn("every_row_has_provenance", rep["failed"])

    def test_a_distance_that_falls_with_voltage_is_caught(self):
        rows = [_row(250, 2.5), _row(500, 1.0)]
        rep = safety_table.validate(_doc(rows))
        self.assertFalse(rep["usable"])
        self.assertIn("distance_rises_with_voltage", rep["failed"])

    def test_a_worse_material_group_asking_for_less_is_caught(self):
        rows = [_row(250, 1.0, mg="I"), _row(250, 0.5, mg="IIIa")]
        rep = safety_table.validate(_doc(rows))
        self.assertFalse(rep["usable"])
        self.assertIn("worse_case_never_asks_for_less", rep["failed"])

    def test_a_dirtier_environment_asking_for_less_is_caught(self):
        rows = [_row(250, 2.5, pd="PD2"), _row(250, 1.0, pd="PD3")]
        rep = safety_table.validate(_doc(rows))
        self.assertFalse(rep["usable"])
        self.assertIn("worse_case_never_asks_for_less", rep["failed"])

    def test_an_unnamed_edition_is_caught(self):
        rep = safety_table.validate(_doc([_row(250, 2.5)], edition=""))
        self.assertFalse(rep["usable"])
        self.assertIn("edition_named", rep["failed"])

    def test_a_duplicate_row_is_caught(self):
        rep = safety_table.validate(_doc([_row(250, 2.5), _row(250, 2.5)]))
        self.assertFalse(rep["usable"])
        self.assertIn("no_duplicate_rows", rep["failed"])

    def test_an_old_schema_is_refused_rather_than_guessed(self):
        rep = safety_table.validate(_doc([_row(250, 2.5)], schema=1))
        self.assertFalse(rep["usable"])
        self.assertIn("schema_is_current", rep["failed"])


class TheIpc2152PointsHaveTheirOwnRules(unittest.TestCase):
    def _doc(self, points, verified=True):
        return {"schema": 2, "verified": verified, "edition": "IPC-2152 (2019)",
                "points": points}

    def _p(self, oz, dt, area, cur, sources=("a", "b")):
        return {"copper_oz": oz, "delta_t_c": dt, "area_mil2": area,
                "current_a": cur, "table": "fig 5-1", "sources": list(sources)}

    def test_a_sane_table_passes(self):
        rep = safety_table.validate_ipc2152(self._doc(
            [self._p(1.0, 10, 100, 2.0), self._p(1.0, 10, 400, 6.0)]))
        self.assertTrue(rep["usable"], rep["checks"])

    def test_current_falling_with_area_is_caught(self):
        rep = safety_table.validate_ipc2152(self._doc(
            [self._p(1.0, 10, 100, 6.0), self._p(1.0, 10, 400, 2.0)]))
        self.assertFalse(rep["usable"])
        self.assertIn("current_rises_with_area", rep["failed"])

    def test_a_hotter_budget_allowing_less_current_is_caught(self):
        rep = safety_table.validate_ipc2152(self._doc(
            [self._p(1.0, 10, 100, 5.0), self._p(1.0, 30, 100, 2.0)]))
        self.assertFalse(rep["usable"])
        self.assertIn("current_rises_with_delta_t", rep["failed"])


class AnAcceptedTableActuallyAnswers(unittest.TestCase):
    """Proves the gate is not decorative: write a valid table, point the loader at it."""

    def test_a_valid_file_makes_the_lookup_work(self):
        rows = [_row(50, 0.6), _row(100, 0.9), _row(250, 2.5), _row(500, 3.0)]
        doc = _doc(rows)
        fd, path = tempfile.mkstemp(suffix=".json")
        os.close(fd)
        try:
            with open(path, "w", encoding="utf-8") as fh:
                json.dump(doc, fh)
            rep = safety_table.check_file(path)
            self.assertTrue(rep["usable"], rep["checks"])

            saved = creepage.DATA_PATH
            creepage.DATA_PATH = path
            try:
                r = creepage.creepage_required(250, pollution_degree=2,
                                               material_group="IIIa",
                                               safety_standard="IEC 60664-1")
                self.assertEqual(r["status"], "ok")
                self.assertEqual(r["value"], 2.5)
                # below the first tabulated step -> refused, not extrapolated
                r2 = creepage.creepage_required(10, pollution_degree=2,
                                                material_group="IIIa",
                                                safety_standard="IEC 60664-1")
                self.assertEqual(r2["reason_code"], "OUT_OF_TABLE")
            finally:
                creepage.DATA_PATH = saved
        finally:
            os.unlink(path)


if __name__ == "__main__":
    unittest.main()
