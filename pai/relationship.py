# -*- coding: utf-8 -*-
"""关系层：关系天数、事件、心情打卡。从 Mirrai 的关系机制移植，数据落本地 JSON。"""
from __future__ import annotations

import datetime
import json
import pathlib
import threading

ROOT = pathlib.Path(__file__).resolve().parent.parent
PATH = ROOT / "data" / "relationship.json"
_LOCK = threading.RLock()

CAPABILITY = {
    "name": "关系层",
    "switch_env": "PAI_RELATIONSHIP",
    "default_on": True,
    "stores_user_text": True,
    "text_hosts_ref": "pai/memory.py:_TEXT_HOSTS",
    "network": False,
    "degrade": "文件缺失=从今天起算；写失败不影响回答",
    "approval": "ADR-046：本地关系记录，显式写入",
}


def _read():
    try:
        d = json.loads(PATH.read_text(encoding="utf-8"))
        return d if isinstance(d, dict) else {}
    except Exception:
        return {}


def _write(d):
    PATH.parent.mkdir(parents=True, exist_ok=True)
    tmp = PATH.with_suffix(".tmp")
    tmp.write_text(json.dumps(d, ensure_ascii=False, indent=2), encoding="utf-8")
    tmp.replace(PATH)


def _now():
    return datetime.datetime.now().astimezone().isoformat(timespec="seconds")


def ensure():
    d = _read()
    if not d.get("started"):
        d["started"] = _now()
        d.setdefault("events", [])
        d.setdefault("moods", [])
        with _LOCK:
            _write(d)
    return d


def add_event(kind, text):
    with _LOCK:
        d = ensure()
        d.setdefault("events", []).append({"at": _now(), "kind": str(kind or "事"),
                                           "text": str(text or "").strip()[:200]})
        d["events"] = d["events"][-200:]
        _write(d)
    return {"ok": True}


def mood(score, note=""):
    with _LOCK:
        d = ensure()
        d.setdefault("moods", []).append({"at": _now(), "score": int(score),
                                          "note": str(note or "").strip()[:200]})
        d["moods"] = d["moods"][-200:]
        _write(d)
    return {"ok": True}


def days():
    d = ensure()
    try:
        start = datetime.datetime.fromisoformat(d["started"])
        return max(1, (datetime.datetime.now().astimezone() - start).days + 1)
    except Exception:
        return 1


def prompt_block():
    d = ensure()
    lines = ["[关系层]", "认识第 %d 天。" % days()]
    ev = (d.get("events") or [])[-3:]
    if ev:
        lines.append("最近的事：")
        lines.extend("- %s：%s" % (x.get("kind", ""), x.get("text", "")) for x in ev)
    moods = (d.get("moods") or [])[-2:]
    if moods:
        lines.append("最近心情：" + "；".join("%s %s" % (x.get("score"), x.get("note", "")) for x in moods))
    return "\n".join(lines)
