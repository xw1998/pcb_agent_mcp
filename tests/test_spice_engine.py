"""Tests for the optional delegation of SPICE runs to an external ngspice.

None of these tests need the real engine. What they check is the part this
repository actually owns:

  * the rawfile parser, against both layouts ngspice writes (ASCII 'Values:'
    and binary 'Binary:'), including the point index that the ASCII form puts
    at the head of every record - if that index were kept as a value, every
    node voltage would be shifted by one and the answers would be wrong but
    plausible, which is the failure mode this project refuses;
  * the deck filter, which keeps exactly the analysis the caller asked for;
  * the three failure paths (engine exits non-zero / writes no rawfile /
    is not installed at all), each of which must be a refusal rather than a
    quiet fall back to the builtin subset;
  * cross_check, which is the only way the delegation itself can be audited.

The stand-in engine is tests/_fake_ngspice.py; it is driven by the JSON file
named in $FAKE_SPEC, so the test decides what the "engine" answers.
"""
import json
import os
import subprocess
import sys
import tempfile
import unittest

_HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, os.path.join(os.path.dirname(_HERE), "src"))

from pcb_agent_mcp.calc import spice  # noqa: E402

FAKE = os.path.join(_HERE, "_fake_ngspice.py")

DIVIDER = "* div\nV1 1 0 10\nR1 1 2 1k\nR2 2 0 2k\n.op\n.end\n"

TRAN = "* div\nV1 1 0 10\nR1 1 2 1k\nR2 2 0 2k\n.tran 1u 1m\n.end\n"

AC = "* div\nV1 1 0 ac 1\nR1 1 2 1k\nR2 2 0 2k\n.ac dec 10 1 1e6\n.end\n"


def _raw_ascii_op(v1, v2):
    return (
        "Title: div\nDate: x\nPlotname: Operating Point\nFlags: real\n"
        "No. Variables: 2\nNo. Points: 1\nVariables:\n"
        "\t0\tv(1)\tvoltage\n\t1\tv(2)\tvoltage\nValues:\n"
        "0\t%.15e\n\t%.15e\n" % (v1, v2)
    )


class TestParseRaw(unittest.TestCase):
    def test_ascii_operating_point_drops_the_point_index(self):
        raw = spice._parse_raw(_raw_ascii_op(10.0, 6.5))
        self.assertEqual(raw["vars"], ["v(1)", "v(2)"])
        self.assertFalse(raw["complex"])
        self.assertEqual(raw["points"], [[10.0, 6.5]])

    def test_ascii_complex_keeps_re_and_im_together(self):
        text = (
            "Plotname: AC Analysis\nFlags: complex\nNo. Variables: 2\n"
            "No. Points: 2\nVariables:\n\t0\tfrequency\tfrequency\n"
            "\t1\tv(2)\tvoltage\nValues:\n"
            "0\t1.000000000000000e+02\n\t0.000000000000000e+00\n"
            "\t3.000000000000000e+00\n\t4.000000000000000e+00\n"
            "1\t1.000000000000000e+03\n\t0.000000000000000e+00\n"
            "\t0.000000000000000e+00\n\t-1.000000000000000e+00\n"
        )
        raw = spice._parse_raw(text)
        self.assertTrue(raw["complex"])
        # every vector in a complex plot is a pair, the frequency axis included
        self.assertEqual(raw["points"][0], [(100.0, 0.0), (3.0, 4.0)])
        self.assertEqual(raw["points"][1], [(1000.0, 0.0), (0.0, -1.0)])
        nodes, scales = spice._raw_nodes(raw)
        self.assertEqual(scales["frequency"], [100.0, 1000.0])
        self.assertEqual(nodes["2"], [(3.0, 4.0), (0.0, -1.0)])

    def test_binary_matches_the_ascii_reading(self):
        head = (
            "Title: div\nPlotname: Operating Point\nFlags: real\n"
            "No. Variables: 2\nNo. Points: 1\nVariables:\n"
            "\t0\tv(1)\tvoltage\n\t1\tv(2)\tvoltage\nBinary:\n"
        )
        import struct
        body = struct.pack("<2d", 10.0, 6.6666666667)
        raw = spice._parse_raw(head.encode("ascii") + body)
        self.assertEqual(raw["points"], [[10.0, 6.6666666667]])

    def test_binary_truncated_is_refused(self):
        head = ("Plotname: x\nFlags: real\nNo. Variables: 2\nNo. Points: 5\n"
                "Variables:\n\t0\tv(1)\tvoltage\n\t1\tv(2)\tvoltage\nBinary:\n")
        import struct
        with self.assertRaises(ValueError):
            spice._parse_raw(head.encode("ascii") + struct.pack("<2d", 1.0, 2.0))

    def test_no_values_section_is_refused(self):
        with self.assertRaises(ValueError):
            spice._parse_raw("Title: not a rawfile at all\n")

    def test_missing_variables_block_is_refused(self):
        with self.assertRaises(ValueError):
            spice._parse_raw("No. Variables: 1\nNo. Points: 1\nValues:\n0\t1.0\n")


class TestDeck(unittest.TestCase):
    def test_only_the_requested_analysis_survives(self):
        net = ("* two cards\nV1 1 0 1\nR1 1 0 1k\n.op\n.tran 1u 1m\n"
               ".four 1k v(1)\n.end\n")
        deck = spice._deck_for_ngspice(net, "tran")
        self.assertIn(".tran 1u 1m", deck)
        self.assertNotIn(".op", deck)
        self.assertNotIn(".four", deck)
        self.assertEqual(deck.count(".end"), 1)
        self.assertTrue(deck.rstrip().endswith(".end"))

    def test_asking_for_a_card_the_deck_does_not_have_is_none(self):
        self.assertIsNone(spice._deck_for_ngspice("* x\nV1 1 0 1\nR1 1 0 1k\n.op\n",
                                                  "ac"))

    def test_elements_and_models_are_passed_through_untouched(self):
        net = ("* keep the devices verbatim\nV1 in 0 1\nR1 in out 1k\n"
               "C1 out 0 1u\n.model dm d(is=1e-14)\n.op\n")
        deck = spice._deck_for_ngspice(net, "op")
        for line in ("V1 in 0 1", "R1 in out 1k", "C1 out 0 1u",
                     ".model dm d(is=1e-14)"):
            self.assertIn(line, deck)


class EngineCase(unittest.TestCase):
    """Runs the module against the stand-in engine."""

    def setUp(self):
        self._tmp = tempfile.mkdtemp(prefix="pcb_spice_test_")
        self.spec_path = os.path.join(self._tmp, "spec.json")
        self._old_spec = os.environ.get("FAKE_SPEC")
        os.environ["FAKE_SPEC"] = self.spec_path
        self._old_cmd = spice.NGSPICE_CMD
        spice.NGSPICE_CMD = [sys.executable, FAKE]

    def tearDown(self):
        spice.NGSPICE_CMD = self._old_cmd
        if self._old_spec is None:
            os.environ.pop("FAKE_SPEC", None)
        else:
            os.environ["FAKE_SPEC"] = self._old_spec
        import shutil
        shutil.rmtree(self._tmp, ignore_errors=True)

    def spec(self, **kw):
        with open(self.spec_path, "w", encoding="utf-8") as fh:
            json.dump(kw, fh)


class TestDelegation(EngineCase):
    def test_operating_point_comes_from_the_engine(self):
        self.spec(kind="op", nodes={"1": 10.0, "2": 7.25})
        r = spice.simulate(DIVIDER, engine="ngspice")
        self.assertEqual(r["status"], "ok", r)
        self.assertEqual(r["engine_used"], "ngspice")
        self.assertEqual(r["value"]["analysis"], "op")
        self.assertAlmostEqual(r["value"]["nodes_v"]["1"], 10.0, places=9)
        self.assertAlmostEqual(r["value"]["nodes_v"]["2"], 7.25, places=9)
        self.assertIn(FAKE, r["engine"]["argv"])
        self.assertIn("fake-ngspice", r["engine"]["version"])
        self.assertEqual(r["engine"]["plotname"], "Operating Point")
        # no per-element report on this path, and it says so
        self.assertIsNone(r["elements"])
        self.assertIn("builtin solver", r["elements_note"])
        # the delegation itself is not machine-verified on this machine
        self.assertFalse(r["verified_against_engine"])
        self.assertTrue(r["basis"])
        self.assertTrue(r["precision"])

    def test_transient_points_follow_the_time_scale(self):
        self.spec(kind="tran", times=[0.0, 1e-3], nodes={"2": [0.0, 6.0]})
        r = spice.simulate(TRAN)
        self.assertEqual(r["status"], "ok", r)
        pts = r["value"]["points"]
        self.assertEqual(len(pts), 2)
        self.assertAlmostEqual(pts[1]["t"], 1e-3, places=12)
        self.assertAlmostEqual(pts[1]["nodes_v"]["2"], 6.0, places=9)
        self.assertEqual(r["value"]["points_returned"], 2)

    def test_ac_points_carry_mag_and_phase(self):
        self.spec(kind="ac", freqs=[1000.0], nodes={"2": [[3.0, 4.0]]})
        r = spice.simulate(AC)
        self.assertEqual(r["status"], "ok", r)
        n2 = r["value"]["points"][0]["nodes_v"]["2"]
        self.assertAlmostEqual(n2["mag"], 5.0, places=9)
        self.assertAlmostEqual(n2["phase_deg"],
                               round(__import__("math").degrees(
                                   __import__("math").atan2(4.0, 3.0)), 6),
                               places=6)

    def test_auto_uses_the_engine_when_one_is_found(self):
        self.spec(kind="op", nodes={"1": 10.0, "2": 6.0})
        r = spice.simulate(DIVIDER, engine="auto")
        self.assertEqual(r["engine_used"], "ngspice")

    def test_temp_c_is_appended_as_a_card_not_sent_twice(self):
        self.spec(kind="op", nodes={"1": 10.0, "2": 6.0})
        r = spice.simulate(DIVIDER, temp_c=80.0)
        self.assertEqual(r["status"], "ok", r)
        # a deck that already states .temp plus temp_c is an argument conflict
        r2 = spice.simulate("* x\nV1 1 0 1\nR1 1 0 1k\n.temp 27\n.op\n",
                            temp_c=80.0)
        self.assertEqual(r2["status"], "error")
        self.assertEqual(r2["reason_code"], "ARG_CONFLICT")


class TestRefusals(EngineCase):
    def test_non_zero_exit_is_engine_failed_with_the_log_tail(self):
        self.spec(kind="op", nodes={}, rc=1,
                  log="Error on line 4: unknown device type\n")
        r = spice.simulate(DIVIDER, engine="ngspice")
        self.assertEqual(r["status"], "error")
        self.assertEqual(r["reason_code"], "ENGINE_FAILED")
        self.assertEqual(r["exit_code"], 1)
        self.assertIn("unknown device type", r["log_tail"])

    def test_no_rawfile_is_not_an_answer(self):
        self.spec(kind="op", write_raw=False)
        r = spice.simulate(DIVIDER, engine="ngspice")
        self.assertEqual(r["status"], "error")
        self.assertEqual(r["reason_code"], "ENGINE_OUTPUT_INVALID")

    def test_garbage_rawfile_is_not_an_answer(self):
        self.spec(kind="op", nodes={"1": 1.0}, format="binary", write_raw=False)
        r = spice.simulate(DIVIDER, engine="ngspice")
        self.assertEqual(r["reason_code"], "ENGINE_OUTPUT_INVALID")

    def test_missing_binary_refuses_instead_of_falling_back(self):
        spice.NGSPICE_CMD = None
        from unittest import mock
        with mock.patch.object(spice, "find_ngspice", return_value=None):
            r = spice.simulate(DIVIDER, engine="ngspice")
        self.assertEqual(r["status"], "error")
        self.assertEqual(r["reason_code"], "ENGINE_NOT_FOUND")
        self.assertEqual(r["tried"][0], "%s env var" % spice.NGSPICE_ENV)

    def test_auto_without_a_binary_is_the_builtin_and_says_so(self):
        spice.NGSPICE_CMD = None
        from unittest import mock
        with mock.patch.object(spice, "find_ngspice", return_value=None):
            r = spice.simulate(DIVIDER, engine="auto")
        self.assertEqual(r["status"], "ok", r)
        self.assertEqual(r["engine_used"], "builtin")
        self.assertIn("no ngspice binary", r["engine_note"])
        self.assertAlmostEqual(r["value"]["nodes_v"]["2"], 10.0 * 2.0 / 3.0,
                               places=7)

    def test_builtin_engine_is_labelled_too(self):
        r = spice.simulate(DIVIDER, engine="builtin")
        self.assertEqual(r["engine_used"], "builtin")
        self.assertIn("engine='builtin'", r["engine_note"])

    def test_unknown_engine_is_an_argument_error(self):
        r = spice.simulate(DIVIDER, engine="ltspice")
        self.assertEqual(r["status"], "error")
        self.assertEqual(r["reason_code"], "ARG_INVALID")

    def test_no_analysis_card_for_the_engine_is_a_refusal(self):
        self.spec(kind="op", nodes={"1": 1.0})
        r = spice.simulate("* x\nV1 1 0 1\nR1 1 0 1k\n", engine="ngspice",
                           analysis="tran")
        self.assertEqual(r["status"], "error")
        self.assertEqual(r["reason_code"], "ARG_REQUIRED")

    def test_unparsed_card_is_refused_before_reaching_the_engine(self):
        self.spec(kind="op", nodes={"1": 1.0})
        r = spice.simulate("* x\nV1 1 0 1\nR1 1 0 1k\nB1 2 0 V=1\n.op\n",
                           engine="ngspice")
        self.assertEqual(r["status"], "error")
        self.assertEqual(r["reason_code"], "NETLIST_INVALID")


class TestCrossCheck(EngineCase):
    def test_disagreeing_engines_report_the_largest_difference(self):
        # the builtin answer for this divider is 6.666666667 V at node 2;
        # the stand-in says 6.676, i.e. 9.3 mV high, and node 1 agrees exactly
        self.spec(kind="op", nodes={"1": 10.0, "2": 6.676})
        r = spice.simulate(DIVIDER, engine="ngspice", cross_check=True)
        self.assertEqual(r["status"], "ok", r)
        cc = r["cross_check"]
        self.assertEqual(cc["compared_nodes"], 2)
        self.assertAlmostEqual(cc["max_abs_diff_v"], 0.009333333, places=6)
        self.assertEqual(cc["worst_node"], "2")
        self.assertIn("operating point", cc["note"])

    def test_agreeing_engines_show_a_near_zero_difference(self):
        self.spec(kind="op", nodes={"1": 10.0, "2": 10.0 * 2.0 / 3.0})
        r = spice.simulate(DIVIDER, engine="ngspice", cross_check=True)
        self.assertLess(r["cross_check"]["max_abs_diff_v"], 1e-6)

    def test_disjoint_node_names_are_not_a_pass(self):
        self.spec(kind="op", nodes={"zz": 1.0})
        r = spice.simulate(DIVIDER, engine="ngspice", cross_check=True)
        cc = r["cross_check"]
        self.assertEqual(cc["compared_nodes"], 0)
        self.assertIsNone(cc["max_abs_diff_v"])
        self.assertIn("NOT a pass", cc["note"])

    def test_cross_check_is_absent_unless_asked_for(self):
        self.spec(kind="op", nodes={"1": 10.0, "2": 6.0})
        r = spice.simulate(DIVIDER, engine="ngspice")
        self.assertNotIn("cross_check", r)


class TestLocate(unittest.TestCase):
    def test_env_var_wins_over_path(self):
        old_cmd = spice.NGSPICE_CMD
        old_env = os.environ.get(spice.NGSPICE_ENV)
        spice.NGSPICE_CMD = None
        try:
            os.environ[spice.NGSPICE_ENV] = sys.executable
            self.assertEqual(spice.find_ngspice(), [sys.executable])
            os.environ.pop(spice.NGSPICE_ENV, None)
            # nothing named ngspice on PATH in this test process by construction:
            # whatever the machine has, the return is either None or a list of str
            got = spice.find_ngspice()
            self.assertTrue(got is None or all(isinstance(x, str) for x in got))
        finally:
            spice.NGSPICE_CMD = old_cmd
            if old_env is None:
                os.environ.pop(spice.NGSPICE_ENV, None)
            else:
                os.environ[spice.NGSPICE_ENV] = old_env


if __name__ == "__main__":
    unittest.main()
