"""config —— 分层配置解析 + 边界校验（s16）。"""

from .loader import DEFAULTS, ResolvedConfig, load_config, resolve_config

__all__ = ["DEFAULTS", "ResolvedConfig", "load_config", "resolve_config"]
