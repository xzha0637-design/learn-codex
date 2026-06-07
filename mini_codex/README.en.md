# mini-codex — assembling 18 chapters into a vehicle that actually runs

> 🌐 **English** · [中文版](README.md)

This is the **capstone** of learn-codex: not yet another single-file demo, but a **multi-folder, modular** mini Codex — taking the mechanism from each of the first 18 chapters, landing each one in its own standalone package, and then assembling them into a complete request pipeline.

```bash
python3 -m mini_codex --demo
```

A single user request **passes through nine gates**, and each gate emits an event that gets printed:

```
▶  turn started
   💭 reasoning   用户想创建一个文件，应当调用 apply_patch。
   🛡  guardian   risk=medium outcome=escalate
   🔐 approval    decision=ask → demo 自动放行
   🔧 tool begin  apply_patch
   ✅ tool end    A greeting.txt (+2 行)
   💬 message     完成 ✅ 已创建 greeting.txt。
■  turn complete
```

## Directory structure (each concern as its own package)

```
mini_codex/
├── __main__.py         入口：python -m mini_codex --demo
├── agent.py            把一切装配成流水线（_handle_tool_call 就是 s17 那条传送带）
├── config/             分层配置解析 + 边界校验            ← s16
│   └── loader.py
├── model/              Responses 风格模型客户端 + 重试      ← s09
│   └── client.py
├── protocol/           Event 总线（EQ：core 产、前端消费）  ← s10
│   └── events.py
├── memory/             AGENTS.md 分层注入 + 字节预算        ← s06
│   └── agents_md.py
├── tools/              工具注册表 + 各工具（每个一文件）
│   ├── registry.py     schema 校验 + 出错回灌（ToolRouter）← s02
│   ├── shell.py        跑命令（自动进沙箱）                ← s01/s05
│   ├── apply_patch.py  补丁信封（模糊匹配 + 原子）         ← s03
│   ├── fs.py           read/write/list_dir                ← s02
│   └── plan.py         任务清单                            ← s12
├── skills/             技能库（markdown 能力包，按需注入）
│   ├── loader.py
│   └── builtin/code_review.md
├── hooks/              钩子注册表 + 信任/超时              ← s13
│   ├── registry.py
│   └── builtin.py
├── safety/             三层防线
│   ├── approval.py     审批策略 + 会话缓存 + BANNED_PREFIX ← s04
│   ├── sandbox.py      Seatbelt 内核沙箱                   ← s05
│   └── guardian.py     风险评估 + fail-closed + 熔断       ← s14
├── persistence/        rollout 留底（append-only）         ← s08
│   └── rollout.py
└── session/            回合循环 + 上下文压缩
    ├── loop.py         封顶 + 可中断 + 重试 + emit 事件    ← s01/s07/s09
    └── compaction.py   超预算压成摘要                      ← s07
```

## How it works (the journey of a single request)

`agent.py`'s `_handle_tool_call` is exactly the pipeline described in [s17](../s17_comprehensive/README.en.md), turned into code:

```
用户请求
  → 配置解析（profile：approval/sandbox 档位）            config/
  → 注入 AGENTS.md（分层 + 字节预算）+ 技能目录            memory/ skills/
  → 模型（带重试）产出「想做什么」                          model/ session/loop
  → 钩子 pre_tool（可否决/改写）                            hooks/
  → Guardian 风险评估（low放行 / critical拒 / 升级）        safety/guardian
  → 审批门（升级的交给策略/用户）                            safety/approval
  → 工具注册表执行（schema 校验 + 沙箱）                     tools/ safety/sandbox
  → rollout 留底（append-only）                            persistence/
  → 事件流喂给前端                                          protocol/
```

**The model appears only at the "what does it want to do" step; every other gate is harness.** This is exactly what the whole course set out to prove: agency comes from the model, and you — the harness engineer — are responsible for building the entire world it inhabits and acts within.

## Where production-grade lands

This vehicle isn't merely "able to run" — every module carries the "production-grade" layer from its corresponding chapter: schema validation + error feedback (tools), retry/backoff (model), deny-default + no network (sandbox), fail closed + circuit breaker (guardian), BANNED_PREFIX (approval), append-only + flush (rollout), config boundary validation (config), hook trust + timeout (hooks), step cap + interruptible (loop).

## Notes

- Offline mock: the `Model` in `model/client.py` drives the demo via a script (creating a file). To wire up a real model, just swap `respond` for an actual OpenAI Responses call (see `codexlib.py` in the learn-codex root).
- The demo runs in the system temp directory and cleans up after itself, leaving no trace in the repo.
