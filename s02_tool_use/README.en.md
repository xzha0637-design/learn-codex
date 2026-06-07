# s02: Tool Use — Add a Tool Without Changing a Single Line of the Loop

> 🌐 **English** · [中文版](README.md)

> *"Adding a tool isn't about changing the loop — it's registering one handler + one schema."*

[learn-codex overview](../README.en.md) · [agent loop](../s01_agent_loop/README.en.md) → **Tools and dispatch** → [apply_patch](../s03_apply_patch/README.en.md)

---

## Get the idea straight first: why adding a tool doesn't move the loop by even a line

s01 nailed down one thing: the kernel of an agent is a loop where **the model has the ideas and the loop runs the errands**. So the very next question is natural — an agent needs to know how to do more and more things (read files, write files, list directories, edit code, search the web…), and where do these "things it can do" get added? Does the loop have to change every time you add one, growing more and more bloated?

The answer is surprising: **the loop never moves.** Understanding this rests on the following two escalating points.

**Point one: the model doesn't "own" any capability — all it can do is "call out a name."**
Back to the picture from s01: the model is like an advisor who can only write notes. It writes down "I want to read the file `config.py`" — but it can't read it; it has merely **voiced an intent**. The thing that can actually go read the file is a little piece of Python on our side. In other words, the model's side only ever does one thing: **state "which tool I want to use, with what arguments"**; whether the tool can actually be used and how it's implemented is entirely in our hands. The model calls out the name; we do the work.

**Point two: since the model only "calls out a name," whoever "claims the name" needs nothing more than a single table.**
The model calls out `read_file` — who picks it up? The dumbest and also most correct approach is a **phone book**: name → the corresponding person (function). The model calls out `read_file`, the loop flips to the `read_file` page, finds the function `run_read_file`, and dials it with the arguments given. Call out `write_file` and it flips to the `write_file` page. This phone book is the **dispatch map** (a `name → handler` dictionary).

Here comes the key insight: **all the loop ever does, from start to finish, is the single act of "look up the phone book, dial accordingly" — it doesn't care in the slightest whether the book has 1 name or 100 names.** Adding a new capability amounts to **copying one more line** into the phone book (plus telling the model about this new name, so it knows there's such a person to call). That "look up the book → dial" line in the loop never gets touched. This is why the dozen-or-so lines of `run_turn` from s01 won't change by a single word in this chapter.

An analogy: the loop is like a company receptionist who only knows how to "transfer the call to whatever department the visitor named." Today the company opens a new "Legal department" — does the receptionist have to change how they work? No — just add a line to the transfer table: "Legal → room 305." The receptionist's action is always the same one. **The model is the visitor, the tools are the various departments, and the loop is that receptionist who only ever knows how to transfer.** All the dozens of capabilities across this entire course are added one line at a time onto this transfer table; the receptionist (the loop) stays the same one from s01.

## Problem

The agent in s01 had only one tool: `shell`. It's already "Turing complete" — read a file with `cat`, write a file with `echo > file`, list a directory with `ls`; in theory shell can do it all.

But using shell for everything has two real problems:

1. **Ugly and brittle**. "Read the first 20 lines of this file" means cobbling together `sed -n '1,20p'`, which may not even be right across platforms; writing a multi-line file means wrestling with here-docs, escaping, and quotes.
2. **Not observable**. The frontend (TUI / IDE) gets back just a blob of `stdout`; it can't tell whether the model is "reading a file" or "wiping the database" this time around — there's no structured semantics.

So even with shell as the workhorse, we still want to give the model a few **first-class tools**: `read_file`, `write_file`, `list_dir`. The question arises — **for each tool we add, does the agent loop have to change along with it?**

## Solution

No. The entire point of this chapter is one sentence:

> **The loop doesn't move. Adding a tool = adding one handler line to the "dispatch map" + adding one schema to the tool list.**

The loop in s01 already had the line `HANDLERS.get(tc.name)` — it was dispatching by name via table lookup all along. All we need to do is pad out that table.

```
   模型回合产出 tool_call(name, arguments)
                  │
                  ▼
        TOOL_HANDLERS.get(name)   ← 唯一的"扩展点"：一张 name→handler 的字典
          ┌───────┼───────┬────────────┐
          ▼       ▼       ▼            ▼
        shell  read_file write_file  list_dir   ← 加工具就在这里加一行
          │       │       │            │
          └───────┴───────┴────────────┘
                  │
          function_call_output 回灌 → 继续循环（run_turn 一字未改）
```

## How it works

Look at [code.py](code.py). Under the `# FROM s01（搬运）` banner is the **untouched** `run_shell` and `run_turn`; only under the `# NEW in s02` banner is the new stuff.

**Step 1** — Write the implementations of the new tools. Each wraps a layer of `safe_path` to anchor the path inside the workspace:

```python
def safe_path(p: str) -> Path:
    path = (WORKDIR / p).resolve()
    if not path.is_relative_to(WORKDIR):
        raise ValueError(f"path escapes workspace: {p}")
    return path
```

**Step 2** — Register the tools into the **dispatch map** and the **tool list** (the schema is the flat Responses API shape):

```python
TOOL_HANDLERS = {"shell": run_shell, "read_file": run_read_file,
                 "write_file": run_write_file, "list_dir": run_list_dir}
TOOLS = [{"name": "read_file", "description": "...",
          "parameters": {"type": "object", "properties": {...}, "required": ["path"]}}, ...]
```

**Step 3** — The loop is unchanged. The line in `run_turn` that does table-lookup dispatch is character-for-character identical to s01:

```python
handler = TOOL_HANDLERS.get(tc.name)
output = handler(**tc.arguments) if handler else f"unknown tool: {tc.name}"
messages.append(tool_output_item(tc.call_id, output))
```

This is precisely the structure of the real Codex. In core there's a [`ToolRegistry`](../../codex/codex-rs/core/src/tools/registry.rs) (`HashMap<ToolName, Arc<dyn CoreToolRuntime>>`), whose `dispatch_any` looks up by name, finds the corresponding handler, and calls `handle()` — directly descended from our `TOOL_HANDLERS.get(name)`, just wrapped on the outside with a layer of hooks / telemetry / sandbox labels. And [`create_tools_json_for_responses_api`](../../codex/codex-rs/tools/src/tool_spec.rs) (`tools/src/tool_spec.rs:78`) is responsible for serializing each tool into the flat JSON sent to the model — corresponding to our `create_tools_json` line.

**Walk through it** — Let's make good on the claim "adding a tool leaves the loop unchanged." Suppose the model wants to write a file this turn; the note it calls out looks like this (the **exact same shape** as that `shell` note from s01, only with `name` swapped and the fields inside `arguments` swapped):

```json
{"type": "function_call", "call_id": "call_7", "name": "write_file",
 "arguments": "{\"path\": \"hello.txt\", \"content\": \"hi\\n\"}"}
```

The loop receives it and still does the one act from s01 — look up the table, dial accordingly:

1. **Look up the phone book**: `TOOL_HANDLERS.get("write_file")` → fetches the `run_write_file` function. (In s01 this table had only the one `shell` entry; now it has four — but the line of code `.get(name)` hasn't changed by a single character.)
2. **Dial with the arguments**: `run_write_file(path="hello.txt", content="hi\n")`, which actually writes the file to the workspace and returns `"wrote 3 bytes to hello.txt"`.
3. **Paste the result back by `call_id`**, wrapped into a `function_call_output` item identical to s01's:
   ```json
   {"type": "function_call_output", "call_id": "call_7", "output": "wrote 3 bytes to hello.txt"}
   ```
4. Ask the model again with the now-longer history, and continue the loop.

Note steps 1–3: **these are exactly those three lines in s01's `run_turn`**, character-for-character identical (go to [code.py](code.py) and check the segment under the `# FROM s01（搬运）` banner). Everything new we added in this chapter — the implementation of `run_write_file`, the extra lines in `TOOL_HANDLERS`, the extra schemas in `TOOLS` — is **all outside the loop**. The loop merely has a few more names it can look up. This is the living template for "point two" above.

`--demo` pulls this out and shows it to you on its own: it doesn't touch the model, it directly calls `TOOL_HANDLERS[name](**kwargs)` — the **same act** as that "dial" in step 2 of `run_turn` — doing write→read→list under `_demo_workspace/`, then demonstrating once more an out-of-bounds write being blocked by `safe_path`, and finally cleaning up the directory. In the output you'll see the line `> write_file {'path': ...}`, which corresponds exactly to the note above.

## Production-grade: how the schema is encoded, how the harness holds the line on it, and what to do when the LLM errs

The loop and dispatch are wired up — but for a tool system that **can ship to production**, the hard part isn't "calling the tool correctly" at all; it's "what to do when the model calls it **wrong**." The model will leave out parameters, fill in wrong types, even call out a tool name that doesn't exist at all. A toy crashes on the spot; a production-grade harness must catch all of these. This section explains three things thoroughly enough to withstand scrutiny.

### 1. How the schema is encoded: it's not a comment, it's **the only guardrail + the prompt**

A tool's `parameters` is a piece of **JSON Schema**. Both of the things it does go far beyond "documentation":

- **For the model**: the field names and `description` are the **sole basis** on which the model decides "how to call it" — it is prompt engineering in itself. `{"type":"integer"}`, `{"enum":[...]}`, `required` narrow down what the model can fill in; write it vaguely and the model fills in garbage.
- **For the harness**: the schema is the ruler by which you **validate** the model's output (see section three).

The real Codex doesn't hand-write JSON strings to splice together a schema; instead it uses a **typed `JsonSchema` struct** ([`tools/src/json_schema.rs:38`](../../codex/codex-rs/tools/src/json_schema.rs)):

```rust
pub struct JsonSchema {
    pub schema_type: Option<JsonSchemaType>,       // "type"
    pub description: Option<String>,
    pub enum_values: Option<Vec<JsonValue>>,        // "enum"
    pub items: Option<Box<JsonSchema>>,             // 数组元素
    pub properties: Option<BTreeMap<String, JsonSchema>>,
    pub required: Option<Vec<String>>,
    pub additional_properties: Option<AdditionalProperties>,
    pub any_of: Option<Vec<JsonSchema>>,            // 联合类型 / 可空
    // ...
}
```

Each tool is a `ResponsesApiTool { name, description, strict: bool, parameters: JsonSchema }` ([`responses_api.rs:26`](../../codex/codex-rs/tools/src/responses_api.rs)), and is then serialized by `ToolSpec`, a `#[serde(tag="type")]` enum, into `{"type":"function", ...}` to be sent to the model. **The key: the schema is a type in code, not strings scattered around** — and this is exactly the bedrock for "no drift" below.

### 2. strict mode: make the model **literally unable to emit** illegal parameters

`ResponsesApiTool` has a `strict: bool`. Set it to `true` and you get OpenAI's **strict function calling**: the API uses **constrained decoding** to guarantee the parameters the model emits must conform to the schema (missing required fields and wrong types are blocked at the **generation stage** and never reach you at all). The cost is written in a source comment (`responses_api.rs:29`):

> *When strict=true, the JSON schema's `required` and `additional_properties` must be complete; **every** field in `properties` must appear in `required`.*

That is, strict doesn't allow "optional fields" (if you want optional, you can only write it as `anyOf:[T, null]`). These built-in Codex tools currently default to `strict: false`, keeping the flexibility and falling back on **runtime validation** instead (section three) — this is a real engineering trade-off: **strict = blocked at generation time, but the schema has to be locked down with no optional fields; not strict = the schema is flexible, but you have to validate it yourself in the harness.**

> There's another layer of **defensive normalization**: OpenAI models require the schema to carry `properties`, but some MCP servers just won't provide it. The moment Codex detects it's missing in [`mcp_tool.rs`](../../codex/codex-rs/tools/src/mcp_tool.rs), it **stuffs an empty `{}` in** — when connecting third-party tools, you can't assume the other side's schema is clean.

### 3. Guarding against LLM errors: it's not "don't let it err," it's "when it errs, it can be fed back and corrected"

This is the crux of the whole section, and the watershed between toy and production. Look at how the real Codex parses the arguments of a tool call ([`handlers/mod.rs:72`](../../codex/codex-rs/core/src/tools/handlers/mod.rs)):

```rust
fn parse_arguments<T: for<'de> Deserialize<'de>>(arguments: &str)
    -> Result<T, FunctionCallError> {
    serde_json::from_str(arguments).map_err(|err| {
        FunctionCallError::RespondToModel(format!("failed to parse function arguments: {err}"))
    })
}
```

Two production-grade takeaways:

1. **Deserialize into a typed struct `T`**: the schema is sent to the model, `T` is used to receive it — **the two share one source**, so the drift of "the schema says there's a field X but the code reads field Y" cannot happen. This is the answer to "how the harness maintains the schema": **don't let the schema and the parsing each have their own copy; let them come from the same type.**
2. **On failure don't panic, instead `RespondToModel(error message)`**: the error is fed back to the model as the **result** of this tool call, and the model, seeing "where the parameter went wrong" on the next round, can correct itself.

And errors come in only two kinds ([`function_call_error.rs`](../../codex/codex-rs/tools/src/function_call_error.rs)) — this dichotomy is the entire philosophy of production-grade error handling:

| Variant | Meaning | How it's handled |
|---|---|---|
| `RespondToModel(String)` | **Recoverable**: the model can fix it itself (wrong parameter, wrong tool name, command failed…) | Feed the error back as the tool result, the loop continues, the model retries |
| `Fatal(String)` | **Unrecoverable**: the harness itself is broken (crash, invariant violated) | Abort the entire turn |

> In one sentence: **a production-grade harness assumes the model will definitely err, and so it makes "erring" an ordinary, recoverable feed-back path, rather than an exception.** The model is the only one who can correct its own errors — so hand the error back to it.

This chapter's [code.py](code.py) ports this whole thing into a `dispatch_tool` layer (wedged between the loop and the tools, exactly where the real Codex's `ToolRouter` sits): **unknown tool → error string; arguments don't match the schema (missing required / extra field / wrong type) → error string; handler throws an exception → error string** — all fed back, never crashing the process. Note the "loop" itself has no change of responsibility (take the result → feed it back); validation and correction are all in this dispatch layer. At the end of `--demo` it deliberately feeds four kinds of erroneous calls to show you how it catches them (the following is **real output**):

```
> read_file {}
  ERROR: invalid arguments for `read_file`: missing required field(s) ['path']
> write_file {'path': 'x.txt', 'content': 123}
  ERROR: invalid arguments for `write_file`: field `content` should be string, got int
> read_file {'path': 'a.txt', 'lines': 5}
  ERROR: invalid arguments for `read_file`: unexpected field `lines` (allowed: ['limit', 'path'])
> search_web {'q': 'codex harness'}
  ERROR: unknown tool `search_web` (available: ['list_dir', 'read_file', 'shell', 'write_file'])
```

Each one is feedback that "will be fed back to the model so it corrects on the next round," rather than a traceback. **This step is the very threshold that lifts s02 from "it can dispatch" to "production-grade."**

## 🆚 How it differs from Claude Code

| | Claude Code | Codex | Why |
|---|---|---|---|
| Number of first-class tools | **Many**: Read / Write / Edit / Glob / Grep / Bash / … | **Few**: shell + apply_patch handle almost everything | Codex bets on the **generality** of shell+apply_patch; Claude bets on **handy, observable specialized tools** |
| Tool schema shape | `{name, description, input_schema}` | `{type:"function", name, description, parameters}` (flat) | Following each vendor's wire API: Anthropic Messages vs OpenAI Responses |
| Out-of-bounds write protection | Path validation inside the tool code (application layer) | The teaching version likewise uses `safe_path`, but the real thing **relies mainly on the kernel-level sandbox** ([s05](../s05_sandbox/README.en.md)) | In autonomous-run scenarios, application-layer guardrails aren't enough; the kernel has to backstop |
| Cost of adding a tool | Register handler + schema, loop unchanged | Register handler + schema, loop unchanged | **The same on both sides** — this is the true protagonist of this chapter |
| Too many tools to fit | **ToolSearch**: a "tool for finding tools" — a huge pile of tools is first "deferred" so only names remain, and the model searches out the schema on demand before using it | **Deferred exposure + `ToolSearchCall`/`ToolSearchOutput` item**: MCP tools over a threshold (100) don't go directly into the prompt; instead the model discovers them by searching | The same problem (hundreds of tools blowing up the context), the same idea (on-demand discovery); Codex makes it a **protocol item** and ties it to MCP scale → see Deep dive 5 |

**Why does Codex have so few tools?** Because **model differences directly determine tool differences.** Codex's training stakes its bet on two weapons: a `shell` that can run any command, and an `apply_patch` that can edit files precisely ([s03](../s03_apply_patch/README.en.md)). The model is taught to use these two things to "figure it out on its own" — to search code it runs `grep`, to look at a file it runs `sed`/`cat`. Claude Code goes the other way, giving the model a cabinet full of finely-crafted specialized tools (each with a clear schema, structured output, renderable by the UI), so the model splices fewer commands and calls more interfaces. Neither is right or wrong — this is a different bet of "the flexibility of a general shell" versus "the controllability and observability of specialized tools."

> In one sentence: **the count and shape of tools are the shadow cast jointly by the two camps' "assumptions about model capability + wire protocol."**

## Deep dive: teaching version vs real Codex source

<details>
<summary>1. The TOOL_HANDLERS dict vs the real ToolRegistry</summary>

The teaching version's "dispatch map" is just a `dict[str, callable]`, and one line `TOOL_HANDLERS.get(tc.name)` in `run_turn` completes the dispatch. The real Codex's [`registry.rs`](../../codex/codex-rs/core/src/tools/registry.rs) makes the same thing into a typed runtime contract:

```rust
pub struct ToolRegistry {
    tools: HashMap<ToolName, Arc<dyn CoreToolRuntime>>,
}
// dispatch_any_with_terminal_outcome(...) ：按名字取出 tool，再 tool.handle(invocation)
```

If `self.tool(&tool_name)` fetches nothing it returns `unsupported call: <name>` — synonymous with our `else f"unknown tool"`. The difference is all in the extra things it does: when a tool can't be fetched it records a telemetry entry, runs the PreToolUse/PostToolUse hooks ([s13](../s13_hooks/README.en.md)), tags the result with sandbox/policy labels, and broadcasts "tool started/finished" via lifecycle events.

| | Teaching version | Real codex-rs |
|---|---|---|
| Table | `dict[str, fn]` | `HashMap<ToolName, Arc<dyn CoreToolRuntime>>` |
| Dispatch | `TOOL_HANDLERS.get(name)` | `ToolRegistry::dispatch_any` |
| Handler form | A plain function | A struct implementing the `ToolExecutor`/`CoreToolRuntime` trait |
| When nothing is fetched | `"unknown tool: …"` | `FunctionCallError::RespondToModel("unsupported call …")` |

The core is the same: **one table looked up by name.** The hundreds of extra lines in the production version are all "protection and observability" mechanisms — hooks, telemetry, sandbox, parallel scheduling.

</details>

<details>
<summary>2. The flat schema and create_tools_json_for_responses_api</summary>

The teaching version's `create_tools_json` is one line: add a `{"type":"function", ...}` to each tool. The real source's `tools/src/tool_spec.rs:78` `create_tools_json_for_responses_api` does the same thing — serializing a group of `ToolSpec` one by one with `serde_json::to_value`:

```rust
pub fn create_tools_json_for_responses_api(tools: &[ToolSpec])
    -> Result<Vec<Value>, serde_json::Error> {
    tools.iter().map(serde_json::to_value).collect()
}
```

The key is that the `ToolSpec` enum tags its variants with `#[serde(tag = "type")]`: `Function` / `Namespace` / `ToolSearch` / `ImageGeneration` / `WebSearch` / `Freeform(custom)`. That is, a "tool" is not just a "function" — `web_search` and `image_generation` are **host-hosted special types** ([s12](../s12_tools_extra/README.en.md) will run into `web_search`). Our read/write/list all belong to the most ordinary `Function` variant, and serialize out to exactly `{"type":"function","name":...,"parameters":...}`.

</details>

<details>
<summary>3. The real shell tool is far more than "one command string"</summary>

Our `run_shell(command: str)` takes a string and runs `subprocess.run(shell=True)`. The real Codex's shell ([`handlers/shell.rs`](../../codex/codex-rs/core/src/tools/handlers/shell.rs)) takes far more structured parameters: an argv array (not a spliced string), `timeout_ms`, `cwd`, network policy, `sandbox_permissions`/`additional_permissions`, and it first has to go through `create_exec_approval_requirement_for_command` to decide whether to pop an approval, then hand off to `ShellRuntime` + `ToolOrchestrator` to run inside the sandbox, emitting a `ToolEmitter::begin/finish` event before and after.

It even runs `intercept_apply_patch` first — if it discovers this shell command is actually an apply_patch, it reroutes it down the patch channel. In other words the real thing's shell is a composite tool that is "approval-bearing, sandbox-bearing, event-bearing, and can even recognize apply_patch." The teaching version strips all of this away, leaving only the "run a command and grab the output" kernel, so you can see the dispatch itself clearly.

</details>

<details>
<summary>4. safe_path application-layer guardrail vs kernel-level sandbox</summary>

We wrapped read/write/list with `safe_path`: after resolving the path, check `is_relative_to(WORKDIR)`, and throw on out-of-bounds. This is an **application-layer** guardrail — the same as in learn-claude-code. It's good enough for teaching, but has one fundamental weakness: **it only blocks paths that "pass through this function of mine."** The model can perfectly well have `shell` run a `python -c "open('/etc/passwd')"`, bypassing `safe_path`.

The real Codex doesn't pin security on path checks in the tool code; it sinks it down into the kernel: macOS Seatbelt's `(deny default)` + a writable-root allowlist, Linux's Landlock+seccomp ([s05](../s05_sandbox/README.en.md)). Even if the model uses an arbitrary subprocess to write outside the workspace, the kernel refuses outright. This is the first appearance of the course's main thread: **Claude Code "blocks" at the application layer, Codex "closes off" at the kernel layer.** This chapter's `safe_path` has the flavor of "blocking"; its true home is in s05.

</details>

<details>
<summary>5. Too many tools to fit: ToolSearch (CC) vs deferred exposure (Codex)</summary>

By [s15](../s15_mcp/README.en.md) you'll connect MCP — a single MCP server might dump **hundreds of tools** on you. If you stuff all their schemas into every request, the context window gets eaten up by tool definitions alone (the room is gone before any work is even done). Both camps solve this with the same idea: **don't lay them all out; let the model "find tools" on demand.**

**Claude Code: `ToolSearch` is a "tool for finding tools."** The huge pile of tools is marked as **deferred** — at first only the **names** are given to the model, with the schemas not loaded. When the model wants to use one, it first calls `ToolSearch` (passing a query term), the harness returns the full schema of the matching tool, and only then can the model "see" and call it. In one sentence: **turn "loading the tool definition" itself into a tool call.**

**Codex: the same idea, made into "deferred exposure + ToolSearch item" within the protocol.** Look at [`core/src/mcp_tool_exposure.rs`](../../codex/codex-rs/core/src/mcp_tool_exposure.rs):

```rust
pub(crate) const DIRECT_MCP_TOOL_EXPOSURE_THRESHOLD: usize = 100;

let should_defer = search_tool_enabled
    && (config.features.enabled(Feature::ToolSearchAlwaysDeferMcpTools)
        || deferred_tools.len() >= DIRECT_MCP_TOOL_EXPOSURE_THRESHOLD);
```

The logic is dead simple: **fewer than 100 MCP tools and they're exposed directly (`direct_tools`); once ≥ 100 (or a feature flag forces it), it switches to "deferred exposure" (`deferred_tools`) — not entering the prompt, waiting for the model to come search.** And "the model searched for a tool once" is in Codex a **first-class protocol item**: `ResponseItem::ToolSearchCall` / `ToolSearchOutput` (`core/src/turn_timing.rs`) — laid out in that flat item list just like `function_call` and `reasoning`. Remember those six variants of the `ToolSpec` enum from Deep dive 2? One of them is `ToolSearch` — it's a first-class citizen of the Codex tool system to begin with.

**Similarities and differences:**
- **Same**: both exist to cure "too many tools blowing up the context," both use "deferral + on-demand discovery," both make "finding tools" into a call by the model.
- **Different**: CC's `ToolSearch` is a **general, conspicuous meta-tool** (any deferred tool goes through it); Codex **ties it to MCP-at-scale** (threshold 100 + feature flag), and in its consistent temperament makes it a **protocol item** (`ToolSearchCall`/`ToolSearchOutput`, so it naturally enters the rollout and is auditable) — once again "CC puts it at the application layer, Codex puts it into the protocol."

> Echoing this chapter's main thread: the loop still doesn't move. "Too many tools" was never the loop's problem, but the problem of **how to hand the tool list to the model** — nothing more than, before the model calls out a name, first having it go "look up the directory once." The loop's "look up the phone book → dial" line carries on as before.

</details>

## Run

```bash
python s02_tool_use/code.py --demo   # 离线：走分发映射跑 write/read/list + 演示越界拦截
python s02_tool_use/code.py          # 交互模式（mock 后端，无需 key）
```

`--demo` creates a temporary `_demo_workspace/` in the current directory, auto-deletes it when done, and leaves no trace.

## Recap

- Adding a tool = registering one handler (into the dispatch map) + one tool schema. **The agent loop doesn't change by a single line.**
- The real Codex uses `ToolRegistry` + `dispatch_any` to do the same thing, wrapped on the outside with hooks / telemetry / sandbox / events.
- Codex's first-class tools are **few** (shell + apply_patch do it all), Claude Code's are **many** — this is the shadow cast jointly by the two camps' "assumptions about model capability + wire protocol."
- The teaching version's `safe_path` is an application-layer guardrail; its true home is the kernel-level sandbox ([s05](../s05_sandbox/README.en.md)).
- Too many tools to fit: both camps "defer + find tools on demand" — CC uses the `ToolSearch` meta-tool, Codex uses deferred exposure (threshold 100) + the `ToolSearchCall` item (see Deep dive 5).
- **Production-grade**: the hard part isn't "calling the tool correctly," it's "when called wrong it can recover." The schema is both the prompt for the model and the harness's validation ruler (and it must be **same-sourced with the parse target, no drift**); on error go through `RespondToModel` feed-back to let the model correct itself, rather than crashing the process — validation/error-catching are all in the dispatch layer between the loop and the tools (see the "Production-grade" section).
- Next stop [s03](../s03_apply_patch/README.en.md): Codex's signature file-editing tool `apply_patch` — why not use `write_file` to overwrite the whole file?

## Think it over

- Since `shell` can in theory `cat`/`echo >`/`ls`, why still give the model dedicated `read_file`/`write_file`/`list_dir`? What exactly do these dedicated tools buy — do they save the model effort, or save the **frontend** effort?
- Codex gives the model only two weapons, shell + apply_patch, relying on the model to "figure it out on its own"; Claude Code gives a cabinet full of dedicated tools. If you were training a new model, would you bet on "few and general" or "many and specialized"? How would this choice in turn constrain your harness?
- This chapter's `safe_path` can block `write_file("../x")`, but can't block `shell("python -c \"open('/etc/passwd','w')\"")`. For an agent that only does path checks at the application layer, where is the security boundary actually? Doesn't this precisely explain why Codex has to sink its defenses down to the kernel ([s05](../s05_sandbox/README.en.md))?
- The real Codex's `dispatch_any` stuffs PreToolUse/PostToolUse hooks and Begin/End events before and after calling the handler. If you had to use only the teaching version's `dict` to add "ask whether to approve before every tool call," would you add this logic in the dispatch map, or in the loop? Why?
- When an MCP server dumps 500 tools on you, "search first then call" saves context but takes an extra step and might not search exhaustively. How would you weigh "lay them all out" against "find on demand"? That threshold of 100 — what's the cost of raising or lowering it, and who should set it?
- `dispatch_tool` feeds "wrong parameter / wrong tool name / handler crash" all back to the model for it to retry. But what if the model **repeatedly** uses the same wrong parameter and falls into an infinite loop? When should `RespondToModel` (recoverable) be upgraded to `Fatal` (abort)? The real Codex blocks infinite retries with a turn budget + circuit breaker — think about that `MAX_CONSECUTIVE_GUARDIAN_DENIALS_PER_TURN` from s14; how should the same principle be carried over to tool retries?
