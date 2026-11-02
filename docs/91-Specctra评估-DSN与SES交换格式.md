# Specctra 评估：DSN / SES 这两个文件能给我们 PCB 绘制部分什么

> 版本 0.0.89（只加文档，工具数 180 / `available` 174 / `not_implemented_yet` 23 / 自检 321 全部不变）。
> 这份文档**不改一行运行代码**。它的产出是一个判决（接不接）和五条机制（借哪几条），
> 其中两条是我们现在确实缺的，已经记在 §91.4 里并标明「记账，未实施」。

## 91.1 这份文档回答什么

用户的原话是「继续执行，做完后安装下 `rea` 然后逆向下 Specctra 看看对于我们 pcb 绘制部分是否有帮助和参考思路」。
`rea` 已经装好（§91.10 有实测），这一篇回答后半句。结论先给：

1. **不接** DSN/SES 交换。三条理由在 §91.7，都不是「难」，是「接了要付的账我们不打算付」。
2. **借五条机制**（§91.4）。其中 A1（`fixed` 位）与 A2（owner）是我们现在**确实缺**的，而且跟 DSN 没关系也能做。
3. 有一条我们**已经在做且方向一致**（外部结果不接管铺铜，§91.5 第 3 条），文档里给出对照，避免以后把它当成新发现。

## 91.2 取证：结论是从哪来的

一手规范**没拿到**。Cadence 的 *SPECCTRA Design Language Reference* 在 KiCad 源码注释里指向的是早年挂在 Yahoo group 文件区的一份 `specctra.pdf`（`specctra_import.cpp` 头部注释原样写着这条路径），现在取不到正文。
所以本文所有语法结论来自**两个独立实现 + 两个真实文件**，凡只有一处实现印证的都标「单侧」。

| 来源 | 具体文件 | 规模 | 用途 |
|---|---|---|---|
| KiCad（GPL-3.0，只读思路） | `pcbnew/specctra_import_export/specctra.cpp` | 4051 行 | 词法/语法与 6 个 section 的类模型 |
| | `.../specctra.h` | 4024 行 | 语义对象（PATH / WIRE / PADSTACK / CLASS / SESSION…） |
| | `.../specctra_export.cpp` | 1878 行 | **导出** DSN：谁被写、谁被丢 |
| | `.../specctra_import.cpp` | 574 行 | **导入** SES：合并语义 |
| | `.../specctra.keywords` | 388 个非注释 token | 词表本体 |
| FreeRouting（GPL-3.0，只读思路） | `io/specctra/SesWriter.java` | 17080 B | SES 写出形态 |
| | `io/specctra/DsnWriter.java` / `RulesWriter.java` / `RulesReader.java` | 3.5 KB / 4.2 KB / 13.6 KB | DSN 与规则文件的往返范围 |
| 真实样本（DSN） | LibrePCB 2.0.0 导出的 `Issue676-ch32v-tx118s.dsn` | 541 行、2 层、9 网、48 段 `wire`、6 个 `via`、7 个 `padstack`、9 个 `image` | 语法的真身 |
| 真实样本（SES） | KiCad 5.1.5 写出的 `Issue026-J2_reference.ses` | 701 行 / 14421 B、93 段 `wire`、10 个 `via`、12 个 `net` | 会话的真身 |

**一条交叉印证值得单独写下来**：KiCad 官方 dev-docs 的原句是
> 「Syntax is based on the Specctra DSN file format.」

也就是说，我们现在读 `.kicad_pcb` 用的那套 s-expr 语法，**本来就是 DSN 的方言**（`adapters/kicad_pcb.py` 的 tokenizer 与 `fidelity/sexpr.py` 是同一张转义表，两个读者对转义有分歧就是对板子有分歧）。
所以摆在面前的从来不是「要不要引入一种新格式」，而是「**要不要用它的语义层**」——语法我们早就在用了。

## 91.3 它是什么

### 三个文件角色

| 后缀 | 角色 | 谁写 | 谁读 |
|---|---|---|---|
| `.dsn` | 待布线设计：板框 + 层 + 规则 + 摆位 + 封装 + 网表 + 已有铜 | 布局系统（KiCad / LibrePCB / 立创…） | 布线器（FreeRouting / Specctra） |
| `.ses` | 会话结果：`base_design` + 摆位回写 + `was_is`（换脚记录）+ 布线 | 布线器 | 布局系统 |
| `.rules` | 可选，规则单独成文件（FreeRouting 独有） | 布线器/布局 | 布线器 |

### DSN 的六个 section

`(pcb NAME (parser …) (resolution …) (unit …) (structure …) (placement …) (library …) (network …) (wiring …))`

| section | 装什么 | 我们的对应物 |
|---|---|---|
| `parser` | 词法自声明：`string_quote`、`space_in_quoted_tokens`、`host_cad`/`host_version` | `source` 里的格式与路径 |
| `resolution` / `unit` | 量子与工程单位 | `canonical.py` 的 C1（只在一处归一） |
| `structure` | `layer` / `boundary`（板框）/ `plane` / `via` / `rule`（width, clearance, 各 `type` 变体）/ `keepout` / `grid` / `snap_angle` | `board.outline` / `board.stackup` / `route/grid.py` 的双栅格 / `route/rules.py` |
| `placement` | `component` → `(place REF x y side rot)` | `board.footprints` |
| `library` | `image`（封装：`pin` + `outline`）/ `padstack`（焊盘与过孔形状） | `board.footprints[].pads` / 过孔模板 |
| `network` | `net`（`pins` 列表）+ `class`（规则 + 网列表） | `board.nets` / 网类 |
| `wiring` | `wire`（`path layer width x y …`）/ `via`（`padstack x y`） | `route` 产出的 ops |

### SES 的形状（真样本）

```
(session NAME
  (base_design NAME)
  (placement (resolution um 10) (component ID (place REF x y front 90)) …)
  (was_is)                                   ; 换脚/换门记录，本样本为空
  (routes (resolution um 10) (parser (host_cad "KiCad's Pcbnew") (host_version …))
    (library_out (padstack "Via[0-1]_800:400_um" (shape (circle F.Cu 8000 0 0)) (attach off)) …)
    (network_out (net NAME (wire (path F.Cu 2500 x y x y …)) (via ID x y (type fix))) …)))
```

### 三条会咬人的口径

1. **Y 轴向上，单位是浮点。** KiCad 两个方向都取负：导出时 `mapY` 注释写「make y negative, since it is increasing going down」，导入会话时 `mapPt` 又取一次负。我们的 canonical 是 Y 向上，正好一致——但这不是巧合，是 C1 当初就选对了。
2. **分辨率与写入精度是两件事。** `(resolution um 10)` 声明的是**量子** = 每 µm 10 个单位 = 0.1 µm（KiCad 注释原话「tenths of micrometers, which is 100 nm resolution」），而坐标照旧写 `%.6g` 浮点。把量子当「只能写整数」会白丢精度；当「没有量子」会让不同工具舍入到不同位置。
3. **`(plane …)` 不是铺铜**（单侧，来自 KiCad 导出注释）。DSN 的 plane 是「焊盘/铜皮对象」，把 zone 写成 plane 语义就变了；KiCad 的写法是 zone → `(wire (polygon …))`。

## 91.4 五条可借

每条四栏：机制 / 我们现在 / 抄什么 / 代价与门槛。

### A1 `(type fix)`：锁定的铜不回流 —— **记账，未实施**

- **机制**：导出侧把 locked 的 track/via 标成 `(type fix)`，KiCad 注释原文「tracks with fix property are not returned in .ses files」；导入侧**先删所有未锁定的 track/via，再添加会话里的**。于是「固定」有了一个可验证的语义：它不进对方的候选池，也不回流覆盖自己。
- **我们现在**：`route/router.py` 有 `incremental` 与 `_existing_tree`（复用既有树），`route/blockers.py` 判占据；但**没有「这块铜不许动」的一等公民**——rip-up 只按几何与代价挑牺牲者，已验收的铜和刚铺的铜在候选池里是同一个身份。
- **抄什么**：给 op 一个 `fixed` 布尔，语义三条——不进 rip-up 候选、不参与换层、重跑结果对它逐位不动。这样「增量重布」才是一个能写进交付说明的承诺，而不是「应该不太会动」。
- **代价**：要动 `_Emitter` 的牺牲者选择与 `_existing_tree` 的归属判定；**必须先有能失败的负控制**（把一块 `fixed` 铜横在必经之路上，布线器应报 no-route，而不是绕过去把它挪开）。

### A2 合并 = 「替换未锁定」，不是 diff —— **记账，未实施**

- **机制**：SES 导入是「删未锁定 + 全量添加」，于是「板上的非固定铜全部来自这一个文件」是一句可验证的话。配套还有 `was_is`：把 pins/gates 交换记录写进会话，让「为什么这两个脚换了」在文件里有据。
- **我们现在**：`edit/plan.py` 分步写 + `lceda_write` 落盘 + `ledger.py` 记账，但没有 **owner** 字段，一次外部或上一轮的结果进来会与既有铜混在一起，事后分不清哪段是谁画的。
- **抄什么**：归属（owner = 来源 job id），写进 `ledger`。这不是格式问题，是记账问题，跟 DSN 无关也能做。
- **代价**：改动面小（`ledger.py` 加一栏 + 写路径透传），但**一旦有了 owner 就必须有人读它**，否则又是一栏没人看的元数据。

### A3 规则优先级：特异性 vs 最严，是两条正交的轴

- **机制**：DSN 的规则优先级是多层**覆盖**（KiCad 源码原样引用 spec 第 11 页）：`pcb < layer < class < class layer < group_set < group_set layer < net < net layer < group < fromto < fromto layer < class_class < class_class layer < padstack < region < class region < net region < class_class region`。越具体越赢。
- **我们现在**：`route/rules.py` 显式取**最严**（并公示 `assumed`），`route/grid.py` 用全板最严网类建通/阻格。
- **抄什么**：不换语义（最严在安全上是对的），但要**承认两者回答的是不同问题**——DRC 问「不许违反哪条」（最严正确），布线问「用户想让它多宽」（特异性正确）。我们现在把最严也用在布线取数上，这在安全上对、在可用性上是**保守损失**，README 里应当写成「选择」而不是「事实」。
- **另一条要写进口径**：`class_class`（类对类）这一层我们没有单独表达。我们的爬电（IEC 60664-1）实际是「两网中较严者」的并集语义，等价于 `class_class` 的一个特例——**不是缺功能，是没写清楚**。这条只改文档，不改代码。

### A4 文件自声明词法

- **机制**：`(parser (string_quote ") (space_in_quoted_tokens on))` —— 引号字符与「引号内是否允许空格」由**文件自己声明**，解析器读了再定词法。
- **我们现在**：`fidelity/sexpr.py` 与 `adapters/kicad_pcb.py` 各有一张 `_ESCAPES` 固定表，两处**故意一致**（注释写了理由）。
- **抄什么**：读**第三方** s-expr 之前先找 `parser` 段。固定表只对我们自己的两种格式成立，对别人的文件是假设。

### A5 量子与打印精度分开登记

- **机制**：见 §91.3 第 2 条。
- **我们现在**：canonical 是 mm 浮点；`edit/epro_edit.py` 按立创口径 `round(v, 4)`（文件也确实只带 4 位）；KiCad 侧是 nm 整数。
- **抄什么**：不要把这三种混成一个「精度」字。写路径落盘前必须知道目标要的是哪一种，且**量子吸附要与对方读回时的吸附同向**（否则往返一次就漂一个量子）。

## 91.5 五条不抄

1. **QARC（弧）**：FreeRouting 不支持弧——KiCad 导出里两处注释写着「this is best done by 4 QARC's / by QARC's but freerouter does not yet support QARCs」，于是圆与弧一律折线化。若将来做交换，弧**必须折线化并公示弦高**，不能假装无损。
2. **逐项 try/catch + 最后报一次 skip 计数**：KiCad 是为了「单个无法解析的件不该沉掉整块板」（`m_skippedCount` 收尾报一次 warning）。我们的口径是 fail-closed；若将来做导入，正确形态是**条数上限 + 全量清单落盘**，不是静默跳过。
3. **让 session 里的 `polygon` 接管铺铜**：KiCad 导入时直接**忽略**会话里的 polygon（板上已有 zone）。我们的 `refill_pours` 委派 `ZONE_FILLER` 与它同侧——**外部结果不接管铺铜**。这条我们已经在做，写在这里是为了以后别改坏。
4. **把 388 个关键字当成能力清单**：词表很大，但两个成熟实现共同实现的只是 `width` / `clearance` / `use_via` 这一小块。`length` / `max_delay` / `parallel` / `shield` / `crosstalk_model` 这些**在词表里，但没人写也没人读**。「规范上有」≠「生态在用」。
5. **GPL 代码**：KiCad 与 FreeRouting 都是 GPL-3.0（FreeRouting 的 SPDX 已核为 `GPL-3.0`），本仓库是 MIT。**一行都不能抄**，只能借协议与机制。这是我们这条线最大的约束，比「难不难」更硬。

## 91.6 一个反面事实：DSN 传的是「几何与宽度」，不是「电气意图」

KiCad 的 `exportNETCLASS()` 给一个网类只写三样：`(width …)`、`(clearance …)`、`(use_via …)`；FreeRouting 的 `RulesReader` 也只认 `WidthRule` / `ClearanceRule` / via infos。
也就是说，**这个生态里的「规则」就是宽度、间距、过孔**。

我们项目里最值钱的那一层——差分对真耦合（`route/couple.py`）、等长蛇形（`route/tune.py`）、阻抗目标（`calc/impedance`、`calc/feedline`）、爬电（`calc/iec60664`）、回流缝合（`route/return_path.py`）——在 DSN 里**没有可用的承载**。它们在我们的 `route/intent.py` 一份意图对象里，不在交换格式里。

这不是 DSN 的缺点，是它的定位：它是**给布线器吃的几何草稿**。反过来看也成立：**如果一块板的信息只需要「几何 + 宽度 + 间距 + 过孔」，那它就已经被 DSN 完整表达了**——这条可以当成我们自己的信息丢失审计（`docs/03`）的一把尺子。

顺带一条收敛证据：DSN 的 `(grid (via_grid …) (wire_grid …))` 是**两个栅格**，与 `docs/27` 里「两套栅格（走线/过孔）不能合并」是同一个结论——两边独立走到了同一处，说明那不是我们的偏好。

## 91.7 结论：不做交换，借机制

不做 DSN/SES 交换，三条理由：

1. **判据错位**。我们的价值是「精确几何 + 外部裁判（`kicad-cli DRC` / 立创）」。外部布线器给的只是一份候选，收一份候选要付一整套 IO 的保真责任（单位、Y 轴、弧、padstack 命名、归属、量子吸附），而收益只是「多一个候选」——我们自己的 `route` 已经能给候选，而且每条候选都过闸门。
2. **零依赖红线**。FreeRouting 是 Java（jar），KiCad 的布线器长在 PCB editor 里，两者都不可能作为 runtime 依赖打包；本仓库 `dependencies = []` 是硬约束。
3. **许可证**。两个参考实现都是 GPL，只能读思路不能抄实现；抄不来就不该假装接得上。

**从这条线真要拿走的是五条机制**（§91.4）。其中 A1 与 A2 跟 DSN 一点关系都没有，做它们不需要先接格式。

## 91.8 重开的门票（写给未来的自己）

- **触发条件只有一条**：用户真的要把这块板交给外部布线器（FreeRouting / 商用 Specctra / 另一套 EDA），而不是我们想「顺便支持一下」。
- **第一版范围**：`structure`(layer / boundary / rule 三条 / via) + `placement` + `library`(image / padstack / pin) + `network`(net / pins / class 三条规则) + `wiring`(wire / via / `type fix`)。弧**折线化并报弦高**；**不写** `plane`、`class_class`、`length` 类。
- **验收判据（每条都能失败）**：
  1. 一块板 导出 →（路由器原样返回）→ 导入 后，**每一段铜的坐标与宽度按量子吸附后逐位相等**；
  2. `(type fix)` 的铜往返后**逐位不变**；
  3. 一个案例带负坐标、一个案例带 4 位小数（立创口径）；
  4. **拒绝臂**：喂一份 `qarc` 的 SES，必须报错，而不是悄悄折线化。

## 91.9 一句话

Specctra 给我们的不是一条要接的格式，而是一句提醒：**「锁定的东西不回流」**和**「合并就是替换未锁定」**——这两条我们现在没有，而它们跟 DSN 一点关系都没有。

## 91.10 附：`rea` 的实际安装结果（不臆造）

按指令装了 [`morluto/rea`](https://github.com/morluto/rea)（REA = Reverse Engineer Anything，MIT，MCP server + CLI）。实测：

| 项 | 实测 |
|---|---|
| 安装方式 | `npm i -g rea-agents@6.8.0 --ignore-scripts --no-audit --no-fund`（首次不带 `--ignore-scripts` 会在 postinstall 上挂住 >7 min，未走完即终止；产物已落盘但版本停在 6.7.0） |
| 版本 | `rea --version` = **6.8.0**；命令行 **153** 个子命令，MCP **207** 个工具，prompts 6 |
| 环境 | node **v24.17.0** / npm 11.13.0 |
| `rea doctor` | `healthy: false`：**node 版本低于该版要求**（要求 `^22.23.4 \|\| ^24.22.1 \|\| >=26.12.1`，本机 24.17.0，`missing_dependency`）；`host: win32 x64` 正常 |
| 分析引擎 | **全部缺失**：Hopper（`HOPPER_LAUNCHER_PATH` 未设）、Ghidra（`GHIDRA_INSTALL_DIR` 未设）、IDA（未注册）、JADX（`REA_JADX_MCP_JAR` 未设）、Kuna（未设）。REA 自己**不下载也不安装**这些引擎 |
| 注册与 skill | 4 个客户端注册（copilot_cli / dsh / vscode / zcode）均为 `missing`，`skill:identity` 为 `missing`（需 `rea setup`，该命令会改客户端配置，未擅自执行） |

**对本次任务的意义要说清楚**：Specctra 的 DSN/SES 是**纯文本 S 表达式**，规范与两种开源实现都在手上（§91.2），不需要反汇编任何东西 ⇒ REA 在这一步**没有被用到**，也不该被当成「用过」。若以后真要做二进制取证（比如某个商业 EDA 的私有二进制格式），前置条件是：升 node 到 ≥24.22.1、装好至少一个引擎、跑 `rea setup` 做客户端注册。
