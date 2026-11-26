# -*- coding: utf-8 -*-
"""本地模型兜底（Ollama）· 云端不通时让月白还能说话。

只做文本补全；不写记忆、不执行工具、不出网（只连 127.0.0.1:11434）。
开关：PAI_LOCAL_FALLBACK=on
模型：PAI_LOCAL_MODEL，默认 qwen3-4b-instruct:latest
"""
from __future__ import annotations

import json
import os
import urllib.request

CAPABILITY = {
    "name": "本地模型兜底",
    "switch_env": "PAI_LOCAL_FALLBACK",
    "default_on": False,
    "stores_user_text": False,
    "network": True,
    "degrade": "关态不启用；开态云端失败时尝试本地 Ollama，失败仍返回原错误",
    "probe_cost": "本地推理，无云端费用",
    "approval": "ADR-044 既有本地兜底",
}


def enabled() -> bool:
    return (os.environ.get("PAI_LOCAL_FALLBACK") or "").strip().lower() == "on"


def model_name() -> str:
    return (os.environ.get("PAI_LOCAL_MODEL") or "qwen3-4b-instruct:latest").strip()


def complete(prompt: str, timeout: int = 180, on_delta=None) -> str:
    if not enabled():
        raise RuntimeError("PAI_LOCAL_FALLBACK 未开")
    body = json.dumps({
        "model": model_name(),
        "messages": [{"role": "user", "content": str(prompt)}],
        "stream": bool(on_delta),
        "options": {"temperature": 0.3, "num_predict": 900},
    }, ensure_ascii=False).encode("utf-8")
    req = urllib.request.Request(
        "http://127.0.0.1:11434/api/chat", data=body, method="POST",
        headers={"Content-Type": "application/json; charset=utf-8"})
    if on_delta is None:
        with urllib.request.urlopen(req, timeout=timeout) as r:
            data = json.loads(r.read().decode("utf-8"))
        return str((data.get("message") or {}).get("content") or "").strip()
    chunks = []
    with urllib.request.urlopen(req, timeout=timeout) as r:
        for raw in r:
            line = raw.decode("utf-8", errors="replace").strip()
            if not line:
                continue
            try:
                data = json.loads(line)
            except Exception:
                continue
            piece = str((data.get("message") or {}).get("content") or "")
            if piece:
                chunks.append(piece)
                try:
                    on_delta(piece)
                except Exception:
                    pass
            if data.get("done"):
                break
    return "".join(chunks).strip()
