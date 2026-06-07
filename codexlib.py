#!/usr/bin/env python3
"""
codexlib.py — learn-codex 唯一的共享模块：模型调用抽象。

为什么需要它？
  learn-claude-code 每章直接 `from anthropic import Anthropic`，模型调用就几行。
  learn-codex 想做到「无 key 也能跑」，所以把模型调用抽到这里，提供两个后端：

    - mock    （默认，无需任何 key）：脚本化地返回工具调用 / 文本，让回合循环离线可见。
    - openai  （真 Codex 线协议）：OpenAI **Responses API**（不是 Chat Completions！）。

  每一章「独有的机制」（apply_patch 解析器、沙箱包装、SQ/EQ 队列……）都内联在该章
  的 code.py 里、可单文件通读；只有「模型怎么被调用」这件公共的事放在这里。

消息格式 = OpenAI Responses API 的 input item 形状（这正是 Codex 真实使用的）：
    {"type":"message","role":"user"|"assistant","content": "..."}
    {"type":"function_call","call_id": "...", "name": "...", "arguments": "<json string>"}
    {"type":"function_call_output","call_id": "...", "output": "..."}

  对比 Claude Code：Anthropic 用 messages[].content 里的 tool_use / tool_result 块；
  Codex 用扁平的 input item 列表（function_call / function_call_output 各自独立成项）。
  这是两家 wire protocol 的根本差异，s07 会专门拆解。
"""

from __future__ import annotations

import json
import os
import re
from dataclasses import dataclass, field

try:
    from dotenv import load_dotenv

    load_dotenv(override=True)
except ImportError:  # dotenv 是可选的
    pass


# ─────────────────────────────────────────────────────────────
#  规范化的返回类型（与后端无关）
# ─────────────────────────────────────────────────────────────


@dataclass
class ToolCall:
    """模型发起的一次工具调用。arguments 已从 JSON 字符串解析为 dict。"""

    call_id: str
    name: str
    arguments: dict


@dataclass
class ModelResponse:
    """一次模型回合的规范化结果。"""

    text: str = ""
    tool_calls: list[ToolCall] = field(default_factory=list)
    # output_items：本回合模型产出的 input-item 列表（assistant 文本 + function_call），
    # 章节直接 `messages += resp.output_items` 把它回灌进对话即可。
    output_items: list[dict] = field(default_factory=list)


# ─────────────────────────────────────────────────────────────
#  构造 input item 的小helper（章节用，省样板）
# ─────────────────────────────────────────────────────────────


def user_item(text: str) -> dict:
    return {"type": "message", "role": "user", "content": text}


def tool_output_item(call_id: str, output: str) -> dict:
    """工具执行结果回灌给模型。Codex 用 function_call_output 这一独立 item。"""
    return {"type": "function_call_output", "call_id": call_id, "output": output}


# ─────────────────────────────────────────────────────────────
#  Model：两个后端
# ─────────────────────────────────────────────────────────────


class Model:
    def __init__(self) -> None:
        self.backend = os.getenv("CODEX_BACKEND", "").strip().lower()
        if not self.backend:
            # 没显式指定就自动选：有 key 用 openai，否则用 mock。
            self.backend = "openai" if os.getenv("OPENAI_API_KEY") else "mock"
        self.model_id = os.getenv("MODEL_ID", "gpt-5-codex")
        self._client = None  # openai 客户端懒加载
        print(f"\033[90m[codexlib] backend={self.backend} model={self.model_id}\033[0m")

    def respond(self, messages: list[dict], tools: list[dict] | None = None,
                system: str | None = None) -> ModelResponse:
        if self.backend == "openai":
            return self._openai_respond(messages, tools or [], system)
        return self._mock_respond(messages, tools or [], system)

    # ---- 真后端：OpenAI Responses API（Codex 的真实线协议）----
    def _openai_respond(self, messages, tools, system) -> ModelResponse:
        if self._client is None:
            from openai import OpenAI  # 懒加载，mock 模式无需安装 openai

            self._client = OpenAI(base_url=os.getenv("OPENAI_BASE_URL") or None)

        # Responses API 的工具格式是「扁平」的：{type:function, name, description, parameters}
        # 对比 Claude：Anthropic 是 {name, description, input_schema}。
        responses_tools = [
            {
                "type": "function",
                "name": t["name"],
                "description": t.get("description", ""),
                "parameters": t.get("parameters", {"type": "object", "properties": {}}),
            }
            for t in tools
        ]

        resp = self._client.responses.create(
            model=self.model_id,
            instructions=system or "",
            input=messages,            # 我们的 messages 已是 Responses input-item 形状
            tools=responses_tools or None,
        )

        out_items: list[dict] = []
        tool_calls: list[ToolCall] = []
        text = getattr(resp, "output_text", "") or ""

        for item in getattr(resp, "output", []) or []:
            itype = getattr(item, "type", None)
            if itype == "function_call":
                args_str = getattr(item, "arguments", "") or "{}"
                call_id = getattr(item, "call_id", None) or getattr(item, "id", "call_0")
                name = getattr(item, "name", "")
                out_items.append({
                    "type": "function_call",
                    "call_id": call_id,
                    "name": name,
                    "arguments": args_str,
                })
                try:
                    parsed = json.loads(args_str)
                except json.JSONDecodeError:
                    parsed = {"_raw": args_str}
                tool_calls.append(ToolCall(call_id, name, parsed))

        if text:
            out_items.insert(0, {"type": "message", "role": "assistant", "content": text})

        return ModelResponse(text=text, tool_calls=tool_calls, output_items=out_items)

    # ---- mock 后端：脚本化，让离线 demo 跑起来（明确是假的）----
    def _mock_respond(self, messages, tools, system) -> ModelResponse:
        names = [t["name"] for t in tools]

        # 如果对话里已经出现过工具结果 → 收尾，输出一句文本，结束循环。
        if any(m.get("type") == "function_call_output" for m in messages):
            last_out = next(m for m in reversed(messages)
                            if m.get("type") == "function_call_output")
            snippet = str(last_out.get("output", ""))[:160]
            text = f"[mock] 工具已执行，结果片段：{snippet}"
            return ModelResponse(text=text, tool_calls=[],
                                 output_items=[{"type": "message", "role": "assistant",
                                                "content": text}])

        query = self._last_user_text(messages)

        # 1) 有 apply_patch 且像是要建/改文件 → 发一个 apply_patch 调用
        if "apply_patch" in names and any(
            k in query for k in ["创建", "新建", "写一个", "写个", "patch",
                                  "create", "add file", "new file"]):
            patch = ("*** Begin Patch\n"
                     "*** Add File: mock_demo.txt\n"
                     "+created by the mock backend\n"
                     "+second line\n"
                     "*** End Patch")
            return self._one_call("apply_patch", {"input": patch})

        # 2) 有 shell 类工具 → 发一个 shell 调用
        shell_name = next((n for n in names if n in
                           ("shell", "exec", "local_shell", "bash")), None)
        if shell_name:
            return self._one_call(shell_name, {"command": self._extract_cmd(query)})

        # 3) 否则直接输出文本
        text = f"[mock] 收到：{query}（没有可用工具，直接回复）"
        return ModelResponse(text=text, tool_calls=[],
                             output_items=[{"type": "message", "role": "assistant",
                                            "content": text}])

    @staticmethod
    def _one_call(name: str, args: dict) -> ModelResponse:
        tc = ToolCall("mock_call_1", name, args)
        item = {"type": "function_call", "call_id": tc.call_id,
                "name": name, "arguments": json.dumps(args, ensure_ascii=False)}
        return ModelResponse(text="", tool_calls=[tc], output_items=[item])

    @staticmethod
    def _last_user_text(messages: list[dict]) -> str:
        for m in reversed(messages):
            if m.get("type") == "message" and m.get("role") == "user":
                return str(m.get("content", ""))
        return ""

    @staticmethod
    def _extract_cmd(query: str) -> str:
        m = re.search(r"`([^`]+)`", query)
        if m:
            return m.group(1)
        for kw in ("执行", "运行", "跑", "run ", "exec "):
            if kw in query:
                tail = query.split(kw, 1)[1].strip().strip(":：").strip()
                if tail:
                    return tail
        return "echo 'hello from the mock backend'"
