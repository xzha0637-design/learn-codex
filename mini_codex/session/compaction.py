"""上下文压缩（s07）：超预算就把最旧的回合压成一条摘要，保留最近若干项。

proactive（这里实现的）省 token；真 Codex 还有 reactive 兜底——撞上 ContextWindowExceeded
就从头删项重试（见 s07「生产级」）。
"""

from __future__ import annotations

BUDGET_CHARS = 6000
KEEP_RECENT = 10
SUMMARY_PREFIX = "[summary]"


def total_chars(messages: list[dict]) -> int:
    n = 0
    for m in messages:
        n += len(str(m.get("content", "")))
        n += len(str(m.get("arguments", "")))
        n += len(str(m.get("output", "")))
    return n


def compact(messages: list[dict]) -> list[dict]:
    if total_chars(messages) <= BUDGET_CHARS or len(messages) <= KEEP_RECENT:
        return messages
    split = len(messages) - KEEP_RECENT
    old = messages[:split]
    summary = {"type": "message", "role": "user",
               "content": f"{SUMMARY_PREFIX} 压缩了最早的 {len(old)} 个对话项以腾出上下文。"}
    return [summary, *messages[split:]]
