# 96 · 会话不是被计算打死的：`tools/list` 的体积与一条批量请求

> 它回答的问题：这个服务器为什么「聊着聊着就断了」？一个算得对的服务，凭什么
> 会在第一次握手时把对话撑死、又凭什么会被一条合法请求整死？
>
> 它推翻的前提：**「工具越多越强」**（`01` 早就否过这条，但 190 个工具的
> `tools/list` 就是它的化石）——工具数不是能力，是**每一轮对话都要付的租金**。

---

## 96.0 两句话

1. `tools/list` 一次回的 192 个工具 **316 728 B ≈ 105 576 token**（按 /3 粗算）。
   聊天客户端把它放进模型上下文，窗口在用户说第一句话之前就没了。
2. JSON-RPC 批量请求是一个**数组**，`handle()` 当 dict 用 ⇒
   `AttributeError: 'list' object has no attribute 'get'`。这个异常抛在**任何
   handler 之外**，直接打断 `serve()` 的读循环 ⇒ 进程退出 ⇒ 之后所有请求静默无响应。

两条都不是算错，是**传输出错**。算得对而送不出去，读数就是零。

---

## 96.1 实测（不是估计）

| 项 | 读数 | 怎么量的 |
|---|---|---|
| 旧 `tools/list` 一行 | **316 728 B** / 192 工具 | 把 `git show HEAD:src/pcb_agent_mcp/server.py` 当模块执行，向它的 `serve()` 喂一条 `tools/list`，量 stdout 那行的字节 |
| 旧答复的 token 粗算 | **105 576**（/3） | 同上；用户报的「约 10 万 token / 315 KB」与它一致 |
| 新 `tools/list` 一行 | **52 021 B** / 46 工具 | 同一口径，同一台机器 |
| 缩小倍数 | **6.09×** | 316 728 / 52 021 |
| 旧版收到批量 | `AttributeError: 'list' object has no attribute 'get'` | 向 HEAD 的 `serve()` 喂 `[{"id":1,"method":"ping"}]`，异常复现原话 |

46 个工具仍然要 52 KB（≈17 000 token）。**这不是零成本**，只是从「装不下」变成
「装得下但很贵」；见 §96.6。

---

## 96.2 机制一：默认只列入口，全量仍在（profile）

`tools.list_tools(toolset)` 收一个 profile：

| profile | 含义 |
|---|---|
| `core`（默认） | `CORE_TOOLS` 46 条入口 + 两个发现工具 |
| `all` / `*` | 全部 192 条（0.0.95 之前的行为） |
| 前缀/名字表 | `board_,magnetics_`、`core,thermal_`，逗号或空格分隔 |

环境变量 `PCB_AGENT_TOOLSET` 决定服务端用哪个（`server.tools/list` 每次都读，
不是启动时缓存）。库调用方 `tools.list_tools()` 的默认参数仍是 `"all"` ——
**不改既有调用者的答案**。

三条护栏，每条都有测试：

* **`core` 里的名字必须真的存在**。不存在的名字不会报错，只会被静默过滤掉 ——
  于是能力「消失得无声无息」。`test_every_name_in_the_profile_exists` 守着。
* **发现工具在任何 profile 里都在**。否则被过滤掉的工具在本次会话里彻底不可达。
* **一个都匹配不上的 profile 回退到 `core`**，不回一张只有两个工具的表。
  `PCB_AGENT_TOOLSET=boards_`（`board_` 的笔误）不该表现成「能力被删光了」。

---

## 96.3 机制二：`tool_search` + `tool_call`

`tool_search(query, limit)` 按**任务词**在 `name + description` 里打分，返回命中项的
**完整 `inputSchema`**；`tool_call(name, arguments)` 用那份 schema 调用，返回的信封
与直接调用**逐字节相同**（`test_calling_through_the_discovery_path_is_the_same_answer`）。
`tool_call` 拒绝调用自己（否则可以无限套），未知名字给 `TOOL_NOT_FOUND` + 指向
`tool_search` 的 hint。

### 中文词是硬缺口，补法是查表而不是假装懂

第一次实现的分词是 `re.split(r"[^0-9a-z_]+", query)`。中文查询**被切得一个词都不剩**，
于是 `tool_search("阻抗")` 回 `{"status":"ok","returned":0}` —— 一个**静默的假否定**，
读起来就是「这个服务器不会算阻抗」。

补法是 `_CJK_ALIASES`：一张 50 条的任务名词表（阻抗 → `impedance`、过孔 → `via`、
铺铜 → `zone pour`、爬电 → `creepage`、熔断 → `fusing` …），只在**包含 CJK 字符时**
参与打分，英文查询逐字不变。

表外的中文词不再安静地回零，而是明说：

```
hint: none of the Chinese words in this query is in the alias table, so nothing
      was searched: it covers common task nouns (阻抗 / 过孔 / 铺铜 / 散热 / 爬电 ...)
search_was_english_only: true
```

打分有两处同样的毛病：原先是**子串**匹配（`via` 会命中 `trivial`，于是 `过孔`
的前 25 条全是"描述里提过这个词"的工具），且名字命中与描述命中**同权**。现在按整词
匹配（`_` 算边界，`via` 命中 `via_resistance` 但不命中 `trivial`），并且**名字命中权
重 12、描述命中 4** —— 名字里说的是"这个工具干什么"，描述里说的往往只是"它提到过"。

**这是一张查表，不是翻译**：它不承诺覆盖任意中文，只承诺覆盖「用 PCB 工具的人会打的
那批名词」，覆盖不到就点名说没覆盖。`test_a_query_nothing_maps_says_so_instead_of_answering_zero`
是一条能失败的反向对照 —— 若哪天它退回「ok / 0 命中 / 无 hint」，测试红。

---

## 96.4 机制三：批量请求按数组答，坏元素只坏自己

```
[ {"id":1,"method":"ping"}, 7, {"id":2,"method":"ping"} ]
  -> 一行数组，三个元素：{id:1 答复} / {id:null, error:-32600} / {id:2 答复}
```

规则（每条一个测试）：

| 输入 | 答复 |
|---|---|
| 批量、元素合法 | **一行**数组，元素按序 |
| 批量含非对象元素（如 `7`） | 该元素单独 `-32600`，`id` 为 `null`，邻居照跑 |
| 批量含通知（无 `id`） | 该元素**不产生元素**（规范如此） |
| 空数组 `[]` | **一个** `-32600` 错误对象（不是静默无操作） |
| 全是通知的批量 | 什么都不写 |

单个请求那条路径也一起加了 `try/except`：**一个坏请求或坏工具只能结束一轮，不能结束服务**。
理由写在注释里，因为「进程死了」与「对话死了」在客户端看来是同一件事。

---

## 96.5 机制四：`NaN` / `Infinity` 不是 JSON

`json.dumps` 默认 `allow_nan=True`，会把 `float("nan")` 写成裸的 `NaN`。严格客户端
**整条消息**拒收 —— 又是「一轮对话死掉」，而且比异常更安静：服务端一切正常。

所以有两处序列化点：JSON-RPC 信封（`server._dumps`）与工具答复的文本
（`tools.result_to_text`）。两处原本各写一份 `json.dumps`，现在统一走
`jsonsafe.dumps`：

* 先试 `allow_nan=False`（正常路径零变化）；
* 抛 `TypeError/ValueError` 时走 `sanitise()`：非有限浮点 → `null`，其它不可序列化
  对象 → `str()`。**不删键**——删掉一个键看起来像「这个工具没算这一项」。

反例：`{"v":NaN,"w":Infinity}` 现在上线为 `{"v":null,"w":null}`，行仍是合法 JSON。

---

## 96.6 还没做（写清楚，免得被读成已经做了）

* **`tool_search` 是词法检索，不是语义检索**。同义词没进表就搜不到（`width` 搜不到
  `线宽` 以外的说法）。`calc_capabilities` 的分层地图仍是更可靠的入口。
* **46 个工具仍是 52 KB ≈ 17 000 token**。真要更小，只能改「入口是哪几个」这个取舍，
  那是产品判断，不是工程量。可用的旋钮是 `PCB_AGENT_TOOLSET`。
* **profile 不能按客户端协商**（MCP 没有这个字段）。现在只有环境变量一个入口。
* **未做流式/分页 `tools/list`**：`nextCursor` 没有实现，一次给完。

---

## 96.7 读数与门禁

| 门禁 | 结果 |
|---|---|
| `--selftest` | **329/329** |
| `tests/test_tools_profile.py` | 15 例（含 3 条能失败的反向对照） |
| `tests/test_server_protocol.py` | 15 例（批量 6 / JSON 3 / 协议面 6） |
| 旧版批量复现 | `AttributeError: 'list' object has no attribute 'get'` 逐字复现 |
| 旧/新 `tools/list` | 316 728 B → **52 021 B**（6.09×） |
| `dependencies` | `[]` 仍成立（`jsonsafe.py` 只用 stdlib `json`） |
