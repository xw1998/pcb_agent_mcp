# Changelog

**版本口径**：从 `0.0.1` 起，第三位数字随每次提交递增（`0.0.2`、`0.0.3`…），
只有真正对外、有不兼容变化的里程碑才动第二位。不为了"看起来成熟"把版本号往上跳。

信封契约（`status` / `basis` / `precision` / `reason_code` / `authoritative`）从这个版本起冻结：
加字段可以，改语义要动第二位。

早期用 `0.1.0`…`0.7.0` 递增标注过内部阶段，已按要求收敛；那些标签只留在 git 历史里
（见文末对照表），不再作为版本号使用。

## [0.0.14] - 2026-10-29

「布线器从 `_scratch/` 脚本收进 `src/`」：`board_route` 兑现 0.0.13 的欠账，兑现方式不是
把脚本搬过去，而是**先把「栅格不是证据」这条信条真的落进判决层**——本轮因此抓出五个
「在成功状态里给出错答案」的真缺陷（前四条写测试时自己发现，第五条是拿 `kicad-cli DRC`
当裁判、跑完整条 apply 链路才现形的）。

- **`route/` 三个模块 + `board_route`（第 127 个工具）**：
  - `rules.py`：clearance / 边距**三级来源**（caller > 板内 `(net_class …)` > `.kicad_pro` 的
    `net_classes` / `netclass_assignments` / `netclass_patterns` 通配，多源取最严），每个数字
    在 `assumptions.where_the_numbers_came_from` 里点名是谁给的；**一个来源都没有就
    `RULES_ABSENT` 拒绝开工**，不按经验取 0.2 mm。
  - `grid.py`：**双栅格**（走线 `margin = clr + track/2`、过孔 `margin_via = clr + via_dia/2`）
    ——合成一张图会在两头同时错：过孔能穿过本该挡它的缝，或走线绕开一块本来能走的空。
    A* 8 向禁切角、`TURN_COST=0.55`、`VIA_COST=9.0`、`HEUR_W=1.25`、`MAX_BUDGET=400000`。
    预算耗尽单独报 `search_budget`，**不混成 `no_path`**：我没走完 ≠ 没有路。
  - `router.py`：Prim MST 按长度降序、失败网反向 second sweep、**按物理重叠** rip-up
    （焊盘包围盒外扩 1.5 mm 与已发 ops 求交，取重叠最多的 3 个非平面网；旧写法按「短的最便宜」
    挑牺牲者，实测站不住——挡路的网可能在板子另一头），只重跑 loser + 被撕的网。
  - **事务化**：一个网的铜只在这个网成功之后才进图（`_Emitter` mark/commit/rollback/forget），
    失败即整网不留痕。
- **五个真缺陷**（`docs/27` §27.9）：
  1. **越界下标在 Python 里回卷**：扁平 list 栅格，问板外 `i=-50` 的点会回卷到板上一个普通空格，
     于是「板外」被回答成「可走」。修法 `index()` 越界返回 `None`。**改的是代码，不是断言。**
  2. **keepout 只封了边框一条带**：第一版照铺铜的写法标「距环边 < margin」，keepout 内部完全
     可走，搜索直接从框里穿过去。修法 `point_in_polygon` 命中即 `d = 0`，且这个判定必须放在
     两个 margin 决定任何事**之前**（否则 `[m_track, m_via)` 那条带里走线图与过孔图互相矛盾）。
  3. **精确判定里根本没有 keepout**：`seg_blockers` / `via_blockers` 只看铜 clearance，
     于是「栅格说能走、精确判定说干净」可以同时成立，而铜实实在在横穿了一个 keepout。
     修法 keepout 进精确判定（线段：端点在内 / 与环边相交 / 到环边 < width/2；过孔：圆心在内 /
     圆环相碰），并**尊重 `(keepout (tracks allowed))` 这类标志**——只禁过孔的 keepout
     不该把走线也封死，否则后面每个网都在绕一条板文件里不存在的规则。
  4. **焊盘逃逸链路没画也没量**：焊盘中心格被别人铜的光环盖住时，起点退到最近空格，但
     「从焊盘到那个空格」这段铜从没画过也没检查过 ⇒ 输出第一段线离焊盘 1~2 mm 什么都不挨着，
     而 `status` 是 `routed`。修法把它当一段普通铜走 `em.track()`，失败整网拒绝（`terminal_unreachable`）。
  5. **板边距只管过孔不管走线**：`via_blockers` 有 `too_close_to_edge`，`seg_blockers` 没有。
     这块板没有 `min_copper_edge_clearance`、也没有 `.kicad_pro`，但 KiCad 照样按板设置默认
     **0.5 mm** 查。于是 `LED1_A`/`LED2_A` 把铜贴在 y = −0.4（铜边距边 **0.275 mm**）被报成 `routed`，
     `kicad-cli pcb drc` 报 **6 条 `copper_edge_clearance`（全部 error）**。修法三处：规则缺来源时
     取 KiCad 默认 0.5 mm 并写进 `assumed`（不沿用「缺规则就拒」：板边距是**关于板**的规则，
     留空等于假绿）；`_mark_edge()` 沿 Edge.Cuts 对每个网封一条 `edge + track/2` 宽的带；
     `_edge_for_segment()` / `_edge_for_via()` 进精确判定。新增 `edge_clearance_mm` 参数供调用方覆盖。
- **实测（同一块 F401 核心板，50 网 / 194 pad / 4 层 / 162 870 cells，出货代码）**：
  baseline `27/50 · 461 ops · 102.9 s` → `sweep + rip 38/50 · 351 ops · 521.8 s`
  （`rips=64 / rip_passes=1 / sweeps=2 / rejected_ops=0`）。**净赚 11 个网，铜反而更少**；代价是
  撕图时要把 16 万格 × 4 层重建一遍并重放全部已发 ops。修板边距之前的 baseline 是 29/50，
  多出来的两个正是贴边的 `LED1_A`/`LED2_A`——**非法铜不算布通**，所以真实起点是 27。
- **端到端复核（自己的闸门绿不算绿）**：`board_route(apply=true)` → 四道闸门全绿、
  `new_blocking_total=0`，再让 `kicad-cli pcb drc`（10.0.7）当裁判：69 条违规全是这块板既有的
  silkscreen/text 告警，**shorting_items 0 / tracks_crossing 0 / clearance 0 / copper_edge_clearance 0**；
  `unconnected_items` 71，对应的就是那 12 个没布通的网。修板边距前后就是这两行：
  **6 条 error → 0 条，布通仍 38/50**——那 6 段贴边铜既没换来一个布通的网，又让整块板 DRC 变红。
  性能另一条：12 网 `229 s → 41.4 s`（`free` 63.2 s、`dict.get` 53.3 s、via gate 33.5 s、
  `_mark_cells` 23.1 s、`__init__` 21.2 s 逐条消掉）；板边距三处检查的代价是 sweep+rip 451.3 → 521.8 s。
- **16 条测试**（`tests/test_route.py`，全部合成板）：规则三个来源与公示、空规则拒绝、双栅格真的
  不同、板外真的不可走、**测试自己量间距不信路由器判决**、keepout 逐点量距离、焊盘落在 keepout 里
  点名拒绝、有网布不通整体不写 / `allow_partial` 才写 / `incremental` 不叠加第二份铜，以及一组
  **板边距的正臂 + 反假绿对照**（板边只剩 0.8 mm 缝时不许穿；同一几何把规则放宽到 0.1 mm 就能过）。
- 工具数 126 → **127**；文档 `docs/27-自动布线器.md`。

**仍然欠着**：差分对 / 等长 / 阻抗目标、回流路径、爬电距离、拥塞协商（negotiated congestion，
这才是现代布线器的核心，也解释了 50 网板上为什么还能剩失败）、Steiner 拓扑、热焊盘重填、
D 形 pad 圆角。`routed` 不等于 DRC 干净。

## [0.0.13] - 2026-10-29

「把不可观察的地方改掉」：一次真按流程画板之后，用户点名两条 —— **过程在 EDA 窗口里看不见、
没法打断**，以及**立创EDA 里查不到器件就只能瞎编**。同轮自审又挖出四条根因级的坑。全部修复，
并且每条都变成闸门或测试。

- **分步写（过程可见、可打断、可续跑）**：`plan.steps_apply` / `step_events` / `step_control`，
  板与原理图共用一套方言；每块重读磁盘、比对 `sha256_before`、只跑这一块的 op、原子写、
  写快照（`steps/<plan_id>/<seq>.*`）与一行 JSONL；块间查 `steps/STOP`（`STOPPED_BY_USER`，
  计划不消耗），`resume` 用 `shutil.move` 归档；末块照跑完整闸门，其中一道是**分步结果 vs
  一次性结果**的等价性。顺手修掉一个只有分步才现形的 bug：原理图 uid 依赖「本次调用里已造
  多少对象」，`sch_ops.run_all` 现在每个 op 前把 `ed._n` 归零。
- **器件库与在线检索**（`parts.py` + 6 个 `part_*` 工具）：`lceda-std.elib` 前 16 字节是
  `SQLite format 3`（461 MB 不是黑箱）：devices 16 828 / components 21 310 / attributes 384 775 /
  倒排索引；符号与封装几何在 `dataStr`，单位写在 `["CANVAS"]`（3 911 篇 mil、131 篇 mm、
  2 篇没写 ⇒ 没写的一律拒绝）。在线 `pro.lceda.cn /api/v5/devices/search` 匿名可查
  （count 1 613 360），但**几何是加密下发**，库外器件回 `geometry: "not_offline"`，
  **绝不编封装**。
- **DRC 分级**（`edit/drc_policy.py`）：`BLOCKING_SEVERITIES = {"error"}`，读不到 severity
  ⇒ fail-closed 阻断；真板 69 条 violation 全是 warning（`text_height` 32 / `silk_over_copper` 21 /
  `silk_overlap` 14 / `silk_edge_clearance` 2）⇒ blocking 0 / advisory 69。两处 apply 闸门改判
  `new_blocking_total`，`strict_drc` 一键回到「warning 也算墙」。老测试原先断言「边缘丝印被挡」，
  改成「默认落地并报 advisory / strict 下回滚」——原断言在真板上等于要求「这块板不可编辑」。
- **`board_routability`（新工具，`oracle.routability` 重写）**：栅格模型对 0.5 mm 间距的 QFP
  根本不成立（0.325 mm margin 让每个 pad 的光晕吞邻居，78/180 判死），改成**几何逃逸走廊 +
  精确距离**，栅格只留作 congestion 且**带网属**（板外不计入分母，这个 bug 曾让 congestion 从
  0.4153 虚高到 0.7556）。三态判词：`blocked_pads`（连 0.2~0.4 mm 短走廊都出不去）/
  `bends_needed`（直线不行、拐弯可行）/ `escapes_exist`；失败给 `why_blocked`（谁挡 + 差多少 mm）；
  已连同网 track/via 的 pad 永不进 blocked。性能靠 `_bucketize` + bbox 间隙下界，真板
  180 pad / 4 层 14 s → **3.3 s**。真板实测：`bends_needed` / 0 blocked / 1 bend（U4 SOT-23-6 第 5 脚）/
  `one_way` 11 / congestion F.Cu 0.4153 · In 0.191 · B.Cu 0.195。**不是路由。**
- **叠层总厚（根因级）**：`create_board` 造 4/6 层板时介电预算除以 `inner + 1` 却只写 `inner` 层，
  且铜+阻焊按 0.07 mm 低估 ⇒ 声明 1.6 mm 的 4 层板叠层实加 **1.18 mm**，而 KiCad 的 `stats`
  **只累加 stackup、忽略 `(general (thickness …))`** ⇒ 引擎报 1.18，所有闸门皆绿。修法：
  n 层铜需要 **n − 1** 层介电，`dielectric_total = thickness − 0.035·n_cu − 0.02`。并变成闸门：
  `engine_stats` 增 `thickness_stated_mm` / `thickness_engine_mm` / `thickness_agrees`（0.005 mm），
  不一致即创建失败；读侧 `board_summary` 增 `thickness_from_stackup_mm` / `thickness_agrees`，
  **不跑 KiCad 也能看见**。真机复验 2/4/6 层 × 0.8/1.2/1.6 mm 全部相符。
- **铺铜闭环**（`fidelity/fill.py` + `board_zones` / `board_fill_zones`）：没有 `filled_polygon`
  的 zone 只是轮廓，此后所有连通性答案答的都是轮廓。现在把填充**委派**给 KiCad 自带的
  `pcbnew.ZONE_FILLER`：默认在**临时副本**上铺并只报结果（谁都不动），`in_place=true` 时把 KiCad
  产出的 `filled_polygon` 节点**移植**进原文件并先写 checkpoint——不让 `SaveBoard` 把整块板重写成
  KiCad 10 排版、把 `(version 20221018)` 升成 `(version 20260206)`。`board_apply(fill_zones=true)`
  把闭环放进写路径，**填不成即失败**，且闸门写死 `drc_rechecked: false`（那次 DRC 发生在铺铜前）
  与「铺铜是快照」。keepout 由引擎兑现：加一个 10×10 keepout，铺铜面积实测量少 ~100 mm²。
- 工具数 123 → **126**；`--selftest` 与全量测试见 `docs/26-分步可见与铺铜闭环.md`。

**仍然欠着**：`lceda_pro_steps` 真机未验证（本机非交互会话起不了 `lceda-pro.exe`，
`APP_NOT_RUNNING`）；在线器件只有元数据没有几何；`board_routability` 不是布线器；铺铜快照
不会自动重铺；~~布线器本体仍在 `_scratch/` 脚本里、未收进 `src/`~~（**0.0.14 已兑现**：`route/` 三模块 + `board_route`）。

## [0.0.12] - 2026-10-28

「把别人写好的东西抄进来，只抄能变成可复算工具的那部分」：把 8 个指名技能里可机判的内容
并成 **4 个新 calc 模块 + 1 个补丁 + 8 个新工具**，另加一套**缺事实永不通过**的规则引擎。
不能复算的部分（报告写作方法论、LaTeX 绘图、付费壳）逐条写了「为什么不抄」。

- **八个技能的处置**（`docs/25-技能并入清单.md` §25.1）：改用 `boost-inductor-circuit-calc`（公式）、
  `kicad-free`（规则）、`circuit-analysis`（严重度 + 证据阶梯）、`rf-circuit-design`（板材/层叠/
  级联/带宽）；拒 `gmai-pcb-manufacturing`、`gmai-pcb-layout-review`（付费 X402，且 `main.py` 是
  固件安全审计样板，模板串域）、`circuit-diagram-generator`（CircuiTikZ/LaTeX，违反零依赖）、
  `circuit-analyze`（**目录是空的**，没装）。`circuit-analysis/references/truth-knowledge-report.md`
  是纯写作方法论 ⇒ 只吸收「证据等级」一条，变成每条规则的 `evidence_level`。
- **新增 `calc/magnetics.py`**：CCM 功率电感（boost / buck / buck_boost），给 duty、输入电流、
  纹波、电感量、峰值/谷值/RMS、CCM 最小输出电流、饱和校核。口径：`ripple=0.30`（技能说
  0.20~0.40）、`isat` 打折 20%；拓扑可行性（duty 越界）**先于**负载检查，避免报错报错在错的
  那条上。锚点：boost 5→12 V / 1 A / 500 kHz / η=0.9 / r=0.3 ⇒ duty **0.625**、ΔI **0.8 A**、
  L **7.8125 µH**、Ipk 3.0667 A；buck 12→5 V ⇒ duty 5/12、L **19.444 µH**。
- **新增 `calc/stackup.py`**：5 种板材（fr4 / ro4003c / ro4350b / rt5880 / megtron6，含 Dk、Df、
  可用频率、成本序）、4 层与 6 层叠（.5/1/1/.5 与 .5/1/.5/1/1/.5）、每层 50Ω 线宽、缝合孔 λ_g/20。
  介质分配明写是**等比建议**，回板厂压合图必须重解。锚点：4 层 1.0 mm FR4 的 L1 50Ω ≈ **0.6315 mm**
  （介质 0.3333 mm），回代 z0 = 50.000；2.4 GHz 在 FR4 上 λ_g = 60.2 mm ⇒ λ_g/20 = **3.01 mm**。
- **新增 `calc/chain.py`**：Friis 级联（NF 用「该级**之前**的增益连乘」，逐级 `excess_factor_share`
  列账）+ IIP3/OIP3（`oip3` 自动转输入参考）+ kTB 灵敏度。锚点：技能 §4.4 算例 → **0.8369 dB**
  （原文写 0.83）；级联 IIP3（0 dBm 经 20 dB → 10 dBm）→ **−10.4139 dBm**、OIP3 **19.5861 dBm**。
- **新增 `calc/rules.py`**：**22 条布局 + 14 条原理图 = 36 条**规则目录，每条带 `severity`
  （Critical/High/Medium/Low）、`fact`、`kind`、`how_to_measure`、`evidence_level`、`source`。
  限值**能算就算**（3W = 3×线宽、缝合孔 = λ_g/20、载流 = IPC、隔离 = IEC 60664-1），算不出就
  `undetermined` 并给 `next_check`。
- **`calc/matching.py` 追加 `bandwidth()`**：BW = f0/Q；VSWR 圈内带宽 × (S−1)/√S；三个数里给两个
  才答。
- **新增 8 个工具**（`TOOLS` 107 → **115**）：`magnetics_inductor`、`stackup_material`、
  `stackup_design`、`rf_chain`、`match_bandwidth`、`design_rules`、`layout_review`、
  `schematic_review`。后两个与 `design_rules` 共用同一份 `facts` schema——模型得先知道该测什么。
- **三条「不许错答案」**：① 规则**缺事实 ⇒ `undetermined`，永不 pass**，`facts` 全空直接
  `ARG_MISSING` 拒答（不然就是一份「全绿」假报告）；② 拼错的 fact 键进 `unknown_facts_ignored`
  并在回包里点名，不让笔误静默降级；③ `current.width_for_current` 必须显式 `standard`
  （`ipc2221` 与 `ipc2152` 差 3 倍以上，替调用方选就是错答案），因此规则里带电流的限值一定要求
  `facts["current_standard"]`，`creepage` 表 `verified:false` 则永远 fail-closed。
- **丢掉了技能里一张自相矛盾的表**：`rf-circuit-design` §3.5 写「2.4 GHz λ/20 = 1.5 mm」，
  与它自己给的 3.01 mm 差一倍（1.5 mm 实为 λ_g/40）⇒ **只实现公式、不搬表**，矛盾记进
  `docs/25` §25.4。
- **能力自述同步**：`calc_capabilities` 的 `layers` 新增 `magnetics / stackup / review` 三节、
  `available` 增 8 名、`evidence` 增 `magnetics / stackup / chain / bandwidth` 与
  「36 rules (22 layout, 14 schematic)」、`not_implemented_yet` 增 2 条（功率磁学只有设计点；
  review 不替你读设计、不解析网表）。
- **`--selftest` 78 → 87**（9 条新锚点断言，含「缺事实不 pass」「空 facts 拒答」两条不变量）；
  **测试 863 → 907**（新增 `tests/test_skills.py` 44 例）。文档新增 `docs/25-技能并入清单.md`，
  `docs/README.md` 与 `README.md` 索引同步。
- **顺手修掉一处报错顺序缺陷**：`lceda_pro_run` 原先先连编辑器，于是「session 空」会被告成
  `APP_NOT_RUNNING`；改成**先校验参数**（`ARG_REQUIRED`）再连接——参数错跟有没有开编辑器无关。

## [0.0.11] - 2026-10-28

「能看见你正开着的那张图」：给立创 Pro 做了一条**进程内桥**——不是文件格式、不是 websocket
网关，是应用自己开在 `\\.\pipe\JLCEDAPro<8 hex>` 上的命令空间。顺手推翻了前两轮的一条结论：
立创 Pro 在这台机器上**起得来**，差别只在父进程有没有控制台。

- **新增 `src/pcb_agent_mcp/lceda_live.py`**：帧层（一行一个 JSON、请求 ≤4 MiB / 回复 ≤16 MiB、
  预算 60 s、0.02 s 轮询按 `\n` 切帧、超时即关管道）、端点枚举 + ping 确认、`invoke`（把一段
  源码当 async 函数体送进编辑器 render 进程，`eda` 在作用域里）、`doc.api`、`open`、
  `session.list` / `session.close`。
- **新增 6 个工具**（`TOOLS` 101 → **107**）：`lceda_pro_status / _api / _open / _sessions /
  _close / _run`。共同口径：`verified_against_app: true`（这些都真跑过）、回带 `endpoint` 与
  `bridge_version`、`value` 是应用答复**原样**（不加字段、不四舍五入）。错误码分两类：
  我们的（`APP_NOT_RUNNING / APP_UNREACHABLE / APP_TIMEOUT / APP_ERROR / FRAME_TOO_LARGE /
  RESULT_TOO_LARGE / ARG_REQUIRED / ARG_INVALID`）与**厂商码原样透传**（19 个）。
  「应用没给码」不再冒用 `INTERNAL`（那是厂商的词），改成 `APP_ERROR` + `app_code: null`。
- **实测数字**（全部对 `_scratch/lceda/` 里的**副本**做，用户工程目录一次没进）：`ping` →
  `bridgeVersion 4.1.60.198f38ab`；`open` 32.5 MB `.eprj` headless **849 ms**；`session.list`；
  `session.close(destroy=true)` → `status: closed`（条目仍在列表里，只是状态变了）；
  `doc.api` → `eda` **378** 个类型 + `callConventions.classToProperty`，`PCB_Document`
  **21** 个方法（带厂商中文注释）；`invoke` → `await eda.dmt_Pcb.getAllPcbsInfo()` 回两块板
  的名字与 uuid、`eda.dmt_Project.getCurrentProjectInfo()` 回工程记录、`throw new Error('boom')`
  回 `EXECUTION_ERROR | boom`。
- **两个会静默写错的坑**：文档名 ≠ 运行时属性名（`PCB_Document` → `eda.pcb_Document`，映射表
  应用自己会给）；树类没有 `list()`（是 `getAllPcbsInfo()`），且 `eda.pcb_Document.*` 在没有
  PCB 文档是"当前文档"时会**回 null**——那不是调用写错。
- **推翻「立创起不来」**：`explorer.exe "<exe>"`（等价双击）启动 → 三个进程稳定
  （203/36/289 MB）、管道数秒内出现；bash 直起仍 0.5 s 退出。`docs/10` 的 §1.1.1 **保留原文并
  逐条标注错在哪**，新增 §1.1.3 记第三轮。同时查清 `bad option: --mcp` 的出处是 **exe 里 node
  自带的选项解析器**（不是立创启动器），所以厂商自带的 `--mcp stdio|http` 服务器起不来。
- **能力自述同步更正**（诚实性硬要求）：`tools.py` 的 `not_implemented_yet` 里
  「LCEDA Pro launch」与「EasyEDA Pro live API drive」两条按新事实重写；`desktop.py` 的立创
  notes 从"没有外部交互权限就完全驱动不了"改成"两扇门，其中命令空间那扇开着"；
  `capabilities.decisions.evidence` 新增 `live bridge` 一行。
- **新增 `docs/24-立创进程内桥.md`**：管道名推导（公式是真的、但这台机器上算不出来 ⇒ 枚举 +
  ping）、帧格式、9 个 op、实测数字表、两个坑、`invoke` 的代码跑在哪、工具表与错误码表，
  以及 §24.9 **没验证的 6 条**（任何写 / `open` 是否碰工程字节 / `destroy` 是否丢未保存改动 /
  厂商 `--mcp` 传输 / `stop`、`doc.external`、`doc.format` / 多实例）。
- **测试**：新增 `tests/test_lceda_live.py` 26 例（24 例用假 transport 跑失败路径——无管道、
  执行报错、管道不回话、非 JSON、超 4 MiB / 16 MiB；2 例真机用例挂在
  `skipUnless(available_endpoints())` 后面，没开应用时跳过而不是失败）。全量 837 → **863**。
  `--selftest` 76 → **78**（新增"不存在的管道回 `APP_NOT_RUNNING` 而不是挂住"与
  "厂商码保留 / 未知码变 `APP_ERROR` 且不冒用 `INTERNAL`"，两条都不依赖真机）。

## [0.0.10] - 2026-10-28

「把两张读不到原文的表做成可自证的答案」：IPC-2152 载流从"空表 + fail-closed"
换成公开的闭式拟合，IEC 60664-1 爬电/电气间隙从"永远拒答"换成**行为反演出来的阶梯表**。
两条路都不靠"我照 PDF 抄了一遍"，靠"我能重新生成并证明"（见 `docs/23`）。

- **IEC 60664-1:2020-05 全量落地**（新增 `calc/iec60664.py`，648 行 + `data/iec60664_tables.json`）：
  - 方法：编译 KiCad 10.0 的 `pcb_calculator/calculator_panels/iec60664.cpp`（只补 `<cstdlib>`），
    先反演阶梯、再对拍。**三套独立探针共 188 528 点，0 失配**：
    `grid.csv` 3 760（公共 API）、`sweep.csv` 93 120（密集几何扫描）、
    `raw.csv` 91 648（`#define private public` 直读原始阶梯函数）。
  - `creepage_mm` / `clearance_mm` / `impulse_withstand_v` / `min_groove_width_mm` / `spacing` /
    `info`。32 条爬电阶梯 / 808 步、Table F.2 逐 PD 26/21/20/17 步、Table F.8 30 步、海拔 11 档。
  - **复刻了三种"原文没写但实现有"的行为**：`Compute()` 的 `creepage >= clearance` clamp
    （只在 `spacing()`，不在 `creepage_mm()`，并在 `extra` 里说明）；clearance 两分支
    **任一越界即双双置 -1**（端口拒答，绝不留下还活着的那个分支去低估间距）；
    Table F.5 的 PCB 材料列在 `V>1000`、`PD>=3`、`PD2+IIIb` 三处被静默关闭（端口把"你的开关没生效"写进 `extra`）。
  - **断点靠恢复不靠继承**：旧做法从上一版 JSON 继承断点，导致 Table F.8 的
    0.04/0.06/0.1/0.12/0.15/0.2/0.25 kV 与 Table F.2 的 40/50 kV 整段消失
    （返回下一档的值，偏大且错）。新探针 `gen6.cpp` 几何走查 + 二分到 1e-12 + snap 到 1e-6 + 落边复核。
  - `calc/creepage.py` 分流：`safety_standard` 命中 IEC 60664-1（任何写法）走新引擎，
    其它安规族**继续 fail-closed**；新增 `spacing_required` 工具（两个距离一起算 +
    `creepage >= clearance` + 槽宽 + `governing`）。
- **IPC-2152 载流从空表换成闭式拟合**（`calc/current.py` + `data/ipc2152_tables.json`）：
  Brooks & Adam 对 Table 3-1 的拟合，系数逐字抄自 KiCad 10.0 `track_width_calculations.cpp`
  （外层 `K=215.3, a=2.0, b=-1.15, c=-1.0`，内层按铜厚四档）。金标
  1 A / 10 °C / 1 oz / 外层 = **0.2773 mm**、内层 **0.2484 mm**、`current_capacity(0.254,10,1,ext)` = **0.951 A**。
  系数放两处（代码常量兜底 + JSON 文档副本），**漂移即 `INTERNAL_CHECK_FAILED`**；
  `via_current_allowed(standard="ipc2152")` 回 `STANDARD_SCOPE`（过孔载流不在这个拟合里）。
- `safety_table_check` 新增 `which="iec60664"`（8 条检查：版本号 / verified 标志 / 两个独立来源 /
  32 条阶梯齐全 / Table F.8 与污染等级无关 / 阶梯递增单调且 `max_v` 对齐 / 线性段过原点 /
  物理探针 / `not_covered` 非空），并把 ipc2152 的校验从"锚点复现"扩成
  "系数形状 + 锚点 + 物理探针"。**这两条校验都带变异测试**（改坏一处必须判死）。
- 测试：新增 `tests/test_iec60664.py`（38 例，含变异层）、`tests/test_calc.py` 新增 11 例；
  `--selftest` 60 → **68** 条，全绿（新增 4 条走协议级 `handle()`：2.5 mm 金标、
  spacing 取大、PD4 拒答、校验器 usable）。
- 文档：新增 `docs/23-两张读不到原文的表.md`。

### 同一版里继续做完的四件欠账

- **板级 2-D 电热场**（`calc/electrothermal.py` 264 → 780 行，`board_electrothermal`）：把
  `k·t·∇²T − 2h(T−T_a) + q(T) + C·dT/dt = 0` 在均匀矩形网格上做 SOR（ω=1.85）。关键是
  **把 `q` 的温升斜率放到矩阵对角线上**（`den = hc + capdt − q_slope`），于是稳态严格线性、
  瞬态是后向 Euler；**失控在求解之前判**（任一格 `hc + capdt − q_ref·α <= 0` ⇒ `THERMAL_RUNAWAY`），
  均匀板退化成 `P·α < 2hA`。金标：50×50×1.6、k=0.3、h=10、2 W、cell 2 mm ⇒ `t_max=73.393487`
  （闭式 73.39345），5 W ⇒ 192.982，13 W ⇒ 拒答，α=0 时 2 W ⇒ 65.0。
- **振动应力独立成层**（`vibration_plate_stress` + `plate_modes(stress_amplitude_mm=…)`）：
  之前"最大应力点报在板角"只对一半——**一个点被用来回答两个问题**。角上是 `w_xy` 峰、
  von Mises 最大（`vm=√3|τ|`），跨中是最大主应力最大（6.64 MPa，角上那份低 37%）。
  现在三个极值分开报（`von_mises_*` / `sigma_max_*` / `tau_xy_max_*`）+ `peak_locations_note`；
  误差棒是 degree 阶梯实测（K=5/7/9：频率 0.04%、曲率 3.6% ⇒ `degree_sensitivity_pct`），
  点支承阶梯塌成一级时给 `None`（"没有误差棒"比"很小"诚实）。
- **PDN 平面模态**（`pdn_plane_modes` + `pdn_impedance(plane=…)` 诊断）：单模峰值闭式
  `|Z_peak| = w·μ0·t·ψ(r1)ψ(r2)·Q/k²`，`Q=1/tanδ`；50×50/εr4.4/1.6 mm/0.02 的 (1,0) 在
  1.4292 GHz 峰 **91.4691 Ω**。**只给峰值不给曲线**：真跑模态求和时 1.45 GHz 处截断 10 阶是
  5.6 Ω、40 阶是 9.4 Ω，随阶数漂移不收敛，那数字就是我选了几阶的函数。函数里带**电容锚点自检**
  （`f=首模/100` 求和必须回到 `1/(ωC_plane)` 的 1% 内），这条校验抓出了自己 20 倍的归一化错。
- **跨版本引擎对拍**（`VERIFIED_ENGINES = ("ngspice-47", "ngspice-41")`）：装了第二个构建
  （conda-forge `ngspice-exe 41`，注意 conda 的 `ngspice` 只是 14 KB 元包），同一批 deck 两边
  都跑：`.op`/`.ac` **逐位 0.0**（189 个标量），`.tran` 最多差 **1.24e-4 V**（1 V 阶跃，两构建
  步长控制不同 ⇒ 网格也不同，这个数不是精度排名）。版本门控本身**不需要真引擎**就能测：
  假引擎现在认 `$FAKE_VERSION`，让替身自称 `ngspice-41/47`（true）与 `ngspice-45`（false）。
- **立创 Pro 第二轮实测（仍然起不来，但知道了死在哪一层）**：rc 0、0.5 s 内退出，`lceda-pro.exe`
  自己的日志与 Chromium `debug.log` **一行都没写** ⇒ 它死在 Electron 之前；`--enable-logging=stderr`
  被自有引导壳以 rc 9 拒掉；同一 shell 下 `kicad.exe` 正常起来（排除"这里起不了 GUI"）。
  顺带读到安装目录里厂商自带的 **Pro API 0.3.38**（`resources/app/assets/pro-api/`，378 个类型、
  `PCB_Document` 21 方法、`SCH_Document` 9、`PNL_Document` 1，附 `cli.js` 帮助器）——
  但那是**进程内**扩展 API，不是传输，`verified_against_app` 仍是 false。
- 测试与自检：新增 `tests/test_electrothermal_plate.py`（31）、`tests/test_vibration_stress.py`（21）、
  `tests/test_pdn_plane.py`（27），`--selftest` 68 → **76**（新增板级应力二问、平面模态二问）。

## [0.0.9] - 2026-10-28

「加类似 ADS 的功能」：把 ADS 里最常点的几个**合成**按钮做成工具——微带耦合线、
耦合谐振器带通、矩形腔 TE101。**是合成器，不是仿真器**：给几何，不给 EM 结果。

- 新增 `calc/filters.py`：
  - `prototype_gvalues(order, response, ripple_db)`——Chebyshev / Butterworth 原型 g 值，
    `ripple_db=None` 按 response 取自然值，Butterworth 传非零 ripple 直接 `ARG_CONFLICT`。
  - `lumped_bandpass(order, f0, fbw, ...)`——LP→BP 变换给每段 L/C，并用**两条独立路径**
    算 S21（真实 L/C 的 ABCD 级联 vs 闭式 `1/(1+eps^2 Tn^2)`），逐点差 > **0.02 dB**
    返回 `INTERNAL_CHECK_FAILED`，**拒答**而不是端出未对账的数字。
  - `coupled_microstrip(z0e, z0o, h, er, ...)`——截面用 `field2d` 解拉普拉斯（奇模电壁、
    偶模磁壁），几何用**good Broyden + 回溯线搜索**在 `log(w), log(s)` 上反解；
    每次迭代都回带（不收敛藏不住）。k 误差 >3% 时带 `k_resolution_note`：
    k 是两个相近数之差，0.5% 的阻抗误差可以是 15% 的 k。
  - `coupled_resonator_bpf(order, f0, fbw, ...)`——Qe=g1/FBW、k_ij=FBW/sqrt(g_i g_j)、
    谐振器长 `lambda_g/2 - 2*Δl`；每段回带 target/achieved k、`meets_target`、`k_note`。
  - `waveguide_cavity(f0, a, b, ...)`——TE101 长度、Rs、**Qc 两种推导**（闭式括号式 vs
    六面 `|H_tan|^2` 数值面积分，差 >2% 拒答）、R/Q；低于 TE10 截止 `OUT_OF_VALIDITY`，
    非 TE101 `NOT_IMPLEMENTED`。
- 新增 5 个工具：`filter_prototype` / `filter_lumped_bandpass` / `filter_coupled_microstrip` /
  `filter_coupled_resonator` / `filter_waveguide_cavity`；`calc_capabilities` 的 `available`
  与"不做什么"清单同步（无全波、无色散、无交叉耦合、无有限 Q、无 iris 几何、无版图、无良率）。
- 实测数字：n=3 0.5 dB g 值 1.596331/1.096681/1.596331/1.0；2.4 GHz/FBW 0.1 的集总带通
  f0 0.0 dB、带边 0.5/0.5001 dB、两路差 0.000521 dB；耦合微带 70/40 Ω → w=0.2855 s=0.0788 mm
  最差 0.006%（`fine_div=6`，3.5 s）；WR-90 @10 GHz → d=19.8536 mm、Qc 7821.5（两路 0.0%）、
  R/Q 160.957 Ω。
- 修掉一个**只在异常分支才炸**的缺陷：`k_resolution_note` 里引用了不存在的变量
  （`z0e_target`），任何 k 误差 >3% 的调用都会 `NameError`；原来的用例都收敛得很好，
  这个分支从没被执行过。现在有一条测试专门把它逼出来。
- `coupled_microstrip` 的网格默认值收到 `fine_div=6`（`cell = h/6`）：实测 `h/6 → h/12`
  只移动 0.9%/0.7%，而 quasi-static 模型自身误差更大，所以**不用 3 倍时间去换 1%**；
  这个漂移写进 `precision` 而不是声称网格无关。
- 测试：新增 `tests/test_filters.py` 31 例（期望值全部来自教科书表 / 手算闭式 / 必须成立的自洽关系，
  没有一条是"跑一遍代码记下来"）；`--selftest` 57 → 60。
- 文档：新增 `docs/22-微带与腔体滤波器.md`；`docs/README.md` 的 `21` 行改写成真引擎实测口径并加 `22` 行。

## [0.0.8] - 2026-10-28

「本机没有 ngspice 二进制」这条欠账清掉：**自己下载装上了**，并且用真引擎把委派跑了一遍。
装的是外部二进制（清华 MSYS2 镜像的 `mingw-w64-x86_64-ngspice-47` 解包到
`%LOCALAPPDATA%\ngspice-47\`），**不入仓库、不成为依赖**——本仓库仍然只用 stdlib。

- 查找链补上 glob 兜底：`%LOCALAPPDATA%` / `%ProgramFiles%` / `%ProgramFiles(x86)%` 下的
  `ngspice*`（`setx` 只对新进程生效，刚装完就调工具的那个进程看不见自己设的变量）。
- `_run_ngspice()` 在 `SPICE_LIB_DIR` 未设且 `<exe>/../lib/ngspice` 存在时注入它——否则
  XSPICE code model 加载失败，`T` 卡与所有 XSPICE 器件全废（能跑但读不懂半个 deck）。
- `_engine_version()` 修 banner 解析：ngspice 版本行的第一个特征是星号行，原来取回来是 `******`。
- `verified_against_engine` 改为**按版本动态判**（`VERIFIED_ENGINES = ("ngspice-47",)`）：
  装了引擎不等于验证过引擎，别的版本回落 false 并在 `engine_verification` 里说明。
- **未解析 deck 直通**：`.subckt/X`、`B`、`T` 这类本模块按名字拒绝的卡，在 `engine="ngspice"`
  下不再致命——deck 原样交给引擎，节点表从 rawfile 读（`x1.m` 这样的子电路内部节点自然出现），
  分析类型/步长退化为文本扫描，信封带 `netlist_parsed:false` + `netlist_parse_note`。
  同一 deck 走 `builtin` 仍然 `NETLIST_INVALID`，能力差如实呈现。
- 修掉直通才会暴露的缺陷：deck 过滤用 `startswith(".end")` 丢行，把 `.ends` 也丢了，
  于是 `.subckt` 块少结束行、ngspice `ENGINE_FAILED`。
- `simulate()` 默认引擎从 `auto` 改为 `builtin`：默认值不能让同一网表在两台机器给出不同答案，
  而调用方从数字上看不出来。`engine="auto"` 才是"有引擎就用"。
- 真机实测（`cross_check=True`）：分压 5e-09 V、二极管 3.47e-04 V、BJT 1.78e-02 V（模型差异）、
  RC 阶跃与 RLC AC **逐位相同**。
- 测试：`tests/test_spice_engine.py` 增 `TestRealEngine`（`skipUnless(spice.find_ngspice())`，
  7 例：分压对账、RC/AC 逐位、版本门控、`.subckt` 与 `B` 直通、`netlist_parsed` 标注）；
  `--selftest` 54 → 57（新增默认引擎断言、`engine="auto"` 选择、双引擎对账一致、B 源直通）。
- 文档：`docs/21-SPICE引擎委派.md` §21.6 由"未验证声明"改写为真引擎实测表 + 版本门控口径，
  新增 §21.6a 讲清"装了引擎真正多出什么"。

## [0.0.7] - 2026-10-28

「SPICE 为什么只做子集」的回答：**不是懒，是零 runtime 依赖约束下唯一诚实的形态**。完整 SPICE
没有"抄进 stdlib 就能用"的版本（ngspice 约 50 万行 C，ahkab/PySpice 要 numpy/scipy），所以这一版
加的是**可选委派**——内置子集保证有答案，机器上装了 ngspice 就把 deck 交给它。不 vendor 二进制，
不新增任何依赖。

- 新增外部引擎委派（`calc/spice.py`）：`simulate(..., engine="auto"|"builtin"|"ngspice",
  cross_check=False)`，原求解器改名为 `_simulate_builtin`。
  - `find_ngspice()`：`NGSPICE_CMD`（测试注入）→ `PCB_AGENT_NGSPICE`（整路径优先，带空格才不拆坏）
    → PATH 上的 `ngspice/ngspice.exe/ngspice_con/ngspice_con.exe`。
  - `_deck_for_ngspice()`：用户网表**原样**通过，只把 `.op/.tran/.ac/.dc/.four/.noise` 过滤到
    调用方要的那一条并补 `.end`——SPICE 自己"跑第一张卡"的约定与本工具"调用方说的，否则最后一张"
    不同，不过滤就会答非所问且看不出来；网表里没有对应卡时 `ARG_REQUIRED`，不猜步长/频点。
  - `_parse_raw()`：ASCII `Values:` 与 `Binary:` 两种布局。ASCII **每条记录第一个数是点序号**
    必须丢弃（不丢则所有节点电压整体错一位，且仍然"看起来合理"）；复数图里**每个向量都是 (re, im)
    对，频率轴也一样**（虚部恒 0），把频率当实数读会在后面炸掉。
  - `_external_envelope()`：`engine_used` / `engine{path,argv,version,plotname}` /
    `elements:null` + `elements_note` / `not_computed` / **`verified_against_engine:false`**。
    逐元件报告在外部路径上**不出**：拿节点电压回推元件电流再当成引擎的答案，是把自己的算术冒充别人。
  - `_cross_check()`：同 deck 跑两个引擎比节点电压，只比 op 全部节点 / tran 首点 / ac 首频点
    （后两者比的是各自积分器与频率栅格，不是模型）；节点名对不上时 `compared_nodes=0` 且写明
    "这不是通过"。
- 失败路径逐条有码：`ENGINE_NOT_FOUND`（`engine="ngspice"` 找不到，**绝不回退到子集**）、
  `ENGINE_TIMEOUT`、`ENGINE_FAILED`（附 `exit_code` + `log_tail`）、`ENGINE_OUTPUT_INVALID`
  （rc=0 但没 raw / raw 解析不了）、`ARG_CONFLICT`（`.temp` 卡与 `temp_c` 同时给）、`ARG_REQUIRED`。
- `engine="auto"` 回退到内置时同样标注 `engine_used="builtin"` + `engine_note`：不标出来就等于
  悄悄降级。
- `sim_spice` 工具新增 `engine`、`cross_check` 两个参数；能力自述里「Simulation is a SUBSET」
  改写为「内置子集 + 可委派（不打包、不验证）」，`layers.simulation` / `verified_against.simulation`
  同步。
- 新增 28 个用例（`tests/test_spice_engine.py` + `tests/_fake_ngspice.py` 假引擎），全量 **670 passed**；
  `--selftest` 加 3 条（`engine=builtin` 被标注且 6.6667 V / 无二进制时 `engine="ngspice"`
  回 `ENGINE_NOT_FOUND` 而不回退 / `auto` 仍出答案且 `cross_check` 不请自来）→ **54/54**。
- 新增 `docs/21-SPICE引擎委派.md`。

## [0.0.6] - 2026-10-28

平面谐振与电热耦合：两个"板子自己把自己搞坏"的问题。前者只给模频与间距比例（Q 不做、判据不外给），
后者闭合"电流→温度→电阻→电流"这个环，并把热失控做**精确判据**而不是"迭代不收敛才报错"。

- 新增 `calc/cavity.py`：平行板腔体 TM_mn0 模 `f_mn = c/(2√εr)·√((m/a)²+(n/b)²)`，每模带 λ_g 与
  λ_0；简并对分行列出；`f_operate_hz` 只加"最近模"读数，不产生判决。
  `via_fence` 给 `pitch/λ_g`、每间距电长度、`vias per λ_g`，**判据必须由调用方给**
  （`max_pitch_ratio`）；不给判据回 `indeterminate` 并在理由里点名 λ_g/20 与 λ_g/10 相差 2 倍；
  只给判据不给间距时回 `max_pitch_mm` 而不反推一个间距。
- 新增 `calc/electrothermal.py`：`R(T) = ρ₂₀(1+α(T−20))L/(w t)`、`P = I²R`、`T = T_amb + P·Rth`
  的定点迭代 `ΔT(n+1) = A + G·ΔT(n)`，`G = dP/dT·Rth` **精确判定**：`G ≥ 1` 回
  `THERMAL_RUNAWAY`（附 G 与全部输入），否则回到 `A/(1−G)`；返回 `iterations`、
  `stability_margin = 1−G`、`r_hot_ohm`、`resistance_gain`。`rth_c_per_w` 必填——
  这一层不提供任何默认 Rth。走线版从几何算 R₂₀，替身版接受任何已知 R₂₀（过孔桶壁/分段/分流器）。
- 新增 4 个工具（88 → **92**）：`cavity_resonance` / `cavity_via_fence` /
  `electrothermal_trace` / `electrothermal_conductor`。
- 新增 31 个用例（`tests/test_cavity_electrothermal.py`），全量 **642 passed**；`--selftest` 加 5 条
  指纹（(1,0) 模 722.864 MHz 且 λ_g=200 mm / 15 mm 间距对 λ_g/20 判 fail / 无判据判 indeterminate /
  电热定点 25.5108 ℃ / G=4.716 判 THERMAL_RUNAWAY），**51/51**。
- 文档：`docs/20-平面谐振与电热耦合.md`（二十一篇），索引与 README 工具表同步。
- 诚实清单同步修正：能力自述里原有一句"没有电热耦合"，现在有了单集中 Rth 的环，那句话改写成
  "只闭合**一个** Rth，2-D/3-D 耦合、瞬态耦合、交流损耗、器件自热、`board_ir_drop` 自发温度场仍然没有"，
  并有测试守着这句旧话不能复活。

### 两处判据设计（这一轮的主要工作）

- **热失控用参数判，不用数值症状判。** 定点迭代的收敛条件是 `G = I²R₂₀Rthα < 1`，这是从
  `d(ΔT_{n+1})/d(ΔT_n)` 直接算出来的。写成"迭代 100 次没收敛就报错"会把 `G = 0.98` 这种
  真实存在的收敛（只是慢）误判成失控，也会把 `G > 1` 的假收敛当成答案。
- **过孔间距不给默认判据。** 同一块板、同一个 15 mm 间距，`λ_g/20` 判 fail、`λ_g/10` 判 pass——
  测试就是用这个"同一几何两种结论"的用例守住了"判据必须来自调用方"。

## [0.0.5] - 2026-10-28

机械振动层：把"这块板在振动台上会不会先坏"变成可算的数。**不抄模态表**——一套 Rayleigh–Ritz
特征值解覆盖五种安装方式，精度对着解析解与 Leissa 量过。

- 新增 `calc/vibration.py`（纯 stdlib）：矩形薄板自由振动。Kirchhoff 应变能
  `U = D/2 ∫∫[w_xx²+w_yy²+2ν w_xx w_yy+2(1−ν) w_xy²]`，`D = E h³/(12(1−ν²))`；单轴单项式基 +
  四边边界约束零空间 + 质量度量白化（`WᵀM₀W = I`）+ Jacobi 对称三对角化，二维装配自动降阶
  （`MAX_DOF = 44`，超出报 `auto_reduced`）。
- 五种安装：`edges`/`ssss`（四边简支）、`clamped`/`cccc`（四边固支）、`free`、`cantilever`
  （x=(cc,free)、y=(free,free)）、`corners`（free×free + 4 个角点 `w=0` 点约束）、`custom`。
  **四角点支撑没有近似成四边简支**——那是给错答案。
- 随机振动 `miles_response`：平坦谱闭式 `g_rms = sqrt(π/2·f_n·Q·W)` 与实测谱三段积分
  （`|H| = 1/√((1−r²)²+(2ζr)²)`，段宽 `δ = max(12ζ, 0.02)`）互证，同一点差 0.045%。
- 疲劳 `steinberg_fatigue`：三带 3σ/2σ/1σ × 0.3333/0.2713/0.3954，`N_i = N0·(z_allow/Δ_i)^b`
  （b=6.4、N0=2e7），Miner 累积 `D = f_n·Σ(frac_i/N_i)`。**`z_allow_mm` 必填**，不提供任何
  "典型允许挠度"——那是器件类别的数，不是用户这颗器件的数据。
- 冲击 `shock_sdof`：半正弦/阶跃，Newmark（β=1/4、γ=1/2，无条件稳定），
  `dt ≤ min(T_n/24, 脉冲/60)`，`nsteps > 200000` 拒答。
- 隔振 `transmissibility`：`|T| = √((1+(2ζr)²)/((1−r²)²+(2ζr)²))`，隔振起点 `f_n√2`，
  r=1 处 `|T| = Q√(1+1/Q²)`。**高频按 1/r 掉**（分子含 `(2ζr)²`），不是 1/r²。
- 新增 `oracle.board_vibration`（派生量 `authoritative:false`）：板框 bbox 当板 + 叠层厚度算 f₁、
  周期、按 |归一化挠度| 排的热点（`near_antinode = |d| >= 0.5`），非矩形轮廓回
  `outline_fill_fraction_of_bbox` 并说明 f₁ 偏低。
- 新增 6 个工具（82 → **88**）：`vibration_plate_modes` / `vibration_random_response` /
  `vibration_fatigue` / `vibration_shock` / `vibration_isolator` / `board_vibration`。
- 新增 52 个用例（`tests/test_vibration.py`，18.9 s），全量 **611 passed**；`--selftest` 加 5 条
  指纹（SSSS 330.385 / CCCC 602.382 且反节点在中线 / 阶跃 DLF 1.72925 / Miles 两算法 1% 内 /
  缺 `z_allow_mm` 回 `ARG_REQUIRED`），**46/46**。
- 文档：`docs/19-机械振动.md`（二十篇），索引与 README 工具表同步。

### 三个数值坑（都会给出"安静的错误答案"）

- **刚度系数是 `D`，不是 `0.5·D`。** `U` 是二次型，二次型求导后 1/2 与应变能里的 1/2 相消；
  写成 `0.5*D` 会让**所有**频率偏低恰好 √2 倍（SSSS 出 233.618 而解析 330.385，比值 0.7071）。
- **二维装配混合项要转置 y 轴**：`ICxw[i][k] * I1yw[j][l]`。写成 `ICyw[j][l]` 在 SSSS/CCCC 上
  看不出来（对称），在 free 边界会吐出 −92000 量级的负特征值。
- **Jacobi 的特征向量矩阵必须转置**：`evecs[mi][r]` 才是模态 mi。返回 `v[r][i]` 不转置会让
  **特征值精确、振型全错**（SSSS 反节点跑到 (57.5, 67.5)、中心挠度 0.845 而不是 1.0）。

另外两处口径钉死：`_moments` 的奇异点在 `s=0/1/2/3` 处分子恰为 0，条件必须写 `s>=2` / `s>=4`
（写 `s>=1` 直接 `ZeroDivisionError`）；显式 `e_gpa` 参数要 ×1e9 与 preset 的 Pa 统一，
不统一会让频率小 31623 倍。

### 精度实测（100×100×1.0 mm FR-4，E=24 GPa，ν=0.15，ρ=1850）

| 工况 | 计算 | 参考 | 差 |
|---|---|---|---|
| SSSS 方板 f₁₁ | 330.385 Hz | `π²√(D/ρh)/a²/2π` | 精确 |
| SSSS 2:1（200×100）f₁₁ | 206.491 Hz | 解析 | 精确 |
| SSSS (1,2)/(2,1) | 825.98 Hz | 简并对 | 精确 |
| CCCC 方板 f₁₁ | 602.382 Hz | `35.99/(2π)·√(D/ρh)/a²` | 精确 |
| FFFF 方板 ν=0.3 λ² | 13.4687 | Leissa 13.489 | 0.15% |
| 悬臂（35 DOF）前四阶 | 58.69 / 152.86 / 366.04 / 465.15 Hz | 收敛性自检 | — |

振型：SSSS 中心 1.0000、y=5% 处 0.1564（`sin(π·0.05)`）、(5%,5%) 处 0.02447。

## [0.0.4] - 2026-10-28

DFM 层：把"这块板能不能交给这家板厂做"变成可判的事实。**没有内置表**——限值只来自用户给的
工艺能力文档，服务器负责规则目录、文档体检与板上实测。

- 新增 `calc/dfm.py`：11 条规则键（`min_track_width_mm` / `min_clearance_mm` / `min_via_drill_mm` /
  `min_via_annular_ring_mm` / `min_pth_annular_ring_mm` / `min_hole_to_hole_mm` /
  `min_copper_to_edge_mm` / `min_slot_width_mm` / `max_board_width_mm` / `max_board_height_mm` /
  `max_copper_layers`），每条带单位与方向；`NOT_CHECKED_BY_DESIGN` 逐条点名 8 类静态文件里
  没有可比事实的项目。
- 新增 `oracle.dfm_check` 与 `oracle._dfm_facts`：11 个板侧事实（窄线宽 / 铜间距 / 过孔钻孔与
  环宽 / PTH 环宽 / 孔到孔 / 铜到板边 / 槽宽 / 板宽 / 板高 / 铜层数），每个都带 `where`；
  孔到孔与铜到板边用有界桶网格搜索（默认 5 mm），窗外只回 `lower_bound`。
- 新增 2 个工具（80 → **82**）：`dfm_capability`（只体检文档）、`dfm_check`（板上实测 + 五态判定）。
- 判定五态 `pass` / `fail` / `not_checked` / `not_applicable`，文档里没声明的规则进
  `missing_known_rules` 而不是算通过；未知键 / 缺 `source` / 空文档三处整份拒收
  （`CAPABILITY_KEY_UNKNOWN` / `SOURCE_REQUIRED` / `CAPABILITY_EMPTY`）。
- 新增 28 个用例（`tests/test_dfm.py`，手算金标：窄线 0.2 / 过孔环 0.15 / PTH 环 0.3 /
  孔到孔 0.7 / 铜到边 0.875 / 槽宽 1.0 / 板 50×30），全量 **559 passed**；`--selftest` 加 3 条
  指纹（错键被拒、同一块板对 0.15 mm 限值 pass、对 0.25 mm 限值 fail 且只报 `min_track_width_mm`），
  **41/41**。
- 文档：`docs/18-DFM-工艺能力校验.md`（十九篇），索引与 README 工具表同步。

### 一个真缺陷：挖空被当成板框

- **`adapters/kicad_pcb.py` 把 `Edge.Cuts` 的第二个轮廓也算进 `board.outline`**，于是显式画出来的
  挖空（1 mm 铣槽）与板边分不开、`board.cutouts` 永远为空——DFM 的槽宽这一条根本量不出来
  （测试直接 `KeyError: 'measured'`）。改为按面积挑外轮廓、其余进 `cutouts`。用 18 块 KiCad
  官方 demo 板核过影响面：只有 `vme-wren` 多轮廓，但它走 `contours` 分支（本来就按面积挑），
  所以只影响显式 `gr_rect` / `gr_circle` / `gr_poly` 画挖空的文件。

## [0.0.3] - 2026-10-28

仿真层：把"算得出来的答案"从单点公式扩到电路 / 电流 / 热三个域。**仍是子集**，
每个未实现项都按名字拒绝并写进 `not_computed`。

- 新增 3 个计算模块：`calc/spice.py`（MNA + 牛顿 + pnjlim + gmin 向下步进，`.op`/`.tran`/`.ac`，
  器件卡 R C L V I E G D Q M）、`calc/power.py`（铜阻 / 过孔 / 走线自感 / 电阻网络 / 分段 IR drop /
  电容组阻抗 / 自谐振 / PDN / 去耦三态判定）、`calc/thermal.py`（1-D 热阻串 / 过孔阵列 / 2-D 板
  SOR / 热瞬态 / 散热片预算 / 对流系数）。
- 新增 `oracle.board_ir_drop`（按板上真实铜算单网压降：节点带层号，两层只在 via 或镀通孔焊盘相连）。
- 新增 14 个工具（66 → **80**）：`sim_spice`、`trace_resistance`、`via_resistance`、
  `trace_inductance`、`ir_drop_chain`、`pdn_impedance`、`decoupling_check`、`thermal_stack`、
  `thermal_via_array`、`thermal_plate`、`thermal_transient`、`thermal_heatsink`、
  `thermal_convection`、`board_ir_drop`。
- 新增 81 个用例（`test_spice` 24 / `test_power` 23 / `test_thermal` 25 / `test_board_ir_drop` 9），
  全量 **531 passed**；`--selftest` 加 4 条指纹（二极管 0.692543633 V / 1 mm×0.2 mm×35 µm 铜
  0.002462857 Ω / FR4 1.6 mm 100 mm² 53.333 K·W⁻¹ / 两段不同宽 IR drop 0.028733333 V），**38/38**。
- 文档：`docs/17-仿真层.md`（十八篇），索引与 README 工具表同步。

### 五个真缺陷（都是"数算得出来但语义错了"）

- **`_element_report` 的二极管分支遮蔽了解向量**：`x = min(_limit_junction(...))` 把入参 `x`
  改成 float，二极管之后的 C/R 全部 `TypeError`。改名 `xd`。
- **温度只让 Vt 涨、IS 不动**：27 ℃ 0.6925 V、100 ℃ 反而 0.8597 V。按 SPICE 实现 `IS(T)`
  （XTI/EG/TNOM）后 100 ℃ → 0.5709 V、−40 ℃ → 0.8004 V（≈ −2 mV/K）。
- **瞬态多跑一步**：`tstop/h = 100.00000000000001`，裸 `ceil` 得 101 步，末点在 1.01 ms。
  改为带相对容差的 ceil。
- **AC 截断静默**：扫频点数被 `max_points` 截断后，末点不是 `fstop`，一条还在上升的曲线会被
  读成"在这里谐振"。现在返回 `warnings` 点名只解了前 N 点、末点频率是多少。
- **`thermal_transient` 的 R/C 长度不匹配报 `ARG_RANGE`**：这是输入畸形不是越界，改 `ARG_INVALID`。

### 顺带收紧的口径

- `_element_report` 的二极管 `model_params` 补 `IS_nominal` / `TNOM_c` / `XTI` / `EG_ev`——
  "为什么是这个压降"不必猜。
- `board_ir_drop` 显式给 `copper_um` 覆盖叠层时**一定**告警（`warnings` 里写 "OVERRIDES the stackup"），
  并在 `assumptions.copper_um_source` 标 `explicit | stackup`。
- `decoupling_check` 增 `regulator_bandwidth_hz`：只判电容真正负责的那一段，避免把稳压器
  的环路增益算成电容的本事。

## [0.0.2] - 2026-10-28

天线层：从"能算板"扩到"能设计第一个天线原型"。**不含全波求解器**，这一点写在每个返回里。

- 新增 4 个计算模块：`calc/feedline.py`（微带 / 带状线 / CPW / GCPW + 反解宽度）、
  `calc/antenna.py`（贴片 / 印刷线 / 偶极单极 / 螺旋 / 小环 / Chu 上限 / 芯片天线）、
  `calc/matching.py`（反射 / L / π / T / 单短截线 / 四分之一波长）、
  `calc/link.py`（FSPL / Friis / 最大距离 / 有效面积 / 远场 / 菲涅耳）。
- 新增 `oracle.antenna_keepout` 与 21 个工具（45 → **66**）：9 个 `feedline_*`/`antenna_*`、
  6 个 `match_*`、5 个 `link_*`、1 个 `board_antenna_keepout`（派生量，pad/track/via 与 zone 分开计数）。
- 新增 93 个用例（`test_feedline` 18 / `test_antenna` 30 / `test_matching` 26 / `test_link` 19），
  全量 **450 passed**；`--selftest` 加 4 条天线指纹（3.14 mm 微带 / 37.2×28.8 mm 贴片 /
  40.05 dB FSPL / 回代 50 Ω），**34/34**。
- 文档：`docs/16-天线与馈线.md`（十七篇），索引与 README 工具表同步。
- 边界（写进 `_capabilities().not_implemented_yet` 与每个天线信封的 `not_computed`）：
  方向图、外壳/手持失谐、同板互耦、SAR、匹配后的实际带宽——需要全波，本层不做也不猜。

### 两个真缺陷（都是"数值算得出来但语义错了"）

- **`l_match` series-first 分支符号错**（`b = -xtot/den` → `+xtot/den`）：25 Ω 纯阻负载会被
  "匹配"成 10+j20、残差 VSWR 0.707。抓到它的是"每个解必须回代重算残差"这条纪律。
- **蛇形天线的打包方向与 fits 判定**：原实现按长度方向数折（`int((bl+gap)/(bw+gap))`）、
  且不判"装不装得下"，会输出一个看着合理的展开长度。改为按宽度方向打包
  （`pitch = wire_width + 0.5`）+ `fits` / `fits_without_meandering` / `fits_against`。

### 顺带修掉的小口径

- `feedline_width` 的有效性窗加 1e-9 相对容差：边界值本身合法（0.05 与 20.0 曾被误拒）。
- `small_loop` 的 `r_rad` 用 6 位有效数字输出，另给 `r_rad_mohm`（原来 `round(...,5)` 把
  2e-7 Ω 舍成 `0.0`，读起来像"不辐射"）。
- `chip_antenna` 小净空在顶层给 `warnings`（λ/20、近场边界一半两条阈值）。
- `reflection` / `l_match` 接受 `"100+25j"` / `"-j30"` / `"j30"` 这类字符串阻抗。

## [0.0.1] - 2026-10-28

首个完整版本：六层——**Calc**（无板也能算）、**Adapter**（两种格式进同一个 Canonical）、
**Oracle**（在板上做几何测量）、**保真**（整份文档进树，事实从树里读）、**写路径**（板 + 原理图，
提案 → 闸门 → 落盘）、**Desktop**（探测/启动/关闭，永不碰工程文件）。
45 个 tool；357 个用例 + 29 项协议自检。

设计的核心不是"能画"，是**每条答案都知道自己有多可信**：厂商引擎说过的标 authoritative，
自己算的标 `authoritative: false` 并说明裁判在哪，算不了的一律 fail-closed 并说出缺什么。

### Calc（无板计算）

- `unit_convert`：立创 Pro 内部单位实测为 **mil**（标签只是显示单位），不做静默 25.4 倍换算。
- 载流：`width_for_current` / `current_capacity` / `via_current_allowed`，IPC-2221 图表拟合
  `I = k·ΔT^0.44·A^0.725`；过孔按桶壁侧面积折算并标 `not_standard_defined`（±30%）。
- 阻抗：`trace_impedance` / `width_for_impedance`（微带/带状线闭式 + 二分反解，越界回 `OUT_OF_VALIDITY`）。
- 差分阻抗两级：`diff_impedance`（边耦合闭式，IPC-2141A 族）与 `diff_impedance_field`
  （解真实截面上的 div(ε∇V)=0，有限体积 + 梯度张量网格 + 红黑 SOR，纯 stdlib）。
  闭式在实测几何上系统性**偏低 8~11%**、方向一致——所以"调宽凑数"会先把线做窄。
  场解的误差对着零厚度带状线精确共形解（≤1.2%）、孤立微带对 Hammerstad（≤2.8%）、
  s/h=8 耦合对 2×孤立精确解（≤2.7%）三张表量过，`tests/test_field2d.py` 每次重算。
- 射频：`lambda_over_20`。
- 安规：`creepage_required` / `clearance_required` 无表 fail-closed；`safety_table_check`
  把"这张表能不能信"做成可执行判据（来源 ≥2 / 电压与材料组单调 / PD1≤PD2≤PD3 / 无重复 key）。
  `data/ipc2152_tables.json` 与 `data/creepage_tables.json` **故意零行**并写 `why_empty`：
  公开来源互相矛盾，填一个猜的值比拒答更糟。

### Adapter 与 Oracle

- **双 Adapter**：KiCad `.kicad_pcb` 与立创 `.epro` 解析到同一个 Canonical（mm、Y 向上、网络按名字）；
  单位与轴向只在 adapter 边界归一化一次。
- **立创侧全部实测**（194 个真实 `.epro` 归档）：记录布局、**恒 mil** 单位、一次保存只改一个成员、
  逐字节重打包一致；`.eprj2`（SQLite）只读。
- **Oracle**：`board_clearance` / `board_free_space` / `board_pour_connectivity` /
  `board_cross_split` / `board_loop_area`，每条答案带 `authoritative: false` 与
  `verdict_available_from: board_drc`——测量不是判决。
- **`board_cross_check`（对账）**：同一份文件上，我们的间距测量与 `kicad-cli 10.0.7` 的 DRC
  **双向**比：13 块官方 demo 板，厂商报的 56 条可比违规全部命中（recall 1.0），我们多报 91 对
  且都在保守侧。不可比的东西单列而不算错：铺铜、差分对内部、板边/孔规则、阻焊钢网。
  详见 [docs/15](docs/15-对账-推导对着厂商判决.md)。
- **焊盘几何另找一个裁判**：`tests/test_kicad_pad_placement.py` 用 `pcbnew` 自己
  （`LoadBoard()` → `Pads()` → `HitTest()`）在四块板上对 412 个 pad 做 20188 次探测，
  **0 处不一致**。

### 保真与裁判

- `fidelity/`：整份文档进树（`sexpr.py` 保留原始拼写、`kicad_doc.py` / `epro_doc.py` 携带每个节点
  与每个 ZIP 成员、删除本身作为事实保留），`verify()` 做 **token 双射**与**字节一致**自证，
  `coverage()` 报告"带上了但还没读"的节点。
- `views.py`：事实从树里读，**每条记录都带 `raw`**。
- `engine.py`：`kicad-cli` 当裁判——stats / Gerbers / position CSV / DRC / 原理图网表与 ERC。

### 写路径

- **板（KiCad）**：24 个 op（板框/开槽/挖空/机械层/文字/移动/DNP/走线/过孔/keepout/zone/网络/netclass/板厚），
  `propose` 只算不写 → `apply` 过五道闸门（字节 / token 双射 / 后置条件 / kicad-cli 比对 / DRC 差集），
  checkpoint 与回滚。
- **板（立创）**：原地重写一个成员 + 六道闸门；六条 op
  （`move_component` / `set_track` / `set_via` / `set_text` / `set_pad_net` / `delete_record`）。
- **原理图（KiCad）**：18 个 op，四道闸门，裁判换成**网表 + ERC**——DRC 判铜，原理图的产品是连通性。
  **立创 `.esch` 明确拒写**（`WRITE_UNSUPPORTED_FORMAT`），只写单张图纸。
- `board_checkpoints` / `board_restore`：每个改动都可回到某个 sha256。

### Desktop

- `app_detect` / `app_status` / `app_launch` / `app_close` / `app_open_project`：
  探测顺序为显式路径 → 运行中镜像 → 盘符 → 注册表，永远回带 `tried`；
  关窗只发 WM_CLOSE（等同点 X），`force` 才 TerminateProcess；**从不读写工程文件**。
- `lceda_api_probe`：立创 Pro websocket 网关（49620-49629）的真客户端（RFC 6455 握手/掩码/分片/超时）。

### Fixed（全部由外部裁判抓出，全部"自洽但错"）

- **合成样本连 KiCad 都不认**：`(dnp)` 裸写、v7 里用 `property`、`gr_rect` 混 v8 的 `stroke`——
  此前所有"Adapter 通过"都建立在一份 KiCad 拒绝加载的文件上。
- **`y = -y` 顺手把旋转方向也反了**：2118 个 pad 里 1030 个偏到 111 mm 外，DRC 干净的板报出成片假违规。
- **封装铜图形只旋转没平移**：四个 `POLY` 全堆在板原点，真违规够不着、假违规多出来。
- **点落在我方多边形内的判定**用成了"到边界的距离"，DRC 报的 8 条只配上 4 条。
- **`shorting_items` 与 `clearance 0.0000` 是同一物理条件**，分开数会把真实一致算成漏报。
- **`.kicad_pro` 的 `netclass_assignments` 没读**：整个 `USB_DIFF` 类里一个 net 都没有，13 条违规全被"自家规则"清掉。
- **`F&B.Cu` 是通配层**、`np_thru_hole` 且 size==drill 的焊盘**没有铜**——两者叠加造出 5419 对假违规。
- **封装/焊盘上的局部 `(clearance ...)` 优先于网络类**（厂商接受 0.125 对类 0.13）。
- **差分对豁免收到"非 pad-pad"**：同对的走线/过孔厂商走 `diff_pair_gap`，同对的两个 pad 仍按类规则。
- **两个写路径闸门 bug**：元件值改动被报成"网表变了"（会拦死所有正常字段编辑）；
  同一条 ERC 违规被 KiCad 重指到不同 item 时被算成"新增+解决"（会教人一按 `allow_new_violations` 了事）。
- **engine gate 假绿**：checkpoint 存成 `.board` 让三个比较全 skipped，`identical=None` 被当成绿灯 →
  现在"没比较成"不算通过。
- **三个演示构建被 DRC 拦下的真实错误**：丝印压板边、`track_dangling`/`via_dangling`
  （铺铜填充在 DRC 里不算连通性）。

### Known limitations

- **立创 Pro 的启动与开工程没能确认，但这次是量出来的**：临时副本上 `lceda-pro.exe <工程.epro>`
  0.5 s 后 rc=1 退出、不带参数 0.5 s 后 rc=0 退出，无窗口、不写用户数据；同环境 KiCad 能正常拉起。
  `open_argv_verified` 保持 `false`，`opened` 只会是 `unconfirmed`。
- **立创实时 API 客户端写了，但没有一次被真应用应答**：本机 49620-49629 全部 `no_listener`
  （需用户手动开「外部交互权限」），每条返回带 `verified_against_app: false`。
- **立创写路径不声称应用会接受写过的文件**：没有一份写过的归档被应用重新打开过，
  每次 apply 都在 `not_verified` 里说；`set_pad_net` 追加的 `PAD_NET` 行从未被观察到应用自己写，
  标 `verified_by_app: false`。
- **SQLite 格式（`.eprj2`）与立创原理图（`.esch`）拒写**：`history_data` 加密、`ticket` 单调，
  记录布局没在真实保存上测过。这不是"以后做"，是"现在拒"。
- **原理图只写单张图纸**，不做跨页改动。
- **比不了的几何明说不比**：铺铜（zone）铜、板边与孔规则、阻焊与钢网；差距 <0.002 mm 的对单独计数
  不判；`free_space` / `pour_connectivity` / `cross_split` / `loop_area` 没有厂商对应物，回
  `not_comparable` 并说理由。
- **实测上限**：88.7 MB 的 jetson 板 DRC 两次都没出报告（823 s / 2543.8 s `ENGINE_FAILED`），
  默认超时 900 s，超时即报错、不降级、不复用上次结果。
- **紧间距耦合的场解没有独立参考答案**，那种几何下工具自己吐 `reference_note`。
- **IPC-2152 / IEC 60664-1 的表是空的**（见上）；**DFM（板厂工艺能力）没有实现**。

### Docs

`docs/00`~`docs/15`：从开源 MCP 调研、AI 原生表示、信息丢失审计、布局度量层、无损转化总纲、
两种存储形式、自研定位、PCB 流程映射、方法论冲突登记、桌面控制、保真层、写路径、
立创实测、原理图写路径，到**对账**——每一篇都在推翻或收紧前一篇的某个前提。

### 早期内部阶段标签（仅留档，不再使用）

| 旧标签 | 提交 | 一句话 |
|---|---|---|
| 0.1.0 | `eba2e02` | Calc 层 MCP 服务首个版本 |
| 0.2.0 | `729cbdc` | Desktop 层 + 双栈 Adapter + Oracle + 差分阻抗 |
| 0.3.0 | `d1b1724` | 保真层：中间层等于那张板，不再只靠推导 |
| 0.4.0 | `5469db8` | 两种格式的写路径（propose → 闸门 → 落盘） |
| 0.5.0 | `78a8c57` | 立创侧推测全部改成实测（194 个真实 `.epro`）+ 原地重写写路径 |
| 0.6.0 | `909f052` | 原理图进写路径（KiCad 18 op + 网表/ERC 四闸门），立创 `.esch` 明确拒写 |
| 0.7.0 | `521a283` | 诚实清单收口：与厂商 DRC 对账（13 板 recall 1.0 / 412 pad 对 pcbnew） |
