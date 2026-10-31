# 57 · SPICE：1/f 噪声、温度扫描，与三条"接了活却不干"的静默

0.0.53 的 `calc_capabilities.not_implemented_yet` 第 14 条原先写着这么一句：

> rejects F H J K S W A cards with the card named in the error

这句话是**错的**。实测：`F`/`H`/`J`/`K`/`S`/`W` 六种卡全部求解成功，`A` 卡的 `gain` 与
`limit` 两种模型也在列，而源码里的 `NOT_IMPLEMENTED_CARDS = {}` 是个空字典。也就是说这条
"局限"不是"我们做不到"，而是"我们早就做了，但自述还留着一句假的拒绝"。

顺着这条线把 19 条逐条实测，抓到三处更严重的东西——它们不是"少做了一点"，而是**接了活、
没干、还照常返回一个看起来可信的数字**：

| 卡片 | 实测行为（0.0.53） | 后果 |
| --- | --- | --- |
| 二极管 `KF=` / `AF=`（1/f 噪声） | `.noise` 结果在 f=1 与 f=10 上**逐位相同** | 卡片被读进来了，噪声里一分钱都没体现 |
| `R1 1 0 1k mc(1)` | 返回 `ok`，`value = 1000.0` | 蒙特卡洛调用被当成标称值，且不给样本数、不给种子 |
| `.temp 25 100` | 只解了 25 ℃，100 ℃ 被丢掉 | 多温度被当成"取第一个" |

0.0.54 把这四条一起做掉：二极管 1/f **实现**（公式拿随包的 ngspice-47 反解出来的，不是猜的）、
Q/M/J 的 `KF=` 在 `.noise` **按名拒答**（连同"引擎自己也不做"的实测证据）、`.temp` 多值改
**扫描**、`mc(...)` 一类的统计值**拒答**。另外把 R/C/L 行尾多出来的 token 也一并拒掉——那是
`mc(1)` 混进来的同一个入口。

一句话结论：**能实测确认的就实现，实测确认不出来的就按名拒答，绝不静默忽略、也绝不猜**。
默认答案（`KF` 不给、`.temp` 单值、值就是数）与 0.0.53 **逐位相同**。

## 57.1 二极管 1/f：公式是从引擎里反解出来的

SPICE 里 `KF`/`AF` 是"闪烁噪声系数"与"闪烁噪声指数"。文献与手册的写法通常是

    S(f) = KF · I^AF / f^exponent

但 `AF` 到底是**电流**的指数还是别的什么、`f` 的指数是不是恒为 −1、这团东西是电流源还是电压源、
跨结还是串在结上——不同的书写法不一样，凭记忆填就是猜。所以做法是**先量后写**。

用随包引擎（`vendor/ngspice/bin/ngspice_con.exe`，ngspice-47 Windows build）跑同一个 deck：

```
V1 2 0 dc 0.6 ac 1
R1 2 1 1k
D1 1 0 DM
.model DM D(KF=1e-16 AF=1)
.control
set filetype=ascii
op
noise v(1) V1 dec 1 1 10
setplot noise1
print onoise_spectrum
.endc
.end
```

引擎读数：f=1 → `2.534181e-08`，f=10 → `8.302515e-09`。

自解（只算 shot + thermal，不含 1/f）给的是 `2.283233345e-09`，两个频率**完全相同**，
其中 `D1` 贡献 `2.038732797e-18`、`R1` 贡献 `3.174421712e-18`。bias 是
`{'2': 0.6, '1': 0.566777147}`；源项里 D1 的 shot `psd = 1.0645775669853785e-23`（⇒
`Id = 3.3227e-5 A`）、R1 的 thermal `1.6576071894e-23`。两点的传递系数都是
`|H|² = 1.9150e5`。

于是把引擎读数减去自解，反解出 flicker 那一项的密度：

    S_flicker(f=1)  = 2.534181e-08² − 2.283233345e-09²  = ...
    S_flicker(f=10) = 8.302515e-09² − 2.283233345e-09²  = ...

两点之比恰为 10 ⇒ **f 的指数就是 −1**。再用 `Id = 3.3227e-5` 反解 `KF`：

    KF = S_flicker(f) · f / |Id|^AF = 1.00112e-16     （卡片写的是 1e-16，即 +0.11 %）

形状、量纲、位置三者一起被钉死：**`flicker_psd = KF·|Id|^AF / f`，单位 A²/Hz，是跨在结上
的一个电流源**（与 shot 同位置，因为两点用的是同一个 `|H|²`）。实现就是这一行：

```python
def _flicker(el, i_amps):
    kf = <card KF>; af = <card AF>
    if kf <= 0: return None
    return lambda f: kf * abs(i_amps) ** af / f
```

`kf <= 0` 返回 `None` 而不是返回一个恒零的 lambda，这样 `KF` 不给时**根本不会多出一条源项**，
默认路径的 `sources` 列表与 0.0.53 逐位相同。

新实现的复现误差（同一 deck，对上引擎那两个读数）：

| 频率 | 本实现 | ngspice-47 | 相对误差 |
| --- | --- | --- | --- |
| 1 Hz | `2.532689514e-08` | `2.534181e-08` | 0.0589 % |
| 10 Hz | `8.296806662e-09` | `8.302515e-09` | 0.069 % |

0.11 % 的 `KF` 反解偏差 + 两次舍入构成了这 0.06 % 的全部——不是拟合参数，是双重反解残差。

### `AF` 是电流的指数，不是频率的指数

这一条我一开始写错了：以为 `S(f)·f²` 在 `AF=2` 时恒定。实测 `S(f)·f` 在 `AF=1` 与 `AF=2`
下**都**恒定，`AF` 改的是电流依赖（`|Id|^1` → `|Id|^2`）。用 MOS 拟合脚本的同一手法在
`S = T + F/f^α` 上做三频拟合，`AF=2` 那档给出负的 `T` 且残差 1.88e-9（比信号小两个数量级
但非零）⇒ `α=1` 是对的、`α=2` 是错的。测试里因此写成"两个 AF 下 `S·f` 都恒定"，`AF` 的
效果交给另一类：`AF=1` 时 flicker 之比 == shot 之比（都是 `|Id|^1`），`AF=2` 时 == 比值的平方
（线性读法会偏差 >0.1）。

## 57.2 Q / M / J 的 `KF=`：按名拒答，因为引擎自己也不做

二极管能反解，是因为引擎真的动了。换到 MOSFET（`_scratch/fit_mos.py`，1 kHz 附近取三点解
`S = T + F/f`）：

| 变体 | `Id` | `T` | `F` | 1/f 残差 |
| --- | --- | --- | --- | --- |
| base | 2.0000e-04 | 0 | 2.3167e-08 | 7e-16 |
| `TOX`×2 | 2.0000e-04 | 0 | 4.6335e-08 | 1e-15 |
| `W`×10 | 2.0000e-03 | 0 | 2.3167e-07 | 7e-15 |
| `L`×2 | 1.0000e-04 | 2.2e-17 | 2.8959e-09 | 1e-17 |
| `KF`×10 | 2.0000e-04 | 0 | 2.3167e-07 | 7e-15 |

五条读数的形状都能解释成 `F ∝ KF·Id·TOX/L²`（`L`×2 得 ×0.125 而不是 ×0.25，因为 `Id` 也
减半；`W`×10 得 ×10 因为 `Id` 也 ×10）。但**`F` 是输出参考量，里面已经含了 `|H|²`，而
`|H|` 随 `R_D`、`gm`、`W/L` 一起变**。要把器件级公式和电路级增益分开，得再跑一组只动
`R_D` 的对照才能把 `|H|²` 解出来——这已经超出"抄引擎"的范围，进入"猜"。所以**不做**。

BJT 更干脆：`_scratch/nfq.cir` 把 `KF` 从 1e-15 扫到 1e-6（九个数量级），ngspice-47 的
噪声答案**逐位相同**（f=1 与 f=10 都是 `6.547292e-09`）。JFET 与 MOS 同族，同样不响应。

这是"没有可抄的东西"，不是"我们懒得抄"。所以策略是：`.op` 照常求解（`KF` 在 DC 点本来
就不影响任何东西，SPICE 亦然，在 `.op` 报错是另一种错答案），**`.noise` 时按名拒答**：

    reason_code = NOT_MODELED
    message     = noise cannot be reported: <elements> carry KF=, and flicker (1/f) noise
                  on a <BJT|JFET|MOSFET> is not modelled by this subset - the f^-1 form was
                  verified against ngspice-47 for a DIODE only, where fitting the engine's
                  two readings reproduces the card's KF to 0.11 %
    hint        = MEASURED: ngspice-47 (the engine bundled with this package) applies no
                  BJT/JFET/MOSFET flicker at all - KF from 1e-15 to 1e-6 changed its own
                  noise answer by 0.0 % - so there is no reference model to copy and
                  engine='ngspice' would return the same thermal+shot number without
                  telling you. Remove KF= (and AF=) from those model cards if thermal +
                  shot is the answer you want; only a diode's KF is modelled here
    extra       = devices_with_unmodelled_flicker: [{element, type, kf}, ...]

hint 里那句"0.0 %"是刻意写的：它把"引擎也不做"这个**实测事实**说出来，否则用户会以为
`engine='ngspice'` 能给出更全的答案。这是本轮最值钱的一条文案——它把"做不到"换成了
"两条路都没有"。

顺带一个连带的修法：JFET 的 `KF`/`AF` 原先在**建模期**被 `_JFET_REFUSED` 拒掉，导致
`.op` 也跟着报错。现在从拒答表里删掉，改成参数环里的 `continue`（读完就不管），把拒绝
搬到唯一该拒的地方——`.noise`。（中途我一度为了填这个坑杜撰了一个 `TCV2` 参数名，随即删掉：
参数名不能编。）

## 57.3 `.temp 25 100` 是扫描，不是"取第一个"

ngspice 的多温度 `.temp` 是扫描。0.0.53 的解析器把值都读进了 `temp_list`，但求解只跑
`temp_c` 一个 ⇒ **答案对应第一个温度，其余静默丢弃**。

现在：`temp_c` 参数没给、且 `temp_list` 长度 > 1 时，逐个温度递归求解，返回

```json
{"analysis": "temperature_sweep", "temperatures_c": [25.0, 100.0],
 "cases": [{"temp_c": 25.0, "result": {<完整单温度答案>}}, ...]}
```

每个 `case` 是**一次独立求解**，不是把同一个 bias 点换个标签。判据（selftest 第 (c) 条）
就是这条：`case[i]` 的 `nodes_v` 必须与"该温度单独跑一遍"的 `nodes_v` **逐位相同**；负控制
是 25 ℃ 与 100 ℃ 的读数本身必须不同（否则"复制一份、改个温度标签"也能过闸）。

实测（`V1 1 0 5 / R1 1 2 1k / D1 2 0 DM(IS=1e-14)`）：25 ℃ → `0.695820796` V，
100 ℃ → `0.570918132` V。

`temp_c` 参数与 `.temp` 卡片同时出现时，参数赢，但**要出声**：

    "<deck> temp_c argument overrode a .temp card that named <...>; only the caller's one
     temperature was solved (drop the argument to get the card's own sweep, one independent
     solve per entry)"

这是 `_Circuit.__init__` 里登记的一条覆盖警告（走既有的 warnings 通道）。

`.temp` 后面一个数都没有时抛 `NETLIST_INVALID`（"`.temp needs at least one temperature`"），
而不是当成空扫描返回一个空 `cases`。

## 57.4 `mc(1)` 与统计值：拒答

`R1 1 0 1k mc(1)` 在 0.0.53 返回 `ok` + `1000.0`。这是最坏的一类错答案：用户以为自己做
了一次蒙特卡洛，拿到的是一个标称值，而两者量级相当、无从分辨。

`mc(...)` 需要两个 deck 里根本没有的东西——**样本数与种子**。所以按名拒答，覆盖
`mc(` / `unif(` / `aunif(` / `gauss(` / `agauss(` / `limit(` 六种写法（后五种是 `mc` 的参数
分布形式，`limit(` 是 ngspice 的限幅调用）。消息里点名是 Monte-Carlo，原文 token 一并列出，
理由写清"it wants a distribution and a run count the deck does not carry"。

负控制：同一 deck 把 `mc(1)` 换成 `1k`，答案照常（`node1 == 5.0`）——拒的是"统计调用"，
不是"带括号的数值"。

## 57.5 R / C / L 行尾的多余 token

`mc(1)` 之所以能混进来，是因为 R/C/L 的解析器读完 value 就把**剩下的全部 token 丢掉**。
同一个洞还能塞进 `R1 1 0 1k 2k`、`R1 1 0 1k tc1=0.1`——都是"打了字、没人看"。

新增 `_tail(allow_ic)`：R 一律拒尾部多余 token；C/L 只放行 `ic=`（这是文档化字段，DC 点
初始条件，有用且被测）。消息里必须出现 `trailing` 与原文 token，含 `(` 的再补一句
"a `mc(...)`/`agauss(...)`/`unif(...)` is a Monte-Carlo call: it wants a distribution and a
run count the deck does not carry"（selftest 断言依赖 "Monte-Carlo" 这个词出现在 message 里）。

E/G 卡另有一条同型的洞：SPICE 里 `E` 是**恰好五个字段**（`n+ n- nc+ nc- gain`），
六个字段是别的卡（如 `TABLE`/`POLY` 形态）混进来的信号。`len(parts) > 6` 即拒，案由是
"An E card here is exactly five fields: n+ n- nc+ nc- gain ..."。负控制：五字段的
`E1 2 0 1 0 2` 仍答 `node2 == 10.0`。

## 57.6 未知参数：点名，但不改答案

起因是我自己在一次手工验证里把 `temp_c` 写成了 `temperature_c`，工具**静默忽略**了这个
参数并返回了一份完整答案——那是一份"用户以为加了参数、其实没加"的答案。

`call_tool` 在结果信封上挂一条检查：`args` 里不属于该工具 `inputSchema` 的键，全部点名。

    argument(s) temperature_c are not in this tool's schema and were IGNORED - the answer
    below was computed as if they had never been given (spelling is exact: analysis,
    cross_check, engine, max_iter, max_points, netlist, temp_c)

为什么不报错：绝大多数调用是模型自己拼的参数，一个多余的键就整条拒掉会把"想用"变成"用不了"；
而且 `call_tool` 层并不知道上层是否有意冗余。**点名 + 不改答案**在这里是更诚实的形态——它
让错拼可见，又不改变任何正确调用的读数。正确调用时**不产生这条警告**，也不产生
`unread_arguments` 键，所以默认信封逐位不变。

## 57.7 能力自述的三处订正

1. 第 14 条那句假声明，改成：

   > F H J K S W are SOLVED (0.0.31-0.0.36; the earlier note that claimed they were
   > rejected was wrong and is corrected here), and A cards for the gain and limit code
   > models since 0.0.39 - what is refused by name is the XSPICE models that carry state
   > or a sample clock, not these cards.

   这是**订正登记**，不是悄悄改掉：旧文案原样保留在括号里并注明"was wrong"。

2. 噪声机制段加入二极管 flicker、`frequency-dependent density is evaluated at each swept
   point`（而不是"held at one value"）、`Q/M/J` 的 `KF=` 在 `.noise` 时 `NOT_MODELED`、
   以及"ngspice-47 自己 0.0 %"这条实测。

3. 温度 / Monte-Carlo 段改为：

   > A multi-valued .temp card is a SWEEP in the builtin solver since 0.0.54 ...
   > What neither path does: distortion, and Monte Carlo - a deck writing an `mc(...)` /
   > `unif(...)` / `agauss(...)` / `gauss(...)` value is REFUSED by name since 0.0.54
   > instead of being read as its nominal number, since the call carries no sample count
   > and no seed.

模块级 `PRECISION` 也从 "Noise is thermal 4kT/R, shot 2q*I" 改成
"Noise is thermal 4kT/R, shot 2q*I and - since 0.0.54 - a DIODE's flicker KF*|Id|^AF/f"。

## 57.8 默认答案不变的自证

- `KF` 不给 ⇒ `_flicker` 返回 `None` ⇒ `sources` 列表里**根本不会多出一条**，`sources` 与
  `contributions_v2_per_hz` 逐位不变。
- `.temp` 单值（或不写）⇒ 走原路径，不产生 `cases`，信封形状不变。
- 常量密度源（thermal / shot）⇒ `callable(psd)` 为假 ⇒ 走原 `psd_a2_per_hz` 分支，逐位不变。
- 值就是数 ⇒ `_STAT_FUNCS` 不命中 ⇒ 解析结果逐位不变。
- 参数名拼对 ⇒ 不产生 `warnings`、不产生 `unread_arguments`。

每一处新增的行为都有一条"旧行为还在"的负控制测试兜着，所以"加了功能"与"改了答案"是可
分辨的两件事。

## 57.9 边界（这一版仍不做的事）

- 只做**二极管**的 1/f。Q/M/J 一律 `NOT_MODELED` 拒答，理由见 57.2，不是"未实现"。
- 1/f 只在 `.noise` 里存在，`.tran` 里不是噪声源（这才是 SPICE 的正确语义：噪声分析是
  频域的、`.tran` 是确定性的）。burst、induced gate noise、avalanche 都不做。
- `.temp` 扫描只在 builtin 求解器里。`engine='ngspice'` 每次跑一个温度，除非 deck 自己
  写 `.control` 循环。
- Monte-Carlo：**两条路都不做**（builtin 拒答，ngspice 也只是把 `mc()` 当标称值）。这是
  设计选择，不是欠账。
- 未知参数点名是**提示**，不是校验：拼错的键不会被拒绝，只会被念出来。
- 块状 `.temp`（`.temp 25` 后再 `.temp 100`，即多条 `.temp` 卡）仍按 SPICE 语义取最后一条，
  这一版没有把它变成累积列表。

## 57.10 判据与负控制一览

每条新判据都配了一条**能翻脸**的控制，`tests/test_spice_flicker_temp.py`（23 例）与
`server.py` 的 5 条 selftest 一一对应：

| 判据 | 负控制 |
| --- | --- |
| 两点复现 ngspice（<1 %） | `KF=0` 时 1 Hz 掉 >3 倍、且两点**相等** |
| `flicker psd == KF·Id/f`（1e-9） | `Id` 由 shot 的 `2q·Id` 反推，不是硬编码 |
| `S(f)·f` 在 AF=1 与 AF=2 下都恒定 | `S(f)·f²` 在两档下**不**恒定（`AF=2` 拟合出负 `T` 与非零残差） |
| AF=1 时 flicker 比 == shot 比 | AF=2 时 == 平方；线性读法偏差 >0.1 |
| Q/M/J 的 `KF=` → `NOT_MODELED` 且点名 | 同一 deck 去掉 `KF=` → `ok`；`KF=` 的 `.op` 仍 `ok` |
| 两家温度 `case` 逐位等于单温度跑 | 25 ℃ 与 100 ℃ 读数必须不同（"复制+改标签"会露馅） |
| `mc(1)` → `NETLIST_INVALID` 且含 "Monte-Carlo" | 同 deck 用 `1k` → `ok` |
| R 尾部 token 全拒 | `C ic=` / `L ic=` 仍可；E 五字段 → `node2 == 10.0` |
| 未知参数被点名 | 正确调用无 `IGNORED`、无 `unread_arguments` |
| F/H/J/K/S/W 六个 deck 全 `ok` | 能力文案**不含** `"rejects F H J K S W"`、**含** `"F H J K S W are SOLVED"` |
