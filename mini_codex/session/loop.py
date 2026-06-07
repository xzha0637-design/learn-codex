"""回合循环（s01）—— 整个 agent 的心脏。

生产级两道护栏：`max_steps` 步数封顶（防失控）+ `cancelled()` 协作式取消（喊停能真停）。
模型调用包了重试（s09）；每一步把发生的事 `emit` 成事件（s10），让前端去消费。
工具怎么执行（校验/审批/沙箱/留底）由 `on_tool_call` 回调决定——那是 agent 的装配活（s17）。
"""

from __future__ import annotations

import json

from ..model import respond_with_retry
from ..protocol import EventType


def run_turn(model, messages, system, tool_specs, on_tool_call, bus,
             max_steps: int = 20, cancelled=None) -> list[dict]:
    bus.emit(EventType.TURN_STARTED, items=len(messages))
    for _step in range(max_steps):
        if cancelled is not None and cancelled():
            bus.emit(EventType.ERROR, reason="interrupted")
            return messages

        resp = respond_with_retry(
            lambda: model.respond(messages, tools=tool_specs, system=system))

        if resp.reasoning:
            bus.emit(EventType.REASONING, text=resp.reasoning)
        if resp.text:
            messages.append({"type": "message", "role": "assistant", "content": resp.text})
        for tc in resp.tool_calls:
            messages.append({"type": "function_call", "call_id": tc.call_id, "name": tc.name,
                             "arguments": json.dumps(tc.arguments, ensure_ascii=False)})

        if not resp.tool_calls:
            bus.emit(EventType.MESSAGE, text=resp.text)
            bus.emit(EventType.TURN_COMPLETE)
            return messages

        for tc in resp.tool_calls:
            output = on_tool_call(tc)        # 走 agent 的安全流水线（钩子→Guardian→审批→沙箱→执行→留底）
            messages.append({"type": "function_call_output", "call_id": tc.call_id, "output": output})

    bus.emit(EventType.ERROR, reason=f"触及 max_steps={max_steps} 上限，强制收尾")
    return messages
