# -*- coding: utf-8 -*-
"""Agent Registry：代理身份、负责人、权限范围、停用。"""
from __future__ import annotations

import datetime
import json
import os
import pathlib
import secrets
import threading

from pai import atomic as AT

ROOT = pathlib.Path(__file__).resolve().parent.parent
PATH = ROOT / "data" / "agents.json"
_LOCK = threading.RLock()

CAPABILITY = {
    "name": "Agent Registry",
    "switch_env": "PAI_AGENT_REGISTRY",
    "default_on": True,
    "stores_user_text": False,
    "network": False,
    "degrade": "文件缺失=内置代理；写失败=拒绝新代理；不静默放权",
    "approval": "ADR-046：本地既成常开治理表，不新增开关",
}

SCOPES = ("job:create", "job:claim", "read", "approve")
BUILTIN = {
    "system": {"name": "system", "owner": "Boss", "scopes": list(SCOPES), "status": "active"},
    "daily": {"name": "daily", "owner": "Boss", "scopes": ["job:create", "read"], "status": "active"},
    "proactive": {"name": "proactive", "owner": "Boss", "scopes": ["job:create", "read"], "status": "active"},
    "manual": {"name": "manual", "owner": "Boss", "scopes": ["job:create", "job:claim", "read"], "status": "active"},
}


def _now():
    return datetime.datetime.now().astimezone().isoformat(timespec="seconds")


def _read():
    try:
        d = json.loads(PATH.read_text(encoding="utf-8"))
        return d if isinstance(d, dict) else {}
    except Exception:
        return {}


def _write(d):
    AT.write_text(PATH, json.dumps(d, ensure_ascii=False, indent=2), encoding="utf-8")


def _all():
    d = dict(BUILTIN)
    d.update(_read())
    return d


def list_agents():
    return [dict(v, id=k) for k, v in sorted(_all().items())]


def get(agent_id):
    d = _all().get(str(agent_id or ""))
    return dict(d, id=str(agent_id)) if d else None


def active(agent_id):
    a = get(agent_id)
    return bool(a and a.get("status") == "active")


def allowed(agent_id, scope):
    a = get(agent_id)
    if not a:
        return False
    return a.get("status") == "active" and str(scope) in (a.get("scopes") or [])


def register(name, owner="Boss", scopes=None):
    name = str(name or "").strip()
    if not name:
        raise ValueError("name 不能为空")
    agent_id = "a_" + secrets.token_hex(6)
    with _LOCK:
        d = _read()
        d[agent_id] = {"name": name[:80], "owner": str(owner or "Boss")[:80],
                       "scopes": [s for s in (scopes or ["job:create", "read"]) if s in SCOPES],
                       "status": "active", "created_at": _now()}
        _write(d)
    return get(agent_id)


def revoke(agent_id):
    agent_id = str(agent_id or "")
    if agent_id in BUILTIN:
        raise ValueError("内置代理不能停用：%s" % agent_id)
    with _LOCK:
        d = _read()
        if agent_id not in d:
            raise KeyError(agent_id)
        d[agent_id]["status"] = "revoked"
        d[agent_id]["revoked_at"] = _now()
        _write(d)
    return get(agent_id)


def enable(agent_id):
    with _LOCK:
        d = _read()
        if agent_id not in d:
            raise KeyError(agent_id)
        d[agent_id]["status"] = "active"
        d[agent_id].pop("revoked_at", None)
        _write(d)
    return get(agent_id)
