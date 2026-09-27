"""顾问对话与好奇心周报 API（家长教育 Agent 第二步）。

网页（waywise.tech/studio）和小程序用同一个家长令牌调用：
  /api/advisor/*               顾问对话（网页用流式，小程序用一次性返回）
  /api/children/{id}/weekly    好奇心周报
"""

import json
import logging
import time
from datetime import date, datetime
from zoneinfo import ZoneInfo

from fastapi import APIRouter, Depends, HTTPException
from fastapi.responses import StreamingResponse
from pydantic import BaseModel, Field

from .advisor import (
    ADVISOR_PROMPT,
    WEEKLY_PROMPT,
    parse_weekly,
    snapshot,
    split_plan_idea,
    week_data,
    week_range,
    week_start,
    weekly_input,
)
from .config import Settings, get_settings
from .llm import LLMClient, LLMError
from .parent_api import current_family, get_parent_store
from .store import ParentStore

log = logging.getLogger(__name__)
router = APIRouter(prefix="/api")

HISTORY_TURNS = 12  # 顾问能看到的最近几轮对话
REPORT_FRESH_S = 3600  # 本周周报一小时内不重复生成


def get_advisor_llm(settings: Settings = Depends(get_settings)) -> LLMClient:
    model = settings.designer_model or settings.llm_model
    return LLMClient(settings.model_copy(update={"llm_model": model, "llm_timeout_s": 40}))


def _child(ps: ParentStore, family_id: str, child_id: str | None) -> dict | None:
    if not child_id:
        return None
    child = next((c for c in ps.children(family_id) if c["id"] == child_id), None)
    if not child:
        raise HTTPException(status_code=404, detail="没有这个孩子")
    return child


def _current_plan(ps: ParentStore, child: dict, settings: Settings) -> dict | None:
    from .app import resolve_theme

    theme = resolve_theme(ps.s, child.get("theme_id"), settings.default_theme_id)
    return theme.model_dump() if theme else None


# ---- 顾问 ----


class AskIn(BaseModel):
    text: str = Field(min_length=1, max_length=800)
    child_id: str | None = None
    stream: bool = False


@router.get("/advisor/history")
def history(child_id: str | None = None, family_id: str = Depends(current_family), ps: ParentStore = Depends(get_parent_store)):
    _child(ps, family_id, child_id)
    rows = ps.s.advisor_history(family_id, child_id)
    out = []
    for r in rows:
        text, plan_idea = split_plan_idea(r["text"]) if r["role"] == "advisor" else (r["text"], None)
        out.append({"role": r["role"], "text": text, "plan_idea": plan_idea, "at": r["created_at"]})
    return {"messages": out}


@router.delete("/advisor/history")
def clear_history(child_id: str | None = None, family_id: str = Depends(current_family), ps: ParentStore = Depends(get_parent_store)):
    _child(ps, family_id, child_id)
    ps.s.clear_advisor_history(family_id, child_id)
    return {"ok": True}


@router.post("/advisor/chat")
async def chat(
    body: AskIn,
    family_id: str = Depends(current_family),
    settings: Settings = Depends(get_settings),
    ps: ParentStore = Depends(get_parent_store),
    llm: LLMClient = Depends(get_advisor_llm),
):
    child = _child(ps, family_id, body.child_id)
    if child:
        context = snapshot(ps, child, family_id, _current_plan(ps, child, settings)).as_context()
    else:
        profile = {k: v for k, v in ps.s.family_profile(family_id).items() if v}
        context = "还没有绑定设备，没有孩子的聊天数据。" + (f"家庭教育理念：{profile}" if profile else "")
    past = ps.s.advisor_history(family_id, body.child_id, limit=HISTORY_TURNS * 2)
    messages = [{"role": "system", "content": f"{ADVISOR_PROMPT}\n\n关于这个孩子：\n{context}"}]
    messages += [{"role": "user" if m["role"] == "parent" else "assistant", "content": m["text"]} for m in past]
    messages.append({"role": "user", "content": body.text})
    ps.s.add_advisor_message(family_id, body.child_id, "parent", body.text)

    def finish(full: str) -> dict:
        ps.s.add_advisor_message(family_id, body.child_id, "advisor", full)
        text, plan_idea = split_plan_idea(full)
        return {"text": text, "plan_idea": plan_idea}

    if not body.stream:
        try:
            full = "".join([c async for c in llm.stream(messages, max_tokens=900, temperature=0.6)])
        except LLMError:
            raise HTTPException(status_code=502, detail="顾问暂时没有回应，请稍后再试")
        return finish(full)

    async def sse():
        full = ""
        try:
            async for chunk in llm.stream(messages, max_tokens=900, temperature=0.6):
                full += chunk
                yield f"data: {json.dumps({'delta': chunk}, ensure_ascii=False)}\n\n"
        except LLMError:
            yield f"data: {json.dumps({'error': '顾问暂时没有回应，请稍后再试'}, ensure_ascii=False)}\n\n"
            return
        yield f"data: {json.dumps({'done': True, **finish(full)}, ensure_ascii=False)}\n\n"

    return StreamingResponse(sse(), media_type="text/event-stream", headers={"Cache-Control": "no-cache"})


# ---- 好奇心周报 ----


@router.get("/children/{child_id}/weekly")
async def weekly(
    child_id: str,
    week: date | None = None,
    family_id: str = Depends(current_family),
    settings: Settings = Depends(get_settings),
    ps: ParentStore = Depends(get_parent_store),
    llm: LLMClient = Depends(get_advisor_llm),
):
    """这一周（周一开始）孩子在想什么。只描述，不打分、不比较。week 缺省为本周。"""
    child = _child(ps, family_id, child_id)
    start = week_start(week or datetime.now(ZoneInfo(settings.timezone)).date())
    begin, end = week_range(start, settings.timezone)
    data = week_data(ps, child_id, begin, end)
    data["stats"]["questions"] = len(data["questions"])
    base = {"week_start": start.isoformat(), "child": {"name": child["name"]}, "stats": data["stats"]}
    if not data["stats"]["sessions"]:
        return {**base, "empty": True, "summary": f"这周{child['name']}还没有和 Mumi 聊天。"}

    now = time.time()
    if cached := ps.s.weekly_report(child_id, start.isoformat()):
        report, made_at = cached
        stale = end > now and made_at < ps.s.last_activity(child_id) and now - made_at > REPORT_FRESH_S
        if not stale:
            return {**report, **base}

    messages = [{"role": "system", "content": WEEKLY_PROMPT},
                {"role": "user", "content": weekly_input(child, ps.s.family_profile(family_id), data)}]
    for _ in range(2):
        try:
            text = "".join([c async for c in llm.stream(messages, max_tokens=1200, temperature=0.5)])
            report = parse_weekly(text)
            break
        except LLMError:
            raise HTTPException(status_code=502, detail="周报暂时生成不了，请稍后再试")
        except (ValueError, json.JSONDecodeError, AttributeError) as e:
            log.warning("weekly report invalid: %s", e)
    else:
        raise HTTPException(status_code=502, detail="周报这次没写好，请稍后再试")
    ps.s.save_weekly_report(child_id, start.isoformat(), report)
    return {**report, **base, "empty": False}
