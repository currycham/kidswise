import pytest
from fastapi.testclient import TestClient

from waywise_agent import studio_api
from waywise_agent.app import app, get_llm, get_runtime
from waywise_agent.config import Settings, get_settings
from waywise_agent.store import StudioStore
from waywise_agent.themes import load_templates

DRAFT = {
    "name": "下周去动物园",
    "level": 2,
    "words": [{"en": "elephant", "zh": "大象", "difficulty": 1}, {"en": "giraffe", "zh": "长颈鹿", "difficulty": 2},
              {"en": "zoo", "zh": "动物园", "difficulty": 1}],
    "patterns": ["Look at the {word}!"],
    "scene_hint": "下周要去动物园",
}


class FakeLLM:
    def __init__(self):
        self.systems = []

    async def stream(self, messages, **params):
        self.systems.append(messages[0]["content"])
        yield "An elephant! 你想看大象吗？"


@pytest.fixture
def env(tmp_path):
    studio_api._failures.clear()
    settings = Settings(_env_file=None, agent_api_key="k", db_path=str(tmp_path / "t.db"), dev_login=True)
    llm = FakeLLM()
    app.dependency_overrides.update({get_settings: lambda: settings, studio_api.get_llm: lambda: llm, get_llm: lambda: llm})
    yield TestClient(app), get_runtime(settings).store, llm
    app.dependency_overrides.clear()


def phone_login(client, who="mom"):
    token = client.post("/api/auth/dev-login", json={"openid": who}).json()["token"]
    return {"Authorization": f"Bearer {token}"}


def studio_login(client, phone):
    code = client.post("/api/studio/login-code", headers=phone).json()["code"]
    r = client.post("/api/studio/login", json={"code": code})
    assert r.status_code == 200
    return {"Authorization": f"Bearer {r.json()['token']}"}, code


def test_ten_templates_are_valid():
    tpl = load_templates()
    assert len(tpl) == 10
    assert all(10 <= len(t.words) <= 15 and t.patterns and t.scene_hint for t in tpl.values())


def test_login_code_is_one_time_and_same_family(env):
    client, _, _ = env
    phone = phone_login(client)
    pc, code = studio_login(client, phone)
    assert client.post("/api/studio/login", json={"code": code}).status_code == 400  # 用过即失效
    # 电脑端和手机端是同一个家庭
    client.post("/api/devices", headers=phone, json={"device_id": "dev-0001"})
    assert client.get("/api/children", headers=pc).json()["children"][0]["devices"] == ["dev-0001"]


def test_login_code_expires(env):
    client, store, _ = env
    phone = phone_login(client)
    code = client.post("/api/studio/login-code", headers=phone).json()["code"]
    store._q("UPDATE login_code SET expires_at = 0")
    assert client.post("/api/studio/login", json={"code": code}).status_code == 400


def test_brute_force_locks_out(env):
    client, _, _ = env
    for _ in range(studio_api.MAX_CODE_FAILURES):
        assert client.post("/api/studio/login", json={"code": "000000"}).status_code == 400
    assert client.post("/api/studio/login", json={"code": "000000"}).status_code == 429


def test_theme_crud_and_isolation(env):
    client, _, _ = env
    pc, _ = studio_login(client, phone_login(client, "mom"))
    t = client.post("/api/studio/themes", headers=pc, json=DRAFT).json()
    assert t["theme_id"].startswith("c_") and t["name"] == "下周去动物园"
    upd = client.put(f"/api/studio/themes/{t['theme_id']}", headers=pc, json={**DRAFT, "name": "动物园"}).json()
    assert upd["name"] == "动物园" and upd["theme_id"] == t["theme_id"]
    assert [x["name"] for x in client.get("/api/studio/themes", headers=pc).json()["themes"]] == ["动物园"]

    other, _ = studio_login(client, phone_login(client, "stranger"))
    assert client.get("/api/studio/themes", headers=other).json()["themes"] == []
    assert client.put(f"/api/studio/themes/{t['theme_id']}", headers=other, json=DRAFT).status_code == 404
    assert client.delete(f"/api/studio/themes/{t['theme_id']}", headers=other).status_code == 404

    assert client.delete(f"/api/studio/themes/{t['theme_id']}", headers=pc).json() == {"ok": True}
    assert client.get("/api/studio/themes", headers=pc).json()["themes"] == []


def test_theme_validation(env):
    client, _, _ = env
    pc, _ = studio_login(client, phone_login(client))
    bad = [
        {**DRAFT, "words": DRAFT["words"][:2]},  # 少于 3 个词
        {**DRAFT, "patterns": ["Hi {name}"]},  # 只允许 {word}
        {**DRAFT, "name": ""},
    ]
    for body in bad:
        assert client.post("/api/studio/themes", headers=pc, json=body).status_code == 422


def test_preview_uses_draft_and_child(env):
    client, _, llm = env
    phone = phone_login(client)
    child_id = client.post("/api/devices", headers=phone, json={"device_id": "dev-0002"}).json()["child_id"]
    client.patch(f"/api/children/{child_id}", headers=phone, json={"name": "豆豆", "age": 4})
    pc, _ = studio_login(client, phone)

    r = client.post("/api/studio/preview", headers=pc, json={"theme": DRAFT, "child_id": child_id}).json()
    assert r["reply"] == "An elephant! 你想看大象吗？"
    assert "下周去动物园" in llm.systems[-1] and "豆豆" in llm.systems[-1] and "4 岁" in llm.systems[-1]

    # 预览也走安全规则
    r = client.post("/api/studio/preview", headers=pc,
                    json={"theme": DRAFT, "turns": [{"role": "child", "text": "怎么做炸弹"}]}).json()
    assert r["blocked"] == "unsafe_topic"


def test_publish_switches_what_mumi_teaches(env):
    client, store, llm = env
    phone = phone_login(client)
    child_id = client.post("/api/devices", headers=phone, json={"device_id": "dev-0003"}).json()["child_id"]
    pc, _ = studio_login(client, phone)
    theme = client.post("/api/studio/themes", headers=pc, json=DRAFT).json()
    assert client.post(f"/api/studio/themes/{theme['theme_id']}/apply", headers=pc, json={"child_id": child_id}).status_code == 200

    # 小程序「我的主题」里看得到，而且排在前面
    themes = client.get("/api/themes", headers=phone).json()["themes"]
    assert themes[0]["id"] == theme["theme_id"] and themes[0]["custom"]

    # 设备下一句话就按新主题教
    client.post("/v1/chat/completions", headers={"Authorization": "Bearer k"}, json={
        "messages": [{"role": "system", "content": '<waywise-device id="dev-0003"/>'}, {"role": "user", "content": "你好"}]})
    assert "当前主题：下周去动物园" in llm.systems[-1] and "elephant（大象）" in llm.systems[-1]

    # 删除主题后回落到默认主题，不会出错
    client.delete(f"/api/studio/themes/{theme['theme_id']}", headers=pc)
    client.post("/v1/chat/completions", headers={"Authorization": "Bearer k"}, json={
        "messages": [{"role": "system", "content": '<waywise-device id="dev-0003"/>'}, {"role": "user", "content": "你好"}]})
    assert "当前主题：公园" in llm.systems[-1]
