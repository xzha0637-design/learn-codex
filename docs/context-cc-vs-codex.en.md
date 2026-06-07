# Long read: A complete guide to "context handling" in Claude Code vs. Codex

> 🌐 **English** · [中文版](context-cc-vs-codex.md)

> This is an in-depth long read aimed at readers with **only a little background**. We start from "what is a context window"
> and work all the way down to every engineering trade-off the two systems make in injection, compaction, truncation, persistence, and memory,
> explaining **why** they took different paths.
>
> Factual basis: Codex comes from the real source code in this repo, `../../codex/codex-rs`; Claude Code (hereafter CC) comes from
> [learn-claude-code](../../learn-claude-code/)'s analysis of its source (`compact.ts` / `autoCompact.ts` / `microCompact.ts` / `query.ts`).
>
> ⚠️ **On the strength of the evidence**: On the Codex side, every number/constant can be traced to a line number in `codex-rs`; on the CC side, the precise numbers (such as "keep the first few messages," "N×K token budget," "number of message types," "polling at 1s/500ms") are paraphrased from learn-claude-code's analysis of the **closed-source** CC, and **cannot be independently verified within this repo**—please treat them as secondhand material, with lower certainty than the Codex side.

[← Back to the learn-codex overview](../README.en.md)

---

## 0. For beginners: what is a context window, and why does it "fill up"

A large model is not a database; it has no "memory." Every time it answers, it relies entirely on the text you hand it **this one time**.

This "all the text" is held in a box with a size limit, called the **context window**, measured in **tokens** (you can roughly think of these as "fragments of words"—one Chinese character is about 1–2 tokens, and 100 English words is about 130 tokens). For example, a model's window might be 272,000 tokens—that is the entirety of what it can "see" at once.

Now picture an agent doing work:

1. You say "help me fix this project's tests."
2. It reads a 1000-line file (≈4000 tokens).
3. Then it reads 30 more files and runs 20 commands.
4. The contents of every file and the output of every command **all pile up in the conversation history**, because the next turn has to resend the entire history to the model—this is the essence of the [agent loop](../README.en.md): the model has no memory, so the history *is* its memory.

As things pile up, the box fills. The model API simply errors out: on the CC side it's called `prompt_too_long` (HTTP 413), and on the Codex side it's called `ContextWindowExceeded`.

**So every serious agent harness has to solve the same problem: the context will always fill up, and there must be a way to make room without forgetting the important things.** This article is about how CC and Codex each solve it—and why their solutions differ.

---

## 1. First, the dissection: what exactly is packed into the context of a single request

Before compaction, let's get clear on "the thing being compacted" and the few pieces it's made of. The two systems are broadly similar in composition, but the naming and shape differ:

| Component | Claude Code | Codex | Where it's covered in detail |
|---|---|---|---|
| System instructions | `system` parameter | `instructions` parameter | [s09 Responses API](../s09_responses_api/README.en.md) |
| Project memory | `CLAUDE.md` | `AGENTS.md` (level by level, upward) | [s06 AGENTS.md](../s06_agents_md/README.en.md) |
| Tool definitions | `tools` (`input_schema`) | `tools` (flat `parameters`) | [s02](../s02_tool_use/README.en.md) / [s09](../s09_responses_api/README.en.md) |
| Conversation history | `messages[]`, with content as `tool_use`/`tool_result` **blocks** | flat item list: `message`/`function_call`/`function_call_output` | [s09](../s09_responses_api/README.en.md) / [s10](../s10_sq_eq_protocol/README.en.md) |
| Model reasoning | thinking block | reasoning item (can be encrypted, carried across turns) | [s09](../s09_responses_api/README.en.md) |
| Tool output | content of the `tool_result` block | content of the `function_call_output` | [s07 compaction](../s07_context_compaction/README.en.md) |

**Key insight**: Of the above, everything except "system instructions / project memory / tool definitions" is relatively fixed; it's the **conversation history + tool output** that grows without bound and eventually bursts the window. So all compaction techniques are mainly wrestling with these two pieces.

Below, we'll go down the chain "injection → representation → compaction → truncation → persistence → memory" and compare section by section.

---

## 2. Injecting project memory: CLAUDE.md (CC) vs. AGENTS.md (Codex)

An agent needs to understand "this project's rules" (which package manager to use, the code style, which directories not to touch). Both sides use a Markdown file to carry this kind of "project memory," but the way they discover and inject it differs.

### Codex: collect AGENTS.md level by level upward, with a cap and override support

The real source is in [`core/src/agents_md.rs`](../../codex/codex-rs/core/src/agents_md.rs). Key points:

- **Walk all the way up from the current directory to the project root** (markers like `.git`), collecting every `AGENTS.md` along the way;
- Use the separator `AGENTS_MD_SEPARATOR = "\n\n--- project-doc ---\n\n"` to concatenate them **in "root → current directory" order** (the more specific ones come later, and the "closer" ones have more say);
- There's an overall cap, `project_doc_max_bytes` (default ~32 KiB)—memory can't be unbounded, or it would devour the window before anything else;
- It supports a local override file `AGENTS.override.md` (`LOCAL_AGENTS_MD_FILENAME`), as well as configurable fallback filenames `project_doc_fallback_filenames`;
- Finally, it's injected into `instructions` as a `<user_instructions>` block.

This "level-by-level upward + concatenate" design is especially well-suited to **monorepos**: put the common rules at the repo root and each subpackage's special rules in its own directory, and whichever subdirectory the agent enters, it automatically stacks up the rules along that path.

### Claude Code: CLAUDE.md

CC uses `CLAUDE.md` to carry the same role (project-level, user-level), injected into the system. It likewise supports a hierarchy (project/user/local), and the philosophy is consistent.

### Differences and why

| | Claude Code | Codex |
|---|---|---|
| Filename | `CLAUDE.md` | `AGENTS.md` (+ `AGENTS.override.md`) |
| Discovery | project/user hierarchy | **collect the entire chain level by level upward from cwd** |
| Size constraint | yes | explicit byte cap (default ~32 KiB) |
| Standardization | its own convention | pushing `AGENTS.md` to become an open convention **across tools/across vendors** |

**Why?** AGENTS.md is a cross-tool standard that OpenAI is promoting—it hopes the same `AGENTS.md` can be shared by Codex and by other tools that follow the convention. Collecting level by level upward turns "project memory" into something that can be layered and stacked, which fits large repos. The two share a philosophy (inject project common sense into the agent); the difference lies in their stance on standardization and the granularity of discovery.

---

## 3. How the "shape" of the history affects compaction

This point is often overlooked but crucial: **what the history looks like determines how you can compact it.**

- **CC (Anthropic Messages)**: the history is `messages[]`, and each message's `content` is a **block array**; a tool call is a `tool_use` block, a tool result is a `tool_result` block, **embedded inside** assistant/user messages. So CC's compaction "operates at both the message and block levels"—it can trim an entire message and also replace the content of a single `tool_result` block on its own.
- **Codex (OpenAI Responses)**: the history is a **flat item list**; `function_call` and `function_call_output` are each **independent items**, on the same level as a text `message`. So Codex mostly "trims from the front of the item sequence."

Keep this shape difference in mind, and the compaction strategies in the next two sections become easy to understand.

---

## 4. Claude Code's compaction: a four-layer pipeline, cheapest first

CC's core design principle is **"run the cheap stuff first, the expensive stuff later"**—anything that can be solved with pure text operations should never call the model. It builds compaction into a four-layer pipeline (plus an emergency layer), run in order before each LLM call.

### L1 · snip_compact —— trim away irrelevant old conversation in the middle (0 API)

When a conversation has accumulated many messages, the earliest ones like "help me create a hello.py" have long been irrelevant to the current work. Keep the **first 3 messages** (initial context) + the **last several** (current work), and replace the middle with a single placeholder `[snipped N messages]`.

> In real CC this is a feature gate (`HISTORY_SNIP`), which also exposes a `SnipTool` for the model to invoke proactively.

### L2 · micro_compact —— replace old tool results with placeholders (0 API)

After reading 10 files in a row, the full contents of the first 7 are still sitting in the context, taking up space for nothing. Keep only the **most recent 3** `tool_result` contents in full, and replace older ones with `[Earlier tool result compacted. Re-run if needed.]`.

> Real CC has two paths: a time-triggered one (60-minute interval) that clears directly, and a cache path that goes through the API `cache_edits`.

### L3 · tool_result_budget —— persist large results to disk (0 API)

After a single `cat` of 5 large files, the `tool_result`s in one message add up to 500KB. It tallies the total size of all `tool_result`s in the last user message, and if it exceeds **200,000 characters**, it **persists** them to disk in `.task_outputs/`, starting with the largest; the context keeps only a `<persisted-output>` marker + a 2000-character preview. The model knows the full content is on disk and re-reads it when needed.

### L4 · compact_history —— full LLM summarization (1 API)

The first three layers are all pure text operations and don't "understand" the content. If the token count still exceeds the threshold, it sends the entire history to the model and asks it to produce a structured summary (real CC requires **9 sections** + the `<analysis>`/`<summary>` dual tags, and **doubly emphasizes the prohibition on calling tools** at both the start and the end), then **replaces** the old messages with this summary.

- Trigger threshold (precise tokens): `contextWindow − maxOutputTokens − 13,000` (that 13,000 is `AUTOCOMPACT_BUFFER_TOKENS`, a safety margin).
- **Recovery after compaction**: CC doesn't just keep the summary—it automatically re-attaches the most recently read files (up to **5**, **5,000** tokens each, **50,000** tokens total budget), the plan, and agent/skill/tool context. This is a production-grade detail the teaching version doesn't have.
- **Circuit breaker**: it stops after 3 consecutive summarization failures, to prevent a runaway loop from burning money.

### Emergency · reactive_compact —— when you actually hit a 413

When the context grows faster than compaction can keep up and the API returns `prompt_too_long` outright, a more aggressive fallback triggers: it rewinds from the tail by message group (`truncateHeadForPTLRetry`), trims at the byte level until the API will accept it, and keeps only the summary + the last few messages. There's a retry cap, and once exceeded it throws (error recovery is a separate topic).

### Two more mechanisms

- **contextCollapse**: a separate context-management system that, when enabled, suppresses proactive autocompact.
- **sessionMemoryCompact**: before calling the LLM summary, it first tries to do a lightweight summary using the existing "session memory" (see Section 10), saving one API call.

**CC's style in one sentence**: client-side all-inclusive, finely layered, cheap-first, and after compaction it works hard to fish back the "most recent and most relevant" things.

---

## 5. Codex's compaction: preserve the prefix cache + the option to offload the work to the server

Codex's compaction is in [`core/src/compact.rs`](../../codex/codex-rs/core/src/compact.rs), with `run_inline_auto_compact_task` and `build_compacted_history` at its core. Like CC, it has both "proactive threshold trigger" and "hit-the-wall reactive trigger," but there are two distinct differences.

### Difference one: trim from the **front**, deliberately preserving the "prefix cache"

`compact.rs` has a telling comment:

> *"Trim from the beginning to preserve cache (prefix-based) and keep recent messages intact."*

Meaning: the large model API's **prompt cache is prefix-based**—as long as the beginning of this request matches the last one, that part can hit the cache, fast and cheap. So when compacting, Codex tends to **keep the prefix stable and trim from the middle/front of the history**, while keeping the recent messages intact. The summary text carries a fixed prefix `SUMMARY_PREFIX` (something like `is_summary` is used to judge whether a message is a summary), and the summary itself is capped at `COMPACT_USER_MESSAGE_MAX_TOKENS = 20,000` tokens.

After compaction it also runs `recompute_token_usage` to re-tally the account, and gives the user a friendly heads-up:

> *"Heads up: Long threads and multiple compactions can cause the model to be less accurate. Start a new thread when possible..."*

—Codex explicitly acknowledges that "compaction is lossy" and suggests you **start a new thread**. This product stance is itself a trade-off.

### Difference two: **server-side remote compaction** (something CC doesn't have)

Codex has [`compact_remote.rs`](../../codex/codex-rs/core/src/compact_remote.rs) and `compact_remote_v2.rs`: `run_inline_remote_auto_compact_task`, `trim_function_call_history_to_fit_context_window`. In other words, **compaction can happen on the server**, not just locally on the client.

Why can Codex do this while CC (mostly) doesn't? Because the protocols differ (see [s09 Responses API](../s09_responses_api/README.en.md), [s10 SQ/EQ](../s10_sq_eq_protocol/README.en.md)):

- Codex uses the **Responses API**, with server-side session state and `x-codex-turn-state` sticky routing—the server "knows" this thread's history, so naturally it can compact on the server for you.
- CC uses the **Messages API**, which is essentially stateless: it sends the complete `messages[]` every time. The state is on the client, so compaction can only happen on the client.

The model info even directly carries compaction-related fields (in `client_tests.rs` you can see `"context_window": 272000, "auto_compact_token_limit": null`)—the window size and the auto-compaction threshold are part of the model's capability description.

### Tool-output truncation: TruncationPolicy

Codex uses `codex_utils_output_truncation` (`TruncationPolicy`, `approx_token_count`, `truncate_text`) to truncate a **single tool output**, and has [`thread_rollout_truncation.rs`](../../codex/codex-rs/core/src/thread_rollout_truncation.rs) to handle truncation at the rollout layer. This corresponds to CC's L3, but Codex leans more toward "truncate by token policy + record to rollout," while CC leans more toward "persist to disk and keep a preview."

**Codex's style in one sentence**: preserve the prefix cache, acknowledge lossiness and advise starting a new thread, and be able to push compaction down to the server.

---

## 6. Comparing the trigger timing

| | Claude Code | Codex |
|---|---|---|
| Proactive | run L1–L3 before each turn; when tokens exceed `ctx−maxOut−13k`, trigger the L4 summary | estimate before each turn; when over `auto_compact_token_limit`, trigger `run_inline_auto_compact_task` |
| Reactive | hit `prompt_too_long` (413) → `reactive_compact` aggressive fallback | hit `ContextWindowExceeded` → inline compaction retry |
| Compaction location | **pure client-side** (4-layer pipeline) | client **or server** (remote v2) |
| Proactively asking the user for help | `/compact` command, `SnipTool` | prompts "start a new thread," `Op::Compact` manual trigger |
| Cache strategy | first 3 + last N | **trim from the front, preserve the prefix cache** |

Both follow the cheap-first idea of "don't call the model if you don't have to," and both have the two paths of "proactive + hit-the-wall fallback"—this is convergent engineering wisdom. The divergence is in **location (client vs. server)** and **caching philosophy**.

---

## 7. Active context ≠ durable record: Codex's Rollout decoupling

This is the point beginners most easily confuse, yet it's the most important: **"what the model can see right now" and "what the system has saved" are two different things.**

- **Codex**: [`rollout/`](../../codex/codex-rs/rollout/) persists the **complete** history (every item, tool call, error, cost, time) to **SQLite + zstd compression**. This record is **complete and never lost**. Compaction only happens to "the active context fed to the model"—even if the active context is summarized down to a single sentence, the full original text is still in the rollout, so you can **resume**, **rewind (roll back N turns, `Op::ThreadRollback`)**, or even replay the entire session. In other words: **compaction is lossy, but the durable record is lossless.**
- **Claude Code**: before L4 compaction, it writes the complete conversation to `.transcripts/` (JSONL) for the record, but this is more like "keeping a backup"; the active session mainly still revolves around the client's `messages[]`.

**Why does Codex make such a big deal of this?** Because it's built for the cloud / unattended operation (`codex exec`, Codex cloud): a session might run for a long time, span machines, need to be auditable, and need to be recoverable from any point. Thoroughly decoupling the "durable complete record" from the "lossy active context" is the foundation that supports these scenarios.

---

## 8. An often-overlooked context cost: reasoning

Codex uses a **reasoning model**, and the model produces `reasoning` items (the reasoning process). These reasoning items:

- are **carried across turns** as first-class citizens of the protocol (even as encrypted content), so the model "remembers how it thought before";
- and therefore **consume the context budget**—the more reasoning, the more it crowds out the window, and the greater the compaction pressure.

On the CC side, the counterpart is the thinking block. The difference is that Codex integrates reasoning deeply into the protocol, the rollout, and the compaction flow (compaction has to decide how much reasoning to keep); this is a category of context cost brought by the "reasoning model + Responses API" combination that isn't as prominent in CC's current form.

---

## 9. Compaction loses detail, so there's a "memory layer"

Compaction is lossy—what the user said half an hour ago, "don't use yarn, use pnpm," might get diluted away in some summary. Both sides realize they need a layer of something that "isn't lost to compaction."

- **Claude Code**: it has a **memory subsystem** (learn-cc s09)—an LLM chooses "what's worth remembering" (not embedding retrieval), extracts it at the stop-hook moment, and writes it to a Markdown memory file that survives across compaction and across sessions; it also distinguishes User Memory / Session Memory, and has low-frequency merge-and-dedup ("Dream," a four-layer gate).
- **Codex**: on one hand it relies on the **static AGENTS.md** (Section 2) to carry long-term project common sense; on the other hand it has the `ext/memories` crate and `Op::SetThreadMemoryMode`—i.e., a toggleable **thread memory mode**. Add to that the **complete rollout** (Section 7), which itself is the "nothing was truly lost" fallback.

### Differences and why

| | Claude Code | Codex |
|---|---|---|
| Long-term project common sense | CLAUDE.md | AGENTS.md (layered) |
| Dynamic memory across compaction | proactively-extracted memory subsystem (choose what to remember, organize and consolidate) | `ext/memories` + thread memory mode; relies more on the complete rollout |
| The "nothing lost" floor | transcript backup | **SQLite complete rollout, resume/rewind-able** |

**Why?** CC makes "memory" a proactive, carefully-curated client-side subsystem, fitting its positioning of "local, interactive, a long-term companion to one person"; Codex leans more toward "complete persistence + recoverability" as the floor, leaving "remembering the key things" more to the static AGENTS.md and the recoverable rollout, fitting its positioning of "cloud, auditable, replayable."

---

## 10. Overall comparison table

| Dimension | Claude Code | Codex |
|---|---|---|
| Protocol state | Messages API, **structurally stateless** (state on the client) | Responses API, **server can be stateful** (Codex defaults to `store:false` against OpenAI and still resends statelessly; only Azure/remote compaction makes use of server-side state; for the precise distinction see [API comparison](api-message-vs-responses.en.md) §5) |
| Project memory | CLAUDE.md | AGENTS.md (level by level upward, capped, overridable, cross-tool standard) |
| History shape | nested content blocks (tool_use/tool_result) | flat items (function_call/_output) |
| Compaction location | pure client-side, four-layer pipeline | client **+ server-side remote compaction** |
| Caching philosophy | first 3 + last N | **trim from the front, preserve the prefix cache** |
| Recovery after compaction | proactively re-attach recent files/plan (5 files × 5K / 50K budget) | keep recent + summary prefix; advise starting a new thread |
| Large tool output | persist to disk + 2000-char preview (>200KB) | TruncationPolicy truncates by token + rollout truncation |
| Active context vs. durable record | transcript backup | **rollout fully decoupled, resume/rewind-able** |
| reasoning context | thinking block | reasoning item carried across turns (consumes budget) |
| Dynamic memory | proactively-extracted memory subsystem | ext/memories + complete-rollout fallback |
| Attitude toward lossiness | works to recover, tries to be seamless | explicitly acknowledges lossiness, advises a new thread |

---

## 11. Why are they different? Settling the account in full

Boiling all the above differences down to one sentence:

> **Codex's context strategy is "server + persistence + recoverable," while CC's is "client + fine-grained pipeline + proactive recovery."**

The root causes are three layers deep:

1. **The protocol determines what's possible.** The Responses API lets the server hold the session state, so Codex *can* offload compaction to the server and *can* maintain a recoverable rollout; the Messages API is essentially stateless, so CC's context management can only—and therefore is done extremely finely—be packed entirely onto the client.
2. **The scenario determines the priorities.** Codex is built for headless / CI / cloud—sessions are long, span machines, need auditing, and need to recover from any point, so "complete persistence + replayability" is the foundation, and "preserving the prefix cache" is a cost optimization. CC is built for local interaction—one person, one machine, in long-term collaboration—so "fish the most relevant things back after compaction, keep the user as unaware as possible" is the core of the experience.
3. **The model form brings new costs.** The reasoning model makes reasoning a chunk of context that needs managing, and Codex integrates it into the protocol and compaction; this is an item of accounting different from managing "classic conversation history."

Neither is more brilliant—these are two self-consistent engineering answers aimed at different worlds. Once you understand "how the account is settled," you can make the trade-offs **for your own scenario** in your own harness.

---

## 12. Think it over

<div class="think">

1. "Compaction is lossy but the rollout is lossless"—if it were your design, would you let the model **know** it had been compacted? If it knew, would it proactively say "I need to re-read that file"? Is that a good thing, or would it get verbose?
2. Codex "trims from the front" to preserve the prefix cache; CC "keeps the first 3" to retain the initial context. In a long session where "there are important constraints at the start and a pile of exploration in the middle," which one loses the constraints first?
3. Server-side compaction saves the client work, but it also means "your conversation was rewritten on the server by a process you can't see." On auditability, is that a plus or a minus?
4. If the context window someday became 100 million tokens, would "compaction" disappear? Or would just the threshold change while the accounting stays the same? (Hint: think about cost, latency, and the "needle in a haystack" attention problem.)
5. CC uses an LLM to proactively "pick what's worth remembering," while Codex relies more on "store everything, fish it out when needed." Which is more like human memory? Which is better suited to an agent?

</div>

---

[← Back to the learn-codex overview](../README.en.md) · Related chapters: [s06 AGENTS.md](../s06_agents_md/README.en.md) · [s07 Context compaction](../s07_context_compaction/README.en.md) · [s08 Rollout](../s08_rollout/README.en.md) · [s09 Responses API](../s09_responses_api/README.en.md) · [s10 SQ/EQ](../s10_sq_eq_protocol/README.en.md)
