# -*- coding: utf-8 -*-
"""全局主模型设置：切换后写盘，重启仍生效。"""
from __future__ import annotations

from pai import paths

import json
import os
import pathlib

from pai import atomic as AT

ROOT = paths.DATA_DIR
PATH = ROOT / "model_settings.json"

CAPABILITY = {
    "name": "全局主模型设置",
    "switch_env": "NONE",
    "default_on": True,
    "stores_user_text": False,
    "network": False,
    "degrade": "文件缺失=沿用环境变量；损坏=忽略并明确记录，不静默改模型",
    "approval": "ADR-046：本地既成常开设置，不新增开关",
}


def load():
    try:
        d = json.loads(PATH.read_text(encoding="utf-8"))
        return d if isinstance(d, dict) else {}
    except Exception:
        return {}


def save(provider="", model="", offline=None):
    # offline 参数只为旧调用兼容；真值只归 network_policy，不落进本文件。
    d = {"provider": str(provider or ""), "model": str(model or "")}
    AT.write_text(PATH, json.dumps(d, ensure_ascii=False, indent=2), encoding="utf-8")
    return d


def apply():
    d = load()
    provider = str(d.get("provider") or "").strip().lower()
    if provider:
        os.environ["PAI_MODEL_PROVIDER"] = provider
        if d.get("model"):
            os.environ["PAI_MODEL_NAME"] = str(d["model"])
    return d


def current():
    from pai import network_policy as NP
    return {"provider": (os.environ.get("PAI_MODEL_PROVIDER") or "").strip().lower(),
            "model": os.environ.get("PAI_MODEL_NAME") or "",
            "offline": NP.is_offline()}
