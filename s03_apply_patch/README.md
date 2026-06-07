# s03: apply_patch — Codex 怎么改文件

> 🌐 [English](README.en.md) · **中文**

> *"把'改动'表达成一个结构化补丁信封，一次调用增/改/删/移多个文件。"*

[learn-codex 总览](../README.md) · [工具与分发](../s02_tool_use/) → **apply_patch** → [审批策略](../s04_approval/)

---

## 先把思想说透：为什么"改文件"要做成一封补丁信封

s02 已经给了模型一个 `write_file`，它能写任意文件——那"改代码"这件事看起来不就解决了吗？让模型把改好的整个文件重写一遍不就行了？真要这么做，你很快会撞上几堵墙。想通 Codex 为什么绕开这条路、走"补丁信封"，靠下面三个递进的道理。

**道理一：直接覆盖整个文件，是在用大炮打蚊子，而且会误伤。**
你只想把一个 800 行文件里的某一行 `timeout=30` 改成 `timeout=60`。如果走 `write_file`，模型得把这 800 行**一字不差地重新吐一遍**，只为改动其中一行。这有三重浪费和风险：吐 800 行又慢又费 token；模型在重抄那 799 行无关代码时，很可能手一抖改坏一处你没让它动的地方；而且事后你想知道"它到底改了啥"，得拿新旧两版整文件去 diff 才看得出来。**改动越小，整文件覆盖的浪费和风险越离谱。** 我们真正想要的，是只描述"变化的那一点点"，而不是把没变的也搬一遍。

**道理二：那就只描述"变化"——而最自然的"变化描述"，是一封能装下多处改动的信封。**
只说变化，办法之一是像 Claude Code 那样逐处替换（"把这串旧的换成这串新的"）。但真实的改代码常常是**一组相互关联的改动**：加一个新文件、改两个老文件、顺手删掉一个废弃文件——它们其实是"同一件事"。如果拆成五次、十次独立的工具调用，这件事就被打散了，没有一个东西能完整地代表"这次改动"。

Codex 的选择是把整组改动**装进一封信**：信封里逐条写"给这个文件加这些行""把那个文件的这一段换成那样""删掉这个文件"。一次调用，一封信，就是一个**完整、自描述的改动集**。这封信本身长得就像一份 `git diff`——于是好处自动涌出来：它能整封拿给用户看"你要批准的就是这些"（审批，见 [s04](../s04_approval/)）、能整封存档备查（rollout，见 [s08](../s08_rollout/)）、出问题能整封回滚。**把"一次改动"做成一个可以整体传递的东西，远比散落的十次调用值钱。**

**道理三（最妙）：信封里靠"内容"而不是"行号"定位，是为了迁就一个会数错数的模型。**
信封里要说清"改的是文件的哪一段"。最容易想到的是报行号——"把第 42 行换掉"。但这对 LLM 是个陷阱：**模型其实不会可靠地数行**。它能读懂代码、能想出怎么改，可你让它精确数到"这是第几行"，它经常差一两行；文件前面但凡有人加删过几行，行号还会整体漂移。把定位押在行号上，等于把成败押在模型最不擅长的能力上。

Codex 反其道而行：定位不靠行号，而靠**贴出那段代码周围几行原文**当"路标"——"在`长这样``长那样`这几行之间，把中间这行改掉"。为什么这聪明？因为**照抄一小段原文，恰恰是模型最擅长的事**（它刚刚才读过这个文件）。于是即便它从头到尾一个行号都没数对，应用补丁的程序仍能拿着这几行路标，在文件里**按内容找到**那个位置，精准下刀。

一个比方：让一个粗心的朋友帮你在书里夹书签。你说"夹在第 213 页"——他多半翻错。但你说"夹在那句『从前有座山』和『山里有座庙』中间"——他一找一个准。**行号是会骗人的页码，上下文是认得出的句子。** apply_patch 赌的就是：模型抄句子比数页码靠谱得多。这一章，我们就把这封信封的"解析"和"按句子定位地应用"亲手实现一遍。

## 问题

模型要修改代码。怎么把"改动"这件事表达出来？

- 用 `bash` + `sed`/`cat > file`？脆弱、易错、难审查。
- 像 Claude Code 那样逐次 `edit(old_string → new_string)`？精确，但要改 5 个文件、每个文件 3 处时，就是 15 次工具调用。

Codex 选了第三条路：**一个补丁信封**。

## 解决方案

`apply_patch` 工具吃一段带标记的补丁文本，一次调用就能对多个文件做新增 / 更新 / 删除 / 重命名：

```
*** Begin Patch
*** Add File: src/hello.py
+print("hi")
*** Update File: README.md
*** Move to: docs/README.md
@@
 # 项目
-旧的一行
+新的一行
*** Delete File: legacy.txt
*** End Patch
```

这个格式有正式的 Lark 文法，定义在真源码 `codex-rs/apply-patch/src/parser.rs:7-22`。标记一览：

| 标记 | 含义 |
|---|---|
| `*** Add File: <path>` | 新建文件，后续每行以 `+` 开头 |
| `*** Update File: <path>` | 修改文件，后接可选 `*** Move to:` 和若干 `@@` 块 |
| `*** Delete File: <path>` | 删除文件 |
| `*** Move to: <path>` | （在 Update 内）重命名 |
| `@@ [上下文]` | 定位锚点；其后 ` `=上下文、`-`=删除、`+`=新增 |

## 工作原理

看 [code.py](code.py)，分两段：**解析** → **应用**。

**解析** `parse_patch(text)`：逐行扫描，遇到 `*** Add/Update/Delete File:` 就开一个 hunk。Update 的正文按 `@@` 切成若干 chunk，每行第一个字符是 tag（` `/`+`/`-`），其余是内容。

**应用** `apply_hunk(h)` 的关键是 Update 的「上下文锚定」——它不靠行号，而是靠内容定位：

```python
old = [ln for tag, ln in chunk if tag in (" ", "-")]   # 文件里现有的样子（上下文+被删）
new = [ln for tag, ln in chunk if tag in (" ", "+")]   # 替换后的样子（上下文+新增）
idx = _find_block(file_lines, old)                     # 按内容找到这一段
file_lines[idx:idx + len(old)] = new                   # 整段替换
```

因为靠上下文而非行号定位，模型即使数错行号，补丁照样能贴上去——这对 LLM 很关键。（教学版 `_find_block` 现在也做 `seek_sequence` 式的**三级降级匹配**：精确 → 忽略行尾空白 → 忽略首尾空白，所以行号漂移、缩进/行尾的小出入都能容忍；详见下方「生产级」一节。真 Codex 在此之上还有 `eof` 锚定与 git-apply 式的字节级宽松。）

**走一遍** — 用 `python s03_apply_patch/code.py --demo` 真正跑的那个例子，看"按内容定位"是怎么发生的。先有一个文件 `poem.txt`（第一步 Add 出来的）：

```
roses are red
violets are blue
codex writes patches
and so can you
```

现在模型想把第 2 行 `violets are blue` 改成 `violets are violet`。注意它**没报任何行号**——它发来的补丁信封是这样（这正是 demo 里那段 Update 补丁）：

```
*** Begin Patch
*** Update File: _demo_workspace/poem.txt
@@
 roses are red
-violets are blue
+violets are violet
 codex writes patches
*** End Patch
```

读这段的窍门全在**每行的第一个字符**：开头一个**空格**表示"这行是路标，原样保留"；`-` 表示"删掉这行"；`+` 表示"加上这行"。于是程序把它拆成两份名单——

1. **"文件现在应该长这样"**（路标 + 被删的行，即空格行和 `-` 行，去掉首字符）：
   ```python
   old = ["roses are red", "violets are blue", "codex writes patches"]
   ```
2. **"替换后应该长这样"**（路标 + 新增的行，即空格行和 `+` 行）：
   ```python
   new = ["roses are red", "violets are violet", "codex writes patches"]
   ```

接着是最关键的一步：`_find_block(file_lines, old)` 拿着 `old` 这串"路标 + 旧行"，**在文件里逐位置比对、找出它出现在哪**——这里匹配到第 0 行起的那三行。找到了，就把文件里那一段**整段换成 `new`**：

```python
idx = _find_block(file_lines, old)        # → 0（按内容找到，全程没用行号）
file_lines[idx:idx + len(old)] = new      # 第 0~2 行整段替换为 new
```

看出妙处了吗？模型只是**照抄了 `roses are red` 和 `codex writes patches` 这两句它刚读过的原文**当路标，夹着要改的那行——它完全没去数"这是第几行"。哪怕这首诗前面被人加过 10 行、真实行号早就漂移，`_find_block` 照样能凭这两句路标找到位置。这就是上面"道理三"在代码里的样子：**赌模型抄句子，而不赌它数页码。** （demo 里还跟了第二个 chunk，在末尾那句 `and so can you` 后面加一行——同理，靠 `and so can you` 这句路标定位。跑一下 `--demo`，前后文件内容都会打印出来。）

## 生产级：补丁贴不上、抄歪了、半途失败——怎么不崩、不留半成品

apply_patch 的真正难处不在"格式对了怎么应用"，而在"**模型把补丁写歪了怎么办**"——它会把上下文缩进抄错、会贴一段文件里根本没有的上下文、一封补丁里某个 hunk 失败而前面的已经写盘了。三件事，生产级各有答案（[code.py](code.py) 都实现了）。

### 一、模糊匹配：容忍模型抄歪空白（seek_sequence 三级降级）

模型照抄上下文当路标，却常把行尾空格、缩进抄得不完全一致。若要求**逐字精确**，补丁就白白贴不上。真 Codex 的 [`seek_sequence.rs`](../../codex/codex-rs/apply-patch/src/seek_sequence.rs) 用**三级降级匹配**（strictness 递减）：① 精确相等 → 找不到再 ② 忽略**行尾**空白（`rstrip`）→ 再找不到 ③ 忽略**首尾**空白（`strip`）；命中越靠前越严。本章已把 `_find_block` 升级成同样的三级。`--demo` 段 ③ 故意把上下文行尾抄出多余空格——照样贴上：

```
③ 生产级·模糊匹配：故意把上下文行尾抄出多余空格，照样贴上：
应用成功:
M _demo_workspace/poem.txt
   第 2 行 → violets are PURPLE
```

（真 Codex 还更进一步：`eof` 锚定、git-apply 式的字节级宽松——但"逐级放宽"是同一个思想。）

### 二、原子性：要么全贴上，要么一个字节都不写

一封补丁可能改五个文件。若第三个 hunk 上下文找不到、而前两个已写盘，你就得到一个**半成品**——比彻底失败更糟（呼应思考 2）。生产级做法是**两阶段提交**：先在内存副本上**模拟**所有改动（"准备"），全过了再**落盘**（"提交"）；任一步失败，磁盘原封不动。本章 `apply_patch_tool` 就先跑一遍 dry-run 预检再写。

### 三、出错回灌：把失败还给模型，让它自己改

定位失败不该让进程崩溃——而该把一句**人话错误**作为工具结果回灌给模型（正是 [s02 生产级](../s02_tool_use/) 那条 `RespondToModel` 原则；真 Codex 的 `ApplyPatchError` 也走这条路）。`--demo` 段 ④ 喂一个贴不上的补丁：

```
④ 生产级·原子性 + 错误回灌：补丁含一个根本不存在的上下文：
   apply_patch 失败（整封未应用，磁盘未改动）：在 .../poem.txt 找不到上下文 ['THIS LINE DOES NOT EXIST', 'x']…；请照抄文件里那几行原文当路标再试。
   文件未被破坏，第 2 行仍是： violets are PURPLE
```

错误串特意点明"整封未应用、磁盘未改动"并给出**下一步怎么做**——模型读到就能重抄路标再试，而不是对着 traceback 干瞪眼。

> 一句话：补丁工具的生产级，是三件事——**抄歪了能容忍（模糊匹配）、失败了不留半成品（原子）、错了能自我修正（回灌）**。

## 🆚 与 Claude Code 的不同

| | Claude Code | Codex |
|---|---|---|
| 编辑原语 | `Edit`(精确 old→new 串替换)、`Write`、`MultiEdit` | 单一 `apply_patch` 补丁信封 |
| 一次调用范围 | 一处（MultiEdit 一个文件多处） | 跨多文件 增/改/删/移 一次搞定 |
| 形态 | 字符串替换 | 类 unified-diff，天生可审查/可回滚 |
| 定位 | 要求 old_string 全局唯一 | `@@` 上下文锚定，容忍行号漂移 |

**为什么不同？三个原因：**

1. **工具要贴合模型被训练出来的输出习惯。** OpenAI 把 `apply_patch` 这个格式直接训进了 Codex 模型；Anthropic 把精确串替换训进了 Claude。工具设计跟着"模型最擅长吐什么"走，而不是反过来。
2. **批量 + 原子 + 可审查。** 一个补丁就是一次完整的、跨文件的改动集，本身就是一份 diff——天然适合展示给用户审批（[s04]）、记录进 rollout（[s08]）、整体回滚。
3. **对 LLM 鲁棒。** 上下文锚定比"行号"或"要求字符串全局唯一"更能容忍模型的小误差。

**代价**：解析器更复杂（[parser.rs](../../codex/codex-rs/apply-patch/src/parser.rs) 有 954 行），且对上下文是否匹配敏感。Claude 的串替换则更简单直接。没有银弹，只有取舍。

[s04]: ../s04_approval/
[s08]: ../s08_rollout/

## 深入：教学版 vs 真 Codex 源码

真 Codex 的 apply_patch 是独立 crate [`apply-patch/`](../../codex/codex-rs/apply-patch/)：`parser.rs`(954 行) + `lib.rs`(1689 行) + `streaming_parser.rs` + `seek_sequence.rs`。教学版只取了它的骨架。

<details>
<summary>一、严格解析 vs 宽松解析</summary>

`parser.rs` 有 `ParseMode::Strict` 和 `ParseMode::Lenient`。因为某些模型（如 gpt-4.1）吐出的补丁格式不总是严丝合缝，Codex 默认走**宽松**模式（`PARSE_IN_STRICT_MODE = false`），容忍标记前后的空白等。教学版只实现了"宽松"的一种近似。

</details>

<details>
<summary>二、流式解析（边生成边解析）</summary>

`streaming_parser.rs` 能在补丁还在被模型生成时就**增量解析**，配合流式 UI 实时显示 diff。教学版则等整段补丁到齐才一次性解析。

</details>

<details>
<summary>三、模糊上下文匹配 seek_sequence.rs</summary>

本章已把 `_find_block` 升级成 `seek_sequence.rs` 同款的**三级降级匹配**（精确 → 忽略行尾 → 忽略首尾空白，见「生产级」一节）。真 Codex 在此之上还做 `eof` 锚定（优先从文件尾匹配）和更接近 `git apply` 的字节级宽松规整。核心思想一致：**逐级放宽，迁就会犯小错的 LLM**。

</details>

<details>
<summary>四、沙箱感知的文件写入 + 边界情况</summary>

`lib.rs` 通过 `ExecutorFileSystem` 写文件，受沙箱（s05）与审批（s04）约束；还处理 `*** Move to:` 重命名、二进制文件保护、符号链接、`*** End of File` 等教学版略过的边界。

</details>

## 运行

```bash
python s03_apply_patch/code.py --demo   # 不需要模型：演示 Add + Update
python s03_apply_patch/code.py          # 交互模式（mock 会发一个 apply_patch 调用）
```

`--demo` 会在 `_demo_workspace/` 里建一个文件再改它，打印前后内容；跑完会**自动清理**该目录（和其余各章一致）。

## 小结

- Codex 用一个结构化补丁信封表达所有文件改动，而非逐次串替换。
- 上下文锚定让补丁对模型误差鲁棒。
- 这是「工具设计跟随模型训练」的典型例子。
- **生产级**：模糊匹配（seek_sequence 三级降级）容忍抄歪的空白、两阶段提交保证原子性（失败不留半成品）、定位失败把错误回灌给模型自我修正（见「生产级」一节）。
- 下一站 [s04](../s04_approval/)：补丁也好、命令也罢，落盘之前要不要先问用户一句"批准吗？"——这就是审批策略。

## 思考

<div class="think">

1. 模型把 `@@` 上下文行的缩进数错了一个空格，精确匹配就会失败——真 Codex 用模糊匹配兜底。你觉得"模糊"到什么程度算安全？过度模糊会带来什么风险？
2. apply_patch 把多文件改动打包成一次调用。可如果补丁的第三个 hunk 应用失败，前两个已经写盘了——该回滚吗？要做到原子性，你会怎么设计？
3. 为什么 OpenAI 宁可训练模型输出这种**自定义**补丁格式，也不直接用标准 unified diff（`git diff`）？标准格式差在哪？
4. 如果让 Claude（被训练成擅长精确串替换）去用 apply_patch、或让 Codex 去用 `Edit(old→new)`，会发生什么？这说明工具和模型之间是什么关系？

</div>
