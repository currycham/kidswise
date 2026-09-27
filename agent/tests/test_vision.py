import json

import pytest
from fastapi.testclient import TestClient

from waywise_agent.app import Runtime, app, get_llm, get_runtime
from waywise_agent.config import Settings, get_settings
from waywise_agent.memory import MemoryScheduler
from waywise_agent.store import Store
from waywise_agent.vision import wants_to_show

AUTH = {"Authorization": "Bearer k"}
TOOLS = [
    {"type": "function", "function": {"name": "self_camera_take_photo", "parameters": {}}},
    {"type": "function", "function": {"name": "handle_exit_intent", "parameters": {}}},
    {"type": "function", "function": {"name": "direct_answer", "parameters": {}}},
]
# xiaozhi 工具调用模式注入的示例对话
FEWSHOT = [
    {"role": "user", "content": "给我讲个故事吧"},
    {"role": "assistant", "tool_calls": [{"id": "fewshot_da_001", "type": "function", "function": {"name": "direct_answer", "arguments": "{}"}}]},
    {"role": "tool", "tool_call_id": "fewshot_da_001", "content": "已直接回复"},
    {"role": "user", "content": "拜拜"},
    {"role": "assistant", "tool_calls": [{"id": "fewshot_exit_001", "type": "function", "function": {"name": "handle_exit_intent", "arguments": "{}"}}]},
    {"role": "tool", "tool_call_id": "fewshot_exit_001", "content": "再见"},
]
SYSTEM = {"role": "system", "content": '<waywise-device id="cam-01"/>'}


class FakeLLM:
    def __init__(self):
        self.calls = []

    async def stream(self, messages, **params):
        self.calls.append(messages)
        yield "A red apple! 这是一个红苹果。Can you say apple?"


@pytest.fixture
def env(tmp_path):
    settings = Settings(_env_file=None, agent_api_key="k", db_path=str(tmp_path / "t.db"))
    llm, store = FakeLLM(), Store(tmp_path / "t.db")
    rt = Runtime(store, MemoryScheduler(store, lambda: llm, 60))
    app.dependency_overrides.update({get_settings: lambda: settings, get_llm: lambda: llm, get_runtime: lambda: rt})
    yield TestClient(app), llm, store
    app.dependency_overrides.clear()


def post(client, messages, stream=False, tools=TOOLS):
    return client.post("/v1/chat/completions", headers=AUTH, json={"messages": messages, "tools": tools, "stream": stream})


@pytest.mark.parametrize("text", ["你看这是什么", "猜猜这是什么？", "你能看到我手里的东西吗", "Look at this!", "what's this", "你看看我画的"])
def test_asks_to_see(text):
    assert wants_to_show(text)


@pytest.mark.parametrize("text", ["我看到一只小狗", "我们去看看大象吧", "你看起来很开心", "我想看动画片", "你好"])
def test_not_asking_to_see(text):
    assert not wants_to_show(text)


def test_photo_request_returns_tool_call_without_llm(env):
    client, llm, _ = env
    body = post(client, [*FEWSHOT, SYSTEM, {"role": "user", "content": "你看这是什么"}]).json()
    choice = body["choices"][0]
    assert choice["finish_reason"] == "tool_calls"
    call = choice["message"]["tool_calls"][0]
    assert call["function"]["name"] == "self_camera_take_photo"
    assert "你看这是什么" in json.loads(call["function"]["arguments"])["question"]
    assert llm.calls == []


def test_photo_request_streams_tool_call(env):
    client, _, _ = env
    r = post(client, [SYSTEM, {"role": "user", "content": "猜猜这是什么"}], stream=True)
    chunks = [json.loads(l[6:]) for l in r.text.splitlines() if l.startswith("data: {")]
    deltas = [c["choices"][0]["delta"] for c in chunks]
    assert deltas[1]["tool_calls"][0]["function"]["name"] == "self_camera_take_photo"
    assert chunks[-1]["choices"][0]["finish_reason"] == "tool_calls"


def test_photo_result_becomes_context_for_mumi(env):
    client, llm, store = env
    ask = [SYSTEM, *FEWSHOT, {"role": "user", "content": "你看这是什么"}]
    call = post(client, ask).json()["choices"][0]["message"]["tool_calls"][0]
    follow = [*ask, {"role": "assistant", "tool_calls": [call]},
              {"role": "tool", "tool_call_id": call["id"], "content": json.dumps({"success": True, "text": "一个红色的苹果，放在桌子上"})}]
    reply = post(client, follow).json()["choices"][0]["message"]["content"]
    assert reply.startswith("A red apple!")
    system, *history = llm.calls[-1]
    assert "一个红色的苹果" in system["content"] and "我看到了" in system["content"]
    assert [m["content"] for m in history] == ["你看这是什么"]  # 示例对话、工具消息都不进历史
    # 孩子那句只记一次，Mumi 的回答记下来
    rows = store._q("SELECT role, text FROM message ORDER BY id")
    assert [(r["role"], r["text"]) for r in rows] == [("user", "你看这是什么"), ("assistant", reply)]


def test_failed_photo_asks_child_to_retry(env):
    client, llm, _ = env
    ask = [SYSTEM, {"role": "user", "content": "你看这是什么"}]
    call = post(client, ask).json()["choices"][0]["message"]["tool_calls"][0]
    post(client, [*ask, {"role": "assistant", "tool_calls": [call]},
                  {"role": "tool", "tool_call_id": call["id"], "content": json.dumps({"success": False, "message": "timeout"})}])
    assert "没看清" in llm.calls[-1][0]["content"]


def test_normal_talk_with_tools_available_goes_to_llm_and_fewshot_does_not_break_sessions(env):
    client, llm, store = env
    post(client, [*FEWSHOT, SYSTEM, {"role": "user", "content": "我看到一只小狗"}])
    post(client, [*FEWSHOT, SYSTEM, {"role": "user", "content": "我看到一只小狗"},
                  {"role": "assistant", "content": "A dog!"}, {"role": "user", "content": "它是白色的"}])
    assert len(llm.calls) == 2
    assert all("给我讲个故事吧" not in json.dumps(c, ensure_ascii=False) for c in llm.calls)
    assert len(store._q("SELECT id FROM session")) == 1  # 示例里的「用户消息」不算，第二句仍是同一场


def test_no_camera_tool_means_no_photo(env):
    client, llm, _ = env
    post(client, [SYSTEM, {"role": "user", "content": "你看这是什么"}], tools=None)
    assert len(llm.calls) == 1
