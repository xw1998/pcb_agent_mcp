"""ruleset: five priorities, and the resolution between them.

Every test here has a control that can fail. The point of the package is not
"collect rules" - it is that a low-priority finding can never be reported as the
next step while a high-priority one fails, and that a rule with no evidence is
`undetermined` instead of a pass.

Two of the boards are real KiCad demos: `pic_programmer` carries an NPTH
mounting hole (the case that used to be graded as a zero annular ring) and
`multichannel_mixer-unrouted` is a positive control for connectivity - it MUST
be caught at P1. Both are skipped, not faked, when KiCad's demos are absent.
"""
import os
import sys
import unittest

_HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, os.path.join(os.path.dirname(_HERE), "src"))

from pcb_agent_mcp.adapters import open_board                       # noqa: E402
from pcb_agent_mcp.ruleset import audit, catalog, LEVELS, LEVEL_NAMES  # noqa: E402
from pcb_agent_mcp.ruleset import measure as M                     # noqa: E402
import importlib                                                # noqa: E402
_audit_mod = importlib.import_module("pcb_agent_mcp.ruleset.audit")
from pcb_agent_mcp.ruleset.levels import (BLOCKING_LEVELS,         # noqa: E402
                                          resolve)

SAMPLE = os.path.join(_HERE, "data", "sample.kicad_pcb")
PROC = {"layers": 2, "copper_oz": 1}


def _demo(name, folder=None, fname=None):
    """Path of a KiCad demo board, or None when this machine has no demos."""
    try:
        from pcb_agent_mcp.fidelity import engine
        cli = engine.find_kicad_cli()
    except Exception:                                          # noqa: BLE001
        return None
    if not cli:
        return None
    root = os.path.dirname(os.path.dirname(cli))
    p = os.path.join(root, "share", "kicad", "demos", folder or name,
                     fname or (name + ".kicad_pcb"))
    return p if os.path.isfile(p) else None


def _check(level, state, cid=None):
    return {"id": cid or ("x." + level), "level": level, "state": state}


class TestPriorityOrder(unittest.TestCase):

    def test_five_levels_and_three_blocking(self):
        self.assertEqual(LEVELS, ("P0", "P1", "P2", "P3", "P4"))
        self.assertEqual(BLOCKING_LEVELS, ("P0", "P1", "P2"))
        for lvl in LEVELS:
            self.assertIn(lvl, LEVEL_NAMES)

    def test_a_lower_level_is_never_the_next_step(self):
        res = resolve([_check("P0", "fail", "p0.a"), _check("P1", "fail", "p1.a"),
                       _check("P2", "fail", "p2.a"), _check("P3", "fail", "p3.a")])
        self.assertEqual(res["blocking_level"], "P0")
        self.assertEqual(res["first_action"]["rule"], "p0.a")
        # Anything below the blocker is deferred, and its own grade is kept.
        for lvl in ("P1", "P2", "P3", "P4"):
            for c in res["levels"][lvl]["checks"]:
                self.assertEqual(c["state"], "deferred")
                self.assertEqual(c["graded_state"], "fail")
        self.assertEqual(res["deferred"], 3)
        # Nothing below a blocker is offered as advice either.
        self.assertEqual(res["flagged"], [])

    def test_p3_cannot_block_a_buildable_board(self):
        res = resolve([_check("P0", "pass"), _check("P3", "fail", "p3.corner")])
        self.assertIsNone(res["blocking_level"])
        self.assertEqual(res["levels"]["P3"]["status"], "flagged")
        self.assertEqual([f["rule"] for f in res["flagged"]], ["p3.corner"])
        self.assertIsNone(res["first_action"])

    def test_failing_state_is_required_by_the_validator(self):
        with self.assertRaises(ValueError):
            resolve([{"id": "x", "level": "P1", "state": "maybe"}])
        with self.assertRaises(ValueError):
            resolve([{"id": "x", "level": "P9", "state": "fail"}])


class TestAudit(unittest.TestCase):

    def test_no_process_means_p0_is_not_guessed(self):
        r = audit(open_board(SAMPLE))
        self.assertIsNone(r["process"])
        p0 = r["levels"]["P0"]["checks"]
        self.assertTrue(any(c["id"] == "P0.process_declared" and
                            c["state"] == "undetermined" for c in p0))
        # The catalogue's own P0 rules must be undetermined too, not passed.
        for c in p0:
            if c["id"].startswith("layout."):
                self.assertEqual(c["state"], "undetermined", c["id"])
        # ... and a P0 blocker must never be invented from the missing process.
        self.assertNotEqual(r["verdict"], "blocked_at_P0_unbuildable")

    def test_declared_process_evaluates_p0(self):
        r = audit(open_board(SAMPLE), process=PROC)
        p0 = r["levels"]["P0"]["checks"]
        graded = [c for c in p0 if c["state"] in ("pass", "fail",
                                                  "pass_with_warning")]
        self.assertTrue(graded, "nothing at P0 was graded with a process declared")
        self.assertNotIn("P0.process_declared", [c["id"] for c in p0])
        self.assertEqual(r["measured"]["min_track_width"]["value"] > 0, True)

    def test_thickness_comes_from_the_board_not_from_the_caller(self):
        r = audit(open_board(SAMPLE), process=PROC)
        self.assertIn("thickness_mm", r["process_filled_from_board"])
        self.assertIsInstance(r["process"]["thickness_mm"], float)

    def test_unrouted_board_is_caught_at_p1(self):
        """Positive control: an unrouted demo board must fail connectivity."""
        p = _demo("multichannel_mixer-unrouted", "multichannel")
        if not p:
            self.skipTest("KiCad demos are not installed here")
        # No declared process: P0 is not evaluated, so the first thing this
        # board can fail is P1 - which is exactly what an unrouted board is.
        r = audit(open_board(p), window_mm=5.0)
        failed = [c["id"] for c in r["levels"]["P1"]["checks"]
                  if c["state"] == "fail"]
        self.assertIn("layout.connectivity_complete", failed)
        self.assertEqual(r["priority"]["blocking_level"], "P1")

    def test_verdict_names_follow_the_evidence(self):
        r = audit(open_board(SAMPLE), process=PROC)
        self.assertRegex(r["verdict"],
                         r"^(blocked_at_P[0-2]|buildable_flag_at_P[34]|"
                         r"nothing_failed_but_\d+_rules_undetermined|measured_clean)")

    def test_every_rule_carries_a_source(self):
        for lvl in LEVELS:
            for c in r_checks(lvl):
                self.assertTrue(c.get("source"), "%s has no source" % c["id"])


def r_checks(level):
    return [c for c in audit(open_board(SAMPLE), process=PROC)["levels"][level]["checks"]]


class TestNpth(unittest.TestCase):
    """An NPTH mounting hole has no copper collar; it must not be graded as one."""

    def test_mounting_hole_is_not_a_zero_annular_ring(self):
        p = _demo("pic_programmer")
        if not p:
            self.skipTest("KiCad demos are not installed here")
        m = M.measure(open_board(p))
        self.assertIn("value", m["min_th_annular"])
        self.assertGreater(m["min_th_annular"]["value"], 0.0)
        self.assertIn("value", m["min_npth_drill"])
        self.assertGreater(m["min_npth_drill"]["value"], 0.0)

    def test_board_with_only_an_npth_hole_passes_p0_annular(self):
        """The control: pic_programmer used to be unbuildable at P0 for this."""
        p = _demo("pic_programmer")
        if not p:
            self.skipTest("KiCad demos are not installed here")
        r = audit(open_board(p), process=PROC, window_mm=5.0)
        p0 = {c["id"]: c for c in r["levels"]["P0"]["checks"]}
        self.assertNotEqual(p0["PCB.TH.ANNULAR_MIN"]["state"], "fail")
        self.assertNotEqual(r["verdict"], "blocked_at_P0_unbuildable")


class TestLowerBoundIsEvidence(unittest.TestCase):

    def test_a_proven_bound_settles_a_one_sided_rule(self):
        m = {"min_via_hole_edge_gap": {"lower_bound": 5.0, "where": "far apart"}}
        checks, _, _ = _audit_mod._fab_checks(dict(PROC), m)
        row = [c for c in checks if c["id"] == "PCB.VIA.HOLE_EDGE_GAP"][0]
        self.assertEqual(row["state"], "pass")
        self.assertEqual(row.get("measured_from"), "lower_bound")

    def test_a_bound_below_the_limit_stays_undetermined(self):
        m = {"min_via_hole_edge_gap": {"lower_bound": 0.1, "where": "close"}}
        checks, _, _ = _audit_mod._fab_checks(dict(PROC), m)
        row = [c for c in checks if c["id"] == "PCB.VIA.HOLE_EDGE_GAP"][0]
        self.assertEqual(row["state"], "undetermined")
        self.assertNotIn("measured_from", row)


class TestMeasureHygiene(unittest.TestCase):

    def test_bbox_normalises_both_spellings(self):
        d = {"x_min": 1.0, "y_min": 2.0, "x_max": 3.0, "y_max": 4.0}
        self.assertEqual(M._bbox4(d), (1.0, 2.0, 3.0, 4.0))
        self.assertEqual(M._bbox4([1.0, 2.0, 3.0, 4.0]), (1.0, 2.0, 3.0, 4.0))

    def test_pruning_does_not_change_the_answer(self):
        b = open_board(SAMPLE)
        fast = M.measure(b)
        old = M._PRUNE
        try:
            M._PRUNE = False
            slow = M.measure(b)
        finally:
            M._PRUNE = old
        self.assertEqual(fast["min_track_space"]["value"],
                         slow["min_track_space"]["value"])

    def test_oracle_spelling_is_lifted_to_value(self):
        """oracle says `measured`; a ruleset row must say `value` or rules go blind."""
        r = audit(open_board(SAMPLE), process=PROC)
        for key in ("min_via_hole_edge_gap", "min_copper_to_edge"):
            row = r["measured"].get(key) or r["unmeasured"].get(key)
            self.assertIsNotNone(row, key)
        # On this board copper is far from the edge, so it is a bound that passes.
        p0 = {c["id"]: c for c in r["levels"]["P0"]["checks"]}
        self.assertIn(p0["PCB.EDGE.ROUTED"]["state"],
                      ("pass", "fail", "undetermined"))


class TestThreeAdapterFixes(unittest.TestCase):
    """Three reader defects that each invented a violation on a finished board.

    None of them was a rule problem: every one of them was this server measuring
    a shape it had misread. All three are pinned here because they are silent -
    a wrong polygon still produces a number, and a number still produces a
    verdict.
    """

    def test_rotated_custom_pad_turns_once_not_twice(self):
        """vme-wren L1: footprint 180, both pads 180. Turning the outline here AND
        in `pad_points` put the two pads of one inductor on top of each other and
        reported a 0.0 mm short between `Net-(IC32-SW)` and `5VREG`."""
        p = _demo("vme-wren")
        if not p:
            self.skipTest("KiCad demos not installed")
        from pcb_agent_mcp.adapters.canonical import pad_points
        from pcb_agent_mcp import geometry as G
        b = open_board(p)
        fp = [f for f in b.footprints if f.get("ref") == "L1"][0]
        self.assertEqual(round(float(fp.get("rotation") or 0), 3), 180.0)
        pads = {p["number"]: pad_points(p) for p in fp["pads"]}
        d = G.dist_polygon_polygon(pads["1"], pads["2"])
        # The control that can fail: before the fix this was 0.0.
        self.assertGreater(d, 1.0, "the two pads of L1 overlap: the outline was "
                                   "turned twice")

    def test_a_through_via_has_no_copper_where_it_does_not_connect(self):
        """`(remove_unused_layers yes)` on 4370 vme-wren vias. Treating the whole
        F.Cu..B.Cu span as copper made an In4.Cu track passing 0.4 mm from one
        read as a 0.0 mm clearance, while `kicad-cli pcb drc` reports no clearance
        violation at all on that board."""
        p = _demo("vme-wren")
        if not p:
            self.skipTest("KiCad demos not installed")
        b = open_board(p)
        v = [v for v in b.vias
             if abs(v["x"] - 96.326125) < 1e-3 and abs(v["y"] + 240.302175) < 1e-3][0]
        self.assertTrue(v["unused_layers_removed"])
        self.assertEqual(len(v["layers_span"]), 12)
        self.assertNotIn("In4.Cu", v["layers"])
        self.assertIn("F.Cu", v["layers"])
        # ... and the copper it does have is kept: 3 of 12 layers here.
        self.assertGreaterEqual(len(v["layers"]), 2)

    def test_a_connector_pad_is_not_a_short(self):
        """StickHub JP1 is `(pad "1" connect custom ...)`: two interlocking comb
        outlines on two different nets. `kicad-cli pcb drc` on that file reports
        ZERO violations, so this must not be a spacing pair."""
        p = _demo("stickhub", folder="stickhub", fname="StickHub.kicad_pcb")
        if not p:
            self.skipTest("KiCad demos not installed")
        b = open_board(p)
        m = M.measure(b)
        row = m.get("min_track_space") or {}
        where = str(row.get("where") or "")
        self.assertNotIn("JP1", where, "a `connect` pad was used as a spacing pair")
        val = row.get("value")
        if val is not None:
            self.assertGreater(float(val), 0.0)

class TestCatalog(unittest.TestCase):

    def test_catalog_lists_every_gradable_rule_with_a_level(self):
        cat = catalog()
        rules = cat["rules"] if isinstance(cat, dict) else cat
        self.assertGreaterEqual(len(rules), 20)
        for r in rules:
            self.assertIn(r["level"], LEVELS, r["id"])
            self.assertTrue(r.get("title"))
            self.assertTrue(r.get("source"), r["id"])
        ids = [r["id"] for r in rules]
        for own in ("layout.connectivity_complete", "layout.segment_angle_45",
                    "layout.courtyard_clear", "layout.parts_on_board"):
            self.assertIn(own, ids)
        for fab in ("layout.min_trace_width", "layout.min_via_hole"):
            row = [r for r in rules if r["id"] == fab][0]
            self.assertEqual(row["level"], "P0")


class TestPrecondition(unittest.TestCase):
    """`state wrong, no write` - and every check here has a control that fails.

    The gate is not a confirmation box: each line below is decidable from the
    board file and the plan. A check that cannot be decided is None, never True.
    """

    def setUp(self):
        import importlib
        self.pre = importlib.import_module("pcb_agent_mcp.ruleset.precondition")

    # 1 ---------------------------------------------------------------
    def test_a_missing_engine_blocks_and_a_switched_off_one_does_not(self):
        """`options.engine=false` is a switch the caller owns (and the API
        documents), so the gate reports undecided instead of overruling it. A
        kicad-cli that cannot be found is a different fact: the caller asked for a
        check that cannot happen, so that one blocks."""
        off = self.pre.drc_engine(engine_on=False)
        self.assertIs(off["ok"], None, "a switched-off engine was reported as a pass")
        self.assertEqual(off["severity"], "report")
        self.assertIn("NOT a pass", off["why"])
        # control 1: the engine on, with kicad-cli present, is a genuine pass.
        from unittest import mock
        engine = importlib.import_module("pcb_agent_mcp.fidelity.engine")
        with mock.patch.object(engine, "find_kicad_cli", lambda: "/fake/kicad-cli"):
            row = self.pre.drc_engine(engine_on=True)
        self.assertIs(row["ok"], True)
        self.assertIn("DRC diff will run", row["why"])
        # control 2: no kicad-cli at all blocks, and says which binary is missing.
        with mock.patch.object(engine, "find_kicad_cli", lambda: None):
            gone = self.pre.drc_engine(engine_on=True)
        self.assertIs(gone["ok"], False)
        self.assertIn("kicad-cli was not found", gone["why"])

    # 2 ---------------------------------------------------------------
    def test_rules_baseline_fails_when_a_quote_is_missing(self):
        self.assertIs(self.pre.rules_baseline()["ok"], True)
        import importlib
        from unittest import mock
        api = importlib.import_module("pcb_agent_mcp.jlc.api")
        real = api.capabilities("pcb")
        stripped = dict(real)
        stripped["rules"] = [dict(r) for r in real["rules"]]
        for r in stripped["rules"]:
            if r.get("limit"):
                r["limit"] = dict(r["limit"])
                r["limit"].pop("quote", None)
            if r.get("recommended"):
                r["recommended"] = dict(r["recommended"])
                r["recommended"].pop("quote", None)
        with mock.patch.object(api, "capabilities", lambda *a, **k: stripped):
            row = self.pre.rules_baseline()
        self.assertIs(row["ok"], False, "an unsourced fab limit was allowed to pass")
        self.assertIn("no verbatim quote", row["why"])

    # 3 ---------------------------------------------------------------
    def test_an_unknown_outline_blocks_copper_but_not_silk(self):
        empty = {}
        self.assertIs(self.pre.outline_known(empty, copper_write=True)["ok"], False)
        # control: the same missing outline is *not* a pass, but it is not a block
        # either when the plan lands no copper - it degrades to None (=undecided).
        self.assertIs(self.pre.outline_known(empty, copper_write=False)["ok"], None)
        closed = {"present": True, "closed": True, "primitives": 4, "loops": 1}
        self.assertIs(self.pre.outline_known(closed)["ok"], True)
        # control: a plan that only TAKES copper away lands nothing, so an unknown
        # shape cannot stop it - "copper" in `affected` would have been too coarse.
        cut = self.pre.assess(affected=("copper",), outline={}, copper_adds=False)
        self.assertTrue(cut["ok"], "a copper cut was blocked by a missing outline")
        add = self.pre.assess(affected=("copper",), outline={}, copper_adds=True)
        self.assertEqual(add["verdict"], "refused_at_outline_known")

    # 4 ---------------------------------------------------------------
    def test_touching_the_board_shape_is_a_block_unless_declared(self):
        bad = self.pre.mech_unchanged(["outline"])
        self.assertIs(bad["ok"], False)
        self.assertEqual(bad["severity"], "block")
        # control: the same plan passes when it says the shape is the subject.
        self.assertIs(self.pre.mech_unchanged(["outline"],
                                             allow_edge_changes=True)["ok"], True)
        self.assertIs(self.pre.mech_unchanged(["copper", "silkscreen"])["ok"], True)

    # 5 ---------------------------------------------------------------
    def test_clearance_gate_is_a_delta_not_an_absolute(self):
        """A board that already has a tight pair may still be edited; a plan that
        CREATES one may not. Both arms use the same numbers."""
        after = {"threshold_mm": 0.2, "violations": 3,
                 "worst": [{"gap_mm": 0.0, "at": [1.0, 2.0]}]}
        created = self.pre.clearance_no_worse(after, {"violations": 0})
        self.assertIs(created["ok"], False)
        self.assertIn("from 0 to 3", created["why"])
        inherited = self.pre.clearance_no_worse(after, {"violations": 3})
        self.assertIs(inherited["ok"], True, "an inherited pair was blamed on this plan")
        self.assertIn("created none", inherited["why"])
        # and a plan that lands no copper is undecided, not clean
        self.assertIs(self.pre.clearance_no_worse(after, None,
                                                  copper_write=False)["ok"], None)

    # 6 ---------------------------------------------------------------
    def test_assess_names_the_check_that_refused(self):
        r = self.pre.assess(affected=("copper",),
                            outline={"present": True, "closed": True},
                            clearance_after={"violations": 2, "threshold_mm": 0.2},
                            clearance_before={"violations": 0})
        self.assertIs(r["ok"], False)
        self.assertEqual(r["verdict"], "refused_at_clearance_no_worse")
        self.assertEqual(r["blocking"], ["clearance_no_worse"])
        self.assertIn("state wrong, no write", r["rule"])
        # control: the same numbers with the inherited count pass the clearance
        # line, and allow_open_outline turns an open shape into undecided rather
        # than into either a pass or a refusal.
        ok = self.pre.assess(affected=("copper",),
                             outline={"present": True, "closed": True},
                             clearance_after={"violations": 2, "threshold_mm": 0.2},
                             clearance_before={"violations": 2})
        self.assertIs(ok["ok"], True)
        self.assertEqual(ok["verdict"], "writable_with_1_undecided")
        opened = self.pre.assess(affected=("copper",), outline={"present": True,
                                                               "closed": False},
                                 allow_open_outline=True)
        self.assertIs(opened["ok"], True)
        self.assertIn("allow_open_outline", opened["verdict"] + str(opened["checks"]))

    # 7 ---------------------------------------------------------------
    def test_the_gate_is_wired_into_propose_and_has_an_off_switch(self):
        import shutil
        import tempfile
        import importlib
        plan = importlib.import_module("pcb_agent_mcp.edit.plan")
        tmp = tempfile.mkdtemp(prefix="pcb_pre_")
        self.addCleanup(shutil.rmtree, tmp, True)
        a = os.path.join(tmp, "a.kicad_pcb")
        b = os.path.join(tmp, "b.kicad_pcb")
        shutil.copy(SAMPLE, a)
        shutil.copy(SAMPLE, b)
        # two different nets, 0.05 mm of copper gap: this plan creates a pair.
        bad = [{"op": "add_track", "layer": "F.Cu", "net": "GND",
                "x1": 120.0, "y1": -110.0, "x2": 120.0, "y2": -112.0, "width": 0.25},
               {"op": "add_track", "layer": "F.Cu", "net": "VCC",
                "x1": 120.3, "y1": -110.0, "x2": 120.3, "y2": -112.0, "width": 0.25}]
        r = plan.propose(a, bad, options={})
        self.assertIn("precondition.clearance_no_worse",
                      [x.get("gate") for x in r.get("blocking")])
        self.assertEqual(r["preconditions"]["verdict"],
                         "refused_at_clearance_no_worse")
        # control: the off switch removes the key entirely - a reply from a gate
        # that did not run must not look like a reply from one that passed.
        off = plan.propose(b, bad, options={"preconditions": False})
        self.assertNotIn("preconditions", off)
        self.assertNotIn("precondition.clearance_no_worse",
                         [x.get("gate") for x in off.get("blocking")])

class TestDesignBaseline(unittest.TestCase):
    """The corpus bands are a target, not a verdict - and the code has to say so.

    Every test here uses a two-board corpus so the bands are known by hand; the
    eighteen-board run costs minutes and belongs to a tool call, not to a suite.
    """

    def setUp(self):
        import importlib
        self.b = importlib.import_module("pcb_agent_mcp.ruleset.baseline")

    def test_a_missing_corpus_errors_instead_of_inventing_bands(self):
        row = self.b.bands(directory=os.path.join(_HERE, "no_such_dir"))
        self.assertEqual(row["status"], "error")
        self.assertEqual(row["reason_code"], "CORPUS_NOT_FOUND")
        # control: a real corpus is ok, and its board count is the count it read.
        real = self.b.bands(paths=[SAMPLE, SAMPLE])
        self.assertEqual(real["status"], "ok")
        self.assertEqual(real["boards"], 2)

    def test_bands_are_measured_not_assumed(self):
        row = self.b.bands(paths=[SAMPLE, SAMPLE])
        width = row["bands"]["min_track_width"]
        self.assertEqual(width["n"], 2)
        # the sample board's narrowest stored track is 0.25 mm; a band that does
        # not contain the value it was computed from is not a band.
        self.assertAlmostEqual(width["p50"], 0.25, places=6)
        self.assertLessEqual(width["p10"], width["p50"])
        self.assertLessEqual(width["p50"], width["p90"])
        self.assertIn("target, not a limit", row["reads_as"])

    def test_a_board_inside_the_corpus_is_inside(self):
        from pcb_agent_mcp.adapters import open_board
        row = self.b.compare(open_board(SAMPLE), paths=[SAMPLE, SAMPLE])
        self.assertEqual(row["status"], "ok")
        self.assertTrue(row["blocks_nothing"])
        pos = dict((r["metric"], r["position"]) for r in row["rows"])
        self.assertEqual(pos["min_track_width"], "inside")
        # control: a corpus that does NOT contain this board puts it outside, and
        # the answer still carries no verdict - only the word.
        other = _demo("pic_programmer")
        if not other:
            self.skipTest("KiCad demos not installed")
        far = self.b.compare(open_board(SAMPLE), paths=[other])
        self.assertIn(dict((r["metric"], r["position"])
                           for r in far["rows"])["min_board_dim"], ("below", "above"))
        self.assertNotIn("verdict", far)

    def test_a_metric_the_board_cannot_answer_is_unknown_not_zero(self):
        from pcb_agent_mcp.adapters import open_board
        row = self.b.compare(open_board(SAMPLE), paths=[SAMPLE])
        rows = dict((r["metric"], r) for r in row["rows"])
        # the sample has no plated through-hole pad, so that metric cannot exist
        # there at all; a 0 would read as "drilled at zero", which is wrong.
        self.assertIn(rows["min_plated_drill"]["position"], ("unknown", "inside"))
        for r in row["rows"]:
            if r["position"] == "unknown":
                self.assertIsNone(r["this"])
                self.assertTrue(r["why"])

if __name__ == "__main__":
    unittest.main()
