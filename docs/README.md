# 设计文档索引

这二十六篇不是教程，是一条**逐步收紧的判决链**：每一篇都在推翻或修正前一篇的某个前提，
最后收敛到「先做 Calc 层」这个可执行决定。建议按编号顺序读，跳过前面会看不懂后面的取舍。

| # | 文档 | 它回答的问题 | 它推翻了什么 |
|---|---|---|---|
| 00 | [开源 MCP 调研与架构建议](00-开源MCP调研与架构建议.md) | 现有哪些开源实现？两个 EDA 的可编程面差在哪？ | —— |
| 01 | [AI 原生的 PCB 表示](01-AI原生的PCB表示.md) | 为什么把图元 API 交给模型必然失败？ | 「tool 越多越强」（247 / 560+ tool 的路线） |
| 02 | [占用可通行表示与经验累积](02-占用可通行表示与经验累积.md) | AI 怎么知道「哪被占了、哪走不过」？经验怎么不重复犯？ | 「给个密度百分比就够了」 |
| 03 | [信息丢失审计](03-信息丢失审计.md) | 这套表示还会丢什么？丢到什么程度算事故？ | 「摘要能保住信息」 |
| 04 | [布局表示与度量层](04-布局表示与度量层.md) | 布局本身就是设计，怎么变成可判的量？ | 「布局好坏不可机判」 |
| 05 | [无损转化架构总纲](05-无损转化架构总纲.md) | 「无损」到底怎么定义、怎么强制？ | 01~04 的隐含前提：**把 PCB 压缩成一份好读的摘要** |
| 06 | [存储形式与现有 MCP 的信息传递](06-存储形式与现有MCP的信息传递.md) | 文件里到底存了什么？现有 MCP 实际喂给模型什么？ | 「文件里没有所以拿不到」——不是没存，是没人算 |
| 07 | [自研定位与风险分期](07-自研定位与风险分期.md) | 我们做什么、按什么次序做、最可能死在哪？ | 「这是转换工具」（改判为**推导**工具，5%/95%） |
| 08 | [PCB 设计流程与工具映射](08-PCB设计流程与工具映射.md) | 真实设计流程六步，每步需要哪些计算与约束？ | 「先接 KiCad 再说」（双栈必须同时在场） |
| 09 | [布局方法论补强与标准冲突](09-布局方法论补强与标准冲突.md) | 经典方法论之间互相矛盾时听谁的？ | 「冲突可以推理裁决」（改为**登记 + 声明**） |
| 10 | [桌面控制与双栈读路径](10-桌面控制与双栈读路径.md) | EDA 开没开、工程开没开怎么变成可查事实？两份格式凭什么喂同一个度量层？ | 「启动了进程就是打开了工程」；「两套写法可以并存」 |
| 11 | [保真层：转换还是推导](11-保真层-转换还是推导.md) | 中间层凭什么说"等于那张板"而不是"推导出那张板"？怎么证明？ | 「解析 5%、推事实 95%」——**推导即猜**；「合成样本通过 = 解析器正确」 |
| 12 | [写路径：从"提案"到"落盘"](12-写路径-提案到落盘.md) | 改那块板凭什么可信？两种格式的静默失败长什么样？ | 「调用一个函数、得到一个改好的文件」；「合成器写对了就行」 |
| 13 | [立创实测：真实存储与记录格式](13-立创实测-真实存储与记录格式.md) | `.epro` 里到底有什么、单位是什么、一次保存改动了谁？哪些结论是被真板子推翻的？ | 「保存会新增成员并改写 project.json」「标签写了 mm 就按 mm 存」「焊盘网络行只有一个 id 字段」——全部改成 194 个真实归档上的实测 |
| 14 | [原理图写路径：裁判换成网表与 ERC](14-原理图写路径.md) | 板能写了，原理图凭什么能写？为什么 DRC 那一套在这里一言不发？ | 「板路径的闸门照搬」——DRC 判铜，原理图的产品是连通性，裁判只能是网表 + ERC；另加「坐标取反只是镜像，KiCad 不会报错」 |
| 15 | [对账：推导对着厂商判决](15-对账-推导对着厂商判决.md) | 我们算的间距凭什么可信？哪些是比不了的？ | 「推导标了 authoritative:false 就算尽责」——要拿厂商 DRC 在同一文件上双向数 recall 与 extra |
| 16 | [天线与馈线](16-天线与馈线.md) | 给定频率与尺寸，第一个能打样的几何是什么？哪几个数不能信？ | 「给个闭式公式就能设计天线」——**没有全波就没有方向图**；长度必须给区间；每个匹配解必须回代 |
| 17 | [仿真层](17-仿真层.md) | 给定网表/铜箔/功耗，哪些数能给、哪些必须拒绝？ | 「有公式就等于能仿真」——**没有全波、没有电热耦合、没有辐射**；SPICE 只做子集且逐条点名拒绝 |
| 18 | [DFM：工艺能力校验](18-DFM-工艺能力校验.md) | 工艺能力从哪来？文档里没写的规则怎么办？ | 「服务器内置一份板厂表」——**没有表，只有规则目录**；错键 / 无出处整份拒收；量不到就给下界 |
| 19 | [机械振动：薄板、单模态、线性](19-机械振动.md) | 它多快开始振？振多狠？多久会裂？掉一下会怎样？ | 薄板 Ritz 求解（不用模态表）→ Miles 随机响应 → Steinberg 三带疲劳 → Newmark 冲击 → 三个应力极值分层报（von Mises 在角、主应力在跨中）；不算装配柔度与焊点蠕变 |
| 20 | [平面谐振与电热耦合](20-平面谐振与电热耦合.md) | 地平面什么时候不再是地？缝合孔要多密？电流把铜烤热之后还稳吗？ | 平行板腔体模 + 过孔间距/λ_g（**判据是调用方的**）；电热定点迭代给 A/(1−G) 与**精确失控判据** G≥1，板级再放进 2-D 层压板温度场（SOR，失控在求解前判）；PDN 只给每个腔体模的峰值 \|Z\|，**不给曲线**（模态和不收敛），不算 3-D、不算交流损耗 |
| 21 | [SPICE：内置子集 + 可选委派真引擎](21-SPICE引擎委派.md) | 为什么不直接内置一个完整 SPICE？装了就一定用得上吗？ | 不 vendor 二进制、不加 runtime 依赖：内置子集保证有答案，装了 ngspice 就委派；`engine="ngspice"` 找不到**报错不回退**；`cross_check` 比两引擎的节点电压；**本机无二进制 ⇒ 外部路径 `verified_against_engine:false`** |
| 22 | [微带与腔体滤波器](22-微带与腔体滤波器.md) | ADS 里那几个合成按钮，不给 EM 结果能给什么？ | 「合成 = 仿真」——只给原型 g 值 / 集总带通 / 耦合线 Newton 反解 / 耦合谐振器 / 矩形腔 TE101 的 Qc 双推导；无交叉耦合、无色散、无有限 Q、无 iris 几何、无版图、无良率 |
| 23 | [两张读不到原文的表](23-两张读不到原文的表.md) | 标准是付费的、公开转述互相矛盾，凭什么敢把数填进去？ | 「读原文抄表」——IPC-2152 抄 KiCad 的**闭式拟合**并用锚点+物理探针三线校验；IEC 60664-1 **编译原实现、探针反演阶梯、20 万点对拍**；并记下三条只有行为对拍才发现的错（尤其**断点必须每次重新恢复，不能继承上一版 JSON**）。其余安规族仍 fail-closed |
| 24 | [立创进程内桥](24-立创进程内桥.md) | 怎么才能看见用户正开着的那张图？应用自己的命令空间长什么样，我们凭什么敢说"读过它"？ | 「要驱动立创就得写扩展或用 websocket 网关」——其实是它自己开的命名管道 `\\.\pipe\JLCEDAPro<8 hex>` 可枚举 + ping 确认；并推翻前两轮的「它起不来」（差别在父进程有没有控制台）。写路径**一次都没做过**，逐条列在 §24.9 |
| 25 | [技能并入：八个技能里哪几条进了工具](25-技能并入清单.md) | 这八个技能里什么能变成工具、什么必须拒？一个技能的说法与它自己的公式不自洽时听谁？ | 「技能里的清单可以直接搬」——**清单是可搬的，建议不是**：规则进目录并标出处与证据等级，量不到一律 undetermined；技能 §3.5 那张 λ/20 表与 λ/20 公式差一倍，表被丢弃；两个付费 `gmai-*` 技能的内容串域（固件安全样板），整份拒收 |
| 26 | [分步可见与铺铜闭环](26-分步可见与铺铜闭环.md) | 过程为什么必须看得见、打得断？器件从哪来？铺铜什么时候才算真的铜？ | 「apply 必须原子」（原子是给脚本的，不是给协作者的）；「有 violation 就挡」（真板 69 条全是 warning）；「栅格能回答 pad 出不出得去」（0.5 mm 间距下必假警）；「写了 `(general (thickness 1.6))` 就是 1.6 mm」（KiCad 只累加 stackup，实造 1.18）；「`add_zone` 之后那块铜就算有了」（没有 filled_polygon 就只是轮廓） |
| 07 §7 | 板框 / 开槽 / 机械层 | 待议，6 条议题 | —— |

## 三条贯穿全部文档的结论

1. **PCB 文件里只有图元；AI 需要的是事实；中间那层「推导」几乎所有人都没做。**
   而源头不缺数据 —— 立创 `.epcb` 的规则 / keepout flags / 槽孔 / 热焊盘辐条都在，KiCad 的叠层 εr 也在。
2. **「AI 的错误仍能让 DRC 全绿」的信息，是最危险的信息。**
   模拟布局、共质心、Kelvin 连接、参考平面连续性都属于这一类 —— 它们的错法在 DRC 里看不见。
3. **猜一个安全间距，比不给答案更糟。**
   所以载流要标准名、安规要数据表、拿不到就 fail-closed 并说明缺什么。

## 与代码的对应

`docs/` 只定方向与判据，量化依据全部落在 `src/pcb_agent_mcp/calc/` 里，每个结果必带
`model` / `basis` / `precision` / `assumptions`，文档里的每条公式都能在代码里找到同名实现。

- `00`~`09` → `calc/` 与整体定位。
- `10` → `desktop.py`（探测/启动/关闭/开工程）、`adapters/`（双栈解析）、`oracle.py`（板级度量）。
- `11` → `fidelity/`（保真树 / 视图 / kicad-cli 裁判）：`sexpr.py`、`kicad_doc.py`、
  `epro_doc.py`、`views.py`、`engine.py`，以及 `oracle.py` 里每条派生答案的 `authoritative: false` 标注。
- `12` → `edit/`（写入：ops / build / checks / plan / fsutil / epro_edit）与 6 个 `board_*` 写工具：
  propose（只算不写）→ apply（checkpoint + 原子写 + 五道闸门 + 回滚），以及 §12 里那三条
  kicad-cli 实测的 DRC 事实。
- `13` → `fidelity/lceda.py`（记录流 / 单位 / 逐字节往返）、`fidelity/eproj_doc.py`（SQLite 只读）、
  `edit/epro_edit.py`（六个 op + 六道闸门 + 行级 diff），以及 `adapters/epro.py` 里每条 `authoritative: false` 的成因。
- `14` → `fidelity/kicad_sch.py`（整棵 `(kicad_sch)` 树）、`edit/sch_ops.py`（18 个 op）、
  `edit/sch_plan.py`（propose / apply / netlist_diff / erc_diff）、`engine.sch_erc` 与
  `engine.sch_netlist`，以及 5 个 `schematic_*` 工具。
- `15` → `fidelity/crosscheck.py`（`board_cross_check`：与厂商 DRC 双向对账、
  `not_measured` 三条、zone / 差分对 / 局部 clearance 三处口径），以及
  `tests/test_kicad_pad_placement.py`（拿 `pcbnew` 当裁判的焊盘几何对账）。
- `16` → `calc/feedline.py` / `calc/antenna.py` / `calc/matching.py` / `calc/link.py`
  与 `oracle.antenna_keepout`，以及 21 个天线类工具与 93 个测试；边界是**不含全波求解器**，
  方向图 / 外壳失谐 / 互耦 / SAR 一律写进 `not_computed`。
- `17` → `calc/spice.py`（DC / 瞬态 / AC 子集，MNA + 牛顿 + pnjlim + gmin 步进）、`calc/power.py`
  （铜阻 / 过孔 / 电感 / 电阻网络 / IR drop / PDN 三态）、`calc/thermal.py`（热阻串 / 过孔阵列 /
  2-D 板 / 瞬态 / 散热片 / 对流）与 `oracle.board_ir_drop`，以及 14 个仿真类工具与 81 个测试；
  边界是**只做子集**：未实现的 SPICE 卡、自热、平面谐振、全波、电热耦合全部逐条点名拒绝。
- `18` → `calc/dfm.py`（11 条规则目录 + 能力文档体检 + 五态判定）、`oracle.dfm_check` 与
  `_dfm_facts`（11 个事实，每条带 `where`），以及 2 个 DFM 工具与 28 个测试；
  边界是**没有内置板厂表**：8 类量不出来的项目写进 `not_checked_by_design`，
  有界搜索窗外的情况只给 `lower_bound`，不冒充最小值。
- `19` → `calc/vibration.py`（Rayleigh-Ritz 薄板模态 / Miles 随机响应 / Steinberg 疲劳 / Newmark 冲击 / 传递率 / 板级应力三极值）与 `oracle.board_vibration`（板框 bbox + stackup 厚度 + 元件当探针点），以及 6 个振动类工具与 73 个测试（含 `tests/test_vibration_stress.py` 的 21 例）；边界是**薄板、单模态、线性**：不算剪切变形、元件附加质量、装配柔度、焊点蠕变、随机+冲击叠加、跌落、非线性接触。应力**不是解出来的场**，而是由同一组 Ritz 曲率算出的三个极值（板角 von Mises / 跨中主应力 / 板边剪应力），张量分量只在 `sigma_max` 点报。
- `21` → `calc/spice.py` 的外部引擎段（`find_ngspice` / `_deck_for_ngspice` / `_run_ngspice` / `_parse_raw` / `_cross_check`，以及 `simulate(..., engine=…, cross_check=…)`），`tests/test_spice_engine.py`（37 例）+ `tests/_fake_ngspice.py`（假引擎）。外部路径已对着**真 ngspice-47**（MSYS2 Windows 构建）跑过并逐节点对账（分压 5e-9 V、RC 阶跃与 RLC AC 逐位 0.0），再用**第二个构建 ngspice-41**（conda-forge）跑同一批 deck 做**跨版本对拍**（`.op`/`.ac` 逐位 0.0，`.tran` 最多差 1.24e-4 V，两构建步长控制不同）；`verified_against_engine` 按**引擎版本**门控，只有 `VERIFIED_ENGINES`（`ngspice-47`、`ngspice-41`）里的版本为 true，别的二进制一律 false（门控本身由假引擎自称版本号来测，不需要真引擎）；默认引擎是 `builtin`（同一网表在两台机器不能给不同答案）。
- `20` → `calc/cavity.py`（平行板腔体模 + 过孔围栏比例）、`calc/electrothermal.py`（R(T) 定点迭代 + 精确失控判据 + 板级 2-D 电热场 `board_electrothermal`）、`calc/power.py` 的 `pdn_plane_modes`/`pdn_impedance(plane=…)`，以及 6 个工具与 89 个测试（`test_vibration` 之外：`test_cavity_electrothermal` 31、`test_electrothermal_plate` 31、`test_pdn_plane` 27）；边界是**没有边界失谐修正**，2-D 场只沿调用方给的层压板 k 扩散（不含真实铜面/平面导热），3-D、热过孔、组件热容都缺；平面模只给峰值不给曲线。
- `22` → `calc/filters.py`（原型 g 值 / 集总带通的两路 S21 对账 / 耦合微带 Newton 反解 / 耦合谐振器带通 / 矩形腔 TE101 的 Qc 双推导），以及 5 个 `filter_*` 工具与 31 个测试。边界是**合成而不是仿真**：给了几何，不给 EM 结果——无交叉耦合、无色散、无有限 Q、无 iris 几何、无版图、无良率。
- `24` → `lceda_live.py`（命管道枚举 + ping 确认 / 一行一 JSON 帧 / 9 个 op / `invoke` 在编辑器 render 进程里跑 async 函数体）与 6 个 `lceda_pro_*` 工具、26 个测试（24 个假 transport 跑失败路径 + 2 个真机用例）。边界是**只证了读**：ping / open（849 ms）/ session.list / session.close / doc.api（378 类型、PCB_Document 21 方法）/ invoke（两块板名 + uuid、抛错回 EXECUTION_ERROR）都在真机上跑过，任何写、`open` 是否碰工程字节、`destroy` 是否丢未保存改动都未验证。
- `25` → `calc/magnetics.py` / `calc/stackup.py` / `calc/chain.py` / `calc/rules.py` 与 8 个新工具（`magnetics_inductor` / `stackup_material` / `stackup_design` / `rf_chain` / `match_bandwidth` / `design_rules` / `layout_review` / `schematic_review`）+ `matching.bandwidth`，36 条规则（22 布局 + 14 原理图）。边界写在每条规则上：事实缺失即 `undetermined`（空事实集直接 `ARG_MISSING`），电流限值必须显式给标准，IEC 60664 隔离间距因表未验证而永远 fail-closed，电感饱和没有器件就是 undetermined。
- `26` → `edit/plan.py` 的分步写（`steps_apply` / `step_events` / `step_control`）、`edit/drc_policy.py`、`parts.py`（离线 `lceda-std.elib` + 在线检索）、`fidelity/fill.py`（`pcbnew ZONE_FILLER` 委派 + 节点移植）、`oracle.routability` 的几何逃逸判据、叠层总厚的 `thickness_agrees` 闸门，以及 `board_progress` / `board_zones` / `board_fill_zones` / `part_*` 共 11 个新工具；边界是**立创 in-app 分步写真机未验证**（本机非交互会话起不了 `lceda-pro.exe`）、在线器件**只有元数据没有几何**、routability **不是布线器**。
- 代码里的每条"拒绝回答"分支，都能在这二十六篇里找到对应的判据来源。
