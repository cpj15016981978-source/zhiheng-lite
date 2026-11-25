# -*- coding: utf-8 -*-
"""离线本地搜索：查本机记忆和记忆条目，不出网。"""
from __future__ import annotations

CAPABILITY = {
    "name": "本地记忆搜索",
    "switch_env": "NONE",
    "default_on": True,
    "stores_user_text": False,
    "network": False,
    "degrade": "库不可用/无命中=返回空；只读本机记忆，不调模型、不出网",
    "approval": "ADR-046：没有开关的既成常开登记，不发明新开关",
}


def search(mem, query, limit=5):
    q = str(query or "").strip()
    if not q:
        return []
    out, seen = [], set()
    try:
        hits = mem.recall(q, k=limit)
    except Exception:
        hits = []
    for hit in hits:
        item = hit.get("item") or {}
        text = item.get("content") or " ".join(
            str(item.get(k) or "") for k in ("subject", "predicate", "object"))
        text = str(text).strip()
        if not text or text in seen:
            continue
        seen.add(text)
        out.append({"title": "记忆 · " + str(item.get("type") or "episode"),
                    "content": text[:800], "source": "memory"})
    try:
        items = mem.recall_items(q, k=limit)
    except Exception:
        items = []
    for it in items or []:
        text = str(it.get("text") or it.get("summary") or "").strip()
        if not text or text in seen:
            continue
        seen.add(text)
        out.append({"title": "记忆条目 · " + str(it.get("kind") or "note"),
                    "content": text[:800], "source": "memory_item"})
    return out[:limit]
