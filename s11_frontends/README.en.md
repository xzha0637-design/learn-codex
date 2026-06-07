# s11: Frontends — a frontend is just a consumer of the event stream (TUI + exec)

> 🌐 **English** · [中文版](README.md)

> *"One core produces one event stream; the frontend holds no business logic — it's just a consumer of that stream. Terminal, CI, IDE each take what they need."*

[learn-codex overview](../README.en.md) · [s10 SQ/EQ protocol](../s10_sq_eq_protocol/README.en.md) → **s11 Frontends: TUI + exec** → [s12 More tools](../s12_tools_extra/README.en.md)

---

## Get the idea straight first: why swapping a "face" doesn't touch the "brain"

By now you know that the agent's "brain" is that agent loop ([s01](../s01_agent_loop/README.en.md)): call the model, run tools, feed back, call again. But the same brain may have to serve three completely different users at once — a person staring at a terminal screen, a script in CI that nobody is watching, an editor backed by an IDE backend. How do you achieve "one brain, many faces," such that adding a new face requires not a single line of change in the brain? Get the following three ideas straight and this chapter clicks.

**Idea one: "what to do" and "how to display it" are two different things, and the easiest mistake is to weld them together.**
The most naive way to write it: the moment the model replies, `print`; the moment a command finishes, `print` — wiring display straight into the loop. But once you write it that way, trouble piles up immediately: to upgrade plain text into a pretty boxed interface, you have to change **every single** `print` in the loop; to let CI parse the output with a script, those colors and box-drawing characters will choke the script. The root cause: **"what happened" is the brain's job, "what it looks like" is the face's job, and once you tangle the two together, neither can move without the other.**

**Idea two: let the brain only "report," never "print," and it's decoupled from every face.**
The clever move: when the core runs a turn, it doesn't print directly — it **reports what happened**, one item at a time: "the turn started," "I'm about to run this command," "the command finished, exit code 0," "I'm done talking." Each of these items is an **event** (a small dict with a `type` field), and lining them up in order gives you an **event stream**. It's like the kitchen line constantly shouting out — "fire on," "this dish is up" — the line only shouts; it **doesn't care at all** how the front of house plates things. The shouting (the event stream) becomes the only interface between the brain and the face, and from then on the two sides evolve independently.

**Idea three (the crucial one): a face is nothing but a consumer that "listens to this stream" — so there can be many faces, and adding one doesn't touch the brain.**
Since the core only shouts events outward, all any "face" has to do is one thing: **listen to this stream and decide how to display it**. The face in the terminal paints each event into a colored box (that bit of code that "turns one event into a few lines on screen" is the **renderer**; swap the renderer = swap the display style, the brain stays put); the face in CI spits each event out verbatim as one line of JSON (the **JSONL** format, one independent JSON object per line — the script reads and parses one line at a time, extremely friendly to automation), then uses the **exit code** to tell `&&` and the pipeline whether the run actually succeeded. The latter is what's called **headless** — nobody sits in front of the terminal, nobody answers y/N, everything speaks through machine-readable output and exit codes (this is exactly `codex exec`). The terminal, CI, and IDE backend are, in essence, all just consumers that "listen to the same stream"; they differ only in where they paint once they've listened.

Tie these three together: **the core shouts, the face listens.** This chapter shows you two ways of listening — one paints for a human (the TUI renderer), one spits out for a machine (headless exec) — and both read the **same** event stream, with not one line changed in the core. This is exactly the fruit borne by that "architectural backbone" from the previous chapter, [s10](../s10_sq_eq_protocol/README.en.md), which split input and output into two queues.

## Problem

Up through [s10](../s10_sq_eq_protocol/README.en.md), Codex has turned a turn into "events flow outward (EQ), operations flow inward (SQ)." But every time we ran it before, we tacitly assumed there was a person sitting in front of the terminal watching the output. In reality Codex has to serve two completely different "users" at once:

- **a human**, sitting at the terminal, who wants a pretty, colored, scrollable interface (the TUI);
- **a machine**, in CI / a pipeline / the cloud, with no TTY and nobody to click an approval, that only wants **output a program can parse** and **an exit code signaling success or failure**.

The easiest version to get wrong: weld the display logic straight into the agent loop — the moment the model replies, `print`; the moment a command finishes, `print`. Write it that way and several problems blow up at once:

- **Reskinning is hard**: to upgrade from "plain text" to a "boxed ratatui interface," you have to change **every single** `print` in the loop;
- **Multiple frontends are impossible**: want the TUI, `codex exec`, and the app-server (IDE backend) to reuse the same turn logic? Can't be done — logic and display are tangled together;
- **Machines can't read it**: CI wants to `| jq` the output? The colorful colors and box-drawing characters will choke the script;
- **Testing is hard**: to verify "the UI shows a red cross when a command fails," you'd have to spin up the entire model loop.

Codex's answer: **let the core produce only a frontend-agnostic typed event stream; the frontend is just a consumer of that stream.** Whoever wants to display it however they like can do so; the core doesn't care.

## Solution

Break the architecture into "one stream, many consumers":

```
            run_turn_events(prompt)   ← core（搬自 s01 的回合循环，去掉所有 print）
                     │
                     │  yield 一条 typed 事件流：
                     │    thread.started → turn.started →
                     │    item.started/completed（命令、消息）→ turn.completed|failed
                     ▼
        ┌────────────────────────────┬────────────────────────────┐
        │  消费者 A：TUI 渲染器         │  消费者 B：headless exec      │
        │  （有人坐在终端前）            │  （CI / 管道 / 云端，无头）     │
        │                            │                            │
        │  WIDGETS 派发表             │  EventProcessor             │
        │   事件类型→渲染函数           │   ├ HumanProcessor 人类文本    │
        │   ├ BoxRenderer  盒子框线    │   └ JsonlProcessor --json     │
        │   └ PlainRenderer 朴素一行   │      每行一个 JSON             │
        │                            │                            │
        │  换渲染器不碰 core            │  跑完据 error_seen 返回退出码    │
        └────────────────────────────┴────────────────────────────┘
```

Notice that both sides read the **same event stream**. Swapping the renderer (the left side's `--plain`) or swapping the processor (the right side's `--json`) only changes that very last layer; **not one line of the core moves**. This is what "one core, many frontends" looks like at minimal scale — in real codex there's even a third consumer, the app-server (the WebSocket backend for IDE/cloud), which likewise just "consumes this stream."

## How it works

Look at [code.py](code.py), in three blocks: core, TUI consumer, exec consumer.

**① core: run the turn as an event stream.** `run_turn_events` is s01's `run_turn`, with the only change being **replacing all `print` with `yield event`**. The event taxonomy aligns with real codex's `exec_events.rs`: each "item" (command, message) has two beats, `started` / `completed`.

```python
def run_turn_events(prompt):
    yield {"type": "thread.started", "thread_id": "thr_demo"}
    yield {"type": "turn.started"}
    while True:
        resp = model.respond(messages, tools=TOOLS, system=SYSTEM)
        ...
        yield {"type": "item.started",
               "item": {"item_type": "command_execution", "command": cmd, "status": "in_progress"}}
        output, code = run_shell(cmd)
        yield {"type": "item.completed",
               "item": {"item_type": "command_execution", "command": cmd,
                        "aggregated_output": output, "exit_code": code,
                        "status": "completed" if code == 0 else "failed"}}
    yield {"type": "turn.completed", ...}   # 或 turn.failed
```

**② Consumer A: the TUI renderer (widgets as functions).** Each kind of event maps to a pure function `(event) -> list[str]` (the lines to print). These are the minimal incarnation of those widgets in real codex's `history_cell/` (`ExecCell`/`MessageCell`/`PatchCell`…). A single `WIDGETS` dispatch table maps event types to render functions; want to support a new event? **Add one line.** Then wrap a wholesale-swappable "renderer" around it:

```python
WIDGETS = {"thread.started": w_thread_started, "turn.started": w_turn_started,
           "item.started": _w_item_started, "item.completed": _w_item_completed, ...}

class BoxRenderer:    # 查 WIDGETS 表，画带色框线
    def render(self, event):
        for ln in WIDGETS.get(event["type"], ...)(event): print(ln)

class PlainRenderer:  # 无视 widget 表，每个事件压成一行朴素文本
    def render(self, event): print(f"[{event['type']}] ...")
```

**③ Consumer B: headless exec (human text / JSONL).** This corresponds to the two implementations of the `EventProcessor` trait in `exec/src`. `JsonlProcessor` does `print(json.dumps(...))` on each event (one JSON per line); `HumanProcessor` picks the key events and colors them for a human. Each `process(event)` returns "whether this was a fatal error," and at the end they're summed into an exit code — corresponding to the `if error_seen { std::process::exit(1); }` at the end of the real `lib.rs`:

```python
def run_exec(prompt, json_mode):
    processor = JsonlProcessor() if json_mode else HumanProcessor()
    error_seen = False
    for event in run_turn_events(prompt):      # 同一条 core 流，换个消费者
        if processor.process(event):
            error_seen = True
    return 1 if error_seen else 0              # 退出码：给 CI 的信号
```

The two drivers `drive_tui` (`for event: renderer.render(event)`) and `run_exec` (`for event: processor.process(event)`) are **almost identical in shape** — this symmetry is no coincidence; it's exactly the effect that "decoupling core from frontend" is meant to achieve.

> **Key: stdout carries only the real output.** With `--json`, stdout must be **pure JSONL**, otherwise `| jq` gets choked by the noise. codexlib's startup banner prints to stdout by default; this chapter uses `contextlib.redirect_stdout(sys.stderr)` to reroute it — real codex exec likewise writes the config summary/warnings to stderr and only writes results to stdout.

### Walk through it

Let's follow `--demo` through it: the prompt is `执行 \`echo hello from codex\` 并报告结果`. The demo first collects the whole stream into a list, `stream = list(run_turn_events(...))`, **emphasizing that these are the same batch of events**, then feeds it to the two consumers separately.

**Step 1**: the core runs the turn and produces events. The mock backend, seeing "执行 …", emits one shell call, so the stream contains in order (each one is a dict):

```
{"type": "thread.started", "thread_id": "thr_demo"}
{"type": "turn.started"}
{"type": "item.started",   "item": {"item_type": "command_execution", "command": "echo hello from codex", "status": "in_progress"}}
{"type": "item.completed", "item": {"item_type": "command_execution", "command": "echo hello from codex", "aggregated_output": "hello from codex", "exit_code": 0, "status": "completed"}}
{"type": "item.completed", "item": {"item_type": "agent_message", "text": "..."}}
{"type": "turn.completed", "usage": {...}}
```

Why "two beats" (started + completed)? Because a command **starting** and **finishing** are two distinct moments — the TUI wants to first show "(running…)" at the start and then append "✓ exit code 0" at the finish; JSONL wants downstream scripts to be able to tell "running" apart from "done." The same stream satisfies both needs.

**Step 2**: Consumer A (`BoxRenderer`) gets that `item.started` line, looks up `WIDGETS["item.started"]`, and renders it into a colored line `$ exec echo hello from codex (running…)`; it gets `item.completed` and renders `✓ ok` + output. It **only looks at events; it has no idea how the command was run**.

**Step 3**: Consumer B (`JsonlProcessor`) gets the **exact same** events, and does `print(json.dumps(event))` on each — spitting them out verbatim as those 6 lines of JSONL above. `turn.completed` is not `turn.failed`, so `error_seen=False` and the exit code is 0.

**Why this step is the point**: Steps 2 and 3 read the same `stream` list. Their output forms are worlds apart (terminal interface vs JSONL), but `run_turn_events` didn't change a single line. This is what "a frontend is just a consumer of the event stream" means — if you want to add a third frontend (say, a web dashboard), you just write another consumer that "listens to this stream," and the core still stays put.

## Production-grade: a frontend must withstand "the stream is intermittent, rewinds, and errors out"

"The core produces events, the frontend only consumes" is clean, but a real event stream arrives **intermittently**: a `function_call` first arrives as an empty shell, then the arguments delta in bit by bit; the body `output_text` is stitched together segment by segment; and partway through, an error or interrupt may still arrive. A production-ready frontend has to handle these:

- **Incremental rendering**: you can't wait for a message to be complete before showing it — you have to refresh as deltas come in (the streaming typewriter effect), otherwise the user is left staring at a "frozen" screen.
- **Fallback for out-of-order/rewind**: `MessagePhase` (commentary vs final, [s09](../s09_responses_api/README.en.md)) can change, tool calls can be interrupted by an approval, a turn can be cut short by an `Interrupt` — the frontend has to be able to cleanly wrap up or retract a "half message," rather than leaving a stub of half-finished UI.
- **Parity for the headless frontend**: `codex exec` serializes the same event stream into **JSONL** (one line per event) for CI/pipelines to consume. It sees the **same stream** as the TUI — so the "display logic" must be completely peeled away from the "production logic," otherwise headless mode would behave inconsistently with interactive mode.

> In one sentence: a frontend's production-grade quality is in acknowledging that "the stream is intermittent, changes, and breaks," then giving every intermediate state a not-ugly way out. The core only faithfully produces events; cleaning all of this up is the frontend layer's job.

## 🆚 How it differs from Claude Code

| | Claude Code | Codex |
|---|---|---|
| Frontend vs core | UI fairly tightly coupled to the agent loop | **Event-stream decoupled**: the core produces `EventMsg`, the frontend is just a consumer |
| Number of frontends | Mostly a single interactive CLI/TUI | TUI + `codex exec` + app-server **share the same core** |
| Adding a new frontend | Often requires touching the loop | Just write a new event consumer; **the core doesn't move** |
| Headless entry point | Has non-interactive uses like `-p/--print`, but the center of gravity is interactive | **First-class citizen** `codex exec`: built for automation, `--json` outputs JSONL |
| Success/failure signal | Mostly in-session interaction | **Exit code** (`error_seen → exit(1)`) + `turn.failed`, for `&&` / CI |
| Render granularity | Leans toward whole output | One widget per event type (`history_cell/exec.rs`, `patches.rs`…), each can evolve independently |

**Why?** Because from the start Codex envisioned "**one core, many frontends**," and these frontends run in wildly different scenarios:

- in the terminal it's a ratatui TUI, with someone watching, so it must look good and be interactive;
- in CI / a pipeline it's `codex exec`, with **nobody watching** — it needs JSONL (machine-readable) + an exit code (`&&` and CI branch on it), and stdout must not be polluted with human noise;
- in the IDE / cloud it's the app-server, which emits events as JSON-RPC `ServerNotification`s to feed the editor.

To let these three frontends reuse the **same turn logic**, you **must not** write rendering into the loop — you have to make the core produce only frontend-agnostic typed events, and let whoever wants to paint them paint however they like. This is exactly the fruit borne by [s10](../s10_sq_eq_protocol/README.en.md) splitting input and output into two queues (SQ/EQ), that **architectural backbone**: s11's TUI and exec are **two different frontends** that grew on that backbone — one for human interaction (terminal widgets), one for headless automation (machine-readable + exit code).

This is of a piece with the whole course's through-line: **Claude Code centers on "a person interacting in front of a terminal/IDE,"** so the loop can be written more directly, but it's also harder to grow forms like "headless" and "IDE backend"; **Codex bets on "autonomous runs with low/no human intervention,"** so it architecturally separates core from frontend with an event stream. Headless isn't "turning off interactive mode" — it's **an output contract redesigned for automation**.

## Deep dive: teaching version vs real Codex source

This chapter's ~180 lines (one core stream + two families of consumers) correspond to two complete crates in real codex: `codex-rs/tui` (tens of thousands of lines) and `codex-rs/exec`. Where's the simplification, and what does the real version do more of?

<details>
<summary>1. The TUI's widgets: teaching version's pure functions vs history_cell's HistoryCell trait</summary>

The teaching version's widget is `(event) -> list[str]`. In real codex's [`tui/src/history_cell/`](../../codex/codex-rs/tui/src/history_cell/), each kind of entry is a type implementing the `HistoryCell` trait (`history_cell/mod.rs:189`):

```rust
fn display_lines(&self, width: u16) -> Vec<Line<'static>>;             // 191 行
fn render(&self, area: Rect, buf: &mut Buffer) { /* 默认画 display_lines */ }  // 301 行
```

| Entry type | Teaching version | Real codex module |
|---|---|---|
| Command execution | `w_exec_begin`/`w_exec_end` | `history_cell/exec.rs` + `exec_cell/` (animation, streaming output, folding) |
| Message bubble | `w_agent_message` | `history_cell/messages.rs` (markdown rendering, `markdown_render.rs`) |
| Patch diff | (none) | `history_cell/patches.rs` + `diff_render.rs` (colored diff) |
| MCP call | (none) | `history_cell/mcp.rs` |
| Plan/todo | (none) | `history_cell/plans.rs` |
| Approval | (none) | `history_cell/approvals.rs` (with interactive buttons, replies on SQ) |

The key commonality is unchanged: **one independent widget per event type, mutually decoupled**, and adding a new entry type won't touch the others. The difference is that a real widget has to return a laid-out `Vec<Line>` (with styling, wrapped to `width`), and also support in-place updates (during a command's streaming output, `display_lines` changes over time).

</details>

<details>
<summary>2. exec's two processors: teaching version's process method vs the real EventProcessor trait</summary>

Real codex defines a trait in [`exec/src/event_processor.rs`](../../codex/codex-rs/exec/src/event_processor.rs), and both the `human` / `jsonl` processors implement it:

```rust
pub(crate) trait EventProcessor {
    fn print_config_summary(&mut self, config: &Config, prompt: &str, sc: &SessionConfiguredEvent);
    fn process_server_notification(&mut self, notification: ServerNotification) -> CodexStatus;
    fn process_warning(&mut self, message: String) -> CodexStatus;
    fn print_final_output(&mut self) {}
}
```

| | Teaching version | Real codex-rs |
|---|---|---|
| Interface | One `process(event) -> bool` | trait + 4 methods (incl. config summary, warning, wrap-up) |
| Selection | `JsonlProcessor() if json_mode else HumanProcessor()` | `match json_mode { true => EventProcessorWithJsonOutput::new(..), _ => EventProcessorWithHumanOutput::create_with_ansi(..) }` (`lib.rs:671`) |
| Return value | bool (whether fatal error) | `CodexStatus::{Running, InitiateShutdown}` controlling when to wrap up and exit |
| JSONL processor | `print(json.dumps(...))` | [`event_processor_with_jsonl_output.rs`](../../codex/codex-rs/exec/src/event_processor_with_jsonl_output.rs) translates the internal `ServerNotification` into a stable `ThreadEvent`, then `println!(serde_json::to_string(..))`, and also maintains state like `next_item_id`, the todo list, token tallies, etc. |

The teaching version makes "fatal error" directly the return value of `process`; the real version uses a `CodexStatus` enum so the processor can proactively demand "time to wrap up" (`InitiateShutdown`).

</details>

<details>
<summary>3. Event kinds: teaching version's 6 vs the protocol's dozens of variants</summary>

The teaching version's core emits only 6 kinds of event (`thread.started` / `turn.started` / `item.started` / `item.completed` / `turn.completed` / `turn.failed`). There are actually **two** sets of event naming, corresponding to the two consumers:

- **The TUI side** consumes the `EventMsg` enum in [`protocol/src/protocol.rs`](../../codex/codex-rs/protocol/src/protocol.rs) (with dozens of variants):

```
TurnStarted, AgentMessage, AgentMessageContentDelta, AgentReasoning,
ExecCommandBegin, ExecCommandOutputDelta, ExecCommandEnd,
PatchApplyBegin, PatchApplyEnd, McpToolCallBegin, McpToolCallEnd,
WebSearchBegin, WebSearchEnd, TokenCount, PlanUpdate, TurnDiff,
ExecApprovalRequest, ApplyPatchApprovalRequest, StreamError, ...
```

- **The exec side** consumes the `ThreadEvent` in [`exec/src/exec_events.rs`](../../codex/codex-rs/exec/src/exec_events.rs) (externally stable, can export TS types):

```rust
#[serde(tag = "type")]
pub enum ThreadEvent {
    #[serde(rename = "thread.started")] ThreadStarted(..),
    #[serde(rename = "turn.started")]   TurnStarted(..),
    #[serde(rename = "item.started")]   ItemStarted(..),
    #[serde(rename = "item.completed")] ItemCompleted(..),
    #[serde(rename = "turn.completed")] TurnCompleted(..),
    #[serde(rename = "turn.failed")]    TurnFailed(..), ...
}
```

Each item (`ThreadItemDetails`) is also a whole set: `agent_message` / `reasoning` / `command_execution` / `file_change` / `mcp_tool_call` / `web_search` / `todo_list`. The teaching version implements only `agent_message` and `command_execution` (with the real `status` / `exit_code` / `aggregated_output` fields).

Notice the `*Delta` variants in `EventMsg` (`AgentMessageContentDelta` / `ExecCommandOutputDelta`) — the real TUI has to handle **streaming increments** (the model spitting text out as it thinks, a command emitting stdout as it runs), and the widget has to be able to "keep appending and redraw." In the teaching version every event "lands in one beat," so the render functions can be stateless pure functions.

Why does the exec side need its own separate set of `ThreadEvent`? Because JSONL is an **external contract**: downstream scripts, the cloud, and IDEs all parse by it, and a single field change is a breaking change — that's why it uses `ts-rs` to export types, fixes field names as `snake_case`, and makes status enums explicit (`in_progress`/`completed`/`failed`/`declined`).

</details>

<details>
<summary>4. The third consumer, app-server, and the real shape of exec's main loop</summary>

The one sentence this chapter most wants you to remember: **the TUI, exec, and app-server are the same kind of thing — all consumers of the core's event stream**, only with different render targets.

| Frontend | Renders into what | Entry crate |
|---|---|---|
| TUI | ratatui terminal interface | `codex-rs/tui` |
| exec | human text / JSONL | `codex-rs/exec` |
| app-server | JSON-RPC `ServerNotification`, fed to IDE/cloud | `codex-rs/app-server` |

They consume the **same set of events**. This version of the TUI has even been refactored to consume the app-server's `ServerNotification` (see that giant dispatch table in [`tui/src/app/app_server_event_targets.rs`](../../codex/codex-rs/tui/src/app/app_server_event_targets.rs)), going over the same protocol as exec.

And the real exec's "loop" isn't that same-process `for` of the teaching version either. It's a **client** connected to an in-process app-server, looping to consume `next_event()` ([`exec/src/lib.rs`](../../codex/codex-rs/exec/src/lib.rs)):

```rust
loop {
    let server_event = tokio::select! {
        maybe_interrupt = interrupt_rx.recv(), .. => { /* 发 TurnInterrupt */ continue; }
        maybe_event = client.next_event() => maybe_event,        // lib.rs:955
    };
    match server_event {
        ServerNotification(n) => match event_processor.process_server_notification(n) {
            CodexStatus::Running => {}
            CodexStatus::InitiateShutdown => { request_shutdown(..).await?; break; }  // 1001
        }, ..
    }
}
event_processor.print_final_output();                            // 1027
if error_seen { std::process::exit(1); }                         // 1028：给 CI 的信号
```

The gap: the real version can, in headless mode, **respond to Ctrl-C / signals by sending `TurnInterrupt`** (that arm of `tokio::select!`), accumulate `turn.failed` / `Error` into `error_seen`, and also `--output-last-message <file>` to write the last message to a file, `codex exec resume` to pick up where the last thread left off, `--image` to feed a local image. The teaching version compresses all of this into "run the loop, check error_seen," but keeps the most essential trunk: **prompt in → events out → pick a consumer to render → exit code makes the call.**

The teaching version's `drive_tui`, that `for event in run_turn_events(prompt): renderer.render(event)`, is almost identical to `run_exec`'s `for event in run_turn_events(prompt): processor.process(event)` — this symmetry is exactly the effect "decoupling core from frontend" is meant to achieve: there can be many frontends, and to the core they're all just "a loop consuming an event stream."

</details>

## Run

```bash
python s11_frontends/code.py --demo            # 离线：同一条流，先 TUI(box) 渲染、再 JSONL 输出（exit 0）
python s11_frontends/code.py --json "看看目录"   # 无头 exec：argv 当 prompt，输出 JSONL
python s11_frontends/code.py --exec "执行 \`ls\`" # 无头 exec：人类可读文本
echo "列出 TODO" | python s11_frontends/code.py --exec   # stdin 喂 prompt
python s11_frontends/code.py --plain           # 交互 TUI，换 [plain] 渲染器（core 一行不动）
python s11_frontends/code.py                   # 交互 TUI（默认 box 渲染器；输入 q 退出）
```

By default `backend=mock`, so it runs offline. Try handing the JSONL to a script to parse (note `2>/dev/null` filters out the startup banner, verifying that stdout is pure JSONL):

```bash
python s11_frontends/code.py --json "执行 \`echo hi\`" 2>/dev/null \
  | python3 -c "import sys,json; [print(json.loads(l)['type']) for l in sys.stdin if l.strip()]"
# thread.started / turn.started / item.started / item.completed / item.completed / turn.completed
```

> Mini-experiment one: run `--demo` and compare the two output blocks from "Consumer A" (TUI box-drawing) and "Consumer B" (JSONL) — they come from the **same `stream` list**, with no change at all in the core.
> Mini-experiment two: have the model run a command that will fail (`python s11_frontends/code.py --json "执行 \`false\`"`), and observe that `--json` gets one extra `turn.failed`, and the **exit code becomes 1** — this is exactly the signal CI uses to judge success or failure.

## Recap

- A frontend = **a consumer of the event stream**, holding no business logic: the core produces typed events, the frontend only "displays" them.
- This chapter gives two families of consumers at once: the **TUI renderer** (widgets as functions + hot-swappable `Box`/`Plain` renderers) and **headless exec** (human text / JSONL + exit code).
- **widgets as functions**: one independent render unit per event type (real codex's `history_cell/*`), each can be added/removed/modified independently.
- **JSONL is an external contract**: stdout must be pristine (human noise goes to stderr), fields are stable, for scripts/CI/pipelines to parse; the **exit code** lets Codex embed into `&&` and CI.
- The TUI, exec, and app-server are the **same kind of consumer**, only with different render targets — this is exactly the fruit borne by [s10](../s10_sq_eq_protocol/README.en.md)'s queue decoupling, and the most direct manifestation of "Codex betting on autonomous runs with low/no human intervention."
- **Production-grade**: the event stream is intermittent — the frontend has to render incrementally (don't freeze), provide fallback for out-of-order/interrupt/rewind (don't leave half-finished UI), and the headless `codex exec` consumes the same stream as the TUI (display thoroughly peeled from production, see the "Production-grade" section).
- Next stop, [s12 More tools](../s12_tools_extra/README.en.md): add tools like `plan` / `web_search` / `view_image` to the agent — what they produce is also just new events on this same event stream.

## Think it over

<div class="think">

1. In the teaching version every event "lands in one beat," so the widget is a stateless pure function. But the real TUI has to handle **streaming increments** like `ExecCommandOutputDelta` — the output of the same command arrives segment by segment. Can the widget still be a pure function then? Where would you put the state of "the output received so far" — in the widget, in the renderer, or in the core?
2. "The core holds no rendering, the frontend holds no logic" sounds clean. But approval ([s10]) inherently requires the TUI to pop up a box, wait for the user to click, then send the decision back to the core via SQ — does this count as "business logic" leaking into the frontend? Who should decide "this command needs approval," and who should decide "what the popup looks like"? And on what grounds can the headless exec skip this step?
3. The same event stream has to be fed to three frontends (terminal / CI / IDE). Notice that in this chapter the TUI side (`EventMsg`) and the exec side (`ThreadEvent`) are actually **two** sets of event naming — why doesn't real codex just use one set? If some event is only useful to the TUI (say, a purely decorative animation hint), is it appropriate to put it into the externally stable `ThreadEvent` contract? Where would you draw this boundary?
4. If some command **itself** prints a line in stdout that looks like JSON, will your `| jq` pipeline mistake it for an event? Real codex uses stderr/stdout separation + a fixed schema to guard against this — what other kinds of "output pollution" can you think of that would trip up downstream scripts?
5. Is an exit code of just 0/1 enough? If a turn "partially succeeds" (2 of 3 commands succeed, 1 fails), should CI treat it as success or failure? If it were up to you, how would you design this exit-code semantics?
6. If you were to add a fourth frontend to Codex — say, a web dashboard — under this chapter's architecture, what would you need to write, and what would you **not** need to touch? Conversely: with Claude Code's "UI fairly tightly coupled to the loop" style, what extra cost would adding this dashboard incur?

</div>

[s10]: ../s10_sq_eq_protocol/README.en.md
