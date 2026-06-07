# 超长篇：Claude Code 的 Messages API 与 Codex 的 Responses API 全解

> 这是一篇深入长文，面向**只有些许基础**的读者。我们从「什么是线协议」讲起，把两套系统递给模型的**那张单子**逐格摊开——请求怎么拼、响应怎么拆、工具怎么编码、推理怎么携带、状态放在谁那、流式长什么样、失败怎么报——并解释**为什么**它们长得不一样。
>
> 事实依据：Codex 来自本仓库的真源码 `../../codex/codex-rs`，每个字段/常量都能查到文件行号；Claude Code（下称 CC）走的 **Anthropic Messages API** 是一套**公开、有文档**的协议，本文据其公开规范与 [learn-claude-code](../../learn-claude-code/) 的源码剖析撰写。
>
> ⚠️ **关于论据强度（与前两篇不同）**：本文 Codex 侧 = `codex-rs` 源码（强）；Anthropic **Messages API 的线格式本身** = 公开协议规范（强）；但 **CC 内部如何使用它**（重试常数、是否流式、循环如何分支）= 转述自对**闭源** CC 的剖析（弱，按二手对待）。文中凡涉及后者会标注。

[← 返回 learn-codex 总览](../README.md) · 姊妹篇：[上下文全解](context-cc-vs-codex.md) · [子代理与多智能体全解](subagent-multiagent-cc-vs-codex.md)

---

## 0. 给初学者：什么是「线协议」，为什么两家长得不一样

你的程序和大模型之间，隔着一根网线。模型跑在厂商的服务器上，你俩靠网络喊话。可网络只会传字节，它不认识「系统提示」「对话历史」「工具清单」「思考过程」这些概念——所以两头必须**事先约定一张单子**：哪个格子放身份设定、哪个格子放对话、哪个放工具、谁先谁后、回话又怎么填。这套约定，就叫**线协议（wire protocol）**。

打个比方：同样是点一杯咖啡，麦当劳的点单格式和星巴克不一样。你走进哪家，就得按哪家的单子填。市面上和 coding agent 相关的，主要有三张单子：

- **Anthropic Messages**（`POST /v1/messages`）—— Claude 用，CC 填的是这张；
- **OpenAI Chat Completions**（`POST /v1/chat/completions`）—— 老牌、通用，但 Codex **已经不用了**；
- **OpenAI Responses**（`POST /v1/responses`）—— OpenAI 较新的一张，Codex 填的是这张。

> 📌 一个容易过时的认知：很多人以为 Codex「也支持 Chat Completions 兜底」。**不再支持了。** `codex-rs` 的 `WireApi` 枚举如今**只剩 `Responses` 一个变体**，配置里写 `wire_api = "chat"` 会直接报错退出（[`model-provider-info/src/lib.rs`](../../codex/codex-rs/model-provider-info/src/lib.rs)，约 53–80 行，错误信息原文：*"`wire_api = "chat"` is no longer supported."*）。所以本文是一场干净的**两方**对比：Messages ⟷ Responses。

这三张单子要表达的东西大同小异（系统提示、对话、工具、推理、采样参数），但**字段名和摆法各不相同**。而且关键在于——**单子的样子是被模型塑造出来的，不是反过来**。这是贯穿全文的一句话，我们叫它「**协议跟随模型**」（[s09](../s09_responses_api/) 里讲过，[s03](../s03_apply_patch/) 里也现身过）：

- Claude 是「会说话、会用工具、能展开一段**带签名的明文思考**」的模型 → Messages 把思考做成消息里一个**可验证的明文块**；
- OpenAI 的 codex 系是「**推理模型**」，先想很久、再决定动手，思考过程**加密**、可调「想多深」 → Responses 把推理做成单子上一个**加密的、可回放的独立条目**，还给了一个「努力档位」旋钮。

看懂这两张单子，你就看懂了两套 agent 最底层的那根电话线。下面顺着「请求 → 对话形状 → 工具 → 推理 → 状态 → 流式 → 结束/失败 → 缺席字段 → 错误」逐段对比。

---

## 1. 一次请求的全景：把两张单子并排摊开

先看「发出去的」。同一个场景：用户说「列出文件」，给一个 `shell` 工具，开启思考。两张单子并排：

**Claude Code → Anthropic Messages**

```json
POST /v1/messages
{
  "model": "claude-opus-4-8",
  "max_tokens": 8000,
  "system": "You are Claude Code.",
  "thinking": { "type": "enabled", "budget_tokens": 4000 },
  "tools": [
    { "name": "shell", "description": "run a command",
      "input_schema": { "type": "object",
        "properties": { "command": { "type": "string" } },
        "required": ["command"] } }
  ],
  "messages": [
    { "role": "user", "content": "列出文件" }
  ],
  "stream": true
}
```

**Codex → OpenAI Responses**（这正是 [`codex-api/src/common.rs`](../../codex/codex-rs/codex-api/src/common.rs) 的 `ResponsesApiRequest` 序列化出来的样子，约 183–203 行）

```json
POST /v1/responses
{
  "model": "gpt-5-codex",
  "instructions": "You are Codex.",
  "input": [
    { "type": "message", "role": "user", "content": "列出文件" }
  ],
  "tools": [
    { "type": "function", "name": "shell", "description": "run a command",
      "strict": false,
      "parameters": { "type": "object",
        "properties": { "command": { "type": "string" } },
        "required": ["command"] } }
  ],
  "tool_choice": "auto",
  "parallel_tool_calls": true,
  "reasoning": { "effort": "medium", "summary": "auto" },
  "store": false,
  "include": ["reasoning.encrypted_content"],
  "stream": true,
  "prompt_cache_key": "<session-uuid>"
}
```

逐格对照顶层字段：

| 作用 | Anthropic Messages | OpenAI Responses（Codex） | 备注 |
|---|---|---|---|
| 模型 | `model` | `model` | 同 |
| 系统提示 | `system`（可为块数组、可带缓存标记） | `instructions`（字符串） | 名字不同；见 §5 缓存 |
| 对话历史 | `messages[]`（**嵌套块**） | `input[]`（**扁平 item**） | 形状差异，见 §2 |
| 工具 | `tools[].input_schema` | `tools[].parameters`（扁平 `type:"function"`） | 见 §3 |
| 工具选择 | `tool_choice:{type,name,…}` | `tool_choice:"auto"`（Codex 写死） | — |
| 并行工具 | `tool_choice.disable_parallel_tool_use` | `parallel_tool_calls: bool`（顶层正向开关） | 一个是「禁用」开关，一个是「启用」开关 |
| 推理 | `thinking:{type,budget_tokens}` | `reasoning:{effort,summary,context}` | **核心差异**，见 §4 |
| 输出上限 | `max_tokens`（**必填**） | —（**没有这个字段**） | 见 §8 |
| 采样温度 | `temperature`/`top_p`/`top_k`（可选） | —（**请求里根本不带**） | 见 §8 |
| 流式 | `stream: bool` | `stream: true`（Codex 写死） | — |
| 缓存 | `cache_control`（块级断点，≤4） | `prompt_cache_key` + 自动前缀缓存 | 两种哲学，见 §5 |
| 状态链 | —（无） | `store` + `previous_response_id`（可选） | **能力差异**，见 §5 |
| 元数据 | `metadata:{user_id}` | `client_metadata`（装 installation_id） | — |

两张单子要装的东西高度重合，但有三处一眼可见的分野，正是后面几节要细讲的：**对话的形状**（嵌套 vs 扁平）、**推理的编码**（明文预算 vs 加密档位）、以及**两个 Anthropic 有而 Codex 请求里没有的字段**（`max_tokens` / `temperature`）。

---

## 2. 对话的「形状」：嵌套内容块 vs 扁平 item 列表

这是两套协议最结构性的差异，也是前几章里「为什么 `messages` 长这样」的总答案。

### Anthropic：一条消息，内含一串「内容块」

CC 的历史是 `messages[]`，每条消息 `{role, content}`，而 `content` 是一个**块数组**。块有好几种 `type`：`text`、`tool_use`、`tool_result`、`thinking`、`image`。关键在于——**工具调用和工具结果是「块」，嵌在消息内部**：

```jsonc
// 助手这一轮：思考块 + 工具调用块，并列在 assistant 消息的 content 里
{ "role": "assistant", "content": [
    { "type": "thinking", "thinking": "用户想看文件…应调用 shell。",
      "signature": "EqoB…(密码学签名)" },
    { "type": "tool_use", "id": "toolu_abc", "name": "shell",
      "input": { "command": "ls -la" } }      // ← input 是真正的 JSON 对象
]}

// 工具结果：作为一个 tool_result 块，塞进一条新的 user 消息，用 tool_use_id 回指
{ "role": "user", "content": [
    { "type": "tool_result", "tool_use_id": "toolu_abc",
      "content": "file1\nfile2", "is_error": false }
]}
```

两个要点，都是生产级的坑：

1. **`tool_use.input` 是一个 JSON 对象**——你直接拿来用，不用再解析。
2. **工具结果是「user 消息里的一个块」**，靠 `tool_use_id` 和那次调用配对；而且它顶着 `user` 的角色（不是某种独立的 "tool" 角色）。

### OpenAI：一串扁平的 item，工具调用/结果各自独立

Codex 的历史是 `input[]`，一串**扁平、并排**的条目。源码里这是一个大枚举 `ResponseItem`（[`protocol/src/models.rs`](../../codex/codex-rs/protocol/src/models.rs)，约 753 行，`#[serde(tag = "type", rename_all = "snake_case")]`），变体很多，常见的几种：

| 变体 | 线上 `type` | 关键字段 |
|---|---|---|
| `Message` | `message` | `role`, `content` |
| `FunctionCall` | `function_call` | `name`, `arguments`（**JSON 字符串**）, `call_id` |
| `FunctionCallOutput` | `function_call_output` | `call_id`, `output` |
| `Reasoning` | `reasoning` | `summary`, `encrypted_content` |
| `CustomToolCall` | `custom_tool_call` | `call_id`, `name`, `input`（自由文本） |
| `LocalShellCall` / `WebSearchCall` / `ImageGenerationCall` | 同名 snake_case | 各自的动作字段 |
| `Compaction` / `ContextCompaction` | `compaction` / `context_compaction` | `encrypted_content`（压缩摘要也是一等 item） |

同样一来一回，长这样：

```jsonc
// 模型这一轮的 output：推理 item 和工具调用 item，平级并排
{ "type": "reasoning", "id": "rs_…",
  "summary": [ { "type": "summary_text", "text": "用户想看文件，应调用 shell。" } ],
  "encrypted_content": "gAAAAAB…(不透明密文)" }
{ "type": "function_call", "call_id": "call_abc", "name": "shell",
  "arguments": "{\"command\": \"ls -la\"}" }     // ← arguments 是字符串，要再 json.loads

// 工具结果：一个顶层 item，用 call_id 回指（不嵌在任何消息里）
{ "type": "function_call_output", "call_id": "call_abc",
  "output": "file1\nfile2" }
```

### 一句话差异，与它的连锁后果

| | Anthropic Messages | OpenAI Responses |
|---|---|---|
| 历史结构 | 消息 → **内容块数组**（二维） | **扁平 item 列表**（一维） |
| 工具调用位置 | `tool_use` 块，嵌在 assistant 消息里 | `function_call`，独立 item |
| 工具结果位置 | `tool_result` 块，嵌在**新 user 消息**里 | `function_call_output`，独立 item |
| 配对键 | `tool_use_id` | `call_id` |
| 工具入参类型 | **JSON 对象**（直接用） | **JSON 字符串**（要再解析一次） |

这个形状差异不是审美，它有连锁后果：

- **流式穿插**：扁平 item 让 Responses 能把「推理 → 工具调用 → 又一个工具调用」当成一串独立条目逐个推流，前端一出现就捕获（见 §6）。嵌套块则在「一条消息内部」流式（`content_block` 索引）。
- **压缩**：嵌套结构让 CC 能在「消息」和「块」两个粒度上动手（既裁整条消息，也单独替换某个 `tool_result` 块的内容）；扁平结构让 Codex 更多是「在 item 序列上从头裁剪」。这条线在[上下文全解](context-cc-vs-codex.md) §3–§5 里展开过。
- **解析陷阱**：`arguments` 是字符串这件事，是接 Responses 时最常见的 bug 来源——忘了 `json.loads` 就把一整个 `"{\"command\":…}"` 当命令喂了下去。

---

## 3. 工具的编码：`input_schema` vs `parameters`，以及一个 Anthropic 没有的东西——文法工具

工具是 agent 的手。两边都用 **JSON Schema** 描述工具入参，但**包装**不同，而且 Responses 多出一类 Anthropic 体系里没有对应物的工具。

### 同：都靠 JSON Schema

```jsonc
// Anthropic：工具是顶层对象，schema 放在 input_schema
{ "name": "shell", "description": "run a command",
  "input_schema": { "type":"object",
    "properties": { "command": {"type":"string"} }, "required": ["command"] } }

// OpenAI Responses：扁平 type:"function"，schema 放在 parameters，多一个 strict
{ "type":"function", "name":"shell", "description":"run a command", "strict": false,
  "parameters": { "type":"object",
    "properties": { "command": {"type":"string"} }, "required": ["command"] } }
```

（注意 Responses 的工具是**扁平**的——`type:"function"` 和 `name` 同级；而**老的 Chat Completions** 会把它再嵌进一层 `"function": {…}`。这也是 Codex 选 Responses 后少掉的一层包装。）

Codex 侧的工具类型是一个枚举 `ToolSpec`（[`tools/src/tool_spec.rs`](../../codex/codex-rs/tools/src/tool_spec.rs)，约 15–51 行），`#[serde(tag="type")]`，变体有：`function` / `namespace` / `tool_search` / `image_generation` / `web_search` / `custom`。函数工具本体 `ResponsesApiTool`（[`tools/src/responses_api.rs`](../../codex/codex-rs/tools/src/responses_api.rs)，约 25–38 行）带 `name / description / strict / defer_loading / parameters / output_schema`；其中 `parameters` 是一个受限的 `JsonSchema`（[`tools/src/json_schema.rs`](../../codex/codex-rs/tools/src/json_schema.rs)，约 38–68 行），只支持 OpenAI Structured Outputs 那个子集（`type/enum/anyOf/$ref/$defs/properties/required/additionalProperties/items` + 一个 Responses 专属的 `encrypted` 标记）。

### 异一：`strict` 模式

Responses 的函数工具有一个 `strict: bool`。开启时，OpenAI 保证模型吐出的 `arguments` **严格符合** schema（Structured Outputs：所有字段必填、不许多余字段）。这是把「防住 LLM 乱填参数」下沉到了**服务端约束**。有意思的是——Codex 当前默认 `strict: false`（[`tools/src/responses_api.rs`](../../codex/codex-rs/tools/src/responses_api.rs) 约 131 行），它选择在**客户端**用 `parse_arguments` + 出错回灌（`RespondToModel`）来兜 LLM 的错（这正是 [s02](../s02_tool_use/) 「生产级」那节讲的 dispatch 层）。Anthropic 这边没有 `strict` 这个旋钮——它靠模型本身对 `input_schema` 的高遵从度 + 客户端校验。

> 取舍：`strict` 把「保证合法」交给服务端（省心，但 schema 受限、且每次 schema 变更要服务端重新编译约束）；客户端校验更灵活、能给模型更友好的纠错信息，但要自己写那层防线。两条路 Codex 都留着，默认走后者。

### 异二：`defer_loading` / `tool_search`——工具太多时按需加载

`ResponsesApiTool` 上那个 `defer_loading: Option<bool>`，加上 `ToolSpec::ToolSearch` 变体，是 Codex 应对「工具/MCP 太多、塞爆上下文」的机制：超过阈值的工具先**不展开**，标记为延迟加载，模型需要时通过 `tool_search` 调出来。这与 CC 的 **ToolSearch 元工具**是同一个问题的两种解法——[s02](../s02_tool_use/) 的「深入五」专门对比过（CC：一个会调用的元工具；Codex：协议层的 `defer_loading` + `ToolSearchCall`/`Output` item）。

### 异三（最有意思）：**文法约束的自由文本工具**——`apply_patch`

这是 Anthropic 体系里**没有直接对应物**的一招。Codex 的 `apply_patch` 不是函数工具、不是 shell，而是一个 `custom`（freeform）工具，入参不是 JSON，而是一段**被 LARK 文法约束的自由文本**（[`core/src/tools/handlers/apply_patch_spec.rs`](../../codex/codex-rs/core/src/tools/handlers/apply_patch_spec.rs)，约 9–27 行）：

```jsonc
{ "type": "custom", "name": "apply_patch",
  "description": "Use the `apply_patch` tool to edit files. This is a FREEFORM tool, so do not wrap the patch in JSON.",
  "format": { "type": "grammar", "syntax": "lark",
              "definition": "start: begin_patch hunk+ end_patch …" } }
```

模型回来的不是 `{"old":…,"new":…}`，而是直接吐出补丁文本，且**被文法卡着**不能跑偏：

```text
*** Begin Patch
*** Update File: a.py
@@
-old line
+new line
*** End Patch
```

对照 Anthropic 这边——它也有编辑文件的「内置工具类型」（如 text editor / `str_replace_based_edit_tool`、bash、computer use），但**入参始终是 JSON 命令**：

```jsonc
{ "type":"tool_use", "name":"str_replace_based_edit_tool",
  "input": { "command":"str_replace", "path":"a.py", "old_str":"…", "new_str":"…" } }
```

**为什么 Codex 走文法自由文本？** 因为它的模型在「写 diff」这件事上，输出**整片补丁文本**比输出**结构化 JSON 字段**更自然、更不容易出错——于是协议提供 `grammar` 这一档，让「工具的形状去贴合模型最擅长的输出方式」。这正是 [s03](../s03_apply_patch/) 反复讲的「**工具形状跟随模型**」最锋利的一次体现：Anthropic 选「结构化命令 + 模型严格填 JSON」，OpenAI 选「自由文本 + 文法兜底」，背后是两家对「自家模型怎样输出补丁最稳」的不同判断。

---

## 4. 推理 / 思考：签名明文块 vs 加密不透明 item

两家的旗舰模型都会「先想再做」，但**怎么把这段思考放进协议**，差到了哲学层面。这是 [s01](../s01_agent_loop/) 「深入四」的展开版。

### Anthropic：思考是消息里一个**带密码学签名的明文块**

- **开关与预算**：`thinking: { "type": "enabled", "budget_tokens": 4000 }`——给一个**token 预算数字**，让模型「最多想这么多」。
- **回来的形状**：`thinking` 块，里面是**明文**思考 + 一个 `signature`（密码学签名）。你能**读到**模型想了什么。
- **签名干嘛用**：保证完整性。下一轮你把 `thinking` 块**原样带回**（交错思考 / interleaved thinking 时尤其必要——模型要看见自己上轮的思路），服务端用 `signature` 验证这段思考没被篡改。
- **被过滤时**：若思考内容触发安全过滤，会变成 `redacted_thinking`（加密块），你照样原样带回、但读不了。

### OpenAI（Codex）：推理是一个**加密、不透明的独立 item** + 一个「努力档位」旋钮

- **开关与档位**：`reasoning: { effort, summary, context }`（[`codex-api/src/common.rs`](../../codex/codex-rs/codex-api/src/common.rs) 约 125–132 行）。`effort` 不是 token 数，而是一个**枚举档位**：`none / minimal / low / medium(默认) / high / xhigh`（[`protocol/src/openai_models.rs`](../../codex/codex-rs/protocol/src/openai_models.rs) 约 40 行）——「想多深」是拧旋钮，不是给数字。
- **回来的形状**：`reasoning` item，里面通常是**摘要**（`summary`，给人看的版本，可选 `auto/concise/detailed/none`，[`protocol/src/config_types.rs`](../../codex/codex-rs/protocol/src/config_types.rs) 约 47 行）+ 一段 **`encrypted_content`（不透明密文）**。原始思维链你**读不到**。
- **怎么跨轮携带**：请求里加 `include: ["reasoning.encrypted_content"]`（[`core/src/client.rs`](../../codex/codex-rs/core/src/client.rs) 约 768–769 行），服务端就把加密推理回传；下一轮你把这些 `reasoning` item 原样塞回 `input`，模型便「记得自己上次想到哪」——但你全程**只是个搬运工，读不了内容**。

### 并排看，差在三个维度

| | Anthropic Messages | OpenAI Responses |
|---|---|---|
| 力度控制 | `budget_tokens`（**数字预算**） | `effort` 档位（**枚举旋钮**） |
| 可读性 | **明文**，你能读 | **密文**，你读不了（只给摘要） |
| 完整性 vs 机密性 | **签名**保完整（防篡改） | **加密**保机密（藏思维链） |
| 在协议里的位置 | 消息里的一个**块**（和文本/工具并列） | 一个**独立 item**（和消息/工具并列） |
| 跨轮携带 | 原样回传 `thinking` 块 | `include` 拉回 + 原样回传 `reasoning` item |
| 占上下文预算 | 是 | 是（且要算进压缩，[s07](../s07_context_compaction/)） |

**为什么不同？** 两家对「该不该让你看见模型的思维链」给了相反的答案：Anthropic 倾向**透明 + 可验证**（明文 + 签名），OpenAI 倾向**保护 + 不透明**（加密 + 只给摘要）。这不只是产品口味，也牵动隐私、可调试性、和「思维链会不会被逆向利用」的安全考量。但有一点完全一致：**两家都把「想了什么」从日志旁注，升格成了协议里要被传输、存储、回放的一等数据**——这是「推理模型时代」线协议的共同进化。

---

## 5. 状态：无状态 re-send vs「可」有状态的服务端会话

这一节要**精确化**一个常见的简化说法。你可能在[上下文全解](context-cc-vs-codex.md)里读到「Messages 无状态、Responses 服务端有状态」——大方向对，但真相更细。

### Anthropic Messages：**结构性无状态**

Messages API **不在服务端保存任何对话状态**。没有 `previous_response_id` 这种东西，没有「会话 id 续接」。每一轮，客户端都把**完整的 `messages[]` 重新发一遍**。状态 100% 在客户端——这是 CC 把上下文管理、压缩、记忆全做成**客户端**精细流水线的根本原因（它别无选择）。

### OpenAI Responses：**有状态是「能力」，但 Codex 默认没用它**

Responses **支持**服务端状态：`store: true` 时，服务端记住这次响应，下轮你只发 `previous_response_id` + **增量** item，省去重发整段历史。但是——

- **Codex 对 OpenAI 端点默认 `store: false`**（[`core/src/client.rs`](../../codex/codex-rs/core/src/client.rs) 约 799 行：`store: provider.is_azure_responses_endpoint()`——只有 **Azure** 端点才 `store:true`）。也就是说，**面向 OpenAI，Codex 同样每轮重发完整 `input`**，和 CC 一样无状态。
- `previous_response_id` 这个字段，**只存在于 WebSocket 请求结构** `ResponseCreateWsRequest`（[`codex-api/src/common.rs`](../../codex/codex-rs/codex-api/src/common.rs) 约 228–253 行），且仅在 `store:true` + 有上轮响应时才填、只发增量。HTTP 路径根本不带它。

所以更精确的图景是：

| | Anthropic Messages | OpenAI Responses（Codex 实际用法） |
|---|---|---|
| 协议是否**支持**服务端状态 | 否 | **是**（`store`+`previous_response_id`） |
| Codex/CC **默认**怎么用 | 无状态，重发全量 | **也**无状态重发全量（OpenAI 端点 `store:false`）；仅 Azure/WS 走增量 |
| 跨轮带的「额外行李」 | 全量 messages | 全量 input **+ 加密 reasoning**（§4） |
| 服务端能否帮你压缩 | 基本不能 | **能**（Codex 有 `compact_remote` 远程压缩，靠的就是 Responses 的服务端能力） |

换句话说：**Responses 把「服务端有状态」做成了一个随时可启用的能力，Codex 平时不开（保持无状态重发），但需要时（Azure 增量、远程压缩、粘性路由）能随手用上**。这份「可选的服务端能力」，正是 Codex 能做 rollout 续接、远程压缩、`x-codex-turn-state` 粘性路由的地基（[上下文全解](context-cc-vs-codex.md) §5、§7）。Messages 则把这条路彻底关死，逼着 CC 在客户端把一切做到极致。

### 缓存：你标断点 vs 给个 key + 自动前缀

「无状态重发」最怕浪费——每轮重发的开头一大段（system + 工具 + 早期历史）几乎没变，凭什么每次重新计费？两家都靠**缓存**省这笔钱，但控制方式相反：

- **Anthropic：你显式标断点。** 在 `system` / `tools` / 某个内容块上挂 `cache_control: {type:"ephemeral"}`，最多 4 个断点，告诉服务端「到这里为止的前缀，缓存起来」。命中后 `usage` 里有 `cache_read_input_tokens`（便宜）和 `cache_creation_input_tokens`（首次写入）。TTL 默认 5 分钟，有 1 小时的扩展档。**控制权在你手里、按断点计。**
- **OpenAI Responses：给个 key，前缀自动缓存。** Codex 传 `prompt_cache_key`（一个会话 id），服务端**自动**对相同前缀做缓存命中；配合 `x-codex-turn-state` 粘性路由，让同一会话尽量落到同一台后端、命中它的缓存（[s09](../s09_responses_api/) 深入三）。`response.completed` 的 `token_usage` 里有 `cached_input` 一项。**控制权更多在服务端、按前缀自动。**

这也呼应了压缩哲学（[上下文全解](context-cc-vs-codex.md) §5）：Codex 压缩时**刻意从历史中段裁、保住前缀**，正是为了别打碎这份自动前缀缓存。

---

## 6. 流式：块导向事件 vs item / 语义导向事件

两家都用 **SSE（Server-Sent Events）** 把响应**一块块**推过来，但事件的**组织单位**不同。

### Anthropic：围绕「内容块」的事件流

一条响应的事件序列（块导向，每个块带 `index`）：

```text
message_start                                   ← 消息开始（含初始 usage）
  content_block_start   (index 0, thinking)     ← 第 0 块：思考块开张
    content_block_delta (thinking_delta …)      ← 思考文字一段段来
    content_block_delta (signature_delta …)     ← 最后补上签名
  content_block_stop    (index 0)
  content_block_start   (index 1, tool_use)     ← 第 1 块：工具调用壳
    content_block_delta (input_json_delta …)    ← 工具入参的 JSON 一片片来（要累积再解析）
  content_block_stop    (index 1)
message_delta  (stop_reason, usage)             ← 收尾：带 stop_reason + 最终 usage
message_stop
```

子事件 `delta` 有好几种：`text_delta`（正文）、`input_json_delta`（工具入参的部分 JSON）、`thinking_delta`（思考）、`signature_delta`（签名）。「细粒度工具流式」会让 `input_json_delta` 不缓冲地直接吐。

### OpenAI Responses：围绕「item / 语义事件」的事件流

Codex 实际匹配的事件（[`codex-api/src/sse/responses.rs`](../../codex/codex-rs/codex-api/src/sse/responses.rs) 约 276 行的 `process_responses_event`）：

```text
response.created                          ← 响应开始
response.output_item.added                ← 新增一个 item（如一个 function_call 的壳）
  response.output_text.delta              ← 助手正文一段段来
  response.function_call_arguments.delta  ← 函数入参一段段来（公开 API 名）
  response.custom_tool_call_input.delta   ← 自由文本工具的输入一段段来（apply_patch 走这条）
  response.reasoning_summary_text.delta   ← 推理摘要一段段来
  response.reasoning_text.delta           ← 推理正文一段段来
response.output_item.done                 ← 某个 item 收尾
response.completed                        ← 整轮完成：带 token_usage + end_turn
response.failed / response.incomplete     ← 失败 / 不完整（见 §7）
```

`response.completed` 上，Codex 读出 `token_usage`（`input / output / cached_input / reasoning_output / total`）和一个 `end_turn` 标志（[`core/src/client.rs`](../../codex/codex-rs/core/src/client.rs) 约 1851 行）。

### 差异

| | Anthropic | OpenAI Responses |
|---|---|---|
| 事件单位 | **内容块**（`content_block_*`，带 index） | **item / 语义事件**（`response.*`） |
| 工具入参流式 | `input_json_delta`（累积 JSON） | `function_call_arguments.delta` / 自由文本工具 `custom_tool_call_input.delta` |
| 思考/推理流式 | `thinking_delta` + `signature_delta` | `reasoning_summary_text.delta` / `reasoning_text.delta` |
| 收尾信号 | `message_delta` 带 `stop_reason`+`usage` | `response.completed` 带 `token_usage`+`end_turn` |
| 失败信号 | HTTP 状态码 + `error` 事件 | **流内** `response.failed` 事件（见 §7） |

一句话：Anthropic 的流是「**一条消息内部，块接块**」；Responses 的流是「**一串顶层 item / 语义节点**」。这与 §2 的历史形状是同构的——流式只是把那个结构**按时间拆开推**。

---

## 7. 结束与失败信号：`stop_reason` vs `response.status` / 事件

### 怎么知道「这一轮该停了」

- **Anthropic：看 `stop_reason`。** 取值：`end_turn`（说完了）、`tool_use`（要调工具，循环继续）、`max_tokens`（顶到输出上限被截断）、`stop_sequence`（撞到停止词）、`pause_turn`（长跑的服务端工具，需再发以续跑）、`refusal`（拒答）。
  - ⚠️ 二手细节：learn-claude-code 剖析称 **CC 实际不靠 `stop_reason` 决定是否继续循环，而是检查 content 里有没有 `tool_use` 块**——因为流式响应里 `stop_reason` 可能不可靠。这条按二手对待。
- **OpenAI Responses：看响应状态 / 事件。** `response.completed`（带 `end_turn` 标志和 `token_usage`）/ `response.incomplete`（如顶到长度）/ `response.failed`（出错）。Codex 据 `end_turn` 和有没有待执行的 `function_call` 决定是否再发一轮（[s01](../s01_agent_loop/) 的循环 + [s10](../s10_sq_eq_protocol/) 的 SQ/EQ）。

### 失败长什么样——一个值得玩味的设计分野

- **Anthropic**：失败基本走 **HTTP 状态码**（400 invalid_request、413 请求过大、429 rate_limit、500 api_error、529 overloaded_error），流内偶有 `error` 事件。
- **OpenAI Responses**：很多失败是**流内的 `response.failed` 事件**，带细分错误码（[`codex-api/src/sse/responses.rs`](../../codex/codex-rs/codex-api/src/sse/responses.rs) 约 325 行）：`context_length_exceeded → ContextWindowExceeded`、`insufficient_quota → QuotaExceeded`、`server_is_overloaded`/`slow_down → ServerOverloaded`、`invalid_prompt`、`cyber_policy`、以及带 `Retry-After` 的 `Retryable`。

> 差异的意味：Anthropic 把失败更多留在「HTTP 层」（连接级），Responses 把失败也做成「**流内的一等事件**」——因为它的一轮可能已经流了很多 item（推理、工具）才失败，用流内事件报错，能携带「失败前已产出什么」的上下文。

### Token 用量字段也不同

| | Anthropic `usage` | OpenAI `token_usage` |
|---|---|---|
| 输入 | `input_tokens` | `input` |
| 输出 | `output_tokens` | `output` |
| 缓存命中 | `cache_read_input_tokens` | `cached_input` |
| 缓存写入 | `cache_creation_input_tokens` | —（前缀自动） |
| **推理专项** | —（含在 output 里） | **`reasoning_output`**（单列） |
| 合计 | （自行相加） | `total` |

OpenAI 把 **`reasoning_output` 单独列出来**，是「推理模型」的特征账单——你能看清这轮花了多少 token 在「想」上。这也回扣 §4：推理是要计量、要进压缩预算的一等成本。

---

## 8. 两个「缺席」的字段：`max_tokens` 与 `temperature` 说明了什么

最能体现「协议跟随模型」的，往往是**缺席**的字段。对照 §1 的两张单子：

| | Anthropic Messages | OpenAI Responses（Codex 请求） |
|---|---|---|
| `max_tokens` | **必填**（硬性输出上限） | **不带这个字段** |
| `temperature` / `top_p` / `top_k` | 可选（调随机性） | **请求里根本没有**（`ResponsesApiRequest` 无此字段） |
| 「想多深」的旋钮 | 无（靠 `budget_tokens` 限思考长度） | `reasoning.effort` 档位 |

我核对过 `ResponsesApiRequest` 的全部字段（[`codex-api/src/common.rs`](../../codex/codex-rs/codex-api/src/common.rs) 约 183–203 行）：**没有 `max_tokens`，也没有 `temperature`**。这不是遗漏，而是模型形态的直接投影：

- **推理模型自己管输出长度**——它会想多久、写多长，由 `effort` 这个高层旋钮间接调，而不是给一个生硬的 token 上限去截断。
- **推理模型不暴露 `temperature`**——采样温度对「先做长链推理再作答」的模型意义不同，OpenAI 干脆不在 Responses 请求里收它。

反过来，Anthropic 把 `max_tokens` 设成**必填**、并保留 `temperature`，是因为 Claude 的接口更贴近「经典补全」的控制面：你明确给出输出预算和随机性。一个把控制权交给「档位 + 模型自治」，一个交给「显式数字参数」——同一件事（控制生成），两种世界观。

---

## 9. 错误模型与重试（简表，细节见 s09）

| | Anthropic Messages | OpenAI Responses（Codex） |
|---|---|---|
| 失败载体 | HTTP 状态码 + 偶发 `error` 事件 | **流内 `response.failed` 事件** + 状态码 |
| 限流 | 429 `rate_limit_error` | `server_is_overloaded`/`slow_down`，带 `Retry-After` |
| 过载 | 529 `overloaded_error` | `ServerOverloaded` |
| 上下文超限 | 400/413（prompt 过长）→ 反应式压缩 | `context_length_exceeded` → 反应式压缩 |
| 重试策略（二手/源码） | ⚠️ CC：退避+抖动、最多 10 次、连续 3 次 529 换备用模型 | 源码：[`responses_retry.rs`](../../codex/codex-rs/core/src/responses_retry.rs) 退避+抖动、honor `Retry-After`、WebSocket→HTTPS **传输回退**、粘性路由保证安全重试 |

共同的铁律（[s09](../s09_responses_api/) 「生产级」一节讲透）：**可重试错误**走指数退避 + 抖动（避免重试风暴）、听服务端的 `Retry-After`、封顶重试数；**致命错误**（鉴权）零重试；**上下文超限**是「先压缩再重试」而非单纯重发——「恢复 ≠ 重试」。

---

## 10. 总对比表

| 维度 | Claude Code（Anthropic Messages） | Codex（OpenAI Responses） |
|---|---|---|
| 端点 | `POST /v1/messages` | `POST /v1/responses` |
| 系统提示 | `system`（可块数组、可缓存） | `instructions`（字符串） |
| 对话历史 | `messages[]`，**内容块**嵌套 | `input[]`，**扁平 item** |
| 工具调用/结果 | `tool_use`/`tool_result` **块**（嵌在消息里） | `function_call`/`function_call_output` **独立 item** |
| 配对键 | `tool_use_id` | `call_id` |
| 工具入参类型 | **JSON 对象** | **JSON 字符串**（要再解析） |
| 工具 schema | `input_schema` | `parameters` + `strict` 旗标 |
| 文法/自由文本工具 | 无（编辑工具也是 JSON 命令） | **有**（`custom`+LARK，`apply_patch`） |
| 多工具按需加载 | ToolSearch 元工具 | `defer_loading` + `tool_search` item |
| 推理力度 | `budget_tokens`（数字） | `reasoning.effort`（档位枚举） |
| 推理可读性 | **明文 + 签名**（可读、防篡改） | **加密**（不可读、只给摘要） |
| 推理在协议中 | 消息里的 `thinking` 块 | 独立 `reasoning` item（`include` 拉回） |
| 服务端状态 | **无**（结构性无状态） | **可有**（`store`+`previous_response_id`）；Codex 对 OpenAI 默认 `store:false` |
| 缓存控制 | `cache_control` 显式断点（≤4） | `prompt_cache_key` + 自动前缀 + 粘性路由 |
| 输出上限 | `max_tokens` **必填** | **不带**（模型自治） |
| 采样温度 | `temperature` 等可选 | **请求里没有** |
| 流式单位 | 内容块事件（`content_block_*`） | item/语义事件（`response.*`） |
| 失败信号 | HTTP 状态 + `error` 事件 | **流内 `response.failed`** + 状态 |
| Token 账单 | `usage{input,output,cache_*}` | `token_usage{input,output,cached_input,reasoning_output,total}` |
| Chat Completions 兜底 | 不适用 | **已移除**（`WireApi` 仅 `Responses`） |

---

## 11. 为什么不同？把账算到底

把上面所有差异收束成一句话：

> **两张单子要装的东西一样（系统提示、对话、工具、推理、用量），但每一格的形状，都是被「自家模型怎么工作」和「状态该放谁那」这两件事塑造出来的。**

根因三层：

1. **模型塑造协议（最深的一层）。** OpenAI 的推理模型 → `reasoning.effort` 档位、加密 `encrypted_content`、不要 `temperature`/`max_tokens`、用 LARK 文法让模型自由吐补丁。Claude → `thinking` 明文块 + 密码学签名、必填 `max_tokens`、工具入参严格 JSON。**不是谁抄谁，是各自的模型长什么样，单子就长什么样。** 这就是 [s03](../s03_apply_patch/)/[s09](../s09_responses_api/) 反复说的「工具与协议跟随模型」。

2. **状态放谁那，决定了能力边界。** Messages 结构性无状态 → CC 只能、也因此把上下文/压缩/记忆做成**极精细的客户端流水线**。Responses 把「服务端状态」做成**可选能力** → Codex 平时无状态重发（和 CC 一样省心），但需要时能解锁远程压缩、增量请求、粘性路由、rollout 续接。一个把路关死、逼出客户端的极致；一个留着门、换来云端/可恢复的弹性（[上下文全解](context-cc-vs-codex.md)、[子代理全解](subagent-multiagent-cc-vs-codex.md) 讲的就是这扇门后的世界）。

3. **缓存哲学，是无状态重发的副产品。** 两家都要在「每轮重发」里省钱：Anthropic 让你**显式标断点**（控制权在你），OpenAI 给你**key + 自动前缀**（控制权在服务端）。于是 Codex 压缩时要「保前缀」，CC 压缩时要「管好自己的断点」。

没有谁更高明——这是两套面向不同模型、不同部署形态的、各自自洽的工程答案。**看懂了「账是怎么算的」，你在自己的 harness 里接任何一家 API，都知道每个字段为什么在那、缺了会怎样、该把状态和缓存放在哪。** 这就是从「会调 API」到「懂线协议」的那一步。

---

## 12. 思考

<div class="think">

1. Anthropic 让你**读得到**思考（明文 + 签名），OpenAI 让你**读不到**（加密 + 摘要）。站在「调试一个出错的 agent」的角度，哪种更有用？站在「保护模型思维链不被逆向」的角度呢？如果是你设计，会怎么权衡？
2. `apply_patch` 用 LARK 文法约束自由文本，而不是 JSON 字段。文法约束能保证「补丁格式合法」，但能保证「补丁逻辑正确」吗？这和 §3 的 `strict` 模式（保证 JSON 合法）是同一种「合法 ≠ 正确」的局限吗？
3. Responses 把失败做成**流内事件**（`response.failed`），Messages 更多靠 **HTTP 状态码**。当一轮已经流了 5 个工具调用才失败，这两种报错方式，哪种让 harness 更好恢复？为什么？
4. Codex 对 OpenAI 默认 `store:false`（无状态重发），明明 Responses 支持服务端状态却不用。它图什么？（提示：想想 ZDR/数据驻留、可移植性、和「服务端替你改了对话你却看不见」的可审计性——回到[上下文全解](context-cc-vs-codex.md) §11。）
5. 两家都没把对方的招数学过来（Anthropic 没加 `effort` 档位，OpenAI 没在请求里收 `temperature`）。如果有一天某家的模型变了形态（比如 Claude 也走纯推理、或 codex 系也开放明文思维链），你预期它们的**单子**会先变哪一格？

</div>

---

[← 返回 learn-codex 总览](../README.md) · 相关章节：[s01 Agent Loop](../s01_agent_loop/)（reasoning vs thinking）· [s02 工具](../s02_tool_use/)（schema/ToolSearch）· [s03 apply_patch](../s03_apply_patch/)（工具形状跟随模型）· [s09 Responses API](../s09_responses_api/) · [s10 SQ/EQ](../s10_sq_eq_protocol/) · 姊妹篇：[上下文全解](context-cc-vs-codex.md) · [子代理与多智能体全解](subagent-multiagent-cc-vs-codex.md)
