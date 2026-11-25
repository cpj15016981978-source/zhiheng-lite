# -*- coding: utf-8 -*-
"""中文标点 v2：优先 sherpa-onnx 标点模型，规则兜底。"""
from __future__ import annotations

import os
import pathlib
import re

CAPABILITY = {
    "name": "中文标点 v2",
    "switch_env": "NONE",
    "default_on": True,
    "stores_user_text": False,
    "network": False,
    "degrade": "模型缺失/加载失败=规则标点；不改原词",
    "approval": "ADR-046：本地文本后处理，无外发",
}

ROOT = pathlib.Path(__file__).resolve().parent.parent
MODEL_DEFAULT = ROOT / "data" / "punct-model"
_MODEL = None
_MODEL_TRY = False

_CJK = re.compile(r"[\u4e00-\u9fff]")
_SPACE = re.compile(r"\s+")
_Q = re.compile(r"(吗|呢|么|为什么|怎么|多少|哪|谁|是否|能不能|可不可以)\s*$")
_CONJ = re.compile(r"(然后|但是|所以|因为|如果|另外|不过|而且|因此|总之|首先|其次|最后|比如|例如)")


def model_available():
    d = pathlib.Path(os.environ.get("PAI_PUNCT_MODEL", MODEL_DEFAULT))
    return (d / "model.int8.onnx").is_file() or (d / "model.onnx").is_file()


def _model():
    global _MODEL, _MODEL_TRY
    if _MODEL is not None or _MODEL_TRY:
        return _MODEL
    _MODEL_TRY = True
    d = pathlib.Path(os.environ.get("PAI_PUNCT_MODEL", MODEL_DEFAULT))
    p = d / "model.int8.onnx"
    if not p.is_file():
        p = d / "model.onnx"
    if not p.is_file():
        return None
    try:
        import sherpa_onnx
        cfg = sherpa_onnx.OfflinePunctuationConfig(
            model=sherpa_onnx.OfflinePunctuationModelConfig(
                ct_transformer=str(p), num_threads=1, provider="cpu"))
        _MODEL = sherpa_onnx.OfflinePunctuation(cfg)
    except Exception:
        _MODEL = None
    return _MODEL


def _join(text):
    text = _SPACE.sub(" ", str(text or "")).strip()
    # Vosk 会在汉字之间插空格；去掉汉字之间的空格，保留英文词间空格。
    text = re.sub(r"(?<=[\u4e00-\u9fff])\s+(?=[\u4e00-\u9fff])", "", text)
    return text


def _rules(text, final=True):
    text = _join(text)
    if not text:
        return ""
    parts = re.split(r"([。！？!?；;])", text)
    out = []
    for i in range(0, len(parts), 2):
        seg = parts[i].strip()
        if not seg:
            continue
        if len(seg) > 12:
            seg = _CONJ.sub(lambda m: "，" + m.group(1) if m.start() > 0 else m.group(1), seg)
        if i + 1 < len(parts) and parts[i + 1]:
            seg += parts[i + 1]
        elif final:
            seg += "？" if _Q.search(seg) else "。"
        out.append(seg)
    res = "".join(out)
    return re.sub(r"[，,]{2,}", "，", res)


def punctuate(text, final=True):
    text = _join(text)
    if not text:
        return ""
    if final and model_available():
        m = _model()
        if m is not None:
            try:
                out = str(m.add_punctuation(text) or "").strip()
                if out:
                    return out
            except Exception:
                pass
    return _rules(text, final=final)
