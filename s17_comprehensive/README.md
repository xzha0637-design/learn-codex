# s17: Comprehensive — 拼装一个迷你 Codex

> *"造好载具，agent 会完成剩下的。"*

[learn-codex 总览](../README.md) · [s16 config](../s16_config/) → **s17 综合（单 agent 篇 · 终点）** → [s18 多智能体（进阶）](../s18_multiagent/)

---

## 先把思想说透：整门课其实只是「一条流水线」

走到最后一章，回头看：前面 16 章好像各讲各的——循环、apply_patch、沙箱、审批、AGENTS.md、事件广播……像 16 个互不相干的话题。但其实它们从头到尾都在拼**同一样东西**。想通下面三个道理，你不只看懂这一章，还会突然看懂整门课的结构。

**道理一：一条用户请求要变成真实行动，必须依次穿过好几道关卡——这条链子就是 agent 的本质形状。**
你说一句「执行 `echo hi`」，它不会、也不该直接就跑。它得**依次穿过**：注入项目记忆（让模型带着这个项目的规矩思考）→ 调模型拿到「想做什么」→ 过审批闸门（这条命令准不准跑）→ 落进沙箱安全地执行 → 结果回灌、继续循环。把这些处理一道接一道排起来，就是一条**流水线**——像工厂传送带，原料（你的话）在带上走，每过一个工位被加工一次，最后出成品（一次被批准、被沙箱、被记录的行动）。前面每一章打磨的那个机制，其实都是这条传送带上的**一道关卡**（gate：先检查、再放行，最典型的就是审批闸门，像机场安检口，命令得先过安检、再过沙箱隔离门，才被允许真正执行）。

**道理二（最关键）：模型只在这条流水线的中间一步出现，其余每一道关卡都是 harness。**
这是整门课最想让你看见的事实。盯着那条传送带数一数：模型只在「想做什么」那一步露了个面，吐出一个工具调用就退场了。它**不决定**这条命令准不准跑（审批）、**不负责**安全地执行（沙箱）、**不操心**怎么被记录和广播（事件）、**也不管**项目的规矩从哪来（记忆注入）。这些全是**harness** 的活。所以那句贯穿全课的话——「Agency 来自模型，载具由你来造」——在这一章变得肉眼可见：**模型提供「想做什么」的智能，你作为 harness 工程师，造好它栖居与行动的整个世界。**

**道理三：把小零件按正确接口接起来，就长出一个能跑的迷你 Codex——复杂度在工程化，骨架可以很小。**
前 16 章每章造了一个**零件**（component），单看都是一小段能跑的代码，像乐高里的单块。这一章不造新零件，而是把它们按正确顺序、正确接口**装配**起来——让数据从一个零件的出口流进下一个的入口，再跑一次**集成验证**（`--demo` 让一条请求真的从头走到尾，确认这些零件合在一起真能协同，而不只是各自单独能跑）。神奇的是：这样拼出来的东西，骨架和真 Codex 那个工业级的「总装车间」`turn.rs` **是同一具**——区别只在真版把每个零件做到了上千行的健壮，而核心思想，五十行就装得下。

一句话定调：**整门课就是在造一条流水线——前 16 章在造零件，这一章把零件装起来、验证它们协同工作。** 看懂「请求如何沿流水线穿过一道道关卡、而模型只占其中一步」，你就握住了任何 agent harness 的骨架。

## 问题

前面 16 章，每章打磨一个零件：循环、apply_patch、沙箱、审批、SQ/EQ、Responses、AGENTS.md…… 可一个真正的 agent 产品不是零件的堆叠，而是它们的**装配体**。

零件之间怎么咬合？一条用户请求，要依次穿过多少道关卡，才变成一次"被批准、被沙箱、被记录"的真实行动？这一章把零件装起来，跑通一条完整流水线。

## 解决方案

一条贯穿始终的请求流水线——这正是 Codex（和任何 agent harness）的本质形状：

```
  用户输入
     │
     ▼  注入项目记忆 (s06 AGENTS.md)
  build_system()
     │
     ▼  调模型，拿工具调用 (s09 Responses 形状 / s01 循环)
  model.respond()
     │
     ▼  每个工具调用，先过闸门
  审批 decide() ──拒绝──▶ 不执行，回灌 "(denied)"
     │ 放行
     ▼  命令落进内核沙箱 (s05) / 文件改动走 apply_patch (s03)
  run_sandboxed() / apply_patch()
     │
     ▼  全程广播事件 (s10)
  emit(...) ──▶ 前端渲染
     │
     ▼  结果回灌，继续循环 (s01)
```

## 工作原理

看 [code.py](code.py)——它不引入任何新机制，只是把前面的精简搬运过来、接成一条线：

- `build_system()` 把向上发现的 AGENTS.md 拼进 system（s06）；
- 工具不再直接执行：`tool_shell` 先过 `decide()` 审批闸门（s04），放行后交给 `run_sandboxed()`（s05）；`tool_apply_patch` 走补丁应用（s03）；
- `emit()` 在每一步广播事件（s10）；
- `run_turn()` 是那个熟悉的 s01 循环，只是中间多了上面这些关卡。

关键就一行心法：**工具 = 审批闸门 → (沙箱 / apply_patch)**。模型负责"想做什么"，harness 负责"能不能做、怎么安全地做"。

真 Codex 的"总装车间"在 [`core/src/session/turn.rs`](../../codex/codex-rs/core/src/session/turn.rs)——它把同样这些关卡，工业级地编排在异步回合里。

**走一遍**：跟着 `--demo` 的第 ① 步——用户说「执行 `echo mini-codex online`」——看一条请求怎么沿流水线一道道关卡穿过去，每步的数据长什么样、为什么要有这一步。

第 1 步 **build_system()**（注入项目记忆）。`run_turn` 一开始就把当前目录向上发现的 AGENTS.md 拼进 system 提示。如果上层有一份 AGENTS.md，system 大致长这样（**为什么**：让模型带着「这个项目的规矩」去思考，而不是每次从零开始）：

```text
You are a mini-Codex coding agent at /path/to/repo. Act, don't explain.

<project_instructions>
--- /path/to/repo/AGENTS.md ---
（这里是项目自己的约定，比如"用 ruff 格式化""测试放 tests/"）
</project_instructions>
```

第 2 步 **model.respond(...)**（调模型，拿工具调用）。把 `[user_item("执行 …")]` 连同工具清单和 system 发给模型，模型不直接执行，而是**回一个工具调用**（s09 Responses 的形状）——大意是「我想调 shell，参数是这条命令」：

```json
{"type": "function_call", "name": "shell",
 "call_id": "call_1", "arguments": {"command": "echo mini-codex online"}}
```

**为什么**：模型只负责「想做什么」，决定权先不交给它——这正是下一关存在的理由。

第 3 步 **decide() 审批闸门**（先检查再放行）。`tool_shell` 拿到命令后**不立刻跑**，先喊一声 `emit("approval", ...)` 广播「有个待批的命令」，再交给 `decide(command, policy)` 判一下。当前 `POLICY = "on-request"`，而 `echo` 在安全名单 `SAFE` 里、又不含危险片段，于是裁决是 `approve`（**为什么**：安全的只读类命令自动放行，省去打扰；真要 `rm -rf` 这种才会停下来问）：

```text
[event] ❓ approval   shell: echo mini-codex online
decide("echo mini-codex online", "on-request") → "approve"     # 命中 SAFE，放行
```

第 4 步 **run_sandboxed()**（命令落进内核沙箱执行）。放行后，命令并不是裸奔 `os.system`，而是被包进 macOS Seatbelt 策略里执行——只允许在工作目录下写文件，别处只读（**为什么**：万一模型给的命令有破坏性，沙箱是内核级的最后一道物理隔离）。执行前先 `emit("exec", ...)`：

```text
[event] ⏵ exec      echo mini-codex online
（在 sandbox-exec 包裹下运行 /bin/sh -c "echo mini-codex online"）
→ 输出: "mini-codex online"
```

第 5 步 **结果回灌，继续循环**（s01）。命令输出被包成一个 `tool_output_item` 追加回消息列表，循环回到第 2 步再问模型「拿到结果了，下一步?」。这次模型没有新的工具调用、只回了一句话，于是 `emit("msg", ...)` 然后结束这一回合：

```python
messages.append(tool_output_item("call_1", "mini-codex online"))
# 再调一次 model.respond → 这次 resp.tool_calls 为空 → 收尾
```

**把这五步连起来看**：用户的一句话，经过「注入记忆 → 模型出意图 → 审批放行 → 沙箱执行 → 结果回灌」五道关卡，才变成一次**被批准、被沙箱、被记录**的真实行动。模型只在第 2 步出现一下；其余每一关都是 harness 在干活。这就是这一章想让你亲眼看到的骨架。

## 生产级：把"能跑"和"能上生产"分开看

走到这里，你已经看过每一章的「生产级」小节。把它们摞起来看，会发现一条清晰的分界线：**让 agent "能跑"的代码很少，让它"能上生产"的代码占了大半**——而后者几乎全在回答同一个问题："出岔子时怎么办？"

| 关卡 | "能跑"的玩具 | "能上生产"补的那一层 |
|---|---|---|
| 回合循环（[s01](../s01_agent_loop/)） | `while True` | 步数封顶 + 可中断（`Op::Interrupt`） |
| 工具（[s02](../s02_tool_use/)） | `handler(**args)` | schema 校验 + 出错回灌（`RespondToModel`/`Fatal`） |
| apply_patch（[s03](../s03_apply_patch/)） | 精确匹配 | 模糊匹配 + 原子（两阶段）+ 错误回灌 |
| 审批（[s04](../s04_approval/)） | 一个 bool | 带记忆的 `ReviewDecision` + `BANNED_PREFIX` 刹车 |
| 沙箱（[s05](../s05_sandbox/)） | 8 行策略 | deny-default + 默认禁网 + 逐条选沙箱 |
| 压缩（[s07](../s07_context_compaction/)） | proactive 估算 | reactive 撞墙兜底 |
| 模型调用（[s09](../s09_responses_api/)） | 一次请求 | 重试 + 退避抖动 + 传输回退 + 错误分类 |
| MCP（[s15](../s15_mcp/)） | 进程内调用 | 超时 + 连接韧性 + 命名空间 |
| 配置（[s16](../s16_config/)） | 读字段 | 边界校验（`deny_unknown_fields` + typed enum） |

看出共同的母题了吗？**几乎每一层的"生产级"都是同一句话的变体：假设它会出错，然后让出错时倒向安全、可恢复、不失控。** fail-closed、回灌让模型自己改、封顶防失控、断电不丢底稿——这不是九个互不相干的技巧，而是同一种工程直觉在九个位置的九次现身。这就是"造载具"的真正含义：模型负责聪明，而你负责让这台载具**在它犯错、网络抖动、用户喊停、磁盘写满时，依然不翻车**。

## 🆚 与 Claude Code 的不同：一张零件总对照

走到终点，把整趟旅程收成一张表——同一条流水线，两套零件选择：

| 关卡 | Claude Code | Codex | 章节 |
|---|---|---|---|
| 线协议 | Anthropic Messages | OpenAI Responses（+reasoning） | s09 |
| 改文件 | Edit 串替换 | apply_patch 补丁信封 | s03 |
| 安全·第一道 | 审批弹窗（应用层） | 内核沙箱（内核层） | s05 |
| 审批 | 危险即问 | 4 档策略 + Guardian | s04 / s14 |
| 架构 | 较直接的循环 | SQ/EQ 双队列 + 多前端 | s10 / s11 |
| 项目记忆 | CLAUDE.md | AGENTS.md（逐级向上） | s06 |
| 会话 | 历史 | Rollout（可续接/回放） | s08 |

**为什么？** 一句话收束全篇：**Claude Code 押"协作"（交互式审批 UX、应用层把关），Codex 押"自主"（内核沙箱、可审计 rollout、为无人值守而生）；而两边的工具差异，归根结底是各自模型被训练出的习惯不同。** 没有谁对谁错，只有面向不同场景的不同下注。

## 深入：教学版 vs 真 Codex 源码

<details>
<summary>一、这条 demo 流水线省掉了什么</summary>

迷你 Codex 只串了 6 个零件。一个真实回合还会穿过：rollout 记录（s08）、上下文压缩（s07）、MCP 工具（s15）、hooks（s13）、Guardian 风险评估（s14）、并把事件喂给 TUI / `codex exec`（s11 前端）。每一个都是这条流水线上的又一道关卡。

</details>

<details>
<summary>二、完整版流水线：16 个零件各自挂在哪一道关卡（全课索引）</summary>

把全课 16 个零件按它们在「一条真实请求」上出现的先后，挂回这条流水线——这张表既是收尾，也是一份回看导航：哪一章造的零件，落在传送带的哪个工位。

| 流水线工位（按请求先后） | 干什么 | 教学版 demo 有? | 章节 |
|---|---|---|---|
| 回合循环（最外层） | `while`：调模型→执行工具→回灌→再调，直到没有工具调用 | ✅ `run_turn` | [s01](../s01_agent_loop/) |
| 工具与分发 | 声明工具 schema、把模型的 tool_call 路由到对应处理函数 | ✅ `TOOLS`/`HANDLERS` | [s02](../s02_tool_use/) |
| 注入项目记忆 | 向上逐级收集 AGENTS.md 拼进 system | ✅ `build_system` | [s06](../s06_agents_md/) |
| 上下文压缩 | 历史过长时摘要旧轮，腾出窗口 | ❌ 省略 | [s07](../s07_context_compaction/) |
| 续接 / 回放 | 把每轮写进 rollout，崩溃后可续、可回放 | ❌ 省略 | [s08](../s08_rollout/) |
| 调模型（线协议） | 用 OpenAI Responses 形状发请求、收 reasoning + tool_call | ✅ `model.respond`（mock） | [s09](../s09_responses_api/) |
| 事件双队列（SQ/EQ） | 提交侧 `Op` 入、事件侧 `Event` 出；解耦前端与 core | ✅ `emit`（精简） | [s10](../s10_sq_eq_protocol/) |
| 前端渲染 | 事件喂给 TUI 或 `codex exec` 无头模式 | ❌ 省略 | [s11](../s11_frontends/) |
| 更多工具 | plan / web_search / view_image 等扩展工具 | ❌ 省略 | [s12](../s12_tools_extra/) |
| Hooks | 在关键时机插入用户自定义脚本 | ❌ 省略 | [s13](../s13_hooks/) |
| 审批闸门 | 工具执行前裁决 approve / ask；Guardian 评风险 | ✅ `decide`（4 档） | [s04](../s04_approval/) / [s14](../s14_guardian/) |
| 内核沙箱 | 放行后命令落进 Seatbelt，只能写工作区 | ✅ `run_sandboxed` | [s05](../s05_sandbox/) |
| 改文件 | 文件改动走 apply_patch 补丁信封而非裸写 | ✅ `apply_patch` | [s03](../s03_apply_patch/) |
| MCP | 接外部工具服务器（客户端 + 服务端） | ❌ 省略 | [s15](../s15_mcp/) |
| 配置与 profile | 分层解析 config.toml + 命名 profile 决定上面每档的默认值 | ❌ 省略 | [s16](../s16_config/) |

读法：**带 ✅ 的 6 行就是本章 demo 真正串起来的那条最小流水线**；带 ❌ 的是真 Codex 在同一条线上额外挂的关卡——它们不改变骨架，只是让每一道工位更结实。注意最后一行「配置」并不站在某个具体工位上，而是**横贯整条线**——它决定了审批用哪档、沙箱开多大、用哪个模型（见上一章 [s16](../s16_config/)）。

</details>

<details>
<summary>三、它仍然是个玩具</summary>

教学版的 apply_patch 只认精确上下文、审批是几个字符串前缀、沙箱策略 8 行、模型调用是 mock。真 Codex 的对应物分别是上千行的 crate、状态机、123 行起步的内核策略、流式 WebSocket 客户端。**但骨架是同一具**——这正是本课程想证明的：复杂度在保护与工程化，核心思想可以很小。

</details>

<details>
<summary>四、harness 工程师的工作</summary>

回看这条流水线：模型只出现在中间一步（"想做什么"）。其余每一道关卡——记忆、审批、沙箱、事件、记录——都是 harness。Agency 来自模型；而你，作为 harness 工程师，负责造好它栖居与行动的整个世界。

</details>

<details>
<summary>五、真源码 crate ↔ 章节（反查表）</summary>

想从 `codex-rs` 的某个 crate / 模块反查「它在本课哪一章拆」，用这张表：

| codex-rs crate / 模块 | 负责什么 | 在本课哪章 |
|---|---|---|
| `core/src/session/turn.rs` | 回合引擎（编排一个 turn） | [s01](../s01_agent_loop/) · [s17](../s17_comprehensive/) |
| `core/src/tools/`（registry / handlers）· `tools/src/tool_spec.rs` | 工具注册与分发 | [s02](../s02_tool_use/) · [s12](../s12_tools_extra/) |
| `apply-patch/`（parser · lib · seek_sequence） | 补丁信封的解析与应用 | [s03](../s03_apply_patch/) |
| `execpolicy/` · `shell-command/src/command_safety/` | 审批决策 / 命令安全判定 | [s04](../s04_approval/) |
| `protocol/src/approvals.rs` | 审批事件 + Guardian 风险枚举 | [s04](../s04_approval/) · [s14](../s14_guardian/) |
| `sandboxing/`（Seatbelt）· `linux-sandbox/`（Landlock+seccomp） | 内核级沙箱 | [s05](../s05_sandbox/) |
| `core/src/agents_md.rs` | AGENTS.md 分层注入 | [s06](../s06_agents_md/) |
| `core/src/compact.rs` · `compact_remote.rs` / `_v2` | 上下文压缩（本地 + 服务端） | [s07](../s07_context_compaction/) |
| `rollout/`（recorder）· `state/` | 会话持久化（resume / rewind / 审计） | [s08](../s08_rollout/) |
| `core/src/client.rs` | Responses API 模型客户端 | [s09](../s09_responses_api/) |
| `protocol/src/protocol.rs` | SQ/EQ 协议：`Op` 进 / `EventMsg` 出 | [s10](../s10_sq_eq_protocol/) |
| `tui/` · `exec/` · `app-server/` | 三种前端（都只是事件消费者） | [s11](../s11_frontends/) |
| `core/src/web_search.rs` · `protocol/src/plan_tool.rs` | 更多工具：plan / web_search / view_image | [s12](../s12_tools_extra/) |
| `hooks/src/registry.rs` | 钩子注册与触发 | [s13](../s13_hooks/) |
| `codex-mcp/`（MCP 客户端）· `mcp-server/` · `core/src/mcp_tool_exposure.rs` | MCP 双向：连别人 / 被别人连 | [s15](../s15_mcp/) |
| `config/`（merge.rs） | 配置与 profiles（横贯整条流水线） | [s16](../s16_config/) |
| `agent-graph-store/` · `agent-identity/` · `protocol`(InterAgentCommunication) | 多智能体：图谱 / 身份 / 带内通信 | [s18](../s18_multiagent/) |

> 注：路径以本仓库 `../../codex/codex-rs/` 为根；同一 crate 可能在多章出现（如 `protocol.rs` 被审批 / 协议 / 前端多处引用），这里只列它的**主讲**章。

</details>

## 运行

```bash
python s17_comprehensive/code.py --demo   # 离线跑通整条流水线
python s17_comprehensive/code.py          # 交互模式（命令会被审批 + 沙箱）
```

## 小结

- 你从 [s01](../s01_agent_loop/) 的一个 `while` 循环出发，途经 16 章、攒下一柜子零件，到这一章把它们拼成了一个会审批、会沙箱、带记忆、可观测的迷你 Codex。
- agent 产品 = 模型 + harness；这一章证明了"装配体"如何由小零件咬合而成——**流水线**是骨架，每章的机制是挂在骨架上的一道道**关卡**。
- 真 Codex 只是把每个零件做到工业级（上千行 crate、状态机、内核策略、流式客户端）——但骨架是同一具，你已经握在手里了。
- **生产级是一条贯穿全书的暗线**：让 agent"能跑"的代码很少，让它"能上生产"的占大半——后者几乎都在回答"出岔子时怎么办"，答案都是同一句：假设它会错，让出错时**倒向安全、可恢复、不失控**（见「生产级」一节那张总表）。
- 进阶专题 [s18 多智能体](../s18_multiagent/)：当**一个** agent 不够用时，几个 agent 怎么互相说话？（正好接住下面思考第 4 问——这就是一个"第 18 个零件"的答案。）

## 思考

<div class="think">

1. 这条流水线里，去掉哪一个零件，agent 仍能"工作"但变得**危险**？去掉哪一个会让它"安全"但**残废**？
2. 如果要把这个迷你 Codex 搬到一个全新领域（比如运维、数据分析），哪些零件原样能用、哪些必须换？（提示：循环不变，工具/知识/权限在变。）
3. 整趟看下来，Codex 和 Claude Code 的差异，有多少是"工程取舍"，有多少其实是"两家模型不同"逼出来的？
4. 现在轮到你了：17 章之后，你会给这个 harness 加的**第 18 个零件**是什么？它解决模型的哪个短板？

</div>
