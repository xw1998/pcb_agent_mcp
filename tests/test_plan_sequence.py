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

def test_frame_on_an_open_outline_still_fails():
    b = Board("test", "memory")
    b.outline = []                             # no closed edge at all
    _, ok, _ = S.gate_frame(b, {})
    assert ok is False

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
