"""内置示例钩子。"""

from __future__ import annotations


def block_rm(ctx: dict) -> dict:
    """pre_tool：任何含 `rm` 的 shell 命令都否决（否决理由会回灌给模型）。"""
    if "rm" in (ctx.get("command") or "").split():
        return {"block": True, "reason": "policy: `rm` is blocked by the block_rm hook"}
    return {}


def log_post_turn(ctx: dict) -> dict:
    """post_turn：回合收尾打一行（这里返回空，副作用由调用方打印）。"""
    return {}
