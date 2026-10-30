# pcb-agent-mcp

让 AI 在 **立创 EDA Pro** 与 **KiCad** 里真正"算得出来再动手"的 MCP 服务。

当前版本 `0.0.18` 已实现十层：只读的算/保真/适配/度量/仿真，一条**带闸门的写路径**（提案 → 落盘，两种格式；还能**分步落盘**，让人在 EDA 窗口里看着它一步步长出来并随时喊停），一个**会说不通在哪**的布线器，一套**外包给立创/板厂自己的裁判**的制造输出与原理图同步。
立创侧的每条口径都以 **194 个真实 `.epro` 归档**为证据（[docs/13](docs/13-立创实测-真实存储与记录格式.md)）；
KiCad 侧的几何与规则对着厂商自己核过：13 块真实板的 DRC 对账（厂商报的 56 条全命中）、
412 个焊盘与 `pcbnew` 逐点对账 0 不一致（[docs/15](docs/15-对账-推导对着厂商判决.md)）。

| 层 | 做什么 | 状态 |
|---|---|---|
| **Calc** | 无板子的工程计算（载流/阻抗/差分阻抗/λ/20/爬电） | 已实现 |
| **Fidelity** | 把整份板文件**转换**成 AI 可读的表达：每个节点都进树，事实从树里读，往返由 KiCad 自己验证 | 已实现（KiCad 侧已被 kicad-cli 判决） |
| **Adapter** | 在保真树之上给一个 Canonical 便利视图（mm、Y 上、net 用名字） | 已实现（KiCad 侧：13 块真实板与 DRC 对账、412 个 pad 与 `pcbnew` 逐点对账；立创侧：194 个真实归档实测） |
| **Oracle** | 在解析出的板子上做度量（clearance / free_space / 铺铜连通 / 跨分割 / 环路面积 / 单网 IR drop） | 已实现 |
| **Sim** | 无板子的物理计算：SPICE 子集（DC/瞬态/AC）、铜阻与 PDN、热阻与 2-D 板温度、薄板模态与振动响应、平面谐振、电热耦合（单集中 Rth） | 已实现（子集，未实现项逐条点名拒绝） |
| **Desktop** | 找得到/启得动/关得掉 EDA，能打开工程并用窗口标题确认 | 已实现 |
| **Write** | `propose`（只算不写）→ `apply`（checkpoint + 原子写 + 六道闸门 + 失败回滚） | 已实现（KiCad 真机验证；`.epro` 实测口径＝原地重写一个成员） |
| **Route** | `board_route`：A* 布线（双栅格 + 精确多边形判决 + 事务化 + 按位置 rip-up），规则只从板/工程/调用方取，取不到就拒绝 | 已实现（50 网真板 baseline 27/50 → sweep+rip 38/50、端到端 apply 后 kicad-cli DRC 0 新增阻断项、12 网提速 5.5×；`0.0.15` 起爬电/差分对间距/等长进搜索，`0.0.16` 起回流/阻抗目标/拥塞协商也进了（协商默认关，一块板 +3 网 / +61% 时间），`0.0.17` 铺铜重填与多端网拓扑，`0.0.18` 起布不通要报**挡路的是谁**（`board_blockers`）与**哪些球埋着**（`board_fanout`）；仍然四邻 + 4×45°、不拆已接受的扇出、拓扑默认 mst 且不是精确 RSMT） |
| **Fab** | 制造输出：Gerber / Excellon / 位置文件 / gbrjob / STEP / PDF / BOM / 网表 / 图形，加 `manifest.json` 与可选 `fab.zip` | 已实现（**只外包不重算**：`kicad-cli` 在临时目录里跑，产物**重新读一遍**验终止符 / 工具表 / 行数 / ISO-10303-21，不合格判 failed；真板 15 个产物 / 103 923 字节 / `fab.zip` 33 577 字节；`out_dir` 落在源目录里直接拒） |
| **Sync** | 原理图网表 ↔ 板：逐脚差集，再变成 op 交给写路径 | 已实现（两侧都用权威：KiCad 自己的网表器 + 板文件里的 pad net；缺封装只报 `needs_placement`，**没有诚实的默认位置**；`sync` 一个字节都不写） |

写路径的三条硬规则：**提案不落盘**（plan 单次使用、1 小时过期）；**落盘要 `confirm=true`**；
**闸门不信任写代码本身**——KiCad 侧是盘上字节、token 双射、每条 op 的后置条件、`kicad-cli` 的
stats/Gerber/贴片坐标比对、以及 **DRC 差集**（新增违规就拦下并回滚）；立创侧是写盘字节、其他成员
逐字节不变、未点名行不变、流仍可解析、改动恰好等于计划、重新解析后事实成立。
KiCad 侧这套闸门在自造示例板上真拦下过三次错（丝印压板边、悬空走线、悬空过孔）。

> 设计文档在 [`docs/`](docs/)，按编号顺序读：
> `00` 调研与架构 → `05` 无损转化架构总纲 → `06` 存储形式与信息传递 → `07` 自研定位与风险分期
> → `10` 桌面控制与双栈读路径 → `11` 保真层 → `12` 写路径 → `13` 立创实测（真实存储与记录格式）
> → `14` 原理图写路径 → `15` 对账（推导对着厂商判决）→ `16` 天线与馈线 → `17` 仿真层
> → `18` DFM 工艺能力校验 → `19` 机械振动（薄板 Ritz 模态、Miles、Steinberg、冲击、传递率）
> → `20` 平面谐振与电热耦合 → `22` 微带与腔体滤波器 → `24` 立创进程内桥
> → `25` 技能并入清单 → `26` 分步可见与铺铜闭环 → `27` 自动布线器。

## 为什么不是"又一个图元 MCP"

现有 EDA MCP 的普遍形态是「把图元 API 转成 tool + 返回 `{"ok":true}`」，模型每次只拿到一个布尔值，
剩下的靠想象 —— 这就是「AI 画 PCB 会瞎话」的根因。

PCB 文件里只有图元；AI 需要的是**事实**（这块板 1A 走多宽、90Ω 微带多宽、2.4GHz 布线间距够不够）；
中间那层「推导」几乎没人做。而源头并不缺数据（立创 `.epcb` 的规则、keepout flags、槽孔、热焊盘辐条都在；
KiCad 的叠层 εr/tanδ/铜厚也在）—— **不是没存，是没人算。**

所以定位是：**解析只占小头，答案的大头是推导** —— 但这两个词在这一版里的含义改过一次
（见 [docs/11](docs/11-保真层-转换还是推导.md)），说清楚比给个比例更有用：

- **解析不是"随手扫 5%"。** 它必须**无损**：整份文档进树（每个节点、每个 ZIP 成员），
  往返由 token 双射与逐字节一致自证。解析省下来的每一分，最后都会变成几何上的一个猜。
- **推导才是主线**：AI 真正要的是事实（1A 走多宽、间距够不够、这块能不能放、铺铜是否真连通），
  文件里没有，只能算。
- **推导必须能被外部推翻。** 每条派生答案都带 `authoritative: false` 和"裁判在哪"；
  有厂商对应物的那一项（间距）已经对过账——13 块真实板上厂商 DRC 报的 56 条全部命中
  （`board_cross_check`，见 [docs/15](docs/15-对账-推导对着厂商判决.md)）。比不了的就明说比不了，
  而不是算一个看起来对的数。

## 三条诚实规则（代码里强制，不是口号）

1. **没有 basis 的值一律无效。** 每个结果必须带 `model` / `basis` / `precision` / `assumptions`，
   `require_basis()` 在信封层强制；缺 `precision` 直接抛错。
2. **一律 fail-closed（对没核验过的那一半）。** 载流不传 `standard` 报 `STANDARD_REQUIRED`；
   `ipc2152` 的闭式系数文件若漂移（`_model_check` 不过）报 `STANDARD_DATA_NOT_LOADED`；
   非 IEC 60664-1 族的安规标准（IEC 62368-1 / 61010-1 / UL 60950 / IPC-2221）仍报
   `SAFETY_TABLE_MISSING`。IEC 60664-1 与 IPC-2152 已经用行为对拍/闭式锚点换成了**可重生成**的数，
   不再靠“表里有没有行”判断。
   **猜一个安全间距比不给答案更糟。**
3. **不复用未核验的第三方实现。** 见下节。

## 为什么要内置计算：两个真实反例

调研技能市场时实测到两处会静默给错答案的现成实现：

| 来源 | 给出的值 | 真值 | 偏差 |
|---|---|---|---|
| 某免费 KiCad 技能 `trace_width()` | 0.139 mm（1A/ΔT10/1oz 外层） | **0.3021 mm** | 低估 **2.16×**，方向危险 |
| 某射频设计技能 λ/20 表（2.4GHz） | 1.5 mm | **3.01 mm** | 差 2×，且与它自己给的公式矛盾 |

第一条的根因是反解式漏了 `^(1/0.725)`；第二条是手抄表格与公式脱钩。
**这类数字一旦进了模型上下文，DRC 是全绿的。**

## 信封契约

每个成功结果：

```json
{
  "status": "ok",
  "value": 0.3021,
  "unit": "mm",
  "model": "ipc2221_chart_fit",
  "basis": "IPC-2221 Fig. 6-4 external-layer chart, fit I = 0.048*dT^0.44*A^0.725",
  "precision": "+/-20%",
  "assumptions": { "delta_t_c": 10, "copper_oz": 1.0, "layer": "external" },
  "extra": { "cross_check_rule_of_thumb": "..." }
}
```

每个失败结果：

```json
{ "status": "error", "reason_code": "STANDARD_REQUIRED", "message": "...", "hint": "..." }
```

约定：信封必写 `status`（ok/error），业务字段**禁止**叫 `status`，避免一键两义。

## 工具（151 个）

下面逐行列的是**读路径、写路径与计算**的主力工具。`0.0.10` 之后陆续加的那些（`board_route` / `pdn_*` / `board_electrothermal` / `filter_*` / `magnetics_inductor` / `stackup_*` / `rf_chain` / `design_rules` / `layout_review` / `schematic_review` / `part_*` / `board_routability` / `board_zones` / `board_fill_zones` / `board_progress` / `lceda_pro_*` / `board_intent` / `lceda_pcb_write` / `board_fab_package` / `board_export` / `board_bom` / `board_netlist` / `board_graphics` / `schematic_pcb_diff` / `schematic_pcb_sync` / `board_blockers` / `board_fanout` / `board_run_ledger` / `fullwave_solver` / `filter_mc_yield` / `magnetics_dcm` / `magnetics_core_loss` / `magnetics_dcr` / `magnetics_winding_ac` / `filter_coupling_matrix` / `fusing_current` / `cavity_plane_q`）没有逐行进表 —— 权威清单请调 **`calc_capabilities`**（它同时列出「拒算什么、为什么」），逐条口径见 [docs/20](docs/20-平面谐振与电热耦合.md)~[docs/27](docs/27-自动布线器.md)。

| tool | 作用 | 关键约束 |
|---|---|---|
| `calc_capabilities` | 自述：能算什么 / 拒算什么及原因 / 还没做什么 | 建议先调它 |
| `unit_convert` | 单位换算（立创实测存 **mil**，标签只是显示单位） | 未知单位 `UNIT_UNKNOWN` |
| `width_for_current` | 电流 → 线宽（IPC-2221 反解） | 必须传 `standard`；ΔT≤0 拒绝 |
| `current_capacity` | 线宽 → 载流 | 开窗时只取下界，不计锡层 |
| `via_current_allowed` | 过孔载流（桶壁侧面积折算） | 标注 `not_standard_defined`，±30% |
| `trace_impedance` | 几何 → Z0（微带/带状线） | w/h 超出 0.05..3.0 标 `validity_note` |
| `width_for_impedance` | Z0 → 线宽（二分反解） | 不可达时回 `UNREACHABLE` + 可达区间 |
| `lambda_over_20` | λ/20 布线间距 | 与频率/εr 绑定，不给记忆值 |
| `creepage_required` | 爬电距离（IEC 60664-1 Table F.5） | 非 60664 族仍 fail-closed；PD4 回 `STANDARD_SCOPE`；不覆盖 homogeneous field |
| `clearance_required` | 电气间隙（瞬态 Table F.2 / 峰值 Table F.8 / 海拔修正） | 两分支任一越界即整题拒答（复刻 C++ 的 -1 传播）；reinforced 另走 ×1.6 |
| `spacing_required` | 一次给出 clearance / creepage / 裁决值与 `governing` | 额外强制 `creepage >= clearance`（标准要求，`creepage_mm` 本身不夹） |
| `safety_table_check` | 判定一张安规表能不能用；`which="iec60664"` 时走 8 条专属检查（含 F.8 与 PD 无关、线性段过原点、`not_covered` 不得被删） | 只能判“转录没法作弊 + 行为对拍过”，判不了“标准原文还有没有未覆盖的条款”；9 条变异用例保证它不是永真的 |
| `diff_impedance` | 差分对几何 → Zdiff（边耦合微带/带状线） | s/h 超出 0.1..3.0 标 `validity_note` |
| `coplanar_field` | 共面线（cpw/gcpw）→ Z0 + eps_eff，解真实截面上的 div(ε∇V)=0；**全板网格，不用对称面** | 场解在七个几何上一律比闭式**读低 1.19%~3.39%**（方向是结论的一部分）；铜厚只划网格不建模；零厚度 `∫g²ds` 对数发散，以 `mesh_drift_pct` 明示；可选 `ground_mm` 把共面地做成**有限宽**（默认 0 = 无限，逐位零变化）：`g/2` 宽的地把 Z0 抬 +13.7%、eps 也抬 +7.8%，地外沿是电荷奇异边（节点 +16% 但 SOR 迭代 ×6） |
| `coplanar_pair_field` | 共面**差分对**（边耦合 cpw/gcpw）→ Zdiff + Z_odd/Z_even/Z_common + eps_eff；同一网格上两个驱动（不设对称面），`frequency_ghz` 一并给导体与介质损耗 | **没有闭式可对账**：给的不是「±x%」而是叠加恒等式（残差 1.9e-8）、奇模电荷平衡 −1.000000000、左右份额逐位相等、`s=w/6` 时共模落到 2w 单线 −0.87%；`Z_odd` 按物理发散到 0，大间距**不**收敛到单线；差分导体损耗是零厚度下界 + 网格漂移 8.79% 的不确定度；可选 `ground_mm`（默认 0 = 无限）：窄地是**共模问题**——`g/2` 时 `z_odd` +1.1% 而 `z_even` +21.9%；一次 `levels=2` 约 40 s |
| `diff_impedance_field` | 同一几何 → Zdiff，但解真实截面上的 div(ε∇V)=0（有限体积 + 红黑 SOR，纯 stdlib） | 参考解只覆盖零厚度带状线/孤立微带；紧间距耦合回 `reference_note`（求解器的意见，不是被核过的数）；不建模阻焊/蚀刻梯形/铜损 |
| `diff_width` | Zdiff → 线宽（间距固定，二分） | 不可达 → `UNREACHABLE` + 可达带宽 |
| `schematic_pcb_diff` | 原理图网表 ↔ 板：**逐脚**差集（哪个封装的哪个脚换了网 / 板上没网） | 两侧都用权威（KiCad 自己的网表器 + 板文件里的 pad net）；缺封装只报 `needs_placement`，**不发明位置** |
| `schematic_pcb_sync` | 把差集变成 op，走正常的 propose/apply 闸门 | 一个字节都不写；位置必须调用方给 |
| `board_fab_package` | 制造包：Gerber/Excellon/位置/gbrjob + `manifest.json` + 可选 `fab.zip` | **只外包给 `kicad-cli` 不重算**；产物重新读一遍验终止符/工具表/行数，不合格判 failed；`out_dir` 落在源目录里直接拒（`OUT_DIR_BESIDE_SOURCE`） |
| `board_export` / `board_graphics` | Gerber / STEP / PDF / 图形 | 同上；`kicad-cli` 缺失即 `KICAD_CLI_NOT_FOUND`，不回退自研 writer |
| `board_blockers` | 布不通时**挡路的是谁**：八扇区哪些开着 + 每个对象的可变性标注 | 标注是**结构事实**（pad movable / 铜 reroutable / 铺铜·keepout·板框 fixed），不是替你做的决定 |
| `board_fanout` | BGA 扇出：每个球朝外短桩 + 四次旋转，用布线器同一套精确几何判 | 不做 via-in-pad、不回拆已接受的出口、**不声称完整扇出**（`skipped` 就是埋着的球） |
| `board_run_ledger` | 这块板试过没有、当时说了什么（按板文件 sha256 归键） | **永不改变答案、不是缓存**、不存能重建板的数据；`PCB_AGENT_LEDGER=0` 一个字不写 |
| `fullwave_solver` | 全波**接缝**：装了哪个求解器、版本、要你给它什么 | **点名未装即 `SOLVER_NOT_FOUND`**，绝不回退闭式数；`verified_here` 恒 `false`（本仓库从未跑过任何全波求解器） |
| `board_summary` | 开板：计数/板框 bbox/叠层/规则 + **哪些事实缺失** | 只读；格式按内容嗅探 |
| `board_components` | 元件位置（mm、Y 向上）、角度、pad 数、bbox | — |
| `board_nets` | 每网络 pad/线/过孔/铺铜数与总走线长 | 暴露"网络表里有、板上没铜" |
| `board_clearance` | 不同网络铜的边到边最小间距 + 肇事对 | 减线宽（保守）；给出测量值不是 DRC 判决 |
| `board_free_space` | 某层可放元件的大矩形（占用栅格 + 余量） | 回答"能放哪"而不是"多密" |
| `board_pour_connectivity` | 铺铜网络 pad 是否真落在填充铜里 | 只有轮廓时标 `outline_only` |
| `board_cross_split` | 信号是否跨参考平面断口 | **无平面 zone 直接拒绝**（看不见就不答） |
| `board_loop_area` | 环路面积（走线长 × 到下层铜的距离） | **无叠层直接拒绝**（猜介质高度 = 猜 EMC 答案） |
| `board_fidelity` | **自证**：节点/字节计数、token 双射往返、还没读取规则的节点清单，KiCad 侧再让 kicad-cli 把原件与写回件各画一次并比对铜箔/统计/位号 | 只读；临时目录工作，绝不写在工程旁边 |
| `board_view` | 从保真树读事实：nets/规则类/层/叠层/setup/元件/pad（精确尺寸·钻孔·旋转·网络）/走线/过孔/zone（含**文件里存的填充多边形**）/板框图元/板上文字/尚无规则的节点 | 每条记录都带 `raw`（原始 S 表达式），视图漏掉的不会丢 |
| `board_drc` | **判决**：跑 KiCad 自己的 DRC 引擎，回违规项（按严重级/类型）+ 未连接项 | 立创无命令行 → 明确拒绝给判决，不近似 |
| `board_stats` | **判决**：KiCad 自己的板统计（尺寸/面积/正反铜面积含铺铜/板厚/最小线宽与孔径） | 只读 |
| `board_cross_check` | **对账**：同一文件上，我们的间距测量与厂商 DRC 双向比对（recall / 漏报 / 我们多报 / 判不了） | 只有 clearance 有厂商对应物；zone 铜、板边孔规则、阻焊钢网明说不比；其余四个派生量回 `not_comparable` |
| `epro_inspect` | 不解译地列出 `.epro` 成员/类型计数/原始 key 名 | 用来"关掉未知字段"，而不是猜字段名 |
| `board_ops` | 写路径的操作目录（KiCad 24 个 / `.epro` 6 个）+ 参数 + 它影响板子的哪一部分 | 调 `board_propose` 前先看它 |
| `board_create` | 造一块新的空板（叠层/规则/闭合板框），写完再问 KiCad：板框闭合吗、DRC 干净吗 | 任一为否就删掉文件而不是留个“看着像板”的东西 |
| `board_propose` | 只算不写：plan_id、改前/改后 digest、每条 op 将被检查的后置条件、blocking | 按**内容**判格式（`PK` = `.epro`） |
| `board_apply` | 落盘：checkpoint → 原子写 → 五道闸门 → 不过就回滚回原字节 | 必须 `confirm=true`；plan 单次使用 |
| `board_checkpoints` | 某个文件的所有检查点（时间、sha256、原因） | — |
| `board_restore` | 回到某个检查点（默认把当前态先再存一份） | 必须 `confirm=true` |
| `app_detect` | EDA 装在哪（显式路径→运行中镜像→盘符→注册表） | 永远回带 `tried` 证据 |
| `app_status` | 是否在跑/几个实例/哪个工程/API 网关端口 | 工程名来自窗口标题，空 = 没窗口显示工程 |
| `lceda_api_probe` | 立创 Pro 的 websocket 网关（49620-49629）：端口扫描 + RFC 6455 握手到拿到能力清单 | 网关只在用户手动开「外部交互权限」后监听；没应答就回 `no_listener`，每条都带 `verified_against_app:false` |
| `lceda_pro_status` | 立创 Pro **进程内桥**在不在：枚举 `\\.\pipe\JLCEDAPro*`、逐个 ping | 只认"答复里带 bridgeVersion"的管道；名字对不算数 |
| `lceda_pro_api` | 问应用自己的 API 面（命名空间类型表 / 某个类的方法） | 连 `classToProperty` 一起回：`PCB_Document` 运行时叫 `pcb_Document` |
| `lceda_pro_open` | 在**运行中**的应用里开工程，回 sessionId | 只对已存在文件；`headless` 仍是活会话。**open 是否碰工程字节未验证** |
| `lceda_pro_sessions` | 列开着的文档会话 | 会话是文档，不是进程 |
| `lceda_pro_close` | 关会话（可 destroy） | 不写工程文件；未保存改动会丢 |
| `lceda_pro_run` | 在编辑器进程里跑一段 JS（async 函数体，`eda` 在作用域） | **能写**：片段自己决定做什么，本工具不担保；实测只跑过只读片段 |
| `app_launch` | 分离启动 EDA（不带工程） | 启动进程 ≠ 打开工程 |
| `app_close` | WM_CLOSE 关窗（等同点 X，可能弹保存框） | `force` 才 TerminateProcess；不碰文件 |
| `schematic_ops` | 原理图写路径的 op 目录（KiCad `.kicad_sch` 18 个）+ 每条用的坐标系 | 调 `schematic_propose` 前先看它 |
| `schematic_propose` | 只算不写：plan_id、改前/改后 digest、**预测的网表与 ERC diff** | 立创 `.esch` 直接 `WRITE_UNSUPPORTED_FORMAT` |
| `schematic_apply` | 落盘：checkpoint → 原子写 → 四道闸门（含 KiCad 网表 + ERC 双向对比）→ 不过就回滚 | 必须 `confirm=true`；plan 单次使用 |
| `schematic_summary` | 只读：这张图**是**什么（每个符号的真 reference/value/footprint/引脚、计数、工程名）+ KiCad 网表与 ERC | 只读，不创建任何文件 |
| `schematic_view` | 只读：summary / symbols / objects / netlist / erc / lib_symbols / raw | 每个返回都重复坐标系说明 |
| `app_open_project` | 打开 `.epro`/`.kicad_pro`/`.kicad_pcb` 并按标题确认 | 三态 `confirmed/unconfirmed`；缺文件拒绝且不创建 |

### 天线 / 馈线 / 匹配 / 链路（21 个，[docs/16](docs/16-天线与馈线.md)）

**这里没有全波求解器。** 方向图 / 外壳失谐 / 互耦 / SAR / 最终效率一律不计算，每个返回都带
`not_computed` 清单（`--selftest` 逐条检查）。

| tool | 作用 | 关键约束 |
|---|---|---|
| `feedline_impedance` | 微带 / 带状线 / CPW / GCPW，以及**边耦合差分对** `cpw_diff` / `gcpw_diff`（需 `spacing_mm`）→ Z0 或 Zdiff 与 εeff；损耗（导体 = 截面场解 `Rs/(2Z0)·∮g²ds`，另给薄电流式作第二估计 + `roughness_um`） | 差分的数**标成差分**（Zdiff = 2·Z_odd，附带 z_odd/z_even/z_common + 电荷平衡）；差分损耗分母是**差分电流**，两片地各 11.4% 形状因子；`t=0` 带网格漂移告警；差分一次求解 5~40 s（越宽越慢） |
| `feedline_width` | Z0 或 Zdiff → 线宽 | 不可达回 `UNREACHABLE` + `reachable_ohm` 两端实测；差分没有闭式，走粗网格阶梯 → 便宜网格二分 → 默认网格牛顿/割线，残差原样报（`abs_error_ohm` / `converged` / `default_solves`），一次 21 s~6 min |
| `antenna_patch` | 矩形贴片：W/L（含边缘 dL）、边缘 Rin、内缩深度、Q、VSWR<2 带宽、效率 | 传输线模型；内缩不含开槽宽度；效率需要 tanδ |
| `antenna_printed` | 印刷四分之一波长（IFA / 蛇形 / 印刷单极）：长度**区间** + IFA 抽头 + Chu 上限 | εeff 无可信闭式 ⇒ 给区间不给单值；蛇形必须传 `bbox_mm` 并回 `fits` |
| `antenna_dipole_monopole` | 细线偶极 / 单极：长度、辐射电阻、方向性、馈电缝隙 | 细线端效应 0.475λ0；粗导体不适用 |
| `antenna_helix` | 轴向螺旋：C/λ、螺距、轴长、Kraus 增益 / HPBW / Zin | 出 Kraus 窗即拒绝 |
| `antenna_small_loop` | 小环：C/λ、R_rad（6 位有效数字 + mΩ）、方向性 | C/λ ≥ 0.1 **拒绝**并给出"硬套会得到什么" |
| `antenna_q_limit` | Chu/Harrington 带宽硬上限（给定包围球） | 是上限不是估计；有损天线的宽 VSWR 曲线不算证据 |
| `antenna_chip` | 陶瓷/芯片天线：净空参考、近场边界、必核清单 | 增益与匹配元件是厂商数据 ⇒ 不计算；净空小给 `warnings` |
| `match_reflection` | 反射系数 / VSWR / 回损 / 失配损耗 + 串联元件量 | 纯电抗负载 → `ARG_RANGE` |
| `match_l` | L 型匹配（两种拓扑，含解析 Q） | **每个解回代重算** `resulting_z_ohm` 与 `residual_vswr` |
| `match_pi` / `match_t` | π / T 型匹配（给 Q 或给元件值） | 同上，回代残差 |
| `match_stub` | 单短截线（并联/串联 × 开路/短路） | 双解按距离配对报，禁止交叉配 |
| `match_quarter_wave` | 四分之一波长变换器 | 只对纯阻负载成立，其它拒绝 |
| `match_bandwidth` | 单谐振匹配的带宽 `BW = f0/Q`，以及 VSWR≤S 圈内带宽（乘 `(S−1)/√S`） | 只对**单谐振**成立；多谐振匹配不服从这条关系 |
| `magnetics_inductor` | CCM boost / buck / buck-boost：占空比、电感量、峰/谷/有效电流、CCM 结束点 | 不做磁芯**选型**（没有材料表）；`ripple` 是**你的设计选择**；没给 Isat 时饱和判 `undetermined` |
| `magnetics_dcm` | DCM 稳态：`K = 2Lfs/R`、boost 的 `D = √(K·M(M−1))`、CCM 边界负载（∝ 1/L） | 环路补偿、软启动、DC 偏置下的电感量跌落 |
| `magnetics_core_loss` | Steinmetz `Pv = k·f^α·B^β`，`k/α/β` **由你给**并写清单位约定 | 不内建材料表——同一牌号被两家拟合出两组三元组；无 DC 偏置、无磁通波形修正 |
| `magnetics_dcr` | 绕组直流电阻：每匝平均长度 × 匝数 × 电阻率/截面 | 只有 DC 部分：无趋肤、无邻近效应、无端接与焊点 |
| `magnetics_winding_ac` | Dowell 1-D 的 `F_R = R_ac/R_dc`：箔 / 圆线层叠，拆成自发场与邻近场两项，可回算欧姆 | 1-D 场看不见**气隙边缘场**（有气隙电感里最大的一项）；无 litz、无交错绕组的 MMF 图、无谐波叠加、无磁芯损耗 |
| `stackup_material` | 这个频率该用哪种板材（er / tanδ / 适用频段 / 成本），以及它看不见什么 | 频段上限是**工程约定**；Dk-频率曲线、批次公差、工艺兼容性都不在表里 |
| `stackup_design` | 4 / 6 层叠：层角色、铜厚、介质分配、**每信号层各自的 50Ω 线宽**、缝合孔 λ_g/20、铜对称性 | 介质分配是**等比**的，不是压合图；线宽必须回到板厂 er 上重解 |
| `rf_chain` | 级联增益、Friis 噪声（含各级占比）、级联 IIP3/OIP3、kTB 底噪与灵敏度 | 缺任一级 `nf_db` ⇒ NF 是 `None` 不是部分和；IP3 公式是 worst case；级间失配不在里面 |
| `design_rules` | 规则目录：id、需要哪个事实、单位、怎么测、证据等级、出处 | —— |
| `layout_review` | 用你测到的事实判 22 条布局规则（参考面连续性 / 去耦距离与同层 / λ_g/20 / 3W / 弯曲半径 / 板边 / 天线净空 / 换层地孔 / 差分等长 / 载流线宽 / 散热孔 / 晶振 / 丝印 / 测试点 / 最小孔与线宽） | **不替你读板**；没测到的规则一律 `undetermined` 并给出怎么测，空事实集直接拒答 |
| `schematic_review` | 用事实判 14 条原理图规则（逐脚去耦 / 每路 bulk / 复位与使能悬空 / LED 限流 / 栅极电阻 / 连接器 ESD / 防反接 / 保险丝 / 补偿 / 放电 / 悬空输入 / 测试点 / 射频匹配 / PA 旁路梯 / IEC 60664 隔离间距） | 不把网表解析成“这颗 IC 的电源脚”，事实要调用方给；隔离间距因表未验证而永远 fail-closed |
| `link_friis` | 链路预算：Prx / 路损 / EIRP / ERP | `FSPL_CONST = -27.5522`（写错 60 dB 的坑） |
| `link_max_range` | 给定灵敏度与额外损耗 → 最大距离 | 额外损耗要显式给，默认 0 就说 0 dB |
| `link_aperture` | 天线有效面积 | — |
| `link_far_field` | 远场距离：`max(2D²/λ, 5D, λ/2π)` 并说明哪个机制主导 | 三段都要给，只给一段会误导 |
| `link_fresnel` | 第一菲涅耳区半径 + 60% 净空 | — |
| `board_antenna_keepout` | 板级净空：某矩形内的铜与 **zone** | 派生量 `authoritative:false`；pad/track/via 与 zone **分开计数**（DRC 看不见铺铜失谐） |
| `sim_spice` | 真解电路：`.op` / `.tran` / `.ac`，MNA + 牛顿 + pnjlim + gmin 步进；装了 ngspice 可委派（`engine`）或双引擎对账（`cross_check`） | 内置子集只认 R C L V I E G D Q M，**X/F/H/K/S/W/B/T/O/U/A 逐条点名拒绝**（`engine="ngspice"` 下直通引擎）；`engine="ngspice"` 找不到二进制**报错不回退**；默认 `builtin` |
| `trace_resistance` | 铜线直流电阻 `rho(T)·L/(w·t)` | 只管这一段；过孔/扩展/端子不在这个数里 |
| `via_resistance` | 过孔桶壁电阻（截面是**铜环**不是钻孔圆） | 载流是另一码事，要温升判据 |
| `trace_inductance` | 直线走线自感（Grover 带公式，±20%） | 只用于比两种布局；地平面像项**明确未计入** |
| `ir_drop_chain` | 分段串联 IR drop（每段自己的宽/厚） | 不把不同线宽折算成平均宽 |
| `pdn_impedance` | 电容组 `\|Z(f)\|` 全曲线 + 平面电容；给 `plane` 时附带**腔体模诊断** | 要看**反谐振峰**，不是看最低点；`plane` 只做诊断，**不改那条曲线** |
| `pdn_plane_modes` | 平面腔体每个模的**峰值** `|Z_peak| = w·μ0·t·ψ(r1)ψ(r2)·Q/k²`（Q=1/tanδ）与端口耦合/null | **只给峰值不给曲线**（模态求和不收敛）；过孔桶壁/反焊盘/分割平面/铜损/开放边缘失谐全部未算 |
| `decoupling_check` | 给定电流/频段/纹波判够不够 | 三态 `pass/fail/indeterminate`；最坏点落在频段下沿说 **indeterminate**（那里由稳压器负责） |
| `thermal_stack` | 叠层 1-D 热阻 `Σ t/(kA)` + 给定功耗的温升 | 严格 1-D，看不见扩热 |
| `thermal_via_array` | 过孔阵列热阻（同环壁几何），可把周围 FR4 并联 | 偏乐观（桶壁中段镀层更薄） |
| `thermal_plate` | 2-D 板稳态温度（SOR）+ 热点 vs 平均 | 能量不守恒**拒绝**；点源须给 `size_mm` |
| `thermal_transient` | 热 RC 网络阶跃响应（Foster） | Rth/Cth 可给多级；单级会低估脉冲峰值 |
| `thermal_heatsink` | 反解需要多少 Rth(j-a) | **不提供散热片表**（那会是唯一一个不是推导出来的数） |
| `thermal_convection` | 由风速/尺寸给 h | 工程关联式 ±25%，只用于比两种假设 |
| `board_ir_drop` | 按**板上真实铜**算单网压降（节点带层号，两层只在 via/镀通孔相连） | 铜厚/镀铜未知或汇点不通即拒绝；`authoritative:false` |
| `dfm_capability` | 只校验一份**工艺能力文档**：11 条规则键、单位、方向、出处 | 未知键 / 没有 `source` / 空文档**整份拒收**；不内置任何板厂表 |
| `dfm_check` | 拿能力文档去量板上真实几何，逐条给 `pass/fail/not_checked/not_applicable` + 余量 + `where` | 文档里没写的规则列进 `missing_known_rules`（不是 pass）；有界搜索窗外只给 `lower_bound` |
| `vibration_plate_modes` | 矩形薄板自由振动：频率/模态形状（Rayleigh–Ritz 单轴单项式基 + 边界零空间 + 质量白化 + Jacobi）；四边简支/固支/自由/悬臂/四角点支撑 | 只有 Kirchhoff 薄板；不算阻尼、不算预紧/大变形、不算开孔与加强筋 |
| `vibration_plate_stress` | 振型 → 表面应力：同一模态在多个多项式阶上各解一次，**误差棒是量出来的**（`degree_sensitivity_pct`）；von Mises 与最大主应力**各报各的点** | `amplitude_mm` **必填**（振型没有幅值）；点支承下阶梯塌成一档 ⇒ `converged=None`（没有误差棒，而不是误差棒很小） |
| `vibration_random_response` | 随机振动：Miles 等效（平坦闭式 + 实测谱积分互证）→ g_rms / 位移 rms | 单模态；非平坦谱逐段积分只在足迹内，谱外不计 |
| `vibration_fatigue` | Steinberg 三带（3σ/2σ/1σ）+ Miner 线性累积 → 循环数与小时寿命 | `z_allow_mm` **必填**（不内置「典型允许挠度」） |
| `vibration_shock` | 半正弦冲击：SDOF Newmark（β=1/4、γ=1/2）→ 峰值位移/加速度、DLF | 单自由度；不算应力、不算二次冲击与接触碰撞 |
| `vibration_isolator` | 隔振：传递率曲线、隔振起点 `f_n√2`、r=1 处 `Q√(1+1/Q²)` | 高频按 **1/r** 掉（分子含 (2ζr)²），不是 1/r²；不算隔振器非线性与蠕变 |
| `vibration_shock_spectrum` | 冲击响应谱：一串单自由度振子逐频算峰值 |ZPA|、伪速度 2πf·z、峰值位移；对数扫频 | 默认 **5 % 阻尼是 SRS 惯例**并明示（不是你的板子的属性）；每条曲线自检「阶跃+近零阻尼恰为 2 倍」与「不超过 2×输入峰值」；SRS 本身不判失效 |
| `solder_joint_fatigue` | 焊点热疲劳：`Δγ = F·DNP·Δα·ΔT/h` → Engelmaier `Nf = 0.5(Δγ/2ε_f)^(1/c)`、多环境 Miner、试验↔使用 Norris-Landzberg 加速因子 | `joint_height_mm` 必填（是分母）；ε_f **是拟合**（默认 0.325，SAC305 文献散布 0.2~0.35）；CTE 预设是典型值、FR-4 过 Tg 会从 16 跳到 ~60；不算 underfill/空洞/IMC/裂纹扩展 |
| `drop_board_strain` | 跌落板面应变：一阶模态 → SDOF 冲击响应 → 曲率 → 应变，三段各自可复核；给 `dnp_mm`+`joint_height_mm` 再加焊点剪切应变 `ε·DNP/h` | 单模态、**不算器件质量**（f1 偏高 ⇒ 响应偏乐观）；`critical_strain_ue` 只能调用方给，不给就 `judged:false`（**没有 verdict**）；线性板理论，超过 h/2 高估 |
| `board_vibration` | 按板上真实板框（bbox）+ 叠层厚度算 f₁/周期/热点，热点标 `near_antinode` | 派生量 `authoritative:false`；非矩形轮廓回 `outline_fill_fraction_of_bbox` 并说 f₁ 偏低 |
| `cavity_resonance` | 平面腔体模 `f_mn = c/(2√εr)·√((m/a)²+(n/b)²)` + 每个模的 λ_g | **只给模频不给 Q**：无介损/铜损/辐射、无开放边缘失谐，所以是频率轴上的位置不是判决 |
| `cavity_via_fence` | 过孔围栏：`pitch/λ_g`、每间距电长度、给定判据后的 `pass/fail` | **判据必须由调用方给**（λ_g/20 与 λ_g/10 差 2 倍，都不内置）；不给就 `indeterminate` 且不反推间距 |
| `electrothermal_trace` | 走线自洽温升：R(T) → I²R → ΔT → R(T) 的定点迭代 + 精确失控判据 `dP/dT·Rth < 1` | `rth_c_per_w` **必填**；G≥1 回 `THERMAL_RUNAWAY` 而不是最后一次迭代；只算直流 |
| `electrothermal_conductor` | 同一环，R₂₀ 由调用方给（过孔桶壁 / 分段串联 / 分流器 / 针脚） | 回 R_hot、电阻增幅、迭代次数、稳定性余量 `1−G`；Rth 仍是调用方的 |
| `board_electrothermal` | 板级 **2-D** 电热：均匀网格 + SOR，`rho(T)` 线性 ⇒ `alpha*T` 落在矩阵对角线上，稳态是**严格线性**的一次求解；瞬态后向 Euler；失控在**求解前**按对角判 | `h_w_m2k` **必填**；只铺介质 k，不穿真实铺铜/热过孔；DC，无趋肤/邻近损耗；不替你开 `.kicad_pcb` |
| `filter_prototype` | 原型 g 值（Chebyshev / Butterworth），滤波器设计的起点 | Butterworth 传非零 ripple 直接 `ARG_CONFLICT`；order ∉ [1,20] 拒 |
| `filter_lumped_bandpass` | LP→BP：每段 L/C + 两路 S21（ABCD 级联 vs 闭式 `1/(1+ε²Tn²)`）+ 可选有限 Q 与一阶寄生（L 并 cp / C 串 esl）+ 功率审计 | 两路差 >0.02 dB **拒答**（`INTERNAL_CHECK_FAILED`）；偶数阶不是 50Ω 网络——串联起始 + `1:√g(n+1)` 理想变压器，写在 `value.termination`；损耗是每段一只电阻（`ω0L/Q`、`ω0C/G`），不是介质/导体物理：无趋肤、无介质损耗、无温漂 |
| `filter_mc_yield` | 容差良率：每个 L/C 各自独立抽样，跑同一条 ABCD 级联，带二项 95 % 半宽 | 只有 uniform/gaussian **独立**分布（无相关性、无批次偏移、无老化）⇒ 最坏情况；零容差报**退化**（构造上 100 %）；标称已不达标时明说，不给容差良率 |
| `filter_coupled_microstrip` | 耦合微带反解 w/s：截面解拉普拉斯（奇模电壁/偶模磁壁）+ Broyden 反解 | 准静态无色散；k<0.02 拒；k 误差 >3% 带 `k_resolution_note`（k 是两个相近数之差） |
| `filter_coupled_resonator` | 边缘耦合半波谐振器带通：Qe/k_ij/λ_g−2Δl + 每段 `meets_target` | 不建模交叉耦合（因此没有带外零点）；不给馈电间隙几何 |
| `filter_waveguide_cavity` | 矩形腔 TE101：长度、Rs、Qc（闭式 vs 六面数值积分）、R/Q | 两路 Qc 差 >2% 拒答；低于 TE10 截止拒；只做 TE101（iris 耦合必须全波） |
| `filter_coupling_matrix` | 耦合矩阵当**真网络**算：交叉耦合的有限传输零点 + 有限无载 Q 的插损与零点深度；每个耦合贴成理想导纳逆变器的精确 Π 等效，节点法解 S 参数（矩阵只供代数，不倒它求 S） | 四条自检不过就**拒答**：无损 inline 必须复现 `1/(1+ε²Tn²)` <1e-6 dB、无损幺正性 <1e-9、三阶零点必须落在 `Ω_z=m12²/m13`、纹波幅度对原型 g 值 <1e-3 dB；trisection 是**对 inline 矩阵的微扰**，不是 Cameron 合成 ⇒ `ripple_actual_db` 是「你得到的」不是「你要的」（`couplings` 参数可传你自己合成的矩阵）；不生成版图、不做 EM、不建模色散 |

`via_current_allowed` 实算 0.3mm 孔 / 25µm 镀铜 → 1.795 A，高于工程经验值 1A；
返回里同时给出 `rule_of_thumb_gap`，请按保守者取用。

## 用法

零外部依赖，Python ≥ 3.9。

```bash
python run_server.py --version       # 0.0.32
python run_server.py --selftest      # 协议级自检，124/124（含板与原理图写路径端到端、天线四问、仿真七问、DFM 三问、振动六问、腔体与电热七问、板级应力二问、平面模态二问、滤波器三问、安规五问、馈线差分对三问、子电路展开三问、行为源四问、无损线二问、有损线与 URC 二问、噪声二问）
python -m unittest discover -s tests # 1785 个用例（约 10.7 分钟，kicad-cli / pcbnew 与二维场解用例占大头）
python run_server.py                 # 起 stdio MCP 服务
```

MCP 客户端配置（stdio）：

```json
{
  "mcpServers": {
    "pcb-agent": {
      "command": "python",
      "args": ["D:/工作/git_project/pcb_agent_mcp/run_server.py"]
    }
  }
}
```

协议实现是 stdlib 手写的 JSON-RPC 2.0 换行分隔流（`2025-06-18`，向后兼容 `2025-03-26` / `2024-11-05`），
`dependencies = []`。日志只写 stderr —— stdout 承载协议。

## 路线图

- **V0** ✅ Calc 层 + `calc_capabilities` 自述。
- **V0.5** ✅ 双 Adapter 只读：KiCad `.kicad_pcb` 与立创 `.epro` 都解析到同一 Canonical，
  单位与轴向只在 adapter 边界归一化一次（Canonical 内只有 mm + Y 向上）。
- **V1** ✅ Oracle 只读投影 + Desktop 层（探测/启动/关闭/开工程，按窗口标题确认）。
- **V1.5** ✅ 保真层：整份文档进树（token 双射可证）、事实从树里读（每条带 `raw`）、
  厂商裁判（`kicad-cli` 的 DRC/stats/Gerbers/pos）。几何派生答案统一标 `authoritative: false`。
- **V2** ✅ KiCad 写路径：`propose → apply`，五道闸门（字节 / token 双射 / 后置条件 /
  kicad-cli 比对 / DRC 差集），checkpoint 与回滚；`examples/build_demo_board.py` 自造一块
  60×40 四层板并用 kicad-cli 判决。
- **V3** ✅ 立创写路径：**原地重写一个成员**（实测：同一工程两次保存只改一个 `.epcb`，129 个成员
  字节全同）、六道闸门（写盘字节 / 其他成员 / 未点名行 / 流可解析 / 改动是计划的 / 重新解析后的
  事实）。单位实测为 **恒 mil**（标签只是显示单位），不做任何换算；SQLite 格式的 `.eprj2` 只读拒写。
  六条 op：`move_component` / `set_track` / `set_via` / `set_text` / `set_pad_net` / `delete_record`。

双栈必须同时在场：**单端开发永远发现不了适配层设计错了。**

## 已知未实现 / 已打折扣的地方（诚实清单）

- **两侧都对着厂商自己核过了，但"核过"的边界要说清。** 立创的存储形式、单位、记录布局、保存行为、
  焊盘网络行在 194 个真实 `.epro` 归档上核过（[docs/13](docs/13-立创实测-真实存储与记录格式.md)）；
  KiCad 侧在 13 块官方 demo 板上与 `kicad-cli 10.0.7` 的 DRC 双向对账（厂商报的 56 条可比违规
  我们一条不漏，recall 1.0），焊盘几何另与 `pcbnew` 逐点对账（412 pad / 20188 次 HitTest / 0 不一致），
  见 [docs/15](docs/15-对账-推导对着厂商判决.md)。剩下的边界是采样而不是方法：13 块板覆盖不到的
  少见语法仍可能没走到。
- **合成样本曾经连 KiCad 都不认。** 保真层的第一个产出就是抓到这件事（`(dnp)` 裸写等三处非法语法）。
  现在 `tests/data/sample.kicad_pcb` 是 v7 合法文件，且每轮被 `kicad-cli` 验证。
- **推导仍然存在，但已经对过账，而且账上明写哪几笔比不了。** `board_clearance` /
  `free_space` / `pour_connectivity` / `cross_split` / `loop_area` 都是本服务自己算的几何量，答案里带
  `authoritative: false` 和 `verdict_available_from: board_drc`——测量不是判决。能对账的那一项
  （间距）由 `board_cross_check` 双向数：13 块真实板上厂商报的 56 条全部命中，我们多报 91 对且都在
  保守侧（会让人把线放宽，不会做窄）。比不了的四项回 `not_comparable` 并说理由；三类几何
  **明说不建模**而不是算成漏报：铺铜（zone）铜、板边与孔规则（`copper_edge_clearance` /
  `annular_width` / `drill_out_of_range`）、阻焊与钢网（`solder_mask_bridge`）。另有"测不出"单独
  计数：差距与规则相差 <0.002 mm 的对（CM5 33 对、video 30 对、StickHub 20 对、tinytapeout 10 对）
  不进 recall 也不进 extra，判决只由厂商给。实测上限也测出来了：88.7 MB 的 jetson 板 DRC 两次都
  没出报告（823 s / 2543.8 s `ENGINE_FAILED`），所以默认超时 900 s，超时即报错、不降级。
- **立创侧还剩两处非实测。** ① 元件焊盘位置＝库封装 + 旋转/镜像变换推导（54.1% 铜端点命中率，
  见 docs/13 §6，底面镜像只有弱证据 35.1%）；② `set_pad_net` 追加一行 `PAD_NET` 时，第 5 字段
  按「封装文档里该焊盘号的每个 PAD 对象各写一行」——8159/8159 指向同号对象是实测量，但**从未观察到
  应用自己写这一行**，所以结果标 `verified_by_app: false`。
- **SQLite 格式（`.eprj2`）只读。** 能打开、能算度量，但写路径直接拒绝（`WRITE_UNSUPPORTED_FORMAT`）：
  `history_data` 加密、`ticket` 单调，任何手改都可能让工程打不开。
- **原理图：KiCad 能写，立创不写。** `.kicad_sch` 有 18 个 op、四道闸门，裁判换成 KiCad 的
  网表 + ERC（[docs/14](docs/14-原理图写路径.md)）——DRC 判铜，原理图的产品是连通性，两套闸门不能照搬。
  **立创 `.esch` 明确拒写**（`WRITE_UNSUPPORTED_FORMAT`，并按内容说出它是 zip 还是 SQLite），
  因为它的记录布局还没在真实保存上测过；这一条不是"以后做"，是"现在拒"。
- **原理图只写单张图纸。** 本层不做跨页改动（改 `sheet` 引脚、改 `Sheetfile`）——那会牵动父图
  的实例路径与页码，和"改一张图"不是一个风险等级。
- **两个闸门 bug 是被真图抓出来的**（docs/14 §5）：元件值改动曾被报成"网表变了"；同一条 ERC
  违规被 KiCad 重新指向不同 item 时曾被算成"新增+解决"。前者会把所有正常字段编辑拦死，
  后者会让人很快学会一按 `allow_new_violations` 了事。
- **立创 Pro 有两条活通路，只有一条真的通过。** websocket 网关那条（`lceda_api_probe`，RFC 6455
  握手/掩码/分片/超时，`tests/test_lceda_api.py` 10 例）本机 49620-49629 全部 `no_listener`——它
  只在用户手动打开「外部交互权限」后才监听，所以那条仍带 `verified_against_app: false`。真正跑通的
  是**应用自己开的命令空间管道**：`lceda_pro_*` 七个工具（第七个 `lceda_pro_steps` 是分步写，**真机未验证**）对着运行中的 4.1.60 实测过 ping、open
  （32.5 MB 副本 849 ms）、session.list、session.close、doc.api（`eda` 378 个类型、`PCB_Document`
  21 个方法、带厂商中文注释）与 invoke（`await eda.dmt_Pcb.getAllPcbsInfo()` 回两块板的名字与
  uuid，抛错回 `EXECUTION_ERROR` + 应用原话）。协议与实测数字见 [docs/24](docs/24-立创进程内桥.md)。
  **写路径到 0.0.16 才有形状，而且形状是"纪律"不是"配方"**：新工具 `lceda_pcb_write` 不生成
  snippet（vendor 的写方法名本机一步都没验过，生成就是 plausible wrong answer），它只做
  「写前 probe → 你的 snippet → 写后 probe → 比较」：没有 probe 就**不发**（`ARG_REQUIRED`）、
  前后一致就报 `WRITE_HAD_NO_EFFECT`、`save` 只能回 `expect=no_error`。每条答案
  `verified_against_app: false`，`not_verified` 里明写"磁盘上的文件真的变了吗 / 重载后还在吗"。
  `lceda_pro_run` 仍然在 assumptions 里写"片段自己能写、本工具不担保"。
  **0.0.17 把阻塞钉清了：不是方法名不知道，是没有文档上下文。** 桥本身全通（ping / invoke /
  `doc.api` 都对），但同时刻 `tabs` 只有 Start Page、`getAllProjectsUuid()` 空、`getAllPcbsInfo()`
  `null`、`openDocument(...)` 回 `null`、`openProject(...)` 30 s 超时——一切写方法都没有作用对象。
  唯一绕得开的 `createPcb()` 会往用户在线工作区里塞一块无名 PCB，**故意不做**。
- **立创的"启动"曾经被判死，第三轮推翻了——差别只在父进程有没有控制台。** 前两轮记的是
  "0.5 s 退出、死在 Electron 之前"，那是**从 bash（继承控制台）启动**的现象；改成
  `explorer.exe "<exe>"`（等价双击）就稳定在三个进程上（203/36/289 MB），管道随即出现，
  于是才有了上面那条进程内桥（[docs/10](docs/10-桌面控制与双栈读路径.md) §1.1.3）。
  argv 形式（`lceda-pro.exe <工程.epro>`）**仍然没有确认**，`open_argv_verified` 保持
  `false`，`app_open_project` 只会回 `unconfirmed`；要开工程请用 `lceda_pro_open`。
  顺带查清：`--` 开头的选项（含厂商自带的 `--mcp stdio|http`）是被 **exe 里 node 自带的
  选项解析器**以 rc 9 拒掉的（`bad option: --mcp`），跟立创启动器无关。
- **布线器的四条边界，0.0.17 之后是这样。** ① 拥塞协商（`congestion_rounds`）**默认关闭**：
  在 4 层 50 网 F401 核心板上 38/50 → 41/50，代价是 61% 的时间，而且那是**一块板**的读数，
  不是对所有板的承诺；默认开会替所有用户花掉这 61%。② 回流路径（换层配地过孔）在真板上
  **74 颗信号过孔里缝了 11 颗**，其余 63 颗被 `ground_not_on_two_layers` 拒（那几处地只在一侧有铜）；
  apply 后 `kicad-cli pcb drc` 的 `new_blocking_total = 0`，但新增 advisory 里有一条 `via_dangling`
  ——布通报 41 网不等于 41 网的铜都接上了，所以这一条的结论只能读到「不新增阻断项」。
  ③ 铺铜：**能报过期，也能重填**（`refill_pours=true`，**默认 false**）。重填委派 KiCad 自己的
  `ZONE_FILLER`，再以字节保真方式把多边形移植回我们的文件；实测移植回来的铜与
  `kicad-cli pcb drc --refill-zones --save-board` 填出来的**顶点集合完全相同**（1 个多边形 /
  252 顶点 / 面积 1023.912 mm²）。但同一份铜在我们的 pre-v7 版式文件里会被 DRC 量出 3 条
  `间距 0.3500 / 实际 0.2255` —— 那是 `min_thickness/2` 的代际差（pre-v7 把 `filled_polygon`
  读作描边中心线），工具把它写进 `assumptions.format_generation` 并给出三条 remedy，
  **不偷偷改几何、也不偷偷升 version**（docs/27 §27.13.3）。
  ④ 多端网拓扑：`topology="steiner"`（iterated 1-Steiner on Hanan grid）确实省长度
  （经典星形 8.0 → 6.0 mm、五点 37.0711 → 30.0 mm），但**默认仍是 `mst`**，每个分支点要多跑一次 A*，
  而且它报的是 Manhattan 树干长度、不是布完之后实际的铜长，也**不是精确 RSMT**。
- **差分阻抗有两级：闭式近似与二维场解，两者差多少是实测的。** `diff_impedance` 仍是边耦合闭式（IPC-2141A 族），`diff_impedance_field` 解真实截面上的 div(ε∇V)=0（有限体积 + 梯度张量网格 + 红黑 SOR，纯 stdlib）。闭式在实测几何上系统性**偏低 8~11%**、方向一致，所以「调宽凑数」会把线先做窄。场解自己的误差对着两个共享零近似的参考答案量过：零厚度带状线对精确共形解（w/b 0.5..4）≤1.2%、孤立微带对 Hammerstad ≤2.8%、耦合通路在 s/h=8 处对 2×孤立精确解 ≤2.7%；这三张表由 `tests/test_field2d.py` 每次重算，不会悄悄过期。**没被独立参考答案覆盖的是紧间距耦合**——那种几何下工具自己吐 `reference_note`，说明「这是求解器的意见，不是被核过的数」。两者都不建模阻焊、蚀刻梯形与铜损/色散。
- **两张读不到原文的表，改走两条不需要原文的路。** 标准是付费的、公开转述互相矛盾（IPC-2152
  与 IPC-2221 差可 >3×），所以不自已抄表：
  - **IPC-2152** 抄 KiCad 10.0 `track_width_calculations.cpp` 里已经公开发表的 **Brooks & Adam 2015
    闭式拟合**（`ΔT = K·I^a·W^b·Th^c`，内外层各四个铜厚档），用三个独立锚点复现（1 A/10 °C/1 oz
    外层 = 0.2773 mm、同条件内层 = 0.2484 mm、IPC-2221 外层 = 0.3021 mm）+ 反向 `current_capacity`
    的 0.951 A 做三线校验；`data/ipc2152_tables.json` 里 `points` 仍是空的，但 `why_empty` 现在说明的是
    “不是表模型，是闭式模型，凭空造点才是不诚实”。
  - **IEC 60664-1:2020-05** 不抄表，**编译 KiCad 10.0 的 `iec60664.cpp` 原实现、用探针把每一道阶梯
    反演出来、再拿全网格对拍**：三套探针共 **188 528 点，0 失配**（grid 3 760 / sweep 93 120 / raw 91 648），
    阶梯 32 键 / 808 步落盘在 `data/iec60664_tables.json`，`verified: true`。
  - 剩下的安规族（IEC 62368-1 / 61010-1 / UL 60950 / IPC-2221）仍**一律 fail-closed**，报 `SAFETY_TABLE_MISSING`。
  - 这个过程中最贵的一条教训：**断点必须每次重新反演，不能从上版 JSON 继承**——自引用会让整段阶梯
    静默消失，端口在那些区间返回下一档（偏大）。见 [docs/23](docs/23-两张读不到原文的表.md)。
  - `calc/safety_table.py` 原有的“表能不能信”规则判据（来源 ≥2、单调、PD1≤PD2≤PD3、无重复 key，
    `tests/test_safety_table.py` 17 例逐条反证）保留；IEC 60664-1 另走 8 条专属检查 + 9 条变异用例。
    **填一个猜的值比拒答更糟。**

### 天线层：不做全波，这一点写在每个返回里

- **没有全波电磁求解器，也没有假装有。** 21 个天线类工具全是闭式模型（Balanis / Hammerstad /
  Chu-Harrington / Kraus / Friis），能给出**第一个能打样的几何**与一阶阻抗/带宽/硬上限；
  方向图、外壳与手持失谐、同板双天线互耦、SAR、匹配后的实际带宽一律不计算，写进每个信封的
  `not_computed`，并被 `--selftest` 的 id 71 检查。换求解器（HFSS/CST/openEMS/nec2）的门槛写在
  [docs/16 §12](docs/16-天线与馈线.md)。
- **印刷线的长度是区间，不是单值。** 薄导体在基材上的 εeff 落在 `1` 与 `(er+1)/2` 之间，
  没有可信闭式 ⇒ 给两端 + 起点建议（薄膜基材取自由空间端），并附 Chu 上限说明"装得下 ≠ 装得好"。
- **CPW 不建模铜厚**，且直接说出来（"requested X µm but NOT modelled"），而不是套一个可能大 2 倍的
  Gupta 修正；CPW 导体损耗回 `null` + 原因，只给介质损耗并标 `lower bound`。
- **每个匹配解都回代重算残差。** 这条纪律抓到了一个真缺陷：`l_match` 的 series-first 分支符号写反，
  会把 25 Ω 纯阻"匹配"成 10+j20（残差 VSWR 0.707）。只报元件值的话它是一组安静的错数。
- **板级净空把 zone 单独列。** 天线净空违规在 DRC 里只报成"间距"，而真正杀死天线的是铺铜——
  地平面压到天线下面会把谐振频率拉低，DRC 完全无视。`board_antenna_keepout` 因此把
  pad/track/via 与 zone 分开计数（StickHub 上 `(150,-100,8,6)` 实测 406 项铜 + 2 个 zone）。

### 仿真层：全是子集，边界写在每个返回里

- **SPICE 只做子集，而且是按名字拒绝的。** `sim_spice` 真解非线性方程（MNA + 牛顿 + pnjlim +
  gmin 向下步进），器件卡只认 `R C L V I E G D Q M` 加行为源 `B`（编译成树，不是 `eval`）；`F H K S W T O U A` 九类一律
  `UNSUPPORTED_CARD` 并点名，而不是跳过——**一半跑完的网表比拒答更危险**。瞬态是后向欧拉
  （不是梯形），AC 先解工作点再只覆盖雅可比。`not_computed` 每一条都写清：没有自热、没有器件
  电容/迁移率/击穿随温度、没有噪声/失真/傅里叶分析。
- **`.param` 是整份 deck 的语言，不是子电路的私产。** `0.0.28` 起顶层与 `.subckt` 体内都能写
  `.param`：只有一个数字读取器（数字或 `{+ - * / **}` 的 ast 白名单），作用域链是
  **全局 → 子电路默认值 → 实例覆盖 → 体内 `.param`**，前向引用合法（按依赖轮次解析），
  循环定义按名字报错。**本轮抓到一个真缺陷**：`_value("1k*2")` 会静默读成 **1000**（`*2` 被当垃圾
  吃掉），所以 `.param` 与 `{…}` 位置现在要求整个 token 匹配数字+单位，错误里直接写出
  `write {1k*2}`。源的 `dc` / `ac` / `pulse(...)` 三个数字槽也统一走这个读取器（此前各写一遍，
  `0.0.26` 的间距参数正是这样掉过一次）。
- **`B` 源也是内置的，表达式在解析期编成一棵树。** `0.0.29` 起 `B1 n+ n- V=expr` 由内置
  求解器自己算：右侧先翻译 SPICE 拼写（`&&`/`||`/`!`/`<>`/`^`、`1k` 后缀、`{}` 分组、
  `? :` 重排），再过 `ast` 白名单变成元组树，求解期只对树求值。于是**语法错带行号报在
  解析期**，而不是伪装成「牛顿不收敛」；`1/0` 是 `EXPRESSION_INVALID` 而**不是**
  `NOT_CONVERGED`（前者改算术，后者查拓扑）。节点引用按**源码文本**取——`V(1)`、
  `V(n-1)`、`V(in)` 在 Python 里分别是数字、减法、关键字，三种都曾被静默读错；参数在
  构建期折成数字，不在作用域就报 `'q' is not a parameter in scope here`；AC 按偏置点
  线性化（`0.001*V(1)^2` 在 2 V 处增益 0.004，与 ngspice 差 < 1e-6）。`laplace()`、
  `I(Vname)`、`ddt`/`idt`、B 源噪声按名拒绝。**ngspice-47 没有 `if()`**，所以对拍用例
  一律用 `? :`——不同方言的差异不是在量数值。`V=` 与 `I=` 同时给按卡面歧义拒绝。同一轮还抓出一个与 B 无关的真缺陷：牛顿
  残差判据用全局尺度，4000 V 节点把门槛抬到 4 mV，1 kΩ 支路上 1 mA 的误差被判收敛
  （节点回来 −3998.999996），改成按行缩放后收敛到 4000.000 / 2000.000。
- **无损传输线 `T` 也是内置的，一条延迟在三个分析里各有一套解析式子。** `0.0.30` 起
  `T1 n1 n2 n3 n4 Z0=<ohm> TD=<s>`（或 `F=`/`NL=` 给同一个延迟）由内置自己算：**DC 是导线**
  （一个支路未知量 + 一行 `v1-v2-v3+v4=0`，`i2 = -i1` 由构造给出；按「每端口一个未知量」写会在
  `T1 2 0 3 0` 这种两端口共地的单端画法上让第二行退化成 `0 = 0`、矩阵奇异）、**AC 是精确 Y 参数**
  （`-jY0·cot θ` / `+jY0·csc θ`，终端接 `Z0` 时输入恰好呈现 `Z0`、相位恰好 `-ωTD`）、
  **瞬态是特征线法**（每个端口 `1/Z0` 电导 + 一个延迟前那一端的电流源，匹配阶跃、开路端翻倍、
  短路端反射系数 −1 都实测）。半波点（`θ = kπ`）导纳无穷，**返回 `SINGULAR` 而不是巨大的数**——
  巨大数看起来像答案；`TD` 缺失或为 0 拒绝（ngspice 会静默当导线）、`TD < tstep` 拒绝
  （钳到一步会把每次反射整体挪一步，第一版实测晚约 2 步）。同一轮修掉一个真缺陷：`PULSE` 的宽度
  缺省原来是 0，`PULSE(0 3 0 1p)` 变成零宽尖峰——SPICE 的缺省是 `tstop`（有损线见下一条）。
- **有损线 `O` 与分布 RC 线 `U` 接上了，但接的方式是把线换成一串 R/L/C。** `0.0.31` 起
  `Oname n1 n2 n3 n4 m`（`LTRA` 模型）与 `Uname n1 n2 n3 m`（`URC` 模型）在**解析期**展开成
  `lump` 段普通 R/L/C，求解器见到的是那串梯形——方程层一行没改。所以每一步都写清楚这件事：
  每个答案报 `segments` 与 `segments_from`，默认段数（20）进 `defaulted_model_params`，
  `not_computed` 里写明皮肤效应、介质损耗、频变 `r`/`l` 都不在模型里。**DC 是精确的**：
  没有 `g` 时梯形就是电阻链，1 Ω 在 3 V/50 Ω 分压里读 `1.514851/1.485149`（`lump=1/2/8/20/50`
  逐位相同，与 ngspice 的 `LTRA` 一致到 1e-6）；**AC/瞬态收敛到分布解**：`g=1` 的 DC 误差
  `lump=1→100` 从 −1.4e-1 单调降到 −2.4e-3，无耗梯形与 `T` 在三频点幅值一致、相位差 < 0.02°，
  `r=1` 时 1 MHz 正是 `0.5·e^-0.01 = 0.4950`（与 ngspice 差 1.6e-7 V），`U` 与解析
  `Z0c·coth(γL)` 差 1.1e-3。`lump=` **只许写在 `.model`**：写在卡上对 ngspice 是 FATAL，
  写在 `.model` 只是 warning——同一份 deck 要两边都能跑就只能这样。`LEN` 必须给（ngspice 也
  拒绝没有长度的 `LTRA`）；两个不同的返回节点拒绝（梯形把返回当理想单节点，那意味着返回导体
  自己有延迟，本梯形表达不了）。同一轮抓出一个真缺陷：`.ac` 里没有 `AC` 关键字的源原来被当成
  1 V 激励（内置满幅、ngspice 全是 0），改成 0 并在整份 deck 无人驱动时告警。
- **`.noise` 也是内置的，用的是伴随法（一次转置求解覆盖全部噪声源）。** `0.0.32` 起
  `.noise V(out[,ref]) <源> dec|oct|lin n fstart fstop` 由内置自己算：每个频点解一次
  `Aᵀy = e_out`，第 `k` 个源在输出端的传函就是 `y[a]-y[b]`，于是「N 个源 = N 次求解」变成一次。
  四条机制都按物理式写死：`R` 是 `4kT/R` 热噪声、`D` 是 `2q|Id|` 散粒、`Q` 是集电极与基极
  两条 `2q|Ic|`/`2q|Ib|`、`M` 是 `4kT·(2/3)·gm` 沟道热噪声（`gm` 由数值雅可比取）。对账是
  逐位的：单只 1 kΩ 读 `4.071372237e-09 = sqrt(4kT·1000)`，两只并联读 `sqrt(4kT·500)`，
  四只串联恰好 2 倍；RC 低通逐点对解析式相对差 3e-9，带内积分 `6.424132021e-08` 对
  `arctan` 闭式只差 −0.000909 %；ngspice-47 对拍分压/RC/MOS 是 1e-7 量级、二极管 0.20 %、
  BJT 0.29 %。**积分规则跟着网格走**：`dec`/`oct` 用 `ln f` 梯形、`lin` 用 `f` 梯形，因为
  带宽就是扫描段本身——`lin 1000` 用 `f` 梯形仍 +47 %（少了 1 Hz–1 kHz 那段），所以答案里
  `method`/`note` 直接把规则说出来，而不是假装与网格无关。输入折算用「单位激励 + |gain|」，
  所以内置**不**要求源带 `AC` 关键字（ngspice 会因此中止，这条差异写在 docs/38 §38.2）；
  `inoise_total` 也不与 ngspice 比（逐点除与积分÷单增益在 RC 上是 12 % 的口径差）。
  不做：`1/f`、burst、induced gate、`kf/af` 参数、`V(...)` 单点噪声——一律按名拒绝或写进
  `not_computed`。同一轮修掉一个真缺陷：MOSFET 的 `W=`/`L=`/`M=` 之前被静默丢弃，现在进
  `wl` 并让 `W=20u` 的噪声严格 ×2。
- **`.subckt` 归内置管了，而且是拍平不是新求解器。** `0.0.27` 起内置 `sim_spice` 自己展开
  `.subckt/.ends` 与 `X` 实例：内部节点名照抄 ngspice 的 `<实例>.<本地>` 小写（嵌套
  `x9.x1.m`），所以 `cross_check` 的节点配对是真的——不然「0 个共同节点」会被读成「一致」。
  参数在调用者作用域求值（`{+ - * / **}` 的 `ast` 白名单，函数与节点引用按名字拒绝），
  子电路内 `.model` 加实例前缀、全局模型不被复制，接地引脚塌成唯一全局地。自证不靠
  ngspice：同一电路手写平铺后逐节点比，`x1.m` 与平铺 `mid` 差 < 1e-12。边界写进
  `not_computed`（顶层 `.param`、表达式函数、嵌套 `.subckt`）。浮空岛两引擎分歧
  （内置 0 V 对 ngspice 2.5 V，`gmin` 约定不同）**登记而不是修掉**——手写平铺 deck 上
  分歧幅度逐位相同（2.501057919），证明它与层次无关。
- **"只做子集"不是懒，是零依赖约束下的唯一诚实解。** 完整 SPICE（ngspice 约 50 万行 C，
  ahkab/PySpice 要 numpy/scipy）没有"抄进 stdlib 就能用"的形态，所以 0.0.7 加的是**可选委派**：
  `engine="auto"` 在 PATH / `PCB_AGENT_NGSPICE` 找到 ngspice 就把 deck 原样交给它（只过滤分析卡），
  找不到则用内置子集并在 `engine_used` / `engine_note` 里说明；`engine="ngspice"` 找不到是
  `ENGINE_NOT_FOUND` 报错，**绝不静默回退**（level-1 MOSFET 的答案冒充不了 BSIM）。
  `cross_check=True` 跑两个引擎比节点电压——这是唯一能审计这条委派本身的手段。本机**已装
  ngspice-47**（MSYS2 构建解包到 `%LOCALAPPDATA%\ngspice-47\`，外部二进制不是仓库依赖）并对着
  金标网表实测：分压 5e-9 V、二极管 0.35 mV、BJT 17.8 mV（模型差异）、RC 阶跃与 RLC AC 逐位相同，
  所以 `verified_against_engine` **按版本**给 true（`VERIFIED_ENGINES`，别的版本回落 false）。
  装了引擎才拿到的能力是**未解析 deck 直通**：`F`、`H`、`K`、`S`、`W`、`A` 等内置按名字拒绝的卡在（`.subckt/X` 自 `0.0.27` 起内置自己拍平就能跑、`B` 自 `0.0.29`、**无损线 `T`** 自 `0.0.30`、**有损线 `O` 与 URC `U`** 自 `0.0.31`、**噪声分析 `.noise`** 自 `0.0.32`，都不再需要引擎）
  `engine="ngspice"` 下照跑，节点表从 rawfile 读，信封带 `netlist_parsed:false` 而**不假装**
  这是一次完整解析；同一 deck 走 `builtin` 仍然拒绝。`simulate()` 默认引擎是 `builtin`
  （`engine="auto"` 才用引擎）——默认值不能让同一网表在两台机器给出不同答案。
- **AC 截断会出声。** 扫频点数超过 `max_points` 时返回 `warnings`：只解了前 N 点、末点不是 `fstop`。
  一个还在上升的曲线被静默截断，会被读成"电路在这里谐振"。
- **温度必须给符号正确的答案。** IS 按 SPICE 实现随温度缩放（XTI/EG/TNOM）：27 ℃ 二极管 0.6925 V，
  100 ℃ 0.5709 V，−40 ℃ 0.8004 V。只让 Vt 随温度涨会让"高温压降更大"——那是安静的错答案。
- **热学不给表、不猜 h。** `thermal_heatsink` 只反解预算，不发散热片库；`thermal_convection`
  是工程关联式（±25%）并明说它只能用来比两种假设；2-D 板方程解完要过能量平衡自检（0.5%），
  不平就报错，不报告。
- **PDN 判最坏 |Z|，判不了就说判不了。** 只有"明显不够"才 `fail`；最坏点落在判定频段下沿时
  回 `indeterminate`——低频端电容不是短路，是稳压器在维持。平面谐振、平面电容的准确取值都
  未建模。
- **板级 IR drop 用板上真实铜，不用长度乘方阻。** 节点是 `(x, y, layer)`，两层只在 via 或
  镀通孔焊盘处相连；铜厚取自叠层（`1 oz`、`0.5 oz`），没写就拒绝，不默认 35 µm。

### DFM 层：没有表，只有规则（[docs/18](docs/18-DFM-工艺能力校验.md)）

- **不内置任何板厂工艺能力表。** 规则限值来自用户给的**能力文档**，服务器只提供目录（11 条规则键：
  最小线宽 / 最小间距 / 最小过孔钻孔与环宽 / 最小 PTH 环宽 / 孔到孔 / 铜到板边 / 最小槽宽 /
  最大板宽 / 最大板高 / 最大铜层数）与体检。填一个猜的线宽限值比拒答更糟——那会让人以为板厂
  已经答应了。
- **三处整份拒收，而不是逐条忽略。** 未知键（拼错的键会被读成「没这条要求」）、缺 `source`
  （说不出出处的限值不可追责）、空文档，一律 `CAPABILITY_*` 报错并回可用的键清单。
- **没写的规则不是 pass。** 文档里没声明的规则进 `missing_known_rules`；不适用（例如板上没有
  PTH 孔时的环宽）才 `not_applicable`；量不出来的进 `not_checked_by_design`——那 8 项
  （阻焊桥、丝印、阻抗签核、工艺公差、拼板/V-cut/基准点、镀通与非镀通槽、铜到成品孔壁、
  圆弧折线化）在静态文件里根本没有可比事实。
- **量不到就给下界，不冒充最小值。** 孔与孔、铜到板边用有界搜索窗口（默认 5 mm）内的桶网格取最小值；
  窗口里找不到时回 `lower_bound` 而不是 `measured`，两者在输出上分得开。
- **这一层抓出一个真缺陷。** `adapters/kicad_pcb.py` 曾把 `Edge.Cuts` 的第二个轮廓也当成板框，
  于是显式画出来的挖空（1 mm 铣槽）与板边分不开、`cutouts` 永远为空——槽宽这一条根本量不出来。
  改后按面积挑外轮廓、其余进 `cutouts`，28 个用例里有手算金标守护。

### 振动层：薄板、单模态、线性（[docs/19](docs/19-机械振动.md)）

- **不抄模态表，直接解特征值问题。** 一套 Rayleigh–Ritz（单轴单项式基 + 边界约束零空间 + 质量度量白化
  + Jacobi）覆盖 SS/CC/free/悬臂/四角点支撑与任意长宽比、任意 ν，少一个「查表查错」的错误源。
  精度是量出来的：SSSS 330.385 Hz、CCCC 602.382 Hz 与解析解**精确吻合**，free-free 方板 λ²=13.4687
  对 Leissa 13.489（0.15%），振型对着 `sin(π·0.05)=0.1564` 逐点核过。
- **三个只在写代码时才会犯的错，已经钉在文档里。** 应变能是二次型 ⇒ 刚度系数是 `D` 而不是 `0.5D`
  （写错会让所有频率恰好偏低 √2 倍）；二维装配混合项必须 `ICxw[i][k]*I1yw[j][l]`（y 轴转置，写错会在
  自由边界吐出 −92000 量级的负特征值）；Jacobi 的特征向量矩阵要转置（否则特征值精确、振型全错）。
- **四角点支撑不近似成四边简支。** 「四角支撑」当成 SSSS 是给错答案（约束形状完全不同）；
  实现是 free×free 加 4 个角点 `w=0` 点约束，零空间在二维装配后再投影。
- **疲劳判据要用户给允许挠度。** `steinberg_fatigue` 的 `z_allow_mm` 必填，与 DFM「不内置板厂表」同一纪律：
  Steinberg 的 ±1.5 mm@2×10⁷ 是「引线类」这个类别的数，不是板上这颗器件的数据。
- **冲击只做单自由度。** 半正弦冲击走 Newmark（β=1/4、γ=1/2，无条件稳定），`dt ≤ min(T_n/24, 脉冲/60)`；
  阶跃在 ζ→0 时 DLF 实测 1.99953（解析 2），半正弦峰值对 `1+exp(−ζπ/√(1−ζ²))` 三档 ζ 全部 ≤0.13%。
  不算接触碰撞、不算多自由度追赶。
- **应力是同一组 Ritz 曲率的副产品，不是一个场解。** 0.0.10 起 `vibration_plate_stress`
  （与 `plate_modes(stress_amplitude_mm=…)`）报**三个分开定位的极值**：板角 von Mises
  （`vm=√3|τ|`，因为角上两个剪应力分量同时最大）、跨中最大主应力、板边最大剪应力；
  张量分量只在 `sigma_max` 点给出，**没有任何应力云图**，也没有孔边/加强筋的应力集中，
  更没有把这三个数再套一个疲劳律（Steinberg 的斜率与允许值仍要你给）。
  误差棒是 degree 阶梯的实测（K=5/7/9 时频率 0.04%、曲率 3.6%），点支承阶梯塌成一级时给 `None`。
- **`board_vibration` 用板框 bbox 而不是真实轮廓**，并明说方向：非矩形轮廓会**偏软**（f₁ 偏低），
  同时回 `outline_fill_fraction_of_bbox` 让人自己判程度。不算元件质量、不算安装点刚度、不算板内开孔。

### 平面谐振与电热耦合：一个只给模频，一个只给一个 Rth（[docs/20](docs/20-平面谐振与电热耦合.md)）

- **腔体模是闭式的，Q 不是。** `cavity_resonance` 给 `f_mn = c/(2√εr)·√((m/a)²+(n/b)²)` 与 λ_g，
  100×80 mm / εr=4.3 的 (1,0) 落在 0.722864 GHz（λ_g 恰为 2a，即半波的定义），(1,1) 严格等于
  √(f₁₀²+f₀₁²)，方板的简并对**分行列出**。没有 Q（介损、铜损、辐射）、没有开放边缘失谐修正
  ——真实平面与其边缘不是 PMC，所以这组数是教科书值，返回里写清"实物会有几个百分点失谐"。
  一个谐振没有 Q 就不是判决，`cavity_resonance` 因此**不判"这个模要不要紧"**。
  0.0.10 补了另一个工具而不是改这个数：`pdn_plane_modes` 从 `tanδ` 取 `Q=1/tanδ`，
  给每个带内模的**峰值** `|Z_peak| = w·μ0·t·ψ(r1)ψ(r2)·Q/k²`，回答"这个模比我的阻抗目标高多少"。
  **只给峰值不给曲线**：真做模态求和时 1.45 GHz 处截断 10 阶是 5.6 Ω、40 阶是 9.4 Ω，
  随阶数漂移不收敛，那数字只是"我选了几阶"的函数。函数里带电容锚点自检（`f=首模/100` 的求和
  必须回到 `1/(ωC_plane)` 的 1% 内），这条自检曾抓出自己 20 倍的归一化错。
- **过孔间距的判据故意不内置。** `λ_g/20` 与 `λ_g/10` 都在流传，差 2 倍，两者都不是推出来的；
  所以只给 `pitch_mm` 时回比例 + `indeterminate`，给了 `max_pitch_ratio` 才判 pass/fail，
  只给判据时回 `max_pitch_mm` 而**不反推一个间距**。测试里同一块板、同一个 15 mm 间距在 0.05 下
  fail、在 0.10 下 pass——这条用例就是"判据不能内置"的守门人。
- **热失控判据是精确的，不是"不收敛再说"。** 电热环 `ΔT(n+1) = A + G·ΔT(n)`，
  `G = I²·R₂₀·Rth·α = dP/dT·Rth`；`G ≥ 1` 时不动点根本不存在，直接回 `THERMAL_RUNAWAY`（带 G 与各输入），
  而不是把第 N 次迭代当温度。手算金标：R₂₀=0.01 Ω / I=1 A / Rth=50 K/W / α=0.00393 → ΔT=0.510829 K、
  R_hot=10.2166 mΩ，且 `P·Rth` 回代恰等于 ΔT。
- **Rth 必须由调用方给。** 本层不知道你的叠层、铜面积、过孔与风速；默认 Rth 会是这一层唯一一个
  不是推导出来的数，和 DFM 不内置板厂表是同一纪律。替身口 `electrothermal_conductor` 让这个环能套在
  任何已知 R₂₀ 的东西上（过孔桶壁、分段串联、分流器、针脚）。
- **那个集中 Rth 只属于两个小工具；板级另有 2-D 场。** 0.0.10 加 `board_electrothermal`，
  把 `k·t·∇²T − 2h(T−T_a) + q(T) + C·dT/dt = 0` 在均匀矩形网格上解（SOR，ω=1.85），
  关键是**把 q 的温升斜率放到对角线上**（稳态严格线性、瞬态是后向 Euler），并且
  **失控在求解之前判**（任一格 `hc + capdt − q_ref·α <= 0` ⇒ `THERMAL_RUNAWAY`）。
  金标 50×50×1.6 mm / k=0.3 / h=10 / 2 W / cell 2 mm ⇒ 73.393487 ℃（闭式 73.39345）。
  仍然没有的：3-D、辐射、热过孔、组件热容与接触热阻、交流损耗（趋肤/邻近）、
  `sim_spice` 里的器件不发热、`board_ir_drop` 也不把自己的 I²R 反哺进温度场；
  而且这个 2-D 场只沿**你给的层压板 k** 扩散，不沿真实铜面/平面导热，也不读板文件。

### 滤波器合成：给几何，不给 EM 结果（[docs/22](docs/22-微带与腔体滤波器.md)）

- **是合成器，不是仿真器。** 六个 `filter_*` 工具回答的是"要画成什么样"：原型 g 值、
  集总 L/C（含有限 Q、一阶寄生、蒙特卡洛良率）、耦合线 `w/s`、耦合谐振器 `Qe/k/λ_g/2`、
  矩形腔 TE101 的长度与 Q。凡是需要全波才能回答的问题（交叉耦合、色散、分布式谐振器的实测 Q、
  iris 几何、辐射）全部进 `not_computed`，并在每一份答案里带出去——调用方不读文档也知道边界。
- **两路独立推导必须对上，否则拒答。** 集总带通把真实 L/C 级联的 S21 与闭式
  `1/(1+ε²Tn²)` 逐点比，差 >0.02 dB 就 `INTERNAL_CHECK_FAILED`（偶数阶要先过 `1:√g(n+1)`
  变压器才对得上，见 [docs/27](docs/27-自动布线器.md) §27.14.9）；腔体 Qc 用闭式括号式与
  六面 `|H_tan|²` 数值积分两路算，差 >2% 就拒答。实测 n=3 / 2.4 GHz / FBW 0.1：
  f0 0.0 dB、带边 0.5/0.5001 dB、两路差 0.000521 dB；WR-90 @10 GHz：d=19.8536 mm、
  Qc=7821.5（两路 0.0%）、R/Q=160.957 Ω。
- **耦合线反解是数值的，不是查表的。** 截面用 `field2d` 解拉普拉斯（奇模电壁、偶模磁壁），
  几何用 good Broyden + 回溯线搜索在 `log(w), log(s)` 上反解，**每次迭代都回带**。
  70/40 Ω @0.2 mm/εr4.3 得 w=0.2855、s=0.0788 mm，最差 0.006%、3.5 s。
- **网格追基板高，不追 gap。** `cell = h/fine_div`（默认 6）：实测 `h/6→h/12` 只移动
  0.9%/0.7%，而 quasi-static 模型自身误差更大，所以不拿 3 倍时间换 1%。这个漂移写进
  `precision`，不声称网格无关。
- **k 的分辨率极限要说出口。** `k=(z0e−z0o)/(z0e+z0o)` 是两个相近数之差：阻抗解到 0.5%
  时 k 的误差可以是 13~15%。所以 k 误差 >3% 就带 `k_resolution_note`，
  `coupled_resonator_bpf` 的每段直接给 `meets_target: false` —— 这个耦合太弱，
  准静态解不出可信的 s，去 EM 里调。
- **一个只在异常分支才炸的缺陷。** `k_resolution_note` 里引用了不存在的变量（`z0e_target`），
  任何 k 误差 >3% 的调用都会 `NameError`；原来的用例都收敛得很好，这个分支从没被执行过。
  修完补了一条专门把它逼出来的测试——只在异常路径上输出的解释性代码，也得有测试。

## 项目结构

```
pcb_agent_mcp/
├── run_server.py                 # 启动器（把 src 加入 sys.path 后起 stdio 服务）
├── pyproject.toml                # 零 runtime 依赖；entry point: pcb-agent-mcp
├── src/pcb_agent_mcp/
│   ├── server.py                 # stdlib JSON-RPC 2.0 stdio 协议层 + --selftest
│   ├── tools.py                  # 151 个 tool 的 schema 与 handler 注册
│   ├── edit/                     # 写路径：propose -> apply（两种格式）
│   │   ├── ops.py                # 37 个 KiCad op（板框/开槽/挖空/机械/文字/走线/过孔/keepout/zone/via-in-pad/泪滴）
│   │   ├── build.py              # S 表达式构造器（层号按文件版本写）
│   │   ├── checks.py             # 板框闭合/越界/铜间距/digest
│   │   ├── plan.py               # propose / apply / create_board / checkpoint
│   │   ├── epro_edit.py          # .epro 写：6 op、原地重写一个成员、六道闸门、行级 diff
│   │   ├── sch_ops.py            # 18 个原理图 op（Y 向下的文件坐标系）
│   │   ├── sch_plan.py           # 原理图 propose/apply + netlist_diff/erc_diff
│   │   └── fsutil.py             # 原子写（同卷临时文件 + fsync + os.replace）
│   ├── desktop.py                # Desktop 层：ctypes 探测/启动/关闭/开工程（不碰文件）
│   ├── lceda_live.py             # 立创 Pro 进程内桥：命令空间管道 + 帧层 + invoke/doc.api 客户端
│   ├── oracle.py                 # Oracle 层：只读度量，缺事实就拒绝回答
│   ├── geometry.py               # 纯 stdlib 2D 几何（mm、Y 向上）
│   ├── fidelity/                 # 保真层：整份文档进树，事实从树里读
│   │   ├── sexpr.py              # 无损 S 表达式（保留原子拼写/引号/转义）
│   │   ├── kicad_doc.py          # 整棵 (kicad_pcb ...) + token 双射自证 + 覆盖报告
│   │   ├── epro_doc.py           # 整个 ZIP：每个成员、每行记录、成员顺序都保留，可逐字节重打包
│   │   ├── lceda.py              # 立创记录流：行/终止符/标签表/单位（恒 mil）、append_record
│   │   ├── eproj_doc.py          # SQLite 工程（.eprj/.eprj2）：只读打开，写一律拒绝
│   │   ├── views.py              # 事实投影，每条记录带 raw
│   │   ├── engine.py             # 裁判：kicad-cli 的 DRC / ERC / netlist / stats / Gerbers / pos
│   │   └── kicad_sch.py          # 整棵 (kicad_sch ...)：符号/引脚/库符号/工程名，可逐字节往返
│   ├── adapters/                 # Adapter 层：两份格式 → 一个 Canonical
│   │   ├── canonical.py          # 唯一真值容器 + present/absent 记账
│   │   ├── kicad_pcb.py          # S-expr 解析、Edge.Cuts 轮廓、叠层、工程规则
│   │   ├── epro.py               # ZIP 内记录流；单位恒 mil→mm；自由焊盘与封装焊盘分开报
│   │   └── __init__.py           # 按内容嗅探格式并分发
│   ├── fab/                      # 制造输出：只外包给 kicad-cli，产物重新读一遍验可用性
│   ├── sync/                     # 原理图网表 ↔ 板：逐脚差集 → op（一个字节都不写）
│   ├── fanout.py                 # BGA 扇出：朝外短桩 + 旋转阶梯 + 布线器同一套精确几何
│   ├── ledger.py                 # 运行账本：按板 sha256 归键，永不改变答案、不是缓存
│   └── calc/                     # Calc 层（纯算术，不读文件、不联网）
│       ├── result.py             # 统一信封：basis/precision 强制
│       ├── units.py              # mil/mm/inch；立创实测存 mil，0.01in 仅兼容别名
│       ├── current.py            # IPC-2221 载流正/反解 + 过孔
│       ├── impedance.py          # 微带/带状线正/反解 + 有效区间护栏
│       ├── diffpair.py           # 边耦合差分阻抗正/反解
│       ├── field2d.py            # 差分截面 2-D 场解（有限体积 + 红黑 SOR）+ 导体形状因子 ∮g²ds
│       ├── rf.py                 # λ/20
│       ├── creepage.py           # 安规间距公共入口：IEC 60664-1 族走 iec60664，其余 fail-closed
│       ├── iec60664.py           # IEC 60664-1:2020-05 爬电/间隙/瞬态/耐压/槽宽（行为反演）
│       ├── safety_table.py       # 表本身的可执行体检（来源数/单调性/重复 key）
│       ├── feedline.py           # 微带/带状线/CPW/GCPW + 反解宽度 + 导体损耗场解/薄电流双估计
│       ├── antenna.py            # 贴片/印刷线/偶极单极/螺旋/小环/Chu 上限/芯片天线
│       ├── matching.py           # 反射/L/π/T/短截线/四分之一波长/带宽=f0/Q（每解回代）
│       ├── link.py               # FSPL/Friis/最大距离/有效面积/远场/菲涅耳
│       ├── spice.py              # SPICE 子集 + 可选委派 ngspice（raw 解析 / deck 过滤 / 双引擎对账）
│       ├── power.py              # 铜阻/过孔/自感/电阻网络/IR drop/电容组/PDN
│       ├── thermal.py            # 1-D 热阻串/过孔阵列/2-D 板 SOR/瞬态/散热片/对流
│       ├── dfm.py                # DFM：11 条规则目录 + 能力文档体检 + 五态判定（无内置表）
│       ├── vibration.py          # 振动：Ritz 模态 + Miles/Steinberg/冲击/传递率 + 应力（degree 阶梯误差棒）
│       ├── reliability.py        # 可靠性：焊点热疲劳（Engelmaier/Miner/Norris-Landzberg）+ 跌落板面应变 + 冲击谱
│       ├── cavity.py             # 平面谐振：腔体模 + 过孔围栏 pitch/λ_g（判据外给）+ plane_q（Qc = h/δs）
│       ├── em.py                 # 全波接缝：找出 openEMS/Meep/gprMAX、版本与 handoff（不生成几何）
│       ├── filters.py            # 滤波器合成：原型 g 值/集总带通(两路对账 + 有限 Q + 寄生 + 蒙特卡洛)/耦合线反解/耦合谐振器/TE101 腔
│       ├── filtermatrix.py       # 耦合矩阵：交叉耦合零点(余子式) + 有限 Qu 插损；节点法 + 精确导纳逆变器 Π 等效，无损时对原型闭式自检
│       ├── electrothermal.py     # 电热耦合：R(T) 定点迭代 + 精确失控判据 + 板级 2-D 场（SOR）
│       ├── magnetics.py          # 功率电感：CCM boost/buck/buck-boost 占空比/电感/峰值/CCM 边界
│       ├── stackup.py           # 板材表 + 4/6 层叠 + 每层 50Ω 线宽 + λ_g/20 缝合孔
│       ├── chain.py             # 射频链路级联：Friis NF / IIP3 / kTB 灵敏度
│       └── rules.py             # 规则引擎：22 条布局 + 14 条原理图目录，缺事实 ⇒ undetermined
├── data/
│   ├── iec60664_tables.json      # IEC 60664-1 阶梯（808 步 / 32 键）+ not_covered + sources，verified:true
│   └── ipc2152_tables.json       # IPC-2152 闭式拟合系数 + 9 个锚点（points 故意为空，原因写在 why_empty）
├── tests/                        # 1785 个用例（含"解析前后 sha256 不变"、"闸门有牙"、真实归档形状，
│                                 #  以及拿 pcbnew / kicad-cli 当裁判的对账用例）
│   └── data/sample.kicad_pcb     # 合成样本（不是真实工程，但已通过 KiCad 校验）
└── docs/                         # 设计文档 00~38，建议按编号顺序读（索引见 docs/README.md）
```

## 免责声明

本项目是**计算与推导辅助工具**，不是标准文本、不是仿真器、不是替代评审的判决依据。

- 载流、阻抗、λ/20 均为**闭式近似**，误差已在 `precision` 字段声明（±12%~±30%）。差分阻抗另有二维场解
  （`diff_impedance_field`），但紧间距耦合没有独立参考答案，那种几何下它会自己声明
  `reference_note`。共面线（cpw/gcpw）也有场解（`coplanar_field`），它在七个几何上都比共形映射
  **读低** 1.19%~3.39%，即共形映射是乐观的那一端；共面损耗现在由场解给出，但只有零厚度值
  （真实铜箔更高约 10%，而那 10% 自己也没收敛）。共面的地默认按**无限宽**算，真实版图地留窄时传 `ground_mm`——单线 `g/2` 宽的地会抬 13.7%，差分对则只动差模 1.1% 而动共模 21.9%。差分对同理，且**差分对的数一律是 Zdiff = 2·Z_odd**；要拿宽度而不是阻抗时用 `feedline_width`，它在差分对上会把残差一起报出来。定稿前请用场求解器或打样厂的阻抗报告复核。
- 仿真层是**子集**：SPICE 只有 R C L V I E G D Q M 与后向欧拉瞬态，没有自热、没有噪声/失真、
  没有器件电容随温度；热学是 1-D/2-D 稳态与单级或多级 RC，不含 TIM 压缩、辐照与空气流场；
  板级 IR drop 只算直流、只用叠层铜厚。逐条写在每次返回的 `not_computed` / `warnings` 里。
- DFM 校验是**工艺能力核对**，不是板厂签核。它只回答「给定这份能力文档，板上哪几条越界了」；
  蚀刻公差、阻焊桥、丝印可读性、拼板与基准点、铜到成品孔壁这 8 类静态文件里没有可比事实，
  一律 `not_checked_by_design`，不要把它读成「通过」。
- 平面谐振只给模频，**不给 Q、不做边界失谐修正、不算平面阻抗**：它告诉你模在哪、间距是多少个 λ_g，
  不告诉你这个模会不会出问题。电热耦合只闭合「铜 R(T) ↔ 一个集中 Rth」这一个环，不是热仿真；
  热失控判据 `dP/dT·Rth ≥ 1` 是精确的，但它继承你给的那个 Rth 的全部误差。
- 振动估算是**设计参考，不是应力分析**。薄板 Kirchhoff 理论只管「板本身怎么弯」，不管焊点/引线/器件的
  实际应力；疲劳用 Steinberg 三带 + Miner 线性累积，允许挠度必须由你按器件类别给；冲击是单自由度
  响应，不是有限元接触分析。
- 爬电/间隙**故意不给默认值**。安规间距须按适用标准与电压等级查表，本项目只做查表与一致性检查；
  涉及市电、医疗、车载等场景请由有资质的工程师评审。
- MIT 许可证，作者不对任何设计后果承担责任。

## 贡献

1. **新增计算必须带 `basis` 与 `precision`**，并在文档里写明依据的公式/图表与适用区间；拿不到依据就 fail-closed，
   不要填一个“看起来合理”的默认值。
2. **不要复用未核验的第三方实现**。往 `calc/` 里加东西前，先手算一个已知点对一遍——本项目的两条反例
   就来自“能跑、但差了 2 倍”的现成代码。
3. **测试要覆盖拒绝路径**：每个 fail-closed 分支至少一个用例，断言 `reason_code` 而不是断言异常。
4. 跑 `python run_server.py --selftest` 与 `python -m unittest discover -s tests`，两者全绿再提交。
5. **不许写用户的工程**。任何新增能力若需要写文件，先走 propose→digest→approve；只读路径要补一条
   "解析前后 sha256 不变" 的用例。

## License

[MIT](LICENSE) © 2026 xw19981010
