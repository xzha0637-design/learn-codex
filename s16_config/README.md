# s16: Config — 分层解析与命名 profile

> *"一个开关切换一整套自主度：prod 上谨慎，草稿仓里放飞。"*

[learn-codex 总览](../README.md) · [s15 MCP](../s15_mcp/) → **s16** → [s17 综合：迷你 Codex](../s17_comprehensive/)

---

## 先把思想说透：为什么行为不该写死，而要「分层叠出来」

走到这章，Codex 已经攒下一大堆可调项：用哪个模型、审批多严（[s04](../s04_approval/)）、沙箱开到哪档（[s05](../s05_sandbox/)）、挂哪些 MCP server（[s15](../s15_mcp/)）……如果你只把「配置」想成「一个写着设置的文件」，会错过 Codex 真正巧妙的地方。它要解决的其实是一个更深的问题：**同一个 agent，在不同场景下该有完全不同的「胆量」，而这份胆量不能写死、还得能一秒切换、还得让企业从上面压得住。** 想通下面三个道理，这套看似繁琐的机制就一目了然了。

**道理一：「该用什么值」强烈依赖你在哪、在干什么——所以不能写死。**
在公司的 prod 仓库里替别人改代码，你要的是：审批要严、沙箱要紧、模型要稳——出事代价太高。可在自己 `/tmp` 下的一次性草稿仓里跑实验，你要的恰恰相反：别每条命令都问我、给我全权限、推理拉满——我要的是速度。**同一个 agent，两种场景，几乎每一项设置都该反着来。** 把任何一套值硬编码进程序，都必然在另一个场景里碍事。配置存在的第一个理由，就是把「行为」从代码里拽出来，变成可以随场景换的东西。

**道理二：与其每次拼一长串 flag，不如把「一整套场景」打包成一个名字。**
就算值可以外置，如果它们散落在十几个命令行 flag 里，每次切场景你都得敲 `--approval … --sandbox … --model …` 一长串——记不住，还容易切错一半（把沙箱放开了却忘了关审批）。巧办法是把「一整套场景配置」打包起来、起个名字，这就是 **profile**：名叫 `safe` 的 profile 里写好「要审批 + 只能在工作区写文件」，名叫 `yolo` 的写好「不审批 + 全部放行」。切场景从此只要一个名字——`--profile safe` 或 `--profile yolo`。这就像手机的「情景模式」：「会议模式」一键静音加震动，你不用挨个去关铃声、开震动。**profile 就是 agent 的情景模式，它把一组「自主度旋钮」捆成一个开关。**

**道理三（最关键）：同一项设置会被好几个地方写到，谁说了算？让来源「分层」，后写的盖前写的。**
现在有了 profile，新问题冒出来了：「用哪个模型」这一项，系统自带一个默认值、你的配置文件 `config.toml`（一种给人手写的设置格式，`model = "gpt-5-codex"` 这样，比 JSON 更省引号大括号）写了一个、你选的 profile 又写了一个、这次启动命令行临时还想再换一个——**到底哪个算数？** Codex 的答案干净利落：把这些来源**从低到高排成几层**，**后面的层盖住前面的层**（业内叫 *last-wins*）。这就像一摞透明胶片叠着往下看——上面那张写了字的地方挡住下面，没写字（透明）的地方露出下层。但「叠」不是把整层粗暴替换，而是**逐项合并**（deep-merge）：上层写了的用上层的，上层没提的保留下层的，遇到「设置里还套设置」就钻进去接着逐项合。所以一个 profile 只想改沙箱、没提模型时，模型就自动沿用下层的值、不会被抹掉。这一层「谁能盖谁」的规则，正是企业能从最上面压一道「这台机器只能 read-only」的底气所在。

把这三点连起来：**`config.toml` 里写着若干 profile；选中一个后，它和系统默认、命令行覆盖一起，经 deep-merge 按「后层盖前层」分层解析成「这次真正生效的一份配置」。** 一个开关（profile 名）就切换一整套自主度——prod 上谨慎、草稿仓里放飞。这一章就用 30 行 Python，把这套机制连同「每个字段到底是哪一层赢的」都摊给你看。

## 问题

到这一章，Codex 已经攒了一大堆可调项：用哪个模型、审批策略多严（[s04](../s04_approval/)）、沙箱开到哪一档（[s05](../s05_sandbox/)）、推理力度多大、挂哪些 MCP server（[s15](../s15_mcp/)）……

但「该用什么值」**强烈依赖你在哪、在干什么**：

- 在公司的 prod 仓库里替别人改代码：审批要严、沙箱要紧、模型要稳——出事代价高。
- 在自己 `/tmp` 下的一次性草稿仓里跑实验：别每条命令都问我、给我全权限、推理拉满——我要的是速度。

如果这些值散落在十几个命令行 flag 里，每次切换场景你都得敲一长串 `--approval … --sandbox … --model …`，记不住也容易敲错。更糟的是：值可能来自多个地方——你写的 `config.toml`、企业下发的策略、这次启动临时加的 `-c`——**到底哪个生效？** 没有清晰的优先级规则，配置就是一团乱麻。

## 解决方案

两件事：**命名 profile**把「一整套场景配置」打包成一个名字；**分层解析**用一条明确的优先级链把多个来源合成一份生效配置——**后面的层覆盖前面的层（last-wins）**。

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

切场景从此只要一个名字：`--profile safe` 或 `--profile yolo`。

## 工作原理

看 [code.py](code.py)，三块：

**第 1 步 — 零依赖地拿到 config。** 优先用标准库 `tomllib`（3.11+）读 `config.toml`；环境没有它（或没有文件）就退回一个内嵌的 sample dict——**绝不引入 pip 依赖**：

```python
try:
    import tomllib            # Python 3.11+
except ImportError:
    tomllib = None            # 退回内嵌 SAMPLE_CONFIG
```

`SAMPLE_CONFIG` 的形状刻意对齐真源码的 `ConfigToml`：顶层默认 + 一个 `profiles` 映射 + 一个 `profile`（默认选谁）。两个 profile 的字段取值也对齐真枚举——`approval_policy ∈ {untrusted, on-failure, on-request, never}`（`AskForApproval`）、`sandbox_mode ∈ {read-only, workspace-write, danger-full-access}`（`SandboxMode`）。

**第 2 步 — deep-merge：overlay 赢。** 这正是 [`merge.rs`](../../codex/codex-rs/config/src/merge.rs) 里 `merge_toml_values` 的语义——两边都是表就递归合并，否则 overlay 整个盖掉：

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

**第 3 步 — `resolve()` 按四层叠加，并记录每个字段「哪层赢了」。** 这个 provenance（来源追踪）是本章的看点——它让「last-wins」从抽象规则变成肉眼可见：

```python
eff = dict(DEFAULTS)                              # 第0层：系统默认
eff = lay(eff, top, "config.toml")               # 第1层：config.toml 顶层
eff = lay(eff, prof, f"profile:{chosen}")        # 第2层：选中的 profile
eff = lay(eff, overrides, "override")            # 第3层：运行时覆盖（最高）
```

`--demo` 解析 `safe` 和 `yolo` 两个 profile，逐字段打印「生效值 + 来自哪一层」，并加一块「yolo + 运行时覆盖 model」演示最高层如何盖掉 profile 给的值（last-wins）。`PROFILE_FIELDS` 限定 profile 只能覆盖它该覆盖的字段——对应真版 `ConfigProfile` 上的 `#[serde(deny_unknown_fields)]`。

**走一遍**：跟着一次 `resolve(cfg, "yolo", overrides={"model": "gpt-5-codex-mini"})` 看「四层胶片」怎么一张张叠出最终配置。我们盯住一个字段 `model`，看它在每一层被写成什么、最后谁赢。

第 0 层 **DEFAULTS**（系统默认，最底下那张胶片）——所有字段都有个保底值：

```python
{"model": "gpt-5-codex", "approval_policy": "on-request",
 "sandbox_mode": "read-only", "model_reasoning_effort": "medium"}
# provenance: 此刻每个字段都记成 "default"
```

第 1 层 **config.toml 顶层**——`SAMPLE_CONFIG` 的顶层只写了一个 `model`（把默认模型升一档），别的没提：

```python
overlay = {"model": "gpt-5-codex-high"}     # 只有这一个字段
# deep-merge 后 model 变成 gpt-5-codex-high；其余沿用第 0 层（胶片没盖到的地方露出下层）
# provenance["model"] = "config.toml"
```

第 2 层 **profile = yolo**——这是 `yolo` 这张胶片写的内容（注意它**没写** `model`）：

```python
{"approval_policy": "never", "sandbox_mode": "danger-full-access",
 "model_reasoning_effort": "high"}
# 它改了 approval / sandbox / reasoning 三项；model 这一格是透明的 → 仍露出第 1 层的 gpt-5-codex-high
# provenance: approval_policy→"profile:yolo", sandbox_mode→"profile:yolo", model_reasoning_effort→"profile:yolo"
```

第 3 层 **运行时覆盖**（最顶上那张，优先级最高）——我们这次启动临时传了 `{"model": "gpt-5-codex-mini"}`：

```python
{"model": "gpt-5-codex-mini"}
# model 被这张最高层胶片盖成 gpt-5-codex-mini —— 第 1 层写的 high 被压在下面看不见了
# provenance["model"] = "override"   ← model 的归属从 config.toml 变成了 override
```

**为什么结果是这样**：`model` 一路被写了三次（default→config.toml→override），但 provenance 最后记的是 `override`，因为它是最高层、最后一个动这个字段的——这就是 last-wins。而 `approval_policy` 只有 `yolo` 那层写过，所以归 `profile:yolo`。最终 `--demo` 第 ③ 块打印出来的就是：

```
字段                    生效值                来自哪一层
model                   gpt-5-codex-mini      override        ← 被运行时覆盖盖掉
approval_policy         never                 profile:yolo
sandbox_mode            danger-full-access    profile:yolo
model_reasoning_effort  high                  profile:yolo
```

看懂这张表，你就看懂了整套分层配置：**每个字段独立地「往上数到最后写它的那一层」，那层的值就是生效值。**

## 生产级：在边界上校验——坏配置在 load 时就拒，不拖到运行时

分层解析很优雅，但它有个隐患：如果一个 profile 写了 `approval_policy = "yolo-mode"`（一个不存在的档位）、或把字段名拼成 `sandbox_modee`，会怎样？一个宽松的解析器会**默默接受**——然后你的 agent 带着一个谁也不认识的审批策略上路，等到运行时行为诡异了才发现。生产级的答案是：**在加载的边界上严格校验，坏配置当场拒绝、报清楚错在哪。**

真 Codex 用两件武器（都在类型层面）：

- **`#[serde(deny_unknown_fields)]`**：拼错的字段名（`sandbox_modee`）不会被悄悄忽略，而是直接 parse 失败。
- **typed enum**：`approval_policy` 的类型就是 `AskForApproval` 枚举，`"yolo-mode"` 根本反序列化不进去——非法值在 load 时就被挡掉。

本章 `validate_profile` 把这套教学化，`--demo` 拿一个三处都错的 profile 演示：

```
✗ profile 'typo': `approval_policy`='yolo-mode' 非法（合法：['never','on-failure','on-request','untrusted']）
✗ profile 'typo': 未知字段 `sandbox_modee`（合法：['approval_policy','model','model_reasoning_effort','sandbox_mode']）
✗ profile 'typo': `model_reasoning_effort`='extreme' 非法（合法：['high','low','medium']）
```

> 一句话：配置是**安全档位的总开关**（它决定审批用哪档、沙箱开多大）——正因为它这么要害，越要在边界上把它焊死：**宁可加载就失败，也不要带着一个错误的安全配置去跑。** 这和沙箱的 deny-default、审批的 fail-closed 是同一种"出错就倒向安全"的工程直觉。

## 🆚 与 Claude Code 的不同

| | Claude Code | Codex |
|---|---|---|
| 配置文件 | `settings.json`（JSON） | `config.toml`（TOML）⭐ |
| 切场景 | 改 settings / 命令行参数 | **命名 profile**：一个名字打包一整套（model + 审批 + 沙箱 + …） |
| 来源合成 | 设置项合并 | **显式分层栈**：system → cloud → user → thread，逐层 last-wins |
| 一键切自主度 | 较分散 | `--profile safe` / `--profile yolo` 一秒切换谨慎 / 放飞 |

**为什么 Codex 要搞这么一套分层 + profile？** 因为它的运行场景比「单人本地交互」宽得多——同一套 core 要跑在本地终端、CI、云端，还可能受企业策略约束。这种环境天然就是**多来源**的：

- **企业要能从上面压一层**：管理员下发的策略（真版的 system / cloud 层）必须能覆盖用户的随手设置，否则在受管环境里就没法强制「这台机器只能 read-only」。分层栈给了这个能力。
- **自主度要能一键切换**：Codex 把宝押在低人工干预的自主运行上，而「该多自主」高度依赖场景。profile 把 `approval + sandbox + model` 这组「自主度旋钮」捆成一个名字——prod 用 `safe`、草稿仓用 `yolo`，不用每次手动拼一长串 flag，也不会切错一半。
- **运行时还要能临时压一层**：这次启动想换个模型？`-c model=…` 作为最高层盖上去，不动你的 `config.toml`。

这与全课主线一致：Claude Code 围绕**交互式 UX**，配置够用就行、临场点弹窗；Codex 为 **headless / CI / 云**下注，所以把「谁能覆盖谁」做成一套**显式、可被企业接管、可一键切自主度**的分层系统。

## 深入：教学版 vs 真 Codex 源码

教学版 `resolve()` 大约 30 行、四个层。真 Codex 的 config 子系统是 `codex-rs/config/`（仅 `types.rs` 就 35000+ 行，`config_requirements.rs` 12 万行），下面四块讲清差距。

<details>
<summary>一、真实层栈：system → cloud → user → thread，外加企业 MDM</summary>

教学版的四层（DEFAULTS → config.toml → profile → override）是真版层栈的简化。真版的 `ConfigLayerStack`（`config/src/state.rs:236`）注释写得很直白：

```rust
/// Layers are listed from lowest precedence (base) to highest (top),
/// so later entries in the Vec override earlier ones.   ← 和教学版 last-wins 完全一致
layers: Vec<ConfigLayerEntry>,
```

每个层有个来源标签 `ConfigLayerSource`（`state.rs`）：

| 层（低→高优先级） | `ConfigLayerSource` 变体 | 谁写的 |
|---|---|---|
| 企业 MDM / 受管 | `Mdm` / `EnterpriseManaged` | 设备管理策略，用户**不能**覆盖 |
| 系统 | `System { file }` | 系统级 config |
| 云下发 | （`cloud_config_layers`） | Codex Web / 组织下发的 fragment |
| 用户 | `User { file, profile }` | 你的 `$CODEX_HOME/config.toml`（**含 profile 子层**） |
| 项目 | `Project { dot_codex_folder }` | 仓库里的 `.codex/` |
| 会话 flags | `SessionFlags` | 这次启动的 `-c` / CLI 覆盖（最高） |

注意 `User` 层里其实**还分两小层**：基础 `config.toml` + 选中 profile 的 override（所以代码里要记一个 `user_layer_index` 指向可写的那层）。教学版把「config.toml 顶层」和「profile」拆成两层正是模仿这个。`merge_toml_values` 则是逐层合并时真正干活的函数（教学版 `deep_merge` 照搬了它）。

</details>

<details>
<summary>二、profile 到底能装什么：ConfigProfile 的字段全景</summary>

教学版 `PROFILE_FIELDS` 只放了 4 个字段（model / approval / sandbox / reasoning_effort）。真版 `ConfigProfile`（`config/src/profile_toml.rs:24`）能打包的远不止：

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

每个字段都是 `Option<T>`：**`None` 表示「这个 profile 不管这一项」**，于是合并时该字段沿用下层的值——这正是教学版 `lay()` 里「只把 layer 真正写了的字段记进 provenance」想表达的语义。`#[serde(deny_unknown_fields)]` 则保证你不能在 profile 里塞乱七八糟的 key（教学版用 `PROFILE_FIELDS` 白名单模拟）。

</details>

<details>
<summary>三、sandbox + approval + model + mcp_servers 全都活在 config 里</summary>

值得专门点出：前面几章那些机制的「配置入口」最终都汇进 `ConfigToml`（`config/src/config_toml.rs:139`）这一个结构：

| 配置项 | 字段 | 对应章节 |
|---|---|---|
| 用哪个模型 | `model: Option<String>` | s09 |
| 审批策略 | `approval_policy: Option<AskForApproval>` | s04 |
| 沙箱档位 | `sandbox_mode: Option<SandboxMode>` | s05 |
| 权限 profile | `permissions` / `permission_profile` | s05 / s14 |
| MCP server | `mcp_servers: HashMap<String, McpServerConfig>` | s15 |
| 命名 profile | `profiles: HashMap<String, ConfigProfile>` | 本章 |
| 默认选哪个 profile | `profile: Option<String>` | 本章 |

也就是说：**config 不是某个孤立模块的设置，而是整个 agent 行为的总线**。教学版只演示了 model / approval / sandbox / reasoning 四个字段的层叠，但同一套分层规则对 `mcp_servers` 这类嵌套表一样适用（`deep_merge` 会递归进去合并）——这也是为什么真版要费力实现「表递归合并」而不是简单的整体替换。

</details>

<details>
<summary>四、ThreadSettings：运行时覆盖配置，且不重启（接 s10）</summary>

教学版的「第 3 层 override」是个静态 dict。真版里，运行时改配置是一条 **`Op`**（接 [s10](../s10_sq_eq_protocol/) 的 SQ/EQ 协议，`protocol.rs:492`）：

```rust
Op::ThreadSettings {
    thread_settings: ThreadSettingsOverrides,   // 改 model / 审批 / 沙箱…
}
// core 应用后回一个事件确认：
EventMsg::ThreadSettingsApplied(ThreadSettingsAppliedEvent)
```

这就是 thread 层（最高优先级）的真身：你在会话**进行到一半**时切了模型或放宽了审批，前端提交一个 `Op::ThreadSettings`，core 把它当作最高层合并进生效配置、回一个 `ThreadSettingsApplied` 事件，**不用重启会话**。教学版没有队列，所以把它压扁成「传一个 `overrides` dict 给 `resolve()`」——但优先级位置（盖过 profile 和 config.toml）和真版一致。

一句话串起来：**config.toml + profile 定的是「会话开始时的初值」，`Op::ThreadSettings` 是「会话中途的临时改写」**，两者落在同一条分层栈的不同高度上。

</details>

## 运行

```bash
python s16_config/code.py --demo   # 解析 safe / yolo，逐字段打印「哪层赢了」（无需 key，离线）
python s16_config/code.py          # 交互模式：输入 profile 名，看生效配置
```

无外部依赖：用标准库 `tomllib`（3.11+）；环境没有它就自动退回内嵌 sample 配置。

## 小结

- 配置来自多个来源，用一条**显式优先级链**合成：DEFAULTS → config.toml → profile → 运行时覆盖，**后层覆盖前层（last-wins）**，靠 deep-merge 逐层叠加。
- **命名 profile**把「一整套自主度配置」打包成一个名字——`safe`（要审批 + 工作区写）/ `yolo`（不审批 + 全权限）一秒切换。
- 真 Codex 的层栈更厚：**system / 企业 MDM → cloud → user(config.toml + profile) → project → session flags**；`ConfigProfile` 能打包十几个字段；`mcp_servers`、`approval`、`sandbox`、`model` 全都汇进同一个 `ConfigToml`。
- 运行时改配置是一条 `Op::ThreadSettings`（接 [s10](../s10_sq_eq_protocol/)），落在层栈最高处、不用重启。
- **生产级**：配置是安全档位的总开关，必须在 load 边界上严格校验——`deny_unknown_fields`（拼错字段直接报错）+ typed enum（非法枚举值反序列化失败），坏配置当场拒，绝不默默生效（见「生产级」一节）。
- 下一站 [s17](../s17_comprehensive/)：把前 16 章的机制拼成一个完整的迷你 Codex。

## 思考

1. 教学版「后层覆盖前层」简单明了。但真版里企业 MDM 层是**最低**优先级之一却又「用户不能覆盖」——这两件事看似矛盾，Codex 是怎么做到「企业策略既参与合并、又不被用户推翻」的？如果让你设计，约束（不可覆盖）和默认值（可覆盖）该不该用同一套分层机制表达？

2. profile 把 `approval + sandbox + model` 捆成一个名字，切换很爽。但「捆绑」也意味着你可能**只想改沙箱、却顺手把审批也换了**。`yolo` 这种「不审批 + 全权限」的预设一旦手滑用在了真实仓库……这个便利性 vs 安全性的张力，你会怎么兜底——要不要给危险 profile 加二次确认？

3. 本章把 TOML 优先用 `tomllib`、退回内嵌 dict，是为了零依赖。可真版的 config 子系统庞大到 `config_requirements.rs` 有 12 万行——一个「读配置」的事为什么会膨胀成这样？当配置要支持「企业下发 + 云同步 + 运行时改写 + 严格校验」时，复杂度都花在了哪？

4. Claude Code 用 `settings.json`、Codex 用 `config.toml` + 分层 + profile。同样是「让用户配置 agent」，一个偏轻量、一个偏体系化——这差异多大程度上是「JSON vs TOML」的口味问题，多大程度上是「单一交互前端 vs headless/CI/云多前端」逼出来的必然？换成你做一个面向 CI 的 agent，会怎么设计配置系统？
