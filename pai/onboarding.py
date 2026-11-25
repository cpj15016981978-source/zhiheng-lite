# -*- coding: utf-8 -*-
"""首次启动引导：产品化入口，不依赖本机预装。"""
from __future__ import annotations

import json
import pathlib

from pai import atomic as AT
from pai import paths

FLAG = paths.DATA_DIR / "onboarding.json"

CAPABILITY = {
    "name": "首次启动引导",
    "switch_env": "PAI_ONBOARDING",
    "default_on": True,
    "stores_user_text": False,
    "network": False,
    "degrade": "读失败=视为未完成，不删除用户数据",
    "approval": "ADR-046：本地首次运行状态，不新增开关",
}


def status():
    try:
        d = json.loads(FLAG.read_text(encoding="utf-8"))
    except Exception:
        d = {}
    return {"done": bool(d.get("done")), "data_dir": str(paths.DATA_DIR),
            "core_model": "qwen2.5:1.5b", "work_dir": str(paths.WORKSPACE)}


def complete():
    AT.write_text(FLAG, json.dumps({"done": True}, ensure_ascii=False), encoding="utf-8")
    return status()
