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
```

环境变量的取值先当**整个路径**试（Windows 下路径常带空格），不是文件才按命令行拆——把
`C:\Program Files\ngspice\bin\ngspice_con.exe` 按空格拆开是这条链上最容易犯的错。

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

## 21.6 未验证声明（诚实清单的一部分）

本机**没有 ngspice 二进制**（`shutil.which` 为空，`C:\Program Files\ngspice*` 不存在），所以：

- 外部路径**从未对着真 ngspice 跑过**，每条外部 envelope 都带 `verified_against_engine: false`；
- 管线用 `tests/_fake_ngspice.py`（假引擎，按 `$FAKE_SPEC` 决定它答什么）测通：ASCII / Binary
  两种 rawfile、点序号丢弃、deck 过滤、三条失败路径（rc≠0 / 不写 raw / 根本没装）、cross_check
  的三种口径；
- 装了真 ngspice 之后，同一工具立刻能跑 `.subckt`、B 源、开关、传输线等内置拒绝的卡；
  到那时把 `verified_against_engine` 改成 true 需要一个前置条件——**对着真引擎跑一遍现有金标网表**，
  这件事本机做不了。

## 21.7 失败路径一览

| reason_code | 触发 | 携带信息 |
|---|---|---|
| `ENGINE_NOT_FOUND` | `engine="ngspice"` 但无二进制 | `tried`（找过哪些位置） |
| `ENGINE_TIMEOUT` | 超过 `NGSPICE_TIMEOUT_S=120 s` | 被 kill，无部分结果 |
| `ENGINE_FAILED` | rc ≠ 0 | `exit_code` + `log_tail`（引擎自己的抱怨） |
| `ENGINE_OUTPUT_INVALID` | rc = 0 但没 raw / raw 解析不了 | `log_tail` 或 `raw_bytes` |
| `ARG_CONFLICT` | `.temp` 卡与 `temp_c` 同时给 | 两张温度卡谁生效取决于行序，拒绝 |
| `ARG_REQUIRED` | 网表里没有所请求的分析卡 | 不猜步长/频点 |
| `NETLIST_INVALID` | 解析器遇到未实现的卡 | 节点表来自本机解析，未解析的卡会让报告不完整 |

## 21.8 代码对应

- `src/pcb_agent_mcp/calc/spice.py`
  - 内置求解器：`_simulate_builtin()`（原 `simulate`）
  - 委派：`find_ngspice()` / `_deck_for_ngspice()` / `_run_ngspice()` / `_parse_raw()` /
    `_raw_nodes()` / `_external_envelope()` / `_cross_check()`
  - 对外唯一入口：`simulate(netlist, analysis=None, max_iter=200, max_points=200,
    temp_c=None, engine="auto", cross_check=False)`
- `tests/test_spice_engine.py`（28 例）+ `tests/_fake_ngspice.py`（假引擎）
- `sim_spice` 工具新增参数 `engine`、`cross_check`
