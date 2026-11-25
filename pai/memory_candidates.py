# -*- coding: utf-8 -*-
"""从每轮对话抽记忆候选；候选要人批准才进 Mirrai 记忆层。"""
from __future__ import annotations

import datetime
import hashlib
import json
import pathlib
import threading

from pai import mirrai_memory as MM

ROOT = pathlib.Path(__file__).resolve().parent.parent
PATH = ROOT / "data" / "memory_candidates.json"
_LOCK = threading.Lock()

CAPABILITY = {
    "name": "记忆候选抽取",
    "switch_env": "PAI_MEMORY_CANDIDATES",
    "default_on": True,
    "stores_user_text": True,
    "text_hosts_ref": "pai/memory.py:_TEXT_HOSTS",
    "network": False,
    "degrade": "文件损坏=空候选；不自动写入正式记忆",
    "approval": "ADR-046：候选必须人工批准",
}

PATTERNS = ("我喜欢", "我不喜欢", "我的生日", "我习惯", "我家人", "我的项目",
            "我的目标", "我承诺", "记住", "以后", "每天", "每周", "下次")


def _read():
    try:
        d = json.loads(PATH.read_text(encoding="utf-8"))
        return d if isinstance(d, list) else []
    except Exception:
        return []


def _write(rows):
    PATH.parent.mkdir(parents=True, exist_ok=True)
    tmp = PATH.with_suffix(".tmp")
    tmp.write_text(json.dumps(rows, ensure_ascii=False, indent=2), encoding="utf-8")
    tmp.replace(PATH)


def extract(text, reply=""):
    t = str(text or "").strip()
    if not t:
        return []
    sentences = [x.strip() for x in t.replace("！", "。").replace("？", "。").split("。") if x.strip()]
    picks = [s for s in sentences if any(p in s for p in PATTERNS)][:5]
    if not picks:
        return []
    with _LOCK:
        rows = _read()
        known = {r.get("title") for r in rows}
        added = []
        for s in picks:
            title = s[:120]
            if title in known:
                continue
            rid = hashlib.sha1((title + datetime.datetime.now().isoformat()).encode()).hexdigest()[:10]
            rows.append({"id": rid, "title": title, "status": "pending",
                         "at": datetime.datetime.now().astimezone().isoformat(timespec="seconds")})
            added.append(rid)
        _write(rows[-200:])
    return added


def pending():
    return [r for r in _read() if r.get("status") == "pending"]


def approve(cid, category="memory", anchor=False):
    with _LOCK:
        rows = _read()
        row = next((r for r in rows if r.get("id") == cid), None)
        if not row:
            raise ValueError("候选不存在")
        MM.add(row["title"], category=category, anchor=anchor)
        row["status"] = "approved"
        _write(rows)
    return {"ok": True, "id": cid}


def reject(cid):
    with _LOCK:
        rows = _read()
        row = next((r for r in rows if r.get("id") == cid), None)
        if not row:
            raise ValueError("候选不存在")
        row["status"] = "rejected"
        _write(rows)
    return {"ok": True, "id": cid}
