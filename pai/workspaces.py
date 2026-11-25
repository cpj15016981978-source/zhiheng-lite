# -*- coding: utf-8 -*-
"""Workspace 骨架：一个任务一个空间，存任务/文件/证据/审批/结果（与 B1 workspace 区分）。"""
from __future__ import annotations

import datetime
import pathlib
import sqlite3
import threading
import uuid

ROOT = pathlib.Path(__file__).resolve().parent.parent
DB = ROOT / "data" / "workspaces.db"
_LOCK = threading.RLock()

CAPABILITY = {
    "name": "Workspace 任务空间",
    "switch_env": "NONE",
    "default_on": True,
    "stores_user_text": False,
    "network": False,
    "degrade": "数据库损坏=明确报错；不伪造空空间",
    "approval": "ADR-046：本地既成常开治理表，不新增开关",
}

ITEM_TYPES = ("job", "file", "evidence", "approval", "result", "note")


def _now():
    return datetime.datetime.now().astimezone().isoformat(timespec="seconds")


def _conn():
    DB.parent.mkdir(parents=True, exist_ok=True)
    c = sqlite3.connect(str(DB), timeout=10)
    c.row_factory = sqlite3.Row
    c.execute("""CREATE TABLE IF NOT EXISTS workspaces(
        id TEXT PRIMARY KEY, title TEXT NOT NULL, status TEXT NOT NULL DEFAULT 'open',
        owner TEXT NOT NULL DEFAULT 'Boss', created_at TEXT NOT NULL, updated_at TEXT NOT NULL)""")
    c.execute("""CREATE TABLE IF NOT EXISTS workspace_items(
        id TEXT PRIMARY KEY, workspace_id TEXT NOT NULL, type TEXT NOT NULL,
        ref_id TEXT NOT NULL DEFAULT '', title TEXT NOT NULL DEFAULT '',
        body TEXT NOT NULL DEFAULT '', created_at TEXT NOT NULL)""")
    c.execute("CREATE INDEX IF NOT EXISTS idx_ws_items ON workspace_items(workspace_id, created_at)")
    return c


def create(title, owner="Boss"):
    title = str(title or "").strip()
    if not title:
        raise ValueError("title 不能为空")
    wid = "w_" + uuid.uuid4().hex[:10]
    now = _now()
    with _LOCK, _conn() as c:
        c.execute("INSERT INTO workspaces(id,title,status,owner,created_at,updated_at) VALUES(?,?,?,?,?,?)",
                  (wid, title[:120], "open", str(owner or "Boss")[:80], now, now))
    return get(wid)


def list_workspaces(status=None):
    with _LOCK, _conn() as c:
        if status:
            rows = c.execute("SELECT * FROM workspaces WHERE status=? ORDER BY updated_at DESC", (status,)).fetchall()
        else:
            rows = c.execute("SELECT * FROM workspaces ORDER BY updated_at DESC").fetchall()
    return [dict(r) for r in rows]


def get(wid):
    with _LOCK, _conn() as c:
        w = c.execute("SELECT * FROM workspaces WHERE id=?", (str(wid),)).fetchone()
        if not w:
            return None
        items = c.execute("SELECT * FROM workspace_items WHERE workspace_id=? ORDER BY created_at ASC",
                          (str(wid),)).fetchall()
    out = dict(w)
    out["items"] = [dict(x) for x in items]
    return out


def add_item(wid, type, ref_id="", title="", body=""):
    if not get(wid):
        raise ValueError("workspace 不存在：%s" % wid)
    typ = str(type or "note")
    if typ not in ITEM_TYPES:
        raise ValueError("未知 item type：%s" % typ)
    iid = "i_" + uuid.uuid4().hex[:10]
    now = _now()
    with _LOCK, _conn() as c:
        c.execute("INSERT INTO workspace_items(id,workspace_id,type,ref_id,title,body,created_at) VALUES(?,?,?,?,?,?,?)",
                  (iid, str(wid), typ, str(ref_id or "")[:80], str(title or "")[:200],
                   str(body or "")[:10000], now))
        c.execute("UPDATE workspaces SET updated_at=? WHERE id=?", (now, str(wid)))
    return {"id": iid, "workspace_id": str(wid), "type": typ}


def set_status(wid, status):
    status = str(status or "")
    if status not in ("open", "waiting", "done", "archived"):
        raise ValueError("未知状态：%s" % status)
    with _LOCK, _conn() as c:
        cur = c.execute("UPDATE workspaces SET status=?, updated_at=? WHERE id=?",
                        (status, _now(), str(wid)))
        if cur.rowcount == 0:
            raise ValueError("workspace 不存在：%s" % wid)
    return get(wid)
