# -*- coding: utf-8 -*-
"""可分发路径：程序目录与用户数据目录分离。"""
from __future__ import annotations

import os
import pathlib
import shutil

APP_ROOT = pathlib.Path(__file__).resolve().parent.parent
LEGACY_DATA = APP_ROOT / "data"

def _default_data():
    if os.name == "nt":
        base = os.environ.get("LOCALAPPDATA") or str(pathlib.Path.home() / "AppData" / "Local")
        return pathlib.Path(base) / "Zhiheng" / "data"
    return pathlib.Path.home() / ".local" / "share" / "zhiheng"

DATA_DIR = pathlib.Path(os.environ.get("PAI_DATA_DIR") or _default_data())
MARKER = DATA_DIR / ".migrated"
WORKSPACE = DATA_DIR / "workspace"
OFFICE_OUT = DATA_DIR / "office_out"
BACKUP_DIR = DATA_DIR / "backups"
INBOX = DATA_DIR / "inbox"

CAPABILITY = {
    "name": "可分发路径",
    "switch_env": "PAI_DATA_DIR",
    "default_on": True,
    "stores_user_text": False,
    "network": False,
    "degrade": "迁移失败=保留旧数据并明确报错；不覆盖已有用户数据",
    "approval": "ADR-046：本地路径策略，不新增开关",
}


def ensure():
    DATA_DIR.mkdir(parents=True, exist_ok=True)
    WORKSPACE.mkdir(parents=True, exist_ok=True)
    OFFICE_OUT.mkdir(parents=True, exist_ok=True)
    BACKUP_DIR.mkdir(parents=True, exist_ok=True)
    INBOX.mkdir(parents=True, exist_ok=True)
    if not MARKER.exists() and LEGACY_DATA.exists() and LEGACY_DATA.resolve() != DATA_DIR.resolve():
        for src in LEGACY_DATA.iterdir():
            if src.name in ("backups", "__pycache__"):
                continue
            dst = DATA_DIR / src.name
            if dst.exists():
                continue
            if src.is_dir():
                shutil.copytree(src, dst)
            else:
                shutil.copy2(src, dst)
        MARKER.write_text("migrated", encoding="utf-8")
    elif not MARKER.exists():
        MARKER.write_text("new", encoding="utf-8")
    return DATA_DIR


ensure()
