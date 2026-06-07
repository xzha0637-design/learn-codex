#!/usr/bin/env python3
"""
s12: Tools Extra — 再给 agent 加几个工具：update_plan / web_search / view_image。

运行:
  python s12_tools_extra/code.py            # 交互模式（mock 后端，无需 key）
  python s12_tools_extra/code.py --demo     # 离线直跑：三个工具全过一遍 + 生命周期事件

默认 backend=mock，无需任何 key；--demo 完全离线（web_search 返回内置假结果，绝不联网）。

本章 = s01 的回合循环原样搬运 + 本章新增三个工具，每个都把"自己在干什么"
       建模成一对可观测的 Begin/End 生命周期事件:
  + update_plan：参数 = {explanation?, plan: [{step, status}]}；维护一份显式待办清单
  + web_search ：离线返回内置 canned 结果（不联网）
  + view_image ：读文件路径，回大小/类型元数据（不做真视觉）

教学点：
  ① "给 agent 更多工具" = 往 HANDLERS 里再注册几个 handler，循环一行不改（呼应 s02）。
  ② Codex 里工具不只是"调用→返回"，而是"发 Begin 事件 → 干活 → 发 End 事件"，
     让任意前端（TUI/IDE/codex exec）都能观测每一步（呼应 s10/s11）。
"""

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
from codexlib import Model, user_item, tool_output_item  # noqa: E402

WORKDIR = Path.cwd()
SYSTEM = (
    f"You are Codex, a coding agent in {WORKDIR}. "
    "For multi-step tasks, call update_plan to maintain an explicit checklist "
    "(at most one step in_progress). Use web_search for up-to-date facts, and "
    "view_image to inspect a local image file. Act, don't explain."
)

model = Model()


# ═══════════════════════════════════════════════════════════
#  NEW in s12（一）：工具生命周期事件（Begin / End）
#
#  Codex 把每次"对前端可见的动作"建模成一个协议事件，流过事件队列（EQ, s10）
#  给前端（TUI/IDE/codex exec）消费、渲染。本章三个工具都用它：
#    update_plan → PlanUpdate{explanation, plan}
#    web_search  → WebSearchBegin{call_id} ... WebSearchEnd{call_id, query, action}
#    view_image  → ViewImageBegin{call_id, path} ... ViewImageEnd{call_id, path}
#  真源码见 protocol/src/protocol.rs 的 EventMsg 枚举（PlanUpdate /
#  WebSearchBeginEvent / WebSearchEndEvent / ViewImageToolCallEvent）。
#  教学版用一个 emit() 把事件压扁成一行 print，模拟那条事件总线。
# ═══════════════════════════════════════════════════════════

_CALL_SEQ = 0


def next_call_id() -> str:
    """给每次工具调用发一个 id；它是把 Begin/End 串成一对的钥匙。"""
    global _CALL_SEQ
    _CALL_SEQ += 1
    return f"call_{_CALL_SEQ}"


def emit(event: str, **fields) -> None:
    """模拟把一个协议事件放进事件队列（EQ）。真身会被 TUI/IDE 消费、渲染。"""
    body = " ".join(f"{k}={v!r}" for k, v in fields.items())
    print(f"\033[35m  ⟦event⟧ {event} {body}\033[0m")


# ═══════════════════════════════════════════════════════════
#  NEW in s12（二）：update_plan —— 模型维护的显式步骤清单
#
#  对应真源码 protocol/src/plan_tool.rs 的 UpdatePlanArgs / StepStatus，
#  和 core/src/tools/handlers/plan.rs 的 PlanHandler。
#  真 handler 几乎不"存"计划，只是把这次 args 当作一个 PlanUpdate 事件 send
#  给前端去渲染，回模型固定一句 "Plan updated"。我们把"广播+渲染"合并成 print。
# ═══════════════════════════════════════════════════════════

# status 三态。真源码是 StepStatus { Pending, InProgress, Completed }（snake_case 上线）。
STATUS_ICON = {"pending": "[ ]", "in_progress": "[~]", "completed": "[x]"}
VALID_STATUS = set(STATUS_ICON)

# 计划保存在内存里。注意：模型每次 update_plan 都重发**完整**清单 → 我们整盘替换。
CURRENT_PLAN: list[dict] = []


def render_plan(plan: list[dict], explanation: str | None = None) -> str:
    if not plan:
        return "(empty plan)"
    lines = []
    if explanation:
        lines.append(f"\033[90m{explanation}\033[0m")
    for it in plan:
        st = it.get("status", "pending")
        icon = STATUS_ICON.get(st, "[?]")
        color = {"completed": "\033[32m", "in_progress": "\033[33m"}.get(st, "")
        reset = "\033[0m" if color else ""
        lines.append(f"  {icon} {color}{it.get('step', '')}{reset}")
    return "\n".join(lines)


def run_update_plan(plan: list[dict], explanation: str | None = None) -> str:
    """update_plan handler：校验 → 整盘替换内存计划 → 发 PlanUpdate 事件 + 渲染 → 回确认。"""
    # 校验：每项要有 step + 合法 status；至多一个 in_progress（真身的硬约束）。
    in_progress = 0
    for it in plan:
        if "step" not in it:
            return "Error: each plan item needs a 'step'"
        st = it.get("status", "pending")
        if st not in VALID_STATUS:
            return f"Error: bad status {st!r}; use one of {sorted(VALID_STATUS)}"
        in_progress += st == "in_progress"
    if in_progress > 1:
        return "Error: at most one step may be in_progress at a time"

    CURRENT_PLAN[:] = plan  # 整盘替换：计划"活"在这里，独立于对话上下文
    # 真 PlanHandler 把整份 args 塞进一个 PlanUpdate 事件 send 出去，由前端渲染。
    emit("PlanUpdate", explanation=explanation, steps=len(plan))
    print("\033[36m── plan ──────────────────────────────\033[0m")
    print(render_plan(plan, explanation))
    print("\033[36m──────────────────────────────────────\033[0m")
    # 真 PlanHandler 回给模型的就是固定一句 "Plan updated"。
    return "Plan updated"


# ═══════════════════════════════════════════════════════════
#  NEW in s12（三）：web_search —— 离线：返回内置 canned 结果，绝不联网
#
#  真 Codex 的 web_search 多为 OpenAI 托管工具（ToolSpec::WebSearch），搜索在
#  远端跑、结果直接回模型；core 这边的 core/src/web_search.rs 只负责把
#  WebSearchAction 格式化给前端看，并随回合发 WebSearchBegin/End 事件。
#  教学版没有远端，于是把"假装搜索"和"发事件"都塞进这一个本地函数。
# ═══════════════════════════════════════════════════════════

# 一张离线"知识"小表，纯属教学占位（真身走 OpenAI 托管的 web_search 工具）。
_CANNED = {
    "codex": [
        ("OpenAI Codex — coding agent", "https://openai.com/codex",
         "Codex is OpenAI's agentic coding tool; core is written in Rust (codex-rs)."),
        ("codex-rs on GitHub", "https://github.com/openai/codex",
         "The Rust workspace: core, tui, exec, sandboxing, protocol, tools."),
    ],
}


def run_web_search(query: str) -> str:
    call_id = next_call_id()
    emit("WebSearchBegin", call_id=call_id)               # ← 开始：先发 Begin
    key = next((k for k in _CANNED if k in query.lower()), None)
    hits = _CANNED.get(key, [
        (f"(offline) no canned result for {query!r}", "about:blank",
         "This teaching build never goes online; returning a placeholder."),
    ])
    # 真 End 事件带的是结构化 WebSearchAction（Search{query, queries} 等）；这里简化成 "search"。
    emit("WebSearchEnd", call_id=call_id, query=query, action="search")  # ← 结束：发 End
    return "\n".join(f"- {title}\n  {url}\n  {snippet}" for title, url, snippet in hits)


# ═══════════════════════════════════════════════════════════
#  NEW in s12（四）：view_image —— 读文件路径，回大小/类型元数据（不做真视觉）
#
#  真 view_image.rs 会：查模型 input_modalities 是否含 Image → 经沙箱读字节 →
#  真正解码并 resize（load_for_prompt_bytes / PromptImageMode）→ 转 base64 data URL
#  作为 InputImage 附进下一次 Responses 请求（模型真"看见"）→ 随 TurnItem::ImageView
#  发 started/completed（即 ViewImageToolCallEvent）。教学版只保留"读文件 + 发事件 + 回元数据"。
# ═══════════════════════════════════════════════════════════

# 极小的"魔数 → 类型"嗅探（够教学）；真身用 image crate 解码并 resize。
_MAGIC = [
    (b"\x89PNG\r\n\x1a\n", "image/png"),
    (b"\xff\xd8\xff", "image/jpeg"),
    (b"GIF87a", "image/gif"),
    (b"GIF89a", "image/gif"),
    (b"BM", "image/bmp"),
]


def _sniff_type(head: bytes) -> str:
    for magic, mime in _MAGIC:
        if head.startswith(magic):
            return mime
    return "application/octet-stream"


def run_view_image(path: str) -> str:
    call_id = next_call_id()
    p = (WORKDIR / path).resolve()
    emit("ViewImageBegin", call_id=call_id, path=str(p))   # ← 开始：先发 Begin
    if not p.is_file():
        # 即便出错也补一个 End，避免前端留下"只有 Begin"的悬空事件。
        emit("ViewImageEnd", call_id=call_id, path=str(p))
        return f"Error: image path is not a file: {path}"
    data = p.read_bytes()
    mime = _sniff_type(data[:16])
    emit("ViewImageEnd", call_id=call_id, path=str(p))     # ← 结束：发 End
    return (f"viewed image: name={p.name} type={mime} size={len(data)} bytes "
            f"(teaching build: no real vision; metadata only)")


# 工具 schema（Responses API 扁平形状）。三个工具一起注册进同一份 TOOLS。
TOOLS = [
    {
        "name": "update_plan",
        "description": ("Update the task plan. Provide an optional explanation and a list of "
                        "plan items, each with a step and status. At most one in_progress."),
        "parameters": {
            "type": "object",
            "properties": {
                "explanation": {"type": "string"},
                "plan": {
                    "type": "array",
                    "description": "The list of steps",
                    "items": {
                        "type": "object",
                        "properties": {
                            "step": {"type": "string"},
                            "status": {"type": "string",
                                       "enum": ["pending", "in_progress", "completed"]},
                        },
                        "required": ["step", "status"],
                    },
                },
            },
            "required": ["plan"],
        },
    },
    {
        "name": "web_search",
        "description": "Search the web for up-to-date information (offline canned in this build).",
        "parameters": {"type": "object",
                       "properties": {"query": {"type": "string"}},
                       "required": ["query"]},
    },
    {
        "name": "view_image",
        "description": "View a local image file from the filesystem when visual inspection is needed.",
        "parameters": {"type": "object",
                       "properties": {"path": {"type": "string",
                                               "description": "Local filesystem path to an image."}},
                       "required": ["path"]},
    },
]

# 加工具 = 往这张表再注册一行 handler。循环（下面）一行不用改。
HANDLERS = {
    "update_plan": run_update_plan,
    "web_search": run_web_search,
    "view_image": run_view_image,
}


# ═══════════════════════════════════════════════════════════
#  FROM s01（搬运）—— 回合循环，与 s01 一字不差
#  (本章只是往 HANDLERS 多注册了三个工具；循环逻辑没动。
#   每个工具的 Begin/End 事件发生在各 handler 内部。)
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
            handler = HANDLERS.get(tc.name)
            output = handler(**tc.arguments) if handler else f"unknown tool: {tc.name}"
            print(str(output)[:300])
            messages.append(tool_output_item(tc.call_id, output))


# ═══════════════════════════════════════════════════════════
#  --demo：离线直跑三个工具，把每个的生命周期事件摊开给你看。
#          不碰模型；web_search 永不联网；临时文件跑完即删。
# ═══════════════════════════════════════════════════════════

def demo() -> None:
    print("s12 demo：update_plan + web_search + view_image，注意每个 ⟦event⟧（离线，不联网）\n")

    print("① update_plan —— 建一个 3 步计划（一步 in_progress、一步 completed）")
    out = run_update_plan(plan=[
        {"step": "Search docs for the API", "status": "completed"},
        {"step": "Inspect the screenshot", "status": "in_progress"},
        {"step": "Write the fix", "status": "pending"},
    ], explanation="Triage with help from the new tools")
    print("handler 回给模型 →", out)

    print("\n② update_plan 非法更新：两个 in_progress 应被拒")
    print("rejected →", run_update_plan(plan=[
        {"step": "a", "status": "in_progress"},
        {"step": "b", "status": "in_progress"},
    ]))

    print("\n③ web_search('codex') —— 返回内置 canned 结果（带 Begin/End）")
    print(run_web_search(query="codex"))

    # 造一个最小的合法 PNG（8 字节魔数足够嗅探出 image/png）。
    tmp = WORKDIR / "_demo_pixel.png"
    tmp.write_bytes(b"\x89PNG\r\n\x1a\n" + b"\x00" * 24)
    try:
        print("\n④ view_image(_demo_pixel.png) —— 只回元数据，不做真视觉（带 Begin/End）")
        print(run_view_image(path="_demo_pixel.png"))
        print("\n⑤ view_image(不存在的路径) —— 报错回灌给模型（Begin 后补一个 End，不留悬空）")
        print(run_view_image(path="_does_not_exist.png"))
    finally:
        tmp.unlink(missing_ok=True)
        print("\n(cleaned up _demo_pixel.png)")


if __name__ == "__main__":
    if "--demo" in sys.argv:
        demo()
        sys.exit(0)

    print("s12: Tools Extra — update_plan + web_search + view_image（输入 q 退出）\n")
    history: list[dict] = []
    while True:
        try:
            query = input("\033[36ms12 >> \033[0m")
        except (EOFError, KeyboardInterrupt):
            break
        if query.strip().lower() in ("q", "exit", ""):
            break
        history.append(user_item(query))
        run_turn(history)
        print()
