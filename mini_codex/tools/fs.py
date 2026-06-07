"""文件工具（s02）：read_file / write_file / list_dir，均锚定在工作区内。"""

from __future__ import annotations

from .registry import Tool, ToolContext


def read_file_handler(ctx: ToolContext, path: str, limit: int | None = None) -> str:
    lines = ctx.safe_path(path).read_text(encoding="utf-8", errors="replace").splitlines()
    if limit and limit < len(lines):
        lines = lines[:limit] + [f"... ({len(lines) - limit} more lines)"]
    return "\n".join(lines) or "(empty file)"


def write_file_handler(ctx: ToolContext, path: str, content: str) -> str:
    p = ctx.safe_path(path)
    p.parent.mkdir(parents=True, exist_ok=True)
    p.write_text(content, encoding="utf-8")
    return f"wrote {len(content)} bytes to {path}"


def list_dir_handler(ctx: ToolContext, path: str = ".") -> str:
    base = ctx.safe_path(path)
    if not base.is_dir():
        return f"not a directory: {path}"
    rows = sorted(e.name + ("/" if e.is_dir() else "") for e in base.iterdir())
    return "\n".join(rows) or "(empty directory)"


READ_FILE = Tool("read_file", "Read a text file in the workspace.",
                 {"type": "object",
                  "properties": {"path": {"type": "string"}, "limit": {"type": "integer"}},
                  "required": ["path"]}, read_file_handler)

WRITE_FILE = Tool("write_file", "Write text content to a file in the workspace.",
                  {"type": "object",
                   "properties": {"path": {"type": "string"}, "content": {"type": "string"}},
                   "required": ["path", "content"]}, write_file_handler)

LIST_DIR = Tool("list_dir", "List entries of a directory in the workspace.",
                {"type": "object", "properties": {"path": {"type": "string"}}, "required": []},
                list_dir_handler)
