# -*- coding: utf-8 -*-
"""Zhiheng Lite office pipeline: one text input, one structured office output."""
from __future__ import annotations

MAX_INPUT = 120_000

TASKS = {
    "minutes": {
        "label": "会议纪要",
        "system": "你是会议纪要整理员。只根据输入内容整理，不补充事实。"
                  "输出 Markdown：## 摘要、## 结论、## 待办（表格：事项|负责人|时间）。"
                  "人名、数字、日期不确定时写“待确认”。",
    },
    "summary": {
        "label": "文档摘要",
        "system": "你是文档摘要员。只根据输入内容提炼。"
                  "输出 Markdown：## 一句话结论、## 关键点（3-5条）、## 风险/待确认。"
                  "不编造原文没有的信息。",
    },
    "actions": {
        "label": "提取待办",
        "system": "你是任务提取员。只提取输入中明确或可合理推断的行动项。"
                  "输出 Markdown 表格：事项|负责人|截止时间|依据。"
                  "没有依据的负责人/时间写“待确认”。",
    },
}


def run(core, task, text, title=""):
    task = str(task or "minutes")
    if task not in TASKS:
        raise ValueError("未知任务：%s" % task)
    body = str(text or "").strip()
    if not body:
        raise ValueError("输入为空")
    if len(body) > MAX_INPUT:
        raise ValueError("输入过长，最多 %d 字" % MAX_INPUT)
    t = str(title or "").strip()
    prompt = (("标题：%s\n\n" % t) if t else "") + body
    content = core.chat([
        {"role": "system", "content": TASKS[task]["system"]},
        {"role": "user", "content": prompt},
    ])
    if not str(content or "").strip():
        raise RuntimeError("模型没有返回内容")
    return {"task": task, "title": t, "content": str(content).strip(),
            "chars": len(body)}
