"""事件类型 + 事件总线（EQ）。

对照 s10：core 只管**产出** Event，前端只管**消费**——两边解耦。真 Codex 用有界异步
channel（背压 / 顺序保证 / 可中断，见 s10「生产级」）；这里用一个最小的同步总线。
"""

from __future__ import annotations

from dataclasses import dataclass, field
from enum import Enum


class EventType(Enum):
    TURN_STARTED = "turn_started"
    REASONING = "reasoning"
    TOOL_BEGIN = "tool_begin"
    TOOL_END = "tool_end"
    APPROVAL = "approval"
    GUARDIAN = "guardian"
    BLOCKED = "blocked"
    MESSAGE = "message"
    COMPACTION = "compaction"
    TURN_COMPLETE = "turn_complete"
    ERROR = "error"


@dataclass
class Event:
    type: EventType
    data: dict = field(default_factory=dict)


class EventBus:
    """极简 EQ：core `emit`，前端 `subscribe`。一个事件可被多个前端消费（TUI / JSONL）。"""

    def __init__(self) -> None:
        self._subscribers: list = []

    def subscribe(self, fn) -> None:
        self._subscribers.append(fn)

    def emit(self, event_type: EventType, **data) -> None:
        event = Event(event_type, data)
        for fn in self._subscribers:
            fn(event)
