# s15: MCP — 客户端 + 服务端：能力不够就插，也能被别人插

> 🌐 [English](README.en.md) · **中文**

> *"MCP 是 agent 世界的标准插座：任何工具都能插进任何 agent，任何 agent 也能反过来插进别的 agent。"*

[learn-codex 总览](../README.md) · [Guardian](../s14_guardian/) → **本章** → [Config 与 Profiles](../s16_config/)

---

## 先把思想说透：为什么需要一个「标准插座」，以及它最妙的一点

到这一章为止，你的 agent 手里的工具都是**写死在自己代码里**的：`shell`、`apply_patch`……想加一个新能力，就得改 agent 的源码。这一章要讲的 MCP，本质上只想解决一句话的问题：**怎么让 agent 和工具，像电器和插座那样即插即用？** 想通下面三个递进的道理，这一章就通了。

**道理一：没有标准，就是 N×M 份胶水代码。**
设想你有 3 个 agent（Codex、Claude Code、你自己写的脚本），和 5 个外部能力（查 GitHub issue、跑 SQL、画图、查天气、读公司知识库）。如果每个 agent 都要为每个能力**单独写一段对接代码**——定义工具 schema、塞进工具表、写 handler、处理鉴权——那就是 3×5 = 15 份互不复用的胶水。再加一个 agent 或一个能力，就要再写一整列、一整行。这正是"没有标准接口"的世界：每个组合都得重新焊一次线。

**道理二：定一个标准的「插头形状」，N×M 就塌缩成 N+M。**
解法和 USB-C 一模一样：**约定一套谁都遵守的接口**。能力方实现一次"标准插头"（一个 MCP **server**），agent 方实现一次"标准插座"（一个 MCP **client**），两边讲同一套 JSON-RPC 对话——无非就是几个固定的"问句"：`initialize`（握手）、`tools/list`（你有哪些工具？）、`tools/call`（帮我调一下这个工具）。于是任何 client 都能接任何 server：5 个能力各做一次 server，3 个 agent 各做一次 client，一共只要 3+5 = 8 份代码，且任意新组合**零额外成本**。"标准"的全部威力，就是把乘法变成加法。

**道理三（本章的灵魂）：插座和插头，可以是同一个东西——这就是双向性。**
前两点你可能在别处也听过。MCP 真正深刻、也最容易被忽略的一点是：**一个 agent 不必只当"用工具的人"，它可以反过来把自己整个变成"一个工具"，被别的 agent 调用。** Codex 既是 client（去用别人的 echo / SQL / 画图工具），又能当 server——把"跑一整个 Codex 编码任务"暴露成一个叫 `codex` 的工具，让别的 agent（甚至 Claude Code、甚至 CI 流水线）像调函数一样调它："喂，帮我把这个模块的测试补全并跑通。"

为什么这一点这么关键？因为它直接决定了 agent 能不能**互相编排、堆成一个系统**。如果 agent 只能当 client，那它永远是"金字塔顶端、面向人"的那一个；可一旦它也能当 server，agent 之间就能**互相调用**：上层负责拆任务，把脏活外包给下层；多个 Codex 可以并行被一个编排器调起来批量干活。打个比方——会用工具的，是个**能干的工人**；能被别人当工具调用的，是一个**可以被随时雇佣、塞进任何流水线的工种**。Codex 两头都站，这正是它"为无人值守、可被程序编排而生"的最直接体现。

这一章就把这两半合在一起讲：先看 Codex 当 client（用别人的工具），再看 Codex 当 server（被别人当工具）。同一套 JSON-RPC，两个方向。

## 问题

具体感受一下两个方向各自的痛点。

**方向 A 的痛点（该用别人的工具时）：** 你让 Codex"看看仓库里有哪些未关闭的 GitHub issue，挑一个修了"。可 Codex 内置工具里根本没有"查 GitHub"这一项。难道要给 Codex 的源码里硬塞一段 GitHub API 对接？那明天你想接 Jira、后天想接公司内部的工单系统，是不是还得各改一遍源码？**能力散落在世界各处，而 agent 的工具表是写死的——这中间缺一座即插即用的桥。**

**方向 B 的痛点（该被别人调用时）：** 反过来，Claude Code 正帮你重构一个大仓库，遇到一段它不熟的代码，想"外包"给 Codex 跑一个独立子任务。或者你的 CI 流水线想在某一步直接喊一句"让 Codex 修掉这个 lint 错误"。可如果 Codex 只会当一个**面向人的 CLI/TUI**，这些场景就接不上——你总得有个活人坐在终端前敲命令。**要让 Codex 能被程序驱动、被别的 agent 当成一个可调用的能力，它就得反过来当 server。**

两个痛点，其实是同一枚硬币的两面：**agent 与外界之间，需要一套双向的、标准的接口。** 这就是 MCP。

## 解决方案

MCP（Model Context Protocol）把"agent ↔ 工具"标准化成一套 JSON-RPC 对话。本章用一个**完全离线**的进程内实现，把两个方向都演示出来。

**方向 A（client）：** 中间放一个**连接管理器**。它持有若干 server 连接，聚合各家的工具清单，把工具名**加前缀命名空间化**成 `mcp__<server>__<tool>`（防止两个 server 都叫 `search` 时撞名），交给模型。模型发起调用时，按前缀把它**路由**回正确的 server。

**方向 B（server）：** Codex 自己实现一个 `handle(request)`，对外暴露工具。最关键的工具叫 **`codex`**：调用它、传一个自然语言 `prompt`，Codex 就**启动一整个回合循环**把任务跑完，再把结果作为 `tools/call` 响应送回去。

```
   ┌─────────── 方向 A：Codex 当 client ───────────┐    ┌──── 方向 B：Codex 当 server ────┐
   │                                               │    │                                 │
   │  回合循环 ──mcp__demo__echo──┐                  │    │   别的 agent / CI / 脚本         │
   │     ▲                       ▼                  │    │        │ tools/call             │
   │     │结果回灌   ┌────────────────────┐         │    │        │  name="codex"          │
   │     └──────────┤ McpConnectionMgr   │         │    │        ▼  prompt="修好这个 bug" │
   │                │ ·add_server/init    │ JSON-RPC│    │   ┌────────────────────────┐   │
   │                │ ·list_all_tools     │◀───────▶│    │   │ CodexMcpServer.handle() │   │
   │                │ ·call_tool(srv,tool)│  (真版  │    │   │  initialize             │   │
   │                └────────────────────┘  走 stdio│    │   │  tools/list→[shell,codex]│  │
   │                         ▲                │      │    │   │  tools/call ─┬ shell     │   │
   │   ┌─────────────────────┴──────────┐    │      │    │   │              └ codex →    │   │
   │   │ FakeMcpServer（别人家的工具）    │    │      │    │   │     run_task() = s01 循环 │   │
   │   │  echo / add                     │    │      │    │   └────────────────────────┘   │
   │   └─────────────────────────────────┘   │      │    │        │ result.content         │
   └─────────────────────────────────────────┘      │    │        ▼ 回到调用方             │
                                                     │    └─────────────────────────────────┘
   两个方向同源：都是 initialize / tools/list / tools/call 这套 JSON-RPC，只是谁问、谁答互换。
```

为了离线、零依赖，本章两侧都把 stdio 传输层抽掉，换成**进程内的方法调用**：`handle(request_dict) -> response_dict`。真 Codex 是把每个 server 当**子进程**、用 JSON-RPC over stdio 通信——但请求/响应的 dict 形状是一致的，换掉的只是"传输层"那一层皮。

## 工作原理

看 [code.py](code.py)。它把两个方向放进**同一个文件**，共享同一套 JSON-RPC 形状和同一个 s01 回合循环。

### 方向 A：Codex 当 client（用别人的工具）

**第 1 步 — 进程内 server 替身。** `FakeMcpServer.handle()` 按 JSON-RPC 的 `method` 分派，暴露 `echo` / `add` 两个工具：

```python
def handle(self, request: dict) -> dict:
    rid, method, params = request.get("id"), request.get("method"), request.get("params", {}) or {}
    if method == "initialize":
        return self._ok(rid, {"protocolVersion": "2025-06-18", ...})
    if method == "tools/list":
        return self._ok(rid, {"tools": self._tools})
    if method == "tools/call":
        return self._call_tool(rid, params.get("name", ""), params.get("arguments", {}) or {})
```

**第 2 步 — 连接 + 聚合 + 命名空间化。** `McpConnectionManager.add_server()` 先发 `initialize` 握手；`list_all_tools()` 把每个 server 的工具名改写成 `mcp__<server>__<tool>`，并把 MCP 的 `inputSchema` 字段转成 Responses API 工具用的 `parameters`：

```python
out.append({
    "name": f"{MCP_PREFIX}{name}{MCP_DELIM}{t['name']}",   # mcp__demo__echo
    "description": t.get("description", ""),
    "parameters": t.get("inputSchema", {...}),             # MCP 叫 inputSchema
})
```

这对齐真源码 [`connection_manager.rs`](../../codex/codex-rs/codex-mcp/src/connection_manager.rs) 的 `list_all_tools()` → `normalize_tools_for_model_with_prefix`，前缀常量 `mcp__`、分隔符 `__` 来自 [`tools.rs:28/260`](../../codex/codex-rs/codex-mcp/src/tools.rs)。

**第 3 步 — 路由调用。** 回合循环（**从 s01 原样搬运**）里，工具分派从"查 HANDLERS 字典"换成 `dispatch()`：凡是 `mcp__` 前缀，就拆出 `(server, tool)` 交给 `manager.call_tool()`，再把返回的 `content` 块拼成纯文本回灌模型。

```python
def dispatch(name, arguments):
    server, tool = split_mcp_tool_name(name)        # mcp__demo__echo → ("demo","echo")
    return manager.call_tool(server, tool, arguments)
```

### 方向 B：Codex 当 server（被别人当工具）

核心是一个 `CodexMcpServer.handle()`，对齐真源码 [`message_processor.rs`](../../codex/codex-rs/mcp-server/src/message_processor.rs) 的 `process_request`：同样按 `method` 分派到 `initialize` / `tools/list` / `tools/call`，未知 method 同样回 `-32601 method not found`。`initialize` 宣告"支持 tools / toolListChanged"——对齐真 `handle_initialize` 里的 `ServerCapabilities::builder().enable_tools().enable_tool_list_changed()`。

最关键的是 `tools/call` 里的 **`codex` 工具 = 跑一整个 Codex 任务**。`run_task()` 就是 **s01 的回合循环**再搬一次：把 prompt 当第一条用户消息，模型循环调用 `shell` 直到说完，返回最终文本：

```python
def _call_tool(self, rid, name, args):
    if name == "shell":
        return self._tool_result(rid, run_shell(args["command"]))
    if name == "codex":
        return self._tool_result(rid, run_task(args["prompt"]))   # ← 跑一整个任务
```

这正是真 server 干的事，只是真版更重：`handle_tool_call_codex`（`message_processor.rs:346`）解析参数、`spawn` 一个异步任务、调用 [`run_codex_tool_session`](../../codex/codex-rs/mcp-server/src/codex_tool_runner.rs)，后者通过 `Op::UserInput` 把 prompt 提交进一个**完整的** Codex thread。

### **走一遍**：方向 B 的 `codex` 工具，一次调用里数据怎么流动

最值得走一遍的，是"别的 agent 把整个 Codex 当一次工具调用"这件事——因为它把本章两个方向、和前面所有章都串了起来。假设 CI 流水线发来这样一条 JSON-RPC 请求（这就是 `--demo` 的第 4 步）：

**① 进来的请求**（一个普通的 `tools/call`，工具名是 `codex`）：

```json
{ "jsonrpc": "2.0", "id": 4, "method": "tools/call",
  "params": { "name": "codex", "arguments": { "prompt": "运行 `echo built by codex`" } } }
```

**② `handle()` 按 method 分派** → `tools/call` → `_call_tool(name="codex", ...)` → 发现是 `codex`，于是调 `run_task("运行 `echo built by codex`")`。**为什么这一跳是重点**：从这里开始，被调的 Codex 走的就是和你平时用它**一模一样**的回合循环——它不知道自己是被一个 agent 调起来的，它只是在跑一个任务。

**③ `run_task` 内部第一圈**：把 prompt 当第一条 user 消息发给模型。模型（这里是离线 mock）看到工具表里有 `shell`、又从 prompt 的反引号里抠出命令，于是产出一个 `function_call`：

```json
{ "type": "function_call", "call_id": "mock_call_1",
  "name": "shell", "arguments": "{\"command\": \"echo built by codex\"}" }
```

**④ 执行 + 回灌**：`run_shell("echo built by codex")` 真的在子进程里跑了一下，拿到 `"built by codex"`，把它包成 `function_call_output` 追加回对话。

**⑤ `run_task` 第二圈**：模型再被调用，这次它看到对话里已经有工具结果了，于是不再调工具、直接收口，返回最终文本 `"[mock] 工具已执行，结果片段：built by codex"`。`run_task` 返回这串文本。

**⑥ 出去的响应**：`_tool_result` 把它包成标准的 MCP `content` 块送回 CI：

```json
{ "jsonrpc": "2.0", "id": 4,
  "result": { "content": [ { "type": "text", "text": "[mock] 工具已执行，结果片段：built by codex" } ],
              "isError": false } }
```

看明白了吗——**对 CI 来说，它只是"调了一个工具、拿到一段文本"**，跟调 `echo` 没两样；可这一次工具调用的背后，是一整个 Codex 会话在跑回合循环、调 shell、读结果、收口。这就是"agent 当工具"的全部魔法：把任意复杂的自主过程，藏进一次普普通通的 `tools/call` 里。

`--demo` 把两个方向接连跑一遍：方向 A 打印命名空间化的工具表、直接 `dispatch("mcp__demo__add",{a:2,b:3})` 看 `→ 5`、再走一遍完整回合循环；方向 B 依次喂 `initialize` → `tools/list` → `tools/call(shell)` → `tools/call(codex)`，最后那个就是上面走的这一遍。

## 生产级：MCP server 在进程外——会卡、会崩、会撞名

MCP 最大的现实是：每个 server 是一个**你管不着的独立进程**（甚至在另一台机器）。它会握手失败、会调用到一半挂住、不同 server 还会暴露**同名**工具。玩具假设它们都乖；生产级 harness 假设它们都会出岔子。

### 一、超时：一个 hang 住的工具不能卡死整个 agent

`tools/call` 走出去后，server 可能永远不返回（死循环、网络黑洞、子进程僵死）。同步等它，整个 agent 就跟着冻住。真 Codex 给每个 server 配一个 `tool_timeout`（[`connection_manager.rs:499`](../../codex/codex-rs/codex-mcp/src/connection_manager.rs)），到点就**停止等待、把超时当错误回灌给模型**。本章 `call_tool_with_timeout` 演示了这条：

```
② 调用超时：一个 hang 住的工具不会把 agent 永远卡死 ——
   调用 slow/hang（超时 0.1s）：
   → [mcp timeout] 工具 slow/hang 超过 0.1s 未返回；停止等待、丢弃这次调用，错误回灌给模型
```

### 二、连接韧性：一个 server 崩了，不能拖垮其余的

你可能挂了 5 个 MCP server，其中一个 initialize 就崩。生产级做法是**并发连接、单个失败只跳过它**（真 Codex 用 `join_set` 并发拉起所有 server，`connection_manager.rs:302`；还有 `wait_for_server_ready(timeout)` 给握手也设上限）。本章 `add_server` 把握手包进 try：

```
① 连接韧性：一个 server 初始化就崩，其余照常连上 ——
   [mcp] 跳过 'broken'：初始化失败（server crashed during initialize），其余服务器照常
   最终可用 server：['demo']（broken 被跳过，没拖垮 demo）
```

### 三、撞名 + 太多：命名空间与延迟暴露

- **撞名**：两个 server 都暴露一个 `search`，模型该调谁？本章早把工具名**命名空间化**成 `mcp__<server>__<tool>`（真 Codex 还会 `sanitize_name` 清掉非法字符，`codex_apps.rs`）——多 server 不会撞车。
- **太多**：一个 server 可能甩来几百个工具，全塞进 prompt 会撑爆上下文。这正是 [s02 的 ToolSearch / 延迟暴露](../s02_tool_use/)——MCP 工具超过阈值（`DIRECT_MCP_TOOL_EXPOSURE_THRESHOLD = 100`）就不直接暴露、改由模型搜索发现。

### 四、外来工具同样受审批 + 沙箱约束

MCP 工具来自**第三方**，更不能无条件信任。它的调用（`McpToolCall`）一样进审批门（[s04](../s04_approval/)）、被 Guardian 评风险（[s14](../s14_guardian/)）、跑命令一样在沙箱里（[s05](../s05_sandbox/)）。"能插别人的工具"不等于"插进来就放行"。

> 一句话：把别人的工具接进来，难的不是 JSON-RPC，而是**假设对方会卡、会崩、会撞名、不可信**，然后每一条都兜住。

## 🆚 与 Claude Code 的不同

| | Claude Code | Codex | |
|---|---|---|---|
| 当 MCP **client**（用别人工具） | ✅（主力） | ✅ | 几乎一样 |
| 工具命名空间 | `mcp__<server>__<tool>` | `mcp__<server>__<tool>` | 同款历史前缀 |
| 传输 | stdio / SSE / HTTP | stdio（rmcp）/ Streamable HTTP | 大同小异 |
| 当 MCP **server**（被别人调） | 基本不做 | ⭐ **也能** | 分野所在 |
| 被调的粒度 | — | 一次 `tools/call codex` = 跑**一整个 Codex 任务** | agent 当工具 |
| 典型使用方 | 人坐在终端前 | 另一个 agent / CI 流水线 / 云端编排器 | 面向程序 vs 面向人 |

**为什么？** 这张表上半截两边几乎**一样**——这恰恰是 MCP 作为"开放共享标准"的意义：**client 这一侧是大家的公共底座**，谁都该会接别人的工具，连命名空间约定都一致（`mcp__server__tool`），因为它们都要塞进同一类"扁平工具名"的模型 API 里。在 client 这件事上较劲没有意义，标准的价值就在于一致。

真正的分野在下半截那颗 ⭐：**方向性**。Codex 不仅当 client，还能反过来**当 server**，把自己整个 agent 暴露成别人的一个工具——这就是 **agents-as-tools（把 agent 当工具）**。

这一处差异，是全书主线"**Claude Code 在应用层为人而挡，Codex 在内核层为无人值守而关**"最直接的落点。Claude Code 把自己定位成**面向人的交互式前端**：它做 client，把外部能力接进来给人用；它不太需要把"整个 Claude Code"暴露成别人的一个函数——因为它的核心价值是那块**人机交互**的体验。

Codex 则为"**低人工干预、可被编排**"下注。一个能当 server 的 Codex，意味着它可以被塞进 CI（某一步直接 `tools/call codex { prompt: "修好失败的测试" }`）、被云端编排器当成可并行调度的子代理、被另一个 agent（包括 Claude Code 自己）当成一个工具——上层负责拆解编排，把脏活外包给 Codex。换句话说：**当 client 让 Codex 成为别人的消费者，当 server 让 Codex 成为别人的能力。** 后者只有在你押注"agent 应该能在没人盯着时被程序驱动"时才划算——而这正是 Codex 一以贯之的取向（内核沙箱 [s05](../s05_sandbox/)、headless `codex exec`、SQ/EQ 多前端 [s10](../s10_sq_eq_protocol/)，全都服务于此）。

## 深入：教学版 vs 真 Codex 源码

教学版把两个方向各压到几十行、共一个文件，讲清了"连接→命名空间→路由"和"分派→`codex` 工具跑整个任务"。真 Codex 的两个 crate——客户端 [`codex-rs/codex-mcp`](../../codex/codex-rs/codex-mcp/) 与服务端 [`codex-rs/mcp-server`](../../codex/codex-rs/mcp-server/)——的复杂度，几乎全在"传输并发、生命周期、命名安全、双向审批"这几件事上。

<details>
<summary>一、传输：进程内 dict 调用 vs stdio 上的子进程 / 三条 tokio 任务</summary>

教学版两侧的 `handle()` 都是普通方法调用，同步、零延迟。真 Codex 完全不同：

**客户端侧**——每个 MCP server 是一个被 spawn 出来的**子进程**（或一个 HTTP 端点）。Codex 用 [rmcp](https://crates.io/crates/rmcp) 库当客户端，通过子进程的 stdin/stdout 收发 JSON-RPC，或走 `StreamableHttp`。握手是真的 `initialize`，带 `ClientCapabilities`、客户端 `Implementation { name: "codex-mcp-client", ... }`，并钉死协议版本 `ProtocolVersion::V_2025_06_18`（`rmcp_client.rs`）。因为是异步 + 子进程，就有了一堆教学版没有的东西：启动超时（`DEFAULT_STARTUP_TIMEOUT = 30s`）、工具调用超时（`DEFAULT_TOOL_TIMEOUT = 120s`）、启动状态机（`McpStartupStatus::{Starting, Ready, Failed, Cancelled}`）、进程退出时 `shutdown()` 掉所有子进程。

**服务端侧**——真 server（[`lib.rs:run_main`](../../codex/codex-rs/mcp-server/src/lib.rs)）是个三任务流水线：

```
stdin ──► [stdin_reader] ──incoming_tx──► [processor] ──outgoing_tx──► [stdout_writer] ──► stdout
          逐行解析            MessageProcessor              序列化 + 写一行 JSON
          成 JsonRpcMessage    ::process_request
```

用 `tokio::spawn` 起三个并发任务，中间用 channel（`CHANNEL_CAPACITY = 128`）连接，最后 `tokio::join!` 一起等。之所以要拆任务：`tools/call codex` 可能跑很久，绝不能阻塞读 stdin / 写 stdout。教学版的同步 `handle()` 没这个问题，因为 mock 任务瞬间返回。

| | 教学版 | 真 codex-rs |
|---|---|---|
| 传输 | 进程内方法调用 | JSON-RPC / stdio（子进程）或 HTTP |
| 协议版本 | 字符串 `"2025-06-18"` | `ProtocolVersion::V_2025_06_18` |
| 并发 | 单线程同步 | client 异步 rmcp；server 三条 tokio 任务 |
| 超时/取消 | 无 | startup 30s、tool 120s、`CancellationToken` |

</details>

<details>
<summary>二、命名冲突、长度上限与 SHA-1 哈希后缀</summary>

教学版直接 `mcp__{server}__{tool}` 拼起来就完事，因为我们的名字短、不会撞。真 `normalize_tools_for_model_with_prefix`（[`tools.rs:149`](../../codex/codex-rs/codex-mcp/src/tools.rs)）要解决三个现实问题：

1. **撞名**：两个不同 server 的工具规整后可能同名 → 给冲突项追加 `_<sha1前12位>` 哈希后缀（`append_hash_suffix` / `CALLABLE_NAME_HASH_LEN = 12`）。
2. **长度**：模型 API 对工具名有上限，`MAX_TOOL_NAME_LENGTH = 64` 字节，超了要截断 + 哈希。
3. **非法字符**：`sanitize_responses_api_tool_name` 把 server/tool 名里 API 不接受的字符清洗掉。

所以真版区分"原始名（protocol 调用用）"和"model-visible 名（喂给模型用）"两套——教学版只有一套。

</details>

<details>
<summary>三、连接生命周期、资源聚合与工具暴露过滤</summary>

教学版 `clients` 是个 `dict`，`add_server` 顺序握手；`list_all_tools` 把所有工具无脑塞给模型。真 `McpConnectionManager` 远不止：

- 用一个 `JoinSet` **并发**启动所有 server，每个启动进度都通过事件往前端发（`McpStartupUpdateEvent` / 最后一条 `McpStartupComplete` 汇总 ready / failed / cancelled）。
- 还聚合 **resources** 与 **resource templates**（`list_all_resources` / `list_all_resource_templates`，带游标分页与重复游标检测）——MCP 不只有工具，还有可读资源，教学版完全略过。
- **可见性过滤**：`tool_is_model_visible`（`connection_manager.rs:86`）检查工具 `_meta.ui.visibility` 是否含 `"model"`，给"只给人看、不给模型用"的 UI 工具留了后门。
- **数量阈值**：`build_mcp_tool_exposure`（[`mcp_tool_exposure.rs`](../../codex/codex-rs/core/src/mcp_tool_exposure.rs)）有个 `DIRECT_MCP_TOOL_EXPOSURE_THRESHOLD = 100`：工具太多时不直接全暴露，而是转成"**deferred**（延迟/可搜索）"工具，避免一次性塞爆模型上下文。

</details>

<details>
<summary>四、服务端的双向：反过来向 client 请求审批，与 codex-reply 多轮续聊</summary>

这是"Codex 当 server"最有意思、也最能体现 MCP 双向性的两点：

**反向审批（elicitation）**——被调的 Codex 在跑任务时若要执行危险命令或打补丁，它需要审批，可此刻**没有人**，只有调用它的那个 client。于是审批本身也走 MCP，**反方向**发回去：`exec_approval.rs` / `patch_approval.rs` 定义了 `ExecApprovalElicitRequestParams` / `PatchApprovalElicitRequestParams`，server 向 client 发一个 elicitation/审批请求，等 client 回应再继续。也就是说：MCP server 既**响应** client 的 `tools/call`，又能**主动向** client 发请求——"子代理把审批委托给上层"。教学版的 `run_task` 直接执行 `shell`、不审批（对齐 s01 "无黑名单"基调），所以没有这条反向通道，但真实场景里"agent 调 agent 时审批该由谁来批"是个很现实的问题（[s14](../s14_guardian/) 的 Guardian 正是为无人值守而生的另一条答案）。

**多轮续聊（codex-reply）**——教学版每次 `codex` 调用都是**全新**任务、互不相关。真 server 还暴露第二个工具 **`codex-reply`**（`handle_call_tool` 的 `match` 里 `"codex-reply" => ...`）：第一次 `codex` 调用返回的 `structured_content.threadId`，调用方可以拿着它再发 `codex-reply { thread_id, prompt }`，**在同一个会话里接着聊**（`ThreadManager::get_thread`）。这把"一次性外包"升级成"有状态的多轮委托"。server 用 `running_requests_id_to_codex_uuid: Mutex<HashMap<RequestId, ThreadId>>` 把 MCP 请求 id 和 Codex thread id 对应起来。

| | 教学版 | 真 codex-rs |
|---|---|---|
| `codex` 调用 | 同步 `run_task` 返回 str | spawn thread + 流式转发所有 event |
| 用什么循环 | s01 简化循环 | 完整 core（沙箱/审批/SQ-EQ 全在） |
| 返回 | 一段 text | text + `structured_content{threadId}` |
| 续聊 | 无 | `codex-reply` + thread_id |
| 审批 | 直接执行 | 反向 elicitation 发回 client |

</details>

## 运行

```bash
python s15_mcp/code.py --demo   # 一口气演示两个方向：client 列工具/调用/回灌 + server 处理四个请求（mock，无需 key）
python s15_mcp/code.py          # 交互模式：默认走 client 方向（你的问题 → 模型 → MCP 工具）
```

交互模式默认演示 **client** 方向（最贴近"你日常用 Codex 接外部工具"）。想看 **server** 方向（Codex 被当成一次 `tools/call`），跑 `--demo` 即可——它会把 `initialize` / `tools/list` / `tools/call(shell)` / `tools/call(codex)` 四个请求接连喂给 `CodexMcpServer.handle()`，并打印每条 JSON-RPC 响应。

默认 `backend=mock`，离线可跑。想接真模型，在根目录 `.env` 里填 `OPENAI_API_KEY`（详见 [.env.example](../.env.example)）。

## 小结

- MCP 把"agent ↔ 工具"标准化成一套 JSON-RPC（`initialize` / `tools/list` / `tools/call`），把 N×M 的胶水代码塌缩成 N+M。
- **方向 A（client）**：连接管理器三件事——连接握手 → 聚合并命名空间化（`mcp__server__tool`）→ 路由调用。回合循环一字未改，MCP 工具和内置工具在循环眼里没区别。
- **方向 B（server）**：Codex 自己 `handle(request)`，招牌工具 `codex` 一次调用 = 跑**一整个 Codex 任务**（内部就是 s01 回合循环）；真版还有 `codex-reply` 多轮续聊、反向审批。
- 两个方向**同源**：同一套 JSON-RPC、同一个 s01 循环，只是谁问谁答互换。
- client 这一侧 Codex 和 Claude Code 几乎一样（开放标准的好处）；差异在 server——Codex 把整个 agent 暴露成别人的工具（agents-as-tools），这是"为无人值守、可被编排"下注的最直接体现。
- **生产级**：MCP server 在进程外——调用套 `tool_timeout`（hang 不卡死 agent）、并发连接且单个崩了只跳过、工具名命名空间化防撞名、外来工具照样过审批+沙箱（见「生产级」一节）。
- 下一站 [s16](../s16_config/)：一个开关切换一整套自主度——Config 与命名 profile。

## 思考

<div class="think">

1. 真 Codex 给撞名工具加 12 位 SHA-1 后缀、还有 64 字节长度上限。如果模型看到的工具名变成 `mcp__db__query_a1b2c3d4e5f6`，它还分得清这是干嘛的吗？命名空间的"可读性"和"唯一性"该怎么权衡？
2. 本章两侧都是进程内对象、零延迟、不会失败。一旦换成真 stdio 子进程：某个 server 启动要 30 秒、或中途崩了，回合循环该怎么办——阻塞等、跳过它、还是把"这个工具暂时不可用"也作为信息喂给模型？
3. 当 Codex 被当成 server、跑任务时要执行危险命令——可此刻没有人，只有上层 agent。审批该由**谁**来批：上层 agent 自己决定？转发给上层背后的人？还是干脆 `approval_policy: never` + 全靠 [s05](../s05_sandbox/) 的内核沙箱兜底？不同选择对应了什么样的信任假设？这跟全书主线"Codex 为低人工干预下注"是契合还是张力？
4. 现在 Codex 既能当 client 又能当 server。那么两个 Codex 互相调用、甚至 A 调 B、B 又调回 A，会发生什么？在这种"agent 拓扑"里，怎么防止无限递归、怎么追踪一次请求到底跑了多少层、花了多少 token？把 agent 变成"可被任意拼装的工具"，是解放了生产力，还是打开了一个新的复杂度黑洞？

</div>
