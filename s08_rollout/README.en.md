# s08: Rollout — Etching the Entire Conversation onto Disk

> 🌐 **English** · [中文版](README.md)

> *"Context is the draft the model reads; rollout is the master copy you leave for the world."*

[learn-codex overview](../README.en.md) · [s07 Context Compaction](../s07_context_compaction/README.en.md) → **s08** → [s09 Responses API](../s09_responses_api/README.en.md)

---

## Get the idea straight first: split "what the model sees now" and "what the world keeps forever" into two copies

To save money, the previous chapter [s07](../s07_context_compaction/README.en.md) actively shortens old conversation — even replacing it with a one-line summary. So a question arises: if the history you feed the model keeps getting trimmed shorter, **who still remembers in full "what actually happened" in that conversation?** This chapter's answer hides in a design that looks simple yet props up three big things: resume / rewind / audit. Grasping the three layers below is enough.

**Point one: the history you feed the model is a "working copy that's allowed to decay" — it was never meant to be the authoritative record.**
Recall the previous chapters: while the program runs, the conversation is just a `messages` list in memory, serving one purpose — "what to feed the model this turn." Precisely because it serves the model, it **tolerates loss**: too long, compact it (s07); old, truncate it; the process exits and it vanishes into thin air. Treating it as scratch paper is the most apt analogy — you scribble on it, erase and rewrite at any time, which helps your thinking right now, but **nobody expects scratch paper to last**. The trouble is: if the whole system has **only** this scratch paper, then one closed terminal, one compaction, and "what this conversation did" is gone forever.

**Point two: so save another copy — "append-only, never-modified, word-for-word complete" — which is a different beast from the scratch paper.**
The fix is plain to the point of looking dumb: every time something concrete happens in a turn (you say a sentence, the model says a sentence, the model wants to run a command, the command returns a result), **honestly append one line to the end of a file**, and never go back and alter lines already written. That file is the rollout — a **line-by-line ledger**. Its division of labor with the scratch paper is crystal clear: the scratch paper serves "what the model needs to see right now" and may decay; the master copy serves "what the world needs to look up afterward" and must stay complete. The keys are just two actions: **writing only appends to the end (never tampering with old lines); reading replays from the start in order.** "Append-only" guarantees history is never rewritten — exactly the foundation for later using it as audit evidence; forcing each written line down to the hard disk immediately (rather than letting it pile up in a buffer) guarantees that even if the process crashes the next second, what already happened lies safely on disk and isn't run in vain.

**Point three (the most crucial): it's precisely this clean cut — "scratch paper may decay / master copy may not" — that buys you resume, rewind, and audit all at once.**
Why insist on splitting these two copies? Because once the disk holds a master copy that's "sliced by turn, complete and intact," three things that were originally impossible all fall into place:
- **resume**: read the master copy back from the start, rebuild it into `messages`, and the session can pick up from where it last broke off — switch machines, come back days later, whatever, as long as the file is still there.
- **rewind**: the master copy is sliced by turn, so you can "lop off the last N turns and roll back to an earlier state to redo" — especially useful when the model wanders into a dead end.
- **audit**: when something goes wrong in the cloud, unattended, you flip back through this word-for-word ledger afterward and learn "what command it actually ran, how the model made its decisions."

These three things share one prerequisite: **the disk must hold a complete record that won't be compacted and won't be tampered with.** This precisely answers the previous chapter's worry — compaction can prune the scratch paper with full confidence (saving tokens), because the master-copy line stays complete throughout. Two records, each minding its own job, never clashing — that's the entire idea of rollout.

## Problem

The agent loop ([s01](../s01_agent_loop/README.en.md)) runs great — but it stores the entire conversation only in **a `messages` list in memory**. Once the process exits, that conversation vanishes into thin air. This brings three real pain points:

1. **Can't resume.** You ran for 40 minutes, the agent changed a dozen files, the terminal closes / the CI job ends, and next time you can only start from scratch.
2. **Can't rewind.** The model wandered into a dead end on turn 7, you want to roll back to turn 5 and redo — but you don't have a "turn 5" in hand.
3. **Can't audit.** Running an agent in the cloud / unattended, something goes wrong afterward, you want to know "what command it actually executed, what the model said" — but the in-memory conversation is long gone.

Even trickier: [s07](../s07_context_compaction/README.en.md)'s **compaction**, to save tokens, actively **drops** old conversation (replacing a stretch of history with a one-line summary). The context fed to the model keeps getting trimmed shorter, yet the "complete record" you wanted gets wrecked by it instead.

## Solution

**Persist the conversation line by line**: every time a turn produces an item (user message, model message, tool call, tool result), **append a line** to a rollout file. On read-back, **replay** those lines into `messages`, and the session can continue from the breakpoint.

The teaching version uses JSONL (one item per line, append-only, human-readable). The file looks like this:

```
   rollout.jsonl  (append-only，每行一个 RolloutItem)
   ┌────────────────────────────────────────────────────────┐
   │ {"timestamp": ..., "type":"session_meta", "payload":{…}} │ ← 第一行：会话头
   │ {"timestamp": ..., "type":"response_item","payload":{    │
   │     "type":"message","role":"user","content":"…"}}       │ ← 你说的话
   │ {…"type":"function_call","name":"shell",…}               │ ← 模型要跑的命令
   │ {…"type":"function_call_output","output":"…"}            │ ← 命令的结果
   │ {…"type":"message","role":"assistant","content":"…"}     │ ← 模型的回复
   └────────────────────────────────────────────────────────┘
       │  record_items() 一边跑一边往下追加 ↑（写完即 flush）
       │
       ▼  resume(path) 逐行读回，跳过 session_meta
   messages = [ {user…}, {function_call…}, {output…}, {assistant…} ]  → 接着跑
```

The point isn't "it's stored," but: **writing is append-only, reading is in-order replay**. Compaction may prune the in-memory context however it likes; the rollout — this master copy — stays complete throughout.

## How it works

Look at [code.py](code.py), three things:

**Step 1 — spin up a recorder, write the session header first.** Real Codex's rollout always has `SessionMeta` as its first line (conversation_id, cwd, git info…):

```python
class RolloutRecorder:
    def __init__(self, path, meta=None):
        self._write_line("session_meta", meta or {"cwd": str(WORKDIR)})
```

**Step 2 — every time a turn produces an item, append a line, flush right after writing.** This is exactly the semantics of `JsonlWriter::write_line` in the real source [`recorder.rs`](../../codex/codex-rs/rollout/src/recorder.rs) (`flush` immediately after `write_all`) — a crash won't lose what's already written:

```python
def _write_line(self, item_type, payload):
    line = {"timestamp": ..., "type": item_type, "payload": payload}
    with self.path.open("a", encoding="utf-8") as f:
        f.write(json.dumps(line, ensure_ascii=False) + "\n")
        f.flush()
```

`run_turn(messages, recorder)` adds only two lines on top of the s01 loop: after the model produces output `recorder.record_items(...)`, and after the tool result is produced `record_items([out_item])` again.

**Step 3 — `resume(path)` replays the lines into `messages`.** Corresponding to `recorder.rs`'s `get_rollout_history` / `load_rollout_items`: parse line by line, skip `session_meta`, restore each `response_item`'s payload into a conversation item, skip corrupted lines outright:

```python
def resume(path):
    messages = []
    for raw in Path(path).read_text().splitlines():
        line = json.loads(raw)                 # 损坏行 try/except 跳过
        if line.get("type") == "response_item":
            messages.append(line["payload"])
    return messages
```

`--demo` runs the whole thing through: record one round of `echo` → print the 5 lines of JSONL written to disk → `resume` replays → assert "the restored `messages` is byte-for-byte identical to what was recorded," proving the session can be resumed losslessly.

**Walk through it** —— follow `--demo` and watch one conversation go from "memory" to "disk" and back to "memory," seeing what the data looks like at each step:

1. **Spin up the recorder, write the first line — the session header.** The user says "run `echo hello from codex` and tell me the result." The recorder first writes a line of `session_meta` (session header), then writes the user message as a line of `response_item`:

   ```json
   {"timestamp":"…Z","type":"session_meta","payload":{"cwd":"…/learn-codex","conversation_id":"demo-0001"}}
   {"timestamp":"…Z","type":"response_item","payload":{"type":"message","role":"user","content":"执行 `echo hello from codex` 并告诉我结果"}}
   ```

   *Why* the first line is always the session header: on resume you first need to know "which session is this, in which directory," before reading the conversation that follows.

2. **Every time a turn produces an item, append a line + flush.** The model decides to call `shell`, runs it and gets the result, and finally replies with a sentence. These three things each write a line (note they're all **appends** — not a single character of the earlier lines is touched):

   ```json
   {…"type":"response_item","payload":{"type":"function_call","call_id":"mock_call_1","name":"shell","arguments":"{\"command\": \"echo hello from codex\"}"}}
   {…"type":"response_item","payload":{"type":"function_call_output","call_id":"mock_call_1","output":"hello from codex"}}
   {…"type":"response_item","payload":{"type":"message","role":"assistant","content":"[mock] 工具已执行，结果片段：hello from codex"}}
   ```

   *Why* flush right after writing with `flush()`: should the process crash right here, the command already run and the result already obtained both lie safely on disk and aren't run in vain.

3. **resume: read back line by line, skip the session header, rebuild `messages`.** The file now has 5 lines (1 session header + 4 conversation items). `resume()` does `json.loads` line by line, **skips `session_meta`**, takes the `payload` of the 4 `response_item`s, and puts them in the list in order:

   ```
   重建出 4 个对话 item：
     [user]      执行 `echo hello from codex` 并告诉我结果
     [call]      shell {"command": "echo hello from codex"}
     [output]    hello from codex
     [assistant] [mock] 工具已执行，结果片段：hello from codex
   ```

   *Why* skip the session header: it's "meta-info about this session," not the conversation content itself, and must not be mixed into the `messages` fed to the model.

4. **Assert losslessness.** At the end the demo runs `assert restored == messages` — the list read back is **byte-for-byte identical** to what was recorded. This is the hard proof that "a session can be resumed losslessly": switch machines, come back days later, and as long as the file is still there, the conversation can pick up from here.

> Against the real source: this "write a line + flush" is exactly the semantics of `JsonlWriter::write_line` in `recorder.rs` (`flush` immediately after `write_all`); resume corresponds to `get_rollout_history` / `load_rollout_items`. The teaching version drops the SQLite index and zstd compression, but the skeleton of "append-only + in-order replay" is identical.

## Production-grade: the master copy must survive a power cut — append-only + flush + retryable

The rollout is Codex's "black box": resume, rewind, and audit all rely on it. So the one thing it can least tolerate is a **lost write**. Production-grade persistence watches three things:

- **append-only**: every time an item is produced, **append a line** (`.append(true)`, [`recorder.rs:727`](../../codex/codex-rs/rollout/src/recorder.rs)), never going back to alter what's written. A crash loses at most the **last line** (the one written halfway through); everything before is all there — on replay just skip the corrupted trailing line. This is why append-only is far steadier than "whole-file overwrite" in the face of a crash.
- **explicit flush**: `flush()` (recorder.rs:825) ensures "all prior writes have landed on disk." Flush before resume / at critical junctures, and even a power cut won't lose a confirmed turn.
- **failure is retryable**: a failed open/write isn't fatal — note it down, "a later `persist()` or `flush()` can retry" (recorder.rs:803). Disk momentarily full, file locked — write again next time, rather than dropping this whole stretch of session.

> In one sentence: a master-copy system's production-grade quality lies not in "what it recorded," but in "**how much it has left when the power cuts / it crashes**." append-only + flush + retryable exist to make the answer "almost all of it." Compaction ([s07](../s07_context_compaction/README.en.md)) saves the window; rollout safeguards this master copy that survives a power cut.

## 🆚 How it differs from Claude Code

| | Claude Code | Codex |
|---|---|---|
| Persistence target | session history (conversation record, mainly for local continued chat) | **full rollout**: every item, tool call, error, token count lands on disk |
| Capability | resume a session | resume (continue running) + **rewind (roll back N turns)** + **audit** |
| Who consumes it | local interaction | the same rollout simultaneously drives **local TUI / `codex exec` (headless) / Codex Web (cloud)** |
| Relationship with compaction | —— | compaction prunes context, rollout still keeps the full raw history; the two are **orthogonal** |

**Why?** Because Codex bets it all on "**low-human-intervention autonomous running**" — `codex exec` in CI, an agent in the cloud, **with nobody watching the terminal**. This kind of scenario's demands on persistence are an order of magnitude beyond "local interactive continued chat":

- **Cloud + headless needs "persistence"**: the process may be scheduled away or restarted at any time; the conversation must have an authoritative copy on disk, so a different machine can `resume` and pick up.
- **Autonomous running needs "auditability"**: nobody gatekeeps in real time, so after-the-fact accountability rests on this line-by-line record — what it actually ran, how the model decided, not a line missing.
- **Multiple frontends need "a single source of truth"**: the session you opened on the web, you can keep working on it back at the terminal with `codex exec --resume`, because everyone reads the same rollout.

This is exactly the projection of the whole course's main thread onto persistence: Claude Code, centered on **interactive UX**, stores "enough history for continued chat"; Codex, for **headless / CI / cloud**, stores "a complete master copy that's resumable, rewindable, and auditable."

## Deep dive: teaching version vs. real Codex source

The teaching version's `RolloutRecorder` is about 40 lines and one JSONL file. Real Codex's rollout subsystem is `codex-rs/rollout/` (the recorder alone is 1600+ lines in a single file), plus a whole `codex-rs/state/` SQLite runtime. The four blocks below make the gap clear.

<details>
<summary>1. Storage: teaching version is JSONL; the real version is JSONL session files + SQLite state.db + zstd</summary>

The teaching version takes the easy route with a single `rollout.jsonl`. Real Codex is **two storage layers coexisting**:

| Dimension | Teaching version | Real codex-rs |
|---|---|---|
| Session file | `_demo_workspace/rollout.jsonl` | `~/.codex/sessions/YYYY/MM/DD/rollout-<time>-<id>.jsonl` |
| Filename | fixed | `rollout-2025-05-07T17-24-21-<uuid>.jsonl` (colons swapped for `-`, filesystem-compatible) |
| Index / listing | none | **SQLite `state.db`** (`codex-rs/state/`, `StateRuntime`) for thread listing, search, pagination |
| Cold data | never compressed | a background worker compresses cold files into **`.jsonl.zst`** (`compression.rs`, depends on `zstd`) |
| Appending to a compressed file | —— | first `materialize_rollout_for_append` decompresses the `.zst` back to `.jsonl`, then appends |

Why does the real version need SQLite? Because `codex resume` has to **sort by time, paginate, and full-text search** across thousands of historical sessions — iterating over a pile of JSONL files can't do any of that, while SQLite's index is naturally good at it. JSONL is still the **authoritative line-by-line record** (a human can `jq` it directly); SQLite is **a queryable index layer on top of it**. Stuffing the timestamp and UUID into the filename, in turn, lets you sort and locate a session from the filename without even opening the database.

```rust
// recorder.rs：每行就是 {timestamp} + 一个 flatten 进来的 RolloutItem
let line = RolloutLineRef { timestamp, item: rollout_item };
let mut json = serde_json::to_string(item)?;
json.push('\n');
self.file.write_all(json.as_bytes()).await?;
self.file.flush().await?;          // ← 教学版照搬了这个「写完即 flush」
```

</details>

<details>
<summary>2. What RolloutItem actually records: five variants + a persistence policy</summary>

The teaching version records only two kinds (`session_meta` / `response_item`). The real `RolloutItem` is an enum with five variants (`protocol.rs:2827`):

```rust
pub enum RolloutItem {
    SessionMeta(SessionMetaLine),   // 会话头：id / cwd / git / 版本 / 模型 provider…
    ResponseItem(ResponseItem),     // 对话本体：message / function_call / output / reasoning…
    Compacted(CompactedItem),       // ★ 压缩标记：被换掉的历史 + 替代摘要（与 s07 直接相关）
    TurnContext(TurnContextItem),   // 回合上下文快照（当时的 cwd / 审批策略 / 模型…）
    EventMsg(EventMsg),             // 少数关键事件（token 计数、patch 应用结果、回退…）
}
```

Moreover, the real version **doesn't record everything** — `policy.rs` has a set of `is_persisted_rollout_item` / `should_persist_response_item`: those that enter history, like `Message / FunctionCall / FunctionCallOutput / Reasoning`, are **recorded**; purely incremental `*Delta`, `ExecApprovalRequest`, `McpStartupUpdate` and such — **transient/UI events are not recorded**. So the rollout holds both "what the model said, what it ran" and "how many tokens it spent, whether the patch applied," but won't get flooded by every lifecycle event.

The teaching version simplifies this policy into "record everything," because our item kinds are few to begin with.

</details>

<details>
<summary>3. resume and rewind: continue running, and roll back N turns</summary>

The teaching version's `resume()` = read the whole file → restore `messages`. The real version's `get_rollout_history` (`recorder.rs:912`) does several more things: parse out the `conversation_id`, wrap the result into `InitialHistory::Resumed { conversation_id, history, rollout_path }` (an empty file returns `InitialHistory::New`), and transparently read back from `.jsonl.zst`.

**rewind**, then, is another "time operation" beyond resume, triggered by a standalone `Op` (`protocol.rs:583`):

```rust
/// 把内存上下文里最后 N 个用户回合丢掉。
/// 注意：它不负责回滚磁盘上的文件改动——那由客户端自己撤销。
ThreadRollback { num_turns: u32 },
```

In other words: **resume is "read the master copy back and continue from the tail," rewind is "lop N segments off the tail and redo."** Both rest on "having a complete record sliced by turn" — without rollout, neither is even conceivable. On failure there's also a dedicated `ThreadRollbackFailed` error code.

</details>

<details>
<summary>4. Division of labor with compaction (s07): one prunes context, one safeguards the master copy</summary>

This is the most easily confused, and the most telling about design intent. [s07](../s07_context_compaction/README.en.md)'s compaction **actively drops history**: replacing a long stretch of old conversation with a one-line summary, so the tokens fed to the model shrink. If the rollout got compacted along with the context, then "the complete record" would be empty talk.

Real Codex's solution makes the two **orthogonal**: compaction itself is also recorded into the rollout as a `RolloutItem::Compacted` — it preserves "the swapped-out original history `replacement_history` + the substitute summary `message`" (`protocol.rs:2836`). So:

| | Context fed to the model | rollout master copy |
|---|---|---|
| When compaction happens | old conversation → replaced by summary, shrinks | append a `Compacted`; **the original history is still in the file** |
| On resume | continue with the compacted context (saving tokens) | can still read the full pre-compaction record (auditable) |

In one sentence: **compaction optimizes "the money you spend right now," rollout safeguards "the account you can trace afterward."** The two don't clash, precisely because the compaction action itself is also faithfully recorded into the master copy.

</details>

## Run

```bash
python s08_rollout/code.py --demo   # 录制 → 落盘 jsonl → resume 回放（mock，无需 key，自动清理）
python s08_rollout/code.py          # 交互模式：你的每句话、每次工具调用都被记进 rollout
```

By default `backend=mock`, runs offline. `--demo` temporarily writes a `rollout.jsonl` under `_demo_workspace/`, and auto-`rmtree`-cleans it up when done.

## Recap

- Every time a turn produces an item, **append a line** to disk (append-only + flush right after writing); `resume` replays line by line into `messages`, and the session continues from the breakpoint.
- Real Codex uses **SQLite (state.db) for indexing + JSONL session files + zstd compression for cold files**; `RolloutItem` has 5 variants, plus a "what to record / what not to record" persistence policy.
- rollout is the common foundation for **resume / rewind (`ThreadRollback`) / audit**, and also the single source of truth shared by Codex Web and `codex exec`.
- It's **orthogonal** to compaction ([s07](../s07_context_compaction/README.en.md)): compaction saves tokens, rollout keeps the full master copy (even the compaction action itself gets recorded).
- **Production-grade**: the master copy must survive a power cut — append-only (a crash loses at most the last line), `flush()` to disk at critical points, a failed write is retryable without losing the session (see the "Production-grade" section).
- Next stop [s09 Responses API](../s09_responses_api/README.en.md): this chapter has been recording and replaying the `message` / `function_call` / `function_call_output` item set all along — the next chapter pops the hood and looks at where this shape actually comes from and how the model is actually called.

## Think it over

1. The teaching version's `resume` reads the whole file into memory then replays. If a session ran for three days and the rollout has hundreds of thousands of lines (compressed into `.zst` too), what goes wrong with this "read it all back" approach? Why does the real version pile a SQLite index on top of JSONL — could filenames alone hold up under "paginate by time + full-text search"?

2. `ThreadRollback`'s comment says explicitly: it only drops the turns in the in-memory context, **it does not roll back the file changes that already happened on disk**. So after "rewind to turn 5," the workspace is actually still stuck in the turn-7 state — would this mismatch of "conversation rolled back, files didn't" make the model even more confused? In your shoes, would you have rewind also undo the file changes, and at what cost?

3. rollout **permanently keeps** every sentence the model says, every command it runs, even token spend. For "cloud / unattended" this is a hard audit requirement, but the same master copy also means sensitive info (keys, customer data) gets written to disk as-is. Codex chooses "record everything + kernel-level sandbox as a backstop"; Claude Code chooses "lightweight history + interactive approval" — if you were setting the policy, which items are worth keeping permanently, and which should be redacted or just not recorded?

4. Compaction (s07) drops history to save tokens, yet rollout insists on keeping the full set — this chapter says the two are "orthogonal," relying on recording the compaction action itself as a `Compacted` item. But if even "the swapped-out original history" is stored into the rollout too, then where exactly does the disk that compaction saves get saved? In what scenario will this "context saves, master copy doesn't" trade-off bite you back?
