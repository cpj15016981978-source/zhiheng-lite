# -*- coding: utf-8 -*-
"""作业健康：消费 expired/failed，不再让过期作业静默消失。"""
from __future__ import annotations

import sqlite3

from pai import job_runner as JR

CAPABILITY = {
    "name": "作业健康",
    "switch_env": "PAI_JOB_HEALTH",
    "default_on": True,
    "stores_user_text": False,
    "network": False,
    "degrade": "库缺失=返回 0；读取失败=明确报错，不伪造",
    "approval": "ADR-046：只读作业状态，不新增开关",
}


def summary():
    p = JR.DB_PATH
    out = {"expired": 0, "failed": 0, "pending": 0, "waiting": 0, "items": []}
    if not p.exists():
        return out
    c = sqlite3.connect(str(p), timeout=10)
    try:
        for st in ("expired", "failed", "pending", "waiting"):
            out[st] = c.execute("SELECT COUNT(*) FROM jobs WHERE status=?", (st,)).fetchone()[0]
        rows = c.execute("SELECT id,title,status,error,updated_at FROM jobs "
                         "WHERE status IN ('expired','failed') ORDER BY updated_at DESC LIMIT 10").fetchall()
        out["items"] = [{"id": r[0], "title": r[1], "status": r[2],
                         "error": r[3], "updated_at": r[4]} for r in rows]
    finally:
        c.close()
    return out


def text():
    s = summary()
    if not s["expired"] and not s["failed"]:
        return ""
    lines = ["作业异常：过期 %d，失败 %d" % (s["expired"], s["failed"])]
    for x in s["items"][:3]:
        lines.append("- [%s] %s" % (x["status"], x["title"]))
    return "\n".join(lines)
