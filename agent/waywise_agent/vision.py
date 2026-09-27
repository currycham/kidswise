"""视觉链路（软件文档 4.7）：对 agent 来说，视觉只是多了一句上下文，不是新模块。

    孩子「你看这是什么」→ agent 直接回一个拍照工具调用（不调大模型，快、不会乱拍）
      → xiaozhi 让板子拍照 → 视觉模型把画面变成一句描述（图片只在内存里，不落盘）
      → 描述作为工具结果回到 agent → Mumi 照常回答，顺势教一个词

xiaozhi 开了工具调用模式后，每次请求都会塞几段假的「工具调用示例」对话，这里也负责把它们去掉。
"""

import json
import re

# 孩子想让 Mumi 看东西的说法。宁可漏判也不乱拍：漏了 Mumi 会让孩子描述，乱拍则多等两秒
_ASK_TO_SEE = re.compile(
    r"(你看(?!起来|上去)|(你|快)看看|看看(这|那|我)|(你|快)?看一下(这|那|我)|快看|你瞧|瞧瞧(这|那|我)|给你看|"
    r"猜猜这是|猜一猜这|这是什么|那是什么|这个是什么|能看[见到]|看得[见到]|看到了?什么|看见了?什么|"
    r"我手(里|上)|手(里|上)(有|拿|是)|我拿(着|的)|拿的(是)?什么|"
    r"look at (this|that|my|me)|what(?: is|'s) (this|that)|can you see|what do you see|guess what)",
    re.IGNORECASE,
)

CAMERA_TOOL = re.compile(r"camera.*take_photo", re.IGNORECASE)


def wants_to_show(text: str) -> bool:
    return bool(_ASK_TO_SEE.search(text))


def with_photo_note(child_text: str, seen: str) -> str:
    """把这次的画面描述贴在孩子这句话后面：放在最后，模型才不会被前面聊过的东西带偏。"""
    if not seen:
        return f"{child_text}\n（Mumi 这次拍的照片没看清。）"
    return f"{child_text}\n（Mumi 刚刚用摄像头看到：{seen}。只根据这一次的画面回答，之前看到的东西都不算。）"


def camera_tool_name(tools: list | None) -> str | None:
    """请求里 xiaozhi 带来的设备拍照工具名（设备 MCP 的 self.camera.take_photo 会被改成 self_camera_take_photo）。"""
    for t in tools or []:
        name = (t.get("function") or {}).get("name", "")
        if CAMERA_TOOL.search(name):
            return name
    return None


def photo_question(child_text: str, age: int) -> str:
    return (
        f"一个 {age} 岁的孩子举着摄像头对你说：「{child_text}」。"
        "请用一两句简单的中文描述画面里最主要的东西：它是什么、什么颜色、大概在做什么。"
        "如果孩子手里拿着东西，重点说那个东西。不要描述人的长相、衣着以外的个人特征，"
        "也不要读出画面里的文字、门牌、屏幕内容。看不清就直说看不清。"
    )


def tool_call_message(tool_name: str, question: str, call_id: str) -> dict:
    return {
        "id": call_id,
        "type": "function",
        "function": {"name": tool_name, "arguments": json.dumps({"question": question}, ensure_ascii=False)},
    }


def drop_fewshot(messages: list) -> list:
    """去掉 xiaozhi 注入的工具调用示例（tool_call id 以 fewshot 开头），连同它前面那句假的用户消息。"""
    fake_ids = {
        tc.get("id")
        for m in messages
        if m.role == "assistant" and m.tool_calls
        for tc in m.tool_calls
        if str(tc.get("id", "")).startswith("fewshot")
    }
    if not fake_ids:
        return list(messages)
    out: list = []
    for m in messages:
        if m.role == "assistant" and m.tool_calls and {tc.get("id") for tc in m.tool_calls} <= fake_ids:
            if out and out[-1].role == "user":
                out.pop()
            continue
        if m.role == "tool" and m.tool_call_id in fake_ids:
            continue
        out.append(m)
    return out


def photo_result(messages: list) -> str | None:
    """如果这次请求是拍照工具返回后的续答，取出画面描述；否则返回 None。"""
    if not messages or messages[-1].role != "tool":
        return None
    call_id = messages[-1].tool_call_id
    for m in reversed(messages[:-1]):
        if m.role == "assistant" and m.tool_calls:
            if any(tc.get("id") == call_id and CAMERA_TOOL.search((tc.get("function") or {}).get("name", "")) for tc in m.tool_calls):
                return _describe(messages[-1].content)
            return None
    return None


# 视觉模型说看不清时，当作没拍到：只让 Mumi 请孩子再试，不给它编造的机会（实测它会说出「I see a kite」）
_UNCLEAR = re.compile(r"(看不清|看不太清|无法辨认|无法识别|模糊|失真|扭曲|太暗|很暗|全黑|过曝)")


def _describe(content) -> str:
    """拍照结果 → 画面描述；返回空串表示没拍清楚。"""
    text = content if isinstance(content, str) else json.dumps(content, ensure_ascii=False)
    try:
        data = json.loads(text)
    except (TypeError, ValueError):
        data = None
    if isinstance(data, dict):
        if data.get("success") is False:
            return ""
        text = next((data[k] for k in ("text", "result", "response", "message", "content") if isinstance(data.get(k), str)), text)
    text = text.strip()
    return "" if _UNCLEAR.search(text) else text
