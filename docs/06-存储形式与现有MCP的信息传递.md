# PCB 到底怎么存的，以及现有 MCP 到底喂给 AI 了什么

日期：2026-10-27
依据：KiCad 官方 Board File Format 规范、EasyEDA Pro 官方文件格式规范（V3, 2025-10-21）、KiCad 的 EasyEDA 导入器文档。

---

## 1. KiCad：`.kicad_pcb` = 纯文本 S-expression

### 1.1 顶层分区（顺序不强制）

```
(kicad_pcb (version 20260101) (generator ...)
  (general   ...)   ; 板厚等
  (page      ...)
  (layers    ...)   ; 层定义与顺序
  (setup     ...)   ; ★ 全局制造参数 + 叠层 + 绘图设置
  (property  ...)
  (net       ...)   ; ★ 只有 序号 + 名字
  (footprint ...)   ; 器件：本体/焊盘/丝印/3D
  (gr_line|gr_arc|gr_rect|gr_poly|gr_text ...)  ; 板级图形
  (image     ...)
  (segment | via | arc ...)   ; ★ 铜（线/孔/圆弧）
  (zone      ...)   ; ★ 覆铜（含已填充多边形）
  (group     ...)
)
```

### 1.2 关键细节

**`(setup ...)` 里藏着大量"规则性"信息**
- 叠层：`(stackup (layer "F.Cu" 0 (type "copper") ...) (layer "dielectric 1" (thickness ..) (material ..) (epsilon_r ..) (loss_tangent ..)) ...)` ——**阻抗计算的全部输入都在这**（介质厚度/εr/tanδ/铜厚）。
- 制造容差：`pad_to_mask_clearance`、`solder_mask_min_width`、`pad_to_paste_clearance(_ratio)`。
- 板级默认：`(general (thickness 1.6))`。

**`(net ...)` 只有序号和名字**，以及 `(net 1 /SIGNAL)`。**网类（netclass）不在这里**——KiCad 把 netclass 移到了工程文件 `.kicad_pro`，自定义规则在 `.kicad_dru`。⇒ **只读 `.kicad_pcb` 的工具，天然拿不到规则。**

**footprint 里**：`(at x y [rot])`、`(layer ...)`、`(fp_text reference/value ...)`、`(pad "1" smd roundrect (at ..) (size ..) (layers ..) (roundrect_rratio ..) (chamfer ..) (net ..) (pinfunction ..) (pintype ..) (solder_mask_margin ..) (solder_paste_margin ..) (thermal_bridge_angle ..) (zone_connect ..))`、`(model ...)`。
⇒ **焊盘形状/阻焊/钢网/热焊盘参数确实都在文件里**，只是没有现成工具把它整理出来。

**segment / via / arc**：`(segment (start ..) (end ..) (width ..) (layer ..) (net ..) (tstamp uuid))`、`(via [blind|micro] (at ..) (size ..) (drill ..) (layers ..) (net ..))`。
⇒ **盲孔/微孔类型是有的**（`blind`/`micro`），通孔则省略类型。

**zone**：`(zone (net ..) (net_name ..) (layer ..) (hatch ..) (connect_pads (clearance ..)) (min_thickness ..) (fill (thermal_gap ..) (thermal_bridge_width ..) (arc_segments ..)) (polygon (pts (xy ...))))` + **已填充的多边形**。
⇒ 覆铜边界、热焊盘间隙、填充结果都是原始的。

### 1.3 文件里**没有**的（必须计算）

| 事实 | 为什么不在文件里 |
|---|---|
| 实际连通性（哪些段连到哪个 pad） | 只存了 "段属于哪个 net" 和一个 uuid；**段与段的几何相接关系要自己算** |
| 飞线/ratsnest、未布线数 | 纯派生量 |
| DRC/ERC 结果 | 派生量 |
| 环路面积、回流路径、跨分割 | 物理派生量 |
| 网络类生效结果 | 在 `.kicad_pro` / `.kicad_dru`，且要被 pattern 规则求值后才知道 |
| 铜的最终形状（zone 是否 refill 过） | 文件里是上次填充的**快照**，可能与规则不一致 |

---

## 2. 立创EDA Pro：`.epro` = ZIP 里的 **JSON Lines 日志**

### 2.1 容器结构

```
.epro (实为 zip)
├── project.json          # 工程清单：schematics/boards/pcbs/devices/symbols/footprints 的引用关系
├── *.esch                # 原理图页（JSON Lines）
├── *.epcb                # PCB（JSON Lines）
├── *.esym / *.efoo       # 符号 / 封装库
├── *.ecop                # ★ 覆铜填充数据（单独文件！）
└── *.eblob               # 内嵌图片二进制
```

### 2.2 V3 的核心设计：**日志 + 最终一致性**（这很关键）

每行是**两个 JSON 对象直接拼接**：

```
{"type":"TYPE","id":"UUID","ticket":1}||{"<key>":"<value>", ...}
```

- 外层 `{type,id,ticket}` 给最终一致性框架用；内层是原子的键值数据。
- **同一个 `type+id` 出现多条记录时，保留 `ticket` 更大的那条。**
- **删除 = 把内层数据设成空字符串** `""`；文档删除写 `{"type":"DELETE_DOC","ticket":n}||{"isDelete":true}`。
- 文档边界用 `{"type":"DOCHEAD"}||{"docType":"PCB","uuid":...}` 分隔，`docType` 有 `PROJECT_CONFIG/BOARD/SCH/SCH_PAGE/PCB/PANEL/SYMBOL/FOOTPRINT/DEVICE/BLOB/INSTANCE`。
- 约定：坐标长度单位 **0.01 英寸**；角度逆时针为正、单位度；颜色 `#RRGGBB`；布尔用 1/0。

⇒ **这是一个 append-only 的设计状态机。** 同一块板子的历史全部在文件里，最终状态要"回放"出来。这意味着：
- 它能表达"版本/撤销"（KiCad 不能）；
- 但**直接读文件 = 读日志**，不做 ticket 归并就会读到中间态。

### 2.3 `.epcb` 的行类型（依据 KiCad 导入器文档，覆盖度很高）

| 类型 | 内容 | 对应我们关心的信息 |
|---|---|---|
| `LAYER` | 层定义与启用 | 叠层 |
| `NET` | 网络名声明（**必须先声明后引用**） | 网表（名义） |
| **`RULE`** | `ruleType 3`=线宽（min/opt/max）、`ruleType 1`=间距（**间距表**） | ★ **规则在文件里**（比 KiCad 强） |
| `VIA` | netname, x, y, drill, diameter | 孔（**没有盲埋孔字段**——Pro 侧通孔模型） |
| `LINE` / `ARC` | netname, layer, 起止, 宽度 / 含夹角 | 铜 |
| `POLY` / `FILL` / `POUR` | 图形 / 实心铜 / 覆铜区（fillOrder=优先级） | 覆铜 |
| **`POURED`** | 父 pourId + `isPoly` 区分**实心轮廓 vs 热焊盘辐条几何** | ★ 填充结果与 thermal spoke 都在 |
| `TEARDROP` | 泪滴 | |
| **`REGION`** | 规则区/板框，`flags`：2=禁器件、5=禁走线、6/8=禁填充、7=禁一切铜 | ★ **keepout 语义是显式的** |
| **`PAD`** | holeData `[ROUND\|SLOT, w, h]`（零尺寸=SMD）、shapeData `RECT[w,h,cornerRadiusPct] \| ELLIPSE \| OVAL \| POLY` | ★ 圆角/槽孔/异形焊盘都在 |
| `COMPONENT` | 层, x, y, 角度, localAttribs | 布局 |
| `PAD_NET` | `[compId, padNumber, netname]` | ★ **pin↔pad↔net 映射是显式的** |
| `ATTR` | parentId, key, value, 可视性, 位置, 字体 | 位号/Value/Device/Footprint… |
| `STRING` / `IMAGE` / `OBJ` | 文字 / 图形 / 位图 | 丝印 |
| `PART`（符号侧） | 多单元符号的单元分隔 | 多单元 |

对照 KiCad 的差异要注意：**EasyEDA Pro 的 `.epcb` 没有"引脚电气类型"**（那是符号 `.esym` 的 `PIN` + `ATTR: Pin Type`），而 KiCad 的 pad 上直接有 `(pintype ...)`。⇒ **两边"同一事实"的存放位置不同，跨后端抽象必须按语义而不是按字段名对齐。**

### 2.4 文件里没有的（与 KiCad 同理）

实际连通性、ratsnest、DRC 结果、布局/电气指标——全部是派生量。

---

## 3. 两边对照：同一事实存在哪

| 事实 | KiCad | EasyEDA Pro |
|---|---|---|
| 板框/开槽 | `gr_*` on `Edge.Cuts` | `REGION`（flags=7 之类） |
| 网络名 | `(net N "NAME")` | `NET` 行 |
| **规则（线宽/间距）** | ❌ 在 `.kicad_pro` / `.kicad_dru` | ✅ `RULE` 行**在 PCB 文件里** |
| 叠层与 εr/tanδ | `(setup (stackup ...))` | 层信息 + 规则（不完整） |
| 焊盘圆角/槽孔 | `roundrect_rratio` / `chamfer` / `(drill oval ...)` | `shapeData[R]` / `holeData[SLOT]` |
| 阻焊/钢网 | `solder_mask_margin` / `solder_paste_margin` | 层 + 规则（部分） |
| 盲埋孔 | ✅ `via blind/micro` | ❌ 通孔模型 |
| 覆铜填充+热焊盘 | `zone` + 填充多边形 + `thermal_*` | `POUR` + `POURED(isPoly)` |
| keepout 语义 | `(keepout (tracks not_allowed) ...)` | `REGION` + flags 位 |
| pin↔pad↔net | pad 上带 net + pintype/pinfunction | `PAD_NET` 显式行 |
| 多单元器件 | 符号侧 unit | 符号侧 `PART` |
| 历史/撤销 | ❌ | ✅ **日志 + ticket** |
| **单位** | mm（内部 nm） | **0.01 英寸** |
| **Y 轴** | 向上为正 | **向下为正** |

> 最后两行是最容易静默出错的地方：跨后端时**坐标必须显式换帧**，否则整个板子镜像。现有 MCP 基本不暴露这件事。

---

## 4. 三类信息：这一步决定了"AI 能拿到什么"

| 类别 | 例子 | 存在哪 |
|---|---|---|
| **① 直接存储** | 器件位置/角度、焊盘几何、铜段、覆铜填充、规则表、keepout、层叠 | 文件里原样 |
| **② 必须计算** | 连通性、ratsnest、未布线数、最小间距、环路面积、跨分割、区域密度、DRC | **不在文件里，谁知道谁算** |
| **③ 源缺失** | 设计意图、datasheet 布局准则、功耗、成本档位、waiver、工艺约束 | 哪都没有 |

**结论：AI 需要的信息里，绝大部分属于 ②。而 ② 只能由工具算出来。** 也就是说，MCP 给 AI 的信息质量，等于"这个 MCP 自己算了多少"——**跟文件格式的富不丰富关系不大。**

---

## 5. 现有 MCP 到底喂了 AI 什么

### 5.1 协议层的事实：MCP 不给"板子"，只给"调用结果"

模型能看到的只有三样：

1. **tool schema**（名字 + 参数 + 描述）；
2. **每次 tool 调用的返回文本**；
3. 少数 `resources`（如果有）。

**没有"工程对象"这个东西。** 模型对板子的"理解"= 上下文窗口里历次返回结果的拼接。两块先天不足：

- **容量**：200 个器件的完整状态就几十万 token，放不下；
- **一致性**：模型不会在脑中维护增量状态，第 40 次调用时它对第 3 次改动已无把握。

### 5.2 三种典型返回形态（都实测常见）

| 形态 | 例子 | AI 得到了什么 |
|---|---|---|
| **A. 布尔/ID** | `add_wire → {"success": true, "uuid": "…"}` | **什么都没得到**。写完不知道吸没吸附、有多长、连对没连对 |
| **B. 单图元裸 JSON** | `get_component("U1") → {x: 12.4, y: 8.2, rot: 0, pads: [ {num:"1", x:.., y:..}, × 48 ]}` | 一个器件 48 个焊盘的绝对坐标。算不出任何设计意义 |
| **C. 人写的摘要** | `board_summary → "Board: 63×33mm, 2 layers, 42 components, 128 nets"` | 4 个数字，够写简介，不够做任何决策 |

**没有第四种**——没有一个现成 MCP 在给"派生事实"（环路面积、对称偏移、跨分割、预算余量）。

### 5.3 具体缺失链（以一次真实的"布线"为例）

```
AI:  route_trace(net=VIN, from=(10,10), to=(20,12), width=0.3)
MCP: {"ok": true}                                   ← 形态 A
AI 现在知道什么？: 一个布尔值。
它不知道：      · 这条线跟谁靠得太近
                · 有没有穿过 keepout
                · 这段铜的回路面积是多少
                · 下面有没有地平面
                · 还需要几段才真正连通
它接下来做什么？: 继续猜下一段坐标。
```

**这就是"AI 瞎话"的完整机制**：不是模型不会推理，而是每次调用它只拿到一个布尔值，然后被迫用想象补全其余部分。

### 5.4 一张"谁给了 AI 什么"的矩阵

| 事实 | 文件里有 | 立创扩展 API 可读 | KiCad IPC 可读 | **现有 MCP 返回给 AI** |
|---|---|---|---|---|
| 器件坐标 | ✅ | ✅ | ✅ | ✅（裸坐标） |
| 焊盘几何/阻焊/槽孔 | ✅ | ✅ | ✅ | ❌ 或只有 w×h |
| 引脚电气类型 | 符号侧/pad | ✅ | ✅ | ❌ |
| pin↔pad 映射 | ✅ | ✅ | ✅ | ❌（默认相等） |
| 规则（间距/线宽） | 立创✅ / KiCad 在 pro | ✅ | 部分 | ❌ 或只有默认值 |
| keepout 语义 | ✅ | ✅ | ✅ | ❌（只说"禁区"，不说禁什么） |
| **实际连通性** | ❌ | 需算 | 需算 | ❌ **基本没有** |
| 未布线数 | ❌ | 需算 | 需算 | 偶尔有（算法不详） |
| 最小间距 / 间隙 | ❌ | 需算 | 需算 | ❌ |
| 环路面积 / 跨分割 | ❌ | 需算 | 需算 | ❌ |
| 对称偏差 / 热梯度 | ❌ | 需算 | 需算 | ❌ |
| 预算余量 | ❌ | 需算 | 需算 | ❌ |
| DRC | ❌ | ✅ | ✅ | ✅（原始 violation 文本） |
| 设计意图 | ❌ | ❌ | ❌ | ❌ |

**最后三行是全部问题所在**：AI 需要的决策依据集中在下半张表，而现有 MCP 只覆盖上半张表。

---

## 6. 对我们的设计意味着什么

1. **文件格式本身就够丰富的**（尤其立创的 `.epcb`：规则、keepout flags、槽孔、热焊盘辐条都在；KiCad 的叠层与 εr 也在）。**问题从来不是"源头没有数据"，而是"没人把图元算成事实"。**
2. **`oracle` 层不是锦上添花，是主体工程。** 价值量与"能实现多少个派生量查询"成正比。
3. **跨后端必须按语义对齐**：KiCad 的规则在 `.kicad_pro`、立创的规则在 `.epcb`；KiCad 的电气类型在 pad 上、立创在符号上。按字段名对齐必错。
4. **必须显式换帧**：单位（mm vs 0.01in）与 Y 轴方向（上 vs 下）。这类错误不报错、只给镜像的板子。
5. **立创的日志格式意味着**：可以拿到"改动历史"，这对 `undo/checkpoint/diff` 是天然优势，值得放进设计（KiCad 侧则必须自己做快照）。
6. **不要试图"读文件然后喂给模型"**。文件是给引擎用的；模型要的是 §5.4 下半张表的数字，那些只能算。

---

## 7. 一句话总结

> **PCB 文件里只有图元；AI 需要的是事实；中间那层"推导"几乎所有人都没做。**
> 现有 MCP 的普遍形态是「把图元 API 转成 tool + 返回 `{"ok":true}`」，所以模型每次只能拿到一个布尔值，剩下的靠想象——这就是它"瞎话"的全部原因。
