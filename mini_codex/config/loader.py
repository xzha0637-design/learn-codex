"""分层配置：defaults → config.toml → profile → 运行时覆盖（last-wins），并在边界上校验。

对照 s16。生产级要点：非法枚举值 / 未知字段在**加载时**就拒（deny_unknown_fields + typed enum），
绝不默默生效。
"""

from __future__ import annotations

from dataclasses import dataclass

DEFAULTS: dict = {
    "model": "gpt-5-codex",
    "approval_policy": "on-request",
    "sandbox_mode": "workspace-write",
    "model_reasoning_effort": "medium",
}

# 各枚举字段的合法取值（对应真源码的 typed enum）。
VALID_VALUES: dict[str, set] = {
    "approval_policy": {"untrusted", "on-failure", "on-request", "never"},
    "sandbox_mode": {"read-only", "workspace-write", "danger-full-access"},
    "model_reasoning_effort": {"low", "medium", "high"},
}

# 内嵌示例配置（零依赖；真版读 config.toml）。
SAMPLE_CONFIG: dict = {
    "profile": "safe",
    "profiles": {
        "safe": {"approval_policy": "untrusted", "sandbox_mode": "workspace-write"},
        "yolo": {"approval_policy": "never", "sandbox_mode": "danger-full-access"},
    },
}


@dataclass
class ResolvedConfig:
    model: str
    approval_policy: str
    sandbox_mode: str
    model_reasoning_effort: str
    provenance: dict           # 每个字段最终由哪一层定的（default/profile:<name>/override）


def _validate(label: str, layer: dict) -> None:
    for key, val in layer.items():
        if key not in DEFAULTS:
            raise ValueError(f"{label}: 未知字段 `{key}`（合法：{sorted(DEFAULTS)}）")
        if key in VALID_VALUES and val not in VALID_VALUES[key]:
            raise ValueError(f"{label}: `{key}`={val!r} 非法（合法：{sorted(VALID_VALUES[key])}）")


def resolve_config(config: dict, profile: str | None = None,
                   overrides: dict | None = None) -> ResolvedConfig:
    """逐层叠出生效配置，每层先校验。返回带 provenance 的 ResolvedConfig。"""
    overrides = overrides or {}
    eff = dict(DEFAULTS)
    prov = {k: "default" for k in eff}

    chosen = profile or config.get("profile")
    profiles = config.get("profiles", {})
    if chosen:
        if chosen not in profiles:
            raise KeyError(f"未知 profile: {chosen!r}（可选：{sorted(profiles)}）")
        layer = profiles[chosen]
        _validate(f"profile '{chosen}'", layer)
        for k, v in layer.items():
            eff[k] = v
            prov[k] = f"profile:{chosen}"

    _validate("override", overrides)
    for k, v in overrides.items():     # 运行时覆盖：最高层，last-wins
        eff[k] = v
        prov[k] = "override"

    return ResolvedConfig(provenance=prov, **eff)


def load_config() -> dict:
    """读取配置（这里返回内嵌示例；真版按零依赖 tomllib 读 config.toml）。"""
    return SAMPLE_CONFIG
