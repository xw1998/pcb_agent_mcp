# -*- coding: utf-8 -*-
"""tools/gen_smd_footprint.py / tools/gen_lceda_symbol.py / lceda_fp 的通用用例。

每条审计（G1-G8、S1-S2）都要有一条"故意弄坏它"的反向对照，否则把 audit
写成 ``return []`` 也全绿。正向里最有价值的一条不来自本仓库：3225 的焊盘几何
与 KiCad 官方 ``Crystal_SMD_3225-4Pin_3.2x2.5mm`` 逐值相同，那是外部尺子。
Nothing here is a route; it is a package-geometry check.
"""

import importlib.util
import json
import os
import shutil
import sys
import tempfile
import unittest

HERE = os.path.dirname(os.path.abspath(__file__))
REPO = os.path.dirname(HERE)
TOOLS = os.path.join(REPO, "tools")
LIB = os.path.join(REPO, "examples", "rk3506_som", "lib")
PRETTY = os.path.join(LIB, "RK3506.pretty")
DOC24 = os.path.join(REPO, "examples", "rk3506_som", "library", "XTAL_24M_X322524MSB4SI_3225.efp")
DOC32 = os.path.join(REPO, "examples", "rk3506_som", "library", "XTAL_32K_NX2012SA_2012.efp")
DOCSW = os.path.join(REPO, "examples", "rk3506_som", "library", "SW_SKRPACE010_4.2x3.2.efp")

FP24 = "Package_Crystal_SMD_3225-4P_L3.2-W2.5_X322524MSB4SI"
FP32 = "Package_Crystal_SMD_2012-2P_L2.05-W1.2_NX2012SA"
FPSW = "Package_Switch_SMD_4.2x3.2-4P-P2.20_SKRPACE010"

_MOD = None

def _gen():
    global _MOD
    if _MOD is None:
        sys.path.insert(0, TOOLS)
        spec = importlib.util.spec_from_file_location(
            "gen_smd_footprint", os.path.join(TOOLS, "gen_smd_footprint.py"))
        mod = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(mod)
        _MOD = mod
    return _MOD

def _doc_text(path):
    with open(path, "r", encoding="utf-8") as fh:
        return fh.read()

class _TmpDoc(object):
    """把改坏后的文档写到临时文件，退出时删掉。"""

    def __init__(self, text):
        self.text = text
        self.dir = None

    def __enter__(self):
        self.dir = tempfile.mkdtemp(prefix="smdfp_")
        self.path = os.path.join(self.dir, "doc.efp")
        with open(self.path, "w", encoding="utf-8") as fh:
            fh.write(self.text)
        return self.path

    def __exit__(self, *exc):
        shutil.rmtree(self.dir, ignore_errors=True)
        return False

def _rewrite(text, fn):
    out = []
    for line in text.splitlines():
        if not line.strip():
            continue
        row = json.loads(line)
        new = fn(row)
        if new is not None:
            out.append(json.dumps(new, ensure_ascii=False))
    return "\n".join(out) + "\n"

def _patch_pad(text, number, **changes):
    """``i5="1"`` 之类把 PAD 行的第 5 个下标改成给定的值。"""
    def fn(row):
        if isinstance(row, list) and row and row[0] == "PAD" and str(row[5]) == number:
            for key, val in changes.items():
                row[int(key[1:])] = val
        return row
    return _rewrite(text, fn)

def _drop_pad(text, number):
    return _rewrite(text, lambda row: None if (
        isinstance(row, list) and row and row[0] == "PAD" and str(row[5]) == number
    ) else row)

def _scale_pads(text, factor):
    """把每个 PAD 的 x/y 同乘一个系数：几何关系不变，绝对尺寸越界。"""
    def fn(row):
        if isinstance(row, list) and row and row[0] == "PAD":
            row[6] = row[6] * factor
            row[7] = row[7] * factor
        return row
    return _rewrite(text, fn)

def _drop_layer(text, layer):
    def fn(row):
        if isinstance(row, list) and len(row) > 5 and row[4] == layer:
            return None
        return row
    return _rewrite(text, fn)

class _Base(unittest.TestCase):
    def setUp(self):
        self.g = _gen()
        self.L = self.g.lceda_fp

    def rows(self, path):
        return self.L.read_rows(path)

    def build(self, path, body, pads, grid=None, tol=0.03, name="X", descr="d", tags="t"):
        return self.g.build(self.rows(path), name, body, pads, descr, tags, grid, tol)

    def fails(self, text, expect, body, pads, grid=None, tol=0.03):
        """build 必须以 ``expect`` 这条审计号失败。"""
        with _TmpDoc(text) as path:
            with self.assertRaises(SystemExit) as ctx:
                self.g.build(self.rows(path), "X", body, pads, "d", "t", grid, tol)
            self.assertIn(expect, str(ctx.exception))

    def geom_error(self, text, fn):
        with _TmpDoc(text) as path:
            rows = self.rows(path)
            with self.assertRaises(self.L.GeometryError) as ctx:
                fn(rows)
            return str(ctx.exception)

class ScaleTest(_Base):
    """G2：单位是解出来的，不是读 CANVAS 的。"""

    def test_three_documents_solve_to_mil(self):
        for path, grid in ((DOC24, 1.7), (DOC32, 1.5001), (DOCSW, 2.1499)):
            rows = self.rows(path)
            info = self.g.solve_scale(rows, self.L.pad_rows(rows))
            self.assertEqual(info["unit"], "mil", path)
            self.assertAlmostEqual(info["scale"], 0.0254, places=9)
            self.assertAlmostEqual(info["grid_mm"], grid, places=3)

    def test_reverse_no_unit_fits_the_envelope(self):
        """整张图放大 100 倍：mil 的网格 170 mm 越 8 mm 上界，mm 的本体 126 mm 越 60 mm ⇒ 0 命中。"""
        text = _scale_pads(_doc_text(DOC24), 100)
        msg = self.geom_error(text, lambda rows: self.g.solve_scale(rows, self.L.pad_rows(rows)))
        self.assertIn("not solvable", msg)

    def test_reverse_grid_not_measurable(self):
        """把 2012 的第二脚挪出同一行，就没有共享行/列 ⇒ 网格步长测不出来。"""
        text = _patch_pad(_doc_text(DOC32), "2", i7=50)
        msg = self.geom_error(text, lambda rows: self.g.solve_scale(rows, self.L.pad_rows(rows)))
        self.assertIn("not measurable", msg)

class PadBoxTest(_Base):
    """G5：焊盘轴向按网格解，不照抄 rot 位。"""

    def test_3225_rot_zero_single_assignment(self):
        rows = self.rows(DOC24)
        pad = [p for p in self.L.pad_rows(rows) if p["num"] == "1"][0]
        self.assertEqual(pad["rot"], 0)
        box = self.L.resolve_pad_box(pad, 0.0254, 1.7)
        self.assertAlmostEqual(box["w_mm"], 1.4, places=4)
        self.assertAlmostEqual(box["h_mm"], 1.2, places=4)
        self.assertEqual(box["rejected"], [])

    def test_2012_rot90_tiebreak_picks_narrow_along_pitch(self):
        """rot=90 两解都活（1.0 与 1.2 顺 pitch 都能留 ≥0.15 间隙）⇒ 取窄轴。"""
        rows = self.rows(DOC32)
        pad = [p for p in self.L.pad_rows(rows) if p["num"] == "1"][0]
        self.assertEqual(pad["rot"], 90)
        box = self.L.resolve_pad_box(pad, 0.0254, 1.5001)
        self.assertAlmostEqual(box["w_mm"], 1.0, places=4)
        self.assertAlmostEqual(box["h_mm"], 1.2, places=4)
        self.assertEqual(len(box["rejected"]), 1)
        self.assertAlmostEqual(box["rejected"][0][0], 1.2, places=4)

    def test_reverse_both_assignments_press_the_pitch(self):
        """把焊盘做成 5.08 x 5.08 ⇒ 两种轴向都留不出 0.15 ⇒ 不许猜。"""
        text = _patch_pad(_doc_text(DOC24), "1", i10=["OVAL", 200, 200])
        def fn(rows):
            pad = [p for p in self.L.pad_rows(rows) if p["num"] == "1"][0]
            self.L.resolve_pad_box(pad, 0.0254, 1.7)
        msg = self.geom_error(text, fn)
        self.assertIn("not resolvable", msg)

class AuditTest(_Base):
    """G1/G3/G4/G6/G7/G8 的反向对照 + 真实文档必须全过。"""

    def test_real_documents_pass_every_audit(self):
        for path, body, pads, grid in ((DOC24, (3.2, 2.5), 4, 1.7),
                                       (DOC32, (2.05, 1.2), 2, 1.5001),
                                       (DOCSW, (4.2, 3.2), 4, 2.2)):
            text, report, specs = self.build(path, body, pads, grid,
                                             tol=0.06 if path is DOCSW else 0.03)
            self.assertEqual(report["audits"], "G1-G8 passed")
            self.assertEqual(len(specs), pads)

    def test_reverse_pad_count(self):
        self.fails(_doc_text(DOC24), "G1", (3.2, 2.5), 5)

    def test_reverse_duplicate_pad_number(self):
        text = _patch_pad(_doc_text(DOC24), "2", i5="1")
        self.fails(text, "G1 duplicate pad numbers", (3.2, 2.5), 4)

    def test_reverse_empty_pad_number(self):
        text = _patch_pad(_doc_text(DOC24), "2", i5="")
        self.fails(text, "G1 empty pad number", (3.2, 2.5), 4)

    def test_reverse_body_size(self):
        """本体是画出来的多边形，标题是独立字符串：拿错标题必须报 G3。"""
        self.fails(_doc_text(DOC24), "G3 body", (3.5, 2.5), 4)

    def test_reverse_duplicate_coordinate(self):
        text = _patch_pad(_doc_text(DOC24), "2", i6=-43.31)
        self.fails(text, "G4 duplicate pad coordinates", (3.2, 2.5), 4)

    def test_reverse_off_centre(self):
        text = _patch_pad(_doc_text(DOC24), "1", i6=-80)
        self.fails(text, "G4 pad centroid", (3.2, 2.5), 4)

    def test_reverse_pad_leaves_courtyard(self):
        text = _patch_pad(_doc_text(DOC24), "3", i6=200)
        self.fails(text, "G6 pad 3 leaves", (3.2, 2.5), 4)

    def test_reverse_no_silkscreen(self):
        self.fails(_drop_layer(_doc_text(DOC24), 3), "G7 no silkscreen", (3.2, 2.5), 4)

    def test_reverse_grid_mismatch(self):
        self.fails(_doc_text(DOC24), "G8 grid step", (3.2, 2.5), 4, grid=1.27)

    def test_grid_tolerance_exists_because_titles_round(self):
        """SKRPACE010 标题写 P2.20、实际画 2.15：默认 0.03 会误报，写明理由放宽到 0.06 才对。"""
        self.fails(_doc_text(DOCSW), "G8 grid step", (4.2, 3.2), 4, grid=2.2)
        text, report, _s = self.build(DOCSW, (4.2, 3.2), 4, 2.2, tol=0.06)
        self.assertAlmostEqual(report["grid_mm"], 2.15, places=2)
        self.assertEqual(report["grid_expected"], [2.2, 0.06])

class OfficialLibraryCrossCheck(unittest.TestCase):
    """外部尺子：KiCad 10.0 自带封装，2026-11-12 读自
    ``D:\\KiCad\\10.0\\share\\kicad\\footprints\\Crystal.pretty``。"""

    KICAD_3225_4PIN = [("1", -1.1, 0.85, 1.4, 1.2), ("2", 1.1, 0.85, 1.4, 1.2),
                       ("3", 1.1, -0.85, 1.4, 1.2), ("4", -1.1, -0.85, 1.4, 1.2)]
    KICAD_2012_2PIN = [("1", -0.7, 0.0, 0.6, 1.1), ("2", 0.7, 0.0, 0.6, 1.1)]
    KICAD_ALPS_SKRK = [("1", -2.1, 0.0, 0.8, 2.0), ("2", 2.1, 0.0, 0.8, 2.0)]

    def setUp(self):
        self.g = _gen()
        self.L = self.g.lceda_fp

    def _specs(self, path, body, pads, grid, tol=0.03):
        return self.g.build(self.rows(path), "X", body, pads, "d", "t", grid, tol)[2]

    def rows(self, path):
        return self.L.read_rows(path)

    def test_3225_pad_geometry_equals_kicad_official(self):
        """LCEDA 的 3225 land 与 KiCad 官方逐值相同（位置与尺寸都不差 1 µm）。"""
        specs = self._specs(DOC24, (3.2, 2.5), 4, 1.7)
        got = sorted((n, x, y, w, h) for (n, x, y, w, h, _s) in specs)
        want = sorted(self.KICAD_3225_4PIN)
        for g, w in zip(got, want):
            self.assertEqual(g[0], w[0])
            for i in (1, 2, 3, 4):
                self.assertAlmostEqual(g[i], w[i], places=3, msg="pad %s" % g[0])

    def test_2012_x_pitch_and_pad_size_differ_but_are_legal(self):
        """2012 两家库不同：LCEDA 1.0x1.2 压 1.5 pitch，KiCad 0.6x1.1 压 1.4。

        不是冲突而是两家各自的 land，故这里只断言"我们的值来自文档"，
        并把官方值作为参照披露，绝不擅自把 LCEDA 焊盘改成 KiCad 的。
        """
        specs = self._specs(DOC32, (2.05, 1.2), 2, 1.5001)
        by_num = {n: (x, y, w, h) for (n, x, y, w, h, _s) in specs}
        self.assertAlmostEqual(by_num["1"][2], 1.0, places=4)
        self.assertAlmostEqual(by_num["1"][3], 1.2, places=4)
        self.assertAlmostEqual(abs(by_num["1"][0]), 0.75, places=3)
        self.assertAlmostEqual(by_num["1"][2], self.KICAD_2012_2PIN[0][3] + 0.4, places=4)

    def test_switch_x_pitch_equals_alps_reference(self):
        """SKRPACE010 的 x 间距 4.2 与 Alps SKRK 官方库的 2x2.1 一致。"""
        specs = self._specs(DOCSW, (4.2, 3.2), 4, 2.2, tol=0.06)
        xs = sorted({round(abs(x), 3) for (_n, x, _y, _w, _h, _s) in specs})
        self.assertEqual(xs, [2.1])
        self.assertAlmostEqual(abs(self.KICAD_ALPS_SKRK[0][1]), 2.1, places=4)

class SourceNoteTest(unittest.TestCase):
    """立创官方库 HEAD 里的许可要求出处声明：所有产物都必须带上，且只能带一次。"""

    def test_note_is_identical_in_both_copies(self):
        sys.path.insert(0, TOOLS)
        sys.path.insert(0, os.path.join(REPO, "src"))
        import lceda_fp                                        # noqa: E402
        from pcb_agent_mcp import parts                         # noqa: E402
        self.assertEqual(lceda_fp.SOURCE_NOTE, parts.SOURCE_NOTE)
        self.assertIn("EasyEDA", lceda_fp.SOURCE_NOTE)
        self.assertIn("lceda.cn", lceda_fp.SOURCE_NOTE)

    def test_with_source_is_idempotent(self):
        sys.path.insert(0, TOOLS)
        import lceda_fp                                        # noqa: E402
        once = lceda_fp.with_source("24 MHz crystal")
        twice = lceda_fp.with_source(once)
        self.assertEqual(once, twice)
        self.assertIn("EasyEDA", once)
        self.assertEqual(lceda_fp.with_source(""), lceda_fp.SOURCE_NOTE)

    def test_every_generated_file_declares_the_source(self):
        missing = []
        for name in sorted(os.listdir(PRETTY)):
            if not name.endswith(".kicad_mod"):
                continue
            with open(os.path.join(PRETTY, name), "r", encoding="utf-8") as fh:
                if "EasyEDA" not in fh.read():
                    missing.append(name)
        for name in sorted(os.listdir(LIB)):
            if not name.endswith(".kicad_sym"):
                continue
            with open(os.path.join(LIB, name), "r", encoding="utf-8") as fh:
                if "EasyEDA" not in fh.read():
                    missing.append(name)
        self.assertEqual(missing, [], "这些产物缺出处声明：%s" % missing)

class FootprintTextTest(_Base):
    """产物层面的三条硬形状：KiCad 语法、焊盘数、无 justify center。"""

    def test_24m_text_shape(self):
        text, _r, specs = self.build(DOC24, (3.2, 2.5), 4, 1.7, name=FP24)
        self.assertEqual(text.count('(pad "'), 4)
        self.assertNotIn("(justify center)", text)
        self.assertIn("(version %s)" % self.g.FP_VERSION, text)
        self.assertEqual(text.count('(generator "pcb_agent_mcp")'), 1)
        self.assertEqual(sum(1 for s in specs if s[5] == "rect"), 4)

    def test_switch_text_shape(self):
        text, _r, _s = self.build(DOCSW, (4.2, 3.2), 4, 2.2, tol=0.06, name=FPSW,
                                  descr="Tact switch; library title P2.20 is rounded, drawn 2.15")
        self.assertEqual(text.count('(pad "'), 4)
        self.assertNotIn("(justify center)", text)
        self.assertIn("P2.20 is rounded", text)

    def test_pad_order_is_document_order_not_renumbered(self):
        """2012 文档里 2 号脚排在 1 号前面，产物必须照抄顺序，不许"整理"。"""
        text, _r, _s = self.build(DOC32, (2.05, 1.2), 2, 1.5001, name=FP32)
        self.assertLess(text.index('(pad "2"'), text.index('(pad "1"'))

if __name__ == "__main__":
    unittest.main()
