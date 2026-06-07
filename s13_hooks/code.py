#!/usr/bin/env python3
"""
s13: Hooks — 在回合的关键时刻挂钩子，不改 core 就能扩展行为。

运行:
  python s13_hooks/code.py --demo    # 不需要模型：注册一个会 VETO `rm` 的 pre_tool 钩子，跑一个 canned 回合
  python s13_hooks/code.py           # 交互模式：同样的钩子在每次真实回合里生效

本章 = s01 的回合循环 + shell 工具（搬运）
     + 新增：一个钩子注册表 HOOKS，四个触发点：
         pre_turn   回合开始
         pre_tool   工具调用前 —— 可以 VETO（否决）这次调用，或改写它的参数
         post_tool  工具调用后
         post_turn  回合结束
       钩子就是按事件名注册的普通可调用对象，在循环里到点就 fire。

忠实对应 codex-rs/hooks crate：
  - 真实的 10 个事件名（HOOK_EVENT_NAMES）：PreToolUse / PostToolUse / Stop / SessionStart ...
        (codex-rs/hooks/src/lib.rs:19)
  - PreToolUse 可否决 + 可改写：PreToolUseOutcome { should_block, block_reason, updated_input }
        (codex-rs/hooks/src/events/pre_tool_use.rs:37)
  - 否决信号：钩子退出码 2，或输出 {"permissionDecision":"deny"} → should_block=true
        (pre_tool_use.rs: parse_completed)
  - 注册表分发：Hooks::dispatch 顺序执行匹配的钩子，任一要求中止则停
        (codex-rs/hooks/src/registry.rs:94)

与 Claude Code 的关系：两边都有 hooks（≈）。差别在 Codex 的 hooks 与审批(s04)、
Guardian(s14) 和事件协议(s10)长在同一套体系里。见 README「🆚」。
"""

import subprocess
import sys
import threading
import time
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
from codexlib import Model, user_item, tool_output_item  # noqa: E402

WORKDIR = Path.cwd()
SYSTEM = f"You are Codex at {WORKDIR}. Use the shell tool. Act, don't explain."
model = Model()


# ═══════════════════════════════════════════════════════════
#  NEW in s13 ①：钩子注册表 + 四个触发点
#
#  钩子按「事件名」分桶。每个钩子是一个普通可调用对象，收一个 ctx(dict)。
#  pre_tool 钩子的返回值有特殊含义（可否决/可改写），其余事件忽略返回值。
# ═══════════════════════════════════════════════════════════

HOOKS: dict[str, list] = {
    "pre_turn": [],
    "pre_tool": [],
    "post_tool": [],
    "post_turn": [],
}

# 真源码的 10 个事件名，仅供展示「本章四点是它的子集」。
REAL_HOOK_EVENTS = [
    "PreToolUse", "PermissionRequest", "PostToolUse", "PreCompact", "PostCompact",
    "SessionStart", "UserPromptSubmit", "SubagentStart", "SubagentStop", "Stop",
]


def register(event: str, fn) -> None:
    """把一个钩子注册到某个触发点。"""
    if event not in HOOKS:
        raise ValueError(f"未知触发点 {event!r}（可选：{list(HOOKS)}）")
    HOOKS[event].append(fn)


def fire(event: str, ctx: dict) -> dict:
    """触发某个事件下的所有钩子，顺序执行。

    对 pre_tool：任一钩子返回 {"block": True, ...} 即否决（停在第一个否决，
                 对应真源码 dispatch 遇到 should_abort 就 break）；
                 返回 {"command": ...} 则改写后续要执行的命令（updated_input）。
    其余事件：钩子只做副作用（打日志等），返回值忽略。
    """
    result: dict = {}
    for fn in HOOKS.get(event, []):
        out = fn(ctx) or {}
        if event == "pre_tool":
            if out.get("block"):
                return {"block": True, "reason": out.get("reason", "blocked by hook")}
            if "command" in out:           # 钩子改写了参数 → 后续钩子/执行都用新值
                ctx = {**ctx, "command": out["command"]}
                result["command"] = out["command"]
    return result


# ═══════════════════════════════════════════════════════════
#  FROM s01（搬运）：shell 工具
# ═══════════════════════════════════════════════════════════

def run_shell(command: str) -> str:
    try:
        r = subprocess.run(command, shell=True, cwd=WORKDIR, capture_output=True,
                           text=True, encoding="utf-8", errors="replace", timeout=120)
        out = (r.stdout + r.stderr).strip()
        tag = "" if r.returncode == 0 else f"[exit {r.returncode}] "
        return tag + (out[:50000] if out else "(no output)")
    except (subprocess.TimeoutExpired, OSError) as e:
        return f"Error: {e}"


TOOLS = [{"name": "shell", "description": "Run a shell command.",
          "parameters": {"type": "object",
                         "properties": {"command": {"type": "string"}},
                         "required": ["command"]}}]
HANDLERS = {"shell": run_shell}


# ═══════════════════════════════════════════════════════════
#  NEW in s13 ②：把四个触发点织进回合循环
#
#  对比 s01：循环骨架没变，只是在四个位置插了 fire(...)：
#    回合开始 → pre_turn
#    每次工具调用前 → pre_tool（可否决 / 可改写）
#    每次工具调用后 → post_tool
#    回合结束 → post_turn
# ═══════════════════════════════════════════════════════════

def execute_tool_call(call_id: str, name: str, command: str, messages: list[dict]) -> None:
    """对一次工具调用走完 pre_tool（否决/改写）→ 执行 → post_tool。run_turn 与 demo 共用。"""
    gate = fire("pre_tool", {"tool": name, "command": command})

    if gate.get("block"):
        # 钩子否决了这次工具调用：不执行，把否决理由作为工具结果回灌给模型。
        reason = gate["reason"]
        print(f"\033[31m⛔ pre_tool 否决 {name}: {reason}\033[0m")
        messages.append(tool_output_item(call_id, f"[blocked by hook] {reason}"))
        return

    if "command" in gate:                   # 钩子改写了命令
        print(f"\033[35m✎ pre_tool 改写命令 → {gate['command']}\033[0m")
        command = gate["command"]

    print(f"\033[33m> {name} {{'command': {command!r}}}\033[0m")
    output = HANDLERS[name](command=command)
    print(str(output)[:300])
    fire("post_tool", {"tool": name, "command": command, "output": output})
    messages.append(tool_output_item(call_id, output))


def run_turn(messages: list[dict]) -> None:
    fire("pre_turn", {"messages": messages})
    while True:
        resp = model.respond(messages, tools=TOOLS, system=SYSTEM)
        messages += resp.output_items
        if not resp.tool_calls:
            if resp.text:
                print(f"\n\033[32m{resp.text}\033[0m")
            fire("post_turn", {"messages": messages})
            return
        for tc in resp.tool_calls:
            execute_tool_call(tc.call_id, tc.name, tc.arguments.get("command", ""), messages)


# ═══════════════════════════════════════════════════════════
#  示例钩子（demo 与交互都注册它们）
# ═══════════════════════════════════════════════════════════

def block_rm(ctx: dict) -> dict:
    """pre_tool 钩子：任何含 `rm` 的命令都否决。"""
    if "rm" in (ctx.get("command") or "").split():
        return {"block": True, "reason": "policy: `rm` is not allowed by the block_rm hook"}
    return {}


def log_post_turn(ctx: dict) -> dict:
    """post_turn 钩子：回合结束打一行日志。"""
    n = len(ctx.get("messages", []))
    print(f"\033[90m[hook] post_turn: 对话现在有 {n} 个 item\033[0m")
    return {}


def install_demo_hooks() -> None:
    register("pre_tool", block_rm)
    register("post_turn", log_post_turn)


# ═══════════════════════════════════════════════════════════
#  NEW in s13 ③（生产级）：钩子是外部命令——会挂、会崩、可能恶意
#
#  教学版的钩子是进程内 Python 函数，乖。真 Codex 的钩子是**用户机器上的任意外部命令**：
#  一个藏在项目目录里的恶意 PreToolUse 钩子，能在每次工具调用时执行任意代码。两道关：
#    ① 信任：只跑「可信」钩子——真 Codex 比对 trusted_hash 的 SHA-256（config_rules.rs），
#       哈希不匹配就不执行（可用 bypass_hook_trust 跳过，registry.rs:33）。
#    ② 超时：钩子套 timeout（declarations.rs:69 timeout_sec），挂死的钩子不能冻住每次调用。
#  对一个**安全**钩子（pre_tool）：超时/出错一律 fail-closed = 当作否决（宁可拦错，不可漏放）。
# ═══════════════════════════════════════════════════════════

TRUSTED_HOOKS: set = set()    # 只有「可信」(哈希匹配)的钩子才会被执行


def run_hook_safely(fn, ctx: dict, timeout_s: float = 0.1) -> dict:
    """带信任校验 + 超时的钩子执行。对安全钩子，超时即 fail-closed 当作否决。"""
    if fn not in TRUSTED_HOOKS:                       # ① 信任：未签名/哈希不符 → 不执行
        return {"_skipped": f"untrusted hook {getattr(fn, '__name__', fn)} 未执行（哈希不匹配）"}
    box: dict = {}
    worker = threading.Thread(target=lambda: box.__setitem__("r", fn(ctx) or {}), daemon=True)
    worker.start()
    worker.join(timeout_s)
    if worker.is_alive():                             # ② 超时 → fail-closed（当否决）
        return {"block": True, "reason": f"hook 超时（>{timeout_s}s），fail-closed 当作否决"}
    return box.get("r", {})


def _slow_hook(ctx: dict) -> dict:
    time.sleep(0.5)                                    # 模拟挂死的钩子
    return {}


def _evil_hook(ctx: dict) -> dict:
    return {"block": False}                            # 假装人畜无害，但它 untrusted


def demo_production() -> None:
    print("\n生产级：钩子是外部命令——会挂、会崩、可能恶意。两道关：信任 + 超时")
    TRUSTED_HOOKS.clear()
    TRUSTED_HOOKS.add(block_rm)                         # 只有 block_rm 可信
    print(" (a) 信任校验：哈希不匹配的钩子不执行（防项目里塞恶意钩子）：")
    print("     _evil_hook（untrusted）→", run_hook_safely(_evil_hook, {"command": "x"}))
    print(" (b) 超时 + fail-closed：挂死的安全钩子超时即当否决：")
    TRUSTED_HOOKS.add(_slow_hook)
    print("     _slow_hook（trusted，sleep 0.5s / 超时 0.1s）→",
          run_hook_safely(_slow_hook, {}, timeout_s=0.1))


# ═══════════════════════════════════════════════════════════
#  --demo：不依赖模型，跑一个 canned 回合（脚本化的两次工具调用），展示钩子 fire / veto
# ═══════════════════════════════════════════════════════════

def demo() -> None:
    install_demo_hooks()
    print("已注册钩子：pre_tool=block_rm（否决含 rm 的命令）, post_turn=log\n")
    print(f"真 Codex 的 10 个事件名：{REAL_HOOK_EVENTS}")
    print("本章只取其中四个触发点的最小子集：pre_turn / pre_tool / post_tool / post_turn\n")

    # canned 回合：模型先想跑安全命令(echo)，再想跑危险的 rm —— 走和 run_turn 完全一样的把关路径。
    canned = [("c1", "shell", "echo hooks are firing"),
              ("c2", "shell", "rm -rf build")]
    messages: list[dict] = [user_item("(canned turn)")]

    fire("pre_turn", {"messages": messages})
    for call_id, name, command in canned:
        execute_tool_call(call_id, name, command, messages)
    fire("post_turn", {"messages": messages})

    print("\n结论：`echo` 正常执行；`rm -rf build` 被 pre_tool 钩子在执行前否决，")
    print("      否决理由作为工具结果回灌给模型（模型据此换个法子）。`rm` 从未真正运行。")
    demo_production()


if __name__ == "__main__":
    if "--demo" in sys.argv:
        demo()
        sys.exit(0)

    install_demo_hooks()
    print("s13: Hooks（输入 q 退出；含 rm 的命令会被 pre_tool 钩子否决）\n")
    history: list[dict] = []
    while True:
        try:
            query = input("\033[36ms13 >> \033[0m")
        except (EOFError, KeyboardInterrupt):
            break
        if query.strip().lower() in ("q", "exit", ""):
            break
        history.append(user_item(query))
        run_turn(history)
        print()
