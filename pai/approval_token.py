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
    "degrade": "默认要求 token；缺/过期/复用一律拒绝；pending 只落 id 不落 token 明文",
    "approval": "ADR-086：常开审批边界；高风险动作需本机 UI nonce + Origin 双门",
}

_LOCK = threading.Lock()
_TOKENS = {}
_PENDING = {}
TTL_SECONDS = 120
PENDING = pathlib.Path(os.environ.get("PAI_DATA_DIR") or (pathlib.Path(os.environ.get("LOCALAPPDATA") or (pathlib.Path.home() / "AppData" / "Local")) / "Zhiheng" / "data")) / "approval_pending.json"


def _load_pending():
    try:
        if PENDING.exists():
            for r in json.loads(PENDING.read_text(encoding="utf-8")):
                if not isinstance(r, dict):
                    continue
                rid = r.get("id") or ""
                if rid and r.get("expires", 0) > _now() and not r.get("used"):
                    _PENDING[rid] = dict(r)
    except Exception:
        pass


def _save_pending():
    try:
        PENDING.parent.mkdir(parents=True, exist_ok=True)
        rows = []
        for rec in list(_TOKENS.values()) + list(_PENDING.values()):
            if rec.get("used"):
                continue
            rows.append({k: rec.get(k) for k in ("id", "kind", "detail", "expires", "bind", "action")})
        # 只落 id/动作，不落 token 明文。
        PENDING.write_text(json.dumps(rows, ensure_ascii=False, indent=2), encoding="utf-8")
    except Exception:
        pass


def pending():
    with _LOCK:
        rows = {}
        for r in list(_TOKENS.values()) + list(_PENDING.values()):
            if r.get("used"):
                continue
            rows[r.get("id")] = r
        return [{"id": r.get("id"), "kind": r.get("kind"), "detail": r.get("detail"),
                 "expires": r.get("expires"), "used": r.get("used"),
                 "can_execute": bool(r.get("action"))} for r in rows.values()]


def take(rid):
    """取走一条待审批并标记已用；只接受完整 id，空串一律拒绝。"""
    rid = str(rid or "")
    if not rid:
        return None
    with _LOCK:
        token = next((t for t, rec in _TOKENS.items() if rec.get("id") == rid), "")
        rec = _TOKENS.get(token) if token else _PENDING.get(rid)
        if not rec or rec.get("used"):
            return None
        rec["used"] = True
        other = dict(rec)
    _save_pending()
    return other


def approve_id(rid):
    rid = str(rid or "")
    if not rid:
        return ""
    with _LOCK:
        token = next((t for t, rec in _TOKENS.items() if rec.get("id") == rid), "")
    return token or ""


def deny_id(rid):
    rid = str(rid or "")
    if not rid:
        return False
    with _LOCK:
        token = next((t for t, rec in _TOKENS.items() if rec.get("id") == rid), "")
        rec = _TOKENS.pop(token, None) if token else _PENDING.pop(rid, None)
        _PENDING.pop(rid, None)
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
    rid = secrets.token_hex(6)
    with _LOCK:
        _TOKENS[token] = {"id": rid, "token": token, "kind": str(kind or ""), "detail": str(detail or ""),
                          "expires": _now() + TTL_SECONDS, "used": False, "bind": str(bind or ""), "action": action or {}}
    _save_pending()
    return {"id": rid, "token": token, "expires_in": TTL_SECONDS, "kind": str(kind or "")}


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
        _PENDING.clear()


_load_pending()
