"""主题词表（软件文档 4.3）。同一个结构也是开发者平台的数据结构。"""

from functools import lru_cache
from pathlib import Path

from pydantic import BaseModel, Field, field_validator

THEMES_DIR = Path(__file__).resolve().parent.parent / "data" / "themes"


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
