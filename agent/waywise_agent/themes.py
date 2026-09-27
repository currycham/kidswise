"""主题词表（软件文档 4.3）。同一个结构也是开发者平台的数据结构。"""

import re
from functools import lru_cache
from pathlib import Path

from pydantic import BaseModel, Field, field_validator

THEMES_DIR = Path(__file__).resolve().parent.parent / "data" / "themes"
TEMPLATES_DIR = Path(__file__).resolve().parent.parent / "data" / "templates"


class Word(BaseModel):
    en: str = Field(min_length=1)
    zh: str = Field(min_length=1)
    difficulty: int = Field(ge=1, le=3)


class Theme(BaseModel):
    theme_id: str = Field(pattern=r"^[a-z][a-z0-9_]*$")
    name: str
    level: int = Field(ge=1, le=3)
    words: list[Word] = Field(min_length=1)
    patterns: list[str] = []
    scene_hint: str = ""

    @field_validator("patterns")
    @classmethod
    def only_word_placeholder(cls, patterns: list[str]) -> list[str]:
        for p in patterns:
            if p.replace("{word}", "").count("{") or p.count("}") != p.count("{word}"):
                raise ValueError(f"pattern may only use {{word}} as placeholder: {p!r}")
        return patterns


@lru_cache
def load_themes(directory: Path = THEMES_DIR) -> dict[str, Theme]:
    themes = {}
    for path in sorted(directory.glob("*.json")):
        theme = Theme.model_validate_json(path.read_text(encoding="utf-8"))
        if theme.theme_id != path.stem:
            raise ValueError(f"{path.name}: theme_id {theme.theme_id!r} must match file name")
        themes[theme.theme_id] = theme
    return themes


def get_theme(theme_id: str) -> Theme | None:
    return load_themes().get(theme_id)


def load_templates() -> dict[str, Theme]:
    """开发者平台的十个模板（软件文档 7.2）：家长从模板改，比从空白开始完成率高一个量级。"""
    return load_themes(TEMPLATES_DIR)


def find_words(theme: Theme | None, text: str) -> list[str]:
    """文本里出现了哪些目标词（按英文匹配，不分大小写，整词，允许复数 s）。

    先看当前主题，再看其他主题：孩子自己聊到动物园的大象，也算碰到了 elephant。
    """
    lowered = text.lower()
    candidates = list(theme.words) if theme else []
    for other in load_themes().values():
        if theme is None or other.theme_id != theme.theme_id:
            candidates.extend(other.words)
    found: list[str] = []
    for w in candidates:
        if w.en not in found and re.search(rf"(?<![a-z']){re.escape(w.en.lower())}s?(?![a-z'])", lowered):
            found.append(w.en)
    return found
