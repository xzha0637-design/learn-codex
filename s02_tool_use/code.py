#!/usr/bin/env python3
"""
s02: Tool Use — 在 s01 的循环上注册更多工具 + 一张分发映射。

运行:
  python s02_tool_use/code.py            # 交互模式
  python s02_tool_use/code.py --demo     # 离线直跑：演示 read/write/list + 分发映射

默认 backend=mock，无需任何 key；--demo 完全离线（根本不碰模型）。

本章 = s01 的回合循环原样搬运 + 本章新增:
  + read_file / write_file / list_dir 三个新工具（各带 safe_path 越界防护）
  + TOOL_HANDLERS 分发映射 + create_tools_json（Responses API 扁平工具 schema）
  + 生产级 dispatch 层：对照 schema 校验参数、兜住异常，出错回灌给模型而非崩进程
    （= 真 Codex 的 ToolRouter；对应 FunctionCallError::RespondToModel 可恢复 / Fatal 中止）
教学点：**循环一行没改**（加工具 = 注册 handler + schema）；而「防 LLM 出错」靠的是
        循环与工具之间那层 dispatch——不指望模型不犯错，而是让它犯错后看得见、能纠正。
"""

import json
import sys
from pathlib import Path

# 仓库根目录加入 import 路径，这样 `from codexlib import ...` 能找到共享模块。
sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
from codexlib import Model, user_item, tool_output_item  # noqa: E402

WORKDIR = Path.cwd()
SYSTEM = (
    f"You are Codex, a coding agent running locally in {WORKDIR}. "
    "Use the shell / read_file / write_file / list_dir tools to act. Act, don't explain."
)

model = Model()


# ═══════════════════════════════════════════════════════════
#  FROM s01（搬运）—— shell 工具 + 回合循环，一字未改
#  Codex 的主力工具就是「跑命令」；没有危险命令黑名单（那是 s04 审批 + s05 内核沙箱的活）。
# ═══════════════════════════════════════════════════════════

def run_shell(command: str) -> str:
    import subprocess
    try:
        r = subprocess.run(command, shell=True, cwd=WORKDIR,
                           capture_output=True, text=True,
                           encoding="utf-8", errors="replace", timeout=120)
        out = (r.stdout + r.stderr).strip()
        tag = "" if r.returncode == 0 else f"[exit {r.returncode}] "
        return tag + (out[:50000] if out else "(no output)")
    except subprocess.TimeoutExpired:
        return "Error: timeout (120s)"
    except OSError as e:
        return f"Error: {e}"


# ═══════════════════════════════════════════════════════════
#  NEW in s02：几个「第一类工具」+ 一个工作区越界防护
#
#  safe_path：把相对路径锚定到 WORKDIR 并 resolve，越界就抛错。
#  这是 learn-claude-code s01/s02 同款的应用层护栏。
#  注意 Codex 真身不靠这个：它把读写交给内核沙箱（s05）兜底——
#  应用层挡 vs 内核层关，这条主线从这里就埋下了伏笔。
# ═══════════════════════════════════════════════════════════

def safe_path(p: str) -> Path:
    path = (WORKDIR / p).resolve()
    if not path.is_relative_to(WORKDIR):
        raise ValueError(f"path escapes workspace: {p}")
    return path


def run_read_file(path: str, limit: int | None = None) -> str:
    try:
        lines = safe_path(path).read_text(encoding="utf-8", errors="replace").splitlines()
        if limit and limit < len(lines):
            lines = lines[:limit] + [f"... ({len(lines) - limit} more lines)"]
        return "\n".join(lines) if lines else "(empty file)"
    except Exception as e:  # noqa: BLE001 — 工具失败要回灌给模型，而非崩进程
        return f"Error: {e}"


def run_write_file(path: str, content: str) -> str:
    try:
        fp = safe_path(path)
        fp.parent.mkdir(parents=True, exist_ok=True)
        fp.write_text(content, encoding="utf-8")
        return f"wrote {len(content)} bytes to {path}"
    except Exception as e:  # noqa: BLE001
        return f"Error: {e}"


def run_list_dir(path: str = ".") -> str:
    try:
        base = safe_path(path)
        if not base.is_dir():
            return f"Error: not a directory: {path}"
        entries = sorted(base.iterdir(), key=lambda x: (x.is_file(), x.name))
        rows = [f"{'F' if e.is_file() else 'D'}  {e.name}" for e in entries]
        return "\n".join(rows) if rows else "(empty directory)"
    except Exception as e:  # noqa: BLE001
        return f"Error: {e}"


# ═══════════════════════════════════════════════════════════
#  NEW in s02：工具 schema（Responses API「扁平」形状）+ 分发映射
#
#  Codex 的工具 schema 是扁平地挂在工具对象上的：{name, description, parameters}。
#  对比 Claude 的 {name, description, input_schema}——字段名都不同（见 🆚）。
#  codexlib._openai_respond 会把它们再包成 {"type":"function", ...} 发给真 API。
# ═══════════════════════════════════════════════════════════

TOOLS = [
    {"name": "shell",
     "description": "Run a shell command in the workspace; returns combined stdout+stderr.",
     "parameters": {"type": "object",
                    "properties": {"command": {"type": "string"}},
                    "required": ["command"]}},
    {"name": "read_file",
     "description": "Read a text file inside the workspace.",
     "parameters": {"type": "object",
                    "properties": {"path": {"type": "string"},
                                   "limit": {"type": "integer"}},
                    "required": ["path"]}},
    {"name": "write_file",
     "description": "Write text content to a file inside the workspace.",
     "parameters": {"type": "object",
                    "properties": {"path": {"type": "string"},
                                   "content": {"type": "string"}},
                    "required": ["path", "content"]}},
    {"name": "list_dir",
     "description": "List entries of a directory inside the workspace.",
     "parameters": {"type": "object",
                    "properties": {"path": {"type": "string"}},
                    "required": []}},
]

# 分发映射：名字 → handler。加工具 = 往这张表 + TOOLS 里各加一行。循环不动。
TOOL_HANDLERS = {
    "shell": run_shell,
    "read_file": run_read_file,
    "write_file": run_write_file,
    "list_dir": run_list_dir,
}


def create_tools_json(tools: list[dict]) -> list[dict]:
    """对应真源码 tools/src/tool_spec.rs::create_tools_json_for_responses_api——
    把每个 ToolSpec 序列化成 Responses API 的扁平 function 对象。这里教学化为一行映射。"""
    return [{"type": "function", **t} for t in tools]


# ═══════════════════════════════════════════════════════════
#  NEW in s02（生产级）：dispatch 层 —— 对照 schema 校验 + 出错回灌给模型
#
#  这一层夹在「循环」和「工具」之间，正是真 Codex 的 ToolRouter / ToolRegistry
#  （core/src/tools/registry.rs）所在。循环依旧只管「拿到结果、回灌」；
#  «模型给的参数对不对、工具崩没崩» 全在这层兜住，并守一条铁律：
#
#    出错不抛给进程，而是把错误**当作工具结果回灌给模型**，让它下一轮自己改。
#    对应 FunctionCallError::RespondToModel（可恢复）；Fatal 才中止整个回合
#    (codex-rs/core/src/tools/function_call_error.rs)。
#
#  这就是「怎么防 LLM 出错」的答案：不指望它不犯错，而是让它犯错后看得见、能纠正。
# ═══════════════════════════════════════════════════════════

TOOLS_BY_NAME = {t["name"]: t for t in TOOLS}
_JSON_PY_TYPES = {"string": str, "integer": int, "number": (int, float),
                  "boolean": bool, "object": dict, "array": list}


def validate_arguments(name: str, args: dict) -> str | None:
    """对照工具 schema 校验模型给的参数；通过返回 None，不过返回「给模型看的」错误串。

    真 Codex 用 parse_arguments::<T>()（core/src/tools/handlers/mod.rs:72）——把
    arguments 反序列化进一个**有类型的结构体**，失败即 RespondToModel。它不漂移，是因为
    「发给模型的 schema」与「反序列化目标类型」同源。我们这边显式对着同一份 schema 走查。
    """
    schema = TOOLS_BY_NAME.get(name, {}).get("parameters", {})
    props: dict = schema.get("properties", {})
    required: list = schema.get("required", [])

    missing = [r for r in required if r not in args]
    if missing:
        return f"missing required field(s) {missing}"
    for key, val in args.items():
        if key not in props:                       # additionalProperties: false 的精神
            return f"unexpected field `{key}` (allowed: {sorted(props)})"
        want = props[key].get("type")
        py = _JSON_PY_TYPES.get(want)
        # bool 是 int 的子类，单独挡：避免 True 混过 integer/number
        if py and (not isinstance(val, py)
                   or (want in ("integer", "number") and isinstance(val, bool))):
            return f"field `{key}` should be {want}, got {type(val).__name__}"
    return None


def dispatch_tool(name: str, args: dict) -> str:
    """生产级分发：解析→校验→执行→兜错，任一步出错都回灌给模型而非崩进程。
    = 真 Codex ToolRouter::dispatch_any（registry.rs）的教学内核。"""
    if name not in TOOL_HANDLERS:                  # ① 未知工具（模型幻觉一个不存在的名字）
        return f"ERROR: unknown tool `{name}` (available: {sorted(TOOL_HANDLERS)})"
    err = validate_arguments(name, args)           # ② 参数不合 schema
    if err is not None:
        return f"ERROR: invalid arguments for `{name}`: {err}"
    try:
        return TOOL_HANDLERS[name](**args)         # ③ 真执行；handler 内部异常也兜住
    except Exception as e:                          # noqa: BLE001
        return f"ERROR: tool `{name}` raised {type(e).__name__}: {e}"


# ═══════════════════════════════════════════════════════════
#  FROM s01（搬运）—— 回合循环，整个 agent 的心脏，与 s01 一字不差
#    模型发起 tool_call → 查 TOOL_HANDLERS 执行 → 结果回灌 → 继续
#    模型没发起 tool_call → 它说完了 → 退出
#  s01 里 HANDLERS 只有一项；s02 里有四项——但 run_turn 完全没变。
# ═══════════════════════════════════════════════════════════

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
            # 循环职责没变（拿结果→回灌）；校验/兜错都在 dispatch 层（= 真 Codex 的 ToolRouter）。
            output = dispatch_tool(tc.name, tc.arguments)
            print(str(output)[:300])
            messages.append(tool_output_item(tc.call_id, output))


# ═══════════════════════════════════════════════════════════
#  --demo：离线直跑三个新工具，把「分发映射」摊开给你看（不碰模型）
# ═══════════════════════════════════════════════════════════

def demo() -> None:
    ws = WORKDIR / "_demo_workspace"
    print("s02 demo：直接走 TOOL_HANDLERS 分发，演示 write/read/list（离线，不碰模型）\n")
    print("已注册工具:", list(TOOL_HANDLERS) or "(none)")
    print("Responses API 扁平 schema 示例:",
          json.dumps(create_tools_json(TOOLS)[1], ensure_ascii=False), "\n")

    def call(name: str, **kwargs):
        print(f"\033[33m> {name} {kwargs}\033[0m")
        out = dispatch_tool(name, kwargs)  # ← 和 run_turn 里一模一样的分发动作（含校验+兜错）
        print(" ", str(out)[:300], "\n")

    try:
        call("write_file", path="_demo_workspace/hello.txt",
             content="hello from codex\nsecond line\n")
        call("read_file", path="_demo_workspace/hello.txt")
        call("list_dir", path="_demo_workspace")
        # 越界写入：safe_path 应用层护栏拦截。
        call("write_file", path="../escape.txt", content="should be blocked")

        # ── 生产级：模型出错时，dispatch 层把错误回灌给它、而不是崩溃 ──
        print("生产级：以下四种「模型出错」全被 dispatch 层接住，错误会作为工具结果回灌、让模型自己改：")
        call("read_file")                              # 缺必填 path
        call("write_file", path="x.txt", content=123)  # content 类型错（schema 要 string）
        call("read_file", path="a.txt", lines=5)       # 未知字段 lines（其实叫 limit）
        call("search_web", q="codex harness")          # 未知工具（模型幻觉出来的）
    finally:
        import shutil
        shutil.rmtree(ws, ignore_errors=True)
        print("(cleaned up _demo_workspace/)")


if __name__ == "__main__":
    if "--demo" in sys.argv:
        demo()
        sys.exit(0)

    print("s02: Tool Use — 在 s01 循环上多挂几个工具（输入 q 退出）\n")
    history: list[dict] = []
    while True:
        try:
            query = input("\033[36ms02 >> \033[0m")
        except (EOFError, KeyboardInterrupt):
            break
        if query.strip().lower() in ("q", "exit", ""):
            break
        history.append(user_item(query))
        run_turn(history)
        print()
