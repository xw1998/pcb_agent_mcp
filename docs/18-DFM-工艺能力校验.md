# 18 · DFM：把「工艺能力」当成输入，而不是服务器的常识

> 一句话：这一层不提供任何一家板厂的数字。它提供的是**规则目录**（每个数是什么、往哪个方向判、
> 拿什么去比），以及一份**能力文档的体检**——你说过的判，你没说的列成"未回答"，
> 你打错一个键就整份拒收。

## 18.1 为什么必须没有内置表

"最小线宽 0.127 mm"不是物理常数，是**某一家板厂在某一档价格上作出的承诺**。把它写进代码，
会有三个后果，而且都是安静的：

1. 换一家厂，答案错，但结果里没有任何一处说"这是别家的数"；
2. 同一家厂的工艺在进步（或在你选的档位下降级），代码里的数不会跟着变；
3. 用户看到的是一个**和 `basis` 写在一起的具体数字**，会以为它被核过。

本项目的纪律是「宁可报错不给错答案」，所以这一层只做两件不猜的事：

- `dfm_capability`：体检一份你给的能力文档；
- `dfm_check`：把**这块板**量出来，逐条对上去。

## 18.2 能力文档的形状

```json
{
  "fab": "板厂名 / 产线名（自由文本，只用于标识）",
  "source": "这些数字从哪来（必填，见 §18.3）",
  "verified": false,
  "rules": {
    "min_track_width_mm": 0.127,
    "min_clearance_mm": 0.127,
    "min_via_drill_mm": 0.3,
    "min_via_annular_ring_mm": 0.13,
    "min_pth_annular_ring_mm": 0.15,
    "min_hole_to_hole_mm": 0.5,
    "min_copper_to_edge_mm": 0.3,
    "min_slot_width_mm": 1.0,
    "max_board_width_mm": 400,
    "max_board_height_mm": 500,
    "max_copper_layers": 6
  }
}
```

**单位写进键名**（`_mm` / `_layers`）。这样"0.15"不可能是 mil、也不可能是 mm 的两种读法之一——
立创把长度存成 mil 而标签只作显示（docs/13），这个坑在这一层用命名直接封死。

规则目录（`calc/dfm.py` 的 `RULES`）一共 11 条，每条带 `what / unit / direction / fact`：

| key | 方向 | 量什么 |
|---|---|---|
| `min_track_width_mm` | min | 最窄铜线（每层的 `segment` 宽度） |
| `min_clearance_mm` | min | 不同网络铜到铜的最小边距（走 `oracle.clearance` 同一套几何） |
| `min_via_drill_mm` | min | 最小过孔成品孔径 |
| `min_via_annular_ring_mm` | min | 过孔环宽 `(diameter - drill)/2` |
| `min_pth_annular_ring_mm` | min | 镀通孔焊盘环宽 `(min(w,h) - drill)/2` |
| `min_hole_to_hole_mm` | min | 两个钻孔的孔壁到孔壁 |
| `min_copper_to_edge_mm` | min | 铜到板边（含挖空轮廓的边） |
| `min_slot_width_mm` | min | 内部开槽/铣槽的最窄喉口 |
| `max_board_width_mm` | max | 外轮廓 x 跨度 |
| `max_board_height_mm` | max | 外轮廓 y 跨度 |
| `max_copper_layers` | max | 实际承载铜的层数 |

## 18.3 三处"必须拒绝"

1. **没有 `source` 就整份拒收**（`SOURCE_REQUIRED`）。一个没有出处的限值，和一个编出来的限值，
   在结果里长得一模一样。这条与 `calc/safety_table.py` 对空表的处理是同一个判断。
2. **键名打错就整份拒收**（`CAPABILITY_KEY_UNKNOWN`，并列出全部已知键）。若只是忽略未知键，
   `min_track_wdith_mm` 会落进"文档没写"那一栏，读起来像"没这条要求"，而不是"我不认识"。
3. **值不是有限正数就拒收**（`CAPABILITY_VALUE_INVALID`）。`0`、负数、字符串、NaN 一律不放过。

## 18.4 判定是三态，不是两态

每条规则的落点只有五种之一：`pass` / `fail` / `not_checked` / `not_applicable`，缺事实时
**绝不当作 pass**：

- `not_applicable`：这块板上没有这种特征。只有过孔规则遇上没有过孔的板才会落到这里。
- `not_checked`：能力文档提了，但这次没能量。原因写在 `note` 里（例如 `check_clearance=False`）。
- `pass` / `fail` 之外还有 **bound**：`min_hole_to_hole_mm` 与 `min_copper_to_edge_mm` 用
  有界搜索（默认窗口 5 mm）。窗口内找到的最小值就是**全局最小值**（任何更小的对都一定在窗口内，
  因此一定被比较过）；窗口内一个都没找到时，返回的是 `lower_bound`——"真值至少是这么多"，
  而不是一个数。这两种情况在输出里字段不同（`measured` vs `lower_bound`），不会被读混。

`overall` 的取法：有 fail 就是 fail；否则有 pass 就是 pass；一条都判不了就是 `not_checked`。
`not_checked` 不是通过。

## 18.5 板侧量的是什么

| 事实 | 怎么量 | 已知近似 |
|---|---|---|
| `narrowest_track` | `segment.width` 取最小 | 不区分层内/层间，不提铜厚 |
| `min_copper_gap` | `oracle.clearance`（多边形-线段边到边） | 走线宽度从中心线距离里减掉；搜索窗为 `4×阈值`，超出即报 bound |
| `min_via_drill` / `min_via_ring` | 过孔列表 | 环宽按成品孔径算；未计镀层减薄 |
| `min_pad_ring` | 带钻孔的焊盘 `(min(w,h)-drill)/2` | 焊盘多边形本身与钻孔的偏移未参与 |
| `min_hole_web` | 孔心距 − 两个半径，桶网格 + 一圈光晕 | 同上，不含镀层 |
| `min_copper_to_edge` | 铜图元 vs 外轮廓与挖空的边（扣掉半个线宽） | 只算铜（丝印轮廓不算铜，否则会凭空多出违规） |
| `min_slot_width` | 挖空多边形每个顶点到非相邻边的最小距离 | 矩形精确；曲线槽按折线算 |
| `board_width`/`height` | 外轮廓 bbox | 按导线中心线，不含板边线宽 |
| `copper_layers` | 真正带铜的层名集合 | 由文件里的铜图元推断，不读叠层声明 |

每条检查都带 `where`（是哪条走线、哪个过孔、哪段板边），因为"最小 0.19 mm"这种答案
不告诉人该动哪里，等于没说。

## 18.6 这一层明确不算的东西

写进每份返回的 `not_checked_by_design`，一字不改地列出来，避免"结果里没有"被读成"查过没问题"：

- 阻焊桥/阻焊定义的间距（需要把阻焊开窗投影到铜上，本层的口径里没有这一层）；
- 丝印最小线宽与字高（丝印根本没进测量）；
- 阻抗控制叠层的签核（判据是板厂的阻抗条，不是这里的场解）；
- 工艺公差：孔位、镀层厚度、铜厚、板厚；
- 拼板、工艺边、V-cut、基准点的间距；
- 镀通/非镀通槽的区分（只按文件里写的算）；
- 铜到**成品孔壁**的间距（含镀层增厚）；
- 圆弧折线化带来的钝角误差。

## 18.7 一个被这条链抓出来的真缺陷

写这一层的测试时，1 mm 的开槽量不出来：`board.cutouts` 是空的，而外轮廓里有两个轮廓。
根因在 `adapters/kicad_pcb.py`：

```python
board.outline = [polys[0]] + polys[1:]      # 旧
```

注释写的是"第一个是外轮廓"，代码却把**剩下的轮廓也算进了板框**。于是一个 1 mm 的铣槽
和板子自己的边界分不开——`outline_bbox` 会把整个挖空包进去，`cutouts` 永远是空。
改成 `outline = [polys[0]]; cutouts = list(polys[1:])`。影响面用 18 块 KiCad 官方 demo 板核过：
只有 `vme-wren` 有多于一个轮廓，而它走的是"线段+圆弧串成环"的另一条分支（本身就在按面积挑外轮廓），
所以这次修正只影响**显式用 `gr_rect`/`gr_circle`/`gr_poly` 画挖空**的文件——也就是 KiCad 自己
写出来的那种。

## 18.8 测试

`tests/test_dfm.py` 28 例：

- 能力文档 5 例（缺 source、空 source、错键、空规则、非正数 / 字符串）；
- 判定边界 7 例（等于限值过、少 1 µm 不过、max 两个方向、bound 能判、无测量是 not_checked、
  not_applicable 与 not_checked 不同、未声明的规则列进 missing 而不是 pass）；
- 板侧事实 8 例，夹具是手算的 50×30 mm 板（0.2 mm 走线、0.6/0.3 过孔两个相距 1 mm、
  镀通孔 1.0/0.4、丝印与铜到板边 0.875、1 mm 槽）；
- 端到端 5 例（全通过 / 一条不过并给出 -0.05 mm 的余量 / 未知键传到调用方 / 派生量标记）；
- 注册 1 例（两个工具都在 `tools/list` 与 `_capabilities.available` 里，旧的"DFM 未实现"一行已删）。

`--selftest` 加 3 条指纹：错键被拒、同一块板对 0.15 mm 限值通过、对 0.25 mm 限值失败并点名规则。
