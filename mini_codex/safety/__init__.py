"""safety —— 三层防线：审批（s04）/ 沙箱（s05）/ Guardian（s14）。

它们正交、叠加：审批 = 用户同不同意（应用层）；沙箱 = 内核让不让碰（内核层）；
Guardian = 没人时派个 AI 先判风险。默认全部朝「拒」倒（fail-closed）。
"""

from .approval import ApprovalGate, Decision
from .guardian import GuardianGate
from .sandbox import build_seatbelt_policy, run_sandboxed

__all__ = ["ApprovalGate", "Decision", "GuardianGate", "run_sandboxed",
           "build_seatbelt_policy"]
