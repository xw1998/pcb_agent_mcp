# Changelog

版本号遵循 [SemVer](https://semver.org/lang/zh-CN/)；`0.x` 期间 tool 的入参与返回结构仍可能变动，
但**信封契约（`status` / `basis` / `precision` / `reason_code`）从 0.1.0 起冻结**。

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
