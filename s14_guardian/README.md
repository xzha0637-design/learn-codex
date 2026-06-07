# s14: Guardian — 自动风险评估

> 🌐 [English](README.en.md) · **中文**

> *"没人盯着的时候，谁来把关？派一个 AI 评审员先判一遍。"*

[learn-codex 总览](../README.md) · [Hooks](../s13_hooks/) → **本章** → [MCP：客户端 + 服务端](../s15_mcp/)

---

## 先把思想说透：没人把关时，就派个 AI 去把关

上一层的审批（[s04]）很优雅：危险命令弹个窗，让人拍板。可它藏着一个隐含前提——**得有个人在那儿、并且在认真看**。一旦 Codex 跑进 CI 流水线、云端任务、IDE 后端这类**没人盯着屏幕**的环境（统称 headless / 无人值守），这个前提就塌了：没有人能回那个弹窗。这一章所有的纠结，根子都在这句话里。想通下面三个道理，你就明白 Guardian 这个新角色为什么非有不可。

**道理一：「没人在场」时，原来的审批会被逼进一个死角。**
模型想跑一条有风险的命令时，审批策略要么「问用户」、要么「不问」。可这两条路在无人值守下都走不通：挂「问用户」，每条稍微敏感的命令都把决定**升级**（escalate，意思是把拍板权从自动流程交回给人）给一个**不会回应**的人——回合就卡死在那里；挂「不问」，等于把判断权全交给模型，它一旦失误或被注入诱导，灾难命令也照跑不误。**要么卡死等一个不会来的回应，要么放任不管**——中间缺了一个能在用户缺席时替他做判断的东西。

**道理二：缺的那个「判断者」，本来就是一项可以委派出去的工作——那就派一个 AI 去做。**
关键的跳跃在这里：「判断这条命令该不该放」原本是人做的事，但它本质上是一项**认知工作**，而认知工作恰恰是 AI 能干的。于是 Codex 的答案是——在主 agent 真正执行某条命令前，**先派出一个专门的 AI 评审员**（这就是 **Guardian**），替缺席的用户把这条命令审一遍。这不是一组写死的 `if`，而是**另开一次模型调用**：把「到此刻为止的对话 + 模型计划要做的动作」喂给这个评审 agent，让它读完返回一个判断。「再开一个 AI 来审第一个 AI」就是本章的灵魂——**安全感不是靠拦住所有命令得来的，而是靠把「判断」这件事也自动化、从而能规模化地铺到每一条命令上**。（本章为了离线可跑，用一组保守规则**模拟**这个评审员，但输出形状和真的一致。）

**道理三（最关键）：把关要分寸，且必须「失败时倒向安全」。**
派个 AI 全权放行或全权拦截都太蠢——大部分命令（`ls`、`echo`）根本不值得惊动任何人，极少数命令（`rm -rf ~`、`curl ... | sh`）则几乎一定是灾难。所以 Guardian 给每个动作打一个**风险档**，由轻到重四级：🟢 `low`、🟡 `medium`、🟠 `high`、🔴 `critical`，再把档位映射成动作——`low → auto_allow`（自动放行，不打扰你）、`critical → auto_deny`（连问都不问直接拒），只有拿不准的 `medium / high → escalate`（升级回 [s04] 让人拍板）。一句话：**两头自动、中间问人。** 而当 Guardian 自己出岔子时——超时、崩了、读不懂返回内容——它**默认判「拒绝」而不是「放行」**，这条原则叫 **fail-closed**：好比门禁系统断电时自动锁死而不是大敞，**出错就倒向安全的那一边**。

把这三点连起来：**Guardian = 一个自动评审员，插在「问用户」之前，把风险分四档——低的自动放、灾难的自动拒、中间的才升级问人；它永远 fail-closed，且用户始终能覆盖它。** 这正是「没人盯着的时候，谁来把关」这个问题最直接的答案，也是本课主线「为低/无人工干预下注」在安全层面的终点：连「把关的人」都被尽量自动化了。

## 问题

[s04] 给了我们审批策略：危险命令升级给用户拍板。这在你本地盯着屏幕时很好用。

可 Codex 的野心是在**没人盯着**时也能跑——`codex exec` 在 CI 里、Codex 在云端、IDE 后端里一跑就是几十个回合。这时审批策略撞上一堵墙：

- 挂 `on-request`，每个稍微敏感的命令都升级问用户——可**没有用户在回应**，回合就卡死在那里。
- 挂 `never`，干脆不问——可这等于把判断权全交给模型，模型一旦失误（或被注入诱导），灾难命令也照跑不误。

两难：**要么卡死等一个不会来的回应，要么放任不管。** 中间缺了一层——一个能在用户缺席时，替用户做出"这个能自动放行 / 这个必须拦下"判断的东西。

## 解决方案

一个自动风险评估器 `guardian(action) -> {risk, reason}`，把它接成一个**自动审批人**，插在"问用户"之前。风险分四档，各对应一个自动决定：

```
   model 要执行 action（命令 / 补丁）
        │
        ▼
   ┌──────────── guardian(action) ────────────┐
   │   评估风险 → {risk, reason}                 │
   │        │                                   │
   │   ┌────┴─────────────────────────────┐    │
   │   │ 🟢 low      → auto_allow（不打扰） │    │
   │   │ 🟡 medium   ┐                      │    │
   │   │ 🟠 high     ┘→ escalate（问用户）   │── 回到 s04 审批门
   │   │ 🔴 critical → auto_deny（连问都不问）│    │
   │   └──────────────────────────────────┘    │
   └────────────────────┬──────────────────────┘
                         ▼
        执行 / 不执行（或交给用户拍板）
```

低风险自动放行（省得每条 `ls` 都打扰你），灾难级自动拒（fail-closed，连问都不问），只有中间的 medium / high 才**升级**回 [s04] 的审批门让人决定。这样：有人时少打扰，没人时也不失守。

## 工作原理

看 [code.py](code.py)，两块。

**第 1 块** — 风险评估器 `guardian()`，返回与真源码同形状的 `{risk, reason}`：

```python
RISK_LEVELS = ("low", "medium", "high", "critical")

def guardian(action):
    if "rm -rf ~" in low or "curl ... | sh": return {"risk": "critical", ...}
    if low.startswith("sudo ") or "rm ":     return {"risk": "high", ...}
    if "git commit" / "pip install" / ">":   return {"risk": "medium", ...}
    return {"risk": "low", "reason": "只读或无明显副作用"}
```

四个档名直接来自真源码 [`GuardianRiskLevel::{Low, Medium, High, Critical}`](../../codex/codex-rs/protocol/src/approvals.rs)（`approvals.rs:85`）。真实里这判断不是规则给的，而是一个**评审 LLM** 给的（详见下方深入）；本章用一组保守规则**模拟**那个评审员，好让 demo 离线可跑。

**第 2 块** — 把风险档位映射成自动决定，再串进执行：

```python
def auto_decision(action):
    risk = guardian(action)["risk"]
    if risk == "low":      return {... "decision": "auto_allow"}
    if risk == "critical": return {... "decision": "auto_deny"}
    return {... "decision": "escalate"}          # medium / high

def guarded_execute(action, ask_user, run_fn):
    v = auto_decision(action)
    if v["decision"] == "auto_allow": return run_fn(action)
    if v["decision"] == "auto_deny":  return "[guardian 自动拒绝] 未执行"
    return run_fn(action) if ask_user(action, v["risk"]) else "[用户拒绝] 未执行"  # escalate
```

`escalate` 分支正是和 [s04] 的接缝——Guardian 判不下来的，回退给审批门让人拍板。

`--demo` 把五条动作喂进 guardian：`echo`（low→自动放行）、`git commit`（medium→升级，模拟用户批准）、`rm -rf build`（high→升级，模拟用户拒绝）、`curl|sh` 和 `rm -rf ~`（critical→自动拒）。每条都打印风险档、理由、自动决定。

**走一遍。** 我们挑 demo 里**三条有代表性的动作**，看每条**进 guardian 长什么样、guardian 判出什么、最后怎么处置**——三条正好覆盖"自动放 / 升级问人 / 自动拒"三种结局。

1. **`echo SQ/EQ works` —— 低风险，自动放行。**
   进 `guardian()`，命令只是打印，没有任何副作用，落到最后一条兜底规则，返回：
   ```json
   { "risk": "low", "reason": "只读或无明显副作用" }
   ```
   `auto_decision` 把 `low` 映射成 `auto_allow`。`guarded_execute` 看到 `auto_allow`，**直接执行，根本不惊动用户**。这就是为什么你不会被每条 `ls`、`echo` 烦死。

2. **`rm -rf build` —— 高风险，升级问人。**
   进 `guardian()`，命中"含 `rm`"的规则，返回：
   ```json
   { "risk": "high", "reason": "删除文件 / 提权 / 危险操作" }
   ```
   `auto_decision` 把 `high`（和 `medium` 一样）映射成 `escalate`——**guardian 不敢自己拍板**。于是 `guarded_execute` 走到最后一行，调 `ask_user(action, "high")` 把球踢回给人（demo 里模拟用户答"拒绝"）。**为什么不自动拒？** 因为 `rm -rf build` 在很多正常工作流里是合理的（清理构建产物），一刀切拒掉会挡住正经活——这种"危险但可能合理"的，正该交给人。这一步就是和 [s04] 审批门的接缝。

3. **`rm -rf ~` —— 灾难级，自动拒（fail-closed 的精神）。**
   进 `guardian()`，命中"灾难"规则，返回：
   ```json
   { "risk": "critical", "reason": "可能摧毁系统 / 远程执行任意代码" }
   ```
   `auto_decision` 把 `critical` 映射成 `auto_deny`。`guarded_execute` **连问都不问**，直接返回 `[guardian 自动拒绝] 未执行`。**为什么连问都不问？** 因为这种命令几乎不存在"正当用途"，把它升级给用户、反而给了"手滑点同意"或"被社工诱导点同意"的机会——倒不如直接焊死。这就是"宁可错杀"在 demo 里的体现。

把三条连起来看：**同一个 guardian，对三条命令给出三种风险档，再映射成三种处置——两头（low/critical）它自己包办，只有中间（medium/high）才把人拉进来。** 这正是 Guardian 的全部价值：在没人盯着时，它替你挡掉了绝大多数无谓的打扰和无谓的风险，只把真正需要人判断的那一小撮留给你。

## 生产级：用 AI 把关，更要给这个 AI 上保险

让一个 AI（Guardian）替缺席的用户把关，听着就危险——这个把关器本身会超时、会崩、会被骗。所以 Guardian 的生产级，全在给它套的几道保险上：

- **fail-closed**：Guardian 超时 / 崩溃 / 返回的 JSON 解析不了——**一律判拒**，绝不"出错就放行"（真源码 `core/src/guardian/review.rs:147/251`）。把关器失灵时倒向安全，和沙箱 deny-default（[s05](../s05_sandbox/)）、审批 `TimedOut→拒`（[s04](../s04_approval/)）同一个直觉。
- **熔断器**：防止 Guardian 自己失控地反复评/反复拒。`MAX_CONSECUTIVE_GUARDIAN_DENIALS_PER_TURN = 3`——一个回合里连续拒到阈值就熔断，不再空转烧钱（呼应 [s09 重试](../s09_responses_api/) 那个"光退避不够、还要断路器"的问题）。
- **结构化契约 + 超时**：Guardian 必须返回严格 JSON（`GuardianAssessment`），跑在一个有超时的 fork 子会话里——读不懂或不返回，都走 fail-closed。
- **诚实的局限**：Guardian 和被它审的往往是**同代同源**的模型——能骗过第一个的注入，可能同样骗过它（见本章思考 1）。"用 AI 审 AI"增加的是一道**不同视角**的检查，不是一道不可逾越的墙。

> 一句话：Guardian 的价值不在"AI 很聪明会把关"，而在"**把关器失灵时，整个系统倒向拒绝而非放行**"。这才是它敢在无人值守时上岗的底气。

## 🆚 与 Claude Code 的不同

这是 Codex 与 Claude Code **最鲜明的一处差异（⭐）**：Codex 多了一整层"自动评审员"，Claude Code 没有对应物。

| | Claude Code | Codex |
|---|---|---|
| 谁判断风险 | **用户**（弹窗摆在你面前，你来判） | **一个自动评审 agent**（Guardian）先判一遍 |
| 没人盯着时 | 卡在弹窗 / 全凭模型 | Guardian 替用户做 low→放行、critical→拒 的判断 |
| 判断依据 | 人的直觉 + 当下注意力 | 评审 LLM 读转录 + 计划动作，返回结构化 `{risk, outcome, rationale}` |
| 失守模式 | 用户点了"无脑同意" | Guardian 误判（但 critical fail-closed + 用户可覆盖） |

**为什么 Codex 要多这一层？** 一句话：**为了在没人看着时也能安全地放大自主度。**

Claude Code 的设想是"人和 agent 并肩"——危险操作弹窗问你，由人把最后一道关，这套在交互式场景里既安全又流畅。但它有个隐含前提：**有一个人在那儿、并且在认真看**。一旦进入 headless / CI / 云端，这个前提就塌了。

Codex 押的是"自主"——它必须假设**没有人在回应**。于是它把"判断风险"这件原本由人做的事，**也交给一个 AI**：一个专门的评审子 agent，在主 agent 的工具调用真正执行前，先独立读一遍上下文、判一遍风险。这一层让 Codex 既不必为每条命令卡死等人，也不必把判断权完全让给可能失误的主模型——而是用"第二个 AI"来给"第一个 AI"把关。这正是本课程主线"Codex 为低人工干预下注"在安全层面的终极体现：连"把关的人"都被尽量自动化了。

注意 Guardian **不取代**审批和沙箱，而是叠在它们之上：Guardian 判 escalate 时仍回退给用户审批（[s04]），命令最终仍跑在内核沙箱里（[s05]）。它只是把"能自动放行/必须拦下"的两头摘出来，省掉无谓的打扰和无谓的风险。

## 深入：教学版 vs 真 Codex 源码

本章的 `guardian()` 是一组 `if`；真 Codex 的 Guardian 是一个**会 fork 当前会话、读转录、返回严格 JSON 的评审 LLM 子 agent**，外加一套 fail-closed 与防滥用机制。下面拆开。

<details>
<summary>一、真 Guardian 是一个评审 LLM，不是规则表</summary>

真源码 `core/src/guardian/mod.rs` 顶部的 doc-comment 把它的工作流写得明明白白：

> Guardian review decides whether an `on-request` approval should be granted automatically instead of shown to the user.
> 1. 重建一份紧凑的转录（保留用户意图 + 最近相关的 assistant/工具上下文）；
> 2. 让一个专门的 guardian 评审会话评估这个**确切的计划动作**，返回严格 JSON；
> 3. 超时 / 执行失败 / 输出畸形一律 **fail closed**；
> 4. 应用 guardian 给出的 allow/deny 结论。

也就是说，Guardian 自己就是一次**模型调用**：它 clone 父会话的 config（继承同样的网络代理/白名单），喂给它"截至此刻的对话 + 计划要做的动作"，要求它吐出结构化评估。本章用规则模拟这个评审员的输出，但形状一致。

它返回的契约是 `GuardianAssessment`（`mod.rs:63`）：

```rust
pub(crate) struct GuardianAssessment {
    pub risk_level: GuardianRiskLevel,            // Low / Medium / High / Critical
    pub user_authorization: GuardianUserAuthorization,  // 转录里用户授权得有多直接
    pub outcome: GuardianAssessmentOutcome,       // Allow / Deny（最终裁决）
    pub rationale: String,                        // 人类可读的理由
}
```

本章只取了 `risk_level` + `rationale`；真源码还多一个 `user_authorization`——评审员会判断"用户在对话里到底有没有明确授权过这类动作"，授权越直接，越敢放行。

</details>

<details>
<summary>二、它只在 on-request 档上岗，且永远 fail-closed</summary>

Guardian 不是无条件介入。`review.rs:147` 的 `routes_approval_to_guardian` 决定何时路由给它：

```rust
pub(crate) fn routes_approval_to_guardian_with_reviewer(turn, approvals_reviewer) -> bool {
    matches!(turn.approval_policy.value(),
             AskForApproval::OnRequest | AskForApproval::Granular(_))
        && approvals_reviewer == ApprovalsReviewer::AutoReview
}
```

即：只有审批策略是 `on-request`（或 `granular`）、且开启了 `AutoReview` 时，才让 Guardian 上岗——它正是 [s04] 那个 `on-request` 档的"自动化升级处理器"。

而它的 fail-closed 是刻进函数注释里的（`review.rs:251`）：

> This function always fails closed: timeouts, review-session failures, and parse failures all block execution.

本章用"critical → auto_deny"体现了 fail-closed 的精神；真源码更彻底：**评审员超时（默认 90 秒，`GUARDIAN_REVIEW_TIMEOUT`）、跑挂了、JSON 解析失败——任何一种异常，都判 Deny**。宁可错杀，不可放过。

</details>

<details>
<summary>三、防止评审员失控：熔断器</summary>

把"判断"交给一个 AI，会引出一个新问题：**如果评审员自己抽风、把正常动作连环拒了怎么办？** 主 agent 可能被卡在"提议→被拒→再提议→又被拒"的死循环里。

真源码用一个熔断器解决（`mod.rs:98`，`GuardianRejectionCircuitBreaker`）：

| 常量 | 值 | 作用 |
|---|---|---|
| `MAX_CONSECUTIVE_GUARDIAN_DENIALS_PER_TURN` | 3 | 一回合内连续被拒 3 次 → 中断该回合 |
| `MAX_RECENT_AUTO_REVIEW_DENIALS_PER_TURN` | 10 | 最近窗口里被拒 10 次 → 中断 |
| `AUTO_REVIEW_DENIAL_WINDOW_SIZE` | 50 | 滑动窗口大小 |

连续否决超阈值，就 `InterruptTurn` 把这一回合停掉，避免无意义地烧 token。本章没有这层——它假设评审是一次性的、不会成环。

</details>

<details>
<summary>四、它在架构上的位置：一个 ext crate + 一条事件</summary>

Guardian 在真 Codex 里是一个**扩展（extension）**，不是 core 的硬编码部分。`ext/guardian/src/lib.rs` 把它实现成一个 `ThreadLifecycleContributor`，通过 `ExtensionRegistryBuilder::thread_lifecycle_contributor` 装进注册表；它持有一个 `AgentSpawner`，用来 fork 出评审子 agent（`spawn_subagent`）。

它对外的可见输出是一条事件 `GuardianAssessmentEvent`（`approvals.rs:178`），前端据此渲染"Guardian 正在评审 / 判了什么风险 / 理由是什么"：

```rust
pub struct GuardianAssessmentEvent {
    pub id: String,
    pub status: GuardianAssessmentStatus,   // InProgress / Approved / Denied / TimedOut / Aborted
    pub risk_level: Option<GuardianRiskLevel>,
    pub rationale: Option<String>,
    pub action: GuardianAssessmentAction,    // Command / Execve / ApplyPatch / NetworkAccess / McpToolCall
    ...
}
```

这条事件走的正是 [s10] 的 EQ（事件队列）。而 `action` 那个枚举说明 Guardian 评的不只是 shell 命令——`ApplyPatch`（补丁，见 [s03]）、`NetworkAccess`（出网）、`McpToolCall`（MCP 工具，见 [s15]）都在它的评审范围里。

最后一点也最重要：**用户始终能覆盖 Guardian。** 真源码里有 `AUTO_REVIEW_DENIED_ACTION_APPROVAL_DEVELOPER_PREFIX`——当用户手动批准了一个曾被 Guardian 拒掉的动作，会注入一条开发者消息告诉模型"用户已手动放行"。Guardian 是把关，不是独裁。

</details>

## 运行

```bash
python s14_guardian/code.py --demo   # 不需要模型：5 条动作 → 风险档 + 自动决定
python s14_guardian/code.py          # 交互模式：把你输入的命令喂给 guardian
```

`--demo` 完全离线（`backend=mock`）。

[s03]: ../s03_apply_patch/
[s04]: ../s04_approval/
[s05]: ../s05_sandbox/
[s10]: ../s10_sq_eq_protocol/
[s15]: ../s15_mcp/

## 小结

- Guardian = 一个自动风险评估器，接成"自动审批人"插在问用户之前：low→放行、critical→拒、medium/high→升级。
- 风险四档 `low/medium/high/critical` 是真源码 `GuardianRiskLevel` 的真实变体；产物 `GuardianAssessment` 是结构化 JSON。
- 真 Guardian 是一个 fork 会话、读转录、返回严格 JSON 的**评审 LLM 子 agent**，永远 fail-closed，带熔断器防失控。
- 它是 Codex 与 Claude Code 最鲜明的差异（⭐）：用"第二个 AI"替缺席的用户把关，为没人盯着时放大自主度而生。
- 它叠在审批（[s04]）/ 沙箱（[s05]）之上、不取代它们，且用户始终可覆盖。
- **生产级**：给把关的 AI 上保险——fail-closed（超时/崩溃/解析失败一律拒）、熔断器（`MAX_CONSECUTIVE_*=3` 防空转）、严格 JSON 契约 + 超时，并诚实承认"同源模型可能被同样的注入骗过"（见「生产级」一节）。
- 下一站 [s15 MCP：客户端 + 服务端](../s15_mcp/)：给 agent 接上外部工具生态——而 Guardian 的 `McpToolCall` 评审，评的正是这些外来工具。

## 思考

1. Guardian 用"第二个 AI"给"第一个 AI"把关。可这两个 AI 往往是同一家、同代的模型——如果第一个会被某条注入诱导，第二个会不会被同样的手法骗过？"用 AI 审 AI"在什么前提下才真的增加了安全，而不只是增加了一层同源的盲点？

2. 真 Guardian 永远 fail-closed：超时/出错/解析失败一律判拒。这对安全是对的，但放在 CI 里——一次评审超时就拦下一个本来没问题的部署命令。当"宁可错杀"撞上"流水线要绿"，你会怎么设计降级策略？错杀的代价和漏放的代价，在你的场景里哪个更高？

3. 本章把风险写成确定的规则；真 Guardian 的风险是模型现判的，意味着**同一条命令，两次评审可能给出不同档位**。这种不确定性，对一个本该"守规矩"的安全层来说是 bug 还是 feature？你愿意要一个偶尔判错但能理解上下文的评审员，还是一个永远一致但只会匹配字符串的规则表？

4. Claude Code 让用户自己判风险，Codex 派 AI 替用户判。前者尊重人的判断但要求人在场，后者解放了人但把判断权让渡给了模型。当 Guardian 帮你拦下/放行了成百上千条命令而你一条都没看过，你对这个系统的"信任"到底是建立在什么之上的？这种信任和你对 Claude Code 弹窗的信任，是同一种东西吗？
