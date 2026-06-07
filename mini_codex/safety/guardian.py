"""Guardian（s14）：自动风险评估器，插在「问用户」之前。

生产级：fail-closed（出错/超时一律判拒）+ 熔断器（一个回合连续拒到阈值就停，防空转）。
风险四档映射成动作：low→放行、critical→拒、medium/high→升级给审批门。
"""

from __future__ import annotations

MAX_CONSECUTIVE_DENIALS_PER_TURN = 3   # 熔断阈值（对应 MAX_CONSECUTIVE_GUARDIAN_DENIALS_PER_TURN）

_CRITICAL = ("rm -rf", "curl", "| sh", "| bash", "sudo", "mkfs", ":(){", "dd if=")
_HIGH = ("rm ", "git push", "npm publish", "delete", "drop table")
_MUTATING = ("write", "apply_patch", "mv ", "cp ", "touch", "mkdir")


def assess(action: str) -> dict:
    """评估一个动作，返回 {risk, outcome, reason}。outcome ∈ {auto_allow, escalate, auto_deny}。"""
    a = action.lower()
    if any(k in a for k in _CRITICAL):
        return {"risk": "critical", "outcome": "auto_deny", "reason": "命中灾难级模式"}
    if any(k in a for k in _HIGH):
        return {"risk": "high", "outcome": "escalate", "reason": "可能造成破坏"}
    if any(k in a for k in _MUTATING):
        return {"risk": "medium", "outcome": "escalate", "reason": "会改动文件"}
    return {"risk": "low", "outcome": "auto_allow", "reason": "只读 / 无害"}


class GuardianGate:
    def __init__(self) -> None:
        self.consecutive_denials = 0

    def review(self, action: str) -> dict:
        """带 fail-closed + 熔断的评审。返回 assess() 结果（出错则 fail-closed 判 deny）。"""
        if self.consecutive_denials >= MAX_CONSECUTIVE_DENIALS_PER_TURN:
            return {"risk": "critical", "outcome": "auto_deny",
                    "reason": f"熔断：本回合已连续拒绝 {self.consecutive_denials} 次"}
        try:
            verdict = assess(action)
        except Exception as e:  # noqa: BLE001 — fail-closed
            verdict = {"risk": "critical", "outcome": "auto_deny", "reason": f"评审出错，fail-closed：{e}"}
        if verdict["outcome"] == "auto_deny":
            self.consecutive_denials += 1
        else:
            self.consecutive_denials = 0
        return verdict
