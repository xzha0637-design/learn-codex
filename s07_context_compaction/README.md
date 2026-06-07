# s07: Context Compaction — 对话太长就把旧回合压成一句摘要

> 🌐 [English](README.en.md) · **中文**

> *"上下文窗口是有限的；与其撞墙，不如把走过的路压成一张地图。"*

[learn-codex 总览](../README.md) · [s06 AGENTS.md](../s06_agents_md/) → **s07** → [s08 Rollout 续接](../s08_rollout/)

---

## 先把思想说透：上下文是有限的，所以要「先用便宜招、最后才动用模型」

每次找模型说话，你都得把「到目前为止发生过的全部对话」重新打包发过去——模型自己不记事，全靠你每回把历史一并喂给它。问题来了：这坨历史会越堆越大，而模型一次能吃下的量是**有上限**的。这一章就是在回答一个很现实的问题：**历史装不下了，怎么办？** 想通下面三层，你就抓住了它的全部巧思。

**道理一：模型的「记忆」其实是你每次重发的那一坨历史，而它有一道物理天花板。**
你可以把和模型的每一次通话想象成「把一摞纸塞进一个固定大小的信封」——这摞纸就是迄今为止的所有对话，信封大小就是模型一次能读的上限。纸越堆越厚：读个文件、跑条命令、看段输出，每一步都往里加几张。塞不下了会发生两件坏事：要么直接撑爆、报错、整个回合崩掉；要么勉强塞进去，但模型被几十页陈年细节淹没，反而越看越糊涂。所以「历史无限增长」和「信封大小固定」这对矛盾，迟早要正面解决。

**道理二：最笨的解法是「把最旧的纸直接扔掉」，但那会扔掉关键决策——真正要的是「浓缩」而不是「丢弃」。**
最省事的想法是：满了就把最早那几张纸撕掉。可那几张纸上可能写着「我们权衡后决定用方案 B、放弃了 A」——撕掉之后模型一回头就失忆，又开始纠结早就拍板的事。所以聪明的做法不是扔，而是**浓缩**：把一长段啰嗦的旧过程，换成一小段精炼的「交接摘要」。这正像你交接工作时，绝不会把三个月的聊天记录原样甩给接手人，而是写一页纸——「现在进度到哪、做过哪些决定、还差什么」。原文没了，但**要点还在**。这就是「压缩」两个字的真意：用一句话顶替掉一大段，把信封重新腾空。

**道理三（最关键）：浓缩要花钱，所以顺序是「先用便宜的招数，逼不得已才动用最贵的那一招」。**
怎么把一大段对话浓缩成一句话？最聪明、最准的办法是**再请模型读一遍、让它自己写摘要**——但这等于额外多打一次电话，又慢又费钱。所以真正的工程智慧不在「会压缩」，而在**省着用这一招**：能用便宜手段腾地方，就绝不轻易惊动模型。于是有了一条由便宜到昂贵的阶梯——先把又臭又长的工具输出**就地截短**（几乎不要钱），最近的细节**原样留着**（白送），实在不够了，才把最旧的一大段**交给模型总结**（最贵、放最后）。本章离线演示时，连「请模型」这步都用一段确定性的拼接规则替身，但顺序的精神一模一样：**廉价的截断在前，昂贵的模型摘要垫底。**

还有一个让你能安心压缩的底气：**被压掉的东西并没有真正消失**。喂给模型的这份历史只是「当下要用的工作副本」，可以随便修剪；与此同时，完整、一字不漏的原始记录另有一份落在磁盘上（就是下一章 [s08](../s08_rollout/) 的 rollout）。正因为「省钱的副本」和「保全的底稿」是两码事，你才敢放手把工作副本压短——天塌不下来。

把这四点连起来：信封有上限 → 不能扔只能浓缩 → 浓缩按「便宜在前、模型摘要垫底」的顺序 → 而且压的只是工作副本、原件另存。这就是上下文压缩的全部思想。

## 问题

agent 干一件复杂的活，往往要几十个来回：读文件、跑命令、看输出、再改、再跑……每一步都往对话历史里堆消息和工具输出。可模型的**上下文窗口是有限的**——堆到一定程度，要么直接报「context window exceeded」、回合崩掉，要么虽然没崩，但模型被海量陈年细节淹没、越来越不准。

最朴素的办法是「丢掉最旧的消息」。但这会丢掉关键决策（「我们之前决定用方案 B 而不是 A」），模型一回头就失忆。

真正要的是：**在不超窗口的前提下，尽量保住「已经发生过什么、做了哪些决定、还剩什么没做」**——把冗长的中间过程压成一段精炼的交接摘要，把最近的细节原样留着。

## 解决方案

当消息列表超过预算（本章用**总字符数**当 token 的廉价代理），就触发压缩：

1. 把列表切成「最旧的一批」和「最近的 N 个」；
2. 把最旧的一批交给模型（离线时用确定性启发式）总结成**一条** `[summary]` 消息；
3. 新历史 = `[摘要]` + `[最近 N 个 item]`。

```
   压缩前（超预算）                      压缩后（回到预算内）
   ┌─────────────────────┐             ┌─────────────────────┐
   │ user  第1步…         │  ┐          │ [summary] 压了最早    │
   │ tool_call shell      │  │ 最旧      │   13 项，要点回顾：    │ ← 一条摘要
   │ tool_result …(长)    │  │ 一批      │   - 第1步 列目录       │
   │ user  第2步…         │  │ 压成      │   - 第2步 读 README   │
   │ …（共 13 项）        │  ┘ 摘要      │   - …                 │
   │ user  第5步…         │  ┐          ├─────────────────────┤
   │ tool_call shell      │  │ 最近      │ user  第5步…          │
   │ tool_result …        │  │ 6 项      │ tool_call shell       │ ← 原样保留
   │ …                    │  │ 原样      │ tool_result …         │
   │ user  请总结一下      │  ┘ 保留      │ user  请总结一下       │
   └─────────────────────┘             └─────────────────────┘
        19 项                                 1 + 6 = 7 项
```

摘要本身被编码成一条 `user` 消息（这点和真源码一致），靠一个前缀（教学版 `[summary]`，真源码 `SUMMARY_PREFIX`）标记身份，方便后续识别、避免被二次压缩。

## 工作原理

看 [code.py](code.py)：

**触发** — `total_chars(messages)` 把所有可见文本（含工具参数/输出）字符数相加，当作 token 用量代理；超过 `BUDGET_CHARS` 就压：

```python
def compact(messages, model=None):
    if total_chars(messages) <= BUDGET_CHARS or len(messages) <= KEEP_RECENT:
        return messages                       # 没超预算，原样返回
    split = len(messages) - KEEP_RECENT
    old, recent = messages[:split], messages[split:]
    summary_item = user_item(summarize(old, model))   # 旧回合 → 一条摘要
    return [summary_item, *recent]            # 摘要 + 最近 N 项
```

**摘要** — `summarize` 离线时走确定性启发式：把每个旧 item 用 `item_text` 压成一行（用户消息截断、工具调用只留名、工具输出大幅裁短），再拼成一段「要点回顾」。真源码这里是把旧历史发给模型、用 `SUMMARIZATION_PROMPT` 让它产出 handoff 摘要——但机制骨架一样：**旧的多条 → 新的一条**。

**接进回合** — `run_turn` 在每回合开始前先 `compact()` 一次（proactive 压缩），打印 `before → after` 的项数变化。

这对应真源码 [`core/src/compact.rs`](../../codex/codex-rs/core/src/compact.rs)：`build_compacted_history` 组装「近期用户消息 + 摘要」的新历史，`SUMMARY_PREFIX` 标记摘要，`is_summary_message` 做前缀识别。触发分两路：**proactive**（`session/turn.rs` 里 `token_limit_reached` 时调 `run_auto_compact`）和 **reactive**（`compact.rs` 主循环命中 `CodexErr::ContextWindowExceeded` 时，从头移除最旧 item 再重试）。此外还有两个**服务端**压缩变体 [`compact_remote.rs`](../../codex/codex-rs/core/src/compact_remote.rs) 与 [`compact_remote_v2.rs`](../../codex/codex-rs/core/src/compact_remote_v2.rs)。

`--demo` 直接演示：造一段约 20 项的假对话，用很小的预算触发压缩，打印 before/after 项数 + 产出的摘要（离线、确定性、无需模型）。

**走一遍** —— 跟着 `--demo` 的一次真实压缩，看每一步数据长什么样、为什么这么做：

1. **造一段长对话。** `build_long_conversation()` 造出 19 个 item：6 轮「用户问 → 工具调用 → 工具结果」再加一句收尾。前几项长这样：

   ```
   user:         第1步：请列出目录。
   function_call: shell  {"command": "do-列出目录"}
   function_call_output: 列出目录 的输出 xxxxxxxx…(故意撑到 40 个 x，模拟冗长输出)
   user:         第2步：请读 README。
   …（如此 6 轮）…
   user:         好的，最后请帮我总结一下我们刚才做了哪些事。
   ```

2. **算账，发现超预算。** `total_chars(messages)` 把所有可见文本字符相加 = **532 字符**，而 `BUDGET_CHARS = 400`。532 > 400 → 触发压缩。*为什么*：这就是「快撑爆窗口」的模拟——真源码这里换成「估算的 token 数接近窗口上限」。

3. **切两段。** `KEEP_RECENT = 6`，所以 `split = 19 - 6 = 13`：最旧的 **13 项**要被压，最近的 **6 项**原样保留。*为什么*留最近的：刚发生的细节对「接着干」最有用，旧的过程才是可以浓缩的。

4. **把旧的 13 项压成一条摘要。** `summarize(old)` 把每项压成一行（用户消息截断、工具调用只留名 `shell`、工具输出大幅裁短），拼成一条带 `[summary]` 前缀的消息：

   ```
   [summary] 压缩了最早的 13 个对话项；要点回顾：
     - user: 第1步：请列出目录。
     - tool_call: shell
     - tool_result: 列出目录 的输出 xxxxxxxxxxxxxxx…
     - user: 第2步：请读 README。
     - …
     - user: 第5步：请看依赖版本。
   ```

   *为什么*工具输出被砍得最狠：回顾时只需知道「跑过、大致结果」，那 40 个 x 没有保留价值。

5. **拼出新历史。** `[摘要] + 最近 6 项` = **7 项**。19 → 7，对话被压回了预算量级，但「做过哪些事」靠摘要还在、最近的上下文一字未动。demo 最后断言 `首项是摘要 且 总项数 == 1 + 6`，校验通过。

> 一个细节：压缩后字符数可能不降反升（demo 里 532 → 575），因为这段假对话每项都很短，而摘要把 13 项的要点都列了出来。真实场景里被压的是**又多又长**的工具输出，摘要再啰嗦也远比原文短——这里只是项数少、字符省不明显，不影响机制演示。

## 生产级：估算会失准——reactive 压缩是最后一道闸

proactive 压缩靠**估算**（字符数代理 token）决定何时压。可估算永远会失准：不同 tokenizer、reasoning 占的预算、工具输出的突发膨胀……总有一刻你以为没超、实际却**撞了模型的硬上限**。这时原样重发只会再撞一次。生产级必须有一条 **reactive（被动）兜底**：真撞上 `ContextWindowExceeded`，就**从头删最旧一项、重试**，直到放得下。对应 [`compact.rs`](../../codex/codex-rs/core/src/compact.rs) 主循环的 `history.remove_first_item()` + `retries = 0` 重试。

本章 `--demo` 演示了这条：proactive 以为没事，但模型硬上限更紧（约 12 项），撞墙后自动逐项裁剪重试：

```
proactive 以为没事，但模型硬上限约 12 项——撞墙后自动从头删项重试：
  ⚠ ContextWindowExceeded → 删最旧一项（user: 第1步：请列出目录。…），剩 18 项后重试
  ⚠ ContextWindowExceeded → 删最旧一项（tool_call: shell…），剩 17 项后重试
  ...
  → ok：12 项放下了，回合成功
```

两路缺一不可：**proactive 省钱**（趁早压、少烧 token），**reactive 保命**（估错了也不至于整轮失败）。一个是优化、一个是正确性兜底——真 Codex 两者并存（turn.rs 的 `run_auto_compact` + compact.rs 的撞墙重试），不是二选一。

> 还有一处生产级细节：删的是**最旧**的项而非随机删，因为最近的上下文对"接着干"最值钱；而被压掉的完整历史并没丢——它仍在 rollout（[s08](../s08_rollout/)）里，可回放可审计。压缩省的是**上下文窗口**，不是**底稿**。

## 🆚 与 Claude Code 的不同

压缩这件事两边**很像**（≈）——都把历史总结成摘要以适配上下文：

| | Claude Code | Codex |
|---|---|---|
| 核心思路 | 摘要旧历史以适配窗口（≈） | 摘要旧历史以适配窗口（≈） |
| 触发 | 接近窗口上限时压缩（proactive + reactive 重试） | 同样 proactive（token 上限）+ reactive（`ContextWindowExceeded`） |
| 压缩在哪跑 | **客户端/本地** | 本地 **＋ 服务端**（`compact_remote` / `compact_remote_v2`） |
| 摘要载体 | 摘要消息 | 一条 `user` 角色摘要消息（`SUMMARY_PREFIX` 前缀） |
| 与完整记录的关系 | — | 压缩只改「活动历史」，完整历史另由 rollout（[s08]）落盘 |

**为什么 Codex 多一条服务端压缩？** 还是那条主线：Codex 为 **headless / CI / 云端**自主运行下注。云端的 Codex agent 可以让**服务端**直接对会话做压缩（`compact_remote`，乃至带 rollout trace 的 `compact_remote_v2`），客户端只需消费结果——这对长时间无人值守的云任务更省、更稳。而本地的 `run_inline_auto_compact_task` 作为**兜底**：当 provider 不支持远程压缩（`should_use_remote_compact_task` 为假）时就走本地。Claude Code 以本地交互式为中心，把压缩放在客户端已经够用，没有「服务端替你压」这一路。

[s08]: ../s08_rollout/

## 深入：教学版 vs 真 Codex 源码

<details>
<summary>一、触发：proactive 主动压 vs reactive 撞墙后压</summary>

教学版只有一种触发：每回合开始前 `total_chars > BUDGET_CHARS` 就压（proactive）。真源码两路并存：

| 路径 | 触发点 | 真源码 |
|---|---|---|
| **Proactive（主动）** | 估算 token 接近 `model_auto_compact_token_limit` | `session/turn.rs`：`token_limit_reached && needs_follow_up` → `run_auto_compact(...)` |
| **Reactive（被动）** | 真的撞上 `CodexErr::ContextWindowExceeded` | `compact.rs` 主循环：`history.remove_first_item()` 从头删一项、`retries = 0` 重试 |

reactive 这一路很关键：即使估算失准、真把窗口撑爆了，也不是直接崩——而是**从最前面（最旧）逐项删除**再重试。从前面删是为了**保住前缀缓存**（prefix cache）：模型 API 的缓存按前缀命中，保留靠后的近期消息更划算。教学版的字符代理只是「token 估算」的极简版，真源码的 token 计量要精细得多（区分 scope、window ordinal、prefill 等）。

</details>

<details>
<summary>二、压缩后保留什么：近期用户消息 + 摘要，工具输出被裁</summary>

教学版新历史 = `[摘要] + 最近 KEEP_RECENT 项`。真源码 `build_compacted_history` 更讲究：

```rust
// compact.rs：新历史 = [初始上下文] + [近期 user 消息（按 token 预算从后往前选）] + [摘要]
let mut new_history = build_compacted_history(Vec::new(), &user_messages, &summary_text);
```

- **只保「真实用户消息」**：`collect_user_messages` 过滤掉摘要消息本身（`is_summary_message`），避免摘要套摘要。
- **按 token 预算从后往前选**：`COMPACT_USER_MESSAGE_MAX_TOKENS = 20_000`，从最近的用户消息往回收，超预算就 `truncate_text` 裁断。
- **初始上下文重新注入**：压缩会清掉历史，于是用 `InitialContextInjection` 决定是否把「初始上下文」（环境、AGENTS.md 等）**重新垫回**。mid-turn 压缩用 `BeforeLastUserMessage`（垫在最后一条真实用户消息之前，因为模型被训练成「压缩摘要应是历史最后一项」）；手动/回合前压缩用 `DoNotInject`（下一个常规回合会自然重注入）。

教学版省掉了「初始上下文重注入」和「按 token 预算选用户消息」，但保留了最核心的「旧多条 → 摘要 + 近期项」骨架。

</details>

<details>
<summary>三、摘要怎么生成：SUMMARIZATION_PROMPT 与 SUMMARY_PREFIX</summary>

教学版 `summarize` 是确定性启发式（逐行拼接 + 截断），为的是**离线、无需模型**。真源码是让模型产出摘要：

- `SUMMARIZATION_PROMPT`（`prompts/templates/compact/prompt.md`）的指令是「你在做 CONTEXT CHECKPOINT COMPACTION，为接手的 LLM 写一份 handoff 摘要」，要求包含：当前进度与关键决策、重要约束/用户偏好、剩余待办、关键数据/引用。
- 摘要产出后，前面拼上 `SUMMARY_PREFIX`（`summary_prefix.md`，一段「另一个语言模型产出了它的思考摘要，请基于它继续、别重复劳动」的引导文本），再作为一条 `role="user"` 的消息存进历史。

```rust
let summary_suffix = get_last_assistant_message_from_turn(history_items).unwrap_or_default();
let summary_text = format!("{SUMMARY_PREFIX}\n{summary_suffix}");
```

也就是说，压缩本身**是一次真实的模型回合**：把旧历史喂进去，让模型「输出一段摘要」，再用这段摘要替换历史。教学版用启发式替代这次回合调用，骨架（`SUMMARY_PREFIX` + 一条 user 消息）保持一致。

</details>

<details>
<summary>四、服务端压缩 remote / remote_v2 与本地的关系</summary>

教学版只有本地一路。真源码 `run_auto_compact`（`session/turn.rs`）是个**三向分派器**：

| 条件 | 走哪条 | 实现 |
|---|---|---|
| provider 支持远程 + 开启 `RemoteCompactionV2` | 远程 v2 | `compact_remote_v2.rs::run_inline_remote_auto_compact_task` |
| provider 支持远程（默认远程） | 远程 v1 | `compact_remote.rs::run_inline_remote_auto_compact_task` |
| provider 不支持远程 | **本地兜底** | `compact.rs::run_inline_auto_compact_task` |

判定靠 `should_use_remote_compact_task(provider)`（即 `provider.supports_remote_compaction()`）。远程压缩把「总结旧历史」这件事下沉到**服务端**完成，客户端拿回压缩好的历史；`compact_remote_v2` 还接入了 rollout trace（`CompactionCheckpointTracePayload`）做可观测。三条路共享同一套骨架函数（`insert_initial_context_before_last_real_user_or_summary`、`compaction_status_from_result`、pre/post-compact hooks），只是「摘要在哪算」不同。

一句话：教学版 ~60 行的「超预算 → 旧的压成一条摘要 + 留近期项」，就是 `compact.rs`（600+ 行）的核心；其余都是 token 精算、初始上下文重注入、pre/post hooks、analytics，以及**本地/远程 v1/远程 v2 三套实现**。

</details>

## 运行

```bash
python s07_context_compaction/code.py --demo   # 造长对话演示压缩 before/after（mock，无需 key，摘要离线生成）
python s07_context_compaction/code.py          # 交互模式：聊到超预算会自动压缩
```

`--demo` 全程离线、不调用模型，摘要由确定性启发式生成，结束打印 `19 项 → 1 摘要 + 6 近期项 = 7 项` 的校验。

## 小结

- 上下文窗口有限：超预算就把**最旧的一批回合**压成一条 `[summary]` 摘要，保留最近 N 项。
- 触发两路：proactive（token 接近上限主动压）+ reactive（撞上 `ContextWindowExceeded` 从头删项重试）。
- 压缩本身是一次模型回合（产出 handoff 摘要）；保住的是「近期细节 + 关键决策」，冗长工具输出被裁。
- Codex 与 Claude Code 的压缩思路≈，但 Codex 额外有**服务端压缩**（`compact_remote` / `_v2`），本地作兜底——为云端自主运行下注。
- 压缩只改「活动历史」；完整、未删减的历史另由 rollout 落盘，两者正交。
- **生产级**：proactive 估算会失准，必须有 reactive 兜底——真撞 `ContextWindowExceeded` 就从头删最旧项重试直到放下（见「生产级」一节）。两路缺一不可：proactive 省钱、reactive 保命。
- 下一站 [s08 Rollout 续接](../s08_rollout/)：压缩省的是 token、rollout 保的是全量底稿——连「压缩」这个动作本身都会被记进去，于是会话可续接、可回放。

## 思考

- 压缩用「字符数 / token 估算」决定何时触发，但估算可能失准。Codex 的兜底是 reactive——真撞墙了再从头删项重试。如果是你，会更信任「提前主动压」还是「撞墙再补救」？两者的成本（多花一次模型回合 vs 一次失败重试）你怎么权衡？
- 摘要是模型自己写的——这意味着「记住什么、丢掉什么」由模型裁决。如果它在摘要里漏掉了一个关键决策（「我们放弃了方案 A」），后续回合会基于残缺记忆继续。你会怎么降低这种「摘要漏关键信息」的风险？
- Codex 把压缩**下沉到服务端**（remote），客户端只消费结果。这省心，但也意味着「我的对话怎么被压、压成了什么」发生在你看不见的地方。对一个 headless 云任务，这种「服务端替你压」是该欢迎的便利，还是该警惕的黑箱？
- 压缩删减「活动历史」，而 rollout（s08）保留**完整**历史。既然完整记录都在，为什么不干脆每次都从完整历史里现算上下文，而要维护一个被压缩过的活动历史？「省钱省窗口」和「永不失忆」之间，这条边界你会画在哪？
