# -*- coding: utf-8 -*-
"""tools/gen_bga333_footprint.py 的用例：正向 + 反向失败对照。

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
GEN = os.path.join(REPO, "tools", "gen_bga333_footprint.py")
_MOD = None


def _gen():
    global _MOD
    if _MOD is None:
        spec = importlib.util.spec_from_file_location("gen_bga333_footprint", GEN)
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
        self.dir = tempfile.mkdtemp(prefix="bga333_")
        self.path = os.path.join(self.dir, "doc.efp")
        with open(self.path, "w", encoding="utf-8") as fh:
            fh.write(self.text)
        g.DOC = self.path
        return self.path

    def __exit__(self, *exc):
        _gen().DOC = self.old
        shutil.rmtree(self.dir, ignore_errors=True)
        return False


def _patch_pad(text, number, **changes):
    """改一条 PAD 行的字段（按 JSON 行改写，不碰别的行）。"""
    out = []
    for line in text.splitlines():
        if not line.strip():
            continue
        row = json.loads(line)
        if isinstance(row, list) and row and row[0] == "PAD" and str(row[5]) == number:
            for key, val in changes.items():
                row[int(key[1:])] = val
        out.append(json.dumps(row, ensure_ascii=False))
    return "\n".join(out) + "\n"


class PackageGeometryTest(unittest.TestCase):
    def setUp(self):
        self.g = _gen()

    # ---------------------------------------------------------------- 正向
    def test_scale_is_solved_not_read(self):
        """文档声明 mm，几何真值是 mil：比例必须由 E1/D1 解出来。"""
        doc = self.g.parse_doc(self.g.DOC)
        info = self.g.solve_scale(doc["pads"], doc["declared_unit"])
        self.assertEqual(info["unit"], "mil")
        self.assertEqual(info["declared_unit"], "mm")
        self.assertTrue(info["declared_unit_disagrees"])
        self.assertAlmostEqual(info["span_x_mm"], 12.364, places=2)
        self.assertAlmostEqual(info["span_y_mm"], 10.678, places=2)

    def test_audit_passes_on_real_document(self):
        rep = self.g.audit(self.g.parse_doc(self.g.DOC)["pads"],
                           self.g.solve_scale(self.g.parse_doc(self.g.DOC)["pads"], "mm"),
                           _body_points(self.g), _datasheet_names(self.g))
        self.assertTrue(rep["pass"], rep["fails"])
        self.assertEqual(rep["pitch_set_mm"], [0.562, 0.65])

    def test_generated_footprint_matches_audit(self):
        """落盘的 .kicad_mod 必须和审计过的几何逐项一致。"""
        with open(self.g.OUT_MOD, "r", encoding="utf-8") as fh:
            text = fh.read()
        self.assertTrue(text.startswith('(footprint "Package_WBBGA-333L_RK3506J"'))
        self.assertEqual(text.count('(pad "'), 333)
        self.assertEqual(text.count("("), text.count(")"))
        pads = [l for l in text.splitlines() if l.strip().startswith("(pad ")]
        self.assertEqual(len(pads), 333)
        self.assertIn('(size 0.25 0.25)', text)
        self.assertIn('"1G2"', text)
        # 每个焊盘都必须在球阵 bbox 内
        import re
        for line in pads:
            m = re.search(r"(?:at|\(at) (-?\d+\.\d+)\s+(-?\d+\.\d+)\s*\)", text[text.index(line):]) \
                if False else None
        # 用 CSV 复核坐标范围（CSV 由同一个生成器写出，二者必须自洽）
        import csv
        with open(self.g.OUT_CSV, "r", encoding="utf-8") as fh:
            rows = list(csv.DictReader(fh))
        self.assertEqual(len(rows), 333)
        xs = [float(r["x_mm"]) for r in rows]
        ys = [float(r["y_mm"]) for r in rows]
        self.assertLessEqual(max(xs) - min(xs), 12.3645)
        self.assertLessEqual(max(ys) - min(ys), 10.6785)

    # ------------------------------------------- 反向：S1 比例解不出唯一值
    def test_scale_declared_mil_truly_mm(self):
        """声明的单位也可能说谎（反向那一半）：声明 mil、几何真是 mm 时必须解成 mm。"""
        doc = self.g.parse_doc(self.g.DOC)
        pads = [dict(p) for p in doc["pads"]]
        for p in pads:                     # 把真实 mm 坐标塞回文档座标系
            p["x"] = p["x"] * 0.0254
            p["y"] = p["y"] * 0.0254
        info = self.g.solve_scale(pads, "mil")
        self.assertEqual(info["unit"], "mm")
        self.assertTrue(info["declared_unit_disagrees"])

    def test_scale_reverse_no_hit(self):
        """跨度既不对 mil 也不对 mm ⇒ 报错，不许挑一个凑。"""
        doc = self.g.parse_doc(self.g.DOC)
        pads = [dict(p) for p in doc["pads"]]
        for p in pads:
            p["x"] = p["x"] * 1.37
        with self.assertRaises(SystemExit):
            self.g.solve_scale(pads, "mil")

    # ------------------------------------------- 反向：A1 焊盘数
    def test_reverse_missing_pad(self):
        text = _doc_text()
        lines = []
        for l in text.splitlines():
            if not l.strip():
                continue
            r = json.loads(l)
            if isinstance(r, list) and r and r[0] == "PAD" and str(r[5]) == "A1":
                continue
            lines.append(l)
        with _TmpDoc("\n".join(lines) + "\n"):
            rep = self.g.audit(self.g.parse_doc(self.g.DOC)["pads"],
                               self.g.solve_scale(self.g.parse_doc(self.g.DOC)["pads"], "mm"),
                               _body_points(self.g), _datasheet_names(self.g))
        self.assertFalse(rep["pass"])
        self.assertTrue(any("A1_pad_count" in f for f in rep["fails"]))

    def test_reverse_duplicate_number(self):
        text = _patch_pad(_doc_text(), "B1", f5="A1")
        with _TmpDoc(text):
            rep = self.g.audit(self.g.parse_doc(self.g.DOC)["pads"],
                               self.g.solve_scale(self.g.parse_doc(self.g.DOC)["pads"], "mm"),
                               _body_points(self.g), _datasheet_names(self.g))
        self.assertFalse(rep["pass"])
        self.assertTrue(any("A1_pad_count" in f for f in rep["fails"]))

    # ------------------------------------------- 反向：A3 间距谱
    def test_reverse_wrong_pitch(self):
        # 挪一个中心焊盘（1G7）半拍：跨度不变，但最近邻多出第三种间距 ⇒ A3 必须失败
        text = _patch_pad(_doc_text(), "1G7", f6=-0.3249 / 0.0254, f7=9.843 / 2)
        with _TmpDoc(text):
            rep = self.g.audit(self.g.parse_doc(self.g.DOC)["pads"],
                               self.g.solve_scale(self.g.parse_doc(self.g.DOC)["pads"], "mm"),
                               _body_points(self.g), _datasheet_names(self.g))
        self.assertFalse(rep["pass"])
        self.assertTrue(any("A3_two_pitches" in f for f in rep["fails"]))

    # ------------------------------------------- 反向：A4 焊盘直径
    def test_reverse_pad_diameter(self):
        text = _patch_pad(_doc_text(), "A1", f10=["ELLIPSE", 12.0, 12.0])
        with _TmpDoc(text):
            rep = self.g.audit(self.g.parse_doc(self.g.DOC)["pads"],
                               self.g.solve_scale(self.g.parse_doc(self.g.DOC)["pads"], "mm"),
                               _body_points(self.g), _datasheet_names(self.g))
        self.assertFalse(rep["pass"])
        self.assertTrue(any("A4_pad_diameter" in f for f in rep["fails"]))

    # ------------------------------------------- 反向：A5 本体
    def test_reverse_body_size(self):
        """本体矩形被拉宽 0.3 mm ⇒ A5 必须失败。"""
        rows, patched = [], False
        for line in _doc_text().splitlines():
            if not line.strip():
                continue
            r = json.loads(line)
            if isinstance(r, list) and r and r[0] == "POLY" and r[4] == 48:
                r[6] = [v - 12.0 if isinstance(v, (int, float)) and v < 0 else v for v in r[6]]
                patched = True
            rows.append(json.dumps(r, ensure_ascii=False))
        self.assertTrue(patched)
        text = "\n".join(rows) + "\n"
        rep = self.g.audit(self.g.parse_doc(self.g.DOC)["pads"],
                           self.g.solve_scale(self.g.parse_doc(self.g.DOC)["pads"], "mm"),
                           _body_points_from(text), _datasheet_names(self.g))
        self.assertFalse(rep["pass"])
        self.assertTrue(any("A5_body_outline" in f for f in rep["fails"]))

    def test_reverse_no_body(self):
        rep = self.g.audit(self.g.parse_doc(self.g.DOC)["pads"],
                           self.g.solve_scale(self.g.parse_doc(self.g.DOC)["pads"], "mm"),
                           None, _datasheet_names(self.g))
        self.assertFalse(rep["pass"])
        self.assertTrue(any("A5_body_outline" in f for f in rep["fails"]))

    # ------------------------------------------- 反向：A6 球名
    def test_reverse_extra_ball_not_1g2(self):
        text = _patch_pad(_doc_text(), "1G2", f5="1G3")
        with _TmpDoc(text):
            rep = self.g.audit(self.g.parse_doc(self.g.DOC)["pads"],
                               self.g.solve_scale(self.g.parse_doc(self.g.DOC)["pads"], "mm"),
                               _body_points(self.g), _datasheet_names(self.g))
        self.assertFalse(rep["pass"])
        self.assertTrue(any("A6_ball_names" in f for f in rep["fails"]))

    # ------------------------------------------- 反向：A7 重复坐标
    def test_reverse_duplicate_coordinate(self):
        text = _patch_pad(_doc_text(), "B1", f6=-243.39, f7=210.2)
        with _TmpDoc(text):
            rep = self.g.audit(self.g.parse_doc(self.g.DOC)["pads"],
                               self.g.solve_scale(self.g.parse_doc(self.g.DOC)["pads"], "mm"),
                               _body_points(self.g), _datasheet_names(self.g))
        self.assertFalse(rep["pass"])
        self.assertTrue(any("A7_no_duplicate" in f for f in rep["fails"]))

    # ------------------------------------------- 反向：A8 居中
    def test_reverse_not_centred(self):
        text = _doc_text()
        rows = []
        for line in text.splitlines():
            if not line.strip():
                continue
            r = json.loads(line)
            if isinstance(r, list) and r and r[0] == "PAD":
                r[6] = float(r[6]) + 30.0     # 整体平移 30 mil
            rows.append(json.dumps(r, ensure_ascii=False))
        with _TmpDoc("\n".join(rows) + "\n"):
            rep = self.g.audit(self.g.parse_doc(self.g.DOC)["pads"],
                               dict(self.g.solve_scale(
                                   self.g.parse_doc(self.g.DOC)["pads"], "mm"),
                                    scale=0.0254),
                               _body_points(self.g), _datasheet_names(self.g))
        self.assertFalse(rep["pass"])
        self.assertTrue(any("A8_centred" in f for f in rep["fails"]))


# ---------------------------------------------------------------- helpers
def _body_points(g):
    doc = g.parse_doc(g.DOC)
    pts = None
    for gr in doc["graphics"]:
        geom = gr["geometry"]
        if gr["kind"] == "poly" and gr["layer_number"] == 48 and isinstance(geom, list):
            nums = [v for v in geom if isinstance(v, (int, float))]
            pts = [(nums[i], nums[i + 1]) for i in range(0, len(nums) - 1, 2)]
    return pts


def _body_points_from(text):
    rows = [json.loads(l) for l in text.splitlines() if l.strip()]
    for r in rows:
        if isinstance(r, list) and r and r[0] == "POLY" and r[4] == 48:
            nums = [v for v in r[6] if isinstance(v, (int, float))]
            return [(nums[i], nums[i + 1]) for i in range(0, len(nums) - 1, 2)]
    return None


def _datasheet_names(g):
    import csv
    names = []
    with open(g.PINMAP, "r", encoding="utf-8-sig") as fh:
        for row in csv.DictReader(fh):
            nm = (row.get("ball") or row.get("pin") or "").strip()
            if nm:
                names.append(nm)
    return names


if __name__ == "__main__":
    unittest.main()
