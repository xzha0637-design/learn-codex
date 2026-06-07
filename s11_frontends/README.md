# s11: Frontends — 前端只是事件流的消费者（TUI + exec）

> 🌐 [English](README.en.md) · **中文**

> *"一个 core 产出一条事件流；前端不含业务，只是这条流的消费者。终端、CI、IDE 各取所需。"*

[learn-codex 总览](../README.md) · [s10 SQ/EQ 协议](../s10_sq_eq_protocol/) → **s11 前端：TUI + exec** → [s12 更多工具](../s12_tools_extra/)

---

## 先把思想说透：为什么换一张「脸」不用动「大脑」

到这里你已经知道，agent 的「大脑」就是那个回合循环（[s01](../s01_agent_loop/)）：调模型、跑工具、回灌、再调。但同一个大脑，可能要同时服务三种完全不同的用户——终端前盯着屏幕的人、CI 里没人看的脚本、IDE 后端的编辑器。怎么做到「一个大脑、很多张脸」，而且加一张新脸时大脑一行都不用改？想通下面三个道理，这一章就通了。

**道理一：「做什么」和「怎么显示」是两件事，最容易犯的错就是把它们焊死。**
最朴素的写法是：模型一回话就 `print`，命令一跑完就 `print`——把显示直接写进循环里。可一旦这么写，麻烦立刻接踵而来：想把朴素文本升级成带框线的漂亮界面，得去改循环里**每一处** `print`；想让 CI 用脚本解析输出，那些颜色和框线又会把脚本噎死。根子在于：**「发生了什么」是大脑的事，「长什么样」是脸的事，把两者缠在一起，就谁也动不了谁。**

**道理二：让大脑只「报告」、不「打印」，它就和所有的脸解耦了。**
巧办法是：core 跑一个回合时不直接打印，而是一件件地**报告发生了什么**——「回合开始了」「我要跑这条命令」「命令跑完了，退出码 0」「我说完了」。每一件事就是一个**事件**（一个带 `type` 字段的小字典），按顺序排起来就是一条**事件流**。这就像后厨不停往外喊话——「开火了」「这道菜好了」——后厨只管喊，它**根本不关心**前厅怎么摆盘。喊话（事件流）成了大脑和脸之间唯一的接口，从此两边各自独立演化。

**道理三（最关键）：脸不过是「听这条流」的消费者——所以脸可以有很多张，加一张不碰大脑。**
既然 core 只往外喊事件，那任何一张「脸」要做的就只剩一件事：**听这条流、决定怎么显示**。终端里那张脸把每个事件画成带色框线（这段「把一个事件变成屏幕上几行」的代码就是**渲染器**，换渲染器 = 换显示风格，大脑不动）；CI 里那张脸把每个事件原样吐成一行 JSON（每行一个独立 JSON 对象的 **JSONL** 格式，脚本读一行解析一行，对自动化极其友好），再用**退出码**告诉 `&&` 和流水线这趟到底成没成。后者就是所谓 **headless（无头）**——没有人坐在终端前、没人回 y/N，全靠机器可读的输出和退出码说话（这正是 `codex exec`）。终端、CI、IDE 后端，本质上都只是「听同一条流」的消费者，区别只在听完往哪儿画。

把这三点连起来：**core 喊话，脸听话。** 这一章就给你看两种听法——一种画给人看（TUI 渲染器），一种吐给机器读（headless exec）——而它们读的是**同一条**事件流，core 一行没改。这正是上一章 [s10](../s10_sq_eq_protocol/) 把输入输出拆成两条队列那条「架构脊梁」结出的果。

## 问题

到 [s10](../s10_sq_eq_protocol/) 为止，Codex 已经把一个回合做成了「事件向外流（EQ）、操作向内流（SQ）」。但前面跑起来时，我们总是默认有个人坐在终端前看输出。现实里 Codex 要同时服务两种完全不同的「用户」：

- **人**，坐在终端前，想要一个漂亮、带颜色、会滚动的界面（TUI）；
- **机器**，CI / 管道 / 云端，没有 TTY、没人点审批，只想要**能被程序解析的输出**和**一个表示成败的退出码**。

最容易写错的版本是：把显示逻辑直接焊进回合循环——模型一回话就 `print`，命令一跑完就 `print`。这样写，几个问题立刻爆炸：

- **换皮难**：想从「纯文本」升级到「带框线的 ratatui 界面」，得去改循环里**每一处** `print`；
- **多前端不可能**：TUI、`codex exec`、app-server（IDE 后端）想复用同一套回合逻辑？做不到——逻辑和显示缠死了；
- **机器读不了**：CI 想 `| jq` 解析输出？花花绿绿的颜色和框线会把脚本噎死；
- **测试难**：要验证「命令失败时 UI 显示红叉」，得把整个模型循环跑起来。

Codex 的答案：**让 core 只产出与前端无关的 typed 事件流；前端只是这条流的消费者。** 谁爱怎么显示怎么显示，core 不管。

## 解决方案

把架构掰成「一条流、多个消费者」：

```
            run_turn_events(prompt)   ← core（搬自 s01 的回合循环，去掉所有 print）
                     │
                     │  yield 一条 typed 事件流：
                     │    thread.started → turn.started →
                     │    item.started/completed（命令、消息）→ turn.completed|failed
                     ▼
        ┌────────────────────────────┬────────────────────────────┐
        │  消费者 A：TUI 渲染器         │  消费者 B：headless exec      │
        │  （有人坐在终端前）            │  （CI / 管道 / 云端，无头）     │
        │                            │                            │
        │  WIDGETS 派发表             │  EventProcessor             │
        │   事件类型→渲染函数           │   ├ HumanProcessor 人类文本    │
        │   ├ BoxRenderer  盒子框线    │   └ JsonlProcessor --json     │
        │   └ PlainRenderer 朴素一行   │      每行一个 JSON             │
        │                            │                            │
        │  换渲染器不碰 core            │  跑完据 error_seen 返回退出码    │
        └────────────────────────────┴────────────────────────────┘
```

注意左右两边读的是**同一条事件流**。换渲染器（左边 `--plain`）、换处理器（右边 `--json`）都只改最末端那一层，**core 一行不动**。这就是「一个 core，多个前端」在最小尺度上的样子——真 codex 里还有第三个消费者 app-server（IDE/cloud 的 WebSocket 后端），同样只是「消费这条流」。

## 工作原理

看 [code.py](code.py)，分三块：core、TUI 消费者、exec 消费者。

**① core：把回合跑成事件流。** `run_turn_events` 就是 s01 的 `run_turn`，唯一改动是**把所有 `print` 换成 `yield 事件`**。事件分类对齐真 codex 的 `exec_events.rs`：每个「item」（命令、消息）有 `started` / `completed` 两拍。

```python
def run_turn_events(prompt):
    yield {"type": "thread.started", "thread_id": "thr_demo"}
    yield {"type": "turn.started"}
    while True:
        resp = model.respond(messages, tools=TOOLS, system=SYSTEM)
        ...
        yield {"type": "item.started",
               "item": {"item_type": "command_execution", "command": cmd, "status": "in_progress"}}
        output, code = run_shell(cmd)
        yield {"type": "item.completed",
               "item": {"item_type": "command_execution", "command": cmd,
                        "aggregated_output": output, "exit_code": code,
                        "status": "completed" if code == 0 else "failed"}}
    yield {"type": "turn.completed", ...}   # 或 turn.failed
```

**② 消费者 A：TUI 渲染器（widgets as functions）。** 每种事件对应一个纯函数 `(event) -> list[str]`（要打印的行）。这是真 codex `history_cell/` 里那些 widget（`ExecCell`/`MessageCell`/`PatchCell`…）的极简化身。一张 `WIDGETS` 派发表把事件类型映射到渲染函数；想支持新事件？**加一行**。再套一个可整体替换的「渲染器」：

```python
WIDGETS = {"thread.started": w_thread_started, "turn.started": w_turn_started,
           "item.started": _w_item_started, "item.completed": _w_item_completed, ...}

class BoxRenderer:    # 查 WIDGETS 表，画带色框线
    def render(self, event):
        for ln in WIDGETS.get(event["type"], ...)(event): print(ln)

class PlainRenderer:  # 无视 widget 表，每个事件压成一行朴素文本
    def render(self, event): print(f"[{event['type']}] ...")
```

**③ 消费者 B：headless exec（人类文本 / JSONL）。** 对应 `exec/src` 里 `EventProcessor` trait 的两个实现。`JsonlProcessor` 把每个事件 `print(json.dumps(...))`（一行一个 JSON）；`HumanProcessor` 挑关键事件上色给人看。每个 `process(event)` 返回「是否致命错误」，最后汇总成退出码——对应真 `lib.rs` 末尾的 `if error_seen { std::process::exit(1); }`：

```python
def run_exec(prompt, json_mode):
    processor = JsonlProcessor() if json_mode else HumanProcessor()
    error_seen = False
    for event in run_turn_events(prompt):      # 同一条 core 流，换个消费者
        if processor.process(event):
            error_seen = True
    return 1 if error_seen else 0              # 退出码：给 CI 的信号
```

两个驱动器 `drive_tui`（`for event: renderer.render(event)`）和 `run_exec`（`for event: processor.process(event)`）形状**几乎一模一样**——这种对称不是巧合，正是「core 与前端解耦」想达到的效果。

> **关键：stdout 只放真正的输出。** `--json` 时 stdout 必须是**纯 JSONL**，否则 `| jq` 会被噪声噎死。codexlib 的启动横幅默认打 stdout，本章用 `contextlib.redirect_stdout(sys.stderr)` 把它改道——真 codex exec 同样把配置摘要/告警写 stderr、只把结果写 stdout。

### 走一遍

我们跟着 `--demo` 走一遍：prompt 是 `执行 \`echo hello from codex\` 并报告结果`。demo 先把整条流收集成一个列表 `stream = list(run_turn_events(...))`，**强调这是同一批事件**，然后分别喂给两个消费者。

**第 1 步**：core 跑回合，产出事件。mock 后端看到「执行 …」就发一次 shell 调用，于是流里依次出现（每个就是一个字典）：

```
{"type": "thread.started", "thread_id": "thr_demo"}
{"type": "turn.started"}
{"type": "item.started",   "item": {"item_type": "command_execution", "command": "echo hello from codex", "status": "in_progress"}}
{"type": "item.completed", "item": {"item_type": "command_execution", "command": "echo hello from codex", "aggregated_output": "hello from codex", "exit_code": 0, "status": "completed"}}
{"type": "item.completed", "item": {"item_type": "agent_message", "text": "..."}}
{"type": "turn.completed", "usage": {...}}
```

为什么是「两拍」（started + completed）？因为命令**开始**和**结束**是两个时刻——TUI 想在开始时先显示「(running…)」、结束时再补上「✓ 退出码 0」；JSONL 想让下游脚本能区分「在跑」和「跑完了」。同一条流满足两种需求。

**第 2 步**：消费者 A（`BoxRenderer`）拿到 `item.started` 那条，查 `WIDGETS["item.started"]`，渲染成一行带色的 `$ exec echo hello from codex (running…)`；拿到 `item.completed` 渲染成 `✓ ok` + 输出。它**只看事件，不知道命令是怎么跑的**。

**第 3 步**：消费者 B（`JsonlProcessor`）拿到**完全相同**的那几条事件，每条 `print(json.dumps(event))`——原样吐成上面那 6 行 JSONL。`turn.completed` 不是 `turn.failed`，所以 `error_seen=False`，退出码 0。

**为什么这一步是重点**：第 2、3 步读的是同一个 `stream` 列表。它们的输出形态天差地别（终端界面 vs JSONL），但 `run_turn_events` 一行都没改。这就是「前端只是事件流的消费者」——你想加第三种前端（比如网页 dashboard），只要再写一个「听这条流」的消费者，core 依旧不动。

## 生产级：前端要扛住"流是断续的、会回退、会出错"

"core 产事件、前端只消费"很干净，但真实的事件流是**断续**地来的：一个 `function_call` 先到一个空壳、参数再一点点 delta 进来；正文 `output_text` 一段段拼；中途还可能来一个错误或中断。一个能上生产的前端得处理这些：

- **增量渲染**：不能等一条消息完整才显示——要边收 delta 边刷新（流式打字机效果），否则用户对着一个"假死"的屏幕干等。
- **乱序/回退的兜底**：`MessagePhase`（commentary vs final，[s09](../s09_responses_api/)）会变、工具调用会被审批打断、回合会被 `Interrupt` 腰斩——前端要能把"半条消息"干净地收尾或撤回，而不是留一截烂尾 UI。
- **无头前端的对等**：`codex exec` 把同一条事件流序列化成 **JSONL**（每个事件一行）给 CI/管道消费。它和 TUI 看的是**同一条流**——所以"显示逻辑"必须完全从"产生逻辑"里剥离，否则无头模式就会和交互模式行为不一致。

> 一句话：前端的生产级，是承认"流是断续、会变、会断"的，然后让每一种中途状态都有一个不难看的收场。core 只管忠实产事件；把这些都收拾干净，是前端这一层的活。

## 🆚 与 Claude Code 的不同

| | Claude Code | Codex |
|---|---|---|
| 前端与 core | UI 与 agent 循环耦合较紧 | **事件流解耦**：core 产出 `EventMsg`，前端只是消费者 |
| 前端数量 | 单一交互式 CLI/TUI 为主 | TUI + `codex exec` + app-server **共用同一个 core** |
| 加一个新前端 | 往往要动到循环 | 写一个新的事件消费者即可，**core 不动** |
| 无头入口 | 有 `-p/--print` 等非交互用法，但重心在交互式 | **一等公民** `codex exec`：为自动化而生，`--json` 输出 JSONL |
| 成败信号 | 以会话内交互为主 | **退出码**（`error_seen → exit(1)`）+ `turn.failed`，给 `&&` / CI 用 |
| 渲染粒度 | 偏整体输出 | 每类事件一个 widget（`history_cell/exec.rs`、`patches.rs`…），可单独演化 |

**为什么？** 因为 Codex 一开始就设想「**一个 core，多个前端**」，而这些前端的运行场景天差地别：

- 终端里是 ratatui TUI，有人盯着，要好看、可交互；
- CI / 管道里是 `codex exec`，**没人盯着**——它需要 JSONL（机器可读）+ 退出码（`&&` 和 CI 据此分支），而且 stdout 不能掺人类噪声；
- IDE / cloud 里是 app-server，把事件发成 JSON-RPC `ServerNotification` 喂给编辑器。

要让这三种前端复用**同一套回合逻辑**，就**不能**把渲染写进循环——必须让 core 只产出与前端无关的 typed 事件，谁爱怎么画怎么画。这正是 [s10](../s10_sq_eq_protocol/) 把输入输出拆成两条队列（SQ/EQ）这条**架构脊梁**结出的果：s11 的 TUI 和 exec，就是这条脊梁上长出的**两个不同前端**——一个为人类交互（终端 widget），一个为无头自动化（机器读 + 退出码）。

这和全课程主线一脉相承：**Claude Code 以「人坐在终端/IDE 前交互」为中心**，于是循环可以写得更直接，但也更难长出「无头」「IDE 后端」这些形态；**Codex 下注「低/无人工干预的自主运行」**，于是从架构上就把 core 和前端用一条事件流隔开。无头不是「把交互模式关掉」，而是**为自动化重新设计的输出契约**。

## 深入：教学版 vs 真 Codex 源码

本章 ~180 行（一条 core 流 + 两族消费者）对应的是真 codex 里两个完整的 crate：`codex-rs/tui`（上万行）和 `codex-rs/exec`。简化在哪、真版多做了什么？

<details>
<summary>一、TUI 的 widget：教学版纯函数 vs history_cell 的 HistoryCell trait</summary>

教学版的 widget 是 `(event) -> list[str]`。真 codex 的 [`tui/src/history_cell/`](../../codex/codex-rs/tui/src/history_cell/) 里，每类条目是一个实现了 `HistoryCell` trait 的类型（`history_cell/mod.rs:189`）：

```rust
fn display_lines(&self, width: u16) -> Vec<Line<'static>>;             // 191 行
fn render(&self, area: Rect, buf: &mut Buffer) { /* 默认画 display_lines */ }  // 301 行
```

| 条目类型 | 教学版 | 真 codex 模块 |
|---|---|---|
| 命令执行 | `w_exec_begin`/`w_exec_end` | `history_cell/exec.rs` + `exec_cell/`（动画、流式输出、折叠） |
| 消息气泡 | `w_agent_message` | `history_cell/messages.rs`（markdown 渲染、`markdown_render.rs`） |
| 补丁 diff | （无） | `history_cell/patches.rs` + `diff_render.rs`（彩色 diff） |
| MCP 调用 | （无） | `history_cell/mcp.rs` |
| 计划/待办 | （无） | `history_cell/plans.rs` |
| 审批 | （无） | `history_cell/approvals.rs`（带交互按钮，回 SQ） |

关键共性没变：**每类事件一个独立 widget，互不耦合**，加一种新条目类型不会动到别的。区别是真 widget 要返回排版好的 `Vec<Line>`（含样式、按 `width` 折行），还要支持就地更新（命令流式输出时 `display_lines` 随时间变化）。

</details>

<details>
<summary>二、exec 的两个处理器：教学版 process 方法 vs 真 EventProcessor trait</summary>

真 codex 在 [`exec/src/event_processor.rs`](../../codex/codex-rs/exec/src/event_processor.rs) 定义了一个 trait，`human` / `jsonl` 两个处理器都实现它：

```rust
pub(crate) trait EventProcessor {
    fn print_config_summary(&mut self, config: &Config, prompt: &str, sc: &SessionConfiguredEvent);
    fn process_server_notification(&mut self, notification: ServerNotification) -> CodexStatus;
    fn process_warning(&mut self, message: String) -> CodexStatus;
    fn print_final_output(&mut self) {}
}
```

| | 教学版 | 真 codex-rs |
|---|---|---|
| 接口 | 一个 `process(event) -> bool` | trait + 4 个方法（含配置摘要、warning、收尾） |
| 选择 | `JsonlProcessor() if json_mode else HumanProcessor()` | `match json_mode { true => EventProcessorWithJsonOutput::new(..), _ => EventProcessorWithHumanOutput::create_with_ansi(..) }`（`lib.rs:671`） |
| 返回值 | bool（是否致命错误） | `CodexStatus::{Running, InitiateShutdown}` 控制何时收尾退出 |
| JSONL 处理器 | `print(json.dumps(...))` | [`event_processor_with_jsonl_output.rs`](../../codex/codex-rs/exec/src/event_processor_with_jsonl_output.rs) 把内部 `ServerNotification` 翻译成稳定的 `ThreadEvent`，再 `println!(serde_json::to_string(..))`，还维护 `next_item_id`、todo 列表、token 累计等状态 |

教学版把「致命错误」直接做成 `process` 的返回值；真版用一个 `CodexStatus` 枚举，让处理器能主动要求「该收尾了」（`InitiateShutdown`）。

</details>

<details>
<summary>三、事件种类：教学版 6 种 vs 协议里的几十种变体</summary>

教学版 core 只发 6 种事件（`thread.started` / `turn.started` / `item.started` / `item.completed` / `turn.completed` / `turn.failed`）。两边其实有**两套**事件命名，对应两个消费者：

- **TUI 侧**消费 [`protocol/src/protocol.rs`](../../codex/codex-rs/protocol/src/protocol.rs) 的 `EventMsg` 枚举（有几十个变体）：

```
TurnStarted, AgentMessage, AgentMessageContentDelta, AgentReasoning,
ExecCommandBegin, ExecCommandOutputDelta, ExecCommandEnd,
PatchApplyBegin, PatchApplyEnd, McpToolCallBegin, McpToolCallEnd,
WebSearchBegin, WebSearchEnd, TokenCount, PlanUpdate, TurnDiff,
ExecApprovalRequest, ApplyPatchApprovalRequest, StreamError, ...
```

- **exec 侧**消费 [`exec/src/exec_events.rs`](../../codex/codex-rs/exec/src/exec_events.rs) 的 `ThreadEvent`（对外稳定、可导出 TS 类型）：

```rust
#[serde(tag = "type")]
pub enum ThreadEvent {
    #[serde(rename = "thread.started")] ThreadStarted(..),
    #[serde(rename = "turn.started")]   TurnStarted(..),
    #[serde(rename = "item.started")]   ItemStarted(..),
    #[serde(rename = "item.completed")] ItemCompleted(..),
    #[serde(rename = "turn.completed")] TurnCompleted(..),
    #[serde(rename = "turn.failed")]    TurnFailed(..), ...
}
```

每个 item（`ThreadItemDetails`）也是一整套：`agent_message` / `reasoning` / `command_execution` / `file_change` / `mcp_tool_call` / `web_search` / `todo_list`。教学版只实现了 `agent_message` 与 `command_execution`（含真实的 `status` / `exit_code` / `aggregated_output` 字段）。

注意 `EventMsg` 里的 `*Delta` 变体（`AgentMessageContentDelta` / `ExecCommandOutputDelta`）——真 TUI 要处理**流式增量**（模型边想边吐字、命令边跑边出 stdout），widget 得能「持续追加并重绘」。教学版每个事件「一拍到位」，所以渲染函数可以是无状态纯函数。

为什么 exec 侧要单独搞一套 `ThreadEvent`？因为 JSONL 是**对外契约**：下游脚本、cloud、IDE 都按它 parse，字段一改就是 breaking change，所以才用 `ts-rs` 导出类型、字段名固定 `snake_case`、状态枚举显式（`in_progress`/`completed`/`failed`/`declined`）。

</details>

<details>
<summary>四、第三个消费者 app-server，与 exec 主循环的真实形态</summary>

本章最想让你记住的一句话：**TUI、exec、app-server 是同一类东西——都是 core 事件流的消费者**，只是渲染目标不同。

| 前端 | 渲染成什么 | 入口 crate |
|---|---|---|
| TUI | ratatui 终端界面 | `codex-rs/tui` |
| exec | 人类文本 / JSONL | `codex-rs/exec` |
| app-server | JSON-RPC `ServerNotification`，喂 IDE/cloud | `codex-rs/app-server` |

它们消费的是**同一套事件**。这版 TUI 甚至已重构成消费 app-server 的 `ServerNotification`（见 [`tui/src/app/app_server_event_targets.rs`](../../codex/codex-rs/tui/src/app/app_server_event_targets.rs) 里那张巨大的派发表），和 exec 走同一条协议。

而真 exec 的「循环」也不是教学版那个同进程 `for`。它是个**客户端**，连到一个 in-process app-server，循环消费 `next_event()`（[`exec/src/lib.rs`](../../codex/codex-rs/exec/src/lib.rs)）：

```rust
loop {
    let server_event = tokio::select! {
        maybe_interrupt = interrupt_rx.recv(), .. => { /* 发 TurnInterrupt */ continue; }
        maybe_event = client.next_event() => maybe_event,        // lib.rs:955
    };
    match server_event {
        ServerNotification(n) => match event_processor.process_server_notification(n) {
            CodexStatus::Running => {}
            CodexStatus::InitiateShutdown => { request_shutdown(..).await?; break; }  // 1001
        }, ..
    }
}
event_processor.print_final_output();                            // 1027
if error_seen { std::process::exit(1); }                         // 1028：给 CI 的信号
```

差距：真版能在无头模式里**响应 Ctrl-C / 信号发 `TurnInterrupt`**（`tokio::select!` 那一臂），把 `turn.failed` / `Error` 累计成 `error_seen`，还能 `--output-last-message <file>` 把最后一条消息写文件、`codex exec resume` 接着上次的 thread 跑、`--image` 喂本地图片。教学版把这些都压成「跑完循环、看 error_seen」，但保留了最本质的主干：**prompt 进 → 事件流出 → 选消费者渲染 → 退出码表态。**

教学版 `drive_tui` 那个 `for event in run_turn_events(prompt): renderer.render(event)`，和 `run_exec` 的 `for event in run_turn_events(prompt): processor.process(event)` 几乎一模一样——这种对称正是「core 与前端解耦」想达到的效果：前端可以有很多个，它们对 core 都只是「一个消费事件流的循环」。

</details>

## 运行

```bash
python s11_frontends/code.py --demo            # 离线：同一条流，先 TUI(box) 渲染、再 JSONL 输出（exit 0）
python s11_frontends/code.py --json "看看目录"   # 无头 exec：argv 当 prompt，输出 JSONL
python s11_frontends/code.py --exec "执行 \`ls\`" # 无头 exec：人类可读文本
echo "列出 TODO" | python s11_frontends/code.py --exec   # stdin 喂 prompt
python s11_frontends/code.py --plain           # 交互 TUI，换 [plain] 渲染器（core 一行不动）
python s11_frontends/code.py                   # 交互 TUI（默认 box 渲染器；输入 q 退出）
```

默认 `backend=mock`，离线可跑。把 JSONL 交给脚本 parse 试试（注意 `2>/dev/null` 把启动横幅滤掉，验证 stdout 是纯 JSONL）：

```bash
python s11_frontends/code.py --json "执行 \`echo hi\`" 2>/dev/null \
  | python3 -c "import sys,json; [print(json.loads(l)['type']) for l in sys.stdin if l.strip()]"
# thread.started / turn.started / item.started / item.completed / item.completed / turn.completed
```

> 小实验一：跑 `--demo`，对照「消费者 A」（TUI 框线）和「消费者 B」（JSONL）两段输出——它们来自**同一个 `stream` 列表**，core 没有任何改动。
> 小实验二：让模型跑一条会失败的命令（`python s11_frontends/code.py --json "执行 \`false\`"`），观察 `--json` 多出一条 `turn.failed`，且**退出码变成 1**——这正是 CI 据以判定成败的信号。

## 小结

- 前端 = **事件流的消费者**，不含业务逻辑：core 产出 typed 事件，前端只把事件「显示出来」。
- 本章一次给出两族消费者：**TUI 渲染器**（widgets as functions + 可热插拔的 `Box`/`Plain` 渲染器）和 **headless exec**（人类文本 / JSONL + 退出码）。
- **widgets as functions**：每类事件一个独立渲染单元（真 codex 的 `history_cell/*`），可单独增删改。
- **JSONL 是对外契约**：stdout 必须纯净（人类噪声走 stderr），字段稳定，供脚本/CI/管道 parse；**退出码**让 Codex 能嵌进 `&&` 和 CI。
- TUI、exec、app-server 是**同一类消费者**，只是渲染目标不同——这正是 [s10](../s10_sq_eq_protocol/) 队列解耦结出的果，也是「Codex 为低/无人工干预的自主运行下注」最直接的体现。
- **生产级**：事件流是断续的——前端要增量渲染（不假死）、给乱序/中断/回退兜底（不留烂尾 UI）、无头 `codex exec` 与 TUI 消费同一条流（显示与产生彻底剥离，见「生产级」一节）。
- 下一站 [s12 更多工具](../s12_tools_extra/)：给 agent 添 `plan` / `web_search` / `view_image` 等工具——它们产生的也只是这条事件流上的新事件。

## 思考

<div class="think">

1. 教学版每个事件「一拍到位」，所以 widget 是无状态纯函数。可真 TUI 要处理 `ExecCommandOutputDelta` 这种**流式增量**——同一个命令的输出一段段到来。这时 widget 还能是纯函数吗？你会把「已经收到的输出」这个状态放在哪？放进 widget、放进渲染器、还是放进 core？
2. 「core 不含渲染、前端不含逻辑」听起来很干净。但审批（[s10]）天然要 TUI 弹个框、等用户点，再把决定通过 SQ 送回 core——这算「业务逻辑」泄漏进前端了吗？该由谁决定「这条命令需要审批」，由谁决定「弹窗长什么样」？无头的 exec 又凭什么可以跳过这一步？
3. 同一条事件流要喂给三种前端（终端 / CI / IDE）。注意本章里 TUI 侧（`EventMsg`）和 exec 侧（`ThreadEvent`）其实是**两套**事件命名——为什么真 codex 不干脆只用一套？如果某种事件只有 TUI 用得上（比如纯装饰性的动画提示），把它放进**对外稳定**的 `ThreadEvent` 契约合适吗？这条边界你会划在哪？
4. 如果某个命令在 stdout 里**自己**打印了一行看起来像 JSON 的内容，你的 `| jq` 管道会不会把它误当成一条事件？真 codex 用 stderr/stdout 分流 + 固定 schema 防这件事——你还能想到哪些「输出污染」会坑到下游脚本？
5. 退出码只有 0/1 够用吗？如果一个回合「部分成功」（3 条命令成 2 条、1 条失败），CI 应该当成功还是失败？换成你来定，会怎么设计这个退出码语义？
6. 如果让你给 Codex 加第四种前端——比如一个网页 dashboard——按本章的架构你需要写什么、又**不**需要碰什么？反过来想：Claude Code 那种「UI 和循环耦合较紧」的写法，加这个 dashboard 会多付出什么代价？

</div>

[s10]: ../s10_sq_eq_protocol/
