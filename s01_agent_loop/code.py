#!/usr/bin/env python3
"""
s01: Agent Loop — Codex 的回合循环。

运行:
  python s01_agent_loop/code.py            # 交互模式
  python s01_agent_loop/code.py --demo     # 跑一轮就退出（适合验证环境）

默认 backend=mock，无需任何 key。想接真模型见根目录 .env.example。

本章 = 一个 `while True` 循环 + 一个 shell 工具。
和 learn-claude-code 的 s01 几乎一样 —— 因为「回合循环」是所有 agent 的共同底座，
Agency 来自模型，不来自循环。差异从 s02、s03 才开始显现（见 README 的「🆚 与 Claude Code 的不同」）。
"""

import subprocess
import sys
from pathlib import Path

# 仓库根目录加入 import 路径，这样 `from codexlib import ...` 能找到共享模块。
sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
from codexlib import Model, user_item, tool_output_item  # noqa: E402

WORKDIR = Path.cwd()
SYSTEM = (
    f"You are Codex, a coding agent running locally in {WORKDIR}. "
    "Use the shell tool to inspect and act on the workspace. Act, don't explain."
)

model = Model()


# ═══════════════════════════════════════════════════════════
#  工具：shell —— Codex 的主力工具就是「跑命令」
#  注意这里没有危险命令黑名单。Claude Code 的 s01 有一个硬编码 blocklist；
#  Codex 的答案不是黑名单，而是「审批 + 操作系统级沙箱」(s04 / s05)。
#  s05 会把这个 run_shell 包进 macOS Seatbelt 沙箱里。
# ═══════════════════════════════════════════════════════════

def run_shell(command: str) -> str:
    try:
        r = subprocess.run(command, shell=True, cwd=WORKDIR,
                           capture_output=True, text=True,
                           encoding="utf-8", errors="replace", timeout=120)
        out = (r.stdout + r.stderr).strip()
        tag = "" if r.returncode == 0 else f"[exit {r.returncode}] "
        return tag + (out[:50000] if out else "(no output)")
    except subprocess.TimeoutExpired:
        return "Error: timeout (120s)"
    except OSError as e:
        return f"Error: {e}"


# 工具定义。注意 schema 是「扁平」放在工具对象里的（Responses API 风格），
# 这点 codexlib 会转换；对比 Claude 的 input_schema 字段名也不同。
TOOLS = [{
    "name": "shell",
    "description": "Run a shell command in the workspace and return combined stdout+stderr.",
    "parameters": {
        "type": "object",
        "properties": {"command": {"type": "string", "description": "the command line"}},
        "required": ["command"],
    },
}]

HANDLERS = {"shell": run_shell}


# ═══════════════════════════════════════════════════════════
#  回合循环 —— 整个 agent 的心脏
#
#  两个信号决定循环走向：
#    模型发起了 tool_call  → 执行工具 → 结果回灌 → 继续
#    模型没发起 tool_call  → 它说完了 → 退出
# ═══════════════════════════════════════════════════════════

def run_turn(messages: list[dict], *, model=model, max_steps: int = 40,
             cancelled=None) -> None:
    """回合循环 —— 整个 agent 的心脏。

    教学主线是这个 `while`；但一个**能上生产**的循环还有两道护栏（玩具版没有）：
      · max_steps：步数封顶。模型万一陷入「反复调工具」的死循环，不至于烧掉无限的钱与时间。
      · cancelled()：协作式取消。每步检查一次，用户一旦中断（真 Codex 的 Op::Interrupt，
        protocol.rs:450）就**干净地停下**（→ TurnAborted），而不是放任它跑完。
    把 `while True` 换成 `for step in range(max_steps)`，循环的"形状"没变，只是不再可能无限转。
    """
    for _step in range(max_steps):
        if cancelled is not None and cancelled():      # 协作式取消：每步问一句"该停了吗"
            print("\033[31m[interrupted] 用户中断，干净退出本回合\033[0m")
            return

        resp = model.respond(messages, tools=TOOLS, system=SYSTEM)
        messages += resp.output_items                  # 把本回合产出回灌进对话

        if not resp.tool_calls:
            if resp.text:
                print(f"\n\033[32m{resp.text}\033[0m")
            return

        for tc in resp.tool_calls:
            print(f"\033[33m> {tc.name} {tc.arguments}\033[0m")
            handler = HANDLERS.get(tc.name)
            output = handler(**tc.arguments) if handler else f"unknown tool: {tc.name}"
            print(str(output)[:300])
            messages.append(tool_output_item(tc.call_id, output))   # 独立 function_call_output item

    # 走到这里 = 触顶。失控的循环在这里被强制收尾，而不是继续烧下去。
    print(f"\033[31m[guard] 触及 max_steps={max_steps} 上限，强制收尾（防止失控的工具调用循环）\033[0m")


def demo_guards() -> None:
    """生产级：循环不能永远转——演示「步数封顶」与「协作式取消」两道护栏。"""
    from codexlib import ModelResponse, ToolCall  # noqa: PLC0415

    class StuckModel:
        """一个"卡住"的模型：永远要求再跑一次工具（模拟死循环 / 反复调失败工具）。"""
        def respond(self, messages, tools=None, system=None):
            return ModelResponse(
                text="", tool_calls=[ToolCall("c", "shell", {"command": "echo still going"})],
                output_items=[{"type": "function_call", "call_id": "c", "name": "shell",
                               "arguments": "{\"command\": \"echo still going\"}"}])

    print("\n生产级：循环不能永远转——封顶 + 可中断")
    print(" (a) 步数封顶 max_steps=3：卡住的模型被截停，而不是烧无限的钱——")
    run_turn([user_item("loop forever")], model=StuckModel(), max_steps=3)

    print("\n (b) 协作式取消：用户在第 3 步中断（真 Codex 的 Op::Interrupt）——")
    box = {"n": 0}
    def cancel() -> bool:
        box["n"] += 1
        return box["n"] > 2          # 第 3 次检查返回 True → 中断
    run_turn([user_item("loop forever")], model=StuckModel(), max_steps=99, cancelled=cancel)


if __name__ == "__main__":
    if "--demo" in sys.argv:
        print("s01 demo：跑一轮 `echo` ——\n")
        run_turn([user_item("执行 `echo hello from codex` 并告诉我结果")])
        demo_guards()
        sys.exit(0)

    print("s01: Agent Loop — Codex 回合循环（输入 q 退出）\n")
    history: list[dict] = []
    while True:
        try:
            query = input("\033[36ms01 >> \033[0m")
        except (EOFError, KeyboardInterrupt):
            break
        if query.strip().lower() in ("q", "exit", ""):
            break
        history.append(user_item(query))
        run_turn(history)
        print()
