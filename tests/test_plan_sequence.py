"""Reverse controls for tools/plan_sequence.py.

The file's whole claim is one sentence: *a step is only judged when everything
before it passed*. That claim is worth nothing unless it can be made to fail, so
every test here has a side that must come out badly:

  * a later step whose own gate says "fail" must NOT be reported as fail when an
    earlier step failed - it must be `blocked` (measured, printed, not judged);
  * `n/a` must not break the chain (otherwise a 2-layer board would swallow
    every step after the stackup);
  * a gate that raises - including `SystemExit`, which the helper tools really
    do raise - must be reported as `error`, never as a pass, and must stop the
    chain the same way a fail does;
  * a corpus may only convict a `physical` rule; for a `house` rule it must say
    "not evidence" no matter how many boards fail it.

Plus three measurement guards that each pin a false green this file shipped or
nearly shipped:

  * `all_nets` must read nets from tracks/pads (a board whose `nets` dict was
    never resolved must not read as "0 power nets", which passed the power gate
    by having nothing to check);
  * a board with no track and no via cannot be said to satisfy the edge rule -
    that is `n/a`, not `pass`;
  * a 2-layer board has no inner plane to reference, so the stackup rule is
    `n/a` there, not a board defect.
"""
import os
import sys

HERE = os.path.dirname(os.path.abspath(__file__))
REPO = os.path.dirname(HERE)
sys.path.insert(0, os.path.join(REPO, "tools"))
sys.path.insert(0, os.path.join(REPO, "src"))

import plan_sequence as S                                        # noqa: E402
from pcb_agent_mcp.adapters.canonical import Board               # noqa: E402

OUTLINE = [[(0.0, 0.0), (45.0, 0.0), (45.0, 45.0), (0.0, 45.0)]]

def _board(outline=OUTLINE, stackup=None):
    b = Board("test", "memory")
    b.outline = [list(c) for c in outline]
    b.stackup = list(stackup or [])
    return b

def _track(net, x0, y0, x1, y1, width=0.20, layer="F.Cu"):
    return {"net": net, "layer": layer, "width": width,
            "start": (x0, y0), "end": (x1, y1)}

# --------------------------------------------------------------------------- #
# the chain: fail vs blocked vs n/a vs error
# --------------------------------------------------------------------------- #
def _fake_chain(monkeypatch, verdicts):
    """Replace the eight real gates with scripted ones.

    verdicts maps step id -> (ok, raises). `ok` may be True/False/None.
    """
    def make(ok, raises):
        def fn(board, ctx):
            if raises:
                raise raises
            return ({"probe": ok}, ok, "scripted")
        return fn
    monkeypatch.setattr(S, "GATES",
                        {sid: (make(*verdicts[sid]), sid) for sid in S.ORDER})
    monkeypatch.setattr(S, "_ctx",
                        lambda path: {"path": path, "canonical": None})

def test_later_fail_becomes_blocked_not_fail(monkeypatch):
    verdicts = {sid: (True, None) for sid in S.ORDER}
    verdicts["floorplan"] = (False, None)
    verdicts["route"] = (False, None)          # would fail on its own merits
    _fake_chain(monkeypatch, verdicts)

    rep = S.evaluate("memory.kicad_pcb")
    got = {s["id"]: s["status"] for s in rep["steps"]}
    assert got["frame"] == "pass"
    assert got["floorplan"] == "fail"
    # this is the point of the file: it measured route, and did not judge it
    assert got["route"] == "blocked"
    assert [s for s in rep["steps"] if s["id"] == "route"][0]["after"] == "floorplan"
    assert rep["first_failing_step"] == "floorplan"
    assert rep["verdict"] == "blocked_at_floorplan"
    # the reading is still there - blocked means measured, not skipped
    assert [s for s in rep["steps"] if s["id"] == "route"][0]["measured"] == {"probe": False}

def test_na_does_not_break_the_chain(monkeypatch):
    verdicts = {sid: (True, None) for sid in S.ORDER}
    verdicts["bus"] = (None, None)
    _fake_chain(monkeypatch, verdicts)

    rep = S.evaluate("memory.kicad_pcb")
    got = {s["id"]: s["status"] for s in rep["steps"]}
    assert got["bus"] == "n/a"
    assert got["route"] == "pass"              # a step that does not apply is not a hole
    assert rep["verdict"] == "sequence_ok"
    assert rep["not_judged"] == []

def test_error_stops_the_chain_and_is_never_a_pass(monkeypatch):
    verdicts = {sid: (True, None) for sid in S.ORDER}
    verdicts["fanout"] = (True, SystemExit("board has no outline - cannot measure sides"))
    _fake_chain(monkeypatch, verdicts)

    rep = S.evaluate("memory.kicad_pcb")
    got = {s["id"]: s["status"] for s in rep["steps"]}
    assert got["fanout"] == "error"
    assert got["power"] == "blocked"
    assert rep["verdict"] == "partly_unknown"  # nothing is the *first failing step*
    assert rep["not_judged"]

def test_plain_exception_also_stops_the_chain(monkeypatch):
    verdicts = {sid: (True, None) for sid in S.ORDER}
    verdicts["power"] = (True, RuntimeError("boom"))
    _fake_chain(monkeypatch, verdicts)
    rep = S.evaluate("memory.kicad_pcb")
    assert {s["id"]: s["status"] for s in rep["steps"]}["power"] == "error"

# --------------------------------------------------------------------------- #
# the corpus may only convict a physical rule
# --------------------------------------------------------------------------- #
def test_corpus_judgement_separates_physical_from_house():
    assert "错的" in S.judge_corpus("physical", 0.2)     # my rule is wrong
    assert "待复核" in S.judge_corpus("physical", 0.6)   # threshold may be too tight
    assert "规则成立" in S.judge_corpus("physical", 1.0)
    house_low = S.judge_corpus("house", 0.2)
    assert "不算反证" in house_low                        # a good 2005 board may break it
    assert "错的" not in house_low
    assert S.judge_corpus("physical", None) == "无可判样本"

def test_route_is_the_only_house_step():
    assert S.ORIGIN["route"] == "house"
    assert all(v == "physical" for k, v in S.ORIGIN.items() if k != "route")

# --------------------------------------------------------------------------- #
# measurement guards (each pins a false green)
# --------------------------------------------------------------------------- #
def test_all_nets_reads_tracks_not_just_the_net_table():
    b = _board()
    b.tracks = [_track("+3V3", 5, 5, 10, 5), _track("GND", 5, 8, 10, 8)]
    assert b.nets == {}                        # never resolved: the old false green
    names = S.all_nets(b)
    assert "+3V3" in names and "GND" in names

def test_frame_passes_when_copper_keeps_its_distance():
    b = _board()
    b.tracks = [_track("SIG", 5.0, 20.0, 10.0, 20.0)]
    measured, ok, _ = S.gate_frame(b, {})
    assert ok is True and measured["tracks_vias_under"] == 0

def test_frame_fails_when_copper_is_inside_the_edge_rule():
    b = _board()
    # centreline 0.15 mm from the edge, track half width 0.10 => 0.05 mm of copper
    # to the edge, against a 0.30 mm rule
    b.tracks = [_track("SIG", 0.15, 20.0, 5.0, 20.0)]
    measured, ok, note = S.gate_frame(b, {})
    assert ok is False
    assert measured["edge_min_mm"] < 0.30      # 0.05 mm of copper, against 0.30
    assert measured["tracks_vias_under"] == 0  # close to the edge, not across it

def test_frame_counts_a_track_that_actually_crosses_the_edge():
    b = _board()
    b.tracks = [_track("SIG", -1.0, 20.0, 5.0, 20.0)]   # copper outside the board
    measured, ok, _ = S.gate_frame(b, {})
    assert ok is False and measured["tracks_vias_under"] == 1
    assert measured["edge_min_mm"] < 0.0

def test_frame_on_a_board_with_no_copper_is_na_not_pass():
    b = _board()
    measured, ok, note = S.gate_frame(b, {})
    assert ok is None                          # nothing measured, nothing claimed
    assert measured["edge_min_mm"] is None
    assert "无从测量" in note

def test_frame_on_an_open_outline_is_unmeasurable_not_a_fault():
    """No edge means no distance to measure.

    This used to come back `fail`, and on a corpus of real 立创EDA boards three
    shipped products "failed" the edge rule because their outline is drawn as
    segments that do not meet. The corpus then printed "好板子多数不过 => 这条规则
    是错的" - a rule convicted by a missing measurement. Whether an outline closes
    is DRC's entry; this gate judges copper-to-edge distance only.
    """
    b = Board("test", "memory")
    b.outline = []                             # no closed edge at all
    measured, ok, note = S.gate_frame(b, {})
    assert ok is None and measured["outline_closed"] is False
    assert "测不出" in note

def test_frame_survives_a_polygon_pad_with_no_width_or_height():
    """A custom pad carries a point list, not a size (`w`/`h` are None).

    `max(None, None)` raised inside the castellation test and turned a whole
    board into `error` (measured: 13 such pads on a real 立创EDA board).
    """
    b = _board()
    b.tracks = [_track("SIG", 20.0, 20.0, 25.0, 20.0)]
    b.footprints = [{"ref": "U1", "pads": [
        {"number": "69", "net": "GND", "x": 20.0, "y": 20.0,
         "w": None, "h": None, "shape": "custom", "layers": ["F.Cu"],
         "polygon": [[2.744995, 2.744995], [2.744995, -2.744995],
                     [-2.744995, -2.744995], [-2.744995, 2.744995]]}]}]
    measured, ok, _ = S.gate_frame(b, {})
    assert ok is True                        # measured, not crashed
    assert S.pad_half(b.pads()[0]) == 2.744995

def test_corpus_paths_finds_easyeda_containers_too():
    """A 立创EDA project is judgeable: the gates read the canonical board.

    Globbing only `*.kicad_pcb` kept 20 real hand-drawn boards out of every
    rule audit while sitting in the same corpus directory.
    """
    import tempfile
    d = tempfile.mkdtemp()
    for name in ("a.kicad_pcb", "b.epro", "c.eprj", "notes.txt"):
        open(os.path.join(d, name), "w").close()
    found = [os.path.basename(p) for p in S.corpus_paths(d)]
    assert found == ["a.kicad_pcb", "b.epro", "c.eprj"]

def test_floorplan_without_an_outline_is_unmeasurable_not_an_error():
    """No sides means "wrong edge" has no object - and it must not stop the chain.

    `plan_floorplan.analyse` raises `ValueError("board has no outline")`, which
    the orchestrator turns into `error` and stops every later step. On three
    shipped 立创EDA boards whose outline is drawn as open segments, that is
    what happened: a missing measurement was printed as a broken board. The
    frame gate already treats the same fact as n/a, so this one must too.
    """
    b = Board("test", "memory")
    b.outline = []
    measured, ok, note = S.gate_floorplan(b, {"path": "memory.kicad_pcb"})
    assert ok is None                     # not a pass, not a fail
    assert "测不出" in note
    assert measured["nets_priced"] == 0

def test_floorplan_still_measures_a_board_that_has_a_closed_outline(monkeypatch):
    """The guard above must not swallow the real reading."""
    import plan_floorplan
    calls = []
    monkeypatch.setattr(plan_floorplan, "analyse",
                        lambda path: calls.append(path) or {"nets_priced": 0})
    measured, ok, note = S.gate_floorplan(_board(), {"path": "memory.kicad_pcb"})
    assert calls == ["memory.kicad_pcb"]      # the file really was measured
    assert "outline_note" not in measured
    assert "闭环" not in note                  # the no-outline wording, not this one

def test_stackup_two_layer_is_na():
    b = _board(stackup=[{"name": "F.Cu"}, {"name": "B.Cu"}])
    b.tracks = [_track("SIG", 5, 5, 10, 5)]
    _, ok, note = S.gate_stackup(b, {})
    assert ok is None                          # no inner plane to reference
    assert "不适用" in note

def test_stackup_four_layer_without_a_plane_fails():
    b = _board(stackup=[{"name": "F.Cu"}, {"name": "In1.Cu"},
                        {"name": "In2.Cu"}, {"name": "B.Cu"}])
    b.tracks = [_track("SIG", 5, 5, 10, 5)]
    measured, ok, _ = S.gate_stackup(b, {})
    assert ok is False
    assert [h["layer"] for h in measured["reference_holes"]] == ["F.Cu"]

def test_stackup_four_layer_with_a_plane_passes():
    b = _board(stackup=[{"name": "F.Cu"}, {"name": "In1.Cu"},
                        {"name": "In2.Cu"}, {"name": "B.Cu"}])
    b.tracks = [_track("SIG", 5, 5, 10, 5)]
    b.zones = [{"net": "GND", "layers": ["In1.Cu"], "outline": list(OUTLINE[0])}]
    _, ok, _ = S.gate_stackup(b, {})
    assert ok is True

# --------------------------------------------------------------------------- #
# length grouping (the bus step's input)
# --------------------------------------------------------------------------- #
def test_length_group_strips_index_and_polarity():
    import plan_meander as M
    assert M.length_group("/DDR_DQ13") == "DDR_DQ"
    assert M.length_group("/DDR_DQS0P") == "DDR_DQS"
    assert M.length_group("/DDR_DQS1N") == "DDR_DQS"
    assert M.length_group("/DDR_A0") == "DDR_A"
    # a net without a trailing index is its own group, not an empty name
    assert M.length_group("/DDR_CLK") == "DDR_CLK"


def test_a_board_that_never_ran_is_never_a_pass():
    """A hung/timed-out child has NO verdict: every step is error, and the
    report says `not_run` instead of leaving steps to look judged."""
    rep = S._unrun_report("B.kicad_pcb", "5.0 秒内没跑完")
    assert rep["verdict"] == "not_run"
    assert [s["status"] for s in rep["steps"]] == ["error"] * len(S.ORDER)
    assert rep["not_judged"] == list(S.ORDER)
    assert rep["first_failing_step"] is None


def test_isolation_reports_a_broken_child_instead_of_raising():
    rep = S.evaluate_isolated("nope.kicad_pcb", timeout=20)
    assert rep["verdict"] == "not_run"
    assert rep["note"]


def test_corpus_does_not_count_a_hung_board_as_judged(monkeypatch, capsys):
    hung = S._unrun_report("a.kicad_pcb", "deadline")
    good = {"board": "b.kicad_pcb", "order": list(S.ORDER), "mode": "independent",
            "verdict": "sequence_ok", "first_failing_step": None,
            "not_judged": [],
            "steps": [{"id": sid, "title": sid, "why": "", "requires": None,
                       "status": "pass", "measured": {}, "note": ""}
                      for sid in S.ORDER]}
    monkeypatch.setattr(S, "evaluate_isolated",
                        lambda p, *a, **kw: hung if p == "a.kicad_pcb" else good)
    out = S.run_corpus(["a.kicad_pcb", "b.kicad_pcb"], 0.30, 0.10, quiet=False)
    assert out["steps"]["frame"]["counts"]["not_run"] == 1
    assert out["steps"]["frame"]["counts"]["pass"] == 1
    assert out["steps"]["frame"]["judged"] == 1


def _rep(steps_override=None, copper=None, verdict="sequence_ok"):
    steps = []
    for sid in S.ORDER:
        steps.append({"id": sid, "title": sid, "why": "", "requires": None,
                      "status": (steps_override or {}).get(sid, "n/a"),
                      "measured": {}, "note": ""})
    rep = {"board": "x.kicad_pcb", "order": list(S.ORDER), "mode": "independent",
           "steps": steps, "first_failing_step": None, "not_judged": [],
           "verdict": verdict}
    if copper is not None:
        rep["copper"] = copper
    return rep

def test_a_copper_free_board_is_named_and_never_counted_as_passing_route(
        monkeypatch, capsys):
    """`shape_pass` on a board with no track means "nothing failed", which is the
    same shape of false green as a rule with nothing to check. Such a board is
    named in the report and its copper steps become n/a."""
    empty = _rep({"route": "pass", "closure": "pass", "bus": "pass", "frame": "pass"},
                 copper={"tracks": 0, "vias": 0, "zones": 1, "filled_zones": 0,
                         "pads": 40})
    monkeypatch.setattr(S, "evaluate_isolated", lambda p, *a, **kw: empty)
    out = S.run_corpus(["empty.kicad_pcb"], 0.30, 0.10, quiet=False)
    assert [c["board"] for c in out["copper_free"]] == ["empty.kicad_pcb"]
    assert out["copper_free"][0]["tracks"] == 0
    for sid in S.COPPER_STEPS:
        assert out["steps"][sid]["counts"]["pass"] == 0, sid
        assert out["steps"][sid]["judged"] == 0, sid
    # a step about pads and the outline is still judged on such a board
    assert out["steps"]["frame"]["counts"]["pass"] == 1
    printed = capsys.readouterr().out
    assert "copper-free boards" in printed
    assert "empty.kicad_pcb" in printed

def test_a_board_with_one_track_is_not_copper_free(monkeypatch):
    one = _rep({"route": "pass"}, copper={"tracks": 1, "vias": 0, "zones": 0,
                                          "filled_zones": 0, "pads": 4})
    monkeypatch.setattr(S, "evaluate_isolated", lambda p, *a, **kw: one)
    out = S.run_corpus(["one.kicad_pcb"], 0.30, 0.10, quiet=False)
    assert out["copper_free"] == []
    assert out["steps"]["route"]["counts"]["pass"] == 1

def test_a_filled_plane_counts_as_copper():
    """A board routed entirely by plane (a power board) has no tracks and IS
    copper: calling it copper-free would throw away the only copper it has."""
    inv = {"tracks": 0, "vias": 0, "zones": 2, "filled_zones": 1, "pads": 10}
    assert S.is_copper_free(inv) is False

def test_an_unmeasured_board_is_not_copper_free():
    """Missing inventory = we did not look. That must not read as empty."""
    assert S.is_copper_free(None) is False
    assert S.is_copper_free({}) is False

def test_copper_inventory_ignores_keepout_zones():
    board = Board("kicad_pcb", "mem.kicad_pcb")
    board.zones = [
        {"net": "GND", "is_keepout": False, "filled_polygons": [[[(0, 0)]]]},
        {"net": None, "is_keepout": True, "filled_polygons": [[[(0, 0)]]]},
        {"net": "V3", "is_keepout": False, "filled_polygons": []},
    ]
    inv = S.copper_inventory(board)
    assert inv["tracks"] == 0 and inv["zones"] == 3
    assert inv["filled_zones"] == 1     # a keepout is not copper, an empty fill is not either

def test_the_drc_flag_reaches_the_closure_step(monkeypatch):
    """`--drc` parsed and printed nothing for one round: the flag existed on the
    CLI but `evaluate()` never put it in `ctx`, so `ctx.get("drc")` was always
    falsy and no DRC reading ever appeared.  A flag that cannot reach the code
    it names is worse than no flag - it reads as 'ran and found nothing'."""
    seen = {}

    def fake_gate(canon, ctx):
        seen["drc"] = ctx.get("drc")
        return {}, True, ""

    monkeypatch.setattr(S, "_ctx", lambda path: {"path": path, "canonical": None})
    monkeypatch.setattr(S, "ORDER", ("closure",))
    monkeypatch.setattr(S, "GATES", {"closure": (fake_gate, "t")})
    S.evaluate("B.kicad_pcb", drc=True)
    assert seen["drc"] is True
    S.evaluate("B.kicad_pcb")
    assert seen["drc"] is False


def test_floorplan_reports_its_threshold_sweep(monkeypatch):
    import plan_floorplan
    monkeypatch.setattr(plan_floorplan, "analyse",
                        lambda path, top=12: {"nets_priced": 100,
                                              "crossing_total": 10,
                                              "overlaps": [],
                                              "overlap_basis": "padding",
                                              "airwire_saved_frac": 0.0,
                                              "crossing_by_group": {}})
    measured, ok, note = S.gate_floorplan(None, {"path": "B.kicad_pcb"})
    assert ok is True
    # 10/100 = 0.10 => passes at 0.10 as well; the sweep must say so instead of
    # only printing the one threshold that was used for the verdict.
    assert measured["by_threshold"]["0.10"] == "pass"
    assert measured["by_threshold"]["0.30"] == "pass"
    assert measured["overlap_basis"] == "padding"

# --------------------------------------------------------------------------- #
# the corpus is stratified by EXOGENOUS evidence, not by my own verdicts
# --------------------------------------------------------------------------- #
def test_evidence_comes_from_the_drc_report_next_to_the_board(tmp_path):
    import json as _json
    p = tmp_path / "b.kicad_pcb"
    p.write_text("(kicad_pcb)")
    doc = {"kicad_version": "10.0", "date": "2026-11-16T00:00:00",
           "violations": [
               {"severity": "error", "type": "clearance"},
               {"severity": "error", "type": "lib_footprint_issues"},
               {"severity": "warning", "type": "lib_footprint_issues"},
               {"severity": "warning", "type": "silk_edge_clearance"}],
           "unconnected_items": [{"x": 1}]}
    (tmp_path / "b.kicad_pcb.drc.json").write_text(_json.dumps(doc))
    got = S.corpus_evidence(str(p))
    assert got["file"] == "b.kicad_pcb.drc.json"
    # the library-mismatch ERROR is reported but not charged to the board: it says
    # the footprint library differs, not that the copper is wrong
    assert got["errors"] == 2 and got["board_errors"] == 1
    assert got["unconnected"] == 1
    assert got["defects"] == 2          # 1 board error + 1 unconnected
    assert got["kicad_version"] == "10.0"

def test_no_report_means_unverified_and_never_a_stratum(tmp_path):
    p = tmp_path / "b.kicad_pcb"
    p.write_text("(kicad_pcb)")
    got = S.corpus_evidence(str(p))
    assert got["file"] is None and got["defects"] is None
    assert "kicad-cli" in got["why"]

def test_strata_bucket_by_rank_and_leave_gaps_unverified():
    rows = [{"board": "a%d" % i, "evidence": {"defects": d}}
            for i, d in enumerate([0, 0, 1, 3, 8, 40])]
    rows.append({"board": "noev", "evidence": {"defects": None}})
    lay, why = S.strata(rows)
    assert lay and lay["names"] == S.STRATUM_NAMES and why is None
    by = {r["board"]: r["stratum"] for r in rows}
    assert by["a0"] == "low" and by["a5"] == "high"
    assert by["noev"] == "unverified"
    assert lay["spans"]["low"][1] <= lay["spans"]["high"][0]

def test_strata_refuse_to_lie_when_too_few_boards_have_evidence():
    rows = [{"board": "a", "evidence": {"defects": 0}},
            {"board": "b", "evidence": {"defects": 9}}]
    lay, why = S.strata(rows)
    assert lay is None and "不做分层" in why

def test_a_rule_is_only_convicted_by_the_cleanest_stratum():
    per = {"low": {"judged": 4, "pass_rate": 1.0},
           "high": {"judged": 4, "pass_rate": 0.25}}
    said, code = S.judge_strata("physical", per, "base")
    assert code == "hold_across_strata" and "分层不改变判决" in said

    per["low"]["pass_rate"] = 0.25
    said, code = S.judge_strata("physical", per, "base")
    assert code == "refuted_on_cleanest" and "规则是错的" in said

    # the inversion is the more damning shape: the cleanest boards fail MOST
    per = {"low": {"judged": 4, "pass_rate": 0.0},
           "high": {"judged": 4, "pass_rate": 0.75}}
    said, code = S.judge_strata("physical", per, "base")
    assert code == "rule_flags_cleanest" and "问题在规则不在板子" in said

    # too few judged in the clean stratum: no conviction, however bad the rest
    per = {"low": {"judged": 1, "pass_rate": 0.0},
           "high": {"judged": 8, "pass_rate": 0.0}}
    said, code = S.judge_strata("physical", per, "base")
    assert code == "low_stratum_thin" and "不能定罪" in said

def test_house_rules_are_not_stratified_either():
    per = {"low": {"judged": 9, "pass_rate": 0.0},
           "high": {"judged": 9, "pass_rate": 1.0}}
    said, code = S.judge_strata("house", per, "base")
    assert code == "house_rule_not_stratified" and said == "base"
