# -*- coding: utf-8 -*-
"""自定义模型注册：本地 Ollama 与 OpenAI 兼容 API 共用一个入口。"""
from __future__ import annotations

import json
import os
import urllib.request
import urllib.error

from pai import atomic as AT
from pai import paths
from pai import model_admin as MA

PATH = paths.DATA_DIR / "custom_models.json"

CAPABILITY = {
    "name": "自定义模型",
    "switch_env": "PAI_CUSTOM_MODELS",
    "default_on": True,
    "stores_user_text": False,
    "network": True,
    "degrade": "配置损坏=空列表；测试失败=明确返回，不静默保存",
    "approval": "ADR-046：用户显式添加模型",
}


def _load():
    try:
        d = json.loads(PATH.read_text(encoding="utf-8"))
        if not isinstance(d, list):
            return []
        changed = False
        for x in d:
            if x.get("key") and not x.get("key_enc"):
                try:
                    from pai import providers as PR
                    x["key_enc"] = PR._protect(str(x["key"]))
                    x.pop("key", None)
                    changed = True
                except Exception:
                    pass
        if changed:
            _save(d)
        return d
    except Exception:
        return []


def _save(d):
    AT.write_text(PATH, json.dumps(d, ensure_ascii=False, indent=2), encoding="utf-8")


def find(name):
    for x in _load():
        if x.get("name") == name or x.get("model") == name:
            return dict(x)
    for x in MA.installed():
        if x.get("name") == name:
            return {"name": name, "label": name, "kind": "local", "provider": "ollama",
                    "base_url": "", "model": name, "multimodal": False}
    return None


def list_models():
    out = []
    for x in _load():
        out.append({"kind": x.get("kind", "api"), "name": x.get("name"),
                    "label": x.get("label") or x.get("name"),
                    "provider": x.get("provider", ""), "base_url": x.get("base_url", ""),
                    "model": x.get("model", ""), "multimodal": bool(x.get("multimodal"))})
    for x in MA.installed():
        if x.get("name") and not any(o["name"] == x["name"] for o in out):
            out.append({"kind": "local", "name": x["name"], "label": x["name"],
                        "provider": "ollama", "base_url": "", "model": x["name"],
                        "multimodal": False})
    return out


def test(payload):
    kind = str(payload.get("kind") or "api")
    if kind == "local" or payload.get("provider") == "ollama":
        name = str(payload.get("model") or payload.get("name") or "")
        names = [x.get("name") for x in MA.installed() if x.get("name")]
        return {"ok": name in names, "detail": "本地模型已存在" if name in names else "本地模型不存在"}
    base = str(payload.get("base_url") or "").rstrip("/")
    key = str(payload.get("key") or "")
    model = str(payload.get("model") or "").strip()
    if not base or not key:
        return {"ok": False, "detail": "Base URL 和 API Key 必填"}
    if not model:
        return {"ok": False, "detail": "模型名称必填"}
    body = json.dumps({"model": model, "messages": [{"role": "user", "content": "ping"}],
                       "max_tokens": 1}).encode("utf-8")
    try:
        req = urllib.request.Request(base + "/chat/completions", data=body,
                                     headers={"Authorization": "Bearer " + key,
                                              "Content-Type": "application/json"}, method="POST")
        with urllib.request.urlopen(req, timeout=20) as r:
            return {"ok": 200 <= r.status < 300, "detail": "连接成功"}
    except urllib.error.HTTPError as e:
        detail = "连接失败：HTTP %s" % e.code
        if e.code == 429:
            detail += "（Key 已到服务端，但被限流/额度限制）"
        return {"ok": False, "detail": detail}
    except Exception as e:
        return {"ok": False, "detail": "%s: %s" % (type(e).__name__, e)}


def save(payload):
    name = str(payload.get("name") or "").strip()
    model = str(payload.get("model") or "").strip()
    if not name or not model:
        raise ValueError("配置名称和模型名称必填")
    kind = str(payload.get("kind") or "api")
    entry = {"name": name, "label": str(payload.get("label") or name),
             "kind": kind, "provider": str(payload.get("provider") or "custom"),
             "base_url": str(payload.get("base_url") or "").rstrip("/"),
             "model": model, "multimodal": bool(payload.get("multimodal"))}
    if str(payload.get("key") or "").strip():
        from pai import providers as PR
        entry["key_enc"] = PR._protect(str(payload.get("key")).strip())
    d = _load()
    d = [x for x in d if x.get("name") != name]
    d.append(entry)
    try:
        import urllib.parse
        from pai import network_policy as NP
        host = urllib.parse.urlparse(entry.get("base_url") or "").hostname
        if host:
            NP.allow_host(host)
    except Exception:
        pass
    _save(d)
    return {"ok": True, "item": entry}


def delete(name):
    d = [x for x in _load() if x.get("name") != name]
    _save(d)
    return {"ok": True}


def key_status():
    out = []
    for x in _load():
        if x.get("kind") == "api":
            out.append({"name": x.get("name"), "provider": x.get("provider") or "",
                        "base_url": x.get("base_url") or "",
                        "configured": bool(x.get("key_enc"))})
    return out
