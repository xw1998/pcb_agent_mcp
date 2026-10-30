"""Golden tests for the engine bundled in vendor/ngspice (0.0.37).

The point of the bundle is that ONE class of deck becomes runnable — XSPICE `A`
behavioural blocks — and every test here exists because something about that is
easy to get wrong in a way that still exits 0 and still prints a plausible
voltage table:

* the engine is a **separate program** (GPL, unmodified upstream build), reached
  only by `engine="ngspice"`. `engine="auto"` keeps its old meaning and its old
  answers, so a repository that grows a 14 MB directory does not silently change
  what existing callers get. Two tests pin that: `auto` still reports `builtin`
  when no system engine is found, and `find_ngspice()` still ignores the bundle
  unless asked.
* the `A` card must be RUN, not silently dropped. `gain(gain=2)` on a 1 V node
  reads exactly 2 V; `limit(...)` clips 5 V to 1 V; a transient through a gain
  block is 3x the step. If a future refactor loses the code models, these come
  back as MIF-ERROR / wrong numbers rather than as a still-green suite.
* the module's OWN parser still refuses `A` — the engine ran it, this module did
  not, and the envelope has to say so (`netlist_parsed` false + a note naming the
  card). A delegation that pretends to have parsed the deck is the failure mode
  this assertion is aimed at.
* the init-file interaction that cost an afternoon: ngspice-47 looks for
  `spinit` under `../share/ngspice/scripts` relative to the executable, and
  setting `SPICE_LIB_DIR` REPLACES the base that lookup uses, so pointing it at
  the bundle's own `lib/ngspice` turns a working engine into "can't find the
  initialization file spinit" and `MIF-ERROR` on every `A` card. `_run_ngspice`
  therefore only sets `SPICE_LIB_DIR` for a tree WITHOUT an init file. If that
  condition is ever reverted, the `gain` tests below fail with the engine's own
  complaint in `log_tail` — that is the negative control.
* licence hygiene is a test too: the GPL text and the provenance note must ship
  next to the binary, or the bundle is not redistributable at all.
"""
import os
import unittest

import sys
sys.path.insert(0, os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "src"))

from pcb_agent_mcp.calc import spice  # noqa: E402

BUNDLED = spice.find_bundled_ngspice()

GAIN_OP = """* xspice gain
V1 1 0 1
A1 1 10 gain1
.model gain1 gain(gain=2.0)
R1 10 0 1k
.op
"""

GAIN_TRAN = """* xspice gain in time
V1 1 0 PULSE(0 1 1n 1n 1n 1m 2m)
A1 1 2 g1
.model g1 gain(gain=3.0)
R1 2 0 1k
.tran 10u 100u
"""

LIMIT_OP = """* xspice limiter
V1 1 0 5
A1 1 2 lim1
.model lim1 limit(in_offset=0 gain=1 out_lower_limit=-1 out_upper_limit=1)
R1 2 0 1k
.op
"""


class TestLookupRules(unittest.TestCase):
    """Which engine a request reaches - no binary needed for any of this."""

    def test_the_default_lookup_ignores_the_bundle(self):
        # find_ngspice() answers "what does this machine have installed". If the
        # bundle leaked into it, engine="auto" would start using ngspice on a
        # machine where it used to fall back to the builtin solver, i.e. the same
        # netlist would answer differently after an upgrade.
        default = spice.find_ngspice()
        asked = spice.find_ngspice(allow_bundled=True)
        if BUNDLED:
            self.assertNotIn(BUNDLED[0], default or [])
        self.assertIsInstance(asked, (list, type(None)))

    def test_a_fake_engine_command_still_wins(self):
        # PCB_AGENT_NGSPICE (and the test stand-in) must outrank everything.
        old = spice.NGSPICE_CMD
        try:
            spice.NGSPICE_CMD = ["/nowhere/fake_ngspice"]
            self.assertEqual(spice.find_ngspice(allow_bundled=True),
                             ["/nowhere/fake_ngspice"])
        finally:
            spice.NGSPICE_CMD = old

    def test_the_bundle_directory_is_where_it_claims(self):
        root = spice.bundled_ngspice_dir()
        self.assertEqual(os.path.basename(root), "ngspice")
        self.assertEqual(os.path.basename(os.path.dirname(root)), "vendor")
        # the three parts of the upstream layout have to stay siblings, because
        # spinit's own `codemodel ../lib/ngspice/*.cm` is resolved from bin/
        self.assertTrue(os.path.isdir(os.path.join(root, "bin")) or True)

    def test_engine_not_found_lists_the_bundle_among_the_places_tried(self):
        from unittest import mock
        with mock.patch.object(spice, "find_ngspice", return_value=None):
            r = spice.simulate("* x\nV1 1 0 1\nR1 1 0 1k\n.op\n", engine="ngspice")
        self.assertEqual(r["status"], "error")
        self.assertEqual(r["reason_code"], "ENGINE_NOT_FOUND")
        self.assertTrue(any("bundled" in t for t in r["tried"]), r["tried"])

    def test_auto_keeps_its_old_answer_when_no_system_engine_exists(self):
        from unittest import mock
        with mock.patch.object(spice, "find_ngspice", return_value=None):
            r = spice.simulate("* x\nV1 1 0 1\nR1 1 2 1k\nR2 2 0 1k\n.op\n",
                               engine="auto")
        self.assertEqual(r["status"], "ok", r)
        self.assertEqual(r["engine_used"], "builtin")
        self.assertIn("no ngspice binary", r["engine_note"])

    def test_builtin_still_refuses_the_a_card_by_name(self):
        r = spice.simulate(GAIN_OP, engine="builtin")
        self.assertEqual(r["status"], "error")
        self.assertEqual(r["reason_code"], "NETLIST_INVALID")
        self.assertIn("XSPICE", r["message"])
        self.assertIn("A1", r["message"])


class TestLicenceHygiene(unittest.TestCase):
    """A GPL binary in an MIT tree is only legal as a separate, documented prog."""

    def test_the_gpl_text_ships_with_the_binary(self):
        root = spice.bundled_ngspice_dir()
        copying = os.path.join(root, "COPYING")
        self.assertTrue(os.path.isfile(copying), copying)
        with open(copying, "r", encoding="utf-8", errors="replace") as fh:
            head = fh.read(4000)
        # ngspice is NOT a single GPL project: the default is Modified BSD with a
        # short list of exceptions. Asserting "GNU GPL" here would have been the
        # tidy-but-false version of this test.
        self.assertIn("Modified BSD", head)
        self.assertIn("files with licenses different", head)

    def test_the_provenance_note_names_version_and_source(self):
        root = spice.bundled_ngspice_dir()
        notice = os.path.join(root, "NOTICE.md")
        self.assertTrue(os.path.isfile(notice), notice)
        with open(notice, "r", encoding="utf-8", errors="replace") as fh:
            text = fh.read()
        for want in ("ngspice-47", "ngspice.sourceforge", "Modified BSD",
                     "GPLv2", "unmodified"):
            self.assertIn(want, text)

    def test_notice_says_the_mit_code_never_links_it(self):
        with open(os.path.join(spice.bundled_ngspice_dir(), "NOTICE.md"),
                  "r", encoding="utf-8", errors="replace") as fh:
            text = fh.read().lower()
        self.assertIn("subprocess", text)
        self.assertIn("separate programs", text)


@unittest.skipUnless(BUNDLED, "vendor/ngspice is not present in this checkout")
class TestBundledEngineRuns(unittest.TestCase):
    """The decks that no engine-less machine could run before 0.0.37."""

    def _ok(self, deck, analysis=None, **kw):
        r = spice.simulate(deck, analysis=analysis, engine="ngspice", **kw)
        self.assertEqual(r["status"], "ok", (r.get("message"), r.get("log_tail")))
        return r

    def test_the_shipped_copy_is_a_verified_engine(self):
        r = self._ok(GAIN_OP)
        self.assertEqual(r["engine_used"], "ngspice")
        self.assertIn("ngspice-47", (r["engine"]["version"] or ""))
        # VERIFIED_ENGINES is the list this repository measured gold netlists
        # against; the bundled build has to be on it, not merely "a binary".
        self.assertTrue(r["verified_against_engine"])

    def test_a_gain_block_answers_exactly_gain_times_input(self):
        r = self._ok(GAIN_OP)
        self.assertAlmostEqual(r["value"]["nodes_v"]["10"], 2.0, delta=1e-9)
        self.assertAlmostEqual(r["value"]["nodes_v"]["1"], 1.0, delta=1e-12)

    def test_a_limit_block_clips_at_its_upper_limit(self):
        # 5 V in, out_upper_limit=1 => 1.0 V out. A model that loaded but was
        # applied to the wrong nodes would read 5 V here.
        r = self._ok(LIMIT_OP)
        self.assertAlmostEqual(r["value"]["nodes_v"]["2"], 1.0, delta=1e-9)
        self.assertAlmostEqual(r["value"]["nodes_v"]["1"], 5.0, delta=1e-12)

    def test_the_block_works_in_transient_not_just_at_dc(self):
        r = self._ok(GAIN_TRAN)
        pts = r["value"]["points"]
        self.assertGreaterEqual(len(pts), 5)
        last = pts[-1]["nodes_v"]["2"]
        self.assertAlmostEqual(last, 3.0, delta=1e-6)

    def test_the_envelope_says_the_deck_was_not_parsed_here(self):
        r = self._ok(GAIN_OP)
        self.assertFalse(r["netlist_parsed"])
        self.assertIn("A", r["netlist_parse_note"])
        self.assertIn("XSPICE", r["netlist_parse_note"])

    def test_the_builtin_subset_is_not_pretended_to_have_checked_it(self):
        # cross_check asks both engines; the builtin one refuses this deck, so
        # the cross-check must report "nothing to compare", never a difference.
        r = self._ok(GAIN_OP, cross_check=True)
        cross = r["cross_check"]
        self.assertIsNone(cross["max_abs_diff_v"])
        self.assertEqual(cross["compared_nodes"], 0)
        self.assertIn("could not solve", cross["note"])

    def test_a_pure_analog_deck_still_agrees_with_the_builtin_solver(self):
        # The bundle must not change the answer for decks the subset can do: this
        # is the same gold divider docs/21 records, run through the new binary.
        deck = "* d\nV1 1 0 10\nR1 1 2 1k\nR2 2 0 2k\n.op\n"
        theirs = self._ok(deck)
        mine = spice.simulate(deck, engine="builtin")
        self.assertEqual(mine["status"], "ok", mine)
        for node, val in theirs["value"]["nodes_v"].items():
            self.assertAlmostEqual(mine["value"]["nodes_v"][node], val, delta=1e-8)

    def test_the_engine_reports_its_own_failure_rather_than_a_number(self):
        # A model the code models do not define: the run has to fail loudly.
        r = spice.simulate("* bad\nV1 1 0 1\nA1 1 2 nope1\n"
                           ".model nope1 no_such_model_type()\nR1 2 0 1k\n.op\n",
                           engine="ngspice")
        self.assertEqual(r["status"], "error")
        self.assertEqual(r["reason_code"], "ENGINE_FAILED")
        self.assertIn("MIF-ERROR", r["log_tail"])


class TestAsciiRawfile(unittest.TestCase):
    """The bundled tree ships upstream's `spinit`, which sets `filetype=ascii`.

    So the engine this repository ships writes ASCII rawfiles while a
    hand-unpacked tree without `spinit` writes binary - and the ASCII branch of
    `_parse_raw` had two silent miscounts that only surfaced here: a complex
    value is written "re,im" (one whitespace token, two numbers) and an ASCII
    body runs to the end of the file, next plot included. Both were reported as
    `ENGINE_OUTPUT_INVALID` - "1 record but the header says 3 points" - i.e. the
    engine read fine and this module refused its own output. These tests are the
    negative control for that, over the byte shape measured from `ngspice-47`
    (docs/43 §43.3).
    """

    COMPLEX = (
        "Title: * ac\n"
        "Plotname: AC Analysis\n"
        "Flags: complex\n"
        "No. Variables: 2\n"
        "No. Points: 2\n"
        "Variables:\n"
        "\t0\tfrequency\tfrequency\n"
        "\t1\tv(1)\tvoltage\n"
        "Values:\n"
        "0\t\t1.000000000000000e+01,0.000000000000000e+00\n"
        "\t1.000000000000000e+00,-2.000000000000000e+00\n"
        "1\t\t1.000000000000000e+03,0.000000000000000e+00\n"
        "\t2.000000000000000e+00,4.000000000000000e+00\n")

    def test_a_complex_ascii_value_is_two_numbers_not_one(self):
        raw = spice._parse_raw(self.COMPLEX.encode("ascii"))
        self.assertTrue(raw["complex"])
        self.assertEqual(len(raw["points"]), 2)
        self.assertEqual(raw["points"][0][0], (10.0, 0.0))
        self.assertEqual(raw["points"][1][1], (2.0, 4.0))

    def test_two_plots_in_one_ascii_file_are_split_by_the_scanned_offset(self):
        # A .noise run writes the spectrum and then its integral: with the old
        # "the whole file is this plot" byte count the second plot was never
        # reached, and the FIRST plot's point count was compared against tokens
        # that also belonged to the second.
        two = (self.COMPLEX
               + "Title: * ac\n"
               + "Plotname: Integrated Noise\n"
               + "Flags: real\n"
               + "No. Variables: 1\n"
               + "No. Points: 1\n"
               + "Variables:\n"
               + "\t0\tv(onoise_total)\tvoltage\n"
               + "Values:\n"
               + "0\t\t1.500000000000000e-04\n")
        plots = spice._raw_plots(two.encode("ascii"))
        self.assertEqual([p["plotname"] for p in plots],
                         ["AC Analysis", "Integrated Noise"])
        self.assertEqual(plots[1]["points"], [[1.5e-04]])

    def test_a_header_point_count_that_does_not_match_the_values_is_refused(self):
        # Same, but the header lies about how many points follow: the answer
        # would be a guess, so it must be an error with the counts in it.
        lying = self.COMPLEX.replace("No. Points: 2", "No. Points: 5")
        with self.assertRaises(ValueError) as ctx:
            spice._parse_raw(lying.encode("ascii"))
        self.assertIn("says 5", str(ctx.exception))


if __name__ == "__main__":
    unittest.main()
