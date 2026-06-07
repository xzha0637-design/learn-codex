# s03: apply_patch — How Codex Edits Files

> 🌐 **English** · [中文版](README.md)

> *"Express an 'edit' as a structured patch envelope, and add / change / delete / move multiple files in a single call."*

[learn-codex overview](../README.en.md) · [Tools and dispatch](../s02_tool_use/README.en.md) → **apply_patch** → [Approval policy](../s04_approval/README.en.md)

---

## Get the idea straight first: why "editing a file" is built as a patch envelope

s02 already gave the model a `write_file`, and it can write any file — so doesn't that already solve "editing code"? Just let the model rewrite the whole edited file, right? If you actually do that, you'll quickly run into several walls. To understand why Codex steers around this road and goes with the "patch envelope," lean on the following three escalating reasons.

**Reason one: overwriting the whole file is using a cannon to swat a mosquito — and it causes collateral damage.**
You only want to change one line in an 800-line file from `timeout=30` to `timeout=60`. If you go through `write_file`, the model has to **spit all 800 lines back out verbatim**, just to change one of them. There's a triple waste and risk here: spitting out 800 lines is slow and burns tokens; while re-copying those 799 lines of unrelated code, the model is very likely to slip and break something you never asked it to touch; and afterward, if you want to know "what exactly did it change," you have to diff the old and new whole files to see it. **The smaller the change, the more absurd the waste and risk of overwriting the whole file.** What we actually want is to describe only "the tiny bit that changed," not to haul over the unchanged parts too.

**Reason two: so just describe the "change" — and the most natural "change description" is an envelope that can hold multiple edits.**
One way to describe only the change is to do per-spot replacement the way Claude Code does ("swap this old string for this new string"). But real code edits are often **a set of interrelated changes**: add a new file, change two old files, and delete an obsolete file along the way — they're really "the same thing." If you split them into five or ten independent tool calls, that one thing gets scattered, and nothing fully represents "this edit."

Codex's choice is to **stuff the whole set of changes into one letter**: inside the envelope, each line spells out "add these lines to this file," "replace this section of that file with that," "delete this file." One call, one letter, is one **complete, self-describing change set**. The letter itself looks just like a `git diff` — and so the benefits pour out automatically: the whole letter can be shown to the user as "this is what you're approving" (approval, see [s04](../s04_approval/README.en.md)), the whole letter can be archived for the record (rollout, see [s08](../s08_rollout/README.en.md)), and the whole letter can be rolled back if something goes wrong. **Making "one edit" into something that can be passed around as a whole is far more valuable than ten scattered calls.**

**Reason three (the cleverest): inside the envelope, locating by "content" rather than "line number" is to accommodate a model that miscounts.**
The envelope needs to make clear "which section of the file is being changed." The most obvious idea is to report line numbers — "replace line 42." But this is a trap for an LLM: **the model can't reliably count lines.** It can read code, it can figure out how to change it, but if you ask it to count precisely to "which line is this," it's often off by one or two; and if anyone added or removed a few lines earlier in the file, the line numbers all shift. Pinning the location on line numbers is pinning success or failure on the model's weakest ability.

Codex does the opposite: it locates not by line number but by **quoting a few lines of the original text around that section** as "landmarks" — "between the lines that `look like this` and `look like that`, change the line in the middle." Why is this clever? Because **copying a small snippet of the original text is exactly what the model is best at** (it just read this file). So even if it never got a single line number right from start to finish, the patch-applying program can still take these few landmark lines and **locate that position by content** in the file, cutting precisely.

An analogy: ask a careless friend to put a bookmark in a book for you. You say "put it on page 213" — he'll probably flip to the wrong page. But you say "put it between the sentence 'Once upon a time there was a mountain' and 'in the mountain there was a temple'" — he'll find it spot-on every time. **Line numbers are deceptive page numbers; context is a recognizable sentence.** What apply_patch bets on is exactly this: the model copies sentences far more reliably than it counts pages. In this chapter, we'll hand-implement both the "parsing" of this envelope and the "content-based application" ourselves.

## Problem

The model needs to edit code. How do you express "an edit"?

- Use `bash` + `sed`/`cat > file`? Fragile, error-prone, hard to review.
- Do per-spot `edit(old_string → new_string)` like Claude Code? Precise, but when you need to change 5 files with 3 spots each, that's 15 tool calls.

Codex picked a third road: **a patch envelope.**

## Solution

The `apply_patch` tool takes a chunk of marked-up patch text and, in a single call, can add / update / delete / rename multiple files at once:

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

This format has a formal Lark grammar, defined in the real source at `codex-rs/apply-patch/src/parser.rs:7-22`. Here's the marker overview:

| Marker | Meaning |
|---|---|
| `*** Add File: <path>` | Create a new file; every following line starts with `+` |
| `*** Update File: <path>` | Modify a file; followed by an optional `*** Move to:` and several `@@` blocks |
| `*** Delete File: <path>` | Delete a file |
| `*** Move to: <path>` | (inside Update) rename |
| `@@ [context]` | Locating anchor; afterward ` `=context, `-`=delete, `+`=add |

## How it works

Look at [code.py](code.py), in two parts: **parse** → **apply**.

**Parse** `parse_patch(text)`: scan line by line; when it hits `*** Add/Update/Delete File:`, open a hunk. The body of an Update is split by `@@` into several chunks, where the first character of each line is the tag (` `/`+`/`-`) and the rest is the content.

**Apply** `apply_hunk(h)`'s key is the Update's "context anchoring" — it locates not by line number but by content:

```python
old = [ln for tag, ln in chunk if tag in (" ", "-")]   # 文件里现有的样子（上下文+被删）
new = [ln for tag, ln in chunk if tag in (" ", "+")]   # 替换后的样子（上下文+新增）
idx = _find_block(file_lines, old)                     # 按内容找到这一段
file_lines[idx:idx + len(old)] = new                   # 整段替换
```

Because it locates by context rather than line number, the patch still applies even if the model miscounts line numbers — and this is crucial for an LLM. (The teaching version's `_find_block` now also does `seek_sequence`-style **three-tier fallback matching**: exact → ignore trailing whitespace → ignore leading-and-trailing whitespace, so line-number drift and small discrepancies in indentation / line endings can all be tolerated; see the "Production-grade" section below. The real Codex adds `eof` anchoring and git-apply-style byte-level leniency on top of this.)

**Walk through it** — using the very example that runs for real with `python s03_apply_patch/code.py --demo`, watch how "locating by content" happens. First there's a file `poem.txt` (Added in the first step):

```
roses are red
violets are blue
codex writes patches
and so can you
```

Now the model wants to change line 2, `violets are blue`, to `violets are violet`. Note that it **reports no line numbers at all** — the patch envelope it sends looks like this (this is exactly that Update patch in the demo):

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

The trick to reading this is all in **the first character of each line**: a leading **space** means "this line is a landmark, kept as-is"; `-` means "delete this line"; `+` means "add this line." So the program splits it into two lists —

1. **"What the file should currently look like"** (landmarks + deleted lines, i.e. the space lines and `-` lines, with the first character stripped):
   ```python
   old = ["roses are red", "violets are blue", "codex writes patches"]
   ```
2. **"What it should look like after replacement"** (landmarks + added lines, i.e. the space lines and `+` lines):
   ```python
   new = ["roses are red", "violets are violet", "codex writes patches"]
   ```

Then comes the most crucial step: `_find_block(file_lines, old)` takes this `old` string of "landmarks + old lines" and **compares it position by position in the file to find where it appears** — here it matches the three lines starting at line 0. Once found, it **replaces that whole section in the file with `new`**:

```python
idx = _find_block(file_lines, old)        # → 0（按内容找到，全程没用行号）
file_lines[idx:idx + len(old)] = new      # 第 0~2 行整段替换为 new
```

See the cleverness? The model merely **copied the two sentences `roses are red` and `codex writes patches` that it just read** as landmarks, sandwiching the line to be changed — it never counted "which line is this." Even if 10 lines had been added before this poem and the real line numbers had long since drifted, `_find_block` could still find the position by these two landmark sentences. This is what "Reason three" above looks like in code: **bet on the model copying sentences, not on it counting pages.** (The demo also follows with a second chunk, adding a line after the last sentence `and so can you` — same idea, located by the `and so can you` landmark. Run `--demo` and the file content before and after will both be printed.)

## Production-grade: the patch won't apply, was copied wrong, fails midway — how to not crash and not leave a half-finished mess

The real difficulty of apply_patch isn't "how to apply once the format is right," but "**what to do when the model writes the patch wrong**" — it'll copy the context indentation incorrectly, it'll paste a context that doesn't exist in the file at all, and one hunk in a patch fails after earlier ones have already been written to disk. Three things, and production-grade has an answer for each ([code.py](code.py) implements them all).

### 1. Fuzzy matching: tolerate the model copying whitespace wrong (seek_sequence three-tier fallback)

The model copies context as landmarks, but often copies trailing spaces and indentation not quite identically. If you require a **character-exact** match, the patch fails to apply for nothing. The real Codex's [`seek_sequence.rs`](../../codex/codex-rs/apply-patch/src/seek_sequence.rs) uses **three-tier fallback matching** (decreasing strictness): ① exact equality → if not found, ② ignore **trailing** whitespace (`rstrip`) → if still not found, ③ ignore **leading-and-trailing** whitespace (`strip`); the earlier the hit, the stricter. This chapter has upgraded `_find_block` to the same three tiers. The `--demo` segment ③ deliberately copies the context line with extra trailing spaces — and it applies anyway:

```
③ 生产级·模糊匹配：故意把上下文行尾抄出多余空格，照样贴上：
应用成功:
M _demo_workspace/poem.txt
   第 2 行 → violets are PURPLE
```

(The real Codex goes even further: `eof` anchoring, git-apply-style byte-level leniency — but "loosen tier by tier" is the same idea.)

### 2. Atomicity: either it all applies, or not a single byte is written

One patch may change five files. If the third hunk's context can't be found while the first two have already been written to disk, you get a **half-finished mess** — worse than a clean failure (echoing Think it over 2). The production-grade approach is **two-phase commit**: first **simulate** all the changes on an in-memory copy ("prepare"), and only after everything passes do you **write to disk** ("commit"); if any step fails, the disk is left untouched. This chapter's `apply_patch_tool` runs a dry-run pre-check before writing.

### 3. Error feedback: hand the failure back to the model and let it fix it itself

A locating failure shouldn't crash the process — instead it should feed **a plain-language error** back to the model as the tool result (exactly the `RespondToModel` principle from [s02 production-grade](../s02_tool_use/README.en.md); the real Codex's `ApplyPatchError` goes this way too). The `--demo` segment ④ feeds a patch that won't apply:

```
④ 生产级·原子性 + 错误回灌：补丁含一个根本不存在的上下文：
   apply_patch 失败（整封未应用，磁盘未改动）：在 .../poem.txt 找不到上下文 ['THIS LINE DOES NOT EXIST', 'x']…；请照抄文件里那几行原文当路标再试。
   文件未被破坏，第 2 行仍是： violets are PURPLE
```

The error string deliberately points out "the whole patch was not applied, the disk was not changed" and gives **what to do next** — so the model reads it and can re-copy the landmarks and retry, instead of staring blankly at a traceback.

> In one sentence: the production-grade of a patch tool is three things — **tolerate wrong copying (fuzzy matching), leave no half-finished mess on failure (atomic), self-correct on error (feedback).**

## 🆚 How it differs from Claude Code

| | Claude Code | Codex |
|---|---|---|
| Edit primitive | `Edit` (precise old→new string replacement), `Write`, `MultiEdit` | A single `apply_patch` patch envelope |
| Scope per call | One spot (MultiEdit: multiple spots in one file) | Add / change / delete / move across multiple files in one go |
| Shape | String replacement | Unified-diff-like, inherently reviewable / rollbackable |
| Locating | Requires old_string to be globally unique | `@@` context anchoring, tolerates line-number drift |

**Why the difference? Three reasons:**

1. **The tool has to fit the output habits the model was trained on.** OpenAI trained the `apply_patch` format directly into the Codex model; Anthropic trained precise string replacement into Claude. Tool design follows "what the model is best at spitting out," not the other way around.
2. **Batch + atomic + reviewable.** One patch is one complete, cross-file change set, and is itself a diff — naturally suited to being shown to the user for approval ([s04]), recorded into a rollout ([s08]), and rolled back as a whole.
3. **Robust for an LLM.** Context anchoring tolerates the model's small errors better than "line numbers" or "requiring a globally unique string."

**The cost**: the parser is more complex ([parser.rs](../../codex/codex-rs/apply-patch/src/parser.rs) has 954 lines), and it's sensitive to whether the context matches. Claude's string replacement is simpler and more direct. There's no silver bullet, only trade-offs.

[s04]: ../s04_approval/README.en.md
[s08]: ../s08_rollout/README.en.md

## Deep dive: teaching version vs the real Codex source

The real Codex's apply_patch is a standalone crate [`apply-patch/`](../../codex/codex-rs/apply-patch/): `parser.rs` (954 lines) + `lib.rs` (1689 lines) + `streaming_parser.rs` + `seek_sequence.rs`. The teaching version takes only its skeleton.

<details>
<summary>1. Strict parsing vs lenient parsing</summary>

`parser.rs` has `ParseMode::Strict` and `ParseMode::Lenient`. Because the patch format some models (like gpt-4.1) spit out isn't always perfectly tight, Codex defaults to **lenient** mode (`PARSE_IN_STRICT_MODE = false`), tolerating whitespace before and after markers and so on. The teaching version only implements an approximation of the "lenient" one.

</details>

<details>
<summary>2. Streaming parsing (parse as it generates)</summary>

`streaming_parser.rs` can **incrementally parse** while the patch is still being generated by the model, pairing with a streaming UI to display the diff in real time. The teaching version waits for the whole patch to arrive and parses it all at once.

</details>

<details>
<summary>3. Fuzzy context matching seek_sequence.rs</summary>

This chapter has upgraded `_find_block` to the same **three-tier fallback matching** as `seek_sequence.rs` (exact → ignore trailing → ignore leading-and-trailing whitespace, see the "Production-grade" section). On top of this, the real Codex also does `eof` anchoring (preferring to match from the file's tail) and a byte-level lenient normalization closer to `git apply`. The core idea is the same: **loosen tier by tier, accommodating an LLM that makes small mistakes.**

</details>

<details>
<summary>4. Sandbox-aware file writes + edge cases</summary>

`lib.rs` writes files through `ExecutorFileSystem`, constrained by the sandbox (s05) and approval (s04); it also handles `*** Move to:` renames, binary-file protection, symlinks, `*** End of File`, and other edge cases the teaching version skips.

</details>

## Run

```bash
python s03_apply_patch/code.py --demo   # 不需要模型：演示 Add + Update
python s03_apply_patch/code.py          # 交互模式（mock 会发一个 apply_patch 调用）
```

`--demo` will create a file under `_demo_workspace/` and then edit it, printing the content before and after; when it finishes it **automatically cleans up** that directory (consistent with the other chapters).

## Recap

- Codex expresses all file changes with a single structured patch envelope, rather than per-spot string replacement.
- Context anchoring makes the patch robust to model errors.
- This is a textbook example of "tool design follows model training."
- **Production-grade**: fuzzy matching (seek_sequence three-tier fallback) tolerates wrongly-copied whitespace, two-phase commit guarantees atomicity (no half-finished mess on failure), and a locating failure feeds the error back to the model for self-correction (see the "Production-grade" section).
- Next stop [s04](../s04_approval/README.en.md): patch or command, before it's written to disk should you first ask the user "approve?" — that's the approval policy.

## Think it over

<div class="think">

1. The model miscounts the indentation of an `@@` context line by one space, and exact matching fails — the real Codex falls back on fuzzy matching. How fuzzy do you think is "safe"? What risk does over-fuzziness bring?
2. apply_patch packs multi-file changes into one call. But if the patch's third hunk fails to apply after the first two have already been written to disk — should you roll back? To achieve atomicity, how would you design it?
3. Why would OpenAI rather train the model to output this **custom** patch format than directly use a standard unified diff (`git diff`)? What's wrong with the standard format?
4. If you had Claude (trained to be good at precise string replacement) use apply_patch, or had Codex use `Edit(old→new)`, what would happen? What does this say about the relationship between a tool and a model?

</div>
</content>
</invoke>
