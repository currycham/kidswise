import json

import pytest
from fastapi.testclient import TestClient

from waywise_agent.app import app
from waywise_agent.config import Settings, get_settings

AUTH = {"Authorization": "Bearer test-key"}


@pytest.fixture
def client(tmp_path):
    settings = Settings(_env_file=None, agent_api_key="test-key", llm_provider="mock", db_path=str(tmp_path / "t.db"))
    app.dependency_overrides[get_settings] = lambda: settings
    yield TestClient(app)
    app.dependency_overrides.clear()


def test_rejects_bad_key(client):
    r = client.post("/v1/chat/completions", json={"messages": [{"role": "user", "content": "hi"}]})
    assert r.status_code == 401


def test_non_stream(client):
    r = client.post("/v1/chat/completions", headers=AUTH, json={"messages": [{"role": "user", "content": "我看到一只狗"}]})
    assert r.status_code == 200
    body = r.json()
    assert body["model"] == "mumi-teacher"
    assert "我看到一只狗" in body["choices"][0]["message"]["content"]


def test_stream(client):
    r = client.post(
        "/v1/chat/completions",
        headers=AUTH,
        json={"stream": True, "messages": [{"role": "user", "content": "hello"}]},
    )
    lines = [l[6:] for l in r.text.splitlines() if l.startswith("data: ")]
    assert lines[-1] == "[DONE]"
    text = "".join(json.loads(l)["choices"][0]["delta"].get("content", "") for l in lines[:-1])
    assert "hello" in text


def test_drops_upstream_system_prompt_and_unwraps_speaker_json():
    from waywise_agent.app import ChatRequest, device_id_of, history_of

    req = ChatRequest(
        messages=[
            {"role": "system", "content": '你是小智，来自台湾的00后女生 <waywise-device id="aa:bb:cc"/>'},
            {"role": "user", "content": '{"speaker": "豆豆", "content": "我看到大象了"}'},
        ]
    )
    assert history_of(req) == [{"role": "user", "content": "我看到大象了"}]
    assert device_id_of(req, None) == "aa:bb:cc"
    assert device_id_of(ChatRequest(messages=[{"role": "user", "content": "hi"}]), "hdr") == "hdr"


def test_strips_asr_emotion_emoji():
    from waywise_agent.app import ChatRequest, history_of

    req = ChatRequest(messages=[{"role": "user", "content": "我画了一只小狗。😊"}])
    assert history_of(req) == [{"role": "user", "content": "我画了一只小狗。"}]


@pytest.mark.parametrize("text", ["你好，小志。", "你好小智", "小智", "Hello 小智!", "你好木米", "你好，Mumi。", "hello mumi", "你好，猫咪。"])
def test_wake_word_becomes_hello(text):
    from waywise_agent.app import ChatRequest, history_of

    assert history_of(ChatRequest(messages=[{"role": "user", "content": text}]))[0]["content"] == "你好"


def test_normal_text_mentioning_xiaozhi_is_kept():
    from waywise_agent.app import ChatRequest, history_of

    text = "小智你今天去哪了"
    assert history_of(ChatRequest(messages=[{"role": "user", "content": text}]))[0]["content"] == text
