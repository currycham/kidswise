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
from .vision import (
    camera_tool_name,
    drop_fewshot,
    photo_question,
    photo_result,
    tool_call_message,
    wants_to_show,
    with_photo_note,
)

log = logging.getLogger("waywise")
app = FastAPI(title="Waywise Agent")

MAX_HISTORY = 20  # 会话内记忆：截断到最近 N 条
# xiaozhi 的 prompt 模板（deploy/xiaozhi/waywise-prompt.txt）只输出这个标记，用来告诉我们是哪台设备
DEVICE_MARKER = re.compile(r'<waywise-device id="([^"]*)"\s*/>')


class Message(BaseModel):
    role: str
    content: str | list | None = None
    tool_calls: list[dict] | None = None
    tool_call_id: str | None = None


class ChatRequest(BaseModel):
    model: str | None = None
    messages: list[Message]
    stream: bool = False
    temperature: float | None = None
    max_tokens: int | None = None
    user: str | None = None
    tools: list[dict] | None = None  # xiaozhi 工具调用模式下带来的工具（含设备拍照）


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


# 固件唤醒词：「你好木米」（自编固件）或「你好小智」（原厂固件）。孩子喊它就是在打招呼
# 「猫咪」是 ASR 对「木米」最常见的误听（板子实测），只在整句就是「你好猫咪」时才当唤醒词
_WAKE_WORDS = re.compile(
    r"^(你好|嗨|hi|hello)?[，,\s]*(小智|小志|小知|小治|小致|木米|木咪|慕米|沐米|mumi|mu mi)[。！!？?.\s]*$|^你好[，,\s]*猫咪[。！!？?.\s]*$",
    re.IGNORECASE,
)


def _normalize_child(text: str) -> str:
    return "你好" if _WAKE_WORDS.match(text.strip()) else text


def history_of(req: ChatRequest) -> list[dict]:
    # 设备接入层（xiaozhi）自带的 system prompt 是它自己的人设和工具规则，一律丢弃，只用 Mumi 的。
    # 工具调用示例、工具调用和工具结果也不进对话历史（拍照结果单独作为画面描述放进 system）。
    return [
        {"role": m.role, "content": _normalize_child(_user_text(m.content)) if m.role == "user" else _text(m.content)}
        for m in drop_fewshot(req.messages)
        if m.role in ("user", "assistant") and not m.tool_calls and (m.role == "user" or _text(m.content))
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
    child_text: str = ""
    saw_photo: bool = False  # 只有这一轮真拍了照，Mumi 才能说「我看到了」


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
        return Turn(child, session, theme, [], blocked=verdict, child_text=child_text)

    system = build_system_prompt(
        ChildProfile(name=child.name, age=child.age),
        theme,
        word_statuses=rt.store.word_statuses(child.id),
        facts=rt.store.recent_facts(child.id, exclude_session=session.id),
        session_start=session.is_new,
    )
    return Turn(child, session, theme, [{"role": "system", "content": system}, *history[-MAX_HISTORY:]], child_text=child_text)


async def prepare_photo_turn(req: ChatRequest, device_id: str, seen: str, settings: Settings, rt: Runtime) -> Turn:
    """拍照工具返回后的续答：孩子那句话上一轮已经记过了，这里只把画面描述放进 system prompt。"""
    history = history_of(req)
    child = rt.store.child_for_device(
        device_id, settings.default_child_name, settings.default_child_age, settings.default_theme_id
    )
    theme = resolve_theme(rt.store, child.theme_id, settings.default_theme_id)
    session = rt.store.resolve_session(device_id, child.id, False, settings.session_idle_s)
    system = build_system_prompt(
        ChildProfile(name=child.name, age=child.age),
        theme,
        word_statuses=rt.store.word_statuses(child.id),
        facts=rt.store.recent_facts(child.id, exclude_session=session.id),
        seen=seen,
    )
    log.info("photo seen session=%s: %s", session.id, seen[:80])
    history = history[-MAX_HISTORY:]
    if history and history[-1]["role"] == "user":
        history[-1] = {"role": "user", "content": with_photo_note(history[-1]["content"], seen)}
    return Turn(child, session, theme, [{"role": "system", "content": system}, *history], saw_photo=True)


def finish_turn(turn: Turn, reply: str, rt: Runtime) -> None:
    """软件文档 4.1 的 ⑥：落库这轮说了什么、碰到哪些目标词，并安排记忆抽取。"""
    try:
        heard = find_words(turn.theme, reply)
        rt.store.add_message(turn.session.id, "assistant", reply, heard, blocked=turn.blocked is not None)
        rt.store.record_words(turn.child.id, heard=heard, said=[])
        rt.memory.schedule(turn.session.id, turn.child.id)
    except Exception:  # noqa: BLE001 — 落库失败不能影响孩子听到回答
        log.exception("finish_turn failed session=%s", turn.session.id)


def tool_call_response(call: dict, stream: bool, model: str):
    """OpenAI 兼容的工具调用回复（流式时按 chat.completion.chunk 的 tool_calls 增量格式）。"""
    completion_id, created = f"chatcmpl-{uuid.uuid4().hex}", int(time.time())
    if not stream:
        return {
            "id": completion_id, "object": "chat.completion", "created": created, "model": model,
            "choices": [{"index": 0, "message": {"role": "assistant", "content": None, "tool_calls": [call]},
                         "finish_reason": "tool_calls"}],
        }

    def chunk(delta: dict, finish: str | None = None) -> str:
        body = {"id": completion_id, "object": "chat.completion.chunk", "created": created, "model": model,
                "choices": [{"index": 0, "delta": delta, "finish_reason": finish}]}
        return f"data: {json.dumps(body, ensure_ascii=False)}\n\n"

    async def sse():
        yield chunk({"role": "assistant"})
        yield chunk({"tool_calls": [{"index": 0, **call}]})
        yield chunk({}, "tool_calls")
        yield "data: [DONE]\n\n"

    return StreamingResponse(sse(), media_type="text/event-stream")


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
    seen = photo_result(drop_fewshot(req.messages))
    if seen is not None:
        turn = await prepare_photo_turn(req, device_id, seen, settings, rt)
    else:
        turn = await prepare_turn(req, device_id, settings, rt)
        camera = camera_tool_name(req.tools)
        if camera and not turn.blocked and wants_to_show(turn.child_text):
            # ④ 视觉：孩子要给 Mumi 看东西 → 让设备拍照，不调大模型
            call = tool_call_message(camera, photo_question(turn.child_text, turn.child.age), f"call_{uuid.uuid4().hex[:12]}")
            log.info("photo requested session=%s", turn.session.id)
            return tool_call_response(call, req.stream, settings.agent_model_name)
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
        async for sentence in safe_stream(llm.stream(messages, **params), may_claim_sight=turn.saw_photo):
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
        text = "".join([t async for t in reply_stream()]).strip()
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
