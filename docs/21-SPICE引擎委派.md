# 21 · SPICE：内置子集 + 可选委派真引擎

## 21.1 为什么不能"直接把 SPICE 内置"

用户的问法很直接：**"为啥只做子集，你直接内置不就好了，不用从头实现啊。"**

这个要求在本项目里撞上一条硬约束：**零 runtime 依赖，只用 Python 标准库**。完整的 SPICE 实现
在这个约束下没有任何"抄过来就能用"的形态：

| 候选 | 体量 / 依赖 | 结论 |
|---|---|---|
| ngspice | 约 50 万行 C，需 flex/bison/autotools 编译，产物是平台二进制 | 无法以源码形式"内置"，也不该把预编译二进制塞进 MIT 仓库 |
| ahkab / PySpice / lcapy | 需要 numpy（+ scipy）；PySpice 还要绑定 libngspice 动态库 | 一装就打破"仅 stdlib" |
| Xyce / LTspice | 同 ngspice，且授权/平台受限 | 同上 |

于是只剩两条诚实做法：

1. 自研一个**可核对的子集**（0.0.1 起就是这样：R C L V I E G D Q M + `.op/.tran/.ac/.model/.temp`，
   未知卡逐条点名拒绝）；
2. **可选委派**：机器上装了 ngspice 就交给它跑子进程，内置求解器不参与。

本轮做的是第 2 条，**没有** vendor 任何二进制、**没有**新增任何 runtime 依赖。二者并不冲突：
内置子集是"保证有答案"，委派是"想要真模型就装上"。

## 21.2 两条铁律

```
engine="builtin"  只用内置子集，绝不调用外部进程
engine="auto"     找到 ngspice 就用它，找不到就用内置（并在 engine_used 里说明）
engine="ngspice"  找不到二进制 => ENGINE_NOT_FOUND 报错，绝不静默回退
```

**为什么"找不到就报错"而不是"那就用子集"**：level-1 MOSFET 与 BSIM 不是同一个器件模型。
调用方明确要求参考实现时，把子集的答案冒充参考实现的答案是**给错答案**，而本项目的第一条纪律是
宁可报错。`ENGINE_NOT_FOUND` 的 envelope 里带 `tried`（找过哪些位置）与修法提示。

反过来，`auto` 回退到内置时也要**说清楚**：`engine_used="builtin"`，`engine_note` 写明
"没找到 ngspice 二进制，这是子集的答案，不是参考引擎的"。不标出来就等于悄悄降级。

## 21.3 如何找到引擎

```
NGSPICE_CMD（模块级，测试注入 argv 前缀）
  > PCB_AGENT_NGSPICE（环境变量：一个路径，或一整条命令行）
  > PATH 上的 ngspice / ngspice.exe / ngspice_con / ngspice_con.exe
  > %LOCALAPPDATA%\ngspice*  %ProgramFiles%\ngspice*  %ProgramFiles(x86)%\ngspice*
    （倒序扫，取 bin/ 下的 exe，或根目录同名 exe）
```

环境变量的取值先当**整个路径**试（Windows 下路径常带空格），不是文件才按命令行拆——把
`C:\Program Files\ngspice\bin\ngspice_con.exe` 按空格拆开是这条链上最容易犯的错。

最后一档 glob 是必需的而不是顺手：`setx` 只对**此后新开的进程**生效，而"刚装完就调工具"
这个过程往往看不见自己刚设的变量。

装了引擎之后还要补一件事：MSYS2 打包的 ngspice 把 XSPICE code model 编成
`<prefix>/lib/ngspice/*.cm`，但二进制里写死的默认路径是 `/mingw64/lib/ngspice`（编译机上的
绝对路径，本机不存在）。少了这些 cm，`T` 卡和所有 XSPICE 器件**加载失败**——一个"能跑但读不懂
半个 deck"的引擎，比没有引擎更容易骗人。所以 `_run_ngspice()` 在 `SPICE_LIB_DIR` 未设、
且 `<exe 所在目录>/../lib/ngspice` 存在时，把它注入子进程环境。

## 21.4 deck 与 rawfile

**deck**：用户网表**原样**交给 ngspice，只做一件改动——把所有 `.op/.tran/.ac/.dc/.four/.noise`
过滤掉，只留调用方要的那一条，末尾补 `.end`。理由：SPICE 自己的约定是"跑第一张分析卡"，
而本工具的分析选择规则是"调用方说的，否则最后一张"。不过滤就会答非所问，而这种错**看不出来**
（照样出数）。网表里没有对应分析卡时不猜（哪来的步长？几个十倍频程？），回 `ARG_REQUIRED`。

**rawfile**：ngspice 两种写法都解析：

- ASCII `Values:` —— **每条记录的第一个数是点序号**，必须先丢掉。不丢的话每个节点电压都会
  整体错一位，且数值仍然"看起来合理"，这是本层最危险的解析错误；
- Binary `Binary:` —— 8 字节小端 double，按 `No. Variables × No. Points` 读，长度不够直接拒绝。

复数（AC）图里**每个向量都是 (re, im) 对，频率轴也一样**（其虚部恒为 0）。把频率当实数读会
在后面 `float()` 处炸掉；把频率当复数存、取值时取实部，才是对的。

## 21.5 cross_check：唯一能审计这条委派的手段

```
sim_spice(netlist, engine="ngspice", cross_check=True)
  -> 同一 deck 跑两个引擎，比节点电压，回
     {compared_nodes, max_abs_diff_v, worst_node, note, elements}
```

只比**同一时刻/同一频点**：

| 分析 | 比较对象 | 原因 |
|---|---|---|
| op | 全部节点 | 直流工作点无条件可比 |
| tran | **只比首个时间点** | 两引擎各自的积分器/自适应步长不同，后面必然分岔；比后面的点等于比积分器，不比模型 |
| ac | **只比首个频点** | 内置 AC 用 deck 自带的扫描点，频率栅格未必对齐 |

节点名两边都对不上时 `compared_nodes=0` 且 note 写"这**不是**通过"，不给空集的假绿。

`elements`（逐元件电压/电流/功率）在外部路径上是 `null`：那是内置求解器用自己的伴随模型算的，
拿节点电压回推元件电流再当成"引擎的结果"报出去，是把自己的算术冒充引擎的答案。

## 21.6 真引擎实测（本机装的 ngspice-47）

本机已装 **ngspice-47**（Windows / MSYS2 构建，解包自清华 MSYS2 镜像的
`mingw-w64-x86_64-ngspice-47`，落在 `%LOCALAPPDATA%\ngspice-47\`，38 MB，含
`bin\` 与 `lib\ngspice\*.cm`；依赖的 6 个 MSYS2 DLL 一并取齐）。装外部二进制**不是**
仓库依赖——仓库仍然只用 stdlib，`pip install zstandard` 只用于一次性解包。

同一 deck 双引擎对比（`cross_check=True`）：

| 网表 | compared_nodes | max_abs_diff_v |
|---|---|---|
| 10 V / 1k / 2k 分压 `.op` | 2 | 5e-09 |
| 二极管 5 V / 1k / IS=1e-14 | 2 | 3.47e-04 |
| BJT V2=0.7 V, BF=200 | 3 | 1.78e-02 |
| RC 阶跃 `.tran` | 2 | 0.0（逐位相同） |
| RLC 串联谐振 `.ac` | 3 | 0.0（逐位相同） |

BJT 的 17.8 mV **是模型差异不是算术差异**（两边模型卡不同），不假装零差。分压的 5e-09 是
ngspice 写 ASCII rawfile 时的有效位数，不是求解差。

两条由此定死的口径：

- `verified_against_engine` **按版本动态判**（`VERIFIED_ENGINES = ("ngspice-47", "ngspice-41")`）：
  装了引擎 ≠ 验证过引擎。别的版本回落 false，并在 `engine_verification` 里说明"这个版本没被本仓库
  的金标网表跑过"。
- `simulate()` 的**默认引擎是 `builtin`**（`engine="auto"` 才能用上引擎）。默认值不能让同一
  网表在两台机器给出不同答案，而调用方从数字上看不出来。

## 21.6b 跨版本对拍：两个构建说同一件事吗

上一节的表是"引擎 vs 本模块内置求解器"。它有一个盲点：**如果我的 rawfile 解析或 deck 改写在
两个版本上的行为不同，我只会看到"某一边差得多"，分不清是哪一边。** 所以又装了第二个构建，
**同一份 deck 同时喂两边**，直接比两个引擎的数字。

第二个构建：**ngspice-41**（Windows / conda-forge 的 `ngspice-exe 41 h295a5eb_0`；注意
conda 的 `ngspice` 包本身是个 14 KB 的元包，它 `depends: ngspice-exe / ngspice-lib`，
要单独取 `ngspice-exe`，7.6 MB，含 `Library/bin/ngspice_con.exe` 与
`Library/lib/ngspice/{analog,digital,xtradev,xtraevt,spice2poly,table}.cm`）。

| 网表 | 比较的标量 | `max\|d\|` |
|---|---|---|
| 分压 `.op` | 2 | **0.0** |
| 二极管 `.op` | 2 | **0.0** |
| BJT `.op` | 3 | **0.0** |
| RLC `.ac`（21 频点 × 3 节点 × re/im/mag） | 189 | **0.0** |
| RC 阶跃 `.tran`（79 点 × 2 节点） | 158 | **1.239e-04 V** |

读法：

- `.op` 与 `.ac` **逐位相同**（0.0，不是"小于容差"）——说明 rawfile 解析、节点名、AC 复数
  拆分这几条管子在两个相差 6 个大版本的构建上都成立。
- `.tran` 差 **1.24e-4 V**（1 V 阶跃上的 1.2e-4 相对），**只在一个时间点**。这不是谁错了：
  两个构建的内部步长控制不同，各自的输出网格本来就不一样；我比的是"各自网格上最坏那一点"，
  所以这个数**同时包含积分器差异与网格差异**，不能当"引擎 A 比 B 准"用。要拆开必须插值到
  统一网格，那是另一个实验，没做。
- 因此 `.tran` 这一格的上限被记成 **1.3e-4 V**，并写进 `ENGINE_VERIFICATION` 原文，而不是
  "逐位相同"。

对拍脚本留在 `_scratch/ng_cross.py`（不入库；它引用 `_scratch/ngspice/` 下的两个二进制，
那些都不进仓库）。版本门控本身有测试，且**不需要真引擎**：假引擎现在认 `$FAKE_VERSION`，
`tests/test_spice_engine.py::TestVersionGating` 让替身自称 `ngspice-47` / `ngspice-41`（必须
为 true）与 `ngspice-45` / `fake-ngspice`（必须为 false）。

## 21.6a 装了引擎真正多出什么：未解析的 deck 直通

内置解析器按名字拒绝 `.subckt/X`、`B`、`T`、`K`、`S`、`W`、`F`、`H`、`J`、`O`、`U`、`A`。
在委派路径上，答案本来就是引擎自己的 rawfile 而不是本模块的模型，所以**解析失败不再致命**：

- deck 原样交给引擎（只过滤分析卡、补 `.end`）；
- 节点表**从 rawfile 里读**（`x1.m` 这样的子电路内部节点自然出现）；
- 分析类型/步长退化为**文本扫描**（最后一张 `.op/.tran/.ac`，`.tran` 的第二个 token）；
- 信封带 `netlist_parsed: false` + `netlist_parse_note`（写明"本模块解析器拒绝了这份 deck"），
  绝不假装这是一次完整解析；
- `engine="builtin"` 对同一份 deck **仍然拒绝**（`NETLIST_INVALID`）——能力差就如实呈现。

顺带修掉一个只有直通才会暴露的缺陷：deck 过滤原来用 `startswith(".end")` 丢行，把
`.ends` 也丢了，于是 `.subckt` 块体少一个结束行、ngspice 直接 `ENGINE_FAILED`。

## 21.7 失败路径一览

| reason_code | 触发 | 携带信息 |
|---|---|---|
| `ENGINE_NOT_FOUND` | `engine="ngspice"` 但无二进制 | `tried`（找过哪些位置） |
| `ENGINE_TIMEOUT` | 超过 `NGSPICE_TIMEOUT_S=120 s` | 被 kill，无部分结果 |
| `ENGINE_FAILED` | rc ≠ 0 | `exit_code` + `log_tail`（引擎自己的抱怨） |
| `ENGINE_OUTPUT_INVALID` | rc = 0 但没 raw / raw 解析不了 | `log_tail` 或 `raw_bytes` |
| `ARG_CONFLICT` | `.temp` 卡与 `temp_c` 同时给 | 两张温度卡谁生效取决于行序，拒绝 |
| `ARG_REQUIRED` | 网表里没有所请求的分析卡 | 不猜步长/频点 |
| `NETLIST_INVALID` | 内置求解器遇到未实现的卡 | 内置路径拒绝；`engine="ngspice"` 时同一 deck 直通引擎 |

## 21.8 代码对应

- `src/pcb_agent_mcp/calc/spice.py`
  - 内置求解器：`_simulate_builtin()`（原 `simulate`）
  - 委派：`find_ngspice()` / `_deck_for_ngspice()` / `_run_ngspice()` / `_parse_raw()` /
    `_raw_nodes()` / `_external_envelope()` / `_cross_check()`
  - 未解析 deck 的文本兜底：`_last_analysis_card()` / `_tran_step_textual()`
  - 对外唯一入口：`simulate(netlist, analysis=None, max_iter=200, max_points=200,
    temp_c=None, engine="builtin", cross_check=False)`
- `tests/test_spice_engine.py`（37 例：假引擎 `tests/_fake_ngspice.py` 覆盖解析/过滤/失败
  路径/cross_check、版本门控自称，7 例 `skipUnless(spice.find_ngspice())` 对着本机真引擎跑，其余纯解析/定位）
- `sim_spice` 工具新增参数 `engine`、`cross_check`
