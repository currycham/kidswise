"""家长端（小程序）API：/api/*，经 https://api.waywise.tech 对外。

软件文档第六章：四个页面全部只读（首页 / 对话记录 / 成长 / 我的主题），
外加登录、设备配对和孩子档案（昵称、年龄、主题）这几个必要的写操作。
"""

import logging
import re
import time
from datetime import date, datetime, timedelta
from zoneinfo import ZoneInfo

from fastapi import APIRouter, Depends, Header, HTTPException
from pydantic import BaseModel, Field

from .auth import AuthError, code_to_openid, issue_token, verify_token
from .config import Settings, get_settings
from .store import NEW, BindError, ParentStore
from .themes import load_themes

log = logging.getLogger(__name__)

router = APIRouter(prefix="/api")

DEVICE_ID = re.compile(r"^[A-Za-z0-9:_\-]{4,64}$")


def get_parent_store(settings: Settings = Depends(get_settings)) -> ParentStore:
    from .app import get_runtime  # 避免循环导入：app 在导入时挂载本路由

    return ParentStore(get_runtime(settings).store)


def current_family(authorization: str = Header(default=""), settings: Settings = Depends(get_settings)) -> str:
    try:
        return verify_token(settings, authorization.removeprefix("Bearer ").strip())
    except (AuthError, ValueError, KeyError):
        raise HTTPException(status_code=401, detail="请重新登录")


def own_child(child_id: str, family_id: str = Depends(current_family), ps: ParentStore = Depends(get_parent_store)) -> str:
    if not ps.owns_child(family_id, child_id):
        raise HTTPException(status_code=404, detail="没有这个孩子")
    return child_id


def _day_range(settings: Settings, day: date | None = None) -> tuple[float, float]:
    tz = ZoneInfo(settings.timezone)
    day = day or datetime.now(tz).date()
    start = datetime(day.year, day.month, day.day, tzinfo=tz)
    return start.timestamp(), (start + timedelta(days=1)).timestamp()


def _zh_of() -> dict[str, str]:
    table: dict[str, str] = {}
    for theme in load_themes().values():
        for w in theme.words:
            table.setdefault(w.en, w.zh)
    return table


# ---- 登录 ----


class LoginIn(BaseModel):
    code: str = Field(min_length=1, max_length=128)


class DevLoginIn(BaseModel):
    openid: str = Field(min_length=1, max_length=64)


def _login_result(settings: Settings, ps: ParentStore, openid: str) -> dict:
    family_id = ps.family_for_openid(openid)
    return {"token": issue_token(settings, family_id), "children": ps.children(family_id)}


@router.post("/auth/login")
async def login(body: LoginIn, settings: Settings = Depends(get_settings), ps: ParentStore = Depends(get_parent_store)):
    try:
        openid = await code_to_openid(settings, body.code)
    except AuthError as e:
        raise HTTPException(status_code=400, detail=str(e))
    return _login_result(settings, ps, openid)


@router.post("/auth/dev-login")
def dev_login(body: DevLoginIn, settings: Settings = Depends(get_settings), ps: ParentStore = Depends(get_parent_store)):
    """联调用：不经过微信直接登录。只有 .env 里 DEV_LOGIN=true 时可用，演示环境要关掉。"""
    if not settings.dev_login:
        raise HTTPException(status_code=404)
    return _login_result(settings, ps, f"dev:{body.openid}")


# ---- 设备与孩子 ----


class BindIn(BaseModel):
    device_id: str


@router.post("/devices")
def bind_device(
    body: BindIn,
    family_id: str = Depends(current_family),
    settings: Settings = Depends(get_settings),
    ps: ParentStore = Depends(get_parent_store),
):
    device_id = body.device_id.strip()
    if not DEVICE_ID.match(device_id):
        raise HTTPException(status_code=400, detail="设备码格式不对")
    try:
        child_id = ps.bind_device(
            family_id, device_id, settings.default_child_name, settings.default_child_age, settings.default_theme_id
        )
    except BindError as e:
        raise HTTPException(status_code=409, detail=str(e))
    return {"child_id": child_id, "children": ps.children(family_id)}


@router.get("/children")
def list_children(family_id: str = Depends(current_family), ps: ParentStore = Depends(get_parent_store)):
    return {"children": ps.children(family_id)}


class ChildIn(BaseModel):
    name: str | None = Field(default=None, min_length=1, max_length=12)
    age: int | None = Field(default=None, ge=2, le=10)
    theme_id: str | None = None


@router.patch("/children/{child_id}")
def update_child(
    body: ChildIn,
    child_id: str = Depends(own_child),
    family_id: str = Depends(current_family),
    ps: ParentStore = Depends(get_parent_store),
):
    if body.theme_id is not None and body.theme_id not in load_themes():
        from .store import StudioStore

        if not StudioStore(ps.s).owns_theme(family_id, body.theme_id):
            raise HTTPException(status_code=400, detail="没有这个主题")
    ps.update_child(child_id, body.name, body.age, body.theme_id)
    return next(c for c in ps.children(family_id) if c["id"] == child_id)


# ---- 家长对孩子数据的控制：视觉开关、看过什么、导出、删除 ----


class VisionIn(BaseModel):
    enabled: bool


@router.get("/children/{child_id}/vision")
def get_vision(
    child_id: str = Depends(own_child),
    settings: Settings = Depends(get_settings),
    ps: ParentStore = Depends(get_parent_store),
):
    """木米的眼睛：开关状态 + 最近 30 天看过什么（时间、孩子当时说的话、看到了什么的文字描述）。"""
    return {"enabled": ps.s.vision_enabled(child_id),
            "seen": ps.s.photo_events(child_id, since=time.time() - 30 * 86400)}


@router.put("/children/{child_id}/vision")
def put_vision(body: VisionIn, child_id: str = Depends(own_child), ps: ParentStore = Depends(get_parent_store)):
    ps.s.set_vision(child_id, body.enabled)
    return {"enabled": body.enabled}


@router.get("/children/{child_id}/export")
def export_child(child_id: str = Depends(own_child), family_id: str = Depends(current_family),
                 ps: ParentStore = Depends(get_parent_store)):
    child = next(c for c in ps.children(family_id) if c["id"] == child_id)
    return {"child": {"name": child["name"], "age": child["age"]}, "exported_at": time.time(),
            **ps.s.export_child_data(child_id)}


class DeleteIn(BaseModel):
    confirm: str  # 必须是孩子的昵称，防止误删


@router.post("/children/{child_id}/delete-data")
def delete_child_data(body: DeleteIn, child_id: str = Depends(own_child), family_id: str = Depends(current_family),
                      ps: ParentStore = Depends(get_parent_store)):
    """删除这个孩子的全部聊天与成长数据，不可恢复。设备绑定保留，木米还能继续用。"""
    child = next(c for c in ps.children(family_id) if c["id"] == child_id)
    if body.confirm.strip() != child["name"]:
        raise HTTPException(status_code=400, detail="请输入孩子的昵称确认删除")
    ps.s.delete_child_data(child_id)
    log.warning("parent deleted all data of child=%s family=%s", child_id, family_id)
    return {"deleted": True}


@router.get("/themes")
def list_themes(family_id: str = Depends(current_family), ps: ParentStore = Depends(get_parent_store)):
    """内置主题 + 本家庭在开发者平台自建的主题（custom=true，排在前面）。"""
    from .store import StudioStore

    def card(t: dict, custom: bool) -> dict:
        return {"id": t["theme_id"], "name": t["name"], "level": t["level"], "word_count": len(t["words"]),
                "sample": [w["en"] for w in t["words"][:4]], "custom": custom,
                "domain": t.get("domain", "英语"), "goal": t.get("goal", "")}

    mine = [card(t, True) for t in StudioStore(ps.s).themes(family_id)]
    builtin = [card(t.model_dump(), False) for t in load_themes().values()]
    return {"themes": mine + builtin}


# ---- 四个只读页面 ----


@router.get("/children/{child_id}/today")
def today(
    child_id: str = Depends(own_child),
    settings: Settings = Depends(get_settings),
    ps: ParentStore = Depends(get_parent_store),
):
    """首页：今天聊了多久、学了哪几个词、一句孩子的原话（软件文档 6.2）。"""
    start, end = _day_range(settings)
    sessions = ps.sessions_between(child_id, start, end)
    messages = ps.messages_of([s["id"] for s in sessions])
    seconds = sum(max(s["last_at"] - s["started_at"], 0) for s in sessions)
    child_lines = [m for m in messages if m["role"] == "user" and not m["blocked"]]

    zh = _zh_of()
    statuses = {w["word"]: w["status"] for w in ps.word_progress(child_id)}
    words: list[str] = []
    for m in messages:
        for w in m["words"]:
            if w not in words:
                words.append(w)

    # 「孩子的原话」不需要算法：挑当天最长的一句孩子发言
    quote = max(child_lines, key=lambda m: len(m["text"]), default=None)
    return {
        "date": datetime.fromtimestamp(start, ZoneInfo(settings.timezone)).date().isoformat(),
        "minutes": round(seconds / 60) if seconds >= 60 else (1 if sessions else 0),
        "sessions": len(sessions),
        "child_turns": len(child_lines),
        "words": [{"en": w, "zh": zh.get(w, ""), "status": statuses.get(w, NEW)} for w in words],
        "quote": {"text": quote["text"], "at": quote["created_at"]} if quote else None,
        "alerts": ps.alerts(child_id, start),
    }


@router.get("/children/{child_id}/sessions")
def sessions_of_day(
    day: date | None = None,
    child_id: str = Depends(own_child),
    settings: Settings = Depends(get_settings),
    ps: ParentStore = Depends(get_parent_store),
):
    """对话记录：按天翻，完整逐句。day 形如 2026-09-26，缺省为今天。"""
    start, end = _day_range(settings, day)
    sessions = ps.sessions_between(child_id, start, end)
    messages = ps.messages_of([s["id"] for s in sessions])
    return {
        "date": datetime.fromtimestamp(start, ZoneInfo(settings.timezone)).date().isoformat(),
        "sessions": [
            {**s, "messages": [
                {"role": "child" if m["role"] == "user" else "mumi", "text": m["text"], "at": m["created_at"],
                 "blocked": m["blocked"]}
                for m in messages if m["session_id"] == s["id"]
            ]}
            for s in sessions
        ],
    }


@router.get("/children/{child_id}/progress")
def progress(child_id: str = Depends(own_child), ps: ParentStore = Depends(get_parent_store)):
    """成长：这周的词 + 三档状态（软件文档 4.4）。"""
    zh = _zh_of()
    week_ago = time.time() - 7 * 24 * 3600
    words = [
        {"en": w["word"], "zh": zh.get(w["word"], ""), "status": w["status"], "this_week": w["updated_at"] >= week_ago}
        for w in ps.word_progress(child_id)
    ]
    counts = {status: sum(w["status"] == status for w in words) for status in ("heard", "said")}
    return {"counts": counts, "words": words}
