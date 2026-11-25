# -*- coding: utf-8 -*-
"""记忆大脑 v1：在档案柜上做“该信哪一页”。

三层：
  memory_notebook  记事本（当前任务现场）
  memory_item      档案柜（可追溯、可冲突）
  MemoryBrain      大脑（检索、排序、冲突并列）
"""
from __future__ import annotations

import datetime as _dt
import os

CAPABILITY = {
    "name": "记忆大脑 v1",
    "switch_env": "PAI_MEMORY_BRAIN",
    "default_on": False,
    "stores_user_text": True,
    "text_hosts_ref": "pai/memory.py:_TEXT_HOSTS",
    "network": False,
    "degrade": "关态直接返回未找到；开态只读写 memory_item / memory_notebook，不调模型、不出网",
    "probe_cost": "只读模块常量",
    "approval": "未获单独批准；由调用方显式开启，不接日常",
}


def enabled():
    return (os.environ.get("PAI_MEMORY_BRAIN") or "").strip().lower() == "on"


class MemoryBrain:
    def __init__(self, memory):
        self.mem = memory

    def notebook(self, session_id):
        return self.mem.get_notebook(session_id)

    def update_notebook(self, session_id, **fields):
        data = self.mem.get_notebook(session_id)
        data.update(fields)
        data["updated_at"] = _dt.datetime.now().astimezone().isoformat(timespec="seconds")
        self.mem.set_notebook(session_id, data)
        return data

    def remember(self, text, **kwargs):
        if not enabled():
            raise RuntimeError("PAI_MEMORY_BRAIN 未开")
        return self.mem.remember_item(text, **kwargs)

    def recall(self, question, k=5):
        if not enabled():
            return {"question": question, "found": False, "items": [],
                    "conflicts": [], "context": ""}
        items = (self.mem.recall_items_semantic(question, k=max(k * 2, 8))
                 if (os.environ.get("PAI_MEMORY_SEMANTIC") or "").strip().lower() == "on"
                 else self.mem.recall_items(question, k=max(k * 2, 8)))
        items.sort(key=lambda x: (
            0 if x.get("status") == "active" else 1,
            -float(x.get("relevance") or 0),
            -float(x.get("importance") or 0),
            -float(x.get("confidence") or 0),
            str(x.get("created_at") or ""),
        ))
        items = items[:k]

        groups = {}
        for it in items:
            key = (it.get("subject") or "", it.get("predicate") or "")
            if key[0] and key[1]:
                groups.setdefault(key, []).append(it)
        conflicts = []
        for key, rows in groups.items():
            statuses = {r.get("status") for r in rows}
            if len(rows) > 1 and "superseded" in statuses and "active" in statuses:
                conflicts.append({
                    "subject": key[0],
                    "predicate": key[1],
                    "active": next(r for r in rows if r.get("status") == "active"),
                    "old": [r for r in rows if r.get("status") == "superseded"],
                })

        lines = []
        for it in items:
            tag = "现行" if it.get("status") == "active" else "旧版"
            src = it.get("source") or "未标来源"
            day = (it.get("created_at") or "")[:10]
            lines.append("- [%s｜%s｜%s] %s" % (tag, src, day, it.get("text")))
        return {
            "question": question,
            "found": bool(items),
            "items": items,
            "conflicts": conflicts,
            "context": "\n".join(lines),
        }
