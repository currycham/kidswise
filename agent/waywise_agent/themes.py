"""主题 / 探索计划（软件文档 4.3；家长教育 Agent 方案第三章）。同一个结构也是开发者平台的数据结构。

最早只有词表（words + patterns），只能教单词。现在扩展为探索计划：任何领域都可以，
有目标、要点（带孩子听得懂的说法）、探索动作；英文词变成可选的一层。内置的十个英语主题照旧可用。
"""

import re
from functools import lru_cache
from pathlib import Path

from pydantic import BaseModel, Field, field_validator, model_validator

THEMES_DIR = Path(__file__).resolve().parent.parent / "data" / "themes"
TEMPLATES_DIR = Path(__file__).resolve().parent.parent / "data" / "templates"


class Word(BaseModel):
    en: str = Field(min_length=1)
    zh: str = Field(min_length=1)
    difficulty: int = Field(default=1, ge=1, le=3)


class Idea(BaseModel):
    """一个要点：大人的说法 + 孩子听得懂的说法。"""

    idea: str = Field(min_length=1, max_length=60)
    kid_words: str = Field(default="", max_length=80)


class Move(BaseModel):
    """一个探索动作：猜一猜 / 找一找 / 数一数 / 动手试 / 连一连 / 讲给我听……"""

    type: str = Field(min_length=1, max_length=10)
    prompt: str = Field(min_length=1, max_length=100)


class Theme(BaseModel):
    theme_id: str = Field(pattern=r"^[a-z][a-z0-9_]*$")
    name: str
    level: int = Field(default=1, ge=1, le=3)
    domain: str = "英语"
    goal: str = ""
    ideas: list[Idea] = []
    moves: list[Move] = []
    words: list[Word] = []
    patterns: list[str] = []
    scene_hint: str = ""
    avoid: str = ""

    @model_validator(mode="after")
    def has_something_to_teach(self) -> "Theme":
        if not (self.words or self.ideas or self.moves):
            raise ValueError("至少要有要教的词、要点或探索动作中的一样")
        return self

    @property
    def is_exploration(self) -> bool:
        return bool(self.ideas or self.moves)

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
