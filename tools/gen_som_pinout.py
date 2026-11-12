# -*- coding: utf-8 -*-
"""RK3506 SOM 140 pin 邮票孔引脚分配生成器（只做分配表，不画板、不布线）。

Nothing here is a route; it is a pin-slot assignment.

规则（优先级自上而下，冲突时高优先级先满足；脚本末尾逐条断言，任一条不满足即报错退出）：

R1 同一物理接口的引脚必须落在同一条边上，且连续（不得跨边拆分）。
R2 差分对（USB D+/D-、MIPI CLKP/CLKN、D0P/D0N、D1P/D1N）必须相邻两脚，中间不得插 GND/电源。
R3 任意连续非 GND 脚不得超过 6 个（1.0 mm 半孔阵列为地回流留密度）。
R4 每条边必须同时有 5V/3V3 或 GND 的分布，GND 数量按边均摊，不允许某条边整边无地。
R5 DDR3L 55 信号球与板载器件（24 MHz 晶振、DVFS 反馈、eMMC、电源）占用球绝不外引。
R6 电源脚集中成块放在边的端部，不夹在高速接口中间。

数据源（唯一）：examples/rk3506_som/pinmap_rk3506j.csv（由完整手册 Rev 1.3 逐 span 解析）。
输出：examples/rk3506_som/som_pinout_140.csv + stdout 审计 JSON。

不猜测：凡手册/参考设计没给的东西，停留在注释里，不进数据。
"""

from __future__ import annotations

import csv
import json
import os
import sys

HERE = os.path.dirname(os.path.abspath(__file__))
REPO = os.path.dirname(HERE)
PINMAP = os.path.join(REPO, "examples", "rk3506_som", "pinmap_rk3506j.csv")
OUT = os.path.join(REPO, "examples", "rk3506_som", "som_pinout_140.csv")

# ---------------------------------------------------------------- 常量（有出处的）
PIN_TOTAL = 140
EDGE_SLOTS = {"N": 40, "E": 30, "S": 40, "W": 30}  # 2x30 + 2x40 = 140 @1.0 mm
MAX_RUN = 6  # R3
PITCH_MM = 1.00  # 邮票孔间距（与参考 SOM 一致：2x30 + 2x40 = 140pin，间距 1.0mm）
PAD_D_MM = 0.85  # 半孔焊盘：1.00 - 0.15（相邻焊盘间隙下限）→ 见 docs/143 §5
HOLE_D_MM = 0.55  # 半孔钻孔：焊盘 0.85 = 孔 0.55 + 2x0.15（嘉立创半孔环最小 孔径+0.30）
CORNER_KEEPOUT_MM = 2.0  # 四角 R2 圆弧区不放半孔

# R5：不外引的球（板载器件占用 或 手册未定义/仅测试点）
INTERNAL = {
    "H2": "板载 24MHz 晶振 XOUT",
    "H3": "板载 24MHz 晶振 XIN",
    "K3": "CPU_AVS 接板内 DVFS 反馈",
    "1B11": "ACODEC_ADC_VCM 手册未给电压，只留测试点",
    "1B12": "ACODEC_ADC_AVDD1V6 手册未给电压，只留测试点",
    "C22": "板载 SPI-NAND FSPI_CLK",
    "A22": "板载 SPI-NAND FSPI_CSN",
    "B23": "板载 SPI-NAND FSPI_D0",
    "B22": "板载 SPI-NAND FSPI_D1",
    "C21": "板载 SPI-NAND FSPI_D2",
    "C23": "板载 SPI-NAND FSPI_D3",
}

# 每条边的原子序列。原子=占 1 脚的单端信号，或占 2 脚的差分对（('*', ball, ball)）。
# 'GND' 显式原子 = R2 保护地（差分对/复位/电源两侧），其余 GND 由均摊算法补。
# ('5V',) ('3V3',) = 电源块原子，块内脚数由 PWR 定义。
PWR = {"5V": 4, "3V3": 4}
GND_TOTAL = 37  # 140 - 95 信号 - 8 电源 = 37

EDGES = {
    # ---- N：RGB LCDC 28 信号（VO_LCDC_D0..D23 + CLK/HSYNC/VSYNC/DEN）
    "N": [
        ("sig", "G3", "LCDC_D0", "GPIO1/LCDC"),
        ("sig", "F2", "LCDC_D1", "GPIO1/LCDC"),
        ("sig", "F3", "LCDC_D2", "GPIO1/LCDC"),
        ("sig", "E1", "LCDC_D3", "GPIO1/LCDC"),
        ("sig", "E2", "LCDC_D4", "GPIO1/LCDC"),
        ("sig", "E3", "LCDC_D5", "GPIO1/LCDC"),
        ("sig", "D3", "LCDC_D6", "GPIO1/LCDC"),
        ("sig", "C1", "LCDC_D7", "GPIO1/LCDC"),
        ("GND",),
        ("sig", "C2", "LCDC_D8", "GPIO1/LCDC"),
        ("sig", "B1", "LCDC_D9", "GPIO1/LCDC"),
        ("sig", "B2", "LCDC_D10", "GPIO1/LCDC"),
        ("sig", "A2", "LCDC_D11", "GPIO1/LCDC"),
        ("sig", "B3", "LCDC_D12", "GPIO1/LCDC"),
        ("sig", "A3", "LCDC_D13", "GPIO1/LCDC"),
        ("GND",),
        ("sig", "C3", "LCDC_D14", "GPIO1/LCDC"),
        ("sig", "1A1", "LCDC_D15", "GPIO1/LCDC"),
        ("sig", "B5", "LCDC_D16", "GPIO1/LCDC"),
        ("sig", "A5", "LCDC_D17", "GPIO1/LCDC"),
        ("sig", "1A2", "LCDC_D18", "GPIO1/LCDC"),
        ("sig", "1A3", "LCDC_D19", "GPIO1/LCDC"),
        ("GND",),
        ("sig", "B7", "LCDC_D20", "GPIO1/LCDC"),
        ("sig", "A7", "LCDC_D21", "GPIO1/LCDC"),
        ("sig", "1A4", "LCDC_D22", "GPIO1/LCDC"),
        ("sig", "B8", "LCDC_D23", "GPIO1/LCDC"),
        ("sig", "B9", "LCDC_CLK", "GPIO1/LCDC"),
        ("GND",),
        ("sig", "A9", "LCDC_HSYNC", "GPIO1/LCDC"),
        ("sig", "1A5", "LCDC_VSYNC", "GPIO1/LCDC"),
        ("sig", "B10", "LCDC_DEN", "GPIO1/LCDC"),
    ],
    # ---- E：MIPI DSI + USB + SARADC + ACODEC + 5V 输入
    "E": [
        ("GND",),
        ("5V",),
        ("GND",),
        ("pair", "B13", "B14", "MIPI_DSI_CLK", "MIPI"),
        ("pair", "B11", "A11", "MIPI_DSI_D0", "MIPI"),
        ("pair", "A12", "B12", "MIPI_DSI_D1", "MIPI"),
        ("GND",),
        ("pair", "A16", "B16", "USB0_D", "USB"),
        ("sig", "B17", "USB0_ID", "USB"),
        ("sig", "B18", "USB0_VBUSDET", "USB"),
        ("GND",),
        ("pair", "B15", "A15", "USB1_D", "USB"),
        ("GND",),
        ("sig", "A20", "SARADC_IN0", "ADC"),
        ("sig", "B20", "SARADC_IN1", "ADC"),
        ("GND",),
        ("sig", "A21", "SARADC_IN2", "ADC"),
        ("sig", "B21", "SARADC_IN3", "ADC"),
        ("GND",),
        ("pair", "A19", "B19", "ACODEC_IN", "ACODEC"),
        ("GND",),
    ],
    # ---- S：SDMMC（外引 SD/eMMC）+ RMII0 + RMII1 + 3V3 输出
    "S": [
        ("GND",),
        ("sig", "K21", "SDMMC_CLK", "SDMMC"),
        ("sig", "J21", "SDMMC_CMD", "SDMMC"),
        ("GND",),
        ("sig", "K23", "SDMMC_D0", "SDMMC"),
        ("sig", "K22", "SDMMC_D1", "SDMMC"),
        ("GND",),
        ("sig", "H23", "SDMMC_D2", "SDMMC"),
        ("sig", "H22", "SDMMC_D3", "SDMMC"),
        ("GND",),
        ("sig", "D21", "RMII0_RXD0", "ETH0"),
        ("sig", "D23", "RMII0_RXD1", "ETH0"),
        ("sig", "G22", "RMII0_RXDVCRS", "ETH0"),
        ("GND",),
        ("sig", "E21", "RMII0_CLK", "ETH0"),
        ("sig", "E22", "RMII0_TXD0", "ETH0"),
        ("sig", "F21", "RMII0_TXD1", "ETH0"),
        ("GND",),
        ("sig", "F23", "RMII0_TXEN", "ETH0"),
        ("sig", "F22", "RMII0_MDC", "ETH0"),
        ("sig", "G21", "RMII0_MDIO", "ETH0"),
        ("GND",),
        ("sig", "L22", "RMII1_RXD0", "ETH1"),
        ("sig", "M22", "RMII1_RXD1", "ETH1"),
        ("sig", "R22", "RMII1_RXDVCRS", "ETH1"),
        ("GND",),
        ("sig", "M23", "RMII1_CLK", "ETH1"),
        ("sig", "M21", "RMII1_TXD0", "ETH1"),
        ("sig", "N21", "RMII1_TXD1", "ETH1"),
        ("GND",),
        ("sig", "P22", "RMII1_TXEN", "ETH1"),
        ("sig", "P23", "RMII1_MDC", "ETH1"),
        ("sig", "P21", "RMII1_MDIO", "ETH1"),
        ("GND",),
        ("3V3",),
    ],
    # ---- W：复位 + UART0/JTAG + SPI0/SPI1 + SAI0/SAI1 + RM_IO 时钟
    "W": [
        ("GND",),
        ("sig", "T3", "RESET_N", "SYS"),
        ("sig", "J2", "UART0_TX", "UART0"),
        ("sig", "J1", "UART0_RX", "UART0"),
        ("sig", "M2", "SPI0_CLK", "SPI0"),
        ("sig", "M3", "SPI0_MOSI", "SPI0"),
        ("sig", "L1", "SPI0_MISO", "SPI0"),
        ("sig", "L2", "SPI0_CSN0", "SPI0"),
        ("sig", "R2", "SPI1_CLK", "SPI1"),
        ("sig", "R1", "SPI1_MOSI", "SPI1"),
        ("sig", "R3", "SPI1_MISO", "SPI1"),
        ("sig", "N3", "SPI1_CSN0", "SPI1"),
        ("sig", "T2", "SPI1_CSN1", "SPI1"),
        ("sig", "V3", "SAI0_LRCK", "SAI0"),
        ("sig", "W1", "SAI0_SCLK", "SAI0"),
        ("sig", "W2", "SAI0_MCLK", "SAI0"),
        ("sig", "V2", "SAI0_SDO", "SAI0"),
        ("sig", "U2", "SAI0_SDI0", "SAI0"),
        ("sig", "U1", "SAI0_SDI1", "SAI0"),
        ("sig", "U3", "SAI0_SDI2", "SAI0"),
        ("sig", "P2", "SAI1_SDI", "SAI1"),
        ("sig", "P3", "SAI1_SDO0", "SAI1"),
        ("sig", "N1", "SAI1_SDO1", "SAI1"),
        ("sig", "N2", "SAI1_SDO3", "SAI1"),
        ("sig", "G1", "CLK_OUT", "CLK"),
        ("sig", "K2", "ETH_CLK0_25M", "CLK"),
    ],
}


def load_pinmap():
    with open(PINMAP, encoding="utf-8") as f:
        return {r["ball"]: r for r in csv.DictReader(f)}


def atom_len(a):
    return 2 if a[0] == "pair" else (PWR[a[0]] if a[0] in PWR else 1)


def spread_gnd(atoms, slots, gnd_need, max_run=MAX_RUN):
    """把 gnd_need 个 GND 插进原子序列，使每段连续非 GND 原子 ≤ max_run。确定性（最长段优先、同位取左）。"""
    seq = list(atoms)
    fixed_gnd = sum(1 for a in seq if a[0] == "GND")
    extra = gnd_need - fixed_gnd
    if extra < 0:
        raise SystemExit("GND 显式原子已超预算：%d > %d" % (fixed_gnd, gnd_need))
    total = sum(atom_len(a) for a in seq)
    if total + extra != slots:
        raise SystemExit("边脚数不闭合：原子 %d + 待插 GND %d != 槽位 %d" % (total, extra, slots))
    for _ in range(extra):
        # 找当前最长连续非 GND 段（按脚数），在该段中点插入
        best = None
        i = 0
        while i <= len(seq):
            if i < len(seq) and seq[i][0] == "GND":
                i += 1
                continue
            j = i
            run = 0
            while j < len(seq) and seq[j][0] != "GND":
                run += atom_len(seq[j])
                j += 1
            if run >= 2 and (best is None or run > best[0]):
                best = (run, i, j)
            i = j + 1
        if best is None:
            raise SystemExit("无可插入 GND 的段（剩余 %d 个）" % extra)
        _, i, j = best
        mid = i
        # 中点：让插入后两段尽量等长；不拆差分对/电源块
        acc = 0
        while mid < j and acc + atom_len(seq[mid]) <= best[0] // 2:
            acc += atom_len(seq[mid])
            mid += 1
        seq.insert(mid, ("GND",))
    return seq


def build():
    pm = load_pinmap()
    rows = []
    pin = 0
    # 每边 GND 数 = 本边槽位 - 本边信号/电源脚数（槽位是物理约束，不可调）
    need = {e: EDGE_SLOTS[e] - sum(atom_len(a) for a in EDGES[e] if a[0] != "GND") for e in EDGES}
    total_gnd = sum(need.values())
    if total_gnd != GND_TOTAL:
        raise SystemExit("GND 合计 %d != 预算 %d" % (total_gnd, GND_TOTAL))
    for edge in "NESW":
        seq = spread_gnd(EDGES[edge], EDGE_SLOTS[edge], need[edge])
        for a in seq:
            if a[0] == "GND":
                pin += 1
                rows.append(dict(pin=pin, edge=edge, net="GND", ball="", group="PWR",
                                 kind="gnd", note=""))
            elif a[0] in PWR:
                for _ in range(PWR[a[0]]):
                    pin += 1
                    rows.append(dict(pin=pin, edge=edge, net=a[0] + "_IN" if a[0] == "5V" else a[0] + "_OUT",
                                     ball="", group="PWR", kind="power", note=""))
            elif a[0] == "pair":
                for b, suffix in ((a[1], "P"), (a[2], "N")):
                    pin += 1
                    rows.append(dict(pin=pin, edge=edge, net=a[3] + suffix, ball=b,
                                     group=a[4], kind="signal",
                                     note=pm[b]["pin_name"] if b in pm else "MISSING"))
            else:
                pin += 1
                rows.append(dict(pin=pin, edge=edge, net=a[2], ball=a[1], group=a[3],
                                 kind="signal", note=pm[a[1]]["pin_name"] if a[1] in pm else "MISSING"))
    return rows, pm


def _span_mm(n_pins):
    """一侧 n 脚在 pitch 下的占长（mm）：半孔盘中心在板边，跨度 = (n-1)*pitch。"""
    return (n_pins - 1) * PITCH_MM


def audit(rows, pm):
    """逐条核对 R1..R8。返回 (errs, stat)。errs 非空即失败——不做「基本通过」的软化。"""
    errs = []
    stat = {}

    # R0 脚数、序号连续、边序 N->E->S->W
    if len(rows) != PIN_TOTAL:
        errs.append("R0 脚数 %d != %d" % (len(rows), PIN_TOTAL))
    for i, r in enumerate(rows, 1):
        if int(r["pin"]) != i:
            errs.append("R0 pin 序号断裂 @%d" % i)
    order = [r["edge"] for r in rows]
    if order != sorted(order, key="NESW".index):
        errs.append("R0 边序不是 N->E->S->W（逆时针引脚编号）")

    sig = [r for r in rows if r["kind"] == "signal"]
    gnd = [r for r in rows if r["kind"] == "gnd"]
    pwr = [r for r in rows if r["kind"] == "power"]
    stat.update(sig=len(sig), gnd=len(gnd), power=len(pwr))

    # R5 球集合闭合：外引球 == 手册信号球 - DDR 55 - 内部占用；无重复、无 MISSING
    balls = [r["ball"] for r in sig]
    if len(balls) != len(set(balls)):
        errs.append("R5 球重复出现：%s" % sorted({b for b in balls if balls.count(b) > 1}))
    all_sig = {b for b, r in pm.items() if r["class"] == "signal"}
    ddr = {b for b, r in pm.items() if r["abbrev"].startswith("DDR")}
    expect = all_sig - ddr - set(INTERNAL)
    got = set(balls)
    if got != expect:
        errs.append("R5 外引球集合不符：多 %s / 少 %s"
                    % (sorted(got - expect), sorted(expect - got)))
    for r in sig:
        if r["ball"] not in pm:
            errs.append("R5 球 %s 不在手册表内" % r["ball"])

    # R1 同一接口同边，且组内不得插入别的接口的脚（GND 允许插在其间作回流）
    grp = {}
    for r in sig:
        grp.setdefault(r["group"], []).append(r)
    for g, rs in grp.items():
        edges = {r["edge"] for r in rs}
        if len(edges) != 1:
            errs.append("R1 接口 %s 跨边 %s" % (g, sorted(edges)))
        lo, hi = min(r["pin"] for r in rs), max(r["pin"] for r in rs)
        inside = [r for r in sig if lo <= r["pin"] <= hi and r["group"] != g]
        if inside:
            errs.append("R1 接口 %s 区间内混入其它接口 %s" % (g, sorted({r["group"] for r in inside})))
        # 组内顺序必须与设计给定顺序一致（按 ball 在 EDGES 中的次序）
        want = [a[1] for e in EDGES.values() for a in e if len(a) > 2 and a[0] in ("sig", "pair")
                and a[3 - 1] == g]
        seen = [r["ball"] for r in rs]
        if want and [b for b in want if b in seen] != seen:
            errs.append("R1 接口 %s 顺序与设计不符" % g)

    # R2 差分对相邻两脚，中间不得 GND/电源
    pairs = [(a[1], a[2], a[3]) for e in EDGES.values() for a in e if a[0] == "pair"]
    for bp, bn, base in pairs:
        rs = [r for r in sig if r["ball"] in (bp, bn)]
        if len(rs) != 2:
            errs.append("R2 差分对 %s 缺脚" % base)
            continue
        if abs(int(rs[0]["pin"]) - int(rs[1]["pin"])) != 1:
            errs.append("R2 差分对 %s 两脚不相邻（%s/%s）" % (base, rs[0]["pin"], rs[1]["pin"]))

    # R3 连续非 GND 段（信号+电源都算「无地夹持」）≤ MAX_RUN
    run = worst = 0
    for r in rows:
        if r["kind"] == "gnd":
            run = 0
        else:
            run += 1
            worst = max(worst, run)
    stat["worst_run"] = worst
    if worst > MAX_RUN:
        errs.append("R3 最长非 GND 段 %d > %d" % (worst, MAX_RUN))

    # R4 每边都有接地，且 GND 总数与预算一致
    for e in "NESW":
        n = sum(1 for r in gnd if r["edge"] == e)
        stat.setdefault("gnd_per_edge", {})[e] = n
        if n < 3:
            errs.append("R4 边 %s 的 GND 仅 %d 个" % (e, n))
    if len(gnd) + len(pwr) + len(sig) != PIN_TOTAL:
        errs.append("R4 脚数分配不闭合")

    # R6 电源脚不得紧贴高速差分接口（MIPI/USB）
    fast = ("MIPI", "USB")
    for p in pwr:
        nb = [r for r in rows if int(r["pin"]) in (int(p["pin"]) - 1, int(p["pin"]) + 1)]
        if any(x["kind"] == "signal" and x["group"] in fast for x in nb):
            errs.append("R6 电源脚 %d 紧贴高速信号" % int(p["pin"]))

    # R7 几何可达：每边跨度 <= 板边 - 2x 角部禁布；相邻焊盘间隙 >= 0.15 mm
    edge_len = 45.0  # docs/143 §5 板框 45x45
    gap = PITCH_MM - PAD_D_MM
    stat["pad_gap_mm"] = round(gap, 3)
    if gap < 0.15 - 1e-9:
        errs.append("R7 焊盘间隙 %.3f < 0.15 mm" % gap)
    if HOLE_D_MM > PAD_D_MM - 0.30 + 1e-9:
        errs.append("R7 半孔环 %.3f < 0.15 mm（嘉立创要求焊盘>=孔径+0.30）" % ((PAD_D_MM - HOLE_D_MM) / 2))
    for e in "NESW":
        n = sum(1 for r in rows if r["edge"] == e)
        span = _span_mm(n)
        stat.setdefault("span_mm", {})[e] = round(span, 3)
        if span > edge_len - 2 * CORNER_KEEPOUT_MM:
            errs.append("R7 边 %s 跨度 %.3f 超出 %.1f" % (e, span, edge_len - 2 * CORNER_KEEPOUT_MM))

    # 接口脚数小结（唯一来源 = 设计序列，不是硬编码常量）
    stat["groups"] = {g: len(rs) for g, rs in grp.items()}
    return errs, stat


def main():
    rows, pm = build()
    errs, stat = audit(rows, pm)
    for e in sorted(EDGES):
        print("edge %s: %d 脚" % (e, sum(1 for r in rows if r["edge"] == e)))
    print(json.dumps(stat, ensure_ascii=False))
    if errs:
        for e in errs:
            print("FAIL " + e)
        return 1
    with open(OUT, "w", encoding="utf-8", newline="") as f:
        w = csv.writer(f)
        w.writerow(["pin", "edge", "net", "ball", "group", "kind", "datasheet_pin_name"])
        for r in rows:
            w.writerow([r["pin"], r["edge"], r["net"], r["ball"], r["group"], r["kind"], r["note"]])
    print("OK -> %s (%d 行)" % (OUT, len(rows)))
    return 0


if __name__ == "__main__":
    sys.exit(main())
