import pytest
from pydantic import ValidationError

from waywise_agent.persona import ChildProfile, build_system_prompt
from waywise_agent.themes import Theme, load_themes

EXPECTED = {
    "park", "supermarket", "zoo", "playground", "neighborhood",
    "mealtime", "dressing", "brushing", "greetings", "colors_numbers",
}


def test_all_ten_themes_load():
    assert set(load_themes()) == EXPECTED


@pytest.mark.parametrize("theme", load_themes().values(), ids=lambda t: t.theme_id)
def test_theme_is_well_formed(theme):
    ens = [w.en.lower() for w in theme.words]
    assert len(ens) == len(set(ens)), "duplicate words"
    assert 10 <= len(theme.words) <= 20
    assert any(w.difficulty == 1 for w in theme.words), "needs an easy entry word"
    assert theme.patterns and theme.scene_hint


def test_rejects_unknown_placeholder():
    with pytest.raises(ValidationError):
        Theme(theme_id="x", name="x", level=1, words=[{"en": "a", "zh": "a", "difficulty": 1}], patterns=["Hi {name}"])


def test_prompt_includes_theme_words():
    prompt = build_system_prompt(ChildProfile(name="豆豆", age=5), load_themes()["zoo"])
    assert "动物园" in prompt and "elephant（大象）" in prompt
    # easier words come before harder ones
    assert prompt.index("elephant") < prompt.index("kangaroo")
