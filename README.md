# Learn Codex —— 从 0 到 1 拆解 OpenAI Codex 的 Agent Harness

> 🌐 [English](README.en.md) · **中文**

> 用 [learn-claude-code](../learn-claude-code/) 的「分解模式」拆解 **OpenAI Codex**：
> 每章一个『问题 → 方案 → 原理 → 可跑代码』，从一个循环长成一个完整 harness。
> 所有实现都对照本地真源码（`../codex/codex-rs`），并**逐章对比 Claude Code：哪里不同、为什么**。

```
                    THE AGENT PATTERN（两家共通的底座）
                    ===================================

   user ──▶ messages[] ──▶  MODEL  ──▶ response
                              │
                       有 tool_call ?
                       /            \
                     是              否 ──▶ 返回文本
                     │
              执行工具 → 结果回灌 ──▶ 回到 messages[]

   循环不变。变的是：工具长什么样、命令怎么被关、对话怎么被表示、
   会话怎么被存、前端怎么消费事件 —— 这些 harness 选择，正是 Codex 和
   Claude Code 分道扬镳的地方。
```

---

## 这是什么

Claude Code 和 Codex 都是「**模型 + harness**」：Agency（感知-推理-行动的能力）来自模型训练，而 harness 是让模型能在终端/文件系统里干活的那层基础设施。learn-claude-code 已经把 Claude Code 这套 harness 拆给你看了。

**learn-codex 拆另一台载具：OpenAI Codex。** 它和 Claude Code 共享同一个回合循环底座，但在几乎每一个 harness 选择上都做了不同的取舍。本课程的主线就是：**把这些不同一条条摆出来，并解释为什么。**

> 和 learn-claude-code 的关系：那个项目是 Python 从零复刻 Claude Code 的概念；本项目同样用 Python 从零复刻 **Codex** 的概念，并把真源码（Rust，`../codex/codex-rs`）作为事实依据，确保教的是 Codex 真正的做法，而不是换皮。

---

## 🆚 核心对比：Codex vs Claude Code（以及为什么）

| 维度 | Claude Code | OpenAI Codex | 为什么不同 |
|---|---|---|---|
| **语言 / 开放性** | TypeScript/JS，闭源 | **Rust，开源**（你本地这份） | Codex 把性能、内核级沙箱、可审计作为一等公民 → 选 Rust + 开源 |
| **线协议** | Anthropic Messages API（`tool_use` / `tool_result` 内容块） | **OpenAI Responses API**（`function_call` / `function_call_output` item + reasoning） | 各用自家模型与 API；Responses 的 item 流更贴合流式与推理 → 见 [s09] |
| **改文件** | `Edit`（精确串替换）/ `Write` / `MultiEdit` | **`apply_patch`**（`*** Begin Patch` 补丁信封，增/改/删/移一次搞定） | 工具贴合模型被训练的输出习惯；补丁天生可审查/回滚 → 见 [s03] |
| **安全第一道防线** | 审批弹窗 + 路径校验（**应用层**） | **内核强制沙箱**（macOS Seatbelt / Linux Landlock+seccomp，**内核层**） | Codex 要在低/无人工审批下也安全（headless、CI、云端）→ 见 [s05] |
| **整体架构** | 较直接的循环 | **SQ/EQ 队列协议**：提交队列与事件队列解耦，喂多个前端 | 解耦让 TUI / `codex exec` / app-server 共用同一个 core → 见 [s10] |
| **前端** | 单一 CLI/TUI | TUI + `codex exec` 无头 + app-server（IDE 后端），都只是事件消费者 | 多入口（终端、CI、IDE、云）→ 见 [s11] |
| **项目记忆** | `CLAUDE.md` | **`AGENTS.md`**（从 cwd 逐级向上收集并注入） | 同思路、不同约定；Codex 推动了 AGENTS.md 这一跨工具标准 → 见 [s06] |
| **会话持久化** | 会话历史 | **Rollout**（SQLite + zstd，可 resume / rewind / compact） | 可续接、可回滚、可审计 → 见 [s08] |
| **审批粒度** | 危险操作弹窗 | 审批策略 `untrusted/on-failure/on-request/never` + Guardian 自动风险评估 | 要在不同自主度档位间精细切换 → 见 [s04]/[s14] |
| **扩展** | skills / subagents / hooks / MCP | MCP（**客户端 + 服务端**）/ plugins / hooks / guardian | Codex 既连别人，也能**被别人当工具** → 见 [s15] |

### 把"为什么"收成一句话

两家公司下了不同的注：

- **Codex 押"自主"**：开源 Rust + 内核沙箱 + 无头友好。它设想的场景是「**没人盯着也要安全干活**」——CI 流水线、云端 Codex、IDE 后端。没人审批时，唯一可靠的防线是把安全**下沉到内核**，把改动表达成**可审计的补丁**，把会话**存进 rollout** 以便回放。
- **Claude Code 押"协作"**：闭源 + 交互式审批 UX + bash 为中心。它设想的场景是「**人和 agent 并肩**」——危险操作弹窗问你，由人把最后一道关，体验尽量流畅。

而**模型差异直接决定工具差异**：`apply_patch` vs 串替换，不是谁更聪明，而是 OpenAI 和 Anthropic 把不同的"编辑习惯"训进了各自的模型——好的 harness 让工具去贴合模型，而不是反过来。

> 这条主线会在每一章的「🆚 与 Claude Code 的不同」小节里具体展开。

[s03]: ./s03_apply_patch/
[s04]: ./s04_approval/
[s05]: ./s05_sandbox/
[s06]: ./s06_agents_md/
[s08]: ./s08_rollout/
[s09]: ./s09_responses_api/
[s10]: ./s10_sq_eq_protocol/
[s11]: ./s11_frontends/
[s14]: ./s14_guardian/
[s15]: ./s15_mcp/

---

## 课程路线图（17 章主线 + 进阶专题 · 难度单调爬升）

`≈` = 与 Claude Code 大体相同　**⭐** = Codex 独有 / 差异显著（本课程重点）。每个部分内部由易到难，整体半径递增——这套递进借鉴了 learn-claude-code「能力→控制→由内向外→集成→综合」的编排哲学。

**Part 1 · 给它一双手，再套上缰绳**

| 章 | 主题 | 一句格言 | vs CC |
|---|---|---|---|
| [s01](./s01_agent_loop/) | 回合循环 | 一个循环就够了 | ≈ |
| [s02](./s02_tool_use/) | 工具与分发 | 加工具只加 handler | ≈ |
| [s03](./s03_apply_patch/) | apply_patch | 把改动装进补丁信封 | ⭐ |
| [s04](./s04_approval/) | 审批策略 | 先划边界，再给自由 | ⭐ |
| [s05](./s05_sandbox/) | Seatbelt 沙箱 | 应用层挡不如内核层关 | ⭐ |

**Part 2 · agent 的脑子（上下文与记忆）**

| 章 | 主题 | 一句格言 | vs CC |
|---|---|---|---|
| [s06](./s06_agents_md/) | AGENTS.md | 项目规矩，逐级向上捡 | ⭐ |
| [s07](./s07_context_compaction/) | 上下文压缩 | 上下文总会满，腾地方 | ≈ |
| [s08](./s08_rollout/) | Rollout 续接 | 会话存盘，随时续上 | ⭐ |

**Part 3 · 掀开引擎盖（协议与架构）**

| 章 | 主题 | 一句格言 | vs CC |
|---|---|---|---|
| [s09](./s09_responses_api/) | Responses API | 模型到底怎么被调用 | ⭐ |
| [s10](./s10_sq_eq_protocol/) | SQ/EQ 协议 | 提交与事件拆成两队列 | ⭐ |

**Part 4 · 前端与扩展**

| 章 | 主题 | 一句格言 | vs CC |
|---|---|---|---|
| [s11](./s11_frontends/) | 前端：TUI + exec | 前端只是事件的消费者 | ⭐ |
| [s12](./s12_tools_extra/) | 更多工具：plan / web / image | 再加几个工具 | ≈ |
| [s13](./s13_hooks/) | Hooks | 挂在循环上，不写进循环里 | ≈ |

**Part 5 · 进阶（护栏 · 多智能体 · 集成）**

| 章 | 主题 | 一句格言 | vs CC |
|---|---|---|---|
| [s14](./s14_guardian/) | Guardian | 没人时，派个 AI 把关 | ⭐ |
| [s15](./s15_mcp/) | MCP：客户端 + 服务端 | 能力不够就插，也能被别人插 | ⭐ |

**Part 6 · 收尾**

| 章 | 主题 | 一句格言 | vs CC |
|---|---|---|---|
| [s16](./s16_config/) | Config 与 Profiles | 预设档位一键切换 | ⭐ |
| [s17](./s17_comprehensive/) | 综合：迷你 Codex | 机制很多，循环一个 | — |

**进阶专题 · 当一个 agent 不够用**

| 章 | 主题 | 一句格言 | vs CC |
|---|---|---|---|
| [s18](./s18_multiagent/) | 多智能体：agent 通信 | 把通信做进协议，它就成了历史 | ⭐ |

> **当前状态：17 章主线 + 进阶专题 s18 已就位，均离线 `--demo` 可跑（18/18 退出 0）；另有两篇深入长文见下。**

---

## 快速开始

```bash
cd learn-codex
python3 s01_agent_loop/code.py --demo     # 回合循环
python3 s03_apply_patch/code.py --demo    # 招牌编辑工具
python3 s05_sandbox/code.py --demo        # 内核沙箱（macOS 上真实拦截越界写）
```

**默认 `backend=mock`：无需任何 key、无需联网**，就能跑通每章的回合循环与机制演示。

想体验真 Codex 线协议（OpenAI Responses API）：

```bash
pip install -r requirements.txt
cp .env.example .env        # 填入 OPENAI_API_KEY，会自动切到 openai 后端
```

模型调用统一封装在 [codexlib.py](./codexlib.py)（mock / openai 双后端）；每章**独有的机制**都内联在该章 `code.py` 里、可单文件通读。

---

## 浏览 HTML 版

每章 README 可一键转成带样式的网页（零依赖，纯 Python 解析器，支持表格 / 代码块 / `<details>` 折叠 / 引用式链接）：

```bash
python3 build_html.py        # 生成根 index.html + 各章 index.html + assets/codex.css
```

浏览器打开生成的 `index.html` 即可逐章阅读，每章文末都有「思考」反问。解析器本身（[build_html.py](./build_html.py)）也是一份关于 Markdown 解析的小教学样本。

---

## 深入长文（超长篇对比）

除章节外，`docs/` 下有几篇专门的 Claude Code vs Codex 深度对比长文：

- [Messages API vs Responses API 全解](docs/api-message-vs-responses.md) —— 两套**线协议**逐格摊开：请求/对话形状/工具编码/推理携带/状态/流式/失败，每处差异都问「为什么」，全 `codex-rs` 源码行号 grounding。
- [上下文处理全解](docs/context-cc-vs-codex.md) —— 注入 / 压缩 / 截断 / 持久化 / 记忆，逐层对比，含服务端压缩与 rollout 解耦。
- [子代理与多智能体全解](docs/subagent-multiagent-cc-vs-codex.md) —— 分身与组队：CC 的文件收件箱队友团 vs Codex 的有身份线程网络（**可跑骨架见 [s18 多智能体](s18_multiagent/)**）。

---

## 毕业作品：mini-codex（把 18 章拼成一台能跑的载具）

学完 18 章，[mini_codex/](mini_codex/) 把它们**装配成一个分文件夹、模块化的迷你 Codex**——不是又一个单文件 demo，而是 `tools/` `skills/` `hooks/` `safety/` `memory/` `persistence/` `session/` 各自独立成包的完整结构。

```bash
python3 -m mini_codex --demo
```

一条用户请求会穿过 **配置 → AGENTS.md 注入 → 模型 → 钩子 → Guardian → 审批 → 沙箱 → 工具 → rollout 留底** 九道关卡，每道关卡 emit 一个事件打印出来——模型只占其中一步。每个模块都带着对应章节的「生产级」那一层（schema 校验 / retry / fail-closed / 熔断 / append-only…）。详见 [mini_codex/README.md](mini_codex/)。

---

## 设计原则（沿用 learn-claude-code）

1. **每章一个机制**，配一句格言。
2. **代码可单文件通读**：用 `FROM s01（搬运）` / `NEW in sNN` 横幅标出"哪些是上一章带来的、哪些是本章新增"。
3. **离线可跑**：mock 后端让任何人零成本验证。
4. **对照真源码**：每章注明对应的 `codex-rs` 文件，教的是 Codex 真正的做法。
5. **逐章对比 Claude Code**：每章都有「🆚 与 Claude Code 的不同」，讲清差异与原因。
6. **深入对照 + 文末反问**：每章配一段 `<details>`「教学版 vs 真 Codex 源码」深入对照，结尾用「思考」抛出开放式反问。
7. **面向初学者：讲思想，不背词汇**：每章开头用「先把思想说透」把机制背后的道理讲通——多用类比、讲推理链，术语在叙述中自然带出，而非名词清单；「工作原理」里带一段「走一遍」的具体实例。难度按 6 个部分单调爬升。

---

## 致谢

- 形式与教学法致敬 [shareAI-lab/learn-claude-code](https://github.com/shareAI-lab/learn-claude-code)。
- 事实依据来自 [OpenAI Codex](https://github.com/openai/codex) 真源码（本仓库 `../codex`）。
