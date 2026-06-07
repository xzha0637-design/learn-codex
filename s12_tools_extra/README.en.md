# s12: Tools Extra — more tools: plan / web_search / view_image, plus a few others

> 🌐 **English** · [中文版](README.md)

> *"Giving an agent more power usually isn't about changing the loop — it's about registering one more handler; and in Codex, every handler also has to learn to 'report what it's doing.'"*

[learn-codex overview](../README.en.md) · [Frontends: TUI + exec](../s11_frontends/README.en.md) → **This chapter** → [Hooks](../s13_hooks/README.en.md)

---

## Get the idea straight first: why "giving an agent more tools" barely touches the agent

By now you've seen Codex's most hardcore pieces: the agent loop ([s01](../s01_agent_loop/README.en.md)), apply_patch ([s03](../s03_apply_patch/README.en.md)), approval and sandbox ([s04](../s04_approval/README.en.md) / [s05](../s05_sandbox/README.en.md)). Now let's switch to a lighter — but equally important — topic: **how to "add a skill" to this agent**. Intuitively you might think "adding a skill" is a big undertaking, requiring you to change its brain, change its loop. The opposite is true — once you understand the three ideas below, you'll find adding a tool is almost anticlimactically simple, while the part Codex really sweats over hides in the third one.

**Idea one: the model doesn't need you to "teach" it how to use a tool; it just needs a "menu."**
Recall the consultant analogy from [s01](../s01_agent_loop/README.en.md): the model is a smart consultant who can only write notes. You hand it a menu — "you may order any of these: run a command, edit a file, search the web, view an image" — and at the right moment it'll write a note saying "I'd like to order 'search the web,' argument codex." The **menu** is the tool's schema (name + description + what the parameters look like). So the first step of "adding a tool" is merely **printing one more line** on the menu. The model reads it, and naturally orders it when needed. You don't have to write any "if the user asks X, call Y" if-else — that's the model's job.

**Idea two: the loop has been ready all along; it just does a name lookup in a table.**
[s02](../s02_tool_use/README.en.md) already drove this home: the agent loop has a single line `HANDLERS.get(tc.name)` — whichever dish the model orders, the loop looks it up by name in a `{name: function}` table to find the corresponding chef and produce it. **So the second step of "adding a tool" is registering one more line `"web_search": run_web_search` in this table.** The loop itself doesn't change a single character. In this chapter we add three tools at once (plan checklist, web search, view image), and you'll see with your own eyes: `run_turn` is **identical, word for word**, to the one in [s01](../s01_agent_loop/README.en.md). That's the whole secret of "adding a skill" — it's declarative, not brain surgery.

**Idea three (this is where Codex is particular): a tool can't "work head-down"; it has to "call out" while it works.**
The first two points hold for Claude Code too. The extra layer Codex truly adds is: **every tool action must be "visible."** Why? Imagine `web_search` takes three seconds, `view_image` has to read a big image into memory and encode it. If a tool just "computes head-down and throws back a string," then during those few seconds the person staring at the screen (or the CI log, or the IDE sidebar) sees only a **frozen, feedback-less interface** — what is it searching? Is it stuck?

Codex's answer: make every tool call out "I've started searching" when it begins (a **Begin** event), and call out again "done searching — searched for codex" when it finishes (an **End** event), with the real work sandwiched in between. These two "shout-outs" flow into the event bus from [s10](../s10_sq_eq_protocol/README.en.md), so the TUI, `codex exec`, the IDE backend — **any frontend** — can render "🔍 Searching…" in real time and then update it to "Search complete."

String these three points together and you've grasped this chapter's backbone: **adding a tool = printing one more menu line + registering one more handler (ideas one and two, shared with Claude Code); and Codex additionally insists that every handler broadcast its process as events (idea three, the inevitable extension of its "one core, many frontends" architecture).** This also explains a seemingly odd phenomenon: Codex has surprisingly few "first-class" tools (the workhorses are just run-command + edit-file), but it makes "any tool action is an observable event" extremely uniform — capability is added by registration, observability is backstopped by events.

## Problem

Have the agent do a real multi-step job — "look up how to use this API, look at the user's error screenshot, then write the fix into the code." It runs into two different kinds of trouble:

**The first kind: the capability it needs isn't in the loop yet.** It can't go online to look up "current" facts (the model's training data has a cutoff date), nor can it "see" a local image. Both have to be added as new tools.

**The second kind (more insidious): even if the tools are added, their "running process" is invisible to the frontend.** `web_search` might run for several seconds, or even split into multiple sub-queries; `view_image` has to read the image, encode it, and attach it to the next request. If all the frontend gets is "that final return value," the user stares at a feedback-less interface, not knowing what the agent is actually searching, which image it's looking at, or where it's stuck. The problem isn't "can the tool run," but "is the tool's **process** visible to the frontend."

This chapter uses three tools to solve both kinds of problem together, and uses them to make clear how "adding capability" and "staying observable" work in concert.

## Solution

Two steps, matching the two kinds of problem above exactly:

**Step one: register the three tools into the same `HANDLERS`.** The loop doesn't change; the model reads the menu and calls them on demand.

**Step two: have each tool model its own process as a pair of lifecycle events.** Emit `Begin` at the start, `End` at the end, with the real work sandwiched in between; tie the pair together with a `call_id`, and the frontend can update "in progress" to "done" in place on that very line.

```
   model sees the menu (schemas of 3 tools), orders on demand
        │
        ├─ update_plan({plan:[…]})  → ⟦event⟧ PlanUpdate{steps}        frontend: render a checkbox to-do list
        │
        ├─ web_search("codex")
        │       │
        │       ⟦event⟧ WebSearchBegin{call_id}      ← frontend: show "🔍 Searching…"
        │       （真正去搜 / 本教学版返回 canned 结果，绝不联网）
        │       ⟦event⟧ WebSearchEnd{call_id, query, action}  ← frontend: update in place to "searched codex"
        │
        └─ view_image("shot.png")
                │
                ⟦event⟧ ViewImageBegin{call_id, path}   ← frontend: show "🖼 Viewing…"
                （读字节 / 嗅探类型；真身还会解码、resize、附进请求）
                ⟦event⟧ ViewImageEnd{call_id, path}     ← frontend: show which image was viewed

   每个工具的返回值都照常作为 function_call_output 回灌 → 继续循环
```

This "every action is an event" approach is precisely the direct application of the [s10](../s10_sq_eq_protocol/README.en.md) event queue (EQ): the core only emits events in time order, and the three frontends each decide how to render.

## How it works

See [code.py](code.py). `run_turn` is still lifted from [s01](../s01_agent_loop/README.en.md), **unchanged word for word**; what's new are three handlers and the events they emit internally.

**Step 1** — an `emit` that simulates the event bus, plus a `call_id` for each call (the key that pairs Begin/End):

```python
def emit(event, **fields):        # 模拟把事件放进 EQ；真身会被 TUI/IDE 消费
    print(f"⟦event⟧ {event} {fields}")
```

**Step 2** — `update_plan`: validate → replace the in-memory checklist wholesale → emit a `PlanUpdate` event and render → return one fixed sentence. The key is "resend the full checklist every time, replace wholesale," so the plan "lives" outside the conversation context and doesn't rely on the model's memory:

```python
def run_update_plan(plan, explanation=None):
    # 校验每项有 step + 合法 status；至多一个 in_progress
    CURRENT_PLAN[:] = plan                 # 整盘替换
    emit("PlanUpdate", explanation=explanation, steps=len(plan))
    print(render_plan(plan, explanation))
    return "Plan updated"                  # 回模型的就是这固定一句
```

**Step 3** — `web_search`: emit `Begin` → fetch results (**offline canned, never goes online**) → emit `End`:

```python
def run_web_search(query):
    call_id = next_call_id()
    emit("WebSearchBegin", call_id=call_id)
    hits = _CANNED.get(...)                # 内置假结果，不联网
    emit("WebSearchEnd", call_id=call_id, query=query, action="search")
    return "\n".join(...)
```

**Step 4** — `view_image`: emit `Begin` → read bytes, sniff the type via magic numbers → emit `End`, returning only **metadata** (no real vision):

```python
def run_view_image(path):
    call_id = next_call_id()
    emit("ViewImageBegin", call_id=call_id, path=str(p))
    data = p.read_bytes()
    mime = _sniff_type(data[:16])          # PNG/JPEG/GIF… 魔数
    emit("ViewImageEnd", call_id=call_id, path=str(p))
    return f"viewed image: ... type={mime} size={len(data)} bytes (metadata only)"
```

These three pieces correspond to three places in the real Codex source: the argument structures come from [`protocol/src/plan_tool.rs`](../../codex/codex-rs/protocol/src/plan_tool.rs) (`StepStatus` / `UpdatePlanArgs`), the plan handler is in [`core/src/tools/handlers/plan.rs`](../../codex/codex-rs/core/src/tools/handlers/plan.rs) (`PlanHandler`), web_search's formatting and events are in [`core/src/web_search.rs`](../../codex/codex-rs/core/src/web_search.rs), and view_image's handler is in [`core/src/tools/handlers/view_image.rs`](../../codex/codex-rs/core/src/tools/handlers/view_image.rs); all three event types are defined in the `EventMsg` enum in [`protocol/src/protocol.rs`](../../codex/codex-rs/protocol/src/protocol.rs).

**Walk through it** (using the real output of `--demo`, to see what each step's data looks like and why):

`--demo` doesn't touch the model or go online; it runs the three tools in sequence. Let's look at a few steps:

① Call `update_plan`, submitting a 3-step checklist (one `completed`, one `in_progress`, one `pending`). The handler emits the event first, then renders, then returns a sentence:

```text
  ⟦event⟧ PlanUpdate explanation='Triage with help from the new tools' steps=3
── plan ──────────────────────────────
Triage with help from the new tools
  [x] Search docs for the API        ← completed
  [~] Inspect the screenshot         ← in_progress（至多一个）
  [ ] Write the fix                  ← pending
──────────────────────────────────────
handler 回给模型 → Plan updated
```

Note that what's returned to the **model** is only the five characters `"Plan updated"` — it doesn't echo the whole table (saving tokens, since the model just sent it anyway); that pretty checklist is for the **frontend** to look at. This is exactly the division of labor of "say one sentence to the model, emit one event to the frontend."

② Deliberately submit two `in_progress`, and the handler rejects it on the spot — this is the "at most one in progress" hard constraint at work:

```text
rejected → Error: at most one step may be in_progress at a time
```

③ Call `web_search("codex")`. Note it's sandwiched by **a pair** of Begin/End events with the same `call_id` (`call_1`) — the frontend uses this key to update "searching" in place to the result:

```text
  ⟦event⟧ WebSearchBegin call_id='call_1'
  ⟦event⟧ WebSearchEnd call_id='call_1' query='codex' action='search'
- OpenAI Codex — coding agent
  https://openai.com/codex
  ...
```

④ Call `view_image` on a temporarily fabricated 1-pixel PNG. It read the bytes, sniffed `image/png` from the 8-byte magic number `\x89PNG…`, and returns **metadata** rather than visual content; likewise sandwiched by Begin/End:

```text
  ⟦event⟧ ViewImageBegin call_id='call_2' path='…/_demo_pixel.png'
  ⟦event⟧ ViewImageEnd   call_id='call_2' path='…/_demo_pixel.png'
viewed image: name=_demo_pixel.png type=image/png size=32 bytes (... metadata only)
```

⑤ Finally, call `view_image` with a nonexistent path to see how the error is fed back to the model — **note that even on failure, we still emit an End after the Begin**, so the frontend isn't left with a dangling "Begin but no End" event:

```text
  ⟦event⟧ ViewImageBegin call_id='call_3' path='…/_does_not_exist.png'
  ⟦event⟧ ViewImageEnd   call_id='call_3' path='…/_does_not_exist.png'
Error: image path is not a file: _does_not_exist.png
```

After the demo finishes, it automatically deletes that temporary PNG. Across the whole chain, the loop logic didn't change a single line — everything new lives inside the three handlers.

## Production-grade: tool output blows up — truncation must be stable, and on character boundaries

Tools like plan / web_search / view_image can produce large output: a web search returns dozens of results, an image is several MB, a long plan is hundreds of lines. Feeding it back to the model as-is, at best burns up the context, at worst a single tool result blows past the window. Production-grade has two iron rules for tool output:

- **Truncation has a cap**: every tool output has a maximum length; over the cap it gets trimmed (keep the head + annotate "N more characters omitted"). This is one continuous line with [s02's dispatch](../s02_tool_use/README.en.md) and [s07's compaction](../s07_context_compaction/README.en.md) — **anything entering the context must have a budget**.
- **Trim on character boundaries; don't break multibyte characters**: in UTF-8 a Chinese character / emoji is multiple bytes, and slicing through the middle carves out a half-broken garbage character. The real Codex uses `truncate_to_char_boundary` ([`tools/handlers/list_available_plugins_to_install.rs:100`](../../codex/codex-rs/core/src/tools/handlers/list_available_plugins_to_install.rs)) — truncating by **character** rather than by **byte**. A toy that only truncates byte count quietly spits out garbage here.

> In a sentence: adding tools is easy, but every tool you add that can produce large output adds one more entry point for "blowing past the context / spitting out garbage" — production-grade has to rein in the output at every exit, **with a cap and on character boundaries**.

## 🆚 How it differs from Claude Code

These three tools, both sides **mostly have** (that's the interesting part): plan checklist, web search, view image. The divide isn't "have or have not," but "how is the tool's process exposed to the frontend."

| | Claude Code | Codex | Relationship |
|---|---|---|---|
| Plan checklist | `TodoWrite` (`{content, status, activeForm}`) | `update_plan` (`{step, status}`, [`plan.rs`](../../codex/codex-rs/core/src/tools/handlers/plan.rs)) | **≈** both resend the full checklist, tri-state status, at most one in progress |
| Web search | yes (`WebSearch` / `WebFetch`, harness executes) | yes (`web_search`, mostly **OpenAI-hosted** execution; [`web_search.rs`](../../codex/codex-rs/core/src/web_search.rs)) | **≈** both have it; the execution site differs |
| View image | yes (`Read` can read images, goes through vision) | yes (`view_image` decodes the image and attaches it to the request; [`view_image.rs`](../../codex/codex-rs/core/src/tools/handlers/view_image.rs)) | **≈** both have it |
| How the tool's process is exposed | tool result + a single UI's built-in rendering | **each action emits explicit Begin/End protocol events**, flowing through EQ to multiple frontends | the divide is here |
| What you change to add a tool | register a handler (loop unchanged) | register a handler (loop unchanged) **+ have it emit events** | Codex adds one more layer of observability convention |

**Why are both sides' tools so similar, yet the divide lands on "events"?** Because "adding capability" itself is universal — hand the model a menu, look it up by name and execute — so both sides converge to nearly the same shape on `TodoWrite` ≈ `update_plan`, search, and view-image (even a detail like "resend the full checklist" is the same). What truly pulls them apart is **architecture**: Codex is "one core, many frontends" ([s11](../s11_frontends/README.en.md)) — the TUI, `codex exec`, and the IDE backend all need to display "searching codex…" or "viewing shot.png…" in real time. To make this work, the core **must not hide the tool's process inside a single function return** — it must **broadcast it as events**. So `WebSearchBegin`/`WebSearchEnd`/`ViewImageToolCall`/`PlanUpdate` sit alongside `ExecBegin`/`ExecEnd` (seen in [s04](../s04_approval/README.en.md)), forming a unified vocabulary of "observable actions." Claude Code leans more toward a single interactive frontend, where the tool's process can be left to that one UI's built-in rendering, without first abstracting it into a cross-frontend protocol event.

> In a sentence: **both sides have nearly the same tools; Codex insists on one more thing — "what the tool is doing" must be modeled as protocol events, so any frontend (including the unattended `codex exec`) can observe every step.** This is exactly the manifestation, at the tool layer, of the whole course's through-line "Codex bets on low-intervention autonomous operation."

## Deep dive: teaching version vs real Codex source

<details>
<summary>1. The real PlanHandler barely stores the plan — it emits an event and forgets it on the spot</summary>

The teaching version stores the plan in `CURRENT_PLAN` and directly `print`s it. The real [`plan.rs`](../../codex/codex-rs/core/src/tools/handlers/plan.rs)'s `PlanHandler::handle` does very little:

```rust
let args = parse_update_plan_arguments(&arguments)?;                  // 解析 UpdatePlanArgs
session.send_event(turn.as_ref(), EventMsg::PlanUpdate(args)).await;  // 广播事件
Ok(boxed_tool_output(PlanToolOutput))                                 // 回模型 "Plan updated"
```

Note it **doesn't store the plan in session state** — it stuffs the whole `UpdatePlanArgs` as-is into a `PlanUpdate` event and sends it out, leaving the frontend that consumes the event (the TUI's history cell) to save and render it. "The plan lives outside the context" is more thorough in the real thing: it lives in **the frontend's event history**, and the core forgets it on the spot. What's returned to the model is always the constant `PLAN_UPDATED_MESSAGE` (which is the string `"Plan updated"`), with no echo of the whole table — saving tokens, and the model just sent it anyway.

| | Teaching version | Real codex-rs |
|---|---|---|
| Where the plan is stored | core process memory `CURRENT_PLAN` | not stored in core; flows to the frontend with the `PlanUpdate` event |
| Rendering | direct `print` | TUI/IDE each render after consuming the event |
| Returned to model | `"Plan updated"` | `PLAN_UPDATED_MESSAGE = "Plan updated"` (identical word for word) |

</details>

<details>
<summary>2. The plan's status is an enum; the schema is generated programmatically by plan_spec</summary>

The teaching version's `status` is a string plus a `VALID_STATUS` set validated by hand. The real source uses the Rust enum `StepStatus { Pending, InProgress, Completed }`, with `#[serde(rename_all = "snake_case")]`, guaranteeing on the wire it's `pending`/`in_progress`/`completed` — an illegal value is rejected outright by serde when deserializing `UpdatePlanArgs`, and never even reaches the handler (whereas the teaching version validates only after reaching the handler).

The tool's JSON schema isn't hand-written either, but assembled programmatically by `create_update_plan_tool()` in [`plan_spec.rs`](../../codex/codex-rs/core/src/tools/handlers/plan_spec.rs):

```rust
JsonSchema::string_enum(
    vec![json!("pending"), json!("in_progress"), json!("completed")], ...)
// plan 是 array<object{step, status}>，required = ["plan"]
```

The `TOOLS[0]["parameters"]` we hand-wrote corresponds field-for-field with it. The sentence in the description "At most one step can be in_progress at a time." comes **verbatim** from the real source — it's a soft constraint written into the **tool description shown to the model**; we added a hard check in the handler too (rejecting two `in_progress`), two lines of defense, one soft and one hard.

</details>

<details>
<summary>3. The real web_search is mostly an OpenAI-hosted tool; the core only handles events and display</summary>

The teaching version's `web_search` is a local function that queries a built-in `_CANNED` table. The real Codex's `web_search` **usually doesn't execute in the core** — it's the special variant `ToolSpec::WebSearch` (one of the tool-spec variants seen in the [s02](../s02_tool_use/README.en.md) deep dive), serialized as `{"type":"web_search", ...}` and sent to the model; the search is completed by **OpenAI's Responses API hosted endpoint**, and the result goes directly back into the model's context.

On the core side, [`web_search.rs`](../../codex/codex-rs/core/src/web_search.rs) mainly handles **formatting the search action for the frontend** — `web_search_action_detail` renders a `WebSearchAction` (`Search{query, queries}` / `OpenPage{url}` / `FindInPage{url, pattern}` / `Other`) into a line of human-readable text; the paired `WebSearchBeginEvent`/`WebSearchEndEvent` are defined in [`protocol/src/protocol.rs`](../../codex/codex-rs/protocol/src/protocol.rs). In other words, the real thing's division of labor is: the search is hosted remotely, while **events and display** stay in the core. The teaching version has no remote, so it stuffs both "pretend to search" and "emit events" into one local function, but preserves the Begin/End observability skeleton.

| | Teaching version | Real codex-rs |
|---|---|---|
| Who executes the search | local `_CANNED` table lookup | OpenAI-hosted endpoint (`type:"web_search"`) |
| The core's duty | pretend to search + emit events | format `WebSearchAction` + emit Begin/End |
| End event fields | `call_id, query, action="search"` (string) | `call_id, query, action: WebSearchAction` (with sub-queries/URLs etc. structured) |

</details>

<details>
<summary>4. The real view_image decodes the image, resizes it, and attaches it to the request as an InputImage</summary>

The teaching version's `view_image` only does `read_bytes()` + sniff the magic number + return a line of metadata. The real [`view_image.rs`](../../codex/codex-rs/core/src/tools/handlers/view_image.rs)'s `ViewImageHandler` does far more:

1. First check whether the model's `input_modalities` includes `Image` — if unsupported, reject outright and return the fixed line `"view_image is not allowed because you do not support image inputs"`.
2. Read the bytes via the sandboxed filesystem, and use `load_for_prompt_bytes` (`codex_utils_image`) to **actually decode and resize** (by default scaled per `PromptImageMode`, optionally keeping the original).
3. Convert the image to a base64 **data URL** and stuff it into the tool output as an `InputImage` — so the image **enters the next Responses request as visual input**, and the model can truly "see" it.
4. Emit `started`/`completed` with `TurnItem::ImageView`, corresponding to `ViewImageToolCallEvent { call_id, path }`.

The teaching version cuts step 3 entirely (no real vision, no data URL attachment), keeping only "read the file + emit Begin/End events + return metadata." We also skip details like the `detail` field (the real thing only accepts `high` / `original`; any other value errors out). One-sentence comparison: **the teaching version's view_image "sniffs the file header"; the real thing "first confirms the model can see images, then decodes, scales, and attaches it as visual input."**

Why does the real thing resize rather than send the original directly? Because vision models are both expensive and slow on huge images, and the default scaling strikes a balance between "clear enough to read" and "saving tokens"; only when `original` is explicitly requested and the model supports it is the original resolution kept.

</details>

<details>
<summary>5. How Begin/End events flow to the TUI (tying back to s10 / s11)</summary>

`PlanUpdate` / `WebSearchBegin` / `WebSearchEnd` / `ViewImageToolCall` are all variants of the `EventMsg` enum in the [s10](../s10_sq_eq_protocol/README.en.md) event queue (EQ). They're produced by the core in the handlers via `session.send_event(...)`, and flow through the EQ to the frontend ([s11](../s11_frontends/README.en.md)).

`call_id` is the key that pairs Begin with End: the TUI receives `WebSearchBegin{call_id}` and draws a "searching" line first, then receives the `WebSearchEnd` with the same `call_id` and updates it in place to show what was searched. This is exactly the value of SQ/EQ decoupling — the core only "emits events in time order," and the three frontends (TUI, `codex exec`, IDE backend) each decide how to render. Our `emit()` is what this chain looks like flattened into a single line of `print`. And it's precisely because of this key that the handler **must still emit End even on error** (see the failure branch of our `view_image`), otherwise the frontend is left with an entry forever stuck "in progress."

</details>

## Run

```bash
python s12_tools_extra/code.py --demo   # 离线：update_plan + web_search(canned) + view_image(元数据) + 生命周期事件
python s12_tools_extra/code.py          # 交互模式（mock 后端，无需 key）
```

`--demo` creates a temporary `_demo_pixel.png` in the current directory and deletes it automatically when done; `web_search` never goes online. The default `backend=mock` runs offline; to connect a real model see the root-directory [.env.example](../.env.example).

## Recap

- "Giving an agent more tools" = printing one more menu line (schema) + registering one more handler in `HANDLERS` — the agent loop **doesn't change a single character** (echoing [s02](../s02_tool_use/README.en.md)). This chapter added three at once.
- The new dimension is **observability**: Codex models each tool action as events — `PlanUpdate`, the paired `WebSearchBegin`/`WebSearchEnd`, `ViewImageToolCall` — pairing Begin/End with `call_id` so any frontend can display progress in real time.
- In the real thing: `update_plan`'s handler forgets it on the spot and only emits an event (the plan lives in the frontend's history); `web_search` is mostly executed by OpenAI's hosted endpoint, with the core only handling formatting and events; `view_image` first confirms the model supports images, then decodes, resizes, and attaches the image as an `InputImage` to the next request.
- **≈** with Claude Code: plan (`TodoWrite`) / search / view-image — both sides have them; the divide is that Codex abstracts each action into cross-frontend protocol events ([s10](../s10_sq_eq_protocol/README.en.md) / [s11](../s11_frontends/README.en.md)), in service of "one core, many frontends."
- **Production-grade**: tool output blows up — it must have a length cap and be truncated, and trimmed on **character boundaries** (`truncate_to_char_boundary`, don't break multibyte characters); anything entering the context must have a budget (see the "Production-grade" section).
- Next stop [s13](../s13_hooks/README.en.md): Hooks — insert user-defined logic before and after tool calls, extending the agent further without having to fork the core.

## Think it over

<div class="think">

1. This chapter adds three tools without changing a line of the loop — which shows "adding capability" is declarative. So what really determines an agent's "ceiling": the **number** of tools, or whether the model **orders the right tool at the right moment**? If your agent has 20 tools configured but keeps ordering wrong, would you first add the 21st, or first fix the **descriptions** on the menu?

2. A single tool call emits two events, `Begin` and `End` — what does that gain over emitting just one "done" event? If some `web_search` is interrupted halfway by the user (the Interrupt from [s10](../s10_sq_eq_protocol/README.en.md)), how should the frontend wrap up a "dangling event" that has only a `Begin` and no `End`? (Hint: think about why we also emitted an End when our `view_image` errored.)

3. In Codex, `web_search` is mostly OpenAI-hosted execution with results going directly back to the model; if you were to change it to "the core executes it itself using a search API," what would you lose, and what would you gain? (Think about no-network/offline environments, controllability, and "who's responsible for search quality.")

4. The real `view_image` scales the image before feeding it to the model, trading off between "saving tokens / running fast" and "not losing the detail the model needs to see clearly." For an error screenshot full of fine print, the default scaling might leave the model unable to read it clearly — would you leave the decision "whether to keep the original" to the model itself, or to the user? Why?

5. Claude Code leaves the tool's process to a single UI's built-in rendering; Codex abstracts it into cross-frontend protocol events. When your product **has only one frontend**, is the latter's "event vocabulary" over-engineering? Conversely, the day you need to add a second frontend, how painful is it to not have it? This "betting early on multiple frontends" trade-off ties back to the whole course's through-line — do you think it's worth it?

</div>
</content>
</invoke>
