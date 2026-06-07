"""rollout：会话留底（s08）。

生产级：**append-only** —— 每个 item 追加一行并 flush，从不回改。崩溃最多丢**最后一行**
（写到一半那条），replay 时跳过损坏的尾行即可。这份底稿是 resume / 审计的单一事实源。
"""

from __future__ import annotations

import json
from pathlib import Path


class Rollout:
    def __init__(self, path: Path) -> None:
        self.path = Path(path)
        self.path.parent.mkdir(parents=True, exist_ok=True)
        self._count = 0

    def record(self, kind: str, **payload) -> None:
        """追加一条 item 并立即 flush（断电也不丢已确认的）。"""
        line = json.dumps({"kind": kind, **payload}, ensure_ascii=False)
        with self.path.open("a", encoding="utf-8") as f:
            f.write(line + "\n")
            f.flush()
        self._count += 1

    def replay(self) -> list[dict]:
        """逐行回放；跳过崩溃残留的损坏尾行（append-only 的红利）。"""
        if not self.path.exists():
            return []
        items = []
        for line in self.path.read_text(encoding="utf-8").splitlines():
            try:
                items.append(json.loads(line))
            except json.JSONDecodeError:
                pass
        return items

    def __len__(self) -> int:
        return self._count
