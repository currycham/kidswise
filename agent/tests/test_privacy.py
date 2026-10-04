"""家长对孩子数据的控制：看过什么（不存照片）、关掉视觉、导出、删除。"""

import json

import pytest
from fastapi.testclient import TestClient

from waywise_agent.app import Runtime, app, get_llm, get_runtime
from waywise_agent.config import Settings, get_settings
from waywise_agent.memory import MemoryScheduler
from waywise_agent.store import Store

AUTH = {"Authorization": "Bearer k"}
TOOLS = [{"type": "function", "function": {"name": "self_camera_take_photo", "parameters": {}}}]
SYSTEM = {"role": "system", "content": '<waywise-device id="cam-01"/>'}


class FakeLLM:
    def __init__(self):
        self.calls = []

    async def stream(self, messages, **params):
        self.calls.append(messages)
        yield "A red apple! 你想尝尝吗？"


@pytest.fixture
def env(tmp_path):
    settings = Settings(_env_file=None, agent_api_key="k", db_path=str(tmp_path / "t.db"), dev_login=True)
    llm, store = FakeLLM(), Store(tmp_path / "t.db")
    rt = Runtime(store, MemoryScheduler(store, lambda: llm, 60))
    app.dependency_overrides.update({get_settings: lambda: settings, get_llm: lambda: llm, get_runtime: lambda: rt})
    client = TestClient(app)
    token = client.post("/api/auth/dev-login", json={"openid": "mom"}).json()["token"]
    h = {"Authorization": f"Bearer {token}"}
    child_id = client.post("/api/devices", headers=h, json={"device_id": "cam-01"}).json()["child_id"]
    yield client, h, child_id, llm, store
    app.dependency_overrides.clear()


def post(client, messages):
    return client.post("/v1/chat/completions", headers=AUTH, json={"messages": messages, "tools": TOOLS}).json()


def show_apple(client):
    ask = [SYSTEM, {"role": "user", "content": "你看这是什么"}]
    call = post(client, ask)["choices"][0]["message"]["tool_calls"][0]
    post(client, [*ask, {"role": "assistant", "tool_calls": [call]},
                  {"role": "tool", "tool_call_id": call["id"], "content": json.dumps({"success": True, "text": "一个红色的苹果，放在桌子上"})}])


def test_parents_see_what_mumi_saw_without_photos(env):
    client, h, child_id, _, store = env
    show_apple(client)
    v = client.get(f"/api/children/{child_id}/vision", headers=h).json()
    assert v["enabled"] is True
    assert v["seen"][0]["asked"] == "你看这是什么" and "红色的苹果" in v["seen"][0]["seen"]
    cols = {r["name"] for r in store._q("PRAGMA table_info(photo_event)")}
    assert not cols & {"image", "photo", "data"}  # 只有文字描述，没有照片


def test_vision_off_means_no_photo_and_mumi_says_its_eyes_are_closed(env):
    client, h, child_id, llm, _ = env
    assert client.put(f"/api/children/{child_id}/vision", headers=h, json={"enabled": False}).json() == {"enabled": False}
    r = post(client, [SYSTEM, {"role": "user", "content": "你看这是什么"}])
    assert "tool_calls" not in r["choices"][0]["message"] or not r["choices"][0]["message"].get("tool_calls")
    assert "眼睛现在闭上啦" in llm.calls[-1][0]["content"]
    client.put(f"/api/children/{child_id}/vision", headers=h, json={"enabled": True})
    assert post(client, [SYSTEM, {"role": "user", "content": "你看这是什么"}])["choices"][0]["message"]["tool_calls"]


def test_export_has_conversations_and_what_mumi_saw(env):
    client, h, child_id, _, _ = env
    post(client, [SYSTEM, {"role": "user", "content": "为什么苹果是红的？"}])
    show_apple(client)
    d = client.get(f"/api/children/{child_id}/export", headers=h).json()
    texts = [m["text"] for s in d["sessions"] for m in s["messages"]]
    assert "为什么苹果是红的？" in texts and d["questions"][0]["text"] == "为什么苹果是红的？"
    assert "红色的苹果" in d["photos_seen"][0]["seen"]


def test_delete_needs_the_childs_name_then_wipes_everything_but_keeps_mumi_working(env):
    client, h, child_id, _, store = env
    post(client, [SYSTEM, {"role": "user", "content": "为什么苹果是红的？"}])
    show_apple(client)
    name = client.get("/api/children", headers=h).json()["children"][0]["name"]
    assert client.post(f"/api/children/{child_id}/delete-data", headers=h, json={"confirm": "不对"}).status_code == 400
    assert client.post(f"/api/children/{child_id}/delete-data", headers=h, json={"confirm": name}).json() == {"deleted": True}
    for t in ("message", "session", "child_question", "photo_event", "word_progress"):
        assert store._q(f"SELECT COUNT(*) n FROM {t}")[0]["n"] == 0, t
    assert client.get("/api/children", headers=h).json()["children"][0]["id"] == child_id  # 绑定还在
    assert post(client, [SYSTEM, {"role": "user", "content": "你好"}])["choices"][0]["message"]["content"]


def test_other_families_cannot_touch_the_data(env):
    client, _, child_id, _, _ = env
    token = client.post("/api/auth/dev-login", json={"openid": "stranger"}).json()["token"]
    other = {"Authorization": f"Bearer {token}"}
    for method, path, body in (("get", "vision", None), ("put", "vision", {"enabled": False}), ("get", "export", None),
                               ("post", "delete-data", {"confirm": "x"})):
        r = getattr(client, method)(f"/api/children/{child_id}/{path}", headers=other, **({"json": body} if body else {}))
        assert r.status_code == 404, path
