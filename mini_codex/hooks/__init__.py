"""hooks —— 钩子注册表（s13），在回合关键时刻挂钩；带信任 + 超时。"""

from .builtin import block_rm, log_post_turn
from .registry import HOOK_EVENTS, HookRegistry

__all__ = ["HookRegistry", "HOOK_EVENTS", "block_rm", "log_post_turn"]
