# Changelog

版本号遵循 [SemVer](https://semver.org/lang/zh-CN/)；`0.x` 期间 tool 的入参与返回结构仍可能变动，
但**信封契约（`status` / `basis` / `precision` / `reason_code`）从 0.1.0 起冻结**。

## [0.6.0] - 2026-10-28

**原理图进写路径，裁判从 DRC 换成网表 + ERC**。诚实清单里「不写原理图。两个 EDA 的原理图都不在
写路径上」这一条收口为：KiCad `.kicad_sch` 能写，立创 `.esch` **明确拒写**并说明原因。
详见 [docs/14](docs/14-原理图写路径.md)。

### Added

- **`fidelity/kicad_sch.py`**（新增）：整棵 `(kicad_sch ...)` 文档——`root()` / `lib_symbols()` /
  `symbol_entry()` / `symbols()` / `project()` / `by_uuid()` / `stats()`，逐字节往返自证。
- **`edit/sch_ops.py`**（新增）：**18 个原理图 op**——`add_wire` / `add_bus` / `add_bus_entry` /
  `add_junction` / `add_no_connect` / `add_label` / `add_global_label` /
  `add_hierarchical_label` / `add_netclass_flag` / `add_text` / `add_polyline` / `add_rectangle` /
  `add_symbol` / `set_symbol_field` / `set_field_position` / `move_symbol` / `delete_symbol` /
  `delete_object`，每条带自己的后置条件与 `coordinates` 说明。
- **`edit/sch_plan.py`**（新增）：`digest` / `netlist_diff` / `erc_diff` / `summary` / `view` /
  `propose` / `apply_plan`。四道闸门：`bytes_written`、`token_identity`、`postconditions`、
  `engine`（KiCad 网表 + ERC 双向对比）。
- **`engine.sch_erc()` / `engine.sch_netlist()`**：`kicad-cli sch erc --format json` 与
  `kicad-cli sch export netlist --format kicadsexpr` 的封装；ERC 的 `sheets[].violations`
  没有 `sheet` 字段，扁平化时补上。
- **5 个 tool**：`schematic_ops` / `schematic_propose` / `schematic_apply` /
  `schematic_summary` / `schematic_view`（工具总数 36 → 41）。
- **`tests/test_sch_edit.py`**（新增，42 例）：逐字节往返、op 级语义、propose 不写盘、apply 只增行、
  回滚、容器类型识别（zip / SQLite / 板 / 其它文本），加 4 个 `kicad-cli` 真图用例。
- **`docs/14-原理图写路径.md`**。

### Changed

- **原理图 op 用文件坐标系（mm，Y 向下，不取反）**。板路径是 Y 向上；取反会把整张图镜像，
  而 KiCad 不报错。每个返回重复 `frame` 说明。
- **诚实清单第 5 条重写**：从「不写原理图」改为「KiCad 能写、立创不写、单张图纸」。
  `calc_capabilities` 的 `not_implemented_yet` 同步收窄到 `.esch` 一条，并新增
  `layers.write_schematic` 与 `verified_against.schematic`。
- **自检 25 → 29 项**：加入 `schematic_propose` 不写盘、`schematic_apply` 三闸门、
  `schematic_view` 读回对象、立创被拒四种。

### Fixed（都是真图抓出来的，不在合成样本上）

- **元件值改动曾被判成"网表变了"并拦住合法编辑**。网表里每个元件是 `(ref, value, footprint)`，
  改 value 自然让记录变化。现 `netlist_diff.changed` 只认**网**与**元件增删**，值/封装变化单列
  `metadata_changed` 只给 warning。`#PWR` 改 Value 会改电源网名——那仍然被拦，单独有用例。
- **同一条 ERC 违规被算成"新增 + 解决"**。KiCad 会把同一条 `net_not_bus_member` 在不同次运行里
  指向该违规的不同 item，坐标随之变化。现违规身份 = `(sheet, type, severity, description)`，
  位置只报不判；计数上升仍会抓到，单纯移动不再伪装成新增。

## [0.5.0] - 2026-10-28

**立创侧从「按格式文档推」改成「在 194 个真实 `.epro` 上测」**。核心是三条被真板子推翻的假设：
一次保存不是"新增成员 + 改写 `project.json`"而是**原地重写一个成员**；`RULE` 标签写着 `"mm"`
而数字**恒为 mil**；`PAD_NET` 第 5 字段不是序号而是**封装内 PAD 对象的 id**。
详见 [docs/13](docs/13-立创实测-真实存储与记录格式.md)。

### Changed

- **`.epro` 写路径整体换形状**：由「append-only 日志 + ticket 递增 + 追加成员」改为
  **「只重写点名的那一个 ZIP 成员」**，另用六道闸门证明其余 129 个成员与未点名的行字节未变。
  `plan.py` 里那句「立创按 ticket 解析追加日志」的注释已改为实测结论并指向 docs/13。
- **`edit/epro_edit.py` 六个 op 全部落地并端到端跑通**（propose 不写盘 → 六门禁全绿 → apply →
  重新解析后事实成立）：`move_component`（连带平移落在锚点上的 `ATTR`）、`set_track`、`set_via`、
  `set_text`、`set_pad_net`、`delete_record`。
- **层表不再预置**：`_context()` 的 `layer_by_code` / `layer_by_name` 全部来自本文件自己的
  `LAYER` 记录，不再带一张编造的默认层表。
- **`board_ops` 的 `.epro` op 目录 3 → 6 个**；`tools.py` 的 `not_implemented_yet` /
  `verified_against` / `epro_inspect` 描述、`board_ops` 的 mm 入参 / mil 存储说明全部改成实测口径。
- **`edit/__init__.py`**：`detect_format` 认 `SQLite format 3`（`.eprj`/`.eprj2`）并按只读工程处理；
  `propose` 的格式判定改写为两行判断，去掉优先级歧义。

### Added

- **`fidelity/lceda.py`**（新增）：立创记录流。`MM_PER_COORD=0.0254`（mil→mm）、
  `Line(body, term, record, parsed)`、`Document`、`Member`（`to_text` / `to_bytes` /
  `is_verbatim` / `set_record` / `remove_line` / `roundtrip` / `tag_layout` / `stats` /
  本轮新增 `dominant_term` / `append_record`）。
- **`fidelity/eproj_doc.py`**（新增）：SQLite 工程只读打开（`documents` / `coppers` / `projects` /
  `branches` …），明确**拒写**。
- **`tests/_lceda_fixtures.py`、`tests/test_lceda_records.py`（18 例）、
  `tests/test_epro_edit.py`（重写，37 例）**。
- **`docs/13-立创实测-真实存储与记录格式.md`**：保存行为逐成员比对、单位证明表、各 tag 的字段
  布局与 arity、`POUR[5]`/`FILL[5]`/`REGION[4]` 是标志位、板级自由 `PAD`、`PAD_NET` 8159/8149
  统计、SQLite schema 与拒写理由、逐字节重打包、几何口径强弱表（旋转 54.1%、底面镜像 35.1%）。
- **`tracks[]` / `vias[]` / `source["texts"][]` 补 `id`**：否则后置条件无法从适配器侧回查该记录。
- **`free_pads`**：板级 `PAD`（271 条、无任何字段指向 `COMPONENT`）单列，不再混进封装焊盘。
- `run_server.py --selftest` 新增 2 项（`board_ops` 广告 6 个 epro op；非归档文件被
  `FORMAT_NOT_SUPPORTED` 拒绝），共 **25/25 PASS**。

### Fixed

- **`epro_doc.to_bytes` 的 central directory 偏移**：读的是 index 19（`char_encoding_flag`），
  应为 16（`disk_number_start`）——错读会让追加成员后整包 CRC 计算位置偏掉。
- **`_contours` 重复顶点**、**`REGION` 的 layer/flag 下标**、**`Board.mark("free_pads")` 未注册**。
- **`set_pad_net` 的追加回调是死代码**（`ctx["appended"]` 从未被执行，`allow_new_record=True`
  实际什么也没写）→ 改为 op 内直接 `member.append_record(...)`；封装焊盘 id 由
  `FOOTPRINT/<uuid>.efoo` 读出，读不到抛 `PAD_OBJECT_UNKNOWN` / `PAD_NOT_FOUND`，不猜。
- **`rows_untouched` 按下标逐行比对**会被"被改的行当然不同"和"删一行导致后面全部平移"误判
  → 换成内容级 `_line_diff`（多重集 + 保序校验）与 plan 自报的 `touches` 集合。
- **`set_text` / `delete_record` 缺后置条件**（`ok=None` → `reparsed` 门禁恒红）→ 补两分支 +
  `_record_present`；`_designator` 从空桩改成真实现（读 `ATTR` 的 `Designator`）。
- **`lines_deleted` 用 set 去重**（删 6 行报 5）→ 新增未去重的 `_planned_counts`。

## [0.4.0] - 2026-10-27

**写路径**：从“只能读”变成“能改，且改得可证”。两种格式两条实现，一套协议：

```
board_propose(path, operations)  →  只算，一个字节都不写；plan 单次使用，1h 过期
board_apply(plan_id, confirm=true) →  checkpoint → 原子写 → 闸门 → 任一不过就回滚
```

### Added

- **KiCad 写路径 `edit/`**（新增）
  - `build.py`：S 表达式构造器（按文件版本写层号：v7 的 `B.Cu=31`、`Edge.Cuts=44`，
    不是 v10 的 2/25——这份表是用 `kicad-cli pcb upgrade` 对读出来的）。
  - `ops.py`：**24 个 op**——板框（矩形/多边形/圆/线/弧）、**开槽**、**挖空**（矩形/圆/多边形）、
    机械层图形、板上文字、移动元件、DNP、走线/删线/改宽、过孔、keepout、zone、网络、netclass、
    板厚。每个 op 自带 `verify()`（后置条件从盘上文件读回来）。
  - `checks.py`：板框闭合测量（开口端点/缺口宽度/连通分量）、越界报告、铜间距、改前改后 digest。
  - `plan.py`：`propose` / `apply_plan` / `create_board` / checkpoint（`~/.pcb_agent_mcp/checkpoints`）
    / `list` / `find` / `restore`。
  - `fsutil.py`：同卷临时文件 + fsync + `os.replace` 的原子写；sha256；home 目录。
- **五道闸门**（每一道都不信任写代码本身）：`bytes_written`、`token_identity`（重新解析盘上文件 →
  逐原子重现计划的树）、`postconditions`（每条 op 自己的承诺）、`engine`（kicad-cli 比对
  stats/Gerber/贴片坐标）、`drc`（**差集**：新增违规就拦）。防假绿两条：“没比较成”不算通过；
  “只改了元信息却动了铜”判静默损坏。
- **立创 `.epro` 写路径 `edit/epro_edit.py`**（新增）：追加式日志——改 = 追加一行（旧字节必须是新字节的
  前缀）、ticket 必须高于成员内所有 ticket（过小会被应用静默忽略）、其它 ZIP 成员逐字节不变、
  `key_inventory` 先验键名（`KEY_NOT_IN_FILE` 并列出真实存在的键）、`unit_verified: false` 如实报。
  三个 op：`set_fields` / `add_object` / `delete_object`。
- **6 个新 tool**：`board_ops`、`board_create`、`board_propose`、`board_apply`、`board_checkpoints`、
  `board_restore`（共 36）。格式按**内容**判（`PK` = `.epro`），文件名从不被信任。
- `examples/build_demo_board.py`：自造一块 60×40 四层板（机械层文字 → 槽+圆孔 → 网络/过孔/闭合走线环/
  keepout/铺铜），每段 `propose → apply`，产物 + `build-report.json` 落盘。
- `docs/12-写路径-提案到落盘.md`：协议、五道闸门、以及本轮拿真机学到的 DRC 事实。

### 本轮被闸门拦下的真实错误（都是设计生效，不是 bug）

- 丝印文字压板边：`gr_text` 以 `at` 点为中心、宽度≈字符数×高度×1.1，12 字符×1.6 mm 放在 x=4 就越过 x=0。
- `track_dangling` ×2 与 `via_dangling`：**铺铜的填充在 DRC 里不算连通性**，端点落在 zone 内的过孔照样算悬空。
  改成 F/B 两条同网走线成环（2 过孔）后 0 违规。
- engine gate 假绿：checkpoint 存成 `.board` 让 kicad-cli 报 `Unknown file type`，三个比较全 skipped 而
  `identical=None` 被当成绿灯→ 现在“没比较成”不算通过。

## [0.3.0] - 2026-10-27

**方向纠正**：中间层改成"等于那张板"，不再"推导出那张板"。新增保真层（`fidelity/`），
并把"厂商引擎说了算"变成可执行事实（`kicad-cli` 当裁判）。

起因是一句纠正：*"如果只是推导那还是猜还是会犯错，我要的是 pcb 的信息转换成 AI 擅长处理的"*。
旧设计（`docs/01` 的"解析 5%、推事实 95%"、`canonical.py` 的"只存离散事实、连续量按需算"）
正是在做推导，本次推翻。

### Added

- **保真层 `fidelity/`**（新增）
  - `sexpr.py`：无损 S 表达式读写。原子保留**原始拼写**（`1.0000` 不会变成 `1.0`），
    引号/裸词区分，转义表可逆，表外转义保留反斜杠（不静默吃掉）。
  - `kicad_doc.py`：整份 `.kicad_pcb` 进树；`verify()` 做 **token 双射**自证
    （`signature(parse(dumps(tree))) == signature(tree)`）；`coverage()` 报告
    "已带但没有读取规则"的节点清单（携带 ≠ 丢弃 ≠ 猜）。
  - `epro_doc.py`：整个 `.epro` 携带——每个 ZIP 成员（含二进制 blob）、每一行日志、
    `(type,id)` 最高 ticket、空 payload = 删除（**删除本身作为事实保留**）、
    不可解析的行单独点名；`verify()` 做**字节一致 + 状态幂等**双证；
    `key_inventory()` 把"猜键名"变成"读键名"（按 type 列出真实出现的键、类型与样例值）。
  - `views.py`：从树上读事实（nets / net_class / layers / stackup / setup / footprints /
    pads（精确尺寸·钻孔·旋转·网络·rratio·primitives）/ tracks / vias / zones（含**文件里存的
    填充多边形**）/ Edge.Cuts / 板上文字与图形 / 未解释节点）。**每条记录都带 `raw`**
    ——完整原始 S 表达式，视图漏掉的不会丢。
  - `engine.py`：**裁判**。`verify(doc)` 把原件与写回件都交给 `kicad-cli`，
    比对 stats（尺寸/铜面积/板厚/焊盘数）、Gerbers（逐层铜箔/丝印/板框，归一化创建时间）、
    position CSV（每个元件的位置与旋转）。`drc(path)` 跑 KiCad 自己的 DRC 并回判决。
    `stats(path)` 回 KiCad 自己的统计。全部只在临时目录工作且从不传 `--save-board`。
- **新工具 4 个**（26 → 30）：`board_fidelity`、`board_view`、`board_drc`、`board_stats`。
- `capabilities` 增 `derived_vs_authoritative` 段：哪几个是"本服务算的"、哪几个是"厂商引擎说的"、
  以及不一致时该信谁。

### Changed

- 五个几何测量工具（`board_clearance` / `board_free_space` / `board_pour_connectivity` /
  `board_cross_split` / `board_loop_area`）的每条 `ok` 信封现在都带
  `answer_source`、`authoritative: false`、`verdict_available_from: board_drc`。
- `adapters/canonical.py` 的定位改为"保真树之上的便利视图"，不再是"唯一真值"。
- `--selftest` 13 → 17 项（新增 token 双射、覆盖完整、铺铜填充是读来的不是算的、DRC 判决或明确拒绝）。
- 测试 110 → 143 个。

### Fixed

- **合成样本根本不是合法 KiCad 文件**（保真层的第一个产出）。`(dnp)` 裸写、v7 格式里用
  `property`、`gr_rect` 混用 v8 的 `stroke` 写法——`kicad-cli` 直接拒绝加载，
  意味着此前所有"Adapter 通过"都建立在一份 KiCad 不认的文件上。样本已重写为 v7 合法文件
  （含 DNP、keepout、铺铜填充、net_class、丝印文字），并被 `kicad-cli` 每轮验证。
- `dnp` 只认 `(dnp)` 裸节点，漏掉真实的 `(attr smd dnp)` 写法。
- `net_class` 只认 `track_width`，漏掉 KiCad 7 的 `trace_width`（反之亦然）；
  现在两种拼法都读，并回报**实际命中的键名**（`trace_width_key`），让"读到"与"猜到"可区分。
- `pad` 的 `roundrect_rratio` / `layers` 等属性取到了表头而不是值（返回 `None`）。

### Verified

- KiCad 侧往返裁判通过（`kicad-cli 10.0.7`）：原始板与写回板的 stats JSON 相同、
  4 层 Gerber 逐字节相同（归一化创建日期/工程 id）、位置 CSV 相同。
- **裁判有牙**（`tests/test_fidelity.py::test_engine_check_has_teeth`）：故意删掉铺铜 zone 节点，
  `back_copper_area` 与 `Copper,L2,Bot` 立刻不同 → `identical: false`。闸门不会假绿。
- `python -m unittest discover -s tests` → 143/143；`--selftest` → 17/17。

## [0.2.0] - 2026-10-27

三层只读能力落地：**Adapter（双栈解析）+ Oracle（板级度量）+ Desktop（EDA 探测与开关）**，
外加差分阻抗。仍然没有写路径。

### Added

- **Adapter 层**（`adapters/`）
  - `canonical.py`：唯一真值容器 `Board`（mm、Y 向上、网络用**名字**）。`present`/`absent` 逐事实记账，
    未知事实键直接 `ValueError`；`resolve_nets()` 把各格式的 net code 一次性归一成名字——
    否则同一条网络在 pad 上是 `"VCC"`、在走线上是 `"2"`，所有按网络比较的度量都会静默返回空集。
  - `kicad_pcb.py`：手写 S-expr 解析；`Edge.Cuts` 的 `gr_line/gr_arc/gr_rect/gr_circle/gr_poly/gr_curve`
    串成闭合轮廓（多轮廓按面积最大者为外框，其余为开槽）；footprint 旋转把 pad 变换到板坐标；
    `property`/`fp_text` 双兼容；叠层读 `(stackup ...)` 的厚度/εr/tanδ；工程规则读 `.kicad_pro`，
    `.kicad_dru` 只取原文并声明"无可机判 schema"。
  - `epro.py`：ZIP + append-only JSONL；按 `(type,id)` 取 **ticket 最大**；payload 全空 = 删除；
    `0.01 inch → mm` 与 Y 翻转在边界完成；字段名走候选表，未命中一律 `declare_absent` 而不是填 0；
    `inspect()` 输出成员/类型计数/原始 key 名，用于关掉未知字段。
  - `detect_format()` 按**内容**嗅探（ZIP 含 `.epcb` / 文本以 `(kicad_pcb` 开头），不看扩展名。
- **Oracle 层**（`oracle.py`）：`summary` / `components` / `nets` / `clearance` / `free_space` /
  `pour_connectivity` / `cross_split` / `loop_area`。全部只读，每个返回值带 `model`/`precision`；
  缺事实时返回 `FACT_ABSENT_IN_FORMAT` 而不是"看起来没问题"。
- **Desktop 层**（`desktop.py`，全 ctypes）：进程枚举、镜像路径、窗口标题、TCP 监听表（端口归属）、
  文件版本、盘符扫描 + 注册表 `Uninstall` 双视图。`detect` 的证据顺序是
  显式路径 → 运行中进程镜像 → 盘符 → 注册表，找不到也回带 `tried`。
  `launch` 分离启动；`close` 只发 WM_CLOSE，`force` 才 TerminateProcess；
  `open_project` 用窗口标题确认，三态 `confirmed/unconfirmed`，缺文件直接拒绝且不创建。
- **差分阻抗**（`calc/diffpair.py`）：`diff_impedance` / `diff_width`，边耦合微带/带状线，±15%。
- **工具**：新增 16 个（总计 26 个）；`calc_capabilities` 重写为四层自述 + `verified_against`，
  不再声称"Adapter/Oracle/差分阻抗未实现"。

### Fixed

- `adapters/*` 用 `from . import geometry` 导致循环导入 → 改 `from .. import geometry`。
- `Board.net_name()` 只认字符串 code，而 KiCad 的 `nets` 字典键是 int → 两种拼法都接受。
- `oracle.clearance` 的空间桶按 bbox 分格且不膨胀，两个相距 0.7 mm 的 pad 会落进**不相邻**的格子而
  **从不被比较**（`min_gap` 返回 null）；改为按 `cell` 膨胀，并在 `precision` 里声明窗口。
- `oracle.clearance` 在指定网络没有铜时返回 `ok` 且空结果 → 改 `NO_GEOMETRY` + 列出实际有铜的网络。
- `oracle.loop_area` 用 bbox 面积算环路，一条直走线的 bbox 面积为 0 → 环路面积为 0；
  改为「走线长度 × 到下层铜的距离」，bbox 面积单列为 `bbox_spread_area_mm2` 仅供参考。
- `calc/diffpair.width_for_diff_impedance` 的宽度搜索上界误写为 `min(3h, 0.9h)`，
  导致 100Ω/0.2mm 介质被判 `UNREACHABLE`；改为单端模型的 `w/h 3.0` 上界（100Ω → 0.234 mm）。
- `desktop.parse_project_from_title` 只认带扩展名的 KiCad 标题；KiCad 7+ 的 `<工程> - KiCad`
  会解析为空 → 增加兜底正则与 `title_reject`（纯应用名不算工程）。
- `desktop.launch` 的 `Popen` 无引用被 GC 时抛 `ResourceWarning` → 持有引用到进程退出。

### Verified

- `--selftest` 13 项；`unittest discover -s tests` **110 个用例全绿**。
- 真机探测：LCEDA Pro **4.1.60**（`D:\LCEDApro\lceda-pro\lceda-pro.exe`）与
  KiCad **10.0.7**（`D:\KiCad\10.0\bin\`）均由盘符扫描命中。

### Known limitations

- **两个 Adapter 只用合成样本验证过**：本机没有真实 `.epro`/`.kicad_pcb`，
  所以 `.epro` 的字段候选表与 `0.01 inch` 单位仍是按文档写的假设（`source.unit_verified: false`）。
- 立创 pro 的命令行开工程形式未在真机确认（`open_argv_verified: false`）。
- 立创 API 网关只探测端口，无客户端。
- 无写路径（不创建/不修改/不删除，`tests/test_tools.py` 用 sha256 固化）。

## [0.1.0] - 2026-10-27

首个可运行版本：Calc 层 + MCP 协议自检。

### Added

- **协议层**：stdlib 手写 JSON-RPC 2.0（换行分隔），支持 MCP `2025-06-18` / `2025-03-26` / `2024-11-05`；
  `initialize` 回显客户端版本，`tools/list`、`tools/call`、`ping` 及空 `resources/list` / `prompts/list`；
  日志只写 stderr。零外部依赖（`dependencies = []`）。
- **统一信封**：`result.ok()` 强制 `model` / `basis` / `precision` / `assumptions`，缺 `basis` 视为无效；
  错误统一 `{"status":"error","reason_code","message","hint"}`。
- **单位**：`unit_convert`，含立创 EDA Pro 内部单位 `0.01inch = 0.254 mm`。
- **载流**：`width_for_current` / `current_capacity` / `via_current_allowed`（IPC-2221 图表拟合，
  `I = k·ΔT^0.44·A^0.725`；过孔按桶壁侧面积折算，标注 `not_standard_defined`）。
- **阻抗**：`trace_impedance` / `width_for_impedance`（IPC-2141A 微带/带状线闭式解 + 二分反解）。
- **射频**：`lambda_over_20`。
- **安规**：`creepage_required` / `clearance_required`，无表 fail-closed。
- **自述**：`calc_capabilities` 报告能算什么、拒算什么及原因、尚未实现什么。
- **验证**：`--selftest` 8 项协议级检查；`tests/test_calc.py` 25 个用例。

### Known limitations

- 差分阻抗未实现（USB 90Ω / 以太网 100Ω 目标不可用单端反解）。
- `ipc2152` 与 IEC 60664-1 数据表故意为空（`verified: false`），相关调用一律拒绝回答。
- 阻抗反解限于 `w/h 0.05..3.0`；越界返回 `OUT_OF_VALIDITY`（闭式解在此之外会给出非正阻抗）。
- Adapter（KiCad `.kicad_pcb` / 立创 `.epro`）与 Oracle 层尚未实现。

### Docs

- `docs/00`~`docs/09`：从开源 MCP 调研、AI 原生表示、信息丢失审计、布局度量层、无损转化架构总纲、
  两个 EDA 的存储形式，到自研定位与风险分期、PCB 设计流程与工具映射、布局方法论补强与标准冲突登记。
