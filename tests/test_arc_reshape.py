# -*- coding: utf-8 -*-
"""tools/arc_reshape.py 的用例。

这个工具要回答的问题只有一个：板上**已经存在**的 45 度三角齿，能不能就地
重画成圆弧蛇形，而长度一分不差、两端仍然接在留下来的铜上。所以每个 gate
都配一条故意弄坏它的对照——把判据写成 `return True` 必须让这里变红。

真板用例（/DDR_DQ0 @ In4.Cu）钉的是这次代价换来的那条事实：三角齿的
**首末顶点是齿尖**，铜真正走的那条线在 0.255 mm 之外；取顶点当弦，长度全绿、
两端各浮空 0.11 mm（引擎 DRC 报 32 条 track_dangling）。
"""
import importlib.util
import math
import os
import unittest

HERE = os.path.dirname(os.path.abspath(__file__))
REPO = os.path.dirname(HERE)
SRC = os.path.join(REPO, "src")
TOOL = os.path.join(REPO, "tools", "arc_reshape.py")
BOARD = os.path.join(REPO, "examples", "rk3506_som", "rk3506_som.kicad_pcb")

_MOD = None


def ar():
    global _MOD
    if _MOD is None:
        import sys
        if SRC not in sys.path:
            sys.path.insert(0, SRC)
        spec = importlib.util.spec_from_file_location("arc_reshape", TOOL)
        mod = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(mod)
        _MOD = mod
    return _MOD


# ---- a board shaped like the one that failed ---------------------------------

class FakeBoard(object):
    """Only the four things Model/Copper ask for: tracks, vias, pads, footprints."""

    def __init__(self, tracks=(), vias=(), pads=(), footprints=()):
        self.tracks = list(tracks)
        self.vias = list(vias)
        self._pads = list(pads)
        self.footprints = list(footprints)
        self.zones = []

    def pads(self):
        return list(self._pads)


def leg(n, a, b, net="/N", layer="In4.Cu", w=0.1):
    return {"uuid": "u%d" % n, "net": net, "layer": layer, "width": w,
            "start": tuple(map(float, a)), "end": tuple(map(float, b))}


def tooth_on_rail(offset=-1.0, n_teeth=2, rail=5.0, height=4.0):
    """A triangle wave hanging its tips off `rail`, free ends ON the rail.

    This is the real geometry: the copper runs along `rail` and the teeth stick
    out to `offset`. Both ends of the chain are rail points, so the chord that
    matters is the rail - not the tip line.
    """
    step = height / (2.0 * n_teeth)
    pts = [(rail, 0.0)]
    for k in range(n_teeth):
        y0 = 2 * k * step
        pts.append((rail + offset, y0 + step))
        pts.append((rail, y0 + 2 * step))
    return [leg(i, pts[i], pts[i + 1]) for i in range(len(pts) - 1)]


class ChainShapeTest(unittest.TestCase):
    def test_the_chord_is_the_rail_not_the_tip_line(self):
        """取顶点当弦的旧写法会把整条波整体挪 1 mm。"""
        m = ar().Model(FakeBoard(tooth_on_rail(offset=-1.0)))
        chs = ar().chains(m)
        self.assertEqual(len(chs), 1, chs)
        ch = chs[0]
        self.assertAlmostEqual(ch["start"][0], 5.0, places=6)
        self.assertAlmostEqual(ch["end"][0], 5.0, places=6)
        self.assertEqual({round(ch["start"][1], 6), round(ch["end"][1], 6)},
                         {0.0, 4.0})
        self.assertAlmostEqual(ch["amp_mm"], 1.0, places=6)
        self.assertEqual(ch["side"], -1)
        self.assertAlmostEqual(ch["chord_mm"], 4.0, places=6)
        # four legs of sqrt(2) hang off a 4 mm chord
        self.assertAlmostEqual(ch["extra_mm"], 4 * math.sqrt(2) - 4.0, places=6)

    def test_a_chain_that_crosses_its_own_chord_is_not_a_meander(self):
        """对照：顶点跨到弦两侧的链不是「挂在轨道上的齿」，不许被当成一条链。

        注意三角形波（峰值只在弦一侧）是**合格**的——三角齿恰恰是这种形状，
        这次要重画的正是它。被排除的是「穿到弦另一边」的那种。
        """
        pts = [(5, 0), (6, 1), (5, 2), (4, 3), (5, 4)]
        m = ar().Model(FakeBoard([leg(i, pts[i], pts[i + 1])
                                  for i in range(len(pts) - 1)]))
        self.assertEqual(ar().chains(m), [])

    def test_a_triangle_wave_is_exactly_what_this_tool_looks_for(self):
        """同一条形状的另一半：三角齿（峰值都在弦一侧）必须被认出来。"""
        pts = [(5, 0), (6, 1), (7, 0), (8, 1), (9, 0)]
        m = ar().Model(FakeBoard([leg(i, pts[i], pts[i + 1])
                                  for i in range(len(pts) - 1)]))
        chs = ar().chains(m)
        self.assertEqual(len(chs), 1, chs)
        self.assertAlmostEqual(chs[0]["amp_mm"], 1.0, places=6)

    def test_a_45_degree_joint_breaks_the_chain(self):
        """对照：链只在 90 度关节处延伸；45 度关节处必须停下（这里剩不到 3 条腿）。"""
        pts = [(5, 0), (6, 0), (6.5, 0.5), (7.5, 0.5), (8, 1.0)]
        m = ar().Model(FakeBoard([leg(i, pts[i], pts[i + 1])
                                  for i in range(len(pts) - 1)]))
        self.assertEqual(ar().chains(m), [])

    def test_touches_counts_only_other_objects(self):
        board = FakeBoard(tooth_on_rail())
        board.tracks.append(leg(99, (5, 0), (5, -1)))
        m = ar().Model(board)
        mine = [t["uuid"] for t in board.tracks[:4]]
        self.assertEqual(m.touches("/N", "In4.Cu", (5.0, 0.0), mine), 1)
        self.assertEqual(m.touches("/N", "In4.Cu", (5.0, 0.0)), 2)
        self.assertEqual(m.touches("/N", "In4.Cu", (5.0, 5.0), mine), 0)


class PlanTest(unittest.TestCase):
    def setUp(self):
        self.mod = ar()

    def _board(self, stub=True, foreign=None):
        tracks = tooth_on_rail()
        if stub:
            tracks.append(leg(90, (5, 0), (5, -1)))
            tracks.append(leg(91, (5, 4), (5, 5)))
        if foreign:
            tracks.append(foreign)
        return FakeBoard(tracks)

    def test_plan_closes_the_length_and_joins_end_to_end(self):
        board = self._board()
        ch = self.mod.chains(self.mod.Model(board))[0]
        self.assertEqual(ch["attach_connected"], [True, True])
        plan, why = self.mod.plan_chain(ch, self.mod.Copper(board))
        self.assertIsNone(why, why)
        pieces = plan["pieces"]
        # 1. consecutive pieces share their endpoints exactly
        for k in range(len(pieces) - 1):
            self.assertAlmostEqual(pieces[k][-1][0], pieces[k + 1][1][0], places=6)
            self.assertAlmostEqual(pieces[k][-1][1], pieces[k + 1][1][1], places=6)
        # 2. the wave starts and ends where the chain did
        self.assertAlmostEqual(pieces[0][1][0], ch["start"][0], places=6)
        self.assertAlmostEqual(pieces[0][1][1], ch["start"][1], places=6)
        self.assertAlmostEqual(pieces[-1][-1][0], ch["end"][0], places=6)
        self.assertAlmostEqual(pieces[-1][-1][1], ch["end"][1], places=6)
        # 3. the length is the same number, and at least one piece is a real arc
        after = sum(self.mod._piece_len(p) for p in pieces)
        self.assertAlmostEqual(after, ch["length_mm"], delta=self.mod.LEN_TOL)
        self.assertTrue(any(p[0] == "arc" for p in pieces), pieces)
        # 4. the wave does not reach further than the copper it replaces
        amp = 0.0
        for p in pieces:
            for pt in (p[1], p[-1]):
                amp = max(amp, abs(pt[0] - ch["start"][0]))
        self.assertLessEqual(amp, ch["amp_mm"] + self.mod.AMP_SLACK + 1e-9)

    def test_ops_conservation(self):
        board = self._board()
        ch = self.mod.chains(self.mod.Model(board))[0]
        plan, why = self.mod.plan_chain(ch, self.mod.Copper(board))
        self.assertIsNone(why, why)
        ops = self.mod.ops_for(ch, plan)
        dels = [o for o in ops if o["op"] == "delete_track"]
        adds = [o for o in ops if o["op"] in ("add_track", "add_track_arc")]
        self.assertEqual(len(dels), len(ch["uuids"]))
        self.assertEqual({o["uuid"] for o in dels}, set(ch["uuids"]))
        self.assertEqual(len(adds), len(plan["pieces"]))
        self.assertEqual(len(adds), len(ops) - len(dels))
        for o in adds:
            self.assertEqual(o["layer"], ch["layer"])
            self.assertEqual(o["net"], ch["net"])

    def test_a_floating_end_is_refused(self):
        """对照：没有留下铜去接的那一端必须拒绝，不能只看长度。"""
        board = self._board(stub=False)
        ch = self.mod.chains(self.mod.Model(board))[0]
        self.assertEqual(ch["attach_connected"], [False, False])
        plan, why = self.mod.plan_chain(ch, self.mod.Copper(board))
        self.assertIsNone(plan)
        self.assertEqual(why["reason"], "attach_floating")

    def test_one_floating_end_is_enough_to_refuse(self):
        tracks = tooth_on_rail() + [leg(90, (5, 0), (5, -1))]
        board = FakeBoard(tracks)
        ch = self.mod.chains(self.mod.Model(board))[0]
        self.assertEqual(sorted(ch["attach_connected"]), [False, True])
        plan, why = self.mod.plan_chain(ch, self.mod.Copper(board))
        self.assertIsNone(plan)
        self.assertEqual(why["reason"], "attach_floating")

    def test_foreign_copper_across_the_corridor_is_refused(self):
        foreign = leg(95, (3.0, 2.0), (5.6, 2.0), net="/OTHER")
        board = self._board(foreign=foreign)
        ch = self.mod.chains(self.mod.Model(board))[0]
        plan, why = self.mod.plan_chain(ch, self.mod.Copper(board))
        self.assertIsNone(plan)
        self.assertEqual(why["reason"], "clearance", why)
        self.assertLess(why["gap_mm"], self.mod.CLR)

    def test_same_net_copper_is_not_a_clearance_hit(self):
        """对照：同网的走线不参与净空判断，否则每条链都会被自己邻居拒掉。"""
        friend = leg(96, (3.0, 2.0), (5.6, 2.0), net="/N")
        board = self._board(foreign=friend)
        ch = self.mod.chains(self.mod.Model(board))[0]
        plan, why = self.mod.plan_chain(ch, self.mod.Copper(board))
        self.assertIsNone(why, why)

    def test_locked_copper_is_refused(self):
        tracks = tooth_on_rail()
        tracks[2]["locked"] = True
        board = FakeBoard(tracks + [leg(90, (5, 0), (5, -1)),
                                    leg(91, (5, 4), (5, 5))])
        ch = self.mod.chains(self.mod.Model(board))[0]
        plan, why = self.mod.plan_chain(ch, self.mod.Copper(board))
        self.assertIsNone(plan)
        self.assertEqual(why["reason"], "copper_is_locked")


class RealBoardTest(unittest.TestCase):
    """这次修的真现场：32 条 dangling 就是在这一条链上长出来的。"""

    @classmethod
    def setUpClass(cls):
        from pcb_agent_mcp.adapters import open_board
        if not os.path.isfile(BOARD):
            raise unittest.SkipTest("rk3506_som board is not in this checkout")
        cls.board = open_board(BOARD)
        cls.chains = ar().chains(ar().Model(cls.board))

    def _dq0(self):
        """/DDR_DQ0 has two chains on In4.Cu (a vertical rail and one on the
        45-degree run); the vertical one is the chain that broke."""
        got = [c for c in self.chains
               if c["net"] == "/DDR_DQ0" and c["layer"] == "In4.Cu"
               and abs(c["start"][0] - c["end"][0]) < 1e-6]
        self.assertEqual(len(got), 1, [c["net"] for c in self.chains])
        return got[0]

    def test_dq0_chord_sits_on_the_copper_line(self):
        ch = self._dq0()
        for pt in (ch["start"], ch["end"]):
            self.assertAlmostEqual(pt[0], 21.843, delta=1e-3)
        # the teeth tips are 0.255 mm in from the rail - the old chord line
        self.assertAlmostEqual(ch["amp_mm"], 0.255, delta=1e-3)

    def test_dq0_ends_land_on_copper_that_stays(self):
        ch = self._dq0()
        self.assertEqual(ch["attach_connected"], [True, True])

    def test_the_whole_board_keeps_its_meander_chains(self):
        """35 条链是这块板的读数；改成 0 条说明横向判据被放松了。"""
        self.assertGreaterEqual(len(self.chains), 30, len(self.chains))
        nets = {c["net"] for c in self.chains}
        self.assertIn("/DDR_DQ0", nets)
        self.assertIn("/DDR_A0", nets)

    def test_a_bad_chain_would_have_been_caught(self):
        """反向对照：把弦挪到齿尖线上，长度照样对，但两端立刻不接铜。

        这条断言是这次 32 条 track_dangling 的缩影：长度判据对「弦在错线」
        完全不敏感，只有 attach_connected 会变红。
        """
        ch = self._dq0()
        tip = [pt for pt in (ch["start"], ch["end"])]
        # simulate the old chord: shift both ends by the amplitude, tip-ward
        shifted = [(x - ch["amp_mm"], y) for (x, y) in tip]
        m = ar().Model(self.board)
        for pt in shifted:
            self.assertEqual(m.touches(ch["net"], ch["layer"], pt), 0,
                             "a tip-line endpoint must meet nothing, got %r" % (pt,))


# ---- two precisions: keys at 1 um, geometry at the file's own -----------------

STAIR = [(20.153553, 13.553553), (20.153553, 13.914178),
         (20.514178, 13.914178), (20.514178, 14.274802),
         (20.874802, 14.274802), (20.874802, 14.635427),
         (21.235427, 14.635427)]

class GeometryPrecisionTest(unittest.TestCase):
    """The chain's LENGTH must be the copper's, not the key grid's.

    `/DDR_DQ0` on rk3506_som carries a six-leg 45-degree staircase whose ends sit
    on 1 um coordinates. Measuring it on the 1 um grid the junction test uses
    read 2.162000 mm where the file's own copper is 2.163748 mm: a 1.748e-03 mm
    hole in a check whose tolerance is 1e-4, and the net then looked as if the
    REWRITE had changed its length when only the measurement had.
    """

    def _board(self, pts):
        return FakeBoard([leg(i, pts[i], pts[i + 1]) for i in range(len(pts) - 1)])

    def test_chain_length_uses_the_file_coordinates(self):
        m = ar().Model(self._board(STAIR))
        chs = ar().chains(m)
        self.assertEqual(len(chs), 1, chs)
        ch = chs[0]
        exact = sum(math.hypot(STAIR[i + 1][0] - STAIR[i][0],
                               STAIR[i + 1][1] - STAIR[i][1])
                    for i in range(len(STAIR) - 1))
        self.assertAlmostEqual(ch["length_mm"], exact, places=6)
        self.assertAlmostEqual(ch["chord_mm"],
                               math.hypot(STAIR[-1][0] - STAIR[0][0],
                                          STAIR[-1][1] - STAIR[0][1]), places=6)
        self.assertAlmostEqual(ch["amp_mm"], 0.255, places=3)

    def test_the_1um_grid_would_have_read_a_different_length(self):
        """反向对照：把顶点先取整到 1 um，长度就变了——这正是修之前那 1.7e-3 的来源。"""
        rounded = [(round(x, 3), round(y, 3)) for x, y in STAIR]
        exact = sum(math.hypot(STAIR[i + 1][0] - STAIR[i][0],
                               STAIR[i + 1][1] - STAIR[i][1])
                    for i in range(len(STAIR) - 1))
        on_grid = sum(math.hypot(rounded[i + 1][0] - rounded[i][0],
                                 rounded[i + 1][1] - rounded[i][1])
                      for i in range(len(rounded) - 1))
        self.assertGreater(abs(on_grid - exact), 1e-4,
                           "a 1 um grid must move this length by more than the "
                           "1e-4 tolerance or this test proves nothing")
        m = ar().Model(self._board(STAIR))
        ch = ar().chains(m)[0]
        anew = abs(ch["length_mm"] - exact)
        agrid = abs(ch["length_mm"] - on_grid)
        self.assertLess(anew, 1e-6, "the chain length must be the file's")
        self.assertGreater(agrid, 1e-4, "and it must NOT read as the grid's")

    def test_the_chord_ends_are_file_points_not_grid_points(self):
        m = ar().Model(self._board(STAIR))
        ch = ar().chains(m)[0]
        for pt in (ch["start"], ch["end"]):
            self.assertTrue(any(abs(pt[0] - p[0]) < 1e-9 and abs(pt[1] - p[1]) < 1e-9
                                for p in STAIR), pt)

if __name__ == "__main__":
    unittest.main()
