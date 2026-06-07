# s01: Agent Loop — One Loop Is All You Need

> 🌐 **English** · [中文版](README.md)

> *"The agent loop is the common foundation of every agent. Agency comes from the model, not from the loop."*

[learn-codex overview](../README.en.md) · **Agent loop** → [Tools and dispatch](../s02_tool_use/README.en.md)

---

## Get the idea straight first: why "one loop" already is an agent

You've probably heard that "agents are complicated." In reality, the core is unbelievably simple — it's just one loop. The hard part isn't the loop itself; it's grasping the three ideas behind it. Get these three straight and every later chapter will feel easy, because you'll have already caught hold of the trunk.

**Idea one: the model is smart, but it "can't move."**
A large model can understand what you say and can figure out that it "should run `ls` to see what's in the directory" — but the model itself **only emits text**. It has no hands; it can't touch your files, and it can't run commands. It's like a brilliant advisor who can only pass notes: the note says "go look in folder A," but the advisor can't step out of the room.

**Idea two: so where does the "ability to act" come from? From a small piece of code that connects "writing a note" to "actually doing it."**
You only have to do one thing: **read the advisor's note → actually execute it → hand the result back so it can see it → it writes the next note**, over and over. This "pass-the-note / execute / hand-back" cycle is the **agent loop**. Every time the model writes a "do something" note, we execute it on its behalf and feed the result back. That's all there is to it — no magic.

**Idea three (the most important): the intelligence belongs to the model; the loop is just a faithful gofer.**
The loop itself "understands" nothing. It makes no judgments; it's only responsible for "relaying messages and executing." It's the **model** that decides what to do next. So you'll witness a striking fact: from start to finish in this course, this loop barely needs **a single line changed** — what changes is only the things we add around it (how to edit files, how to keep it from causing trouble, how to help it remember things…).

These "things around it" together form a **vehicle**: the model is the driver, and the vehicle carries it anywhere to get work done. **This course teaches you to build that vehicle one block at a time.** And the first block is this loop.

## Problem

You ask the model: "See what's in this directory, then run the build."

The model can emit a command, but once it's done emitting, it stops. It won't execute on its own, nor can it see the result and keep reasoning. You have to manually copy its command into the terminal, run it, and paste the output back — on every round trip, you're playing that middle layer.

Automating that middle layer is the agent loop.

## Solution

One `while True`: if the model wants to use a tool, execute it and feed the result back; if the model doesn't use a tool, that means it has finished talking, so exit.

```
   user query
       │
       ▼
   ┌───────────────────────────────┐
   │  model.respond(messages,tools) │◀────────────┐
   └───────────────┬───────────────┘             │
                   │                              │
            有 tool_call ?                         │
            /          \                          │
          是            否 → 打印文本，结束          │
          │                                       │
   执行工具(shell) → function_call_output 回灌 ─────┘
```

## How it works

Look at [code.py](code.py), three steps:

**Step 1** — Make the user's question the first message. Codex uses the input-item shape of OpenAI's Responses API (a flat list of items):

```python
messages = [user_item(query)]   # {"type":"message","role":"user","content": query}
```

**Step 2** — Send it to the model along with the tool definitions, and get back a normalized result (text + tool calls):

```python
resp = model.respond(messages, tools=TOOLS, system=SYSTEM)
messages += resp.output_items   # 把模型本回合产出回灌进对话
```

**Step 3** — If there are no tool calls, finish; if there are, execute them, append the results back as standalone `function_call_output` items, and keep looping:

```python
if not resp.tool_calls:
    return
for tc in resp.tool_calls:
    output = HANDLERS[tc.name](**tc.arguments)
    messages.append(tool_output_item(tc.call_id, output))
```

There's only one tool: `shell` (run a command). In the real Codex, this tool is the shell/exec tool in `codex-rs/core` — the agent's workhorse is "running commands."

**Walk through it** — Replace the abstract "passing notes" with real data. This is exactly the round that `python s01_agent_loop/code.py --demo` runs. You say a single sentence, then watch how the `messages` list grows one item at a time:

1. Your question first becomes the **first item** in the list. It's not a special object; it's just an ordinary dictionary — the `type` field marks who said it:
   ```json
   {"type": "message", "role": "user", "content": "执行 `echo hello from codex` 并告诉我结果"}
   ```

2. Send this list (along with the tool roster) to the model. The model understands that "it wants me to run a command," so instead of replying with text, it replies with a **"do something" note** — a `function_call` item. Note that `arguments` is a JSON **string** (what the model spits out is text), and `call_id` is the receipt number for this call:
   ```json
   {"type": "function_call", "call_id": "mock_call_1", "name": "shell",
    "arguments": "{\"command\": \"echo hello from codex\"}"}
   ```
   The loop sees that `resp.tool_calls` is non-empty — that's the "continue" signal.

3. The loop **executes on the model's behalf**: it looks up `run_shell` by the name `shell`, actually runs `echo hello from codex`, and gets the output `hello from codex`. Then it wraps the result as a third item and appends it back to the list. The key is using the **same `call_id`** to match the result to that earlier note:
   ```json
   {"type": "function_call_output", "call_id": "mock_call_1", "output": "hello from codex"}
   ```

4. Ask the model again with this longer history. This time it sees the command has already run and the result is right in front of it, with nothing left to do — so it replies with plain text and no `function_call`. `resp.tool_calls` is empty, and the loop **exits** right there.

See it? Across the whole round, the loop made **not a single judgment** itself — "should I run a command" and "is it done" were both decided by the model through "whether or not it sends a `function_call`." The loop is just a faithful gofer: look up the table, execute, and paste the result back by `call_id`. This is the living template for idea three.

## Production-grade: the loop can't spin forever — cap it + make it interruptible

The teaching version's `while True` has two fatal gaps: it **can't stop**, and it **can't be reined in**. A production-grade agent loop must add two guardrails (this chapter's [code.py](code.py) already adds them, demonstrated at the end of `--demo`).

### 1. Step cap: prevent runaway tool-call loops

The model can fall into an infinite loop — repeatedly calling the same failing tool, or bouncing back and forth between two tools. An unbounded `while True` will **keep burning money and time** until it hits the token limit or you manually kill the process. The production-grade approach is to give the round a **step/budget cap** (`for step in range(max_steps)`), and force a wrap-up when it hits the ceiling:

```
 (a) 步数封顶 max_steps=3：卡住的模型被截停 ——
> shell {'command': 'echo still going'}   （×3）
[guard] 触及 max_steps=3 上限，强制收尾（防止失控的工具调用循环）
```

### 2. Cooperative cancellation: when the user says stop, it has to actually stop

A round that's halfway through, when the user presses Ctrl-C / clicks "stop," must **cleanly stop right there** — not finish running the remaining tool calls before paying attention to you. The real Codex makes "stop" a first-class operation, `Op::Interrupt` ([`protocol.rs:450`](../../codex/codex-rs/protocol/src/protocol.rs)); the round checks it between every step and, on a hit, emits `Interrupted`. This chapter renders it for teaching with a `cancelled()` callback — at each step it asks "should I stop now?":

```
 (b) 协作式取消：用户在第 3 步中断 ——
> shell {'command': 'echo still going'}   （×2）
[interrupted] 用户中断，干净退出本回合
```

> Why "cooperative" rather than "force-kill": you can't hard-cut a tool that's running halfway — that would leave a half-written file or a half-committed transaction. The right approach is to check the cancellation signal at a **safe checkpoint** (between steps), letting the step currently running **wrap up first** before exiting. This is also why `Op::Interrupt` sends a signal rather than a `kill -9`.

## 🆚 How it differs from Claude Code

This chapter is nearly identical on both sides — **and that's precisely the point**: the agent loop is the shared foundation, agency lives in the model, not in the loop. But three early signs already reveal where they diverge:

| | Claude Code | Codex | Why |
|---|---|---|---|
| Stop signal | `stop_reason == "tool_use"` (Anthropic Messages API) | Consumes the Responses API event stream; architecturally goes a step further by splitting "submissions" and "events" into two queues | The two have different wire protocols (see [s09]); Codex's queueing is for multiple frontends + streaming approvals ([s11]) |
| Dangerous commands | s01's `run_bash` has a hardcoded blacklist (`rm -rf /`, `sudo`…) | **No blacklist** | Codex doesn't trust blacklists (command variants are infinite); its answer is approvals ([s04]) + a kernel-level sandbox ([s05]) |
| The model's "thinking" | `thinking` blocks (with a `signature`), inside the assistant message's content; the tool loop must **carry them back verbatim** | `reasoning` items (with optional `encrypted_content`), a **standalone item** in the flat item list; carried across rounds via `previous_response_id` | Each follows the basic shape of its protocol (**blocks** vs **items**); the protections differ too — one **signs** to prevent tampering, the other **encrypts** to prevent leakage → see "Deep dive four" below |

> In one line: **Claude Code "blocks" at the application layer; Codex "closes the door" at the kernel layer.** This through-line runs through every later chapter.

[s04]: ../s04_approval/README.en.md
[s05]: ../s05_sandbox/README.en.md
[s09]: ../s09_responses_api/README.en.md
[s11]: ../s11_frontends/README.en.md
## Deep dive: teaching version vs the real Codex source

The teaching version's `run_turn` is a synchronous `while` of about ten lines. The real Codex's turn engine lives in [`core/src/session/turn.rs`](../../codex/codex-rs/core/src/session/turn.rs); it's asynchronous, wrapped by the SQ/EQ protocol, and has to handle streaming and multiple frontends. Below we unpack a few differences — *the core loop is the same one; everything extra is protection and engineering*.

<details>
<summary>1. Synchronous while vs asynchronous Session</summary>

The teaching version directly calls `model.respond` in a `while True`. The real Codex's round runs inside the tokio async runtime: the `Session` (`core/src/session/`) takes `Op::UserInput` from the **submission queue**, and `turn.rs` orchestrates the whole round — injecting context (AGENTS.md, skills), streaming the call via `ModelClient::stream_responses`, dispatching tools through `ToolRouter::dispatch_any`, then sending `EventMsg`s back to the frontend. "Continue or stop" isn't decided by a single `return`, but driven by the **event stream**.

</details>

<details>
<summary>2. stop_reason isn't the only signal</summary>

The teaching version decides whether to continue based on whether `resp.tool_calls` is empty. What the real Codex consumes is the **streaming items** of the Responses API: a `function_call` item may start being processed as it arrives; the round's progress relies on incrementally parsing the output items, rather than looking solely at a single `stop_reason`. (This parallels how CC in learn-claude-code uses `needsFollowUp` rather than `stop_reason`.)

</details>

<details>
<summary>3. A lot more happens in a round than just "calling the model"</summary>

| What a real round also does | Teaching version s01 | Where it's added |
|---|---|---|
| Inject AGENTS.md project memory | omitted | s06 |
| Command approval | omitted | s04 |
| Kernel sandbox | omitted | s05 |
| Record the rollout (resumable) | omitted | s08 |
| Context compaction | omitted | s07 |
| Broadcast events to multiple frontends | omitted | s11 |

s01 strips all of this away and keeps only the barest loop — they grow back chapter by chapter later.

</details>

<details>
<summary>4. Where the model's "thinking" went: reasoning items (Codex) vs thinking blocks (CC)</summary>

This chapter's `model.respond` takes only the **text** and **tool calls** the model returns — but a reasoning model also spits out its "thinking process." Where this goes, and how it's carried over the hurdle of "tool calls" into the next round, is handled completely differently by the two, and is yet another place where the wire-protocol difference lands.

**Codex (OpenAI Responses API)**: thinking is a kind of **standalone item** — `ResponseItem::Reasoning { id, summary, content, encrypted_content }` ([`protocol/src/models.rs:772`](../../codex/codex-rs/protocol/src/models.rs)). It's **on a par with** `message` / `function_call`, sitting right there in that flat item list. In the request, Codex sets `include = ["reasoning.encrypted_content"]` ([`core/src/client.rs:768`](../../codex/codex-rs/core/src/client.rs)) so the server returns the reasoning **encrypted**, then relies on the cached Responses connection + `previous_response_id` (the comment at the top of `client.rs`) to **carry it across rounds** — the model can keep thinking along its previous train of thought through a string of tool calls, while you never see the raw CoT. (For simplicity, the teaching version's `codexlib.py` discards the reasoning item; the real Codex keeps it and sends it back.)

**Claude Code (Anthropic Messages API)**: thinking is a **`thinking` block inside the assistant message's content array** (with a `signature`; there's also `redacted_thinking`), alongside the `text` / `tool_use` blocks. In the tool loop, you must **carry these signed thinking blocks back verbatim** for the model to keep thinking; Anthropic **verifies the signature** to prevent tampering. *(CC-side details are paraphrased from Anthropic docs / learn-claude-code; being closed-source, they can't be independently verified in this repo — lower certainty than the Codex side.)*

**Why are they different?**

1. **Follow the basic shape of the protocol**: Anthropic is "content blocks inside a message," so thinking is naturally a **block**; Responses is "a flat item list," so thinking is naturally a **standalone item**. Same root as the first row of the 🆚 table above — shape decides everything.
2. **Different protections**: Anthropic **signs** the thinking (to prevent tampering on the way back), Codex **encrypts** it (to prevent leaking the raw CoT). Both protect reasoning — one takes the integrity route, the other the confidentiality route.
3. **Stateless replay vs stateful reference**: the Messages API is stateless, so each round **resends** the signed thinking block; the Responses API can use `previous_response_id` + server-side `store` to **reference** the previous segment, without resending every time.

In one line: **both have to carry the "half-formed train of thought" safely over the hurdle of a tool call — Anthropic relies on "sign + replay the content block," Codex relies on "encrypt + reference the item."** For the full picture of the Responses API, see [s09].

</details>

## Run

```bash
python s01_agent_loop/code.py --demo   # 跑一轮就退出（mock，无需 key）
python s01_agent_loop/code.py          # 交互模式
```

The default is `backend=mock`, runnable offline. To connect a real model, set `OPENAI_API_KEY` in the root `.env` (see [.env.example](../.env.example)).

## Recap

- One loop + one tool = one agent.
- Codex represents the conversation as a Responses API input-item list (`message` / `function_call` / `function_call_output`), which differs from Claude's `tool_use`/`tool_result` blocks; the model's reasoning then appears as a standalone `reasoning` item (see Deep dive four).
- **Production-grade**: replace `while True` with a loop bounded by `max_steps` (against runaways), and check the cooperative-cancellation signal at each step (the real Codex's `Op::Interrupt`, so "stop" really stops) — the loop's shape doesn't change, but it can no longer spin forever and can be reined in (see the "Production-grade" section).
- Next stop, [s02](../s02_tool_use/README.en.md): add a few more tools, and you'll see with your own eyes that **the loop doesn't need a single line changed**.

## Think it over

<div class="think">

1. If the model starts streaming out a `function_call` before it has "thought things through," can your loop still just look at "is there a tool_call"? How would you change it?
2. The same `run_turn` has to feed both the TUI in a terminal and `codex exec` in CI. How would you peel apart "what is produced" from "how it's displayed"? (s11 gives one answer, but can you come up with your own first?)
3. The teaching version has no dangerous-command interception at all. Why is the "blacklist" a dead end? Codex replaces it with approvals + a sandbox — what price does that pay?
4. Every round, the loop resends the entire history to the model. What happens to this when the conversation gets long? At which round would you start to worry? (Take this question with you to s07.)

</div>
