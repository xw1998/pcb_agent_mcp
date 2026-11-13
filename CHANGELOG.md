# Changelog

## [0.1.12] - 2026-11-13

RK3506 核心板第十二步：**BGA 逃逸落铜 + 设计下限降到 0.1 mm** —— 429 个 BGA 球里 417 个逃出到 0.2/0.1 通孔（拒绝 0、空网跳过 12），834 个 op 一次过 `postconditions`+`token_identity`；原地 DRC 结构性违规从 562 条降到 **只剩 199 条 `via_dangling`**（内层还没接线的诚实计数）。

- **逃逸读数**：`footprints 45 / tracks 559 / vias 462 / zones 3 / nets 187`；`worst = {via_to_via_gap 0.3618, via_to_ball_pad_gap 0.1723, stub_w 0.1, via 0.2/0.1}`。官方 `ZONE_FILLER` 重铺后 `/GND 1788.09 mm²`（原 1891.837，被 256 个异网通孔各吃约 0.4 mm²）、`/+3V3 149.028`、`/+5V_IN 23.776`。
- **interstitial 偏移写成 `pitch/√2` 是硬错**：每个过孔落在斜对角球焊盘边缘（到 A3 球心 0.233 mm，焊盘半径 0.125 + 过孔半径 0.1 = 0.225）。正确是 `pitch/2`，修正后 `via_to_ball_pad_gap 0.1723`。连带推得内层容量：内层只有过孔没有 SMD pad ⇒ 每道缝只过一根 0.1 mm 线，中缝余量 0.031 mm。
- **`unconnected-(U1-NC-Pad1G2)` 让后置校验失败**：网名带圆括号，写盘后重解析与原文不一致 ⇒ `plan()` 跳过 `unconnected-*` 与 `/NC_*` 并计入 `stats["nc"]`（12 个），不再靠转义硬撑。
- **段级过滤把 `refused 52` 打到 0**：只做点级检查时 stub 中段会穿过别的球心。新增 `_pt_seg`（到球心 `SEG_BALL 0.30`）与 `_seg_seg`（stub 之间 `SEG_SEG 0.20`，相交返回 0.0），候选表扩到 ring-1..3 × 四象限。
- **`precheck_clearance_mm` 必须与 `min_clearance` 同步**：规则降到 0.1 后不同步，`clearance_no_worse` 会以 `tightest 0.0 mm` 拦下整批 op。
- **DDR 直线阶梯在物理上不成立**：U1 侧 x 序 vs U2 行主序逆序 **130/1225**；试遍 U2 的 8 种朝向 × 2 扫向，最小逆序 **133** ⇒ 必须分组 + 绕行，不是布线器参数问题。
- **丝印归零**：45 个位号移 F.Fab（`silk_overlap 18→0`）、4 段拨杆 + U2 pin-1 圆点移 F.Fab（`silk_over_copper 3→0`）；板上改了封装同步改库（`lib_footprint_mismatch 2→0`）。
- **`lib_footprint_issues 34` 是假警报**：`KICAD_CONFIG_HOME` 隔离目录缺全局 `fp-lib-table`/`sym-lib-table`，拷入后 0。另：`_rawdrc2.py` 拷板到 temp 会连带丢 `lib/`，产生假库问题，准确读法是原地跑 `kicad-cli pcb drc`。
- 未做：DDR 50 条 + J1 约 100 条信号线仍在逃逸过孔为止（见 docs/149）；不使用 `board_route`/`board_fanout`/`autoLayout`，也不写通用布线器。

## [0.1.11] - 2026-11-13

RK3506 核心板第十一步：**电源走线真的落成了铜** —— 142 段手工 45° 走线、45 个焊盘内过孔、3 块官方 `ZONE_FILLER` 铺铜；新增违规 **0**（DRC 562 与落铜前基线逐类型相同）。

- **落铜读数**：`tracks 142 / vias 45 / zones 3 / nets 187 / 无网焊盘 0-682`；`/GND 1891.837 mm²`、`/+3V3 150.486`、`/+5V_IN 23.776`。499 条 `clearance` 全是 J1 相邻半孔 0.15 < 0.20（封装固有，登记为开放项，不静默改规则）。
- **70/140 邮票孔丢网（本轮核心缺陷）**：板级 188 行 `(net …)` 被散插在 45 个 footprint **之间**，KiCad 解析 pad 时后置净表尚不存在 ⇒ pad 落 net 0。`_fixorder.py` 把净表整体前移到 `title_block` 之后 ⇒ J1 `140 withnet 140`。`shorting_items 4 / holes_co_located 8 / solder_mask_bridge 8` 全部随之消失。
- **KiCad 10 的板**：自己保存后**没有板级净表**（网络由 pad 反推），且写回会**丢弃 board 级 `net_class`**。因此 `pads()`/`audit()` 改为"pad 上的 `(net "/NAME")` 与板级 `(net 12 "/NAME")` 都读"，`check()` 的 `nets` 从 `-1` 改为按 pad 去重计数；0.4/0.2 的过孔规则必须同时进 `.kicad_pro` 的 `net_settings.classes` 与 `design_settings.rules`（否则 `via_diameter 44 + drill_out_of_range 45`）。
- **半孔必然违反 `copper_edge_clearance`**（圆心在板边、盘半径 0.425）：规则降为 ignore，同时 `audit()` 新增"走线距板边 ≥0.30（落在半孔盘内的部分豁免）"的 0.05 mm 步长采样校验 —— 降级一条规则必须补一条代码级校验。
- **斜切长度必须 ≥ 线宽**：0.30 的斜边在 0.60 宽干线上短于笔画，放大 115 px/mm 读出来像圆弧。改 `d = max(d, width)` 后 `assert_45()` 仍全在 {0,45,90,135}°，DRC 分型不变；连带把 C1 的 5V 支线从角点挪开（斜切把 y 30.92 吃掉了 0.02 mm ⇒ loose end），改为 `(29.20,30.50)→45°→(28.40,30.88)`。
- **5V 入口支线只能 0.25 宽**：半孔盘铜起于 44.575、板边 45.0、留铜 0.30 ⇒ 中心最东 44.60。0.90/0.70 两档实测越界或被判 loose end。
- **`check()` 的 DRC 必须 `refill=True`**：不重铺时 `filled_polygon` 为空、走线到铺铜距离按 0 算，那个读数不是更严而是错的。
- **`_refill.py` 去掉一个假断言**：`txt.find('(net 0 "")')` 在 KiCad 10 板上恒 -1，会让脚本在健康的板上停住；改为三分支（已存在 / 旧式净表补回 / 都无则到 `.kicad_pro` 核实 classes）。
- 未做：U1/U2 的 0.65 节距 BGA 球（需 0.1 mm 净类 + dogbone）、DDR 50 网、STAMP 非电源扇出、丝印清理、In4/B.Cu 平面。

## [0.1.10] - 2026-11-13

RK3506 核心板第十步：**邮票孔整圈重排** —— 0.1.9 的 140 个半孔不是等距环，而是每边从起始角点起排、远端空出 6.00 mm 的断环，而且每边第 1 个焊盘正好压在 R2 圆角点上。

- **缺陷实测（旧几何）**：pad 1 `(-22.5,-22.5)`（封装局部）＝板角点；N 边 pad 40 停在 `16.5`，到 pad 41（`22.5`，E 边第 1 个）之间 **6.00 mm 空档**；E 边 pad 70 停在 `6.5`，到 pad 71（`22.5,22.5`）同样 6.00 mm 空档。四边同构。
- **根因**：`edge_axis()` 用 `-HALF + off` 起排，即整段**锚在起始角点**上；同时审计 F5 用 `HALF - span/2` 算「到角点余量」——算的是**假设居中**的模型，不校验实际落点，于是 `corner_clear_mm 3.0` 一路 PASS，实际起始端余量 0.00。
- **修法**：`edge_axis()` 改半跨 `half_span = span/2` 居中起排；F5 改为**按已生成坐标实测**（`HALF - max(|min|,|max|)`），并改报错文案 `pad sits in the R2 arc`，让同类错误不可能再假绿。
- **新几何实测**：N/S `x = ±19.5`（span 39.0、角点余量 **3.00**），E/W `y = ±14.5`（span 29.0、角点余量 **8.00**）；140 盘、1.000 节距、盘 0.85 / 孔 0.55 不变（docs/143 §5 冻结值）。整圈对称、无缺口、圆角区不放盘。
- **重建链**：旧板移到 `D:/tmp/_rk_old/`（不删），`--create` → `--castellation` → `--planes`。In1.Cu 实测 `outline 1933.988 mm² / fill 1922.446 mm² / zones 1`；DRC `641`（`clearance 499` 全是相邻半孔 0.15 < 0.20、`copper_edge_clearance 140`、`isolated_copper 1`、`lib_footprint_issues 1`），与 0.1.9 同量级同构成，留待板厂半孔工艺口径裁定。
- **看图口径（两条硬读数）**：① `kicad-cli pcb export pdf --scale 1` 会把板画到页面之外——实测墨迹只剩 4887 px 且 bbox 贴左上边界（板在负 Y 区被页顶裁掉），**`--scale 0`（自动缩放）既完整又保彩色**；② pcbnew `PLOT_CONTROLLER.SetColorMode(True)` 对 PDF 不生效（实测整图 5 档灰阶、无 RGB 色）。
- **中断原因不是 MCP**：受限档下 `kicad-cli` 要建 `C:\Users\<u>\Documents\KiCad\10.0` 得 `error 5` 并被 SIGSEGV，`mkdir` 也被沙箱拒；`pcb-agent` 连接器工具列表拉取正常（50 个工具）。属写权限，不属连接器故障。
## [0.1.9] - 2026-11-13

RK3506 核心板第九步：**从图纸走到板子**——45 mm 方板 / 6 层 / R2 圆角 / 140 邮票孔落地，并把第一块 GND 平面交给 KiCad 官方 `ZONE_FILLER` 铺。
板上一根走线都没有，没有调用任何自动布局/自动布线（`tools/gen_som_board.py` docstring 首句即 `Nothing here is a route; it is a space survey.`）。

- **`create_board` 两个新 spec**：`inner_kinds`（`signal/power/mixed/jumper/user`，非法值 `EditError(ARG_INVALID)`；原来所有内层一律 `signal`）与 `corner_radius_mm`（`>= min(w,h)/2` 报错，`<=0` 退回 `rect_as_lines`）。新增 `build.rounded_rect()`：4 `gr_line` + 4 `gr_arc`（mid 落在 45° 对角线上）。**圆角板框是 Edge.Cuts 机械几何，不属禁弧**（禁弧约束铜层走线），这句话写进 docstring，防止后来者把板角当违规删掉。
- **`--create` 实测**：`45.0000×45.0000 mm / 6 layers / R2 / outline closed / thickness 声 1.6 测 1.6 / engine_drc 0`；存在即拒跑。
- **`--castellation` 实测**：J1 140 半孔（0.85/0.55、1.000 节距、中心落在 `±22.5` 即板边线上）走 `propose`+`apply_plan`，四道门全过；`stamp_pad_nets()` 从 `netmap_terminals.csv` 取 `device=STAMP`，不是 140 行即退出。
- **`plane_outline()`**：内缩 0.5、圆角 1.5、弧采样 15°，实测 **28 点 / 1933.988 mm²**（解析 1934.069）。自交伏笔已修：`range(1,n)` 漏切点再补 append 会跨接，面积一度 1964.738 > 外接盒 1936。
- **`fill.add_zone` 三处修复，每处都留失败原文**：① 内层被 `_COPPER_LAYERS=("F.Cu","B.Cu")` 硬拒 ⇒ `is_copper_name()` 正则 + 解析后 `board_copper_layers()` 精确校验（`LAYER_NOT_ON_BOARD`）；② `_available_nets` 读 arg1 拿到净编号 ⇒ `arg_text(node,2) or arg_text(node,1)`（原报错 `NET_NOT_FOUND: … no net GND; 98 nets exist, e.g. 1, 10, 11`）；③ 新增 `y_flip(board)`——按板已有 `Edge.Cuts` 的 y 符号判定翻转，因为 `create_board` 写 Y 向下而老板是 Y 向上，**判定法而非新参数**（强行翻转会打坏 `sky_star` 的正 Y zone）。写错方向的症状是 `kimathLogOverflow(): Overflow converting value -2147493567.024006 to int`。
- **`--planes` 四层逐层实跑（同文件/同多边形/同网/各次先剥净既有 zone）**：`In1.Cu` **ok（fill 1922.987 mm²，1 个填充多边形）**；`In3.Cu`、`In4.Cu` 都 `ENGINE_FAILED` 于同一个 `-2147493567.024006`；`F.Cu` `TIMEOUT`（300 s 不返回）。判别式只剩层本身——「带既有填充」「多边形形状」「6 层堆叠」「模板缺字段」逐一被排除。故 `PLANE_LAYERS_NOW = [In1.Cu]`，`GND_PLANE_LAYERS` 三层仍是设计目标；`planes()` 失败记进 `out[layer]` 后 `continue`，一层的失败不掩盖另一层的读数。
- **铺完的 DRC 如实照抄**：`not_clean / errors 637 / unconnected 42 / by_type clearance 499 + copper_edge_clearance 138 + isolated_copper 1 + lib_footprint_issues 1`，`top_errors` 全是相邻 J1 PTH 焊盘的 `间距 0.2000 实际 0.1500`（节距 1.000、盘 0.85 ⇒ 边距 0.15）。这些**不是铺铜造成**（板上无走线），邮票孔本就骑边，要按板厂工艺口径裁定，**不当缺陷清掉**。
- **修 `rounded_rect` 的圆角弧 `mid`（用户 GUI 打开报 `kimathLogOverflow` 的直接原因）**：第 1 个弧写成 `y0 + d`（到圆心 0.766 r，三点不同圆）⇒ pcbnew 算弧心溢出 `Overflow converting value -2147493567.024006 to int`；`kicad-cli` 对同一文件 stats/DRC/SVG 全绿，所以 CLI 全绿**不能**当"文件能打开"的证据。判读链：demo 板 LoadBoard 正常 → demo 压成单行仍正常（单行不是因）→ 本板与删 zone 版都 40 s 不返回 → `_noJ1`（仅板框）直接断言 → 删掉 4 个 `gr_arc` 即 rc 0 ⇒ 圆角弧是根因。修后 `LoadBoard rc 0 / zones 1 / footprints 1`，`--check` `drc 641`（`copper_edge_clearance` 138→140）
- 没做完：In4/B/Cu 平面（下一步拿 KiCad GUI 在同一块板上复现同层 zone，分清「缺参数」还是「我们写错层 id」）、45 器件手工单面布局、In3 按轨分块、手工 45° 布线

## [0.1.8] - 2026-11-13

RK3506 核心板第八步：**把 `docs/145` 冻结的网表画成原理图**，并让 KiCad 自己的网表器 / ERC / PDF 当裁判。
零板改动、零封装改动、零布线（生成器 docstring 首句即 `Nothing here is a route …`）。

- **新增 `tools/gen_som_sch.py` + `examples/rk3506_som/rk3506_som.kicad_sch`（A1 / 45 符号 / 682 标签）**：实跑
  **`components 45, nets 187, pins_expected 585, pins_found 682, pins_wrong_or_absent 0, rails_missing []`**。
  画法是**把 local label 贴在引脚尖端**而不是拉线——333 球 BGA 的符号按 EasyEDA 三泳道出脚，拉线必穿脚；
  "标签落在连接点即同网"由 KiCad 网表器证明，不是本文件的意见。
- **四条独立交叉断言 + 一条渲染门，全部带反向对照**：C1 表侧引脚数、C2 库侧引脚数（682−585=97）、
  C3 表外引脚扣掉 `no_connect`、C4 ERC 类型只能是 `isolated_pin_label`、
  C5 **ERC violation 坐标集合 == plan 单标签坐标集合**（11 点，比坐标不比文字，避开中文 locale）、
  C6 表侧单端网必须"孤立或被表外引脚终结"；T1 `TEXT_OVERLAP_MM = 1.5`（两轴都超才判）——
  实测 `pairs_on_each_other 0`、最坏 `1M11` vs `DDR_CSN` 1.14×1.43 mm。
  plan 侧**只读库与冻结 CSV，绝不读自己生成的文件**；KiCad 侧只跑网表器/ERC。
- **电源补成真电路**：`netmap_terminals.csv` 把稳压器建模成"一个以轨名命名的端点"，原理图不允许
  （源脚不存在 ⇒ ERC 无从检查），故补电感/输入输出电容/反馈分压/自举电容，值由 `docs/144` 的
  `Vref 0.6 V ±1.5 %`、`fsw 500 kHz` 算出，其余明写为设计值。四路实算：
  `+0V9_CPU 4.32 µH / 4.7 / 10.0 µF / 5.9k`、`+0V9_LOGIC 6.19 / 4.7 / 10.0 / 4.99k`、
  `+1V35_DDR 8.25 / 4.7 / 4.7 / 12.4k`、`+3V3 4.64 / 10.0 / 4.7 / 45.3k`。
  `+0V9_PMU`/`+0V9_ANA` 走磁珠（`Device:L` + `L_0603_1608Metric` 代位封装，真料号未定）。
- **J1 邮票孔 140 自生成**（不是采购件，任何库都没有）：`lib/SOM_CASTELLATION_140.kicad_sym` 由
  `som_pinout_140.csv` 按 0.1.2 的 R8 边序写，**边序是封装侧事实，符号只镜像它**（
  `tools/gen_som_footprint.py` 的 F1–F8 断言）。其余 8 颗器件全部取库出处（立创 EasyEDA → 本仓库转换）。
- **三处"静默错答案"修在生成器里**：① KiCad 10 属性隐藏必须写 `(hide yes)`，裸 `hide` 被静默忽略 ⇒
  Footprint/Datasheet/Description 真被印到 PDF 上（改后文本 span 2595→1931，−664 正是这三个字段）；
  ② 符号→图纸字段坐标必须 Y 翻转（`_rotate()` 在库帧 Y 向上做 CCW，调用处写 `x+dx, y−dy`，
  写成 `y+dy` 会关于原点镜像，U8 的 Value 压住自己的 `GND` 引脚名 3.87×1.94 mm）；
  ③ `_extent_top()` 必须按 shape kind 各取 y（对 `row[2:6]` 取 max 会把 x 当 y，RK3506 算出 109.22）。
- **排版两件**：32 对叠压引脚分离步长定 **4 格 = 10.16 mm**（1 格标签叠标签、2 格球号压标签、4 格都清空）；
  RK3506 **隐藏引脚名**（球名是四功能手册串最长 53 mm，比任何列距都宽），图上每脚显示"球号 + 网络名"，
  名字仍在库与 `netmap_terminals.csv` 里。
- **两条环境口径**：① `KICAD_CONFIG_HOME` 不能跟着 HOME 一起重定向——只重定向 HOME 时全局库失效，
  同一张图 ERC 从 11 条涨到 79 条（34 个无源器件变 `lib_symbol_issues`+`footprint_link_issues`），
  补 `KICAD_CONFIG_HOME=%APPDATA%\kicad` 后回到 11 条；② ERC 必须在 `.kicad_pro` 所在目录跑
  （`kicad-cli` 按 cwd 解析 `sym-lib-table`/`fp-lib-table`）。另记：`_run()` 已自带 `kicad-cli` 前缀，
  传 exe 路径会得到 `kicad-cli kicad-cli …`（rc=1 + 打印 help，极易误判为路径问题）。
- **一条测试基建修复**：`tests/test_step_apply.py` 原写 `from tests.test_sch_edit import …`，而本机
  `site-packages` 另有一个名为 `tests` 的包（常规包优先于命名空间包）⇒ 收集阶段必炸、整轮回归被打断；
  改成裸名 `from test_sch_edit import …`（与其余 11 个文件的 `import _toolcount` 同一套 prepend 约定）。
  **加 `tests/__init__.py` 是错的方向**：它会让 `tests/` 不再是 sys.path 项，反而打坏 11 个裸导入。
- 验证：`python tools/gen_som_sch.py --check` rc=0 且 `cross_checks_failed []`；九个 `.kicad_sym`
  `sym upgrade --force` 全部 rc=0；反向对照四条全触发（plan 掉一个孤立标签 / `table_pins` +1 /
  `outside_table` +3 / 渲染门阈值降到 1.0 mm ⇒ 报 48 对）。全量回归见提交说明。
- 没修完：**31 个电源球每引脚去耦**（下一批）、`DDR_ZQ` 缺 240 Ω、磁珠料号、SY8113 `ILIM`；
  `parts.footprint_to_kicad()` 仍采信 CANVAS 声明单位（连续第五批）。

## [0.1.7] - 2026-11-12
RK3506 核心板第七步：**把四张冻结的球/焊盘表合成唯一一张网表**，让"哪个脚在哪条网上"变成可审计的数据。
零板改动、零封装改动、零布线（本工具 docstring 首句即 `Nothing here is a route, a placement, or a copper decision.`）。

- **新增 `tools/gen_netmap.py` + `tests/test_netmap.py`（38 用例）+ 两个产物 CSV**：实跑
  **`balls 332, nets 174, terminals 593`**（地 1 / 无连接 11 / 电源 9 / 信号 153）。八个规则 R1–R8 逐条在生成时断言，
  任一不过 ⇒ 退出码 2 且**不写字节**；R8 抽成 `audit_net_ends(nets, terminals)` 以便用玩具网表直接测。
- **DDR3L 逐对同网 50 条**：两侧词表不同（`DQS1P→UDQS`、`CASN→CAS#`、`A10→A10/AP`、`RZQ→ZQ`），靠**声明表**
  `DDR_SOC_TO_CHIP` 翻译 + "芯片信号脚必须全被表覆盖" 审计；模糊匹配必然漏串。
- **32.768 kHz 晶振丢弃并具名**：手册 p8 只有内部 LF RC 与 GPIO 复用的 `CLK_32K`（`RM_IO14`），该脚落在球 N3
  且 N3 已外引为 `SPI1_CSN0`（邮票孔 123）⇒ RK3506J 无 32 K 宿主脚；器件/封装留 `lib/`，本设计不连。
- **A15 落 `NC_Y17`**：MT41K256M16（4 Gb x16）96 个脚名里只有 A0–A14，SoC 侧 A15 球 Y17 必须悬空；
  映射表删掉 `A15` 并用 `DDR_SOC_NO_CHIP_PIN` 反向看住（把它加回映射表即报错）。
- **VREF 归到 `+0V675_REF` 一条轨**（5 端：SoC 2 + DDR 2 + 分压 1），并把"正好一半"升为 R7 硬门禁
  （0.675 对 1.35/2 差 ≤ 5 mV），不是两条独立网；`RZQ` 侧为信号网 `DDR_ZQ`，电阻值仍留空。
- **邮票孔 140 端 / 98 条网**（95 信号 + GND + `+5V_IN` + `+3V3`）；`RAIL_ALIASES` 是唯一做
  `5V_IN`/`3V3_OUT`/`GND`→轨名归一的地方，信号名不做别名（孔上名字就是板级契约）。
- **一条自纠错**：初版"孔说网 X 但球在网 Y"拿 `stamp_nets[ball]` 比 `row["net"]`——两侧同源、永远为真；
  改为"先用球/引脚名规则定出该球的网，再要求孔行与它一致"，并又补了"一个球不许占两个孔""孔上的球必须在球表里"。
- **SY8113BADC 外部取证补两条**：Vref **0.6 V ±1.5 %**、fsw **500 kHz**（四处分销页一致）；**ILIM 仍缺**
  （`datasheet.eeworld.com.cn`/`hqew` 的 PDF 页 fetch 均失败）⇒ FB 分压与电感可算，限流校验继续留空并具名。
- 验证：`python tools/gen_netmap.py` 退出码 0；`python -m unittest discover -s tests -p "test_netmap.py"` **38/38**；
  `--selftest` 与全量回归见提交说明。
- 没修完：`parts.footprint_to_kicad()` 仍采信 CANVAS 声明单位（连续第四批）；网表**空间可实现性**一概未看。

## [0.1.6] - 2026-11-12

RK3506 核心板第六步：**把电源从「20 个电源组」落成一棵可算的树**，并把树上出现的两颗稳压器做成真封装与真符号。
三条审计（G4/G5/G6）原来会**拒掉 KiCad 官方自己的封装**——这轮不是让产物通过审计，是审计错了，而错误由仓库之外
的官方库证伪。零板改动（`rk3506_som.kicad_pcb` 不动）。

- **新增 `tools/power_tree.py` + `tests/test_power_tree.py`（35 用例）**：docstring 写死
  `Nothing here is a route, a placement, or a copper decision.`。`read_groups` / `check_rail_bands` /
  `check_internal` / `check_cover` / `check_unique` / `check_vref` / `check_sources` / `check_load` / `build` / `write_csv`，
  每条判据都有一条反向失败对照；任一条不过⇒退出码 **2**、不写字节。实跑 `rails 10, balls 176`。
- **10 条轨（`examples/rk3506_som/power_rails.csv`）**：`+5V_IN`（邮票孔 42–45）/ `+0V9_CPU`(SY8113,1200 mA) /
  `+0V9_LOGIC`(SY8113,800) / `+0V9_PMU`(磁珠,100) / `+0V9_ANA`(磁珠,150) / `+1V35_DDR`(SY8113,800) /
  `+0V675_REF`(1 %/1k 分压,2) / `+3V3`(SY8113,1600) / `+1V8_ANA`(AMS1117-1.8,250) / `GND`(141 球)。
- **176 球逐球归档（`power_balls.csv`）**：`GND 141 / +0V9_LOGIC 7 / +3V3 7 / +1V8_ANA 6 / +1V35_DDR 5 /
  +0V9_CPU 3 / +0V9_ANA 2 / +0V675_REF 2 / INTERNAL 2 / +0V9_PMU 1`，合计 176。
- **三条来自手册的「没有」，不编数**：全文检索 `sequence` 与 `DCDC` **各 0 命中** ⇒ 手册无上电时序表、
  无 DCDC 设计指引；Table 2-2/3-2 不给每轨电流 ⇒ `load_ma` 一律标注 `design value` 并在 `basis` 列写依据。
  `1A13` 在源表出现两次（`VSS` 与 `ACODEC_ADC_AVSS`）= 手册自身缺陷 ⇒ 登记不去重，唯一性下沉到 rail 级，双列点名。
- **p39 Interface 表把 `ACODEC_ADC_VCM`(1B11) 与 `ACODEC_ADC_AVDD1V6`(1B12) 标 O=内部输出** ⇒ 它们是 SoC 自出，
  **不接稳压器**（接了两电源对接），轨名记 `INTERNAL`，只去耦。
- **四项取值理由**：CPU 轨固定 0.95 V 不做 DVFS（核心板不是整机）；`PMU`/`ANA` 用磁珠不分设稳压器
  （`PMU_LOGIC_DVDD0V9` 与 `LOGIC_DVDD` 的带 **0.81/0.90/0.99 与 0.855/0.90/0.998** 逐值相等，独立性是去耦要求不是稳压器要求）；
  VCCIO 全 3.3 V 且 SDMMC 不切 1.8 V（**限制**，要跑 UHS-I 须加负载开关）；DDR3L 单 x16 **无 VTT**、
  VREF=0.675 V 由 1 %/1k 分压且 `check_vref` 硬门禁「必须正好 VDDQ/2」（查定义不查电阻）。
- **`8.220 W` 的口径写死**：只累加 `src == +5V_IN` 且 `kind == regulator` 的轨，项是 `typ_v × load_ma / 1000`
  （0.95×1200 + 0.90×800 + 1.35×800 + 3.30×1600）⇒ 这是**四条 5 V 轨的输出和（下界）**，**不含任何效率系数**；
  AMS1117（src=+3V3）、两条磁珠轨、分压轨**故意不进**（重复计数 / 不产功率 / 拿自己设的数证明自己）。
- **G6 包络改用标题 LS（`--lead-span`）**：旧规则 `body/2 + 0.75` 会拒掉 KiCad 官方 `TSOT-23-6`
  （6 盘 1.325×0.6 @ x=±1.1375 ⇒ 外缘 **1.80** > 0.80+0.75）⇒ 改 `max(body/2, LS/2) + 0.75`。
- **G5 节距轴必须测（`min_grid_gap_info` 返回 `axis`）**：两列三行的 SOT-23/TSOT-23 节距在 **y** 上，
  拿 x 判会把 0.800 mm 盘判死而沿 y 的真实间隙是 0.418 mm。无节距邻居的盘（SOT-223 散热盘）**不许猜**：
  单候选接受、双候选抛 `GeometryError`（"state the box explicitly"）。
- **G4 改焊盘并集 bbox 居中**：SOT-223 是 1+3 非对称盘组，质心偏 **1.42 mm** 而图形完全居中 ⇒ 质心判据会误拒。
  `--pad-box NUM:WxH`（可重复）作显式 override 且写进报告，由居中审计**反向确认**。
- **courtyard 必须包住焊盘**（`emit_generic`：`max(hw, pw_max) + margin`）：焊盘合法越出本体
  （SOT-223 4.07 对 1.75、TSOT 1.60 对 0.80），只增不减 ⇒ 三份既有封装**仅 courtyard 行变化**
  （1.85/1.50→2.0501/1.70、1.275→1.5001、2.35→2.8751），`diff` 过滤 `descr`/`tags` 后零几何差。
- **两颗稳压器的取件与产物**：**SY8113BADC**（LCSC C78989）→ `Package_TSOT-23-6_L2.9-W1.6-P0.95-LS2.8`
  （6 盘 0.8×0.532 @ x=±1.2, y=±0.95/0）+ `.kicad_sym`（6 pin）；**AMS1117-1.8**（C351432）→
  `Package_SOT-223-4_L6.5-W3.5-P2.30-LS7.0`（3 信号盘 2.47×0.98 @ x=+2.835 + 散热盘 **(2.47, 3.6)** @ x=−2.835）+ `.kicad_sym`（4 pin）。
  `library/` 各 6 件（`.efp`/`_meta.json`/`_source.json`/`_symbol.efp`/`_symbol_meta.json`/`_symbol_source.json`），
  全部 `copyright: true`。
- **外部尺子（逐行读自本机官方库，作用是证伪我自己的规则）**：`Package_TO_SOT_SMD.pretty/TSOT-23-6.kicad_mod`
  与 `SOT-223-3_TabPin2.kicad_mod` — 后者散热盘 **2.0×3.8 @ x=+3.15（长轴在 y）** ⇒ 立创文档的原样读法同向，
  交换读法 (3.6, 2.47) 会把 bbox 中心推到 x=−0.28 被 G4 拒（已写成用例）。
- **KiCad 版本口径（必须写死）**：本仓库封装是 **KiCad 10 原生格式**（`(version 20260206)` +
  `(duplicate_pad_numbers_are_jumpers no)`），**KiCad 8 一律拒收**（去掉 jumpers token 仍失败 ⇒ 是 version 号本身被拒）；
  验证一律用 `/d/KiCad/10.0/bin/kicad-cli.exe`（10.0.7）。且两关输出目录口径**相反**：
  `fp upgrade -o` 目标须**不存在**、`fp export svg -o` 目标须**先存在**。
- **验证**：`test_power_tree` **35/35 OK**、`test_smd_footprint` **34/34 OK**（新增 5 例 + `SotRulerTest` 3 例外部尺子）；
  未回归 `test_spinand_footprint` 22/22、`test_bga333_footprint` 14/14、`test_ddr3l_footprint` 19/19、`test_som_pinout` 18/18；
  KiCad 三关全过（`fp upgrade`「封装库未更新」/`fp export svg` **8 张**含两颗新件/两符号 `sym upgrade --force` 真重写），
  两颗封装栅格化后视觉复核（盘组、本体、丝印、1 脚圆点、courtyard 均无误）。
- **没修完（如实）**：**SY8113 的 Vref / fsw / ILIM 不在本仓库** ⇒ 反馈分压、电感、输出电容**全部留空不编**
  （`fb_divider` 空，下一批第一件事是取厂商手册再算）；`parts.footprint_to_kicad()` 仍直接采信 CANVAS 声明的单位
  （照样会产出 486 mm 宽封装，**连续第四批**）；符号形状仍是库的原形状转写（未按功能分块重画）；
  原理图、布局、布线、内层平面未开工。

## [0.1.5] - 2026-11-12

RK3506 核心板第五步：再取四颗器件（SPI-NAND、24 MHz、32.768 kHz、复位按键），并把转写从「一器件一脚本」抽出**通用 SMD 工具**；同一轮发现并修掉一个**许可级缺陷**：此前所有产物都没有写立创官方库要求的出处声明。零板改动。

- **新增 `tools/gen_smd_footprint.py`（通用 SMD 转写）**：既然器件之间除了「焊盘在网格上」没有共同点，就把期望值放到命令行、把审计留在代码里——`--pads` / `--body WxH` / `--grid`。八条审计 **G1-G8**：G1 盘数与编号；G2 单位可解（本体落在 0.5–60 mm 且网格落在 0.2–8 mm 的候选唯一）；G3 画出来的本体等于 `--body`（±0.02，`--body` 取**库标题**这一独立字符串，不被图移动）；G4 坐标不重且质心 (0,0)；G5 每个盘 box 可解；G6 盘不出 courtyard 包络（本体/2 + 0.75）；G7 丝印存在；G8 盘距谱等于 `--grid`。`--grid-tol` 是**有理由的**宽松口：SKRPACE010 标题写 `P2.20` 而实际画 `2.15`，默认 0.03 会误报。
- **新增 `tools/gen_lceda_symbol.py`（符号转写）**：S1 pin 唯一非空；**S2 符号 pin 号集合必须等于封装焊盘号集合**——符号第 9 脚没焊盘（或反之）是错误不是警告。
- **四颗器件的取件与产物（全部来自立创官方库）**：
  - **SPI-NAND W25N01GVZEIG**（LCSC C907680，WSON-8 EP 6×8，1 Gb）→ `Package_WSON-8_L8.0-W6.1-P1.27_W25N01GVZEIG.kicad_mod`（9 盘 = 8×oval 0.56×1.40 + 1×rect 4.30×3.40 居中）+ `W25N01GVZEIG.kicad_sym`（9 pin）。**披露两处不擅自调和**：标题 W6.10 与绘制 6.0000 差 0.10（画的多边形胜过标题字符串）；文档里 4 个 `VIA` 行与 8 个 layer-50 `FILL` 行不转（footprint 装不下 via，mask/paste 由 KiCad 派生），写进 `report["skipped"]` 不静默丢。
  - **24 MHz X322524MSB4SI**（LCSC C15643，SMD3225-4P）→ `Package_Crystal_SMD_3225-4P_L3.2-W2.5_X322524MSB4SI`。**外部尺子**：KiCad 官方 `Crystal_SMD_3225-4Pin_3.2x2.5mm` 的 4 个 land 是 1.4×1.2 @ (±1.1, ±0.85)，与我们从立创文档解出的**逐值相同**（用例逐值断言）。
  - **32.768 kHz NX2012SA**（LCSC C891748，SMD2012-2P）→ `Package_Crystal_SMD_2012-2P_L2.05-W1.2_NX2012SA`。两家库的 land **本来就不同**：立创 1.0×1.2 压 1.5 pitch，KiCad 0.6×1.1 压 1.4 pitch。用例只断言"我们的值来自文档"，并把官方值作参照披露，**不改别人的焊盘**。
  - **复位按键 SKRPACE010**（LCSC C139797，4.2×3.2 4P）→ `Package_Switch_SMD_4.2x3.2-4P-P2.20_SKRPACE010`；x 间距 2.1 与 Alps SKRK 官方库一致。
- **许可级缺陷（本轮真发现）**：立创 `.efp` 的 `HEAD` 行写明「引用/生成/输出官方库全部或部分内容时**必须显著声明出处为"嘉立创EDA/EasyEDA官方库"并给出两个官网链接**」。此前 0.1.3（BGA333）、0.1.4（DDR3L）、0.1.5 的产物**全部没有**。修法收敛到**唯一写入点** `lceda_fp.with_source()`（幂等，已声明则不重复追加），`parts.symbol_to_kicad()` 同样内置；六个 `.kicad_mod` 与六个 `.kicad_sym` 已重生成，用例扫目录断言一个都不缺。
- **单位陷阱第三次复现**：三份新文档 `CANVAS` 也全写 `"mm"`，坐标真值仍是 mil ⇒ `solve_scale()` 用「本体包络 + 网格包络」解唯一候选（**不拿本体当尺子**，否则 G3 自证循环）。
- **焊盘行下标定案**：`[5]`=编号、`[6]`=x、`[7]`=y、`[10]`=形状、**`[14]`=rotation**（`[13]` 是每脚小偏移，上一版误读 `[12]` 导致"两解都活"的假象消失）。`rot` 只当**候选**：`resolve_pad_box()` 先用"压 pitch 就否决"过滤，两解都活时取**沿 pitch 更窄**者（外在依据 = KiCad 官方 2012 用 0.6×1.1 压 1.4）。
- **新增 `tests/test_smd_footprint.py`（26 用例）**：G1(盘数/重号/空号)、G2(无候选/网格测不出)、G3、G4(重坐标/偏心)、G5、G6、G7、G8(含 `--grid-tol` 的正反两面) 各一条反向；正向含三份文档全过、3225 与官方库逐值一致、2012 的 tie-break 选中窄轴、产物的三处硬形状（焊盘数/无 `justify center`/`(version 20260206)`）、2012 的 pad 顺序**照抄文档不重排**、出处声明幂等与两份常量逐字相同。
- **验证**：`test_smd_footprint.py` **26/26 OK**；`test_bga333_footprint.py` 14/14、`test_ddr3l_footprint.py` 19/19、`test_spinand_footprint.py` 22/22 均未回归；`kicad-cli fp upgrade`「封装库未更新」、`fp export svg` 出 6 张图、六个 `.kicad_sym` 全部 `sym upgrade --force` 写出新格式文件。
- **没修完（如实）**：`parts.footprint_to_kicad()` 仍**直接采信 CANVAS 声明的单位**（对这类文档会产出 486 mm 宽的封装），本轮仍靠自写生成器绕开，缺陷留着；电源树器件（20 个电源组的稳压器与时序）、原理图、布局、布线、内层平面依旧未开工。

## [0.1.4] - 2026-11-12

RK3506 核心板第四步：把「取几何」从一次性脚本变成**仓库工具**，并用它取了 **DDR3L 颗粒**（x16 4Gb）的封装与符号。零板改动。

- **新增 `tools/fetch_lceda_part.py`**：立创官方库取件工具（search / fetch）。fetch 产出 `<stem>.efp`（解密文档）+ `<stem>_source.json`（uuid/key/iv/title/version/字节数/`doc_sha256`/版权声明）+ `<stem>_meta.json`（原始组件记录）；已存在 `.efp` 不覆盖（需 `--force`）。失败分支全部具名：key≠32B、iv≠12B、GCM 拒绝、明文非 gzip、明文不像文档。**实测坑：目录检索返回的 uuid 是器件记录，直接喂 components/ 会得到 `component not found (code=1111113)`；要用 `attributes.Footprint`/`attributes.Symbol` 里的 component uuid。**
- **新增 `tools/lceda_fp.py`**：通用转写模块。`solve_scale()` 把单位当**方程**解（两个候选、唯一命中，零/双命中即报错）；`layer_bbox()` 按层取几何，兼容 `POLY` 扁平点列与 `CIRCLE`（`FILL` 的几何在 `row[7]` 而不是 `row[6]`）；`emit_footprint()` 出 KiCad 10 原生 `.kicad_mod`。
- **新增 `tools/gen_ddr3l_footprint.py` + 产物**：DDR3L **MT41K256M16HA-125:E**（LCSC C248057，**FBGA-96 9×14**）→ `Package_FBGA-96_L14.0-W9.0_MT41K256M16.kicad_mod`（96 盘）与 `MT41K256M16.kicad_sym`（96 pin）。八条审计 A1–A8（96 盘/编号唯一、单位解出且最近邻谱只有 0.8、盘径单一且与 0.45 球径差 ≤0.03、本体 9×14、行字母 A..T 16 行 / 列 {1,2,3,7,8,9}、坐标不重、质心 (0,0)、盘不出本体），任一不过非零退出、不写字节。
- **单位陷阱第二次复现**：该文档 `CANVAS` 也写 `"mm"`，但本体 ±177.165/±275.595 raw → **9.0000×14.0002 mm**、X 跨 251.96 → **6.3998**（8×0.8）、Y 跨 472.44 → **12.0000**（15×0.8）、盘 18 → **0.4572 mm**。⇒ CANVAS 单位在本库不可信是**两次实测**，不是一次性事故。
- **新增 `tests/test_ddr3l_footprint.py`（19 用例 = 6 正向 + 11 反向 + 2 交叉）**：反向逐条打掉一个审计通道（声明 mm 仍须解成 mil、双候选都不命中、少焊盘、重号、挪中心焊盘 E2 半 pitch、盘径错、本体错、无本体、行字母写成 O、列写成 6、坐标重、质心偏）。两条**交叉判据**查两侧能不能对接：符号的 96 个 pin number 与封装的 96 个焊盘号**集合相等**；符号里 DQ0–DQ15 与 LDQS/UDQS/LDM/UDM/RAS#/CAS#/WE#/CK/CS#/ZQ 全在。
- **KiCad 10 又三个语法坑**：`(size 1.27)` 单值字体尺寸非法（必须 `(size 1.27 1.27)`）、`(justify center)` 非法（居中=不写 justify）、`(generator pcb_agent_mcp)` 必须带引号。三者报错都是同一句「无法加载库」⇒ 只能二分定位（本轮手法：删整块 property 能过 ⇒ 逐条加回 ⇒ 先抓 justify ⇒ 再抓单值 size）。
- **验证**：`python -m unittest discover -s tests -p test_ddr3l_footprint.py` **19/19 OK**；`python -m unittest discover -s tests -p test_bga333_footprint.py` **14/14 OK**；`kicad-cli fp upgrade` 输出「封装库未更新」、`fp export svg` 出图、`sym upgrade` 输出「用更新的格式保存符号库」。
- **没修完（如实）**：焊盘 0.4572 是库值（Micron land pattern 未核到）；DDR 符号形状仍是库的 25.4×124.46 大矩形（是否按功能分块重画未决定）；SPI-NAND / 晶振 / 复位 / 电源树器件、原理图、布局、布线、内层平面均未开工。

## [0.1.3] - 2026-11-12

RK3506 核心板第三步：把 0.1.2 记的「⛔ 333 球坐标缺失」这条硬阻塞自己解开——不再向用户要资料，改走立创 EDA 官方库在线接口取到 333 球真实几何，解密后落成 KiCad 封装与符号。零板改动（天空星板不动，`rk3506_som.kicad_pcb` 不动）。

- **取几何链路（可复用的四个步骤）**：① `POST https://pro.lceda.cn/api/v5/devices/search`（`{"wd":"RK3506","page":1,"pageSize":10}`，匿名可用）→ 命中 RK3506J **LCSC C48642327 / FBGA-333L**，`device_uuid 128603d95e744add8ee01d5f70f34383`、`footprint_uuid d190bd3d49d2453ebaeb5200af6bd2ad`、`symbol_uuid 0afac4e6936a4bec9702ed842676ae84`；② `GET /api/v5/components/<uuid>` 返回 `dataStrId` + `key`(32B) + `iv`(**12B**)；③ 下载密文 → **AES-256-GCM**（`AESGCM(key).decrypt(iv, raw, None)`，tag 在尾部）→ 拿到 **gzip** 流 → `gzip.decompress` → EasyEDA 文档文本；④ `HEAD` 行自带版权声明（**嘉立创EDA/EasyEDA 官方库**），故 `.efp` 原文档随库留在 `examples/rk3506_som/library/`，来源凭证全量落 `lceda_source.json`（含 key/iv），并写明**只作几何来源、不作为库再分发**。旧 `easyeda.com/api/products/...` 一律 **403**，不要再走。
- **比例不“读”而是“解”**（本轮最贵的一条）：该封装 `CANVAS` 行写 `["CANVAS",0,0,"mm",1,1,0.3937,0.3937]`，但坐标真值是 **mil**。按 mm 解释会得到 486 mm 宽的器件（不可能）；用手册当尺子才能证明——pad 直径 9.843→**0.250 mm**、X 跨 486.78→**12.364 mm = 手册 E1**、Y 跨 420.40→**10.678 mm = 手册 D1**、本体 ±261.8105/±222.4405→**13.300 × 11.300 mm = 手册 E × D**。于是 `solve_scale()` 在 `("mil",0.0254)` 与 `("mm",1.0)` 两候选中**用 D1/E1 当尺子解唯一命中**，命中数 ≠ 1 即 `SystemExit`，不写任何字节。
- **几何结论**：**333 焊盘**（手册具名 332 + 库多出 `1G2`），两组阵列双间距——外圈稀疏阵 163 球（`<行A..Y><列1..23>`，20 行）行/列距 **22.13 mil = 0.562 mm = 手册 e**，bbox 12.364×10.678；内密阵 170 球（`1<行A..N><列1..14>`，13 行 14 列共 182 槽位实装 170）间距 **25.59 mil = 0.650 mm = 手册 e1**，bbox 8.45×7.80；两族行字母 20+13=**33**，与手册「33 行字母」吻合。焊盘取库的 **0.250 mm**（手册 **b=0.30 是球径不是焊盘**；已 grep 证实手册**无 land pattern**）。
- **新增 `tools/gen_bga333_footprint.py`**（docstring 首行 `Nothing here is a route; it is a package geometry transcription.`）：读官方文档 → 解比例 → **八条审计 A1–A8**（pad 数/编号唯一、比例由 E1/D1 解出、间距谱只允许 0.562/0.65、焊盘直径单一、本体 13.30×11.30、球名与手册 332 逐一致且多出只能是 `1G2`、坐标无重复、球阵中心在原点）→ 生成 `.kicad_mod` 与球表 CSV。**任一审计不过即非零退出、一个字节都不写**。
- **新增 `tests/test_bga333_footprint.py`（14 用例，11 条反向失败对照）**：正向证「比例是解出来的不是读出来的」「真实文档审计通过」「产物 333 pad / 括号平衡 / size 0.25 / 含 1G2 / 跨度 ≤12.3645·10.6785」；反向逐条打掉审计一个通道——声明 mil 实为 mm、两候选都不命中、少焊盘、重号、挪中心焊盘 `1G7` 半拍（跨度不变但最近邻多出第三间距）、焊盘直径错、本体尺寸错、找不到本体、多出非 `1G2` 的球、坐标重复、球阵不居中。
- **新增产物**：`examples/rk3506_som/lib/RK3506.pretty/Package_WBBGA-333L_RK3506J.kicad_mod`（36,418 B，KiCad 10 原生，`kicad-cli fp upgrade` = 「封装库未更新」、`fp export svg` 成功）、`examples/rk3506_som/lib/RK3506.kicad_sym`（64,457 B，**333 pin**，pin 名取手册功能名、number 为球号，`sym upgrade` 通过）、`examples/rk3506_som/bga333_balls.csv`（334 行，`1G2` 标 `named_in_datasheet=no`）。
- **KiCad 10 语法三坑**（写错就整库加载失败）：封装头必须 `(version 20260206)`（`20240108` 报 Unable to convert library）、元件属性用 `(property "Reference" …)`（不是 `fp_text`）、`(tags …)` **只接受一个字符串**。另：括号必须平衡、`-0.0` 要归零。
- **两条明确不声称**：courtyard（本体 +0.25 mm）与 1 脚标记描边（库给的是半径 0.05 mm 的 circle，只加 0.15 描边使其可见）都是**工程默认值**，报告里以 `courtyard_rule` / `pin1_marker_rule` 字段明示，不当手册事实；`1G2` 手册未具名，保留但标 NC 待定。
- **验证**：`python -m unittest discover -s tests -p test_bga333_footprint.py` **14/14 OK**；`python run_server.py --selftest` **366/366**。
- **没修完（如实）**：`src/pcb_agent_mcp/parts.py` 的 `footprint_pads_mm`/`footprint_to_kicad` 仍**直接采信 CANVAS 声明的单位**，对这份文档会产出 486 mm 的错误封装（本轮用自写生成器绕开，缺陷留着）；DDR3L 颗粒、SPI-NAND、电源器件尚未取库、未选型；原理图、布局、布线、内层平面未开工。

## [0.1.2] - 2026-11-12

RK3506 核心板（SOM）第二步：把「邮票孔引哪些脚」变成可复现的规则表演算，并修掉上一版写错的半孔几何。零板改动（天空星板不动）。

- **新增 `tools/gen_som_pinout.py`**：从 `pinmap_rk3506j.csv` 确定性生成 `examples/rk3506_som/som_pinout_140.csv`（140 脚：95 信号 + 37 GND + 8 电源），规则 R1–R8 写在文件头且**每条都在跑完时断言**，任一条不满足即失败退出，不输出半成品。
- **数量闭合可推**：161 具名信号球 − 55 DDR3L − 11 板载/内部占用（24M 晶振 2、CPU_AVS 1、VCM/AVDD1V6 2、SPI-NAND FSPI 6）= **95**；+ GND 37 + 电源 8 = **140**（2×40 + 2×30 @1.0 mm）。
- **修掉 §5 两处自造数**：上一版把半孔写成「孔 φ1.0 / 盘 φ1.6 / 四边 35 pin」。盘 1.6 > 间距 1.0 ⇒ 相邻焊盘会连成铜带，物理上不成立。按「间距 1.0 是硬约束」反算：间隙下限 0.15 ⇒ 盘 ≤0.85；嘉立创半孔要求盘 ≥ 孔 + 0.30 ⇒ 孔 ≤0.55。定 **孔 0.55 / 盘 0.85 / 间隙 0.15 / 环 0.15**，跨度 40 脚 39.0 mm、30 脚 29.0 mm（≤ 45 − 2×2）。
- **边分配**：N 40 = LCDC 28 + GND 12；E 30 = 5V×4 + MIPI 6 + USB 6 + SARADC 4 + ACODEC 2 + GND 8；S 40 = SDMMC 6 + RMII0 9 + RMII1 9 + 3V3×4 + GND 12；W 30 = RESET + UART0 2 + SPI0 4 + SPI1 5 + SAI0 7 + SAI1 4 + 时钟 2 + GND 5。最长无地段 6 脚。
- **新增 `tests/test_som_pinout.py`**：18 条用例，6 条正向 + **12 条反向失败对照**（把 GND 插进 USB 差分对、拆开 MIPI 时钟对、接口跨边/互插、GND 砍到 6 个、塞进 DDR 球、球重号、电源贴近 USB、盘间隙 0.05、半孔环 0.075、边序颠倒、缺脚）——保证审计函数被写成 `return []` 时测试会红。
- **板载存储取舍留痕**：SPI-NAND 走 FSPI（6 球内部占用，6×8 mm 比 eMMC 11.5×13 省面积），SDMMC 6 球外引给底板做 SD/eMMC；两种存储不争同一组球。
- **明确不声称**：物理排布与创龙 SOM-TL3506 同构（2×30+2×40 @1.0 mm，已核其规格页），但逐脚定义是自研的，**不声明可直接换板**。
- **验证**：`python -m unittest discover -s tests -p test_som_pinout.py` 18/18；`python run_server.py --selftest` **366/366**（110.3 s）。
- **仍 ⛔ 硬阻塞**：333 球坐标表缺失，BGA 封装与原理图全连接不动。

## [0.1.1] - 2026-11-12

RK3506 核心板（SOM）立项，并把「数据源残缺」这个坑做成可复用的教训。零代码改动、零板改动（天空星板不动），只加文档、球表数据与一块新空板骨架。

- **`docs/143` + `examples/rk3506_som/`**：`pinmap_rk3506j.csv`（332 行）、`power_groups_rk3506j.csv`（172 行）、`ddr3l_signals.csv`（60 行）、`rk3506_som.kicad_pcb`（6 铜层空板骨架）。
- **根因是一条下载断点**：上一轮 `RK3506J_datasheet_V1.3.pdf` 只有 1,231,973 B（完整件 2,724,252 B），是下到 45% 就断、无 `%%EOF` 的残件。PyMuPDF 能靠重建 xref 打开 50 页，页数看着对，却按缺字吐文本流 ⇒ 球号/引脚名重复末位被吞（`A11→A1`、`1F11→1F1`、`DDR3_A11→DDR3_A1`、`DDR_DQ11→DDR_DQ1`）。上一轮用「重复末位补位」的启发式去补，得到两处**真错数**：`DQ1=Y3 / DQ11=Y12` 写反（正确 `DQ1=Y12`、`DQ11=Y3`），以及把 `LOGIC_DVDD` 球位读成 `1F1 1H1 1J10`（正确 `1E4 1E5 1E6 1F11 1G11 1H11 1J10`）、把 `SYS_PLL_AVDD1V8` 判成「手册未给球位」（正确 `1H1`）。
- **重解析后的球数对账**：Table 2-1 **332 具名球**（旧判 290 是残件缺行；`1C/1E/1G` 三行不是「手册没有」）；行字母 33；VSS 140 + `ACODEC_ADC_AVSS` 1（`1A13` 同名球在两表都出现，手册自身重复）+ 电源 29 + 信号 161；封装 333 球与具名球差 **1**，手册未列、无据不猜。
- **DDR3L 60 球逐球**（55 信号 + 5 `DDR_VDDQ`），`RZQ=1L8`、`VREF_CA=1L10`、`VREF_DQ=1L11`、`OPEN0/1=1L6/1K5`；A0–A15、DQ0–DQ15 无重号无缺项。
- **电压全部改抄 Table 3-2**：CPU 0.855/0.95/1.103、LOGIC 0.855/0.90/0.998、PMU 0.81/0.90/0.99、DDR3L 1.283/1.35/1.418、1.8V 组 1.62/1.80/1.98、3.3V 组 3.00/3.30/3.63；`Fosc` 24 MHz、A7 上限 1.2 GHz、RK3506J −40~85℃。`ACODEC_ADC_VCM`(1B11) 与 `AVDD1V6`(1B12) 两张表都没给 ⇒ 记「未定义/待核」，不接电源只留测试点。
- **骨架自证**：`create_board(width_mm=45, height_mm=45, thickness_mm=1.6, copper_layers=6)`，板框 45.0000×45.0000 `closed`、板厚引擎实测 1.6 与声明一致、`kicad-cli pcb drc` **0 违规**。**内层类型仍是 signal**（L2/L5/L6 的 GND 平面与 L4 的 PWR 分区要 `add_zone` + 官方 `--refill-zones`，前提是有网表）。
- **⛔ 硬阻塞**：333 球坐标表缺失（手册无 ball map 页，本地无 RK3506 库）⇒ 不生成 BGA 封装、不编球坐标；需官方 PCB 库 / RK3506-EVM 工程 / 带坐标 ball map / 立创 EDA 模型任一。
- **口径**：「因为文档残缺而得到的否定结论」不能当事实——下载件先验 `%%EOF` 再解析。

## [0.1.0] - 2026-11-12

第一个打标签的里程碑版本（此前 `0.0.x` 只递增版本号、不打标签；上一次标签是 `v0.0.128`）。零行为改动，只收口版本口径 + 记录当前板状态。

- **版本号两处同步**：`src/pcb_agent_mcp/__init__.py`、`pyproject.toml` 由 `0.0.151` 改为 `0.1.0`（跨越式升级，不是 0.0.151 的延续）。
- **里程碑内容 = 天空星板 1:1 复刻收口**：`examples/sky_star/sky_star_routed.kicad_pcb` 现为 **arc 0 / 929 段（F.Cu 435 + B.Cu 494）/ 172 过孔 / 458 焊盘 / 96 封装 / 103 已布铜网 / 未连接 54**；Edge.Cuts 43×71 mm 居中于 A4 图页。
- **权威 DRC**：186（`lib 96 + silk 27 + shorting 19 + clearance 10 + mask 34`），优于圆弧版基线 188；全部铜层走线只走轴向或 45°，零圆弧、零 90° 直角转弯；铜全部由官方 `ZONE_FILLER` 生成。
- **明确没修完**（不得读成已修好）：`$1N4905`（P1 pad 10 拐角，尖点钉在焊盘内，圆弧版本来就 shorting，45° 无解）；排针块 65 条（`docs/141` 定档 1:1 互锁、暂停）；`+3V3` 等 17 个网有焊盘无铜；122 条非正交非 45° 段是原板继承、按 1:1 口径未动。

## [0.0.151] - 2026-11-12

用户一句「不要用圆弧线」：板上 **151 条铜层圆弧全部换成直线**（`docs/142`）。零代码改动，只改板。

- **支点**：151/151 都是 90° 拐角圆角（`w=0.15`、R≤1.2、拱高≤0.351），而 **90° 圆角的弦恒为 45°** ⇒ 删弧加弦就在 45° 规则内，端点不动、网不变。落地 **`arc=0 / segment=928 / 未连接 54`**（与换弧前一致）。
- **弦比弧靠外**：弧最外 `0.414R`、弦整条在 `R/√2 = 0.707R` ⇒ DRC **188 → 197**，9 条 0.0303–0.1455 撞到原本「弧刚好绕过」的过孔/焊盘。
- **修法只有改腿长**（弦是唯一几何）：缩短 `L<R` 让斜切朝拐角尖点平移；并证明**腿只变短 ⇒ 新腿是旧腿的点集子集 ⇒ 腿的余量只可能变好 ⇒ 只需判斜切自身**（搜索空间 3 段→1 段）；`L>R` 时腿变长，必须 `min(斜切, 腿)` 一起评。14 条紧弦 **9 条可重建、8 条落地**，余量 0.030–0.150 → **0.178–0.372**。
- **两处收尾**：`OSC_IN` 2.8 mm 横段悬空（补 45° 上抬到弧端 `(148.9,92.9)`，**不许补 90° 竖段**）；`OSC_OUT` 0.1033（腿 0.423→0.30、竖桩 0.101→0.224）。
- **终值**：`arc 0 / 929 段 / DRC 186（优于圆弧基线 188）/ 未连接 54 / shorting 19 + clearance 10 + mask 34`；151 条弦全精确 45°，新加几何也只走轴或 45°。
- **两个坑**：`add_track` 的 `y` 吃文件坐标（负值= `-y_up`），第一次写反 ⇒ 151 条弦整段镜像、未连接 54→**337**；`for key, pt in ((x1,y1),(x2,y2))` 会把二元组拆成两个 float，`key` 变成 x ⇒ 245 条候选弦被读成 **0** 条。
- **没修完**：`$1N4905` B.Cu（P1 pad 10 拐角，尖点到焊盘中心仅 0.43 mm 且钉在焊盘里，**圆弧版本那里本来就是 shorting**，45° 无解）；排针块 65 条（`docs/141` 已定档 1:1 互锁、暂停）；122 条非正交非 45° 段是原板继承、未动。

## [0.0.150] - 2026-11-12

立创天空星开发板第一次做真通孔（`docs/141`）。**通孔化是完成的，排针块那 65 条 DRC 没修完**。

- **新 op `set_pad_drill`**（`edit/ops.py`，OPS 45 个）：给已有 pad 加孔，`drill`/`type`（`thru_hole`|`np_thru_hole`）/`plating`；保留原 `at`/`size`/`layers`/`net`，只把 `smd` 换成 THT 并写 `(drill …)`。`tests/test_ops_edit.py` +196 行。
- **落板**：88 个 `thru_hole` + 32 个 `np_thru_hole` = **120 个钀孔**（全板原先只有过孔有孔）。
- **通孔化当场把“行高不够”变成真实约束**：P2 排针 40 脚全 THT，一条 0.15 线要穿 pad 行必须离 pad 中心 ≥ 1.125，而行距 2.54 ⇒ **每条行缝只容 1 条线（有效宽 0.29 mm）**。原先“从内列 pad 正上方横穿”的十几条 B.Cu 全部变短路。
- **DRC 279 → 188**：依次 gapfix 18 op / batch1、`_rowup` 30 / `_rowy2` 36 / `_pc7fix` 2 / PC0 6 + 删悬空段。基线 123（`lib_footprint_issues` 96 + `silk_over_copper` 27）不动；真违规 **65**（shorting 21 + clearance 9 + mask 35，后者全是短路的副产物），未连接 54，未布线 16 网。
- **根因定档（不是七个独立坏点）**：`$1N4752` 自己的 pad-8 逃生线 B.Cu `y=88.49`、`x 152.6→166.75`（11.6 mm，还横穿 pad 7 中心）把区域一分为二，合法穿墙点两端都被封；而 `$1N4752`/`$1N4863`/`$1N9171` 三网争 `87.22` 与 `89.76` 两条缝，`$1N4849` 又需同层横穿 ⇒ **1:1 互锁**。
- **两个候选方案被只读测量双双证伪**（`_n2fix.py`）：`$1N4891` F.Cu 北绕 `y=79.60` 最差 **−0.183**（`R21.1`，另撞 `U4.2` −0.044、`RN3.2` −0.173、`_SDIO_CLK` −0.150）——“排针上方是空的”假设不成立；`$1N9171` 东侧走廊 `x=169.30` 除最后一段撞 `$1N4863` 的 `y=90.0`（−0.150）外全绿（≥+0.295）。
- **五条可复用走廊事实**：列间通道 `x 166.33..167.07` 在全部 y 上自由；东侧走廊 `x≥169.095` 自由；F.Cu `x 159..164, y 83..93` 被 `_SPI_CS`/`_SPI_MISO`/`_SPI_MOSI`/`$1N1206` 四竖线封死 ⇒ **换层救不了东西向交叉**；45° 斜腿穿 pad 列必侵占 pad 中心净空（实测最近 0.73 < 1.125）⇒ 换列只能在 `x≤164.3` 或 `x≥169.0`。
- **两处更窄的死结**：`$1N4905`（P1 pad 10）被 `$1N4919` 圆角墙卡住，缺口 **0.238** 对需 **0.30**；`$1N4401` 的 `115.16` 缝已被 PD6 `y=115.05` + 45° 肩占满（+0.055）。
- **下一个入口**：一次协同重排 `$1N4752/$1N4863/$1N9171`（＋`$1N4849`）并把一个逃生 via 迁出排针块；不用自写搜索/布线器（既违反“不许自动布线”也违反“自写通用布线器同样违反本意”），须先补“多网同时 survey”只读工具。

## [0.0.149] - 2026-11-12

`docs/139` 第 8 节四条欠账里唯一能真修的第三条：**串扰一次只解一段，而段与段之间本来毫无关系**。

- **`sim/couple.py::max_runs` 之后加 `jobs`**：多段串扰求解现在可以并行。每个 run 是一整套独立的 2-D 场解（只吃 float，没有 board 对象、没有全局），天然可发给独立进程；`Pool.map` 保序，所以并行答案与串行逐位相同。
- **默认是串行，不是 auto**（改过一次）。库在背后起进程，等于把调用方没写 `if __name__ == "__main__":` 的脚本在每个 worker 里重跑一遍；`jobs` 要显式给（数字或 `"auto"` = min(runs, cores, 8)），或经 `PCB_AGENT_JOBS` 在机器上打开。
- **实测（`_par_probe.py`，3 段平行、`mesh_div=4`）**：串行 wall **324.1 s**（三段分别 75.33 / 102.50 / 146.24 s，合计 324.07 s）；`jobs=3` wall **144.0 s**，即 **2.25×**，上限就是最长那一段 —— 并行不是"除以 3"，是"取最大值"，这一点写进了 `value.parallel`。
- **实解上也逐字符相同**（`_par_probe2.py`，2 段、`mesh_div=4`）：串行 wall 318.2 s 对 `jobs=2` 168.9 s（1.88×），两次 `runs_solved` 的 `next`/`fext` 整段 JSON 逐字符一致（670 / 808 字符，含完整 `waveform_ns_v`）。
- **回退不吞**：池起不来（Windows spawn 没有 main guard 就是这条）时退回串行，并在 `value.parallel.why` 里带异常类型与消息，notes 里也照实说"one after another, because …"，不让"要了加速却没拿到"变成静默。
- **`jobs=0` 报 `ARG_RANGE`、非数字报 `ARG_INVALID`**；`jobs` 被 `min(jobs, 8, 越段数)` 夹住，理由同 `MAX_JOBS`。
- **`tools.py` 补齐广告**：`board_crosstalk` 的 schema 里上一轮漏了 `max_runs`（实现有、schema 没有 = 调用方看不见），这轮把 `max_runs` 与 `jobs` 一起写进 `properties`，描述里删掉"只解最严重的一段"与"这一次求解花了 285.8 s"这两句已经过期的话。
- **一处连带**：run 的参数改成以 dict 关键字进 `coupled_lines(**kw)`（worker 只认这一种形态），旧测试桩写的是位置参数，全量回归照出 3 例红，桩跟着改成 `(kind, width_mm, spacing_mm)`；被测代码没错。
- 新增 `tests/test_board_coupling.py::TestHowManyRunsAtOnce` 六例：默认串行、`auto` 的三重夹取、env 与显式值的优先级、`jobs=0`/非数字的具名拒绝、池起不来时回退且说明、以及在**真的脚本文件里**（spawn 需要真 `__main__`）跑一遍"并行答案 == 串行答案"。

## [0.0.148] - 2026-11-12

`0.0.147` 之后收的八件事，落点全在「规则到底量了什么」和「仿真到底算了什么」（`docs/139`）：

- **RF 六条规则从「文件里没写」变成真测量**（`ruleset/measure.py::_rf_facts`）。哪些网是 RF，从**网名按整词**读（`RF/ANT/FEED/LNA/GPS/WIFI` 与 `2G4/5G8/433M` 这类频段拼写；`TX/RX` 故意不算——开发板上那是 UART；`BT` 不能子串命中 `BAT+`；KiCad 的 `unconnected-(U5-ANT-Pad24)` 是标签不是网）。读数里写明取了哪几个网名，取不到才回 `undetermined`，调用方仍可经 `facts` 指定。
- **直角判据数错了量**（真缺陷）：原实现读的是两条边之间的夹角，于是一条被切成两段的**直线**（180° 共线）会被算成直角。改成读**方向转过的角度**（共线 0°、直角 90°、45° 斜切 45°），阈值 `>= 89°`，命中项带坐标与转角。
- **`_rec` 把 bool 变成 float**（真缺陷）：`float(True)` 让 `calc/rules.py` 的 `bool_true/bool_false` 一律落到「must be true/false」，本模块量出来的每个布尔事实判分时都降级成 undetermined。现在 bool 原样保留。
- **两处口径**：`take_lb` 只搬了限值、丢掉实测值（`measured` 为空）；`3W` 改吃 `rf_line_width_mm`（板上最细的 RF 走线）而不是调用方心里那条线的 `trace_width_mm`，该事实进 `known_facts`/`facts_a_rule_may_need`；`rf_line_z0_error_pct` 永远 `_na`——目标阻抗是设计决定，只有 `feedline_impedance` 有权回答。
- **串扰默认只解最严重的一段**（`sim/couple.py::max_runs`）：一次整板求解实测 **285.8 s**，默认 `max_runs=1`、上限 8，被拒的段逐一具名进 `runs_refused`，`max_runs=0` 报 `ARG_RANGE`（原来 `or 1` 把 0 吃掉了）。
- **峰值 J 单独做网格收敛**（`sim/field.py`）：R 0.013992→0.013986（0.043 %）确认收敛，而 **J 29.7096→30.8407（×1.0381）不确认**；阈值用既有 `PEAK_GROWTH_WARN=1.02`、270° 内角参考 `2**(1/3)=1.2599`，不确认时 notes 明确「读这一区的电阻与 ring 值，不要引这个峰值」。
- **`board_current_field` 把铺铜当铜**：铺铜检测提到 `board_ir_drop` 之前（只靠铺铜连的网否则是 `singular`），`model_pours` 默认 true、false 才退成纯走线；铜厚走 `_cu_um_by_layer` 兜底；网格预算冲突时**粗化并说出代价**（`pour_notes`）。实测 `paths=0 / pour_area_mm2=144 / R=0.0035974` 的板子给出读数，同一次里峰值仍不收敛（×1.1861）。
- **腔模带三项阻尼**（`sim/planes.py`）：每个模给 `1/Q = 1/Qc + 1/Qd + 1/Qrad` 与逐项值，`tan_delta` 按**电容权重 t/er** 跨层混合（实测 0.0201477，既非厚度加权 0.0202857 也非算术平均 0.014）；缺 `loss_tangent` 的层**计为无损耗并在 `missing_tand_in` 具名**，不猜 FR4 默认值。stickhub 真板 (0,1) 1.7665 GHz：Qc 960.4 / Qd 50 / Qrad 13.47 ⇒ **Q 10.50**，这块小板上辐射是主项。不写判词——高 Q 不等于坏。
- **自检里那三条红是守卫写错了**（`server.py`）：`--selftest` 长期 363/366，三条都断言 `find_ngspice(allow_bundled=True) == bundle`，而代码与 docstring 写的是「`PCB_AGENT_NGSPICE` 与 PATH 排在 bundle 之前」（`0.0.112` 改过文、行为没动）——本机 `ngspice-47` 在 PATH 上，断言恒红。改成断言真正承诺的两半：默认回答永远不含 bundle，`allow_bundled` 只在没安装时补位；另两条把 A 卡读数改用优先级最高的入口（`PCB_AGENT_NGSPICE`）显式路由到 bundle 再测，测完还原环境变量。修后 **366/366 passed in 92.8 s**，单元侧补一例覆盖「补位而不覆盖」。

## [0.0.147] - 2026-11-12

`0.0.146` 之后跑了一次全量回归：**4108 passed / 48 failed**，修完再跑就是
**4165 passed, 9 skipped, 53 subtests passed in 644.20s**（`-n 6`，8 核）。48 个红逐类查下来
只有一个是真缺陷，其余五类是「陈旧期望」，但都不是可以放着不管的那种红（`docs/138`）：

- **`engine.drc(save_to=...)` 会改它读的板子**（真缺陷）。`kicad-cli pcb drc --refill-zones
  --save-board` 会**写**它拿到的那个 `.kicad_pcb`；当板子旁边没有 `.kicad_pro` 时落点会落回
  调用方自己的文件，KiCad 10 重存时把 `(net_class "Default" ...)` 当 legacy 丢掉，板子的
  `clearance 0.2` 就没了——表现是「一次 apply 之后 `board_route` 报 `RULES_ABSENT`」。修：
  refill 且目标就是原件时先复制到 scratch 再交给 kicad-cli。实测修前 `engine=False → net_class=1`、
  `engine=True → 0`，修后两次都是 1；新增 `tests/test_drc_reads_only.py`（4 例）钉住
  「DRC 不得改动它读的板子」。
- **棘轮漂移**：十二处 `assertEqual(len(tools.TOOLS), 229)` 早在 surface 走到 250/252 之后就永远红，
  红数字等于没有守卫。收口到 `tests/_toolcount.py`（252 / 222），另三处 `available == 204` 同样接上。
- **19 个用例在断言已退役的 `board_route` 返回 ok**：单测改走库函数 `tools._board_route()`
  （planner 没坏，坏的是它被当工具广告），退役本身用一个类钉住（`TOOL_RETIRED` ≠ `TOOL_NOT_FOUND`、
  四个 profile 都不广告、拒绝不写盘）。
- **夹具开框**：`test_sync` 的板子只有一条 `gr_line`，新闸门 `precondition.outline_known` 如实拒绝；
  补成闭合矩形。
- **新闸门 vs 旧期望**：`test_ops_edit` 拆成「过孔跟随焊盘网络」与「指定他网被当短路拒绝且文件不变」；
  改板形的类显式传 `allow_edge_changes=True`；`test_crosscheck` 故意造 0.15 mm 间距 ⇒ 该类传
  `preconditions=False`（只在违规就是被测对象时开闸）。
- **本机 ngspice-47 没有 spinit**：`engine="ngspice"` 先走 PATH，落到没有 XSPICE 的本机引擎，
  `gain1` 找不到定义。golden 钉到 `vendor/ngspice`，并加反向对照记录「本机引擎确实跑不了这张卡」。

工具数仍 **252**、`available` **222**，selftest 363/366。

## [0.0.146] - 2026-11-12

拿 KiCad 自带的 12 块 demo 板把两个「不需要调用方给数」的入口全扫了一遍（`docs/137`），
扫出两个真缺陷，都在 `board_plane_resonance`：

- **按覆盖率排序会把 0.2 % 的铜当板**。`complex_hierarchy` 只有 F.Cu/B.Cu 两层铜，
  F.Cu 上只有一块小铺铜，旧逻辑照样把它配成腔的另一面——算出来是一个不存在的谐振腔。
  现在**第二块板盖不住包围盒 50 % 就拒答**（`NOT_APPLICABLE` + `pours_found` 逐层点名）；
  点名 `layer_a`/`layer_b` 仍然放行，`pair_from_file` 报 false——那是调用方的假设。
- **「文件没有叠层」被说成「铺铜不够」**。`microwave`/`multichannel` 整个文件没有
  `(stackup)`，`copper_layers` 是空的，旧文案却说「没有两块铺了铜的层」。现在分开：
  没有叠层就直说铜层与 er 都没说，并给「点名 + 给 er」的出路。

基线本身（`docs/137`，只读、不改板）：腔 12 块 **6 答 6 拒**（拒的理由三种：第二块板
0.2 % / 只有一层铺铜 / 没叠层 / 没闭合外形），最低模 video **0.226452 GHz**（312 mm 的
板）、openair-max 0.710525、cm5_minima 1.09213（er 串联平均 4.571）、
royalblue54L_feather 1.20955、stickhub 1.76654；网表 12 块里 10 块抽出 19–97 个网
（566/455/365 节点等），`multichannel` 有 576 段走线但没叠层 ⇒ 79 个网全 `ARG_REQUIRED`，
`microwave` 真的没走线 ⇒ `nets=0` 且 `skipped=0`——**两种 0 不是一回事**。

测试：`tests/test_board_planes.py` 12 → **14**（0.2 % 的板被拒 + 点名放行、
没有叠层的文案），工具数仍 252，selftest 363/366。

## [0.0.145] - 2026-11-12

0.0.142 到 0.0.144 把「电路」「热」「J」接到板文件上，这一版接最后两族：**板子在哪
里谐振**（平面腔）和**两块并排跑的铜说多少话**（串扰）。两个原语 `plane_resonance` /
`crosstalk_coupled_lines` 一直是算得对的；缺的是它们的输入——两块板、介质厚度、er、
耦合段长——**全都在文件里**，而它们要人手打。

新增（`sim/planes.py` + `sim/couple.py`，工具数 250 → 252）：

- **`board_plane_resonance`：腔就是外形框**。两块板 = 两块铺铜层，介质厚度与 er 取
  叠层之间那段，范围取外形包围盒。三件事说出口：① 公式只认矩形，所以腔是**包围盒**，
  盒面积与外形面积并排报（角上挖掉 30% 的板按盒子响，不按铜响）；② 叠层没写 er 就
  **拒答**（填 4.3 等于静默定下每一个模）；③ 模的位置不是问题——谐振需要激励与耦合，
  两样文件里都没有，`cavity_via_fence` / `cavity_plane_q` 各自另问。多层介质用
  **电容等效串联平均** `sum t / sum(t/er)`，不是算术平均。实测：stickhub（B.Cu/F.Cu，
  1.51 mm，er 4.5，16.5×40 mm）最低模 (0,1) = 1.76654 GHz，2.4 GHz 下 f/f_mode =
  1.35858；royalblue54L_feather（In6.Cu/In5.Cu，0.3 mm）最低模 (1,0) = 1.20955 GHz；
  ecc83 只有一层铺铜，**拒答**并把找到的铺铜逐层点名。
- **`board_crosstalk`：先在板上找耦合段，再解一次**。平行（按线段方向、默认 10°）、
  边到边 gap ≤ `max_gap_mm`、纵向重叠 ≥ `min_overlap_mm`，每个候选连 gap/overlap
  一起列出来——一个网几十条线，「取最近的两条」就是拿从没并排跑过的线出数。**上升
  时间必须由调用方给**：它是 FEXT 的分母，文件里没有（`ARG_MISSING`，消息直说
  FEXT ∝ 1/rise）。参考平面取叠层里**最近**的那侧（一条线有两个邻居），介质厚度与 er
  同来。拒答：同网、无共层、两线宽不同（一个耦合数只属于一个线宽）。候选里只解最
  狠的一个——**一次完整场解 30 s 到 5 min（0.15 mm 对实测 285.8 s）**，其余列出不解。

两个入口都只读文件、只写测量；铺铜仍然只由 KiCad 的 `ZONE_FILLER` 生成。

## [0.0.144] - 2026-11-12

0.0.143 把「热」接到了板文件上。这一版接「电流密度」：**J 在哪**，用板子自己的铜算。
`current_density_field` 一直是正确的原语——J = I/(w·t) 在直段上对，在板子真正失效的地方
（拐角、线宽台阶、焊盘处的漏斗）错——缺的是**区域和接触点从文件里来**。

新增（`sim/field.py`、`board_current_field`，工具数 249 → 250）：

- **区域就是文件里的铜**：一个网、一层，走线段取 `board_ir_drop(return_network=True)`
  的同一张图，焊盘按文件里的 `w/h` 作为矩形并进区域（否则焊盘下面是断的，而"焊盘处的
  漏斗"正是要问的东西），厚度取叠层。接触点是调用方指定的两个焊盘——**哪两个是设计
  问题，不是文件属性**——半径取 `0.4·min(w,h)`，所以量的是焊盘那么大的接触。
- **两层分开算**：一个网打过孔就是两个场，合成一个 2-D 解就是一块不存在的板。别的层、
  过孔、铺铜都写进 `notes` 说"不在这个区域里"。
- **和 1-D 的答案并排给**：同一对焊盘上 `board_ir_drop` 的电阻与场解出的电阻一起报，
  附相对差。它们是两个测量：1-D 是每条边一个电阻、电流沿宽度均布，看不见焊盘自己的铜
  和漏斗；场两个都有。ecc83（C1.1→P3.1，0.1 A）实测 0.007034 对 0.008472（−17%），
  峰值 J 5.51 对 3.57 A/mm²。

the number that looks right and is not（两条，都来自同一个 30 mm 直段）：

- **默认网格会给出低 25% 的电阻**。按包围盒与 unknown 预算取步长时，1 mm 走线只有 7 格
  宽；实测 0.010467 对解析 0.013989（−25.2%）。**更糟的是中间某个步长（0.12 mm）直接把区域判成
  DISCONNECTED**——粗掩膜丢掉细走线，solver 没法把它当"精度问题"报出来。改成默认步长
  = 最窄铜宽/6，并且**必须在细一倍的网格上复核**才发布：实测 0.014016 / 0.013989
  （1 mm）、0.027915 / 0.027892（0.5 mm），解析 0.013989 / 0.027978。ecc83 上两档差
  0.2%，`is_grid_convergent: true`；不一致就写"这个数只能当形状，不能当电阻"。
- **电阻不是 ρL/(w·t) 的 L**。接触是焊盘那么大的圆盘，量的是 30 − 2×0.8 = 28.4 mm。
  按 30 mm 算会多出 5.7%，而那 5.7% 是"接触不是焊盘"。

`MESH_TOO_FINE` 是第三条拒答：最窄铜宽定出来的步长要的格数超过预算时，报出窄宽、步长、
面积与格数，让人加预算或自己给更粗的步长，而不是交一张断开的图。

## [0.0.143] - 2026-11-11

「整板仿真」到上一版还只走到电路：板文件能变成可解的 deck，能算 IR-drop，但
**热**那一头仍然要调用方手打几何。这一版把它接上：`board_thermal` 吃同一份板文件、
调用方给的每焊盘电流，先解 DC 得到每条走线的 I²R，再把那些热放进一块尺寸等于板子
外形框的 2-D 平板，铜用文件里的叠层与铺铜。判决口径还是同一条——**同热必须能追到
可算的物理量**。

新增（`sim/power.py`、`board_thermal`，工具数 248 → 249）：

- **功耗不重写。** `dissipation` 走 `board_ir_drop(return_network=True)` 的同一张图，
  自证 = 同网同源下 `total_power_w == total_copper_loss_w`（合成板 places=9 通过；
  ecc83 0.1 A 实测 8.471667803002245e-05 对 8.4717e-05，差 3.2e-10 是发布位数）。
  按功率排序输出每段走线，热点在前。
- **电流是调用方的事，而且必须自洽。** `.kicad_pcb` 里没有任何电流，所以 `currents`
  必填；每网电流和不为零就 `ARG_INVALID`（"add up to ... A, not zero"）——半个电路
  解出来的热图是编的。焊盘存在但不在任何网 → `ARG_INVALID`；焊盘在任何网上但一块铜
  都不通 → `NOT_CONNECTED` 点名。
- **孤岛不是缺陷，驱动电流落在两块铜上才是。** 实测 ecc83 的 `Net-(P3-P1)` 分裂，
  稠密求解直接 `singular at 11`。现在按连通块剪枝：不承载电流的块被丢下并计数
  （`islands`，同时写进 `notes`），而如果被驱动的焊盘分居两块铜、电流根本流不动，
  就 `NOT_CONNECTED` "sit on ... separate pieces of copper"。
- **平板就是这块板**：尺寸 = 外形框，厚度取叠层，铜取叠层 + 文件里的铺铜（铺铜按
  外形框矩形给，非矩形会退化成更多面积并在 `notes` 里说清）。过孔的功耗**报告但不
  作为 2-D 源**（桶是竖直的，平面场没地方放它），功耗数字在
  `value.dissipation.via_power` 里。

the number that looks right and is not（两个都写成测试防回退）：

- **0.010000 mV 级的 1.9 % 偏差**：`board_thermal` 在 25 °C 上解，`STRIP_R` 是 20 °C
  的阻值。实测 0.020090018285714283 对 0.019702857142857143 = **1.01966**，正好
  `1 + 0.00393×5`。若报 20 °C 的数，就等于"热在 25 °C 算、在 20 °C 放"。
- **"温差四倍"不是 4.000**：2 A 对 1 A 实测 **4.2244**。干净的 4.000 反而说明
  铜的温漂被丢掉了。

一处不是板子的话：平板自己的能量平衡门槛（0.5 % 输入功率）在千瓦级以下的板子上
量的是**最后一轮 SOR**，不是模型——ecc83 报 5.149 %、带铺铜的合成板报 0.534 %。
门槛不放松（放松＝编一个温度），改成按物理尺度收紧容差：`P/(2hA)` 再降四个数量级，
失败继续收紧并重试，**用了多少容差写进 `notes`**（ecc83 收到 1.79e-08 K）。

ecc83 实测（0.1 A 走 `Net-(P3-P1)`，h = 10 W/(m²K)）：P = 8.638e-05 W，
`t_max` = 25.004072 °C（`t_min` 25.000962），平板 52.07×46.355×1.6 mm，铺铜矩形 1 块。

## [0.0.142] - 2026-11-11

「仿真」以前只吃一份手写的网表。这一版让 `.kicad_pcb` 自己变成网表：叠层、参考面、
走线电阻、走线电感与对参考面的电容、过孔、孤岛，全部从板文件里量出来，交给同一个
求解器。判决口径只有一条——**同一块板，两条独立路径必须给出同一个数**。

新增（`sim/board.py`、`sim/run.py`、`board_netlist`、`board_sim`）：

- **R 不重写。** `oracle.board_ir_drop` 新增 `return_network=True`，只出图不求解，
  deck 与 IR-drop 报告共用同一个图构建器。自证=10 块成品板逐位对照，差
  **0 ~ 2.8e-14 mV**（答案从 10.2 mV 到 336.8 mV）。
- **L 与 C 同一条传输线**：`L'C'·1e6 == εr/c₀²` 是判据本身，不是注释。有 L 才写
  `R a mid` + `L mid b`；**没有 L 就直接 `R a b`**（`include_lc=false` 曾因此在远端悬空）。
- **一个网的焊盘不总是一块铜。** 先按 R 边算连通块，`islands` / `copperless_pads`
  逐块点名；`keep_pads` 剪掉不含被接线焊盘的块，并在 `notes` 里说清剪了几块、为什么。

the three wrong answers that look right（三个「看起来合理的错答案」，都写成测试防回退）：

- **0.000000 mV**：R 与 L 并排画时，IR-drop 报 0——它量的是被 `mid` 节点分掉的那一半。
- **+10 V**：内置 `engine="builtin"` 的 I 源与 ngspice 符号相反，测试曾经防守**错**的那一侧。
  `calc/spice.py` 改正（DC 与 AC 两处）。
- **1000.000000 mV**：给一个**没有铜**的焊盘接地，ngspice 用 gmin 解出浮空网络，`status: ok`。
  修法是前置拒答：`copperless_pads` 直接 `NOT_CONNECTED`，再对 `R/L/V` 做并查集，
  每个节点必须与 `"0"` 同根（C 在直流开路、I 源不定电位，都不算连通）。

同一类还修了一处不是板子的话：稠密路径上的 `singular at column 181`。现在它先做
"从源可达"剪枝（与铺铜路径同构），孤岛 sink 判 `NOT_CONNECTED` 并点名 pad，
`board_ir_drop` 不再把线性代数的列号当成结论。

12 块官方成品板实测（`docs/133` 有全表）：video 12836 节点、cm5_minima 4249 边是最大的
两块；**microwave 板里 0 根走线**（纯铜皮图形板）、**multichannel 无叠层**（要先给
`copper_um`，否则 L/C 无从算起）、**royalblue54L 54 个网 `ARG_RANGE`**（文件里过孔
`drill 0.00001 mm`，25 µm 镀铜把它堵死）——三类跳过原因都点名，不返回空图。

`not_modelled` 六条写在返回里（焊盘/过孔的接触电阻、铜箔粗糙度、介质损耗、
温升、铺铜只在 `model_zones=true` 时算导体、器件模型仍要调用方给），
`board_electrothermal` 仍吃几何不吃板文件、`board_sim` 还不输出每元件功耗——这两件
登记未做，没有假装做了。

`board_netlist` / `board_sim` 不进 `CORE_TOOLS`：46 个工具已经把 25 KiB 的 `tools/list`
预算顶到 23 976 B。

测试：`tests/test_board_sim.py` 新增 22 条（串联 R/L、传输线恒等式、跨网节点、
孤岛剪枝、浮空拒答、deck 与 IR-drop 逐位一致），`tests/test_board_ir_drop.py` 的
孤岛期望从 `NOT_APPLICABLE` 改成 `NOT_CONNECTED`。

## [0.0.141] - 2026-11-11

八条规则一直回答「no measurement supplied for 'X'」——那不是关于板子的话。这一版
把测量补上，每条都先在 12 块官方成品板上跑，**三次口径被成品板推翻**（下面标了
"推翻"），推不翻的那一版才是留在这里的。

新增测量（`ruleset/measure.py`）：

- **`silkscreen_over_pad`**：每条丝印按自身宽度当胶囊，量它到焊盘铜皮的距离。
  推翻：`fp_rect` 只给两个对角点，被当折线画出**对角线**——pic_programmer 的 4 个
  插座因此判成压焊盘；adapter 把矩形展开成闭合 5 点环之后归零。剩 4 块板仍为真，
  逐条手验：cm5_minima 竖线穿 SW501.4、openair-max 横线穿 PWR_SW1.5、
  tiny_tapeout 三条。
- **`power_pad_has_thermal_vias`**：只判**库名自己声明**有裸露焊盘的器件
  （`_EP` / `ThermalVias` / `exposed`）。推翻两次：面积 ≥1 mm² 抓到 royalblue 23 个
  （1206 电容、USB-C 外壳 MP 焊盘）；"footprint 最大焊盘"抓到 J4 外壳。热通路三选一
  计数（同号组内镀铜孔 / 同网落在焊盘下的过孔 / 其它器件同网落在焊盘下的镀铜孔）。
  焊盘中心落在自身铺铜内的（cm5_minima U702）**不判级**。
- **`stitching_via_pitch_mm`**：铺铜网上过孔的最坏近邻间距。近邻搜索按环扩张——
  固定 ±1 格窗口曾把相距 5 mm 的两颗过孔判成"没有邻居"。
- **`diffpair_gap_varies`**：判定改占比口径（偏离主间隙 >0.05 mm 的耦合长度占比
  >20%），采样前先剔除扇出（端点落在自身焊盘/过孔上）、不平行（>15°）、垂足落在
  对侧线段之外的样本。推翻：极值口径在 royalblue 读 0.23..2.14 mm，全是扇出；
  12 块干净板最高 11%、真正改间隙的落在 21–27%。
- **`ics_without_bulk_cap`**、**`min_mask_expansion`**：字面测量，见 docstring。
- **`test_points_on_rails_and_connectors`**：rail 必须是**铺铜且名字像电源**的网
  （royalblue 铺了 70 个网，含全部连接器信号）。**不判级**——pogo 治具要焊盘、手测
  要排针，板文件不说用哪种。
- **`crystal_has_guard_ring`**：晶振 courtyard 外扩 0.75 mm 取 8 点，落在最大铺铜网
  内 ≥6 判"被包"。**不判级**：interf_u 与 video 的晶振周围一点铜都没有，它们是成品。

改写：

- **`trace_under_crystal` 返回 bool**（原来返回 count，grader 判
  `must be true/false, got 2.0`）；只判晶振**同侧**层（royalblue 的 In4 走线不再算）；
  判定区域从 courtyard 换成 **courtyard 裁到 pad hull**——HC18-U 库画了 13.1×17.3 mm
  的 courtyard 围着 4.9 mm 的引脚间距，interf_u / video 会把 2.5 mm 外的走线判成
  "在晶振下面"；再除掉 bounding box 的角（royalblue 的 45° 走线在 0.65 mm 外）。
- **`fact_refusals`**：`undetermined` 的 `reason` 改成测量自己的原句，不再是一句
  通用的 `no measurement supplied`。

新增 `tests/test_layout_facts.py`（30 条）：八条测量每一条都有**反向失败对照**——
同一个板只改被测的那一件事，结论必须翻面。

**版本口径**：从 `0.0.1` 起，第三位数字随每次提交递增（`0.0.2`、`0.0.3`…），
只有真正对外、有不兼容变化的里程碑才动第二位。不为了"看起来成熟"把版本号往上跳。

信封契约（`status` / `basis` / `precision` / `reason_code` / `authoritative`）从这个版本起冻结：
加字段可以，改语义要动第二位。

早期用 `0.1.0`…`0.7.0` 递增标注过内部阶段，已按要求收敛；那些标签只留在 git 历史里
（见文末对照表），不再作为版本号使用。

## [0.0.140] - 2026-11-11

拿 12 块 KiCad 官方 demo 板当"成品板语料"回测整套规则，改的是**测量**，不是判据。
四条 P0 假失败、一条把成品板判成违规的间距规则、一条把 +5V 平面当回流参考的读数，
全部由语料暴露；没有被语料推翻的阈值一个都没动。

新增/重写（`ruleset/measure.py`）：

- **`courtyard_overlaps` 读闭合轮廓**：`contours` 由 `fp_rect`/`fp_poly`/`fp_circle`
  与首尾相接的 `fp_line` 链给出，间距用精确多边形距离；只有**画不出闭合轮廓**的
  器件才退回 bbox，且只作候选（`undetermined`）。pic_programmer 上 bbox 曾报
  P3 与 C7 有 0.295×6.6 mm 重叠，实际轮廓相距 2.05 mm。
- **`_interiors_meet`：相接 ≠ 重叠**。`dist_polygon_polygon` 对两者都返回 0，但
  royalblue54L_feather 的 15 对、stickhub 的 8 对是"库把 courtyard 画成外形，
  两个器件正好贴着摆"，0.0 mm clearance 在 KiCad 里合法。三个判据：横截相交、
  顶点严格在内部、**边中点严格在内部**（后者专治"两个矩形共用两条边的延长线"
  ——那时所有顶点都落在对方边界上、所有交点都在端点，前两条都看不见）。
- **轮廓是笔的中心线，不是墨**：`fp_rect`/`fp_circle` 不再按 stroke 半宽外扩。
  库统一用 0.05 mm 画 courtyard，外扩半宽会让每一对 flush 摆放凭空重叠 0.05 mm。
- **方向判据带容差**：浮点坐标下"共线"会算成 1.8e-13 而不是 0，严格 `<0` 就把它
  读成横截。容差按边长平方缩放（叉积的量纲是 mm²）；实测 royalblue 13 对 → 11 对。
- **`_reference_plane(board)` 重写**：回流网取**铺铜面积最大的网**，不再取"面积第二大
  的网"或按名字猜；断流门槛 1.0 mm，并按 `under 1.0 / 1.0-2.0 / 2.0-5.0 / over 5.0`
  分档；11/12 块可测成品板都有 >1 mm 的跨缝 ⇒ 这一条**降级为 `CAP_FACTS`**
  （`measured`, not graded），evidence 与读数保留。
- P0 假失败四类：`_hole_list` 跳过退化钻孔（`DEGENERATE_DRILL_MM = 0.02`，
  royalblue 有 `(drill 0.00001)`）；`_hole_web` 跳过同 owner 同位置的重复孔记录；
  `cu_items` 排除 `unconnected-(...)` 自动网（KiCad 的"此焊盘不连任何东西"标记，
  不是网）；`_castellated` 识别**半孔**（孔心到边 ≤ 钻孔半径 + 0.01），半孔铜到边
  与孔间距是工艺属性，不参与板边/孔间距判据。

读数（12 块语料，重跑后）：P0 只剩 2 条**真差异**（cm5_minima 0.1914 对 0.2、
complex_hierarchy 0.175 对 0.18，都是声明工艺与实际不符），royalblue 由 27 对
假碰撞降到 11 对真重叠，video 的 297 条参考平面虚警归零。

## [0.0.139] - 2026-11-11

P1 里剩下两条从"没有测量"变成"量出来了，但一条只量不判、一条把限值留给调用方"。

新增（`ruleset/measure.py`）：

- **`_pour_nets(board)`**：这块板**用铺铜承载**的网——非 keepout 且带 `filled_polygons`
  的 zone 的 net。不是网名猜测（测试里故意起名 `VRAIL`，证明代码里没有 `GND`）。
- **`_return_vias(board)` → `layer_changes_without_return_via`**：每个"所在网没有铺铜"的
  过孔，到最近"铺铜网过孔"的距离。换层处回流电流必须跳板对，跳不回去的环路既反射又辐射。
  1.0 mm 阈值只决定**计数哪些**，每一条距离都留在 `examples` 里（前 8 条，按距离降序）。
- **`_power_traces(board)` → `power_trace_width_mm`**：铺铜网里**最窄**的一段走线。
  电源余量受最窄处约束，不受平均宽度约束——一条 2 mm 的轨在 router 让开一个焊盘留下的
  0.2 mm 脖子上照样是保险丝。
- `CAP_FACTS` 增 `layer_changes_without_return_via`（cap 文案含 `where`）；
  `catalog_facts` 增这两项的 `take`，`audit` 侧不需要新代码。

读数（`examples/sky_star`）：

| 项 | 值 |
|---|---|
| 换层无回流过孔 | **146 / 147**（1.0 mm 门槛），最坏 `$1N4905` at (131.57, -88.49)，距最近回流过孔 **13.255 mm** |
| 铺铜网 | `{+3V3, +5V, GND}`；回流网上 54 个过孔，信号网上 147 个 |
| 电源线宽 | **0.15 mm**（GND @ (141.47, -116.00)；18 条走线 / 2 个有走线的铺铜网） |
| 只由铺铜承载、无走线可量 | `+3V3`，进 `note`，不进数字 |

判词路径（三种 `facts` 组合都实测过）：不传电流 ⇒ `undetermined`、`reason` 含 `current_a`；
`{"current_a":0.3,"current_standard":"ipc2221","copper_oz":1.0}` ⇒ **pass**（limit 0.0574）；
`current_a=2.0` ⇒ **fail**，`margin` 0.6359。

为什么不把 146/147 判 fail：规则问的是 RF 与快速信号，**板文件不说哪些网是"快"的**。
在一块已量产的 GPIO 开发板上把每一次换层都判成缺陷，是把未知洗成决定。同一套 cap 机制：
`facts={"layer_changes_without_return_via": 0}` 一给就判 pass。同理线宽 0.15 mm 只是
**下界**，限值必须由调用方的 `current_a` + `current_standard` 给出（IPC-2221 与 IPC-2152
差 3 倍以上，已有 `_limit_width_for_current` 会拒答）。

测试：`tests/test_return_vias.py` 新增 10 条（近距不计数 / 5 mm 要计数且距离对 /
`VRAIL` 证明不硬编码 GND / 无回流过孔要拒 / 无铺铜要拒 / 最窄段才是数字 / 同板 0.1 mm
的信号线不参与 / pour-only 进 `note` / 全铺铜承载要拒 / pass-fail 三条 `facts` 路径）。

`--selftest` 363/366（3 项恒为 ngspice-47 环境干扰）；相关子集 147 + 26 passed。

## [0.0.138] - 2026-11-11

`layout.reference_plane_continuous`（P1）从"没有测量"变成"有测量、有判词、判词带证据"。
这个数字在上一轮停在这里：同一块板，三种实现给出 0 / 24 / 25 三个交叉数。查清之后两者都
不是答案——**0 是筛子太松（只取最大一块铺铜），24/25 是栅格在铺铜边界造出来的**。

新增：

- **`_Scan`**：精确 even-odd 射线法，边按 1 mm 分带索引，所以一次采样只碰附近那条带里的边。
  用途是**复核**栅格报出来的候选，而不是主要手段（34 000 个顶点逐点算，全板要几分钟）。
- **两遍结构**：0.2 mm 栅格筛"不是全程覆盖"的走线 → 每条被筛出来的走线按 **0.02 mm**
  重读一遍 → 只有重读同意的才上报。栅格漏掉的只可能是比 0.2 mm 更窄、且被覆盖包住
  的缺口，这个上限写在 `method` 里。
- **`uncovered_mm`**：每条跨分割走线的**内在空档最宽值**（两端都踩在铜上的那段）。
- **`counts`**：按 `层 over 参考层` 分开计数。
- 判 fail 的检查项现在带 **`evidence`**（测量的 `where`）与 **`examples`**（前 8 条）。
  规则库侧只多一行接线，没有测量时这两个字段不出现。

修掉：

- `_raster` 的格心口径：扫描行放在格带**中心**（`y0+(j+0.5)*step`），填充按"格心落在
  span 内"（`ceil(...-0.5)` / `floor(...-0.5)`），`covered()` 用 `floor` 读回同一套网格。
  之前扫描行压在晶格上，sky_star 的铺铜又在 0.05 mm 晶格上，顶点整好落在扫描行上，
  奇偶被翻、在实心铜里造出 24 个假洞。

读数（`examples/sky_star`，`measure()` 全量 1.0 s）：

| 项 | 值 |
|---|---|
| 跨分割走线 | **119 / 862**（F.Cu over B.Cu **27**，B.Cu over F.Cu **92**） |
| 最宽内在空档 | **6.98 mm**（`$1I8749/$2N1200` on F.Cu over B.Cu） |
| B.Cu 那条 22.6 mm 直线的空档剖面 | 0.50 → 2.04 → **5.74** → 0.20 → 0.22 → 0.22 → 0.56 → 0.46 mm |
| 栅格误报、重读驳回 | 2 条（走线离铺铜边界 < 0.1 mm） |

两处独立印证：同一批走线用 `G.point_in_polygon` 逐环奇偶另算一遍，5.74 / 4.04 / 3.64 mm
逐位一致；被驳回的 2 条重读后是 0 个未覆盖采样。

测试：`tests/test_reference_plane.py` 新增 7 条（实心铜不报、4 mm 洞要报且宽度对、
0.4 mm 洞仍要报、贴边走线不许报、跑出铺铜只计数、层序未知要拒、无铺铜要拒）；
相关子集 162 passed（`pytest tests/test_step_apply.py` 收集即失败、`test_blockers` 一条
断言与本次无关，均为既有问题）。

## [0.0.137] - 2026-11-11

P1 里两条去耦规则（`layout.decoupling_distance` / `layout.decoupling_same_layer`）从
"没有测量"变成"有测量，但第一条不给判词"。

新增：

- **分层铜图**（`measure._copper_graph`）：按网络建图——走线（弧线折线化）、过孔（跨层边）、
  焊盘（锚点成为节点，落在焊盘多边形内的线端点/过孔自动相连）。坐标量化到 1e-4 mm，
  所以 `-95.10000000000198` 与 `-95.1` 是同一个节点。
- **`decoupling_max_distance_to_pin_mm`**：电容焊盘 → 同网络多脚器件（≥3 脚）焊盘的
  **最短铜路径**（Dijkstra），逐电容取最有利焊盘，全板取最大。
- **`decoupling_via_in_path`**：同一张图删掉过孔边走第二遍；焊盘对到不了 ⇒ 这条连接
  **不可能不换层**。
- **`measure.grade_caps` + `audit._catalog_checks` 的 cap 分支**：把"量得出但分母不是
  规则要的那批对象"的事实，输出成 `state: undetermined` + `measured` +
  `measured_state`，并且**去掉 margin**。调用方用 `facts={...}` 传值即覆盖，cap 自动让开。

真板读数（`examples/sky_star`）：最坏 50.75 mm（C9.2 → U1.49，该网络铜总长 54.09 mm，
所以是绕行的真实长度）；27 个电容里只有 9 个有走线路径，另外 18 个在 GND / +3V3 上，
由**铺铜**承载、文件里没有走线可量 —— 这 18 个计数点名，不并进数字；过孔被迫的 1 个（C9）。

为什么不判：板文件里没有"这个电容服务哪个引脚"，按位号自动筛出来的是**所有**走线连到多脚
器件的电容，混着开关消抖与滤波器电容。把这种分母判成 pass/fail 就是把未知洗成决定。

测试：`tests/test_decoupling.py`（8 条，含铺铜网络必须报无路径、两脚器件不能当目标、
过孔在网络上但不在路径里仍 pass、SMD 焊盘从背面到不了必须报被迫换层）；相关子集 139 passed；
`run_server.py --selftest` 363/366（3 个 ngspice 环境干扰，与本次改动无关）。

## [0.0.136] - 2026-11-11

修掉 0.0.135 登记的那个假绿：**engine 门在"扩铜撞铺铜"时不报新增，因为 refill 会让它
在结构上不可能报**——重填后的铺铜永远靠"把自己挖掉"来满足 clearance，所以只要板上有铺铜，
`new_blocking_total == 0` 就是构造性成立的。门缺的不是更严的 DRC，而是 DRC 结构上看不见的
那个量：铺铜为此让出了多少面积。

新增：

- **`engine.drc(..., save_to=)`**：把重填后的板子留下来（`--refill-zones --save-board` 写进
  scratch 副本再拷出），输入文件仍然只读。不带这个参数时行为逐位不变。
- **`gates.drc.pour`**：对两次重填副本逐 zone 量填充面积，给 `before_mm2` / `after_mm2` /
  `delta_mm2` / `pour_gave_up_mm2` / `pour_gained_mm2`，并在 `decision` 句里写明
  "铺铜被重塑了：整体 ±X mm²（GND@F.Cu +5.32, +3V3@F.Cu -3.18）"。
- **`gates.drc.unconnected`**：给 `before` / `after` / `delta`，并写明它是**计数不是判词**
  ——同一文件两次重填对"哪几对断"并不一致。

三条口径（都是实测，不是推断）：

1. **同一文件连续重填 3 次，四个 zone 的面积逐位相同（delta 全 0）**，所以这个量可以当判据；
   而 `unconnected` 的**条目集合**在两次重填之间会变（2 对 zone-zone vs 13 对，总数都 49），
   所以它只报计数。
2. **孔环绕向是假设，不是事实**：shoelace 求和取绝对值（sky_star 四个 zone 全为负，说明
   KiCad 统一顺时针写铜），并逐 zone 校验"铺铜不可能大于自己的 outline"——不通过就
   `comparable: false` 并**不给数**，宁可没数不给错数。
3. **面积是 `filled_polygon` 的环面积之和**，孔必须反向绕（否则会加到铜里）。这条就是
   上面那个校验在抓的东西。

真板上验过（`examples/sky_star`，把一条 1.6 mm GND 走线加宽到 0.60，它横穿 +3V3 铺铜）：
DRC 123 → 123（0 新增，正如上面说的是构造性的），而 `pour` 报 GND@F.Cu **+5.32 mm²**、
+3V3@F.Cu **−3.18 mm²**、整体 **+2.14 mm²**——铺铜确实动了，而且方向可解释。这条读数
在 0.0.135 之前是完全没有的。

测试：`tests/test_pour_delta.py`（6 条），最难的两条是"同一块板两次必须正好 0"（一个对
自己都会漂的量不能当判据）和"铺铜比自己的 outline 还大必须拒答"。

## [0.0.135] - 2026-11-11

天空星重建板按 0.0.134 的语料区间**逐段、逐孔**量了一遍，补齐 MCP 缺的三件能力，
并在真板上把能放下的都放下：**线宽 57/862**、**过孔 113/201**、**泪滴 221/231**，
每一步都官方 `--refill-zones --save-board` 后再 DRC 对账，**DRC 逐类仍是 123 条基线**（clearance 0）。

新增（全是"只读测量 + 给出可执行计划"，都不写盘）：

- **`board_width_ladder`**：逐段给出 `max_width_mm`（到异网走线/弧线/焊盘/过孔的真实距离），
  按目标线宽分组装成 `set_track_width` 的 `uuids`。天空星实测 805/862 一条都让不出一格，
  只有 57 条能宽（1 × 0.204、3 × 0.25、37 × 0.30、3 × 0.50、13 × 0.60）。
- **`board_via_ladder`**：逐孔给出 `max_size_mm` / `max_drill_mm` / `bound_by`，
  分组装成 `set_via_size`。201 孔全 0.45/0.25，`clearance_mm=0.2` 下 **110 升 0.50/0.30**、
  2 个 0.49/0.28、1 个 0.46/0.25、88 个留在 0.45（0.60 扇出与 `U1.28` 卡住的就是这些）。
- `house_style.via_room`：逐孔房间的几何，四类约束（异网铜 / **孔壁间距不分网** / 焊盘钻孔 / 环宽）
  取最紧；**铺铜不做约束**（交给 `ZONE_FILLER`）；显式 `clearance_mm` 不会被项目里的 0.15 放宽。
- `house_style.segment_room`：逐段房间，弧线按折线参与。

补的写路径：

- `set_track_width` 支持 `uuids` 选择器（只改点名的段），并且**弧线也算走线**——
  `_track_nodes = items("segment") + items("arc")`，否则 157 条弧线会集体 `SEGMENT_NOT_FOUND`。
- 新 op **`set_via_size`**（OPS 43 → 44）。
- 工具数 244 → **246**（`board_width_ladder`、`board_via_ladder`）。

三条踩出来的裁边规则（写进 `docs/132` 第 10 节）：**等号也算被限**（目标恰好等于上限时
浮点间距是 0.1999999…，门会读成新违规）；**先裁尺寸再算环宽**（用裁前的 size 会写出
`0.49/0.30`，环宽 0.095 被 DRC 拒 `wanted 0.1`，整个 plan 回滚）；**环宽相等不裁**。

登记一个已知假绿：`board_apply` 的 engine 门在"扩铜撞铺铜"时不报新增违规——两次 DRC 里
有一次没重填填充区，0.0000 的 gap 让"新增"不可比。加铜之后必须自己
`kicad-cli pcb drc --refill-zones --save-board` 再跑 `board_drc`（`docs/132` 第 11 节）。

## [0.0.134] - 2026-11-11

「好板子的样子」从 18 块 demo 收窄成**真正造得出来的那批**：`only_buildable=true` 时每块板按
**它自己的叠层**声明工艺（层数从 stackup 数出来，1 oz）再过 P0，合格的才进语料。

18 块 → **13 块通过**、5 块被拦，每块都带上拦它的规则与实测/限值：

- `CM5_MINIMA_3`(6 层) `PCB.EDGE.ROUTED` 0.1914/0.2 —— 差 8.6 µm
- `complex_hierarchy`(2 层) `PCB.TH.ANNULAR_MIN` 0.175/0.18 —— 差 5 µm
- `jetson`(10 层)、`RoyalBlue54L-Feather`(8 层)、`tinytapeout-demo`(4 层) —— 实测 0.0 / 1e-5，真违规

13 块的目标区间（p10/p50/p90，mm）：线宽 0.15/0.35/0.80、跨网间距 0.10/0.20/0.61、
过孔直径 0.50/0.80/1.60、金属化钻孔 0.30/0.60/0.80、焊环 0.20/0.40/1.00、
铜到板边 0.254/0.564/2.0、较小板边 41.9/99.1/120.7。

两条新口径：

- **`marginal` 与真违规分开**：差 ≤0.01 mm 的失败仍然算 P0 不合格、仍然被剔出语料，但带
  `shortfall_mm` 与 `marginal` 标记——8.6 µm 可能是「板边量到锣刀中心线还是成品边」这类定义差异，
  读数把两种事实分开，不替规则松口。
- **层数从板里读**：拿 2 层去声明白一块 6 层板，得到的 "unbuildable" 说的是我们的声明，不是板。

`only_buildable` 默认 false（全部 18 块），显式打开才用这 13 块；语料带磁盘缓存按
（版本, 路径, mtime）失效，18 块全跑一次约 7 分钟。

tests 33 → 34（新增：合格板进语料、不合格板既被剔出也把档位计数降为 0、并指名拦它的规则）。

## [0.0.133] - 2026-11-11

`board_design_baseline` 现在除了「像不像」，还给「该画成什么样」：`suggested` 段把语料里
每块板的**最小值**取中位（成品板通常压到的下沿），给线宽 / 跨网间距 / 过孔直径 / 钻孔 /
铜到板边各一个起点值，并写死被谁压过：

- `board_rule_audit` 的 P0 厂商限值（带引文）
- 铜要过的电流与压降
- 实际工艺

两条诚实性修补：

- **下界不当读数**：`floor_values` 记下这一档里有多少值来自「搜索窗内没有更近的对」；
  整档都是下界时，建议值带 `caveats` 说明真实间距可能更大。
- **缓存带版本号**：磁盘缓存的 key 里加 `_CACHE_VERSION`，否则旧版本写下的档位会继续
  回答、字段却缺了新增项——看起来有效，其实漏了东西。

tests 32 → 33。

## [0.0.132] - 2026-11-11

规则只会说「不许」，画板需要一个**目标**。这一版加 `board_design_baseline`（第 244 个工具）：
把语料里每块成品板量一遍，按最近秩取 p10 / p50 / p90，再把本板放进每一档（below / inside /
above）。语料默认是 KiCad 自带的 18 块 demo，可用 `corpus_dir` 换成自己的板。

18 块板实测：线宽 p10/p50/p90 = 0.10/0.20/0.50 mm，跨网间距 0.09/0.20/0.45 mm，
过孔直径 0.40/0.60/1.60 mm，金属化钻孔 0.20/0.40/0.70 mm，焊环 0.20/0.35/1.00 mm，
铜到板边 0.20/0.35/1.83 mm，较小板边 41.9/91.4/110.0 mm。

三条口径写死：

- **`blocks_nothing`**：不拦写、不给 pass。落在中间 80% 之外只说明和这批板不同，不说明错
  （1 oz 有余量时 0.15 mm 线是正当选择）。
- **`lower_bound` 不当真值**：「搜索窗内没有更近的对」是下界，读数会顶到窗宽，标记透出。
- **测不到就是 unknown**：没有金属化通孔就没有钻孔读数，给 0 会被读成零孔径。

语料取不到报 `CORPUS_NOT_FOUND` + 试过的目录，不拿两块板凑「档位」。18 块板量一遍约 2 分钟，
所以结果按（路径, mtime）落在临时目录的 JSON 里，文件一变缓存即失效。

tests 28 → 32（新增 4 条，全部用小语料手算可核：缺语料必须报错、档位必须包含它自己的值、
自身必须在档内、不可测必须是 unknown 而不是 0）。selftest 363/366（3 项已知 ngspice-47）。

## [0.0.131] - 2026-11-11

这一版做**门**：规则能判板子能不能造，`ruleset/precondition.py` 判它**现在能不能动**。
写工具（`edit/plan.py` 的 `propose` 与 `apply_plan`）在写任何字节之前先过六条机器可裁决的检查。

**不是"未确认不得布线"，是"状态不对不得布线"**——门是一份状态，不是一个人工确认框。

### 六条检查（全部不需要人、不需要网络、不猜）

| 检查 | 判什么 | 结果 |
|---|---|---|
| `rules_baseline` | 规则集能加载；15 条阻塞规则全有来源，29 条 fab 限值全有逐字引文 | `ok:false` → 拒绝 |
| `drc_engine` | kicad-cli 在不在 | 不在 → 拒绝；`options.engine=false` 只记未定（那是调用方的开关，门不覆写契约） |
| `outline_known` | 板框存在且闭合（否则"离板边太近"不是事实） | 落铜时拒绝 / 非铜降为未定 |
| `mech_unchanged` | 计划没有偷偷改 Edge.Cuts | 拒绝，除非 `allow_edge_changes=true` |
| `clearance_no_worse` | 计划没有把不同网络铜拉到 0.2 mm 以内 | 拒绝（**增量判据**） |
| `process_declared` | 有没有可对照的工艺声明 | 只报不判，且不算 pass |

三条设计口径：

- **增量而非绝对**：`clearance_no_worse` 只拒"这次新造出来的"贴近对。真板本来就可能有
  0.1 mm 的既有对，要求先修好历史才能编辑，会让门在最需要编辑的板上失效。
- **propose 与 apply 用同一份数**：propose 把 `outline` / `clearance_after` /
  `clearance_before` 存进 `preconditions_input`，apply 复判用它；只有 `rules_baseline`
  与 `drc_engine` 在 apply 时重读。门与它守的提案不会给出两个说法。
- **关闭开关 = 回复逐字节不变**：`options.preconditions=false` 时连 `preconditions` 键都不出现。

拒绝抛 `PRECONDITION_NOT_MET`，`extra.preconditions` 带六条完整读数。

### `board_fab_package` 加 DRC 门（默认开）

出包前对**这个文件**跑一次 `kicad-cli pcb drc --refill-zones`：有 error 或未连通项 →
`FAB_REFUSED_UNCLEAN` 拒绝出包；engine 跑不起来 → `FAB_GATE_UNRUNNABLE`；
warning 不拦，写进 `out["drc"]`。`drc_gate=false` 只跳过检查，不会把脏板变干净。

### 实测

- 两态对照（`_t/pp2.kicad_pcb`，4 error + 1 未连通）：`drc_gate=true` → `FAB_REFUSED_UNCLEAN`；
  `drc_gate=false` → 正常出包（9 个文件、244+246 flash、两套钻孔）。
- 端到端（`pic_programmer`）：(100,-120)→(112,-120) 加铜被 `refused_at_clearance_no_worse`
  拒（0→3 对）；(200,-130)→(210,-130) 五门全过写入成功。
- `tests/test_ruleset.py` 21 → **28 passed**（新增 7 条，每条都有反向对照：
  找不到引擎必须 fail、显式关引擎必须只剩未定、删一条引文必须 fail、无板框落铜必须 fail、碰 Edge.Cuts 必须 fail、
  `0→3` 必须 fail 而 `3→3` 必须 pass、`preconditions:false` 必须不出现该键）。

## [0.0.130] - 2026-11-11

这一版做**规则**：把散落在 `layout_review` / `design_rules` / `jlc_spec_check` 里的判据收成一套
带优先级的规则集（`pcb_agent_mcp.ruleset`），再用 **18 块 KiCad 官方 demo 成品板**去问它：
`if 规则说这些板做不出来，那是规则错了`。

### 规则从三个地方来，55 条

| 来源 | 条数 | 落到哪层 |
|---|---|---|
| 厂商能力库 `jlc/data/jlc-pcb-capabilities.json`（每条带 `src`/`tier`/`url`/逐字 `quote`） | 29 PCB（+20 SMT，仅 `process.smt` 时加载） | P0（丝印 3 条降 P4） |
| 设计目录 `calc/rules.py` | 22 | P1/P2/P3/P4 |
| 本包自己量的几何（连通性 / 45° /  courtyard / 出框） | 4 | P1×1 P2×2 P3×1 |

### 五级优先级，和三条硬规矩

P0 可制造性 / P1 电气完整性 / P2 装配机械 / P3 信号质量 / P4 印字可读。

1. **只有 P0–P2 能阻塞**：P3/P4 的 fail 只进 `flagged`，且只在没有阻塞项时才出现。
2. **阻塞层之下全部 `deferred`**，保留自己的 `graded_state`；`first_action` 只从阻塞层按
   margin 升序取——低层的 fail 永远不许当「下一步」。
3. **severity ≠ priority**：`min_trace_width` 目录 severity 是 `Low` 但 P0（板厂真拒单）；
   `rf_z0_tolerance` 是 `High` 却 P3（阻抗偏 5% 的板照样装、照样连通）。

### 18 块官方 demo 校准（含两块未布线正对照）

| 结果 | 板数 |
|---|---|
| 不声明工艺 → P0 `undetermined`，只报 P1 及以下 | 18/18 |
| 声明工艺（2 层 1 oz）后 **P0 全 pass** | **11/18**（6 块 P3 flagged + 4 块 20+ 条 undetermined + StickHub 转 P1） |
| 声明工艺后被 P0 拦（每条都指到具体焊盘/过孔坐标） | **7/18** |
| 未布线正对照被 P1 `layout.connectivity_complete` 拦住 | 2/2 ✓ |
| 不声明工艺时被 P1 拦（含两块正对照） | 4/18 |

### 第一轮 6 块量产板被误判 unbuildable、vme-wren 被误判 P2：八类假阳性全是自己的错

| # | 假阳性 | 根因 | 修法 | 权威对照 |
|---|---|---|---|---|
| 1 | NPTH 安装孔当镀通孔 | `_hole_list` 不带 `pad_type`，无铜孔被算进 `min_th_annular` ⇒ 焊环 0 | oracle 带 `pad_type`；measure 分流镀通/非镀通，新增 `min_npth_drill`/`min_npth_ring` | 该板重判 P3 通过 |
| 2 | 丝印填充图形当线宽 | `fp_poly` 的 `width` 是填充轮廓，不是笔画 | 排除 `_SILK_AREA_KINDS`；`PCB.SILK.*` 三条整体降 P4 | KiCad DRC 不报丝印细 |
| 3 | 无网络铜造出 0 间距 | `net=None` 焊盘的 bbox 覆盖整颗器件，与别人的铜算出「重叠」 | 无网络铜退出异网间距扫描，另报 `copper_items_without_net` 计数 | 真最近间距是 0.091 mm，不是 0 |
| 4 | oracle 拼写导致盲判 | oracle 返 `measured`，ruleset 只认 `value` ⇒ 三条规则永远 `undetermined` | `_norm()` 归一；`unmeasurable` 以 `no ` 开头视为 `NONE_PRESENT` | — |
| 5 | 板框断链 → 碎成内槽 | `_chain_segments(tol=1e-6)` 把 1e-3 级折线断开，碎片被当板框/cutout | `_CHAIN_TOL = 1e-3`，未闭合链记 `board.outline_open` | 修后板框 160.1×233.5 mm 单条闭合、1338 个出框器件归零 |
| 6 | custom 焊盘被旋转两次 | `pad["rotation"]` 是**绝对角**，adapter 又按 fp 角转了一次 ⇒ 两盘相交成 0.0 mm 假短路 | adapter **不再旋转** `polygon`，旋转只在 `canonical.pad_points` 做一次 | `kicad-cli pcb drc` 对该板 **610 条违规、0 条 clearance** |
| 7 | through via 内层凭空多出焊环 | 文件带 `(remove_unused_layers yes)`，该 via 在内层本无铜 | 新增 `_resolve_via_copper()`：首末层 ∪ zone 连接层 ∪ 同网 zone 层 ∪ 邻近同网 track 端点层 | 该 via 解析为 `['F.Cu','In5.Cu','B.Cu']`（span 12）；4370/4374 个解析；audit 225 s→87 s |
| 8 | `connect` 焊盘被当间距对 | `(pad "1" connect ...)` 是焊锡桥，KiCad DRC 刻意不查其间距 | oracle 打 `pad_connect` 标记，measure 跳过任一侧带标记的对（只标记不删） | `kicad-cli pcb drc` 对 StickHub = **0 条违规**（该板真因未连通转 P1） |

另两处口径：**板厚吸附**（`cm5_minima` 叠层总量 1.5296 mm 吸附到 1.6，容差 0.1 mm，记 `process_snapped`）；
**同一数字只许一个 owner**（`min_trace_width`/`min_via_hole` 交厂商库判，目录只留可发现性说明 `graded_by`）。

### 修完假阳性之后剩下的 P0 fail——全部是文件里真实的几何

细焊环 0.150/0.18（`SW501.4`）、铜到板边 0.191/0.2 与 0.0/0.2（`J1.16`、`JP1.2`、`J2.1#3`）、
钻孔存成 0.00001 mm、两个过孔同一坐标 (136.960,−80.660)、`J2.1` 三个焊盘共用编号 1 其中两个同坐标、
异网铜真重叠（`U5.1` vs `U5.2`）。**这是「官方 demo ≠ 官方可量产」，不是规则错。**

### 新增

- `pcb_agent_mcp.ruleset`：`levels.py`（五级 / 阻塞判定 / `resolve()`）、`measure.py`（40 条事实，
  一次量完）、`audit.py`（合并两个库 + 下界即证据 + 厚度吸附）。
- 工具 `board_rule_audit`（整块板按优先级判，只读）与 `pcb_rule_catalog`（规则全表），
  工具数 241 → **243**。
- `tools/calibrate_rules.py`：语料校准台，逐板落盘、可 `--only` / `--markdown`，重跑跳过已完成的。
- `tests/test_ruleset.py` 18 条，每条都有能反向失败的控制（未布线正对照必须 P1 拦、NPTH 不许
  算出零焊环、下界满足限值必须判 pass、非法 state 必须抛错…）。
- `docs/132-规则优先级与官方成品板校准.md`：逐板结果、四类假阳性的取证、剩余 fail 的坐标，
  以及 **20 条恒定 `undetermined` 的规则缺哪个 fact、由谁补** 的欠账表。

### 口径写死

- **下界即证据**：`lower_bound` 已满足 `>=` 限值 ⇒ pass 并标 `measured_from: lower_bound`；
  已超 `<=` 限值 ⇒ fail。证据是「至少这么小」，不是「大概这么小」。
- **P0 判的是「按声明的这道工艺」**，每条 fail 带 `process` 与逐字 `quote`，不写成判决书。
- `undetermined` 永不计入 pass，也不冒充「通过」。

## [0.0.129] - 2026-11-11

这一版做一件事：**把 `jackwang988/jlceda-pcb-ai-workflow`（MIT）整套制板门禁吸收进来**，
变成 `pcb_agent_mcp.jlc` 子包 + 12 个工具，并接进制板包主流程。

### 吸收了什么（原来的缺口）

| 缺口 | 现在 |
|---|---|
| 没有任何工艺规范库（`dfm.py` 刻意不内置厂商数字） | `jlc/data/*.json`：29 条 PCB + 20 条 SMT + 24 条 BOM/CPL，每条带 `src`/`quote`/`tier`(T0/T1/T2)/`url`/`fetched_at`，T3 第三方禁止入库 |
| 只有"是否合格"，没有三档 | `jlc_spec_check`：`BLOCK`(低于官方极限)/`WARN`(极限与建议之间)/`PASS`，`NEEDS_EVIDENCE`(适用但没实测值，exit 2，绝不写 PASS)、`N/A`(板上无此对象，用 `NONE_PRESENT` 表示) |
| Gerber 只有 M02\* 级浅检查 | `jlc_gerber_guard`：自己解析 RS274X/Excellon，量板框闭合与 BBox、最小线宽/孔径/槽宽/各档钻孔、丝印宽度、单位与坐标格式一致性，并按**角色**认层（`-F_Cu.gbr`、`.GTL`、`%TF.FileFunction%` 三套都认） |
| BOM/CPL 只读 CSV 行 | `jlc_bom_cpl`：真实编码（EDA 原生 UTF-16 TAB 是 WARN + 规范化建议，不是静默乱码）、必需列、位号文法、数量、LCSC 号、坐标单位、旋转位数、BOM↔CPL 位号集合交叉核对、坐标是否落在板内 |
| 没有对话门禁 | `jlc_gate`：G1 原理图→G2 库/封装→G3 布局→G4 PCB→G5 导出；`confirm` 必须带用户原话 + `bind`(sha256)，改了被绑文件即 `STALE`，前序失效后续也失效 |
| 封存只到 manifest | `jlc_release`：G1–G5 全 confirmed 才允许，封存时**重跑**复核（不信旧报告），写 `release/<version>/manifest.json` + 报告快照 + 门禁留痕；**不上传、不下单** |
| 没有规则文件自己的对照 | `jlc_rules_check`：6 个官方 DRC 预设抽最小允许值，回判是否比官方极限更松 |
| 没有几何/原理图只读审计 | `jlc_geometry_audit` / `jlc_schematic_spec`：重叠、越框、keepout 被压、位号碰撞、标注缺失；**只判不给布局** |
| 没有踩坑清单 | `jlc_pitfalls`：19 条（导出、钻孔、BOM/CPL、层命名、阻焊、拼板、版本漂移） |

### 接进主流程

- `board_fab_package` 新增 `jlc_guard` / `jlc_process`（默认关）：打开后在导出包上跑
  `gerber_guard`，结果放在返回的 `jlc` 段；不给 `jlc_process` 就只做结构复核，
  **因为铜重猜错会选到错的极限行**。
- 默认 `core` 工具集加 4 个：`jlc_gerber_guard` / `jlc_bom_cpl` / `jlc_gate` / `jlc_release`
  （其余 8 个一个 `tool_search` 就到；core 列表实测裁剪后 46 670 B，仍在 48 KiB 一页内）。

### 移植时改的三处（不是照抄）

1. 判定字段 `status` → `verdict`，避免和信封的 `status: ok|error` 撞名；
2. 层识别从"固定文件名"改成"**角色**"，否则对我们自己 kicad-cli 导出的包必然误报 BLOCK；
3. 包里的 `.txt` 导出说明和 `.GDD` 钻孔图**不再被当 Excellon 解析**（移植时实测出的假 BLOCK）。

### 测试

`tests/test_jlc_gates.py` 15 条，每条都有能反向失败的控制：库自检对缺 `quote` 的规则必须报错、
0.05/0.11/0.30 必须落三档、删掉顶层铜文件必须 `GB.FILE.REQUIRED`、导出说明不得被当钻孔、
BOM↔CPL 位号集合错一个必须 BLOCK、门禁 PENDING→CONFIRMED→STALE 与"没原话/没绑定/跳序"必须拒、
门禁没全过时封存必须拒。

来源与归属：能力与规范数值移植自 [jackwang988/jlceda-pcb-ai-workflow](https://github.com/jackwang988/jlceda-pcb-ai-workflow)（MIT），
原项目作者保留其著作权；本仓库只做上述三处改造并接入自己的信封。

## [0.0.128] - 2026-11-07

这一版没有新工具，只有一件事：**把「下通道总线画不出」从前面的算术推断变成真画一遍的实测**，
以及一条它暴露出来的布局结论。（`0.0.119`–`0.0.127` 是逐次提交顺延的号，没有单独记录。）

### 实测：下通道 8 网总线，冲突 31 → 6

在副本 `stm32f412rg_core.v2.kicad_pcb` 上真画、真让 KiCad 判，`v1` 一个字节没动。

- **行序可证无交叉**：8 条网的 U1 焊盘 x 与排针 x 同序，按排针 x 降序排行（PA3/PA4 互换一位）后，
  竖出线是否撞上层行只取决于 `pad_x(i) > max hdr_x(j), j<i`；8 条逐条成立。
- 落板后 DRC 冲突 31 → 10；把 `VCAP1` 换层点挪到 x>60.64、`PB12`/`PB13`/`PC0` 各改一次
  跳层后 → 6（3 处 `shorting_items`、2 处 `clearance`、1 处 `solder_mask_bridge`）。
- 剩下 6 处全在右下角 x 57..64：`PB12 PB13 PB14 PB15 PC0` 五条网在那里争同一条 4 mm 的缝，
  给任何一条换层就会在 B.Cu 上撞到另一条（实测 PB12 的 B.Cu 横段撞 PB13 的过孔、
  PB13 的 B.Cu 斜段撞 C8 的 GND 焊盘）⇒ **它们必须一起重画**，不是逐条修。

### 结论：总线该走背面，因为 J2A 是插件排针

THT 焊盘每层都有铜，背面走线可以直接落进焊盘，排针那端根本不需要过孔；而挡住总线的
`PB12 PB13 PB14 PB15` 全在正面，总线搬到背面后它们一条都不用动。出线端仍受
「0.5 mm 焊盘排打不下过孔」限制，但解法是先往 F.Cu 走 0.41 mm 再交错过孔。
详见 `docs/122` §四点八 / §四点九。

### 测：旋转焊盘的落点

`tests/test_fidelity.py` 新增一条：旋转 90°/45° 的 footprint，焊盘落点必须与 pcbnew 实测值一致
（Y 取反口径）。零度不是用例——那正是当年把旋转符号写反却自洽的地方。

## [0.0.118] - 2026-11-05

一件事：**「画得难看」一直被当成审美问题，于是每次只能靠照着别人的板子临摹**。
这一版把那些话翻成能判死的数——五个只判不画的几何判据，加四条写进 skill 的规则。

### 新增：五个几何判据工具（只判不生成，不构成自动布局/自动布线）

- `board_constraints_report` — 项目里到底写了什么限制（`design_settings.rules` 与
  `net_classes`），哪些线正贴着下限（`hugging`），以及**两处规则互相矛盾时引擎用哪个**
  (`contradictions`)。没有项目文件时报 `PROJECT_ABSENT`，不拿默认值充数。
- `board_footprint_extents` — 每个器件的四类盒：**真实 courtyard**（读 `F.CrtYd` 图元）、焊盘、
  丝印、铜箔图形；没画外框的器件列进 `missing_courtyard`（那是不确定，不是零）。
- `board_geometry_check` — 不等 DRC 判决，直接量铜：最近一对线（`closest_pair`）、间距违规、
  线压别人焊盘、锐角清单、直角清单。拐角只在「同网同宽恰好两段相接」处量，T 型接头跳过不猜。
- `board_pad_obstacle_map` — 栅格化成文本的落点合法性图（`rows_bottom_up` + `free_fraction`）
  与点探针。**障碍只有焊盘/线/过孔，故意不算器件本体**——本体可以压线，把本体当障碍正是
  上一版器件乱飞的原因。焊盘按**矩形**膨胀，不按外接圆（外接圆会把合法点报成不可用）。
- `board_fanout_order_check` — 两个平行焊盘组之间的 45° 扇出**能不能零交叉**，是配对的
  不变量：交叉数 ≥ 逆序数，「零交叉存在」⇔「逆序数 = 0」。支持 `side_a/side_b=left|right|
  top|bottom` 取 LQFP 的一条边，`mapping` 排除 GND/+3V3/VBUS。工具会独立重算一遍扇出数真实
  交点，与逆序数矛盾时 `self_check_failed` 拒答。

### 修：`move_footprint` 的后置条件把「角度 0」判成错

`(at x y)` 在 KiCad 里就是角度 0，但校验函数要求文件里显式写出角度，于是**它把自己写出的
结果判成不合格**：一块真实板上 19 个 move 挂了 17 个，整批回滚，而 `failed_detail` 是 `None`。
改用 `arg_num(at, 3, 0.0)`。这条留在 skill 里当纪律：后置条件必须在真实工程板上整批跑一次。

### 修：适配器从来不解析 courtyard（永久解）

`F.CrtYd` 既不匹配铜也不匹配丝印/阻焊，被静默丢弃，于是所有「器件会不会打架」的判断
都退化成「焊盘盒加个余量」——实测差 0.4~1.9 mm/边。现在逐图元取局部盒加线宽一半、
按封装旋转转到板坐标后取包络；丝印项也补了 `bbox`（文字是 advance box，明确不是字形墨迹）。

### 口径改动（不许静默）：

`_scratch/f412rg2/v42_route.kicad_pro` 的 `min_track_width` 由 0.2 降到 0.15。理由：0.5 mm
间距的 LQFP-64 扇出只能走 0.15（这条结论本 skill 的「扇出算术」早写着），JLCPCB 最小线宽 0.09。
`board_rules_write` 不写 `design_settings.rules`，所以这个值是直接改的 json——**写在这里备案**。

### 反向对照（判据必须能失败）

`_scratch/geomctl/` 三块小夹板：`right90` 报直角 1 个（90°）、`fourtyfive` 报 0、`acute` 报锐角
1 个（45° 内角）。扇出判据用反单调映射必须报 `crossing_forced`（实测逆序 45 / 交点 42），
单调映射报 `zero_crossing_exists`（0 / 0）。

## [0.0.117] - 2026-11-04

一件事：**这个服务器能算过孔的电阻、载流、热栅栏，却从来没能在板上放一个过孔**。
于是「两面铺地、中间零个孔」的板子在所有检查里都是干净的——而人眼一眼就说它「没缝上」。

### 新增：`board_stitch_vias`（缝合过孔：读现状 / 出孔位）

- `action="audit"`（默认，只读）：现有同网过孔数、按 `pitch_mm` 居中网格里
  **落在该网 `filled_polygon` 内且两/多层都有填充铜**却没有孔在半 pitch 内的点数（gaps）、
  以及**换层信号过孔附近没有回流地孔**的个数与清单。
- `action="place"`：出 `add_via` 计划（走 `board_propose` + `board_apply` 四道闸门），
  网格点 + 每个换层信号过孔旁一个地孔。失败点**按原因**报告，不并成一个计数：
  `no_pour_here` / `pour_edge_too_close` / `too_close`(+`with_net`,`gap_mm`) /
  `hole_too_close` / `too_near_board_edge` / `existing_via` / `on_same_net_pad` /
  `no_room_around_signal_via`。
- 三条硬规则：① 点必须落在**填充**里（`filled_polygon`），不是 zone 外框；
  ② 单层没填充 ⇒ 拒绝 `no_same_net_pour`，绝不放出「一头悬空」的孔；
  ③ 孔环还要离填充边界一个半径 + clearance（否则环形跨在铺铜自己的间隙上）。
- 孔间距是**制造规则**，与网络无关：两个钻孔中心距 < 0.25 mm 一样拒绝。
- 差 0.05 mm 的点不丢，按 `nudge_mm`（默认 0.4）就近挪一步；挪不到才记账拒绝。
- 这不是自动布线：不动任何走线，只加孔，网格由调用者给。

### 实测（F412 核心板，v36 → v37）

- `board_stitch_vias` 计划 114 个孔（103 网格 + 11 个信号过孔旁的回流孔，40 个是挪过的）。
- 写入后：`existing_stitch_vias` 0 → 114；网格无孔点 104 → 5；
  无回流地孔的换层信号过孔 44 → 14（剩下 33 个四周被扇出占满，如实报告 `no_room_around_signal_via`）。
- 重铺后 `board_drc(refill_zones=true)` **74 条，`clearance` 0，与加孔前逐类相同** ⇒ 114 个孔一条新违规都没引入。

### 诚实边界

- 缝合孔的作用是「两面变一片 + 回流就近」，低速板按 2–5 mm 网格是经验值，不是算出来的；
  本工具不判断「多少个才够」，只报「按你给的 pitch 还差哪些点」。
- 间距由本服务器自己量（不走 KiCad 引擎），最终判词仍然只能由 `board_drc` 给。
- 差分对、跨分割这类回流问题属于另一条线（`board_cross_split`），不在本工具内。

## [0.0.116] - 2026-11-04

两件事：**DRC 到底该不该先重铺**，以及**角度第一次成为被检查的量**。

### 1. 修复（会改变读数，必须看）

`board_drc` 的 `refill_zones` 默认 **true**；`board_apply` 的 DRC 闸门两侧都带 `refill=True`。

- 现场：F412 核心板，231 段走线、两片 GND 区（F.Cu 优先级 1 / B.Cu 优先级 0），
  填充比最后一轮布线**早一代**。同一份字节：
  - 不重铺，`violations 502`，其中 `clearance 324`；
  - 重铺后（`kicad-cli --refill-zones`，scratch 副本，**不写文件**），`violations 52`，`clearance 0`。
- 机理：`filled_polygon` 是**快照**。填铜之后画的每一根线都没有被从多边形里挖掉，
  于是每根新线都被判成「走线 → 填充区 0.25 mm，实际 0.0000 mm」。
  后果不是「报得更严」，而是：① 一次真实只新增 3 条违规的编辑被闸门报成 **169~178 条**并回滚，
  编辑永远写不进去；② 反过来，习惯性 `allow_new_violations=true` 会把真短路当合格。
- 为什么这不是「放水」：重铺只会去掉幻觉。两根线太近，重铺后还是太近。
  **重铺后 clearance 从 324 掉到 0，说明那 324 条一条都不在走线之间。**
- 输出里新增 `zones_refilled_for_check` / `zone_refill_note`，不静默：报了就是报了。
  `_engine.drc(refill=)` 默认 False ⇒ 引擎层零行为变化；默认值的改动只在 `board_drc` 与写闸门上，
  并在回包里明说。

### 2. 新增 `board_45_corners`（角度第一次被量）

这个服务器此前只量**距离**（clearance / edge / annular ring），**从不问角度**，
所以一块 123 个 90° 直角、0 个 45° 的板子「全项通过」。这也是一块板能被画成
「自动布线都不如」的直接原因：没有一条判据在管拐角。

- `action="audit"`：`segments / arcs_skipped / off45_segments / off45_worst_deg /
  off45_median_deg / corners / corner_kinds{right,miter45,straight,other} /
  right_angle_corners / acute_corners / junctions / right_angle_shortlist`。
- `action="chamfer"`：保持拓扑的对称 45° 斜切，`apply=true` 走 `board_propose` + `board_apply`。
- 角的定义：顶点两侧的夹角。180° 共线（straight）／135° 已 45°（miter45）／90°（right）／
  <90°（acute，只报不修）。**≥3 条同网同层线段交于一点 = junction，只报不动**——
  T 型没有「一个角」，选哪条是主干是人的决定。
- 五种拒绝，见到就当结论读：`vertex_on_pad` / `vertex_on_via`（在焊盘或过孔上切角 = 拔铜断路）、
  `leg_too_short` / `leg_reserved`（腿不够长，或同一段两端各切一刀会互穿）、
  `would_collide`（附 `with` / `with_net` / `gap_mm`）。另有 `width_mismatch` 列表。

**实测（F412 核心板 v36，重铺后倒角）**：

| | segments | right | miter45 | acute |
|---|---|---|---|---|
| before | 231 | 123 | 3 | 0 |
| after | 312 | 42 | 165 | 0 |

`chamfered 81 / skipped {would_collide:28, vertex_on_via:6, vertex_on_pad:5, leg_too_short:2, leg_reserved:1}`。
倒角后 DRC（重铺口径）**52 → 52，一条不增**；unconnected 45 → 44。

**这条数字本身就是结论**：123 个直角只有 81 个能切 —— 45° 必须在布线的当下走出来，
布完再倒角只是验收，不是补救。

### 3. 技能

- 新 `skills/pcb-layout-methodology/references/07-routing-geometry-and-pours.md`：
  45°/圆弧的**诚实口径**（10 GHz 插损只差 0.15–0.28 dB，真正收益在铜箔应力：塑性变形低 37%；
  锐角是真缺陷 = 蚀刻酸角）、`L ≤ 0.4W` 与 `R ≥ 0.5W`、线宽梯度、铺铜参数与**快照陷阱**、
  缝合孔与回流、四处禁布区、泪滴、可判真伪的验收清单。每条带来源 URL。
- `SKILL.md` 加「布线之前必读的三条」。
- `pcb-agent-mcp-usage/references/01-tool-map.md` 重新生成（192 个工具，版本号改为动态读取）。

### 4. 已知边界（诚实列）

- `chamfer` 不产生新层、不改走线方向、不做弧线；圆弧仍要人给。
- `junctions > 0` 时不处理（T 型的主干选择是人的决定，工具不替人猜）。
- 倒角后 `off45_segments` 仍是 7（那是**段方向**不是拐角，来自斜线本身，与倒角无关）。

## [0.0.115] - 2026-11-04

断线续跑第二版：把 checkpoint 从「人记得写」变成「写盘就自动有」。

### 现场（第三次，同一天）

`app-server_2026-11-04.log`：

- `17:52:33 [context_compactor] compact success: c_compact, msgs=104->3, original_tokens=123191, compacted_tokens=60837, ratio=49.4%`（压缩那次请求本身 `cost_ms=84959`）
- `17:57:14 recommendation decision ... answer_runes=0 operation=run.recommendations reason=no_answer` —— 压缩之后那一次生成返回空
- `17:57:15 run forwarding finished duration_ms=1035952`（整轮 17.3 分钟）
- `18:38:15 session started`（41 分钟后客户端起全新会话）
- 全天：压缩 46 次，`answer_runes=0` 32 次；压缩中位 `original_tokens=112799 -> compacted_tokens=55356`；MCP stdio / JSON-RPC 协议层错误 **0 次**

同一位置第三次复现，数字每次都不同 ⇒ 断点在宿主的「压缩 → 再生成」那一段，不在 MCP 传输层。

### 新增

- `journal.auto(kind, text, board, data)`：写不进去就返回 `None`，**绝不抛异常**。写盘日志不该把一次成功的板级写入变成待排查的错误。
- `board_apply` 成功落盘后**自动**追加一条 `kind="apply"`，文本形如
  `apply ok -> v21_g4n5b.kicad_pcb bytes=41209 operations_done=3 operations_total=3`。
  回包 **一个字节都不变**（自检里对 dict 做了 `sort_keys` 全等比较）。
  干跑 / 失败 / 回包里没有文件的一律不写。
- `board_journal(action="resume")`：从**最新的 `kind="apply"`** 开始，带上它之后的所有 note，回包上限 **4096 B**，超了从**最旧**的一端丢。没有 apply 条目时回 `no_checkpoint=true`，不把尾巴冒充检查点。
- 5 条自检（自动落行且回包不变 / 干跑与失败不写 / resume 起点与预算 / 无检查点不冒充 / 关掉时静默跳过且报 disabled）。

### 边界（不许读错）

1. 自动行只证明「这一行字在盘上」，不证明板子被那次 apply 改对了 —— 仍然要重读文件、跑 DRC。
2. `PCB_AGENT_JOURNAL=0` 时自动落行**静默跳过**，此时 `resume` 回 `status=disabled` 并报出磁盘上原有条数。
3. 自动行按 `MAX_TEXT=400` 截断；截断只影响这一行，不影响任何工具答案。

## [0.0.114] - 2026-11-04

断线续跑：把一次交接从「重写一篇摘要」压成「读 8 行」——新增 `board_journal`。

0.0.113 把回包体积闸住之后，用户报的还是「现在还是断了」。这一次日志把现场
钉住了，而且比上一轮更像同一条链在换位置：

- `17:21:45` `[context_compactor] compact success: c_compact, msgs=57->3,
  original_tokens=88078, compacted_tokens=59054, ratio=67.0%` —— 压缩成功，
  压缩完的上下文仍有 5.9 万 token。
- `17:26:10` 紧随其后的那条请求 `cost_ms=260388`（260 秒），产出 85 995 字节。
- `17:26:14` `run forwarding finished ... duration_ms=1804814`（整轮 30 分钟）。
- `17:40:00` `session started` —— 客户端起的是一个**全新会话**。
- 全天 `UPSTREAM_TIMEOUT` 55 次、`empty choices` 281 次，**仍然没有任何
  MCP stdio / 协议层错误**。

结论不能改口：断点在宿主的压缩-再生成那一段，不在 MCP 的传输层。MCP 能动的
只有「让每次交接便宜」这一侧。盘上的板子一点没丢，丢的是对话自己的记忆；而
现在补回来的方式是让模型重写一篇多页交接摘要，那件事正是 260 秒里最贵的一段。

所以这一版加的不是又一个检查器，而是一个**只写不删、每行约 100 字节**的进度
日志：

- 存储：`$PCB_AGENT_HOME/journal.jsonl`（默认 `~/.pcb_agent_mcp`），只在该有
  写入时创建；`PCB_AGENT_JOURNAL=0` 可关。
- `board_journal(action='add', kind, text, board, data)`：一条笔记，`text`
  硬上限 **400 字符**（超了按 `ARG_TOO_LONG` 拒绝并给出上限，不静默截断），
  回执只给这一条 + 全库条数与字节数。
- `board_journal(action='tail', limit=8)`：最后 N 条 + 字节成本，实测 8 条
  回包小于 2 KiB。
- `board_journal(action='clear', confirm=true)`：`os.replace` 改名成
  `.bak-<stamp>`，**不删除**；`confirm` 不给就 `CONFIRM_REQUIRED`。
- 写满 4000 条或 2 MiB 时 `JOURNAL_FULL` 拒绝新增，绝不为腾地方丢旧条目。

三条不许读错的边界写进工具描述与自检：

1. 日志证明的是「这行字在盘上」，**不证明**任何一次布线/DRC 真的发生过；
   `tail` 的 `what_this_proves` 每次原样重复这一句。
2. `PCB_AGENT_JOURNAL=0` 时 `tail` 返回 `status=disabled` 且**同时报出磁盘上
   仍有几条**——「没读到」不等于「没做过」。
3. `clear` 是改名不是删除。

自检 4 条（`356/361 -> 360/361` 的计数漂移另行说明：新增工具让 5 条把「工具数
= 190」写死的审计链断言变成 191，已同步更新，见下）：

- 8 条写入后 `tail` 一次读回，回包 < 2 KiB（否则「读尾巴」等于又一篇文章）；
- 401 字符被 `ARG_TOO_LONG` 拒绝且库内仍是 8 条；
- `PCB_AGENT_JOURNAL=0` 下 `tail` 报 `disabled` + `entries_total=8`，
  `add` 报 `JOURNAL_DISABLED`；
- `clear(confirm=true)` 后旧文件在 `.bak-*`、日志路径不存在、`tail` 为 0 条。

工具数 190 -> **191**；`--selftest` 的计数型断言与叙述一并更新。

## [0.0.113] - 2026-11-04

会话也有体积预算了：一条回包合法 ≠ 整场对话还活着。

前两道闸门（0.0.104 的 48 KiB 单条回复、0.0.112 的 64 KiB 单帧）都只盯
**一个消息**，而用户报的「还是断了」现场是：宿主 `context_compactor` 记
`original_tokens=133868`，压缩完紧接着一条 `answer_runes=111` 的近空回复 ⇒
会话重启。四十条 48 KiB 的回包加起来是 60 万 token，每一条都合法。

- `PCB_AGENT_SESSION_BUDGET_KB`（默认 **256 KiB**，`0` 关闭）与
  `PCB_AGENT_SESSION_TAIL_KB`（默认 **6 KiB**）；
- 记账的是**真正进对话的字节**（`note_reply_bytes` 在 `cap_result` 的每条
  返回路径上记账），不是工具算出来的字节；
- 预算未到时，回包**逐字节不变**（反向对照写在自检里）；越过后，后续回包
  按 6 KiB 收尾，`reason_code = SESSION_BUDGET`，带上 `session_bytes` /
  `session_budget_bytes` / `why_this_is_short`，全文照旧落 `saved_to`；
- `serve()` 启动时 `reset_session_budget()`：一个进程 = 一场对话。

自检 **357/357 passed**（108.3 s，`_scratch/_st113.log`），新增四条
（不变 / 越界切 / 关掉不切 / 反向对照）。

## [0.0.112] - 2026-11-04

一帧（行）也有上限了，写不出去也不再断线。前两道闸门都是「每条回复」：
`tools/call` 48 KiB（0.0.104）、`tools/list` 25 KiB（0.0.109）。但**一个批次就是
一帧**，而承载它们的那个 JSON-RPC 消息没有任何上限——实测 8 个
`calc_capabilities` 合法回包拼成**一行 279 633 B**。读行那侧的默认上限是
`asyncio.StreamReader._DEFAULT_LIMIT = 65536`（本机 Python 3.12 实核），越线
的报错是「连接断了」，不是「回复太大」，所以这类故障每次都被读成「这个 mcp
又断了」。

- 新增 `PCB_AGENT_MAX_LINE_KB`（默认 **64 KiB**，0 关闭）。`server._line_for_wire`
  在把一帧变成字节**之前**就把它压进预算：单条回复超限就换成回执（保留 id、
  `reason_code=LINE_TOO_LARGE`、`bytes_full`/`cap_bytes`、原文头部 240 字符）；
  批次按元素分预算，**一条都不丢**（丢一条是协议错误），每元素各自降级。
  低于上限的回复**逐字节不变**（不加键、不重排），所以默认配置下这条闸门不可见。
- `write()` 不再只捕 `OSError`。以前 `_dumps`/`encode` 抛任何别的异常都会
  一路冒出 `serve()`，进程无声退出、客户端看到 EOF、日志里什么都没有——和
  「客户端走了」长得一模一样。现在：记日志 → 换成裸信封重试一次 → 只有 stdout
  真的关了或第二次仍失败才收线，并且明说是因为不愿写出一行坏帧去把客户端搞不同步。
- 自检新增四条（都带能失败的反向对照）：批次一行 ≤64 KiB 且 8 个 id 齐全；
  `PCB_AGENT_MAX_LINE_KB=0` 时同一批次回到约 279 kB（证明是这道闸门干的，
  且关掉即恢复原行为）；装得下的回复逐字节不变；一个第一次 write 就抛
  `ValueError` 的 stdout 之后，下一个请求仍然被答（ids 51、52 都在）。

**没有变**：48 KiB / 25 KiB 两个上限、分页、`saved_to`、`cap_result` 的裁剪
阶梯、超时默认 0（无限等）。本轮只补「帧」这一层。

## [0.0.111] - 2026-11-04

**把「画板」这件事写成两个 skill：一套流程，一份用法。**

零功能改动：`src/` 未变，工具数仍是 **190**，测试与自检门槛一条未动。

用户在拿到第 N 版 F412 两层板之后判「布局和走线完全都是狗屎，你连画板子的基础都没有」，
并要求「全网找资料学习放进这个 mcp 的 skill 里，还有就是把这个 mcp 的使用方法也放进 skill」。

复盘发现没有一条错误是技巧不足，全部是**顺序错**：双层板画成 4 层、单面贴片做成两面、
没有铺铜、用自动布线、先布线后规划导致 `W25_CLK` 的板底横干 + 竖井筑成 L 形死墙、
`U2`/`C2` 占掉东侧唯一走廊、中段只剩两条 B.Cu 车道而要过四条网 ⇒ 最后 12 组网络布不通。

所以产出两份技能文档（`skills/`，均过 `quick_validate.py`）：

- `skills/pcb-layout-methodology/`（SKILL.md + 6 篇 references）：第 0 步把约束写成可判定的数、
  七步流程（层叠 → 摆放 → 走廊规划 → 电源与地 → 关键信号 → 边布边量 → 验收）、
  扇出算术 `g ≥ n·w + (n+1)·c`、14 条实测失败模式（每条带当时的证据）、以及**什么时候该重摆
  而不是继续修**（改摆放代价 < 改布线代价 × 3）。
- `skills/pcb-agent-mcp-usage/`（SKILL.md + 工具全表 + 检查单）：190 个工具默认只广告 `core`，
  其余走 `tool_search` + `tool_call`；六阶段工作流（量事实 → 找空间 → 立意图 → propose →
  apply → 验）；权威性分层（`board_drc` 权威，`board_pour_connectivity` 只是中心点在多边形内）；
  48 KiB 对话预算与 `saved_to`；已退休的 `board_route`/`board_fanout`；经验库 `experience_*` 的
  写入三道门；九个真踩过的坑（`.kicad_pcb` 全在一行、层名是 `"F.Cu"`、过孔压焊盘不算连通、
  无网焊盘仍是障碍、`_scratch/geo.py` 的门限不是本服务的语义）。

联网取到并读了正文的三份来源：IPC-2221B Table 6-1 的逐行数值（rftools.io，**二手转述**，
正式报告须回原表）、ST 社区晶振选型与布局文（`Cs = CIO + CPCB ≈ 3 pF`、`CL1 = CL2 = 2(CL−Cs)`、
槽形地屏蔽与独立底板 + 地护墙 + 单独接最近 VSS）、Sierra Circuits 十大高速布线惯例
（两层堆叠方案、缝合 λ/20~λ/10、铺铜留 1.5–2 倍线宽、离板边 10–15 mil、差分 90 Ω ±10%）。
**诚实标注**：ST AN4879（USB）与 AN2867 的 PDF 正文本次未取到，只核到条目，故 USB 一节
只写通行做法、不引其数值。

文档 `docs/113-画板方法论与MCP用法两个skill.md` 附 14 条失败模式对本板的逐条命中表
（命中 11 条、已避免 2 条、待核 1 条），结论是「这一版布局要重摆」，不是再修几条线。

## [0.0.110] - 2026-11-03

**「又中断了」的归因，以及每轮都在付的那笔工具清单钱。**

在宿主自己的日志（`app-server_2026-11-03.log`，20 302 行）里，当天的 dying
签名是 `AIGCFailed err="empty choices"`（模型流返回空选择）→
`run terminal outcome classified err="agent run: empty choices"`：113 次，横跨
5 个会话；19 条 run 终止记录里 17 条是它，另 1 条是同一会话的
`agent run: [18] compact failed` —— 对话长到要压缩时，压缩本身也是一次模型
调用，它也吃同一个空流，于是压缩失败、run 判死。同一份日志里本会话被压缩三次
（78 767 / 123 625 / 104 242 tokens → 55 798 / 57 302 / 58 827），压缩底板
5.7 万 tokens。**MCP 这一侧的每个出口都在预算内**（initialize 624 B、
tools/list 37 210 B、ping 38 B、未知方法 95 B、批量 37 250 B）。

能改的只有「让对话晚一点需要压缩」：工具定义会作为函数声明进入**每一次**模型
请求，所以 core 档 37 210 B（≈11k tokens，占压缩底板约 19 %）是每轮都在付的。
`tools/list` 的预算因此从「无」改为默认 **25 KiB**，并把裁剪做成阶梯
（200/80 → 120/40 → 120/0 → 60/0 → 0/0），挑第一档能一页装下的：

- core 46 条：37 210 → **23 976 B（−36 %）**，仍是**一页**、46 条全在，
  参数名/类型/枚举/必填一个不动，只把散文缩到每工具 60 字
- `PCB_AGENT_MAX_TOOLS_KB=0` 逐字节还原旧答案（54 905 B 全文）
- 分页只在连裸 schema 都放不下时才发生（schema 地板 15 127 B）
- `PCB_AGENT_MAX_RESULT_KB`（tools/call 的 48 KiB）**语义未变**，两个变量分开

自检 350/350（新增 2 条：削的只能是散文、抬预算必须真把散文还回来），
`tests/test_server_protocol.py` 14 passed。

顺带修掉 0.0.108 留下的 3 条红测试：`tests/test_fanout.py` 还在向已退役的
`board_fanout` 要计划，现在改成断言 `TOOL_RETIRED`（连 `apply: True` 也必须
拒、且文件不变），并留一条「规划器本体仍在库里」的对照 —— 退役的是工具入口，
不是那段代码。

文档：`docs/110-中断到底是谁断的.md`。

## [0.0.109] - 2026-11-03

### Added
- `tools_page()` — `tools/list` 现在也受体积预算约束，三层：装得下逐字节原样发、
  装不下先瘦身描述（`description` 200 字符 / schema 内各参数 80 字符，名字/类型/
  `required`/默认值一字不动）、仍装不下才用 MCP 游标分页（`params.cursor` 进、
  `result.nextCursor` 出）。**分页只花往返次数，不丢条目。**
- 自检新增 4 条判据：默认 `core` 档一页且 ≤48 KiB；预算强制 8 KiB 时分页必须覆盖
  全部 46 条且每页 ≤8 KiB（反向对照）；闸门关掉时逐字节等于旧答案；`all` 档跟游标
  覆盖全部 190 条且每页 ≤48 KiB。
- `--selftest --verbose` / `PCB_AGENT_SELFTEST_VERBOSE=1` 打印全部检查行。

### Changed
- `--selftest` 默认只打 FAIL 明细 + 最慢 5 条 + 汇总：**stdout 85 KB → 4 002 B**。
  通过的检查行默认隐藏，失败行任何档位照打。
- `tests/test_server_protocol.py::test_tools_list_follows_the_profile` 改为走游标
  计数（原来断言「`all` 一次返回 190 条」，分页后会因正确的原因变红）。

### Fixed
- **`tools/list` 是唯一没有体积闸门的出口**：`tools/call` 自 0.0.104 起有 48 KiB
  上限，而每次会话必拉的 `tools/list` 原样全发——`core` 档 **54 905 B**（46 条，
  超 48 KiB 共 5 753 B）、`all` 档 **300 159 B**（190 条）。宿主只会把这件事报成
  「服务坏了 / 对话中断」，不会报成「应答太大」。修后 `core` 39 102 B 一页、
  `all` 5 页最坏 48 466 B。

### Documented
- `docs/109-tools-list才是那条没闸门的应答.md`（归因读数、三层修法、两条反向对照
  各自红过一次的经过、三行取证手册、三条欠账）。

### Measured
- `--selftest`：348/348 PASS，124.6 s，最慢 25.3 s（棘轮 180 s / 35 s 内）。

## [0.0.108] - 2026-11-03

### Added

- **stdio 循环不再被单个慢工具卡死**（`serve()`）。此前是 `for raw in stdin: handle(raw)`，
  只有一个线程：一个慢工具（起 `kicad-cli` 的 DRC、铺铜、解析大板）独占进程，连下一行都
  不读，于是 `notifications/cancelled` 永远看不到，宿主唯一能做的动作是杀连接——这在观测上
  与「对话死掉」不可区分。现在读线程 + 工作线程分离，取消立即回 `-32800`（带
  `still_running: true`，明说工具还在跑），可选 `PCB_AGENT_CALL_TIMEOUT_S` 把卡死的调用变成
  错误应答而不是冻住的会话。批量请求仍走内联。
- **自检自己也是闸门**：`check()` 逐条计时，>1 s 立刻打；收尾打印总耗时、慢检查合计与最慢
  一条；并新增一条预算判据。实测 344 条 / 140.7 s，其中 121.0 s 花在 13 条 >1 s 的检查上
  （最慢 25.1 s，共面波导的 2D 场求解；提交前复跑一遍 344/344 PASS、128.3 s / 最慢 23.9 s，
同一套判据两次相差 25 %，所以棘轮必须留余量）。判据写成**棘轮**（180 s / 单条 35 s）而不是目标价
  （120 s / 20 s），后者今天是 FAIL 的，写成通过就是装饰；目标登记在 `docs/108` 的欠账里。
- **移除自动布线器与自动 BGA 扇出**（`board_route` / `board_fanout`）：不是默认关，是从
  `TOOLS` 里摘掉，按名字调用回 `TOOL_RETIRED` 而不是 `TOOL_NOT_FOUND`。192 → 190 条工具。
  本服务器只提供执行原语，摆放与路径是调用方的决定。

### Fixed

- **`--selftest` 崩在一条 `isinstance` 写反上**：`[m.get("id") if isinstance(m, list) else m ...]`
  对 list 调 `.get`，抛 `AttributeError`，排在后面的 PASS/FAIL 一行都没打印。也就是说**上一轮
  「339/339 PASS」在这个源码上不可能出现**——闸门自己坏了，等于没有闸门。
- **自检的总耗时读数自己骗自己**：`_t0` 在同一函数里被 stdio 计时块再赋值一次（Python 是函数
  作用域），于是「total」算的是最后一段，读到 `0.5 s` 而实际 140 s。改名 `_gate_t0` 后读数成立。

### Documented

- `docs/108` 记录这一轮的中断归因：本会话没有一次 `tools/call`，中断与 MCP 应答无关；
  同时把 MCP 侧能推高上下文的三件量清楚——`tools/list` `core` 46 条 / 53 032 B、`all` 190 条 /
  290 189 B、`initialize` 623 B；以及 `PCB_AGENT_CALL_TIMEOUT_S` 默认仍是 0。

## [0.0.105] - 2026-11-03

### Documented

- `docs/103` 追加 §103.8：在仓库根目录那块 **88 MB** 的 `jetson-agx-thor-baseboard.kicad_pcb`
  （1125 封装 / 4862 焊盘 / 22151 走线 / 3235 过孔 / 125 zone / 1388 网络）上复测改前改后：
  `board_view kind=all` **95.6 MB → 765 B**（13 类计数）、`kind=footprints` 12.8 MB → **15 436 B/页**
  （53 个封装，`next_offset 53`）、`board_components` 371 KB → **28 489 B/页**、
  `board_nets` 139 KB → **9 509 B/页**；四个数都在闸门之下且都不是被闸门砍的（分页在产出端就解决了）。
  同一条里写死读法：分页只保证一页小，枚举完 1125 个封装仍是 22 页 ≈ 340 KB，
  真表要走落盘文件或交给计算层算完回读数。附实测耗时 78.2 s / 7.5 s / 47.0 s / 0.4 s。

## [0.0.104] - 2026-11-03

### Fixed

- **单条回复的体积闸门从「传输界」改成「对话界」**。此前仓库里四个上限
  （`_VIEW_MAX_BYTES` 64 KiB、`PCB_AGENT_MAX_RESULT_KB` 1024 KiB、`lceda_live` 4/16 MiB、
  `MAX_FRAME` 16 MiB）全在回答「管道搬得动吗」，没有一条在回答「读进上下文还剩不剩地方推理」，
  于是 109 190 B 的 `board_view` pads 页、68 089 B 的 `calc_capabilities`、
  `lceda_pro_run` 的整 `getAll()` 对象都能一路走到对话里把会话撑死。
  现在默认 **48 KiB**（实测锚点：最大的诚实答案 `board_ops` 33 340 B 之上、最小的倾泻
  `calc_capabilities` 68 089 B 之下），`board_view` 单页 64 KiB → **16 KiB**。
- **超限不再整包丢弃，改成截断 + 落盘**。旧的 `RESULT_TOO_LARGE` 让调用方付了查询的代价只拿回
  一句「太大了」。新的 `cap_result()` 留住 `status`、所有标量、每个列表的头部（按字节预算，不按条数）
  与每个长串前 2000 字符，加 `truncated` / `bytes_full` / `cap_bytes` / `truncated_fields`，
  完整 JSON 落到 `%TEMP%\pcb_agent_mcp_results\` 并以 `saved_to` 指出。
  **未超上限的回复逐字节不变**（不加键、不重排）。
- **「截断」必须真的变短**：第一版单档设置下 68 089 B 的回复截完仍 34 931 B，超过它自己声称的
  24 576 B 上限。现在按 `_CAP_SCHEDULE` 逐档收紧，仍装不下就退化成「形状+计数」，再不行退化成回执；
  实测 48 KiB→34 162 B、2 KiB→1 918 B、1 KiB→853 B，三档都在上限内。
- **列表不许按「前 8 条」砍**：一串名字是一个答案，砍成 8 条等于回错答案（186 个工具名里
  `board_summary` 是第 22 个，被砍掉后自检当场红）；列表改按每次预算的字节装到装不下为止。
- **自检里「量数据」与「量闸门」分开**。默认闸门开着跑 selftest，`magnetics_data` 的 500 行粉芯表
  被截成 27 行（另一轮 64 行），三条数据检查假 FAIL。现在 `selftest()` 正文把上限置 0 看完整答案，
  闸门由末尾四条专检负责（含「上限关掉就不许切」的反向对照），并新增一条默认值检查。

### Measured

- `python -m unittest tests.test_tools tests.test_server_protocol tests.test_tools_profile` → 46 tests OK
- `python -m run_server --selftest` → **336/336 passed**

## [0.0.103] - 2026-11-03

### Fixed

- `desktop.launch()` 现在显式构造子进程环境并摘掉 `ELECTRON_RUN_AS_NODE`，返回值多一个
  `env_cleared`。真因是实测的：本服务由 Electron 宿主启动，该变量被继承，于是任何 Electron
  app 变成一台裸 node——`--version` 打出 node 的版本、参数被当成脚本路径、无参数则开 REPL
  并因 stdin 为 EOF 立即 `rc 0` 退出；没有窗口、没有命令空间管道、应用日志一个字不写。
  摘掉后 `lceda-pro.exe` 的 `\\.\pipe\JLCEDAPro<8 hex>` 在 **1.0 s** 出现，
  `lceda_pro_status` 回 `ok`（`bridgeVersion 4.1.60.198f38ab`）。
- `APP_NOT_RUNNING` 的 hint 重写。旧文把现象当原因（「从 shell 启动会 0.5 s 退出，用 explorer
  才能活」），新文给两个静默真因：继承的 node 模式，以及启动方的进程树在调用返回时被回收
  （本会话里 `CREATE_BREAKAWAY_FROM_JOB` 直接 `WinError 5`，所以 `app_launch` 用
  `DETACHED_PROCESS` 起，并且必须在调用结束前确认它还在）。

### Tests

- `tests/test_desktop.py` 新增 `test_launch_does_not_hand_an_electron_child_its_own_node_mode`：
  把父进程的变量设成 `1`，启动一个只打印自己环境的小脚本，要求它写下 `absent`——
  判据是**子进程自己怎么看**，忘了传 `env=` 这种退步会红。
- `run_server.py --selftest` **331 → 332**：父环境设成 `1` 时 `_child_env()` 必须回报
  `dropped == ["ELECTRON_RUN_AS_NODE"]` 且子环境不含该键。

### Docs

- `docs/102`（新）：四个读数钉住现象、`ping` 被当脚本这一条取证、为什么旧观测真而解释错、
  「启动成功」与「启动之后还在」是两件事的四种启动对照表、三条教训。

## [0.0.102] - 2026-11-03

### Added

- `fab.board_extent(path)`：读一块板在**文件坐标**里占了多大，并判断纸张装不装得下。
  复用 `edit.ops._board_points` 的同一遍历（也就是 `translate_board` 搬的那批位置），
  所以「盒子」和「整板平移」对同一块板的理解一致。
- `board_export_graphics` 的 pdf / svg / ps / dxf 返回值多一个 `placement`：
  `bbox_file_mm` / `positions` / `y_axis` / `how_measured` / `paper` / `fits_paper` /
  `warnings` / `negative_axes` / `advice`。`status` 不变（文件是真的），`warnings` 只在触发时出现，
  未触发时返回体与 0.0.101 逐字节相同。
- `advice` 直接给出 `translate_board` 的 `dx` / `dy`。符号这里最容易反：编辑 API 是 Y 向上、
  文件是 Y 向下，所以抬高 `y_min = -30.48` 的板要填 `dy = -30.48`（等于 `y_min`），不是相反数。

### Changed

- 负坐标与超尺寸拆成两种原因：板在纸内但坐在负半轴时只报「落在负坐标 + 怎么搬」，
  只在真的比纸大时报 `clipped, not scaled`。此前一因两果。
- `_paper_mm`：认得的纸名按表查（统一按横向算），`(paper "User" W H)` 用自带尺寸，
  不认识的纸名 / 没尺寸的 `User` 一律**不判**——不出现 `paper` 也不出现 `fits_paper`。

### Tests

- `tests/test_graphics_placement.py` 八例。关键一条是端到端：
  `test_the_advice_actually_clears_the_warning` 把 `dx` / `dy` **从警告里读出来**（不是写在测试里），
  执行 `translate_board` 后重出一张 PDF，要求 `warnings` 清空、`fits_paper` 为真、`bbox` 的
  `y_min` 落在 0.00。建议只要还是文案，这条就会红。
- `run_server.py --selftest` **329 → 331**，两条都是 `board_extent`：一条判盒子与「读不了不判」，
  一条把警告里的 `dx` / `dy` 原样交给 `translate_board`，要求警告消失、`y_min` 恰好落在 0.00
  （符号填反只会「搬到了别处」，不会红——所以判据是「警告清空」而不是「搬过」）。

### Docs

- `docs/99` 追加 §99.7（同一块 F412 平移前后两行读数、符号的由来、六种「不判」情形、三条诚实披露）。

## [0.0.101] - 2026-11-03

**STM32F412RGT6 核心板：把 DRC 118 → 0 之后的全部读数落成一份可对账的板卡报告**
**纯文档，零代码改动**（工具数仍 **192**、写 op 仍 **41**、自检仍 **329/329**、`dependencies = []` 仍成立）

### 1. 新增 `docs/101-F412核心板报告.md`

一块用本仓库全流程做出来的板，DRC 清零、出图落位、交付包八种格式齐全之后，**它到底是一块什么样的板**：

| 读数 | 值 |
|---|---|
| 尺寸 / 面积 | 53.34 × 30.48 mm / 1625.803 mm²，四层、板厚 1.6 mm |
| 元件 / 网络 / 焊盘 | 32 封装 / 54 网 / 194 焊盘（49 通孔 + 145 贴片） |
| 导线 / 过孔 / 铺铜 | 556 段（**全部 0.15 mm**）/ 129 个（全 0.45-0.2）/ 2 块（GND→In1、+3V3→In2） |
| DRC | 118 → 76 → 72 → 46 → 17 → 2 → **0**，平移后重跑仍 0/0/0 |
| IR drop（0.5 A） | 2.943345 mV = **0.089192 %**，铜损 1.471 mW |
| 交付包 | gerbers/drill/pos/step/ipc2581/odb/ipcd356/gencad **八种全 ok** |

### 2. 这份文档要说清的三个「DRC 不会告诉你」的事实

1. **556 段全是同一个宽度不是巧合**：`board_view.facts.net_classes` 里只有一个 `Fanout` 类，
   `trace_width 0.15`、`clearance 0.15`，而 `nets_in_class` 把 **54 个网全部**装了进去——
   电源网和信号网被绑在同一档。
2. **这条在工程上能到多少**：按 IPC-2221（`I = k·ΔT^0.44·A^0.725`，外层 `k=0.048`），
   0.15 mm × 1 oz ⇒ 0.5 A 温升 **6.50 °C**、1 A **31.4 °C**、ΔT = 10 °C 只允许 **0.604 A**
   （内层 `k=0.024` 时 0.302 A）。所以「DRC 0」与「能跑 1 A」是两件事。
3. **118 条里没有一条是「不通」**：`unconnected` 与 `schematic_parity` **从第一份起就是 0**，
   全部是孔重合（28 + 16）、丝印字高（32 条，规则本身是 0.8 mm 而实际 0.7 mm）、
   丝印压掩膜（24 + 14）三类工艺/丝印警告。

### 3. 一份自洽校验，不依赖任何外部真值

同一块板跑 0.5 A 与 1.0 A：压降 **恰好 2.000000 倍**、铜损 **恰好 4.000000 倍**，
百分比 `0.089192 → 0.178385` 也按同一条 `100·mV/1000/3.3` 走。这三组数互相钉住，
正好是 0.0.100 那个 1000 倍错最缺的东西——当时 89.19 % 与 2.943 mV **谁都不咬合**。

### 4. 诚实披露（文档第 101.9 节，六条）

无阻抗控制（USB 是 0.15 mm 单端，不是 90 Ω 差分）/ 电源与信号同宽 / 缝合孔不是先行做的 /
无板边工艺（无邮票孔、V-cut、拼板、阻抗条）/ 只有 `.kicad_pcb`（`schematic_parity` 为 0 是因为
没有原理图可比，不是「比对通过」）/ 未跑热与机械。

## [0.0.100] - 2026-11-03

**`board_ir_drop` 的 `worst_sink_drop_pct_of_3v3` 放大 1000 倍：2.94 mV 报成了 3.3 V 的 89.19 %**
**对外可见的数值变更，所以单独占一个版本号，不是静默 bugfix**
（`test_board_ir_drop` 9 例 → **10 例**；压降相关四个模块 **80 例全绿** /
工具数仍 **192**、写 op 仍 **41**、`dependencies = []` 仍成立）

### 1. 它错在哪

`max_drop` 是**毫伏**（变量名里就写着 `drop_mv`），下一行却当**伏特**除 3.3：

| 读数 | 值 |
|---|---|
| 现场（F412 核心板 `J2A.1 → U1.1`，0.5 A，铺铜参与建模） | `max_drop_mv` = **2.943345** / 百分比 = **89.192273** |
| 手算 | `2.943345 / 3300 = 0.0892 %` |
| 自洽性旁证 | `total_copper_loss_w` = 0.001471 (= 0.5 A × 2.943 mV) ⇒ 压降就是 mV 级 |

3.3 V 上掉 89 % 意味着 MCU 拿到 0.36 V——**这块板根本起不来**。两个字段互相矛盾时，
通常是单位的错，不是电路的错。

### 2. 为什么活到 0.0.100

`tests/test_board_ir_drop.py` 断言了 `max_drop_mv` / `total_copper_loss_w` /
`worst_current_density` / `edge_count` / `node_count`，**唯独这个百分比一条断言都没有**。
派生字段没人算过，就没人发现它算错。

### 3. 修法与门禁

- 代码：`round(100.0 * (max_drop / 1000.0) / 3.3, 6)`，并在原处写下这条错的历史；
- 新增 `test_pct_agrees_with_mv`：① 自洽（百分比必须由同一个 `max_drop_mv` 推出）
  ② 绝对值（20 mm 的 0.5 mm / 35 µm 铜在 1 A 下 19.7029 mV ⇒ **0.597056 %**）。
  只写 ① 等于让两个字段互相作证，只写 ② 换个电源电压又漏。

### 4. 边界

只改这一个字段，压降/损耗/电流密度/网络拓扑算法一字节未动；字段**含义**未变；
但此前按 89 % 做过的判断是按 1000 倍的数做的 ⇒ 对外写明，不假装是内部整理。

## [0.0.99] - 2026-11-03

**新 op `translate_board(dx, dy)`：整板按同一增量平移，fail-closed**
**它存在的理由是一个读数：DRC 已经 118 → 0、Gerber 也正常，可出图是一张白纸**
（`test_ops_edit` 78 例 → **84 例** / 受影响模块 **201 例全绿**（test_ops_edit 84 + test_edit / test_tools / test_tools_profile / test_fidelity 102 + tests.test_step_apply 15） /
工具数仍 **192**、写 op 40 → **41**、`dependencies = []` 仍成立）

### 1. 白图不是导出参数错

STM32F412RGT6 核心板 DRC 清零后出图，`pymupdf` 量到内容 bbox
`Rect(1.4, -84.98, 149.8, -1.4)`——**整块板都在页面外**。同参数导真实板
`StickHub.kicad_pcb` 得到 `Rect(401.8, 226.8, 448.6, 338.5)`，落在 841.9 × 595.3
的页内。所以不是参数：`kicad-cli pcb export pdf/svg` **按文件坐标原样摆位**，而这块板
的文件 Y ∈ [-30.48, 0]，整板位于负 Y ⇒ 落在纸的上方被裁掉。Gerber 不受影响
（`Edge_Cuts.gbr` 是 `X0Y0 … X53340000Y30480000`，导出器自己归一化过）。

三次渲染绕过全部失败并记进 docs/99：`pixmap(clip=)`、`set_mediabox(负 y)`、
cairosvg 换 `viewBox`。

### 2. 为什么平移整板，而不是给导出加偏移

加偏移 = 让人看的图和文件说两套坐标，此后「图上的位置 ↔ 文件坐标」的每次换算都要带一个
隐藏偏移，而 DRC 的 `items[].pos`、Gerber 的 X/Y、`board_view` 的焊盘坐标全是文件坐标。
平移整板则所有读数一起动、相对几何不变——这正是它能被验证的原因。

### 3. 参数与三条边界

| 参数 | 含义 |
|---|---|
| `dx` | mm，默认 0 |
| `dy` | mm，**Y 向上**（与 `board_view` 同一个坐标系；文件里的 Y 是反的） |
| `dx = dy = 0` | `ARG_INVALID`——这是笔误，不是操作 |

- **封装只搬自己的 `(at …)`**：封装内部坐标是它自己的坐标系，跟着父对象走；
- **`setup` 整块跳过**：里面是 `aux_axis_origin` / `grid_origin`，绘图原点不是铜；
- **未知顶层种类 → `UNSUPPORTED_ITEM`**：搬认识的、留下不认识的，会让一块板静默裂成两半。
  测试里构造一个 `(table (column_count 2) (at 5 5))` 验证「拒绝且文件一字节没变」。

### 4. 后置条件用位置指纹，并写死它的局限

平移没法用「绝对值等于多少」验：要验的正是「每个点都动了、动的量一样」，而「之前在哪」
只存在于运行期。所以 `run` 把平移前后全部位置（遍历序、文件坐标）各取 sha256 写进 record，
`verify` 在**写完的文件**上重算并对照四项：位置个数、`digest_after`、record 的 `dx/dy`、
`digest_before ≠ digest_after`。`verify_limit` 明说：指纹是位置的**多重集**，它证明整体动了
这么多，不证明每个点仍属于原来那个对象。

反向对照：写成功后把一个角点改回原处，同一 record 再跑 `verify_all` 必须红，
且理由含「not the ones the run computed」。

### 5. 六条测试（`tests/test_ops_edit.py::TranslateBoard`）

整板同增量（边界线/线段/过孔/两 zone/封装 `at`，`items_total` = 10）· 封装内部坐标不动 ·
30° 旋转保留 · 零位移拒绝 · 未知种类拒绝且不落盘 · 反向对照。
Y 的符号每条各验一次（`dy = 25` 让文件 Y 从 10 变成 −15）。

## [0.0.98] - 2026-11-03

**`set_footprint_field` 加五个可选几何参数：字段印在哪一层、在哪、多大**
**顺带修掉「改不了位号名字」——写后 `verify` 还按旧名字找封装，改名永远回滚**
（`test_ops_edit` 71 例 → 78 例 / 受影响的五个模块 **160 例全绿** /
工具数仍 **192**、写 op 仍 **40**、`dependencies = []` 仍成立）

### 1. 32 条 `text_height` 与 46 条丝印冲突，都指向同一个缺口

STM32F412RGT6 核心板（`kicad-cli pcb drc --severity-all`）第一版 **118 条**，其中
`text_height` **32**（位号 0.7 mm，低于板厂最小丝印字高 0.8 mm）、`silk_over_copper`
**24**、`silk_overlap` **14**。把字高抬到 0.8 之后总量 **118 → 46**，剩下的 46 条
`silk_over_copper 26 + silk_overlap 18 + silk_edge 2` **100 % 涉及参考字段**——位号
压在自己的丝印框或邻居的焊盘上。要修只能**挪它**，而那个 op 当时只会改字符串。

新增五个可选参数：

| 参数 | 语义 | 拒绝 |
|---|---|---|
| `layer` | 文本印在哪层 | 不在 `(layers)` 表 → `LAYER_NOT_IN_BOARD`；不在白名单 → `LAYER_NOT_ALLOWED` |
| `x` / `y` | 封装**局部**坐标，**Y-up** | 非数 / 布尔 / `NaN` / `±inf` → `ARG_INVALID` |
| `text_height` / `text_width` | 字高 / 字宽，mm | `≤ 0` → `ARG_INVALID` |

三条约定：**白名单只有 8 层**（`F/B.SilkS`、`F/B.Fab`、`F/B.CrtYd`、`Dwgs.User`、
`Cmts.User`）——**故意不许写铜层**，文字落到 `F.Cu` 就是真的加了一块铜、DRC 会按铜判，
而调用者通常只想换地方；**`(at x y)` 在封装局部帧、Y 向下，参数是 Y-up，恰好翻转一次**；
**只校验被点名的那一维**（给了 `x` 没给 `y`，`y` 保持不变但标未检查）——校验没人要求
改的数字，通过率就是装饰。

一个都不点名时行为与 0.0.97 相同，由用例**直接断言**（`effects` 子节点仍为 `None`：
没人要的字号不能被凭空造出来）。

### 2. 既有缺陷：改位号名字永远不可能成功

`_verify_set_field` 第一句是 `ed.footprint(ref)`。当这次操作改的正是 **Reference 的值**，
写完之后旧名字已不存在 ⇒ `REFERENCE_NOT_FOUND` ⇒ 整笔回滚。现在只在
「`field == Reference` 且新值非空」这一种情况下改按新名字找，其余
`REFERENCE_NOT_FOUND` 原样抛出。

### 3. 门禁

`tests/test_ops_edit.py::FieldGeometry` **7 例**，其中一条是**反假绿**：真写到 `x = 2.5`
之后拿 `x = 4.0` 去问 `verify` 必须 `ok = False`、再拿 `2.5` 问必须 `True`。
`test_ops_edit` **71 → 78 例**；`test_ops_edit + test_fidelity +
test_board_ops_footprint + test_tools_profile + test_tools` 合计 **160 例 OK**。

详见 [docs/98](docs/98-字段的图层位置与字号.md)。

## [0.0.97] - 2026-11-03

**`board_view` 的焊盘坐标在非零角度的封装上绕自身原点镜像：J2B.6 报 x=-10.16，
KiCad 判它 15.24，差 25.4 mm**（`test_fidelity` 33 例 → 34 例 / 受影响的 74 + 466 例
全绿 / `dependencies = []` 仍成立）

### 1. 一个只错在「旋转不为 0」上的符号

`board_view` 的 `kind="pads"` 报 STM32F412 核心板 `J2B.6 [PB7]` 在 `x = -10.16`，
而 `kicad-cli pcb drc` 的报文里同一个 PTH 焊盘写着 `at (15.24, -26.67)`。

根因不在板、也不在 canonical 层：`_at()` / `_xy()` 已经把文件的 Y（向下）取负成
Y-up 局部偏移，`_rotate()` 又按**文件帧**的约定用 `math.radians(-deg)`。两个翻转
叠加，旋转退化成反射——每个非零角度的封装把自己的焊盘绕封装原点镜像了一次。
`rotation == 0` 时 `_rotate` 直接返回原值，所以错误被完全遮住：那块板 32 个封装里
30 个是 0 度，没有任何用例给过非零角度。

改一行（`math.radians(deg)`）+ 把证据写进 docstring；新增回归用例
`test_a_rotated_footprint_puts_its_pads_where_kicad_does`，用三个角度（90°/45°/90°）
四个坐标对齐 pcbnew 实测值。修后 `U1.1 20.9950/18.9900`、`J2A.3 7.6200/3.8100`、
`J2A.6 15.2400/3.8100`、`J2B.3 7.6200/26.6700`、`J2B.6 15.2400/26.6700`、
`J4.2 50.0000/14.7600`，与 DRC 报文逐位一致。

**影响面**：旋转 0 的封装与不吃焊盘绝对坐标的工具不受影响；拿 `board_view` 焊盘
坐标去和别人比的读数要重算——已知至少 `_scratch/f412rg/warn_probe.py` 的「过孔压
焊盘」匹配（修前只检出 1 处，是错的）。

详见 [docs/97](docs/97-board_view焊盘旋转符号.md)。

## [0.0.96] - 2026-11-03

**会话不是被计算打死的：`tools/list` 316 728 B（≈10.6 万 token）压到 52 021 B**
**一条 JSON-RPC 批量请求能把服务打死，而它抛在任何 handler 之外**
（工具数 **190 → 192** / `--selftest` 仍 **329/329** / `dependencies = []` 仍成立）

### 1. 一个算得对的服务，死在第一次握手

`tools/list` 回 192 个工具、**316 728 B**（按 /3 粗算 **105 576 token**）。聊天客户端
把它放进上下文，窗口在用户说第一句话之前就满了：对话中断、卡死，而服务器日志里一行
错都没有——因为服务器没有错，是**每一轮都要付的租金**太贵。

现在默认答复 **46 个入口工具 / 52 021 B（6.09×）**，其余用 `tool_search`（按任务搜，
返回完整 `inputSchema`）+ `tool_call`（按名调用，信封与直接调用逐字节相同）取用；
`PCB_AGENT_TOOLSET=all` 回到全量，也可写前缀（`board_,magnetics_`）。三条护栏都有
测试：`core` 里的名字必须真的存在（拼错会被静默过滤，能力无声消失）、发现工具在任何
profile 里都在、一个都匹配不上的 profile 回退到 `core` 而不是给一张空表。

`tool_search` 的第一版对中文是**静默假否定**：分词按 `[^0-9a-z_]` 切，`阻抗` 被切得
一个词不剩 ⇒ `ok, 0 matches`，读起来就是「这个服务器不会算阻抗」。改成 50 条任务名词
的查表（阻抗→impedance、铺铜→zone pour、爬电→creepage…），表外的中文词不再回零，
而是明说「没查到，表只覆盖常见名词」+ `search_was_english_only: true`。

### 2. 批量请求把进程打死

JSON-RPC 批量是一个数组，`handle()` 当 dict 用 ⇒ 逐字复现
`AttributeError: 'list' object has no attribute 'get'`。异常抛在**任何 handler 之外**，
打断 `serve()` 的读循环 ⇒ 进程退出 ⇒ 之后所有请求静默无响应。现在：批量按**一行数组**
回答，非对象元素单独 `-32600`（`id: null`）且邻居照跑，通知不产生元素，空数组给一个
`-32600`（不是静默无操作），并且单请求路径也加了 `try/except`——**一个坏请求只能结束
一轮，不能结束服务**。

### 3. `NaN` 不是 JSON

`json.dumps` 默认 `allow_nan=True`，把一个除零结果写成裸 `NaN`，严格客户端整条消息
拒收——同样是「一轮对话死掉」，但更安静。两处序列化点（JSON-RPC 信封、工具答复文本）
原本各写一份 `json.dumps`，现在统一走新文件 `jsonsafe.py`：正常路径 `allow_nan=False`
逐字不变，不可序列化时才 sanitise（非有限浮点→`null`，外来对象→`str`，**不删键**）。

### 4. 门禁

`--selftest` **329/329**；新增 `tests/test_tools_profile.py`（15 例）与
`tests/test_server_protocol.py`（15 例），含三条能失败的反向对照（全量仍 ≥158 KB、
旧版批量异常、profile 名拼错）。`dependencies = []` 仍成立。

## [0.0.95] - 2026-11-02

**铺铜的填充参数写在 KiCad 读它的地方：一块真实板 386 条假 clearance**
**净类规则也终于有写入路径：`board_rules` / `board_rules_write` 改 `.kicad_pro`**
（工具数 **188 → 190** / 写路径 op **38 → 40** / `available` **182 → 184** /
`--selftest` **328 → 329** / pytest **3522 → 3631** / `dependencies = []` 仍成立）

### 1. 386 条 clearance 不在铜上，在文件少了一个字段

STM32F412RG 核心板（199 412 B，两个内层铺铜 GND / In1.Cu 与 +3V3 / In2.Cu）报
**386 条 `clearance` error**，描述的原文都一样：「填充区 间距 0.2500 mm; 实际 0.2005 mm」。
同一时刻我自己的几何 pass 量的是 **0.2505 mm**（polygon 边缘到过孔半径，5780 + 6059 个顶点逐条算），
所以差的那 0.05 mm 不在铜上。

真因是 `(filled_areas_thickness no)`：缺这个字段时 KiCad 把 `filled_polygon` 当成**中心线**，
用 `min_thickness/2` 沿它描边；这块板 `min_thickness = 0.1` ⇒ 每条边外扩 0.05 mm ⇒
`0.2505 − 0.05 = 0.2005`，逐位对上。三档对照（同一份字节、同一个 `.kicad_pro`、同一台 kicad-cli 10.0.7）：

| 字段 | violations | error | 其中 clearance |
|---|---|---|---|
| 缺失（KiCad 缺省当 yes） | 536 | 386 | 200 × 「0.2500 / 0.2005」 |
| `yes` | 536 | 386 | 200 |
| `no` | 150 | **0** | **0** |

补上字段并**重填**之后整块板 DRC：`violations_total` **504 → 150**、`clearance` **386 → 0**，
剩下的全是 warning（via_dangling 2 / silk_edge_clearance 2 / holes_co_located 28 /
hole_to_hole 16 / lib_footprint_issues 32 / text_height 32 / silk_overlap 14 /
silk_over_copper 24）。**警告条数一条没少**——这不是「放宽规则」，是把该报的留住了、不该报的拿掉。

### 2. `set_zone_params`：第 39 个写 op

改的是一个铺铜**意味着什么**，不是一个数字：`filled_areas_thickness`（yes/no，bool 也收）、
`clearance`（写进 `connect_pads`，不是 zone 顶层）、`min_thickness`；字段位置照 KiCad 自己写的顺序
（`connect_pads` → `min_thickness` → `filled_areas_thickness`），这样「本工具写的」与「KiCad 写的」
只差值本身。三条硬规矩：

- **旧填充必须作废**：`filled_polygon` 是旧参数下的铜，留着就是「文件说一套、板说另一套」；
  丢几个如实回 `dropped_filled_polygons`，并回 `refill_required=true` —— 之后要么 `board_fill_zones`，
  要么让 EDA 自己填。
- **keep-out 没有填充参数**：命中的是 keep-out 就 `ARG_INVALID` 并说明，不硬塞 `clearance`。
- **一个参数都不给是拒绝**（`ARG_MISSING`），selector 打空是 `TARGET_NOT_FOUND`；
  参数已经等于要的值时仍会重铺，所以 `zones_changed` 与 `zones_matched` 分开报，
  「0 changed / 2 matched / 2 dropped」是一个真实状态，不是失败。

### 3. DRC 的判决要对着同一套规则读

`fidelity.engine.drc(path, project=...)`；不给时自动去板旁边找同名 `.kicad_pro`，找到就连板带 pro
一起拷进临时目录（**still 不往用户目录写一个字节**），并在信封里说清这次读的是哪套：
`project_file_used` / `rule_source`（`project` 或 `kicad_defaults`）/ `rule_source_note`。

为什么必须说：同一份 199 KB 字节，旁边没有 `.kicad_pro` 时 **665 violations / 515 error**
（drill_out_of_range 158、track_width 199、via_diameter 158，再加 14 条 silk_overlap 从 warning
翻成 error），旁边有 pro 时 **150 / 0 error**。写路径的 DRC 差集里，「改前」那一半是 checkpoint 副本、
住在服务自己的 home 里、旁边没有 pro ⇒ 两半读数不同源，14 条老丝印会被算成 14 条新错误。
现在两半都显式对着 target 旁边的 pro 读（`_project_beside`），checkpoint 副本旁没有 pro 时**如实说**
「这份判决用的是 KiCad 缺省规则」，而不是让它看起来像工程规则。

判据：`tests/test_ops_edit.py::ZoneParams` 7 例（字段落点 / yes 拼写 / 旧填充作废 / clearance 进
`connect_pads` / 空参数 / 选择器打空 / keep-out 拒绝）、`tests/test_engine_drc_project.py` 6 例
（暂存配对、跨目录显式 pro、自动配对、无 pro 要明说、输入不被写、差集用的是哪套），
自检 **328 → 329**（一条串起 propose → apply → 读回文件里的三个字段 + keep-out 拒绝）。

### 4. 一轮实测审计：13 条「会造成错误结论」与「根本做不到」

审计原文的两句判词决定了这一轮的次序：**「如果只补一处，我建议补 `set_zone_params`，并让 `add_zone`
直接照 KiCad 原生头部写全字段——顺带把 `board_fill_zones(in_place=True)` 改成填完自动跑一次 DRC，
把'铺铜不合格'当场报出来，而不是留给用户去发现 386 个 error。」** 以及「其余都是从这两条衍生出来的绕路成本」。
所以先做那两条（§2 与 §6–§7），再做被它们拖出来的七条。

**写出的 zone 就是 KiCad 的 zone（§7）**：`add_zone` 现在写 `connect_pads` 模式词、
`(fill yes [(thermal_gap)] [(thermal_bridge_width)] (island_removal_mode 0))` 和
`(filled_areas_thickness no)`，冒烟验证三档模式词 → pcbnew 读回 `ZONE_CONNECTION` 2 / 1 / 0。

**`connect_pads` 不猜（§6）**：文件写了什么（`connect_pads`，无模式词时 `null`）与 KiCad 读成什么
（`connect_pads_effective` + `connect_pads_source`）分开报。**无模式词 = 热焊盘**（KiCad 自己保存
thermal zone 就不写词），旧解析层把这条读成了 `solid`，两处都反了；`tests/test_thermal_relief.py`
的三条旧错断言随之重写（32 例）。

**填充成功 ≠ 铺铜合格（§8）**：`fill(..., verify_drc=True)` 填完自己跑一次 KiCad DRC，写进
`value.drc_after_fill`；测量模式（不写文件）把 DRC 跑在**临时副本**上，因为原文件那时还是轮廓；
工具层 `drc` 开关默认 true；写路径的 `gates.zone_fill` 只对**新增**错误阻塞
（`drc_errors_before_the_pour` / `drc_errors_added_by_the_pour`）。

**DRC 违规终于带几何量（§9）**：`fidelity/drc_geometry.py`（新文件）从**本地化的描述文本**里读
「要求多少 / 实测多少」，给每条违规补 `geometry{constraint_mm, actual_mm, shortfall_mm, ratio, order, source}`；
没有数字的描述给 `geometry: null` + `geometry_absent_because`，没有 `actual` 词时标
`order: "assumed"` —— 不补 0、不猜。同一个量透到 `drc_policy` 的阻塞条目、写路径的 DRC 差集与
`board_fill_zones` 的 `top_errors`，所以阻塞文案现在是
`clearance at (12.34, -5.67) (wanted 0.25 mm, got 0.2005 mm)` 而不是一个数量。

**同 net/layer 删了再加不是错误（§10）**：`delete_zone` 的后置条件从「selector 不再匹配」改成
**按 uuid 判**（它删的是一批具体对象），同一次 apply 里删旧 zone + 加新 zone 现在通过；`propose`
仍然把这类组合先说出来（`plan_conflicts: delete_then_add_same_zone` + `what_to_do`），
后置条件失败时带 `failed_detail`。

**删除是一批对象（§10）**：新 op `delete_copper`（第 40 个写 op）按 `net` / `layer` / `region`
（mm，Y 向上）批量删 `tracks` / `vias` / `zones`，不必再 ripup 出 528 个 op；zone 只有**整个 outline
都在 region 内**才删，跨界的进 `zones_skipped_partial`，`locked` 一律不删。

**`dry_run`（§11）**：走同一条写入 + 全部门禁 + 还原字节的路径，被拦时返回
`status: ok, dry_run: true, would_write: false` + 原样 `blocking`，而不是抛异常堆栈；不消耗 plan。

**回包自己说清形态（§12）**：`payload_is`（`value` / `top_level`）+ `where_to_read`，一个业务字段都没动。

**两条读错的读数被纠正**：`board_pour_connectivity` 标 `authoritative: false` + `use_instead`，
并默认连带跑一次 DRC（`value.drc_cross_check`）；`rule_bundle` 之外加 `rules_in_effect`
（`per_net_summary` 按实际生效的 (clearance, track, via, drill, source) 分组），
并明说 `clearance_mm` / `track_mm` / `via_mm` 是**板级缺省**。

**三套异常接口对齐（§15）**：`OpError` / `EditError` 补 `.message` / `.reason_code` / `.hint`（`.code` 保留）。

判据：`tests/test_drc_geometry.py`（新文件，19 例：三条真实描述样本的 `parse`、无数字给 `null` + 原因、
`assumed` 标注、`summarise` 按差量排序、`payload_is` 四例、`board_zones` / `board_propose` 走
`call_tool` 两例、`pour_connectivity` 的非权威标注）、`tests/test_zone_fill.py` 14 → **17 例**
（填完的 `drc_after_fill`、测量模式测的是副本、`verify_drc=false` 就什么都不说）、
`tests/test_edit.py` 加一条位置断言（它同时抓到阻塞文案里的一个真 bug：`drc_policy` 写 `pos` 是
`[x, y]`、KiCad 的 item 写 `{x, y}`，旧文案按 dict 读 ⇒ 阻塞路径自己抛 `TypeError`，
错误码变成 `APPLY_FAILED` 而不是 `GATE_BLOCKED`）。

### 5. 净类规则写进 `.kicad_pro`：两个新工具，五道闸门（§16）

`set_netclass`（写 op）改的是**板文件里**的 `(net_class ...)`，而 KiCad 7+ 从**工程文件**读净类、
板里那份根本不看——于是「改成功了」与「布线与 DRC 仍按 Default」可以同时成立。这是审计里
「根本做不到」那档的第一条，本轮补上：

- **`board_rules`**（读）：工程声明了哪些类、每个类多少网、一个网归属哪个类、用的是
  `netclass_assignments`（KiCad 7+）还是 `class.nets`（v6）形态、pattern 几条，以及
  **能不能写**（`writable` / `style_roundtrip` / `why_not_writable`）。
- **`board_rules_write`**（写）：`set_netclass` / `remove_netclass` / `assign_nets` /
  `unassign_nets`，写前落 checkpoint、原子写、`dry_run` 只跑闸门不落盘。

**文件形态是量出来的**：KiCad 10.0.7 写工程是 2 空格缩进 + **键按字母排序** + **非 ASCII 转义**
+ 末尾换行。真实 16 523 字节的工程文件用 `json.dumps(indent=2, sort_keys=True, ensure_ascii=True)+"\n"`
**逐字节往返相同**，而 `ensure_ascii=False` 差 1 行（正好 `sort_field` 那行）。

**往返不上就拒绝写**（`PRO_FORMAT_UNKNOWN`）：为了改一个 clearance 去重排、重转义 900 行手写 JSON
是没人能 review 的 diff，而 `SaveBoard` 那一课已经说明 KiCad 会拒绝「看起来对」的文件。

五道闸门：`format_roundtrip`（写前，未改动文本能自己串回自己）、`diff_is_intended`（写前，只有
操作点名的键变了，差分按**类名**键而不是数组下标 ⇒ 新增一个类不会被读成「后面每个类都变了」）、
`bytes_written`、`json_roundtrip`、`readback`（后三道在写后，其中读回用
`adapters.kicad_pcb.load_project_rules`——**DRC 用的同一个读法**）。任一写后闸门不过就写回原字节
（`restored: true`）并报 `GATE_BLOCKED`，措辞写明「这是本工具的缺陷，不是你的工程有问题」。

拒绝面：删 `Default`（`CANNOT_REMOVE_DEFAULT`）、类还被网或 pattern 引用（`CLASS_STILL_IN_USE` /
`PATTERN_REFERENCES_CLASS`，`reassign_to` 才放行）、拼错的旋钮（`UNKNOWN_KNOB`，不被静默忽略）、
非数字（`KNOB_NOT_A_NUMBER`）、`assign_nets` 到不存在的类（`NO_SUCH_CLASS`，**不顺手建类**）。
新类是 `Default` **整条记录**的拷贝再覆盖调用方给的旋钮，回包写 `shape_copied_from` + `inherited`。

**改完 KiCad 真的按新规则判**（参考工程，`HV 0.5/0.3` + GND/+3V3 归 HV + 删 Fanout 并把 52 个网归 Default）：
`kicad-cli pcb drc` 改前 **118 violations / 0 unconnected**、改后 **126 / 0**，+8 正是抬到 0.5 mm
之后多出来的。文件能被 KiCad 打开、DRC 正常跑、数字按预期变化——「写进去了」与「生效了」的区别。

判据：`tests/test_pro_rules.py`（新文件，**22 例**：字面量形式的工程文件必须先通过 `dump()` 证明
这就是 KiCad 的形态、键序被打乱的工程被拒且**一个字节不动**、读回的类/归属/形态、新建类继承
Default 的**全部键**且 `inherited` 说得对、五道闸门逐个 true 且 `changed_keys` 全在
`/net_settings/` 下、一个网两类的字符串↔数组往返、`load_project_rules` 读到新 clearance、
无改动的操作**不写**、`dry_run` 的 `measured_on` 指副本且临时文件被清掉、`Default` 删除被拒、
在用类要 `reassign_to`、pattern 挡删除、未知旋钮/非数字/未知 op、走 `call_tool` 的
`payload_is: top_level` 与错误回包的 `hint`）。

### 6. 设计取舍写进文档，不改代码（§17）

审计里剩下的部分是边界，不是缺陷，所以写进 [docs/95](docs/95-铺铜填充参数与DRC规则来源.md) §17：

- **`PLANS` 是进程内的** ⇒ propose 与 apply 必须同一个进程；计划一次性、TTL 1 小时、上限 32，
  同一时刻**一个**计划在飞（两个计划同时写一块板，第二个的差集是在第一个的中间态上量的）。
- **六批布线的 DRC 占大头**：每批写完都要问一次「有没有新违规」，而 `kicad-cli` 只能整板跑一次、
  没有增量接口 ⇒ 六批 = 六次整板 DRC。省它的办法是调用方决定批次粒度（`options.step=N` 的步进写
  每批都是一个完整可打开的板），不是少跑 DRC。
- **`pcbnew.LoadBoard` 带 `filled_polygon` 挂到 300 s 量级**（pcbnew 10 实测）⇒ 填充的成败判断
  不靠 `LoadBoard`，`board_fill_zones` 用 kicad-cli 的产物与文件字节判断，只有需要 `ZONE_FILLER`
  时才进 pcbnew 且对**副本**做。
- **`tools._load_board({...})` 能直调，`tools.board_xxx()` 不能**：`board_*` 是 `TOOLS` 表里的条目，
  从 Python 里走 `tools.call_tool("board_zones", {...})`（回包与 MCP 完全一致）；不存在的同名函数
  故意不补，否则同一件事有两个名字、两种回包形态。

### 7. 版本数字

工具数 **188 → 190**（`board_rules` / `board_rules_write`）/ 写 op **38 → 40**
（`set_zone_params` → 39、`delete_copper` → 40）/ `available` **182 → 184** /
`--selftest` **329** 条（本轮不新增编号；本机 326/329 —— 3 条失败是随包 ngspice 找不到 `spinit`
的环境问题，把工作区 stash 回 0.0.94 后同样失败，与本次改动无关）/
`pytest` 实测 **3631 收集 / 3618 passed / 8 failed / 5 skipped，1328.02 s（22 分 08 秒）**（同样 8 条 ngspice 失败在干净 HEAD 上逐条复现）/ `dependencies = []` 仍成立。

## [0.0.94] - 2026-11-02

**跑台账认得自己的主人：一条 run 记下它是哪个后台作业做的**
（工具数 **188**（不变）/ `available` **182**（不变）/ `not_implemented_yet` **23**（不变）/
`--selftest` **328/328** / `dependencies = []` 仍成立）

`board_run_ledger` 原来只能按板文件 sha256 与 kind 过滤。板子试过没有、当时说了什么，这两问能答；
「刚才那个 `board_job` 到底做了哪些 run」答不了——`board_job` 交回来的 id 到了台账里就断了。
台账因此多两样东西：

- 每行多一个 `owner`：work 在后台作业里跑时写那个作业的 id（`jobs.current()` 由 worker 线程
  自己带着，不由每个调用点手传；不在作业里就是 `null`，**不借最近的 id**）；
- `board_run_ledger(owner=...)` 只列该作业的 run，并在信封里回显 `owner` 与 `owner_note`。
  `owner=""` 是 `ARG_INVALID` 而不是「不过滤」——空串会静默返回所有 `owner` 为 null 的 run，
  正好是提问的反面。

判据（`tests/test_ledger.py` 新增 `TestWhoRanIt` 5 例 + 自检一条）：作业里的 run 记的就是那个 id、
**另一个没人用过的 id 必须返回 0 而不是退回全量**、线程结束后 `jobs.current()` 又变回 `None`。
自检 **327 → 328**。

## [0.0.93] - 2026-11-02

**经验累积层：两条链、七条随包发出的实测条目、七个工具**
（工具数 **181 → 188** / `calc_capabilities.available` **175 → 182** /
`not_implemented_yet` **23**（不变）/ `--selftest` **327/327** / `dependencies = []` 仍成立）

`docs/02 §B` 的「经验累积」此前只有名字没有实现，[docs/94](docs/94-经验累积模块与语料.md) 是它的正文。

### 1. 两条链，一条红线

`hard`（有失败侧、板上可测的规则，违反＝缺陷）与 `soft`（好板子的习惯，违背＝问一句）分开判；
`violated` 只留给 hard，soft 用 `outside_habit`；`pad_placement_verified` 问的是「读者能不能担保输入」，
单列 `verified`/`unverified`，不进缺陷列表；量不出来是 `not_checkable_here`，**不是** pass。
**候选永不自己变成规则**：`promote` 要 `confirm=true` + `reason`；`candidate`/`promote`/`outcome`/`import`
四个写入口一致拒绝 `exp.seed.*`，否则一个候选就能影子掉一条在役硬规则。

### 2. 种子：6 个真实工程、20 块板、16 522 段铜、12 805 个 via

`data/experience_seed.json`（新文件，只读）7 条 active：
走线 45° 栅格 16 336 成立 / 186 不成立（1.1258 %，最差一块 144 段）、
via 环宽 19 / 0、via 几何单一 15 / 4、电源线更宽 18 / 1、线宽 ≤12 种 19 / 1、
通孔跨层 235 / 0、焊盘位置可担保 0 / 6。
**数字由 `experience.observe()` 自己产出**（`corpus.measured_with` 写明），生成脚本不入库——
随包数字与运行时数字不可能出自两套公式。

### 3. 两个被真实板子撞出来的陷阱

- `(0.5−0.3)/2 = 0.09999999999999998 < 0.1` ⇒ 一块真实出货板上 **1497 个 via 被判环宽过薄**；
  加 `RING_EPS_MM = 1e-6`。
- 同一个 24 mil via 一个文件写 `0.6096 mm`、另一个写 `0.61 mm` ⇒ `round(x, 4)` 把 **5 块好板判成两种几何**；
  分组改 `round(x, 3)`，回到 15 / 19。

### 4. 七个工具与开关

`experience_recall` / `experience_check` / `experience_candidate` / `experience_promote` /
`experience_outcome` / `experience_export` / `experience_import`；本地库 `PCB_AGENT_HOME/experience.json`。
`PCB_AGENT_EXPERIENCE=0` 时给 `DATA_UNAVAILABLE` 而**不是空列表**（「关掉了」与「库里是空的」是两个答案）。
默认开着且不改任何既有答案，靠的是没有任何既有工具调用它。

### 5. 判据

`tests/test_experience.py` **24 例**（种子的度量必须是模块认识的那七个；soft 永不说 violated；
0.1 环不算 below；同一 via 两种单位算一个；候选不进 active；导入不是提升；关闭 = `DATA_UNAVAILABLE`）。
`--selftest` 新增 9104–9112 共 9 个编号，**327/327**。

## [0.0.92] - 2026-11-02

**在真实板上量出来的两处错读数：通孔焊盘跨层 + 底面不镜像；顺带一个 `pad_trust` 闸门**
（工具数 **181**（不变）/ `calc_capabilities.available` **175**（不变）/
`not_implemented_yet` **23**（不变）/ `--selftest` **324/324** / `dependencies = []` 仍成立）

0.0.91 发布后拿用户自己的 6 个立创工程（20 块成品板、16 522 段铜）当语料跑了一遍，
`board_dangling_copper` 在一块 6 层开发板上报出 **342 tail / 170 浮铜 / 310 可切**。
逐条核对自由端与焊盘的关系之后，真因不在悬空铜判据，而在**焊盘**：

### 1. 通孔焊盘不是单面焊盘（读者错）

`.epcb` 里焊盘自己的 `PAD[4]` 是**层码**，`12` = 多层；实测 6 个工程 4724 个焊盘：
`('1', 无孔) 4308 / ('2', 无孔) 3 / ('12', 有孔) 413`，**「12」与「有孔」严格互为充要、零例外**。
旧代码忽略这个字段，改用**元件所在层**，于是从反面走到通孔焊盘的走线被判成「没接到」：
全语料 235 个通孔焊盘 100% 单层、64 个 `BOTTOM` 走线端点精确落在通孔焊盘中心。
修法：`epro._pad_layers()` 给 `["*.Cu"]`（`dangling` 与 `fanout`/`router`/`oracle` 早就会读这个通配），
另存 `layers_as_written` 保留旧值以便追溯。修后该板 **342→338 tail、170→99 浮铜**。

### 2. 底面元件不镜像（旧数字错）

旧表写「底面镜像 `y`，35.1% 落点」——量的是**错的假设**。按同网铜端点重测 2549 个带网焊盘
（容差 0.05 mm）：**不镜像 62.1% 顶面 / 82.6% 底面**，`-y` 37.0%/53.2%，`-x` 3.0%/6.0%，
两者 1.7%/3.5%；异网误撞 1/1/0/0 与 3/2/0/0 ⇒ 次序不是容差放宽的产物。
顺带把 `place()` 里那处「先取负、再在闭包里取负」的写法改成直算（行为不变，读得懂）；
底面自定义多边形焊盘也不再翻转（中心不翻、形状翻会自相矛盾）。
命中率是**下界**不是精度（未布线的焊盘、只靠铺铜喂的焊盘没有端点可比对），
所以 `pad_transform.verified_by_app` 仍是 `false`。

### 3. 因此工具要先问「焊盘能不能信」

`dangling.pad_trust(board)` = 两条事实的合取：焊盘几何确实读到（`present["pads"]["present"]`）
**且** 位置要么构造上精确（KiCad）要么经应用核验（`source["pad_transform"]["verified_by_app"] is not False`）。
任一条不成立 ⇒ 每条 tail 与每片浮铜都带 `cuttable: false` + `why_not: "pad_placement_untrusted"`，
`removal_ops` 返回空，工具信封 `pad_trust` 说明是哪一条、为什么，`message` 直说「只列候选、不切」。
**立创板上 `trusted` 恒 false**：拿 62.1% 的推导位置去判「这段铜接不到任何东西」，
等于把「我放错了焊盘」说成「你的铜是悬空的」。KiCad 板与所有既有单测行为不变。

### 判据与负控制（`tests/test_dangling.py` 22 → 28 例）

| 类 | 用例要点 | 负控制 |
|---|---|---|
| `PadTrust` | KiCad 板算可信；同一块板 `verified_by_app=false` 时 `cuttable=0` / `why_not` 命名 / 两种 `removal_ops` 都空；`present["pads"]=False` 是**另一个** reason | 可信时那条 tail 仍可切（`cuttable=1`、`ops=1`）⇒ 闸门不是「一律拒绝」 |
| `ThroughHoleSpansEveryLayer` | 层码 1/2/12 的层规则；有孔即多层（层码是第二证人不是唯一证人）；B.Cu 走线接在顶面通孔焊盘上时 tail/floating 都是 0 | 把焊盘改回单层，同一段铜立刻变成 1 片浮铜 |

实测：`test_dangling` **28/28**（28.4 s）；`test_epro` 26 例、`test_epro_edit` 49 例、
`test_lceda_records` 19 例全绿；`--selftest` **324/324**。


## [0.0.91] - 2026-11-02

**悬空铜能查能切：`board_dangling_copper`（FreeRouting 算法层第二轮）**
（`docs/92` 的落地部分；工具数 **181**（新增 1）/ `calc_capabilities.available` **175** /
`not_implemented_yet` **23** / `--selftest` **324/324** / `dependencies = []` 仍成立）

### 它是什么

FreeRouting 在写出板子之前会把布线阶段留下的悬空铜清掉（`DanglingCopper.java`，85 行）：
**一个 via 只在一层上有本网的铜**、**一条 trace 有自由端**，把每个尾巴背后一路删到下一个
**fork / pin / fixed item**，最多 16 轮（删掉 stub 可能让后面的 via 只剩一层铜，所以是串行收敛）；
**用户放置的铜是 fixed，保留**；删完开路连接数上升就整轮 undo。

我们抄的是机制，不是代码（GPL-3.0，只读）。落成 `src/pcb_agent_mcp/dangling.py`（607 行），
与他们的两处**有意不同**都写在 docstring 里：切除单位是**整节点**（`segment`/`arc`/`via`），
不是 track 中段；不需要 16 轮，因为我们的链一次走到头。

### 三条属于我们自己的读数

1. **「attached」是几何断言**：点落在另一段的宽度内 / via 的尺寸内 / pad 的 reach 内 / 同网
   `filled_polygon` 内才算附着；只是靠近不算，间隙就是间隙（`TOL_MM = 0.002`，可改）。
2. **「cuttable」是证明不是希望**：链只有一个附着点，且不是任何 pad 的唯一铜 ⇒ 切它
   **不可能**开连接；否则报 `why_not`（`locked` / `would_open_a_pad`）。
3. **zone 尊重而非忽略**：同网 `filled_polygon` 才算铜，keepout 不是铜，没 fill 的板子
   **如实说「不猜」**。

### 写路径

* 新 op **`delete_via`**（悬空铜要删的第二类节点原来删不掉），错误码 `ARG_MISSING` /
  `TARGET_NOT_FOUND`，走 `_refuse_fixed`（`allow_fixed=true` 才动 locked 的 via）；
* `delete_track` 加 **`include_arcs`**（默认 **false** ⇒ 既有答案逐位不变），弧也是铜。

### 工具行为

`board_dangling_copper(path, apply, include_floating, max_report, tolerance_mm, …)`：
默认**只报告**（`writes_nothing: True`）；**没有可切的就绝不造空计划**（`message` 是
`"no dangling copper"` 或 `"nothing on this board is both dangling and safe to cut"`，
`apply=true` 在干净板上返回的是拒绝编造）；**浮铜默认不切**（`include_floating=true` 才切，
「有人故意放的测试点」不是这个工具能替人判的缺陷）；`apply=true` 走 propose → apply_plan，
**写后再 survey 一次**放进 `value["after"]`。

### 顺手修掉的一处读写分歧

`adapters/kicad_pcb.py::_uuid` 原来只认 `(uuid "<x>")`（KiCad 8 起），而 KiCad 7 写的是裸 atom
`(tstamp <x>)`；`edit/build.py::id_of` 早就两种都读。**读得到的不一定能删**就是这种分歧，这版两个都读。

### 本轮实测

`tests/test_dangling.py` **22 个用例全绿**（含负控制：活 via 杀掉尾巴后不再报；同一块板改
`tolerance_mm` 就改答案；干净板必须报 0；locked via 拒绝且不落盘）；相关子集
`test_edit` + `test_ops_edit` + `test_route_fixed` **110 例全绿**、
`test_kicad_pcb` + `test_fidelity` + `test_tools` + `test_route` **149 例全绿**；
`--selftest` **324/324**（新增三条 (9101)/(9102)/(9103)）；`calc_capabilities` 实测
`available 175` / `not_implemented_yet 23` / `len(TOOLS) 181`。
全量回归（3493 例）按既定节奏留到功能收口时集中跑。

### 只记账、未实施

`LastMileBlockerRipup.java`（93 行）：只剩少数未完成连接时撕掉落在剩余 airline 走廊内的外来铜，
`MAX_ITEMS = 24`、走廊 `margin = max(span/4, 1)`、只撕 `Trace`/`Via` 且非 fixed、非本网、
`netCount() > 0`。我们的 `_negotiate()` 已是它的更强形态（带价格、按 wish 走廊、整轮重布线），
增量价值等悬空铜跑过真板再评估。

### 一条反面事实

`autoroute/BoardUpdateStrategy.java` 整个文件 6 行，枚举**只有一个值** `GLOBAL_OPTIMAL`——
「策略」等于没有策略。我们加任何 `mode`/`strategy` 类参数时，要么两个值都有可观测的行为差，
要么就别暴露它。

### 不动的东西

既有工具、既有 op 默认行为、既有信封字段一个都没变：不传 `include_floating` / `include_arcs` /
`tolerance_mm` 时，旧的删除语义与旧的悬空判定都不受影响。

## [0.0.90] - 2026-11-02

**A1「锁定的铜不回流」：文件 `(locked)` 与调用方 `fixed_nets` 两条入口**
（`docs/91 §91.4` 里判为「最值钱」的那条缺口，现在有了运行代码；工具数**仍 180**
（只加参数、不新增工具）、`calc_capabilities.available` **174**、`not_implemented_yet` **23**、
`--selftest` **321/321**、`dependencies = []` 仍成立）

### 它是什么

Specctra 用 `(type fix)` 声明「这块铜不许动」，并且用一条自己的规则兜住：会话文件里
**从不返回**固定铜。我们没有会话文件，但同样的诉求天天出现——「这段我手工调好了，你别碰」。
所以这里抄的是**机制**，不是格式：

* **文件说**：`(segment|arc|via … (locked))`（KiCad 自己的写法，逐字节读出来，等价 `(type fix)`）；
* **调用方说**：`board_route(fixed_nets="GND,VCC")`。

两条入口合成一个集合，语义三条：

1. **不布**——即使它的 pad 看起来没连上也不布：布它就得动铜，而铜已经声明不许动；
2. **不撕**——rip-up、拥塞协商、二遍 sweep 的受害者条件里都没有它；
3. **不耦合、不等长**——这两类 pass 会**重写**铜（耦合把第二条网重建成第一条的偏移副本，
   蛇形重建一段直跑），所以固定网出现在 `diff_pairs` / `length_groups` 里是
   `ARG_CONFLICT`（**拒绝**，不是静默剔掉：剔掉之后那一对/一组仍会以「对/组」的名义报出来，
   而其中一条网从来没被允许动过）。

还有一条是诚实的那半：**声明固定但板上一根铜都没有的网**报 `fixed_missing_copper`，
因为「放着不动、然后管它叫固定」是唯一会把开路网报成已了结的结局。

### 写路径

`delete_track` / `set_track_width` 之前对锁定铜是照删照改。现在先收集命中的段，
命中里只要有一条 `(locked)` 就 `ARG_CONFLICT` 并且**在第一次写之前**拒绝
（错误文本点名是哪些 tstamp、并告诉你下一步：`allow_fixed=true`）。开关在，
所以「守卫不存在」和「守卫拔不掉」是两种不同的缺陷，这里只主张前者已被修掉。

### 两条能失败的负控制

* **旗标必须改变答案，而不只是多一个字段**：同一块板、同一个调用，去掉 `fixed_nets`
  GND 就会被布（`status: routed`、有 op）；加上就一个 op 都不许提它。
* **无铜不是「已固定」**：`fixed_missing_copper` 的用例末尾用同一块板不带旗标再布一次，
  SIG 必须变回 `routed`——否则那条判断在「什么都不布」的布线上也会通过。

### 本轮实测

`tests/test_route_fixed.py` **15 个用例全绿**（两条入口 + 写路径 6 例 + 布线器 7 例）；
相关回归 243 例（`test_route` / `test_edit` / `test_ops_edit` / `test_fidelity` /
`test_tools` / `test_board_ops_footprint`）全绿；`--selftest` **321/321**；
`calc_capabilities` 实测 `available 174` / `not_implemented_yet 23` / `len(TOOLS) 180`。
全量回归（3493 例）按既定节奏留到功能收口时集中跑。

### 顺手钉死的一处口径

`fixed["copper"]` 只描述**板上确实存在的网**：一个板子从未听说过的名字（`NOPE`）不给
六个零的记录——那看起来像「查了，没有」，而真相是「没有可查的东西」，它只在
`unknown` 里出现。有 pad 无铜的网（本文中的 SIG）仍然给记录，那才是 `fixed_missing_copper`。

### 不动的东西

写路径的其余 op、布线器默认行为、`board_route` 的既有字段一个都没变：不传
`fixed_nets`、板里没有 `(locked)` 时，逐位等于 `0.0.89` 的答案。

## [0.0.89] - 2026-11-02

**Specctra（DSN/SES）评估：判决「不接交换」，只借五条机制**
（**只加文档**：新增 `docs/91-Specctra评估-DSN与SES交换格式.md`（174 行 / 10 059 字符 / 17 339 字节）；
运行代码**零改动** ⇒ 工具数**仍 180**、`calc_capabilities.available` **174**、`not_implemented_yet` **23**、
`--selftest` **321/321**、用例 **3493** 全部不变；`dependencies = []` 仍成立）

### 问的是什么

「用一个行业标准的板级交换格式（Specctra DSN/SES）能不能帮到我们 PCB 绘制这一层」——
具体是布线器、写路径、规则来源这三处。查完的答案是：**格式不接，机制借五条**。

### 怎么查的（一手文件，不是博客综述）

一手规范**没拿到**：Cadence 的 *SPECCTRA Design Language Reference* 在 KiCad 注释里指向早年
Yahoo group 的 `specctra.pdf`，现在取不到正文 ⇒ 文档里凡只有一处实现印证的结论**都标了「单侧」**，
没有一句是「规范说」。实际取证是四个来源：

- **KiCad**（GPL-3.0）`pcbnew/specctra_import_export/`：`specctra.cpp` 4051 行、`specctra.h` 4024 行、
  `specctra_export.cpp` 1878 行、`specctra_import.cpp` 574 行、`specctra.keywords` **388 个非注释 token（全唯一）**。
- **FreeRouting**（GPL-3.0，SPDX 已核）：`SesWriter.java`、`DsnWriter.java`、`RulesReader.java`、`RulesWriter.java`。
- 一个真实 DSN（LibrePCB 2.0.0 导出）：**541 行**、2 层、9 网、**48 段铜**、6 个过孔、7 个 padstack、9 个 image。
- 一个真实 SES（KiCad 5.1.5 写）：**701 行 / 14 421 字节**、12 网、**93 段铜**、10 个过孔。

### 判决：不接交换，三条理由

1. **判据错位**：我们的价值是「精确几何 + 外部裁判（`kicad-cli` DRC / 立创自己）」。收外部布线器的候选，
   要付整套 IO 保真责任（往返不丢东西、锁定的铜不许回流、规则映射可解释），换来的只是「多一个候选」。
2. **零依赖红线**：FreeRouting 是 Java 程序、KiCad 的布线器长在 PCB editor 里，两者都不能当 runtime 依赖。
3. **许可证**：两个参考实现都是 **GPL-3.0**，本仓库是 MIT ⇒ **一行都不能抄**，只能读机制、自己写。

### 借五条（A1–A5），其中两条我们**确实缺**，本版**记账未实施**

| | 机制 | 我们现在 |
|---|---|---|
| A1 | `(type fix)` 的铜**不回流**：导入侧先删所有未锁定 track/via，再加会话内容 | `route/router.py` 有 `incremental`/`_existing_tree`，但**没有「这块铜不许动」的一等公民** ⇒ 记账未实施 |
| A2 | 合并 = **替换未锁定**，不是 diff；每段铜有 owner 归属 | `ledger.py` 没有 owner 一栏 ⇒ 记账未实施 |
| A3 | 规则优先级（`pcb < layer < class < … < net region`）的「**特异性**」与我们的「**最严者胜**」是**两条正交的轴** | 不改语义，只改文档表述：DRC 问「不许违反哪条」，布线问「用户想多宽」；`class_class` 与爬电的「两网较严者」是可对照的等价关系 |
| A4 | 文件**自声明词法**（`(parser (string_quote ") (space_in_quoted_tokens on))`） | 我们的保真层已在树里带口径，但没有「文件自己说怎么读」的显式段 ⇒ 只作表述参考 |
| A5 | **量子**（`(resolution um 10)` = 100 nm）与**打印精度**（`%.6g` 浮点）分开登记 | 我们的 `precision` 已分开，但没把「输入量子」写进信封 ⇒ 只作表述参考 |

### 不抄五条

- **QARC/弧**：FreeRouting 自己不支持弧（源码注释两处），KiCad 把圆/弧**一律折线化** ⇒ 抄了等于抄一个降级。
- **逐项 try/catch + `m_skippedCount`**：KiCad 导入是「能读的读、读不了的跳过、末尾报一次 warning」；
  我们 fail-closed，正确形态是「**条数上限 + 全量清单落盘**」，不是静默跳过。
- **让 session 的 polygon 接管铺铜**：KiCad 自己也忽略（板上已有 zone），我们 `refill_pours` 委派 ZONE_FILLER，同侧。
- **388 个 keyword 当能力清单**：两个成熟实现**共同只有** `width` / `clearance` / `use_via` 三样。
- **GPL 代码**。

### 顺带坐实的两件事

- **KiCad 的 `.kicad_pcb` 语法本来就是 DSN 方言**：官方 dev-docs 原句
  「Syntax is based on the Specctra DSN file format.」（已 curl 核实原文）。
- **DSN/SES 传的是「几何与宽度」，不是电气意图**：KiCad 的 `exportNETCLASS()` 一个网类只写
  `(width)` / `(clearance)` / `(use_via)`；FreeRouting 的 `RulesReader` 只认 `WidthRule` / `ClearanceRule` + via infos
  ⇒ 我们最值钱的那层（差分对真耦合、等长蛇形、阻抗、爬电、回流缝合）在 DSN 里**没有承载**。
  另有一条独立收敛：DSN 的 `(grid (via_grid …) (wire_grid …))` 两个栅格与我们 `docs/27` 的
  「两套栅格不能合并」是同一个结论。

### 同时做了的：装 `rea`（结论是**这一步没用到它**）

- 包名 `rea-agents`（npm），装到 **6.8.0**。首次不带 `--ignore-scripts` 的安装在 postinstall 挂住 >7 min
  （日志只停在 150 字节的 `prebuild-install` deprecation），终止后改用
  `npm i -g rea-agents@6.8.0 --ignore-scripts --no-audit --no-fund`（27 s）成功。
- `rea doctor` 实测：`healthy: false`，本机 `node 24.17.0` 被判 `missing_dependency`
  （要求 `^22.23.4 || ^24.22.1 || >=26.12.1`）；**五个分析引擎全缺**（hopper / ghidra / ida / jadx / kuna），
  REA 不下载也不安装它们；4 个客户端注册全 missing。能力计数 `cli_commands 153 / mcp_tools 207 / mcp_prompts 6`。
- **不升级宿主的 node**（那是 Agent 运行时，属越界）；本步也没有用到它——Specctra 是**纯文本 S 表达式**，
  不需要反汇编 ⇒ 如实写进 `docs/91` §10，并写清「若将来真做二进制取证」要先满足什么。

### 不动的东西（自证）

- 运行代码、`data/*.json`、`tests/` **零改动**；`dependencies = []` 仍成立。
- 工具数 **180** / `available` **174** / `not_implemented_yet` **23** / `--selftest` **321/321** / 用例 **3493** 不变。
- **本版没有跑全量回归**（按既定口径：功能全部做完后集中跑一次）。

---

## [0.0.88] - 2026-11-02

**沿长度这一路的熔化潜热：`fusing_current_taper` 从「到 1083 °C」变成「熔穿」**
（工具数**仍 180**，本版只加参数、不新增工具；`dependencies = []` 仍成立；
默认 `melting_model="sensible"` ⇒ 默认信封**逐位不变**）

### 为什么这一路不能乘一个 `1+β`

`fusing_current`（0.0.86）与 `repetitive_pulse_rating`（0.0.87）都是**一个**温度，
所以潜热在那里是一个标量（`I²t` 乘 `1+β`）或一个闭式储层。
`fusing_current_taper` 是**沿长度的一维 PDE**：每段一个等温节点，`ρ(T)` 在 Thomas 三对角解里，
导热又会让 onset 与平台**不成比例**地变化——乘一个数等于把机制抹掉。

所以每个节点带一个焓状态

```
E_i = C_i (T_i − Tm) + phi_i L_i,  C_i = ρ_m c_p A_i l_i,  L_i = ρ_m L_f A_i l_i
```

`0 < phi < 1` 的节点**温度钉在 `melt_c`**、未知量是 `phi`。平台因此是**精确相界面**：
没有糊状温度带，没有宽度参数，**没有新的可调参数**——唯一的旋钮是步长，而步长被 `time_resolution_s` 报出来。

### 一个要害（这一版真踩过）

`phi` 项在**非相变节点上相消**（`E^{n+1} = E^n + dt·Q ⇒ C·T' = C·T + Q`）。
第一版 (`a`) 在 `rhs` 里多写了 `phi_old·L/dt`：等于把 0.325 J 的储层当源项灌进解，
温度恰好多出 `L/C = 542 K`，`L = 0` 也对不上 `_taper_run`；
再加上「只有上一刻已在平台上的节点才钉住」让活跃集在 `[1] ↔ [0]` 振荡（迭代打满 80），
温度爆到 3.7e6 °C。修法是：投影**无条件**（`E → T` 可逆）、`rhs` 与 `_taper_run` 逐字相同、
停止点对齐（在事件上停、状态留在最后一个**完成**步）。

### 两个时间，两个问题

| 键 | 含义 |
|---|---|
| `t_onset_s` | 某节点**首次到达** `melt_c`，且**逐位等于** sensible 档的 `value`（跨模型自检） |
| `value` / `t_melt_s` | 某节点**完全液化**（latent 档才有意义） |
| `melt_delay_pct` | `100·(t_full − t_onset)/t_onset` |

### 锚（都不是本模块自己的输出）

- `latent_j_kg = 0` 与 `_taper_run` **逐位相同**（单节点/多节点、`k = 0/400`、`ends_c` 有无）。这不是
  `if latent: return _taper_run(...)` 那种假控制——储层全零走**同一套算术**。
- 绝热单节点：`t_full/t_onset → 1+β = 1.253093`（2000 步 1.253527、20000 步 1.253202、200000 步 **1.253095**）。
- `_taper_latent_share(25)` 与 `latent_heat_share_of_i2t(25)` 在 1e-15 内相同 ⇒ **β 是两个模型之间的锚，不是新常量**。
- 能量守恒 `energy_balance_rel`：6.24e-4 → 8.75e-5 → **1.39e-5**（同上三档）。
- 二分下界在 latent 档除以 `sqrt(1+β)`（否则起点「已经熔穿」，逻辑反了）。

### 自我纠正：`melt_delay_note` 里那句话被自己的实测打掉

我最初写的是「延迟是绝热 `latent_share_of_i2t` 的**下界**、随电流升高逼近它」。
实测（dt = 2e-4 s）：

| 构型 | 延迟 % |
|---|---|
| 均匀 10 mm × 0.5 mm，端点 25 °C，8 A | **76.855** |
| 同构型 10 A / 20 A / 40 A | 35.956 / 26.911 / 25.696 |
| 1.0/0.5/1.0 mm 颈缩，端点绝热，4 A / 50 A | 8.458 / 12.510 |

同一个 β = 25.31 %，导热占比大的构型**远高于**它、占比小的构型**远低于**它。
`melt_delay_note` 已按这些可复现的数改写，测试里钉死 10 A / 35.956 % 一格。

### 步长是唯一分辨率

停止点与全熔时刻都在一个步内插值，所以延迟只锐到 `dt`。1.0/0.5/1.0 mm、I = 20 A（`t_end = 10`）：
`dt = 5e-3` 时 sensible 0.181339 / latent 0.2000 / 延迟 10.291 %；
`dt = 1e-4` 时 0.185334 / 0.2020 / 8.993 %。

### 契约与计数

- 工具数 **180 不变**；`available` 仍是 **174**；`not_implemented_yet` 仍是 **23 条**——
  第 22 条从「Melting, by name」（三个工具都没潜热）**改写**为「Melting, past the melt-through」
  （三个工具都有潜热、都没有熔后行为）。**不是删掉登记，是换成还成立的那条。**
- 旧断言两处随语义更新：`test_fusing_latent.test_the_taper_still_stops_at_the_melt_point` →
  `test_the_taper_has_it_too_since_088`；`test_gap_is_registered` 改为断言旧条不在、新条在。
- 自检 **318 → 321**（三条：默认逐位不变 + onset 等于 sensible、绝热锚 1+β、延迟两侧可失败）。
- 新增 `tests/test_taper_latent.py` **29 例**；用例总数 **3464 → 3493**。
- 本版按用户指令**没有跑全量回归**（只跑单文件 + `--selftest`），3493 例的实测秒数待回填。

### 仍未做

熔穿之后的体积变化、熔流、表面张力成球、接头合金化/金属间化合物（铜在第一个节点全液时已经失效，
这些都在问题之后）；横向梯度（仍是一维轴向，只有 `ends_c` 一个边界温度）；
`latent_heat_j_kg` 与 `fusing_current` 一致**不进 schema**，不给一个静默改物理的旋钮。

## [0.0.87] - 2026-11-02

**`repetitive_pulse_rating` 的主数是错的：它把「速率平衡点」当成「最大可持续电流」返回。
这一版把 `value` 换成真正的「不熔电流」，把旧数按原名保留并标成上界，
再给脉冲串补上熔化潜热**（工具数**仍 180**，本版只加参数、不新增工具；
`dependencies = []` 仍成立；默认 `melting_model="sensible"` ⇒ 默认信封**逐位不变**）

### 这是一个语义错误，不是精度问题

旧实现里

```
value = i_crit = sqrt( A·h·P / (duty·β·ρ) )
```

`A` 是侧面积、`h` 是对流系数、`P` 是周长、`β` 是 `ρ(T)` 的温度系数、`ρ` 是电阻率。
它只写了一件很窄的事：**平均散热功率 = 平均焦耳热**。它里面**没有热容**（所以脉冲多短都一个答案）、
**没有熔点**（所以答案与「会不会熔」无关），却被命名为「最大可持续电流」放在 `value` 上。

决定性反例是 **duty**：把 5 % 改成 1 %，这个公式按 `1/sqrt(duty)` 涨，量出来 **23.445147 A**；
而真正的不熔电流几乎不变，**5.284344 A**。**高报 4.436718 倍**。

### 三段电流（并存，各有各的定义）

| 键 | 定义 | 0.5 mm / 1 oz / 1 ms / duty 5 % |
|---|---|---|
| `max_current_without_melting_a`（**= `value`**） | 稳态峰值**恰好到达** 1083 °C 的电流（二分 200 次，与温升同一条精确映射） | **29.617434 A** |
| `max_repetitive_current_a` | 旧的 `i_crit`，**数值一字不改**，另给 `max_repetitive_current_a_note` 写明是 upper bound | **33.156444 A** |
| `max_current_without_melt_through_a` | 峰值可**钉在 1083 °C**、靠停歇把储层排空的电流（仅 `sensible_plus_latent`） | **29.717885 A** |

第一段与潜热**无关**（潜热不改变「峰值到 1083」这件事），第三段才是潜热真正买到的东西：
它比第一段只高 **+0.34 %~+11.3 %**（见下表 `thru/no` 列），而远不是第三段 vs 第二段那个量级。

### 五组几何（全部实测，`_scratch/probe_freeze087b.py` / `probe_scan087.py` 复核）

| geo | 参数 | `value`（不熔） | `max_repetitive_current_a`（上界） | `..._without_melt_through_a` | thru/no | run/no |
|---|---|---|---|---|---|---|
| A | 0.5 mm / 1 oz / h 200 / 1 ms / D 5 % | 29.617434 | 33.156444 | 29.717885 | 1.003392 | 1.119491 |
| B | 2.0 mm / 2 oz / h 50 / 10 ms / D 20 % | 41.302920 | 46.121186 | 41.338090 | 1.000852 | 1.116657 |
| C | 0.5 mm / 1 oz / h 20 / 1 s / D 10 % | 5.079179 | 7.414006 | 5.581757 | 1.098949 | 1.459686 |
| D | 0.5 mm / 1 oz / h 20 / 1 s / **D 1 %** | 5.284344 | **23.445147** | 5.881642 | 1.113032 | **4.436718** |
| E | 0.25 mm / 0.5 oz / h 30 / 0.1 s / D 50 % | 1.279918 | 1.435716 | 1.286822 | 1.005394 | 1.121725 |

（`h` 单位 W·m⁻²·K⁻¹；`run/no` = 旧值 / 新值。）

### 潜热在脉冲串上的实测

- `latent_rise_k = L_f / c_p = 208700 / 385 = **542.077922 K**`
- 旧口径下 0.95×runaway：A 31.498622 A ⇒ `sensible` **81** 发 vs `latent` **139** 发（+71.605 %）；
  B 43.815127 A ⇒ **274** vs **467**（+70.438 %）
- 新口径下 1.005×`value`：A ⇒ **218** vs **2360** 发（+982.569 %）
- 1.02×`value`：C `sensible` **3** 发、D **1** 发，`latent` **永不熔**
- 1.02×`thru`：A 熔在 **305** 发、B **1037**、C **2**、D **1**、E **100**；0.98×`thru` 全部 `survives_indefinitely=True`
- **`value` 以下（0.99 / 0.995×`value`）两个模型都 `pulses_to_melt = None`** —— 物理上峰值到不了
  1083 °C 就不可能熔。（本版早期的一条内部记录写在 0.95×`value` 下 91→166 发，
  **是错的**，被本轮实测推翻，已从文档里删掉。）

### 绝热极限：`lat = ceil((1+β)/t_on)` 也是错的

`h = 0`、`duty = 1`、`i_fuse = 5.024 A`，实测 `sensible` **1 / 2 / 5 / 10** 发（`t_on` = 1 / 0.5 / 0.2 / 0.1 s），
`latent` **2 / 3 / 7 / 13** 发。按 `ceil((1+β)/t_on)` 推 0.2 s 档应是 **6**，实测 **7**。

真规律是**储层按熔点处的吸热速率灌满**：

```
φ = a·(1 + β·dTm) = 423.8640 × (1 + 0.003861 × 1058) = 2155.3238 K/s
额外通电时间 = (L_f/c_p) / φ = 542.077922 / 2155.3238 = 0.251506 s
```

实测咬合：`t_on` = 0.05 → 5 发（0.25 s）、0.01 → 25 发（0.25 s）、0.005 → 51 发（0.255 s）、
0.1 → 3 发（0.3 s）、0.2 → 2 发（0.4 s）。`t_on` 越短越接近 0.2515 s 的极限。

负控制（都能失败）：把温升模型算成 `1.512` 倍（真值 `1 + β = 1.003861`）；
按**冷态速率** `a` 付潜热要 **1.279 s**（约 5 倍于 0.25 s）；按温升付约 **0.509 s**（约 2 倍）。

### 数值方法：临界点逐发在 1/n 上收敛，不能直接二分

`_train_latent` 的逐发推进在临界电流处按 **1/n** 收（B 组要 1e5 发才看到 1 %），
所以二分上限只能用**解析不动点**：`_latent_settles` 解 `F(x) = x`（`F` 是「冷却一个周期后仍留在储层里的量」），
80 次二分，并与逐发暴力实现交叉验证（`_scratch/probe_xpair087.py` 12 点全 OK；
`probe_drift087.py` 在 0.98×thru 下 2000 与 20000 发沟底漂移 **0.0**）。

### 文件

- `src/pcb_agent_mcp/calc/current.py`：`COPPER_LATENT_RISE_K`；`_latent_settles` / `_train_latent` /
  `_latent_ceiling`；`_train` 的除零分支（`b == 0` ⇒ `shots=None`）；
  `repetitive_pulse_rating(..., melting_model="sensible")`（`None` → `"sensible"`，非法 → `ARG_INVALID`）；
  `value` 改成不熔电流；新增 `max_current_without_melting_a` / `runaway_bound_over_no_melt` /
  `max_repetitive_current_a_note` / `max_current_without_melt_through_a` / `melt_through_over_no_melt` /
  `melt_beyond_shot_cap` / `melting_model` / `pulses_to_melt_sensible` / `latent_rise_k` /
  `pulses_to_melt_with_latent` / `latent_release_capacity_k_per_period` / `latent_gain_last_pulse_k` /
  `latent_verdict` / `latent_delays_melt_pct` / `max_repetitive_current_unaffected_by_latent`。
- `src/pcb_agent_mcp/tools.py`：schema 加 `melting_model`；handler 透传；description 改写成三段电流口径；
  `not_implemented_yet` 第 22 条收窄到**只剩** `fusing_current_taper`。
- `src/pcb_agent_mcp/server.py`：自检 `870/8701/8702/8703/8704/8705/8706/8707`（三段电流 + 潜热第四数 +
  阈值两侧 305 + `ARG_INVALID` + 默认 == 显式 sensible），**314 → 318**；工具数历史文案补
  `0.0.87 arguments only (melting_model on repetitive_pulse_rating)`。
- `tests/test_pulse_latent.py`：**新增 28 例**（五组几何 + 三段语义 + 默认逐位不变 + 潜热迟滞 +
  绝热档 + 储层交叉验证 + 天花板两侧 + 两条负控制 + 发数上限不等于存活 + 工具层）。
- `tests/test_pulse_fusing.py`：1 处旧断言跟着主数语义改（`> 10.0` → `> 5.0`，并加
  「`max_repetitive_current_a` 必须大于 `value`」）。
- `tests/test_fusing_latent.py`：1 处 —— 旧断言「另两个熔断工具都不认 `melting_model`」改名收窄成
  **只管 `fusing_current_taper`**（`repetitive_pulse_rating` 本版就有这个参数了）。
- `docs/89-脉冲串的熔化潜热.md`：新增（9 节）。
- `docs/README.md`：八十八篇 → **八十九篇**（2 处）+ 表行 `| 89 |`。
- `README.md`：版本 0.0.87、`--selftest 318/318`、一页速查新增「脉冲串的潜热」行、
  「另两个熔断工具」收窄成只剩 taper、用例数 3376/3436 → **3464**（两处）、自检话题补「脉冲串三电流四问」。
- `pyproject.toml` / `__init__.py`：0.0.87。

### 测试

自检 **318/318**。按用户要求「简化测试，功能全部做完后再批量测回归」，
本版**没有跑全量回归**：只跑单文件（`tests.test_pulse_fusing` + `tests.test_pulse_latent` +
`tests.test_fusing_latent` 合计 `Ran 93 tests in 25.556s` / `OK`）。全量 3464 例的实测秒数等下一次集中回归回填。

### 仍未做（登记，不静默）

`fusing_current_taper` 仍按熔点判（时间**短 20.2 %**）：沿长度那一版要每节点一个 `0..L_f` 的焓状态，
源项按 `T(H)` 分段，backward Euler + Picard——登记为下一版。潜热这一侧也没有 Stefan 前沿、
没有熔流与体积变化、没有焊点合金化。

## [0.0.86] - 2026-11-02

**Onderdonk 只算到铜「到达 1083 °C」，可它还没熔——这一版把熔化潜热做成闭式，
并用独立积分把那个容易错的 ρ(Tm)/ρ(Ta) 钉住**
（工具数**仍 180**，本版只加参数、不新增工具；`dependencies = []` 仍成立；
默认 `melting_model="sensible"` ⇒ 以前打印的熔断表**逐位不变**）

先记一笔作废的路线：0.0.85 结尾登记「圆/环源（要 Bessel `J0/J1` 与 `J1` 零点，天然的 0.0.86 候选）」。
Bessel 实现先做出来了（`j0j1j2` 用升幂级数 + Miller 反向递推 + 归一化，对 mpmath dps=40 到 x=8000
最差相对差 **1.58e-11**；`j1_zero` 用 Newton + 区间双向夹逼，400 个零点与 `besseljzero(1,m)` 最差
相对误差 **2.53e-16**），极化级数的整板极限也**逐位精确**——但部分和**对数发散**：

| terms | R (K/W)，5 mm 板 / 1 mm 厚 / a = 2 mm / k = 200（物理上界 0.397887） |
|---|---|
| 100 | 15.499606962319 |
| 400 | 19.875190673392 |
| 800 | 22.067574739857 |
| 1600 | 24.261116583130 |
| 2400 | 25.544548217302 |
| 3200 | 26.455238543509 |

逐项衰减实测是 **1/m**（m=200 → 6.486e-3、m=400 → 3.253e-3，恰好减半），解析上
`I_m ~ β^(−1/2)`、`⟨J0⟩ ~ β^(−3/2)`、`f ~ R/β` ⇒ 通项 `~4.09/β`，`Σ 1/β` 发散。
**不入库，也不做发散级数的外推**——"多取几项就收敛"在这里是假的。改做潜热。

### 模型

```
c·ρ_m·A·dT/dt = I²·ρ(T)            ρ(T) = ρ20·(234+T)/(234+Ta)
(I²t)_sensible = (c·ρ_m·A²/ρ20)(234+Ta) ln((234+Tm)/(234+Ta))
(I²t)_latent   = ρ_m·L_f·A²/ρ(Tm)
β ≡ latent/sensible = L_f / (c·(234+Tm)·ln(ratio))     ratio = (234+Tm)/(234+Ta)
I_melt = I_onderdonk · sqrt(1 + β)                     I²t 被能量平衡钉死
```

几何、面积、`ρ20` **全部消掉**。

### 为什么不是 `L_f/(c·ΔT) = 0.512`

潜热阶段站在**最热点**上：最后一段是在 `ρ(1083)/ρ(25) = 5.0849` 倍冷态功率下走完的，所以它
**按时间便宜、按能量昂贵**。`β/naive = (234+Tm)ln(ratio)/(Tm−Ta) = 2.0244`，真值 **0.253093**。

| Ta (°C) | ratio | β | I²t 倍数 | 电流倍数 |
|---|---|---|---|---|
| 0 | 5.628205 | 0.238224 | 1.238224 | 1.112755 |
| **25** | **5.084942** | **0.253093** | **1.253093** | **1.119416** |
| 50 | 4.637324 | 0.268294 | 1.268294 | 1.126186 |
| 85 | 4.128527 | 0.290285 | 1.290285 | 1.135907 |
| 100 | 3.943114 | 0.300007 | 1.300007 | 1.140178 |

实用：0.5 mm × 1 oz × 1 s ⇒ **5.024 A → 5.624 A**。稳态那一半只动时间不动电流：
`capacity_a` 仍 1.404 A、`fuse_over_capacity` 3.578 → 4.005、熔穿时间 **12.803135 → 16.043515 s**。

### 独立校验（RK4 直接积分同一条 ODE）

| Ta (°C) | β_RK4 | β_闭式 | 相对 |
|---|---|---|---|
| 0 | 0.238223631682 | 0.238223631682 | 1.8e-12 |
| 25 | 0.253092718970 | 0.253092718970 | 2.6e-15 |
| 50 | 0.268294437783 | 0.268294437784 | 1.5e-12 |
| 85 | 0.290284624241 | 0.290284624243 | 4.9e-12 |
| 100 | 0.300006804552 | 0.300006804551 | 2.6e-12 |

### `c` 取 385 而不是 400 的依据

Onderdonk 是拟合式，它隐含了铜的 `c·ρ_m/ρ20`。在 **25 °C** 对齐：
`(c·ρ_m/ρ20)(234+25)` 对 `1/(CMIL²·33·ln10)`。

- `c=385`、`ρ_m=8960`、`ρ20=1.72e-8` ⇒ `5.194456e16` 对 `5.125730e16`，**+1.34 %**
- `ρ20=1.68e-8` ⇒ +3.75 %；反解让等式精确的 `ρ20 = 1.7431e-8 Ω·m`
- 若取 `c=400` 会推到 **+5.3 %**，与 33 打架

这个恒等式**只在 25 °C 定义**（0 °C 差 −8.4 %、100 °C 差 +30.7 %），所以测试里专门有一条
负控制要求它在别的温度上**必须不平**，否则这个检查没有分辨力。

### 数据来源（联网核实，2026-11-02）

熔化潜热 **13.26 kJ/mol**、摩尔质量 63.546 g/mol、熔点 1357.77 K、`cp`(25 °C) **24.440 J/(mol·K)**、
密度 8.96 g/cm³ —— https://www.newworldencyclopedia.org/entry/Copper ；
第二来源潜热 **210 J/g** —— https://link.springer.com/referenceworkentry/10.1007/978-1-4614-1533-6_337?page=24 。
折算 `13.26/0.063546 = 208.7 kJ/kg`、`24.440/0.063546 = 384.6 J/(kg·K)`。两来源潜热差 0.6 %
（β 上 0.6 %、电流上 0.3 %）。**存 208700 与 385，不存 210000 与 400**：后者让 `ρ20` 反解漂到 1.69e-8。

### 本轮抓到的两个真缺陷

1. **系数被无条件乘上去**：第一版把 `(1+β)` 直接写进 `melt_at_cap_s`，于是
   `sensible` 与 `sensible_plus_latent` **都**打印 `16.043515 s`。数字看着对、`status` 是 `ok`，
   但它**悄悄把「按熔点判」的答案换成了「按熔穿判」的答案**——而这正是本版承诺不动的东西。
   修法是让系数跟着 `melting_model` 走，`sensible` 回到 **12.803135**。
   这类错误只有「默认路径逐位不变」这条检查能抓到：单看一个模型，两个数都自洽。
2. **`%%` 逃逸漏在 err 的 hint 里**：`melting_model` 的拒绝提示里写成了 `+25.3 %%`
   （那句没有 `%` 格式化），于是用户看到的是双百分号。改成单个 `%`。

### 仍未做（登记，不静默）

`repetitive_pulse_rating` 与 `fusing_current_taper` **仍只算到熔点**：它们报的熔断时间是
「到达 1083 °C 的时间」，比熔穿时间**短 20.2 %**（0 °C 短 19.2 %、100 °C 短 23.1 %）。
改对**不是标量乘法**：潜热阶段要给每个节点一个 `0..L_f` 的焓状态，光滑 ODE 变成切换系统。
所以 `calc_capabilities.not_implemented_yet` 新增一条 **「Melting, by name」** 逐条写明，
并把「把潜热折算成温度偏移」这条**拒绝掉**——那正是本版禁止的静默近似。
另：同心圆/环源（第 1 节，收敛但未实施，需 3-D FVM 验证）、熔化后的体积变化与熔流、焊点合金化。

### 文件

- `src/pcb_agent_mcp/calc/current.py`：常量 `COPPER_CP_J_PER_KGK = 385` /
  `COPPER_LATENT_HEAT_J_PER_KG = 208700` / `COPPER_DENSITY_KG_M3` / `COPPER_RHO20_OHM_M` /
  `M2_PER_CMIL` / `MELTING_MODELS`；新函数 `latent_heat_share_of_i2t()`；
  `fusing_current(..., melting_model="sensible")`，两个模型的电流、`melt_at_cap_s` 的系数、
  `model` / `basis` / `precision` / `assumptions` 分支；`out` 新增 `melting_model` /
  `fusing_current_sensible_only_a` / `fusing_current_with_latent_a` / `latent_share_of_i2t` /
  `latent_heat_means`。
- `src/pcb_agent_mcp/tools.py`：`fusing_current` schema 加 `melting_model`（enum 两项 + 三档温度百分比）、
  handler 透传、工具描述改成量化；覆盖范围那两处「no latent heat」改成引用同一个
  `+25.3 % I²t / +11.9 % 电流`；`not_implemented_yet` 新增「Melting, by name」。
- `src/pcb_agent_mcp/server.py`：自检 `860/861/862` 三条（默认逐位不变 + `sqrt(1+β)`；
  稳态熔穿时间按系数走；β 五点 + 33 一致性 + 负控制 `naive/β = 2.0244`），**311 → 314**；
  工具数历史文案补 `0.0.86 arguments only (melting_model on fusing_current)`。
- `tests/test_fusing_latent.py`：**28 例**，含三条能失败的负控制（naive 热质 / 33 只在 25 °C /
  积分器把 `ρ(Tm)` 换成 `ρ(Ta)` 会偏 5 倍以上）与一条独立 RK4 积分。
- `docs/88-熔断与熔化潜热.md`：新增（9 节）。
- `docs/README.md`：八十七篇 → **八十八篇**（2 处）+ 表行 `| 88 |` + 文末清单行。
- `README.md`：版本 0.0.86、`--selftest 314/314`、一页速查新增「熔断的潜热」行、
  两个熔断工具的「无潜热」改成量化缺口、自检话题清单补「熔断潜热三问」。
- `pyproject.toml` / `__init__.py`：0.0.86。

### 测试

自检 **314/314**。全量回归 `Ran 3436 tests in 1382.506s` /
`OK (skipped=5)`（0.0.85 是 `Ran 3408 tests in 1141.571s` / `OK (skipped=5)`），
本轮新增 `tests/test_fusing_latent.py` 28 例 ⇒ 3408 + 28 = **3436**。

## [0.0.85] - 2026-11-02

**一块板上的小焊盘，比它自己那一柱铜多出多少热阻——这一版把收缩（铺展）热阻做成解析解，
消掉 `not_implemented_yet` 第 16 / 20 两条一直在指的那个缺口**
（工具数 179 → **180**，`dependencies = []` 仍成立；`thermal_heatsink` 多一个可选入参，不传则逐位不变）

`thermal_stack` 一直写着「strictly 1-D, it cannot see spreading」，`thermal_plate` 更直接——它把厚度
**集总**掉（`k·t·∇²T − 2h(T−Tamb) + q = 0`），板在厚度方向恒温，于是「板上一个小焊盘」在它那里
**连这个物理量都不存在**。缺的不是精度，是一个不在场的量。

### 模型

矩形板、四侧绝热、`z=0` 上矩形 `[x0,x1]×[y0,y1]` 均匀热流，`z=t` 等温（板压在冷板上）或对流（Robin）：

```
R      = 1/(k·A_s²·W·L) · Σ_{m,n} I_mn² · ε_m · ε_n · f(λ_mn)
I_mn   = ∫_source cos(mπx/W) cos(nπy/L) dA        ε_0 = 1, ε_m = 2 (m ≥ 1)
λ_mn   = π·sqrt((m/W)² + (n/L)²)
等温:  f = tanh(λt)/λ
对流:  E = e^(−2λt), g = h/(kλ)
       f = [(1+E) + g(1−E)] / [(1−E) + g(1+E)] / λ
```

回答的是**源面平均温度每瓦**（均匀热流 + 平均温度是一对共轭量）。对流分支写成 `exp(−2λt)` 而不是
`cosh/sinh`：厚板 `λt ≫ 1` 时后者直接溢出，而这个形式两端都稳定。

### 两个精确极限——它们抓到了一个真 bug

| 极限 | 手算 | 实测 |
| --- | --- | --- |
| 源 = 整板（只剩 `m=n=0`） | `t/(k·A_plate) = 0.05` | **`0.05`**，`spreading_excess` **`0.0`** |
| 源 = 整板 + 对流面 | `0.05 + 1/(h·A) = 10.05` | **`10.05`**（`1e-9` 内） |

第二条在开发中抓到真 bug：`m=n=0` 项原写 `f = t`，对流时**漏了 `k/h`**。一维解是
`T = q(t−z)/k + q/h`，极限必须是 `t + k/h`。漏掉以后 `h=1000` 的 4×4 板给出 `0.5154`（横向铺展的残值），
正确答案 `≈10.5`——**整项对流热阻凭空消失，而 `status` 仍是 `ok`**。这正是「两个能对到 1e-15 的
极限」存在的意义：只在单一算例上对过的实现会漏掉它，且漏了不会响。

### 独立三维 FVM：序列是极限，差距是离散误差

`_scratch`（numpy/scipy，**不入库**）另写一个不分离变量的三维有限体积解（直接离散 `∇·(k∇T)=0`，
CG + Jacobi，10×10×1 mm、k=200、中心 4×4、等温远面），解析值 `0.236078530`：

| `nz` | FVM | 差 |
| --- | --- | --- |
| 8 | 0.206754 | **12.42 %** |
| 12 | 0.216500 | **8.29 %** |
| 24 | 0.226334 | **4.13 %** |
| 48 | 0.231294 | **2.03 %** |
| 96 | 0.233786 | **0.97 %** |

**每 halve 一次 `dz`，差距就 halve 一次**（一阶收敛）⇒ 序列的极限就是解析值，差距是离散误差、
不是缺项。`nx` 40/80/160/240 给 `0.233291/0.231772/0.231294/0.231191`，横向已饱和。

### 冻结读数（10×10×1 mm、k=200、等温远面、`terms=400`）

| 源 | R (K/W) | `psi` | `excess` | `relief` |
| --- | --- | --- | --- | --- |
| 中心 4×4 | `0.236078529798` | `0.188862824` | `0.186078529798` | `0.076421470202` |
| 中心 2×2 | `0.716124740033` | `0.286449896` | `0.666124740033` | `0.533875259967` |
| 角 3×5 | `0.253879439710` | `0.196654168` | `0.203879439710` | `0.045886877810` |
| 条 8×1 | `0.335405525827` | `0.189734017` | `0.285405525827` | `0.289594474173` |

默认 `terms=120` 的中心 4×4 = **`0.236046830818`**，`convergence_rel = 1.1e-4`（对 `terms=240`）。
精确结构检验：`x` / `y` 镜像差 **`0.0`**，`k` 加倍比值 **恰好 `2.0`**，角上 3×5 比中心 4×4 更便宜
（`0.254` 对 `0.236`，绝热板边给了它更多可扩散的面积）。

### 夹逼：`t/(k·A_plate) ≤ R ≤ t/(k·A_source)`

地板是 `m=n=0` 项（源铺满整面），其余项全为正 ⇒ 只能更高；上限是「只在源下面成柱」的板。
两个余量分开报（`spreading_excess` / `spreading_relief`），`extra["brackets"]` 写明恒成立。
偏中心 1.5×3.5 mm 源、`terms=400`：`0.05 ≤ 0.568797535268 ≤ 0.952380952381`（coverage 5.25 %）。

### 口径与实测的收敛速度

**均匀热流，不是等温焊盘**（半空间上两者差 8 %，方向是**偏大**）——写在 `precision` 里，不藏在假设里。
薄板极限 `R → t/(k·A_s)` 的实测截断（`R/col`）：

| t (mm) | `terms=120` | `terms=400` |
| --- | --- | --- |
| 1 | 0.572697948（2.9e-04） | 0.572899792（2.6e-05） |
| 0.1 | 0.944566510（1.8e-03） | 0.946584510（1.6e-04） |
| 0.01 | 0.982412006（7.8e-03） | 0.992694476（1.4e-03） |
| 0.001 | 0.983182440（8.5e-03） | 0.994913976（2.5e-03） |

指示函数的余弦系数按 `1/m` 衰减 ⇒ 薄板收敛慢（`t=1e-3` 时 120 项仍差 0.85 %）。**所以判据是先量后写的**：
第一版写「`R/col` 与 1 差 < `0.02·t`」，在 `t=1` 处等价于要求 2 % 而实测 42.7 %，被测出的真实行为推翻，
改成「随 `t` 减小**单调上升** + `t=1e-3, terms=400` 时 `≥ 0.99`」。

对流面：`h=1e9` 时 `0.236080537` 对等温 `0.236046831`（差 3.4e-5）；`h=10/100/1000` 时
`R − 1/(h·A_plate) = 0.5189/0.5186/0.5154`（`Bi→0` 的横向铺展残值）。**但 `1/(h·A_plate)` 只是近似**：
热只从源附近出去，硬减它在 `h=1e6` 处留 **0.0217** 残差——第一版测试就是这么错的，现在的判据是
「`h` 越大 `R` 越小、恒 `≥` 等温值、`h=1e9` 时差 < 1e-3」。

### 负控制：整板锚点对一类错误是**盲**的

| 错法 | 结果 |
| --- | --- |
| 漏掉 `ε_m` 正交因子（`m>0` 不乘 2） | 同一算例偏 **> 10 %**，被抓 |
| **同一个错法 vs 整板锚点** | 差 **`0.0`** —— 整板锚点对这一类错误**完全看不见** |
| 整体系数乘错 | 偏 **> 40 %**，被抓 |
| 朴素径向 `ln(b/a)/(2πkt)` 当矩形用 | 差 **> 30 %**，被抓 |

第二行是重点：整板锚点只点亮 `m=n=0` 一项，对正交因子、对系数、对所有 `m,n>0` 的项无感。
所以「极限锚点」与「负控制」是两类测试，不能互相替代——蒙对一个极限，和模型是对的，是两件事。

### 拒绝而不是猜

`far_face="adiabatic"` **拒绝**（`ARG_INVALID`，hint: 绝热远面 + 绝热侧边 ⇒ 没有汇 ⇒ 稳态不存在）；
`convective` 缺 `h` ⇒ `ARG_REQUIRED`（hint 指向 `thermal_convection`）；源出板/零面积/零厚度/零 `k` ⇒
`ARG_RANGE`；`terms` 越界 ⇒ `ARG_RANGE`；**`terms` 非整数 ⇒ `ARG_INVALID`**——这一条在工具层踩到过：
`int(a["terms"])` 把 `25.5` 静默变成 `25` 并返回 `ok`，自检 `852` 因此在 310/311 处报 FAIL，改成
「按名字拒绝」后 311/311。**静默修正**正是这个项目明确不做的事。

### 与既有答案的关系

- `thermal_heatsink` 的 `rth_spread_k_per_w` 默认 **0** ⇒ 不传时逐位不变（自检同时钉住 5 W/60 K 的
  `10.0 / 8.0` 与传 `0.1860785` 后的 `9.81392147 / 7.851137176`）；`INFEASIBLE` 文案点名 case-to-sink
  与 spreading（100 W / 0.6 K/W 预算里 case + spreading 占 0.686 ⇒ 拒绝并说「没有散热片能救」）。
- `thermal_stack` 与热过孔两条 `not_computed` 文案改为**指向新工具**（不是删掉缺口，而是把「完全没有」
  精确成「连源面都不再是矩形的那些情形」）。
- `calc_capabilities.available` **173 → 174**（`thermal_spreading` 入列；180 个工具里 6 个不进 `available`，
  与 179/5 的关系一致）。
- 顺手修一处**只在新工具里出现**的 `-0.0`：`0.05 − 0.05` 的舍入会打印成 `"-0.0"`，用 `_z12()` 归一成 `0.0`。

### 测试与自检

- `tests/test_thermal_spreading.py` **32 例**（两个精确极限 + 4 个冻结值 + 夹逼 + 单调 + 镜像/尺度精确 +
  薄板极限按实测重写 + Robin 收敛按实测重写 + 4 条故意写错的负控制 + 7 类坏参数 + 工具层 schema/默认值/
  `terms` 不截断/heatsink 预算/能力表/负零）。
- `--selftest` **308/308 → 311/311**（新增 3 条）。
- 全量回归 **`Ran 3408 tests in 1141.571s` / `OK (skipped=5)`**（0.0.84 是 3376 例 / 1327.362 s；
  3376 + 32 = 3408。秒数不作性能结论——本机负载没有记录，skipped 仍是那 5 条）。
- **本轮抓到的第二类回归**：10 个旧测试文件里那条「工具数棘轮」（`assertEqual(len(tools.TOOLS), 179)`）
  与 3 处 `available 173` 没跟着改，第一次全量跑出 2 例 F。这类硬计数断言的意义正在于此——
  加了工具就必须显式改一遍，而改动点会出现在 diff 里。已在 13 处补成 `180` / `174` 并各留一行来历。
- `docs/87-矩形热源的收缩热阻.md`（新，8 节）。

## [0.0.84] - 2026-11-02

**铺铜上那个焊盘不是"实心"接上去的——这一版把热焊盘花的辐条算进来，消掉本项目唯一一处自称乐观的偏差**
（工具数仍 **179**，`dependencies = []` 仍成立；不加工具，`board_ir_drop` 多一个可选对象参数）。

`not_implemented_yet` 第 18 条写着 `THERMAL-RELIEF SPOKES ARE NOT MODELLED … 这是本答案唯一可能**乐观**的方向`。
别的缺口是没有答案，这一条是**给了答案但偏小**，所以先补它。

### 一条辐条就是 gap/width 个方块

```
R = R_sheet · gap / (n · w)
```

辐条沿电流方向长 `gap`、宽 `w`，所以自己是 `gap/w` 个方块，`n` 条并联再除 `n`。`R_sheet` 取该层
**实际在用**的那个（`rho/t`），因此带 `frequency_hz` 时自动是趋肤修正后的 `t_eff`——辐条和铺铜是同一片铜。

手工值（35 µm、`rho=1.724e-8`、4 × 0.5 mm 跨 0.5 mm）：
`1.724e-8/35e-6 = 4.9257142857e-4 Ω/□` ⇒ `× 0.5/(4·0.5) = 1.2314285714e-4 Ω = 123.14 µΩ`，
实测 `connection_ohm_by_layer["F.Cu"] = 1.23142857e-4`（相对误差 < 1e-6），且 `n`/`w`/`gap` 各翻一倍时比值 2.000000。

### 实测：默认真的是乐观的（同板、两个 1×10 mm 焊盘、20 mm 间距、0.5 mm 栅格）

| 情形 | `max_drop_mv` | 说明 |
| --- | --- | --- |
| 不传 `thermal_relief` | **0.935986** | 0.0.82/0.0.83 的金标，逐位不变 |
| 4 × 0.5 mm / gap 0.5 | **1.207938** | 抬升 271.95 µΩ ≈ `2R`(=246.29) + 分布改变后铜再多吃一点 |
| 4 × 0.25 mm | 1.454998 | 辐条窄一半 ⇒ 连接电阻翻倍 |
| 4 × 1.0 mm | 1.083380 | 宽一半 ⇒ 减半 |
| 1 × 0.5 mm | 1.947975 | 单条 = 四条之和 |

抬升判据写成 **`2R ≤ 抬升 < 1.5·2R`**：下界就是"每个焊盘只许算一次"，上界防"重复计了三四次"。

### 四个真缺陷/真细节

1. **接触格必须均摊**：0.5 mm 栅格下 1×10 mm 焊盘压到 60 个填充格，`pad_boxes` 原本每格连 `1 µΩ`
   （实心时并联无害）。换成真电阻就变成 60 条 123 µΩ 并联 = **2.05 µΩ**，答案小 60 倍。
   改为每格 `k·R`（`k` = 接触格数），`k` 条并联恰为 `R`：一个焊盘的整体连接是**一个**辐条组。
2. **锚点去重**：焊盘锚点既是网络节点又在 `pad_boxes` 里，1 µΩ 时代双重短接无害，真电阻会**并联减半**，
   所以节点循环对锚点 `continue`。
3. **这一跳引出静默错答案**：焊盘**完全不落在填充里**（离铺铜 2 mm、在 `zone_link_mm` 内）时，
   锚点被跳过 + 焊盘循环"接触格为空"什么都不做 ⇒ 焊盘从网络里消失，整条网报 `NOT_CONNECTED`。
   修法是焊盘循环在接触格为空时自己走 `nearest_cell` + 辐条电阻（探针里撞出来的，已写成测试）。
   顺带得到一条对照：同一块短铺铜板，旧"一个方块"stand-in 给 1.654493 mV，4×0.5 mm 辐条给 **1.421040 mV**
   ——旧 stand-in 在**覆铜远、辐条宽**时是**悲观**的，只在填充里才乐观，两个方向都不该用 stand-in 代替几何。
4. **和一个既有局部变量撞名**：新参数的字典最初叫 `tr`，而函数后面 `for tr in tracks:` 也用它，
   于是"不传 `thermal_relief`"的调用在跑到铺铜那一段时 `tr` 已经变成最后一条走线 ⇒ `KeyError: 'gap_mm'`。
   全量回归抓到了（3 个既有 AC 用例直接报错），改名 `tr_geo`。这条留在这里是因为它正好是"默认关时行为零变化"
   这条红线要防的东西：不是逻辑写错，而是**新代码改了旧路径的变量含义**，且只在特定调用形态下现形。

### 文件里的值：报告，但不采用

KiCad 的写法（22.8 万行真实板上实证）：`(connect_pads (clearance X))` = 实心、`(connect_pads yes …)` = 热焊盘花、
`(fill yes (thermal_gap 0.5) (thermal_bridge_width 0.5))`——**两个数在 `fill` 子节点里**，不在 `connect_pads` 里。
`_zone()` 现在把它们读出来放进 `zone_summary["board_file_states"]`，**但几何一律由调用方显式给**：
`connect_pads yes` 与 `zone_connect 2/3/0` 的数值语义没取到一手文档（`dev-docs.kicad.org` 抓取失败、本机无 KiCad），
凭记忆当默认等于让记忆决定答案。行为改为：文件声称热焊盘花而调用方没传参数时，给一条 `OPTIMISTIC` warning
并把文件里的三个数报在旁边（`connect_pads yes` 的板子不传参时 `max_drop_mv` 仍是 0.935986，已写成测试）。

### 拒绝而不是猜

三个数全部必填、一个都不默认。`ARG_INVALID`（非对象 / 未知键 / 非数字，未知键的 hint 说明"静默忽略会让调用方
以为辐条被建模了"）、`ARG_REQUIRED`（缺任一键，说明"没有合理默认值"）、`ARG_RANGE`（`spokes` 不在 1..16、
`width ≤ 0`、`gap = 0`——后者 hint 指向"gap 0 就是实心，别传 `thermal_relief`"）、`ARG_CONFLICT`（`model_zones` 没开）。
给了参数却一个焊盘都没用上时也 warning，不沉默。

### 不在这个数里的（逐条回给调用方）

焊盘自己的扩散电阻、辐条根部到焊盘角的三维电流集中、`n·w` 超过焊盘周长（**接受并报告，不 clamp**）。
另两条边界：不加一个没有几何依据的 spreading 项；`thermal_relief` **只作用于焊盘**，走线端在填充里仍 `1 µΩ`、
跨缝桥接仍一个方块（那两处没有辐条）。

### 测试与自检

- `tests/test_thermal_relief.py` **31 例**（公式对账 + 三方向缩放 + 单调 + 负控制"默认更低" + `k·R` 均摊恒等
  + 填充外焊盘仍连接 + AC 带 `t_eff`（`R(AC)/R(DC) = 1.0069737` 与 `r_ac_over_rdc` 对到 1e-6）+ 8 类坏参数
  + 文件值报告不采用 + 工具层 schema/handler）。
- `--selftest` **305/305 → 308/308**（新增 3 条：公式手算对账、默认 0.935986 与辐条 1.207938 同时钉住、
  `n`/`w` 的位置与半填参数被 `ARG_REQUIRED` 拒绝）。
- 全量回归 **`Ran 3376 tests in 1327.362s` / `OK (skipped=5)`**（0.0.83 是 3345 例 / 1330.170 s；3345 + 31）。
- `docs/86-热焊盘花的辐条电阻.md`（新，8 节）。

## [0.0.83] - 2026-11-02

**Miles 只算一个模，而第二个模常常只差 1.5 倍——这一版把其余模态都算进去，并先把「每个模占多少」这个数算准**
（工具数仍 **179**，`dependencies = []` 仍成立；本版不加工具，新能力是两个可选参数）。

`not_implemented_yet` 第 21 条一直以 `SINGLE-MODE` 开头。它缺的不是公式而是两样输入：
每个模的**参与因子**与观测点的**振型值**——两者都要振型，而 `plate_modes` 之前只回频率。

### 两个模差 5 % 时，单模答案错多少（实测）

同一块板、`psd = {"g2_per_hz": 0.1}`、Q=25：

| 输入 | `g_rms` | 对 Miles | 位移 rms | 对 Miles 位移 | 相干份额 |
| --- | --- | --- | --- | --- | --- |
| 100 Hz 单模 | 19.8131 | 0.9998 | 0.492168 mm | 0.9998 | 0 |
| 100 + 105 Hz | **28.3684** | **1.432** | **0.795185 mm** | **1.615** | **28.6 %** |
| 100 + 1000 Hz | 65.7146 | 3.316 | 0.492586 mm | 1.001 | 0.00065 % |

`g_rms` 高 43 %、位移高 61 %，且**28.6 % 的位移方差来自交叉项**——单模公式里根本没有那一项。

### 参与因子是精确矩，不是拟合权重

`Γ = ∫w dA / ∫w² dA`（w 按峰值归一到 1），两个积分都是 Ritz 基的**精确单项式矩**（可分离基 `φ = X(xi)Y(eta)`，
`∫X = Σ Wx[k]/(k+1)`、`∫X² = ΣΣ Wx[k]Wx[k']/(k+k'+1)`），没有求积也没有拟合。两个解析锚点：

- SSSS 方板 (1,1)：`16/π² = 1.621139`，本工具 `1.62102`（**0.007 %**）；
- 带偶数下标的模（(1,2)、(2,1)、(2,2)）：**恰为 0**（实测 −5.9e-13，是求积残差）——均匀基座加速度**不激励**它；
- 质量捕获率 `(16/π²)²/4 = 0.656966`，本工具 `0.657023`（模态质量 7.4011 g = 板重 29.6 g 的 1/4）。

### 三条积分，一条被弃用

| 量 | 被积函数 | 说明 |
| --- | --- | --- |
| 位移（**相干**） | `W·|Σ_i D_i|²` | 交叉项在里面，收敛 |
| 每模自己的位移 | `W·|D_k|²` | 分账与「只有最强模」 |
| 绝对加速度（传感器读数） | `W·|1 − ω²Σ_i D_i|²` | `a` 与 `ω²z` 相消，收敛 |

**弃用** `W·|ω²Σ_i D_i|²`：它高频趋于常数 `c²`，任何带宽积分都被**非共振**项污染——两模 100/1000 Hz 时给
**82.32 g** 对正确 **65.71 g**（25 % 高）。这条写成了可失败的测试（每次都把那个错数算出来、断言它明显偏大）。

**加速度与位移不同规格**：`g_i = ω_i²·z_i`、`g_rms = √Σ g_i²`（模态叠加的标准做法，也是 Miles 自己那一步）；
真正的相干加速度要每个模在观测点的**相位**，`plate_modes` 只给峰值归一后的幅值，所以**不给**。
`g_rms`（共振口径，可与 Miles 比）与 `g_rms_absolute_including_quasistatic`（板上传感器读数，100+105 Hz 时 36.94 对 28.37）
两个数都报，不许混用。

### 默认关时逐位不变

- `participation` 不给 ⇒ `plate_modes` 的值里没有 `participation` 键、每模没有三个新键，频率与振型逐位相同。
- `modes` 不给 ⇒ `miles_response` 走原来那条路径（新代码是函数开头的 `if modes is not None:`，单模路径一字未动），
  171 个既有振动测试全部通过。
- `vibration_random_response` 的 `required` 从 `["f_n_hz","psd"]` 改为 `["psd"]`——不是放松判据，是纠正必填项
  的归属：`modes` 路径不需要 `f_n_hz`，而两者都不给时仍是 `ARG_INVALID`（有测试）。

### 测试与回归

- 新增 `tests/test_vibration_modal.py`（**41 例**）：解析锚点（`16/π²`、偶数模 0、捕获率、模态质量 = 板重/4）/
  默认关零变化（3）/ 单模对 Miles（4，含一条**独立网格**的位移积分收敛检查）/ 多模（10，含独立复积分对相干份额、
  0 参与因子不加、份额之和 100 %、真板四模塌成单模）/ 每模阻尼（3）/ 拒绝臂（8，各带 `reason_code` 与字段名）/
  PSD 曲线（3，含「曲线在峰之前截断要报警而不是静默 clamp」）/ 工具面（4）。
- `--selftest` **301/301 → 305/305**（新增 4 条：`Γ = 16/π²` 与 `Γ = 0`、捕获率 0.656966、单模对 Miles 1 %、
  5 % 邻居改变答案且报出相干份额）。
- 全量回归 **`Ran 3345 tests in 1330.170s` / `OK (skipped=5)`**（0.0.82 是 3304 例 / 1055.161 s；3304 + 41）。
- 文档：[docs/85-随机振动的模态叠加.md](docs/85-随机振动的模态叠加.md)（第八十五篇）。

**仍然不做**（都在 `not_implemented_yet` 第 21 条里逐条写着）：相位化的**单一**加速度谱、非比例阻尼的复模态耦合、
点力/单点固定激振、多模应力谱（疲劳仍走 `steinberg_fatigue` 的单模 3σ）。

## [0.0.82] - 2026-11-02

**DFM 残余里那条「掩膜定义的焊盘」——它不是没测，是没被问过；一次遍历同时给出「有几个」和「压得最紧的那个有多紧」**
（工具数仍 **179**，`dependencies = []` 仍成立；本版不加工具，回读走 `dfm_check` 既有的 `value["facts"]` 与 `checks`）。

`NOT_CHECKED_BY_DESIGN` 里从 `0.0.56` 起就写着「掩膜定义的焊盘」，这一版把它从「不做」改成「做」：
阻焊**开窗**比**铜**小（负的 mask margin）是两个文件里的纯几何，不读菲林图像也能算准。

### 两条规则，因为一个数答不了两个问题

- `max_mask_defined_pads`（count，max 方向）——「做不做这种焊盘」。
- `min_mask_defined_overlap_mm`（mm，min 方向）——「做的话，最紧的那处压多少」。

**同一块板对两条规则给出相反结论**（实测）：两个 1×1 mm 焊盘、margin −0.05/−0.03 的板上，
`max_mask_defined_pads: 2` 判 **pass**（margin 0.0），`min_mask_defined_overlap_mm: 0.05` 判 **fail**（margin −0.02）。
用一条规则去答另一个是**错答案**，不是缺答案——这就是分两条的理由。

**符号负控制**（同一几何翻成正 margin）：计数 2 → 0、最小重叠 0.030 → `not_applicable`、掩膜桥 1.080 → 0.920 mm。
一个符号写反会同向推动两个数，所以两条断言必须一起钉。

### 口径

- 焊盘**只数一次**（双面开窗不重复），未开掩膜的焊盘不数。
- margin 优先级 **pad > footprint > `(setup (pad_to_mask_clearance))`**，负值原样透传、不 clamp；来源登记
  `pad` / `footprint` / `setup` / `absent_treated_as_zero`。
- **门**：文档没提这两条规则 ⇒ 两个事实都带 `were not examined`，不做无谓遍历。
- **本版唯一一处放松既有校验**：`read_capability` 原来要求所有 limits `> 0`，这让「我们不做这种焊盘」
  （`max_mask_defined_pads: 0`）无法表达。现在 **max 允许 0、min 取 0 仍拒、任何负值仍拒、非数仍拒**，
  四侧都有测试钉住，最坏方向仍是 fail-closed。
- **仍然不做**：菲林本身就是一张图，库里从不读它 ⇒「远离焊盘、也远离两个开窗之间那条桥的掩膜残余」照旧不测；
  `NOT_CHECKED_BY_DESIGN` 改写**不删**、条数仍 **8**。

### 测试与回归

- 新增 `tests/test_dfm_mask_defined.py`（**27 例**）：规则目录 / 测量（单负 margin、取最小不取最大、正 margin 归零、
  absent=0、footprint 级、setup 级、pad 优先、未开掩膜不数、双面只数一次、45 个时 `list_truncated`、掩膜桥的两个符号）/
  判定（5 个端到端 + 只提一条规则时另一条不被报）/ 门控（2）/ 文档侧（4：max 0 接受、min 0 拒、负值拒、`min_track_width_mm: 0` 仍拒）。
- 棘轮两处：`RULES` **15 → 17**、`rules_known` 15 → 17 与 `coverage_pct` 6.7 → 5.9。
- `--selftest` **299/299 → 301/301**（新增 2 条：max 0 的房间，以及 count/overlap 的一次遍历）。
- 全量回归 **`Ran 3304 tests in 1055.161s` / `OK (skipped=5)`**（0.0.81 是 3277 例 / 1024.718 s；3277 + 27）。
- 文档：[docs/84-掩膜定义的焊盘.md](docs/84-掩膜定义的焊盘.md)（第八十四篇）。

## [0.0.81] - 2026-11-02

**两家厂商都不印 Steinmetz 三元组——那就把厂商真印出来的东西入库，并给你带来的三元组一个能对账的裁判**
（新工具 `magnetics_core_loss_audit`，工具数 **178 → 179**；`dependencies = []` 仍成立）。

`not_implemented_yet` 里从 `0.0.55` 起就挂着「无 material-level 三元组」，`0.0.80` 之后这条是唯一挡住
铁氧体损耗计算的东西。这一版去把「厂商到底印了什么」量清楚，结论是**没有一家印三元组**：

- **Ferroxcube HB2013**（`download/11`，`FXC_HB2013.pdf`，10,439,076 B / 1108 页）：每个牌号规格页
  只印 **2–3 个 PV 点**，而且带 `≤` 或 `≈`，其余全是图。
- **Ferroxcube 2022 3C96/3C98 宣传册**（`download/90`，784,415 B / 8 页）：裸数字，与 2013 手册逐位相同
  ——顺手当交叉校验用了。
- **TDK SIFERRIT N87**（`download/528882/x/pdf.pdf`，12 页，June 2025）：损耗只画三族曲线，
  **一个数都不印**；唯一数字是零件页上的 `Max. 375`。

所以这一版**不编三元组**（拿 2–3 个点、其中还有上界，去解 3 个指数，是把算术冒充厂商数据），
改成两件事：**把 17 个印刷点入库**，以及**让 `magnetics_core_loss_audit` 拿你手里的三元组逐点打分**。

### 入库：`data/material_loss_anchors.json`

9 个牌号 / **17 个点** / 3 个来源，每点带 `printed_page`、`pdf_page`、`printed_as`（原文，如 `<=80`）、
`qualifier`。四类 qualifier **分开存、分开判**：

| qualifier | 点数 | 含义 | 能判什么 |
| --- | --- | --- | --- |
| `at_most` | 2 | `≤` | 只能判「有没有越上界」 |
| `approx` | 11 | `~` | 只能判「是否落在 10 % 带内」 |
| `plain` | 3 | 裸数字 | 同上 |
| `max` | 1 | `Max. 375` | 只能判上界 |

**上界与典型值不可混比**——3C90 p84 同一列里就混着两类（`≤80` 与 `~450`），这也是「不能拟合」的
直接理由。`3B7`（p74）与 `N27` 一个点都没印，登记在 `materials_without_points` 里，
**不是漏抄**：查询它们得到 `points_printed = 0` 而不是错误。

页号口径两处不同，写在表里：**HB2013 的 printed page == pdf page**（页脚 `2013 Jul 31`），
而 3C96/3C98 宣传册**没有恒定偏移**（pdf 第 6 页印的是 "5"）⇒ 每点单独记 `pdf_page`。
另登记一条厂商自身异常：HB2013 的 PDF metadata 标题写着 "Data Handbook 2009"，页脚却是 `2013 Jul 31`
—— **原样保留，不顺手改**。

三条交叉校验也写进 `cross_checks`：3C96 在三处被印了两次（2013 手册与 2022 宣传册逐位相同）、
宣传册正文与它自己的表一致、以及列位移检查（按行号错位一列会让 3C90 的 β 从 2.49 变 2.5 上下、
3C93 的 β 从 2.81 掉出去 ⇒ 现行对齐是对的）。

### 新工具：`magnetics_core_loss_audit`

输入 `material` + `k/alpha/beta` + `units`（默认 **`khz_mt`**，厂商风格；注意 `core_loss_steinmetz`
的默认是 `si`，理由写在 schema 里）+ 可选 `temperature_c`。逐点复算并**把两种判据分开报**：

- `within_10_percent`：`approx` / `plain` 点用
- `satisfies_the_printed_bound`：`at_most` / `max` 点用
- 另一类为 `null`（不可比就说不可比，不拿 0 冒充）

判词三种：`consistent_with_every_printed_point`（无越界、无典型值出带）、
`violates_a_printed_upper_bound`、`does_not_match_every_printed_point`。
`value.solved_here` 恒为 `false`，并带 `why_not_solved` 与 `how_to_read_a_bound` 两段说明。

`k=5e-7 / α=1.6 / β=2.5` 的实测读数（`khz_mt`）：

```
3C90 p84
 25 kHz/200 mT  ≤80   算出  48.78081  −39.023988 %  上界满足  带外
100 kHz/100 mT  ≤80   算出  79.24466   −0.944175 %  上界满足  带内
100 kHz/200 mT  ~450  算出 448.27549   −0.383225 %  典型值    带内
→ consistent_with_every_printed_point (points_in_band=2, bound_violations=0, typical_value_misses=0)

N87 100 kHz/200 mT Max.375 → 448.27549  +19.540131 %  → violates_a_printed_upper_bound
```

第二段是**负控制**：同一个三元组在另一个牌号上必须失败，否则「通过」没有信息量。

### 拒绝臂（不是静默忽略）

`3B7` / `N27`（有规格页但没印 PV 行）→ `DATA_UNAVAILABLE` + `points_printed = 0` + 一句
「nothing to score a triple against」，**不改成拟合**；不在表里的牌号 → `NOT_FOUND` + 9 个可选牌号；
`units` 拼错 → `ARG_INVALID`（不做 cgs 猜测）；`temperature_c` **只比厂商在该温度真印过的点**
（问 3C93 的 25 °C、3C90 的 25 °C 都是 `OUT_OF_VALIDITY` + `temperatures_printed`，不做温度插值；
3C93 问 140 °C 才得到 `points_evaluated=3`）；`k <= 0` → `ARG_RANGE`；表缺失/损坏 → `DATA_UNAVAILABLE`。

### 7 种「篡改表」负控制

`tests/test_loss_audit.py` 每次都现场写一份坏表：schema 错、负 Pv、qualifier 拼错、source id 指向不存在的
来源、截断到 6 点、`materials_without_points` 里的牌号却带着点、缺 `printed_as` —— 七种都必须报错，
**证明 loader 有牙**（否则「表是对的」这句话就没有证据）。

### 唯一被改动的既有答案

只有一处：`magnetics_material_core_loss` 对铁氧体报 `FIT_NOT_RESOLVED` 时的 **hint**，追加了
「If you have a triple, magnetics_core_loss_audit scores it against the N loss points this grade's
vendor actually printed」。`reason_code` 与 message **一个字都没动**（有测试钉住这两件事必须分开）。
`--selftest` 从 295/295 → **299/299**；全量回归 **3248 → 3277 例**（实测 `Ran 3277 tests in 1024.718s` / `OK (skipped=5)`）。

### 仍然没做的

不**给**三元组（差额就是上面那些理由）；印点之间**不插值、不外推**；不覆盖 DC 偏置损耗；
10 % 只是报告带，原始 `deviation_pct` 一律透出。

## [0.0.80] - 2026-11-02

**粉末芯的磁化曲线与温度曲线：厂商印了 125 行 B(H) 和 54 行 Δµ/µ，两条自己的式子在端点上差 54 %**
（新工具 `magnetics_powder_curve`，工具数 **177 → 178**；`dependencies = []` 仍成立）。

`not_implemented_yet` 的第一条从 `0.0.76` 挂到现在，原文是「No saturation CURVE of a real
material」。这一版把它做掉，做法是**把目录里已经印出来的系数读进表里**，一个都不猜：

```
DC 磁化（printed p.128–132）   B = [(a + bH + cH²)/(1 + dH + eH²)]^x     B 特斯拉, H 奥斯特
温度  （printed p.138–139）    Δµ/µ = a + bT + cT² + dT³ + eT⁴           T 摄氏度, 参考 25 °C
稳定性（printed p.15）         范围值，不是曲线 ⇒ 只用来交叉校验，不当曲线用
High DC Bias 变体（p.12/14）   同 B(H) 式，XFlux 26/40/60µ、Edge 26/60µ
```

29 组 / **125 行**磁化，9 组 / **54 行**温度，**49 行**印刷稳定性，**5 行**偏置变体。

**三条交叉校验（数字全部进数据文件与自检，不是文档里说说）**

1. **磁化 ≡ DC 偏置积分**：`B(H) = 1e-4·∫µ_r,diff dH`（中点法 20000 步），100 Oe 处全 125 行
   中位 **0.48 %**、61 个环型中位 **0.49 %**。超过 10 % 的**只有三行**：`MPP Toroids 550µ`
   0.2341 vs 0.5128 T（**54.34 %**）、`Kool Mµ EQ&LP 75µ` 16.1 %、`MPP Toroids 300µ` 14.6 %。
2. **温度 ≡ printed p.15 稳定性列**：60µ 九个材料，`µ(T)` 动态范围差最差 **0.045 pp**。
3. **printed p.25 Example 3**：60µ Kool Mµ `B(95 Oe) = 0.44126 T`（印 0.441，差 0.06 %）、
   `B(15.8 Oe) = 0.09149 T`（印 0.092，差 0.6 %）。
4. **一条被否决的方法**：想用 `µr = x·a^(x−1)·(b − a·d)/(µ0·79.5771)` 反推小信号磁导率 ——
   61 个环型**中位误差 36.9 %**，因为 `B(0) = a^x ≠ 0`（60µ 是 5.29 mT，当小信号模型用就错）。

**两条式子差 54 % 时听谁的：都报，不选**

`fits_disagree: true` + 两个读数 + 差值一起回给调用方。阈值 10 % 是量出来的：全表中位 0.48 %、
最差 54.34 %，10 % 正好把两个种群分开。**不取平均、不挑一个、不加权** —— 目录自己印了两条式子，
我们只负责把它们一起端出来并标明分歧。既有答案（`select_core` / `magnetics_dc_bias` /
`magnetics_material_core_loss` / `magnetics_toroid_mlt`）**零改动**。

**`H = 0` 一律拒绝，并给出理由**

`B(0) = a^x ≠ 0`，所以零偏置不是「磁化曲线的起点」。拒绝时回 `b_at_zero_h_t = 0.00529`
（60µ Kool Mµ），并把零偏置问题指向 `magnetics_dc_bias`。同一个理由就是上面那条 36.9 % 的否决。

**7 行带转捩点（`turns_over_at_h_oe`）**

印刷拟合在某个 H 之后不再物理（B 反而下降）。这些行的转捩点写进数据行，工具就地回答
「H = 500 Oe 时这个材料还行不行」：`MPP Toroids 550µ → 23.8 Oe`（唯一落在印刷 H 带内）、
`High Flux Toroids 160µ → 191.0`、`Edge Toroids 125µ → 268.0`、`90µ → 496.9`、
`75µ → 613.7`、`60µ → 852.6`、`MPP Toroids 200µ → 1224.8`。

**温度区间 `[−50, 200] °C`**

−50 是 p.15 印刷动态范围的下界，200 是 `MATERIALS RATED TO 200 °C` 的评级。
`(100, 200]` 照答但打 `inside_printed_dynamic_range: false`；`−60` **不是**拒绝下界（那只是图的轴）。
变体页（High DC Bias）**没有温度拟合** ⇒ 问就 `NOT_FOUND`，不套标准页的数。

**5 条印刷异常登记不改**（`kept_as_printed: true`）：MPP 温度 147/160/173µ 三行相同、
200/300µ 两行相同；Edge 温度 90µ 的 `d = −2.000E-21 / e = 1.000E-23` 明显是凑数位；
Edge 磁化 Toroids 40–125µ 五行退化成近线性（`a ≈ 1.000E-03`、`x 0.833–1.021`）；
`MPP Toroids 550µ` 的 `c = 1.118E-10` 且 100 Oe 与积分差 54.3 % —— 两个数都报，一个都不选。

**5 条不携带（`not_carried`）**：Bsat 无温度曲线（p.10 只有单值）；粉末芯无 Steinmetz、
无 DC 偏置修正；无单料号重量（p.16 印 `See individual data sheets`）；µ-vs-B / µ-vs-f 是范围
不是曲线；High DC Bias 页后两张 `Perm/a/b/c` 表（XFlux `443.53/2.015/1.312`、
Edge `83.23/2.106/1.520`）是另一种拟合式而页面没印公式。

**`include_evidence=True`** 把上面全部（三条校验的每个数字 + 5 条异常 + 5 条不携带 + 页号 + URL）
放进信封顶层，让「这个数凭什么信」能在软件里就地问出来，而不是只能翻文档。

**拒绝臂各自有码**：`H ≤ 0` / 无 h 无 t / 匝数与 h 同时给 / 匝数缺件 / 变体给了 shape →
`ARG_INVALID`（各自点名并回可选值）；材料印在多个形状下没给 shape → `ARG_INVALID` +
`shape_families_printed`；未印磁导率 / 未印材料 / 该材料无温度拟合 → `NOT_FOUND` +
`*_printed` 清单；温度越界 → `OUT_OF_VALIDITY`；表缺失或坏 → `DATA_UNAVAILABLE`。

测试与自检：`tests/test_powder_curve.py` **37 例**（表的来历与正性、7 个转捩行、**四种篡改
`_scratch` 副本的负控制**、p.25 例 3、温度对印刷数、两条式子分歧、8 条拒绝臂、匝数路线、
工具面与棘轮）；`--selftest` `288/288` → **`295/295`**（+7）。工具数 177 → **178**。

**全量回归 `Ran 3248 tests in 1236.909s` / `OK (skipped=5)`**（0.0.79 是 3211 例 / 1401.993 s，
`3211 + 37`）。

未做（诚实清单）：九个铁氧体材料的 Steinmetz 三元组（`steinmetz` 仍 `null`，问就拒）；
`magnetics_core_loss` 的 DC 偏置 Steinmetz 项（现状只给下界）；环型标称重量（厂商指向单料号
数据表，已收档）。

## [0.0.79] - 2026-11-01

**环型的平均匝长与铜损：目录不印的那个数，变成一条带区间的估计**
（新工具 `magnetics_toroid_mlt`，工具数 **176 → 177**；`magnetics_select_core` 加一个
`toroid_mlt` 参数；`dependencies = []` 仍成立）。

`0.0.76`／`0.0.78` 的 `not_implemented_yet` 里一直挂着同一条：「环型的平均匝长（因此环型没有铜损估计）」。
这一版把它做掉 —— 但**不是**用一条公式做掉：

```
T106-1（OD 26.92 / ID 14.48 / H 11.10）
  flat = 2H + (OD−ID)          = 34.64 mm         磁芯截面按矩形读的周长
  MLT  = flat × 1.2477…1.4605  = 43.22 … 50.59 mm 中位 47.32
  厂商在同一尺寸上印的 MLT      = 43.9 mm         ← 落在区间内，不落在中位上
  54 匝 φ1.0 mm 铜 @100 °C     R_dc 0.073719 Ω（0.067335…0.078821）
                               P_cu 1.842964 W（1.683375…1.970525）
```

**为什么只能给区间**：一匝有多长属于**绕组**（线径、绕法、层数），不属于磁芯 —— 厂商不印 MLT
是有理由的。厂商自己印的 43.9 mm 距离中位 47.32 还有 **7 %**，这不是误差，是同一个磁芯配不同
绕组的合法差别。所以答案里每个引用它的数都同时给 `range` 并标 `is_estimate`。

**为什么不是一条公式**：三个候选闭式都量过残差 —— `flat` 比印刷值**低 19.8–31.5 %**；
`2H + (OD+ID)/2` 残差 −4.8…+7.5 %；`2H + OD − 0.4·ID` 残差 −5.0…+2.5 %。后两个是**拿这 13 个点
自己拟合**的系数，误差好看是因为它就是拟合误差；当成「公式」写进答案等于把拟合噪声伪装成厂商
定义。采用「实测比值带」：`q ∈ [1.247656, 1.460472]`，中位 **1.365936**，n = **13**。

**数据**（`data/toroid_mlt_reference.json`，新文件，纯转录）：**13 行**印刷
`(OD, ID, H, MLT)`，每行带 `url`（Amidon / Profi-Electronic 逐页重印的 T 型数据表），
`source.how` 写明提取方式（页面剥标签后读 `le / Ae / Ve / WA / sa / mlt` 六个印刷量的最后一列）。
两个极值就是 `T184-6`（1.247656）与 `T520-26`（1.460472）；`T68-6` 恰好是中位。
`T300A-26` 印刷 `Ae = 338 cm²` 是掉小数点（它自己印的 `Ve/le = 67.0/19.8 = 3.38`）——
**登记不改**，`notes` 里写着。band **每次调用从 rows 现算**，文件里不存汇总（存了就会
和行漂移，自检用一条伪造行证明它真的在算）。

**顺带的两个几何自检**（同一批 13 页，写进 `le_basis` / `ae_basis` 的原文里）：

| 关系 | 印刷值残差（13 行） | 口径 |
|---|---|---|
| `le = π(OD+ID)/2` | **−0.61 % … +0.45 %** | 可以直接用 |
| `Ae = H(OD−ID)/2` | **+4.41 % … +9.69 %**（偏高） | 印刷截面是圆角，矩形**过填**；只说明量级，不替换印值 |

**铜的链路**（每个数都能手工复算）：

```
N1     = floor(π(ID − d)/d)                  铜只能绕着内孔排，单层最多这么多匝
layers = ceil(N / N1)                        超过一层 ⇒ 后续层更长 ⇒ r_dc 是下界（必须标记）
ρ(T)   = 1.724e-8 · (1 + 0.00393·(T − 20))    复用既有常数，不另配系数
R_dc   = ρ(T)·N·MLT/A_cu        P_cu = I_rms²·R_dc
window = π(ID/2)² ; needed = N·A_cu/ku
```

54 匝 φ1.0 mm 在 T106-1 上：`N1 = 42`、`layers = 2`（带 `multilayer_note`，含 `biased LOW`）、
`R_dc = 0.073719 Ω`；@20 °C 是 `0.056085 Ω`，比值恰好 `1 + 0.00393×80 = 1.3144`。
`ku = 0.4` 时 `needed = 106.028752 mm²` 放得下；`ku = 0.05` 时 `needed = 848.230016 mm²`
**放不下**，给 `window_fits: false` + `window_note`，写明「这个电阻是那么多匝**会**测到的值，
不是能绕出来的值」。

**`select_core(toroid_mlt="estimate")`**：零件、匝数、其余字段**全不动**（逐字节比过），
只多一个 winding（`mlt_mm` / `mlt_range_mm` / `mlt_is_estimate` / `r_dc_*` / `p_copper_*`）
与顶层 `toroid_mlt` 块（`mode/how/ratio_band/rows_with_an_estimate/rows_without_one/
why_only_toroids/not_a_printed_value/band_note`）。`off` 与**省略参数逐字节相同**
（`json.dumps(sort_keys=True)` 直接比）；`off` 时 winding 里仍保留「no mean turn length」那句，
`mlt_mm` **这个键根本不出现**。只对环型给估计：固体芯的 MLT 是线圈骨架的印刷平均匝长，
那是厂商自己的数（`why_only_toroids` 里写着）。粉末芯表每行新增
`mlt_flat_rect_mm` / `mlt_estimate_mm` / `mlt_range_mm`，`mlt_mm` 列**仍然是 `null`**。

**拒绝臂各自有码**：尺寸 `ID ≥ OD` / 线径 ≥ 内径 / 半匝 / 填充系数出界 → `ARG_RANGE`；
`turns` 没线径 / `i_rms_a` 与 `fill_factor` 没 `turns` / 非数 → `ARG_INVALID`；
表缺 → `DATA_UNAVAILABLE`（hint 点名文件名，**不退回默认 band**）。

测试与自检：`tests/test_toroid_mlt.py` **33 例**（13 行逐行复算、band 现算、`le`/`Ae` 残差、
区间与厂商值、铜与温度、`N1`/层数/窗口、9 条拒绝臂、`select_core` 三态逐字节、粉末行字段、
工具面与棘轮）；`--selftest` `282/282` → **`288/288`**（+6 条：几何与区间的来历、band 现算、
铜独立复算、温度关系、七条拒绝臂、`select_core` 三态）。工具数 176 → **177**
（`select_core` 只覆盖目录里的零件，用户自己的环没有入口）。

**全量回归 `Ran 3211 tests in 1401.993s` / `OK (skipped=5)`**（0.0.78 是 3178 例 / 1448.686 s，
`3178 + 33`）。

未做（诚实清单）：包漆与加固层厚度（让真实 `N1` 略小）、多层长度增长模型（现状给下界并标记）、
集肤与邻近（在 `magnetics_winding_ac`）、温升、litz / 铜箔 / 并绕。

## [0.0.78] - 2026-11-01

**DC 偏置接进 `select_core`：那个「0.0.77 不求解的不动点」做掉了**
（`magnetics_select_core` 加 3 个参数，工具数**仍是 176**；`dependencies = []` 仍成立）。

`0.0.77` 的 `not_implemented_yet` 里点着名的那一条原话是「DC 偏置能算 `% µi` 跌落，但**没接进
`select_core`**：这里给的匝数仍是零偏置的匝数，偏置下的匝数是一个 **0.0.77 不求解**的不动点」。
这一版把它闭合 —— 同一个问题换一个答案：

```
100 µH / 峰值 10 A / 60 µ 粉末环 / DC 偏置 8 A
  0.0.77（零偏置）  0077141A7  Kool Mµ      54 匝   235.3 µH（偏置下只剩约 42 %）
  0.0.78（偏置）    0074121A7  XFlux Ultra  82 匝   100.256 µH
                    H 200.085901 Oe，µ 42.600515 %，B 0.636789 T（限 1.28 T），带内
```

**为什么不是不动点**：`dL/dN = 0 ⇔ N^c = 2a/((c−2)·b·k^c)`，驻点**只在 `c > 2` 时存在**。
实测 `data/dc_bias_fit.json` 的 **125 行里 86 行 `c > 2`**（39 行 `c ≤ 2` 单调），越过峰以后
**更多匝数换来更少电感**：同一颗 XFlux Ultra 60µ / 8 A，82 匝 = **100.256 µH**，1000 匝上限只有
**22.73 µH**。所以不动点迭代会停在错的分支或两头振荡，改成**三步区间搜索**：
定位 `n_peak` → 在峰左侧（单调）二分找第一个达到 L 的匝数 → 只在峰值磁密超限时继续加匝
（同样 L 下匝数越多 `B` 越小，但峰存在 ⇒ 这个修法有终点）。

**口径三处改动，每处都能翻脸**：

| 项目 | 口径 | 为什么 |
|---|---|---|
| 通量 | `B = L_biased·Ipk/(N·Ae)`，**用偏置后的 L** | 偏置把 µ 压下去，同样电流储更少磁通；用零偏置 L 会虚高约 2.3 倍并报出不存在的饱和 |
| AL 余量 | 对**偏置后的 AL**（`AL₀×µ/100` = `35 nH × 42.6005 % = 14.91018 nH`）比，`al_ratio = 0.997446` | 对印刷 35 nH 比会得到 0.42 这个**不存在的 58 % 缺口**，还会误触 `gap_required`（粉末芯没有可切的气隙） |
| 带外 | `H` 出 10–800 Oe 时 `h_within_printed_band=false` + `h_band_note`（含 `EXTRAPOLATION`）+ 顶层点名，**不 clamp 不硬拒** | 夹到 800 Oe 会把跌落算浅；硬拒则大电流设计全部出局。实测 232 个候选里 7 个带外，最小那个在 1743.508039 Oe（µ 剩 0.451206 %） |

**做不到的零件按名字拒绝**，带诊断而不是带一个不存在的数：`dc_bias_unreachable` +
`turns_max_scanned` / `maximum_inductance_h` / `turns_at_maximum` / `percent_at_maximum` /
`h_oe_at_maximum` / `why`，且 `why` 区分两种原因（`c > 2`「过去峰就掉」vs `c ≤ 2`「一路涨到上限」）
——因为补救方向不同。电感够而磁密超时另给 `turns_reaching_l` 与 `b_peak_at_that_turns_t`。
样例：Kool Mµ `0077141A7` @8 A，1000 匝上限只有 **1.53129e-06 H**、µ 剩 **0.011779 %**。

**拒绝臂各自有码**（不是一条 `ARG_INVALID` 打天下）：偏置但没 `include_powder_cores` →
`ARG_INVALID`（hint 点名该参数）、偏置 > 峰值电流 → `ARG_RANGE`、`0` 偏置 → `ARG_RANGE`
（hint 说明「省略参数就是逐字节不变的 0.0.77 答案」）、非数 → `ARG_INVALID`、表缺 → `DATA_UNAVAILABLE`。
`dc_bias_turns_max` 默认 **1000**，是参数（自检用 `6` 做负控制：压到 6 匝必须报做不到）。
固体芯 `dc_bias = null` + `dc_bias_because` + `missing` 加 `dc_bias`，**不套粉末曲线**。
顶层 `dc_bias` 还带 `found_by`（`N^c = 2a/((c-2)*b*k^c)` 与 `86 of the 125`）、
`loss_fit_is_unbiased`（损耗系数是零预磁化下测的 ⇒ 偏置设计的 `core_loss_w` 是**下界**）、
`listed_with_a_fit`。

**零偏置零变化，用 sha256 证明**：把 HEAD 版 `magnetics.py` 拷成副本（改绝对导入、覆盖三个数据
路径常量），5 组用例（含全池 1939 行）新旧结果 JSON 的 **sha256 全部相同**。

测试与自检：`tests/test_select_core_dc_bias.py` **31 例**（无偏置不变含 288/288 与五个零件钉、
两个约束同时满足、`L(N)` 非单调与 `86/125`、`dc_bias_turns_max` 负控制、fit 真被读（改一行必须改答案）、
六类拒绝、固体芯说「没有」、带外标记、工具面与棘轮）；`--selftest` `278/278` → **`282/282`**（+4 条：
回路闭合与手算复核、非单调与达不到的形态、带外标记、五条拒绝臂 + 工具数）。

**全量回归 `Ran 3178 tests in 1448.686s` / `OK (skipped=5)`**（0.0.77 是 3147 例 / 1000.249 s，
`3147 + 31`）。

未做（照旧写清）：按温度（fit 页没有温度轴）、`µ(B)` 自洽饱和、固体芯的偏置曲线、偏置下的损耗
拟合、环型 MLT 与铜损（目录不印）、多目标优化（只给满足约束的最小匝数）。

## [0.0.77] - 2026-11-01

**DC 偏置：零电流算出来的电感，在真电流下还剩多少**
（新工具 `magnetics_dc_bias`，工具数 **175 → 176**；`magnetics_data` 多一个 `kind`；`dependencies = []` 仍成立）。

`0.0.76` 的 §8 第 2 条写着「DC 偏置下的 AL 跌落曲线：只在 p.10 印了两个点，做曲线要另一份资料」。
**另一份资料就在同一个 PDF 里**，只是不在上一轮翻的那三页上：
`Permeability versus DC Bias Curves — Fit Formula`（印刷 **63–65** = PDF **65–67**，这一段 `印刷页 = PDF 页 − 2`，
与粉末芯表段的 −1 不同；五个页脚样本逐条写进数据文件）。式子是一行：

```
% initial permeability = 1 / (a + b * H^c)          H 单位 Oersteds
```

**`a` 在 125 行上全是 0.01** ⇒ `H=0` 时正好 100，也就是说它输出的是**百分数本身**。
按 0–1 比例去解 80 %（`1/0.8` 而不是 `1/80`）会让 18 个数字**整体差 10 倍**——本轮先踩了。

**数据规模**（`data/dc_bias_fit.json`，新文件）：**29 个（材料 × 形状族）组、125 行** `(permeability, a, b, c)`，
10 条材料线（Kool Mµ 四支 / XFlux 两支 / Edge / High Flux / MPP）、4 个形状族
（Toroids 9 组、E/U/EER 7 组、EQ & LP 5 组、Blocks 8 组）。非对称是印刷事实：
`Kool Mµ Ultra` 与 `MPP` 没有 E/EQ 组，`MPP` 的 Blocks 只有 26 µ 一行。

**五条校验**（每条都能翻脸）：

| 校验 | 结果 |
|---|---|
| 厂商 p.10 的 80 %/50 % 汇总表（9 材料 × 2 档）| **18/18** 在 1 % 内；最差 **MPP 50 %：算 106.00 对印 105（0.96 %）**；15 个在 0.1 % 内、第 16 个 0.12 % |
| 印刷 Example 2（p.24：60µ / 20 匝 / 20 A / le 63.5 mm）| `H = 79.15824 Oe` → **58.408 %**，原文「from curve fit equation, % µi = 0.58」| 
| 印刷 Example 3（p.25：15.8 Oe）| 积分 **0.0936 T** 对印 0.092 T（+1.7 %）；这一条只钉量级——15.8 Oe 时曲线还有 96.5 %，弦式与积分只差 0.3 % |
| p.52 MPP Toroids 面板 10 条曲线的像素读数 | 坐标用**印刷网格线最小二乘自标定**（残差 ≤0.6 pt）、600 dpi 扫描；7 条在各自 5 个水平上 **0.11–1.08 %**，次优磁导率差 6–30 % ⇒ 归属无歧义 |
| `B` 必须**积分** | 95 Oe：积分 **0.4421 T** 对印 0.441 T（+0.25 %），弦式 `µr·H·1e-4` 只 **0.285 T（−35 %）**；63.3 Oe：0.3306 对 0.331 ⇒ `% µi` 是**微商（增量）磁导率** |

**上一轮那张表被明确否掉**：PDF 130–134「DC Magnetization」的 `B = x(a+bH+cH²)/(1+dH+eH²)` 是**另一个函数族**
（`x·c/e` 的渐近 B 超过材料饱和磁密，且不重现自己的曲线与算例），写进 `not_carried`，`B` 改由积分 µi 拟合得到。
它不是「厂商印错了」，语义是 B–H 大信号曲线，与 `% µi` 这个小信号增量量不是同一个对象。

**拒绝臂**（每条都是本来会被静默猜掉的分支）：磁导率**不插值**（MPP 55 µ → `NOT_FOUND` + 顶层
`permeabilities_printed` 14 项）；形状族**不猜**（材料只印一个族才自动取，多个必须点名并列出候选）；
`h_oe` 与 `turns/dc_current_a/le_mm` 二选一（静默取一条会藏起另一条）；`H ≤ 0` 拒；
`H` 出 10–800 Oe 带**报警不 clamp**（页面只印「Fit valid only for range shown on graph」不给数，
带宽是从 p.52 网格线量出来的**样品**，`warnings` 里这么写）；`% µi < 25` 与 `B/Bsat > 0.7` 各一条 warning。

`H` 由绕组算走 CGS `H[Oe] = 0.4π·N·I[A]/le[cm]`（`le` 给 mm，写成 mm 就整好差 10 倍）；
`b_t_basis` 每次把**弦式会是多少**一起打出来。

**测试与闸门**：`tests/test_dc_bias_fit.py` **35 例**（表形与 `a` 全 0.01、零偏置=100 %、两份 p.10 副本一致、
18 个数字、两个算例、积分非弦式与积分收敛、CGS 口径、六类拒绝、带外报警、两份错表与缺表、`include_b`、
饱和对照、工具注册与棘轮、能力自述）；`--selftest` **274/274 → 278/278**（四条新自检）；
工具数棘轮 **5 处 + `server.py` 两处** 由 175 改 176。
**全量回归 `Ran 3147 tests in 1000.249s` / `OK (skipped=5)`**（0.0.76 是 3112 例 / 1233.364 s，`3112 + 35`）。
`magnetics_data` 的三个既有 `kind`、`magnetics_select_core`、`magnetics_material_core_loss`、
`data/magnetic_materials.json`、`data/powder_cores.json` **一个字节没改**。

**未做**：把 DC 偏置接进 `select_core`（匝数↑ ⇒ H↑ ⇒ µ↓ ⇒ 电感↓ 是**不动点**，得迭代）；
按温度（目录只印一条拟合）；饱和建模（`% µi` 是小信号量，`b_t` 只作对照）；组级有效范围（页面不给数）。

---

## [0.0.76] - 2026-11-01

**粉末磁环的几何与 AL 入库——`magnetics_select_core` 第一次能选一颗"买得到的"环**
（同一工具扩参数，**工具数仍是 175**，`dependencies = []` 仍成立，`include_powder_cores` 默认 **False**）。

`0.0.55` 那张磁芯表只有 16 个铁氧体/块状芯，`0.0.74`/`0.0.75` 补的是粉末材料的**损耗系数**，
但粉末芯本身（环的 OD/ID/HT/Ae/Le/Ve/AL）一直没进表 —— 于是 `select_core` 面对"MPP 60 µ 100 µH 5 A"
只会说"表里没有"。这一版把厂商粉末芯目录（印刷 p.155–197）整本转录进来。

**数据规模**（`data/powder_cores.json`，新文件，99 KB）：**43 个尺寸、500 个磁导率行、4441 个非空格里
1923 个是真料号**（其余 2518 格厂商印 `-` 表示"该列不供此磁导率"）、1920 个不同料号字符串。
九列材料：Kool Mµ / Kool Mµ Hƒ / Kool Mµ MAX / Kool Mµ Ultra / XFlux / XFlux Ultra / Edge / High Flux / MPP。
PDF 页 156–198 ↔ 印刷页 155–197（**印刷页 = PDF 页 − 1**，43 块逐块断言）。

**取列按坐标、不按词序**——厂商对空单元格**根本不印**，按词序解析会在缺一格处整行右移，而错位后每行
照样"看起来九个值"。防它的是料号里的**材料码**：1923 格里 1921 与所在列一致，2 例外都是厂商自己印错。

**六条自校验**（每条都能失败）：

| 检查 | 结果 |
|---|---|
| `Ve = Ae·Le`（厂商同时印三个数） | **42/43** 在 2 % 内、41/43 在 0.7 % 内；唯一例外 p.193 是 **1.0152 的对偶**（0.9348），而该尺寸的 Ae、Le 各自被另两条检查确认 ⇒ 错的是印的 Ve |
| `Le` 对对数均值周长 `π(OD−ID)/ln(OD/ID)` | **43/43 < 1 %**（最差 0.97 %，p.178） |
| `Ae` 对外接矩形 `HT·(OD−ID)/2` | **43/43 ∈ (0.85, 1.0)**，实测 0.9034–0.9972 |
| 料号材料码 vs 列 | **1921/1923** |
| `AL/µ` 按 (页, 列) 分组取中位，偏离 > 12 % | **仅 3 处**，全在 p.158 的 perm 40 / AL 18（渲染成图确认**印刷就是 18**，该列中位 16） |
| 每列磁导率 ⊆ 厂商 p.10 印的范围 | 全部通过 |

**5 条印刷异常原样保留并登记**（每条都写了复核方式）：p.176 的 MPP 位印了 High Flux 料号、
p.191 的 Edge 位同样、p.162 的 `CO58031A2` 是**字母 O**（文本层字符码确认）、p.193 的几何自相矛盾、
**p.166 / p.168 同一个 `0058133A2` 印在两个不同尺寸上**（本轮新发现：1923 行只有 1920 个不同字符串，
多出的 3 个碰撞里两个是已知异常，第三个查出这个）。

**顺带补的两张厂商页**：p.10 九列 `sat_flux_density_t`（MPP 0.8 / High Flux 1.5 / Edge 1.5 / XFlux 1.6 /
XFlux Ultra 1.6 / Kool Mµ 四支 1.0）与 60 µ 的 50 kHz·1000 G 损耗密度；p.16 的 60 µ 密度
（5.8–7.8 g/cm³）与**11 项磁导率-密度因子**（14 µ 0.85 … 173 µ 1.08，**200/300/550 µ 没印因子**）。
质量 = `Ve × 密度(该列 60 µ) × 因子(µ)`；没因子的三档 **`mass_g = null` 拒绝插值**。

**接入 `select_core`**：`include_powder_cores=true` 把 43 个环并进池子，`permeability` 在此之后才收窄
（`core_pool` 回报 `parts` 与 `permeability_filter`，模型是**分布式气隙** ⇒ `gap_mm = null` +
`gap_is_distributed`）。MPP 60 µ / 100 µH / 5 A / 100 kHz / 0.05 T 作答
`0055141A2`：**601 匝**、`b_peak 0.639959 T`、`b_limit 0.64 T`（p.10 的 0.8 T 按该页**不印温度**打八折并写进字段）、
损耗走厂商 toroid 60 µ 拟合 × Ve = **0.0011 W**，三角波 duty 0.4 → **0.001029 W**（`iGSE shape ratio 0.93514`）。
**不伪造的**：`p_copper_w = null`（目录不印 MLT）、`gap_mm = null`、`not_modelled` 四条。

**修掉一条"状态码对、文案错"的答案**：`material="MPP", permeability=55` 原先报
`no core in the table uses material MPP`（池被磁导率过滤空，被误读成材料不存在）⇒ 现在先过材料存在性再收窄，
报 `NOT_FOUND: no powder core is printed with permeability 55` 并交出 `permeabilities_printed`
（14/19/26/40/60/75/90/125/147/160/173/200/300/550）。同一条 `extra` 也加到了
`magnetics_data(kind="powder_parts")` 的空结果分支。

**零变化证明**：`include_powder_cores=False`（默认）时对 `0.0.75` 源码逐位对照，
`select_core` **7/7 identical**、`magnetic_data` **3/3 identical**。`data/magnetic_materials.json` 只动 4 行：
MPP `bsat_mt_25c` 800、High Flux 1500，各带一条 `CHANGED in 0.0.76, and not silently` 的 note。

**测试与自检**：新增 `tests/test_powder_cores.py` **26 例**（打印网格 / 不信厂商的几何 / 列就是材料 /
展开的料号 / 选型 / 拒绝 / 补进去的饱和值七组）；`--selftest` **271/271 → 274/274**（三条新自检：
数据规模、唯一的 `Ve ≠ Ae·Le`、默认与粉末两条选型读数）。**全量回归 `Ran 3112 tests in 1233.364s` / `OK (skipped=5)`**
（0.0.75 是 3086 例 / 961.090 s，`3086 + 26`）。文档见 [docs/78](docs/78-粉末磁环几何与AL.md)。

**仍未做**（列在这里而不是假装做了）：环的 MLT 与铜损、DC 偏置下的 AL 跌落曲线、标称重量、
按温度的 Bsat、p.12/p.14 的 DC 磁化曲线（`B = x(a+bH+cH²)/(1+dH+eH²)`，厂商自己写"Not a B-H curve"，需要新语义）、
环窗口的绕组可行性（有 `window_area_mm2` 但没骨架数据，只报不判）。

## [0.0.75] - 2026-11-01

**XFlux / Edge 粉末材料与 High DC Bias 的独立印行**（同一工具 `magnetics_material_core_loss`，
**工具数仍是 175**，`dependencies = []` 仍成立）。

`0.0.74` 篇收尾时写着「XFlux / Edge 系数同 PDF 齐全但材料本身未入表」。这一版兑现它，
并且补上上一轮**整页漏读**的一半：XFlux 与 Edge 各有两条 **High DC Bias** 产品线，
它们的 `(a, b, c)` 印在**另外两页**（印刷 p.12 / p.14）而不是在 p.109–111 的形状表里。

一句话结论：**High DC Bias 不是基础线的插值**——拿基础行回答
「High DC Bias XFlux 60µ / 100 kHz / 0.1 T」给 `1315.52 mW/cm³`，真值 `1512.30`（高 **15.0 %**）；
Edge 那一条差得更多（`373.79` 对 `500.25`，高 **33.8 %**）。两次都是 `status: ok` 的错答案。

数据：`data/magnetic_materials.json` 的 `materials` 12 → **14**，`loss_fit` 61 → **93 行**
（XFlux 19 行：环 11 / E-U-EER 3 / EQ-LP 3 / 块 2；Edge 13 行：环 9 / E-U-EER 2 / EQ-LP 1 / 块 1，
其中 High DC Bias 5 行）。新增顶层 `density_vs_permeability_60u`（60µ 基准 + 11 个因子，
并显式列出厂商**没印因子**的 200/300/550µ）。材料常数入表：XFlux 700 °C / 6900 kg/m³ / 1.6 T，
Edge 500 °C / 7800 kg/m³ / 1.5 T。

**交叉核对用的是厂商自己印的第二个数字**，不是自证：p.10 的汇总表（60µ，1000 G）
XFlux @50 kHz 算出 **574.999** 对印刷 **575**（比值 1.0000）——一次同时证明转录正确、
`c` 是频率指数、`Bpk` 是半摆幅（把 1000 G 读成峰峰值给 `142.26`，只有印刷值的 **0.247 倍**）。
XFlux Ultra 差 **5.70 % / 3.41 %**、100 kHz 列 XFlux 差 **+2.8 %** 都没被宽容差吸收：前者登记为
**未解释**，后者是厂商汇总表三位有效数字的舍入。

一处**显式修正**：MPP 的 `density_kg_m3` 从 `8700` 改成 **`7800`**。一手目录印刷 p.16 印的是
7.8 g/cm³（同表 Edge 也印 7.8，渲染成图逐行复核过，不是抽取错位）。旧值改名保留为
`density_alloy_kg_m3`（**合金**密度，与压制后的**芯体**密度是两个物理量），`density_note` 以
`CHANGED in 0.0.75, and not silently` 开头 —— 这是本版唯一一处改变既有答案的地方。

一处**看起来像笔误、复核后不是**：Edge 90µ/125µ 的 `a = 481.77` 比 75µ 的 `121.47` 大 **3.97 倍**，
渲染成图再读一遍仍是 `481.77`，所以原样保留，并用测试断言「它不许被顺手修正」。

测试与自检：`tests/test_material_loss_fit.py` **31 → 44 例**（新增 p.10 交叉核对 5、High DC Bias
独立性 4、Edge 异常行 1、密度与材料常数 4），`--selftest` `268/268` → **`271/271`**（新增 3 条）。

没做（写在 [docs/77](docs/77-XFlux与Edge粉末芯.md) §8）：粉末芯**几何**未入 `cores`
（16 个核仍全是铁氧体，`select_core` 选不了磁环——这是当前最大缺口）；DC 磁化曲线
`B = x(a+bH+cH²)/(1+dH+eH²)`（p.12/p.14 共 5 组）未入表，因为它语义是 **B-H 曲线**不是损耗，
塞进同一工具会把两个量混成一个答案。

## [0.0.74] - 2026-11-01

**厂商自己印的那条损耗式子**（新工具 `magnetics_material_core_loss`，第 **175** 个）。
`magnetics_core_loss` 一直要求**调用方给** `k / alpha / beta`，因为仓库里没有 material-level 三元组。
粉末芯（Kool Mµ / MPP / High Flux）出厂印的本来就不是三元组，而是

```
PL = a * Bpk^b * f^c        PL 单位 mW/cm3，Bpk 用 TESLA，f 用 kHz
```

而且**同一材料不同磁导率、不同形状各有一行**（Kool Mµ 环 + E/U/EER + EQ/LP + 块 共 40 行）。
这一版把 Magnetics 官方粉末芯目录（210 页 PDF，印刷 p.109–111）里的 **61 行**系数入表，带印刷页码，
并新增一个**按名字查**的工具。

一句话结论：**"Kool Mu 的损耗"不是一个数**——同一形状族里 14µ 与 60µ 的 `a` 差 **1.45 倍**，
环与块差 **6.5 倍**；而最容易错的那一步不是系数、是**哪个指数是频率**：`c`（1.541）才是频率，
写反成 1.988 之后答案依然"像样"，只是差 **2.8 倍**。
**工具数棘轮 174 → 175**（新增一个工具，三处棘轮已同步）。

### 判据为什么是这几条（每条都能失败）

| 锚点 | 实测 | 它能抓住什么 |
| --- | --- | --- |
| **厂商自己的三个算例** | Kool Mµ 60µ 环 100 kHz：`Bpk = 0.014 / 0.055 / 0.092 T` → `11.0384 / 167.589 / 466.030 mW/cm³`，目录印 `11.0 / 168 / 466`（±0.5 %）| 录错一行系数、或读错单位约定（B 是 `T` 不是 `mT`）|
| **误差方向必须不一致** | 三条相对差 `+0.35 % / −0.24 % / +0.01 %` | 系统性偏（单位或某个指数错）会让三个点同向偏出几十个百分点，而不是在 3 位有效数字里来回 |
| **哪个指数是频率** | 频率 ×10 ⟹ 损耗 × `10^1.541 = 34.7536`；磁通 ×2 ⟹ × `2^1.988 = 3.9669`；断言与 `10^1.988` / `2^1.541` 的差 > 0.3 | 两个指数写反（这正是 `a·B^b·f^c` 这个写法最容易被顺手读错的地方）|
| **单位换算必须算出来** | `k_SI = a·10^(3−3c) = 1.0553675249259014`；`khz_t` 路径与 `si` 路径给出**逐位相同**的 `11038.403394 W/m³` | 手写一个常数、或把 `khz_mt` 当成 `khz_t`（差 `1000^b ≈ 1e6`）|
| **61 行每一行都能被自己解析回来** | 61 行全遍历：用该行自己的 `(variant, shape, permeability, 频率侧)` 查询必须拿回**同一个 dict 对象** | 变体/形状标签在转录时写错一个字母，就会让某行变成"不可达"或"两行同匹配"（抽样测不出来）|
| **欠定即拒绝** | 6 条拒绝臂各给**自己的** `reason_code`（`FIT_NOT_RESOLVED` ×4 / `NOT_FOUND` / `ARG_RANGE`），且都带候选行 | 随便挑一行回答（两个磁导率之间可能差一个量级）|
| **100 kHz 边界不插值** | Kool Mµ Ultra 环在 100 kHz **分裂**成 `<100`（`79.79`）与 `>100`（`11.83`）两行，**恰好 100 kHz 判无解**并说明"两侧都不声明边界"；`99 kHz` / `101 kHz` 都正常解析 | `>=` / `<=` 任一写法都会把边界静默归给一侧，而 100 kHz 正是开关电源的默认工作点 |
| **有效域说出来** | 拟合来自印刷 p.99–108 的曲线图，坐标轴 **5 kHz–2 MHz**、纵轴 **0.5 T**；出界只 `warnings`（并写明是外推），`>0.5 T` 另说"粉末芯在那里已接近饱和，而这条拟合没有饱和项" | 把曲线拟合读成保证值；或超出域外静默给数 |
| **旧答案一位不动** | SI 三元组仍是**精确** `130000 W/m³`（自检 `1876` 显式对照）；`steinmetz` 槽位**仍为 `null`** | 把厂商的 per-permeability 拟合塞进 material-level 的 `steinmetz` 槽位（那会让 `select_core` 静默换答案）|

### 语义决策

- **新工具而不是给旧工具加参数**：`material_core_loss` 的问题是"哪一行"，`core_loss` 的问题是"你给我三元组"。
  两者入口不同、拒绝理由不同，塞进一个工具必然要么撒谎、要么改既有语义。旧工具一行未改。
- **新增第四个单位约定 `khz_t`**（mW/cm³ + T + kHz）：厂商用**特斯拉**，仓库原有 `khz_mt` 是毫特斯拉。
  不给这个约定，调用方就得自己乘 `1e-3^b`——那是"静默错答案"的最佳产地。
- **解析逐层报错**：变体 → 形状 → 磁导率 → 频率侧，每层单独给话术（"prints no EQ/LP fit"、
  "no fit at 999 u for that shape/variant; the printed rows cover …"）。
  最有用的是"列出候选行"：调用方看得见自己少问了哪一维。
- **边界判无解而不是插值**：`79.79` 与 `11.83` 差 6.7 倍，两侧的拟合**不连续**，
  插值出一个中间值等于虚构一条厂商没印的曲线。
- **曲线拟合不冒充保证**：`not_a_spec` 原文摘录写进表、每次带进 `value` 的
  `fit_is_a_curve_fit_not_a_limit`，并往 `not_modelled` 追加"拟合自身有效域"。
- **`steinmetz` 槽位仍是 `null`**：`steinmetz_note` 改写成"null 是刻意的——该槽位是 material-level
  SI 三元组，厂商不那样出版"，并指向 `loss_fit`。

### 口径

- 数据：`data/magnetic_materials.json` 的 `materials[*].loss_fit`（**61 行**：Kool Mu 40 / MPP 10 /
  High Flux 11），每行带 `printed_page`；`sources` 指向 PDF 的 `#page=109/110/111`。
- `tests/test_material_loss_fit.py` **31 例**（算例 2 / 指数方向 2 / 单位 3 / 61 行可达性 4 /
  拒绝 11 / 警告 3 / 波形与注册 3 / 零变化 3），耗时 **0.06 s**；
  selftest `262/262` → **`268/268`**（+6 条自检，id `1873`–`1878`）。
- 全量回归 `Ran 3073 tests`（`3042 + 31`）。
- `dependencies = []` 仍成立（只用 stdlib 的 `json` / `math` / `io`）。
- 工具数棘轮：`tests/test_vibration_mass_copper.py`、`tests/test_core_loss_igse.py`、
  `tests/test_fringing_loss.py` 三处断言 **175**；`README.md` 的「工具（175 个）」与目录树注释同步。
- **本轮没做的**：XFlux / Edge 系数在同一 PDF 里齐全，但材料本身（Bsat / Tc / 密度）未入表 ⇒
  只入系数会出现"能算损耗、查不到材料"的半个材料；九个铁氧体没有厂商拟合（它们出版的是另一种对象，
  不许凭记忆填）；**粉末芯几何未入 `cores`** ⇒ `select_core` 仍选不了磁环（这是当前最大的磁材缺口）。
- 逐条口径见 [docs/76](docs/76-厂商损耗系数表.md)。

## [0.0.73] - 2026-11-01

**自述跟不上代码**（纯文案，数值与工具数不变）。0.0.72 落地后三处自述仍是旧的：
`calc_capabilities` 的 `not_implemented_yet` 第 1 条、`magnetics_winding_ac` 的 `not_modelled`
与 docstring、`magnetics_select_core` 的 `not_modelled`。这一版把它们改成事实并加交叉引用
（都指向 `magnetics_fringing_loss`，并说明"铜损算了"与"磁通/电感没修正"是两件事）。
selftest `262/262`，工具数仍 174。

## [0.0.72] - 2026-11-01

**气隙之外那一圈**（新工具 `magnetics_fringing_loss`，第 **174** 个）。`magnetics_winding_ac` 的
Dowell 公式假设绕组周围是一维、平行于箔面的场——带气隙的电感里这个假设不成立，磁通从隙边缘**鼓出来**
横穿绕组，而这一项在 gapped inductor 里经常是**最大的一项**（`winding_ac_resistance` 的 `not_modelled`
第一句就写着 no gap/fringing field）。这一版把那一项算出来，并把它的每一个假设都做成能失败的检查。

一句话结论：**气隙边缘场不是 Dowell 的邻近项，它是另一个场**——同一绕组（4×0.05 mm、100 kHz、25 A）
上它是 `I²R_dc(F_R−1) = 0.0105 W` 的 **6.2 倍**（`0.0653932 W`，占自身 DC 铜损 `3.58 %`）；
而那个"薄板式高估 36 倍"是**峰值/rms 混算**的产物，真实是 **18 倍**（`t=1 mm`/`100 kHz` 时 `g(D)=0.0556`）。
**工具数棘轮 173 → 174**（新增一个工具，两处棘轮已同步）。

### 判据为什么是这几条（每条都能失败）

| 锚点 | 实测 | 它能抓住什么 |
| --- | --- | --- |
| **场是真解，不是形状拟合** | 两条带电条带的 2-D 场对 Biot–Savart 4000 段求积 **< 3e-5**；`∂H_z/∂z + ∂H_x/∂x` 与 `∂H_z/∂x − ∂H_x/∂z` 都 **< 1e-5**（势场）；`x=±a` 处 `H_z → q/2`（半无限面，二次收敛）；远场 `H_z ∝ 1/z²`（偶极，斜率 `±0.06`） | 直接写一个 `exp(-2z/g)` 那种"看起来像"的衰减因子 |
| **`q` 不能假设隙内均匀** | 轴向积分闭式 `G(a,g) = (a·log1p((g/a)²) + 2g·atan(a/g))/π`，对求积 **2.6e-5**，`g/a→0` 时**精确**趋于 `q·g`；baseline `gap_mmf_fraction 0.936756`、`pole_concentration 1.067514` | 拿 `B = μ0NI/g` 当唯一场（于是 `pole_concentration ≡ 1`，系统性低估 6.75 %）|
| **精确板因子** | `g(D) = 3(sinhD−sinD)/(D³(sinh²(D/2)+cos²(D/2)))` 对 4001 点复积分 **2e-7**；`D<1e-2` 走级数 `1−D⁴(1/24−1/840)`（两个 O(D) 立方相消）| 一路乘薄板式 `σω²μ0²t²/12`（1 mm 上高估 18 倍）|
| **口径定死 rms** | `factor_rms = 2·factor_peak`；`exact_rms/thin_rms → 1.0000`（`1e-7`）| 一半用峰值场一半用 rms ⇒ 差 2 倍，且两种错看起来都像"系数问题" |
| **薄板式是极限不是另一条公式** | `g(1e-6) = 1 − 4e-26` ⇒ `D→0` 精确退回教科书式 | 两个解各写一遍、只验一个方向（本轮就是靠这条抓到那个 2 倍）|
| **三条没拟合过的物理关系** | 20 kHz/100 kHz = **0.0400051 ≈ 1/25**（薄箔 ∝ `f²`）；standoff 0→2 mm 单调降且 **2 mm/0 mm = 0.0913**；绕组高度 6→40 mm 只差 **2.02 %**（`1/z⁴` 尾巴）| 一个拟合式不会有这三种行为 |
| **旧答案一位不动** | `magnetics_winding_ac` 同参数仍 `fr 1.00575 = 1.00029 + 0.00546`（自检 `1869` 显式对照）| 顺手把旧工具也改成"含边缘场" |
| **圆线拒绝但给原料** | 拒绝箔片公式回答圆线，交出 `field_weight_a2_m` + `probe_radii_mm`（每点 `peak_field_a_m` 与 1 mm 箔的损耗）| 拿箔片答案静默回答圆线 |
| **负控制（拒绝而不是忽略）** | MMF 二选一（都给/都不给 ⇒ `ARG_MISSING`）；ρ 与温度同给 ⇒ `ARG_INVALID`；`layers=201` / 负 `standoff` / 非正几何 ⇒ `ARG_RANGE`；`gap > leg_width/2` ⇒ **警告**（前提没了但算术还算得出）| 接受一个参数然后按默认回答 |

### 语义决策

- **`layers` 同时是壳数与匝数**：分层是同轴箔壳，一壳一匝 ⇒ `MMF = layers × I_rms`。不做"壳数 ≠ 匝数"
  的自由度（那样 DC 电阻与 MMF 会自相矛盾）；按实测 MMF 缩放请走 `ampere_turns`。
- **两种损耗按功率相加、无交叉项**：Dowell 自身场的邻近项与本项相加，真实合损耗不是精确的和——
  这条写进 `not_modelled`，不伪造一个"复合场"解。
- **径向分量没有静默忽略，而是量化**：`radial_over_axial_at_0p2g = 0.3213` 进返回值也进文案
  （"Here it is 0.32 of the axial component"）。它驱动**跨层闭合**的环流，因子依赖叠层几何而非箔厚，
  不是一个能乘进去的系数。
- **隙变宽总损耗反而变小**：0.2→2 mm 是 `0.0803 → 0.0505 W`。`q` 掉得比"面积变大"快。
  同时 `pole_concentration` 上升（0.8759 → 1.1417），所以**不能只看 `q`**——两条都打印。
- **高度几乎无关是物理结论**（`1/z⁴` 尾巴），不是数值巧合；它被当成自检的一条。

### 口径

- 新工具，旧工具一行未改：`magnetics_winding_ac` / `magnetics_dcr` / `magnetics_core_loss` /
  `magnetics_select_core` 全部原样。
- `tests/test_fringing_loss.py` **35 例**（场闭式 6 / `G` 3 / 板因子 4 / 工具 13 / 拒绝 13 / 注册 4），
  总耗时 **0.4 s**；selftest `257/257` → **`262/262`**（+5 条自检，id `1868`–`1872`）。
- 全量回归 `Ran 3042 tests`（`3007 + 35`）。
- `dependencies = []` 仍成立（只用 `math` 的 `atan` / `log1p` / `sinh` / `atan`）。
- 工具数棘轮：`tests/test_vibration_mass_copper.py`、`tests/test_core_loss_igse.py`、
  `tests/test_fringing_loss.py` 三处断言 **174**；`README.md` 的「工具（174 个）」与目录树注释同步。
- 逐条口径见 [docs/75](docs/75-气隙边缘场损耗.md)。

## [0.0.71] - 2026-11-01

**正弦不是唯一形状**（`magnetics_core_loss` 新增 `waveform` / `duty` / `segments`；
`magnetics_select_core` 新增 `flux_waveform` / `duty`）。`Pv = k·f^α·B^β` 是一条**正弦**的拟合曲线，
而电感里的磁通基本都不是正弦（Buck / 反激是三角，PFC 是低频绝对正弦上叠高频三角）。
这一版把那条曲线换成**对你给的那条轨迹逐段积分**的 iGSE，并且把它**归一到你自己的 Steinmetz 三元组**上。

一句话结论：**三角波被正弦公式高估多少，由 α 决定，不是一个常数**——α=2.7 高估 **33 %**
（`ratio_to_sine 0.666230`）、α=1.4 高估 **6.8 %**（`0.932129`）、α=0.7 **低估 3.5 %**
（`1.035020`）、α=1 **精确相等**；流传的"三角是正弦的 0.378 倍"是**把 `ΔB` 当幅值读**，
iGSE 的 `ΔB_pp` 是**峰峰值**，同峰值下正确值 `0.932129`。**工具数棘轮仍是 173**
（两个工具都只是加参数，没有新工具）。

### 判据为什么是这几条（每条都能失败）

| 锚点 | 实测 | 它能抓住什么 |
| --- | --- | --- |
| **默认 = 正弦到每一位** | 不给 `waveform`：360/360 成功案例 + 12/12 错误案例与 `0.0.70` 逐字节相同；输出里**没有** `waveform` / `igse` / `ratio_to_sine` 这些键 | 把正弦路径也改成"走 iGSE、只是形状是正弦" |
| **`k_i` 自证 I** | `I_α` 闭式对 `∫₀^{2π}\|cos\|^α dθ` 的 400001 点求积，相对差 <`1e-8`；`I_α(1)=4.0` 精确 | 闭式写错、或把 `Γ` 直接算（α 大时溢出） |
| **`k_i` 自证 II** | 正弦时 iGSE 对 **B(t) 直接数值积分** vs Steinmetz 相对差 `5.7e-11`（断言 `1e-9`）| 归一化常数抄错/记错（文献正文没取到） |
| 三角闭式 | `1859840.0 W/m³`，对轨迹数值积分 `1e-5`（受 `_fmt` 6 位有效数字限制）| 闭式与积分各写一遍、只验一处 |
| **方向由 α 的凸性决定** | α=1.4 / 2.7 `overestimate`、α=0.7 `underestimate`、α=1 `equal`（`ratio=1.0`，断言 `1e-12`）| 无条件断言"三角比正弦低" |
| 比率与 f、ΔB 无关 | 同形状换 `f`、换 `ΔB`，`ratio_to_sine` 不变（`1e-9`）| 把 `f`、`ΔB` 留在比率里（`α=1` 那条例检立刻失败）|
| 分段 == 闭式 | duty 0.5 拆成两行 `[[0.2,0.5],[-0.2,0.5]]`，`P_v` 与三角**逐位相同**（`1e-12`）| 分段实现引入额外常数 |
| 形状真的被看见 | 8 段阶梯占正弦的 `0.977559`（低 2.2 %）、带 5 % 边沿方波 `1.019154`（比三角 `1859840.0` 高 9.3 %），三者 `ΔB_pp` 都是 `0.2 T` | 只按峰峰值算（iGSE 退化成 Steinmetz）|
| 单位约定仍一致 | `si` / `khz_mt` / `khz_gauss` 三角形同值（`1e-9`）| 换单位结论就变 |
| `select_core` 只缩损耗 | B66283：`0.106066 → 0.100831`（比率 `0.9506439` = `_igse_shape_ratio(1.3, duty .5)`），`turns 25` / `b_peak_t 0.305344` / `gap 0.297616` **全不动** | 用形状去改匝数、改磁密限、改气隙 |
| **负控制（拒绝而不是忽略）** | 未知 `waveform` / `duty` 配 sine / `segments` 配 triangle 或 sine ⇒ `ARG_INVALID`；`duty∉(0,1)` / dt 和不等于 1 / 轨迹不闭合 / `α,β≤0` ⇒ `ARG_RANGE`；只有 1 行、`b_peak_t` 与峰峰值矛盾 ⇒ `ARG_INVALID` | 接受一个参数然后按默认形状回答——靠遗漏说谎 |

### 语义决策

- **`k_i` 是解出来的，不是抄来的**：文献（Li/Abdallah/Sullivan 2001，Xplore `955931`）正文这一轮取不到，
  所以按"正弦必须还原你的 Steinmetz"解 `k_i = k/[(2π)^(α−1)·2^(β−α)·I_α]`，再用对 `B(t)` 的独立数值积分
  验到 `5.7e-11`。文档写的是"定义来自该文、归一化常数自行推导并数值验证"，**不写"照抄文献常数"**。
- **`ΔB` 是峰峰值**：`flux_swing_pp_t` 与实际喂进积分的 `dB_pp_t` 一起打印，段表里每行带 `dB_dt_t_s`，
  任何人可以自己复算 `Σ k_i\|dB/dt\|^α ΔB_pp^(β−α) Δt`。**不给单一"形状系数"**——它随 α 与 duty 变。
- **非正弦必须同时打印"偷懒答案"**：`P_v_sine_equiv_w_m3` + `ratio_to_sine` + `sine_equiv_is`（`overestimate` /
  `underestimate` / `equal`）。商业场景里用户会拿旧数字对比，方向与幅度必须出现在同一个信封里。
- **`select_core` 只接受 sine / triangle**：`piecewise` 明确拒绝并指向 `magnetics_core_loss(segments=)`——
  选型判的是**形状**，任意轨迹是一次具体计算，不是选型判据。
- **`select_core` 的 α 取 `loss_exponents[0]`**：`core_loss_w` 本来就按 `f^x·B^y` 从参考点缩放，那个 `x`
  就是 Steinmetz 的频率指数，与 iGSE 的 α 是同一个数。这件事写在 `flux_waveform.shape_ratio_basis` 里，不靠猜。
- **没有 `loss_exponents` 时只拒损耗这一个数**：`core_loss_w` 置 `null` 且 `core_loss_basis` 点名
  `loss_exponents`，但**选型本身照常回答**（`status` 仍 `ok`，turns / 饱和 / 气隙都在）。
- **`not_modelled` 换掉一条、补一条**：非正弦时删掉"non-sinusoidal flux"（已经算了），补上
  DC 偏置、窄脉冲高估（`duty<0.05` 只给方向）等具名条目；正弦路径的列表**逐字不变**。

### 口径

- 同模块加参数：不给 `waveform` 时 `core_loss_steinmetz` 的**每一个字段**与 `0.0.70` 完全相同
  （`_scratch/cmp071.py` 自证 360/360 + 12 错误臂）；`select_core` 默认路径 240/240 逐字节相同
  （`_scratch/cmp071b.py`）。`skin` / `roundwire` / `acthermal` / `oracle` **一行未改**。
- `tests/test_core_loss_igse.py` **34 例**（`I_α` 2 / 正弦还原 2 / 三角 6 / 分段 4 / 单位 1 / 拒绝 9 /
  `select_core` 5 / 工具 5）；selftest `252/252` → **`257/257`**（+5 条自检）。
- 成本：纯算术无迭代，34 例测试文件总耗时 **1.9 s**；正弦路径零新增开销。
- `dependencies = []` 仍成立（只用 `math`，`I_α` 走 `lgamma`）。
- 顺手修正 `calc_capabilities.not_implemented_yet` 里一条**过期**的自述："no core SELECTION and no
  material table"——`magnetics_select_core` 与 `data/magnetic_materials.json` 早就在（11 材料 / 16 磁芯），
  真实缺口是**表里没有 Steinmetz 三元组**（只有 7/16 有单点实测损耗），已改成这句话。
- 逐条口径见 [docs/74](docs/74-iGSE-非正弦磁通的磁芯损耗.md)。

## [0.0.70] - 2026-11-01

**铜不是一个厚度**（`board_ir_drop` 新增 `frequency_hz` / `faces` / `mu_r` / `roughness_rq_um`）。
`69`/`70` 已经能算"一块铜在某个频率上是多少欧姆"，`71`/`72` 把这件事接到了自热上，但都停在
**一根导体**。真正被问到的是**一块板**：20 mm 的 1 oz 走线、0.3 mm/25 µm 的过孔、一层 70 µm 的
外层铜——它们不是同一个厚度，因此不是同一个比率。这一版把那个比率接到板级求解器上。

一句话结论：**只缩放每条边的电阻、不重解场**（拓扑与 DC 路径一行没动 ⇒ 不给频率时
**逐字节**与 `0.0.69` 相同），1 oz 铜在 **1 MHz 就差 `0.697 %`**、70 µm 在 1 MHz 差 **`10.68 %`**
（`t_eff` 与厚度**无关**，所以叠铜不带线性收益）；过孔走**管**解，1 MHz 上它与薄片只差 `0.01 %`
（诚实：等于没有），1 GHz 上差 **`7.05 %`**，而 DC 上 **5.26 倍**的差是**另一件事**，不许混着引。
**工具数棘轮仍是 173**（同一个工具加参数，不是新工具）。

### 判据为什么是这几条（每条都能失败）

| 锚点 | 实测 | 它能抓住什么 |
| --- | --- | --- |
| **默认关 = DC 到每一位** | 8/8 案例（含 4 个铺铜/CG）`max_drop_mv` / `total_copper_loss_w` / `not_computed` / `edges` 全等，`ac_summary` 键根本不出现 | 任何"顺手把 DC 也改成 AC 形状"的实现 |
| 单段 1 MHz | `19.702857 mV` → `19.840259 mV`（×`1.006973727`），`δ 66.082849628 µm`，`t_eff 34.75760990931462 µm` | 量纲/系数错；或错把 `δ` 当厚度 |
| **每段自己的比率** | 35 µm `1.006973727` 与 70 µm `1.106809163` 并存，总 `16.651231 mV`；"整板一个比率"的错答案差 **>1e-3 mV** | 取最小/平均/第一个厚度 |
| 过孔走管 | 跨层 10+10 mm + 0.3/25 µm：`20.979986` → `21.119535 mV`，via `1.001681046` 对 track `1.006973727`（差 4 倍）| 给过孔套薄片公式 |
| **比率与独立算例对表** | 走线段比率 == `skin.ac_sheet_resistance` 的 `r_ac_over_r_dc`（`places=9`）；过孔比率 == `roundwire.ac_round_conductor` 的 `ac_over_dc` | 自己再写一遍闭式公式，然后慢慢漂移 |
| 粗糙度必须自己乘 | 17.5 µm @1 GHz：无 Rq `8.374313654`、Rq 1 µm `10.028273309`（+20 %） | 直接取 `r_ac_over_r_dc`（它**不含**粗糙度）会静默丢掉 |
| `faces` 不是"省一半" | 1 GHz：`16.748629747` → `8.374313654`（0.5）；1 MHz：`1.006973727` → `1.000437082`，`(two−1)/(one−1)=0.06268 ≈ 1/16`（低频展开 `4s⁴/45`） | 按 0.5 外推 transition 区 |
| 铺铜同待遇 | `0.935986` → `0.942512 mV`，`sheet_ohm_per_sq 0.000492571 → 0.000496006`，CG 仍 52 次 / 残差 `7.27e-10` | 改场求解器（迭代数会变）；或漏掉铺铜的 `t_eff` |
| 单调 | 1e4…1e9 Hz：`16.054334 / 16.060492 / 16.651231 / 33.209124 / 108.928522 / 344.268298 mV` | 符号/方向错 |
| **负控制（拒绝而不是忽略）** | `f=0` ⇒ `ARG_RANGE`；`faces="three"` ⇒ `ARG_INVALID`（含 `factor of two`）；**`faces="two"` 无 f ⇒ `ARG_INVALID`**；`mu_r<1` / `Rq<0` ⇒ `ARG_RANGE` | 接受一个参数然后按 DC 回答——靠遗漏说谎 |

### 语义决策

- **只缩放电阻、不重解场**：频率改变的是"边的阻值"，网络求解器自己会在改过的边上重算电流分配。
  这就是"默认关逐字节不变"能**逐字节证明**而不是"容差内"的原因。
- **没有单一顶层 `r_ac_over_rdc`**：不同厚度比率不同，给一个数就是骗人。改为 `ac_summary` 里的
  `ratio_min`/`ratio_max` + 逐段 `sum_r_dc_ohm`/`sum_r_ac_ohm`。
- **键名刻意是 `sum_r_*`**：它是"逐段求和"，不是网络等效电阻（后者要考虑并联铺铜与分流，
  那是求解器给的 `max_drop_mv`）。叫 `r_total` 会被人当网络电阻引用。
- **`copper_um` 元数据永远是物理厚度**：`worst_current_density` 的口径不能因为给了个频率就悄悄变义；
  峰值比 DC 平均高 `ratio_max` 倍这件事写在 `warnings` 里，不偷偷改口径。
- **只给 `faces`/`mu_r`/`roughness_rq_um` 而不给频率 ⇒ 拒绝**，不忽略：`faces` 的两倍是
  "一面还是两面"这个物理事实，不是化妆品。
- `not_computed` 有频率时**移除** `"skin effect"`、**加上** proximity 与"宽条横向拥挤"两条；
  无频率时逐字保留原列表与原次序（含 zone 分支插在 index 0 的那条）。

### 口径

- 同模块加参数：`frequency_hz=None` 时 `board_ir_drop` 的**每一个字段**与 `0.0.69` 完全相同
  （`_scratch/cmp069.py` 自证 8/8）；`skin` / `roundwire` / `zonefield` / `power` **一行未改**。
- `tests/test_board_ir_drop_ac.py` **24 例**（默认即 DC 3 条 / 单段锚 1 / 两比率 2 / 管解 2 /
  faces+Rq 3 / 铺铜 2 / 8 条拒绝 / schema 3）；selftest `247/247` → **`252/252`**（+5 条自检）。
- 成本：每块板每种厚度（每种孔/镀层组合）**一次** 1-D 解（含 3 次 `_bisect_ratio` 200 步）并缓存，
  24 例测试文件总耗时 **2.0 s**；DC 路径成本与 `0.0.69` 相同。
- `dependencies = []` 仍成立（只用 `math`）。
- 逐条口径见 [docs/73](docs/73-铜在频率上的压降与铜损.md)。

## [0.0.69] - 2026-11-01

**一个脉冲串不是一个频率**（`ac_self_heating` 新增 `harmonics` / `dc_current_a`）。0.0.68 让它接受
"一个频率 + 一个电流"，但最需要问这句话的场合恰恰不是单频：buck 的纹波、PWM 方波的边沿、
DC 偏置上叠一条几百 kHz。把这些折成"一个频率 + 一个 RMS 电流"是行业里常见的偷懒，这一版
**把偷懒的代价量出来**并给出替代。

一句话结论：**每个谐波在同一根铜里各自耗散 `I_k²·R(f_k)`，所以功率相加、温度不相加；
buck 纹波偷懒只差 `0.0718 %`（可以偷懒），全 GHz 频谱差 `94.66 %`（全当 DC）与 `10.05 %`
（全压最大那一项），而且两者都**低估**；9 A DC 与 9 A@100 Hz 各自稳到 `436.506 K`，
合起来冷增益 `1.2544` 直接跑飞——回路增益是**相加**的。** 工具数棘轮**仍是 173**
（同一个工具加参数，不是新工具）。

### 判据为什么是这几条（每条都能失败）

| 锚点 | 实测 | 它能抓住什么 |
| --- | --- | --- |
| **单谐波 ≡ 单频** | `dT 1.83292 K`、`P 0.0916456 W`、`β 0.986895` **逐位 `==`**；5 个形状维度全 `==`（sheet 1.58531 / 两面 0.788378 / 粗糙 1.61693 / 实心 0.506997 / 管 0.146779） | 任何"重新推一遍"而不是"同一套算术"的实现 |
| 功率相加 | buck 5 项 `P = 0.0119286 W`、`I_rms = 1.08784 A`、`T = 25.7157 ℃` | 取最大项、或 RMS 乘单一电阻 |
| DC 项看整个截面 | DC 项 `R/R_dc` **恰为 `1.0`**、功率占比 `0.8444`；3.5 MHz 项 `1.07919` | 给所有项乘同一个 `R/R_dc`（会让占 84 % 功率的那项凭空多 6.3 %） |
| 指数是测量 + 恒等式 | 全高频 `β = 0.5`、`beta_deviation 7.97e-11`；`dlnP_dlnrho_weighted_check` 与实测恒等（容差 `1e-9`） | 硬编码 0.5、按项数平均、拿 `_sig` 后的功率加权 |
| 两个简化模型 | buck `0.0718 %` / `0.0957 %`（**控制**：不是永远很大）；全高频 `94.66 %` / `10.05 %`，方向都是 `under` | 只报一个数不给方向；或把差读成误差棒 |
| **负控制（增益相加）** | 9 A@100 Hz 单独：`gain 0.627201`、`dT 436.506 K`；**9 A DC + 9 A@100 Hz**：`THERMAL_RUNAWAY`、`gain_cold 1.2544`（= 两倍） | 只看最大一项、或先算 RMS 再查表——都会判成安全 |
| transition 不猜 | 1 MHz / 35 µm：`regime transition` ⇒ `beta_expectation = null` + 说明（实测 β 0.987） | 把 0.987 就近说成 1 |
| 14 条拒绝 | `current_a`+`harmonics` 同给、顶层 `frequency_hz`+`harmonics`、空表、**只给 `dc_current_a`**、**同频两条电流**、0 Hz、未知键、缺电流、缺频率、负幅值 ×2、非 list、条目非 object、>32 条 | 每一处都是"频谱可以被猜"的地方 |

「同一频率两条电流」必须拒：两条同频电流要按**相量**相加，本模型没有相位；
把幅值相加最多把损耗高估 2 倍。「只给 `dc_current_a`」也必须拒：那是
`electrothermal_conductor` / `electrothermal_trace` 的活（这条的判断要排在
`current_a` 必填检查**之前**，否则先报 `ARG_REQUIRED`）。

### 逐位不变是怎么做到的

`(a)` 多频用 `Σ (I_k²·Rth)·R_k` 逐项累加——**逐项的 `k` 与 `r` 与单频路径的数字一字不差**，
只是多了一项；`(b)` 只有一项时 β 直接返回 `_beta_at()`，不走 `w·b/tot`（那会在末位漂 1 ulp）。
DC 项走 `_r_dc()`，`R_dc ∝ ρ` 一次方 ⇒ 它的 `dlnR/dlnρ` **精确是 1.0**。

### 单频行为的可见变化（只有三处，数值一个没动）

这是同一个工具改了内部，所以必须自证"既有答案没变"。做法：从 `8c0f9dd` 取出旧模块注入
同一进程，22 个案例两边跑 JSON 对比 —— **9/22 逐字相同，13 个 DIFF 全部落在下面三处**：
全部数值（`dT`/`P`/`β`/增益/迭代数/熔点余量）、所有 `checks`、runaway 文案**逐字未变**。

| 变化 | 内容 |
| --- | --- |
| 新键 | `value.model_kind = "one_frequency"`（频谱是 `"spectrum"`）——两种模式 `value` 形状不同，需要判别键 |
| `limits[5]` 尾部 | 原文一字未删，只在后面追加 `(harmonics=[{frequency_hz, current_a}], dc_current_a=<A>)` |
| required 消息 | `current_a is required` → `… (for a spectrum give harmonics and/or dc_current_a)` |

### 语义决策

- 多频**不输出** `resistance_*` / `r_over_rdc_*` / `skin_depth_*` / `regime_*` /
  `unit_gain_current_a` / `headroom_current`：这些键在多频下**没有对应物**，置 `None`
  会被读成"算不出来"，所以直接不出现；代之以 `terms[]`（13 字段）+
  `unit_gain_current_scale = sqrt(1/cold_gain)`（把每个幅值乘上它，冷增益正好到 1）。
- 「全当 DC」叫 `all_dc_model_rel_diff`，**不叫** `dc_linear_model_rel_diff`——后者的语义是
  "保持同一冷电阻、只丢掉指数"，多频丢的东西更多（连频率差一起丢），同名不能指两件事；
  频谱结果里不出现 `dc_linear_model_*`（测试用 `assertNotIn` 锁死）。
- 多频 `beta_expectation` 是**逐项指数的功率加权平均**（dc/wall-limited ⇒ 1.0，
  skin-dominated ⇒ 0.5）；任一项落 `transition` ⇒ `null` + 列出是哪几个频率。
- `harmonics` / `dc_current_a` **追加在签名末尾**，位置调用不破；schema 的 `required`
  从 `["shape","frequency_hz","current_a","rth_c_per_w","length_mm"]` 收窄为
  `["shape","rth_c_per_w","length_mm"]`（`current_a` 与 `frequency_hz` 现在是二选一），
  但函数内部仍然"都不给 ⇒ `ARG_REQUIRED`"，所以没有静默默认。

### 口径

- 同模块加参数：**没碰任何其他工具的输出**；`electrothermal_conductor` 读数逐位不变。
- `tests/test_acthermal.py` **62 → 95 例**（+33：等价性 6 案例 ×2 条 / 功率 6 / 指数 4 /
  模型对比 3 / runaway 4 / 拒绝 14）；selftest `242/242` → **`247/247`**（+5 条自检）。
- 成本：buck 五项频谱 **1.8 ms**；每项一次 `_one()`，成本线性于项数。
- `dependencies = []` 仍成立（只用 `math`）。
- 逐条口径见 [docs/72](docs/72-谐波电流的自热.md)。

## [0.0.68] - 2026-11-01

**铜在频率上的自热**（`ac_self_heating`）。`calc_capabilities` 里那条 "nothing here turns a loss
into a temperature at frequency" 是这一版收掉的最后一个明确缺口。

一句话结论：**同一个 35 µm 铜、同一个 3 A，100 Hz 与 DC 闭式解差 `3.042e-07`，1 GHz 却比 DC 模型
高 `7.905 %`；而"跑飞"其实是两个不同的答案——指数 1 是"任何温度都不自洽"，指数 1/2 是"有解，
但解在 37325.8 ℃"。** 工具数棘轮 172 → **173**。

### 判据为什么是这几条（每条都能失败）

| 锚点 | 实测 | 它能抓住什么 |
| --- | --- | --- |
| 低频 = DC | `dT 1.82079 K` vs `electrothermal_conductor` 闭式，`rel 3.042e-07` | 任何 1000 倍量纲错 |
| 同一条对账的反向控制 | 喂 **R@环境温度**（不是 R@20 ℃）⇒ `rel 1.97 %` | 一个"能通过"的对照不是对照 |
| 自洽性 | `resid = dT − I²·Rth·R_hot`，5 个形状，`< 1e-9` | 只做一次乘法的实现 |
| 指数实测 | 1.0 / 0.5000 / 0.999454 / 0.952475 / 0.50673 / 0.361116（粗糙） | 硬编码 β；薄壁仍报 0.5 |
| 两面 | `R_cold 0.0833056` vs `0.0833055` | 盲目乘 0.5 |
| `α = 0` | `dT = 9·20·0.00992013` 精确，`gain = 0`，`unit_gain_current = null` | `unit_gain` 写成 0 或 inf |
| 两种 runaway | 12 A/100 Hz：`gain_cold 1.11502`，**无根**；10 A/1 GHz：`root 37325.8 ℃` + "ordering, not a reading" | 合并成一句文案 |
| 边界那一对 | 11 A（`gain 0.93693 < 1`）**有根**（`dT 3854.27 K`），不能报"任何温度都不自洽" | 一个 `if` 把话说反 |
| 单调 | T 随 f、随 I 单调升；`I = 0 ⇒ dT = 0, iterations = 0` | 符号/方向错 |
| `Rth` 必须显式 | 缺参 ⇒ `ARG_REQUIRED`，文案写 "never assumed here" | 默认 Rth = 凭空来的数 |
| `shape` 必须显式 | 缺参 ⇒ `ARG_REQUIRED`；`"wire"` ⇒ `BAD_PARAMETER` | 靠"传了哪个参数"推断几何 |

### 这一轮修掉的四个真 bug（都会静默给错答案）

1. `dc_linear_model_delta_t_c` 读 `dc["value"]["delta_t_c"]` ⇒ `TypeError`：`result.ok()` 的 `extra`
   合并到**信封顶层**，正确读法是 `dc["delta_t_c"]`。
2. 镀层单位 `2.0*p >= d`（µm vs mm 差 1000 倍）⇒ 合法的 0.3 mm 孔 + 0.5 µm 镀层被读成"镀层把孔封死"。
3. 圆导体电阻 `rho*l_mm*1e3/area` 多乘 `1e3`（`ρ` 单位是 **Ω·mm**）⇒ 电阻大 1000 倍，
   **所有小电流都"跑飞"**。DC 对账的负控制就是为这一类错准备的。
4. DC 对照喂 `r_cold`（25 ℃）而不是 `r20c`（20 ℃）⇒ 1.97 % 假差异；修后 `3.0e-07`，
   而那个 1.97 % 被保留成一条**必须失败**的测试。

另加：`assumptions` 曾被 `extra` 里的同名键整体覆盖（`rth_source` 就此消失），现在
`assumptions` 只走第六个位置参数、`checks`/`value` 只走 `extra`。

### 语义变更（本版未发布，故不涉及兼容）

- `runaway_possible`（解点 `gain >= 1`，解点上恒为 False）拆成两条**不同陈述**：
  `steady_state_unstable`（解本身稳不稳）与 `cold_loop_gain_above_one`（环境点上增益是否已 ≥1），
  外加 `runaway_gain_cold`。名字换了是因为旧名字在解点上是空话。

### 口径

- 新模块 + 新工具，**没碰任何既有工具的输出**；`electrothermal_conductor` 读数逐位不变，
  只在 `not_computed` 第 2 条**追加**一句指向 `ac_self_heating`。
- `tests/test_acthermal.py` **62 例**；selftest `234/234` → **`242/242`**（8 条新自检）。
- 成本：单次 **2–5 ms**（求根 30–34 次二分；`fd_verify` 开关只省约 4 ms）。
- 逐条口径见 [docs/71](docs/71-电流在频率上的自热.md)。

## [0.0.67] - 2026-11-01

**圆不是方的**（`ac_round_conductor`）。0.0.66 的薄板解明写着一条边界："过孔场：孔壁的电流分布
是另一个问题"。这一版解那个问题，并且撞上了一个比问题本身更值得记的东西——
**圆管的教科书闭式解在 `x ≳ 15` 之后会安静地返回实心线的答案**。

一句话结论：**一个 0.15 mm 钻孔、25 µm 镀铜的过孔在 1 GHz 是 `10.5781 × R_dc`，
DC 答案低了一个数量级；而教科书闭式在 `x=40, a1/a2=0.9` 时答 `20.2523`（= 实心答案），
真值 `3.8501`，差 5.26 倍，不报错。** 工具数棘轮 171 → **172**。

### 判据为什么是这几条（每条都能失败）

| 锚点 | 实测 | 它能抓住什么 |
| --- | --- | --- |
| 实心 DC 级数 | `R/Rdc → 1 + x⁴/48`（rel 1.4e-14）、`X/Rdc → x²/4` | **把圆当成方**：薄板是 `1 + 4x⁴/45`，系数不同，这里立刻现形 |
| DC 内电感 | `L_dc = μ/(8π) = 50.0 nH/m`，**与直径无关** ⇒ 2.1e-11 | 归一化分母（用外圆而不是环形截面）、`μ` 的因子 |
| 大 x 渐近 | `R = x/2 + 1/4 + 3/(32x)`、`X = x/2 − 3/(32x)`；`x=40` rel 4.96e-8，45° 相位 1.2e-4 | 渐近分支；`X` 的常数项是 **0** 不是 `1/4`（写错就永远差一点 45°） |
| 八个 mpmath 锚 | `x = 0.5/5/10/40/71/200/300/1000`，80–900 dps 闭式 + 50 dps 直接积分同一条 Riccati ODE，**三方一致 1e-30~1e-45** | 走针的公式、步数、Richardson 系数 |
| **另一个求解器** | 复有限差分（`E'(a1)=0` 鬼点 + 三对角），via@1 GHz `n=1148` 差 **8.0e-5**、自身收敛 9.6e-6 | 闭式不是离散化产物（`x_out > 200` 直接 `fd_verify = "skipped"`，不印假吻合） |
| 薄壁 → 平板 | 25 µm @ 1 GHz 除中径因子 `(a1+a2)/(2a2)=0.833` 后 `R` 差 1.41 %、`X` 差 1.5e-4；`x=40`（4 个 δ 壁）`R` 差 1.26 % | 圆柱按外圆周出流、平板按壁面积归一——**不除因子会"差 11.6 %"**，那是物理不是错误 |
| 管 → 实心 | `a1→0` 时 `z_tube/(1−(a1/a2)²) → z_solid`（比**物理阻抗**，不比 `Z/Rdc`） | `R_dc` 定义不同，直接比比值会差一个面积因子 |
| 薄壁 → `R_dc` | 0.5 µm 镀层 @ 1 GHz：`1.00000000029`（0.3 mm 孔）、`1.0002898`（0.1 mm 孔） | 「用趋肤深度算薄镀层」——`δ=2.09 µm` 比镀层厚 4 倍，那是**铜太薄**不是趋肤 |
| DC 自证 | `η = jx²(ρ²−rᵢ²)/ρ` ⇒ `Z/Rdc ≡ 1` 精确 | 归一化与边界条件同时错才能过 |
| 两种孔径约定 | 0.3 mm + 25 µm：`0.021598` vs `0.025525 mm²`（**差 18 %**） | 把"钻孔"和"成品孔"静默统一 |
| fail-closed | `frequency_hz` 缺 ⇒ `ARG_REQUIRED`；`diameter` 与 `drill/plating` 同时给、`wall_um ≤ 0`、`plating_um` 缺、`mu_r < 1`、`profile_samples < 2` ⇒ `BAD_PARAMETER`/`ARG_INVALID` | 猜一个孔是过孔答案变成错的唯一方式 |

### 那个安静的错答案（本轮最贵的一条）

教科书闭式 `c = −J1(ka1)/Y1(ka1)` 数学上对，数值上在 `x ≳ 15` 之后 `c → −i`，
`J0 + c·Y0` 退化成纯实心解，**孔的信息被完全消掉**：

| `x` | `a1/a2` | 闭式 | 走针（真值） |
| --- | --- | --- | --- |
| 10 | 0.9 | (1.0812, 0.6172) | 同（1e-10） |
| **40** | **0.9** | **(20.2523, 19.9976)** | **(3.8501, 3.7967)** ← **5.26 倍** |

第一反应是"上高精度"。实测 `x=71, a1/a2=0.845`（mpmath）：

| dps | 闭式 | 相对误差 |
| --- | --- | --- |
| 30 / 40 / 50 | `ZeroDivisionError` | — |
| **55** | **(10.223656803931606, 10.151851379981979)** | **2.422e-05**（静默） |
| 60 | (10.223983803730397, 10.151729519211120) | 1.363e-10 |
| 65 / 70 / 80 | (10.2239838017698…, 10.1517295191057…) | 4.7e-15 / 1.7e-16 |

**55 dps 给的错答案四位有效数字全对**，看着和收敛了没区别。所以：闭式**降级为 `x_out ≤ 10`
的复核**，`x_out > 10` 时 `closed_form_skipped` 里逐字写着它会给什么；答案走
**对数导数（Bessel 方程 Riccati 形式）走针** `η' = (ka2)² − η² − η/ρ`、`Z/Rdc = jx²(1−ri²)/η(1)`，
步数按**壁厚** `16(x_out−x_in)`（via@1 GHz 782 步，按外径要 766、按 5 mm 铜柱 @ 1 GHz 要近 2 万）。

同一个病在剖面上更危险：`radial_profile_bessel` 在 `x=72` 给一条**平直的 1.0、相位恒 0**
（真值 `4.85e-10 → 1.0`，累积滞后 −1278°）。所以 `profile` 也走 `ψ = lnE`（走针顺带积出，代价 0）
+ **Hermite 三次插值**（`ψ' = η` 两端已知，不线性插值 9 个点画指数曲线），`x_out > 5` 时信封写
`profile_vs_closed_skipped`。

### 三个必须写清的边界

1. **无邻近效应**：只解孤立导体。真实过孔阵列里相邻孔的场会互相挤压，本工具不预报。
2. **无 pad / antipad / 端部扩散 / 温升 / 扫频**：只在 `a1..a2` 之间解场，两端当无限长圆柱。
   焊盘与反焊盘对 `L` 的影响远大于对 `R` 的，这里一个都不报。
3. **两种孔径约定都保留**：`ac_round_conductor` 用**成品孔**（铜在外侧，`OD = drill + 2·plating`），
   `via_resistance` 默认仍是**钻孔**（铜在里侧）。**不静默统一**，因为两种都是真实图纸语言。

### 兼容性与口径

- **`via_resistance` 新增 `plating_outside=False`**：默认分支的**读数逐位不变**、`basis` 与 `extra`
  逐字不变，只在 `assumptions` 里**追加**一个 `hole_convention`（说清默认读的是「钻孔、铜在里侧」——
  这条以前根本没写，是描述与代码打架的根因）。置 `True` 时切成品孔约定（`A = π((d/2+t)²−(d/2)²)`，
  外径 `d+2t`）并跳过「镀层堵死孔」的检查。所以：**数值不动、语义被写明、开关可选**。
- **`calc/skin.py` 的 `basis` 2/15 → 4/45**（0.0.66 已发布的那条 s⁴ 系数订正：`basis` 文本跟着
  `checks` 里的实测残差走），**默认答案不变**。
- **`regime` 新增第四态 `wall-limited`**（有孔且 `x_wall = x_out − x_in ≤ 1`）：过孔在薄镀层
  高频下不是趋肤问题，把孔挖大没用、加厚镀层才有用。
- **成本**：单次 via@1 GHz 约 **68 ms**；`cProfile` 归因 `_bisect` 三次里程碑频率搜索占 **85 %**
  （二分的是**壁厚 s ≤ 5** 而不是频率，对 `f` 二分会把 `x` 推到 1e6）、`fd_annulus` 7 %、
  答案走针 3 %。所以 `fd_verify=false` **省不下多少**（63.8 → 59.9 ms）。
- 测试：`tests/test_roundwire.py` **59 例**（mpmath dps 表、闭式崩塌、剖面负控制、两种孔径、
  10 条拒绝、信封诚实性、注册一致性），selftest **227 → 234**，`docs/README` 与根 README
  的工具计数 168/171 → **172**。

## [0.0.66] - 2026-11-01

**铜不会一直是那个铜**（`ac_sheet_resistance`）。到 0.0.65 为止，这个仓库里每一条关于铜的
电阻答案都带着同一个隐含前提：**直流**——`board_ir_drop`、`copper_resistance`、
`current_density_field` 一个不少。这一版补上薄板内沿厚度 1-D 扩散的**精确解**。

一句话结论：**1 oz 铜的 `R` 偏离 DC 1 % 的频率是 1.198 MHz，10 MHz 时是 1.5408 倍；
DC 答案在 1 MHz 就开始错，不是"精度差一点"。** 工具数棘轮 170 → **171**。

### 判据为什么是这几条（每条都能失败）

| 锚点 | 实测 | 它能抓住什么 |
| --- | --- | --- |
| 小 s 展开 | `R−1 → 4s⁴/45`、`X → 2s²/3` | **s⁴ 系数写错**：第一版 docstring/checks/测试里全写 2/15。60 位 `Decimal` 实测 `(R−1)/s⁴ = 0.088888888855...` = **4/45**（来自 `u coth u = 1 + u²/3 − u⁴/45 + …`）。这条是被 `checks["series_r_abs_error"]` 抓出来的——只报 `R` 而不报这条残差，2/15 会一直躺着 |
| 大 s 渐近 | `s ∈ {5,10,100}` 上 `R = X = s`（相对差 < 1.5e-4），即表面阻抗 45° | 渐近分支、`√f` 的系数 |
| `s=1` 钉死 | `R = 1.0856357047503276`、`X = 0.6503925810415222` | 整条曲线的中段，任何一项漏掉都过不了 |
| X 的独立物理锚 | `X·R_dc == 2πf·μ₀t/3`（薄板内感） | `X` 那一支的推导**与 R 无关**，所以这是唯一"非自己验自己"的代数锚 |
| **另一个求解器** | 对同一方程做复三对角 FD（Neumann ghost + Dirichlet），`n = 501/1001/2001/4001/8001` ⇒ 差 **0.2004/0.1001/0.0500/0.0250/0.0125 %**，精确按 O(dz²) 减半 | **闭式不是离散化产物**。自建锚可能一起通过（同一个公式），这条不会 |
| face 恒等 | `faces="two"@35 µm` 与 `faces="one"@17.5 µm` 的 `R/Rdc`、`X/Rdc` **差 < 1e-12** | 「因子 2 被塞在结果里而不是几何里」 |
| 一个 δ 内的**电流**占比 | `\|1 − e^{-(1+j)}\| = 0.8589546`（100 MHz / 400 µm 铜，`s = 60.53`） | 把它写成 `1 − e⁻¹ = 0.63212`——那是**密度**比，差 36 %，足以让「δ 内有一半电流」错方向 |
| DC 平坦 / 峰值在面 | `s ≤ 0.1` 时 `profile` 与 `depth/t` 一致到 1e-6；否则 `j` 单调递减、峰值在第 0 层 | `cosh(γz)/cosh(γt)` 的边界写反 |
| 2 oz 反直觉 | 70 µm 的 `f_1%` = **299 564 Hz** = 1 oz 的 **1/4**；且同频率下 `R/Rdc` **更大**（70 µm@1 MHz 1.1068 对 35 µm 1.0070） | 「越厚越晚进入趋肤」这个直觉——两句是**不同比较**，同时断言，不让一句被读成另一句 |
| 默认路径不动 | `current_density_field` 不给 `frequency_hz` 时输出**逐位相等**，信封里没有 `frequency_hz` 键 | 新参数把老答案挪了 |
| 频率只缩放 | 20×2 mm 带 10 MHz：`resistance` ×1.5408001、`peak_j` ×1.5408001、`peak_j_gross` 与 `crowding_factor` **不变** | 把 AC 做成"重解一遍"而不是"同一个线性问题换电导" |
| fail-closed | 无 `frequency_hz` ⇒ `ARG_REQUIRED`；`f=0`/负、`t=0`、`rho=0`、`mu_r<1`、`profile_samples<2`、`rq<0`、`faces="three"` ⇒ `BAD_PARAMETER`；geometry 与 `dc_resistance_ohm` 同时给 ⇒ `ARG_INVALID` | `faces` **不是外观选项**：one/two 差 2 倍有效厚度，静默默认成 `one` 会让该算对称的带状线凭空多一倍损耗 |

### 三个必须写清的边界

1. **只有 1-D**（沿厚度）。横向重分布、边沿拥挤、邻近效应、平面内涡流、过孔场、位移电流
   **都不在解里**，`limits.not_modelled` 六条逐条列出。`current_density_field` 拿到的也只是
   「同一个横向图案换了个电导」——那条 warning 就在信封里。
2. **粗糙度只作 Hammerstad 乘子，默认关**。不给 `Rq` 时明说这是**下界**。锚：`Rq=1 µm@10 MHz`
   ⇒ `K = 1.0020409`；`Rq = δ@1 GHz` ⇒ `K = 1.60514`。
3. **升温不在迭代里**。100 ℃ 时 `dc_sheet` 4.9257e-4 → 8.7122e-4，而 `R/Rdc` **降到** 1.3456
   （`ρ` 变大 ⇒ `δ` 变大 ⇒ `s` 变小）。要温升就把冷阻喂给 `electrothermal_conductor`。

### 数值稳定性（这一版最值钱的修正）

第一版照抄 `cosh(2s) − cos(2s)` 在 `s = 0.01` 时相消、**丢 8 位**，把 `R−1 = 1.3e-9` 算成 `3e-5`
（相对错 4 个数量级）。改用半角恒等式：

```
sinh 2s ± sin 2s = 2 (sinh s cosh s ± sin s cos s)
cosh 2s − cos 2s = 2 (sinh²s + sin²s)      ← 平方和，恒正，不相消
```

`s ≥ 20` 直接返回 `(s, s)`（残差 `2e^{-2s} < 1e-16`，同时避开 `sinh` 在 `s ≈ 354` 的溢出点——
400 µm/100 MHz 时 `s = 60.5`，不分支就 `OverflowError`）。

> 单位坑也记在这里：`s = t/(k·δ)` 里 `t` 是 mm、`δ` 是 **m**。第一版漏了 `1e-3`，`s` 大 1000 倍。
> 溢出只是症状，真错的是单位。

### 复用

`current_density_field` 新增 `frequency_hz`（默认 `None`）与 `skin_faces`（默认 `"one"`），
内部把电导换成 `G = t_eff/ρ`，并把 `_narrowest_section` 改传 `t_eff` 使 `crowding_factor`
保持纯几何（DC 路径 `t_eff == t`，逐位不变）。新增两个键：`peak_j_surface_a_per_mm2`
（= 原 `peak_j_a_per_mm2`，**表面值**）与 `peak_j_gross_a_per_mm2`（= peak/r，毛厚度均值）。

### 验证

`tests/test_skin.py` **44 例**新增；全量 `discover -s tests` **2795 例 / OK (skipped=5) / 1237.462 s**
（0.0.65 是 2751 / 1129.183 s）；`--selftest` **227/227**（0.0.65 是 223/223，新增四问：1 oz@10 MHz 的
`R/Rdc`/`δ`/`s`/`f_1%` 数值锚、`two@35 µm ≡ one@17.5 µm`、`current_density_field` 的 AC 缩放与
`crowding_factor` 不动、三条 fail-closed 的 reason_code）。

### 文档

`docs/69-趋肤效应与交流电流分布.md`（新），`docs/README.md` 六十九篇 → **七十篇**并补齐
`67`/`68`/`69` 的"与代码的对应"条目；`README.md` 速查表新增一行、`current_density_field` /
`magnetics_dcr` / `board_electrothermal` 三处的"仍然没有"改成指向新工具（**只加指针，
不声称闭环**）。

## [0.0.65] - 2026-11-01

**负载跳变要的是曲线**（`pdn_step_response`）。上一版能答「有没有难看的频点」，这一版答
另一件事：负载跳了，轨**掉多深、什么时候掉、多久回来**。

这两件事不可互换，而且不可互换的方式很具体：一条带内 \|Z\| 很平的轨，遇到阶跃照样先掉
`I*ESR`——那是电阻，频域里它就是一条水平的 \|Z\|，看不出「难看」。所以这是**新工具，不是
给 `pdn_impedance` 加开关**：时域要 `rise_ns`、要调节器模型、要返回波形，与频域的输入表
不是同一张。工具数棘轮 169 → **170**。

一句话结论：**同一张网络，DC 尾落在 `-I*(R_s+plane_r)`、电荷恒等式闭合到 7.6e-15，而
峰值比 ngspice 低 0.1–1.3 %（反向欧拉的数值阻尼，所以报的 Q 是下界）。**

### 判据为什么是这几条（每条都能失败）

| 锚点 | 实测 | 它能抓住什么 |
| --- | --- | --- |
| 一阶闭式 | `1 Ω`+`1 µF`+`10 mΩ`、`1 A`：1τ 处 **−0.634964** 对闭式 **−0.635763**（0.126 %），5τ 处 −0.993248 对 −0.993329 | **时间常数写错**：`τ = C(R_s+ESR)`，`R_s‖ESR` 只决定起点。我最初把 `τ` 写成 `C(R_s‖ESR)`，期望 −0.6358、实测 −0.6313，差 0.7 % 看起来像后向欧拉的一阶误差，其实是闭式错了 **10 倍** |
| DC 尾 | `100 µF`/`2 A`/`20 mΩ`：尾部 **−0.039999954** 对 −0.04，`dc_check_rel` 1.16e-06，`settle` 12.7 µs 在 40 µs 窗内 | 漂移的积分器、写错符号的伴随模型 |
| 电荷恒等式 | `Σ(-C·Δu)` 对上 `∫(I_load − i_vrm)dt`，闭合到 **7.6e-15** | **某条支路没进节点方程**。这条只有两个前提都成立才闭合：电荷符号是 `−C·Δu`（写成 `+C·Δu` 给 **1.99**），求和用**右端点**（梯形法只能闭合到 0.7 %，那 0.7 % 全是方法差造成的假象） |
| 纯电容是斜坡 | `1 µF`/`50 mΩ`/`rise 50 ns`、窗 1 µs：50 ns 处 **−0.075417** 对 −0.075、1 µs 处 **−1.025416** 对 −1.025 | 后向欧拉对纯电容**精确**，所以这里能分辨「方法误差」与「实现错」。这一格的 `dc_check_rel = 0.999999`、`tail_settled = False` 是**正确读数**（`r_series = 1 MΩ` 时 DC 答案本身是 −1e6 V） |
| LC 环周期 | 3 个半周期反推 **653.07 ns** 对 `2π√(LC)` **628.32 ns**（+3.94 %）；峰值 −0.110376 @ 212.5 ns | L 或 C 的量纲/符号 |
| 首摆冲过 DC | `ζ = 0.25` 时 **`undershoot 0.060376 > \\|v_final\\| 0.050007`** | 只会「单调掉到底」的模型会在这里给 `undershoot = 0` |
| **换一个求解器** | 同一网络丢给 ngspice：一阶最大差 **0.126 %**（1τ 处）、LC 峰值低 **1.27 %**、bulk 峰值低 **0.42 %**、三条尾部全部对上 | **整个拓扑建错**——四条自建锚可能一起「通过」，因为它们在同一个伴随模型里。三副 deck 在 `docs/samples/pdn_step/`，可直接 `ngspice -b` |
| 摆位进峰值不进 DC 尾 | `at="die"` **−0.032524503** / `at="bulk"` **−0.052936594**（1.63×），**两条 `v_final` 都是 −0.030000** | 「摆位是 DC 的事」——DC 时电容开路，电流横竖要过那块板；频域只能看到 \|Z\| 的一处凸起 |
| 平面不被静默丢掉 | 无 plane **−10.00 mV**、2 mΩ plane **−30.00 mV**，`nodes` 相同 | 「没有 bulk 节点就没有平面」——见下节的真缺陷 |
| 理想阶跃进电感不给数 | 有 L 无 `rise_ns` ⇒ **`ARG_REQUIRED`** 且 message 含 `rise_ns`；同网络给 `rise_ns` 就有答案 | `V = L·dI/dt` 由步长决定（`h = 0.25 ns`、`20 A` ⇒ **16 V**、`h_refine` **100 %**） |
| 慢边沿更浅 | `rise 2000 ns` 的峰值与 `vrm_di_dt` 都小于 `rise 100 ns` | 边沿被忽略 |
| `count` 是并联 | `count=3` 与列三遍差 **<1e-15** | 并联合并写错方向（`ESR*n` 而不是 `ESR/n`） |
| 负阶跃方向 | `preload 2 A / step −2 A`：`v_initial = −0.04`、`v_final → 0`、轨**上升**、`0 < settle < t_stop` | 方向写反；以及 **0 V 尾巴让相对带宽退化**（`tol = band/100*0 = 0` 永不触发） |
| 拒绝而不钳位 | 无电容/零阶跃/零容值/未知 `at`/电压源直连/bulk 无路径 → 各自 reason_code；`samples=10`、`settle_band_pct=0`、`rise_ns=-1`、`t_stop_ns=-1`、`r_series_ohm=-1` 全部报错 | 「静默忽略」被当成实现 |
| 步数预算不静默粗化 | `samples=500000` ⇒ **`TOO_LARGE`**（守卫是活的：经网格尾部密度可达，不是死代码） | 悄悄把网格做粗 |

### 本轮修掉的真缺陷

1. **平面在单节点情形被静默忽略（改变答案）。** `at` 默认 `"die"`，全部电容都在 die 时
   只有 1 个未知节点，`plane_r_ohm`/`plane_l_h` 只加在「bulk → die」那条支路上，于是
   **整对参数被丢掉，一个 warning 都没有**：同一块 2 mΩ 板报 −10.00 mV，真值 −30.00 mV。
   修法是**把平面并进调节器支路**（串联求和，拓扑等价），不是加警告。
2. **理想阶跃进电感**：`h_refine_peak_delta_pct = 100 %`，一个只在网格上存在的答案。
   改成 `ARG_REQUIRED` 并把 `V = L·dI/dt` 写进 message。
3. **0 V 尾巴让 `settle_time_s` 退化**：`tol = band/100*|dc_expected|` 在载荷释放时是 0。
   改成 `scale = max(|dc_expected|, |peak − v_initial|)`。
4. **`5%%` 直接印在 `precision`**：那句是普通字符串拼接不是 `%` 格式化，`%%` 没被还原。

### 边界（`not_computed` 与 `not_implemented_yet`）

- 调节器是 **Thevenin**：没有环路带宽、没有相位裕度、没有限流、没有多轨 droop 分摊。
  **这条轨在这里通过，不代表它在真实环路上不会因为环路自己引起的振荡而挂掉。**
- 板是**一到两个集中节点**，不是分布平面（平面内扩展是 `board_ir_drop` /
  `pdn_plane_impedance`）。
- 没有 die/封装电感与片上电容；没有电容的 **DC bias / 温度 / 老化降额**（给的 `c_farad`
  就当它在工作点上，实际陶瓷电容在标称电压下可能只剩一半——**降额要用户先算**）。
- **只有单台阶**（可带边沿）；其他波形在范围外。
- **后向欧拉的阻尼让 Q 是下界**：振铃衰减比物理快，所以「我这里不振铃」不等于「板上不
  振铃」；`h_refine_peak_delta_pct` 只界定**峰值**，不界定衰减率。
- 交叉验证只覆盖线性网络（三副 deck 都没有半导体）。

### 目录

- `calc/power.py`：`STEP_MODEL` + `pdn_step_response`（自适应后向欧拉、伴随模型、
  `probes`、DC 锚、电荷恒等式、`refine` 重跑、全部 fail-closed）。
- `tools.py`：新工具 12 个属性 + `available` 165 条 + `not_implemented_yet` 22 条
  （新增「PDN TRANSIENT 是板级答案不是变换器答案」）。
- `server.py`：自检 4 问（1818–1822）⇒ **223/223**。
- `tests/test_pdn_step.py`：**23 例**。
- `docs/68-PDN负载阶跃.md`、`docs/samples/pdn_step/`（三副 ngspice deck + 对表说明）。
- 全量回归 **2751 例 / OK (skipped=5) / 1129.183 s**（0.0.64 的 2728 + 本轮 23）。耗时不作性能结论（本机负载未记录）。

## [0.0.64] - 2026-11-01

**铺铜也是铜**（`board_ir_drop(..., model_zones=True)`）。上一版把「铺铜在并联，所以这个
drop 是上界」写成一条限制，这一版把它做成数——因为在真机上那条限制不是「偏高」，是
**没有答案**：

```
+3V3（jetson-agx-thor-baseboard.kicad_pcb，src U71.14，sinks C93.2/C355.2，2 A，25 µm）
→ error NOT_APPLICABLE: could not solve the copper network: singular: singular at 21
```

根因不是 bug：这块板的 `+3V3` 有 315 段走线、149 个过孔、166 个焊盘、**7 块铺铜**，其中
`In2.Cu` 上一块 **17832 mm² 的整层平面**。走线建图之后网被这块平面**分成两半**，两半之间
唯一的连接就是平面本身——于是图是两个互不相连的子图，线性代数只能说它奇异。

一句话结论：**开了开关以后，同一块板从「不可解」变成 57.2440 mV / 13.4 s。** 而正确答案
不是「算出来了」，是 §3 那条能解析算出来的锚点先站住。

### 判据为什么是这几条（每条都能失败）

| 锚点 | 实测 | 它能抓住什么 |
| --- | --- | --- |
| 铺铜条带对「方块数」 | 1×10 mm 焊盘 + 30×10 mm 铺铜 ⇒ 手算 **1.9 方块 = 0.935896 mV**，实测 **0.935986**（差 **0.01 %**） | 薄片电阻的离散：格间电阻**与栅距无关**（正方形永远是 1 方块），所以这个数只能来自 `rho/t`，不能来自任何「长度×方块电阻」的拟合 |
| 「长度×方块电阻」是上界 | 20 mm / 10 mm = **2 方块 = 0.985142 mV**，比实测高 **5.3 %** | 焊盘只 1 mm 宽 ⇒ 电流从 1 mm 的边缘进、在铺铜里散开，**有效长度 19 mm**。点接触/线接触的实现会落在这个上界上 |
| 栅距不是精度旋钮 | `h=1.0` 与 `h=0.5` 差 **<2 %**；真机 `h=2/1/0.5` 给 57.5795 / 57.8807 / 58.1658 mV（增量 +0.301 / +0.285，**非二次收敛**，外推 ≈58.45） | 「把网格加密就会收敛到某个数」的期望：2-D 扩展电阻带对数项，加密只买接触附近的分辨率 |
| 默认路径逐位不变 | `model_zones=False` 在真机上仍是 **`singular at 21`**，与 0.0.63 **逐字相同** | 换了求解器有没有动历史答案：无铺铜时 CG 与稠密解**逐位相同**（19.702857 mV 单走线、20.979986 mV 跨层过孔）；7×7 薄片 `max\|dv\| = 2.6e-12` |
| 铺铜不接上就不是零压降 | 铺铜只盖住源那一侧 → **`NOT_CONNECTED`** 并点名 sink | 「附近有铺铜」被当成「连上了」——这种实现会给出一个漂亮的小 drop |
| 别人的铜不算 | zone 的 net 改成 `VCC2` → `NOT_APPLICABLE`；带 `(keepout …)` → `NOT_APPLICABLE` | 按层/按位置取名而不按 net 配对的实现 |
| 预算不静默粗化 | `max_zone_nodes=100` → `TOO_LARGE`；`zone_grid_mm=0` → `ARG_RANGE`（**不是**悄悄钳到 0.2）；`zone_link_mm=-0.1` → `ARG_RANGE` | 「静默忽略」被当成实现 |

### 三个真缺陷（都在真机/合成锚点上被抓出来）

1. **平行边重复计入破坏 IC(0) 的 pattern**。焊盘到铺铜是几十条平行边，同一对节点在邻接
   表里出现多次，后向代入就把同一格算两遍。症状：合成锚点 **迭代 20000 次、残差卡在 0.09、
   跑 43 s**。把邻接建好后折叠成一条合成电导（`1/Σ(1/R)`）⇒ **52 次迭代 / 0.17 s**。
2. **栅距按包围盒规划**。`B.Cu` 上 5 块 2×2 mm 小铺铜散在 112×79 mm 里 ⇒ bbox 规划给
   `h = 1.816 mm`、**整层 11 格**（每块两格，等于没建）。改成 shoelace **实际填充面积** ⇒
   `h = 0.2 mm`、**1085 格**。
3. **内层平面搭不上**。过孔原来只在 `layers[0] ↔ layers[-1]` 建节点，`In2.Cu` 在中间，
   桶身物理上穿过它但图上没有那个节点 ⇒ 平面孤立（实测只连到 `U71.15`）。开关打开且过孔跨
   >2 层时按层拆分，桶身电阻按层数均分。层级开关保证 2 层板上的贯通过孔**不拆**。

### 求解器：CG + IC(0)

7052 个未知数，原来的稠密解 O(n³) 跑不动。新增 `resistive_network_cg`（地面节点消元 →
SPD → Jacobi / IC(0) 预条件共轭梯度，返回形状与原稠密解兼容 + `iterations/residual_rel/
converged/precond`）。真机 `+3V3`：Jacobi **3113 迭代 / 39 s** → IC(0) **907 迭代 /
13.4 s**，`residual_rel = 9.4e-10`。不收敛直接 `NOT_CONVERGED`，**不返回数字**。

`max_nodes`（守稠密解）在开关打开时放宽到 `max(max_nodes, 4096)`；铺铜格另有
`max_zone_nodes`（默认 8000，是墙钟换来的，不是精度换来的）。

### 边界（写进 `precision` / `not_implemented_yet`，逐条都能被判据打掉）

1. **热焊盘辐条没有建模。** 焊盘落在填充里按实心铜（1e-6 Ω）处理 ⇒ 真实连接是辐条时，
   这个 drop 是**下界**。这是本条答案唯一偏乐观的方向。
2. **桥接是量级模型。** 跨 ≤0.5 mm 的缝按 1 个方块算，warning 里自称
   `the single crudest number in this answer`。
3. **zone priority / 最小宽度 / 与其他网的间隙不解**，直接用文件里的填充（填充过期会静默继承）。
4. **孤岛丢弃**：从源点 BFS 不可达的铺铜节点删掉并报数（真机 25 个）。
5. **>2000 格的焊盘退化成点接触**，接触扩展被高估，单独计数 + warning。
6. **纯 DC**：无 skin effect、无平面谐振（那是 `pdn_plane_impedance`）。
7. **没有第三方实现对表**：解析锚点只有那一条 1.9 方块，真机读数只有「自洽 + 与栅距探针
   趋势一致」这一层证据。

### 目录 / 表面

- 新文件 `src/pcb_agent_mcp/calc/zonefield.py`（约 260 行）：`plan_layer` / `build_sheets` /
  `cell_index` / `nearest_cell` / `sheet_edges` / `cell_xy`，常量 `ZONE_SHEET_MODEL`、`LINK_OHM`。
- `calc/power.py` 新增 `resistive_network_cg` / `_ic0_factor` / `_ic0_solve` / `_cg_pack`。
- `oracle.board_ir_drop` 新增四个参数（`model_zones` / `zone_grid_mm` / `max_zone_nodes` /
  `zone_link_mm`），新增 `zone_summary` 与 `solver` 两个输出块，`model` / `basis` / `precision` /
  `not_computed` 全部分叉。
- `tests/test_ir_drop_zones.py` **25 例**（含 13 条能失败的负控制）；`--selftest`
  **216/216 → 219/219**（铺铜并入网络三问）；**工具数仍 169**（是参数不是新工具）。
- 文档：[`docs/67-铺铜与IR-Drop.md`](docs/67-铺铜与IR-Drop.md)。
- 全量回归 **2728 例 / OK (skipped=5) / 1286.504 s**（0.0.63 的 2703 + 本轮 25）。
  耗时不作性能结论（本机负载未记录）。

## [0.0.63] - 2026-11-01

**2-D 稳态电流密度场**（`current_density_field`）。此前所有载流工具（`width_for_current`、
`current_capacity`、`fusing_current`、`fusing_current_taper`）用的都是 `J = I/(w·t)` ——
这个式子在一段等宽直线上精确，但**在每个几何变化处都错**，而且错得没有方向指示：
等宽段算得对，于是任何用它算出来的数都长得很正常。

一句话结论：**拐角处的峰值不是"高一点"，它是发散的。** 直角弯内侧是 270° 金属内角，
`J ~ r^(-1/3)`，网格每加密一倍峰值乘 `2^(1/3) = 1.2599` —— 实测 **1.266（h=0.05）/
1.262（h=0.025）**。所以本版的最重要输出不是那个峰值，而是 `peak_is_grid_convergent`
与 `peak_growth`：**峰值不可引用时会明说不可引用**，并指向环读数（`decay = 0.7963`，
理论 `2^(-1/3) = 0.7937`，这个不随网格变）。

### 判据为什么是这几条（每条都能失败）

| 锚点 | 实测 | 它能抓住什么 |
| --- | --- | --- |
| 均匀 20×2 mm 条带 `R` 对 `rho/t·L/W` | **0.004901063** 对 0.004925714（−0.50 %） | 离散步长的量纲：`G = t/rho`（与格距无关的薄层电导）才对。差值 0.5 % 全来自触点在格胞中心（有效长度 19.9 而非 20 mm），不是离散误差 |
| 均匀条带的场是平的 | `peak/median` **1.0000**、`crowding` **1.0000**、三环全 14.2857、`decay` **1.0000**、`warnings == []` | 矩阵下标、边只算一半、电流符号 —— 三个真缺陷都在这里立刻掉出来 |
| 4:1 阶跃的窄段密度恰是 4 倍 | 窄段均匀区对 `I/(w_narrow·t)` 差 **±0.1 %**、`median/wide` ≈ 4.000 | 缩放与掩膜 |
| 直角弯的峰值按 `2^(1/3)` 涨 | **1.2660**（h 0.05）/ **1.262**（h 0.025），且 `peak_is_grid_convergent=false`、警告含 `NOT GRID CONVERGENT`、层表峰值单调 | 任何"把角磨圆"的实现（模糊掩膜、圆角化、只看 `max V` 梯度）都过不了它 |
| 环衰减 `decay_2h_4h_over_0.5h_1h` | **0.7963** 对理论 **0.7937** | 峰值测不出斜率；这个读数测的就是 `r^(-1/3)` 的斜率，**不随网格变** |
| 未收敛不给数字 | `max_sweeps=3` → `NOT_CONVERGED` + `extra.failed_level`，信封里**没有 `value` 键** | 闸门自检 |

### 三条布局实测（同一几何、同一网格，只改铜）

| 改法 | R (Ω) | peak J (A/mm²) | growth | 相对原弯 |
| --- | --- | --- | --- | --- |
| 原直角弯 | 0.009613 | 74.174 | 1.2660 | —— |
| **45° 斜接** | 0.009179 | **51.760** | 1.1486 | **−30 %** |
| 内角补铜（三角形 fillet） | 0.009320 | 47.437 | 1.1468 | −36 % |
| **切掉内角尖端的"倒角"** | 0.011294 | **142.878** | 1.2411 | **+93 %** |

**最后一行是本版唯一一条反直觉读数，必须单独说**：切掉内角尖端在接头内部造出一个空洞三角形，
把电流通路挤成一条 **0.35 mm** 宽的颈 —— 尖端没了，拥挤只是从一点搬到了一段。所以本版**不**把它
文档化成"倒角更好"；这一条对 1-D 公式完全不可见，对"切掉尖角降低峰值"的直觉正好相反。

### 新增

| 项 | 内容 |
| --- | --- |
| `calc/currentfield.py`（约 940 行，纯 stdlib） | 电阻网模型（`G = t/rho` / 每方块）、SOR + cascadic 多重网格（`omega` 默认 **1.97**，逐层从**原始几何**重新栅格化）、按边遍历的电流求和、三环 annulus、载流截面 |
| 工具 `current_density_field` | `paths`/`polygons` + `source`/`sink` → `resistance_ohm`、`peak_j_a_per_mm2` + `peak_at_mm`、`median`/`p99`/`mean`、`narrowest_section_mm2` + `narrowest_section_at`、`crowding_factor`、`peak_growth`、`peak_is_grid_convergent`、`rings`；`extra` 带 `levels`（逐层 h/grid/unknowns/sweeps/residual/peak/growth）、`checks`（电流守恒 / 功率平衡）、`contacts`、`warnings` |
| `tests/test_currentfield.py` **19 例**（43.6 s） | 4 条解析锚点 + 4 条奇异性 + 9 条 fail-closed（`NOT_CONVERGED` / `CONTACT_NOT_ON_COPPER` / `NOT_CONNECTED` / source==sink / 四类 `BAD_GEOMETRY` + 三类 `BAD_PARAMETER`）+ 2 条信封诚实性 |
| `docs/66-电流场.md` | 方程与离散为什么恰好精确、四条锚点表、奇异性一节（峰值何时可引用）、三条布局实测、成本（omega 扫描 / tol 代价）、七条没有证明的事 |

### 改了什么

| 项 | 变化 | 为什么 |
| --- | --- | --- |
| `tools.py` | 注册 `current_density_field`（工具数 **168 → 169**）；`required: ["source","sink"]` | 2-D 场解是**新工具**，不是 1-D 工具上的开关：它要的是几何，不是宽度 |
| `calc/current.py` 的 `fusing_current_taper` | `precision` 加一句指向新工具 | 两者测的是同一个颈：熔断点在哪里，就去哪里量 2-D 拥挤，再把结果当几何改动喂回来。**读数一个数都没变** |
| `tests/test_vibration_mass_copper.py` | 工具数棘轮 168 → **169**（带注释） | 棘轮的意义就是逼着每次加工具显式登记 |
| `server.py --selftest` | 新增 3 条（均匀条带解析锚点 / 270° 角按 `2^(1/3)` 增长 / 两块不相连的铜必须拒绝并报 `Components found: 2`） | 工具进了注册表就必须有协议级自检，否则 `--selftest` 会对新工具一言不发 |

**默认关时逐位不变**：本版不改任何既有工具的输入输出。`width_for_current`、`current_capacity`、
`ir_drop_chain`、`pdn_impedance`、`fusing_current`、`fusing_current_taper` 读数全部不动。

### 三次真缺陷（都写在 `_currents` 的 docstring 里）

1. `i_in += iedge` 符号反了（`iedge` 是**流出**源）⇒ 1 A 都进不来，报 `NO_CURRENT`。
2. 为了"每条边只算一次"用 `if d < c: continue` —— 汇点在最右列，它的边**全部**指向更小下标，
   于是一条都没算：`I_out = 0`、`power_edge_sum = 1.03 W` 而 `I²R = 204 W`（守恒误差 **1.0**）。
   改成按边遍历（每格只看右邻与下邻）后 **6.3e-6**。
3. `_narrowest_section` 第一版按列数铜格胞 —— 胶囊 path 的端点锥尖只有 1 格宽，被报成"最窄截面"，
   `crowding_factor` 掉到 **0.435**（小于 1，荒谬）。改成"**载流 ≥ 最大载流 90 %** 的切面里取最小面积"。

### 边界（不许读成"电流仿真做完了"）

- **2-D 薄片、DC**：无 3-D 扩散、无过孔拥挤、无趋肤/邻近、无位移电流。
- **均匀 `rho` 与厚度**：无温升、无 `I²R` 回馈（那个环在 `board_electrothermal`，两工具不联动）。
- **接触是硬边等势块**：其边缘的 `J` 按构造就奇异，**不是该改布局的地方**（这一句写在 `precision` 里）。
- **截面按轴投影**：斜走线截面被高估 `1/cos` ⇒ `crowding_factor` 是**下界**。
- **不自动读 zone / 铺铜 / 平面**：`paths`/`polygons` 是什么就是什么；多连通直接 `NOT_CONNECTED`
  并给出 `Components found: N`。
- **峰值只在 `peak_is_grid_convergent` 为真时可引用**。

### 规模

| 项 | 读数 |
| --- | --- |
| 工具数 | **169**（168 + 1） |
| `--selftest` | **216/216**（213 + 3） |
| 新增测试 | `tests/test_currentfield.py` **19 例**（43.6 s，含求解结果缓存；不缓存 80 s） |
| 全量回归 | **2703 例 / OK (skipped=5) / 1190.706 s**（0.0.62 的 2684 + 本轮 19）。**耗时不作性能结论**：本机负载未记录；`skipped` 从 3 变 5 是哪两条重新被跳过，**没有排查，不当作结论** |

## [0.0.62] - 2026-11-01

立创 **`.epro` 归档「写出去之后，应用认不认」**这一格的实测回答。此前每次 `board_apply` 都会在
`not_verified` 里自己声明「这份归档写出后从没在 LCEDA Pro 里打开过，所以不宣称应用接受它」。
本版把它从"不宣称"改成一次**带控制臂与两条负控制**的实测，并把读数随每次 apply 一起带走。

一句话结论：**同一份归档，控制臂回读 `0.25`、写入臂回读 `19.685`（=0.499999 mm，正是计划点名的
0.5 mm），而计划没点名的另一条线两边都是 `0.3`。** 应用接受我们写的 `.epro`，且只读到我们改的那一个值。

### 判据为什么不是「导入成功」

`extractProjectInfo` 与 `importProjectByProjectFile` 的成功返回**都不是有效判据**：负控制 A
（zip 结构合法，但 PCB 成员的记录流被换成垃圾 `["BOGUS",1,2,3]` + 一条坏 `LINE`）
**两个都返回成功、还照样建出了工程**，只有 `eda.pcb_PrimitiveLine.getAll()` 的回读是 `[]`。
所以本版唯一承认的证据是**几何回读**，负控制 A 就是这个判据会失败的地方。

### 四臂实测（全部 2026-11-01，LCEDA Pro V4.1.60 / bridge `4.1.60.198f38ab`）

| 臂 | 文件 / 字节 / sha256 | `extractProjectInfo` | `import…` 返回 | PCB2 线段回读 |
| --- | --- | --- | --- | --- |
| 控制臂（应用自己导出，本模块没动） | `probe_export.epro` / 108580 / `f339d7b0…` | ok | 建成工程 `d24397f2…` | **`[[2,1,21,1,0.25],[1,5,21,5,0.3]]`** |
| 写入臂（`set_track e1 width_mm 0.5`） | `probe_written.epro` / 108443 / `57d238c9…` | ok | 建成工程 `0fe207c0…` | **`[[2,1,21,1,19.685],[1,5,21,5,0.3]]`** |
| 负控制 A（合法 zip，记录流换垃圾） | `probe_corrupt.epro` / 95966 / `e8ff189a…` | **仍 ok** | **仍 ok、仍建工程** `41a715b1…` | **`[]`（全丢）** |
| 负控制 B（根本不是 zip） | — | 未测 | **`undefined`，没建工程** | — |

### 新增

| 项 | 内容 |
| --- | --- |
| `edit/epro_edit.ACCEPTED_ARCHIVES` | 容器类型 → 实测条目：`measured_on` / `app` / `bridge` / `door`（那条门到底怎么调）/ `unit` / `run`（四条臂，带 `bytes`+`sha256`+`import_returned`+`lines_read_back`）/ `limits`。当前只有 `epro_zip` 一条 |
| `_acceptance()` / `_acceptance_report()` | 命中表 → `{container, accepted_by_app: true, measured, read_back, control_read_back}`；**未命中 → `accepted_by_app: None` + 「nothing is claimed」**，不读成"被拒" |
| `_not_verified_text()` | 命中表时给出两条 sha256、两边回读、以及**四件不覆盖的事**；未命中时**逐字保留 0.0.61 的旧文案** |
| `apply_plan` / `propose` 的 `app_acceptance` | 新增字段（加字段，不改语义） |
| `docs/65-归档写后重开.md` | 那条门的完整参数、`File` 要求、`getProjectFile` 导出回路、base64 送字节、回读链路、四臂表、两个负控制为什么必须存在、本轮没证明的五点 |

### 改了什么

| 项 | 变化 | 为什么 |
| --- | --- | --- |
| `.epro` 的 `apply_plan().not_verified` | 命中表后换成本轮的实测句子（带读数与四个边界）；未命中的容器**原文案逐字保留** | 继续写"没验过"同样是不诚实，只是反方向。**这是一个显式的输出变化**，单独登记在这里 |
| `not_implemented_yet` 第 3 条 | 从「no written archive was re-opened」改成实测读数 + 判据为何是回读 + 四条范围 | 清单不能因为一次成功就整条删掉 |
| `not_implemented_yet` 第 6 条 | 删掉「`.epro` 归档写路径未验」，改成「这条桥本身没走归档（归档那条是另外导入验的）」 | 两件事不许互相顶替 |
| `tests/test_epro_edit.py` | `test_the_result_names_how_to_undo_it` 的断言跟着换；新增 `TestAppAcceptance` **12 例** | 老断言断的是"没验过"，现在是"验过且写明范围" |

`verified_by_app` 的**语义一行没动**：它说的是「这个 op 形态是厂商被观察到写过的」，与「应用接受这份
归档」是两件事。所以一个 `verified_by_app=false` 的写入，即使归档被应用接受了，那句话照样留着。

### 边界（不许读成「立创写路径已经全验」）

- **只对一个 app build**（V4.1.60 / bridge `4.1.60.198f38ab`）。换 build 就是换仪器。
- **只 ZIP 容器**：`.eprj` / `.eprj2`（SQLite）仍 `SQLITE_REFUSED`，`.esch` 仍 `WRITE_UNSUPPORTED_FORMAT`。
- **只一条门**：`props.importOption='ImportDocument'` + `saveTo.operation='New Project'`。
  `Existing Project` 门会把归档**合并进**那个工程；`Offline Client Local Path` 门需要未启用的扩展权限。
- **屏幕未核对**：每条读数都是应用自己的返回值。
- **回读的几何只有 2 条 LINE、1 块 PCB、1 份归档**。
- `importProjectByProjectFile` 之后**同一次 invoke 内**读不到内容（`boards:0 / pcbs:[]`），
  下一次 invoke 才正常——所以不能在同一次调用里判"导入后内容在不在"。
- bridge 的 `open(path)` **打不开 `.epro`**（session alive 但 `getCurrentProjectInfo()` 空），
  `.eprj2` 则完整 ⇒ `open` + alive 不是判据。

### 规模

工具数不变（**168**，本版不新增工具）；`--selftest` 仍是 **213/213**；
`tests/test_epro_edit.py` 49 → **61 例**（新增 `TestAppAcceptance` 12 例）。

| 项 | 读数 |
| --- | --- |
| 全量回归 | **2684 例 / OK (skipped=3) / 1575.615 s**（0.0.61 的 2672 + 本轮 12）。**耗时不作性能结论**：本轮机器上同时有 lceda-pro 的 5~6 个进程在跑（上一版实测时的情况未记录），只如实记这一次量到的是 1575.615 s。`skipped` 这一次是 3，上一版是 5，**没有排查是哪两条不再跳过**，不当作结论 |

## [0.0.61] - 2026-11-01

立创编辑器里的**第一次真机写入**，以及由此打开的一道闸门。0.0.60 之前
`lceda_pcb_write` 的每份答案都硬编码 `verified_against_app: false`，因为**一次都没写过**。
本版在 LCEDA Pro **V4.1.60**（bridge `4.1.60.198f38ab`）上把 create / delete / move / save
各跑了一遍，并把「写进去了」拆成四级证据，然后把 `false` 换成**一张实测表**。

一句话结论：**探针动了 → 工程文件变字节与 sha → 关掉重开几何一致 → 两次整应用重启后读回来
仍是同一组数字。** 闸门只在这一条桥版本上、且这一次效应被读到时才打开。

### 新增

| 项 | 内容 |
| --- | --- |
| `lceda_write.VERIFIED_WRITES` | 「哪条桥版本 → 哪些 op 真跑过 → 哪天量的 → 逐条读数是什么 → 还差什么」。当前只有一条：`4.1.60.198f38ab` / `create, delete, move, save` / `2026-11-01` |
| `lceda_write._gate()` | 三个同时成立的条件：调用方报了桥版本、版本在表里、op 在那条的 `ops` 里**且这一次**效应被读到（change 类必须探针动）。`report["measured"]` 把测量叙事随答案一起带走 |
| `run(..., op=, bridge_version=)` | 两个新关键字参数，默认 `None` ⇒ 走旧路径。`not_verified` 在放行后换成该条目真正的 `still_not_verified` |
| `docs/64-真机写一次.md` | 四级证据的原始读数（project/pcb uuid、tabId、两条 create 的 id、四次字节数与 sha、两次重启后的 `getAll()` 返回值）＋七个实测坑 |

### 改了什么

| 项 | 变化 | 为什么 |
| --- | --- | --- |
| `lceda_pcb_write` 的 `verified_against_app` | 从**硬编码 `false`** 改成**按表放闸** | 而在 V4.1.60 上它真的写进去过；继续打 `false` 同样是不诚实，只是反方向。**这是一个显式的输出变化**，单独登记在这里 |
| `assumptions` / `precision` | 跟着 `report` 走：放行时写「写路径本身在这条桥版本上量过」，并保留 `bridge_version`；不放行时**原文案逐字保留** | 让读者一眼看出 `true` 的含义是「在这条版本上量过」，不是「支持的 API」 |
| `lceda_pcb_write` / `lceda_write_snippet` 的注册描述 | 改口：列出「哪条桥版本 + 哪些 op 才能 true」，并写明生成器**本身**仍未验证 | 能力自述必须与行为一致 |
| `not_implemented_yet` 第 6 条 | 从「任何 WRITE 都没跑过」改成四级证据 + **仍未证明的四条** | 清单不能因为一次成功就整条删掉 |
| `not_implemented_yet` 第 7 条 | 加「一个生成器产出的 create 片段真机跑通过一次」，并登记两个坑（函数体必须 `return`、空串要写 `''`） | 一次成功只担保那一个片段 |

### 实测读数（全部来自 V4.1.60）

- `create('', 1, 0, 0, 10, 0, 0.2)` → 探针 `0 → 1`，id `62e0d3e03e906d83`，`duration_ms 48`
- `save()` → 工程文件 **409600 → 569344** B，sha `a8872034…` → `a14010a1…`
- `closeDocument` + `openDocument` → 同几何复读（layer 1、0,0–10,0、w 0.2）
- `delete(id)` → `1 → 0`；再 save，sha 变 `22fd2d4a…`（size 不变）
- `create('', 1, 1, 1, 21, 1, 0.25)` + save → **585728** B
- **整应用重启**、按 uuid 从磁盘重开 → `getAll()` 仍是 `layer 1、(1,1)-(21,1)、w 0.25`
- 第二轮走工具层：生成器产出 `return await eda.pcb_PrimitiveLine.create('', 1, 1, 5, 21, 5, 0.3);`
  → `lceda_pcb_write` 探针 `1 → 2`；`modify(id,{startX:2})`（`(primitiveId, property)` 是
  `doc.api` 报的签名）→ 探针 `1 → 2`；**第二次整应用重启**后两条线仍在：
  `(2,1)-(21,1) w 0.25`、`(1,5)-(21,5) w 0.3`

### 负控制（这一版新增的判据都能失败）

| 情形 | 期望 | 实测 |
| --- | --- | --- |
| 版本命中 + op 在表里 + 探针动了 | 放行 | `true` |
| 桥版本不在表里（`9.9.9`） | 拒 | `false` |
| 不传版本（老调用方式） | 拒，且 `not_verified` 是旧的两条 | `false`，逐字相同 |
| `op='write'`（调用方自定义） | 拒，哪怕探针动了 | `changed: true` 而 `verified_against_app: false` |
| 版本命中但探针没动 | 拒 | `WRITE_HAD_NO_EFFECT` + `false` |
| `save` | 放行，但明说**这一次**的 save 没被读回 | `true` + 「THIS call's save was not itself read back」 |

### 边界（不许读成「立创写路径已经全验」）

- 探针读到的**不等于**屏幕上看到的：每条读数都是应用自己的返回值。
- 参数写错的片段抓不住：一个「什么都没干」的 create 会被探针抓到，一个「干了别的事」的抓不到。
- `.epro` **归档**写路径仍未验（本篇验的是活编辑器路径，两者不许互相顶替）。
- 厂商 `--mcp` 传输仍不可达；websocket 网关仍需用户手动开「外部交互权限」。
- 探针工程是本服务自己 `createProject` 造在 `D:\lcedapro\project\` 里的，**用户工程全程只读**。

### 顺带量到的坑（已写进 docs/64）

`fn="run"` 收到的是**函数体**（只写表达式回 `null`，必须 `return`）；`argsv` 里的字符串是原样
JS，空串要写 `"''"`；`SNIPPET_NO_RECEIVER` 是因为应用给的 `callPath` 是 `pcb_PrimitiveLine.`
而不是 `eda.<prop>.`；`save()` 是**异步落盘**（一次 save 后 0.1 s 读磁盘还是旧字节、约 8 s 后
才变），所以判落盘必须轮询；应用重启后 `session.list` 可能为空、`getCurrentProjectInfo()` 会
60 s 超时而同一会话的 `getAllPcbsInfo()` 正常；`JSON.stringify` 判空不可靠（空数组回 `'{}'`）；
Electron 窗口不向 UIA 暴露控件，模拟点击点不掉模态框。

### 规模

工具数不变（**168**，本版不新增工具）；`--selftest` 仍是 **213/213**；
`tests/test_lceda_live.py` 45 → **53 例**（新增 `TheGateOpensOnlyWhereAMeasurementExists` 8 例）。

| 项 | 读数 |
| --- | --- |
| 全量回归 | **2672 例 / OK (skipped=5) / 1197.812 s**（0.0.60 的 2664 + 本轮 8）。**耗时不作性能结论**：用例只多 8 个（+0.3 %）却慢了 144 s（+13.7 %），本轮没有单独排查原因，只如实记「这一次量到的是 1197.812 s」 |

## [0.0.60] - 2026-11-01

腔体**外面**的三笔账。`cavity_plane_q` 的 docstring 从 0.0.10 起就列着三件明说不做的事：
radiation Q、Huray 结节模型、以及「真实的开边会把共振往下拉几个百分点」。本版把它们做成
**三个新工具**（`plane_radiation_q` / `plane_open_end` / `roughness_huray`），
**`cavity.py` 的 `plane_q` 一行未改**——旧读数逐位不变。

一句话结论：**辐射 Q 有一条闭式 `εr·A_eff/(h·ΣL_eff)`，方形退化成手算的 `εr·a/(6h)` 与
`εr·a/(8h)`；开边 Δl 与 Yariv 2021 的电容式**恒等**（因子 2 来自引文而不是约定）；Huray 在
`x = 1` 处精确 `1/√5`，而报告出来的 knee 频率必须满足 `δ = r/2`（第一版漏了 `2π`，是测试
拿频率回代算出来的）。**

### 新增

| 项 | 内容 |
| --- | --- |
| `plane_radiation_q` | 平面模从**开边**辐射掉的 Q：`P = ½·ω·ε₀·Σ∫V²dl`（每单位宽度 `G′ = ωε₀ = k₀/η₀`）、`U = ½·ε₀·εr·(1/h)∫∫V²dA`，ω 约掉 ⇒ `Q_rad = εr·∫∫(V/V₀)²dA / (h·Σ_open ∫(V/V₀)²dl)`；顺带按倒数和合成 `q_total`、报 `which_dominates`、`per_edge`（满足 `1/Q = Σ1/q_e`）、`radiated_power_w_at_1v_gap` |
| `plane_open_end` | 开边把共振拉低多少：`Δl = (h/2π)(ln(2π·a/h)+1)`，`a_eff = a + n_x·Δl(a)`，`f_eff = c/(2√εr)·√((m/a_eff)²+(n/b_eff)²)`；另报 `first_order_check`（一阶式 / 精确式 / 相对差 / 界，四者分开） |
| `roughness_huray` | Huray 雪球：`K = 1+(RF−1)·F`、`F = 1/√(1+2x+2x²)`（`x = 2δs/r`）、`RF = 1+1.5·sr`；报 `K`、`Rs`、`excess_loss_pct`、`knee_frequency_hz`、`which_regime`，给 `rq_um` 时**并排**报 Hammerstad |
| `calc/planar.py` | **新文件**（708 行）：`ETA0 = μ0·c`、`EPS0 = 1/(μ0c²)`（算出来的，不是手抄）、`_edges`（列表 / `"all"` / `"none"` / `"x0,x1"`，未知边与重复边都拒）、`_mode_shape`、`_which_dominates`、`_validate_plane`，三个信封函数与全部推导 |
| `calc/roughness.py` | **+88 行**三条纯函数（无信封，与 `roughness_factor` 并列）：`huray_surface_ratio`、`huray_transition`、`huray_loss_factor`；docstring 改口指向它们并写明来源（不再自称 "NOT the Huray model"） |
| `tools.py` | 注册三个工具（`T.TOOLS` 165 → **168**）；`cavity_plane_q` / `pdn_plane_with_caps` / `calc_capabilities` 三处「radiation Q 未实现」的能力自述改口指向新工具（**只改文案，数值路径一行未动**） |
| 测试 | `tests/test_planar.py` **54 例**（6 个类；31 条拒绝各带 `reason_code`，其余每条判据配能翻脸的负控制） |
| selftest | 6 条新读数（Huray 数值与 knee、Huray 光频拒绝、辐射 Q 闭式与 `which_dominates`、`k₀h` 闸门拒绝、开边 Δl 与「(1,0) 看不见 y」），`208/208` → **`213/213`** |
| 全量回归 | **2664 例 / OK (skipped=5) / 1053.292 s**（0.0.59 的 2610 + 本轮 `test_planar.py` 的 54）。**耗时不作性能结论**：用例只多 54 个（+2.1 %）却慢了 179 s（+20.5 %），本轮没有单独排查原因，只如实记「这一次量到的是 1053.292 s」 |
| 工具数 | **165 → 168** |

### 算术锚点（每个都在模块之外）

| 锚点 | 读数 | 判定方式 |
| --- | --- | --- |
| 方形 (1,0) 的辐射 Q | `εr·a/(6h)` = 4.3·50/(6·0.2) ⇒ **179.167** | 手算 `∫cos²`，不是自洽回读 |
| 方形 (1,1) 的辐射 Q | `εr·a/(8h)` ⇒ **134.375** | 同上 |
| `per_edge` 倒数和 | `1/Q = Σ1/q_e`（537.5/537.5/1075/1075 ⇒ 179.167） | 恒等式，`rel ≤ 1e-4` |
| 开边 Δl 对**引文电容式** | `ε₀(a+2Δl)/h == ε₀[a/h + (1/π)(ln(2πa/h)+1)]` | 与 Yariv eq. 5.20 恒等（`rel 1e-5`）⇒ 因子 2 有出处 |
| 一维模的二阶项 | `relative_gap == −ε/(1+ε)`（−1.05313 % vs 一阶 −1.06434 %） | **预测**，不是容差 |
| Huray 在 knee | `F(δ = r/2) == 1/√5 = 0.447214` 精确 | 代数；knee 频率回代 `δ/r = 0.5` |
| Huray 两极限 | `δ→0 ⇒ K→RF`、`δ→∞ ⇒ K→1` | 两条极限 + 「任何频点 `1 < K < RF`」 |

### 登记（修正，不静默改变既有答案）

- **纸面推导曾漏 `k²`**：第一版得到「`P` 与 λ 无关、`h→0` 时不趋零」，与「窄缝趋近全反射」
  矛盾。改正后 `P ∝ h²`；把这类错误挡在模型外面的是 `k₀h < 1` 这条**模型自带**的闸门
  （一条窄缝流失的入射功率份额就是 `k₀h`）。
- **knee 频率漏了 `2π`**（未发布的代码里自查所得，无已发布答案受影响）：定义式 `ω = 8/(μ0σr²)`，
  频率要再除 `2π`；漏掉后报告的 knee 落在 `δ = r/2.5`，测试 `test_knee_frequency_is_where_x_is_one`
  不信这个字段、拿频率回代算 δ 才抓到。
- **测试第一版 20 条红**：信封里的数一律 6 位有效数字，用 `assertAlmostEqual(places=9)` 卡末位
  必然红；改为**相对**比较（1e-5 起）。这条写进 `tests/test_planar.py` 的 docstring，免得下一个人
  再踩。

### 边界（写进 `assumptions` / 精度栏，不是待办）

- 辐射：窄缝 + 无限 PEC 法兰、模态用未扰动 `cos·cos`、边按无限长（**无角点**）、介质与铜同时截止
  （缝不半埋）；对 3-D 求解器差**几十个百分点**，只能用来判「这里辐射到底算不算个事」。
- 开边：`a/h ≥ 10` 的大长宽比渐近式；**只改频率不改 Q**；无角点修正。
- Huray：`σ`/`μ` 单常数、只乘铜的 `Rs`、无箔 profile、无各向异性、`Rz ↔ Rq` **不换算**；
  `r` 与 `sr` 不可与 `Rq` 互推；`sr > 30`、`r > λ₀/100` 拒绝。
- 三个工具**都不给 pass/fail**：共振是否成问题要看激励与耦合，两者都不在模型里。

## [0.0.59] - 2026-10-31

熔断的**长度方向**。`fusing_current` 一直写着 `assumptions.spatial = "one temperature for the
whole trace cross-section and length"` —— 对一根等宽走线这句话只是保守，对**锥形/分段**走线
它换不来唯一的答案：熔在哪一段。本版加一个**新工具**（不是一个新参数，因为单宽度表达不了分段
几何，而且"熔在哪"是新的一维信息）：`fusing_current_taper`。

一句话结论：**均匀段对三个独立锚点（解析绝热式 / Onderdonk / 分段无关性）都在千分之几内，
"传导到底省了多少"由一条真跑一遍的绝热对照臂量出来而不是声称，且 `fusing_current` 与
`repetitive_pulse_rating` 的可执行代码一行未改、答案逐位不变。**

### 新增

| 项 | 内容 |
| --- | --- |
| `fusing_current_taper` | 一段走线（`segments[{length_mm,width_mm}]`）**沿长度**的温度与单发熔断：1-D 轴向有限体积 + 后向 Euler + 逐段 Thomas；可选 `ends_c` 另解稳态并二分出 `steady_threshold_a`；可选 `t_melt_s` **反解电流** |
| `calc/current.py` | 新增常量 `ALPHA_234_CU = 1/254`、`K_THERMAL_CU = 400`、`MELT_CU_C = 1083`；新增 `_taper_adiabatic_current`、`_taper_geometry`、`_thomas`、`_taper_coeffs`、`_taper_run`、`_taper_steady`、`_taper_steady_threshold`、`fusing_current_taper`（`fusing_current` / `repetitive_pulse_rating` 一行未改） |
| `tools.py` | 注册 `fusing_current_taper`（schema 只 required `segments`）；模块级 `C_ALPHA` / `K_TH_CU` 供 schema 默认值与 lambda 共用（防漂移）；`calc_capabilities` 的 `simulation` 层补 `"fusing (one-pulse adiabatic, along a taper with axial conduction, and pulse trains)"`（此前这一族在能力自述里**完全没提**）；`not_implemented_yet` 里把"沿长度的梯度"从"仍未做"删掉并改写成新工具的介绍 + **仍然**不做的东西 |
| 返回块 | `segments[{length_mm,width_mm,area_mm2,resistance_ohm}]`、`profile[{x_mm,t_c,current_density_a_mm2,r_ohm}]`、`hot_segment`、`melted`/`t_melt_s`/`melt_segment`/`melt_at_mm`、`peak_c`/`peak_segment`/`margin_k`、`conduction_delay_pct` + `adiabatic_control`、`steady_threshold_a`、`reverse`/`asked_time` |
| 测试 | `tests/test_fusing_taper.py` **41 例**（新；8 个类，17 条拒绝各带 `reason_code`，其余每条判据配能翻脸的负控制） |
| selftest | 3 条新读数（均匀段 0.993269 s 且 `conduction_delay_pct == 0.0`、颈部熔在 `melt_segment == 1` / `melt_at_mm == 21.0` 且 delay > 5、2 A 不熔时 `value is None`），`205/205` → **`208/208`** |
| 全量回归 | **2610 例 / OK (skipped=5) / 874.399 s**（0.0.58 的 2569 + 41；本机同为无外部满载进程，可与 0.0.58 的 865.034 s 同负载比较） |
| 工具数 | **164 → 165** |

### 算术锚点（三个，互不共享近似）

| 锚点 | 读数 | 差 |
| --- | --- | --- |
| 均匀段（2 mm × 35 µm，25 ℃）对**解析绝热式** `t = ρ_m·c_p/(ρ20·α) · ln((1+α(T_m−20))/(1+α(T_a−20))) / J²` | 20.21 A ⇒ **0.993269 s**（解析 0.993880） | **−0.0614 %** |
| 同一读数对 **Onderdonk** 的 `I = A·√(33·ln10/(t))`（1 s） | 1 s 需要 20.21 A | **−0.6731 %** |
| **分段无关性**：同一根线切成 n=1/2/5/10/40 段 | 全部 **0.993269 s** | **< 1e-9** |

`33` 与今天常数的关系写在这里而不是拟合掉：`ρ_m·c_p/ρ20 = 33.21`（Onderdonk 的 33 是 1940 年代
的铜常数），`33·ln10 = 75.985` 对 `76.46` ⇒ 解析式本身就差 **0.31 %**。**本版不把 33 改成 33.21
去凑 Onderdonk**：那会把"Onderdonk 是旧常数下的闭式"这条事实抹掉。

### 传导是量出来的，不是声称的

`conduction_delay_pct` 不由公式给，而是**真跑一遍对照臂**：`k_thermal_w_mk = 0`、两端绝热、
窗口 `max(at_s, t_melt·1.02)`。均匀段上它**精确 0**（负控制：对照臂不是"少算一点"），
并有测试断言 `k_thermal_w_mk=0` 的正跑与该对照臂**逐位相同**。

颈部扫描（臂 20 mm×2 mm、颈 2 mm、总长 42 mm、35 µm、**30 A**、窗 1 s）：

| 颈宽 mm | `t_melt_s` | 同窗绝热对照 | `conduction_delay_pct` |
| --- | --- | --- | --- |
| 2.00（= 均匀） | 0.450642 | 0.450642 | **0.000** |
| 1.00 | 0.234030 | 0.112355 | **+108.295** |
| 0.50 | 0.039764 | 0.027780 | **+43.137** |
| 0.25 | 0.007593 | 0.006627 | **+14.584** |

平均宽（1.9286 mm）的均匀段 = **0.419000 s**，颈 0.5 mm 比它早 **10.5×**。delay 随颈宽
**非单调**（108 → 43 → 15 %）是真物理：颈越细越快、侧面一热传导的相对贡献就越大；
登记为**不许"修平"**，测试里也不钉单调性。

### 端条件与反解

`ends_c` 给出才谈稳态：另解一个没有 `C/dt` 的线性系统，并对 I 二分。
100 mm×2 mm×35 µm 的 **`steady_threshold_a = 3.049725 A`**（代回稳态峰值 **1083.0017 ℃**）、
颈 0.5 mm 的锥形 **9.427701 A**。端固定让 20.21 A 的熔断从 0.993269 挪到 **1.007784 s**
（+1.461 %）、25 A 从 0.648901 到 0.655064（+0.950 %）。

绝热端（`ends_c=None`）对任何正电流都**没有稳态**，所以 `steady_threshold_a` 回 `null` +
写死的理由——这是**回答**不是缺失。

反解方向（给 `t_melt_s` 求电流，对数二分，下界 = 最窄段绝热电流 × 0.999、上界 ×4 扩）：

| 目标 t | 求得 I | 下界 | 正反往返相对偏差 |
| --- | --- | --- | --- |
| 0.02 s | **39.085049 A** | 35.617071 | −6.5e-4 |
| 0.05 s | **27.861327 A** | 22.526214 | −4.6e-4 |
| 0.10 s | **23.093069 A** | 15.928438 | −2.0e-4 |
| 0.30 s | **19.168011 A** | 9.196288 | −2.2e-4 |

**不熔不给数**：2 A / 1 s ⇒ `value: null`、`peak_c 29.1838`、`margin_k 1053.82`；5 A / 10 s ⇒
`peak_c 471.2996`、`margin_k 611.70`。`status` 仍是 ok（不是错误），但答案里没有任何时间数字。

### 登记（本版开发中自己踩的四个坑，都已修在实现里）

1. **反解返回 `unit = "s"`（问法错位）**：反解会把 `current_a` 覆盖成求得值，后面的
   `if current_a is not None and run["melted"]` 于是误判成"问时间"。加 `asked_time = current_a is None`
   （覆盖前取），`value`/`unit` 与对照窗口三处都改用它。
2. **反解 `t_melt_s = 0.3` 返回 `melted: False`**：二分收敛点上熔断恰好落在窗口末端，
   `temp[i] < melt_c <= new[i]` 的严格不等式在边界上有歧义。最终正跑窗口改成
   `t_melt_target * 1.02`，让穿越严格落在最后一步内部。
3. **`steady_threshold_a` 恒为 `None`**：I 越过失控点后 `_taper_steady` 返回**负温度**
   （`diag` 变负），`peak >= melt_c` 判不出"已过阈值"，二分一直向上扩到 60 次失败。
   改成 `if min(diag) <= 0.0: return None`（数学上就是 M-矩阵性质丢了 = 无稳态），
   阈值二分把 `None` 当成"在上界之上"。
4. **`adiabatic_control` 判为 `null`**：对照臂窗口原来用 `run["at_s"]`（熔断前最后一步）比真实
   `t_melt` 略小。改成 `max(at_s, t_melt·1.02)`。
5. **全量回归第一次跑出 1 个失败**：`tests/test_vibration_mass_copper.py` 里的工具数棘轮
   `assertEqual(len(tools.TOOLS), 164)` —— 它就是设计成加工具必须显式改这个数的，
   按本版 165 更新并标注由来，单文件复测 46 例 OK，第二次全量回归 2610 例全通过。

### 边界

侧向散热**没有 `h` 参数**（这一族是绝热侧面，与 `repetitive_pulse_rating` 的 `h` 不可互相替代）、
段内横向等温、无熔潜热、无集肤、无电迁移、无开断后电弧、脉冲**列车**不在本工具。
`precision` 里写的是 **`OPPOSITE`**：删侧向散热会使电流偏低、抹横向梯度会使电流偏高，
谁赢取决于宽厚比，所以**不给统一误差符号**。`board_ir_drop` 的 A/mm² 粗判据不进本工具。

详见 [docs/62](docs/62-熔断沿长度的一维梯度.md)。

## [0.0.58] - 2026-10-31

电热的**厚度方向**。0.0.57 及之前 `board_electrothermal` 解的是一块**均质板**：热源在顶层铜上、
地板在另一面，而它按同一个 `k_w_mk` 在面内扩散,厚度方向只是一块没有结构的板。本版加
`layer_stack`（**默认关，关时体积与行为零变化**）：N 个层节点、中点法则 slab、层间只有层压板
`k_z`、**对流/辐射只在最上与最下两个表面层**（埋进板里的平面确实没有空气）。

一句话结论：**层堆做出来了，闭式锚点 1e-6 一致、`k_z → ∞` 代数恒等地退化回单平面；但本版
必须先登记一处自己犯的错——第一版在 z 方向也做扫描，`k_z = 1e6`（合法的"层间短路"）时
第一次扫描就宣称收敛，返回的其实是初始猜测，8.5 K 的静默错答案。**

### 新增

| 项 | 内容 |
| --- | --- |
| `board_electrothermal` | 新参数 `layer_stack`（`copper_layers[{z_mm,thickness_um,zones?}]` + `k_z_w_mk?`）；能力自述把 "no 3-D" 改口为 "layered z-direction finite volume, not a 3-D field solve" |
| `calc/electrothermal.py` | `_build_stack`（层节点 / 中点 slabs / 片导 / 逐层铜厚面积平均）、**`_sor_layers` 重写**（面内 SOR + 逐列三对角精确解 + 整块松弛）、`_solve_layers_3d`（返回辐射外层次数）、`op_norm`、双门允许量、`stack_report`、分层过孔报告、`_LAYERED_MODEL` |
| 返回块 | `layer_stack`（`levels[{index,z_mm,slab_mm,copper_um_area_avg,surface,role,t_max_c,t_min_c,t_avg_c}]` / `gaps[{conductance_w_per_k,via_conductance_w_per_k,conductance_with_vias_w_per_k,net_heat_up_w,through_vias_w,areal_rth_m2k_per_w}]` / `hot_level` / `rise_across_board_k`）、`energy_balance.residual_allowance_w` + `residual_allowance`（**仅分层路径**） |
| 测试 | `tests/test_electrothermal_layers.py` **29 例**（新，含 13 类拒绝与 9 条负控制）；既有 196 例电热测试全绿 |
| selftest | 2 条新读数（分层闭式 1e-3 K、`k_z → ∞` 对单平面 0.01 K），`203/203` → **`205/205`** |
| 全量回归 | **2569 例 / OK (skipped=5) / 865.034 s**（0.0.57 的 2540 + 29；本机同为无外部满载进程，可与 0.0.57 的 892.297 s 同负载比较） |

### 登记修正（不静默改变既有答案）

1. **z 方向的假收敛（8.5 K）**：判据"单次扫描最大改动 < `tol_k`"在刚性耦合下第 1 次扫描即成立
   （改动量是 `O(h·A/gz)`）。实测两层无铜 `k_z = 1e6` → **26.2468 °C / `sor_sweeps = 1`**，而正确
   答案是单平面的 **34.771887 °C**。改逐列 Thomas 后 `tol 1e-4/1e-6/1e-8` 给
   34.771012 / 34.771874 / 34.771882（90/142/197 sweeps，差 ≤ 1.3e-5 K）。**只影响分层路径**。
2. **`hc_face = h·ac`**：第一版给每个表面层单平面的两面 `2h·ac`，散热翻倍 ⇒ 能量不平衡 −95.208 %。
3. **`omega = 1.0`**：面内的 1.85 在"整列一块"的松弛上式破坏性的（崩成 0.001 °C / 负温）。
4. **物理性门的分层相对 slack**（`0.002·升幅`）：`k_z = 1e12` 的 `t_min = 24.986143` 会误伤。
5. **分层瞬态补 `t = 0` 那一格**：否则同一个 `points` 在两条路径上 `history` 长度不同。
6. **分层过孔报告要显式覆盖 `extra["thermal_vias"]`**：原先一直显示 "no thermal_vias given"。

**单平面自证**：`discover -s tests -p "test_*thermal*.py"` → 196 例 OK；本轮每处改动都以
`stk is not None` 为界，0.0.57 的输出逐位不变（`test_the_plate_output_is_unchanged` 断言
无 `layer_stack` 键、`energy_balance` 里无 `residual_allowance_w`）。

### 算术锚点（不是自己对自己的回放）

两层均匀源、`alpha_per_k = 0`、每表面层**一个**对流面：`u_top = q(hA+G)/(hA(hA+2G))`、
`u_bot = qG/(hA(hA+2G))` ⇒ 实测 **25.035534 / 25.033734**，手算同值（1e-6），`residual` 3.2e-11。
瞬态容量 **44.1549 J/K** = `rho·cp·A·t`。三层对称（源在中层）@ `k_z = 0.05/0.3/5`：
(27.068/27.445/27.068)、(27.188/27.257/27.188)、(27.217/27.221/27.217) —— 中层最热、两表面对称。
117 个 `d = 0.3` 通孔：`rise_across_board_k` 10.464 → 10.464，`via_conductance_w_per_k` 0.6318
（层压板本身比想象中更会导热，物理上正常）。

### 边界

层内等温（z 方向有限体积，**不是 3-D 场解**）、层间只有层压板 `k_z` + 你列的过孔、无介质各向异性、
无铜-树脂界面热阻、内部层无对流无辐射、过孔仍是点载荷（网格相关）、DC（无趋肤/介质/AC 损耗）。
`layer_stack` 与 `copper_pours` 互斥（同一块铜会算两遍），分层模式下 `conductors` 必须显式给
`z_mm` 且 `layers == 1`，`sink_c` / `barrel_profile` / 非全厚 `span` 一律拒答。

详见 [docs/61](docs/61-电热-层间三维与多平面耦合.md)。

## [0.0.57] - 2026-10-31

振动层补四个缺口。前三处是"能力清单上写着不做"，第四处是**算错了**——而且错得很有道理，
所以一直没被发现。

一句话结论：**不带铜的做法一处没动（无铜路径逐位不变），带铜的应力数字变了，变多少、
为什么变、旧配方错在哪，全部逐条登记在本节。**

### 新增

| 项 | 内容 |
| --- | --- |
| `vibration_plate_modes` | 新参数 `copper_um_bottom`（不对称叠层）、`added_mass_regions`（分布质量）；描述 "Two things" → "Four things" |
| `vibration_plate_outline` | 新参数 `copper_um` / `copper_um_bottom`（广义刚度 `d11/d22/d12/d66`） |
| `vibration_plate_stress` | 新参数 `copper_um` / `copper_um_bottom`（逐面解） |
| `calc/vibration.py` | `_q_terms`、`_layer_z_integrals`、`_inv3`、`_mat3_mul`、`_laminate_section`、`_poly_moments`、`_region_metric`、`_sym_eig_with_mass` |
| `calc/vibration_poly.py` | `_solve_once(..., sect=...)` 与 `dmat` 分支、`composite` 输出 |
| 返回块 | `composite.asymmetric`（`b11_n`/`b_over_a_mm`/`d11_reduced_n_m`/`stiffness_lost_pct`/`neutral_plane_mm`/`z_*_surface_mm`）、`added_mass_distributed`（逐 patch 的 `mass_ratio` 与合计）、应力 `surface_face`/`surfaces_checked`/`von_mises_by_surface_mpa` |
| 测试 | `tests/test_vibration_section.py` **26 例**（新）；既有振动测试 145 例全过 |
| selftest | `200/200` → **`203/203`** |
| 全量回归 | **2540 例 / OK (skipped=5) / 892.297 s**（0.0.56 的 2514 + 26；本机同为无外部满载进程，可与 0.0.56 的 911.376 s 同负载比较） |
| 工具数 | **164（不变）**——四处都是可选参数 |

### 一、不对称叠层：`D* = D − B A⁻¹B`

层序自下而上（底铜 / 层压板 / 顶铜），`z` 原点在**层压板中面**，`A = ∫Qdz`、`B = ∫Qzdz`、
`D = ∫Qz²dz`。`B ≠ 0` 时中面一弯就带出拉伸应变 `ε⁰ = −A⁻¹Bκ`；板子面内自由（没有面内牵引）时
它实际显示的弯曲刚度是**折减刚度**

    D* = D − B A⁻¹B        （中性面 z₀ = B11/A11）

`D` 本身比板子能给出的更硬，用 `D` 算频率会**偏高**。实测（100×80×1.6 FR-4，单面 35 µm）：

| 量 | 值 |
| --- | --- |
| `b11` | 3558.77 N |
| `z₀ = b11/a11` | 0.0815537 mm（中性面朝铜面挪 81.6 µm） |
| `d11`（未折减） | 11.2903 N·m |
| `d11_reduced` | **10.9913 N·m**（软 2.65 %） |
| f11 | **737.558 Hz**（裸板 677.29 / 双面 800.849） |

镜像叠层 `z₀` 反号、`d11` 相同。**对称叠层仍走 0.0.53 起的闭式**（两条路径代数等价，
浮点上差 ~2.7 ppm；统一切换会静默改变既有答案，所以不切）。
适用条件写进 `composite.asymmetric.note`：这是**平态附近**的解，真板会弓起来，那个平衡态不解。

### 二、分布元件质量：`added_mass_regions`

`{"mass_g": …, "center_mm": […], "size_mm": […]}`，**省略 `size_mm` = 整板**（灌封、三防漆）。
一块 patch 上质量按**均匀分布**处理，模态形状用单项式基时矩可以精确积分：

    R_IJ = (∫ s_I s_J dx)·(∫ s_I s_J dy) / (Δx·Δy)

**除以 patch 面积这一步是必须的**：`s` 是归一化坐标多项式，patch 上的平均是均值不是积分。
漏掉它，patch 越小质量贡献越趋近 0——20 g 的 0.5 mm patch 会被读成**裸板**（这就是它的负控制）。
质量矩阵 `M = I + Σβ_m R_m + (点质量项)`，白化用带质量的 Cholesky + Jacobi。

20 g 压在 SSSS 板中心，patch 从 40 mm 缩到 0.5 mm：363.713 → 328.618 → 319.238 → 316.558
→ 316.077 → **316.052 Hz**，点质量（独立代码路径）**316.044 Hz**（2.5e-5 碰头）。
整板 20 g（β = 0.844595）给 **498.682 Hz** = `677.29/√1.844595`。
坏输入逐条拒：挂在板外 / `mass_g ≤ 0` / `size_mm` 有 0 → `ARG_RANGE`；空列表 → `ARG_INVALID`。

### 三、多边形轮廓上的铜

装配从标量 `D` 换成广义刚度 `dmat=(d11,d22,d12,d66)`，**罚项尺度也从 `D` 改成 `sect["d11"]`**
（用错尺度会让"最平一档"选错，而这一档就是误差棒）；面积质量 `mpa += ρ_Cu(t_top+t_bot)`。
`copper_um` 单独给 = **两面相同**（与矩形工具同义；初版当成"仅顶面"，同一块板两个工具给出
10.99 与 14.2，已改）。无铜分支保留原式 `D·jac·st`，**逐位不变**。
正方形轮廓 vs 矩形板：`D11` 同值 14.2、f1 差 0.0118 %（SSSS）/ 0.056 %（clamped）；
锁定读数 SSSS 40×30×1.6 `4588.03`、clamped `8546.05`。铜过厚 → `OUT_OF_VALIDITY`。

### 四、登记修正：铜面应力用错 ν（0.0.56 的真缺陷）

原式 `σ_x = −E_CU z/(1−ν_CU²)·(w_xx + ν_LAM w_yy)`：系数里是**铜的 E 和铜的 ν**，
括号里却是**层压板的 ν = 0.15**（铜是 0.34）。在 `w_xx = 0` 的峰点上偏差**恰好**
是 `ν_CU/ν_LAM = 2.2667` 倍。

**归因（不是"改完看起来对了"）**：用 0.0.56 的公式在 0.0.57 自报的曲率点上回放，
4 阶 × 5 读数 = **20/20 全对、相对差 ≤ 4.3e-6** ⇒ 峰点没移、幅度没变，差异**唯一来源**是
`Q12` 里那个 ν 的层归属。

新式 `σ = Q(z)(ε⁰ + zκ)`（逐层 `(E, ν)`、`κ_xy` 用工程扭转 `2w_xy`、双面时解两个面报较坏一面）。

**前后值（60×40×1.0 clamped，70 µm 双面，`stress_amplitude_mm=0.05`）**：

| 阶 | 读数 | 0.0.56 | 0.0.57 |
| --- | --- | --- | --- |
| 1 | `sigma_1_mpa` | −10.1128 | **−22.9224** |
| 1 | `von_mises_mpa` | 62.9744 | **59.3745** |
| 1 | `surface_strain_ue` | 91.9347 | **208.385** |
| 2 | `sigma_2_mpa` | 13.6333 | **30.9021** |
| 2 | `von_mises_mpa` | 84.8968 | **80.0438** |
| 3 | `sigma_2_mpa` | 27.1399 | **61.5172** |
| 3 | `von_mises_mpa` | 169.005 | **159.344** |
| 4 | `sigma_1_mpa` | −38.9402 | **−88.2644** |
| 4 | `von_mises_mpa` | 242.488 | **228.626** |
| 4 | `surface_strain_ue` | 354.002 | **802.404** |

每阶只有一个主应力变（该阶峰点落在 `w_xx = 0` 或 `w_yy = 0` 上）；`von Mises` 不跟着等比变
（它把 `σ` 与 `τ` 混在一起，`τ/σ = (1+ν)/(1−ν)` 本身随 ν 动）。

**"默认答案不变"的自证**：`legacy=True` 分支（无铜）保留原式，逐位对比 0.0.56 → 0.0.57
共 5 个用例，**无铜用例的所有数字一个不动**，只有新增键与新的 `formula` 文本；
有铜用例的**频率、振型、刚度一个不动**（只有应力按上表变）。
`tests/test_vibration_mass_copper.py` 里有一条断言**依赖了旧的混 ν 数值**（"应力比 = 纯材料因子
5.2874"），已改写为"材料因子 × 曲率混合比"，并把 5.2874 → **5.8741** 登记在此；
注释里写明回退会让它失败。

### 负控制一览（每条新判据都能翻脸）

| 机制 | 负控制 |
| --- | --- |
| patch 按面积平均 | 漏 1/面积 ⇒ patch 缩小时退化成裸板（比较两条路径的差） |
| 用 `D*` 而不是 `D` | 用 `D` ⇒ f11 偏高，落不进裸板与双面之间的夹逼 |
| 应力用本层的 ν | 用层压板的 ν ⇒ `kx = 0` 的点上差 2.2667 倍 |
| `copper_um` = 两面 | 当单面 ⇒ poly 的 `D11` 回到 10.99 而不是 14.2 |
| 对称走旧路径 | 通用截面与闭式差 ~2.7 ppm（测试钉 1e-5 的等价性） |

### 文档

- 新增 [`docs/60`](docs/60-不对称叠层与分布质量.md)（推导、实测、前后值、边界）；
  [`docs/56`](docs/56-振动-铜加强筋与点质量.md) 与 [`docs/19`](docs/19-机械振动.md) 各追加一节指向它
  （并声明 `56.3` 的 5.288 倍是**旧配方**读数）；`docs/README.md` 索引同步（五十九 → 六十）。

## [0.0.56] - 2026-10-31

`dfm_capability` 的 `not_checked_by_design` 里有一条是"阻焊 web"，还有一条是"丝印线宽"，
第三条是"铜到孔壁"。这三条在**每张板厂能力表里都是必填项**（"最小阻焊桥 0.10 mm"、
"最小丝印线宽 0.15 mm"、"铜到孔壁 0.25 mm"），而我们只能回一句"不检查"。这一版把三条
变成**实测**。

一句话结论：**能算的算到出处，不能算的说清差哪一步；工艺参数从文档来，不从记忆来。**

### 新增

| 项 | 内容 |
| --- | --- |
| `calc/dfm.py` 规则 | **11 → 15**：`min_silk_line_width_mm` / `min_silk_text_height_mm` / `min_mask_web_mm` / `min_copper_to_hole_mm` |
| `dfm.plating_um` | 能力文档新增可选键（微米），`read_plating` 校验并转毫米；**从不默认** |
| `board.silk` / `board.mask_apertures` | 板级 + 封装级丝印条目与阻焊开窗投影；`pad["mask_layers"]` / `pad["mask_margin(_source)"]` |
| `present["pad_to_mask"]` | 从 `{present: false, note: "parsed only on demand"}` 变成真解析：`{present: true, expansion: {rule, board_default_mm, pads_from}}` |
| 测试 | `tests/test_dfm_silk_mask.py` **37 例**（5 个类）；selftest 3 条 |
| 工具数 | **164**（不变——`plating_um` 属于能力文档，不是新工具参数） |

### 阻焊 web：不偏移多边形

开窗 = 铜形状按 `margin` 做 Minkowski 膨胀，核是半径 `margin` 的圆盘。对圆盘膨胀有恒等式

    dist(A ⊕ D_r1 , B ⊕ D_r2) = dist(A, B) − r1 − r2

所以 `web = dist(铜A, 铜B) − margin_A − margin_B`，**精确、不含近似**。偏移多边形是
"假精度"的来源，这里绕开了它。`margin` 按 KiCad 的优先级取：pad 自己的
`(solder_mask_margin)` > footprint 的 > `(setup (pad_to_mask_clearance ...))` >
三处都没写 = **0**（这是 KiCad 自己写进 setup 的值，字段留空就是 0），且每块开窗都在
`pads_from` 里记账，能追到是哪个层级给的数。

两条只有踩过才知道的：

- **正反面绝不配对**。`*.Mask` 展开成 `F.Mask` + `B.Mask`；把 `*.Mask` 当一层，量到的是
  **板厚**，不是阻焊桥。
- **`web ≤ 0` 是"合并"，不是"0 宽的桥"**。开窗叠在一起 = 中间没有阻焊，这是常见合法形态
  （大膨胀的密脚 QFN）。报 "0 mm web" 会被读成"桥太细做不出来"。所以合并对数进 `note`、
  判词 `not_applicable`。

读数取整到 **1e-6 mm**：多边形距离带浮点噪声，`0.2999999999999986` 对 `0.3` 的精确比较会
FAIL——那是双精度最后一位决定的判词，不是板子的判词。

### 丝印：字高是 `(size w h)` 的**第二个**数

| 量 | 读法 |
| --- | --- |
| 笔画宽度 | `(stroke (width x))`，回落 pre-6 的内联 `(width x)` |
| 字符高度 | `(effects (font (size w h)))` 的 **`h`**（`EDA_TEXT::Format` 写 `.x` 再 `.y`，`.y` 才是高度） |

**文件没写的宽度不填默认值**：KiCad 的回落值是程序设置，不是这块板的属性；编一个 0.15 mm
进去，"我读不到"就变成了"我检查过了"。这样的条目被**排除 + 计数**，`where` 里明写
`"N other silk item(s) state no line width and are excluded, so the true minimum may be
smaller"`——这句话承认这个数是**上界**，所以它只能用来证"合格"。

开发中真踩的坑：板级图形叫 `gr_line`/`gr_rect`/…，封装级叫 `fp_line`/`fp_rect`/…，**两套
名字空间**。一开始板级循环复用了封装级的 `_GRAPHIC_KINDS`（只有 `fp_*`），结果**每一条板级
丝印、板级开窗、板级铜图形都被静默丢掉**，`board.silk` 恒为空，主夹具的"最细丝印"被一块
0.15 的 text 顶替了设计里的 0.12 line。修法是分开两个元组，并留一条负控制
（`F.Cu` 上的 0.05 mm `gr_line` 不得成为最细丝印）盯住"种类 + 层共同决定归属"。

### 铜到成品孔壁：`dist − drill/2 − plating`

    gap = dist(另一网络的铜, 孔心) − drill/2 − plating

`plating` 来自能力文档的 `plating_um`（微米）。**没给就拒答**：verdict `not_checked` +
`note = "... the finished hole wall is unknown; measuring to the drill wall would answer
a different question"`。负数/非有限数 ⇒ `CAPABILITY_VALUE_INVALID`。

两处排除，两个不同理由：**拥有该孔的 pad**（它的铜是环形环，`min_pth_annular_ring_mm`
管）与**同网络铜**（过孔接出去的走线是连接，不是间距）。

同一块板、同一个 1.05 mm 限值：

| `plating_um` | gap | 判词 |
| --- | --- | --- |
| `0` | **1.100** | pass |
| `100` | **1.000** | **fail** |

**同一条指令换个工艺参数，判词翻面**——所以它必须来自文档，不能来自我们的记忆。

### 仍然不算什么（`NOT_CHECKED_BY_DESIGN` 仍是 8 条）

条数不变，是**收窄**不是删除（`docs/README.md` 里"8 类"那句话因此仍然成立）。三条被
收窄成真正剩下的那部分：**mask-defined pad**（开窗比铜小）与"开窗到板边/阻焊自身边缘的
sliver"；**不写线宽的丝印**与**丝印压焊盘**；**barrel taper / 孔径公差**与**镀后环形环**
（环形环这里仍量到钻壁——镀层会让成品环变小，但改 `min_pth_annular_ring_mm` 就是**静默
改变既有答案**，只登记不动）。

### 判据与负控制

| 负控制 | 抓的是 |
| --- | --- |
| 0.11 mm 笔画对 0.12 限值 ⇒ **FAIL**，0.12 对 0.12 ⇒ pass | 判据方向 |
| `(size 0.6 1.8)` 必须报 **1.8** | 读错字段 |
| `F.Cu` 的 0.05 mm `gr_line` 不得成为最细丝印 | `gr_*` / `fp_*` 混用 |
| 不写宽度的条目被排除 + 计数 | 编默认值 |
| 1.4 间距 − 1.0 盘 − 2×0.05 = **0.300**，对 0.31 限值 ⇒ FAIL | 算术 |
| pad 0.2 / fp 0.15 / setup 0.05 三档 ⇒ 0.150 / 0.100 / 0.300 | 优先级 |
| 一面对两块、另一面一块 ⇒ **绝不**互配 | 面不分 |
| 开窗合并 ⇒ `not_applicable` + 合并对数，不是 0 | 把"没有"当"太薄" |
| `F.Mask` 图形 = 0 膨胀（0.150 − 0.050 − 0.000 = 0.100） | 图形也被加 margin |
| 0 µm ⇒ 1.100 pass / 100 µm ⇒ 1.000 fail | 镀层真的进了算式 |
| 无 `plating_um` ⇒ `not_checked`，**不是** pass | 按 0 算 |
| `plating_um = -1` / `"25"` ⇒ `CAPABILITY_VALUE_INVALID` | 类型与符号 |
| 1.2 盘 / 0.6 孔 ⇒ `lower_bound`，**不是** 0.0 | 环形环当间距 |
| 同网络走线接自家过孔 ⇒ `lower_bound` | 连接当间距 |
| NPTH 孔也被邻网铜约束：0.75 − 0.1 − 0.25 − 0.025 = **0.375** | 只管 PTH |
| `len(RULES) == 15`；`NOT_CHECKED_BY_DESIGN` 仍 8 条 | 目录漂移、删条目当收窄 |
| `read_plating(25) == (0.025, None)`；1 条规则 = 6.7 % | 微米/毫米、分母 |

### 默认答案不变（以及登记过的三处改变）

**没变的**：既有 11 条规则的读数与判词逐位不变（主夹具同一次 `dfm_check`：
`min_copper_gap` **4.4**、`min_copper_to_edge` **0.875**、`passed 15 / failed 0`）；
`dependencies = []` 仍成立；`TOOLS` 仍 **164**；不写这三条限值的既有调用，对应 fact 一律
`why: "...so the sweep was skipped"`，`not_stated` 照旧点名。

**登记过的三处**：`rules_known` **11 → 15**（`coverage_pct` 分母从 11 变 15，同一份 3 条
规则的文档从 27.3 % 掉到 20.0 %——不是覆盖变差，是分母变准）；`present["pad_to_mask"]` 由
false 变 true（0.0.55 那句"只在需要时解析"当时是真的，现在不真了）；`present` 新增 `silk`。

全部落在 [`docs/59`](docs/59-阻焊丝印与成品孔.md)。

### 验证（实测，不是预期）

| 门 | 实测 |
| --- | --- |
| `tests/test_dfm.py` | **28 例**全 OK（主夹具同一次 `dfm_check`：`passed 15 / failed 0`，既有的 `min_copper_gap` **4.4** / `min_copper_to_edge` **0.875** 逐位不变） |
| `tests/test_dfm_silk_mask.py` | **37 例**全 OK（0.013 s，5 个类） |
| `run_server.py --selftest` | **200/200 passed**（0.0.55 是 197；新增 3 条：丝印 0.12 / 字高 1.0 / web 0.300；同一块板 `plating_um` 0 ⇒ **1.100 pass**、100 ⇒ **1.000 fail**；不给 `plating_um` ⇒ `not_checked`） |
| 全量 `unittest discover -s tests` | **2514 例 / OK (skipped=5) / 911.376 s**（2477 + 37；这次本机没有别的满载进程，0.0.55 那次另跑着 5 个 CPU 满载的外部进程、2031.147 s ⇒ 秒数只能跟同一负载下的另一次比） |
| `pcb_agent_mcp.tools.TOOLS` | **164**（不变；`plating_um` 属能力文档，不是新工具参数） |
| `pyproject.toml` `dependencies` | `[]`（不变） |

## [0.0.55] - 2026-10-31

`magnetics_inductor` 的自述原话是 `no core selection and no material table lives here`。
这句话诚实，但它背后的缺口不小：**没有表就答不了"哪个买得到的磁芯装得下这个电感、要几匝、
要不要开气隙、会不会饱和、窗口塞不塞得进"**。这一版补上——补法不是"算一个最佳答案"，
而是**先要一张带来源的厂商表**，再对每个候选部件逐条点名它为什么行、为什么不行。

一句话结论：**表里每个数都能点开来源；每个取不到的数都是 `null` 并写明缺什么；饱和只在
厂商公布过的温度上判、中间温度不插值；损耗是一个实测点，不是一条曲线。**

### 新增

| 项 | 内容 |
| --- | --- |
| `data/magnetic_materials.json` | **新增**，12 种材料 / 16 个磁芯，`verified: true`、`retrieved: 2026-10-31`，**每行带 `source` URL** |
| `magnetics_select_core` | 选型：两条匝数约束取大、峰值磁密、气隙（串联磁阻差）、厂商实测损耗点（或按调用方给的指数外推）、绕组 DC 与窗口；`verdict` 或**具名**出局原因 |
| `magnetics_data` | 把表读回来（`kind=materials\\|cores\\|all` + 材料/形状/厂商/名称过滤）。**不算任何东西**，是带出处的转录 |
| 工具数 | **162 → 164** |

### 表里有什么、没有什么

材料：`mu_i`、`bsat_mt_25c` / `bsat_mt_100c`（**只填厂商明写的那个温度**）、`tc_min_c`、
`density_kg_m3`、`resistivity_ohm_m`、损耗密度参考点 `pv_ref = (kW/m³, B, f, T)`。
磁芯：`ae_mm2` / `le_mm` / `ve_mm3` / `al_nh` / `gap_um` / `amin_mm2` / `core_factor_mm1` /
`mass_g` / `loss_ref`（一个实测点 + 工况）/ E 42/21/15 的骨架 `aw_mm2 = 178`、`mlt_mm = 93`。

**没有的，四句话写在表头 `what_this_is_not`：**

1. **不是 Steinmetz 拟合。** TDK / Ferroxcube 的 `Cm/x/y` 在材料 PDF 里，2026-10-31 用
   `web` 抓 `product.tdk.com` / `ferroxcube.com` 全失败（JS 渲染）⇒ 16 行 `steinmetz` **全是
   `null`**；`select_core` 只在**调用方自己给两个指数**时才把实测点外推。
2. **不是热模型。** 一个 `Rth` 都没找到 ⇒ 只报瓦，不报温升（`delta_t_k` 在任何返回里都不存在）。
3. **不是 B-H 曲线。** `bsat` 是一个点（好一点是两个温度的两个点）；点之间的形状、AL 的直流
   偏置跌落、AL 的温度系数全都没有。
4. **不是全型号。** 16 个磁芯是"厂商页面写全了几何的磁芯"，不是"能买到的磁芯"。

最能说明纪律的一格是 **N27 的损耗密度**：页面给了 `920 kW/m³`，但可达的那段没写 (B, f, T)。
于是它被存成 `pv_quoted_unconditioned_kw_m3`（**引文**），`pv_ref` 是 `null`——同样的
920 kW/m³ 在 200 mT/100 kHz 与 100 mT/300 kHz 下对应完全不同的材料，**没有工况的损耗密度
不是一个可以拿来算的数**，`schema` 里也没给它参与计算的路。

### 选型的三条式子

    N   = max(ceil(sqrt(L/AL)), ceil(L·Ipk/(B_lim·Amin)), 1)
    g   = µ0·A·N²/L − le/µi            （串联磁阻差，无边缘修正）
    Awire = Irms/J,  R_dc = ρ(T)·N·MLT/Awire,  P_cu = Irms²R_dc

`A` 用 **`Amin`**（厂商给了就用）——中心柱比外腿窄的磁芯里通量峰在窄柱上。两条匝数下界
**都报出来**：ELP 18/4/10（N87）在 100 µH / 3 A / 0.3 T 下 `turns_from_al = 6.201737`、
`turns_from_flux = 25.445293` ⇒ **26 匝**、`b_peak = 0.2936 T`（AL 只要求 7 匝，7 匝会把峰值
推到 1.09 T）。这也解释了一个反直觉的数：**同一件 PQ 20/16（N87）100 µH → 16 匝，
100 mH → 15823 匝**（989.4 倍，不是 √1000 = 31.6 倍）——小电感时 `N_al` 赢（∝√L），
大电感时 `N_b` 赢（∝L）。

### 饱和只在公布过的温度上判

`b_limit_t` 优先级：调用方给的 `b_max_t` > `bsat_derate × Bsat(material, T)`（默认
`bsat_derate = 0.80`，是设计余量不是材料参数）。`temperature_c` 只认表里有 Bsat 的温度
（现在 **25 / 100 ℃**），中间温度**不插值**：

| 调用 | 结果 |
| --- | --- |
| 3C90 @ 25 ℃ | **6 个可行** |
| 3C90 @ 100 ℃ | **0 个可行 + 6 个 `unjudged`**（`reason = "saturation_unknown"`，`because = "3C90 has no published Bsat at 100 C in this table"`），`status` 仍是 `ok` |

第一版是"材料缺 Bsat 就整次调用返回错误"——那样一个只有 25 ℃ 数据的材料会把整个问题打死，
哪怕问题本身可答。现在的形态是**能判的照判、判不了的按名挂起**（`unjudged` 在顶层）。

### 损耗是"一个点"

四级路径：Steinmetz（表里恒 `null`，路径保留）→ 厂商实测点且**工况逐项相等**（相对容差
1e-6）⇒ 原样复现 → 要调用方给 `loss_exponents=(x, y)` 才按
`P_ref·(f/f_ref)^x·(B/B_ref)^y` 外推 → 再否则 `core_loss_w = null` + "未知不是零"。

U20/16/7-3C90（实测点 `1.9 W @ 200 mT / 25 kHz / 100 ℃`）：

| 调用 | `core_loss_w` |
| --- | --- |
| `freq_hz=25000, b_ac_t=0.2` | **1.9**（"the vendor's own measured point"） |
| `freq_hz=100000, b_ac_t=0.2` | **`null`**（工况不对，要指数） |
| 同上 + `loss_exponents=[1.6, 2.5]` | **17.460215** |

`b_ac_t` 的语义写死在参数说明里：**交流磁密幅值**（峰峰的一半），不是峰峰值。

### 绕组与窗口：只在该部件真有骨架数据时判

E 42/21/15（N87，2 A rms / 5 A·mm⁻² / k_u = 0.4）：`wire_area_mm2` 0.4、`copper_area_mm2` 2.4、
`r_dc_ohm` 0.031611、`p_copper_w` 0.126444、窗口需求 6.0 / 178 mm² ⇒ `window_fits = true`。
其余部件 `window_fits = null` 并进 `window_image`（"能选但没验窗口"比"假装验过"诚实）；
`require_window_fit = true` 时它们以 `window_unchecked` 出局。绕组只算 **DC**（趋肤/邻近在
`magnetics_winding_ac`，两个工具不互相冒充）。

### 拒答一律带名字

`rejected[].reason` 是封闭集：`saturates` / `saturation_unknown` / `window_overflow` /
`window_unchecked` / `no_geometry` / `material_filter` / `shape_filter`。材料名或形状认不出 ⇒
`NOT_FOUND` + `materials_in_table` / `shapes_in_table`。`feasible` 按 `ve_mm3` 升序，
`limit` **只截列表不改计数**。`not_modelled` 固定输出：无 Rth ⇒ 无温升、DC 偏置 AL 跌落、
气隙边缘场、绕组 AC、AL 容差、任何 Steinmetz 拟合都不算。

### 测试与自检

- `tests/test_magnetic_select.py`：**新增 36 例，全 PASS**。每条判据配一条**能翻脸**的负控制，
  其中两条最值钱：`test_no_steinmetz_triple_is_invented`（将来谁真填了三元组，这条会主动失败，
  提醒重定外推路径）、`test_a_thousandfold_inductance_is_a_thousandfold_flux_turns`（单位滑档
  ——mH 当 H 写——会让比值从 ~1000 掉到 ~31.6，落在断言外）。
- `server.py`：4 条 selftest（本块用 JSON-RPC id `1600`/`1601` 的局部 helper，不走全局编号）⇒ `--selftest` **193/193 → 197/197**。
- 全量回归：**2477 例 / OK (skipped=5) / 2031.147 s**（`2441` + 36 例；同一台机器上另有 5 个 CPU
  满载的外部进程，所以这个秒数只跟同负载的另一次实测比）。既有用例里有一条**故意**要跟着动：
  `test_vibration_mass_copper` 的工具数闸门 `162` → `164`——那条测试的存在意义就是"工具数不能悄悄长"，
  所以新工具加进来必须显式抬这一格，否则它会失败（本轮它确实先失败了一次，抬完再跑干净）。

### 不变

既有磁件工具的可执行代码**一行未改**：`git diff` 在 `calc/magnetics.py` 里只删了 3 行 docstring
（"no core selection and no saturation curve from a real material" 那段改成 `select_core` 的
边界说明），在 `tools.py` 里只删了 5 行工具描述文字。`magnetics_inductor` / `magnetics_core_loss` /
`magnetics_dcm` / `magnetics_winding_ac` 的答案与 `0.0.54` **逐位相同**，新参数全部有默认值，
旧函数签名未动，`TOOLS` 里既有工具名一个未变。

文档：[`docs/58-磁芯选型与厂商材料表.md`](docs/58-磁芯选型与厂商材料表.md)。

## [0.0.54] - 2026-10-31

`calc_capabilities.not_implemented_yet` 第 14 条原先写着「rejects F H J K S W A cards with the
card named in the error」。**这句话是假的**：`F`/`H`/`J`/`K`/`S`/`W` 六种卡全部求解成功，
`A` 的 `gain`/`limit` 也在列，而源码里的 `NOT_IMPLEMENTED_CARDS = {}` 是个空字典。顺着这条
逐条实测 19 条 `not_implemented_yet`，又抓到三处更坏的——**接了活、没干、还照常返回一个看起来
可信的数字**。

一句话结论：**能实测确认的就实现，实测确认不出来的就按名拒答**。没有一处「静默忽略」，也
没有一处「猜一个公式」。

### ⚠ 三处「静默忽略」被换掉（这是**改行为**，但只在别名陈的 deck 上）

| 卡片 | 0.0.53 实测行为 | 0.0.54 |
| --- | --- | --- |
| 二极管 `KF=` / `AF=`（1/f 噪声） | `.noise` 结果在 f=1 与 f=10 上**逐位相同**（卡片读进来了，一分钱没体现） | **实现** `flicker_psd = KF·\|Id\|^AF / f`（A²/Hz，跨结电流源）；两点复现 ngspice-47 到 0.0589 % / 0.069 % |
| `Q` / `M` / `J` 的 `KF=` | 同上，静默忽略 | `.noise` 时 **`NOT_MODELED` 按名拒答**（`.op` 仍照常求解——`KF` 在 DC 点本来就不影响任何东西） |
| `R1 1 0 1k mc(1)` | 返回 `ok`，`value = 1000.0` | **`NETLIST_INVALID`** 按名拒答（`mc(`/`unif(`/`aunif(`/`gauss(`/`agauss(`/`limit(` 六种） |
| `.temp 25 100` | 只解了 25 ℃，100 ℃ 被丢掉 | **扫描**：每温度一次独立求解，返回 `analysis="temperature_sweep"` + `cases[]` |

### 二极管 1/f 的公式是从随包引擎里**反解**出来的，不是猜的

随包引擎（`vendor/ngspice/bin/ngspice_con.exe`，ngspice-47 Windows build）同一个 deck：
f=1 → `2.534181e-08`，f=10 → `8.302515e-09`。自解（只含 shot + thermal）两点都是
`2.283233345e-09`（`D1` 贡献 `2.038732797e-18`、`R1` 贡献 `3.174421712e-18`，`|H|² = 1.9150e5`）。
两点差值之比恰为 **10** ⇒ 频率指数就是 −1。`Id = 3.3227e-5 A` 由 shot 的 `2q*Id` 反推
（`psd = 1.0645775669853785e-23`），反解得

    KF = S_flicker(f)·f / |Id|^AF = 1.00112e-16     （卡片 1e-16，即 +0.11 %）

形状、量纲、位置三者一起被钉死：**跨结的一个电流源，`KF·|Id|^AF/f`**。新实现的对引擎误差
**0.0589 %（1 Hz）/ 0.069 %（10 Hz）**——那 0.11 % 的反解残差 + 两次舍入就是全部，不是拟合参数。

`AF` 是**电流**的指数、不是频率的指数。我一开始写错了（以为 `S·f²` 在 `AF=2` 时恒定），
实测发现 `S·f` 在两个 AF 下都恒定；`AF=2` 的 `S·f²` 假设被三点拟合否掉（拟出负的 `T` 与非零
残差 1.88e-9）。测试里因此写成「两个 AF 下 `S·f` 都恒定」，`AF` 的效果交给另一类：
`AF=1` 时 flicker 之比 == shot 之比，`AF=2` 时 == 比值的平方（线性读法偏差 > 0.1）。

### `Q`/`M`/`J` 为什么不做：引擎自己也不做（这是**实测事实**，写进了 hint）

- BJT：`KF` 从 `1e-15` 扫到 `1e-6`（九个数量级），ngspice-47 的噪声答案**逐位相同**
  （f=1 与 f=10 都是 `6.547292e-09`）。
- MOSFET：`F ∝ KF·Id·TOX/L²` 能拟出来（`TOX`×2 → ×2、`W`×10 → ×10、`L`×2 → ×0.125、
  `KF`×10 → ×10，三点解 `S=T+F/f` 残差 ≤ 1e-14），但 `F` 是**输出参考量**、里面已经含了
  `|H|²`，而 `|H|` 随 `R_D`/`gm`/`W/L` 一起变 ⇒ 器件级公式与电路级增益分不开。**宁愿不做也不猜。**

所以 `.noise` 时的拒答消息里明写：「MEASURED: ngspice-47 … KF from 1e-15 to 1e-6 changed its
own noise answer by 0.0 % - so there is no reference model to copy and `engine='ngspice'` would
return the same thermal+shot number without telling you」，并给出
`extra.devices_with_unmodelled_flicker`（元素名 / 类型 / `KF` 值）让人自己决定要不要删 `KF=`。

### `.temp` 多值是**扫描**，不是「取第一个」

`analysis = "temperature_sweep"`、`temperatures_c = [...]`、`cases = [{"temp_c": t, "result": {…}}]`，
每个 `case` 是**一次独立求解**（不是把同一个 bias 点换个标签）。判据：`case[i]` 的 `nodes_v`
必须与「该温度单独跑一遍」**逐位相同**；负控制是 25 ℃ 与 100 ℃ 的读数本身必须不同
（否则「复制一份、改个温度标签」也能过闸）。实测（`V1 1 0 5 / R1 1 2 1k / D1 2 0 DM(IS=1e-14)`）：
25 ℃ → `0.695820796` V，100 ℃ → `0.570918132` V。

- `temp_c` 参数与 `.temp` 卡片同时出现时，参数赢，但 `_Circuit.__init__` 会登记一条覆盖警告
  （「temp_c argument overrode a .temp card that named …; only the caller's one temperature was
  solved (drop the argument to get the card's own sweep)」）。
- `.temp` 后面一个数都没有 → `NETLIST_INVALID`（不是空扫描 + 空 `cases`）。
- **多条 `.temp` 卡**仍按 SPICE 语义取最后一条（实测：`.temp 25` 之后 `.temp 100` ⇒ 答案就是
  100 ℃ 的 `0.570918132`），这一版没有把它变成累积列表。

### 行尾 token 与未知参数：两处「打了字、没人看」

- R/C/L 的解析器读完 value 就把剩下的 token **全丢掉**，所以 `R1 1 0 1k 2k`、`R1 1 0 1k tc1=0.1`、
  `R1 1 0 1k mc(1)` 全被当成 `1k`。新增 `_tail()`：`R` 一律拒，`C`/`L` 只放行文档化的 `ic=`。
- `E` 卡是**恰好五个字段**（`n+ n- nc+ nc- gain`），六个字段是别的卡（`TABLE`/`POLY` 形态）
  混进来的信号 ⇒ `len(parts) > 6` 即拒。负控制：五字段的 `E1 2 0 1 0 2` 仍答 `node2 == 10.0`。
- **`call_tool` 加了未知参数点名**。起因是我自己手工验证时把 `temp_c` 写成了 `temperature_c`，
  工具**静默忽略**了这个参数并返回了一份完整答案。现在不属于该工具 `inputSchema` 的键会被写进
  `warnings`（「argument(s) … are not in this tool's schema and were IGNORED - the answer below
  was computed as if they had never been given (spelling is exact: …)」）。**不报错**，因为工具
  层并不知道上层是否有意冗余；**正确调用不产生这条警告**、也不产生 `unread_arguments` 键。

### 能力自述的**订正登记**（不是悄悄改掉）

第 14 条那句假声明改成「F H J K S W are SOLVED (0.0.31-0.0.36; **the earlier note that claimed
they were rejected was wrong** and is corrected here), and A cards for the gain and limit code
models since 0.0.39 - what is refused by name is the XSPICE models that carry state or a sample
clock, not these cards.」旧文案原样留在括号里并注明 was wrong。另外两处：噪声机制段加入二极管
flicker + 「frequency-dependent density is evaluated at each swept point」+ `Q/M/J` 的 `KF=` 拒答 +
「ngspice-47 自己 0.0 %」；温度/MC 段改为「多值 `.temp` 是 SWEEP」「`mc(...)` 被按名拒答」。
模块级 `PRECISION` 也从「Noise is thermal 4kT/R, shot 2q*I」扩成含二极管 flicker。

### 默认答案**逐位不变**的自证

- `KF` 不给 ⇒ `_flicker()` 返回 `None` ⇒ `sources` 里**根本不会多出一条**。
- `.temp` 单值（或不写）⇒ 走原路径，不产生 `cases`，信封形状不变。
- 常量密度源 ⇒ `callable(psd)` 为假 ⇒ 走原 `psd_a2_per_hz` 分支。
- 值就是数 ⇒ `_STAT_FUNCS` 不命中 ⇒ 解析结果不变。
- 参数名拼对 ⇒ 无 `warnings`、无 `unread_arguments`。

每一处新增行为都有一条「旧行为还在」的负控制测试兜着，所以「加了功能」与「改了答案」是
**可分辨**的两件事。

### 测试

- 新增 `tests/test_spice_flicker_temp.py`（23 例）：`TheDiodeFlickerReproducesTheEngine`、
  `TheShapeIsFrequencyDependent`、`TheFlickerFollowsTheCurrent`、`TheUnmodelledFlickerIsRefused`、
  `TheTemperatureCardIsASweep`、`MonteCarloValuesAreRefused`、
  `TheCardsThatWereFalselyClaimedToBeRejected`、`NoTrailingTokenIsDropped`、
  `AnArgumentNoHandlerReadsIsReported`。
- `tests/test_spice_jfet.py` 的 `test_flicker_noise_is_refused_rather_than_silently_omitted`
  改口：`.op` + `KF=1e-16` 现在 `ok`（`id == 1e-3`），`.noise` 时 `NOT_MODELED`。
- `server.py` 新增 5 条 selftest（二极管 KF 复现 + `KF=0` 负控制 / Q-M-J 拒答 + 无 KF 对照 /
  `.temp` 扫描 + 单温度逐位对照 / `mc(1)` 拒答 + `1k` 对照 / 未知参数点名 + 正确调用对照），
  并把原「J 拒答 KF」那条改成只拒 `RD`/`RS`/`B`/`CGS`（KF 已搬到噪声期）。
  `--selftest` **188/188 → 193/193**。
- 全量回归：`unittest discover -s tests` → **2441 例 / OK (skipped=5) / 4133.839 s**
  （0.0.53 是 2418 例 / 1329.823 s；这次实测时本机另有 5 个 CPU 满载的外部进程，所以
  4133.839 s **不是**空载基线，两者的耗时不可直接比，用例数 2418 → 2441 才是增量）。

`docs/57-SPICE-1f噪声与温度扫描.md` 记全部实测数字与判据-负控制对照表。

## [0.0.53] - 2026-10-31

`vibration_plate_modes` 从 0.0.10 起一直是「**裸的**均质板」：厚度方向上只有层压板一种材料，
质量只有板自己。于是两个最常见的板级问题它答不对——**外层铜把 f1 抬起来多少**（2 oz 上读低
15 % 以上），以及**一颗 20 g 的连接器把 f1 压下去多少**（读高 2 倍以上）。这一版把这两件事装成
**三个参数**（`copper_um` / `added_mass_g` / `added_mass_at_mm`），不是新工具：工具总数仍是 162。

一句话结论：**铜只做双面对称的薄皮**（进变换截面），**质量只做一个用户给定的点**（秩一白化，
不截断模态）。两者都是闭式，没有一处需要全波或有限元，所以没有一处猜。

### ⚠ 这是**加字段**，且**默认逐位不变**

- `value["composite"]` 与 `value["added_mass"]` 是**新增**字段，不给新参数时**都是 `None`**。
- 铜走 `if cu_t > 0.0:` 的**新展开式**，`else:` 分支是 0.0.52 的旧式逐字保留；
  点质量走 `apply_mass is None` 直通。所以默认路径的浮点运算是**同一条**，不是"应该差不多"。
- 自证：`plate_modes(100, 80, 1.6, material="fr4", mount="custom", bc_x=["ss"]*2, bc_y=["ss"]*2)`
  不给三个新参数时 f1 仍是 `677.290`（与 0.0.52 逐位相同），旧振动测试 3 个文件 99 例全跑通过，
  selftest 里另有一条 `copper_um=0.0` 的**严格 `==`**（连 `composite is None`、`added_mass is None` 一起判）。

### ⚠ `warnings` 的文案变了（内容更窄，不再是一句话打包）

0.0.52 里恒定的那句「component mass, copper stiffening… not modelled」被拆成**条件式**：

| 条件 | 现在的 warning |
| --- | --- |
| `copper_um > 0` | 「copper_um is a SYMMETRIC skin pair (%g um per face); …real 1/2 oz outer layers with inner planes break that symmetry and add a membrane-bending term…」 |
| 没给 `copper_um` | 「copper stiffening is not modelled: pass copper_um … which is what makes f1 read high on a 2 oz board」 |
| 没给 `added_mass_g` | 「component mass is not modelled: pass added_mass_g (and its point) or f1 reads high on a board with one heavy part」 |
| 铜或质量**任一**没给 | 「mounting compliance and shear deformation are not modelled either」 |

也就是说**两条都给的时候，"安装柔度/剪切变形"那一句仍然会出现**（它本来就还没做），
但"铜/质量没建模"那两句会消失。读 `warnings` 的调用方需要按内容而不是条数判断。

### ⚠ 有铜时 `stress` 的**语义**变了：取最外层**铜面**，不是层压板中面

`plate_modes(stress_amplitude_mm=…, copper_um=…)` 现在用 `z_s = h/2 + t`、`E_s = E_CU`、`ν_s = NU_CU`
算应力（最外层已经不是层压板），并在返回值里**明说**：`stress.surface_material = "copper"`、
`stress.surface_z_mm = 0.835`。不给铜时逐位仍是旧口径（`"laminate"`、`0.8`）。

实测（1 oz，amplitude 0.01 mm）：`sigma_max` 0.331804 → **1.7544 MPa**（×5.288
`= (110·0.835/(1−0.34²))/(24·0.8/(1−0.15²))`），但 `von_mises` 0.356682 → **1.56796 MPa** 只 ×4.396
—— 因为 `τ/σ = (1+ν)/(1−ν)` 随 ν 变了。**「不该跟着缩」这件事在测试里用 `assertNotAlmostEqual`
而不是漏掉**。

### 模型

```
D11 = Σ E_i/(1−ν_i²)·∫z²dz      D12 = Σ ν_i E_i/(1−ν_i²)·∫z²dz
D66 = Σ E_i/(2(1+ν_i))·∫z²dz    mpa = ρh + 2·ρ_cu·t
铜皮: ∫z²dz 单面 = ((h/2+t)³ − (h/2)³)/3，双面对称 ×2
K  = d11·T_xx + d22·T_yy + d12·T_mix + 2·d66·T_tw
点质量: M = I + β s sᵀ,  β = m/(mpa·a·b),  c = √(1+β|s|²) − 1,  d = −c/(1+c)
        A = I + d·s sᵀ  (对称),  K' = AᵀKA = AKA,  ω² = μ/mpa,  v = A·y
```

`|s| = 0`（点落在节线上）⇒ 严格 no-op。铜常数 `E_CU = 110 GPa`、`NU_CU = 0.34`、`RHO_CU = 8960`。

### 三道能失败的闸门

1. **变换截面必须被独立积分复现**。测试里另写一份三层（铜/层压板/铜）Simpson 分层积分，
   `D11 / D12 / D66 / mpa` 四项相对误差 < 1e-5；1 oz / 2 oz / 0.5 oz 与另一种材料
   （30 GPa / ν0.2 / 1900 / 2 mm）都跑。**Simpson 必须偶数个区间**——写成 1001 时 D11 读
   14.18974（真值 14.20004，−0.07 %），看着像模型错、其实是求积错，这条坑写进了 docstring。
2. **形状一根不动，且 `f ∝ √(D11/mpa)` 是精确的**。这条有代数根：每一层都满足
   `E/(1−ν²) − 2·E/(2(1+ν)) = νE/(1−ν²)`，加权求和后仍有
   `d12/d11 ≡ 1 − 2·d66/d11 ≡ ν_eff`（FR-4 1 oz 实测 0.2278669，与恒等式差 3.4e-6，
   差异**只来自 6 位有效数字的输出舍入**）⇒ 三个 D 退化成"一个有效 ν 乘 d11"⇒ 模态形状不动。
   实测 `800.849 / 677.290 = 1.18243 = √(14.2/3.5872 ÷ 8.38056/2.96)`，antinode 与 quiet area
   的振型值对裸板**严格相等**。
3. **"只加质量"这个错答案必须被负控制打死**。`677.290/√(3.5872/2.96) = 615.237`，
   比裸板还**低 23.2 %**——一个只把铜皮质量加进去、不加刚度的实现绝不可能落到 800.849。
   selftest 同时判 `_mass_only < 0.95·_plain_f` **且** `f1(1oz) > 1.2·_mass_only`。

### 真值表（FR-4 E24 / ν0.15 / ρ1850，100×80×1.6 mm，SSSS）

| 输入 | f1 (Hz) | D11 / mpa | 备注 |
| --- | --- | --- | --- |
| 无铜 | **677.290** | 8.38056 / 2.96 | 与解析 SSSS 闭式一致 |
| 17.5 µm（0.5 oz） | — | 11.228 / — | 精确 11.228022 |
| 35 µm（1 oz） | **800.849** | 14.2 / 3.5872 | `mass_share_copper` 0.1748 |
| 70 µm（2 oz） | **888.369** | 20.5284 / 4.2144 | `mass_share_copper` 0.2976 |
| 20 g @ 板心 | **316.044** | — | `mass_ratio` 0.844595（板 23.68 g）、`mode_basis_at_point` 13.7777、`whitening_c` 2.5548 |
| 0.001 g @ [0, 40]（节线） | **677.290** | — | 逐位相同，`basis` 0.0、`c` 0.0 |

位置依赖（20 g）：板心 **316.044** < 四分点 (25,20) **436.228** < 近边 (95,40) **645.209** < 节线 **677.290**。

**全秩 vs 单模**：单模锚点 `677.290/√(1+4×0.844595) = 323.6815`，全秩一解 316.044 比它**低 2.36 %**
—— 截断到单模会让答案**偏高**，这是"闭式白化"相对"只留一阶"的分辨读数。
**错误锚点**（用 `β|s|²` 而不是 `β(cᵀs)²`）会给出 ≈190 Hz，与 316 差 66 %——手算的坑，代码是对的。

### 两个数学坑（踩过，写进测试 docstring 防回退）

1. 从 `M = L Lᵀ` 得到的白化矩阵是 **`L⁻ᵀ`，不是 `L⁻¹`**：`X M Xᵀ = I` 只对 `X = L⁻¹` 成立，
   `Xᵀ M X ≠ I`。
2. `L⁻ᵀ` 与闭式 `M^{-1/2}` 相差一个**正交因子**（`X = A Q`）⇒ 两个白化刚度矩阵**逐元不同、
   只有特征值相同**（实测特征值一致到 1.3e-15，矩阵差到 9e-2）。所以测试只比特征值；
   逐元比较在**任何**容差下都是错测试。

### 拒答

| 输入 | `reason_code` | `message` / `hint` |
| --- | --- | --- |
| `added_mass_g = 0` 或负数 | `ARG_RANGE` | `"must be a number > 0"` + `"no component mass"` |
| `added_mass_at_mm = [120, 40]`（板外） | `ARG_RANGE` | `"outside the plate"` |
| `added_mass_at_mm = [50, -1]` | `ARG_RANGE` | `"outside the plate"` |
| `added_mass_at_mm = "here"` | `ARG_INVALID` | — |
| `copper_um = -1` / `"lots"` | `ARG_RANGE` | — |
| `copper_um = 420`（> h/2） | `OUT_OF_VALIDITY` | hint 含 `"thin-plate limit"`；399 仍 `ok` |

### 仍然不建模（`calc_capabilities` 第 19 条已改口）

分布元件质量、元件刚度、**不对称**叠层（外层 1/2 oz + 内层平面会破坏对称、要膜-弯耦合项）、
多边形轮廓路径（`vibration_plate_outline`）上的铜、应力工具那个**独立求解**里的铜、
安装柔度、剪切变形、转动惯量、装配级与焊点蠕变。±10 % 仍是多项式基的误差棒。

### 落点

- `calc/vibration.py`：`E_CU`/`NU_CU`/`RHO_CU`、`_rank1_whiten`（+250/−28 行）。
- `tools.py`：三项 schema + lambda 透传 + 描述；`_capabilities` 第 19 条从
  「no component mass or copper stiffening」改成「ONE point mass and SYMMETRIC copper skins are modelled …」。
- `tests/test_vibration_mass_copper.py`：**46 例**（含独立 Simpson 分层积分、负控制、秩一白化对
  Cholesky 路径的特征值对照）。
- `server.py`：**5 条 selftest**（1613–1619 组），`--selftest` 从 183/183 到 **188/188**。
- `docs/56-振动-铜加强筋与点质量.md`；全量用例 2418 例（实测 1329.823 s）。

## [0.0.52] - 2026-10-31

`filter_waveguide_cavity` 从 0.0.16 起只做 TE101：**长度、Rs、Qc、R/Q 四个数，旁的全拒**。
可真要做腔体滤波器，你问的第一个问题几乎总是"我的带里还坐着谁"——TE011 在同一个频率上
Qc 比 TE101 高 28 %，而 TE111/TM111 是**简并的一对**，它们就贴在旁边。这一版把这件事装成
**三个参数**（`mode` / `modes_upto_hz` / `roughness_um`），不是新工具：工具总数仍是 162。

一句话结论：**任意 `TE_mnp` / `TM_mnp` 都是闭式**（`f_mnp`、`kz`、`d = pπ/kz`、`Qc`、`R/Q`），
模谱只是把同一个闭式在一张 `(m,n,p)` 网格上跑一遍并按频率排序，粗糙度还是 0.0.51 的那个乘子。
**没有一处需要全波**，所以没有一处猜；需要全波的东西（虹膜、探针、辐射 Q）继续点名拒绝。

### 模型

```
f_mnp = (c/2)·sqrt((m/a)² + (n/b)² + (p/d)²),   c = C0/sqrt(er·mu_r)
kz    = sqrt(k0² − kc²),  kc² = (mπ/a)² + (nπ/b)²,   d = pπ/kz
Qc    = ωμ∫|H|²dV / (Rs·∮|H_tan|²dS),  U = ½μ∫|H|²,  Pc = ½Rs∮|H_tan|²
```

TE 的场（`u=cos(mπx/a)sin(nπy/b)sin(pπz/d)` 一组）：`Hz = cos·cos·sin`、
`Hx = −(kz·kx/kc²)·sin·cos·cos`、`Hy = −(kz·ky/kc²)·cos·sin·cos`、
`Ex = −(ωμ·ky/kc²)·cos·sin·sin`、`Ey = +(ωμ·kx/kc²)·sin·cos·sin`；
TM 是 `Ez = sin·sin·cos` 那一支，`Hx = (ωε·ky/kc²)·sin·cos·cos`、`Hy = −(ωε·kx/kc²)·cos·sin·cos`。
面上一维积分的闭式只有两种：`∫sin² = L/2`、`∫cos² = L/2`（索引为 0 时分别是 `0` 与 `L`）——
六面一共 12 个积分，全部解析。

### 三道能失败的闸门

1. **通用闭式必须逐位复现 TE101 的括号式**。同一个物理量、两条独立推导（括号式是老代码，
   `Qc = (π·η/2Rs)·(b/2a)·(1+(a/2b)²)/(1+(a/2b)²+(a/2d)²)` 那一支），差 ≤ `0.11`（`delta=0.11`
   是两路数值精度之和）。任一处的 sin/cos 配对写错，这条立刻翻脸——本版的第 1 个 bug 就是被
   它抓出来的（`q_agreement_pct` 从 4.2 % 掉到 0.0 %）。
2. **每个模 `u_e/u_m = 1`**（`|·−1| ≤ 1e-6`，整数个模）。这条**不能**写成 `u_e/(u_e+u_m) = 1`：
   对任何平衡模后者恒 `0.5`，是一个**永不通过**的闸门；测试里专门有一条
   `test_the_returned_ratio_cannot_be_ue_over_total` 把这件事钉死。改写期间它抓到过一次
   场表达式漏因子（`INTERNAL_CHECK_FAILED (U_e/U = 0.500000000)` 其实是判据错，不是场错）。
3. **模谱里每个模的 Qc 必须等于单独问它的答案**（`≤ 0.11`）。这条抓到第 2 个 bug：模谱每行
   曾用 `f0` 的 `Rs`，15.13 GHz 那行高了 23 %（`√1.5132`）；现在每行按自己的频率重算
   `δs`、`Rs` 与 `K`。

第 3 个 bug 是自述，不是物理：`model` 无条件写 "R/Q"、又把 `", mode chart"` 挂在
`r_over_q` 上（反了条件）——于是空字段旁边写着它的名字，而真的给了模谱的调用反而不说。
两条 `TestTheSelfDescription` 把它钉成「报了才写、给了才写」。

外加两条**能翻脸的负控制**（在测试自己的独立实现上，不走模块代码）：① 只数 `z=0` 一面墙、
   丢掉 `z=d` ⇒ 7848.4 → 9216.2（**+17.43 %**）；② x 墙上把法向 `hx²` 当切向、丢掉 `hy²`
   ⇒ 10966.6 → 12076.4（**+10.12 %**）。两条都远超闸门，说明闸门有牙。（原本设计的第一条
   负控制是"把法向算进切向"，实测差 **0.0**——PEC 壁上 `H·n` 恒为 0，那条对照根本没有牙，
   换成上面这两个索引/面数错位才有效。）

### 真值

WR-90（22.86 × 10.16 mm）、10 GHz、铜、光滑：

| 量 | 值 | 交叉验证 |
|---|---|---|
| `mode` | `TE_1_0_1` | 旧值是 `"TE101"`（见下「可见变化」） |
| `length_mm` | **19.8536** | 与 0.0.51 逐位相同 |
| `surface_resistance_ohm` | 0.02609 | — |
| `q_conductor` / `q_conductor_numeric` | **7821.5 / 7821.5** | 差 `0.0 %` |
| `r_over_q_ohm` | **160.9572** | 括号式同值 |

同频 TE011（16.573220 GHz，`d = 19.8536` 巧合地相同）：`Qc = 8828.7`，同频 TE101 是 6873.4
⇒ **1.2845 倍**——这就是"为什么大家都做 TE011"。测试断言的是**次序**（`> 1.15×`）而不是这个
比值，因为比值随 `b/a` 走。

模谱（`modes_upto_hz = 20 GHz`，9 个模，`next_mode = TE_2_0_1 @ 1.5132·f0`）：

| 模 | f (GHz) | Qc | 退化 | 是工作模 |
|---|---|---|---|---|
| TE_1_0_1 | 10.0000 | 7821.5 | 1 | ✔ |
| TE_2_0_1 | 15.1324 | 9837.8 | 1 | |
| TM_1_1_0 | 16.1451 | 8389.5 | 1 | |
| TE_1_0_2 | 16.4624 | 9852.4 | 1 | |
| TE_0_1_1 | 16.5732 | 8828.7 | 1 | |
| TE_1_1_1 | 17.8232 | 7473.0 | **2** | |
| TM_1_1_1 | 17.8232 | 7226.5 | **2** | |
| TM_2_1_0 | 19.7396 | 10686.1 | 1 | |
| TE_2_0_2 | 20.0000 | 11061.2 | 1 | |

方腔（20×20 mm）在 10 GHz 上 `TE_1_0_1` 与 `TE_0_1_1` 退化数都是 **2**、频率相等（`Δ ≤ 1 Hz`），
而 `TM_1_1_0` 是 1——"退化"是按 `(m,n)` 交换算的，不是按 TM/TE 算的。

介质/磁性填充（30 × 15 mm，`er 2.2`、`mu_r 1.2`，12 GHz）：`d = 7.9535`、`Qc = 5934.2`、
`q_agreement_pct = 0.0000`。

### 一处真缺陷的修复（显式登记）

**0.0.51 及以前，`er ≠ 1` 或 `mu_r ≠ 1` 会被直接拒答**——不是物理不允许，是两路推导用了两套
常数：场积分的 `_te101_q_numeric` 写死真空 `EPS0`/`MU0`，括号式用介质里的 `c`，于是
`q_agreement_pct` 报 `−68.43 %`、工具按"两路不一致"拒答。现在两处都吃填充（`eps = EPS0·er`、
`mu = MU0·mu_r`，所有 `ωε`/`ωμ` 与 `u_e = ¼ε∫|E|²` 同步），差 **0.0000 %**。
**默认（`er = mu_r = 1`）下逐位不变**——`git archive` 旧树逐字段 diff 已证。

### 可见变化（默认答案逐位不变，但字符串变了）

| 位置 | 0.0.51 | 0.0.52 |
|---|---|---|
| `value["mode"]` | `"TE101"` | `"TE_1_0_1"` |
| `model` | `"rectangular TE101 cavity: resonance, wall-loss Qc, R/Q"` | 同构：`"... TE_1_0_1 cavity: resonance, wall-loss Qc, R/Q"`；非 TE101 改成 `"(R/Q is TE101 only)"`；真的给了模谱才追加 `", mode chart"` |
| `basis` / `precision` / `assumptions` | 只讲 TE101 | 讲任意模 + 模谱 + 拒绝面 |
| `not_computed` | 5 条（其中两条是"假想"） | **仍是 5 条**，措辞改成"pass roughness_um" / "pass modes_upto_hz" |
| 数字 | — | **全部逐位相同**（19.8536 / 0.02609 / 7821.5 / 0.0 / 160.9572 / 7821.5），`b_assumed` 同值 |

登记理由：`"TE101"` 与 `"TE_2_0_1"` 混在一张表里无法无歧义解析（三位单数字只能表达个位数索引），
所以统一成下划线形式；**数字一个都没动**。三种拼法都可读：`TE101`（仅单数字）、`TE_1_0_1`、
`te 1 0 1`（大小写与分隔符不敏感）；索引 ≥ 10 必须是下划线形式。

### 参数与拒答

`mode`（字符串）、`modes_upto_hz`（Hz）、`roughness_um`（与 0.0.51 同名同义：RMS `Rq`，
`Rs *= K` ⇒ `Qc /= K`，模谱每行按自己的频率各算一个 `K`）。

拒绝（全部 `ARG_INVALID` / `OUT_OF_VALIDITY` / `ARG_RANGE`，文案带原因与出路）：

* `TE_1_0_0` ⇒ `TE_mn0` 的 `p ≥ 1`（`Hz ∝ sin(pπz/d)`，`p = 0` 时恒零，不是模）；
* `TE_0_0_0` ⇒ `TE_mnp` 至少一个横向索引非零（否则 `kc = 0`）；
* `TM_0_1_1` / `TM_1_0_1` ⇒ `TM` 要 `m ≥ 1` 且 `n ≥ 1`（否则 `Ez` 恒零）；
* `TM_1_1_0`（单模问）⇒ `OUT_OF_VALIDITY`：场沿长度均匀，**任何长度**都谐振在其二维截止
  频率（`16.1451 GHz`），长度不是它决定的；**但模谱里照列**（它的 `d` 在表里有确定值，
  物理上合法）并给出二维截止频率让你自己排期；
* 低于该模二维截止 ⇒ `OUT_OF_VALIDITY`（报截止频率）；
* `modes_upto_hz` 会超过 200 个模 ⇒ `ARG_RANGE` 并报**实际个数**（WR-90 @200 GHz 是 11454 个）。
  上限 200 是"表还会读"的边界，不是物理边界。

### 落点

`calc/filters.py`（`_sin2`/`_cos2`/`_parse_cavity_mode`/`_cavity_mode_name`/`_cavity_q_closed`/
`_cavity_q_numeric`/`_cavity_mode_chart`，TE101 默认路径仍走老的 `_te101_fields`/`_te101_q_numeric`）、
`tools.py`（描述、schema、透传、`_capabilities` 第 20 条）、`server.py`（自检 1609–1612 组，
**五条**读数：通用闭式 vs 括号式、每模 `u_e/u_m`、模谱、粗糙度、填充腔）、
`tests/test_waveguide_modes.py`（**29 例**，含**自写独立壁损积分**——用 `|H|²−|H·n|²` 从来不点
切向分量，所以不会"跟闭式一起错"）、`docs/55-波导腔全模谱与粗糙度.md`。
自检 **178 → 183**，回归 **2418 例 / OK (skipped=5) / 1329.823 s**（0.0.51 是 2343 例 / 1242.191 s）。

## [0.0.51] - 2026-10-31

第 52/53 篇的铜损都假设铜是**光滑的**。真实铜箔不是：电解铜箔的毛面是几微米的山峰，HVLP
箔也有 0.3~1 µm；1 GHz 以上"Rs 比光滑模型高 20~80 %"是行业共识。这一版把这件事装进**参数**
（`roughness_um`），不是新工具——粗糙度是 `Rs` 的一个乘子，不是新物理模型，
而"默认关时答案逐位不变"这个契约用参数表达最干净（旧树逐字段 diff 已证）。

一句话结论：**乘子只有一个，它乘的是 Rs，所以 `Qc` 是除以 K（不是除以 K²）**。这一点正是
最容易写错的一处，也是本版用第二条代码路径去交叉验证的地方。

### 模型

Hammerstad–Jensen（1980）的一阶形式：

```
K = 1 + (2/π) · atan( 1.4 · (Rq/δs)² )        K < 2（arctan 有界于 π/2，从下方逼近）
Rs_rough = K · Rs_smooth                      σ_eff = σ / K²
```

`δs = sqrt(1/(π f μ0 σ))`。三个锚点被测试钉到 5 位小数（δs 的整数倍）：

| Rq/δs | 1 | 2 | 3 | 5 | 10 | →∞ |
|---|---|---|---|---|---|---|
| K | **1.60514** | **1.88750** | 1.94958 | 1.98182 | 1.99545 | → 2 |

`K(δs) = 1.60514` 是说**一个趋肤深度的粗糙度就已经让 Rs 涨 60 %**。`K < 2` 不是装饰：
它说明这个形式在物理上不会让电流走两倍以上的路，所以"Rq 给到 100 µm 得到 K = 3.7"这种读数
一定是公式写错了（浮点上 `u ≳ 1e7` 之后 K 会被舍入成恰好 2.0，也不会超过，测试对 `1e9·δ`
与 `1e15·δ` 都断言 `≤ 2`）。

**输入是 Rq（RMS），不是 Rz**：模型里的 Δ 就是高度 RMS 值。箔规格书给的是 Rz，两者之间
没有固定换算因子（取决于高度分布），所以**不换算**——参数说明与 `not_computed` 里直接写明
"Rz is NOT converted"。给个假换算比不换算更坏。

### Qc 除以 K，不是除以 K²

`plane_q` 的铜损是 `Qc = ωμh/(2Rs) = h/δs`，`Rs *= K` ⇒ `Qc_rough = h/(δs·K) = Qc_smooth/K`：
`δs ∝ σ^(-1/2)`，`σ → σ/K²` 使 `δs → K·δs`，`Qc` 只吃 K 的一次方，而电阻 `Rs = 1/(σδs)`
吃 `K²`。半条命的地方在于"σ_eff = σ/K²"就在旁边，很容易把这个 K 挪到 Qc 上。

**交叉真值**因此就是这条：`q_conductor(σ, rq) == q_conductor(σ/K², 不给 rq)`，两条代码路径
在 1 GHz、2δ 上实测 **405.626 对 405.6262**（相对 6e-7，即 6 位有效数字的舍入）。一个把
K² 分给 Qc 的实现会落在 **214.9**——差 89 %，不会被当成噪声放过。（这条期望式我第一遍
自己写成了 `/K²`，是被实测打成 405.6 才纠正的，登记在此。）

### 三条真值 + 四条会失败的对照

真值：① σ_eff 恒等（上面那条，相对 ≤ 2e-6）；② 默认逐位不变（`roughness_um` 省略时
`plane_q` / `pdn_plane_impedance` / `pdn_plane_with_caps` 的整个 dict 与 0.0.50 相同）；
③ `1/Q = 1/Qc + 1/Qd` 仍成立，`which_dominates` 按新的 Qc 判。

会失败的对照：① **介质侧逐位不动**（`q_dielectric`、`tan_delta` 给不给 `roughness_um`
必须**完全相等**，不是接近）；② `K < 2`；③ **同一个箔高频更狠**（2 µm 箔：1 GHz 1.57834 /
2 GHz 1.7633 / 10 GHz 1.95045，把 `δs` 的平方根写成线性这条会反转）；④ **方向**：曲线峰值
必须**不高于**光滑，而**谷必须变浅**（|Z| 变大——谷是两板间的近短路，阻尼把它填平）。

### 实测读数

平面 Q，1 GHz、h = 1.6 mm、tanδ = 0.02（δs = 2.08981 µm）：

| Rq (µm) | K | `q_conductor` | 相对光滑 | `q_total` |
|---|---|---|---|---|
| 光滑 | 1 | 765.621 | 1 | 46.9349 |
| 0.3 | 1.01836 | 751.816 | 0.9817 | 46.8821 |
| 1.0 | 1.19749 | 639.355 | 0.8351 | 46.3734 |
| 2.0 | 1.57834 | 485.081 | 0.6336 | 45.3278 |
| 5.0 | 1.92097 | 398.559 | 0.5206 | 44.4266 |

Qc 掉到 52 %，`q_total` 只从 46.93 掉到 44.43（−5.3 %）——因为 **FR4 的 Qd = 50 才是主导**。
换成铜主导（0.1 mm、tanδ 0.002，2 GHz）：`59.60 → 35.64`，**−40.2 %**。**判据必须落到 Qc
上**，落到合成量 Q 上会在介质主导时把 48 % 的量级读成"没有"。

整条曲线（50×50 mm、εr 4.4、t 1.6 mm、tanδ 0.02、端口 `[12.5,25]`、焊盘 0.5 mm，order 256）：

| 量 | 光滑 | Rq = 2 µm |
|---|---|---|
| `peak_z_ohm` / `peak_freq_hz` | 92.4503 @ 2.85841 GHz | **91.1734 @ 28.5841 MHz** |
| 同一模频点 2.8584089 GHz 的 \|Z\| | 92.4504 | **89.9413**（−2.71 %） |
| `dip_z_ohm` / `dip_freq_hz` | 1.75989 @ 1.55812 GHz | **1.80715**（变浅） |
| `first_mode_q` / `_copper` / `_dielectric` | 47.4101 / 915.295 / 50 | **45.7925 / 544.17 / 50** |
| `truncation_order` / 耗时 | 256 / 2.26 s | 256 / 2.30 s |

**峰"换了身份"**：`peak_z_ohm` 从 92.4503 变 91.1734，看数字像"几乎没变"，其实位置从
2.86 GHz 掉到 28.58 MHz——那 91.17 Ω 是带边缘的平板电容值（`1/(ωC_pp)`），模峰被压到
89.94 Ω 之后全带最大值就变成了容性那一头。工具没撒谎，是"峰值"这个量在多机制叠加时会改主意：
要看 `peak_freq_hz`，或者在你要的模频点上取 `curve` 的一行。这一版把它写进 `not_computed`
之外的正文，因为它是**读法**问题，不是模型问题。

### 参数与拒答

三个工具各加一个可选 `roughness_um`（`cavity_plane_q` 放在 `value["roughness"]`；
`pdn_plane_impedance` / `pdn_plane_with_caps` 放在**顶层** `roughness`）。块里给
`rq_um` / `loss_factor` / `skin_depth_*` / `q_copper_smooth_*` / `q_copper_with_roughness_*` / `model`。

* `roughness_um < 0` 或非数值 ⇒ `ARG_RANGE`（粗糙度不可能为负）；
* `roughness_um` 给了但 `conductivity_s_m = null`（理想铜）⇒ `ARG_RANGE`——修正的是**铜损**，
  没有铜损时它无对象，静默忽略等于骗人；
* `roughness_um = 0` **不拒**：读数逐位等于省略，但多一个 `loss_factor = 1.0` 的块
  （"你确实问了"的记录）。

### 一处标签修正（显式登记，数字未变）

`feedline_impedance` 从 0.0.4x 起就有 `roughness_um`，用**同一个公式**，但说明写的是 "Ra"。
Hammerstad–Jensen 的输入是 RMS（Rq），"Ra"（算术平均）不是同一个量——**标签错，不是数字错**。
本版：① 公式搬到 `calc/roughness.py`，`feedline` 与平面 Q 共用一份（测试断言两条路径相对差
≤ 1e-15）；② 说明改成 "Rq (RMS)"。**数字一个都没动**（`git archive` 旧树逐字段 diff 已证）。
顺带统一 `μ0`：`feedline` 里原写 `1.25663706212e-6`（2019 SI 修订**之前**的测量值），现在与
`roughness.py` 共用 `4π×10⁻⁷`（安培的定义值）；相对差 **5.4e-10**，远在 6 位有效数字之下，
逐字段 diff 里没有一个数字变化。

### 默认关：逐位不变（运行时证据，不是声明）

`git archive HEAD | tar -x -C _scratch/old050` 造 0.0.50 旧树，同一脚本分别跑旧/新树再逐字段
diff：**只有两条措辞变化**——`q1/not_computed[1]` 与 `q2/not_computed[1]` 追加了
"- pass roughness_um for the Hammerstad-Jensen multiplier"；**没有任何数字或键变化**。
`pdn_plane_with_caps`（50×50 / er 4.4 / 0.5 mm / tanδ 0.02 / port [25,25] / pad 0.5 mm /
1 颗 100 nF、1 nH、0.01 Ω，`max_order` 512、tol 0.05）旧新 JSON **完全 identical**。
`dependencies = []` 仍成立（只用 `math`）。

### 不做（有意）

Huray 雪球（要结节半径与密度，那是箔的工艺数据）、具体箔的 profile（一维 RMS 表达不了）、
各向异性粗糙度（压延铜条纹要方向与角度）、除 δs(f) 之外的频率依赖、介质咬铜（属介质侧）、
**Rz→Rq 换算**（没有固定因子）。

### 测试与文档

- 新增 `calc/roughness.py`（`MU0` / `skin_depth_m` / `roughness_factor` / `effective_conductivity`，
  唯一一份公式）；`calc/power.py` 的 `_plane_q_of` / `_plane_modal_z` / `_plane_modal_matrix`
  加 `rq_m`，两个曲线函数加 `roughness_um`；`calc/cavity.py` 的 `plane_q` 全套（含条件化的
  `precision` / `not_computed` / `assumptions`）；`calc/feedline.py` 改用共享公式；
- 新增 `tests/test_roughness.py` **19 例**（5 个 TestCase：锚点与饱和、σ_eff 交叉、Qd 逐位不动、
  曲线峰不升谷变浅、理想铜拒答、两条路径 1e-15）；
- `--selftest` 175 → **178/178**（id 1604–1608：三个 K 锚点、σ_eff 交叉、Qd 不动 + 无 `roughness`
  键/有 `roughness` 键 + Qc 至少掉 25 %）；
- 新增 `docs/54-表面粗糙度与导体Q.md`；`docs/20` §20.8 追加 0.0.51 段与自检段；
  `docs/README.md` 加第 54 条；
- 全量回归 **Ran 2343 tests / OK (skipped=5) / 实测 1242.191 s**（0.0.50 是 2324 例 / 1075.690 s）。

## [0.0.50] - 2026-10-31

0.0.49 把平面自己的整条曲线算出来了，但它每次返回都写着同一句边界：「不报平面-电容反谐振」。
这一版把这条边界收掉——不是"加一个峰检测"，而是**把电容放进同一个网络**：腔体当成
`(N+1)` 端口 Z 矩阵（端口 0 是注入点、1..N 是电容站），电容并在自己的端口上，然后**端口降阶**。
降阶不是拟合，是线性网络的恒等变换，所以同一结果可以用第二条完全不同的路径重算出来对账。

**这一版同时修了 0.0.49 的一个判据缺陷**（收敛门），并把"默认答案逐位不变"作为修正的
前置条件证了一遍。修正登记在下面单独一节，不与新功能混读。

### 模型：端口降阶，不是拟合

```
Z_in(f) = Z00 − Z0c · (Zcc + diag(Z_c))⁻¹ · Zc0
Z_c,k   = esr_k + jω·esl_k + 1/(jω C_k)
Z_ij    = jωμ0h/(ab) · Σ_mn  P_i(m)P_j(m) Q_i(n)Q_j(n) / (k_mn² − κ²)
P_i(m)  = √c_m · S_x,i(m) · cos(mπx_i/a),   Q_i(n) = √c_n · S_y,i(n) · cos(nπy_i/b)
```

每个端口带**自己的孔径（焊盘）** `size_mm`（不给就复用 `port_size_mm`），所以电容摆在
焊盘上、注入探针也在焊盘上，两者都靠 `sin(u)/u` 正则化。`m=n=0` 项走同一公式（`S=1`），
不需要特判。**每个模只做一次除法**，被所有 `(i,j)` 共用：15 个矩阵元素在 order 256 上约
0.24 s（单元素 0.04 s 的 6 倍加速）；`np_=1/2/3` 写死展开再快 2.5 倍（0.0768 → 0.0303 s），
`np_≥4` 走通用路径。复线性方程用自写的部分主元高斯消元 `_csolve`，奇异回 `None` ⇒
`NO_SOLUTION`（宁可报错不给错答案）。

### 三条独立真值 + 四条会失败的对照

真值（都在 `tests/test_plane_caps.py` 里，不是"看着像"）：

1. **零电容恒等**：`caps=[]` 与 `pdn_plane_impedance` 逐点 `1e-9` 相对相等、`peak` 同值、
   `truncation_order` 同为 256——降阶公式在 `Z_c → ∞` 时必须退化成原模态和。
2. **开路恒等**：`c=1e-18` 时 3 个频点与 `pdn_plane_impedance` 相对差 **0.0**
   （`0.574742 / 87.3555 / 8.3159` 全等），且 `peak_at_cavity_mode = True`。
3. **导纳路径重算**：`Y = inv(Zcav)`，对角加 `1/Z_c`，再求逆取 `[0][0]`——与降阶公式独立，
   实测相对差 ≤ **2e-6**（6 位有效数字输出下只剩末位差异）。

会失败的对照（负控制，任何一条哪天被"顺手"弄成恒真，立刻挂）：

* **位置选择性**：(0,2) 模的波腹 (25,25) 上 `|Z| = 60.2092` 对同频 plane-only `92.4504`
  = **0.6513**（阻尼住）；同一模的节点 (25,12.5) 上 `92.4709 / 92.4504` = **1.0002**（不动）。
  只查幅度会被"到处都降"骗过，所以这条必须成对。
* **ESL 方向互换**：1 nH 的反谐振峰在 **440 MHz**、4 nH 在 **279 MHz**（各 >5 倍幅度差，
  且 `peak_freq_hz` 精确等于网格点）。
* **零电容恒等**在曲线层再查一次（同上第 1 条）。
* **无源性**：所有点 `Re(Z)/|Z| ≥ −1e-9`；实测 `worst_re_over_abs_z = None`（没有负实部）。

### 0.0.49 收敛门的修正（登记，不是新功能）

0.0.49 的门只看"最后一阶的差 < tol"。加上电容以后，实测**加载曲线**的增量是
16 阶 6.83 % → 32 阶 11.61 % → 64 阶 21.03 % → 128 阶 **22.31 %**（比 64 阶还大）→
256 阶 2.54 % → 512 阶 0.65 %。单条件门会在 128 阶之后的某个"差小"点放行，而那里答案还
差 2.5~9.7 %。三处修改：

1. **双条件门**：`rc <= tol` **且** `len(increments) >= 3` **且** `rc <= 0.5·prev_rc`
   （增量自身必须减半）；同时门控 **`Z_in` 与 `Z00` 两半**，各自按自身带内峰值归一，
   保证 plane-only 与加载两侧同阶可比。
2. **空探针集兜底**：`freqs_hz` 只给一个频点（或窄于 1.7× 的带）时 `probe_f` 为空 ⇒
   门"无物可测"、在 order 64 就放行；实测该点 order 64 与 128 差 **9.7 %**。
   修法 `if not probe_f: probe_f = sorted(set([lo, hi]))`（两处，`assert count == 2`）。
3. **`pdn_plane_with_caps` 的 `max_order` 默认 1024**（plane-only 仍 256 不变）：0.5 mm 焊盘
   默认带，中心位置 512 阶接受，但 **(0,2) 节点位置要 1024**（512 阶还动 2.1 %）——那里的
   加载 `|Z|` 是两个可比数之差。默认应当能回答默认问题。

**默认答案逐位不变的自证**（这条修正没有动 0.0.49 的答案）：默认带扫描仍
`[16:0.062851, 32:0.0596887, 64:0.0501057, 128:0.0252258, 256:0.00203222]` ——
128 阶的 0.0252 只比 `0.5×0.0501 = 0.02505` 大一点点，因此**仍在 order 256 接受**；
读数仍为 `peak 92.4503 Ω @ 2.85841 GHz`、`dip 1.75989 Ω @ 1.55812 GHz`、106 点、约 2.13 s。

**带电容的旧读数作废并重算**：0.0.50 早期版本报过 `158.46 Ω / 439.6 MHz`，那是 256 阶
就放行的结果（该阶自身还差 2.5 %）。修正后同一颗电容（100 nF / 1 nH / 0.01 Ω，焊盘 0.5 mm，
中心 (25,25)）为 **`peak 163.96 Ω @ 417.929 MHz`、order 512**，`plane_only_peak 92.4589`、
`peak_ratio_vs_plane 1.77333`、`at_plane_peak_damping 1.53497`、`peak_at_cavity_mode False`、
`dip 1.75833 Ω @ 1.55812 GHz`，约 **18.27 s**。

### 实测读数（50×50 mm、εr 4.4、t 1.6 mm、tanδ 0.02、端口 [12.5,25]、焊盘 0.5 mm）

| 场景 | 结果 | 阶数 / 耗时 |
|---|---|---|
| plane 默认 | peak **92.4503 Ω @ 2.85841 GHz**、dip **1.75989 Ω @ 1.55812 GHz**、`plane_anchor 0.14916 %` | 256 / 2.13 s |
| `caps=[]` | 与 plane **逐位相同**（106 点 `freq_hz`/`z_ohm` 全等） | 256 / 2.09 s |
| 1 颗 100nF/1nH/0.01Ω @ 中心 | peak **163.96 Ω @ 417.929 MHz**、ratio **1.77333**、damping **1.53497**、`low_freq_anchor 0.000217935 %` | 512 / 18.27 s |
| 同上，挪到 (0,2) 节点 (25,12.5) | peak **142.966 Ω**、ratio 1.54625、damping **0.999778** | **1024** / 70.0 s |
| (0,2) 波腹 vs 节点（单频 2.8584089 GHz） | 波腹 **60.2092** / plane 92.4504 = 0.6513；节点 **92.4709** = 1.0002 | 256 |
| 开路恒等 `c=1e-18` | 3 点与 `pdn_plane_impedance` 相对差 **0.0** | 256 |
| 注入点短路（1 µF，esr 1e-6，esl 1e-15） | peak **3.15e-4 Ω** 对 plane 87.3555（约 1/28 万） | 256 / 0.30 s |
| ESL 4 nH / 1 nH（网格 279/440/700 MHz） | 峰在 **279 MHz** / **440 MHz**，反谐振峰互换 | 512 |
| mode2 单点扫描（`pdn_plane_impedance`，tol 1e-3） | 16:4.065 %、32:3.947 %、64:3.333 %、128:1.681 %、256:0.1355 %、**512:0.0332 % 接受** | 512 |

`peak_ratio_vs_plane` 与 `at_plane_peak_damping` **必须分开报**：反谐振会让加载峰**高于**
平面峰（1.773），只给一个"阻尼比"会被读反——中心和节点两行的 contrast（1.535 对 0.9998）
就是这条读法的证据。

### 边界（写进每次返回的 `not_computed`）

* 只算**你点名的那几颗**（≤8 颗，`CAPS_MAX`），不选型、不优化、不替你挑容值；
* 封装/过孔/焊盘自身的引线电感**不含**，要自己折进 `esl_h`；`esr_ohm` 不含介质损耗角；
* 3-D（竖向场、相邻平面耦合、腔体外壳）、辐射 Q、开放边缘失谐、分割平面/moat、
  过孔桶壁/反焊盘仍不算；
* `port_size_mm` 缺 ⇒ `ARG_REQUIRED`；`caps[k].at_mm` 缺 ⇒ `ARG_RANGE`（消息点名 `at_mm`）；
* 电容中心落在板外或 `size_mm ≤ 0` ⇒ 拒；`max_order` 不够 ⇒ `NOT_CONVERGED`（带 `order_scan`）；
* 低频锚点条件化：只在电容主导（`|Z_anchor| ≤ 0.1·|Z00|`）时判 0.5 %，否则报
  `low_freq_anchor_applicable: false`——`1/(ωC_pp)` 不是精确低频极限（铺展电感 + `f→0`
  的 `Q = h/δs` 约定瑕疵）。

### 零变化

`dependencies = []` 仍成立；新函数与新工具插在 `calc/power.py` 同一族里，`pdn_plane_modes` /
`pdn_impedance` / `cavity_plane_q` 的答案逐位不变。唯一动到老路径的是上面登记过的**收敛门**，
并用默认带扫描逐位自证了答案不变。`available` 自述 161 → **162**（`TOOLS` 同数）。

### 测试与文档

- `calc/power.py` 新增 `_plane_modal_matrix` / `_csolve` / `pdn_plane_with_caps` 与
  `_CAP_NOT_COMPUTED`（约 600 行）；
- 第 **162** 个工具 `pdn_plane_with_caps`（`required` 8 项，`port_size_mm` 支持 `oneOf` 单值/两值）；
- 新增 `tests/test_plane_caps.py` **23 例**：矩阵同族（1 端口 / 两端口不同焊盘 / 4 端口通用路径 /
  对称）、复解 1e-12 / 换行 / 奇异、**导纳路径独立重算**、零电容逐点、开路同频、注入点短路、
  波腹与节点、ESL 互换（峰频精确等于 279/440 MHz）、加载峰高于平面峰且 `order > 256`、
  `NOT_CONVERGED` 闸门、加载侧不跳门、缺参 4 种、越界 6 种、8 颗允许 / 9 颗 `ARG_RANGE`、
  工具面 `value == peak_z_ohm`；
- `--selftest` 172 → **175/175**（id 1601/1602/1603：零电容恒等、波腹/节点位置选择性、
  1 nH 与 4 nH 反谐振互换）；
- 新增 `docs/53-平面电容反谐振.md`（含 53.3 收敛门双条件的完整实测）；`docs/52` §52.4 与
  `docs/20` §20.4 / §20.8.3 追加修正登记并指向 53.3；`docs/README.md` 加第 53 条；
- 全量回归 **Ran 2324 tests / OK (skipped=5) / 实测 1075.690 s**（0.0.49 是 2301 例 / 1031.964 s）。

## [0.0.49] - 2026-10-31

`not_implemented_yet` 里有一条是全仓库唯一**自己写明的物理空白**：「平面阻抗的曲线——
模态和不算，因为它不收敛」。这一版把它兑现了，而兑现的方式不是"多算几阶"或"换个求和技巧"，
是找出**被漏掉的那个物理量**：端口的尺寸。

**不收敛的是点源，不是物理。** 点源在二维薄片里的电抗对数发散（尾项 `1/(m²+n²)`），真实的
焊盘不会：把特征函数在焊盘上取面积平均（`S = sin(u)/u`，`u = iπs/(2a)`），尾项多衰减一层
`(2a/(πsm))²`，级数收敛所需阶数约 `m ~ 2a/(πs)`。物理解释一句话：**焊盘接不上半波长短于
自己的模**。所以 `port_size_mm` 在这里**必填、无默认**——焊盘越大峰越低越平，默认一个就是
替调用者改答案。

### 模型

```
Z(f) = j·w·μ0·h · Σ_mn  A_mn / (k_mn² − κ²)
A_mn = (c_m c_n)²/(a b) · Π cos·cos · S_x(m)² S_y(n)²
κ²   = k²(1 − j/Q),  k² = w²μ0ε0εr
1/Q  = 1/Qc + 1/Qd,  Qc = h/δs (δs = √(1/(πfμ0σ))),  Qd = 1/tanδ
```

损耗放进**介质**（经典有损腔，一个复波数），Q 在求值频率上取一次 —— 分母只含一个复数量，
内循环只剩复除法；**在谐振点上与"每模用自己的 f_mn"完全等价**。`m=n=0` 是特例（均匀场
`H=0`，只有介质损耗），这一项正好是 `1/(jωC_pp(1−j/Q))`，构成下列锚点。

### 收敛判据不能是逐点相对误差

第一版用"逐点相对变化 < 容差"，`pad=0.5 mm` 在 order 256 仍报 `NOT_CONVERGED`，最差点在
**238 MHz**：首模以下平面是「平板电容 ∥ 扩散电感」，相消点上 |Z| 是两个大电抗之差，逐点
相对误差物理上不可能收敛。**误差要按答案自身的尺度量**，判据改为
`max|ΔZ| / max|Z| ≤ tolerance`（默认 0.01），逐点量作为副列照报。

| 阶数 | maxΔ/峰值 | 逐点最大相对变化 |
|---|---|---|
| 16 | 6.29 % | 105.8 % |
| 32 | 5.97 % | 51.4 % |
| 64 | 5.01 % | 30.5 % |
| 128 | 2.52 % | 13.4 % |
| 256 | **0.203 %** | 1.07 % |

### 实测（50×50 mm、εr 4.4、t 1.6 mm、tanδ 0.02、端口 [12.5,25]、焊盘 0.5 mm）

| 键 | 值 |
|---|---|
| `peak_z_ohm` / `peak_freq_hz` | **92.4503 Ω @ 2.85841 GHz**（(0,2) 模） |
| `dip_z_ohm` / `dip_freq_hz` | **1.75989 Ω @ 1.55812 GHz** |
| `first_resonance_hz` | 1.42920 GHz |
| `first_mode_q` / `_copper` / `_dielectric` | **47.4101** / 915.295 / 50 |
| `plane_capacitance_f` | 6.08725e-11 |
| `truncation_order` | 256（maxΔ/峰值 0.203 %） |
| `capacitance_anchor_error_pct` | **0.14916** |
| `curve_points` / `mode_frequencies_inserted` | 106 / 10（整条曲线约 2.4 s） |

**2 GHz、含铜、同一端口**随阶数（Ω）：焊盘 0.5 mm → 10.74475 / 12.88796 / 13.97429 /
14.06190 / 14.08098（o32/64/128/256/384）；焊盘 1.0 mm → 10.13321 / 11.18562 / 11.26916 /
11.29041 / 11.29209。
**负控制（点端口，同几何）**：5.7106 / 8.2956 / 10.9700 / 13.6975 / 16.4539 / **19.2255**
（o8/16/32/64/128/256），单调上升、无极限——这条对照是会失败的：哪天有人让点端口也收敛了，
它立刻挂，"有限孔径"这个论断就废了。

### 两条独立真值（都不是这套级数）

1. **平板电容锚点**：`f10/1000` 处模态和必须回到 `1/(ωC_pp)`，实测 **0.149 %**，超 1 % 直接
   `INTERNAL_CHECK_FAILED`（运行时校验，不是测试里的一次性断言）。**但它查不出坐标单位错**
   ——`m=n=0` 项不含 `x,y`。本轮真踩过：把毫米当米传，f10 处读出 350.99 Ω 而不是 87.36 Ω，
   是**与闭式单模峰值互校**才发现的。
2. **闭式单模峰值**（`pdn_plane_modes` 的另一条代码路径）：f10 处闭式 91.4691 Ω（Q=50），
   模态和 order 64 得 86.72 Ω，`86.72 × 50/47.41 = 91.5` ✓。f10 处逐项：`(1,0) = 86.7241`
   （纯实部，并联谐振就是纯阻性）、`(0,0) = 1.8290`、`(0,2) = 1.2192`，其余 < 0.4。

一个细节值得写下来：**有损模的 |Z| 峰值不在 f_mn 上**，而在 `f_mn(1 − 1/(2Q²))`——Q=47.41
时低 0.0222 %（318 kHz），所以采样曲线的最大值可能落在 f_mn 下面一格。插入模频仍然必要
（峰很窄），但"峰值频率 == f_mn"这个说法不精确，测试里按这条写。

### 闸门

阶数扫描 8→16→32→64→128→256（上限 `max_order`，允许 8..1024），达不到容差返回
`NOT_CONVERGED` + `order_reached` / `order_scan` / `hint`；电容锚点偏差 >1 % 返回
`INTERNAL_CHECK_FAILED`；带内所有模频并入频率列表（`mode_frequencies_inserted`）。
`conductivity_s_m = null` 表示理想铜——这不是装饰，是复现"只有介质损耗"口径的开关。

### 读这个答案时必须知道的三件事

* **峰比实测偏高偏窄、谷比实测偏深**：这是**平面自己**的曲线，没有电容、没有封装、没有
  过孔。真实板上电容组是主导阻尼，所以这里的 Q 是**上界**。
* **不报反谐振**：平面与电容组之间的反谐振需要把两者放进同一个网络，这里没有。
* 仍然点名不算：电容/封装、die-package 电感、分割平面与 moat、过孔桶壁/反焊盘电感、
  辐射 Q、开放边缘失谐（真实平面比 PMC 值低几个百分点）。

### 零变化

新函数插在与 `pdn_plane_modes` / `pdn_impedance` 同一族的 `calc/power.py`，不碰任何老路径；
`pdn_plane_modes` 的答案逐位不变（只改了它的 `not_computed` 首条与 precision 文案，指向新
工具）。`dependencies = []` 仍成立。`available` 自述从 160 变 **161**（唯一不列的是
`calc_capabilities` 自己）。

### 测试与文档

- `calc/power.py` 新增 `_ap_sinc` / `_plane_q_of` / `_plane_modal_z`（预计算表 + 折叠归一化）/
  `_plane_aperture` / `pdn_plane_impedance`（约 470 行）与 1 个工具 `pdn_plane_impedance`
  （第 **161** 个）；
- 新增 `tests/test_plane_curve.py` **31 例**：教科书级数**独立重写**（不折叠表、`√2` 单写、
  `sin(u)/u` 内联）对拍 1e-9、互易性精确相等、低频=平板电容（0.5 %）、低频与阶数无关、
  f10 峰对闭式（1 %）、铜损按 Q 比例降峰、并联谐振 |φ|<12° 且实部>0、点端口单调且末/首>2.5、
  `NOT_CONVERGED` 闸门与 `order_scan` 字段、锚点 <1 %、曲线 ok / 模频插入 / 峰与 dip 语义 /
  `value == peak` / 调用者网格保留 / 默认带被标记、11 条拒答、3 条工具面；
- `--selftest` 169 → **172/172**（id 114–116：低频=平板电容、f10=闭式峰×`Q_total·tanδ`、
  点端口仍发散而 0.5 mm 焊盘 256→384 只动 0.1 %）；
- `docs/52-平面阻抗曲线.md`；`docs/20` §20.8.3 改写成修订记录（旧 5.6→9.4 那组数复现不出来，
  改用自测序列，结论一致）；
- 全量回归 **Ran 2301 tests / OK (skipped=5) / 实测 1031.964 s**（0.0.48 是 2270 例 / 997.041 s）。

## [0.0.48] - 2026-10-31

`calc_capabilities` 的 `not_computed` 里一直写着那句没兑现的话：「Surge and fusing beyond
one pulse: repetitive pulses with duty-cycle derating」。`fusing_current` 只答一发，而板上
几乎没有一个电流是一发——短路保护动作前的几十微秒、继电器弹跳、电机启动、容性上电。这一版
把那一列车补上：**一阶集中温度的一维 ODE，每段精确解，参数只有铜自己的物性和你给的 h**。

### 模型（没有拟合常数）

```
C' dT/dt = I² ρ'(T) − h·P·(T − Ta)         每单位长度
ρ'(T)    = ρ20 (234 + T)/(234 + 20)         与 Onderdonk 的 234 同一个
C' = ρ_m c_p (w·t)     hP = h·2(w+t)

d(ΔT)/dt = A + k·ΔT
A  = I²ρ'(Ta)/(A·C')   β = 1/(234 + Ta)   k = Aβ − 1/τ   τ = C'/(hP)
ΔT(t) = (ΔT₀ + A/k)e^{kt} − A/k            每段精确解，不是步进
```

ρ 对 T 线性 ⇒ 方程线性 ⇒ 关断段就是 `ΔT₀·e^{−t/τ}`。**模型里没有 dt**，精度不靠步长。

### 失控判据是闭式的，而且不含热容

```
I² ≥ A·h·P/(D·β·ρ'(Ta))          D = t_on/(t_on+t_off)
max_repetitive_current_a = √(A·h·P/(D·β·ρ'(Ta)))     ← 工具的 value
max_duty_cycle_at_current = 1/(1 + k·τ)              （k > 0）
```

这是两个**速率**的比较（加热 vs 散热），不是能量比较，所以 `C'` 在里面根本不出现。

### 发数与稳态也是闭式

`e_on = e^{k t_on}`、`e_off = e^{−t_off/τ}`、`q = e_on·e_off`、`b = (A/k)(e_on−1)e_off`：

```
trough(m) = b(q^m − 1)/(q − 1)      steady_trough = b/(1 − q)   (q < 1)
u   = (ΔT_melt + A/k)/e_on − A/k
arg = 1 + u(q − 1)/b
arg ≤ 0 → shots = None（永远不熔）
否则   → m = ln(arg)/ln q , shots = ceil(m) + 1
```

`shots = None` 时输出 `survives_indefinitely: true` 与 `pulses_to_melt: null`——**"永远"不是
一个计数**，不报一个大整数。

### 实测锚点（1 mm / 1 oz / 1 ms / D=0.1 / h=10 / Ta=25）

| 键 | 值 |
|---|---|
| `max_repetitive_current_a` | **10.313011** |
| `single_pulse_fusing_current_a` | **317.727834** |
| `derating_vs_single_pulse` | **0.032459** |
| `thermal_time_constant_s` / `time_constant_over_period` | **5.800448396** / **580.04484** |
| `adiabatic_limit_reproduced_pct` | **−0.6156** |
| 5 A 时 `steady_peak_c / trough / ripple / mean_rise` | **104.648 / 104.525 / 0.123 / 79.586** |
| 5 A 时 `dc_equivalent_current_a` | **1.581139**（`= 5·√0.1 = 1.5811388`） |
| 5 A 时 `max_duty_cycle_at_current` | **0.425433**（手算 `1/(1+kτ)` = 0.4254327516…） |
| 12 A（超临界）`pulses_to_melt / melt_after_s` | **1191 / 11.91**，`runs_away = true` |
| `h = 0`（绝热）5 A `pulses_to_melt` | **4014**，总导通 4.014 s |
| h/2 与 2h 的稳态峰值 | **254.869 / 59.547** °C，`h_sensitivity` 报 **245 %** |

**「单发」从来不是「列车」的近似**：同一根线单发 318 A，占空比 10 % 的列车只许 10.3 A。

### 三条内建闸门（失败即 `INTERNAL_CHECK_FAILED`）

1. **绝热极限必须复现 Onderdonk**（差 >2 % 报错，实测 −0.6156 %）——两条互不共享常数的
   路线（我们的 `ρ20/ρ_m/c_p` vs 它的 `33`）必须落在同一个熔断时间上；
2. **3 圈逐圈映射 = 几何级数**（相对 1e-9）——挡住级数符号错；
3. **熔化发数逐发回溯**（shots ≤ 2000）——挡住 off-by-one。

第 2 条在临界点附近抓到一个**真缺陷**：`(q³−1)/(q−1)` 在 `q−1 ~ 1e-10` 时两个近乎相等的
数相减、有效数字掉七八位，把一次正确计算判成了失败。改成代数分解 `1 + q + q²`（精确、无
相消）。**闸门本身也要在它要判的边界上被验证**——这是本轮记下的一条方法论。

### 独立真值

`tests/test_pulse_fusing.py::_rk4_train` 是一个从头写的 RK4：直接积原始 ODE、`ρ(T)` 现写、
导通段与关断段两个不同右端、不知道 `q`/`shots`/几何级数。它给出

- 12 A → **1191 发**（闭式 1191）；5 A → 稳态峰值 **104.5246 °C**（闭式 104.525）；
- 0.2 mm / 400 A → **1 发**（闭式 1）。

（写这个 RK4 时自己也错过一次：关断段忘了把 `I²` 去掉，稳态温度错了 10 倍——正是它该抓
的那类错。）

### 零变化

新函数 + 一个新工具，不碰任何老路径：`dependencies = []` 仍成立，`--selftest` 的三条新读数
纯代数（不起场解、不起仿真）。`available` 自述清单顺手修掉四个**在 `TOOLS` 里根本不存在的
名字**（`board_export` / `board_bom` / `board_netlist` / `board_graphics`——BOM 与网表是
`schematic_export` 的格式，图是 `board_export_graphics`），并把 `part_export` 与
`repetitive_pulse_rating` 补进去。现在是 `available 160 / TOOLS 160`，唯一不列的是
`calc_capabilities` 自己。

### 测试与文档

- 新增 `src/pcb_agent_mcp/calc/current.py` 的 repetitive-pulse 段（约 434 行）与 1 个工具
  `repetitive_pulse_rating`（第 **160** 个）；
- 新增 `tests/test_pulse_fusing.py` **37 例**（含独立 RK4、绝热能量积分手推、17 条拒绝路径）；
- `--selftest` 166 → **169/169**（第 865~867 三条读数）；
- `docs/51-重复脉冲降额.md`；
- 全量回归 **Ran 2270 tests / OK (skipped=5) / 实测 997.041 s**（0.0.47 是 2233 例 / 934.014 s）。

### 仍然不算

长度方向温度梯度（整根线一个温度）、板与铜面作为 `h` 之外的独立散热体、熔化潜热、脉冲内
集肤效应、电迁移（Black 方程需要板厂认可的电流密度）、开铜后的电弧/等离子。精度声明
**±40 %**，并写明由两个单向选择主导：`h` 是你的、铜是集中的。`h` 不给默认值（裸板静风
~10 W/(m²·K) 到铜皮锁金属壳的数百，差 20 倍，猜一个就是给错答案）。

## [0.0.47] - 2026-10-30

158 个工具里没有一个串扰。这是 SI 那块最大的空洞，也是 0.0.43 把铜补进 2-D 场之后
**唯一一个能直接用现成场解落地**的问题：耦合微带的两根线本来就是「偶模」和「奇模」两个
本征问题，而 `field2d` 早就会解本征模的电容。

但只有电容是不够的，所以这一版是三段流水线，不是一条闭式公式：

```
2-D 场解 ──→ 四个电容 ──→ 模态代数 ──→ 两个解耦的一维线 ──→ V1 / V2
(c_odd_d / c_even_d / c_odd_a / c_even_a)      (时域精确解)
```

为什么不能套闭式：耦合微带的闭式阻抗误差在 5 % 量级，回答不了强耦合；为什么不能只给
`Kb`：`Kb = 0.25(k_l + k_c)` 只在弱耦合成立（本仓库实测 k=26 % 时偏 2 倍），而且它**
根本不含 FEXT**——`Kb` 是个纯代数数，FEXT 是「两模跑得不一样快」在长线上的累积，
只能从时域解里读出来。

### 第一段：场解给四个电容

同一组横截面解两个边界条件（两线同电位 = 偶模；两线反电位 = 奇模），有介质和无介质
各一次，得到 `c_odd_d / c_even_d / c_odd_a / c_even_a`。

### 第二段：模态代数（全是恒等式）

```
Cg   = c_even·ε0            Cm = (c_odd - c_even)·ε0/2          C11 = Cg + Cm
L_odd  = μ0/c_odd_a         L_even = μ0/c_even_a
L11  = (L_odd + L_even)/2   Lm = (L_even - L_odd)/2
奇模: C = C11 + Cm, L = L11 - Lm        偶模: C = C11 - Cm, L = L11 + Lm
Z = sqrt(L/C)               v = 1/sqrt(LC)               ε_eff = c_d / c_a
```

`ε_eff = c_d/c_a` 是这一版抓到的**第一个真缺陷**：原来写成 `c0/v`，那是 `sqrt(ε_eff)`。
微带上两个数差得不算离谱（2.71 对 1.65），**带状线上一眼看穿**——均匀介质必须正好等于
εr，而它报 2.0736 = √4.3。修正后 stripline 两模都是 **4.3 / 4.3**，微带（mesh 12）是
**2.7124 / 3.37062**。判据写成「均匀介质里 `ε_eff` 必须等于 εr，差一位都不行」，这是
一条**能失败的恒等式**，不是口径。

### 第三段：时域精确解（含一个「跑多久不是口味」的坑）

每模态一根一维线（`rs` 在源端、`rl` 在受端），梯形源、步长取 `samples_per_delay`，
用弹跳级数（bounce series）逐次反射叠加，延迟线性插值。最后
`V1,2 = (V_even ± V_odd)/√2` 合回两根线上，`NEXT = V2(t) 的峰`、`FEXT = V2 在远端时空点
的峰`。

**第二个真缺陷**：反弹级数原来固定跑 8 个往返。匹配时没问题，但 10 Ω / 25 Ω 端接会被
截断在 0.483 V，触发内建的 DC 沉降闸门报 `INTERNAL_CHECK_FAILED`——**把一个正常问题
误杀成内部错误**。改成按更差的模态反射系数算往返数：

```
trips = ceil(ln(1e-10) / ln(max|Γ|))     swell = max(8, 2·trips + 4)
```

10 Ω → swell 140 / 13 374 步 / 远端 **0.500000000000**；1 Ω 也是 0.5；0.1 Ω（要 7000+
个往返）明确报 `OUT_OF_VALIDITY` 而不是硬算。匹配时 `Γ=0`，运行长度与从前**逐位相同**
（n=1870）。

### 近端串扰：`NEXT = Kb · V_incident`，不是乘源电压

这是一个**差 2 倍的因子**。按纪律不当地 bug 修掉，而是造一条**独立真值**去裁决：集总
梯形网络（lumped ladder，K=200/400 段，leapfrog 时域推进），与模态解完全不共享代码。

微带 `w=s=h=0.2, t=0.035, er=4.3, L=50 mm, tr=0.2 ns, amp=1 V, rs=rl=50 Ω`：

| 时刻 (ns) | 集总梯形 K=200 | 集总梯形 K=400 | 模态解 |
|---|---|---|---|
| 0.05 | 0.010639 | 0.010639 | 0.010683 |
| 0.10 | 0.021244 | 0.021244 | 0.021366 |
| 0.15 | 0.031849 | 0.031849 | 0.032049 |
| 0.20 | 0.042454 | 0.042460 | 0.042428 |
| 0.30 | 0.042428 | 0.042428 | 0.042428 |
| 峰值 | 0.0424918 | 0.042460 | **0.0424279** |

差 **0.15 % / 0.08 %**，逐时刻形状也对得上。所以判据是「`NEXT` 对得上独立梯形」，而
`NEXT = Kb·amp/2`（`V_incident = amp·RL/(RS+RL)`，匹配时正好一半）只是这条真值的
**推论**，不是出发点。

### 远端串扰：均匀介质里精确为 0

这不是「小」，是**算术抵消**：两模速度相同时，每一段的 `ΔL·∂V/∂t` 极性配对后正好
抵消。带状线（均匀 εr=4.3）实测：

| 量 | 值 |
|---|---|
| 速度失配 | 3.41168e-05 % |
| **FEXT** | **−1.96505e-07 V = 1.96505e-05 %** |
| NEXT（负控制，必须非零） | 0.0135137 V = **1.35137 %** |

NEXT 不是零，所以「FEXT 是零」不是「什么都没发生」。这条判据能失败：任何把 FEXT
写成 `k_c·…` 的实现都会在这一格报出百分之几。

### 微带 FEXT 的两个区间（分界是相干长度）

```
L_coh = tr / |1/v_odd - 1/v_even|
```

微带 `Δ(1/v) = 6.304e-10 s/m`，`tr = 0.1 / 0.2 / 0.4 ns` → `L_coh = 158.6 / 317.3 /
634.5 mm`。于是：

- `L ≪ L_coh`：严格 **∝ L**（10/25/50/100/200 mm → −0.0078168 / −0.0195421 /
  −0.0390842 / −0.0781683 / −0.1563307）；
- `L ≫ L_coh`：**饱和到 `amp/4`**——400 mm 与 800 mm 都是**同一个数**
  **−0.248002151**，即 `amp/4 = 0.25 V` 的 **99.2 %**（剩下的 0.8 % 是有限上升沿
  还没把最后那点耦合面积走完，不是数值误差）。

输出新增 `fext.scales_with_length` / `fext.coherent_length_mm` / `fext.saturation_limit_v`，
让调用方**自己判断**手里的长度落在哪个区间，而不是事后被一个数误导。

### 三条内建闸门（失败即错误信封）

- **无耦合对照**：`spacing` 极大时受端必须全 0、远端必须 0.5（`leak0 > 1e-9` → `INTERNAL_CHECK_FAILED`）；
- **DC 沉降**：末态必须落在电阻分压（`|dc| > 1e-6·amp` → 失败）——这条抓住的就是上面那个反弹级数截断；
- **步数预算**：先于场解、用 `c0/√er` 估，超 `MAX_STEPS=400000` 直接 `OUT_OF_VALIDITY`（1.1e8 步在 0 秒内返回）。

### 零变化

新模块 + 一个新工具，**不碰任何老路径**：`dependencies = []` 仍成立，`--selftest` 的
864 组三条只跑合成电容、**不起场解**（一次微带场解 30~76 s，自检不能从 116 s 变成 5 分钟）。

### 测试与文档

- 新增 `src/pcb_agent_mcp/calc/crosstalk.py`（约 452 行）与 1 个工具 `crosstalk_coupled_lines`（第 **159** 个）；
- 新增 `tests/test_crosstalk.py` **47 例 / 实测 34.5 s**（含一条**独立集总梯形真值**与 11 条拒绝路径）；
- `--selftest` 163 → **166/166**（864 组：无耦合对照 / 均匀介质 FEXT 算术归零 + NEXT ≠ 0 + `ε_eff = εr` + 速度差 < 1e-14 / 弱耦合 `NEXT/(Kb·V_inc)` 差 < 2e-5，实测 9.96e-7）；
- `docs/50-两线串扰.md`；
- 全量回归 **Ran 2233 tests / OK (skipped=5) / 实测 934.014 s**。

### 仍然不算

第三条线 / 屏蔽线 / 多线耦合、异阻抗受端（模态电路不成立，**直接拒绝**）、色散铜损与
辐射损耗、非准 TEM（含纵向分量的高频段）、介质分层导致的各向异性、连接器与过孔的耦合。
`spacing/h < 0.05` 也拒绝（近场不再满足准静态假设）。这一版是**两导体、准 TEM、两端同
阻抗**的精确解，不是「任意互连的串扰」。

## [0.0.46] - 2026-10-30

0.0.45 把过孔接进了板级 2-D 场，但那句话还挂着：`thermal_via_array` 的 `precision`
写着 **OPTIMISTIC**——墙是**一处**镀层厚度，两端焊盘界面**根本不建模**。这一版把警告
换成两个能填的参数，并且都配能失败的对照。

两个缺口的代数形态不同，做法也不同：

| 缺口 | 形态 | 结果 |
|---|---|---|
| 中段镀层更薄 | 同一根桶壁沿长度**串联** | `R = Σ L_i/(k_cu·A_i)` |
| 两端界面（TIM/焊盘） | 整个场之后**再串**一层 | `R_if = interfaces·t/(k·A)` |

串联是关键词：薄的那一段不论多短都跑不掉，**不能按长度平均**——用等效平均厚度去算，
正好犯下 0.0.45 抱怨过的那个错。

### 桶壁剖面 `barrel_profile`

    A(t) = π·((d/2)² - (d/2-t)²)
    R    = Σ L_i / (k_cu · A_i)        长度之和必须等于 length_mm

一段 1.6 mm、两端 25 µm、中段 15 µm（0.55 / 0.5 / 0.55）：

| 段 | 长 | 镀层 | 环面积 (mm²) | R (K/W) | 占桶壁 |
|---|---|---|---|---|---|
| 0 | 0.55 | 25 µm | 0.0215984 | 66.142314 | 28.885 % |
| 1 | 0.50 | **15 µm** | 0.0134303 | **96.699289** | **42.230 %** |
| 2 | 0.55 | 25 µm | 0.0215984 | 66.142314 | 28.885 % |
| 合计 | 1.6 | — | — | **228.983918** | 100 % |

- 31 % 的长度扛 **42.2 %** 的阻力；
- 同几何均匀 25 µm 是 **192.414004**，剖面让一根过孔的热阻 **+19.0058 %**；
- `plating_equivalent_um` = **20.6827 µm**（等效面积反解，回代复原同一 R 到 5 位）；
- 单段 `1.6 mm @ 25 µm` == 均匀墙，**到第 9 位**（同一条物理的两条路径）。

`plating_um` 因此变成**可选**：给了剖面就不必再给标称值；真给了，它只用来算
`plating_thinning_effect_pct`。不给时报告的 `barrel_area_mm2` 取**最薄那段**，并在
`barrel_area_mm2_source` 里写明。

### 界面层 `interface`

    R_iface = interfaces · thickness / (k · area)

25 mm²、100 µm、k=3 → **1.3333 K/W**；`interfaces = 2` 逐位成倍（2.6667）。它是**整个场
之后**的一层，所以与阵列串联、**不除以根数**：9 根 / 1 mm 间距时占 **5.18 %**，双面
**11.44 %**。故意不带 TIM 表——种类太多、压装条件比材料更影响结果，所以只吃「面积 /
厚度 / 导热率」三个数；voiding、泵出、安装压力写进 `not_computed`。

### 剖面进板级 2-D 场

`board_electrothermal` 的 `thermal_vias` 每数组也收 `barrel_profile`（长度之和 =
`span_mm`），`G = 1/Σ L_i/(k_cu·A_i)`。注意这里 `k_cu` 是 electrothermal 自己的
**400 W/(m·K)**（`thermal.py` 用 385）：

| 墙（20×20、2 mm 格、0.02 W） | G_tot (W/K) | t_max (℃) |
|---|---|---|
| 均匀 25 µm | 0.539961 | 25.037263 |
| 1.0@25 + 0.6@10（R = 280.392 K/W） | 0.356643 | **25.055977** |

差 **+0.0187 K**——数不大但方向对，因为这块板上的等效 h 已被抬到几百，瓶颈不在桶壁。
所以判据写成「`G_one` 等于 `1/R_hand`」，而不是「t_max 变大了」。

### 零变化（三条，逐条可测）

- 不给 `barrel_profile` / `interface` → 老公式，`rth_one_via_k_per_w = 192.414004399`，
  报告里**不出现**任何新键；
- 板级不给 `barrel_profile` → 数组报告里没有 `barrel_profile_modelled` /
  `rth_per_via_k_per_w` / `plating_equivalent_um`，`t_max` 仍 25.037263；
- `not_computed` / `precision` **换句不删句**，只有真给了才换掉「uniform plating」。

### 拒绝（不近似）

剖面长度之和 ≠ `length_mm`（板级 ≠ `span_mm`）→ `ARG_RANGE`（两个数都写出来）；某段
`plating_um ≥ 钻孔半径` → `ARG_RANGE`（实心铜）；段内 `length_mm`/`plating_um ≤ 0` →
`ARG_RANGE`；非列表 / 空列表 / 元素非对象 / 段里缺键 → `ARG_INVALID` 点名第几段；
`plating_um` 与 `barrel_profile` 都不给 → `ARG_INVALID`（「没有墙」）；界面缺
`area_mm2`/`thickness_um`/`k_w_mk` → `ARG_INVALID`（并说明「这里没有 TIM 表」）；
`interfaces` 不是 1/2 → `ARG_RANGE`；非对象 → `ARG_INVALID`。

### 测试与文档

- 新增 `tests/test_thermal_via_profile.py`（31 例：零变化 4、剖面 11、界面 6、板级 5、
  工具面 3）；
- `--selftest` 160 → **163/163**（863 组：剖面 = 手算串联和；单段剖面 = 均匀墙；TIM 层 =
  `t/(k·A)` 串联）；
- `docs/49-桶壁剖面与界面热阻.md`；
- 全量回归 **Ran 2186 tests / OK (skipped=5) / 实测 907.949 s**（约 15 分钟）。

### 仍然不算

镀液/工艺模型（剖面是**你的数**，不做 IPC-4761 孔型表）、两端焊盘与平面的铺开、桶内吸锡、
界面 voiding/泵出/安装压力、`k_cu` 随温度变化、相邻过孔耦合、`rth_cs`（仍是输入）、
散热器选型（只给预算）。这一版之后它仍是**一阶模型**，但已知的乐观项变成了三个数。

## [0.0.45] - 2026-10-30

0.0.44 把铜补进了 2-D 场，但答案里还挂着两句实话：「radiation (matters above ~60 C)」
和「thermal vias」。这一版把两条都做进模型。

它们在代数上是**同一个对象**——每格一个到某个不参与求解的温度的线性电导——所以共用
一个新的 SOR 内循环 `_sor_plate_add`，而 `_sor_plate`（只层压板）和 `_sor_plate_map`
（带铺铜）**一行未动**：两条老路径必须逐位不变，2100+ 例回归与 0.0.43/0.0.44 的逐位
等价断言都压在这上面。

### 辐射：T⁴ 的精确改写 + 外层不动点

    ε·σ·(T⁴ - Ta⁴) = h_rad·(T - Ta),  h_rad = ε·σ·(T + Ta)·(T² + Ta²)  [K]

这是**恒等式**，不是小温差展开。`h_rad` 依赖 T，所以外面加一层不动点：`h_rad` 滞后
上一迭代、整场重解、场移动量 < `tol_k` 就停（上限 80 次外层）。线性化一次只是「把 ε
折进 h」，等于编了一个数。

### 热过孔：环形截面铜管，落在一格

    G = k_cu · π·((d/2)² - (d/2 - plating)²) / span

0.3 mm 钻孔 25 µm 镀层只占钻孔面积的 **30.5556 %**——按钻孔面积算会高估三倍多。
整根过孔的 G 落在**中心所在的那一格**（点载荷），不分摊：摊到四格会让「一格 100 根」
看起来像「四格各 25 根」。`span_mm` 缺省 = 板厚，`sink_c` 缺省 = 环温并写进
`sink_c_source`。**不与层压板竖向导热并联**——竖向已经在 `h` 里，再并一次就是双计。

### 量出来的数字

100×80×1.6、k=0.3、h=10、2 mm 网格、走线 y=40 / x 10..90 / 3 mm / 35 µm / 4 A：

| 条件 | t_max (℃) |
|---|---|
| 裸板 | 36.082953 |
| ε = 0.9（辐射占散热 35.674 %，外层 4 次，残差 −2.0e-5 %） | 33.360543 |
| **ε = 0.0** | **36.082953（逐位相同）** |
| 过孔 287 根压在走线上（y 34..46） | 25.431386（−10.652 K） |
| 同数量挪到 y 0..12 | 36.082764（**−0.000189 K**） |

1. **`h_rad` 有手算对照**：`ε·σ·(T+Ta)(T²+Ta²)` 在 5 位上与报告相同；`T = Ta` 时退化
   成 `4εσTa³` = 5.4103。
2. **过孔场 = 等效 h**：20×20 板上 100 根共 `G_tot = 0.539961 W/K`，把 h 提到
   `10 + G_tot/(2·A·N) = 684.95125` 重跑，`t_max` 25.037265 对 25.037263，差 2e-6 K。
   这条等式就是「过孔真的接进了场」的证明。
3. **等温极限**：50×50、2 W、ε=0.9、q 带 `1+α(T−20)`，闭式根 52.85045568 ℃；
   k=100 / tol=1e-6 实测 52.850398（差 −5.8e-5，spread 8e-6），k=1000 / tol=1e-7
   实测 52.850374。**这是「不动点而不是一次线性化」的检验**。
4. **`sink_c = 0` 合法**：`t_min = 0.365545`、`status = ok`。

### 两处闸门修正（不是放宽）

- 物理性闸门从 `1e-6` 改成 `max(1e-6, 10·tol_k)`：原来的 1e-6 会把「SOR 停在自己的
  残差上」判成 runaway（实测报过 25 C 低于环温 25 C）。真非物理的场差几度，不是 1e-5。
- 闸门基准从环温改成 `min(ta, 所有 sink_c)`：一根通到 0 ℃ 冷板的过孔把格子拖到 0.37 ℃
  是**正确的解**，用环温当基准会误判。

### 三条零变化

- 不给 `emissivity` / `thermal_vias` → `need_add = False`，走 0.0.44 原路径，逐位不变；
- `emissivity = 0.0` → 与「根本没传」逐位相同，且 `radiation.modelled` 仍为 false；
- `not_computed` **换句不删句**：第 1 条换成灰体局限、第 2 条换成单管局限。

### 拒绝（不近似）

`emissivity` 不在 0..1 → `ARG_RANGE`，非数字 → `ARG_INVALID`；`plating_um ≥ 钻孔半径`
→ `ARG_RANGE`（填满就没有环形截面）；`pitch/d/plating/span ≤ 0`、出界、**一根中心都不
在板内** → `ARG_RANGE`；缺键 → `ARG_INVALID` 点名；不是列表 / 元素不是对象 / `sink_c`
不是数 → `ARG_INVALID`。

### 测试与文档

- 新增 `tests/test_electrothermal_radiation_vias.py`（39 例：零变化 6、辐射 11、过孔 20、
  工具面 2）；
- `--selftest` 156 → **160/160**（861 组：辐射降温 + `h_rad` 手算恒等 + ε=0 逐位相同；
  862：100 根过孔场 = 等效 h 且单根 G 是手算环形截面；863：过孔位置判据）；
- `docs/48-板级辐射与热过孔.md`；
- 全量回归 **Ran 2155 tests / OK (skipped=5) / 实测 784.695 s**（上一版 2116 例 / 1013.902 s）。

### 仍然不算

3-D、层内平面之间的内部辐射、随温度变化的发射率、元件热质量与接触热阻、AC 损耗、
`sim_spice` 里的器件自热、直接读 `.kicad_pcb` 的走线与 zone；过孔侧还有中段镀层减薄、
两端焊盘/平面铺开、铜阻随温度变化、相邻过孔相互作用。这一版让「板子往空气里辐射」和
「过孔把热往下带走」第一次能算进来，它不是 3-D 热仿真。

## [0.0.44] - 2026-10-30

一块在发热走线下面铺了地的板子，`board_electrothermal` 以前会读到比实际更高的温度——
因为散热介质只有你给的层压板 k，铜完全不在场。这一版把铜补进去，并且用"同面积换个位置
就失效"把它钉住。

### 模型：面内片电导，不是 3-D

这个求解器解的是 `k*t*lap(T) - 2*h*(T-Tamb) + q(T) = 0`，`k*t` 是面内片电导。所以铜唯一
能起作用的途径是把热量铺开再让它从两个表面散掉，混法就是把铜并到面内片电导上：

    S[i][j] = k_lam*t_board + sum_pours(frac[i][j]*t_cu*N)*k_cu

`frac` 是矩形与该格的**精确面积重叠**（铺铜边落在格子中间就是 0.5，不是 0 或 1——按
"中心是否落在矩形内"判会让答案取决于网格从哪里起算）；面之间取**调和平均**（材料不连续
的标准有限体积处理），S 均匀时它退化成原来的 `S*dy/dx`。

`copper_pours` 的每一项是 `{x1_mm, y1_mm, x2_mm, y2_mm, thickness_um, layers?}`；
`k_copper_w_mk` 默认 400 W/(m*K)（是参数，不是常数）。竖向那条路没有被加厚——它本来就在
`h` 里，这条边界写进每次答案的 `copper_spreading.assumption`。

### 量出来的四件事

100×80×1.6 mm、k=0.3、h=10、2 mm 网格、走线 y=40 / 3 mm / 35 µm / 4 A：

| 铜在哪 | t_max | 相对裸板 |
|---|---|---|
| 没有铜 | 36.082953 ℃ | — |
| 整板满铺 35 µm（k_eff = 9.05） | 27.395644 ℃ | −8.687 K |
| 100×10 条带压在走线上（y 35..45） | 29.357 ℃ | −6.726 K |
| 同一块条带挪到 y 0..10 | 36.080894 ℃ | **−0.002 K** |

1. **整板满铺必须等于把等效 k 直接传进去**：`0.3 + 35e-6/1.6e-3*400 = 9.05`，
   两次求解 `t_max` 在 6 位小数上相同（27.395644 vs 27.395644）。这条同时是一个能
   失败的对照——调和平均若写成算术平均，两者就不再相等。
2. **半格重叠是精确的**：x ∈ [0,1] 时 `k_eff` 最大 `4.675 = 0.3 + 0.5*8.75`，板面积
   平均 `0.3875 = (0.3*1999 + 4.675*40)/2000`，都是手算。
3. **两块同位置铺铜的铜厚相加**：`0.3 + 2*8.75 = 17.8`。
4. **能量仍然平衡**：有铜时残差 −1.4e-4 %（阈值 0.5 %）。

### 三条零变化的保证

- 不给 `copper_pours` → 走原来的 `_sor_plate`，逐位不变（既有 75 例电热/腔体测试原样
  通过）；`_sor_plate_map` 是独立函数，没有在内循环里加 `isinstance`。
- 0 µm 铜 → `t_max/t_min/t_avg/delta` 逐位等于不加铜，且 `copper_spreading.modelled`
  仍为 true、`k_eff` 三个读数都等于 `k_lam`。
- `not_computed` **换句不删句**：有铜时第一条从"只沿你给的层压板 k 扩散"换成"铜被涂抹成
  每格一个面内片电导"，两条都是限制。

### 拒绝（不近似）

矩形跑出板子、`thickness_um < 0`、面积为 0、`k_copper_w_mk <= 0`、`layers = 0` 一律
`ARG_RANGE`；缺键 / 不是列表 / 元素不是对象 → `ARG_INVALID`（点名缺哪个键）。裁掉越界
矩形等于替用户改设计，猜一个 35 µm 等于编数。

### 测试与文档

- 新增 `tests/test_electrothermal_pours.py`（26 例：零变化 4、等效标量 k 6、位置判据 6、
  拒绝 8、工具面 2）；
- `--selftest` 153 → **156/156**（858：整板满铺 = 等效标量 k，`t_max` 差 < 1e-6；
  859：同面积换位置就失效，−6.7 K vs −0.002 K；860：跑出板子拒绝）；
- `docs/47-铜面导热.md`；
- 全量回归 **Ran 2116 tests / OK (skipped=5) / 实测 1013.902 s**（上一版 2090 例 / 1025.960 s）。

### 仍然不算

单元内部的铜形状、铜-层压板界面热阻、热过孔、**没有列进 `copper_pours` 的铜**（不会自己
去读 `.kicad_pcb` 的 zone）、3-D、辐射、元件热质量与接触热阻、AC 损耗。这一版让"平面在
发热点下面"第一次能算进来，它不是 3-D 热仿真。

## [0.0.43] - 2026-10-30

三件互相独立的事，共同点是**都拒绝用「自己看着像」代替证据**：写 snippet 的方法名从应用
自己的表里问、步骤复放不把「跑完没报错」当「复现」、跑不完的 DRC 交出 id 而不是给一个更大的
超时或一个截断的答案。

### 1. `lceda_write_snippet`：从应用 API 表生成写 snippet

`lceda_pcb_write`（0.0.38）一直是可检查的写路径（`probe → code → probe → 比较`，探针没动就
拒绝 `WRITE_HAD_NO_EFFECT`），但它不生成 snippet——因为厂商写方法名一个都没在真机上验证过。
`doc.api` 把这个洞补上：编辑器会把自己有什么报出来，带厂商中文注释，每个类还带 `callPath`
（运行期接收者：`PCB_Document`，不是 `pcb_Document`）。

于是把一件事拆成三份，每份只由能负责的一方回答：

| 要素 | 谁回答 |
|---|---|
| 方法**名字** | 应用（`doc.api`） |
| **参数** | 调用者（`argsv`，原样传入——应用不报签名） |
| **效果** | 探针（`lceda_pcb_write`） |

打分只有三档：**3 = 精确名**（`save` → `save`）、**2 = 名字含操作词**（`moveComponent` 含
`move`）、**1 = 只有厂商注释**（`applyEdit` 注释「移动器件」）。精确必须压过包含，否则
`save` / `saveAs` 会算成「一样好」，把判断题推给用户。最高档多于一个 → `SNIPPET_AMBIGUOUS`
并列出候选；一个都没有 → `SNIPPET_NO_API` 并给出路（自己写 snippet 交给 `lceda_pcb_write`）；
`move` / `create` 缺参数 → `SNIPPET_NO_ARGS`，不发一个空参调用让编辑器在运行期炸。

`evidence` 字段写清这一条是靠哪一档来的（`exact_name` / `name_contains_operation` /
`vendor_comment_only`），`verified_against_app` **恒 false**——生成的名字还没在真机上跑过。

接收者解析顺序：调用者显式 `property` → dump 的 `callPath` → dump 的 `classToProperty`，
**绝不把类名小写化成属性名**（`DMT_Pcb → dmt_Pcb` 没有任何缩写规律）。

非字符串参数记进 `json_encoded_args`（1-based 下标），因为 JSON 传进来的对象已经丢掉了
原本的字面写法。

### 2. `lceda_step_record` / `lceda_step_replay`：步骤脚本的录制与复放

`lceda_pro_steps` 一次跑一串 snippet 并能逐步看值，但那是一次性读数：换一块板、换一个人，
同样 8 步就得凭记忆重写。录制把**已经发生的那一次**留下来（每步的 `value` 变成脚本里的
`expected`）。

复放逐步比较，每步四种结果：`match` / `differs`（两个值都贴出来）/ `no_recorded_value`
（这一步没有记录可比 → **不是通过**）/ `failed`（抛错，复放停在这里）。整体
`status_verdict` 三态：`reproduced` / `differs` / `inconclusive`。

三个「不冒充」的决定：

1. **没有 `value` 的步骤不带 `expected` 键**，而不是 `expected: null`——后者会和「返回了
   null」判等，把缺记录读成巧合通过。
2. **失败就停**并列出 `not_run`：在第 3 步抛错之后继续跑第 4 步，是在一个与脚本假设不符的
   设计上继续动手。
3. **`idempotent` 恒为 `"unknown"`**：幂等性是 snippet 作者的属性，不是本服务能推出来的。
   同理不判「末态全等」——末态一样可能来自一条完全不同的路径。

脚本自带 sha256，`load` 重算并给 `sha256_matches`；对不上不拒（可能是有意改过），但会说明
它不该被当成记录的那一份。默认目录是 `~/.pcb_agent_mcp/step-scripts/`（或 `PCB_AGENT_HOME`），
**绝不写在工程旁边**；不给 `save=true` 也不给 `path` 时一个字节都不落盘。

### 3. 后台作业：大板 DRC 交出 id

88.7 MB 的板子跑一次 DRC 会超过默认超时。原来的收场只有两种，都是错的：把超时调大（把
超时伪装成判决）、或者返回一个截断的答案。

`board_drc(background=true)` 交回一个 `jid`，`board_job(action=list|get|wait|cancel)` 取回。
四条规矩：

- **运行中没有结果**：`get` 返回 `state: running` 且**不带结果**，不近似、不填上一次的判决；
- **失败留存** `error{type, message}`，不是静默变回 `null`；
- **`cancel` 只丢弃结果，不杀子进程**——`_run` 用 `subprocess.run`，没有句柄可杀，写清而不是
  假装停掉了；
- 作业只在内存，未知 id 是 `JOB_NOT_FOUND`；每类并发上限 2（`MAX_RUNNING`），超出 `JOB_BUSY`。

`submit` 返回的是**提交时刻**的快照并带 `state_is_as_of_submission: True`——不这样做，一个
极快的作业会让「提交」这个动作的返回值取决于调度。

同时把 `fidelity/engine.py` 的 DRC 默认超时从 300 s 提到 `DRC_TIMEOUT_S = 900`：300 s 对这
个体量等于不给机会（可被 `timeout_s` 覆盖）。

### 测试

- 新增 `tests/test_lceda_snippet.py`（19 例）、`tests/test_lceda_steps.py`（23 例）、
  `tests/test_jobs.py`（15 例），共 **57 例**；
- `--selftest` 从 149 增至 **153/153**（新增 854~857：snippet 名字与接收者来自应用、表里没有
  就拒绝、脚本 sha256 长 64 且 `idempotent == "unknown"`、`board_job` 未知 id 的拒绝码）；
- 全量回归实测 **Ran 2090 tests / OK (skipped=5)，1025.96 s**（本机，约 17 分钟）。

### 文档

`docs/45-立创写snippet生成.md`、`docs/46-步骤脚本复放与后台作业.md`。

### 未做（保持拒绝，不伪造）

全波电磁、3-D 热、内置板厂工艺表、付费标准（IEC 62368-1 / 61010-1 / UL 60950 / IPC-2221）
的默认值、元件库几何、立创 DRC 判决——仍属「结构上不做」，给的是替代方案的理由，不是近似值。

## [0.0.42] - 2026-10-30

板框不是矩形的时候，`board_vibration` 一直在答"同样大小的矩形板"。这一版把它
说清楚，并给出**第二条路**：直接解真实外轮廓。

### 差多少（先量后写）

100×100 L 形（挖掉 50×50 一角，fill = 0.75），1.6 mm FR-4：

| 边界 | 包围盒 | 真实轮廓 | 比值 |
|---|---|---|---|
| 全简支 | 553.651 Hz | 1734.760 Hz | 3.13× |
| 全固支 | 1009.460 Hz | 3187.210 Hz | 3.16× |

方板 SSSS 的反节点就在 (50, 50)，正是被挖掉的那点，所以这不是小数点差异。

### 走法：一整套只留一条

三条候选全部写到能出数再判：Morley 三角（~150 DOF 仍 −5.1~−22%、n=6 要 19.3 s）、
10-DOF 三次三角 + Guyan（−12%，缩聚把高阶模态先删了）、距离包络基
`ψ = E(x,y)xⁱyʲ`（方板 0.000%，16 边形 **+88%**、64 边形 **+105%**，min/softmin 型连方板
都 +85%）。三条全部否掉，最后落在 **penalty-Ritz**：Green 定理把域积分化成环上
线积分（内部零离散误差、无剖分）+ 质量内积 GMS 白化 + 罚阶梯。

### 六条量出来的事实（都进了模块 docstring）

1. 不白化直接 α = 1e6：固支方板 **+6600%**；白化后 −0.11%；
2. 白化不能用 Cholesky（α > 1e10 报 not PD），也不能用 `M` 的特征分解截断
   （`1/√λ` 放大无刚度方向 → **伪零频模** 0.006 Hz 对真实 1.2e7）；
3. 矩形固支要 degree ≥ 8：p=6 读 **+120.245%**，p=8 +0.023%，p=10 与 p=8 逐位相同
   （误差已由罚项主导，不是度数不足）；
4. 质量阵已含 ρh，白化后特征值就是 ω²；再除一次 mpa 得到**统一 −41.88%**（多算一步的指纹）；
5. **α 不是越大越准**：64 边形 554 → 560(1e5) → 562(1e6) → 585(1e7)，真值 553.7。
   所以报"阶梯最平的一档"，该档的步长就是误差棒（`alpha_spread_pct`）；
6. **每阶要说出自己有多少是罚项**：`penalty_share = α·vᵀCv / vᵀAv`。固支方板
   第 1 阶 0.000402（`physical=True`）、第 2~4 阶 0.901/0.988/0.988（`False`）——
   分界三个数量级，不需要挑阈值。第 2~4 阶的 21~28 kHz 是"degree 8 在固支下只能装一阶"
   的直接后果，必然出现，所以必须带标记。

顺带修掉一个真错：刚体模态数原先一律按 0，导致「单点支撑 + 全自由」直接
`NO_CONVERGENCE`。现在按 `3 − rank{[1,x_k,y_k]}` 算（1 点→2、2 点→1、3 非共线点→0）。

### 实测精度

- 退化到矩形：ssss −0.009%、clamped +0.009%、corners −0.011%、free 第 4 阶 +0.0008%、
  cantilever **−0.42%**（并且 `alpha_converged=False`，自己说了没收敛）；
- 旋转方板 15°/30°/45°：−0.011% / −0.011% / −0.010%；
- 内接 64 边形 vs 圆板：简支 **+1.06%**（spread 0.36%）、固支 **+1.14%**（spread 1.99%）；
  16 边形 +4.34% / +9.95%，8 边形 +26.26% / +19.77%（几何本身也差 10%，报出来不辩）；
- L 形：`alpha_spread_pct` 14.70%（固支 52.40%）、`alpha_converged=False`、
  `boundary_rms_mm` 0.387 mm，并有一条"只作指示"的告警——**不给一个看起来很准的数字**。

### 接线

`board_vibration` 的 bbox 答案**逐位不变**（有测试盯着），`fill < 0.99` 时另给
`plate_on_outline`（`f1_hz` / `ratio_to_bbox` / `modes` / `alpha_spread_pct` /
`alpha_converged` / `boundary_rms_mm` / `poly_degree` / `area_mm2`）与 `hotspots_on_outline`；
矩形板零成本。新工具 `vibration_plate_outline`（任意边数、逐边 `ss`/`cc`/`free`、`free`
板、点支撑）。性能：GMS 携带 `M·v` + `whiten` 改 `(AQ)` → p=8 固支 3.45 s → **1.22 s**。

新版 `tests/test_vibration_polygon.py` **26 例**（含罚项占比的负控制）、selftest **149 条**。

---

## [0.0.41] - 2026-10-30

`limit` 与 `mult` 这两张卡**进了 `.ac`**。上一版它们被一律拒绝，理由是「小信号增益取决于工作点，
而扫频不带工作点」——这句话本身就是错的：`.ac` 分支先跑 `_dc`、把 `x0` 当 `x_prev` 交给
`assemble()`，二极管/BJT/MOS/开关全在那一点上线性化。删掉拒绝，按同一条规则处理：

    limit（带内）: df/dv = gain
    limit（带外）: df/dv = 0
    mult         : df/dv_k = out_gain * in_gain_k * PROD_{j≠k} in_gain_j*(v_j + in_offset_j)

### 先量后写：参考引擎自己怎么算

`ngspice-47` 实测（`.ac dec 1 1k 2k`，只驱动一个输入）：`limit(gain=2, hi=1)` 偏置 0.2 V → **2.0**、
偏置 2 V（夹住）→ **0.0**、偏置 0.5 V（输出**恰好**等于上限）→ **1.0**；`mult()` 3 V/4 V → **4.0**、
`mult(in_gain=[2 3] in_offset=[1 1] out_gain=2 out_offset=7)` 2 V/4 V → **60.0**。

第三条是这一轮唯一需要额外约定的事：**偏置正好落在边界上时，参考引擎报带内斜率的一半**
（gain=2 报 1.0，gain=4 同一个 tie 报 2.0，偏一个 ulp 就变回整斜率或 0）。这是测出来的约定，
不是推导出来的，内置照抄（`_xspice_ac_slopes()` 里一个 `0.5` 分支），理由只有一个：对拍要在能
精确的地方精确，否则每次扫到这条边界都平白差一个增益。

### 内部形态

`assemble()` 的 `A` 分支在 `mode == "ac"` 时从 `x_prev`（就是 `x0`）取输入电压、算偏置点斜率，
照常 stamp `row = v(out)` 与每输入 `-df/dv_i`；`x_prev is None` 仍然 raise（没有偏置就没有可
线性化的地方，编一个增益才是错的）。`is_linear()` 里 `MULT` 仍算非线性——那是给 DC 的电压限幅用的。

线性化之后有两个输出会是彻底的 0，而 0 容易被误读成 deck 写坏了，所以 `_ac` 出口主动告警：
`limit` 的偏置在/越过边界、`mult` 的某个输入在偏置点为 0。拒绝没了，但「为什么是 0」不能静默。

### 判据

- `tests/test_spice_bundled.py` 32 → **37 例**：带内 2.0 / 夹住 0.0（+告警）、tie 1.0、mult 4.0 与 60.0、
  mult 零因子 0.0（+告警）、`summer` 不受影响（5.0，防一起改坏）、五张 deck 两引擎对拍
  `max_abs_diff_v` 全 **0.0**（含那条 tie）。
- selftest 新增 163（limit：2.0 / 0.0 + 告警文案）与 164（mult：60.0），`--selftest` **146/146**。
- `0.0.40` 那条「`.ac` 遇 `mult` 必拒」被上面五条替换——这是唯一一处被撤销的判据，理由写进了
  用例注释：拒绝的前提本来就是错的。

### 文档

`docs/43` 新增 §43.12（实测表 + tie 约定 + 内部形态 + 判据），§43.11 里那句「`.ac` 下拒绝」改标为
当时状态并指向新节；README 的 tools 行把「`A` 逐条点名拒绝」改成「只认四种纯代数 code model」。

## [0.0.40] - 2026-10-30

`A` 卡里**多端口但仍然只是代数表达式**的两种搬进内置求解器：`summer`（加权和）与 `mult`（加权积）。
`0.0.39` 只覆盖单端口的 `gain`/`limit`，这一版把「端口多」这件事从「第二套器件描述语言」里摘出来——
它们没有状态、没有采样时钟，方程仍是纯代数。

### 为什么是这两种

    summer:  out = out_offset + out_gain * SUM_i  in_gain_i * (v_i + in_offset_i)
    mult:    out = out_offset + out_gain * PROD_i in_gain_i * (v_i + in_offset_i)

`integrator`/`ddt`/`s_xfer`（带状态与采样时钟）与 `divider` 仍然按名字拒绝。

### 实测锚点（先量后写）

`summer()` 两端口 3 V+4 V ⇒ **7.0 V**；`in_gain=[2 3]` ⇒ **18.0 V**；
`in_offset=[1 1]` ⇒ **23.0 V**；再 `out_gain=2 out_offset=7` ⇒ **53.0 V**。
`mult()` 两端口 ⇒ **12.0 V**；三端口 ⇒ **60.0 V**；`in_offset=[0 0 2]` 三端口 ⇒ **84.0 V**；
四参数全给 ⇒ **247.0 V**。

### 五条只有量过才知道的语法事实

1. **模型名是 `mult`，不是 `multiplier`**（后者引擎答 `no simulations run!`）。
2. **多端口参数是向量 `in_gain`/`in_offset`，不是标量 `gain`**：`summer(gain=[2 3])` 被引擎
   `ignored` 后**静默回退成 `in_gain=1`**，3 V+4 V 读 7.0 V 而不是 18.0 V。一个「成功」的读数掩盖了
   参数没生效 ⇒ 内置对未知参数**直接拒绝并点名**。
3. **向量长度必须恰好等于端口数**（2 端口给 3 个值 ⇒ 引擎 `Error on line 4`；内置报
   `3 value(s) for 2 input node(s)`），不补齐。
4. **标量给向量参数一律拒**（引擎自己是 `MIF-ERROR - Array parameter expected`）。
5. **单端口的 `summer`/`mult` 被引擎拒**（`A1 1 10 m1` 与 `A1 [1] 10 m1` 都错）⇒ 最少 2 端口。

数组连接写法 `A1 [1 2] 10 m1` 自本版起可解析；`%v(1) %v(2)` 引擎本身也不收，内置继续拒绝。

### 内部形态

仍是「代数表达式驱动的电压源」，右端从一元变多元：`v(out) - f(v_1 … v_n) = 0`，Jacobian 第 k 列
`-df/dv_k`。`summer` 偏导是常数（一步 Newton）；`mult` 用**真偏导**（双线性，一两步收敛）并进
`is_linear()` 的非线性判定。`.ac` 下 `limit`/`mult` 拒绝、`summer`/`gain` 放行——乘积块的小信号增益
依赖工作点。元素字段改成 `in_gain`/`in_offset` 列表 + `out_gain`/`out_offset` 标量（旧的标量
`el["gain"]` 不再存在），节点表把所有 `inputs` 算进外部节点，子电路实例化时跟着改名映射。

### 判据

- `tests/test_spice_bundled.py` 新增 9 例：正 3 例（53 V / 247 V / 三端口 60 V 与 84 V，且
  `kind` 与 `in_gain` 都对），负 6 例（`gain=[]` 写法、向量长度、标量给向量、单端口、`%v()`、
  `.ac` 遇 `mult`），对拍 1 例（两个 deck 的 `max_abs_diff_v` 都是 **0.0**）。
- selftest 160/161/162（summer 53 V、mult 247 V、长度不符 `NETLIST_INVALID`）；id 158 文案同步成
  四种模型。`--selftest` **144/144**（原 141）。
- `.ac` 遇 `mult` 改走 `NETLIST_INVALID` 信封并带 `hint`（原先 `assemble()` 里的 `ValueError` 会
  直接穿过 `simulate()`），raise 保留作 backstop。

### 文档

`docs/43` 新增 §43.11（实测表 + 五条语法事实 + 内部形态 + 判据清单）；`docs/17` §17.11 第三条收窄
（`netlist_parsed:false` 只剩有状态 code model 与 `%v()` 写法，`[1 2 3]` 自本版起可解析）；README
层表 Sim 行、A 段仿真行、B2 行与两处自查块同步；`tools.py` 的 `sim_spice` 自述。

## [0.0.39] - 2026-10-30

`A` 卡（XSPICE 代码模型）里**纯代数**的两种搬进内置求解器：`gain` 与 `limit`。剩下的仍按名字拒绝。

### 为什么是这两种

`gain` 和 `limit` 没有状态、没有采样时钟、只有一个端口，方程就是两行：

    gain :  out = gain * (in + in_offset)
    limit:  out = gain * (in + in_offset), 再夹到 [out_lower_limit, out_upper_limit]

`integrator`/`ddt`/`s_xfer` 带状态与采样时钟，`summer`/`multiplier` 是多端口（`%v()` 与 `[1 2 3]`
数组连接）——那是第二套器件描述语言，不是加一个器件方程，仍然拒绝。

### 四条先量后写的路径事实

1. **`A` 卡是两节点**（`A1 in out model`），输出对地。三节点写法被引擎拒成 `incomplete or empty netlist`。
2. **`in_offset` 在乘之前**：2 V 配 `gain(gain=2 in_offset=1)` 读 **6.0 V**（不是 5.0 V）。
3. **`limit` 必须两个边界都给**：只给一个时引擎自己报 incomplete ⇒ 内置也没有可继承的默认值，
   缺就按名字拒绝。
4. **`limit_range` 拒绝而不近似**：0.9 V 配 `limit_range=0.5` 实测落在 **0.82 V**，硬夹会写 0.9 V。

### 内部形态

一个由代数表达式驱动的电压源：行方程 `v(out) - f(v(in)) = 0`，夹紧段按上一次迭代线性化（带内斜率
`gain`、带外斜率 0），所以停在边界上的解是真解而不是卡住的迭代。`.ac` 遇 `limit` 一律拒绝（小信号
增益取决于工作点落在哪一段）。

### 判据

- `tests/test_spice_bundled.py`：内置解 `gain` 读 2.0 V 且元件报告给 `input_v`/`output_v`/`region`；
  **负控制**——带状态的 code model 与 `limit_range` 仍按名字拒绝；`cross_check` 对这个 deck 从
  「无可比」变成**真的比**（差 0.0）。
- selftest 157/158/159；`--selftest` **141/141**（原 139）。
- `tests/test_spice_engine.py` 的委派用例保留 `%v(1)` 写法：那条 deck 仍被内置拒绝，`netlist_parsed`
  仍为 `false` ——拒绝的理由从「A 一律不认」变成「数组连接写法不认」。

### 文档

`docs/43` 新增 §43.10（四条实测 + 负控制）；`docs/17` §17.11 更正「`netlist_parsed:false`、
`cross_check` 报无可比」这两条诚实项；README 一页速查的 B2 行改成「`A` 卡的**其余** code model」，
A 表与层表同步；`tools.py` 两处自述。

## [0.0.38] - 2026-10-30

**只改文档**：把「做完了什么 / 没做完什么」写成一页速查（README 的 `## 一页速查`）。
这一版**没有动任何代码路径**——`--selftest` 仍 139/139，全量回归仍 1990 例。

### README

- 新增「一页速查：做完了什么 / 还差什么」，四段：
  **A 做完了**（13 行：能力 × 起于哪版 × 凭什么说做完 × 它自己的边界）、
  **B1 结构上不做**（8 条：现在给什么替代 + 为什么不做）、
  **B2 现在拒绝、门开着**（9 条：缺的前置写清楚）、
  **B3 做了但打折**（9 条：边界一句话）。
- 判定口径写死：**有工具 + 有闸门/参照 + 有一条能失败的对照**才算「做完了」；只写了代码没有对照的一律算没做完。
- 头部版本 `0.0.18` → `0.0.38`；`--version` 示例 `0.0.34` → `0.0.38`；层表 Sim 行补
  「`0.0.37` 起 `A` 卡由随包 ngspice 承担」。
- 路线图补 **V4**（仿真物理深度与场内解 `0.0.19`~`0.0.26`）/ **V5**（内置 SPICE 自己长大
  `0.0.27`~`0.0.36`）/ **V6**（多页原理图 `0.0.33`、随包内核 `0.0.37`）/ **V7**（剩下的全在 B 段）。
- **修正一处自述不一致**：README 从 `0.0.24` 起一直写「工具（151 个）」，而 `len(tools.TOOLS)`
  实测是 **153**（后续几版加了工具但没回填这处计数）。以代码为准改成 153，并说明权威清单是 `calc_capabilities`。

### docs

- `docs/README.md` 顶部加一句指路：完成度总账在仓库根 README 的「一页速查」，本索引只谈文档。

## [0.0.37] - 2026-10-30

最小 SPICE 内核**随工具打包**，`A`（XSPICE 代码模型）从此有了真实载体——**不自己实现**。这一版
改的不是求解器，而是一条口径：`0.0.30` 起一直写着「外部二进制只作一次性证据、不是仓库依赖」，
现在多出一个**随包的外部程序**（`vendor/ngspice/`，14 MB）。口径改写是显式的，不是被顺手带过去的。

- **随包什么**：官方 `ngspice-47`（`ngspice-47_64.7z`，13 814 879 B，sha256 `59225971…518f5a`）
  **未改动**的最小可运行子集——`bin/ngspice_con.exe` + 三个硬依赖 DLL（`libomp140.x86_64.dll`、
  `samplerate.dll`、`sndfile.dll`；**缺任一个直接退出码 127、无任何提示**）、`lib/ngspice/*.cm`（7 个）、
  `share/ngspice/scripts/spinit`、`COPYING`。丢弃：GUI `ngspice.exe`（8.6 MB）、`*.osdi`（5.6 MB，MPLv2）、
  examples/docs/scripts 的其余部分。布局必须是 `bin/` + `lib/` + `share/` 同级——上游形状，不是我们的发明。
- **只由 `engine="ngspice"` 使用，`engine="auto"` 刻意不看它**。查找顺序：`PCB_AGENT_NGSPICE` →
  `PATH` → **随包** → 目录扫描；`find_ngspice()` 默认 `allow_bundled=False`，所以仓库长出 14 MB 目录
  不改变任何既有安装上 `auto` 的答案。撞不到时要能看出来：`ENGINE_NOT_FOUND.tried` 里有一行
  `bundled: …/vendor/ngspice/bin/ngspice_con.exe`；`auto` 失败时信封多一个 `bundled_engine` 字段 +
  `hint` 指路（不静默换引擎，只告诉调用方还有这条路）。
- **三条路径事实，全部有负控制**：① `spinit` 按**可执行文件所在目录**找（与 cwd 无关）；② `lib/ngspice/*.cm`
  **真被用到**（把 `analog.cm` 写成垃圾文本 → `MIF-ERROR - unable to find definition of model gain1`）；
  ③ **`SPICE_LIB_DIR` 会顶掉 `spinit` 的查找基准**——对有 `spinit` 的树设它，引擎报
  `Warning: can't find the initialization file spinit.` 并让每张 `A` 卡 `MIF-ERROR`。第 ③ 条是本轮最贵的坑，
  修法是 `_run_ngspice` 只在「有 `lib/ngspice` 且**没有** `spinit`」的树上设这个变量。
- **`A` 卡真的能跑**：`A1 1 10 g1` + `.model g1 gain(gain=2.0)` 读 **2.000 V**；`limit(in_offset=0 gain=1
  out_lower_limit=-1 out_upper_limit=1)` 对 5 V 读 **1.000 V**；`gain(gain=3)` 的 `.tran 10u 100u` 末点
  **3.000 V**。诚实项照旧：`netlist_parsed:false`、`netlist_parse_note` 点名 XSPICE、`cross_check` 报「无可比」。
  试过但不通、未采纳：`integrator`（`MIF-ERROR … model integ1`）、`summer`（`Missing [, an array connection
  was expected`）、`multiplier`（`MIF-ERROR`）。
- **数值风险为零（量出来的，不是推的）**：系统手工解包副本与随包官方副本对 divider/diode/bjt/tran/ac/tline
  六类 deck 逐点对拍，`maxdiff` 全 **0.0 到 1 ulp**（`tran` 2045 点、`ac` 21 点、`tline` 97 点；复测同一张 AC deck 3 点全同、最大差 5.6e-17）。所以「随包优先」
  不改动任何既有期望值。
- **许可证是混搭，不是单一 GPL**：`COPYING` 的默认是 **Modified BSD（3 条款）**，XSPICE code model 是
  **公有领域**（Georgia Tech），`xspice/icm/table`（`table.cm`）是 **GPLv2 or newer**，KLU/numparam 是
  LGPLv2/+，OSDI 是 MPLv2.0（**未随包**）。MIT 与它们共存靠的是**独立程序**：只 `subprocess.run`，
  不 link、不 include、不 import。`vendor/ngspice/NOTICE.md` 逐项列明，测试里有断言盯着这份声明
  （先写的「GPL」版本被 `COPYING` 打回来一次——按事实写，不按印象写）。
- **顺带修掉一个真缺陷：ASCII rawfile 读不了。** 官方 `spinit` 里有 `set filetype=ascii`，所以随包引擎
  写的是 **ASCII** rawfile，而手工解包树（没有 `spinit`）写的是二进制——同一份二进制，换棵树，编码就变了。
  全量回归里 16 个「真引擎对拍」用例因此一起报 `ENGINE_OUTPUT_INVALID`（`ASCII rawfile has 1 records
  but the header says 3 points`），而错在 `_parse_raw` 而不是引擎：① 复杂值写成 `re,im` 是**一个空白
  token、两个数**，旧分支按空白切只数到 `1+nvar`；② ASCII 的 `Values:` 一直到**文件尾**，第二张 plot
  （`.noise`）的表头也被当成数值 token，旧 `bytes_used` 取「整个文件剩余」⇒ `_raw_plots` 永远只走第一张。
  修法：按行扫、`,` 与空白都算分隔符、遇到第一行非数值即停并回报偏移（ASCII 的字符偏移即字节偏移）。
  这条会被 `TestAsciiRawfile` 的三个用例钉住（复杂值、两张 plot、表头点数说谎必须报错）。
- **测试**：`tests/test_spice_bundled.py` **20 例**（查找规则 6 / 许可卫生 3 / 真跑 8 / ASCII rawfile 3，
  无随包时真跑那一组 skip）；`--selftest` 135 → **139/139**（新增：随包只按需出现、`gain` 2 V 且
  `netlist_parsed:false`、`limit` 削顶、负控制 `builtin` 仍 `NETLIST_INVALID`）；全量回归 1990 例全绿（skipped=5）。
- **文档**：新增 [`docs/43-随包SPICE内核.md`](docs/43-随包SPICE内核.md)（九节，含两条误读的回收记录）。

**修订（`0.0.37`）**：本版上一条 `[0.0.36]` 里「但 `A` 在本机 ngspice-47 上连 code model 都加载不了
（`code_model:` 命令不存在）」**是错的**。那条读数出自两个错误前提——模型名写成 `amp`（XSPICE 没有这个
模型，正确的是 `gain(gain=2.0)`）、以及手工解包树缺 `spinit`。同一份二进制补齐 `.cm` + `spinit` 后 `A` 卡
正常。原文保留，回收过程见 `docs/43` §43.7、`docs/17` §17.4/§17.5 与 `docs/42` §42.9 的修订块。

## [0.0.36] - 2026-10-30

JFET `J` 进内置求解器，`NOT_IMPLEMENTED_CARDS` 缩到只剩 `A`。这一版**新增一条器件方程**，和前面几版
（一条 stamp、一条电导、一串 R/L/C）不同：所以真正被推翻的不是实现细节，而是**「JFET 就是 MOS 少了
一个衬底」**这个直觉——同一份 `BETA`、同一条 `Vgs-VTO`，JFET 读出来是 MOSFET 的两倍。

- **`J`（`Jname nd ng ns model [AREA=<x>] [OFF]`）只有三个端子**：`M` 是四端子因为衬底要单独接；
  JFET 的沟道就是衬底、栅是一个反偏结，没有第四个端子。写成四节点会被按下来并提示
  `three nodes (drain, gate, source)`，而不是把第四格当 `AREA=`。`W=`/`L=` 在 `J` 卡上由
  **ngspice 自己拒绝**（`unknown parameter (w)`），消息里直接说明这条限制来自引擎、`AREA` 是唯一几何旋钮。
- **平方律里没有 `1/2`**：饱和 `Id = BETA_eff·Vov²·(1+λVds)`、线性 `BETA_eff·(2·Vov·Vds − Vds²)·(1+λVds)`，
  `Vov = s·Vgs − VTO`（`s=±1` 分 NJF/PJF）。实测 `BETA=1m`、`Vov=1` 读 **1.000 mA**，不是 MOSFET 的
  `0.500 mA`（同一个仓库里两套方程并存，正是最容易被抄错的地方，故测试写成反向断言
  `assertNotAlmostEqual(0.5e-3)`）。两式在 `Vds=Vov` **精确相接**（`Vds=0.999→0.999999 mA` 线性、
  `1.001→1.000 mA` 饱和），所以区域边界不需要平滑项。
- **`AREA` 是卡覆盖模型，不是相乘**：实测 `.model AREA=2` 配卡 `AREA=3` 读 **3×**，不是 6×。
  「相乘」这个更自然的语义给出的答案**同样自洽**、只是整体大一倍，页面上没有任何东西会说多乘了一次
  ——所以它是反向断言。`AREA<=0`/`BETA<=0`/`LAMBDA<0` 拒绝（负 λ 会让饱和电流随 `Vds` 下降）。
- **缺省值必须公示**：空 `NJF()` 用实测的 `VTO=-2 / BETA=1e-4 / LAMBDA=0 / AREA=1`，逐项进
  `assumptions.defaulted_model_params`（`{"jm.vto":-2.0,...}`，只指定一部分时只剩缺省的几个）。
  用缺省 `VTO=-2` 去对 `VTO=-1` 的管子，曲线会平滑、单调、看起来完全合理，只是整体平移一倍。
- **栅结不 stamp，但越界要说**：报告给 `gate_junction_forward_v`，超过 **0.5 V** 时器件加
  `outside_model` 并 append 到信封 `warnings`。**方向是实测的**（NJF `max(vg-vd, vg-vs)`、PJF 镜像：
  栅低于沟道才正偏——`Vg=-4`/源接地实测栅流 3.14 A、栅结钳在 ~0.86 V），不是从手册推的。
- **拒绝 15 个参数按名字，每条都实测「不拒会错多少」**：`RD`（`RD=100`/`Vd=0.2` 电流移 **14%**）、`RS`（**16%**）、
  `B`（`B=2`→**1.5×**、`B=0.5`→**0.75×**，是另一条方程）、`IS`/`PB`/`MJ`/`FC`（没有栅结）、
  `CGS`/`CGD`（AC 没有器件电容）、`KF`/`AF`（没有闪烁噪声）、`TCV`/`BEX`（单温度 27 ℃）、
  `BETA1`/`LAMBDA1`（level-2）。三个按缺省放行：`RD=0`/`RS=0`/`B=1.0`。
- **噪声只有 `channel_thermal = 4kT(2/3)|gm|`**：测试断言机制列表**恰好**是这一条——多出一个机制
  就意味着悄悄补了物理（没有栅结 shot、没有闪烁）。
- **对账**：DC `Vds` 族逐节点 `< 1e-8`（`8.616 V` 那类差 4e-8 = `gmin` 量级）；AC 共源 `4.0∠180°`、
  复数差 `< 1e-6`；瞬态跳过栅压台阶窗后尾段最差 `< 12 V 的 1%`（固定步长 vs 自适应，同为 `0.0.35` 的归因）；
  selftest 的委派探针（id 96）从 `J` 搬到 `A`——**但 `A` 在本机 ngspice-47 上连 code model 都加载
  不了**（`code_model:` 命令不存在），于是这一条不再编造替身，改测「迁移本身」：同一个 JFET deck
  必须被内置解析、读 `0.267949 V`（`2-√3`，跳过这张卡是 `0.500 V`）且与引擎 `max_abs_diff_v = 2e-9`。
  新增 selftest 151（`1 mA` vs `0.5 mA` + 两个 `gm`）、152（`AREA` 覆盖 + 栅结越界）、153（五个参数点名拒绝）。

## [0.0.35] - 2026-10-30

电压/电流控制开关 `S`/`W` 进内置求解器，`NOT_IMPLEMENTED_CARDS` 缩到 `J`/`A`。这一版**没有新算法**，
新增的是"一个状态"：开关不是非线性器件，而是**两个电导里现在装的是哪一个**。于是真正被推翻的是
三条凭直觉会写错的东西——滞回宽度是整宽不是半宽、`VH=0` 时恰好等于 `VT` 就闭合、DC 也走滞回带
——三条都不是从手册读来的，是拿 ngspice 一点一点试出来再改过来的（16/16 一致）。

- **`S`（压控）`Sname n+ n- nc+ nc- model [ON|OFF]`**：四个端子，因为控制量是**电压**、电压是两点
  之差。**`W`（流控）`Wname n+ n- vname model [ON|OFF]`**：第三个字段与 `F`/`H` 同一条口径——是
  **元件名**不是节点，写成 `W1 1 2 3 swl` 会读一个不存在的电流、永远 0 A、永远断开，而 deck 依旧
  `status: ok`。两者都**不占未知量**：开通是 `1/RON`、断开是 `1/ROFF` 的一条电导。
- **状态是状态，不是一条公式**：状态在一次求解里**锁存**，收敛后用解去问"这个状态自洽吗"，
  不一致就翻转并**热启动重解**（DC 与瞬态同一种循环，最多 8 次，超了 `NOT_CONVERGED` 并提示加滞回），
  翻转数写进 `switch_flips`——"有一个开关在动"因此是可观测的。第一版把 DC 写成"每次 `assemble`
  用 `x_prev` 重算状态"，那条路会把 `ON`/`OFF` 种子直接扔掉、给出与 ngspice 不符的答案。
- **`ON`/`OFF` 是起始状态，不是锁**：实测 ngspice-47——`ON` + 0 V 控制的工作点仍是 **open**（0 V 低于
  `VT−VH`）、`OFF` + 5 V 是 **closed**，而 1.2 V 落在 `VT=1 VH=0.5` 的带内时 `ON` 保持 closed、无种子
  则 open（带内两个状态都不矛盾，种子就是全部答案）。这三张 deck 都进了测试与双引擎对拍。
  实现上状态在一次求解里**锁存**、收敛后复核、不一致就翻转重解（DC 与瞬态同一种循环，8 次上限）。
- **阈值判据只能量出来**：① 滞回是 `VT ± VH` 的**整宽**（`VT=1、VH=0.5` 要 `> 1.5` 才开通，不是
  `> 1.25`）；② `VH=0` 时用 `>=`（恰好 `VT` 判**闭合**）；③ **DC 也走滞回带**（工作点里控制 1.0 V
  的 `VT=1、VH=0.5` 开关仍是 `open`），关断侧同样实测（0.4 V 关、0.5 V = `VT−VH` 仍开）。
- **拒绝而不是钳位**：`RON <= 0`（MNA 里没有零阻电导，"ideal short"）、`ROFF <= 0`、
  `ROFF < RON`（"conducts BETTER when open"）、`VH < 0`、未知参数**点名**、`S` 要 `SW` / `W` 要 `CSW`、
  卡的形状不对时把形状印出来、模型缺失。缺省 `VT=0 VH=0 RON=1 ROFF=1e6` 写进 `defaulted`。
- **双稳不给答案**：`S1 3 0 3 0` 这类"闭合就把自己的控制拿掉"的 deck 两个状态都不自洽，DC 报
  `NOT_CONVERGED` 并附 hint（双稳、滞回在 DC 帮不上忙），不给"碰巧停下的那个状态"。
- **三口径对账**：DC 四组控制值（5 / 0 / 1.0 / 1.000001）对 ngspice-47 `max_abs_diff_v = 0.0`；
  `W` 两种栅极电阻逐节点 0.0 且**符号**按引擎口径；AC（状态冻结）开通 `0.4472136∠−26.565°`、
  断开 `Zp/(1M+Zp) = 7.0675e-4∠−44.97°` **逐位 0.0**；瞬态非边沿差 20 µs 步长 **0.299%** →
  2 µs **0.182%**，开关边沿差 20 µs **18.9%** → 2 µs **2.74%**——边沿差**随步长收敛**，是"边沿被
  量化到网格"，不是阈值判据写错。
- **37 例新测试**（`tests/test_spice_switches.py`，含 5 例与真 ngspice 对拍）+ selftest **156/157/158**
  （开关三问：两状态 + 控制端口不取电流；同一斜坡 `VH=0` 开、`VH=0.5` 不开 + 双稳必须 `NOT_CONVERGED`；
  `ON`/`OFF` 只作种子——0 V + `ON` 仍 open、5 V + `OFF` 仍 closed、带内由种子决定），
  selftest 128 → **131**。原"内置拒绝、引擎照跑"的委派探针从 `S` 换成 `J`（JFET）——S 已经能跑了，
  一个会撒谎的探针不能留着。

## [0.0.34] - 2026-10-30

受控源 `F`/`H` 与互感 `K` 进内置求解器，`NOT_IMPLEMENTED_CARDS` 缩到 `J`/`S`/`W`/`A`。
这一版没有新算法，三张卡都是纯线性 stamp——它真正被推翻的是**"按位置读卡面"**：
`F`/`H` 的第三个 token 是元件名，读成节点对不会报错，只会解出一份 0 A 的假答案。

- **`F`（CCCS）`Fname n+ n- vname gain`**：`vname` 是**控制元件的名字**，不是节点对。按 `E`/`G`
  的四节点形式读会把 `F1 2 0 V1 2` 里的 `V1` 当成节点、控制项取 0 V，于是它变成"0 A 电流源"，
  节点读 0 V 而 deck 依旧 `status: ok`。所以解析器强制 5 个 token、控制名里出现 `=` 或**能当数字
  读**直接拒绝、控制元件必须在 `branch_name` 里（`V E H L BV`），否则报 "has no branch current"
  ——`R`/`C`/`D`/`F`/`G`/`I` 都没有可命名的支路未知量。
- **`H`（CCVS）`Hname n+ n- vname gain`**：电压源，所以它**要自己一行**。测试把这条写成可数的断言：
  同一张 deck 用 `F` 时 `unknowns = 3`（2 节点 + `V1` 支路），换成 `H` 必须 `4`——`F` 若偷偷
  分配一行，矩阵仍然可解，只是多了一个没人约束的未知量。
- **`K`（互感）`Kname L1 L2 k` 不是器件**：没有端子（`nodes: ()`）、不占未知量、没有"自己的电流"。
  `M = k*sqrt(L1*L2)`；AC 在两条**已有**的电感支路行之间交叉加 `-jωM`，瞬态交叉加 `-M/h` 并在 RHS
  补 `-(M/h)*i_prev_other`，DC 什么也不加（`d/dt = 0`，两个电感都是导线）。报告是一条
  `type: "K"` 记录（`inductors`/`coupling`/`mutual_h` + 一句"no terminals and no current of its
  own"），**没有 `voltage_v` 字段**，因为它没有端子可量。
- **`|k| > 1` 拒绝而不是钳位**：`M² = k²·L1·L2`，`|k|>1` 意味着互感大于产生它的两个磁通本身，
  钳到 1 等于把不可能的输入变成看起来正常的答案。消息里带范围：
  `coupling coefficient 1.5 is outside [-1, 1] …`。`|k| = 1` 合法，`k = 1`/`-1` 都有 `ok` 断言。
  同批拒绝还有：引用的不是本 deck 元件、引用的不是 `L` 卡、耦合到自己、电感 ≤ 0。
- **子电路里的名字是实例局部的**：`_instantiate` 新增 `body_names` + `scoped_ref()`，对 `F`/`H` 的
  `ctrl_name` 与 `K` 的 `l1`/`l2` 加实例前缀。不做的两种失败形态：体内有、顶层没有时白做；
  体内有、顶层**有同名**时静默耦合错对象（解出来了、数是错的）。三条能失败的对照：体内控制源读
  `2.000 V`、顶层放 1 mH 的诱饵 `L1` 而体内是 100 uH（比例必须仍是 2.000，全局解析会变成 0.632）、
  同一变压器子电路实例化两次（第二个的次级必须低于 1e-9）。
- **对账用三种口径，且把差距归因而不是修掉**：`F`/`H` 的 `.op` 与 `.ac` 对 ngspice-47 都是
  `max_abs_diff_v = 0.0`；`K` 的 `.ac`（k = 1 / 0.5 / −0.5 / 带载）逐点 **0.0**；瞬态结构判据
  k=1 等绕组 `max |v2-v1| = 0.0`、k=1 开路次级 `v2/v1 = 1.9999999864 ~ 2.0000000098`
  （= `sqrt(400u/100u)`）。瞬态对 ngspice（20 kHz 正弦、按时间插值）最差 **2.862e-03**（幅度 0.2342，
  **1.222%**）——归因于**后向欧拉 vs 梯形**，同一份 deck 不带 `K` 也差同量级；交叉项写错的话上面那五条
  精确读数至少崩一条。
- **两条"物理正确但不直观"的读数**写进文档免得下次当 bug：k=1 的两个电感并联在同一对节点上，DC 是
  奇异矩阵（`M²=L1·L2` 让 `[L]` 奇异），零阻抗是正确物理；k=1、两个 1 mH、次级 1 kΩ 的 200 µs 稳态
  是 `v ≈ 0、i1 ≈ 20 mA`（理想变压器把次级短路等效到初级）。
- 新增 `tests/test_spice_fhk.py` **45 例**（F 的读数与方向 6 / H 的行与报告 4 / 受控源拒绝 6 /
  K 的 AC 变比与相位 8 / K 的瞬态与 DC 4 / K 的拒绝 7 / 子电路前缀 4 / 真引擎对拍 6——最后一组只在
  装了 ngspice 时跑）。新增 selftest **153/154**（`F1` 读 −1 mA 得 `V(2)=2.000` 而 `H1` 得
  `V(3)=-0.002`；k=1 且 `L2/L1=4` 的开路次级读 2.000×，而 `k=1.5` 必须 `NETLIST_INVALID` 且消息含
  `outside [-1, 1]`）。`tests/test_spice.py` 的"未实现卡按名字拒绝"删掉 `F`/`K`/`H` 三条（保留
  `S`/`X`/`T`），注释补上 0.0.34 的沿革——一条会变假的拒绝断言比没有断言更坏。
- `NOT_COMPUTED` 改口：不再说"F/H/K 没实现"，改成列**它们的二阶物理**没做（`K` 无磁芯损耗、
  无频变电感、无饱和；`F`/`H` 的增益是常数不是表）。`README.md` 与 `sim_spice` 的工具描述同步改口；
  顺带修掉 `README` 里一行过期的"`X/F/H/K/S/W/B/T/O/U/A` 逐条点名拒绝"（`X`/`B`/`T`/`O`/`U` 早就做了）。
- 验证：`--selftest` **128/128**；全量回归 **1877 例 / OK (skipped=5) / 935.249 s**
  （0.0.33 是 1832 例 / 760.223 s，新增 45 例）。`docs/40-受控源与互感.md` 新写；
  `docs/17` §17.4/§17.5 与 `docs/README.md` 同步。

## [0.0.33] - 2026-10-30

多页（层级）原理图接上了：`.kicad_sch` 能改 `sheet` 节点（增页 / 加 pin / 改字段 / 删页），
`apply` 多一道 `tree` 闸门。这一版真正被推翻的是**上一版闸门问错了树**——不是"少做了一层"。

- **假绿的根因：单文件闸门问的是另一个问题。** 0.0.32 的 `apply` 把**待测文件**复制进临时目录再让
  kicad-cli 跑网表与 ERC。对单页图这是精确正确的；对层级工程，副本里没有子页，KiCad 要么报错、
  要么当"这张图没有子页"答下去，两种结果都被当判据用了。实测（`complex_hierarchy` demo +
  `ampli_ht.kicad_sch`，kicad-cli **10.0.7**）：问整棵树 **52 nets / 73 ERC**，问单文件副本只剩
  那张图自己的 nets、子页违规一条看不见。而**改前与改后都错同一处，差值看起来是干净的**。
- **修法不是再加一道闸门，而是让两边问同一组文件。** 新增 `fidelity/hierarchy.py` 的
  `materialise()`：把树里每个可达 `.kicad_sch` 按同一相对布局复制进临时目录，只替换被编辑的那一个，
  两个引擎调用都在副本里跑，`finally` 整个删掉。同一文件被两个 sheet 引用时**只复制一次**并在
  `skipped` 说明（复制两遍等于改写工程布局）；复制不了的列进 `skipped`/`outside`，同时 `tree`
  闸门把"引擎看到的是一棵残缺树"说出口。demo 实测 `before files 2 / after files 3`。
- **`apply` 现在报 7 个 gate 字段**：`bytes_written` / `token_identity` / `postconditions` / `tree` /
  `engine` / `netlist` / `erc`。加一个页不改变连通性：`nets_added 0`、ERC 新增 0（52/73 加页前后不变）。
- **页号是工程全局的，而且一个节点可以有 N 条。** `vme-wren` demo 73 页 / 17 个 sheet 节点、页码
  1..73 全局不重复；`io_driver_8x.kicad_sch` 一个文件 4 个实例 = 4 个页号，所以 `(instances (project
  "P" (path "/<父实例路径>" (page "N"))))` 里要写 **N 条**。只写一条的失败形态是"每个实例同号"——
  看着成功了。`_next_page` 取 `max(本页, 工程 max_page, sheet_instances) + 1`，`_used_pages` 合并
  磁盘工程页与本页内存页（同一次 plan 里第二个 `add_sheet` 才看得见第一个）。
- **4 个 sheet op**（op 表 18 → **22**）：`add_sheet` / `add_sheet_pin` / `set_sheet_field` /
  `delete_sheet`。多实例 + 显式 `page` 直接 `ARG_INVALID`（不替调用方决定哪个实例是"第一条"）；
  页号冲突判 `PAGE_EXISTS` 且按**工程全局**判；`set_sheet_field` 只许 `Sheetname`/`Sheetfile`，
  同值 `OP_NOOP`；`Sheetfile` 只收裸文件名（`../x` 让本服务无法核验落点）；`delete_sheet` 返回
  `file_kept` / `other_sheets_using_it` / `orphaned`，**从不删文件**。
- **sheet pin 是承诺，ERC 才是判据。** 子图没有对应 `hierarchical_label` 时，加 pin 会让 ERC 新增
  `hier_label_mismatch` + `pin_not_connected` → 拦截回滚；先在子图放 label 则 `hier_label_mismatch`
  消失（正控制），证明拦截来自 label 本身而不是"我们不想让你加 pin"。
- **两个新工具**：`schematic_hierarchy`（只读：每页文件 / 实例路径 / 页号 / 深度 / 缺失·循环·重复页，
  页号报的是工程全局）、`schematic_new_file`（建空白 `.kicad_sch`，写完立刻 `from_text` 自检
  `bytes_identical` + `token_identity`；200 字节空页）。**建新页不塞进 plan**：多文件原子写与回滚
  没有诚实答案，拆成「先建页 → 再 `add_sheet`」两步比假原子更诚实。
- **`blocking` 在 propose 阶段就落进 plan**：`FAILING_ISSUES = ("missing_file", "cycle", "too_deep",
  "unreadable", "duplicate_page")`，其余只是 warning。`engine=False` 时层级闸门仍必须拦住——否则
  关掉引擎就等于关掉整棵树。
- **仍然只报*新增*的 issue**：工程本来就有问题不构成拒绝理由。
- **七条能失败的对照**（每条都对应一次真实的假绿，都在 `tests/test_sch_sheets.py`）：`missing_file`、
  `cycle`、`duplicate_page`、pin 无 label、多实例只写一条页号、`walk` 的 `text` 只对入口生效、
  `blocking` 在 `engine:false` 时被忽略。
- 新增 `tests/test_sch_sheets.py` **47 例**（读侧 10 / 根识别 4 / `materialise` 3 / sheet op 14 /
  多实例分页 5 / 层级闸门 4 / `new_file` 5 / 真引擎 3——后 3 例只在装了 kicad-cli + demo 时跑，
  否则 skip）。新增 selftest **151/152**（读两文件工程得 sheets 1 / file_count 2 / pages `["2"]`；
  指向不存在的子图必须 `blocking` 非空且 `apply` 回 `status == "error"` 且根文件字节未变）。
- 顺带把 `walk` 的 override 文本收窄到"只在该根同时是被编辑文件时"——否则根 uuid 变成被编辑
  文件的 uuid，实例路径全错（proposed 得 4/4、磁盘得 4/5）。这是同一族的第三次假绿。
- 验证：`--selftest` **126/126**；全量回归 **1832 例 / OK (skipped=5) / 760.223 s**
  （0.0.32 是 1785 例 / 640.589 s，新增 47 例）。`docs/39-多页工程.md` 新写；`docs/14` §0/§4/§8
  与 `docs/README.md` 同步。

## [0.0.32] - 2026-10-30

`.noise` 进内置了，用的是**伴随法**——每个频点解一次 `Aᵀy = e_out`，全部噪声源的传函一次拿到，
而不是"一个源解一遍"。四条机制按物理式写死，对账既打解析闭式也打 ngspice-47。

- **伴随法而不是逐源求解**：`h_k = y[a]-y[b]` 就是第 `k` 个源到输出的传函，`|gain|` 用单位激励
  再乘一次源幅度。性质是「与 `.ac` 不可能分叉」（同一矩阵反着读），实测分压器 `gain = 0.5` 与
  `.ac` 的 `|V(2)|` 在 61 个频点上逐个 1e-9 一致；`inoise = onoise/|gain|`。
- **四条机制**：`R` 热噪声 `4kT/R`、`D` 散粒 `2q|Id|`、`Q` 集电极与基极 `2q|Ic|` 与 `2q|Ib|`、
  `M` 沟道热噪声 `4kT·(2/3)·gm`（`gm` 走数值雅可比，不由 `KP·W/L` 猜）。`O`/`U` 展开出来的
  阶梯元件归回卡名（`oline#r3` → `O1`），不伪造分布式噪声。`contributions` 把每个源在输出端的
  贡献单列，`sources` 只列真在电路里的源——理想电压源内阻 0 时密度为 0 但源仍然在列。
- **解析对账（逐位）**：单只 1 kΩ（电流源驱动）`4.071372237e-09 = sqrt(4kT·1000)`；1k‖1k
  `2.878894918e-09 = sqrt(4kT·500)`；四只串联恰为单只 ×2；RC 低通逐点相对差 **3e-9**，带内积分
  `6.424132021e-08` 对 `arctan` 闭式 `6.42419041041798e-08` 差 **−0.000909 %**；`sqrt(kT/C) =
  6.437404735994157e-08`（扫到 1 MHz 低 0.2 %，方向正确）。二极管 `IS=1e-14` 的 `D1/R1` 噪声比
  `0.642238` 复现 `q·Id·R/(2kT)` 且与频率无关。
- **真引擎对拍**（ngspice-47）：分压器与 RC 逐点最大相对差 `1.73945e-07`、MOS `1.7e-07`、
  二极管 0.20 %、BJT 0.29 %。输入源必须带 `AC` 值 ngspice 才肯跑（否则 `Noise input source v1
  has no AC value` → exit 1），内置**不**要求——这条方言差异写进 `docs/38` §38.2。
- **积分规则跟着网格走，而且说出来**：`dec`/`oct` 用 `ln f` 梯形、`lin` 用 `f` 梯形。实测一种规则
  不可能同时适合两种网格——`dec 10` 误差 −0.00091 %、`dec 100` −0.00001 %，而 `lin 1000` 用 `f`
  梯形仍 **+47.1 %**（`lin 200` 更是 +217.6 %），因为「带宽就是扫描段」，1 Hz–1 kHz 那段没被采到
  就是没被采到。所以答案的 `method`/`note` 直接把规则与网格一起写出来，不假装与网格无关。
- **不比较 `inoise_total`**：内置是逐点 `onoise/|gain|` 再积分，ngspice 的输出积分除以单点增益，
  RC 上两者差 12 %——是口径差不是误差，所以只比输出谱，`engine_note` 里写明。
- **委派也能做噪声**：`_deck_for_ngspice` 放行 `.noise`，`-r raw` 天然两个 plot，`_raw_plots` 取
  `all_plots[1:]` 的 `v(onoise_total)`（实测 41 点）。`engine="ngspice"` 不再给错位错误。
- **拒绝清单**（11 条，全部带原文与用例，`docs/38` §38.9）：输出点名没人连接的节点、输入源不在
  网表、卡缺字段（`got 6 tokens`）、输出写成 `I(1)`、输出写成 `V(0)`、输入源是 `E` 卡、
  `fstart >= fstop` 或 `<= 0`、扫描词不是 `dec/oct/lin`、偏置点不收敛、矩阵奇异（那里的密度是
  无穷，给「大数」就是假答案）、`.print`（内置不做，同份 deck 走委派可跑）。**不拒绝但要出声**的
  三处：增益为 0（`inoise = None` + 告警）、单点扫描（积分为 0 + 告警）、超 `max_points`
  （截断 + 告警）。
- **顺手修掉一个真缺陷**：MOSFET 的 `W=`/`L=`/`M=` 之前被**静默丢弃**（器件照样跑，`gm` 不对）。
  现在进 `el["wl"] = (W/L)*M`，`W=20u` 的噪声功率严格 ×2。旧用例不带 `W/L` ⇒ 基线零漂移。
- 新增 `tests/test_spice_noise.py` **42 例**（热噪声 9 / RC 积分 6 / 输入折算 5 / 散粒 4 / 拒绝
  10 / 真引擎对拍 5 / 委派 3），迁移两处旧前提：`test_spice.py` 的「坏分析名」从 `noise`
  换成 `disto`，并新增「合法但 deck 没要的分析 → `ARG_REQUIRED`」。新增 selftest 149/150
  （RC 1 Hz `4.071291874e-09` + 带内积分对 `arctan` 1e-4 + `< sqrt(kT/C)`；坏节点
  `NETLIST_INVALID` 且无噪声元件时读 0、`sources == []`）。`docs/38` 新写口径，
  `docs/17` §17.4/§17.9 同步。
- 验证：`--selftest` **124/124**；全量回归 **1785 例 / OK (skipped=5) / 640.589 s**
  （0.0.31 是 1742 例 / 754.856 s；新增 `test_spice_noise.py` 42 例 + `test_spice.py` 1 例，
  34 处旧用例无一因 `noise` 变成合法分析名而改判——它们问的是别的名字）。

## [0.0.31] - 2026-10-30

有损线 `O`（`LTRA` 模型）与分布 RC 线 `U`（`URC` 模型）接上了，但接的方式是把**线换成一串
R/L/C**：解析期按模型的 `r l g c len` 切 `lump` 段，求解器见到的是普通梯形，方程层一行没改。
所以每一步都写清楚「这是模型不是分布解」——段数随答案走、默认值被公示、收敛性本身是被测对象。

- **DC 是精确的，而且与段数无关**：没有 `g` 时梯形就是一段电阻链。1 Ω 在 3 V / 50 Ω 分压里读
  `1.514851485 / 1.485148515`（= `3·51/101`、`3·50/101`），`lump=1/2/8/20/50` 逐位相同（末位
  1e-9 级浮点漂移），与 ngspice 的 `LTRA` 一致（`cross_check < 1e-6`，`compared_nodes = 3`）；
  端口 1 电流 `3/101 A`。段数无关正是「每段分到的值有没有除对」的负控制：按 `r·len` 给每段，
  读数会随段数线性跑掉。`U` 同理（`r=2000 len=0.5` 串联总阻 1 kΩ 与负载分压）。
- **有 `g` 时收敛到分布解，一阶**：`r=1 l=250n g=1 c=100p len=1`、50 Ω 负载，分布解
  `1/(cosh(gL) + Z0c/RL·sinh(gL)) = 0.6383313`；`lump=1/2/5/10/20/50/100` 的误差
  −1.433e-1 / −9.337e-2 / −4.350e-2 / −2.284e-2 / −1.169e-2 / −4.744e-3 / −2.383e-3，单调下降。
  测试断言的是「单调 + 60 段 < 5e-3」，不是「等于解析解」——后者永远为假。
- **AC 有两路外部参照**：无耗梯形（`r=0 l=250n g=0 c=100p len=1 lump=100`）与 `T1 Z0=50 TD=5n`
  在 100k/550k/1M 三档 `|V(2)|=|V(3)|=0.5`、相位 −0.18/−0.99/−1.8° 完全一致（幅值 1e-4、相位差
  < 0.02°）；有耗（`r=1`）1 MHz 读 `|V(3)| = 0.495049 = 0.5·e^-0.01`（`α = r/(2Z0) = 0.01`），
  与 ngspice 的 `max_abs_diff_v = 1.57835e-07`。`U` 因为 ngspice 的 URC 参数在 `rperl/cperl` 下
  给出「能跑但值不直观」的数（与 `0.0.30` 对 `F` 卡的判断同类），改用解析
  `Z_in = Z0c·coth(γL)`：1 MHz 解析 0.909627，`lump=1/2/5/20/60` → 0.953477/0.929348/0.915335/
  0.910689/0.909951，60 段误差 1.1e-3（相位比幅值慢收敛是 RC 集总化的固有形态）。
- **瞬态：波前在 `TD` 到达并随段数变陡**。`PULSE(0 3 0 0.05n)`、`tstep=0.1n`、`tstop=20n`，
  半幅时刻 `lump=5/10/20/40` → 5.3/5.2/5.1/5.1 ns（`TD=5 ns`），末值 1.48515（= DC 分压）。
  代价是 O(n³)：0.14/0.44/1.96/9.94 s（200 步），`lump=200`（603 未知量）报 `TOO_LARGE`，
  提示里加了「lower lump= on a lossy line」。
- **`lump=` 只许写在 `.model` 里**，这不是审美：实测写在卡上是 **FATAL**
  （`Error on line 4 or its substitute`，退出码 1），写在 `.model` 只是 warning
  （`unrecognized parameter - ignored`）而 deck 照常解。同一份 deck 要既能内置解又能原样委派，
  段数只能进 `.model`；卡上出现任何尾部参数一律拒绝并说明原因。
- **段数是模型参数，必须公示**：`elements[]` 里每张 O/U 卡一条记录，带 `segments`、
  `segments_from`（`default 20 (lump= not given)`）、`length_m`、`per_length`、`totals`、
  两端口电压、端口 1 电流与 `current_convention`；默认值进 `assumptions.defaulted_model_params`
  （`{"O1.lump": 20}`）。端口电流只报 port1——集总线上没有单一「沿线电流」，报 port2 就是编一个数。
- **梯形的内部节点算但不上报**：20 段会造出 39 个 `o1#n3` 之类的节点，`nodes_v` 只给调用方的
  节点（`{"1","2","3"}`）。**抓到一个真缺陷**：`U1 2 3 0 m` 的远端节点只被内部元件引用，于是被
  当成内部节点藏起来、读 `nodes_v["3"]` 直接 `KeyError`——「远端什么都没接」是合法电路（开路端），
  修法是把所有 O/U 的端口节点从内部集合里 `discard` 掉。
- **顺手抓到的第二个真缺陷：`.ac` 里没有 `AC` 关键字的源不该被当成 1 V 激励**。写对拍用例时用了
  `V1 1 0 3`（只有直流值），内置给了满幅答案、ngspice 全是 0——实测 `v(1)=1.0/0.5` 对
  `0.0/0.0`。SPICE 的规则是 `AC` 关键字才是激励，旧实现 `or (1.0, 0.0)` 的默认值等于「电路没被
  驱动却满幅有答案」。改成 `(0.0, 0.0)`，并在整份 deck 没有任何 V/I 源带 `AC` 说明时于 `warnings`
  里写「没有人驱动这次扫描，每个节点都会读到 0」。既有测试全部显式写了 `ac 1`，无基线漂移。
- **拒绝清单**：模型缺失/未定义/类型错、`LTRA` 给 `Z0=`/`TD=`（指向 `T` 卡）、`URC` 给
  `ISPERL`/`RSPERL`/`FMAX`、缺 `LEN` 或 `LEN<=0`、四参数全 0（`chain of nothing`）、未知模型参数
  （按名）、per-length 负值、`LUMP` 不是 ≥1 的整数、`lump=` 在卡上、两个不同返回节点
  （梯形把返回当理想单节点，那意味着返回导体自己有延迟）、端口短接、自己是自己的负载、
  `U` 缺 `R`/缺 `C`、`R` 与 `RPERL` 并存。
- **诚实清单同步**：`O`/`U` 从 `NOT_IMPLEMENTED_CARDS` 移除，剩 `F H J K S W A`；`NOT_COMPUTED`
  那条改写为「集总梯形，段数决定色散，只随段数收敛；皮肤效应/介质损耗/频变 `r,l` 不在里面
  （`T` 是精确解），且 ngspice-47 自己拒绝非零 `G` 的 `O` 卡，所以 `g>0` 无法对拍」。
- 新增 `tests/test_spice_oline.py` **34 例**（DC 精确串联电阻 / 段数收敛与不敏感 / 与 `T` 及解析解
  对照 / 瞬态波前 / 17 条拒绝 / 与真引擎对拍），并迁移三处旧用例：`test_spice.py` 的未实现卡清单
  去掉 `O` 改列 `H`、`test_spice_engine.py` 的「未解析卡直通」从 `O` 换成 `S` 卡、
  `test_spice_tline.py` 的「`O` 仍按名拒绝」改成「`O` 缺模型仍按名拒绝」。新增 selftest 147/148
  （DC 1.514851/1.485149 + 默认 20 段公示 + 内部节点不外报；1 MHz 读 0.909951 对分布解 0.909627），
  并把 id 96 的委派探针**第三次搬家** `T` → `O` → `S`（1 V 对 `VT=0.5` 闭合读 0.500 V）；
  一条永不失败的自检不是自检。`docs/37` 新写口径，`docs/17` §17.8、`docs/36` §36.1/§36.8 同步。
- 验证：`--selftest` **122/122**；全量回归 **1742 例 / OK (skipped=5) / 754.856 s**
  （0.0.30 是 1708 例 / 702.669 s；新增 34 例，另有 4 处旧用例因 `O` 不再是「未实现的卡」与 `.ac` 激励口径变了而改判）。

## [0.0.30] - 2026-10-30

无损传输线 `T` 归内置管了。一条延迟在三个分析里各有一套**解析**式子：DC 是导线、AC 是移相器、
瞬态是延迟加反射。理想模型没有有限答案的地方（半波点）就拒绝，不返回矩阵算出来的大数。

- **DC 是一个未知量，不是两个**：`v(n1)-v(n2)-v(n3)+v(n4) = 0` 一行，`i2 = -i1` 由构造给出。
  按「每个端口一个电流未知量」写的第一版在 `T1 2 0 3 0`（两端口共用地，单端线最常见的画法）
  上第二行退化成 `0 = 0`，矩阵奇异——这个坑只在这种画法上现形。实测与 ngspice 逐位一致：
  50/50 分压读 1.500 V、卡报 `t1#i1 = 0.03` / `t1#i2 = -0.03`。
- **AC 用精确 Y 参数**（`Y11 = Y22 = -jY0·cot θ`、`Y12 = Y21 = +jY0·csc θ`，`θ = 2πf·TD`），
  判据是定义本身：终端接 `Z0` 时输入必须**恰好**呈现 `Z0`（`|V(3)| = 0.5` 与频率无关）、
  相位必须恰好是 `-ωTD`（50 MHz 一档 `-18°` 直至 250 MHz `-90°`）、近端相位 0。
  `θ = kπ` 处导纳无穷：**返回 `SINGULAR` 而不是巨大的数**——巨大数看起来像一个答案，而它正是
  本模块存在所要消灭的东西；`|sin θ| < 1e-3` 给答案但顶层 `warnings` 记一条（每条线只记一次）。
- **瞬态是特征线法（Bergeron）**：每个端口一个 `1/Z0` 电导 + 一个「一个延迟之前那一端」的
  电流源，`t=0` 用直流点播种。对理想线这不是离散化，误差只来自 `t-TD` 落在两步之间的线性插值。
  匹配阶跃（近端恒 1.5 V、远端 `TD` 前 0 / 后 1.5）、开路（远端 `TD` 翻倍到 3.0 V、近端
  `2·TD` 翻倍）与短路（反射系数 −1）都实测。报告里的端口电流按同一个 companion 关系重算，
  不读被钉零的支路行——不然「报告的电流」与「解出的电压」会各说一套。
- **两条拒绝**：`TD` 缺失或为 0（ngspice 对缺失的 TD **静默默认 0**，实测线直接变导线）、
  `TD < tstep`（远端要读一个还没解出来的时刻；第一版试过钳到一步，实测波形整体晚约 2 步、
  与告警文案不符，属于「合理外观的错误答案」，改判 `ARG_RANGE` 并给出 `tstep = TD/10`）。
  `Z0` 必须给（ngspice-47 自己也报 `transmission line z0 must be given`；默认 50 Ω 会静默改掉
  整份 deck 的每个反射系数），`IC=` / `REL=` / `ABS=` 按名拒绝。
- **`F=`/`NL=` 与 `TD=` 是同一个延迟**（`TD = 1/(F·NL)`）：ngspice 实测两者逐点全等，
  `F=1G NL=2` 即 0.5 ns。两条写法互相矛盾（`TD` + `F` 同时给）或缺一半（`NL` 无 `F`）都报错。
- **顺带修掉一个真缺陷（由本轮的瞬态用例逼出来）**：`PULSE` 的宽度缺省原来是 **0**，于是
  `PULSE(0 3 0 1p)` 变成零宽尖峰、整段瞬态停在初值——任何「不给宽度的阶跃」都静默失效。
  SPICE 的缺省是 `tstop`，已按定义实现（瞬态段给所有 `V`/`I` 元件写入 `_tstop`）。
- **诚实清单同步**：`T` 从「按名字拒绝的卡」里移除，`NOT_COMPUTED` 那条改为
  「**有损**线 `O` 与 URC `U`、频变损耗」，并写明「只解理想无损线，真实走线的衰减与上升沿退化
  不在答案里」；`F H J K S W O U A` 仍然逐条点名。
- 新增 `tests/test_spice_tline.py` **37 例**（DC 导线/阶跃与反射/AC 相位与驻波/拒绝/告警/与真引擎
  对拍），并做了**突变检验**：把 `_stamp_tline` 在 DC 里改成只留支路行（卡解析但不贡献方程），
  只有「远端也读到 1.500 V」失败而 deck 级对照仍通过；把线在三个模式里都改成导线，5 条断言全红。
  新增 selftest 145/146（DC 两端 1.500 V + `±30 mA`、瞬态远端 `TD` 前 0 V 后 1.500 V），
  并把 id 96 的委派探针从 `T` 换成 `O`（1 Ω 串联电阻读 1.5149/1.4851 V，那 15 mV 就是理想 `T`
  没有的损耗），`tests/test_spice_engine.py` 里同一条「未解析卡直通」用例同步改成 `O` 卡；
  `docs/36` 与 `docs/17` §17.7 记口径。
- 验证：`--selftest` **120/120**；全量回归 **1708 例 / OK (skipped=5) / 702.669 s**
  （0.0.29 是 1671 例 / 766.788 s；新增 37 例，另有 1 例旧用例因 `T` 不再是「未实现的卡」而改判）。

## [0.0.29] - 2026-10-30

行为源 `B` 归内置管了。表达式在**解析期**编成一棵树，`laplace()` / `I(Vname)` / `ddt` / `idt`
仍然按名拒绝——把「语法」补上，同时不假装有复频域。

- **语法错在解析期报，带行号与卡面原文**：`line 2: B1: cannot read the expression 'V(1)+*2'`。
  如果留到牛顿第一次迭代 `eval`，「表达式写错」会以「不收敛」的形式出现，用户会去查浮空节点。
  同理 `1/0` 是 `EXPRESSION_INVALID` 而**不是** `NOT_CONVERGED`——一个改算术，一个改拓扑。
- **节点引用按源码文本取**：`V(1)` / `V(n-1)` / `V(in)` 直接塞进 Python 分别是数字、减法、
  关键字，三种错法都在语法上成立，所以只会在某张 deck 上安静地给出错答案。现在每个 `V(…)` 先被
  换成占位符并记下 `(n+, n-)`，`ast` 之后回查。`V(9)` 指向无人连接的节点时按行号报错，而不是
  读成 0 V 变成一个常数源；`I(...)` 按名拒绝（本求解器没有「任意元件电流」这个未知量）。
- **参数在构建期折成数字**：`.param k=3` / `.subckt params: gain` 都能进表达式，名字不在作用域
  时是构建错误 `'q' is not a parameter in scope here`，不是每轮迭代算不出值。
- **AC 按偏置点线性化**：`V=V(1)*0.5` 增益 0.5；`V=0.001*V(1)^2` 在 2 V 处 0.004，与
  ngspice-47 差 < 1e-6。用的就是装配时已经在算的雅可比列，所以 `.op` 与 `.ac` 不会各有一套导数。
- **两种条件写法，但对拍只用 `? :`**：**ngspice-47 没有 `if()` 函数**（实测
  `Error: no such function 'if'`），所以「两引擎答案不同」若混入方言差异，量的就不是数值了。
- **顺序陷阱（本轮真踩到）**：`? :` 展开出的 Python 三元含 `if` 关键字，而 `if(`→`_if_(` 的改名
  在同一条流水线上。先展开后改名会得到 `'((4) _if_ ((_vbnode0_>2)) else (1))'` 与
  `invalid syntax`。顺序写死：先改名，再展开。
- **差分在定义域边界退化**：`sqrt(V(1))` 在 `V=0` 处中央差分要点算 `sqrt(-1e-6)`，于是整张 deck
  报「表达式无值」。改成两侧各自 try、一侧成功就用单侧差分（一阶精度，是已知逼近不是缺陷）。
- **顺带抓到一个与 B 无关的真缺陷（由 B 逼出来）**：牛顿的 KCL 残差判据用**全局**尺度
  `1e-9 + 1e-6*max|x|`，一个 4000 V 节点把门槛抬到 4 mV，于是 1 kΩ 支路上 1 mA 的残差
  （1 V 误差）被判为收敛——节点回来 `-3998.999996`，安静、合理、差一伏。改成**按行**缩放
  （`denom` 只看该行的 `|A[r][c]*x[c]|`）后同一张 deck 7 次迭代收敛到 4000.000 / 2000.000。
- **诚实清单同步**：`B` 从「按名字拒绝的卡」里移除（`NOT_COMPUTED` 那条改为
  「`laplace(...)`、`I(Vname)`、B 源噪声、`ddt`/`idt`」），`F H J K S W T O U A` 仍然逐条点名。
- 新增 `tests/test_spice_b.py` **39 例**（常数/节点引用/算术与函数/时间与瞬态/参数/AC/与真引擎
  对拍/拒绝），改判 3 条旧用例（`test_spice.py` 的未实现卡清单、`test_spice_engine.py` 的两条
  B 探针），新增 selftest 141~144（B 分压 5.000 V、`laplace` 与 `1/0` 的两种 reason_code、
  4 kV 分压守卫），并把 id 96 的委派探针从 B 换成 `T`（B 已经不是「内置拒绝的卡」了）；
  `V=` 与 `I=` 同时给按卡面歧义拒绝，而不是让表达式编译器报 `2 I=3` 的语法错。
  口径写在 [`docs/35`](docs/35-B源与表达式.md)。
- 验证：`--selftest` **118/118**；全量回归 **1671 例 / OK (skipped=5) / 766.788 s**（新增 39 例，
  0.0.28 是 1632 例 / 663.082 s）。

## [0.0.28] - 2026-10-30

`.param` 现在在顶层与 `.subckt` 体内都成立。参数不是子电路的私产：真实 deck 几乎全靠参数化，
而参数在顶层没有来源，等于把 `0.0.27` 的子电路能力砍掉一半。

- **作用域链**：全局 → 子电路默认值 → 实例覆盖 → 体内 `.param`；靠后的遮蔽靠前的。体内
  `.param` 在**当前实例**的 env 里求值（`r=2k` 覆盖 ⇒ `rr={r*2}` = 4k），但体内名不可被实例
  覆盖（那会让派生量的语义取决于调用者）。全局参数对子电路体可见。
- **前向引用合法，循环必须报错**：`.param b={a*3} a=1k` 能算（按依赖轮次解析，不是先写的先算）；
  `.param a={b+1} b={a+1}` 报 `param 'a' refers to 'b', which is itself unresolved (still
  waiting: a, b) - a circular definition cannot be resolved`。拼错的引用的消息不同
  （`'q' is not a parameter in scope here`）——两种错误要改的地方不一样。
- **抓到并修掉一个静默错答案**：`_value("1k*2")` 一直把 `1k` 读成 1000、把 `*2` 当垃圾吃掉，
  于是 `R1 1 0 1k*2` 安静地得到 1 kΩ。在一次算阻抗的工具里这是最坏的一类错误。现在 `.param`
  与 `{…}` 位置（以及走 `_value_or_expr` 的元件值）要求整个 token 匹配
  `数字 + 可选指数 + 可选单位字母`，否则报错并写出正确写法：`write {1k*2}`。
- **只有一个数字语法**：不带括号的表达式（`.param r=1k*2`）拒绝。ngspice 接受它；本版要求
  `{1k*2}`，因为否则「这个 token 是数字还是表达式」要在四个位置各写一遍，四处边界迟早不一样。
- **源的三个数字槽共用一个读取器**：`dc` 值、`ac 幅度 相位`、`pulse(...)` 的七个参数，加上裸写的
  DC 值，全部经 `_value_or_expr` + 新 `_resolve_element()`。测例逐槽验：`dc={vdd}` 落在 2.5 V，
  `ac {mag} 0` 让节点幅度正好 0.5，`pulse(0 {vhi} …)` 解析后每个参数都是 float。
- 新增 `tests/test_spice_param.py` **22 例**（四个类 + 源参数 + 与 ngspice 的 `cross_check`），
  selftest 137/138 从「顶层 `.param` 被拒」改判为「循环 `.param`」，新增 139（6.750 V 与手写
  内联逐位相同）。口径写在 [`docs/34`](docs/34-参数与作用域.md)，`docs/33` 的对应边界就地标注
  被推翻。
- 验证：`--selftest` **115/115**；全量回归 **1632 例 / OK (skipped=5) / 663.082 s**
  （新增 22 例，0.0.27 是 1610 例 / 659.621 s）。

## [0.0.27] - 2026-10-30

内置 SPICE 现在能跑 `.subckt` / `X` 实例了。做法**不是**新写一个层次求解器，而是
**解析完之后把层次拍平成一份平铺 deck**，交给既有的求解器——方程层一行不改，子电路因此
变成纯语法层的能力。

- **内部节点名照抄 ngspice 的 `<实例>.<本地>` 小写**（嵌套是 `x9.x1.m`）。这不是审美：
  `cross_check` 靠节点名配对，内置用自己的命名的结果是「0 个共同节点」，而 0 个共同节点
  会被读成「一致」。逐字对齐后 `compared_nodes` 才真的到 2 以上、`max_abs_diff_v < 1e-6`。
- **参数在调用者作用域求值**：`.subckt NAME a b PARAMS: r=1k g={r*2}`；实例可覆盖
  （`r=3k` / `r={base/2}`）。实测 `9 V` 下默认给 **4.5 V**、覆盖 `r=3k` 给 **6.75 V**、
  `{r2*2}` 给 **6.0 V**；负控制是「默认与覆盖必须差 > 5%」——参数没传进去时两数相同，这条会失败。
- **表达式只做 `+ - * / **`，走 `ast` 白名单，不用 `eval`**：函数（`sqrt`/`sin`）、节点引用、
  `.model` 参数里的表达式一律按名字拒绝。半实现的表达式语言就是「看起来对的错答案」的生产机。
  另有除零、指数 > 64、非有限结果三道闸。
- **子电路内 `.model` 加实例前缀，全局 `.model` 不被复制**：两个子电路各写 `.model dg`
  时互不覆盖（测试用 IS 差 1e5 倍的两个二极管，电流差 > 10 倍）。
- **接地引脚塌成唯一全局地**（`0`/`gnd`/`gnd!`/`vss`）。这里踩过一个很难看的坑：
  归一后的地是 `None`，而 `_node(None)` 原本 `str(None)` 造出一个名为 `"None"` 的假节点，
  嵌套实例的输出从 6 V 变 12 V、还看不出错。修法 `_node` 早退，测试断言结果里没有 `"None"` 键。
- **五类错误在拍平期就报掉**（未知子电路 / 引脚数不符 / 未声明参数 / 自引用含链式
  `a → b → a` / 深度超 `SUBCKT_MAX_DEPTH = 32`），另有四条：顶层 `.param` 专门报
  「parameters exist only on .subckt definitions and on their X instances」、体内嵌套 `.subckt` 拒绝、
  体内非 `.model` 指令拒绝、缺 `.ends` 时先遇到单行 `.end` 报
  **「missing .ends, not a stray directive」**（`.end` 是 `.ends` 的前缀，判断顺序必须先 `.ends`）。
- **自证比「和 ngspice 一致」更硬**：同一电路手写成平铺 deck 后逐节点比，`div` 分压器
  `x1.m = 4.999999997` 与平铺版差 < 1e-12，嵌套版 `x9.mid = 6.0`。引脚绑错任何一个，
  差值就是量级级别——这条判据能失败。
- **诚实登记一条既有分歧**：DC 无到地通路的浮空岛，内置给 0 V、ngspice 给约 2.5 V
  （`gmin` 约定不同）。同一结构的**手写平铺 deck** 分歧幅度逐位相同（2.501057919 对
  2.501057919）⇒ 与层次无关、`0.0.27` 之前就有。所以它没被「修掉」，而是写进测试并断言两边一致。
- `X` 从 `NOT_IMPLEMENTED_CARDS` 移除；`NOT_COMPUTED` 里「subcircuits (.subckt), hierarchical
  netlists」换成两条范围明确的限制（顶层 `.param` 与非常数表达式；嵌套定义与体内非 `.model` 指令）。
  `sim_spice` 与 `calc_capabilities` 的自述同步改写。
- 新增 `tests/test_spice_subckt.py` **27 例**（六个类：忠实展开 / 实例不共享节点 / 参数落地 /
  局部模型作用域 / 接地引脚全局 / 该拒绝的贵错误 / 真引擎一致），`tests/test_spice_engine.py`
  里原来那条「内置拒绝、引擎能跑」改成「两引擎都能跑且 `cross_check` 比到内部节点」，
  selftest 新增 3 条（135/136/137+138 合并）。口径写在 [`docs/33`](docs/33-subckt与层次展开.md)。

- 验证：`--selftest` **114/114**；全量回归 **1610 例 / OK (skipped=5) / 659.621 s**（新增 27 例，0.0.26 是 1583 例 / 740.732 s）。

## [0.0.26] - 2026-10-30

0.0.24 的边耦合共面差分对接进馈线：`feedline_impedance` / `feedline_width` 多两个 kind
（`cpw_diff` / `gcpw_diff`）与一个必填的 `spacing_mm`。**这两个 kind 没有新增物理**，它们
路由到同一个场解；新增的是一条通路，以及这条通路上「赔多少钱」的口径。

- **差分必须标成差分**：`z0_ohm` / `value` 是 `Zdiff = 2·Z_odd`，`z0_meaning` 以
  `DIFFERENTIAL` 开头，`value["differential"]` 带上 `z_odd_ohm` / `z_even_ohm` /
  `z_common_ohm`（= 0.5·Z_even）/ `eps_eff_odd` / `eps_eff_even` / `charge_balance_odd` /
  `mesh_drift_ohm` / `mesh`。`w=0.3 s=0.2 g=0.2 h=0.2 er=4.3` 实测
  **cpw_diff 129.86 Ω**（odd 64.9301 / even 146.2017 / common 73.1009 / eps 2.40452）、
  **gcpw_diff 101.887 Ω**（50.9437 / 65.4168 / 32.7084 / 2.88850）。
- **损耗的分母是差分电流**：`Rs/(2·Z_odd)·∮g²ds`，覆盖两根条 + 两片地。实测算术
  `conductor 0.4312` / `dielectric 0.5157` / `total 0.9469` dB/100mm，`strips` 4.528725 一份、
  `strips_each` 2.264362 一根、`return_left == return_right` 0.257699、`return_back == 0.0`，
  且 `field_over_sheet_ratio = 0.757`（薄层式不给地任何份额，所以场解必须更低）。
- **反解：三段，且默认网格有最后一句**。粗网格阶梯找括号（不扫整个窗口）→ 便宜网格二分
  （停条件是**宽度括号 < 0.15·h**，不是阻抗容差）+ 局部斜率 → 默认网格一次 + 一步牛顿 +
  最多两步割线。**第一版是错的**：便宜网格定宽度、默认网格复算一遍再把差距报出来，返回
  101.13 Ω 对目标的 100 Ω，差 1.1%——它把误差揭发出来而不是消掉。便宜网格的偏差是**乘性**
  的（实测 −1.06%），所以斜率穿得过它，牛顿步实测落在 +0.063 Ω。
- **耗时是网格属性，不是算法属性**：`fine_div=10` 时 w=0.3 是 5.2 s、w=0.82 是 ~30 s、
  w=2.33 是 ~2 min；`fine_div=3` 在 w/h=0.05 / w=20h 两端要 22 s / 28 s（细步长跟着
  `min(w,s,h)` 走，盒子却还得装远场）。于是一次反解 130 Ω **21 s**、100 Ω **85 s**、80 Ω
  **360 s**，全部 2 次默认网格求解、残差 0.04~0.07 Ω。
- **诚实三件套**：`abs_error_ohm` / `converged` / `default_solves` 加一条只在没收敛时出现的
  `not_converged_note`，外加 `cheap_mesh_width_mm` / `cheap_mesh_z0_ohm` /
  `cheap_over_default_pct`（便宜网格**必须**被纠正的量）。容差 `max(0.1, 1e-3·target)` 是有
  依据的：解算器自身离散度 0.1~0.5%，追到 0.0009 Ω 要多花一次 30 s 求解。
- **`spacing_mm` 必填、不默认**：缺 → `ARG_REQUIRED`，`0` / 负 → `ARG_RANGE`，非数字 →
  `ARG_INVALID`；单线 kind 传了它**逐位不变**（`value` 与 `assumptions` 全等）。
- **顺手修掉一句过期文案**：`feedline_impedance` 的描述里「Coplanar conductor loss is still
  refused, not guessed.」从 0.0.23 起就不成立了，现在改成实际行为；同时给差分 kind 明写
  「`verify_field` 不是比较——场解就是答案」。
- `--selftest` 加 132/133/134（**111/111**）；`tests/test_feedline_diff.py` 13 例（约 167 s），
  两个负控制：`_PairIgnored`（当成两根独立单线 = 179.35 Ω，错 27.6%）与 `_SpacingDropped`
  （参数收下不用，与 0.0.25 真实出过的那一次同型）。全量 **1583 例 / OK（skipped=5）/ 740.7 s**
  （比 0.0.25 的 516.9 s 多出来的部分几乎全在新增的 13 例差分对测试上：每例都要真解一次场）。

## [0.0.25] - 2026-10-29

共面的地不再是无限大的：`coplanar_field` 与 `coplanar_pair_field` 各多一个可选
`ground_mm`，`0`（默认）逐位等于旧行为，`> 0` 表示每块地从缝口外沿起就只有那么宽。

- **默认零变化是逐位证明的，不是"应该不会变"**：`ground_mm = None` 走原来那条代码路径，
  一个锚点都不多；测试用 `assertEqual`（不是 `assertAlmostEqual`）比对 `value` 与 `eps_eff`。
  理由很硬——`VALIDATION` 里每一个共面数都是在无限地上量的，默认一改，等于把历史数字
  悄悄重新定义一遍。
- **地的外沿强制落在网格线上**：有限宽地的全部物理就是"回流在那条边停住"，边落在单元
  中间会让答案依赖单元尺寸。截断盒同时放大到 `max(8·(w+2g), ground_w + span)`，
  否则宽地会被截在自己里面、又变回窄地，且不报错。
- **单线实测**（`w=0.3/g=0.2/h=0.2/er=4.3`，工具默认网格，无限地 89.674 Ω）：

  | `ground_mm` | 相当于 | `Z0` | ΔZ0 | `eps_eff` |
  |---|---|---|---|---|
  | 0.1 | g/2 | 101.971 | +13.71% | 2.4703 |
  | 0.2 | g | 96.362 | +7.46% | 2.4197 |
  | 0.5 | 2.5 g | 92.049 | +2.65% | 2.3468 |
  | 1.0 | 5 g | 90.659 | +1.10% | 2.3117 |
  | 5.0 | 25 g | 89.896 | +0.25% | 2.2915 |

  `eps_eff` 一起涨（2.2925 → 2.4703）不是笔误：窄地把电流挤到条的正下方，那里是衬底。
  gcpw 反过来几乎不敏感（同几何 `g/2` 只 +0.36%），因为回流主要走背面那块地。
- **差分对的分裂才是设计相关性所在**：同一个 `ground_mm = g/2`，`z_odd` 只 +1.06%
  而 `z_even` +21.90%（`z_common` 73.1 → 89.1 Ω）。奇模回流贴缝口，削窄只是把回流从远端
  挪到近端；偶模回流要铺开，地一窄就没地方铺。所以**窄地是共模问题，不是差模问题**，
  "地留窄点省面积"对差模成立、对共模不成立。
- **代价不是多几个节点，是多几轮迭代**：单线 `ground_mm=0.1` 节点 2257→2627（+16%）
  但 SOR sweeps 564→3334（+490%），墙钟 1.6 s→12.5 s；`5 mm` 时 20313 节点、83 s。
  地外沿是表面电荷的奇异边，那里最后收敛——放松容差最先坏的也是那里。
- **判据单独立项**：`VALIDATION["coplanar_finite_ground"]`，并写明**不进**
  `coplanar_vs_conformal`——保角映射的地是无限宽，拿它当有限宽地的误差棒是错的。
  这一项里只有四类成立：默认零变化（逐位）、单调性、回落到无限地（25 g 差 0.25%）、
  两模分裂（`z_even` 反应必须 ≥5 倍 `z_odd`）。
- **负控制是本轮真踩到的 bug**：`ground_mm` 在截面类里实现了，但工具层 schema 没登记，
  于是 `coplanar_field(..., ground_mm=0.1)` 返回的仍是 89.67 Ω、`ground_width_mm` 还是
  `null`，**而且没有任何报错**。"新参数默认关 ⇒ 行为零变化"这条要求反过来正好掩盖了
  "参数根本没接上"，两者输出完全一样，所以必须有专门的断言区分。测试类 `_WingIgnored`
  （接受参数、然后丢掉）复现那个形态：它必须逐位等于无限地，而诚实实现必须离它 >2%。

新判据：自检 130（单线 13.7% 与"默认即无限"）、131（差分对 odd 1% vs even 22%）。
新测试 `tests/test_coplanar_ground.py`（8 例）。新文档 [`docs/31-共面有限宽地.md`](docs/31-共面有限宽地.md)。

不做：地外沿倒角/圆角（第二个离散误差源，和"边在哪"是两件事）、地被打断成岛（拓扑
改变，不是宽度参数）、缝合过孔/屏蔽罩（三维结构，二维准静态截面表达不了）、有限宽地的
闭式对照（本仓库没有可引用的实现，凭记忆转写＝编造，所以只报自洽不报"±x%"）。

全量 **1570 例 / OK（skipped=5）/ 516.9 s**；`--selftest` **108/108**。

## [0.0.24] - 2026-10-29

「没有闭式可对账的截面，判据要换成什么」——`0.0.23` 把共面**单线**接进了场解，至少还能和
`feedline._cpw` 的共形映射对账（−1.19%…−3.39%）。边耦合共面线（差分 CPW / GCPW）**没有可引用的闭式**，
所以这一批的重点不是「又解了一个几何」，而是**在没有外部数值的情况下凭什么相信它**：
三类判据（叠加恒等式 / 精确不变量 / 极限）+ 两个故意写错的负控制。
新工具 `coplanar_pair_field`，工具数 **150 → 151**，`--selftest` 104 → **106/106**。

### 两个模式 = 两个驱动，不是两个对称面

微带/带状线那一对用半域 + 电壁/磁壁。共面**对**不这么做：墙一旦是求解器的假设，
就不能再被任何检查发现，而这一批恰恰没有外部参照物——只有内部不变量。
所以网格**镜像穿过 `x = 0`**，奇模（+1/−1）与偶模（+1/+1）是同一张全板网格上的两个驱动，
第三个驱动 `grounded`（+1/0）只用于自检、不进工具（否则一次调用从 8 次求解涨到 12 次）。
代价是节点翻倍：基准几何网点 3293 → 5588，`levels=2` 一次 **37.8 s**（工具默认就是它）。

- **坑一：左条被 SOR 悄悄松弛掉**。`_Section.build` 只把 `live` 与 `ground` 当固定节点，
  `live_left` 留在未知集里，每轮被当成空介质重算：实测电位停在 **0.272 V** 而不是 −1 V，
  于是 `z_odd == z_even == 108.944`、`charge_balance` 恒 0。修法是 `build` 之后手动把它
  从 `_rows`/`_free` 里摘掉。**这个 bug 不报错，只给一个看着很正常的数。**
- **坑二：盒尺寸跟着间距走会毁掉唯一可检的极限**。按 footprint 缩放时 `s=1.5 mm` 读到 **104.0 Ω**，
  而同一根条单独解是 **89.7 Ω**——差的是边界距离。改成从**地外沿**量（`span = w+2g`）。

### 四条自检

| 检查 | 性质 | 实测 |
|---|---|---|
| 叠加恒等式 `(C_odd+C_even)/2 == C_grounded` | 独立物理（三个驱动） | 介质残差 **1.92e-08**、空气 **5.78e-08**（受 SOR 的 1e-8 容差限制，不是机器精度） |
| 奇模严格反对称 | 精确 | `charge_balance_odd = −1.000000000`、even `+1.000000000` |
| 形状因子左右逐位相等 | 精确 | 左右条各 **2.264362**、左右翼各 **0.257699**（`places=12`），cpw 背面 0 |
| 间距 → 0：共模落到 2w 单线 | 极限（对照已对过账的闭式） | **−0.87%**（`s=w/6`）、−0.83%（`s=w/10`）；廉价网格 −0.62% |

三个驱动实测（`cpw w=0.3 s=0.2 g=0.2 h=0.2 er=4.3`，`fine_div=10`）：
`c_dielectric` odd **8.997022** / grounded **6.347287** / even **3.697552**；
`c_air` 3.741710 / 2.768722 / 1.795734。

### 两个极限方向相反，这是最容易被写错的地方

`Z_common` 随间距收敛到「一根 2w 宽的条」，而 `Z_odd` **发散到 0**——两条 +V/−V 之间的缝
电容按 `1/s` 对数发散，`Z_odd → 0` 是物理正确的，不是求解失败。反过来，间距 → **大**
**不**回到单线：`s = 0.6 / 1.5 / 4.0 mm` 时 `z_odd = 86.6 / 104.1 / 116.5 Ω`，
而同一根条单独解是 89.7 Ω——每条条此时只有**一侧**有地。这条故意不断言，但写进
`VALIDATION["coplanar_pair_limits"]` 里作为口径。

### 差分损耗：三件事同时成立

`alpha_c = Rs/(2·Z_odd)·∫g²ds`（奇模表面电荷，按**差分电流**归一化）。w=0.3 s=0.2 g=0.2 h=0.2
er=4.3 @2.4 GHz tanδ=0.02：导体 **0.4312** + 介质 **0.5157** = **0.9469 dB/100mm**
（同一几何的单线是 0.2081 / 0.6642，形状因子 3.321224 → **5.044122**）。

1. **归一化只能选差分电流**：两条条各自归一化会让左右不同（差模下左条电荷是负的），
   归一化到总和则在奇模下恒为 0（两端地电流和为零），只能以正条电荷为 `q_ref` 再把所有导体求和。
2. **形状因子是零厚度值，网格漂移 8.79%**（`fine_div=8/refine=1.5` 对 4.6 的粗网格）；
   真实铜箔约再高 10% 而那 10% 已测过不收敛 ⇒ 这一项是**下界 + 不确定度**，
   `conductor_note` 逐字这么写。
3. **墙上的电流不是免费的**：`S` 从 3.32 涨到 5.04（+52%），`Z_odd` 只有单线的 0.72 倍，
   `alpha_c ∝ S/Z` 因此涨约 2.07 倍——不是「多了根线所以翻倍」那么简单。

### 两个「故意写错」的负控制，以及一条元结论

| 负控制 | 造出的 bug | 抓它的是哪条 |
|---|---|---|
| `_FreeSecondStrip` | 左条不摘出未知集（= 上面坑一） | 两个模式的电容**塌成同一个数**（`places=9`），且该数既不是诚实的 odd 也不是 even |
| `_SignFlipped` | 奇模驱动忘了负号（+1/+1） | 叠加恒等式残差 ≈ 0.42（>5%） |

**并且把「恒等式对浮动条是盲的」也写成断言**：浮动左条时残差只有 **1.47e-10**——
浮动电位是驱动的同一个线性函数，三次求解照样构成线性关系。也就是说**一条检查兜不住两种 bug**。
（`_SignFlipped` 第一版忘了同时摘左条，于是 `val` 根本没进解、残差 1.4e-10，**控制静默失效**；
这条写进了它的 docstring。）

文档：[`docs/30-共面差分对.md`](docs/30-共面差分对.md)。用例新增 `tests/test_coplanar_pair.py` 15 例
（其中 2 例是负控制、1 例专门钉住「恒等式对浮动条盲」），全量 **1562 例 / OK（skipped=5）/ 586.7 s**。

## [0.0.23] - 2026-10-29

「共面线也能解了，而且它只过得了四关里的三关」——`0.0.19` 起微带/带状线的导体损耗由二维场解给出，
但 `kind="cpw"/"gcpw"` 一直回 `conductor_db_per_100mm: null`（回流在同一层，`field2d` 没有共面截面）。
这一批补上共面截面：新工具 `coplanar_field`，共面损耗接上，`feedline_impedance` /
`feedline_width` 的 `verify_field` 也覆盖共面。工具数 **149 → 150**，`--selftest` 102 → **104/104**。

### 共面截面不是「把微带的类改一改」

三处看起来能省、实际会静默出错的地方，都被单独立了检查：

- **半域对称**：先写的半域（`x ≥ 0`，对称面当电壁）读高 2.16%，且与网格细化无关。原因有两层——
  对称面 `i=0` 那列控制体积只有**半格**、而 `charge()` 按整格面长算（电容偏大 2.5%），
  以及半域电荷只有物理值的一半（`eta0/sqrt(c*ca)` 直接算就是 2 倍 Z0）。
  修法是**放弃半域，改全板网格**：场解存在的意义是给一个和闭式不共享假设的答案，
  它连对称面都跟闭式一样假设，就没法核闭式。
- **前向差分 vs 中心差分**：`_Section` 的 `x[i+1]-x[i]` 在 `x→-x` 下不对称，共面线要报**左翼/右翼**
  份额时实测 **0.683 / 0.620**（差 10%），而 Z0 完全看不出来。`_Coplanar` 改中心差分后两翼
  **逐位相等**（`places=12`），电势解反对称度实测 `0.000e+00`。`_Section` 一个字节没动——
  动它会连带改 `VALIDATION` 表、`verify_field` 与 `filters.py` 的 Newton 反演。
- **盒尺寸跟着 h 走**：`24h` 在 0.2 mm 衬底上收敛得很好（8h 90.315 / 12h 89.940 / 16h 89.802 /
  24h 89.689），但 2 mm 衬底就是 48 mm 的地、7345 节点、**4000 sweep 还没收敛**，一次求解几分钟。
  量出来的事实是：**截断尺度由面内几何决定，不由衬底高度决定**——2 mm 衬底在 `4 mm ≈ 5.7(w+2g)`
  处已经和 48 mm 差 0.15%。盒改成 `8·(w+2g)`，那一例从「跑不完」降到 66 s。

### 只有三关能过，第四关（增量电感）原理上不成立

| 检查 | 性质 | 实测 |
|---|---|---|
| 对共形映射 | 独立物理 | 七个几何 **−1.19% … −3.39%，全部同号（场解读低）** |
| 尺度不变性（几何 ×2 ⇒ S/2） | 精确 | `2*S2/S = 1.000000` |
| 两翼份额逐位相等 | 精确 | `places=12` |
| 零厚度漂移 > 有限厚度漂移 | 机制 | 8.43%/6.10% vs 6.68%/4.06% |

微带/带状线有两条独立校验，其中一条是增量电感。共面**做不了，也不该硬做**：零厚度下中心条两条边
和地两条边全是 `r^-1/2`，`∫g²ds` 对数发散、没有收敛值，闭式导数只含正则部分 —— 第一版调网格
「对上」差 3~4 倍，那不是仪器不准，是检查在这里不成立。所以换成上面四条，并把这个缺口写进文档。

**误差的符号本身是结论**：七个几何全部是场解读低，也就是共形映射站在乐观那一端，偏差随 `h/g`
增长（0.67 → −1.19%，13.3 → −3.39%）。过去只能含糊说「模型自己几个百分点」。

### 共面损耗：从「拒绝」变成「有方向的数」

`alpha_c = Rs/(2·Z0)·∫g²ds`，与微带/带状线共用尾段。w=0.3 g=0.2 h=0.2 er=4.3 @2.4 GHz tanδ=0.02：
cpw 介质 0.4561 + 导体 0.2081 = **0.6642 dB/100mm**；gcpw 0.6718 + 0.3150 = **0.9868**。
`shape_factor_split` 把回流拆成**中心条 / 左翼 / 右翼 / 背面地**四路（cpw 背面恒 0；
**gcpw 背面承担 92%**，因为两个翼被背面地短接），三项之和与 S 逐位相符。

三件同时说清的事：① 场解与薄层 `Rs/(Z0·w)` 的比值是 **0.49~0.50**，不是错误——薄层式把回流
整个漏掉，而共面线的回流是铜损的约三分之一；② 只有**零厚度**值，真实铜箔更高约 10%
（实测 3.24 @两格 / 3.39 @2.5 格 / 3.50 @三格 / 3.67 @四格，**还在爬**），所以它是**下界**；
③ 零厚度 `∫g²ds` 的对数依赖以 `mesh_drift_pct`（6%~8%）明示，不把最后一位当确定值。

### 同批抓到并修掉的东西

- `coplanar_solve_pair` / `coplanar_shape_factor` 的 `max_nodes` 护栏放在**第一次 SOR sweep 之前**
  （网格便宜、求解贵），超限抛 `section needs %d mesh nodes, over the %d-node limit`；
  工具侧翻译成 `OUT_OF_VALIDITY` 并给三条出路。
- 两条旧测试是**故意**钉住旧口径的（`test_cpw_conductor_loss_is_null_not_guessed`、
  `test_coplanar_still_refuses_conductor_loss`），随能力升级一起改写成「有值 + 三处声明」，
  并新增 gcpw 背面回流 > 80% 的一条。
- `docs/16` §13.8「CPW 仍然拒答」与两行表格已过期，按「权威台账漏记会静默腐烂」的教训就地改写，
  保留原来的推理、标出边界变化，指向新文档。

文档：[`docs/29-共面波导场解.md`](docs/29-共面波导场解.md)。用例新增 `tests/test_coplanar.py` 15 例，全量 **1547 例 / OK（skipped=5）/ 666.7 s**。

## [0.0.22] - 2026-10-29

「板子会不会先断在焊点上」——前面二十一批都只回答「电学/热学答数是多少」，
这一批开始回答**「它撑得住多少次」**。三个新工具：`solder_joint_fatigue`（焊点热疲劳）、
`drop_board_strain`（跌落的板面应变）、`vibration_shock_spectrum`（冲击响应谱）。
工具数 **146 → 149**，用例 1490 → **1531**（新增 `test_reliability` 41 例），
`--selftest` 98 → **102/102**。

### 三条工具的分工：能闭合的给闭合，不能闭合的只给「有多狠」

这一批最关键的取舍是**不给失效判据**。焊点热疲劳有公认的闭合模型（Engelmaier），
所以直接给 `Nf`；但「跌落到多少 µε 才算焊点断」没有普适值——那是器件级鉴定报告里的数。
于是拆成三层，每一层只承担它能承担的那一件事：

- 热疲劳 → 给 `cycles_to_failure`，物理闭合，不问用户要阈值；
- 跌落 → 只给**板面表面应变**，`verdict` 由调用方传 `critical_strain_ue` 才出现，
  不给就 `judged: false` + `verdict: null` + 一条 warning；
- 冲击谱 → 只回答「这个脉冲在各频点有多狠」，默认 5 % 阻尼并明示那是 **SRS 惯例**
  而不是你的板子的属性，且**不用它判任何失效**。

同一批里还有一个反向动作：`solder_joint_fatigue` 的 `fatigue_ductility` 给了默认 0.325
（Engelmaier SnPb 拟合），但必须 `assumptions.ductility` 点名 + 文档摊开敏感性——
同一应变下 ε_f = 0.200 / 0.325 / 0.350 实测给 **Nf = 582 / 1870 / 2235，差 3.8 倍**。
给默认是为了不让用户去查一个他也查不到的拟合常数，但绝不把它伪装成物理常数。

### 焊点热疲劳：Engelmaier 的 `c` 方向性被写反，被测试抓出来

剪切应变（Coffin-Manson 用的是切应变幅，符号只决定谁受压）：

```
Δγ = F · DNP · |α_c − α_b| · ΔT / h
Nf = 0.5 · (Δγ / (2 ε_f))^(1/c)
c  = −0.442 − 6e-4·T̄_sj + 1.74e-2·ln(1 + 360/t_D)
```

`DNP` 是器件到中性点距离，可直给 `dnp_mm`，也可由 `body_a_mm`/`body_b_mm` 算对角线
`hypot(a/2, b/2)`（回报 `dnp_source` 说明取的是哪一路）。`Δα` 内部只取绝对值——
`alpha_component=6`、`alpha_board=16` 时 `dalpha_ppm = −10` 是**合法输入**，
符号只告诉你哪一侧受压，不影响应变幅；回报里仍保留带符号的 `delta_alpha_ppm`。

第一版 docstring 把 `c` 的方向写反了（写成「c 靠近 0 ⇒ Nf 上升」）。真实情况是：
`−6e-4·T̄` 与 `1.74e-2·ln(1+360/t_D)` 两项都让 `c` **更负**，而 `1/c` 为负，
所以**更热**（T̄ 更大）和**驻留更久**（t_D 更大）都**降低** Nf。机理是蠕变需要时间：
慢而热的循环更伤。测试 `test_hotter_is_more_damaging` / `test_longer_dwell_is_more_damaging`
把这个方向钉死，docstring 已改并补了一句解释。

实测表（15×15 mm 器件、0.5 mm standoff、Δα=10 ppm/K、ΔT=100 K ⇒ Δγ=0.0212132）：

| 条件 | c | Nf |
|---|---|---|
| T̄=25 ℃ / 15 min | −0.4010 | 2544 |
| T̄=50 ℃ / 15 min | −0.4160 | 1870 |
| T̄=85 ℃ / 15 min | −0.4370 | 1259 |
| T̄=125 ℃ / 15 min | −0.4610 | 838 |
| T̄=50 ℃ / 1 min | −0.3695 | 5261 |
| T̄=50 ℃ / 60 min | −0.4381 | 1234 |
| T̄=50 ℃ / 240 min | −0.4561 | 908 |

Miner 线性累积损伤：`damage_i = n_i / Nf_i`，`blocks_to_failure = 1 / Σ damage_i`。
**有 `cycles` 才有 `verdict`**，没给 `cycles` 时 `total_damage`/`blocks_to_failure`/`verdict`
三者全为 `None`——给 0 会被下游读成「不损伤」，`None` 才会让调用方意识到自己没给工况。

### Norris-Landzberg：只能搬幂律，不能再拟合一遍

`N ∝ f^n · ΔT^(−m) · exp(Ea / k T_max)`（m=1.75、n=1/3、Ea=0.141 eV、
k=8.617333262e-5 eV/K），加速因子：

```
AF = N_use/N_test = (ΔT_test/ΔT_use)^m · (f_use/f_test)^n · exp(Ea/k·(1/T_use − 1/T_test))
```

实测：test 165 K / 2 cpd / 125 ℃，use 100 K / 1 cpd / 55 ℃ ⇒ **AF = 4.58117**，
Nf_test 197.97 → 折合 **906.95**。`frequency_cpd` 是**含驻留的循环频率**，
和 `c` 里的 `t_D` 是两个独立旋钮，**不可互换**——`norris_landzberg.c_dwell` 已写明。
另外这一项只做「把测试寿命外推到使用条件」，不重拟合 ε_f/c 那套常数。

### 跌落：串已有的三段，不新写物理

跌落的板面应变是三个已经各自对过账的求解器串起来的，本批**一行新物理都没写**：

1. `vibration.plate_modes` → 一阶频率 f1；
2. `vibration.shock_sdof` → 峰值相对位移（单模态等价）；
3. `vibration.plate_stress` → `surface_strain_ue`。

实测（100×80×1.6 mm FR-4、四角点支、Q=15、1500 g / 0.5 ms 半正弦）：
f1 **223.662 Hz**、静位移 7.44848 mm、峰值挠度 **3.12767 mm**、
放大率 **0.4199**（< 1，**短脉冲的正确结果**：脉冲比半周期还短，板子来不及跟上）、
ZPA 631.268 g、表面应变 **2036.8 µε**。

串联的代价是「错在哪一段」会糊成一个错答案，所以测试**逐段复核**（分别比频率、比位移、
比应变）——这样串错哪一段会暴露成三个不同的失败点。可选焊点剪切应变
`γ = ε_board·DNP/h`：`dnp_mm` 与 `joint_height_mm` 必须同时给，只给一个就 `ARG_REQUIRED`。

### 冲击响应谱：逐频调同一个 SDOF，5 % 阻尼是惯例不是属性

对 20 Hz~5 kHz 的对数频点逐个调用 `shock_sdof`，每个点报 `zpa_g`、`pv_mm_s = 2πf·disp`、
`disp_mm`。默认 **5 % 阻尼**，回报里 `damping.defaulted: true` + `convention` 明示
「SRS 惯例，不是你的板子的属性」。

实测（1500 g / 0.5 ms 半正弦、5 % 阻尼、20 Hz~5 kHz 对数 12 点）：
峰值 ZPA **2462.06 g @ 1832.23 Hz**（= **1.641×** 输入，半正弦理论上限约 1.73）；
低频 PV 平台 **4337.7 mm/s**，而理想速度跃变 ΔV = a₀·2t_d/π = **4682.3 mm/s**，
两者比值 **0.9250** = `exp(−ζπ/2)`——这在测试里是硬断言，用来确认「低频段
PV = 速度跃变被阻尼吃掉的那一点」，而不是靠形状看着像。

两条自检：阶跃 + ζ=1e-9 ⇒ 每频恰好 2×（实测 59.9995/59.9999/60.0，输入 30 g）；
任一点不超 2×输入峰值。**ζ 恰好 0 会被 `_sdof_zeta` 拒绝**——无阻尼不是阻尼比。

### 两条不共用代码的第二解 + 三条独立自检

延续 0.0.21 的 ABCD 级联思路，本批给两个最容易被「代数重排抄错」的量各写了一条
**完全不同代码路径**的验证：

- `Nf`：测试里用二分法反解 `ε_f·(2N)^c − Δγ/2 = 0`，不经 `0.5·(Δγ/(2ε_f))^(1/c)` 的代数重排；
- `AF`：测试里把 test 与 use 两条**完整绝对寿命**分别算出来再相除，
  这样 `(f_use/f_test)^n` 的方向写反（AF 从 4.58 变 0.79）会立刻暴露。

模块里的 `self_check` 与主算式同源，只能抓「公式写错」，抓不了「公式抄错」——
这是它的边界，文档里写明了。

### 能力自述

`layers` 新增 `reliability` 一条；`vibration` 的 NOT-done 段改写为
「assembly compliance / random+shock combined / 非线性接触仍不做，
跌落的整链与焊点疲劳已下移到 reliability 层」。文档 `docs/28-焊点疲劳与跌落.md`
的 §28.6「明确不做的事」列了 8 条，包括：不给焊点失效阈值、不做器件级 CTE 表、
不做焊点蠕变/疲劳的耦合、不做 PCB 翘曲、不做多轴跌落、不做随机振动+冲击合成、
不做疲劳裂纹扩展（只做寿命估算）、不替代 JEDEC 条件定义。

## [0.0.21] - 2026-10-29

「交叉耦合看得见了」——前面几批的滤波器全部是 `inline`（只有相邻耦合），所以
**说不出交叉耦合零点**，也算不了**有限无载 Q** 对通带与零点的削平。
本批新增 `filter_coupling_matrix`：给一个耦合矩阵 + 外部 Q + 无载 Q，返回 S11/S21 曲线、
带内纹波、每个零点的位置与深度。工具数 **145 → 146**，用例 1451 → **1490**
（`test_filters` 31，新增 `test_coupling_matrix` 39），`--selftest` 95 → **98/98**。

### 引擎：节点法 + 理想导纳逆变器的精确 Π 等效

只有 Π 等效能把「交叉耦合」这笔账记对：一个耦合量 `J` 不只是两点之间的串联支路
（那是 `inline` 的直觉），它两端还各带一次 shunt。用 ABCD 级联只能串成链，
一旦出现 `1-3` 这种非相邻耦合就**表达不了环路**；而把整个案子写成矩阵 `A = ΩU − jR + M`
再求逆拿 S，得到的是**非物理的 |S11|**（|S21| 恰好还对，所以特别容易蒙混过关）。

最终做法：把每个耦合按 `1j*J` 两端 shunt + 两点之间 `1j/J` 串联精确 stamp 进节点方程，
节点 0 = 源、1..n = 谐振器、n+1 = 负载，源/负载各并 1 S（Z0 归一化），
每个谐振器 shunt `1j*Ω`（有损时再加 `1/(Qu·FBW)`），解 `Y·V = e_s` 得
`S11 = 2·V0 − 1`、`S21 = 2·V_{n+1}`。矩阵因此只保留两个用途：
`M` 的代数来源（inline 的 `m(i,i+1) = 1/√(g_i g_{i+1})`），以及零点余子式
`Ω_z = m12²/m13`。

中途踩过一个更隐蔽的坑：早期版本在节点之间插了**内部节点**来实现串联支路，
等效于把阻抗翻倍——整块阻带从 −5.8 dB 起，形状还是「对的」。改成直接在两节点间
stamp `1/z` 后自检才归零。

### `Qu` 归一化：1/FBW 倍的错，而且看起来很像对的

`inline` 合成给的是**归一化**网络（带宽 = FBW），所以有损支路必须是
`1/(Qu·FBW)` 而不是 `1/Qu`。用错时插损小 **1/FBW 倍**：FBW = 0.1、Qu = 200 时
算出 **0.093 dB**，真值 **0.920 dB**，正好 **~10 倍**。
这个错**不会**被曲线形状或幺正性抓住，是被 Matthaei 的损耗近似对账抓出来的
（现在判 `INTERNAL_CHECK_FAILED`）。对账实测偏差：
`n=3/Qu=200/FBW=0.1/0.5 dB → −1.18%`、`n=5/Qu=500 → −0.68%`、
`n=3/Qu=400/FBW=0.1@2.4 GHz → −0.62%`、`n=2/Qu=100 → −9.12%`
（偶数阶带 ripple 基线，已按 `base_il = ripple` 处理）。

### 闭式检查为什么用 `eps_eff` 而不是精确 ε

初版拿 `filters._epsilon` 当参考，结果 chebyshev **全阶**报
1.8e-4 ~ 4.4e-4 dB 的失配，36 组 smoke 里 6 组判 `INTERNAL_CHECK_FAILED`。
根因不是本模块算错：`filters.py` 的 g 值来自 Matthaei 的
`log(1/tanh(R/17.37))` 递归，与精确 ε 本就差 ~5e-5 相对，这个差落在远阻带就是 4e-4 dB，
而且**与 FBW 无关**（0.2/0.08/0.02/0.005/0.001 依次 4.41e-4/4.44e-4/4.46e-4/4.46e-4/4.46e-4）。

解法是把参考式钉在网络自己的 |Ω| = 1 点上：两个 family 都满足 `|Tn(1)| = 1`，
于是在那里读出 `eps_eff`，参考式改成 `10log10(1 + (eps_eff·Tn(Ω))²)`
（butterworth 用 `10log10(1 + Ω^{2n})`）。残差随即降到 **0 或 1e-12 dB**（全阶）。

**代价是 `eps_eff` 会把「所有耦合同比例缩放」整个吸收掉**，所以另外加了一条
**独立**的纹波幅度检查（网络实际纹波 vs 原型 g 值对应的纹波，容差 1e-3 dB）：
形状由闭式检查管，尺度由这条管，两条缺一条都能放过错答案。
实测 `effective_ripple_db`：请求 0.5 → 0.500051，请求 0.1 → 0.100010。

### 带内纹波不能从显示扫描里数

扫描点是给人看曲线的（默认 41 点、span = 3），带内可能只落 4~7 个点，
于是同一个 trisection 滤波器读出 **span=3 → 0.5248 dB**、**span=6 → 0.3492 dB**、
密网格 **0.8643 dB**。所以 `inband` 改走**独立的 401 点密网格**（`IN_BAND_POINTS`），
只扫 `band_lo..band_hi`，输出里报 `sampled_points_in_band`。
收敛性实测 401 / 2001 / 20001 三点一致（0.8643 / 0.8643 / 0.8643）。

### 零点：位置准，深度由 Q 定

2.6 GHz / FBW 5% / 零点 2.86 GHz：`analytic_omega = measured_omega = 3.818181818`，
`omega_error_pct = 0.0`，无损时 `depth_db = 342.6238`（就是个真零，深度只受数值精度限）；
把 `Qu` 降到 150，深度掉到 **59.573 dB**，`depth_limited_by` 会写明是 unloaded Q。

`couplings` 自定义分支额外报诚实字段：若矩阵给出的零点满足 `|Ω| < 1`，
标 `inside_passband = true` 并加 note——那意味着**这不是带通滤波器，是带内挖了一个陷波**，
不能当 feature 报；一条零点都没有时，`why_none` 会提示「可能是没零点，也可能是零点在
扫描跨度之外，把 span 加大再看」。

### 微扰的真实代价：摊开，不修

trisection 的零点参数是按近似给的，实测纹波代价很大：请求 **0.1 dB** → 实际 **0.8643 dB**（8.6×）。
但这**不是病态**：只重调一条 `m12`（0.9192 → 0.9224，其余不动）就能把纹波压回 **0.1017 dB**。
缺的是「吸收零点位移」那一步，属于 Cameron 全合成的范围，本模块**不做**——
它把差距原样报出来，而不是替你调一个数藏起来。

### 其它

- `q_external` 一旦由用户给定，就**跳过**闭式检查：拿原型多项式去判用户自己挑的 Qe，
  等于用他没要的答案判他对错。此时明示 `closed_form_compared: false`。注：
  `q_external` 收的是**绝对 Qe**，内部按 `Qe·FBW` 存。
- `MAX_ORDER = 8`、`MIN_ZERO_OMEGA = 1.2`（太靠近通带的零点会被拒答并说明理由）。
- 36 组 smoke（order 1..6 × 2 family × qu∈{None, 200, 500}）全部 ok、幺正性全 ≤1e-9。
- 测试里**自带第三解**：`tests/test_coupling_matrix.py` 里手写 ABCD 级联
  （`abcd_inverter` / `abcd_shunt` / `abcd_series`）独立算一遍 S21。
  模块的节点解与模块自检同源，没有一路完全不同的线性代数就验不出系统性错误——
  上面那个「内部节点把阻抗翻倍」正是这样被抓住的。

## [0.0.20] - 2026-10-29

「磁件层补上开关频率看得见的那一半」——上一批做完了**导体**损耗（`feedline`），
这一批做**绕组**损耗：新增 `magnetics_winding_ac`，用 Dowell 一维解把 DCR 换成
开关频率处的 `R_ac/R_dc`。工具数 **144 → 145**，用例 1428 → **1451**，
`--selftest` 仍 **95/95**。推导、第二解与读数写在 `docs/27` §27.16。

### 闭式与那条最容易抄错的项

```
F_R = D·(sinh2D + sin2D)/(cosh2D − cos2D)
    + (2/3)(m²−1)·D·(sinhD − sinD)/(coshD + cosD)
D   = h / δ_skin
```

**两项都带 `D`。** 漏掉第二项的 `D` 是最常见的转录错误，而且 `D = 1` 时两种写法
**逐位相同**，所以低频/单层自测完全看不出来。本批第一版就漏了：`D = 8`、`m = 2`
时给出 10 而真值是 24。是被第二解抓出来的。

### 第二解：逐层精确 ODE，而不是再推一遍级数

所有层串联 ⇒ 每层 MMF 落差分相同；第 i 层 H 从 `(i/m)H0` 到 `((i+1)/m)H0`，
层内 `h(x) = Hl·cosh(gx) + B·sinh(gx)`、`g = (1+j)/δ`，损耗 `(1/(2σ))∫|J|²dx` 求积。
**不含 `sinh2D + sin2D` 这一段**，与闭式是不同推导。`m ∈ {1,2,3,4,8}` ×
`D ∈ {0.2,0.5,1,2,4,8}` 共 30 格，最大相对偏差 **< 1e-4**。另加两个解析极限：
`D→0` → 1、`D≫1` → `D(2m²+1)/3`（后者可由表面阻抗 `P = (Rs/2)Σ[(k−1)²+k²]` 独立推出）。

两次失败尝试（一维 FD、传输矩阵）留在 `_scratch/` 作反例：根因是气隙内 `H` 恒定、
界面处 `dH/dx` **允许跳变**（`dH/dx = J`，气隙里 J = 0），所以「给两个边界条件」
必然抹平拐点，得到 1/3 或 1/9 级的错答案；正确约束是「每层落差分相同」。

### 圆线：两个取法差 30 % 以上，不替调用方选

Dowell 的等价箔有两种通行取法 —— 面积等价 `h_eq = (π/4)d²/p`（DC 逐位相同）、
孔隙率修正 `D = (h_eq/δ)·√η`（`η = h_eq/p`）。0.4 mm 线 / 0.5 mm 节距 / 2 层 /
100 kHz：**1.815 与 1.219**。默认两个都返回（`fr_area_equivalent` /
`fr_porosity_corrected` / `round_wire_bracket` / `reported`），`porosity_corrected=true`
才把 `fr` 换成修正式 —— 不是舍入误差就不该由库来选边。

### 温度只经电阻率进入，且两个方向都进测试

`ρ(120 °C) = 2.4015e-8` ⇒ `δ` 从 208.97 涨到 246.64 µm ⇒ `D` 变小 ⇒
**`F_R` 反而降**（1.00465 → 1.0114，同几何）；同时 `R_ac` 本身**升**。
两条断言并列，防止把前者读成「更凉」。

### 八种拒绝 + 一条必须点名的漏项

`ARG_RANGE`（`freq_hz<=0`、`layers<1` 或 `>64`、`wire_diameter_mm>pitch_mm`、
`dc_resistance_ohm<=0`、`turns<1`、`mean_length_mm<=0`）、
`ARG_INVALID`（同时给 `thickness_mm` 与 `wire_diameter_mm`、`shape=foil` 且
`turns≠layers`、同时给 `temperature_c` 与 `resistivity_ohm_m`、未知 `shape`）、
`ARG_MISSING`（`shape=round` 缺 `pitch_mm`、只给 `turns` 或只给 `mean_length_mm`）。

`not_modelled` 里**必须**有点名 `fringing`：有气隙电感里气隙边缘场常是最大的
交流绕组损耗，而 Dowell 的 1-D 场**原理上看不见**它。不写会让 `F_R = 1.02`
被当成「绕组不热」。同列表还含 `litz`（无股间模型）与 `interleaved`（本实现按
连续层叠算，不描述交错绕法带来的场抵消）。

### 落地

- `calc/magnetics.py`：新增 `winding_ac_resistance(...)` + `_dowell_fr(layers, delta)`，
  常量 `COPPER_RESISTIVITY_OHM_M` / `COPPER_TEMPCO_PER_K` / `WINDING_SHAPES`；
  输出 `skin_depth_um` / `equivalent_foil_mm` / `delta_eq` / `fr` / `fr_own_field` /
  `fr_proximity` / `fr_terms_add_up` / `r_ac_over_r_dc` / `reported` / `not_modelled`，
  可选欧姆回算（`dc_resistance_ohm` 直给，或 `turns` + `mean_length_mm` 几何回算）。
- `tools.py`：新增 `magnetics_winding_ac`（注册在 `magnetics_core_loss` 之后，`layers` 用
  `_num` —— 本文件没有 `_int`）；`calc_capabilities` 的 magnetics 段去掉「no AC winding
  resistance」，改写为「Dowell 1-D 不算场解 / 无气隙边缘场 / 无 litz 股模型」。
- `tests/test_winding_ac.py`（新增 23 例）：独立逐层 ODE 解 30 格 < 1e-4、两个解析极限、
  两项相加且 ≥1、对 D 与 m 单调、m=1 时 prox 恰为 0、读数形状、温度两方向、圆线 bracket、
  八种拒绝、工具接线。
- `docs/27` §27.16（含 27.16.1~27.16.5）+ §27.14.8 DCR 条目追加 + §27.15 测试表行与计数。
- `README.md`：工具数 145、目录树注释、工具行、「没有逐行进表」清单。

## [0.0.19] - 2026-10-29

「仿真层的物理深度」——上一批留下的下一个动作：把 `feedline` 的导体损耗从
`Rs/(Z0·w)` 薄电流近似换成**自有 2-D 场解的微扰积分**。工具数不变（**144**），
用例 1413 → **1428**，`--selftest` 仍 **95/95**。推导与实测写在 `docs/16` §13。

### 为什么换：旧式的两个错误方向相反且都不报

`alpha_c = Rs/(Z0·w)` 同时① 假设电流沿宽度均匀（忽略边缘拥挤，**低估**）、
② 假设回流导体与走线等宽（回流面远宽于走线，**高估**）。实测微带 `w/h = 1.75`
时②赢 2 倍以上，所以真值**低于**它。旧 docstring 写「a LOWER bound, typically
20-40 % low on microstrip」——**这句话是错的，已删除**。它既不是上界也不是下界。

### 换成什么：`alpha_c = Rs/(2·Z0)·∮g²ds`

- `calc/field2d.py` 新增 `conductor_shape_factor(kind, w_mm, h_mm, t_mm, er, fine_div,
  gap_h, refine, max_nodes)` + `_surface_charge_faces` + `_section_solution`（粗网格 warm start）。
  `g` = 归一化到**信号导体总电荷**的表面电荷密度（按全导体求和恒为 0 —— Gauss；
  这个坑一开始把结果炸成 1e11）。积分方向是 `Σ q²/(L·Q_live²)`（不是 `Σ(q/Q)²·L`）。
- 副产品自检：`ground_to_live_charge_ratio = -1.0000`（精确），进测试。
- `build(odd=False)`：`odd=True` 会把 `x=0` 整列伪固定成 0 V（电壁），假电荷会算进形状因子。
- `SHAPE_FACTOR_MAX_NODES = 45000` 保险丝，检查在**第一次 SOR 之前**（否则先烧 30 s 再拒）。
  超限则退回 `conductor_model="sheet_bound"`。

### 两条独立验证（都是测试断言）

- **平行板极限**：微带 `sf·w` 随 `w/h` 从 5→100 单调 1.4809 → 1.9644（理论 2）；
  带状线 `w/b` 0.75→25 从 0.6444 → 0.9847（理论 1）。
- **增量电感规则**（齐次带状线）：`sf/inc` 在 `fine_div=24` 时 1.034 / 1.020，收敛到 2–3 %。
- **微带只有第一条**——`t=0` 时条带顶面/侧面无法表达成 `∂Z0/∂h`，实测差 3–5 倍。
  这一点在文档里明写，不假装有第二条。

### 零铜厚：报漂移，不报"收敛值"

`t=0` 的 `∮g²ds` 对数发散。`refine=1.6` 跑两级网格、报 `mesh_drift_pct`
（微带 6.87 %、带状线 4.08 %、带 35 µm 铜后 3.87 %）+ `shape_factor_caveat`。

### 粗糙度：独立乘子

Hammerstad-Jensen `K = 1 + (2/π)·atan(1.4·(Ra/δ)²)`，`δ` = 1 GHz 铜 2.0898 µm。
新参数 `feedline_impedance(..., roughness_um=)`；`Ra = 1 µm` ⇒ `K = 1.1975`，
只乘 `Rs` 不动几何因子（测试断言两次 `sf` 相等而 db 变大）。

### 两条估计并列，不选边

`conductor_db_per_100mm`（场解）+ `conductor_sheet_bound_db_per_100mm`（旧式）
+ `field_over_sheet_ratio`。实测：微带 t=0 **0.202 vs 0.3829，ratio 0.528**；
带 35 µm 铜 + Ra 1 µm **0.273 vs 0.4916，ratio 0.555**；带状线 **0.2004 vs 0.5684，ratio 0.352**。
`shape_factor_split` 按 signal / return / trace_sides 拆（三列相加 = 总数）；
信号导体拿走六成以上，说明"回流面等宽"错得最狠。

### CPW 仍然拒答

共面线回流在同层、电流绕槽边，`field2d` 没有共面截面 ⇒ `conductor_model = "not_computed"`，
总损耗标 `lower_bound` 并写明缺哪一块。套 Gupta 修正不叫完整，叫给错答案。

### 测试

新增 `tests/test_trace_loss.py` **15 项 / 18 s**：平行板单调性、w/b=10 时 `sf·w > 0.93`、
增量电感 ratio 窗口、split 求和对账、`Q_ground/Q_live ≈ -1`、有限铜漂移小于零厚度、
`max_nodes` 前置抛错、两条估计都在且 `total = conductor + dielectric`、t=0 带 caveat、
粗糙度是独立乘子、`ARG_RANGE`、节点超限回退 `sheet_bound`、CPW 仍 `not_computed`。

## [0.0.18] - 2026-10-29

「把审计清单做到底」——0.0.17 之后做了一次只读审计，把**没做的 / 做了但有硬边界 /
验证缺口 / 文档过期**四类逐条列出来，然后照着清单一条条做掉。`TOOLS` 129 → **144**，
用例 **1413** 个（+5 skip），`--selftest` 87 → **95/95**。设计口径逐条写在 `docs/27` §27.14。

### 制造输出：交给裁判，然后自己再读一遍

- **`fab/`（新包）+ 4 个工具**：`board_fab_package`（Gerber + Excellon + 位置文件 + gbrjob，
  可选 zip 与 `manifest.json`）、`board_export`（Gerber / STEP / PDF）、`board_bom`、`board_netlist`、
  `board_graphics`。Gerber 由板厂的 CAM 判，所以**只外包不重算**：`kicad-cli` 在临时目录里跑
  （`cwd` 在 temp、输出全是绝对路径，永远不可能写在用户工程旁边），然后**把每个产物重新读一遍**
  查那条让它可用的性质——Gerber 不以 `M02*` 结尾、Excellon 没有工具表、位置文件没有行、
  STEP 不是 `ISO-10303-21`，一律判 **failed**。实测 `_scratch/core`：**15** 个产物 /
  **103 923** 字节 / `fab.zip` **33 577** 字节 / 9 层 Gerber / 2 个钻孔 / 位置表 **32** 行，
  每层铜 `flashes=51`、`x2_attributes=true`、`problems=[]`。
- `OUT_DIR_BESIDE_SOURCE` 是**拒绝**不是警告：`out_dir` 落在源目录里，除非显式
  `allow_beside_source=true`，直接拒。
- 缺 `kicad-cli` ⇒ `KICAD_CLI_NOT_FOUND`，不回退到自研 writer。

### 阻塞物账本、运行账本、BGA 扇出

- **`route/blockers.py` + `board_blockers`**：`no_path` 是个没用的答案。给的是挡路对象的账本，
  每个对象带一条**结构性**标注（pad `movable` / track·via `reroutable` / zone·pour·keepout·edge·graphic
  `fixed`）——是「它要花什么代价让开」，**不是本模块替你做决定**。八扇区用布线器**同一个精确线段测试**
  探，报哪些扇区是开的：「四面被围」和「三面开着但搜索还是失败」是两种诊断。自检读数：
  5×5 球阵 / 1.27 mm / 一侧 0.4 mm 墙 ⇒ `open_sectors` `['E','NE','N','NW','SW','S','SE']` 七开一关。
- **`ledger.py` + `board_run_ledger`**：一行一条 JSON，按板文件 sha256 归键。四条规矩写进模块文档串：
  **永不改变答案**、**不是缓存**、**不存它不需要的设计数据**、**默认开、一个开关关**（`PCB_AGENT_LEDGER=0`）。
  超 4 MiB 只轮转一次，**不删**。
- **`fanout.py` + `board_fanout`**：BGA 扇出是**排序问题**不是布线问题。每个球先试朝外短桩再试
  四次旋转（≤90°），每个候选都用布线器同一套精确几何判，接受的桩与过孔**真的进栅格**，
  下一个球必须绕着已存在的铜出。不做的写清楚：不做 via-in-pad、不回拆已接受的出口、
  不声称完整扇出（`skipped` 就是留在里面的球）。自检：1.27 mm/5×5 **16 直出 + 9 狗骨**；
  1.0 mm 间距 **0** 个能出。

### 原理图 ↔ PCB 同步

- **`sync/`（新包）+ `schematic_pcb_diff` / `schematic_pcb_sync`**：KiCad 的「Update PCB from
  Schematic」没有可脚本化的等价物，于是从两边已有的权威重建——原理图侧用 KiCad 自己的网表器
  （`kicad-cli sch export netlist --format kicadsexpr`），板侧用同一个 `Editor` 读文件里的 pad net。
  差集是**逐脚**的（「C7 的 2 脚在原理图上是 GND，板上没有网」能动手；「GND 网不一样」不能）。
  缺的封装在板上没有位置 ⇒ 只报 `needs_placement`，调用方给位置才变 `add_footprint`——**没有诚实的默认值**。
  `sync` 一个字节都不写，只产出 op 列表交给正常的 propose/apply。

### 全波 EM：这条接缝的价值在于它拒绝的样子

- **`calc/em.py` + `fullwave_solver`**：照抄 `calc/spice.py` 对 ngspice 的两条纪律——
  **点名要一个没装的求解器是错误**（`SOLVER_NOT_FOUND`），不是悄悄给一套闭式数当答案；
  **二进制在场不是「它能用」的证据**，每条答案带 `verified_here: false`，`VERSION_VERIFICATION`
  明写「本仓库从未运行过任何全波求解器」。探测 openEMS / Meep / gprMAX 的位置、版本与
  `handoff`（它要你给什么），**不生成**网格、端口或几何文件。

### 计算层五处补强

- **`cavity_plane_q`**：`Qc = h/δs`。1.6 mm / 1 GHz / σ=5.8e7 ⇒ **765.6**，与 a、b、m、n **都无关**
  （Qc 不是几何的函数，几何只决定频率）。场分布与侧壁开口失谐仍要全波，写进 `not_computed`。
- **`fusing_current`**（Onderdonk）：`area_mil² = (w/0.0254)·(t_um/25.4)`；10 mil × 1 oz / 1 s / 25 °C
  ⇒ **2.552 A**。这是**一根线的热平衡**——没有周围铜、没有基材导热、没有镀层，所以它是**另一个问题**，
  不能替代 IPC-2152 的稳态载流。
- **`magnetics_dcm`**：`K = 2Lfs/R`，boost `D = √(K·M(M−1))`；CCM 边界负载 boost
  `Vin²D/(2Lfs·Vout)`，**边界负载 ∝ 1/L**（电感减小 ⇒ 边界负载**升高**）。
- **`magnetics_core_loss`**：Steinmetz `Pv = k·f^α·B^β`，`k/α/β` **由调用方给**并写清单位约定——
  同一牌号被两家拟合出两组三元组，内建材料表等于替调用方选了一家。
- **`magnetics_dcr`**：每匝平均长度 × 匝数 × 电阻率/截面，**只有 DC 部分**（无趋肤、无邻近、无端接与焊点）。

### 滤波器：偶数阶不是 50Ω 网络，损耗不是公式，良率不是数点

- **偶数阶**：`n=2/4` 一开始全场 `INTERNAL_CHECK_FAILED`（2.055 / 2.97 dB）。排除三种猜想后落在原型本身：
  Chebyshev 偶数阶 `g(n+1) ≠ 1`（n=2/0.5 dB：`[1.402945, 0.707084, 1.984127]`），两端接 z0 **不是原型网络**。
  正确形态是**串联起始 + `1:√(g(n+1))` 理想输出变压器**（等价：并联起始且 `load = z0/g(n+1)`）。
  改完 `|Ω|≤3` 内吻合 **0.0005 dB**。变压器写进 `value.termination`——藏进元件值会让这组 L/C
  看起来像 50Ω 滤波器。
- **有限 Q**（`lumped_bandpass(q_unloaded=…)`）：损耗**不经公式**，每个分支多一只电阻
  （串联 `ω0L/Q`、并联 `ω0C/G`），响应仍由 ABCD 级联算；另用 Matthaei
  `IL₀ ≈ 4.343·Σg_k(k=1..n)/(Qu·FBW)`（**只到 `g_n`**）做独立第二推导，只当门限（偏差 > 50 % 且
  `est < 3 dB` ⇒ 拒答），专抓「损耗电阻放错臂」的 2 倍错。实测吻合 **−0.9 % ~ −6.3 %**。
- **功率审计**：一条与 ABCD 无关的路径（逐节传播电压电流）检查
  `P_load + ΣP_loss = P_incident·(1−|S11|²)`。第一版把源内阻也加进去，得到恰好 **2 倍**的假不平衡
  ——恒等式里源内阻在参考面外。相对比较的分母加地板 `max(|want|, 1e-6)`，否则 n=5 的 −200 dB
  深零点会把抵消噪声判成不一致。
- **3 dB 带宽相对带内最小 IL 量**；即便如此 `Ql` 随 Qu 下降**略升**（200→8.72、100→8.89、80→8.98、
  50→9.23），因为损耗按储能加权。写进 `loaded_q_caveat`：**这是测量值，不是 `1/(1/Qe+1/Qu)`**。
- **寄生**（`parasitics_cp_f` / `parasitics_esl_h`）：L 并 cp、C 串 esl，只会让响应变差。
  3 阶/1 GHz/10 % 的 87 nH 串联电感配 0.5 pF ⇒ SRF **762 MHz < f0**，IL 101 dB，
  触发 `srf_below_3f0` +「响应就是答复，但这颗元件不存在」。
- **`filter_mc_yield`（新工具）**：每元素独立乘子、同种子同答案；双零容差 ⇒ **退化**（构造上 100 %，
  并明说）；标称已不达标 ⇒ 给「这不是容差良率」的文案；良率必带**二项 95 % 半宽**；
  `assumptions` 写死 uniform + 独立 = **最坏情况**（无相关性、无温度、无老化、无批次偏移）。
- **仍未做**：`coupled_resonator` / `waveguide_cavity` 的有限 Q、温度/老化/相关容差、掩模全带扫描。

### 其它

- **`edit/ops.py`**：盲/埋孔层对（`add_via` 不再假定 F.Cu↔B.Cu）、`add_via_in_pad`（先判 pad 形状
  与尺寸够不够放环）、`add_teardrop`（泪滴是**铜**，必须继承 pad 的 net，长度短于焊盘自身半长时拒绝）。
  新增 op **不许有旁路**，仍走同一条五道闸门。
- **`docs/27` §27.14** 新增一节，按「卡点在哪里」组织；§27.15 是测试与门禁。
- **四处文档过期订正**：`docs/17` 的「IPC-2152 / IEC 爬电仍然 fail-closed」「无平面谐振」「无熔断判据」
  三项都已不成立（改成现状并写清各自仍缺什么）；`docs/07` §8「不装 KiCad」已推翻（KiCad 从 `0.0.1`
  起就是**裁判**不是依赖）；`docs/19` 有两个 `## 19.9` 和两个 `## 19.10`（后加的应力层复用号），
  改为 `19.11 / 19.12` 并同步两处引用；`README` / `docs/25` 的磁学与 `schematic_review` 表述。

## [0.0.17] - 2026-10-29

「把 0.0.16 停下的三个边界推到底」——铺铜从「知道过期」变成「会重填」、多端网从「只有 Prim MST」
变成「可要求 Steiner」、弧从「固定 7.5°」变成「绝对弦差」。四件事的卡点都不在算法上，而在
**引擎读不读得动我们的文件 / 省的是哪一段长度 / 精度问的是角度还是弦差 / 进程里有没有文档上下文**。
逐条口径见 `docs/27` §27.13。

- **铺铜自动重填**（`board_route(refill_pours=True)`，**默认 false**）：先问 `_pour_report` 有没有
  过期（没有就 `refill.status="nothing_stale"`，**不调引擎**），有过期才委派 KiCad 的 `ZONE_FILLER`
  并以字节保真方式把新多边形移植回**我们这份**文件（不拿 pcbnew 存的板整体替换）。
- **引擎挂死的真因是「输入形态」不是「填充慢」**：`pcbnew.LoadBoard` 在「我们写出的 + 带
  `filled_polygon` 的板」上挂死 300 s（`faulthandler` 栈停在 `pcbnew.py:9896 in LoadBoard`），
  同一文件 `kicad-cli` 读得动。逐个排除到只剩这一个组合后改成 **`fill._without_previous_fill()`：
  交给引擎之前把每个 zone 的 `filled_polygon` 剥掉**——因为它是 filler 的**输出**不是输入。
  重填从 `TIMEOUT` 变 **0.6–1.4 s `status="refilled"`**，铜面积 1044.468 → **1023.912 mm²**
  （新走线把铜挤开）。剥了几个写进 `value.stale_polygons_dropped_before_the_engine`，
  `assumptions` 里同时写 `engine_saw_no_stale_polygons` 与理由。
- **移植的铜与 `kicad-cli` 的铜是同一块**（控制臂实测）：两边都是 1 个多边形 / 252 顶点 /
  点集完全相同 / 面积 1023.912 mm²，`identical=True`。第一版控制臂拿 pre_route（无轨道）的板去比，
  得到 `identical=False` —— 那比的是两块不同的板，已改正。
- **同一份铜两个 DRC 读数是版式代际差，不是几何差**：我们的 refilled（`version 20221018`）报 5 条
  （`clearance 3` + `text_height 2`），`kicad-cli --refill-zones --save-board` 存过的报 2 条。
  差的 3 条全是 `间距 0.3500 / 实际 0.2255`，而 `0.2255 = 0.35 − min_thickness(0.25)/2`。
  排除矩阵（min_thickness→0、换 zone 节点、逐节点反向替换、升 version、`--save-board` 不 `--refill-zones`）
  指向唯一解释：**pre-v7 把 `filled_polygon` 读作 `min_thickness` 宽描边的中心线，DRC 量描边外沿**。
  处理方式是把事实写进 `assumptions.format_generation`（`file_version` / `stroke_halfwidth_mm` /
  `what_is_measured` / `why` / 三条 `remedy`），**不偷偷改几何、不偷偷升 version**。
- **多端网拓扑**（`route/topology.py`，新文件）：`mst` 与 `steiner`（iterated 1-Steiner on Hanan grid，
  候选点上限 400、超限置 `hanan_truncated`）。实测经典星形 `length_mm` 6.8284（`mst`，历史欧氏口径）→
  `steiner` 的 `mst_length_mm=8.0` / `length_mm=6.0` / `saved_mm=2.0`；五点 37.0711 → **30.0**
  （`saved_mm=10.0`，Steiner 点 `(0,5)` / `(10,5)`）。**默认仍是 `mst`**（零行为变化），
  Steiner 每个分支点多一次 A*；分支点落网格外→整网回退 mst 并写 `topology_note`；
  非法值 `ARG_INVALID`。三条 NOT 写在文件头：不是精确 RSMT / 报的不是实际铜长 / 每个分支点要额外 A*。
- **弧近似改成绝对弦差**：`geometry.arc_to_points(..., chord_tol_mm=)`，`θ_max = 2·acos(1 − tol/r)`、
  段数夹 4~720；`adapters/kicad_pcb.py` 三处调用点（三点定圆 / legacy `(start center)(end)(angle)` /
  `fp/gr arc`）统一 `_ARC_CHORD_TOL = 0.001`（1 µm）。默认 7.5°/段的误差与半径成正比（r=1 → 1.82 µm、
  r=5 → 9.12/10.27 µm），改成 1 µm 弦差后 r=1/5/0.5 分别是 n=18/40/4、误差 0.00095/0.00096/0.00048 mm。
  理由：焊盘边缘的多边形近似不该成为 clearance 判定的分歧源。
- **立创 Pro 写路径的阻塞被钉清**：桥可达（ping / invoke / `doc.api` 全通），但同时刻 `tabs` 只有
  Start Page、`getAllProjectsUuid()` 空、`getAllPcbsInfo()` `null`、`openDocument(...)` 回 `null`、
  `openProject(...)` 30 s 超时 ⇒ **没有文档上下文，一切写方法都没有作用对象**。`createPcb()`
  造游离 PCB 会进用户在线工作区，**故意不做**。`verified_against_app` 仍为 `false`，但理由从
  「没验」换成「当前应用状态下令它不可达」。
- **门禁**：`test_route.py` 66 → **76** 例 / 22 类（新增 `TestARefillCanBeAskedFor` 4 例、
  `TestABranchPointIsNotAPad` 6 例）；`--selftest` 87 项不变。

## [0.0.16] - 2026-10-29

「把 0.0.15 的 §27.10 那张欠账清单逐条做完」——九条加上一条新欠账。每一节都分开写**机制**与
**证据**，凡是只有单测的就写明只有单测；「测试绿了」不等于「真板上量过」。逐条口径见
`docs/27` §27.12。

- **差分对真耦合**（`route/couple.py`）：第二条网变成第一条网链的逐段偏移副本（`gap + width`，
  miter ≤ 3w），每段仍走一次 `seg_blockers` 精确复检；三条拒绝点名理由
  （`pair_needs_two_pads_each` / `offset_blocked` / `fanout_too_long`），并新增 `coupled_fraction`
  让 `coupled: true` 有数支撑。真板：`SWDIO/SWCLK` 各 3 焊盘被拒，`PA8/PA9` 因 `U1.41` 挡路被拒
  （`needed 0.2 / actual 0.1601 mm`）——**同一对 `couple=false` 时读数一模一样**，所以那个
  `gap_achieved_mm` 从来不是耦合的证据。
- **回流路径**（`route/return_path.py`）：换层的信号过孔配一颗地过孔（2 mm 内、0.6 mm 内已有则不加、
  必须落在地铜上），`_GROUND_NAMES` 故意不含 `AGND`。真板整块板一轮：90 颗过孔 / 74 颗信号过孔，
  **缝了 11 颗**（距离 0.93–1.97 mm，全部落在两侧都有地铜的位置），跳过 63 颗（`ground_not_on_two_layers`）；
  apply 五道闸门全过、`kicad-cli pcb drc` 的 `new_blocking_total = 0`（新 advisory 24，其中一条
  `via_dangling` —— 布通 41 网不等于都接上了，这条不藏）。单层网仍如实回 `nothing_to_stitch`。
- **阻抗目标进搜索**（`calc/impedance.py` + `rules.impedance_targets`）：ohm → `track_mm`，
  叠层不明（`IMPEDANCE_NEEDS_STACKUP`）与不可达（`IMPEDANCE_UNREACHABLE`）两种拒绝。
  真板 50 Ω：叠层自动取 h=0.5100 ⇒ 0.92177 mm；给定 h=0.2 ⇒ 0.33488 mm；200 Ω ⇒ 不可达。
- **爬电自动查表**（`calc/iec60664.py`，IEC 60664-1:2020-05 Table F.5）：`working_voltage_v` /
  `pollution_degree` / `material_group` / `insulation` / `coating` / `altitude_m`，
  与调用方给的 `creepage_mm` **取严**、两个来源都进 `sources["creepage"]`。PD4 / 超表尾 / 非整数污染
  等级一律拒绝而不外推。真板 250 V / PD2 / IIIa / basic ⇒ 2.5 mm。
- **拥塞协商**（`router._wish/_price/_negotiate/_reconcile` + `astar(soft=)`）：需求驱动 + 软代价
  （`SOFT_BLOCK=0.5`、`WISH_BUDGET=40000`）+ 只收最好一轮（`best_round` / `reverted_to_best`）。
  `board_route` 新增 `congestion_rounds`（默认 **0 = 关**）/ `congestion_max_rips` / `congestion_weight`。
  真板：38/50 442.9 s → **41/50** 713.0 s（+61%），round 2 把 41 掉回 36 被 `best_round` 撤回。
  **41 不是 38 的超集**（新通 8 条、新失败 5 条），所以只能主张「多了 3 条」。
- **连接意图**（`route/intent.py` + 新工具 `board_intent`）：一份对象、三个消费者
  （`board_route` / `board_routability` / `board_intent`），未知键 fail-closed 连词表一起报、
  caller 赢但每个不一致的键进 `conflicts`、不发明数字。板级检查分两档：keepout 吞焊盘 / 区域出板 /
  ref 不存在 = 拦，「footprint 还没搬进 region」= 只报。
- **蛇形先挑宿主**（`route/tune.py`）：`_candidate_order` 按「能买多少长度」打分，端点过孔 ×0.25、
  中点 2 mm 内有别人铜 ×0.25；`AMPLITUDE_LADDER` 把「配不上」变成「配上了，振幅 0.9 mm」，
  报告新增 `amplitude_ladder_mm / narrowed / hosts_considered`。
- **异形焊盘 = 全部图元**：`gr_poly/gr_rect/gr_circle/gr_line/gr_arc` 全收集，多图元取凸包
  （`outline: convex_hull_of_primitives`），表达不了的 kind 进 `ignored_primitives`。
  **单图元必须原样保留**——全量门禁抓到 `pic_programmer JP1.2` 是凹的五点多边形，凸包把缺口填成铜，
  pcbnew `HitTest` 与我们在焊盘自己的铜里判反；修后 demo 板逐点比对零分歧。
- **新铜让铺铜过期**（`router._pour_report`）：报 `needs_refill` 与每块 zone 的
  `segments_inside / same_net_as_the_new_copper`，**不自己重填**。
- **立创 Pro 写路径**（新工具 `lceda_pcb_write`，第 129 个）：probe 纪律而不是合成 snippet——
  无 probe 不发（`ARG_REQUIRED`）、前后一致即 `WRITE_HAD_NO_EFFECT`、`save` 只回 `no_error`，
  每条答案 `verified_against_app: false` 并列 `not_verified`。`_canon` 把整数与同值浮点视作同一读数，
  但只对 `abs(int) <= 2**53` 生效（更大的 id 圆成 double 会把「变了」读成「没变」）。
- **门禁**：全量 **1111 例全绿**（`Ran 1111 tests in 304.779s`, `OK (skipped=5)`；0.0.15 为 1061 例，
  本轮 +50 例）。新增的类：意图 9 / 蛇形宿主 3 / 铺铜过期 3 / 异形焊盘 3 / 阻抗 7 / 爬电查表 5 /
  真耦合 4 / 换层地过孔 1 / 立创写路径 11，另有若干落在既有类里的用例；逐类清单见
  `docs/27` §27.12.11。

## [0.0.15] - 2026-10-29

「把 `kicad-free` 与 `rf-circuit-design` 里那几条**走线规则**真的变成搜索里的约束」——
爬电距离、差分对间距、等长。三条规则的形状不同，所以落地方式也不同，但共同的硬约束只有一条：
**数只能有一个来源，且必须写明是谁给的；没给就是没施加，绝不默许一个数**。

- **爬电距离（只认调用方）**：`board_route` 新增 `creepage_mm` / `creepage_pairs`
  （`'A:B:1.0,C:D:0.8'`）。没给值就写进 `sources["creepage"] =
  "not supplied: creepage comes from a standard table keyed to working voltage / pollution degree /
  coating and is never invented here"` 且**不进 `assumed`**（依据 `kicad-free` 的「不编造
  安全间距」）；非正数 / 非数字 / 坏串 / 对里两网相同 ⇒ `RULES_INVALID`。给了值就**同时**进两处：
  栅格取 `spacing = max(creepage_max, spacing_max)` 并顶掉 `clr`（悲观、全板让路，并在 `notes` 公示），
  精确判定用 `pair_spacing = max(pair_clearance, creepage, diff_gap)`（每对各自的数）。
- **差分对（只做间距，不耦合）**：新增 `diff_pairs`，每项 `{p, n, gap_mm, width_mm?, max_skew_mm?}`。
  `gap_mm` 进上述最小间距；`width_mm` 覆盖两成员宽度（对内同宽，其他网不变）；`max_skew_mm`
  让这一对**自动**成为一个等长组。报告给 `gap_declared_mm / spacing_required_mm / gap_achieved_mm /
  length_p_mm / length_n_mm / skew_mm / coupled: false`——两条网是各自独立布的，不肩并肩跑，
  实测声明 0.2 mm 的一对 `gap_achieved_mm = 1.65 mm`、状态 `pair_not_coupled`。
- **等长（后处理，不是搜索约束）**：新增 `length_groups`（`{name?, nets, target_mm?, max_skew_mm?,
  amplitude_mm?}`）与第四个模块 `route/tune.py`。连续 45° 三角蛇形（腿距 `max(3w, 0.5 mm)`，
  一整齿 `2A(√2−1)`，一段最多 `0.828·(L−0.5)/2`），**末齿允许更矮**（落到目标而不是下一个整齿），
  **跨多段分配**，每次改铜前用 `seg_blockers` 逐段精确复检（任一段被拒就整次放弃、一个点不写），
  成功后原位替换 `ops` 并 `em.rebuild()` 重算地图。`status ∈ {matched, length_not_matched, reported}`，
  「没配上」也报出真实可达数（`no_segment_long_enough` / `blocked`）。
- **真板验证**：F401 核心板 `SWDIO/SWCLK`，`layers=["F.Cu"]` 禁换层，plan 9.2–10.5 s / `ops=61` /
  `failed=[]`；等长组 `matched`，skew **9.7782 → 0.4998 mm**；端到端 `apply` 五道闸门全过，
  `kicad-cli pcb drc` 69 条（new_blocking 0 / new_advisory 4 条 silkscreen）。
  另留一条真结论：`creepage_mm=0.4` 下同板三条网全 `terminal_unreachable`（`needed 0.4 / actual 0.2189`）
  ——那颗 MCU 是 0.5 mm 间距，这是物理事实，不是 bug。
- **文档**：`docs/27` 新增 §27.11（三条规则怎么进搜索、`coupled: false` 到底承诺了什么），
  §27.10 诚实清单同步改写；测试 16 → 30 个用例（新增 4 个类）。

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
