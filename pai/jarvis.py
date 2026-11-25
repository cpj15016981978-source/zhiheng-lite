# -*- coding: utf-8 -*-
"""贾维斯上下文 v1：把身份、承诺、任务、待批合成一块，不再各说各的。"""
from __future__ import annotations

import pathlib

from pai import continuity as CT
from pai import proactive as PA


CAPABILITY = {
    "name": "贾维斯上下文",
    "switch_env": "PAI_JARVIS",
    "default_on": True,
    "stores_user_text": False,
    "network": False,
    "degrade": "读不到续线/任务/提案=返回空段，不假装有上下文",
    "approval": "ADR-046：本地上下文只读，不新增开关",
}

ROOT = pathlib.Path(__file__).resolve().parent.parent


def _tasks(tasks, profile):
    if tasks is None:
        return []
    try:
        rows = tasks.list(profile, limit=8)
        return [x for x in rows if x.get("state") not in ("succeeded", "cancelled")]
    except Exception:
        return []


def snapshot(profile="work", tasks=None):
    due = CT.due(profile)
    threads = CT.all_threads(profile)[:8]
    proposals = PA.pending()
    task_rows = _tasks(tasks, profile)
    return {"identity": {"product": "知衡", "ai": "时中"},
            "due": due[:5], "threads": threads[:5],
            "proposals": proposals[:5], "tasks": task_rows[:5]}


def _line(x):
    return "%s：%s" % (x.get("title") or "未命名", x.get("next_step") or "继续")


def prompt_block(profile="work", tasks=None):
    s = snapshot(profile, tasks)
    lines = ["[贾维斯上下文 · 本地]",
             "产品：知衡；AI：时中；先结论，再行动；不确定就说不确定。"]
    if s["due"]:
        lines.append("到期承诺：")
        lines.extend("- " + _line(x) for x in s["due"])
    else:
        lines.append("到期承诺：无。")
    if s["threads"]:
        lines.append("未完成：")
        lines.extend("- " + (x.get("title") or "未命名") for x in s["threads"])
    if s["tasks"]:
        lines.append("任务台账：")
        lines.extend("- %s [%s]" % (x.get("title") or "未命名", x.get("state") or "") for x in s["tasks"])
    if s["proposals"]:
        lines.append("待你批准的提案：")
        lines.extend("- " + (x.get("title") or "未命名") for x in s["proposals"])
    lines.append("规则：没有批准不动手；回答先对当前问题，不续旧话题。")
    text = "\n".join(lines)
    return text[:1800]


def brief(profile="work", tasks=None):
    s = snapshot(profile, tasks)
    lines = ["贾维斯简报", "产品：知衡 / AI：时中", ""]
    lines.append("【到期】")
    lines.extend(("- " + _line(x)) for x in s["due"]) if s["due"] else lines.append("- 无")
    lines.append("【未完成】")
    lines.extend(("- " + (x.get("title") or "未命名")) for x in s["threads"]) if s["threads"] else lines.append("- 无")
    lines.append("【任务】")
    lines.extend(("- %s [%s]" % (x.get("title") or "未命名", x.get("state") or "")) for x in s["tasks"]) if s["tasks"] else lines.append("- 无")
    lines.append("【待批】")
    lines.extend(("- " + (x.get("title") or "未命名")) for x in s["proposals"]) if s["proposals"] else lines.append("- 无")
    lines.append("")
    lines.append("下一步：先处理到期第一条；需要动手时走“推进”批准。")
    return "\n".join(lines)
