#!/usr/bin/env python3
"""
s06: AGENTS.md — 从 cwd 向上走到项目根，分层收集 AGENTS.md 注入指令。

运行:
  python s06_agents_md/code.py            # 交互模式（输入一个目录，看从那里发现的 AGENTS.md）
  python s06_agents_md/code.py --demo      # 自建临时目录树演示发现 + 拼接，跑完自动清理

默认 backend=mock，无需任何 key；本章 --demo 完全离线、不调用模型。

本章 = s01 搬运的回合循环 + 本章新增的「AGENTS.md 分层发现 + 注入」。
新增机制全部内联在下方 `# ═══ NEW in s06 ═══` 横幅下，可单文件通读。
依据真源码：codex-rs/core/src/agents_md.rs（AgentsMdManager / read_agents_md 向上走 /
分隔符 / AGENTS.override.md 兜底 / project_doc_max_bytes / project_root_markers）。
"""

import shutil
import subprocess
import sys
import tempfile
from pathlib import Path

# 仓库根加入 import 路径，复用共享模型模块（mock 后端离线可跑）。
sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
from codexlib import Model, user_item, tool_output_item  # noqa: E402

# ═══════════════════════════════════════════════════════════
#  NEW in s06：AGENTS.md 分层发现 + 注入
#
#  机制（对照 agents_md.rs 的模块级注释）：
#    1. 从 cwd 向上走，直到遇到「项目根标记」（默认 .git 目录）。
#       走不到标记就只看 cwd 自己；不会越过项目根。
#    2. 从项目根「向下」到 cwd（含两端），逐层收集每个目录里的 AGENTS.md。
#    3. 按 根→cwd 顺序拼接其内容，根在最前。
#    4. 拼成一个 <user_instructions> 块，注入进本回合的 instructions（system）。
#
#  教学版用纯文件系统操作复刻这条「向上找根、向下收集」的路径。
# ═══════════════════════════════════════════════════════════

# 候选文件名：override 优先于规范名（对照 agents_md.rs candidate_filenames，
# 真源码顺序为 AGENTS.override.md → AGENTS.md，命中一个即停）。
AGENTS_FILENAMES = ["AGENTS.override.md", "AGENTS.md"]

# 项目根标记：真源码默认就是 [".git"]（config/src/project_root_markers.rs）。
PROJECT_ROOT_MARKERS = [".git"]

# 项目文档字节上限：真源码默认 32 * 1024（DEFAULT_PROJECT_DOC_MAX_BYTES）。
# 超出预算就从「剩余额度」处截断，且越靠 cwd 的文件越可能被裁。
PROJECT_DOC_MAX_BYTES = 32 * 1024

# 拼接分隔符：真源码 AGENTS_MD_SEPARATOR（仅在 user/internal → project 的过渡处插入；
# 教学版统一用它分隔每个被发现的 project 文档，效果上等价于「能看到边界」）。
AGENTS_MD_SEPARATOR = "\n\n--- project-doc ---\n\n"


def find_project_root(start: Path) -> Path | None:
    """从 start 向上走，遇到任一根标记即返回该祖先目录；走不到返回 None。

    对照 agents_md.rs：遍历 dir.ancestors()，逐个祖先检查 join(marker) 是否存在。
    空标记列表会禁用向上遍历（这里不演示，但真源码支持）。
    """
    for ancestor in [start, *start.parents]:
        for marker in PROJECT_ROOT_MARKERS:
            if (ancestor / marker).exists():
                return ancestor
    return None


def search_dirs(cwd: Path) -> list[Path]:
    """返回要扫描的目录列表，顺序为 根→cwd（含两端）。

    对照 agents_md.rs agents_md_paths：先从 cwd 向上收集到 root，再 reverse()。
    找不到根时只返回 [cwd]（不越过、不向上）。
    """
    root = find_project_root(cwd)
    if root is None:
        return [cwd]
    dirs: list[Path] = []
    cursor = cwd
    while True:
        dirs.append(cursor)
        if cursor == root:
            break
        parent = cursor.parent
        if parent == cursor:  # 到了文件系统顶端
            break
        cursor = parent
    dirs.reverse()  # 根在最前
    return dirs


def discover_agents_md(cwd: Path) -> list[Path]:
    """按 根→cwd 顺序，逐目录找第一个命中的候选文件名，返回路径列表。"""
    found: list[Path] = []
    for d in search_dirs(cwd):
        for name in AGENTS_FILENAMES:
            candidate = d / name
            if candidate.is_file():
                found.append(candidate)
                break  # 一个目录只取一个（override 优先）
    return found


def read_agents_md(cwd: Path) -> str:
    """读取并按顺序拼接所有 AGENTS.md，受 PROJECT_DOC_MAX_BYTES 预算约束。

    对照 read_agents_md：维护 remaining 字节预算，超出就截断当前文件并停止。
    """
    if PROJECT_DOC_MAX_BYTES == 0:
        return ""
    remaining = PROJECT_DOC_MAX_BYTES
    chunks: list[str] = []
    for path in discover_agents_md(cwd):
        if remaining <= 0:
            break
        data = path.read_bytes()
        if len(data) > remaining:
            data = data[:remaining]  # 截断到剩余预算
        text = data.decode("utf-8", errors="replace").strip()
        if text:
            chunks.append(text)
            remaining -= len(data)
    return AGENTS_MD_SEPARATOR.join(chunks)


def build_user_instructions_block(cwd: Path) -> str:
    """把拼接后的 AGENTS.md 包成 <user_instructions> 块，注入回合 system。

    对照真源码：core/src/context/user_instructions.rs 把目录 + 正文包进
    <INSTRUCTIONS>，再由 protocol.rs 的 USER_INSTRUCTIONS_OPEN/CLOSE_TAG 外层包裹。
    教学版用最直观的 <user_instructions> 单层标签表达「这是注入的指令块」。
    """
    text = read_agents_md(cwd)
    if not text:
        return ""
    return f"<user_instructions>\n{text}\n</user_instructions>"


# ═══════════════════════════════════════════════════════════
#  FROM s01（搬运）：回合循环 + 一个 shell 工具
#  唯一区别：SYSTEM 里拼上了从 AGENTS.md 发现并注入的 <user_instructions> 块。
# ═══════════════════════════════════════════════════════════

BASE_SYSTEM = (
    "You are Codex, a coding agent running locally. "
    "Use the shell tool to inspect and act on the workspace. Act, don't explain."
)


def run_shell(command: str, cwd: Path) -> str:
    try:
        r = subprocess.run(command, shell=True, cwd=cwd, capture_output=True,
                           text=True, encoding="utf-8", errors="replace", timeout=120)
        out = (r.stdout + r.stderr).strip()
        tag = "" if r.returncode == 0 else f"[exit {r.returncode}] "
        return tag + (out[:50000] if out else "(no output)")
    except (subprocess.TimeoutExpired, OSError) as e:
        return f"Error: {e}"


TOOLS = [{
    "name": "shell",
    "description": "Run a shell command in the workspace and return combined stdout+stderr.",
    "parameters": {
        "type": "object",
        "properties": {"command": {"type": "string", "description": "the command line"}},
        "required": ["command"],
    },
}]


def run_turn(messages: list[dict], cwd: Path) -> None:
    model = Model()
    # 关键：每回合用当前 cwd 重新发现并注入 AGENTS.md（项目指令随目录而变）。
    block = build_user_instructions_block(cwd)
    system = BASE_SYSTEM + ("\n\n" + block if block else "")
    handlers = {"shell": lambda command: run_shell(command, cwd)}
    while True:
        resp = model.respond(messages, tools=TOOLS, system=system)
        messages += resp.output_items
        if not resp.tool_calls:
            if resp.text:
                print(f"\n\033[32m{resp.text}\033[0m")
            return
        for tc in resp.tool_calls:
            print(f"\033[33m> {tc.name} {tc.arguments}\033[0m")
            fn = handlers.get(tc.name)
            out = fn(**tc.arguments) if fn else f"unknown tool: {tc.name}"
            print(str(out)[:300])
            messages.append(tool_output_item(tc.call_id, out))


# ═══════════════════════════════════════════════════════════
#  --demo：自建临时嵌套目录树，从最深处发现 + 拼接，跑完清理
# ═══════════════════════════════════════════════════════════

def demo() -> None:
    print("s06 demo：AGENTS.md 分层发现（根→cwd 拼接）\n")
    tmp = Path(tempfile.mkdtemp(prefix="s06_agents_"))
    try:
        # 构造：tmp/proj/.git + tmp/proj/AGENTS.md + tmp/proj/sub/AGENTS.md
        proj = tmp / "proj"
        sub = proj / "sub"
        sub.mkdir(parents=True)
        (proj / ".git").mkdir()  # 项目根标记
        (proj / "AGENTS.md").write_text(
            "# 仓库级规则（根）\n- 用 4 空格缩进\n- 提交信息用英文", encoding="utf-8")
        (sub / "AGENTS.md").write_text(
            "# 子包级规则（sub）\n- 本目录改用 2 空格缩进\n- 跑 `pytest -q`", encoding="utf-8")

        print(f"临时树：\n  {proj}/.git\n  {proj}/AGENTS.md\n  {sub}/AGENTS.md\n")
        print(f"从最深目录发现：{sub}")
        root = find_project_root(sub)
        print(f"  → 找到项目根（.git 标记）：{root}")
        paths = discover_agents_md(sub)
        print("  → 发现顺序（根在前）：")
        for p in paths:
            print(f"      {p}")

        print("\n注入回合 instructions 的 <user_instructions> 块：")
        print("─" * 60)
        print(build_user_instructions_block(sub))
        print("─" * 60)
        print("\n注意：根的规则在最前，子包规则在后——后者可在局部覆盖前者的约定。")
    finally:
        shutil.rmtree(tmp, ignore_errors=True)  # 清理临时树
        print(f"\n[已清理临时目录 {tmp}]")
    sys.exit(0)


if __name__ == "__main__":
    if "--demo" in sys.argv:
        demo()

    print("s06: AGENTS.md — 输入一个目录看从那里发现的 AGENTS.md（输入 q 退出）\n")
    history: list[dict] = []
    while True:
        try:
            line = input("\033[36ms06 目录/问题 >> \033[0m")
        except (EOFError, KeyboardInterrupt):
            break
        if line.strip().lower() in ("q", "exit", ""):
            break
        # 如果输入是一个已存在目录，就只展示发现结果；否则当作给模型的问题（cwd=.）。
        p = Path(line).expanduser()
        if p.is_dir():
            block = build_user_instructions_block(p.resolve())
            print(block if block else "(该目录路径上未发现任何 AGENTS.md)")
            print()
            continue
        history.append(user_item(line))
        run_turn(history, Path.cwd())
        print()
