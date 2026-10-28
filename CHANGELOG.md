# Changelog

**版本口径**：从 `0.0.1` 起，第三位数字随每次提交递增（`0.0.2`、`0.0.3`…），
只有真正对外、有不兼容变化的里程碑才动第二位。不为了"看起来成熟"把版本号往上跳。

信封契约（`status` / `basis` / `precision` / `reason_code` / `authoritative`）从这个版本起冻结：
加字段可以，改语义要动第二位。

早期用 `0.1.0`…`0.7.0` 递增标注过内部阶段，已按要求收敛；那些标签只留在 git 历史里
（见文末对照表），不再作为版本号使用。

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
