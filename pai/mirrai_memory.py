# -*- coding: utf-8 -*-
"""Mirrai 记忆层移植：锚点、类别权重、关键词覆盖 + 新鲜度评分。

移植自 Ch1rpys/Mirrai（MIT），不搬 PostgreSQL 表结构。
"""
from __future__ import annotations

import datetime
import json
import pathlib
import re
import threading

ROOT = pathlib.Path(__file__).resolve().parent.parent
PATH = ROOT / "data" / "mirrai_memory.json"
_LOCK = threading.Lock()

CAPABILITY = {
    "name": "Mirrai 记忆层",
    "switch_env": "PAI_MIRRAI_MEMORY",
    "default_on": True,
    "stores_user_text": True,
    "text_hosts_ref": "pai/memory.py:_TEXT_HOSTS",
    "network": False,
    "degrade": "文件缺失=空记忆；写失败不影响回答",
    "approval": "ADR-046：本地 JSON，显式写入",
}

WEIGHT = {"anchor": 1.0, "milestone": 0.8, "promise": 0.7, "memory": 0.5}


def _read():
    if not PATH.exists():
        return {"anchors": [], "memories": []}
    try:
        d = json.loads(PATH.read_text(encoding="utf-8"))
        return d if isinstance(d, dict) else {"anchors": [], "memories": []}
    except Exception:
        return {"anchors": [], "memories": []}


def _write(d):
    PATH.parent.mkdir(parents=True, exist_ok=True)
    tmp = PATH.with_suffix(".tmp")
    tmp.write_text(json.dumps(d, ensure_ascii=False, indent=2), encoding="utf-8")
    tmp.replace(PATH)


def all():
    return _read()


def add(title, body="", category="memory", anchor=False):
    title = str(title or "").strip()
    if not title:
        raise ValueError("title 不能为空")
    cat = "anchor" if anchor else (category if category in WEIGHT else "memory")
    with _LOCK:
        d = _read()
        if anchor:
            d.setdefault("anchors", []).append({"title": title, "body": str(body or "").strip(),
                                                "category": cat})
            d["anchors"] = d["anchors"][-200:]
        else:
            d.setdefault("memories", []).append({"title": title, "body": str(body or "").strip(),
                                                  "category": cat})
            d["memories"] = d["memories"][-500:]
        _write(d)
    return {"ok": True, "category": cat, "title": title}


def _tokens(text):
    t = str(text or "").lower()
    return set(re.findall(r"[\u4e00-\u9fff]{2,}", t)) | set(re.findall(r"[a-z0-9]{2,}", t))


def _recency(ts, now=None):
    now = now or datetime.datetime.now()
    try:
        dt = datetime.datetime.fromisoformat(str(ts))
    except Exception:
        return 0.0
    days = max(0.0, (now - dt).total_seconds() / 86400)
    return max(0.0, 1.0 - days / 180.0)


def score(query, item, now=None):
    q = _tokens(query)
    text = str(item.get("title") or "") + " " + str(item.get("body") or "")
    m = _tokens(text)
    cover = len(q & m) / len(q) if q else 0.0
    cat = WEIGHT.get(item.get("category") or "memory", 0.5)
    fresh = _recency(item.get("at") or "", now)
    return round(0.65 * cover + 0.20 * fresh + 0.15 * cat, 4)


def recall(query, k=5, now=None):
    d = _read()
    rows = list(d.get("anchors") or []) + list(d.get("memories") or [])
    for r in rows:
        r["score"] = score(query, r, now)
    rows = [r for r in rows if r["score"] > 0]
    rows.sort(key=lambda x: x["score"], reverse=True)
    return rows[:k]


def prompt_block(query, k=5):
    anchors = _read().get("anchors") or []
    hit = recall(query, k)
    if not anchors and not hit:
        return ""
    lines = ["[Mirrai 记忆层]"]
    if anchors:
        lines.append("【锚点记忆 · 永不编造】")
        lines.extend("- " + a.get("title", "") for a in anchors[:5])
    if hit:
        lines.append("【相关记忆】")
        lines.extend("- %s（%.2f）" % (r.get("title", ""), r.get("score", 0)) for r in hit)
    return "\n".join(lines)
