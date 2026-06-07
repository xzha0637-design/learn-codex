# s04: Approval Policy — Ask Whether It's Allowed First

> 🌐 **English** · [中文版](README.md)

> *"Approval is the user's consent form; the sandbox is the kernel's injunction. The two are orthogonal."*

[learn-codex overview](../README.en.md) · Previous: [s03 apply_patch](../s03_apply_patch/README.en.md) → **s04** → Next: [s05 sandbox](../s05_sandbox/README.en.md)

---

## Get the idea straight first: why "ask whether it's allowed" — and why let you tune the "force of asking"

Up through s03, the agent we've built has a scary temperament: whatever the model says to run, it runs, with no human in between. This chapter adds a gate — but the *thinking* behind adding that gate is worth grasping more than the gate itself. Once you've thought through the three points below, you'll understand why Codex doesn't just slap on a "dangerous-command blacklist" and call it a day.

**Point one: blocking danger can't rely on "listing the bad commands."**
The most intuitive approach is to write a blacklist: `rm -rf /`, `sudo`… block them on sight. But that road is a dead end. How many ways are there to wipe out an entire home directory? `rm -rf ~`, `rm -fr $HOME`, `find ~ -delete`, even a three-line Python script — you can never list them all, and an attacker (or a confused model) can always slip past the few you did list. **Enumerating bad things is doomed to leak.** So the right question isn't "which commands are bad," but "do I **have any confidence** this command is safe" — and if you don't, don't presume to make the call for the user.

**Point two: when unsure, the safest move isn't to "guess" but to "ask the person responsible."**
A conscientious assistant who hits something uncertain won't act on their own, nor flatly refuse — they'll turn around and ask you, "Should this be done?" "Ask for approval" is exactly this instinct: take the **uncertain command** and **escalate** it to the person who actually bears the consequences. Notice how fundamentally this differs from a blacklist — a blacklist tries to "judge right from wrong itself," whereas approval is "admitting it can't judge it all, so it hands the decision back to a human." This step turns the agent from "acting on its own" into "asking for consent first."

**Point three (the most crucial, and most easily overlooked): the "force of asking" must be tunable, because no single setting fits every scenario.**
Here's where Codex's real cleverness lies. Using it locally while you watch the screen, versus having it run unattended in a CI pipeline at midnight — your expectations of "what should stop and ask, and what should pass on its own" are **exactly opposite**. When you're present, you want it not to interrupt you over every little thing, asking only when something is truly dangerous; when nobody's there, a popup that "waits for you to click approve" would make the whole pipeline **hang forever**, and you'd rather it just refuse dangerous commands outright and never stop to wait.
So Codex doesn't hardcode "whether to ask" as a single `if` in the code — it builds it into a **tunable knob**: a few tiers of policy, sliding all the way from "ask about everything" to "never bother." The same agent, in a different tier, can go from a "cautious assistant pairing at your side" to an "unattended automation worker." **Turning 'degree of autonomy' into a knob you can dial** — that's the soul of this chapter on approval.

To string it together in one line: a blacklist tries to judge right and wrong for you (doomed to leak); approval admits it can't judge it all, so it asks when it should (handing the decision back to a human); and that "force of asking" is made into a knob, letting one mechanism cover the entire spectrum from "human watching" to "nobody minding."

## Problem

The shell tool in s01 has a spine-chilling detail: whatever the model says to run, it runs. No blacklist, and no human gatekeeper.

So add a dangerous-command blacklist? It won't hold. The variants of a command are infinite — `rm -rf ~`, `rm -fr $HOME`, `find ~ -delete`, a Python script that deletes files… you can never list them all (this is exactly the other half of the problem that the [s05] sandbox solves).

But there's an even **more upstream** problem: **sometimes we don't want it deciding at all — we want a human to make the call.** Using Codex interactively on your own machine versus running it unattended in a CI pipeline have completely different tolerances for "what should auto-pass and what should stop and ask."

So Codex makes "whether to ask the user" a **tunable policy**, rather than a single `if` hardcoded into the source.

## Solution

An approval gate `decide(command, policy) -> "approve" | "ask" | "reject"`, plus **4 policy tiers**, letting you switch autonomy by scenario:

```
                 ┌──────────────── 审批门 decide() ────────────────┐
   command ─────▶│  is_known_safe(cmd)?   is_dangerous(cmd)?        │
                 │            │                    │                │
                 │            ▼     × 策略档位 ×     ▼                │
                 │   ┌─────────────────────────────────────────┐   │
                 │   │ untrusted  : 白名单外一律 ask              │   │
                 │   │ on-request : 危险才 ask，其余 approve       │   │
                 │   │ on-failure : 先 approve，失败再 ask         │   │
                 │   │ never      : 不问；危险直接 reject          │   │
                 │   └─────────────────────────────────────────┘   │
                 └──────────────────┬──────────────────────────────┘
                                    ▼
                  approve → 执行   ask → 问用户(y/N)   reject → 拒绝
```

`approve` passes straight through, `reject` refuses outright, and only `ask` "escalates" the command to the user — popping up an approval request and waiting for a decision to come back.

## How it works

Look at [code.py](code.py) — three new pieces.

**Piece 1** — two conservative heuristics (not enumerating bad commands, just judging "clearly safe" and "clearly dangerous"):

```python
def is_known_safe(command):   # ls / cat / echo / pwd / grep / git status ...
    ...
def is_dangerous(command):    # rm -f|-rf / sudo / curl|wget ... | sh
    ...
```

They map directly to the real source's [`is_known_safe_command`](../../codex/codex-rs/shell-command/src/command_safety/is_safe_command.rs) and [`command_might_be_dangerous`](../../codex/codex-rs/shell-command/src/command_safety/is_dangerous_command.rs). Note that the safe `git` covers only read-only subcommands (`status` / `log` / `diff` / `show` / `branch`), consistent with the real source's `is_safe_git_command`.

**Piece 2** — the approval gate `decide()`, which layers the policy tier on top of the heuristics to carve out `approve / ask / reject`:

```python
def decide(command, policy):
    safe, danger = is_known_safe(command), is_dangerous(command)
    if policy == "untrusted":  return "approve" if safe else "ask"
    if policy == "on-request": return "approve" if safe else ("ask" if danger else "approve")
    if policy == "on-failure": return "ask" if danger else "approve"
    if policy == "never":      return "reject" if danger else "approve"
```

These three return values correspond exactly to the real source's [`Decision::{Allow, Prompt, Forbidden}`](../../codex/codex-rs/execpolicy/src/decision.rs). The semantics of each tier aren't something I made up — they're copied from [the doc-comment on `AskForApproval`](../../codex/codex-rs/protocol/src/protocol.rs) (`protocol.rs:760`): `UnlessTrusted`'s serialized name is literally `"untrusted"`, "only commands known to be safe and read-only are auto-approved."

**Piece 3** — wrap the shell tool with the gate. The `ask` tier has to actually go ask a human:

```python
def gated_shell(command, policy, ask_user):
    verdict = decide(command, policy)
    if verdict == "approve": return _run(command)
    if verdict == "reject":  return "[拒绝] 未执行"
    return _run(command) if ask_user(command, policy) else "[用户拒绝] 未执行"
```

That round trip of `ask_user` corresponds in the real source to [`ExecApprovalRequestEvent`](../../codex/codex-rs/protocol/src/approvals.rs) (event out) + [`Op::ExecApproval { decision }`](../../codex/codex-rs/protocol/src/protocol.rs) (decision back), with the decision carried by `ReviewDecision`. This is precisely one instance of that "event out, Op back" queue from [s10].

**Walk through it** — follow the same command through the approval gate and see what the data looks like at each step. Suppose the model, on some turn, produces a tool call like this:

```json
{ "type": "function_call", "name": "shell",
  "arguments": { "command": "rm -rf /" } }
```

We don't execute it directly; instead we first feed `command="rm -rf /"` into `decide()`. The first step runs each of the two heuristics once:

```python
is_known_safe("rm -rf /")  → False    # rm 不在只读白名单里
is_dangerous("rm -rf /")   → True     # 命中 "rm -rf" 危险模式
```

Once we have this pair of booleans `safe=False, danger=True`, **the same command yields completely different verdicts under different policy tiers** — and this is exactly what the "knob" means:

```text
decide("rm -rf /", "on-request") → "ask"      # 危险 → 升级问人
decide("rm -rf /", "never")      → "reject"   # 无人值守 → 直接拒，绝不停下等
decide("rm -rf /", "untrusted")  → "ask"      # 非白名单 → 一律先问
```

Note that no tier silently lets it through. Let's continue with the `on-request` tier: the verdict is `"ask"`, so `gated_shell` doesn't execute but produces an approval request to hand to the user — in real Codex it's an `ExecApprovalRequestEvent` that looks like this:

```json
{ "type": "exec_approval_request", "call_id": "call_42",
  "command": "rm -rf /", "cwd": "/work",
  "available_decisions": ["approved", "denied", "abort"] }
```

The user sees it and returns a decision (`denied`). That round trip comes back to `gated_shell`, which accordingly returns `"[用户拒绝] 未执行"` — the command **never actually ran** from start to finish. String these three steps together and you've grasped the whole picture of approval: *the heuristics give "is there any confidence," the policy tier translates that confidence into "pass / ask / reject," and if asking, it runs an out-and-back approval loop.*

`--demo` feeds the same `rm -rf /` into the three tiers `untrusted` / `on-request` / `never`, prints each tier's decision, and lets the "simulated user" refuse when asked — proving that the dangerous command is never actually executed under any of the three tiers.

## Production-grade: approval isn't a bool — it's a decision with memory and brakes

The teaching-version approval gate is elegant, but it squashes "the user's reply" into a `bool` (approve/reject). A production-ready approval system is fierce on three counts — which happen to answer "does it get more dangerous the more comfortable it gets to use."

### 1. The user's reply is a ReviewDecision, more than just "approve/reject"

In the real Codex approval loop, what the user returns is a [`ReviewDecision`](../../codex/codex-rs/protocol/src/protocol.rs) (`protocol.rs:3660`):

| Decision | Meaning |
|---|---|
| `Approved` | Allow this once |
| `ApprovedForSession` | Allow, and **record it in the session cache** — same-prefix commands won't be asked again this session |
| `ApprovedExecpolicyAmendment` | Allow, and **learn a permanent allow rule** |
| `Denied` (default) | Refuse, but continue the session and let the model try another way |
| `TimedOut` | Auto-review timed out → **treated as refusal** (fail closed, echoing [s14](../s14_guardian/README.en.md)) |
| `Abort` | Refuse and stop, awaiting the user's next move |

Note that `Denied` is `#[default]`, and `TimedOut` also tips toward refusal — **the default values all tip toward the safe side**, the same temperament as the sandbox's ([s05]) deny-default.

### 2. Session cache: fewer interruptions the more you use it

`ApprovedForSession` records the approved command prefix into the cache; the next time the same prefix comes up it auto-approves and doesn't interrupt you. `--demo` demonstrates this:

```
用户对 `cargo build --release` 选 ApprovedForSession → ✓ 已记住：本会话内 `cargo …` 自动放行
下次 `cargo test` 的裁决 → auto-approve（会话缓存命中）（没再打扰用户）
```

### 3. Brakes: BANNED_PREFIX — more comfortable to use ≠ more permissive

"Learning to allow" sounds great, but it hides a trap: if you could learn `python` into a permanent allow, then from then on **any** `python -c "..."` would never be asked about — approval is effectively hollowed out. Real Codex uses `BANNED_PREFIX_SUGGESTIONS` ([`exec_policy.rs:52`](../../codex/codex-rs/core/src/exec_policy.rs)) to block this kind of prefix: `python` / `bash` / `sh` / `zsh` / `git` / `pwsh`… these interpreters/shells that can run arbitrary code will **never be generalized into a rule no matter how many times they're approved**:

```
✗ 拒绝把 `python` 学成永久放行：它能跑任意代码，泛化它等于架空审批（BANNED_PREFIX）
✗ 拒绝把 `git` 学成永久放行：……
```

> In one line: production-grade approval = **memorable (fewer interruptions) + has brakes (no slippery slope) + defaults toward refusal (fail closed)**. "approve once" is easy; the hard part is "after approval, the system doesn't become unsafe as a result."

## 🆚 How it differs from Claude Code

| | Claude Code | Codex |
|---|---|---|
| Approval form | **Instant popup** asking you on dangerous operations | Explicit, tunable **policy tiers** (`untrusted/on-request/on-failure/never`) |
| Who configures it | Built into the experience, rarely an exposed "mode" | User/project picks a tier in `config.toml`, the command line, or a profile (see [s16]) |
| Relationship to the sandbox | Approval + path validation is the main defense (application layer) | Approval and sandbox are **orthogonal**: approval = user consent, sandbox = kernel enforcement (see [s05]) |
| Fit for scenarios | Leans interactive, human-in-the-loop | One policy set covers interactive / headless CI / cloud — just change the tier |

**Why the difference?** Because Codex wants to slide along a **continuous spectrum of autonomy**, rather than serving only the one "human watching" scenario:

- You type code locally with `on-request`: everyday commands run on their own, and only dangerous ones stop to ask you.
- `codex exec` runs unattended in a CI pipeline with `never`: never stuck on a popup nobody will answer — dangerous commands are either backstopped by the sandbox or refused outright.
- For maximum caution, use `untrusted`: aside from a few read-only commands, everything gets asked first.

And the most crucial point is splitting **approval and sandbox into two orthogonal layers** (this is exactly where the course's main thread lands in this chapter): approval answers "**does the user consent**," an application-layer gate manned by a human; the sandbox ([s05]) answers "**does the kernel let it touch this**," a kernel-layer gate enforced by the machine. A command can perfectly well be "approved but still running inside the sandbox" — approval only lets it in the door; the sandbox still limits what it can touch once inside. Claude Code treats the approval popup + path validation as the main defense; Codex piles on an additional kernel-layer defense independent of any human, which is exactly why it dares dial the approval tier to `never` for unattended runs.

## Deep dive: teaching version vs. real Codex source

The teaching version's `decide()` with 4 `if`s is, in the real codex-rs, an entire **execpolicy** subsystem + an approval event loop. Let's open it up and see where the differences lie.

<details>
<summary>1. This chapter's 4 tiers are real variants of the source's AskForApproval enum</summary>

The enum at `protocol.rs:760` in the real source (whose doc-comment I copied straight into code.py):

| This chapter's string | Real source variant | Doc-comment's intent |
|---|---|---|
| `"untrusted"` | `AskForApproval::UnlessTrusted` | Only commands `is_safe_command()` deems "read-only" are auto-approved; everything else is asked |
| `"on-failure"` | `AskForApproval::OnFailure` | Auto-approve everything (counting on it running in the sandbox), only escalating to the user on failure |
| `"on-request"` | `AskForApproval::OnRequest` (`#[default]`) | The model decides when to ask the user |
| `"never"` | `AskForApproval::Never` | Never ask; failures go straight back to the model |

Note the real source also has a 5th variant, `Granular(GranularApprovalConfig)` — per-item switches like `sandbox_approval` / `rules` / `mcp_elicitations` / `request_permissions`, taking "ask vs. auto-reject" down to the granularity of individual approval flows. This chapter omits it for teaching purposes.

```rust
// protocol.rs:760
pub enum AskForApproval {
    UnlessTrusted,                 // serde "untrusted"
    OnFailure,
    OnRequest,                     // #[default]
    Granular(GranularApprovalConfig),
    Never,
}
```

</details>

<details>
<summary>2. The real decider is execpolicy: a prefix-rule state machine, not a few ifs</summary>

This chapter's `decide()` writes the judgment as Python branches; the real Codex's judgment body lives in the standalone `codex-rs/execpolicy` crate, a **prefix-rule** engine:

- Rules are written in `.rules` files (a Starlark dialect), of the form `prefix_rule(["git", "push"], decision="prompt")`, `prefix_rule(["rm"], decision="forbidden")`.
- `ExecPolicyManager` (`core/src/exec_policy.rs:235`) loads and merges these rules by priority from multiple layers of config directories, runs `check_multiple_with_options(...)` on a command, and matches the most specific prefix.
- Only **when no rule matches** does it fall back to this chapter's two heuristics: `render_decision_for_unmatched_command()` (`exec_policy.rs:628`) — *this* function is the real decision logic for "unknown command + policy tier → Allow/Prompt/Forbidden," and this chapter's `decide()` is its minimal projection.

The verdict isn't a bare `Decision` but is wrapped into `ExecApprovalRequirement` (`core/src/tools/sandboxing.rs:160`):

```rust
enum ExecApprovalRequirement {
    Skip { bypass_sandbox: bool, .. },   // Allow：直接跑（甚至可绕过沙箱）
    NeedsApproval { reason, proposed_execpolicy_amendment, .. },  // Prompt：问用户
    Forbidden { reason },                // Forbidden：拒
}
```

That `bypass_sandbox` field inside `Skip` is precisely the type-level evidence that "approval and sandbox are orthogonal": a command explicitly allowed by execpolicy can **skip the sandbox**, while every other command enters the sandbox even if approved.

</details>

<details>
<summary>3. Approving once can conveniently write a rule into execpolicy</summary>

The real source's approval is richer than "approve / reject." `ReviewDecision` (`protocol.rs:3660`) has these branches:

| Variant | Meaning |
|---|---|
| `Approved` | Approve this once |
| `ApprovedForSession` | Auto-approve all similar requests within this session |
| `ApprovedExecpolicyAmendment { proposed_execpolicy_amendment }` | Approve + **write this prefix as an allow rule**, so same-prefix commands are no longer asked |
| `NetworkPolicyAmendment { .. }` | Persist a network allow/deny rule |
| `Denied` | Refuse this once, but continue the session and try another way |
| `Abort` | Refuse and stop, awaiting the user's next move |

In other words, when the gate judges `Prompt`, the real source incidentally `derive`s an `ExecPolicyAmendment` (a command prefix) and hands it to you along with the approval request; if you pick `ApprovedExecpolicyAmendment`, it runs `append_amendment_and_update()` to persist `prefix_rule([...], allow)` into `default.rules` — and the next time a similar command comes up you won't be asked. This chapter's `gated_shell` only returns execute/don't-execute; it has no such "learning" loop.

</details>

<details>
<summary>4. Approval isn't a synchronous popup but a round trip through SQ/EQ</summary>

This chapter's `ask_user(command, policy)` is a synchronous function call — ask and immediately get a boolean back. The real Codex can't be this simple, because it has to pop an approval **midway through a turn** and let all three frontends — TUI / `codex exec` / app-server — be able to respond (see [s10]).

So the real path is asynchronous: core produces an `ExecApprovalRequestEvent` (`approvals.rs:217`, carrying `call_id` / `command` / `cwd` / `parsed_cmd` / `proposed_execpolicy_amendment` / `available_decisions`) and drops it into the **event queue**; the frontend renders it and asks the user; the user's decision is wrapped into `Op::ExecApproval { id, decision }` (`protocol.rs:504`) and dropped back into the **submission queue**; only when core receives it does this turn continue.

```
core ──ExecApprovalRequestEvent──▶ (EQ) ──▶ 前端弹窗
core ◀──Op::ExecApproval{decision}── (SQ) ◀── 用户点了批准/拒绝
```

This chapter flattens that round trip into a single function call; [s10] will pull these two queues apart and cover them separately. And [s14]'s Guardian inserts another layer on this loop: **before asking the user, first let an automated reviewer assess the risk.**

</details>

## Run

```bash
python s04_approval/code.py --demo   # 不需要模型：3 档策略 × 安全/危险命令，打印决定
python s04_approval/code.py          # 交互模式：shell 命令先过审批门（默认 on-request）
```

`--demo` is fully offline (`backend=mock`). To hook up a real model, fill in `OPENAI_API_KEY` in the root `.env`.

[s05]: ../s05_sandbox/README.en.md
[s10]: ../s10_sq_eq_protocol/README.en.md
[s14]: ../s14_guardian/README.en.md
[s16]: ../s16_config/README.en.md

## Recap

- The approval gate `decide(command, policy) → approve | ask | reject` corresponds to the real source's `Decision::{Allow, Prompt, Forbidden}`.
- The 4 policy tiers `untrusted / on-request / on-failure / never` are real variants of the source's `AskForApproval`, letting autonomy slide between "ask about everything" and "never ask."
- Don't enumerate bad commands; only judge "clearly safe / clearly dangerous," leaving the rest to the policy tier.
- **Approval ≠ sandbox**: approval is the user's consent (application layer); the sandbox is the kernel's injunction ([s05], kernel layer); the two are orthogonal and stackable.
- **Production-grade**: approval is a `ReviewDecision` with memory — `ApprovedForSession` enters the session cache (fewer interruptions the more you use it), `BANNED_PREFIX` blocks learning an interpreter into a permanent allow (no slippery slope), and `TimedOut`/`Denied` default toward refusal (fail closed). See the "Production-grade" section.
- Next stop [s05](../s05_sandbox/README.en.md): when nobody is approving, what backstops it? Lock the command into a kernel-level sandbox.

## Think it over

1. This chapter's `on-failure`, without a sandbox, chose to "ask first" for dangerous commands. But the real source's `OnFailure` is "**run everything first**, only asking on failure" — it dares to do this because every command runs inside the sandbox. If you only had approval and no sandbox, would you still dare let `on-failure` run first? Doesn't this show that the "approval tier's" courage is actually borrowed from "whether there's a sandbox"?

2. `ApprovedExecpolicyAmendment` lets the user approve once and write the prefix into a permanent allow rule, with fewer interruptions the more it's used. But "more comfortable to use" and "more permissive" are often the same thing — what brakes would you add to this kind of automatic learning? (Hint: the real source has a `BANNED_PREFIX_SUGGESTIONS` where `python` / `bash` / `sudo` may never be suggested as allow prefixes — why these few?)

3. Claude Code uses instant popups; Codex uses tunable tiers. If you were writing an agent that runs in CI with nobody watching, is the `never` tier plus a sandbox safe enough? Conversely, for a local pair-programming agent, would frequent popups instead train the user to "mindlessly click approve," turning approval into a rubber stamp?

4. Approval asks "does the user consent"; the sandbox asks "does the kernel let it touch this." Making them two orthogonal layers has the benefit that each can be tuned in strength independently; but for the user, would "this command was approved yet still failed" actually be harder to understand than a single line of defense? How would you present the state of these two layers to the user?
