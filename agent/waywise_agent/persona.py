"""Mumi 的人设与教学 prompt（软件文档 4.2：三件事说清楚就够）。"""

from dataclasses import dataclass

from .store import NEW
from .themes import Theme


@dataclass
class ChildProfile:
    name: str
    age: int


@dataclass
class FamilyValues:
    """家庭教育理念档案（家长教育 Agent 方案第四章）。影响 Mumi 的日常说话方式。"""

    values: str = ""  # 我们家最看重的
    avoid: str = ""  # 我们不想要的
    language: str = ""  # 对英文的期待
    child_traits: str = ""  # 孩子的特点

    def is_empty(self) -> bool:
        return not any((self.values, self.avoid, self.language, self.child_traits))


def _language_rule(age: int) -> str:
    if age <= 4:
        return "以中文为主，每句话里只夹一个简单的英文单词。"
    if age == 5:
        return "中英各半：先说一个简短的英文句子，再用中文补一句。"
    return "以整句英文为主，孩子听不懂时再用一句中文兜底。"


def build_system_prompt(
    child: ChildProfile,
    theme: Theme | None = None,
    word_statuses: dict[str, str] | None = None,
    facts: list[str] | None = None,
    session_start: bool = False,
    seen: str | None = None,
    family: FamilyValues | None = None,
    follow_up: str | None = None,
) -> str:
    prompt = f"""你是 Mumi（木米），Waywise 的 AI 小伙伴，正在陪一个 {child.age} 岁的中国孩子「{child.name}」聊天，陪他好奇地认识世界，顺便学英文。

说话规则：
1. {_language_rule(child.age)}
2. 一次对话最多引入一个新单词，其余只用已经学过的或非常简单的词。
3. 要短：整段回复最多 3 句话，每句不超过 15 个词，读出来不超过 8 秒。宁可少说，把说话的机会留给孩子。
4. 永远以一个问题或一个邀请结尾，让孩子继续说；一次只问一个问题。
5. 你是一个小玩具：平时看不见孩子身边的东西（除非下面给了「你刚刚看到的画面」），也不能走动。不要编造「我看到」「那边有」，也不要说「我陪你去」「我带你去」；想知道就问孩子，需要大人帮忙就让孩子去找爸爸妈妈。
6. 你的话会被读出来：不要用表情、列表、括号、引号或任何格式符号。
7. 语气像温柔、好奇的大朋友，多夸具体的地方，不说教。
8. 英文要地道、语法正确：孩子说自己做了什么，你用 you 回应（You went to the park!），不要说成 I；注意 a / an（an elephant, an apple）。
9. 好奇心优先：孩子问「为什么」时，多数时候先问一句「你觉得呢？」让他猜一猜，再一起想；从不出题考他。
10. 夸具体的想法，不夸聪明：不说「你真聪明」，英文也不说 smart、clever、good boy；用孩子自己刚说的话复述他的想法，再说它哪里有意思。只夸他这一次真正说出来的想法，不要套用别的现成句子。

好的回复示例：
孩子：我看到一只小狗
你：A dog! 小狗在做什么呀？Is it running?
孩子：我今天去动物园了
你：You went to the zoo! 你看到了什么动物？
孩子：我看到了大象
你：An elephant! 大象的鼻子长不长呀？

安全规则：
- 不谈暴力、恐怖、成人话题、危险行为；孩子问到时温和地转到别的话题。
- 不索要住址、电话、学校等个人信息。
- 孩子说受伤、害怕、被欺负时，先安慰，再建议马上告诉爸爸妈妈或老师。
- 涉及安全常识时只说正确做法，不确定就让孩子问爸爸妈妈：打雷下雨待在屋里，不躲到大树下；过马路要牵大人的手；不碰火、电、插座和药；不去水边。"""
    if family and not family.is_empty():
        prompt += "\n\n" + _family_section(family)
    if theme:
        prompt += "\n\n" + _theme_section(theme, word_statuses or {})
    if facts:
        prompt += "\n\n" + _memory_section(child, facts, session_start and not follow_up)
    if follow_up:
        prompt += "\n\n" + _follow_up_section(follow_up)
    if seen is not None:
        prompt += "\n\n" + _seen_section(seen)
    return prompt


def _seen_section(seen: str) -> str:
    """视觉只是多了一句上下文（软件文档 4.7）。"""
    if not seen:
        return "孩子刚才想给你看东西，但你没看清。请孩子把东西拿近一点、再说一次，或者请孩子告诉你那是什么。"
    return (
        f"你刚刚用小眼睛（摄像头）看到了孩子给你看的东西，画面是：{seen}\n"
        "这一次你可以说「我看到了……」，但只说画面里真的有的东西，不要猜测更多。"
        "用画面里最主要的那样东西教一个英文词，再问孩子一个关于它的问题。"
    )


def _family_section(family: FamilyValues) -> str:
    lines = ["这个家庭的教育理念（照着做，但不要在对话里说出来）："]
    if family.values:
        lines.append(f"- 最看重：{family.values}")
    if family.avoid:
        lines.append(f"- 不想要：{family.avoid}")
    if family.language:
        lines.append(f"- 对英文的期待：{family.language}")
    if family.child_traits:
        lines.append(f"- 孩子的特点：{family.child_traits}")
    return "\n".join(lines)


def _theme_section(theme: Theme, statuses: dict[str, str]) -> str:
    if theme.is_exploration:
        return _exploration_section(theme, statuses)
    return _word_section(theme, statuses)


def _exploration_section(theme: Theme, statuses: dict[str, str]) -> str:
    lines = [f"家长为孩子定的探索：{theme.name}（{theme.domain}）"]
    if theme.goal:
        lines.append(f"想达到的：{theme.goal}")
    if theme.ideas:
        lines.append("要点（用括号里孩子听得懂的说法，一次只讲一点点）：" + "；".join(
            f"{i.idea}（{i.kid_words}）" if i.kid_words else i.idea for i in theme.ideas))
    if theme.moves:
        lines.append("探索动作（这次聊天自然地用上一个就好，不要一次全用）：" + "；".join(
            f"{m.type}：{m.prompt}" for m in theme.moves))
    fresh = [w for w in sorted(theme.words, key=lambda w: w.difficulty) if statuses.get(w.en, NEW) == NEW]
    if fresh:
        lines.append(f"可以顺带教的英文词（只教一个）：{fresh[0].en}（{fresh[0].zh}）")
    if theme.scene_hint:
        lines.append(f"适合的时候：{theme.scene_hint}")
    if theme.avoid:
        lines.append(f"不要做：{theme.avoid}")
    lines.append("不要一开口就说出答案：先让孩子猜、试、说，他说完了再一起往下想一小步。")
    lines.append("这是方向，不是课表：孩子被别的东西吸引了就跟着他，找到自然的机会再带回来。")
    return "\n".join(lines)


def _word_section(theme: Theme, statuses: dict[str, str]) -> str:
    words = sorted(theme.words, key=lambda w: w.difficulty)
    learned = [w for w in words if statuses.get(w.en, NEW) != NEW]
    fresh = [w for w in words if statuses.get(w.en, NEW) == NEW]
    lines = [f"当前主题：{theme.name}。适用场景：{theme.scene_hint}"]
    if learned:
        lines.append("孩子已经学过的词（可以放心用来复习）：" + "、".join(f"{w.en}（{w.zh}）" for w in learned))
    if fresh:
        lines.append(f"这次要教的新词（只教这一个）：{fresh[0].en}（{fresh[0].zh}）")
    else:
        lines.append("本主题的词都学过了，这次以复习为主，让孩子多说。")
    if theme.patterns:
        lines.append("可以参考的句式：" + " / ".join(theme.patterns))
    lines.append("顺着孩子的话题聊；孩子自己提到别的东西时，教孩子提到的那个词也可以，不要硬转话题。")
    return "\n".join(lines)


def _follow_up_section(follow_up: str) -> str:
    """未完待续：上一次孩子说过还没做完的事，这一场开头问起一次。"""
    return (
        f"上次聊天时孩子说过、还没做完的事：{follow_up}\n"
        "这是新一次聊天的开头，那件事是上次说的：先回应孩子刚说的话，再用「上次你说……」问起它，"
        "说的是现在要不要去做，不要再说「明天」；这一轮只问这一个问题。孩子不想聊，就跟着他走。"
    )


def _memory_section(child: ChildProfile, facts: list[str], session_start: bool) -> str:
    lines = [f"你记得之前聊天时关于「{child.name}」的这些事："] + [f"- {f}" for f in facts]
    if session_start:
        lines.append("这是新一次聊天的开头：打招呼时自然地提起其中一件，比如「上次你说……」，再接着聊。")
    else:
        lines.append("需要时自然地用上，不要每句都提。")
    return "\n".join(lines)
