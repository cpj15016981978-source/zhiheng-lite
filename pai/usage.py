# -*- coding: utf-8 -*-
"""模型用量/花费统计：只读 trace JSONL，不联网、不调模型。"""
from __future__ import annotations

import datetime
import json
import os
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
DEFAULT_DIR = ROOT / "data" / "trace"

CAPABILITY = {
    "name": "模型用量统计",
    "switch_env": "NONE",
    "default_on": True,
    "stores_user_text": False,
    "network": False,
    "degrade": "trace 文件不存在/行损坏=跳过，返回 0；只读本地 JSONL，不调模型、不出网",
    "approval": "ADR-046：没有开关的既成常开登记，不发明新开关",
}


def _dir():
    return Path(os.environ.get("PAI_TRACE_DIR") or DEFAULT_DIR)


def today():
    path = _dir() / ("trace-%s.jsonl" % datetime.date.today().isoformat())
    out = {"date": datetime.date.today().isoformat(), "requests": 0,
           "responses": 0, "cost": 0.0, "tokens": 0, "unpriced": 0,
           "by_model": [], "file": str(path)}
    if not path.exists():
        return out
    by = {}
    with path.open("r", encoding="utf-8", errors="replace") as f:
        for line in f:
            try:
                rec = json.loads(line)
            except Exception:
                continue
            if rec.get("kind") == "model.request":
                out["requests"] += 1
            if rec.get("kind") != "model.response":
                continue
            out["responses"] += 1
            model = str(rec.get("model_id") or rec.get("model") or "unknown")
            item = by.setdefault(model, {"model_id": model, "calls": 0,
                                         "cost": 0.0, "tokens": 0})
            item["calls"] += 1
            try:
                item["cost"] += float(rec.get("cost") or 0)
                out["cost"] += float(rec.get("cost") or 0)
            except (TypeError, ValueError):
                pass
            try:
                item["tokens"] += int(rec.get("tokens") or 0)
                out["tokens"] += int(rec.get("tokens") or 0)
            except (TypeError, ValueError):
                pass
            if rec.get("priced") is False:
                out["unpriced"] += 1
    out["by_model"] = sorted(by.values(), key=lambda x: x["calls"], reverse=True)
    return out
