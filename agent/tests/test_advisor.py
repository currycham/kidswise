"""家长教育 Agent 第二步：顾问（认识这个孩子）+ 好奇心周报。"""

import json

import pytest
from fastapi.testclient import TestClient

from waywise_agent import advisor_api, studio_api
from waywise_agent.advisor import parse_weekly, split_plan_idea
from waywise_agent.app import app, get_llm, get_runtime
from waywise_agent.config import Settings, get_settings

WEEKLY = {
    "summary": "这周豆豆迷上了天为什么会黑。",
    "topics": [{"topic": "天空", "questions": ["为什么天会黑"]}],
    "highlights": [{"quote": "因为太阳下班了", "why": "他用自己的生活经验解释自然现象"}],
    "notice": [],
    "next": [{"idea": "傍晚一起看太阳落山", "plan_idea": "陪他看日落，猜太阳去哪了"}],
}


class FakeLLM:
    def __init__(self):
        self.calls, self.replies = [], []

    async def stream(self, messages, **params):
        self.calls.append(messages)
        text = self.replies.pop(0) if self.replies else "你好"
        for i in range(0, len(text), 5):
            yield text[i : i + 5]


@pytest.fixture
def env(tmp_path):
    studio_api._failures.clear()
    settings = Settings(_env_file=None, agent_api_key="k", db_path=str(tmp_path / "t.db"), dev_login=True)
    chat, adv = FakeLLM(), FakeLLM()
    app.dependency_overrides.update({get_settings: lambda: settings, get_llm: lambda: chat,
                                     advisor_api.get_advisor_llm: lambda: adv})
    client = TestClient(app)
    h = {"Authorization": f"Bearer {client.post('/api/auth/dev-login', json={'openid': 'mom'}).json()['token']}"}
    child_id = client.post("/api/devices", headers=h, json={"device_id": "dev-0001"}).json()["child_id"]
    client.patch(f"/api/children/{child_id}", headers=h, json={"name": "豆豆", "age": 5})
    yield client, h, child_id, chat, adv, get_runtime(settings).store
    app.dependency_overrides.clear()


def talk(client, *lines):
    """像设备一样：每说一句发一次请求，带上这一场之前的全部对话。"""
    msgs = [{"role": "system", "content": '<waywise-device id="dev-0001"/>'}]
    for i, t in enumerate(lines):
        if i:
            msgs.append({"role": "assistant", "content": "你觉得呢？"})
        msgs.append({"role": "user", "content": t})
        client.post("/v1/chat/completions", headers={"Authorization": "Bearer k"}, json={"messages": list(msgs)})


def test_advice_is_plain_text():
    text, _ = split_plan_idea("你家孩子真棒 🌟\n\n1. **一起猜**：延续他的比喻\n- 另一种做法")
    assert "**" not in text and "🌟" not in text and "- 另一种" not in text and "1. 一起猜" in text


def test_split_plan_idea():
    text, idea = split_plan_idea("可以试试三种做法……\n\n探索计划建议：陪他猜一猜天为什么会黑\n")
    assert idea == "陪他猜一猜天为什么会黑" and "探索计划建议" not in text
    assert split_plan_idea("只是聊聊")[1] is None


def test_advisor_knows_this_child(env):
    client, h, child_id, _, adv, _ = env
    client.put("/api/studio/profile", headers=h, json={"values": "好奇心", "avoid": "刷题"})
    talk(client, "为什么天会黑呀", "因为太阳下班了")
    adv.replies = ["这周豆豆问了「为什么天会黑呀」……\n探索计划建议：陪他猜一猜天为什么会黑"]
    r = client.post("/api/advisor/chat", headers=h, json={"text": "他问题特别多我有点烦，怎么办？", "child_id": child_id}).json()
    assert r["plan_idea"] == "陪他猜一猜天为什么会黑" and "探索计划建议" not in r["text"]
    system = adv.calls[-1][0]["content"]
    for must in ("豆豆，5 岁", "最看重：好奇心", "不想要：刷题", "为什么天会黑呀", "「因为太阳下班了」", "不要编造论文", "不诊断"):
        assert must in system, must
    assert adv.calls[-1][-1] == {"role": "user", "content": "他问题特别多我有点烦，怎么办？"}


def test_advisor_remembers_the_conversation(env):
    client, h, child_id, _, adv, _ = env
    adv.replies = ["第一次回答", "第二次回答"]
    client.post("/api/advisor/chat", headers=h, json={"text": "第一个问题", "child_id": child_id})
    client.post("/api/advisor/chat", headers=h, json={"text": "接着问", "child_id": child_id})
    roles = [(m["role"], m["content"]) for m in adv.calls[-1][1:]]
    assert roles == [("user", "第一个问题"), ("assistant", "第一次回答"), ("user", "接着问")]
    hist = client.get(f"/api/advisor/history?child_id={child_id}", headers=h).json()["messages"]
    assert [m["role"] for m in hist] == ["parent", "advisor", "parent", "advisor"]
    client.delete(f"/api/advisor/history?child_id={child_id}", headers=h)
    assert client.get(f"/api/advisor/history?child_id={child_id}", headers=h).json()["messages"] == []


def test_advisor_streams(env):
    client, h, child_id, _, adv, _ = env
    adv.replies = ["一段比较长的回答，分好几块流出来。\n探索计划建议：试试数一数"]
    r = client.post("/api/advisor/chat", headers=h, json={"text": "问", "child_id": child_id, "stream": True})
    events = [json.loads(l[6:]) for l in r.text.splitlines() if l.startswith("data: ")]
    assert len([e for e in events if "delta" in e]) > 2
    assert events[-1]["done"] and events[-1]["plan_idea"] == "试试数一数"


def test_advisor_other_family_cannot_use_my_child(env):
    client, _, child_id, _, _, _ = env
    other = {"Authorization": f"Bearer {client.post('/api/auth/dev-login', json={'openid': 'x'}).json()['token']}"}
    assert client.post("/api/advisor/chat", headers=other, json={"text": "问", "child_id": child_id}).status_code == 404
    assert client.get(f"/api/children/{child_id}/weekly", headers=other).status_code == 404


def test_weekly_empty_week_needs_no_llm(env):
    client, h, child_id, _, adv, _ = env
    r = client.get(f"/api/children/{child_id}/weekly", headers=h).json()
    assert r["empty"] and "还没有" in r["summary"] and adv.calls == []


def test_weekly_report_generated_then_cached(env):
    client, h, child_id, _, adv, _ = env
    talk(client, "为什么天会黑呀", "因为太阳下班了")
    adv.replies = ["```json\n" + json.dumps(WEEKLY, ensure_ascii=False) + "\n```"]
    r = client.get(f"/api/children/{child_id}/weekly", headers=h).json()
    assert r["summary"].startswith("这周豆豆") and r["stats"]["questions"] == 1 and r["stats"]["sessions"] == 1
    assert r["highlights"][0]["quote"] == "因为太阳下班了" and r["next"][0]["plan_idea"]
    user = adv.calls[-1][1]["content"]
    assert "为什么天会黑呀" in user and "「因为太阳下班了」" in user
    # 一小时内再看，不重新生成
    client.get(f"/api/children/{child_id}/weekly", headers=h)
    assert len(adv.calls) == 1


def test_weekly_bad_output_is_retried_then_errors(env):
    client, h, child_id, _, adv, _ = env
    talk(client, "为什么天会黑呀")
    adv.replies = ["不是 JSON", "{}"]
    assert client.get(f"/api/children/{child_id}/weekly", headers=h).status_code == 502
    assert len(adv.calls) == 2


def test_parse_weekly_caps_lengths():
    many = {**WEEKLY, "highlights": [{"quote": f"q{i}", "why": ""} for i in range(9)], "next": [{"idea": "a"}] * 5}
    out = parse_weekly(json.dumps(many))
    assert len(out["highlights"]) == 3 and len(out["next"]) == 2
