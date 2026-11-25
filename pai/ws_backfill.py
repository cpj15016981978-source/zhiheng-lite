# -*- coding: utf-8 -*-
"""把旧作业补挂到 Workspace：只处理 workspace_id 为空的历史作业。"""
from __future__ import annotations

from contextlib import closing

from pai import job_runner as JR
from pai import workspaces as WS

CAPABILITY = {
    "name": "旧作业补挂",
    "switch_env": "PAI_WS_BACKFILL",
    "default_on": False,
    "stores_user_text": False,
    "network": False,
    "degrade": "没有旧作业=返回 0；写失败=报错，不假装补挂",
    "approval": "ADR-046：显式运行一次，不自动执行",
}


def backfill(workspace_id="", title="历史作业", limit=0):
    wid = str(workspace_id or "").strip()
    if not wid:
        for w in WS.list_workspaces():
            if w["title"] == title and w["status"] != "archived":
                wid = w["id"]
                break
        if not wid:
            wid = WS.create(title)["id"]
    if not WS.get(wid):
        raise ValueError("workspace 不存在：%s" % wid)
    n = 0
    added = 0
    with closing(JR._conn()) as c, c:
        sql = "SELECT * FROM jobs WHERE workspace_id='' ORDER BY created_at"
        if limit:
            sql += " LIMIT %d" % max(1, int(limit))
        rows = c.execute(sql).fetchall()
        for row in rows:
            job = JR._row(row)
            c.execute("UPDATE jobs SET workspace_id=?, updated_at=? WHERE id=?",
                      (wid, JR._now(), job["id"]))
            JR._add(c, job["id"], "workspace_linked", {"workspace_id": wid})
            n += 1
            WS.add_item(wid, "job", ref_id=job["id"],
                        title="历史作业：" + str(job.get("title") or ""))
            added += 1
            if job.get("approved"):
                WS.add_item(wid, "approval", ref_id=job["id"],
                            title="已批准：" + str(job.get("title") or ""))
                added += 1
            if job.get("result") or job.get("error"):
                WS.add_item(wid, "result", ref_id=job["id"],
                            title=("完成：" if job.get("result") else "失败：") + str(job.get("title") or ""),
                            body=str(job.get("result") or job.get("error") or "")[:1000])
                added += 1
    return {"ok": True, "workspace_id": wid, "jobs": n, "items": added}
