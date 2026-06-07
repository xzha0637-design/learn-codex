"""shell 工具（s01/s02）：跑命令，自动关进内核沙箱（s05）。"""

from __future__ import annotations

from ..safety.sandbox import run_sandboxed
from .registry import Tool, ToolContext


def shell_handler(ctx: ToolContext, command: str) -> str:
    return run_sandboxed(command, writable_roots=[str(ctx.workspace)], cwd=ctx.workspace)


SHELL = Tool(
    name="shell",
    description="Run a shell command. Sandboxed: writes confined to the workspace, no network.",
    parameters={"type": "object",
                "properties": {"command": {"type": "string"}},
                "required": ["command"]},
    handler=shell_handler,
)
