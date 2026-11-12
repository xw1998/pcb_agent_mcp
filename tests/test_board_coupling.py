"""Crosstalk between two nets, found on the board and solved once.

Two halves, deliberately separated because they fail differently:

* the SEARCH (`coupled_runs`, `reference_plane`) is pure geometry and is tested
  hard - parallel within an angle, edge-to-edge gap, longitudinal overlap - so a
  run that does not exist cannot be invented; a tool that reports crosstalk
  between traces that merely look near each other is worse than no tool;
* the SOLVE is the old 2-D field engine and is exercised once, with a coarse
  mesh, because a 0.15 mm pair costs minutes (measured: 285.8 s at mesh_div 8).
  What is asserted there is that it runs on the numbers the file gave, not that
  the physics is right - that is `test_crosstalk.py`'s job.
"""
import os
import sys
import tempfile
import unittest

sys.path.insert(0, os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "src"))

from pcb_agent_mcp.adapters import kicad_pcb as K  # noqa: E402
from pcb_agent_mcp.sim import couple as C  # noqa: E402

STACKUP_2 = """  (setup (stackup
      (layer "F.Cu" (type "copper") (thickness 0.035))
      (layer "dielectric 1" (type "core") (thickness 1.51) (epsilon_r 4.5)
             (loss_tangent 0.02))
      (layer "B.Cu" (type "copper") (thickness 0.035))))"""

STACKUP_4 = """  (setup (stackup
      (layer "F.Cu" (type "copper") (thickness 0.035))
      (layer "dielectric 1" (type "prepreg") (thickness 0.1) (epsilon_r 4.2))
      (layer "In1.Cu" (type "copper") (thickness 0.035))
      (layer "dielectric 2" (type "core") (thickness 1.2) (epsilon_r 4.6))
      (layer "In2.Cu" (type "copper") (thickness 0.035))
      (layer "dielectric 3" (type "prepreg") (thickness 0.1) (epsilon_r 4.2))
      (layer "B.Cu" (type "copper") (thickness 0.035))))"""


def _seg(x1, y1, x2, y2, w, net, layer="F.Cu"):
    return ('  (segment (start %s %s) (end %s %s) (width %s) (layer "%s") '
            '(net %s))' % (x1, y1, x2, y2, w, layer, net))


def _board(segs, four_layer=False):
    stack = STACKUP_4 if four_layer else STACKUP_2
    layers = ('(0 "F.Cu" signal) (4 "In1.Cu" signal) (6 "In2.Cu" signal) '
              '(31 "B.Cu" signal) (44 "Edge.Cuts" user)') if four_layer else \
             '(0 "F.Cu" signal) (31 "B.Cu" signal) (44 "Edge.Cuts" user)'
    text = ['(kicad_pcb (version 20221018) (generator pcbnew)',
            '  (general (thickness 1.6))',
            '  (layers %s)' % layers,
            stack,
            '  (net 0 "") (net 1 "A") (net 2 "B") (net 3 "GND")',
            '  (gr_rect (start 0 0) (end 20 10) (layer "Edge.Cuts") (width 0.1))']
    text.extend(segs)
    text.append(')')
    path = os.path.join(tempfile.gettempdir(),
                        "board_couple_%d_%d.kicad_pcb" % (os.getpid(), len(segs)))
    with open(path, "w", newline="") as fh:
        fh.write("\n".join(text) + "\n")
    return K.parse_board(path)


PAIR = [_seg(2, 4, 8, 4, 0.3, 1),
        _seg(2, 4.6, 8, 4.6, 0.3, 2),
        _seg(2, 8, 8, 8, 1.0, 3, "B.Cu")]


class TestTheSearchFindsRunsThatExist(unittest.TestCase):
    def test_a_parallel_run_is_one_run_with_its_own_gap(self):
        runs = C.coupled_runs(C._tracks(_board(PAIR), "A", "F.Cu"),
                              C._tracks(_board(PAIR), "B", "F.Cu"))
        self.assertEqual(len(runs), 1, runs)
        self.assertAlmostEqual(runs[0]["gap_mm"], 0.3, places=9)   # 0.6 - 0.3
        self.assertAlmostEqual(runs[0]["overlap_mm"], 6.0, places=9)
        self.assertAlmostEqual(runs[0]["angle_deg"], 0.0, places=6)

    def test_a_crossing_trace_is_not_a_run(self):
        b = _board([_seg(2, 4, 8, 4, 0.3, 1), _seg(5, 1, 5, 7, 0.3, 2)])
        self.assertEqual(C.coupled_runs(C._tracks(b, "A", "F.Cu"),
                                        C._tracks(b, "B", "F.Cu")), [])

    def test_a_gap_beyond_the_limit_is_not_a_run(self):
        b = _board([_seg(2, 4, 8, 4, 0.3, 1), _seg(2, 5.0, 8, 5.0, 0.3, 2)])
        self.assertEqual(C.coupled_runs(C._tracks(b, "A", "F.Cu"),
                                        C._tracks(b, "B", "F.Cu"),
                                        max_gap_mm=0.5), [])

    def test_a_short_overlap_is_not_a_run(self):
        b = _board([_seg(2, 4, 8, 4, 0.3, 1), _seg(7.5, 4.6, 9, 4.6, 0.3, 2)])
        self.assertEqual(C.coupled_runs(C._tracks(b, "A", "F.Cu"),
                                        C._tracks(b, "B", "F.Cu"),
                                        min_overlap_mm=2.0), [])

    def test_touching_copper_is_a_zero_gap_not_a_negative_one(self):
        b = _board([_seg(2, 4, 8, 4, 0.3, 1), _seg(2, 4.3, 8, 4.3, 0.3, 2)])
        runs = C.coupled_runs(C._tracks(b, "A", "F.Cu"),
                              C._tracks(b, "B", "F.Cu"))
        self.assertEqual(len(runs), 1)
        self.assertEqual(runs[0]["gap_mm"], 0.0)


class TestTheReferencePlaneComesFromTheStackup(unittest.TestCase):
    def test_an_outer_layer_is_a_microstrip(self):
        ref = C.reference_plane(_board(PAIR), "F.Cu")
        self.assertTrue(ref["outer"])
        self.assertEqual(ref["reference_below"], "B.Cu")
        self.assertIsNone(ref["reference_above"])
        self.assertAlmostEqual(ref["dielectric_mm"], 1.51, places=9)
        self.assertAlmostEqual(ref["er"], 4.5, places=9)

    def test_an_inner_layer_is_a_stripline_and_takes_the_nearer_side(self):
        ref = C.reference_plane(_board(PAIR, four_layer=True), "In1.Cu")
        self.assertFalse(ref["outer"])
        self.assertEqual(ref["nearest"], "above")     # 0.1 mm to F.Cu, 1.2 to In2
        self.assertAlmostEqual(ref["dielectric_mm"], 0.1, places=9)
        self.assertAlmostEqual(ref["er"], 4.2, places=9)
        self.assertAlmostEqual(ref["both_sides"]["below"]["dielectric_mm"], 1.2,
                               places=9)


class TestWhatIsRefused(unittest.TestCase):
    def test_a_rise_time_is_required(self):
        out = C.board_crosstalk(_board(PAIR), {"net_a": "A", "net_b": "B"})
        self.assertEqual(out["status"], "error", out)
        self.assertEqual(out["reason_code"], "ARG_MISSING")
        self.assertIn("rise_time_ns", out["message"])

    def test_a_net_does_not_couple_to_itself(self):
        out = C.board_crosstalk(_board(PAIR), {"net_a": "A", "net_b": "A",
                                               "rise_time_ns": 0.5})
        self.assertEqual(out["reason_code"], "ARG_INVALID")

    def test_nets_that_never_share_a_layer_are_refused(self):
        b = _board([_seg(2, 4, 8, 4, 0.3, 1, "F.Cu"),
                    _seg(2, 4.6, 8, 4.6, 0.3, 2, "B.Cu")])
        out = C.board_crosstalk(b, {"net_a": "A", "net_b": "B",
                                    "rise_time_ns": 0.5})
        self.assertEqual(out["reason_code"], "NOT_APPLICABLE")
        self.assertIn("share a copper layer", out["message"])

    def test_no_qualifying_run_reports_the_closest_one_instead(self):
        b = _board([_seg(2, 4, 8, 4, 0.3, 1), _seg(2, 6, 8, 6, 0.3, 2)])
        out = C.board_crosstalk(b, {"net_a": "A", "net_b": "B",
                                    "rise_time_ns": 0.5, "max_gap_mm": 0.5})
        self.assertEqual(out["reason_code"], "NOT_APPLICABLE")
        near = out["extra"]["closest_parallel"]
        self.assertEqual(len(near), 1)
        self.assertAlmostEqual(near[0]["gap_mm"], 1.7, places=9)

    def test_two_different_widths_are_refused_because_the_solver_has_one(self):
        b = _board([_seg(2, 4, 8, 4, 0.3, 1), _seg(2, 4.5, 8, 4.5, 0.6, 2)])
        out = C.board_crosstalk(b, {"net_a": "A", "net_b": "B",
                                    "rise_time_ns": 0.5})
        self.assertEqual(out["reason_code"], "ARG_INVALID")
        self.assertIn("ONE width", out["message"])


THREE = [_seg(2, 2, 8, 2, 0.3, 1),
         _seg(2, 5, 8, 5, 0.3, 1),
         _seg(2, 8, 8, 8, 0.3, 1),
         _seg(2, 2.6, 8, 2.6, 0.3, 2),      # 0.6 c-c, 0.3 gap, same width -> solves
         _seg(2, 8.7, 8, 8.7, 0.3, 2),      # 0.7 c-c, 0.4 gap, same width -> solves
         _seg(2, 5.9, 8, 5.9, 0.6, 2)]      # 0.9 c-c, 0.45 gap, 0.6 vs 0.3 -> refused


class TestHowManyRunsGetSolved(unittest.TestCase):
    """The orchestration around the field solve, with the solve stubbed out.

    A real solve is minutes (see TestOneRealSolve), so what is pinned here is
    which runs are handed to the solver and what the report says about the ones
    that are not - not the physics, which is the solver's own test.
    """

    def _patched(self, calls, result=None):
        import unittest.mock as mock

        def fake(kind, width_mm, spacing_mm, *a, **kw):
            # keyword names, because that is how a run's parameters travel now
            # (a dict handed to _solve_one_run, so the same call works in a
            # spawned worker); it used to be positional
            calls.append({"width": width_mm, "gap": spacing_mm})
            if result is not None:
                return dict(result)
            return {"status": "ok", "value": {"next": len(calls) * 1.0,
                                              "fext": len(calls) * 2.0}}

        return mock.patch.object(C._X, "coupled_lines", side_effect=fake)

    def test_the_default_solves_one_run_and_says_how_many_were_left(self):
        calls = []
        with self._patched(calls):
            out = C.board_crosstalk(_board(THREE),
                                    {"net_a": "A", "net_b": "B",
                                     "rise_time_ns": 0.5})
        self.assertEqual(out["status"], "ok", out)
        self.assertEqual(len(calls), 1)
        v = out["value"]
        self.assertEqual([r["rank"] for r in v["runs_solved"]], [1])
        self.assertEqual(v["board"]["candidate_runs"], 3)
        self.assertEqual(len(v["runs_refused"]), 0)
        self.assertTrue(any("solved 1 of 3" in n for n in out["notes"]), out["notes"])

    def test_max_runs_solves_that_many_and_a_width_mismatch_is_refused_by_name(self):
        calls = []
        with self._patched(calls):
            out = C.board_crosstalk(_board(THREE),
                                    {"net_a": "A", "net_b": "B",
                                     "rise_time_ns": 0.5, "max_runs": 3})
        self.assertEqual(out["status"], "ok", out)
        self.assertAlmostEqual(calls[0]["width"], 0.3, places=9)
        self.assertAlmostEqual(calls[0]["gap"], 0.3, places=6)
        self.assertAlmostEqual(calls[1]["gap"], 0.4, places=6)
        v = out["value"]
        self.assertEqual([r["rank"] for r in v["runs_solved"]], [1, 2])
        self.assertEqual([r["next"] for r in v["runs_solved"]], [1.0, 2.0])
        self.assertEqual(len(v["runs_refused"]), 1)
        bad = v["runs_refused"][0]
        self.assertEqual(bad["rank"], 3)
        self.assertAlmostEqual(bad["width_b_mm"], 0.6, places=9)
        self.assertIn("ONE width", bad["why"])
        self.assertTrue(any("not solved here" in n for n in out["notes"]),
                        out["notes"])

    def test_zero_runs_is_refused_rather_than_solved_silently(self):
        out = C.board_crosstalk(_board(THREE),
                                {"net_a": "A", "net_b": "B",
                                 "rise_time_ns": 0.5, "max_runs": 0})
        self.assertEqual(out["reason_code"], "ARG_RANGE", out)

    def test_a_solver_failure_says_which_run_it_was(self):
        calls = []
        with self._patched(calls, result={"status": "error",
                                          "reason_code": "MESH_TOO_FINE",
                                          "message": "grid too fine"}):
            out = C.board_crosstalk(_board(THREE),
                                    {"net_a": "A", "net_b": "B",
                                     "rise_time_ns": 0.5})
        self.assertEqual(out["reason_code"], "MESH_TOO_FINE", out)
        self.assertEqual(out["run_rank"], 1)


class TestHowManyRunsAtOnce(unittest.TestCase):
    """`jobs`: solving several runs at the same time, and what is said when not.

    Nothing here measures a speed-up. A spawned pool cannot be exercised from
    inside pytest - spawn re-imports `__main__`, and under pytest that is the
    test runner, so the pool refuses to start and falls back to serial. What is
    pinned here is the decision (how many, capped by what), the refusals, the
    fallback being *reported*, and - in a subprocess with a real `__main__` -
    that a parallel answer is the same answer.
    """

    def _patched(self, calls):
        import unittest.mock as mock

        def fake(kind, width_mm, spacing_mm, *a, **kw):
            calls.append(spacing_mm)
            return {"status": "ok",
                    "value": {"next": spacing_mm, "fext": spacing_mm * 2.0}}

        return mock.patch.object(C._X, "coupled_lines", side_effect=fake)

    def test_the_default_is_one_run_at_a_time(self):
        calls = []
        with self._patched(calls):
            out = C.board_crosstalk(_board(THREE), {"net_a": "A", "net_b": "B",
                                                    "rise_time_ns": 0.5,
                                                    "max_runs": 3})
        self.assertEqual(out["status"], "ok", out)
        self.assertEqual(len(calls), 2)
        how = out["value"]["parallel"]
        self.assertIs(how["used"], False)
        self.assertEqual(how["jobs"], 1)
        self.assertIn("default", how["asked"])
        self.assertIn("one after another", how["why"])
        self.assertIn("wall_s", how)
        # the answer is still the serial one, in rank order: gap 0.3 then 0.4
        self.assertEqual([round(r["next"], 6) for r in out["value"]["runs_solved"]],
                         [0.3, 0.4])

    def test_auto_is_capped_by_the_runs_the_cores_and_eight(self):
        self.assertEqual(C._jobs_asked({"jobs": "auto"}, 3)[0], min(3, C.MAX_JOBS,
                         os.cpu_count() or 1))
        self.assertEqual(C._jobs_asked({"jobs": "auto"}, 40)[0], C.MAX_JOBS)
        self.assertEqual(C._jobs_asked({"jobs": 99}, 2)[0], 2)
        self.assertEqual(C._jobs_asked({"jobs": "4"}, 8)[0], 4)
        self.assertEqual(C._jobs_asked({"jobs": "auto"}, 1)[0], 1)

    def test_the_machine_env_can_ask_and_a_number_wins_over_it(self):
        import unittest.mock as mock
        with mock.patch.dict(os.environ, {C.JOBS_ENV: "3"}):
            self.assertEqual(C._jobs_asked({}, 8)[0], 3)
            self.assertEqual(C._jobs_asked({"jobs": 2}, 8)[0], 2)
        with mock.patch.dict(os.environ, {C.JOBS_ENV: "auto"}):
            self.assertEqual(C._jobs_asked({}, 2)[0], min(2, C.MAX_JOBS,
                             os.cpu_count() or 1))

    def test_jobs_zero_and_a_non_number_are_refused_by_name(self):
        out = C.board_crosstalk(_board(THREE), {"net_a": "A", "net_b": "B",
                                                "rise_time_ns": 0.5,
                                                "max_runs": 3, "jobs": 0})
        self.assertEqual(out["reason_code"], "ARG_RANGE", out)
        out = C.board_crosstalk(_board(THREE), {"net_a": "A", "net_b": "B",
                                                "rise_time_ns": 0.5,
                                                "max_runs": 3, "jobs": "twice"})
        self.assertEqual(out["reason_code"], "ARG_INVALID", out)

    def test_a_pool_that_cannot_start_says_so_and_still_answers(self):
        import unittest.mock as mock
        calls = []
        with self._patched(calls), \
                mock.patch("multiprocessing.get_context",
                           side_effect=OSError("no semaphores here")):
            out = C.board_crosstalk(_board(THREE), {"net_a": "A", "net_b": "B",
                                                    "rise_time_ns": 0.5,
                                                    "max_runs": 3, "jobs": 2})
        self.assertEqual(out["status"], "ok", out)
        self.assertEqual(len(calls), 2)
        how = out["value"]["parallel"]
        self.assertIs(how["used"], False)
        self.assertIn("OSError", how["why"])
        self.assertIn("one after another", how["why"])

    def test_a_parallel_answer_is_the_same_answer(self):
        """Run from a real script file, where spawn is allowed to work.

        `python -c` would not do: a spawned worker re-imports the main *file*,
        so the stub below has to live at module level of something on disk or
        the workers would solve the real field and never finish in a test.
        """
        import json
        import subprocess
        import textwrap

        script = textwrap.dedent('''
            import json, os, sys
            sys.path.insert(0, "src"); sys.path.insert(0, "tests")
            import unittest.mock as mock
            from test_board_coupling import _board, THREE
            from pcb_agent_mcp.sim import couple as C

            # at module level, so a spawned worker sees it too: what is being
            # pinned is the plumbing (pickling, order, equality), not physics
            mock.patch.object(
                C._X, "coupled_lines",
                side_effect=lambda kind, width_mm, spacing_mm, *a, **k: {
                    "status": "ok",
                    "value": {"next": round(spacing_mm, 9),
                              "fext": round(spacing_mm * 2.0, 9)}}).start()

            def run(jobs):
                a = {"net_a": "A", "net_b": "B", "rise_time_ns": 0.5,
                     "max_runs": 3, "mesh_div": 4}
                if jobs is not None:
                    a["jobs"] = jobs
                out = C.board_crosstalk(_board(THREE), a)
                assert out["status"] == "ok", out
                return out

            if __name__ == "__main__":
                one = run(1)
                two = run(2)
                print(json.dumps({
                    "one": [r["next"] for r in one["value"]["runs_solved"]],
                    "two": [r["next"] for r in two["value"]["runs_solved"]],
                    "used": two["value"]["parallel"]["used"],
                    "jobs": two["value"]["parallel"]["jobs"],
                    "serial_used": one["value"]["parallel"]["used"]}))
        ''')
        root = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
        path = os.path.join(tempfile.gettempdir(), "couple_par_%d.py" % os.getpid())
        with open(path, "w", newline="") as fh:
            fh.write(script)
        env = dict(os.environ)
        env["PYTHONPATH"] = "src;tests;."
        env["PYTHONDONTWRITEBYTECODE"] = "1"
        try:
            got = subprocess.run([sys.executable, path], capture_output=True,
                                 text=True, cwd=root, env=env, timeout=300)
        finally:
            try:
                os.remove(path)
            except OSError:
                pass
        self.assertEqual(got.returncode, 0, got.stderr[-3000:])
        data = json.loads(got.stdout.strip().splitlines()[-1])
        self.assertIs(data["serial_used"], False)
        self.assertIs(data["used"], True, data)
        self.assertEqual(data["jobs"], 2)
        self.assertEqual(data["one"], data["two"])
        self.assertEqual([round(v, 9) for v in data["two"]], [0.3, 0.4])

class TestOneRealSolve(unittest.TestCase):
    """Slow: the field engine costs 30 s to 5 min, so this is ONE coarse run."""

    def test_the_pair_on_the_board_is_the_pair_that_is_solved(self):
        out = C.board_crosstalk(_board(PAIR),
                                {"net_a": "A", "net_b": "B",
                                 "rise_time_ns": 0.5, "mesh_div": 6,
                                 "samples_per_delay": 20, "waveform_points": 21})
        self.assertEqual(out["status"], "ok", out)
        v = out["value"]
        info = v["board"]
        self.assertEqual(info["layer"], "F.Cu")
        self.assertEqual(info["kind"], "microstrip")
        self.assertAlmostEqual(info["width_mm"], 0.3, places=9)
        self.assertAlmostEqual(info["spacing_mm"], 0.3, places=9)
        self.assertAlmostEqual(info["length_mm"], 6.0, places=9)
        self.assertAlmostEqual(info["dielectric_mm"], 1.51, places=9)
        self.assertEqual(info["candidate_runs"], 1)
        self.assertIn("next", v["crosstalk"])
        self.assertIn("fext", v["crosstalk"])


if __name__ == "__main__":
    unittest.main()
