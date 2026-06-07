"""模型客户端：规范化返回（文本 / 工具调用 / 推理）+ 重试（s09）。

离线 mock：脚本化驱动 demo——没有工具结果就发一个 apply_patch 调用建文件，有了就收尾。
真版在 `respond` 里发 OpenAI Responses 请求（见 learn-codex 的 codexlib openai 后端）。
"""

from __future__ import annotations

import random
from dataclasses import dataclass, field

INITIAL_DELAY_MS = 200
BACKOFF_FACTOR = 2.0


@dataclass
class ToolCall:
    call_id: str
    name: str
    arguments: dict


@dataclass
class ModelResponse:
    text: str = ""
    reasoning: str = ""
    tool_calls: list = field(default_factory=list)


class TransientError(Exception):
    """可重试：流断 / 429 / 5xx。"""


class FatalError(Exception):
    """致命：鉴权 / 请求非法——重试无意义。"""


def backoff(attempt: int) -> float:
    """指数退避 + 抖动，毫秒（s09 util.rs:85）。"""
    return INITIAL_DELAY_MS * (BACKOFF_FACTOR ** (attempt - 1)) * random.uniform(0.9, 1.1)


def respond_with_retry(call, max_retries: int = 5):
    """对一次模型调用做重试：可重试错误退避重试、致命错误直接抛、封顶 max_retries（s09）。"""
    attempt = 0
    while True:
        try:
            return call()
        except FatalError:
            raise
        except TransientError:
            attempt += 1
            if attempt > max_retries:
                raise
            _ = backoff(attempt)   # 真版此处 sleep(delay)；离线 demo 不真睡


class Model:
    """离线 mock 模型：确定性地驱动 demo 的一轮。真版连 Responses API。"""

    def __init__(self, model_id: str = "gpt-5-codex") -> None:
        self.model_id = model_id

    def respond(self, messages: list[dict], tools=None, system=None) -> ModelResponse:
        # 已经有工具结果 → 这一回合收尾，输出最终文本。
        if any(m.get("type") == "function_call_output" for m in messages):
            last = next(m for m in reversed(messages)
                        if m.get("type") == "function_call_output")
            return ModelResponse(
                reasoning="文件已写好，可以收尾了。",
                text=f"完成 ✅ 已创建 greeting.txt。工具回报：{str(last.get('output', ''))[:80]}")
        # 否则：发一个 apply_patch 调用，建 greeting.txt。
        patch = ("*** Begin Patch\n*** Add File: greeting.txt\n"
                 "+hello, mini-codex\n+assembled from 18 chapters\n*** End Patch")
        return ModelResponse(
            reasoning="用户想创建一个文件，应当调用 apply_patch。",
            tool_calls=[ToolCall("call_1", "apply_patch", {"input": patch})])
