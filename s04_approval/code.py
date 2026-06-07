#!/usr/bin/env python3
"""
s04: Approval Policy — 在执行命令前，先按「策略」决定要不要问用户。

运行:
  python s04_approval/code.py --demo    # 不需要模型：在 3 个策略下各跑安全/危险命令，打印决定
  python s04_approval/code.py           # 交互模式：shell 命令先过审批门，再决定执行

本章 = s01 的回合循环 + shell 工具（搬运）
     + 新增：一个审批门 decide(command, policy) -> "approve" | "ask" | "reject"
       策略有 4 档：untrusted / on-request / on-failure / never。
       门用「已知安全前缀白名单」+「危险命令启发式」做判断，再由策略档位裁决。
     + 生产级：审批是带记忆的 ReviewDecision（不是 bool）——ApprovedForSession 进会话缓存、
       同前缀下次自动放行；但解释器/shell 前缀（python/bash/git…）永不许被学成 allow（BANNED_PREFIX 刹车）。

忠实对应 codex-rs（变体名直接来自真源码）：
  - AskForApproval 4 档 UnlessTrusted("untrusted")/OnFailure/OnRequest/Never  (protocol/src/protocol.rs:760)
  - 裁决产物 Decision::{Allow, Prompt, Forbidden}                            (execpolicy/src/decision.rs:9)
  - 已知安全 is_known_safe_command（ls/cat/echo/git status...）               (shell-command/.../is_safe_command.rs:12)
  - 危险启发式 command_might_be_dangerous（rm -rf / sudo ...）                (shell-command/.../is_dangerous_command.rs:7)
  - 审批=一条事件出、一条 Op 回：ExecApprovalRequestEvent + Op::ExecApproval{decision}  (approvals.rs:217 / protocol.rs:504)

⚠️ 关键：审批 ≠ 沙箱（s05）。审批 = 「用户同不同意」（应用层、人把关）；
   沙箱 = 「内核让不让碰」（内核层、机器强制）。两者正交：命令可以被批准、但仍跑在沙箱里。
"""

import subprocess
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
from codexlib import Model, user_item, tool_output_item  # noqa: E402

WORKDIR = Path.cwd()
SYSTEM = (f"You are Codex at {WORKDIR}. Use the shell tool to act. "
          "Act, don't explain.")
model = Model()


# ═══════════════════════════════════════════════════════════
#  NEW in s04 ①：两套启发式 —— 已知安全 / 可能危险
#
#  Codex 不靠「枚举所有坏命令」(那永远列不全)。它只做两件保守的判断：
#    is_known_safe : 命令是不是「只读、明显无害」的少数已知安全命令
#    is_dangerous  : 命令是不是命中了少数高危模式 (rm -rf / sudo / curl|sh)
#  其余「未知命令」既不白也不黑，交给策略档位去裁决。
# ═══════════════════════════════════════════════════════════

# 真源码 is_safe_command.rs 里的只读安全集合（节选其核心成员）。
_KNOWN_SAFE_PREFIXES = {
    "ls", "cat", "echo", "pwd", "head", "tail", "grep", "wc", "stat",
    "which", "whoami", "true", "false", "id", "uname", "seq", "cut",
}
# git 仅这几个只读子命令算安全（真源码 is_safe_git_command）。
_SAFE_GIT_SUBCOMMANDS = {"status", "log", "diff", "show", "branch"}


def is_known_safe(command: str) -> bool:
    """只读、明显无害 → True。对应 is_known_safe_command。"""
    parts = command.split()
    if not parts:
        return False
    head = parts[0]
    if head == "git":
        sub = parts[1] if len(parts) > 1 else ""
        return sub in _SAFE_GIT_SUBCOMMANDS
    return head in _KNOWN_SAFE_PREFIXES


def is_dangerous(command: str) -> bool:
    """命中高危模式 → True。对应 command_might_be_dangerous（保守启发式）。"""
    c = command.strip()
    parts = c.split()
    head = parts[0] if parts else ""
    # rm -f / rm -rf （真源码 is_dangerous_to_call_with_exec 的判断）
    if head == "rm" and any(a in ("-f", "-rf", "-fr") for a in parts[1:]):
        return True
    # sudo <cmd>：脱去 sudo 再看里面是不是危险（真源码递归处理 sudo）
    if head == "sudo":
        return True
    # 把远程脚本 pipe 进 shell 执行：curl ... | sh / wget ... | bash
    if "|" in c and any(s in c for s in ("sh", "bash", "zsh")) and \
            any(d in c for d in ("curl", "wget")):
        return True
    return False


# ═══════════════════════════════════════════════════════════
#  NEW in s04 ②：审批门 decide() —— 策略档位裁决
#
#  这是本章的心脏。它把「命令的两种启发式」叠加上「4 档策略」，
#  产出三选一：approve（直接放行）/ ask（问用户）/ reject（直接拒）。
#  对应真源码 render_decision_for_unmatched_command 的 Decision::{Allow,Prompt,Forbidden}。
# ═══════════════════════════════════════════════════════════

POLICIES = ("untrusted", "on-request", "on-failure", "never")


def decide(command: str, policy: str) -> str:
    """审批门：返回 "approve" | "ask" | "reject"。

    各档语义直接照搬真源码 AskForApproval 的 doc-comment：
      untrusted   : 只有「已知安全且只读」的命令自动批准，其余一律问。
      on-request  : 模型/门按需问；非危险的就放行，危险的升级给用户。
      on-failure  : 全部先放行（真实里靠沙箱兜底），失败了才问用户。
      never       : 永不问用户；危险命令在无沙箱兜底时只能拒绝。
    """
    safe = is_known_safe(command)
    danger = is_dangerous(command)

    if policy == "untrusted":
        # 最保守：白名单之外的一切都问。
        return "approve" if safe else "ask"

    if policy == "on-request":
        if safe:
            return "approve"
        # 危险 → 升级问用户；普通未知命令 → 放行（按需才问）。
        return "ask" if danger else "approve"

    if policy == "on-failure":
        # 先跑再说（真实里有沙箱保护）；本教学版对危险命令仍升级问用户，
        # 因为这里没有沙箱兜底（对应真源码「无沙箱保护时宁可 Prompt」）。
        return "ask" if danger else "approve"

    if policy == "never":
        # 永不问用户。危险命令在没有沙箱兜底时只能直接拒
        # （对应真源码：approval=Never 且 sandbox 未禁用 → Decision::Forbidden）。
        return "reject" if danger else "approve"

    raise ValueError(f"unknown policy: {policy!r}（可选：{POLICIES}）")


# ═══════════════════════════════════════════════════════════
#  FROM s01（搬运）：shell 工具本体
# ═══════════════════════════════════════════════════════════

def _run(command: str) -> str:
    try:
        r = subprocess.run(command, shell=True, cwd=WORKDIR, capture_output=True,
                           text=True, encoding="utf-8", errors="replace", timeout=120)
        out = (r.stdout + r.stderr).strip()
        tag = "" if r.returncode == 0 else f"[exit {r.returncode}] "
        return tag + (out[:50000] if out else "(no output)")
    except (subprocess.TimeoutExpired, OSError) as e:
        return f"Error: {e}"


# ═══════════════════════════════════════════════════════════
#  NEW in s04 ③：用审批门包住 shell 工具
#
#  ask_user 决定「问到用户时如何回应」——demo 里脚本化（拒绝危险命令），
#  交互里真去问。这正对应真源码里 ExecApprovalRequestEvent 出、
#  Op::ExecApproval{decision: ReviewDecision} 回的那一来一回。
# ═══════════════════════════════════════════════════════════

def gated_shell(command: str, policy: str, ask_user) -> str:
    if prefix_of(command) in SESSION_ALLOW:        # 会话缓存命中：批准过的前缀不再打扰
        return "[会话缓存自动放行] " + _run(command)
    verdict = decide(command, policy)
    if verdict == "approve":
        return _run(command)
    if verdict == "reject":
        return f"[策略 {policy} 直接拒绝] 高危命令未执行：{command}"
    # verdict == "ask"：升级给用户（一条 approval 请求出，一条决定回）。
    if ask_user(command, policy):
        return _run(command)
    return f"[用户拒绝] 命令未执行：{command}"


# ═══════════════════════════════════════════════════════════
#  NEW in s04 ④（生产级）：审批是带记忆的 ReviewDecision，不是一个 bool
#
#  真 Codex 的用户答复是 ReviewDecision（protocol.rs:3660），远不止「同意/拒绝」：
#    Approved / ApprovedForSession（记进会话缓存，同前缀下次自动放行）/
#    ApprovedExecpolicyAmendment（学成一条永久 allow 规则）/ Denied(默认) /
#    TimedOut（自动评审超时 → 按拒处理，fail-closed）/ Abort（停下，等用户）
#
#  「越用越少打扰」的代价是「越用越宽松」——所以有一道刹车 BANNED_PREFIX_SUGGESTIONS：
#  解释器/shell 这类前缀永远不许被学成 allow，因为「允许 python」= 允许跑任意代码，
#  等于把审批架空（真源码 exec_policy.rs:52）。
# ═══════════════════════════════════════════════════════════

# 节选自真源码 BANNED_PREFIX_SUGGESTIONS：这些前缀再怎么批准也不会被泛化成永久规则。
BANNED_PREFIXES = {"python", "python3", "py", "pythonw", "pypy", "pypy3",
                   "bash", "sh", "zsh", "pwsh", "powershell", "git",
                   "/bin/bash", "/bin/zsh"}
SESSION_ALLOW: set[str] = set()    # 会话级审批缓存（ApprovedForSession 记下的前缀）


def prefix_of(command: str) -> str:
    parts = command.split()
    return parts[0] if parts else ""


def remember_prefix(command: str) -> str:
    """把一条被批准的命令学成会话 allow 前缀；若命中 BANNED 前缀则拒绝学。
    对应 derive_requested_execpolicy_amendment + BANNED_PREFIX_SUGGESTIONS 的刹车。"""
    p = prefix_of(command)
    if p in BANNED_PREFIXES:
        return f"✗ 拒绝把 `{p}` 学成永久放行：它能跑任意代码，泛化它等于架空审批（BANNED_PREFIX）"
    SESSION_ALLOW.add(p)
    return f"✓ 已记住：本会话内 `{p} …` 自动放行（ApprovedForSession）"


def approval_decision(command: str, policy: str) -> str:
    """把「会话缓存」叠加到 decide() 之上：缓存命中就直接 auto-approve（不打扰用户）。"""
    if prefix_of(command) in SESSION_ALLOW:
        return "auto-approve（会话缓存命中）"
    return decide(command, policy)


TOOLS = [{
    "name": "shell",
    "description": "Run a shell command; it is gated by the approval policy first.",
    "parameters": {"type": "object",
                   "properties": {"command": {"type": "string"}}, "required": ["command"]},
}]


# ═══════════════════════════════════════════════════════════
#  FROM s01（搬运）：回合循环。工具 handler 现在先过审批门 gated_shell。
# ═══════════════════════════════════════════════════════════

def run_turn(messages: list[dict], policy: str, ask_user) -> None:
    while True:
        resp = model.respond(messages, tools=TOOLS, system=SYSTEM)
        messages += resp.output_items
        if not resp.tool_calls:
            if resp.text:
                print(f"\n\033[32m{resp.text}\033[0m")
            return
        for tc in resp.tool_calls:
            print(f"\033[33m> {tc.name} {tc.arguments}\033[0m")
            command = tc.arguments.get("command", "")
            output = gated_shell(command, policy, ask_user)
            print(str(output)[:300])
            messages.append(tool_output_item(tc.call_id, output))


# ═══════════════════════════════════════════════════════════
#  --demo：不依赖模型，直观演示「同一条命令在不同策略下的决定」
#  危险命令一律由「模拟用户」拒绝，证明它没有被执行。
# ═══════════════════════════════════════════════════════════

def _deny_dangerous(command: str, policy: str) -> bool:
    """模拟用户：被问到时一律拒绝危险命令（所以它不会被执行）。"""
    print(f"    \033[31m? 策略[{policy}] 问用户是否执行 [{command}] → 用户拒绝\033[0m")
    return False


def demo() -> None:
    safe_cmd = "echo hello from codex"
    risky_cmd = "rm -rf /"
    print("审批门 decide(command, policy) → approve | ask | reject\n")
    print(f"  安全命令 : {safe_cmd}")
    print(f"  危险命令 : {risky_cmd}   (is_dangerous={is_dangerous(risky_cmd)})\n")

    for policy in ("untrusted", "on-request", "never"):
        print(f"\033[36m── 策略 = {policy} ─────────────────────────────\033[0m")
        for cmd in (safe_cmd, risky_cmd):
            verdict = decide(cmd, policy)
            print(f"  decide({cmd!r:28}) = \033[1m{verdict}\033[0m")
        # 实际「过门」执行一遍危险命令，证明它在每档下都没真正跑。
        out = gated_shell(risky_cmd, policy, _deny_dangerous)
        print(f"  执行危险命令的结果: {out}\n")

    print("结论：untrusted 连安全命令也只放只读白名单；on-request 危险才问；")
    print("      never 不问用户、危险命令直接拒。三档都没真正执行 `rm -rf /`。")
    print("      （注意：审批之外还有沙箱 s05 —— 即使被批准，命令仍可被关进内核沙箱。）")

    print("\n生产级·审批带记忆（ReviewDecision，不是一个 bool）——")
    SESSION_ALLOW.clear()
    print(" (a) 会话缓存 ApprovedForSession：批准一次，同前缀以后自动放行：")
    print("     用户对 `cargo build --release` 选 ApprovedForSession →", remember_prefix("cargo build --release"))
    print("     下次 `cargo test` 的裁决 →", approval_decision("cargo test", "untrusted"), "（没再打扰用户）")
    print(" (b) 刹车 BANNED_PREFIX：解释器/shell 永远不许被学成永久放行：")
    print("    ", remember_prefix("python deploy.py"))
    print("    ", remember_prefix("git push --force"))
    print(" (c) fail-closed：审批 TimedOut（自动评审超时）→ 默认按 Denied 处理（呼应 s14 Guardian）。")
    SESSION_ALLOW.clear()


if __name__ == "__main__":
    if "--demo" in sys.argv:
        demo()
        sys.exit(0)

    policy = "on-request"
    print(f"s04: Approval（策略={policy}；输入 q 退出。危险命令会问你 y/N）\n")

    def ask(cmd: str, pol: str) -> bool:
        return input(f"\033[33m[策略 {pol}] 批准执行 [{cmd}] ? (y/N) \033[0m"
                     ).strip().lower() == "y"

    history: list[dict] = []
    while True:
        try:
            query = input("\033[36ms04 >> \033[0m")
        except (EOFError, KeyboardInterrupt):
            break
        if query.strip().lower() in ("q", "exit", ""):
            break
        history.append(user_item(query))
        run_turn(history, policy, ask)
        print()
