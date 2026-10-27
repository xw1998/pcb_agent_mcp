# pcb-agent-mcp

让 AI 在 **立创 EDA Pro** 与 **KiCad** 里真正"算得出来再动手"的 MCP 服务。

当前版本 `0.1.0` 只实现四件套里的 **Calc 层**（无板子的工程计算）。Adapter 与 Oracle 尚未实现，
但工具信封、错误码与 `calc_capabilities` 自述已经按最终形态定好了，后续只往里填内容，不改契约。

> 设计文档在 [`docs/`](docs/)，按编号顺序读：
> `00` 调研与架构 → `05` 无损转化架构总纲 → `06` 存储形式与信息传递 → `07` 自研定位与风险分期
> → `08` PCB 设计流程与工具映射 → `09` 布局方法论补强与标准冲突。

## 为什么不是"又一个图元 MCP"

现有 EDA MCP 的普遍形态是「把图元 API 转成 tool + 返回 `{"ok":true}`」，模型每次只拿到一个布尔值，
剩下的靠想象 —— 这就是「AI 画 PCB 会瞎话」的根因。

PCB 文件里只有图元；AI 需要的是**事实**（这块板 1A 走多宽、90Ω 微带多宽、2.4GHz 布线间距够不够）；
中间那层「推导」几乎没人做。而源头并不缺数据（立创 `.epcb` 的规则、keepout flags、槽孔、热焊盘辐条都在；
KiCad 的叠层 εr/tanδ/铜厚也在）—— **不是没存，是没人算。**

所以本项目 5% 预算做解析，95% 预算做推导。

## 三条诚实规则（代码里强制，不是口号）

1. **没有 basis 的值一律无效。** 每个结果必须带 `model` / `basis` / `precision` / `assumptions`，
   `require_basis()` 在信封层强制；缺 `precision` 直接抛错。
2. **两道 fail-closed。** 载流不传 `standard` 报 `STANDARD_REQUIRED`；`ipc2152` 表未加载报
   `STANDARD_DATA_NOT_LOADED`；爬电/间隙表缺失报 `SAFETY_TABLE_MISSING`。
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

## 工具（10 个）

| tool | 作用 | 关键约束 |
|---|---|---|
| `calc_capabilities` | 自述：能算什么 / 拒算什么及原因 / 还没做什么 | 建议先调它 |
| `unit_convert` | 单位换算，含立创内部单位 `0.01inch` | 未知单位 `UNIT_UNKNOWN` |
| `width_for_current` | 电流 → 线宽（IPC-2221 反解） | 必须传 `standard`；ΔT≤0 拒绝 |
| `current_capacity` | 线宽 → 载流 | 开窗时只取下界，不计锡层 |
| `via_current_allowed` | 过孔载流（桶壁侧面积折算） | 标注 `not_standard_defined`，±30% |
| `trace_impedance` | 几何 → Z0（微带/带状线） | w/h 超出 0.05..3.0 标 `validity_note` |
| `width_for_impedance` | Z0 → 线宽（二分反解） | 不可达时回 `UNREACHABLE` + 可达区间 |
| `lambda_over_20` | λ/20 布线间距 | 与频率/εr 绑定，不给记忆值 |
| `creepage_required` | 爬电距离查表 | 无表 fail-closed |
| `clearance_required` | 电气间隙查表 | 无表 fail-closed |

`via_current_allowed` 实算 0.3mm 孔 / 25µm 镀铜 → 1.795 A，高于工程经验值 1A；
返回里同时给出 `rule_of_thumb_gap`，请按保守者取用。

## 用法

零外部依赖，Python ≥ 3.9。

```bash
python run_server.py --version       # 0.1.0
python run_server.py --selftest      # 协议级自检，8/8
python -m unittest discover -s tests # 25 个用例
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

- **V0（当前）** Calc 层 + `calc_capabilities` 自述。
- **V0.5** 双 Adapter 只读：KiCad `.kicad_pcb` 与立创 `.epro` 都解析到同一 Canonical，
  单位与轴向只在 adapter 边界归一化一次（Canonical 内只有 mm + Y 向上）。
- **V1** Oracle 只读投影：`fits_where` / `min_clearance` / `loop_area` / `cross_split` / `pour_connectivity`。
- **V2** 立创写路径（含 ticket 语义、写后回读校验）。
- **V3** KiCad 写路径。

双栈必须同时在场：**单端开发永远发现不了适配层设计错了。**

## 已知未实现

- 差分阻抗（coupled line）——USB 90Ω / 以太网 100Ω 目标**不能**用单端 `width_for_impedance` 解。
- 铺铜连通性、回流路径、温度场（属 Oracle，需板子）。
- IPC-2152 表（现行载流标准，与 IPC-2221 差可 >3×）、IEC 60664-1 爬电/间隙表 —— 需人工核验后填入
  `data/creepage_tables.json` 并置 `verified: true`。

## 项目结构

```
pcb_agent_mcp/
├── run_server.py                 # 启动器（把 src 加入 sys.path 后起 stdio 服务）
├── pyproject.toml                # 零 runtime 依赖；entry point: pcb-agent-mcp
├── src/pcb_agent_mcp/
│   ├── server.py                 # stdlib JSON-RPC 2.0 stdio 协议层 + --selftest
│   ├── tools.py                  # 10 个 tool 的 schema 与 handler 注册
│   └── calc/                     # Calc 层（纯算术，不读文件、不联网）
│       ├── result.py             # 统一信封：basis/precision 强制
│       ├── units.py              # 含立创内部单位 0.01inch
│       ├── current.py            # IPC-2221 载流正/反解 + 过孔
│       ├── impedance.py          # 微带/带状线正/反解 + 有效区间护栏
│       ├── rf.py                 # λ/20
│       └── creepage.py           # 爬电/间隙，fail-closed
├── data/
│   └── creepage_tables.json      # 空表 + verified:false（故意不填）
├── tests/test_calc.py            # 25 个用例
└── docs/                         # 设计文档 00~09，建议按编号顺序读
```

## 免责声明

本项目是**计算与推导辅助工具**，不是标准文本、不是仿真器、不是替代评审的判决依据。

- 载流、阻抗、λ/20 均为**闭式近似**，误差已在 `precision` 字段声明（±12%~±30%）。定稿前请用场求解器
  或打样厂的阻抗报告复核。
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

## License

[MIT](LICENSE) © 2026 xw19981010
