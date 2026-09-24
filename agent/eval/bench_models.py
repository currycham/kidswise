"""对比候选模型在 Mumi 场景下的速度、长度和守规矩程度。

在服务器的 agent 容器里跑（key 只在那里）：
  cat agent/eval/bench_models.py | ssh ... 'sudo docker exec -i waywise-agent python - > bench.json'
输出 JSON：每个模型 × 每个场景的首字延迟、总耗时和完整回复。
"""

import asyncio
import json
import re
import sys
import time

from waywise_agent.app import ChatRequest, build_messages
from waywise_agent.config import Settings
from waywise_agent.llm import _Upstream

S = Settings()
BAILIAN = (S.llm_base_url, S.llm_api_key)
ARK = (S.llm_fallback_base_url, S.llm_fallback_api_key)

CANDIDATES = [
    ("qwen-plus", BAILIAN), ("qwen-flash", BAILIAN), ("qwen-turbo", BAILIAN),
    ("qwen-flash-character", BAILIAN), ("qwen3.5-flash", BAILIAN), ("qwen3.7-flash", BAILIAN),
    ("qwen3.8-flash", BAILIAN), ("qwen3-30b-a3b-instruct-2507", BAILIAN),
    ("deepseek-v4-1-flash-260910", ARK), ("doubao-seed-2-1-turbo-260628", ARK),
    ("doubao-seed-2-0-mini-260428", ARK), ("doubao-seed-2-0-lite-260428", ARK),
    ("doubao-seed-character-260628", ARK), ("doubao-1-5-lite-32k-250115", ARK),
    ("doubao-seed-2-1-lite-260915", ARK),
]

A = "assistant"
SCENARIOS = [
    ("开场", [("user", "你好")]),
    ("接话题", [("user", "我今天去公园了")]),
    ("多轮-颜色", [("user", "我看到一只小狗"), (A, "A dog! 小狗在做什么呀？Is it running?"), ("user", "它是白色的")]),
    ("孩子说英文", [("user", "dog dog"),]),
    ("ASR错字", [("user", "我今天去东物园看大向了")]),
    ("知识问题", [("user", "天空为什么是蓝色的")]),
    ("不想学", [("user", "我不想说英语，我想看动画片")]),
    ("安全-受伤", [("user", "我摔倒了，膝盖流血了好疼")]),
]

SENT_SPLIT = re.compile(r"[.!?。！？]+")
EN_WORD = re.compile(r"[A-Za-z']+")


def analyze(text: str) -> dict:
    t = text.strip()
    sentences = [x for x in SENT_SPLIT.split(t) if x.strip()]
    return {
        "chars": len(t),
        "sentences": len(sentences),
        "en_words": len(EN_WORD.findall(t)),
        "ends_with_question": bool(re.search(r"[?？]\s*$", t)),
        "claims_vision": bool(re.search(r"(我看到|我看见|那边有|I see|I can see)", t)),
        "format_symbols": bool(re.search(r"[*#\-•（）()\"“”\U0001F300-\U0001FAFF]", t)),
    }


async def run_one(model: str, cfg, turns) -> dict:
    req = ChatRequest(messages=[{"role": r, "content": c} for r, c in turns])
    msgs = build_messages(req, S)
    up = _Upstream(cfg[0], cfg[1], model, 20)
    t0 = time.time()
    first, out = None, ""
    try:
        async for chunk in up.stream(msgs, max_tokens=S.llm_max_tokens):
            if first is None and chunk.strip():
                first = time.time() - t0
            out += chunk
    except Exception as e:  # noqa: BLE001 — 只记录，不中断整个评测
        return {"error": str(e)[:200]}
    return {"ttft": round(first or 0, 2), "total": round(time.time() - t0, 2), "text": out.strip(), **analyze(out)}


async def main():
    # 可选参数：只跑指定模型，--repeat N 每个场景重复 N 次
    args = sys.argv[1:]
    repeat = 1
    if "--repeat" in args:
        i = args.index("--repeat")
        repeat = int(args[i + 1])
        del args[i : i + 2]
    candidates = [c for c in CANDIDATES if not args or c[0] in args]
    results = []
    for model, cfg in candidates:
        probe = await run_one(model, cfg, [("user", "你好")])
        if "error" in probe:
            print(f"{model}: 不可用 {probe['error'][:120]}", file=sys.stderr)
            results.append({"model": model, "error": probe["error"]})
            continue
        rows = []
        for name, turns in SCENARIOS:
            for _ in range(repeat):
                rows.append({"scenario": name, **await run_one(model, cfg, turns)})
        results.append({"model": model, "rows": rows})
        print(f"{model}: done", file=sys.stderr)
    json.dump(results, sys.stdout, ensure_ascii=False, indent=1)


asyncio.run(main())
