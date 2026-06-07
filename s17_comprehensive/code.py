#!/usr/bin/env python3
"""
s17: Comprehensive — 把前面所有零件拼成一个「迷你 Codex」。

运行:
  python s17_comprehensive/code.py --demo    # 离线演示完整流水线
  python s17_comprehensive/code.py           # 交互模式

本章不引入新机制，而是把前面的拼起来，跑通一条完整的请求链：

  用户 → 注入 AGENTS.md(s06) → 模型(s09形状) → 工具调用
       → 审批闸门(s04) → 内核沙箱(s05) → 执行 → 事件(s10) → 结果回灌(s01)
  其中编辑文件用 apply_patch(s03)。

每一段都从对应章节精简搬运而来，用横幅标注来源。这就是"造载具"的总装车间。
"""

import platform
import shutil
import subprocess
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
from codexlib import Model, user_item, tool_output_item  # noqa: E402

WORKDIR = Path.cwd()
model = Model()
IS_MACOS = platform.system() == "Darwin"


# ═══════════════════════════════════════════════════════════
#  FROM s06（精简）：向上走收集 AGENTS.md，注入 system
# ═══════════════════════════════════════════════════════════

def load_agents_md(start: Path) -> str:
    docs, cur, root = [], start.resolve(), None
    chain = [cur] + list(cur.parents)
    for d in chain:                       # 找项目根（.git）
        if (d / ".git").exists():
            root = d
            break
    scan = [p for p in reversed(chain) if root is None or root in p.parents or p == root]
    for d in scan:
        f = d / "AGENTS.md"
        if f.exists():
            docs.append(f"--- {f} ---\n{f.read_text()[:2000]}")
    return "\n\n".join(docs)


def build_system() -> str:
    base = f"You are a mini-Codex coding agent at {WORKDIR}. Act, don't explain."
    agents = load_agents_md(WORKDIR)
    return base + (f"\n\n<project_instructions>\n{agents}\n</project_instructions>" if agents else "")


# ═══════════════════════════════════════════════════════════
#  FROM s04（精简）：审批闸门（4 档策略）
# ═══════════════════════════════════════════════════════════

SAFE = ("ls", "cat", "echo", "pwd", "git status", "git diff", "head", "tail", "wc")
RISKY = ("rm ", "sudo", "curl", "wget", "> /", "mkfs", ":(){")


def decide(command: str, policy: str) -> str:
    cmd = command.strip()
    risky = any(r in cmd for r in RISKY)
    safe = cmd.startswith(SAFE)
    if policy == "never":
        return "approve"
    if policy == "untrusted":
        return "ask"
    if policy == "on-request":
        return "approve" if (safe and not risky) else "ask"
    return "approve"  # on-failure：先跑，失败再说（这里简化为放行）


# ═══════════════════════════════════════════════════════════
#  FROM s05（精简）：macOS Seatbelt 沙箱
# ═══════════════════════════════════════════════════════════

def run_sandboxed(command: str) -> str:
    root = str(WORKDIR.resolve())
    if not IS_MACOS:
        return _raw(["/bin/sh", "-c", command]) + "  (非 macOS：未沙箱)"
    policy = ("(version 1)(deny default)(allow process-exec)(allow process-fork)"
              "(allow sysctl-read)(allow file-read*)"
              '(allow file-write-data (literal "/dev/null"))'
              '(allow file-write* (subpath (param "ROOT")))')
    return _raw(["/usr/bin/sandbox-exec", "-p", policy, "-D", f"ROOT={root}",
                 "--", "/bin/sh", "-c", command])


def _raw(args: list[str]) -> str:
    try:
        r = subprocess.run(args, cwd=WORKDIR, capture_output=True, text=True,
                           encoding="utf-8", errors="replace", timeout=120)
        out = (r.stdout + r.stderr).strip()
        return (("" if r.returncode == 0 else f"[exit {r.returncode}] ")
                + (out or "(no output)"))[:8000]
    except (subprocess.TimeoutExpired, OSError) as e:
        return f"Error: {e}"


# ═══════════════════════════════════════════════════════════
#  FROM s03（精简）：apply_patch（支持 Add / Delete / Update）
# ═══════════════════════════════════════════════════════════

def apply_patch(input: str) -> str:
    lines = input.strip("\n").split("\n")
    if lines[0].strip() != "*** Begin Patch":
        return "apply_patch 失败: 缺少 *** Begin Patch"
    out, i = [], 1
    while i < len(lines) and lines[i].strip() != "*** End Patch":
        ln = lines[i]
        if ln.startswith("*** Add File: "):
            path = (WORKDIR / ln[14:].strip()).resolve()
            i += 1
            body = []
            while i < len(lines) and not lines[i].startswith("*** "):
                if lines[i].startswith("+"):
                    body.append(lines[i][1:])
                i += 1
            path.parent.mkdir(parents=True, exist_ok=True)
            path.write_text("\n".join(body) + "\n")
            out.append(f"A {ln[14:].strip()}")
        elif ln.startswith("*** Delete File: "):
            (WORKDIR / ln[17:].strip()).unlink(missing_ok=True)
            out.append(f"D {ln[17:].strip()}")
            i += 1
        elif ln.startswith("*** Update File: "):
            path = (WORKDIR / ln[17:].strip()).resolve()
            i += 1
            old, new = [], []
            while i < len(lines) and not lines[i].startswith("*** "):
                raw = lines[i]
                if raw.startswith("@@"):
                    pass
                elif raw.startswith("-"):
                    old.append(raw[1:])
                elif raw.startswith("+"):
                    new.append(raw[1:])
                else:
                    old.append(raw[1:] if raw[:1] == " " else raw)
                    new.append(raw[1:] if raw[:1] == " " else raw)
                i += 1
            text = path.read_text().split("\n")
            for j in range(len(text) - len(old) + 1):
                if text[j:j + len(old)] == old:
                    text[j:j + len(old)] = new
                    break
            path.write_text("\n".join(text))
            out.append(f"M {ln[17:].strip()}")
        else:
            i += 1
    return "应用成功:\n" + "\n".join(out)


# ═══════════════════════════════════════════════════════════
#  FROM s10（精简）：事件
# ═══════════════════════════════════════════════════════════

def emit(kind: str, detail: str = "") -> None:
    icon = {"turn": "▶", "approval": "❓", "exec": "⏵", "patch": "✎",
            "msg": "💬", "denied": "⛔"}.get(kind, "·")
    print(f"\033[90m[event]\033[0m {icon} {kind:9} {detail[:140]}")


# ═══════════════════════════════════════════════════════════
#  总装：工具 = 审批闸门 → (沙箱 / apply_patch)
# ═══════════════════════════════════════════════════════════

POLICY = "on-request"


def tool_shell(command: str) -> str:
    emit("approval", f"shell: {command}")
    d = decide(command, POLICY)
    if d == "ask":
        ans = input(f"\033[33m批准 [{command}] ? (y/N) \033[0m").lower() if sys.stdin.isatty() else "n"
        if ans != "y":
            emit("denied", command)
            return "(被拒绝，未执行)"
    emit("exec", command)
    return run_sandboxed(command)


def tool_apply_patch(input: str) -> str:
    emit("patch", input.splitlines()[1] if "\n" in input else input)
    return apply_patch(input)


TOOLS = [
    {"name": "shell", "description": "Run a sandboxed, approved shell command.",
     "parameters": {"type": "object", "properties": {"command": {"type": "string"}},
                    "required": ["command"]}},
    {"name": "apply_patch", "description": "Apply a *** Begin Patch ... *** End Patch.",
     "parameters": {"type": "object", "properties": {"input": {"type": "string"}},
                    "required": ["input"]}},
]
HANDLERS = {"shell": tool_shell, "apply_patch": tool_apply_patch}


def run_turn(messages: list[dict]) -> None:
    emit("turn", "started")
    while True:
        resp = model.respond(messages, tools=TOOLS, system=build_system())
        messages += resp.output_items
        if not resp.tool_calls:
            if resp.text:
                emit("msg", resp.text)
            return
        for tc in resp.tool_calls:
            fn = HANDLERS.get(tc.name)
            output = fn(**tc.arguments) if fn else f"unknown tool {tc.name}"
            print(f"       {str(output)[:200]}")
            messages.append(tool_output_item(tc.call_id, output))


def demo() -> None:
    try:
        print("迷你 Codex 流水线：AGENTS.md → 模型 → 审批 → 沙箱 → 事件 → 回灌\n")
        print(f"当前 system 提示是否含 AGENTS.md: {'是' if 'project_instructions' in build_system() else '否'}\n")
        print("① 一条安全命令（on-request 自动放行）——")
        run_turn([user_item("执行 `echo mini-codex online`")])
        print("\n② 用 apply_patch 建个文件 ——")
        print(tool_apply_patch("*** Begin Patch\n*** Add File: _demo_workspace/built.txt\n"
                               "+assembled by s17\n*** End Patch"))
        print("内容:", (WORKDIR / "_demo_workspace/built.txt").read_text().strip())
    finally:
        shutil.rmtree(WORKDIR / "_demo_workspace", ignore_errors=True)  # 自清工作区，和其余各章一致


if __name__ == "__main__":
    if "--demo" in sys.argv:
        demo()
        sys.exit(0)
    print("s17: 迷你 Codex（输入 q 退出）\n")
    history: list[dict] = []
    while True:
        try:
            query = input("\033[36ms17 >> \033[0m")
        except (EOFError, KeyboardInterrupt):
            break
        if query.strip().lower() in ("q", "exit", ""):
            break
        history.append(user_item(query))
        run_turn(history)
        print()
