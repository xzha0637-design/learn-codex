"""plan 工具（s12）：维护一个可观测的任务清单（过程，不是真执行）。"""

from __future__ import annotations

from .registry import Tool, ToolContext


def update_plan_handler(ctx: ToolContext, steps: list) -> str:
    ctx.plan = list(steps)
    return "plan updated:\n" + "\n".join(
        f"  [{s.get('status', 'todo')}] {s.get('step', '')}" for s in steps)


UPDATE_PLAN = Tool(
    name="update_plan",
    description="Update the task plan: a list of {step, status} objects.",
    parameters={"type": "object",
                "properties": {"steps": {"type": "array"}},
                "required": ["steps"]},
    handler=update_plan_handler,
)
