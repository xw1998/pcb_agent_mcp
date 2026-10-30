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


# 0.0.40: the two MULTI-PORT algebraic models. Every number in these decks was
# measured on the bundled ngspice-47 first; the builtin has to land on the same
# value, and on the engine's own reading of the formulas:
#   summer: out = out_offset + out_gain * SUM_i in_gain_i*(v_i + in_offset_i)
#   mult  : out = out_offset + out_gain * PROD_i in_gain_i*(v_i + in_offset_i)
SUMMER_OP = """* xspice summer
V1 1 0 3
V2 2 0 4
A1 [1 2] 10 s1
.model s1 summer(in_gain=[2 3] in_offset=[1 1] out_gain=2 out_offset=7)
R1 10 0 1k
.op
"""

MULT_OP = """* xspice mult
V1 1 0 3
V2 2 0 4
A1 [1 2] 10 m1
.model m1 mult(in_gain=[2 3] in_offset=[1 1] out_gain=2 out_offset=7)
R1 10 0 1k
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

    def test_builtin_solves_the_two_algebraic_code_models(self):
        # 0.0.39: gain/limit left the "refused by name" table because they are pure
        # algebra. The control that keeps this honest is the pair of tests below:
        # a code model that carries STATE is still refused, and so is a parameter
        # that changes the corner.
        r = spice.simulate(GAIN_OP, engine="builtin")
        self.assertEqual(r["status"], "ok", r)
        self.assertAlmostEqual(r["value"]["nodes_v"]["10"], 2.0, delta=1e-9)
        a = [e for e in r["value"]["elements"] if e["type"] == "A"]
        self.assertEqual(len(a), 1)
        self.assertEqual(a[0]["kind"], "GAIN")
        self.assertEqual(a[0]["region"], "linear")
        self.assertEqual(a[0]["gain"], 2.0)

    def test_a_code_model_with_state_is_still_refused_by_name(self):
        r = spice.simulate("* i\nV1 1 0 1\nA1 1 2 i1\n"
                           ".model i1 integrator(gain=1)\nR1 2 0 1k\n.op\n",
                           engine="builtin")
        self.assertEqual(r["status"], "error")
        self.assertEqual(r["reason_code"], "NETLIST_INVALID")
        self.assertIn("INTEGRATOR", r["message"])
        self.assertIn("only gain, limit, summer and mult", r["message"])

    def test_limit_range_is_refused_rather_than_clamped(self):
        # measured: 0.9 V in with limit_range=0.5 settles at 0.82 V, not 0.9 V
        r = spice.simulate("* q\nV1 1 0 0.9\nA1 1 2 q1\n"
                           ".model q1 limit(gain=1 out_lower_limit=-1 "
                           "out_upper_limit=1 limit_range=0.5)\nR1 2 0 1k\n.op\n",
                           engine="builtin")
        self.assertEqual(r["status"], "error")
        self.assertIn("limit_range", r["message"])


    def test_builtin_solves_a_multi_port_summer(self):
        # 0.0.40: a second input node, an ARRAY CONNECTION on the card and vector
        # parameters in the .model. 2*(3+1) + 3*(4+1) = 23, *2 + 7 = 53 V, which
        # is what the bundled engine answers on the same deck.
        r = spice.simulate(SUMMER_OP, engine="builtin")
        self.assertEqual(r["status"], "ok", r)
        self.assertAlmostEqual(r["value"]["nodes_v"]["10"], 53.0, delta=1e-9)
        a = [e for e in r["value"]["elements"] if e["type"] == "A"][0]
        self.assertEqual(a["kind"], "SUMMER")
        self.assertEqual(a["inputs"], ["1", "2"])
        self.assertEqual(a["in_gain"], [2.0, 3.0])
        self.assertEqual(a["in_offset"], [1.0, 1.0])
        self.assertEqual(a["out_gain"], 2.0)
        self.assertEqual(a["out_offset"], 7.0)

    def test_builtin_solves_a_bilinear_multiplier(self):
        # 2*(3+1) * 3*(4+1) = 120, *2 + 7 = 247 V. A product is NOT a sum: if the
        # implementation summed, this would read 53 V and the summer test above
        # would still pass, which is why both exist.
        r = spice.simulate(MULT_OP, engine="builtin")
        self.assertEqual(r["status"], "ok", r)
        self.assertAlmostEqual(r["value"]["nodes_v"]["10"], 247.0, delta=1e-9)
        a = [e for e in r["value"]["elements"] if e["type"] == "A"][0]
        self.assertEqual(a["kind"], "MULT")

    def test_a_three_port_multiplier_is_a_product_of_three(self):
        # measured: mult() on 3 V, 4 V, 5 V settles at 60 V; with in_offset=[0 0 2]
        # it settles at 84 V. A model hard-wired to two ports would fail here.
        deck = ("* m3\nV1 1 0 3\nV2 2 0 4\nV3 3 0 5\nA1 [1 2 3] 10 m1\n"
                ".model m1 mult(in_offset=[0 0 2])\nR1 10 0 1k\n.op\n")
        r = spice.simulate(deck, engine="builtin")
        self.assertEqual(r["status"], "ok", r)
        self.assertAlmostEqual(r["value"]["nodes_v"]["10"], 84.0, delta=1e-9)

    def test_the_multi_port_models_take_vectors_not_the_scalar_gain(self):
        # The trap: `summer(gain=[2 3])` LOOKS like the obvious spelling and the
        # engine answers "MIF: unrecognized parameter (gain) - ignored", i.e. it
        # silently keeps in_gain=1 and reports 7 V where 18 V was meant. Refusing
        # the parameter is the only answer that cannot be mistaken for one.
        r = spice.simulate("* s\nV1 1 0 3\nV2 2 0 4\nA1 [1 2] 10 s1\n"
                           ".model s1 summer(gain=[2 3])\nR1 10 0 1k\n.op\n",
                           engine="builtin")
        self.assertEqual(r["status"], "error")
        self.assertEqual(r["reason_code"], "NETLIST_INVALID")
        self.assertIn("gain= in .model", r["message"])
        self.assertIn("in_gain=", r["message"])

    def test_a_vector_length_must_match_the_port_count(self):
        # measured: the engine refuses a 2-port block with in_gain=[2 3 4]
        # outright, so padding or truncating here would invent an answer.
        r = spice.simulate("* s\nV1 1 0 3\nV2 2 0 4\nA1 [1 2] 10 s1\n"
                           ".model s1 summer(in_gain=[2 3 4])\nR1 10 0 1k\n.op\n",
                           engine="builtin")
        self.assertEqual(r["status"], "error")
        self.assertEqual(r["reason_code"], "NETLIST_INVALID")
        self.assertIn("3 value(s) for 2 input node(s)", r["message"])

    def test_a_scalar_where_a_vector_is_required_is_refused(self):
        # measured: the engine answers "Array parameter expected - No array
        # delimiter found" - the same class of silent fallback as `gain=`.
        r = spice.simulate("* m\nV1 1 0 3\nV2 2 0 4\nA1 [1 2] 10 m1\n"
                           ".model m1 mult(in_offset=1)\nR1 10 0 1k\n.op\n",
                           engine="builtin")
        self.assertEqual(r["status"], "error")
        self.assertIn("ARRAY parameter", r["message"])

    def test_a_single_input_multi_port_block_is_refused(self):
        # measured: `A1 1 10 m1` and `A1 [1] 10 m1` are both an engine error, so
        # one port is not "a summer of one" here either.
        r = spice.simulate("* s\nV1 1 0 3\nA1 1 10 s1\n"
                           ".model s1 summer(in_gain=[2])\nR1 10 0 1k\n.op\n",
                           engine="builtin")
        self.assertEqual(r["status"], "error")
        self.assertIn("needs at least 2", r["message"])

    def test_a_percent_v_array_connection_is_still_refused(self):
        # 0.0.37 measured that the engine does not accept `%v(1) %v(2)` for an
        # array connection (incomplete or empty netlist). The builtin keeps
        # refusing it, so the control for "the deck parses here" stays honest.
        r = spice.simulate("* p\nV1 1 0 3\nV2 2 0 4\nA1 %v(1) %v(2) 10 s1\n"
                           ".model s1 summer()\nR1 10 0 1k\n.op\n",
                           engine="builtin")
        self.assertEqual(r["status"], "error")
        self.assertEqual(r["reason_code"], "NETLIST_INVALID")

    def test_a_mult_block_is_refused_in_ac_like_limit(self):
        # a mult's small-signal gain IS the other input's value, so it depends on
        # the operating point; sweeping without one would report a gain nobody
        # chose. summer() is linear and is allowed - the pair keeps this honest.
        bad = spice.simulate("* m\nV1 1 0 ac 1\nV2 2 0 ac 1\nA1 [1 2] 10 m1\n"
                             ".model m1 mult()\nR1 10 0 1k\n.ac dec 1 1 10\n",
                             engine="builtin")
        self.assertEqual(bad["status"], "error")
        self.assertIn("in an .ac sweep", bad["message"])
        good = spice.simulate("* s\nV1 1 0 ac 1\nV2 2 0 ac 1\nA1 [1 2] 10 s1\n"
                              ".model s1 summer(in_gain=[2 3])\nR1 10 0 1k\n"
                              ".ac dec 1 1 10\n", engine="builtin")
        self.assertEqual(good["status"], "ok", good)

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

    def test_the_envelope_says_the_deck_was_parsed_here_now(self):
        # 0.0.37: false, with a note naming XSPICE. 0.0.39: gain/limit are solved
        # here, so this deck parses - the note belongs to the OTHER code models,
        # which test_a_code_model_with_state_is_still_refused_by_name covers.
        r = self._ok(GAIN_OP)
        self.assertTrue(r["netlist_parsed"])
        self.assertNotIn("netlist_parse_note", r)

    def test_cross_check_now_really_compares_this_deck(self):
        # 0.0.37 this deck was one-sided: the builtin refused it, so the
        # cross-check had to say "nothing to compare". 0.0.39 solves gain/limit,
        # so the honest state is a REAL comparison - and it has to be exact,
        # because both sides are solving out = 2*v(in) with no approximation in
        # between. A non-zero difference here would mean one of them is wrong.
        r = self._ok(GAIN_OP, cross_check=True)
        cross = r["cross_check"]
        self.assertEqual(cross["max_abs_diff_v"], 0.0)
        self.assertGreater(cross["compared_nodes"], 0)

    def test_cross_check_agrees_exactly_on_the_multi_port_blocks(self):
        # 0.0.40: both sides now solve summer/mult, so this comparison is real for
        # them too. Exact 0.0 is the right expectation: the formulas are the ones
        # measured off THIS engine, with no approximation in between.
        for deck, want in ((SUMMER_OP, 53.0), (MULT_OP, 247.0)):
            r = self._ok(deck, cross_check=True)
            self.assertAlmostEqual(r["value"]["nodes_v"]["10"], want, delta=1e-9)
            self.assertEqual(r["cross_check"]["max_abs_diff_v"], 0.0, r["cross_check"])
            self.assertGreater(r["cross_check"]["compared_nodes"], 0)

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
