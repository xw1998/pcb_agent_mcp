# 阶段 4g —— 真板上等长到底能做到什么：逐段落齿早就在代码里，剩的是出线栅栏与 0.2 mm floor 的一笔交换

`197` 收尾时留了一句推测：「`tune_board` 是对一条网只下一个 `too_shallow` 结论，不会挑哪一段有空气」。这一轮**先量后写**，把这句话推翻了：`plan()` 本来就是逐段枚举的，真板 12 网一次跑下来有 10 条网真的落下了齿。剩下的两条才是真瓶颈——出线栅栏的 clearance（要 0.105、走廊是 0）与「尊重 0.2 mm apply floor 就得少买长度」的交换。后者原来只能靠 `allow_new_violations` 或压低 `precheck_clearance_mm` 绕过，本轮把它变成工具上的一个开关：**`board_tune_length` 新增 `respect_apply_floor`**（默认关，关时行为与体积零变化）。

## §1 结论先行

1. **「按段选点」这个能力已经存在**，不需要新建。`plan()` 对一条网是「按段长降序逐段试 → 每段再试振幅梯度（`AMPLITUDE_LADDER`）与两侧 → 一段不行换下一段」，`inventory[net].remove(seg)` 之后把新齿当新段放回去，`while deficit > tol` 继续下一颗。真板读数：一趟 `style=chamfer` 落下 **244 op / 23 个原始段被替换 / 221 条新段**，12 条网里 **10 条真的下到了齿**（`/DDR_CLKP` 21 颗、`/DDR_DQ1` 5 颗、`/DDR_DQ5` 6 颗……），只有 `/DDR_DQ4`（本来就最长、零缺口）与 `/DDR_DQS0N`（0 颗，见 §3）没有。
2. **残余不匹配点名的全是出线栅栏**：`/DDR_DQ3` 的拒绝是 `clearance needed 0.105 actual 0.0`、对手是 `/DDR_DQ1` 的 via `@(21.000,22.311)`；`/DDR_DQS0N` 对手是 `/DDR_DQ3` 在 `(20.15,22.95)` 的 track；`/DDR_DQS0P` 对手是 `/DDR_DQS0N` 在 `(19.9,22.3)` 的 track。y≈22.3–23.3 就是 `196 §2` 算出来的那道 0.5621 mm 球距的栅栏——**代码层的读数与几何层的上界对上了**，不是算法不行。
3. **0.2 mm floor 是一笔明码交换**（本轮新增开关的那一半）：默认档（`floor_mm=0`，只按各网自己的 DRC 净距判）把 skew 从 6.468 压到 4.800 mm，代价是**新增 35 对 <0.2 mm 的异网对**（最紧 0.1085 mm，位于 `(21.0,14.45)`，两对都是 `/DDR_DQ5`）；开 `respect_apply_floor` 后 **new_pairs = 0、ops 从 244 降到 114**，代价是 skew 只到 **5.930**——也就是说，**这 1.130 mm 的改良是完全由那 35 对贴到 0.10–0.11 mm 的铜买来的**（占默认档总改良 1.668 mm 的 68%）。
4. **`style=arc` 在这块板上更差，且有一处能力缺口**：405 op、new_pairs **259**、skew 只到 5.018；`/DDR_DQ2` 那 0.141 mm 的缺口它根本补不上（`does_not_fit`）——圆弧蛇形的齿数是 `range(2, 65, 2)`，最少 2 颗，**表达不出小于一颗齿的增量**。这解释了为什么 `197` 的连续段读数再宽，也不能简单换成 arc 就解决。
5. **把整块板给一条网、邻居不争，7 条仍然吞不下自己欠的长度**（§7 `headroom`）：组判 `blocked_by_geometry`，binding net `/DDR_DM0` 差 **4.800 mm**——与默认档最后一颗齿之后的残余**同值**。所以这 4.800 mm 不是搜索不够，是 `/DDR_DM0` 的几何上限；调路由参数再多次也不会变，只能回布局。

## §2 怎么读的

探针 `D:/tmp/_tune2.py`（不进仓库）：读 `_rsv2_contract_p2_pinch3.json` 的**板文件文本**，把该 plan 的 118 条 op 逐条塞进 `board.tracks` / `board.vias`（plan 的铜不在板文本里，不塞就会去调一条没布过的板），再用 `rules.resolve(clearance_mm=0.105, track_mm=0.1, via 0.2/0.1)` 建 bundle，然后对一个组跑 `TB.plan()`：

- 组 = lane0 九条 + `DQS0P/N` + `CLKP` 共 12 网，`tol_mm=0.10`；
- 参考长度 = 该组最长成员 `/DDR_DQ4` 的 **17.808 mm**（不是外部给定目标）；
- 三档：`chamfer/floor 0`（默认）、`chamfer/floor 0.2`（= 新开关打开）、`arc/floor 0`。

## §3 三个读数

| 配置 | ops | 原始段被替换 | 新增段 | new_pairs(<0.2 mm) | skew 前 → 后 | 用时 |
|---|---|---|---|---|---|---|
| `chamfer`，`floor_mm=0`（**默认**） | 244 | 23 | 221 | **35** | 6.468 → **4.800** | 16.7 s |
| `chamfer`，`floor_mm=0.2`（**`respect_apply_floor`**） | 114 | 9 | 105 | **0** | 6.468 → **5.930** | 19.9 s |
| `arc`，`floor_mm=0` | 405 | 11 | 194 | **259** | 6.468 → 5.018 | 28.2 s |

逐网读数（默认档 / 尊重 floor 档，`meanders` 颗数、`floor` = 落在 apply floor 以下的段数）：

| 网 | 长度 | 缺口 | 默认：齿/floor | 尊重 floor：齿/floor | 尊重档的拒绝理由 |
|---|---|---|---|---|---|
| `/DDR_CLKP` | 11.340 | 6.468 | 21 / 0 | 21 / 0 | `does_not_fit`（剩余段 0.5 mm） |
| `/DDR_DQ0` | 15.755 | 2.054 | 1 / 0 | 1 / 0 | — |
| `/DDR_DQ1` | 11.878 | 5.930 | 5 / 10 | **0 / 0** | `clearance` 0.2 vs `/DDR_DQ0` track `@(21.0,22.3)` |
| `/DDR_DQ2` | 17.667 | 0.141 | 1 / 1 | **0 / 0** | `clearance` 0.2 vs `/DDR_DQ4` track `@(20.45,21.959)`（实际 0.1475，差 0.0525） |
| `/DDR_DQ3` | 13.415 | 4.394 | 5 / 7 | **0 / 0** | `clearance` 0.2 vs `/DDR_DQ1` via `@(21.000,22.311)` |
| `/DDR_DQ4` | 17.808 | 0 | 0 / 0 | 0 / 0 | 参考网，无缺口 |
| `/DDR_DQ5` | 12.670 | 5.138 | 6 / 6 | **1 / 0** | `clearance` 0.2 vs `/DDR_DQS0P` track `@(19.6,22.5)` |
| `/DDR_DQ6` | 16.014 | 1.794 | 3 / 6 | 4 / 0 | `clearance` 0.2 vs `/DDR_DQ5` track `@(19.05,22.95)` |
| `/DDR_DQ7` | 16.487 | 1.321 | 2 / 1 | 2 / 0 | — |
| `/DDR_DM0` | 12.790 | 5.018 | 2 / 2 | 1 / 0 | `clearance` 0.2 vs `/DDR_DQ6` via `@(19.064,22.973)` |
| `/DDR_DQS0N` | 15.049 | 2.760 | 0 / 0 | 0 / 0 | `clearance` 0.2 vs `/DDR_DQ3` track `@(20.15,22.95)` |
| `/DDR_DQS0P` | 14.623 | 3.186 | 1 / 2 | 0 / 0 | `clearance` 0.2 vs `/DDR_DQS0N` track `@(19.9,22.3)` |

三条读法：

1. **默认档的 35 对不是「压到规则边」而是压到 0.1085–0.1475**：最紧的两对都是 `/DDR_DQ5` 在 `(21.0,14.45)` 与 `(20.675,14.125)`，0.1085 mm——比 net rule 的 0.105 只多 0.0035 mm。这不是「规划器算错了净距」，是它按规则把空气用满了；**这块板的走廊本来就只值这么多**。
2. **尊重 floor 的档不是「什么都画不出来」**：它仍然落下 114 op、9 个原始段被替换，`/DDR_CLKP` 21 颗、`/DDR_DQ6` 4 颗、`/DDR_DQ7` 2 颗——**凡是走廊里真有 0.2 mm 的地方它照样用**，只是拒绝把铜挤进 0.1 mm 的缝里。这一点在测试里做成了反向对照（`§5`）。
3. **`/DDR_DQ1` 从 5 颗变 0 颗是这笔交换最锋利的一面**：它 5 颗齿全是靠 10 段贴到作废线换来的，一旦要求可写，它连一颗也无处可放——因为它的通道上每个候选都在栅栏后面（`@(21.0,22.3)` 就是出线孔）。所以「少买 1.13 mm」不是均匀缩水，而是**集中在通道最挤的那几条网上**。

`arc` 档另外两条（只在这档出现）：

- `/DDR_DQ2` 的 0.141 mm 缺口判 `does_not_fit`：圆弧齿最少 2 颗、`ARC_EXTRA_FACTORS` 最小 0.18 ⇒ 最小增量 ≫0.141 mm，**亚毫米缺口它表达不了**；
- 大量 `too_shallow` 带 `also_blocked`：唯一能塞进去的圆角波形爬升只有 0.08–0.11 mm（`ARC_H_LEGIBLE_MM=0.15` 之下），画出来是「一个直角台阶」而不是蛇形——这判据是对的，不应放宽。

## §4 代码改动

**`route/tune_board.py`**：`plan()` 的 `assumptions.clearance` 原来无条件写「the apply-time 0.2 mm floor is COUNTED, not enforced」；现在按 `floor_mm` 分两句——非零时说「AND the apply-time %.2f mm floor, so nothing this plan adds lands closer than the write gate allows」。**这是把一句会变成假话的文案改成随参数变的真话**，不改任何几何。

**`tools.py`（`_tune_length`）**：

```python
floor_mm = TB.FLOOR_MM if args.get("respect_apply_floor") is True else 0.0
res = TB.plan(..., floor_mm=floor_mm, with_headroom=args.get("headroom") is True)
```

schema 新增 `respect_apply_floor`（`_bool`，默认 `False`），描述里写清「默认关之所以是默认：在已经 0.1 mm 的走廊上它会拒绝 net rule 允许的波形，长度变短、`new_pairs` 变 0」。工具数不变（仍是 262）。

`apply` 路上一句话跟着改稳：`clearance_floor.new_pairs` 为 0 时那段 `next_step`（「没写盘，因为 N 对落在 floor 以下」）不再出现，调用方不必知道 `allow_new_violations` 的存在。

**`tests/test_tune_board.py`**：原 `TestTheApplyFloor` 的 fixture 提到共享基类 `_Channels`（避免新类靠继承把父类测试重跑一遍），新增 **3 例**：

- `test_the_flag_trades_correction_for_a_writeable_plan`：同一对网、同一走廊，默认档 `new_pairs ≥ 1` + `assumptions.clearance` 含 `COUNTED`；尊重档 `new_pairs == 0`、`status == length_not_matched`、`ops == []`、`assumptions.clearance` 含 `0.20 mm floor`；
- `test_a_wave_that_is_already_clear_of_the_floor_is_still_planned`（**反向失败对照**）：邻线挪到 1.0 mm 后，尊重档仍然 `matched` 且 `ops > 0`——证明这个开关不是「一律不给画」；
- `test_the_tool_reaches_the_flag_and_stops_asking_for_allow_new_violations`：工具面 `respect_apply_floor=true` ⇒ `new_pairs == 0` 且 `next_step` 不含 `allow_new_violations`；同 fixture 不带该键 ⇒ `new_pairs ≥ 1` 且 `next_step` 含它（**证明开关是新增的一支，没有把默认路径换掉**）。

`tests/test_tune_board.py` = **38 passed**（35 + 3，8 s）。

## §5 未做项

1. **没有写盘**：244/114 两组 op 都只到 plan 为止，没有 `board_propose` + `board_apply`，也没有 `board_drc` 复测。写下去要不要接受那 35 对，是产品判断，不是本轮的读数。
2. **`headroom` 已经跑了**（见 §7）：把整块板子给一条网、邻居不争，12 条网里 **7 条仍然吞不下自己欠的长度** ⇒ 该组的 `verdict=blocked_by_geometry`（binding net = `/DDR_DM0`，差 4.800 mm）。这条是**终局**，不是待调参数。
3. **0.2 mm 余量仍是交换**（`188 §4`）：本轮第一次把它量化成「35 对 ↔ 1.130 mm」，但没有把它变成判据——要不要为了 skew 接受 0.1085 mm 的异网对，是用户的设计决定。
4. **`rules/` 里的优先级表仍未动**（`197 §4` 的两条布局规则还在文档里）。
5. 盲/埋孔仍未评估；`boot_sw` 等阶段 4 的历史欠账未动。

## §6 复现

```
# 真板 12 网三档读数（每档 17–28 s）
MSYS2_ARG_CONV_EXCL='*' env -u PYTHONPATH -u PYTHONHOME \
  python D:/tmp/_tune2.py D:/tmp/_rsv2_contract_p2_pinch3.json chamfer 0
MSYS2_ARG_CONV_EXCL='*' env -u PYTHONPATH -u PYTHONHOME \
  python D:/tmp/_tune2.py D:/tmp/_rsv2_contract_p2_pinch3.json chamfer 0.2
MSYS2_ARG_CONV_EXCL='*' env -u PYTHONPATH -u PYTHONHOME \
  python D:/tmp/_tune2.py D:/tmp/_rsv2_contract_p2_pinch3.json arc 0
# §7 headroom（219 s，整块板给一条网）
MSYS2_ARG_CONV_EXCL='*' env -u PYTHONPATH -u PYTHONHOME \
  python D:/tmp/_hr.py D:/tmp/_rsv2_contract_p2_pinch3.json 0
# 测试
env -u PYTHONPATH -u PYTHONHOME python -m pytest tests/test_tune_board.py -q
```

## §7 `headroom`：11 条欠长的网里 7 条**单独也吞不下**

`plan(with_headroom=True)` 对每条网在**干净板子上单独**跑一整趟（`headroom()`，`HEADROOM_EXTRA_MM = 0.2`：只求「刚过缺口」就停）。口径要注意：这里的「单」是**整块板都给它、邻居不争**，所以 `shortfall` 是几何终局，`reachable` 只是「自己的铜够用」，不是「和邻居一起也能放」。

| 网 | 欠 (mm) | 单独能吞 (mm) | 差 (mm) | 卡在哪 | status |
|---|---|---|---|---|---|
| `/DDR_CLKP` | 6.468 | 5.066 | **1.402** | `does_not_fit` | shortfall |
| `/DDR_DQ0` | 2.054 | 2.254 | 0 | — | reachable |
| `/DDR_DQ1` | 5.930 | 3.848 | **2.082** | `does_not_fit` | shortfall |
| `/DDR_DQ2` | 0.141 | 0.341 | 0 | — | reachable |
| `/DDR_DQ3` | 4.394 | 0.780 | **3.614** | `clearance` | shortfall |
| `/DDR_DQ4` | 0 | 0 | 0 | — | already_within_tol |
| `/DDR_DQ5` | 5.138 | 3.315 | **1.824** | `does_not_fit` | shortfall |
| `/DDR_DQ6` | 1.794 | 1.994 | 0 | — | reachable |
| `/DDR_DQ7` | 1.321 | 1.521 | 0 | — | reachable |
| `/DDR_DM0` | 5.018 | 0.219 | **4.800** | `clearance` | shortfall |
| `/DDR_DQS0N` | 2.760 | **0.000** | **2.760** | `clearance` | shortfall |
| `/DDR_DQS0P` | 3.186 | 0.291 | **2.895** | `clearance` | shortfall |

四条读法：

1. **组的 `verdict` 是 `blocked_by_geometry`，binding net 是 `/DDR_DM0`（差 4.800 mm）**：只要有成员单独也吞不下，组就不是「可调」；`198 §3` 默认档最后仍差 4.800 mm，与这个 binding 数字**同值**——残余不是搜索不够，是 `/DDR_DM0` 的几何上限。
2. **两种 `blocks` 指向同一件事**：`does_not_fit`（`/DDR_CLKP`、`/DDR_DQ1`、`/DDR_DQ5`）是「没有一段够长放下齿」，`clearance`（`/DDR_DQ3`、`/DDR_DM0`、`/DDR_DQS0N`、`/DDR_DQS0P`）是「把整块板给它也躲不开别人的铜」。前者是铜太碎、后者是走廊太窄，**都不是路由能改的**。
3. **`/DDR_DQS0N` 的 `reachable_add_mm = 0.000` 最刺眼**：一条 15.05 mm 的网，一颗齿都放不下——它整条贴在 `/DDR_DQ3` 旁边（`197 §3b` 的连续段读数：`/DDR_DQS0N` 连续 0），这是同一条事实的两种量法互相印证。
4. **4 条 `reachable` 的余量都只有 ≈0.2 mm**（DO0 2.254−2.054、DQ2 0.341−0.141、DQ6 1.994−1.794、DQ7 1.521−1.321 全等于 0.2）：那是探针的 `HEADROOM_EXTRA_MM`，**不是富余**，只说明「自己的铜刚好够它自己欠的那一点」。

这条读数的用途是判词级的：`board_tune_length(headroom=true)` 回来的 `blocked_by_geometry` 只要出现，就**不该再靠调路由参数重开这条 lane**，而应回到布局（`197 §4` 的两条规则：走廊按齿宽预留；齿只能落在量到的连续段里）。
