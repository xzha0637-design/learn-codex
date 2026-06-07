# s09: Responses API — Codex 的线协议

> *"不是 Chat Completions，也不是 Anthropic Messages。"*

[learn-codex 总览](../README.md) · [s08 Rollout 续接](../s08_rollout/) → **s09** → [s10 SQ/EQ 协议](../s10_sq_eq_protocol/)

---

## 先把思想说透：模型动不了，它只会「按格式吐条目」——看懂那张单子，一切就不神秘了

[s01](../s01_agent_loop/) 讲过一句话：模型很聪明，但它**动不了**，它只会输出文字。这一章把那句话往下钻一层——既然模型只会「输出」，那它到底**输出成什么样子**？我们又是**怎么把话递给它**的？把这条「电话线」看明白，前面几章里那些「为什么 `messages` 长这样、为什么工具调用是独立一项」的疑惑会瞬间全部消散。三层道理。

**道理一：你和模型之间隔着一根网线，两头必须先约好「话要怎么填进单子」。**
模型不在你的程序里，它跑在 OpenAI 的服务器上，你俩靠网络喊话。可网络只会传字节，不认识「系统提示」「对话」「工具」这些概念——所以两头必须**事先约定一张单子**：哪个字段放身份设定、哪个字段放对话、哪个字段放工具清单、谁先谁后。这套约定就叫**线协议**。打个比方：同样是「点一杯咖啡」，麦当劳的点单格式和星巴克的不一样，你走进哪家，就得按哪家的单子填。市面上常见三张单子——Anthropic 的 Messages（Claude 用）、OpenAI 老的 Chat Completions、OpenAI 新的 Responses；它们表达的东西大同小异，但**字段名和摆法各不相同**。Codex 选的是 Responses。看懂一章，无非就是把「发出去的单子」和「收回来的单子」摊开，逐格念明白。

**道理二：模型的「行动」全是假象——它从头到尾只是在单子上多填了一种条目而已。**
这是最容易被神话、也最该被戳破的一点。当你看到 agent「跑了个命令」，仿佛模型真伸手干了活——其实模型**什么都没干**。它做的，只是在回话的单子里多填了一项，类型写着 `function_call`、名字写着 `shell`、参数写着 `{"command":"ls"}`。这一项和「它说的一句人话」是**并排的两个条目**，没有高下之分，都只是「填在单子上的字」。真正去执行命令、把结果再填回单子的，是你那段 harness 代码（正是 [s01](../s01_agent_loop/) 的循环）。所以 Responses 把整段对话拆成一串**扁平的条目**：一句用户消息是一项、一次工具调用是一项、一条工具结果又是一项，平铺并排——这恰恰就是前几章 `messages` 列表的形状。一旦你接受「模型只会吐条目、动手的另有其人」，「为什么工具调用要单列成一项」就不再是疑问，而是顺理成章。

**道理三（最关键）：协议是跟着模型长出来的，不是先有协议再塞模型——所以连「想了什么」都被请进了单子。**
为什么 Codex 偏偏挑 Responses，而不是更老牌的 Chat Completions？因为它家的模型工作方式是「**先想一段、再决定动手**」，而协议得能**装下这种工作方式**。于是 Responses 干了件 Chat Completions 没做的事：把模型的**推理**也当成单子上一等的内容——请求里能用一个旋钮调「想多深」（轻一点/中等/深一点），回话里那段思考会作为**单独一项**回来，和「人话」「工具调用」并列。换句话说，「模型想了什么」不再是日志里的旁注，而是协议里**要被传输、被存档、下次还能原样带回去**的正经数据。这就是「**协议跟随模型**」：模型怎么思考、怎么行动，单子就长成什么样——而不是反过来拿一张老单子去削足适履。（这和 [s03](../s03_apply_patch/) 里「工具形状跟随模型」是同一个道理的两次现身。）

把三点连起来：隔着网线要约定单子 → 模型的「行动」只是单子上多填一种条目 → 而单子的样子是被模型的思考-行动方式塑造出来的。看懂这张单子，这一章就通了；后面那些字段对比，不过是把这三点落到一个个具体格子里。

## 问题

core 到底怎么和模型通信？三家 API 形状各不相同：Anthropic 的 Messages、OpenAI 的 Chat Completions、OpenAI 的 **Responses**。Codex 选了 Responses——为什么？因为它原生支持 **reasoning（推理）**、**item 流式**、以及服务端会话状态，正好贴合 Codex 模型「先推理、再行动」的工作方式。

## 解决方案

用 OpenAI **Responses API**。它和 Chat Completions 有几处关键不同：

- 系统提示叫 `instructions`（不叫 `system`）；
- 输入 `input` 是一串**扁平的 item**：`message` / `function_call` / `function_call_output` 混排（这正是前几章 `messages` 列表的形状）；
- 工具是**扁平**的 `{"type":"function","name","description","parameters"}`；
- 多了 `reasoning: {effort}` 这一档——控制推理力度，Chat Completions 没有。

下面就用一个最小例子，把「请求怎么拼、响应怎么拆」一步步走一遍。

## 工作原理

看 [code.py](code.py)（本章纯讲解、离线），三个函数：

- `build_request(...)` 把对话 + 工具打包成一个 Responses 请求（一个 Python dict，序列化后就是发出去的 JSON）；
- `parse_response(resp)` 从响应的 `output` items 里分别抽出 `reasoning` / `message`(文本) / `function_call`(工具调用)；
- `protocol_comparison()` 把同一个 `shell` 工具在三家协议里的写法并排打印。

这正是 [codexlib.py](../codexlib.py) 的 `openai` 后端真实在做的事；真源码见 `../../codex/codex-rs/core/src/client.rs`（`ModelClient::stream_responses`）与 `codex-api` crate。

**走一遍** —— 跟着 `--demo`，看一来一回的数据各长什么样、关键字段为什么是这个名字：

1. **构造请求。** 给定一句用户消息「列出文件」和一个 `shell` 工具，`build_request` 打包出这样一坨 JSON（发给 OpenAI 的就是它）：

   ```json
   {
     "model": "gpt-5-codex",
     "instructions": "You are Codex.",
     "input": [
       {"type": "message", "role": "user", "content": "列出文件"}
     ],
     "tools": [
       {"type": "function", "name": "shell", "description": "run a command",
        "parameters": {"type": "object",
                       "properties": {"command": {"type": "string"}}}}
     ],
     "reasoning": {"effort": "medium"},
     "stream": true
   }
   ```

   逐字段看**为什么是这样**：系统提示放在 `instructions`（不是 `system`）；对话放在 `input`，是一串**扁平 item**（这里只有一条 user message，将来工具调用/结果也会平铺进同一个数组）；工具是**扁平**的 `{type:"function", name, parameters}`；`reasoning.effort` 告诉模型「中等力度地想一想」——这一档 Chat Completions 根本没有。

2. **模型回来一份响应。** demo 用一份写死的响应（含推理 + 一次工具调用）。响应的核心是 `output`——一串 item：

   ```json
   {
     "output": [
       {"type": "reasoning",
        "summary": [{"type": "summary_text",
                     "text": "用户想看文件，应当调用 shell 跑 ls。"}]},
       {"type": "function_call", "call_id": "call_abc", "name": "shell",
        "arguments": "{\"command\": \"ls -la\"}"}
     ]
   }
   ```

   *为什么*推理和工具调用是**两个并排的 item**：Responses 把「想了什么」和「决定干什么」拆成独立条目，于是流式时可以先把推理推给你看、再推工具调用——而不是塞在一条消息内部。注意 `arguments` 是一个 **JSON 字符串**（不是对象），需要再 `json.loads` 一次。

3. **解析响应。** `parse_response` 按 `type` 分流，把上面的 `output` 抽成三样东西：

   ```
   reasoning: ['用户想看文件，应当调用 shell 跑 ls。']
   text:      ''
   tool_call: shell({'command': 'ls -la'})  call_id=call_abc
   ```

   这里 `text` 是空的——因为这一回合模型没有说人话，它**直接决定调工具**。`call_id` 很关键：等 `shell` 跑完，工具结果要带着**同一个 `call_id`**作为 `function_call_output` 平铺回 `input`，模型才知道「这是刚才那次调用的结果」。这就把本章和前几章的回合循环接上了。

4. **三家协议并排。** demo 最后把同一个 `shell` 工具定义在三家的写法打印出来，一眼看出差异：

   ```
   Anthropic（Claude）:        {"name":"shell", …, "input_schema":{…}}
   OpenAI Responses（Codex）:  {"type":"function","name":"shell", …, "parameters":{…}}
   OpenAI Chat Completions:    {"type":"function","function":{"name":"shell", …,"parameters":{…}}}
   ```

   Anthropic 用 `input_schema` 且工具是顶层对象；Responses 用 `parameters` 且扁平；老的 Chat Completions 把工具又**嵌**进一层 `function`。同一件事，三张不同的单子——这就是「线协议」的全部含义。

## 生产级：网络会抖、模型会拒——重试、退避、回退怎么做

"请求怎么拼、响应怎么拆"通了。但真相是：`stream_responses` 不是一次干净的函数调用，而是一条**会抖的网络长流**——它会中途断线、会被限流（429）、会撞上服务端 5xx。玩具在这里抛异常崩掉；能上生产的 harness 把"失败"当成一条**可恢复的常规路径**。这一节把它讲到经得起检验，对应 [`responses_retry.rs`](../../codex/codex-rs/core/src/responses_retry.rs) + [`util.rs:85`](../../codex/codex-rs/core/src/util.rs)。

### 一、先分类：哪些错该重试，哪些重试也白搭

重试的第一原则不是"怎么退避"，而是"**该不该重试**"——盲目重发一个 401 只是浪费时间。

| 类别 | 例子 | 怎么办 |
|---|---|---|
| **可重试（瞬时）** | 流断线、429 限流、5xx 服务端错 | 退避后重发——问题大概率自己会好 |
| **致命（非瞬时）** | 401 鉴权失败、400 请求非法 | 立刻失败——重试一万次还是错 |

真源码里这体现为 `CodexErr::Stream(_, requested_delay)`（可重试、还可能带服务端要求的延迟）与其它致命变体的区分。本章 [code.py](code.py) 用 `TransientError` / `FatalError` 两个类把它教学化。

### 二、指数退避 + 抖动：为什么不能"固定间隔重试"

可重试的错误等多久再发？真 Codex 的 `backoff()`（`util.rs:85`）公式：

```
delay = INITIAL_DELAY × FACTOR^(attempt-1) × random(0.9, 1.1)
```

- **指数**（200 → 400 → 800ms…）：服务端可能正过载，越重试越要给它喘息，而不是变本加厉猛敲。
- **抖动（jitter）±10%**：最容易漏、却最关键的一笔。若 1000 个客户端被同一次故障打断、又都用**完全相同**的间隔重试，它们会**齐刷刷**在同一毫秒再次涌上来，把刚缓过来的服务端二次打垮（thundering herd / 重试风暴）。一点随机抖动，就把这波重试**摊开**在时间轴上。

### 三、听服务端的：honor Retry-After

服务端在 429 里明说了"等 1500ms 再来"，就**别用退避公式瞎猜**——听它的。真源码：`CodexErr::Stream(_, requested_delay)` 若带了 `requested_delay` 就 `unwrap_or_else(|| backoff(...))`——有就用服务端给的，没有才退避。

### 四、封顶 + 传输回退：重试不是无限的

- **两个上限**：`request_max_retries`（整个请求）与 `stream_max_retries`（流中途重连）分开配（[`model-provider-info`](../../codex/codex-rs/model-provider-info/src/lib.rs)），且都有硬上限，防止配出一个"永远重试"的死循环。
- **传输回退**：重试在 **WebSocket** 上耗尽后，真 Codex 不直接放弃，而是**降级到 HTTPS+SSE、把重试计数清零再来一轮**（`responses_retry.rs`）——一条路堵死，换条路再走。
- **告诉用户**：每次重连往前端发一句 `Reconnecting... {n}/{max}`，免得用户对着看似卡死的屏幕干瞪眼（还特意藏掉第一次 websocket 重连的噪声）。

### 走一遍

`--demo` 段 ④ 把这套跑给你看（已固定随机种子，输出可复现）：

```
 (a) 流断两次后自动重连：
  ↻ Reconnecting... 1/5，193ms 后重试（指数退避+抖动）：stream disconnected
  ↻ Reconnecting... 2/5，372ms 后重试（指数退避+抖动）：stream disconnected
    → ok：第 3 次连上，拿到完整响应
 (b) 429 限流、honor 服务端 Retry-After（不瞎猜延迟）：
  ↻ Reconnecting... 1/5，1500ms 后重试（服务端 Retry-After）：429 too many requests
    → ok：等满服务端要求的 1500ms 后通过
 (c) 鉴权失败是致命错误，立刻放弃：
  ✗ 致命错误，不重试：401 unauthorized（API key 无效）
 (d) 一直失败，封顶 max_retries=2 后放弃（真身会先试 WebSocket→HTTPS 回退）：
  ↻ Reconnecting... 1/2，206ms 后重试（指数退避+抖动）：stream keeps dropping
  ↻ Reconnecting... 2/2，366ms 后重试（指数退避+抖动）：stream keeps dropping
  ✗ 重试 2 次仍失败，放弃
```

看 (a) 的 193/372ms：就是 200×2⁰ 和 200×2¹ 各乘了点抖动；(b) 直接用服务端的 1500ms；(c) 一眼判死、零重试；(d) 撞到上限才放手。

### 还有一类特殊"失败"：上下文撑爆 ≠ 重试

不是所有失败都靠重试。撞上 `ContextWindowExceeded`（上下文超限），原样重发只会再超一次——正确反应是**先压缩历史、再重试**（reactive compaction，见 [s07](../s07_context_compaction/)）。这点出一条铁律：**"恢复"不等于"重试"**，得按错误性质选对策略。

> 一句话：生产级的模型调用，一半代码在"怎么发请求、解析响应"，另一半在"**它失败时怎么优雅地活下来**"。后者才是玩具与产品的分界线。

## 🆚 与 Claude Code 的不同

| | Claude Code（Anthropic Messages） | Codex（OpenAI Responses） |
|---|---|---|
| 系统提示 | `system` 参数 | `instructions` 参数 |
| 工具定义 | `{name, description, input_schema}` | `{type:"function", name, description, parameters}` |
| 工具调用/结果 | 内容块 `tool_use` / `tool_result`（嵌在 message 里） | 独立 item `function_call` / `function_call_output` |
| 推理 | extended thinking 块 | `reasoning.effort` 档位 + reasoning item |
| 对话表示 | `messages[].content` 块数组 | 扁平的 input-item 列表 |

**为什么？** 最直接的原因是**各用自家模型与自家 API**——工具/对话的形状必须贴合各自后端。更深一层：Responses 的「item 流 + reasoning 档位 + 服务端状态」让 Codex 能把模型的**推理过程**显式纳入协议、并支持流式地穿插工具调用与审批（[s10 SQ/EQ 协议](../s10_sq_eq_protocol/)）。这不是谁更好，而是「工具与协议跟随模型」的又一例证（呼应 [s03](../s03_apply_patch/) 里 apply_patch 的同款逻辑）。

## 深入：教学版 vs 真 Codex 源码

真客户端在 [`core/src/client.rs`](../../codex/codex-rs/core/src/client.rs)（约 89K）+ `codex-api` crate。教学版只展示了"构造请求 / 解析响应"的形状。

<details>
<summary>一、WebSocket 升级 + SSE 回退 + prewarm</summary>

教学版只画了「构造一个 dict → 解析一个 dict」，仿佛一来一回就完事。真 Codex 在**连接**这一层就讲究得多：

- **优先 WebSocket**：用 `ResponsesWebsocketClient` 在一条长连接上发多个回合，省掉每回合重新握手（TLS + HTTP）的开销。
- **失败回退到 HTTP + SSE**：环境不支持 WebSocket（某些代理/网关）时，自动退回「HTTP POST + SSE 流式响应」这条更通用的路。
- **prewarm（预热）**：在你真正发第一个请求之前，先偷偷发一个**不生成内容**的请求，把连接、路由、服务端会话状态都「热」起来——于是你按下回车后，**首字延迟**明显更短。

*为什么教学版可以全省掉*：这些都不改变「请求/响应长什么样」，只影响**快不快、稳不稳**。教学目的是讲清协议形状，所以连接优化整层略去。

</details>

<details>
<summary>二、流式 SSE 增量解析</summary>

教学版的 demo 用一份**写死的完整响应**一次性解析。真 Codex 面对的是**流**：服务端通过 SSE（Server-Sent Events）把响应**一块块**推过来，每块是一个带 `type` 的事件，例如：

```
event: response.output_item.added        ← 新增了一个 item（如一个 function_call 的壳）
event: response.reasoning_summary.delta  ← 推理摘要又来了一小段文字
event: response.output_text.delta        ← 助手正文又来了一小段
event: response.completed                ← 这一回合到此结束
```

真源码用 `eventsource_stream` 增量解析这些事件，边收边把 output items 拼起来。这正是命令输出 `OutputDelta`、流式 reasoning 的来源——它们汇成事件流，喂给 [s10](../s10_sq_eq_protocol/) 的 **SQ/EQ** 那一层。*为什么要流式*：用户不用干等模型憋完整段，能边想边看；工具调用也能在出现的瞬间就被前端捕获。

</details>

<details>
<summary>三、粘性路由：重试时怎么"接着上次那台继续"</summary>

重试本身见上面的「生产级」一节；这里补它的搭档——**粘性路由（sticky routing）**：请求携带一个 `x-codex-turn-state` 头，让**同一回合**的多次请求落到**同一个后端实例**。*为什么重要*：Responses 有**服务端会话状态**——前面那次推理/工具调用的上下文存在某台机器上，重试若被路由到别的实例，状态就对不上了。粘性路由保证「接着上次那台继续」——这正是重试能**安全**进行的前提（幂等性的一半）。

教学版没有网络、没有服务端状态，这一整块自然不需要。

</details>

<details>
<summary>四、reasoning 是一等公民</summary>

教学版把 `reasoning` 当一个普通字段，`parse_response` 抽出文本就完事。真 Codex 把推理当**可携带、可回放的状态**来对待：

- **`effort` 档位**：请求里 `reasoning: {effort: low|medium|high}` 控制模型「想多深」——想得越深越准、但越慢越贵。
- **reasoning summary**：响应回来的常是推理的**摘要**（给人看的版本），而非逐字的原始思维链。
- **加密 reasoning 内容**：有些推理内容是**加密**回传的——客户端原样存着、下回合再原样带回去，让模型「记得自己上次想到哪」，但客户端并不能明文读它。
- **纳入持久化**：推理会被写进 rollout（[s08](../s08_rollout/)），也会参与上下文压缩（[s07](../s07_context_compaction/)）的预算计算。

一句话：在 Responses 里，「模型想了什么」不是日志旁注，而是协议里**一等的、要被存储和回放的**数据。这正是 Codex 选 Responses 而非 Chat Completions 的核心理由之一。

</details>

## 运行

```bash
python s09_responses_api/code.py --demo   # 构造请求 + 解析响应 + 三方对比 + 重试/退避/错误分类
```

## 小结

- Codex 走 OpenAI Responses API：系统提示叫 `instructions`，对话是**扁平 input-item** 列表，工具是**扁平** `{type:"function",…}`，外加一档 `reasoning.effort`。
- 工具调用/结果是**独立 item**（`function_call` / `function_call_output`），而非 Anthropic 那样嵌在 message 里的内容块——这解释了前几章 `messages` 为何长那样。
- reasoning 是协议一等公民：请求里调力度、响应里单独成 item，于是「想了什么」能被流式、被回放、被纳入压缩（[s07](../s07_context_compaction/)）与 rollout（[s08](../s08_rollout/)）。
- 协议跟随模型：各用自家 API 与自家工具形状，和 [s03](../s03_apply_patch/) 里 apply_patch 跟随模型是同一个道理。
- **生产级**：模型调用是会抖的网络流——可重试错误走指数退避 + 抖动（避免重试风暴）、honor 服务端 Retry-After、封顶 `max_retries`、耗尽再做 WebSocket→HTTPS 传输回退；致命错误（鉴权）零重试；上下文超限是"先压缩再重试"而非单纯重发（见「生产级」一节）。
- 下一站 [s10 SQ/EQ 协议](../s10_sq_eq_protocol/)：Responses 流式吐出的这些 item/事件，harness 内部怎么用一套 **SQ（提交队列）/ EQ（事件队列）** 收发、把「模型推理 → 工具调用 → 审批 → 结果」串成有序的事件流。

## 思考

<div class="think">

1. Responses 把"工具调用/结果"做成独立 item，Anthropic 把它们做成 message 里的内容块。哪种更适合"流式地穿插多个工具调用"？为什么？
2. reasoning 作为协议一等公民，意味着模型的"思考过程"会回到上下文里。这对调试好在哪？对 token 成本和隐私又意味着什么？
3. 如果明天要给 [codexlib.py](../codexlib.py) 加一个 Anthropic 后端（让 Codex 的 harness 去跑 Claude），你得改哪几处？哪些属于"协议形状"差异、哪些属于"模型习惯"差异（回想 [s03](../s03_apply_patch/)）？
4. WebSocket + prewarm + 粘性路由都是为了什么？教学版省掉它们，是功能少了，还是只是慢一点、贵一点？
5. 重试用抖动来避免"重试风暴"。可如果故障是服务端**彻底挂了**（不是过载），指数退避+抖动只是让一群客户端"慢一点、但仍持续地"敲它。光靠客户端退避够吗？还需要什么？（提示：断路器 circuit breaker——连续失败到某个阈值就**直接快速失败一段时间**，根本不发请求。想想 [s14](../s14_guardian/) 的熔断器是不是同一个思想。）

</div>
