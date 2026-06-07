# mini-codex —— 把 18 章拼成一台能跑的载具

这是 learn-codex 的**毕业作品**：不是又一个单文件 demo，而是一个**分文件夹、模块化**的迷你 Codex——把前 18 章每一章的机制，各自落到一个独立的包里，再装配成一条完整的请求流水线。

```bash
python3 -m mini_codex --demo
```

一条用户请求会**穿过九道关卡**，每道关卡 emit 一个事件打印出来：

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

## 目录结构（每个关注点独立成包）

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

## 它怎么工作（一条请求的旅程）

`agent.py` 的 `_handle_tool_call` 就是 [s17](../s17_comprehensive/) 讲的那条流水线，落成代码：

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

**模型只出现在「想做什么」那一步；其余每一道关卡都是 harness。** 这正是全课程想证明的：Agency 来自模型，而你——harness 工程师——负责造好它栖居与行动的整个世界。

## 生产级落点

这台载具不只是"能跑"，每个模块都带着对应章节的「生产级」那一层：schema 校验 + 出错回灌（tools）、retry/退避（model）、deny-default + 禁网（sandbox）、fail-closed + 熔断（guardian）、BANNED_PREFIX（approval）、append-only + flush（rollout）、配置边界校验（config）、钩子信任 + 超时（hooks）、步数封顶 + 可中断（loop）。

## 注

- 离线 mock：`model/client.py` 的 `Model` 脚本化地驱动 demo（建一个文件）。接真模型时，把 `respond` 换成真正的 OpenAI Responses 调用即可（参考 learn-codex 根目录的 `codexlib.py`）。
- demo 在系统临时目录里跑、跑完自清，不在仓库里留痕。
