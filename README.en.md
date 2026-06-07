# Learn Codex — Taking Apart OpenAI Codex's Agent Harness From 0 to 1

> 🌐 **English** · [中文版](README.md)

> Using the "decomposition mode" of [learn-claude-code](../learn-claude-code/) to take apart **OpenAI Codex**:
> each chapter is one "problem → solution → how it works → runnable code," growing from a single loop into a complete harness.
> Every implementation is checked against the local real source (`../codex/codex-rs`), and **compared chapter by chapter with Claude Code: where it differs, and why**.

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

## What this is

Both Claude Code and Codex are "**model + harness**": agency (the capacity to perceive, reason, and act) comes from the model's training, while the harness is the infrastructure layer that lets the model get work done in a terminal / file system. learn-claude-code has already taken apart Claude Code's harness for you.

**learn-codex takes apart another vehicle: OpenAI Codex.** It shares the same agent-loop foundation with Claude Code, but makes different trade-offs on nearly every harness choice. The through-line of this course is exactly this: **lay out these differences one by one, and explain why.**

> Relationship to learn-claude-code: that project reproduces the concepts of Claude Code from scratch in Python; this project likewise reproduces the concepts of **Codex** from scratch in Python, and uses the real source (Rust, `../codex/codex-rs`) as ground truth, ensuring that what it teaches is how Codex actually does things, rather than a reskin.

---

## 🆚 The core comparison: Codex vs Claude Code (and why)

| Dimension | Claude Code | OpenAI Codex | Why it differs |
|---|---|---|---|
| **Language / openness** | TypeScript/JS, closed-source | **Rust, open-source** (the copy you have locally) | Codex treats performance, kernel-level sandboxing, and auditability as first-class citizens → chose Rust + open-source |
| **Wire protocol** | Anthropic Messages API (`tool_use` / `tool_result` content blocks) | **OpenAI Responses API** (`function_call` / `function_call_output` items + reasoning) | Each uses its own model and API; the Responses item stream fits streaming and reasoning better → see [s09] |
| **Editing files** | `Edit` (exact string replacement) / `Write` / `MultiEdit` | **`apply_patch`** (`*** Begin Patch` patch envelope — add / change / delete / move in one shot) | The tool fits the output habits the model was trained on; patches are inherently reviewable / revertible → see [s03] |
| **First line of safety defense** | Approval popup + path validation (**application layer**) | **Kernel-enforced sandbox** (macOS Seatbelt / Linux Landlock+seccomp, **kernel layer**) | Codex needs to be safe even under low / no human approval (headless, CI, cloud) → see [s05] |
| **Overall architecture** | A fairly direct loop | **SQ/EQ queue protocol**: submission queue and event queue decoupled, feeding multiple frontends | Decoupling lets TUI / `codex exec` / app-server share the same core → see [s10] |
| **Frontend** | A single CLI/TUI | TUI + `codex exec` headless + app-server (IDE backend), all merely event consumers | Multiple entry points (terminal, CI, IDE, cloud) → see [s11] |
| **Project memory** | `CLAUDE.md` | **`AGENTS.md`** (collected level by level up from cwd and injected) | Same idea, different convention; Codex pushed AGENTS.md as a cross-tool standard → see [s06] |
| **Session persistence** | Conversation history | **Rollout** (SQLite + zstd, can resume / rewind / compact) | Resumable, revertible, auditable → see [s08] |
| **Approval granularity** | Popup for dangerous operations | Approval policies `untrusted/on-failure/on-request/never` + Guardian automatic risk assessment | Needs fine-grained switching between different autonomy levels → see [s04]/[s14] |
| **Extensions** | skills / subagents / hooks / MCP | MCP (**client + server**) / plugins / hooks / guardian | Codex both connects to others and can **be used as a tool by others** → see [s15] |

### Boiling the "why" down to one sentence

The two companies placed different bets:

- **Codex bets on "autonomy"**: open-source Rust + kernel sandbox + headless-friendly. The scenario it imagines is "**get work done safely even when no one is watching**" — CI pipelines, cloud Codex, IDE backends. When no one is approving, the only reliable defense is to **push safety down into the kernel**, to express changes as **auditable patches**, and to **store sessions in rollout** so they can be replayed.
- **Claude Code bets on "collaboration"**: closed-source + interactive approval UX + bash-centric. The scenario it imagines is "**human and agent side by side**" — dangerous operations pop up to ask you, the human stands as the last gate, and the experience stays as smooth as possible.

And **model differences directly determine tool differences**: `apply_patch` vs string replacement is not about who's smarter, but about OpenAI and Anthropic training different "editing habits" into their respective models — a good harness makes the tool fit the model, not the other way around.

> This through-line gets fleshed out concretely in every chapter's "🆚 How it differs from Claude Code" section.

[s03]: ./s03_apply_patch/README.en.md
[s04]: ./s04_approval/README.en.md
[s05]: ./s05_sandbox/README.en.md
[s06]: ./s06_agents_md/README.en.md
[s08]: ./s08_rollout/README.en.md
[s09]: ./s09_responses_api/README.en.md
[s10]: ./s10_sq_eq_protocol/README.en.md
[s11]: ./s11_frontends/README.en.md
[s14]: ./s14_guardian/README.en.md
[s15]: ./s15_mcp/README.en.md

---

## Course roadmap (17-chapter main line + advanced topics · difficulty climbs monotonically)

`≈` = broadly the same as Claude Code　**⭐** = unique to Codex / significantly different (the focus of this course). Each part goes from easy to hard internally, with the overall radius increasing — this progression borrows from learn-claude-code's "capability → control → inside-out → integration → synthesis" arrangement philosophy.

**Part 1 · Give it a pair of hands, then put on the reins**

| Ch | Topic | One-line motto | vs CC |
|---|---|---|---|
| [s01](./s01_agent_loop/README.en.md) | Agent loop | One loop is enough | ≈ |
| [s02](./s02_tool_use/README.en.md) | Tools and dispatch | Adding a tool just adds a handler | ≈ |
| [s03](./s03_apply_patch/README.en.md) | apply_patch | Pack changes into a patch envelope | ⭐ |
| [s04](./s04_approval/README.en.md) | Approval policy | Draw the boundary first, then grant freedom | ⭐ |
| [s05](./s05_sandbox/README.en.md) | Seatbelt sandbox | Blocking at the app layer is no match for closing at the kernel layer | ⭐ |

**Part 2 · The agent's brain (context and memory)**

| Ch | Topic | One-line motto | vs CC |
|---|---|---|---|
| [s06](./s06_agents_md/README.en.md) | AGENTS.md | Project rules, picked up level by level | ⭐ |
| [s07](./s07_context_compaction/README.en.md) | Context compaction | Context always fills up — make room | ≈ |
| [s08](./s08_rollout/README.en.md) | Rollout resume | Save the session, pick it back up anytime | ⭐ |

**Part 3 · Lifting the engine hood (protocol and architecture)**

| Ch | Topic | One-line motto | vs CC |
|---|---|---|---|
| [s09](./s09_responses_api/README.en.md) | Responses API | How the model actually gets called | ⭐ |
| [s10](./s10_sq_eq_protocol/README.en.md) | SQ/EQ protocol | Split submissions and events into two queues | ⭐ |

**Part 4 · Frontends and extensions**

| Ch | Topic | One-line motto | vs CC |
|---|---|---|---|
| [s11](./s11_frontends/README.en.md) | Frontends: TUI + exec | A frontend is merely a consumer of events | ⭐ |
| [s12](./s12_tools_extra/README.en.md) | More tools: plan / web / image | Add a few more tools | ≈ |
| [s13](./s13_hooks/README.en.md) | Hooks | Hang on the loop, don't write into the loop | ≈ |

**Part 5 · Advanced (guardrails · multi-agent · integration)**

| Ch | Topic | One-line motto | vs CC |
|---|---|---|---|
| [s14](./s14_guardian/README.en.md) | Guardian | When no one's around, send an AI to stand guard | ⭐ |
| [s15](./s15_mcp/README.en.md) | MCP: client + server | If you lack a capability, plug one in — and be pluggable by others | ⭐ |

**Part 6 · Wrap-up**

| Ch | Topic | One-line motto | vs CC |
|---|---|---|---|
| [s16](./s16_config/README.en.md) | Config and Profiles | Switch preset levels with one key | ⭐ |
| [s17](./s17_comprehensive/README.en.md) | Synthesis: mini Codex | Many mechanisms, one loop | — |

**Advanced topic · When one agent isn't enough**

| Ch | Topic | One-line motto | vs CC |
|---|---|---|---|
| [s18](./s18_multiagent/README.en.md) | Multi-agent: agent communication | Build communication into the protocol, and it becomes history | ⭐ |

> **Current status: the 17-chapter main line + advanced topic s18 are all in place, all runnable offline with `--demo` (18/18 exit 0); plus two in-depth long reads, see below.**

---

## Quick start

```bash
cd learn-codex
python3 s01_agent_loop/code.py --demo     # 回合循环
python3 s03_apply_patch/code.py --demo    # 招牌编辑工具
python3 s05_sandbox/code.py --demo        # 内核沙箱（macOS 上真实拦截越界写）
```

**Default `backend=mock`: no key needed, no network needed**, and you can run through each chapter's agent loop and mechanism demo.

To experience the real Codex wire protocol (OpenAI Responses API):

```bash
pip install -r requirements.txt
cp .env.example .env        # 填入 OPENAI_API_KEY，会自动切到 openai 后端
```

Model calls are uniformly wrapped in [codexlib.py](./codexlib.py) (mock / openai dual backends); each chapter's **unique mechanism** is inlined in that chapter's `code.py` and can be read through as a single file.

---

## Browse the HTML version

Each chapter's README can be turned into a styled web page with one command (zero dependencies, a pure-Python parser, supporting tables / code blocks / `<details>` folding / reference-style links):

```bash
python3 build_html.py        # 生成根 index.html + 各章 index.html + assets/codex.css
```

Open the generated `index.html` in a browser to read chapter by chapter; each chapter ends with a "Think it over" question. The parser itself ([build_html.py](./build_html.py)) is also a small teaching sample on Markdown parsing.

---

## In-depth long reads (very long comparisons)

Beyond the chapters, `docs/` contains a few dedicated, in-depth Claude Code vs Codex comparison long reads:

- [The complete guide to Messages API vs Responses API](docs/api-message-vs-responses.en.md) — the two **wire protocols** laid out cell by cell: request / conversation shape / tool encoding / reasoning carriage / state / streaming / failure, asking "why" at every difference, all grounded in `codex-rs` source line numbers.
- [The complete guide to context handling](docs/context-cc-vs-codex.en.md) — injection / compaction / truncation / persistence / memory, compared layer by layer, including server-side compaction and rollout decoupling.
- [The complete guide to subagents and multi-agents](docs/subagent-multiagent-cc-vs-codex.en.md) — clones and teams: CC's file-inbox teammate squad vs Codex's network of threads with identity (**runnable skeleton in [s18 multi-agent](s18_multiagent/README.en.md)**).

---

## Graduation work: mini-codex (assembling 18 chapters into one runnable vehicle)

After finishing the 18 chapters, [mini_codex/](mini_codex/README.en.md) **assembles** them into a folder-split, modular mini Codex — not yet another single-file demo, but a complete structure where `tools/` `skills/` `hooks/` `safety/` `memory/` `persistence/` `session/` are each their own independent package.

```bash
python3 -m mini_codex --demo
```

A single user request passes through nine gates — **config → AGENTS.md injection → model → hooks → Guardian → approval → sandbox → tools → rollout persistence** — each gate emitting an event that gets printed; the model occupies only one of those steps. Each module carries the "production-grade" layer of its corresponding chapter (schema validation / retry / fail closed / circuit breaker / append-only…). See [mini_codex/README.md](mini_codex/README.en.md) for details.

---

## Design principles (carried over from learn-claude-code)

1. **One mechanism per chapter**, paired with a motto.
2. **Code readable as a single file**: use `FROM s01（搬运）` / `NEW in sNN` banners to mark "which parts came from the previous chapter, which are new in this one."
3. **Runnable offline**: the mock backend lets anyone verify at zero cost.
4. **Checked against the real source**: each chapter cites the corresponding `codex-rs` file, teaching how Codex actually does things.
5. **Compared chapter by chapter with Claude Code**: every chapter has a "🆚 How it differs from Claude Code," explaining the difference and the reason.
6. **In-depth cross-check + closing question**: each chapter comes with a `<details>` "teaching version vs real Codex source" in-depth cross-check, and ends with a "Think it over" open-ended question.
7. **For beginners: teach ideas, don't memorize vocabulary**: each chapter opens with "Get the idea straight first" to talk through the reasoning behind the mechanism — using analogies and chains of reasoning, with terminology brought out naturally in the narrative rather than as a glossary of nouns; "How it works" includes a "Walk through it" concrete example. Difficulty climbs monotonically across the 6 parts.

---

## Acknowledgments

- Form and pedagogy pay homage to [shareAI-lab/learn-claude-code](https://github.com/shareAI-lab/learn-claude-code).
- Factual ground truth comes from the real source of [OpenAI Codex](https://github.com/openai/codex) (this repo's `../codex`).
