"""mini-codex —— 一个分模块的迷你 Codex。

把 learn-codex 18 章的机制拼成一个**能跑、分文件夹**的 harness（而不是塞进一个 .py）。
每个关注点独立成包：

    config/        分层配置 + 边界校验           (s16)
    model/         Responses 风格模型客户端 + 重试 (s09)
    protocol/      Event 总线（EQ）              (s10)
    memory/        AGENTS.md 分层注入 + 字节预算  (s06)
    tools/         工具注册表 + schema 校验 + 各工具 (s02 / s01 / s03 / s12)
    skills/        技能加载（按需注入能力包）
    hooks/         钩子注册表 + 信任/超时          (s13)
    safety/        审批 + 沙箱 + Guardian         (s04 / s05 / s14)
    persistence/   rollout 留底（append-only）    (s08)
    session/       回合循环（封顶+可中断）+ 压缩   (s01 / s07)
    agent.py       把以上一切装配成一台「载具」    (s17)

入口：python -m mini_codex --demo
"""

from .agent import Agent

__all__ = ["Agent"]
