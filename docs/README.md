# 设计文档索引

这四十三篇不是教程，是一条**逐步收紧的判决链**：每一篇都在推翻或修正前一篇的某个前提，
最后收敛到「先做 Calc 层」这个可执行决定。建议按编号顺序读，跳过前面会看不懂后面的取舍。

> **完成度总账在仓库根 README 的「一页速查：做完了什么 / 还差什么」**（哪些做完、哪些结构上不做、
> 哪些现在拒但门开着、哪些做了但打折）。本索引只谈文档：每篇推翻了前一篇的哪个前提。

| # | 文档 | 它回答的问题 | 它推翻了什么 |
|---|---|---|---|
| 00 | [开源 MCP 调研与架构建议](00-开源MCP调研与架构建议.md) | 现有哪些开源实现？两个 EDA 的可编程面差在哪？ | —— |
| 01 | [AI 原生的 PCB 表示](01-AI原生的PCB表示.md) | 为什么把图元 API 交给模型必然失败？ | 「tool 越多越强」（247 / 560+ tool 的路线） |
| 02 | [占用可通行表示与经验累积](02-占用可通行表示与经验累积.md) | AI 怎么知道「哪被占了、哪走不过」？经验怎么不重复犯？ | 「给个密度百分比就够了」 |
| 03 | [信息丢失审计](03-信息丢失审计.md) | 这套表示还会丢什么？丢到什么程度算事故？ | 「摘要能保住信息」 |
| 04 | [布局表示与度量层](04-布局表示与度量层.md) | 布局本身就是设计，怎么变成可判的量？ | 「布局好坏不可机判」 |
| 05 | [无损转化架构总纲](05-无损转化架构总纲.md) | 「无损」到底怎么定义、怎么强制？ | 01~04 的隐含前提：**把 PCB 压缩成一份好读的摘要** |
| 06 | [存储形式与现有 MCP 的信息传递](06-存储形式与现有MCP的信息传递.md) | 文件里到底存了什么？现有 MCP 实际喂给模型什么？ | 「文件里没有所以拿不到」——不是没存，是没人算 |
| 07 | [自研定位与风险分期](07-自研定位与风险分期.md) | 我们做什么、按什么次序做、最可能死在哪？ | 「这是转换工具」（改判为**推导**工具，5%/95%） |
| 08 | [PCB 设计流程与工具映射](08-PCB设计流程与工具映射.md) | 真实设计流程六步，每步需要哪些计算与约束？ | 「先接 KiCad 再说」（双栈必须同时在场） |
| 09 | [布局方法论补强与标准冲突](09-布局方法论补强与标准冲突.md) | 经典方法论之间互相矛盾时听谁的？ | 「冲突可以推理裁决」（改为**登记 + 声明**） |
| 10 | [桌面控制与双栈读路径](10-桌面控制与双栈读路径.md) | EDA 开没开、工程开没开怎么变成可查事实？两份格式凭什么喂同一个度量层？ | 「启动了进程就是打开了工程」；「两套写法可以并存」 |
| 11 | [保真层：转换还是推导](11-保真层-转换还是推导.md) | 中间层凭什么说"等于那张板"而不是"推导出那张板"？怎么证明？ | 「解析 5%、推事实 95%」——**推导即猜**；「合成样本通过 = 解析器正确」 |
| 12 | [写路径：从"提案"到"落盘"](12-写路径-提案到落盘.md) | 改那块板凭什么可信？两种格式的静默失败长什么样？ | 「调用一个函数、得到一个改好的文件」；「合成器写对了就行」 |
| 13 | [立创实测：真实存储与记录格式](13-立创实测-真实存储与记录格式.md) | `.epro` 里到底有什么、单位是什么、一次保存改动了谁？哪些结论是被真板子推翻的？ | 「保存会新增成员并改写 project.json」「标签写了 mm 就按 mm 存」「焊盘网络行只有一个 id 字段」——全部改成 194 个真实归档上的实测 |
| 14 | [原理图写路径：裁判换成网表与 ERC](14-原理图写路径.md) | 板能写了，原理图凭什么能写？为什么 DRC 那一套在这里一言不发？ | 「板路径的闸门照搬」——DRC 判铜，原理图的产品是连通性，裁判只能是网表 + ERC；另加「坐标取反只是镜像，KiCad 不会报错」 |
| 15 | [对账：推导对着厂商判决](15-对账-推导对着厂商判决.md) | 我们算的间距凭什么可信？哪些是比不了的？ | 「推导标了 authoritative:false 就算尽责」——要拿厂商 DRC 在同一文件上双向数 recall 与 extra |
| 16 | [天线与馈线](16-天线与馈线.md) | 给定频率与尺寸，第一个能打样的几何是什么？哪几个数不能信？ | 「给个闭式公式就能设计天线」——**没有全波就没有方向图**；长度必须给区间；每个匹配解必须回代 |
| 17 | [仿真层](17-仿真层.md) | 给定网表/铜箔/功耗，哪些数能给、哪些必须拒绝？ | 「有公式就等于能仿真」——**没有全波、没有电热耦合、没有辐射**；SPICE 只做子集且逐条点名拒绝 |
| 18 | [DFM：工艺能力校验](18-DFM-工艺能力校验.md) | 工艺能力从哪来？文档里没写的规则怎么办？ | 「服务器内置一份板厂表」——**没有表，只有规则目录**；错键 / 无出处整份拒收；量不到就给下界 |
| 19 | [机械振动：薄板、单模态、线性](19-机械振动.md) | 它多快开始振？振多狠？多久会裂？掉一下会怎样？ | 薄板 Ritz 求解（不用模态表）→ Miles 随机响应 → Steinberg 三带疲劳 → Newmark 冲击 → 三个应力极值分层报（von Mises 在角、主应力在跨中）；不算装配柔度与焊点蠕变 |
| 20 | [平面谐振与电热耦合](20-平面谐振与电热耦合.md) | 地平面什么时候不再是地？缝合孔要多密？电流把铜烤热之后还稳吗？ | 平行板腔体模 + 过孔间距/λ_g（**判据是调用方的**）；电热定点迭代给 A/(1−G) 与**精确失控判据** G≥1，板级再放进 2-D 层压板温度场（SOR，失控在求解前判）；PDN 只给每个腔体模的峰值 \|Z\|，**不给曲线**（模态和不收敛），不算 3-D、不算交流损耗 |
| 21 | [SPICE：内置子集 + 可选委派真引擎](21-SPICE引擎委派.md) | 为什么不直接内置一个完整 SPICE？装了就一定用得上吗？ | 不 vendor 二进制、不加 runtime 依赖：内置子集保证有答案，装了 ngspice 就委派；`engine="ngspice"` 找不到**报错不回退**；`cross_check` 比两引擎的节点电压；**本机无二进制 ⇒ 外部路径 `verified_against_engine:false`**；`0.0.37` 起又加了**随包**一档（见 `43`）——`dependencies=[]` 仍成立，随包只服务 `engine="ngspice"` |
| 22 | [微带与腔体滤波器](22-微带与腔体滤波器.md) | ADS 里那几个合成按钮，不给 EM 结果能给什么？ | 「合成 = 仿真」——只给原型 g 值 / 集总带通 / 耦合线 Newton 反解 / 耦合谐振器 / 矩形腔 TE101 的 Qc 双推导；无交叉耦合、无色散、无 iris 几何、无版图；集总带通**有**有限 Q、一阶寄生与蒙特卡洛良率（但只有 uniform 独立分布 = 最坏情况），分布式滤波器仍无实测 Q |
| 23 | [两张读不到原文的表](23-两张读不到原文的表.md) | 标准是付费的、公开转述互相矛盾，凭什么敢把数填进去？ | 「读原文抄表」——IPC-2152 抄 KiCad 的**闭式拟合**并用锚点+物理探针三线校验；IEC 60664-1 **编译原实现、探针反演阶梯、20 万点对拍**；并记下三条只有行为对拍才发现的错（尤其**断点必须每次重新恢复，不能继承上一版 JSON**）。其余安规族仍 fail-closed |
| 24 | [立创进程内桥](24-立创进程内桥.md) | 怎么才能看见用户正开着的那张图？应用自己的命令空间长什么样，我们凭什么敢说"读过它"？ | 「要驱动立创就得写扩展或用 websocket 网关」——其实是它自己开的命名管道 `\\.\pipe\JLCEDAPro<8 hex>` 可枚举 + ping 确认；并推翻前两轮的「它起不来」（差别在父进程有没有控制台）。写路径**一次都没做过**，逐条列在 §24.9 |
| 25 | [技能并入：八个技能里哪几条进了工具](25-技能并入清单.md) | 这八个技能里什么能变成工具、什么必须拒？一个技能的说法与它自己的公式不自洽时听谁？ | 「技能里的清单可以直接搬」——**清单是可搬的，建议不是**：规则进目录并标出处与证据等级，量不到一律 undetermined；技能 §3.5 那张 λ/20 表与 λ/20 公式差一倍，表被丢弃；两个付费 `gmai-*` 技能的内容串域（固件安全样板），整份拒收 |
| 26 | [分步可见与铺铜闭环](26-分步可见与铺铜闭环.md) | 过程为什么必须看得见、打得断？器件从哪来？铺铜什么时候才算真的铜？ | 「apply 必须原子」（原子是给脚本的，不是给协作者的）；「有 violation 就挡」（真板 69 条全是 warning）；「栅格能回答 pad 出不出得去」（0.5 mm 间距下必假警）；「写了 `(general (thickness 1.6))` 就是 1.6 mm」（KiCad 只累加 stackup，实造 1.18）；「`add_zone` 之后那块铜就算有了」（没有 filled_polygon 就只是轮廓） |
| 27 | [自动布线器](27-自动布线器.md) | 搜出来的线凭什么算数？50 网板上 23 个失败是几何还是争用？自己的闸门绿了算不算绿？ | 「栅格说能走就能走」——**搜索只是提议，精确多边形几何才是判决**；两套栅格（走线/过孔）不能合并；「预算耗尽」不等于「没有路」；「缺规则就不查」也不行——板边距留空等于往板边写非法铜；并抓出五个**能在 `status: ok` 下给出错答案**的真缺陷：越界下标在 Python 里回卷、keepout 只封了边框一条带、keepout 根本不进精确判定、焊盘逃逸那段铜从来没画过、板边距只管过孔不管走线（后者由 `kicad-cli DRC` 报 6 条 error 才现形） |
| 28 | [界面可靠性：焊点热疲劳与跌落应变](28-焊点疲劳与跌落.md) | 温循多少轮回裂？掉一下板面应变多少？ | 「许用值可以由服务器给」——**不行**：ε_f 是拟合、CTE 是典型值、临界应变只能是调用方的，不给就只回来应变 + `judged:false`；「热疲劳和振动疲劳是同一件事」——不是，Steinberg 判相对位移、这里判剪切应变与板面应变；「Engelmaier 的 c 越接近 0 越安全」——写反了，更热/驻留更久都让 c 更负、Nf 更小；「跌落可以直接调 shock_sdof」——单模态链要三段各自复核，且位移放大率可以小于 1 |
| 29 | [共面波导：另一类截面](29-共面波导场解.md) | 共面线（cpw/gcpw）的阻抗能不能也解出来、凭什么信它、共面损耗那句「不计算」怎么消失的？ | 「共面线塞进已有的边耦合截面就行」——不行，回流在同一层、槽口是奇异点、衬底有限厚且有背面两种；「半域对称可以省一半节点」——它把 2.5% 的静默偏差和 2 倍因子一起带进来，而且场解与闭式的对账**不该共享对称假设**；「零厚度也能做增量电感校验」——零厚度 `∫g²ds` 对数发散、没有收敛值，这条检查在这里不成立，换成两条精确（尺度不变、两翼逐位相等）+ 一条机制（零厚度漂移 > 有限厚度）；「共面损耗拒绝就完了」——现在接上，但只给零厚度值 + 明确方向（真实铜箔更高约 10%，且那 10% 自己也没收敛） |
| 30 | [共面差分对：没有闭式可比时判据换成什么](30-共面差分对.md) | 边耦合共面线（差分 CPW/GCPW）的阻抗和差分损耗能不能解出来、在没有闭式可对账的情况下凭什么信它？ | 「没有闭式就没法判」——换成三类不依赖外部数值的判据：叠加恒等式（三驱动，残差 1.9e-8）、精确不变量（奇模电荷平衡 −1.000000000、左右份额逐位相等）、极限（间距→0 时共模落到已对过账的 2w 单线 −0.87%）；「两个模式＝两个对称面」——不，是两个驱动，墙必须从解里读出来，否则检查与被检共享假设；「间距→0 两个极限同向」——反向，`Z_odd` 对数发散到 0 才是物理；「恒等式能兜住所有 bug」——它对浮动条是盲的（残差 1.47e-10），所以两个负控制分别管两种 bug |
| 31 | [共面有限宽地：一条边在哪，值就属于哪一种](31-共面有限宽地.md) | 共面的地不是无限大的，把宽度当参数以后阻抗怎么变、该不该默认开？ | 「无限地就是唯一答案」——真实版图的地常常只剩几个 gap 宽，而保角映射的地是无限宽，所以有限宽的地下它不能当误差棒，`VALIDATION` 里单独立一项；「窄地影响差模」——不，`ground_mm = g/2` 只改 `z_odd` 1.1% 却改 `z_even` 21.9%，是共模问题（`z_common` 73.1→89.1 Ω）；「多几个节点而已」——地外沿是表面电荷的奇异边，单线节点 +16% 但 SOR 迭代 +490%（564→3334），墙钟 1.6 s→12.5 s；「默认关就等于接上了」——本轮真断过：schema 层把参数丢掉时两种状态输出逐位相同、且不报错，所以负控制 `_WingIgnored` 专门管这一种 |
| 32 | [差分对进馈线：接一条通路，钱花在网格上](32-差分对进馈线.md) | 差分对凭什么叫 `feedline_impedance` 的 kind、反解为什么从毫秒变成分钟、损耗的分母到底是谁？ | 「新工具更干净」——不，新工具要把 `lambda_g`、损耗链、反解各写一遍，而接成 kind 以后 `feedline_width` 的答案能被前向调用**逐位复核**；「便宜网格定宽度再复算一遍就够了」——那是把误差揭发出来而不是消掉，实测读完 100.05 Ω 用默认网格复算成 101.13 Ω，差 1.1%；「容差能随便收紧」——牛顿步落在 +0.063 Ω（0.06%），而解算器自身离散度 0.1~0.5%，追到 0.0009 Ω 要多花一次 30 s 求解；「参数收下就等于接上了」——所以负控制 `_SpacingDropped` 专门管这一种 |
| 33 | [subckt 与层次展开：内置引擎自己拍平](33-subckt与层次展开.md) | 子电路凭什么归内置管、拍平做对了没有外部引擎怎么证明？ | 「层次是一条新求解路径」——不，拍平成普通 deck，方程层一行不改，子电路因此只是语法能力；「节点名随便起」——照抄 ngspice 的 `x1.m` 小写，否则 `cross_check` 比到 0 个节点会被读成「一致」；「表达式语言尽力支持一下」——只做 `+ - * / **` 的 ast 白名单，函数与节点引用按名字拒绝；「两引擎不一致就是拍平错了」——浮空岛的 `gmin` 分歧在手写平铺 deck 上逐位相同（2.501057919），是既有行为 |
| 34 | [参数与作用域：一个数从 .param 走到元件](34-参数与作用域.md) | 参数凭什么归内置管、作用域链怎么排、为什么宁可报错也不猜？ | 「参数是子电路的私产」——不，它是整份 deck 的公共语言，顶层没有来源就等于砍掉子电路的一半；「1k*2 就是 2 kΩ」——旧版 `_value` 静默读成 1000 且看不出来，本版拒绝并写出正确写法；「先写的先算」——前向引用合法、循环必须报错，两者句法相同只能靠求解轮次区分；「体内 .param 可以被实例覆盖」——不，那会让派生量的语义取决于调用者 |
| 35 | [行为源 B：一个表达式从卡面走到雅可比](35-B源与表达式.md) | B 源凭什么归内置管、表达式在哪一层变成树、为什么 `? :` 与 `if()` 要分开判？ | 「把右侧字符串交给 `eval` 就好」——不行：语法错要带行号在解析期报，节点名必须按源码文本取（`V(1)`/`V(n-1)`/`V(in)` 在 Python 里分别是数字、减法、关键字），参数在构建期折成数字，AC 按偏置点线性化；`? :` 展开必须排在 `if(` 改名之后（否则 `if … else` 被改成 `_if_`）；顺带抓出全局残差尺度让 1 V 误差被当收敛的真缺陷 |
| 36 | [无损传输线 T：一条延迟从卡面走到三个分析](36-无损传输线.md) | 传输线凭什么归内置管、三个分析各用哪一套式子、为什么半波点宁可拒绝也不给数？ | 「两条线各一个支路未知量」——不行：`T1 2 0 3 0` 两端口共地时第二行退化成 `0 = 0`，矩阵奇异，改成一个未知量 + 一行 `v1-v2-v3+v4=0`；「缺 TD 就当 0」——ngspice 正是这么静默默认的（线变导线），所以缺 TD 与 `TD=0` 都拒绝；「延迟比步长短就钳到一步」——实测波形整体晚约 2 步、与告警文案不符，改判 `ARG_RANGE`；「半波点返回矩阵算出来的大数」——理想线在那里导纳无穷，返回巨大数正是本模块要消灭的假答案，改判 `SINGULAR` 并指向 `O` 卡；顺带抓出 `PULSE` 宽度缺省写 0 的真缺陷（SPICE 的缺省是 `tstop`） |
| 07 §7 | 板框 / 开槽 / 机械层 | 待议，6 条议题 | —— |
| 37 | [有损线 O 与分布 RC 线 U：把一条线换成一串 R/L/C](37-有损线与URC.md) | 有损线凭什么归内置管、段数为什么只许写在 `.model` 里、在没有引擎可对拍的地方凭什么信它？ | 「O/U 是分布线」——不是：解析期展开成 `lump` 段普通 R/L/C，段数与是否默认都随答案上报，DC 是模型的精确串联电阻、AC/瞬态随段数一阶收敛；「`lump=` 写在卡上也行」——ngspice 把它当 FATAL，而写在 `.model` 只是 warning，同一份 deck 要能两边跑就只能进 `.model`；「两个返回节点也能算」——梯形把返回当理想单节点，`n3 != n4` 拒绝而不是静默把串联电阻减半；「没有引擎对拍就等于没验过」——U 用 `Z0c·coth(γL)` 解析解做参照（ngspice 的 URC 数值不直观，与 `F` 卡同类判断）；「`G>0` 也能对拍」——ngspice-47 自己拒绝非零 G；顺手抓出 `.ac` 里没有 `AC` 关键字的源被当成 1 V 激励的真缺陷（旧读数满幅、ngspice 全是 0） |
| 38 | [噪声分析 `.noise`：一次伴随求解，把「哪来的噪声」变成可对账的数](38-噪声分析.md) | 噪声凭什么归内置管、为什么是伴随法而不是「每个源解一次」、带内 rms 的积分规则凭什么跟着网格走？ | 「把各元件的噪声密度加起来」——不行：同一个 `4kT/R` 在短路旁边贡献 0、开路旁边贡献满，每个源都必须过传递；「一种积分规则够用」——`dec 10` 对闭式差 −0.0009 %，`lin 1000` 差 +47 %，规则只能由网格决定；「ngspice 能跑就一样」——它要求输入源带 `AC` 说明否则整份 deck 中止，而且它的 `inoise_total` 与逐点折算不是同一个量，交叉校验只比谱；「MOS 的 `W/L` 早就支持了」——没有，一直被静默忽略（器件弱 10~100 倍，噪声的 `gm` 直接继承这个错），本轮修掉并逐位对上前先由 `.op` 数值微分求出 `gm` |
| 39 | [多页（层级）原理图：问哪一棵树、页号归谁、pin 谁说了算](39-多页工程.md) | 跨文件以后闸门还对不对、页号是工程全局还是本文件、引擎被问的是哪棵树、sheet pin 算不算接通、建新页为什么不塞进 plan？ | 「单文件闸门对层级也一样」——不，`walk` 把入口文件的 `text` 当成根，问的是**另一棵树**（proposed 4/4、磁盘 4/5）；「页号是本文件最大 +1」——是**工程全局**，`vme-wren` demo 73 页 / 17 个 sheet 节点、页码 1..73 不重复；「一个 sheet 节点写一条 `(path … (page …))` 就够」——同一节点被实例化 N 次就有 N 条，只写一条会让每个实例同号；「`set_sheet_field` 想改啥都行」——只许 `Sheetname` / `Sheetfile`，`Sheetfile` 还只收裸文件名；「pin 写了就等于接通」——子图没有对应 `hierarchical_label` 时 ERC 立刻多出 `hier_label_mismatch` + `pin_not_connected`，拦截来自 label 本身；「建新页顺手塞进 plan」——多文件原子写没有诚实答案，拆成 `schematic_new_file` 两步 |
| 40 | [受控源 F/H 与互感 K：第三个 token 不是节点](40-受控源与互感.md) | 为什么 `F`/`H` 的第三格是元件名而不是节点对、为什么 `H` 要自己一行而 `F` 不用、`K` 为什么不是器件、瞬态差 1.22% 该归给谁？ | 「按 `E`/`G` 的位置读 `F`/`H`」——控制项会取两个不存在的节点之间的 0 V，`F1 2 0 V1 2` 变成 0 A 电流源而 deck 依旧 `ok`；「`F` 也该分配一行」——它 stamp 在**已有**支路列上，未知量必须不动（`F` 3 vs `H` 4）；「`K` 是器件」——没有端子、不占行、DC 上什么也不许改（0.5 V 工作点逐位相同）；「k 的绝对值超过 1 钳到 1 就行」——那是把不可能的输入变成正常答案，拒绝并在消息里给范围；「子电路里的名字全局解析」——顶层同名的诱饵 `L1` 会让 `K` 静默耦合错对象（比例从 2.000 变 0.632）；「瞬态差 1.22% 是互感写错」——是后向欧拉 vs 梯形，同一份 deck 不带 `K` 也差同量级，交叉项另由 AC 逐位 0.0 与结构判据（等绕组 `v2-v1=0`、开路次级比例 2.0000000）钉住 |
| 41 | [开关 S/W：两个电导，一个状态，三条只能量出来的阈值](41-开关.md) | 开关凭什么不占未知量、状态为什么不能塞进牛顿迭代、滞回宽度/边界/DC 口径各是什么？ | 「滞回是 `VT±VH/2`」——是**整宽** `VT±VH`：斜坡到 1.2 V 时 `VH=0.5`（门槛 1.5）仍然不开；「`ON` 就等于锁在 on」——只是**起始状态**（`ON` + 0 V 控制仍收敛到 open，带内才由种子决定）；「恰好 VT 判开」——引擎在 `VH=0` 时用 `>=`，1.000000 V 就**闭合**（约定只能由对账定，不能由读者定）；「滞回是瞬态的事」——DC 也走同一条带，工作点里控制 1.0 V 的开关仍是 open；「开关是器件、要给一行」——它不占未知量，只是两个电导换一个；「瞬态边沿差 18.9% 是判据写错」——固定步长 vs 自适应，20 µs→2 µs 后 2.74%，非边沿 0.299%→0.182%；「双稳总能收敛到一边」——两个状态都不自洽就是 `NOT_CONVERGED` + bistable hint |
| 42 | [JFET `J`：平方律里没有那个 1/2](42-JFET.md) | 为什么 `J` 不是 MOS 的复制粘贴、三端子与 `AREA` 的语义是什么、栅结不建模凭什么还敢给答案？ | 「JFET 也是 `Id=(BETA/2)(Vgs-Vto)^2`」——没有 `1/2`：`BETA=1m`、`Vov=1` 读 `1.000 mA`，MOS 才是 `0.500 mA`（测试写成反向断言 `assertNotAlmostEqual(0.5e-3)`）；「膝点要平滑」——两式在 `Vds=Vov` 精确相接，`Vds=0.999` 读 `0.999999 mA`（linear）、`1.001` 读 `1.000 mA`（saturation）；「卡上的 `AREA=` 与模型的相乘」——**覆盖**：model 2 配卡 3 读 3× 不是 6×；「缺省值无所谓」——空 `NJF()` 用 `VTO=-2/BETA=1e-4`，一条平移一倍的曲线看着完全合理，所以逐项进 `assumptions.defaulted_model_params`；「不建模栅结等于假设它不存在」——`gate_junction_forward_v` 超 `0.5 V` 时加 `outside_model` 进 `warnings`（方向实测：NJF `max(vg-vd, vg-vs)`、PJF 镜像）；「`W=`/`L=` 该支持」——ngspice 自己在 `J` 卡上拒绝，本子集只是把限制说清，`AREA` 是唯一几何旋钮；「拒绝 `RD`/`RS`/`B` 太保守」——实测 `RD=100` 移 14%、`RS=100` 移 16%、`B=2` 换 1.5×，都是**跑得动但方程变了」 |
| 43 | [随包 SPICE 内核：不重写引擎，把最小内核装进包里](43-随包SPICE内核.md) | 为什么不再自己实现 `A`、随包的那一份到底要哪些文件、`engine="auto"` 为什么不看它、混搭许可怎么和 MIT 共存？ | 「`A` 在本机 ngspice 上跑不了」——**错的**：模型名写成 `amp`（应为 `gain`）+ 手工树缺 `spinit`，同一份二进制补齐后 `gain(gain=2)` 读 2.000 V；「`spinit` 按 cwd 找」——按**可执行文件所在目录**找；「`lib/ngspice/*.cm` 是装饰」——把 `analog.cm` 写成垃圾文本立刻 `MIF-ERROR`；「设 `SPICE_LIB_DIR` 总没坏处」——它会**顶掉** `spinit` 的查找基准，对有 `spinit` 的树设它等于把引擎弄坏；「ngspice 是 GPL」——`COPYING` 默认是 Modified BSD，GPLv2+ 只管 `table.cm`，共存靠独立进程；「仓库里多 14 MB 会改 `auto` 的答案」——`auto` 走 `allow_bundled=False`，行为零变化 |

## 三条贯穿全部文档的结论

1. **PCB 文件里只有图元；AI 需要的是事实；中间那层「推导」几乎所有人都没做。**
   而源头不缺数据 —— 立创 `.epcb` 的规则 / keepout flags / 槽孔 / 热焊盘辐条都在，KiCad 的叠层 εr 也在。
2. **「AI 的错误仍能让 DRC 全绿」的信息，是最危险的信息。**
   模拟布局、共质心、Kelvin 连接、参考平面连续性都属于这一类 —— 它们的错法在 DRC 里看不见。
3. **猜一个安全间距，比不给答案更糟。**
   所以载流要标准名、安规要数据表、拿不到就 fail-closed 并说明缺什么。

## 与代码的对应

`docs/` 只定方向与判据，量化依据全部落在 `src/pcb_agent_mcp/calc/` 里，每个结果必带
`model` / `basis` / `precision` / `assumptions`，文档里的每条公式都能在代码里找到同名实现。

- `00`~`09` → `calc/` 与整体定位。
- `10` → `desktop.py`（探测/启动/关闭/开工程）、`adapters/`（双栈解析）、`oracle.py`（板级度量）。
- `11` → `fidelity/`（保真树 / 视图 / kicad-cli 裁判）：`sexpr.py`、`kicad_doc.py`、
  `epro_doc.py`、`views.py`、`engine.py`，以及 `oracle.py` 里每条派生答案的 `authoritative: false` 标注。
- `12` → `edit/`（写入：ops / build / checks / plan / fsutil / epro_edit）与 6 个 `board_*` 写工具：
  propose（只算不写）→ apply（checkpoint + 原子写 + 五道闸门 + 回滚），以及 §12 里那三条
  kicad-cli 实测的 DRC 事实。
- `13` → `fidelity/lceda.py`（记录流 / 单位 / 逐字节往返）、`fidelity/eproj_doc.py`（SQLite 只读）、
  `edit/epro_edit.py`（六个 op + 六道闸门 + 行级 diff），以及 `adapters/epro.py` 里每条 `authoritative: false` 的成因。
- `14` → `fidelity/kicad_sch.py`（整棵 `(kicad_sch)` 树）、`edit/sch_ops.py`（22 个 op）、
  `edit/sch_plan.py`（propose / apply / netlist_diff / erc_diff）、`engine.sch_erc` 与
  `engine.sch_netlist`，以及 5 个 `schematic_*` 工具。
- `15` → `fidelity/crosscheck.py`（`board_cross_check`：与厂商 DRC 双向对账、
  `not_measured` 三条、zone / 差分对 / 局部 clearance 三处口径），以及
  `tests/test_kicad_pad_placement.py`（拿 `pcbnew` 当裁判的焊盘几何对账）。
- `16` → `calc/feedline.py` / `calc/antenna.py` / `calc/matching.py` / `calc/link.py`
  与 `oracle.antenna_keepout`，以及 21 个天线类工具与 93 个测试；边界是**不含全波求解器**，
  方向图 / 外壳失谐 / 互耦 / SAR 一律写进 `not_computed`。
- `17` → `calc/spice.py`（DC / 瞬态 / AC 子集，MNA + 牛顿 + pnjlim + gmin 步进）、`calc/power.py`
  （铜阻 / 过孔 / 电感 / 电阻网络 / IR drop / PDN 三态）、`calc/thermal.py`（热阻串 / 过孔阵列 /
  2-D 板 / 瞬态 / 散热片 / 对流）与 `oracle.board_ir_drop`，以及 14 个仿真类工具与 81 个测试；
  边界是**只做子集**：未实现的 SPICE 卡、自热、平面谐振、全波、电热耦合全部逐条点名拒绝。
- `18` → `calc/dfm.py`（11 条规则目录 + 能力文档体检 + 五态判定）、`oracle.dfm_check` 与
  `_dfm_facts`（11 个事实，每条带 `where`），以及 2 个 DFM 工具与 28 个测试；
  边界是**没有内置板厂表**：8 类量不出来的项目写进 `not_checked_by_design`，
  有界搜索窗外的情况只给 `lower_bound`，不冒充最小值。
- `19` → `calc/vibration.py`（Rayleigh-Ritz 薄板模态 / Miles 随机响应 / Steinberg 疲劳 / Newmark 冲击 / 传递率 / 板级应力三极值）与 `oracle.board_vibration`（板框 bbox + stackup 厚度 + 元件当探针点），以及 6 个振动类工具与 73 个测试（含 `tests/test_vibration_stress.py` 的 21 例）；边界是**薄板、单模态、线性**：不算剪切变形、元件附加质量、装配柔度、焊点蠕变、随机+冲击叠加、跌落、非线性接触。应力**不是解出来的场**，而是由同一组 Ritz 曲率算出的三个极值（板角 von Mises / 跨中主应力 / 板边剪应力），张量分量只在 `sigma_max` 点报。
- `21` → `calc/spice.py` 的外部引擎段（`find_ngspice` / `_deck_for_ngspice` / `_run_ngspice` / `_parse_raw` / `_cross_check`，以及 `simulate(..., engine=…, cross_check=…)`），`tests/test_spice_engine.py`（37 例）+ `tests/_fake_ngspice.py`（假引擎）。外部路径已对着**真 ngspice-47**（MSYS2 Windows 构建）跑过并逐节点对账（分压 5e-9 V、RC 阶跃与 RLC AC 逐位 0.0），再用**第二个构建 ngspice-41**（conda-forge）跑同一批 deck 做**跨版本对拍**（`.op`/`.ac` 逐位 0.0，`.tran` 最多差 1.24e-4 V，两构建步长控制不同）；`verified_against_engine` 按**引擎版本**门控，只有 `VERIFIED_ENGINES`（`ngspice-47`、`ngspice-41`）里的版本为 true，别的二进制一律 false（门控本身由假引擎自称版本号来测，不需要真引擎）；默认引擎是 `builtin`（同一网表在两台机器不能给不同答案）。
- `20` → `calc/cavity.py`（平行板腔体模 + 过孔围栏比例）、`calc/electrothermal.py`（R(T) 定点迭代 + 精确失控判据 + 板级 2-D 电热场 `board_electrothermal`）、`calc/power.py` 的 `pdn_plane_modes`/`pdn_impedance(plane=…)`，以及 6 个工具与 89 个测试（`test_vibration` 之外：`test_cavity_electrothermal` 31、`test_electrothermal_plate` 31、`test_pdn_plane` 27）；边界是**没有边界失谐修正**，2-D 场只沿调用方给的层压板 k 扩散（不含真实铜面/平面导热），3-D、热过孔、组件热容都缺；平面模只给峰值不给曲线。
- `22` → `calc/filters.py`（原型 g 值 / 集总带通的两路 S21 对账 / 耦合微带 Newton 反解 / 耦合谐振器带通 / 矩形腔 TE101 的 Qc 双推导）+ `calc/filtermatrix.py`（耦合矩阵当**真网络**：理想导纳逆变器的精确 Π 等效 + 节点法解 S，三阶 trisection 的零点由 `Ω_z=m12²/m13` 余子式给出、有限 Qu 的插损对 Matthaei 对账），以及 7 个 `filter_*` 工具与 101 个测试（`test_filters` 31 + `test_coupling_matrix` 39 + 其余 31）。边界是**合成而不是仿真**：给了几何，不给 EM 结果——交叉耦合能给**电学上的**带外零点与抑制量，但物理实现（折叠版图、iris、EM 验证）不做，也无色散、无版图；耦合矩阵的 trisection 是**微扰**不是 Cameron 全合成（`ripple_actual_db` 是「你得到的」）；集总带通**有**有限 Q（每段一只电阻 + 功率审计）、一阶寄生与蒙特卡洛良率（uniform 独立 = 最坏情况），分布式滤波器仍无实测 Q、无良率。
- `24` → `lceda_live.py`（命管道枚举 + ping 确认 / 一行一 JSON 帧 / 9 个 op / `invoke` 在编辑器 render 进程里跑 async 函数体）与 6 个 `lceda_pro_*` 工具、26 个测试（24 个假 transport 跑失败路径 + 2 个真机用例）。边界是**只证了读**：ping / open（849 ms）/ session.list / session.close / doc.api（378 类型、PCB_Document 21 方法）/ invoke（两块板名 + uuid、抛错回 EXECUTION_ERROR）都在真机上跑过，任何写、`open` 是否碰工程字节、`destroy` 是否丢未保存改动都未验证。
- `25` → `calc/magnetics.py` / `calc/stackup.py` / `calc/chain.py` / `calc/rules.py` 与 8 个新工具（`magnetics_inductor` / `stackup_material` / `stackup_design` / `rf_chain` / `match_bandwidth` / `design_rules` / `layout_review` / `schematic_review`）+ `matching.bandwidth`，36 条规则（22 布局 + 14 原理图）。边界写在每条规则上：事实缺失即 `undetermined`（空事实集直接 `ARG_MISSING`），电流限值必须显式给标准（IPC-2152 有表了：Brooks & Adam 对 Table 3-1 的闭式拟合，系数抄自 KiCad 10.0，被拟合丢掉的参数仍然没有），IEC 60664-1 隔离间距有表了（从 KiCad 实现反推、188k 点对账），其它标准族仍然 fail-closed；电感饱和没有器件就是 undetermined；磁芯损耗 / DCR / DCM 分别是独立工具（假设互不相容，不做成同一个开关）。
- `26` → `edit/plan.py` 的分步写（`steps_apply` / `step_events` / `step_control`）、`edit/drc_policy.py`、`parts.py`（离线 `lceda-std.elib` + 在线检索）、`fidelity/fill.py`（`pcbnew ZONE_FILLER` 委派 + 节点移植）、`oracle.routability` 的几何逃逸判据、叠层总厚的 `thickness_agrees` 闸门，以及 `board_progress` / `board_zones` / `board_fill_zones` / `part_*` 共 11 个新工具；边界是**立创 in-app 分步写真机未验证**（本机非交互会话起不了 `lceda-pro.exe`）、在线器件**只有元数据没有几何**、routability **不是布线器**。
- `27` → `route/rules.py`（三级来源 + 最严取胜 + `assumed` 公示）、`route/grid.py`（双栅格 / 精确 `seg_blockers`·`via_blockers` / keepout 环 / 事务化 `_Emitter` / A* 内联）、`route/router.py`（MST 降序、second sweep、按位置挑牺牲者的 rip-up、wish 驱动的拥塞协商、`_pour_report`、`incremental`）、`route/tune.py`（等长 45° 蛇形 + 宿主排序 + 振幅阶梯）、`route/couple.py`（差分对真耦合）、`route/return_path.py`（换层地缝合）、`route/intent.py`（一份意图对象三个消费者）、`route/topology.py`（mst / iterated 1-Steiner on Hanan grid）与 `board_route` / `board_intent` / `board_blockers` / `board_fanout` / `board_run_ledger` 工具，82 条测试在 `tests/test_route.py`（+ `test_blockers.py` 23 / `test_fanout.py` 22 / `test_ledger.py` 17）；爬电（含 IEC 60664-1 查表）、差分对间距与真耦合、等长、回流、阻抗目标、拥塞协商自 `0.0.16` 起都进了搜索，`0.0.17` 又把铺铜重填（`refill_pours`，委派 `ZONE_FILLER` + 字节保真移植，默认关）与多端网拓扑接上，边界是**拥塞协商默认关闭（一块板 +3 网 / +61% 时间）、拓扑默认 mst 且不是精确 RSMT、重填后的 DRC 差是版式代际差而不是几何差**，`routed` 不等于 DRC 干净（但 keepout 与板边距已进精确判定，端到端 apply 后 `kicad-cli DRC` 新增阻断项 = 0，见 §27.9.7）。
- `29` → `calc/field2d.py` 的 `_Coplanar` / `coplanar_solve_pair` / `coplanar_shape_factor` /
  `coplanar_field`、`calc/feedline.py` 的共面损耗分支（`coplanar_shape_factor_safe`）、
  1 个新工具（`coplanar_field`）与 15 个测试（`tests/test_coplanar.py`）；
  边界是**零厚度共形映射的对照**：场解在七个几何上一律读低 1.19%~3.39%，而铜厚只划网格不建模
  （实测把损耗抬高约 10%，且那个数本身不收敛），零厚度 `∫g²ds` 的对数发散以 `mesh_drift_pct`
- `30` → `calc/field2d.py` 的 `_CoplanarPair` / `coplanar_pair_solve` / `coplanar_pair_mode_check` / `coplanar_pair_shape_factor` / `coplanar_pair_field`、1 个新工具（`coplanar_pair_field`，第 151 个）与 15 个测试（`tests/test_coplanar_pair.py`）；
- `31` → `calc/field2d.py` 的 `_Coplanar` / `_CoplanarPair` 的 `ground_mm`（强制锚点、盒尺寸放大、地分类外沿）与九个函数的透传、`VALIDATION["coplanar_finite_ground"]`、两个工具的 `ground_mm` / `ground_width_mm` / `ground_note` 与 8 个测试（`tests/test_coplanar_ground.py`）；
  边界是**没有闭式可对账**：给出的不是「准确度 ±x%」而是三条自洽/不变量/极限的判据 + 两个故意写错的负控制，`Z_common` 在 `s=w/6` 上对 2w 单线 −0.87%，`Z_odd` 按物理发散到 0，大间距**不**收敛到单线（每条条只有单侧地）；差分导体损耗是零厚度下界 + 网格漂移 8.79% 的不确定度；有限宽地没有接进 `feedline_impedance`，接法见 `32` 的边界。
- `32` → `calc/feedline.py` 的 `DIFF_KINDS` / `_base_kind` / `_solve_diff` / `coplanar_pair_shape_factor_safe`、`_BASIS` 与 `_PRECISION` 的两条差分条目、`_attenuation` 的差分归一化分支、`feedline_impedance(spacing_mm=…)` 与 `feedline_width` 的三段式（粗网格阶梯 → 便宜网格二分 → 默认网格牛顿/割线）、两个工具的新 kind 与 `spacing_mm`、3 条 selftest（132/133/134）与 13 个测试（`tests/test_feedline_diff.py`）；
  边界是**没有闭式、也没有 ±x%**：`verify_field` 在这两个 kind 上只回 note（场解就是答案），`ground_mm` 不在馈线这条通路上，反解是 21 s~6 min 且残差原样报出（`abs_error_ohm` / `converged` / `default_solves`），正经核对宽度应该用前向的 `feedline_impedance`。
  明示，共面差分对与有限宽地不做（前者见 `30`，后者接进馈线见 `32`）。
- `0.0.18` 补上的三块没有单独开篇，口径写在 [`27`](27-自动布线器.md) §27.14：
  `fab/`（Gerber/Excellon/位置/STEP/PDF/BOM/网表/图形**只外包给 `kicad-cli` 不重算**，产物重新读一遍
  验终止符与工具表，`out_dir` 落在源目录里直接拒）、`sync/`（原理图网表 ↔ 板的**逐脚**差集，
  缺封装只报 `needs_placement`，sync 一个字节都不写）、`calc/em.py`（全波**接缝**：点名未装即
  `SOLVER_NOT_FOUND` 绝不回退，`verified_here` 恒 false）。
- `33` → `calc/spice.py` 的 `.subckt/.ends` 状态机、`_parse_subckt` / `_parse_instance` / `_expand_instances` / `_instantiate` / `_value_or_expr` / `_eval_expr`（ast 白名单）/ `_resolve_value`、`SUBCKT_MAX_DEPTH = 32`、`_node(None)` 早退、`X` 从 `NOT_IMPLEMENTED_CARDS` 移除，1 个新测试文件（`tests/test_spice_subckt.py` 27 例）与 3 条 selftest（135/136/137+138）；
  边界是**拍平不是求解**：顶层 `.param`、表达式函数与节点引用、`.model` 参数里的表达式、嵌套 `.subckt`、体内非 `.model` 指令全部拒绝，浮空岛的两引擎分歧登记为既有行为而不是缺陷。
- `34` → `calc/spice.py` 的 `_parse_param_card` / `_resolve_params` / `_resolve_element` / `_looks_numeric` / `_MissingParam`，`spec["params"]` 与 `sub["locals"]` 两级作用域、前向引用按依赖轮次解析、源卡的 dc/ac/wave 三个数字槽统一走一个读取器，1 个新测试文件（`tests/test_spice_param.py` 22 例）与 selftest 139；
  边界是**只有一种数字语法**：不带括号的表达式（`1k*2`）拒绝并要求写成 `{1k*2}`，参数里不许用函数 / 节点引用 / `.model` 参数，体内 `.param` 不可被实例覆盖。
- `35` → `calc/spice.py` 的 `_parse_b_source` / `_b_translate` / `_b_ternary` / `_b_walk` / `_b_fold` / `_b_run` / `_b_eval` / `_b_partials` / `_stamp_b_voltage` / `_stamp_b_current`，`B` 从 `NOT_IMPLEMENTED_CARDS` 移除、`BV`/`BI` 成为支路未知量、AC 走偏置点线性化，1 个新测试文件（`tests/test_spice_b.py` 39 例）与 4 条 selftest（141~144）；边界是**一个表达式、一套差分**：`laplace()` / `I(Vname)` / `ddt` / `idt` / B 源噪声按名拒绝，无第二表达式（`V=` 与 `I=` 不能同时给），差分步长固定不给调，单侧差分退化处是一阶精度。
- `36` → `calc/spice.py` 的 `_parse_tline` / `_stamp_tline`（DC 一行约束 / AC 精确 Y 参数 / 瞬态特征线法）/ `_tline_history`（二分 + 插值）/ `_tline_ports`（报告电流按 companion 重算，不读被钉零的支路行）、`T` 从 `NOT_IMPLEMENTED_CARDS` 移除、AC 半波点 `SINGULAR` 与近共振只告警一次，1 个新测试文件（`tests/test_spice_tline.py` 37 例）与 3 条 selftest（96 换成 `O` 探针、145/146）；边界是**理想无损、单 Z0、单延迟**：`O`/`U` 仍按名拒绝，`IC=`/`REL=`/`ABS=` 不接，半波点的有限答案需要损耗，所以没有——它被写进 `NOT_COMPUTED`。
- `37` → `calc/spice.py` 的 `_parse_lossy_line` / `_parse_urc` / `_lossy_model` / `_ladder` / `_expand_lossy_lines` / `_lossy_report`（解析期把 O/U 展开成普通 R/L/C 梯形）、`DEFAULT_LUMP = 20`、`O`/`U` 从 `NOT_IMPLEMENTED_CARDS` 移除、内部节点不进 `nodes_v`、`.ac` 里没有 `AC` 关键字的源幅度为 0 并给出告警，1 个新测试文件（`tests/test_spice_oline.py` 34 例）与 3 条 selftest（96 换成 `S` 探针、147/148）；
- `38` → `calc/spice.py` 的 `.noise` 解析（`V(out)` / `V(n1,n2)`，输出与输入源按名校验）、`_sweep_freqs`（`.ac` 与 `.noise` 共用同一套频率网格）、`_tline_guard`（从 `.ac` 里抽出的奇异点守卫，两处共用）、`_noise_sources`（四条机制，各带公式文本）/ `_noise_band`（按网格选 ln-f 或 f 梯形）/ `_noise_analysis`（伴随求解 `Aᵀy = e_out`，一次覆盖全部源）/ `_noise_owner`（阶梯元件的噪声归回原 `O`/`U` 卡名）、`KB_J_PER_K` / `E_CHARGE_C` / `_sig10`；委派侧 `_raw_plots` + `_parse_raw` 的 `bytes_used`（`.noise` rawfile 有两个 plot）、`_external_envelope` 的 noise 分支、`_cross_check` 的 noise 分支（只比输出谱）；顺带修 `_parse_element` 的 `M` 卡 `W=/L=/M=`（原先静默丢弃，`_mos_currents` 用 `KP·W/L`）；1 个新测试文件（`tests/test_spice_noise.py` 42 例）与 2 条 selftest（149/150）
  边界是**集总模型不是分布线**：`lump=` 只许在 `.model`（卡上是 ngspice 的 FATAL）、`LEN` 必须给、两个不同返回节点拒绝、皮肤效应/介质损耗/频变 `r`/`l` 与 `REL=`/`ABS=` 都不接，`g>0` 没有引擎可对拍。
- `40` → `calc/spice.py` 的 `_parse_element` 的 `F`/`H`/`K` 三个分支、`NOT_IMPLEMENTED_CARDS` 缩到 `J S W A`、`_Circuit.__init__` 的 `by_name` / `branch_name` / `couplings` 与 `_ctrl_row` / `_ctrl_resolved` / `_m` / `_l1` / `_l2`、`assemble` 里 `F` 的「往已有支路列加」与 `H` 的「自己一行 + 控制列 `-gain`」、`couplings` 的 AC（`-jωM`）与瞬态（`-M/h` 交叉项 + RHS 的 `i_prev`）、`K` 在元素循环开头早退（它没有 terminals）、`.ac` / `.noise` 的频率注入加 `K`、`_element_report` 的 `K` 记录与 `F` 的 `control_current_a`、`_instantiate` 的 `body_names` / `scoped_ref()`（`F`/`H` 的 `ctrl_name` 与 `K` 的 `l1`/`l2` 加实例前缀）、`NOT_COMPUTED` 改口，1 个新测试文件（`tests/test_spice_fhk.py` **45 例**）与 2 条 selftest（153/154）；边界是**线性常数增益**：`K` 没有磁芯损耗、频变电感与饱和，`F`/`H` 的增益只能是常数或 `.param` 表达式（没有 `POLY()`），控制元件收 `V E H L BV` 是 ngspice 的超集，所以这一类 deck 不做双引擎对账。
- `41` → `calc/spice.py` 的 `_parse_element` 的 `S`/`W` 分支、`NOT_IMPLEMENTED_CARDS` 缩到 `J A`、`_Circuit.__init__` 的 `switches` 与 `_resolve_switch`（模型/类型/未知参数/`RON<=0`/`ROFF<=0`/`ROFF<RON`/`VH<0` 全部拒绝、缺省写进 `defaulted`）、`_switch_control` / `switch_wanted` / `switch_conductance`、`assemble` 的电导 stamp、DC 与瞬态同一种**锁存 + 收敛后复核 + 热启动重解**（8 次上限、`switch_flips`）、`_element_report` 的 S/W 记录、`_noise_sources` 的 `thermal_switch`、`_switch_hint`（双稳），1 个新测试文件（`tests/test_spice_switches.py` **37 例**）与 3 条 selftest（96 的委派探针从 `S` 换成 `J`、156/157/158）；边界是**分段线性**：没有电荷注入、结电容、先断后合，`RON` 不许为 0，`VT±VH` 是整宽且 DC 也走带内。
- `42` → `calc/spice.py` 的 `_parse_element` 的 `J` 分支（三节点 + 只收 `AREA=`/`OFF`，`W=`/`L=` 点名拒绝）、`NOT_IMPLEMENTED_CARDS` 缩到只剩 `A`、模型引用白名单与 `want["J"]=("NJF","PJF")`、`_JFET_IMPL` / `_JFET_REFUSED`（16 条按名字拒绝 + 三条按缺省放行）/ `_resolve_jfet`（缺省写进 `defaulted`、`AREA` 卡覆盖模型、`BETA_eff=BETA*AREA`、`BETA<=0`/`LAMBDA<0`/`AREA<=0` 拒绝）、`_jfet_currents`（两式在 `Vds=Vov` 相接、无 `1/2`）/ `_stamp_jfet`、`_element_report` 的 `J` 记录（含 `gate_junction_forward_v` 与 `outside_model`）、`_noise_sources` 的 `channel_thermal`（`4kT(2/3)|gm|`，无栅结 shot、无闪烁），1 个新测试文件（`tests/test_spice_jfet.py` **56 例**）与 4 条 selftest（96 的委派探针从 `J` 换成 `A` 并改测「迁移本身」、151/152/153）；边界是 **level-1 平方律**：无 `RD`/`RS` 内部节点、无栅结二极管（只报不建模）、无器件电容、无闪烁/散粒噪声、单温度、`W=`/`L=` 由引擎拒绝。
- `43` → `vendor/ngspice/`（官方 `ngspice-47` 未改动子集：`bin/ngspice_con.exe` + 三个 DLL + `lib/ngspice/*.cm` + `share/ngspice/scripts/spinit` + `COPYING` + `NOTICE.md` + `README.md`）与 `calc/spice.py` 的 `bundled_ngspice_dir` / `find_bundled_ngspice` / `find_ngspice(allow_bundled=)` / `_run_ngspice` 的 `SPICE_LIB_DIR` 条件（`BUNDLED_NOTE` 写进 `ENGINE_NOTE`），1 个新测试文件（`tests/test_spice_bundled.py` 20 例：查找规则 / 许可卫生 / 真跑 / ASCII rawfile）与 4 条 selftest（154~157：只按需出现、`gain` 2 V、`limit` 削顶、`builtin` 仍拒绝）；边界是**只 Windows x86_64、只服务 `engine="ngspice"`**：`auto` 不看随包、不做 DLL 进程内、不翻译引擎报错、`A` 不进内置解析器。
- `39` → `fidelity/hierarchy.py`（读侧：`sheet_facts` / `sheets` / `walk` / `flat` / `instance_paths` / `parents_of` / `find_root`，加引擎用的 `materialise` / `cleanup`）、`edit/sch_ops.py` 的 4 个 sheet op（`add_sheet` / `add_sheet_pin` / `set_sheet_field` / `delete_sheet`，op 表 18 → 22）与 `_next_page` / `_used_pages` / `_page_tree`（工程全局页号）、`edit/sch_plan.py` 的 `hier_diff` / `hierarchy` / `blocking`（`FAILING_ISSUES`）/ `gates["tree"]` / `new_file`、2 个新工具（`schematic_hierarchy` / `schematic_new_file`）、1 个新测试文件（`tests/test_sch_sheets.py` 47 例）与 2 条 selftest（151/152）；边界是**只改一处文件**：页面模板、搬文件、跨页批量重命名、pin 与 label 形状一致性、KiCad 5 老格式、立创多页都不接。
- 代码里的每条"拒绝回答"分支，都能在这四十三篇里找到对应的判据来源。
