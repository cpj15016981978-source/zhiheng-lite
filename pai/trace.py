# -*- coding: utf-8 -*-
"""I1 最小事件流：把每次模型请求/回应写成追加式 JSONL。

登记默认关；webapp/chat 入口显式设 PAI_TRACE=on。
正文落本地，不重复写第二套事实库。
"""
import datetime
import hashlib
import json
import os
import threading
import uuid
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
DEFAULT_DIR = ROOT / "data" / "trace"
_LOCK = threading.Lock()

CAPABILITY = {
    "name": "I1 模型请求事件流",
    "switch_env": "PAI_TRACE",
    "default_on": False,
    "stores_user_text": True,
    "text_hosts_ref": "pai/memory.py:_TEXT_HOSTS",
    "network": False,
    "degrade": "PAI_TRACE=off 时不写事件，对话照常；事件写失败不打断回答",
    "probe_cost": "只读 PAI_TRACE / PAI_TRACE_DIR 与本地 data/trace 路径；不联网、不调模型、不写记忆库",
    "approval": "本地落 prompt/assistant 正文，用于重放；登记默认关，入口显式开，PAI_TRACE=off 可关",
}


def enabled():
    return (os.environ.get("PAI_TRACE") or "off").strip().lower() == "on"


def _now():
    return datetime.datetime.now().isoformat(timespec="seconds")


def _dir():
    return Path(os.environ.get("PAI_TRACE_DIR") or DEFAULT_DIR)


class Trace:
    def __init__(self, path=None):
        self.path = Path(path) if path else None
        self._last_prompt_sha = None

    def _file(self):
        if self.path:
            return self.path
        return _dir() / ("trace-%s.jsonl" % datetime.date.today().isoformat())

    def append(self, kind, data):
        if not enabled():
            return None
        rec = {"at": _now(), "kind": kind}
        rec.update(data or {})
        p = self._file()
        p.parent.mkdir(parents=True, exist_ok=True)
        line = json.dumps(rec, ensure_ascii=False) + "\n"
        with _LOCK:
            with open(p, "a", encoding="utf-8", newline="\n") as f:
                f.write(line)
        return rec

    def record_prompt_change(self, prompt, source="orchestrator"):
        sha = hashlib.sha256(prompt.encode("utf-8")).hexdigest()[:12]
        old = self._last_prompt_sha
        self._last_prompt_sha = sha
        if old is not None and old != sha:
            self.append("prompt.changed", {"source": source, "old_sha12": old,
                                            "new_sha12": sha, "chars": len(prompt)})
        return sha

    def record_tool_call(self, tool, args, result=None, error=None):
        if not enabled():
            return None
        cid = uuid.uuid4().hex[:12]
        self.append("tool.call", {"call_id": cid, "tool": tool, "args": args})
        self.append("tool.result", {"call_id": cid, "tool": tool,
                                     "ok": error is None,
                                     "result": result, "error": error})
        return cid

    def record_approval(self, decision, subject="", by="", task_id="", reason=""):
        return self.append("approval.decision", {"decision": decision, "subject": subject,
                                                  "by": by, "task_id": task_id,
                                                  "reason": reason})

    def record_model_call(self, prompt, outputs, source="orchestrator"):
        if not enabled():
            return None
        self.record_prompt_change(prompt, source)
        rid = uuid.uuid4().hex[:12]
        self.append("model.request", {
            "request_id": rid,
            "source": source,
            "prompt": prompt,
            "prompt_sha12": hashlib.sha256(prompt.encode("utf-8")).hexdigest()[:12],
        })
        for o in outputs or ():
            self.append("model.response", {
                "request_id": rid,
                "model_id": o.get("model_id"),
                "model": o.get("model"),
                "output": o.get("output"),
                "cost": o.get("cost"),
                "tokens": o.get("tokens"),
                "priced": o.get("priced"),
            })
        return rid

    def record_error(self, prompt, error, source="orchestrator"):
        return self.append("model.error", {
            "source": source,
            "prompt": prompt,
            "prompt_sha12": hashlib.sha256(prompt.encode("utf-8")).hexdigest()[:12],
            "error": type(error).__name__,
            "message": str(error)[:300],
        })


TRACE = Trace()
