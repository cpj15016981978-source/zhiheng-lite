# -*- coding: utf-8 -*-
"""写作能力 v1：把写作任务整理成稳定提示，交给本地/在线模型出稿。"""
from __future__ import annotations

CAPABILITY = {
    "name": "写作能力 v1",
    "switch_env": "NONE",
    "default_on": True,
    "stores_user_text": False,
    "network": False,
    "degrade": "缺主题/未知任务=ValueError；只生成提示，不出网、不写库",
    "approval": "ADR-046：没有开关的既成常开登记，不发明新开关",
}

TASKS = {
    "draft": "写初稿",
    "outline": "列大纲",
    "polish": "润色",
    "rewrite": "改写",
    "shorten": "缩短",
    "expand": "扩写",
}

RULES = {
    "draft": "直接给完整初稿，结构清楚，能直接使用。",
    "outline": "先给一级/二级大纲，每节一句话说明，不要展开成文。",
    "polish": "保留原意与事实，改掉啰嗦、歧义和生硬表达。",
    "rewrite": "保留关键信息，换一种更清楚的写法。",
    "shorten": "删掉重复和空话，保留事实与结论。",
    "expand": "围绕原有观点补充细节、例子和过渡，不编造事实。",
}


def build_prompt(task="draft", topic="", audience="", tone="", length="",
                 material="", style_block=""):
    task = str(task or "draft").strip()
    topic = str(topic or "").strip()
    if task not in TASKS:
        raise ValueError("未知写作任务：%s" % task)
    if not topic:
        raise ValueError("缺主题")
    parts = [
        "你是时中。请完成一个写作任务。",
        "任务：%s" % TASKS[task],
        "主题：%s" % topic,
        "读者：%s" % (audience or "不指定"),
        "语气：%s" % (tone or "清楚、自然、不端着"),
        "长度：%s" % (length or "适中"),
        "要求：%s" % RULES[task],
        "不要解释你正在写；直接给成品。事实不确定就写明不确定，不要编。",
    ]
    if style_block:
        parts += ["", str(style_block)]
    if material:
        parts += ["素材：", str(material)[:12000]]
    return "\n".join(parts)
