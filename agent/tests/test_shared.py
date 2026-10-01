"""别的家长在做什么：按孩子最近的问题匹配计划，借用并改成自己孩子的版本。"""

import json

import pytest
from fastapi.testclient import TestClient

from waywise_agent import studio_api
from waywise_agent.app import app, get_llm, get_runtime
from waywise_agent.config import Settings, get_settings
from waywise_agent.shared import load_shared

PLAN = {
    "name": "乐乐的下雨天探险", "domain": "自然", "level": 1,
    "goal": "愿意猜小动物怎么躲雨",
    "ideas": [{"idea": "蜗牛躲进壳里", "kid_words": "蜗牛钻进自己的房子"}],
    "moves": [{"type": "猜一猜", "prompt": "下雨了蜗牛躲在哪？"}, {"type": "找一找", "prompt": "雨停了去楼下找蜗牛"}],
    "words": [{"en": "snail", "zh": "蜗牛", "difficulty": 1}],
    "scene_hint": "下雨天", "avoid": "不徒手抓小动物",
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
    yield client, h, child_id, designer, get_runtime(settings).store
    app.dependency_overrides.clear()


def talk(client, text, history=()):
    msgs = [{"role": "system", "content": '<waywise-device id="dev-0001"/>'}, *history, {"role": "user", "content": text}]
    return client.post("/v1/chat/completions", headers={"Authorization": "Bearer k"}, json={"messages": msgs})


def test_seed_plans_are_valid_and_honest():
    plans = load_shared()
    assert len(plans) >= 8
    for sp in plans.values():
        assert sp.plan.is_exploration and sp.keywords and sp.why
        # 还没有真实分享时：示例要标明是示例，而且不编造孩子的反应
        assert "示例" in sp.author and sp.reaction == ""


def test_matches_what_the_child_is_asking(env):
    client, h, child_id, _, _ = env
    talk(client, "蜗牛为什么背着房子？")
    talk(client, "下雨的时候小鸟去哪里？", [{"role": "user", "content": "x"}, {"role": "assistant", "content": "y"}])
    plans = client.get(f"/api/studio/shared?child_id={child_id}", headers=h).json()["plans"]
    assert plans[0]["id"] == "sh_rainy_day"
    assert "最近问过「" in plans[0]["reason"]
    assert len(plans) <= 6 and all("plan" not in p for p in plans)


def test_without_child_questions_gives_default_order(env):
    client, h, child_id, _, _ = env
    plans = client.get(f"/api/studio/shared?child_id={child_id}&limit=3", headers=h).json()["plans"]
    assert len(plans) == 3 and all(p["reason"] == "" for p in plans)
    assert [p["id"] for p in client.get("/api/studio/shared", headers=h).json()["plans"]][:3] == [p["id"] for p in plans]


def test_other_familys_child_is_not_visible(env):
    client, h, _, _, _ = env
    token = client.post("/api/auth/dev-login", json={"openid": "dad-elsewhere"}).json()["token"]
    other = client.post("/api/devices", headers={"Authorization": f"Bearer {token}"}, json={"device_id": "dev-0002"}).json()["child_id"]
    assert client.get(f"/api/studio/shared?child_id={other}", headers=h).status_code == 404


def test_shared_plan_detail(env):
    client, h, _, _, _ = env
    d = client.get("/api/studio/shared/sh_day_night", headers=h).json()
    assert d["name"] == "为什么天会黑" and d["plan"]["moves"]
    assert client.get("/api/studio/shared/nope", headers=h).status_code == 404


def test_borrow_rewrites_for_this_child(env):
    client, h, child_id, designer, _ = env
    talk(client, "蜗牛为什么背着房子？")
    designer.replies = [json.dumps(PLAN, ensure_ascii=False)]
    r = client.post("/api/studio/borrow", headers=h, json={"shared_id": "sh_rainy_day", "child_id": child_id})
    assert r.status_code == 200
    assert r.json()["based_on"] == "下雨天小动物去哪了" and r.json()["draft"]["name"] == "乐乐的下雨天探险"
    context = designer.calls[-1][1]["content"]
    assert "原计划" in context and "下雨天小动物去哪了" in context and "蜗牛为什么背着房子" in context
    assert client.post("/api/studio/borrow", headers=h, json={"shared_id": "nope"}).status_code == 404
