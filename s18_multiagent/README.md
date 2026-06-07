# s18: Multi-Agent — 当一个 agent 不够用：把「通信」做进协议里

> 🌐 [English](README.en.md) · **中文**

> *"组队的难点不是分身，是说话。把通信做进协议，它就成了可审计、可加密、可跨机的历史。"*

[learn-codex 总览](../README.md) · [s17 综合：迷你 Codex](../s17_comprehensive/) → **s18 多智能体（进阶专题）** · [回到总览 ↺](../README.md)

---

## 先把思想说透：组队真正难的那一步，是「说话」

前 17 章你造的都是**一个** agent。但真实世界里，一个常常不够用——前端、后端、测试可以并行，一个 reviewer 可以替你把关。于是有了「多个 agent」。多 agent 难在哪？不在"造出第二个"，而在"它们怎么说上话"。想通下面三个道理，这一章就通了。

**道理一：先分清两件事——「分身」和「组队」，它们常被混为一谈。**
- **分身（subagent）**：主 agent 修 bug 时，需要先"读 30 个文件搞清调用链"。直接在主对话里读，会把上下文撑爆、还让它忘了本来要干嘛。办法是**开个分身**：给它一张干净白纸去查，查完**只把结论带回来**，中间过程全扔。就像你修 bug 时新开一个终端查资料，查完关掉、只把结论记进笔记。
- **组队（multi-agent）**：几个**对等**的 agent 各司其职、并行推进、互相通信——一个**团队**。

分身是"主仆"（父派子、子回传），组队是"同事"（互相喊话）。本章重点是组队里最难、也最能体现两家分野的一环：**通信**。

**道理二（最关键）：「把通信放在哪」决定了整个系统的性格。**
两个 agent 要说话，有两种放法：
- **放在 agent 外面**：发消息 = 往对方的"收件箱"写一个文件，收消息 = 读自己的目录。像往邻居门缝塞纸条——简单、你随时能掀开看，但前提是**大家得住同一栋楼**（同一台机器、同一个文件系统）。这是 Claude Code 的路。
- **放进协议里面**：一条"agent 给 agent 的消息"和"用户说的话""工具调用"**平起平坐**，本身就是对话协议里的一类条目。这是 Codex 的路。

听起来只是实现细节，其实是分水岭——**位置决定了它能不能加密、能不能跨机器、留不留痕、能不能一次发给好几个人。**

**道理三：当「通信即历史」时，通信就从一根旁路小管子，升级成了一等基础设施。**
Codex 选"放进协议"，于是一件神奇的事发生了：A 对 B 说的每句话，**自动落进 rollout**（[s08](../s08_rollout/) 那本帐）。"谁、在什么时候、对谁、说了什么、是过程还是结论"——全都可重放、可追责。再给消息配上**身份**（谁签发的，密码学可验证）、**多收件人**（像邮件的 To/Cc）、**可加密**（中间人看不到明文），通信本身就成了能上云、能审计、能在不可信环境里跑的**分布式基础设施**。Codex 这套看着比"塞纸条"重得多，正是因为它冲着**云端、多租户、可审计**去的——而 CC 的"文件收件箱"冲着**本地、能 `cat` 围观、调试爽**去的。又一次：场景决定机制。

> 这一章我们就亲手搭一个最小的"带内通信"骨架：一条 `AgentMessage` + 一个会**投递并留痕**的 router，让 Lead、Worker、Reviewer 三个 agent 协作一遍。

## 问题

你想让一个 **Lead** 派一个 **Worker** 去改文件，再派一个 **Reviewer** 审一下改动。三个 agent 得互相通信。

如果靠"在主对话里塞一段文本"传话，立刻一团乱：这句是**谁**发的？发给**谁**？是随口的**过程**还是拍板的**结论**？事后**留底**了吗？要是 Worker 在另一台机器上跑，这套还成立吗？

把"agent 之间怎么传话"做成一个**有寻址、有阶段、能留痕**的一等机制——这就是本章。

## 解决方案

两块新零件（其余都搬运自 [s01](../s01_agent_loop/) 的回合循环——**每个 agent 内部仍然是一个 s01 loop**）：

1. **`AgentMessage`**：带内消息类型，带 `author`（谁）/ `recipient`（发给谁，To）/ `cc`（抄送，多收件人）/ `content` / `phase`（commentary 过程 or final_answer 结论）/ `encrypted`（可加密）。
2. **`InterAgentRouter`**：一次 `submit` 做两件事——**(a)** 把消息记进**共享 rollout**（带内的精髓：通信即历史）；**(b)** 按 `AgentPath` 投递给 recipient + 每个 cc。

```
        Lead ──派生──▶ Worker（lead/worker）        每条消息都：
         │                  │                       ① 进共享 rollout（留痕）
         │  ✉ task          │                       ② 投递到收件箱（To + Cc）
         └─────────────────▶┤
                            （内部跑一个 s01 回合，真把文件改了）
         ┌──── ✉ 完成(final) ┘
         │
        Lead ──派生──▶ Reviewer（lead/reviewer）
         │  ✉ 审一下(To=reviewer, Cc=worker)
         └─────────────────▶ Reviewer ──✉ APPROVE/REJECT(final)──▶ Lead
```

## 工作原理

看 [code.py](code.py)：`AgentMessage`、`InterAgentRouter.submit`（对应真源码的 `Op::InterAgentCommunication`）、三个 `Agent`（`Worker` 内部调模型 + shell 工具真干活，`Reviewer` 像 [s14](../s14_guardian/) 那样用规则模拟 auto_review 的判断）。

**走一遍** —— 这正是 `python s18_multiagent/code.py --demo` 跑出来的那一轮。盯住两件事：**消息的 To/Cc/phase**，和**它怎么一条条进 rollout**。

**① Lead 派生 Worker、下发任务。** 派生先在 **agent 图谱**记一条父/子边；任务作为一条 `commentary` 消息投出：

```
  ⑂ spawn: lead ──▶ lead/worker（图谱记一条边，status=open）
  ✉️  lead ──▶ lead/worker  [commentary]  "在 _demo_workspace 里执行 `echo ... > artifact.txt`"
       ↳ 记入共享 rollout（第 1 项）——通信即历史，可审计可重放
```

**② Worker 干活、回话。** Worker 读到收件箱里的任务，**内部跑一个 s01 回合**（调模型 → 模型让它跑 shell → 真的把文件建出来），然后发两条消息回 Lead：一条 `commentary`（"开工了"），一条 `final_answer`（"完成，产物是这个"）。注意 **phase 在区分"过程"和"结论"**：

```
  ✉️  lead/worker ──▶ lead  [commentary]  '收到任务，开工。'
  ✉️  lead/worker ──▶ lead  [final_answer]  "完成。产物内容：'hello from the worker'"
```

**③ Lead 派生 Reviewer，发审查请求——To + Cc 多收件人。** 这条消息**主送 reviewer、抄送 worker**：一条消息同时进了两个收件箱。这是"塞纸条"很难干净做到、而带内协议天生支持的：

```
  ✉️  lead ──▶ lead/reviewer  +cc ['lead/worker']  [final_answer]  "请审查这次改动：..."
```

**④ Reviewer 把关、回裁决。** Reviewer（模拟 Codex 的 `auto_review` 子代理）按风险规则判一下，回一条 `final_answer`：`APPROVE：改动安全，可合入`。Lead 读到裁决，收尾。

**⑤ 演示加密。** 最后 Lead 发一条**加密**消息——它在 rollout 里只留下不透明的 `‹encrypted›`，明文中间人看不到（呼应 agent 的加密身份）。

**最后打印全量共享 rollout**——这就是整章的题眼：

```
   1. [commentary  ] lead → lead/worker: 在 _demo_workspace 里执行 `echo ...`
   2. [commentary  ] lead/worker → lead: 收到任务，开工。
   3. [final_answer] lead/worker → lead: 完成。产物内容：'hello from the worker'
   4. [final_answer] lead → lead/reviewer cc=['lead/worker']: 请审查这次改动：...
   5. [final_answer] lead/reviewer → lead: APPROVE：改动安全，可合入
   6. [commentary  ] lead → lead/worker: ‹encrypted›
```

六条 agent 间通信，**没有一条是"塞进某个对话的自由文本"**——它们都是协议里有寻址、有阶段、留了痕的一等条目。在 Claude Code 里，这六条会是 `~/.claude/teams/<t>/inboxes/*.json` 里**读完即删**的文件；在 Codex 里，它们留在 rollout 里，可重放、可审计、可加密、可跨机。

## 生产级：进程内的玩具 vs 跨机器的真网络

本章的 router 是进程内一本共享 list——清楚地演示了"带内通信"的形状。但把它放到真实的多 agent / 云端环境，立刻冒出几个玩具版回避了的生产级问题：

- **真传输 + 分区**：agent 可能在不同机器、不同信任域。消息要走真网络，于是要面对**网络分区**——消息丢了/重了/乱序了怎么办？投递要有**确认 + 去重 + 重试**，而不是 `list.append` 那样必达。
- **真加密 + 身份**：教学版的 `encrypted=True` 只是个标志。真 Codex 用 `agent-identity` 的 **ed25519 签名 + curve25519 加密**：A 发给 B 的消息可验签（确认是 A 发的、没被篡改）、可加密（中间人看不到）。多租户/不可信环境里这是刚需，不是装饰。
- **`trigger_turn` 的背压**：真 `InterAgentCommunication` 带一个 `trigger_turn` 字段（见上方深入一）——决定一条消息**要不要立刻唤醒收件人跑一个回合**。若每条消息都触发，一个 5-agent 团队会陷入互相唤醒的风暴；生产级要能"只投递、不打断"，把唤醒攒批或限流。
- **失败隔离**：一个 agent 崩了/卡死了，不能拖垮整个团队（和 [s15](../s15_mcp/) 的"一个 MCP server 崩了不拖垮其余"同理）——要有超时、心跳、和"把死掉的成员从图谱里摘掉"的机制。

> 一句话：多 agent 的难点从来不是"造出第二个 agent"，而是**当它们分布在不会都正常的真实世界里时，通信还能可靠、可信、不失控**。带内协议给了你可审计的地基；其余的工业化，全在替"对方会丢消息、会撒谎、会崩"兜底。

## 🆚 与 Claude Code 的不同

通信是这一章两家分道扬镳得最彻底的地方：**CC 把通信放在文件系统里（带外），Codex 把通信放进协议本身（带内）。**

| 维度 | Claude Code | OpenAI Codex | 为什么 |
|---|---|---|---|
| 通信位置 | **带外**：文件系统收件箱 | **带内**：协议里的 `AgentMessage` 条目 | CC local-first；Codex 为云/分布式而生 |
| 传输 | 写对方的 `.json` mailbox | 提交 `Op::InterAgentCommunication` | 一个借道文件，一个走协议 |
| 投递 | **轮询**（Lead 1s / 队友 500ms） | 协议事件流，随回合流动 | 文件没有推送，只能定时读 |
| 寻址 | 文件名 = agent 名 | `AgentPath` + 多收件人（To/Cc） | 路径式寻址呼应 agent 图谱 |
| 安全 | 靠文件权限 | **内容可加密 + 加密身份**（ed25519） | 不可信 / 多租户环境里中间人不可见 |
| 持久化 | mailbox 文件（读完即删） | **进 rollout 持久记录** | 通信即历史，天然可审计可重放 |
| 跨机器 | 否（假设同一文件系统） | **是**（协议消息，可上云） | 决定性差异 |
| 模型如何看见 | 注入 `<teammate-message>` 文本 | 本就是历史里的 `AgentMessage` 条目 | 一个是事后塞，一个是原生 |

> 一句话：**CC 把 agent 通信当成共享文件系统上的邮件（Unix 管道的智慧）；Codex 把它当成协议里可加密、可路由、可审计的一等消息（分布式系统的思路）。** 还有一处气质延续：CC 标志性的子代理是"帮我干活的分身"，而 Codex 标志性的子代理是**"替我把关的审查员"（`auto_review`）**——把[Guardian](../s14_guardian/)那句"没人时派个 AI 把关"贯彻到了多智能体层。

完整的逐层对比（分身三模式、agent 图谱、cloud-tasks、external-agent-sessions、Codex 即 MCP 服务端……）见超长篇：**[子代理与多智能体全解](../docs/subagent-multiagent-cc-vs-codex.md)**。本章是那篇的**可跑骨架**。

## 深入：教学版 vs 真 Codex 源码

教学版把"通信"缩成一个内存 router + 一本共享 list。真 Codex 把它做成了协议类型 + 持久 rollout + 加密身份 + 云任务。*核心是同一个——把通信做成带内的一等条目；多出来的全是工业化。*

<details>
<summary>一、提交侧：Op::InterAgentCommunication</summary>

发一条 agent 间消息，在真 Codex 里是提交一个一等操作 [`Op::InterAgentCommunication { communication }`](../../codex/codex-rs/protocol/src/protocol.rs)（`protocol.rs:499`）。承载内容的结构体（`protocol.rs:626`）：

```rust
pub struct InterAgentCommunication {
    pub author: AgentPath,
    pub recipient: AgentPath,
    pub other_recipients: Vec<AgentPath>,   // ← 这就是 Cc（多收件人）
    pub content: String,
    pub encrypted_content: Option<String>,  // ← 可加密
    pub trigger_turn: bool,                 // ← 这条消息要不要立刻唤醒收件人跑一个回合
}
```

教学版的 `AgentMessage` 是它的精简像：`cc` ↔ `other_recipients`，`encrypted` ↔ `encrypted_content`。真源码多出的 `trigger_turn` 很有意思——它把"发消息"和"让对方立刻行动"解耦：可以只投递、不打断对方。

</details>

<details>
<summary>二、历史侧：通信即历史（→ 进 rollout）</summary>

为什么说"带内通信天然留痕"？看 `InterAgentCommunication::to_response_input_item()`（`protocol.rs:673`）：它把一条通信**变成一条 `assistant` 历史消息**（phase 标成 `Commentary`）。也就是说，agent 间的一句话，落地后就是对话历史里的一项，自然随线程进 rollout（见 [s08](../s08_rollout/)）。

落进**模型可见历史**时，它是 [`ResponseItem::AgentMessage { author, recipient, content }`](../../codex/codex-rs/protocol/src/models.rs)（`models.rs:767`）——和 `Message` / `Reasoning` / `FunctionCall` 并列的一种 ResponseItem，自带"谁发的、发给谁"。教学版的"把消息 append 进共享 `rollout` list"，就是这一步的玩具版。

</details>

<details>
<summary>三、阶段（phase）、加密、寻址</summary>

- **MessagePhase**（`models.rs:741`）：`Commentary`（中途碎碎念）/ `FinalAnswer`（最终答复）。源码注释还诚实地提醒：*"providers do not emit this consistently, so callers must treat `None` as phase unknown"*——别假设模型一定给你标对。教学版的 `Phase` 枚举就是它。
- **加密内容**：`AgentMessageInputContent::EncryptedContent { encrypted_content }`（`models.rs:720`）。配合 [`agent-identity/`](../../codex/codex-rs/agent-identity/) 的 **ed25519 签名 + curve25519**，A 发给 B 的话可以加密、可验签——多租户/不可信环境里中间人看不到。
- **AgentPath**（`protocol/src/agent_path.rs`）：路径式寻址（像 `lead/worker`），呼应下面的 agent 图谱。

</details>

<details>
<summary>四、不止"通信"：图谱、身份、审查员、云、互操作</summary>

| Codex 的多智能体基础设施 | 真源码 | 教学版 |
|---|---|---|
| **agent 图谱**：持久化的父/子拓扑 | [`agent-graph-store/`](../../codex/codex-rs/agent-graph-store/)，`ThreadSpawnEdgeStatus{Open,Closed}`（`types.rs:7`） | router 里一个 `graph` list |
| **加密身份**：每个 agent 有可验证身份 | [`agent-identity/`](../../codex/codex-rs/agent-identity/)（ed25519） | `encrypted` 标志 + AgentPath |
| **审查员子代理**：审批可路由给 AI | `ApprovalsReviewer = "user" \| "auto_review" \| "guardian_subagent"`（`config_types.rs:159`） | `Reviewer`（规则模拟） |
| **派任务上云** | [`cloud-tasks/`](../../codex/codex-rs/cloud-tasks/) | 省略（demo 全在进程内） |
| **导入外部 agent 会话** | [`external-agent-sessions/`](../../codex/codex-rs/external-agent-sessions/) | 省略 |
| **Codex 即一个可被调用的成员** | `mcp-server` 暴露 `codex` 工具（见 [s15](../s15_mcp/)） | 省略 |

`auto_review` 的官方描述：*"uses a carefully prompted **subagent** to gather relevant context and apply a risk-based decision framework before approving or denying the request."*——审批官本身就是个子代理。

</details>

<details>
<summary>五、教学版砍掉了什么</summary>

进程内对象冒充了：网络/跨机传输、真正的 ed25519 签名与加密、SQLite/zstd 的 rollout 落盘（[s08](../s08_rollout/) 才是真的）、并发与锁、云任务调度、`trigger_turn` 的回合唤醒语义、以及每个 agent 本应有的完整 s01–s17 栈（这里把 Worker 的"脑子"缩成一个回合，把 Reviewer 缩成一条规则，好让**通信**这条主线看得清清楚楚）。骨架就是这么大；其余都是工业化。

</details>

## 运行

```bash
python s18_multiagent/code.py --demo   # 离线：Lead → Worker → Reviewer 协作一遍（mock，无需 key）
python s18_multiagent/code.py          # 交互模式：你当 Lead，给 worker 派一句任务
```

默认 `backend=mock`，离线可跑；demo 跑完会**自动清理** `_demo_workspace/`（和其余各章一致）。

## 小结

- 多智能体分两件事：**分身**（开白纸干脏活、只回传结论）和**组队**（对等协作、互相通信）；难的是后者的**通信**。
- 关键抉择是"通信放哪"：**CC 带外**（文件收件箱、轮询、可 `cat` 围观，但假设同机）vs **Codex 带内**（协议里的 `AgentMessage`、进 rollout、可加密、多收件人、可跨机）。
- 带内通信的精髓是**通信即历史**：每条 agent 消息自动落进 rollout，于是可重放、可审计；再加身份、phase、加密，通信就成了分布式基础设施。
- Codex 标志性的子代理是**审查员（auto_review）**——把"没人时派个 AI 把关"从 [s14](../s14_guardian/) 延续到了多智能体层。
- **生产级**：进程内 list 之外，真多 agent 要面对网络分区（确认+去重+重试）、真加密+签名身份（ed25519，防篡改/窃听）、`trigger_turn` 背压（别互相唤醒成风暴）、失败隔离（一个崩了不拖垮全队）（见「生产级」一节）。
- 下一站：回到 [总览](../README.md) 看全景，或读超长篇 [子代理与多智能体全解](../docs/subagent-multiagent-cc-vs-codex.md) 把分身三模式、cloud-tasks、互操作一次看透——本章是那篇的可跑骨架。

## 思考

<div class="think">

1. CC 用"文件系统当消息总线"——朴素但你能 `cat` 围观。Codex 用协议内 `AgentMessage` + agent 图谱——结构化但更"黑盒"。调试一个 5-agent 卡死的团队时，你更想要哪种？为什么？
2. Codex 给 agent 消息加**加密 + 身份**。在什么场景下，"这条改动是哪个 agent、由谁授权做的"必须能被密码学证明？（提示：合规、多租户、供应链。）
3. 真源码的 `trigger_turn` 让"发消息"和"唤醒对方"解耦——可以只投递、不打断。如果所有消息都立刻唤醒收件人跑一个回合，一个 5-agent 团队会怎样？你会在什么时候故意 `trigger_turn=false`？
4. "审查员也是个子代理"（auto_review）——让一个 AI 去审另一个 AI 要不要执行危险命令。这个 reviewer 会不会被同样的注入手法骗过？要让"AI 审 AI"真的增加安全（而非增加一层同源盲点），前提是什么？（接着 [s14](../s14_guardian/) 那个问题想。）
5. 本章每个 agent 的"脑子"都被缩成了一个回合或一条规则。如果把它们换成完整的 s01–s17 栈（各自带审批、沙箱、压缩、rollout），这个 3-agent demo 里**最先**出问题的会是哪一环？为什么？

</div>
