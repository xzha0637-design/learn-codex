# s13: Hooks — Hooking into the critical moments of a turn

> 🌐 **English** · [中文版](README.md)

> *"Extension points let users change behavior without forking the core."*

[learn-codex overview](../README.en.md) · [More tools: plan / web_search / view_image](../s12_tools_extra/README.en.md) → **This chapter** → [Guardian](../s14_guardian/README.en.md)

---

## Get the idea straight first: why "leave sockets" on the loop

The agent loop ([s01](../s01_agent_loop/README.en.md)) runs just fine, but every person and every team has their own "private agenda": "**Any** `rm` command must not run on our setup," "log an audit record before every tool call," "auto-run a formatter the moment a turn ends." These needs are all over the map, and they vary from person to person. So here's the question — how do you satisfy these customizations without dirtying that clean loop? Once you've worked through the three ideas below, the hook mechanism stops being just a new piece of jargon and becomes a design you'll feel "should obviously have been there all along."

**Idea one: cramming other people's customizations into the core is a dead end.**
The most direct approach is: every time a new need shows up, add an `if` to the loop. But needs are infinite, and they **vary from person to person** — your team bans `rm`, their team bans `curl`, and after a few more the loop is stuffed full of business logic, and those `if`s mean nothing to anyone else. The even worse fallback is "fork": the core gives you no room, so to customize you have to copy the whole project, edit the source, and maintain it yourself — every time someone upstream upgrades, you have to merge by hand, an endless misery. Neither path works, which tells us that **customization shouldn't be written into the core, but the core must leave room for customization.**

**Idea two: so leave "sockets" at the loop's critical moments, and let users plug their own code in.**
The clever move is: the core doesn't try to guess what you want to do; it just leaves mount points at a few **fixed moments**, and when the time comes it turns around and calls out, "anyone want to chime in here?" These fixed moments are the **fire points**, and the little snippet of code you plug in is the **hook**. It's like the screw holes pre-drilled into an engine casing — the manufacturer doesn't know what you're going to install, but they leave the holes, and you can bolt on whatever part you like. This chapter leaves four holes: `pre_turn` (before the turn begins), `pre_tool` (before each tool use), `post_tool` (after the tool is used), and `post_turn` (after the turn ends). From now on, "ban `rm`" is just a snippet of code you hang on `pre_tool` — the core hasn't changed a single line, and it doesn't need to know about your private agenda.

**Idea three (the most crucial): a socket can't only "observe" — it has to be able to "block" and "modify" — but after blocking, the model must be kept informed.**
If a hook can only look but not act, it's at best a logger. A truly useful socket needs **power**: the `pre_tool` hole is the most special — a hook hung on it can shout "**no**" right before the command actually runs, **vetoing** that call (like a security screener with the authority to stop a piece of luggage); or, rather than vetoing, it can **quietly rewrite** the arguments and let it through — for example, forcibly adding `--dry-run` to the command (like a screener who doesn't confiscate your water but just tightens the cap before letting it through). But there's an easily overlooked key point here: after a hook vetoes, it **must not silently discard** the call — it has to feed "it was vetoed, and the reason is X" back to the model **as the result** of that tool call. Why? Because the model thinks it ran that command; if you don't tell it "it was blocked," it'll keep going based on a wrong picture of the world. Only after the result is fed back does it learn "this road is closed" and turn to try another way. **So a hook isn't pulling tricks behind the model's back — it's having a "conversation" with the model.**

Tie these three points together: **a hook = your own code hung at a few moments the core has pre-reserved, letting you customize without forking; among them, `pre_tool` has the most power — it can veto and rewrite, and the veto reason is fed back to the model.** This is exactly what "let users change behavior without forking the core" looks like once it's made concrete.

## Problem

The agent loop runs just fine, but every team has its own "private agenda":

- "**Any** `rm` command must not run on our setup, even if the model thinks it's safe."
- "Before every tool call, record the command into an audit log."
- "The moment a turn ends, auto-run `prettier` to format the changes."
- "For this tool's arguments, I'd like to quietly rewrite them before execution (e.g., forcibly add `--dry-run`)."

These needs are all over the map, and they **vary from person to person**. If you add an `if` to the core for every new one, the core gets stuffed full of business logic; worse, for one small customization a user has no choice but to fork all of Codex.

What's needed is a set of **stable extension points**: "open a slot" at the loop's critical moments and let users plug in their own logic — observe, veto, or even rewrite, all without touching the core.

## Solution

A hook registry + four **fire points**. Hooks are ordinary callables registered by event name; when the loop reaches the corresponding moment it `fire`s them:

```
   run_turn(messages)
        │
   ┌────� fire("pre_turn") ─────────────────────────┐  回合开始
   │                                                 │
   │   model.respond(...)                            │
   │        │                                         │
   │   每个 tool_call:                                 │
   │        │                                         │
   │   ┌─ fire("pre_tool") ──▶ {block?} {command?}    │  ★ 可否决 / 可改写
   │   │        │                                      │
   │   │   block=True ─▶ 不执行，把理由回灌给模型 ──────┤
   │   │   command=.. ─▶ 改写后再执行                    │
   │   │        ▼                                       │
   │   │   run_shell(command)                          │
   │   │        ▼                                       │
   │   └─ fire("post_tool") ─────────────────────────┤  工具调用后
   │                                                  │
   └──── fire("post_turn") ──────────────────────────┘  回合结束
```

`pre_tool` is the most powerful one: its return value can **veto** this tool call (`block`) or **rewrite** its arguments (`command`). The other three fire points only produce side effects (logging, formatting, …).

## How it works

See [code.py](code.py), three parts.

**Part 1** — the registry + `fire()`. Hooks are bucketed by event name; `fire` runs them in order, and special-cases veto/rewrite for `pre_tool`:

```python
HOOKS = {"pre_turn": [], "pre_tool": [], "post_tool": [], "post_turn": []}

def register(event, fn): HOOKS[event].append(fn)

def fire(event, ctx):
    for fn in HOOKS[event]:
        out = fn(ctx) or {}
        if event == "pre_tool":
            if out.get("block"):   return {"block": True, "reason": out.get("reason")}
            if "command" in out:   ctx = {**ctx, "command": out["command"]}  # 改写
    ...
```

"If any hook vetoes, stop at the first veto" corresponds to `if should_abort_operation { break }` inside the real source's `Hooks::dispatch` ([registry.rs:94](../../codex/codex-rs/hooks/src/registry.rs)).

**Part 2** — weave the four fire points into the s01 loop. The skeleton is unchanged; we just insert `fire` in four places:

```python
def run_turn(messages):
    fire("pre_turn", {"messages": messages})
    while True:
        resp = model.respond(...)
        ...
        for tc in resp.tool_calls:
            gate = fire("pre_tool", {"tool": tc.name, "command": command})
            if gate.get("block"):
                messages.append(tool_output_item(tc.call_id, f"[blocked by hook] {gate['reason']}"))
                continue                      # ← 否决：不执行，理由回灌给模型
            if "command" in gate: command = gate["command"]   # ← 改写
            output = HANDLERS[tc.name](command=command)
            fire("post_tool", {"output": output, ...})
    fire("post_turn", {"messages": messages})
```

Note that when a call is vetoed, we **feed the veto reason back to the model as the tool result** — so the model learns "this road is blocked" and switches to another approach. This matches the real source.

**Part 3** — two example hooks:

```python
def block_rm(ctx):                                   # pre_tool：否决任何含 rm 的命令
    if "rm" in (ctx.get("command") or "").split():
        return {"block": True, "reason": "policy: `rm` is not allowed"}
    return {}

def log_post_turn(ctx):                              # post_turn：打一行日志
    print(f"[hook] post_turn: 对话现在有 {len(ctx['messages'])} 个 item")
```

`--demo` runs one canned turn (without invoking the model): the model first wants `echo`, then wants `rm -rf build`. The result: `echo` runs normally, `rm` is vetoed by `block_rm` before execution, and the `post_turn` hook logs a line to wrap up. `rm` never actually ran.

**Walk through it.** We'll follow this canned turn, keeping our eyes on the most crucial fire point, `pre_tool`, to see **what the data handed to the hook looks like at each step**, **what the hook returns**, and **what the core does as a result**. Two hooks are already registered: `block_rm` (hung on `pre_tool`) and `log_post_turn` (hung on `post_turn`).

1. **The turn begins**, and the core fires `pre_turn`. This demo hangs no hook at this point, so we skip it.

2. The model's first action: run `echo hi`. Before executing, the core packs the information for this tool call into a **ctx dict** and feeds it to each `pre_tool` hook:
   ```json
   { "tool": "shell", "command": "echo hi" }
   ```
   `block_rm` checks whether the command contains `rm` — it doesn't, so it **returns an empty dict `{}`** (meaning "I won't block and won't modify"). The core sees no `block` in the return, executes as usual, gets the output `hi`, then fires `post_tool`.

3. The model's second action: run `rm -rf build`. Again the ctx is packed first and fed to `pre_tool`:
   ```json
   { "tool": "shell", "command": "rm -rf build" }
   ```
   This time `block_rm` finds `rm` in the command and **returns a "veto" note**:
   ```json
   { "block": true, "reason": "policy: `rm` is not allowed" }
   ```
   The core sees `block: true` and **immediately does not execute** this command. `rm -rf build` was never run from start to finish — this is the hook's veto power.

4. **The crucial next step: feeding back.** The core didn't silently skip; instead it appends the veto reason to the conversation history as the "result" of this tool call:
   ```json
   { "type": "tool_output", "call_id": "...", "output": "[blocked by hook] policy: `rm` is not allowed" }
   ```
   This will be fed back to the model. **Why?** Because the model thinks it ran `rm`; if you don't tell it "it was blocked," it'll keep going based on a wrong picture of the world. After the feedback, the model reads "this road was blocked by policy" and will take a different route (e.g., switch to an approach that doesn't delete files).

5. **The turn ends**, the core fires `post_turn`, and it's the `log_post_turn` hook's turn. The ctx it receives contains the entire conversation:
   ```json
   { "messages": [ ... 此刻所有 item ... ] }
   ```
   It just logs a line (`the conversation now has N items`) and changes nothing — this is the textbook use of a "side-effects-only" fire point.

Tying this whole trip together: **the same `pre_tool` fire point lets `echo` through and vetoes `rm`**, the difference lying only in the note the hook returns; and the **feeding-back** step after a veto is the key to letting the model "stay informed and reroute." `rm` never ran the whole time, and the core didn't change a single line — this is exactly what "customize without forking" looks like.

## Production-grade: hooks are external commands — they hang, they crash, they may be malicious

The teaching version's hooks are in-process Python functions, well-behaved. But the real Codex's hooks are **arbitrary external commands on the user's machine** — this is their power, and also their largest risk surface: a malicious `PreToolUse` hook hidden in a project directory can execute arbitrary code on **every tool call** you make. So in a production-grade hook system, the focus isn't on "how to fire" at all, but on "how to keep it from harming you." Two gates (this chapter's [code.py](code.py) demonstrates them via `run_hook_safely`):

### One — trust: a hook whose hash doesn't match doesn't run

You can't unconditionally run a hook on your own machine just because some project wrote one into its `.codex/hooks`. The real Codex records a **`trusted_hash` (SHA-256)** for each hook ([`config_rules.rs`](../../codex/codex-rs/hooks/src/config_rules.rs)) — only if the hash matches (the version you trusted) does it run; if the project quietly changed the hook's contents and the hash doesn't match, it **doesn't run**. To allow everything globally you can set `bypass_hook_trust` (`registry.rs:33`), but that's tearing down this gate yourself.

```
(a) 信任校验：_evil_hook（untrusted）→ {'_skipped': 'untrusted hook 未执行（哈希不匹配）'}
```

### Two — timeout + fail-closed: a hung hook must not freeze every tool call

Hooks are external processes; they can hang (an infinite loop, waiting for a network response that never comes). If you wait on it synchronously, your agent freezes along with it on every tool call. The real Codex gives hooks a `timeout_sec` ([`declarations.rs:69`](../../codex/codex-rs/hooks/src/declarations.rs)). And for a hook with a **security purpose** (`PreToolUse`), which way should a timeout/crash lean? **Fail closed = treat it as a veto** — better to wrongly block one legitimate call than to let a dangerous operation through just because the gatekeeper died (the same temperament as [s14](../s14_guardian/README.en.md) Guardian and [s05](../s05_sandbox/README.en.md) sandbox).

```
(b) 超时 + fail-closed：_slow_hook（sleep 0.5s / 超时 0.1s）→ {'block': True, 'reason': 'hook 超时，fail-closed 当作否决'}
```

> In one line: the production-grade aspect of hooks isn't "how many callbacks you can hang," but **making "running other people's code inside your turn" controllable** — run only the trusted ones, give them a ceiling, and have them lean toward safety when they fail.

## 🆚 How it differs from Claude Code

**Both sides have hooks** — this is one of the few `≈` (largely the same) chapters, because hooks are a general extension pattern. But the similarities and differences each land somewhere specific:

| | Claude Code | Codex |
|---|---|---|
| Hooks at all? | Yes | Yes |
| Fire points | PreToolUse / PostToolUse / Stop / Notification ... | PreToolUse / PostToolUse / Stop / SessionStart / UserPromptSubmit ... (10 of them) |
| Veto mechanism | Hook exit code / JSON decision | Exit code `2` or `permissionDecision:"deny"` → `should_block` |
| Which system it grows in | A standalone hooks config | **The same system as the approval policy ([s04]) / Guardian ([s14]) / event protocol ([s10])**: a hook's veto, an approval's Prompt, and Guardian's risk assessment are all links on the same "pre-execution gatekeeping chain" |

**Why?** Because the value of an extension point is precisely **letting users customize behavior without forking the core** — both vendors agree on this, so the mechanisms converge. Codex's difference isn't "whether there are hooks," but **who the hooks grow alongside**: it stitches the `PreToolUse` hook, the `AskForApproval` approval, and Guardian's automatic review into the same gatekeeping pipeline between "tool call → execution." The more complete this pipeline, the more safely Codex can run with low/no human intervention (headless / CI / cloud) — which is exactly this course's through-line. Claude Code's hooks are more like a relatively standalone extension layer built around the interactive experience.

## Deep dive: teaching version vs the real Codex source

This chapter's hooks are "Python functions in the same process"; the real Codex's hooks are "external commands running in another process," and they're compatible with Claude Code's hooks JSON convention. Let's break it open.

<details>
<summary>One — this chapter's four points = a subset of the real source's ten event names</summary>

The real source's `hooks/src/lib.rs:19` defines 10 event names (`HOOK_EVENT_NAMES`):

```rust
pub const HOOK_EVENT_NAMES: [&str; 10] = [
    "PreToolUse", "PermissionRequest", "PostToolUse",
    "PreCompact", "PostCompact", "SessionStart",
    "UserPromptSubmit", "SubagentStart", "SubagentStop", "Stop",
];
```

This chapter's `pre_tool` / `post_tool` map directly to `PreToolUse` / `PostToolUse`; `pre_turn` ≈ `UserPromptSubmit` (a turn is opened by user input), `post_turn` ≈ `Stop` (the turn wraps up). The rest — like `PreCompact`/`PostCompact` (before/after context compaction, see [s07]), `SubagentStart`/`SubagentStop` (subagent lifecycle), `SessionStart`, and `PermissionRequest` — aren't covered in this chapter.

One more detail: the **matcher field** is only meaningful for 8 of the events (`HOOK_EVENT_NAMES_WITH_MATCHERS`) — a hook can use a regex matcher (e.g., `^Bash$`) to fire only for specific tools, whereas this chapter's `block_rm` is a simplified version that fires indiscriminately for all tools.

</details>

<details>
<summary>Two — pre_tool's real contract: it can veto, and it can rewrite</summary>

The output struct of the real source's `pre_tool_use.rs:37` is far richer than this chapter's `{block, command}` dict:

```rust
pub struct PreToolUseOutcome {
    pub should_block: bool,              // 否决这次工具调用
    pub block_reason: Option<String>,    // 否决理由（回灌给模型）
    pub additional_contexts: Vec<String>,// 给模型追加上下文
    pub updated_input: Option<Value>,    // 改写工具参数（本章的 command 改写）
    pub hook_events: Vec<HookCompletedEvent>,
}
```

How does a hook express veto/rewrite? It's an external command, and the convention (compatible with Claude Code) is:

| What the hook did | Result |
|---|---|
| Exit code `2`, reason written to stderr | `should_block = true`, reason goes into `block_reason` |
| stdout outputs `{"hookSpecificOutput":{"permissionDecision":"deny", ...}}` | Veto |
| stdout outputs `{... "permissionDecision":"allow", "updatedInput":{...}}` | Allow + **rewrite the arguments** with `updatedInput` |
| Exit code `0`, no output | Allow, no change |

When multiple hooks compete to rewrite, the real source takes the last one by **completion order** (`latest_updated_input`, `pre_tool_use.rs:148`). This chapter flattens this protocol into a function returning a dict — but the three core things, "it can veto, it can rewrite, and the veto reason is fed back to the model," are faithful.

</details>

<details>
<summary>Three — hooks run in another process, not as same-process functions</summary>

This chapter's `register("pre_tool", block_rm)` registers a same-process Python function; the real Codex's hooks are an **external command** configured in the hooks JSON / `config.toml`, executed by `ClaudeHooksEngine` spawning a child process via `CommandShell` (that `shell_program` / `shell_args` in `registry.rs:60`).

This brings several real-world complexities this chapter doesn't have:

- **Timeout**: each hook has a `timeout_sec`; running too long gets it killed (`ConfiguredHandler.timeout_sec`).
- **Trust**: a hook is an arbitrary command on the user's machine, with `bypass_hook_trust` / trust verification, to prevent a malicious hook being quietly slipped into a project directory.
- **Source layering**: hooks can come from multiple sources — user / project / plugin (`HookSource`) — and are sorted and dispatched by source and `display_order`.
- **stdin contract**: the core serializes `{session_id, turn_id, cwd, tool_name, tool_input, permission_mode, ...}` into JSON and feeds it to the hook's standard input (`command_input_json`, `pre_tool_use.rs:170`).

In one line: this chapter is an "in-process callback," and the real thing is a "child process with timeouts, trust, layering, and JSON communication."

</details>

<details>
<summary>Four — hooks, approval, Guardian: three links on the pre-execution gatekeeping chain</summary>

This chapter presents hooks as an isolated mechanism, but in the real Codex, between "tool call → actual execution" lies a **gatekeeping chain**, and the hook is just one link:

```
   model 要调 shell(command)
        │
   ① PreToolUse 钩子   ── should_block? ──▶ 否决（本章）
        │ 未否决
   ② 审批策略 (s04)    ── Decision::Prompt? ──▶ 问用户 / 自动拒
        │ 需要问用户时
   ③ Guardian (s14)    ── 先自动评一遍风险 ──▶ low 自动批 / critical 自动拒 / 否则升级
        │ 放行
   ④ 沙箱 (s05)        ── 内核强制 ──▶ 执行
```

The `PermissionRequest` hook event is precisely the mount point for ② and ③ — a hook can even step into the "approval request" itself. This is why Codex's hooks and approval/Guardian "grow within the same system": they aren't four unrelated features, but four pluggable checkpoints on the same pipeline. Once you understand this chain, [s14]'s Guardian is just the "before asking the user, first dispatch an AI reviewer to gatekeep" link.

</details>

## Run

```bash
python s13_hooks/code.py --demo   # 不需要模型：canned 回合，看 pre_tool 否决 rm
python s13_hooks/code.py          # 交互模式：含 rm 的命令会被钩子否决
```

`--demo` is fully offline (`backend=mock`).

[s04]: ../s04_approval/README.en.md
[s07]: ../s07_context_compaction/README.en.md
[s10]: ../s10_sq_eq_protocol/README.en.md
[s14]: ../s14_guardian/README.en.md

## Recap

- A hook = a callable registered by event name; the loop `fire`s them at four fire points: `pre_turn / pre_tool / post_tool / post_turn`.
- `pre_tool` is the most powerful: it can **veto** (`should_block`) and **rewrite** (`updated_input`) a tool call; the veto reason is fed back to the model.
- The real source has 10 events, external-command hooks, and timeout/trust/layering; this chapter takes the minimal subset.
- **Production-grade**: hooks are external commands — run only the hash-trusted ones (`trusted_hash`, to guard against malicious project hooks), wrap them with `timeout_sec` (a hang won't freeze the call), and a security hook that times out fails closed as a veto (see the "Production-grade" section).
- Hooks aren't an island: alongside approval ([s04]), Guardian ([s14]), and the event protocol ([s10]), they belong to one "pre-execution gatekeeping chain."
- Next stop [s14 Guardian](../s14_guardian/README.en.md): the next link in the gatekeeping chain — letting an AI reviewer automatically judge risk before asking the user.

## Think it over

1. This chapter's `pre_tool` hook can rewrite command arguments (`updated_input`). That's convenient (auto-adding `--dry-run`), but it also means "the model thinks it ran A, but B actually ran." When something goes wrong and you're debugging, would this kind of "well-meaning silent edit" be harder to debug than an outright veto? How would you make the rewrite transparent to the model/user?

2. In the real Codex, a hook is an arbitrary external command on the user's machine — this is the root of its power and also the root of its risk: a malicious `PreToolUse` hook hidden in a project directory can execute arbitrary code on every tool call. Codex uses "trust verification" to guard against this. If it were you, what criteria would you use to decide "which hooks are trustworthy"? For hooks a project ships with, should they be trusted by default or doubted by default?

3. Hooks, approval, Guardian, and the sandbox are four checkpoints on the same gatekeeping chain. If a `pre_tool` hook lets a command through but the approval policy wants to reject it, who has the final say? Conversely, can a hook "click approve on the user's behalf" to bypass approval? Pinning down this chain's priority order matters more than perfecting each link on its own — how would you order these priorities?

4. This chapter is one of the few places where Codex and Claude Code are `≈` (largely the same). When two systems converge on some mechanism, it often signals that the mechanism has touched some "universal optimum." Is a hook such a universal extension pattern? Or is its convergence merely because both vendors are copying the same (Claude Code's) hooks JSON convention?
