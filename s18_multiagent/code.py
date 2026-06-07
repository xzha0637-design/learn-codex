#!/usr/bin/env python3
"""
s18: Multi-Agent — 当一个 agent 不够用：把「通信」做进协议里。

运行:
  python s18_multiagent/code.py --demo   # 离线：Lead 派 Worker 改文件、再派 Reviewer 审，全程带内通信
  python s18_multiagent/code.py          # 交互模式：你当 Lead，给 worker 派一句任务

本章 = s01 的回合循环（每个 agent 内部仍是一个 s01 loop，搬运精简版）
     + 新增（全在「agent 之间」）：
         ① AgentMessage —— 带内消息类型（author / recipient / cc / content / phase / encrypted）
         ② InterAgentRouter —— 按 AgentPath 投递，且**同时记进共享 rollout**（通信即历史）
         ③ agent 图谱 —— 记录 spawn 出来的父/子边
         ④ auto_review 子代理 —— Codex 标志性的「替我把关」分身

忠实对应 codex-rs（事实依据）：
  - 提交侧 Op::InterAgentCommunication { communication }     (protocol/src/protocol.rs:499)
  - struct InterAgentCommunication { author, recipient,
        other_recipients(=Cc), content, encrypted_content, trigger_turn }
                                                              (protocol.rs:626)
  - 通信变成一条 assistant 历史项（→ 进 rollout）             (protocol.rs:673 to_response_input_item)
  - 历史侧 ResponseItem::AgentMessage { author, recipient, content }
                                                              (protocol/src/models.rs:767)
  - enum MessagePhase { Commentary, FinalAnswer }            (models.rs:741)
  - 内容可加密 AgentMessageInputContent::EncryptedContent     (models.rs:720)
  - AgentPath 路径寻址                                        (protocol/src/agent_path.rs)
  - agent 图谱 ThreadSpawnEdgeStatus { Open, Closed }         (agent-graph-store/src/types.rs:7)
  - 加密身份（ed25519 + curve25519）                          (agent-identity/)
  - 审批官本身是子代理：ApprovalsReviewer = "user" | "auto_review" | "guardian_subagent"
                                                              (protocol/src/config_types.rs:159)

🆚 与 Claude Code 最鲜明的差异（⭐）：CC 把 agent 通信放在**文件收件箱**（带外、轮询、可 cat 围观，
   但假设同一台机器）；Codex 把通信做成**协议里的一等 AgentMessage**（带内、进 rollout、可加密、
   可跨机、多收件人 To/Cc）。WHY：CC 是 local-first，Codex 为云端 / 可审计 / 互操作而生。见 README。
"""

from __future__ import annotations

import shutil
import subprocess
import sys
from dataclasses import dataclass, field
from enum import Enum
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
from codexlib import Model, user_item, tool_output_item  # noqa: E402

WORKDIR = Path.cwd()


# ═══════════════════════════════════════════════════════════
#  FROM s01（搬运）：一个 agent = 「调模型 → 跑工具 → 回灌」的循环
#  多智能体里每个 agent 内部仍是这个循环；本章新增的全在 agent 之间。
# ═══════════════════════════════════════════════════════════

SHELL_TOOL = {
    "name": "shell",
    "description": "运行一条 shell 命令",
    "parameters": {"type": "object",
                   "properties": {"command": {"type": "string"}},
                   "required": ["command"]},
}


def run_shell(command: str) -> str:
    try:
        r = subprocess.run(command, shell=True, cwd=WORKDIR,
                           capture_output=True, text=True, timeout=10)
        return (r.stdout + r.stderr).strip() or "(no output)"
    except Exception as e:  # noqa: BLE001
        return f"error: {e}"


def run_turn(model: Model, messages: list[dict], system: str) -> str:
    """s01 的回合循环：模型要用工具就执行、回灌；不用就结束。"""
    while True:
        resp = model.respond(messages, tools=[SHELL_TOOL], system=system)
        messages += resp.output_items
        if not resp.tool_calls:
            return resp.text
        for tc in resp.tool_calls:
            out = run_shell(**tc.arguments) if tc.name == "shell" else "unknown tool"
            messages.append(tool_output_item(tc.call_id, out))


# ═══════════════════════════════════════════════════════════
#  NEW in s18 ①：带内消息类型 AgentMessage
#  对应真源码 struct InterAgentCommunication（protocol.rs:626）+ 落历史时的
#  ResponseItem::AgentMessage（models.rs:767）。它和「用户消息」「工具调用」一样，
#  是协议里的一等条目——不是旁路文件，也不是塞进对话的自由文本。
# ═══════════════════════════════════════════════════════════

class Phase(Enum):
    COMMENTARY = "commentary"        # 中途碎碎念（过程）
    FINAL_ANSWER = "final_answer"    # 最终答复（结论）
    # 真源码 enum MessagePhase { Commentary, FinalAnswer }（models.rs:741）


@dataclass
class AgentMessage:
    author: str                                   # AgentPath：谁发的
    recipient: str                                # AgentPath：主收件人（To）
    content: str
    phase: Phase = Phase.COMMENTARY
    cc: list[str] = field(default_factory=list)   # other_recipients（Cc），支持多收件人
    encrypted: bool = False                        # 内容是否加密（EncryptedContent）

    def shown(self) -> str:
        return "‹encrypted›" if self.encrypted else self.content


# ═══════════════════════════════════════════════════════════
#  NEW in s18 ②：InterAgentRouter —— Op::InterAgentCommunication 的教学版
#  一次 submit 做两件事：
#    (a) 把通信记进**共享 rollout**——带内的精髓：通信本身就是历史，可审计可重放
#        （对应 to_response_input_item() 把通信变成一条 assistant 历史项，protocol.rs:673）
#    (b) 按 AgentPath 投递给 recipient + 每个 cc 的收件箱
# ═══════════════════════════════════════════════════════════

class InterAgentRouter:
    def __init__(self) -> None:
        self.agents: dict[str, Agent] = {}
        self.rollout: list[dict] = []                  # 共享持久记录（通信即历史）
        self.graph: list[tuple[str, str, str]] = []    # agent 图谱：(parent, child, status)

    def register(self, agent: Agent) -> None:
        self.agents[agent.path] = agent
        agent.router = self

    def spawn(self, parent: str, child: Agent) -> None:
        # 记一条父/子边到 agent 图谱（对应 agent-graph-store 的 ThreadSpawnEdgeStatus::Open）
        self.register(child)
        self.graph.append((parent, child.path, "open"))
        print(f"  ⑂ spawn: {parent} ──▶ {child.path}（图谱记一条边，status=open）")

    def submit(self, msg: AgentMessage) -> None:
        # 这一步 = 提交一个 Op::InterAgentCommunication（protocol.rs:499）
        self.rollout.append({"type": "agent_message",
                             "author": msg.author, "recipient": msg.recipient,
                             "cc": list(msg.cc), "phase": msg.phase.value,
                             "content": msg.shown()})
        cc = f"  +cc {msg.cc}" if msg.cc else ""
        print(f"  ✉️  {msg.author} ──▶ {msg.recipient}{cc}  [{msg.phase.value}]  {msg.shown()!r}")
        print(f"       ↳ 记入共享 rollout（第 {len(self.rollout)} 项）——通信即历史，可审计可重放")
        for tgt in [msg.recipient, *msg.cc]:        # 投递给 To + 每个 Cc
            if tgt in self.agents:
                self.agents[tgt].inbox.append(msg)


# ═══════════════════════════════════════════════════════════
#  NEW in s18 ③：Agent —— 有 AgentPath 身份、有收件箱
# ═══════════════════════════════════════════════════════════

@dataclass
class Agent:
    path: str
    role: str
    router: InterAgentRouter = None
    inbox: list = field(default_factory=list)

    def send(self, recipient: str, content: str, phase: Phase = Phase.COMMENTARY,
             cc: list[str] | None = None, encrypted: bool = False) -> None:
        self.router.submit(AgentMessage(self.path, recipient, content, phase,
                                        cc or [], encrypted))


class Worker(Agent):
    """真正干活的 agent —— 内部就是一个 s01 回合（调模型 + shell 工具）。"""

    def handle_inbox(self, model: Model) -> None:
        for msg in self.inbox:
            if msg.recipient != self.path:
                continue  # 这条是 cc 给我的，围观即可，不行动
            self.send("lead", "收到任务，开工。", Phase.COMMENTARY)
            run_turn(model, [user_item(msg.content)],
                     system=f"You are {self.path}, a worker agent. Act, don't explain.")
            artifact = WORKDIR / "_demo_workspace" / "artifact.txt"
            result = artifact.read_text().strip() if artifact.exists() else "(无产物)"
            self.send("lead", f"完成。产物内容：{result!r}", Phase.FINAL_ANSWER)
        self.inbox.clear()


class Reviewer(Agent):
    """Codex 标志性的「替我把关」子代理（auto_review）。

    真 auto_review 是一个被仔细 prompt 的 LLM 子代理（config_types.rs:159），fork 上下文、
    搜集材料、按风险框架判批不批。这里像 s14 那样用一条保守规则**模拟**它的判断。
    """

    def handle_inbox(self) -> None:
        for msg in self.inbox:
            if msg.recipient != self.path:
                continue
            risky = any(k in msg.content for k in ("rm -rf", "curl ", "sudo", " /etc"))
            verdict = "REJECT" if risky else "APPROVE"
            why = "发现危险操作" if risky else "改动安全，可合入"
            self.send("lead", f"{verdict}：{why}", Phase.FINAL_ANSWER)
        self.inbox.clear()


# ═══════════════════════════════════════════════════════════
#  一次完整的「组队协作」：Lead → Worker → Reviewer，全程带内通信
# ═══════════════════════════════════════════════════════════

def collaborate(model: Model, task_cmd: str) -> None:
    ws = WORKDIR / "_demo_workspace"
    ws.mkdir(exist_ok=True)
    try:
        router = InterAgentRouter()
        lead = Agent("lead", "领队")
        router.register(lead)

        print("① Lead 派生 Worker，并下发任务（commentary 阶段）——")
        worker = Worker("lead/worker", "工人")
        router.spawn("lead", worker)
        lead.send("lead/worker", task_cmd, Phase.COMMENTARY)
        worker.handle_inbox(model)

        print("\n② Lead 派生 Reviewer，发审查请求，并 cc 给 Worker（演示 To + Cc 多收件人）——")
        reviewer = Reviewer("lead/reviewer", "审查员")
        router.spawn("lead", reviewer)
        worker_result = next(m for m in reversed(router.rollout)
                             if m["author"] == "lead/worker")
        lead.send("lead/reviewer", f"请审查这次改动：{worker_result['content']}",
                  Phase.FINAL_ANSWER, cc=["lead/worker"])
        reviewer.handle_inbox()

        print("\n③ Lead 读取审查结论，收尾——")
        verdict = next(m for m in reversed(router.rollout)
                       if m["author"] == "lead/reviewer")
        print(f"  Lead 收到裁决：{verdict['content']}")

        print("\n④ 演示：带内消息可加密（EncryptedContent）——中间人看不到明文")
        lead.send("lead/worker", "归档密钥 sk-demo-7f3a（仅 worker 可解）",
                  Phase.COMMENTARY, encrypted=True)

        print("\n── agent 图谱（谁派生了谁；对应 agent-graph-store）──")
        for parent, child, status in router.graph:
            print(f"   {parent} ──▶ {child}  [{status}]")

        print("\n── 共享 rollout（每条 agent 通信都在此留痕——带内通信的精髓）──")
        for i, it in enumerate(router.rollout, 1):
            cc = f" cc={it['cc']}" if it["cc"] else ""
            print(f"   {i}. [{it['phase']:<12}] {it['author']} → {it['recipient']}{cc}: {it['content']}")

        print("\n对照 Claude Code：以上每条会是 ~/.claude/teams/<t>/inboxes/*.json 里"
              "读完即删的文件；")
        print("Codex 把它们留在 rollout 里——可重放、可审计、可加密、可跨机、可多收件人。")
    finally:
        shutil.rmtree(ws, ignore_errors=True)  # 自清工作区，和其余各章一致


def demo() -> None:
    model = Model()
    print("\n场景：Lead 派 Worker 改个文件，再派 Reviewer 审一下——三个 agent 全程带内通信。\n")
    collaborate(
        model,
        "在 _demo_workspace 里执行 "
        "`echo 'hello from the worker' > _demo_workspace/artifact.txt`",
    )


if __name__ == "__main__":
    if "--demo" in sys.argv:
        demo()
        sys.exit(0)

    print("s18: Multi-Agent（你当 Lead，输入给 worker 的一句任务；q 退出）\n")
    model = Model()
    while True:
        try:
            line = input("\033[36mlead >> \033[0m")
        except (EOFError, KeyboardInterrupt):
            break
        if line.strip().lower() in ("q", "exit", ""):
            break
        collaborate(model, line)
        print()
