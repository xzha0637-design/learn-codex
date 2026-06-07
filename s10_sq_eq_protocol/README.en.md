# s10: SQ/EQ Protocol — Submission Queue and Event Queue

> 🌐 **English** · [中文版](README.md)

> *"Events flow outward, operations flow inward; the core and the frontend each mind their own business."*

[learn-codex overview](../README.en.md) · [Responses API](../s09_responses_api/README.en.md) → **This chapter** → [Frontends: TUI + exec](../s11_frontends/README.en.md)

---

## Get the idea straight first: cleanly separate "what to do" from "what happened," and one brain can wear many faces

Up until this chapter, our loop has always been "call a function, wait for it to return"—simple and direct, but with a hidden fatal flaw. Picture an utterly ordinary requirement: the agent is partway through its work, the model says "I want to run `rm -rf build/`," and at this point you have to **stop it right then and there and ask "approve or not?"**, only letting it actually run once you give the nod. Written as "call a function, wait for it to return," you'll find this is **simply impossible**. This chapter starts from that impossibility and, step by step, forces out Codex's architectural backbone. Three layers of reasoning.

**Reasoning one: a function call is a "welded-shut straight pipe"—once you go in, you charge straight through to the end, and no one can wedge in along the way.**
The line `output = run_shell(cmd)`, the instant it's called, just buries its head and runs the command to completion and spits out the result; **between "deciding to run" and "actually running," there isn't any gap at all**. But "approval," precisely, needs exactly that gap: between "wanting to run" and "having run," to pause, let a human step in, and wait for them to weigh in. The same goes for "I want to call a halt midway" and "it's still busy and I want to add another sentence"—all of these demand "wedging in midway through an ongoing operation." The straight-pipe function call structurally rejects this kind of insertion; this isn't a matter of not being written well enough—it's the fatal weakness of this shape itself.

**Reasoning two: the fix is to split "what I want to do" and "what's happening on my end" into two independent streams, leaving a gap in the middle where someone can step in.**
Since one straight pipe won't do, then **cut it open into two streams, one each way**: one stream dedicated to carrying "things the outside wants the core to do"—the user typed a sentence, the user approved this command, the user wants to interrupt (this is "what to do," flowing inward); the other dedicated to carrying "what's happening on the core's end"—a turn has started, I want to run this command please approve it, the command's output is in, I said a sentence (this is "what happened," flowing outward). Each lines up into its own queue, one in and one out, neither blocking the other. And so that crucial gap appears: the core can shout out on the outward stream "I want to run this one, approve or not?", then **stop right there and wait**; and whether it's approved or not will come back along the inward stream, as a separate message, **a little later**. Approval is thus no longer "a single function call," but "one message out + one message back"—precisely because it's been split into two slips of paper on two streams, this "asking" can wedge into the middle of an ongoing turn. Interrupting and adding input midway rely on the very same gap.

**Reasoning three (the most crucial): this split, as a side effect, completely decouples the core from the "interface"—from now on, one brain can wear many faces.**
Look again at those two streams above: on the inward stream, the core simply doesn't care where this "what to do" came from—whether it was typed in a terminal, fed by a CI script, or sent over the network by an IDE, it's all the same to the core; on the outward stream, the core also doesn't care who consumes this "what happened" in the end, or how it gets painted. In other words, **the core only deals with these two streams, and from now on never needs to know what the interface looks like**. This unlocks exactly the form Codex truly wants: **the same core logic, with several different faces hooked up behind it**—the colorful interactive interface in a terminal, the command line in CI that runs and then quits, the service process behind an IDE plugin; they submit the same kind of "what to do" and consume the same "what happened" stream. One kitchen, several dining rooms, sharing the same cooking process. "Interface-agnostic" and "can step in midway through a turn" are actually two faces of the same single cut.

Connecting the three points: the straight pipe can't admit a human → so cut it open into two streams, "what to do / what happened," and a human decision can be stuffed into the gap → and this split in turn decouples the core from the interface, one brain wearing many faces. These two streams are the "submission queue / event queue" of this chapter's title, and the bedrock of everything that follows.

## Problem

The loop in s01 is "call a function, take the return value"—the frontend and the core are welded together. But the real Codex needs to:

- Pop up an approval **partway through a turn** ("want to run `rm`, approve or not?"), and continue only after the user responds;
- **Interrupt** a running turn at any time;
- Let **three frontends—TUI, `codex exec`, and app-server (the IDE backend)—share the same core logic**.

Directly `return`-ing a result makes none of this possible—you can't wedge a human decision into the middle of a function return.

## Solution

Split input and output each into a queue:

```
  前端 (TUI / exec / IDE)                     Session (core)
        │                                          │
        │   submit Op  ──────────────▶  Submission Queue (SQ)
        │   (user_input / exec_approval / interrupt)│
        │                                          │  处理 Op、产出 Event
        │   render Event ◀────────────  Event Queue (EQ)
        │   (turn_started / exec_begin / agent_message / ...)
```

The core only cares about "consume Ops, produce Events," and has no idea what the frontend looks like; the frontend only cares about "render Events, submit Ops." The two queues in the middle decouple them completely.

The teaching version takes only the three most representative Ops (actions the frontend can submit)—let's get acquainted first:

| Op | When it's sent | What it does |
|---|---|---|
| `user_input` | You finish typing a sentence and hit enter | Opens a turn |
| `exec_approval` | The core asks "approve or not?" and you've answered | Sends the approval decision back to the core |
| `interrupt` | You hit interrupt (e.g. Esc / Ctrl-C) | Calls a halt to the running turn |

(The real Codex has a dozen-odd Ops; here there are only enough to lay out the "ask—answer—stop" main thread clearly; for the full list see "Deep dive" below.)

**Why two queues, instead of one function?** Contrast it with s01's approach and you'll get it:

```python
# s01 的同步写法：前端和 core 焊死，一行 return 定生死
output = run_shell(cmd)          # core 直接执行、直接返回，中间没有任何缝隙

# s10 的双队列写法：执行被拆成"问一声"和"得到答复"两半
yield  Event("exec_approval_request", command=cmd)   # ← core 把"请批准"丢出去，然后挂起
decision = ...                                        # ← 这里是一道缝：可以塞进一个人类决定
if decision == "approved": run_shell(cmd)            # ← 拿到答复才执行
```

In an approach like `run_shell(cmd)`, **once the function is called it runs straight through to the end**, and you have no chance at all to wedge a human in between "deciding to run" and "actually running." But the dual queues split these two things onto two conveyor belts: the core shouts on the EQ "I want to run this one, approve or not?", then **stops and waits**; the frontend, taking its time, finishes asking the user, then sends the answer back via the SQ. This "gap"—is the only hiding place for capabilities like approval, interruption, and adding input midway through a turn.

> Analogy: a synchronous function is like an **automatic door**—it opens the moment a person approaches, can't be held shut; the dual queues are like a **doorbell + intercom**—ring the bell (Event out), the person inside decides whether to open (Op back), then open the door (execute). One more link where a human can step in.

## How it works

See [code.py](code.py). This chapter uses Python generators to lay out the two queues clearly—**`yield` is the EQ (events flow out), `.send()` is the SQ (decisions flow in)**:

```python
def run_session(messages):
    yield ev("turn_started")
    ...
    for tc in resp.tool_calls:
        decision = yield ev("exec_approval_request", command=command)  # 事件出 → 决定入
        if decision == "approved":
            yield ev("exec_begin", command=command)
            output = run_shell(command)
            yield ev("exec_end", output=output)
```

The frontend `drive()` consumes events; when it hits `exec_approval_request` it constructs an `Op("exec_approval")` and `send`s the decision back. The two dataclasses `Op` / `Event` correspond to the two enums `Op` and `EventMsg` in the real source `../../codex/codex-rs/protocol/src/protocol.rs` (here we take only a minimal subset).

**Walk through it.** Let's follow that one turn in `--demo` and see **exactly what each slip of paper on the two conveyor belts looks like**, and **why** they have to be passed this way. The user's words are "run `echo SQ/EQ works`."

1. The frontend makes the user input into an **Op slip** and drops it into the SQ (inward):
   ```json
   { "op": "user_input", "text": "执行 `echo SQ/EQ works`" }
   ```
   The core, on receiving it, starts a turn. **Why a slip of paper instead of a function argument?** Because the frontend and the core may not even be in the same thread/process (in the real Codex they're two tokio tasks), so they can only communicate by passing slips.

2. The core first drops an **Event** onto the EQ (outward), telling the frontend "we're open for business":
   ```json
   { "event": "turn_started" }
   ```
   On receiving it the frontend can paint a "thinking…" prompt.

3. The model decides to run `echo SQ/EQ works`. Note: **the core won't sneakily run it on its own**, but first stops and drops a "please approve" Event onto the EQ:
   ```json
   { "event": "exec_approval_request", "command": "echo SQ/EQ works" }
   ```
   In the code, this step is that line `decision = yield ev("exec_approval_request", ...)`—`yield` **sends the event out**, then **suspends right there**, waiting for a decision to come back. **Why suspend?** This is exactly the point of the dual queues: the turn is stuck halfway, waiting for a human decision, rather than charging ahead and executing.

4. The frontend sees this "please approve" Event, asks the user (in the demo it auto-answers approved), makes the decision into an **Op slip** and sends it back to the SQ (inward):
   ```json
   { "op": "exec_approval", "decision": "approved" }
   ```
   In the code this is `.send("approved")`—it becomes the return value `decision` of that `yield` in step 3, and the turn **comes back to life right where it was** from suspension.

5. Having gotten `approved`, the core finally actually executes, and splits the process into two Events dropped back onto the EQ:
   ```json
   { "event": "exec_begin", "command": "echo SQ/EQ works" }
   { "event": "exec_end",   "output": "SQ/EQ works\n" }
   ```
   Based on these the frontend first shows "running this command," then shows the output.

Putting this whole round trip together: **Ops flow from the frontend to the core (user_input, exec_approval), Events flow from the core to the frontend (turn_started, exec_approval_request, exec_begin, exec_end)**—one in and one out, cleanly delineated. And that thing of "suspending at step 3, coming back to life at step 4 via an external slip of paper" is exactly why Codex can "wedge a human's approval decision in while the model is streaming output"—which is something ordinary "call a function, take the return value" cannot do.

## Production-grade: the queues have to withstand "produced faster than consumed"

Decoupling the core and the frontend with two queues (SQ for submissions / EQ for events) gives you, above all, asynchrony; but asynchrony immediately brings three production-grade problems:

- **Backpressure**: the model streaming out events may be faster than a slow frontend (a stuttering IDE, a `codex exec` redirected to disk) can consume. If the event queue is **unbounded**, the backlog will eat memory all the way to OOM. Production-grade either uses a **bounded queue** (when full, makes the producing side wait a bit, pressing the "fastness" down), or explicitly drops the droppable intermediate events—rather than pretending the downstream can always keep up.
- **Ordering guarantees**: the same turn's `reasoning → function_call → output → completed` must arrive at the frontend **in order**, otherwise the UI will render scrambled cause-and-effect. The ordering of the event stream is part of the protocol (the `EventMsg` in [`protocol.rs`](../../codex/codex-rs/protocol/src/protocol.rs)), not "best effort."
- **Interrupts must be able to wedge into the queue**: the user's `Op::Interrupt` (mentioned in [s01](../s01_agent_loop/README.en.md)) has to be able to cut through the submission queue **promptly** and interrupt the running turn—this requires that the queue not be jammed by the work ahead of it to the point where even the interrupt can't be handed in.

> In a sentence: the dual queues give you decoupling, but you have to answer on their behalf "what to do when the downstream is slow." **Bounded + ordered + interruptible** is what makes an event stream production-ready, rather than just a `yield` in a demo.

## 🆚 How it differs from Claude Code

| | Claude Code | Codex |
|---|---|---|
| Loop shape | A fairly direct "request → response" loop | **SQ/EQ dual queues**, core decoupled from frontend |
| Approval | Synchronous popup, blocking before execution | Approval is one `Event` out, one `Op` back, **wedged asynchronously into the streaming turn** |
| Number of frontends | A single CLI/TUI | TUI + `codex exec` + app-server **sharing the same core** |
| Interrupt / midway input | Limited | Submit `Interrupt` / input-appending `Op` at any time |

**Why?** The operating form Codex envisions is "**one core, many frontends**": in the terminal it's the TUI, in CI it's `codex exec`, in the IDE it's app-server, and they must reuse the same turn logic. To let them all "insert a human decision (approval/interrupt) while the model is streaming output," you can't use synchronous coupling like a "function call return value"—you can only split input and output into two queues. Claude Code leans toward a single interactive frontend, so its loop can be written more directly.

## Deep dive: the teaching version vs. the real Codex source

The real protocol is in [`protocol/src/protocol.rs`](../../codex/codex-rs/protocol/src/protocol.rs); both `Op` and `EventMsg` are very large enums. The teaching version takes three or four variants of each.

<details>
<summary>1. Op is far more than 3 kinds</summary>

The real `Op` includes `UserInput`, `Interrupt`, `ExecApproval`, `PatchApproval`, `Compact`, `ThreadRollback`, `Review`, `RunUserShellCommand`, `ReloadUserConfig`, `RefreshMcpServers`, `ResolveElicitation`… each one is a class of action the frontend can submit. The teaching version takes only `user_input / exec_approval / interrupt`.

</details>

<details>
<summary>2. EventMsg is fine-grained</summary>

The real `EventMsg` has `TurnStarted/TurnComplete`, `AgentMessage`, `AgentReasoning`, `ExecCommandBegin` / `ExecCommandOutputDelta` / `ExecCommandEnd`, `ApplyPatchApprovalRequest`, `PatchApplyBegin/End`, `McpToolCallBegin/End`… in particular `OutputDelta` lets the frontend display command output **streaming**, whereas the teaching version merges/omits these.

</details>

<details>
<summary>3. The generator is just a teaching aid; the reality is async channels</summary>

The teaching version uses the Python generator's `yield`/`send` to simulate the two queues—this is **cooperative**: only when the Session actively yields control can the frontend get a word in. The real Codex uses tokio's `mpsc` channels: the Session runs in one task, the frontend sends and receives in another task, and so it can **truly asynchronously** interrupt or approve while a turn is in progress.

</details>

<details>
<summary>4. The same Event stream feeds three frontends</summary>

`TUI`, `codex exec` (s11), and `app-server` (the IDE backend) are all consumers of the same `EventMsg` stream; the `Submission` can also come from different sources (keyboard / stdin / WebSocket). One core, many skins.

</details>

## Run

```bash
python s10_sq_eq_protocol/code.py --demo   # 看事件流出(EQ) + 审批流入(SQ)
python s10_sq_eq_protocol/code.py          # 交互模式：每条命令都问你批不批
```

`--demo` is completely offline (doesn't call the real model), and runs that whole round trip from "Walk through it" above. You'll see the events flow **out** one by one (turn_started → exec_approval_request → exec_begin → exec_end), with one approval decision flowing **back** in the middle. Read it against the five steps of "Walk through it," and the in-and-out directions of the dual queues become concrete. Interactive mode replaces the "auto approved" of step 4 with actually asking you `y/N`—you're the one standing in the gap making the decision.

## Recap

- Split "submissions (Op)" and "events (Event)" into two queues, and the core and the frontend are completely decoupled.
- This layer of decoupling is exactly the prerequisite for "in-turn approval / interruption / multiple frontends"—and also Codex's architectural backbone.
- **Production-grade**: an asynchronous queue has to withstand "produced faster than consumed"—backpressure (a bounded queue to prevent OOM), strict event ordering, interrupts being able to wedge promptly into the queue (see the "Production-grade" section).
- Next stop [s11 Frontends: TUI + exec](../s11_frontends/README.en.md): hook this core up to a real dining room—the same Event stream, feeding the two skins TUI and `codex exec`.

## Think it over

<div class="think">

1. The generator's `yield/send` is cooperative—only when the Session yields control can the frontend get a word in. A true async channel lets the frontend submit `Interrupt` **at any time**. On the matter of "interrupting a hung command," where does the difference in their behavior lie?
2. Three frontends connect to one Session at the same time, each submitting `Op`s—who should the events be broadcast to? How do conflicts get reconciled?
3. Modeling "approval" as one Event + one Op (rather than a synchronous function call)—what shapes can it let the approval UI grow into? (Think: pushing the approval to your phone to tap "agree.")
4. Claude Code works without such a heavy queue protocol. What did it thereby sacrifice, and what did it gain in return?

</div>
