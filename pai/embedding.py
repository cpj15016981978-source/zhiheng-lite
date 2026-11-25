# -*- coding: utf-8 -*-
"""Embedding 角色：优先 HTTP，缺省用确定性本地向量兜底。

不假装兜底向量是语义模型：backend 字段会明确标 local-hash。
"""
from __future__ import annotations

import hashlib
import json
import math
import os
import urllib.request

DIM = 256

CAPABILITY = {
    "name": "Embedding 角色",
    "switch_env": "PAI_EMBED_BASE_URL",
    "default_on": False,
    "stores_user_text": False,
    "network": True,
    "degrade": "未配 HTTP 时用 local-hash 兜底；HTTP 失败时抛错，由 ModelRouter 决定是否降级",
    "approval": "ADR-044 本地模型/HTTP 接入登记",
}


def backend() -> str:
    return "http" if (os.environ.get("PAI_EMBED_BASE_URL") or "").strip() else "local-hash"


def _hash_embed(text: str, dim: int = DIM):
    vec = [0.0] * dim
    text = str(text or "")
    grams = [text[i:i + 2] for i in range(max(0, len(text) - 1))] or [text]
    for g in grams:
        h = hashlib.sha256(g.encode("utf-8")).digest()
        idx = int.from_bytes(h[:4], "big") % dim
        sign = 1.0 if h[4] & 1 else -1.0
        vec[idx] += sign
    norm = math.sqrt(sum(v * v for v in vec)) or 1.0
    return [v / norm for v in vec]


def embed(texts):
    if isinstance(texts, str):
        texts = [texts]
    texts = [str(t) for t in texts]
    base = (os.environ.get("PAI_EMBED_BASE_URL") or "").strip().rstrip("/")
    if not base:
        return {"backend": "local-hash", "vectors": [_hash_embed(t) for t in texts]}
    model = (os.environ.get("PAI_EMBED_MODEL") or "bge-m3").strip()
    key = (os.environ.get("PAI_EMBED_API_KEY") or os.environ.get("PAI_MODEL_API_KEY") or "").strip()
    body = json.dumps({"model": model, "input": texts}, ensure_ascii=False).encode("utf-8")
    headers = {"Content-Type": "application/json; charset=utf-8"}
    if key:
        headers["Authorization"] = "Bearer " + key
    req = urllib.request.Request(base + "/embeddings", data=body, method="POST", headers=headers)
    with urllib.request.urlopen(req, timeout=120) as r:
        data = json.loads(r.read().decode("utf-8"))
    vectors = [row["embedding"] for row in data.get("data", [])]
    if len(vectors) != len(texts):
        raise RuntimeError("embedding 返回数量不匹配")
    return {"backend": "http", "model": model, "vectors": vectors}
