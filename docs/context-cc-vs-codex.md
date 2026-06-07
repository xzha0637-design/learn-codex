# 超长篇：Claude Code 与 Codex 的「上下文处理」全解

> 🌐 [English](context-cc-vs-codex.en.md) · **中文**

> 这是一篇深入长文，面向**只有些许基础**的读者。我们从"上下文窗口是什么"讲起，一路拆到两套系统在
> 注入、压缩、截断、持久化、记忆上的每一处工程取舍，并解释**为什么**它们走了不同的路。
>
> 事实依据：Codex 来自本仓库的真源码 `../../codex/codex-rs`；Claude Code（下称 CC）来自
> [learn-claude-code](../../learn-claude-code/) 对其源码（`compact.ts` / `autoCompact.ts` / `microCompact.ts` / `query.ts`）的剖析。
>
> ⚠️ **关于论据强度**：Codex 侧每个数字/常量都能在 `codex-rs` 里查到行号；CC 侧的精确数字（如「保头几条」「N×K token 预算」「消息类型种数」「轮询 1s/500ms」）转述自 learn-claude-code 对**闭源** CC 的剖析，**无法在本仓库独立核验**——请按二手材料对待，确定性低于 Codex 侧。

[← 返回 learn-codex 总览](../README.md)

---

## 0. 给初学者：上下文窗口是什么，为什么会"满"

大模型不是数据库，它没有"记忆"。它每次回答，靠的都是你**这一次**塞给它的全部文字。

这"全部文字"装在一个有大小上限的盒子里，叫**上下文窗口（context window）**，单位是 **token**（你可以粗略理解为"词的碎片"，1 个汉字大约 1–2 token，100 个英文单词大约 130 token）。比如某模型的窗口是 272,000 token——这就是它一次能"看见"的全部。

现在想象一个 agent 在干活：

1. 你说"帮我把这个项目的测试修好"。
2. 它读了一个 1000 行的文件（≈4000 token）。
3. 又读了 30 个文件、跑了 20 条命令。
4. 每个文件的内容、每条命令的输出，**全都堆在对话历史里**，因为下一轮还要把整段历史重新发给模型——这是 [agent loop](../README.md) 的本质：模型没有记忆，历史就是它的记忆。

堆着堆着，盒子满了。模型 API 直接报错：CC 那边叫 `prompt_too_long`（HTTP 413），Codex 那边叫 `ContextWindowExceeded`。

**于是每一个严肃的 agent harness 都必须解决同一个问题：上下文总会满，得有办法腾地方，还不能把重要的事忘掉。** 这篇文章就是讲 CC 和 Codex 各自怎么解决它——以及它们为什么解得不一样。

---

## 1. 先解剖：一次请求的上下文里到底装了什么

在压缩之前，先看清"要压的东西"由几块组成。两套系统的构成大同小异，但叫法和形状不同：

| 组成部分 | Claude Code | Codex | 在哪一章细讲 |
|---|---|---|---|
| 系统指令 | `system` 参数 | `instructions` 参数 | [s09 Responses API](../s09_responses_api/) |
| 项目记忆 | `CLAUDE.md` | `AGENTS.md`（逐级向上） | [s06 AGENTS.md](../s06_agents_md/) |
| 工具定义 | `tools`（`input_schema`） | `tools`（扁平 `parameters`） | [s02](../s02_tool_use/) / [s09](../s09_responses_api/) |
| 对话历史 | `messages[]`，内容是 `tool_use`/`tool_result` **块** | 扁平 item 列表：`message`/`function_call`/`function_call_output` | [s09](../s09_responses_api/) / [s10](../s10_sq_eq_protocol/) |
| 模型推理 | thinking 块 | reasoning item（可加密、跨轮携带） | [s09](../s09_responses_api/) |
| 工具输出 | `tool_result` 块的内容 | `function_call_output` 的内容 | [s07 压缩](../s07_context_compaction/) |

**关键认知**：上面除了"系统指令/项目记忆/工具定义"是相对固定的，**对话历史 + 工具输出**才是会无限膨胀、最终撑爆窗口的部分。所以一切压缩手段，主要都在跟这两块较劲。

下面我们顺着"注入 → 表示 → 压缩 → 截断 → 持久化 → 记忆"这条链，逐段对比。

---

## 2. 注入项目记忆：CLAUDE.md（CC） vs AGENTS.md（Codex）

agent 要懂"这个项目的规矩"（用什么包管理器、代码风格、别碰哪些目录）。两边都用一个 Markdown 文件承载这种"项目记忆"，但发现与注入的方式不同。

### Codex：逐级向上收集 AGENTS.md，有上限、可覆盖

真源码在 [`core/src/agents_md.rs`](../../codex/codex-rs/core/src/agents_md.rs)。要点：

- **从当前目录一路向上走到项目根**（`.git` 等标记），把沿途每一个 `AGENTS.md` 都收集起来；
- 用分隔符 `AGENTS_MD_SEPARATOR = "\n\n--- project-doc ---\n\n"` 把它们**按"根 → 当前目录"的顺序**拼接（越具体的越靠后，越"近"越有发言权）；
- 有总量上限 `project_doc_max_bytes`（默认约 32 KiB）——记忆不能无限大，否则它自己就先把窗口吃掉了；
- 支持本地覆盖文件 `AGENTS.override.md`（`LOCAL_AGENTS_MD_FILENAME`），以及可配置的回退文件名 `project_doc_fallback_filenames`；
- 最终作为一个 `<user_instructions>` 块注入到 `instructions` 里。

这套"逐级向上 + 拼接"的设计特别适合 **monorepo**：仓库根放通用规矩，每个子包放自己的特殊规矩，agent 进到哪个子目录，就自动叠加那一路的规矩。

### Claude Code：CLAUDE.md

CC 用 `CLAUDE.md` 承载同样的角色（项目级、用户级），注入进 system。它同样支持层级（项目/用户/本地），理念一致。

### 差异与为什么

| | Claude Code | Codex |
|---|---|---|
| 文件名 | `CLAUDE.md` | `AGENTS.md`（+ `AGENTS.override.md`） |
| 发现方式 | 项目/用户层级 | **从 cwd 逐级向上收集整条链** |
| 大小约束 | 有 | 显式字节上限（默认 ~32KiB） |
| 标准化 | 自家约定 | 推动 `AGENTS.md` 成为**跨工具/跨厂商**的开放约定 |

**为什么？** AGENTS.md 是 OpenAI 主推的一个跨工具标准——它希望同一份 `AGENTS.md` 能被 Codex、以及其它遵循该约定的工具共用。逐级向上收集则是把"项目记忆"做成可分层叠加的，契合大仓库。两者哲学一致（给 agent 注入项目常识），差别在标准化姿态与发现粒度。

---

## 3. 历史的"形状"如何影响压缩

这一点常被忽略，但很关键：**历史长什么样，决定了你能怎么压它。**

- **CC（Anthropic Messages）**：历史是 `messages[]`，每条消息的 `content` 是一个**块数组**，工具调用是 `tool_use` 块、工具结果是 `tool_result` 块，**嵌在** assistant/user 消息里。所以 CC 的压缩是"在消息和块两个层级上动手"——既能裁整条消息，也能单独把某个 `tool_result` 块的内容替换掉。
- **Codex（OpenAI Responses）**：历史是**扁平的 item 列表**，`function_call` 和 `function_call_output` 各自是**独立的 item**，与文本 `message` 平级。所以 Codex 更多是"在 item 序列上从头裁剪"。

记住这个形状差异，下面两节的压缩策略就好理解了。

---

## 4. Claude Code 的压缩：四层流水线，便宜的先跑

CC 的核心设计原则是 **"便宜的先跑，贵的后跑"**——能用纯文本操作解决的，绝不调模型。它把压缩做成一条四层流水线（外加应急层），每轮 LLM 调用前依次跑。

### L1 · snip_compact —— 裁掉中间无关的旧对话（0 API）

对话攒到很多条时，最前面那些"帮我建个 hello.py"早跟当前工作无关了。保留**头部 3 条**（初始上下文）+ **尾部若干条**（当前工作），中间用一句占位符 `[snipped N messages]` 替掉。

> 真实 CC 里这是个 feature gate（`HISTORY_SNIP`），还暴露了 `SnipTool` 让模型主动调用。

### L2 · micro_compact —— 旧工具结果替换成占位符（0 API）

连续读了 10 个文件，前 7 个的完整内容还躺在上下文里白占地方。只保留**最近 3 条** `tool_result` 的完整内容，更旧的替换成 `[Earlier tool result compacted. Re-run if needed.]`。

> 真实 CC 有两条路径：按时间触发（间隔 60 分钟）直接清，和走 API `cache_edits` 的缓存路径。

### L3 · tool_result_budget —— 大结果落盘（0 API）

一次 `cat` 了 5 个大文件，单条消息里的 `tool_result` 加起来 500KB。统计最后一条 user 消息里所有 `tool_result` 总大小，超过 **200,000 字符**就从最大的开始**落盘**到 `.task_outputs/`，上下文里只留一个 `<persisted-output>` 标记 + 前 2000 字符预览。模型知道完整内容在磁盘上，需要时重新读。

### L4 · compact_history —— LLM 全量摘要（1 API）

前三层都是纯文本操作、不"理解"内容。如果 token 仍超阈值，就把整段历史发给模型，要求它生成结构化摘要（真实 CC 要求 **9 个部分** + `<analysis>`/`<summary>` 双标签，并在首尾**双重强调禁止调用工具**），然后用这条摘要**替换**掉旧消息。

- 触发阈值（精确 token）：`contextWindow − maxOutputTokens − 13,000`（那 13,000 是 `AUTOCOMPACT_BUFFER_TOKENS` 安全余量）。
- **压缩后恢复**：CC 不只留摘要——它会自动重新附加最近读过的文件（最多 **5 个**、每个 **5,000** token、总预算 **50,000** token）、计划、agent/skill/tool 上下文。这是教学版没有的生产级细节。
- **熔断器**：连续摘要失败 3 次就停，防止死循环烧钱。

### 应急 · reactive_compact —— 真撞上 413 时

上下文涨得比压缩还快、API 直接返回 `prompt_too_long` 时，触发更激进的回退：按消息组从尾部回退（`truncateHeadForPTLRetry`），字节级裁到 API 能接受，只留摘要 + 最后几条。有重试上限，超了就抛错（错误恢复属于另一个话题）。

### 还有两个机制

- **contextCollapse**：一套独立的上下文管理系统，启用时会抑制主动 autocompact。
- **sessionMemoryCompact**：在调 LLM 摘要前，先尝试用已有的"会话记忆"（见第 10 节）做轻量摘要，省一次 API。

**一句话总结 CC 的风格**：客户端全包、分层精细、便宜优先、压缩后努力把"最近最相关"的东西捞回来。

---

## 5. Codex 的压缩：保前缀缓存 + 可以把活儿甩给服务端

Codex 的压缩在 [`core/src/compact.rs`](../../codex/codex-rs/core/src/compact.rs)，核心是 `run_inline_auto_compact_task` 与 `build_compacted_history`。它和 CC 同样有"主动阈值触发"和"撞墙反应式触发"，但有两处鲜明不同。

### 不同点一：从**头部**裁剪，刻意保住"前缀缓存"

`compact.rs` 里有一句点睛注释：

> *"Trim from the beginning to preserve cache (prefix-based) and keep recent messages intact."*

意思是：大模型 API 的 **prompt 缓存是基于前缀的**——只要这次请求的开头和上次一样，那部分就能命中缓存、又快又便宜。所以 Codex 压缩时倾向于**保持前缀稳定、从历史中段/前段裁**，并保留最近消息完整。摘要文本带固定前缀 `SUMMARY_PREFIX`（用 `is_summary` 之类判断一条消息是不是摘要），摘要本身上限 `COMPACT_USER_MESSAGE_MAX_TOKENS = 20,000` token。

压缩完还会 `recompute_token_usage` 重新核账，并给用户一句温馨提示：

> *"Heads up: Long threads and multiple compactions can cause the model to be less accurate. Start a new thread when possible..."*

——Codex 明确承认"压缩有损"，并建议你**开新线程**。这条产品态度本身就是一种取舍。

### 不同点二：**服务端远程压缩**（CC 没有的东西）

Codex 有 [`compact_remote.rs`](../../codex/codex-rs/core/src/compact_remote.rs) 和 `compact_remote_v2.rs`：`run_inline_remote_auto_compact_task`、`trim_function_call_history_to_fit_context_window`。也就是说，**压缩这件事可以发生在服务端**，而不只是客户端本地。

为什么 Codex 能这么做、CC（基本）不这么做？因为协议不同（见 [s09 Responses API](../s09_responses_api/)、[s10 SQ/EQ](../s10_sq_eq_protocol/)）：

- Codex 走 **Responses API**，带服务端会话状态、`x-codex-turn-state` 粘性路由——服务端"知道"这个线程的历史，自然可以在服务端帮你压。
- CC 走 **Messages API**，基本是无状态的：每次把完整 `messages[]` 发过去。状态在客户端，压缩也就只能在客户端做。

模型信息里甚至直接带着压缩相关字段（`client_tests.rs` 里能看到 `"context_window": 272000, "auto_compact_token_limit": null`）——窗口大小和自动压缩阈值是模型能力描述的一部分。

### 工具输出截断：TruncationPolicy

Codex 用 `codex_utils_output_truncation`（`TruncationPolicy`、`approx_token_count`、`truncate_text`）对**单条工具输出**做截断，并有 [`thread_rollout_truncation.rs`](../../codex/codex-rs/core/src/thread_rollout_truncation.rs) 处理 rollout 层的截断。对应 CC 的 L3，但 Codex 更偏"按 token 策略截断 + 记录到 rollout"，CC 更偏"落盘留预览"。

**一句话总结 Codex 的风格**：保前缀缓存、承认有损并劝你开新线程、能把压缩下沉到服务端。

---

## 6. 触发时机对比

| | Claude Code | Codex |
|---|---|---|
| 主动（proactive） | 每轮前跑 L1–L3，token 超 `ctx−maxOut−13k` 触发 L4 摘要 | 每轮前估算，超 `auto_compact_token_limit` 触发 `run_inline_auto_compact_task` |
| 反应式（reactive） | 撞 `prompt_too_long`(413) → `reactive_compact` 激进回退 | 撞 `ContextWindowExceeded` → 内联压缩重试 |
| 压缩位置 | **纯客户端**（4 层流水线） | 客户端 **或 服务端**（remote v2） |
| 主动求助用户 | `/compact` 命令、`SnipTool` | 提示"开新线程"，`Op::Compact` 手动触发 |
| 缓存策略 | 头 3 + 尾 N | **从头裁、保前缀缓存** |

两边都遵循"能不调模型就不调"的便宜优先思路，也都有"主动 + 撞墙兜底"两条路径——这是趋同的工程智慧。分野在**位置（客户端 vs 服务端）**和**缓存哲学**。

---

## 7. 活跃上下文 ≠ 持久记录：Codex 的 Rollout 解耦

这是初学者最容易混淆、却最重要的一点：**"模型现在看得见的东西"和"系统保存下来的东西"，是两回事。**

- **Codex**：[`rollout/`](../../codex/codex-rs/rollout/) 把**完整**历史（每条 item、工具调用、错误、成本、时间）持久化到 **SQLite + zstd 压缩**。这份记录是**全量、不丢**的。压缩只发生在"喂给模型的活跃上下文"上——哪怕活跃上下文被摘要成一句话，rollout 里的完整原文仍在，所以你可以**resume（续接）**、**rewind（回退 N 个回合，`Op::ThreadRollback`）**、甚至重放整个会话。换句话说：**压缩有损，但持久记录无损。**
- **Claude Code**：L4 压缩前会把完整对话写入 `.transcripts/`（JSONL）留档，但这更像"留个备份"，活跃会话主要还是围绕客户端的 `messages[]`。

**为什么 Codex 把这件事做得这么重？** 因为它为云端/无人值守而生（`codex exec`、Codex 云端）：会话可能跑很久、跨机器、要可审计、要能从任意点恢复。把"durable 全量记录"和"lossy 活跃上下文"彻底解耦，是支撑这些场景的地基。

---

## 8. 一个常被忽视的上下文成本：reasoning

Codex 用的是**推理模型**，模型会产出 `reasoning`（推理过程）item。这些 reasoning：

- 会作为协议一等公民**跨轮携带**（甚至是加密内容），让模型"记得自己之前怎么想的"；
- 也因此**占用上下文预算**——推理越多，挤占的窗口越多，压缩压力越大。

CC 这边对应的是 thinking 块。差别在于 Codex 把 reasoning 深度整合进协议、rollout 与压缩流程（压缩时要决定 reasoning 留多少），这是"推理模型 + Responses API"组合带来的、CC 当前形态下没那么突出的一类上下文成本。

---

## 9. 压缩会丢细节，于是有了"记忆层"

压缩是有损的——用户半小时前说的"别用 yarn，用 pnpm"，可能在某次摘要里就被冲淡了。两边都意识到要有一层"不随压缩丢失"的东西。

- **Claude Code**：有一套**记忆子系统**（learn-cc s09）——LLM 选择"什么值得记"（不是 embedding 检索），在 stop hook 时机提取，写入 Markdown 记忆文件，跨压缩、跨会话存活；还区分 User Memory / Session Memory，并有低频的合并去重（"Dream"四层门控）。
- **Codex**：一方面靠**静态的 AGENTS.md**（第 2 节）承载长期项目常识；另一方面有 `ext/memories` crate 和 `Op::SetThreadMemoryMode`——即一个可开关的**线程记忆模式**。再加上**全量 rollout**（第 7 节）本身就是"什么都没真正丢"的兜底。

### 差异与为什么

| | Claude Code | Codex |
|---|---|---|
| 长期项目常识 | CLAUDE.md | AGENTS.md（分层） |
| 跨压缩的动态记忆 | 主动提取的记忆子系统（选什么记、整理巩固） | `ext/memories` + 线程记忆模式；更多依赖全量 rollout |
| "不丢"的底线 | transcript 备份 | **SQLite 全量 rollout，可 resume/rewind** |

**为什么？** CC 把"记忆"做成一个主动、精挑细选的客户端子系统，契合它"本地、交互、长期陪伴一个人"的定位；Codex 更倾向"全量持久化 + 可恢复"作为底线，把"记住关键"更多交给静态 AGENTS.md 和可恢复的 rollout，契合它"云端、可审计、可重放"的定位。

---

## 10. 总对比表

| 维度 | Claude Code | Codex |
|---|---|---|
| 协议状态 | Messages API，**结构性无状态**（状态在客户端） | Responses API，**服务端可有状态**（Codex 对 OpenAI 默认 `store:false` 仍无状态重发，仅 Azure/远程压缩用上服务端状态；精确辨析见 [API 对比](api-message-vs-responses.md) §5） |
| 项目记忆 | CLAUDE.md | AGENTS.md（逐级向上、有上限、可覆盖、跨工具标准） |
| 历史形状 | 嵌套内容块（tool_use/tool_result） | 扁平 item（function_call/_output） |
| 压缩位置 | 纯客户端，四层流水线 | 客户端 **+ 服务端远程压缩** |
| 缓存哲学 | 头 3 + 尾 N | **从头裁，保前缀缓存** |
| 压缩后恢复 | 主动重附最近文件/计划（5 文件×5K/50K 预算） | 保最近 + 摘要前缀；建议开新线程 |
| 工具大输出 | 落盘 + 2000 字预览（>200KB） | TruncationPolicy 按 token 截断 + rollout 截断 |
| 活跃上下文 vs 持久记录 | transcript 备份 | **rollout 全量解耦，可 resume/rewind** |
| reasoning 上下文 | thinking 块 | reasoning item 跨轮携带（占预算） |
| 动态记忆 | 主动提取的记忆子系统 | ext/memories + 全量 rollout 兜底 |
| 有损态度 | 努力恢复、尽量无感 | 明确承认有损、劝开新线程 |

---

## 11. 为什么不同？把账算到底

把上面所有差异收束成一句话：

> **Codex 的上下文策略是"服务端 + 持久化 + 可恢复"，CC 的是"客户端 + 精细流水线 + 主动恢复"。**

根因有三层：

1. **协议决定了可能性。** Responses API 让服务端持有会话状态，于是 Codex *能*把压缩甩到服务端、*能*维护可恢复的 rollout；Messages API 基本无状态，CC 的上下文管理只能、也因此做得极其精细地，全压在客户端。
2. **场景决定了优先级。** Codex 为 headless / CI / 云端而生——会话长、跨机器、要审计、要能从任意点恢复，所以"全量持久 + 可重放"是地基，"保前缀缓存"是成本优化。CC 为本地交互而生——一个人一台机长期协作，所以"压缩后把最相关的东西捞回来、让用户尽量无感"是体验核心。
3. **模型形态带来了新成本。** 推理模型让 reasoning 成为一块要管理的上下文，Codex 把它整合进协议与压缩；这是它与"经典对话历史"管理不同的一笔账。

没有谁更高明——这是两套面向不同世界的、自洽的工程答案。理解了"账是怎么算的"，你就能在自己的 harness 里做出**属于你的场景**的取舍。

---

## 12. 思考

<div class="think">

1. "压缩有损但 rollout 无损"——如果让你设计，你会让模型**知道**自己被压缩过吗？知道了它会不会主动说"我需要重读那个文件"？这是好事还是会变啰嗦？
2. Codex 为了保前缀缓存而"从头裁"，CC 为了留初始上下文而"保头 3"。这两种策略在一个"开头有重要约束、中间是一堆探索"的长会话里，谁会先把约束弄丢？
3. 服务端压缩省了客户端的活，但也意味着"你的对话在服务端被一个你看不见的过程改写了"。在可审计性上，这是加分还是减分？
4. 如果上下文窗口某天变成 1 亿 token，"压缩"这件事会消失吗？还是只是阈值变了、账本不变？（提示：想想成本、延迟、和"大海捞针"的注意力问题。）
5. CC 用一个 LLM 主动"挑什么值得记"，Codex 更靠"全量存着、要用再捞"。哪种更像人类的记忆？哪种更适合 agent？

</div>

---

[← 返回 learn-codex 总览](../README.md) · 相关章节：[s06 AGENTS.md](../s06_agents_md/) · [s07 上下文压缩](../s07_context_compaction/) · [s08 Rollout](../s08_rollout/) · [s09 Responses API](../s09_responses_api/) · [s10 SQ/EQ](../s10_sq_eq_protocol/)
