# 让 PCB 变成 AI 能处理的模型（而非暴露 EDA 的图元 API）

日期：2026-10-27
背景：现有开源 MCP（KiCAD-MCP-Server 247 tool、eda-agent 560+ tool、easyeda 系列）的共性问题——
把 `place_component(x,y)` / `add_wire(x1,y1,x2,y2)` 这类**图元 API** 直接交给模型，
于是 AI 必须自己做几何、自己维护一张它维护不了的坐标地图，结果是幻觉、漏约束、信息不足。

---

## 1. 根因：PCB 设计被拆错了

| 子问题 | 本质 | LLM 能力 | 现有 MCP 的做法 | 正确分工 |
|---|---|---|---|---|
| 连接关系 netlist | 离散图 | ✅ 强 | 让 AI 一步步 `add_wire` | **AI 出网表，机器画线** |
| 布局 placement | 2D 装箱 + 约束满足 | ❌ 弱（尤其绝对坐标） | 让 AI 出 `(x,y,rot)` | **AI 出区域/邻接/顺序意图，机器求解坐标** |
| 布线 routing | 迷宫搜索 | ❌ 极差 | 让 AI `route_trace` 逐段画 | **100% 算法（A*/Freerouting），AI 只给约束** |
| DRC/ERC | 规则比较 | ❌ 不该做 | 返回 violation 列表让 AI 自己理解 | **机器分组 + 归一化 + 给建议动作** |
| 评审/取舍/命名 | 权衡 | ✅ 强 | 没提供 | **AI 主战场** |

一句话：**AI 应当是"规格作者 + 评审者 + 导航者"，不是"鼠标操作员"。**
现有工具让 AI 当鼠标操作员，这就是它一定会"瞎话"的原因。

---

## 2. LLM 到底擅长什么（决定表示形式）

经验上模型对以下四类输入处理得好，对连续坐标/图像测量处理得差：

1. **离散符号图**：网表、依赖图、树 → 文本/表格，几乎无损
2. **粗粒度网格**：20×12 ~ 60×40 的字符或小整数网格 → 空间关系可读，且能对齐到词元
3. **DSL / 代码**：结构化、有语法、可静态检查 → 模型的母语
4. **数值比较与清单**：`0.78 vs 0.55`、`violations=3` → 能做判断
5. **差量（diff）**：`改了什么` 比 `现在是什么` 更容易被正确理解

它做得差的：精确毫米坐标、角度、像素级看图的测量、超过几百项的并列枚举、需要多步几何推演的问题。

⇒ 表示设计原则：**离散化、粗网格化、可测量、可 diff、分层检索。绝不要求模型输出或心算坐标。**

---

## 3. 四层表示

```
L1 意图规格 Design Spec（YAML，AI 写，人可读）
      ↓ 编译（确定性）
L2 粗粒度状态 Digest（机器算，AI 读；词元少、可比对、可测量）
      ↓ 按需下钻
L3 精确几何（机器持有，AI 只在窗口内查询）
      ↓
L4 算法执行（求解器/布线器/DRC 引擎，AI 不进入）
```

### 3.1 L1：AI 只写这个

```yaml
board: {size: [63, 33], layers: 2, rules: {clearance: 0.2, min_track: 0.2, min_via: 0.3}}
flow: [J1, F1, D1, C1, U1, L1, C2, J2]        # 信号流顺序 = AI 最擅长的"叙事"
blocks:
  - id: power
    parts: [F1, D1, C1, U1, L1, C2]
    region: {anchor: edge-left, band: [0, 0.35]}   # 相对比例，不是毫米
    order: [F1, D1, C1, U1, L1, C2]
  - id: mcu
    parts: [U2, Y1, C3, C4]
    region: {anchor: center}
  - id: io
    parts: [J2, D2, R1]
    region: {anchor: edge-right}
constraints:
  - {type: near,  a: C1, b: U1.VIN, max: 3mm}
  - {type: apart, a: power, b: io, min: 8mm}
  - {type: same_side, parts: [L1, D1]}
nets:
  - {name: VIN, pins: [J1.1, F1.1], class: power, width: 1.0}
  - {name: USB_DP, diff_with: USB_DM, impedance: 90, match: 0.5}
```

要点：
- **没有一处绝对坐标**。区域用"锚点 + 比例带"，距离用"mm 阈值"，顺序用列表。
- 每条约束都是**可机器判定真假**的（near/apart/same_side/order），不是形容词。
- 整体体积小，人一眼能审，模型不容易漏项。

### 3.2 L2：Digest —— 解决"信息不够"的核心

**同一个板子，机器给出多种分辨率的事实，AI 按需取。**

**(a) 粗网格地图**（60×30 字符，词元约 300，比对整板截图有效得多）

```
   col 0        15        30        45        59
r0  +-------------------------------------------+
r2  |[J1]              [U1###]        [L1]      |  F.Cu 占用 34%
r6  |      [F1][D1][C1]                         |  unrouted 11
r10 |                                   [J2]    |  ⚠ 拥塞 r8-r14,c20-c34
r16 |        [U2#########]                      |
r20 |   [Y1]  [C3][C4]                          |
r26 |              [D2][R1]                     |
r29 +-------------------------------------------+
```

**(b) 结构表**（每个 region 一行，AI 用来做取舍）

| region | parts | area% | density | unrouted | max_density | flag |
|---|---|---|---|---|---|---|
| left(power) | 6 | 18 | 0.31 | 2 | 0.55 | |
| center(mcu) | 3 | 42 | 0.78 | 9 | 0.91 | ⚠ 过高 |
| right(io) | 3 | 12 | 0.22 | 0 | 0.30 | |

**(c) 连接拓扑聚合**（不是逐段线，而是"模块↔模块：条数 + 总长 + 最密走廊"）

```
power→mcu: 4 nets, 总长 38mm, 走廊 c28-c34 ⚠
mcu→io   : 7 nets, 总长 61mm, 走廊 c38-c45
GND      : 23 pins, 覆铜覆盖 78%
```

**(d) 违规归一化**

```
DRC 3 violations (2 error, 1 warning)
  E1 clearance 1.8mil@(U1.pad7, C3)  建议: 移 C3 或改 pad 出线
  E2 unrouted VIN (J1.1→F1.1)         建议: 加宽至 1.0mm 后重跑
  W1 silk over pad (R1)               建议: autofix
```

**(e) 变更 diff**（每次 apply 后）

```
Δ vs step3: +2 parts, +11 tracks, unrouted 13→11, density center 0.83→0.78, new DRC: E2
```

### 3.3 L3：按需下钻（请求-应答，不是快照）

给 AI 一组"测量/查询"工具，让它问事实，而不是从图上猜：

- `measure(C3, U1.VIN)` → `2.41mm`（含最短路与是否穿越其它器件）
- `nearest_parts(C3, k=5)` → 带距离的表
- `pads_of(U1)` → 引脚表（编号/名/类型/相对位置，非绝对坐标）
- `query(predicate)` → `net==GND && layer==F.Cu && len>5mm` 之类
- `window(center, r=10mm)` → 该窗口的迷你 digest（局部图 + 局部违规）

### 3.4 L4：算法层（AI 完全不进入）

- 布局求解：把 L1 的 region + order + constraints 变成坐标（模拟退火 / 力导向 / 装箱），返回是否满足全部约束
- 布线：Freerouting / 自研 A*，AI 只给 net 集合与 class
- DRC 修复：确定性 repair（丝印压盘、填充、spoke 角度、等长微调）

---

## 4. 交互协议：Propose → Simulate → Digest → Approve

**绝不"边想边写盘"。**

```
AI  propose_plan(spec)        ① 只算不落盘：求解坐标 + 预检 courtyard/keepout/DRC 增量
                              ② 返回：plan_id + digest + diff + preview.png（可选）
     ↕ 循环（AI 看 digest 改 spec，无需重开）
AI  apply_plan(plan_id)       ③ 落盘 + 自动 checkpoint
AI  autoroute(nets)           ④ 返回 unrouted 数 + 最差拥塞走廊
AI  validate()                ⑤ 结构化违规
AI  repair(violations)        ⑥ 确定性修复
```

这解决了三件事：
1. **不需要模型想象结果**——每次提案都回真实 digest；
2. **可回滚**——apply 前必 checkpoint（KiCad 原理图改写没有撤销栈，这是硬伤）；
3. **一次调用代替 N 次**——不是 40 次 `place_component`，是 1 次 `propose_plan`。

---

## 5. Tool 面：从 247 砍到 ~15

| Tool | 类 | 说明 |
|---|---|---|
| `board_digest(level, window, focus)` | read | 分级摘要（§3.2） |
| `parts_table(filter)` | read | 器件表 |
| `net_graph(scope)` | read | 连接拓扑聚合 |
| `measure(a, b)` | read | 精确测量，替代"看图估计" |
| `query(predicate)` | read | 结构化检索 |
| `validate(kind=erc\|drc)` | read | 归一化违规 |
| `propose_plan(spec)` | 计算 | 不落盘 |
| `apply_plan(plan_id)` | 写 | 带 checkpoint |
| `checkpoint(name)` / `restore(name)` | 写 | 2 个 |
| `place_auto(block, spec)` | 算法 | 装箱求解 |
| `autoroute(nets)` | 算法 | 布线 |
| `repair(violations)` | 算法 | 确定性修 |
| `preview(scope, scale)` | 视觉 | 只在需要"看一眼整体观感"时用 |
| `part_search(q)` | 库 | 选型 |
| `export(target)` | 输出 | Gerber/BOM/pos |

对比：mixelpixx 247 个 tool 的 schema 本身就吃掉几万 token，还没开始画就把上下文烧了一半。

**原则：少工具、厚载荷、强语义。** 一个 `propose_plan` 承载 40 个图元操作。

---

## 6. 这套设计如何对应两个 EDA

| 层 | 立创EDA Pro | KiCad 10 |
|---|---|---|
| L1 spec | 语言无关，完全相同 | 同 |
| L2 digest | 扩展内读 `SCH_*/PCB_*` 图元计算 | IPC 读 PCB + S-expr 读原理图计算 |
| L3 几何 | 扩展 API | IPC / 文件改写 |
| L4 算法 | 自研求解器（宿主侧，不在扩展里） | 同（Freerouting 外挂） |

**关键**：digest 与 spec 都是我们自己的产物，**两个后端只负责"读图元 → 算事实"和"收指令 → 落图元"**。这就是上次报告里说的 IR 层，但重心从"统一图元"改成"**统一事实与意图**"——后者才是 AI 真正需要的东西。

---

## 7. 立即可以验证的实验（1~2 天）

不需要先做完整 MCP，只要证明"AI 拿到 digest 就够了"：

1. 拿一个真实 KiCad 板子，写脚本产出 §3.2 的 (a)(b)(c)(d) 四份文本；
2. 把 digest 交给模型，问 10 个"只有看板才知道"的问题：
   - 哪个区域最拥塞？为什么可能过不了？
   - 去耦电容离 MCU 电源脚多远？合格吗？
   - 加一个 8mm×6mm 的器件应该放哪？
   - 这 3 条 DRC 里哪条最该先修？
3. **再用整板 PNG 问同样问题，比对正确率**。

如果 digest 组的正确率显著高于看图组，这套表示就是对的；之后才是实现工具面。

**验收判据**：任何"让 AI 判断布局"的问题，答案必须能追溯到 digest 里一个具体数字——追溯不到，说明 digest 少了字段，补字段而不是补提示词。
