# 02 分场景检查单

四张单子，按任务粘贴使用。每张最后一项都是「写下读数」，不写就不算做完。

## A. 接手一块板（只读）

1. `board_summary` —— 文件到底有什么：计数、板框 bbox、层叠、设计规则。
   若连 outline 都不闭合，后面所有几何推断都不可信，先修这个。
2. `board_components` —— 器件与**每层分布**；单面贴片要求下 B.Cu 器件数必须是 0。
3. `board_nets` —— 网络清单 + 每网 pad/track/via/zone 数与总长。
   「总长 0」的网就是没布线，别被网表长度骗。
4. `board_zones` —— 铺铜：有 zone 无 `filled_polygon` 就是只有轮廓、没有铜。
5. `board_drc` —— 权威违约与未连通。这一步的数字才是「板子有多坏」的基准。
6. 写进报告：**计数 + 未连通网名 + 未连通组数 + 铺铜状态**。四样都要。

## B. 改板（写盘）

1. 先确认目标文件是**副本**（用户工程只读）。记录原文件 sha256。
2. `board_intent` —— 把这一版的约束写成 version 1 JSON，先核一遍能不能满足。
3. `board_free_space` / `board_routability` / `board_blockers` —— 改摆放前看空间与阻挡；
   **挪一个器件往往比绕十根线便宜**。
4. `board_propose` —— 拿 `plan_id` 与 postcondition 列表。**逐条读 postcondition**：
   它列的就是 apply 会验什么；验不到你想验的东西，说明提案写得不对。
5. `board_apply` —— 写入并自证（checkpoint → 原子写 → 四道闸门）。
6. 复验：`board_drc` 零 violation 零 unconnected；`board_zones` 有填充；
   `board_cross_split` / `board_loop_area`（有层叠时）不报跨分割。
7. 记录：`plan_id`、checkpoint 名、apply 前的 digest、修掉的违约条目。

**红线**：不走 propose 直接文本改 `.kicad_pcb` = 没有闸门、没有 checkpoint、没有回滚。
不许。

## C. 出图与交付

1. `dfm_check`（带真实能力表）+ `dfm_capability` —— 先确认板子做得出来。
2. `board_fab_package` —— Gerber / Excellon（mm，PTH 与 NPTH 分开）/ 贴片坐标 CSV，
   可选 STEP / IPC-2581 / ODB++ / IPC-D-356 / GenCAD。每个产物它都报来源与设置。
3. `board_export_graphics` / `board_export_3d` —— 人看的图。
4. 自检：Gerber 独立复核（层数、板框、钻孔数与 CSV 行数对得上）；
   **不要只看「导出成功」**。
5. 写进记录：层表、最小线宽/间距、孔数与环宽、板厚与叠层、文件名清单。

## D. 排障（「为什么没读到 / 为什么答案不对」）

按这个顺序查，别跳：

1. **文件层**：文件多大？`.kicad_pcb` 是不是全在一行（100 KB 一行是常态）？
   `grep -n` 拿不到行号不代表文件坏了。
2. **解析层**：`board_summary` 报的计数合理吗（封装数、焊盘数、网数）？
   明显偏少 ⇒ 版本/语法没被解析（例如 KiCad 版本差异），先看 `board_fidelity`。
3. **事实层**：我要的那个数，工具里有没有对应事实？没有就是 `ARG_MISSING`/
   `DATA_UNAVAILABLE`，**这不是 0**。补参数，别补猜测。
4. **口径层**：这个读数是权威的吗？`authoritative: false` 的要换权威工具复核
   （铺铜连通性 → `board_drc` 的 unconnected）。
5. **单位层**：mm 还是 mil、per-unit-length 还是总面积、峰峰值还是幅值。
   本项目里单位口径错误至少造成过两次「差 10 倍」和一次「差 4 倍」。
6. **体积层**：答案被截断了？读 `saved_to` 的完整 JSON，不要重新调用更大的查询。
7. **层名层**：脚本按 `"F"` / `"B"` 过滤得到空结果 —— 真实值是 `"F.Cu"` / `"B.Cu"`。

每一条排查都要留下**读数与出处**（哪个工具、哪个字段、什么值）。「看起来不对」
不是结论。

## E. 上板前的最后七问（与 `pcb-layout-methodology` 第 7 步同一张表）

1. 权威 DRC 零违约零未连通？
2. 每条高速信号下面参考铜连续？
3. 每个 GND 焊盘都吃到铜、两面缝合孔够密？
4. 电源线宽/压降在允许温升内？
5. 间距/环宽/孔到孔 ≥ 能力表？
6. 调试口、测试点、去耦电容可达可换？
7. 渲出来的图里没有大片空白、没有绕板三圈、没有平行长距离双面走线？
