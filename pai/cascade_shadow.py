# -*- coding: utf-8 -*-
"""级联 shadow：只旁路观察，不改变线上答案。

默认 cloud_only；只有 PAI_CASCADE_MODE=shadow 才在云端答完后异步跑一次本地，
记录一致率，供以后决定是否晋升 cascade。本模块不做路由。
"""
from __future__ import annotations

import datetime
import json
import os
import pathlib
import re
import threading

ROOT = pathlib.Path(__file__).resolve().parent.parent
LOG = pathlib.Path(os.environ.get("PAI_CASCADE_SHADOW_LOG") or (ROOT / "data" / "cascade_shadow.jsonl"))

CAPABILITY = {
    "name": "级联 shadow 观察",
    "switch_env": "PAI_CASCADE_MODE",
    "default_on": False,
    "stores_user_text": False,
    "network": False,
    "degrade": "非 shadow=完全不跑；本地失败=只记日志，不影响云端答案",
    "approval": "ADR-046：默认 cloud_only；shadow 只观察不改答案",
}


def enabled():
    return (os.environ.get("PAI_CASCADE_MODE") or "cloud_only").strip().lower() == "shadow"


def _norm(text):
    return re.sub(r"[\s，,。.、；;：:!！?？\"'`]+", "", str(text or ""))[:400]


def _append(row):
    try:
        LOG.parent.mkdir(parents=True, exist_ok=True)
        row = dict(row)
        row.setdefault("at", datetime.datetime.now().astimezone().isoformat(timespec="seconds"))
        with LOG.open("a", encoding="utf-8") as f:
            f.write(json.dumps(row, ensure_ascii=False) + "\n")
    except Exception:
        pass


def observe_async(prompt, cloud_answer, local_fn):
    """异步旁路；返回线程，测试可 join。"""
    if not enabled():
        return None

    def run():
        try:
            local_answer = local_fn(prompt)
            a, b = _norm(local_answer), _norm(cloud_answer)
            _append({"event": "shadow", "ok": True,
                     "agree": bool(a) and a == b,
                     "len_local": len(a), "len_cloud": len(b)})
        except Exception as e:
            _append({"event": "shadow", "ok": False,
                     "error": "%s: %s" % (type(e).__name__, e)})

    t = threading.Thread(target=run, daemon=True, name="cascade-shadow")
    t.start()
    return t
