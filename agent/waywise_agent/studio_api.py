"""开发者平台 API：/api/studio/*（软件文档第七章）。

它就是主题 JSON（软件文档 4.3）的一个表单：登录 → 从模板或空白建主题 → 预览 → 发布。
登录：家长在小程序里拿一个 6 位「电脑登录码」，在 waywise.tech/studio 输入（电脑上没法直接用微信身份）。
"""

import json
import logging
import re
import time
from collections import defaultdict

from fastapi import APIRouter, Depends, HTTPException, Request
from pydantic import BaseModel, Field, ValidationError, field_validator

from .auth import issue_token
from .config import Settings, get_settings
from .llm import LLMClient, LLMError
from .parent_api import current_family, get_parent_store
from .persona import ChildProfile, FamilyValues, build_system_prompt
from .safety import check_input, safe_stream
from .store import ParentStore, StudioStore
from .shared import load_shared, match
from .themes import Idea, Move, Theme, Word, load_templates

log = logging.getLogger(__name__)

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
    """编辑中的主题 / 探索计划。theme_id 由服务端分配。

    英语主题：至少 3 个词；探索计划：至少一个要点或探索动作，英文词可选。
    """

    name: str = Field(min_length=1, max_length=20)
    level: int = Field(default=1, ge=1, le=3)
    domain: str = Field(default="英语", max_length=10)
    goal: str = Field(default="", max_length=120)
    ideas: list[Idea] = Field(default=[], max_length=8)
    moves: list[Move] = Field(default=[], max_length=8)
    words: list[Word] = Field(default=[], max_length=30)
    patterns: list[str] = Field(default=[], max_length=10)
    scene_hint: str = Field(default="", max_length=100)
    avoid: str = Field(default="", max_length=120)

    @field_validator("patterns")
    @classmethod
    def short_patterns(cls, v: list[str]) -> list[str]:
        v = [p.strip() for p in v if p.strip()]
        if any(len(p) > 80 for p in v):
            raise ValueError("每个句式不超过 80 个字符")
        return v

    def to_theme(self, theme_id: str = "draft") -> Theme:
        if not (self.ideas or self.moves) and len(self.words) < 3:
            raise HTTPException(status_code=422, detail="英语主题至少写 3 个词；或者加上要点、探索动作，做成探索计划")
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


# ---- 家庭教育理念档案 & 孩子的问题 ----


class ProfileIn(BaseModel):
    values: str = Field(default="", max_length=300)
    avoid: str = Field(default="", max_length=300)
    language: str = Field(default="", max_length=200)
    child_traits: str = Field(default="", max_length=300)


@router.get("/profile")
def get_profile(family_id: str = Depends(current_family), ss: StudioStore = Depends(get_studio_store)):
    return ProfileIn(**ss.s.family_profile(family_id)).model_dump()


@router.put("/profile")
def put_profile(body: ProfileIn, family_id: str = Depends(current_family), ss: StudioStore = Depends(get_studio_store)):
    ss.s.save_family_profile(family_id, body.model_dump())
    return body.model_dump()


@router.get("/questions")
def child_questions(child_id: str, family_id: str = Depends(current_family), ps: ParentStore = Depends(get_parent_store)):
    """孩子最近问的问题：好奇心地图的原料，也给设计师当素材。"""
    if not ps.owns_child(family_id, child_id):
        raise HTTPException(status_code=404, detail="没有这个孩子")
    return {"questions": ps.s.recent_questions(child_id)}


# ---- 设计师：家长一句话 → 探索计划草稿 ----

DESIGNER_PROMPT = """你是 Waywise 的「探索计划设计师」。家长会用一句话说出想让孩子学什么、玩什么，
你把它变成一个 3–6 岁孩子能在日常聊天里玩起来的探索计划。孩子身边的小伙伴 Mumi 会照着它陪孩子聊。

原则：
- 好奇心优先：重在让孩子愿意猜、愿意问、愿意动手，而不是记住答案；不出题、不考试、不做练习册式的内容
- 贴合年龄：要点用孩子听得懂的说法（kid_words），超出年龄的概念写进 avoid
- 探索动作要具体、能在家里或路上随手做：猜一猜、找一找、数一数、动手试、连一连、讲给我听、比一比
- 贴合这个孩子：用上他感兴趣的东西和他最近问过的问题；只用上面给出的孩子信息，不要编造他没有的宠物、家人、经历
- 照着这个家庭的教育理念来；理念里不想要的，计划里一定不要出现
- Mumi 是一个不能走动、没有手、看不见（除非孩子让它拍照）的小玩具：需要动手的事让孩子自己做，或者和爸爸妈妈一起做；Mumi 只负责说
- 安全：不涉及危险操作；需要大人参与的动作写明「和爸爸妈妈一起」
- 英文词可选：和主题自然相关时给 2–6 个（中英对照），家长明确说学英语时给 8–15 个；不相关就给空数组

只输出一个 JSON 对象，不要任何其他文字，字段如下：
{"name": "不超过 12 个字的名字",
 "domain": "数学/自然/地理/生活/情绪/社交/语言/艺术/其他 之一",
 "level": 1到3的整数（1 适合 3–4 岁，2 适合 5 岁，3 适合 6 岁以上）,
 "goal": "一句话：希望孩子能做到什么、愿意想什么",
 "ideas": [{"idea": "要点", "kid_words": "孩子听得懂的说法"}],   // 2–4 个
 "moves": [{"type": "猜一猜", "prompt": "Mumi 可以怎么问或邀请孩子"}],   // 3–5 个
 "words": [{"en": "earth", "zh": "地球", "difficulty": 1}],
 "scene_hint": "什么时候适合聊这个",
 "avoid": "不要讲什么、不要怎么做"}"""


class DraftIn(BaseModel):
    idea: str = Field(min_length=2, max_length=300)
    child_id: str | None = None


def _designer_llm(settings: Settings) -> LLMClient:
    if settings.designer_model:
        return LLMClient(settings.model_copy(update={"llm_model": settings.designer_model, "llm_timeout_s": 30}))
    return LLMClient(settings.model_copy(update={"llm_timeout_s": 30}))


def _parse_plan(text: str) -> dict:
    match = re.search(r"\{.*\}", text, re.DOTALL)
    if not match:
        raise ValueError("没有找到 JSON")
    return json.loads(match.group(0))


def get_designer(settings: Settings = Depends(get_settings)) -> LLMClient:
    return _designer_llm(settings)


def _child_context(family_id: str, child_id: str | None, ps: ParentStore) -> tuple[dict | None, list[str]]:
    """设计师要知道的：孩子是谁、最近在想什么，以及这个家的理念。"""
    child = next((c for c in ps.children(family_id) if c["id"] == child_id), None)
    lines = []
    if child:
        lines.append(f"孩子：{child['name']}，{child['age']} 岁")
        facts = ps.s.recent_facts(child["id"])
        if facts:
            lines.append("孩子最近感兴趣、经历过的事：" + "；".join(facts))
        questions = [q["text"] for q in ps.s.recent_questions(child["id"], limit=12)]
        if questions:
            lines.append("孩子最近问过的问题：" + "；".join(questions))
    profile = ps.s.family_profile(family_id)
    if any(profile.values()):
        lines.append("这个家庭的教育理念：" + "；".join(f"{k}：{v}" for k, v in profile.items() if v))
    return child, lines


async def _design(context: list[str], designer: LLMClient) -> dict:
    """设计师按上下文起草一个探索计划；JSON 不合规时带着错误再试一次。"""
    messages = [{"role": "system", "content": DESIGNER_PROMPT}, {"role": "user", "content": "\n".join(context)}]
    last_error = ""
    for _ in range(2):
        try:
            text = "".join([c async for c in designer.stream(messages, max_tokens=1200, temperature=0.7)])
        except LLMError:
            raise HTTPException(status_code=502, detail="设计师暂时没有回应，请稍后再试")
        try:
            draft = ThemeDraft(**_parse_plan(text))
            draft.to_theme()
            return draft.model_dump()
        except (ValueError, ValidationError, HTTPException) as e:
            last_error = getattr(e, "detail", None) or str(e)[:200]
            log.warning("designer output invalid: %s", last_error)
            messages += [{"role": "assistant", "content": text},
                         {"role": "user", "content": f"上面的 JSON 不符合要求：{last_error}。请只输出修正后的 JSON。"}]
    raise HTTPException(status_code=502, detail="这次没起草好，换个说法再试一次")


@router.post("/draft")
async def draft_plan(
    body: DraftIn,
    family_id: str = Depends(current_family),
    ps: ParentStore = Depends(get_parent_store),
    designer: LLMClient = Depends(get_designer),
):
    """家长说一句话，设计师起草一个探索计划（不保存；家长改完再保存 / 发布）。"""
    if check_input(body.idea):
        raise HTTPException(status_code=400, detail="这个想法涉及安全方面的内容，换一个试试吧")
    _, lines = _child_context(family_id, body.child_id, ps)
    return await _design([f"家长的想法：{body.idea}", *lines], designer)


# ---- 别的家长在做什么：按孩子最近的问题挑几个计划，借用并改成自己孩子的 ----

@router.get("/shared")
def shared_plans(child_id: str | None = None, limit: int = 6, family_id: str = Depends(current_family),
                 ps: ParentStore = Depends(get_parent_store)):
    child = None
    if child_id:
        if not ps.owns_child(family_id, child_id):
            raise HTTPException(status_code=404, detail="没有这个孩子")
        child = next(c for c in ps.children(family_id) if c["id"] == child_id)
    questions = [q["text"] for q in ps.s.recent_questions(child_id, limit=30)] if child else []
    facts = ps.s.recent_facts(child_id) if child else []
    plans = match(child["name"] if child else None, child["age"] if child else None, questions, facts,
                  limit=max(1, min(limit, 12)))
    return {"plans": plans}


@router.get("/shared/{shared_id}")
def shared_plan(shared_id: str, _: str = Depends(current_family)):
    sp = load_shared().get(shared_id)
    if not sp:
        raise HTTPException(status_code=404, detail="没有这个计划")
    return {**sp.card(), "plan": sp.plan.model_dump()}


class BorrowIn(BaseModel):
    shared_id: str
    child_id: str | None = None


@router.post("/borrow")
async def borrow_plan(
    body: BorrowIn,
    family_id: str = Depends(current_family),
    ps: ParentStore = Depends(get_parent_store),
    designer: LLMClient = Depends(get_designer),
):
    """借用别的家庭的一个计划：保留它的核心想法，按自己孩子的年龄、兴趣和家庭理念改写（不保存）。"""
    sp = load_shared().get(body.shared_id)
    if not sp:
        raise HTTPException(status_code=404, detail="没有这个计划")
    original = sp.plan.model_dump(exclude={"theme_id"})
    _, lines = _child_context(family_id, body.child_id, ps)
    context = [
        "家长想借用一个别的家庭做过的探索计划，改成适合自己孩子的版本。",
        "原计划：" + json.dumps(original, ensure_ascii=False),
        f"原作者的想法：{sp.why}",
        "请保留原计划的核心想法和好玩的探索动作；按这个孩子的年龄、兴趣、最近问过的问题和家庭理念改写，"
        "能用上孩子自己的问题和喜欢的东西最好；名字可以改得更贴近这个孩子。",
        *lines,
    ]
    draft = await _design(context, designer)
    return {"draft": draft, "based_on": sp.plan.name}


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

    family = FamilyValues(**{k: v for k, v in ps.s.family_profile(family_id).items() if k in FamilyValues.__dataclass_fields__})
    messages = [{"role": "system", "content": build_system_prompt(profile, theme, statuses, session_start=True, family=family)}]
    messages += [{"role": "user" if t.role == "child" else "assistant", "content": t.text} for t in turns]
    try:
        reply = "".join([s async for s in safe_stream(llm.stream(messages, max_tokens=settings.llm_max_tokens))])
    except LLMError:
        raise HTTPException(status_code=502, detail="Mumi 暂时没有回应，请稍后再试")
    return {"reply": reply.strip(), "blocked": None}
