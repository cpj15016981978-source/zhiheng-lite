# -*- coding: utf-8 -*-
"""视觉理解：硅基流动 Qwen2.5-VL（OpenAI 兼容 /chat/completions）。

Key 只从环境变量读，绝不进前端、日志、异常正文。
"""
from __future__ import annotations

import base64
import json
import os
import urllib.error
import urllib.request

from . import offline as OFF

DEFAULT_BASE = "https://api.siliconflow.cn/v1"
DEFAULT_MODEL = "Qwen/Qwen3-VL-32B-Instruct"
MAX_BYTES = 10 * 1024 * 1024

CAPABILITY = {
    "name": "图片视觉理解",
    "switch_env": "PAI_VISION_API_KEY",
    "default_on": False,
    "stores_user_text": False,
    "network": True,
    "degrade": "无 Key/401/403/模型无权限/超时=VisionError，不伪造描述；图片只发用户配置的 SiliconFlow 端点",
    "approval": "ADR-044 出网接入登记；ADR-046 出网/常开边界",
}


class VisionError(ValueError):
    pass


def _key():
    return (os.environ.get("PAI_VISION_API_KEY")
            or os.environ.get("SILICONFLOW_API_KEY") or "").strip()


def configured():
    return bool(_key()) and OFF.is_allowed("vision")


def describe(data, mime="image/png", question="请描述图片内容，并提取其中可见文字。"):
    OFF.assert_allowed("vision", VisionError)
    if not data:
        raise VisionError("图片为空")
    if len(data) > MAX_BYTES:
        raise VisionError("图片超过 10MB")
    key = _key()
    if not key:
        raise VisionError("未配置 PAI_VISION_API_KEY / SILICONFLOW_API_KEY")
    base = (os.environ.get("PAI_VISION_BASE_URL") or DEFAULT_BASE).rstrip("/")
    model = (os.environ.get("PAI_VISION_MODEL") or DEFAULT_MODEL).strip()
    data_url = "data:%s;base64,%s" % (mime or "image/png",
                                      base64.b64encode(data).decode("ascii"))
    body = {
        "model": model,
        "messages": [{
            "role": "user",
            "content": [
                {"type": "text", "text": str(question or "描述图片")},
                {"type": "image_url", "image_url": {"url": data_url}},
            ],
        }],
        "max_tokens": int(os.environ.get("PAI_VISION_MAX_TOKENS", "800")),
        "temperature": 0.2,
        "stream": False,
    }
    req = urllib.request.Request(
        base + "/chat/completions",
        data=json.dumps(body, ensure_ascii=False).encode("utf-8"),
        method="POST",
        headers={"Content-Type": "application/json",
                 "Authorization": "Bearer " + key})
    try:
        with urllib.request.urlopen(req, timeout=int(os.environ.get("PAI_VISION_TIMEOUT", "120"))) as r:
            resp = json.loads(r.read().decode("utf-8"))
    except urllib.error.HTTPError as e:
        detail = e.read().decode("utf-8", errors="replace")[:300] if hasattr(e, "read") else ""
        raise VisionError("视觉接口 HTTP %s：%s" % (e.code, detail)) from None
    except urllib.error.URLError as e:
        raise VisionError("连不上视觉接口：%s" % e.reason) from None
    try:
        text = resp["choices"][0]["message"]["content"]
    except (KeyError, IndexError, TypeError):
        raise VisionError("视觉接口响应结构不符合预期") from None
    if isinstance(text, list):
        text = "".join(str(x.get("text") or "") for x in text if isinstance(x, dict))
    text = str(text or "").strip()
    if not text:
        raise VisionError("视觉接口没有返回文字")
    return text

