# AI 驱动 EDA（立创EDA Pro + KiCad）MCP —— 开源方案调研与架构建议

调研日期：2026-10-27
目标：做一个 MCP，让 AI 在 **立创EDA 专业版** 和 **KiCad** 里边画原理图、边画 PCB。

---

## 0. 结论先行

**不要自己从零发明协议层，但要自己拥有「领域模型层」。**

推荐路线：

```
        ┌──────────────── MCP Server（我们自己的仓库）────────────────┐
        │  Tool 层：list_components / place / wire / connect /        │
        │           run_erc / run_drc / layout / route / export ...   │
        │  ↓                                                          │
        │  EDA 领域模型（统一 IR）：Project / Sheet / Component / Pin / │
        │           Net / Wire / Footprint / Pad / Track / Via / Rule  │
        │  ↓                              ↓                           │
        │  Adapter: KiCad                 Adapter: LCEDA Pro           │
        └────┬────────────────────────────┬───────────────────────────┘
             │                            │
   kicad-python(IPC, PCB)          WebSocket ↔ 扩展(.eext)
   + S-expr 文件改写(原理图)         （eda 对象 / SCH_* / PCB_*）
   + kicad-cli(ERC/DRC/导出)
```

理由（全部有事实支撑，见 §1）：

1. **两家的"可编程面"完全不对称**：立创EDA Pro 的扩展 API 是**原理图 + PCB 全量、在线、有官方文档**；KiCad 的官方 IPC API 到 **KiCad 10 仍然只支持 PCB 编辑器**，原理图要么等 KiCad 11，要么做 **S-expression 文件改写**。
2. 所以"两个 EDA 共用一套 tool 名"这件事，只能靠中间那层 IR 抹平，不能靠底层对齐。
3. 上层 Tool 语义、下层 Adapter 机制，都能在白嫖开源项目后大幅缩短工期 —— 现成项目已经有 250+ 工具的完整语义清单可以直接抄命名与参数设计。

---

## 1. 先钉死事实：两个 EDA 到底能怎么被程序操作

| 维度 | 立创EDA 专业版 (LCEDA Pro) | KiCad 9 / 10 |
|---|---|---|
| 官方脚本 API | **扩展 API（JS/TS）**，`eda` 对象，类前缀 `SCH_*` / `PCB_*` / `DMT_*` / `SYS_*` | **IPC API**（protobuf + NNG，`kicad-python` / kipy） |
| 原理图可写 | ✅ `ISCH_PrimitiveComponent` / `Wire` / `NetLabel` / `Bus` / `Pin` 等图元齐全 | ❌ **KiCad 9/10 的 IPC 插件只在 PCB 编辑器里**；原理图插件要到 **KiCad 11**。9/10 只能改 `.kicad_sch` 文本（S-expr） |
| PCB 可写 | ✅ `IPCB_PrimitiveComponent` / `Track` / `Via` / `Pour` / `Region` / `Pad` / `Dimension` / `Image` | ✅ IPC API（那是它 9.0 的首批用例） |
| 在线生效（不用重开文件） | ✅ 直接操作编辑器内存对象 | ✅ IPC 直连运行中的 GUI；SWIG 文件路线需重载 |
| 无 GUI 头 | ❌ 扩展必须跑在编辑器里 | 部分：`kicad-cli` 做 ERC/DRC/导出/netlist；**headless IPC 要 KiCad 11** |
| DRC / ERC | ✅ `SCH_Drc` / `PCB_Drc` | ✅ `kicad-cli sch erc` / `pcb drc` |
| 元件库 | ✅ 立创/LCSC 商城库，扩展内搜索 | ✅ 本地 `.kicad_sym` / `.pretty`；无官方在线库 |
| 厂商路线图风险 | 低（官方主推 AI 网关） | ⚠️ **SWIG（`import pcbnew`）已弃用，KiCad 11 移除** ⇒ 只有 IPC 是前向兼容 |
| 外部程序通信 | 扩展 ↔ WebSocket（需在扩展市场里勾选"外部交互权限"） | IPC 直接连 socket/命名管道；`KICAD_API_SOCKET` / `KICAD_API_TOKEN` |

来源：`prodocs.lceda.cn/cn/api/`（含 `SYS_WebSocket` 文档与 For-AI 初始化指南）、`dev-docs.kicad.org/en/apis-and-binding/ipc-api/for-addon-developers/`、PyPI `kicad-python`。

### 关键推论

- **立创侧**：唯一正统做法 = 写一个 `.eext` 扩展，在扩展里开 WebSocket 连本地 bridge，把 `eda` API 包成命令。这是社区所有立创 MCP 的共同架构，也是官方 `run-api-gateway` 的做法。**端口 49620–49629 已是事实上的约定**。
- **KiCad 侧**：PCB 走 IPC（在线、可撤销、毫秒级）；原理图只能 S-expr 改写（无撤销、无热加载），或者干脆用 `kicad-skip` 这种现成库。**没有人绕得过去。**
- 因此"AI 看着画面画图"这件事，在立创侧天然可以做到（在线），在 KiCad 侧原理图阶段做不到（只能改文件再让用户 F5 重载），PCB 阶段可以。

---

## 2. 候选项目清单

### A. KiCad 系

| 项目 | 许可 | 语言 | 机制 | 规模 | 值得抄什么 |
|---|---|---|---|---|---|
| **mixelpixx/KiCAD-MCP-Server** | MIT | Python + Node 桥 | SWIG(`pcbnew`) + IPC + `kicad-cli` + S-expr | **247 个 tool / 20 类** | **当前最完整的参考实现**。tool 命名与分类（board/component/routing/schematic/export/drc/library/symbol_library/…）、toolbox 按需加载防上下文爆炸、`{ok, error}` 结构化返回、S-expr 动态符号注入 + 引脚坐标旋转换算、增量写盘自校验 |
| **mixelpixx/Konnect** | AGPL-3.0（商用需授权） | Rust 单二进制 | 原生 KiCad 10 插件 + 官方 IPC | 171 tool | 演示了"单二进制零运行时依赖 + 原生插件"的终态形态；**AGPL 慎用作代码来源，只做设计参考** |
| **salitronic/eda-agent** | Apache-2.0 | TypeScript/Python | Altium 为主，可选 KiCad / EasyEDA Pro | **560+ tool** | **唯一同时支持 "Altium + KiCad + EasyEDA Pro" 的开源实现**，和我们"多后端一套 tool"的诉求最像；重点看它的后端抽象层 |
| **Seeed-Studio/kicad-mcp-server** | MIT | Python(fastmcp) | `pcbnew` + `kicad-cli` | ~30 tool | 分析/校验向。**简洁、可读性最好**，适合当"第一版骨架"直接 fork；诚实标注了"原理图编辑 experimental" |
| **kicad-eda-mcp**（PyPI） | 见仓库 | Python | IPC + CLI + SWIG，自动拉起 KiCad | 22 router / ~300 op | "IPC→CLI→SWIG 三层降级"的调度设计；`{ok,message,error,data}` 返回约定 |
| **eda-copilot**（biosshot，EasyEDA Copilot 的 KiCad 版 / KiCad Copilot） | MIT | TypeScript | 纯文件操作（不依赖 IPC、不装插件） | — | **PCB 布局用紧凑 JS DSL 描述约束 → 先出 PNG/SVG 预览 → 批准再落盘**，这条"AI 提案 + 人审"的交互模式非常值得抄；另含本地布线器 `eda-copilot-router`(KRT) |

### B. 立创EDA / EasyEDA Pro 系

| 项目 | 许可 | 机制 | 覆盖 | 备注 |
|---|---|---|---|---|
| **oshwhub/run-api-gateway**（官方扩展，jlc-ext 商店） | Apache-2.0 | 扩展 ↔ WS(49620-49629) ↔ Node bridge | 通用「执行 JS 代码」网关 | **官方背书**。配合 `easyeda-api` Skill（内置整站 API 文档，供 AI 本地检索）。这是立创侧最省事的起点 |
| **lyq11/yuanlitu-mcp-service-public**（元狸图） | Apache-2.0，**只发二进制、无源码** | 扩展 + 本地 MCP 进程 | 原理图读/写/DRC/库搜索，**明确不做 PCB 布线** | 实测过 6 个大模型跑 STM32 核心板，有公开横评；tool 语义设计可直接参考，但**闭源不可改、不可二次分发** |
| **@vlabsoft/easyeda-pro-mcp** | 见 npm | MCP stdio + WS 8765 + 扩展 | 读/导航/导出 + **写操作必须显式确认**（`easyeda_confirmed_action`） | "写操作强制二次确认"的安全模型值得抄 |
| **oaslananka/easyeda-mcp-pro** | 见仓库 | 扩展 + Node server | 有 `config/easyeda-compatibility.json`、`docs/COMPATIBILITY.md`、feature-maturity 机制 | **兼容性矩阵 + 功能成熟度门禁**这套文档工程做法值得抄 |
| **easyeda-copilot**（biosshot） | MIT | 扩展 + MCP | 原理图生成、LCSC 搜索、PCB 布局、DRC 配置、SPICE 仿真 | MIT 且 **同时有 KiCad 版**，是"一套语义两套后端"的现成范例 |

### C. 结论：起点选谁

- **KiCad 侧骨架**：fork **Seeed-Studio/kicad-mcp-server**（MIT、小而清），补齐能力时对照 **mixelpixx**（MIT、大而全）逐条抄。
- **立创侧**：**自己写扩展**，但把 `run-api-gateway` + `easyeda-api` Skill 当作"官方文档加速器"；tool 语义参考**元狸图**与 **easyeda-copilot**。
- **架构分层**：参考 **salitronic/eda-agent** 的多后端抽象。
- **许可证红线**：Konnect 是 AGPL-3.0，**不要把它代码搬进我们的仓库**。

---

## 3. 建议的领域模型（IR）—— 我们真正要自研的部分

两个后端的图元命名完全不同，硬对齐会漏。建议先定义最小可用的 9 个对象，只做"能画出可用板子"所需的那部分：

```
Project  → sheets[] / pcb
Sheet    → components[] / wires[] / labels[] / buses[] / ports[]
Component(ref, lib_id/mpn, value, footprint, x, y, rot, mirror, props{})
Pin(owner, number, name, type, x, y)              # 坐标必须绝对化
Net(name, pins[])                                 # 连通的唯一真源
Wire(segments[[x1,y1,x2,y2]], net)                # 或 NetLabel 隐式连接
Label(name, kind: local|global|hier|power, x, y, rot)
Footprint(ref, pads[{num,x,y,w,h,shape,layer}], x, y, layer, rot)
Track(net, layer, width, points[]) / Via(x,y,net,r,drill)
Rule(class, clearance, width, ...)
```

**关键设计约束（来自踩坑经验）**

1. **连接只认一种真源**：要么几何线段求交，要么网络标签名字匹配。**不要混用**——KiCad 的 `add_wire` 端点没吸到引脚时就是一根浮线，但工具会返回成功。⇒ 每次写线后必须回读端点坐标做吸附断言，不吸附就报错。
2. **坐标必须绝对化 + 网格吸附**：KiCad 1.27 mm / 立创自有栅格。原理图离栅格会让 junction 静默失效。
3. **组件引用（refdes）必须由服务端注解**，不能让模型自己编（`R1/R2` 撞号是高频事故）。
4. **每次写操作前自动快照**（元狸图的做法：会话首次写自动备份），并给出 `restore_checkpoint`。
5. **写操作分级**：`read` / `low-risk write`（放元件、画线）/ `high-risk write`（删网络、覆铜、自动布线、批量移动）——高风险强制 `confirm` 参数携带人类可读确认文本。
6. **未实现就报未实现**，不要返回"看起来成功"。立创侧 PCB 自动布线、KiCad 侧原理图在线编辑，都要在 tool 描述里显式标注 `unsupported / file-only`。

---

## 4. 第一版范围建议（2 周内能出可用 demo）

**做（MVP）**

- 立创 Pro 扩展：`eda` 封装 + WS bridge + 会话心跳/重连
- KiCad adapter：`kicad-python`(IPC/PCB) + `.kicad_sch` S-expr(原理图) + `kicad-cli`(ERC/DRC/导出)
- 统一 tool 集（约 25 个）：
  - 项目：`open_project` / `project_info` / `checkpoint` / `restore`
  - 库：`search_parts` / `get_part` / `place_component`
  - 原理图：`add_component` / `move` / `rotate` / `add_wire` / `connect_to_net` / `add_label` / `add_no_connect` / `list_nets` / `annotate` / `run_erc`
  - PCB：`sync_from_schematic` / `set_board_outline` / `place_footprint` / `list_unrouted` / `add_track` / `add_via` / `run_drc` / `export_gerber+bom+pos`
  - 视觉闭环：`get_schematic_image` / `get_board_image`（AI 自检的必要条件）

**不做（第一版明确砍掉）**

- 自动布线（KiCad 侧外挂 Freerouting 即可，别自己写）
- 高速信号 / 阻抗 / 等长 / 电源完整性分析
- 3D 与结构协同

**验收判据**（沿用"宁可报错不给错答案"）

- 同一份 netlist 输入，两个后端产出的 ERC 都 0 error；
- 生成的板子 `kicad-cli pcb drc` 0 error（或明确列出剩余 violation 及原因）；
- 每个写 tool 都有"写后回读断言"，失败即 error，不返回 ok。

---

## 5. 已知的坑（提前记账）

1. **立创扩展的"外部交互权限"**：不勾就 `SYS_WebSocket` 恒 throw。分发时要写进安装说明。
2. **端口占用 49620–49629** 被官方网关与多个社区项目共用，多开必然打架 ⇒ 我们自己的方案要么沿用该区间并做握手识别（`easyeda-bridge` 那种），要么换独立区间 + 明确文档。
3. **KiCad 原理图改写无撤销**：用户误以为 Ctrl+Z 能救，实际救不了 ⇒ 必须自带 checkpoint。
4. **KiCad 11 才是分水岭**：headless IPC + 原理图插件 + plot 导出。架构上要预留"到时把 S-expr 后端换成 IPC 后端"的抽象。
5. **SWIG 是死路**：不要在新代码里依赖 `import pcbnew`（KiCad 11 移除）。
6. **AI 视觉自检是刚需**：所有成熟项目都提供 schematic/board 截图 tool。模型不见图就会"以为连上了"——公开横评里就有模型把 MCU 电源脚接错还自认成功。

---

## 6. 参考链接

- 立创EDA 专业版扩展 API 文档：https://prodocs.lceda.cn/cn/api/
  - For AI 初始化指南：https://prodocs.lceda.cn/cn/api/guide/how-to-start-for-ai.html
  - `SYS_WebSocket.send`：https://prodocs.lceda.cn/cn/api/reference/pro-api.sys_websocket.send.html
- KiCad IPC API（官方）：https://dev-docs.kicad.org/en/apis-and-binding/ipc-api/for-addon-developers/
- kicad-python：https://pypi.org/project/kicad-python/
- mixelpixx/KiCAD-MCP-Server：https://github.com/mixelpixx/KiCAD-MCP-Server （MIT，247 tools）
- mixelpixx/Konnect：https://github.com/mixelpixx/Konnect （AGPL，Rust，KiCad 10 原生插件）
- Seeed-Studio/kicad-mcp-server：https://github.com/Seeed-Studio/kicad-mcp-server （MIT）
- salitronic/eda-agent：https://github.com/salitronic/eda-agent （Apache-2.0，Altium/KiCad/EasyEDA Pro）
- biosshot/easyeda-copilot：https://github.com/biosshot/easyeda-copilot （MIT，含 KiCad Copilot）
- easyeda-copilot-mcp (npm)：https://www.npmjs.com/package/easyeda-copilot-mcp
- run-api-gateway（官方扩展）：https://jlc-ext.com/item/oshwhub/run-api-gateway
- lyq11/yuanlitu-mcp-service-public：https://github.com/lyq11/yuanlitu-mcp-service-public （Apache-2.0，二进制）
- @vlabsoft/easyeda-pro-mcp：https://www.npmjs.com/package/@vlabsoft/easyeda-pro-mcp
- oaslananka/easyeda-mcp-pro：https://deepwiki.com/oaslananka/easyeda-mcp-pro
