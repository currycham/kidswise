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
    "读这段孩子和 Mumi 的对话，输出一个 JSON 对象，有两个字段：\n"
    "facts：最多 3 条关于这个孩子的事实（喜好、经历、家人、正在关心的事），JSON 字符串数组；"
    "每条不超过 20 字，而且要能单独看懂：写清楚是谁、是什么（宠物、家人要写上名字和身份，不要只写一个名字）。"
    "孩子只是问了问题、没讲自己的事，就给 []。\n"
    "follow_up：孩子自己说过想下次再做、再看、再找的一件事，或者孩子问了却还没聊完的一个问题，"
    "用一句不超过 20 字的话写下来，下次聊天时可以问起它；没有就给空字符串。\n"
    "只写孩子自己说出来的事，不要写 Mumi 说的话，也不要推测或编造。只输出 JSON。"
)

# 这些字不算内容：判断一条事实是不是孩子自己说的，只看剩下的字
_STOP = set("的了一个只是在有和也很都我你他她它们吗呢吧啊呀哦嗯着过叫")


def grounded(fact: str, child_text: str) -> bool:
    """一条事实的内容字，至少一半出现在孩子自己的话里；防止模型编出孩子没说过的事（宁可少记）。"""
    words = re.findall(r"[a-zA-Z]{3,}", fact)
    chars = [c for c in re.sub(r"[a-zA-Z0-9\s]", "", fact) if re.match(r"[\u4e00-\u9fff]", c) and c not in _STOP]
    units = [w.lower() for w in words] + chars
    if not units:
        return False
    low = child_text.lower()
    return sum(u in low for u in units) / len(units) >= 0.5


def _json_value(text: str):
    for pattern in (r"\{.*\}", r"\[.*\]"):
        match = re.search(pattern, text, re.DOTALL)
        if match:
            try:
                return json.loads(match.group(0))
            except ValueError:
                continue
    return None


def parse_memory(text: str) -> tuple[list[str], str]:
    """{"facts": [...], "follow_up": "..."}；也接受只有事实的数组（旧格式）。"""
    data = _json_value(text)
    facts_raw, follow = (data.get("facts", []), data.get("follow_up", "")) if isinstance(data, dict) else (data or [], "")
    facts = [f.strip()[:30] for f in facts_raw if isinstance(f, str) and f.strip()][:3] if isinstance(facts_raw, list) else []
    follow = follow.strip()[:30] if isinstance(follow, str) else ""
    return facts, follow


def parse_facts(text: str) -> list[str]:
    return parse_memory(text)[0]


async def extract_memory(llm: LLMClient, messages: list[dict]) -> tuple[list[str], str | None]:
    """一场对话结束后的记忆：事实 + 一件没做完的事。都必须是孩子自己说过的。"""
    transcript = "\n".join(f"{'孩子' if m['role'] == 'user' else 'Mumi'}：{m['text']}" for m in messages)
    prompt = [{"role": "system", "content": EXTRACT_PROMPT}, {"role": "user", "content": transcript}]
    text = "".join([chunk async for chunk in llm.stream(prompt, max_tokens=240, temperature=0.2)])
    child_text = " ".join(m["text"] for m in messages if m["role"] == "user")
    facts, follow = parse_memory(text)
    kept = [f for f in facts if grounded(f, child_text)]
    if len(kept) < len(facts):
        log.warning("memory: dropped facts the child never said: %s", [f for f in facts if f not in kept])
    if follow and not grounded(follow, child_text):
        log.warning("memory: dropped follow-up the child never said: %s", follow)
        follow = ""
    return kept, follow or None


async def extract_facts(llm: LLMClient, messages: list[dict]) -> list[str]:
    return (await extract_memory(llm, messages))[0]


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
        messages = self.store.session_messages(session_id, include_blocked=False)
        if not any(m["role"] == "user" for m in messages):
            return
        try:
            facts, follow_up = await extract_memory(self.llm_factory(), messages)
        except LLMError as e:
            log.warning("memory extraction failed for %s: %s", session_id, e)
            return
        self.store.save_facts(child_id, session_id, facts)
        self.store.save_follow_up(child_id, session_id, follow_up)
        log.info("memory session=%s facts=%s follow_up=%s", session_id, facts, follow_up)
