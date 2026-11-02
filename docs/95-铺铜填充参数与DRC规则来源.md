# 95 - 铺铜的填充参数，与 DRC 的规则来源

两件事在同一块真实板上同时显形，所以放在一篇里：**一个字段写不写决定了 386 条 clearance 的真假**，
**一份 `.kicad_pro` 在不在旁边决定了 515 条 error 的真假**。两者的共同点是——
它们都不在铜上，而在「KiCad 怎么读这份文件」上。

## 1. 386 条 clearance：差 0.05 mm 的那一半

板子：STM32F412RG 核心板，199 412 B，两个内层铺铜（GND / In1.Cu、+3V3 / In2.Cu），
`min_thickness = 0.1`、`connect_pads.clearance = 0.25`。

kicad-cli 10.0.7 的判决：**386 条 `clearance` error**，描述全部长一样——

```
间距违规 (填充区 间距 0.2500 mm; 实际 0.2005 mm)
```

同一时刻，本工具自己的几何 pass 对同一条边量到的是 **0.2505 mm**
（polygon 边缘到过孔半径；GND 5 780 个顶点、+3V3 6 059 个顶点逐个障碍算，直方图 median 也是 0.2505）。
所以「实际」比文件里的多边形小 0.05 mm，而 0.05 正好是 `min_thickness / 2`。

原因：KiCad 的 zone 有一个 `(filled_areas_thickness yes|no)` 字段，含义是

- `no`：存下来的 `filled_polygon` **就是这个铺铜的铜边缘**；
- `yes`（以及**字段缺失**时的缺省）：`filled_polygon` 是一根**中心线**，真正的铜要沿它按
  `min_thickness/2` 描边加粗。

这块板的 zone 没有这个字段 ⇒ KiCad 按 yes 读 ⇒ 每条边比文件外扩 0.05 mm ⇒
`0.2505 − 0.05 = 0.2005`，与 DRC 的读数逐位对上。

三档对照（同一份字节、同一个 `.kicad_pro`、同一台 kicad-cli）：

| zone 里的字段 | violations | error | clearance |
|---|---|---|---|
| 缺失 | 536 | 386 | 200 ×「0.2500 / 0.2005」 |
| `yes` | 536 | 386 | 200 |
| `no` | 150 | **0** | **0** |

补字段 + 重填之后的整板 DRC：

```
violations_total  504 → 150
clearance         386 → 0
by_severity       150 全 warning
by_type           via_dangling 2, silk_edge_clearance 2, holes_co_located 28,
                  hole_to_hole 16, lib_footprint_issues 32, text_height 32,
                  silk_overlap 14, silk_over_copper 24
```

**警告一条没少**。这不是放宽规则，是把该报的留住、不该报的拿走。

### 为什么不是「工具自己猜」

`filled_areas_thickness` 描述的是**存下来的多边形是什么意思**，只有写这个多边形的那个程序知道：
KiCad 自己填的、按 no 存；某些工具/旧版本写出的文件没有这个字段，KiCad 就按 yes 读。
所以本工具**不默认给每个 zone 补 no**，而是提供一个能改的 op，并如实回报「填充已作废、要重填」。

## 2. `set_zone_params`（第 39 个写 op）

```
set_zone_params { filled_areas_thickness: yes|no|bool,
                  clearance: mm, min_thickness: mm,
                  net | layer | uuid }
```

- **字段落点照 KiCad 自己的顺序**：`connect_pads (clearance …)` → `(min_thickness …)` →
  `(filled_areas_thickness …)`。位置对解析器不重要，但对 diff 重要——「本工具写的」与「KiCad 写的」
  应该只差值本身。
- **`clearance` 进 `connect_pads`**，不是 zone 顶层；没有 `connect_pads` 就新建一个。
- **旧填充必须作废**：`filled_polygon` 是旧参数下的铜，留着就是「文件说一套、板说另一套」。
  丢几个如实回 `dropped_filled_polygons`，并回 `refill_required: true`；之后要么
  `board_fill_zones`，要么让 EDA 自己填。
- **keep-out 没有填充参数**：命中 keep-out ⇒ `ARG_INVALID` 并说明，不硬塞 `clearance`。
- **空参数 ⇒ `ARG_MISSING`**；selector 打空 ⇒ `TARGET_NOT_FOUND`。
- **参数已经等于目标值时仍会重铺**，所以 `zones_changed`（值变了的）与 `zones_matched`（被这个 op
  点名的）分开报；「0 changed / 2 matched / 2 dropped」是一个真实状态，不是失败。

## 3. DRC 的判决要对着同一套规则读

`fidelity.engine.drc(path, project=...)`。KiCad 从**板旁边的** `.kicad_pro` 取两样东西：

- **设计规则下限**（最小线宽、最小过孔、最小间距）；
- **规则的严重度**（同一条 `silk_overlap`，工程里是 warning，缺省是 error）。

同一份 199 KB 字节，两种读法：

| 旁边有 `.kicad_pro` | violations | error | 说明 |
|---|---|---|---|
| 没有 | 665 | **515** | drill_out_of_range 158、track_width 199、via_diameter 158，14 条 silk_overlap 从 warning 变 error |
| 有 | 150 | **0** | 上面四类全部消失或回到 warning |

所以 DRC 差集的**两半必须同规则**：写路径里「改前」那一半是 checkpoint 副本，住在服务自己的
home 里，旁边没有工程文件；「改后」那一半在用户目录里，旁边有。两半不同源，就会把 14 条老丝印
算成 14 条新增错误。

现在的做法：

1. `drc(path, project=...)` 显式给规则；不给时自动去板旁边找同名 `.kicad_pro`；
2. 找到就连**板 + pro 一起**拷进临时目录（按板的 basename 命名，这样 KiCad 才配得上对），
   用户目录一个字节都不写；
3. 信封里说清这次读的是哪套：`project_file_used` / `rule_source`（`project` 或 `kicad_defaults`）/
   `rule_source_note`。副本旁边没有 pro 时**明说**用的是 KiCad 缺省规则，而不是让它看起来像工程规则。

## 4. 边界（这些仍然不算）

- **不重算填充**：`set_zone_params` 只改参数并作废旧多边形；铜是谁填的仍是 EDA / `kicad-cli`。
- **不判断你该不该设 `no`**：它描述的是文件里多边形的性质，改错了只是让 KiCad 读错，本工具替不了这个判断。
- **0.05 mm 这个差只在特定条件下显形**：得存在 `filled_polygon`，且 `min_thickness/2` 大于该处
  多边形到障碍的实际余量。没有填充的 zone 谈不到这件事。
- **DRC 仍然只是 KiCad 的意见**：规则来自工程文件，规则本身对不对不在这里判。

## 5. 判据落在哪里

- `tests/test_ops_edit.py::ZoneParams`（7 例）：字段落点 / `yes` 拼写 / 旧填充作废 / `clearance` 进
  `connect_pads` / 空参数 / 选择器打空 / keep-out 拒绝。
- `tests/test_engine_drc_project.py`（6 例）：显式 pro 的暂存配对、跨目录 pro 仍按板的 basename 落地、
  自动配对、无 pro 要明说、输入不被写、差集用的是哪一套。**全部 fake 掉 kicad-cli**——被测量的是暂存与信封，
  不是 KiCad。
- `--selftest` 一条：`board_propose` → `board_apply` → 读回文件里的三个字段，再验 keep-out 被拒。

## 6. `connect_pads` 的三种模式：文件写了什么，KiCad 读成什么

zone 头上的 `connect_pads` 有两种写法，值域不是「有/无」而是三态：

| 文件里写 | pcbnew 读出来的 | 含义 |
|---|---|---|
| `(connect_pads yes (clearance 0.25))` | `ZONE_CONNECTION_FULL` = 2 | 实连（整片铜直接贴焊盘） |
| `(connect_pads no (clearance 0.25))` | `ZONE_CONNECTION_NONE` = 0 | 不连 |
| `(connect_pads thru_hole_only (clearance 0.25))` | 第三种 | 只连通孔 |
| `(connect_pads (clearance 0.25))` —— **没有模式词** | `ZONE_CONNECTION_THERMAL` = 1 | 热焊盘（十字辐条） |

最后一行是关键，也是反直觉的一行：**缺省是热焊盘，不是实连**。KiCad 自己保存一个 thermal zone
的时候就不写模式词，所以「没有词」= 「thermal」是 KiCad 的规范写法，不是数据缺失。

旧解析层把这条读反了（无词 → `solid`），于是：

- 它读别人的板会把 thermal 说成 solid；
- 它自己写的 zone 只写 `(connect_pads (clearance …))` 却被自己读成 solid，读回与写出的口径不一致。

现在两件事分开报：`connect_pads` 是**文件里写了什么**（没有模式词时就是 `null`），
`connect_pads_effective` 是**KiCad 会怎么读**（`thermal` / `solid` / `none` / `thru_hole_only`），
后者带 `connect_pads_source`（`file` 还是 `default`）。判据以「加载回 pcbnew 读值」为唯一权威，
不靠「缺省更可能是什么」去猜。

## 7. `add_zone` 写完整原生头部

以前写出来的 zone 只有 `net / layer / hatch / connect_pads(clearance) / min_thickness / fill yes /
polygon`，缺三样东西，而这三样都决定 KiCad 怎么读这份铜：

1. `(connect_pads (clearance X))` 里的**模式词**（见上一节）；
2. `(fill yes (island_removal_mode 0))` 里的 **`thermal_gap` / `thermal_bridge_width`**（热焊盘用）；
3. **`(filled_areas_thickness no)`**——见第 1 节，这一条是那块真实板 386 条 clearance 的根因。

现在按 KiCad 自己的顺序写全：

```
(zone (net N) (net_name "X") (layer "L") (uuid …)
  (hatch edge 0.5)
  (connect_pads [yes|no|thru_hole_only] (clearance C))
  (min_thickness T)
  (filled_areas_thickness no)
  (fill yes [(thermal_gap G)] [(thermal_bridge_width W)] (island_removal_mode 0))
  (polygon …))
```

不写模式词就是 thermal（KiCad 的规范写法），`solid` 只在调用方明确要的时候写。
热焊盘的 `thermal_gap` / `thermal_bridge_width` **默认不写**——不写等于 KiCad 自己的 0.5 / 0.5，
写一个数值就是替调用方做决定，而这两个数没有「更可能的缺省」。

## 8. 填充成功 ≠ 铺铜合格

`board_fill_zones` 以前只回答「引擎有没有把铜填出来」。那块真实板上，filler 返回成功、
文件里躺着 386 条 clearance error——**两个不同的问题被一个 ok 混成了一个**。

现在 `fill()` 在写完（或算完）之后自己跑一次 KiCad DRC，把结果放进 `value.drc_after_fill`：

- `ran`：DRC 跑没跑起来（跑不起来就 `verdict: "unknown"` + `why_it_matters`，不假装通过）；
- `verdict`：`clean`（error 0 且 unconnected 0）/ `not_clean` / `unknown`；
- `errors` / `warnings` / `unconnected_total` / `by_type` / `top_errors`（每条带
  `geometry`：约束值、实际值、差多少）；
- `rule_source` / `project_file_used`：这次判决读的是哪套规则；
- `measured_on`：`the written file`，或者 `a temporary copy (this call wrote nothing)`
  ——测量模式不写文件，此时若拿原文件去跑 DRC，测到的是**铺铜之前的轮廓**，那等于什么都没测。

同时写一条 `assumptions.fill_success_is_not_pour_quality`。工具层用 `drc`（默认 true）开关它。

写路径里的门禁（`gates.zone_fill`）只对**新增**错误阻塞：铺铜之前就存在的错误不该让填充变成不可用，
于是把 `drc_errors_before_the_pour` / `drc_errors_added_by_the_pour` 分开报，`added <= 0` 时只写
`warning`，`added > 0` 才 `ok = false`。

## 9. DRC 违规的几何量：只能从描述里读

`kicad-cli pcb drc --format json` 给每条违规四样东西：`type`、`severity`、`description`、`items[].pos`。
**没有**「要求多少、实测多少」这两个数——它们只存在于那句**本地化**的 `description` 里：

```
间距违规 (填充区 间距 0.2500 mm; 实际 0.2005 mm)
钻孔太靠近其他孔 (电路板配置约束 最小 0.2500 mm; 实际 0.0000 mm)
Clearance violation (Zone clearance 0.25mm; actual 0.2005mm)
```

所以调用方想算「差多少」只能自己写解析器（参考会话就写了 `measure_gap.py`）。现在
`fidelity/drc_geometry.py` 统一做这件事，给每条违规补：

- `numbers_mm`：描述里所有带 `mm` 的数；
- `geometry.constraint_mm` / `actual_mm` / `shortfall_mm`（= 约束 − 实际，正数表示不够）/
  `ratio` / `order`（`from_keyword` 或 `assumed`）/ `source`；
- 描述里没有一对毫米值时：`geometry: null` + `geometry_absent_because` 说明原因，
  **绝不补 0，绝不猜**；
- 没有 `actual` 词时按「先约束后实测」读，但标 `order: "assumed"` 并给 `caveat`。

同一个几何量还会透到 `drc_policy.classify` 的阻塞条目、写路径的 DRC 差集、以及
`board_fill_zones` 的 `top_errors` 里，所以「新出现的错误在哪、差多少」在报错文案里就有。

写路径的 DRC 阻塞文案以前只有数量（"13 NEW DRC error(s) (clearance, shorting_items)"），
现在带位置与几何：

```
1 NEW DRC error(s) appear after this edit (clearance): clearance at (12.34, -5.67)
(wanted 0.25 mm, got 0.2005 mm); pass allow_new_violations=true to accept them,
or use board_drc(options) to see every one
```

## 10. 删除是一批对象，不是一个 uuid

`delete_zone` 的后置条件原来是「这个 selector 不再匹配任何 zone」——语义错了：它删的是一批具体对象，
而 selector 会匹配到「同 net/layer 里后来又加回来的那个」。于是同一次 apply 里「删掉旧 zone、加一个新
zone」必然 `GATE_BLOCKED`，报错只说 postcondition failed / `remaining_matches: 1`。

现在：

- **后置条件按 uuid 判**（`checked_by: "uuid"`，回 `deleted_uuids` / `still_present` /
  `remaining_matches_are_other_zones`）——删的是不是那批对象，只有 uuid 答得了；
- `propose` 会把这类组合**先说出来**：`plan_conflicts` 里一条
  `delete_then_add_same_zone`，带 `what_was_fixed` 与 `what_to_do`（优先用 `set_zone_params`）；
- 后置条件失败时的文案带上 `failed_detail`（哪个 op、第几个、查的是什么）。

另加 `delete_copper`：按 `net` / `layer` / `region`（mm，Y 向上）批量删 `tracks` / `vias` / `zones`，
不必再 ripup 出 528 个 op。zone 只有**整个 outline 都在 region 内**才删，跨界的记进
`zones_skipped_partial` 并给 hint；`locked` 对象拒绝删（`_refuse_fixed`）。

## 11. 试穿一次再决定：`dry_run`

`board_apply(options={dry_run: true})` 走**同一条**写入 + 全部门禁 + 还原字节的路径，
而不是跳过写入：只在写完之后才存在的门禁（字节复核、重解析、KiCad 重读、DRC 差集、postcondition）
如果被跳过，那「dry run 通过」就是一句空话。

返回 `status: ok` / `would_write` / `file_restored_byte_for_byte` / `sha256_unchanged` / `gates` /
`decision` / `plan_still_open: true`。被门禁拦下的计划不抛异常堆栈，而是
`status: ok, dry_run: true, would_write: false, message: "DRY RUN: this plan WOULD be refused…"`，
`blocking` 原样带出——它本来就是要给调用方看的东西。

## 12. 回包有两种形态，所以回包自己说清是哪一种

同一个 `call_tool` 返回两种信封：

| 形态 | 工具 | 业务字段在哪 |
|---|---|---|
| `value` | `board_zones` / `board_fill_zones` / `board_pour_connectivity` / `board_drc` | 在 `value` 里（外面是 `status`/`unit`/`model`/`basis`/`precision`） |
| `top_level` | `board_propose` / `board_apply` | 在**顶层**（`plan_id` / `gates` / `blocking` / `operations`），没有 `value` |

改名会打断所有既有调用方，所以**一个字段都没动**，只加 `payload_is` 与一句 `where_to_read`。
这一条是踩出来的：按 `value["zones"]` 去读一个 `top_level` 回包，读到 0，而文件里有 2 个——
**对一个正确回包的错误读法**，是最糟的一种错。

## 13. `board_pour_connectivity` 不是连通性判决

它做的是「每个焊盘中心点是否落在铺铜多边形内」。看不见的东西：热焊盘辐条、过孔、别的层的同网铜、
焊盘自己的铜、走出焊盘的走线。参考会话在这上面读出了 10 个 GND + 3 个 +3V3 的 "isolated"，
而同一块板 KiCad DRC 的 `unconnected_total` 是 **0**——13 个全是假象。

所以现在：`value.authoritative = false`、`value.use_instead = "board_drc().unconnected_total"`、
`model` 里写明 `NOT a connectivity verdict`，并默认**连带跑一次 DRC**，把权威结论放在
`value.drc_cross_check`（`authoritative` / `unconnected_total` / `errors` / `rule_source` /
`verdict_source`）。`cross_check: false` 可以关掉。

## 14. 每网生效的规则，不是板级缺省

`router.plan` 的 `stats.rule_bundle` 打的是 `Default` 类（0.15 / 0.25 / 0.6-0.3）。实际每网用的是
`Fanout` 类（0.15 / 0.15 / 0.45-0.2），于是「为什么这条走线按 0.15 布」在回包里找不到依据。

现在 `route/rules.py::per_net_summary` 把 bundle 按 (clearance, track, via_dia, via_drill, source)
分组，给 `groups` / `groups_total` / `nets_with_an_entry` / `board_default`，塞进
`board_route` / `router.plan` 的 `rules_in_effect`；`assumptions` 加一条明说
`clearance_mm` / `track_mm` / `via_mm` 是**板级缺省**，不是各网的值。

## 15. 三套异常接口对齐

`RouteError` 有 `.message/.reason_code/.hint`，`jobs.JobError` 也有全套，`OpError` / `EditError`
原来只有一个 `str`。现在补齐 `.message/.reason_code/.hint`（`.code` 保留为别名），
调用方不必再按「这是哪一套」写三份 `except`。
