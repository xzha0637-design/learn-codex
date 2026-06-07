"""工具注册表 + 分发层（s02）。

生产级：分发前先**对照 schema 校验**模型给的参数（缺必填/多字段/类型错/未知工具），
任何失败都**回灌成错误串**给模型（RespondToModel），绝不崩进程。这层就是真 Codex 的 ToolRouter。
"""

from __future__ import annotations

from dataclasses import dataclass, field
from pathlib import Path

_JSON_PY = {"string": str, "integer": int, "number": (int, float),
            "boolean": bool, "object": dict, "array": list}


@dataclass
class ToolContext:
    """传给每个工具 handler 的执行上下文。"""
    workspace: Path
    plan: list = field(default_factory=list)

    def safe_path(self, rel: str) -> Path:
        """把相对路径锚定到工作区内，越界抛错（应用层护栏；内核兜底见 safety/sandbox）。"""
        p = (self.workspace / rel).resolve()
        if not str(p).startswith(str(self.workspace.resolve())):
            raise ValueError(f"path escapes workspace: {rel}")
        return p


@dataclass
class Tool:
    name: str
    description: str
    parameters: dict     # JSON Schema
    handler: object      # callable(ctx, **args) -> str


class ToolRegistry:
    def __init__(self) -> None:
        self._tools: dict[str, Tool] = {}

    def register(self, tool: Tool) -> None:
        self._tools[tool.name] = tool

    def specs(self) -> list[dict]:
        return [{"name": t.name, "description": t.description, "parameters": t.parameters}
                for t in self._tools.values()]

    def validate(self, name: str, args: dict) -> str | None:
        tool = self._tools.get(name)
        if tool is None:
            return f"unknown tool `{name}` (available: {sorted(self._tools)})"
        props = tool.parameters.get("properties", {})
        for req in tool.parameters.get("required", []):
            if req not in args:
                return f"missing required field `{req}`"
        for key, val in args.items():
            if key not in props:
                return f"unexpected field `{key}`"
            want = props[key].get("type")
            py = _JSON_PY.get(want)
            if py and (not isinstance(val, py)
                       or (want in ("integer", "number") and isinstance(val, bool))):
                return f"field `{key}` should be {want}, got {type(val).__name__}"
        return None

    def dispatch(self, name: str, args: dict, ctx: ToolContext) -> str:
        err = self.validate(name, args)
        if err is not None:
            return f"ERROR: invalid call: {err}"
        try:
            return self._tools[name].handler(ctx, **args)
        except Exception as e:  # noqa: BLE001 — 回灌而非崩溃
            return f"ERROR: tool `{name}` raised {type(e).__name__}: {e}"
