# s18: Multi-Agent — When One Agent Isn't Enough: Build "Communication" into the Protocol

> 🌐 **English** · [中文版](README.md)

> *"The hard part of teaming up isn't cloning yourself — it's talking. Build communication into the protocol, and it becomes auditable, encryptable, cross-machine history."*

[learn-codex overview](../README.en.md) · [s17 Comprehensive: Mini Codex](../s17_comprehensive/README.en.md) → **s18 Multi-Agent (advanced topic)** · [back to overview ↺](../README.en.md)

---

## Get the idea straight first: the truly hard step in teaming up is "talking"

For the previous 17 chapters, everything you built was **one** agent. But in the real world, one often isn't enough — frontend, backend, and testing can run in parallel, and a reviewer can vet things on your behalf. So we get "multiple agents." Where's the difficulty in multi-agent? Not in "building a second one," but in "how they get to talk to each other." Grasp the following three ideas and this chapter clicks.

**Idea one: first separate two things — "cloning" and "teaming up" — they're often conflated.**
- **Cloning (subagent)**: while the main agent is fixing a bug, it first needs to "read 30 files to figure out the call chain." Reading them directly in the main conversation would blow up the context and make it forget what it was supposed to do in the first place. The trick is to **spin up a clone**: hand it a clean blank sheet to investigate, and when it's done, **bring back only the conclusion** — throw away all the intermediate steps. It's like opening a new terminal to look something up while fixing a bug, then closing it once you're done and only jotting the conclusion into your notes.
- **Teaming up (multi-agent)**: several **peer** agents, each with its own job, making progress in parallel and communicating with one another — a **team**.

Cloning is "master/servant" (parent dispatches child, child reports back); teaming up is "colleagues" (calling out to each other). This chapter focuses on the hardest part of teaming up, and the one that best reveals where the two systems diverge: **communication**.

**Idea two (the most crucial): "where you put communication" determines the entire system's personality.**
For two agents to talk, there are two places to put it:
- **Outside the agent**: sending a message = writing a file into the other party's "inbox," receiving a message = reading your own directory. Like slipping a note under your neighbor's door — simple, you can lift it and look anytime, but the prerequisite is that **everyone has to live in the same building** (same machine, same file system). This is Claude Code's path.
- **Inside the protocol**: an "agent-to-agent message" stands **on equal footing** with "what the user said" and "tool calls" — it's itself a category of entry in the conversation protocol. This is Codex's path.

It sounds like just an implementation detail, but it's actually a watershed — **the location determines whether it can be encrypted, whether it can cross machines, whether it leaves a trace, and whether you can send it to several people at once.**

**Idea three: when "communication is history," communication is upgraded from a little side pipe into first-class infrastructure.**
Codex chose "put it in the protocol," and so a magical thing happens: every sentence A says to B **automatically lands in the rollout** (that ledger from [s08](../s08_rollout/README.en.md)). "Who, when, to whom, said what, and was it process or conclusion" — all of it is replayable and accountable. Add **identity** to the message (who issued it, cryptographically verifiable), **multiple recipients** (like email's To/Cc), and **encryptability** (a man-in-the-middle can't read the plaintext), and communication itself becomes **distributed infrastructure** that can go to the cloud, be audited, and run in untrusted environments. Codex's setup looks much heavier than "slipping notes," precisely because it's aimed at **the cloud, multi-tenancy, and auditability** — whereas CC's "file inbox" is aimed at **local use, being able to `cat` and watch, and a pleasant debugging experience**. Once again: the scenario dictates the mechanism.

> In this chapter we'll build with our own hands a minimal "in-band communication" skeleton: one `AgentMessage` + a router that **delivers and leaves a trace**, letting three agents — Lead, Worker, and Reviewer — collaborate through one round.

## Problem

You want a **Lead** to dispatch a **Worker** to change a file, then dispatch a **Reviewer** to review the change. The three agents have to communicate with one another.

If you rely on "stuffing a chunk of text into the main conversation" to pass messages, it immediately becomes a mess: who sent this sentence? Sent to whom? Is it offhand **process** or a settled **conclusion**? Was it **persisted** afterward? And if the Worker is running on another machine, does this whole approach still hold?

Turning "how agents pass messages to each other" into a first-class mechanism that is **addressable, has phases, and leaves a trace** — that's this chapter.

## Solution

Two new parts (everything else is carried over from [s01](../s01_agent_loop/README.en.md)'s agent loop — **each agent is still internally an s01 loop**):

1. **`AgentMessage`**: an in-band message type, carrying `author` (who) / `recipient` (sent to whom, To) / `cc` (carbon copy, multiple recipients) / `content` / `phase` (commentary = process, or final_answer = conclusion) / `encrypted` (encryptable).
2. **`InterAgentRouter`**: one `submit` does two things — **(a)** records the message into the **shared rollout** (the essence of in-band: communication is history); **(b)** delivers it by `AgentPath` to the recipient + each cc.

```
        Lead ──spawn──▶ Worker（lead/worker）        每条消息都：
         │                  │                       ① 进共享 rollout（留痕）
         │  ✉ task          │                       ② 投递到收件箱（To + Cc）
         └─────────────────▶┤
                            （内部跑一个 s01 回合，真把文件改了）
         ┌──── ✉ 完成(final) ┘
         │
        Lead ──派生──▶ Reviewer（lead/reviewer）
         │  ✉ 审一下(To=reviewer, Cc=worker)
         └─────────────────▶ Reviewer ──✉ APPROVE/REJECT(final)──▶ Lead
```

## How it works

See [code.py](code.py): `AgentMessage`, `InterAgentRouter.submit` (corresponding to the real source's `Op::InterAgentCommunication`), and three `Agent`s (the `Worker` internally calls the model + the shell tool to do real work; the `Reviewer`, like [s14](../s14_guardian/README.en.md), uses rules to simulate the auto_review judgment).

**Walk through it** — this is exactly the round that `python s18_multiagent/code.py --demo` runs. Keep your eye on two things: the message's **To/Cc/phase**, and **how it enters the rollout one entry at a time**.

**① Lead spawns the Worker and issues the task.** Spawning first records a parent/child edge in the **agent graph**; the task is sent out as a `commentary` message:

```
  ⑂ spawn: lead ──▶ lead/worker（图谱记一条边，status=open）
  ✉️  lead ──▶ lead/worker  [commentary]  "在 _demo_workspace 里执行 `echo ... > artifact.txt`"
       ↳ 记入共享 rollout（第 1 项）——通信即历史，可审计可重放
```

**② Worker does the work and replies.** The Worker reads the task from its inbox, **internally runs an s01 round** (calls the model → the model has it run shell → actually creates the file), then sends two messages back to the Lead: one `commentary` ("got started"), and one `final_answer` ("done, here's the artifact"). Note **phase distinguishes "process" from "conclusion"**:

```
  ✉️  lead/worker ──▶ lead  [commentary]  '收到任务，开工。'
  ✉️  lead/worker ──▶ lead  [final_answer]  "完成。产物内容：'hello from the worker'"
```

**③ Lead spawns the Reviewer and sends the review request — To + Cc multiple recipients.** This message is **primarily sent to the reviewer, with the worker cc'd**: a single message lands in two inboxes at once. This is something "slipping notes" can hardly do cleanly, but that an in-band protocol supports natively:

```
  ✉️  lead ──▶ lead/reviewer  +cc ['lead/worker']  [final_answer]  "请审查这次改动：..."
```

**④ Reviewer vets it and returns a verdict.** The Reviewer (simulating Codex's `auto_review` subagent) judges by risk rules and replies with a `final_answer`: `APPROVE：改动安全，可合入`. The Lead reads the verdict and wraps up.

**⑤ Demonstrate encryption.** Finally the Lead sends an **encrypted** message — in the rollout it leaves behind only an opaque `‹encrypted›`, and a man-in-the-middle can't see the plaintext (echoing the agent's encrypted identity).

**Finally, print the full shared rollout** — this is the crux of the whole chapter:

```
   1. [commentary  ] lead → lead/worker: 在 _demo_workspace 里执行 `echo ...`
   2. [commentary  ] lead/worker → lead: 收到任务，开工。
   3. [final_answer] lead/worker → lead: 完成。产物内容：'hello from the worker'
   4. [final_answer] lead → lead/reviewer cc=['lead/worker']: 请审查这次改动：...
   5. [final_answer] lead/reviewer → lead: APPROVE：改动安全，可合入
   6. [commentary  ] lead → lead/worker: ‹encrypted›
```

Six agent-to-agent communications, and **not a single one is "freeform text stuffed into some conversation"** — they're all first-class entries in the protocol, addressable, with phases, and leaving a trace. In Claude Code, these six would be **read-once-then-deleted** files in `~/.claude/teams/<t>/inboxes/*.json`; in Codex, they live in the rollout, replayable, auditable, encryptable, and cross-machine.

## Production-grade: an in-process toy vs. a real cross-machine network

This chapter's router is a single shared list inside one process — it clearly demonstrates the shape of "in-band communication." But put it into a real multi-agent / cloud environment and several production-grade problems that the toy version dodged immediately surface:

- **Real transport + partitions**: agents may be on different machines, in different trust domains. Messages have to traverse a real network, and so they face **network partitions** — what happens when a message is lost / duplicated / reordered? Delivery needs **acknowledgment + deduplication + retry**, not the guaranteed-delivery of `list.append`.
- **Real encryption + identity**: the teaching version's `encrypted=True` is just a flag. Real Codex uses `agent-identity`'s **ed25519 signatures + curve25519 encryption**: a message A sends to B can be signature-verified (confirming A sent it and it wasn't tampered with) and encrypted (a man-in-the-middle can't see it). In multi-tenant / untrusted environments this is a hard requirement, not decoration.
- **`trigger_turn` backpressure**: real `InterAgentCommunication` carries a `trigger_turn` field (see Deep dive one above) — deciding whether a message **should immediately wake the recipient to run a round**. If every message triggers one, a 5-agent team falls into a storm of waking each other; production-grade systems need to be able to "deliver only, don't interrupt," batching or rate-limiting the wake-ups.
- **Failure isolation**: one agent crashing / hanging mustn't drag down the whole team (the same principle as [s15](../s15_mcp/README.en.md)'s "one MCP server crashing doesn't drag down the rest") — you need timeouts, heartbeats, and a mechanism to "remove the dead member from the graph."

> In one sentence: the difficulty of multi-agent was never "building a second agent," but **keeping communication reliable, trustworthy, and under control when the agents are distributed across a real world where not everything stays healthy**. The in-band protocol gives you an auditable foundation; all the remaining industrialization is about covering for "the other party will lose messages, will lie, and will crash."

## 🆚 How it differs from Claude Code

Communication is where the two systems part ways most completely in this chapter: **CC puts communication in the file system (out-of-band), Codex puts communication into the protocol itself (in-band).**

| Dimension | Claude Code | OpenAI Codex | Why |
|---|---|---|---|
| Communication location | **Out-of-band**: file-system inbox | **In-band**: an `AgentMessage` entry in the protocol | CC is local-first; Codex is born for cloud/distributed |
| Transport | Write the other party's `.json` mailbox | Submit `Op::InterAgentCommunication` | One borrows the file system, one goes through the protocol |
| Delivery | **Polling** (Lead 1s / teammates 500ms) | A protocol event stream, flowing with the round | Files have no push, you can only read on a timer |
| Addressing | Filename = agent name | `AgentPath` + multiple recipients (To/Cc) | Path-style addressing echoes the agent graph |
| Security | Relies on file permissions | **Content-encryptable + encrypted identity** (ed25519) | In untrusted / multi-tenant environments the man-in-the-middle is blind |
| Persistence | Mailbox file (read-once-then-deleted) | **Persistent record in the rollout** | Communication is history, naturally auditable and replayable |
| Cross-machine | No (assumes the same file system) | **Yes** (protocol messages, can go to the cloud) | The decisive difference |
| How the model sees it | Inject `<teammate-message>` text | It's already an `AgentMessage` entry in history | One is stuffed in after the fact, the other is native |

> In one sentence: **CC treats agent communication as mail on a shared file system (the wisdom of Unix pipes); Codex treats it as a first-class message in the protocol that can be encrypted, routed, and audited (the mindset of distributed systems).** There's also a continuity of temperament: CC's signature subagent is "a clone that does work for me," whereas Codex's signature subagent is **"a reviewer that vets things on my behalf" (`auto_review`)** — carrying [Guardian](../s14_guardian/README.en.md)'s line of "dispatch an AI to vet when no one's around" all the way up to the multi-agent layer.

The complete layer-by-layer comparison (the three subagent modes, the agent graph, cloud-tasks, external-agent-sessions, Codex as an MCP server…) is in the long-form piece: **[The complete guide to subagents and multi-agent](../docs/subagent-multiagent-cc-vs-codex.en.md)**. This chapter is that piece's **runnable skeleton**.

## Deep dive: teaching version vs. real Codex source

The teaching version shrinks "communication" down to an in-memory router + one shared list. Real Codex makes it into a protocol type + persistent rollout + encrypted identity + cloud tasks. *The core is the same one — making communication a first-class in-band entry; everything extra is industrialization.*

<details>
<summary>One. Submission side: Op::InterAgentCommunication</summary>

Sending an agent-to-agent message is, in real Codex, submitting a first-class operation [`Op::InterAgentCommunication { communication }`](../../codex/codex-rs/protocol/src/protocol.rs) (`protocol.rs:499`). The struct that carries the content (`protocol.rs:626`):

```rust
pub struct InterAgentCommunication {
    pub author: AgentPath,
    pub recipient: AgentPath,
    pub other_recipients: Vec<AgentPath>,   // ← 这就是 Cc（多收件人）
    pub content: String,
    pub encrypted_content: Option<String>,  // ← 可加密
    pub trigger_turn: bool,                 // ← 这条消息要不要立刻唤醒收件人跑一个回合
}
```

The teaching version's `AgentMessage` is a stripped-down likeness of it: `cc` ↔ `other_recipients`, `encrypted` ↔ `encrypted_content`. The extra `trigger_turn` in the real source is interesting — it decouples "sending a message" from "making the other party act immediately": you can deliver only, without interrupting the other party.

</details>

<details>
<summary>Two. History side: communication is history (→ into the rollout)</summary>

Why do we say "in-band communication naturally leaves a trace"? Look at `InterAgentCommunication::to_response_input_item()` (`protocol.rs:673`): it turns a communication **into an `assistant` history message** (phase marked as `Commentary`). That is to say, a sentence between agents, once landed, is an item in the conversation history, and naturally goes into the rollout along with the thread (see [s08](../s08_rollout/README.en.md)).

When it lands in the **model-visible history**, it's [`ResponseItem::AgentMessage { author, recipient, content }`](../../codex/codex-rs/protocol/src/models.rs) (`models.rs:767`) — a kind of ResponseItem on par with `Message` / `Reasoning` / `FunctionCall`, carrying "who sent it, sent to whom." The teaching version's "append the message into the shared `rollout` list" is the toy version of this step.

</details>

<details>
<summary>Three. Phase, encryption, addressing</summary>

- **MessagePhase** (`models.rs:741`): `Commentary` (mid-stream musings) / `FinalAnswer` (the final reply). The source comment also honestly reminds you: *"providers do not emit this consistently, so callers must treat `None` as phase unknown"* — don't assume the model always labels it correctly for you. The teaching version's `Phase` enum is exactly this.
- **Encrypted content**: `AgentMessageInputContent::EncryptedContent { encrypted_content }` (`models.rs:720`). Paired with [`agent-identity/`](../../codex/codex-rs/agent-identity/)'s **ed25519 signatures + curve25519**, what A says to B can be encrypted and signature-verified — in multi-tenant / untrusted environments a man-in-the-middle can't see it.
- **AgentPath** (`protocol/src/agent_path.rs`): path-style addressing (like `lead/worker`), echoing the agent graph below.

</details>

<details>
<summary>Four. More than "communication": graph, identity, reviewer, cloud, interop</summary>

| Codex's multi-agent infrastructure | Real source | Teaching version |
|---|---|---|
| **agent graph**: a persistent parent/child topology | [`agent-graph-store/`](../../codex/codex-rs/agent-graph-store/), `ThreadSpawnEdgeStatus{Open,Closed}` (`types.rs:7`) | a `graph` list in the router |
| **encrypted identity**: every agent has a verifiable identity | [`agent-identity/`](../../codex/codex-rs/agent-identity/) (ed25519) | `encrypted` flag + AgentPath |
| **reviewer subagent**: approvals can be routed to an AI | `ApprovalsReviewer = "user" \| "auto_review" \| "guardian_subagent"` (`config_types.rs:159`) | `Reviewer` (rule simulation) |
| **dispatch tasks to the cloud** | [`cloud-tasks/`](../../codex/codex-rs/cloud-tasks/) | omitted (the demo is entirely in-process) |
| **import external agent sessions** | [`external-agent-sessions/`](../../codex/codex-rs/external-agent-sessions/) | omitted |
| **Codex as a callable member** | `mcp-server` exposes a `codex` tool (see [s15](../s15_mcp/README.en.md)) | omitted |

The official description of `auto_review`: *"uses a carefully prompted **subagent** to gather relevant context and apply a risk-based decision framework before approving or denying the request."* — the approval officer is itself a subagent.

</details>

<details>
<summary>Five. What the teaching version cut</summary>

In-process objects stood in for: network / cross-machine transport, real ed25519 signing and encryption, the SQLite/zstd rollout persistence ([s08](../s08_rollout/README.en.md) is the real one), concurrency and locking, cloud task scheduling, the round-waking semantics of `trigger_turn`, and the full s01–s17 stack each agent should have (here the Worker's "brain" is shrunk to a single round and the Reviewer to a single rule, so that the main thread of **communication** can be seen crystal clear). That's how big the skeleton is; everything else is industrialization.

</details>

## Run

```bash
python s18_multiagent/code.py --demo   # 离线：Lead → Worker → Reviewer 协作一遍（mock，无需 key）
python s18_multiagent/code.py          # 交互模式：你当 Lead，给 worker 派一句任务
```

By default `backend=mock`, runnable offline; when the demo finishes it **automatically cleans up** `_demo_workspace/` (consistent with the other chapters).

## Recap

- Multi-agent splits into two things: **cloning** (open a blank sheet to do the dirty work, report back only the conclusion) and **teaming up** (peer collaboration, communicating with one another); the hard part is the latter's **communication**.
- The key choice is "where to put communication": **CC out-of-band** (file inbox, polling, you can `cat` and watch, but assumes the same machine) vs. **Codex in-band** (the `AgentMessage` in the protocol, into the rollout, encryptable, multiple recipients, cross-machine).
- The essence of in-band communication is **communication is history**: every agent message automatically lands in the rollout, and so it's replayable and auditable; add identity, phase, and encryption, and communication becomes distributed infrastructure.
- Codex's signature subagent is the **reviewer (auto_review)** — carrying "dispatch an AI to vet when no one's around" from [s14](../s14_guardian/README.en.md) up to the multi-agent layer.
- **Production-grade**: beyond the in-process list, a real multi-agent system has to face network partitions (acknowledgment + deduplication + retry), real encryption + signed identity (ed25519, anti-tamper / anti-eavesdrop), `trigger_turn` backpressure (don't wake each other into a storm), and failure isolation (one crashing doesn't drag down the whole team) (see the "Production-grade" section).
- Next stop: go back to the [overview](../README.en.md) for the full picture, or read the long-form piece [The complete guide to subagents and multi-agent](../docs/subagent-multiagent-cc-vs-codex.en.md) to see the three subagent modes, cloud-tasks, and interop all at once — this chapter is that piece's runnable skeleton.

## Think it over

<div class="think">

1. CC uses "the file system as a message bus" — plain, but you can `cat` and watch. Codex uses an in-protocol `AgentMessage` + an agent graph — structured but more of a "black box." When debugging a 5-agent team that's hung, which would you rather have? Why?
2. Codex adds **encryption + identity** to agent messages. In what scenarios must "which agent made this change, and authorized by whom" be cryptographically provable? (Hint: compliance, multi-tenancy, supply chain.)
3. The real source's `trigger_turn` decouples "sending a message" from "waking the other party" — you can deliver only, without interrupting. If every message immediately woke the recipient to run a round, what would happen to a 5-agent team? When would you deliberately set `trigger_turn=false`?
4. "The reviewer is also a subagent" (auto_review) — having one AI review whether another AI should execute a dangerous command. Could this reviewer be fooled by the same injection techniques? For "AI reviewing AI" to genuinely add security (rather than add a layer of same-origin blind spot), what's the prerequisite? (Continue thinking from that question in [s14](../s14_guardian/README.en.md).)
5. Each agent's "brain" in this chapter has been shrunk to a single round or a single rule. If you swapped them for a full s01–s17 stack (each with its own approval, sandbox, compaction, rollout), which link in this 3-agent demo would break **first**? Why?

</div>
