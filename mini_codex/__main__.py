"""入口：python -m mini_codex [--demo]

离线 mock 跑一条完整请求穿过整条流水线（配置 → AGENTS.md 注入 → 模型 → 钩子 → Guardian →
审批 → 沙箱执行 → rollout 留底 → 收尾），把每一道关卡 emit 的事件打印出来。
"""

from __future__ import annotations

import shutil
import sys
import tempfile
from pathlib import Path

from .agent import Agent
from .protocol import EventType

_ICONS = {
    EventType.TURN_STARTED: "▶  turn started",
    EventType.REASONING: "   💭 reasoning",
    EventType.GUARDIAN: "   🛡  guardian",
    EventType.APPROVAL: "   🔐 approval",
    EventType.BLOCKED: "   ⛔ blocked",
    EventType.TOOL_BEGIN: "   🔧 tool begin",
    EventType.TOOL_END: "   ✅ tool end",
    EventType.MESSAGE: "   💬 message",
    EventType.COMPACTION: "   🗜  compaction",
    EventType.TURN_COMPLETE: "■  turn complete",
    EventType.ERROR: "   ⚠  error",
}


def _print_event(ev) -> None:
    label = _ICONS.get(ev.type, str(ev.type))
    data = " ".join(f"{k}={v}" for k, v in ev.data.items())
    print(f"{label}  {data}".rstrip())


def demo() -> None:
    ws = Path(tempfile.mkdtemp(prefix="mini_codex_"))
    try:
        # 一个项目级 AGENTS.md，演示分层注入。
        (ws / "AGENTS.md").write_text("# 本项目规矩\n- 用中文回复\n- 所有产物放在工作区内\n",
                                      encoding="utf-8")

        agent = Agent(ws, profile="safe")
        agent.bus.subscribe(_print_event)

        print(f"工作区：{ws}")
        print(f"生效配置：model={agent.config.model}  "
              f"approval={agent.config.approval_policy}  sandbox={agent.config.sandbox_mode}")
        print(f"已注册工具：{[t['name'] for t in agent.tools.specs()]}")
        print(f"可用技能：{agent.skills.list()}")
        print("=" * 68)
        agent.run("在工作区创建 greeting.txt，写入一句问候。")
        print("=" * 68)

        artifact = ws / "greeting.txt"
        print("产物 greeting.txt：", repr(artifact.read_text()) if artifact.exists() else "(未创建)")
        print(f"rollout：写了 {len(agent.rollout)} 条，replay 回放出 {len(agent.rollout.replay())} 条")
        print("\n这一条请求，穿过了 配置→记忆→模型→钩子→Guardian→审批→沙箱→工具→留底 "
              "九道关卡——模型只占其中一步。")
    finally:
        shutil.rmtree(ws, ignore_errors=True)


if __name__ == "__main__":
    if "--demo" in sys.argv or True:   # 离线 mock：交互意义不大，统一跑 demo
        demo()
