# -*- coding: utf-8 -*-
"""使用场景：免费本地 / 本地增强 / 混合 / API / 隐私离线。"""
from __future__ import annotations

from pai import paths

import json
import os
import pathlib

from pai import atomic as AT

ROOT = pathlib.Path(__file__).resolve().parent.parent
FLAG = paths.DATA_DIR / "model_mode.json"

CAPABILITY = {
    "name": "模型模式",
    "switch_env": "PAI_MODEL_MODE",
    "default_on": False,
    "stores_user_text": False,
    "network": False,
    "degrade": "未知模式=拒绝；云端缺 Key=提示，不静默离线；本地模型缺失=提示",
    "approval": "ADR-046：本地单用户模型档，默认关，用户显式开启",
}

MODES = {
    "low": {"label": "免费本地", "provider": "ollama", "model": "qwen2.5:1.5b",
            "offline": True, "router": "qwen3:0.6b", "small": "qwen2.5:1.5b",
            "main": "qwen2.5:1.5b", "ctx": "2048"},
    "balanced": {"label": "本地增强", "provider": "ollama", "model": "qwen3-4b-instruct:latest",
                 "offline": True, "router": "qwen3:0.6b", "small": "qwen2.5:1.5b",
                 "main": "qwen3-4b-instruct:latest", "ctx": "4096"},
    "hybrid": {"label": "混合 · 本地优先/API兜底", "provider": "ollama",
               "model": "qwen3-4b-instruct:latest", "offline": False,
               "router": "qwen3:0.6b", "small": "qwen2.5:1.5b",
               "main": "qwen3-4b-instruct:latest", "ctx": "4096", "hybrid": True},
    "cloud": {"label": "API 模式", "provider": "deepseek", "model": "deepseek-chat",
              "offline": False, "router": "", "small": "", "main": "", "ctx": "8192"},
}


def _read():
    try:
        return json.loads(FLAG.read_text(encoding="utf-8"))
    except Exception:
        return {}


def status():
    mode = str(_read().get("mode") or "")
    return {"mode": mode if mode in MODES else "custom", "modes": MODES}


def apply(mode):
    if mode not in MODES:
        raise ValueError("未知模式：%s" % mode)
    m = MODES[mode]
    os.environ["PAI_MODEL_PROVIDER"] = m["provider"]
    os.environ["PAI_MODEL_NAME"] = m["model"]
    os.environ["PAI_OLLAMA_NUM_CTX"] = m["ctx"]
    if m.get("hybrid"):
        os.environ["PAI_HYBRID"] = "1"
    else:
        os.environ.pop("PAI_HYBRID", None)
    if m["router"]:
        os.environ["PAI_CASCADE_ROUTE"] = "1"
        os.environ["PAI_CASCADE_ROUTER_MODEL"] = m["router"]
        os.environ["PAI_CASCADE_SMALL_MODEL"] = m["small"]
        os.environ["PAI_CASCADE_MAIN_MODEL"] = m["main"]
    else:
        os.environ["PAI_CASCADE_ROUTE"] = "0"
        for k in ("PAI_CASCADE_ROUTER_MODEL", "PAI_CASCADE_SMALL_MODEL", "PAI_CASCADE_MAIN_MODEL"):
            os.environ.pop(k, None)
    try:
        from pai import custom_select as CS
        CS.clear()
    except Exception:
        pass
    from pai import model_settings as MS
    MS.save(m["provider"], m["model"])
    AT.write_text(FLAG, json.dumps({"mode": mode}, ensure_ascii=False), encoding="utf-8")
    return status()


def startup_apply():
    s = status()
    return apply(s["mode"]) if s["mode"] in MODES else s
