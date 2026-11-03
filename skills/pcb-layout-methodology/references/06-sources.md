# 06 出处，以及哪些只核到条目

纪律：**二手博客只能当索引用，数值必须回到标准或厂商原件**。下面把「读了正文」与
「只见到条目」分开写，别把后者当证据。

## 读了正文（可直接引用其内容）

1. **IPC-2221B Table 6-1（导体间距的行与数值）**
   `https://rftools.io/blog/ipc-2221b-conductor-spacing/`
   给出 50 V 下 B1 = 0.1 / B2 = 0.6 / B3 = 0.6（低电压档，51–500 V 再放 2.5–10 倍）/
   B4 = 0.13 / A5 = 0.13 mm；涂层把间距改 4 倍以上；海拔 >3050 m 用 B3；
   IPC-2221B 只覆盖裸板功能性绝缘，安规要走 IEC 60664-1。
   **注意**：这是二手转述，写进正式报告前应回 IPC-2221B 原表核对行名与档位。

2. **ST：How to select a compatible crystal and load capacitors for STM32 with layout
   guidelines**（ST 社区文，2025-04-17）
   `https://community.st.com/stm32-mcus-60/how-to-select-a-compatible-crystal-and-load-capacitors-for-stm32-with-layout-guidelines-148169`
   提供：Pierce 振荡器结构；`gm ≥ 5·Gmcrit` 判据；`Cs = CIO + CPCB ≈ 3 pF`；
   `CL1 = CL2 = 2(CL − Cs)`；第 5 节布局要求——晶体与负载电容**各自**的 GND「底板」、
   装配层沿周边的地「护墙」+ 多个过孔、底板**单独接到最近的 VSS 引脚**（指向 AN2867 §7
   Figure 14/15）。

3. **Sierra Circuits：10 Best High-Speed PCB Routing Practices**（2025-10-29）
   `https://www.protoexpress.com/blog/best-high-speed-pcb-routing-practices/`
   提供：两层堆叠方案（一层信号+电源、一层完整地；两侧都走线则两面铺铜 + 缝合孔）；
   缝合间距 λ/20~λ/10；铺铜与信号留 1.5–2 倍线宽；离板边 10–15 mil；
   差分对同层/等长/等间距/同数过孔，90 Ω ±10%；换层处就近地过孔；
   缝合电容 10–100 nF；不跨分割。

## 只见到条目（未读正文，不得当数值证据）

4. **ST AN4879《Introduction to USB hardware and PCB guidelines using STM32 MCUs》**
   V13.0，2026-10-30（在 ST 官方文档索引页见到）。
   本仓库 `04-interface-rules.md` 的 USB 一节只写了通行做法，**没有引用它的具体数值**。

5. **ST AN2867《Guidelines for oscillator design on STM8AF/AL/S and STM32 MCUs/MPUs》**
   由第 2 条的正文反复指认为晶振布局的原始出处（§7 Figure 14/15）。本次**未取到 PDF 正文**；
   `gm`/`Gmcrit` 的产品表在它的 Figure 2/3 里，要用具体型号的 `gm` 必须回 AN2867 查。

6. **IPC-2221 载流图表拟合**（`I = k·ΔT^0.44·A^0.725`，k = 0.048 外层 / 0.024 内层）
   已在本仓库实现并有测试：`src/pcb_agent_mcp/calc/current.py`（`ipc2221` 与 `ipc2152`
   两个标准族并存，函数拒绝替调用方选标准）。

## 检索过但未采用

- 中文科普页（elecfans「pcb上下层铺地」「pcb板两层布线」）：结论与上面 1/3 一致，
  但无标准编号，**只当交叉验证，不作出处**。
- JLCPCB 间距指南、PCBSync 的 IPC-2221 综述：内容与 1 重叠，未逐行核。

## 使用方法

- 引数值 → 只用第 1/2/3 组，并标明「二手转述」。
- 引型号参数（gm、ESR、C0、CL）→ 必须回**器件数据手册**，不许从本文件抄。
- 引安规 → IEC 60664-1 + 产品标准，IPC-2221B 不覆盖。
