"""OpenAI 兼容的上游 LLM 客户端：主 + 备，换供应商只改配置。"""

import json
import logging
from collections.abc import AsyncIterator

import httpx

from .config import Settings

log = logging.getLogger(__name__)


class LLMError(RuntimeError):
    pass


# 对话场景不需要思考模式，关掉能省约 1 秒首字延迟（方舟实测 1.2–2.0s → 0.9s）
THINKING_OFF = {
    "volces.com": {"thinking": {"type": "disabled"}},
    "aliyuncs.com": {"enable_thinking": False},
    "bigmodel.cn": {"thinking": {"type": "disabled"}},
    "deepseek.com": {"thinking": {"type": "disabled"}},
}


class _Upstream:
    def __init__(self, base_url: str, api_key: str, model: str, timeout_s: float):
        self.url = base_url.rstrip("/") + "/chat/completions"
        self.headers = {"Authorization": f"Bearer {api_key}"}
        self.model = model
        self.timeout = httpx.Timeout(timeout_s, connect=3.0)
        self.extra = next((v for domain, v in THINKING_OFF.items() if domain in base_url), {})

    async def stream(self, messages: list[dict], **params) -> AsyncIterator[str]:
        body = {"model": self.model, "messages": messages, "stream": True, **self.extra, **params}
        async with httpx.AsyncClient(timeout=self.timeout) as client:
            async with client.stream("POST", self.url, headers=self.headers, json=body) as resp:
                if resp.status_code != 200:
                    raise LLMError(f"{resp.status_code} {(await resp.aread())[:300]!r}")
                async for line in resp.aiter_lines():
                    if not line.startswith("data:"):
                        continue
                    data = line[5:].strip()
                    if data == "[DONE]":
                        return
                    choices = json.loads(data).get("choices") or []
                    if choices and (text := choices[0].get("delta", {}).get("content")):
                        yield text


class _Mock:
    """不调外部模型，用于没有 API key 时本地联调。"""

    async def stream(self, messages: list[dict], **params) -> AsyncIterator[str]:
        heard = next((m["content"] for m in reversed(messages) if m["role"] == "user"), "")
        reply = f"I heard you! 你说的是「{heard}」。Let's learn a word: apple, 苹果。Can you say apple?"
        for token in reply.split(" "):
            yield token + " "


class LLMClient:
    def __init__(self, settings: Settings):
        if settings.llm_provider == "mock":
            self.upstreams = [_Mock()]
            return
        self.upstreams = [
            _Upstream(settings.llm_base_url, settings.llm_api_key, settings.llm_model, settings.llm_timeout_s)
        ]
        if settings.llm_fallback_base_url:
            self.upstreams.append(
                _Upstream(
                    settings.llm_fallback_base_url,
                    settings.llm_fallback_api_key,
                    settings.llm_fallback_model,
                    settings.llm_timeout_s,
                )
            )

    async def stream(self, messages: list[dict], **params) -> AsyncIterator[str]:
        """按顺序尝试上游；只有在还没吐出任何内容时才切到备用。"""
        last_err: Exception | None = None
        for upstream in self.upstreams:
            started = False
            try:
                async for chunk in upstream.stream(messages, **params):
                    started = True
                    yield chunk
                return
            except (httpx.HTTPError, LLMError) as e:
                if started:
                    raise
                log.warning("upstream failed, trying next: %s", e)
                last_err = e
        raise LLMError(f"all upstreams failed: {last_err}")
