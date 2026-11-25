# -*- coding: utf-8 -*-
"""模型调用台账：provider / model / latency / tokens / cost。"""
from __future__ import annotations

import datetime
import json
import pathlib
import threading


CAPABILITY = {
    "name": "模型调用台账",
    "switch_env": "PAI_MODEL_USAGE",
    "default_on": True,
    "stores_user_text": False,
    "network": False,
    "degrade": "台账写失败=不影响生成；只记元数据，不记正文",
    "approval": "ADR-046：本地只读统计，不新增开关",
}

ROOT = pathlib.Path(__file__).resolve().parent.parent
PATH = ROOT / "data" / "model_usage.jsonl"
_LOCK = threading.Lock()


def record(outs, fallback=False):
    if not isinstance(outs, list):
        return
    now = datetime.datetime.now().astimezone().isoformat(timespec="seconds")
    with _LOCK:
        PATH.parent.mkdir(parents=True, exist_ok=True)
        with PATH.open("a", encoding="utf-8") as f:
            for o in outs:
                if not isinstance(o, dict):
                    continue
                row = {"at": now, "fallback": bool(fallback),
                       "provider": str(o.get("model_id") or "").split(":")[0],
                       "model": str(o.get("model") or ""),
                       "latency": o.get("latency"),
                       "tokens": o.get("tokens"),
                       "cost": o.get("cost"),
                       "priced": o.get("priced")}
                f.write(json.dumps(row, ensure_ascii=False) + "\n")


def summary(limit=200):
    if not PATH.exists():
        return {"count": 0, "tokens": 0, "cost": 0.0, "avg_latency": 0.0, "recent": []}
    rows = []
    for line in PATH.read_text(encoding="utf-8", errors="replace").splitlines()[-limit:]:
        try:
            rows.append(json.loads(line))
        except Exception:
            pass
    def _num(v):
        try:
            return float(v)
        except (TypeError, ValueError):
            return 0.0
    rows = [x for x in rows if isinstance(x, dict)]
    tokens = int(sum(_num(x.get("tokens")) for x in rows))
    cost = sum(_num(x.get("cost")) for x in rows)
    lat = [_num(x.get("latency")) for x in rows if x.get("latency") and _num(x.get("latency")) > 0]
    return {"count": len(rows), "tokens": tokens, "cost": round(cost, 6),
            "avg_latency": round(sum(lat) / len(lat), 3) if lat else 0.0,
            "recent": rows[-20:][::-1]}
