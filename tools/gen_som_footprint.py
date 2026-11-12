#!/usr/bin/env python3
"""RK3506 SOM 140 pin 邮票孔封装生成器（只做焊盘，不画板、不布线）。

Nothing here is a route; it is a pad table laid on a board edge.

输入（唯一）：``examples/rk3506_som/som_pinout_140.csv``（140 行，边序 N->E->S->W）。
输出：``examples/rk3506_som/lib/RK3506.pretty/SOM_CASTELLATION_140.kicad_mod``。

几何全部来自 docs/143 §5（修订 r1）与 §8 的 R8，不在这里另定数值：

* 板 45 x 45 mm，四角 R2；板中心 = 封装原点 (0,0)，半孔盘中心**落在板边线上**；
* 140 pin @ 1.0 mm，2x40（N/S，跨度 39.0）+ 2x30（E/W，跨度 29.0）；
* 半孔：孔 **φ0.55**、盘 **φ0.85**、相邻焊盘间隙 **0.15**、环 **0.15**（孔径 ≥ 0.5，
  盘 ≥ 孔径 + 0.30 ⇒ 0.85 ≥ 0.55 + 0.30 恰好）；
* 四角 2 mm 禁布（R2 圆弧区不放半孔）；编号 R8：N 左→右 1–40、E 上→下 41–70、
  S 右→左 71–110、W 下→上 111–140。

逐条断言（任一不过即非零退出，不写字节）：
F1 焊盘数 == 140 且编号 1..140 各一次；
F2 每边的焊盘中心严格在本边的板边线上（|x| = |y| = 板半宽）；
F3 同边相邻焊盘间距严格 1.000 mm，跨度 = (n-1) * 1.000；
F4 边内序号方向符合 R8（N 是 +x，E 是 +y，S 是 -x，W 是 -y：KiCad 板坐标 y 向下）；
F5 任一半孔盘到最近板角的边内距离 >= 2.000 mm（R2 禁布）；
F6 焊盘孔 0.55 / 盘 0.85 与 docs/143 冻结值一致，且盘 - 孔 >= 0.30（立创半孔环下限）。
"""

from __future__ import annotations

import csv
import json
import os
import sys

HERE = os.path.dirname(os.path.abspath(__file__))
REPO = os.path.dirname(HERE)
EX = os.path.join(REPO, "examples", "rk3506_som")
CSV_IN = os.path.join(EX, "som_pinout_140.csv")
PRETTY = os.path.join(EX, "lib", "RK3506.pretty")
NAME = "SOM_CASTELLATION_140"
OUT = os.path.join(PRETTY, NAME + ".kicad_mod")

BOARD_MM = 45.0          # docs/143 §6 板级冻结：45 x 45，四角 R2
CORNER_R_MM = 2.0        # R2 圆弧区；半孔不得进入
PITCH_MM = 1.0           # 140 pin @1.0 mm（与参考 SOM 同构）
PAD_D_MM = 0.85          # docs/143 §5 冻结
HOLE_D_MM = 0.55         # docs/143 §5 冻结
RING_MIN_MM = 0.30       # 立创半孔工艺：盘 >= 孔径 + 0.30
EDGE_SLOTS = {"N": 40, "E": 30, "S": 40, "W": 30}
EDGES = ("N", "E", "S", "W")

HALF = BOARD_MM / 2.0


def read_rows():
    with open(CSV_IN, "r", encoding="utf-8", newline="") as f:
        return list(csv.DictReader(f))


def edge_axis(edge, index, count):
    """第 index 个（0 基）在本边的焊盘中心坐标（mm，板坐标系 y 向下）。

    R8 方向：N 左→右(+x)、E 上→下(+y)、S 右→左(-x)、W 下→上(-y)。
    """
    span = (count - 1) * PITCH_MM
    off = index * PITCH_MM
    if edge == "N":
        return (-HALF + off, -HALF)
    if edge == "S":
        return (HALF - off, HALF)
    if edge == "E":
        return (HALF, -HALF + off)
    if edge == "W":
        return (-HALF, HALF - off)
    raise SystemExit("unknown edge %r" % edge)


def plan(rows):
    """[(pin:int, edge, x, y)] in pin order, plus the per-edge audit numbers."""
    out = []
    per_edge = {}
    for edge in EDGES:
        group = [r for r in rows if r["edge"] == edge]
        want = EDGE_SLOTS[edge]
        if len(group) != want:
            raise SystemExit("F1 edge %s has %d pins, frozen value is %d"
                             % (edge, len(group), want))
        for i, r in enumerate(group):
            x, y = edge_axis(edge, i, want)
            out.append((int(r["pin"]), edge, x, y))
        # F2/F3/F4 数值断言：按边重算一遍，不信任上面的构造
        xs = [round(edge_axis(edge, i, want)[0], 6) for i in range(want)]
        ys = [round(edge_axis(edge, i, want)[1], 6) for i in range(want)]
        if edge in ("N", "S"):
            if any(abs(abs(v) - HALF) > 1e-9 for v in ys):
                raise SystemExit("F2 %s pads are not on the board edge" % edge)
            steps = [round(xs[i + 1] - xs[i], 6) for i in range(want - 1)]
            d = 1.0 if edge == "N" else -1.0
            if len(set(steps)) != 1 or abs(steps[0] - d) > 1e-9:
                raise SystemExit("F3/F4 %s pitch/direction is wrong: %s" % (edge, steps[:3]))
            span = round(max(xs) - min(xs), 6)
        else:
            if any(abs(abs(v) - HALF) > 1e-9 for v in xs):
                raise SystemExit("F2 %s pads are not on the board edge" % edge)
            steps = [round(ys[i + 1] - ys[i], 6) for i in range(want - 1)]
            d = 1.0 if edge == "E" else -1.0
            if len(set(steps)) != 1 or abs(steps[0] - d) > 1e-9:
                raise SystemExit("F3/F4 %s pitch/direction is wrong: %s" % (edge, steps[:3]))
            span = round(max(ys) - min(ys), 6)
        if abs(span - (want - 1) * PITCH_MM) > 1e-9:
            raise SystemExit("F3 %s span %s != %s" % (edge, span, (want - 1) * PITCH_MM))
        per_edge[edge] = {"pins": want, "span_mm": span,
                          "corner_clear_mm": round(HALF - span / 2.0, 6)}
        if per_edge[edge]["corner_clear_mm"] < CORNER_R_MM:
            raise SystemExit("F5 %s corner clearance %s < %s"
                             % (edge, per_edge[edge]["corner_clear_mm"], CORNER_R_MM))
    if PAD_D_MM - HOLE_D_MM < RING_MIN_MM - 1e-9:      # F6
        raise SystemExit("F6 ring %s < %s" % (round(PAD_D_MM - HOLE_D_MM, 6), RING_MIN_MM))
    nums = [p[0] for p in out]
    if sorted(nums) != list(range(1, 141)) or len(set(nums)) != 140:   # F1
        raise SystemExit("F1 pad numbers are not 1..140 exactly once")
    if [p[0] for p in out] != sorted(nums):
        raise SystemExit("F1 pin order is not N->E->S->W ascending")
    return out, per_edge


def footprint_text(pads):
    """KiCad 10 footprint file. Only pads, courtyard and two F.Fab markers.

    The literal shapes below are copied from the known-good 0.1.3/0.1.6 files
    (``hide yes`` / ``(fill no)``) -- KiCad 10 refuses the older variants.
    """
    f = str(round(PAD_D_MM, 4))
    d = str(round(HOLE_D_MM, 4))
    T = "\t"
    lines = [
        '(footprint "%s"' % NAME,
        T + '(version 20260206)',
        T + '(generator "pcb_agent_mcp")',
        T + '(generator_version "0.1.8")',
        T + '(layer "F.Cu")',
        T + '(descr "RK3506 SOM 140 castellated half-holes: %s x %s mm board edge, '
        '1.000 mm pitch, pad %s / drill %s, 4-corner %s mm keepout. '
        'Pads only; not a footprint of a purchased part -- no BOM line. '
        'Geometry: docs/143 ss5 (r1) and ss8 R8. Nothing here is a route.")'
        % (BOARD_MM, BOARD_MM, f, d, CORNER_R_MM),
        T + '(tags "RK3506 SOM castellated stamp hole 140 pin 1.0mm")',
        T + '(attr through_hole)',
        T + '(duplicate_pad_numbers_are_jumpers no)',
    ]

    def prop(name, value, layer, at_y, hide):
        out = [T + '(property "%s" "%s"' % (name, value),
               T + T + '(at 0 %s 0)' % at_y,
               T + T + '(layer "%s")' % layer]
        if hide:
            out.append(T + T + '(hide yes)')
        out += [T + T + '(effects',
                T + T + T + '(font',
                T + T + T + T + '(size 1 1)',
                T + T + T + T + '(thickness 0.15)',
                T + T + T + ')',
                T + T + ')',
                T + ')']
        return out

    lines += prop("Reference", "J**", "F.SilkS", round(-HALF - 2.0, 4), False)
    lines += prop("Value", NAME, "F.Fab", round(HALF + 2.0, 4), False)
    lines += prop("Datasheet", "", "F.Fab", 0, True)
    lines += prop("Description", "140 castellated half-holes on the %s x %s mm "
                   "board edge; pads only, no BOM line" % (BOARD_MM, BOARD_MM),
                   "F.Fab", 0, True)

    def rect(layer, width):
        return [T + '(fp_rect',
                T + T + '(start %s %s)' % (-HALF, -HALF),
                T + T + '(end %s %s)' % (HALF, HALF),
                T + T + '(stroke',
                T + T + T + '(width %s)' % width,
                T + T + T + '(type solid)',
                T + T + ')',
                T + T + '(fill no)',
                T + T + '(layer "%s")' % layer,
                T + ')']

    lines += rect("F.CrtYd", "0.05")
    lines += rect("F.Fab", "0.1")
    for pin, edge, x, y in pads:
        lines += [T + '(pad "%d" thru_hole circle' % pin,
                  T + T + '(at %s %s)' % (round(x, 4), round(y, 4)),
                  T + T + '(size %s %s)' % (f, f),
                  T + T + '(drill %s)' % d,
                  T + T + '(layers "*.Cu" "*.Mask")',
                  T + T + '(remove_unused_layers no)',
                  T + ')']
    lines.append(")")
    return "\n".join(lines) + "\n"


def main(argv=None):
    rows = read_rows()
    pads, per_edge = plan(rows)
    text = footprint_text(pads)
    os.makedirs(PRETTY, exist_ok=True)
    with open(OUT, "w", encoding="utf-8", newline="\n") as f:
        f.write(text)
    print(json.dumps({"out": OUT, "pads": len(pads),
                      "pad_mm": PAD_D_MM, "drill_mm": HOLE_D_MM,
                      "per_edge": per_edge,
                      "audits": "F1-F6 passed",
                      "bytes": len(text)}, ensure_ascii=False, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
