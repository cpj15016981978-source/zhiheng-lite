# -*- coding: utf-8 -*-
"""ROI 埋点：每个作业估时、实际耗时、成功/失败、节省分钟。"""
from __future__ import annotations

import datetime
import pathlib
import sqlite3
import threading

ROOT = pathlib.Path(__file__).resolve().parent.parent
DB = ROOT / "data" / "roi.db"
_LOCK = threading.RLock()

CAPABILITY = {
    "name": "ROI 埋点",
    "switch_env": "NONE",
    "default_on": True,
    "stores_user_text": False,
    "network": False,
    "degrade": "库坏了=明确报错；缺估时=只记实际耗时，不编节省",
    "approval": "ADR-046：本地既成常开统计表，不新增开关",
}


def _now():
    return datetime.datetime.now().astimezone().isoformat(timespec="seconds")


def _conn():
    DB.parent.mkdir(parents=True, exist_ok=True)
    c = sqlite3.connect(str(DB), timeout=10)
    c.row_factory = sqlite3.Row
    c.execute("""CREATE TABLE IF NOT EXISTS roi(
        id INTEGER PRIMARY KEY AUTOINCREMENT,
        job_id TEXT NOT NULL DEFAULT '',
        workspace_id TEXT NOT NULL DEFAULT '',
        agent_id TEXT NOT NULL DEFAULT '',
        estimated_minutes REAL NOT NULL DEFAULT 0,
        actual_seconds REAL NOT NULL DEFAULT 0,
        success INTEGER NOT NULL DEFAULT 0,
        note TEXT NOT NULL DEFAULT '',
        created_at TEXT NOT NULL)""")
    c.execute("CREATE INDEX IF NOT EXISTS idx_roi_ws ON roi(workspace_id, created_at)")
    return c


def record(job_id="", workspace_id="", agent_id="", estimated_minutes=0,
           actual_seconds=0, success=False, note=""):
    with _LOCK, _conn() as c:
        c.execute("INSERT INTO roi(job_id,workspace_id,agent_id,estimated_minutes,actual_seconds,success,note,created_at)"
                  " VALUES(?,?,?,?,?,?,?,?)",
                  (str(job_id or ""), str(workspace_id or ""), str(agent_id or ""),
                   max(0.0, float(estimated_minutes or 0)), max(0.0, float(actual_seconds or 0)),
                   1 if success else 0, str(note or "")[:500], _now()))
    return True


def rows(limit=50, workspace_id=""):
    with _LOCK, _conn() as c:
        if workspace_id:
            rs = c.execute("SELECT * FROM roi WHERE workspace_id=? ORDER BY created_at DESC LIMIT ?",
                           (str(workspace_id), int(limit))).fetchall()
        else:
            rs = c.execute("SELECT * FROM roi ORDER BY created_at DESC LIMIT ?", (int(limit),)).fetchall()
    return [dict(r) for r in rs]


def dashboard(limit=50):
    out = summary()
    with _LOCK, _conn() as c:
        rs = c.execute("SELECT workspace_id, COUNT(*) n, SUM(success) ok, "
                       "SUM(estimated_minutes) est, SUM(actual_seconds) sec "
                       "FROM roi GROUP BY workspace_id ORDER BY n DESC").fetchall()
    out["by_workspace"] = [dict(r) for r in rs]
    out["recent"] = rows(limit)
    return out


def summary(workspace_id=""):
    with _LOCK, _conn() as c:
        if workspace_id:
            rows = c.execute("SELECT * FROM roi WHERE workspace_id=? ORDER BY created_at DESC",
                             (str(workspace_id),)).fetchall()
        else:
            rows = c.execute("SELECT * FROM roi ORDER BY created_at DESC").fetchall()
    saved = 0.0
    for r in rows:
        if r["estimated_minutes"] and r["success"]:
            saved += max(0.0, float(r["estimated_minutes"]) - float(r["actual_seconds"]) / 60.0)
    return {"count": len(rows),
            "success": sum(1 for r in rows if r["success"]),
            "estimated_minutes": round(sum(float(r["estimated_minutes"] or 0) for r in rows), 1),
            "actual_minutes": round(sum(float(r["actual_seconds"] or 0) for r in rows) / 60.0, 1),
            "saved_minutes": round(saved, 1)}
