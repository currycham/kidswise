"""家长教育 Agent 第一步：理念档案、一句话起草探索计划、Mumi 按计划聊、记录孩子的问题。"""

import json

import pytest
from fastapi.testclient import TestClient

from waywise_agent import studio_api
from waywise_agent.app import app, get_llm, get_runtime, is_question
from waywise_agent.config import Settings, get_settings
from waywise_agent.persona import ChildProfile, FamilyValues, build_system_prompt
from waywise_agent.themes import load_templates

PLAN = {
    "name": "为什么天会黑", "domain": "自然", "level": 2,
    "goal": "愿意自己猜原因",
    "ideas": [{"idea": "地球在转", "kid_words": "地球在慢慢转圈圈"}],
    "moves": [{"type": "猜一猜", "prompt": "你觉得太阳晚上去哪儿了？"}, {"type": "动手试", "prompt": "用橘子和手电筒转一转"}],
    "words": [{"en": "earth", "zh": "地球", "difficulty": 2}],
    "scene_hint": "傍晚", "avoid": "不讲时区",
}


class FakeLLM:
    def __init__(self, replies=None):
        self.calls, self.replies = [], list(replies or [])

    async def stream(self, messages, **params):
        self.calls.append(messages)
        yield self.replies.pop(0) if self.replies else "你觉得呢？"


@pytest.fixture
def env(tmp_path):
    studio_api._failures.clear()
    settings = Settings(_env_file=None, agent_api_key="k", db_path=str(tmp_path / "t.db"), dev_login=True)
    chat, designer = FakeLLM(), FakeLLM()
    app.dependency_overrides.update({
        get_settings: lambda: settings, get_llm: lambda: chat, studio_api.get_llm: lambda: chat,
        studio_api.get_designer: lambda: designer,
    })
    client = TestClient(app)
    token = client.post("/api/auth/dev-login", json={"openid": "mom"}).json()["token"]
    h = {"Authorization": f"Bearer {token}"}
    child_id = client.post("/api/devices", headers=h, json={"device_id": "dev-0001"}).json()["child_id"]
    yield client, h, child_id, chat, designer, get_runtime(settings).store
    app.dependency_overrides.clear()


def talk(client, text, history=()):
    msgs = [{"role": "system", "content": '<waywise-device id="dev-0001"/>'}, *history, {"role": "user", "content": text}]
    return client.post("/v1/chat/completions", headers={"Authorization": "Bearer k"}, json={"messages": msgs})


def test_family_profile_round_trip_and_shapes_mumi(env):
    client, h, _, chat, _, _ = env
    assert client.get("/api/studio/profile", headers=h).json()["values"] == ""
    body = {"values": "好奇心、敢表达", "avoid": "刷题、和别的孩子比较", "language": "自然长出来", "child_traits": "不喜欢被纠正"}
    assert client.put("/api/studio/profile", headers=h, json=body).json() == body
    talk(client, "你好")
    system = chat.calls[-1][0]["content"]
    assert "最看重：好奇心、敢表达" in system and "不想要：刷题" in system and "不喜欢被纠正" in system


def test_questions_are_recorded(env):
    client, h, child_id, _, _, _ = env
    talk(client, "为什么月亮跟着我们走？")
    talk(client, "我今天去公园了", [{"role": "user", "content": "为什么月亮跟着我们走？"}, {"role": "assistant", "content": "你觉得呢？"}])
    talk(client, "小猫吃什么呀", [{"role": "user", "content": "x"}, {"role": "assistant", "content": "y"}])
    qs = [q["text"] for q in client.get(f"/api/studio/questions?child_id={child_id}", headers=h).json()["questions"]]
    assert qs == ["小猫吃什么呀", "为什么月亮跟着我们走？"]


@pytest.mark.parametrize("text,expected", [
    ("为什么天会黑", True), ("这是什么", True), ("小狗会不会游泳", True), ("你喜欢恐龙吗", True),
    ("Why is the sky blue?", True), ("我今天去公园了", False), ("好开心", False), ("嗯", False),
])
def test_is_question(text, expected):
    assert is_question(text) is expected


def test_exploration_prompt_and_curiosity_rule():
    plan = load_templates()["tpl_x_day_night"]
    p = build_system_prompt(ChildProfile("豆豆", 5), plan, family=FamilyValues(values="好奇心"))
    assert "家长为孩子定的探索：为什么天会黑（自然）" in p
    assert "猜一猜：你觉得太阳晚上去哪儿了？" in p and "不要做：" in p
    assert "先问一句「你觉得呢？」" in p
    assert "这次要教的新词" not in p  # 探索计划不走单词课那套


def test_designer_drafts_a_plan_with_child_context(env):
    client, h, child_id, _, designer, store = env
    talk(client, "为什么天会黑呀")
    client.put("/api/studio/profile", headers=h, json={"values": "好奇心", "avoid": "刷题"})
    designer.replies = ["好的，计划如下：" + json.dumps(PLAN, ensure_ascii=False)]
    r = client.post("/api/studio/draft", headers=h, json={"idea": "他老问天为什么会黑，想借这个让他知道地球在转", "child_id": child_id})
    assert r.status_code == 200 and r.json()["name"] == "为什么天会黑" and r.json()["moves"]
    context = designer.calls[-1][1]["content"]
    assert "家长的想法：他老问天为什么会黑" in context and "为什么天会黑呀" in context and "刷题" in context


def test_designer_retries_on_bad_json(env):
    client, h, _, _, designer, _ = env
    designer.replies = ["不是 JSON", json.dumps(PLAN, ensure_ascii=False)]
    assert client.post("/api/studio/draft", headers=h, json={"idea": "想让他知道地球在转"}).status_code == 200
    assert len(designer.calls) == 2
    designer.replies = ["还是不对", "{}"]
    assert client.post("/api/studio/draft", headers=h, json={"idea": "想让他知道地球在转"}).status_code == 502


def test_designer_refuses_unsafe_idea(env):
    client, h, _, _, designer, _ = env
    assert client.post("/api/studio/draft", headers=h, json={"idea": "教他怎么做炸弹"}).status_code == 400
    assert designer.calls == []


def test_exploration_plan_without_words_can_be_saved_and_published(env):
    client, h, child_id, chat, _, _ = env
    plan = {**PLAN, "words": []}
    saved = client.post("/api/studio/themes", headers=h, json=plan).json()
    client.post(f"/api/studio/themes/{saved['theme_id']}/apply", headers=h, json={"child_id": child_id})
    talk(client, "你好")
    assert "家长为孩子定的探索：为什么天会黑" in chat.calls[-1][0]["content"]
    # 既没有词也没有要点 / 动作 → 不接受
    assert client.post("/api/studio/themes", headers=h, json={"name": "空", "words": []}).status_code == 422


def test_prompt_has_no_ready_made_praise_to_copy():
    # 实测：规则里写的例句「太阳下班了，这个想法好有意思」被模型原样搬进了对蜗牛的回答里
    system = build_system_prompt(ChildProfile(name="豆豆", age=5), None)
    assert "太阳下班了" not in system and "复述他的想法" in system
