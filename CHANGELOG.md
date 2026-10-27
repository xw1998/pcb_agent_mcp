# Changelog

版本号遵循 [SemVer](https://semver.org/lang/zh-CN/)；`0.x` 期间 tool 的入参与返回结构仍可能变动，
但**信封契约（`status` / `basis` / `precision` / `reason_code`）从 0.1.0 起冻结**。

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
