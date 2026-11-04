---
name: pcb-agent-mcp-usage
description: 正确使用 pcb_agent_mcp 这个 MCP 服务画板与算电路——191 个工具怎么发现（默认只广告 core 子集，其余走 tool_search + tool_call）、读板/写板的六阶段工作流（量事实 → 找空间 → 定意图 → propose → apply → 验）、哪些读数权威（board_drc）哪些不是（board_pour_connectivity 只是中心点在多边形内）、body 体积与 48 KiB 对话预算（saved_to 落盘、大板文件要切片读）、已退休的 board_route / board_fanout、经验库 experience_* 的写入门槛、以及一批真实踩过的坑（.kicad_pcb 全在一行、层名是 "F.Cu" 不是 "F"、过孔压焊盘不算连通、无网焊盘仍是障碍）。适用于任何要调用 pcb_agent_mcp 或排查它「为什么没读到/为什么写坏了/为什么答案不对」的场合。用户说「用这个 mcp」「工具怎么调」「为什么布不通」「读数对不对」时触发。
---

# pcb_agent_mcp 使用手册

## 会话断了怎么办（board_journal）

1. 先 `board_journal(action="resume")`：从最近一次成功落盘开始，带它之后的 note，回包上限 4 KiB。**它只说明上次的人写了什么，不说明板子是对的。**
2. 再读板子本身（`board_stats` / `board_drc`）核对；两者对不上时以文件为准。
3. 每做完一个网络组，用 `kind="note"` 记一行「下一步是什么、卡在哪」——`board_apply` 成功时会自动留 apply 行，但「为什么停在这」只有人能写。
4. `PCB_AGENT_JOURNAL=0` 关掉时自动落行静默跳过，`resume` 回 `status=disabled` 并报出磁盘原有条数。

## 三件必须先知道的事

1. **它注册了 191 个工具，但默认只广告 `core` 子集（约 45 个）+ 两个元工具。**
   这是故意的：把 301 KB 的工具清单塞进上下文会直接把对话撑爆。
   ⇒ 想用的工具不在清单里，就先 `tool_search`（按**任务**搜，支持中文任务名词，
   例如「阻抗」「回流」「铺铜」），再 `tool_call` 执行。**不要臆造工具名，也不要因为
   「清单里没有」就断言「这个服务做不到」。**
2. **工具数与环境变量会变。** 本文写的 190 是 `0.0.111` 的数；任何判断以
   `calc_capabilities` / `tools/list` 的当前返回为准。`PCB_AGENT_TOOLSET=all` 可广告全部。
3. **它不替你设计。** 它给事实、给度量、给规则判定；摆哪里、走哪条，是人（或按
   `pcb-layout-methodology` 技能）决定的。`board_route` / `board_fanout` 已**退休**，
   按名调用回 `TOOL_RETIRED`——这是刻意的，不要再去找替代的自动布线。

## 六阶段工作流

### 阶段 1：量事实（只读，先别动）
| 想知道 | 工具 |
|---|---|
| 板子多大、几层、多少网、多少器件、层叠与 setup | `board_summary` |
| 器件清单与每层分布（验单面贴片） | `board_components` |
| 网表与网络类（clearance / trace_width / via） | `board_nets` |
| 每个焊盘的精确尺寸/钻孔/旋转/网（这是所有几何推论的源头） | `board_view` |
| 铺铜到底铺出来没有 | `board_zones` |
| 项目里到底写了什么限制（线宽/间距/网络类）、我画的贴不贴边 | `board_constraints_report` |
| 器件真实外框（courtyard/丝印/焊盘三种盒） | `board_footprint_extents` |
| 走线的间距、直角/锐角、线对焊盘侵入 | `board_geometry_check` |
| 某块区域还有没有合法落点 | `board_pad_obstacle_map` |
| 这组配对能不能零交叉扇出（画之前判） | `board_fanout_order_check` |
| 权威 DRC + 未连通项 | `board_drc` |

**判据类工具只判不画。** 本服务**没有**自动布线/自动布局（`board_route`/`board_fanout`
已退休，`PCB_AGENT_TOOLSET=all` 也拿不回来）。上面这五个工具回答「现在这样行不行 / 这个方案
可行吗」，几何全靠自己算。

**读约束必须读项目文件**：`min_track_width` 之类的限制住在 `.kicad_pro` 的
`design_settings.rules` 里，不在 `.kicad_pcb` 里。`board_constraints_report` 没拿到项目文件时
报 `PROJECT_ABSENT` 而不是拿一个默认值充数。它还会把「文件里写了什么」与「引擎实际执行哪个」
分开报（`contradictions`，例如 `min_clearance=0.000` 与 `Default.clearance=0.200` 同时存在时，
引擎用的是后者）。

**`board_view` 是一颗体积炸弹**：它会回 nets、net classes、layers、stackup、setup、
footprints、pads、tracks、vias、zones（含填充多边形）、Edge.Cuts、graphics、texts。
一块 88 MB 的板子用它一次就能把会话塞满。做法：**先用 `board_summary` 看规模，
再按层/按网/按区域切片取**；大结果只读 `saved_to` 落盘的 JSON，不要把整段读进上下文。

### 阶段 2：找空间与找阻挡（摆放阶段用）
| 想知道 | 工具 | 它明确不回答 |
|---|---|---|
| 新器件还能放哪 | `board_free_space` | 「板子有多密」 |
| 每个焊盘能不能出线、每层拥挤度 | `board_routability` | 「哪条路最优」 |
| 某个焊盘/某点八向射线里谁挡路、能不能挪 | `board_blockers` | 全局布线可行性 |

判读要点：**「焊盘出不去」是摆放级硬阻断，任何路由器都修不了**；
「需要 dogleg 才能出」对人没问题，对直线扇出就不行。

### 阶段 3：定意图（在布任何线之前，把约束写成 JSON）
`board_intent`：一个 version 1 的对象，含 `nets.route` / `nets.layers`，
以及 `constraints`（clearance / track / via / creepage / impedance / diff-pair 等）。
它**在布线之前**拿设计意图去核板子。意图写不出来，说明约束还没定（回到
`pcb-layout-methodology` 的第 0 步）。

### 阶段 4：propose（不写盘）
`board_propose` 返回：`plan_id`、改动前后的 board digest、**每条操作将要被检查的
postcondition**、以及阻断原因（板框没闭合、会新增 DRC 违约、走线出框）。
磁盘上什么都没发生。**看 postcondition 列表，就是在看这份提案会被怎么验。**

### 阶段 5：apply（写入，并自证）
`board_apply` 会：先取 checkpoint → 原子写入 → 过四道不信任写入代码的闸门
（磁盘字节 == 计划字节；重新解析得到计划树；每条 postcondition 从文件里重读；
…）。checkpoint 在 `$PCB_AGENT_HOME`（默认 `~/.pcb_agent_mcp`），不在工程目录边上。
回滚用 `board_checkpoints` 列表 + `board_restore`。

### 阶段 6：验（分权威与不权威）
| 结论 | 权威工具 | 说明 |
|---|---|---|
| 有没有 DRC 违约、有没有未连通 | **`board_drc`** | 跑 KiCad 自己的引擎。**它和 `board_clearance` 打架时信它。** |
| 铺铜把网的焊盘连上了吗 | `board_pour_connectivity` | **非权威**：中心点在内 ≠ 连通，它看不见热焊盘辐条、过孔、走线、焊盘自身铜；参考板上它把 13 个已连通的焊盘判成孤立 |
| 铺铜实际铺了多少 | `board_fill_zones` | 默认铺**副本**只测量；`in_place=true` 才把 KiCad 的多边形搬进你的文件 |
| 信号有没有跨分割 | `board_cross_split` | **没有平面铺铜时拒答**——因为「没找到分割」会让坏层叠变绿 |
| 环路面积 | `board_loop_area` | **没有层叠时拒答**，猜介质厚度就是猜 EMC |
| 按规则目录打分 | `layout_review`（先 `design_rules` 拿目录） | 事实缺失一律 `undetermined`，不给你一份全绿的假报告 |
| 工艺能力 | `dfm_check`（拿真实能力表）+ `dfm_capability` | |
| 出图/交付 | `board_fab_package`、`board_export_graphics`、`board_export_3d` | Gerber/钻孔/坐标/STEP，逐件报来源与设置 |

## 读数的纪律（这个服务的设计意图）

- **信封**：`status` / `value` / `basis` / `assumptions` / `precision` / `reason_code` / `authoritative`。
  看到 `reason_code` 就停下读它，`ARG_MISSING` 表示**你少给了参数**，
  `OUT_OF_VALIDITY` 表示**超出模型有效域**，`DATA_UNAVAILABLE` 表示**数据表里没有**。
  这三类都不是「答案是 0」。
- **拒答优于错答**：`board_cross_split`、`board_loop_area`、`decoupling_check`、
  `ir_drop` 类都会在缺参数时拒答或给 `INDETERMINATE`。**不要为了要一个数而补一个猜的参数。**
- **`authoritative` 字段**：同一件事有权威与非权威两个读数时，报告里必须写明用的是哪个。
- **单点读数失败不否证被测对象**：某个网布不通，可能只是这一版摆放不行。

### 改服务自己的代码时：每条后置条件必须有能失败的反向对照

`board_propose` 的每个 op 都带后置条件（写完了读回来对不对）。后置条件写错的代价是
**整批回滚**：2026-11-04 一次真实编辑 19 个 `move_footprint` 挂了 17 个，报
`postcondition check failed` 而 `failed_detail` 是 `None`。真因是校验函数要求文件里
**显式写出角度**，而 KiCad 把 `(at x y)` 当作角度 0——它把自己写出的结果判成了错。

所以：
1. 新写/修改一个后置条件，必须在**一块真实工程板**上跑一遍全部 op（不是砂盒上的一两个）。
2. 每种“合法但看着不典型”的写法都要有夹具：角度 0 写成省略、铺铜未 fill、零宽丝印线段。
3. **反向对照是必须的**：一个判据如果从来不会说「不行」，它就没有在判。
   本仓库的做法写在各工具的 `reverse_control` 字段里（例如 `board_geometry_check`
   的直角/锐角/45° 三块小夹板，`board_fanout_order_check` 的反单调映射）。
4. 不许用「回滚成功」当作「对的」——回滚只能证明它拒绝了自己，不能证明它接受了对的。

## 体积与预算（血泪）

- 对话侧默认上限 **48 KiB**；`tools/list` 自己的预算默认 **25 KiB**（可裁剪阶梯）。
- 超限时回复会被**截断**并把完整 JSON 写到 `saved_to`。**分页 ≠ 安全**：分页只是把
  体积挪了地方，真正要做的是「一开始就别取那么大」。
- 具体做法：
  1. 先 `board_summary`（小），需要细节再按**层、网、区域**取。
  2. 需要整表就让它落盘，然后**用 python 逐块读**，不要 `cat`。
  3. 不要在一条回复里贴两个大 JSON。

## 经验库（`experience_*`）

这是「从别人的板子里学」的机制，分两类且**不可互换**：
- **HARD**：有失败面的规则，失败面在板上可测（例如过孔环宽 < 0.10 mm）。
- 另一类是观察性的，只能在特定前提下评估。

写入门槛（照做，别绕）：
1. `experience_recall` 先看已有条目；`experience_check` 对当前板子只读打分
   （`violated` 是一条 finding，不是自动修复）。
2. 新观察 → `experience_candidate`（候选，**不会被应用、不会自己变成规则**）。
3. 升级 → `experience_promote` 必须 `confirm=true` + **人的理由**；
   「计数跨过阈值」不算理由。
4. 真实板上是否成立 → `experience_outcome`（只记命中/违反计数，**不改条目状态**）。
5. 导出/导入 → `experience_export` / `experience_import`。

## 已退休与禁止

- `board_route`、`board_fanout`：**退休**。按名调用 → `TOOL_RETIRED`。
- 任何形式的自动布局 / 自动布线：不做，也不写新工具替代。
- 写盘路径只有 `board_propose → board_apply`（原理图同理 `schematic_propose → schematic_apply`）。
  **不要用文本编辑去改 `.kicad_pcb` 绕过它**。
- 用户的工程目录（例如 `LCEDA-Pro` 下的）**只读**；实验在副本上做。

## 常见坑（都是真踩过的）

1. **`.kicad_pcb` 常常整个文件只有一行**（100 KB 在一行里）。`grep -n` 毫无意义，
   要用括号配对切片或正则，并记住「行号」这个概念不存在。
2. **层名是字符串 `"F.Cu"` / `"B.Cu"`**，不是 `"F"` / `"B"`。按 `!= "B"` 过滤会得到空结果
   并看起来像「这一层没东西」。
3. **过孔压在焊盘里不算连通**（本项目朴素检查器用中心点距离 < 0.02 判连通，
   且电气上也不该这么做）。换层点放焊盘外侧。
4. **无网焊盘（`net` 为 None）仍然是障碍**，间距检查不会跳过它。
5. **THT 焊盘**可以从任意层直接落到 pad 中心（点在多边形内 ⇒ 距离 0 ⇒ 判连通），
   这和 SMD 的行为不同。
6. **`board_pour_connectivity` 不是权威**（见上），铺铜连通性最终以 `board_drc` 的
   unconnected 为准。
7. **`_scratch/geo.py` 这类自写检查器**的门限（centroid `hit=0.02`、track↔pad 中心线 ≥0.275、
   via↔track 保守取 0.645）是**本项目的本地口径**，不是本服务的语义；两者冲突时信 `board_drc`。
8. **别把 `board_view` 当 `board_summary` 用。**
9. **`saved_to` 里的 JSON 是完整答复**，截断只是显示问题；先读 `saved_to`，再决定要不要全部读。

## 环境变量

| 变量 | 作用 |
|---|---|
| `PCB_AGENT_TOOLSET` | 工具档位；`core`（默认）/ 逗号分隔名 / `all`。过滤后仍强制保留 `tool_search` + `tool_call` |
| `PCB_AGENT_HOME` | checkpoint 与经验库的落地目录，默认 `~/.pcb_agent_mcp` |
| `PCB_AGENT_CALL_TIMEOUT_S` | 单次调用超时（默认是否为有限值见 `docs/109` 的欠账，别假设） |
| `PCB_AGENT_NGSPICE` | SPICE 可执行路径（`sim_spice`） |

## 下一步可读

- 工具全表（按域分组，190 个名字）：`references/01-tool-map.md`
- 分场景检查单（读板 / 改板 / 出图 / 排障）：`references/02-workflow-checklists.md`
- 怎么摆、怎么布、怎么验收：另一份技能 `pcb-layout-methodology`
