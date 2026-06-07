"""apply_patch 工具（s03）：补丁信封，按内容（非行号）定位。

生产级：三级模糊匹配（精确→忽略行尾→忽略首尾空白）+ 原子两阶段（预检过了才写）+ 错误回灌。
"""

from __future__ import annotations

from .registry import Tool, ToolContext

BEGIN, END = "*** Begin Patch", "*** End Patch"


def _find_block(haystack: list[str], needle: list[str]) -> int:
    if not needle:
        return 0
    cmps = (lambda a, b: a == b,
            lambda a, b: a.rstrip() == b.rstrip(),
            lambda a, b: a.strip() == b.strip())
    n = len(needle)
    for eq in cmps:
        for i in range(len(haystack) - n + 1):
            if all(eq(haystack[i + k], needle[k]) for k in range(n)):
                return i
    return -1


def _parse(text: str) -> list[dict]:
    raw = text.split("\n")
    if raw and raw[0].strip() == BEGIN:
        raw = raw[1:]
    if raw and raw[-1].strip() == END:
        raw = raw[:-1]
    hunks: list[dict] = []
    i = 0
    while i < len(raw):
        line = raw[i]
        if line.startswith("*** Add File: "):
            path = line[len("*** Add File: "):].strip()
            i += 1
            body = []
            while i < len(raw) and not raw[i].startswith("*** "):
                if raw[i].startswith("+"):
                    body.append(raw[i][1:])
                i += 1
            hunks.append({"kind": "add", "path": path, "lines": body})
        elif line.startswith("*** Delete File: "):
            hunks.append({"kind": "delete", "path": line[len("*** Delete File: "):].strip()})
            i += 1
        elif line.startswith("*** Update File: "):
            path = line[len("*** Update File: "):].strip()
            i += 1
            old, new = [], []
            while i < len(raw) and not raw[i].startswith("*** "):
                r = raw[i]
                if r.startswith("@@"):
                    pass
                elif r.startswith("-"):
                    old.append(r[1:])
                elif r.startswith("+"):
                    new.append(r[1:])
                else:
                    ctx_line = r[1:] if r.startswith(" ") else r
                    old.append(ctx_line)
                    new.append(ctx_line)
                i += 1
            hunks.append({"kind": "update", "path": path, "chunks": [(old, new)]})
        else:
            i += 1
    if not hunks:
        raise ValueError("空补丁或无法识别的标记")
    return hunks


def apply_patch_handler(ctx: ToolContext, input: str) -> str:
    try:
        hunks = _parse(input)
    except ValueError as e:
        return f"ERROR: apply_patch 解析失败（整封未应用）: {e}"

    # 原子预检：在内存副本上模拟所有 update，任一上下文定位不到就整封拒绝。
    for h in hunks:
        if h["kind"] != "update":
            continue
        try:
            lines = ctx.safe_path(h["path"]).read_text(encoding="utf-8").split("\n")
        except OSError as e:
            return f"ERROR: 读不到 {h['path']}（整封未应用）: {e}"
        for old, new in h["chunks"]:
            idx = _find_block(lines, old)
            if idx < 0:
                return f"ERROR: 在 {h['path']} 找不到上下文（整封未应用，磁盘未改动）"
            lines[idx:idx + len(old)] = new

    results = []
    for h in hunks:
        if h["kind"] == "add":
            p = ctx.safe_path(h["path"])
            p.parent.mkdir(parents=True, exist_ok=True)
            p.write_text("\n".join(h["lines"]) + ("\n" if h["lines"] else ""), encoding="utf-8")
            results.append(f"A {h['path']} (+{len(h['lines'])} 行)")
        elif h["kind"] == "delete":
            ctx.safe_path(h["path"]).unlink(missing_ok=True)
            results.append(f"D {h['path']}")
        else:
            p = ctx.safe_path(h["path"])
            lines = p.read_text(encoding="utf-8").split("\n")
            for old, new in h["chunks"]:
                idx = _find_block(lines, old)
                lines[idx:idx + len(old)] = new
            p.write_text("\n".join(lines), encoding="utf-8")
            results.append(f"M {h['path']}")
    return "应用成功:\n" + "\n".join(results)


APPLY_PATCH = Tool(
    name="apply_patch",
    description="Apply a *** Begin Patch ... *** End Patch envelope (add/update/delete files).",
    parameters={"type": "object",
                "properties": {"input": {"type": "string", "description": "the full patch text"}},
                "required": ["input"]},
    handler=apply_patch_handler,
)
