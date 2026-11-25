# -*- coding: utf-8 -*-
"""青鸟备份：把记忆库、文档索引、笔记、风格和审批记录打成一个 zip。"""
from __future__ import annotations

from pai import paths

import datetime
import json
import os
import pathlib

from pai import atomic as AT
import shutil
import sqlite3
import tempfile
import zipfile

CAPABILITY = {
    "name": "青鸟备份",
    "switch_env": "NONE",
    "default_on": True,
    "stores_user_text": False,
    "network": False,
    "degrade": "源文件缺失=跳过；SQLite 用 backup API 保证一致；失败明确报错",
    "approval": "ADR-046：本地单用户备份登记，不发明新开关",
}

ROOT = paths.DATA_DIR
DATA = ROOT


def _copy_db(src, dst):
    if not src.exists():
        return
    c = sqlite3.connect(str(src))
    try:
        d = sqlite3.connect(str(dst))
        try:
            c.backup(d)
        finally:
            d.close()
    finally:
        c.close()


def create(dest_dir=None):
    outdir = pathlib.Path(dest_dir or ROOT / "backups")
    outdir.mkdir(parents=True, exist_ok=True)
    stamp = datetime.datetime.now().strftime("%Y%m%d-%H%M%S")
    out = outdir / ("qingniao-backup-%s.zip" % stamp)
    with tempfile.TemporaryDirectory(prefix="qingniao-backup-") as tmp:
        tmp = pathlib.Path(tmp)
        (tmp / "data").mkdir()
        # 所有本地 SQLite 走 backup API；小 JSON 原样复制；不备份 traces/大文件。
        skip = {"traces", "models", "edge_profile", "browser_profile", "backups"}
        for src in sorted(DATA.iterdir()):
            if src.name in skip or not src.is_file():
                continue
            if src.suffix == ".db":
                _copy_db(src, tmp / "data" / src.name)
            elif src.suffix in (".json", ".jsonl"):
                shutil.copy2(src, tmp / "data" / src.name)
        notes = DATA / "notes"
        if notes.exists():
            shutil.copytree(notes, tmp / "data" / "notes")
        meta = {"app": "知衡", "created_at": datetime.datetime.now().isoformat(timespec="seconds")}
        AT.write_text(tmp / "backup.json", json.dumps(meta, ensure_ascii=False, indent=2), encoding="utf-8")
        with zipfile.ZipFile(out, "w", zipfile.ZIP_DEFLATED) as z:
            for p in tmp.rglob("*"):
                if p.is_file():
                    z.write(p, p.relative_to(tmp))
    return {"ok": True, "path": str(out), "bytes": out.stat().st_size}


def main():
    print(json.dumps(create(), ensure_ascii=False))


if __name__ == "__main__":
    main()
