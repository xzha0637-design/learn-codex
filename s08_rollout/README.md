# s08: Rollout — 把整场对话刻进磁盘

> *"上下文是给模型看的草稿，rollout 是给世界留的底稿。"*

[learn-codex 总览](../README.md) · [s07 Context Compaction](../s07_context_compaction/) → **s08** → [s09 Responses API](../s09_responses_api/)

---

## 先把思想说透：把「模型现在看的」和「世界永久留的」分成两份

上一章 [s07](../s07_context_compaction/) 为了省钱，会主动把旧对话压短、甚至替换成一句摘要。那问题就来了：如果喂给模型的历史越修越短，**那场对话「到底发生过什么」，还有谁完整记得？** 这一章的答案，藏在一个看似简单、却撑起 resume / rewind / 审计三件大事的设计里。想通下面三层就够了。

**道理一：喂给模型的那份历史，是一份「可以损耗的工作副本」——它本来就不该是权威记录。**
回想前几章：程序跑的时候，对话就是内存里的一个 `messages` 列表，它专门为「这一回合要喂给模型什么」服务。正因为是为模型服务，它就**容许损耗**：太长了要压（s07）、旧的要截、进程一退出它就烟消云散。把它当成草稿纸最贴切——你在上面写写画画、随时擦掉重写，方便当下思考，但**没人指望草稿纸能留存**。麻烦在于：如果整个系统**只有**这张草稿纸，那一关终端、一压缩，「这场对话干过什么」就永远找不回来了。

**道理二：所以另存一份「只增不改、一字不漏」的底稿——它和草稿纸是两码事。**
解法朴素得近乎笨：回合里每发生一件具体的事（你说一句话、模型说一句话、模型要跑一条命令、命令返回结果），就**老老实实往一个文件末尾追加一行**，从不回头改动已经写下的行。这份文件就是 rollout——一本**逐条流水账**。它和草稿纸的分工泾渭分明：草稿纸为「模型当下要看什么」服务，可以损耗；底稿为「世界事后要查什么」服务，必须完整。关键就两个动作：**写，只在末尾追加（绝不篡改旧行）；读，从头按顺序回放。** 「只追加」保证了历史不被改写——这正是日后能拿它当审计凭据的根基；写完一行就立刻逼它落到硬盘（而不是攒在缓冲区里），则保证进程哪怕下一秒崩掉，已经发生的事也稳稳躺在盘上、不会白跑。

**道理三（最关键）：正是「草稿可损耗 / 底稿不可损耗」这一刀切开，才同时换来了续接、回退、审计三种能力。**
为什么非要分这两份？因为一旦磁盘上有了一份「按回合切好、完整无缺」的底稿，三件原本做不到的事就全都顺理成章了：
- **续接（resume）**：把底稿从头读回来、重建成 `messages`，会话就能从上次断掉的地方接着跑——换台机器、隔几天都行，只要这个文件还在。
- **回退（rewind）**：底稿是按回合切分的，于是你能「砍掉最后 N 个回合、退回更早的状态重来」——模型钻进死胡同时尤其有用。
- **审计**：在云端、无人盯着的场景里出了问题，事后翻这本一字不漏的流水账，就能知道「它当时到底跑了哪条命令、模型怎么决策的」。

这三件事有一个共同的前提：**磁盘上得有一份不会被压缩、不会被篡改的完整记录。** 这恰好回应了上一章的隐忧——压缩尽管放心去修剪草稿（省 token），因为底稿这条线始终完整。两份记录各司其职、互不打架，这就是 rollout 的全部思想。

## 问题

回合循环（[s01](../s01_agent_loop/)）跑得很好——但它把整场对话只存在**内存里的一个 `messages` 列表**。一旦进程退出，这场对话就烟消云散。这带来三个真实痛点：

1. **没法续上。** 你跑了 40 分钟、agent 改了十几个文件，终端一关、CI 任务一结束，下次只能从零开始。
2. **没法回退。** 模型在第 7 个回合钻进了死胡同，你想退回第 5 个回合重来——可你手里没有「第 5 个回合」这个东西。
3. **没法审计。** 在云端 / 无人值守跑 agent，事后出了问题，你想知道「它当时到底执行了哪条命令、模型说了什么」——内存里的对话早没了。

更刁钻的是：[s07](../s07_context_compaction/) 的**压缩**为了省 token，会主动**丢掉**旧对话（把一段历史换成一句摘要）。喂给模型的上下文越修越短，可你要的「完整记录」反而被它破坏了。

## 解决方案

把对话**逐条落盘**：回合每产出一个 item（用户消息、模型消息、工具调用、工具结果），就**追加一行**到一个 rollout 文件。读回时把这些行**回放**成 `messages`，会话就能从断点继续。

教学版用 JSONL（一行一个 item，append-only，肉眼可读）。文件长这样：

```
   rollout.jsonl  (append-only，每行一个 RolloutItem)
   ┌────────────────────────────────────────────────────────┐
   │ {"timestamp": ..., "type":"session_meta", "payload":{…}} │ ← 第一行：会话头
   │ {"timestamp": ..., "type":"response_item","payload":{    │
   │     "type":"message","role":"user","content":"…"}}       │ ← 你说的话
   │ {…"type":"function_call","name":"shell",…}               │ ← 模型要跑的命令
   │ {…"type":"function_call_output","output":"…"}            │ ← 命令的结果
   │ {…"type":"message","role":"assistant","content":"…"}     │ ← 模型的回复
   └────────────────────────────────────────────────────────┘
       │  record_items() 一边跑一边往下追加 ↑（写完即 flush）
       │
       ▼  resume(path) 逐行读回，跳过 session_meta
   messages = [ {user…}, {function_call…}, {output…}, {assistant…} ]  → 接着跑
```

关键不是「存了」，而是：**写是只追加、读是按序回放**。压缩可以任意修剪内存里的上下文，rollout 这条底稿始终完整。

## 工作原理

看 [code.py](code.py)，三件事：

**第 1 步 — 起一个 recorder，先写会话头。** 真 Codex 的 rollout 第一行永远是 `SessionMeta`（conversation_id、cwd、git 信息……）：

```python
class RolloutRecorder:
    def __init__(self, path, meta=None):
        self._write_line("session_meta", meta or {"cwd": str(WORKDIR)})
```

**第 2 步 — 回合每产出 item 就追加一行，写完即 flush。** 这正是真源码 [`recorder.rs`](../../codex/codex-rs/rollout/src/recorder.rs) 里 `JsonlWriter::write_line` 的语义（`write_all` 后立刻 `flush`）——崩溃也不丢已写部分：

```python
def _write_line(self, item_type, payload):
    line = {"timestamp": ..., "type": item_type, "payload": payload}
    with self.path.open("a", encoding="utf-8") as f:
        f.write(json.dumps(line, ensure_ascii=False) + "\n")
        f.flush()
```

`run_turn(messages, recorder)` 在 s01 循环上只多了两行：模型产出后 `recorder.record_items(...)`、工具结果产出后再 `record_items([out_item])`。

**第 3 步 — `resume(path)` 把行回放成 `messages`。** 对应 `recorder.rs` 的 `get_rollout_history` / `load_rollout_items`：逐行解析，跳过 `session_meta`，把 `response_item` 的 payload 还原成对话 item，损坏行直接跳过：

```python
def resume(path):
    messages = []
    for raw in Path(path).read_text().splitlines():
        line = json.loads(raw)                 # 损坏行 try/except 跳过
        if line.get("type") == "response_item":
            messages.append(line["payload"])
    return messages
```

`--demo` 完整跑一遍：录一轮 `echo` → 打印落盘的 5 行 JSONL → `resume` 回放 → 断言「还原出的 `messages` 和录制时逐字节一致」，证明会话可无损续上。

**走一遍** —— 跟着 `--demo` 看一条对话从「内存」到「磁盘」再回到「内存」，每步数据长什么样：

1. **起 recorder，写下第一行会话头。** 用户说了句「执行 `echo hello from codex` 并告诉我结果」。recorder 先落一行 `session_meta`（会话头），再把用户消息落成一行 `response_item`：

   ```json
   {"timestamp":"…Z","type":"session_meta","payload":{"cwd":"…/learn-codex","conversation_id":"demo-0001"}}
   {"timestamp":"…Z","type":"response_item","payload":{"type":"message","role":"user","content":"执行 `echo hello from codex` 并告诉我结果"}}
   ```

   *为什么*第一行总是会话头：resume 时要先知道「这是哪场会话、在哪个目录」，再读后面的对话。

2. **回合每产出一个 item，就追加一行 + flush。** 模型决定调 `shell`，跑完拿到结果，最后回一句。这三件事各落一行（注意全是**追加**，前面的行一个字没动）：

   ```json
   {…"type":"response_item","payload":{"type":"function_call","call_id":"mock_call_1","name":"shell","arguments":"{\"command\": \"echo hello from codex\"}"}}
   {…"type":"response_item","payload":{"type":"function_call_output","call_id":"mock_call_1","output":"hello from codex"}}
   {…"type":"response_item","payload":{"type":"message","role":"assistant","content":"[mock] 工具已执行，结果片段：hello from codex"}}
   ```

   *为什么*写完就 `flush()`：万一进程在这里崩了，已经跑过的命令和拿到的结果都已经稳稳躺在磁盘上，不会白跑。

3. **resume：逐行读回，跳过会话头，重建 `messages`。** 文件现在有 5 行（1 行会话头 + 4 个对话 item）。`resume()` 逐行 `json.loads`，**跳过 `session_meta`**，把 4 条 `response_item` 的 `payload` 取出来按序放进列表：

   ```
   重建出 4 个对话 item：
     [user]      执行 `echo hello from codex` 并告诉我结果
     [call]      shell {"command": "echo hello from codex"}
     [output]    hello from codex
     [assistant] [mock] 工具已执行，结果片段：hello from codex
   ```

   *为什么*跳过会话头：它是「关于这场会话的元信息」，不是对话内容本身，不能混进喂给模型的 `messages`。

4. **断言无损。** demo 最后 `assert restored == messages`——读回来的列表和录制时**逐字节一致**。这就是「会话可无损续接」的硬证据：换台机器、隔几天，只要这个文件还在，对话就能从这里接着跑。

> 对照真源码：这套「写一行 + flush」正是 `recorder.rs` 里 `JsonlWriter::write_line`（`write_all` 后立刻 `flush`）的语义；resume 对应 `get_rollout_history` / `load_rollout_items`。教学版省了 SQLite 索引和 zstd 压缩，但「append-only + 按序回放」的骨架一模一样。

## 生产级：留底要经得起断电——append-only + flush + 可重试

rollout 是 Codex 的"黑匣子"：resume、rewind、审计全靠它。所以它最不能容忍的就是**写丢**。生产级的持久化盯三件事：

- **append-only**：每产出一个 item 就**追加一行**（`.append(true)`，[`recorder.rs:727`](../../codex/codex-rs/rollout/src/recorder.rs)），从不回头改已写的内容。崩溃最多丢**最后一行**（写到一半那条），前面的全都在——重放时跳过损坏的尾行即可。这就是 append-only 比"整文件覆盖"在崩溃面前稳得多的原因。
- **显式 flush**：`flush()`（recorder.rs:825）确保"之前的写都落到盘上"。在 resume / 关键节点前 flush，断电也不丢已确认的回合。
- **失败可重试**：打开/写文件失败不是致命——记下来，"a later `persist()` or `flush()` can retry"（recorder.rs:803）。磁盘暂时满了、文件被占用，下次再写，而不是丢掉这一整段会话。

> 一句话：留底系统的生产级，不在"记了什么"，而在"**断电/崩溃时它还剩多少**"。append-only + flush + 可重试，就是为了让答案是"几乎全在"。压缩（[s07](../s07_context_compaction/)）省的是窗口，rollout 保的是这份经得起断电的底稿。

## 🆚 与 Claude Code 的不同

| | Claude Code | Codex |
|---|---|---|
| 持久化对象 | session history（会话记录，主要供本地续聊） | **完整 rollout**：每个 item、工具调用、错误、token 计数都落盘 |
| 能力 | 恢复会话 | resume（续跑）+ **rewind（回退 N 个回合）** + **审计** |
| 谁来消费 | 本地交互 | 同一份 rollout 同时驱动**本地 TUI / `codex exec`（无头）/ Codex Web（云端）** |
| 与压缩的关系 | —— | 压缩修剪上下文，rollout 仍保留全量原始历史，两者**正交** |

**为什么？** 因为 Codex 把宝押在「**低人工干预的自主运行**」上——`codex exec` 在 CI 里、agent 在云端，**没有人盯着终端**。这种场景对持久化的要求和「本地交互式续聊」是两个量级：

- **云 + 无头需要「持久」**：进程随时可能被调度走、被重启，对话必须在磁盘上有一份权威副本，换台机器也能 `resume` 接上。
- **自主运行需要「可审计」**：没人实时把关，事后追责就靠这份逐条记录——它当时到底跑了什么、模型怎么决策的，一行不少。
- **多前端需要「单一事实源」**：你在网页上开的会话，回到终端 `codex exec --resume` 还能接着干，因为大家读的是同一份 rollout。

这正是全课主线在持久化上的投影：Claude Code 围绕**交互式 UX**存「够续聊的历史」；Codex 为**headless / CI / 云**存「可 resume、可 rewind、可审计的完整底稿」。

## 深入：教学版 vs 真 Codex 源码

教学版的 `RolloutRecorder` 大约 40 行、一个 JSONL 文件。真 Codex 的 rollout 子系统是 `codex-rs/rollout/`（recorder 单文件就 1600+ 行），外加一整个 `codex-rs/state/` SQLite 运行时。下面四块讲清差距。

<details>
<summary>一、存储：教学版 JSONL，真版是 JSONL 会话文件 + SQLite state.db + zstd</summary>

教学版图省事用一个 `rollout.jsonl`。真 Codex 是**两层存储并存**：

| 维度 | 教学版 | 真 codex-rs |
|---|---|---|
| 会话文件 | `_demo_workspace/rollout.jsonl` | `~/.codex/sessions/YYYY/MM/DD/rollout-<时间>-<id>.jsonl` |
| 文件名 | 固定 | `rollout-2025-05-07T17-24-21-<uuid>.jsonl`（冒号换成 `-`，兼容文件系统） |
| 索引 / 列表 | 无 | **SQLite `state.db`**（`codex-rs/state/`，`StateRuntime`）做线程列表、搜索、分页 |
| 冷数据 | 永不压缩 | 后台 worker 把冷文件压成 **`.jsonl.zst`**（`compression.rs`，依赖 `zstd`） |
| 续写压缩文件 | —— | 先 `materialize_rollout_for_append` 把 `.zst` 解回 `.jsonl` 再 append |

为什么真版要 SQLite？因为 `codex resume` 要在成千上万个历史会话里**按时间排序、分页、全文搜索**——遍历一堆 JSONL 文件做不到这些，而 SQLite 的索引天生擅长。JSONL 仍是**权威的逐行记录**（人能 `jq` 直接看），SQLite 是**它上面的可查询索引层**。文件名里塞时间戳和 UUID，则是为了不开数据库也能从文件名排序、定位会话。

```rust
// recorder.rs：每行就是 {timestamp} + 一个 flatten 进来的 RolloutItem
let line = RolloutLineRef { timestamp, item: rollout_item };
let mut json = serde_json::to_string(item)?;
json.push('\n');
self.file.write_all(json.as_bytes()).await?;
self.file.flush().await?;          // ← 教学版照搬了这个「写完即 flush」
```

</details>

<details>
<summary>二、RolloutItem 到底记什么：五个变体 + 一套持久化策略</summary>

教学版只记两类（`session_meta` / `response_item`）。真 `RolloutItem` 是个枚举，有五个变体（`protocol.rs:2827`）：

```rust
pub enum RolloutItem {
    SessionMeta(SessionMetaLine),   // 会话头：id / cwd / git / 版本 / 模型 provider…
    ResponseItem(ResponseItem),     // 对话本体：message / function_call / output / reasoning…
    Compacted(CompactedItem),       // ★ 压缩标记：被换掉的历史 + 替代摘要（与 s07 直接相关）
    TurnContext(TurnContextItem),   // 回合上下文快照（当时的 cwd / 审批策略 / 模型…）
    EventMsg(EventMsg),             // 少数关键事件（token 计数、patch 应用结果、回退…）
}
```

而且真版**不是什么都记**——`policy.rs` 有一套 `is_persisted_rollout_item` / `should_persist_response_item`：会进历史的 `Message / FunctionCall / FunctionCallOutput / Reasoning` 等**记**；纯增量的 `*Delta`、`ExecApprovalRequest`、`McpStartupUpdate` 这类**瞬态/UI 事件不记**。所以 rollout 里既有「模型说了什么、跑了什么」，也有「花了多少 token、patch 应用成没成」，但不会被每个生命周期事件灌爆。

教学版把这套策略简化成「全记」，因为我们的 item 种类本来就少。

</details>

<details>
<summary>三、resume 与 rewind：续跑，以及回退 N 个回合</summary>

教学版 `resume()` = 读全文件 → 还原 `messages`。真版 `get_rollout_history`（`recorder.rs:912`）多做几件事：解析出 `conversation_id`、把结果包成 `InitialHistory::Resumed { conversation_id, history, rollout_path }`（空文件则返回 `InitialHistory::New`）、并能从 `.jsonl.zst` 透明读回。

**rewind（回退）**则是 resume 之外的另一种「时间操作」，由一个独立的 `Op` 触发（`protocol.rs:583`）：

```rust
/// 把内存上下文里最后 N 个用户回合丢掉。
/// 注意：它不负责回滚磁盘上的文件改动——那由客户端自己撤销。
ThreadRollback { num_turns: u32 },
```

也就是说：**resume 是「把底稿读回来从尾部继续」，rewind 是「把尾部砍掉 N 段重来」**。两者都建立在「有一份按回合切分的完整记录」之上——没有 rollout，这两件事都无从谈起。失败时还有专门的 `ThreadRollbackFailed` 错误码。

</details>

<details>
<summary>四、与压缩（s07）的分工：一个修剪上下文，一个保全底稿</summary>

这是最容易混淆、也最能体现设计意图的一点。[s07](../s07_context_compaction/) 的压缩**会主动丢历史**：把一长段旧对话替换成一句摘要，好让喂给模型的 token 变少。如果 rollout 跟着上下文一起被压，那「完整记录」就成了空话。

真 Codex 的解法是让两者**正交**：压缩这件事本身也被当成一个 `RolloutItem::Compacted` **记进 rollout**——它保存了「被换掉的原始历史 `replacement_history` + 替代摘要 `message`」（`protocol.rs:2836`）。于是：

| | 喂给模型的上下文 | rollout 底稿 |
|---|---|---|
| 压缩发生时 | 旧对话 → 被摘要替换，变短 | 追加一条 `Compacted`，**原始历史仍在文件里** |
| resume 时 | 用压缩后的上下文继续（省 token） | 仍可读到压缩前的全量记录（可审计） |

一句话：**压缩优化的是「当下要花的钱」，rollout 守护的是「事后能追的账」。** 二者不打架，正因为压缩动作本身也被忠实地记进了底稿。

</details>

## 运行

```bash
python s08_rollout/code.py --demo   # 录制 → 落盘 jsonl → resume 回放（mock，无需 key，自动清理）
python s08_rollout/code.py          # 交互模式：你的每句话、每次工具调用都被记进 rollout
```

默认 `backend=mock`，离线可跑。`--demo` 会在 `_demo_workspace/` 下临时写一个 `rollout.jsonl`，跑完自动 `rmtree` 清理。

## 小结

- 回合每产出一个 item 就**追加一行**落盘（append-only + 写完即 flush）；`resume` 逐行回放成 `messages`，会话从断点继续。
- 真 Codex 用 **SQLite（state.db）做索引 + JSONL 会话文件 + 冷文件 zstd 压缩**；`RolloutItem` 有 5 个变体，且有一套「记什么/不记什么」的持久化策略。
- rollout 是 **resume / rewind（`ThreadRollback`）/ 审计**的共同地基，也是 Codex Web 和 `codex exec` 共享的单一事实源。
- 它和压缩（[s07](../s07_context_compaction/)）**正交**：压缩省的是 token，rollout 保的是全量底稿（连压缩动作本身都记进去）。
- **生产级**：留底要经得起断电——append-only（崩溃最多丢最后一行）、关键点 `flush()` 落盘、写失败可重试不丢会话（见「生产级」一节）。
- 下一站 [s09 Responses API](../s09_responses_api/)：本章一直在记录、回放 `message` / `function_call` / `function_call_output` 这套 item——下一章就掀开引擎盖，看这套形状到底从哪来、模型究竟是怎么被调用的。

## 思考

1. 教学版 `resume` 把整个文件读进内存再回放。如果一个会话跑了三天、rollout 有几十万行（还压成了 `.zst`），这种「全量读回」会出什么问题？真版为什么要在 JSONL 之上再压一层 SQLite 索引——纯靠文件名能扛住「按时间分页 + 全文搜索」吗？

2. `ThreadRollback` 的注释明说：它只丢内存上下文里的回合，**不回滚磁盘上已经发生的文件改动**。那么「rewind 到第 5 个回合」之后，工作区其实还停在第 7 个回合的状态——这种「对话回退了、文件没回退」的错位，会不会让模型更糊涂？换成你，会让 rewind 一并撤销文件改动吗，代价是什么？

3. rollout 把模型说的每句话、跑的每条命令、甚至 token 花费都**永久留底**。这对「云端 / 无人值守」是审计刚需，但同一份底稿也意味着敏感信息（密钥、客户数据）被原样写进磁盘。Codex 选择「全记 + 内核沙箱兜底」，Claude Code 选择「轻量历史 + 交互式审批」——如果是你来定策略，哪些 item 值得永久留底，哪些该脱敏或干脆不记？

4. 压缩（s07）为省 token 丢历史，rollout 却坚持留全量——本章说它俩「正交」，靠的是把压缩动作本身也记成一个 `Compacted` item。可如果连「被换掉的原始历史」也一并存进 rollout，那压缩省下的磁盘到底省在哪？这种「上下文省、底稿不省」的取舍，在什么场景下会反过来咬你一口？
