"""skills —— 技能库：技能 = 一个 markdown 能力包，按需注入，省得每次塞满上下文。"""

from pathlib import Path

from .loader import SkillLibrary


def default_library() -> SkillLibrary:
    return SkillLibrary(Path(__file__).resolve().parent / "builtin")


__all__ = ["SkillLibrary", "default_library"]
