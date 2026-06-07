#!/usr/bin/env python3
"""
s10: SQ/EQ Protocol — 把「提交」和「事件」拆成两个队列。

运行:
  python s10_sq_eq_protocol/code.py --demo    # 演示事件流出 + 审批流入
  python s10_sq_eq_protocol/code.py           # 交互模式

本章 = s01 的回合循环 + shell 工具（搬运）
     + 新增：不再「调函数拿返回值」，而是 Session **产出事件**（Event Queue），
       前端**提交操作**（Submission Queue）。两者解耦——这是 Codex 架构的脊梁。

为什么这是 Codex 的脊梁？因为同一个 Session 可以喂给 TUI / `codex exec` / app-server
三种前端，而且能在「回合进行中」插入审批、打断、追加输入。见 README「🆚」。

忠实对应 codex-rs/protocol/src/protocol.rs 的 Op（提交）与 EventMsg（事件）两套枚举。
本章用 Python 生成器把这件事讲清楚：
    yield  = 事件流「出」 (EQ)
    .send()= 操作流「入」 (SQ，比如审批决定)
"""

import subprocess
import sys
from dataclasses import dataclass, field
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
from codexlib import Model, user_item, tool_output_item  # noqa: E402

WORKDIR = Path.cwd()
SYSTEM = f"You are Codex at {WORKDIR}. Use the shell tool. Act, don't explain."
model = Model()


# ═══════════════════════════════════════════════════════════
#  协议类型：Op（提交队列输入）与 Event（事件队列输出）
#  对应 protocol.rs 里的 Op / EventMsg（这里只取最小子集）
# ═══════════════════════════════════════════════════════════

@dataclass
class Op:
    """Submission Queue 的一条提交。"""
    kind: str                     # "user_input" | "exec_approval" | "interrupt"
    payload: dict = field(default_factory=dict)


@dataclass
class Event:
    """Event Queue 的一条事件。"""
    kind: str                     # "turn_started" | "agent_message" | "exec_begin" ...
    payload: dict = field(default_factory=dict)


def ev(kind: str, **payload) -> Event:
    return Event(kind, payload)


# ═══════════════════════════════════════════════════════════
#  FROM s01（搬运）：shell 工具
# ═══════════════════════════════════════════════════════════

def run_shell(command: str) -> str:
    try:
        r = subprocess.run(command, shell=True, cwd=WORKDIR, capture_output=True,
                           text=True, encoding="utf-8", errors="replace", timeout=120)
        return ((r.stdout + r.stderr).strip() or "(no output)")[:50000]
    except (subprocess.TimeoutExpired, OSError) as e:
        return f"Error: {e}"


TOOLS = [{"name": "shell", "description": "Run a shell command.",
          "parameters": {"type": "object",
                         "properties": {"command": {"type": "string"}},
                         "required": ["command"]}}]


# ═══════════════════════════════════════════════════════════
#  Session：回合循环改写成「产出事件」的生成器
#
#  关键变化（对比 s01）：
#    s01: output = run_shell(cmd)            —— 直接拿返回值
#    s10: decision = yield ev("exec_approval_request", ...)  —— 事件流出、决定流入
#
#  yield 出去的是 Event（EQ）；通过 .send() 灌回来的是 Op 的决定（SQ）。
#  正是这层解耦，让「回合进行中审批」成为可能。
# ═══════════════════════════════════════════════════════════

def run_session(messages: list[dict]):
    yield ev("turn_started")
    while True:
        resp = model.respond(messages, tools=TOOLS, system=SYSTEM)
        messages += resp.output_items

        if resp.text:
            yield ev("agent_message", text=resp.text)

        if not resp.tool_calls:
            yield ev("turn_complete")
            return

        for tc in resp.tool_calls:
            command = tc.arguments.get("command", "")
            # 不直接执行，而是先「请求审批」——把决定权交给前端（通过 SQ 灌回）。
            decision = yield ev("exec_approval_request", command=command)
            if decision != "approved":
                output = "(被用户拒绝，未执行)"
            else:
                yield ev("exec_begin", command=command)
                output = run_shell(command)
                yield ev("exec_end", command=command, output=output)
            messages.append(tool_output_item(tc.call_id, output))


# ═══════════════════════════════════════════════════════════
#  前端：消费 EQ、向 SQ 提交决定
#  approve_fn 决定如何回应审批请求（demo 里自动批准；交互里问用户）
# ═══════════════════════════════════════════════════════════

def drive(messages: list[dict], approve_fn) -> None:
    gen = run_session(messages)
    event = next(gen)
    while True:
        render(event)
        try:
            if event.kind == "exec_approval_request":
                # 前端构造一条 Op 提交回去（SQ）；这里只需把决定 send 进生成器。
                op = Op("exec_approval", {"decision": approve_fn(event.payload["command"])})
                event = gen.send(op.payload["decision"])
            else:
                event = next(gen)
        except StopIteration:
            return


def render(event: Event) -> None:
    icon = {"turn_started": "▶", "agent_message": "💬", "exec_approval_request": "❓",
            "exec_begin": "⏵", "exec_end": "✓", "turn_complete": "■"}.get(event.kind, "·")
    detail = event.payload.get("command") or event.payload.get("text") or ""
    print(f"\033[90m[EQ]\033[0m {icon} {event.kind:22} {str(detail)[:120]}")
    if event.kind == "exec_end":
        print(f"       output: {str(event.payload.get('output',''))[:160]}")


if __name__ == "__main__":
    if "--demo" in sys.argv:
        print("s10 demo：事件从 Session 流出(EQ)，审批决定灌回(SQ)\n")
        drive([user_item("执行 `echo SQ/EQ works`")], approve_fn=lambda cmd: "approved")
        print("\n（注意：审批请求是一个事件、批准是一次提交——两条独立的队列。）")
        sys.exit(0)

    print("s10: SQ/EQ（输入 q 退出；每条命令都会问你批不批）\n")

    def ask(cmd: str) -> str:
        return "approved" if input(f"\033[33m批准执行 [{cmd}] ? (y/N) \033[0m").lower() == "y" \
            else "denied"

    history: list[dict] = []
    while True:
        try:
            query = input("\033[36ms10 >> \033[0m")
        except (EOFError, KeyboardInterrupt):
            break
        if query.strip().lower() in ("q", "exit", ""):
            break
        history.append(user_item(query))
        drive(history, approve_fn=ask)
        print()
