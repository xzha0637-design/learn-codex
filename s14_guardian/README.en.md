# s14: Guardian — Automatic Risk Assessment

> 🌐 **English** · [中文版](README.md)

> *"When no one is watching, who keeps the gate? Send an AI reviewer to make the first call."*

[learn-codex overview](../README.en.md) · [Hooks](../s13_hooks/README.en.md) → **this chapter** → [MCP: client + server](../s15_mcp/README.en.md)

---

## Get the idea straight first: when no one is at the gate, send an AI to keep it

The approval layer one level down ([s04]) is elegant: a dangerous command pops up a dialog and lets a human make the call. But it hides an implicit premise — **there has to be a person there, and they have to be actually watching**. The moment Codex runs into a CI pipeline, a cloud task, an IDE backend, or any environment where **no one is staring at the screen** (collectively, headless / unattended), that premise collapses: there is no one to answer the dialog. Every knot in this chapter traces back to that one sentence. Get the following three ideas straight and you'll see why the new role called Guardian is non-negotiable.

**Idea one: when "no one is present," the old approval scheme gets backed into a corner.**
When the model wants to run a risky command, the approval policy either "asks the user" or "doesn't." But under unattended operation, neither path works: set it to "ask the user," and every slightly sensitive command **escalates** (meaning it hands the decision back from the automated flow to a human) to a person who **will never respond** — the turn just hangs there; set it to "don't ask," and you've handed all judgment to the model, which will faithfully run a catastrophic command the moment it slips up or gets talked into it by an injection. **Either hang waiting for a response that never comes, or let everything through** — what's missing in the middle is something that can make the call on the user's behalf while the user is absent.

**Idea two: the missing "judge" is, by its nature, a job you can delegate — so delegate it to an AI.**
Here's the crucial leap: "deciding whether this command should be let through" was originally a human job, but at its core it's **cognitive work**, and cognitive work is exactly what AI can do. So Codex's answer is — before the main agent actually executes a command, **dispatch a dedicated AI reviewer first** (this is **Guardian**) to vet the command on behalf of the absent user. This is not a set of hard-coded `if`s; it's **a separate model call**: feed this reviewing agent "the conversation up to this moment + the action the model plans to take," and let it read and return a verdict. "Spin up another AI to review the first AI" is the soul of this chapter — **a sense of safety doesn't come from blocking every command; it comes from automating the act of "judging" itself, so it can be rolled out at scale to every single command**. (For the sake of being offline-runnable, this chapter **simulates** the reviewer with a set of conservative rules, but the output shape matches the real thing.)

**Idea three (the most important): gatekeeping needs nuance, and it must "fail toward safety."**
Sending an AI to either wave everything through or block everything is too dumb — most commands (`ls`, `echo`) aren't worth bothering anyone about, and a tiny few (`rm -rf ~`, `curl ... | sh`) are almost certainly disasters. So Guardian assigns every action a **risk tier**, four levels from light to heavy: 🟢 `low`, 🟡 `medium`, 🟠 `high`, 🔴 `critical`, then maps each tier to an action — `low → auto_allow` (auto-allow, doesn't bother you), `critical → auto_deny` (deny outright, without even asking), and only the uncertain `medium / high → escalate` (escalate back to [s04] for a human to call). In one line: **both ends are automatic, the middle asks a human.** And when Guardian itself goes wrong — times out, crashes, can't make sense of the return — it **defaults to "deny" rather than "allow"**; this principle is called **fail-closed**: like an access-control system that locks itself shut rather than swinging wide open when the power goes out — **when something breaks, fall toward the safe side**.

Tie the three together: **Guardian = an automatic reviewer, slotted in *before* "ask the user," sorting risk into four tiers — auto-allow the low ones, auto-deny the catastrophic ones, escalate only the middle to a human; it is always fail-closed, and the user can always override it.** This is the most direct answer to "when no one is watching, who keeps the gate," and it's the safety-layer endpoint of this course's through-line, "betting on low/no human intervention": even "the person at the gate" has been automated as much as possible.

## Problem

[s04] gave us the approval policy: dangerous commands escalate to the user for a call. That works great when you're sitting there watching the screen.

But Codex's ambition is to run when **no one is watching** — `codex exec` inside CI, Codex in the cloud, an IDE backend, all running for dozens of turns at a stretch. Here the approval policy hits a wall:

- Set `on-request`, and every slightly sensitive command escalates to the user — but **there is no user responding**, and the turn just hangs there.
- Set `never`, and you simply don't ask — but that hands all judgment to the model, and the moment the model slips up (or gets lured by an injection), it'll run a catastrophic command just the same.

A dilemma: **either hang waiting for a response that never comes, or let everything through.** What's missing in the middle is a layer — something that, while the user is absent, can make the "this can be auto-allowed / this must be blocked" call on the user's behalf.

## Solution

An automatic risk assessor `guardian(action) -> {risk, reason}`, wired in as an **automatic approver** slotted in *before* "ask the user." Risk is sorted into four tiers, each mapped to one automatic decision:

```
   model 要执行 action（命令 / 补丁）
        │
        ▼
   ┌──────────── guardian(action) ────────────┐
   │   评估风险 → {risk, reason}                 │
   │        │                                   │
   │   ┌────┴─────────────────────────────┐    │
   │   │ 🟢 low      → auto_allow（不打扰） │    │
   │   │ 🟡 medium   ┐                      │    │
   │   │ 🟠 high     ┘→ escalate（问用户）   │── 回到 s04 审批门
   │   │ 🔴 critical → auto_deny（连问都不问）│    │
   │   └──────────────────────────────────┘    │
   └────────────────────┬──────────────────────┘
                         ▼
        执行 / 不执行（或交给用户拍板）
```

Low risk gets auto-allowed (so you're not bothered by every `ls`), catastrophic gets auto-denied (fail-closed, without even asking), and only the middle — medium / high — **escalates** back to [s04]'s approval gate for a human to decide. This way: when someone's there, fewer interruptions; when no one is, no breach.

## How it works

Look at [code.py](code.py), two blocks.

**Block 1** — the risk assessor `guardian()`, returning `{risk, reason}` in the same shape as the real source:

```python
RISK_LEVELS = ("low", "medium", "high", "critical")

def guardian(action):
    if "rm -rf ~" in low or "curl ... | sh": return {"risk": "critical", ...}
    if low.startswith("sudo ") or "rm ":     return {"risk": "high", ...}
    if "git commit" / "pip install" / ">":   return {"risk": "medium", ...}
    return {"risk": "low", "reason": "只读或无明显副作用"}
```

The four tier names come straight from the real source [`GuardianRiskLevel::{Low, Medium, High, Critical}`](../../codex/codex-rs/protocol/src/approvals.rs) (`approvals.rs:85`). In reality this judgment isn't made by rules but by a **reviewing LLM** (see the deep dive below); this chapter **simulates** that reviewer with a set of conservative rules so the demo runs offline.

**Block 2** — map the risk tier to an automatic decision, then thread it into execution:

```python
def auto_decision(action):
    risk = guardian(action)["risk"]
    if risk == "low":      return {... "decision": "auto_allow"}
    if risk == "critical": return {... "decision": "auto_deny"}
    return {... "decision": "escalate"}          # medium / high

def guarded_execute(action, ask_user, run_fn):
    v = auto_decision(action)
    if v["decision"] == "auto_allow": return run_fn(action)
    if v["decision"] == "auto_deny":  return "[guardian 自动拒绝] 未执行"
    return run_fn(action) if ask_user(action, v["risk"]) else "[用户拒绝] 未执行"  # escalate
```

The `escalate` branch is precisely the seam with [s04] — what Guardian can't decide gets handed back to the approval gate for a human to call.

`--demo` feeds five actions into guardian: `echo` (low→auto-allow), `git commit` (medium→escalate, simulated user approval), `rm -rf build` (high→escalate, simulated user denial), `curl|sh` and `rm -rf ~` (critical→auto-deny). Each one prints the risk tier, the reason, and the automatic decision.

**Walk through it.** We pick **three representative actions** from the demo and watch each one — **what it looks like going into guardian, what guardian rules, and how it's finally handled** — three that happen to cover the three outcomes "auto-allow / escalate to human / auto-deny."

1. **`echo SQ/EQ works` — low risk, auto-allowed.**
   Into `guardian()`; the command merely prints, with no side effects, falling to the last catch-all rule, which returns:
   ```json
   { "risk": "low", "reason": "只读或无明显副作用" }
   ```
   `auto_decision` maps `low` to `auto_allow`. `guarded_execute` sees `auto_allow` and **executes directly, without bothering the user at all**. This is why you don't get worn out by every `ls`, every `echo`.

2. **`rm -rf build` — high risk, escalate to a human.**
   Into `guardian()`; it hits the "contains `rm`" rule and returns:
   ```json
   { "risk": "high", "reason": "删除文件 / 提权 / 危险操作" }
   ```
   `auto_decision` maps `high` (like `medium`) to `escalate` — **guardian doesn't dare make the call itself**. So `guarded_execute` reaches the last line and calls `ask_user(action, "high")` to kick the ball back to a human (the demo simulates the user answering "deny"). **Why not auto-deny?** Because `rm -rf build` is reasonable in plenty of normal workflows (cleaning up build artifacts), and blanket-denying it would block legitimate work — this "dangerous but possibly reasonable" kind is exactly what should go to a human. This step is the seam with [s04]'s approval gate.

3. **`rm -rf ~` — catastrophic, auto-deny (the spirit of fail-closed).**
   Into `guardian()`; it hits the "catastrophe" rule and returns:
   ```json
   { "risk": "critical", "reason": "可能摧毁系统 / 远程执行任意代码" }
   ```
   `auto_decision` maps `critical` to `auto_deny`. `guarded_execute` **doesn't even ask**, returning `[guardian 自动拒绝] 未执行` directly. **Why not even ask?** Because a command like this has almost no "legitimate use," and escalating it to the user instead creates a chance for a "fat-fingered yes" or a "social-engineered yes" — better to weld it shut. This is "better to over-kill" made concrete in the demo.

Tie the three together: **one and the same guardian gives three commands three risk tiers, then maps them to three dispositions — both ends (low/critical) it handles on its own, and only the middle (medium/high) pulls a human in.** This is the whole value of Guardian: when no one is watching, it shields you from the vast majority of pointless interruptions and pointless risks, leaving you only the small handful that genuinely need human judgment.

## Production-grade: when an AI keeps the gate, you'd better insure that AI

Letting an AI (Guardian) keep the gate for an absent user sounds dangerous right off — the gatekeeper itself can time out, crash, or be fooled. So Guardian's production-grade story is entirely in the several insurance layers wrapped around it:

- **fail-closed**: Guardian times out / crashes / returns JSON that won't parse — **deny across the board**, never "let through on error" (real source `core/src/guardian/review.rs:147/251`). Falling toward safety when the gatekeeper fails is the same instinct as the sandbox's deny-default ([s05](../s05_sandbox/README.en.md)) and approval's `TimedOut→deny` ([s04](../s04_approval/README.en.md)).
- **circuit breaker**: prevents Guardian itself from spiraling into endless review/endless denial. `MAX_CONSECUTIVE_GUARDIAN_DENIALS_PER_TURN = 3` — once consecutive denials in one turn hit the threshold, trip the breaker and stop spinning in place burning money (echoing the "backoff alone isn't enough, you also need a circuit breaker" problem from [s09 retries](../s09_responses_api/README.en.md)).
- **structured contract + timeout**: Guardian must return strict JSON (`GuardianAssessment`), running inside a forked sub-session with a timeout — anything it can't make sense of or won't return goes fail-closed.
- **an honest limitation**: Guardian and the model it reviews are often **the same generation from the same source** — an injection that can fool the first might just as easily fool it (see this chapter's Think it over #1). "AI reviewing AI" adds a check from **a different vantage point**, not an impassable wall.

> In one line: Guardian's value isn't "the AI is smart enough to keep the gate," it's "**when the gatekeeper fails, the whole system falls toward deny rather than allow**." That's the confidence behind it taking the post when no one is around.

## 🆚 How it differs from Claude Code

This is **the single most striking difference (⭐)** between Codex and Claude Code: Codex adds a whole layer of "automatic reviewer," and Claude Code has no counterpart.

| | Claude Code | Codex |
|---|---|---|
| Who judges risk | **The user** (the dialog is put in front of you, you judge) | **An automatic reviewing agent** (Guardian) judges first |
| When no one's watching | Stuck at the dialog / entirely up to the model | Guardian makes the low→allow, critical→deny calls for the user |
| Basis for the judgment | Human intuition + present attention | A reviewing LLM reads the transcript + planned action, returns structured `{risk, outcome, rationale}` |
| Failure mode | The user clicked "approve mindlessly" | Guardian misjudges (but critical is fail-closed + the user can override) |

**Why does Codex need this extra layer?** In one line: **to safely scale up autonomy even when no one is watching.**

Claude Code's vision is "human and agent side by side" — dangerous operations pop up a dialog asking you, and a human holds the final gate. In interactive settings this is both safe and smooth. But it has an implicit premise: **there is a person there, and they're actually watching**. Once you enter headless / CI / cloud, that premise collapses.

Codex bets on "autonomy" — it has to assume **no one is responding**. So it takes "judging risk," a job originally done by a human, and **hands it to an AI too**: a dedicated reviewing sub-agent that, before the main agent's tool call actually executes, independently reads the context once and judges the risk once. This layer lets Codex neither hang waiting for a human on every command nor surrender judgment entirely to a main model that might slip up — instead it uses "a second AI" to keep the gate for "the first AI." This is the ultimate expression, at the safety layer, of this course's through-line "Codex bets on low human intervention": even "the person at the gate" has been automated as much as possible.

Note that Guardian **does not replace** approval and the sandbox; it layers on top of them: when Guardian rules escalate, it still falls back to user approval ([s04]), and the command still ultimately runs inside the kernel-level sandbox ([s05]). It merely lifts out the two ends — "can be auto-allowed / must be blocked" — sparing pointless interruptions and pointless risks.

## Deep dive: the teaching version vs. the real Codex source

This chapter's `guardian()` is a set of `if`s; real Codex's Guardian is a **reviewing-LLM sub-agent that forks the current session, reads the transcript, and returns strict JSON**, plus a suite of fail-closed and anti-abuse mechanisms. Let's take it apart below.

<details>
<summary>1. The real Guardian is a reviewing LLM, not a rule table</summary>

The doc-comment at the top of the real source `core/src/guardian/mod.rs` spells out its workflow plainly:

> Guardian review decides whether an `on-request` approval should be granted automatically instead of shown to the user.
> 1. Rebuild a compact transcript (preserving user intent + the most recent relevant assistant/tool context);
> 2. Have a dedicated guardian review session assess this **exact planned action**, returning strict JSON;
> 3. Timeout / execution failure / malformed output all **fail closed**;
> 4. Apply the allow/deny conclusion the guardian gives.

That is, Guardian is itself **a model call**: it clones the parent session's config (inheriting the same network proxy/allowlist), feeds it "the conversation up to this moment + the action it plans to take," and demands it produce a structured assessment. This chapter simulates this reviewer's output with rules, but the shape matches.

The contract it returns is `GuardianAssessment` (`mod.rs:63`):

```rust
pub(crate) struct GuardianAssessment {
    pub risk_level: GuardianRiskLevel,            // Low / Medium / High / Critical
    pub user_authorization: GuardianUserAuthorization,  // 转录里用户授权得有多直接
    pub outcome: GuardianAssessmentOutcome,       // Allow / Deny（最终裁决）
    pub rationale: String,                        // 人类可读的理由
}
```

This chapter takes only `risk_level` + `rationale`; the real source has one more, `user_authorization` — the reviewer judges "whether the user actually explicitly authorized this kind of action in the conversation," and the more direct the authorization, the more it dares to allow.

</details>

<details>
<summary>2. It only takes the post on the on-request tier, and it is always fail-closed</summary>

Guardian doesn't intervene unconditionally. `review.rs:147`'s `routes_approval_to_guardian` decides when to route to it:

```rust
pub(crate) fn routes_approval_to_guardian_with_reviewer(turn, approvals_reviewer) -> bool {
    matches!(turn.approval_policy.value(),
             AskForApproval::OnRequest | AskForApproval::Granular(_))
        && approvals_reviewer == ApprovalsReviewer::AutoReview
}
```

That is: only when the approval policy is `on-request` (or `granular`) and `AutoReview` is enabled does Guardian take the post — it is precisely the "automated escalation handler" for that `on-request` tier from [s04].

And its fail-closed behavior is carved into the function's comment (`review.rs:251`):

> This function always fails closed: timeouts, review-session failures, and parse failures all block execution.

This chapter expresses the spirit of fail-closed via "critical → auto_deny"; the real source goes further: **the reviewer times out (default 90 seconds, `GUARDIAN_REVIEW_TIMEOUT`), crashes, or fails to parse the JSON — any one of these anomalies, and it's a Deny**. Better to over-kill than to let through.

</details>

<details>
<summary>3. Keeping the reviewer from running wild: the circuit breaker</summary>

Handing "judgment" to an AI raises a new problem: **what if the reviewer itself goes haywire and denies normal actions one after another?** The main agent could get stuck in a "propose → denied → propose again → denied again" infinite loop.

The real source solves this with a circuit breaker (`mod.rs:98`, `GuardianRejectionCircuitBreaker`):

| Constant | Value | Role |
|---|---|---|
| `MAX_CONSECUTIVE_GUARDIAN_DENIALS_PER_TURN` | 3 | 3 consecutive denials within one turn → interrupt that turn |
| `MAX_RECENT_AUTO_REVIEW_DENIALS_PER_TURN` | 10 | 10 denials within the recent window → interrupt |
| `AUTO_REVIEW_DENIAL_WINDOW_SIZE` | 50 | Sliding window size |

When consecutive denials exceed the threshold, `InterruptTurn` stops the turn, avoiding meaninglessly burning tokens. This chapter has no such layer — it assumes review is one-shot and won't loop.

</details>

<details>
<summary>4. Its place in the architecture: one ext crate + one event</summary>

In real Codex, Guardian is an **extension**, not a hard-coded part of core. `ext/guardian/src/lib.rs` implements it as a `ThreadLifecycleContributor`, installed into the registry via `ExtensionRegistryBuilder::thread_lifecycle_contributor`; it holds an `AgentSpawner` used to fork the reviewing sub-agent (`spawn_subagent`).

Its outward-visible output is an event `GuardianAssessmentEvent` (`approvals.rs:178`), from which the frontend renders "Guardian is reviewing / what risk it ruled / what the reason is":

```rust
pub struct GuardianAssessmentEvent {
    pub id: String,
    pub status: GuardianAssessmentStatus,   // InProgress / Approved / Denied / TimedOut / Aborted
    pub risk_level: Option<GuardianRiskLevel>,
    pub rationale: Option<String>,
    pub action: GuardianAssessmentAction,    // Command / Execve / ApplyPatch / NetworkAccess / McpToolCall
    ...
}
```

This event travels precisely along [s10]'s EQ (event queue). And that `action` enum shows Guardian reviews more than just shell commands — `ApplyPatch` (patches, see [s03]), `NetworkAccess` (going out to the network), and `McpToolCall` (MCP tools, see [s15]) are all within its review scope.

The last point is also the most important: **the user can always override Guardian.** The real source has `AUTO_REVIEW_DENIED_ACTION_APPROVAL_DEVELOPER_PREFIX` — when a user manually approves an action that Guardian had denied, a developer message is injected telling the model "the user has manually allowed it." Guardian keeps the gate; it is not a dictator.

</details>

## Run

```bash
python s14_guardian/code.py --demo   # 不需要模型：5 条动作 → 风险档 + 自动决定
python s14_guardian/code.py          # 交互模式：把你输入的命令喂给 guardian
```

`--demo` is fully offline (`backend=mock`).

[s03]: ../s03_apply_patch/README.en.md
[s04]: ../s04_approval/README.en.md
[s05]: ../s05_sandbox/README.en.md
[s10]: ../s10_sq_eq_protocol/README.en.md
[s15]: ../s15_mcp/README.en.md

## Recap

- Guardian = an automatic risk assessor, wired in as an "automatic approver" slotted in before asking the user: low→allow, critical→deny, medium/high→escalate.
- The four risk tiers `low/medium/high/critical` are the real variants of the source's `GuardianRiskLevel`; the product `GuardianAssessment` is structured JSON.
- The real Guardian is a **reviewing-LLM sub-agent** that forks a session, reads the transcript, and returns strict JSON, always fail-closed, with a circuit breaker to guard against running wild.
- It is the most striking difference between Codex and Claude Code (⭐): using "a second AI" to keep the gate for an absent user, born to scale up autonomy when no one is watching.
- It layers on top of approval ([s04]) / the sandbox ([s05]) without replacing them, and the user can always override it.
- **Production-grade**: insure the gatekeeping AI — fail-closed (timeout/crash/parse-failure all deny), circuit breaker (`MAX_CONSECUTIVE_*=3` to prevent idle spinning), strict JSON contract + timeout, and an honest admission that "a same-source model might be fooled by the same injection" (see the "Production-grade" section).
- Next stop, [s15 MCP: client + server](../s15_mcp/README.en.md): hook the agent up to an external tool ecosystem — and Guardian's `McpToolCall` review is reviewing exactly those external tools.

## Think it over

1. Guardian uses "a second AI" to keep the gate for "the first AI." But these two AIs are often the same vendor, same generation of model — if the first can be lured by some injection, will the second be fooled by the same trick? Under what premise does "AI reviewing AI" truly add safety, rather than just adding a layer of same-source blind spot?

2. The real Guardian is always fail-closed: timeout/error/parse-failure all rule deny. This is right for safety, but inside CI — one review timeout blocks a deployment command that was perfectly fine. When "better to over-kill" collides with "the pipeline must be green," how would you design a degradation strategy? In your scenario, which costs more — the cost of over-killing or the cost of letting something slip through?

3. This chapter writes risk as deterministic rules; the real Guardian's risk is judged live by a model, meaning **the same command, two reviews, might give different tiers**. For a safety layer that's supposed to "follow the rules," is this uncertainty a bug or a feature? Would you rather have a reviewer that occasionally misjudges but can understand context, or a rule table that's forever consistent but only matches strings?

4. Claude Code lets the user judge risk themselves; Codex dispatches an AI to judge for the user. The former respects human judgment but requires a human present; the latter frees the human but cedes the judgment to the model. When Guardian has blocked/allowed hundreds or thousands of commands for you and you've looked at not a single one, what is your "trust" in this system actually built on? And is that trust the same thing as your trust in Claude Code's dialog?
