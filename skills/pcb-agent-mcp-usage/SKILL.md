---
name: pcb-agent-mcp-usage
description: 正确使用 pcb_agent_mcp 这个 MCP 服务画板与算电路——244 个工具怎么发现（默认只广告 core 子集，其余走 tool_search + tool_call）、读板/写板的六阶段工作流（量事实 → 找空间 → 定意图 → propose → apply → 验）、哪些读数权威（board_drc）哪些不是（board_pour_connectivity 只是中心点在多边形内）、body 体积与 48 KiB 对话预算（saved_to 落盘、大板文件要切片读）、已退休的 board_route / board_fanout、经验库 experience_* 的写入门槛、以及一批真实踩过的坑（.kicad_pcb 全在一行、层名是 "F.Cu" 不是 "F"、过孔压焊盘不算连通、无网焊盘仍是障碍）。适用于任何要调用 pcb_agent_mcp 或排查它「为什么没读到/为什么写坏了/为什么答案不对」的场合。用户说「用这个 mcp」「工具怎么调」「为什么布不通」「读数对不对」时触发。
---

# pcb_agent_mcp 使用手册

## 会话断了怎么办（board_journal）

1. 先 `board_journal(action="resume")`：从最近一次成功落盘开始，带它之后的 note，回包上限 4 KiB。**它只说明上次的人写了什么，不说明板子是对的。**
2. 再读板子本身（`board_stats` / `board_drc`）核对；两者对不上时以文件为准。
3. 每做完一个网络组，用 `kind="note"` 记一行「下一步是什么、卡在哪」——`board_apply` 成功时会自动留 apply 行，但「为什么停在这」只有人能写。
4. `PCB_AGENT_JOURNAL=0` 关掉时自动落行静默跳过，`resume` 回 `status=disabled` 并报出磁盘原有条数。

## 三件必须先知道的事

1. **它注册了 244 个工具，但默认只广告 `core` 子集（约 45 个）+ 两个元工具。**
   这是故意的：把 301 KB 的工具清单塞进上下文会直接把对话撑爆。
   ⇒ 想用的工具不在清单里，就先 `tool_search`（按**任务**搜，支持中文任务名词，
   例如「阻抗」「回流」「铺铜」），再 `tool_call` 执行。**不要臆造工具名，也不要因为
   「清单里没有」就断言「这个服务做不到」。**
2. **工具数与环境变量会变。** 本文写的 244 是当前数；任何判断以
   `calc_capabilities` / `tools/list` 的当前返回为准。`PCB_AGENT_TOOLSET=all` 可广告全部。
3. **它不替你设计。** 它给事实、给度量、给规则判定；摆哪里、走哪条，是人（或按
   `pcb-layout-methodology` 技能）决定的。`board_route` / `board_fanout` 已**退休**，
   按名调用回 `TOOL_RETIRED`——这是刻意的，不要再去找替代的自动布线。

## 六阶段工作流

### 阶段 0：先把判据装上（只在要「画一块好板」时）
| 想知道 | 工具 |
|---|---|
| 这套规则都有哪些、怎么分级 | `pcb_rule_catalog`（22 条目录 / 29 条 fab 限值 / 20 条 SMT，每条带原文引文） |
| 这块板**能不能造** | `board_rule_audit`（P0 工艺 / P1 电气 / P2 装配 / P3 信号质量 / P4 美观） |
| 好板子长什么样、我该画多宽 | `board_design_baseline`（13 块 P0 合格成品板的 p10/p50/p90 + 起点值） |

三条纪律：

1. **优先级不是建议，是顺序**。P0 不合格其余一律不用看；`verdict` 里 `blocked_at_P0_*`
   表示「造不出来」，不是「还能改进」。P0/P1/P2 阻塞写，P3/P4 只标 `flagged`。
2. **P0 必须声明工艺**，否则全列 `undetermined`——不许猜。声明要**按板子自己的层数**
   （层数从 stackup 数），拿 2 层去声明白一块 6 层板，得到的 unbuildable 说的是声明，不是板。
3. **`board_design_baseline` 不是判据**：`blocks_nothing`，落在 p10-p90 之外不说明错。
   它给的是起点值，被三样东西压过——P0 限值、铜要过的电流、实际工艺。

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
| **0.4 的电源线到底能不能出这个焊盘**（最宽能出去多少） | `board_routability` 加 `width_scan_mm=[0.6,0.5,0.4,0.3,0.25,0.15]` | 指出该在哪一段加宽 |
| 某个焊盘/某点八向射线里谁挡路、能不能挪 | `board_blockers` | 全局布线可行性 |
| **一条线切下去有几条通道、有几个网必须跨过去** | `board_cut_capacity` | 哪条网走哪条通道 |
| **这附近有没有合法过孔、余量多少** | `board_via_sites` | 这个孔有没有用、该不该放 |
| **这一网的焊盘簇之间，主干能走多宽、卡在哪一格** | `board_trunk_plan`（`net` 必填） | 路径怎么写、该走哪层 |
| **每个焊盘角上还有没有位置放泪滴、最长多少** | `board_teardrop_room` | 该不该加、给什么 curve |
| **这一网的铜断成几块、最近两块差多远在哪** | `board_net_gaps`（不给 `net` 就数全板） | 先连哪两块、换层还是挪件 |

判读要点：**「焊盘出不去」是摆放级硬阻断，任何路由器都修不了**；
「需要 dogleg 才能出」对人没问题，对直线扇出就不行。

三条最容易误读的读数：

- **「焊盘出不去」和「线上没位置」是两件事。** `board_cut_capacity` 报的是通道数；
  如果通道几百条而 `width_scan_mm` 报 0.3，堵点在焊盘逃逸宽度，不在线上。
  反例留在 `docs/125`：12 个网穿排针行曾被解释成「通道不够」，实测差两个数量级。
- **`board_via_sites` 的 `room_mm` 是「最紧那条约束的余量」**，`limited_by` 指名是哪条。
  铺铜默认不挡（灌铜会绕开新孔重切），`inside_pours` 说明这个孔会咬掉哪块平面；
  要严格口径用 `pours_block=true`。
- **`board_trunk_plan` 的宽度是「能走」，不是「走了」**：`beyond_reach` 是搜索半径
  的上界（上界会自称上界，别读成测量值）；`no_path` 与很窄的宽度都可能是栅格伪影，
  复核手段只有一个——把 `grid_mm` 降到该宽度的四分之一重跑（实测 `9->13` 在
  0.25/0.15/0.12 下是 0.164/0.314/0.345，粗栅格**把宽度读窄** `≈0.7·grid`）。
  只要超出 `band_mm`，工具什么都不说。
- **`board_teardrop_room` 的 `pad_width_mm` 是「走线离开焊盘处的弦」，不是焊盘外接尺寸**：
  0.3×1.6 的焊盘 45° 引出时，弦是 0.3 而不是对角线 1.6（按对角线读会把一整排排针判死）；
  `room_mm` 为负读成「放不下这么多」，不是「差这么多间距」；`max_length_mm` 不传给
  `limit` 之外的角（`limit=100000` 会从 5 s 变 98 s）。真板 238 个角只有 4 个不行，
  最紧 `R19.1 room −0.100`。
- **`board_net_gaps` 的「块」是我的几何口径，不是 DRC 的判词**：真板对得上的是
  `+3V3 38/38 / AGND 9/9 / $2N1188 6/6 / VDDA 5/5`（我的 `edges_needed` 对
  `board_drc().unconnected`），**对不上的是 GND（我 4、DRC 15）**——差的 11 条是
  只靠一个焊盘的散热辐条挂住的铺铜岛，我把它算成一个导体、KiCad 不算。
  过不过板子听 DRC 的；这个工具回答的是「在哪、差多远」。`gap_mm` 是金属到金属，
  量在它给出的那对 `seg` 上；超过 `gap_limit_mm` 的只计数不测量。
- **`board_routability` 不给线宽就是旧答案**：`width_scan_mm` 不传，`widest_escape`
  这个字段根本不出现——不是返回 0。

### 阶段 3：定意图（在布任何线之前，把约束写成 JSON）
`board_intent`：一个 version 1 的对象，含 `nets.route` / `nets.layers`，
以及 `constraints`（clearance / track / via / creepage / impedance / diff-pair 等）。
它**在布线之前**拿设计意图去核板子。意图写不出来，说明约束还没定（回到
`pcb-layout-methodology` 的第 0 步）。

### 阶段 4：propose（不写盘）
`board_propose` 返回：`plan_id`、改动前后的 board digest、**每条操作将要被检查的
postcondition**、以及阻断原因（板框没闭合、会新增 DRC 违约、走线出框）。
磁盘上什么都没发生。**看 postcondition 列表，就是在看这份提案会被怎么验。**

`propose` 的回包还带 `preconditions`：六条**机器可裁决**的前置状态——规则基线有据、
DRC 引擎在、板框已知（只对新增铜要求）、机械层没被动过（纯 `translate_board` 平移不算）、
本次计划没新造 <0.2 mm 跨网间距（**增量判据**，板子上原有的贴近对不算你的）、工艺是否已声明。
不是「没确认不许布线」，是「状态不对不许布线」：`verdict=refused_at_<check>` 时先修状态；
`options.preconditions=false` 可以关（关掉后回包里连 `preconditions` 键都没有）。

### 阶段 5：apply（写入，并自证）
`board_apply` 会：先取 checkpoint → 原子写入 → 过四道不信任写入代码的闸门
（磁盘字节 == 计划字节；重新解析得到计划树；每条 postcondition 从文件里重读；
…）。checkpoint 在 `$PCB_AGENT_HOME`（默认 `~/.pcb_agent_mcp`），不在工程目录边上。
回滚用 `board_checkpoints` 列表 + `board_restore`。

状态不对时 `apply` 抛 `PRECONDITION_NOT_MET`，`extra.preconditions` 带六条完整读数；
它复判用的是 `propose` 存下的同一份数（只有规则文件与引擎会重读），所以门和提案不会给出两个说法。

### 阶段 6：验（分权威与不权威）
| 结论 | 权威工具 | 说明 |
|---|---|---|
| 有没有 DRC 违约、有没有未连通 | **`board_drc`** | 跑 KiCad 自己的引擎。**它和 `board_clearance` 打架时信它。** |
| 铺铜把网的焊盘连上了吗 | `board_pour_connectivity` | **非权威**：中心点在内 ≠ 连通，它看不见热焊盘辐条、过孔、走线、焊盘自身铜；参考板上它把 13 个已连通的焊盘判成孤立 |
| 铺铜实际铺了多少 | `board_fill_zones` | 默认铺**副本**只测量；`in_place=true` 才把 KiCad 的多边形搬进你的文件 |
| 信号有没有跨分割 | `board_cross_split` | **没有平面铺铜时拒答**——因为「没找到分割」会让坏层叠变绿 |
| 环路面积 | `board_loop_area` | **没有层叠时拒答**，猜介质厚度就是猜 EMC |
| 按规则目录打分 | `layout_review`（先 `design_rules` 拿目录） | 事实缺失一律 `undetermined`，不给你一份全绿的假报告 |
| 工艺能力 | `dfm_check`（拿真实能力表）+ `dfm_capability` |
| 按优先级判「能不能造」 | `board_rule_audit` | P0 只认厂商能力库的引文；工艺未声明就不判，「全 pass」与「全未定」是两种答案 |
| 离好板子有多远 | `board_design_baseline` | **不判**：`blocks_nothing`，只给「在 p10-p90 之内/外」 | |
| 出图/交付 | `board_fab_package`、`board_export_graphics`、`board_export_3d` | Gerber/钻孔/坐标/STEP，逐件报来源与设置；**出包前跑一次 DRC，有 error 或未连通就 `FAB_REFUSED_UNCLEAN` 拒包**（`drc_gate=false` 只跳过检查，不会让脏板变干净） |

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
6. **量一块板就入库** → `experience_harvest`（path=板子）。它用同一份代码量 4 个单板能教的
   指标，把读数写进 `observations`，把与 active 习惯**相反**的写成反例候选（id 带
   `counterexample`）。写进去的全是 candidate，`confidence` 是 `single board (1/1)`。
   **hard 规则不从单板学**（板厂能力/规范才给），调用会直接拒。
7. 库活着吗 → `experience_status`：active 几条、候选几条没人确认、文件最后写入时间。

## 布局质量与拓扑（先量，再分区，才落坐标）

`board_drc` 只有通过/不通过：在「勉强合规」到「正常」那一片区域里它是平的，**没有梯度**，
所以照着它生成的板子会「合规但丑」。这两层补上梯度和约束顺序：

1. `board_layout_metrics path=板子` — 8 个连续读数：最近邻间距（中位/CV/拥挤比例）、
   朝向在轴比例、对齐比例、板边留白、5 mm 网格密度 CV 与空格比例、簇分离比。
   每个读数带 `how` 与所用阈值。**要判断「离不离群」必须给参考集**：
   `reference=[路径...]` 或 `reference_dir=目录 reference_limit=12`。
   没给就没有分位（它明说）；参考集是同族备份副本时判 `usable_for_judgement: false`。
   落在 p10 以下是**问题**不是缺陷。
2. `board_topology path=板子` — 主控（焊盘最多 ≥16）→ `mcu`；其余按网名命中数投票分到
   memory/clock/usb/debug/uart；只碰电源轨的算 `power`；15 mm 内就近并入，否则 `unclassified`；
   `flow` 从 power 做 BFS。**先按 flow 给每组划区域，再在组内落坐标**——跳过这一步就是
   「合规但丑」的典型来源。
3. 读数在边缘、并且你知道「为什么丑」时，用 `pattern_add kind='pitfall'` 记一条：
   `symptom` / `cause` / `fix`（各 ≥8 字）+ 可选 `metric`（必须是上面某个读数名）与
   `below_percentile`。绑不到读数的症状会被拒。

纪律：这三样**都不写板、都不自动布局**。度量是 `authoritative: false`，最终判词仍只由
`board_drc` 给。

## 布线纪律（走线「像参考板」怎么量）

`docs/122` 的结论先说：**形状不是眼睛在读的东西**。实测 F412 那块 53 mm 板在斜线占比、
绕行率（1.089 对 1.258）、网离散度（1.000 对 1.013）、拐角 45° 占比（96% 对 95%）上全部
不输生产板，唯一巨大差异是**线宽梯级 1 级 vs 13 级**。所以三个工具各管一层：

1. `board_routing_profile path=板子 [reference=[...]]` — 形状：段长中位/p90/最大、斜线占比、
   离 45° 网格占比、跨板长段数、每网拐角与过孔中位、扇出长度；`layers` 每层给方向偏好强度。
   给参考集时 `reference.rows` 逐项给 `delta` 与 `verdict`。**形状读数不能判丑**，别拿它当判词。
2. `board_house_style path=板子 reference=[参考板...]` — 规矩：`width.ladder`（本板线宽梯级）、
   `by_role`（电源/差分/信号各取什么宽）、`vias.families`、`corners`。参考板只贡献**比例**
   （`rules.scaled_from` 印出算式：本板中线宽 × 参考的同角色/其信号宽），**不贡献毫米**——
   生产板的 0.10 mm 信号线不是 53 mm 板子的宽度。结果：
   - `fix.fix`：可以改的，每行就是一次 `set_track_width`（走 `board_apply` 过 DRC 差集闸门）
   - `fix.blocked`：规则想要的宽度**放不下**，写清 `fits_mm` 与「先挪邻居」
   - `fix.capped` / `fix.pair_capped`：被余量砍到多少、差分对被拉平到较紧的一侧
   - `clearance_room`：逐网「最宽能到多少」（邻网也加宽用对称界 `d−clearance`）
3. `board_routing_violations path=板子 [limits]` — 重画清单：超长段（默认 0.25×对角线）、
   离网格段、拐角超限的网、无方向偏好的层，每条带 uuid/网/层/长度/两端坐标，按长度降序。

纪律：三者**都不写盘、都不布线**。想让板子真的有线宽梯级，是要在**扇出时留通道**——
`blocked` 里那几条（0.5 mm 节距出线、差分对 0.389 mm 间距）一旦画完，加宽就没空间了。

## 方案与布局库（`pattern_*`）

经验库存的是「能被测量反驳的说法」，这里存的是「能被人再来一遍的东西」：
- **recipe**（已验证方案）：参数 + 步骤 + 一条读数。例：USB-C 受电端 CC1/CC2 各 5.1 kΩ。
- **layout**（可复用布局）：相对**一个 anchor** 的毫米偏移。
- **pitfall**（差样本）：症状 / 原因 / 修法，可绑 `board_layout_metrics` 的一个读数。

规矩（会被拒，不是建议）：
1. `parameters` 每项必须 `{value, unit, source}`；裸数字直接拒——没来源的数字是猜，
   而猜被复用了比没有库更糟。
2. layout 必须**恰好一个** `anchor: true` 的元素；缺 `dx_mm` 不等于 0，会拒。
3. `pattern_add` 只写 candidate；`pattern_verify(confirm=true, reason=..., evidence=[{from, reading}])`
   才能变 verified。`from` 写路径或命令，`reading` 写它返回了什么。
4. 要用布局 → `pattern_layout_plan(id, x_mm, y_mm, rotation_deg, mirror, path=板子)`：
   只返回坐标 + 冲突（越界 / 同一位号已在板上 / 小于 `min_gap_mm`），**`writes_nothing`**。
   落位仍走 `board_propose → board_apply`；没有自动布局。
5. 用过之后 `pattern_use(id, outcome="worked"|"failed", note=...)`；`failed` 和 `worked`
   一样重要。`pattern_status` 会点名「verified 但从没被用过」的条目。

## 已退休与禁止

- `board_route`、`board_fanout`：**退休**。按名调用 → `TOOL_RETIRED`。
- 任何形式的自动布局 / 自动布线：不做，也不写新工具替代。
- 写盘路径只有 `board_propose → board_apply`（原理图同理 `schematic_propose → schematic_apply`）。
  **不要用文本编辑去改 `.kicad_pcb` 绕过它**。
- 用户的工程目录（例如 `LCEDA-Pro` 下的）**只读**；实验在副本上做。
- **禁止自己造铺铜填充**（手算带状矩形、一条条 `pcb_PrimitiveFill` 之类）。铺铜是两个对象：
  边框你写，**填充交给 EDA 官方**——KiCad 用 `board_fill_zones`（官方 `ZONE_FILLER`），
  立创用 `lceda_pour(action="create")` 建边框 + 编辑器 `Shift+B` 重建，立创侧 API 没有重建入口
  （98 个命名空间 + 键鼠注入均实测无效）。自行填充实测会报 302 条 Clearance 违规。
  新画的线在填充之后，要让铜皮合并进最终 Gerber 就**再按一次 `Shift+B`**，然后 `lceda_pour(action="check")` 复核。

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

- 工具全表（按域分组，200 个名字）：`references/01-tool-map.md`
- 分场景检查单（读板 / 改板 / 出图 / 排障）：`references/02-workflow-checklists.md`
- 怎么摆、怎么布、怎么验收：另一份技能 `pcb-layout-methodology`
