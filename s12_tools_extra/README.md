# s12: Tools Extra — 更多工具：plan / web_search / view_image，再加几个工具

> *"给 agent 加能力，往往不是改循环，而是多注册一个 handler；而在 Codex 里，每个 handler 还要学会'报告自己在干什么'。"*

[learn-codex 总览](../README.md) · [前端：TUI + exec](../s11_frontends/) → **本章** → [Hooks](../s13_hooks/)

---

## 先把思想说透：为什么"给 agent 更多工具"几乎不用动 agent

到这里，你已经见过 Codex 最硬核的几块：回合循环（[s01](../s01_agent_loop/)）、apply_patch（[s03](../s03_apply_patch/)）、审批与沙箱（[s04](../s04_approval/) / [s05](../s05_sandbox/)）。现在我们换个轻松点、但同样重要的话题：**怎么给这个 agent "加技能"**。直觉上你可能以为"加技能"是件大工程，要去改它的大脑、改它的循环。恰恰相反——理解下面三个道理，你会发现加工具简单得有点反高潮，而 Codex 真正用心的地方，藏在第三点。

**道理一：模型不需要你"教"它用工具，它只需要一张"菜单"。**
回想 [s01](../s01_agent_loop/) 的顾问比喻：模型是个只会写纸条的聪明顾问。你给它一张菜单——"你可以点这几样：跑命令、改文件、查网页、看图片"——它就会在合适的时候写一张纸条说"我要点'查网页'，参数是 codex"。**菜单**就是工具的 schema（名字 + 描述 + 参数长什么样）。所以"加一个工具"的第一步，仅仅是往菜单上**多印一行**。模型读到了，自然就会在需要时点它。你不必写任何"如果用户问 X 就调用 Y"的 if-else——那是模型的活。

**道理二：循环早就准备好了，它只按名字查表。**
[s02](../s02_tool_use/) 已经讲透了这点：回合循环里有一句 `HANDLERS.get(tc.name)`——模型说要点哪样菜，循环就按名字去一张 `{名字: 函数}` 的表里找对应的厨师，把它做出来。**所以"加一个工具"的第二步，就是往这张表里多注册一行 `"web_search": run_web_search`。** 循环本身一个字都不用改。这一章我们一口气加三个工具（计划清单、网页搜索、看图片），你会亲眼看到：`run_turn` 和 [s01](../s01_agent_loop/) 那个**一字不差**。这就是"加技能"的全部秘密——它是声明式的，不是改造大脑。

**道理三（这才是 Codex 的讲究）：工具不能"闷头干活"，它得一边干一边"喊话"。**
前两点 Claude Code 也一样。Codex 真正多出来的一层是：**每个工具动作都得是"看得见"的**。为什么？想象 `web_search` 要跑三秒，`view_image` 要把一张大图读进来、编码。如果工具只是"闷头算完、扔回一个字符串",那在这几秒里，盯着屏幕的人（或者 CI 的日志、IDE 的侧边栏）只会看到一个**毫无反馈的卡顿界面**——它在搜什么?卡住了吗?

Codex 的答案是：让每个工具在开干时喊一声"我开始搜了"（一个 **Begin** 事件），干完再喊一声"搜完了，搜的是 codex"（一个 **End** 事件），中间夹着真正的活。这两声"喊话"流进 [s10](../s10_sq_eq_protocol/) 那条事件总线，于是 TUI、`codex exec`、IDE 后端——**任意一个前端**——都能实时画出"🔍 搜索中…"再更新成"搜索完成"。

把这三点串起来，你就摸到了本章的主干：**加工具 = 多印一行菜单 + 多注册一个 handler（道理一、二，与 Claude Code 共享）；而 Codex 还坚持让每个 handler 把自己的过程广播成事件（道理三，是它"一个 core、多个前端"架构的必然延伸）。** 这也解释了一个看似奇怪的现象：Codex 的"第一公民"工具其实很少（主力就是跑命令 + 改文件），但它把"任何工具动作都是可观测事件"这件事做得极其统一——能力靠注册往上加，可观测性靠事件兜底。

## 问题

让 agent 干一件真实的多步骤活——"查一下这个 API 怎么用、看看用户给的报错截图、然后把修复写进代码"。它会撞上两类不同的难处：

**第一类：它需要的能力，循环里现在没有。** 它没法上网查"现在"的事实（模型训练数据有截止日期），也没法"看"一张本地图片。这两样得作为新工具加进来。

**第二类（更隐蔽）：就算工具加进来了，它们"跑的过程"对前端是隐形的。** `web_search` 可能要跑几秒、甚至拆成多个子查询；`view_image` 要把图片读进来、编码、附到下一次请求。如果前端拿到的只有"最终那个返回值",用户就盯着一个没有任何反馈的界面，不知道 agent 到底在搜什么、看哪张图、卡在哪。问题不在"工具能不能跑",而在"工具跑的**过程**对前端可见吗"。

本章用三个工具把这两类问题一起解决，并借它们讲清"加能力"与"保持可观测"这两件事如何协同。

## 解决方案

两步走，正好对应上面两类问题：

**第一步：把三个工具注册进同一份 `HANDLERS`。** 循环不改，模型读到菜单就会按需调用。

**第二步：让每个工具把自己的过程建模成一对生命周期事件。** 开始发 `Begin`、结束发 `End`，中间夹着真正的工作；用一个 `call_id` 把这一对串起来，前端就能在那一行原地把"进行中"更新成"已完成"。

```
   model 看到菜单（3 个工具的 schema），按需点单
        │
        ├─ update_plan({plan:[…]})  → ⟦event⟧ PlanUpdate{steps}        前端：画出带勾选框的待办列表
        │
        ├─ web_search("codex")
        │       │
        │       ⟦event⟧ WebSearchBegin{call_id}      ← 前端：显示"🔍 搜索中…"
        │       （真正去搜 / 本教学版返回 canned 结果，绝不联网）
        │       ⟦event⟧ WebSearchEnd{call_id, query, action}  ← 前端：原地更新成"搜了 codex"
        │
        └─ view_image("shot.png")
                │
                ⟦event⟧ ViewImageBegin{call_id, path}   ← 前端：显示"🖼 查看中…"
                （读字节 / 嗅探类型；真身还会解码、resize、附进请求）
                ⟦event⟧ ViewImageEnd{call_id, path}     ← 前端：显示看了哪张图

   每个工具的返回值都照常作为 function_call_output 回灌 → 继续循环
```

这套"一切动作皆事件"正是 [s10](../s10_sq_eq_protocol/) 事件队列（EQ）的直接应用：core 只管按时序吐事件，三个前端各自决定怎么画。

## 工作原理

看 [code.py](code.py)。`run_turn` 仍是 [s01](../s01_agent_loop/) 搬运、**一字未改**；新增的是三个 handler，以及它们内部发出的事件。

**第 1 步** — 一个模拟事件总线的 `emit`，外加给每次调用发个 `call_id`（Begin/End 配对的钥匙）：

```python
def emit(event, **fields):        # 模拟把事件放进 EQ；真身会被 TUI/IDE 消费
    print(f"⟦event⟧ {event} {fields}")
```

**第 2 步** — `update_plan`：校验 → 整盘替换内存清单 → 发 `PlanUpdate` 事件并渲染 → 回固定一句话。关键在"每次重发完整清单、整盘替换",于是计划"活"在对话上下文之外、不靠模型记忆：

```python
def run_update_plan(plan, explanation=None):
    # 校验每项有 step + 合法 status；至多一个 in_progress
    CURRENT_PLAN[:] = plan                 # 整盘替换
    emit("PlanUpdate", explanation=explanation, steps=len(plan))
    print(render_plan(plan, explanation))
    return "Plan updated"                  # 回模型的就是这固定一句
```

**第 3 步** — `web_search`：发 `Begin` → 取结果（**离线 canned，绝不联网**）→ 发 `End`：

```python
def run_web_search(query):
    call_id = next_call_id()
    emit("WebSearchBegin", call_id=call_id)
    hits = _CANNED.get(...)                # 内置假结果，不联网
    emit("WebSearchEnd", call_id=call_id, query=query, action="search")
    return "\n".join(...)
```

**第 4 步** — `view_image`：发 `Begin` → 读字节、用魔数嗅探类型 → 发 `End`，只回**元数据**（不做真视觉）：

```python
def run_view_image(path):
    call_id = next_call_id()
    emit("ViewImageBegin", call_id=call_id, path=str(p))
    data = p.read_bytes()
    mime = _sniff_type(data[:16])          # PNG/JPEG/GIF… 魔数
    emit("ViewImageEnd", call_id=call_id, path=str(p))
    return f"viewed image: ... type={mime} size={len(data)} bytes (metadata only)"
```

这三块对应真 Codex 的三处源码：参数结构来自 [`protocol/src/plan_tool.rs`](../../codex/codex-rs/protocol/src/plan_tool.rs)（`StepStatus` / `UpdatePlanArgs`），plan 的 handler 在 [`core/src/tools/handlers/plan.rs`](../../codex/codex-rs/core/src/tools/handlers/plan.rs)（`PlanHandler`），web_search 的格式化与事件在 [`core/src/web_search.rs`](../../codex/codex-rs/core/src/web_search.rs)，view_image 的 handler 在 [`core/src/tools/handlers/view_image.rs`](../../codex/codex-rs/core/src/tools/handlers/view_image.rs)；三类事件都定义在 [`protocol/src/protocol.rs`](../../codex/codex-rs/protocol/src/protocol.rs) 的 `EventMsg` 枚举里。

**走一遍**（用 `--demo` 的真实输出，看每步数据长什么样、为什么）：

`--demo` 不碰模型、不联网，把三个工具依次跑一遍。挑几步看：

① 调 `update_plan`，提交一份 3 步清单（一步 `completed`、一步 `in_progress`、一步 `pending`）。handler 先发事件、再渲染、最后回一句话：

```text
  ⟦event⟧ PlanUpdate explanation='Triage with help from the new tools' steps=3
── plan ──────────────────────────────
Triage with help from the new tools
  [x] Search docs for the API        ← completed
  [~] Inspect the screenshot         ← in_progress（至多一个）
  [ ] Write the fix                  ← pending
──────────────────────────────────────
handler 回给模型 → Plan updated
```

注意回给**模型**的只有 `"Plan updated"` 五个字——不回显整张表（省 token，模型本来就刚发过它）；那张漂亮的清单是发给**前端**看的。这正是"对模型说一句话、对前端发一个事件"的分工。

② 故意提交两个 `in_progress`，handler 当场拒绝——这是"至多一个进行中"的硬约束在起作用：

```text
rejected → Error: at most one step may be in_progress at a time
```

③ 调 `web_search("codex")`。注意它被**一对** Begin/End 事件夹住，`call_id` 相同（`call_1`）——前端就靠这把钥匙把"搜索中"原地更新成结果：

```text
  ⟦event⟧ WebSearchBegin call_id='call_1'
  ⟦event⟧ WebSearchEnd call_id='call_1' query='codex' action='search'
- OpenAI Codex — coding agent
  https://openai.com/codex
  ...
```

④ 调 `view_image` 看一张临时造出来的 1 像素 PNG。它读了字节、靠 8 字节魔数 `\x89PNG…` 嗅出 `image/png`，回的是**元数据**而非视觉内容；同样被 Begin/End 夹住：

```text
  ⟦event⟧ ViewImageBegin call_id='call_2' path='…/_demo_pixel.png'
  ⟦event⟧ ViewImageEnd   call_id='call_2' path='…/_demo_pixel.png'
viewed image: name=_demo_pixel.png type=image/png size=32 bytes (... metadata only)
```

⑤ 最后用一个不存在的路径调 `view_image`，看错误如何回灌给模型——**注意即便出错，Begin 之后我们也补了一个 End**，不给前端留下"只有 Begin、没有 End"的悬空事件：

```text
  ⟦event⟧ ViewImageBegin call_id='call_3' path='…/_does_not_exist.png'
  ⟦event⟧ ViewImageEnd   call_id='call_3' path='…/_does_not_exist.png'
Error: image path is not a file: _does_not_exist.png
```

跑完 demo 会自动删掉那个临时 PNG。整条链路里，循环逻辑一行没动——新增的全在三个 handler 内部。

## 生产级：工具输出会爆——截断要稳，且在字符边界上

plan / web_search / view_image 这些工具，输出可能很大：一次网页搜索几十条结果、一张图几 MB、一个长计划几百行。原样喂回模型，轻则烧光上下文、重则一条工具结果就把窗口顶爆。生产级对工具输出有两条铁律：

- **截断有上限**：每个工具输出有一个最大长度，超了就裁（保留头部 + 标注"还有 N 字符省略"）。这和 [s02 的 dispatch](../s02_tool_use/)、[s07 的压缩](../s07_context_compaction/) 是一条线——**任何进上下文的东西都得有预算**。
- **在字符边界上裁，别切坏多字节字符**：UTF-8 里一个汉字/emoji 是多个字节，从中间砍一刀会切出半个乱码字符。真 Codex 用 `truncate_to_char_boundary`（[`tools/handlers/list_available_plugins_to_install.rs:100`](../../codex/codex-rs/core/src/tools/handlers/list_available_plugins_to_install.rs)）——按**字符**而非**字节**截断。一个只截字节数的玩具会在这里悄悄吐出乱码。

> 一句话：加工具容易，但每加一个能产出大输出的工具，就多一个"撑爆上下文/吐出乱码"的入口——生产级要在每个出口都把输出**有上限、按字符边界**地收住。

## 🆚 与 Claude Code 的不同

这三个工具，两家**几乎都有**（这正是看点）：计划清单、网页搜索、看图。分野不在"有没有",而在"工具的过程怎么暴露给前端"。

| | Claude Code | Codex | 关系 |
|---|---|---|---|
| 计划清单 | `TodoWrite`（`{content, status, activeForm}`） | `update_plan`（`{step, status}`，[`plan.rs`](../../codex/codex-rs/core/src/tools/handlers/plan.rs)） | **≈** 都重发完整清单、三态状态、至多一个进行中 |
| 网页搜索 | 有（`WebSearch` / `WebFetch`，harness 执行） | 有（`web_search`，多为 **OpenAI 托管**执行；[`web_search.rs`](../../codex/codex-rs/core/src/web_search.rs)） | **≈** 都有；执行落点不同 |
| 看图 | 有（`Read` 能读图片、走视觉） | 有（`view_image` 把图解码后附进请求；[`view_image.rs`](../../codex/codex-rs/core/src/tools/handlers/view_image.rs)） | **≈** 都有 |
| 工具过程怎么暴露 | 工具结果 + 单一 UI 内置渲染 | **每个动作发显式 Begin/End 协议事件**，流过 EQ 给多前端 | 分野在这里 |
| 加一个工具要改什么 | 注册 handler（循环不变） | 注册 handler（循环不变）**+ 让它发事件** | Codex 多一层可观测约定 |

**为什么两边的工具这么像、分野却落在"事件"上?** 因为"加能力"这件事本身是通用的——给模型一张菜单、按名字查表执行——所以两家在 `TodoWrite` ≈ `update_plan`、搜索、看图上几乎收敛到同一形状（连"重发完整清单"这种细节都一样）。真正拉开差距的是**架构**：Codex 是"一个 core、多个前端"（[s11](../s11_frontends/)）——TUI、`codex exec`、IDE 后端都要能实时显示"正在搜索 codex…""正在查看 shot.png…"。要做到这点，core 就**不能把工具过程藏在一次函数返回里**，必须把它**广播成事件**。于是 `WebSearchBegin`/`WebSearchEnd`/`ViewImageToolCall`/`PlanUpdate` 与 `ExecBegin`/`ExecEnd`（[s04](../s04_approval/) 见过）并列，构成一套统一的"可观测动作"词汇表。Claude Code 更偏单一交互前端，工具过程交给那一个 UI 内置渲染即可，不必先抽象成跨前端的协议事件。

> 一句话：**两家"有什么工具"几乎一样；Codex 多坚持一条——"工具在干什么"必须建模成协议事件，好让任意前端（含无人值守的 `codex exec`）都能观测每一步。** 这正是全课主线"Codex 为低人工干预的自主运行下注"在工具层的体现。

## 深入：教学版 vs 真 Codex 源码

<details>
<summary>一、真 PlanHandler 几乎不存计划，只发一个事件转手就忘</summary>

教学版把计划存进 `CURRENT_PLAN` 并直接 `print`。真 [`plan.rs`](../../codex/codex-rs/core/src/tools/handlers/plan.rs) 的 `PlanHandler::handle` 干的事极少：

```rust
let args = parse_update_plan_arguments(&arguments)?;                  // 解析 UpdatePlanArgs
session.send_event(turn.as_ref(), EventMsg::PlanUpdate(args)).await;  // 广播事件
Ok(boxed_tool_output(PlanToolOutput))                                 // 回模型 "Plan updated"
```

注意它**没有把计划存进 session 状态**——它把整份 `UpdatePlanArgs` 原样塞进一个 `PlanUpdate` 事件 send 出去，由消费事件的前端（TUI 的历史单元）去保存和渲染。"计划活在上下文之外"在真身里更彻底：它活在**前端的事件历史**里，core 转手就忘。回给模型的永远是常量 `PLAN_UPDATED_MESSAGE`（就是字符串 `"Plan updated"`），不回显整张表——省 token，且模型本来就刚发过它。

| | 教学版 | 真 codex-rs |
|---|---|---|
| 计划存哪 | core 进程内存 `CURRENT_PLAN` | 不存于 core；随 `PlanUpdate` 事件流向前端 |
| 渲染 | 直接 `print` | TUI/IDE 消费事件后各自渲染 |
| 回模型 | `"Plan updated"` | `PLAN_UPDATED_MESSAGE = "Plan updated"`（一字相同） |

</details>

<details>
<summary>二、计划的 status 是枚举，schema 由 plan_spec 程序化生成</summary>

教学版的 `status` 是字符串 + 一个 `VALID_STATUS` 集合手工校验。真源码用 Rust 枚举 `StepStatus { Pending, InProgress, Completed }`，配 `#[serde(rename_all = "snake_case")]`，保证上线时是 `pending`/`in_progress`/`completed`——非法值在反序列化 `UpdatePlanArgs` 时就被 serde 直接拒掉，根本进不了 handler（教学版则是进了 handler 才校验）。

工具的 JSON schema 也不是手写的，而是 [`plan_spec.rs`](../../codex/codex-rs/core/src/tools/handlers/plan_spec.rs) 里 `create_update_plan_tool()` 程序化拼出来的：

```rust
JsonSchema::string_enum(
    vec![json!("pending"), json!("in_progress"), json!("completed")], ...)
// plan 是 array<object{step, status}>，required = ["plan"]
```

我们手写的那份 `TOOLS[0]["parameters"]` 跟它逐字段对应。描述里那句 "At most one step can be in_progress at a time." **逐字**来自真源码——它是写进**给模型看的工具描述**里的软约束；我们在 handler 里又加了一道硬校验（拒绝两个 `in_progress`），两道防线一软一硬。

</details>

<details>
<summary>三、真 web_search 多为 OpenAI 托管工具，core 只负责事件与展示</summary>

教学版的 `web_search` 是个本地函数，查一张内置 `_CANNED` 表。真 Codex 的 `web_search` **通常不在 core 里执行**——它是 `ToolSpec::WebSearch` 这个特殊变体（[s02](../s02_tool_use/) 深入里见过的工具规格变体之一），序列化成 `{"type":"web_search", ...}` 发给模型；搜索由 **OpenAI 的 Responses API 托管端**完成，结果直接回到模型上下文。

core 这边的 [`web_search.rs`](../../codex/codex-rs/core/src/web_search.rs) 主要负责**把搜索动作格式化给前端看**——`web_search_action_detail` 把 `WebSearchAction`（`Search{query, queries}` / `OpenPage{url}` / `FindInPage{url, pattern}` / `Other`）渲染成一行人类可读文字；成对的 `WebSearchBeginEvent`/`WebSearchEndEvent` 则定义在 [`protocol/src/protocol.rs`](../../codex/codex-rs/protocol/src/protocol.rs)。也就是说真身的分工是：搜索托管在远端、**事件与展示**留在 core。教学版没有远端，于是把"假装搜索"和"发事件"都塞进一个本地函数，但保留了 Begin/End 这层可观测骨架。

| | 教学版 | 真 codex-rs |
|---|---|---|
| 谁执行搜索 | 本地查 `_CANNED` 表 | OpenAI 托管端（`type:"web_search"`） |
| core 的职责 | 假装搜 + 发事件 | 格式化 `WebSearchAction` + 发 Begin/End |
| End 事件字段 | `call_id, query, action="search"`（字符串） | `call_id, query, action: WebSearchAction`（带子查询/URL 等结构） |

</details>

<details>
<summary>四、真 view_image 把图片解码、resize、作为 InputImage 附进请求</summary>

教学版的 `view_image` 只 `read_bytes()` + 嗅探魔数 + 回一句元数据。真 [`view_image.rs`](../../codex/codex-rs/core/src/tools/handlers/view_image.rs) 的 `ViewImageHandler` 做的远不止：

1. 先查模型 `input_modalities` 是否含 `Image`——不支持就直接拒，回那句固定的 `"view_image is not allowed because you do not support image inputs"`。
2. 经沙箱化的 filesystem 读字节，用 `load_for_prompt_bytes`（`codex_utils_image`）**真正解码并 resize**（默认按 `PromptImageMode` 缩放，可选保留原图）。
3. 把图片转成 base64 **data URL**，作为 `InputImage` 塞进工具输出——于是图片**作为视觉输入进了下一次 Responses 请求**，模型真能"看见"。
4. 随 `TurnItem::ImageView` 发 `started`/`completed`，对应 `ViewImageToolCallEvent { call_id, path }`。

教学版第 3 步整个砍掉（不做真视觉、不附 data URL），只保留"读文件 + 发 Begin/End 事件 + 回元数据"。`detail` 字段（真身只接受 `high` / `original`，给别的值会报错）这类细节我们也省了。一句话对照：**教学版的 view_image 是"嗅一下文件头"，真身是"先确认模型能看图，再解码、缩放、附成视觉输入"。**

为什么真身要 resize 而不是原图直传？因为视觉模型对超大图既贵又慢，默认缩放在"看得清"和"省 token"之间取平衡；只有显式要 `original` 且模型支持时才保留原分辨率。

</details>

<details>
<summary>五、Begin/End 事件如何流到 TUI（连回 s10 / s11）</summary>

`PlanUpdate` / `WebSearchBegin` / `WebSearchEnd` / `ViewImageToolCall` 都是 [s10](../s10_sq_eq_protocol/) 事件队列（EQ）里 `EventMsg` 枚举的变体。它们由 core 在 handler 里 `session.send_event(...)` 产出，经 EQ 流到前端（[s11](../s11_frontends/)）。

`call_id` 是把 Begin 和 End 配对的钥匙：TUI 收到 `WebSearchBegin{call_id}` 先画一行"搜索中",收到同 `call_id` 的 `WebSearchEnd` 再原地更新成搜了什么。这正是 SQ/EQ 解耦的价值——core 只管"按时序吐事件",三个前端（TUI、`codex exec`、IDE 后端）各自决定怎么画。我们的 `emit()` 就是这条链路被压扁成一行 `print` 的样子。也正因为有这把钥匙，handler 里**即便出错也要补发 End**（看我们 `view_image` 失败分支），否则前端会留下一个永远停在"进行中"的悬空条目。

</details>

## 运行

```bash
python s12_tools_extra/code.py --demo   # 离线：update_plan + web_search(canned) + view_image(元数据) + 生命周期事件
python s12_tools_extra/code.py          # 交互模式（mock 后端，无需 key）
```

`--demo` 会在当前目录建一个临时 `_demo_pixel.png`，跑完自动删除；`web_search` 永不联网。默认 `backend=mock`，离线可跑；想接真模型见根目录 [.env.example](../.env.example)。

## 小结

- "给 agent 更多工具"= 多印一行菜单（schema）+ 往 `HANDLERS` 多注册一个 handler——回合循环**一字不改**（呼应 [s02](../s02_tool_use/)）。本章一口气加了三个。
- 新维度是**可观测**：Codex 把每次工具动作建模成事件——`PlanUpdate`、成对的 `WebSearchBegin`/`WebSearchEnd`、`ViewImageToolCall`——用 `call_id` 把 Begin/End 配对，让任意前端实时显示进度。
- 真身里：`update_plan` 的 handler 转手就忘、只发事件（计划活在前端历史里）；`web_search` 多由 OpenAI 托管端执行、core 只管格式化与事件；`view_image` 会先确认模型支持图像，再解码、resize、把图作为 `InputImage` 附进下一次请求。
- 与 Claude Code **≈**：计划（`TodoWrite`）/搜索/看图两家都有；分野在 Codex 把每个动作抽象成跨前端的协议事件（[s10](../s10_sq_eq_protocol/) / [s11](../s11_frontends/)），服务于"一个 core、多个前端"。
- **生产级**：工具输出会爆——必须有长度上限并截断，且在**字符边界**上裁（`truncate_to_char_boundary`，别切坏多字节字符）；任何进上下文的东西都得有预算（见「生产级」一节）。
- 下一站 [s13](../s13_hooks/)：Hooks——在工具调用前后插入用户自定义逻辑，进一步扩展 agent 而不必 fork 掉 core。

## 思考

<div class="think">

1. 本章加三个工具，循环一行没改——这说明"加能力"是声明式的。那么一个 agent 的"上限"到底由什么决定：工具的**数量**，还是模型**会不会在对的时机点对的工具**？如果你的 agent 配了 20 个工具却老是点错，你会先加第 21 个，还是先改菜单上的**描述**？

2. 一次工具调用发 `Begin` 和 `End` 两个事件，比只发一个"完成"事件多了什么？如果某次 `web_search` 跑到一半被用户打断（[s10](../s10_sq_eq_protocol/) 的 Interrupt），只有 `Begin` 没有 `End` 的"悬空事件"前端该怎么收场？（提示：想想我们 `view_image` 出错时为什么也补了一个 End。）

3. `web_search` 在 Codex 里多半是 OpenAI 托管执行、结果直接回模型；如果让你改成"core 自己用一个搜索 API 执行",你会失去什么、又会获得什么？（想想无网/离线环境、可控性、以及"谁为搜索质量负责"。）

4. 真 `view_image` 会先缩放再喂给模型，在"省 token / 跑得快"和"别丢失模型需要看清的细节"之间权衡。如果是一张满是小字的报错截图，默认缩放可能让模型看不清——"要不要保留原图"这个决定，你会交给模型自己判断，还是交给用户?为什么?

5. Claude Code 把工具过程交给单一 UI 内置渲染，Codex 把它抽象成跨前端的协议事件。当你的产品**只有一个前端**时，后者这套"事件词汇表"是不是过度设计?反过来，等你哪天要加第二个前端，没有它又会有多痛?这条"为多前端提前下注"的取舍，扣回全课主线——你觉得它值不值?

</div>
