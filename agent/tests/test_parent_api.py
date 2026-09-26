import pytest
from fastapi.testclient import TestClient

from waywise_agent import parent_api
from waywise_agent.app import app, get_runtime
from waywise_agent.auth import AuthError, issue_token, verify_token
from waywise_agent.config import Settings, get_settings


@pytest.fixture
def env(tmp_path):
    settings = Settings(_env_file=None, agent_api_key="k", db_path=str(tmp_path / "t.db"), dev_login=True)
    app.dependency_overrides[get_settings] = lambda: settings
    yield TestClient(app), settings, get_runtime(settings).store
    app.dependency_overrides.clear()


def login(client, who="mom"):
    r = client.post("/api/auth/dev-login", json={"openid": who})
    assert r.status_code == 200
    return {"Authorization": f"Bearer {r.json()['token']}"}


def chat_history(store, device_id):
    """模拟设备在绑定前已经聊过天。"""
    child = store.child_for_device(device_id, "小朋友", 5, "park")
    s = store.resolve_session(device_id, child.id, True, 600)
    store.add_message(s.id, "user", "我今天在公园看到一只白色的小狗", [])
    store.add_message(s.id, "assistant", "A dog! Is it running?", ["dog"])
    store.add_message(s.id, "user", "dog", ["dog"])
    store.add_message(s.id, "user", "我家住在幸福路88号", [], blocked=True)
    store.record_words(child.id, heard=["dog"], said=["dog"])
    store.add_safety_event(child.id, s.id, "hurt", "我摔倒了", True)
    return child.id


def test_dev_login_is_off_by_default(tmp_path):
    settings = Settings(_env_file=None, agent_api_key="k", db_path=str(tmp_path / "t.db"))
    app.dependency_overrides[get_settings] = lambda: settings
    try:
        assert TestClient(app).post("/api/auth/dev-login", json={"openid": "x"}).status_code == 404
    finally:
        app.dependency_overrides.clear()


def test_wechat_login(env, monkeypatch):
    client, _, _ = env

    async def fake(settings, code):
        if code != "good":
            raise AuthError("微信登录失败: 40029 invalid code")
        return "openid-123"

    monkeypatch.setattr(parent_api, "code_to_openid", fake)
    assert client.post("/api/auth/login", json={"code": "bad"}).status_code == 400
    r = client.post("/api/auth/login", json={"code": "good"})
    assert r.status_code == 200 and r.json()["children"] == []
    # 同一个 openid 再登录 = 同一个家庭
    assert verify_token(env[1], r.json()["token"]) == verify_token(
        env[1], client.post("/api/auth/login", json={"code": "good"}).json()["token"]
    )


def test_token_rules(env):
    _, settings, _ = env
    token = issue_token(settings, "fam_1", now=1000)
    assert verify_token(settings, token, now=1001) == "fam_1"
    with pytest.raises(AuthError):
        verify_token(settings, token, now=1000 + 31 * 24 * 3600)
    with pytest.raises(AuthError):
        verify_token(settings, token[:-1] + ("0" if token[-1] != "0" else "1"))
    assert env[0].get("/api/children", headers={"Authorization": "Bearer nope"}).status_code == 401


def test_bind_moves_existing_child_and_blocks_second_parent(env):
    client, _, store = env
    child_id = chat_history(store, "aa:bb:cc:dd:ee:01")
    mom = login(client, "mom")
    r = client.post("/api/devices", headers=mom, json={"device_id": "aa:bb:cc:dd:ee:01"})
    assert r.status_code == 200 and r.json()["child_id"] == child_id
    # 再绑一次是幂等的
    assert client.post("/api/devices", headers=mom, json={"device_id": "aa:bb:cc:dd:ee:01"}).status_code == 200
    # 别的家长不能抢
    dad = login(client, "stranger")
    assert client.post("/api/devices", headers=dad, json={"device_id": "aa:bb:cc:dd:ee:01"}).status_code == 409
    assert client.post("/api/devices", headers=dad, json={"device_id": "bad id!"}).status_code == 400
    # 也看不到别人家的孩子
    assert client.get(f"/api/children/{child_id}/today", headers=dad).status_code == 404


def test_bind_new_device_creates_child(env):
    client, _, _ = env
    mom = login(client)
    r = client.post("/api/devices", headers=mom, json={"device_id": "new-device-01"})
    assert r.status_code == 200
    kids = client.get("/api/children", headers=mom).json()["children"]
    assert kids[0]["devices"] == ["new-device-01"] and kids[0]["theme_id"] == "park"


def test_update_child(env):
    client, _, _ = env
    mom = login(client)
    child_id = client.post("/api/devices", headers=mom, json={"device_id": "dev-0002"}).json()["child_id"]
    r = client.patch(f"/api/children/{child_id}", headers=mom, json={"name": "豆豆", "age": 4, "theme_id": "zoo"})
    assert r.json()["name"] == "豆豆" and r.json()["age"] == 4 and r.json()["theme_id"] == "zoo"
    assert client.patch(f"/api/children/{child_id}", headers=mom, json={"theme_id": "mars"}).status_code == 400
    assert client.patch(f"/api/children/{child_id}", headers=mom, json={"age": 30}).status_code == 422


def test_read_pages(env):
    client, _, store = env
    child_id = chat_history(store, "aa:bb:cc:dd:ee:02")
    mom = login(client)
    client.post("/api/devices", headers=mom, json={"device_id": "aa:bb:cc:dd:ee:02"})

    today = client.get(f"/api/children/{child_id}/today", headers=mom).json()
    assert today["sessions"] == 1 and today["minutes"] == 1
    assert today["quote"]["text"] == "我今天在公园看到一只白色的小狗"  # 最长的一句，且不取被拦下的那句
    assert today["words"] == [{"en": "dog", "zh": "小狗", "status": "heard"}]
    assert today["alerts"][0]["category"] == "hurt"

    day = client.get(f"/api/children/{child_id}/sessions", headers=mom).json()
    roles = [m["role"] for m in day["sessions"][0]["messages"]]
    assert roles == ["child", "mumi", "child", "child"]

    prog = client.get(f"/api/children/{child_id}/progress", headers=mom).json()
    assert prog["words"][0]["en"] == "dog" and prog["words"][0]["this_week"]

    themes = client.get("/api/themes", headers=mom).json()["themes"]
    assert len(themes) == 10
