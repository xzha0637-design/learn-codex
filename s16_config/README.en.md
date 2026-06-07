# s16: Config — Layered resolution and named profiles

> 🌐 **English** · [中文版](README.md)

> *"One switch flips an entire posture of autonomy: cautious in prod, unleashed in a throwaway repo."*

[learn-codex overview](../README.en.md) · [s15 MCP](../s15_mcp/README.en.md) → **s16** → [s17 Comprehensive: mini Codex](../s17_comprehensive/README.en.md)

---

## Get the idea straight first: why behavior shouldn't be hardcoded, but "stacked up in layers"

By this chapter, Codex has accumulated a big pile of tunable knobs: which model to use, how strict approval is ([s04](../s04_approval/README.en.md)), how far open the sandbox goes ([s05](../s05_sandbox/README.en.md)), which MCP servers to mount ([s15](../s15_mcp/README.en.md))… If you only think of "config" as "a file with settings written in it," you'll miss the part where Codex is genuinely clever. What it's really solving is a deeper problem: **the same agent should have completely different "nerve" in different scenarios, and that nerve can't be hardcoded — it also has to switch in a second, and it has to let enterprises clamp down from above.** Once you grasp the three ideas below, this seemingly fussy mechanism becomes obvious at a glance.

**Idea one: "what value to use" depends heavily on where you are and what you're doing — so it can't be hardcoded.**
Editing someone else's code in your company's prod repo, what you want is: strict approval, tight sandbox, a stable model — the cost of an incident is too high. But running an experiment in a one-shot throwaway repo under your own `/tmp`, you want exactly the opposite: don't ask me about every single command, give me full permissions, crank the reasoning all the way up — what I want is speed. **Same agent, two scenarios, and almost every single setting should be the reverse.** Hardcode any one set of values into the program, and it's guaranteed to get in the way in the other scenario. The first reason config exists is to pull "behavior" out of the code and turn it into something you can swap per scenario.

**Idea two: instead of assembling a long string of flags every time, package "a whole scenario" into one name.**
Even if the values can live outside the code, if they're scattered across a dozen command-line flags, every time you switch scenarios you have to type out a long string of `--approval … --sandbox … --model …` — hard to remember, and easy to get half of it wrong (you opened the sandbox up but forgot to turn approval back on). The clever move is to package "a whole scenario's worth of config" together and give it a name — this is a **profile**: a profile named `safe` has "require approval + can only write files in the workspace" baked in, and one named `yolo` has "no approval + everything allowed" baked in. Switching scenarios now takes just a name — `--profile safe` or `--profile yolo`. It's like your phone's "scenes": "meeting mode" mutes and turns on vibration with one tap, and you don't go turn off the ringer and turn on vibration one by one. **A profile is the agent's scene; it bundles a set of "autonomy dials" into a single switch.**

**Idea three (the most important): the same setting gets written by several places — who wins? Make the sources "layered," and a later write covers an earlier one.**
Now that we have profiles, a new problem pops up: for the single item "which model," the system ships a default value, your config file `config.toml` (a settings format meant to be hand-written by humans, like `model = "gpt-5-codex"`, sparing you the quotes and braces of JSON) writes one, the profile you picked writes another, and at this launch you also want to temporarily swap in yet another on the command line — **which one actually counts?** Codex's answer is clean and crisp: arrange these sources **into a few layers from low to high**, and **a later layer covers an earlier one** (the industry calls this *last-wins*). It's like a stack of transparent sheets looked at top-down — where the upper sheet has writing it blocks the one below, and where it's blank (transparent) the lower layer shows through. But "stacking" isn't crudely replacing the whole layer; it's a **field-by-field merge** (deep-merge): where the upper layer wrote something, use the upper layer's; where it didn't mention something, keep the lower layer's; and when you hit "settings nested inside settings," drill in and keep merging field by field. So when a profile only wants to change the sandbox and doesn't mention the model, the model automatically carries over the lower layer's value and isn't wiped out. This one layer of "who can cover whom" rules is precisely what gives enterprises the confidence to clamp a "this machine can only be read-only" from the very top.

Tie the three points together: **`config.toml` holds several profiles; once you pick one, it — together with the system defaults and the command-line overrides — gets layered-resolved via deep-merge under "later layer covers earlier layer" into "the one config that actually takes effect this time."** One switch (the profile name) flips an entire posture of autonomy — cautious in prod, unleashed in a throwaway repo. This chapter uses 30 lines of Python to lay the whole mechanism out for you, right down to "which layer exactly won for each field."

## Problem

By this chapter, Codex has piled up a big bunch of tunable knobs: which model to use, how strict the approval policy is ([s04](../s04_approval/README.en.md)), how far open the sandbox goes ([s05](../s05_sandbox/README.en.md)), how much reasoning effort, which MCP servers to mount ([s15](../s15_mcp/README.en.md))…

But "what value to use" **depends heavily on where you are and what you're doing**:

- Editing someone else's code in your company's prod repo: strict approval, tight sandbox, a stable model — the cost of an incident is high.
- Running an experiment in a one-shot throwaway repo under your own `/tmp`: don't ask me about every single command, give me full permissions, crank the reasoning all the way up — what I want is speed.

If these values are scattered across a dozen command-line flags, every time you switch scenarios you have to type out a long string of `--approval … --sandbox … --model …`, hard to remember and easy to mistype. Worse: the values may come from multiple places — the `config.toml` you wrote, the policy your enterprise pushes down, the `-c` you tacked on for just this launch — **which one takes effect?** Without a clear precedence rule, config is a tangled mess.

## Solution

Two things: **named profiles** package "a whole scenario's config" into one name; **layered resolution** uses one explicit precedence chain to merge multiple sources into one effective config — **a later layer covers an earlier one (last-wins)**.

```
   低优先级 ───────────────────────────────────────────▶ 高优先级
   ┌──────────┐  ┌──────────────┐  ┌───────────────┐  ┌──────────┐
   │ DEFAULTS │→ │ config.toml  │→ │ 选中的 profile │→ │ 运行时    │
   │ 系统默认  │  │ 顶层（你的全局）│  │ safe / yolo … │  │ 覆盖(-c)  │
   └──────────┘  └──────────────┘  └───────────────┘  └──────────┘
        每一层只覆盖它显式写了的字段，其余沿用下层 —— deep-merge

   profile = "safe"                    profile = "yolo"
   ┌─────────────────────────┐         ┌─────────────────────────┐
   │ approval = untrusted     │         │ approval = never         │
   │ sandbox  = workspace-write│        │ sandbox  = danger-full   │
   │  → prod / 别人的仓库      │         │  → 一次性草稿仓           │
   └─────────────────────────┘         └─────────────────────────┘
```

Switching scenarios now takes just a name: `--profile safe` or `--profile yolo`.

## How it works

Look at [code.py](code.py), three pieces:

**Step 1 — get the config with zero dependencies.** Prefer the standard library `tomllib` (3.11+) to read `config.toml`; if the environment doesn't have it (or there's no file), fall back to an embedded sample dict — **never pull in a pip dependency**:

```python
try:
    import tomllib            # Python 3.11+
except ImportError:
    tomllib = None            # 退回内嵌 SAMPLE_CONFIG
```

The shape of `SAMPLE_CONFIG` is deliberately aligned with the real source's `ConfigToml`: top-level defaults + a `profiles` map + a `profile` (which one to pick by default). The two profiles' field values are aligned with the real enums too — `approval_policy ∈ {untrusted, on-failure, on-request, never}` (`AskForApproval`), `sandbox_mode ∈ {read-only, workspace-write, danger-full-access}` (`SandboxMode`).

**Step 2 — deep-merge: overlay wins.** This is exactly the semantics of `merge_toml_values` in [`merge.rs`](../../codex/codex-rs/config/src/merge.rs) — if both sides are tables, merge recursively; otherwise the overlay covers the whole thing:

```python
def deep_merge(base, overlay):
    out = dict(base)
    for key, val in overlay.items():
        if isinstance(val, dict) and isinstance(out.get(key), dict):
            out[key] = deep_merge(out[key], val)   # 嵌套表递归
        else:
            out[key] = val                          # 标量：overlay 直接盖
    return out
```

**Step 3 — `resolve()` stacks four layers and records, for each field, "which layer won."** This provenance (source tracking) is the highlight of this chapter — it turns "last-wins" from an abstract rule into something visible to the naked eye:

```python
eff = dict(DEFAULTS)                              # 第0层：系统默认
eff = lay(eff, top, "config.toml")               # 第1层：config.toml 顶层
eff = lay(eff, prof, f"profile:{chosen}")        # 第2层：选中的 profile
eff = lay(eff, overrides, "override")            # 第3层：运行时覆盖（最高）
```

`--demo` resolves the two profiles `safe` and `yolo`, prints field by field "effective value + which layer it came from," and adds a "yolo + runtime override of model" block to demonstrate how the highest layer covers the value the profile gave (last-wins). `PROFILE_FIELDS` restricts a profile to only cover the fields it's supposed to — corresponding to the real `ConfigProfile`'s `#[serde(deny_unknown_fields)]`.

**Walk through it**: follow one `resolve(cfg, "yolo", overrides={"model": "gpt-5-codex-mini"})` and watch the "four sheets" stack up the final config one by one. We keep our eyes on a single field, `model`, watching what it gets written to at each layer and who wins in the end.

Layer 0 **DEFAULTS** (system defaults, the bottom sheet) — every field has a fallback value:

```python
{"model": "gpt-5-codex", "approval_policy": "on-request",
 "sandbox_mode": "read-only", "model_reasoning_effort": "medium"}
# provenance: 此刻每个字段都记成 "default"
```

Layer 1 **config.toml top level** — the top level of `SAMPLE_CONFIG` only writes one `model` (bumping the default model up a notch), nothing else:

```python
overlay = {"model": "gpt-5-codex-high"}     # 只有这一个字段
# deep-merge 后 model 变成 gpt-5-codex-high；其余沿用第 0 层（胶片没盖到的地方露出下层）
# provenance["model"] = "config.toml"
```

Layer 2 **profile = yolo** — this is what the `yolo` sheet writes (note it **does not write** `model`):

```python
{"approval_policy": "never", "sandbox_mode": "danger-full-access",
 "model_reasoning_effort": "high"}
# 它改了 approval / sandbox / reasoning 三项；model 这一格是透明的 → 仍露出第 1 层的 gpt-5-codex-high
# provenance: approval_policy→"profile:yolo", sandbox_mode→"profile:yolo", model_reasoning_effort→"profile:yolo"
```

Layer 3 **runtime override** (the topmost sheet, highest precedence) — at this launch we temporarily pass `{"model": "gpt-5-codex-mini"}`:

```python
{"model": "gpt-5-codex-mini"}
# model 被这张最高层胶片盖成 gpt-5-codex-mini —— 第 1 层写的 high 被压在下面看不见了
# provenance["model"] = "override"   ← model 的归属从 config.toml 变成了 override
```

**Why the result comes out this way**: `model` got written three times along the way (default→config.toml→override), but provenance finally records `override`, because it's the highest layer and the last to touch this field — that's last-wins. Whereas `approval_policy` was only written by the `yolo` layer, so it belongs to `profile:yolo`. The final block ③ that `--demo` prints is exactly:

```
字段                    生效值                来自哪一层
model                   gpt-5-codex-mini      override        ← 被运行时覆盖盖掉
approval_policy         never                 profile:yolo
sandbox_mode            danger-full-access    profile:yolo
model_reasoning_effort  high                  profile:yolo
```

Once you understand this table, you understand the whole layered config: **each field independently "counts upward to the last layer that wrote it," and that layer's value is the effective value.**

## Production-grade: validate at the boundary — reject bad config at load time, don't drag it to runtime

Layered resolution is elegant, but it has a hidden hazard: what happens if a profile writes `approval_policy = "yolo-mode"` (a tier that doesn't exist), or spells the field name as `sandbox_modee`? A lenient parser will **silently accept it** — and then your agent sets off carrying an approval policy nobody recognizes, only to discover it when runtime behavior gets weird. The production-grade answer is: **validate strictly at the loading boundary, reject bad config on the spot, and report clearly what's wrong.**

Real Codex uses two weapons (both at the type level):

- **`#[serde(deny_unknown_fields)]`**: a misspelled field name (`sandbox_modee`) isn't quietly ignored, it makes the parse fail outright.
- **typed enum**: the type of `approval_policy` is the `AskForApproval` enum, so `"yolo-mode"` simply can't deserialize into it — an illegal value is blocked at load time.

This chapter's `validate_profile` makes this pedagogical, and `--demo` takes a profile with three errors at once to demonstrate:

```
✗ profile 'typo': `approval_policy`='yolo-mode' 非法（合法：['never','on-failure','on-request','untrusted']）
✗ profile 'typo': 未知字段 `sandbox_modee`（合法：['approval_policy','model','model_reasoning_effort','sandbox_mode']）
✗ profile 'typo': `model_reasoning_effort`='extreme' 非法（合法：['high','low','medium']）
```

> In one sentence: config is the **master switch for the safety tiers** (it decides which approval tier, how big the sandbox opens) — precisely because it's this critical, the more you want to weld it shut at the boundary: **better to fail at load than to run with a wrong safety config.** This is the same "when in doubt, fail toward safety" engineering instinct as the sandbox's deny-default and approval's fail-closed.

## 🆚 How it differs from Claude Code

| | Claude Code | Codex |
|---|---|---|
| Config file | `settings.json` (JSON) | `config.toml` (TOML) ⭐ |
| Switch scenarios | edit settings / command-line args | **named profile**: one name packages a whole set (model + approval + sandbox + …) |
| Source synthesis | merge settings items | **explicit layered stack**: system → cloud → user → thread, last-wins layer by layer |
| One-tap autonomy switch | rather scattered | `--profile safe` / `--profile yolo` flips cautious / unleashed in a second |

**Why does Codex build this whole layering + profile setup?** Because its running scenarios are far wider than "single-person local interaction" — the same core has to run in a local terminal, in CI, in the cloud, and may also be constrained by enterprise policy. This kind of environment is inherently **multi-source**:

- **The enterprise has to be able to clamp a layer from above**: the policy the admin pushes down (the real system / cloud layers) must be able to cover the user's casual settings, otherwise in a managed environment there's no way to enforce "this machine can only be read-only." The layered stack provides this capability.
- **Autonomy has to be switchable with one tap**: Codex bets on low-intervention autonomous running, and "how autonomous it should be" depends heavily on the scenario. A profile bundles the "autonomy dials" of `approval + sandbox + model` into one name — `safe` for prod, `yolo` for the throwaway repo, no need to manually assemble a long string of flags each time, and no getting half of it wrong.
- **Runtime still has to be able to clamp a temporary layer**: want to swap the model for just this launch? `-c model=…` goes on as the highest layer without touching your `config.toml`.

This is consistent with the whole course's through-line: Claude Code centers on **interactive UX**, config just needs to be good enough and pops up a dialog on the spot; Codex bets on **headless / CI / cloud**, so it makes "who can cover whom" into an **explicit, enterprise-takeover-able, one-tap-autonomy-switchable** layered system.

## Deep dive: the teaching version vs. real Codex source

The teaching version `resolve()` is about 30 lines, four layers. Real Codex's config subsystem is `codex-rs/config/` (`types.rs` alone is 35,000+ lines, `config_requirements.rs` 120,000 lines), and the four blocks below make the gaps clear.

<details>
<summary>1. The real layer stack: system → cloud → user → thread, plus enterprise MDM</summary>

The teaching version's four layers (DEFAULTS → config.toml → profile → override) are a simplification of the real layer stack. The real `ConfigLayerStack` (`config/src/state.rs:236`) has a very blunt comment:

```rust
/// Layers are listed from lowest precedence (base) to highest (top),
/// so later entries in the Vec override earlier ones.   ← 和教学版 last-wins 完全一致
layers: Vec<ConfigLayerEntry>,
```

Each layer has a source tag `ConfigLayerSource` (`state.rs`):

| Layer (low→high precedence) | `ConfigLayerSource` variant | Who wrote it |
|---|---|---|
| Enterprise MDM / managed | `Mdm` / `EnterpriseManaged` | device management policy, user **cannot** override |
| System | `System { file }` | system-level config |
| Cloud-pushed | (`cloud_config_layers`) | fragments pushed by Codex Web / the org |
| User | `User { file, profile }` | your `$CODEX_HOME/config.toml` (**includes the profile sub-layer**) |
| Project | `Project { dot_codex_folder }` | the `.codex/` in the repo |
| Session flags | `SessionFlags` | this launch's `-c` / CLI overrides (highest) |

Note the `User` layer actually **splits into two sub-layers**: the base `config.toml` + the selected profile's override (which is why the code records a `user_layer_index` pointing at the writable layer). The teaching version splitting "config.toml top level" and "profile" into two layers is exactly imitating this. `merge_toml_values` is then the function that does the real work when merging layer by layer (the teaching version's `deep_merge` copies it).

</details>

<details>
<summary>2. What a profile can actually hold: the full field panorama of ConfigProfile</summary>

The teaching version's `PROFILE_FIELDS` only puts in 4 fields (model / approval / sandbox / reasoning_effort). The real `ConfigProfile` (`config/src/profile_toml.rs:24`) can package far more:

```rust
pub struct ConfigProfile {
    pub model: Option<String>,
    pub model_provider: Option<String>,
    pub approval_policy: Option<AskForApproval>,     // ← 教学版有
    pub sandbox_mode: Option<SandboxMode>,           // ← 教学版有
    pub model_reasoning_effort: Option<ReasoningEffort>,
    pub model_reasoning_summary: Option<ReasoningSummary>,
    pub model_verbosity: Option<Verbosity>,
    pub web_search: Option<WebSearchMode>,
    pub tools: Option<ToolsToml>,
    pub features: Option<FeaturesToml>,              // ← 见下一块「feature flags」
    pub personality: Option<Personality>,
    // …还有十几个
}
```

Every field is an `Option<T>`: **`None` means "this profile doesn't manage this item,"** so during merge the field carries over the lower layer's value — this is exactly the semantics the teaching version's `lay()` is trying to express with "only record into provenance the fields the layer actually wrote." `#[serde(deny_unknown_fields)]` then guarantees you can't stuff random keys into a profile (the teaching version simulates this with the `PROFILE_FIELDS` allowlist).

</details>

<details>
<summary>3. sandbox + approval + model + mcp_servers all live in config</summary>

Worth calling out specially: the "config entry points" for all those mechanisms in the previous chapters ultimately funnel into the single `ConfigToml` (`config/src/config_toml.rs:139`) structure:

| Config item | Field | Corresponding chapter |
|---|---|---|
| which model to use | `model: Option<String>` | s09 |
| approval policy | `approval_policy: Option<AskForApproval>` | s04 |
| sandbox tier | `sandbox_mode: Option<SandboxMode>` | s05 |
| permission profile | `permissions` / `permission_profile` | s05 / s14 |
| MCP server | `mcp_servers: HashMap<String, McpServerConfig>` | s15 |
| named profile | `profiles: HashMap<String, ConfigProfile>` | this chapter |
| which profile to pick by default | `profile: Option<String>` | this chapter |

That is: **config isn't the settings of some isolated module, it's the bus for the entire agent's behavior**. The teaching version only demonstrates the layering of the four fields model / approval / sandbox / reasoning, but the same layering rules apply equally to nested tables like `mcp_servers` (`deep_merge` recurses in to merge) — which is also why the real version goes to the trouble of implementing "recursive table merge" instead of simple wholesale replacement.

</details>

<details>
<summary>4. ThreadSettings: override config at runtime, without restarting (continues from s10)</summary>

The teaching version's "layer 3 override" is a static dict. In the real version, changing config at runtime is an **`Op`** (continuing the SQ/EQ protocol from [s10](../s10_sq_eq_protocol/README.en.md), `protocol.rs:492`):

```rust
Op::ThreadSettings {
    thread_settings: ThreadSettingsOverrides,   // 改 model / 审批 / 沙箱…
}
// core 应用后回一个事件确认：
EventMsg::ThreadSettingsApplied(ThreadSettingsAppliedEvent)
```

This is the true identity of the thread layer (highest precedence): when you switch the model or relax approval **partway through** a session, the frontend submits an `Op::ThreadSettings`, the core merges it as the highest layer into the effective config and returns a `ThreadSettingsApplied` event, **without restarting the session**. The teaching version has no queue, so it flattens this into "pass an `overrides` dict to `resolve()`" — but the precedence position (covering the profile and config.toml) matches the real version.

To string it together in one sentence: **`config.toml` + profile define "the initial values at session start," and `Op::ThreadSettings` is "the temporary rewrite mid-session,"** both landing at different heights on the same layered stack.

</details>

## Run

```bash
python s16_config/code.py --demo   # 解析 safe / yolo，逐字段打印「哪层赢了」（无需 key，离线）
python s16_config/code.py          # 交互模式：输入 profile 名，看生效配置
```

No external dependencies: uses the standard library `tomllib` (3.11+); if the environment doesn't have it, it automatically falls back to the embedded sample config.

## Recap

- Config comes from multiple sources, synthesized with one **explicit precedence chain**: DEFAULTS → config.toml → profile → runtime override, **a later layer covers an earlier one (last-wins)**, stacked layer by layer via deep-merge.
- **Named profiles** package "a whole autonomy config" into one name — `safe` (require approval + workspace write) / `yolo` (no approval + full permissions) switch in a second.
- Real Codex's layer stack is thicker: **system / enterprise MDM → cloud → user(config.toml + profile) → project → session flags**; `ConfigProfile` can package over a dozen fields; `mcp_servers`, `approval`, `sandbox`, `model` all funnel into the same `ConfigToml`.
- Changing config at runtime is an `Op::ThreadSettings` (continuing [s10](../s10_sq_eq_protocol/README.en.md)), landing at the top of the layer stack, no restart needed.
- **Production-grade**: config is the master switch for the safety tiers, and must be strictly validated at the load boundary — `deny_unknown_fields` (a misspelled field errors out directly) + typed enum (an illegal enum value fails to deserialize), bad config is rejected on the spot, never silently taking effect (see the "Production-grade" section).
- Next stop [s17](../s17_comprehensive/README.en.md): assembling the mechanisms from the first 16 chapters into one complete mini Codex.

## Think it over

1. The teaching version's "later layer covers earlier layer" is simple and clear. But in the real version the enterprise MDM layer is one of the **lowest** precedence and yet "the user cannot override it" — these two things look contradictory, so how does Codex pull off "enterprise policy both participates in the merge and can't be overturned by the user"? If it were up to you to design, should constraints (non-overridable) and defaults (overridable) be expressed with the same layering mechanism?

2. A profile bundles `approval + sandbox + model` into one name, and switching is great. But "bundling" also means you might **only want to change the sandbox, yet swap approval along with it by accident**. Once a preset like `yolo` ("no approval + full permissions") gets used in a real repo by a slip of the hand… how would you backstop this convenience-vs-safety tension — should you add a second confirmation to dangerous profiles?

3. This chapter prefers `tomllib` for TOML and falls back to an embedded dict, for the sake of zero dependencies. But the real version's config subsystem is so huge that `config_requirements.rs` has 120,000 lines — why does a "read config" matter balloon into this? When config has to support "enterprise push + cloud sync + runtime rewrite + strict validation," where does all the complexity go?

4. Claude Code uses `settings.json`, Codex uses `config.toml` + layering + profiles. Both are about "letting the user configure the agent," one leaning lightweight, the other leaning systematic — how much of this difference is the "JSON vs TOML" matter of taste, and how much is the inevitability forced out by "single interactive frontend vs headless/CI/cloud multi-frontend"? If you were building a CI-facing agent, how would you design the config system?
