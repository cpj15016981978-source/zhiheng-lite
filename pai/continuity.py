# -*- coding: utf-8 -*-
"""Muse 连续性 v1：未完成的线、检查点、恢复。

一条线不是普通待办：它要能回答「上次停在哪、证据是什么、下一步做什么」。
"""
from __future__ import annotations

import datetime
import json
import os
import pathlib
import threading
import uuid

CAPABILITY = {
    "name": "Muse 连续性 v1",
    "switch_env": "NONE",
    "default_on": True,
    "stores_user_text": False,
    "network": False,
    "degrade": "文件坏=按空列表；无 next_step=拒绝；所有写操作本地 JSON，不调模型",
    "approval": "ADR-046：本地单用户连续性登记，不发明新开关",
}

ROOT = pathlib.Path(__file__).resolve().parent.parent
PATH = ROOT / "data" / "continuity.json"
_LOCK = threading.Lock()


class ContinuityReadError(RuntimeError):
    """文件存在但读不出来：与“不存在”严格区分，调用方不得覆盖。"""


def _read():
    if not PATH.exists():
        return []
    try:
        d = json.loads(PATH.read_text(encoding="utf-8"))
    except (OSError, ValueError) as e:
        raise ContinuityReadError("续线文件读失败：%s" % e) from e
    if not isinstance(d, list):
        raise ContinuityReadError("续线文件格式不是数组")
    return d


def _write(items):
    PATH.parent.mkdir(parents=True, exist_ok=True)
    tmp = PATH.with_suffix(".tmp")
    tmp.write_text(json.dumps(items, ensure_ascii=False, indent=2), encoding="utf-8")
    os.replace(tmp, PATH)


def _now():
    return datetime.datetime.now().astimezone().isoformat(timespec="seconds")


def all_threads(profile="work", include_done=False):
    out = []
    try:
        items = _read()
    except ContinuityReadError:
        return []
    for x in items:
        if x.get("profile", "work") != profile:
            continue
        if not include_done and x.get("status") == "done":
            continue
        out.append(x)
    return sorted(out, key=lambda x: x.get("updated", ""), reverse=True)


def save(profile="work", title="", last_step="", next_step="",
         evidence="", due="", thread_id=""):
    title = str(title or "").strip()
    next_step = str(next_step or "").strip()
    if not title:
        raise ValueError("缺 title")
    if not next_step:
        raise ValueError("缺 next_step")
    with _LOCK:
        items = _read()
        now = _now()
        rec = next((x for x in items if x.get("id") == thread_id), None) if thread_id else None
        if rec is None:
            rec = {"id": thread_id or uuid.uuid4().hex[:10], "profile": profile,
                   "created": now, "status": "open"}
            items.append(rec)
        rec.update({"title": title[:200], "last_step": str(last_step or "")[:2000],
                    "next_step": next_step[:2000], "evidence": str(evidence or "")[:2000],
                    "due": str(due or ""), "updated": now, "status": "open"})
        _write(items)
    return rec


def auto_from_chat(text, response, profile="work"):
    """只在出现“未完成/下一步/继续”等信号时，自动维护一条当前线。"""
    text = str(text or "").strip()
    response = str(response or "").strip()
    marks = ("继续", "下一步", "还没", "待办", "回头", "稍后", "记住", "未完成", "接下来")
    if not text or not response or not any(m in text + response for m in marks):
        return None
    title = text[:80]
    next_step = response[-500:]
    try:
        return save(profile=profile, title=title, last_step=text[:1000],
                    next_step=next_step, evidence="自动检查点", thread_id="auto-%s" % profile)
    except (ValueError, ContinuityReadError):
        return None


def close(profile="work", thread_id=""):
    with _LOCK:
        items = _read()
        rec = next((x for x in items if x.get("id") == thread_id and
                    x.get("profile", "work") == profile), None)
        if rec is None:
            raise ValueError("没有这条线：%s" % thread_id)
        rec["status"] = "done"
        rec["updated"] = _now()
        _write(items)
        return rec


def due(profile="work"):
    now = datetime.datetime.now().astimezone()
    out = []
    for x in all_threads(profile):
        d = str(x.get("due") or "").strip()
        if not d:
            continue
        try:
            when = datetime.datetime.fromisoformat(d)
            if when.tzinfo is None:
                when = when.astimezone()
            if when <= now:
                out.append(x)
        except ValueError:
            pass
    return out
