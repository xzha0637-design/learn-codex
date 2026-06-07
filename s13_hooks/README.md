# s13: Hooks — 在回合的关键时刻挂钩子

> *"扩展点让用户改行为，而不必 fork 掉 core。"*

[learn-codex 总览](../README.md) · [更多工具：plan / web_search / view_image](../s12_tools_extra/) → **本章** → [Guardian](../s14_guardian/)

---

## 先把思想说透：为什么要在循环上「预留插口」

回合循环（[s01](../s01_agent_loop/)）跑得好好的，但每个人、每个团队都有自己的「私心」：「**任何** `rm` 命令在我们这儿都不许跑」「每次工具调用前先记一条审计日志」「回合一结束自动跑一遍格式化」。这些需求五花八门、还因人而异。问题来了——怎么满足这些定制，又不把那个干净的循环搞脏？想通下面三个道理，钩子（hook）这个机制就不再是个新名词，而是一个你会觉得「本该如此」的设计。

**道理一：把别人的定制塞进 core，是一条死路。**
最直接的做法是：每来一个需求就往循环里加一个 `if`。可需求是无穷的、而且**因人而异**——你的团队禁 `rm`，他的团队禁 `curl`，再加几条循环就被业务逻辑塞爆，而且这些 `if` 对别人毫无意义。更糟的退路是「fork」：core 不给你留余地，你想定制就只能把整个项目拷一份、改源码、自己维护——别人一升级你就得手动合并，苦不堪言。两条路都不通，说明**定制不该写进 core，但 core 又必须给定制留出位置**。

**道理二：那就在循环的关键时刻「预留插口」，让用户把自己的代码挂上去。**
巧办法是：core 不去猜你想干什么，只在几个**固定时刻**留下挂载点，到点了就回头喊一声「这里有人要插话吗？」。这些固定时刻就是**触发点**，挂上去的那一小段代码就是**钩子**。这就像发动机外壳上预先钻好几个螺丝孔——core 厂家不知道你要装什么，但留好了孔，你想拧什么零件上去都行。本章留了四个孔：`pre_turn`（回合开始前）、`pre_tool`（每次用工具前）、`post_tool`（用完工具后）、`post_turn`（回合结束后）。从此「禁 `rm`」只是你挂在 `pre_tool` 上的一段代码，core 一行没动，也不必知道你的私心。

**道理三（最关键）：插口不能只能「旁观」，得能「拦」、能「改」——但拦完要让模型知情。**
如果钩子只能看不能动，那它顶多是个日志器。真正有用的插口得有**权力**：`pre_tool` 这个孔最特殊，挂在它上面的钩子能在命令真正执行前喊一声「**不行**」，把这次调用**否决**掉（好比安检员有权拦下一件行李）；或者不否决、但**偷偷改写**参数再放行——比如给命令强制加上 `--dry-run`（好比安检员没没收你的水，只是把瓶盖拧紧了放行）。但这里有个容易被忽略的关键：钩子否决之后**不能默默丢弃**，而要把「被否决了、理由是 X」当成这次工具调用的结果**回灌给模型**。为什么？因为模型本以为它跑了那条命令，你不告诉它「被拦了」，它就会基于一个错误的世界观继续往下走。回灌之后它才知道「这条路堵了」，转头换个法子。**所以钩子不是背着模型搞小动作，而是在和模型「对话」。**

把这三点连起来：**钩子 = 在 core 预留的几个时刻挂自己的代码，不 fork 就能定制；其中 `pre_tool` 最有权力，能否决、能改写，且否决理由会回灌给模型。** 这正是「让用户改行为，而不必 fork 掉 core」这句话落到实处的样子。

## 问题

回合循环跑得好好的，但每个团队都有自己的「私心」：

- 「**任何** `rm` 命令在我们这儿都不许跑，哪怕模型觉得安全。」
- 「每次工具调用前，先把命令记进审计日志。」
- 「回合一结束，自动跑一遍 `prettier` 把改动格式化掉。」
- 「这个工具的参数，我想在执行前悄悄改写一下（比如强制加 `--dry-run`）。」

这些需求五花八门，而且**因人而异**。如果每来一个就往 core 里加一个 `if`，core 会被业务逻辑塞爆；更糟的是，用户为了一个小定制，只能去 fork 整个 Codex。

需要的是一组**稳定的扩展点**：在回合的关键时刻"开个口子"，让用户挂上自己的逻辑——不碰 core，就能观察、否决、甚至改写。

## 解决方案

一个钩子注册表 + 四个**触发点（fire point）**。钩子是按事件名注册的普通可调用对象，循环跑到对应时刻就 `fire` 它们：

```
   run_turn(messages)
        │
   ┌────� fire("pre_turn") ─────────────────────────┐  回合开始
   │                                                 │
   │   model.respond(...)                            │
   │        │                                         │
   │   每个 tool_call:                                 │
   │        │                                         │
   │   ┌─ fire("pre_tool") ──▶ {block?} {command?}    │  ★ 可否决 / 可改写
   │   │        │                                      │
   │   │   block=True ─▶ 不执行，把理由回灌给模型 ──────┤
   │   │   command=.. ─▶ 改写后再执行                    │
   │   │        ▼                                       │
   │   │   run_shell(command)                          │
   │   │        ▼                                       │
   │   └─ fire("post_tool") ─────────────────────────┤  工具调用后
   │                                                  │
   └──── fire("post_turn") ──────────────────────────┘  回合结束
```

`pre_tool` 是最有权力的一个：它的返回值能**否决**这次工具调用（`block`），或**改写**它的参数（`command`）。其余三个触发点只做副作用（日志、格式化……）。

## 工作原理

看 [code.py](code.py)，三块。

**第 1 块** — 注册表 + `fire()`。钩子按事件名分桶；`fire` 顺序执行，并对 `pre_tool` 特判否决/改写：

```python
HOOKS = {"pre_turn": [], "pre_tool": [], "post_tool": [], "post_turn": []}

def register(event, fn): HOOKS[event].append(fn)

def fire(event, ctx):
    for fn in HOOKS[event]:
        out = fn(ctx) or {}
        if event == "pre_tool":
            if out.get("block"):   return {"block": True, "reason": out.get("reason")}
            if "command" in out:   ctx = {**ctx, "command": out["command"]}  # 改写
    ...
```

「任一钩子否决就停在第一个否决」对应真源码 `Hooks::dispatch` 里 `if should_abort_operation { break }`（[registry.rs:94](../../codex/codex-rs/hooks/src/registry.rs)）。

**第 2 块** — 把四个触发点织进 s01 的循环。骨架没变，只是在四处插了 `fire`：

```python
def run_turn(messages):
    fire("pre_turn", {"messages": messages})
    while True:
        resp = model.respond(...)
        ...
        for tc in resp.tool_calls:
            gate = fire("pre_tool", {"tool": tc.name, "command": command})
            if gate.get("block"):
                messages.append(tool_output_item(tc.call_id, f"[blocked by hook] {gate['reason']}"))
                continue                      # ← 否决：不执行，理由回灌给模型
            if "command" in gate: command = gate["command"]   # ← 改写
            output = HANDLERS[tc.name](command=command)
            fire("post_tool", {"output": output, ...})
    fire("post_turn", {"messages": messages})
```

注意被否决时，我们**把否决理由作为工具结果回灌给模型**——模型于是知道"这条路被堵了"，会换个法子。这和真源码一致。

**第 3 块** — 两个示例钩子：

```python
def block_rm(ctx):                                   # pre_tool：否决任何含 rm 的命令
    if "rm" in (ctx.get("command") or "").split():
        return {"block": True, "reason": "policy: `rm` is not allowed"}
    return {}

def log_post_turn(ctx):                              # post_turn：打一行日志
    print(f"[hook] post_turn: 对话现在有 {len(ctx['messages'])} 个 item")
```

`--demo` 跑一个 canned 回合（不走模型）：模型先想 `echo`、再想 `rm -rf build`。结果 `echo` 正常执行，`rm` 被 `block_rm` 在执行前否决，`post_turn` 钩子打日志收尾。`rm` 从未真正运行。

**走一遍。** 我们跟着这个 canned 回合，盯住那个最关键的触发点 `pre_tool`，看**每一步传给钩子的数据长什么样**、钩子**回了什么**、core **据此做了什么**。已注册两个钩子：`block_rm`（挂在 `pre_tool`）和 `log_post_turn`（挂在 `post_turn`）。

1. **回合开始**，core 触发 `pre_turn`。本 demo 没在这点挂钩子，略过。

2. 模型的第一个动作：跑 `echo hi`。执行前，core 把这次工具调用的信息打包成一个 **ctx 字典**，喂给每个 `pre_tool` 钩子：
   ```json
   { "tool": "shell", "command": "echo hi" }
   ```
   `block_rm` 检查命令里有没有 `rm`——没有，于是**返回空字典 `{}`**（意思是"我不拦、也不改"）。core 看返回里没有 `block`，照常执行，得到输出 `hi`，再触发 `post_tool`。

3. 模型的第二个动作：跑 `rm -rf build`。同样先打包 ctx 喂给 `pre_tool`：
   ```json
   { "tool": "shell", "command": "rm -rf build" }
   ```
   这次 `block_rm` 发现命令里有 `rm`，**返回一张"否决"纸条**：
   ```json
   { "block": true, "reason": "policy: `rm` is not allowed" }
   ```
   core 看到 `block: true`，**立刻不执行**这条命令。`rm -rf build` 从头到尾没被运行过——这就是钩子的否决权。

4. **关键的下一步：回灌。** core 没有默默跳过，而是把否决理由当成这次工具调用的"结果"，追加进对话历史：
   ```json
   { "type": "tool_output", "call_id": "...", "output": "[blocked by hook] policy: `rm` is not allowed" }
   ```
   这条会喂回给模型。**为什么？** 因为模型本以为它跑了 `rm`，如果不告诉它"被拦了"，它会基于一个错误的世界观继续往下走。回灌之后，模型读到"这条路被策略堵了"，就会改走别的路（比如换个不删文件的做法）。

5. **回合结束**，core 触发 `post_turn`，轮到 `log_post_turn` 钩子。它拿到的 ctx 里有整个对话：
   ```json
   { "messages": [ ... 此刻所有 item ... ] }
   ```
   它只是打一行日志（`对话现在有 N 个 item`），不改任何东西——这就是"只做副作用"的触发点的典型用法。

把这一趟连起来看：**同一个 `pre_tool` 触发点，对 `echo` 放行、对 `rm` 否决**，区别只在钩子返回的那张纸条；而否决之后那一步**回灌**，是让模型"知情并改道"的关键。`rm` 全程未跑，core 一行没改——这正是"不 fork 也能定制"的样子。

## 生产级：钩子是外部命令——会挂、会崩、可能恶意

教学版的钩子是进程内的 Python 函数，乖。但真 Codex 的钩子是**用户机器上的任意外部命令**——这是它的威力，也是它最大的风险面：一个藏在项目目录里的恶意 `PreToolUse` 钩子，能在你**每次工具调用**时执行任意代码。所以生产级的钩子系统，重点根本不在"怎么触发"，而在"怎么不让它害你"。两道关（本章 [code.py](code.py) 的 `run_hook_safely` 演示了）：

### 一、信任：哈希不匹配的钩子不执行

你不能因为一个项目的 `.codex/hooks` 里写了个钩子，就无条件在自己机器上跑它。真 Codex 给每个钩子记一个 **`trusted_hash`（SHA-256）**（[`config_rules.rs`](../../codex/codex-rs/hooks/src/config_rules.rs)）——只有哈希匹配（你信任过的那一版）才执行；项目里偷偷改了钩子内容、哈希对不上，就**不跑**。想全局放行可设 `bypass_hook_trust`（`registry.rs:33`），但那等于自己拆了这道闸。

```
(a) 信任校验：_evil_hook（untrusted）→ {'_skipped': 'untrusted hook 未执行（哈希不匹配）'}
```

### 二、超时 + fail-closed：挂死的钩子不能冻住每次工具调用

钩子是外部进程，会卡死（死循环、等一个永不来的网络响应）。若同步等它，你的 agent 每次工具调用都跟着冻住。真 Codex 给钩子配 `timeout_sec`（[`declarations.rs:69`](../../codex/codex-rs/hooks/src/declarations.rs)）。而对一个**安全用途**的钩子（`PreToolUse`），超时/崩溃该倒向哪边？**fail-closed = 当作否决**——宁可拦错一个正常调用，也不能因为把关器挂了就把危险操作放过去（和 [s14](../s14_guardian/) Guardian、[s05](../s05_sandbox/) 沙箱同一个气质）。

```
(b) 超时 + fail-closed：_slow_hook（sleep 0.5s / 超时 0.1s）→ {'block': True, 'reason': 'hook 超时，fail-closed 当作否决'}
```

> 一句话：钩子的生产级，不在"能挂多少回调"，而在**把"别人的代码跑在你的回合里"这件事变得可控**——只跑可信的、给它设上限、它出事就倒向安全。

## 🆚 与 Claude Code 的不同

两边**都有 hooks**，这一章是少数 `≈`（大体相同）的章节之一——钩子是个通用的扩展模式。但同与不同各有落点：

| | Claude Code | Codex |
|---|---|---|
| 有没有 hooks | 有 | 有 |
| 触发点 | PreToolUse / PostToolUse / Stop / Notification ... | PreToolUse / PostToolUse / Stop / SessionStart / UserPromptSubmit ...（10 个）|
| 否决方式 | 钩子退出码 / JSON 决定 | 退出码 `2` 或 `permissionDecision:"deny"` → `should_block` |
| 长在哪套体系 | 独立的 hooks 配置 | 与**审批策略（[s04]）/ Guardian（[s14]）/ 事件协议（[s10]）同一套**：钩子的否决、审批的 Prompt、Guardian 的风险评估，都是同一条"执行前的把关链"上的环节 |

**为什么？** 因为扩展点的价值就是**让用户在不 fork core 的前提下定制行为**——这一点两家想法一致，所以机制趋同。Codex 的差异不在"有没有钩子"，而在钩子**和谁长在一起**：它把 `PreToolUse` 钩子、`AskForApproval` 审批、Guardian 自动评审拼成同一条"工具调用 → 执行"之间的把关流水线。这条流水线越完整，Codex 就越能在低/无人工干预下（headless / CI / 云）安全地跑——这正是本课程的主线。Claude Code 的钩子更像一个相对独立的、围绕交互式体验的扩展层。

## 深入：教学版 vs 真 Codex 源码

本章的钩子是「同进程里的 Python 函数」；真 Codex 的钩子是「另起进程跑的外部命令」，并且兼容 Claude Code 的 hooks JSON 约定。下面拆开。

<details>
<summary>一、本章四点 = 真源码十个事件名的子集</summary>

真源码 `hooks/src/lib.rs:19` 定义了 10 个事件名（`HOOK_EVENT_NAMES`）：

```rust
pub const HOOK_EVENT_NAMES: [&str; 10] = [
    "PreToolUse", "PermissionRequest", "PostToolUse",
    "PreCompact", "PostCompact", "SessionStart",
    "UserPromptSubmit", "SubagentStart", "SubagentStop", "Stop",
];
```

本章的 `pre_tool` / `post_tool` 直接对应 `PreToolUse` / `PostToolUse`；`pre_turn` ≈ `UserPromptSubmit`（回合由用户输入开启）、`post_turn` ≈ `Stop`（回合收尾）。其余如 `PreCompact`/`PostCompact`（上下文压缩前后，见 [s07]）、`SubagentStart`/`SubagentStop`（子 agent 生命周期）、`SessionStart`、`PermissionRequest` 本章未做。

还有个细节：只有 8 个事件的 **matcher 字段**才有意义（`HOOK_EVENT_NAMES_WITH_MATCHERS`）——钩子可以用一个正则 matcher（如 `^Bash$`）只对特定工具触发，本章的 `block_rm` 是无差别对所有工具触发的简化版。

</details>

<details>
<summary>二、pre_tool 的真实契约：能否决，也能改写</summary>

真源码 `pre_tool_use.rs:37` 的输出结构，比本章的 `{block, command}` 字典丰富得多：

```rust
pub struct PreToolUseOutcome {
    pub should_block: bool,              // 否决这次工具调用
    pub block_reason: Option<String>,    // 否决理由（回灌给模型）
    pub additional_contexts: Vec<String>,// 给模型追加上下文
    pub updated_input: Option<Value>,    // 改写工具参数（本章的 command 改写）
    pub hook_events: Vec<HookCompletedEvent>,
}
```

钩子怎么表达否决/改写？它是一个外部命令，约定（兼容 Claude Code）是：

| 钩子做了什么 | 结果 |
|---|---|
| 退出码 `2`，stderr 写理由 | `should_block = true`，理由进 `block_reason` |
| stdout 输出 `{"hookSpecificOutput":{"permissionDecision":"deny", ...}}` | 否决 |
| stdout 输出 `{... "permissionDecision":"allow", "updatedInput":{...}}` | 放行 + 用 `updatedInput` **改写参数** |
| 退出码 `0`、无输出 | 放行、不改 |

多个钩子竞争改写时，真源码按**完成顺序**取最后一个（`latest_updated_input`，`pre_tool_use.rs:148`）。本章把这套协议压扁成函数返回字典——但「能否决、能改写、否决理由回灌给模型」这三件核心事是忠实的。

</details>

<details>
<summary>三、钩子在另一个进程里跑，不是同进程函数</summary>

本章 `register("pre_tool", block_rm)` 注册的是同进程 Python 函数；真 Codex 的钩子是配置在 hooks JSON / `config.toml` 里的一条**外部命令**，由 `ClaudeHooksEngine` 通过 `CommandShell` 起子进程执行（`registry.rs:60` 里那个 `shell_program` / `shell_args`）。

这带来几个本章没有的真实复杂度：

- **超时**：每个钩子有 `timeout_sec`，跑太久会被杀（`ConfiguredHandler.timeout_sec`）。
- **信任**：钩子是用户机器上的任意命令，有 `bypass_hook_trust` / 信任校验，防止项目目录里偷塞恶意钩子。
- **来源分层**：钩子可来自 user / project / plugin 多个来源（`HookSource`），按来源和 `display_order` 排序分发。
- **stdin 契约**：core 把 `{session_id, turn_id, cwd, tool_name, tool_input, permission_mode, ...}` 序列化成 JSON 喂给钩子的标准输入（`command_input_json`, `pre_tool_use.rs:170`）。

一句话：本章是「进程内回调」，真实是「带超时、带信任、带分层、用 JSON 通信的子进程」。

</details>

<details>
<summary>四、钩子、审批、Guardian：执行前把关链上的三个环节</summary>

本章把钩子讲成一个孤立机制，但在真 Codex 里，"工具调用 → 真正执行"之间是一条**把关链**，钩子只是其中一环：

```
   model 要调 shell(command)
        │
   ① PreToolUse 钩子   ── should_block? ──▶ 否决（本章）
        │ 未否决
   ② 审批策略 (s04)    ── Decision::Prompt? ──▶ 问用户 / 自动拒
        │ 需要问用户时
   ③ Guardian (s14)    ── 先自动评一遍风险 ──▶ low 自动批 / critical 自动拒 / 否则升级
        │ 放行
   ④ 沙箱 (s05)        ── 内核强制 ──▶ 执行
```

`PermissionRequest` 这个钩子事件正是②③的挂载点——钩子甚至能介入"审批请求"本身。这就是为什么 Codex 的钩子和审批/Guardian"长在同一套体系里"：它们不是四个互不相干的功能，而是同一条流水线上四个可插拔的关卡。理解了这条链，[s14] 的 Guardian 就只是"在问用户之前，先派一个 AI 评审员把关"那一环。

</details>

## 运行

```bash
python s13_hooks/code.py --demo   # 不需要模型：canned 回合，看 pre_tool 否决 rm
python s13_hooks/code.py          # 交互模式：含 rm 的命令会被钩子否决
```

`--demo` 完全离线（`backend=mock`）。

[s04]: ../s04_approval/
[s07]: ../s07_context_compaction/
[s10]: ../s10_sq_eq_protocol/
[s14]: ../s14_guardian/

## 小结

- 钩子 = 按事件名注册的可调用对象；循环在四个触发点 `fire` 它们：`pre_turn / pre_tool / post_tool / post_turn`。
- `pre_tool` 最有权力：能**否决**（`should_block`）也能**改写**（`updated_input`）工具调用；否决理由会回灌给模型。
- 真源码有 10 个事件、外部命令钩子、超时/信任/分层，本章取最小子集。
- **生产级**：钩子是外部命令——只跑哈希可信的（`trusted_hash`，防恶意项目钩子）、套 `timeout_sec`（挂死不冻住调用）、安全钩子超时即 fail-closed 当否决（见「生产级」一节）。
- 钩子不是孤岛：它和审批（[s04]）、Guardian（[s14]）、事件协议（[s10]）同属一条"执行前把关链"。
- 下一站 [s14 Guardian](../s14_guardian/)：把关链的下一环——让一个 AI 评审员在问用户之前先自动判风险。

## 思考

1. 本章的 `pre_tool` 钩子能改写命令参数（`updated_input`）。这很方便（自动加 `--dry-run`），但也意味着「模型以为它跑了 A、实际跑的是 B」。当出问题排查时，这种"善意的偷改"会不会比直接否决更难调试？你会怎么让改写对模型/用户透明？

2. 钩子在真 Codex 里是用户机器上的任意外部命令——这是它强大的根源，也是风险的根源：一个藏在项目目录里的恶意 `PreToolUse` 钩子，能在每次工具调用时执行任意代码。Codex 用"信任校验"来防。如果是你，会用什么标准决定"哪些钩子可信"？项目自带的钩子，默认该信任还是默认该怀疑？

3. 钩子、审批、Guardian、沙箱是同一条把关链上的四个关卡。如果一个 `pre_tool` 钩子放行了某命令、但审批策略要拒，谁说了算？反过来，钩子能否"代替用户点同意"绕过审批？把这条链的优先级定清楚，比单独做好每一环更重要——你会怎么排这个优先级？

4. 这一章是少数 Codex 和 Claude Code `≈`（大体相同）的地方。当两个系统在某个机制上趋同时，往往说明这个机制触到了某种"通用最优解"。钩子是不是这样一个通用扩展模式？还是说，它的趋同只是因为两家都在抄同一套（Claude Code 的）hooks JSON 约定？
