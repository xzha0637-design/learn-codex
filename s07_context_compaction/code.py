#!/usr/bin/env python3
"""
s07: Context Compaction — 对话超预算时，把最旧的回合压成一条 [summary] 摘要。

运行:
  python s07_context_compaction/code.py            # 交互模式（聊到超预算自动压缩）
  python s07_context_compaction/code.py --demo      # 造一段长对话演示压缩 before/after

默认 backend=mock，无需任何 key；本章 --demo 完全离线、摘要用确定性启发式生成。

本章 = s01 搬运的回合循环 + 本章新增的「上下文压缩」。
新增机制全部内联在下方 `# ═══ NEW in s07 ═══` 横幅下，可单文件通读。
依据真源码：codex-rs/core/src/compact.rs（build_compacted_history / SUMMARY_PREFIX /
ContextWindowExceeded 触发；并提及 compact_remote.rs / compact_remote_v2.rs 这两个
服务端压缩变体）。
"""

import sys
from pathlib import Path

# 仓库根加入 import 路径，复用共享模型模块（mock 后端离线可跑）。
sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
from codexlib import Model, user_item, tool_output_item  # noqa: E402

# ═══════════════════════════════════════════════════════════
#  NEW in s07：上下文压缩（compaction）
#
#  机制（对照 compact.rs）：
#    - 触发：用「总字符数」当 token 的代理。超过 BUDGET_CHARS 就压缩。
#      真源码有两种触发：proactive（token 接近上限，turn.rs 主动压）
#      + reactive（命中 ContextWindowExceeded 错误，边压边重试）。
#    - 做法：把「最旧的若干回合」交给模型总结成一条 [summary] 消息，
#      只保留最近 KEEP_RECENT 个 item，再把摘要垫在它们前面。
#    - 离线：摘要可以是确定性启发式（拼接旧回合的要点），不需要真模型。
#
#  真源码用 SUMMARY_PREFIX 前缀标记摘要消息；教学版用 "[summary] " 等价表达。
# ═══════════════════════════════════════════════════════════

# 预算：总字符数代理 token。超过就触发压缩。
# 设得偏小，好让一段二十来项的长对话稳定触发压缩（真源码的阈值是 token 上限的某个比例）。
BUDGET_CHARS = 400
# 压缩后保留的最近 item 数（其余压进摘要）。
KEEP_RECENT = 6
# 摘要消息前缀。对照真源码 SUMMARY_PREFIX（prompts/templates/compact/summary_prefix.md），
# 它用一段长文本标记「这是上一段思考的摘要」；教学版用简短前缀，语义等价。
SUMMARY_PREFIX = "[summary]"


def total_chars(messages: list[dict]) -> int:
    """把整个消息列表的可见文本字符数加起来，当作 token 用量的廉价代理。"""
    n = 0
    for m in messages:
        c = m.get("content", "")
        if isinstance(c, str):
            n += len(c)
        n += len(str(m.get("arguments", "")))  # function_call 的参数
        n += len(str(m.get("output", "")))      # function_call_output 的结果
    return n


def item_text(m: dict) -> str:
    """把一个 input item 压成一行简短文本，供摘要启发式拼接。

    刻意裁短每一类 item：压缩的本意就是用更少的字符承载「发生过什么」。
    对照真源码：工具输出在压缩历史里会被 truncate（TruncationPolicy），
    而冗长的中间过程被一段 handoff 摘要替代。
    """
    t = m.get("type")
    if t == "message":
        role = m.get("role", "?")
        return f"{role}: {str(m.get('content','')).strip()[:40]}"
    if t == "function_call":
        return f"tool_call: {m.get('name','')}"  # 只留工具名，参数略去
    if t == "function_call_output":
        # 工具输出在摘要里被大幅裁短——它通常很长，但回顾时只需知道「跑过、大致结果」。
        out = str(m.get("output", "")).strip().replace("\n", " ")
        return f"tool_result: {out[:24]}…"
    return str(m)


def summarize(old_items: list[dict], model: Model | None = None) -> str:
    """把最旧的一批 item 总结成一条文本。

    真源码：把旧历史发给模型，用 SUMMARIZATION_PROMPT 让它产出 handoff 摘要。
    教学版离线：默认用确定性启发式（逐行拼接 + 截断），不需要模型；
    若显式传入 model 且后端非 mock，也可走真模型（这里仍走启发式以保证离线）。
    """
    lines = [item_text(m) for m in old_items]
    lines = [ln for ln in lines if ln.strip()]
    body = "\n".join(f"  - {ln}" for ln in lines)
    head = f"压缩了最早的 {len(old_items)} 个对话项；要点回顾："
    return f"{SUMMARY_PREFIX} {head}\n{body}"


def compact(messages: list[dict], model: Model | None = None) -> list[dict]:
    """若超预算：把旧 item 压成一条摘要消息，垫在最近 KEEP_RECENT 个 item 前。

    对照 compact.rs build_compacted_history：新历史 = [初始上下文] + [近期用户消息] +
    [摘要]。教学版简化为 [摘要消息] + [最近 KEEP_RECENT 个 item]，摘要本身就是
    一条 user 消息（真源码里摘要也被编码成 role="user" 的消息）。
    """
    if total_chars(messages) <= BUDGET_CHARS or len(messages) <= KEEP_RECENT:
        return messages  # 没超预算，原样返回
    split = len(messages) - KEEP_RECENT
    old, recent = messages[:split], messages[split:]
    summary_text = summarize(old, model)
    summary_item = user_item(summary_text)  # 摘要作为一条 user 消息（同真源码）
    return [summary_item, *recent]


def is_summary(m: dict) -> bool:
    """判断一条消息是不是压缩摘要（对照 compact.rs is_summary_message：前缀匹配）。"""
    return (m.get("type") == "message"
            and str(m.get("content", "")).startswith(SUMMARY_PREFIX))


# ═══════════════════════════════════════════════════════════
#  FROM s01（搬运）：回合循环 + 一个 shell 工具
#  唯一区别：每回合开始前先跑一次 compact()（proactive 压缩）。
# ═══════════════════════════════════════════════════════════

import subprocess  # noqa: E402

SYSTEM = ("You are Codex, a coding agent. Use the shell tool. Act, don't explain.")
model = Model()


def run_shell(command: str) -> str:
    try:
        r = subprocess.run(command, shell=True, cwd=Path.cwd(), capture_output=True,
                           text=True, encoding="utf-8", errors="replace", timeout=120)
        out = (r.stdout + r.stderr).strip()
        tag = "" if r.returncode == 0 else f"[exit {r.returncode}] "
        return tag + (out[:50000] if out else "(no output)")
    except (subprocess.TimeoutExpired, OSError) as e:
        return f"Error: {e}"


TOOLS = [{
    "name": "shell",
    "description": "Run a shell command and return combined stdout+stderr.",
    "parameters": {
        "type": "object",
        "properties": {"command": {"type": "string", "description": "the command line"}},
        "required": ["command"],
    },
}]
HANDLERS = {"shell": run_shell}


def run_turn(messages: list[dict]) -> list[dict]:
    # proactive 压缩：每回合开始前先看一眼预算，超了就先压（对照 turn.rs 的
    # token_limit_reached → run_auto_compact）。
    before = len(messages)
    messages[:] = compact(messages, model)
    if len(messages) != before:
        print(f"\033[35m[compaction] {before} → {len(messages)} 项（旧回合已压成摘要）\033[0m")
    while True:
        resp = model.respond(messages, tools=TOOLS, system=SYSTEM)
        messages += resp.output_items
        if not resp.tool_calls:
            if resp.text:
                print(f"\n\033[32m{resp.text}\033[0m")
            return messages
        for tc in resp.tool_calls:
            print(f"\033[33m> {tc.name} {tc.arguments}\033[0m")
            fn = HANDLERS.get(tc.name)
            out = fn(**tc.arguments) if fn else f"unknown tool: {tc.name}"
            print(str(out)[:200])
            messages.append(tool_output_item(tc.call_id, out))


# ═══════════════════════════════════════════════════════════
#  --demo：造一段长对话，跑压缩，打印 before/after + 摘要
# ═══════════════════════════════════════════════════════════

def build_long_conversation() -> list[dict]:
    """造约 20 个 item 的假对话（用户问 / 工具调用 / 工具结果交替）。"""
    msgs: list[dict] = []
    topics = ["列出目录", "读 README", "跑测试", "查 git 分支", "看依赖版本", "统计行数"]
    for i, topic in enumerate(topics):
        msgs.append(user_item(f"第{i+1}步：请{topic}。"))
        msgs.append({"type": "function_call", "call_id": f"c{i}",
                     "name": "shell", "arguments": f'{{"command": "do-{topic}"}}'})
        msgs.append(tool_output_item(
            f"c{i}", f"{topic} 的输出 " + ("x" * 40)))  # 故意撑长，模拟真实冗长输出
    msgs.append(user_item("好的，最后请帮我总结一下我们刚才做了哪些事。"))
    return msgs


class ContextWindowExceeded(Exception):
    """模拟真 CodexErr::ContextWindowExceeded：实际 token 超了模型硬上限。"""


def respond_reactive(messages, attempt_fn):
    """reactive 压缩：proactive 用「字符数估算」决定何时压，但估算会失准。真撞上
    ContextWindowExceeded 时，就**从头删最旧一项、重试**，直到放得下——兜底的最后一道闸。
    对应 compact.rs 主循环：history.remove_first_item() + retries=0 重试。"""
    work = list(messages)
    while True:
        try:
            return attempt_fn(work), work
        except ContextWindowExceeded:
            if len(work) <= 1:
                raise
            dropped = work.pop(0)      # 从头删最旧的一项
            print(f"  ⚠ ContextWindowExceeded → 删最旧一项（{item_text(dropped)[:28]}…），"
                  f"剩 {len(work)} 项后重试")


def demo_reactive() -> None:
    print("\n生产级：估算失准时的 reactive 压缩（撞墙 → 删最旧 → 重试）——")
    convo = build_long_conversation()
    hard_limit = 12   # 模型真实硬上限（这里用 item 数代理），比 proactive 估的更紧
    def attempt(msgs):
        if len(msgs) > hard_limit:
            raise ContextWindowExceeded()      # 模型那头报「装不下」
        return f"ok：{len(msgs)} 项放下了，回合成功"
    print(f"  proactive 以为没事，但模型硬上限约 {hard_limit} 项——撞墙后自动从头删项重试：")
    result, _ = respond_reactive(convo, attempt)
    print("  →", result)


def demo() -> None:
    print("s07 demo：上下文压缩 before/after\n")
    convo = build_long_conversation()
    print(f"压缩前：{len(convo)} 个 item，约 {total_chars(convo)} 字符 "
          f"（预算 {BUDGET_CHARS}）")
    print("  前几项预览：")
    for m in convo[:4]:
        print(f"    · {item_text(m)[:70]}")
    print("    · ...")

    after = compact(convo)
    print(f"\n压缩后：{len(after)} 个 item，约 {total_chars(after)} 字符")
    print("  结构：[摘要] + 最近", KEEP_RECENT, "项\n")

    print("产出的摘要（确定性启发式，离线无需模型）：")
    print("─" * 60)
    print(after[0]["content"])
    print("─" * 60)

    assert is_summary(after[0]), "首项应为摘要"
    assert len(after) == 1 + KEEP_RECENT, "应为 1 条摘要 + KEEP_RECENT 条近期项"
    print(f"\n校验通过：{len(convo)} 项 → 1 条摘要 + 最近 {KEEP_RECENT} 项 "
          f"= {len(after)} 项。")
    demo_reactive()
    sys.exit(0)


if __name__ == "__main__":
    if "--demo" in sys.argv:
        demo()

    print("s07: Context Compaction — 聊到超预算会自动压缩（输入 q 退出）\n")
    history: list[dict] = []
    while True:
        try:
            query = input("\033[36ms07 >> \033[0m")
        except (EOFError, KeyboardInterrupt):
            break
        if query.strip().lower() in ("q", "exit", ""):
            break
        history.append(user_item(query))
        history = run_turn(history)
        print(f"\033[90m[当前 {len(history)} 项 / 约 {total_chars(history)} 字符]\033[0m\n")
