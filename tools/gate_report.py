# -*- coding: utf-8 -*-
"""门禁产物：JSON 报告 + 跳过留痕（带 commit sha）。"""
from __future__ import annotations

import datetime
import json
import pathlib
import subprocess
import time

ROOT = pathlib.Path(__file__).resolve().parents[1]
REPORTS = ROOT / "reports"


def _sha():
    try:
        return subprocess.run(["git", "rev-parse", "--short", "HEAD"],
                              cwd=str(ROOT), capture_output=True, text=True, encoding="utf-8", errors="replace").stdout.strip() or "unknown"
    except Exception:
        return "unknown"


def _write(name, payload):
    REPORTS.mkdir(exist_ok=True)
    (REPORTS / name).write_text(json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8")


def write_report(tier, ok, spent, budget, steps):
    ts = datetime.datetime.now().strftime("%Y%m%d-%H%M%S")
    _write("%s-%s-%s.json" % (ts, tier, _sha()),
           {"tier": tier, "ok": ok, "secs": spent, "budget_s": budget,
            "commit": _sha(), "at": ts, "steps": steps})


def record_skip(tier, reason):
    p = REPORTS / "skips.jsonl"
    p.parent.mkdir(exist_ok=True)
    with p.open("a", encoding="utf-8") as f:
        f.write(json.dumps({"at": time.strftime("%Y-%m-%dT%H:%M:%S"), "tier": tier,
                            "commit": _sha(), "reason": reason}, ensure_ascii=False) + "\n")
