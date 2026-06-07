# s02: Tool Use — 加一个工具，循环一行都不用改

> 🌐 [English](README.en.md) · **中文**

> *"加工具不是改循环，而是注册一个 handler + 一份 schema。"*

[learn-codex 总览](../README.md) · [回合循环](../s01_agent_loop/) → **工具与分发** → [apply_patch](../s03_apply_patch/)

---

## 先把思想说透：为什么加一个工具，循环却一行都不用动

s01 想通了一件事：agent 的内核就是一个循环，**模型出主意、循环跑腿**。那么很自然的下一个问题是——agent 要会的事情越来越多（读文件、写文件、列目录、改代码、搜网页……），这些"会的事情"是加在哪里的？是不是每加一样，那个循环就得改一次、越变越臃肿？

答案出乎意料：**循环永远不动。** 想通这一点，靠下面两个递进的道理。

**道理一：模型并不"拥有"任何能力，它只会"喊名字"。**
回到 s01 的画面：模型像个只能写纸条的顾问。它写下"我要读 `config.py` 这个文件"——但它读不了，它只是**喊出了一个意图**。真正能去读文件的，是我们这边的一小段 Python 代码。换句话说，模型那一侧永远只有一件事：**说出"我想用哪个工具、给什么参数"**；至于这个工具到底能不能用、怎么实现的，全在我们手里。模型喊的是名字，干活的是我们。

**道理二：既然模型只是"喊名字"，那"认领名字的人"用一张表就够了。**
模型喊 `read_file`，谁来接？最笨也最对的办法，就是一本**电话簿**：名字 → 对应的人（函数）。模型喊 `read_file`，循环翻到 `read_file` 这一页，找到 `run_read_file` 这个函数，照它给的参数拨过去。喊 `write_file` 就翻到 `write_file` 那页。这本电话簿，就是**分发映射**（一个 `name → handler` 的字典）。

于是关键的洞察来了：**循环要做的事，从头到尾只有"查电话簿、照着拨"这一个动作——它根本不在乎簿子里有 1 个名字还是 100 个名字。** 加一项新能力，等于往电话簿里**多抄一行**（再把这个新名字告诉模型，让它知道有这号人可喊）。循环那句"查簿子→拨号"压根不用碰。这就是为什么 s01 那十几行 `run_turn`，到本章一个字都不会改。

打个比方：循环像公司前台，只会"按访客报的部门名转接电话"。今天公司新开了个"法务部"，前台要改工作方式吗？不用——只要在转接表上加一行"法务部 → 305 房间"就行。前台的动作永远是同一个。**模型是访客，工具是各个部门，循环是那个永远只会转接的前台。** 全课程几十种能力，都是往这张转接表上一行行加出来的；前台（循环）始终是 s01 那一个。

## 问题

s01 的 agent 只有一个工具：`shell`。它已经"图灵完备"了——读文件 `cat`、写文件 `echo > file`、列目录 `ls`，理论上 shell 全能干。

但全用 shell 有两个现实问题：

1. **又丑又脆**。"读这个文件的前 20 行"要拼 `sed -n '1,20p'`，跨平台还不一定对；写多行文件要跟 here-doc、转义、引号搏斗。
2. **不可观测**。前端（TUI / IDE）拿到的只是一坨 `stdout`，它分不清这次模型是在"读文件"还是在"删库"——没有结构化的语义。

所以哪怕主力是 shell，我们也想给模型几个**第一类工具**：`read_file`、`write_file`、`list_dir`。问题来了——**每加一个工具，回合循环要不要跟着改？**

## 解决方案

不用。这一章的全部重点就一句话：

> **循环不动。加一个工具 = 往「分发映射」加一行 handler + 往工具清单加一份 schema。**

s01 的循环里本来就有一句 `HANDLERS.get(tc.name)`——它早就是按名字查表分发的。我们要做的只是把那张表填厚。

```
   模型回合产出 tool_call(name, arguments)
                  │
                  ▼
        TOOL_HANDLERS.get(name)   ← 唯一的"扩展点"：一张 name→handler 的字典
          ┌───────┼───────┬────────────┐
          ▼       ▼       ▼            ▼
        shell  read_file write_file  list_dir   ← 加工具就在这里加一行
          │       │       │            │
          └───────┴───────┴────────────┘
                  │
          function_call_output 回灌 → 继续循环（run_turn 一字未改）
```

## 工作原理

看 [code.py](code.py)。`# FROM s01（搬运）` 横幅下是**原封不动**的 `run_shell` 和 `run_turn`；`# NEW in s02` 横幅下才是新增的东西。

**第 1 步** — 写新工具的实现。每个都套一层 `safe_path` 把路径锚定在工作区内：

```python
def safe_path(p: str) -> Path:
    path = (WORKDIR / p).resolve()
    if not path.is_relative_to(WORKDIR):
        raise ValueError(f"path escapes workspace: {p}")
    return path
```

**第 2 步** — 把工具登记进**分发映射**和**工具清单**（schema 是扁平的 Responses API 形状）：

```python
TOOL_HANDLERS = {"shell": run_shell, "read_file": run_read_file,
                 "write_file": run_write_file, "list_dir": run_list_dir}
TOOLS = [{"name": "read_file", "description": "...",
          "parameters": {"type": "object", "properties": {...}, "required": ["path"]}}, ...]
```

**第 3 步** — 循环照旧。`run_turn` 里查表分发的那一行，和 s01 逐字相同：

```python
handler = TOOL_HANDLERS.get(tc.name)
output = handler(**tc.arguments) if handler else f"unknown tool: {tc.name}"
messages.append(tool_output_item(tc.call_id, output))
```

这正是真 Codex 的结构。core 里有一个 [`ToolRegistry`](../../codex/codex-rs/core/src/tools/registry.rs)（`HashMap<ToolName, Arc<dyn CoreToolRuntime>>`），它的 `dispatch_any` 就是按名字查表、找到对应 handler、调用 `handle()`——和我们的 `TOOL_HANDLERS.get(name)` 一脉相承，只是外面包了一圈 hook / 遥测 / 沙箱标签。而 [`create_tools_json_for_responses_api`](../../codex/codex-rs/tools/src/tool_spec.rs)（`tools/src/tool_spec.rs:78`）负责把每个工具序列化成发给模型的扁平 JSON——对应我们 `create_tools_json` 那一行。

**走一遍** — 把"加工具循环不变"这句话坐实。假设模型这回合想写文件，它喊出来的纸条长这样（和 s01 那张 `shell` 纸条**形状完全一样**，只是 `name` 换了、`arguments` 里的字段换了）：

```json
{"type": "function_call", "call_id": "call_7", "name": "write_file",
 "arguments": "{\"path\": \"hello.txt\", \"content\": \"hi\\n\"}"}
```

循环拿到它，做的还是 s01 那一个动作——查表、照着拨：

1. **查电话簿**：`TOOL_HANDLERS.get("write_file")` → 取到 `run_write_file` 函数。（在 s01 里这张表只有 `shell` 一项；现在它有四项——但 `.get(name)` 这句代码一字未变。）
2. **照参数拨号**：`run_write_file(path="hello.txt", content="hi\n")`，真的把文件写到工作区，返回 `"wrote 3 bytes to hello.txt"`。
3. **结果按 `call_id` 贴回去**，包成和 s01 一模一样的 `function_call_output` 项：
   ```json
   {"type": "function_call_output", "call_id": "call_7", "output": "wrote 3 bytes to hello.txt"}
   ```
4. 带着更长的历史再问模型，继续循环。

注意第 1~3 步：**这正是 s01 `run_turn` 里那三行**，逐字相同（去 [code.py](code.py) 里对一下 `# FROM s01（搬运）` 横幅下那段）。我们这一章新增的所有东西——`run_write_file` 的实现、`TOOL_HANDLERS` 里多出来的几行、`TOOLS` 里多出来的几份 schema——**全都在循环之外**。循环只是多了几个可以查到的名字而已。这就是上面"道理二"的活样板。

`--demo` 把这件事单独拎出来给你看：它不碰模型，直接调 `TOOL_HANDLERS[name](**kwargs)`——和 `run_turn` 里第 2 步那次"拨号"是**同一个动作**——在 `_demo_workspace/` 下 write→read→list，再演示一次越界写入被 `safe_path` 拦截，最后清理目录。你会在输出里看到那行 `> write_file {'path': ...}`，正对应上面那张纸条。

## 生产级：schema 怎么编码、harness 怎么守住它、LLM 出错怎么办

循环和分发通了——但一个**能上生产**的工具系统，难点根本不在"调对工具"，而在"模型调**错**时怎么办"。模型会漏填参数、填错类型、甚至喊一个根本不存在的工具名。玩具会当场崩；生产级 harness 必须把这些全接住。这一节把三件事讲到能经得起检验。

### 一、schema 怎么编码：它不是注释，是**唯一的护栏 + 提示词**

工具的 `parameters` 是一份 **JSON Schema**。它干的两件事都远超"文档"：

- **对模型**：字段名和 `description` 是模型决定"怎么调"的**唯一依据**——它本身就是提示词工程。`{"type":"integer"}`、`{"enum":[...]}`、`required` 把模型能填的东西收窄；写得含糊，模型就乱填。
- **对 harness**：schema 是你**校验**模型输出的标尺（见第三节）。

真 Codex 不手写 JSON 字符串拼 schema，而是用一个**有类型的 `JsonSchema` 结构**（[`tools/src/json_schema.rs:38`](../../codex/codex-rs/tools/src/json_schema.rs)）：

```rust
pub struct JsonSchema {
    pub schema_type: Option<JsonSchemaType>,       // "type"
    pub description: Option<String>,
    pub enum_values: Option<Vec<JsonValue>>,        // "enum"
    pub items: Option<Box<JsonSchema>>,             // 数组元素
    pub properties: Option<BTreeMap<String, JsonSchema>>,
    pub required: Option<Vec<String>>,
    pub additional_properties: Option<AdditionalProperties>,
    pub any_of: Option<Vec<JsonSchema>>,            // 联合类型 / 可空
    // ...
}
```

每个工具是一个 `ResponsesApiTool { name, description, strict: bool, parameters: JsonSchema }`（[`responses_api.rs:26`](../../codex/codex-rs/tools/src/responses_api.rs)），再由 `ToolSpec` 这个 `#[serde(tag="type")]` 枚举序列化成 `{"type":"function", ...}` 发给模型。**关键：schema 是代码里的类型，不是散落的字符串**——这正是下面"不漂移"的地基。

### 二、strict 模式：让模型**根本吐不出**非法参数

`ResponsesApiTool` 有个 `strict: bool`。设成 `true`，就是 OpenAI 的 **strict function calling**：API 用**受约束解码**保证模型吐出的参数一定符合 schema（缺必填、错类型在**生成阶段**就被挡掉，根本到不了你手上）。代价写在源码注释里（`responses_api.rs:29`）：

> *当 strict=true 时，JSON schema 的 `required` 与 `additional_properties` 必须齐全；`properties` 里**每个**字段都必须出现在 `required` 里。*

也就是 strict 不许"可选字段"（要可选只能写成 `anyOf:[T, null]`）。Codex 这些内置工具目前默认 `strict: false`，把灵活性留下、改用**运行时校验**兜底（第三节）——这是个真实的工程权衡：**strict = 生成期就挡住，但 schema 必须写死、无可选字段；不 strict = schema 灵活，但你必须自己在 harness 里验。**

> 还有一层**防御性规整**：OpenAI 模型要求 schema 必须带 `properties`，可有些 MCP 服务器偏不给。Codex 在 [`mcp_tool.rs`](../../codex/codex-rs/tools/src/mcp_tool.rs) 里一发现缺了就**塞一个空 `{}` 进去**——接第三方工具时，你不能假设对方的 schema 是干净的。

### 三、防 LLM 出错：不是"别让它错"，而是"错了能回灌、能改"

这是整节的题眼，也是玩具与生产的分水岭。看真 Codex 怎么解析一次工具调用的参数（[`handlers/mod.rs:72`](../../codex/codex-rs/core/src/tools/handlers/mod.rs)）：

```rust
fn parse_arguments<T: for<'de> Deserialize<'de>>(arguments: &str)
    -> Result<T, FunctionCallError> {
    serde_json::from_str(arguments).map_err(|err| {
        FunctionCallError::RespondToModel(format!("failed to parse function arguments: {err}"))
    })
}
```

两个生产级要点：

1. **反序列化进一个有类型的结构体 `T`**：schema 发给模型、`T` 用来接收——**两者同源**，所以"schema 说有字段 X、代码却读字段 Y"这种漂移不会发生。这就是"harness 怎么维护 schema"的答案：**别让 schema 和解析各写一份，让它们出自同一个类型。**
2. **失败不 panic，而是 `RespondToModel(错误信息)`**：错误会作为这次工具调用的**结果回灌给模型**，模型下一轮看到"参数错在哪"就能自己改。

而错误只分两类（[`function_call_error.rs`](../../codex/codex-rs/tools/src/function_call_error.rs)）——这个二分法就是生产级错误处理的全部哲学：

| 变体 | 含义 | 怎么处理 |
|---|---|---|
| `RespondToModel(String)` | **可恢复**：模型自己能改（参数错、工具名错、命令失败…） | 把错误当工具结果回灌，循环继续，模型重试 |
| `Fatal(String)` | **不可恢复**：harness 自己坏了（崩溃、不变量被破坏） | 中止整个回合 |

> 一句话：**生产级 harness 假设模型一定会出错，于是把"出错"做成一条普通的、可恢复的回灌路径，而不是一个异常。** 模型是唯一能修正自己错误的人——所以把错误还给它。

本章 [code.py](code.py) 把这套搬成了一个 `dispatch_tool` 层（夹在循环和工具之间，正是真 Codex `ToolRouter` 的位置）：**未知工具 → 错误串；参数不合 schema（缺必填 / 多字段 / 类型错）→ 错误串；handler 抛异常 → 错误串**——全都回灌、绝不崩进程。注意"循环"本身职责没变（拿结果→回灌），校验与纠错都在 dispatch 这一层。`--demo` 末尾专门喂四种错误调用，给你看它怎么接住（以下是**真实输出**）：

```
> read_file {}
  ERROR: invalid arguments for `read_file`: missing required field(s) ['path']
> write_file {'path': 'x.txt', 'content': 123}
  ERROR: invalid arguments for `write_file`: field `content` should be string, got int
> read_file {'path': 'a.txt', 'lines': 5}
  ERROR: invalid arguments for `read_file`: unexpected field `lines` (allowed: ['limit', 'path'])
> search_web {'q': 'codex harness'}
  ERROR: unknown tool `search_web` (available: ['list_dir', 'read_file', 'shell', 'write_file'])
```

每一条都是"会回灌给模型、让它下一轮改对"的反馈，而不是一个 traceback。**这一步，就是把 s02 从「能调通」抬到「生产级」的那道坎。**

## 🆚 与 Claude Code 的不同

| | Claude Code | Codex | 为什么 |
|---|---|---|---|
| 第一类工具数量 | **多**：Read / Write / Edit / Glob / Grep / Bash / … | **少**：shell + apply_patch 几乎包办一切 | Codex 赌 shell+apply_patch 的**通用性**；Claude 赌**好用、可观测的专用工具** |
| 工具 schema 形状 | `{name, description, input_schema}` | `{type:"function", name, description, parameters}`（扁平） | 跟各家 wire API 走：Anthropic Messages vs OpenAI Responses |
| 越界写防护 | 工具代码里的路径校验（应用层） | 教学版同样用 `safe_path`，但真身**主要靠内核沙箱**（[s05](../s05_sandbox/)）| 自主运行场景下，应用层护栏不够，要内核兜底 |
| 加工具的代价 | 注册 handler + schema，循环不改 | 注册 handler + schema，循环不改 | **两边相同**——这就是本章的真正主角 |
| 工具太多装不下 | **ToolSearch**：一个"找工具的工具"——海量工具先"延迟"、只留名字，模型按需搜出 schema 再用 | **延迟暴露 + `ToolSearchCall`/`ToolSearchOutput` item**：MCP 工具超阈值（100）就不直接进 prompt，改由模型搜索发现 | 同一问题（几百个工具撑爆上下文）、同一思路（按需发现）；Codex 把它做成**协议 item**、并绑在 MCP 规模上 → 见深入五 |

**为什么 Codex 工具这么少？** 因为**模型差异直接决定工具差异**。Codex 的训练把宝押在两件武器上：一个能跑任意命令的 `shell`，一个能精确改文件的 `apply_patch`（[s03](../s03_apply_patch/)）。模型被教会用这两样东西"自己想办法"——要搜代码就 `grep`，要看文件就 `sed`/`cat`。Claude Code 则反过来，给模型一柜子做工精细的专用工具（每个都有清晰 schema、结构化输出、可被 UI 渲染），让模型少拼命令、多调接口。没有谁对谁错——这是"通用 shell 的灵活"对"专用工具的可控、可观测"的不同下注。

> 一句话：**工具的多寡与形状，是两家"模型能力假设 + wire 协议"共同投下的影子。**

## 深入：教学版 vs 真 Codex 源码

<details>
<summary>一、TOOL_HANDLERS 字典 vs 真 ToolRegistry</summary>

教学版的"分发映射"就是一个 `dict[str, callable]`，`run_turn` 里一句 `TOOL_HANDLERS.get(tc.name)` 完成分发。真 Codex 的 [`registry.rs`](../../codex/codex-rs/core/src/tools/registry.rs) 把同一件事做成了类型化的运行时契约：

```rust
pub struct ToolRegistry {
    tools: HashMap<ToolName, Arc<dyn CoreToolRuntime>>,
}
// dispatch_any_with_terminal_outcome(...) ：按名字取出 tool，再 tool.handle(invocation)
```

`self.tool(&tool_name)` 取不到就回 `unsupported call: <name>`——和我们 `else f"unknown tool"` 同义。差别全在它额外做的事：取不到工具时记一条遥测、跑 PreToolUse/PostToolUse hook（[s13](../s13_hooks/)）、给结果打沙箱/策略标签、把"工具开始/结束"通过 lifecycle 事件广播出去。

| | 教学版 | 真 codex-rs |
|---|---|---|
| 表 | `dict[str, fn]` | `HashMap<ToolName, Arc<dyn CoreToolRuntime>>` |
| 分发 | `TOOL_HANDLERS.get(name)` | `ToolRegistry::dispatch_any` |
| handler 形态 | 普通函数 | 实现 `ToolExecutor`/`CoreToolRuntime` trait 的结构体 |
| 取不到时 | `"unknown tool: …"` | `FunctionCallError::RespondToModel("unsupported call …")` |

核心是一样的：**一张按名字查的表**。生产版多出来的几百行，全是 hook、遥测、沙箱、并行调度这些"保护与可观测"机制。

</details>

<details>
<summary>二、扁平 schema 与 create_tools_json_for_responses_api</summary>

教学版的 `create_tools_json` 就一行：给每个工具加个 `{"type":"function", ...}`。真源码 `tools/src/tool_spec.rs:78` 的 `create_tools_json_for_responses_api` 做的是同一件事——把一组 `ToolSpec` 逐个 `serde_json::to_value` 序列化：

```rust
pub fn create_tools_json_for_responses_api(tools: &[ToolSpec])
    -> Result<Vec<Value>, serde_json::Error> {
    tools.iter().map(serde_json::to_value).collect()
}
```

关键在 `ToolSpec` 这个枚举用 `#[serde(tag = "type")]` 标了五个变体：`Function` / `Namespace` / `ToolSearch` / `ImageGeneration` / `WebSearch` / `Freeform(custom)`。也就是说"工具"不止"函数"一种——`web_search`、`image_generation` 是**宿主托管的特殊 type**（[s12](../s12_tools_extra/) 会碰到 `web_search`）。我们的 read/write/list 都属于最普通的 `Function` 变体，序列化出来正是 `{"type":"function","name":...,"parameters":...}`。

</details>

<details>
<summary>三、真 shell 工具远不止「一个 command 字符串」</summary>

我们的 `run_shell(command: str)` 收一个字符串、`subprocess.run(shell=True)`。真 Codex 的 shell（[`handlers/shell.rs`](../../codex/codex-rs/core/src/tools/handlers/shell.rs)）收的是结构化得多的参数：argv 数组（不是拼字符串）、`timeout_ms`、`cwd`、网络策略、`sandbox_permissions`/`additional_permissions`，还要先经过 `create_exec_approval_requirement_for_command` 决定要不要弹审批，再交给 `ShellRuntime` + `ToolOrchestrator` 在沙箱里跑，前后各发一个 `ToolEmitter::begin/finish` 事件。

它甚至会先 `intercept_apply_patch`——如果发现这条 shell 命令其实是个 apply_patch，就改走补丁通道。换句话说真身的 shell 是"带审批、带沙箱、带事件、还能识别 apply_patch"的复合工具。教学版把这些全剥掉，只留"跑命令拿输出"的内核，让你看清分发本身。

</details>

<details>
<summary>四、safe_path 应用层护栏 vs 内核沙箱</summary>

我们给 read/write/list 套了 `safe_path`：把路径 resolve 后检查 `is_relative_to(WORKDIR)`，越界抛错。这是**应用层**的护栏——和 learn-claude-code 同款。它够教学，但有个根本弱点：**它只拦住"经过我这个函数"的路径**。模型完全可以让 `shell` 跑一句 `python -c "open('/etc/passwd')"`，绕开 `safe_path`。

真 Codex 不把安全寄托在工具代码的路径检查上，而是下沉到内核：macOS Seatbelt 的 `(deny default)` + 可写根白名单，Linux 的 Landlock+seccomp（[s05](../s05_sandbox/)）。哪怕模型用任意子进程去写工作区外，内核直接拒绝。这正是全课程主线的第一次显形：**Claude Code 在应用层"挡"，Codex 在内核层"关"。** 本章的 `safe_path` 是"挡"的味道；它真正的归宿在 s05。

</details>

<details>
<summary>五、工具一多就装不下：ToolSearch（CC）vs 延迟暴露（Codex）</summary>

到 [s15](../s15_mcp/) 你会接上 MCP——一个 MCP 服务器可能甩给你**几百个工具**。要是把它们的 schema 全塞进每次请求，上下文窗口光是工具定义就被吃光了（活还没干，地方先没了）。两家都用同一个思路解决：**别全摊开，让模型按需"找工具"。**

**Claude Code：`ToolSearch` 是一个"找工具的工具"。** 海量工具被标成**延迟（deferred）**——一开始只把**名字**给模型，schema 先不加载。模型想用时，先调 `ToolSearch`（传一个查询词），harness 返回匹配工具的完整 schema，模型这才"看得见"并调用它。一句话：**把"加载工具定义"本身变成一次工具调用。**

**Codex：同一个思路，做成协议里的"延迟暴露 + ToolSearch item"。** 看 [`core/src/mcp_tool_exposure.rs`](../../codex/codex-rs/core/src/mcp_tool_exposure.rs)：

```rust
pub(crate) const DIRECT_MCP_TOOL_EXPOSURE_THRESHOLD: usize = 100;

let should_defer = search_tool_enabled
    && (config.features.enabled(Feature::ToolSearchAlwaysDeferMcpTools)
        || deferred_tools.len() >= DIRECT_MCP_TOOL_EXPOSURE_THRESHOLD);
```

逻辑很直白：**MCP 工具不到 100 个就直接暴露（`direct_tools`）；一旦 ≥ 100（或特性开关强制），就改成"延迟暴露"（`deferred_tools`）——不进 prompt，等模型来搜。** 而"模型搜了一次工具"在 Codex 里是**第一类协议 item**：`ResponseItem::ToolSearchCall` / `ToolSearchOutput`（`core/src/turn_timing.rs`）——和 `function_call`、`reasoning` 一样摊在那张扁平 item 列表里。还记得深入二里 `ToolSpec` 枚举那六个变体吗？其中就有一个 `ToolSearch`——它本就是 Codex 工具体系里的一等公民。

**异同：**
- **同**：都为治"工具太多撑爆上下文"，都用"延迟 + 按需发现"，都把"找工具"做成模型的一次调用。
- **异**：CC 的 `ToolSearch` 是一个**通用、显眼的元工具**（任何延迟工具都走它）；Codex 把它**绑在 MCP-at-scale**（阈值 100 + 特性开关），并按一贯气质做成**协议 item**（`ToolSearchCall`/`ToolSearchOutput`，于是天然进 rollout、可审计）——又一次"CC 放应用层、Codex 放进协议"。

> 呼应本章主线：循环依然不动。"工具太多"从来不是循环的问题，而是**怎么把工具清单交给模型**的问题——无非在模型喊名字之前，先让它去"查一次号台"。循环那句"查电话簿→拨号"照旧。

</details>

## 运行

```bash
python s02_tool_use/code.py --demo   # 离线：走分发映射跑 write/read/list + 演示越界拦截
python s02_tool_use/code.py          # 交互模式（mock 后端，无需 key）
```

`--demo` 会在当前目录建临时 `_demo_workspace/`，跑完自动删除，不留痕迹。

## 小结

- 加一个工具 = 注册一个 handler（进分发映射）+ 一份工具 schema。**回合循环一行都不用改。**
- 真 Codex 用 `ToolRegistry` + `dispatch_any` 做同一件事，外面再裹 hook / 遥测 / 沙箱 / 事件。
- Codex 第一类工具**少**（shell + apply_patch 包打天下），Claude Code **多**——这是两家"模型能力假设 + wire 协议"共同的影子。
- 教学版的 `safe_path` 是应用层护栏；它真正的归宿是内核沙箱（[s05](../s05_sandbox/)）。
- 工具一多就装不下：两家都"延迟 + 按需找工具"——CC 用 `ToolSearch` 元工具，Codex 用延迟暴露（阈值 100）+ `ToolSearchCall` item（见深入五）。
- **生产级**：难点不在"调对工具"，而在"调错了能恢复"。schema 既是给模型的提示词、又是 harness 的校验标尺（且要与解析目标**同源、不漂移**）；出错走 `RespondToModel` 回灌让模型自己改，而非崩进程——校验/兜错都在循环与工具之间的 dispatch 层（见「生产级」一节）。
- 下一站 [s03](../s03_apply_patch/)：Codex 改文件的招牌工具 `apply_patch`——为什么不用 `write_file` 整文件覆盖？

## 思考

- 既然 `shell` 理论上能 `cat`/`echo >`/`ls`，为什么还要单独给模型 `read_file`/`write_file`/`list_dir`？这几个专用工具到底买到了什么——是给模型省事，还是给**前端**省事？
- Codex 只给 shell + apply_patch 两件武器，靠模型"自己想办法"；Claude Code 给一柜子专用工具。如果让你训练一个新模型，你会押"少而通用"还是"多而专用"？这个选择会怎样反过来约束你的 harness？
- 本章的 `safe_path` 拦得住 `write_file("../x")`，却拦不住 `shell("python -c \"open('/etc/passwd','w')\"")`。一个只在应用层做路径检查的 agent，安全边界其实在哪？这是不是正好解释了 Codex 为什么要把防线下沉到内核（[s05](../s05_sandbox/)）？
- 真 Codex 的 `dispatch_any` 在调用 handler 前后塞了 PreToolUse/PostToolUse hook 和 Begin/End 事件。如果让你只用教学版这张 `dict` 去加"每次工具调用前问一句要不要批准"，你会把这段逻辑加在分发映射里，还是加在循环里？为什么？
- 当一个 MCP 服务器甩给你 500 个工具，"先搜索再调用"省了上下文，却多走一步、还可能搜不全。你会怎么权衡"全摊开"和"按需找"？那个 100 的阈值，调高调低各有什么代价、该由谁来定？
- `dispatch_tool` 把"参数错 / 工具名错 / handler 崩"都回灌给模型让它重试。可如果模型**反复**用同样的错参数、陷进死循环呢？什么时候该把 `RespondToModel`（可恢复）升级成 `Fatal`（中止）？真 Codex 靠回合预算 + 熔断挡住无限重试——想想 s14 那个 `MAX_CONSECUTIVE_GUARDIAN_DENIALS_PER_TURN`，同一个道理该怎么搬到工具重试上？
