# -*- coding: utf-8 -*-
"""协调者执行轨迹采集：20 个只读任务，真跑 job_worker，收 job_events + verifier。

用法：
  python tools/coordinator_trace.py --out D:/DSH/coordinator_trace.json
"""
from __future__ import annotations

import argparse
import json
import os
import pathlib
import tempfile

ROOT = pathlib.Path(__file__).resolve().parent.parent
TMP = pathlib.Path(tempfile.mkdtemp(prefix="zhiheng-trace-"))
os.environ["PAI_DATA_DIR"] = str(TMP)
os.environ["PAI_JOB_RUNNER"] = "on"
os.environ["PAI_JOB_WORKER"] = "on"
os.environ["PAI_OFFLINE"] = "1"
(TMP / ".migrated").write_text("new", encoding="ascii")

import sys
sys.path.insert(0, str(ROOT))
from pai import job_runner as JR
from pai import unified as U
from pai import paths as PATHS

JR.DB_PATH = TMP / "jobs.db"
PATHS.WORKSPACE = TMP / "workspace"
PATHS.WORKSPACE.mkdir(parents=True, exist_ok=True)

FILES = {
    "a.txt": "alpha\n",
    "b.txt": "beta\n",
    "notes.md": "# notes\n- M-9\n- token\n",
    "report.md": "# report\n本周完成审批。\n",
    "README.md": "# 知衡\n本地优先。\n",
    "meeting.md": "# 会议\n先做验收。\n",
    "invoice.md": "金额 1200\n",
    "data.csv": "a,1\nb,2\n",
    "docs/plan.md": "# plan\n1. token\n2. trace\n",
    "inbox/mail.md": "# mail\n交期。\n",
    "inbox/todo.md": "# todo\n回信。\n",
}
for rel, text in FILES.items():
    p = PATHS.WORKSPACE / rel
    p.parent.mkdir(parents=True, exist_ok=True)
    p.write_text(text, encoding="utf-8")


def read(rel, **kw):
    d = {"text": "读 " + rel, "action": "read_text", "path": rel,
         "verify": {"file_exists": rel}}
    d.update(kw)
    return d


def ls(rel="."):
    return {"text": "列目录 " + rel, "action": "list_dir", "path": rel}


TASKS = [
    ("t01", "读 a", [read("a.txt")]),
    ("t02", "列根目录", [ls(".")]),
    ("t03", "读 b", [read("b.txt")]),
    ("t04", "读 docs/plan", [read("docs/plan.md")]),
    ("t05", "列 docs", [ls("docs")]),
    ("t06", "读 inbox/mail", [read("inbox/mail.md")]),
    ("t07", "读 a 和 b", [read("a.txt"), read("b.txt")]),
    ("t08", "列 inbox", [ls("inbox")]),
    ("t09", "读 notes", [read("notes.md")]),
    ("t10", "读 README", [read("README.md")]),
    ("t11", "读 report", [read("report.md")]),
    ("t12", "读 meeting", [read("meeting.md")]),
    ("t13", "读 invoice", [read("invoice.md")]),
    ("t14", "读 data.csv", [read("data.csv")]),
    ("t15", "列 docs + 读 plan", [ls("docs"), read("docs/plan.md")]),
    ("t16", "读 inbox 两封", [read("inbox/mail.md"), read("inbox/todo.md")]),
    ("t17", "读 a/b/notes", [read("a.txt"), read("b.txt"), read("notes.md")]),
    ("t18", "列根 + 读 README", [ls("."), read("README.md")]),
    ("t19", "读三份记录", [read("records.txt") if (PATHS.WORKSPACE / "records.txt").exists() else read("notes.md"),
                           read("report.md"), read("meeting.md")]),
    ("t20", "列 docs + 读 plan + notes", [ls("docs"), read("docs/plan.md"), read("notes.md")]),
]


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--out", default="D:/DSH/coordinator_trace.json")
    ap.add_argument("--limit", type=int, default=0)
    ns = ap.parse_args()
    tasks = TASKS[:ns.limit] if ns.limit else TASKS
    rows = []
    for tid, goal, steps in tasks:
        plan = U.coordinator_start(goal, steps)
        pid = plan["id"]
        run = U.coordinator_step(pid, parallel=True)
        tr = JR.trajectory(pid)
        verified = 0
        events = 0
        for s in (tr.get("steps") or []):
            events += len(s.get("events") or [])
            for e in (s.get("events") or []):
                if e.get("type") == "verified" and (e.get("payload") or {}).get("ok"):
                    verified += 1
        rows.append({
            "id": tid, "goal": goal, "plan": pid,
            "jobs": len(run.get("results") or []),
            "parallel": run.get("parallel"),
            "steps": tr.get("steps") or [],
            "events": events,
            "verified": verified,
            "ok": all(s.get("status") == "done" for s in (tr.get("steps") or [])),
        })
        print("%s jobs=%s parallel=%s events=%s verified=%s ok=%s" % (
            tid, len(run.get("results") or []), run.get("parallel"), events, verified,
            rows[-1]["ok"]), flush=True)
    total_steps = sum(len(r["steps"]) for r in rows)
    total_events = sum(r["events"] for r in rows)
    summary = {"tasks": len(rows), "steps": total_steps, "events": total_events,
               "verified": sum(r["verified"] for r in rows),
               "ok": sum(1 for r in rows if r["ok"]), "rows": rows}
    print("TOTAL tasks=%s ok=%s steps=%s events=%s verified=%s" % (
        summary["tasks"], summary["ok"], total_steps, total_events, summary["verified"]))
    p = pathlib.Path(ns.out); p.parent.mkdir(parents=True, exist_ok=True)
    p.write_text(json.dumps(summary, ensure_ascii=False, indent=2), encoding="utf-8")
    print("saved", p)


if __name__ == "__main__":
    main()
