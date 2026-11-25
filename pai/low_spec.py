# -*- coding: utf-8 -*-
"""低配模式：8GB/无独显只加载 1.5B，不加载 4B。"""
from __future__ import annotations

from pai import paths

import json
import os
import pathlib

from pai import atomic as AT

ROOT = pathlib.Path(__file__).resolve().parent.parent
FLAG = paths.DATA_DIR / "low_spec.json"

CAPABILITY = {
    "name": "低配模式",
    "switch_env": "PAI_LOW_SPEC",
    "default_on": False,
    "stores_user_text": False,
    "network": False,
    "degrade": "模型缺失=提示先拉取；切换失败=保留原设置，不静默降级",
    "approval": "ADR-046：本地单用户性能档，默认关，用户显式开启",
}

PRESET = {"provider": "ollama", "model": "qwen2.5:1.5b", "offline": True,
          "cascade_router": "qwen3:0.6b", "cascade_small": "qwen2.5:1.5b",
          "cascade_main": "qwen2.5:1.5b", "num_ctx": "2048"}


def status():
    try:
        d = json.loads(FLAG.read_text(encoding="utf-8"))
    except Exception:
        d = {}
    return {"enabled": bool(d.get("enabled")), **PRESET}


def enable():
    os.environ["PAI_MODEL_PROVIDER"] = PRESET["provider"]
    os.environ["PAI_MODEL_NAME"] = PRESET["model"]
    os.environ["PAI_CASCADE_ROUTER_MODEL"] = PRESET["cascade_router"]
    os.environ["PAI_CASCADE_SMALL_MODEL"] = PRESET["cascade_small"]
    os.environ["PAI_CASCADE_MAIN_MODEL"] = PRESET["cascade_main"]
    os.environ["PAI_OLLAMA_NUM_CTX"] = PRESET["num_ctx"]
    AT.write_text(FLAG, json.dumps({"enabled": True}, ensure_ascii=False), encoding="utf-8")
    from pai import model_settings as MS
    MS.save(PRESET["provider"], PRESET["model"], True)
    return status()


def disable():
    AT.write_text(FLAG, json.dumps({"enabled": False}, ensure_ascii=False), encoding="utf-8")
    return status()


def apply():
    if status().get("enabled"):
        return enable()
    return status()
