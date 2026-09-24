"""跨会话记忆（软件文档 4.5）：一场结束后抽取 ≤3 条事实，下次塞进 system prompt。"""

import asyncio
import json
import logging
import re
from collections.abc import Callable

from .llm import LLMClient, LLMError
from .store import Store

log = logging.getLogger(__name__)

EXTRACT_PROMPT = (
    "从这段对话里提取最多 3 条关于这个孩子的事实（喜好、经历、家人、正在关心的事），"
    "每条不超过 20 字，用 JSON 字符串数组返回，例如 [\"喜欢白色的小狗\", \"今天去了公园\"]。"
    "只写孩子自己说出来的事，不要写 Mumi 说的话，也不要推测。没有就返回 []。只输出 JSON。"
)


def parse_facts(text: str) -> list[str]:
    match = re.search(r"\[.*\]", text, re.DOTALL)
    if not match:
        return []
    try:
        data = json.loads(match.group(0))
    except ValueError:
        return []
    return [f.strip()[:30] for f in data if isinstance(f, str) and f.strip()][:3]


async def extract_facts(llm: LLMClient, messages: list[dict]) -> list[str]:
    transcript = "\n".join(f"{'孩子' if m['role'] == 'user' else 'Mumi'}：{m['text']}" for m in messages)
    prompt = [{"role": "system", "content": EXTRACT_PROMPT}, {"role": "user", "content": transcript}]
    text = "".join([chunk async for chunk in llm.stream(prompt, max_tokens=200, temperature=0.2)])
    return parse_facts(text)


class MemoryScheduler:
    """会话安静 idle_s 秒后在后台抽取；新会话开始时 flush 上一场，保证记忆已就绪。"""

    def __init__(self, store: Store, llm_factory: Callable[[], LLMClient], idle_s: float):
        self.store = store
        self.llm_factory = llm_factory
        self.idle_s = idle_s
        self._pending: dict[str, asyncio.Task] = {}

    def schedule(self, session_id: str, child_id: str) -> None:
        if task := self._pending.get(session_id):
            task.cancel()
        self._pending[session_id] = asyncio.create_task(self._later(session_id, child_id))

    async def flush(self, session_id: str, child_id: str) -> None:
        """立即完成这一场的抽取（如果还没抽过）。"""
        if task := self._pending.pop(session_id, None):
            task.cancel()
        if not self.store.session_extracted(session_id):
            await self._extract(session_id, child_id)

    async def _later(self, session_id: str, child_id: str) -> None:
        try:
            await asyncio.sleep(self.idle_s)
        except asyncio.CancelledError:
            return
        self._pending.pop(session_id, None)
        await self._extract(session_id, child_id)

    async def _extract(self, session_id: str, child_id: str) -> None:
        messages = self.store.session_messages(session_id)
        if not any(m["role"] == "user" for m in messages):
            return
        try:
            facts = await extract_facts(self.llm_factory(), messages)
        except LLMError as e:
            log.warning("memory extraction failed for %s: %s", session_id, e)
            return
        self.store.save_facts(child_id, session_id, facts)
        log.info("memory session=%s facts=%s", session_id, facts)
