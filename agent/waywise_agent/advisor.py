"""家长教育 Agent 第二步：顾问 + 好奇心周报（家长教育 Agent 方案 2.1、第五章）。

顾问和通用 AI 的区别只有一条：它认识这个孩子。这里负责把「这个孩子」整理成一段上下文：
家庭理念、Mumi 记得的事、最近问的问题、这周聊了多少、正在进行的探索、他最近说过的原话。
"""

import json
import re
import time
from dataclasses import dataclass
from datetime import date, datetime, timedelta
from zoneinfo import ZoneInfo

from .store import HEARD, SAID, ParentStore

ADVISOR_PROMPT = """你是 Waywise 的家庭教育顾问，服务的是一个 3–6 岁孩子的家长。你和通用 AI 的区别只有一条：你认识这个孩子。
下面「关于这个孩子」里的内容来自孩子每天和小伙伴 Mumi 的真实聊天，以及家长写下的家庭教育理念。

怎么回答：
1. 先把问题放回这个孩子身上：能引用他最近真实说过的话、问过的问题、正在迷的东西，就引用；数据里没有的，不要编。
2. 给选项，不给唯一答案：通常给 2–3 种做法，说清各自的取舍，最后说「哪种更贴合你们家」由家长决定。
3. 尊重家庭理念：家长写了不想要的东西，就不要推荐；如果你认为某种做法值得再想想，温和地提供另一种视角和理由，但不说教。
4. 说清楚依据和不确定：发展心理学、教育研究里比较一致的看法可以说「研究里比较一致的看法是……」；只是一种观点的，说明是观点。
   不要编造论文、作者、数据或出处；拿不准就直说不确定。
   不要推荐具体的书名、绘本名、App 或课程名（容易记错、也像在带货），改为描述去找什么样的：「找一本画着白天黑夜的绘本」。
   不要夸大：少用「大脑在飞速建模」这类听起来很科学但说不清的话。
5. 不诊断：涉及发育是否正常、心理问题、疾病、用药时，只描述一般规律，并建议和儿科医生或专业人士聊一聊。
   涉及孩子安全（被伤害、被欺负、自伤、走失）时，第一句就建议立刻采取行动、联系相关的人。
6. 不制造焦虑：不和别的孩子比较，不给孩子打分、贴标签（「你家孩子是 xx 型」）。
7. 简洁：一般 150–350 字，口语化，像一个懂行的朋友。可以分几个小段、用 1. 2. 3. 编号；不要用 Markdown（标题、加粗、列表符号）和表情符号。
8. 如果顺着家长的问题，很适合让 Mumi 在日常聊天里陪孩子做一次探索，就在回答最后单独一行写：
   探索计划建议：<一句话，家长的想法，可以直接交给设计师，比如「借他爱问为什么，陪他猜一猜天为什么会黑」>
   不适合就不要写这一行。"""

WEEKLY_PROMPT = """你是 Waywise 的家庭教育顾问，要给家长写一份孩子的「好奇心周报」。
原则：只描述，不评价、不打分、不和别的孩子比较；数据里没有的不要编；语气温暖、具体、简短。

只输出一个 JSON 对象，不要其他文字：
{"summary": "两三句话：这周他在想什么、迷上了什么",
 "topics": [{"topic": "动物", "questions": ["他问过的原问题", "..."]}],   // 把这周的问题按主题归类，每类最多 4 个
 "highlights": [{"quote": "孩子的原话", "why": "为什么值得留意，比如他自己把两件事连起来了"}],   // 0–3 个
 "notice": ["值得家长留意的一点（不是诊断）"],   // 0–2 条，没有就空数组
 "next": [{"idea": "下周可以怎么接着玩", "plan_idea": "可以直接交给设计师的一句话"}]}   // 1–2 条"""

PLAN_LINE = re.compile(r"^\s*探索计划建议[：:]\s*(.+?)\s*$", re.MULTILINE)


@dataclass
class ChildSnapshot:
    child: dict
    profile: dict
    facts: list[str]
    questions: list[str]
    recent_lines: list[str]
    week: dict
    plan: dict | None
    alerts: list[str]

    def as_context(self) -> str:
        c = self.child
        parts = [f"孩子：{c['name']}，{c['age']} 岁"]
        values = {k: v for k, v in self.profile.items() if v}
        if values:
            names = {"values": "最看重", "avoid": "不想要", "language": "对英文的期待", "child_traits": "孩子的特点"}
            parts.append("家庭教育理念：" + "；".join(f"{names.get(k, k)}：{v}" for k, v in values.items()))
        else:
            parts.append("家庭教育理念：家长还没填写")
        w = self.week
        parts.append(
            f"最近 7 天和 Mumi 聊天：{w['sessions']} 次，约 {w['minutes']} 分钟，孩子说了 {w['child_turns']} 句，"
            f"问了 {w['questions']} 个问题；新听过的英文词 {w['heard']} 个，会说的 {w['said']} 个"
        )
        if self.plan:
            parts.append(f"正在进行的探索：{self.plan['name']}（{self.plan.get('domain', '英语')}）" +
                         (f"，目标：{self.plan['goal']}" if self.plan.get("goal") else ""))
        if self.facts:
            parts.append("Mumi 记得的事：" + "；".join(self.facts))
        if self.questions:
            parts.append("最近问过的问题：" + "；".join(self.questions))
        if self.recent_lines:
            parts.append("最近说过的一些原话：" + "；".join(f"「{t}」" for t in self.recent_lines))
        if self.alerts:
            parts.append("最近 7 天的安全提醒：" + "；".join(self.alerts))
        return "\n".join(parts)


ALERT_NAMES = {"hurt": "说自己受伤", "lost": "说找不到大人", "distress": "说了需要关心的话", "unsafe_topic": "问到不适合的话题"}


def snapshot(ps: ParentStore, child: dict, family_id: str, theme: dict | None, now: float | None = None) -> ChildSnapshot:
    now = now or time.time()
    since = now - 7 * 24 * 3600
    sessions = ps.sessions_between(child["id"], since, now + 1)
    messages = ps.messages_of([s["id"] for s in sessions])
    child_lines = [m["text"] for m in messages if m["role"] == "user" and not m["blocked"] and len(m["text"]) >= 4]
    progress = [w for w in ps.word_progress(child["id"]) if w["updated_at"] >= since]
    questions = ps.s.questions_between(child["id"], since, now + 1)
    return ChildSnapshot(
        child=child,
        profile=ps.s.family_profile(family_id),
        facts=ps.s.recent_facts(child["id"], limit=12),
        questions=[q["text"] for q in questions][-20:],
        recent_lines=child_lines[-12:],
        week={
            "sessions": len(sessions),
            "minutes": round(sum(max(s["last_at"] - s["started_at"], 0) for s in sessions) / 60),
            "child_turns": sum(m["role"] == "user" and not m["blocked"] for m in messages),
            "questions": len(questions),
            "heard": sum(w["status"] == HEARD for w in progress),
            "said": sum(w["status"] == SAID for w in progress),
        },
        plan=theme,
        alerts=sorted({ALERT_NAMES.get(a["category"], a["category"]) for a in ps.alerts(child["id"], since)}),
    )


_MARKDOWN = re.compile(r"\*\*|__|^#{1,6}\s*|^\s*[-*•]\s+", re.MULTILINE)
_EMOJI = re.compile("[\U0001f000-\U0001faff\u2600-\u27bf\ufe0f]")


def clean_advice(text: str) -> str:
    """模型偶尔还是会带加粗、表情：去掉，小程序和网页都按纯文本显示。"""
    return _EMOJI.sub("", _MARKDOWN.sub("", text)).strip()


def split_plan_idea(text: str) -> tuple[str, str | None]:
    """把回答末尾的「探索计划建议：……」拆出来，给页面做成一个按钮。"""
    match = PLAN_LINE.search(text)
    if not match:
        return clean_advice(text), None
    return clean_advice(PLAN_LINE.sub("", text)), clean_advice(match.group(1))


# ---- 好奇心周报 ----


def week_start(d: date) -> date:
    return d - timedelta(days=d.weekday())


def week_range(start: date, tz: str) -> tuple[float, float]:
    zone = ZoneInfo(tz)
    begin = datetime(start.year, start.month, start.day, tzinfo=zone)
    return begin.timestamp(), (begin + timedelta(days=7)).timestamp()


def week_data(ps: ParentStore, child_id: str, start: float, end: float) -> dict:
    sessions = ps.sessions_between(child_id, start, end)
    messages = ps.messages_of([s["id"] for s in sessions])
    child_lines = [m["text"] for m in messages if m["role"] == "user" and not m["blocked"]]
    progress = [w for w in ps.word_progress(child_id) if start <= w["updated_at"] < end]
    return {
        "stats": {
            "sessions": len(sessions),
            "minutes": round(sum(max(s["last_at"] - s["started_at"], 0) for s in sessions) / 60),
            "child_turns": len(child_lines),
            "questions": 0,
            "words_heard": [w["word"] for w in progress if w["status"] == HEARD],
            "words_said": [w["word"] for w in progress if w["status"] == SAID],
        },
        "questions": [q["text"] for q in ps.s.questions_between(child_id, start, end)],
        "lines": [t for t in child_lines if len(t) >= 4][-40:],
    }


def weekly_input(child: dict, profile: dict, data: dict) -> str:
    s = data["stats"]
    parts = [
        f"孩子：{child['name']}，{child['age']} 岁",
        f"这周：聊了 {s['sessions']} 次、约 {s['minutes']} 分钟，孩子说了 {s['child_turns']} 句，问了 {len(data['questions'])} 个问题",
        "这周的问题：" + ("；".join(data["questions"]) or "（没有）"),
        "这周说过的一些原话：" + ("；".join(f"「{t}」" for t in data["lines"]) or "（没有）"),
    ]
    if s["words_said"] or s["words_heard"]:
        parts.append(f"英文：会说了 {', '.join(s['words_said']) or '无'}；听过 {', '.join(s['words_heard']) or '无'}")
    if any(profile.values()):
        parts.append("家庭教育理念：" + "；".join(f"{k}：{v}" for k, v in profile.items() if v))
    return "\n".join(parts)


def parse_weekly(text: str) -> dict:
    match = re.search(r"\{.*\}", text, re.DOTALL)
    data = json.loads(match.group(0)) if match else {}
    out = {
        "summary": str(data.get("summary", "")).strip(),
        "topics": [
            {"topic": str(t.get("topic", "")), "questions": [str(q) for q in t.get("questions", [])][:4]}
            for t in data.get("topics", []) if isinstance(t, dict) and t.get("topic")
        ][:6],
        "highlights": [
            {"quote": str(h.get("quote", "")), "why": str(h.get("why", ""))}
            for h in data.get("highlights", []) if isinstance(h, dict) and h.get("quote")
        ][:3],
        "notice": [str(n) for n in data.get("notice", []) if n][:2],
        "next": [
            {"idea": str(n.get("idea", "")), "plan_idea": str(n.get("plan_idea", ""))}
            for n in data.get("next", []) if isinstance(n, dict) and n.get("idea")
        ][:2],
    }
    if not out["summary"]:
        raise ValueError("周报缺少 summary")
    return out
