# 155 布局规则的出处台账与 uncited 清单（0.1.18）

这篇只回答一件事：**`calc/rules.py` 里那 27 条布局规则，每一条能不能拿出一句话给别人看。**
答案是 5 条能、22 条不能。不能的那些，在这篇里被逐条点名，不再披着「有依据」的外衣。

> **0.1.20 更正（本文以下内容是 0.1.18 的事实，不改写）**：读完 ADI《PCB设计秘籍》后，
> 覆盖变成 **cited 13/27（其中 9 条连阈值都有原文，4 条只有原则有原文）/ uncited 14**。
> 新增出处的行、以及「为什么剩下 14 条仍然没有出处」见 `docs/157`。
> 本文 §4 的 22 条清单只作为 0.1.18 的历史快照，不要拿它当现状。

## 0 为什么会有这篇

0.1.17 之前，27 条布局规则的 `source` 字段写的全是本仓库自带的技能名
（`"kicad-free (…)"`、`"rf-circuit-design 3.4"`）。那不是出处，那只是「我当年觉得这是常识」。
于是「电感本体下方不能走线」这条规则虽然写进了代码，却只拿到 P3 级别的口气，本板上真的有
铜穿过去也没人当回事——**不是没写规则，是这条规则背后没有一句可以核对的话，所以它没有分量。**

0.1.18 的第一件事就是把这句话补上，并且把「补不上」的那些如实记账。

## 1 台账文件

`data/layout-rule-sources.json`（与 `jlc/data/jlc-pcb-capabilities.json` 同构）：

- `sources{S_XXX}` 每条带 `tier` / `title` / `url` / `fetched_at` / `kind`
- `entries[{rule_id, rule_level, severity, agreement, quotes[{src, quote}], conflict_note, why_severity, read_on_this_board}]`
- `quote` 是**逐字**原文，可以拿去搜索引擎核；`agreement` 记录这条规则的证据强度
- 顶层 `description` 里写死：**只有出现在 `entries` 且 `quote` 非空的 rule_id 才算有出处**

读它的两个入口：

```
PYTHONPATH=src python -m pcb_agent_mcp.calc.rule_sources        # 打印 5 cited / 22 uncited
```

代码侧 `pcb_agent_mcp.calc.rule_sources.citation(rule_id)` 对没登记的规则**返回 (None, 理由)**，
而不是返回空字典——空字典会被下游读成「已检查、无出处问题」。

## 2 四个来源

| 键 | 层级 | 标题 | URL | 抓取日 |
|---|---|---|---|---|
| S_ADI | T1 | ADI Analog Dialogue RAQ#164（Frederik Dostal） | `https://www.analog.com/en/resources/analog-dialogue/raqs/raq-issue-164.html` | 2026-11-14 |
| S_ROHM | T1 | ROHM 降压型 DC/DC 转换器电路板布局基础与案例（中译） | `https://m.elecfans.com/article/7114464.html` | 2026-11-14 |
| S_NEXTPCB | T2 | Inductor Placement and PCB Design Rules | `https://www.nextpcb.com/blog/inductor-placement-pcb-design-rules` | 2026-11-14 |
| S_GENUWAY | T2 | 晶诺威科技：晶振在 PCB 布局中的走线与接地要求 | `https://www.genuway.com/8085.html` | 2026-11-14 |

三条是原厂应用笔记（ADI / ROHM 是芯片厂，说这话要为自家芯片的工作负责），两条是板厂/元件厂
的工艺指南（T2：有工程依据但不是原厂承诺）。层级差不是客套，它决定规则能拿到多高的级别。

## 3 已核对的 5 条（cited）

| 规则 | 级别 | 一致性 | 逐字原文（摘要，全文见 JSON） | 本板读数 |
|---|---|---|---|---|
| `layout.magnetic_keepout` | P1 | 三源独立一致 | ADI「**No sensitive control traces should be routed under an inductor, neither right on the PCB surface nor below, in internal layers, or on the backside of the PCB.**」／NEXTPCB「Do not route any signal traces directly underneath a power inductor on the top layer or the immediate inner layers.」／ROHM「电感的正下方不可配置GND或其他布线(图26)。…应避免电感正下方的布线。」 | 17 段命中，其中 **8 段是真穿越**（轴线 ≥0.3 mm 进本体），3 孔 |
| `layout.magnetic_keepout_deep` | P2 | **有真冲突，取从严** | 同上三源，NEXTPCB 另有一句允许背面布线的前提条款 | 139 段 |
| `layout.magnetic_via_keepout` | P1 | 派生（非逐字） | ADI「I am in favor of a solid ground plane layer, even under the coil.」／NEXTPCB「Yes, but it should be a solid, unbroken ground plane.」 | 3 孔，全在 L1 本体 |
| `layout.crystal_keepout_adjacent` | P2 | 单源 | GENUWAY「**Keep the area under the crystal well-grounded.**」／「Keep clock lines and switching signal lines away from the crystal and its connections as far as possible.」 | 4 段（In4×2 / In2 / B.Cu） |
| `layout.rf_right_angle_bend` | P0 | 单源 + 用户硬约束 | ROHM「拐角布线不要设计为直角，设计为45°或圆弧状比较好。」 | 折角一律 45° |

### 3.1 三源冲突必须留在台账里，不许抹平

- **ADI / ROHM**：任何层都不许在电感下方布信号线（含内层、含背面）。
- **NEXTPCB**：「If you must route under an inductor due to severe space constraints, do it on the
  bottom layer of a multi-layer board, ensuring a solid ground plane exists between the inductor
  and the signal traces.」

本仓库**取从严**，所以深层 139 段仍然计为未达标。理由不是「更安全」这种空话，是这条豁免的
前提（电感与信号线之间有一整片实心 GND）在本板 In4 上**无法逐段证明**——而按「宁可报错不给
错答案」，无法证明的豁免等同于没有豁免。如果哪天能证明那一片 GND 完整，这 139 段可以按
NEXTPCB 重新判，但那是新的证据，不是把这条冲突忘掉。

### 3.2 agreement 字段是干什么的

| 值 | 含义 | 代表规则 |
|---|---|---|
| `three_independent_sources_agree` | 三个互不相关的来源说同一件事 | `magnetic_keepout` |
| `sources_conflict_resolved_从严` | 来源之间真有分歧，本仓库选了严格解释并记录理由 | `magnetic_keepout_deep` |
| `derived_from_magnetic_keepout` | 来源没有逐字说这句，但它是来源原句的直接推论 | `magnetic_via_keepout` |
| `single_source` | 只有一个来源，且没有逐字覆盖到这条规则的全部范围 | `crystal_keepout_adjacent` |
| `single_source_plus_user_instruction` | 一个弱措辞来源 + 用户当面硬要求 | `rf_right_angle_bend` |

强度决定级别：`crystal_keepout_adjacent` 之所以是 P2 而不是 P1，就是因为来源只到「下方要好好
接地」和「时钟线远离」，没有一句话明确点出「内层不许布线」。**级别跟着证据走，不跟着我的
判断走。**

## 4 uncited 清单（22 条，逐条点名）

以下规则目前**没有任何外部出处**，`source` 里仍是本仓库技能名。它们在代码里照样跑、照样出
数字，但任何人拿它们来指责本板时，我没有一句话可以递给对方。这张表存在的意义就是不让它们
悄悄混进「已核实」那一栏。

| 规则 | 原有 source | 缺什么 |
|---|---|---|
| `layout.reference_plane_continuous` | kicad-free（回流路径） | 缺 IPC/芯片厂关于参考平面开槽的原文 |
| `layout.decoupling_distance` | kicad-free（同层 <3 mm） | 缺原厂手册对去耦距离的量化要求 |
| `layout.decoupling_same_layer` | kicad-free | 同上 |
| `layout.bulk_per_ic` | kicad-free（每 IC 10 uF） | 「10 uF」这个数没有出处 |
| `layout.stitching_via_pitch` | rf-circuit-design 3.5 | 缺 λ/20 的推导原文或厂商指南 |
| `layout.rf_z0_tolerance` | rf-circuit-design 3.4（50Ω ±5%） | ±5% 缺标准出处 |
| `layout.rf_bend_radius` | rf-circuit-design 3.4（3w） | 「3w」缺出处 |
| `layout.rf_spacing_3w` | rf-circuit-design 3.4 | 同上，3W 规则缺原始文献 |
| `layout.rf_to_board_edge` | rf-circuit-design 3.4（>3 mm） | 「3 mm」缺出处 |
| `layout.antenna_keepout` | rf-circuit-design 3.4（>10 mm） | 「10 mm」缺出处 |
| `layout.layer_change_return_via` | rf-circuit-design 3.4 + kicad-free | 缺原厂对回流过孔的原文 |
| `layout.diffpair_skew` | rf-circuit-design 3.4（<5 mil） | 5 mil 缺出处（DDR 的等长痛点另见 docs/152） |
| `layout.diffpair_gap_constant` | rf-circuit-design 3.4 | 缺出处 |
| `layout.power_trace_width` | IPC-2221 chart + kicad-free 直觉表 | **部分有**：载流由 `calc/current.py` 走 IPC-2221 表，但「直觉表」那部分没有 |
| `layout.power_pad_thermal_vias` | kicad-free | 缺热设计原文（见 docs/86 的辐条电阻推导，但不是同一件事） |
| `layout.magnetic_keepout_graze` | kicad-free（几何口径） | 派生自三源，但「矩形 pad hull 的角算不算」是我自己的几何约定 |
| `layout.crystal_keepout` | kicad-free（同层） | 与 GENUWAY 那句「well-grounded」是间接关系，没有「同层本体下方禁布线」的原文 |
| `layout.crystal_guard` | kicad-free（包地） | 缺原文 |
| `layout.silkscreen_over_pad` | kicad-free | 缺板厂 DFM 原文（`jlc` 库里有丝印能力，但没有「压焊盘」这条判定） |
| `layout.test_points_present` | kicad-free / rf-circuit-design 4.1 | 缺出处 |
| `layout.min_via_hole` | kicad-free（0.3 mm）— 源码自己都写着「a cheap-process convention, not a law」 | 这条**本来就不是规则**，是工艺惯例，应降级或删除 |
| `layout.min_trace_width` | kicad-free（>6 mil） | 与 `jlc` 库重复：真限值在 `jlc-pcb-capabilities.json` 里，这条的自造阈值应改为引用它 |

**注意第 14 行**：`layout.power_trace_width` 不是全 uncited，载流那一半走的是 IPC-2221 表；
写进 uncited 表是因为取限值时混了「直觉表」。宁可把它列进这张表，也不要让它假装全绿。

## 5 这一版没有做的事（不许读成做了）

- **没有把 22 条补上出处。** 本轮只抓到了 4 个来源、覆盖 5 条规则，剩下的 22 条要一条条去
  IPC-2221/2222、各原厂 layout guideline、JEDEC 里找原文，工作量远大于本轮。
- **没有因为「没有出处」就把规则删掉。** 它们仍在跑、仍报警，只是级别与 confidence 应按
  uncited 打折。删规则会把已经量过的真实问题一起删掉。
- **没有改任何一条规则的判据逻辑或阈值。** 本轮唯一的代码改动是 `source` 字符串（变成真出处）
  和新增只读的 `rule_sources` 模块。`audit()` 的数字一个都没动——改动前后 `_v18` 的
  52 条判定应逐条相同，这是本轮的自我对照。

## 6 可复用口径

1. **「我有这个规则」和「我能拿出一句话」是两件事。** 只有后者让规则有分量、能定级、能被别人
   反驳。写规则时 `source` 里填自己的技能名，等于给自己发了一张空头支票。
2. **冲突不要选边就完事，要写进台账。** 选从严可以，但「为什么从严」必须是一句能检验的话
   （这里是「豁免前提在本板无法逐字证明」），不是「保守一点好」。
3. **级别跟证据强度走。** 单源只到「well-grounded」的规则不许拿 P1，否则 P1 这个档次会被稀释，
   到真正的三源一致规则出现时反而没人信。
4. **uncited 表要公开。** 藏一张未核实清单，比没有清单更糟：它会让「5 条已核实」看起来像
   「27 条已核实」。
