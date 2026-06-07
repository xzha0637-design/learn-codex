#!/usr/bin/env python3
"""
s05: Sandbox — 用操作系统级沙箱关住每一条命令（Codex 的安全招牌）。

运行:
  python s05_sandbox/code.py --demo    # 不需要模型：演示「写工作区内 OK / 写区外被拒」
  python s05_sandbox/code.py           # 交互模式：shell 工具自动跑在沙箱里

本章 = s01 的回合循环 + shell 工具（搬运）
     + 新增：把每条命令包进 macOS Seatbelt（/usr/bin/sandbox-exec）。
       策略从 (deny default) 起步，只放开「读到处可以、写仅限可写根」。

忠实复刻 codex-rs/sandboxing：
  - 可执行路径 /usr/bin/sandbox-exec        (seatbelt.rs: MACOS_PATH_TO_SEATBELT_EXECUTABLE)
  - 策略 (deny default) 起步                 (seatbelt_base_policy.sbpl)
  - 可写根用 -D 参数注入、(param "...") 引用 (seatbelt.rs: create_seatbelt_command_args)
Linux 上 Codex 改用 Landlock+seccomp / bwrap（codex-rs/linux-sandbox），本 demo 在非 macOS 退回无沙箱。

与 Claude Code 的根本差异：Claude Code 靠「审批弹窗 + 工作区路径校验」（应用层）控制风险；
Codex 把命令交给内核强制隔离 —— 即使模型想 `rm -rf ~`，内核也会直接拒绝。见 README「🆚」。
"""

import platform
import shutil
import subprocess
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
from codexlib import Model, user_item, tool_output_item  # noqa: E402

WORKDIR = Path.cwd()
SYSTEM = (f"You are Codex at {WORKDIR}. Use shell to act; everything you run is sandboxed "
          "to the workspace. Act, don't explain.")
model = Model()

IS_MACOS = platform.system() == "Darwin"
SEATBELT = "/usr/bin/sandbox-exec"


# ═══════════════════════════════════════════════════════════
#  构造 Seatbelt 策略（SBPL，Scheme 方言）
#  起手式永远是 (deny default) —— 默认什么都不许，再逐条放开。
# ═══════════════════════════════════════════════════════════

def build_seatbelt_policy(n_writable_roots: int) -> str:
    lines = [
        "(version 1)",
        "(deny default)",                              # 关键：默认全拒
        "(allow process-exec)",
        "(allow process-fork)",
        "(allow sysctl-read)",
        "(allow file-read*)",                          # 读：到处都行（Codex 默认也大多放开读）
        '(allow file-write-data (literal "/dev/null"))',
    ]
    # 写：只放开「可写根」。每个根由 -D 注入为 WRITABLE_ROOT_i 参数。
    for i in range(n_writable_roots):
        lines.append(f'(allow file-write* (subpath (param "WRITABLE_ROOT_{i}")))')
    return "\n".join(lines)


def run_sandboxed(command: str, writable_roots: list[str]) -> str:
    roots = [str(Path(r).resolve()) for r in writable_roots]

    if not IS_MACOS:
        out = _raw_run(["/bin/sh", "-c", command])
        return f"[非 macOS：Seatbelt 不可用，已无沙箱执行] {out}"

    policy = build_seatbelt_policy(len(roots))
    args = [SEATBELT, "-p", policy]
    for i, root in enumerate(roots):
        args += ["-D", f"WRITABLE_ROOT_{i}={root}"]
    args += ["--", "/bin/sh", "-c", command]
    return _raw_run(args)


def _raw_run(args: list[str]) -> str:
    try:
        r = subprocess.run(args, cwd=WORKDIR, capture_output=True, text=True,
                           encoding="utf-8", errors="replace", timeout=120)
        out = (r.stdout + r.stderr).strip()
        tag = "" if r.returncode == 0 else f"[exit {r.returncode}] "
        return tag + (out if out else "(no output)")
    except (subprocess.TimeoutExpired, OSError) as e:
        return f"Error: {e}"


# ═══════════════════════════════════════════════════════════
#  FROM s01（搬运并改造）：shell 工具现在默认跑在沙箱里
# ═══════════════════════════════════════════════════════════

def run_shell(command: str) -> str:
    return run_sandboxed(command, writable_roots=[str(WORKDIR)])


TOOLS = [{
    "name": "shell",
    "description": "Run a shell command. It is sandboxed: writes are confined to the workspace.",
    "parameters": {"type": "object",
                   "properties": {"command": {"type": "string"}}, "required": ["command"]},
}]
HANDLERS = {"shell": run_shell}


def run_turn(messages: list[dict]) -> None:
    while True:
        resp = model.respond(messages, tools=TOOLS, system=SYSTEM)
        messages += resp.output_items
        if not resp.tool_calls:
            if resp.text:
                print(f"\n\033[32m{resp.text}\033[0m")
            return
        for tc in resp.tool_calls:
            print(f"\033[33m> {tc.name} {tc.arguments}\033[0m")
            handler = HANDLERS.get(tc.name)
            output = handler(**tc.arguments) if handler else f"unknown tool: {tc.name}"
            print(str(output)[:400])
            messages.append(tool_output_item(tc.call_id, output))


# ═══════════════════════════════════════════════════════════
#  --demo：不依赖模型，直观演示沙箱「放行 vs 拦截」
# ═══════════════════════════════════════════════════════════

def demo() -> None:
    ws = Path("sandbox_demo").resolve()
    ws.mkdir(exist_ok=True)
    escape = Path.home() / "codex_escape_test.txt"
    escape.unlink(missing_ok=True)

    try:
        print(f"可写根 = {ws}")
        if not IS_MACOS:
            print("⚠️  当前不是 macOS，Seatbelt 不可用，下面的拦截不会真正生效。\n")

        print("\n生成的 Seatbelt 策略（这就是「关」的全部依据，注意它的结构）：")
        print("\033[90m" + build_seatbelt_policy(1) + "\033[0m")
        print("  ↑ (deny default) 先全拒 → 再逐条放行：读(file-read*)放开、写(file-write*)只给可写根；")
        print("    **通篇没有一条 network-* 允许 → 出网默认被拒**（不只防越界写，更防偷偷外传）。\n")

        print("① 写【工作区内】—— 应当成功：")
        print(run_sandboxed(f"touch {ws}/inside.txt && echo '  -> 写成功'", [str(ws)]))

        print("\n② 写【工作区外】(home 目录) —— 应当被内核拒绝：")
        print(run_sandboxed(f"touch {escape} && echo '  -> 居然写成功了（不该发生）'", [str(ws)]))

        print(f"\n实际检查：{escape} 存在吗？ -> {escape.exists()}")
        print("（macOS 上它应当不存在：sandbox-exec 在内核层挡住了越界写。）")
    finally:
        escape.unlink(missing_ok=True)
        shutil.rmtree(ws, ignore_errors=True)   # 自清工作区，和其余各章一致


if __name__ == "__main__":
    if "--demo" in sys.argv:
        demo()
        sys.exit(0)
    print("s05: Sandbox（输入 q 退出；shell 命令都被关在工作区里）\n")
    history: list[dict] = []
    while True:
        try:
            query = input("\033[36ms05 >> \033[0m")
        except (EOFError, KeyboardInterrupt):
            break
        if query.strip().lower() in ("q", "exit", ""):
            break
        history.append(user_item(query))
        run_turn(history)
        print()
