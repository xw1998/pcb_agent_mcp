# -*- coding: utf-8 -*-
"""tools/gen_ddr3l_footprint.py 的用例：正向 + 反向失败对照。

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
GEN = os.path.join(REPO, "tools", "gen_ddr3l_footprint.py")
_MOD = None


def _gen():
    global _MOD
    if _MOD is None:
        spec = importlib.util.spec_from_file_location("gen_ddr3l_footprint", GEN)
        mod = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(mod)
        _MOD = mod
    return _MOD


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
        self.dir = tempfile.mkdtemp(prefix="ddr3l_")
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
    """按 JSON 行改写整篇文档；fn(row) 返回新 row 或 None（丢弃）。"""
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


def _patch_body(text, new_points):
    """把 layer 48 的那条闭合折线换成 new_points（扁平 [x,y,"L",...]）。"""
    def fn(row):
        if (isinstance(row, list) and len(row) > 6 and row[0] == "POLY"
                and row[4] == 48 and isinstance(row[6], list)
                and any(isinstance(v, str) for v in row[6])):
            row[6] = new_points
        return row
    return _rewrite(text, fn)


class Ddr3lGeometryTest(unittest.TestCase):
    def setUp(self):
        self.g = _gen()

    # ---------------------------------------------------------------- 正向
    def test_scale_solved_against_pitch_and_body(self):
        """文档声明 mm，真值是 mil：比例由 0.8 pitch 与 9×14 本体解出来。"""
        rows = self.g.lceda_fp.read_rows(self.g.DOC)
        pads = self.g.lceda_fp.pads(rows)
        dx, dy = self.g.expected_spans()
        info = self.g.lceda_fp.solve_scale(pads, dx, dy)
        self.assertEqual(info["unit"], "mil")
        self.assertAlmostEqual(info["dx_mm"], 6.4, places=3)
        self.assertAlmostEqual(info["dy_mm"], 12.0, places=3)

    def test_pitch_spectrum_is_one_value(self):
        rows = self.g.lceda_fp.read_rows(self.g.DOC)
        pads = self.g.lceda_fp.pads(rows)
        s = self.g.lceda_fp.solve_scale(pads, *self.g.expected_spans())["scale"]
        self.assertEqual(len(self.g.lceda_fp.nearest_pitches(pads, s)), 1)

    def test_audit_passes_on_real_document(self):
        rows = self.g.lceda_fp.read_rows(self.g.DOC)
        pads = self.g.lceda_fp.pads(rows)
        info = self.g.lceda_fp.solve_scale(pads, *self.g.expected_spans())
        body = self.g.doc_body(rows)
        self.assertEqual(self.g.audit(rows, pads, info, body), [])

    def test_generated_footprint_shape(self):
        text, report, pads, s = self.g.build(self.g.lceda_fp.read_rows(self.g.DOC))
        self.assertEqual(text.count("(pad \""), 96)
        self.assertEqual(text.count("(") - text.count(")"), 0)
        self.assertIn("(size 0.4572 0.4572)", text)
        self.assertEqual(report["unit"], "mil")
        self.assertEqual(report["pads"], 96)
        self.assertIn('(property "Reference" "U**"', text)
        # body 9×14 → 半宽 4.5 / 半高 7.0
        self.assertIn("(start -4.5000 -7.0000)", text)
        self.assertIn("(end 4.5000 7.0000)", text)

    def test_pad_table_spans(self):
        text, report, pads, s = self.g.build(self.g.lceda_fp.read_rows(self.g.DOC))
        xs = [p[1] * s for p in pads]
        ys = [p[2] * s for p in pads]
        self.assertAlmostEqual(max(xs) - min(xs), 6.4, places=3)
        self.assertAlmostEqual(max(ys) - min(ys), 12.0, places=3)
        # A1 在左上：EasyEDA +Y 朝上，KiCad +Y 朝下
        a1 = [p for p in pads if p[0] == "A1"][0]
        self.assertLess(a1[1] * s, 0)
        self.assertGreater(a1[2] * s, 0)

    # ---------------------------------------------------------------- 反向
    def test_reverse_declared_mm_truly_mil_still_solved(self):
        """声明 mm 而真值 mil：必须仍解成 mil（不能采信 CANVAS）。"""
        rows = self.g.lceda_fp.read_rows(self.g.DOC)
        pads = self.g.lceda_fp.pads(rows)
        info = self.g.lceda_fp.solve_scale(pads, *self.g.expected_spans())
        self.assertEqual(info["unit"], "mil")

    def test_reverse_scale_no_hit(self):
        rows = self.g.lceda_fp.read_rows(self.g.DOC)
        pads = self.g.lceda_fp.pads(rows)
        with self.assertRaises(self.g.lceda_fp.GeometryError):
            self.g.lceda_fp.solve_scale(pads, 60.0, 120.0)

    def test_reverse_missing_pad(self):
        with _TmpDoc(_drop_pad(_doc_text(), "T9")):
            rows = self.g.lceda_fp.read_rows(self.g.DOC)
            pads = self.g.lceda_fp.pads(rows)
            info = self.g.lceda_fp.solve_scale(pads, *self.g.expected_spans())
            errs = self.g.audit(rows, pads, info, self.g.doc_body(rows))
            self.assertTrue(any(e.startswith("A1") for e in errs), errs)

    def test_reverse_duplicate_number(self):
        with _TmpDoc(_patch_pad(_doc_text(), "A1", f5="A2")):
            rows = self.g.lceda_fp.read_rows(self.g.DOC)
            pads = self.g.lceda_fp.pads(rows)
            info = self.g.lceda_fp.solve_scale(pads, *self.g.expected_spans())
            errs = self.g.audit(rows, pads, info, self.g.doc_body(rows))
            self.assertTrue(any(e.startswith("A1") for e in errs), errs)

    def test_reverse_wrong_pitch(self):
        """挪中心焊盘 E2 半个 pitch：跨度不变，但最近邻多出第三种间距。"""
        with _TmpDoc(_patch_pad(_doc_text(), "E2", f6=-94.49 + 15.7)):
            rows = self.g.lceda_fp.read_rows(self.g.DOC)
            pads = self.g.lceda_fp.pads(rows)
            info = self.g.lceda_fp.solve_scale(pads, *self.g.expected_spans())
            errs = self.g.audit(rows, pads, info, self.g.doc_body(rows))
            self.assertTrue(any(e.startswith("A2") for e in errs), errs)

    def test_reverse_pad_diameter(self):
        with _TmpDoc(_patch_pad(_doc_text(), "A1", f10=["ELLIPSE", 36, 36])):
            rows = self.g.lceda_fp.read_rows(self.g.DOC)
            pads = self.g.lceda_fp.pads(rows)
            info = self.g.lceda_fp.solve_scale(pads, *self.g.expected_spans())
            errs = self.g.audit(rows, pads, info, self.g.doc_body(rows))
            self.assertTrue(any(e.startswith("A3") for e in errs), errs)

    def test_reverse_body_size(self):
        mm = 1.0 / 0.0254                      # 只改本体：±150 mil 而不是 ±177.165
        pts = [-150, -260, "L", -150, 260, 150, 260, 150, -260, -150, -260]
        with _TmpDoc(_patch_body(_doc_text(), pts)):
            rows = self.g.lceda_fp.read_rows(self.g.DOC)
            pads = self.g.lceda_fp.pads(rows)
            info = self.g.lceda_fp.solve_scale(pads, *self.g.expected_spans())
            errs = self.g.audit(rows, pads, info, self.g.doc_body(rows))
            self.assertTrue(any(e.startswith("A4") for e in errs), errs)

    def test_reverse_no_body(self):
        with _TmpDoc(_rewrite(_doc_text(), lambda row: None if (
            isinstance(row, list) and len(row) > 6 and row[0] == "POLY" and row[4] == 48
        ) else row)):
            rows = self.g.lceda_fp.read_rows(self.g.DOC)
            with self.assertRaises(self.g.lceda_fp.GeometryError):
                self.g.doc_body(rows)

    def test_reverse_row_letter_o(self):
        """把一行球名换成手册不存在的 O 行，A5 必须红。"""
        with _TmpDoc(_patch_pad(_doc_text(), "T1", f5="O1")):
            rows = self.g.lceda_fp.read_rows(self.g.DOC)
            pads = self.g.lceda_fp.pads(rows)
            info = self.g.lceda_fp.solve_scale(pads, *self.g.expected_spans())
            errs = self.g.audit(rows, pads, info, self.g.doc_body(rows))
            self.assertTrue(any(e.startswith("A5") for e in errs), errs)

    def test_reverse_column_out_of_set(self):
        with _TmpDoc(_patch_pad(_doc_text(), "A1", f5="A6")):
            rows = self.g.lceda_fp.read_rows(self.g.DOC)
            pads = self.g.lceda_fp.pads(rows)
            info = self.g.lceda_fp.solve_scale(pads, *self.g.expected_spans())
            errs = self.g.audit(rows, pads, info, self.g.doc_body(rows))
            self.assertTrue(any(e.startswith("A5") for e in errs), errs)

    def test_reverse_duplicate_coordinate(self):
        with _TmpDoc(_patch_pad(_doc_text(), "A2", f5="A9", f6=-125.98)):
            rows = self.g.lceda_fp.read_rows(self.g.DOC)
            pads = self.g.lceda_fp.pads(rows)
            info = self.g.lceda_fp.solve_scale(pads, *self.g.expected_spans())
            errs = self.g.audit(rows, pads, info, self.g.doc_body(rows))
            self.assertTrue(any(e.startswith("A6") for e in errs), errs)

    def test_symbol_pin_numbers_equal_pad_numbers(self):
        """符号的 96 个 pin number 必须与封装 96 个焊盘号逐字相同。"""
        import re as _re
        import sys as _sys
        _sys.path.insert(0, os.path.join(REPO, "src"))
        from pcb_agent_mcp import parts
        sym_doc = os.path.join(os.path.dirname(self.g.DOC), "DDR3L_MT41K256M16HA-125E_symbol.efp")
        with open(sym_doc, "r", encoding="utf-8") as fh:
            text, report = parts.symbol_to_kicad(fh.read(), name="MT41K256M16")
        pins = set(_re.findall(r'(?:number) "([^"]+)"', text))
        rows = self.g.lceda_fp.read_rows(self.g.DOC)
        pads = {p[0] for p in self.g.lceda_fp.pads(rows)}
        self.assertEqual(len(pins), 96)
        self.assertEqual(report["pins"], 96)
        self.assertEqual(pins, pads)

    def test_symbol_covers_all_sixteen_data_lines(self):
        import re as _re
        import sys as _sys
        _sys.path.insert(0, os.path.join(REPO, "src"))
        from pcb_agent_mcp import parts
        sym_doc = os.path.join(os.path.dirname(self.g.DOC), "DDR3L_MT41K256M16HA-125E_symbol.efp")
        with open(sym_doc, "r", encoding="utf-8") as fh:
            text, _report = parts.symbol_to_kicad(fh.read(), name="MT41K256M16")
        names = set(_re.findall(r'(?:name) "([^"]+)"', text))
        for i in range(16):
            self.assertIn("DQ%d" % i, names)
        for n in ("LDQS", "UDQS", "LDM", "UDM", "RAS#", "CAS#", "WE#", "CK", "CS#", "ZQ"):
            self.assertIn(n, names, names)
        self.assertGreaterEqual(len(names), 40)

    def test_reverse_not_centred(self):
        rows = self.g.lceda_fp.read_rows(self.g.DOC)
        pads = self.g.lceda_fp.pads(rows)
        info = self.g.lceda_fp.solve_scale(pads, *self.g.expected_spans())
        body = self.g.doc_body(rows)
        shifted = [(p[0], p[1] + 10.0, p[2], p[3], p[4]) for p in pads]
        errs = self.g.audit(rows, shifted, info, body)
        self.assertTrue(any(e.startswith("A7") for e in errs), errs)


if __name__ == "__main__":
    unittest.main()
