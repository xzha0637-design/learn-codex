"""技能加载器。

技能像「可按需翻开的手册」：平时只把**名字 + 一句简介**给模型（省上下文），模型要用时
才 `get(name)` 把整页注入——这和工具的「延迟暴露 / ToolSearch」（s02 生产级）是同一个思路。
"""

from __future__ import annotations

from pathlib import Path


class SkillLibrary:
    def __init__(self, root: Path) -> None:
        self.root = Path(root)

    def list(self) -> list[str]:
        if not self.root.is_dir():
            return []
        return [p.stem for p in sorted(self.root.glob("*.md"))]

    def summary_line(self, name: str) -> str:
        """技能的一句话简介（取 markdown 第一行标题），用于「目录」式低成本展示。"""
        body = self.get(name) or ""
        first = next((ln for ln in body.splitlines() if ln.strip()), name)
        return first.lstrip("# ").strip()

    def get(self, name: str) -> str | None:
        p = self.root / f"{name}.md"
        return p.read_text(encoding="utf-8") if p.is_file() else None
