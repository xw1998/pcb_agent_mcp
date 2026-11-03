# 109 · 「mcp 还是有问题」：`tools/list` 是唯一没有体积闸门的应答

## 一句话

上一轮（108）的结论是「这一轮的中断不在 MCP 侧」。这一轮换了个问法去量：**MCP 自己发出去的每一条应答，哪一条没有预算？** 答案是一条——`tools/list`。它每次会话必拉，`core` 档实测 **54 905 B / 46 条**，`all` 档 **300 159 B / 190 条**，而同一份代码给 `tools/call` 的回包上了 48 KiB 硬闸门（0.0.104 就上了）。**守住了回包，没守住自己发出去的那条**。已修：描述先瘦身、仍超预算才用 MCP 游标分页，一条工具都不丢。

## 1 为什么这一轮不该再说「不是 MCP 的问题」

108 那句结论在方法上是对的（当时确实零 `tools/call`），但它把问题关在「有没有调用」上，而**撑爆会话的不一定是一次调用，也可以是初始化时那一次拉取**。这一轮把「发出去的字节」当被测对象重新量了一遍，就量到了 §1.1 的数字。

### 1.1 实测读数（`PCB_AGENT_HOME` 指到一个可写目录，`PYTHONDONTWRITEBYTECODE=1`）

| 出口 | 实测 | 有没有闸门 |
|---|---|---|
| `tools/call` 回包 | 按 `PCB_AGENT_MAX_RESULT_KB`（默认 48 KiB）截断 + 全文落盘 | 有（0.0.104） |
| `tools/list`（`core`，默认档） | **54 905 B / 46 条** | **没有** |
| `tools/list`（`all`） | **300 159 B / 190 条** | **没有** |
| `--selftest` stdout | **85 KB / 344 行** | **没有** |

`core` 档 54 905 B 对 49 152 B（48 KiB）**超 5 753 B**。也就是说：这套代码一边写着「一条 48 KiB 的应答不是模型能用的答案」，一边在会话的第一条请求上发 54.9 KB。宿主把这条报成什么都不会是「应答太大」——它只会报「服务坏了」「对话中断」。

拆开这 54 905 B：`name` 724 B、`description` 21 886 B、`inputSchema` 30 179 B（其中 schema 内部各参数的 `description` 12 937 B）。**大头是散文，不是结构**——工具名、参数类型、`required` 全加起来不到一半。

## 2 修法：先瘦身，再分页，绝不丢工具

`tools.tools_page(specs, cap_bytes, cursor)` 三层，按顺序（`tools.py`）：

1. **装得下就原样发**。`cap_bytes` 为 0 或全量已在上限内时，返回值与 `list_tools()` **逐字节相同**，应答里连 `nextCursor` 这个键都不出现。这一层是「不静默改变既有答案」的落点。
2. **装不下就瘦身描述**：`description` 截到 200 字符、schema 内每个 `description` 截到 80 字符，尾巴加 ` ...`。**工具名、参数名、类型、`required`、默认值一字不动**。同时在应答里加 `descriptions_shortened` 与一句 `note`，说明全文怎么拿（`PCB_AGENT_MAX_RESULT_KB=0`，或 `tool_search`）。
3. **还装不下就分页**：按 MCP 2025-06-18 的游标协议，`params.cursor` 进、`result.nextCursor` 出。分页只花往返次数，**不花条目**——这是一条硬约束，由 §3 的反向对照守着。

预算要先扣信封：`{"tools":[...],"nextCursor":...,"tools_total":...,"note":...}` 本身要占位置，`note` 一句就近 330 B。**第一版忘了扣，最坏一页量到 49 397 B，超出 49 152 B 上限 245 B**——一条 245 B 的超标正是这套机制存在的意义所在，所以把信封预留写成 `_TOOLS_ENVELOPE_BYTES = 1024`，并把它记在这里。

结果（同一台机器、同一份源码）：

| 档位 | 修前 | 修后 | 页数 |
|---|---|---|---|
| `core`（46 条） | 54 905 B | **39 102 B**（一页，46 条全在） | 1 |
| `all`（190 条） | 300 159 B | 最坏一页 **48 466 B** | 5 |
| `core`，预算强制 8 KiB | — | 最坏一页 7 393 B | 6（46 条全在） |

## 3 反向对照：每条判据都要能红

自检新增 4 条（`server.py`，`# --- 0.0.109 tools/list 也有预算 ---` 段）：

1. **默认 `core` 档一页装下且 ≤ 48 KiB**，且工具数与 `list_tools("core")` 相等。
2. **预算强制 8 KiB 时必须分页且一条不丢**：页序与 `list_tools("core")` 完全一致、每页 ≤ 8 KiB、无重名。如果分页漏一条，这条就红——没有它，第 1 条只是在炫耀「默认档本来就小」。
3. **闸门关掉即逐字节等于旧答案**，且没有 `descriptions_shortened`。这条防的是「瘦身顺手改了 schema」。
4. **`all` 档跟游标能覆盖全部 190 条**且每页 ≤ 48 KiB。

第 2、3 条在开发中各自红过一次，都是真红：

- 第 3 条第一次红，因为 `os.environ.pop("PCB_AGENT_MAX_RESULT_KB")` 是**回落到默认 48**，不是关掉；
- 第 4 条第一次红，因为上一条把 cap 设成 `"0"` 之后没复位，`all` 档就原样发了 300 170 B 一页——**这条红恰好证明了判据测的是闸门本身**，不是「碰巧小」。

## 4 `--selftest` 也收成摘要档

344 行全打在 stdout 上是 **85 KB**，和 `tools/list` 是同一个毛病：**一份读不完的诊断等于没有诊断**。现在默认只打 FAIL 明细 + 最慢 5 条 + 一行汇总：

```
---- 347 checks, 124.6s total, 105.4s of it in checks over 1 s, slowest 25.3s (...)
  slowest: 25.3s ..., 15.1s ..., 9.0s ..., 8.3s ..., 7.6s ...
  348 passing checks hidden; --selftest --verbose (or PCB_AGENT_SELFTEST_VERBOSE=1) prints every line
348/348 passed in 124.6s
```

**stdout 85 KB → 4 002 B**。全量细节一条没删，`--selftest --verbose` 或 `PCB_AGENT_SELFTEST_VERBOSE=1` 随时拿回；`FAIL` 行在任何档位都照打——藏通过的行和藏失败的行不是一回事。

## 5 本轮读数（提交前实测）

```
348/348 passed in 124.6s
stdout 4 002 B（修前 85 KB）
/ 348 checks, slowest 25.3s（棘轮 180 s / 35 s 内）
```

`tests/test_server_protocol.py::test_tools_list_follows_the_profile` 同步改成走游标计数——它原来断言「`all` 档一次返回 190 条」，分页后这条会因为正确的原因变红，改成按客户端真实行为数。

## 6 给人的三行取证手册

以后再遇到「会话中断」，按顺序取这三样，别先猜：

```
1. 会话里有没有 tools/call？有 → 看那条的返回字节与耗时（>48 KiB 应已被 cap，>60 s 应已被 timeout）。
2. 一条 call 都没有却断了 → 断在初始化：拉 tools/list 的字节数（core 档应 ≤48 KiB）与 initialize 应答大小。
3. 两者都没有 → 是调用方自己的输出（目录列举、整表打印）把上下文推高的，不是 MCP。
```

## 7 欠账

1. **`core` 档仍然靠瘦身才进 48 KiB**（39 102 / 49 152，余量 20 %）。真正的目标是把默认档压到 ≤10 KiB（只留 `tool_search` + `tool_call` + 少量原语），那需要用户点头——它改变默认暴露的工具数量。
2. **客户端是否跟 `nextCursor` 未验证**。`core` 档不分页，所以默认路径不吃这条风险；但一个不跟游标的客户端拿 `all` 档只会看到第一页。等有机会在真客户端上验一次。
3. **一条既有测试红灯，与本轮无关**：
   `tests/test_blockers.py::TestOneOpenSideIsADifferentDiagnosis::test_the_tool_turns_that_into_a_next_step_instead_of_a_shrug`
   期望 `hint` 里有 `further along the net`，而 `route/blockers.py:153` 只在那一条
   分支里带这句话，实际走到的是 `nothing at this point needs to move: walk the net
   yourself ...`。`git log -S` 显示这句话最后一次改动在 `b9c6478`（0.0.18）。
   **不擅自改**：现在的实现说「这个点不用动」，测试期望「故障在更下游」——那是
   两种不同的诊断结论，不是文案错字；把断言改成适配现状就是把差异藏起来。
   登记为欠账，需要单独判定哪一边才是 board_blockers 的意图。
4. **`PCB_AGENT_CALL_TIMEOUT_S` 默认仍是 `0`（无上限）**。船上时间由宿主决定，但一个卡住的 `board_drc` 会占住 worker 线程直到进程被杀——这仍然是「表现成中断」的一条路径。改默认值属于行为变更，需要用户点头。
