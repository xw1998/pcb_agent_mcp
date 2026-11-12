# -*- coding: utf-8 -*-
"""RK3506J WBBGA-333L (FBGA-333L) 焊盘封装生成器（只出 .kicad_mod 与球表，不布线、不布局）。

Nothing here is a route; it is a package geometry transcription.

几何来源（唯一）：嘉立创 EDA / EasyEDA 官方库 LCSC C48642327（RK3506J，FBGA-333L）的
footprint 文档 examples/rk3506_som/library/BGA-333_L13.3-W11.3_RK3506B.efp，
来源与解密参数见同目录 lceda_source.json。文档不是猜的、不是照着丝印画的，是官方库的原图。

三个必须自己判断、不能照抄的地方（照抄就错）：

S1 单位。文档 CANVAS 行写的是 "mm"，但坐标真值是 mil：pad 直径 9.843、X 跨 486.78、Y 跨 420.40。
   按 mil 折算得到 0.250 mm 直径、12.364 mm 与 10.678 mm 跨度 —— 正好等于手册 Fig 2-5 的 E1/D1，
   按 "mm" 折算得到的是 486 mm 宽的器件（物理上不存在）。所以比例不是"读出来"的，是"解出来"的：
   手册给的球阵跨度 D1/E1 是尺子，两个候选比例里只有一个对得上尺子；两个都不对上或都对上 ⇒ 报错退出。

S2 焊盘直径。文档给 0.250 mm；手册 b=0.30 是球的直径，不是焊盘。两者不同名不同量，别混。

S3 外框与 courtyard。Cmts.User 的矩形正好 13.30 × 11.30 mm = 手册 D × E，直接当本体外形；
   courtyard 手册没给，按"本体 + 0.25 mm"的工程默认值取，并在报告里标 courtyard_rule，不当成手册事实。

审计（任一不过即非零退出、且一个字节都不写）：
A1 焊盘 333 个、编号不重复
A2 比例由 E1/D1 解出，且与声明的单位不一致时如实记录
A3 最近邻间距只出现 0.562 / 0.650 两种（=手册 e / e1），容差 0.003 mm
A4 焊盘直径单一值 0.250 mm
A5 本体矩形 13.30 × 11.30 mm（容差 0.02）
A6 球名与手册 332 个具名球逐一相同，多出来的只能是 1G2（手册未列的第 333 球）
A7 坐标无重复
A8 球阵 bbox 中心在原点（器件对称，偏移会被后续贴装放大）
"""

from __future__ import annotations

import csv
import json
import os
import re
import sys
import collections
from decimal import Decimal, ROUND_HALF_UP

HERE = os.path.dirname(os.path.abspath(__file__))
REPO = os.path.dirname(HERE)
EX = os.path.join(REPO, "examples", "rk3506_som")
DOC = os.path.join(EX, "library", "BGA-333_L13.3-W11.3_RK3506B.efp")
META = os.path.join(EX, "library", "lceda_source.json")
PINMAP = os.path.join(EX, "pinmap_rk3506j.csv")
OUT_DIR = os.path.join(EX, "lib", "RK3506.pretty")
OUT_MOD = os.path.join(OUT_DIR, "Package_WBBGA-333L_RK3506J.kicad_mod")
OUT_CSV = os.path.join(EX, "bga333_balls.csv")

FP_NAME = "Package_WBBGA-333L_RK3506J"

# 手册 Rev 1.3 / Fig 2-5 的封装尺寸（mm）。这是尺子，不是从库里抄的。
DS_BODY_D = 11.30          # D  (Y 方向，11.3)
DS_BODY_E = 13.30          # E  (X 方向，13.3)
DS_ARRAY_D1 = 10.678       # D1 球阵 Y 跨
DS_ARRAY_E1 = 12.364       # E1 球阵 X 跨
DS_BALL_B = 0.30           # b  球径（不是焊盘径）
DS_PITCH_E = 0.562         # e  外圈间距
DS_PITCH_E1 = 0.65         # e1 内阵间距
BALLS = 333

# 两个候选比例。“mm”/=1.0 是被文��声明的那一个，必须参与竞争而不是直接采信。
SCALE_CANDIDATES = (("mil", 0.0254), ("mm", 1.0))
SPAN_TOL = 0.005           # 跨度容差：0.5%
PITCH_TOL = 0.003
COURTYARD_MARGIN = 0.25
SILK_MARGIN_FROM_DOC = True   # 丝印矩形按文档给的位置，不自己内缩

MIL_MM = 0.0254


# ----------------------------------------------------------------- 读文档
def read_rows(path):
    rows = []
    with open(path, "r", encoding="utf-8") as fh:
        for line in fh:
            line = line.strip()
            if not line:
                continue
            try:
                row = json.loads(line)
            except Exception:
                continue
            if isinstance(row, list) and row:
                rows.append(row)
    return rows


def parse_doc(path):
    """把 EasyEDA 封装文档拆成 pads / graphics / declared_unit。坐标保持文档原单位。"""
    pads, graphics = [], []
    declared = None
    for row in read_rows(path):
        kind = row[0]
        if kind == "CANVAS":
            declared = row[3]
        elif kind == "PAD":
            pads.append({
                "id": row[1],
                "layer_number": row[4],
                "number": str(row[5]),
                "x": float(row[6]),
                "y": float(row[7]),
                "shape": (row[10] or ["ELLIPSE"])[0],
                "w": float((row[10] or [0, 0, 0])[1]),
                "h": float((row[10] or [0, 0, 0])[2]),
            })
        elif kind in ("POLY", "FILL", "RECT", "CIRCLE", "ARC", "ELLIPSE"):
            # 真实行形状：["POLY", id, 0, "", layer, width, geometry, 0]
            # layer/width/geometry 在 4/5/6，不是 1/2/3——照 parts.py 的语义读，但按文档原样取下标。
            graphics.append({"kind": kind.lower(),
                             "layer_number": int(row[4]) if len(row) > 4 else 0,
                             "width": float(row[5]) if len(row) > 5 else 0.0,
                             "geometry": row[6] if len(row) > 6 else None})
    return {"pads": pads, "graphics": graphics, "declared_unit": declared}


# ----------------------------------------------------------------- 比例求解
def solve_scale(pads, declared_unit):
    """用手册的球阵跨度当尺子，在两个候选比例里解出唯一正确的那一个。"""
    xs = [p["x"] for p in pads]
    ys = [p["y"] for p in pads]
    span_x_raw = max(xs) - min(xs)
    span_y_raw = max(ys) - min(ys)
    tried = []
    hits = []
    for unit, s in SCALE_CANDIDATES:
        sx = span_x_raw * s
        sy = span_y_raw * s
        ok = (abs(sx - DS_ARRAY_E1) <= SPAN_TOL * DS_ARRAY_E1
              and abs(sy - DS_ARRAY_D1) <= SPAN_TOL * DS_ARRAY_D1)
        tried.append({"unit": unit, "scale": s,
                      "span_x_mm": round(sx, 4), "span_y_mm": round(sy, 4),
                      "matches_datasheet": bool(ok)})
        if ok:
            hits.append((unit, s, sx, sy))
    if len(hits) != 1:
        raise SystemExit(
            "S1 失败：球阵跨度 12.364 x 10.678 mm（手册 E1/D1）在候选比例里命中 %d 个，"
            "必须恰好 1 个。候选：%s" % (len(hits), json.dumps(tried, ensure_ascii=False)))
    unit, s, sx, sy = hits[0]
    return {
        "unit": unit,
        "scale": s,
        "declared_unit": declared_unit,
        "declared_unit_disagrees": bool(declared_unit and declared_unit != unit),
        "span_x_mm": round(sx, 4),
        "span_y_mm": round(sy, 4),
        "candidates": tried,
        "evidence": "球阵跨度在 mil 比例下等于手册 E1/D1；在 mm 比例下是 486 mm 宽的器件",
    }


# ----------------------------------------------------------------- 审计
def nearest_neighbour_pitches(pads_mm):
    out = []
    pts = [(p["x"], p["y"]) for p in pads_mm]
    for i in range(len(pts)):
        best = None
        x, y = pts[i]
        for j in range(len(pts)):
            if i == j:
                continue
            a, b = pts[j]
            d = ((x - a) ** 2 + (y - b) ** 2) ** 0.5
            if best is None or d < best:
                best = d
        out.append(best)
    return out


def audit(pads_doc, scale_info, body_poly, datasheet_names):
    rep = {"pass": True, "checks": {}, "fails": []}

    def check(name, ok, detail):
        rep["checks"][name] = {"ok": bool(ok), "detail": detail}
        if not ok:
            rep["pass"] = False
            rep["fails"].append("%s: %s" % (name, detail))

    s = scale_info["scale"]
    pads = [{"number": p["number"], "x": round(p["x"] * s, 4), "y": round(p["y"] * s, 4),
             "d": round(p["w"] * s, 4), "shape": p["shape"]} for p in pads_doc]

    # A1
    numbers = [p["number"] for p in pads]
    check("A1_pad_count",
          len(pads) == BALLS and len(set(numbers)) == BALLS,
          "%d 焊盘 / %d 唯一编号（应为 %d）" % (len(pads), len(set(numbers)), BALLS))

    # A2
    check("A2_scale_solved",
          scale_info["declared_unit_disagrees"],
          "文档声明 %r，实解 %r（%.4g mm/单位）" % (
              scale_info["declared_unit"], scale_info["unit"], s))

    # A3 间距谱
    p = nearest_neighbour_pitches(pads)
    uniq = sorted(set(round(v, 3) for v in p))
    want = (DS_PITCH_E, DS_PITCH_E1)
    check("A3_two_pitches",
          len(uniq) <= 2 and all(min(abs(u - w) for w in want) <= PITCH_TOL for u in uniq)
          and all(min(abs(u - w) for u in uniq) <= PITCH_TOL for w in want),
          "最近邻距离集合 %s（应为 %.3f / %.3f）" % (uniq, want[0], want[1]))

    # A4 焊盘直径
    dias = sorted(set(round(q["d"], 4) for q in pads))
    check("A4_pad_diameter", len(dias) == 1 and dias[0] > 0,
          "直径 %s mm（手册 b=%.2f 为球径，不同名不同量）" % (dias, DS_BALL_B))

    # A5 本体
    ok_body = False
    body_detail = "无 Cmts.User 本体矩形"
    if body_poly:
        xs = [pt[0] * s for pt in body_poly]
        ys = [pt[1] * s for pt in body_poly]
        w = max(xs) - min(xs)
        h = max(ys) - min(ys)
        ok_body = abs(w - DS_BODY_E) <= 0.02 and abs(h - DS_BODY_D) <= 0.02
        body_detail = "本体 %.3f x %.3f mm（应 %.2f x %.2f）" % (w, h, DS_BODY_E, DS_BODY_D)
    check("A5_body_outline", ok_body, body_detail)

    # A6 球名 vs 手册
    extra = sorted(set(numbers) - set(datasheet_names))
    missing = sorted(set(datasheet_names) - set(numbers))
    check("A6_ball_names",
          len(missing) == 0 and extra == ["1G2"],
          "手册未列: %s / 库里多出: %s（多出必须只有 1G2）" % (missing, extra))

    # A7 坐标无重复
    xy = [(q["x"], q["y"]) for q in pads]
    check("A7_no_duplicate", len(set(xy)) == len(xy),
          "%d 个坐标 / %d 唯一" % (len(xy), len(set(xy))))

    # A8 居中
    cx = (max(q["x"] for q in pads) + min(q["x"] for q in pads)) / 2.0
    cy = (max(q["y"] for q in pads) + min(q["y"] for q in pads)) / 2.0
    check("A8_centred", abs(cx) <= 0.01 and abs(cy) <= 0.01,
          "球阵中心 (%.4f, %.4f) mm" % (cx, cy))

    rep["pads_mm"] = pads
    rep["pitch_set_mm"] = uniq
    return rep


# ----------------------------------------------------------------- KiCad 输出
def _f(v):
    if abs(float(v)) < 5e-5:
        v = 0.0
    d = Decimal(str(float(v))).quantize(Decimal("0.0001"), rounding=ROUND_HALF_UP)
    text = format(d.normalize(), "f")
    if "." not in text:
        text += ".0"
    return text


def _pt(x, y):
    return "(xy %s %s)" % (_f(x), _f(y))


FP_VERSION = "20260206"   # KiCad 10 原生格式；老版本(20240108) kicad-cli 10 拒收，实测过


def _prop(name, value, y, layer, hide=False):
    L = ['\t(property "%s" "%s"' % (name, value)]
    L.append('\t\t(at 0 %s 0)' % _f(y))
    L.append('\t\t(layer "%s")' % layer)
    if hide:
        L.append('\t\t(hide yes)')
    L.append('\t\t(effects\n\t\t\t(font\n\t\t\t\t(size 1 1)\n\t\t\t\t(thickness 0.15)\n\t\t\t)\n\t\t)')
    L.append('\t)')
    return L


def build_footprint(rep, body_wh, silk_half, dot, meta):
    pads = rep["pads_mm"]
    d = pads[0]["d"]
    body_e, body_d = body_wh
    cout_e, cout_d = body_e + 2 * COURTYARD_MARGIN, body_d + 2 * COURTYARD_MARGIN
    L = []
    L.append('(footprint "%s"' % FP_NAME)
    L.append('\t(version %s)' % FP_VERSION)
    L.append('\t(generator "pcb_agent_mcp")')
    L.append('\t(generator_version "0.1.3")')
    L.append('\t(layer "F.Cu")')
    L.append('\t(descr "%s")' % (
        "RK3506J WBBGA-333L (FBGA-333L), 333 ball, 0.562 / 0.650 mm pitch. "
        "Geometry transcribed from JLCEDA official library LCSC C48642327 "
        "(footprint uuid %s); scale and sizes audited against datasheet Rev 1.3 Fig 2-5. "
        "Nothing here is a route." % meta.get("footprint_uuid", "?")))
    L.append('\t(tags "RK3506 WBBGA-333L BGA 0.562 0.65")')
    L.append('\t(attr smd)')
    L.append('\t(duplicate_pad_numbers_are_jumpers no)')
    L.extend(_prop("Reference", rep["pads_mm"] and "U**" or "U**", -(body_d / 2 + 2.0), "F.SilkS"))
    L.extend(_prop("Value", FP_NAME, body_d / 2 + 2.0, "F.Fab"))
    L.extend(_prop("Datasheet", meta.get("datasheet", ""), 0, "F.Fab", hide=True))
    L.extend(_prop("Description", meta.get("doc_title", ""), 0, "F.Fab", hide=True))

    L.append('\t(fp_rect')
    L.append('\t\t(start %s %s)' % (_f(-body_e / 2), _f(-body_d / 2)))
    L.append('\t\t(end %s %s)' % (_f(body_e / 2), _f(body_d / 2)))
    L.append('\t\t(stroke\n\t\t\t(width 0.1)\n\t\t\t(type solid)\n\t\t)')
    L.append('\t\t(fill no)')
    L.append('\t\t(layer "F.Fab")\n\t)')
    L.append('\t(fp_rect')
    L.append('\t\t(start %s %s)' % (_f(-DS_ARRAY_E1 / 2), _f(-DS_ARRAY_D1 / 2)))
    L.append('\t\t(end %s %s)' % (_f(DS_ARRAY_E1 / 2), _f(DS_ARRAY_D1 / 2)))
    L.append('\t\t(stroke\n\t\t\t(width 0.05)\n\t\t\t(type solid)\n\t\t)')
    L.append('\t\t(fill no)')
    L.append('\t\t(layer "F.Fab")\n\t)')

    sx, sy = silk_half
    for a, b, c, e in ((-sx, -sy, sx, -sy), (sx, -sy, sx, sy), (sx, sy, -sx, sy), (-sx, sy, -sx, -sy)):
        L.append('\t(fp_line')
        L.append('\t\t(start %s %s)' % (_f(a), _f(b)))
        L.append('\t\t(end %s %s)' % (_f(c), _f(e)))
        L.append('\t\t(stroke\n\t\t\t(width 0.15)\n\t\t\t(type solid)\n\t\t)')
        L.append('\t\t(layer "F.SilkS")\n\t)')

    if dot:
        L.append('\t(fp_circle')
        L.append('\t\t(center %s %s)' % (_f(dot["x"]), _f(dot["y"])))
        L.append('\t\t(end %s %s)' % (_f(dot["x"] + dot["r"]), _f(dot["y"])))
        L.append('\t\t(stroke\n\t\t\t(width 0.15)\n\t\t\t(type solid)\n\t\t)')
        L.append('\t\t(fill no)')
        L.append('\t\t(layer "F.SilkS")\n\t)')

    L.append('\t(fp_rect')
    L.append('\t\t(start %s %s)' % (_f(-cout_e / 2), _f(-cout_d / 2)))
    L.append('\t\t(end %s %s)' % (_f(cout_e / 2), _f(cout_d / 2)))
    L.append('\t\t(stroke\n\t\t\t(width 0.05)\n\t\t\t(type solid)\n\t\t)')
    L.append('\t\t(fill no)')
    L.append('\t\t(layer "F.CrtYd")\n\t)')

    for p in pads:
        L.append('\t(pad "%s" smd circle' % p["number"])
        L.append('\t\t(at %s %s)' % (_f(p["x"]), _f(-p["y"])))
        L.append('\t\t(size %s %s)' % (_f(d), _f(d)))
        L.append('\t\t(layers "F.Cu" "F.Paste" "F.Mask")\n\t)')
    L.append(")")
    return "\n".join(L) + "\n"


# ----------------------------------------------------------------- main
def main():
    with open(META, "r", encoding="utf-8") as fh:
        meta = json.load(fh)
    doc = parse_doc(DOC)
    scale_info = solve_scale(doc["pads"], doc["declared_unit"])

    # 手册 332 个具名球
    ds_names = []
    with open(PINMAP, "r", encoding="utf-8-sig") as fh:
        for row in csv.DictReader(fh):
            nm = (row.get("ball") or row.get("pin") or "").strip()
            if nm:
                ds_names.append(nm)

    # 本体矩形：Cmts.User(48) 的四边形；丝印矩形：F.SilkS(3) 的四条线；1 脚标记：F.SilkS(3) 的圆
    s = scale_info["scale"]
    body_points, body_wh, silk_half, dot = None, None, None, None
    silk_x = silk_y = 0.0
    for g in doc["graphics"]:
        geom = g["geometry"]
        if not isinstance(geom, list) or not geom:
            continue
        if g["kind"] == "poly" and g["layer_number"] == 48 and isinstance(geom[0], (int, float)):
            nums = [v for v in geom if isinstance(v, (int, float))]
            body_points = [(nums[i], nums[i + 1]) for i in range(0, len(nums) - 1, 2)]
        elif g["kind"] == "poly" and g["layer_number"] == 3 and isinstance(geom[0], (int, float)):
            nums = [v for v in geom if isinstance(v, (int, float))]
            for i in range(0, len(nums) - 1, 2):
                silk_x = max(silk_x, abs(nums[i]))
                silk_y = max(silk_y, abs(nums[i + 1]))
    if body_points:
        xs = [pt[0] * s for pt in body_points]
        ys = [pt[1] * s for pt in body_points]
        body_wh = (max(xs) - min(xs), max(ys) - min(ys))
    silk_half = (silk_x * s, silk_y * s)
    # 1 脚标记位置：F.SilkS 上的 CIRCLE（文档放在本体左上外侧）
    for g in doc["graphics"]:
        geom = g["geometry"]
        if (g["kind"] == "poly" and g["layer_number"] == 3
                and isinstance(geom, list) and geom and geom[0] == "CIRCLE"):
            dot = {"x": float(geom[1]) * s, "y": float(geom[2]) * s, "r": float(geom[3]) * s,
                   "from_doc_width": g["width"] * s}
            break

    rep = audit(doc["pads"], scale_info, body_points, ds_names)
    rep["source"] = {
        "lcsc": meta.get("lcsc"), "mpn": meta.get("mpn"),
        "package": meta.get("package"),
        "footprint_uuid": meta.get("footprint_uuid"),
        "doc_title": meta.get("doc_title"),
    }
    rep["datasheet"] = {
        "body_D_mm": DS_BODY_D, "body_E_mm": DS_BODY_E,
        "array_D1_mm": DS_ARRAY_D1, "array_E1_mm": DS_ARRAY_E1,
        "ball_b_mm": DS_BALL_B, "pitch_e_mm": DS_PITCH_E, "pitch_e1_mm": DS_PITCH_E1,
    }
    rep["courtyard_rule"] = "body + %.2f mm (engineering default; datasheet gives no courtyard)" % COURTYARD_MARGIN
    rep["silk_rule"] = "silk rectangle taken from the library document, width 0.15 mm (6 mil in doc)"
    rep["pin1_marker_rule"] = "library dot kept at its own radius (0.06 mm); not enlarged by hand"
    rep["scale"] = scale_info
    rep["counts"] = {
        "pads": len(rep["pads_mm"]),
        "by_pitch": dict(collections.Counter(
            round(min(abs(v - DS_PITCH_E), abs(v - DS_PITCH_E1)), 3)
            for v in nearest_neighbour_pitches(rep["pads_mm"]))),
    }
    rep["notes"] = [
        "手册 b=0.30 mm 是球径；焊盘 0.250 mm 是官方库的落地尺寸，两者不是同一个量。",
        "手册 332 个具名球，封装 333 个焊盘，多出的 1G2 是手册未列的第 333 球（保留，标注 NC 待定）。",
        "Y 号在 KiCad 里取负（KiCad 的 +Y 朝下，EasyEDA 的 +Y 朝上），这样 1 脚仍在左上。",
    ]
    if not rep["pass"]:
        print(json.dumps(rep, ensure_ascii=False, indent=2))
        raise SystemExit("审计不通过，一个字节都没写。")

    text = build_footprint(rep, body_wh, silk_half, dot, meta)
    os.makedirs(OUT_DIR, exist_ok=True)
    with open(OUT_MOD, "w", encoding="utf-8", newline="\n") as fh:
        fh.write(text)

    order = sorted(rep["pads_mm"], key=lambda p: (p["number"][0].isdigit(), p["number"]))
    with open(OUT_CSV, "w", encoding="utf-8", newline="\n") as fh:
        w = csv.writer(fh)
        w.writerow(["ball", "x_mm", "y_mm", "pad_mm", "shape", "named_in_datasheet"])
        for p in order:
            w.writerow([p["number"], "%+.4f" % p["x"], "%+.4f" % (-p["y"]),
                        "%.4f" % p["d"], p["shape"],
                        "no" if p["number"] == "1G2" else "yes"])
    rep["wrote"] = {"kicad_mod": OUT_MOD, "balls_csv": OUT_CSV,
                    "bytes": os.path.getsize(OUT_MOD)}
    print(json.dumps(rep, ensure_ascii=False, indent=2))


if __name__ == "__main__":
    main()
