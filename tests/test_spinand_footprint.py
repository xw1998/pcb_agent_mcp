# -*- coding: utf-8 -*-
"""tools/gen_spinand_footprint.py 与 tools/gen_lceda_symbol.py 的用例。

每条审计都要有一条"故意弄坏它"的对照，否则把 audit 写成 return [] 也全绿。
Nothing here is a route; it is a package-geometry check.
"""

import importlib.util
import json
import os
import shutil
import tempfile
import unittest

HERE = os.path.dirname(os.path.abspath(__file__))
REPO = os.path.dirname(HERE)
GEN = os.path.join(REPO, "tools", "gen_spinand_footprint.py")
SYM = os.path.join(REPO, "tools", "gen_lceda_symbol.py")
_MOD = None
_SYMMOD = None

def _gen():
    global _MOD
    if _MOD is None:
        spec = importlib.util.spec_from_file_location("gen_spinand_footprint", GEN)
        mod = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(mod)
        _MOD = mod
    return _MOD

def _sym():
    global _SYMMOD
    if _SYMMOD is None:
        spec = importlib.util.spec_from_file_location("gen_lceda_symbol", SYM)
        mod = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(mod)
        _SYMMOD = mod
    return _SYMMOD

def _doc_text():
    g = _gen()
    with open(g.DOC, "r", encoding="utf-8") as fh:
        return fh.read()

class _TmpDoc(object):
    """把文档写到临时文件，替换模块里的 DOC 常量，退出时还原。"""

    def __init__(self, text):
        self.text = text

    def __enter__(self):
        g = _gen()
        self.old = g.DOC
        self.dir = tempfile.mkdtemp(prefix="spinand_")
        self.path = os.path.join(self.dir, "doc.efp")
        with open(self.path, "w", encoding="utf-8") as fh:
            fh.write(self.text)
        g.DOC = self.path
        return self.path

    def __exit__(self, *exc):
        _gen().DOC = self.old
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

def _patch_layer_rows(text, layer, fn):
    def inner(row):
        if isinstance(row, list) and len(row) > 5 and row[4] == layer:
            return fn(row)
        return row
    return _rewrite(text, inner)

def _drop_layer(text, layer, kind=None):
    def fn(row):
        if isinstance(row, list) and len(row) > 5 and row[4] == layer:
            if kind is None or row[0] == kind:
                return None
        return row
    return _rewrite(text, fn)

class SpiNandFootprintTest(unittest.TestCase):
    def setUp(self):
        self.g = _gen()

    # ---------------------------------------------------------------- 正向
    def test_scale_solved_against_pitch_and_body(self):
        """文档声明 mm、焊盘还写 rot=90：比例由 1.27 pitch 与 6×8 本体解出来。"""
        rows = self.g.lceda_fp.read_rows(self.g.DOC)
        pads = self.g.lceda_fp.pad_rows(rows)
        info = self.g.solve_scale(pads, self.g.doc_body(rows))
        self.assertEqual(info["unit"], "mil")
        self.assertAlmostEqual(info["pitch_mm"], 1.27, places=3)
        self.assertAlmostEqual(info["body_mm"][0], 6.0, places=3)
        self.assertAlmostEqual(info["body_mm"][1], 8.0, places=3)

    def test_pad_rotation_is_resolved_not_read(self):
        """rot=90 若照字面执行会让 1.40 mm 宽压在 1.27 pitch 上 ⇒ 只剩一种解释。"""
        rows = self.g.lceda_fp.read_rows(self.g.DOC)
        pads = self.g.lceda_fp.pad_rows(rows)
        pin = [p for p in pads if p["num"] == "1"][0]
        box = self.g.lceda_fp.resolve_pad_box(pin, 0.0254, self.g.PITCH_MM)
        self.assertAlmostEqual(box["w_mm"], 0.56, places=3)
        self.assertAlmostEqual(box["h_mm"], 1.40, places=3)
        self.assertEqual(len(box["rejected"]), 1)
        self.assertAlmostEqual(box["rejected"][0][0], 1.40, places=3)

    def test_audit_passes_on_real_document(self):
        rows = self.g.lceda_fp.read_rows(self.g.DOC)
        pads = self.g.lceda_fp.pad_rows(rows)
        body = self.g.doc_body(rows)
        info = self.g.solve_scale(pads, body)
        silk = self.g.silk_segments_mm(rows, info["scale"])
        self.assertEqual(self.g.audit(rows, pads, info, body, silk), [])

    def test_generated_footprint_shape(self):
        text, report, specs, _s = self.g.build(self.g.lceda_fp.read_rows(self.g.DOC))
        self.assertEqual(report["pads"], 9)
        self.assertEqual(len(specs), 9)
        self.assertEqual(text.count('(pad "'), 9)
        self.assertIn("smd oval", text)
        self.assertIn("smd rect", text)
        self.assertNotIn("smd circle", text)
        self.assertNotIn("(justify center)", text)
        self.assertIn('(version %s)' % self.g.FP_VERSION, text)

    def test_silk_and_pin1_mark(self):
        rows = self.g.lceda_fp.read_rows(self.g.DOC)
        s = self.g.solve_scale(self.g.lceda_fp.pad_rows(rows), self.g.doc_body(rows))["scale"]
        marks = self.g.pin1_marks_mm(rows, s)
        self.assertEqual(len(marks), 2, "marking dot + document circle")
        cx, cy, _r = marks[0]
        self.assertAlmostEqual(cx, -3.0, places=3)
        self.assertAlmostEqual(cy, 4.0, places=3)
        text, _r, _s2, _s3 = self.g.build(rows)
        self.assertEqual(text.count("(fp_circle"), 1)

    def test_report_discloses_title_width_and_skips(self):
        _t, report, _s, _sc = self.g.build(self.g.lceda_fp.read_rows(self.g.DOC))
        self.assertAlmostEqual(report["title_width_discrepancy_mm"], 0.1, places=3)
        self.assertEqual(report["skipped"]["via_rows"], 4)
        self.assertEqual(report["body_mm"], [6.0, 8.0])

    # ---------------------------------------------------------------- 反向
    def _fails_with(self, text, expect):
        """build 必须失败，且失败信息里出现 expect 的审计号。"""
        with _TmpDoc(text):
            with self.assertRaises(SystemExit) as ctx:
                self.g.build(self.g.lceda_fp.read_rows(_gen().DOC))
            self.assertIn(expect, str(ctx.exception))

    def test_reverse_scale_no_hit(self):
        """改掉 pitch（10 号位 x 挪到 60）⇒ 两个候选单位都不匹配 ⇒ 拒绝写字节。"""
        text = _patch_pad(_doc_text(), "2", i6=60)
        with _TmpDoc(text):
            with self.assertRaises(self.g.lceda_fp.GeometryError):
                self.g.solve_scale(self.g.lceda_fp.pad_rows(self.g.lceda_fp.read_rows(_gen().DOC)),
                                   self.g.doc_body(self.g.lceda_fp.read_rows(_gen().DOC)))

    def test_reverse_pad_box_unresolvable(self):
        """把焊盘做宽到两种轴分配都压 pitch ⇒ 无法解出，不许猜。"""
        text = _patch_pad(_doc_text(), "1", i10=["OVAL", 100, 100])
        with _TmpDoc(text):
            rows = self.g.lceda_fp.read_rows(_gen().DOC)
            pad = [p for p in self.g.lceda_fp.pad_rows(rows) if p["num"] == "1"][0]
            with self.assertRaises(self.g.lceda_fp.GeometryError):
                self.g.lceda_fp.resolve_pad_box(pad, 0.0254, self.g.PITCH_MM)

    def test_reverse_duplicate_number(self):
        self._fails_with(_patch_pad(_doc_text(), "5", i5="1"), "A1")

    def test_reverse_missing_pad(self):
        self._fails_with(_drop_pad(_doc_text(), "8"), "A1")

    def test_reverse_duplicate_pin_x(self):
        """两个脚叠在同一 x ⇒ pitch 仍可解，但 x 集合不再等间距 ⇒ A3。"""
        self._fails_with(_patch_pad(_doc_text(), "3", i6=-75), "A3")

    def test_reverse_pin_x_positions(self):
        """把一个脚挪 5 个原始单位（0.127 mm）：pitch 仍 1.27，但 x 间距不再对称。"""
        self._fails_with(_patch_pad(_doc_text(), "1", i6=-80), "A3")

    def test_reverse_body_size(self):
        def shrink(row):
            row[6] = [-100.0, -157.48, "L", -100.0, 157.48, 100.0, 157.48,
                      100.0, -157.48, -100.0, -157.48]
            return row
        self._fails_with(_patch_layer_rows(_doc_text(), 48, shrink), "A5")

    def test_reverse_no_body(self):
        text = _drop_layer(_doc_text(), 48)
        with _TmpDoc(text):
            with self.assertRaises(self.g.lceda_fp.GeometryError):
                self.g.doc_body(self.g.lceda_fp.read_rows(_gen().DOC))

    def test_reverse_exposed_pad_not_centred(self):
        self._fails_with(_patch_pad(_doc_text(), "9", i6=10), "A8")

    def test_reverse_exposed_pad_size(self):
        self._fails_with(_patch_pad(_doc_text(), "9", i10=["RECT", 100, 133.858]),
                         "A8")

    def test_reverse_duplicate_coordinate(self):
        self._fails_with(_patch_pad(_doc_text(), "3", i6=-25), "A6")

    def test_reverse_asymmetric_rows(self):
        self._fails_with(_patch_pad(_doc_text(), "5", i7=150), "A6")

    def test_reverse_pad_leaves_courtyard(self):
        self._fails_with(_patch_pad(_doc_text(), "1", i7=210), "A9")

    def test_reverse_silk_margin(self):
        def push(row):
            row[6] = [-121.11, -160.48, "L", -93.524, -160.48]
            return row
        text = _patch_layer_rows(_doc_text(), 3, push)
        self._fails_with(text, "A10")

    # ---------------------------------------------------------------- 交叉
    def test_symbol_pin_numbers_equal_pad_numbers(self):
        """符号 pin 号集合 == 封装焊盘号集合（9 == 9）。"""
        s = _sym()
        fp = os.path.join(self.g.EX, "lib", "RK3506.pretty",
                          "Package_WSON-8_L8.0-W6.1-P1.27_W25N01GVZEIG.kicad_mod")
        with open(os.path.join(self.g.EX, "library",
                               "NAND_W25N01GVZEIG_WSON8_symbol.efp"), encoding="utf-8") as fh:
            text, report = s.build(fh.read(), "W25N01GVZEIG", "U", "", "", "",
                                   s.pad_numbers_of(fp))
        self.assertEqual(report["pins"], 9)
        self.assertEqual(report["pin_numbers"], [str(i) for i in range(1, 10)])
        self.assertEqual(report["cross_check"], "pad set == pin set")

    def test_symbol_has_quad_data_lines(self):
        """四线 SPI 的四个数据脚必须在符号里出现（DQ0..DQ3 或 DI/DO/WP/HOLD）。"""
        s = _sym()
        from pcb_agent_mcp import parts
        with open(os.path.join(self.g.EX, "library",
                               "NAND_W25N01GVZEIG_WSON8_symbol.efp"), encoding="utf-8") as fh:
            doc = parts.parse_symbol_doc(fh.read())
        names = " ".join(p["name"].upper() for p in doc["pins"])
        for token in ("DQ0", "DQ1", "DQ2", "DQ3", "CLK", "CS#"):
            self.assertIn(token, names)
        self.assertEqual(len(doc["pins"]), 9)

if __name__ == "__main__":
    unittest.main()
