"""Agent —— 把所有零件装配成一台「载具」（s17 的流水线落到代码里）。

一次工具调用穿过的关卡（`_handle_tool_call`）：
    钩子 pre_tool（否决/改写）→ Guardian 风险评估 → 审批门 → 工具注册表（schema 校验 + 沙箱执行）→ rollout 留底
模型只出现在「想做什么」那一步；其余每一道关卡都是 harness。每一步都 `emit` 成事件给前端。
"""

from __future__ import annotations

from pathlib import Path

from .config import load_config, resolve_config
from .hooks import HookRegistry, block_rm
from .memory import collect_agents_md
from .model import Model
from .persistence import Rollout
from .protocol import EventBus, EventType
from .safety import ApprovalGate, Decision, GuardianGate
from .session import compact, run_turn
from .skills import default_library
from .tools import ToolContext, build_default_registry


def _summarize_patch(patch: str) -> str:
    ops = [ln.replace("*** ", "").strip() for ln in patch.split("\n")
           if ln.startswith("*** ") and "File:" in ln]
    return "; ".join(ops) or "patch"


def _default_ask_user(action: str) -> bool:
    """非交互 demo 的审批回调：本应弹给用户，这里自动放行并标注。真 CLI 会真问。"""
    print(f"    🔐 [would prompt user] 批准执行：{action} → demo 自动放行")
    return True


class Agent:
    def __init__(self, workspace, profile=None, overrides=None, ask_user=None) -> None:
        self.config = resolve_config(load_config(), profile, overrides)
        self.workspace = Path(workspace).resolve()
        self.model = Model(self.config.model)
        self.tools = build_default_registry()
        self.ctx = ToolContext(workspace=self.workspace)
        self.hooks = HookRegistry()
        self.hooks.register("pre_tool", block_rm)            # 内置一个否决 rm 的钩子
        self.approval = ApprovalGate(self.config.approval_policy)
        self.guardian = GuardianGate()
        self.skills = default_library()
        self.bus = EventBus()
        self.rollout = Rollout(self.workspace / ".mini_codex" / "rollout.jsonl")
        self.ask_user = ask_user or _default_ask_user

    def build_system(self) -> str:
        """组装 system：基底 + AGENTS.md 分层注入（s06）+ 技能目录（按需 load）。"""
        parts = [
            f"You are mini-codex, a coding agent in {self.workspace}.",
            f"Config: approval={self.config.approval_policy}, sandbox={self.config.sandbox_mode}.",
        ]
        agents = collect_agents_md(self.workspace)
        if agents:
            parts.append("# Project memory (AGENTS.md, 分层注入)\n" + agents)
        skills = self.skills.list()
        if skills:
            catalogue = "\n".join(f"- {s}: {self.skills.summary_line(s)}" for s in skills)
            parts.append("# Available skills（按需 load）\n" + catalogue)
        return "\n\n".join(parts)

    def _action_string(self, tc) -> str:
        if tc.name == "shell":
            return tc.arguments.get("command", "")
        if tc.name == "apply_patch":
            return "apply_patch: " + _summarize_patch(tc.arguments.get("input", ""))
        return f"{tc.name} {tc.arguments}"

    def _handle_tool_call(self, tc) -> str:
        """一次工具调用的安全流水线（这就是 s17 的那条传送带）。"""
        action = self._action_string(tc)

        # ① 钩子 pre_tool：可否决 / 可改写
        gate = self.hooks.fire("pre_tool", {"tool": tc.name, "command": action})
        if gate.get("block"):
            self.bus.emit(EventType.BLOCKED, by="hook", tool=tc.name, reason=gate["reason"])
            self.rollout.record("blocked", by="hook", tool=tc.name, reason=gate["reason"])
            return f"[blocked by hook] {gate['reason']}"

        # ② Guardian：风险评估（low→放行 / critical→拒 / medium·high→升级）
        verdict = self.guardian.review(action)
        self.bus.emit(EventType.GUARDIAN, action=action[:50],
                      risk=verdict["risk"], outcome=verdict["outcome"])
        if verdict["outcome"] == "auto_deny":
            self.rollout.record("denied", by="guardian", tool=tc.name, reason=verdict["reason"])
            return f"[guardian auto-deny] {verdict['reason']}"

        # ③ 审批门：被 Guardian 升级的，交给审批策略 / 用户
        if verdict["outcome"] == "escalate":
            decision = self.approval.decide(action)
            self.bus.emit(EventType.APPROVAL, action=action[:50], decision=decision.value)
            if decision == Decision.REJECT:
                self.rollout.record("denied", by="approval", tool=tc.name)
                return f"[approval reject] {action}"
            if decision == Decision.ASK and not self.ask_user(action):
                self.rollout.record("denied", by="user", tool=tc.name)
                return f"[user denied] {action}"

        # ④ 执行：工具注册表负责 schema 校验 + 沙箱（shell 工具内部包了 run_sandboxed）
        self.bus.emit(EventType.TOOL_BEGIN, tool=tc.name)
        output = self.tools.dispatch(tc.name, tc.arguments, self.ctx)
        self.bus.emit(EventType.TOOL_END, tool=tc.name, output=str(output)[:80])
        self.rollout.record("tool", name=tc.name, output=str(output)[:200])
        return output

    def run(self, query: str, max_steps: int = 20, cancelled=None) -> list[dict]:
        self.rollout.record("user", text=query)
        messages = compact([{"type": "message", "role": "user", "content": query}])
        run_turn(self.model, messages, self.build_system(), self.tools.specs(),
                 self._handle_tool_call, self.bus, max_steps=max_steps, cancelled=cancelled)
        return messages
