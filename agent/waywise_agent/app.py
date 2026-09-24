"""对外只暴露一个 OpenAI 兼容端点（软件文档 3.3）：设备接入层以为自己在调一个 LLM。"""

import json
import logging
import time
import uuid

from fastapi import Depends, FastAPI, Header, HTTPException
from fastapi.responses import StreamingResponse
from pydantic import BaseModel

from .config import Settings, get_settings
from .llm import LLMClient, LLMError
from .persona import ChildProfile, build_system_prompt
from .themes import get_theme

log = logging.getLogger("waywise")
app = FastAPI(title="Waywise Agent")

MAX_HISTORY = 20  # 会话内记忆：截断到最近 N 条


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


def check_key(authorization: str = Header(default=""), settings: Settings = Depends(get_settings)) -> None:
    if authorization.removeprefix("Bearer ").strip() != settings.agent_api_key:
        raise HTTPException(status_code=401, detail="invalid api key")


def _text(content: str | list | None) -> str:
    if isinstance(content, list):
        return "".join(p.get("text", "") for p in content if isinstance(p, dict))
    return content or ""


def build_messages(req: ChatRequest, settings: Settings) -> list[dict]:
    # TODO(第 4 天): 按 device_id 查孩子档案、当前主题、跨会话记忆
    child = ChildProfile(name=settings.default_child_name, age=settings.default_child_age)
    upstream_system = [_text(m.content) for m in req.messages if m.role == "system"]
    system = build_system_prompt(child, get_theme(settings.default_theme_id))
    if upstream_system:
        # 设备接入层塞进来的上下文（如视觉描述）作为补充，不覆盖人设
        system += "\n\n补充上下文：\n" + "\n".join(upstream_system)
    history = [{"role": m.role, "content": _text(m.content)} for m in req.messages if m.role in ("user", "assistant")]
    return [{"role": "system", "content": system}, *history[-MAX_HISTORY:]]


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
):
    device_id = x_device_id or req.user
    messages = build_messages(req, settings)
    params = {k: v for k, v in {"temperature": req.temperature, "max_tokens": req.max_tokens}.items() if v is not None}
    completion_id = f"chatcmpl-{uuid.uuid4().hex}"
    created = int(time.time())
    model = settings.agent_model_name
    log.info("chat device=%s turns=%d", device_id, len(messages) - 1)

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
            try:
                async for text in llm.stream(messages, **params):
                    yield chunk({"content": text})
            except LLMError as e:
                log.error("llm failed: %s", e)
                yield chunk({"content": "Mumi 有点累了，我们等一下再聊好吗？"})
            yield chunk({}, "stop")
            yield "data: [DONE]\n\n"

        return StreamingResponse(sse(), media_type="text/event-stream")

    try:
        text = "".join([t async for t in llm.stream(messages, **params)])
    except LLMError as e:
        log.error("llm failed: %s", e)
        raise HTTPException(status_code=502, detail="upstream llm unavailable")
    return {
        "id": completion_id,
        "object": "chat.completion",
        "created": created,
        "model": model,
        "choices": [{"index": 0, "message": {"role": "assistant", "content": text}, "finish_reason": "stop"}],
        "usage": {"prompt_tokens": 0, "completion_tokens": 0, "total_tokens": 0},
    }
