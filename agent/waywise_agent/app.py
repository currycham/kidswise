"""对外只暴露一个 OpenAI 兼容端点（软件文档 3.3）：设备接入层以为自己在调一个 LLM。"""

import json
import logging
import re
import time
import uuid
from dataclasses import dataclass

from fastapi import Depends, FastAPI, Header, HTTPException
from fastapi.responses import StreamingResponse
from pydantic import BaseModel

from .config import Settings, get_settings
from .llm import LLMClient, LLMError
from .memory import MemoryScheduler
from .persona import ChildProfile, build_system_prompt
from .safety import Verdict, check_input, safe_stream
from .store import Child, SessionRef, Store
from .themes import Theme, find_words, get_theme

log = logging.getLogger("waywise")
app = FastAPI(title="Waywise Agent")

MAX_HISTORY = 20  # 会话内记忆：截断到最近 N 条
# xiaozhi 的 prompt 模板（deploy/xiaozhi/waywise-prompt.txt）只输出这个标记，用来告诉我们是哪台设备
DEVICE_MARKER = re.compile(r'<waywise-device id="([^"]*)"\s*/>')


class Message(BaseModel):
    role: str
    content: str | list | None = None


class ChatRequest(BaseModel):
    model: str | None = None
    messages: list[Message]
    stream: bool = False
    temperature: float | None = None
    max_tokens: int | None = None
    user: str | None = None


def get_llm(settings: Settings = Depends(get_settings)) -> LLMClient:
    return LLMClient(settings)


@dataclass
class Runtime:
    store: Store
    memory: MemoryScheduler


_RUNTIMES: dict[str, Runtime] = {}


def get_runtime(settings: Settings = Depends(get_settings)) -> Runtime:
    rt = _RUNTIMES.get(settings.db_path)
    if rt is None:
        store = Store(settings.db_path, settings.region)
        memory = MemoryScheduler(store, lambda: LLMClient(settings), settings.memory_idle_s)
        rt = _RUNTIMES[settings.db_path] = Runtime(store, memory)
    return rt


def check_key(authorization: str = Header(default=""), settings: Settings = Depends(get_settings)) -> None:
    if authorization.removeprefix("Bearer ").strip() != settings.agent_api_key:
        raise HTTPException(status_code=401, detail="invalid api key")


def _text(content: str | list | None) -> str:
    if isinstance(content, list):
        return "".join(p.get("text", "") for p in content if isinstance(p, dict))
    return content or ""


# SenseVoice 会在识别结果里带情绪/事件表情（😊 😡 🎼 等），不是孩子说的话
_ASR_EMOJI = re.compile("[\U0001f000-\U0001faff\u2600-\u27bf\ufe0f]")


def _user_text(content: str | list | None) -> str:
    """还原孩子说的纯文本：拆开 xiaozhi 声纹格式 {"speaker": ..., "content": ...}，去掉 ASR 表情。"""
    text = _text(content)
    stripped = text.strip()
    if stripped.startswith("{") and stripped.endswith("}"):
        try:
            data = json.loads(stripped)
        except ValueError:
            data = None
        if isinstance(data, dict) and isinstance(data.get("content"), str):
            text = data["content"]
    return _ASR_EMOJI.sub("", text).strip()


# 固件唤醒词（写在板子里，暂时改不了）。孩子喊它就是在打招呼，别让 Mumi 纠结自己叫不叫「小智」
_WAKE_WORDS = re.compile(r"^(你好|嗨|hi|hello)?[，,\s]*(小智|小志|小知|小治|小致)[。！!？?.\s]*$", re.IGNORECASE)


def _normalize_child(text: str) -> str:
    return "你好" if _WAKE_WORDS.match(text.strip()) else text


def history_of(req: ChatRequest) -> list[dict]:
    # 设备接入层（xiaozhi）自带的 system prompt 是它自己的人设和工具规则，一律丢弃，只用 Mumi 的。
    # TODO(第 8 天): 视觉描述接进来时，给它一个明确标记再单独放行。
    return [
        {"role": m.role, "content": _normalize_child(_user_text(m.content)) if m.role == "user" else _text(m.content)}
        for m in req.messages
        if m.role in ("user", "assistant")
    ]


def device_id_of(req: ChatRequest, header: str | None) -> str:
    for m in req.messages:
        if m.role == "system" and (match := DEVICE_MARKER.search(_text(m.content))):
            if match.group(1) not in ("", "None"):
                return match.group(1)
    return header or req.user or "unknown"


def resolve_theme(store: Store, theme_id: str | None, default_id: str) -> Theme | None:
    """孩子当前主题：内置主题 → 家长在开发者平台自建的主题 → 默认主题。"""
    if theme_id:
        if theme := get_theme(theme_id):
            return theme
        if data := store.custom_theme(theme_id):
            return Theme.model_validate(data)
    return get_theme(default_id)


@dataclass
class Turn:
    child: Child
    session: SessionRef
    theme: Theme | None
    messages: list[dict]
    blocked: Verdict | None = None  # 输入层拦下时的固定回复，不走大模型


async def prepare_turn(req: ChatRequest, device_id: str, settings: Settings, rt: Runtime) -> Turn:
    """软件文档 4.1 的 ①③：查孩子和主题 → 定会话 → 记下孩子这句 → 组装 system prompt。"""
    history = history_of(req)
    child = rt.store.child_for_device(
        device_id, settings.default_child_name, settings.default_child_age, settings.default_theme_id
    )
    theme = resolve_theme(rt.store, child.theme_id, settings.default_theme_id)
    # xiaozhi 每次都发整场对话；只有一条孩子的话 = 设备刚连上，新的一场
    first_turn = sum(m["role"] == "user" for m in history) <= 1
    session = rt.store.resolve_session(device_id, child.id, first_turn, settings.session_idle_s)
    if session.previous_id:
        await rt.memory.flush(session.previous_id, child.id)

    child_text = next((m["content"] for m in reversed(history) if m["role"] == "user"), "")
    said = find_words(theme, child_text)
    # ② 输入安全检查：命中就不走大模型
    verdict = check_input(child_text)
    rt.store.add_message(session.id, "user", child_text, said, blocked=verdict is not None)
    rt.store.record_words(child.id, heard=[], said=said)
    if verdict:
        rt.store.add_safety_event(child.id, session.id, verdict.category, child_text, verdict.notify_parent)
        log.warning("safety input blocked category=%s session=%s", verdict.category, session.id)
        return Turn(child, session, theme, [], blocked=verdict)

    system = build_system_prompt(
        ChildProfile(name=child.name, age=child.age),
        theme,
        word_statuses=rt.store.word_statuses(child.id),
        facts=rt.store.recent_facts(child.id, exclude_session=session.id),
        session_start=session.is_new,
    )
    return Turn(child, session, theme, [{"role": "system", "content": system}, *history[-MAX_HISTORY:]])


def finish_turn(turn: Turn, reply: str, rt: Runtime) -> None:
    """软件文档 4.1 的 ⑥：落库这轮说了什么、碰到哪些目标词，并安排记忆抽取。"""
    try:
        heard = find_words(turn.theme, reply)
        rt.store.add_message(turn.session.id, "assistant", reply, heard, blocked=turn.blocked is not None)
        rt.store.record_words(turn.child.id, heard=heard, said=[])
        rt.memory.schedule(turn.session.id, turn.child.id)
    except Exception:  # noqa: BLE001 — 落库失败不能影响孩子听到回答
        log.exception("finish_turn failed session=%s", turn.session.id)


@app.get("/healthz")
def healthz():
    return {"ok": True}


@app.get("/v1/models", dependencies=[Depends(check_key)])
def list_models(settings: Settings = Depends(get_settings)):
    return {"object": "list", "data": [{"id": settings.agent_model_name, "object": "model", "owned_by": "waywise"}]}


@app.post("/v1/chat/completions", dependencies=[Depends(check_key)])
async def chat_completions(
    req: ChatRequest,
    x_device_id: str | None = Header(default=None),
    settings: Settings = Depends(get_settings),
    llm: LLMClient = Depends(get_llm),
    rt: Runtime = Depends(get_runtime),
):
    device_id = device_id_of(req, x_device_id)
    turn = await prepare_turn(req, device_id, settings, rt)
    messages = turn.messages
    max_tokens = min(req.max_tokens or settings.llm_max_tokens, settings.llm_max_tokens)
    params = {k: v for k, v in {"temperature": req.temperature, "max_tokens": max_tokens}.items() if v is not None}
    completion_id = f"chatcmpl-{uuid.uuid4().hex}"
    created = int(time.time())
    model = settings.agent_model_name
    log.info("chat device=%s session=%s new=%s turns=%d", device_id, turn.session.id, turn.session.is_new, len(messages) - 1)

    async def reply_stream():
        if turn.blocked:
            yield turn.blocked.reply
            return
        # ⑤ 输出安全检查：按句清洗、拦截、截断
        async for sentence in safe_stream(llm.stream(messages, **params)):
            yield sentence

    if req.stream:

        async def sse():
            def chunk(delta: dict, finish: str | None = None) -> str:
                body = {
                    "id": completion_id,
                    "object": "chat.completion.chunk",
                    "created": created,
                    "model": model,
                    "choices": [{"index": 0, "delta": delta, "finish_reason": finish}],
                }
                return f"data: {json.dumps(body, ensure_ascii=False)}\n\n"

            yield chunk({"role": "assistant"})
            reply = ""
            try:
                async for text in reply_stream():
                    reply += text
                    yield chunk({"content": text})
            except LLMError as e:
                log.error("llm failed: %s", e)
                yield chunk({"content": "Mumi 有点累了，我们等一下再聊好吗？"})
            if reply:
                finish_turn(turn, reply.strip(), rt)
            yield chunk({}, "stop")
            yield "data: [DONE]\n\n"

        return StreamingResponse(sse(), media_type="text/event-stream")

    try:
        text = "".join([t async for t in reply_stream()])
    except LLMError as e:
        log.error("llm failed: %s", e)
        raise HTTPException(status_code=502, detail="upstream llm unavailable")
    finish_turn(turn, text.strip(), rt)
    return {
        "id": completion_id,
        "object": "chat.completion",
        "created": created,
        "model": model,
        "choices": [{"index": 0, "message": {"role": "assistant", "content": text}, "finish_reason": "stop"}],
        "usage": {"prompt_tokens": 0, "completion_tokens": 0, "total_tokens": 0},
    }


from .parent_api import router as parent_router  # noqa: E402 — 放在末尾，parent_api 会反向引用 get_runtime

app.include_router(parent_router)

from .studio_api import router as studio_router  # noqa: E402

app.include_router(studio_router)
