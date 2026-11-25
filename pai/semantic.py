# -*- coding: utf-8 -*-
"""语义连续性：用本机 Ollama 从一轮对话里提取“未完成的线”。"""
from __future__ import annotations

import json
import os
import threading
import time
import urllib.request

from pai import continuity as CT

CAPABILITY = {
    "name": "语义连续性 v1",
    "switch_env": "NONE",
    "default_on": True,
    "stores_user_text": False,
    "network": True,
    "degrade": "模型不可用/JSON 坏=跳过；不阻塞对话；只连 127.0.0.1:11434",
    "approval": "ADR-046：本地单用户语义总结，后台执行，不自动外发",
}

_last = 0.0
_lock = threading.Lock()


def _model():
    return (os.environ.get("PAI_LOCAL_MODEL") or os.environ.get("PAI_MODEL_NAME")
            or "qwen3-4b-instruct:latest").strip()


def _ask(text, response):
    prompt = """你是个人助理的连续性提取器。只输出一个 JSON，不要解释。
判断这轮对话里有没有“未完成、需要下次继续”的线。
有就输出：
{"has_thread":true,"title":"不超过40字","last_step":"上次停在哪","next_step":"下一步做什么","evidence":"依据"}
没有就输出：
{"has_thread":false}
用户：%s
助手：%s""" % (text[:2000], response[:3000])
    body = json.dumps({"model": _model(), "messages": [{"role": "user", "content": prompt}],
                       "stream": False, "options": {"temperature": 0.1, "num_predict": 300}},
                      ensure_ascii=False).encode("utf-8")
    req = urllib.request.Request("http://127.0.0.1:11434/api/chat", data=body, method="POST",
                                 headers={"Content-Type": "application/json; charset=utf-8"})
    with urllib.request.urlopen(req, timeout=45) as r:
        data = json.loads(r.read().decode("utf-8"))
    return str((data.get("message") or {}).get("content") or "").strip()


def _parse(raw):
    raw = raw.strip()
    if "```" in raw:
        raw = raw.split("```")[-2] if raw.count("```") >= 2 else raw.replace("```", "")
    a, b = raw.find("{"), raw.rfind("}")
    if a < 0 or b <= a:
        return None
    try:
        d = json.loads(raw[a:b + 1])
    except ValueError:
        return None
    if not d.get("has_thread"):
        return None
    title = str(d.get("title") or "").strip()
    nxt = str(d.get("next_step") or "").strip()
    if not title or not nxt:
        return None
    return {"title": title[:200], "last_step": str(d.get("last_step") or "")[:2000],
            "next_step": nxt[:2000], "evidence": str(d.get("evidence") or "语义总结")[:2000]}


def auto_semantic(text, response, profile="work"):
    """后台调用；返回线程对象或 None。"""
    global _last
    with _lock:
        now = time.time()
        if now - _last < 45:
            return None
        _last = now

    def run():
        try:
            item = _parse(_ask(text, response))
            if item:
                CT.save(profile=profile, thread_id="semantic-%s" % profile, **item)
        except Exception:
            pass

    threading.Thread(target=run, daemon=True).start()
    return None
