#!/usr/bin/env python3
"""
s16: Config — 分层解析 config.toml + 命名 profile（最后写入者胜）。

运行:
  python s16_config/code.py --demo    # 解析 "safe" / "yolo" 两个 profile，逐字段打印「哪层赢了」
  python s16_config/code.py           # 交互模式：输入 profile 名，看生效配置

本章 = s01 的回合循环骨架（搬运一个最小 run_turn 兜底）
     + 新增：分层配置解析。defaults → config.toml → 选中的命名 profile → 运行时覆盖，
       逐层 deep-merge，**后面的层覆盖前面的层**（last-wins）。

为什么要 profile？一条命令就能切「自主度套餐」：prod 上用谨慎的 safe（要审批 + 沙箱），
临时草稿仓里用放飞的 yolo（不审批 + 全权限）。见 README「🆚」。

零依赖：优先用标准库 tomllib（Python 3.11+）解析 config.toml；解析不到就退回
内嵌的 sample 配置 dict（绝不引入 pip 依赖）。
忠实对应 codex-rs/config/src/（ConfigToml / ConfigProfile / 分层 loader）。
"""

import sys
from pathlib import Path

# 仓库根目录加入 import 路径，复用共享模块（交互兜底会用到）。
sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
from codexlib import Model, user_item  # noqa: E402

# 零依赖地拿 TOML：标准库 tomllib（3.11+）。拿不到就用内嵌 sample（绝不 pip install）。
try:
    import tomllib  # Python 3.11+
except ImportError:  # 3.10 及更早：退回内嵌 sample 配置
    tomllib = None


# ═══════════════════════════════════════════════════════════
#  NEW in s16：分层配置 —— 四个层，从低到高优先级
#
#  对应 codex-rs 的真实层栈（precedence 从低到高）：
#      system 默认  →  cloud（云下发）  →  user（config.toml + profile）  →  thread（运行时覆盖）
#  教学版取其骨架：
#      DEFAULTS（系统默认）→ config.toml 顶层 → 选中的 profile → runtime 覆盖
#  规则：后面的层覆盖前面的层（last-wins），即 codex-rs 的 merge_toml_values 语义。
# ═══════════════════════════════════════════════════════════

# 第 0 层：系统默认（最低优先级）。字段名 / 取值刻意对齐真源码：
#   approval_policy ∈ {untrusted, on-failure, on-request, never}  (AskForApproval)
#   sandbox_mode    ∈ {read-only, workspace-write, danger-full-access}  (SandboxMode)
DEFAULTS = {
    "model": "gpt-5-codex",
    "approval_policy": "on-request",
    "sandbox_mode": "read-only",
    "model_reasoning_effort": "medium",
}

# 内嵌 sample 配置：当环境没有 tomllib（或没有 config.toml）时的兜底。
# 形状刻意对齐 ConfigToml：顶层默认 + profiles 映射 + profile（默认选哪个）。
SAMPLE_CONFIG = {
    # config.toml 顶层（覆盖 DEFAULTS）——比如把默认模型升一档。
    "model": "gpt-5-codex-high",
    "profile": "safe",  # 不指定 --profile 时默认用谁
    "profiles": {
        # safe：prod / 别人的仓库——要审批，写也只在工作区。
        "safe": {
            "approval_policy": "untrusted",     # 任何不在信任名单里的命令都要批
            "sandbox_mode": "workspace-write",
        },
        # yolo：你自己的一次性草稿仓——别问我，放手跑。
        "yolo": {
            "approval_policy": "never",         # 永不打断
            "sandbox_mode": "danger-full-access",
            "model_reasoning_effort": "high",   # 顺便把推理力度拉满
        },
    },
}


def load_config_toml() -> dict:
    """优先读同目录 config.toml（用 tomllib）；读不到就用内嵌 SAMPLE_CONFIG。零依赖。"""
    cfg_path = Path(__file__).resolve().parent / "config.toml"
    if tomllib is not None and cfg_path.exists():
        with cfg_path.open("rb") as f:
            return tomllib.load(f)
    return SAMPLE_CONFIG


def deep_merge(base: dict, overlay: dict) -> dict:
    """把 overlay 合并进 base，overlay 赢（last-wins）。嵌套表递归合并。

    这正是 codex-rs/config/src/merge.rs 的 merge_toml_values 语义：
    两边都是 table 就递归；否则 overlay 整个覆盖 base。
    """
    out = dict(base)
    for key, val in overlay.items():
        if isinstance(val, dict) and isinstance(out.get(key), dict):
            out[key] = deep_merge(out[key], val)
        else:
            out[key] = val          # 标量 / 列表：overlay 直接盖掉
    return out


# profile 里允许出现的字段（对应 ConfigProfile 的子集）。profile 只能覆盖这些，
# 不能凭空塞 profiles / 顶层无关键。真版用 #[serde(deny_unknown_fields)] 强约束。
PROFILE_FIELDS = {"model", "approval_policy", "sandbox_mode", "model_reasoning_effort"}

# 生产级：各枚举字段的合法取值（对应真源码的 typed enum）。非法值/未知字段应在**加载时**
# 就被拒，而不是默默生效、到运行时才以诡异行为爆出来。
VALID_VALUES = {
    "approval_policy": {"untrusted", "on-failure", "on-request", "never"},
    "sandbox_mode": {"read-only", "workspace-write", "danger-full-access"},
    "model_reasoning_effort": {"low", "medium", "high"},
}


def validate_profile(name: str, prof: dict) -> list[str]:
    """在边界上校验一个 profile，返回所有错误（空=通过）。对应真源码
    `#[serde(deny_unknown_fields)]` + typed enum：坏配置在 load 时就拒。"""
    errors = []
    for k, v in prof.items():
        if k not in PROFILE_FIELDS:                          # deny_unknown_fields
            errors.append(f"profile '{name}': 未知字段 `{k}`（合法：{sorted(PROFILE_FIELDS)}）")
        elif k in VALID_VALUES and v not in VALID_VALUES[k]:  # 枚举值校验
            errors.append(f"profile '{name}': `{k}`={v!r} 非法（合法：{sorted(VALID_VALUES[k])}）")
    return errors


def resolve(config_toml: dict, profile_name: str | None,
            overrides: dict | None = None) -> tuple[dict, dict]:
    """分层解析出生效配置，并记录每个字段「最终由哪一层定的」。

    返回 (effective, provenance)：
      effective  : 生效配置 dict
      provenance : 字段 → 是哪一层赢的（default / config.toml / profile:<name> / override）
    """
    overrides = overrides or {}
    provenance: dict[str, str] = {}

    def lay(cfg: dict, layer: dict, label: str) -> dict:
        # 合并一层，并把这层真正改动到的字段记进 provenance。
        for k in layer:
            if k in PROFILE_FIELDS or k in DEFAULTS:
                provenance[k] = label
        return deep_merge(cfg, layer)

    # 第 0 层：系统默认。
    eff = dict(DEFAULTS)
    for k in eff:
        provenance[k] = "default"

    # 第 1 层：config.toml 顶层（剔掉 profiles / profile 这两个「元」字段）。
    top = {k: v for k, v in config_toml.items() if k not in ("profiles", "profile")}
    eff = lay(eff, top, "config.toml")

    # 第 2 层：选中的命名 profile（没传就用 config.toml 里的默认 profile）。
    chosen = profile_name or config_toml.get("profile")
    profiles = config_toml.get("profiles", {})
    if chosen:
        if chosen not in profiles:
            raise KeyError(f"未知 profile: {chosen!r}（可选：{sorted(profiles)}）")
        prof = {k: v for k, v in profiles[chosen].items() if k in PROFILE_FIELDS}
        eff = lay(eff, prof, f"profile:{chosen}")

    # 第 3 层：运行时覆盖（最高优先级，比如 CLI 的 -c / --model）。
    eff = lay(eff, overrides, "override")

    return eff, provenance


# ═══════════════════════════════════════════════════════════
#  FROM s01（搬运）：最小回合循环，仅供交互兜底用一次
#  （本章重点是配置，不是循环——这里只把生效配置塞进 system 跑一轮）
# ═══════════════════════════════════════════════════════════

def run_one_turn(query: str, effective: dict) -> None:
    model = Model()
    system = (f"You are Codex. Effective config: model={effective['model']}, "
              f"approval={effective['approval_policy']}, "
              f"sandbox={effective['sandbox_mode']}. Answer briefly.")
    resp = model.respond([user_item(query)], tools=[], system=system)
    print(f"\n\033[32m{resp.text}\033[0m")


# ═══════════════════════════════════════════════════════════
#  --demo：解析 safe / yolo 两个 profile，逐字段打印「哪层赢了」
# ═══════════════════════════════════════════════════════════

def _print_resolution(title: str, eff: dict, prov: dict) -> None:
    print(f"\n\033[36m── {title} ──\033[0m")
    print(f"  {'字段':<24}{'生效值':<22}来自哪一层")
    print("  " + "-" * 60)
    for key in ("model", "approval_policy", "sandbox_mode", "model_reasoning_effort"):
        print(f"  {key:<24}{str(eff.get(key)):<22}{prov.get(key, '-')}")


def demo_validation() -> None:
    print("\n生产级：配置在边界上校验——坏配置在 load 时就拒，不拖到运行时")
    bad = {"approval_policy": "yolo-mode",        # 非法枚举值（应是 never/on-request/…）
           "sandbox_modee": "workspace-write",    # 拼错的字段名（unknown field）
           "model_reasoning_effort": "extreme"}   # 非法枚举值
    print("  校验一个有三处错误的 profile：")
    for e in validate_profile("typo", bad):
        print("   ✗", e)
    print("  → 真 Codex 里这样的 config 会让加载**直接失败**（deny_unknown_fields + typed enum），")
    print("    而不是默默用一个谁也不认识的 approval_policy 去跑。")


def demo() -> None:
    cfg = load_config_toml()
    src = "config.toml" if (tomllib and (Path(__file__).resolve().parent /
                            "config.toml").exists()) else "内嵌 SAMPLE_CONFIG"
    print(f"配置来源：{src}（tomllib {'可用' if tomllib else '不可用，已退回内嵌'}）")
    print("层叠顺序（低→高优先级）：DEFAULTS → config.toml 顶层 → 选中 profile → 运行时覆盖")

    # ① 选 safe profile。
    eff, prov = resolve(cfg, "safe")
    _print_resolution("profile = safe（prod / 别人的仓库：要审批 + 工作区写）", eff, prov)

    # ② 选 yolo profile。
    eff, prov = resolve(cfg, "yolo")
    _print_resolution("profile = yolo（一次性草稿仓：不审批 + 全权限）", eff, prov)

    # ③ yolo 之上再叠一个运行时覆盖：临时把模型换掉——演示最高层「最后写入者胜」。
    eff, prov = resolve(cfg, "yolo", overrides={"model": "gpt-5-codex-mini"})
    _print_resolution("profile = yolo + 运行时覆盖 model（override 是最高层，赢）",
                      eff, prov)
    print("\n✓ 注意第 ③ 块的 model：profile 给的值被运行时 override 盖掉了——last-wins。")
    demo_validation()


if __name__ == "__main__":
    if "--demo" in sys.argv:
        demo()
        sys.exit(0)

    cfg = load_config_toml()
    avail = sorted(cfg.get("profiles", {}))
    print(f"s16: Config — 输入 profile 名解析生效配置（可选：{avail}；q 退出）\n")
    while True:
        try:
            name = input("\033[36ms16 profile >> \033[0m")
        except (EOFError, KeyboardInterrupt):
            break
        if name.strip().lower() in ("q", "exit", ""):
            break
        try:
            eff, prov = resolve(cfg, name.strip())
        except KeyError as e:
            print(f"\033[31m{e}\033[0m")
            continue
        _print_resolution(f"profile = {name.strip()}", eff, prov)
        print()
