#!/usr/bin/env python3
"""
s09: Responses API — Codex 的线协议（不是 Chat Completions，也不是 Anthropic Messages）。

运行:
  python s09_responses_api/code.py --demo    # 离线：构造请求 + 解析响应 + 三方对比 + 重试/退避

本章不依赖网络：
  ① 把对话与工具打包成 Responses 请求
  ② 从响应的 output items 里抽出 文本 / 工具调用 / 推理(reasoning)
  ③ 生产级：模型调用是一条会抖的网络长流——重试、指数退避+抖动、honor Retry-After、
     错误分类（可重试 vs 致命）、max_retries 封顶、传输回退。玩具崩在这里，生产级 harness 扛过去。

忠实对应：
  - codex-rs/core/src/client.rs（ModelClient::stream_responses）
  - codex-rs/core/src/responses_retry.rs（handle_retryable_response_stream_error）
  - codex-rs/core/src/util.rs:85（backoff：指数退避 + 0.9~1.1 抖动）
  - codex-rs/model-provider-info/src/lib.rs（request_max_retries / stream_max_retries）
"""

import json
import random
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
from codexlib import ToolCall  # noqa: E402


# ═══════════════════════════════════════════════════════════
#  ① 构造 Responses API 请求
#  Responses 的 input 是「扁平的 item 列表」，tools 也是扁平的 {type:function,...}
# ═══════════════════════════════════════════════════════════

def build_request(model_id, system, messages, tools):
    return {
        "model": model_id,
        "instructions": system,           # 不叫 "system"，叫 "instructions"
        "input": messages,                # message / function_call / function_call_output 混排
        "tools": [{"type": "function", "name": t["name"],
                   "description": t.get("description", ""),
                   "parameters": t["parameters"]} for t in tools],
        "reasoning": {"effort": "medium"},  # Responses 独有：推理力度（Chat Completions 没有）
        "stream": True,
    }


# ═══════════════════════════════════════════════════════════
#  ② 解析 Responses API 响应
#  响应里 output 是一串 item：reasoning / message / function_call
# ═══════════════════════════════════════════════════════════

def parse_response(resp: dict):
    text_parts, tool_calls, reasoning = [], [], []
    for item in resp.get("output", []):
        t = item.get("type")
        if t == "reasoning":
            reasoning.append("".join(s.get("text", "") for s in item.get("summary", [])))
        elif t == "message":
            for part in item.get("content", []):
                if part.get("type") == "output_text":
                    text_parts.append(part.get("text", ""))
        elif t == "function_call":
            tool_calls.append(ToolCall(
                call_id=item.get("call_id", ""),
                name=item.get("name", ""),
                arguments=json.loads(item.get("arguments", "{}")),
            ))
    return "".join(text_parts), tool_calls, reasoning


# ═══════════════════════════════════════════════════════════
#  三方协议：同一个「工具定义」在三家长什么样
# ═══════════════════════════════════════════════════════════

def protocol_comparison():
    tool = {"name": "shell", "description": "run a command",
            "schema": {"type": "object", "properties": {"command": {"type": "string"}}}}

    anthropic = {"name": tool["name"], "description": tool["description"],
                 "input_schema": tool["schema"]}                       # Claude: input_schema
    openai_responses = {"type": "function", "name": tool["name"],
                        "description": tool["description"],
                        "parameters": tool["schema"]}                  # Codex: 扁平 + parameters
    openai_chat = {"type": "function", "function": {                   # 旧的 Chat Completions: 嵌套
        "name": tool["name"], "description": tool["description"],
        "parameters": tool["schema"]}}

    print("Anthropic Messages（Claude Code）:")
    print("  " + json.dumps(anthropic, ensure_ascii=False))
    print("OpenAI Responses（Codex 用的）:")
    print("  " + json.dumps(openai_responses, ensure_ascii=False))
    print("OpenAI Chat Completions（老接口，Codex 不用）:")
    print("  " + json.dumps(openai_chat, ensure_ascii=False))


# 一份「写死的」Responses API 响应样例（含 reasoning + function_call）
CANNED_RESPONSE = {
    "id": "resp_demo",
    "output": [
        {"type": "reasoning",
         "summary": [{"type": "summary_text",
                      "text": "用户想看文件，应当调用 shell 跑 ls。"}]},
        {"type": "function_call", "call_id": "call_abc", "name": "shell",
         "arguments": "{\"command\": \"ls -la\"}"},
    ],
}


# ═══════════════════════════════════════════════════════════
#  ③ 生产级：模型调用是一条会抖的网络流——重试 / 退避 / 错误分类 / 回退
#
#  真相：stream_responses 是一条 SSE/WebSocket 长流，中途会断、会被限流(429)、会 5xx。
#  玩具在这里抛异常崩掉；生产级 harness 把「失败」当成一条**可恢复**的常规路径：
#    · 可重试错误（流断 / 429 / 5xx）→ 指数退避 + 抖动后重试，封顶 max_retries
#    · 服务端给了 Retry-After → 听它的，别自己瞎猜
#    · 致命错误（401 鉴权 / 400 参数）→ 立刻失败，重试没意义
#    · 抖动（0.9~1.1）是为了让并发客户端的重试时刻错开，避免一起踩踏（thundering herd）
#  对应 responses_retry.rs::handle_retryable_response_stream_error + util.rs:85::backoff。
# ═══════════════════════════════════════════════════════════

INITIAL_DELAY_MS = 200      # util.rs 的初始延迟
BACKOFF_FACTOR = 2.0        # 每次退避翻倍（指数）


def backoff(attempt: int) -> float:
    """指数退避 + 抖动，返回毫秒。忠实搬运 util.rs:85 的公式：
    base = INITIAL * FACTOR^(attempt-1)，再乘 0.9~1.1 抖动。"""
    base = INITIAL_DELAY_MS * (BACKOFF_FACTOR ** (attempt - 1))
    return base * random.uniform(0.9, 1.1)


class TransientError(Exception):
    """可重试：流断 / 限流 / 5xx。可带服务端要求的 retry_after（毫秒）。
    对应 CodexErr::Stream(_, requested_delay)——requested_delay 就是服务端给的延迟。"""
    def __init__(self, msg: str, retry_after_ms: float | None = None):
        super().__init__(msg)
        self.retry_after_ms = retry_after_ms


class FatalError(Exception):
    """致命：鉴权 / 参数错——重试没用，立刻失败（对应 CodexErr 的非 Stream 致命变体）。"""


def call_with_retry(stream_fn, max_retries: int = 5):
    """对一条 Responses 流做重试。= handle_retryable_response_stream_error 的教学内核：
    可重试错误退避后重试、honor 服务端 delay、封顶 max_retries；致命错误直接抛。"""
    attempt = 0
    while True:
        try:
            return stream_fn(attempt)
        except FatalError as e:
            print(f"  ✗ 致命错误，不重试：{e}")            # 401/400：重试无意义
            raise
        except TransientError as e:
            attempt += 1
            if attempt > max_retries:
                # 真 Codex 这里还会先试「传输回退」(WebSocket→HTTPS, retries 清零)，
                # 都不行才放弃。见 responses_retry.rs。
                print(f"  ✗ 重试 {max_retries} 次仍失败，放弃（真身会先试传输回退）：{e}")
                raise
            # honor 服务端 Retry-After；否则指数退避（CodexErr::Stream 的 requested_delay 逻辑）
            delay = e.retry_after_ms if e.retry_after_ms is not None else backoff(attempt)
            src = "服务端 Retry-After" if e.retry_after_ms is not None else "指数退避+抖动"
            print(f"  ↻ Reconnecting... {attempt}/{max_retries}，{delay:.0f}ms 后重试（{src}）：{e}")
            # 真身此处 tokio::time::sleep(delay).await；demo 不真睡，省得你等。


def demo():
    print("① 构造一个 Responses 请求 ——")
    req = build_request("gpt-5-codex", "You are Codex.",
                        [{"type": "message", "role": "user", "content": "列出文件"}],
                        [{"name": "shell", "description": "run a command",
                          "parameters": {"type": "object",
                                         "properties": {"command": {"type": "string"}}}}])
    print(json.dumps(req, ensure_ascii=False, indent=2)[:700] + "\n")

    print("② 解析一份 Responses 响应（含 reasoning + function_call）——")
    text, calls, reasoning = parse_response(CANNED_RESPONSE)
    print(f"  reasoning: {reasoning}")
    print(f"  text:      {text!r}")
    for c in calls:
        print(f"  tool_call: {c.name}({c.arguments})  call_id={c.call_id}")
    print()

    print("③ 同一个工具，三家协议的写法对比 ——")
    protocol_comparison()

    print("\n④ 生产级：模型流会抖——重试 / 退避 / 错误分类（不真睡，直接打印延迟）——")
    random.seed(7)  # 固定抖动，方便你对照输出

    calls = {"n": 0}
    def flaky(attempt):                       # (a) 流断两次后第三次成功
        calls["n"] += 1
        if calls["n"] <= 2:
            raise TransientError("stream disconnected")
        return "ok：第 3 次连上，拿到完整响应"
    print(" (a) 流断两次后自动重连：")
    print("    →", call_with_retry(flaky), "\n")

    def rate_limited(attempt):                # (b) 429 且服务端给了 Retry-After
        if attempt == 0:
            raise TransientError("429 too many requests", retry_after_ms=1500)
        return "ok：等满服务端要求的 1500ms 后通过"
    print(" (b) 429 限流、honor 服务端 Retry-After（不瞎猜延迟）：")
    print("    →", call_with_retry(rate_limited), "\n")

    def auth_fail(attempt):                   # (c) 鉴权失败——致命，不浪费重试
        raise FatalError("401 unauthorized（API key 无效）")
    print(" (c) 鉴权失败是致命错误，立刻放弃：")
    try:
        call_with_retry(auth_fail)
    except FatalError:
        pass
    print()

    def always_down(attempt):                 # (d) 一直失败——封顶 max_retries 后放弃
        raise TransientError("stream keeps dropping")
    print(" (d) 一直失败，封顶 max_retries=2 后放弃（真身会先试 WebSocket→HTTPS 回退）：")
    try:
        call_with_retry(always_down, max_retries=2)
    except TransientError:
        pass


if __name__ == "__main__":
    # 本章是讲解性的（剖析线协议），无论是否带 --demo 都直接演示。
    demo()
