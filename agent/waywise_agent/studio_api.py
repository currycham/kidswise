"""开发者平台 API：/api/studio/*（软件文档第七章）。

它就是主题 JSON（软件文档 4.3）的一个表单：登录 → 从模板或空白建主题 → 预览 → 发布。
登录：家长在小程序里拿一个 6 位「电脑登录码」，在 waywise.tech/studio 输入（电脑上没法直接用微信身份）。
"""

import time
from collections import defaultdict

from fastapi import APIRouter, Depends, HTTPException, Request
from pydantic import BaseModel, Field, ValidationError, field_validator

from .auth import issue_token
from .config import Settings, get_settings
from .llm import LLMClient, LLMError
from .parent_api import current_family, get_parent_store
from .persona import ChildProfile, build_system_prompt
from .safety import check_input, safe_stream
from .store import ParentStore, StudioStore
from .themes import Theme, Word, load_templates

router = APIRouter(prefix="/api/studio")

MAX_CODE_FAILURES = 10  # 同一 IP 10 分钟内输错 10 次就锁住，防止暴力试 6 位码
_failures: dict[str, list[float]] = defaultdict(list)


def get_studio_store(settings: Settings = Depends(get_settings)) -> StudioStore:
    from .app import get_runtime

    return StudioStore(get_runtime(settings).store)


def get_llm(settings: Settings = Depends(get_settings)) -> LLMClient:
    from .app import get_llm as app_get_llm

    return app_get_llm(settings)


# ---- 登录 ----


@router.post("/login-code")
def new_login_code(family_id: str = Depends(current_family), ss: StudioStore = Depends(get_studio_store)):
    """小程序调用：给当前家长一个 5 分钟有效、一次性的电脑登录码。"""
    return {"code": ss.create_login_code(family_id), "expires_in": StudioStore.CODE_TTL_S}


class CodeIn(BaseModel):
    code: str = Field(pattern=r"^\d{6}$")


@router.post("/login")
def login_with_code(
    body: CodeIn,
    request: Request,
    settings: Settings = Depends(get_settings),
    ss: StudioStore = Depends(get_studio_store),
    ps: ParentStore = Depends(get_parent_store),
):
    ip = request.headers.get("x-forwarded-for", request.client.host if request.client else "?").split(",")[0].strip()
    now = time.time()
    _failures[ip] = [t for t in _failures[ip] if now - t < 600]
    if len(_failures[ip]) >= MAX_CODE_FAILURES:
        raise HTTPException(status_code=429, detail="试错次数太多，请 10 分钟后再试")
    family_id = ss.consume_login_code(body.code)
    if not family_id:
        _failures[ip].append(now)
        raise HTTPException(status_code=400, detail="登录码不对或已过期，请在小程序里重新获取")
    return {"token": issue_token(settings, family_id), "children": ps.children(family_id)}


# ---- 模板与主题 ----


class ThemeDraft(BaseModel):
    """编辑中的主题。theme_id 由服务端分配。"""

    name: str = Field(min_length=1, max_length=20)
    level: int = Field(default=1, ge=1, le=3)
    words: list[Word] = Field(min_length=3, max_length=30)
    patterns: list[str] = Field(default=[], max_length=10)
    scene_hint: str = Field(default="", max_length=100)

    @field_validator("patterns")
    @classmethod
    def short_patterns(cls, v: list[str]) -> list[str]:
        v = [p.strip() for p in v if p.strip()]
        if any(len(p) > 80 for p in v):
            raise ValueError("每个句式不超过 80 个字符")
        return v

    def to_theme(self, theme_id: str = "draft") -> Theme:
        try:
            return Theme(theme_id=theme_id, **self.model_dump())
        except ValidationError as e:
            raise HTTPException(status_code=422, detail=e.errors()[0]["msg"])


@router.get("/templates")
def templates(_: str = Depends(current_family)):
    return {"templates": [t.model_dump() for t in load_templates().values()]}


@router.get("/themes")
def my_themes(family_id: str = Depends(current_family), ss: StudioStore = Depends(get_studio_store)):
    return {"themes": ss.themes(family_id)}


@router.post("/themes")
def create_theme(body: ThemeDraft, family_id: str = Depends(current_family), ss: StudioStore = Depends(get_studio_store)):
    body.to_theme()  # 校验
    return ss.create_theme(family_id, body.model_dump())


def _own_theme(theme_id: str, family_id: str = Depends(current_family), ss: StudioStore = Depends(get_studio_store)) -> str:
    if not ss.owns_theme(family_id, theme_id):
        raise HTTPException(status_code=404, detail="没有这个主题")
    return theme_id


@router.put("/themes/{theme_id}")
def update_theme(body: ThemeDraft, theme_id: str = Depends(_own_theme), ss: StudioStore = Depends(get_studio_store)):
    body.to_theme(theme_id)
    return ss.update_theme(theme_id, body.model_dump())


@router.delete("/themes/{theme_id}")
def delete_theme(theme_id: str = Depends(_own_theme), ss: StudioStore = Depends(get_studio_store)):
    ss.delete_theme(theme_id)
    return {"ok": True}


class ApplyIn(BaseModel):
    child_id: str


@router.post("/themes/{theme_id}/apply")
def apply_theme(
    body: ApplyIn,
    theme_id: str = Depends(_own_theme),
    family_id: str = Depends(current_family),
    ps: ParentStore = Depends(get_parent_store),
):
    """发布：设为孩子当前主题。下一句话起 Mumi 就按这个主题教（软件文档 7.1）。"""
    if not ps.owns_child(family_id, body.child_id):
        raise HTTPException(status_code=404, detail="没有这个孩子")
    ps.update_child(body.child_id, None, None, theme_id)
    return {"ok": True, "child_id": body.child_id, "theme_id": theme_id}


# ---- 预览：平台的灵魂 ----


class Turn(BaseModel):
    role: str = Field(pattern=r"^(child|mumi)$")
    text: str = Field(min_length=1, max_length=200)


class PreviewIn(BaseModel):
    theme: ThemeDraft
    child_id: str | None = None
    turns: list[Turn] = Field(default=[], max_length=12)


@router.post("/preview")
async def preview(
    body: PreviewIn,
    family_id: str = Depends(current_family),
    settings: Settings = Depends(get_settings),
    ps: ParentStore = Depends(get_parent_store),
    llm: LLMClient = Depends(get_llm),
):
    """用编辑中的主题直接和 Mumi 说几句，看它会怎么教。不落库、不影响孩子的记录。

    turns 为空时模拟孩子刚打开 Mumi 说「你好」，也就是「它会怎么开口」。
    """
    theme = body.theme.to_theme()
    child = next((c for c in ps.children(family_id) if c["id"] == body.child_id), None)
    profile = ChildProfile(
        name=child["name"] if child else settings.default_child_name,
        age=child["age"] if child else settings.default_child_age,
    )
    statuses = ps.s.word_statuses(child["id"]) if child else {}
    turns = body.turns or [Turn(role="child", text="你好")]
    if turns[-1].role != "child":
        raise HTTPException(status_code=400, detail="最后一句应该是孩子说的")

    if verdict := check_input(turns[-1].text):
        return {"reply": verdict.reply, "blocked": verdict.category}

    messages = [{"role": "system", "content": build_system_prompt(profile, theme, statuses, session_start=True)}]
    messages += [{"role": "user" if t.role == "child" else "assistant", "content": t.text} for t in turns]
    try:
        reply = "".join([s async for s in safe_stream(llm.stream(messages, max_tokens=settings.llm_max_tokens))])
    except LLMError:
        raise HTTPException(status_code=502, detail="Mumi 暂时没有回应，请稍后再试")
    return {"reply": reply.strip(), "blocked": None}
