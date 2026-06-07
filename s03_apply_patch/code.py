#!/usr/bin/env python3
"""
s03: apply_patch — Codex 的招牌文件编辑工具。

运行:
  python s03_apply_patch/code.py --demo    # 不需要模型/key，直接演示「增/改/删」
  python s03_apply_patch/code.py           # 交互模式（mock 会发一个 apply_patch 调用）

本章 = s01 的回合循环 + shell 工具（搬运）
     + 新增 apply_patch 工具：一个能在「一次调用」里对多个文件做
       新增 / 更新 / 删除 / 移动 的结构化补丁工具。

为什么 Codex 不用 Claude Code 那种 edit(old_string -> new_string)？见 README 的「🆚」。
本章的解析器/应用器忠实复刻 codex-rs/apply-patch 的格式（见该 crate parser.rs 的 Lark 文法）。
"""

import shutil
import subprocess
import sys
from dataclasses import dataclass, field
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
from codexlib import Model, user_item, tool_output_item  # noqa: E402

WORKDIR = Path.cwd()
SYSTEM = (f"You are Codex at {WORKDIR}. Edit files with the apply_patch tool, "
          "run commands with shell. Act, don't explain.")
model = Model()


# ═══════════════════════════════════════════════════════════
#  apply_patch 的格式标记（与 codex-rs/apply-patch/src/parser.rs 一致）
#
#    *** Begin Patch
#    *** Add File: path/new.txt
#    +新文件的每一行都以 + 开头
#    *** Update File: path/old.txt
#    *** Move to: path/renamed.txt          (可选，重命名)
#    @@ 可选的定位上下文
#     上下文行（前导空格）
#    -被删除的行
#    +新增的行
#    *** Delete File: path/gone.txt
#    *** End Patch
# ═══════════════════════════════════════════════════════════

BEGIN, END = "*** Begin Patch", "*** End Patch"
ADD, DELETE, UPDATE = "*** Add File: ", "*** Delete File: ", "*** Update File: "
MOVE, EOF = "*** Move to: ", "*** End of File"


@dataclass
class Hunk:
    kind: str                       # "add" | "delete" | "update"
    path: str
    move_to: str | None = None
    add_lines: list[str] = field(default_factory=list)        # add 用
    chunks: list[list[tuple]] = field(default_factory=list)   # update 用：[[(tag,line),...]]


def parse_patch(text: str) -> list[Hunk]:
    lines = text.strip("\n").split("\n")
    if not lines or lines[0].strip() != BEGIN:
        raise ValueError("补丁第一行必须是 '*** Begin Patch'")
    if lines[-1].strip() != END:
        raise ValueError("补丁最后一行必须是 '*** End Patch'")

    hunks: list[Hunk] = []
    i = 1
    while i < len(lines) - 1:
        line = lines[i]
        if line.startswith(ADD):
            h = Hunk("add", line[len(ADD):].strip())
            i += 1
            while i < len(lines) - 1 and not _is_header(lines[i]):
                if lines[i].startswith("+"):
                    h.add_lines.append(lines[i][1:])
                i += 1
            hunks.append(h)
        elif line.startswith(DELETE):
            hunks.append(Hunk("delete", line[len(DELETE):].strip()))
            i += 1
        elif line.startswith(UPDATE):
            h = Hunk("update", line[len(UPDATE):].strip())
            i += 1
            if i < len(lines) - 1 and lines[i].startswith(MOVE):
                h.move_to = lines[i][len(MOVE):].strip()
                i += 1
            chunk: list[tuple] = []
            while i < len(lines) - 1 and not _is_header(lines[i]):
                raw = lines[i]
                if raw.startswith("@@"):                 # 新上下文块的分隔
                    if chunk:
                        h.chunks.append(chunk)
                        chunk = []
                elif raw == EOF:
                    pass
                else:
                    tag = raw[0] if raw and raw[0] in " +-" else " "
                    chunk.append((tag, raw[1:] if raw else ""))
                i += 1
            if chunk:
                h.chunks.append(chunk)
            hunks.append(h)
        else:
            i += 1
    return hunks


def _is_header(line: str) -> bool:
    return line.startswith((ADD, DELETE, UPDATE)) or line.strip() == END


# ═══════════════════════════════════════════════════════════
#  应用补丁到文件系统
# ═══════════════════════════════════════════════════════════

def safe_path(p: str) -> Path:
    path = (WORKDIR / p).resolve()
    if not path.is_relative_to(WORKDIR):
        raise ValueError(f"路径逃出工作区: {p}")
    return path


def apply_hunk(h: Hunk) -> str:
    if h.kind == "add":
        path = safe_path(h.path)
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text("\n".join(h.add_lines) + ("\n" if h.add_lines else ""))
        return f"A {h.path} (+{len(h.add_lines)} 行)"

    if h.kind == "delete":
        safe_path(h.path).unlink(missing_ok=True)
        return f"D {h.path}"

    # update：对每个 chunk，用「上下文+删除行」定位，替换为「上下文+新增行」
    path = safe_path(h.path)
    file_lines = path.read_text().split("\n")
    for chunk in h.chunks:
        old = [ln for tag, ln in chunk if tag in (" ", "-")]
        new = [ln for tag, ln in chunk if tag in (" ", "+")]
        idx = _find_block(file_lines, old)
        if idx < 0:
            raise ValueError(f"在 {h.path} 中找不到要修改的上下文：{old[:2]}...")
        file_lines[idx:idx + len(old)] = new
    target = safe_path(h.move_to) if h.move_to else path
    if h.move_to:
        target.parent.mkdir(parents=True, exist_ok=True)
        path.unlink(missing_ok=True)
    target.write_text("\n".join(file_lines))
    moved = f" -> {h.move_to}" if h.move_to else ""
    return f"M {h.path}{moved}"


def _find_block(haystack: list[str], needle: list[str]) -> int:
    """按内容定位 needle 在 haystack 中的起点。忠实搬运 seek_sequence.rs 的
    **三级降级匹配**（strictness 递减）：先精确，再忽略行尾空白，最后忽略首尾空白。
    模型把上下文的缩进/行尾抄歪一点，仍能贴上——这对会犯小错的 LLM 至关重要。"""
    if not needle:
        return 0
    n = len(needle)
    comparators = (                                   # 从严到松；命中靠前（更严）的就用它
        lambda a, b: a == b,                          # ① 精确
        lambda a, b: a.rstrip() == b.rstrip(),        # ② 忽略行尾空白
        lambda a, b: a.strip() == b.strip(),          # ③ 忽略首尾空白
    )
    for eq in comparators:
        for i in range(len(haystack) - n + 1):
            if all(eq(haystack[i + k], needle[k]) for k in range(n)):
                return i
    return -1


def apply_patch_tool(input: str) -> str:
    """apply_patch 工具入口。input = 整段补丁文本（Codex 的真实参数名就是 input）。

    生产级两道关：
      ① 原子性预检：先在内存副本上模拟所有 update，任一上下文定位不到就整封拒绝、
         一个字节都不写——避免「前两个 hunk 写了、第三个失败」的半成品（呼应思考 2）。
      ② 出错回灌：失败返回**给模型看的**错误串（不抛异常崩进程），模型据此重抄路标再试
         （对应真 Codex 的 ApplyPatchError 经 RespondToModel 回灌，见 s02「生产级」）。
    """
    try:
        hunks = parse_patch(input)
    except (ValueError, OSError) as e:
        return f"apply_patch 解析失败（整封未应用）: {e}"

    # ① 预检：在副本上模拟每个 update，确认所有上下文都能定位（两阶段提交的"准备"阶段）
    for h in hunks:
        if h.kind != "update":
            continue
        try:
            lines = safe_path(h.path).read_text().split("\n")
        except OSError as e:
            return f"apply_patch 失败（整封未应用）：读不到 {h.path}：{e}"
        for chunk in h.chunks:
            old = [ln for tag, ln in chunk if tag in (" ", "-")]
            new = [ln for tag, ln in chunk if tag in (" ", "+")]
            idx = _find_block(lines, old)
            if idx < 0:
                return (f"apply_patch 失败（整封未应用，磁盘未改动）：在 {h.path} "
                        f"找不到上下文 {old[:2]}…；请照抄文件里那几行原文当路标再试。")
            lines[idx:idx + len(old)] = new   # 在副本上推进，保证后续 chunk 定位准确

    # ② 预检全过，再真正落盘（"提交"阶段）
    try:
        results = [apply_hunk(h) for h in hunks]
        return "应用成功:\n" + "\n".join(results)
    except (ValueError, OSError) as e:
        return f"apply_patch 失败: {e}"


# ═══════════════════════════════════════════════════════════
#  FROM s01（搬运）：shell 工具 + 回合循环
# ═══════════════════════════════════════════════════════════

def run_shell(command: str) -> str:
    try:
        r = subprocess.run(command, shell=True, cwd=WORKDIR, capture_output=True,
                           text=True, encoding="utf-8", errors="replace", timeout=120)
        out = (r.stdout + r.stderr).strip()
        return out[:50000] if out else "(no output)"
    except (subprocess.TimeoutExpired, OSError) as e:
        return f"Error: {e}"


TOOLS = [
    {"name": "apply_patch",
     "description": "Apply a structured patch (*** Begin Patch ... *** End Patch) that can "
                    "add/update/delete/move multiple files in one call.",
     "parameters": {"type": "object",
                    "properties": {"input": {"type": "string", "description": "the full patch"}},
                    "required": ["input"]}},
    {"name": "shell", "description": "Run a shell command.",
     "parameters": {"type": "object",
                    "properties": {"command": {"type": "string"}}, "required": ["command"]}},
]
HANDLERS = {"apply_patch": apply_patch_tool, "shell": run_shell}


def run_turn(messages: list[dict]) -> None:
    while True:
        resp = model.respond(messages, tools=TOOLS, system=SYSTEM)
        messages += resp.output_items
        if not resp.tool_calls:
            if resp.text:
                print(f"\n\033[32m{resp.text}\033[0m")
            return
        for tc in resp.tool_calls:
            print(f"\033[33m> {tc.name}\033[0m")
            handler = HANDLERS.get(tc.name)
            output = handler(**tc.arguments) if handler else f"unknown tool: {tc.name}"
            print(str(output)[:400])
            messages.append(tool_output_item(tc.call_id, output))


# ═══════════════════════════════════════════════════════════
#  --demo：不依赖模型，直接演示 apply_patch 的 增 / 改
# ═══════════════════════════════════════════════════════════

def demo() -> None:
    ws = Path("_demo_workspace")
    ws.mkdir(exist_ok=True)

    try:
        print("① Add File ——")
        add_patch = (f"{BEGIN}\n*** Add File: _demo_workspace/poem.txt\n"
                     "+roses are red\n+violets are blue\n+codex writes patches\n"
                     f"+and so can you\n{END}")
        print(apply_patch_tool(add_patch))
        print("文件内容:\n" + (ws / "poem.txt").read_text())

        print("② Update File —— 把第二行改掉，并在末尾加一行")
        upd_patch = (f"{BEGIN}\n*** Update File: _demo_workspace/poem.txt\n"
                     "@@\n roses are red\n-violets are blue\n+violets are violet\n"
                     " codex writes patches\n"
                     "@@\n and so can you\n+-- a haiku, sort of\n"
                     f"{END}")
        print(apply_patch_tool(upd_patch))
        print("文件内容:\n" + (ws / "poem.txt").read_text())

        # ── 生产级 ③：模糊匹配——上下文的行尾空白抄歪了，仍能定位（seek_sequence 三级降级）──
        print("③ 生产级·模糊匹配：故意把上下文行尾抄出多余空格，照样贴上：")
        fuzzy = (f"{BEGIN}\n*** Update File: _demo_workspace/poem.txt\n"
                 "@@\n roses are red    \n"                       # ← 行尾多了空格（抄歪）
                 "-violets are violet\n+violets are PURPLE\n"
                 f"{END}")
        print(apply_patch_tool(fuzzy))
        print("   第 2 行 →", (ws / "poem.txt").read_text().splitlines()[1])

        # ── 生产级 ④：定位不到 → 整封拒绝、磁盘不动（原子性）+ 错误回灌给模型 ──
        print("\n④ 生产级·原子性 + 错误回灌：补丁含一个根本不存在的上下文：")
        bad = (f"{BEGIN}\n*** Update File: _demo_workspace/poem.txt\n"
               "@@\n THIS LINE DOES NOT EXIST\n-x\n+y\n"
               f"{END}")
        print("  ", apply_patch_tool(bad))            # ← 返回错误串（会回灌给模型），文件未动
        print("   文件未被破坏，第 2 行仍是：", (ws / "poem.txt").read_text().splitlines()[1])
    finally:
        shutil.rmtree(ws, ignore_errors=True)   # 自清工作区，和其余各章一致


if __name__ == "__main__":
    if "--demo" in sys.argv:
        demo()
        sys.exit(0)
    print("s03: apply_patch（输入 q 退出；试试『新建一个文件 notes.txt』）\n")
    history: list[dict] = []
    while True:
        try:
            query = input("\033[36ms03 >> \033[0m")
        except (EOFError, KeyboardInterrupt):
            break
        if query.strip().lower() in ("q", "exit", ""):
            break
        history.append(user_item(query))
        run_turn(history)
        print()
