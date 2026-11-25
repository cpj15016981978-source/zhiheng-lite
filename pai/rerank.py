# -*- coding: utf-8 -*-
"""Rerank 角色：优先 HTTP，缺省用词面重叠兜底。"""
from __future__ import annotations

import json
import os
import re
import urllib.request

CAPABILITY = {
    "name": "Rerank 角色",
    "switch_env": "PAI_RERANK_BASE_URL",
    "default_on": False,
    "stores_user_text": False,
    "network": True,
    "degrade": "未配 HTTP 时用词面重叠兜底；HTTP 失败时抛错，由 ModelRouter 决定是否降级",
    "approval": "ADR-044 本地模型/HTTP 接入登记",
}


def backend() -> str:
    return "http" if (os.environ.get("PAI_RERANK_BASE_URL") or "").strip() else "lexical"


def _terms(text):
    # 中文按 1–2 字滑窗，避免整句被当一个词
    raw = str(text).lower()
    words = set(re.findall(r"[a-z0-9_]{2,}", raw))
    han = re.findall(r"[\u4e00-\u9fff]+", raw)
    for chunk in han:
        words.update(chunk[i:i + 2] for i in range(max(0, len(chunk) - 1)))
        words.update(chunk)
    return words


def rerank(query, documents, top_k=None):
    docs = [str(d) for d in documents]
    if not docs:
        return {"backend": backend(), "results": []}
    base = (os.environ.get("PAI_RERANK_BASE_URL") or "").strip().rstrip("/")
    if base:
        model = (os.environ.get("PAI_RERANK_MODEL") or "bge-reranker-v2-m3").strip()
        key = (os.environ.get("PAI_RERANK_API_KEY") or os.environ.get("PAI_MODEL_API_KEY") or "").strip()
        body = json.dumps({"model": model, "query": str(query), "documents": docs},
                          ensure_ascii=False).encode("utf-8")
        headers = {"Content-Type": "application/json; charset=utf-8"}
        if key:
            headers["Authorization"] = "Bearer " + key
        req = urllib.request.Request(base + "/rerank", data=body, method="POST", headers=headers)
        with urllib.request.urlopen(req, timeout=120) as r:
            data = json.loads(r.read().decode("utf-8"))
        rows = data.get("results") or data.get("data") or []
        out = [{"index": int(x.get("index", i)), "score": float(x.get("relevance_score", x.get("score", 0)))}
               for i, x in enumerate(rows)]
        out.sort(key=lambda x: x["score"], reverse=True)
    else:
        q = _terms(query)
        out = []
        for i, d in enumerate(docs):
            t = _terms(d)
            score = len(q & t) / (len(t) + 1e-9)
            out.append({"index": i, "score": round(score, 6)})
        out.sort(key=lambda x: x["score"], reverse=True)
    if top_k:
        out = out[:top_k]
    return {"backend": backend(), "results": out}
