#!/usr/bin/env python3
"""
s15: MCP（客户端 + 服务端）—— Codex 既能用别人的工具，也能被别人当成工具。

运行:
  python s15_mcp/code.py            # 交互模式（输入 q / 空行退出）
  python s15_mcp/code.py --demo     # 一口气演示两个方向，然后退出

默认 backend=mock，无需任何 key，完全离线（进程内 dict JSON-RPC，不开子进程、不走网络）。

本章 = s01 的回合循环（搬运） + 新增两半，合成一个完整的 MCP 故事：

  ── 方向 A：Codex 作为 MCP **客户端**（用别人的工具）──
    1. FakeMcpServer        ：进程内「假服务器」，回应 initialize / tools/list / tools/call，
                              替代真 Codex 通过 stdio 跟 rmcp 子进程对话；
    2. McpConnectionManager ：连接 + 聚合各服务器的工具，把工具名命名空间化成
                              `mcp__<server>__<tool>` 暴露给模型，再把模型的调用路由回服务器。

  ── 方向 B：Codex 作为 MCP **服务端**（被别人当工具）──
    3. CodexMcpServer       ：自己实现一个 handle(request)，处理 initialize / tools/list /
                              tools/call，对外暴露 `shell`（跑一条命令）和 `codex`
                              （用回合循环跑一整个任务）——后者正是「Codex 成为别人可调用的子代理」。

真 Codex：客户端走 JSON-RPC over stdio（rmcp 库，协议版本 2025-06-18，见
codex-rs/codex-mcp/）；服务端用三条 tokio 任务（读 stdin / 处理 / 写 stdout）在 stdio 上
搬 JSON-RPC，暴露 `codex` + `codex-reply`（见 codex-rs/mcp-server/）。这里把两条 stdio 管道
都换成进程内方法调用，但消息形状（jsonrpc / id / method / params / result）忠实保留。
"""

import json
import subprocess
import sys
import threading
import time
from pathlib import Path

# 仓库根目录加入 import 路径，复用共享模型模块。
sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
from codexlib import Model, user_item, tool_output_item  # noqa: E402

# MCP 工具名的命名空间分隔符与历史前缀（对齐 codex-mcp/src/tools.rs:28/260）。
MCP_PREFIX = "mcp__"
MCP_DELIM = "__"

WORKDIR = Path.cwd()
model = Model()


# ═══════════════════════════════════════════════════════════
#  FROM s01（搬运）：shell 工具
#  两个方向都要用它——客户端的 demo 不需要，但服务端的 `codex`/`shell` 工具需要真去跑命令。
# ═══════════════════════════════════════════════════════════

def run_shell(command: str) -> str:
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
#  方向 A — 客户端这一侧：进程内「假 MCP 服务器」（替身，让 demo 离线可跑）
#
#  真 Codex 把每个 MCP server 当作子进程，用 JSON-RPC over stdio 通信
#  （见 codex-rs/codex-mcp/src/rmcp_client.rs）。我们不开子进程、不走网络，
#  而用一个普通 Python 对象顶替：handle() 收一个请求 dict、返回一个响应 dict。
#  消息形状（jsonrpc/id/method/params/result）与线协议一致，只是传输从 stdio
#  变成了直接函数调用。
# ═══════════════════════════════════════════════════════════

class FakeMcpServer:
    """一个最小的「别人家」MCP 服务器：暴露 echo / add 两个工具。"""

    def __init__(self, name: str = "demo") -> None:
        self.name = name
        # 工具清单：每个工具一个 name / description / inputSchema（注意 MCP 的字段名）。
        self._tools = [
            {"name": "echo", "description": "Echo back the given text.",
             "inputSchema": {"type": "object",
                             "properties": {"text": {"type": "string"}},
                             "required": ["text"]}},
            {"name": "add", "description": "Add two integers a and b.",
             "inputSchema": {"type": "object",
                             "properties": {"a": {"type": "integer"}, "b": {"type": "integer"}},
                             "required": ["a", "b"]}},
        ]

    def handle(self, request: dict) -> dict:
        """JSON-RPC 入口：按 method 分派，永远返回一个响应 dict。"""
        rid, method, params = request.get("id"), request.get("method"), request.get("params", {}) or {}
        if method == "initialize":
            # 握手：服务器自报家门 + 协议版本（真 rmcp 钉死 2025-06-18）。
            return self._ok(rid, {
                "protocolVersion": "2025-06-18",
                "serverInfo": {"name": self.name, "version": "0.0.0"},
                "capabilities": {"tools": {}},
            })
        if method == "tools/list":
            return self._ok(rid, {"tools": self._tools})
        if method == "tools/call":
            return self._call_tool(rid, params.get("name", ""), params.get("arguments", {}) or {})
        # 未知方法 → JSON-RPC 错误对象（code -32601 = method not found）。
        return {"jsonrpc": "2.0", "id": rid,
                "error": {"code": -32601, "message": f"method not found: {method}"}}

    def _call_tool(self, rid, name: str, args: dict) -> dict:
        # MCP 的 tools/call 结果形状：{content:[{type:text,text:...}], isError:bool}
        if name == "echo":
            return self._tool_result(rid, str(args.get("text", "")))
        if name == "add":
            try:
                return self._tool_result(rid, str(int(args["a"]) + int(args["b"])))
            except (KeyError, ValueError, TypeError) as e:
                return self._tool_result(rid, f"bad args: {e}", is_error=True)
        return self._tool_result(rid, f"unknown tool '{name}'", is_error=True)

    @staticmethod
    def _ok(rid, result: dict) -> dict:
        return {"jsonrpc": "2.0", "id": rid, "result": result}

    @classmethod
    def _tool_result(cls, rid, text: str, is_error: bool = False) -> dict:
        return cls._ok(rid, {"content": [{"type": "text", "text": text}], "isError": is_error})


# ═══════════════════════════════════════════════════════════
#  方向 A — NEW in s15：MCP 连接管理器（客户端这一侧）
#
#  对齐 codex-rs/codex-mcp/src/connection_manager.rs 的 McpConnectionManager：
#    · add_server：连接并 initialize 每个服务器；
#    · list_all_tools()：聚合各服务器的工具，名字加前缀命名空间化；
#    · call_tool(server, tool, args)：把模型的调用路由回正确的服务器。
#  真版本管理多个异步 rmcp 客户端；这里 clients 就是一组 FakeMcpServer。
# ═══════════════════════════════════════════════════════════

class McpConnectionManager:
    def __init__(self) -> None:
        self.clients: dict[str, FakeMcpServer] = {}
        self._next_id = 0

    def _rpc(self, server: FakeMcpServer, method: str, params: dict | None = None) -> dict:
        """发一条 JSON-RPC 请求给某个服务器，拿回 result（或抛错）。"""
        self._next_id += 1
        resp = server.handle({"jsonrpc": "2.0", "id": self._next_id,
                              "method": method, "params": params or {}})
        if "error" in resp:
            raise RuntimeError(resp["error"]["message"])
        return resp["result"]

    def add_server(self, server) -> bool:
        """连接一个服务器：先 initialize 握手，再登记。

        生产级：MCP server 是独立进程/远端，**一个连不上不能拖垮其余的**。这里把
        initialize 包进 try——握手失败就跳过它、继续连别的（真 Codex 用 join_set 并发连，
        connection_manager.rs:302，单个失败不影响全局）。"""
        try:
            info = self._rpc(server, "initialize")
        except RuntimeError as e:
            print(f"\033[31m[mcp] 跳过 '{server.name}'：初始化失败（{e}），其余服务器照常\033[0m")
            return False
        self.clients[server.name] = server
        print(f"\033[90m[mcp] connected '{server.name}' "
              f"(protocol {info.get('protocolVersion')})\033[0m")
        return True

    def list_all_tools(self) -> list[dict]:
        """聚合所有服务器的工具，转成模型能用的「扁平」工具定义（带命名空间名）。"""
        out: list[dict] = []
        for name, server in self.clients.items():
            for t in self._rpc(server, "tools/list")["tools"]:
                out.append({
                    # 关键：模型看到的是命名空间化的名字，防止多服务器撞名。
                    "name": f"{MCP_PREFIX}{name}{MCP_DELIM}{t['name']}",
                    "description": t.get("description", ""),
                    # MCP 叫 inputSchema；Responses API 工具叫 parameters。这里做字段转换。
                    "parameters": t.get("inputSchema", {"type": "object", "properties": {}}),
                })
        return out

    def call_tool(self, server: str, tool: str, arguments: dict) -> str:
        """把 (server, tool) 这一对路由到对应客户端，执行并把结果摊平成文本。"""
        client = self.clients.get(server)
        if client is None:
            return f"unknown MCP server '{server}'"
        result = self._rpc(client, "tools/call", {"name": tool, "arguments": arguments})
        # 把 MCP 的 content 块拼成纯文本，喂回模型（真 Codex 也会序列化 content）。
        text = "".join(c.get("text", "") for c in result.get("content", [])
                       if c.get("type") == "text")
        return ("[tool error] " if result.get("isError") else "") + text

    def call_tool_with_timeout(self, server: str, tool: str, arguments: dict,
                               timeout_s: float) -> str:
        """生产级：给一次 MCP 工具调用套上超时。MCP server 在另一个进程/远端，
        一个 hang 住的工具不能把整个 agent 永远卡死——超时就**停止等待、把错误回灌给模型**
        （真 Codex：每个 server 一个 tool_timeout，connection_manager.rs:499/562）。"""
        box: dict = {}
        worker = threading.Thread(
            target=lambda: box.__setitem__("out", self.call_tool(server, tool, arguments)),
            daemon=True)
        worker.start()
        worker.join(timeout_s)
        if worker.is_alive():           # 到点还没返回 → 超时，停止等待
            return (f"[mcp timeout] 工具 {server}/{tool} 超过 {timeout_s}s 未返回；"
                    f"停止等待、丢弃这次调用，错误回灌给模型")
        return box.get("out", "[mcp] 无结果")


def split_mcp_tool_name(qualified: str):
    """把模型发来的 `mcp__<server>__<tool>` 拆回 (server, tool)。"""
    if not qualified.startswith(MCP_PREFIX):
        return None
    rest = qualified[len(MCP_PREFIX):]
    server, _, tool = rest.partition(MCP_DELIM)
    return (server, tool) if server and tool else None


# 组装客户端这一侧：连上一个假服务器，把它的工具暴露给模型。
manager = McpConnectionManager()
manager.add_server(FakeMcpServer("demo"))
TOOLS = manager.list_all_tools()

SYSTEM = (
    "You are Codex. You can call MCP tools exposed by connected servers. "
    "Tool names are namespaced as mcp__<server>__<tool>. Act, don't explain."
)


def dispatch(name: str, arguments: dict) -> str:
    """统一的工具分派：凡是 mcp__ 前缀的都路由给 connection manager。"""
    parsed = split_mcp_tool_name(name)
    if parsed is None:
        return f"unknown tool: {name}"
    server, tool = parsed
    return manager.call_tool(server, tool, arguments)


# ═══════════════════════════════════════════════════════════
#  FROM s01（搬运）：回合循环 —— 一字未改的心脏
#  方向 A（客户端）直接用它；方向 B 的 `codex` 工具内部也是同一个循环（见 run_task）。
#  唯一区别是分派函数：客户端用 dispatch（按 mcp__ 前缀路由）。
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
            output = dispatch(tc.name, tc.arguments)
            print(str(output)[:300])
            messages.append(tool_output_item(tc.call_id, output))


# ═══════════════════════════════════════════════════════════
#  方向 B — 服务端这一侧：`codex` 工具 = 跑一整个 Codex 任务
#  这就是 s01 的回合循环再搬一次——当别人调用我们暴露的 `codex` 工具时，
#  就用这个循环把一整个任务跑完，返回最终的 assistant 文本。
# ═══════════════════════════════════════════════════════════

# 给内部回合循环用的工具表（模型在 `codex` 任务里能调用 shell）。
LOOP_TOOLS = [{
    "name": "shell",
    "description": "Run a shell command and return combined stdout+stderr.",
    "parameters": {
        "type": "object",
        "properties": {"command": {"type": "string"}},
        "required": ["command"],
    },
}]
LOOP_HANDLERS = {"shell": run_shell}
LOOP_SYSTEM = f"You are Codex running a task in {WORKDIR}. Use shell. Act, don't explain."


def run_task(prompt: str) -> str:
    """跑一整个 Codex 子任务，返回最终的 assistant 文本。这就是 `codex` 工具的工作。"""
    messages = [user_item(prompt)]
    transcript: list[str] = []
    while True:
        resp = model.respond(messages, tools=LOOP_TOOLS, system=LOOP_SYSTEM)
        messages += resp.output_items
        if not resp.tool_calls:
            return resp.text or "\n".join(transcript) or "(task done)"
        for tc in resp.tool_calls:
            out = LOOP_HANDLERS.get(tc.name, lambda **_: f"unknown tool {tc.name}")(**tc.arguments)
            transcript.append(f"$ {tc.arguments.get('command', tc.name)}\n{out}")
            messages.append(tool_output_item(tc.call_id, out))


# ═══════════════════════════════════════════════════════════
#  方向 B — NEW in s15：Codex 作为 MCP 服务器
#
#  对齐 codex-rs/mcp-server/src/message_processor.rs 的 process_request：
#  一个 handle(request) 按 JSON-RPC 的 method 分派到 initialize / tools/list /
#  tools/call。真服务器暴露 `codex` + `codex-reply`；我们暴露 `shell`（一次命令）
#  和 `codex`（用 run_task 跑一整个任务）—— 后者正是「Codex 成为别人可调用的子代理」。
# ═══════════════════════════════════════════════════════════

class CodexMcpServer:
    # 我们对外宣告的工具（MCP 用 inputSchema 字段名）。
    TOOLS = [
        {
            "name": "shell",
            "description": "Run a single shell command in the Codex workspace.",
            "inputSchema": {
                "type": "object",
                "properties": {"command": {"type": "string"}},
                "required": ["command"],
            },
        },
        {
            "name": "codex",
            "description": "Run a whole Codex coding task from a natural-language prompt.",
            "inputSchema": {
                "type": "object",
                "properties": {"prompt": {"type": "string"}},
                "required": ["prompt"],
            },
        },
    ]

    def handle(self, request: dict) -> dict:
        """JSON-RPC 入口：和真 message_processor 一样按 method 分派。"""
        rid = request.get("id")
        method = request.get("method")
        params = request.get("params", {}) or {}
        if method == "initialize":
            return self._ok(rid, {
                "protocolVersion": "2025-06-18",
                "serverInfo": {"name": "codex-mcp-server", "version": "0.0.0"},
                # 真服务器宣告 tools + toolListChanged 能力。
                "capabilities": {"tools": {"listChanged": True}},
            })
        if method == "tools/list":
            return self._ok(rid, {"tools": self.TOOLS})
        if method == "tools/call":
            return self._call_tool(rid, params.get("name", ""), params.get("arguments", {}) or {})
        # 对齐真服务器：未知 method → -32601 method not found。
        return {"jsonrpc": "2.0", "id": rid,
                "error": {"code": -32601, "message": f"method not found: {method}"}}

    def _call_tool(self, rid, name: str, args: dict) -> dict:
        if name == "shell":
            cmd = args.get("command")
            if not cmd:
                return self._tool_result(rid, "missing 'command'", is_error=True)
            return self._tool_result(rid, run_shell(cmd))
        if name == "codex":
            prompt = args.get("prompt")
            if not prompt:
                return self._tool_result(rid, "missing 'prompt'", is_error=True)
            # 这一步会跑一整个 Codex 回合循环——别人的 agent 借此把 Codex 当子代理。
            return self._tool_result(rid, run_task(prompt))
        # 对齐真服务器 handle_call_tool 的兜底分支。
        return self._tool_result(rid, f"Unknown tool '{name}'", is_error=True)

    @staticmethod
    def _ok(rid, result: dict) -> dict:
        return {"jsonrpc": "2.0", "id": rid, "result": result}

    @classmethod
    def _tool_result(cls, rid, text: str, is_error: bool = False) -> dict:
        # MCP tools/call 结果形状：content 块 + isError。
        return cls._ok(rid, {"content": [{"type": "text", "text": text}], "isError": is_error})


codex_server = CodexMcpServer()


# ───────────────────────────────────────────────────────────
#  --demo：一口气演示两个方向
# ───────────────────────────────────────────────────────────

def _pp(label: str, obj: dict) -> None:
    print(f"\033[33m{label}\033[0m")
    print(json.dumps(obj, ensure_ascii=False, indent=2))


def demo_client() -> None:
    print("\033[1m═══ 方向 A：Codex 作为 MCP 客户端（用别人的工具）═══\033[0m\n")

    # 1) 列出服务器暴露的工具（已命名空间化）——这就是模型会看到的工具表。
    print("连接后聚合到的工具（暴露给模型的名字）：")
    for t in TOOLS:
        print(f"  \033[36m{t['name']}\033[0m — {t['description']}")

    # 2) 直接路由一次调用，展示请求→服务器→结果的完整往返（不依赖模型）。
    print("\n直接调用 mcp__demo__add(a=2, b=3)：")
    print("  →", dispatch("mcp__demo__add", {"a": 2, "b": 3}))

    # 3) 演示「模型调用 → 执行 → 结果回灌 → 模型收口」的完整闭环。
    #    离线 mock 后端不认识 mcp__ 工具名，所以这里手动构造模型本应发出的
    #    function_call（真模型在线时产出的就是这个形状），再交给回合循环消费。
    print("\n经由回合循环（模型发起 mcp__demo__echo 调用，结果流回并收口）：")
    call = {"type": "function_call", "call_id": "c1", "name": "mcp__demo__echo",
            "arguments": json.dumps({"text": "hello from mcp"})}
    print(f"\033[33m> {call['name']} {call['arguments']}\033[0m")
    out = dispatch(call["name"], json.loads(call["arguments"]))
    print(out)
    # 把这次调用与其结果回灌进对话；run_turn 里的模型回合看到结果便收尾。
    run_turn([user_item("请回显 hello"), call, tool_output_item("c1", out)])


def demo_server() -> None:
    print("\n\033[1m═══ 方向 B：Codex 作为 MCP 服务端（被别人当工具）═══\033[0m\n")

    # 1) 握手
    req = {"jsonrpc": "2.0", "id": 1, "method": "initialize", "params": {}}
    _pp("→ initialize", req)
    _pp("← response", codex_server.handle(req))

    # 2) tools/list —— 别的 agent 借此发现 Codex 暴露了哪些工具。
    req = {"jsonrpc": "2.0", "id": 2, "method": "tools/list", "params": {}}
    print()
    _pp("→ tools/list", req)
    _pp("← response", codex_server.handle(req))

    # 3) tools/call shell —— 跑一条命令。
    req = {"jsonrpc": "2.0", "id": 3, "method": "tools/call",
           "params": {"name": "shell", "arguments": {"command": "echo hello from codex-as-server"}}}
    print()
    _pp("→ tools/call (shell)", req)
    _pp("← response", codex_server.handle(req))

    # 4) tools/call codex —— 跑一整个任务（内部走 s01 回合循环，离线 mock 模型驱动）。
    #    这一步就是「别的 agent 把整个 Codex 当成一次工具调用」。
    req = {"jsonrpc": "2.0", "id": 4, "method": "tools/call",
           "params": {"name": "codex", "arguments": {"prompt": "运行 `echo built by codex`"}}}
    print()
    _pp("→ tools/call (codex = 跑一整个任务)", req)
    _pp("← response", codex_server.handle(req))


class _SlowServer:
    """生产级演示：一个 tools/call 会卡住的服务器（模拟挂死的 MCP server）。"""
    name = "slow"

    def handle(self, request: dict) -> dict:
        rid, m = request.get("id"), request.get("method")
        if m == "initialize":
            return {"jsonrpc": "2.0", "id": rid, "result": {
                "protocolVersion": "2025-06-18", "serverInfo": {"name": self.name},
                "capabilities": {"tools": {}}}}
        if m == "tools/list":
            return {"jsonrpc": "2.0", "id": rid, "result": {"tools": [
                {"name": "hang", "description": "hangs",
                 "inputSchema": {"type": "object", "properties": {}}}]}}
        if m == "tools/call":
            time.sleep(0.5)             # 模拟卡住（真实里可能是网络/子进程挂死）
            return {"jsonrpc": "2.0", "id": rid, "result": {
                "content": [{"type": "text", "text": "(终于返回，但已太晚)"}], "isError": False}}
        return {"jsonrpc": "2.0", "id": rid, "error": {"code": -32601, "message": "x"}}


class _BrokenServer:
    """生产级演示：initialize 就崩的服务器。"""
    name = "broken"

    def handle(self, request: dict) -> dict:
        return {"jsonrpc": "2.0", "id": request.get("id"),
                "error": {"code": -32603, "message": "server crashed during initialize"}}


def demo_production() -> None:
    print("\n\033[1m═══ 生产级：超时 + 连接韧性（MCP server 在进程外，会卡、会崩）═══\033[0m\n")
    mgr = McpConnectionManager()
    print("① 连接韧性：一个 server 初始化就崩，其余照常连上 ——")
    mgr.add_server(_BrokenServer())        # 崩 → 被跳过
    mgr.add_server(FakeMcpServer("demo"))  # 好 → 连上
    print(f"   最终可用 server：{list(mgr.clients)}（broken 被跳过，没拖垮 demo）")
    print("\n② 调用超时：一个 hang 住的工具不会把 agent 永远卡死 ——")
    mgr.add_server(_SlowServer())
    print("   调用 slow/hang（超时 0.1s）：")
    print("   →", mgr.call_tool_with_timeout("slow", "hang", {}, timeout_s=0.1))


def demo() -> None:
    print("s15 demo：MCP 的两个方向——能力不够就插，也能被别人插\n")
    demo_client()
    demo_server()
    demo_production()
    print("\n\033[90m两个方向同源：客户端用 mcp__ 前缀路由出去，"
          "服务端用 handle(request) 接进来；中间都是同一套 JSON-RPC。\033[0m")


# ───────────────────────────────────────────────────────────
#  交互式 REPL 兜底：默认演示「客户端」方向（你的问题 → 模型 → MCP 工具）。
#  想体验「服务端」方向，用 --demo 或参见 README。
# ───────────────────────────────────────────────────────────

if __name__ == "__main__":
    if "--demo" in sys.argv:
        demo()
        sys.exit(0)

    print("s15: MCP（客户端 + 服务端）——交互模式默认走「客户端」方向（输入 q 退出）\n")
    print("可用 MCP 工具：" + ", ".join(t["name"] for t in TOOLS))
    print("（想看「服务端」方向，跑 python s15_mcp/code.py --demo）\n")
    history: list[dict] = []
    while True:
        try:
            query = input("\033[36ms15 >> \033[0m")
        except (EOFError, KeyboardInterrupt):
            break
        if query.strip().lower() in ("q", "exit", ""):
            break
        history.append(user_item(query))
        run_turn(history)
        print()
