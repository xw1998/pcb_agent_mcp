# 43. 随包 SPICE 内核：把 `A` 卡从「拒绝」变成「能跑」

到 `0.0.36` 为止，这个服务器对 `sim_spice` 的立场是：内置子集能解的都在内置解，解不了的
（只剩 `A`，XSPICE 的器件与第二套器件描述语言）按名字拒绝，想让引擎跑就自己装 ngspice。
`0.0.37` 把后半句去掉了——仓库里自带一份官方 `ngspice-47` 的 Windows 构建，`engine="ngspice"`
在没有系统安装的机器上也能跑，于是 `A` 卡第一次有了「能给出答案」的路径。

这一篇记的是**打包之后才量出来的事**，以及一件必须回收的旧结论。

---

## 43.1 为什么是「打包」而不是「自己实现」

用户的原话是「SPICE 不是有最小内核随工具打包推送呗不用自己实现」。这条指令把边界画得很清楚：

- 不写第二个 SPICE。`A` 卡背后是 XSPICE 的整套 code model 框架（`analog`/`digital`/`table`/
  `tlines`/`xtradev`/`xtraevt`/`spice2poly` 七套），自研等于换项目；
- 也不引入 runtime 依赖：随包的是**独立可执行文件**，用 `subprocess` 调，不是 `import`，
  所以「零 runtime 依赖」这条对本仓库的 Python 代码依然成立（`stdlib` 之外什么都不用装）。

01.4 里那条「外部二进制只作一次性证据」的口径因此**变了**，这里如实登记：随包的二进制不是一次性
证据，它是交付物的一部分。变化的只是分发形态，不是依赖形态——删掉 `vendor/ngspice/`，MIT 代码
一行都不用改，`engine="auto"` 的行为也不变（见 43.4）。

## 43.2 打包了什么（14 MB）

| 文件 | 体积 | 为什么必须在 |
| --- | --- | --- |
| `bin/ngspice_con.exe` | 7.6 MB | 控制台构建，只有一个入口 |
| `bin/libomp140.x86_64.dll` | 0.7 MB | 硬链接依赖 |
| `bin/samplerate.dll` | 1.6 MB | 硬链接依赖 |
| `bin/sndfile.dll` | 2.6 MB | 硬链接依赖 |
| `lib/ngspice/*.cm`（7 个） | 1.5 MB | XSPICE code model，缺一个 `A` 就 `MIF-ERROR` |
| `share/ngspice/scripts/spinit` | 4 KB | 启动时加载上面那七个 |
| `COPYING` / `NOTICE.md` / `README.md` | — | 许可证与来源，见 43.6 |

那三个 DLL 不是可选的：`ldd` 会列出它们，而**删掉任何一个，`ngspice_con.exe` 直接以退出码 127
结束、一句提示都没有**。这是本轮第一个「看起来像别的问题」的坑。

丢掉的：`bin/ngspice.exe`（GUI 构建，8.6 MB）、`lib/ngspice/*.osdi`（16 个 OpenVAF 模型，5.6 MB，
`spinit` 只在 `osdi_enabled` 被打开时加载，本仓库不打开）、`share/ngspice/scripts/` 里除 `spinit`
以外的脚本、`examples/`、`docs/`（除 `COPYING`）。整包解压 57 MB → 随包 14 MB。

## 43.3 三条只能量出来的路径事实

三条都用负控制量过，因为「`A` 卡跑出答案」本身不说明**为什么**跑出来了。

**(1) `spinit` 是按可执行文件所在目录找的，不是按 cwd。** 官方 `spinit` 内部写的是
`codemodel ../lib/ngspice/*.cm`，而 ngspice 编译进去的默认脚本目录是 `../share/ngspice`——两者
都是**相对 exe 所在目录**解析。所以在无关目录里启动也能加载；把 `lib/` 改名走，同一个 deck 立刻
变成 `Error opening code model "../lib/ngspice/analog.cm": No such file or directory!`。

**(2) 那七个 `.cm` 是真被用到的，不是装饰。** 负控制：把 `lib/ngspice/analog.cm` 换成一个文本文本
文件，`gain` 模型立刻消失——`MIF-ERROR - unable to find definition of model gain1`。

**(3) `SPICE_LIB_DIR` 会顶掉 `spinit` 的查找基准**，这是本轮最贵的一条。`_run_ngspice` 原本对
「exe 旁边有 `lib/ngspice`」的树设置 `SPICE_LIB_DIR`（`0.0.30` 前后为了让 T 卡能加载加的）：
对**没有** `spinit` 的手工解包树这是对的，对**有** `spinit` 的官方树则是致命的——一旦设上，
引擎就报 `Warning: can't find the initialization file spinit.` 并让每张 `A` 卡 `MIF-ERROR`。
修法是按树判断：

```python
init = <exe>/../share/ngspice/scripts/spinit
if os.path.isdir(<exe>/../lib/ngspice) and not os.path.isfile(init):
    env["SPICE_LIB_DIR"] = <exe>/../lib/ngspice
```

有 init 的树什么都不用给，没有的才需要指针。`tests/test_spice_bundled.py` 里的 `gain`/`limit`
用例就是这条的负控制：条件写反，它们会带着引擎自己的抱怨挂在 `log_tail` 上。

**(4) 官方 `spinit` 里有 `set filetype=ascii`，所以随包引擎写的是 ASCII rawfile。** 手工解包树
没有 `spinit`，写的是二进制——于是**同一份 `ngspice-47`，换棵树，rawfile 的编码就变了**。
这条事实是这轮全量回归才炸出来的（16 个「真引擎对拍」用例同时红），但它不是测试的问题，
是 `_parse_raw` 的两个真缺陷（见 43.5 末尾）：它把「引擎答案读对了、本模块读错了」的失败
报成了 `ENGINE_OUTPUT_INVALID`。

## 43.4 引擎查找顺序（以及为什么 `auto` 不看随包）

`engine="ngspice"` 的顺序：

| 顺序 | 来源 | 说明 |
| --- | --- | --- |
| 1 | `PCB_AGENT_NGSPICE` | 显式覆盖，永远第一 |
| 2 | `PATH` 上的 `ngspice` / `ngspice_con` | 用户自己放进 PATH 的，算显式选择 |
| 3 | **`vendor/ngspice/`** | 本仓库能担保的那一份（16 进制构建=`ngspice-47`，在 `VERIFIED_ENGINES` 里） |
| 4 | `%LOCALAPPDATA%` / `Program Files` 下的 `ngspice*` | 目录扫描，最弱的一档：手工解包的树经常缺 `spinit` |

随包排在目录扫描**之前**的原因是可复现：一个「找到了但加载不了 code model」的安装不该把
我们能担保的那一份顶掉。`engine="auto"` 则**完全不看**随包（`find_ngspice()` 的默认参数
`allow_bundled=False`），理由写在 `ENGINE_NOTE` 里：仓库长出一个 14 MB 目录，不该让所有既有调用方
拿到不同的答案。`auto` 在无引擎机器上仍然是内置子集，`builtin` 仍然是默认引擎。

副作用是 `auto` 遇到装坏的引擎会失败——所以 `auto` 失败时信封会多一个
`bundled_engine` 字段并在 `hint` 里点名「`engine='ngspice'` 会用随包那一份」，否则机器上唯一能
工作的引擎反而不可发现。

## 43.5 `A` 卡：从「按名字拒绝」到「有答案」

| deck | 结果 |
| --- | --- |
| `A1 1 10 g1` + `.model g1 gain(gain=2.0)`，1 V 输入 | `V(10) = 2.000000e+00`（`delta 1e-9`） |
| `A1 1 2 lim1` + `limit(out_upper_limit=1)`，5 V 输入 | `V(2) = 1.0`（削顶） |
| 上面那张 `gain` 换成 `.tran`，1 V 阶跃、`gain=3` | 末点 `3.0`（`delta 1e-6`） |

信封同时要诚实：这张 deck **不是**这个模块解析的——`netlist_parsed=false`，`netlist_parse_note`
点名 `A` 卡与 XSPICE；内置子集仍然按名字拒绝（`NETLIST_INVALID`）。`cross_check` 对这类 deck
会说「内置解不出来，没什么可比」，而**不是**给出一个 0 差值假装核对过了。

随包这一份的版本串落在 `VERIFIED_ENGINES` 里，所以 `verified_against_engine=true`：它不是
「一个二进制」，是这个仓库量过金标网表的那一版。

**ASCII rawfile：随包之后才暴露的真缺陷。** 全量回归里 16 个 `RealEngineAgrees` 用例一起报
`ENGINE_OUTPUT_INVALID`，消息是 `ASCII rawfile has 1 records but the header says 3 points`
（AC）与 `… 33 records but the header says 61 points`。两个都是**本模块的错**，不是引擎的：

1. 复杂值在 ASCII 里写成 `1.000000000000000e+00,-2.000000000000000e+00`——**一个空白 token、
   两个数**。旧分支按空白切，每个点只数到 `1 + nvar` 个 token，而不是 `1 + nvar*2`；
2. ASCII 的 `Values:` 一直到**文件尾**，第二张 plot 的 `Title:` / `No. Points:` 也被当成数值
   token（`.noise` 的两张 plot 就是这样）。旧的 `bytes_used` 直接取「整个文件剩余」，所以
   `_raw_plots` 永远只走第一张 plot。

修法：按行扫，`,` 与空白都算分隔符，遇到第一行「不是数值」就停，并把那个偏移回报成 `bytes_used`
（ASCII rawfile 是纯 ASCII，字符偏移即字节偏移）。三个新用例钉住这个形状——复杂值两个数、两张 plot
按偏移切开、表头点数与实测不符必须报错（`tests/test_spice_bundled.py` 的 `TestAsciiRawfile`）。
**修之前那 16 个用例是红的**，这就是它的负控制。

## 43.6 许可证：不是单一的 GPL

上一轮笔记里写的是「ngspice 是 GPL」，`COPYING` 拆开看是**混搭**，这里按实际内容改正：

| 组件 | 形式 | 许可 |
| --- | --- | --- |
| 前端 / 模拟器件 | `bin/ngspice_con.exe` | Modified BSD（3 条款） |
| XSPICE code model（`analog`/`digital`/`xtradev`/`xtraevt`/`spice2poly`/`tlines`） | `lib/ngspice/*.cm` | 公有领域（Georgia Tech） |
| `xspice/icm/table` | `lib/ngspice/table.cm` | **GPLv2 or newer** |
| KLU 求解器 / numparam | 编进 exe | LGPLv2 / LGPLv2+ |
| OSDI 紧凑模型（MPLv2.0） | **未随包** | — |
| sndfile / samplerate / libomp | `bin/*.dll` | LGPLv2.1 / BSD / Apache-2.0+LLVM |

所以准确说法是：**主体 Modified BSD + 公有领域 XSPICE，其中一个 code model（`table`）是 GPLv2+，
二进制里还有 (L)GPL 组件**。三种许可都允许**未修改**的二进制再分发，前提是许可证文本与来源随行、
对应源码可得——这就是 `COPYING` 原样留在 `vendor/ngspice/` 的原因，也是 `NOTICE.md` 写清版本、
来源 URL、sha256 与「未做任何修改」的原因。

MIT 与它们能共存，是因为**它们是两个程序**：本仓库的代码从不 include 它的头文件、不链接它的
目标文件、不 vendored 它的源码，只用 `subprocess.run` 起一个进程——与「shell 脚本调用 `grep`」
是同一层关系。若将来改了这个二进制，义务就从「指向上游源码」变成「公布你的源码」，且 `table.cm`
那部分还要额外满足 GPLv2+。

## 43.7 回收一条旧结论

`0.0.36` 在 `docs/17`、`docs/42` §42.9、`CHANGELOG` 与 `tools.py` 的自述里都写过同一句话：
**「本机 `ngspice-47` 没有编译 XSPICE，`code_model:` 命令不存在，`A` 卡一律 `MIF-ERROR`」**，
并据此把 id 96 的自检降级成「测迁移本身」。

这句话**是错的**，两处误读叠在一起：

1. `MIF-ERROR - unable to find definition of model amp` 说的是**模型名**不存在。`A` 卡的模型类型
   是 `gain`（`.model gain1 gain(gain=2.0)`），不是 `amp`，参数也不是 `in_gain`。用对名字以后
   同一个二进制立刻给出 `V(10)=2.0`。
2. `code_model: no such command available in ngspice` 是**命令行被撕开**造成的假象：
   `printf 'codemodel C:\Users\...'` 里 `\U` 被 `printf` 当成转义，命令碎成多行，ngspice 把
   残余当成未知命令。改用 `cat > cmds.txt <<'EOF'` 写命令文件后命令正常执行。

真相是**本机那份手工解包树缺 `spinit`**（`lib\ngspice\` 里只有 `.cm` 与 `ivlng.dll`，没有
`scripts/`），于是 code model 一个都没加载。旧结论因此不改写历史，而是在原处加修订块
（`docs/17` §17.4/§17.5、`docs/42` §42.9、`CHANGELOG` 的 `0.0.36` 条目）并把这条记录在这里：
**一条读数（`MIF-ERROR`）被读成了一个结论（没有 XSPICE），而它只是「文件名写错了」**。

## 43.8 自证

```bash
# 1) 随包引擎在不在、是哪一版
vendor/ngspice/bin/ngspice_con.exe --version        # ngspice-47 ... Aug 11 2026

# 2) A 卡端到端（gain）：期望 V(10) = 2.000000e+00
env -u PYTHONHOME PYTHONPATH=src python -c "
from pcb_agent_mcp.calc import spice
print(spice.simulate('* x\nV1 1 0 1\nA1 1 10 g1\n.model g1 gain(gain=2.0)\n'
                     'R1 10 0 1k\n.op\n', engine='ngspice')['value']['nodes_v'])"

# 3) 负控制：把 code model 弄坏，同一个 deck 必须失败
#    注意 Windows 程序不认 /tmp 这类 MSYS 路径，deck 要 cygpath -w 转一次
cp -r vendor/ngspice /tmp/ngspice_bad
echo garbage > /tmp/ngspice_bad/lib/ngspice/analog.cm
/tmp/ngspice_bad/bin/ngspice_con.exe -b "$(cygpath -w /tmp/gain.cir)" 2>&1 | grep MIF-ERROR
# 实测输出：Error opening code model "../lib/ngspice/analog.cm" ... /
#           MIF-ERROR - unable to find definition of model g1

# 4) 用例
env -u PYTHONHOME PYTHONPATH=src python -m unittest tests.test_spice_bundled -v

# 5) 负控制：ASCII rawfile 的两个坑（复杂值 / 两张 plot）由 TestAsciiRawfile 钉住
```

## 43.9 仍然不做

- **只打 Windows 构建**。`find_bundled_ngspice()` 在非 Windows 平台直接返回 `None`（`.exe`
  跑不了），那些机器继续走「自己装 ngspice」那条老路，行为与 `0.0.36` 一致。
- **不做进程内 DLL**。`ngspice-47_dll_64.7z`（`libngspice`）能用 `ctypes` 在进程里跑，但要把 raw
  数据回调与线程安全一起做完，收益只是省一个子进程的启动开销（~10 ms）。登记为后续，不做。
- **不给 `auto` 加随包分支**。理由见 43.4：那会改变所有既有调用方的答案。
- **不替引擎解释它自己的错误**。`ENGINE_FAILED` 只把引擎的日志尾巴交出去（`log_tail`），
  不翻译成「大概是某张卡」。
- `A` 卡**原先整体不在内置**。随包只解决「能不能跑」，不解决「谁来解」。`0.0.39` 把其中
  **纯代数**的两种 code model（`gain`、`limit`）搬进了内置（见 [17 §17.11](17-仿真层.md) 与
  下面 43.10）；剩下的仍然按名字拒绝，因为「跳过一张不认识的卡然后给出一个数字」正是这个模块
  一直在防的事。

## 43.10 `0.0.39`：两种 code model 进了内置（`gain` / `limit`）

随包解决了「谁来跑」，没解决「谁来解」。`0.0.39` 把 `A` 卡里**没有状态、没有采样时钟、只有一个
端口**的那两种搬进内置求解器——它们不是器件方程，是两行代数：

    gain :  out = gain * (in + in_offset)
    limit:  out = gain * (in + in_offset), 再夹到 [out_lower_limit, out_upper_limit]

四条都先量后写：

1. **`A` 卡是两节点**（`A1 in out model`），不是 E 卡的四节点。三节点写法 `A1 1 0 10 g1` 被引擎
   直接拒成 `incomplete or empty netlist`；两节点写法 `gain(gain=2)` 读 2.000 V。
2. **`in_offset` 在乘之前**：2 V 输入配 `gain(gain=2 in_offset=1)` 读 **6.0 V**，不是 5.0 V
   （若是先乘后加，会是 5.0）。这一条决定表达式顺序，猜错就是系统性偏差。
3. **`limit` 必须两个边界都给**：只写 `out_lower_limit=-1` 时引擎自己报 incomplete，所以内置
   没有可继承的缺省——两个都缺就按名字拒绝，而不是自己编一个。
4. **`limit_range` 拒绝而不近似**：0.9 V 输入、`limit_range=0.5` 实测落在 **0.82 V**，硬夹会写
   0.9 V。问拐点的人恰恰在意这一处，所以按名字拒绝（`fraction=` 同理）。

内部形态是一个「由代数表达式驱动的电压源」：行方程 `v(out) - f(v(in)) = 0`，夹紧段用上一次迭代
的值线性化（带内斜率 `gain`，带外斜率 0），所以落在边界上的解是真解而不是卡住的迭代。`.ac` 下
`limit` 一律拒绝——夹紧块的小信号增益取决于工作点落在哪一段，而这套求解器不带工作点进扫频。

判据：`tests/test_spice_bundled.py` 的
`test_builtin_solves_the_two_algebraic_code_models` / `test_a_code_model_with_state_is_still_refused_by_name` /
`test_limit_range_is_refused_rather_than_clamped` / `test_cross_check_now_really_compares_this_deck`，
selftest 157/158/159。负控制是后两条：**有状态的 code model 与会动拐点的参数仍然按名字拒绝**，
所以「内置现在能跑 `A` 了」不等于「内置把不认识的卡当没写」。
