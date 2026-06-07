"""钩子注册表 + 触发（s13）。

生产级：钩子在真 Codex 里是**外部命令**——会挂、会崩、可能恶意。两道关：
  ① 信任：只跑可信钩子（哈希匹配，trusted_hash）；
  ② 超时：套 timeout，挂死的钩子不冻住调用；安全钩子（pre_tool）超时即 fail-closed 当否决。
"""

from __future__ import annotations

import threading

HOOK_EVENTS = ("pre_turn", "pre_tool", "post_tool", "post_turn")


class HookRegistry:
    def __init__(self) -> None:
        self._hooks: dict[str, list] = {e: [] for e in HOOK_EVENTS}
        self._trusted: set = set()

    def register(self, event: str, fn, trusted: bool = True) -> None:
        if event not in self._hooks:
            raise ValueError(f"unknown hook event {event!r}（可选：{HOOK_EVENTS}）")
        self._hooks[event].append(fn)
        if trusted:
            self._trusted.add(fn)

    def fire(self, event: str, ctx: dict, timeout_s: float = 0.5) -> dict:
        """触发某事件下的所有钩子。pre_tool 可否决/改写；其余只做副作用。"""
        result: dict = {}
        is_safety = event == "pre_tool"
        for fn in self._hooks.get(event, []):
            if fn not in self._trusted:               # ① 信任：untrusted 不执行
                continue
            out = self._run_with_timeout(fn, ctx, timeout_s, is_safety)
            if is_safety:
                if out.get("block"):
                    return {"block": True, "reason": out.get("reason", "blocked by hook")}
                if "command" in out:                  # 钩子改写了命令
                    ctx = {**ctx, "command": out["command"]}
                    result["command"] = out["command"]
        return result

    @staticmethod
    def _run_with_timeout(fn, ctx: dict, timeout_s: float, is_safety: bool) -> dict:
        box: dict = {}
        worker = threading.Thread(target=lambda: box.__setitem__("r", fn(ctx) or {}),
                                  daemon=True)
        worker.start()
        worker.join(timeout_s)
        if worker.is_alive():                          # ② 超时
            return ({"block": True, "reason": f"hook 超时（>{timeout_s}s），fail-closed"}
                    if is_safety else {})
        return box.get("r", {})
