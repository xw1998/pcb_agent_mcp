"""Reverse controls for tools/plan_floorplan.py.

Three of these would have caught real defects in this file:

  * a side target computed from the group's OWN current inset, which makes every
    translation zero and makes the whole search report "0.000 mm saved" - the bug
    that shipped in the first draft;
  * a search that returns a number worse than the board it started from, i.e. an
    enumeration that is not actually minimising;
  * the hub (the module itself) being allowed to move, which makes every other
    proposal look bad and produces a "better" plan that is worse.
"""
import os
import sys

HERE = os.path.dirname(os.path.abspath(__file__))
REPO = os.path.dirname(HERE)
sys.path.insert(0, os.path.join(REPO, "tools"))
sys.path.insert(0, os.path.join(REPO, "src"))

import plan_floorplan as P                                        # noqa: E402

BOX = (0.0, 0.0, 45.0, 45.0)


def test_edge_sides():
    assert P.side_of_edge(0.2, 20.0, BOX) == "W"
    assert P.side_of_edge(44.8, 20.0, BOX) == "E"
    assert P.side_of_edge(20.0, 0.2, BOX) == "S"
    assert P.side_of_edge(20.0, 44.8, BOX) == "N"
    assert P.side_of_edge(20.0, 20.0, BOX) is None      # interior is not a terminal


def test_position_sides_are_halves_not_edges():
    # a part at x=15 is on the W half even though it is 15 mm from the edge
    assert P.side_of_position(15.0, 22.5, BOX) == "W"
    assert P.side_of_position(30.0, 22.5, BOX) == "E"
    assert P.side_of_position(22.5, 5.0, BOX) == "S"
    assert P.side_of_position(22.5, 40.0, BOX) == "N"


def test_shift_target_moves_and_never_leaves_the_board():
    pads = [{"x": 20.0, "y": 20.0}, {"x": 25.0, "y": 24.0}]     # 5 x 4 body
    for side in P.SIDES:
        dx, dy = P.shift_target(pads, side, BOX)
        xs = [p["x"] + dx for p in pads]
        ys = [p["y"] + dy for p in pads]
        assert min(xs) >= BOX[0] - 1e-9 and max(xs) <= BOX[2] + 1e-9
        assert min(ys) >= BOX[1] - 1e-9 and max(ys) <= BOX[3] + 1e-9
        # the bug this pins: the group must actually arrive at the edge it chose
        if side == "W":
            assert abs(min(xs) - (BOX[0] + P.TARGET_INSET_MM)) < 1e-9
        if side == "E":
            assert abs(max(xs) - (BOX[2] - P.TARGET_INSET_MM)) < 1e-9
        if side == "S":
            assert abs(min(ys) - (BOX[1] + P.TARGET_INSET_MM)) < 1e-9
        if side == "N":
            assert abs(max(ys) - (BOX[3] - P.TARGET_INSET_MM)) < 1e-9


def test_hpwl_is_zero_for_a_point():
    assert P.hpwl([(1.0, 2.0)]) == 0.0
    assert abs(P.hpwl([(0.0, 0.0), (3.0, 4.0)]) - 7.0) < 1e-12


def test_real_board_plan_cannot_be_worse_than_the_board():
    board = os.path.join(REPO, "examples", "rk3506_som", "rk3506_som.kicad_pcb")
    if not os.path.exists(board):
        return
    got = P.analyse(board)
    assert got["airwire_mm_best"] <= got["airwire_mm_now"] + 1e-6
    assert got["hub_group"], "the group holding the widest part must be named"
    # the hub is priced but never proposed a move: it has to reach every side
    assert all(not g["is_hub"] or g["current_side"] == g["suggested_side"]
               for g in got["groups"])
    assert got["crossing_total"] > 0
    assert got["nets_priced"] > 0
