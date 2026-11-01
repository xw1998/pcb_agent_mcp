# pcb-agent-mcp

让 AI 在 **立创 EDA Pro** 与 **KiCad** 里真正"算得出来再动手"的 MCP 服务。

当前版本 `0.0.81` 已实现十层：只读的算/保真/适配/度量/仿真，一条**带闸门的写路径**（提案 → 落盘，两种格式；还能**分步落盘**，让人在 EDA 窗口里看着它一步步长出来并随时喊停），一个**会说不通在哪**的布线器，一套**外包给立创/板厂自己的裁判**的制造输出与原理图同步。
立创侧的每条口径都以 **194 个真实 `.epro` 归档**为证据（[docs/13](docs/13-立创实测-真实存储与记录格式.md)）；
KiCad 侧的几何与规则对着厂商自己核过：13 块真实板的 DRC 对账（厂商报的 56 条全命中）、
412 个焊盘与 `pcbnew` 逐点对账 0 不一致（[docs/15](docs/15-对账-推导对着厂商判决.md)）。

> **只想判断「现在能不能用」？** 直接看下面隔一节的
> **「一页速查：做完了什么 / 还差什么」**——三十秒扫完；本表是逐层机制。

| 层 | 做什么 | 状态 |
|---|---|---|
| **Calc** | 无板子的工程计算（载流/电流场/阻抗/差分阻抗/λ/20/爬电） | 已实现 |
| **Fidelity** | 把整份板文件**转换**成 AI 可读的表达：每个节点都进树，事实从树里读，往返由 KiCad 自己验证 | 已实现（KiCad 侧已被 kicad-cli 判决） |
| **Adapter** | 在保真树之上给一个 Canonical 便利视图（mm、Y 上、net 用名字） | 已实现（KiCad 侧：13 块真实板与 DRC 对账、412 个 pad 与 `pcbnew` 逐点对账；立创侧：194 个真实归档实测） |
| **Oracle** | 在解析出的板子上做度量（clearance / free_space / 铺铜连通 / 跨分割 / 环路面积 / 单网 IR drop） | 已实现 |
| **Sim** | 无板子的物理计算：SPICE 子集（DC/瞬态/AC）、铜阻与 PDN、热阻与 2-D 板温度、薄板模态与振动响应、平面谐振、电热耦合（单集中 Rth） | 已实现（子集，未实现项逐条点名拒绝（`0.0.37` 起 `A` 卡由随包 ngspice 承担；`0.0.39` 起 `gain`/`limit`、`0.0.40` 起多端口的 `summer`/`mult` 这四种代数 code model 由内置自己解） |
| **Desktop** | 找得到/启得动/关得掉 EDA，能打开工程并用窗口标题确认 | 已实现 |
| **Write** | `propose`（只算不写）→ `apply`（checkpoint + 原子写 + 六道闸门 + 失败回滚） | 已实现（KiCad 真机验证；`.epro` 实测口径＝原地重写一个成员，`0.0.62` 起**写出的归档再喂回应用并回读几何**验证过） |
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

## 一页速查：做完了什么 / 还差什么

这一节回答一个问题：**现在能不能用、哪一格还不能信。** 三十秒扫完；机制与数字在下面
「已知未实现 / 已打折扣的地方」与 `docs/` 对应篇目里。

判定口径只有一条：一个能力同时满足 ① 有工具 ② 有闸门或参照 ③ **有一条能失败的对照**
（负控制，或对着厂商判决的对账）才算「做完了」。只写了代码、没有对照的，一律算没做完。

### A ｜做完了（有工具、有对账、边界写在每次返回里）

| 能力 | 起于 | 凭什么说做完了 | 它自己的边界 |
|---|---|---|---|
| **工程计算**：载流 / 阻抗 / 差分 / λ/20 / 爬电 / 间隙 / 铜阻 / 电感 / 链路 | `0.0.1`，真实表 `0.0.10` | IEC 60664-1 阶梯从 KiCad 10.0 原实现反演，**188 528 点 0 失配**（32 键 / 808 步）；IPC-2152 用 Brooks & Adam 闭式 + 3 个独立锚点复现 | 闭式近似，误差进 `precision`；IEC 62368-1 / 61010-1 / UL 60950 / IPC-2221 仍 fail-closed |
| **二维场解**：差分截面 / 共面单线 / 共面差分对 / 有限宽地 / 导体损耗 | `0.0.19`~`0.0.26` | 零厚度带状线对精确共形解 ≤1.2%、孤立微带对 Hammerstad ≤2.8%；共面差分对无闭式，改用叠加恒等式（残差 1.9e-8）+ 奇模电荷平衡 −1.000000000 + 极限 | 紧间距耦合**没有独立参考答案**（回 `reference_note`）；铜厚只划网格不建模；共面解比闭式**读低 1.19%~3.39%** |
| **保真层**：整份文档进树，往返可证 | `0.0.1`（`0.0.18` 补口径） | KiCad 侧 token 双射 + 逐字节往返；立创侧 194 个真实 `.epro` 归档实测 | 13 块 demo 板覆盖不到的少见语法可能没走到 |
| **Adapter**：两份格式 → 一个 Canonical | `0.0.1` | 13 块真实板上与 `kicad-cli 10.0.7` 对账（厂商 56 条 recall 1.0）、412 pad / 20 188 次 HitTest / 0 不一致 | 立创焊盘位置是推导（54.1% 命中），底面镜像只有弱证据（35.1%） |
| **Oracle 度量**：间距 / 可放空间 / 铺铜连通 / 跨分割 / 环路 / IR drop | `0.0.1` | 每条带 `authoritative:false` + 裁判在哪；能对账的间距项双向数 recall 与 extra | 三类几何**不建模**（zone 铜、板边与孔规则、阻焊钢网）；比不了的明确 `not_comparable` |
| **写路径**：提案 → 落盘 → 闸门 → 回滚（两种格式） | `0.0.1` | KiCad 五道闸门（含 DRC 差集）在自造板上**真拦下过三次错**；立创六道闸门，实测同一工程两次保存只改一个成员 | 立创原理图 `.esch`、SQLite `.eprj2` 一律拒写 |
| **原理图**：KiCad 单页 22 op + 多页 4 个 sheet op | `0.0.1` / `0.0.33` | 裁判换成 KiCad 自己的网表 + ERC 双向 diff；页号按**工程全局**分配 | 不做跨多文件原子写、页面模板、搬文件、批量重命名 |
| **布线器**：A* + 精确多边形判决 + 事务化 + 按位置 rip-up | `0.0.14`~`0.0.17` | 50 网真板 27/50 → 38/50（开协商 41/50）；端到端 apply 后 `kicad-cli DRC` 新增阻断项 = 0 | 四邻 + 4×45°、不拆已接受扇出、拓扑非精确 RSMT、拥塞协商默认关 |
| **制造输出与同步**：Gerber/Excellon/STEP/PDF/BOM/网表 + 原理图↔板差集 | `0.0.18` | 产物**重新读一遍**验终止符/工具表/行数/ISO-10303-21；`out_dir` 落在源目录里直接拒 | 只外包 `kicad-cli`，不重写 writer；`schematic_pcb_sync` 一个字节都不写 |
| **仿真（内置 SPICE）** | `0.0.7` 起，`0.0.27`~`0.0.39` 逐类长出来 | 器件 `R C L V I E G F H K D Q M B S W J` + `X`/`T`/`O`/`U` + `.noise` + `A` 的 `gain`/`limit`/`summer`/`mult`，每类都有对账（例：1 kΩ 读 `4.071372237e-09 = sqrt(4kT·1000)`） | 后向欧拉；无自热、无失真/傅里叶、无器件电容随温度；`A` 里只有四种纯代数 code model 进内置；1/f 只做**二极管**（`KF*|Id|^AF/f`，`Q`/`M`/`J` 的 `KF=` 在 `.noise` 按名拒答），`mc(...)` 这类统计值一律拒答，`.temp` 多值是扫描（`0.0.54`） |
| **仿真（外部引擎 + 随包）** | `0.0.7` → `0.0.37` 随包 | 真 ngspice-47 逐节点对账（分压 5e-9 V）；随包副本与系统副本六类 deck 差 0 ~ 1 ulp | 只 Windows x86_64；`engine="auto"` **刻意不看随包**；不做 DLL 进程内 |
| **热 / PDN / 振动 / 平面 / 电热** | `0.0.5`~`0.0.6`、`0.0.10` | 2-D 板温度 `73.393487 ℃` 对闭式 `73.39345`；Ritz 模态 SSSS 330.385 Hz、CCCC 602.382 Hz 与解析解吻合 | 1-D/2-D 稳态、薄板单模态、平面只有模频与导体/介质损耗的 Q 上界、电热单集中 `Rth` |
| **熔断（沿长度）**：锥形/分段走线的温度梯度与单发熔断 | `0.0.59` | 均匀段 20.21 A 落在 **0.993269 s**（对解析绝热式 0.993880 差 −0.0614 %，对 Onderdonk 的 1 s 差 −0.6731 %）；分段数 n=1..40 **差 <1e-9**、均匀段剖面极差 0、`conduction_delay_pct` 精确 0（对照臂是真跑一遍 `k=0`）；颈部 0.5 mm 0.039764 s 对平均宽均匀段 0.419000 s ⇒ **10.5×**；端固定（`ends_c`）才有稳态阈值 3.049725 A，代回峰值 1083.0017 ℃ | 侧向散热**无 `h`**（绝热侧面）、段内横向等温、无潜热/集肤/电迁移/电弧；不熔只回 `value: null`；绝热端没有稳态阈值（回 `null` + 理由）；`precision` 里**不给统一误差符号**（两个方向相反） |
| **电流场（2-D）**：拐角/瓶颈的 `J` 与热点位置 | `0.0.63` | 均匀条带 `R` **0.004901063** 对 `rho/t·L/W` **0.004925714**（−0.50 %，差值全来自触点在格胞中心）；直角弯的 270° 内角 `J ~ r^(-1/3)`，峰值随 h 减半按 **1.266 / 1.262** 涨（理论 `2^(1/3)`=1.2599）⇒ `peak_is_grid_convergent=false`，只有环衰减 **0.7963**（理论 0.7937）才是网格无关读数；45° 斜接 **−30 %**、内角补铜 −36 %、**切掉内角尖端的"倒角" +93 %** | 2-D 薄片、DC、均匀 `rho`/厚度（无 `I²R` 回馈）、接触是硬边等势块（其边缘本身奇异）、截面按轴投影 ⇒ `crowding_factor` 是**下界**；多连通直接报错并给 `Components found: N`；未收敛不给数字（`NOT_CONVERGED` + `failed_level`） |
| **铺铜也是铜**：把 zone 当 2-D 薄片并进 IR-drop 网络 | `0.0.64` | 真机 `+3V3` 从 `NOT_APPLICABLE: singular at 21`（走线建图被 `In2.Cu` 一块 17832 mm² 平面分成两半）变成 **57.2440 mV / 13.4 s / 7052 节点 / IC(0) 907 迭代 / residual 9.4e-10**；解析锚点：1×10 mm 焊盘 + 30×10 mm 铺铜 ⇒ **1.9 方块 = 0.9359 mV**，实测 **0.935986**（差 0.01 %），而「长度×方块电阻」的 2 方块 = 0.9851 mV 是**上界**（+5.3 %，焊盘只 1 mm 宽、电流会散）；栅距按 **shoelace 填充面积**规划（bbox 规划在 `B.Cu` 上只给 11 格），格间电阻**与栅距无关** ⇒ `h=1.0` 与 `0.5` 差 <2 % | 开关默认关，关掉时读数**逐字不变**（真机仍 `singular at 21`）；**热焊盘辐条没有建模** ⇒ 那里是**下界**（本条唯一偏乐观的方向）；跨 ≤0.5 mm 的缝按**一个方块**桥接（warning 自称最粗的一个数）；zone priority / 最小宽度 / 与其他网间隙取文件里的填充，不解；孤岛丢弃（报数）；>2000 格的焊盘退化成点接触并告警；纯 DC（无 skin effect、无平面谐振） |
| **负载跳变要的是曲线**：时域 droop 与 \|Z(f)\| 是两个问题 | `0.0.65` | 同一张网络：`1 Ω`+`1 µF`+`10 mΩ` 的 `1 A` 阶跃在 1τ 处 **−0.634964 V** 对闭式 **−0.635763**（0.126 %），DC 尾落在 **−1.000 V**；`100 µF`/`2 A` 的尾部 **−0.039999954** 对 `-I*R` = −0.04（`dc_check_rel` 1.16e-06、`settle` 12.7 µs），电荷恒等式 `Σ(-C·Δu)` 对上 `∫(I_load−i_vrm)dt` 闭合到 **7.6e-15**；LC 环 3 个半周期 反推 **653.07 ns** 对 `2π√(LC)` 628.32 ns；全部电容 `at='die'` 时 **`plane_r_ohm`/`plane_l_h` 照样生效**（吃进调节器支路）：峰值 **−0.032524503** vs bulk 摆位 **−0.052936594**（1.63×），而两条 DC 尾都是 **−0.030000**；**同一网络丢给 ngspice 逐点对表**：一阶最大 0.126 %、LC 峰值低 1.27 %、bulk 0.42 %，一律偏低（反向欧拉的阻尼 ⇒ 报的 Q 是下界） | VRM 是 **Thevenin**：没有环路带宽/相位裕度/限流/多轨 droop 分摊，**这条轨在这里通过不代表真实环路不会自己振**；板是 1~2 个集中节点；无 die/封装电感与片上电容；**无电容 DC bias/温度/老化降额**（`c_farad` 就当在工作点上）；只有单台阶；理想阶跃进电感**拒绝**（`V=L·dI/dt` 由步长决定，实测 0.25 ns/20 A ⇒ 16 V、`h_refine` 100 %）；`h_refine_peak_delta_pct` 只界定峰值不界定衰减率 |

| **铜到多少频率就不该再按 DC 算了**：趋肤效应的 1-D 精确解 | `0.0.66` | 薄板内沿厚度的扩散方程有闭式解 `R_ac/R_dc = s(sinh2s+sin2s)/(cosh2s−cos2s)`，`s = t/δ`；1 oz 铜 `R` 高出 DC 1 % 的频率是 **1.198257 MHz**、10 MHz 是 **1.5408001 倍**（`δ` 20.897 µm，`s` 1.6749 ⇒ **还不是** `√f` 区）、`f_10%` 3.864536 MHz、`f_2x` 15.535544 MHz；`s=1` 锚 **1.0856357047503276 / 0.6503925810415222**；小 s 展开 `R−1 = 4s⁴/45`、`X = 2s²/3`（后者与板内感 `ωμ₀t/3` 独立吻合）；大 s `R = X = s` 45°；**另一条独立解**（复三对角 FD，n=501..8001）差 0.2004→0.0125 %，精确按 O(dz²) 减半；`faces='two'` 在 35 µm 上**逐位等于** `faces='one'` 在 17.5 µm 上（差 <1e-12）；`current_density_field` 加 `frequency_hz` 后`R` 与 `peak_j` 同乘 1.5408、`peak_j_gross` 与 `crowding_factor` **不动** | 只有 **1-D**（沿厚度）：横向重分布、边沿拥挤、邻近效应、平面内涡流、过孔场、位移电流**都不在解里**（`limits.not_modelled` 6 条）；粗糙度只作 Hammerstad 乘子且默认关（不给 `Rq` 时明说是**下界**）；升温不在迭代里（100 ℃ 时 `dc_sheet` 4.9257e-4→8.7122e-4 而 `R/Rdc` **降到** 1.3456，因为 `δ` 随 `ρ` 变大）；`f_1%`/`f_2x` 随厚度 ∝ **1/t²** ⇒ 2 oz 的 `f_1%` 是 1 oz 的 **1/4**，且同频率下越厚 `R/Rdc` 越大（70 µm@1 MHz 1.1068 对 35 µm 1.0070）——「厚铜更好」只对**绝对** `R_ac` 成立 || **平面边缘**：开边辐射 Q、开边失谐、Huray 结节 | `0.0.60` | 50×50 mm/0.2 mm/εr 4.3 的 (1,0)：`Q_rad` **179.167** = 手算 `εr·a/(6h)`，`Qc` 115.071 ⇒ `Q_total` **70.0689**、(1,1) **134.375**；`k₀h=0.00606004` 过闸、`h=100 mm` 拒；开边 Δl **0.266086 mm** ⇒ 位移 **−1.05313 %**，1.6 mm 上 **−6.01208 %**，`(1,0)` 只开 y 边位移**精确 0**；Huray 1 GHz/r 0.5 µm/sr 1 给 `K` **1.11953**、knee **69.8767 GHz**（`δ = r/2`） | 辐射按窄缝 + 无限 PEC 法兰、无角点、介质与铜同时截止，对 3-D 求解器差**几十个百分点**；开边是大长宽比渐近式（`a/h ≥ 10`）、不改 Q；Huray 只乘铜的 `Rs`、不与 `Rq` 互推、`r > λ₀/100` 拒绝；三个工具都**不给 pass/fail** |
| **立创真机写入**：`lceda_pcb_write` 的 create / delete / move / save | `0.0.61` | 在 V4.1.60 上：create 探针 `0 → 1`、save 让工程文件 **409600 → 569344 B** 且 sha 变、delete `1 → 0`、`modify(id,{startX:2})` 后被**两次重启**仍为 `(2,1)-(21,1)`；第二轮走工具层（`lceda_write_snippet` 生成 → `lceda_pcb_write` 执行）复现同一结论 | 只证明「写进去了」，不证明「写对了」：参数写错抓不住、屏幕未核对（`.epro` 归档那条另验于 `0.0.62`，走的是导入不是这条桥）；闸门按桥版本表放行，不传版本的答案与 0.0.60 **逐字相同** |
| **立创归档写后重开**：`board_apply` 写出的 `.epro` 是否被应用接受 | `0.0.62` | 应用自己的导出件（控制臂）回读 `[[2,1,21,1,0.25],[1,5,21,5,0.3]]`；本模块改 `set_track e1 width 0.5 mm` 后回读 **`[[2,1,21,1,19.685],[1,5,21,5,0.3]]`**（19.685 mil = 0.499999 mm，没点名的线仍 0.3）；负控制 A（记录流换成垃圾）**导入仍返回 ok 但回读 `[]`**，负控制 B（不是 zip）导入返回 `undefined` | 只对 **V4.1.60（bridge `4.1.60.198f38ab`）**、只 ZIP、只 `ImportDocument` + `New Project` 这条门成立；屏幕未核对；`.esch` 与 `.eprj2` 仍拒写 |
| **磁件选型**：真厂商表 → 匝数 / 磁密 / 气隙 / 损耗点 / 窗口 | `0.0.55`、`0.0.74`~`0.0.81` | 12 种材料 / 16 个磁芯每行带来源 URL；36 例负控制（缺 Bsat 不插值、单点损耗不外推、少一匝必越限、4× 频率同 B 必须 `null`）；`0.0.76` 加**粉末磁环**（Magnetics 粉末芯目录印刷 p.155–197）：**43 个尺寸 / 500 个磁导率行 / 1923 个可买料号**，九列材料按**坐标**取（厂商对空单元格根本不印，按词序解析会在缺格处整行右移），六条互检：`Ve = Ae·Le` 42/43（唯一例外 p.193 是印的 Ve 自己错）、`Le` 对对数均值周长 43/43 < 1 %、`Ae` 对外接矩形 0.90–1.00、**料号材料码 vs 列 1921/1923**、`AL/µ` 分组中位仅 3 处超 12 %（渲染确认印刷即如此）、每列磁导率 ⊆ 厂商印的范围；5 条印刷异常**原样保留并登记**（含同一个 `0058133A2` 印在两个尺寸上）；`0.0.77` 加 **DC 偏置**（同一份目录的 `Permeability versus DC Bias Curves — Fit Formula` 三页，印刷 63–65）：`% µi = 1/(a + b·H^c)`（H 单位 Oe），**29 组 / 125 行**，`a` 恒 0.01 ⇒ 输出就是百分数本身；五重校验：p.10 汇总表的 **18 个 80 %/50 % 数字全中**（最差 MPP 50 %：106.00 对印 105 = 0.96 %，其中 15 个在 0.1 % 内、第 16 个 0.12 %）、印刷 Example 2（60µ Kool Mu / 20 匝 / 20 A ⇒ 58.408 %，原文 0.58）、印刷 Example 3（H=15.8 Oe ⇒ 积分 0.0936 T，印 0.092 T）、p.52 曲线 600 dpi 像素读数（网格线自标定，10 条曲线里 7 条在自己磁导率上 5 点吻合 0.11–1.08 %）、结构一致性（`a` 全 0.01、`b,c > 0`）；`b_t` **必须积分** `1e-4·∫µr dH`（95 Oe 弦式只给 0.285 T，积分 0.4421 T 对印 0.441 T）；`0.0.78` 把 DC 偏置**接进选型**：回路 `H = 0.4π·N·I/le[cm]` → `µ = 1/(a+b·H^c)` → `L = AL₀·N²·µ/100`，**不做不动点**——`N^c = 2a/((c−2)·b·k^c)` 只在 `c > 2` 时成立而 **86/125 行 `c > 2`**，峰右侧多绕反而更少电感（XFlux Ultra 60µ / 8 A：82 匝 100.256 µH，1000 匝只有 22.73 µH）⇒ 三步区间搜索（定位 `n_peak` → 峰左侧二分找第一个够 L 的匝数 → 只在磁密超时继续加匝）；100 µH/10 A/8 A 偏置答 `0074121A7` **82 匝对零偏置 54 匝**、`H 200.085901 Oe`、`µ 42.600515 %`（用该行 `(a,b,c)` 反算一致）、`B 0.636789 T`（限 1.28）；通量**用偏置后的 L 算**、AL 余量对**偏置后的 AL**比（`al_ratio 0.997446`，`gap_required = null`）、带外**标记不 clamp**；`0.0.79` 补上**环型平均匝长与铜损**：MLT 属于绕组不属于磁芯，所以走「矩形截面周长 `2H+(OD−ID)` × 13 个印刷点的**实测比值带**」（`1.247656…1.460472`，中位 `1.365936`，每行带 URL），`r_dc` / `p_copper` 一律**给区间**（T106-1 + 54 匝 φ1.0 mm @100 °C：`R_dc 0.073719 Ω`（`0.067335…0.078821`）、`P_cu 1.842964 W`；厂商自己印的 43.9 mm **落在区间内**）；顺带自检 `le = π(OD+ID)/2` 残差 **−0.61…+0.45 %**、`Ae = H(OD−ID)/2` 比印值**高 4.4…9.7 %**（圆角）；`layers>1` 报 `biased LOW`、窗口放不下给 `window_note` | 无 Rth（不报温升）、**厂商自己不印 Steinmetz 三元组**（Ferroxcube 只印 2–3 个 PV 点、TDK 只画曲线）⇒ `0.0.81` 起改为把 **17 个印刷损耗点**入库并提供 `magnetics_core_loss_audit` 让你带来的三元组对着厂商原印点打分，**不拟合、不编系数**、**B-H 曲线与 Δµ/µ(T) 自 `0.0.80` 起有印刷系数可查**（`magnetics_powder_curve`，见该行右侧说明）、**DC 偏置已接进 `select_core`**（`dc_bias_current_a`）但只按印刷 fit 算、**不随温度**、**不建模 `µ(B)` 自洽饱和**（`b_t` 只与 `0.8×Bsat` 比作对照）、达不到 L 的零件判 `dc_bias_unreachable` 而非给数；窗口只有 1 个部件有骨架数据；磁环**目录确实不印 MLT**（这一列仍是 `null` + 具名 missing），但 `0.0.79` 起 `select_core(toroid_mlt="estimate")` / `magnetics_toroid_mlt` 会给**几何 × 实测比值带的区间估计**（`1.2477…1.4605`，n = 13，带 `is_estimate` 与 `*_range_*`），不再是 `null`；多层给下界、集肤与温升仍不算、`gap_mm` 是 `null` 不是凑出来的、200/300/550 µ 没印密度因子 ⇒ `mass_g` 拒绝插值、标称重量不印 ⇒ 质量是 `Ve×密度×因子` 的估计值；**两条印刷曲线在端点上差 54 %**（`MPP Toroids 550µ` 100 Oe：B(H) 0.2341 T 对偏置积分 0.5128 T）⇒ `magnetics_powder_curve` **两个都报**、`fits_disagree` 标出来，不取平均不挑一个（全表 125 行中位仅 0.48 %）|
| **DFM / 规则 / 流程工具** | `0.0.4`、`0.0.12`、`0.0.56` | 15 条规则目录 + 五态判定（0.0.56 把丝印线宽/字高、阻焊 web、铜到**成品**孔壁从"不检查"变成实测）；22 条布局 + 14 条原理图规则，缺事实即 `undetermined` | 没有内置板厂表，也没有内置安规默认值；丝印不写宽度只报上界，`plating_um` 不给就拒答成品孔壁 |

### B ｜没做完，分三种，**别混读**

#### B1 ｜结构上不做（不是排期问题：做了就是错答案，或者要外部求解器）

| 不做的事 | 现在给什么替代 | 为什么不做 |
|---|---|---|
| 全波电磁（方向图 / 互耦 / SAR / iris / 色散） | `fullwave_solver` 是个**接缝**：装了什么、版本、要你给它什么；`verified_here` 恒 `false` | 用闭式冒充全波，正是本项目最想消灭的假答案 |
| 自己写一个完整 SPICE | `0.0.37` 起**随包**官方 ngspice-47（独立进程，不 link） | 抄开源比重写诚实，MIT 与混搭许可也能共存 |
| 内置板厂工艺能力表 | 只给 15 条规则目录 + 体检，限值必须来自你的能力文档 | 猜一个线宽等于替板厂签字 |
| 安规默认值 | IEC 60664-1 有表；其余族 `SAFETY_TABLE_MISSING` | 标准付费、公开转述互相矛盾 |
| 热学 `h` / `Rth` / Steinberg 允许挠度 / 焊点 `ε_f` 的默认值 | 参数必填，缺就 `ARG_REQUIRED` | 那是「引线类」的数，不是你这颗器件的数 |
| 元件库几何 | 在线检索只给**元数据**；离线 `lceda-std.elib` 给封装 | 没有几何就画不出焊盘，编不出来 |
| 立创的 DRC 判决 | 明确拒绝给判决（立创无命令行），不近似 | 拿自己的几何冒充「厂商判过」是第二种假答案 |
| 3-D 热 / 器件自热 / 交流损耗进 SPICE | 全部写进 `not_computed` | 需要场解与频率相关模型，超出子集 |

#### B2 ｜现在拒绝，**门是开着的**（前置条件写在那里）

| 现在拒什么 | 返回什么 | 缺的前置 |
|---|---|---|
| 立创原理图 `.esch` 写 | `WRITE_UNSUPPORTED_FORMAT`（并按内容说出它是 zip 还是 SQLite） | 一次真实保存的**记录布局实测**（现在只有读） |
| 立创 SQLite 工程 `.eprj2` 写 | 只读打开，写一律拒 | `history_data` 加密 + `ticket` 单调，手改会让工程打不开 |
| 立创进程内写（`lceda_pcb_write`） | 0.0.61 起**真机跑通过**：create / delete / move / save 在 LCEDA Pro **V4.1.60**（bridge `4.1.60.198f38ab`）上做过，探针推动、工程文件变字节与 sha、关掉重开、**两次整应用重启**后几何仍在；`verified_against_app` 由 `VERIFIED_WRITES` 表放闸 | **只覆盖这一条桥版本**：其它版本、`write`（自定义片段）、探针没动的写一律仍答 `false`；探针读到的不等于屏幕上看到的，参数写错的片段抓不住；`.epro` **归档**写路径的「应用接受」已另验（0.0.62，见下条），但**走的是导入而不是这条桥**；同时刻也没有文档上下文 |
| 立创 `.epro` 归档写 | 0.0.62 起**不再整条拒**：写出的归档已被导入 LCEDA Pro **V4.1.60**（bridge `4.1.60.198f38ab`）并按几何回读验证（`ACCEPTED_ARCHIVES['epro_zip']`），`apply_plan` 的 `app_acceptance` 逐次给出读数 | **只对这一条 app build / ZIP 容器 / `ImportDocument`+`New Project` 门**；换 build 就是换仪器，SQLite 与 `.esch` 仍 `WRITE_UNSUPPORTED_FORMAT` |
| 立创分步写 `lceda_pro_steps` | 工具在，真机未验证 | 只能在用户手动开「外部交互权限」的会话里验 |
| websocket 网关 `lceda_api_probe` | 端口扫描 + 握手，本机 49620-49629 全部 `no_listener` | 同上 |
| `app_open_project` 的 argv 形式 | 只回 `unconfirmed`；开工程请用 `lceda_pro_open` | 无法确认 `lceda-pro.exe <工程>` 真的会打开 |
| `set_pad_net` 的「应用也这么写」 | `verified_by_app:false` | 从未观察到应用自己写那一行 `PAD_NET` |
| `A` 卡的其余 code model（有状态 / 采样时钟 / 除法类） | `builtin` 按名字拒绝并点名（`integrator`、`divider`…） | 那部分是第二套器件描述语言（状态、采样时钟）；纯代数的四种（`gain`/`limit`/`summer`/`mult`）已在 `0.0.39`/`0.0.40` 进内置，`%v()` 端口写法仍拒（引擎也不收） |
| 88.7 MB 级大板的 DRC 出报告 | 0.0.43 起 `board_drc(background=true)` 只交回 `jid`，用 `board_job` 取回（运行中**没有结果**、失败留 `error`）；超时仍报错，**不降级** | jetson 板实测 823 s / 2543.8 s 都没出报告——后台作业只解决「等不起」，**不解决引擎自己失败**；`cancel` 只丢结果、不杀子进程 |

#### B3 ｜做了但打折（能判断方向，不能拿去签核）

- **阻抗**：闭式系统性**偏低 8~11%**（方向一致），所以「调宽凑数」会先把线做窄；要紧的几何用二维场解。
- **共面**：铜厚不建模，真实铜箔损耗高约 10%，而那 10% 自己也没收敛；地默认按无限宽算，窄地是**共模**问题（`g/2` 时 `z_even` +21.9%）。
- **串扰**：是**两导体、准 TEM、两端同阻抗**的精确解，不是「任意互连的串扰」——异阻抗受端、第三条线/屏蔽线、色散铜损与辐射、`spacing/h < 0.05` 都**直接拒绝**；均匀介质里 `FEXT` 精确为 0（算术抵消，不是小），微带的 `FEXT` 只在相干长度 `tr/|1/v_odd−1/v_even|` 内 ∝ L，长线饱和到 `amp/4`——所以读 `FEXT` 前先看 `fext.scales_with_length` 与 `coherent_length_mm`。
- **脉冲降额**：`repetitive_pulse_rating` 是**一阶集中温度**（整根线一个温度），不是 3-D 热；`h` 不给默认值（裸板静风 ~10 到铜皮锁金属壳的数百，差 20 倍），`h/2` 与 `2h` 的稳态峰值一并报出（实测差 **245 %**）；不算长度方向温度梯度、板与铜面作为 `h` 之外的独立散热、熔化潜热、脉冲内集肤、电迁移、开铜后的电弧。**「单发」不是「列车」的近似**：1 mm/1 oz 单发熔断 318 A，占空比 10 % 的列车只许 10.3 A。失控阈值不含热容（是两个速率的比较），所以 `runs_away` 是**存在性**判断，不代表稳态温度低。
- **振动**：薄板 Kirchhoff、单模态；`board_vibration` 的主答案仍是板框 bbox（非矩形会**偏软**，回 `outline_fill_fraction_of_bbox`），0.0.42 起另给 `plate_on_outline`＝**真实外轮廓**的解（L 形实测差 3.13×）；应力是三个极值，不是场。铜与质量 0.0.57 起进**四样**：`copper_um`/`copper_um_bottom`（不对称叠层按**折减刚度** `D* = D − B A⁻¹B` 解，且明说这是**平态附近**的线性化——真板会弓起来，那个平衡态不解）、`added_mass_g`（一个点）与 `added_mass_regions`（一块 footprint 上均布，或整板）、多边形轮廓路径的铜；有铜时应力取**最外层**那一面并**用那一层自己的 (E, ν)**（0.0.56 用铜的 E 配层压板的 ν，1 oz 上 10 % 量级偏差，本版登记修正），所以 `sigma_max` 会跳上去、`von_mises` **不**跟着等比跳（`τ/σ=(1+ν)/(1−ν)` 变了）。**元件刚度**仍不进（质量进了、弹簧没进），层压板仍是各向同性层。
- **轮廓振动**：只解外轮廓（孔不网格化），`alpha_spread_pct` 是罚阶梯最平一档的步长（>3 % 只作指示），凹角与高边数**经常不收敛**，`modes[i].physical=false` 的阶是伪刚度不是模态。
- **平面谐振**：只给模频与 λ_g 比例，另给**导体 + 介质损耗**的 Q 上界（`cavity_plane_q`：`Qc = h/δs`、`Qd = 1/tanδ`，两项之和是 Q 的上界）；辐射 Q、真实开边失谐、Huray 结节从 0.0.60 起各有工具（`plane_radiation_q` / `plane_open_end` / `roughness_huray`，见 [docs/63](docs/63-开边辐射Q与Huray结节.md)），但**箔 profile 仍不算**；「带内有哪些模、各多高」用 `pdn_plane_modes` 的单模峰值，「整条曲线多深」用 `pdn_plane_impedance`（0.0.49：有限焊盘孔径让模态和收敛，阶数闸门 + 平板电容锚点；点端口 2 GHz 从 order 8 的 5.71 Ω 爬到 256 的 19.23 Ω 无极限，0.5 mm 焊盘 256→384 只动 0.1 %）。**但曲线只是平面自己**：没有电容/封装/过孔 ⇒ Q 是上界、峰偏高偏窄、1.56 GHz 的谷（1.76 Ω）比真板更浅。平面与电容组的**反谐振**：0.0.50 起 `pdn_plane_with_caps` 把 ≤8 颗电容的 RLC 并在腔体网络的电容端口上（端口降阶是**恒等式**，不是拟合），一条曲线里同时有平面自己的模与电容的并联/反谐振（实测 1 颗 100 nF/1 nH 把 2.86 GHz 的 92.45 Ω 变成 418 MHz 的 164 Ω 反谐振峰）；它只算**你点名的那几颗**，不选型、不优化，封装/过孔电感要你自己折进 `esl_h`，3-D 与相邻平面耦合不算。**导体粗糙度**：0.0.51 起 `cavity_plane_q` / `pdn_plane_impedance` / `pdn_plane_with_caps` 收可选 `roughness_um`（RMS `Rq`，不是 `Rz`），按 Hammerstad–Jensen `K = 1 + (2/π)·atan(1.4·(Rq/δs)²) < 2` 只乘到 `Rs` 上 ⇒ `Qc = h/(δs·K)`、`Qd` 逐位不动；不给时整条输出逐位不变。
- **PDN 负载阶跃**（`pdn_step_response`，[docs/68](docs/68-PDN负载阶跃.md)）：答的是「负载跳了轨掉多深、多久回来」，与 `pdn_impedance` 的「有没有难看的频点」**不是同一个问题**（带内 |Z| 平照样先掉 `I*ESR`）；反向欧拉 + 自适应网格，峰值在 `h/2` 上重跑并报 `h_refine_peak_delta_pct`（>5 % 直说峰值不是测量）；`v_initial_v` 是**台阶前**的 DC 点、单调 droop 的 `t_peak_s` 是窗口末端。**边界**：VRM 是 Thevenin（无环路）、板是 1~2 个集中节点、无封装/片上电容、无电容降额、只有单台阶、BE 的阻尼让 Q 只是下界；三副 ngspice deck 在 `docs/samples/pdn_step/`，线性网络对表最大差 0.13 %（一阶）/1.27 %（LC 峰值，方向必偏低）。
- **电热**：只闭合「铜 `R(T)` ↔ 一个集中 `Rth`」；2-D 板场的散热介质是**你给的层压板 k + 你列进 `copper_pours` 的铜**（0.0.44：精确面积重叠 → 每格一个面内片电导，面上取调和平均）。铜只帮忙它**在的地方**（同面积条带压在走线上降 6.7 K，挪到无热区降 0.002 K）。0.0.45 又补了两条真缺口：`emissivity` 打开**双面辐射**（`ε·σ·(T⁴−Ta⁴)` 精确改写成 `h_rad·(T−Ta)`，`h_rad` 滞后一迭代、整场重解到不动点；ε=0.9 降 2.72 K、辐射占散热 35.7 %，ε=0 逐位等于不传），`thermal_vias` 加**环形截面铜管到指定 sink**（`k_cu·π(r²−r_in²)/span`，落在中心所在的那一格，点载荷；同数量挪到无热区只动 1.9e-4 K）。0.0.46 再补两块：`thermal_via_array` 的 `barrel_profile` 让**中段镀层减薄按段串联**（`R = Σ L_i/(k_cu·A_i)`，最薄段 31 % 长度扛 42.2 % 阻力，等效厚度只能当摘要），`interface` 在整场之后**再串一层** `interfaces·t/(k·A)`（TIM / 焊盘；`interfaces=2` 逐位成倍，不除以根数）。0.0.58 补上**厚度方向**：`layer_stack` 把板切成 N 层，每层一张面内场（层内等温 = z 方向有限体积），层间只走层压板 `k_z`（+ 你列的过孔，作为串联的间隙），对流与辐射**只在最上、最下两个表面层**——埋进板里的平面确实没有空气，这是模型的核心假设而不是简化。中点法则让 `Σdz == thickness`、`ΣS_k == gp + k_cu·Σt_cu` 精确成立，于是 `k_z → ∞` 是**代数恒等**地退化回单平面（实测 34.771874 vs 34.771887）；z 方向改成**逐列精确三对角解**（第一版用扫描，`k_z = 1e6` 第一次扫描就假收敛、8.5 K 的静默错答案，见 [docs/61](docs/61-电热-层间三维与多平面耦合.md)）。仍不算：真 3-D 场解（层内 z 梯度）、层间内部辐射、温度相关发射率、元件热质量与接触热阻、介质各向异性与铜-树脂界面热阻、**没列进来的铜**（不会自己去读 zone）、两端镀层**向外铺开**（孔口喇叭形）、镀液/孔型表（IPC-4761）、voiding 与泵出；竖向路径除层压板与过孔外仍在 `h` 里。
- **滤波器**：是**合成器**不是仿真器——给几何与电学零点，不给 EM 结果、色散、版图；集总带通的蒙特卡洛是 uniform 独立（最坏情况）。
- **波导腔**（`filter_waveguide_cavity`）：0.0.52 起任何 `TE_mnp` / `TM_mnp` 都给长度、`Rs`、`Qc`（闭式括号式 vs 六面 `|H_tan|²` 数值积分；`R/Q` 仍只给 TE101），还能按 `modes_upto_hz` 排一张**模谱**（每个候选模的 `f`、`f/f0`、`Qc`、退化数，并点名下一个模）与 `roughness_um`。边界是**闭式、无耦合结构**：`f_mnp = (c/2)√((m/a)²+(n/b)²+(p/d)²)`、`Qc = ωμ∫|H|²dV/(Rs∮|H_tan|²dS)`，`er`/`mu_r` 只改光速；没有虹膜/探针/波端口 ⇒ 不给带宽、不给匹配、不给外部 Q；`TM_mn0` 在长度上没有唯一 `d`（单模问直接拒答、模谱里照列），不算辐射 Q、不算 3-D 场图、模谱上限 200 个模。**填介质是 0.0.51 的一个真缺陷**（场积分用真空 `ε0/μ0`、闭式用介质 ⇒ 两者对不上就拒答），现在两处都吃填充、`q_agreement_pct = 0`。
- **布线**：`routed` 不等于 DRC 干净；拥塞协商默认关（一块板 +3 网 / +61% 时间）；默认拓扑 `mst` 报的是 Manhattan 树干长。
- **立创**：单位**恒 mil**（标签只是显示单位）；焊盘位置来自库封装推导（54.1% 命中）。
- **随包引擎**：只有 Windows x86_64 一份二进制；不翻译引擎报错原文；`A` 只在引擎里跑。

### 自己核这三条

```bash
python run_server.py --version          # 0.0.81
python run_server.py --selftest         # 299/299
python -m unittest discover -s tests    # 3277 例（实测 1024.718 s、skipped=5；0.0.80 是 3248 例 /
                                        #   1236.909 s，本轮新增 29 例（`tests/test_loss_audit.py`）；
                                        #   秒数不作性能结论——本机负载没有记录；skipped 仍是那 5 条；
                                        #   3248 + 29）
```

外加一个**机器可读**的同一份清单：`calc_capabilities`——它逐条列出「能算什么 / 拒算什么、为什么」。
本文这一节会过期，那个工具的答案不会。

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

## 工具（179 个）

下面逐行列的是**读路径、写路径与计算**的主力工具。`0.0.10` 之后陆续加的那些（`board_route` / `pdn_*` / `board_electrothermal` / `filter_*` / `magnetics_inductor` / `magnetics_select_core` / `magnetics_data` / `stackup_*` / `rf_chain` / `design_rules` / `layout_review` / `schematic_review` / `part_*` / `board_routability` / `board_zones` / `board_fill_zones` / `board_progress` / `lceda_pro_*` / `board_intent` / `lceda_pcb_write` / `lceda_write_snippet` / `lceda_step_record` / `lceda_step_replay` / `board_job` / `board_fab_package` / `board_export` / `board_bom` / `board_netlist` / `board_graphics` / `schematic_pcb_diff` / `schematic_pcb_sync` / `board_blockers` / `board_fanout` / `board_run_ledger` / `fullwave_solver` / `filter_mc_yield` / `magnetics_dcm` / `magnetics_core_loss` / `magnetics_dcr` / `magnetics_winding_ac` / `magnetics_fringing_loss` / `magnetics_material_core_loss` / `magnetics_dc_bias` /
`filter_coupling_matrix` / `fusing_current` / `fusing_current_taper` / `current_density_field` / `cavity_plane_q` / `roughness_huray` / `plane_radiation_q` / `plane_open_end` / `ac_sheet_resistance` / `ac_round_conductor` / `ac_self_heating`）没有逐行进表 —— 权威清单请调 **`calc_capabilities`**（它同时列出「拒算什么、为什么」），逐条口径见 [docs/20](docs/20-平面谐振与电热耦合.md)~[docs/27](docs/27-自动布线器.md)。

| tool | 作用 | 关键约束 |
|---|---|---|
| `calc_capabilities` | 自述：能算什么 / 拒算什么及原因 / 还没做什么 | 建议先调它 |
| `unit_convert` | 单位换算（立创实测存 **mil**，标签只是显示单位） | 未知单位 `UNIT_UNKNOWN` |
| `width_for_current` | 电流 → 线宽（IPC-2221 反解） | 必须传 `standard`；ΔT≤0 拒绝 |
| `current_capacity` | 线宽 → 载流 | 开窗时只取下界，不计锡层 |
| `via_current_allowed` | 过孔载流（桶壁侧面积折算） | 标注 `not_standard_defined`，±30% |
| `fusing_current_taper` | 一段走线（可锥形/分段）**沿长度**的温度梯度与单发熔断：1-D 轴向有限体积 + 后向 Euler + 逐段 Thomas；给 `ends_c` 另解稳态阈值，给 `t_melt_s` 则反解电流 | 侧向散热**没有 `h` 参数**（绝热侧面）、段内横向等温、无潜热/集肤/电迁移/电弧；`conduction_delay_pct` 由**真跑一遍的绝热对照臂**给出，均匀段精确 0；不熔只回 `value: null` + 峰值与余量 |
| `current_density_field` | 铜区上的 2-D 稳态电流密度场 `div(sigma·grad V)=0`：给 `J` 的峰值与**位置**、`median`/`p99`/`mean`、最窄载流截面与 `crowding_factor`、三环 annulus 读数，以及 `peak_growth` 与 `peak_is_grid_convergent`；给 `frequency_hz` 时电导换成 `t_eff/rho`（`t_eff` 来自 `ac_sheet_resistance`），于是 `R` 是 AC 值、`peak_j` 是**表面值**、`peak_j_gross` 是毛厚度均值 | 薄层电导 `G = t/rho`（与格距无关 ⇒ 均匀条带精确给 `R = (rho/t)(L/W)`）、cascadic 多重网格 + SOR（`omega` 1.97）、纯 stdlib，3~60 s；**峰值在内角处不收敛**（`J ~ r^(-1/3)`，每减半 ×1.2599）⇒ 必须看 `peak_is_grid_convergent` 或改用环读数；2-D/DC、均匀厚度与 `rho`、接触边缘奇异是几何、`narrowest_section` 按轴投影（斜线高估 ⇒ 拥挤因子是下界）、不自动读 zone/铺铜；`frequency_hz` 只**缩放**电导，横向重分布/边沿拥挤/邻近效应仍不在解里（见 `ac_sheet_resistance`） |
| `ac_round_conductor` | 圆导体的 AC 阻抗：**实心线**（`diameter_mm`）或**镀铜孔/圆管**（`drill_mm`+`plating_um`，或 `diameter_mm`+`wall_um`）；给 `R/Rdc`、`X/Rdc`、`R`/`X` Ω/m、内电感、`δ`、`x=a/δ`、`regime`、`f(+1%)`/`f(+10%)`/`f(2×)` 与 9 点径向剖面 | 孔是 **Neumann 面**（`E'(a1)=0`，孔内 `H_φ=0`），`R_dc` 按**环形截面**；实心走闭式、圆管走 **Riccati 走针**（教科书闭式在 `x≳15` 会返回实心答案，`x=40,ri=0.9` 差 5.26 倍，故只作 `x≤10` 复核）；无邻近效应/pad/反焊盘/端部扩散/温升/扫频；`drill` 是**成品孔**（铜在外），`via_resistance` 默认是另一约定（`plating_outside=true` 才对齐），两者差 18 % |
| `ac_self_heating` | 一个导体在**一个频率或一个频谱**上的自热：解 `dT = I²·Rth·R(Ta+dT)`，`R(T)` 是 AC 电阻在该温度的值；**给 `harmonics` 就是频谱**（`P = Σ I_k²·R_k`，功率相加、温度不相加，DC 项走 `R_dc` 看整个截面）；给 `T`/`dT`/`R_cold`/`R_hot`/`β`/`regime`、`unit_gain_current_a`、`rth_for_root_at_melt_c_per_w`、`dc_linear_model_rel_diff`，频谱模式给 `terms[]` / `unit_gain_current_scale` / `all_dc_model_rel_diff` / `single_frequency_model_rel_diff` | `Rth` 是**调用者的单一集总**（不推、无默认值，频谱里所有谐波共用这一个）、单导体无邻近、频谱只有幅值（同频两条电流会被拒，因为要按相量相加）、线性 ρ(T) 用到 100 ℃ 以上**乐观**、不建模熔化/熔断/电迁移；两种 runaway 分开报——指数 1 无稳态、指数 1/2 的根在熔点以上只作**排序**（`ordering, not a reading`） |
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
| `crosstalk_coupled_lines` | 两线耦合（微带/带状线）→ 偶/奇模 Z、ε_eff、v、`NEXT`、`FEXT`：场解给四个电容 → 模态代数 → 两个解耦的一维线时域精确解 | 两导体、准 TEM、**两端同阻抗**（异阻抗直接拒绝）；均匀介质里 `FEXT` **精确为 0**（stripline 实测 1.97e-05 %，而 `NEXT` 1.35 %，负控制非空），微带的 `FEXT` 只在相干长 `tr/\|1/v_odd−1/v_even\|` 内 ∝L、长线饱和到 `amp/4`；`NEXT` 对得上**独立**集总梯形（0.15 %） |
| `repetitive_pulse_rating` | 重复脉冲的降额：一阶集中温度 ODE 每段精确解 → 稳态峰值、失控阈值 `√(A·h·P/(D·β·ρ(Ta)))`、占空比上限 `1/(1+kτ)`、熔化发数、同冷却下的等效直流 `I·√D` | `h` 必须由你给（不给默认，范围 20 倍），`h=0` 是绝热极限（`value=0`，只报发数）；长度方向梯度不在本工具（单发沿锥形/分段见 `fusing_current_taper`，0.0.59；**列车**仍不建模）、板/铜面独立散热、潜热、脉冲内集肤、电迁移、电弧；绝热极限与 `fusing_current`（Onderdonk 的 33）互校 **−0.6156 %**，发数由独立 RK4 逐段积分复现（12 A → 1191 发） |
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
| `board_drc` | **判决**：跑 KiCad 自己的 DRC 引擎，回违规项（按严重级/类型）+ 未连接项 | 立创无命令行 → 明确拒绝给判决，不近似；大板用 `background=true` 交作业（默认超时 `DRC_TIMEOUT_S = 900`，`timeout_s` 可覆盖） |
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
| `lceda_write_snippet` | 生成写 snippet：**名字问应用**（`doc.api`）、**参数问调用者**（`argsv` 原样传入）、**效果问 probe**（`lceda_pcb_write`） | 三档打分：精确名 > 名字含操作词 > 只有厂商注释（精确必须压过包含，否则 `save`/`saveAs` 变成「一样好」）；最高档多于一个 → `SNIPPET_AMBIGUOUS` 带候选，一个都没有 → `SNIPPET_NO_API`；`verified_against_app` 恒 `false` |
| `lceda_step_record` | 把刚跑过的那一次 `lceda_pro_steps` 记成脚本：每步的返回值变成脚本里的 `expected`，脚本自带 sha256 | 零副作用；不给 `save=true` 也不给 `path` 时一个字节都不落盘；**没有值的步骤不带 `expected` 键**（不是 `expected: null`） |
| `lceda_step_replay` | 逐步重跑并比较：`match` / `differs`（两个值都贴出来）/ `no_recorded_value` / `failed` | 失败即停并列出 `not_run`；`idempotent` 恒为 `"unknown"`；比的是**每步自己的返回值**，不是末态全等 |
| `board_job` | 后台作业：`list` / `get` / `wait` / `cancel`（`board_drc(background=true)` 交回 `jid`） | 运行中**没有结果**（不近似、不填上一次的判决）；失败留 `error{type,message}`；未知 id → `JOB_NOT_FOUND`；每类并发上限 2（`JOB_BUSY`）；`cancel` 只丢结果不杀子进程 |
| `app_launch` | 分离启动 EDA（不带工程） | 启动进程 ≠ 打开工程 |
| `app_close` | WM_CLOSE 关窗（等同点 X，可能弹保存框） | `force` 才 TerminateProcess；不碰文件 |
| `schematic_ops` | 原理图写路径的 op 目录（KiCad `.kicad_sch` 22 个，含 4 个 sheet op）+ 每条用的坐标系 | 调 `schematic_propose` 前先看它 |
| `schematic_propose` | 只算不写：plan_id、改前/改后 digest、**预测的网表与 ERC diff** | 立创 `.esch` 直接 `WRITE_UNSUPPORTED_FORMAT` |
| `schematic_apply` | 落盘：checkpoint → 原子写 → **五道闸门**（含 KiCad 网表 + ERC 双向对比 + 整棵树）→ 不过就回滚 | 必须 `confirm=true`；plan 单次使用 |
| `schematic_summary` | 只读：这张图**是**什么（每个符号的真 reference/value/footprint/引脚、计数、工程名）+ KiCad 网表与 ERC | 只读，不创建任何文件 |
| `schematic_view` | 只读：summary / symbols / objects / netlist / erc / lib_symbols / raw | 每个返回都重复坐标系说明 |
| `schematic_hierarchy` | 只读：整棵层级树——每页文件、实例路径、页号、深度、缺失/循环/重复页 | 页号报的是**工程全局**，不是本文件最大 +1 |
| `schematic_new_file` | 建一张空白的 `.kicad_sch`（写完立刻 `from_text` 自检字节与 token 全等） | 只建文件不改树；接进工程请另调 `add_sheet` |
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
| `magnetics_inductor` | CCM boost / buck / buck-boost：占空比、电感量、峰/谷/有效电流、CCM 结束点 | 不回答"哪个能买到的磁芯装得下"——那在 `magnetics_select_core`（真厂商表）；`ripple` 是**你的设计选择**；没给 Isat 时饱和判 `undetermined` |
| `magnetics_dcm` | DCM 稳态：`K = 2Lfs/R`、boost 的 `D = √(K·M(M−1))`、CCM 边界负载（∝ 1/L） | 环路补偿、软启动、DC 偏置下的电感量跌落 |
| `magnetics_core_loss` | Steinmetz `Pv = k·f^α·B^β`（`waveform="sine"`，默认），或对**你给的轨迹**做 iGSE：`waveform="triangle"`+`duty`、`waveform="piecewise"`+`segments`。`k/α/β` **由你给**并写清单位约定；非正弦一律同时打印正弦公式会说多少、`ratio_to_sine` 与错的方向；`k_i` 由「正弦必须还原你的 Steinmetz」解出（α=2.7 三角高估 33 %、α=1.4 高估 6.8 %、α=0.7 低估 3.5 %、α=1 精确相等）——不给 `waveform` 时**逐字节不变** | 不内建 material-level 三元组——同一牌号被两家拟合出两组三元组（三个粉末材料的**厂商拟合**在 `magnetics_material_core_loss`，按磁导率 × 形状查）；无 DC 偏置（只有交流摆幅）、无气隙边缘损耗、`duty<0.05` 的窄脉冲 iGSE 已知高估（GSE/WGSE 项未实现）；`ΔB` 是**峰峰值**不是幅值 |
| `magnetics_dcr` | 绕组直流电阻：每匝平均长度 × 匝数 × 电阻率/截面 | 只有 DC 部分：无趋肤、无邻近效应、无端接与焊点（薄板/走线的 1-D 趋肤是 `ac_sheet_resistance`，绕组的层叠趋肤是 `magnetics_winding_ac`） |
| `magnetics_winding_ac` | Dowell 1-D 的 `F_R = R_ac/R_dc`：箔 / 圆线层叠，拆成自发场与邻近场两项，可回算欧姆 | 1-D 场看不见**气隙边缘场**（有气隙电感里最大的一项）；无 litz、无交错绕组的 MMF 图、无谐波叠加、无磁芯损耗 |
| `magnetics_fringing_loss` | 气隙边缘场的铜损：2-D 磁荷模型（两条反向带电条带）解出 `H_z`/`H_x`，`q` 由轴向积分的闭式定，沿厚度积分 `H_z²` 后乘**精确**板因子 `g(D)`（`D = t/δ`，`D→0` 精确退回薄板式）。返回逐层损耗、等效串联电阻、`loss_over_dc`、`probes[]` | 2-D（沿 `leg_depth` 无限）；极面理想磁导体（无饱和）；径向分量**未建模**（这里实测是轴向的 `0.32`）；与 Dowell 邻近项**按功率相加、无交叉项**；圆线拒绝（给 `field_weight_a2_m` + `probe_radii_mm` 自己乘线因子）；单频正弦；均匀 MMF、同轴壳（`layers` 同时是壳数与匝数）|
| `magnetics_material_core_loss` | 五种粉末芯（Kool Mµ / MPP / High Flux / XFlux / Edge，含 Ultra 与 **High DC Bias** 变体）的 **厂商自己印的**损耗拟合 `PL = a·Bpk^b·f^c`（mW/cm³ + 特斯拉 + kHz），按 **材料 × 变体 × 形状 × 磁导率**（Ultra 还要看频率在 100 kHz 哪一侧）从表里查出那一行，再委托 `magnetics_core_loss` 的 `khz_t` 约定算。`value` 里回报行标签、印刷页码、源 URL、系数本身与「这是曲线拟合不是保证值」。Kool Mµ 60µ 环 100 kHz 复现目录印的 `11.0 / 168 / 466 mW/cm³`（`11.0384 / 167.589 / 466.030`），XFlux 60µ 环 50 kHz / 0.1 T 复现汇总表印的 `575`（`574.999`）| 表里 **93 行**、五个粉末材料有拟合——**九个铁氧体没有**（它们出版的是 material-level 三元组，不是这种按磁导率的表）；系数表只覆盖环 / E-U-EER / EQ-LP / 块 四个形状族；`High DC Bias` 是**另外两页独立印的行**（p.12 / p.14），与基础线差 15 %–34 %，必须显式传 `variant`；拟合不含 DC 偏置与温度，且只在 5 kHz–2 MHz、≤0.5 T 的曲线范围内有意义（出界只警告）；问题没问全就**拒绝**，不替你挑磁导率 |
| `magnetics_dc_bias` | 厂商印的 **DC 偏置**拟合 `% µi = 1/(a + b·H^c)`（H 单位 **Oersteds**，`H = 0.4π·N·I/le[cm]`；`a` 全为 0.01 ⇒ 结果直接是百分数，H=0 得 100 %）。按 **材料 × 磁导率 × 形状族** 查出那一行，返回 `percent_initial_permeability`、`inductance_ratio`、`fit{a,b,c,printed_page,printed_formula}`；给 `include_b=true` 时再把 `µr(H)` 从 0 积到 H 得 `b_t`（`1e-4·∫µr,diff dH`，梯形 4000 步），并拿厂商 p.10 的 Bsat 报 `b_saturation_ratio`；`le_mm` 可省（缺了就用目录印的 le），N 与 I 也可省（直接给 `h_oe`）| 10 个粉末材料 / 29 组 / 125 行（Kool Mµ 与 MPP 的 E/ER 组厂商没印，MPP Blocks 只有 26µ）；磁导率**只有目录真印的那些**（传 55 µ 报 `NOT_FOUND` + `permeabilities_printed`，不插值、不就近取整）；`shape_family` 在多个形状族都有该磁导率时**必须给**；H 超出目录曲线画到的 10–800 Oe 只警告不夹紧；B 是**从 0 积到 H** 的增量磁密，不含直流预磁化偏置点；不建模饱和、不随温度（目录只有 25 ℃ 一条曲线）；铁氧体没有这张表（只覆盖粉末芯）；不做「匝数 × H × µ」的不动点迭代（`0.0.78` 起由 `select_core(dc_bias_current_a=...)` 用区间搜索解，这里仍只按你给的 `turns`/`h_oe` 报一个点） |
| `magnetics_select_core` | 从 `data/magnetic_materials.json`（`include_powder_cores=true` 时并上 `data/powder_cores.json` 的 43 个粉末磁环尺寸）里挑磁芯：两条匝数约束取大、峰值磁密、要不要开气隙（串联磁阻差）、厂商**一个**实测损耗点（或按你给的指数外推，`flux_waveform="triangle"`+`duty` 时按 iGSE 形状比只缩损耗、`turns`/`b_peak`/`gap` 不动）、绕组 DC 与窗口，每个候选都带 `verdict` 或具名出局原因；`dc_bias_current_a`（`0.0.78`）把 DC 偏置接进这条链：偏置下重解匝数、`µ`、`B`，做不到判 `dc_bias_unreachable` 并给能达到的最大值；`toroid_mlt="estimate"`（`0.0.79`）给环型补一条**带区间的**平均匝长与铜损（默认 `off`，与省略参数逐字节相同） | 表不是全型号；饱和只在厂商公布过 Bsat 的温度上判（25 / 100 ℃，**不插值**，缺数据进 `unjudged`）；无 Rth（不报温升）、无气隙边缘场修正、窗口只有 1 个部件有骨架数据；DC 偏置只在印刷 fit 的 10–800 Oe 带内是读数（带外标记 `EXTRAPOLATION` 不 clamp）、不随温度、不建模 `µ(B)` 自洽饱和、损耗系数是零预磁化下测的（偏置芯的 `core_loss_w` 是下界）；粉末磁环是**分布式气隙**（`gap_mm` 恒 `null`、`gap_is_distributed` 为真）、磁导率只有目录真印的那些（传 55 µ 报 `NOT_FOUND` + `permeabilities_printed`，不就近取整）|
| `magnetics_toroid_mlt` | 环型任意几何的**平均匝长与 DC 铜损**（目录不印、`select_core` 只覆盖目录零件，所以单独一个工具）：`flat = 2H + (OD−ID)` × 13 个印刷点的**实测比值带** `1.247656…1.460472`（中位 `1.365936`，`data/toroid_mlt_reference.json`，每行 URL）；给 `turns` 时再给 `N1 = ⌊π(ID−d)/d⌋`、`layers = ⌈N/N1⌉`、`R_dc = ρ(T)·N·MLT/A_cu`，给 `i_rms_a` 再给 `P_cu`，给 `fill_factor` 再给窗口校验；顺带返回 `le / Ae / Ve` 与两条已对过的口径 | 表里的 13 个环都是粉末磁环（T 型），比值带**只有单绕组样本** ⇒ 多层是**下界**并标记；**包漆与加固层不算**（真实 `N1` 略小）、**多层长度增长不建模**、**集肤与邻近不算**（在 `magnetics_winding_ac`）、**温升不算**（`temperature_c` 是调用方给的）、litz / 铜箔 / 并绕不算；给的是**区间**不是单点 —— 单点会落在这个 16 % 宽的带里假装是厂商数 |
| `magnetics_powder_curve` | 粉末芯**印刷曲线**的就地求值（`0.0.80`）：`B = [(a+bH+cH²)/(1+dH+eH²)]^x`（125 行，B 特斯拉 / H 奥斯特）与 `Δµ/µ = a+bT+cT²+dT³+eT⁴`（54 行，T 摄氏度、参考 25 °C），系数全部是厂商在 printed p.128–132 / p.138–139 自己印的，一个都不猜；给 `turns`+`dc_current_a`+`le_mm` 时 按 `H = 0.4π·NI/le` 自己算 H，也给 `µ_r` 的中央差分与 `b_t_from_dc_bias_integral`；两条式子在端点上最大差 **54.34 %** 时**两个都报**并置 `fits_disagree`；7 行带 `turns_over_at_h_oe`（印刷拟合在 H 超过该值后非物理）；给 `temperature_c` 时回 `µ_change_pct` / `µ_ratio_vs_25c`，并与 printed p.15 的稳定性列对账（九材料最差 0.045 pp）；`include_evidence=true` 把三条交叉校验 + 5 条印刷异常 + 5 条不携带一起回 |`H ≤ 0` 一律拒绝（`B(0)=a^x≠0`，当小信号模型反推 µr 中位误差 36.9 %）并回 `b_at_zero_h_t`；温度只到 `[−50, 200] °C`（−50 是 p.15 印刷动态范围下界、200 是评级），`(100, 200]` 答但打 `inside_printed_dynamic_range=false`；**转捩点之后是印刷拟合非物理，不是材料真的掉磁**；High DC Bias 变体（p.12/14，5 行）**没有温度拟合**，问就 `NOT_FOUND` 不套标准页；不携带 Bsat 温度曲线、粉末芯 Steinmetz、DC 偏置损耗修正、单料号重量、µ-vs-B / µ-vs-f 曲线；图上的曲线是矢量、**没有从像素反读**（`graphs_not_read_off_pixels`）；5 条印刷异常**原样保留并登记**（MPP 温度三行相同、Edge 90µ 凑数位、Edge 磁化五行近线性、MPP 550µ 与积分差 54.3 %），不顺手改 |
| `magnetics_core_loss_audit` | 把你手上的 Steinmetz 三元组**对着厂商原印的损耗点打分**（`0.0.81`）：Ferroxcube HB2013 与 2022 3C96/3C98 宣传册上一共 **17 个 PV 点**（9 个牌号；3C90 p84 三行、3C92 p88 两行、3C93 p90 三行、3C94 p92 两行、3C96 p96 三行、3C98 三行、N87 产品页一行 `Max. 375`），**3B7 与 N27 一个点都没印**（`points_printed=0`，不是错）；每个点带 `printed_page` / `printed_as` / `qualifier`，`≤` 与 `Max.` 只判「有没有越上界」、`~` 与裸数字只判「10 % 带内」，**两类判据分开报**（3C90 同一列就混着两类，混比就是错答案）；给 `temperature_c` 只比同温点（3C93 是 140 °C），单位约定默认厂商风格 `khz_mt`（与 `core_loss_steinmetz` 的 `si` 默认不同）；判词 `consistent_with_every_printed_point` / `violates_a_printed_upper_bound` / `does_not_match_every_printed_point`，`value.solved_here` 恒 `false` 并写明「这是打分不是求解」 | 只判印刷点，**印点之间不插值、不外推**；**不给三元组**（2–3 个点含上界，解 3 个指数是把算术冒充厂商数据）；10 % 只是报告带、原始 `deviation_pct` 一律透出；不覆盖 DC 偏置损耗、不覆盖 TDK 只画曲线的牌号（无点可比时拒绝而非猜）|
| `magnetics_data` | 把那张表读回来：材料的 μi / Bsat（各温度）/ Tc / 损耗密度，磁芯的 Ae·le·Ve·AL·Amin·质量·骨架窗口与平均匝长·实测损耗点，**每行带来源 URL**；`kind="powder_cores"` 读粉末磁环目录（43 个尺寸 × 9 列材料），`kind="powder_parts"` 把它展平成 1923 行料号，`kind="dc_bias_fit"` 读 DC 偏置拟合表（29 组 / 125 行 `(permeability, a, b, c)` + 每组印刷页） | 这里不算任何东西，只是带出处的转录；取不到的字段是 `null` + 说明；表里没有任何 **material-level** Steinmetz 三元组（五个粉末材料的 per-permeability 厂商拟合在 `loss_fit` 里，用 `magnetics_material_core_loss` 去算）|
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
| `sim_spice` | 真解电路：`.op` / `.tran` / `.ac`，MNA + 牛顿 + pnjlim + gmin 步进；装了 ngspice 可委派（`engine`）或双引擎对账（`cross_check`）；`0.0.37` 起**最小 ngspice-47 随包**（`vendor/ngspice/`，14 MB，独立进程、混搭许可见 `vendor/ngspice/NOTICE.md`），只有 `engine="ngspice"` 会在本机一个都没装时用它，`engine="auto"` 刻意不看 | 内置子集认 R C L V I E F H K S W D Q M J B T O U、子电路 `X` 与 `.noise`，`A` 只认**四种纯代数 code model**（`gain`/`limit`/`summer`/`mult`），其余逐条点名拒绝（第二套器件描述语言；`engine="ngspice"` 下直通引擎，`0.0.37` 起引擎本身随包，见 `docs/43`），`J` 是 **level-1 JFET**（平方律无 `1/2`、`AREA` 卡覆盖模型、不 stamp 栅结但报 `gate_junction_forward_v`，见 `docs/42`）；开关是**两个电导 + 一个状态**（`VT±VH` 是整宽、`VH=0` 时恰好 `VT` 就闭合、DC 也走滞回带，全部对 ngspice 逐点校准）；`engine="ngspice"` 找不到二进制**报错不回退**；默认 `builtin` |
| `trace_resistance` | 铜线直流电阻 `rho(T)·L/(w·t)` | 只管这一段；过孔/扩展/端子不在这个数里 |
| `via_resistance` | 过孔桶壁电阻（截面是**铜环**不是钻孔圆） | 载流是另一码事，要温升判据 |
| `trace_inductance` | 直线走线自感（Grover 带公式，±20%） | 只用于比两种布局；地平面像项**明确未计入** |
| `ir_drop_chain` | 分段串联 IR drop（每段自己的宽/厚） | 不把不同线宽折算成平均宽 |
| `pdn_impedance` | 电容组 `\|Z(f)\|` 全曲线 + 平面电容；给 `plane` 时附带**腔体模诊断** | 要看**反谐振峰**，不是看最低点；`plane` 只做诊断，**不改那条曲线** |
| `pdn_plane_modes` | 平面腔体每个模的**峰值** `|Z_peak| = w·μ0·t·ψ(r1)ψ(r2)·Q/k²`（Q=1/tanδ）与端口耦合/null | **只给峰值**（单模闭式）；要整条曲线用 `pdn_plane_impedance`；过孔桶壁/反焊盘/分割平面/开放边缘失谐未算 |
| `pdn_plane_impedance` | 平面阻抗**整条曲线**：模态和 `j·w·μ0·t·Σ A_mn/(k_mn²−κ²)`，铜+介质损耗（0.0.51 起可给 `roughness_um` ⇒ `Rs*=K`、`Qc=h/(δs·K)`），峰值/谷/首模 Q；`port_size_mm` **必填**（点源发散，焊盘正则化）+ 阶数扫描闸门（0.0.50 起是双条件：差 < tol **且**增量至少减半） | **只是平面自己**：没有电容/封装/过孔 ⇒ Q 是上界、峰偏高偏窄、谷偏深；要看平面+你列的电容的反谐振用 `pdn_plane_with_caps` |
| `pdn_plane_with_caps` | 平面 **+ ≤8 颗电容**（`c`/`esl_h`/`esr_ohm`/`at_mm`/`size_mm`）的整条 `\|Z(f)\|`：腔体做成 (N+1) 端口 Z 矩阵、电容并在自己端口上、端口降阶 `Z_in = Z00 − Z0c(Zcc+diag Z_c)⁻¹Zc0`（恒等式，可被导纳路径独立重算）；同时报 `peak_z_ohm` / `peak_at_cavity_mode` / `plane_only_peak_z_ohm` / `peak_ratio_vs_plane` / `at_plane_peak_damping`；`roughness_um` 同上 | 只算**你点名的电容**，不选型不优化；封装/过孔电感要靠 `esl_h` 自己给；≤8 颗、3-D 与相邻平面耦合不算；缺 `port_size_mm` 直接拒 |
| `plane_radiation_q` | 平面模从**开边**辐射掉的 Q：`P = ½·ω·ε₀·Σ∫V²dl`（每单位宽度 `G′ = ωε₀ = k₀/η₀`，与磁流矩路径同值）、`U = ½·ε₀·εr·(1/h)∫∫V²dA`，ω 约掉 ⇒ `Q_rad = εr·∫∫/（h·Σ∫dl）`，方形闭式 `εr·a/(6h)`（(1,0)）/`εr·a/(8h)`（(1,1)）；同时把 `Qc/Qd/Q_rad` 按倒数和合成 `q_total` 并报 `which_dominates`、`per_edge`、`radiated_power_w_at_1v_gap` | `open_edges` 缺省 = 四边全开；**`k₀h ≥ 1` 拒**（辐射比入射多，模型自相矛盾）、`>0.1` 打 warning；模态用未扰动 `cos·cos`、边按无限长（**无角点**）、缝不半埋、`q_total` 才是能拿去用的数 |
| `plane_open_end` | 开边把共振往下拉多少：`Δl = (h/2π)(ln(2π·a/h)+1)`（Yariv 2021 eq. 5.20 除以 2 —— 他的一个修正项属于两个端），`a_eff = a + n_x·Δl`，给 `f_eff`、`Δf/f`、`first_order_check`（一阶式与精确式**分开报**，相对差是**预测**不是容差） | `a/h` 或 `b/h` **< 10 拒**（渐近式）；只开「模式看不见」的那对边时位移**精确 0**；只改频率不改 Q；无角点修正；`open_edges` 支持 `"all"`/`"none"`/`"x0,x1"`/列表 |
| `roughness_huray` | Huray 雪球：`K = 1+(RF−1)·F`、`F = 1/√(1+2x+2x²)`（`x = 2δs/r`）、`RF = 1+1.5·sr`，报 `K`、`Rs` 乘后值、`excess_loss_pct`、`knee_frequency_hz`（`δ = r/2`）、`which_regime`；给 `rq_um` 时**并排**报 Hammerstad 值 | 输入是 `(r, surface_ratio)` 或 `nodule_density_per_mm2`（**二选一**），**不与 `Rq` 互推**；`sr > 30`、`r > λ₀/100`、`f ≤ 0` 拒（准静态前提，超了**拒绝而不是钳住**）；只乘铜的 `Rs`，无箔 profile、无各向异性、`Rz→Rq` 不换算 |
| `decoupling_check` | 给定电流/频段/纹波判够不够 | 三态 `pass/fail/indeterminate`；最坏点落在频段下沿说 **indeterminate**（那里由稳压器负责） |
| `thermal_stack` | 叠层 1-D 热阻 `Σ t/(kA)` + 给定功耗的温升 | 严格 1-D，看不见扩热 |
| `thermal_via_array` | 过孔阵列热阻（同环壁几何），可把周围 FR4 并联；给 `barrel_profile` 就把中段减薄**逐段串联**算（不再平均掉），给 `interface` 再加一层 TIM/焊盘在整场之后串联 | 不给剖面时仍偏乐观（一个均匀桶壁）；`barrel_profile` 是你给的形状，不是镀液模型 |
| `thermal_plate` | 2-D 板稳态温度（SOR）+ 热点 vs 平均 | 能量不守恒**拒绝**；点源须给 `size_mm` |
| `thermal_transient` | 热 RC 网络阶跃响应（Foster） | Rth/Cth 可给多级；单级会低估脉冲峰值 |
| `thermal_heatsink` | 反解需要多少 Rth(j-a) | **不提供散热片表**（那会是唯一一个不是推导出来的数） |
| `thermal_convection` | 由风速/尺寸给 h | 工程关联式 ±25%，只用于比两种假设 |
| `board_ir_drop` | 按**板上真实铜**算单网压降（节点带层号，两层只在 via/镀通孔相连）；`model_zones=true` 把每块同网铺铜当 **2-D 薄片**（`R_sheet = rho/t`，栅距按填充面积规划）并入网络，用 CG + IC(0) 解，`zone_grid_mm`/`max_zone_nodes`/`zone_link_mm` 三个旋钮。给 `frequency_hz`（可加 `faces`/`mu_r`/`roughness_rq_um`）则同一张图变成**单频**答案：每段导体按**它自己**的 1-D 精确趋肤解缩放电阻（走线/铺铜走薄片 `t_eff(f)`，过孔走圆管 Bessel），`ac_summary` 给出逐段 `sum_r_dc_ohm`/`sum_r_ac_ohm`/`ratio_min`/`ratio_max`，`copper_um` 元数据仍是**物理**厚度 | 铜厚/镀铜未知或汇点不通即拒绝；铺铜默认**不进**答案（UPPER bound），开了以后热辐条仍是**下界**方向、桥接是量级模型；不给频率就是纯 DC 且**逐字节不变**，给了频率则没有 proximity、没有宽条横向拥挤、没有相位；`authoritative:false` |
| `dfm_capability` | 只校验一份**工艺能力文档**：15 条规则键、单位、方向、出处（`plating_um` 单独校验） | 未知键 / 没有 `source` / 空文档**整份拒收**；不内置任何板厂表 |
| `dfm_check` | 拿能力文档去量板上真实几何，逐条给 `pass/fail/not_checked/not_applicable` + 余量 + `where` | 文档里没写的规则列进 `missing_known_rules`（不是 pass）；有界搜索窗外只给 `lower_bound` |
| `vibration_plate_modes` | 矩形薄板自由振动：频率/模态形状（Rayleigh–Ritz 单轴单项式基 + 边界零空间 + 质量白化 + Jacobi）；四边简支/固支/自由/悬臂/四角点支撑；铜进三件（`copper_um` 双面对称 / `copper_um_bottom` 起**不对称**叠层：`B≠0`、`D* = D − B A⁻¹B`、`ε⁰ = −A⁻¹Bκ`），质量进两件（`added_mass_g`+`added_mass_at_mm` 一个点，秩一白化；`added_mass_regions` 一块 footprint 上均布或整板） | 只有 Kirchhoff 薄板；不算阻尼、不算预紧/大变形、不算开孔与加强筋；不做元件**刚度**、层压板各向异性；不对称叠层只给**平态附近**的线化解（真板会弓起来，那个平衡态不解） |
| `vibration_plate_outline` | **真实外轮廓**（任意简单多边形）的薄板自由振动：Green 定理在环上精确积内部矩 + 质量内积 GMS 白化 + 罚阶梯取**最平一档**（该档步长即 `alpha_spread_pct` 误差棒）；逐边 `ss`/`cc`/`free`、`free` 板、点支撑；每阶带 `penalty_share`/`physical`，撑不住的阶自己承认；0.0.57 起收 `copper_um`/`copper_um_bottom`（广义刚度 `d11/d22/d12/d66`，与矩形工具**同义**：单独给 `copper_um` = 两面相同） | 只解**外轮廓**：孔/挖空不网格化、不加元件质量、不算应力与疲劳；凹角与高边数不保证收敛（`alpha_converged=false` + `boundary_rms_mm` 明说）；`poly_degree<8` 带固支时空间里可能没有合法解 |
| `vibration_plate_stress` | 振型 → 表面应力：同一模态在多个多项式阶上各解一次，**误差棒是量出来的**（`degree_sensitivity_pct`）；von Mises 与最大主应力**各报各的点** | `amplitude_mm` **必填**（振型没有幅值）；点支承下阶梯塌成一档 ⇒ `converged=None`（没有误差棒，而不是误差棒很小） |
| `vibration_random_response` | 随机振动：Miles 等效（平坦闭式 + 实测谱积分互证）→ g_rms / 位移 rms | 单模态；非平坦谱逐段积分只在足迹内，谱外不计 |
| `vibration_fatigue` | Steinberg 三带（3σ/2σ/1σ）+ Miner 线性累积 → 循环数与小时寿命 | `z_allow_mm` **必填**（不内置「典型允许挠度」） |
| `vibration_shock` | 半正弦冲击：SDOF Newmark（β=1/4、γ=1/2）→ 峰值位移/加速度、DLF | 单自由度；不算应力、不算二次冲击与接触碰撞 |
| `vibration_isolator` | 隔振：传递率曲线、隔振起点 `f_n√2`、r=1 处 `Q√(1+1/Q²)` | 高频按 **1/r** 掉（分子含 (2ζr)²），不是 1/r²；不算隔振器非线性与蠕变 |
| `vibration_shock_spectrum` | 冲击响应谱：一串单自由度振子逐频算峰值 |ZPA|、伪速度 2πf·z、峰值位移；对数扫频 | 默认 **5 % 阻尼是 SRS 惯例**并明示（不是你的板子的属性）；每条曲线自检「阶跃+近零阻尼恰为 2 倍」与「不超过 2×输入峰值」；SRS 本身不判失效 |
| `solder_joint_fatigue` | 焊点热疲劳：`Δγ = F·DNP·Δα·ΔT/h` → Engelmaier `Nf = 0.5(Δγ/2ε_f)^(1/c)`、多环境 Miner、试验↔使用 Norris-Landzberg 加速因子 | `joint_height_mm` 必填（是分母）；ε_f **是拟合**（默认 0.325，SAC305 文献散布 0.2~0.35）；CTE 预设是典型值、FR-4 过 Tg 会从 16 跳到 ~60；不算 underfill/空洞/IMC/裂纹扩展 |
| `drop_board_strain` | 跌落板面应变：一阶模态 → SDOF 冲击响应 → 曲率 → 应变，三段各自可复核；给 `dnp_mm`+`joint_height_mm` 再加焊点剪切应变 `ε·DNP/h` | 单模态、**不算器件质量**（f1 偏高 ⇒ 响应偏乐观）；`critical_strain_ue` 只能调用方给，不给就 `judged:false`（**没有 verdict**）；线性板理论，超过 h/2 高估 |
| `board_vibration` | 按板上真实板框（bbox）+ 叠层厚度算 f₁/周期/热点，热点标 `near_antinode`；`fill < 0.99` 时另给 `plate_on_outline`（真实轮廓的 f₁ + `ratio_to_bbox` + 误差棒） | 派生量 `authoritative:false`；bbox 答案不变、方向仍明说（非矩形 bbox 偏软）；轮廓解只在 `fill < 0.99` 时出现，矩形板零成本 |
| `cavity_resonance` | 平面腔体模 `f_mn = c/(2√εr)·√((m/a)²+(n/b)²)` + 每个模的 λ_g | **只给模频不给 Q**：无介损/铜损/辐射、无开放边缘失谐，所以是频率轴上的位置不是判决 |
| `cavity_via_fence` | 过孔围栏：`pitch/λ_g`、每间距电长度、给定判据后的 `pass/fail` | **判据必须由调用方给**（λ_g/20 与 λ_g/10 差 2 倍，都不内置）；不给就 `indeterminate` 且不反推间距 |
| `electrothermal_trace` | 走线自洽温升：R(T) → I²R → ΔT → R(T) 的定点迭代 + 精确失控判据 `dP/dT·Rth < 1` | `rth_c_per_w` **必填**；G≥1 回 `THERMAL_RUNAWAY` 而不是最后一次迭代；只算直流 |
| `electrothermal_conductor` | 同一环，R₂₀ 由调用方给（过孔桶壁 / 分段串联 / 分流器 / 针脚） | 回 R_hot、电阻增幅、迭代次数、稳定性余量 `1−G`；Rth 仍是调用方的 |
| `board_electrothermal` | 板级电热：均匀网格 + SOR，`rho(T)` 线性 ⇒ `alpha*T` 落在矩阵对角线上，稳态是**严格线性**的一次求解；瞬态后向 Euler；失控在**求解前**按对角判；可选 `copper_pours`（面内片电导）、`emissivity`（双面辐射，`h_rad` 外层不动点）、`thermal_vias`（到 sink 的铜管）、`layer_stack`（0.0.58：N 个层节点，层间只走 `k_z`，只有两个外表面有对流/辐射，z 方向逐列精确解） | `h_w_m2k` **必填**；真实铺铜要自己列（不会读 zone），过孔是点载荷（网格相关）；分层模式**不是** 3-D 场解（层内等温），且与 `copper_pours` 互斥；DC，无趋肤/邻近损耗；不替你开 `.kicad_pcb` |
| `filter_prototype` | 原型 g 值（Chebyshev / Butterworth），滤波器设计的起点 | Butterworth 传非零 ripple 直接 `ARG_CONFLICT`；order ∉ [1,20] 拒 |
| `filter_lumped_bandpass` | LP→BP：每段 L/C + 两路 S21（ABCD 级联 vs 闭式 `1/(1+ε²Tn²)`）+ 可选有限 Q 与一阶寄生（L 并 cp / C 串 esl）+ 功率审计 | 两路差 >0.02 dB **拒答**（`INTERNAL_CHECK_FAILED`）；偶数阶不是 50Ω 网络——串联起始 + `1:√g(n+1)` 理想变压器，写在 `value.termination`；损耗是每段一只电阻（`ω0L/Q`、`ω0C/G`），不是介质/导体物理：无趋肤、无介质损耗、无温漂 |
| `filter_mc_yield` | 容差良率：每个 L/C 各自独立抽样，跑同一条 ABCD 级联，带二项 95 % 半宽 | 只有 uniform/gaussian **独立**分布（无相关性、无批次偏移、无老化）⇒ 最坏情况；零容差报**退化**（构造上 100 %）；标称已不达标时明说，不给容差良率 |
| `filter_coupled_microstrip` | 耦合微带反解 w/s：截面解拉普拉斯（奇模电壁/偶模磁壁）+ Broyden 反解 | 准静态无色散；k<0.02 拒；k 误差 >3% 带 `k_resolution_note`（k 是两个相近数之差） |
| `filter_coupled_resonator` | 边缘耦合半波谐振器带通：Qe/k_ij/λ_g−2Δl + 每段 `meets_target` | 不建模交叉耦合（因此没有带外零点）；不给馈电间隙几何 |
| `filter_waveguide_cavity` | 矩形腔**任意 `TE_mnp` / `TM_mnp`**：长度、Rs、Qc（闭式 vs 六面数值积分）、R/Q（仅 TE101），`modes_upto_hz` 排模谱（含退化与下一个模），`roughness_um` 按 `Rs*=K` 折粗糙度 | 两路 Qc 差 >2% 拒答；低于该模二维截止拒；`TM_mn0` 单模问拒答（长度无唯一解，模谱里仍列）；iris/探针耦合、带宽、辐射 Q 必须全波 |
| `filter_coupling_matrix` | 耦合矩阵当**真网络**算：交叉耦合的有限传输零点 + 有限无载 Q 的插损与零点深度；每个耦合贴成理想导纳逆变器的精确 Π 等效，节点法解 S 参数（矩阵只供代数，不倒它求 S） | 四条自检不过就**拒答**：无损 inline 必须复现 `1/(1+ε²Tn²)` <1e-6 dB、无损幺正性 <1e-9、三阶零点必须落在 `Ω_z=m12²/m13`、纹波幅度对原型 g 值 <1e-3 dB；trisection 是**对 inline 矩阵的微扰**，不是 Cameron 合成 ⇒ `ripple_actual_db` 是「你得到的」不是「你要的」（`couplings` 参数可传你自己合成的矩阵）；不生成版图、不做 EM、不建模色散 |

`via_current_allowed` 实算 0.3mm 孔 / 25µm 镀铜 → 1.795 A，高于工程经验值 1A；
返回里同时给出 `rule_of_thumb_gap`，请按保守者取用。

## 用法

零外部依赖，Python ≥ 3.9。

```bash
python run_server.py --version       # 0.0.81
python run_server.py --selftest      # 协议级自检，299/299（含板与原理图写路径端到端、天线四问、仿真七问、DFM 三问、振动九问、腔体与电热七问、板级应力二问、平面模态二问、滤波器三问、安规五问、馈线差分对三问、子电路展开三问、行为源四问、无损线二问、有损线与 URC 二问、噪声二问、多页二问、受控源与互感二问、开关三问、JFET 三问、随包引擎三问、多端口 code model 三问、偏置线性化扫频二问、非矩形板轮廓三问、写 snippet 与后台作业四问、铜面导热三问、辐射与热过孔三问、桶壁剖面与界面层三问、两线串扰三问、重复脉冲降额三问、平面阻抗曲线三问、平面电容反谐振三问、表面粗糙度三问、波导腔全模谱与填充五问、铜加强筋与点质量五问、SPICE 1/f 与温度扫描五问、磁芯选型四问、阻焊丝印与成品孔三问、熔断沿长度三问、开边辐射 Q 与 Huray 五问、电流场三问、铺铜并入网络三问、负载阶跃四问、趋肤四问、粉末磁环三问、DC 偏置四问、DC 偏置接进选型四问、环型 MLT 与铜损六问、粉末磁化曲线七问、厂商损耗锚点审计四问）
python -m unittest discover -s tests # 3277 个用例（实测 1024.718 s、skipped=5，kicad-cli / pcbnew 与二维场解用例占大头；
                                     #   0.0.80 同机 3248 例跑了 1236.909 s，秒数随机器负载浮动，不作性能结论）
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
`dependencies = []`（`0.0.37` 起 `vendor/ngspice/` 是一个随包的**外部程序**，不是一个 Python 依赖，删掉它不影响任何 import，见下文「最小内核随包」）。日志只写 stderr —— stdout 承载协议。

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

- **V4** ✅ 仿真层的物理深度与场内解（`0.0.19`~`0.0.26`）：导体损耗走二维场解、共面单线/差分对/
  有限宽地、差分对接进馈线、磁件损耗、耦合矩阵、焊点疲劳与跌落应变。
- **V5** ✅ 内置 SPICE 自己长大（`0.0.27`~`0.0.32`、`0.0.34`~`0.0.36`）：`X`/`.param`/`B`/`T`/`O`/`U`/
  `.noise`/`F H K`/`S W`/`J` 逐个进内置，`NOT_IMPLEMENTED_CARDS` 缩到只剩 `A`。
- **V6** ✅ 多页原理图（`0.0.33`）与 `A` 的真实载体（`0.0.37`：最小内核随包，不重写引擎）。
- **V7** ⬜ 还没做的都在「一页速查」的 B 段，按前置条件排着；**没有一项是"再加个公式就能有"**。

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
  0.0.43 补的是**等法**不是判决：`board_drc(background=true)` 交回 `jid`、`board_job` 取回，
  运行中只回 `state: running` 而**不带结果**——但 2543.8 s 那次是引擎自己失败，后台化不改变这件事。
- **立创侧还剩两处非实测。** ① 元件焊盘位置＝库封装 + 旋转/镜像变换推导（54.1% 铜端点命中率，
  见 docs/13 §6，底面镜像只有弱证据 35.1%）；② `set_pad_net` 追加一行 `PAD_NET` 时，第 5 字段
  按「封装文档里该焊盘号的每个 PAD 对象各写一行」——8159/8159 指向同号对象是实测量，但**从未观察到
  应用自己写这一行**，所以结果标 `verified_by_app: false`。
- **SQLite 格式（`.eprj2`）只读。** 能打开、能算度量，但写路径直接拒绝（`WRITE_UNSUPPORTED_FORMAT`）：
  `history_data` 加密、`ticket` 单调，任何手改都可能让工程打不开。
- **原理图：KiCad 能写，立创不写。** `.kicad_sch` 有 22 个 op、五道闸门，裁判换成 KiCad 的
  网表 + ERC（[docs/14](docs/14-原理图写路径.md)）——DRC 判铜，原理图的产品是连通性，两套闸门不能照搬。
  **立创 `.esch` 明确拒写**（`WRITE_UNSUPPORTED_FORMAT`，并按内容说出它是 zip 还是 SQLite），
  因为它的记录布局还没在真实保存上测过；这一条不是"以后做"，是"现在拒"。
- **多页（层级）原理图已接上（0.0.33，[docs/39](docs/39-多页工程.md)）。** 4 个 sheet op 与 `schematic_hierarchy`
  读整棵树；页号按**工程全局**分配，同一节点被实例化 N 次就写 N 条 `(path … (page …))`；
  `apply` 多一道 `tree` 闸门，确保两个引擎看到的是同一棵树。**仍不做**跨多文件的一次原子改动
  （建新页走 `schematic_new_file`，两步比假原子更诚实），也不做页面模板、搬文件、跨页批量重命名、
  pin 与 label 形状一致性、KiCad 5 老格式与立创多页。
- **两个闸门 bug 是被真图抓出来的**（docs/14 §5）：元件值改动曾被报成"网表变了"；同一条 ERC
  违规被 KiCad 重新指向不同 item 时曾被算成"新增+解决"。前者会把所有正常字段编辑拦死，
  后者会让人很快学会一按 `allow_new_violations` 了事。
- **立创 Pro 有两条活通路，只有一条真的通过。** websocket 网关那条（`lceda_api_probe`，RFC 6455
  握手/掩码/分片/超时，`tests/test_lceda_api.py` 10 例）本机 49620-49629 全部 `no_listener`——它
  只在用户手动打开「外部交互权限」后才监听，所以那条仍带 `verified_against_app: false`。真正跑通的
  是**应用自己开的命令空间管道**：`lceda_pro_*` 七个工具（第七个 `lceda_pro_steps` 的**分步 + 停表机制**本身仍未真机跑过——写本身已在 0.0.61 验过，验的不是这条编排）对着运行中的 4.1.60 实测过 ping、open
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
  **0.0.61 把最后一格补上了：真机写了一次，而且写进去了。** 在 V4.1.60（bridge
  `4.1.60.198f38ab`）上按 `createProject → openProject → createPcb → openDocument` 造一个**本服务
  自己**的工程（用户那份全程只读），然后 create / delete / move / save 各跑一遍：探针 `0 → 1`、
  工程文件 **409600 → 569344 B** 且 sha 变、`closeDocument + openDocument` 几何一致、
  `modify(id,{startX:2})` 之后**两次整应用重启**读回来仍是 `(2,1)-(21,1)`；第二轮换成工具层跑
  （`lceda_write_snippet` 生成 → `lceda_pcb_write` 执行、probe `1 → 2`）复现同一结论。于是
  `verified_against_app` 不再是硬编码：`lceda_write.VERIFIED_WRITES` 记「哪条桥版本、哪些 op、哪天
  量的、还差什么」，`run(...)` 只在**版本命中 + op 在表里 + 这一次效应被读到**时才放行，不传版本的
  老调用答案与 0.0.60 逐字相同。仍然没证明的照旧写着：探针读到的≠屏幕上看到的、参数写错的片段
  抓不住、`.epro` **归档**写路径未验、厂商 `--mcp` 传输仍不可达。全套读数与七个实测坑见
  [docs/64](docs/64-真机写一次.md)。
  **0.0.62 补上的是另一半：应用到底认不认我们写出来的归档。** 这条以前每次 `board_apply` 都会自己声明「没验过」。现在验了，而且判据不是「导入返回成功」——一个 PCB 记录流被换成垃圾的 zip，`extractProjectInfo` 与 `importProjectByProjectFile` **都返回成功还照样建了工程**，只有把 `pcb_PrimitiveLine.getAll()` 的读数拿出来才存在会失败的判据。四臂：控制臂（应用自己导出、本模块没动）回读 `[[2,1,21,1,0.25],[1,5,21,5,0.3]]`；写入臂（`set_track e1 width_mm 0.5`）回读 **`[[2,1,21,1,19.685],[1,5,21,5,0.3]]`**，19.685 mil = 0.499999 mm，而没点名的线仍是 0.3；负控制 A 回读 `[]`；负控制 B（根本不是 zip）导入返回 `undefined`。范围写死在结果里：只对 V4.1.60（bridge `4.1.60.198f38ab`）、只 ZIP、只 `ImportDocument` + `New Project` 这条门，屏幕未核对。`not_verified` 与 `app_acceptance` 逐次带着这些读数；**没进表的容器一律逐字保留 0.0.61 的旧文案**，`verified_by_app` 的语义一行没动。见 [docs/65](docs/65-归档写后重开.md)。
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
  gmin 向下步进），器件卡认 `R C L V I E G F H K D Q M` 加行为源 `B`（编译成树，不是 `eval`）、
  子电路 `X`、无损线 `T`、有损线 `O` 与 URC `U`、压控/流控开关 `S`/`W`（分段线性电导，见
  `docs/41`）、结型场效应管 `J`（level-1 平方律，见 `docs/42`）；只有 `A` 这一类按名字拒绝并点名，而不是跳过——**一半跑完的网表比拒答更危险**。瞬态是后向欧拉
  （不是梯形），AC 先解工作点再只覆盖雅可比。`not_computed` 每一条都写清：没有自热、没有器件
  电容/迁移率/击穿随温度、没有失真/傅里叶分析（噪声自 `0.0.32` 起有 `4kT/R` 与 `2q*I`，
  `0.0.54` 起再加二极管的 `KF*|Id|^AF/f`）。
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
  装了引擎才拿到的能力是**未解析 deck 直通**：`A` 这类内置按名字拒绝的卡在（`.subckt/X` 自 `0.0.27` 起内置自己拍平就能跑、`B` 自 `0.0.29`、**无损线 `T`** 自 `0.0.30`、**有损线 `O` 与 URC `U`** 自 `0.0.31`、**噪声分析 `.noise`** 自 `0.0.32`、**受控源 `F`/`H` 与互感 `K`** 自 `0.0.34`、**开关 `S`/`W`** 自 `0.0.35`、**JFET `J`** 自 `0.0.36`，都不再需要引擎）
  `engine="ngspice"` 下照跑，节点表从 rawfile 读，信封带 `netlist_parsed:false` 而**不假装**
  这是一次完整解析；同一 deck 走 `builtin` 仍然拒绝。`simulate()` 默认引擎是 `builtin`
  （`engine="auto"` 才用引擎）——默认值不能让同一网表在两台机器给出不同答案。
- **`0.0.37`：最小内核随包，不是自己重写一个 SPICE。** `vendor/ngspice/`（14 MB）是官方
  `ngspice-47` Windows 构建的**未改动**子集（`bin/ngspice_con.exe` + 三个硬依赖 DLL +
  `lib/ngspice/*.cm` + `share/ngspice/scripts/spinit` + `COPYING`），只被 `engine="ngspice"`
  在「本机一个都没装」时兜底，`engine="auto"` **刻意不看它**（仓库长出 14 MB 不该改变既有
  安装的答案）。它是**独立进程**（只 `subprocess.run`，不 link 不 include），所以 MIT 与上游的
  混搭许可（Modified BSD / XSPICE 公有领域 / `table.cm` GPLv2+ / LGPL 组件）能共存——
  `vendor/ngspice/NOTICE.md` 逐项写明，`tests/test_spice_bundled.py` 里有断言盯着这份声明。
  与"零依赖"的关系是**改写而不是打脸**：`dependencies = []` 仍然成立（Python 包没有任何
  import 依赖），多出来的是一个可删的二进制目录，删掉只影响 `engine="ngspice"` 的兜底。
  三条只能量出来的路径事实（`spinit` 按 exe 自己找、`.cm` 真被用到、`SPICE_LIB_DIR` 会顶掉
  `spinit` 的查找基准）与两次误读的回收都记在 [`docs/43`](docs/43-随包SPICE内核.md)。
- **AC 截断会出声。** 扫频点数超过 `max_points` 时返回 `warnings`：只解了前 N 点、末点不是 `fstop`。
  一个还在上升的曲线被静默截断，会被读成"电路在这里谐振"。
- **`0.0.54`：三条「接了活却不干」的静默被换成「实测后要么实现、要么按名拒答」。**
  能力自述第 14 条原先写着 "rejects F H J K S W A cards with the card named in the error"，
  **这句是假的**（六种卡全解、`NOT_IMPLEMENTED_CARDS = {}` 是空 dict），已就地订正并注明
  "was wrong"。顺着它逐条实测，抓到三处更坏的：二极管 `KF=`/`AF=` 被静默忽略（1 Hz 与 10 Hz
  的噪声**逐位相同**）、`R1 1 0 1k mc(1)` 返回 `ok` + `1000.0`（蒙特卡洛被读成标称值）、
  `.temp 25 100` 只解 25 ℃。修法：二极管 1/f **实现**——公式是从随包 ngspice-47 里反解出来的
  （f=1 `2.534181e-08` / f=10 `8.302515e-09`，与只含 shot+thermal 的自解相减，差值之比恰为 10
  ⇒ 频率指数 −1；`Id` 从 shot 的 `2q*Id` 反推，`KF` 读回 `1.00112e-16` 对卡片 `1e-16` 即
  +0.11 %），新实现对引擎两点误差 **0.0589 % / 0.069 %**；`Q`/`M`/`J` 的 `KF=` 在 `.noise`
  按名拒绝（`NOT_MODELED`），理由写在 hint 里且是**实测的**：ngspice-47 把 `KF` 从 1e-15 扫到
  1e-6，它自己的 BJT 噪声**一动没动**（0.0 %）⇒ 没有可抄的东西，`engine="ngspice"` 只会给出
  同一个安静的 thermal+shot 数（MOS 的拟合也能拟出 `F ∝ KF·Id·TOX/L²`，但 `F` 里混着 `|H|²`
  ⇒ 器件级公式与电路级增益分不开，**宁愿不做也不猜**）。`.temp 25 100` 改**扫描**（每温度一次
  独立求解，`cases[i]` 与该温度单独跑**逐位相同**；25 ℃ `0.695820796` / 100 ℃ `0.570918132`；
  `temp_c` 参数覆盖卡片时会**出声**；多条 `.temp` 卡仍按 SPICE 语义取最后一条，实测
  `25` 之后 `100` ⇒ 100 ℃ 的 `0.570918132`）。`mc(...)`/`unif(...)`/`aunif(...)`/`gauss(...)`/
  `agauss(...)`/`limit(...)` 按名拒答（它要的是样本数与种子，deck 里没有），R/C/L 行尾多出来的
  token 也一并拒（`C`/`L` 只放行 `ic=`）、`E` 五字段以外一律拒。另加一条 `call_tool` 的
  **未知参数点名**（起因：我自己把 `temp_c` 写成 `temperature_c`，工具静默忽略并返回了一份完整
  答案）——拼错的键会被念出来，但**不改任何读数**，正确调用逐位不变。全部落在
  [`docs/57`](docs/57-SPICE-1f噪声与温度扫描.md)，默认（`KF` 不给 / `.temp` 单值 / 值就是数 /
  参数名拼对）的输出与 `0.0.53` **逐位相同**，每处新增行为都有一条「旧行为还在」的负控制。
- **`0.0.55`：磁件不再假装有选型——先要一张带来源的厂商表，再逐候选点名。**
  `magnetics_inductor` 的自述原话是 "no core selection and no material table lives here"，
  这句话诚实，但它藏着的缺口很大：没有表就答不了"哪个买得到的磁芯装得下这个电感"。
  0.0.55 加 `data/magnetic_materials.json`（12 种材料 / 16 个磁芯，`verified: true`，
  每行带来源 URL）与两个工具：`magnetics_select_core`（选型）和 `magnetics_data`（读表，
  不算任何东西）；`0.0.77` 起又多了第三个 `magnetics_dc_bias`——厂商 DC 偏置
  `Fit Formula` 三页（印刷 63–65）的 `% µi = 1/(a + b·H^c)`，29 组 / 125 行。纪律写在表头的 `what_this_is_not` 里：**不是 Steinmetz 拟合**
  （TDK/Ferroxcube 的 `Cm/x/y` 在材料 PDF 里、`web` 抓不到 ⇒ 16 行的 `steinmetz` 全是
  `null`；外推只在**你**给了两个指数时做）、**不是热模型**（一个 `Rth` 都没找到 ⇒ 只报瓦、
  不报温升）、**不是 B-H 曲线**（`bsat` 只有点）、**不是全型号**。最能说明纪律的一格是
  N27 的损耗密度：厂商页面给了 **920 kW/m³**，但可达的那段没写 (B, f, T)，于是它被存成
  `pv_quoted_unconditioned_kw_m3`（引文）而 `pv_ref` 是 `null`——**没有工况的损耗密度不是
  一个可以拿来算的数**，`schema` 里也没给它参与计算的路。选型本身是三条式子加一张表：
  匝数 `N = max(ceil(√(L/AL)), ceil(L·Ipk/(B_lim·Amin)), 1)`（两条下界都报出来：
  ELP 18/4/10 在同一问题上 `turns_from_al` 6.201737 对 `turns_from_flux` 25.445293，
  磁通才是活约束 ⇒ 26 匝）；饱和 `b_limit_t` 要么调用方给、要么 `0.80 × Bsat(material, T)`，
  **只在 25 / 100 ℃ 判、不插值**（3C90 在 25 ℃ 6 个可行、100 ℃ **0 个可行 + 6 个
  `saturation_unknown`**——插值会给出一个假答案，第一版"缺数据就整次报错"也被这一条推翻）；
  气隙 `g = µ0·A·N²/L − le/µi`（串联磁阻差，**无边缘修正**，且已带气隙的 A250 靠
  `al_ratio == 1.0` 判"无需再加"）；损耗是厂商**一个实测点**、工况精确匹配才复现
  （U20/16/7-3C90 的 1.9 W @200 mT/25 kHz/100 ℃ 逐位复现，同 B 下 100 kHz 必须 `null`——
  忽略工况这里仍会报 1.9）；窗口只在该部件真有骨架数据时判（E 42/21/15：aw 178 mm²、
  mlt 93 mm 来自 Ferroxcube 骨架页），其余 `window_fits: null` 并进 `window_image`
  ——"能选但没验窗口"比"假装验过"诚实。排序按体积，`limit` 只截列表不改计数。
  全部落在 [`docs/58`](docs/58-磁芯选型与厂商材料表.md)，36 例测试每条判据配一条能翻脸的
  负控制（含 `test_no_steinmetz_triple_is_invented`——将来谁真填了三元组，这条会主动失败
  提醒重定外推路径），工具数 162 → 164，既有磁件工具的可执行代码**一行未改**、答案逐位相同。
- **`0.0.56`：DFM 里三条"不算"的量，现在是实测，而且带出处。**
  `dfm_capability` 原本有一段 `not_checked_by_design`，其中三条（阻焊桥、丝印线宽/字高、
  铜到孔壁）在每张板厂能力表里都是必填，我们却只能回"不检查"。0.0.56 把三条做成实测，
  规则目录 11 → **15** 条，且三条都带"不能编数"的口径：
  **阻焊 web 不偏移多边形**——开窗是铜按 `margin` 做圆盘膨胀，而
  `dist(A⊕D_r1, B⊕D_r2) = dist(A,B) − r1 − r2` 对圆盘膨胀**恒等成立**，所以直接量铜间距
  再减两个 `margin`，**精确无近似**（偏移多边形是假精度的来源）；`web ≤ 0` 是**开窗合并**
  （"没有阻焊"≠"阻焊太薄"），合并对数进 `note`、判词 `not_applicable`，绝不报 "0 mm"；
  正面 `F.Mask` 与背面 `B.Mask` **绝不配对**（把 `*.Mask` 当一层，量到的是板厚）。
  **丝印**宽度读 `(stroke (width x))`（回落 pre-6 的内联 `(width x)`），字高读
  `(effects (font (size w h)))` 的**第二个**数（`EDA_TEXT::Format` 写 `.x` 再 `.y`，
  `.y` 才是高度——读第一个数会把合格的板报成 0.6 mm）；**文件没写的宽度不填默认值**，
  只排除 + 计数，`where` 里写"the true minimum may be smaller"，所以它只能用来证"合格"。
  **铜到成品孔壁** `= dist(另一网络铜, 孔心) − drill/2 − plating`：`plating_um` 是**工艺参数**，
  放在**能力文档**里与限值同源（工具 schema 不动、工具数仍 164），**从不默认**——没给就
  `not_checked`，因为量到钻壁是另一个量；并排除拥有该孔的 pad（那是环形环，另一条规则）
  与同网络铜（那是连接）。最能说明问题的一组读数：同一块板、同一个 1.05 mm 限值，
  `plating_um=0` ⇒ **1.100 pass**，`plating_um=100` ⇒ **1.000 fail**——判词由工艺参数决定，
  所以它必须来自文档而不是我们的记忆。`NOT_CHECKED_BY_DESIGN` **仍是 8 条**（收窄而非删除：
  还剩 mask-defined pad、不写宽度的丝印、镀后环形环等）。三条负控制都能翻脸：0.11 mm 笔画
  对 0.12 限值 FAIL、开窗合并必须 `not_applicable` 而不是 0、缺 `plating_um` 必须 `not_checked`
  而不是 pass。登记过的三处输出改变：`rules_known` 11 → 15（`coverage_pct` 分母变）、
  `present["pad_to_mask"]` 由 false 变 true（旧 note 说"只在需要时解析"，现在不真了）、
  `present` 新增 `silk`；既有 11 条读数**逐位不变**（主夹具 `min_copper_gap` 4.4、
  `min_copper_to_edge` 0.875、passed 15 / failed 0）。全部落在
  [`docs/59`](docs/59-阻焊丝印与成品孔.md)，37 例测试每条判据配一条能翻脸的负控制。
- **`0.0.57`：振动层补四处——其中一处是"一直算错"。**
  前三处是能力缺口：`copper_um_bottom` 让**不对称叠层**进来（膜-弯耦合 `B≠0`，
  面内自由板实际显示的刚度是**折减刚度** `D* = D − B A⁻¹B`，`D` 单独会偏硬；
  中面应变 `ε⁰ = −A⁻¹Bκ`；中性面 `z₀ = B11/A11`。单面 35 µm 的 100×80×1.6 板
  `d11` 11.2903 → **10.9913**（软 2.65 %）、`z₀` **0.0815537 mm**、f11 **737.558 Hz**，
  夹在裸板 677.29 与双面 800.849 之间，镜像叠层 `z₀` 反号而 `d11` 相同）；
  `added_mass_regions` 让**分布元件质量**进来（精确多项式矩积分 + **按 footprint 面积
  平均** + 带质量白化：patch 从 40 mm 缩到 0.5 mm，f11 从 363.713 收敛到 316.052 Hz，
  点质量独立路径 **316.044**，2.5e-5 碰头；负控制是"漏 1/面积"会让两级都退化成裸板）；
  多边形轮廓路径收铜（广义刚度 `d11/d22/d12/d66`，罚尺度改用 `sect["d11"]`，
  与矩形 `D11` 同值 14.2、f1 差 0.0118 %，无铜分支**逐位不变**）。
  第四处是**修正**：0.0.56 的铜面应力用**铜的 E 配层压板的 ν**（括号里 0.15，铜是 0.34），
  1 oz 板上 10 % 量级偏差。本版改成 `σ = Q(z)(ε⁰+zκ)`，**用哪一层就取哪一层的 (E, ν)**，
  `κ_xy` 用工程扭转 `2w_xy`，双面时解两个面报较坏一面。**归因**：旧公式在 0.0.57 自报的
  曲率点上回放 **20/20 读数差 ≤ 4.3e-6**（峰点未移、幅度未变，唯一来源是 `Q12` 里的 ν），
  在 `kx=0` 的峰点上比值恰为 `ν_CU/ν_LAM = 2.2667`；前后值逐条列在
  [`docs/60`](docs/60-不对称叠层与分布质量.md)（`sigma_1` −10.1128 → **−22.9224**、
  `von_mises` 62.9744 → **59.3745**、`surface_strain_ue` 91.9347 → **208.385** …）。
  **无铜路径 `legacy=True` 逐位不变**。工具数仍 **164**（四处都是可选参数，默认关时
  体积与行为零变化）；26 例新测试 + 3 条 selftest（`200/200` → **`203/203`**）。
- **温度必须给符号正确的答案。** IS 按 SPICE 实现随温度缩放（XTI/EG/TNOM）：27 ℃ 二极管 0.6925 V，
  100 ℃ 0.5709 V，−40 ℃ 0.8004 V。只让 Vt 随温度涨会让"高温压降更大"——那是安静的错答案。
  同一个 deck 写 `.temp 25 100` 是**扫描**（`0.0.54` 起），不是"取第一个"——这两件事都是"温度
  必须给符号正确的答案"的一部分：一个是符号，一个是几个温度。
- **热学不给表、不猜 h。** `thermal_heatsink` 只反解预算，不发散热片库；`thermal_convection`
  是工程关联式（±25%）并明说它只能用来比两种假设；2-D 板方程解完要过能量平衡自检（0.5%），
  不平就报错，不报告。
- **PDN 判最坏 |Z|，判不了就说判不了。** 只有"明显不够"才 `fail`；最坏点落在判定频段下沿时
  回 `indeterminate`——低频端电容不是短路，是稳压器在维持。平面谐振、平面电容的准确取值都
  未建模。
- **板级 IR drop 用板上真实铜，不用长度乘方阻。** 节点是 `(x, y, layer)`，两层只在 via 或
  镀通孔焊盘处相连；铜厚取自叠层（`1 oz`、`0.5 oz`），没写就拒绝，不默认 35 µm。

### DFM 层：没有表，只有规则（[docs/18](docs/18-DFM-工艺能力校验.md)）

- **不内置任何板厂工艺能力表。** 规则限值来自用户给的**能力文档**，服务器只提供目录（15 条规则键：
  最小线宽 / 最小间距 / 最小过孔钻孔与环宽 / 最小 PTH 环宽 / 孔到孔 / 铜到板边 / 最小槽宽 /
  最大板宽 / 最大板高 / 最大铜层数，以及 0.0.56 起的最小丝印线宽 / 最小丝印字高 /
  最小阻焊 web / 铜到**成品**孔壁）与体检。填一个猜的线宽限值比拒答更糟——那会让人以为板厂
  已经答应了。
- **三处整份拒收，而不是逐条忽略。** 未知键（拼错的键会被读成「没这条要求」）、缺 `source`
  （说不出出处的限值不可追责）、空文档，一律 `CAPABILITY_*` 报错并回可用的键清单。
- **没写的规则不是 pass。** 文档里没声明的规则进 `missing_known_rules`；不适用（例如板上没有
  PTH 孔时的环宽）才 `not_applicable`；量不出来的进 `not_checked_by_design`——仍是 8 项
  （mask-defined pad 与开窗到阻焊自身边缘的 sliver、不写线宽的丝印与丝印压焊盘、阻抗签核、
  工艺公差、拼板/V-cut/基准点、镀通与非镀通槽、**镀后**环形环与桶壁斜度、圆弧折线化）。
  0.0.56 把其中三条**收窄**成"真正剩下的那部分"：阻焊桥、丝印线宽/字高、铜到成品孔壁现在
  是实测的，条数不变但内容变了（[docs/59](docs/59-阻焊丝印与成品孔.md)）。
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
- **铜与质量：只进两样，且形状一根不动（0.0.53，[docs/56](docs/56-振动-铜加强筋与点质量.md)）。**
  `plate_modes(copper_um=…)` 把铜当**双面对称**薄皮进变换截面（`D11/D12/D66` 逐层积分、
  `mpa = ρh + 2ρ_cu·t`），`plate_modes(added_mass_g=…, added_mass_at_mm=…)` 把**一个**点质量
  写成秩一刚度扰动 `M = I + β s sᵀ` 并**闭式白化**（`c = √(1+β|s|²)−1`、`d = −c/(1+c)`、
  `K' = AᵀKA`），不是把模态截断到单模。
  于是三道闸门各自都能翻脸：**形状不变**有代数根——每一层都满足
  `E/(1−ν²) − 2·E/(2(1+ν)) = νE/(1−ν²)`，加权后仍有 `d12/d11 ≡ 1 − 2·d66/d11 ≡ ν_eff`
  （FR-4 1 oz 实测 0.2278669），所以 `f ∝ √(D11/mpa)` **精确**：1 oz 的 800.849 Hz
  `= 677.290 × √(14.2/3.5872 ÷ 8.38056/2.96)`（1.18243），振型逐位不变；
  **纯质量**这个错答案被负控制钉死——`677.290/√(3.5872/2.96) = 615.237` 比裸板还低 **23.2 %**，
  一个只加铜皮质量、不加刚度的实现绝不可能落到 800.849；**点落在节线上严格 no-op**
  （`whitening_c = 0`、四阶频率逐个 `==`），而 20 g 在板心把 677.290 拉到 316.044 Hz，
  比单模锚点 323.6815 **还低 2.36 %**（截断到单模会让答案偏高 —— 这是「全秩 vs 单模」的分辨读数）。
  有铜时应力取**最外层铜面**（`z_s = h/2+t`、`E_CU`、`NU_CU`）：`sigma_max` 涨 5.288 倍
  （`= (110·0.835/(1−0.34²))/(24·0.8/(1−0.15²))`），`von_mises` 只涨 4.396 ——
  `τ/σ = (1+ν)/(1−ν)` 随 ν 变，所以它**不该**跟着缩，测试里用 `assertNotAlmostEqual` 明说这件事。
  边界：**对称**皮才只需一组 `D`（不对称叠层要膜-弯耦合）、只加**一个**点、
  分布元件质量/元件刚度/多边形轮廓路径上的铜/应力工具那个独立求解里的铜都不算；
  铜常数取 rolled/ED sheet 的 110 GPa（电镀铜更软 60~90 GPa，只改几个百分点）。
  不给这三个参数时输出与 0.0.52 **逐位相同**（铜走旧式分支、`apply_mass=None` 直通）。
- **平面还有两笔账在腔体外面：开边辐射与开边失谐，加上粗糙度不再是"一个 Rq"（0.0.60，[docs/63](docs/63-开边辐射Q与Huray结节.md)）。**
  `cavity_plane_q` 的 docstring 一直列着三件明说不做的事，这一轮做完了，**而 `plane_q` 一行未改**（旧读数逐位不变）。
  ① `plane_radiation_q`：一条开边流失的功率 `P = ½·ω·ε₀·∫V²dl` —— 每单位宽度的辐射电导 `G′ = ωε₀ = k₀/η₀`，
  由「无限大法兰上的窄缝等效磁流」与「2-D 磁线源对偶」两条独立路径得到**同一个值**，`ω` 在 `Q = ωU/P` 里约掉 ⇒
  闭式 `Q_rad = εr·∫∫(V/V₀)²dA / (h·Σ∫...)`，方形四边全开退化成 `εr·a/(6h)`（(1,0)）与 `εr·a/(8h)`（(1,1)），
  50 mm/0.2 mm 实测 **179.167 / 134.375**，`Qc` **115.071** ⇒ `Q_total` **70.0689**（`which_dominates = copper`），
  1.6 mm 上 `Q_rad` **22.3958** 反过来压住铜（`which = radiation`）。闸门是模型**自己**给的：一条窄缝流失的入射
  功率份额就是 `k₀h`，所以 `k₀h ≥ 1` 时「辐射的比进来的还多」⇒ `MODEL_SCOPE` 拒绝（`h = 100 mm` 实测被拒），
  `>0.1` 打 warning。**登记**：第一版纸面推导漏了 `k²`，得出「`P` 与 λ 无关、`h→0` 不趋零」的物理矛盾结论，
  改正后才有上面这条式子；把它挡住的是 `k₀h < 1` 这条自洽闸门，不是我们的偏好。
  ② `plane_open_end`：`Δl = (h/2π)(ln(2π·a/h)+1)`，来源是 Yariv 2021 eq. 5.20 的电容式（**一个**修正项属于
  **两个**端，所以单边算一半），测试要求「函数自己的 Δl 还原出的电容」与引文式在 1e-5 内**恒等**——因子 2 是
  别人的公式给的，不是约定。50 mm/0.2 mm ⇒ `Δl` **0.266086 mm**、位移 **−1.05313 %**（一阶式 −1.06434 %，
  两者之差**精确等于 −ε/(1+ε)**，是预测不是容差）；1.6 mm FR4 ⇒ **−6.01208 %**。负控制：`(1,0)` 模只开 y 边
  位移**精确 0**（模式看不见那个方向）——「两个方向一起拉伸」的实现能过本节其余全部用例，只在这里翻脸。
  ③ `roughness_huray`：`K = 1+(RF−1)·F`、`F = 1/√(1+2x+2x²)`、`RF = 1+1.5·sr`（1 GHz、r 0.5 µm、sr 1 ⇒
  `K` **1.11953**，即 +11.95 %；10 GHz/sr 6 ⇒ **2.99940**），knee 在 `δ = r/2`（`F` 精确 `1/√5`）。**登记**：
  第一版 knee 频率漏了 `2π`，报告值落在 `δ = r/2.5`；测试不信这个字段、拿频率回代算 δ 与 `r/2` 比才抓到。
  `sr = 0` ⇒ `K` **精确 1.0**（负控制）；`r > λ₀/100` 的光频**拒绝而不是钳住**；`Rq` 与 `(r, sr)` **不互推**，
  只并排报 Hammerstad。54 例新测试（31 条拒绝、逐条负控制）+ 6 条 selftest；**工具数 165 → 168**。
- **熔断不再是"整根线一个温度"：沿长度的一维梯度（0.0.59，[docs/62](docs/62-熔断沿长度的一维梯度.md)）。**
  `fusing_current` 的 `assumptions.spatial` 一直写着"one temperature for the whole trace
  cross-section and length"——锥形/分段走线上这句话换不来"熔在哪"。`fusing_current_taper`
  把每段当一个有限体积节点（`C = ρ_m·c_p·A·L`、`R = ρ(T)·L/A`、段间 `G = 1/(L_i/(2kA_i)+L_j/(2kA_j))`），
  时间上后向 Euler + 沿三对角 **Thomas 精确解**（不在 z/时间方向做"扫描 + 收敛判据"——0.0.58
  的 8.5 K 静默错答案就是被扫描判据骗的），熔断时刻由步内线性插值给出并回段号与 `melt_at_mm`。
  三个算术锚点：均匀段 20.21 A 熔断 **0.993269 s**（解析绝热式 0.993880、Onderdonk 1 s，
  差 −0.0614 % / −0.6731 %，**0.31 % 的残差被写进测试而不是拟合掉**：Onderdonk 的 33 对
  今天常数 `ρ_m·c_p/ρ20 = 33.21`，`33·ln10 = 75.985` 对 `76.46`）、分段数 n=1..40 **差 <1e-9**
  （离散是段无关的），均匀段剖面极差 **0.0**。第二个锚点是"传导到底省了多少"：不说"考虑了传导"，
  而是**真跑一遍** `k=0`、端绝热、同一窗口的绝热对照臂，`conduction_delay_pct` 由两者相除；
  均匀段上它**精确 0**（负控制：对照臂不是"少算一点"），有测试断言
  `k_thermal_w_mk=0` 的正跑与该对照臂**逐位相同**。颈部（臂 2 mm/颈 0.5 mm/总长 42 mm、35 µm、30 A）
  `t_melt` **0.039764 s** 对平均宽均匀段 0.419000 s ⇒ 晚 **10.5×**，且 delay 随颈宽
  **非单调**（2.00/1.00/0.50/0.25 mm → 0 % / +108.295 % / +43.137 % / +14.584 %）——
  这是真物理（越细越快、侧面一热传导就越相对重要），登记为**不许"修平"**。
  `ends_c` 给出才谈稳态：另解一个无 `C/dt` 的线性系统并对 I 二分，`min(diag) <= 0` 判"已过
  失控点"回 `None`（数学上就是 M-矩阵性质丢了）；100 mm×2 mm×35 µm 的阈值 **3.049725 A**
  （代回峰值 **1083.0017 ℃**）、颈 0.5 mm 的锥形 **9.427701 A**；绝热端对任何正电流都没有稳态，
  所以回 `null` + 写死的理由——这是**回答**不是缺失。反解方向（给 `t_melt_s` 求电流）用对数二分，
  下界取"最窄段绝热电流"，正反往返相对偏差 **−6.5e-4 ~ −2.2e-4**。**不熔不给数**：
  2 A/1 s 回 `value: null` + `peak_c 29.1838` + `margin_k 1053.82`。`precision` 里写的是
  **方向相反**（删侧向散热使电流偏低、抹横向梯度使电流偏高，谁赢取决于宽厚比），不编一个符号。
  仍不算：侧向散热（**这一族没有 `h` 参数**）、段内横向梯度、潜热、集肤、电迁移、开断后电弧、
  脉冲**列车**（那是 `repetitive_pulse_rating` 的一阶集中温度，两侧不互相替代）。
  41 例新测试（每个拒绝一条 `reason_code`、每条判据配能翻脸的负控制）+ 3 条 selftest
  （`205/205` → **`208/208`**）；**工具数 164 → 165**，`fusing_current` 与
  `repetitive_pulse_rating` 的可执行代码**一行未改**、答案逐位不变。
- **电热的厚度方向：层间耦合，和一次 8.5 K 的静默错答案（0.0.58，[docs/61](docs/61-电热-层间三维与多平面耦合.md)）。**
  `layer_stack` 把板切成 N 层，每层一张面内场，层间只走层压板 `k_z`（+ 过孔，作为串联的间隙），
  **对流/辐射只在最上、最下两个表面层**——埋进板里的平面确实没有空气。中点法则让
  `Σdz == thickness`、`ΣS_k == gp + k_cu·Σt_cu` 精确成立 ⇒ `k_z → ∞` 是**代数恒等**地退化成
  单平面（34.771874 vs 34.771887，`tol 1e-6`）；两层均匀源对闭式 **25.035534 / 25.033734**（1e-6），
  瞬态容量 44.1549 J/K = `rho·cp·A·t`。**登记修正**：第一版在 z 方向也扫描，"改动量 < `tol_k`"
  在刚性耦合下第 1 次扫描就成立 ⇒ `k_z = 1e6` 返回 **26.2468 °C**（`sor_sweeps = 1`，一个完全
  均温的场），而答案是 34.77 °C，**8.5 K 的静默错答案**；改成**逐列精确三对角解（Thomas）+
  整块松弛**（`omega = 1.0`）后收敛只由面内决定。另登记：`hc_face = h·ac`（第一版给了两面 ⇒
  能量不平衡 −95.208 %）、物理性门的分层相对 slack、分层瞬态补 `t = 0` 那一格、分层过孔报告
  必须显式覆盖 `extra["thermal_vias"]`。29 例新测试（13 类拒绝 + 9 条负控制）+ 2 条 selftest
  （`203/203` → **`205/205`**）；**工具数仍 164**，默认关时单平面输出逐位不变。
- **不对称叠层、分布质量、多边形上的铜，以及应力的 ν（0.0.57，[docs/60](docs/60-不对称叠层与分布质量.md)）。**
  `copper_um_bottom` 打开膜-弯耦合：`B = ∫Qz dz ≠ 0` 时中面一弯就带拉伸，于是
  `ε⁰ = −A⁻¹Bκ`、面内自由板实际显示的刚度是**折减刚度** `D* = D − B A⁻¹B`（`D` 单独偏硬
  ⇒ 用 `D` 算频率会偏高），中性面 `z₀ = B11/A11`。三个结构恒等式当判据（`Aε⁰+Bκ = 0`、
  `Bε⁰+Dκ = D*κ`、镜像叠层 `z₀` 反号且 `d11` 相同），对称叠层仍走 0.0.53 的闭式
  （两条路径代数等价、浮点上差 ~2.7 ppm，统一切换会**静默改答案**，所以不切）。
  `added_mass_regions` 把质量写成 `M = I + Σβ_m R_m`，`R_m` 是**精确矩积分再除以 patch 面积**
  ——漏掉除法，patch 越小质量越趋近 0，20 g 的 0.5 mm patch 会被读成裸板（这就是它的负控制）。
  多边形路径把标量 `D` 换成 `dmat=(d11,d22,d12,d66)`、罚尺度跟着换成 `d11`，且
  **`copper_um` 单独给 = 两面相同**（与矩形工具同义；初版当成"仅顶面"，同一块板两个工具
  给出 10.99 与 14.2，已改）。应力改为 `σ = Q(z)(ε⁰+zκ)`，**用哪一层就取哪一层的 (E, ν)**
  ——0.0.56 用铜的 E 配层压板的 ν，1 oz 上 10 % 量级偏差，这是**登记修正**而不是风格变化：
  无铜路径逐位不变，有铜的读数变了（`kx=0` 的峰点上恰好是 `ν` 比 2.2667 倍），前后值全在文档里。
- **`board_vibration` 的主答案仍是板框 bbox**，并明说方向：非矩形轮廓会**偏软**（f₁ 偏低），
  同时回 `outline_fill_fraction_of_bbox` 让人自己判程度。不算元件质量、不算安装点刚度、不算板内开孔。
  0.0.42 补了第二条路（[docs/44](docs/44-非矩形板振动.md)）：`plate_on_outline` 与工具
  `vibration_plate_outline` 直接拿真实外轮廓多边形当边界，同一块 L 形板（fill = 0.75）
  实测 f₁ 从 553.651 Hz 变成 1734.760 Hz（**3.13×**）。精度与诚实度都靠两个读数：
  `alpha_spread_pct`（罚阶梯最平一档的步长＝误差棒）与每阶的 `penalty_share`/`physical`
  （固支方板第 2~4 阶的 21~28 kHz 里 >90 % 是罚项，被标成非模态）。
  仍不算孔/挖空、不加元件质量与铜厚、不算应力；凹角（L 形 spread 14.7 %）与高边数
  （8 边形 vs 圆板 +26 %）直接报"没收敛"，不给一个看起来很准的数字。

### 平面谐振与电热耦合：模频、曲线、反谐振，和一个 Rth（[docs/20](docs/20-平面谐振与电热耦合.md)、[docs/52](docs/52-平面阻抗曲线.md)、[docs/53](docs/53-平面电容反谐振.md)、[docs/54](docs/54-表面粗糙度与导体Q.md)）

- **腔体模是闭式的，Q 不是。** `cavity_resonance` 给 `f_mn = c/(2√εr)·√((m/a)²+(n/b)²)` 与 λ_g，
  100×80 mm / εr=4.3 的 (1,0) 落在 0.722864 GHz（λ_g 恰为 2a，即半波的定义），(1,1) 严格等于
  √(f₁₀²+f₀₁²)，方板的简并对**分行列出**。没有 Q（介损、铜损、辐射）、没有开放边缘失谐修正
  ——真实平面与其边缘不是 PMC，所以这组数是教科书值，返回里写清"实物会有几个百分点失谐"。
  一个谐振没有 Q 就不是判决，`cavity_resonance` 因此**不判"这个模要不要紧"**。
  0.0.10 补了另一个工具而不是改这个数：`pdn_plane_modes` 从 `tanδ` 取 `Q=1/tanδ`，
  给每个带内模的**峰值** `|Z_peak| = w·μ0·t·ψ(r1)ψ(r2)·Q/k²`，回答"这个模比我的阻抗目标高多少"。
- **模态和为什么曾经不能算，0.0.49 为什么能算了。** 点端口做模态求和时 |Z| 随阶数单调爬升、
  没有极限（自测口径：50×50 mm、2 GHz，order 8/16/32/64/128/256 得 5.71/8.30/10.97/13.70/
  16.45/19.23 Ω）——那是**点源在二维薄片里的对数发散**，不是这块板的性质。给端口一个有限焊盘
  （特征函数在焊盘上取面积平均 `sin(u)/u`）级数就收敛，所以 `pdn_plane_impedance` 把
  `port_size_mm` 列为必填：0.5 mm 焊盘在 256→384 阶只动 0.14 %。收敛判据按**带内峰值**尺度
  （`max|ΔZ|/max|Z|`）而不是逐点相对变化——首模以下电容与扩散电感的相消点上，|Z| 是两个大电抗
  之差，逐点相对误差物理上不可能收敛。函数里仍带电容锚点自检（`f=首模/100` 的求和必须回到
  `1/(ωC_plane)` 的 1 % 内，实测 0.149 %），这条自检曾抓出自己 20 倍的归一化错。
- **平面与电容的反谐振，0.0.50 起算得出来，但只算你点名的那几颗。** 把腔体当成 (N+1) 端口
  Z 矩阵（端口 0 = 注入点，1..N = 电容站，每端口带自己的焊盘孔径），电容按
  `Z_c = esr + jω·esl + 1/(jωC)` 并在自己端口上，端口降阶
  `Z_in = Z00 − Z0c·(Zcc + diag Z_c)⁻¹·Zc0` 是**线性网络恒等式、不是拟合**：同一结果可用
  导纳路径（先求逆、对角加 `1/Z_c`、再求逆取 `[0][0]`）独立重算，实测差 ≤ 2e-6。三条独立真值
  （零电容 = `pdn_plane_impedance` 逐点、开路 = `pdn_plane_impedance` 同频、导纳路径）与四条
  **会失败的对照**（波腹阻尼 vs 节点不阻尼、4 nH 与 1 nH 的反谐振峰互换、零电容恒等、
  `Re(Z)/|Z| ≥ 0` 无源性）都在 `tests/test_plane_caps.py` 里。矩阵法把每个模的除法只做一次、
  被所有 (i,j) 共用（15 个元素 order 256 约 0.24 s）；`np_=1/2/3` 展开再快 2.5 倍。
  读法要分开：反谐振会让加载峰**高于**平面峰（实测 `peak_ratio_vs_plane 1.773`），所以
  `at_plane_peak_damping`（平面峰处被压到 1.535×）与 `peak_ratio_vs_plane` 是两个不同的数，
  不能只读一个。
- **0.0.49 的收敛门在 0.0.50 补严了——这是修正，不是新功能。** 原门只看"最后一阶的差 < tol"，
  实测加载曲线 16/32/64/128 阶的差是 6.83/11.61/21.03/**22.31 %**（128 阶比 64 阶还大），
  单条件门会在中途放行 2.5~9.7 % 错的答案。现在要求"差 < tol **且** 增量至少减半 **且** 至少三阶"，
  且 `Z_in` 与 `Z00` 两半各自按自身带内峰值归一后过门；另修掉"只给一个频点时探针集为空、
  门无物可测"的洞（实测该点 order 64 与 128 差 9.7 %）。plane-only 的默认答案**逐位不变**
  （仍在 order 256 接受，peak 92.4503 Ω @ 2.85841 GHz），带电容的旧读数因此更新：
  158.46 Ω / 439.6 MHz → **163.96 Ω / 417.93 MHz**（256 阶自身还差 2.5 %）。`max_order`
  默认对 `pdn_plane_with_caps` 提到 **1024**（模节点位置 512 阶还动 2.1 %），plane-only 保持 256。
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
  仍然没有的：3-D、层间内部辐射、温度相关发射率、组件热容与接触热阻、交流损耗（趋肤/邻近）——`ac_sheet_resistance` 给的是*电阻*，把它变成温升还得自己喂 `electrothermal_conductor`、
  `sim_spice` 里的器件不发热、`board_ir_drop` 也不把自己的 I²R 反哺进温度场；
  而且这个 2-D 场只沿**你给的层压板 k** 与你**列进 `copper_pours` 的铜**（`0.0.44`，且只走面内片电导）扩散，外加你自己给的 `emissivity`（双面辐射）与 `thermal_vias`（到指定 sink 的铜管，`0.0.45`；每数组可给 `barrel_profile` 分段串联，`0.0.46`）；真实铜面/平面、`.kicad_pcb` 的 zone 不会被自动读进来，也不读板文件。

### 滤波器合成：给几何，不给 EM 结果（[docs/22](docs/22-微带与腔体滤波器.md)）

- **是合成器，不是仿真器。** 六个 `filter_*` 工具回答的是"要画成什么样"：原型 g 值、
  集总 L/C（含有限 Q、一阶寄生、蒙特卡洛良率）、耦合线 `w/s`、耦合谐振器 `Qe/k/λ_g/2`、
  矩形腔**任何 `TE_mnp` / `TM_mnp`** 的截止/长度/Q（0.0.52，含 `modes_upto_hz` 的模谱与 `roughness_um`）。凡是需要全波才能回答的问题（交叉耦合、色散、分布式谐振器的实测 Q、
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
│   ├── tools.py                  # 179 个 tool 的 schema 与 handler 注册
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
│   ├── lceda_snippet.py          # 从应用 doc.api 表生成写 snippet：三档打分 + 接收者三条来源，最高档不唯一就拒
│   ├── lceda_steps.py            # 步骤脚本：录制/落盘/载入/复放，逐值比对 + sha256 自持 + 失败即停
│   ├── jobs.py                   # 后台作业：内存作业表 + 提交时刻快照 + 并发上限；取消只丢结果
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
│       ├── dfm.py                # DFM：15 条规则目录 + 能力文档体检 + 五态判定（无内置表）
│       ├── vibration.py          # 振动：Ritz 模态 + Miles/Steinberg/冲击/传递率 + 应力（degree 阶梯误差棒）
│       ├── vibration_poly.py     # 非矩形外轮廓：Green 定理精确矩 + GMS 白化 + 罚阶梯（spread/penalty_share 自述局限）
│       ├── reliability.py        # 可靠性：焊点热疲劳（Engelmaier/Miner/Norris-Landzberg）+ 跌落板面应变 + 冲击谱
│       ├── cavity.py             # 平面谐振：腔体模 + 过孔围栏 pitch/λ_g（判据外给）+ plane_q（Qc = h/δs）
│       ├── em.py                 # 全波接缝：找出 openEMS/Meep/gprMAX、版本与 handoff（不生成几何）
│       ├── filters.py            # 滤波器合成：原型 g 值/集总带通(两路对账 + 有限 Q + 寄生 + 蒙特卡洛)/耦合线反解/耦合谐振器/矩形腔任意 TE_mnp/TM_mnp + 模谱
│       ├── filtermatrix.py       # 耦合矩阵：交叉耦合零点(余子式) + 有限 Qu 插损；节点法 + 精确导纳逆变器 Π 等效，无损时对原型闭式自检
│       ├── electrothermal.py     # 电热耦合：R(T) 定点迭代 + 精确失控判据 + 板级 2-D 场（SOR）
│       ├── magnetics.py          # 功率电感：CCM boost/buck/buck-boost 占空比/电感/峰值/CCM 边界
│       ├── stackup.py           # 板材表 + 4/6 层叠 + 每层 50Ω 线宽 + λ_g/20 缝合孔
│       ├── chain.py             # 射频链路级联：Friis NF / IIP3 / kTB 灵敏度
│       └── rules.py             # 规则引擎：22 条布局 + 14 条原理图目录，缺事实 ⇒ undetermined
├── data/
│   ├── iec60664_tables.json      # IEC 60664-1 阶梯（808 步 / 32 键）+ not_covered + sources，verified:true
│   ├── ipc2152_tables.json       # IPC-2152 闭式拟合系数 + 9 个锚点（points 故意为空，原因写在 why_empty）
│   ├── magnetic_materials.json   # 12 种材料 / 16 个磁芯，每行带来源 URL（0.0.55 起）
│   ├── powder_cores.json         # 粉末磁环目录：43 尺寸 / 500 磁导率行 / 1923 料号（0.0.76 起）
│   ├── dc_bias_fit.json          # DC 偏置拟合：29 组 / 125 行 (µ, a, b, c)，印刷 p.63–65（0.0.77 起）
│   ├── toroid_mlt_reference.json # 13 行印刷 MLT（环型实测比值带的来历），每行带 URL（0.0.79 起）
│   ├── powder_curve_fits.json    # 125 行 DC 磁化 B(H) + 54 行 Δµ/µ(T) + 49 行印刷稳定性表 + 5 行偏置变体，每行带印刷页与 URL（0.0.80 起）
│   ├── material_loss_anchors.json # 9 个牌号 / 17 个厂商印刷 PV 点 / 3 个来源，每点带 printed_page、printed_as 与 qualifier（`≤` / `~` / `Max.` / 裸数字四分类，0.0.81 起）
│   └── creepage_tables.json      # 电气间隙/爬电距离阶梯（schematic_review 的隔离间距用）
├── tests/                        # 3277 个用例（含"解析前后 sha256 不变"、"闸门有牙"、真实归档形状，
│                                 #  以及拿 pcbnew / kicad-cli 当裁判的对账用例）
│   └── data/sample.kicad_pcb     # 合成样本（不是真实工程，但已通过 KiCad 校验）
├── vendor/ngspice/               # 0.0.37 随包引擎：官方 ngspice-47 未改动子集（14 MB，独立进程）
├── docs/                         # 设计文档 00~65，建议按编号顺序读（索引见 docs/README.md）
├── CHANGELOG.md
└── LICENSE
```

## 免责声明

本项目是**计算与推导辅助工具**，不是标准文本、不是仿真器、不是替代评审的判决依据。

- 载流、阻抗、λ/20 均为**闭式近似**，误差已在 `precision` 字段声明（±12%~±30%）。**电流密度例外**：
  从 0.0.63 起 `current_density_field` 解的是真 2-D 场（`div(sigma·grad V)=0`），给 `J` 的峰值**与位置**；
  但拐角处它**不收敛**（270° 内角 `J ~ r^(-1/3)`，每减半 ×1.2599），所以峰值只在 `peak_is_grid_convergent`
  为真时可引用，否则引用环读数或不那么尖的几何——**它测的是「挤在哪」，不是「什么时候熔」**（后者是
  `fusing_current_taper` 的 1-D 温度轴，两者仍然不联动）。差分阻抗另有二维场解
  （`diff_impedance_field`），但紧间距耦合没有独立参考答案，那种几何下它会自己声明
  `reference_note`。共面线（cpw/gcpw）也有场解（`coplanar_field`），它在七个几何上都比共形映射
  **读低** 1.19%~3.39%，即共形映射是乐观的那一端；共面损耗现在由场解给出，但只有零厚度值
  （真实铜箔更高约 10%，而那 10% 自己也没收敛）。共面的地默认按**无限宽**算，真实版图地留窄时传 `ground_mm`——单线 `g/2` 宽的地会抬 13.7%，差分对则只动差模 1.1% 而动共模 21.9%。差分对同理，且**差分对的数一律是 Zdiff = 2·Z_odd**；要拿宽度而不是阻抗时用 `feedline_width`，它在差分对上会把残差一起报出来。定稿前请用场求解器或打样厂的阻抗报告复核。
- 仿真层是**子集**：SPICE 的子集见上（R C L V I E G F H K S W D Q M B T O U X 与 `.noise`），
  瞬态是后向欧拉，没有自热、没有失真、没有器件电容随温度、开关没有电荷注入与先断后合；热学是 1-D/2-D 稳态与单级或多级 RC，不含 TIM 压缩、辐照与空气流场；
  板级 IR drop 只算直流、只用叠层铜厚。逐条写在每次返回的 `not_computed` / `warnings` 里。
- DFM 校验是**工艺能力核对**，不是板厂签核。它只回答「给定这份能力文档，板上哪几条越界了」；
  蚀刻公差、mask-defined pad、不写线宽的丝印、拼板与基准点、镀后环形环这 8 类静态文件里没有
  可比事实（0.0.56 起阻焊桥、丝印线宽与字高、铜到成品孔壁**已经**实测），一律
  `not_checked_by_design`，不要把它读成「通过」。
- 平面谐振只给模频与**导体 + 介质损耗**的 Q 上界（`cavity_plane_q`，`Qc = h/δs`、`Qd = 1/tan_delta`，两项之和是上界）；辐射 Q、开边失谐、Huray 结节从 0.0.60 起**另有工具**（`plane_radiation_q` / `plane_open_end` / `roughness_huray`，[docs/63](docs/63-开边辐射Q与Huray结节.md)）——但三者各自都是渐近式：辐射按窄缝 + 无限 PEC 法兰（**无角点**，对 3-D 求解器差几十个百分点）、开边按 `a/h ≥ 10` 的大长宽比式且只改频率不改 Q、Huray 只乘铜的 `Rs` 且**不与 `Rq` 互推**；**箔 profile 仍然不算**。`plane_q` 本身一行未改，只告诉你模在哪、间距是多少个 λ_g，
  不告诉你这个模会不会出问题。曲线能算了（`pdn_plane_impedance`，0.0.49，需要 `port_size_mm`），但它算的是**平面自己**——没有电容、没有封装、没有过孔，所以 Q 是上界、峰偏高偏窄、谷偏深；平面与**你列出的那 ≤8 颗电容**的反谐振由 `pdn_plane_with_caps` 报（0.0.50），它不替你选电容、也不含封装与过孔电感。导体粗糙度从 0.0.51 起可按 `roughness_um`（RMS `Rq`）算，但它只是 Hammerstad–Jensen 的一个乘子：不做 Huray 结节、不做箔 profile、不做各向异性、不做介质咬铜，也不把 `Rz` 换算成 `Rq`；不给 `roughness_um` 时输出与 0.0.50 逐位相同。电热耦合只闭合「铜 R(T) ↔ 一个集中 Rth」这一个环，不是热仿真；
  热失控判据 `dP/dT·Rth ≥ 1` 是精确的，但它继承你给的那个 Rth 的全部误差。
- 波导腔从 0.0.52 起不做 TE101 专用：任何 `TE_mnp` / `TM_mnp` 的截止、长度、`Qc`、`R/Q` 与模谱都给，但**仍然是闭式 + 一个粗糙度乘子**——没有虹膜/探针/波端口（不给带宽、匹配、外部 Q）、不算辐射 Q、不算 3-D 场图、不做 `Rz→Rq` 换算；`TM_mn0` 的长度不唯一，所以单模问直接拒答（模谱里照列，让你自己排期）。
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
