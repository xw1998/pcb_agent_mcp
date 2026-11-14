"""The citation ledger, and the two ways it could lie.

A ledger of sources is worth nothing if every rule can claim to be in it, or if a
missing file reads as "nothing to check". So every test here has a control that
must fail:

* a rule that is NOT in the ledger must come back as UNCITED, with a reason,
  never as an empty entry that a caller would read as "checked, no problem";
* a ledger that has been deleted must raise the error path, not return {};
* a quote pointing at a source key the file does not define must be rejected -
  that is the one mechanical way a fabricated citation would slip in.
"""
import io
import json
import os
import sys
import tempfile
import unittest

_HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, os.path.join(os.path.dirname(_HERE), "src"))

from pcb_agent_mcp.calc import rule_sources as RS      # noqa: E402
from pcb_agent_mcp.calc.rules import LAYOUT_RULES      # noqa: E402

RULE_IDS = [r["id"] for r in LAYOUT_RULES]
SRC_BY_ID = {r["id"]: r["source"] for r in LAYOUT_RULES}


class TestLedgerIsReadable(unittest.TestCase):
    def test_ledger_parses_and_every_quote_is_non_empty(self):
        led, err = RS.ledger()
        self.assertIsNone(err, "ledger must load: %r" % (err,))
        self.assertTrue(led["entries"], "ledger has no usable entries")
        for rid, e in led["entries"].items():
            self.assertTrue(e["quotes"], "%s has no quotes" % rid)
            for q in e["quotes"]:
                self.assertTrue(q["quote"].strip(), "%s has an empty quote" % rid)
                self.assertIn(q["src"], led["sources"],
                              "%s cites an undefined source %s" % (rid, q["src"]))
        self.assertEqual(led["problems"], [],
                         "ledger self-check found problems: %r" % (led["problems"],))

    def test_every_source_carries_a_url_and_a_date(self):
        """A source without a URL is a claim without a place to check it."""
        led, err = RS.ledger()
        self.assertIsNone(err)
        for key, s in led["sources"].items():
            self.assertTrue(str(s.get("url", "")).startswith("http"),
                            "%s has no http url" % key)
            self.assertTrue(s.get("fetched_at"), "%s has no fetched_at" % key)
            self.assertIn(s.get("tier"), ("T0", "T1", "T2"),
                          "%s has an unknown tier %r" % (key, s.get("tier")))

    def test_a_cited_entry_has_a_known_rule_id(self):
        """Control: the ledger must not cite rules that do not exist."""
        led, err = RS.ledger()
        self.assertIsNone(err)
        for rid in led["entries"]:
            self.assertIn(rid, RULE_IDS, "%s is not a layout rule" % rid)


class TestUncitedIsReportedAsUncited(unittest.TestCase):
    """The reverse control the user's question needed: what happens to the rest."""

    def test_an_unlisted_rule_is_refused_with_a_reason(self):
        e, why = RS.citation("layout.decoupling_distance")
        self.assertIsNone(e)
        self.assertIn("UNCITED", why)
        self.assertIn("layout.decoupling_distance", why)

    def test_source_line_returns_none_not_a_guess(self):
        line, why = RS.source_line("layout.diffpair_skew")
        self.assertIsNone(line)
        self.assertIn("UNCITED", why)

    def test_coverage_counts_add_up_and_lists_both_sides(self):
        rep, err = RS.coverage(RULE_IDS)
        self.assertIsNone(err)
        self.assertEqual(rep["n_cited"] + rep["n_uncited"], rep["n_rules"])
        self.assertEqual(rep["n_rules"], len(RULE_IDS))
        self.assertEqual(set(rep["cited"]) | set(rep["uncited"]), set(RULE_IDS))
        self.assertEqual(set(rep["cited"]) & set(rep["uncited"]), set())
        # this round's honest number: five rules got a sentence, the rest did not
        self.assertEqual(rep["n_cited"], 5)
        self.assertEqual(rep["n_uncited"], len(RULE_IDS) - 5)

    def test_coverage_is_not_a_global_number(self):
        """Ask about one uncited rule and the report must say 0 cited, not 5."""
        rep, err = RS.coverage(["layout.antenna_keepout"])
        self.assertIsNone(err)
        self.assertEqual((rep["n_cited"], rep["n_uncited"], rep["n_rules"]), (0, 1, 1))


class TestTheFiveCitedRulesNowSaySomethingReal(unittest.TestCase):
    CITED = ["layout.magnetic_keepout", "layout.magnetic_keepout_deep",
             "layout.magnetic_via_keepout", "layout.crystal_keepout_adjacent",
             "layout.rf_right_angle_bend"]

    def test_source_strings_no_longer_name_only_our_own_skill(self):
        for rid in self.CITED:
            src = SRC_BY_ID[rid]
            self.assertIsInstance(src, str)
            # control: the old self-citation must be gone from these five
            self.assertNotIn("kicad-free (", src, "%s still self-cites" % rid)
            self.assertLess(len(src), 400,
                            "%s source string is a paragraph, not a citation" % rid)

    def test_uncited_rules_still_self_cite(self):
        """Control in the other direction: an uncited rule must NOT look cited."""
        for rid in ("layout.decoupling_distance", "layout.antenna_keepout",
                    "layout.stitching_via_pitch"):
            self.assertNotEqual(SRC_BY_ID[rid].strip(), "")
            _, why = RS.citation(rid)
            self.assertIsNotNone(why, "%s looks cited but is not in the ledger" % rid)

    def test_magnetic_keepout_has_three_independent_sources(self):
        e, why = RS.citation("layout.magnetic_keepout")
        self.assertIsNone(why)
        self.assertEqual(e["agreement"], "three_independent_sources_agree")
        srcs = {q["src"] for q in e["quotes"]}
        self.assertEqual(srcs, {"S_ADI", "S_ROHM", "S_NEXTPCB"},
                         "three-source claim must actually carry three sources")

    def test_the_deep_rule_records_its_conflict(self):
        """If the conflict is dropped, this rule silently becomes 'everyone agrees'."""
        e, why = RS.citation("layout.magnetic_keepout_deep")
        self.assertIsNone(why)
        self.assertIn("conflict", e["agreement"])
        self.assertTrue(e.get("conflict_note"),
                        "a rule with conflicting sources must say so in the ledger")


class TestMissingFileIsAnErrorNotAnEmptyLedger(unittest.TestCase):
    def test_deleted_ledger_raises_instead_of_returning_empty(self):
        saved = RS.DATA_PATH
        try:
            RS.DATA_PATH = os.path.join(tempfile.gettempdir(), "__no_such_ledger__.json")
            led, err = RS.ledger()
            self.assertIsNone(led, "a missing ledger must not produce an object")
            self.assertIn("missing", err)
            cov, err2 = RS.coverage(RULE_IDS)
            self.assertIsNone(cov, "coverage must propagate the error, not print 0/0")
            self.assertIn("missing", err2)
        finally:
            RS.DATA_PATH = saved

    def test_a_fabricated_source_key_is_rejected(self):
        """The mechanical guard: a quote pointing nowhere invalidates the entry."""
        saved = RS.DATA_PATH
        tmp = os.path.join(tempfile.gettempdir(), "__bad_ledger__.json")
        try:
            ledger = {
                "schema_version": 1,
                "sources": {"S_OK": {"tier": "T1", "title": "t", "url": "http://x",
                                     "fetched_at": "2026-11-14"}},
                "entries": [
                    {"rule_id": "layout.invented",
                     "quotes": [{"src": "S_DOES_NOT_EXIST", "quote": "something"}]},
                ],
            }
            with io.open(tmp, "w", encoding="utf-8") as f:
                json.dump(ledger, f)
            RS.DATA_PATH = tmp
            led, err = RS.ledger()
            self.assertIsNone(err)
            self.assertEqual(led["entries"], {},
                             "an entry citing an undefined source must be dropped")
            self.assertTrue(led["problems"], "the drop must be reported, not silent")
            self.assertIn("unknown source", led["problems"][0])
        finally:
            RS.DATA_PATH = saved
            if os.path.exists(tmp):
                os.remove(tmp)


if __name__ == "__main__":
    unittest.main()
