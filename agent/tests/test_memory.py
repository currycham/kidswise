import asyncio

import pytest
from fastapi.testclient import TestClient

from waywise_agent.app import Runtime, app, get_llm, get_runtime
from waywise_agent.config import Settings, get_settings
from waywise_agent.memory import MemoryScheduler, extract_facts, grounded, parse_facts
from waywise_agent.store import HEARD, NEW, SAID, Store, word_status

AUTH = {"Authorization": "Bearer test-key"}
MARKER = '<waywise-device id="dev-1"/>'


class FakeLLM:
    """记忆抽取请求返回固定事实，其余返回固定回复；记下每次收到的 system prompt。"""

    def __init__(self):
        self.prompts: list[str] = []

    async def stream(self, messages, **params):
        system = messages[0]["content"]
        if system.startswith("从这段对话"):
            yield '["喜欢白色的小狗"]'
            return
        self.prompts.append(system)
        yield "A dog! Is it running?"


@pytest.fixture
def env(tmp_path):
    settings = Settings(_env_file=None, agent_api_key="test-key", db_path=str(tmp_path / "t.db"), memory_idle_s=60)
    llm = FakeLLM()
    store = Store(settings.db_path)
    rt = Runtime(store, MemoryScheduler(store, lambda: llm, settings.memory_idle_s))
    app.dependency_overrides[get_settings] = lambda: settings
    app.dependency_overrides[get_llm] = lambda: llm
    app.dependency_overrides[get_runtime] = lambda: rt
    yield TestClient(app), llm, store
    app.dependency_overrides.clear()


def say(client, *turns):
    msgs = [{"role": "system", "content": "xiaozhi persona " + MARKER}]
    for i, t in enumerate(turns):
        if i:
            msgs.append({"role": "assistant", "content": "A dog! Is it running?"})
        msgs.append({"role": "user", "content": t})
    r = client.post("/v1/chat/completions", headers=AUTH, json={"messages": msgs})
    assert r.status_code == 200


def test_second_session_remembers_first(env):
    client, llm, store = env
    say(client, "我看到一只白色的小狗")
    say(client, "我看到一只白色的小狗", "它叫雪球")  # 同一场的第二轮
    assert "你记得" not in llm.prompts[-1]

    say(client, "你好")  # 新连接 = 新的一场：先补抽上一场的记忆
    assert "喜欢白色的小狗" in llm.prompts[-1]
    assert "上次你说" in llm.prompts[-1]


def test_sessions_and_messages_are_stored(env):
    client, _, store = env
    say(client, "hi")
    say(client, "hi", "dog")
    rows = store._q("SELECT id FROM session")
    assert len(rows) == 1
    msgs = store.session_messages(rows[0]["id"])
    assert [m["role"] for m in msgs] == ["user", "assistant", "user", "assistant"]
    assert store._q("SELECT region FROM message LIMIT 1")[0]["region"] == "cn"


def test_word_progress_three_levels(env):
    client, llm, store = env
    child_id = store._q("SELECT id FROM child")[0]["id"] if store._q("SELECT id FROM child") else None
    say(client, "我看到小狗了")  # Mumi 说了 dog → 听过
    child_id = store._q("SELECT id FROM child")[0]["id"]
    assert store.word_statuses(child_id)["dog"] == HEARD
    say(client, "我看到小狗了", "dog")
    say(client, "我看到小狗了", "dog", "Dogs!")  # 孩子说了两次 → 会说
    assert store.word_statuses(child_id)["dog"] == SAID
    assert "孩子已经学过的词" in llm.prompts[-1] and "dog（小狗）" in llm.prompts[-1]


def test_word_status_thresholds():
    assert word_status(0, 0) == NEW
    assert word_status(1, 0) == HEARD
    assert word_status(0, 1) == HEARD
    assert word_status(3, 2) == SAID


def test_idle_gap_starts_new_session(tmp_path):
    store = Store(tmp_path / "t.db")
    child = store.child_for_device("d", "豆豆", 5, "park")
    assert store.child_for_device("d", "x", 9, "zoo").id == child.id
    a = store.resolve_session("d", child.id, first_turn=True, idle_s=600)
    b = store.resolve_session("d", child.id, first_turn=False, idle_s=600)
    c = store.resolve_session("d", child.id, first_turn=False, idle_s=0)
    assert a.is_new and not b.is_new and b.id == a.id
    assert c.is_new and c.previous_id == a.id


def test_parse_facts():
    assert parse_facts('好的：["喜欢恐龙", "有个妹妹"]') == ["喜欢恐龙", "有个妹妹"]
    assert parse_facts("[]") == []
    assert parse_facts("没有") == []
    assert len(parse_facts('["a","b","c","d"]')) == 3


def test_scheduler_extracts_after_idle(tmp_path):
    store = Store(tmp_path / "t.db")
    child = store.child_for_device("d", "豆豆", 5, "park")
    s = store.resolve_session("d", child.id, True, 600)
    store.add_message(s.id, "user", "我喜欢小狗", [])
    sched = MemoryScheduler(store, FakeLLM, idle_s=0.01)

    async def run():
        sched.schedule(s.id, child.id)
        await asyncio.sleep(0.1)

    asyncio.run(run())
    assert store.recent_facts(child.id) == ["喜欢白色的小狗"]


def test_grounded_keeps_what_the_child_said_and_drops_inventions():
    child = "蜗牛为什么背着房子呀？ 下雨的时候小鸟躲在哪里？ 我家有一只小白狗叫雪球"
    assert grounded("蜗牛背着房子", child)
    assert grounded("有一只叫雪球的小白狗", child)
    assert not grounded("今天去了公园", child)
    assert not grounded("有个妹妹", child)
    assert grounded("likes dinosaurs", "I like dinosaurs")


def test_extraction_drops_copied_examples():
    # 孩子只问了问题，模型却「记住」了孩子没说过的事（实测：照抄了提示词里的例子）
    class Copycat:
        async def stream(self, messages, **params):
            yield '["蜗牛背着房子", "有一只叫雪球的小白狗", "今天去了公园"]'

    msgs = [{"role": "user", "text": "蜗牛为什么背着房子呀？"}, {"role": "assistant", "text": "你觉得呢？"}]
    assert asyncio.run(extract_facts(Copycat(), msgs)) == ["蜗牛背着房子"]
