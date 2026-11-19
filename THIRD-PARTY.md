# 第三方组件与商标

本文件回答一个问题：**这个仓库里，哪些东西不是作者写的，它们按什么许可使用，作者动了什么。**
主许可证见 [LICENSE](LICENSE)（代码 PolyForm Noncommercial 1.0.0，文档 CC BY-NC-SA 4.0）。

---

## 1. 随包 ngspice（`vendor/ngspice/`）

| 项 | 值 |
|---|---|
| 程序 | ngspice（电路级仿真器） |
| 版本 | **ngspice-47**（creation date `Aug 11 2026 13:55:25`） |
| 来源 | <https://ngspice.sourceforge.io/> / <https://sourceforge.net/projects/ngspice/files/ng-spice-rework/47/> |
| 归档 | `ngspice-47_64.7z`（Windows 64-bit，13 814 879 B），sha256 `59225971bd68cdd1199443649aa4615a9e6d684933f205ab49006a3942518f5a` |
| 随包内容 | `bin/ngspice_con.exe` + 三个硬依赖 DLL + `lib/ngspice/*.cm` + `share/ngspice/scripts/spinit` + `COPYING` + `NOTICE.md` |
| 丢弃内容 | GUI、`.osdi` compact models、examples、文档 |
| 作者改动 | **无** —— 逐字节取自归档 |

**许可（混搭，逐项）**

| 组件 | 许可 |
|---|---|
| 前端 / 模拟器件 | Modified BSD（3-clause） |
| XSPICE code models（`lib/ngspice/*.cm`） | 公有领域（Georgia Tech） |
| `lib/ngspice/table.cm` | **GPLv2 or newer** |
| KLU 求解器 / numparam（编进 exe） | LGPLv2 / LGPLv2+ |
| `bin/*.dll`（sndfile / samplerate / libomp） | LGPLv2.1 / BSD / Apache-2.0-with-LLVM-exception |

`vendor/ngspice/COPYING` 是归档原样（未改动）；`vendor/ngspice/NOTICE.md` 是作者写的逐项说明。

**为什么这些许可能和本仓库的非商业许可共存**：它是**另一个程序**。本仓库只做
`subprocess.run`，不 include 它的头文件、不链接它的目标文件、不 vendored 一行源码——与
「shell 脚本调用 `grep`」是同一层关系。所以「许可共存」的前提是**独立进程**这个形态，不是偷懒。
据此，**本项目的打包约定**：

- 不修改 `vendor/ngspice/` 里任何一个字节。要升级就整目录替换成新的官方归档子集，并同步改
  `NOTICE.md` 里的版本号、归档大小与 sha256。
- 若将来非要打补丁，义务会从「指向上游源码」变成「公布你的源码」，其中 `table.cm` 还必须满足
  GPLv2+ —— 那等于把这一块变成 GPL 代码，必须在 `NOTICE.md` 里明说。
- `vendor/ngspice/` 可以整目录删除：`find_ngspice()` 只是不再看到它，`engine="auto"` 不受影响，
  `engine="ngspice"` 回答 `ENGINE_NOT_FOUND`，仓库其余部分不依赖它。

---

## 2. 立创 EDA 能力库（`src/pcb_agent_mcp/jlc/data/*.json`）

能力清单与规范数值移植自 **[jackwang988/jlceda-pcb-ai-workflow](https://github.com/jackwang988/jlceda-pcb-ai-workflow)**（**MIT**）。

保留的 MIT 声明（上游原文）：

```
MIT License

Copyright (c) jackwang988

Permission is hereby granted, free of charge, to any person obtaining a copy
of this software and associated documentation files (the "Software"), to deal
in the Software without restriction, including without limitation the rights
to use, copy, modify, merge, publish, distribute, sublicense, and/or sell
copies of the Software, and to permit persons to whom the Software is
furnished to do so, subject to the following conditions:

The above copyright notice and this permission notice shall be included in all
copies or substantial portions of the Software.

THE SOFTWARE IS PROVIDED "AS IS", WITHOUT WARRANTY OF ANY KIND, EXPRESS OR
IMPLIED, INCLUDING BUT NOT LIMITED TO THE WARRANTIES OF MERCHANTABILITY,
FITNESS FOR A PARTICULAR PURPOSE AND NONINFRINGEMENT. IN NO EVENT SHALL THE
AUTHORS OR COPYRIGHT HOLDERS BE LIABLE FOR ANY CLAIM, DAMAGES OR OTHER
LIABILITY, WHETHER IN AN ACTION OF CONTRACT, TORT OR OTHERWISE, ARISING FROM,
OUT OF OR IN CONNECTION WITH THE SOFTWARE OR THE USE OR OTHER DEALINGS IN THE
SOFTWARE.
```

作者在移植时做了三类改造（**这是衍生作品，不是原样复制**）：① 把叙事性清单拆成按
`kind` 索引的 JSON，每条带 `source` 字段；② 把「限值」与「查表阶梯」分开——限值一律要求调用方
给出能力文档，取不到就 `ARG_REQUIRED` / `SAFETY_TABLE_MISSING`；③ 给每个数补上「这条是抄的还是
量的」的标记，抄来的不冒充实测。

**边界**：MIT 只覆盖上游那一份；本仓库对这批 JSON 的改造部分按本仓库许可发布，两者不冲突
（MIT 允许再许可，但**保留原声明**这条义务仍然成立，所以上面那段原文不许删）。

---

## 3. 随包数据表（`src/pcb_agent_mcp/*.json`）

`magnetic_materials.json` / `powder_cores.json` / `dc_bias_fit.json` /
`toroid_mlt_reference.json` / `powder_curve_fits.json` / `material_loss_anchors.json` /
`creepage_tables.json` 等，是**厂商数据表与公开标准里的数字**，每行带来源 URL 或印刷页码。

- 这些是**事实数据**，不构成对原文的复制发行；本仓库不重发任何标准或数据表的正文。
- 引用时请回到原始来源核对（每个 JSON 条目里都有 URL / 页号），本仓库的数字只保证
  「当时这么读到」，不保证「现在厂商还这么写」。
- 安规类表（爬电/间隙）**故意不给默认值**：标准是付费文档，本项目只做查表与一致性检查。

---

## 4. 示例工程（`examples/`）

`examples/` 下的板子与原理图是**作者自己画的**（元件符号、封装、布局均为自建），
不是从厂商参考设计或开源硬件工程导入的原件。少数板子的网名/器件型号沿用了公开参考设计的命名，
但几何与文件内容不是复制品；如需引用外部工程，会在此处逐个列出。

---

## 5. 商标与免责

| 名称 | 权利人 | 本项目怎么用 |
|---|---|---|
| KiCad | KiCad E.D.A. 开发者团队 | 说明本工具读写 `.kicad_pcb` / `.kicad_sch` 并调用 `kicad-cli` / `pcbnew` |
| 立创 EDA / 嘉立创 / JLCPCB | 其各自权利人 | 说明本工具读写 `.epro`、对照其制板能力文档 |
| Rockchip / RK3506 | 瑞芯微电子 | 示例板所用 SoC 的型号 |
| ngspice | ngspice 项目 | 随包引擎名 |

本项目与上述任何一方**没有隶属、赞助、认证或背书关系**。仓库内不含任何厂商的私有库、模板、
SDK 或二进制（`vendor/ngspice/` 是唯一随包的三方二进制，且未改动）。

---

Copyright (c) 2026 xw19981010 —— 本文件属于文档，按 CC BY-NC-SA 4.0 发布；其中引用的第三方
许可原文按各自原许可发布。
