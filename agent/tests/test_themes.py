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


def test_prompt_teaches_one_new_word_at_a_time():
    zoo = load_themes()["zoo"]
    child = ChildProfile(name="豆豆", age=5)
    prompt = build_system_prompt(child, zoo)
    assert "动物园" in prompt
    assert "这次要教的新词（只教这一个）：elephant（大象）" in prompt
    assert "kangaroo" not in prompt  # 还没轮到的词不出现

    prompt = build_system_prompt(child, zoo, word_statuses={"elephant": "heard"})
    assert "已经学过的词（可以放心用来复习）：elephant（大象）" in prompt
    assert "只教这一个）：monkey（猴子）" in prompt
