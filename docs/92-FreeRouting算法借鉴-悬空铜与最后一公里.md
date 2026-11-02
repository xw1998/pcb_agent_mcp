# FreeRouting 第二轮：悬空铜能查能切，最后一公里只记账

> 版本 `0.0.91`。工具数 **181**（新增 `board_dangling_copper`）/ `available` **175** / `not_implemented_yet` **23** / 自检 **324/324**。
> 上一轮（`docs/91`）看的是**格式层**，结论是「不接 DSN/SES 交换，借五条机制」，其中 A1（锁定的铜不回流）已在 `0.0.90` 落地。
> 这一轮看的是**算法层**：FreeRouting 在写出板子之前和布线收尾时各做一件我们用得上的事——前者这一版做了，后者只记账。

## 92.1 这篇回答什么

用户的原话是：

> 「继续执行，你再看看 https://github.com/freerouting/freerouting」

`docs/91` 已经把 DSN/SES 判死，所以这次不再看 `io/specctra/`，而是把 `autoroute/pipeline/` 下的后处理阶段逐个读完，只保留**与我们的写路径能力天然咬合**的部分。结论先给：

1. **做了一件**：`DanglingCopper`（悬空铜的检测与安全切除）——我们过去完全没有这个能力，而它的判据可以与 `0.0.90` 的 `(locked)` 语义直接拼起来。
2. **只记账一件**：`LastMileBlockerRipup`（最后一公里撕铜）——我们的 `_negotiate()` 已经是它的更强形态（带价格、按 wish 走廊、整轮重布线），单独实现它增量价值不大，等悬空铜落地后再评估。
3. **抄到一条反面事实**：`BoardUpdateStrategy` 这个「策略」枚举**只有一个值**。这提醒我们不要在参数上假装有选择。

## 92.2 取证：结论是从哪来的

`git clone --depth 1` 在这个仓库上约 **1 MB/min**，走不完即终止；改用 GitHub Contents API 逐文件取，稳定且快：

```
https://api.github.com/repos/freerouting/freerouting/contents/<path>
  Accept: application/vnd.github+json          列目录
  Accept: application/vnd.github.raw           取单文件正文
```

（`git/trees/master?recursive=1` 会因响应过大在传输中 `Recv failure`，`curl` 退出码 56，别用它。）

| 文件 | 行数 / 字节 | 用途 |
|---|---|---|
| `autoroute/pipeline/DanglingCopper.java` | 85 行 / 2853 B | **本版实施对象** |
| `autoroute/pipeline/LastMileBlockerRipup.java` | 93 行 / 3048 B | 记账，未实施 |
| `autoroute/BoardUpdateStrategy.java` | **6 行 / 153 B** | 反面事实（只有一个枚举值） |
| `autoroute/pipeline/AutorouteUnroutedReport.java` | 80 行 | 未完成连接的报表，与我们 `board_route` 的 `unrouted` 重叠 |
| `autoroute/RetryNeighbourhood.java` | 99 行 | 重试邻域，与我们的 `_negotiate` 重叠 |
| `autoroute/ItemSelectionStrategy.java` | 7 行 | `SEQUENTIAL, PRIORITIZED` 两值 |

GPL-3.0：**只读思路，不拷代码**。下面凡是写「他们的做法」都是机制描述，我们的实现是独立写的 Python。

## 92.3 `DanglingCopper` 是什么（机制，逐条）

它的 docstring 一句话就是判据本身：

> a via with copper of its net on one layer only and a trace with a free end, with the stub behind each up to the next fork, pin or fixed item

拆开是三条：

1. **什么算悬空**：① `netCount() == 1 && isTail()` 的铜——一个 via 只在一层上有本网的铜；② 有自由端的 trace。
2. **删到哪儿为止**：把每个尾巴**背后**的 stub 一路删到下一个 **fork / pin / fixed item**（`RoutingBoard#removeTraceTails`）。所以它切的不是「一整条 track」，而是「一条 track 的一段」。
3. **为什么最多 16 轮**：`private static final int MAX_PASSES = 16;` 注释写得很清楚——**删掉一个 stub 可能让它后面的 via 只剩一层铜**，于是下一轮又多出一个悬空 via。这是串行收敛，不是并行一遍。

护栏有两条：

- **用户放置的铜是 fixed，保留。** 这也正是 `0.0.90` 做的 A1：`(locked)` 的铜任何人都不许回流。
- **整体 undo 的条件**：删完先算每个受影响网的 `NetIncompletes`（开路连接数），**如果上升就整轮撤掉**。源码注释自己承认这条检查是**护栏而非判据**：「the open count cannot rise by construction, the check is the guard」。

日志格式（写进他们日志的那一句）：

```
Dangling copper after <where>: removed N via(s) and M trace piece(s) behind K tail(s)
in P pass(es); X connection(s) of their nets open, as before.
```

## 92.4 `LastMileBlockerRipup`：只记账，暂不实现

机制：当只剩少数未完成连接、且分数不再改善时，把**落在剩余 airline 走廊内**的外来铜撕掉，给最后几条连线腾空间。

- 上限 `MAX_ITEMS = 24`（两条入口各判一次）；
- 只撕 `Trace` 与 `Via`；`isDeletionForbidden()` 或 `isUserFixed()` 一律不动；**自己这条 airline 的网不许撕**；且 `netCount() > 0`（不属于任何网的孤铜也不在目标里）；
- 走廊 = airline 两端点的**轴对齐 bbox**，`span = max(Δx, Δy)`，`margin = Math.max(span / 4, 1)`（单位是内部整数坐标，1 = 最小格）。

**为什么不实现**：我们的 `route/router.py::_negotiate()` 已经是它的更强形态——按 wish 走廊算受害者的历史价格、每轮撕 `max_rips` 个、保留最好一轮。`LastMileBlockerRipup` 的增量在于「不带价格、只在最后几条时触发、有 item 级上限」，这三条在 `_negotiate` 里可以表现为参数，而不是新阶段。**记在这里，等悬空铜跑一段真板之后再决定要不要加。**

## 92.5 不抄的，以及为什么

| 文件 | 不抄的理由 |
|---|---|
| `BatchOptimizer`（51 KB） | 后布线批量重优化，属于「整板重排」量级，与我们「改一个就验一个」的写路径模型冲突 |
| `MazeTraceShove` / `MazeTraceShover` | 以**整网**为粒度做侧推，我们的写路径是单段/单节点粒度，语义对不上 |
| `ExitStubs` / `TwoLayerBga` | BGA 扇出，我们 `board_fanout` 已有 |
| `DrillPage` | 我们没有 cam 层需求 |
| `AutorouteUnroutedReport` / `RetryNeighbourhood` | 与 `board_route` 的 `unrouted` 与 `_negotiate` 重叠，没有增量 |

**一条反面事实值得单独写下来**：`autoroute/BoardUpdateStrategy.java` 整个文件 6 行，内容是

```java
public enum BoardUpdateStrategy {
  GLOBAL_OPTIMAL
}
```

也就是说这个「策略」**只有一个可选值**——等于没有策略。这是一条对我们的直接提醒：**参数上有枚举不等于真的有选择**，所以我们加任何 `mode`/`strategy` 类参数时，要么两个值都有可观测的行为差，要么就别暴露它。

## 92.6 我们落地了什么

### 92.6.1 新模块 `src/pcb_agent_mcp/dangling.py`（607 行）

三条**属于我们自己的读数**，都写在模块 docstring 里，也写在工具描述里：

1. **「attached」是同一套铜模型里的几何断言**：一段铜的点落在另一段的宽度内、via 的尺寸内、pad 的 reach 内、或同网 `filled_polygon` 内 ⇒ 附着。**只是靠近不算，间隙就是间隙**（`TOL_MM = 0.002`，可被 `tolerance_mm` 改）。
2. **「cuttable」是证明不是希望**：一条链只有**一个**附着点，且它**不是任何 pad 的唯一铜** ⇒ 切它**不可能**开连接。做不到这两条就报 `why_not`（`locked` / `would_open_a_pad`），而不是「大概没事」。
3. **zone 尊重而非忽略**：同网 `filled_polygon` 才算铜；keepout 不是铜；**没 fill 的板子没有这种铜，报告如实说「不猜」**。

与 FreeRouting 的两处**有意的不同**，写在 docstring 里：

- **切除单位是整节点**（KiCad 的 `segment` / `arc` / `via`），不是他们那种「track 中段切除」。理由是：我们的写路径 op 只能整节点删，切一半会造出一个谁也没定义过的端点。
- **不重复他们那种「最多 16 轮」**：我们一次 survey 就把「死端 via 并入链」做完（`_absorb_dead_end_vias` 在自由端与空中端点各吸收一次），不存在「删了 stub 才暴露下一个 via」的串行收敛——因为我们的链本来就是一整条走到头。

### 92.6.2 两个新写路径 op（`edit/ops.py`）

悬空铜要删的是**两类节点**，原来只能删 `segment`：

- `delete_via`（新）：参数 `copper`，错误码 `ARG_MISSING` / `TARGET_NOT_FOUND`，走 `_refuse_fixed`（`allow_fixed=true` 才动 locked 的 via）；
- `delete_track` 加 `include_arcs`（默认 **false** ⇒ 既有答案逐位不变）。

### 92.6.3 新工具 `board_dangling_copper`

参数：`path`（必填）、`apply`、`include_floating`、`max_report`、`tolerance_mm`、`allow_new_violations`、`strict_drc` + `_COMMON`。

- **默认只报告**：`writes_nothing: True`，`next_step` 提示怎么 apply。
- **没有可切的就绝不造空计划**：`message` 是 `"no dangling copper"` 或 `"nothing on this board is both dangling and safe to cut"`，`apply=true` 在干净板上返回的是**拒绝编造**，不是一个空 plan。
- **浮铜默认不切**：`include_floating=true` 才切。理由是「有人故意放的测试点」不是这个工具能替人判的缺陷。
- `apply=true` 走 `propose → apply_plan(confirm=True)`，**写后再 survey 一次**放进 `value["after"]`，让「切干净了没有」成为写路径自己的读数。

### 92.6.4 顺带修掉的一处读写分歧

`adapters/kicad_pcb.py::_uuid` 原来只认 `(uuid "<x>")`（KiCad 8 起），而 KiCad 7 写的是裸 atom `(tstamp <x>)`。`edit/build.py::id_of` 早就两种都读，读的那一侧没跟上——**同一个 id 在读路径和写路径上拼写不同，就是「读得到的不一定能删」**。这一版两个都读。

## 92.7 判据与负控制（22 例，逐条都能失败）

`tests/test_dangling.py`（464 行，22 例）：

| 类 | 用例要点 | 负控制 |
|---|---|---|
| `WhatCountsAsATail` | T 型尾被认出；主干不是尾；pad 尾被拒（`would_open_a_pad`）；locked 尾被拒；浮铜单列且默认不切；**弧也是铜** | 主干/活分支必须不报 |
| `ViasAndPours` | 死端 via 随链切；铺铜算附着；keepout 不是铜 | **活 via** 杀掉尾巴后不再报 |
| `TheGapIsReal` | 0.002 mm 下两段不相连；0.5 mm 下并成一条链 | 同一块板、改容差就改答案 |
| `ACleanBoardReportsNothing` | 干净板报 0 且 `writes_nothing` | 报出任何东西即失败 |
| `WritingTheCut` | 只报告不写；切后其余全在；浮铜须显式；op 的 uuid 真在文件里 | 其余五段必须仍在 |
| `TheCutRefusesLockedCopperAtTheOpLayer` | locked via 报 `ARG_CONFLICT` 并点名 uuid、提示 `allow_fixed=true`；`allow_fixed` 后真删；locked via 让整条 tail 不可切 | 拒绝那条必须不落盘 |

Monkey test 之外的实测（本轮跑的）：`test_dangling` **22/22**；相关子集 `test_edit` + `test_ops_edit` + `test_route_fixed` **110 例全绿**；`test_kicad_pcb` + `test_fidelity` + `test_tools` + `test_route` **149 例全绿**；`--selftest` **324/324**（新增三条 9101/9102/9103：四读一起 / 切只切该切的 / 干净板拒绝编造）。

全量回归（3493 例）按用户指令「功能全部做完后再批量测」留到收口时集中跑。

## 92.8 补：这工具在真实板上的第一课 —— 焊盘在哪，答案就在哪

0.0.91 发布后拿用户自己的 20 块立创成品板当语料（只读复制到 `_scratch/`，不入库）跑了一遍，
抓到一个会**说假话**的地方：一块 6 层开发板报出 342 条 tail、170 片浮铜、310 条「可切」。
逐条核对自由端与焊盘的关系之后，真因不在悬空铜判据，而在**焊盘**：

| 发现 | 取证 | 后果 |
|---|---|---|
| `.epcb` 的通孔焊盘被写成单层（`layers: ["TOP"]`），走线从反面过来就算「没接到」 | 全语料 235 个通孔焊盘 100% 单层；64 个 `BOTTOM` 走线端点精确落在通孔焊盘中心 | 把「已连接」误报成「悬空」 |
| 修法：焊盘自己的 `PAD[4]` 层码 **12＝多层**，与「有孔」严格互为充要 | 6 工程 4724 焊盘：`('1',无孔) 4308 / ('2',无孔) 3 / ('12,有孔) 413`，零例外 | 修后该板 342→338 tail、170→99 浮铜 |
| 底面元件**不镜像** | 2549 个带网焊盘逐假设比对：不镜像 62.1%/82.6%，`-y` 37.0%/53.2%，异网误撞 1/2 | 旧文档写的「`-y` 35.1%」量的是错假设，已更正 |
| 位置仍是**推导**，不是核过的 | `pad_transform.verified_by_app` 一直是 `false` | 见下 |

最后一条决定了工具必须**先问焊盘能不能信，再谈切不切**。`dangling.pad_trust(board)` 把这件事
写成两条事实的合取：焊盘几何确实读到了（`present["pads"]["present"]`），且位置要么构造上就精确
（KiCad），要么经过应用核验（`source["pad_transform"]["verified_by_app"] is not False`）。
任一条不成立 ⇒ 每条 tail 与每片浮铜都带 `cuttable: false`、`why_not: "pad_placement_untrusted"`，
`removal_ops` 返回空列表，工具信封里 `pad_trust` 说明是哪一条不成立、为什么。**列候选不等于判可切**——
这正是 FreeRouting 那条判据的另一半：证明不了「它接不到任何东西」，就不许删。

新增 6 例（`PadTrust` 4 例 + `ThroughHoleSpansEveryLayer` 2 例），全部带能失败的对照：
把 `layers` 改回单层、把 `verified_by_app` 改回 `false`、把 `present["pads"]` 改回 `None`，
三种改法各自都会让对应用例红。

## 92.9 一句话

FreeRouting 这一轮给我们的不是算法，而是一句**判据**：**「删得掉」不等于「该删」——先证明它不可能开连接，再动手**；另外附赠一条反面提醒：**只有一个枚举值的策略等于没有策略。**
