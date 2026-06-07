#!/usr/bin/env python3
"""
s11: Frontends — 前端只是事件流的消费者（TUI + exec）。

运行:
  python s11_frontends/code.py --demo        # 离线：同一条事件流，先 TUI 渲染、再 JSONL 输出
  python s11_frontends/code.py --json "..."   # 无头 exec：prompt 当参数，输出 JSONL（每行一个 JSON）
  python s11_frontends/code.py --exec "..."   # 无头 exec：人类可读文本（成败用退出码表达）
  echo "prompt" | python s11_frontends/code.py --exec   # 从 stdin 读 prompt
  python s11_frontends/code.py --plain        # 交互 TUI，但换「纯文本」渲染器（演示热插拔）
  python s11_frontends/code.py                # 交互 TUI（盒子渲染器；输入 q 退出）

本章 = s01 的回合循环（搬运）改造成产出【一条 typed 事件流】的 core
     + 新增：core 只 yield 事件，前端只是【消费者】。本章一次给出两个消费者——
         · TUI 渲染器：widgets as functions（事件类型→渲染函数的派发表），
                       两个可【热插拔】的渲染器（盒子 / 纯文本），换皮不碰 core；
         · headless exec：同一条流渲染成 人类文本 或 JSONL（--json，每行一个 JSON），
                          用退出码表态，给 CI / 脚本 / 管道用。
       关键证据：--demo 用【同一条 core 事件流】喂给两个消费者，输出形态天差地别，
                而 run_turn_events（core）一行都不用改。

忠实对应（事实依据，按需去读）:
  · codex-rs/tui/        消费者①：ratatui 前端。app.rs/chatwidget.rs 收事件不含业务；
                         history_cell/ 每类条目一个 widget，实现 HistoryCell::display_lines。
  · codex-rs/exec/src/   消费者②：无头 exec。event_processor.rs 是 trait，human/jsonl 两实现；
                         exec_events.rs 是 JSONL schema；lib.rs 末尾 error_seen → exit(1)。
  · codex-rs/app-server/ 消费者③：同一套事件发成 JSON-RPC ServerNotification，喂 IDE/cloud。
"""

import contextlib
import json
import subprocess
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
from codexlib import Model, user_item, tool_output_item  # noqa: E402

WORKDIR = Path.cwd()
SYSTEM = f"You are Codex at {WORKDIR}. Use the shell tool. Act, don't explain."

# 无头模式的铁律：stdout 只能有「真正的输出」（--json 时即纯 JSONL），其余一律走 stderr，
# 否则 `... | jq` 会被杂音噎死。codexlib 的启动横幅默认打到 stdout，这里改道到 stderr——
# 真 codex exec 同样把配置摘要/告警写 stderr、只把结果写 stdout。
with contextlib.redirect_stdout(sys.stderr):
    model = Model()


# ═══════════════════════════════════════════════════════════
#  FROM s01（搬运）：shell 工具 + 回合循环
#
#  唯一改动：原来 run_turn 直接 print（把「产生什么」和「怎么显示」焊死了）。
#  这里把 print 全部撤掉，循环改成【yield 一条 typed 事件流】——core 从此对
#  「前端长什么样」一无所知。这一步剥离，就是「一个 core，多个前端」的全部前提。
# ═══════════════════════════════════════════════════════════

def run_shell(command: str):
    """返回 (aggregated_output, exit_code)。exit_code 是无头模式的命脉（CI 据此判断成败）。"""
    try:
        r = subprocess.run(command, shell=True, cwd=WORKDIR, capture_output=True,
                           text=True, encoding="utf-8", errors="replace", timeout=120)
        return ((r.stdout + r.stderr).strip() or "(no output)")[:50000], r.returncode
    except subprocess.TimeoutExpired:
        return "Error: timeout (120s)", 124
    except OSError as e:
        return f"Error: {e}", 1


TOOLS = [{"name": "shell", "description": "Run a shell command.",
          "parameters": {"type": "object",
                         "properties": {"command": {"type": "string"}},
                         "required": ["command"]}}]


# ═══════════════════════════════════════════════════════════
#  NEW ①：core —— 把一个回合跑成一条 typed 事件流
#
#  事件分类对齐真 codex 的 exec_events.rs：thread.started / turn.started /
#  item.started / item.completed / turn.completed|failed。每个「item」有
#  started / completed 两拍。无头下没人审批，命令直接执行（真 codex 靠 s04
#  审批策略 + s05 内核沙箱兜底）。
#
#  注意：这条流不分「给 TUI 用」还是「给 exec 用」——它就是一条流。
#  下面两个消费者读的是同一批字典。这就是本章要证明的事。
# ═══════════════════════════════════════════════════════════

def run_turn_events(prompt: str):
    """core：一个回合 → 逐个 yield dict 事件。它不知道、也不关心谁在消费。"""
    messages = [user_item(prompt)]
    yield {"type": "thread.started", "thread_id": "thr_demo"}
    yield {"type": "turn.started"}

    turn_failed = False
    while True:
        resp = model.respond(messages, tools=TOOLS, system=SYSTEM)
        messages += resp.output_items

        if resp.text:
            yield {"type": "item.completed",
                   "item": {"item_type": "agent_message", "text": resp.text}}

        if not resp.tool_calls:
            break

        for tc in resp.tool_calls:
            command = tc.arguments.get("command", "")
            yield {"type": "item.started",
                   "item": {"item_type": "command_execution",
                            "command": command, "status": "in_progress"}}
            output, code = run_shell(command)
            if code != 0:
                turn_failed = True
            yield {"type": "item.completed",
                   "item": {"item_type": "command_execution",
                            "command": command, "aggregated_output": output,
                            "exit_code": code,
                            "status": "completed" if code == 0 else "failed"}}
            messages.append(tool_output_item(tc.call_id, output))

    if turn_failed:
        yield {"type": "turn.failed", "error": {"message": "a command exited non-zero"}}
    else:
        yield {"type": "turn.completed", "usage": {"input_tokens": 0, "output_tokens": 0}}


# ═══════════════════════════════════════════════════════════
#  NEW ②：消费者 A —— TUI 渲染器（widgets as functions）
#
#  真 codex 的 history_cell/ 里每类条目是一个 widget（ExecCell/MessageCell…），
#  都实现 HistoryCell::display_lines(width) -> Vec<Line>。这里把「widget」退化成
#  最朴素的形态：一个纯函数 (event) -> list[str]（要打印的行）。每种事件一个独立
#  小函数，互不知道彼此——可单独增删改。
# ═══════════════════════════════════════════════════════════

def w_thread_started(ev: dict) -> list[str]:
    return [f"\033[90m── thread {ev['thread_id']} ──\033[0m"]


def w_turn_started(_ev: dict) -> list[str]:
    return ["\033[90m── turn started ──\033[0m"]


def w_agent_message(item: dict) -> list[str]:
    # 一个「消息气泡」widget：把多行文本缩进进一个简单框里。
    lines = item["text"].splitlines() or [""]
    body = [f"\033[36m│\033[0m {ln}" for ln in lines]
    return ["\033[36m╭─ codex ─────\033[0m", *body, "\033[36m╰─────────────\033[0m"]


def w_exec_begin(item: dict) -> list[str]:
    return [f"\033[35m$ exec\033[0m \033[1m{item['command']}\033[0m  \033[90m(running…)\033[0m"]


def w_exec_end(item: dict) -> list[str]:
    ok = item["exit_code"] == 0
    badge = "\033[32m✓ ok\033[0m" if ok else f"\033[31m✗ exit {item['exit_code']}\033[0m"
    out = item["aggregated_output"].splitlines()[:4]
    return [f"  {badge}", *[f"  \033[90m{ln}\033[0m" for ln in out]]


def w_turn_complete(_ev: dict) -> list[str]:
    return ["\033[90m── turn complete ──\033[0m", ""]


def w_turn_failed(ev: dict) -> list[str]:
    return [f"\033[31m── turn failed: {ev['error']['message']} ──\033[0m", ""]


# 这张「派发表」就是 TUI 前端的全部业务：事件 → 渲染它的 widget 函数。
# item.* 这两类要看 item_type 再细分，所以单独有个 _exec/_message 入口。
# 想支持一种新事件？加一行。完全不碰 core。
def _w_item_started(ev: dict) -> list[str]:
    item = ev["item"]
    return w_exec_begin(item) if item["item_type"] == "command_execution" else []


def _w_item_completed(ev: dict) -> list[str]:
    item = ev["item"]
    if item["item_type"] == "command_execution":
        return w_exec_end(item)
    if item["item_type"] == "agent_message":
        return w_agent_message(item)
    return []


WIDGETS = {
    "thread.started": w_thread_started,
    "turn.started": w_turn_started,
    "item.started": _w_item_started,
    "item.completed": _w_item_completed,
    "turn.completed": w_turn_complete,
    "turn.failed": w_turn_failed,
}


class BoxRenderer:
    """默认 TUI 渲染器：查 WIDGETS 表，把每个事件渲染成（可能多行的）带色文本。"""
    name = "box"

    def render(self, event: dict) -> None:
        widget = WIDGETS.get(event["type"])
        lines = widget(event) if widget else [f"\033[90m?? {event['type']}\033[0m"]
        for ln in lines:
            print(ln)


class PlainRenderer:
    """另一个 TUI 渲染器：无视 widget 表，每个事件压成一行朴素文本（适合日志/无色终端）。"""
    name = "plain"

    def render(self, event: dict) -> None:
        item = event.get("item", {})
        detail = item.get("command") or item.get("text") or ""
        if item.get("item_type") == "command_execution" and "exit_code" in item:
            detail = f"exit={item['exit_code']} {item['aggregated_output'][:60]}"
        print(f"[{event['type']}] {str(detail)[:100]}".rstrip())


# ═══════════════════════════════════════════════════════════
#  NEW ③：消费者 B —— headless exec（同一条流，人类文本 / JSONL）
#
#  对应 exec/src 里 EventProcessor trait 的两个实现：JsonlProcessor ≈
#  EventProcessorWithJsonOutput（--json）、HumanProcessor ≈ ...WithHumanOutput。
#  process(event) 返回是否「致命错误」，最终汇总成退出码。注意它和上面 TUI
#  渲染器读的是同一批事件——只是渲染目标不同。
# ═══════════════════════════════════════════════════════════

class JsonlProcessor:
    """--json：每个事件 println! 一行 JSON。给脚本/CI 用 jq 之类 parse。"""

    def process(self, event: dict) -> bool:
        print(json.dumps(event, ensure_ascii=False))
        return event["type"] == "turn.failed"


class HumanProcessor:
    """默认：人类可读文本。同一条流，挑关键事件、上色、收尾打印最终消息。"""

    def process(self, event: dict) -> bool:
        t, item = event["type"], event.get("item", {})
        if t == "item.started" and item.get("item_type") == "command_execution":
            print(f"\033[35mexec\033[0m  \033[1m{item['command']}\033[0m")
        elif t == "item.completed" and item.get("item_type") == "command_execution":
            tag = "\033[32m✓\033[0m" if item["status"] == "completed" else "\033[31m✗\033[0m"
            print(f"  {tag} exit={item['exit_code']}  {item['aggregated_output'][:200]}")
        elif t == "item.completed" and item.get("item_type") == "agent_message":
            print(f"\033[35mcodex\033[0m\n{item['text']}")
        elif t == "turn.failed":
            print(f"\033[31mturn failed:\033[0m {event['error']['message']}")
            return True
        return False


# ═══════════════════════════════════════════════════════════
#  NEW ④：两个驱动器 —— 都只是「消费 core 的事件流」
#
#  drive_tui 和 run_exec 形状几乎一模一样（都是 for event in 流：交给消费者）。
#  这种对称不是巧合，正是「core 与前端解耦」想达到的效果。run_exec 末尾对应
#  lib.rs 的 `if error_seen { std::process::exit(1); }`。
# ═══════════════════════════════════════════════════════════

def drive_tui(prompt: str, renderer) -> None:
    for event in run_turn_events(prompt):       # core 不知道 renderer 是谁
        renderer.render(event)


def run_exec(prompt: str, json_mode: bool) -> int:
    processor = JsonlProcessor() if json_mode else HumanProcessor()
    error_seen = False
    for event in run_turn_events(prompt):       # 同一条 core 流，换个消费者
        if processor.process(event):
            error_seen = True
    return 1 if error_seen else 0


def read_prompt_from_argv_or_stdin() -> str | None:
    flags = ("--demo", "--json", "--exec", "--plain")
    args = [a for a in sys.argv[1:] if a not in flags]
    if args:
        return " ".join(args)
    if not sys.stdin.isatty():                  # 有人用管道喂进来
        piped = sys.stdin.read().strip()
        if piped:
            return piped
    return None


if __name__ == "__main__":
    # ── --demo：同一条 core 事件流，喂给两个不同消费者 ──────────────
    if "--demo" in sys.argv:
        # 把整条流先收集成列表，强调「同一批事件」被两个消费者分别消费。
        stream = list(run_turn_events("执行 `echo hello from codex` 并报告结果"))

        print("\n\033[1m[消费者 A] TUI 渲染器（box）——同一条流画成终端界面\033[0m")
        for ev in stream:
            BoxRenderer().render(ev)

        print("\n\033[1m[消费者 B] headless exec（--json）——同一条流发成 JSONL\033[0m")
        jsonl = JsonlProcessor()
        error_seen = any(jsonl.process(ev) for ev in stream)

        code = 1 if error_seen else 0
        print(f"\n\033[90m# core 一行没改，只是换了消费者。exec 退出码 = {code}"
              f"（0=成功，1=回合失败——给 CI 的信号）\033[0m")
        sys.exit(code)

    # ── --exec / --json：无头模式（非交互），有 prompt 就跑完退出 ────
    if "--exec" in sys.argv or "--json" in sys.argv:
        prompt = read_prompt_from_argv_or_stdin()
        if prompt is not None:
            sys.exit(run_exec(prompt, json_mode="--json" in sys.argv))
        print("用法：--exec/--json 需要一个 prompt（argv 或 stdin）", file=sys.stderr)
        sys.exit(2)

    # ── 否则：交互式 TUI（默认 box，--plain 换渲染器演示热插拔）─────
    renderer = PlainRenderer() if "--plain" in sys.argv else BoxRenderer()
    print(f"s11: Frontends — TUI 薄渲染器（渲染器=[{renderer.name}]；输入 q 退出）\n")
    while True:
        try:
            q = input("\033[36ms11 >> \033[0m")
        except (EOFError, KeyboardInterrupt):
            break
        if q.strip().lower() in ("q", "exit", ""):
            break
        drive_tui(q, renderer)
        print()
