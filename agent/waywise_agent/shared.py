"""别的家长在做什么：家长之间可以借用的探索计划。

还没有真实家长分享之前，这里放的是 Waywise 团队写的示例计划（author 写明「示例」），
不编造家长和孩子的反应：reaction 只在真实家长分享、自己审过之后才有。

家长不用会搜索：默认用孩子最近问的问题去匹配，只给最相关的几个，不做信息流。
"""

import json
from dataclasses import dataclass, field
from functools import lru_cache
from pathlib import Path

from .themes import Theme, load_templates

SHARED_DIR = Path(__file__).resolve().parent.parent / "data" / "shared"


@dataclass(frozen=True)
class SharedPlan:
    id: str
    plan: Theme
    author: str
    why: str
    keywords: tuple[str, ...]
    ages: tuple[int, int] = (3, 6)
    reaction: str = ""   # 孩子的真实反应：只来自真实分享

    def card(self, reason: str = "") -> dict:
        return {"id": self.id, "name": self.plan.name, "domain": self.plan.domain, "goal": self.plan.goal,
                "author": self.author, "why": self.why, "reaction": self.reaction, "reason": reason}


@lru_cache
def load_shared(directory: Path = SHARED_DIR) -> dict[str, SharedPlan]:
    templates = load_templates()
    out: dict[str, SharedPlan] = {}
    for path in sorted(directory.glob("*.json")):
        d = json.loads(path.read_text(encoding="utf-8"))
        if d["id"] != path.stem:
            raise ValueError(f"{path.name}: id {d['id']!r} must match file name")
        if "template" in d:
            plan = templates[d["template"]]
        else:
            plan = Theme.model_validate(d["plan"])
        if not plan.is_exploration:
            raise ValueError(f"{path.name}: shared plans are exploration plans")
        out[d["id"]] = SharedPlan(id=d["id"], plan=plan, author=d["author"], why=d["why"],
                                  keywords=tuple(d["keywords"]), ages=tuple(d.get("ages", (3, 6))),
                                  reaction=d.get("reaction", ""))
    return out


@dataclass
class _Scored:
    plan: SharedPlan
    score: float
    reason: str = ""
    order: int = field(default=0)


def match(child_name: str | None, age: int | None, questions: list[str], facts: list[str], limit: int = 6) -> list[dict]:
    """按孩子最近问的问题（其次是最近的经历、年龄）挑出最相关的几个；都不沾边时按默认顺序给。"""
    who = child_name or "孩子"
    scored = []
    for i, sp in enumerate(load_shared().values()):
        s = _Scored(sp, 0.0, order=i)
        hit_q = [q for q in questions if any(k in q for k in sp.keywords)]
        hit_f = [f for f in facts if any(k in f for k in sp.keywords)]
        s.score = 3 * len(hit_q) + len(hit_f)
        if age is not None and sp.ages[0] <= age <= sp.ages[1]:
            s.score += 0.5
        if hit_q:
            s.reason = f"{who}最近问过「{_short(hit_q[0])}」"
        elif hit_f:
            s.reason = f"{who}最近聊到过「{_short(hit_f[0])}」"
        scored.append(s)
    scored.sort(key=lambda s: (-s.score, s.order))
    return [s.plan.card(s.reason) for s in scored[:limit]]


def _short(text: str, n: int = 18) -> str:
    text = text.strip().rstrip("？?。！!")
    return text if len(text) <= n else text[:n] + "…"
