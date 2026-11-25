# -*- coding: utf-8 -*-
"""角色开关：只控制 ModelRouter 是否允许使用某角色，不改 provider 配置。"""
from __future__ import annotations

import json
import pathlib

from pai import atomic as AT

ROOT = pathlib.Path(__file__).resolve().parent.parent
PATH = ROOT / "data" / "role_settings.json"

CAPABILITY = {
    "name": "角色开关持久化",
    "switch_env": "PAI_ROLES_CONFIG",
    "default_on": False,
    "stores_user_text": False,
    "network": False,
    "degrade": "文件损坏时按全开处理，不阻断 ModelRouter",
    "approval": "ADR-044 角色开关登记",
}


def read() -> dict:
    try:
        data = json.loads(PATH.read_text(encoding="utf-8"))
        return data if isinstance(data, dict) else {}
    except Exception:
        return {}


def enabled(role: str) -> bool:
    return bool(read().get(role, True))


def set_enabled(role: str, on: bool) -> None:
    data = read()
    data[str(role)] = bool(on)
    PATH.parent.mkdir(parents=True, exist_ok=True)
    AT.write_text(PATH, json.dumps(data, ensure_ascii=False, indent=2), encoding="utf-8")


def all_roles() -> dict:
    return {r: enabled(r) for r in ("chat", "embed", "rerank", "asr", "tts", "vision", "guard", "judge")}
