"""protocol —— core 与前端之间的事件流（s10 SQ/EQ 的精简）。"""

from .events import Event, EventBus, EventType

__all__ = ["Event", "EventBus", "EventType"]
