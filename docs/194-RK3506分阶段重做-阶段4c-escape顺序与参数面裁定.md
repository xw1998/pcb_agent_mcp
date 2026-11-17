# 阶段 4c —— escape 顺序买不到腰部空气；以及那三个参数「不上工具面」是裁定，不是欠账

`193 §3` 给这块板的「可读齿放不进去」留了两条出路：**band 生成期预留**，和**改 escape 顺序（`net_priority`）**。`193 §7 #1` 还留了第三条账：`bundle_bands` / `net_priority` / `terminal_override` 三个参数在 `route.router.plan` 里有、在 `board_route` 的 schema 里没有，看着像「做完了但摸不到」。

本轮把后两件一起做了：出路 #2 量掉（判负），第三条账**改判为「按裁定不做」**并写清楚为什么。**本轮零代码改动**——不是省事，是查完之后发现没有该改的地方。

本轮不升版本、不进 CHANGELOG（沿用 181–193 的先例）；工具数不变（262）。

## §1 结论先行

**一、`net_priority` 换不动腰部，三档落在 0.2475–0.2500 mm。**

12 网口径同 `193`（`D:/tmp/_s1d.esc2.kicad_pcb`，端点钉 `In2.Cu`/`B.Cu`，pitch 0.05、track 0.1、clearance 0.105、`new_copper_layers=["In2.Cu","B.Cu"]`，band 开、无 reserve）：

| escape 顺序 | 通了 | ops | elapsed | lane0 spread | `/DDR_DQ1` 腰部净空 | 最近者 |
|---|---|---|---|---|---|---|
| 默认（按长度） | 12/12 | 116 | 33.6 s | 7.737 | **0.2500** | `/DDR_DQ0` |
| `/DDR_DQ1` **最先** | 12/12 | 119 | 95.0 s | 6.510 | **0.2500** | `/DDR_DQ0` |
| `/DDR_DQ1` **最后**（其余 11 条全部先） | 12/12 | 113 | 84.5 s | 7.368 | **0.2475** | `/DDR_DQ5` |

**二、那三个参数不该上工具面**，因为 `board_route` / `board_fanout` 在 `0.0.107` 就已经从 `TOOLS` 里摘掉了，用户的裁决是逐字的：

> 「不再允许自动布线和自动布局的存在，防止你偷懒」

`tools.py` 末尾那段注释把边界写死了：这两个名字不在 `TOOLS` 里、`PCB_AGENT_TOOLSET=all` 拿不回来、按名字调只会拿到 `TOOL_RETIRED`；留下的只有实现代码「供人读和供自检引用」。所以「把 `bundle_bands` 接进 `board_route` 的 schema」这条路**根本不存在**——那不是一个没接的口子，是一个被关掉的工具。`193 §7 #1` 那条账就此关闭，性质从「欠账」改成「按裁定不做」。

**三、`193` 的出路 #2 判负，出路 #1 仍是唯一一条**（band 生成期就预留；本轮没做，`_paint_ring` 仍画在已 emit 的铜上）。

## §2 我是怎么发现第二条的（如实记一笔）

按 `193 §7 #1` 的字面意思，先把它当欠账去做：给 `board_route` 加三个参数（`bundle_bands` 走 `_parse_object_list`、`net_priority` 按逗号切、`terminal_override` 只收对象或 JSON 文本），共 84 行，并在 `tests/test_route_reserve.py` 里加了 5 例（band 以对象/JSON 到达、非 JSON 拒绝、缺 `nets` 由路由器点名、两个参数进搜索）。

写完测试全过（13 passed）之后毙掉自己的理由是**一句更硬的读法**：

```
tools.py:15802    for _gone in RETIRED_TOOLS:
tools.py:15803        TOOLS.pop(_gone, None)
```

也就是说我加的 84 行 schema 与转发**谁也走不到**：`call_tool("board_route", ...)` 在 `TOOLS.get(name)` 那一步就 `None`，直接返回 `TOOL_RETIRED`。测试之所以绿，是因为它们调的是 `tools._board_route`（函数本体，自检引用用），不是工具。**「测试通过」在这里证明的是函数能跑，不是能力可达**——如果按测试绿就交付，交付的东西会是一段描述一个不存在入口的 schema，比不做更糟。

于是全部回退：`tools.py` 恢复到 `HEAD`（`git diff` 为空），5 例一并删掉，`test_route_reserve.py` 回到 8 例（10.4 s 全过）。本轮留下的只有读数与本文。

## §3 为什么 reserve 挤不出走廊：这块板上没有「别的路」

`193 §1` 的两组读数在这里并排读才有意义：

- 环值 `price 2` ⇒ **12/12**，净空 0.2475–0.2873（跟没预留差不多，且 `spread` 从 7.737 降到 6.539，方向与直觉相反）；
- 环值 `price 4` ⇒ **10/12**，`/DDR_DQ5` 与 `/DDR_DQ1` 两条 `search_budget`。

`search_budget` 是「预算内没找到路」，不是「找到了一条非法路」。于是：**中间没有「挤出去了但还通」这一档**。原因不是参数没调对——这条 lane 在 `In2.Cu`/`B.Cu` 上是一个瓶颈走廊，band 用 `discount 0.6` 把成员吸到同一条路上，而板子本身没有第二条可走的路。价钱一旦真的起作用，它就不再是「让别人绕远」，而是墙；而那正是 `193 §2` 用 `price 18` 的反向对照在小例上排除掉的形态（小例真有别的路，所以 3/3 仍通）。

一句话：**预留能买到的空气上限 = 走廊外面还剩多少空间；这块板剩 0.05 mm（0.250 → 0.300）。**

## §4 顺序能换什么、换不动什么

三条读法，都是实测：

1. **换不动腰部**：0.2475 / 0.2500 / 0.2500，差 0.0025 = 一个栅格步。可读齿要 0.475（`188 §4`：`ARC_H_LEGIBLE_MM = 0.15`，两侧各一倍半线宽 ⇒ 中心距下限），差 0.225 mm ≈ **1.9 倍**。
2. **换得动「谁最近」与「谁被绕长」**：默认与「DQ1 最先」最近者都是 `/DDR_DQ0`；「DQ1 最后」变成 `/DDR_DQ5`。同口径下 `/DDR_DQ6` 在默认档 0.3035、在「DQ1 最后」档 0.3000 —— **净空是「此刻谁离它最近」，不是这条网的性质**（`193 §3` 已写死，这里第二次被证实）。
3. **顺序的代价是时间不在铜**：DQ1 最先 ⇒ 95.0 s（第一条网没有任何骨架可跟，A* 从零搜整条 lane）；默认 33.6 s；DQ1 最后 ⇒ `/DDR_DQS0P` 中途 `failed` 一次、`rejected 2`、靠第二遍救回、84.5 s。**先放谁进去不省事，只是把成本挪到别处。**

## §5 那三个参数到底是给谁用的

不是给客户端，是给**写盘之前的人与 agent**：

- 被禁的是「工具自己算一条路径就写进去」（`add_track` / `add_via` 的坐标必须由调用方给）；
- `route.router.plan` 是**离线测量与规划**的实现代码，自 `0.0.107` 起就是这个身份：`test_route.py` 用 18 个用例直接问它，`docs/188`–`193` 的全部读数都从它来；
- 所以 `bundle_bands` / `net_priority` / `terminal_override` 的调用方式就是探针脚本（`D:/tmp/`，不入库），产物是 ops，铜最后仍由调用方逐条交 `board_edit` 写、由官方 `ZONE_FILLER` 灌。

这条边界写在 `tools.py` 那段注释里（「与之配套的纪律」），本轮不改它，只在这里指一次，免得下一次又有人把 `193 §7 #1` 当欠账去接。

## §6 复现

```bash
# 三档顺序：第 7 个参数是优先级网名，空字符串 = 默认顺序
MSYS2_ARG_CONV_EXCL='*' env -u PYTHONPATH -u PYTHONHOME \
    D:/KiCad/10.0/bin/python.exe D:/tmp/_rsv3.py \
    D:/tmp/_rsv3_p1.json plain 0.5 4.0 '' '' /DDR_DQ1

# 腰部净空对任意 ops json 复算（同口径：同层、跳过自身焊盘 1.0 mm 内、0.1 mm 采样）
MSYS2_ARG_CONV_EXCL='*' D:/KiCad/10.0/bin/python.exe \
    D:/tmp/_rsv_gap.py D:/tmp/_rsv3_p1.json /DDR_DQ1
```

两个前缀都不是装饰：

- `MSYS2_ARG_CONV_EXCL='*'`：Git Bash 会把 `/DDR_DQ1` 改写成 `C:/Users/.../PortableGit/DDR_DQ1`，参数**静默丢失**、脚本照常跑完照常报 12/12。`193 §6` 踩过一次，本轮 `_rsv_gap.py` 又踩一次（第一次打印 `no mid-run copper`）。
- `env -u PYTHONPATH -u PYTHONHOME`：宿主机的 `PYTHONPATH` 指向一个 UTF-8 解不开的目录，KiCad 的 python 在 `site` 初始化阶段就 `UnicodeDecodeError` 退出（本轮第一次跑就是这么失败的，且报错看起来跟脚本毫无关系）。

## §7 这一轮没有做的

1. **band 生成期预留**（`193 §7 #2`，现在它是唯一的出路）：`_paint_ring` 仍画在已 emit 的铜上，正确时机是走廊几何被决定的那一刻（`_skeleton` / `_band_key` 的时序）。这是一次真正的实现改动，本轮不夹带。
2. **0.2 mm 余量**（`ruleset.precondition.CLEARANCE_FLOOR_MM`）：仍不动。它是这块板上**唯一还没被量过的买家**，但方向与「多留空气」相反——`188 §4` 的 2×2 矩阵显示 floor 0.2→0.1 会让短网可加数从 5 掉到 2/4，所以它是一笔**交换**，不是解法。
3. **`terminal_override` 的真板使用**：本轮只在（已回退的）小例里碰过。真板那 12 个点来自 `_ddr_esc6.json`（阶段 2e 的 dogbone 逃逸），换一批点会得到完全不同的走廊，是另一条实验线。
4. `hole_in_pad` 9 / 旧 `via_in_pad` 9 / `copper_under_magnetics` 1 / 5 处半路由 / 旧锐角 / `track_dangling` 25 仍全在阶段 2/3 的欠账清单里。
