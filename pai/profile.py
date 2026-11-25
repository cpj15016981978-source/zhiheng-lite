# -*- coding: utf-8 -*-
"""Profile 隔离：工作 / 副业 / 学习 / 游戏 各自一套数据。

默认 work 仍用旧 data/memory.db，避免升级时丢记忆；
其他 profile 用 data/profiles/<name>/memory.db。
"""
from __future__ import annotations

from pai import paths

import os
import pathlib

PROFILES = ("work", "side", "study", "game")
LEGACY = "work"
BASE = paths.DATA_DIR

CAPABILITY = {
    "name": "Profile 隔离",
    "switch_env": "NONE",
    "default_on": True,
    "stores_user_text": False,
    "network": False,
    "degrade": "profile 名非法=抛 ProfileError；目录按项目 data/ 分，找不到也不回退到别的 profile",
    "approval": "ADR-046：没有开关的既成常开登记，不发明新开关",
}


class ProfileError(ValueError):
    pass


def normalize(name=None):
    p = str(name or os.environ.get("PAI_PROFILE") or LEGACY).strip().lower()
    if p not in PROFILES:
        raise ProfileError("未知 profile：%s（可选：%s）" % (p, "、".join(PROFILES)))
    return p


def current():
    return normalize()


def data_root(profile=None):
    return BASE if normalize(profile) == LEGACY else BASE / "profiles" / normalize(profile)


def db_path(profile=None):
    p = normalize(profile)
    return BASE / "memory.db" if p == LEGACY else BASE / "profiles" / p / "memory.db"


def wiki_dir(profile=None):
    return data_root(profile) / "wiki"


def skills_dir(profile=None):
    p = normalize(profile)
    if p == LEGACY:
        return pathlib.Path(os.environ.get("PAI_SKILLS_DIR") or str(BASE / "skills"))
    return data_root(p) / "skills"


def logs_dir(profile=None):
    return data_root(profile) / "logs"


def ensure(profile=None):
    d = data_root(profile)
    d.mkdir(parents=True, exist_ok=True)
    return d
