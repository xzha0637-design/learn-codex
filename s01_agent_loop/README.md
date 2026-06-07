# s01: Agent Loop — 一个循环就够了

> *"回合循环是所有 agent 的共同底座。Agency 来自模型，不来自循环。"*

[learn-codex 总览](../README.md) · **回合循环** → [工具与分发](../s02_tool_use/)

---

## 先把思想说透：为什么"一个循环"就是一个 agent

你可能听过"agent 很复杂"。其实它的内核简单到不可思议——只有一个循环。难的不是循环本身，是想通它背后的三个道理。把这三点想明白，后面每一章你都会很轻松，因为你已经抓住了主干。

**道理一：模型很聪明，但它"动不了"。**
大模型能读懂你的话、能想出"该跑 `ls` 看看目录里有什么"——可它本身**只会输出文字**。它没有手，碰不到你的文件，也跑不了命令。它像一个绝顶聪明、却只能写纸条的顾问：纸条上写着"去 A 文件夹看看"，但它自己迈不出房间。

**道理二：那"行动力"从哪来？来自一小段把"写纸条"接到"真去做"的代码。**
你只需要做一件事：**读顾问的纸条 → 真的去执行 → 把结果递回去给它看 → 它接着写下一张纸条**，如此往复。这个"递纸条—执行—回传"的往复，就是**回合循环**。模型每写一张"要动手"的纸条，我们就替它执行，再把结果喂回去。仅此而已——没有魔法。

**道理三（最关键）：聪明是模型的，循环只是个忠实的跑腿。**
循环本身什么都不"懂"，它不做任何判断，只负责"传话和执行"。是**模型**在决定下一步做什么。所以你会看到一个惊人的事实：本课从头到尾，这个循环几乎**一行都不用改**——变的只是我们往它周围添的东西（怎么改文件、怎么不让它闯祸、怎么帮它记事……）。

这些"周围的东西"合起来就是一台**载具**：模型是司机，载具载着它去任何地方干活。**这门课，就是教你一块一块地造出这台载具。** 而第一块，就是这个循环。

## 问题

你问模型："看看这个目录里有什么，然后跑一下 build。"

模型能输出一条命令，但输出完就停了。它不会自己执行，也看不到结果继续推理。你得手动把它的命令复制到终端、跑、再把输出贴回去——每一个来回，你都在当那个中间层。

把这个中间层自动化，就是回合循环（agent loop）。

## 解决方案

一个 `while True`：模型要用工具就执行、把结果喂回去；模型不用工具就说明它说完了，退出。

```
   user query
       │
       ▼
   ┌───────────────────────────────┐
   │  model.respond(messages,tools) │◀────────────┐
   └───────────────┬───────────────┘             │
                   │                              │
            有 tool_call ?                         │
            /          \                          │
          是            否 → 打印文本，结束          │
          │                                       │
   执行工具(shell) → function_call_output 回灌 ─────┘
```

## 工作原理

看 [code.py](code.py)，三步：

**第 1 步** — 把用户问题作为第一条消息。Codex 用的是 OpenAI Responses API 的 input-item 形状（扁平的 item 列表）：

```python
messages = [user_item(query)]   # {"type":"message","role":"user","content": query}
```

**第 2 步** — 连同工具定义发给模型，拿回规范化结果（文本 + 工具调用）：

```python
resp = model.respond(messages, tools=TOOLS, system=SYSTEM)
messages += resp.output_items   # 把模型本回合产出回灌进对话
```

**第 3 步** — 没有工具调用就结束；有就执行，把结果作为独立的 `function_call_output` item 追加回去，继续循环：

```python
if not resp.tool_calls:
    return
for tc in resp.tool_calls:
    output = HANDLERS[tc.name](**tc.arguments)
    messages.append(tool_output_item(tc.call_id, output))
```

工具只有一个：`shell`（跑命令）。在真 Codex 里，这个工具就是 `codex-rs/core` 里的 shell/exec 工具——agent 的主力就是「跑命令」。

**走一遍** — 把抽象的"递纸条"换成真实数据。这正是 `python s01_agent_loop/code.py --demo` 跑的那一轮。你只说一句话，看 `messages` 这个列表怎么一项一项长出来：

1. 你的问题先变成列表里的**第一项**。它不是一个特殊对象，就是一个普通字典——`type` 字段标明它是谁说的：
   ```json
   {"type": "message", "role": "user", "content": "执行 `echo hello from codex` 并告诉我结果"}
   ```

2. 把这个列表（连同工具清单）发给模型。模型读懂了"它要我跑个命令"，于是不回文字，而是回一张**"要动手"的纸条**——一个 `function_call` 项。注意 `arguments` 是一段 JSON **字符串**（模型吐出来的就是文本），`call_id` 是这次调用的回执号：
   ```json
   {"type": "function_call", "call_id": "mock_call_1", "name": "shell",
    "arguments": "{\"command\": \"echo hello from codex\"}"}
   ```
   循环看到 `resp.tool_calls` 非空——这就是"继续"的信号。

3. 循环**替模型执行**：照名字 `shell` 查到 `run_shell`，真的去跑 `echo hello from codex`，拿到输出 `hello from codex`。然后把结果包成第三项追回列表里。关键是用**同一个 `call_id`** 把结果和刚才那张纸条对上号：
   ```json
   {"type": "function_call_output", "call_id": "mock_call_1", "output": "hello from codex"}
   ```

4. 带着这条更长的历史再问一次模型。这次它看到命令已经跑完、结果就在眼前，没什么可做了——于是回一句纯文本、不带任何 `function_call`。`resp.tool_calls` 为空，循环就此**退出**。

看明白了吗？整轮下来，循环自己**一个判断都没做**——"要不要跑命令""跑完了没有"全是模型用"发不发 `function_call`"来表态的。循环只是个忠实的跑腿：查表、执行、把结果按 `call_id` 贴回去。这就是道理三的活样板。

## 生产级：循环不能永远转——封顶 + 可中断

教学版的 `while True` 有两个要命的缺口：它**停不下来**，也**收不住**。生产级的回合循环必须补上两道护栏（本章 [code.py](code.py) 已加上，`--demo` 末尾演示）。

### 一、步数封顶：防失控的工具调用循环

模型可能陷入死循环——反复调同一个失败的工具、或在两个工具间来回跳。没有上限的 `while True` 会**一直烧钱烧时间**，直到撞上 token 上限或你手动杀进程。生产级做法是给回合一个**步数/预算上限**（`for step in range(max_steps)`），撞顶就强制收尾：

```
 (a) 步数封顶 max_steps=3：卡住的模型被截停 ——
> shell {'command': 'echo still going'}   （×3）
[guard] 触及 max_steps=3 上限，强制收尾（防止失控的工具调用循环）
```

### 二、协作式取消：用户喊停，它得真停

一个跑了一半的回合，用户按 Ctrl-C / 点"停止"，它必须**当场干净地停下**——而不是把剩下的工具调用跑完才理你。真 Codex 把"停"做成一等操作 `Op::Interrupt`（[`protocol.rs:450`](../../codex/codex-rs/protocol/src/protocol.rs)），回合在每步之间检查它，命中就发出 `Interrupted`。本章用一个 `cancelled()` 回调把它教学化——每步问一句"该停了吗"：

```
 (b) 协作式取消：用户在第 3 步中断 ——
> shell {'command': 'echo still going'}   （×2）
[interrupted] 用户中断，干净退出本回合
```

> 为什么是"协作式"而非"强杀"：你不能在工具跑到一半时硬砍——那会留下半写的文件、半提交的事务。正确做法是在**安全的检查点**（每步之间）检查取消信号，让正在跑的那一步**先收尾**再退出。这也是为什么 `Op::Interrupt` 是发个信号、而不是 `kill -9`。

## 🆚 与 Claude Code 的不同

这一章两边几乎一样——**这恰恰是重点**：回合循环是共同底座，Agency 在模型里，不在循环里。但有三处苗头已经能看出分野：

| | Claude Code | Codex | 为什么 |
|---|---|---|---|
| 停止信号 | `stop_reason == "tool_use"`（Anthropic Messages API） | 消费 Responses API 的事件流；架构上更进一步把"提交"与"事件"拆成两个队列 | 两家 wire protocol 不同（详见 [s09]）；Codex 的队列化是为多前端 + 流式审批（[s11]） |
| 危险命令 | s01 的 `run_bash` 里有硬编码黑名单（`rm -rf /`、`sudo`…） | **没有黑名单** | Codex 不信任黑名单（命令变体无穷），它的答案是审批（[s04]）+ 内核沙箱（[s05]） |
| 模型的"思考" | `thinking` 块（带 `signature` 签名），在 assistant 消息的 content 里；工具循环要**原样带回** | `reasoning` item（可加密 `encrypted_content`），扁平 item 列表里**独立一项**；靠 `previous_response_id` 跨轮携带 | 各自跟着协议的基本形状（**块** vs **item**）；保护手段也不同：一个**签名**防篡改、一个**加密**防泄露 → 详见下方「深入四」 |

> 一句话：**Claude Code 在应用层"挡"，Codex 在内核层"关"。** 这条主线会贯穿后面每一章。

[s04]: ../s04_approval/
[s05]: ../s05_sandbox/
[s09]: ../s09_responses_api/
[s11]: ../s11_frontends/

## 深入：教学版 vs 真 Codex 源码

教学版 `run_turn` 是个十来行的同步 `while`。真 Codex 的回合引擎在 [`core/src/session/turn.rs`](../../codex/codex-rs/core/src/session/turn.rs)，是异步的、被 SQ/EQ 协议包裹、要处理流式与多前端。下面拆几处差异——*核心循环是同一个，多出来的全是保护与工程化*。

<details>
<summary>一、同步 while vs 异步 Session</summary>

教学版直接 `while True` 调 `model.respond`。真 Codex 的回合跑在 tokio 异步运行时里：`Session`（`core/src/session/`）从**提交队列**取 `Op::UserInput`，`turn.rs` 编排整个回合——注入上下文（AGENTS.md、skills）、流式调用 `ModelClient::stream_responses`、经 `ToolRouter::dispatch_any` 派发工具、再把 `EventMsg` 发回前端。"继续还是停止"不由一个 `return` 决定，而由**事件流**驱动。

</details>

<details>
<summary>二、stop_reason 不是唯一信号</summary>

教学版靠 `resp.tool_calls` 是否为空决定继续。真 Codex 消费的是 Responses API 的**流式 item**：`function_call` item 一边到达、一边可能就开始处理；回合的推进靠对 output items 的增量解析，而不是单看一个 `stop_reason`。（这点和 learn-claude-code 里 CC 用 `needsFollowUp` 而非 `stop_reason` 异曲同工。）

</details>

<details>
<summary>三、一个回合里发生的事，远不止"调模型"</summary>

| 一个真实回合还会做 | 教学版 s01 | 在哪一章补上 |
|---|---|---|
| 注入 AGENTS.md 项目记忆 | 省略 | s06 |
| 命令审批 | 省略 | s04 |
| 内核沙箱 | 省略 | s05 |
| 记录 rollout（可续接） | 省略 | s08 |
| 上下文压缩 | 省略 | s07 |
| 向多个前端广播事件 | 省略 | s11 |

s01 把这些全砍掉，只留最赤裸的循环——后面逐章长回来。

</details>

<details>
<summary>四、模型的"思考"去哪了：reasoning item（Codex）vs thinking 块（CC）</summary>

本章的 `model.respond` 只取了模型回来的**文本**和**工具调用**——但推理模型还会吐出它的"思考过程"。这东西放在哪、怎么跨过"工具调用"这道坎带到下一轮，两家做法截然不同，是 wire protocol 差异的又一处落点。

**Codex（OpenAI Responses API）**：思考是一类**独立的 item**——`ResponseItem::Reasoning { id, summary, content, encrypted_content }`（[`protocol/src/models.rs:772`](../../codex/codex-rs/protocol/src/models.rs)）。它和 `message` / `function_call` **平级**，就摊在那张扁平 item 列表里。Codex 在请求里设 `include = ["reasoning.encrypted_content"]`（[`core/src/client.rs:768`](../../codex/codex-rs/core/src/client.rs)）让服务端把推理**加密**返回，再靠缓存的 Responses 连接 + `previous_response_id`（`client.rs` 顶部注释）把它**跨回合携带**——模型能在一串工具调用之间接着上一段思路想，而你看不到原始 CoT。（教学版 `codexlib.py` 为简化把 reasoning item 丢弃了；真 Codex 会留着并回传。）

**Claude Code（Anthropic Messages API）**：思考是 assistant 消息 **content 数组里的一个 `thinking` 块**（带一段 `signature` 签名；另有 `redacted_thinking`），和 `text` / `tool_use` 块并列。在工具循环里，你必须把这些带签名的 thinking 块**原样回传**，模型才能接着想；Anthropic 会**验签**防篡改。*（CC 侧细节转述自 Anthropic 文档 / learn-claude-code，闭源无法在本仓库独立核验——确定性低于 Codex 侧。）*

**为什么不一样？**

1. **跟着协议的基本形状走**：Anthropic 是"消息里装内容块"，思考自然是一个**块**；Responses 是"扁平 item 列表"，思考自然是**独立 item**。和上面 🆚 表第一行同源——形状决定一切。
2. **保护手段不同**：Anthropic 给思考**签名**（防回传时被篡改），Codex 给思考**加密**（防原始 CoT 泄露）。都要保护推理，一个走完整性、一个走机密性。
3. **无状态重放 vs 有状态引用**：Messages API 无状态，所以每轮把签名思考块**重新发回**；Responses API 能用 `previous_response_id` + 服务端 `store` **引用**上一段，不必每次重发。

一句话：**两家都要把"想到一半的思路"安全地带过工具调用这道坎——Anthropic 靠"签名 + 重放内容块"，Codex 靠"加密 + 引用 item"。** Responses API 的全貌见 [s09]。

</details>

## 运行

```bash
python s01_agent_loop/code.py --demo   # 跑一轮就退出（mock，无需 key）
python s01_agent_loop/code.py          # 交互模式
```

默认 `backend=mock`，离线可跑。想接真模型，在根目录 `.env` 里填 `OPENAI_API_KEY`（详见 [.env.example](../.env.example)）。

## 小结

- 一个循环 + 一个工具 = 一个 agent。
- Codex 把对话表示成 Responses API 的 input-item 列表（`message` / `function_call` / `function_call_output`），这点和 Claude 的 `tool_use`/`tool_result` 块不同；模型的推理则以独立的 `reasoning` item 出现（见深入四）。
- **生产级**：把 `while True` 换成有 `max_steps` 上限的循环（防失控）、每步检查协作式取消信号（真 Codex 的 `Op::Interrupt`，喊停能真停）——循环形状没变，但不再可能无限转、也收得住（见「生产级」一节）。
- 下一站 [s02](../s02_tool_use/)：再加几个工具，你会亲眼看到——**循环一行都不用改**。

## 思考

<div class="think">

1. 如果模型"还没想清楚"就开始流式吐出 `function_call`，你的循环还能只看"有没有 tool_call"吗？该怎么改？
2. 同一个 `run_turn` 要同时喂给终端里的 TUI 和 CI 里的 `codex exec`，你会怎么把"产生什么"和"怎么显示"剥离开？（s11 给了一个答案，但你能先自己想出来吗？）
3. 教学版完全没有危险命令拦截。为什么说"黑名单"是一条死路？Codex 用审批 + 沙箱替代它，又付出了什么代价？
4. 循环每一轮都把全部历史重新发给模型。对话很长时这会怎样？你会在第几轮开始担心？（带着这个问题去看 s07。）

</div>
