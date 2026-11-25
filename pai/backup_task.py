# -*- coding: utf-8 -*-
"""每日备份：调用现有 backup.create，保留最近 7 份。"""
from __future__ import annotations

import pathlib

from pai import backup as B

CAPABILITY = {
    "name": "每日备份",
    "switch_env": "PAI_BACKUP",
    "default_on": True,
    "stores_user_text": False,
    "network": False,
    "degrade": "备份失败=记录错误并保留旧备份；不删除唯一一份",
    "approval": "ADR-046：本地既成备份任务，不新增开关",
}

KEEP = 7


def run():
    r = B.create()
    outdir = pathlib.Path(r["path"]).parent
    files = sorted(outdir.glob("qingniao-backup-*.zip"))
    removed = 0
    for old in files[:-KEEP]:
        try:
            old.unlink()
            removed += 1
        except OSError:
            pass
    return {**r, "removed": removed, "kept": KEEP}


def latest():
    outdir = B.ROOT / "backups"
    files = sorted(outdir.glob("qingniao-backup-*.zip"))
    if not files:
        return {"ok": False, "path": "", "count": 0}
    p = files[-1]
    return {"ok": True, "path": str(p), "count": len(files),
            "bytes": p.stat().st_size, "at": p.stat().st_mtime}
