# s07: Context Compaction — when a conversation gets too long, squash old turns into a one-line summary

> 🌐 **English** · [中文版](README.md)

> *"The context window is finite; rather than slamming into the wall, squash the road you've traveled into a map."*

[learn-codex overview](../README.en.md) · [s06 AGENTS.md](../s06_agents_md/README.en.md) → **s07** → [s08 Rollout resume](../s08_rollout/README.en.md)

---

## Get the idea straight first: context is finite, so "use the cheap tricks first, only invoke the model as a last resort"

Every time you talk to the model, you have to repackage "the entire conversation so far" and send it again — the model itself remembers nothing; it relies on you re-feeding it the whole history each round. Here's the problem: this pile of history keeps growing, and the amount the model can swallow in one go has an **upper limit**. This chapter answers a very practical question: **the history no longer fits — now what?** Grasp the three layers below and you've got all of its cleverness.

**Truth one: the model's "memory" is really that pile of history you resend each time, and it has a physical ceiling.**
You can picture each call to the model as "stuffing a stack of paper into a fixed-size envelope" — that stack is all the conversation so far, and the envelope size is the model's per-call reading limit. The stack keeps getting thicker: read a file, run a command, look at some output — every step adds a few sheets. When it won't fit, two bad things happen: either it bursts outright, errors, and the whole turn crashes; or it barely squeezes in, but the model gets buried under dozens of pages of ancient detail and grows more confused the more it reads. So the tension between "history grows without bound" and "envelope size is fixed" must eventually be confronted head-on.

**Truth two: the dumbest fix is "just throw away the oldest paper," but that throws away key decisions — what you really want is "condense," not "discard."**
The laziest idea is: when it's full, tear off the earliest few sheets. But those sheets might say "after weighing the options we decided to use plan B and dropped A" — tear them off and the moment the model looks back it has amnesia, agonizing again over things long since settled. So the smart move isn't to throw away, but to **condense**: replace a long, rambling stretch of old process with a short, distilled "handoff summary." This is exactly like a job handover — you'd never dump three months of chat logs raw on your successor; instead you write one page: "where we are now, what decisions were made, what's still missing." The original text is gone, but **the key points remain**. That's the true meaning of "compaction": replace a big chunk with a single sentence, and empty out the envelope again.

**Truth three (the most crucial): condensing costs money, so the order is "use the cheap tricks first, and only when forced do you invoke the most expensive one."**
How do you condense a big chunk of conversation into a single sentence? The smartest, most accurate way is to **have the model read it again and write its own summary** — but that amounts to one extra phone call, slow and costly. So the real engineering wisdom lies not in "being able to compact" but in **being frugal with this trick**: if you can free up room with cheap means, never lightly disturb the model. Hence a ladder from cheap to expensive — first **truncate in place** the long, foul-smelling tool outputs (almost free), keep the most recent details **as-is** (free of charge), and only when that's still not enough do you **hand the oldest big chunk to the model to summarize** (most expensive, saved for last). In this chapter's offline demo, even the "ask the model" step is stood in for by a deterministic concatenation rule, but the spirit of the ordering is identical: **cheap truncation up front, expensive model summary as the floor.**

There's one more thing that lets you compact with peace of mind: **what gets compacted away doesn't actually disappear**. The history fed to the model is just "the working copy needed right now," which can be trimmed freely; meanwhile a complete, word-for-word original record lives on disk (that's the rollout of the next chapter, [s08](../s08_rollout/README.en.md)). Precisely because "the money-saving copy" and "the preserved master draft" are two different things, you dare to compact the working copy aggressively — the sky won't fall.

Stringing these four points together: the envelope has a limit → you can't discard, only condense → condensing follows the order "cheap first, model summary as the floor" → and what you compact is only the working copy, with the original kept separately. That's the entirety of the context-compaction idea.

## Problem

When an agent works on something complex, it often takes dozens of round trips: read files, run commands, look at output, edit again, run again… and every step piles messages and tool outputs into the conversation history. But the model's **context window is finite** — pile up to a certain point and either it reports "context window exceeded" outright and the turn crashes, or it doesn't crash but the model gets buried under a mountain of ancient detail and grows less and less accurate.

The most naive approach is "drop the oldest messages." But this drops key decisions ("we previously decided to use plan B rather than A"), and the moment the model looks back it has amnesia.

What you really want is: **without exceeding the window, preserve as much as possible of "what has already happened, which decisions were made, and what's still left to do"** — squash the verbose intermediate process into a distilled handoff summary, and keep the most recent details as-is.

## Solution

When the message list exceeds the budget (this chapter uses **total character count** as a cheap proxy for tokens), trigger compaction:

1. Slice the list into "the oldest batch" and "the most recent N";
2. Hand the oldest batch to the model (offline: a deterministic heuristic) to summarize into **one** `[summary]` message;
3. New history = `[summary]` + `[most recent N items]`.

```
   压缩前（超预算）                      压缩后（回到预算内）
   ┌─────────────────────┐             ┌─────────────────────┐
   │ user  第1步…         │  ┐          │ [summary] 压了最早    │
   │ tool_call shell      │  │ 最旧      │   13 项，要点回顾：    │ ← 一条摘要
   │ tool_result …(长)    │  │ 一批      │   - 第1步 列目录       │
   │ user  第2步…         │  │ 压成      │   - 第2步 读 README   │
   │ …（共 13 项）        │  ┘ 摘要      │   - …                 │
   │ user  第5步…         │  ┐          ├─────────────────────┤
   │ tool_call shell      │  │ 最近      │ user  第5步…          │
   │ tool_result …        │  │ 6 项      │ tool_call shell       │ ← 原样保留
   │ …                    │  │ 原样      │ tool_result …         │
   │ user  请总结一下      │  ┘ 保留      │ user  请总结一下       │
   └─────────────────────┘             └─────────────────────┘
        19 项                                 1 + 6 = 7 项
```

The summary itself is encoded as a single `user` message (this matches the real source), tagged with a prefix (the teaching version's `[summary]`, the real source's `SUMMARY_PREFIX`) to mark its identity, making it easy to recognize later and to avoid being compacted a second time.

## How it works

See [code.py](code.py):

**Trigger** — `total_chars(messages)` sums the character count of all visible text (including tool arguments/output) and uses it as a token-usage proxy; exceed `BUDGET_CHARS` and it compacts:

```python
def compact(messages, model=None):
    if total_chars(messages) <= BUDGET_CHARS or len(messages) <= KEEP_RECENT:
        return messages                       # 没超预算，原样返回
    split = len(messages) - KEEP_RECENT
    old, recent = messages[:split], messages[split:]
    summary_item = user_item(summarize(old, model))   # 旧回合 → 一条摘要
    return [summary_item, *recent]            # 摘要 + 最近 N 项
```

**Summary** — when offline, `summarize` takes a deterministic heuristic path: it squashes each old item into one line via `item_text` (user messages truncated, tool calls keep only the name, tool output heavily trimmed), then concatenates them into a "key-points recap." In the real source this is where the old history is sent to the model and `SUMMARIZATION_PROMPT` has it produce a handoff summary — but the mechanical skeleton is the same: **many old items → one new item**.

**Wiring into the turn** — `run_turn` runs `compact()` once at the start of every turn (proactive compaction), printing the `before → after` change in item count.

This corresponds to the real source [`core/src/compact.rs`](../../codex/codex-rs/core/src/compact.rs): `build_compacted_history` assembles the new history of "recent user messages + summary," `SUMMARY_PREFIX` tags the summary, and `is_summary_message` does prefix-based recognition. The trigger splits into two paths: **proactive** (in `session/turn.rs`, calling `run_auto_compact` when `token_limit_reached`) and **reactive** (when the `compact.rs` main loop hits `CodexErr::ContextWindowExceeded`, it removes the oldest item from the front and retries). On top of this there are two **server-side** compaction variants, [`compact_remote.rs`](../../codex/codex-rs/core/src/compact_remote.rs) and [`compact_remote_v2.rs`](../../codex/codex-rs/core/src/compact_remote_v2.rs).

`--demo` demonstrates it directly: build a fake conversation of about 20 items, trigger compaction with a tiny budget, and print before/after item counts plus the produced summary (offline, deterministic, no model needed).

**Walk through it** — follow one real compaction under `--demo` and see what the data looks like at each step and why it's done this way:

1. **Build a long conversation.** `build_long_conversation()` produces 19 items: 6 rounds of "user asks → tool call → tool result" plus one closing line. The first few items look like this:

   ```
   user:         第1步：请列出目录。
   function_call: shell  {"command": "do-列出目录"}
   function_call_output: 列出目录 的输出 xxxxxxxx…(故意撑到 40 个 x，模拟冗长输出)
   user:         第2步：请读 README。
   …（如此 6 轮）…
   user:         好的，最后请帮我总结一下我们刚才做了哪些事。
   ```

2. **Do the math, find it's over budget.** `total_chars(messages)` sums all visible-text characters = **532 characters**, while `BUDGET_CHARS = 400`. 532 > 400 → compaction triggers. *Why*: this is the simulation of "about to burst the window" — in the real source this is replaced by "estimated token count approaching the window limit."

3. **Slice into two parts.** `KEEP_RECENT = 6`, so `split = 19 - 6 = 13`: the oldest **13 items** are to be compacted, the most recent **6 items** are kept as-is. *Why* keep the recent ones: the just-happened details are most useful for "carrying on," while the old process is what can be condensed.

4. **Squash the old 13 items into one summary.** `summarize(old)` squashes each item into one line (user messages truncated, tool calls keeping only the name `shell`, tool output heavily trimmed) and concatenates them into a single message with the `[summary]` prefix:

   ```
   [summary] 压缩了最早的 13 个对话项；要点回顾：
     - user: 第1步：请列出目录。
     - tool_call: shell
     - tool_result: 列出目录 的输出 xxxxxxxxxxxxxxx…
     - user: 第2步：请读 README。
     - …
     - user: 第5步：请看依赖版本。
   ```

   *Why* tool output gets cut the hardest: when recapping, you only need to know "it ran, roughly what came out"; those 40 x's have no retention value.

5. **Assemble the new history.** `[summary] + most recent 6 items` = **7 items**. 19 → 7, the conversation has been compacted back to budget scale, but "what was done" still lives in the summary and the most recent context is untouched. The demo finally asserts `the first item is the summary AND the total count == 1 + 6`, and the check passes.

> One detail: after compaction the character count may rise rather than fall (532 → 575 in the demo), because every item of this fake conversation is very short while the summary lists out the key points of all 13 items. In a real scenario what gets compacted is tool output that's **both numerous and long**, and however verbose the summary is it's still far shorter than the original — here it's just that the item count drops while character savings aren't obvious, which doesn't affect the mechanism demo.

## Production-grade: estimates drift — reactive compaction is the last gate

Proactive compaction relies on **estimation** (character count as a token proxy) to decide when to compact. But estimates always drift: different tokenizers, the budget reasoning consumes, sudden bloat in tool output… there's always a moment when you think you haven't exceeded but in fact you've **slammed into the model's hard limit**. At that point resending as-is just slams into it again. Production-grade must have a **reactive fallback**: when you really hit `ContextWindowExceeded`, **delete the oldest item from the front and retry**, until it fits. This corresponds to `history.remove_first_item()` + `retries = 0` retry in the [`compact.rs`](../../codex/codex-rs/core/src/compact.rs) main loop.

This chapter's `--demo` demonstrates this: proactive thinks all is well, but the model's hard limit is tighter (about 12 items), and after hitting the wall it automatically trims item by item and retries:

```
proactive 以为没事，但模型硬上限约 12 项——撞墙后自动从头删项重试：
  ⚠ ContextWindowExceeded → 删最旧一项（user: 第1步：请列出目录。…），剩 18 项后重试
  ⚠ ContextWindowExceeded → 删最旧一项（tool_call: shell…），剩 17 项后重试
  ...
  → ok：12 项放下了，回合成功
```

Neither path is dispensable: **proactive saves money** (compact early, burn fewer tokens), **reactive saves your life** (even if the estimate is wrong, the whole turn doesn't fail). One is an optimization, the other a correctness fallback — real Codex runs both at once (`run_auto_compact` in turn.rs + the wall-hitting retry in compact.rs), not either-or.

> One more production-grade detail: it deletes the **oldest** item rather than a random one, because the most recent context is most valuable for "carrying on"; and the full history that was compacted away isn't lost — it's still in the rollout ([s08](../s08_rollout/README.en.md)), replayable and auditable. Compaction saves the **context window**, not the **master draft**.

## 🆚 How it differs from Claude Code

The two are **very similar** (≈) on compaction — both summarize history into a summary to fit the context:

| | Claude Code | Codex |
|---|---|---|
| Core idea | Summarize old history to fit the window (≈) | Summarize old history to fit the window (≈) |
| Trigger | Compact when approaching the window limit (proactive + reactive retry) | Same: proactive (token limit) + reactive (`ContextWindowExceeded`) |
| Where compaction runs | **Client / local** | Local **plus server** (`compact_remote` / `compact_remote_v2`) |
| Summary carrier | A summary message | One `user`-role summary message (`SUMMARY_PREFIX` prefix) |
| Relationship to the full record | — | Compaction only changes the "active history"; the full history is persisted separately by the rollout ([s08]) |

**Why does Codex have an extra server-side compaction path?** Still that same through-line: Codex bets on **headless / CI / cloud** autonomous operation. A cloud Codex agent can let the **server** compact the session directly (`compact_remote`, and even `compact_remote_v2` with a rollout trace), with the client merely consuming the result — which is cheaper and more stable for long-running unattended cloud tasks. And the local `run_inline_auto_compact_task` serves as the **fallback**: when the provider doesn't support remote compaction (`should_use_remote_compact_task` is false), it goes local. Claude Code centers on local interactive use, where putting compaction on the client is already sufficient, with no "the server compacts for you" path.

[s08]: ../s08_rollout/README.en.md

## Deep dive: teaching version vs the real Codex source

<details>
<summary>1. Trigger: proactive compaction vs reactive after-the-wall compaction</summary>

The teaching version has only one trigger: compact before each turn when `total_chars > BUDGET_CHARS` (proactive). The real source runs both paths:

| Path | Trigger point | Real source |
|---|---|---|
| **Proactive** | Estimated tokens approach `model_auto_compact_token_limit` | `session/turn.rs`: `token_limit_reached && needs_follow_up` → `run_auto_compact(...)` |
| **Reactive** | Actually hitting `CodexErr::ContextWindowExceeded` | `compact.rs` main loop: `history.remove_first_item()` deletes one item from the front, `retries = 0` retry |

The reactive path is crucial: even if the estimate drifts and the window really does burst, it doesn't simply crash — instead it **deletes items one by one from the very front (the oldest)** and retries. Deleting from the front is to **preserve the prefix cache**: the model API's cache hits by prefix, so keeping the later, recent messages is more economical. The teaching version's character proxy is just a bare-bones version of "token estimation"; the real source's token accounting is far more refined (distinguishing scope, window ordinal, prefill, etc.).

</details>

<details>
<summary>2. What's kept after compaction: recent user messages + summary, with tool output trimmed</summary>

The teaching version's new history = `[summary] + most recent KEEP_RECENT items`. The real source's `build_compacted_history` is more careful:

```rust
// compact.rs：新历史 = [初始上下文] + [近期 user 消息（按 token 预算从后往前选）] + [摘要]
let mut new_history = build_compacted_history(Vec::new(), &user_messages, &summary_text);
```

- **Keep only "real user messages"**: `collect_user_messages` filters out the summary message itself (`is_summary_message`), avoiding a summary nesting summaries.
- **Select from the back forward by token budget**: `COMPACT_USER_MESSAGE_MAX_TOKENS = 20_000`, gathering backward from the most recent user message, and `truncate_text` cuts when over budget.
- **Re-inject the initial context**: compaction clears the history, so `InitialContextInjection` decides whether to **re-pad** the "initial context" (environment, AGENTS.md, etc.) back in. Mid-turn compaction uses `BeforeLastUserMessage` (padded before the last real user message, because the model is trained to expect "the compaction summary should be the last item of history"); manual/pre-turn compaction uses `DoNotInject` (the next regular turn will naturally re-inject it).

The teaching version drops "initial-context re-injection" and "selecting user messages by token budget," but keeps the most core "many old items → summary + recent items" skeleton.

</details>

<details>
<summary>3. How the summary is generated: SUMMARIZATION_PROMPT and SUMMARY_PREFIX</summary>

The teaching version's `summarize` is a deterministic heuristic (line-by-line concatenation + truncation), for the sake of being **offline, no model needed**. The real source has the model produce the summary:

- `SUMMARIZATION_PROMPT` (`prompts/templates/compact/prompt.md`) instructs "you are doing CONTEXT CHECKPOINT COMPACTION, writing a handoff summary for the LLM that takes over," requiring it to include: current progress and key decisions, important constraints / user preferences, remaining to-dos, and key data / references.
- After the summary is produced, `SUMMARY_PREFIX` (`summary_prefix.md`, a piece of guidance text reading "another language model produced its thinking summary; please continue from it and don't repeat the work") is prepended, then it's stored into history as a single `role="user"` message.

```rust
let summary_suffix = get_last_assistant_message_from_turn(history_items).unwrap_or_default();
let summary_text = format!("{SUMMARY_PREFIX}\n{summary_suffix}");
```

In other words, compaction itself **is a real model turn**: feed in the old history, have the model "output a summary," then replace the history with that summary. The teaching version substitutes a heuristic for this turn call, while the skeleton (`SUMMARY_PREFIX` + one user message) stays consistent.

</details>

<details>
<summary>4. The relationship between server-side compaction remote / remote_v2 and local</summary>

The teaching version has only the local path. The real source's `run_auto_compact` (`session/turn.rs`) is a **three-way dispatcher**:

| Condition | Which path | Implementation |
|---|---|---|
| Provider supports remote + `RemoteCompactionV2` enabled | Remote v2 | `compact_remote_v2.rs::run_inline_remote_auto_compact_task` |
| Provider supports remote (default remote) | Remote v1 | `compact_remote.rs::run_inline_remote_auto_compact_task` |
| Provider doesn't support remote | **Local fallback** | `compact.rs::run_inline_auto_compact_task` |

The decision relies on `should_use_remote_compact_task(provider)` (i.e. `provider.supports_remote_compaction()`). Remote compaction pushes "summarize the old history" down to the **server**, with the client getting back the compacted history; `compact_remote_v2` also wires in a rollout trace (`CompactionCheckpointTracePayload`) for observability. All three paths share the same skeleton functions (`insert_initial_context_before_last_real_user_or_summary`, `compaction_status_from_result`, pre/post-compact hooks); only "where the summary is computed" differs.

In one sentence: the teaching version's ~60 lines of "over budget → squash the old into one summary + keep recent items" is the core of `compact.rs` (600+ lines); everything else is precise token accounting, initial-context re-injection, pre/post hooks, analytics, and **the three implementations of local / remote v1 / remote v2**.

</details>

## Run

```bash
python s07_context_compaction/code.py --demo   # 造长对话演示压缩 before/after（mock，无需 key，摘要离线生成）
python s07_context_compaction/code.py          # 交互模式：聊到超预算会自动压缩
```

`--demo` runs entirely offline, never calling the model; the summary is generated by a deterministic heuristic, and at the end it prints the `19 items → 1 summary + 6 recent items = 7 items` check.

## Recap

- The context window is finite: when over budget, squash **the oldest batch of turns** into a single `[summary]` and keep the most recent N items.
- Two trigger paths: proactive (compact actively when tokens approach the limit) + reactive (delete items from the front and retry on hitting `ContextWindowExceeded`).
- Compaction itself is a model turn (producing a handoff summary); what's preserved is "recent details + key decisions," while verbose tool output is trimmed.
- Codex and Claude Code have ≈ compaction ideas, but Codex additionally has **server-side compaction** (`compact_remote` / `_v2`), with local as the fallback — a bet on cloud autonomous operation.
- Compaction only changes the "active history"; the complete, undiminished history is persisted separately by the rollout, and the two are orthogonal.
- **Production-grade**: proactive estimation drifts, so there must be a reactive fallback — when you really hit `ContextWindowExceeded`, delete the oldest item from the front and retry until it fits (see the "Production-grade" section). Neither path is dispensable: proactive saves money, reactive saves your life.
- Next stop [s08 Rollout resume](../s08_rollout/README.en.md): compaction saves tokens, the rollout preserves the full master draft — even the "compaction" action itself gets recorded, so the session can be resumed and replayed.

## Think it over

- Compaction uses "character count / token estimation" to decide when to trigger, but the estimate may drift. Codex's fallback is reactive — only delete items from the front and retry once it actually hits the wall. If it were you, would you trust "compact proactively in advance" or "patch it up after hitting the wall" more? How would you weigh the costs of the two (one extra model turn vs one failed retry)?
- The summary is written by the model itself — meaning "what to remember, what to drop" is adjudicated by the model. If it omits a key decision in the summary ("we abandoned plan A"), subsequent turns will carry on from a flawed memory. How would you reduce this risk of "the summary dropping key information"?
- Codex pushes compaction **down to the server** (remote), with the client only consuming the result. This is carefree, but it also means "how my conversation is compacted, and into what" happens somewhere you can't see. For a headless cloud task, is this "the server compacts for you" a convenience to welcome or a black box to be wary of?
- Compaction trims the "active history," while the rollout (s08) preserves the **complete** history. Since the full record is all there, why not just compute the context fresh from the full history every time, instead of maintaining a compacted active history? Between "save money, save the window" and "never forget," where would you draw this boundary?
