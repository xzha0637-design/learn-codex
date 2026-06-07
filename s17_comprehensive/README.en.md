# s17: Comprehensive — Assembling a mini Codex

> 🌐 **English** · [中文版](README.md)

> *"Build the vehicle well, and the agent will do the rest."*

[learn-codex overview](../README.en.md) · [s16 config](../s16_config/README.en.md) → **s17 Comprehensive (single-agent · the finish line)** → [s18 Multi-agent (advanced)](../s18_multiagent/README.en.md)

---

## Get the idea straight first: the whole course is really just "one pipeline"

Now that we've reached the final chapter, look back: the previous 16 chapters seemed to each cover their own thing — the loop, apply_patch, the sandbox, approval, AGENTS.md, event broadcasting… like 16 unrelated topics. But in fact, from start to finish, they were all building **the same thing**. Once you grasp the three ideas below, you'll not only understand this chapter, you'll suddenly understand the structure of the entire course.

**Idea one: for a user request to become a real action, it must pass through several gates in sequence — and that chain is the essential shape of an agent.**
You say "run `echo hi`", and it won't — and shouldn't — just run it directly. It has to **pass, in sequence, through**: inject project memory (so the model thinks with this project's rules in mind) → call the model to get "what it wants to do" → go through the approval gate (is this command allowed to run) → land in the sandbox to execute safely → feed the result back, continue the loop. Line these steps up one after another, and you get a **pipeline** — like a factory conveyor belt, where the raw material (your words) travels along the belt, gets processed once at each station, and finally comes out as a finished product (an action that has been approved, sandboxed, and recorded). The mechanism polished in each earlier chapter is, in fact, **one gate** on this conveyor belt (a gate: check first, then let through — the most typical being the approval gate, like an airport security checkpoint, where a command must clear security and then pass through the sandbox isolation door before it's allowed to actually execute).

**Idea two (the most crucial): the model appears at only one step in the middle of this pipeline; every other gate is the harness.**
This is the fact the whole course most wants you to see. Stare at that conveyor belt and count: the model shows its face only at the "what to do" step, spits out a tool call, and exits the stage. It **does not decide** whether this command is allowed to run (approval), **is not responsible** for executing it safely (sandbox), **does not worry** about how it's recorded and broadcast (events), **and doesn't care** where the project's rules come from (memory injection). Those are all the **harness's** job. So the line that runs through the entire course — "Agency comes from the model; you build the vehicle" — becomes visible to the naked eye in this chapter: **the model provides the intelligence of "what to do"; you, as the harness engineer, build the entire world it inhabits and acts within.**

**Idea three: wire the small parts together with the right interfaces, and a runnable mini Codex grows out of it — the complexity is in the engineering, the skeleton can be tiny.**
Each of the previous 16 chapters built a **component** — each, on its own, a small runnable piece of code, like a single Lego brick. This chapter builds no new component; instead it **assembles** them in the right order with the right interfaces — letting data flow from one component's outlet into the next one's inlet — and then runs an **integration check** one more time (`--demo` lets a single request actually travel from start to finish, confirming that these parts truly cooperate when put together, not merely that each runs on its own). The remarkable thing is: what you assemble this way has the **same skeleton** as the industrial-grade "final assembly shop" `turn.rs` of the real Codex — the only difference being that the real version makes each part robust at the scale of thousands of lines, while the core idea fits in fifty.

To set the tone in one sentence: **the whole course is about building one pipeline — the previous 16 chapters build the parts, and this chapter assembles them and verifies they work together.** Understand "how a request passes through gate after gate along the pipeline, with the model occupying only one step," and you've got hold of the skeleton of any agent harness.

## Problem

In the previous 16 chapters, each chapter polished one part: the loop, apply_patch, the sandbox, approval, SQ/EQ, Responses, AGENTS.md… But a real agent product is not a pile of parts — it's their **assembly**.

How do the parts mesh? A single user request — how many gates must it pass through, in sequence, to become a real action that is "approved, sandboxed, recorded"? This chapter assembles the parts and runs a complete pipeline end to end.

## Solution

A single request pipeline that runs through everything — and this is precisely the essential shape of Codex (and any agent harness):

```
  用户输入
     │
     ▼  注入项目记忆 (s06 AGENTS.md)
  build_system()
     │
     ▼  调模型，拿工具调用 (s09 Responses 形状 / s01 循环)
  model.respond()
     │
     ▼  每个工具调用，先过闸门
  审批 decide() ──拒绝──▶ 不执行，回灌 "(denied)"
     │ 放行
     ▼  命令落进内核沙箱 (s05) / 文件改动走 apply_patch (s03)
  run_sandboxed() / apply_patch()
     │
     ▼  全程广播事件 (s10)
  emit(...) ──▶ 前端渲染
     │
     ▼  结果回灌，继续循环 (s01)
```

## How it works

Look at [code.py](code.py) — it introduces no new mechanism, it just brings the earlier slimmed-down pieces over and wires them into a line:

- `build_system()` stitches the upward-discovered AGENTS.md into the system prompt (s06);
- tools no longer execute directly: `tool_shell` first passes the `decide()` approval gate (s04), and once let through hands off to `run_sandboxed()` (s05); `tool_apply_patch` goes through patch application (s03);
- `emit()` broadcasts an event at every step (s10);
- `run_turn()` is that familiar s01 loop, just with these extra gates added in the middle.

The mantra is a single line: **tool = approval gate → (sandbox / apply_patch)**. The model handles "what to do"; the harness handles "whether it can be done, and how to do it safely."

The real Codex's "final assembly shop" is in [`core/src/session/turn.rs`](../../codex/codex-rs/core/src/session/turn.rs) — it orchestrates these same gates, at industrial grade, within an async turn.

**Walk through it**: follow step ① of `--demo` — the user says "run `echo mini-codex online`" — and watch how a single request passes through gate after gate along the pipeline, what the data looks like at each step, and why each step has to exist.

Step 1 **build_system()** (inject project memory). Right at the start, `run_turn` stitches the AGENTS.md discovered by searching upward from the current directory into the system prompt. If a parent level has an AGENTS.md, the system prompt looks roughly like this (**why**: so the model thinks with "this project's rules" in mind, rather than starting from scratch every time):

```text
You are a mini-Codex coding agent at /path/to/repo. Act, don't explain.

<project_instructions>
--- /path/to/repo/AGENTS.md ---
（这里是项目自己的约定，比如"用 ruff 格式化""测试放 tests/"）
</project_instructions>
```

Step 2 **model.respond(...)** (call the model, get a tool call). Send `[user_item("执行 …")]` along with the tool list and system prompt to the model; the model does not execute directly, but instead **returns a tool call** (the shape of s09 Responses) — roughly meaning "I want to call shell, with this command as the argument":

```json
{"type": "function_call", "name": "shell",
 "call_id": "call_1", "arguments": {"command": "echo mini-codex online"}}
```

**Why**: the model only handles "what to do"; the decision is deliberately not handed to it yet — which is exactly the reason the next gate exists.

Step 3 **decide() approval gate** (check first, then let through). After `tool_shell` gets the command, it **does not run it immediately**; it first shouts `emit("approval", ...)` to broadcast "there's a command pending approval," then hands it to `decide(command, policy)` for a ruling. The current `POLICY = "on-request"`, and `echo` is on the safe list `SAFE` and contains no dangerous fragments, so the verdict is `approve` (**why**: safe, read-only-style commands are auto-approved to spare the interruption; something like `rm -rf` is what actually stops to ask):

```text
[event] ❓ approval   shell: echo mini-codex online
decide("echo mini-codex online", "on-request") → "approve"     # 命中 SAFE，放行
```

Step 4 **run_sandboxed()** (the command lands in the kernel-level sandbox to execute). Once let through, the command isn't run bare with `os.system`; instead it's wrapped in a macOS Seatbelt policy to execute — allowed to write files only under the working directory, read-only everywhere else (**why**: in case the command the model gave is destructive, the sandbox is the kernel-level last line of physical isolation). Before executing, it first `emit("exec", ...)`:

```text
[event] ⏵ exec      echo mini-codex online
（在 sandbox-exec 包裹下运行 /bin/sh -c "echo mini-codex online"）
→ 输出: "mini-codex online"
```

Step 5 **feed the result back, continue the loop** (s01). The command output is wrapped into a `tool_output_item` and appended back to the message list, and the loop returns to step 2 to ask the model again, "I have the result — what's next?" This time the model has no new tool call and just replies with a sentence, so it does `emit("msg", ...)` and then ends this turn:

```python
messages.append(tool_output_item("call_1", "mini-codex online"))
# 再调一次 model.respond → 这次 resp.tool_calls 为空 → 收尾
```

**Connecting these five steps**: a single sentence from the user, after passing through the five gates of "inject memory → model produces intent → approval lets it through → sandbox executes → feed the result back," finally becomes a real action that is **approved, sandboxed, and recorded**. The model appears at only step 2; at every other gate it's the harness doing the work. This is the skeleton this chapter wants you to see with your own eyes.

## Production-grade: look at "it runs" and "it's production-ready" separately

By now you've seen the "Production-grade" section of every chapter. Stack them up and a clear dividing line emerges: **the code that makes an agent "run" is small; the code that makes it "production-ready" takes up the bulk** — and the latter is almost entirely about answering the same question: "what do we do when things go wrong?"

| Gate | The "it runs" toy | The "production-ready" layer added on top |
|---|---|---|
| Turn loop ([s01](../s01_agent_loop/README.en.md)) | `while True` | Step cap + interruptible (`Op::Interrupt`) |
| Tools ([s02](../s02_tool_use/README.en.md)) | `handler(**args)` | schema validation + error feedback (`RespondToModel`/`Fatal`) |
| apply_patch ([s03](../s03_apply_patch/README.en.md)) | exact match | fuzzy match + atomic (two-phase) + error feedback |
| Approval ([s04](../s04_approval/README.en.md)) | a bool | `ReviewDecision` with memory + `BANNED_PREFIX` brake |
| Sandbox ([s05](../s05_sandbox/README.en.md)) | 8-line policy | deny-default + no network by default + per-command sandbox selection |
| Compaction ([s07](../s07_context_compaction/README.en.md)) | proactive estimation | reactive wall-hit fallback |
| Model call ([s09](../s09_responses_api/README.en.md)) | one request | retry + backoff jitter + transport fallback + error classification |
| MCP ([s15](../s15_mcp/README.en.md)) | in-process call | timeout + connection resilience + namespacing |
| Config ([s16](../s16_config/README.en.md)) | read a field | boundary validation (`deny_unknown_fields` + typed enum) |

See the shared motif? **Almost every layer's "production-grade" is a variation on the same sentence: assume it will fail, then make failure fail closed, recoverable, and not spiral out of control.** Fail closed, feed back so the model fixes it itself, cap to prevent runaway, don't lose the draft on power loss — these aren't nine unrelated tricks, but the same engineering instinct showing up nine times in nine places. This is what "building the vehicle" really means: the model handles being smart, while you handle making this vehicle **stay on the road even when it errs, the network flickers, the user shouts stop, or the disk fills up**.

## 🆚 How it differs from Claude Code: a master parts comparison

At the finish line, let's gather the whole journey into one table — the same pipeline, two sets of parts choices:

| Gate | Claude Code | Codex | Chapter |
|---|---|---|---|
| Wire protocol | Anthropic Messages | OpenAI Responses (+reasoning) | s09 |
| Editing files | Edit string replacement | apply_patch patch envelope | s03 |
| Security · first line | approval popup (application layer) | kernel sandbox (kernel layer) | s05 |
| Approval | ask when dangerous | 4-tier policy + Guardian | s04 / s14 |
| Architecture | a more direct loop | SQ/EQ dual queues + multiple frontends | s10 / s11 |
| Project memory | CLAUDE.md | AGENTS.md (level by level upward) | s06 |
| Session | history | Rollout (resumable/replayable) | s08 |

**Why?** To close the whole piece in one sentence: **Claude Code bets on "collaboration" (interactive approval UX, application-layer gatekeeping); Codex bets on "autonomy" (kernel sandbox, auditable rollout, built for unattended operation); and the tool differences on both sides, in the end, come down to the different habits their respective models were trained into.** Neither is right or wrong — only different bets for different scenarios.

## Deep dive: the teaching version vs. the real Codex source

<details>
<summary>1. What this demo pipeline leaves out</summary>

The mini Codex strings together only 6 parts. A real turn would also pass through: rollout recording (s08), context compaction (s07), MCP tools (s15), hooks (s13), Guardian risk assessment (s14), and feeding events to the TUI / `codex exec` (s11 frontends). Each one is yet another gate on this pipeline.

</details>

<details>
<summary>2. The full pipeline: which gate each of the 16 parts hangs on (whole-course index)</summary>

Hang all 16 parts of the course back onto this pipeline, in the order they appear along "a single real request" — this table is both a wrap-up and a navigation map for looking back: which station on the conveyor belt the part built in each chapter lands on.

| Pipeline station (in request order) | What it does | In the teaching demo? | Chapter |
|---|---|---|---|
| Turn loop (outermost) | `while`: call model → execute tools → feed back → call again, until there's no tool call | ✅ `run_turn` | [s01](../s01_agent_loop/README.en.md) |
| Tools and dispatch | declare tool schemas, route the model's tool_call to the matching handler | ✅ `TOOLS`/`HANDLERS` | [s02](../s02_tool_use/README.en.md) |
| Inject project memory | collect AGENTS.md level by level upward, stitch into the system prompt | ✅ `build_system` | [s06](../s06_agents_md/README.en.md) |
| Context compaction | when history grows too long, summarize old rounds to free up the window | ❌ omitted | [s07](../s07_context_compaction/README.en.md) |
| Resume / replay | write each round into the rollout; resumable and replayable after a crash | ❌ omitted | [s08](../s08_rollout/README.en.md) |
| Call the model (wire protocol) | send the request in the OpenAI Responses shape, receive reasoning + tool_call | ✅ `model.respond` (mock) | [s09](../s09_responses_api/README.en.md) |
| Event dual queues (SQ/EQ) | `Op` in on the submission side, `Event` out on the event side; decouple frontend from core | ✅ `emit` (slimmed down) | [s10](../s10_sq_eq_protocol/README.en.md) |
| Frontend rendering | feed events to the TUI or `codex exec` headless mode | ❌ omitted | [s11](../s11_frontends/README.en.md) |
| More tools | extension tools like plan / web_search / view_image | ❌ omitted | [s12](../s12_tools_extra/README.en.md) |
| Hooks | insert user-defined scripts at key moments | ❌ omitted | [s13](../s13_hooks/README.en.md) |
| Approval gate | rule approve / ask before tool execution; Guardian assesses risk | ✅ `decide` (4-tier) | [s04](../s04_approval/README.en.md) / [s14](../s14_guardian/README.en.md) |
| Kernel sandbox | once let through, the command lands in Seatbelt and can only write the workspace | ✅ `run_sandboxed` | [s05](../s05_sandbox/README.en.md) |
| Editing files | file changes go through the apply_patch patch envelope rather than bare writes | ✅ `apply_patch` | [s03](../s03_apply_patch/README.en.md) |
| MCP | connect to external tool servers (client + server) | ❌ omitted | [s15](../s15_mcp/README.en.md) |
| Config and profiles | layered parsing of config.toml + named profiles decide the defaults for each of the above tiers | ❌ omitted | [s16](../s16_config/README.en.md) |

How to read it: **the 6 rows marked ✅ are the minimal pipeline this chapter's demo actually strings together**; the rows marked ❌ are the extra gates the real Codex hangs on the same line — they don't change the skeleton, they just make each station sturdier. Note that the last row, "Config," doesn't stand at any specific station, but instead **cuts across the entire line** — it decides which approval tier is used, how wide the sandbox opens, and which model is used (see the previous chapter, [s16](../s16_config/README.en.md)).

</details>

<details>
<summary>3. It's still a toy</summary>

The teaching version's apply_patch only recognizes exact context, approval is a few string prefixes, the sandbox policy is 8 lines, and the model call is a mock. The real Codex's counterparts are, respectively, thousand-line crates, a state machine, a kernel policy starting at 123 lines, and a streaming WebSocket client. **But the skeleton is the same** — and this is exactly what this course wants to prove: the complexity is in the protection and the engineering; the core idea can be tiny.

</details>

<details>
<summary>4. The harness engineer's job</summary>

Look back at this pipeline: the model appears at only one step in the middle ("what to do"). Every other gate — memory, approval, sandbox, events, recording — is the harness. Agency comes from the model; and you, as the harness engineer, are responsible for building the entire world it inhabits and acts within.

</details>

<details>
<summary>5. Real source crate ↔ chapter (reverse lookup table)</summary>

If you want to look up, from some crate / module in `codex-rs`, "which chapter in this course takes it apart," use this table:

| codex-rs crate / module | What it's responsible for | Which chapter in this course |
|---|---|---|
| `core/src/session/turn.rs` | turn engine (orchestrates a turn) | [s01](../s01_agent_loop/README.en.md) · [s17](../s17_comprehensive/README.en.md) |
| `core/src/tools/` (registry / handlers) · `tools/src/tool_spec.rs` | tool registration and dispatch | [s02](../s02_tool_use/README.en.md) · [s12](../s12_tools_extra/README.en.md) |
| `apply-patch/` (parser · lib · seek_sequence) | parsing and applying the patch envelope | [s03](../s03_apply_patch/README.en.md) |
| `execpolicy/` · `shell-command/src/command_safety/` | approval decisions / command safety judgment | [s04](../s04_approval/README.en.md) |
| `protocol/src/approvals.rs` | approval events + Guardian risk enum | [s04](../s04_approval/README.en.md) · [s14](../s14_guardian/README.en.md) |
| `sandboxing/` (Seatbelt) · `linux-sandbox/` (Landlock+seccomp) | kernel-level sandbox | [s05](../s05_sandbox/README.en.md) |
| `core/src/agents_md.rs` | AGENTS.md layered injection | [s06](../s06_agents_md/README.en.md) |
| `core/src/compact.rs` · `compact_remote.rs` / `_v2` | context compaction (local + server-side) | [s07](../s07_context_compaction/README.en.md) |
| `rollout/` (recorder) · `state/` | session persistence (resume / rewind / audit) | [s08](../s08_rollout/README.en.md) |
| `core/src/client.rs` | Responses API model client | [s09](../s09_responses_api/README.en.md) |
| `protocol/src/protocol.rs` | SQ/EQ protocol: `Op` in / `EventMsg` out | [s10](../s10_sq_eq_protocol/README.en.md) |
| `tui/` · `exec/` · `app-server/` | three frontends (all just event consumers) | [s11](../s11_frontends/README.en.md) |
| `core/src/web_search.rs` · `protocol/src/plan_tool.rs` | more tools: plan / web_search / view_image | [s12](../s12_tools_extra/README.en.md) |
| `hooks/src/registry.rs` | hook registration and triggering | [s13](../s13_hooks/README.en.md) |
| `codex-mcp/` (MCP client) · `mcp-server/` · `core/src/mcp_tool_exposure.rs` | MCP bidirectional: connecting to others / being connected to | [s15](../s15_mcp/README.en.md) |
| `config/` (merge.rs) | config and profiles (cutting across the entire pipeline) | [s16](../s16_config/README.en.md) |
| `agent-graph-store/` · `agent-identity/` · `protocol`(InterAgentCommunication) | multi-agent: graph / identity / in-band communication | [s18](../s18_multiagent/README.en.md) |

> Note: paths are rooted at this repo's `../../codex/codex-rs/`; the same crate may appear in multiple chapters (e.g., `protocol.rs` is referenced in several places — approval / protocol / frontends), and only its **primary** chapter is listed here.

</details>

## Run

```bash
python s17_comprehensive/code.py --demo   # 离线跑通整条流水线
python s17_comprehensive/code.py          # 交互模式（命令会被审批 + 沙箱）
```

## Recap

- You started from a single `while` loop in [s01](../s01_agent_loop/README.en.md), passed through 16 chapters, accumulated a cabinet full of parts, and in this chapter assembled them into a mini Codex that approves, sandboxes, carries memory, and is observable.
- An agent product = model + harness; this chapter proves how the "assembly" is built from small parts meshing together — the **pipeline** is the skeleton, and each chapter's mechanism is a **gate** hung on that skeleton.
- The real Codex merely makes each part industrial-grade (thousand-line crates, state machines, kernel policies, streaming clients) — but the skeleton is the same, and you already hold it in your hands.
- **Production-grade is a hidden thread running through the whole book**: the code that makes an agent "run" is small, the code that makes it "production-ready" takes up the bulk — the latter is almost all about answering "what do we do when things go wrong," and the answer is always the same sentence: assume it will err, and make failure **fail closed, recoverable, not out of control** (see that master table in the "Production-grade" section).
- Advanced topic [s18 Multi-agent](../s18_multiagent/README.en.md): when **one** agent isn't enough, how do several agents talk to each other? (This neatly catches the 4th Think-it-over question below — it's the answer to an "18th part.")

## Think it over

<div class="think">

1. In this pipeline, removing which one part still lets the agent "work" but makes it **dangerous**? Removing which one makes it "safe" but **crippled**?
2. If you wanted to move this mini Codex to a brand-new domain (say, ops or data analysis), which parts work as-is and which must be swapped out? (Hint: the loop stays the same; the tools/knowledge/permissions change.)
3. Looking back over the whole journey, how much of the difference between Codex and Claude Code is "engineering trade-offs," and how much is actually forced by "two different models"?
4. Now it's your turn: after 17 chapters, what's the **18th part** you'd add to this harness? Which shortcoming of the model does it address?

</div>
