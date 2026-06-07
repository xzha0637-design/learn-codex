"""tools —— 工具注册表 + 各工具，每个工具独立成文件（s02 / s01 / s03 / s12）。"""

from .apply_patch import APPLY_PATCH
from .fs import LIST_DIR, READ_FILE, WRITE_FILE
from .plan import UPDATE_PLAN
from .registry import Tool, ToolContext, ToolRegistry
from .shell import SHELL


def build_default_registry() -> ToolRegistry:
    """组装默认工具集。加一个工具 = 多 register 一行（循环不改，s02 主线）。"""
    reg = ToolRegistry()
    for tool in (SHELL, APPLY_PATCH, READ_FILE, WRITE_FILE, LIST_DIR, UPDATE_PLAN):
        reg.register(tool)
    return reg


__all__ = ["Tool", "ToolContext", "ToolRegistry", "build_default_registry"]
