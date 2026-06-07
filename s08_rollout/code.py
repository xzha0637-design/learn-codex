#!/usr/bin/env python3
"""
s10: Rollout — 把整场对话「逐条落盘」，之后还能 resume 接着跑。

运行:
  python s10_rollout/code.py --demo    # 录一段 → 落盘 jsonl → 读回重建对话（离线，无需 key）
  python s10_rollout/code.py           # 交互模式：你说的每句、每次工具调用都被记进 rollout

本章 = s01 的回合循环 + shell 工具（搬运）
     + 新增：一个 RolloutRecorder。回合每产出一个 item（用户消息 / 模型消息 /
       function_call / function_call_output），就**追加一行 JSONL 到 rollout 文件**；
       再提供 resume(path)：把那些行**回放**成 messages 列表，继续会话。

为什么要落盘？s09 的压缩只是「修剪喂给模型的上下文」，会丢历史；rollout 是**完整、
持久、可审计**的记录——它同时驱动 `codex exec`（无头）、Codex Web（云端）和本地 resume。

教学简化：这里用 JSONL（一行一个 item，append-only，好读好讲）。
真 Codex 用 **SQLite（state.db）+ 冷文件 zstd 压缩**，见 README「深入」。
忠实对应 codex-rs/rollout/src/recorder.rs 的 RolloutRecorder。
"""

import json
import shutil
import subprocess
import sys
import time
from pathlib import Path

# 仓库根目录加入 import 路径，复用共享模块。
sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
from codexlib import Model, user_item, tool_output_item  # noqa: E402

WORKDIR = Path.cwd()
SYSTEM = (
    f"You are Codex, a coding agent running locally in {WORKDIR}. "
    "Use the shell tool to inspect and act on the workspace. Act, don't explain."
)

model = Model()


# ═══════════════════════════════════════════════════════════
#  FROM s01（搬运）：shell 工具 + 工具定义
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
#  NEW in s10：RolloutRecorder —— 每个 item 追加一行 JSONL
#
#  真 Codex 的 rollout 文件每行长这样（recorder.rs:JsonlWriter）：
#      {"timestamp": "...Z", "type": "response_item", "payload": {...}}
#  即 {时间戳} + {一个 RolloutItem}。RolloutItem 是个枚举，变体有：
#      session_meta / response_item / compacted / turn_context / event_msg
#  教学版只记两类：会话头（session_meta）+ 每个对话 item（response_item）。
#  关键性质：append-only（只追加不回写）+ 每行写完就 flush（崩溃也不丢已写部分）。
# ═══════════════════════════════════════════════════════════

class RolloutRecorder:
    """把会话逐条落盘成 JSONL。对应 codex-rs/rollout/src/recorder.rs。"""

    def __init__(self, path: Path, meta: dict | None = None):
        self.path = Path(path)
        self.path.parent.mkdir(parents=True, exist_ok=True)
        # 会话头：真 Codex 第一行就是 SessionMeta（conversation_id / cwd / git 信息…）。
        self._write_line("session_meta", meta or {"cwd": str(WORKDIR)})

    def _write_line(self, item_type: str, payload: dict) -> None:
        # 一行一个 RolloutLine：{timestamp, type, payload}。
        line = {
            "timestamp": time.strftime("%Y-%m-%dT%H:%M:%S", time.gmtime()) + "Z",
            "type": item_type,
            "payload": payload,
        }
        # append 模式 + 立即 flush：这正是 recorder.rs 里 write_all + flush 的语义。
        with self.path.open("a", encoding="utf-8") as f:
            f.write(json.dumps(line, ensure_ascii=False) + "\n")
            f.flush()

    def record_items(self, items: list[dict]) -> None:
        """把本回合新产出的对话 item 逐条落盘。

        注意：真 Codex 不是什么都记。它有一个「持久化策略」(policy.rs)，
        只记会进历史的 ResponseItem（message / function_call / function_call_output …）
        和少数关键 Event；纯增量 delta、审批请求等不落盘。教学版直接全记。
        """
        for it in items:
            self._write_line("response_item", it)


def resume(path: Path) -> list[dict]:
    """读回 rollout 文件，重建 messages 列表，用于继续会话。

    对应 recorder.rs 的 get_rollout_history / load_rollout_items：
    逐行解析，跳过 session_meta，把 response_item 的 payload 还原成对话 item。
    （真版还会处理 compacted / turn_context，并优雅跳过损坏行。）
    """
    messages: list[dict] = []
    for raw in Path(path).read_text(encoding="utf-8").splitlines():
        raw = raw.strip()
        if not raw:
            continue
        try:
            line = json.loads(raw)
        except json.JSONDecodeError:
            continue  # 损坏行：跳过而不是崩溃（真版同样宽容）
        if line.get("type") == "response_item":
            messages.append(line["payload"])
    return messages


# ═══════════════════════════════════════════════════════════
#  FROM s01（搬运，微调）：回合循环 —— 多接一个 recorder，边跑边录
# ═══════════════════════════════════════════════════════════

def run_turn(messages: list[dict], recorder: RolloutRecorder) -> None:
    while True:
        before = len(messages)
        resp = model.respond(messages, tools=TOOLS, system=SYSTEM)
        messages += resp.output_items
        # 落盘：模型本回合新增的 item（assistant 文本 + function_call）。
        recorder.record_items(messages[before:])

        if not resp.tool_calls:
            if resp.text:
                print(f"\n\033[32m{resp.text}\033[0m")
            return

        for tc in resp.tool_calls:
            print(f"\033[33m> {tc.name} {tc.arguments}\033[0m")
            handler = HANDLERS.get(tc.name)
            output = handler(**tc.arguments) if handler else f"unknown tool: {tc.name}"
            print(str(output)[:300])
            out_item = tool_output_item(tc.call_id, output)
            messages.append(out_item)
            recorder.record_items([out_item])  # 工具结果也落盘


# ═══════════════════════════════════════════════════════════
#  --demo：录一段 canned 回合 → 落盘 → resume 回放，全程离线
# ═══════════════════════════════════════════════════════════

def demo() -> None:
    ws = Path("_demo_workspace")
    ws.mkdir(exist_ok=True)
    rollout_path = ws / "rollout.jsonl"
    try:
        print("① 录制：跑一轮 `echo`，每个 item 追加到 rollout.jsonl ——\n")
        rec = RolloutRecorder(rollout_path, meta={"cwd": str(WORKDIR),
                                                  "conversation_id": "demo-0001"})
        first = user_item("执行 `echo hello from codex` 并告诉我结果")
        messages = [first]
        rec.record_items([first])      # 用户回合也落盘
        run_turn(messages, rec)

        print("\n② 落盘后的 rollout.jsonl（每行 = 一个 RolloutItem）——")
        for ln in rollout_path.read_text(encoding="utf-8").splitlines():
            print("  " + ln)

        print("\n③ resume：读回文件，重建对话 ——")
        restored = resume(rollout_path)
        print(f"  重建出 {len(restored)} 个对话 item：")
        for it in restored:
            kind = it.get("type")
            if kind == "message":
                print(f"    [{it.get('role')}] {str(it.get('content'))[:60]}")
            elif kind == "function_call":
                print(f"    [call] {it.get('name')} {it.get('arguments')}")
            elif kind == "function_call_output":
                print(f"    [output] {str(it.get('output'))[:60]}")

        # 证明 resume 出来的就是当时的 messages：可以直接接着跑（这里不再调模型，只比对）。
        assert restored == messages, "resume 应当无损还原对话"
        print("\n✓ resume 还原的 messages 与录制时完全一致——会话可从此继续。")
    finally:
        shutil.rmtree(ws, ignore_errors=True)  # 清理临时工作区


if __name__ == "__main__":
    if "--demo" in sys.argv:
        demo()
        sys.exit(0)

    print("s10: Rollout — 边跑边录，可 resume（输入 q 退出）\n")
    out = Path("_demo_workspace")
    out.mkdir(exist_ok=True)
    rec = RolloutRecorder(out / "session.jsonl",
                          meta={"cwd": str(WORKDIR), "conversation_id": "live"})
    print(f"\033[90m[rollout] 记录到 {rec.path}\033[0m")
    history: list[dict] = []
    while True:
        try:
            query = input("\033[36ms10 >> \033[0m")
        except (EOFError, KeyboardInterrupt):
            break
        if query.strip().lower() in ("q", "exit", ""):
            break
        u = user_item(query)
        history.append(u)
        rec.record_items([u])
        run_turn(history, rec)
        print()
