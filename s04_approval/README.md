# s04: Approval Policy — 先问能不能做

> 🌐 [English](README.en.md) · **中文**

> *"审批是用户的同意书；沙箱是内核的禁令。两者正交。"*

[learn-codex 总览](../README.md) · 上一章：[s03 apply_patch](../s03_apply_patch/) → **s04** → 下一章：[s05 sandbox](../s05_sandbox/)

---

## 先把思想说透：为什么要"先问能不能做"，又为什么要让你调这个"问的力度"

到 s03 为止，我们造的 agent 有个吓人的脾气：模型说跑什么，它就跑什么，中间没有任何人。这一章要补上一道关——但补这道关的"思路"，比关本身更值得想通。把下面三个道理想明白，你就懂了为什么 Codex 不是简单加个"危险命令黑名单"了事。

**道理一：拦危险，不能靠"把坏命令列出来"。**
最直觉的做法是写一张黑名单：`rm -rf /`、`sudo`…… 见到就拦。可这条路是死的。删整个 home 有多少种写法？`rm -rf ~`、`rm -fr $HOME`、`find ~ -delete`、甚至一段三行的 Python 脚本——你永远列不全，攻击者（或一个犯迷糊的模型）总能绕过你列的那几条。**枚举坏东西注定漏**。所以正确的问题不是"哪些命令是坏的"，而是"这条命令我**有没有把握**它是安全的"——没把握，就别擅自替用户做主。

**道理二：没把握时，最稳的不是"猜"，而是"问那个负责的人"。**
一个尽职的助理遇到拿不准的事，不会自作主张，也不会一概拒绝——他会回头问你一句"这个要不要做？"。"先问能不能做"（ask for approval）就是这个直觉：把"拿不准的命令"**升级**给真正负责后果的人来拍板。注意它和黑名单的根本不同——黑名单试图"自己判对错"，审批是"承认自己判不全，于是把决定权交回给人"。这一步，让 agent 从"擅自行动"变成"先征得同意"。

**道理三（最关键，也最容易忽略）："问的力度"必须能调，因为没有一个力度适合所有场景。**
这里是 Codex 真正的巧思。你本地盯着屏幕用它，和它在半夜的 CI 流水线里无人值守地跑——对"什么该停下来问、什么该自己放行"的期待**完全相反**。你在场时，希望它别动不动打断你，只有真危险才问；没人在场时，一个"等待你点同意"的弹窗会让整条流水线**永远卡住**，这时你宁可它直接拒绝危险命令、绝不停下来等。
所以 Codex 不把"要不要问"写死成代码里的一句 `if`，而是做成一个**可调的旋钮**——几档策略，从"凡事都先问"一路滑到"永不打扰"。同一个 agent，换个档，就能从"贴身结对的谨慎助手"变成"无人值守的自动化工人"。**把'自主程度'变成一个你能拧的旋钮**，这就是审批这一章的灵魂。

一句话串起来：黑名单想替你判对错（注定漏），审批承认判不全、于是该问就问（把决定权还给人），而那个"问的力度"被做成一个旋钮，让同一套机制覆盖从"人盯着"到"没人管"的整条光谱。

## 问题

s01 的 shell 工具有个让人后背发凉的细节：模型说要跑什么，它就跑什么。没有黑名单，也没有人把关。

那就加个危险命令黑名单？挡不住。命令的变体是无穷的——`rm -rf ~`、`rm -fr $HOME`、`find ~ -delete`、一个删文件的 Python 脚本……你永远列不全（这正是 [s05] 沙箱要解决的另一半问题）。

但还有一个**更前置**的问题：**有些时候我们根本不想让它自己决定，而想让人来拍板。** 在你本地交互式地用 Codex，和在 CI 流水线里无人值守地跑 Codex，对「什么该自动放行、什么该停下来问」的容忍度完全不同。

于是 Codex 把「要不要问用户」做成一个**可调的策略**，而不是写死在代码里的一句 `if`。

## 解决方案

一个审批门 `decide(command, policy) -> "approve" | "ask" | "reject"`，外加 **4 档策略**，让你按场景切换自主度：

```
                 ┌──────────────── 审批门 decide() ────────────────┐
   command ─────▶│  is_known_safe(cmd)?   is_dangerous(cmd)?        │
                 │            │                    │                │
                 │            ▼     × 策略档位 ×     ▼                │
                 │   ┌─────────────────────────────────────────┐   │
                 │   │ untrusted  : 白名单外一律 ask              │   │
                 │   │ on-request : 危险才 ask，其余 approve       │   │
                 │   │ on-failure : 先 approve，失败再 ask         │   │
                 │   │ never      : 不问；危险直接 reject          │   │
                 │   └─────────────────────────────────────────┘   │
                 └──────────────────┬──────────────────────────────┘
                                    ▼
                  approve → 执行   ask → 问用户(y/N)   reject → 拒绝
```

`approve` 直接放行，`reject` 直接拒，`ask` 才会把这一条「升级」给用户——弹出一个审批请求、等一个决定回来。

## 工作原理

看 [code.py](code.py)，三块新东西。

**第 1 块** — 两套保守的启发式（不枚举坏命令，只判断「明显安全」与「明显危险」）：

```python
def is_known_safe(command):   # ls / cat / echo / pwd / grep / git status ...
    ...
def is_dangerous(command):    # rm -f|-rf / sudo / curl|wget ... | sh
    ...
```

它们直接对应真源码的 [`is_known_safe_command`](../../codex/codex-rs/shell-command/src/command_safety/is_safe_command.rs) 与 [`command_might_be_dangerous`](../../codex/codex-rs/shell-command/src/command_safety/is_dangerous_command.rs)。注意安全的 `git` 只有只读子命令（`status` / `log` / `diff` / `show` / `branch`），这和真源码 `is_safe_git_command` 一致。

**第 2 块** — 审批门 `decide()`，把启发式叠加上策略档位，裁出 `approve / ask / reject`：

```python
def decide(command, policy):
    safe, danger = is_known_safe(command), is_dangerous(command)
    if policy == "untrusted":  return "approve" if safe else "ask"
    if policy == "on-request": return "approve" if safe else ("ask" if danger else "approve")
    if policy == "on-failure": return "ask" if danger else "approve"
    if policy == "never":      return "reject" if danger else "approve"
```

这三个返回值正对应真源码的 [`Decision::{Allow, Prompt, Forbidden}`](../../codex/codex-rs/execpolicy/src/decision.rs)。各档的语义不是我编的，而是抄自 [`AskForApproval` 的 doc-comment](../../codex/codex-rs/protocol/src/protocol.rs)（`protocol.rs:760`）：`UnlessTrusted` 序列化名就叫 `"untrusted"`，「只有已知安全且只读的命令自动批准」。

**第 3 块** — 用门包住 shell 工具。`ask` 这一档要真的去问人：

```python
def gated_shell(command, policy, ask_user):
    verdict = decide(command, policy)
    if verdict == "approve": return _run(command)
    if verdict == "reject":  return "[拒绝] 未执行"
    return _run(command) if ask_user(command, policy) else "[用户拒绝] 未执行"
```

`ask_user` 这一来一回，对应真源码里 [`ExecApprovalRequestEvent`](../../codex/codex-rs/protocol/src/approvals.rs)（事件出）+ [`Op::ExecApproval { decision }`](../../codex/codex-rs/protocol/src/protocol.rs)（决定回），承载决定的是 `ReviewDecision`。这正是 [s10] 那条「事件出、Op 回」队列的一个实例。

**走一遍** — 跟着同一条命令穿过审批门，看每一步的数据长什么样。假设模型在某个回合产出了这样一个工具调用：

```json
{ "type": "function_call", "name": "shell",
  "arguments": { "command": "rm -rf /" } }
```

我们不直接执行它，而是先把 `command="rm -rf /"` 喂进 `decide()`。第一步是两套启发式各跑一遍：

```python
is_known_safe("rm -rf /")  → False    # rm 不在只读白名单里
is_dangerous("rm -rf /")   → True     # 命中 "rm -rf" 危险模式
```

拿到 `safe=False, danger=True` 这对布尔值后，**同一条命令在不同策略档下会得到完全不同的裁决**——这正是"旋钮"的意义：

```text
decide("rm -rf /", "on-request") → "ask"      # 危险 → 升级问人
decide("rm -rf /", "never")      → "reject"   # 无人值守 → 直接拒，绝不停下等
decide("rm -rf /", "untrusted")  → "ask"      # 非白名单 → 一律先问
```

注意没有哪一档会无声放行它。拿 `on-request` 这一档继续走：裁决是 `"ask"`，于是 `gated_shell` 不执行，而是产出一个审批请求递给用户——真 Codex 里它是一条 `ExecApprovalRequestEvent`，长这样：

```json
{ "type": "exec_approval_request", "call_id": "call_42",
  "command": "rm -rf /", "cwd": "/work",
  "available_decisions": ["approved", "denied", "abort"] }
```

用户看到后回一个决定（`denied`）。这一来一回回到 `gated_shell`，它据此返回 `"[用户拒绝] 未执行"`——命令从头到尾**没有真的跑**。把这三步连起来看，你就摸到了审批的全貌：*启发式给出"有没有把握"，策略档把这份把握翻译成"放行/问/拒"，问的话再走一条出—回的审批回路*。

`--demo` 把同一条 `rm -rf /` 喂进 `untrusted` / `on-request` / `never` 三档，打印每档的决定，并让「模拟用户」在被问到时拒绝——证明危险命令在三档下都没被真正执行。

## 生产级：审批不是一个 bool，是带记忆、有刹车的决定

教学版的审批门很优雅，但它把"用户的答复"压成了一个 `bool`（同意/拒绝）。一个能上生产的审批系统，狠在三件事上——它们恰好回答了"越用越顺手会不会越用越危险"。

### 一、用户的答复是 ReviewDecision，不止"同意/拒绝"

真 Codex 审批回路里用户回的是 [`ReviewDecision`](../../codex/codex-rs/protocol/src/protocol.rs)（`protocol.rs:3660`）：

| 决定 | 含义 |
|---|---|
| `Approved` | 这一次放行 |
| `ApprovedForSession` | 放行，且**记进会话缓存**——同前缀命令本会话内不再问 |
| `ApprovedExecpolicyAmendment` | 放行，且**学成一条永久 allow 规则** |
| `Denied`（默认） | 拒绝，但继续会话、让模型换个法子 |
| `TimedOut` | 自动评审超时 → **按拒处理**（fail-closed，呼应 [s14](../s14_guardian/)） |
| `Abort` | 拒绝并停下，等用户下一步 |

注意 `Denied` 是 `#[default]`、`TimedOut` 也倒向拒——**默认值全朝安全那边倒**，和沙箱 [s05](../s05_sandbox/) 的 deny-default 一个气质。

### 二、会话缓存：越用越少打扰

`ApprovedForSession` 把批准过的命令前缀记进缓存，下次同前缀直接 auto-approve、不再打断你。`--demo` 演示了这条：

```
用户对 `cargo build --release` 选 ApprovedForSession → ✓ 已记住：本会话内 `cargo …` 自动放行
下次 `cargo test` 的裁决 → auto-approve（会话缓存命中）（没再打扰用户）
```

### 三、刹车：BANNED_PREFIX——越用越顺手 ≠ 越用越宽松

"学习放行"听着爽，却藏着陷阱：若你能把 `python` 学成永久 allow，以后**任何** `python -c "..."` 都不问了——审批等于被架空。真 Codex 用 `BANNED_PREFIX_SUGGESTIONS`（[`exec_policy.rs:52`](../../codex/codex-rs/core/src/exec_policy.rs)）挡住这类前缀：`python` / `bash` / `sh` / `zsh` / `git` / `pwsh`…… 这些能跑任意代码的解释器/shell，**再怎么批准也不会被泛化成规则**：

```
✗ 拒绝把 `python` 学成永久放行：它能跑任意代码，泛化它等于架空审批（BANNED_PREFIX）
✗ 拒绝把 `git` 学成永久放行：……
```

> 一句话：生产级审批 = **可记忆（少打扰）+ 有刹车（不滑坡）+ 默认朝拒（fail-closed）**。"approve once" 容易，难的是"approve 之后系统不会因此变得不安全"。

## 🆚 与 Claude Code 的不同

| | Claude Code | Codex |
|---|---|---|
| 审批形态 | 危险操作**即时弹窗**问你 | 显式、可调的**策略档位**（`untrusted/on-request/on-failure/never`） |
| 谁来配 | 体验内置，少有暴露的"模式" | 用户/项目可在 `config.toml`、命令行、profile 里选档（见 [s16]） |
| 与沙箱的关系 | 审批 + 路径校验是主防线（应用层） | 审批与沙箱**正交**：审批=用户同意，沙箱=内核强制（见 [s05]） |
| 适配场景 | 偏交互式、人在回路 | 同一套策略覆盖交互 / 无头 CI / 云端——只是换个档 |

**为什么不同？** 因为 Codex 要在**自主度的连续谱**上滑动，而不是只服务「人盯着」这一个场景：

- 你本地敲代码，挂 `on-request`：日常命令自己跑，碰到危险的才停下来问你。
- CI 流水线里 `codex exec` 无人值守，挂 `never`：永不卡在一个没人回应的弹窗上——危险命令要么靠沙箱兜底，要么直接拒。
- 想最大化谨慎，挂 `untrusted`：除了少数只读命令，什么都先问。

而最关键的一点，是把**审批和沙箱拆成正交的两层**（这正是本课程主线在本章的落点）：审批回答「**用户同不同意**」，是应用层、由人把关的一道关；沙箱（[s05]）回答「**内核让不让碰**」，是内核层、机器强制的一道关。一条命令完全可以「被批准、但仍然跑在沙箱里」——批准只是放它进门，沙箱仍然限制它在门内能碰什么。Claude Code 把审批弹窗 + 路径校验当主防线；Codex 多压了一道独立于人的内核防线，于是才敢把审批档位调到 `never` 去无人值守地跑。

## 深入：教学版 vs 真 Codex 源码

教学版 4 个 `if` 的 `decide()`，在真 codex-rs 里是一整套 **execpolicy** 子系统 + 一条审批事件回路。下面拆开看差在哪。

<details>
<summary>一、本章的 4 档，是真源码 AskForApproval 枚举的真实变体</summary>

真源码 `protocol.rs:760` 的枚举（doc-comment 我直接抄进了 code.py）：

| 本章字符串 | 真源码变体 | doc-comment 原意 |
|---|---|---|
| `"untrusted"` | `AskForApproval::UnlessTrusted` | 只有 `is_safe_command()` 认定的「只读」命令自动批准，其余都问 |
| `"on-failure"` | `AskForApproval::OnFailure` | 全部自动批准（指望跑在沙箱里），失败才升级问用户 |
| `"on-request"` | `AskForApproval::OnRequest`（`#[default]`） | 模型决定何时问用户 |
| `"never"` | `AskForApproval::Never` | 永不问；失败直接回给模型 |

注意真源码还有第 5 个变体 `Granular(GranularApprovalConfig)`——逐项开关 `sandbox_approval` / `rules` / `mcp_elicitations` / `request_permissions` 等，把「问 vs 自动拒」做到一个个审批流的粒度。本章为教学省略了它。

```rust
// protocol.rs:760
pub enum AskForApproval {
    UnlessTrusted,                 // serde "untrusted"
    OnFailure,
    OnRequest,                     // #[default]
    Granular(GranularApprovalConfig),
    Never,
}
```

</details>

<details>
<summary>二、真正的裁决器是 execpolicy：前缀规则状态机，不是几个 if</summary>

本章的 `decide()` 把判断写成 Python 分支；真 Codex 的判断主体在 `codex-rs/execpolicy` 这个独立 crate 里，是一套**前缀规则（prefix rule）**引擎：

- 规则写在 `.rules` 文件里（Starlark 方言），形如 `prefix_rule(["git", "push"], decision="prompt")`、`prefix_rule(["rm"], decision="forbidden")`。
- `ExecPolicyManager`（`core/src/exec_policy.rs:235`）从多层 config 目录按优先级加载、合并这些规则，对一条命令做 `check_multiple_with_options(...)`，匹配最具体的前缀。
- **匹配不到任何规则**时，才回退到本章的两套启发式：`render_decision_for_unmatched_command()`（`exec_policy.rs:628`）——这个函数才是「未知命令 + 策略档位 → Allow/Prompt/Forbidden」的真实裁决逻辑，本章的 `decide()` 就是它的极简投影。

裁决结果不是裸的 `Decision`，而是包成 `ExecApprovalRequirement`（`core/src/tools/sandboxing.rs:160`）：

```rust
enum ExecApprovalRequirement {
    Skip { bypass_sandbox: bool, .. },   // Allow：直接跑（甚至可绕过沙箱）
    NeedsApproval { reason, proposed_execpolicy_amendment, .. },  // Prompt：问用户
    Forbidden { reason },                // Forbidden：拒
}
```

`Skip` 里那个 `bypass_sandbox` 字段，正是「审批与沙箱正交」在类型层面的证据：被 execpolicy 显式 allow 的命令可以**跳过沙箱**，其余命令即便批准了也照样进沙箱。

</details>

<details>
<summary>三、批准一次，能顺手写一条规则进 execpolicy</summary>

真源码的审批比「approve / reject」更丰富。`ReviewDecision`（`protocol.rs:3660`）有这些分支：

| 变体 | 含义 |
|---|---|
| `Approved` | 批准这一次 |
| `ApprovedForSession` | 本会话内同类请求都自动批 |
| `ApprovedExecpolicyAmendment { proposed_execpolicy_amendment }` | 批准 + **把这个前缀写成一条 allow 规则**，以后同前缀命令免问 |
| `NetworkPolicyAmendment { .. }` | 持久化一条网络 allow/deny 规则 |
| `Denied` | 拒绝这一次，但继续会话、换个法子 |
| `Abort` | 拒绝并停下，等用户下一步 |

也就是说，当门判 `Prompt` 时，真源码会顺带 `derive` 一个 `ExecPolicyAmendment`（一个命令前缀），随审批请求一起递给你；你选 `ApprovedExecpolicyAmendment`，它就 `append_amendment_and_update()` 把 `prefix_rule([...], allow)` 落盘进 `default.rules`——下次同类命令不再问你。本章的 `gated_shell` 只返回执行/不执行，没有这个「学习」回路。

</details>

<details>
<summary>四、审批不是同步弹窗，而是穿过 SQ/EQ 的一来一回</summary>

本章 `ask_user(command, policy)` 是个同步函数调用——问完立刻拿到布尔值。真 Codex 做不到这么简单，因为它要在**一个回合进行到一半**时弹审批，还要让 TUI / `codex exec` / app-server 三种前端都能回应（见 [s10]）。

所以真实路径是异步的：core 产出一条 `ExecApprovalRequestEvent`（`approvals.rs:217`，带 `call_id` / `command` / `cwd` / `parsed_cmd` / `proposed_execpolicy_amendment` / `available_decisions`）丢进**事件队列**；前端渲染、问到用户；用户的决定包成 `Op::ExecApproval { id, decision }`（`protocol.rs:504`）丢回**提交队列**；core 收到才继续这一回合。

```
core ──ExecApprovalRequestEvent──▶ (EQ) ──▶ 前端弹窗
core ◀──Op::ExecApproval{decision}── (SQ) ◀── 用户点了批准/拒绝
```

本章把这一来一回压扁成一次函数调用；[s10] 会把这两条队列单独拆开讲。而 [s14] 的 Guardian 则在这条回路上再插一层：**在问用户之前，先让一个自动评审员判一遍风险**。

</details>

## 运行

```bash
python s04_approval/code.py --demo   # 不需要模型：3 档策略 × 安全/危险命令，打印决定
python s04_approval/code.py          # 交互模式：shell 命令先过审批门（默认 on-request）
```

`--demo` 完全离线（`backend=mock`）。想接真模型，在根目录 `.env` 填 `OPENAI_API_KEY`。

[s05]: ../s05_sandbox/
[s10]: ../s10_sq_eq_protocol/
[s14]: ../s14_guardian/
[s16]: ../s16_config/

## 小结

- 审批门 `decide(command, policy) → approve | ask | reject`，对应真源码 `Decision::{Allow, Prompt, Forbidden}`。
- 4 档策略 `untrusted / on-request / on-failure / never` 是真源码 `AskForApproval` 的真实变体，让自主度在「凡事都问」到「永不问」之间滑动。
- 不枚举坏命令，只判「明显安全 / 明显危险」，其余交给策略档位。
- **审批 ≠ 沙箱**：审批是用户的同意（应用层），沙箱是内核的禁令（[s05]，内核层），两者正交、可叠加。
- **生产级**：审批是带记忆的 `ReviewDecision`——`ApprovedForSession` 进会话缓存（越用越少打扰）、`BANNED_PREFIX` 挡住把解释器学成永久放行（不滑坡）、`TimedOut`/`Denied` 默认朝拒（fail-closed）。见「生产级」一节。
- 下一站 [s05](../s05_sandbox/)：当没人审批时，靠什么兜底？把命令关进内核沙箱。

## 思考

1. 本章的 `on-failure` 在没有沙箱时，对危险命令选择了「先问」。可真源码的 `OnFailure` 是「**全部先跑**、失败才问」——它敢这么做，靠的是命令都跑在沙箱里。如果你只有审批、没有沙箱，你还敢让 `on-failure` 先跑吗？这是否说明「审批档位」的胆量，其实是被「有没有沙箱」借出来的？

2. `ApprovedExecpolicyAmendment` 让用户批准一次就把前缀写成永久 allow 规则，越用越少打扰。但「越用越顺手」和「越用越宽松」往往是同一件事——你会给这种自动学习加上什么刹车？（提示：真源码有个 `BANNED_PREFIX_SUGGESTIONS`，`python` / `bash` / `sudo` 永远不准被建议成 allow 前缀，为什么是这几个？）

3. Claude Code 用即时弹窗、Codex 用可调档位。如果你在写一个跑在 CI 里、没人盯着的 agent，`never` 档配合沙箱够安全吗？反过来，一个本地结对编程的 agent，频繁弹窗会不会反而训练用户「无脑点同意」，把审批变成橡皮图章？

4. 审批问的是「用户同不同意」，沙箱问的是「内核让不让碰」。把它们做成正交的两层，好处是各自可独立调强弱；但对用户来说，「这条命令被批准了却还是失败」会不会反而比单一防线更难理解？你会怎么把这两层的状态呈现给用户？
