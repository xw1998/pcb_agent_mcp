# 45 · 立创写 snippet：名字问应用，参数问调用者，效果问 probe

`lceda_pcb_write` 从 0.0.38 起就是一条**可检查**的写路径：`probe → code → probe → 比较`，
probe 前后相同就拒绝（`WRITE_HAD_NO_EFFECT`）。它故意不生成 snippet，理由是当时一句实话——
厂商的写方法名一个都没在真机上验证过，凭空造一个「看起来对」的调用正是这个项目一直在拒绝的东西。

留下的洞很具体：调用者知道**要做什么**（保存 / 移动 / 创建），但不知道**哪个方法做**。
猜错的最坏情况不是抛异常，而是撞上一个名字相近、语义不同的方法，改动就这样悄悄发生了。

`doc.api` 从另一头把这个洞补上：**编辑器会把自己有什么报出来**，带厂商的中文注释，并且
每个类带一个 `callPath`——那正是运行期接收者（`PCB_Document` 不是 `pcb_Document`）。
于是把一件事拆成三份，每份只由能负责的一方回答：

| 要素 | 谁回答 | 为什么不能换人 |
|---|---|---|
| 方法**名字** | 应用（`doc.api`） | 这是唯一有证据来源；记忆与猜测都没有 |
| **参数** | 调用者（`argsv`） | 应用只报名字和注释，**不报签名**，所以参数一律原样传入 |
| **效果** | probe（`lceda_pcb_write`） | 名字存在不等于这个调用改了设计 |

## 45.1 名字：从「最像的那个」改成「要么唯一，要么拒绝」

打分只有三档，没有编辑距离、没有模糊匹配、没有模型：

- **3 = 精确**：方法名（小写后）**就是**这个操作（`save` → `save`）；
- **2 = 包含**：方法名里含这个操作词（`moveComponent` 含 `move`）；
- **1 = 只有注释**：方法名毫无关系，只有厂商中文注释说它是这件事（`applyEdit` 注释「移动器件」）。

精确必须压过包含，否则 `save` / `saveAs` 会算成「一样好」，然后把「正确答案」和「另一个答案」
一起端出来让人挑——这不是谨慎，这是把判断题推给用户。同理，注释档命中但方法名以
`get/is/query/list/find` 开头的一律跳过：注释里借了一个写词，不代表它是写。

然后：

- 最高档只有一个 → 生成；`evidence` 字段写清是 `exact_name` / `name_contains_operation` /
  `vendor_comment_only`，`confidence` 是 `name` / `comment_only`；
- 最高档有多个 → `SNIPPET_AMBIGUOUS`，**把候选连同注释一起给出来**，让人 `pick`；
- 一个都没有 → `SNIPPET_NO_API`，并提示「自己写 snippet 交给 `lceda_pcb_write`」——
  拒绝的时候给出路，而不是让人原地打转。

`SNIPPET_NO_ARGS` 是第四种：`move` / `create` 需要参数而 `argsv` 是空的。宁可报「没给参数」，
也不发一个 `moveComponent()` 出去让编辑器自己在运行期炸。

## 45.2 接收者：绝不把类名小写化成属性名

`PCB_Document → pcb_Document` 是**应用给的映射**（`callConventions.classToProperty`），
不是命名约定。`DMT_Pcb → dmt_Pcb` 更是没有任何缩写的规律可言。所以解析顺序是：

1. 调用者显式给的 `property`（`receiver_from: caller`）；
2. dump 自己的 `callPath`（`receiver_from: callPath`）——这是 ⭐ 常态；
3. dump 里带的 `classToProperty` 映射（`receiver_from: classToProperty`）。

三条都没有 → `SNIPPET_NO_RECEIVER`，hint 里写明「类名不是属性名，本工具不会把它小写化成一个」。
`callPath` 还必须长得像 `eda.<prop>.`，否则也拒——一个奇怪的路径拼出来的 snippet 只有一种下场。

## 45.3 两种 `doc.api` 形状，认错就拒

实测（4.1.60，见 [`24`](24-立创进程内桥.md)）：

- **namespace 级**：`{types: [{name, comment}], callConventions: {entry, classToProperty}}`
  ——只有类名，**没有方法**；
- **类级**：`{title, remarks, methods: [{name, comment}], callPath}`。

把 namespace 表当类表传进来是很容易犯的错（都是 `doc.api` 的答案），所以它被单独认出来，
回 `ARG_INVALID` 并提示「这是 NAMESPACE 表，请按类问一次」。

## 45.4 参数只有两种待遇

- **字符串**：原样插入，因为调用者写的是 JavaScript（`"'U1'"` 就是 `'U1'`，不加引号）；
- **其余一切**（数字、`true`/`false`/`null`、对象、数组）：`json.dumps`，
  1-based 位置记进 `json_encoded_args`，这样「哪些参数被编码了」是一个读数而不是印象。

空字符串参数直接拒：`''` 传进去 99% 是漏填，而不是真的想传空。

## 45.5 probe 建议：读的对象必须能因为这次写而变

`want_probe` 默认开，`suggest_probe` 用同一张表挑一个**读**方法作为 probe，只读不跑。
排序是 `(-score, 关键词强度, 长度, 名字)`，关键词强度按「能读到你要改的那个对象」排：

    位置/变换/器件列表  ≫  任意 get

这条不是洁癖，是一张真表逼出来的：`getCanvasOrigin` 与 `getAllComponentsPosition` 都是读，
打分都是 2，但**只有第二个可能在器件移动后变化**。按名字长度排会选到画布原点——
一个永远不变、因此永远「没有效果」的 probe，会把每一次成功的写都判成失败。

## 45.6 每一条答案都带「没验证的是什么」

`verified_against_app` 恒为 `false`，`not_verified` 固定三条：

1. 方法的参数顺序与类型（应用给的是名字和注释，不是签名）；
2. 方法的返回值是什么；
3. 调用它是否真的改了设计——那是 probe 的事。

`precision` 里写死一句：**方法存在是应用的事实，签名、返回值与效果都未证，本服务从未运行过这条
snippet**。要效果就读回来：`lceda_pcb_write`（带 probe）。

## 45.7 与写路径的配合流程

```
lceda_pro_api(class_name="PCB_Document")     # 名字与 callPath 的唯一来源
        ↓
lceda_write_snippet(op="move", api_dump=…, argsv=["'U1'", 5, 0])
        ↓  code + probe
lceda_pcb_write(session=…, op="move", code=…, probe=…)
        ↓  before / after / changed
board_progress()                              # 分步日志
```

也可以完全离线：把 `doc.api` 的类级答案粘贴给 `api_dump`，不需要 session、不需要应用在跑。
这条通路是本轮全部测试的走法——**生成零副作用**，所以它能在没有编辑器的地方被反复质疑。

## 45.8 落点与边界

- 代码：`src/pcb_agent_mcp/lceda_snippet.py`（纯函数，不碰网络、不跑任何东西）；
  工具注册与 `available` / 不实现清单在 `src/pcb_agent_mcp/tools.py`。
- 测试：`tests/test_lceda_snippet.py`（19 例）。负控制是重点——去掉「精确压过包含」，
  `save` 就重新变成歧义；去掉 probe 的关键词强度排序，probe 就选回 `getCanvasOrigin`。
- selftest：854（名字与接收者都来自应用）、855（表里没有就拒，不近似）。
- 边界：**只这三个 op**（save / move / create）。要别的事，自己写 snippet 交给
  `lceda_pcb_write`——本工具不会为了多覆盖一个 op 去猜词表，词表一宽，歧义立刻回来。
- 边界：**不生成参数**。`argsv` 就是全部知识，工具不知道 `moveComponent` 吃几个数。
- 边界：**不知道方法是否可写**。名字与注释是应用的原话，`deleteComponent` 这类破坏性方法
  不会因为「它是写」而被额外拦下——probe 是唯一的护栏，而 probe 是调用者的责任。
