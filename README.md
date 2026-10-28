# pcb-agent-mcp

让 AI 在 **立创 EDA Pro** 与 **KiCad** 里真正"算得出来再动手"的 MCP 服务。

当前版本 `0.0.6` 已实现七层：六层只读 + 一条**带闸门的写路径**（提案 → 落盘，两种格式）。
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
> → `20` 平面谐振与电热耦合。

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
2. **一律 fail-closed。** 载流不传 `standard` 报 `STANDARD_REQUIRED`；`ipc2152` 表**未核验**
   （`verified:false` 或零行）报 `STANDARD_DATA_NOT_LOADED`；爬电/间隙表缺失或未核验报
   `SAFETY_TABLE_MISSING`。
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

## 工具（92 个）

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
| `creepage_required` | 爬电距离查表 | 无表 fail-closed |
| `clearance_required` | 电气间隙查表 | 无表 fail-closed |
| `safety_table_check` | 判定一张安规表能不能用：来源≥2、随电压/材料组/污染等级单调、PD1≤PD2≤PD3、无重复 key | 只能判"转录没法作弊"，判不了"来源有没有抄错"；因此 `data/*.json` 仍 `verified:false`，查询继续拒答 |
| `diff_impedance` | 差分对几何 → Zdiff（边耦合微带/带状线） | s/h 超出 0.1..3.0 标 `validity_note` |
| `diff_impedance_field` | 同一几何 → Zdiff，但解真实截面上的 div(ε∇V)=0（有限体积 + 红黑 SOR，纯 stdlib） | 参考解只覆盖零厚度带状线/孤立微带；紧间距耦合回 `reference_note`（求解器的意见，不是被核过的数）；不建模阻焊/蚀刻梯形/铜损 |
| `diff_width` | Zdiff → 线宽（间距固定，二分） | 不可达 → `UNREACHABLE` + 可达带宽 |
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
| `feedline_impedance` | 微带 / 带状线 / CPW / GCPW → Z0 与 εeff | CPW 不建模铜厚（明说）；CPW 导体损耗回 `null` + 原因 |
| `feedline_width` | Z0 → 线宽（二分） | 不可达回 `OUT_OF_RANGE` + `reachable_ohm` 两端实测 |
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
| `link_friis` | 链路预算：Prx / 路损 / EIRP / ERP | `FSPL_CONST = -27.5522`（写错 60 dB 的坑） |
| `link_max_range` | 给定灵敏度与额外损耗 → 最大距离 | 额外损耗要显式给，默认 0 就说 0 dB |
| `link_aperture` | 天线有效面积 | — |
| `link_far_field` | 远场距离：`max(2D²/λ, 5D, λ/2π)` 并说明哪个机制主导 | 三段都要给，只给一段会误导 |
| `link_fresnel` | 第一菲涅耳区半径 + 60% 净空 | — |
| `board_antenna_keepout` | 板级净空：某矩形内的铜与 **zone** | 派生量 `authoritative:false`；pad/track/via 与 zone **分开计数**（DRC 看不见铺铜失谐） |
| `sim_spice` | 真解电路：`.op` / `.tran` / `.ac`，MNA + 牛顿 + pnjlim + gmin 步进 | 器件卡 R C L V I E G D Q M；**X/F/H/K/S/W/B/T/O/U/A 逐条点名拒绝**，不静默跳过 |
| `trace_resistance` | 铜线直流电阻 `rho(T)·L/(w·t)` | 只管这一段；过孔/扩展/端子不在这个数里 |
| `via_resistance` | 过孔桶壁电阻（截面是**铜环**不是钻孔圆） | 载流是另一码事，要温升判据 |
| `trace_inductance` | 直线走线自感（Grover 带公式，±20%） | 只用于比两种布局；地平面像项**明确未计入** |
| `ir_drop_chain` | 分段串联 IR drop（每段自己的宽/厚） | 不把不同线宽折算成平均宽 |
| `pdn_impedance` | 电容组 `\|Z(f)\|` 全曲线 + 平面电容 | 要看**反谐振峰**，不是看最低点 |
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
| `vibration_random_response` | 随机振动：Miles 等效（平坦闭式 + 实测谱积分互证）→ g_rms / 位移 rms | 单模态；非平坦谱逐段积分只在足迹内，谱外不计 |
| `vibration_fatigue` | Steinberg 三带（3σ/2σ/1σ）+ Miner 线性累积 → 循环数与小时寿命 | `z_allow_mm` **必填**（不内置「典型允许挠度」） |
| `vibration_shock` | 半正弦冲击：SDOF Newmark（β=1/4、γ=1/2）→ 峰值位移/加速度、DLF | 单自由度；不算应力、不算二次冲击与接触碰撞 |
| `vibration_isolator` | 隔振：传递率曲线、隔振起点 `f_n√2`、r=1 处 `Q√(1+1/Q²)` | 高频按 **1/r** 掉（分子含 (2ζr)²），不是 1/r²；不算隔振器非线性与蠕变 |
| `board_vibration` | 按板上真实板框（bbox）+ 叠层厚度算 f₁/周期/热点，热点标 `near_antinode` | 派生量 `authoritative:false`；非矩形轮廓回 `outline_fill_fraction_of_bbox` 并说 f₁ 偏低 |
| `cavity_resonance` | 平面腔体模 `f_mn = c/(2√εr)·√((m/a)²+(n/b)²)` + 每个模的 λ_g | **只给模频不给 Q**：无介损/铜损/辐射、无开放边缘失谐，所以是频率轴上的位置不是判决 |
| `cavity_via_fence` | 过孔围栏：`pitch/λ_g`、每间距电长度、给定判据后的 `pass/fail` | **判据必须由调用方给**（λ_g/20 与 λ_g/10 差 2 倍，都不内置）；不给就 `indeterminate` 且不反推间距 |
| `electrothermal_trace` | 走线自洽温升：R(T) → I²R → ΔT → R(T) 的定点迭代 + 精确失控判据 `dP/dT·Rth < 1` | `rth_c_per_w` **必填**；G≥1 回 `THERMAL_RUNAWAY` 而不是最后一次迭代；只算直流 |
| `electrothermal_conductor` | 同一环，R₂₀ 由调用方给（过孔桶壁 / 分段串联 / 分流器 / 针脚） | 回 R_hot、电阻增幅、迭代次数、稳定性余量 `1−G`；Rth 仍是调用方的 |

`via_current_allowed` 实算 0.3mm 孔 / 25µm 镀铜 → 1.795 A，高于工程经验值 1A；
返回里同时给出 `rule_of_thumb_gap`，请按保守者取用。

## 用法

零外部依赖，Python ≥ 3.9。

```bash
python run_server.py --version       # 0.0.6
python run_server.py --selftest      # 协议级自检，51/51（含板与原理图写路径端到端、天线四问、仿真四问、DFM 三问、振动五问、腔体与电热五问）
python -m unittest discover -s tests # 642 个用例（约 3 分钟，kicad-cli / pcbnew 用例占大头）
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
- **立创 Pro 的实时 API：客户端写了，但一次都没被真应用应答过。** `lceda_api_probe` 现在真的会做
  RFC 6455 握手、发帧、解析能力清单（`tests/test_lceda_api.py` 10 例，含握手短路/掩码/分片/超时），
  但本机 49620-49629 全部 `no_listener`——那个网关只有在用户手动打开「外部交互权限」后才监听。
  所以每条返回都带 `verified_against_app: false`：**代码路径通了，真应用这一侧仍是空的。**
- **立创的"启动"和"开工程"都没能在这台机器上确认，而这次是量出来的。** 用一个临时副本
  （`_scratch/argv_probe/`，绝不碰用户工程目录）实测：`lceda-pro.exe <工程.epro>` **0.5 s 后
  以 rc=1 退出**，不带参数则 **0.5 s 后 rc=0 退出**，全程没有窗口、没有写用户数据目录
  （`%APPDATA%\JLCEDA Pro` 仍是空的）；同一环境下 KiCad 10.0.7 能被正常拉起并关掉，
  所以不是本层的启动方式坏了。结论：`open_argv_verified` 保持 `false`，`opened` 只会是
  `unconfirmed`；桌面控制对立创目前只到"探测到装在哪"（4.1.60，盘符扫描命中）。
- **差分阻抗有两级：闭式近似与二维场解，两者差多少是实测的。** `diff_impedance` 仍是边耦合闭式（IPC-2141A 族），`diff_impedance_field` 解真实截面上的 div(ε∇V)=0（有限体积 + 梯度张量网格 + 红黑 SOR，纯 stdlib）。闭式在实测几何上系统性**偏低 8~11%**、方向一致，所以「调宽凑数」会把线先做窄。场解自己的误差对着两个共享零近似的参考答案量过：零厚度带状线对精确共形解（w/b 0.5..4）≤1.2%、孤立微带对 Hammerstad ≤2.8%、耦合通路在 s/h=8 处对 2×孤立精确解 ≤2.7%；这三张表由 `tests/test_field2d.py` 每次重算，不会悄悄过期。**没被独立参考答案覆盖的是紧间距耦合**——那种几何下工具自己吐 `reference_note`，说明「这是求解器的意见，不是被核过的数」。两者都不建模阻焊、蚀刻梯形与铜损/色散。
- **IPC-2152 / IEC 60664-1 的表是空的，而且是故意空的。** `calc/safety_table.py` 把"表能不能信"
  做成可执行的判据：来源 ≥2、随电压单调不降、随材料组变差单调不降、PD1≤PD2≤PD3、无重复 key
  （`tests/test_safety_table.py` 17 例逐条反证）。但规则判不了"来源有没有抄错"——
  互相抄的两个博客也能全过。公开来源对 IPC-2152 的读数互相矛盾（与 IPC-2221 差可 >3×），
  所以 `data/ipc2152_tables.json` 与 `data/creepage_tables.json` 都保持 `verified: false`、
  行数据留空并写清 `why_empty`，`current_capacity(standard=ipc2152)` / `creepage_required` /
  `clearance_required` 继续 fail-closed 并说出缺什么。**填一个猜的值比拒答更糟。**

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
  gmin 向下步进），器件卡只认 `R C L V I E G D Q M`；`X F H K S W B T O U A` 十一类一律
  `UNSUPPORTED_CARD` 并点名，而不是跳过——**一半跑完的网表比拒答更危险**。瞬态是后向欧拉
  （不是梯形），AC 先解工作点再只覆盖雅可比。`not_computed` 每一条都写清：没有自热、没有器件
  电容/迁移率/击穿随温度、没有噪声/失真/傅里叶分析。
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
  不算应力、不算接触碰撞、不算多自由度追赶。
- **`board_vibration` 用板框 bbox 而不是真实轮廓**，并明说方向：非矩形轮廓会**偏软**（f₁ 偏低），
  同时回 `outline_fill_fraction_of_bbox` 让人自己判程度。不算元件质量、不算安装点刚度、不算板内开孔。

### 平面谐振与电热耦合：一个只给模频，一个只给一个 Rth（[docs/20](docs/20-平面谐振与电热耦合.md)）

- **腔体模是闭式的，Q 不是。** `cavity_resonance` 给 `f_mn = c/(2√εr)·√((m/a)²+(n/b)²)` 与 λ_g，
  100×80 mm / εr=4.3 的 (1,0) 落在 0.722864 GHz（λ_g 恰为 2a，即半波的定义），(1,1) 严格等于
  √(f₁₀²+f₀₁²)，方板的简并对**分行列出**。没有 Q（介损、铜损、辐射）、没有开放边缘失谐修正
  ——真实平面与其边缘不是 PMC，所以这组数是教科书值，返回里写清"实物会有几个百分点失谐"。
  一个谐振没有 Q 就不是判决，本层因此**不判"这个模要不要紧"**。
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
- **只闭合一个环，别读成做了耦合仿真。** 没有 2-D/3-D 耦合（热点烤热邻居再降低自己的局部 Rth）、
  没有瞬态耦合（热容不进环）、没有交流损耗（趋肤/邻近）、`sim_spice` 里的器件不发热、
  `board_ir_drop` 也没把自己的 I²R 反哺进温度场——那一层仍由用户迭代。

## 项目结构

```
pcb_agent_mcp/
├── run_server.py                 # 启动器（把 src 加入 sys.path 后起 stdio 服务）
├── pyproject.toml                # 零 runtime 依赖；entry point: pcb-agent-mcp
├── src/pcb_agent_mcp/
│   ├── server.py                 # stdlib JSON-RPC 2.0 stdio 协议层 + --selftest
│   ├── tools.py                  # 92 个 tool 的 schema 与 handler 注册
│   ├── edit/                     # 写路径：propose -> apply（两种格式）
│   │   ├── ops.py                # 24 个 KiCad op（板框/开槽/挖空/机械/文字/走线/过孔/keepout/zone）
│   │   ├── build.py              # S 表达式构造器（层号按文件版本写）
│   │   ├── checks.py             # 板框闭合/越界/铜间距/digest
│   │   ├── plan.py               # propose / apply / create_board / checkpoint
│   │   ├── epro_edit.py          # .epro 写：6 op、原地重写一个成员、六道闸门、行级 diff
│   │   ├── sch_ops.py            # 18 个原理图 op（Y 向下的文件坐标系）
│   │   ├── sch_plan.py           # 原理图 propose/apply + netlist_diff/erc_diff
│   │   └── fsutil.py             # 原子写（同卷临时文件 + fsync + os.replace）
│   ├── desktop.py                # Desktop 层：ctypes 探测/启动/关闭/开工程（不碰文件）
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
│   └── calc/                     # Calc 层（纯算术，不读文件、不联网）
│       ├── result.py             # 统一信封：basis/precision 强制
│       ├── units.py              # mil/mm/inch；立创实测存 mil，0.01in 仅兼容别名
│       ├── current.py            # IPC-2221 载流正/反解 + 过孔
│       ├── impedance.py          # 微带/带状线正/反解 + 有效区间护栏
│       ├── diffpair.py           # 边耦合差分阻抗正/反解
│       ├── field2d.py            # 差分截面的 2-D 场解（有限体积 + 红黑 SOR）
│       ├── rf.py                 # λ/20
│       ├── creepage.py           # 爬电/间隙，fail-closed
│       ├── safety_table.py       # 表本身的可执行体检（来源数/单调性/重复 key）
│       ├── feedline.py           # 微带/带状线/CPW/GCPW + 反解宽度
│       ├── antenna.py            # 贴片/印刷线/偶极单极/螺旋/小环/Chu 上限/芯片天线
│       ├── matching.py           # 反射/L/π/T/短截线/四分之一波长（每解回代）
│       ├── link.py               # FSPL/Friis/最大距离/有效面积/远场/菲涅耳
│       ├── spice.py              # SPICE 子集：MNA + 牛顿 + pnjlim + gmin 步进
│       ├── power.py              # 铜阻/过孔/自感/电阻网络/IR drop/电容组/PDN
│       ├── thermal.py            # 1-D 热阻串/过孔阵列/2-D 板 SOR/瞬态/散热片/对流
│       ├── dfm.py                # DFM：11 条规则目录 + 能力文档体检 + 五态判定（无内置表）
│       ├── vibration.py          # 振动：薄板 Ritz 模态 + Miles + Steinberg + 冲击 + 传递率
│       ├── cavity.py             # 平面谐振：腔体模 + 过孔围栏 pitch/λ_g（判据外给）
│       └── electrothermal.py     # 电热耦合：R(T) 定点迭代 + 精确失控判据
├── data/
│   ├── creepage_tables.json      # 空表 + verified:false + why_empty（故意不填）
│   └── ipc2152_tables.json       # 同上；表本身由 calc/safety_table.py 可执行地体检
├── tests/                        # 642 个用例（含"解析前后 sha256 不变"、"闸门有牙"、真实归档形状，
│                                 #  以及拿 pcbnew / kicad-cli 当裁判的对账用例）
│   └── data/sample.kicad_pcb     # 合成样本（不是真实工程，但已通过 KiCad 校验）
└── docs/                         # 设计文档 00~20，建议按编号顺序读（索引见 docs/README.md）
```

## 免责声明

本项目是**计算与推导辅助工具**，不是标准文本、不是仿真器、不是替代评审的判决依据。

- 载流、阻抗、λ/20 均为**闭式近似**，误差已在 `precision` 字段声明（±12%~±30%）。差分阻抗另有二维场解
  （`diff_impedance_field`），但紧间距耦合没有独立参考答案，那种几何下它会自己声明
  `reference_note`。定稿前请用场求解器或打样厂的阻抗报告复核。
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
