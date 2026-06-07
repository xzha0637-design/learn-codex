# s15: MCP — client + server: plug in what you lack, and let others plug into you

> 🌐 **English** · [中文版](README.md)

> *"MCP is the standard socket of the agent world: any tool can plug into any agent, and any agent can turn around and plug into another agent."*

[learn-codex overview](../README.en.md) · [Guardian](../s14_guardian/README.en.md) → **This chapter** → [Config & Profiles](../s16_config/README.en.md)

---

## Get the idea straight first: why we need a "standard socket," and the single most brilliant thing about it

Up to this chapter, every tool in your agent's hands has been **hard-coded into its own source**: `shell`, `apply_patch`… Want to add a new capability? You have to edit the agent's source. The MCP this chapter is about exists, at bottom, to solve a one-sentence problem: **how do we make agents and tools plug-and-play, the way appliances and sockets are?** Get the following three escalating ideas, and this chapter clicks.

**Idea 1: with no standard, you get N×M pieces of glue code.**
Imagine you have 3 agents (Codex, Claude Code, your own script) and 5 external capabilities (query GitHub issues, run SQL, draw charts, check the weather, read the company knowledge base). If each agent has to **write a separate piece of integration code** for each capability — define the tool schema, stuff it into the tool table, write the handler, handle auth — that's 3×5 = 15 pieces of mutually non-reusable glue. Add one more agent or one more capability and you write another whole column, another whole row. This is exactly the world of "no standard interface": every combination has to be soldered up from scratch.

**Idea 2: agree on one standard "plug shape," and N×M collapses to N+M.**
The fix is exactly USB-C's: **agree on one interface everyone obeys**. The capability side implements the "standard plug" once (an MCP **server**), the agent side implements the "standard socket" once (an MCP **client**), and the two sides speak the same JSON-RPC dialogue — just a few fixed "questions": `initialize` (handshake), `tools/list` (what tools do you have?), `tools/call` (call this tool for me). Now any client can connect to any server: the 5 capabilities each become a server once, the 3 agents each become a client once, for a total of just 3+5 = 8 pieces of code, with **zero extra cost** for any new combination. The entire power of a "standard" is turning multiplication into addition.

**Idea 3 (the soul of this chapter): the socket and the plug can be the same thing — this is bidirectionality.**
You may have heard the first two points elsewhere. MCP's truly profound and most easily overlooked point is this: **an agent doesn't have to be only "the one who uses tools"; it can turn around and make its entire self into "a tool" that other agents call.** Codex is both a client (going out to use someone else's echo / SQL / charting tools) and a server — exposing "run an entire Codex coding task" as a tool named `codex`, so that another agent (even Claude Code, even a CI pipeline) can call it like a function: "Hey, fill in the tests for this module and get them passing for me."

Why is this so crucial? Because it directly decides whether agents can **orchestrate each other and stack into a system**. If an agent can only be a client, it's forever "the one at the top of the pyramid, facing humans"; but once it can also be a server, agents can **call each other**: the upper layer splits the task and outsources the grunt work to the lower layer; multiple Codexes can be spun up in parallel by one orchestrator for batch work. By analogy — one who can use tools is a **capable worker**; one who can be called as a tool by others is **a trade you can hire at any moment and drop into any assembly line**. Codex stands on both ends, which is the most direct expression of its being "born for unattended, programmatically-orchestrated use."

This chapter brings these two halves together: first Codex as client (using someone else's tools), then Codex as server (being used as a tool by someone else). Same JSON-RPC, two directions.

## Problem

Let's concretely feel the pain points of each direction.

**The pain of direction A (when you should use someone else's tool):** You ask Codex to "look at which open GitHub issues are in the repo and pick one to fix." But there's simply no "query GitHub" item among Codex's built-in tools. Should you cram a GitHub API integration into Codex's source? Then tomorrow you want to connect Jira, the day after the company's internal ticketing system — do you keep editing the source each time? **Capabilities are scattered across the world, while the agent's tool table is hard-coded — what's missing in between is a plug-and-play bridge.**

**The pain of direction B (when you should be called by someone else):** Conversely, Claude Code is helping you refactor a large repo, hits a stretch of code it isn't familiar with, and wants to "outsource" an isolated subtask to Codex. Or your CI pipeline wants, at some step, to directly shout "have Codex fix this lint error." But if Codex can only be a **human-facing CLI/TUI**, these scenarios can't connect — you always need a live human sitting in front of a terminal typing commands. **For Codex to be programmatically driven, to be treated by other agents as a callable capability, it has to turn around and be a server.**

The two pain points are really two sides of one coin: **between an agent and the outside world, you need one bidirectional, standard interface.** That is MCP.

## Solution

MCP (Model Context Protocol) standardizes "agent ↔ tool" into a JSON-RPC dialogue. This chapter uses a **fully offline**, in-process implementation to demonstrate both directions.

**Direction A (client):** In the middle sits a **connection manager**. It holds several server connections, aggregates everyone's tool listings, **prefix-namespaces** the tool names into `mcp__<server>__<tool>` (so that two servers both named `search` don't collide), and hands them to the model. When the model issues a call, it **routes** it back to the right server by prefix.

**Direction B (server):** Codex itself implements a `handle(request)` and exposes tools to the outside. The most critical tool is named **`codex`**: call it, pass a natural-language `prompt`, and Codex **launches an entire agent loop** to run the task to completion, then sends the result back as the `tools/call` response.

```
   ┌─────────── 方向 A：Codex 当 client ───────────┐    ┌──── 方向 B：Codex 当 server ────┐
   │                                               │    │                                 │
   │  回合循环 ──mcp__demo__echo──┐                  │    │   别的 agent / CI / 脚本         │
   │     ▲                       ▼                  │    │        │ tools/call             │
   │     │结果回灌   ┌────────────────────┐         │    │        │  name="codex"          │
   │     └──────────┤ McpConnectionMgr   │         │    │        ▼  prompt="修好这个 bug" │
   │                │ ·add_server/init    │ JSON-RPC│    │   ┌────────────────────────┐   │
   │                │ ·list_all_tools     │◀───────▶│    │   │ CodexMcpServer.handle() │   │
   │                │ ·call_tool(srv,tool)│  (真版  │    │   │  initialize             │   │
   │                └────────────────────┘  走 stdio│    │   │  tools/list→[shell,codex]│  │
   │                         ▲                │      │    │   │  tools/call ─┬ shell     │   │
   │   ┌─────────────────────┴──────────┐    │      │    │   │              └ codex →    │   │
   │   │ FakeMcpServer（别人家的工具）    │    │      │    │   │     run_task() = s01 循环 │   │
   │   │  echo / add                     │    │      │    │   └────────────────────────┘   │
   │   └─────────────────────────────────┘   │      │    │        │ result.content         │
   └─────────────────────────────────────────┘      │    │        ▼ 回到调用方             │
                                                     │    └─────────────────────────────────┘
   两个方向同源：都是 initialize / tools/list / tools/call 这套 JSON-RPC，只是谁问、谁答互换。
```

For the sake of offline, zero-dependency operation, this chapter strips the stdio transport layer from both sides and swaps in **in-process method calls**: `handle(request_dict) -> response_dict`. Real Codex treats each server as a **subprocess** and communicates via JSON-RPC over stdio — but the shape of the request/response dicts is identical; all that's swapped out is the "transport layer" skin.

## How it works

See [code.py](code.py). It puts both directions in **one file**, sharing the same JSON-RPC shape and the same s01 agent loop.

### Direction A: Codex as client (using someone else's tools)

**Step 1 — in-process server stand-in.** `FakeMcpServer.handle()` dispatches by the JSON-RPC `method` and exposes two tools, `echo` / `add`:

```python
def handle(self, request: dict) -> dict:
    rid, method, params = request.get("id"), request.get("method"), request.get("params", {}) or {}
    if method == "initialize":
        return self._ok(rid, {"protocolVersion": "2025-06-18", ...})
    if method == "tools/list":
        return self._ok(rid, {"tools": self._tools})
    if method == "tools/call":
        return self._call_tool(rid, params.get("name", ""), params.get("arguments", {}) or {})
```

**Step 2 — connect + aggregate + namespace.** `McpConnectionManager.add_server()` first sends an `initialize` handshake; `list_all_tools()` rewrites each server's tool names into `mcp__<server>__<tool>` and converts MCP's `inputSchema` field into the `parameters` used by Responses API tools:

```python
out.append({
    "name": f"{MCP_PREFIX}{name}{MCP_DELIM}{t['name']}",   # mcp__demo__echo
    "description": t.get("description", ""),
    "parameters": t.get("inputSchema", {...}),             # MCP 叫 inputSchema
})
```

This aligns with the real source [`connection_manager.rs`](../../codex/codex-rs/codex-mcp/src/connection_manager.rs)'s `list_all_tools()` → `normalize_tools_for_model_with_prefix`; the prefix constant `mcp__` and delimiter `__` come from [`tools.rs:28/260`](../../codex/codex-rs/codex-mcp/src/tools.rs).

**Step 3 — route the call.** In the agent loop (**copied verbatim from s01**), tool dispatch switches from "look up the HANDLERS dict" to `dispatch()`: anything with the `mcp__` prefix gets split into `(server, tool)` and handed to `manager.call_tool()`, and the returned `content` blocks are assembled into plain text and fed back to the model.

```python
def dispatch(name, arguments):
    server, tool = split_mcp_tool_name(name)        # mcp__demo__echo → ("demo","echo")
    return manager.call_tool(server, tool, arguments)
```

### Direction B: Codex as server (being used as a tool by someone else)

The core is a single `CodexMcpServer.handle()`, aligned with the real source [`message_processor.rs`](../../codex/codex-rs/mcp-server/src/message_processor.rs)'s `process_request`: likewise dispatching by `method` to `initialize` / `tools/list` / `tools/call`, and likewise returning `-32601 method not found` for an unknown method. `initialize` declares "supports tools / toolListChanged" — aligning with the real `handle_initialize`'s `ServerCapabilities::builder().enable_tools().enable_tool_list_changed()`.

The most critical part is the **`codex` tool = run an entire Codex task** inside `tools/call`. `run_task()` is just **the s01 agent loop** copied once more: it takes the prompt as the first user message, the model loops calling `shell` until it's done, and it returns the final text:

```python
def _call_tool(self, rid, name, args):
    if name == "shell":
        return self._tool_result(rid, run_shell(args["command"]))
    if name == "codex":
        return self._tool_result(rid, run_task(args["prompt"]))   # ← 跑一整个任务
```

This is exactly what the real server does, just heavier in the real version: `handle_tool_call_codex` (`message_processor.rs:346`) parses the arguments, `spawn`s an async task, and calls [`run_codex_tool_session`](../../codex/codex-rs/mcp-server/src/codex_tool_runner.rs), which submits the prompt into a **complete** Codex thread via `Op::UserInput`.

### **Walk through it**: direction B's `codex` tool, how data flows in a single call

The most worthwhile thing to walk through is "another agent treating an entire Codex as one tool call" — because it strings together both directions of this chapter and all the previous chapters. Suppose the CI pipeline sends this JSON-RPC request (this is `--demo`'s step 4):

**① The incoming request** (an ordinary `tools/call`, tool name `codex`):

```json
{ "jsonrpc": "2.0", "id": 4, "method": "tools/call",
  "params": { "name": "codex", "arguments": { "prompt": "运行 `echo built by codex`" } } }
```

**② `handle()` dispatches by method** → `tools/call` → `_call_tool(name="codex", ...)` → discovers it's `codex`, so it calls `run_task("运行 `echo built by codex`")`. **Why this hop is the key point**: from here on, the Codex being called runs the **exact same** agent loop as when you use it day-to-day — it doesn't know it was invoked by an agent; it's just running a task.

**③ `run_task`'s first lap inside**: sends the prompt as the first user message to the model. The model (here an offline mock) sees `shell` in the tool table, digs the command out of the prompt's backticks, and produces a `function_call`:

```json
{ "type": "function_call", "call_id": "mock_call_1",
  "name": "shell", "arguments": "{\"command\": \"echo built by codex\"}" }
```

**④ Execute + feed back**: `run_shell("echo built by codex")` actually runs in a subprocess, gets back `"built by codex"`, wraps it as a `function_call_output`, and appends it back into the conversation.

**⑤ `run_task`'s second lap**: the model is called again; this time it sees the conversation already has the tool result, so it no longer calls a tool and closes out directly, returning the final text `"[mock] 工具已执行，结果片段：built by codex"`. `run_task` returns this string.

**⑥ The outgoing response**: `_tool_result` wraps it into a standard MCP `content` block and sends it back to CI:

```json
{ "jsonrpc": "2.0", "id": 4,
  "result": { "content": [ { "type": "text", "text": "[mock] 工具已执行，结果片段：built by codex" } ],
              "isError": false } }
```

See it? — **to CI, it just "called a tool and got back a stretch of text,"** no different from calling `echo`; yet behind this one tool call is an entire Codex session running the agent loop, calling shell, reading the result, and closing out. This is the entire magic of "agent as tool": hiding an arbitrarily complex autonomous process inside one perfectly ordinary `tools/call`.

`--demo` runs both directions back-to-back: direction A prints the namespaced tool table, directly does `dispatch("mcp__demo__add",{a:2,b:3})` to see `→ 5`, then walks a full agent loop; direction B feeds `initialize` → `tools/list` → `tools/call(shell)` → `tools/call(codex)` in turn, that last one being the lap we just walked.

## Production-grade: MCP servers live out-of-process — they'll hang, crash, and collide on names

The biggest reality of MCP is: each server is an **independent process you don't control** (possibly even on another machine). It can fail the handshake, hang halfway through a call, and different servers can expose **identically named** tools. The toy assumes they all behave; a production-grade harness assumes they'll all go sideways.

### 1. Timeouts: one hung tool must not freeze the entire agent

After `tools/call` goes out, the server may never return (infinite loop, network black hole, zombie subprocess). Wait on it synchronously and the entire agent freezes along with it. Real Codex gives each server a `tool_timeout` ([`connection_manager.rs:499`](../../codex/codex-rs/codex-mcp/src/connection_manager.rs)) and, when it's up, **stops waiting and feeds the timeout back to the model as an error**. This chapter's `call_tool_with_timeout` demonstrates this:

```
② 调用超时：一个 hang 住的工具不会把 agent 永远卡死 ——
   调用 slow/hang（超时 0.1s）：
   → [mcp timeout] 工具 slow/hang 超过 0.1s 未返回；停止等待、丢弃这次调用，错误回灌给模型
```

### 2. Connection resilience: one server crashing must not drag down the rest

You might have 5 MCP servers mounted, and one of them crashes right at initialize. The production-grade approach is to **connect concurrently and skip only the one that fails** (real Codex uses `join_set` to spin up all servers concurrently, `connection_manager.rs:302`; there's also `wait_for_server_ready(timeout)` to cap the handshake too). This chapter's `add_server` wraps the handshake in a try:

```
① 连接韧性：一个 server 初始化就崩，其余照常连上 ——
   [mcp] 跳过 'broken'：初始化失败（server crashed during initialize），其余服务器照常
   最终可用 server：['demo']（broken 被跳过，没拖垮 demo）
```

### 3. Name collisions + too many: namespacing and deferred exposure

- **Collisions**: two servers both expose a `search` — which one should the model call? This chapter long ago **namespaced** tool names into `mcp__<server>__<tool>` (real Codex also runs `sanitize_name` to strip illegal characters, `codex_apps.rs`) — multiple servers won't collide.
- **Too many**: one server might fling back hundreds of tools, and stuffing them all into the prompt would blow up the context. This is exactly [s02's ToolSearch / deferred exposure](../s02_tool_use/README.en.md) — once MCP tools exceed a threshold (`DIRECT_MCP_TOOL_EXPOSURE_THRESHOLD = 100`), they're not exposed directly but discovered via model search instead.

### 4. Foreign tools are subject to approval + sandbox just the same

MCP tools come from **third parties** and can be trusted even less. Their calls (`McpToolCall`) go through the approval gate just the same ([s04](../s04_approval/README.en.md)), get risk-assessed by Guardian ([s14](../s14_guardian/README.en.md)), and run commands in the sandbox just the same ([s05](../s05_sandbox/README.en.md)). "Being able to plug in someone else's tools" does not mean "let them through the moment they're plugged in."

> In one line: bringing someone else's tools in, the hard part isn't the JSON-RPC, it's **assuming the other side will hang, crash, collide on names, and is untrusted** — and then catching every one of those.

## 🆚 How it differs from Claude Code

| | Claude Code | Codex | |
|---|---|---|---|
| As MCP **client** (using others' tools) | ✅ (mainstay) | ✅ | nearly identical |
| Tool namespace | `mcp__<server>__<tool>` | `mcp__<server>__<tool>` | same legacy prefix |
| Transport | stdio / SSE / HTTP | stdio (rmcp) / Streamable HTTP | broadly similar |
| As MCP **server** (being called by others) | basically doesn't | ⭐ **also can** | where they diverge |
| Granularity of being called | — | one `tools/call codex` = run **an entire Codex task** | agent as tool |
| Typical caller | a human at a terminal | another agent / CI pipeline / cloud orchestrator | program-facing vs human-facing |

**Why?** The top half of this table is nearly **identical** on both sides — and that's precisely the point of MCP as an "open shared standard": **the client side is everyone's common foundation**; everyone should be able to connect to others' tools, right down to the matching namespace convention (`mcp__server__tool`), because they all have to fit into the same kind of model API with "flat tool names." Competing on the client side is pointless; the value of a standard is consistency.

The real divergence is the ⭐ in the bottom half: **directionality**. Codex isn't just a client; it can turn around and **be a server**, exposing its entire agent self as a tool for others — this is **agents-as-tools (treating an agent as a tool)**.

This one difference is the most direct landing point of the book's through-line: "**Claude Code guards for humans at the application layer; Codex closes for unattended operation at the kernel layer.**" Claude Code positions itself as a **human-facing interactive frontend**: it's a client, bringing external capabilities in for humans to use; it doesn't much need to expose "the entire Claude Code" as someone else's function — because its core value is that block of **human-machine interaction** experience.

Codex, by contrast, bets on "**low human intervention, orchestratable**." A Codex that can be a server means it can be slotted into CI (one step directly does `tools/call codex { prompt: "fix the failing tests" }`), treated by a cloud orchestrator as a sub-agent that can be scheduled in parallel, and treated by another agent (including Claude Code itself) as a tool — the upper layer handles decomposition and orchestration, and outsources the grunt work to Codex. In other words: **being a client makes Codex someone else's consumer; being a server makes Codex someone else's capability.** The latter pays off only when you bet that "an agent should be programmatically drivable when no one is watching" — and that is Codex's consistent disposition (kernel sandbox [s05](../s05_sandbox/README.en.md), headless `codex exec`, SQ/EQ multi-frontend [s10](../s10_sq_eq_protocol/README.en.md), all serving this).

## Deep dive: teaching version vs real Codex source

The teaching version compresses each direction to a few dozen lines in one shared file, making clear "connect → namespace → route" and "dispatch → `codex` tool runs an entire task." The complexity of real Codex's two crates — the client [`codex-rs/codex-mcp`](../../codex/codex-rs/codex-mcp/) and the server [`codex-rs/mcp-server`](../../codex/codex-rs/mcp-server/) — is almost entirely in these few things: "transport concurrency, lifecycle, naming safety, bidirectional approval."

<details>
<summary>1. Transport: in-process dict calls vs subprocesses over stdio / three tokio tasks</summary>

Both sides' `handle()` in the teaching version are ordinary method calls — synchronous, zero latency. Real Codex is completely different:

**Client side** — each MCP server is a spawned **subprocess** (or an HTTP endpoint). Codex uses the [rmcp](https://crates.io/crates/rmcp) library as the client, sending and receiving JSON-RPC over the subprocess's stdin/stdout, or going over `StreamableHttp`. The handshake is a real `initialize`, carrying `ClientCapabilities`, the client `Implementation { name: "codex-mcp-client", ... }`, and pinning the protocol version `ProtocolVersion::V_2025_06_18` (`rmcp_client.rs`). Because it's async + subprocesses, you get a pile of things the teaching version doesn't have: startup timeout (`DEFAULT_STARTUP_TIMEOUT = 30s`), tool-call timeout (`DEFAULT_TOOL_TIMEOUT = 120s`), a startup state machine (`McpStartupStatus::{Starting, Ready, Failed, Cancelled}`), and `shutdown()`-ing all subprocesses on process exit.

**Server side** — the real server ([`lib.rs:run_main`](../../codex/codex-rs/mcp-server/src/lib.rs)) is a three-task pipeline:

```
stdin ──► [stdin_reader] ──incoming_tx──► [processor] ──outgoing_tx──► [stdout_writer] ──► stdout
          逐行解析            MessageProcessor              序列化 + 写一行 JSON
          成 JsonRpcMessage    ::process_request
```

It uses `tokio::spawn` to start three concurrent tasks, connected by channels (`CHANNEL_CAPACITY = 128`) in the middle, and finally `tokio::join!`s to wait on them together. The reason for splitting into tasks: `tools/call codex` may run a long time and must never block reading stdin / writing stdout. The teaching version's synchronous `handle()` doesn't have this problem because the mock task returns instantly.

| | Teaching version | Real codex-rs |
|---|---|---|
| Transport | in-process method call | JSON-RPC / stdio (subprocess) or HTTP |
| Protocol version | string `"2025-06-18"` | `ProtocolVersion::V_2025_06_18` |
| Concurrency | single-threaded synchronous | client async rmcp; server three tokio tasks |
| Timeout/cancellation | none | startup 30s, tool 120s, `CancellationToken` |

</details>

<details>
<summary>2. Name collisions, length limits, and SHA-1 hash suffixes</summary>

The teaching version just concatenates `mcp__{server}__{tool}` and calls it done, because our names are short and won't collide. The real `normalize_tools_for_model_with_prefix` ([`tools.rs:149`](../../codex/codex-rs/codex-mcp/src/tools.rs)) has to solve three real-world problems:

1. **Collisions**: tools from two different servers may end up with the same normalized name → append a `_<first 12 of sha1>` hash suffix to the conflicting items (`append_hash_suffix` / `CALLABLE_NAME_HASH_LEN = 12`).
2. **Length**: model APIs have a cap on tool names, `MAX_TOOL_NAME_LENGTH = 64` bytes; exceed it and you truncate + hash.
3. **Illegal characters**: `sanitize_responses_api_tool_name` cleanses characters in server/tool names that the API won't accept.

So the real version distinguishes two sets of names — "the raw name (used for the protocol call)" and "the model-visible name (used to feed the model)" — whereas the teaching version has only one.

</details>

<details>
<summary>3. Connection lifecycle, resource aggregation, and tool-exposure filtering</summary>

In the teaching version, `clients` is a `dict`, `add_server` handshakes sequentially, and `list_all_tools` mindlessly stuffs all tools into the model. The real `McpConnectionManager` is far more:

- It uses a `JoinSet` to start all servers **concurrently**, sending each startup's progress to the frontend via events (`McpStartupUpdateEvent` / a final `McpStartupComplete` summarizing ready / failed / cancelled).
- It also aggregates **resources** and **resource templates** (`list_all_resources` / `list_all_resource_templates`, with cursor pagination and duplicate-cursor detection) — MCP isn't only tools, there are also readable resources, which the teaching version skips entirely.
- **Visibility filtering**: `tool_is_model_visible` (`connection_manager.rs:86`) checks whether a tool's `_meta.ui.visibility` contains `"model"`, leaving a back door for "UI tools shown only to humans, not given to the model."
- **Count threshold**: `build_mcp_tool_exposure` ([`mcp_tool_exposure.rs`](../../codex/codex-rs/core/src/mcp_tool_exposure.rs)) has a `DIRECT_MCP_TOOL_EXPOSURE_THRESHOLD = 100`: when there are too many tools, it doesn't expose them all directly but converts them into "**deferred** (searchable)" tools, avoiding blowing up the model context all at once.

</details>

<details>
<summary>4. The server's bidirectionality: requesting approval back from the client, and codex-reply multi-turn follow-ups</summary>

These are the two most interesting points of "Codex as server," and the ones that best embody MCP's bidirectionality:

**Reverse approval (elicitation)** — when the Codex being called needs to run a dangerous command or apply a patch mid-task, it needs approval, but right now there's **no one** — only the client that called it. So the approval itself also goes over MCP, sent **in reverse**: `exec_approval.rs` / `patch_approval.rs` define `ExecApprovalElicitRequestParams` / `PatchApprovalElicitRequestParams`; the server sends an elicitation/approval request to the client and waits for the client's response before continuing. That is: an MCP server both **responds** to the client's `tools/call` and can **proactively send** requests to the client — "the sub-agent delegates approval to the upper layer." The teaching version's `run_task` executes `shell` directly without approval (aligning with s01's "no blacklist" tone), so it has no such reverse channel, but in real scenarios "when an agent calls an agent, who should approve" is a very real question ([s14](../s14_guardian/README.en.md)'s Guardian is precisely another answer born for unattended operation).

**Multi-turn follow-up (codex-reply)** — in the teaching version, each `codex` call is a **brand-new** task, unrelated to the others. The real server also exposes a second tool, **`codex-reply`** (`"codex-reply" => ...` in `handle_call_tool`'s `match`): the `structured_content.threadId` returned by the first `codex` call can be carried back by the caller to send `codex-reply { thread_id, prompt }`, **continuing in the same session** (`ThreadManager::get_thread`). This upgrades "one-shot outsourcing" into "stateful multi-turn delegation." The server uses `running_requests_id_to_codex_uuid: Mutex<HashMap<RequestId, ThreadId>>` to map MCP request ids to Codex thread ids.

| | Teaching version | Real codex-rs |
|---|---|---|
| `codex` call | synchronous `run_task` returns str | spawn thread + stream-forward all events |
| Which loop | s01 simplified loop | full core (sandbox/approval/SQ-EQ all in) |
| Return | a stretch of text | text + `structured_content{threadId}` |
| Follow-up | none | `codex-reply` + thread_id |
| Approval | execute directly | reverse elicitation sent back to client |

</details>

## Run

```bash
python s15_mcp/code.py --demo   # 一口气演示两个方向：client 列工具/调用/回灌 + server 处理四个请求（mock，无需 key）
python s15_mcp/code.py          # 交互模式：默认走 client 方向（你的问题 → 模型 → MCP 工具）
```

Interactive mode demonstrates the **client** direction by default (closest to "your day-to-day use of Codex connecting to external tools"). To see the **server** direction (Codex treated as one `tools/call`), just run `--demo` — it feeds the four requests `initialize` / `tools/list` / `tools/call(shell)` / `tools/call(codex)` to `CodexMcpServer.handle()` back-to-back and prints each JSON-RPC response.

Default `backend=mock`, runnable offline. To connect a real model, fill in `OPENAI_API_KEY` in the root `.env` (see [.env.example](../.env.example)).

## Recap

- MCP standardizes "agent ↔ tool" into one set of JSON-RPC (`initialize` / `tools/list` / `tools/call`), collapsing N×M glue code into N+M.
- **Direction A (client)**: the connection manager does three things — connection handshake → aggregate and namespace (`mcp__server__tool`) → route the call. The agent loop is unchanged by a single character; to the loop's eyes, MCP tools and built-in tools are no different.
- **Direction B (server)**: Codex itself does `handle(request)`, and the headline tool `codex` is one call = run **an entire Codex task** (internally just the s01 agent loop); the real version also has `codex-reply` multi-turn follow-ups and reverse approval.
- The two directions are **the same source**: the same JSON-RPC, the same s01 loop, only who asks and who answers swapped.
- On the client side, Codex and Claude Code are nearly identical (the benefit of an open standard); the difference is on the server — Codex exposes its entire agent self as someone else's tool (agents-as-tools), the most direct expression of betting on "unattended, orchestratable."
- **Production-grade**: MCP servers live out-of-process — wrap calls in `tool_timeout` (a hang doesn't freeze the agent), connect concurrently and skip only the one that crashes, namespace tool names to prevent collisions, and put foreign tools through approval + sandbox just the same (see the "Production-grade" section).
- Next stop [s16](../s16_config/README.en.md): one switch flips an entire set of autonomy levels — Config and named profiles.

## Think it over

<div class="think">

1. Real Codex adds a 12-char SHA-1 suffix to colliding tools, plus a 64-byte length cap. If the tool name the model sees becomes `mcp__db__query_a1b2c3d4e5f6`, can it still tell what it does? How should you trade off a namespace's "readability" against its "uniqueness"?
2. Both sides in this chapter are in-process objects — zero latency, never failing. Once you swap in real stdio subprocesses: a server takes 30 seconds to start, or crashes midway — what should the agent loop do — block and wait, skip it, or feed "this tool is temporarily unavailable" to the model as information too?
3. When Codex is treated as a server and, mid-task, needs to run a dangerous command — but right now there's no one, only the upper-layer agent. Who should approve: the upper-layer agent decides itself? Forward it to the human behind the upper layer? Or just `approval_policy: never` + rely entirely on [s05](../s05_sandbox/README.en.md)'s kernel sandbox as backstop? What trust assumptions do the different choices correspond to? Is this in harmony with, or in tension with, the book's through-line of "Codex betting on low human intervention"?
4. Now Codex can be both client and server. So what happens when two Codexes call each other, or even A calls B and B calls back into A? In this kind of "agent topology," how do you prevent infinite recursion, how do you track how many layers a single request actually ran through and how many tokens it spent? Does turning an agent into "a tool that can be assembled arbitrarily" liberate productivity, or open a new black hole of complexity?

</div>
