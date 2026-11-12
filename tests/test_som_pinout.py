# -*- coding: utf-8 -*-
"""RK3506 SOM 140 pin 引脚分配的规则测试。

每一条判据都必须有**反向失败对照**：故意把表改坏，断言同一套审计会报错。
只断言「通过」是不够的——那种测试在审计函数被写成 return [] 之后也会全绿。
"""
import copy
import importlib.util
import os
import sys
import unittest

REPO = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, os.path.join(REPO, "src"))

_spec = importlib.util.spec_from_file_location(
    "gen_som_pinout", os.path.join(REPO, "tools", "gen_som_pinout.py"))
G = importlib.util.module_from_spec(_spec)
_spec.loader.exec_module(G)


class TestSOMpinout(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.rows, cls.pm = G.build()
        cls.errs, cls.stat = G.audit(cls.rows, cls.pm)

    def _mut(self):
        return copy.deepcopy(self.rows)

    def _renum(self, rows):
        for k, r in enumerate(rows, 1):
            r["pin"] = k
        return rows


    def _expect_fail(self, rows, needle):
        errs, _ = G.audit(rows, self.pm)
        self.assertTrue(any(needle in e for e in errs),
                        "改坏后审计没报 %r，实际 errs=%r" % (needle, errs))

    # ---- 正向：设计本身必须全条通过
    def test_all_rules_pass(self):
        self.assertEqual(self.errs, [], "设计未通过审计：%r" % (self.errs,))

    def test_counts(self):
        self.assertEqual(len(self.rows), G.PIN_TOTAL)
        self.assertEqual(self.stat["sig"], 95)
        self.assertEqual(self.stat["power"], 8)
        self.assertEqual(self.stat["gnd"], 37)
        self.assertEqual(self.stat["sig"] + self.stat["power"] + self.stat["gnd"], G.PIN_TOTAL)

    def test_edges_match_reference(self):
        # 2x30 + 2x40 = 140 @1.0mm（与参考 SOM 同构）
        self.assertEqual(G.EDGE_SLOTS, {"N": 40, "E": 30, "S": 40, "W": 30})
        for e, n in G.EDGE_SLOTS.items():
            self.assertEqual(sum(1 for r in self.rows if r["edge"] == e), n)

    def test_ball_set_closure(self):
        sig = {r["ball"] for r in self.rows if r["kind"] == "signal"}
        self.assertEqual(len(sig), 95)
        # 95 = 161 信号球 - 55 DDR3L - 11 内部占用
        self.assertEqual(161 - 55 - len(G.INTERNAL), 95)

    def test_gnd_density(self):
        self.assertLessEqual(self.stat["worst_run"], G.MAX_RUN)
        for e in "NESW":
            self.assertGreaterEqual(self.stat["gnd_per_edge"][e], 3)

    def test_geometry(self):
        self.assertGreaterEqual(self.stat["pad_gap_mm"], 0.15)
        for e, span in self.stat["span_mm"].items():
            self.assertLessEqual(span, 45.0 - 2 * G.CORNER_KEEPOUT_MM)

    # ---- 反向对照：改坏必须报错
    def test_r2_reverse_gnd_inside_pair(self):
        rows = self._mut()
        i = next(k for k, r in enumerate(rows) if r["net"] == "USB0_DN")
        rows.insert(i, dict(pin=0, edge="E", net="GND", ball="", group="PWR", kind="gnd", note=""))
        self._renum(rows)
        self._expect_fail(rows, "R2 差分对 USB0_D")

    def test_r2_reverse_pair_split(self):
        rows = self._mut()
        m = next(r for r in rows if r["net"] == "MIPI_DSI_CLKN")
        rows.remove(m)
        tail = max(k for k, r in enumerate(rows) if r["edge"] == "E")
        rows.insert(tail, m)
        self._renum(rows)
        self._expect_fail(rows, "R2 差分对 MIPI_DSI_CLK")

    def test_r1_reverse_group_cross_edge(self):
        rows = self._mut()
        r = next(r for r in rows if r["net"] == "SAI0_LRCK")
        r["edge"] = "N"
        self._expect_fail(rows, "R1 接口 SAI0 跨边")

    def test_r1_reverse_group_interleaved(self):
        rows = self._mut()
        # 把一个 SPI0 脚插进 SPI1 组中间 -> 两个接口互相插入
        a = next(r for r in rows if r["net"] == "SPI0_CLK")
        rows.remove(a)
        i = next(k for k, r in enumerate(rows) if r["net"] == "SPI1_MOSI")
        rows.insert(i, a)
        self._renum(rows)
        self._expect_fail(rows, "R1 接口")

    def test_r3_reverse_too_few_gnd(self):
        rows = self._mut()
        # 只留 6 个 GND：37 -> 6，必然出现 >6 的无地长段
        gnd = [r for r in rows if r["kind"] == "gnd"]
        for g in gnd[6:]:
            rows.remove(g)
        self._renum(rows)
        self._expect_fail(rows, "R3 最长非 GND 段")

    def test_r5_reverse_ddr_ball(self):
        rows = self._mut()
        r = next(r for r in rows if r["kind"] == "signal")
        r["ball"] = "Y12"  # DDR_DQ1，R5 明令不外引
        self._expect_fail(rows, "R5 外引球集合不符")

    def test_r5_reverse_duplicate(self):
        rows = self._mut()
        a = next(r for r in rows if r["net"] == "SPI0_CLK")
        b = next(r for r in rows if r["net"] == "SPI0_MOSI")
        b["ball"] = a["ball"]
        self._expect_fail(rows, "R5 球重复")

    def test_r6_reverse_power_beside_usb(self):
        rows = self._mut()
        m = next(r for r in rows if r["kind"] == "power")
        rows.remove(m)
        i = next(k for k, r in enumerate(rows) if r["net"] == "USB1_DP")
        rows.insert(i, m)
        self._renum(rows)
        self._expect_fail(rows, "R6 电源脚")

    def test_r7_reverse_pad_gap(self):
        old = G.PAD_D_MM
        try:
            G.PAD_D_MM = 0.95  # 间隙 0.05mm，做不出来
            errs, stat = G.audit(self.rows, self.pm)
        finally:
            G.PAD_D_MM = old
        self.assertTrue(any("R7 焊盘间隙" in e for e in errs), errs)

    def test_r7_reverse_hole_ring(self):
        old = G.HOLE_D_MM
        try:
            G.HOLE_D_MM = 0.70  # 环 0.075mm，低于「孔径+0.30」
            errs, _ = G.audit(self.rows, self.pm)
        finally:
            G.HOLE_D_MM = old
        self.assertTrue(any("R7 半孔环" in e for e in errs), errs)

    def test_r0_reverse_edge_order(self):
        rows = self._mut()
        rows[0]["edge"], rows[-1]["edge"] = rows[-1]["edge"], rows[0]["edge"]
        self._expect_fail(rows, "R0 边序")

    def test_r0_reverse_missing_pin(self):
        rows = self._mut()
        rows.pop(10)
        self._expect_fail(rows, "R0 脚数")


if __name__ == "__main__":
    unittest.main()
