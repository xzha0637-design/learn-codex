# 超长篇：Claude Code 与 Codex 的「子代理与多智能体」全解

> 接着[上下文那篇](context-cc-vs-codex.md)，这篇拆另一个大主题：当**一个 agent 不够用**时，两套系统怎么"分身"（subagent）和"组队"（multi-agent）——以及为什么 CC 走向"文件系统里的队友团"，Codex 走向"有身份、有图谱、能上云、能被调用的线程网络"。
>
> 事实依据：Codex 来自真源码 `../../codex/codex-rs`；CC 来自 [learn-claude-code](../../learn-claude-code/) 对 `AgentTool.tsx` / `runAgent.ts` / `forkSubagent.ts` / 团队章节的剖析。
>
> ⚠️ **关于论据强度**：Codex 侧每个机制都能对到 `codex-rs` 的文件/行号；CC 侧的精确数字（如「15 种消息类型」「轮询 Lead 1s / 队友 500ms」「保头几条」）转述自 learn-claude-code 对**闭源** CC 的剖析，**无法在本仓库独立核验**——请按二手材料对待，确定性低于 Codex 侧。

[← 返回 learn-codex 总览](../README.md)

---

> 🛠 **想动手？** 本篇的可跑骨架是章节 [s18 多智能体](../s18_multiagent/)（`python s18_multiagent/code.py --demo`，离线）——一个最小的带内 `AgentMessage` + router + 共享 rollout demo，把下面 ★ 那节讲的「带内通信」跑给你看。

## 0. 给初学者：为什么一个 agent 不够

先分清两个**不同**的需求，它们常被混为一谈：

1. **子任务隔离（subagent / 分身）**：主 agent 在修 bug，需要先"读 30 个文件搞清调用链"。如果在主对话里读，这 30 个文件的内容会把上下文撑爆、还让它忘了本来要干嘛。解决办法：**开个分身**，给它一张干净的白纸去查，查完**只把结论**带回来，中间过程全扔。就像你修 bug 时"新开一个终端"查资料，查完关掉、只把结论记进笔记。

2. **多智能体协作（multi-agent / 组队）**：一个大项目，前端、后端、测试可以**并行**推进。这时你想要的不是"分身查个资料"，而是"几个 agent 各司其职、互相通信、协调进度"——一个**团队**。

这两件事，CC 和 Codex 都做了，但做法差异极大。我们先看"分身"，再看"组队"。

---

## 1. 分身（Subagent）：把脏活关进一张白纸

### Claude Code：`task` 工具 + 全新上下文

CC 的分身是一个叫 `task` 的工具（真源码 `AgentTool.tsx` / `runAgent.ts`）。主 agent 像调用任何工具一样调它，传一句任务描述；harness 就 **spawn 一个子 agent**：

- 给它一份**全新的 `messages[]`**（只有那句任务描述），跑自己的循环；
- 子 agent 干完，**只把最后的文本结论**回传给主 agent——中间读了什么、聊了多少轮，全部丢弃；
- 但**文件系统的副作用保留**（它写的文件、改的代码还在）。

三个关键设计（learn-cc s06）：

| 决策 | 选择 | 为什么 |
|---|---|---|
| 上下文隔离 | 全新 `messages[]` | 子 agent 的中间过程不污染主对话 |
| 只回传结论 | 取最后一条文本 | 不回传整个历史 |
| 禁止递归 | 子 agent 没有 `task` 工具 | 防止"分身再开分身"无限套娃 |
| **上下文隔离 ≠ 权限隔离** | 子 agent 的工具调用**仍走** PreToolUse hook | 隔离的是注意力，不是安全策略 |

**进阶（真实 CC 有三种模式）**：

| 模式 | 上下文 | 目的 |
|---|---|---|
| Normal Subagent | 全新 messages[] | 纯隔离 |
| **Fork Subagent** | `buildForkedMessages()` 构造**缓存友好的前缀** | **共享 prompt cache** |
| General-Purpose | 同 Normal | 通用 |

Fork 模式（`forkSubagent.ts`）是教学版没讲的精髓：它**不**为隔离而创建全新上下文，而是让子 agent 的 system prompt、tools、model、messages 前缀、thinking 配置和父 agent **字节级一致**，这样 Anthropic API 的 prompt cache 能命中、不必重算——**省钱省延迟**。还有 `permissionMode: 'bubble'`：子 agent 的权限弹窗**冒泡到父终端**，你在主终端里替它审批。

### Codex：派生一个**线程**，记进**agent 图谱**，还带**身份**

Codex 的分身没有做成一个简单的 `task` 工具，而是更"基础设施化"。证据散落在几个 crate 里：

- [`agent-graph-store/`](../../codex/codex-rs/agent-graph-store/)：注释直说它是 *"Storage-neutral parent/child topology for thread-spawned agents"*——一个存**父/子 agent 拓扑**的存储，带 `ThreadSpawnEdgeStatus`。也就是说，Codex 把"谁派生了谁"建成一张**可持久化、可查询的图**，而不只是一次性的函数调用。
- [`agent-identity/`](../../codex/codex-rs/agent-identity/)：用 **ed25519 签名 + curve25519** 给 agent 一个**加密身份**。子 agent 不是匿名分身，而是有可验证身份的实体。
- 派生出的子 agent 本身是一个 **thread**（`thread-store` / `thread-manager`），和主线程一样有完整的 rollout（见[上下文篇](context-cc-vs-codex.md)第 7 节）——可持久化、可恢复、可审计。

对比一下就很清楚：

| | Claude Code | Codex |
|---|---|---|
| 分身是什么 | 一次 `task` 工具调用，跑完即弃 | 一个被派生的 **thread**，进入 **agent 图谱** |
| 拓扑 | 父子关系隐式（chainId、depth+1） | 显式持久化的 **parent/child 图** |
| 身份 | 无（匿名子 agent） | **加密身份**（可签名验证） |
| 缓存优化 | Fork 模式共享 prompt cache | 走 Responses 服务端状态（见上下文篇） |
| 回传 | 只回传文本结论 | 子线程完整 rollout 可被父/系统访问 |

**为什么 Codex 这么"重"？** 因为它要支持云端、可审计、可恢复、可互操作的场景：当一群 agent 在云上跑、要追责、要重放时，"谁派生了谁、各自身份是什么、各自完整历史在哪"就不能是内存里一次性的东西，得是持久化的图 + 身份 + rollout。CC 的 `task` 则把分身做成轻量的本地工具调用，契合"本地、交互、当场用完"的体感。

---

## 2. 一个特别的子代理：Codex 的"审批官"本身就是个 subagent

这是 Codex 一个很能体现其气质的设计。回忆[审批与 Guardian](../README.md)那两章——Codex 的审批可以**路由给谁**？看 `protocol/src/config_types.rs` 里的 `ApprovalsReviewer`：

```
"user" | "auto_review" | "guardian_subagent"
```

其中 `auto_review`（旧名就叫 `guardian_subagent`）的官方描述是：

> *"uses a carefully prompted **subagent** to gather relevant context and apply a risk-based decision framework before approving or denying the request."*

也就是说：**当一个危险操作需要审批、而现场没有人类时，Codex 会派一个专门的"审批官"子代理**去搜集上下文、按风险框架决定批不批。配合 `Op::Review` / `EnteredReviewMode` / `ReviewDecision`，Codex 还有一个专门的 **Review 模式**（典型用途：派一个 reviewer 子代理去审一段 diff）。

**这正是 Codex 的灵魂在多智能体层面的延续**：Claude Code 倾向"让人来把关"，Codex 倾向"没人时，派个 AI 把关"——连"分身"最典型的用途，都是为了**安全审查**。CC 的子代理更多是"帮我干活的分身"，Codex 的标志性子代理是"替我把关的审查员"。

---

## 3. 组队（Multi-Agent）：从"分身"到"团队"

分身是"主仆"（父派子、子回传）。当任务需要**多个对等 agent 并行协作**时，就需要团队基础设施。这里两套系统分道扬镳得最彻底。

### Claude Code：文件系统就是消息总线

learn-cc 的 s15–s17 揭示了一个朴素而强大的设计——**没有中央消息总线，协调全靠文件系统**：

- **MessageBus = 文件收件箱**（s15）：每个 agent 有一个目录当"收件箱"，发消息就是往对方目录写一个文件，收消息就是读自己目录。无需任何中间件，文件系统天然是持久的、可观察的、跨进程的。
- **15 种消息类型**：请求、响应、状态更新、认领、交接…… 形成一套通信词汇表。
- **协议状态机**（s16）：`ProtocolState` 跟踪请求状态，四步协议流程，`dispatch_message` 按类型路由，`match_response` 做类型校验——让"队友间的对话"有章法、不串台。
- **Lead + 队友 + 自治认领**（s17）：有一个 Lead（领队）和若干队友；队友进入 **WORK → IDLE → SHUTDOWN** 生命周期，**空闲时轮询一个"任务看板"，自己认领未被认领的任务**（`scan_unclaimed_tasks` + `claim_task` 的 owner 检查），干完把 summary 发回 Lead。队友 idle 时**等待而非退出**，并周期性**重注入自己的身份**以防上下文漂移。
- **权限冒泡**：队友的危险操作冒泡到 Lead/主终端审批。

一句话：**CC 把多智能体做成"一群跑在你机器上的队友，靠共享文件系统的收件箱和一套协议，自治地认领并协作完成看板上的任务"。** 极其 local-first、去中心、可观察（你能直接 `cat` 它们的收件箱看它们在聊什么）。

### Codex：有身份的线程网络 + 云 + 互操作 + 可被调用

Codex 没有把团队做成"文件收件箱里的队友"，而是做成一套更结构化、更面向云与互操作的基础设施：

- **agent 图谱 + 身份**（第 1 节）：多 agent 天然是图里有身份的节点，父子/派生关系被持久化追踪。
- **协作模式（Collaboration Mode）**：`config_types.rs` 里有 `CollaborationMode`（带 model + effort 设置）、`collaboration-mode-templates` crate、"TUI 启动时的初始协作模式"。即 Codex 把"如何协作"抽象成可配置、可切换的**模式**。
- **AgentMessage**：`protocol/src/models.rs` 有 `AgentMessage { content: Vec<AgentMessageInputContent> }`——agent 间消息是协议里的一等内容类型，而非借道文件。
- **cloud-tasks**（`cloud-tasks/` `cloud-tasks-client/`）：把 agent 任务**派到云上**跑——这是 CC 的本地文件收件箱模型给不了的。Codex Web 就建在这之上。
- **external-agent-sessions**（`external-agent-sessions/` `external-agent-migration/`）：能**解析并导入"外部 agent"的会话历史**（`detect_recent_sessions` / `load_session_for_import`，基于 `RolloutItem`）——即把别的 agent（甚至别的工具）的历史接过来继续。**互操作**是一等公民。
- **Codex 即 MCP 服务端**（见 MCP 那章）：Codex 能**作为一个工具被别的 agent 调用**（`mcp-server` 暴露一个 `codex` 工具，跑一整个任务）。在多智能体世界里，这意味着 **Codex 本身就是别人团队里的一个可调用成员**。

一句话：**Codex 把多智能体做成"有身份、有图谱、能上云、能互相导入历史、还能被别的 agent 当工具调用的线程网络"。** 它不假设大家都在同一台机器、同一个文件系统上。

---

## ★ 重点：agent 之间到底怎么通信

组队的核心是**通信**。两套系统在"一个 agent 怎么把话传给另一个 agent"上做了截然不同的选择：**CC 把通信放在文件系统里（带外），Codex 把通信放进协议本身（带内）。** 这是整篇最值得细看的一节。

### Claude Code：文件收件箱 + 轮询（带外通信）

CC 的 agent 通信完全建立在文件系统上——**模型协议本身根本不知道"队友"存在**，通信是 harness 在旁边搭的一根管子。

- **收件箱 = 一个文件**：每个 agent 有一个 mailbox（真实路径 `~/.claude/teams/{team}/inboxes/{agent}.json`）。发消息 = 往对方文件 append 一条 JSON；用 `proper-lockfile` 文件锁防并发写（最多重试 10 次）。读是**消费式**的：读完即删。
- **轮询，而非推送**：Lead 用 `useInboxPoller` **每 1 秒**扫一次收件箱，有消息就作为新一轮 turn 提交给模型；队友用 `useSwarmPermissionPoller` **每 500ms** 轮询审批回复。没有长连接，就是定时读文件。
- **15 种结构化消息类型**：通信不是自由文本，而是一套词汇表——

  | 类型 | 方向 | 用途 |
  |---|---|---|
  | `plain text` | 双向 | 普通通信（包进 `<teammate-message>` 交给模型） |
  | `idle_notification` | 队友→Lead | 我这轮干完了，进入空闲 |
  | `permission_request` / `_response` | 双向 | 操作审批的请求/回复 |
  | `plan_approval_request` / `_response` | 双向 | 计划审批 |
  | `shutdown_request` / `_approved` / `_rejected` | 双向 | 体面关机握手 |
  | `task_assignment` | Lead→队友 | 派活 |
  | `team_permission_update` / `mode_set_request` | Lead→队友 | 广播/修改权限 |
  | `sandbox_permission_*` | 双向 | 网络权限请求/回复 |
  | `teammate_terminated` | 系统 | 队友被移除通知 |

- **怎么进入模型视野**：文本消息被包进 `<teammate-message>` XML 标签，注入收件方下一轮的上下文。对模型而言，"队友说了什么"=它 context 里多出来的一段标记文本。
- **团队注册表**：`~/.claude/teams/{team}/config.json` 记 Lead、成员（名字、类型、颜色、是否活跃）。
- **气质**：去中心（没有中央 broker，大家互写文件）、可观察（你能 `cat` 收件箱围观它们聊天）、语言无关——但**假设共享同一个文件系统**（同一台机器）。

### Codex：协议内的 AgentMessage（带内通信）

Codex 把 agent 通信**做进了协议本身**——它不是旁边的文件管子，而是和"用户消息""工具调用"平起平坐的一类协议条目。

- **提交侧**：一等 `Op::InterAgentCommunication { communication }`（[`protocol.rs:499`](../../codex/codex-rs/protocol/src/protocol.rs)）。注释明说它"应被记录为 assistant 历史"——一条 agent 间通信会**进入线程的持久记录 rollout**（见[上下文篇](context-cc-vs-codex.md)）。
- **历史侧**：`ResponseItem::AgentMessage { author, recipient, content }`（`models.rs`）——agent 消息是 Responses 协议里的一种 **ResponseItem**，和 `Message`/`Reasoning`/`FunctionCall` 并列，自带 `author`（谁发的）和 `recipient`（发给谁）。
- **寻址用 AgentPath，支持多收件人**：`InterAgentCommunication` 带 `recipient: AgentPath` + `other_recipients: Vec<AgentPath>`（`protocol.rs:626`）——像邮件的 **To + Cc**，一条消息可发给多个 agent。`AgentPath` 是路径式寻址，呼应第 1 节的 agent 图谱。
- **内容可加密**：`AgentMessageInputContent::EncryptedContent { encrypted_content }`——agent 间消息可以是**加密**的（呼应 agent-identity 的签名身份）。多租户/不可信环境里，A 发给 B 的话中间人看不到。
- **带"阶段"语义**：`MessagePhase`（`Commentary` / `FinalAnswer`）区分"中途碎碎念"和"最终答复"，让接收方知道这是过程还是结论。
- **气质**：带内（通信即历史的一部分，天然可审计、可重放）、不假设共享文件系统（可跨机/上云）、可加密 + 身份绑定、多收件人寻址。

### 一张表看懂"通信"的分野

| 维度 | Claude Code | Codex |
|---|---|---|
| 通信位置 | **带外**：文件系统收件箱 | **带内**：协议里的 `AgentMessage` 条目 |
| 传输 | 写对方的 `.json` mailbox | `Op::InterAgentCommunication` 提交 |
| 投递 | **轮询**（Lead 1s / 队友 500ms） | 协议事件流，随回合流动 |
| 寻址 | 文件名 = agent 名 | `AgentPath` + 多收件人（To/Cc） |
| 并发安全 | `proper-lockfile` 文件锁 | 协议/线程层处理 |
| 消息词汇 | 15 种结构化类型 | 通信条目 + phase（commentary/final） |
| 安全 | 靠文件权限 | **内容可加密 + 加密身份** |
| 持久化 | mailbox 文件（读完即删） | **进 rollout 持久记录** |
| 可观察性 | `cat` 收件箱即可围观 | 在 rollout / 事件流里审计 |
| 跨机器 | 否（假设同一文件系统） | **是**（协议消息，可上云） |
| 模型如何看见 | 注入 `<teammate-message>` 文本 | 本就是历史里的 `AgentMessage` 条目 |

### 为什么一个"带外文件"、一个"带内协议"？

- **CC 选文件收件箱**：因为它 local-first。一台机器上，文件系统是现成、可靠、可观察的 IPC——你甚至能直接 `cat` 看 agent 们在说什么，调试体验极好；轮询简单到不会出错。代价是**假设共享一个文件系统**，难跨机器。通信被有意做成"harness 层的管子"，模型协议保持干净。
- **Codex 选协议内消息**：因为它为云端/分布式/可审计而生。当 agent 可能在不同机器、甚至不同信任域里时，"写对方的文件"根本不成立——必须是**可路由、可加密、带身份、且天然进入持久记录**的协议消息。代价是更重、更"黑盒"（不能简单 `cat`，得去 rollout/事件流里看）。

**一句话**：CC 把 agent 通信当成**共享文件系统上的邮件**，Codex 把它当成**协议里可加密、可路由、可审计的一等消息**。前者是 Unix 管道的智慧，后者是分布式系统的思路——又一次，场景（本地 vs 云）决定了机制。

---

## 4. 横切对比：递归、权限、同步/异步、本地/云

| 维度 | Claude Code | Codex |
|---|---|---|
| 分身载体 | `task` 工具，跑完即弃 | 派生 thread，进 agent 图谱 |
| 分身模式 | Normal / **Fork（共享缓存）** / General | thread + 身份 + rollout |
| 标志性子代理 | "帮我干活"的通用分身 | **"替我把关"的审查员（auto_review）** |
| 递归防护 | 子 agent 无 `task`；`isInForkChild()` 查 tag | 图谱拓扑天然记录深度/边状态 |
| 权限 | 隔离上下文但**不隔离权限**；冒泡到父终端审批 | 子代理同样受审批/沙箱约束；可路由给 reviewer 子代理 |
| 同步/异步 | 同步等待，或 `run_in_background` 异步 + 通知 | 线程/云任务天然异步、可持久 |
| 团队协调 | **文件系统收件箱** + 15 种消息 + 协议 + 自治看板 | 协议内 `AgentMessage` + 协作模式 + cloud-tasks |
| 边界 | 同机、同文件系统、local-first | 跨机、上云、可导入外部会话、可被调用 |
| 可观察性 | `cat` 收件箱即可围观 | rollout + agent 图谱 + 身份，偏审计 |

---

## 5. 为什么不同？把账算到底

> **CC 把多智能体做成"你机器上一支用文件收件箱协调的自治队友团"；Codex 做成"一张有身份、能上云、能互操作、能被调用的线程网络"。**

根因还是那三层（和[上下文篇](context-cc-vs-codex.md)同源）：

1. **场景**：CC 面向本地交互——一个人一台机，于是"文件系统当总线、队友自治认领看板、权限冒泡到你终端"既简单又契合，你还能亲眼围观。Codex 面向云端/无人值守/可审计——于是"持久化 agent 图谱 + 加密身份 + cloud-tasks + rollout"是地基。
2. **互操作野心**：Codex 的 `external-agent-sessions` 和 MCP-server 表明它想**和别的 agent 互通、并成为别人系统里的一块积木**；CC 的团队更像"自家队友闭环协作"。
3. **安全气质的延续**：Codex 连"分身"最标志性的用法都是**审查员子代理（auto_review）**——把"没人时也要有人把关"贯彻到多智能体层；CC 则把人留在审批回路里（权限冒泡到你终端）。

两套都自洽。一个把多智能体当**本地协作模式**，一个把它当**分布式基础设施**。

---

## 6. 思考

<div class="think">

1. CC 用"文件系统当消息总线"——朴素但你能 `cat` 围观。Codex 用协议内 `AgentMessage` + agent 图谱——结构化但更"黑盒"。在调试一个 5-agent 卡死的团队时，你更想要哪种？
2. Codex 给 agent 加**加密身份**。在什么场景下，"这条改动是哪个 agent、由谁授权做的"必须能被密码学证明？（提示：想想合规、多租户、供应链。）
3. "审批官也是个子代理"（auto_review）——让一个 AI 去审另一个 AI 要不要执行危险命令。这个 reviewer 会不会被同样的方式骗过？该给它什么它不该有的"超能力"才靠谱？
4. CC 的队友"空闲时自己去看板认领任务"。如果两个队友同时认领同一个任务会怎样？`claim_task` 的 owner 检查解决了竞态——可如果是跨机器（像 Codex 云端），文件锁还够用吗？
5. Codex 能把任务派到云上、还能导入别的 agent 的会话。当"我的 agent"和"云上的 agent"和"别人家的 agent"边界模糊后，"上下文"和"身份"哪个会先成为瓶颈？

</div>

---

[← 返回 learn-codex 总览](../README.md) · 姊妹篇：[上下文处理全解](context-cc-vs-codex.md) · 相关章节：子代理与团队、审批与 Guardian、MCP
