"""memory —— AGENTS.md 分层注入 + 字节预算（s06）。"""

from .agents_md import PROJECT_DOC_MAX_BYTES, collect_agents_md

__all__ = ["collect_agents_md", "PROJECT_DOC_MAX_BYTES"]
