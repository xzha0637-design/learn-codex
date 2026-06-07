# s10: SQ/EQ Protocol — 提交队列与事件队列

> *"事件向外流，操作向内流；core 与前端各管各的。"*

[learn-codex 总览](../README.md) · [Responses API](../s09_responses_api/) → **本章** → [前端：TUI + exec](../s11_frontends/)

---

## 先把思想说透：把「该做什么」和「发生了什么」彻底分家，一个脑子才能配多张脸

到这一章为止，我们的循环一直是「调个函数、等它返回」——简单直接，但有个隐藏的硬伤。设想一个再正常不过的需求：agent 正干到一半，模型说「我想跑 `rm -rf build/`」，这时你得**当场拦住它问一句「批不批」**，等你点头才让它真跑。用「调函数等返回」的写法，你会发现这件事**根本做不到**。本章就是从这个做不到出发，一步步逼出 Codex 的架构脊梁。三层道理。

**道理一：函数调用是一根「焊死的直管」——一旦进去就一路冲到底，中途插不进任何人。**
`output = run_shell(cmd)` 这一行，调用的瞬间它就闷头把命令跑完、把结果吐出来，**从「决定要跑」到「真的跑了」之间，没有任何缝隙**。可「审批」这件事，恰恰需要的就是这道缝：在「想跑」和「跑了」中间，停下来、让一个人介入、等他表态。同理还有「跑到一半我想喊停」「它还在忙我想追加一句话」——这些全都要求「在一件事进行的途中插进来」。直管式的函数调用结构上就排斥这种插入，这不是写得不够好，是这种形状本身的死穴。

**道理二：解法是把「我要做什么」和「我这边发生了什么」拆成两股独立的流，中间留出可介入的缝。**
既然一根直管不行，那就把它**剖成来回两股**：一股专门承载「外面想让 core 做的事」——用户敲了句话、用户批了这条命令、用户要打断（这是「该做什么」，向里流）；另一股专门承载「core 这边发生了什么」——回合开始了、我想跑这条命令请批准、命令输出来了、我说了一句话（这是「发生了什么」，向外流）。各自排成一条队，一进一出、互不阻塞。于是关键的那道缝出现了：core 可以在向外那条流上喊一声「我想跑这条、批不批」，然后**就地停住、等着**；而批准与否，会顺着向里那条流，作为一条独立的消息**晚一点**送回来。审批于是不再是「一次函数调用」，而是「一条消息出去 + 一条消息回来」——正因为拆成了两条流上的两张纸条，这个「问」才插得进一个正在进行的回合中间。打断、中途追加输入，靠的也是同一道缝。

**道理三（最关键）：这一拆，顺带把 core 和「界面」彻底解了耦——一个脑子从此能配许多张脸。**
你再回看上面那两股流：向里的一股，core 根本不在乎这条「该做什么」是从谁那儿来的——是终端里敲的、是 CI 脚本喂的、还是 IDE 通过网络发来的，对 core 都一个样；向外的一股，core 也不管这条「发生了什么」最后被谁、被画成什么样子。换句话说，**core 只跟这两条流打交道，从此再不需要知道界面长什么样**。这一下就解锁了 Codex 真正想要的形态：**同一套 core 逻辑，背后接好几张不同的脸**——终端里带颜色的交互界面、CI 里跑完就退的命令行、IDE 插件背后的服务进程，它们提交的是同一种「该做什么」、消费的是同一股「发生了什么」。一个后厨，配多个前厅，共用同一套做菜流程。「界面无关」和「回合中途可介入」这两件大事，其实是同一刀切下去的两面。

把三点连起来：直管插不进人 → 于是剖成「该做什么 / 发生了什么」两条流、缝里能塞进人类决定 → 而这一拆又让 core 与界面解耦、一个脑子配多张脸。这两条流，就是本章标题里的「提交队列 / 事件队列」，也是后面一切的地基。

## 问题

s01 的循环是「调函数、拿返回值」——前端和 core 焊死在一起。可真实的 Codex 要做到：

- 在**一个回合进行到一半**时弹出审批（"要跑 `rm`，批不批？"），等用户回应再继续；
- 随时**打断**（Interrupt）正在跑的回合；
- 让 **TUI、`codex exec`、app-server（IDE 后端）三种前端共用同一套 core 逻辑**。

直接 `return` 一个结果，这些都做不到——你没法在函数返回中途插进一个人类决定。

## 解决方案

把输入和输出各自拆成一条队列：

```
  前端 (TUI / exec / IDE)                     Session (core)
        │                                          │
        │   submit Op  ──────────────▶  Submission Queue (SQ)
        │   (user_input / exec_approval / interrupt)│
        │                                          │  处理 Op、产出 Event
        │   render Event ◀────────────  Event Queue (EQ)
        │   (turn_started / exec_begin / agent_message / ...)
```

core 只关心「消费 Op、产出 Event」，完全不知道前端长什么样；前端只关心「渲染 Event、提交 Op」。中间用两条队列彻底解耦。

教学版只取了三种最有代表性的 Op（前端能提交的动作），先认个脸：

| Op | 什么时候发 | 干什么 |
|---|---|---|
| `user_input` | 你敲完一句话回车 | 开启一个回合 |
| `exec_approval` | core 问"批不批"、你答完 | 把审批决定送回 core |
| `interrupt` | 你按下打断（如 Esc / Ctrl-C） | 喊停正在跑的回合 |

（真 Codex 的 Op 有十几种，这里只够把"问—答—停"这条主线讲清楚；完整清单见下方"深入"。）

**为什么是两条队列，而不是一个函数？** 拿 s01 的写法对照一下就懂了：

```python
# s01 的同步写法：前端和 core 焊死，一行 return 定生死
output = run_shell(cmd)          # core 直接执行、直接返回，中间没有任何缝隙

# s10 的双队列写法：执行被拆成"问一声"和"得到答复"两半
yield  Event("exec_approval_request", command=cmd)   # ← core 把"请批准"丢出去，然后挂起
decision = ...                                        # ← 这里是一道缝：可以塞进一个人类决定
if decision == "approved": run_shell(cmd)            # ← 拿到答复才执行
```

`run_shell(cmd)` 这种写法里，**函数一旦被调用就一路跑到底**，你没有任何机会在"决定要跑"和"真的跑了"之间插进一个人。而双队列把这两件事拆到了两条传送带上：core 在 EQ 上喊"我想跑这条、批不批"，然后**停下来等**；前端慢慢悠悠地问完用户，再从 SQ 把答复送回来。这道"缝"——就是审批、打断、回合中途追加输入这些能力的唯一藏身之处。

> 类比：同步函数像**自动门**，人一靠近就开，关不住；双队列像**门铃 + 对讲机**——按铃（Event 出）、屋里的人决定开不开（Op 回）、再开门（执行）。多了一道人能介入的环节。

## 工作原理

看 [code.py](code.py)。本章用 Python 生成器把两条队列讲清楚——**`yield` 就是 EQ（事件流出），`.send()` 就是 SQ（决定流入）**：

```python
def run_session(messages):
    yield ev("turn_started")
    ...
    for tc in resp.tool_calls:
        decision = yield ev("exec_approval_request", command=command)  # 事件出 → 决定入
        if decision == "approved":
            yield ev("exec_begin", command=command)
            output = run_shell(command)
            yield ev("exec_end", output=output)
```

前端 `drive()` 消费事件；遇到 `exec_approval_request` 就构造一条 `Op("exec_approval")` 把决定 `send` 回去。`Op` / `Event` 两个 dataclass 对应真源码 `../../codex/codex-rs/protocol/src/protocol.rs` 里的 `Op` 与 `EventMsg` 两套枚举（这里只取最小子集）。

**走一遍。** 我们跟着 `--demo` 里那一个回合，看两条传送带上**每张纸条到底长什么样**、以及**为什么**要这样传。用户的话是「执行 `echo SQ/EQ works`」。

1. 前端把用户输入做成一张 **Op 纸条**，丢进 SQ（向里）：
   ```json
   { "op": "user_input", "text": "执行 `echo SQ/EQ works`" }
   ```
   core 收到后开始一个回合。**为什么是纸条而不是函数参数？** 因为前端和 core 可能根本不在同一个线程/进程里（真 Codex 里它们是两个 tokio 任务），只能靠传纸条沟通。

2. core 先往 EQ（向外）丢一张 **Event**，告诉前端"开张了"：
   ```json
   { "event": "turn_started" }
   ```
   前端收到就可以画个"思考中…"的提示。

3. 模型决定要跑 `echo SQ/EQ works`。注意：**core 不会自己偷偷跑**，而是先停下来，往 EQ 丢一张"请批准"的 Event：
   ```json
   { "event": "exec_approval_request", "command": "echo SQ/EQ works" }
   ```
   在代码里，这一步就是那行 `decision = yield ev("exec_approval_request", ...)`——`yield` 把事件**送出去**，然后**就地挂起**，等一个决定送回来。**为什么要挂起？** 这正是双队列的意义：回合卡在半路，等一个人类决定，而不是闷头执行。

4. 前端看到这张"请批准"的 Event，问用户（demo 里自动答 approved），把决定做成一张 **Op 纸条**送回 SQ（向里）：
   ```json
   { "op": "exec_approval", "decision": "approved" }
   ```
   代码里就是 `.send("approved")`——它变成第 3 步那个 `yield` 的返回值 `decision`，回合从挂起处**原地复活**。

5. 拿到 `approved`，core 才真正执行，并把过程拆成两张 Event 丢回 EQ：
   ```json
   { "event": "exec_begin", "command": "echo SQ/EQ works" }
   { "event": "exec_end",   "output": "SQ/EQ works\n" }
   ```
   前端据此先显示"正在跑这条命令"、再显示输出。

把这一趟连起来看：**Op 从前端流向 core（user_input、exec_approval），Event 从 core 流向前端（turn_started、exec_approval_request、exec_begin、exec_end）**——一进一出，泾渭分明。而"在第 3 步挂起、第 4 步靠一张外部纸条复活"这件事，就是为什么 Codex 能"在模型流式输出的同时，插进一个人类的审批决定"——这是普通的"调函数拿返回值"做不到的。

## 生产级：队列要扛得住"产得比消费快"

把 core 和前端用两条队列（SQ 提交 / EQ 事件）解耦，最大的好处是异步；但异步立刻带来三个生产级问题：

- **背压（backpressure）**：模型流式吐 event 可能比一个慢前端（卡顿的 IDE、被重定向到磁盘的 `codex exec`）消费得快。若事件队列**无界**，积压会一路吃内存直到 OOM。生产级要么用**有界队列**（满了就让生产侧等一等，把"快"压下来），要么明确丢弃可丢的中间事件——而不能假装下游永远跟得上。
- **顺序保证**：同一回合的 `reasoning → function_call → output → completed` 必须**按序**到达前端，否则 UI 会渲染出错乱的因果。事件流的顺序是协议的一部分（[`protocol.rs`](../../codex/codex-rs/protocol/src/protocol.rs) 的 `EventMsg`），不是"尽力而为"。
- **中断要插得进队**：用户 `Op::Interrupt`（[s01](../s01_agent_loop/) 提过）得能**及时**穿过提交队列、打断正在跑的回合——这要求队列别被前面的活堵死到中断都递不进去。

> 一句话：双队列给了你解耦，但你得替它回答"下游慢了怎么办"。**有界 + 有序 + 可中断**，才是一条能上生产的事件流，而不只是 demo 里的一个 `yield`。

## 🆚 与 Claude Code 的不同

| | Claude Code | Codex |
|---|---|---|
| 循环形态 | 较直接的「请求→响应」循环 | **SQ/EQ 双队列**，core 与前端解耦 |
| 审批 | 同步弹窗，挡在执行前 | 审批是一条 `Event` 出、一条 `Op` 回，**异步插进流式回合** |
| 前端数量 | 单一 CLI/TUI | TUI + `codex exec` + app-server **共用同一个 core** |
| 打断/中途输入 | 受限 | 随时提交 `Interrupt` / 追加输入的 `Op` |

**为什么？** Codex 设想的运行形态是「**一个 core，多个前端**」：终端里是 TUI、CI 里是 `codex exec`、IDE 里是 app-server，它们必须复用同一套回合逻辑。要让它们都能「在模型流式输出的同时插入人类决定（审批/打断）」，就不能用「函数调用返回值」这种同步耦合，只能把输入输出拆成两条队列。Claude Code 更偏单一交互式前端，循环可以写得更直接。

## 深入：教学版 vs 真 Codex 源码

真协议在 [`protocol/src/protocol.rs`](../../codex/codex-rs/protocol/src/protocol.rs)，`Op` 与 `EventMsg` 都是很大的枚举。教学版各取了三四个变体。

<details>
<summary>一、Op 远不止 3 种</summary>

真 `Op` 包含 `UserInput`、`Interrupt`、`ExecApproval`、`PatchApproval`、`Compact`、`ThreadRollback`、`Review`、`RunUserShellCommand`、`ReloadUserConfig`、`RefreshMcpServers`、`ResolveElicitation`…… 每一种都是前端能提交的一类动作。教学版只取了 `user_input / exec_approval / interrupt`。

</details>

<details>
<summary>二、EventMsg 是细粒度的</summary>

真 `EventMsg` 有 `TurnStarted/TurnComplete`、`AgentMessage`、`AgentReasoning`、`ExecCommandBegin` / `ExecCommandOutputDelta` / `ExecCommandEnd`、`ApplyPatchApprovalRequest`、`PatchApplyBegin/End`、`McpToolCallBegin/End`…… 尤其 `OutputDelta` 让前端能**流式**显示命令输出，而教学版把这些合并/省略了。

</details>

<details>
<summary>三、生成器只是教具，真实是异步 channel</summary>

教学版用 Python 生成器的 `yield`/`send` 模拟两条队列——这是**协作式**的：只有 Session 主动让出控制权，前端才能插话。真 Codex 用 tokio 的 `mpsc` channel：Session 在一个任务里跑、前端在另一个任务里收发，因此能**真正异步**地在回合进行中打断或审批。

</details>

<details>
<summary>四、同一套 Event 喂三个前端</summary>

`TUI`、`codex exec`（s11）、`app-server`（IDE 后端）都是同一条 `EventMsg` 流的消费者；`Submission` 也可来自不同来源（键盘 / stdin / WebSocket）。一套 core，多个皮。

</details>

## 运行

```bash
python s10_sq_eq_protocol/code.py --demo   # 看事件流出(EQ) + 审批流入(SQ)
python s10_sq_eq_protocol/code.py          # 交互模式：每条命令都问你批不批
```

`--demo` 完全离线（不调真模型），跑完上面"走一遍"里那一整趟。你会看到事件一条条**流出**（turn_started → exec_approval_request → exec_begin → exec_end），中间夹着一条审批决定**流回**。把它对照"走一遍"的五步读，双队列的进出方向就具象了。交互模式则把第 4 步的"自动 approved"换成真的问你 `y/N`——你就是那个站在缝里做决定的人。

## 小结

- 把「提交（Op）」和「事件（Event）」拆成两条队列，core 与前端彻底解耦。
- 这层解耦正是「回合中审批 / 打断 / 多前端」的前提——也是 Codex 的架构脊梁。
- **生产级**：异步队列要扛住"产得比消费快"——背压（有界队列防 OOM）、严格的事件顺序、中断能及时插进队（见「生产级」一节）。
- 下一站 [s11 前端：TUI + exec](../s11_frontends/)：把这套 core 接上真正的前厅——同一条 Event 流，喂给 TUI 和 `codex exec` 两个皮。

## 思考

<div class="think">

1. 生成器的 `yield/send` 是协作式的——只有 Session 让出控制权，前端才能插话。真异步 channel 允许前端**随时**提交 `Interrupt`。在"打断一条卡死的命令"这件事上，两者表现差在哪？
2. 三个前端同时连到一个 Session、各自提交 `Op`，事件该广播给谁？冲突如何收敛？
3. 把"审批"建模成一条 Event + 一条 Op（而不是一个同步函数调用），能让审批 UI 长成什么样子？（想想：把审批推到你手机上点"同意"。）
4. Claude Code 不用这么重的队列协议也能工作。它因此牺牲了什么、又换来了什么？

</div>
