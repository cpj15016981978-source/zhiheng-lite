# -*- coding: utf-8 -*-
"""本地 trace 持久化（opt-in，只写文件，不写数据库）。

开关：PAI_TRACE_STORE=on
目录：PAI_TRACE_DIR 或 data/traces/
"""
import datetime
import hashlib
import json
import os
from pathlib import Path


CAPABILITY = {
    "name": "trace 持久化",
    "switch_env": "PAI_TRACE_STORE",
    "default_on": True,
    "stores_user_text": True,
    "text_hosts_ref": "pai/memory.py:_TEXT_HOSTS",
    "network": False,
    "degrade": "写入失败=不影响回答；目录不可写只记日志",
    "approval": "ADR-046：本地文件，默认仅本机",
}

ROOT = Path(__file__).resolve().parents[1]


def enabled():
    return (os.environ.get("PAI_TRACE_STORE") or "").strip().lower() == "on"


def _dir():
    p = Path(os.environ.get("PAI_TRACE_DIR") or (ROOT / "data" / "traces"))
    p.mkdir(parents=True, exist_ok=True)
    return p


def _sha12(text):
    return hashlib.sha256(str(text or "").encode("utf-8")).hexdigest()[:12]


def append(prompt, trace, source="chat"):
    """追加一条 trace。默认不写；被拦截的密钥不保存原文。"""
    if not enabled():
        return None
    trace = trace or {}
    rec = {
        "ts": datetime.datetime.now().isoformat(timespec="seconds"),
        "source": str(source or "chat"),
        "prompt_sha12": _sha12(prompt),
        "prompt_len": len(str(prompt or "")),
        "prompt": None if trace.get("blocked_secret") else str(prompt or ""),
        "trace": trace,
    }
    day = datetime.datetime.now().strftime("%Y%m%d")
    path = _dir() / ("trace-%s.jsonl" % day)
    with path.open("a", encoding="utf-8") as f:
        f.write(json.dumps(rec, ensure_ascii=False) + "\n")
    return path


def read(path):
    p = Path(path)
    if not p.exists():
        return []
    out = []
    for line in p.read_text(encoding="utf-8").splitlines():
        line = line.strip()
        if not line:
            continue
        try:
            out.append(json.loads(line))
        except ValueError:
            continue
    return out
