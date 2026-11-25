# -*- coding: utf-8 -*-
"""API Key 管理：只存本机，不返回明文。"""
from __future__ import annotations

import base64
import ctypes
import json
import os
import pathlib
from ctypes import wintypes

from pai import atomic as AT
from pai import paths

PATH = paths.DATA_DIR / "providers.json"

CAPABILITY = {
    "name": "API Key 管理",
    "switch_env": "PAI_PROVIDERS",
    "default_on": True,
    "stores_user_text": False,
    "network": False,
    "degrade": "读失败=视为未配置；只有显式配置才设置环境变量",
    "approval": "ADR-046：本地凭证文件，界面不显示明文",
}

ENV = {"deepseek": "DEEPSEEK_API_KEY", "openai": "OPENAI_API_KEY",
       "zhipu": "ZHIPU_API_KEY", "moonshot": "MOONSHOT_API_KEY",
       "siliconflow": "SILICONFLOW_API_KEY"}


class _DATA_BLOB(ctypes.Structure):
    _fields_ = [("cbData", wintypes.DWORD), ("pbData", ctypes.POINTER(ctypes.c_char))]


def _blob(data):
    buf = ctypes.create_string_buffer(data)
    return _DATA_BLOB(len(data), ctypes.cast(buf, ctypes.POINTER(ctypes.c_char)))


def _protect(text):
    if os.name != "nt":
        raise RuntimeError("凭证加密仅支持 Windows DPAPI")
    raw = ctypes.create_string_buffer(str(text).encode("utf-8"))
    inp = _DATA_BLOB(len(raw.raw) - 1, ctypes.cast(raw, ctypes.POINTER(ctypes.c_char)))
    out = _DATA_BLOB()
    if not ctypes.windll.crypt32.CryptProtectData(ctypes.byref(inp), None, None, None, None, 0, ctypes.byref(out)):
        raise OSError("CryptProtectData 失败")
    try:
        return base64.b64encode(ctypes.string_at(out.pbData, out.cbData)).decode("ascii")
    finally:
        ctypes.windll.kernel32.LocalFree(out.pbData)


def _unprotect(token):
    if os.name != "nt":
        raise RuntimeError("凭证解密仅支持 Windows DPAPI")
    raw = base64.b64decode(token)
    inp = _blob(raw)
    out = _DATA_BLOB()
    if not ctypes.windll.crypt32.CryptUnprotectData(ctypes.byref(inp), None, None, None, None, 0, ctypes.byref(out)):
        raise OSError("CryptUnprotectData 失败")
    try:
        return ctypes.string_at(out.pbData, out.cbData).decode("utf-8")
    finally:
        ctypes.windll.kernel32.LocalFree(out.pbData)


def _load():
    try:
        d = json.loads(PATH.read_text(encoding="utf-8"))
    except Exception:
        return {}
    if not isinstance(d, dict):
        return {}
    for v in d.values():
        if isinstance(v, dict) and v.get("key_enc"):
            try:
                v["key"] = _unprotect(v["key_enc"])
            except Exception:
                v["key"] = ""
    return d


def _save(d):
    clean = {}
    for k, v in d.items():
        if not isinstance(v, dict):
            continue
        item = {"base_url": str(v.get("base_url") or "")}
        if v.get("key"):
            item["key_enc"] = _protect(v["key"])
        clean[k] = item
    AT.write_text(PATH, json.dumps(clean, ensure_ascii=False, indent=2), encoding="utf-8")
    try:
        os.chmod(PATH, 0o600)
    except OSError:
        pass


def status():
    d = _load()
    return {"providers": [{"name": k, "configured": bool((v or {}).get("key")),
                           "base_url": (v or {}).get("base_url", "")}
                          for k, v in d.items()]}


def set_key(provider, key, base_url=""):
    p = str(provider or "").strip().lower()
    if p not in ENV:
        raise ValueError("不支持的 provider：%s" % p)
    key = str(key or "").strip()
    if not key:
        raise ValueError("Key 为空")
    d = _load()
    d[p] = {"key": key, "base_url": str(base_url or "").strip()}
    _save(d)
    return status()


def delete(provider):
    d = _load()
    d.pop(str(provider or "").strip().lower(), None)
    _save(d)
    return status()


def apply():
    for p, v in _load().items():
        if v.get("key"):
            os.environ[ENV[p]] = v["key"]
            if v.get("base_url"):
                os.environ["PAI_%s_BASE_URL" % p.upper()] = v["base_url"]
    return status()
