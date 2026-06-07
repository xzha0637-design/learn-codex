"""从工作区逐级向上收集 AGENTS.md，注入 system；带**字节预算**（s06）。

生产级：越靠近 cwd 的越具体、越靠后（优先级更高）；累计字节超预算就截断——
别让一个失控的项目文档挤掉真正的对话（对应 project_doc_max_bytes）。
"""

from __future__ import annotations

from pathlib import Path

PROJECT_DOC_MAX_BYTES = 4096


def collect_agents_md(start: Path, max_bytes: int = PROJECT_DOC_MAX_BYTES) -> str:
    """收集 [根 … start] 链上的所有 AGENTS.md，拼成一段（近的在后），超预算则截断。"""
    start = Path(start).resolve()
    found = [d / "AGENTS.md" for d in [start, *start.parents] if (d / "AGENTS.md").is_file()]

    docs, used = [], 0
    for path in reversed(found):                 # 从最远（根）到最近（start）
        try:
            text = path.read_text(encoding="utf-8", errors="replace")
        except OSError:
            continue
        remaining = max_bytes - used
        if remaining <= 0:
            docs.append("[...AGENTS.md 已达字节预算，后续截断]")
            break
        raw = text.encode("utf-8")
        if len(raw) > remaining:                 # 超预算 → 截断
            text = raw[:remaining].decode("utf-8", errors="ignore") + "\n[...truncated: over budget]"
        used += len(text.encode("utf-8"))
        docs.append(f"# {path}\n{text.strip()}")
    return "\n\n".join(docs)
