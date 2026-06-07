# s06: AGENTS.md — walk up from cwd, injecting project rules layer by layer

> 🌐 **English** · [中文版](README.md)

> *"A project's rules live in files, stacking up the directory tree layer by layer — the root sets the tone, the children fine-tune."*

[learn-codex overview](../README.en.md) · Previous: [s05 sandbox](../s05_sandbox/README.en.md) → **s06** → Next: [s07 context compaction](../s07_context_compaction/README.en.md)

---

## Get the idea straight first: why "feed the model project memory," and why "search upward along the directory tree"

The agents in the previous chapters have hands (they can edit files) and locks (the sandbox). But they still have a fundamental blind spot: **they know nothing about your project**. This chapter solves exactly that — "teaching them to do as the locals do." Get two ideas across and you'll understand why AGENTS.md looks the way it does.

**Idea one: the model isn't breaking your rules on purpose — it simply doesn't know them, so you have to write them down and hand them over.**
You just hired a highly skilled contractor. On day one, there's no way they know "we indent with 2 spaces," "commit messages go in Chinese," "nobody touches that `legacy/` directory." They aren't deliberately causing trouble — **nobody told them**. The model is the same: every new conversation it starts is like an "amnesiac's day one," so its indentation flips between 2 and 4, and its commit messages flip between Chinese and English.
What to do? The plainest and most effective fix — **write the project's rules onto a note, and slip it to the model to read before every job.** That note is `AGENTS.md`: a plain-text file placed inside the project, containing "things to know about this project" written for the model. The mechanism is plain to the point of being mundane, but it turns "the model doesn't know the rules" into "did you write the rules down" — a question **you control**.

**Idea two: one note isn't enough — a big project's rules are layered, so discovering them has to be layered too.**
But you'll soon run into a real structure: in a big repo (monorepo) the rules are **not a single set**. The repo root has one global tone, but `packages/web` insists on 2 spaces, `packages/api` uses 4, and `services/legacy` still keeps its own ancestral rules. A single "global note" simply can't express this "**globally consistent, locally exceptional**" structure.
So how do you make the agent know the global tone *and* respect the special rules of the corner you're currently in? The answer hides in a very natural analogy — **law is layered**: a nation has a constitution, a province has its statutes, a city has its bylaws; when you do business in a city, all three apply to you **at once**, and the more specific the layer, the more it can override the layers above on the details. Project rules should be organized the same way.
So Codex's approach is: **starting from the directory you're currently in (cwd), walk up the directory tree** until you hit the "project root" marker (by default the `.git` directory, which naturally marks "the repo starts here"), collecting every `AGENTS.md` along the way. The act of "walking up" itself answers "which layers do I belong to" — just as you'd state your address as "China · Zhejiang · Hangzhou," from specific to general, exactly the chain you belong to.

**Idea three: concatenating with "root first, leaf last" works because the model reads in order, and what it sees later can override what it saw earlier.**
Once you've collected a stack of rules, in what order do you concatenate them for the model? Codex picks **root first, the directory you're in last**. This isn't arbitrary — it exploits a property of how the model reads text: it reads **in order**, and **later instructions can refine or even override earlier ones**. So "the repo sets the tone (read first), the sub-package makes exceptions (read later, can override)" naturally holds — translating exactly the "law is layered, lower overrides upper" intuition into a shape the model can follow.

Putting it together: the model "forgets" every time, so write the rules into a file and feed them to it (idea one); a big project's rules are layered, so walk up the directory tree and collect them layer by layer (idea two); concatenate root → leaf so the specific local rules can override the general global ones (idea three). The three steps together turn the agent from a "newcomer who doesn't know the rules" into a "veteran who has read this project's notes and can tell global from local."

## Problem

You ask an agent to work in a big repo. The repo has its own rules: how many spaces to indent, what language commit messages use, how to run tests, which directories not to touch. But the model knows none of it — every time it starts from scratch and guesses, so the indentation style flips between 2 and 4, and commit messages flip between Chinese and English.

Worse is the **monorepo**: the repo root has one global convention, but `packages/web` uses 2 spaces, `packages/api` uses 4, and `services/legacy` has its own ancestral rules. A single "global instruction" simply can't express this "globally consistent, locally exceptional" structure.

What you need is: **let the project write its rules into files, and have the agent automatically discover them and stack them into context by hierarchy** — where the rules closest to your current working directory can locally override the conventions of the layers above.

## Solution

Codex's answer is `AGENTS.md`: a kind of plain-text instruction file placed inside directories. Rule discovery is **layered** —

1. From the current working directory `cwd`, **walk up** until you hit a "project root marker" (by default the `.git` directory);
2. From the project root **back down** to `cwd` (inclusive on both ends), collect the `AGENTS.md` in each directory layer by layer;
3. Concatenate in **root → cwd** order (root first), with a separator marking the boundaries between them;
4. Wrap the result in a `<user_instructions>` block and inject it into this turn's `instructions` (system).

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

"Root first, leaf last" is key: the model reads instructions in order, and **what appears later can refine or even override what appeared earlier** — exactly matching "the repo sets the tone, the sub-package makes exceptions."

## How it works

Look at [code.py](code.py); three functions string the whole path together:

**Step 1** — `find_project_root(start)`: walk upward through every ancestor of `start`, return on hitting any root marker (default `.git`); if you reach the top without one, return `None` (in which case it only looks at `cwd` itself, never crossing the boundary upward).

```python
def find_project_root(start: Path) -> Path | None:
    for ancestor in [start, *start.parents]:
        for marker in PROJECT_ROOT_MARKERS:   # 默认 [".git"]
            if (ancestor / marker).exists():
                return ancestor
    return None
```

**Step 2** — `discover_agents_md(cwd)`: first compute the **root→cwd** directory sequence (`search_dirs` collects from cwd upward to root, then `reverse()`), and for each directory find the first matching candidate filename (`AGENTS.override.md` takes priority over `AGENTS.md`):

```python
for d in search_dirs(cwd):            # 顺序：根 → cwd
    for name in AGENTS_FILENAMES:     # ["AGENTS.override.md", "AGENTS.md"]
        if (d / name).is_file():
            found.append(d / name)
            break                     # 一个目录只取一个（override 优先）
```

**Step 3** — `read_agents_md` reads within a budget (`PROJECT_DOC_MAX_BYTES`, default 32 KiB) and concatenates with a separator; `build_user_instructions_block` then wraps it into a `<user_instructions>` block, which `run_turn` splices into this turn's system. Note that `run_turn` **rediscovers with the current cwd every turn** — project instructions change with the directory.

This faithfully maps to the real source [`core/src/agents_md.rs`](../../codex/codex-rs/core/src/agents_md.rs): `AgentsMdManager`'s `agents_md_paths` does the "find the root upward, collect downward" (`dir.ancestors()` + `dirs.reverse()`), `read_agents_md` maintains the byte budget, and the separator constant is `AGENTS_MD_SEPARATOR = "\n\n--- project-doc ---\n\n"`. In the end it becomes a `<user_instructions>` block (`USER_INSTRUCTIONS_OPEN_TAG` in [`protocol/src/protocol.rs`](../../codex/codex-rs/protocol/src/protocol.rs)) injected into the turn.

**Walk through it** — take a real small directory tree and watch these three steps assemble scattered rules into one block of injected text. Suppose the disk looks like this (the repo root has a `.git` marker, and the root and a sub-package each hold an `AGENTS.md`):

```text
proj/.git/                ← 项目根标记
proj/AGENTS.md            ← 仓库级：用 4 空格缩进 / 提交信息用英文
proj/sub/AGENTS.md        ← 子包级：本目录改用 2 空格 / 跑 `pytest -q`
```

Now you're standing in the deepest `proj/sub` and start a turn, cwd = `proj/sub`:

Step 1, `find_project_root(proj/sub)` tries upward layer by layer from `sub`: no `.git` in `sub`? Up one more layer — `proj` has `.git`, stop. **Project root = `proj`**.

Step 2, `discover_agents_md` first collects the "from cwd upward to root" chain, then reverses it, yielding the **root→cwd** discovery order:

```text
1. proj/AGENTS.md        （根，最前）
2. proj/sub/AGENTS.md    （叶，最后）
```

Step 3, read in this order, concatenate with the `--- project-doc ---` separator, then wrap in the `<user_instructions>` tag — this is the exact block of text that gets stuffed into this turn's system:

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

Look at the order of this text and you'll get "idea three": the root's "4 spaces" comes first, the sub-package's "2 spaces" comes last. The model reads straight through, and when it reaches the sub-package line it uses it to **override** the root's indentation convention — so in `sub` it writes 2 spaces, but "commit messages in English," a root rule that wasn't overridden, still holds. One block of concatenated text expresses both "global tone + local exception" at once.

`--demo` shows it directly: it builds `tmp/proj/.git` + `tmp/proj/AGENTS.md` + `tmp/proj/sub/AGENTS.md`, starts discovery from the deepest `sub`, prints the "root first" concatenated result (which is the block above), and deletes the whole temporary tree when done.

## Production-grade: project docs can also run wild — byte budget + truncation

AGENTS.md is text the user/project can stuff into every turn's system. A well-meaning but runaway project might write a **tens-of-thousands-of-lines** AGENTS.md (or a chain of directories stacking up), and injecting it verbatim would eat the context window dry — out of room before you even start working. The production-grade approach is to put a **byte budget** on the injection: real Codex uses `project_doc_max_bytes` ([`agents_md.rs:133`](../../codex/codex-rs/core/src/agents_md.rs)), accumulating bytes as it collects along the directory tree, and once it exceeds the remaining budget it `truncate`s away the excess (agents_md.rs:166-168) — better to truncate than to let project memory crowd out the actual conversation.

Merging also has an **explicit priority**: collect upward level by level from cwd, where the closer to the file the more specific, the more it should win (echoing [s16](../s16_config/README.en.md)'s layered last-wins, where the real source likewise uses `merge_toml_values`). "Picking up rules level by level going up" isn't arbitrary concatenation — it's a directional merge chain.

> In a word: anything "injectable into context by the user/project" (AGENTS.md, skills, tool output) must have a **budget cap** — otherwise it's a backdoor around compaction ([s07](../s07_context_compaction/README.en.md)).

## 🆚 How it differs from Claude Code

| | Claude Code | Codex |
|---|---|---|
| Filename | `CLAUDE.md` | `AGENTS.md` (a cross-tool / cross-vendor standard) |
| Discovery | Also reads project-level memory, but centers more on a single file + imports | **Layered**: walk up from cwd to find the root (`.git`), then collect root→cwd layer by layer |
| Stacking order | Project memory + user memory merged | **Root→leaf concatenation**, where the leaf layer can locally override the root layer's conventions |
| Injection form | Into the system prompt / memory | Wrapped into a `<user_instructions>` block injected into the turn's `instructions` |
| Boundary control | — | Never crosses the project root; capped by `project_doc_max_bytes` (default 32 KiB) |

**Why?** Two trade-offs:

- **A cross-vendor standard.** `AGENTS.md` is a **tool-agnostic** convention Codex spearheaded — the same file can be read by multiple agent tools, rather than being locked to some CLI's private filename (`CLAUDE.md` to Claude Code). This aligns with the whole course's through-line: Codex bets on headless / CI / cloud-style "unattended, multi-tool collaboration" scenarios, where you need a neutral instruction vehicle that everyone recognizes.
- **Layered discovery is born for the monorepo.** "Find the root upward, collect downward layer by layer, leaf layer can override" precisely expresses the hierarchical structure of "the repo sets the tone, the sub-package makes exceptions." Claude Code leans more toward a single interactive workspace, where a single file + imports is already enough; Codex has to be pulled up and run by `codex exec` in any subdirectory of a large monorepo, so it must let instructions auto-assemble in layers "according to where you stand."

## Deep dive: teaching version vs real Codex source

<details>
<summary>1. Walking up to find the root: ancestors + project_root_markers</summary>

The teaching version's `find_project_root` iterates with `[start, *start.parents]` and stops on hitting `.git`. The real source's `agents_md_paths` in `agents_md.rs` walks `dir.ancestors()`, calling `fs.get_metadata` on each ancestor's `ancestor.join(marker)` to check existence; the marker list comes from config:

| Behavior | Real source |
|---|---|
| Default markers | `default_project_root_markers()` → `DEFAULT_PROJECT_ROOT_MARKERS = &[".git"]` (`config/src/project_root_markers.rs`) |
| Config override | `project_root_markers_from_config` reads the `project_root_markers` array from `config.toml` |
| **Semantics of an empty array** | `Ok(Some(Vec::new()))` — **disables upward traversal**, looks only at cwd itself |
| No root found | `search_dirs` degrades to `vec![dir]`, never crossing the boundary upward |

Note the real source also merges the config layer stack (skipping the `Project` layer); the teaching version drops this multi-layer config merge — it has no effect on the "discovery path" itself, only on which layer the markers come from.

```rust
// agents_md.rs：找到根后，从 cwd 向上收集到 root，再反转成 根→cwd
let mut cursor = dir.clone();
loop { dirs.push(cursor.clone()); if cursor == root { break; } cursor = parent; }
dirs.reverse();   // 根在最前
```

</details>

<details>
<summary>2. Candidate filenames and the AGENTS.override.md fallback</summary>

The teaching version's `AGENTS_FILENAMES = ["AGENTS.override.md", "AGENTS.md"]` stops on the first hit in a directory. The real source's `candidate_filenames()` has the exact same order, and additionally supports appending user-configured `project_doc_fallback_filenames` afterward:

```rust
names.push(LOCAL_AGENTS_MD_FILENAME);    // "AGENTS.override.md"  —— 优先
names.push(DEFAULT_AGENTS_MD_FILENAME);  // "AGENTS.md"
for candidate in &self.config.project_doc_fallback_filenames { ... }  // 再追加自定义兜底
```

The purpose of `AGENTS.override.md`: it lets you drop a **local, usually .gitignore'd** override file **without modifying** the team's shared `AGENTS.md` (typically already committed to git). The real source also has a separate path, `load_global_instructions`, which reads a global `AGENTS.override.md` / `AGENTS.md` from `~/.codex/` (CODEX_HOME) as `User`-level instructions — it and the project level are two provenances, and when concatenating, the `--- project-doc ---` separator is inserted only at the user/internal → project transition. The teaching version simplifies this whole provenance system down to "uniformly separate each project doc with a separator."

</details>

<details>
<summary>3. The byte budget project_doc_max_bytes and truncation</summary>

The teaching version's `read_agents_md` maintains a `remaining` byte budget and, on overflow, truncates with `data[:remaining]` and stops. The real source's logic is identical, with default value `DEFAULT_PROJECT_DOC_MAX_BYTES = 32 * 1024` (`config/src/config_toml.rs`):

| Detail | Real source |
|---|---|
| Budget of 0 | `read_agents_md` returns `Ok(None)` directly — AGENTS.md fully disabled |
| Per-file decrement | `remaining = remaining.saturating_sub(data.len())` |
| Over-budget truncation | `data.truncate(remaining)` plus a `tracing::warn!` "truncating" log line |
| Invalid UTF-8 | `warn_invalid_utf8` pushes a startup warning, then `from_utf8_lossy` replaces the bad bytes |
| Non-file entries | `get_metadata` checks `is_file`; directories / symlink anomalies are skipped |

Because the budget is consumed in **root→cwd order**, the files closer to cwd (deeper, more specific) are the ones most likely to be cut when the budget runs out — a deliberate trade-off: better to preserve the upper layers' global conventions.

</details>

<details>
<summary>4. How it becomes a user_instructions block injected into the turn</summary>

The teaching version uses one layer of `<user_instructions> ... </user_instructions>` to express "this is the injected instruction block." The real source's wrapping is two layers:

- Inner: the `UserInstructions` fragment in `core/src/context/user_instructions.rs`, whose `body()` produces `"{directory}\n\n<INSTRUCTIONS>\n{text}\n"`, with `type_markers()` being `("# AGENTS.md instructions for ", "</INSTRUCTIONS>")` — meaning the real injection **carries the cwd path**, telling the model "which directory these rules correspond to."
- Outer: `USER_INSTRUCTIONS_OPEN_TAG` / `USER_INSTRUCTIONS_CLOSE_TAG` (`<user_instructions>` / `</user_instructions>`) in `protocol/src/protocol.rs`.

The injection happens during turn construction (around `session/turn.rs`), placed into the prompt as a `user`-role message alongside other context fragments like `<environment_context>`. There's also a `Feature::ChildAgentsMd` toggle that, when enabled, additionally appends a `HIERARCHICAL_AGENTS_MESSAGE` internal guidance telling the model "you will see layered AGENTS.md."

In a word: the teaching version's ~80 lines of "find the root upward + collect downward + concatenate + wrap in tags" is the core of `agents_md.rs` (433 lines) + the provenance system + config-layer merging + the global/project dual-source; everything else is production-grade guardrails like the budget, UTF-8, and multi-source provenance.

</details>

## Run

```bash
python s06_agents_md/code.py --demo   # 自建临时目录树演示分层发现，跑完自动清理（mock，无需 key）
python s06_agents_md/code.py          # 交互模式：输入一个目录看从那里发现的 AGENTS.md
```

`--demo` runs entirely offline, never calls the model, and deletes the temporary directory tree it created when it finishes.

## Recap

- `AGENTS.md` = project rules written into directories, **discovered hierarchically**: walk up from cwd to find the root (default `.git`), then collect root→cwd layer by layer.
- The concatenation order "root first, leaf last" lets sub-packages locally override repo-level conventions — a natural fit for the monorepo.
- Capped by `project_doc_max_bytes` (default 32 KiB), with `AGENTS.override.md` providing a local override that doesn't touch the shared file.
- Ultimately wrapped into a `<user_instructions>` block injected into the turn's `instructions` — the same kind as Claude Code's `CLAUDE.md`, but taking the cross-vendor standard + layered discovery route.
- **Production-grade**: AGENTS.md injection has a **byte budget** (`project_doc_max_bytes`), and it truncates on overflow — don't let a runaway project doc crowd out the actual conversation; merging has an explicit priority by directory level (see the "Production-grade" section).
- Next stop [s07](../s07_context_compaction/README.en.md): once the conversation gets long, how to compact old turns into summaries and free up context.

## Think it over

- "Root first, leaf last, the latter can override the former" relies on the model **faithfully reading in order and letting later text beat earlier text**. If the model doesn't always do this (say it trusts the global rules it saw first more), does layered override still hold? How would you tune the wording in `AGENTS.md` to make "override" more robust?
- The project root marker defaults to `.git`. Running `codex exec` in a directory without `.git` (e.g., an unpacked tarball, a temporary sandbox) degrades discovery to "look only at cwd." Is this good or a hazard for "unattended automation"? What would you set the marker to?
- Injecting `AGENTS.md` into **every turn** means it continuously occupies the context budget; meanwhile s07 needs to compact history to make room. When 32 KiB of project instructions and compacted conversation history compete for the same window, which should be preserved? Codex chooses to preserve the upper-layer global rules — how would you weigh it?
- `AGENTS.md` is read by the "model," lives in the repo, and may be consumed by multiple tools. Will it gradually overlap or even conflict with the human-facing `README.md` and `CONTRIBUTING.md`? If the same rule must be told to both humans and the model, would you rather maintain one copy or two?
