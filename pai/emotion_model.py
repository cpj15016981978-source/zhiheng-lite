# -*- coding: utf-8 -*-
"""情绪小模型推理：纯 Python 字符 n-gram NB，CPU 毫秒级。"""
from __future__ import annotations

import json
import math
import os
import re
import pathlib

ROOT = pathlib.Path(__file__).resolve().parent.parent
MODEL_PATH = pathlib.Path(os.environ.get("PAI_EMOTION_MODEL_PATH") or (pathlib.Path(__file__).parent / "emotion_model.json"))

CAPABILITY = {
    "name": "情绪小模型",
    "switch_env": "PAI_EMOTION_MODEL",
    "default_on": False,
    "stores_user_text": False,
    "network": False,
    "degrade": "模型缺失/低置信=回退关键词规则；不阻塞对话",
    "approval": "ADR-046：本地只读推理，默认关",
}

_MODEL = None


def enabled():
    return os.environ.get("PAI_EMOTION_MODEL", "").strip().lower() in ("1", "on", "true", "yes")


def _norm(text):
    s = str(text or "").lower()
    return re.sub(r"[\s，。！？、,.!?;；:：\"'“”‘’（）()\[\]【】]", "", s)


def _tokens(text):
    s = _norm(text)
    return list(s) + [s[i:i + 2] for i in range(len(s) - 1)]


def load():
    global _MODEL
    if _MODEL is None:
        _MODEL = json.loads(MODEL_PATH.read_text(encoding="utf-8"))
    return _MODEL


def classify(text):
    model = load()
    toks = [t for t in _tokens(text) if t in set(model.get("vocab") or [])]
    if not toks:
        return "warm", 0.0
    vocab_size = max(1, len(model.get("vocab") or []))
    scores = {}
    for label in model["labels"]:
        score = float(model["log_prior"][label])
        total = int(model["totals"][label]) + vocab_size
        counts = (model["counts"].get(label) or {})
        for t in toks:
            score += math.log((counts.get(t, 0) + 1) / total)
        scores[label] = score
    if not scores:
        return "warm", 0.0
    best = max(scores, key=scores.get)
    mx = max(scores.values())
    ex = {k: math.exp(v - mx) for k, v in scores.items()}
    z = sum(ex.values()) or 1.0
    return best, ex[best] / z
