"""安全机制（软件文档 4.6）：输入词表 + 输出规则 + prompt 约束，三层。

输入和输出都只用词表和正则，不再调一次大模型 —— 那会让延迟翻倍（软件文档 4.1、坑 #3）。
"""

import re
from collections.abc import AsyncIterator
from dataclasses import dataclass


@dataclass(frozen=True)
class Verdict:
    category: str
    reply: str  # 直接读给孩子听的固定回复，不走大模型
    notify_parent: bool  # 家长端要看到的事件（第 6 天起展示）


# 顺序即优先级：求助类最先判断。词条尽量用短语，避免误伤（例如「打」会命中「打招呼」）。
_INPUT_RULES: list[tuple[str, list[str], str, bool]] = [
    (
        "lost",
        ["迷路", "找不到妈妈", "找不到爸爸", "找不到家", "妈妈不见了", "爸爸不见了", "走丢"],
        "别怕，站在原地不要乱走。大声喊妈妈，或者请穿制服的叔叔阿姨帮忙，好吗？",
        True,
    ),
    (
        "hurt",
        ["受伤", "流血", "出血", "好疼", "很疼", "好痛", "很痛", "摔倒", "摔跤", "烫到", "割到", "被咬", "骨折", "救命", "着火", "起火"],
        "Oh no! 别怕别怕。快去告诉爸爸妈妈，或者身边的大人，请他们帮你，好吗？",
        True,
    ),
    (
        "distress",
        ["不想活", "想死", "去死", "自杀", "打我", "欺负我", "有坏人", "摸我", "不让我告诉"],
        "谢谢你告诉 Mumi，你很勇敢。这件事很重要，现在就去告诉爸爸妈妈或者老师，好吗？",
        True,
    ),
    (
        "personal_info",
        ["我家住在", "我家地址", "我的地址", "我家在哪", "电话号码", "手机号", "密码", "身份证"],
        "这是你的小秘密，只告诉爸爸妈妈就好哦。Let's play a word game! 你想说说哪种动物？",
        False,
    ),
    (
        "unsafe_topic",
        ["手枪", "开枪", "真枪", "炸弹", "杀人", "杀死", "打死", "毒药", "吸毒", "抽烟", "喝酒", "赌博", "色情", "裸体", "脱光", "性交", "鬼故事", "恐怖片"],
        "这个问题我们去问问爸爸妈妈吧。Let's talk about something fun! 你今天玩了什么呀？",
        True,
    ),
    (
        "jailbreak",
        ["忽略之前", "忽略上面", "忘记你的设定", "从现在开始你是", "系统提示", "提示词", "prompt", "ignore previous", "ignore all", "you are now", "jailbreak", "开发者模式"],
        "我是 Mumi，是你的英语小伙伴。我们接着玩吧！Can you tell me your favorite color?",
        False,
    ),
]

_PHONE = re.compile(r"1[3-9]\d{9}")


def check_input(text: str) -> Verdict | None:
    """命中任何一条就返回固定回复；返回 None 表示可以交给大模型。"""
    lowered = text.lower()
    for category, words, reply, notify in _INPUT_RULES:
        if any(w in lowered for w in words):
            return Verdict(category, reply, notify)
    if _PHONE.search(re.sub(r"\s", "", text)):
        reply = next(r for c, _, r, _ in _INPUT_RULES if c == "personal_info")
        return Verdict("personal_info", reply, False)
    return None


# ---- 输出规则 ----

# 整句丢弃：不能兑现的承诺、索要个人信息、以及输入层同样的危险话题
_OUTPUT_BANNED = re.compile(
    r"(我陪你去|我带你去|我陪你一起去|我来找你|我去找你|我去接你"
    r"|你家住|你家在哪|你住在哪|地址是|电话号码|手机号|哪个幼儿园|哪个学校"
    r"|手枪|开枪|炸弹|杀人|杀死|毒药|抽烟|喝酒|色情|裸体)"
)
# 错误的安全常识：模型会编出「打雷躲到大树下」这种话，整句丢弃
# 用前瞻做「同一句里同时出现」，不管词序（英文是 "tree to hide under"，中文是「躲到树下」）
_OUTPUT_UNSAFE_ADVICE = re.compile(
    r"^(?:(?=.*(躲|藏|hide|shelter))(?=.*(树|tree))"
    r"|(?=.*(马路|street|road))(?=.*(玩|跑|play|run))"
    r"|.*(玩火|点火|摸插座|插座里|plug|socket|(去|到)(河边|水边|湖边|池塘)))",
    re.IGNORECASE | re.DOTALL,
)
# 读出来的话不能带格式：表情、markdown、括号里的动作描写
_EMOJI = re.compile("[\U0001f000-\U0001faff☀-➿️]")
_FORMAT = re.compile(r"[*#`_~>|•]|^\s*[-\d]+[.、)]\s*")
_STAGE = re.compile(r"[（(【\[][^）)】\]]{0,20}[）)】\]]")
_SENTENCE_END = re.compile(r"[。！？!?…\n]|(?<![A-Z][a-z])\.(?=\s|$)")

MAX_SENTENCES = 4  # 「A dog!」这种短句也算一句，所以比 prompt 里的 3 句宽一点
MAX_CHARS = 120
FALLBACK_REPLY = "我们换个话题吧！What's your favorite animal? 你最喜欢什么动物？"


def clean_sentence(sentence: str) -> str:
    s = _STAGE.sub("", sentence)
    s = _EMOJI.sub("", s)
    s = _FORMAT.sub("", s)
    return re.sub(r"[ \t]{2,}", " ", s).strip()


async def safe_stream(chunks: AsyncIterator[str]) -> AsyncIterator[str]:
    """按句缓冲模型输出：逐句清洗、拦截，超长截断。

    xiaozhi 本来就按标点切句送 TTS，所以按句缓冲只多等半句话，首包几乎不受影响。
    """
    buffer, sentences, chars, dropped = "", 0, 0, False

    def take(sentence: str) -> str | None:
        nonlocal sentences, chars, dropped
        if _OUTPUT_BANNED.search(sentence) or _OUTPUT_UNSAFE_ADVICE.search(sentence):
            dropped = True
            return None
        cleaned = clean_sentence(sentence)
        if not cleaned or sentences >= MAX_SENTENCES or chars + len(cleaned) > MAX_CHARS:
            return None
        sentences += 1
        chars += len(cleaned)
        return cleaned

    async for chunk in chunks:
        buffer += chunk
        while match := _SENTENCE_END.search(buffer):
            sentence, buffer = buffer[: match.end()], buffer[match.end() :]
            if (out := take(sentence)) is not None:
                yield out + " "
    if buffer.strip() and (out := take(buffer)) is not None:
        yield out
    if sentences == 0 and dropped:
        yield FALLBACK_REPLY
