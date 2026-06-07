"""审批门（s04）：按策略裁决 + 会话缓存 + BANNED_PREFIX 刹车。

生产级：用户答复是带记忆的决定（ApprovedForSession 进缓存），但解释器/shell 前缀
永不许被学成永久放行（BANNED_PREFIX），否则审批被架空。
"""

from __future__ import annotations

from enum import Enum

KNOWN_SAFE = {"ls", "cat", "echo", "pwd", "head", "tail", "grep", "wc", "stat"}
# 节选自真源码 BANNED_PREFIX_SUGGESTIONS（exec_policy.rs:52）。
BANNED_PREFIXES = {"python", "python3", "py", "bash", "sh", "zsh", "pwsh", "git"}


class Decision(Enum):
    APPROVE = "approve"
    ASK = "ask"
    REJECT = "reject"


def is_dangerous(command: str) -> bool:
    parts = command.split()
    head = parts[0] if parts else ""
    if head == "rm" and any(a in ("-f", "-rf", "-fr") for a in parts[1:]):
        return True
    if head == "sudo":
        return True
    if "|" in command and any(s in command for s in ("sh", "bash", "zsh")) \
            and any(d in command for d in ("curl", "wget")):
        return True
    return False


class ApprovalGate:
    def __init__(self, policy: str = "on-request") -> None:
        self.policy = policy
        self.session_allow: set[str] = set()

    @staticmethod
    def _prefix(command: str) -> str:
        parts = command.split()
        return parts[0] if parts else ""

    def decide(self, command: str) -> Decision:
        if self._prefix(command) in self.session_allow:
            return Decision.APPROVE                       # 会话缓存命中
        safe = self._prefix(command) in KNOWN_SAFE
        danger = is_dangerous(command)
        if self.policy == "untrusted":
            return Decision.APPROVE if safe else Decision.ASK
        if self.policy == "on-request":
            return Decision.APPROVE if (safe or not danger) else Decision.ASK
        if self.policy == "on-failure":
            return Decision.ASK if danger else Decision.APPROVE
        if self.policy == "never":
            return Decision.REJECT if danger else Decision.APPROVE
        raise ValueError(f"unknown approval policy: {self.policy!r}")

    def remember(self, command: str) -> str:
        """ApprovedForSession：把前缀记进缓存；BANNED 前缀拒绝学。"""
        p = self._prefix(command)
        if p in BANNED_PREFIXES:
            return f"✗ 拒绝把 `{p}` 学成永久放行（BANNED_PREFIX）"
        self.session_allow.add(p)
        return f"✓ 已记住 `{p}`，本会话自动放行"
