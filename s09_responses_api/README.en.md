# s09: Responses API — Codex's wire protocol

> 🌐 **English** · [中文版](README.md)

> *"Not Chat Completions, and not Anthropic Messages either."*

[learn-codex overview](../README.en.md) · [s08 Rollout resume](../s08_rollout/README.en.md) → **s09** → [s10 SQ/EQ protocol](../s10_sq_eq_protocol/README.en.md)

---

## Get the idea straight first: the model can't move, all it does is "spit out entries in a format" — once you read that order form, nothing is mysterious anymore

[s01](../s01_agent_loop/README.en.md) made one point: the model is very smart, but it **can't move** — all it can do is output text. This chapter drills one level deeper into that sentence — since the model can only "output," what exactly does it **output**? And how do we **hand our words to it**? Once you see this "telephone line" clearly, the puzzlement from the earlier chapters — "why does `messages` look like this, why is a tool call a separate item" — vanishes instantly. Three layers of reasoning.

**Reason one: between you and the model runs a network cable, and both ends must first agree on "how words get filled into the order form."**
The model isn't inside your program; it runs on OpenAI's servers, and the two of you shout across the network. But the network only carries bytes — it doesn't know concepts like "system prompt," "conversation," or "tools" — so both ends must **agree on an order form in advance**: which field holds the identity setup, which field holds the conversation, which field holds the tool list, what comes first and what comes second. This set of agreements is called the **wire protocol**. An analogy: even for "ordering a coffee," McDonald's order format differs from Starbucks's — whichever you walk into, you fill out their form. There are three common forms on the market — Anthropic's Messages (what Claude uses), OpenAI's older Chat Completions, and OpenAI's newer Responses; they express roughly the same things, but **the field names and layout each differ**. Codex chose Responses. Understanding this chapter is nothing more than laying out "the form sent out" and "the form received back" and reading every cell aloud.

**Reason two: the model's "action" is entirely an illusion — from start to finish it merely filled in one more kind of entry on the form.**
This is the point most easily mythologized, and the one most worth puncturing. When you see an agent "run a command," it looks as if the model really reached out and did the work — but in fact the model **did nothing**. All it did was fill in one more item on the reply form, with the type written as `function_call`, the name written as `shell`, and the arguments written as `{"command":"ls"}`. This item and "a sentence of human speech it said" are **two side-by-side entries**, with no rank between them — both are just "words filled on the form." What actually executes the command and fills the result back into the form is that chunk of harness code of yours (precisely the loop from [s01](../s01_agent_loop/README.en.md)). So Responses splits the whole conversation into a string of **flat entries**: a user message is one item, a tool call is one item, a tool result is yet another item, laid out side by side — and this is exactly the shape of the `messages` list from the earlier chapters. Once you accept that "the model only spits out entries, and someone else does the moving," "why must a tool call be a separate item" is no longer a question but a matter of course.

**Reason three (the most crucial): the protocol grew out of the model, not the other way around — which is why even "what it thought" gets invited onto the form.**
Why did Codex pick Responses specifically, rather than the more established Chat Completions? Because its model's way of working is "**think a stretch first, then decide to act**," and the protocol has to be able to **hold that way of working**. So Responses did something Chat Completions didn't: it treats the model's **reasoning** as first-class content on the form too — the request can use a knob to dial "how deep to think" (lighter / medium / deeper), and in the reply that stretch of thinking comes back as a **separate item**, side by side with "human speech" and "tool call." In other words, "what the model thought" is no longer a marginal note in the logs but proper data in the protocol that **gets transmitted, gets archived, and can be carried back verbatim next time**. This is "**the protocol follows the model**": however the model thinks and acts, that's how the form grows — rather than the reverse, taking an old form and cutting feet to fit the shoe. (This is the same principle as "tool shape follows the model" in [s03](../s03_apply_patch/README.en.md), showing up a second time.)

Tie the three together: across a network cable you must agree on a form → the model's "action" is merely one more kind of entry on the form → and the form's shape is sculpted by the model's think-then-act way of working. Once you read this form, this chapter clicks; the field comparisons that follow are just landing these three points into one concrete cell after another.

## Problem

How exactly does core communicate with the model? The three APIs have differently shaped forms: Anthropic's Messages, OpenAI's Chat Completions, and OpenAI's **Responses**. Codex chose Responses — why? Because it natively supports **reasoning**, **item streaming**, and server-side session state, which fits perfectly with the "reason first, act second" way of working of the Codex model.

## Solution

Use the OpenAI **Responses API**. It differs from Chat Completions in a few key places:

- the system prompt is called `instructions` (not `system`);
- the input `input` is a string of **flat items**: `message` / `function_call` / `function_call_output` interleaved (this is exactly the shape of the `messages` list from the earlier chapters);
- tools are **flat** `{"type":"function","name","description","parameters"}`;
- there's an extra `reasoning: {effort}` tier — controlling reasoning intensity, which Chat Completions lacks.

Below, a minimal example walks "how the request is assembled, how the response is taken apart" step by step.

## How it works

Look at [code.py](code.py) (this chapter is pure explanation, offline), three functions:

- `build_request(...)` packs the conversation + tools into a Responses request (a Python dict, which once serialized is the JSON sent out);
- `parse_response(resp)` pulls `reasoning` / `message`(text) / `function_call`(tool call) separately out of the response's `output` items;
- `protocol_comparison()` prints the same `shell` tool written in all three protocols side by side.

This is exactly what the `openai` backend of [codexlib.py](../codexlib.py) really does; the real source is in `../../codex/codex-rs/core/src/client.rs` (`ModelClient::stream_responses`) and the `codex-api` crate.

**Walk through it** — follow `--demo` to see what the data each way looks like, and why the key fields are named the way they are:

1. **Build the request.** Given one user message "list the files" and one `shell` tool, `build_request` packs out this blob of JSON (this is exactly what gets sent to OpenAI):

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

   Field by field, see **why it's like this**: the system prompt goes in `instructions` (not `system`); the conversation goes in `input`, a string of **flat items** (here just one user message; tool calls/results will also lay flat into the same array later); tools are **flat** `{type:"function", name, parameters}`; `reasoning.effort` tells the model "think with medium intensity" — a tier Chat Completions simply doesn't have.

2. **The model comes back with a response.** The demo uses a hardcoded response (containing reasoning + one tool call). The core of the response is `output` — a string of items:

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

   *Why* reasoning and the tool call are **two side-by-side items**: Responses splits "what it thought" and "what it decided to do" into independent entries, so when streaming it can push the reasoning to you first and then push the tool call — rather than stuffing them inside one message. Note that `arguments` is a **JSON string** (not an object), and needs another `json.loads`.

3. **Parse the response.** `parse_response` sorts by `type`, extracting the `output` above into three things:

   ```
   reasoning: ['用户想看文件，应当调用 shell 跑 ls。']
   text:      ''
   tool_call: shell({'command': 'ls -la'})  call_id=call_abc
   ```

   Here `text` is empty — because in this turn the model didn't say any human speech, it **directly decided to call a tool**. The `call_id` is crucial: once `shell` finishes, the tool result must lay flat back into `input` as a `function_call_output` carrying the **same `call_id`**, so the model knows "this is the result of that earlier call." This is what connects this chapter to the agent loop of the earlier chapters.

4. **The three protocols side by side.** At the end the demo prints the same `shell` tool defined in all three's notation, so you can see the difference at a glance:

   ```
   Anthropic（Claude）:        {"name":"shell", …, "input_schema":{…}}
   OpenAI Responses（Codex）:  {"type":"function","name":"shell", …, "parameters":{…}}
   OpenAI Chat Completions:    {"type":"function","function":{"name":"shell", …,"parameters":{…}}}
   ```

   Anthropic uses `input_schema` and the tool is a top-level object; Responses uses `parameters` and is flat; the older Chat Completions **nests** the tool inside another `function` layer. The same thing, three different forms — and that's the whole meaning of "wire protocol."

## Production-grade: the network jitters, the model refuses — how to do retry, backoff, and fallback

"How the request is assembled, how the response is taken apart" is now clear. But the truth is: `stream_responses` is not one clean function call but a **jittery long network stream** — it disconnects midway, gets rate-limited (429), runs into server-side 5xx. A toy throws an exception and crashes here; a production-ready harness treats "failure" as a **recoverable, routine path**. This section explains it to a level that holds up under scrutiny, corresponding to [`responses_retry.rs`](../../codex/codex-rs/core/src/responses_retry.rs) + [`util.rs:85`](../../codex/codex-rs/core/src/util.rs).

### One, classify first: which errors should be retried, and which are hopeless to retry

The first principle of retrying isn't "how to back off" but "**should it be retried at all**" — blindly resending a 401 just wastes time.

| Category | Example | What to do |
|---|---|---|
| **Retryable (transient)** | stream disconnect, 429 rate limit, 5xx server error | back off and resend — the problem will most likely fix itself |
| **Fatal (non-transient)** | 401 auth failure, 400 invalid request | fail immediately — retrying ten thousand times is still wrong |

In the real source this shows up as the distinction between `CodexErr::Stream(_, requested_delay)` (retryable, possibly carrying a server-requested delay) and the other fatal variants. This chapter's [code.py](code.py) makes it pedagogical with two classes, `TransientError` / `FatalError`.

### Two, exponential backoff + jitter: why you can't "retry at a fixed interval"

How long should you wait before resending a retryable error? The formula of the real Codex `backoff()` (`util.rs:85`):

```
delay = INITIAL_DELAY × FACTOR^(attempt-1) × random(0.9, 1.1)
```

- **Exponential** (200 → 400 → 800ms…): the server may be overloaded, and the more you retry the more you should give it room to breathe, rather than hammering it harder.
- **Jitter ±10%**: the easiest stroke to miss, yet the most crucial. If 1000 clients are interrupted by the same failure and all retry at the **exact same** interval, they'll all surge back in **lockstep** at the same millisecond, knocking down the just-recovering server a second time (thundering herd / retry storm). A bit of random jitter **spreads** this wave of retries out across the timeline.

### Three, listen to the server: honor Retry-After

When the server explicitly says in a 429 "wait 1500ms before coming back," then **don't blindly guess with the backoff formula** — listen to it. Real source: if `CodexErr::Stream(_, requested_delay)` carries a `requested_delay`, then `unwrap_or_else(|| backoff(...))` — use what the server gave if there is one, and only back off if there isn't.

### Four, capping + transport fallback: retries aren't infinite

- **Two caps**: `request_max_retries` (the whole request) and `stream_max_retries` (mid-stream reconnects) are configured separately ([`model-provider-info`](../../codex/codex-rs/model-provider-info/src/lib.rs)), and both have hard ceilings, to prevent configuring a "retry forever" dead loop.
- **Transport fallback**: after retries are exhausted on **WebSocket**, the real Codex doesn't just give up but **downgrades to HTTPS+SSE, zeros out the retry count, and goes another round** (`responses_retry.rs`) — one road blocked, take another road.
- **Tell the user**: each reconnect sends the frontend a line `Reconnecting... {n}/{max}`, so the user isn't left staring blankly at a seemingly frozen screen (it even deliberately hides the noise of the first websocket reconnect).

### Walk through it

The `--demo` segment ④ runs this for you (random seed fixed, output reproducible):

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

Look at the 193/372ms in (a): that's just 200×2⁰ and 200×2¹ each multiplied by a bit of jitter; (b) directly uses the server's 1500ms; (c) judges death at a glance, zero retries; (d) lets go only after hitting the cap.

### There's another special kind of "failure": context blowout ≠ retry

Not every failure is solved by retrying. When you hit `ContextWindowExceeded` (context over the limit), resending verbatim will only overflow again — the correct reaction is to **compact the history first, then retry** (reactive compaction, see [s07](../s07_context_compaction/README.en.md)). This points to an iron law: **"recovery" is not "retry"** — you must pick the right strategy according to the nature of the error.

> In one sentence: a production-grade model call has half its code in "how to send the request, parse the response," and the other half in "**how to gracefully survive when it fails**." The latter is what separates a toy from a product.

## 🆚 How it differs from Claude Code

| | Claude Code (Anthropic Messages) | Codex (OpenAI Responses) |
|---|---|---|
| System prompt | `system` parameter | `instructions` parameter |
| Tool definition | `{name, description, input_schema}` | `{type:"function", name, description, parameters}` |
| Tool call/result | content blocks `tool_use` / `tool_result` (nested inside the message) | independent items `function_call` / `function_call_output` |
| Reasoning | extended thinking block | `reasoning.effort` tier + reasoning item |
| Conversation representation | `messages[].content` block array | flat input-item list |

**Why?** The most direct reason is that **each uses its own model and its own API** — the shape of tools/conversation has to fit its respective backend. One layer deeper: Responses's "item stream + reasoning tier + server-side state" lets Codex explicitly bring the model's **reasoning process** into the protocol, and support streaming the interleaving of tool calls and approvals ([s10 SQ/EQ protocol](../s10_sq_eq_protocol/README.en.md)). This isn't about who's better, but yet another piece of evidence for "tools and protocol follow the model" (echoing the same logic of apply_patch in [s03](../s03_apply_patch/README.en.md)).

## Deep dive: the teaching version vs. the real Codex source

The real client is in [`core/src/client.rs`](../../codex/codex-rs/core/src/client.rs) (~89K) + the `codex-api` crate. The teaching version only shows the shape of "build request / parse response."

<details>
<summary>One, WebSocket upgrade + SSE fallback + prewarm</summary>

The teaching version only draws "build a dict → parse a dict," as if one round trip finishes it. The real Codex is far more particular at the **connection** layer:

- **Prefer WebSocket**: use `ResponsesWebsocketClient` to send multiple turns over one long connection, saving the per-turn overhead of re-handshaking (TLS + HTTP).
- **Fall back to HTTP + SSE on failure**: when the environment doesn't support WebSocket (certain proxies/gateways), it automatically falls back to the more universal path of "HTTP POST + SSE streaming response."
- **prewarm**: before you actually send the first request, it quietly sends a request that **generates no content** first, "warming up" the connection, routing, and server-side session state — so after you press Enter, the **time-to-first-byte** is noticeably shorter.

*Why the teaching version can skip all of this*: none of these change "what the request/response looks like," they only affect **how fast, how stable**. The teaching purpose is to explain the protocol shape, so the entire layer of connection optimization is omitted.

</details>

<details>
<summary>Two, streaming SSE incremental parsing</summary>

The teaching version's demo uses one **hardcoded complete response** parsed all at once. The real Codex faces a **stream**: the server pushes the response over **block by block** via SSE (Server-Sent Events), each block an event with a `type`, for example:

```
event: response.output_item.added        ← 新增了一个 item（如一个 function_call 的壳）
event: response.reasoning_summary.delta  ← 推理摘要又来了一小段文字
event: response.output_text.delta        ← 助手正文又来了一小段
event: response.completed                ← 这一回合到此结束
```

The real source uses `eventsource_stream` to incrementally parse these events, assembling the output items as they arrive. This is precisely the source of the command output `OutputDelta` and streaming reasoning — they converge into an event stream, fed to the **SQ/EQ** layer of [s10](../s10_sq_eq_protocol/README.en.md). *Why stream*: the user doesn't have to wait for the model to hold in a whole stretch, they can watch as it thinks; and tool calls can be captured by the frontend the instant they appear.

</details>

<details>
<summary>Three, sticky routing: how to "continue on the same machine as last time" when retrying</summary>

Retry itself is covered in the "Production-grade" section above; here we add its partner — **sticky routing**: the request carries an `x-codex-turn-state` header, making multiple requests of the **same turn** land on the **same backend instance**. *Why it matters*: Responses has **server-side session state** — the context of the earlier reasoning/tool call is stored on some machine, and if a retry gets routed to a different instance, the state won't match. Sticky routing guarantees "continue on the same machine as last time" — which is precisely the precondition for retries to proceed **safely** (half of idempotency).

The teaching version has no network and no server-side state, so this whole block naturally isn't needed.

</details>

<details>
<summary>Four, reasoning is a first-class citizen</summary>

The teaching version treats `reasoning` as an ordinary field, and `parse_response` extracting the text is the end of it. The real Codex treats reasoning as **carryable, replayable state**:

- **`effort` tier**: `reasoning: {effort: low|medium|high}` in the request controls "how deep" the model thinks — the deeper it thinks the more accurate, but the slower and more expensive.
- **reasoning summary**: what comes back in the response is often a **summary** of the reasoning (the version for humans to see), rather than the verbatim raw chain of thought.
- **encrypted reasoning content**: some reasoning content is returned **encrypted** — the client stores it verbatim and carries it back verbatim next turn, letting the model "remember where it got to last time," but the client cannot read it in plaintext.
- **brought into persistence**: reasoning gets written into the rollout ([s08](../s08_rollout/README.en.md)), and also participates in the budget calculation of context compaction ([s07](../s07_context_compaction/README.en.md)).

In one sentence: in Responses, "what the model thought" is not a marginal note in the logs but **first-class data in the protocol that has to be stored and replayed**. This is precisely one of the core reasons Codex chose Responses over Chat Completions.

</details>

## Run

```bash
python s09_responses_api/code.py --demo   # 构造请求 + 解析响应 + 三方对比 + 重试/退避/错误分类
```

## Recap

- Codex goes with the OpenAI Responses API: the system prompt is called `instructions`, the conversation is a **flat input-item** list, tools are **flat** `{type:"function",…}`, plus a `reasoning.effort` tier.
- Tool calls/results are **independent items** (`function_call` / `function_call_output`), rather than content blocks nested inside the message the way Anthropic does — which explains why `messages` looked the way it did in the earlier chapters.
- reasoning is a first-class citizen of the protocol: dial the intensity in the request, and it forms a separate item in the response, so "what it thought" can be streamed, replayed, and brought into compaction ([s07](../s07_context_compaction/README.en.md)) and rollout ([s08](../s08_rollout/README.en.md)).
- The protocol follows the model: each uses its own API and its own tool shape, the same principle as apply_patch following the model in [s03](../s03_apply_patch/README.en.md).
- **Production-grade**: a model call is a jittery network stream — retryable errors go through exponential backoff + jitter (avoiding the retry storm), honor the server's Retry-After, cap `max_retries`, and once exhausted do a WebSocket→HTTPS transport fallback; fatal errors (auth) get zero retries; context-over-limit is "compact first, then retry" rather than a plain resend (see the "Production-grade" section).
- Next stop [s10 SQ/EQ protocol](../s10_sq_eq_protocol/README.en.md): for these items/events streamed out by Responses, how the harness internally uses a set of **SQ (submission queue) / EQ (event queue)** to send and receive, stringing "model reasoning → tool call → approval → result" into an ordered event stream.

## Think it over

<div class="think">

1. Responses makes "tool call/result" into independent items, while Anthropic makes them into content blocks inside a message. Which is better suited for "streaming the interleaving of multiple tool calls"? Why?
2. reasoning being a first-class citizen of the protocol means the model's "thinking process" comes back into the context. What's good about this for debugging? And what does it mean for token cost and privacy?
3. If tomorrow you had to add an Anthropic backend to [codexlib.py](../codexlib.py) (letting Codex's harness run Claude), which places would you have to change? Which belong to "protocol shape" differences and which to "model habit" differences (recall [s03](../s03_apply_patch/README.en.md))?
4. What are WebSocket + prewarm + sticky routing all for? When the teaching version omits them, is functionality lost, or is it just slower and more expensive?
5. Retry uses jitter to avoid the "retry storm." But if the failure is that the server is **completely down** (not overloaded), exponential backoff + jitter just makes a crowd of clients "slower, but still persistently" hammer it. Is client-side backoff alone enough? What else is needed? (Hint: a circuit breaker — once consecutive failures hit some threshold, it **just fails fast for a while**, not sending requests at all. Consider whether the circuit breaker of [s14](../s14_guardian/README.en.md) is the same idea.)

</div>
