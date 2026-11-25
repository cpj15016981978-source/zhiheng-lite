# -*- coding: utf-8 -*-
"""原子写：同目录临时文件 + os.replace，避免写一半截断。"""
from __future__ import annotations

import os
import pathlib
import uuid

CAPABILITY = {
    "name": "原子写工具",
    "switch_env": "NONE",
    "default_on": True,
    "stores_user_text": False,
    "network": False,
    "degrade": "临时写失败=抛错；不留下半成品；调用方决定是否回退",
    "approval": "ADR-046：本地既成常开工具，不新增开关",
}


def write_text(path, text, encoding="utf-8"):
    p = pathlib.Path(path)
    p.parent.mkdir(parents=True, exist_ok=True)
    tmp = p.with_name(".%s.%s.tmp" % (p.name, uuid.uuid4().hex[:8]))
    try:
        tmp.write_text(str(text), encoding=encoding, newline="")
        os.replace(str(tmp), str(p))
    finally:
        try:
            if tmp.exists():
                tmp.unlink()
        except OSError:
            pass
    return p


def write_bytes(path, data):
    p = pathlib.Path(path)
    p.parent.mkdir(parents=True, exist_ok=True)
    tmp = p.with_name(".%s.%s.tmp" % (p.name, uuid.uuid4().hex[:8]))
    try:
        tmp.write_bytes(data)
        os.replace(str(tmp), str(p))
    finally:
        try:
            if tmp.exists():
                tmp.unlink()
        except OSError:
            pass
    return p
