# Extra-long read: A full breakdown of Claude Code's Messages API vs Codex's Responses API

> 🌐 **English** · [中文版](api-message-vs-responses.md)

> This is an in-depth long read, aimed at readers with **only a little background**. We start from "what is a wire protocol," then lay out — grid by grid — **the order form** that each system hands to the model: how the request is assembled, how the response is taken apart, how tools are encoded, how reasoning is carried, where state lives, what streaming looks like, how failures are reported — and we explain **why** they look different.
>
> Evidentiary basis: Codex comes from this repo's real source `../../codex/codex-rs`, where every field/constant can be traced to a file and line number; Claude Code (hereafter CC) speaks the **Anthropic Messages API**, a **public, documented** protocol, and this article is written from its public spec plus the source-code analysis in [learn-claude-code](../../learn-claude-code/).
>
> ⚠️ **On the strength of the evidence (different from the previous two articles)**: in this article, the Codex side = `codex-rs` source (strong); the **Anthropic Messages API wire format itself** = public protocol spec (strong); but **how CC uses it internally** (retry constants, whether it streams, how the loop branches) = relayed from analysis of the **closed-source** CC (weak, treat as secondhand). Wherever the latter is involved, it is flagged in the text.

[← Back to the learn-codex overview](../README.en.md) · Sister pieces: [Full breakdown of context](context-cc-vs-codex.en.md) · [Full breakdown of subagents and multi-agent](subagent-multiagent-cc-vs-codex.en.md)

---

## 0. For beginners: what is a "wire protocol," and why do the two look different

Between your program and the large model there's a network cable. The model runs on the vendor's servers, and the two of you shout at each other over the network. But the network only carries bytes; it doesn't know concepts like "system prompt," "conversation history," "tool list," or "thinking process" — so the two ends must **agree in advance on an order form**: which cell holds the persona, which cell holds the conversation, which holds the tools, who comes first, and how the reply gets filled in. This set of conventions is called a **wire protocol**.

An analogy: ordering the same cup of coffee, McDonald's order format isn't the same as Starbucks's. Whichever you walk into, you fill out that shop's form. In the world relevant to coding agents, there are mainly three forms:

- **Anthropic Messages** (`POST /v1/messages`) — used by Claude; CC fills out this one;
- **OpenAI Chat Completions** (`POST /v1/chat/completions`) — venerable and general-purpose, but Codex **no longer uses it**;
- **OpenAI Responses** (`POST /v1/responses`) — OpenAI's newer one; Codex fills out this one.

> 📌 A belief that easily goes stale: many people think Codex "also supports Chat Completions as a fallback." **Not anymore.** The `WireApi` enum in `codex-rs` now **has only the single `Responses` variant left**; writing `wire_api = "chat"` in the config will error out and exit ([`model-provider-info/src/lib.rs`](../../codex/codex-rs/model-provider-info/src/lib.rs), around lines 53–80, error message verbatim: *"`wire_api = "chat"` is no longer supported."*). So this article is a clean **two-party** comparison: Messages ⟷ Responses.

These three forms express roughly the same things (system prompt, conversation, tools, reasoning, sampling params), but **the field names and their placement all differ**. And the key point is — **the form's shape is molded by the model, not the other way around**. This is the one sentence running through the whole article; we call it "**the protocol follows the model**" (covered in [s09](../s09_responses_api/README.en.md), and it also showed up in [s03](../s03_apply_patch/README.en.md)):

- Claude is a model that "can talk, can use tools, and can unfold a stretch of **signed plaintext thinking**" → Messages makes thinking a **verifiable plaintext block** inside the message;
- OpenAI's codex line is a "**reasoning model**" — it thinks for a long while first, then decides to act, with its thinking process **encrypted** and a knob for "how deep to think" → Responses makes reasoning a **standalone, encrypted, replayable item** on the form, and gives you an "effort level" knob too.

Once you understand these two forms, you understand the lowest-level phone line of both agents. Below we compare them section by section, following "request → conversation shape → tools → reasoning → state → streaming → end/failure → absent fields → errors."

---

## 1. The full panorama of one request: laying the two forms side by side

First look at "what goes out." Same scenario: the user says "list the files," a `shell` tool is provided, thinking is on. The two forms side by side:

**Claude Code → Anthropic Messages**

```json
POST /v1/messages
{
  "model": "claude-opus-4-8",
  "max_tokens": 8000,
  "system": "You are Claude Code.",
  "thinking": { "type": "enabled", "budget_tokens": 4000 },
  "tools": [
    { "name": "shell", "description": "run a command",
      "input_schema": { "type": "object",
        "properties": { "command": { "type": "string" } },
        "required": ["command"] } }
  ],
  "messages": [
    { "role": "user", "content": "列出文件" }
  ],
  "stream": true
}
```

**Codex → OpenAI Responses** (this is exactly what [`codex-api/src/common.rs`](../../codex/codex-rs/codex-api/src/common.rs)'s `ResponsesApiRequest` serializes to, around lines 183–203)

```json
POST /v1/responses
{
  "model": "gpt-5-codex",
  "instructions": "You are Codex.",
  "input": [
    { "type": "message", "role": "user", "content": "列出文件" }
  ],
  "tools": [
    { "type": "function", "name": "shell", "description": "run a command",
      "strict": false,
      "parameters": { "type": "object",
        "properties": { "command": { "type": "string" } },
        "required": ["command"] } }
  ],
  "tool_choice": "auto",
  "parallel_tool_calls": true,
  "reasoning": { "effort": "medium", "summary": "auto" },
  "store": false,
  "include": ["reasoning.encrypted_content"],
  "stream": true,
  "prompt_cache_key": "<session-uuid>"
}
```

Top-level fields, cell by cell:

| Purpose | Anthropic Messages | OpenAI Responses (Codex) | Notes |
|---|---|---|---|
| Model | `model` | `model` | same |
| System prompt | `system` (can be a block array, can carry cache markers) | `instructions` (string) | different name; see §5 caching |
| Conversation history | `messages[]` (**nested blocks**) | `input[]` (**flat items**) | shape difference, see §2 |
| Tools | `tools[].input_schema` | `tools[].parameters` (flat `type:"function"`) | see §3 |
| Tool choice | `tool_choice:{type,name,…}` | `tool_choice:"auto"` (hard-coded by Codex) | — |
| Parallel tools | `tool_choice.disable_parallel_tool_use` | `parallel_tool_calls: bool` (top-level positive switch) | one is a "disable" switch, one is an "enable" switch |
| Reasoning | `thinking:{type,budget_tokens}` | `reasoning:{effort,summary,context}` | **core difference**, see §4 |
| Output cap | `max_tokens` (**required**) | — (**no such field**) | see §8 |
| Sampling temperature | `temperature`/`top_p`/`top_k` (optional) | — (**not even sent in the request**) | see §8 |
| Streaming | `stream: bool` | `stream: true` (hard-coded by Codex) | — |
| Caching | `cache_control` (block-level breakpoints, ≤4) | `prompt_cache_key` + automatic prefix caching | two philosophies, see §5 |
| State chain | — (none) | `store` + `previous_response_id` (optional) | **capability difference**, see §5 |
| Metadata | `metadata:{user_id}` | `client_metadata` (carries installation_id) | — |

The two forms carry highly overlapping things, but there are three immediately visible divides — exactly what the next few sections will detail: **the shape of the conversation** (nested vs flat), **the encoding of reasoning** (plaintext budget vs encrypted level), and **two fields Anthropic has but the Codex request doesn't** (`max_tokens` / `temperature`).

---

## 2. The "shape" of the conversation: nested content blocks vs a flat item list

This is the most structural difference between the two protocols, and the master answer to the earlier chapters' "why does `messages` look like this."

### Anthropic: one message, holding a string of "content blocks"

CC's history is `messages[]`, each message `{role, content}`, where `content` is a **block array**. Blocks come in several `type`s: `text`, `tool_use`, `tool_result`, `thinking`, `image`. The key point — **tool calls and tool results are "blocks," nested inside messages**:

```jsonc
// 助手这一轮：思考块 + 工具调用块，并列在 assistant 消息的 content 里
{ "role": "assistant", "content": [
    { "type": "thinking", "thinking": "用户想看文件…应调用 shell。",
      "signature": "EqoB…(密码学签名)" },
    { "type": "tool_use", "id": "toolu_abc", "name": "shell",
      "input": { "command": "ls -la" } }      // ← input 是真正的 JSON 对象
]}

// 工具结果：作为一个 tool_result 块，塞进一条新的 user 消息，用 tool_use_id 回指
{ "role": "user", "content": [
    { "type": "tool_result", "tool_use_id": "toolu_abc",
      "content": "file1\nfile2", "is_error": false }
]}
```

Two takeaways, both production-grade pitfalls:

1. **`tool_use.input` is a JSON object** — you use it directly, no need to parse again.
2. **A tool result is "a block inside a user message,"** paired with that call via `tool_use_id`; and it carries the `user` role (not some standalone "tool" role).

### OpenAI: a flat string of items, with tool calls/results each standalone

Codex's history is `input[]`, a string of **flat, side-by-side** entries. In the source this is one big enum `ResponseItem` ([`protocol/src/models.rs`](../../codex/codex-rs/protocol/src/models.rs), around line 753, `#[serde(tag = "type", rename_all = "snake_case")]`), with many variants; the common ones:

| Variant | Wire `type` | Key fields |
|---|---|---|
| `Message` | `message` | `role`, `content` |
| `FunctionCall` | `function_call` | `name`, `arguments` (**JSON string**), `call_id` |
| `FunctionCallOutput` | `function_call_output` | `call_id`, `output` |
| `Reasoning` | `reasoning` | `summary`, `encrypted_content` |
| `CustomToolCall` | `custom_tool_call` | `call_id`, `name`, `input` (freeform text) |
| `LocalShellCall` / `WebSearchCall` / `ImageGenerationCall` | same-name snake_case | their respective action fields |
| `Compaction` / `ContextCompaction` | `compaction` / `context_compaction` | `encrypted_content` (a compaction summary is also a first-class item) |

The same round trip looks like this:

```jsonc
// 模型这一轮的 output：推理 item 和工具调用 item，平级并排
{ "type": "reasoning", "id": "rs_…",
  "summary": [ { "type": "summary_text", "text": "用户想看文件，应调用 shell。" } ],
  "encrypted_content": "gAAAAAB…(不透明密文)" }
{ "type": "function_call", "call_id": "call_abc", "name": "shell",
  "arguments": "{\"command\": \"ls -la\"}" }     // ← arguments 是字符串，要再 json.loads

// 工具结果：一个顶层 item，用 call_id 回指（不嵌在任何消息里）
{ "type": "function_call_output", "call_id": "call_abc",
  "output": "file1\nfile2" }
```

### The one-sentence difference, and its chain reaction

| | Anthropic Messages | OpenAI Responses |
|---|---|---|
| History structure | message → **content-block array** (two-dimensional) | **flat item list** (one-dimensional) |
| Tool-call location | `tool_use` block, nested inside the assistant message | `function_call`, standalone item |
| Tool-result location | `tool_result` block, nested inside a **new user message** | `function_call_output`, standalone item |
| Pairing key | `tool_use_id` | `call_id` |
| Tool-arg type | **JSON object** (use directly) | **JSON string** (parse once more) |

This shape difference is not aesthetic; it has chain reactions:

- **Streaming interleaving**: flat items let Responses treat "reasoning → tool call → another tool call" as a string of standalone entries, pushing each one as it streams, so the frontend captures them the moment they appear (see §6). Nested blocks stream "inside a single message" (`content_block` index).
- **Compaction**: the nested structure lets CC operate at two granularities, "message" and "block" (trimming whole messages, but also separately replacing the content of some `tool_result` block); the flat structure makes Codex more about "trimming the item sequence from the front." This thread is unfolded in [Full breakdown of context](context-cc-vs-codex.en.md) §3–§5.
- **Parsing trap**: the fact that `arguments` is a string is the most common bug source when integrating Responses — forget `json.loads` and you feed an entire `"{\"command\":…}"` as the command.

---

## 3. The encoding of tools: `input_schema` vs `parameters`, and one thing Anthropic doesn't have — grammar tools

Tools are the agent's hands. Both sides describe tool inputs with **JSON Schema**, but the **wrapping** differs, and Responses has an extra category of tool with no counterpart in the Anthropic system.

### Same: both rely on JSON Schema

```jsonc
// Anthropic：工具是顶层对象，schema 放在 input_schema
{ "name": "shell", "description": "run a command",
  "input_schema": { "type":"object",
    "properties": { "command": {"type":"string"} }, "required": ["command"] } }

// OpenAI Responses：扁平 type:"function"，schema 放在 parameters，多一个 strict
{ "type":"function", "name":"shell", "description":"run a command", "strict": false,
  "parameters": { "type":"object",
    "properties": { "command": {"type":"string"} }, "required": ["command"] } }
```

(Note that Responses tools are **flat** — `type:"function"` and `name` are at the same level; whereas the **old Chat Completions** nests them one more level into `"function": {…}`. That's also a layer of wrapping Codex shed by choosing Responses.)

On the Codex side, the tool type is an enum `ToolSpec` ([`tools/src/tool_spec.rs`](../../codex/codex-rs/tools/src/tool_spec.rs), around lines 15–51), `#[serde(tag="type")]`, with variants: `function` / `namespace` / `tool_search` / `image_generation` / `web_search` / `custom`. The function-tool body `ResponsesApiTool` ([`tools/src/responses_api.rs`](../../codex/codex-rs/tools/src/responses_api.rs), around lines 25–38) carries `name / description / strict / defer_loading / parameters / output_schema`; of these, `parameters` is a restricted `JsonSchema` ([`tools/src/json_schema.rs`](../../codex/codex-rs/tools/src/json_schema.rs), around lines 38–68) that supports only the OpenAI Structured Outputs subset (`type/enum/anyOf/$ref/$defs/properties/required/additionalProperties/items` + a Responses-specific `encrypted` marker).

### Difference one: `strict` mode

Responses function tools have a `strict: bool`. When on, OpenAI guarantees the `arguments` the model emits **strictly conform** to the schema (Structured Outputs: every field required, no extra fields). This pushes "preventing the LLM from filling in garbage params" down into a **server-side constraint**. Interestingly — Codex currently defaults to `strict: false` ([`tools/src/responses_api.rs`](../../codex/codex-rs/tools/src/responses_api.rs) around line 131); it chooses to catch the LLM's mistakes on the **client side** with `parse_arguments` + error feedback (`RespondToModel`) (exactly the dispatch layer covered in [s02](../s02_tool_use/README.en.md)'s "production-grade" section). Anthropic has no `strict` knob here — it relies on the model's own high adherence to `input_schema` + client-side validation.

> Trade-off: `strict` hands "guaranteeing validity" to the server (carefree, but the schema is restricted, and every schema change requires the server to recompile the constraint); client-side validation is more flexible and can give the model friendlier correction messages, but you write that line of defense yourself. Codex keeps both roads and defaults to the latter.

### Difference two: `defer_loading` / `tool_search` — load on demand when there are too many tools

That `defer_loading: Option<bool>` on `ResponsesApiTool`, plus the `ToolSpec::ToolSearch` variant, is Codex's mechanism for handling "too many tools/MCP, blowing out the context": tools beyond a threshold are **not expanded** at first, marked for deferred loading, and the model pulls them out via `tool_search` when needed. This is the same problem as CC's **ToolSearch meta-tool**, solved two ways — [s02](../s02_tool_use/README.en.md)'s "Deep dive five" compares them specifically (CC: a meta-tool the model invokes; Codex: protocol-level `defer_loading` + `ToolSearchCall`/`Output` items).

### Difference three (the most interesting): **a grammar-constrained freeform tool** — `apply_patch`

This is a move with **no direct counterpart** in the Anthropic system. Codex's `apply_patch` is neither a function tool nor a shell, but a `custom` (freeform) tool, whose input is not JSON but a stretch of **free text constrained by a LARK grammar** ([`core/src/tools/handlers/apply_patch_spec.rs`](../../codex/codex-rs/core/src/tools/handlers/apply_patch_spec.rs), around lines 9–27):

```jsonc
{ "type": "custom", "name": "apply_patch",
  "description": "Use the `apply_patch` tool to edit files. This is a FREEFORM tool, so do not wrap the patch in JSON.",
  "format": { "type": "grammar", "syntax": "lark",
              "definition": "start: begin_patch hunk+ end_patch …" } }
```

What the model returns is not `{"old":…,"new":…}` but directly emitted patch text, and **held in place by the grammar** so it can't drift:

```text
*** Begin Patch
*** Update File: a.py
@@
-old line
+new line
*** End Patch
```

Contrast Anthropic here — it too has "built-in tool types" for editing files (such as text editor / `str_replace_based_edit_tool`, bash, computer use), but **the input is always a JSON command**:

```jsonc
{ "type":"tool_use", "name":"str_replace_based_edit_tool",
  "input": { "command":"str_replace", "path":"a.py", "old_str":"…", "new_str":"…" } }
```

**Why does Codex go with grammar-constrained free text?** Because for its model, on the task of "writing a diff," emitting **a whole patch of text** is more natural and less error-prone than emitting **structured JSON fields** — so the protocol offers the `grammar` option, letting "the tool's shape conform to the model's most comfortable output style." This is the sharpest expression of "**the tool shape follows the model**" that [s03](../s03_apply_patch/README.en.md) repeatedly stresses: Anthropic chooses "structured commands + the model strictly fills JSON," OpenAI chooses "free text + grammar as backstop," and behind it lie the two companies' different judgments about "how their own model most reliably outputs a patch."

---

## 4. Reasoning / thinking: a signed plaintext block vs an encrypted opaque item

Both companies' flagship models "think before they act," but **how they put that thinking into the protocol** diverges at the philosophical level. This is the expanded version of [s01](../s01_agent_loop/README.en.md)'s "Deep dive four."

### Anthropic: thinking is a **cryptographically signed plaintext block** inside the message

- **Switch and budget**: `thinking: { "type": "enabled", "budget_tokens": 4000 }` — you give a **token budget number**, telling the model "think at most this much."
- **The returned shape**: a `thinking` block, containing **plaintext** thinking + a `signature` (cryptographic signature). You can **read** what the model thought.
- **What the signature is for**: it guarantees integrity. On the next turn you carry the `thinking` block back **verbatim** (especially necessary for interleaved thinking — the model needs to see its own train of thought from the previous turn), and the server uses the `signature` to verify this thinking wasn't tampered with.
- **When filtered**: if the thinking content triggers a safety filter, it becomes `redacted_thinking` (an encrypted block); you still carry it back verbatim but can't read it.

### OpenAI (Codex): reasoning is an **encrypted, opaque standalone item** + an "effort level" knob

- **Switch and level**: `reasoning: { effort, summary, context }` ([`codex-api/src/common.rs`](../../codex/codex-rs/codex-api/src/common.rs) around lines 125–132). `effort` is not a token count but an **enum level**: `none / minimal / low / medium (default) / high / xhigh` ([`protocol/src/openai_models.rs`](../../codex/codex-rs/protocol/src/openai_models.rs) around line 40) — "how deep to think" is turning a knob, not giving a number.
- **The returned shape**: a `reasoning` item, usually containing a **summary** (`summary`, the human-facing version, optional `auto/concise/detailed/none`, [`protocol/src/config_types.rs`](../../codex/codex-rs/protocol/src/config_types.rs) around line 47) + a stretch of **`encrypted_content` (opaque ciphertext)**. The raw chain of thought you **cannot read**.
- **How it's carried across turns**: add `include: ["reasoning.encrypted_content"]` to the request ([`core/src/client.rs`](../../codex/codex-rs/core/src/client.rs) around lines 768–769), and the server returns the encrypted reasoning; on the next turn you stuff these `reasoning` items back into `input` verbatim, and the model "remembers where it got to last time" — but throughout you're **just a courier who can't read the contents**.

### Side by side, the difference is along three dimensions

| | Anthropic Messages | OpenAI Responses |
|---|---|---|
| Intensity control | `budget_tokens` (**number budget**) | `effort` level (**enum knob**) |
| Readability | **plaintext**, you can read | **ciphertext**, you can't read (only a summary) |
| Integrity vs confidentiality | **signature** ensures integrity (anti-tamper) | **encryption** ensures confidentiality (hides the chain of thought) |
| Position in the protocol | a **block** in the message (alongside text/tools) | a **standalone item** (alongside messages/tools) |
| Carrying across turns | return the `thinking` block verbatim | `include` pulls it back + return the `reasoning` item verbatim |
| Consumes context budget | yes | yes (and must be counted in compaction, [s07](../s07_context_compaction/README.en.md)) |

**Why different?** The two companies give opposite answers to "should you be allowed to see the model's chain of thought": Anthropic leans toward **transparency + verifiability** (plaintext + signature), OpenAI leans toward **protection + opacity** (encryption + summary only). This isn't just product taste; it also touches privacy, debuggability, and the security concern of "could the chain of thought be reverse-engineered." But one thing is fully shared: **both companies have promoted "what was thought" from a log side-note to first-class data in the protocol that must be transmitted, stored, and replayed** — this is the shared evolution of wire protocols in "the reasoning-model era."

---

## 5. State: stateless re-send vs a "can-be" stateful server-side session

This section will **make precise** a common simplification. You may have read in [Full breakdown of context](context-cc-vs-codex.en.md) that "Messages is stateless, Responses is server-side stateful" — the general direction is right, but the truth is finer.

### Anthropic Messages: **structurally stateless**

The Messages API **keeps no conversation state on the server**. There's no such thing as `previous_response_id`, no "resume by session id." Every turn, the client **re-sends the complete `messages[]`**. State is 100% on the client — this is the fundamental reason CC builds context management, compaction, and memory into a fine-grained **client-side** pipeline (it has no other choice).

### OpenAI Responses: **stateful is a "capability," but Codex doesn't use it by default**

Responses **supports** server-side state: with `store: true`, the server remembers this response, and next turn you send only `previous_response_id` + the **incremental** items, sparing you from re-sending the whole history. But —

- **Codex defaults to `store: false` for the OpenAI endpoint** ([`core/src/client.rs`](../../codex/codex-rs/core/src/client.rs) around line 799: `store: provider.is_azure_responses_endpoint()` — only the **Azure** endpoint gets `store:true`). That is, **facing OpenAI, Codex likewise re-sends the complete `input` every turn**, stateless like CC.
- The `previous_response_id` field **exists only in the WebSocket request struct** `ResponseCreateWsRequest` ([`codex-api/src/common.rs`](../../codex/codex-rs/codex-api/src/common.rs) around lines 228–253), and is filled only when `store:true` + there's a previous response, sending only the delta. The HTTP path doesn't carry it at all.

So the more precise picture is:

| | Anthropic Messages | OpenAI Responses (Codex's actual usage) |
|---|---|---|
| Does the protocol **support** server-side state | no | **yes** (`store`+`previous_response_id`) |
| How Codex/CC use it **by default** | stateless, re-send everything | **also** stateless re-send everything (OpenAI endpoint `store:false`); only Azure/WS go incremental |
| The "extra luggage" carried across turns | full messages | full input **+ encrypted reasoning** (§4) |
| Can the server compact for you | basically no | **yes** (Codex has `compact_remote` remote compaction, relying exactly on Responses' server-side capability) |

In other words: **Responses makes "server-side stateful" a capability you can enable at any time; Codex normally leaves it off (staying stateless re-send), but can reach for it when needed (Azure incremental, remote compaction, sticky routing)**. This "optional server-side capability" is exactly the foundation that lets Codex do rollout resume, remote compaction, and `x-codex-turn-state` sticky routing ([Full breakdown of context](context-cc-vs-codex.en.md) §5, §7). Messages closes this road completely, forcing CC to do everything to the extreme on the client.

### Caching: you mark breakpoints vs give a key + automatic prefix

"Stateless re-send" most fears waste — the long opening chunk re-sent every turn (system + tools + early history) barely changes; why pay for it anew each time? Both companies use **caching** to save this money, but the control method is opposite:

- **Anthropic: you mark breakpoints explicitly.** Hang `cache_control: {type:"ephemeral"}` on `system` / `tools` / some content block, up to 4 breakpoints, telling the server "cache the prefix up to here." On a hit, `usage` has `cache_read_input_tokens` (cheap) and `cache_creation_input_tokens` (first write). TTL defaults to 5 minutes, with a 1-hour extended tier. **Control is in your hands, billed by breakpoint.**
- **OpenAI Responses: give a key, the prefix is cached automatically.** Codex passes `prompt_cache_key` (a session id), and the server **automatically** does cache hits on identical prefixes; combined with `x-codex-turn-state` sticky routing, it tries to land the same session on the same backend to hit its cache ([s09](../s09_responses_api/README.en.md) Deep dive three). `response.completed`'s `token_usage` has a `cached_input` item. **Control is more on the server side, automatic by prefix.**

This also echoes the compaction philosophy ([Full breakdown of context](context-cc-vs-codex.en.md) §5): when Codex compacts, it **deliberately trims from the middle of the history and keeps the prefix**, precisely to avoid shattering this automatic prefix cache.

---

## 6. Streaming: block-oriented events vs item / semantic-oriented events

Both companies use **SSE (Server-Sent Events)** to push the response over **chunk by chunk**, but the **organizing unit** of events differs.

### Anthropic: an event stream organized around "content blocks"

The event sequence of one response (block-oriented, each block carries an `index`):

```text
message_start                                   ← 消息开始（含初始 usage）
  content_block_start   (index 0, thinking)     ← 第 0 块：思考块开张
    content_block_delta (thinking_delta …)      ← 思考文字一段段来
    content_block_delta (signature_delta …)     ← 最后补上签名
  content_block_stop    (index 0)
  content_block_start   (index 1, tool_use)     ← 第 1 块：工具调用壳
    content_block_delta (input_json_delta …)    ← 工具入参的 JSON 一片片来（要累积再解析）
  content_block_stop    (index 1)
message_delta  (stop_reason, usage)             ← 收尾：带 stop_reason + 最终 usage
message_stop
```

Sub-event `delta`s come in several kinds: `text_delta` (body), `input_json_delta` (partial JSON of tool input), `thinking_delta` (thinking), `signature_delta` (signature). "Fine-grained tool streaming" makes `input_json_delta` spill out directly without buffering.

### OpenAI Responses: an event stream organized around "item / semantic events"

The events Codex actually matches ([`codex-api/src/sse/responses.rs`](../../codex/codex-rs/codex-api/src/sse/responses.rs)'s `process_responses_event` around line 276):

```text
response.created                          ← 响应开始
response.output_item.added                ← 新增一个 item（如一个 function_call 的壳）
  response.output_text.delta              ← 助手正文一段段来
  response.function_call_arguments.delta  ← 函数入参一段段来（公开 API 名）
  response.custom_tool_call_input.delta   ← 自由文本工具的输入一段段来（apply_patch 走这条）
  response.reasoning_summary_text.delta   ← 推理摘要一段段来
  response.reasoning_text.delta           ← 推理正文一段段来
response.output_item.done                 ← 某个 item 收尾
response.completed                        ← 整轮完成：带 token_usage + end_turn
response.failed / response.incomplete     ← 失败 / 不完整（见 §7）
```

On `response.completed`, Codex reads out `token_usage` (`input / output / cached_input / reasoning_output / total`) and an `end_turn` flag ([`core/src/client.rs`](../../codex/codex-rs/core/src/client.rs) around line 1851).

### Difference

| | Anthropic | OpenAI Responses |
|---|---|---|
| Event unit | **content block** (`content_block_*`, with index) | **item / semantic event** (`response.*`) |
| Tool-arg streaming | `input_json_delta` (accumulate JSON) | `function_call_arguments.delta` / freeform-tool `custom_tool_call_input.delta` |
| Thinking/reasoning streaming | `thinking_delta` + `signature_delta` | `reasoning_summary_text.delta` / `reasoning_text.delta` |
| Closing signal | `message_delta` with `stop_reason`+`usage` | `response.completed` with `token_usage`+`end_turn` |
| Failure signal | HTTP status code + `error` event | **in-stream** `response.failed` event (see §7) |

In one sentence: Anthropic's stream is "**inside one message, block after block**"; Responses' stream is "**a string of top-level items / semantic nodes**." This is isomorphic to §2's history shape — streaming just **splits that structure apart by time and pushes it**.

---

## 7. End and failure signals: `stop_reason` vs `response.status` / events

### How to know "this turn should stop"

- **Anthropic: look at `stop_reason`.** Values: `end_turn` (done speaking), `tool_use` (wants to call a tool, loop continues), `max_tokens` (hit the output cap and got truncated), `stop_sequence` (hit a stop word), `pause_turn` (a long-running server-side tool, needs another send to keep going), `refusal` (refused to answer).
  - ⚠️ Secondhand detail: the learn-claude-code analysis says **CC actually doesn't rely on `stop_reason` to decide whether to continue the loop, but checks whether there's a `tool_use` block in the content** — because in a streaming response `stop_reason` may be unreliable. Treat this as secondhand.
- **OpenAI Responses: look at the response status / events.** `response.completed` (with the `end_turn` flag and `token_usage`) / `response.incomplete` (e.g. hit the length) / `response.failed` (errored). Codex decides whether to send another turn based on `end_turn` and whether there's a pending `function_call` to execute ([s01](../s01_agent_loop/README.en.md)'s loop + [s10](../s10_sq_eq_protocol/README.en.md)'s SQ/EQ).

### What failure looks like — a design divide worth savoring

- **Anthropic**: failures mostly go through **HTTP status codes** (400 invalid_request, 413 request too large, 429 rate_limit, 500 api_error, 529 overloaded_error), with the occasional in-stream `error` event.
- **OpenAI Responses**: many failures are **in-stream `response.failed` events**, carrying fine-grained error codes ([`codex-api/src/sse/responses.rs`](../../codex/codex-rs/codex-api/src/sse/responses.rs) around line 325): `context_length_exceeded → ContextWindowExceeded`, `insufficient_quota → QuotaExceeded`, `server_is_overloaded`/`slow_down → ServerOverloaded`, `invalid_prompt`, `cyber_policy`, and a `Retryable` carrying `Retry-After`.

> What the difference means: Anthropic keeps failure more at the "HTTP layer" (connection level), while Responses also makes failure a "**first-class in-stream event**" — because one of its turns may already have streamed many items (reasoning, tools) before failing, and reporting errors via in-stream events can carry the context of "what was already produced before the failure."

### The token-usage fields also differ

| | Anthropic `usage` | OpenAI `token_usage` |
|---|---|---|
| Input | `input_tokens` | `input` |
| Output | `output_tokens` | `output` |
| Cache hit | `cache_read_input_tokens` | `cached_input` |
| Cache write | `cache_creation_input_tokens` | — (prefix automatic) |
| **Reasoning-specific** | — (included in output) | **`reasoning_output`** (listed separately) |
| Total | (add it up yourself) | `total` |

OpenAI **lists `reasoning_output` separately** — the characteristic bill of a "reasoning model": you can see clearly how many tokens this turn spent on "thinking." This also ties back to §4: reasoning is a first-class cost that must be metered and must enter the compaction budget.

---

## 8. Two "absent" fields: what `max_tokens` and `temperature` tell us

What best embodies "the protocol follows the model" is often the **absent** field. Compare §1's two forms:

| | Anthropic Messages | OpenAI Responses (Codex request) |
|---|---|---|
| `max_tokens` | **required** (hard output cap) | **doesn't carry this field** |
| `temperature` / `top_p` / `top_k` | optional (tune randomness) | **not in the request at all** (`ResponsesApiRequest` has no such field) |
| "how deep to think" knob | none (relies on `budget_tokens` to limit thinking length) | `reasoning.effort` level |

I've cross-checked all the fields of `ResponsesApiRequest` ([`codex-api/src/common.rs`](../../codex/codex-rs/codex-api/src/common.rs) around lines 183–203): **no `max_tokens`, and no `temperature`**. This isn't an omission but a direct projection of the model's form:

- **A reasoning model manages its own output length** — how long it thinks and how much it writes is tuned indirectly by the high-level `effort` knob, rather than truncated by a blunt token cap.
- **A reasoning model doesn't expose `temperature`** — sampling temperature means something different for a model that "does a long chain of reasoning first, then answers," so OpenAI simply doesn't accept it in the Responses request.

Conversely, Anthropic makes `max_tokens` **required** and keeps `temperature` because Claude's interface is closer to the "classic completion" control surface: you explicitly give the output budget and the randomness. One hands control to "the level + model autonomy," the other to "explicit numeric parameters" — the same thing (controlling generation), two worldviews.

---

## 9. Error model and retries (brief table, details in s09)

| | Anthropic Messages | OpenAI Responses (Codex) |
|---|---|---|
| Failure carrier | HTTP status code + occasional `error` event | **in-stream `response.failed` event** + status code |
| Rate limiting | 429 `rate_limit_error` | `server_is_overloaded`/`slow_down`, with `Retry-After` |
| Overload | 529 `overloaded_error` | `ServerOverloaded` |
| Context overflow | 400/413 (prompt too long) → reactive compaction | `context_length_exceeded` → reactive compaction |
| Retry policy (secondhand/source) | ⚠️ CC: backoff+jitter, at most 10 times, 3 consecutive 529s switch to a backup model | source: [`responses_retry.rs`](../../codex/codex-rs/core/src/responses_retry.rs) backoff+jitter, honor `Retry-After`, WebSocket→HTTPS **transport fallback**, sticky routing guarantees safe retries |

The shared iron law ([s09](../s09_responses_api/README.en.md)'s "production-grade" section spells it out): **retryable errors** use exponential backoff + jitter (to avoid a retry storm), obey the server's `Retry-After`, and cap the retry count; **fatal errors** (auth) get zero retries; **context overflow** is "compact first, then retry" rather than a plain re-send — "recovery ≠ retry."

---

## 10. Overall comparison table

| Dimension | Claude Code (Anthropic Messages) | Codex (OpenAI Responses) |
|---|---|---|
| Endpoint | `POST /v1/messages` | `POST /v1/responses` |
| System prompt | `system` (can be a block array, can be cached) | `instructions` (string) |
| Conversation history | `messages[]`, **content blocks** nested | `input[]`, **flat items** |
| Tool call/result | `tool_use`/`tool_result` **blocks** (nested in messages) | `function_call`/`function_call_output` **standalone items** |
| Pairing key | `tool_use_id` | `call_id` |
| Tool-arg type | **JSON object** | **JSON string** (parse again) |
| Tool schema | `input_schema` | `parameters` + `strict` flag |
| Grammar/freeform tool | none (the edit tool is also a JSON command) | **yes** (`custom`+LARK, `apply_patch`) |
| On-demand loading of many tools | ToolSearch meta-tool | `defer_loading` + `tool_search` item |
| Reasoning intensity | `budget_tokens` (number) | `reasoning.effort` (level enum) |
| Reasoning readability | **plaintext + signature** (readable, anti-tamper) | **encrypted** (unreadable, summary only) |
| Reasoning in the protocol | a `thinking` block in the message | a standalone `reasoning` item (`include` pulls it back) |
| Server-side state | **none** (structurally stateless) | **can have** (`store`+`previous_response_id`); Codex defaults to `store:false` for OpenAI |
| Cache control | `cache_control` explicit breakpoints (≤4) | `prompt_cache_key` + automatic prefix + sticky routing |
| Output cap | `max_tokens` **required** | **not carried** (model autonomy) |
| Sampling temperature | `temperature` etc. optional | **not in the request** |
| Streaming unit | content-block events (`content_block_*`) | item/semantic events (`response.*`) |
| Failure signal | HTTP status + `error` event | **in-stream `response.failed`** + status |
| Token bill | `usage{input,output,cache_*}` | `token_usage{input,output,cached_input,reasoning_output,total}` |
| Chat Completions fallback | not applicable | **removed** (`WireApi` is `Responses` only) |

---

## 11. Why different? Settling the account once and for all

Collapsing all the differences above into one sentence:

> **The two forms carry the same things (system prompt, conversation, tools, reasoning, usage), but the shape of every cell is molded by two things: "how the company's own model works" and "where state should live."**

Three layers of root cause:

1. **The model molds the protocol (the deepest layer).** OpenAI's reasoning model → `reasoning.effort` levels, encrypted `encrypted_content`, no `temperature`/`max_tokens`, a LARK grammar letting the model freely emit patches. Claude → a `thinking` plaintext block + cryptographic signature, required `max_tokens`, strictly-JSON tool inputs. **It's not that one copies the other; it's that each model is shaped a certain way, so the form is shaped that way.** This is the "tools and protocol follow the model" that [s03](../s03_apply_patch/README.en.md)/[s09](../s09_responses_api/README.en.md) keep stressing.

2. **Where state lives determines the capability boundary.** Messages is structurally stateless → CC can only — and therefore does — build context/compaction/memory into an **extremely fine-grained client-side pipeline**. Responses makes "server-side state" an **optional capability** → Codex normally re-sends statelessly (as carefree as CC), but when needed can unlock remote compaction, incremental requests, sticky routing, rollout resume. One closes the road and forces the client's extreme; the other leaves the door and gains cloud/recoverable elasticity ([Full breakdown of context](context-cc-vs-codex.en.md), [Full breakdown of subagents](subagent-multiagent-cc-vs-codex.en.md) describe the world behind this door).

3. **The caching philosophy is a byproduct of stateless re-send.** Both companies want to save money on "re-sending every turn": Anthropic lets you **mark breakpoints explicitly** (control on you), OpenAI gives you a **key + automatic prefix** (control on the server). So Codex must "keep the prefix" when compacting, and CC must "manage its own breakpoints" when compacting.

Neither is more brilliant — these are two self-consistent engineering answers for different models and different deployment forms. **Once you understand "how the account is settled," you can integrate any company's API in your own harness and know why each field is where it is, what happens if it's missing, and where to put state and caching.** That's the step from "can call an API" to "understand the wire protocol."

---

## 12. Think it over

<div class="think">

1. Anthropic lets you **read** the thinking (plaintext + signature), OpenAI lets you **not read** it (encrypted + summary). From the angle of "debugging a misbehaving agent," which is more useful? From the angle of "protecting the model's chain of thought from being reverse-engineered"? If you were designing it, how would you weigh the trade-off?
2. `apply_patch` constrains free text with a LARK grammar instead of JSON fields. A grammar constraint can guarantee "the patch format is valid," but can it guarantee "the patch logic is correct"? Is this the same "valid ≠ correct" limitation as §3's `strict` mode (which guarantees JSON is valid)?
3. Responses makes failure an **in-stream event** (`response.failed`), while Messages relies more on the **HTTP status code**. When a turn has already streamed 5 tool calls before failing, which of these error-reporting styles lets the harness recover better? Why?
4. Codex defaults to `store:false` for OpenAI (stateless re-send), declining to use server-side state even though Responses supports it. What's in it for Codex? (Hint: think about ZDR/data residency, portability, and the auditability of "the server changed your conversation behind your back without you seeing it" — back to [Full breakdown of context](context-cc-vs-codex.en.md) §11.)
5. Neither company copied the other's move (Anthropic didn't add an `effort` level, OpenAI didn't accept `temperature` in the request). If one day a company's model changed form (say Claude also went pure reasoning, or the codex line also opened up plaintext chain of thought), which cell of its **form** do you expect to change first?

</div>

---

[← Back to the learn-codex overview](../README.en.md) · Related chapters: [s01 Agent Loop](../s01_agent_loop/README.en.md) (reasoning vs thinking) · [s02 Tools](../s02_tool_use/README.en.md) (schema/ToolSearch) · [s03 apply_patch](../s03_apply_patch/README.en.md) (the tool shape follows the model) · [s09 Responses API](../s09_responses_api/README.en.md) · [s10 SQ/EQ](../s10_sq_eq_protocol/README.en.md) · Sister pieces: [Full breakdown of context](context-cc-vs-codex.en.md) · [Full breakdown of subagents and multi-agent](subagent-multiagent-cc-vs-codex.en.md)
