# -*- coding: utf-8 -*-
"""一次性审批令牌：请求 → 人工确认 → 短时 token → 敏感动作。"""
from __future__ import annotations

import datetime
import json
import os
import pathlib
import secrets
import threading

CAPABILITY = {
    "name": "审批令牌",
    "switch_env": "PAI_REQUIRE_APPROVAL_TOKEN",
    "default_on": True,
    "stores_user_text": False,
    "network": False,
    "degrade": "未要求时=兼容旧请求；要求时缺/过期/复用 token 一律拒绝",
    "approval": "ADR-046：本地单用户审批边界，默认关，生产显式开启",
}

_LOCK = threading.Lock()
_TOKENS = {}
TTL_SECONDS = 120
PENDING = pathlib.Path(os.environ.get("PAI_DATA_DIR") or (pathlib.Path(os.environ.get("LOCALAPPDATA") or (pathlib.Path.home() / "AppData" / "Local")) / "Zhiheng" / "data")) / "approval_pending.json"


def _load_pending():
    try:
        if PENDING.exists():
            for r in json.loads(PENDING.read_text(encoding="utf-8")):
                if isinstance(r, dict) and r.get("token") and r.get("expires", 0) > _now() and not r.get("used"):
                    _TOKENS[r["token"]] = r
    except Exception:
        pass


def _save_pending():
    try:
        PENDING.parent.mkdir(parents=True, exist_ok=True)
        PENDING.write_text(json.dumps(list(_TOKENS.values()), ensure_ascii=False, indent=2), encoding="utf-8")
    except Exception:
        pass


def pending():
    with _LOCK:
        return [{"id": (r.get("token") or "")[:10], "kind": r.get("kind"), "detail": r.get("detail"),
                 "expires": r.get("expires"), "used": r.get("used"),
                 "can_execute": bool(r.get("action"))}
                for r in _TOKENS.values() if not r.get("used")]


def take(rid):
    """取走一条待审批并标记已用；返回记录（含 action）或 None。"""
    rid = str(rid or "")
    with _LOCK:
        token = next((t for t in _TOKENS if t.startswith(rid)), "")
        rec = _TOKENS.get(token)
        if not rec or rec.get("used"):
            return None
        rec["used"] = True
    _save_pending()
    return dict(rec)


def approve_id(rid):
    rid = str(rid or "")
    with _LOCK:
        token = next((t for t in _TOKENS if t.startswith(rid)), "")
    return token or ""


def deny_id(rid):
    rid = str(rid or "")
    with _LOCK:
        token = next((t for t in _TOKENS if t.startswith(rid)), "")
        rec = _TOKENS.pop(token, None) if token else None
    _save_pending()
    return bool(rec)


def deny(token):
    with _LOCK:
        rec = _TOKENS.pop(str(token or ""), None)
    _save_pending()
    return bool(rec)


def required():
    raw = os.environ.get("PAI_REQUIRE_APPROVAL_TOKEN", "").strip().lower()
    return raw not in ("0", "off", "false", "no", "disabled")


def _now():
    return datetime.datetime.now().timestamp()


def issue(kind, detail="", bind="", action=None):
    token = secrets.token_urlsafe(24)
    with _LOCK:
        _TOKENS[token] = {"token": token, "kind": str(kind or ""), "detail": str(detail or ""),
                          "expires": _now() + TTL_SECONDS, "used": False, "bind": str(bind or ""), "action": action or {}}
    _save_pending()
    return {"token": token, "expires_in": TTL_SECONDS, "kind": str(kind or "")}


def consume(token, kind, bind=None):
    with _LOCK:
        rec = _TOKENS.get(str(token or ""))
        if not rec or rec["used"]:
            return False
        if rec["expires"] < _now():
            _TOKENS.pop(str(token), None)
            return False
        if str(kind or "") and rec["kind"] != str(kind):
            return False
        if rec.get("bind") and str(bind or "") != rec["bind"]:
            return False
        rec["used"] = True
        _save_pending()
        return True


def clear():
    with _LOCK:
        _TOKENS.clear()


_load_pending()
