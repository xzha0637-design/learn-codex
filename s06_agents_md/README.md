# s06: AGENTS.md — 从 cwd 一路向上，把项目规则分层注入

> *"项目的规矩写在文件里，沿着目录树一层层叠加——根定基调，子包微调。"*

[learn-codex 总览](../README.md) · 上一章：[s05 sandbox](../s05_sandbox/) → **s06** → 下一章：[s07 context compaction](../s07_context_compaction/)

---

## 先把思想说透：为什么要给模型"喂项目记忆"，又为什么要"顺着目录树往上找"

前几章的 agent 有手（能改文件）、有锁（沙箱）。但它还有个根本短板：**它对你这个项目一无所知**。这一章解决的就是"让它入乡随俗"。两个道理讲透，你就懂了 AGENTS.md 为什么长这样。

**道理一：模型不是不守规矩，是它压根不知道你的规矩——所以得把规矩写下来递给它。**
你新来一个能力很强的外包同事，第一天他不可能知道"我们这缩进用 2 个空格""提交信息写中文""`legacy/` 那个目录谁都别碰"。他不是故意捣乱，是**没人告诉过他**。模型也一样：它每开一个新对话都像"失忆的第一天"，于是缩进忽 2 忽 4、提交信息一会儿中文一会儿英文。
怎么办？最朴素也最有效的办法——**把项目的规矩写成一张纸条，每次干活前先塞给它读一遍**。这张纸条就是 `AGENTS.md`：一个放在项目里的纯文本文件，里面是给模型看的"本项目须知"。机制朴素到近乎平凡，但它把"模型不懂规矩"这个问题，变成了"你有没有把规矩写下来"这个**你能掌控**的问题。

**道理二：一张纸条不够——大项目的规矩是分层的，所以发现规矩也得分层。**
但很快你会撞见一个真实结构：大仓库（monorepo）里规矩**不是一份**。仓库根有一套全局基调，可 `packages/web` 偏要用 2 空格、`packages/api` 用 4 空格、`services/legacy` 还守着自己的祖传规矩。一份"全局须知"根本表达不了这种"**整体一致、局部例外**"。
那怎么让 agent 既知道全局基调、又尊重你当前所在角落的特殊规矩？答案藏在一个很自然的类比里——**法律是分层的**：国家有宪法，省有省的条例，市有市的细则；你站在某个市里办事，这三层**同时**对你生效，而越具体的那层，越能在细节上覆盖上层。项目规矩也该这样组织。
于是 Codex 的做法是：**从你当前所在的目录（cwd）顺着目录树一路往上走**，直到撞见"项目根"的标记（默认是 `.git` 目录，它天然标出"仓库从这里开始"），把沿途每一层的 `AGENTS.md` 都收集起来。"往上走"这个动作本身就在回答"我属于哪些层"——就像你报地址会说"中国·浙江·杭州"，从具体到笼统，正是你归属的那条链。

**道理三：拼接时"根在前、叶在后"，是因为模型顺着读、后看到的能盖住先看到的。**
收集到一摞规矩，按什么顺序拼给模型？Codex 选**根在最前、你所在的目录在最后**。这不是随意定的，而是利用了模型读文本的一个特性：它是**顺序读**的，**后出现的指令能细化甚至推翻先出现的**。于是"仓库定基调（先读）、子包做例外（后读、能覆盖）"就自然成立了——这正好把上面"法律分层、下级覆盖上级"的直觉，翻译成了模型能照做的形状。

串起来：模型每次都"失忆"，所以把规矩写成文件喂给它（道理一）；大项目规矩分层，所以顺着目录树往上找、逐层收集（道理二）；按根→叶拼接，让具体的局部规矩能盖住笼统的全局规矩（道理三）。三步合起来，agent 就从"不懂规矩的新人"变成了"读过本项目须知、还分得清全局与局部的老手"。

## 问题

你让 agent 在一个大仓库里干活。这个仓库有自己的规矩：缩进用几个空格、提交信息用什么语言、测试怎么跑、哪些目录碰不得。但模型一无所知——它每次都从零开始猜，于是缩进风格忽 2 忽 4，提交信息一会儿中文一会儿英文。

更糟的是 **monorepo**：仓库根有一套全局约定，可 `packages/web` 用 2 空格、`packages/api` 用 4 空格，`services/legacy` 还有自己的祖传规矩。一份「全局指令」根本表达不了这种「整体一致、局部例外」的结构。

你需要的是：**让项目把规则写进文件，agent 自动发现并按层级叠加进上下文**——而且越靠近你当前工作目录的规则，越能在局部覆盖上层的约定。

## 解决方案

Codex 的答案是 `AGENTS.md`：一种放在目录里的纯文本指令文件。发现规则是**分层**的——

1. 从当前工作目录 `cwd` **向上走**，直到撞见「项目根标记」（默认是 `.git` 目录）；
2. 从项目根 **向下** 回到 `cwd`（含两端），逐层收集每个目录里的 `AGENTS.md`；
3. 按 **根 → cwd** 的顺序拼接（根在最前），中间用一个分隔符标出边界；
4. 把结果包成一个 `<user_instructions>` 块，注入进这一回合的 `instructions`（system）。

```
    /repo/.git              ← 项目根标记（停在这里，不再向上）
    /repo/AGENTS.md         ──┐  根：仓库级约定（最前）
    /repo/pkg/AGENTS.md     ──┤  中：包级约定
    /repo/pkg/sub/AGENTS.md ──┘  叶：你所在目录的约定（最后 → 可局部覆盖）
         ▲
         │  cwd = /repo/pkg/sub
         │
    发现顺序：根 → cwd            拼接后注入：
         │                       <user_instructions>
         ▼                         [repo 的规则]
    向上找根 → 向下收集            --- project-doc ---
                                   [pkg 的规则]
                                   --- project-doc ---
                                   [sub 的规则]
                                 </user_instructions>
```

「根在前、叶在后」很关键：模型读指令是顺序读的，**后出现的可以细化甚至推翻先出现的**——这正好对应「仓库定基调、子包做例外」。

## 工作原理

看 [code.py](code.py)，三个函数串起整条路径：

**第 1 步** — `find_project_root(start)`：从 `start` 向上遍历每个祖先目录，命中任一根标记（默认 `.git`）就返回；走到顶都没有就返回 `None`（此时只看 `cwd` 自己，不向上越界）。

```python
def find_project_root(start: Path) -> Path | None:
    for ancestor in [start, *start.parents]:
        for marker in PROJECT_ROOT_MARKERS:   # 默认 [".git"]
            if (ancestor / marker).exists():
                return ancestor
    return None
```

**第 2 步** — `discover_agents_md(cwd)`：先算出 **根→cwd** 的目录序列（`search_dirs` 从 cwd 向上收集到 root 再 `reverse()`），逐目录找第一个命中的候选文件名（`AGENTS.override.md` 优先于 `AGENTS.md`）：

```python
for d in search_dirs(cwd):            # 顺序：根 → cwd
    for name in AGENTS_FILENAMES:     # ["AGENTS.override.md", "AGENTS.md"]
        if (d / name).is_file():
            found.append(d / name)
            break                     # 一个目录只取一个（override 优先）
```

**第 3 步** — `read_agents_md` 按预算（`PROJECT_DOC_MAX_BYTES`，默认 32 KiB）读取并用分隔符拼接；`build_user_instructions_block` 再包成 `<user_instructions>` 块，由 `run_turn` 拼进本回合的 system。注意 `run_turn` **每回合都用当前 cwd 重新发现**——项目指令是随目录变化的。

这忠实对应真源码 [`core/src/agents_md.rs`](../../codex/codex-rs/core/src/agents_md.rs)：`AgentsMdManager` 的 `agents_md_paths` 做「向上找根、向下收集」（`dir.ancestors()` + `dirs.reverse()`），`read_agents_md` 维护字节预算，分隔符常量是 `AGENTS_MD_SEPARATOR = "\n\n--- project-doc ---\n\n"`。最终它会变成一个 `<user_instructions>` 块（[`protocol/src/protocol.rs`](../../codex/codex-rs/protocol/src/protocol.rs) 的 `USER_INSTRUCTIONS_OPEN_TAG`）注入回合。

**走一遍** — 拿一棵真实的小目录树，看这三步把分散的规矩拼成一块注入文本。假设磁盘上长这样（仓库根有 `.git` 标记，根和子包各放了一个 `AGENTS.md`）：

```text
proj/.git/                ← 项目根标记
proj/AGENTS.md            ← 仓库级：用 4 空格缩进 / 提交信息用英文
proj/sub/AGENTS.md        ← 子包级：本目录改用 2 空格 / 跑 `pytest -q`
```

现在你站在最深的 `proj/sub` 里发起一个回合，cwd = `proj/sub`：

第 1 步，`find_project_root(proj/sub)` 从 `sub` 往上一层层试：`sub` 里没有 `.git`？再上一层——`proj` 里有 `.git`，停。**项目根 = `proj`**。

第 2 步，`discover_agents_md` 先把"从 cwd 向上到 root"这条链收集出来再反转，得到**根→cwd**的发现顺序：

```text
1. proj/AGENTS.md        （根，最前）
2. proj/sub/AGENTS.md    （叶，最后）
```

第 3 步，按这个顺序读取、用 `--- project-doc ---` 分隔符拼起来，再包进 `<user_instructions>` 标签——这就是真正塞进本回合 system 的那块文本：

```text
<user_instructions>
# 仓库级规则（根）
- 用 4 空格缩进
- 提交信息用英文

--- project-doc ---

# 子包级规则（sub）
- 本目录改用 2 空格缩进
- 跑 `pytest -q`
</user_instructions>
```

看这块文本的顺序就懂了那条"道理三"：根的"4 空格"在前，子包的"2 空格"在后。模型顺着读下来，读到子包那句时就用它**覆盖**了根的缩进约定——于是在 `sub` 里它写 2 空格，但"提交信息用英文"这条根没被覆盖的规矩，依旧生效。一块拼接文本，同时表达了"全局基调 + 局部例外"。

`--demo` 直接演示：自建 `tmp/proj/.git` + `tmp/proj/AGENTS.md` + `tmp/proj/sub/AGENTS.md`，从最深的 `sub` 发起发现，打印「根在前」的拼接结果（就是上面这块），跑完删除整棵临时树。

## 生产级：项目文档也会失控——字节预算 + 截断

AGENTS.md 是用户/项目能塞进每个回合 system 的文本。一个善意但失控的项目可能写出一个**几万行**的 AGENTS.md（或一串目录层层叠加），原样注入就会把上下文窗口吃光——还没开始干活，地方先没了。生产级做法是给注入设一个**字节预算**：真 Codex 用 `project_doc_max_bytes`（[`agents_md.rs:133`](../../codex/codex-rs/core/src/agents_md.rs)），沿目录树收集时累计字节，一旦超出剩余预算就把多出来的部分 `truncate` 掉（agents_md.rs:166-168）——宁可截断，也不让项目记忆挤掉真正的对话。

合并也有**明确的优先级**：从 cwd 逐级向上收集，越靠近文件的越具体、越该赢（呼应 [s16](../s16_config/) 的分层 last-wins，真源码同样用 `merge_toml_values`）。"逐级向上捡规矩"不是随便拼接，而是一条有方向的合并链。

> 一句话：任何"可由用户/项目注入进上下文"的东西（AGENTS.md、skills、工具输出）都得有**预算上限**——否则它们就是绕过压缩（[s07](../s07_context_compaction/)）的后门。

## 🆚 与 Claude Code 的不同

| | Claude Code | Codex |
|---|---|---|
| 文件名 | `CLAUDE.md` | `AGENTS.md`（跨工具/跨厂商标准） |
| 发现方式 | 也读项目级 memory，但更以单文件 + import 为中心 | **分层**：从 cwd 向上找根（`.git`），再根→cwd 逐层收集 |
| 叠加顺序 | 项目记忆 + 用户记忆合并 | **根→叶 拼接**，叶层可局部覆盖根层约定 |
| 注入形态 | 进系统提示/记忆 | 包成 `<user_instructions>` 块注入回合 `instructions` |
| 越界控制 | — | 不越过项目根；`project_doc_max_bytes`（默认 32 KiB）封顶 |

**为什么？** 两点取舍：

- **跨厂商标准**。`AGENTS.md` 是 Codex 牵头推动的一个**与具体工具无关**的约定——同一个文件可被多家 agent 工具读取，而不是绑死在某个 CLI 的私有文件名上（`CLAUDE.md` 之于 Claude Code）。这与全课主线一致：Codex 押注的是 headless / CI / 云端等「无人值守、多工具协作」的场景，那里需要一个中立的、谁都认的指令载体。
- **分层发现为 monorepo 而生**。「向上找根、向下逐层收集、叶层可覆盖」精确地表达了「仓库定基调、子包做例外」的层级结构。Claude Code 更偏单一交互式工作区，单文件 + import 的模型已经够用；Codex 要在大型 monorepo 的任意子目录里被 `codex exec` 拉起来跑，就必须让指令「随你站的位置」自动分层组装。

## 深入：教学版 vs 真 Codex 源码

<details>
<summary>一、向上找根：ancestors + project_root_markers</summary>

教学版 `find_project_root` 用 `[start, *start.parents]` 遍历，命中 `.git` 即停。真源码 `agents_md.rs` 的 `agents_md_paths` 走 `dir.ancestors()`，逐个祖先 `ancestor.join(marker)` 调 `fs.get_metadata` 判断是否存在；标记列表来自配置：

| 行为 | 真源码 |
|---|---|
| 默认标记 | `default_project_root_markers()` → `DEFAULT_PROJECT_ROOT_MARKERS = &[".git"]`（`config/src/project_root_markers.rs`） |
| 配置覆盖 | `project_root_markers_from_config` 读 `config.toml` 的 `project_root_markers` 数组 |
| **空数组的语义** | `Ok(Some(Vec::new()))` —— **禁用向上遍历**，只看 cwd 自己 |
| 找不到根 | `search_dirs` 退化为 `vec![dir]`，绝不越界向上 |

注意真源码还合并了 config layer stack（跳过 `Project` 层），教学版省掉了多层配置合并这一步——它对「发现路径」本身没有影响，只决定标记来自哪一层。

```rust
// agents_md.rs：找到根后，从 cwd 向上收集到 root，再反转成 根→cwd
let mut cursor = dir.clone();
loop { dirs.push(cursor.clone()); if cursor == root { break; } cursor = parent; }
dirs.reverse();   // 根在最前
```

</details>

<details>
<summary>二、候选文件名与 AGENTS.override.md 兜底</summary>

教学版 `AGENTS_FILENAMES = ["AGENTS.override.md", "AGENTS.md"]`，一个目录命中一个即停。真源码 `candidate_filenames()` 顺序完全一致，且额外支持用户配置的 `project_doc_fallback_filenames` 追加在后面：

```rust
names.push(LOCAL_AGENTS_MD_FILENAME);    // "AGENTS.override.md"  —— 优先
names.push(DEFAULT_AGENTS_MD_FILENAME);  // "AGENTS.md"
for candidate in &self.config.project_doc_fallback_filenames { ... }  // 再追加自定义兜底
```

`AGENTS.override.md` 的用途：让你在**不改动**团队共享的 `AGENTS.md`（通常已提交进 git）的前提下，放一个**本地、通常 .gitignore 掉**的覆盖文件。真源码还有一条独立路径 `load_global_instructions`，从 `~/.codex/`（CODEX_HOME）读全局 `AGENTS.override.md` / `AGENTS.md`，作为 `User` 级指令——它和项目级是两个 provenance，拼接时只在 user/internal → project 的过渡处插 `--- project-doc ---` 分隔符。教学版把这套 provenance 体系简化成了「统一用分隔符分隔每个 project 文档」。

</details>

<details>
<summary>三、字节预算 project_doc_max_bytes 与截断</summary>

教学版 `read_agents_md` 维护 `remaining` 字节预算，超出就 `data[:remaining]` 截断并停止。真源码逻辑一致，默认值 `DEFAULT_PROJECT_DOC_MAX_BYTES = 32 * 1024`（`config/src/config_toml.rs`）：

| 细节 | 真源码 |
|---|---|
| 预算为 0 | `read_agents_md` 直接 `Ok(None)`——彻底关闭 AGENTS.md |
| 逐文件扣减 | `remaining = remaining.saturating_sub(data.len())` |
| 超额截断 | `data.truncate(remaining)` 并 `tracing::warn!` 一条「truncating」日志 |
| 非法 UTF-8 | `warn_invalid_utf8` 推一条 startup warning，再 `from_utf8_lossy` 替换坏字节 |
| 非文件项 | `get_metadata` 判 `is_file`，目录/符号链接异常会被跳过 |

因为是**根→cwd 顺序**消费预算，越靠近 cwd（越深、越具体）的文件越可能在预算用尽时被裁——这是一个有意的取舍：宁可保住上层的全局约定。

</details>

<details>
<summary>四、它如何变成 user_instructions 块注入回合</summary>

教学版用一层 `<user_instructions> ... </user_instructions>` 表达「这是注入的指令块」。真源码的包裹分两层：

- 内层：`core/src/context/user_instructions.rs` 的 `UserInstructions` fragment，`body()` 产出 `"{directory}\n\n<INSTRUCTIONS>\n{text}\n"`，`type_markers()` 是 `("# AGENTS.md instructions for ", "</INSTRUCTIONS>")`——也就是说真实注入里**带上了 cwd 路径**，告诉模型「这些规则对应哪个目录」。
- 外层：`protocol/src/protocol.rs` 的 `USER_INSTRUCTIONS_OPEN_TAG` / `USER_INSTRUCTIONS_CLOSE_TAG`（`<user_instructions>` / `</user_instructions>`）。

注入时机在回合构建里（`session/turn.rs` 一带），与 `<environment_context>` 等其它上下文片段一起，作为一条 `user` 角色的消息排进 prompt。还有一个 `Feature::ChildAgentsMd` 开关，开启时会额外追加一段 `HIERARCHICAL_AGENTS_MESSAGE` 的内部指引，告诉模型「你会看到分层的 AGENTS.md」。

一句话：教学版 ~80 行的「向上找根 + 向下收集 + 拼接 + 包标签」，就是 `agents_md.rs`（433 行）+ provenance 体系 + 配置层合并 + 全局/项目双来源的核心；其余都是预算、UTF-8、多来源 provenance 这些生产级护栏。

</details>

## 运行

```bash
python s06_agents_md/code.py --demo   # 自建临时目录树演示分层发现，跑完自动清理（mock，无需 key）
python s06_agents_md/code.py          # 交互模式：输入一个目录看从那里发现的 AGENTS.md
```

`--demo` 全程离线、不调用模型，结束时会删除它创建的临时目录树。

## 小结

- `AGENTS.md` = 写进目录里的项目规则，**分层发现**：从 cwd 向上找根（默认 `.git`），再根→cwd 逐层收集。
- 拼接顺序「根在前、叶在后」，让子包能在局部覆盖仓库级约定——天然契合 monorepo。
- 用 `project_doc_max_bytes`（默认 32 KiB）封顶，`AGENTS.override.md` 提供不动共享文件的本地覆盖。
- 最终包成 `<user_instructions>` 块注入回合 `instructions`，与 Claude Code 的 `CLAUDE.md` 同类但走的是跨厂商标准 + 分层发现。
- **生产级**：AGENTS.md 注入有**字节预算**（`project_doc_max_bytes`），超了就截断——别让失控的项目文档挤掉真正的对话；合并按目录层级有明确优先级（见「生产级」一节）。
- 下一站 [s07](../s07_context_compaction/)：对话变长后，怎么把旧回合压成摘要、腾出上下文。

## 思考

- 「根在前、叶在后、后者可覆盖前者」依赖模型**忠实地按顺序读、并让后文压过前文**。如果模型并不总是这么做（比如它更信任最先看到的全局规则），分层覆盖还成立吗？你会怎么在 `AGENTS.md` 的措辞上下功夫，让「覆盖」更稳？
- 项目根标记默认是 `.git`。在一个没有 `.git` 的目录（比如解压出来的 tarball、临时沙箱）里跑 `codex exec`，发现会退化成「只看 cwd」。这对「无人值守自动化」是好事还是隐患？你会把标记设成什么？
- 把 `AGENTS.md` 注入进**每一回合**意味着它持续占用上下文预算；而 s07 又要为腾地方压缩历史。当 32 KiB 的项目指令和被压缩的对话历史抢同一个窗口时，谁更该被保住？Codex 选择保住上层全局规则，你会怎么权衡？
- `AGENTS.md` 是给「模型」读的、放在仓库里、还可能被多家工具消费。它和给「人」读的 `README.md`、`CONTRIBUTING.md` 会不会逐渐重合甚至冲突？如果同一条规矩要同时讲给人和模型听，你愿意维护一份还是两份？
