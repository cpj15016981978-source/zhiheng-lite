# -*- coding: utf-8 -*-
"""A1 活动账：给 DB 变化留机器可读的归因。

只记元数据：时间、动作、会话、是否写库、条数。
不记用户原话。
"""
import datetime
import json
import os
import threading
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
DEFAULT_DIR = ROOT / "data"
_LOCK = threading.Lock()

CAPABILITY = {
    "name": "A1 活动账",
    "switch_env": "PAI_A1_ACTIVITY",
    "default_on": False,
    "stores_user_text": False,
    "network": False,
    "degrade": "PAI_A1_ACTIVITY=off 时不写活动账；A1 归因会退化为未归因",
    "probe_cost": "只读环境变量与 data/ 路径；不联网、不调模型、不写记忆库",
    "approval": "未获单独批准：为 A1 归因新增，默认关；由 webapp/chat 打开",
}


def enabled():
    return (os.environ.get("PAI_A1_ACTIVITY") or "off").strip().lower() == "on"


def _path():
    d = Path(os.environ.get("PAI_A1_ACTIVITY_DIR") or DEFAULT_DIR)
    return d / "a1.activity.jsonl"


def record(kind, **fields):
    if not enabled():
        return None
    rec = {"at": datetime.datetime.now().isoformat(timespec="seconds"), "kind": kind}
    rec.update(fields)
    p = _path()
    p.parent.mkdir(parents=True, exist_ok=True)
    line = json.dumps(rec, ensure_ascii=False) + "\n"
    with _LOCK:
        with open(p, "a", encoding="utf-8", newline="\n") as f:
            f.write(line)
    return rec


def record_chat(session_id, episode_id, channel="cli", facts=0, principles=0,
                model="", db_write=True):
    return record("user_activity", action="chat.turn", session=session_id,
                  episode_id=episode_id, channel=channel, db_write=bool(db_write),
                  facts=int(facts), principles=int(principles), model=str(model))
