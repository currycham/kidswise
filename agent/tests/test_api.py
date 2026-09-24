import json

import pytest
from fastapi.testclient import TestClient

from waywise_agent.app import app
from waywise_agent.config import Settings, get_settings

AUTH = {"Authorization": "Bearer test-key"}


@pytest.fixture
def client():
    app.dependency_overrides[get_settings] = lambda: Settings(_env_file=None, agent_api_key="test-key", llm_provider="mock")
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
