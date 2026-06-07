# Extra-long read: A complete guide to "subagents and multi-agent" in Claude Code vs. Codex

> 🌐 **English** · [中文版](subagent-multiagent-cc-vs-codex.md)

> Following [the context piece](context-cc-vs-codex.en.md), this one unpacks another big topic: when **one agent isn't enough**, how do the two systems "split themselves" (subagent) and "form a team" (multi-agent) — and why CC heads toward "a crew of teammates living in the filesystem" while Codex heads toward "a network of threads that have identities, a graph, can go to the cloud, and can be invoked."
>
> Evidentiary basis: Codex comes from the real source `../../codex/codex-rs`; CC comes from [learn-claude-code](../../learn-claude-code/)'s analysis of `AgentTool.tsx` / `runAgent.ts` / `forkSubagent.ts` / the team chapters.
>
> ⚠️ **On the strength of the evidence**: every Codex-side mechanism can be matched to a file/line number in `codex-rs`; the precise CC-side numbers (e.g. "15 message types", "polling Lead 1s / teammate 500ms", "keep the first few entries") are relayed from learn-claude-code's analysis of **closed-source** CC and **cannot be independently verified in this repo** — please treat them as second-hand material, lower in certainty than the Codex side.

[← Back to the learn-codex overview](../README.en.md)

---

> 🛠 **Want to get hands-on?** This piece's runnable skeleton is the chapter [s18 multi-agent](../s18_multiagent/README.en.md) (`python s18_multiagent/code.py --demo`, offline) — a minimal in-band `AgentMessage` + router + shared-rollout demo that runs the "in-band communication" from the ★ section below for you to see.

## 0. For beginners: why one agent isn't enough

First, distinguish two **different** needs that often get conflated:

1. **Subtask isolation (subagent / split-off)**: the main agent is fixing a bug and needs to first "read 30 files to understand the call chain." If it reads them in the main conversation, the contents of those 30 files will blow up the context and make it forget what it set out to do. The fix: **spin up a split-off**, hand it a clean blank sheet to investigate, and have it bring back **only the conclusion** when done, discarding the entire intermediate process. It's like opening a "new terminal" to look something up while fixing a bug, then closing it and recording only the conclusion in your notes.

2. **Multi-agent collaboration (multi-agent / teaming up)**: on a big project, frontend, backend, and testing can proceed **in parallel**. Here what you want isn't "a split-off to look something up," but "several agents each doing their job, communicating with each other, coordinating progress" — a **team**.

Both CC and Codex do these two things, but their approaches differ enormously. Let's look at "split-offs" first, then "teaming up."

---

## 1. Subagent: lock the dirty work behind a blank sheet

### Claude Code: the `task` tool + a brand-new context

CC's split-off is a tool called `task` (real source `AgentTool.tsx` / `runAgent.ts`). The main agent calls it like any other tool, passing a one-line task description; the harness then **spawns a sub-agent**:

- gives it a **brand-new `messages[]`** (containing only that task description) and lets it run its own loop;
- when the sub-agent finishes, **only the final text conclusion** is passed back to the main agent — whatever it read in between and however many turns it chatted are all discarded;
- but **filesystem side effects are preserved** (the files it wrote and the code it changed are still there).

Three key design choices (learn-cc s06):

| Decision | Choice | Why |
|---|---|---|
| Context isolation | Brand-new `messages[]` | The sub-agent's intermediate process doesn't pollute the main conversation |
| Return only the conclusion | Take the last text entry | Don't pass back the entire history |
| No recursion | The sub-agent has no `task` tool | Prevents "a split-off spawning more split-offs" in infinite nesting |
| **Context isolation ≠ permission isolation** | The sub-agent's tool calls **still go through** the PreToolUse hook | What's isolated is attention, not the security policy |

**Going deeper (real CC has three modes)**:

| Mode | Context | Purpose |
|---|---|---|
| Normal Subagent | Brand-new messages[] | Pure isolation |
| **Fork Subagent** | `buildForkedMessages()` constructs a **cache-friendly prefix** | **Share the prompt cache** |
| General-Purpose | Same as Normal | General |

Fork mode (`forkSubagent.ts`) is the essence the teaching version didn't cover: it does **not** create a brand-new context for isolation, but instead makes the sub-agent's system prompt, tools, model, messages prefix, and thinking config **byte-for-byte identical** to the parent agent, so the Anthropic API's prompt cache can hit and avoid recomputation — **saving money and latency**. There's also `permissionMode: 'bubble'`: the sub-agent's permission prompts **bubble up to the parent terminal**, and you approve on its behalf in the main terminal.

### Codex: spawn a **thread**, record it in the **agent graph**, and give it an **identity**

Codex's split-off isn't made into a simple `task` tool, but is more "infrastructure-ified." The evidence is scattered across a few crates:

- [`agent-graph-store/`](../../codex/codex-rs/agent-graph-store/): the comments flatly state it is *"Storage-neutral parent/child topology for thread-spawned agents"* — a store that holds the **parent/child agent topology**, with `ThreadSpawnEdgeStatus`. In other words, Codex builds "who spawned whom" into a **persistable, queryable graph**, not just a one-shot function call.
- [`agent-identity/`](../../codex/codex-rs/agent-identity/): uses **ed25519 signatures + curve25519** to give an agent a **cryptographic identity**. A sub-agent isn't an anonymous split-off but an entity with a verifiable identity.
- A spawned sub-agent is itself a **thread** (`thread-store` / `thread-manager`), and like the main thread it has a full rollout (see [the context piece](context-cc-vs-codex.en.md), section 7) — persistable, recoverable, auditable.

A comparison makes it clear:

| | Claude Code | Codex |
|---|---|---|
| What a split-off is | A single `task` tool call, discarded when done | A spawned **thread** that enters the **agent graph** |
| Topology | Parent-child relationship is implicit (chainId, depth+1) | An explicitly persisted **parent/child graph** |
| Identity | None (anonymous sub-agent) | **Cryptographic identity** (can be signed and verified) |
| Cache optimization | Fork mode shares the prompt cache | Uses Responses server-side state (see the context piece) |
| Return | Returns only the text conclusion | The sub-thread's full rollout is accessible to the parent/system |

**Why is Codex this "heavy"?** Because it has to support cloud, auditable, recoverable, interoperable scenarios: when a swarm of agents runs in the cloud and you need accountability and replay, "who spawned whom, what each one's identity is, where each one's full history lives" can't be a one-shot thing in memory — it has to be a persisted graph + identity + rollout. CC's `task`, by contrast, makes the split-off a lightweight local tool call, fitting the "local, interactive, used up on the spot" feel.

---

## 2. A special subagent: Codex's "approver" is itself a subagent

This is a Codex design that really captures its temperament. Recall the two chapters on [approval and Guardian](../README.en.md) — who can Codex's approval **route to**? Look at `ApprovalsReviewer` in `protocol/src/config_types.rs`:

```
"user" | "auto_review" | "guardian_subagent"
```

The official description of `auto_review` (formerly named `guardian_subagent`) is:

> *"uses a carefully prompted **subagent** to gather relevant context and apply a risk-based decision framework before approving or denying the request."*

In other words: **when a dangerous operation needs approval and there's no human on the scene, Codex dispatches a dedicated "approver" subagent** to gather context and decide whether to approve based on a risk framework. Together with `Op::Review` / `EnteredReviewMode` / `ReviewDecision`, Codex also has a dedicated **Review mode** (a typical use: dispatch a reviewer subagent to review a diff).

**This is exactly the continuation of Codex's soul at the multi-agent layer**: Claude Code leans toward "let a human be the gatekeeper," Codex leans toward "when no one's around, dispatch an AI to gatekeep" — even the most typical use of a "split-off" is for **security review**. CC's subagents are more "split-offs that do work for me," while Codex's signature subagent is "a reviewer that gatekeeps for me."

---

## 3. Teaming up (Multi-Agent): from "split-off" to "team"

A split-off is "master and servant" (parent spawns child, child returns). When the task needs **multiple peer agents collaborating in parallel**, you need team infrastructure. This is where the two systems diverge most completely.

### Claude Code: the filesystem is the message bus

learn-cc's s15–s17 reveal a plain yet powerful design — **no central message bus; coordination relies entirely on the filesystem**:

- **MessageBus = a file inbox** (s15): every agent has a directory acting as its "inbox"; sending a message means writing a file into the other party's directory, and receiving means reading your own directory. No middleware needed — the filesystem is inherently persistent, observable, and cross-process.
- **15 message types**: request, response, status update, claim, handoff... forming a communication vocabulary.
- **A protocol state machine** (s16): `ProtocolState` tracks request state, a four-step protocol flow, `dispatch_message` routes by type, and `match_response` does type validation — giving "the conversation between teammates" structure so the wires don't cross.
- **Lead + teammates + autonomous claiming** (s17): there's one Lead and several teammates; teammates go through a **WORK → IDLE → SHUTDOWN** lifecycle, and **when idle they poll a "task board" and claim unclaimed tasks themselves** (`scan_unclaimed_tasks` + the owner check in `claim_task`), sending a summary back to the Lead when done. When idle, teammates **wait rather than exit**, and periodically **re-inject their own identity** to guard against context drift.
- **Permission bubbling**: a teammate's dangerous operations bubble up to the Lead/main terminal for approval.

In one sentence: **CC makes multi-agent into "a crew of teammates running on your machine that, via a shared-filesystem inbox and a protocol, autonomously claim and collaborate to finish the tasks on the board."** Extremely local-first, decentralized, observable (you can directly `cat` their inboxes to see what they're talking about).

### Codex: a network of identity-bearing threads + cloud + interop + invocable

Codex doesn't make a team into "teammates in file inboxes," but into a more structured infrastructure oriented toward cloud and interop:

- **Agent graph + identity** (section 1): multiple agents are naturally identity-bearing nodes in a graph, and parent-child/spawn relationships are persistently tracked.
- **Collaboration Mode**: `config_types.rs` has `CollaborationMode` (with model + effort settings), the `collaboration-mode-templates` crate, and "the initial collaboration mode at TUI startup." That is, Codex abstracts "how to collaborate" into a configurable, switchable **mode**.
- **AgentMessage**: `protocol/src/models.rs` has `AgentMessage { content: Vec<AgentMessageInputContent> }` — inter-agent messages are a first-class content type in the protocol, not routed through files.
- **cloud-tasks** (`cloud-tasks/` `cloud-tasks-client/`): dispatch agent tasks **to the cloud** to run — something CC's local file-inbox model simply can't give. Codex Web is built on top of this.
- **external-agent-sessions** (`external-agent-sessions/` `external-agent-migration/`): can **parse and import the session history of "external agents"** (`detect_recent_sessions` / `load_session_for_import`, based on `RolloutItem`) — i.e. take over another agent's (or even another tool's) history and continue. **Interop** is a first-class citizen.
- **Codex as an MCP server** (see the MCP chapter): Codex can **be invoked as a tool by another agent** (`mcp-server` exposes a `codex` tool that runs an entire task). In a multi-agent world, this means **Codex is itself an invocable member of someone else's team**.

In one sentence: **Codex makes multi-agent into "a network of threads that have identities, have a graph, can go to the cloud, can import each other's history, and can be invoked as a tool by other agents."** It doesn't assume everyone is on the same machine and the same filesystem.

---

## ★ Key point: how do agents actually communicate

The core of teaming up is **communication**. The two systems make starkly different choices on "how one agent passes a message to another": **CC puts communication in the filesystem (out-of-band), Codex puts communication into the protocol itself (in-band).** This is the section in the whole piece most worth a close look.

### Claude Code: file inbox + polling (out-of-band communication)

CC's agent communication is built entirely on the filesystem — **the model protocol itself has no idea "teammates" exist**; communication is a pipe the harness rigs up on the side.

- **Inbox = a file**: every agent has a mailbox (real path `~/.claude/teams/{team}/inboxes/{agent}.json`). Sending a message = appending a JSON entry to the other party's file; `proper-lockfile` file locking guards against concurrent writes (up to 10 retries). Reading is **consuming**: read and delete.
- **Polling, not push**: the Lead uses `useInboxPoller` to scan the inbox **every 1 second**, and any message is submitted to the model as a new turn; teammates use `useSwarmPermissionPoller` to poll approval replies **every 500ms**. No long-lived connection — just reading files on a timer.
- **15 structured message types**: communication isn't freeform text but a vocabulary —

  | Type | Direction | Use |
  |---|---|---|
  | `plain text` | Bidirectional | Ordinary communication (wrapped in `<teammate-message>` and handed to the model) |
  | `idle_notification` | Teammate→Lead | I finished this turn, going idle |
  | `permission_request` / `_response` | Bidirectional | Operation approval request/reply |
  | `plan_approval_request` / `_response` | Bidirectional | Plan approval |
  | `shutdown_request` / `_approved` / `_rejected` | Bidirectional | Graceful shutdown handshake |
  | `task_assignment` | Lead→Teammate | Assign work |
  | `team_permission_update` / `mode_set_request` | Lead→Teammate | Broadcast/modify permissions |
  | `sandbox_permission_*` | Bidirectional | Network permission request/reply |
  | `teammate_terminated` | System | Teammate-removed notification |

- **How it enters the model's view**: a text message is wrapped in a `<teammate-message>` XML tag and injected into the recipient's next-turn context. To the model, "what a teammate said" = an extra chunk of tagged text in its context.
- **Team registry**: `~/.claude/teams/{team}/config.json` records the Lead and members (name, type, color, whether active).
- **Temperament**: decentralized (no central broker — everyone writes each other's files), observable (you can `cat` an inbox to watch them chat), language-agnostic — but **assumes a shared filesystem** (the same machine).

### Codex: AgentMessage inside the protocol (in-band communication)

Codex builds agent communication **into the protocol itself** — it's not a file pipe on the side but a class of protocol item on equal footing with "user messages" and "tool calls."

- **Submission side**: a first-class `Op::InterAgentCommunication { communication }` ([`protocol.rs:499`](../../codex/codex-rs/protocol/src/protocol.rs)). The comment explicitly states it "should be recorded as assistant history" — an inter-agent communication **enters the thread's persistent record, the rollout** (see [the context piece](context-cc-vs-codex.en.md)).
- **History side**: `ResponseItem::AgentMessage { author, recipient, content }` (`models.rs`) — an agent message is a kind of **ResponseItem** in the Responses protocol, alongside `Message`/`Reasoning`/`FunctionCall`, carrying its own `author` (who sent it) and `recipient` (who it's sent to).
- **Addressing via AgentPath, with multiple recipients**: `InterAgentCommunication` carries `recipient: AgentPath` + `other_recipients: Vec<AgentPath>` (`protocol.rs:626`) — like email's **To + Cc**, one message can go to multiple agents. `AgentPath` is path-style addressing, echoing the agent graph from section 1.
- **Content can be encrypted**: `AgentMessageInputContent::EncryptedContent { encrypted_content }` — inter-agent messages can be **encrypted** (echoing agent-identity's signed identity). In multi-tenant/untrusted environments, what A sends to B is invisible to any man in the middle.
- **Carries "phase" semantics**: `MessagePhase` (`Commentary` / `FinalAnswer`) distinguishes "midway musings" from "the final answer," letting the recipient know whether this is process or conclusion.
- **Temperament**: in-band (communication is part of the history, naturally auditable and replayable), doesn't assume a shared filesystem (can cross machines / go to the cloud), can be encrypted + identity-bound, multi-recipient addressing.

### One table to grasp the "communication" divide

| Dimension | Claude Code | Codex |
|---|---|---|
| Communication location | **Out-of-band**: filesystem inbox | **In-band**: the `AgentMessage` item in the protocol |
| Transport | Write the other party's `.json` mailbox | Submit `Op::InterAgentCommunication` |
| Delivery | **Polling** (Lead 1s / teammate 500ms) | Protocol event stream, flowing with the turn |
| Addressing | Filename = agent name | `AgentPath` + multiple recipients (To/Cc) |
| Concurrency safety | `proper-lockfile` file lock | Handled at the protocol/thread layer |
| Message vocabulary | 15 structured types | A communication item + phase (commentary/final) |
| Security | Relies on file permissions | **Content can be encrypted + cryptographic identity** |
| Persistence | Mailbox file (deleted once read) | **Enters the rollout persistent record** |
| Observability | Just `cat` the inbox to watch | Audit in the rollout / event stream |
| Cross-machine | No (assumes the same filesystem) | **Yes** (protocol messages, can go to the cloud) |
| How the model sees it | Injected `<teammate-message>` text | Already an `AgentMessage` item in the history |

### Why one "out-of-band file" and one "in-band protocol"?

- **CC picks the file inbox**: because it's local-first. On a single machine, the filesystem is a ready-made, reliable, observable IPC — you can even directly `cat` to see what the agents are saying, a superb debugging experience; polling is simple enough to never go wrong. The cost is that it **assumes a shared filesystem** and is hard to span across machines. Communication is deliberately made a "harness-layer pipe," keeping the model protocol clean.
- **Codex picks in-protocol messages**: because it's born for cloud/distributed/auditable. When agents may be on different machines, or even in different trust domains, "writing the other party's file" simply doesn't hold up — it must be a **routable, encryptable, identity-bearing protocol message that naturally enters the persistent record**. The cost is heavier and more "black-box" (you can't simply `cat`; you have to look in the rollout/event stream).

**In one sentence**: CC treats agent communication as **mail on a shared filesystem**, Codex treats it as **a first-class message in the protocol that can be encrypted, routed, and audited**. The former is the wisdom of Unix pipes, the latter the mindset of distributed systems — once again, the scenario (local vs. cloud) decides the mechanism.

---

## 4. Cross-cutting comparison: recursion, permissions, sync/async, local/cloud

| Dimension | Claude Code | Codex |
|---|---|---|
| Split-off vehicle | `task` tool, discarded when done | Spawned thread, enters the agent graph |
| Split-off mode | Normal / **Fork (shared cache)** / General | thread + identity + rollout |
| Signature subagent | A general-purpose split-off that "does work for me" | **A reviewer that "gatekeeps for me" (auto_review)** |
| Recursion guard | Sub-agent has no `task`; `isInForkChild()` checks the tag | The graph topology naturally records depth/edge state |
| Permissions | Isolates context but **does not isolate permissions**; bubbles up to the parent terminal for approval | Subagents are likewise bound by approval/sandbox; can route to a reviewer subagent |
| Sync/async | Synchronous wait, or `run_in_background` async + notification | Threads/cloud tasks are naturally async and persistable |
| Team coordination | **Filesystem inbox** + 15 message types + protocol + autonomous board | In-protocol `AgentMessage` + collaboration mode + cloud-tasks |
| Boundary | Same machine, same filesystem, local-first | Cross-machine, cloud, can import external sessions, can be invoked |
| Observability | Just `cat` the inbox to watch | rollout + agent graph + identity, audit-leaning |

---

## 5. Why different? Settling the account in full

> **CC makes multi-agent into "a self-governing crew of teammates on your machine coordinated via file inboxes"; Codex makes it into "a network of threads that have identities, can go to the cloud, can interoperate, and can be invoked."**

The root cause is still those three layers (same source as [the context piece](context-cc-vs-codex.en.md)):

1. **Scenario**: CC targets local interaction — one person, one machine, so "the filesystem as the bus, teammates autonomously claiming the board, permissions bubbling up to your terminal" is both simple and fitting, and you can even watch with your own eyes. Codex targets cloud/unattended/auditable — so "a persisted agent graph + cryptographic identity + cloud-tasks + rollout" is the foundation.
2. **Interop ambition**: Codex's `external-agent-sessions` and MCP-server show it wants to **interoperate with other agents and become a building block in someone else's system**; CC's team is more like "in-house teammates collaborating in a closed loop."
3. **Continuation of the security temperament**: Codex's most signature use of a "split-off" is even **a reviewer subagent (auto_review)** — carrying "there must be a gatekeeper even when no one's around" through to the multi-agent layer; CC keeps the human in the approval loop (permissions bubble up to your terminal).

Both are self-consistent. One treats multi-agent as a **local collaboration mode**, the other as **distributed infrastructure**.

---

## 6. Think it over

<div class="think">

1. CC uses "the filesystem as the message bus" — plain, but you can `cat` to watch. Codex uses in-protocol `AgentMessage` + an agent graph — structured but more "black-box." When debugging a 5-agent team that's deadlocked, which would you rather have?
2. Codex gives agents a **cryptographic identity**. In what scenarios must "which agent made this change, authorized by whom" be cryptographically provable? (Hint: think compliance, multi-tenancy, supply chain.)
3. "The approver is also a subagent" (auto_review) — letting one AI review whether another AI should execute a dangerous command. Could this reviewer be fooled in the same way? What "superpower" that it shouldn't have would you have to give it for it to be trustworthy?
4. CC's teammates "go claim tasks from the board themselves when idle." What happens if two teammates claim the same task at the same time? The owner check in `claim_task` resolves the race — but if it's cross-machine (like Codex's cloud), is the file lock still enough?
5. Codex can dispatch tasks to the cloud and import other agents' sessions. Once the boundaries between "my agent," "the cloud agent," and "someone else's agent" blur, which becomes the bottleneck first — "context" or "identity"?

</div>

---

[← Back to the learn-codex overview](../README.en.md) · Sister piece: [A complete guide to context handling](context-cc-vs-codex.en.md) · Related chapters: subagents and teams, approval and Guardian, MCP
